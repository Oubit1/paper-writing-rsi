"""Version-bound, local Tectonic PDF builds for the source review workbench."""
from __future__ import annotations

import concurrent.futures
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading

from .manuscript import ManuscriptConflict
from .source_review import source_outline
from .store import Store, now, uid, digest


ASSET_SUFFIXES = {".tex", ".bib", ".bst", ".cls", ".sty", ".pdf",
                  ".png", ".jpg", ".jpeg", ".eps", ".otf", ".ttf"}


class TexPreviewJobs:
    def __init__(self, root, source_review, store=None, compiler=None, timeout_s=300):
        self.root = Path(root).resolve()
        self.source = source_review
        self.store = store or Store(self.root)
        self.compiler = Path(compiler or self.root / "tools/tectonic/tectonic.exe")
        self.timeout_s = timeout_s
        self.lock = threading.RLock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="paper-pdf")
        with self.store.connect() as db:
            db.executescript("""
              CREATE TABLE IF NOT EXISTS tex_preview_jobs (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                source_hash TEXT NOT NULL, revision INTEGER NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '', diagnostics TEXT NOT NULL DEFAULT '',
                folder TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS tex_preview_project ON tex_preview_jobs(project_id,created_at);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(tex_preview_jobs)")}
            for name, definition in {
                "build_kind": "TEXT NOT NULL DEFAULT 'current'",
                "proposal_id": "TEXT NOT NULL DEFAULT ''",
                "base_hash": "TEXT NOT NULL DEFAULT ''",
                "selected_hunk_ids": "TEXT NOT NULL DEFAULT '[]'",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE tex_preview_jobs ADD COLUMN {name} {definition}")
            db.execute("UPDATE tex_preview_jobs SET base_hash=source_hash WHERE base_hash='' AND build_kind='current'")
            db.execute("UPDATE tex_preview_jobs SET status='interrupted',error=?,updated_at=? "
                       "WHERE status IN ('queued','running')",
                       ("服务重新启动，请重新编译当前稿件。", now()))

    def _row(self, pid, jid):
        return self.store.query("SELECT * FROM tex_preview_jobs WHERE id=? AND project_id=?",
                                (jid, pid), one=True)

    def get(self, pid, jid):
        row = self._row(pid, jid)
        if not row:
            return None
        state = self.source.get(pid)
        row["is_current"] = row["build_kind"] == "current" and row["source_hash"] == state["hash"]
        row["base_is_current"] = row["base_hash"] == state["hash"]
        row["selected_hunk_ids"] = json.loads(row["selected_hunk_ids"])
        row["can_accept"] = False
        if row["build_kind"] == "candidate" and row["base_is_current"]:
            try:
                proposal = self.source.proposal(pid, row["proposal_id"])
                pending = {h["id"] for h in proposal["hunks"] if h["status"] == "pending"}
                row["can_accept"] = bool(row["selected_hunk_ids"]) and set(row["selected_hunk_ids"]) <= pending
            except (ValueError, KeyError):
                pass
        row["preview_url"] = row["pdf_url"] = (
            f"/api/preview/{pid}/pdf/{jid}" if row["status"] == "completed" else None
        )
        row["job_id"] = row["id"]
        row["stale"] = row["status"] == "completed" and not (
            row["base_is_current"] if row["build_kind"] == "candidate" else row["is_current"])
        if row["build_kind"] == "candidate":
            snapshot = self.root / row["folder"] / "main.tex"
            row["candidate_outline"] = source_outline(snapshot.read_text(encoding="utf-8")) if snapshot.is_file() else []
        row.pop("folder", None)
        return row

    def latest(self, pid):
        state = self.source.get(pid)
        row = self.store.query(
            "SELECT id FROM tex_preview_jobs WHERE project_id=? AND build_kind='current' "
            "ORDER BY (source_hash=? AND status='completed') DESC,created_at DESC,rowid DESC LIMIT 1",
            (pid, state["hash"]), one=True)
        return self.get(pid, row["id"]) if row else None

    def submit(self, pid, base_hash=None):
        state = self.source.get(pid)
        if base_hash and base_hash != state["hash"]:
            raise ManuscriptConflict("编译请求基于旧版源码，请先读取当前稿件。",
                                     {"hash": state["hash"], "revision": state["revision"]})
        return self._submit(pid, state, "current", state["hash"], "", [])

    def submit_candidate(self, pid, proposal_id, base_hash, selected_hunk_ids=None):
        # Capture source, choices and their base under the source writer lock.
        # The compiler only receives this detached snapshot after releasing it.
        lock = getattr(getattr(self.source, "store", None), "lock", None)
        with lock if lock is not None else nullcontext():
            state = self.source.get(pid)
            if not isinstance(base_hash, str) or base_hash != state["hash"]:
                raise ManuscriptConflict("候选预览基于旧稿，请重新读取当前版本。",
                                         {"hash": state["hash"], "revision": state["revision"]})
            proposal = self.source.proposal(pid, proposal_id)
            if proposal.get("review_mode") not in {"local", "layout", "structural"}:
                raise ValueError("PDF 候选预览支持具体文字问题或布局建议；旧范围重写仅保留历史。")
            preview = self.source.preview(pid, proposal_id, selected_hunk_ids)
            chosen = selected_hunk_ids if selected_hunk_ids is not None else [
                h["id"] for h in proposal["hunks"] if h["status"] == "pending"]
            captured = {**state, "tex": preview["tex"], "hash": digest(preview["tex"])}
        return self._submit(pid, captured, "candidate", base_hash, proposal_id, sorted(chosen))

    def _submit(self, pid, state, build_kind, base_hash, proposal_id, selected_hunk_ids):
        if not self.compiler.is_file():
            raise ValueError("本地 LaTeX 编译器尚未就绪。")
        with self.lock:
            existing = self.store.query(
                "SELECT id FROM tex_preview_jobs WHERE project_id=? AND source_hash=? AND build_kind=? "
                "AND base_hash=? AND proposal_id=? AND selected_hunk_ids=? "
                "AND status IN ('queued','running','completed') ORDER BY rowid DESC LIMIT 1",
                (pid, state["hash"], build_kind, base_hash, proposal_id, json.dumps(selected_hunk_ids)), one=True)
            if existing:
                result = self.get(pid, existing["id"])
                result["reused"] = True
                return result
            jid = uid("preview")
            folder = self.root / "runtime/previews" / pid / jid
            folder.mkdir(parents=True, exist_ok=False)
            assets_value = state.get("assets_path") or state.get("source_binding", {}).get("assets_path")
            if not assets_value and hasattr(self.source, "assets_path"):
                assets_value = self.source.assets_path(pid)
            assets = Path(assets_value) if assets_value else None
            if assets and not assets.is_absolute():
                assets = self.root / assets
            if assets:
                assets = assets.resolve()
                if not assets.is_relative_to(self.root) or not assets.is_dir():
                    raise ValueError("预览素材必须位于论文工作区内。")
                self._copy_assets(pid, assets, folder)
            (folder / "main.tex").write_text(state["tex"], encoding="utf-8", newline="\n")
            stamp = now()
            self.store.execute("INSERT INTO tex_preview_jobs (id,project_id,source_hash,revision,status,created_at,updated_at,"
                               "error,diagnostics,folder,build_kind,proposal_id,base_hash,selected_hunk_ids) "
                               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (jid, pid, state["hash"], state["revision"], "queued",
                                stamp, stamp, "", "", folder.relative_to(self.root).as_posix(), build_kind,
                                proposal_id, base_hash, json.dumps(selected_hunk_ids)))
            self.pool.submit(self._run, pid, jid)
            return self.get(pid, jid)

    def _copy_assets(self, pid, assets, folder):
        # ZIPs may place the selected TeX entry in a subdirectory while keeping
        # figures at the archive root. Capture those resources as fallbacks;
        # the entry's own directory always takes precedence. Never walk above
        # this project's uploaded archive, or broaden ordinary project assets.
        archive = (self.root / "projects" / pid / "uploaded").resolve()
        layers = [assets]
        if archive.is_relative_to(self.root) and assets.is_relative_to(archive):
            parent = assets
            while parent != archive:
                parent = parent.parent
                layers.insert(0, parent)
        seen = set()
        for layer in layers:
            for item in layer.rglob("*"):
                if not item.is_file() or item.suffix.lower() not in ASSET_SUFFIXES:
                    continue
                resolved = item.resolve()
                if not resolved.is_relative_to(layer):
                    raise ValueError("素材不能通过链接读取工程目录以外的内容。")
                seen.add(resolved)
                if len(seen) > 500 or item.stat().st_size > 100_000_000:
                    raise ValueError("预览素材数量或单文件过大。")
                destination = folder / item.relative_to(layer)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, destination)

    def _update(self, jid, **fields):
        fields["updated_at"] = now()
        self.store.execute("UPDATE tex_preview_jobs SET " +
                           ",".join(k + "=?" for k in fields) + " WHERE id=?",
                           list(fields.values()) + [jid])

    def _run(self, pid, jid):
        row = self._row(pid, jid)
        folder = (self.root / row["folder"]).resolve()
        self._update(jid, status="running")
        output = ""
        try:
            # A source asset named main.pdf is not evidence of this build.
            # Delete only the captured copy so success requires a fresh output.
            (folder / "main.pdf").unlink(missing_ok=True)
            # Compile a captured current/candidate snapshot, with no shell escape.
            env = dict(os.environ)
            env["TECTONIC_UNTRUSTED_MODE"] = "1"
            kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            proc = subprocess.Popen(
                [str(self.compiler), "--untrusted", "--keep-logs", "--synctex",
                 "--outdir", str(folder), str(folder / "main.tex")],
                cwd=str(folder), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, text=True, encoding="utf-8", errors="replace", **kwargs)
            try:
                output, _ = proc.communicate(timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                output, _ = proc.communicate()
                raise RuntimeError("编译超时；当前正文已保留，可查看诊断后重试。")
            (folder / "console.log").write_text(output, encoding="utf-8")
            if proc.returncode != 0 or not (folder / "main.pdf").is_file():
                raise RuntimeError("LaTeX 编译未成功，请检查诊断。")
            self._update(jid, status="completed", diagnostics=output[-30000:], error="")
        except Exception as exc:
            self._update(jid, status="failed", error=str(exc), diagnostics=output[-30000:])

    def pdf_path(self, pid, jid):
        row = self._row(pid, jid)
        if not row or row["status"] != "completed":
            raise ValueError("此版本还没有成功编译的 PDF。")
        folder = (self.root / row["folder"]).resolve()
        allowed = (self.root / "runtime/previews").resolve()
        if not folder.is_relative_to(allowed):
            raise ValueError("预览文件范围无效。")
        result = (folder / "main.pdf").resolve()
        if not result.is_relative_to(folder) or not result.is_relative_to(allowed):
            raise ValueError("预览文件范围无效。")
        if not result.is_file():
            raise ValueError("预览文件不存在，请重新编译。")
        return result

    def shutdown(self, wait=False):
        self.pool.shutdown(wait=wait, cancel_futures=True)
