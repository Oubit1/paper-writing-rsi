"""SQLite is the source of truth; artifacts are immutable versioned snapshots."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def uid(prefix):
    return prefix + "_" + uuid.uuid4().hex[:16]


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


JSON_FIELDS = {"source_ids", "payload", "details", "validation", "evidence", "usage"}


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        (self.root / "runtime").mkdir(exist_ok=True, parents=True)
        self.path = self.root / "runtime" / "writing.sqlite"
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY,name TEXT NOT NULL,task TEXT NOT NULL,target_journal TEXT NOT NULL,
              source_ids TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL,mode TEXT NOT NULL,request TEXT NOT NULL,
              role_id TEXT NOT NULL,provider TEXT NOT NULL,rounds INTEGER NOT NULL,status TEXT NOT NULL,
              current_role TEXT NOT NULL DEFAULT '',round INTEGER NOT NULL DEFAULT 0,
              call_count INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
              error TEXT NOT NULL DEFAULT '',details TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,kind TEXT NOT NULL,
              message TEXT NOT NULL,payload TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS artifacts (
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL,run_id TEXT NOT NULL,role_id TEXT NOT NULL,
              revision INTEGER NOT NULL,parent_id TEXT,content TEXT NOT NULL,sha256 TEXT NOT NULL,
              path TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,validation TEXT NOT NULL,
              created_at TEXT NOT NULL,UNIQUE(project_id,role_id,revision));
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL,role_id TEXT NOT NULL,run_id TEXT NOT NULL,
              sender TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS experiences (
              id TEXT PRIMARY KEY,project_id TEXT NOT NULL,run_id TEXT NOT NULL,rule TEXT NOT NULL,
              scope TEXT NOT NULL,status TEXT NOT NULL,evidence TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS invocations (
              id TEXT PRIMARY KEY,run_id TEXT NOT NULL,role_id TEXT NOT NULL,model TEXT NOT NULL,
              effort TEXT NOT NULL,provider TEXT NOT NULL,status TEXT NOT NULL,usage TEXT NOT NULL,
              created_at TEXT NOT NULL,finished_at TEXT,error TEXT NOT NULL DEFAULT '');
            CREATE INDEX IF NOT EXISTS artifact_project ON artifacts(project_id,role_id,revision);
            CREATE INDEX IF NOT EXISTS message_project ON messages(project_id,role_id,created_at);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        result = dict(row)
        for key in JSON_FIELDS.intersection(result):
            result[key] = json.loads(result[key])
        return result

    def query(self, sql, args=(), one=False):
        with self.connect() as db:
            rows = db.execute(sql, args)
            return self.decode(rows.fetchone()) if one else [self.decode(r) for r in rows.fetchall()]

    def execute(self, sql, args=()):
        with self.lock, self.connect() as db:
            return db.execute(sql, args).rowcount

    def create_project(self, name, task="", target_journal=""):
        p = dict(id=uid("project"), name=name, task=task, target_journal=target_journal,
                 source_ids=[], created_at=now())
        self.execute("INSERT INTO projects VALUES (?,?,?,?,?,?)",
                     (p["id"], name, task, target_journal, "[]", p["created_at"]))
        return p

    def project(self, project_id):
        return self.query("SELECT * FROM projects WHERE id=?", (project_id,), one=True)

    def create_run(self, project_id, mode, request, role_id, provider, rounds):
        r = dict(id=uid("run"), project_id=project_id, mode=mode, request=request,
                 role_id=role_id, provider=provider, rounds=rounds, status="queued",
                 created_at=now())
        self.execute("INSERT INTO runs (id,project_id,mode,request,role_id,provider,rounds,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     tuple(r[k] for k in ("id","project_id","mode","request","role_id","provider","rounds","status","created_at")) + (r["created_at"],))
        self.event(r["id"], "queued", "已排队；尚未调用模型。")
        return self.run(r["id"])

    def run(self, run_id):
        return self.query("SELECT * FROM runs WHERE id=?", (run_id,), one=True)

    def update_run(self, run_id, **fields):
        allowed = {"status", "current_role", "round", "error", "details"}
        if not fields.keys() <= allowed:
            raise ValueError("invalid run field")
        fields["updated_at"] = now()
        values = [json.dumps(v, ensure_ascii=False) if k == "details" else v for k, v in fields.items()]
        self.execute("UPDATE runs SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?", values + [run_id])

    def reserve_call(self, run_id, limit):
        with self.lock, self.connect() as db:
            changed = db.execute("UPDATE runs SET call_count=call_count+1,updated_at=? WHERE id=? AND call_count<? AND status='running'",
                                 (now(), run_id, limit)).rowcount
            if not changed:
                raise RuntimeError("本轮调用额度已耗尽，或任务已停止。")

    def event(self, run_id, kind, message, payload=None):
        self.execute("INSERT INTO events(run_id,kind,message,payload,created_at) VALUES(?,?,?,?,?)",
                     (run_id, kind, message, json.dumps(payload or {}, ensure_ascii=False), now()))

    def message(self, project_id, role_id, run_id, sender, content):
        self.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                     (uid("msg"), project_id, role_id, run_id, sender, content, now()))

    def latest(self, project_id):
        rows = self.query("SELECT a.* FROM artifacts a JOIN (SELECT role_id,MAX(revision) AS rev FROM artifacts WHERE project_id=? AND status NOT IN ('mock','discussion','discussion_mock','memory_review','memory_review_mock','consultation','consultation_mock') GROUP BY role_id) b ON a.role_id=b.role_id AND a.revision=b.rev WHERE a.project_id=? ORDER BY a.created_at", (project_id, project_id))
        return {r["role_id"]: r for r in rows}

    def artifact(self, project_id, run_id, role_id, content, payload, validation, status="candidate"):
        with self.lock, self.connect() as db:
            channel_statuses={'mock','discussion','discussion_mock','memory_review','memory_review_mock','consultation','consultation_mock'}
            is_side_channel=status in channel_statuses
            parent = db.execute("SELECT id,revision FROM artifacts WHERE project_id=? AND role_id=? AND " +
                     ("status=?" if is_side_channel else
                      "status NOT IN ('mock','discussion','discussion_mock','memory_review','memory_review_mock','consultation','consultation_mock')")+
                     " ORDER BY revision DESC LIMIT 1", (project_id,role_id,status) if is_side_channel else (project_id,role_id)).fetchone()
            rev = db.execute("SELECT COALESCE(MAX(revision),0)+1 FROM artifacts WHERE project_id=? AND role_id=?",(project_id,role_id)).fetchone()[0]
            aid = uid("artifact")
            rel = Path("projects") / project_id / "artifacts" / f"{role_id}_v{rev:03d}_{aid[-8:]}.md"
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation avoids silently replacing a preceding manuscript.
            with path.open("x", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            row = (aid,project_id,run_id,role_id,rev,parent["id"] if parent else None,content,
                   digest(content),rel.as_posix(),status,json.dumps(payload,ensure_ascii=False),
                   json.dumps(validation,ensure_ascii=False),now())
            db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",row)
        return self.query("SELECT * FROM artifacts WHERE id=?", (aid,), one=True)

    def invocation_start(self, run_id, role, provider):
        iid = uid("call")
        self.execute("INSERT INTO invocations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (iid,run_id,role["id"],role["model"],role["effort"],provider,"running","{}",now(),None,""))
        return iid

    def invocation_end(self, iid, status, usage=None, error=""):
        self.execute("UPDATE invocations SET status=?,usage=?,finished_at=?,error=? WHERE id=?",
                     (status,json.dumps(usage or {},ensure_ascii=False),now(),error,iid))

    def experience(self, project_id, run_id, rule, status, evidence):
        record = dict(id=uid("memory"),project_id=project_id,run_id=run_id,rule=rule,
                      scope="project",status=status,evidence=evidence,created_at=now())
        self.execute("INSERT INTO experiences VALUES(?,?,?,?,?,?,?,?)",
                     tuple(json.dumps(record[k],ensure_ascii=False) if k == "evidence" else record[k]
                           for k in ("id","project_id","run_id","rule","scope","status","evidence","created_at")))
        return record

    def recover_interrupted(self):
        rows = self.query("SELECT id FROM runs WHERE status IN ('queued','running','cancelling')")
        for row in rows:
            self.update_run(row["id"],status="interrupted",error="服务重启；保留已有版本，未自动重放模型请求。")
            self.event(row["id"],"interrupted","服务重启，未重新付费执行。")
        self.execute("UPDATE invocations SET status='interrupted',finished_at=? WHERE status='running'", (now(),))
