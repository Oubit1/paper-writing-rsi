"""Version-bound PDF tests: temporary roots, captured source and fake compiler."""
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app.manuscript import ManuscriptConflict
from app.store import Store, digest
from app.tex_preview import TexPreviewJobs


class FakeSource:
    def __init__(self, pid, assets):
        self.pid = pid
        self.state = {"tex": "\\documentclass{article}\n\\begin{document}Accepted source.\\end{document}\n",
                      "revision": 1, "assets_path": str(assets)}
        self.state["hash"] = digest(self.state["tex"])

    def get(self, pid):
        if pid != self.pid:
            raise ValueError("Project not found")
        return dict(self.state)

    def revise(self):
        self.state["tex"] = self.state["tex"].replace("Accepted source.", "Author edit.")
        self.state["hash"] = digest(self.state["tex"])
        self.state["revision"] += 1


class FakeProcess:
    def __init__(self, owner, args, **kwargs):
        self.owner, self.args, self.kwargs = owner, args, kwargs
        self.returncode = owner.returncode
        self.folder = Path(kwargs["cwd"])
        self.captured_tex = (self.folder / "main.tex").read_text(encoding="utf-8")
        self.killed = False
        self.first_communicate = True
        owner.calls.append(self)
        owner.started.set()

    def communicate(self, timeout=None):
        if self.owner.timeout and self.first_communicate:
            self.first_communicate = False
            raise subprocess.TimeoutExpired(self.args, timeout)
        if not self.owner.release.wait(4):
            raise RuntimeError("Fake compiler fixture did not release")
        if not self.returncode and self.owner.write_pdf:
            (self.folder / "main.pdf").write_bytes(b"%PDF-1.4\nFake compiler output\n")
        return self.owner.output, None

    def kill(self):
        self.killed = True


class FakeCompiler:
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.returncode = 0
        self.write_pdf = True
        self.timeout = False
        self.output = "Fake compilation succeeded."

    def __call__(self, args, **kwargs):
        return FakeProcess(self, args, **kwargs)


class TexPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root)
        self.pid = self.store.create_project("Preview fixture")["id"]
        self.assets = self.root / "author-project"
        self.assets.mkdir()
        (self.assets / "main.tex").write_text("Original asset main.tex remains untouched.", encoding="utf-8")
        (self.assets / "paper.cls").write_text("Original class.", encoding="utf-8")
        (self.assets / "references.bib").write_text("Original bibliography.", encoding="utf-8")
        (self.assets / "figure.pdf").write_bytes(b"Original figure PDF.")
        (self.assets / "excluded.py").write_text("Not a TeX asset.", encoding="utf-8")
        self.compiler_path = self.root / "tools/fake-tectonic.exe"
        self.compiler_path.parent.mkdir()
        self.compiler_path.write_text("This fixture must never be executed.")
        self.source = FakeSource(self.pid, self.assets)
        self.fake = FakeCompiler()
        self.patcher = patch("app.tex_preview.subprocess.Popen", side_effect=self.fake)
        self.patcher.start()
        self.service = TexPreviewJobs(self.root, self.source, self.store, compiler=self.compiler_path)

    def tearDown(self):
        self.fake.release.set()
        self.service.shutdown(wait=True)
        self.patcher.stop()
        self.temp.cleanup()

    def wait(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.service.get(self.pid, job_id)
            if job["status"] in {"completed", "failed", "interrupted"}:
                return job
            time.sleep(.01)
        self.fail("Fake compilation did not finish")

    def submit(self):
        return self.service.submit(self.pid, self.source.state["hash"])

    def test_stale_cas_rejected_without_job_or_compiler_call(self):
        old_hash = self.source.state["hash"]
        self.source.revise()
        with self.assertRaises(ManuscriptConflict) as caught:
            self.service.submit(self.pid, old_hash)
        self.assertEqual(caught.exception.current["hash"], self.source.state["hash"])
        self.assertEqual(caught.exception.current["revision"], 2)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM tex_preview_jobs", one=True)["n"], 0)

    def test_snapshot_copies_assets_and_never_changes_author_source(self):
        original = {p.name: p.read_bytes() for p in self.assets.iterdir()}
        original_state = dict(self.source.state)
        job = self.wait(self.submit()["id"])
        self.assertEqual(job["status"], "completed", job["error"])
        proc = self.fake.calls[0]
        self.assertEqual(proc.captured_tex, original_state["tex"])
        self.assertEqual((proc.folder / "paper.cls").read_bytes(), original["paper.cls"])
        self.assertEqual((proc.folder / "references.bib").read_bytes(), original["references.bib"])
        self.assertEqual((proc.folder / "figure.pdf").read_bytes(), original["figure.pdf"])
        self.assertFalse((proc.folder / "excluded.py").exists())
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.assets.iterdir()})
        self.assertEqual(self.source.state, original_state)
        self.assertNotEqual(proc.folder, self.assets)
        self.assertIn("--untrusted", proc.args)
        self.assertEqual(proc.kwargs["env"]["TECTONIC_UNTRUSTED_MODE"], "1")
        self.assertNotIn("shell", proc.kwargs)
        self.assertFalse(Path(proc.args[-1]).is_relative_to(self.assets))

    def test_same_hash_queued_running_and_completed_jobs_are_reused(self):
        self.fake.release.clear()
        first = self.submit()
        self.assertTrue(self.fake.started.wait(2))
        duplicate = self.submit()
        self.assertEqual(duplicate["id"], first["id"])
        self.assertTrue(duplicate["reused"])
        self.assertEqual(len(self.fake.calls), 1)
        self.fake.release.set()
        self.assertEqual(self.wait(first["id"])["status"], "completed")
        cached = self.submit()
        self.assertEqual(cached["id"], first["id"])
        self.assertTrue(cached["reused"])
        self.assertEqual(len(self.fake.calls), 1)

    def test_uploaded_nested_entry_includes_archive_resources_with_local_precedence(self):
        import io
        import zipfile
        from app.project_upload import import_project
        from app.source_review import SourceReviewStore

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("MIA/Manuscript.tex", self.source.state["tex"])
            zipped.writestr("fig1.pdf", b"Archive root figure")
            zipped.writestr("images/fig2.pdf", b"Nested figure")
            zipped.writestr("references.bib", "Root references")
            zipped.writestr("MIA/references.bib", "Entry references")
            zipped.writestr("MIA/paper.cls", "Entry class")
        review = SourceReviewStore(self.root, self.store)
        result = import_project(self.root, self.store, review, archive.getvalue(), "Example.zip")
        pid = result["project"]["id"]
        original_state = review.get(pid)
        service = TexPreviewJobs(self.root, review, self.store, compiler=self.compiler_path)
        try:
            job = service.submit(pid, original_state["hash"])
            service.shutdown(wait=True)
            self.assertEqual(service.get(pid, job["id"])["status"], "completed")
            captured = self.fake.calls[-1].folder
            self.assertEqual((captured / "fig1.pdf").read_bytes(), b"Archive root figure")
            self.assertEqual((captured / "images/fig2.pdf").read_bytes(), b"Nested figure")
            self.assertEqual((captured / "references.bib").read_text(), "Entry references")
            self.assertEqual((captured / "paper.cls").read_text(), "Entry class")
            self.assertEqual(review.get(pid)["hash"], original_state["hash"])
        finally:
            service.shutdown(wait=True)

    def test_regular_assets_do_not_inherit_parent_files(self):
        (self.assets.parent / "unrelated.pdf").write_bytes(b"Private unrelated resource")
        self.assertEqual(self.wait(self.submit()["id"])["status"], "completed")
        self.assertFalse((self.fake.calls[-1].folder / "unrelated.pdf").exists())

    def test_uploaded_fallback_cannot_follow_link_outside_archive(self):
        archive = self.root / "projects" / self.pid / "uploaded"
        assets = archive / "MIA"
        assets.mkdir(parents=True)
        escape = archive / "escape.pdf"
        escape.write_bytes(b"Link placeholder")
        self.source.state["assets_path"] = str(assets)
        real_resolve = Path.resolve
        def canonical(path, *args, **kwargs):
            return self.root / "private.pdf" if path == escape else real_resolve(path, *args, **kwargs)
        with patch.object(Path, "resolve", canonical), self.assertRaises(ValueError):
            self.submit()
        self.assertEqual(self.fake.calls, [])

    def test_pdf_source_hash_and_staleness_follow_current_source(self):
        self.fake.release.clear()
        first = self.submit()
        captured_hash = first["source_hash"]
        self.assertTrue(self.fake.started.wait(2))
        self.source.revise()
        self.fake.release.set()
        old = self.wait(first["id"])
        self.assertEqual(old["source_hash"], captured_hash)
        self.assertEqual(old["revision"], 1)
        self.assertTrue(old["stale"])
        self.assertFalse(old["is_current"])
        self.assertTrue(self.service.pdf_path(self.pid, old["id"]).is_file())
        self.assertEqual(old["preview_url"], f"/api/preview/{self.pid}/pdf/{old['id']}")
        self.assertEqual(old["preview_url"], old["pdf_url"])
        current = self.wait(self.submit()["id"])
        self.assertNotEqual(current["id"], old["id"])
        self.assertEqual(current["source_hash"], self.source.state["hash"])
        self.assertTrue(current["is_current"])
        self.assertFalse(current["stale"])
        self.assertEqual(self.service.latest(self.pid)["id"], current["id"])
        self.assertNotIn("folder", current)

    def test_asset_directory_outside_workspace_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            self.source.state["assets_path"] = outside
            with self.assertRaises(ValueError):
                self.submit()
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM tex_preview_jobs", one=True)["n"], 0)

    def test_asset_canonical_target_outside_project_is_rejected(self):
        escape = self.assets / "escape.sty"
        escape.write_text("Link placeholder.")
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "secret.sty"
            target.write_text("This must not be copied.")
            real_resolve = Path.resolve

            def canonical(path, *args, **kwargs):
                return target if path == escape else real_resolve(path, *args, **kwargs)

            # Model a file symlink on every OS, including Windows without link privileges.
            with patch.object(Path, "resolve", canonical), self.assertRaises(ValueError):
                self.submit()
        self.assertEqual(self.fake.calls, [])

    def test_pdf_folder_and_final_canonical_target_cannot_escape_workspace(self):
        job = self.wait(self.submit()["id"])
        row = self.service._row(self.pid, job["id"])
        folder = (self.root / row["folder"]).resolve()
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "main.pdf"
            target.write_bytes(b"Private outside file.")
            self.store.execute("UPDATE tex_preview_jobs SET folder=? WHERE id=?", (outside, job["id"]))
            with self.assertRaises(ValueError):
                self.service.pdf_path(self.pid, job["id"])
            self.store.execute("UPDATE tex_preview_jobs SET folder=? WHERE id=?", (row["folder"], job["id"]))
            real_resolve = Path.resolve

            def canonical(path, *args, **kwargs):
                return target if path == folder / "main.pdf" else real_resolve(path, *args, **kwargs)

            with patch.object(Path, "resolve", canonical), self.assertRaises(ValueError):
                self.service.pdf_path(self.pid, job["id"])

    def test_failed_compilation_has_diagnostics_and_can_retry_same_source(self):
        self.fake.returncode = 1
        self.fake.output = "Undefined control sequence at line 7."
        failed = self.wait(self.submit()["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("Undefined control", failed["diagnostics"])
        self.assertIsNone(failed["preview_url"])
        with self.assertRaises(ValueError):
            self.service.pdf_path(self.pid, failed["id"])
        self.fake.returncode = 0
        retried = self.wait(self.submit()["id"])
        self.assertNotEqual(failed["id"], retried["id"])
        self.assertEqual(retried["status"], "completed")

    def test_old_asset_pdf_cannot_be_reported_as_fresh_compiler_output(self):
        original_pdf = self.assets / "main.pdf"
        original_pdf.write_bytes(b"%PDF-1.4\nOld manuscript version.\n")
        before = original_pdf.read_bytes()
        self.fake.write_pdf = False
        failed = self.wait(self.submit()["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["preview_url"])
        self.assertEqual(original_pdf.read_bytes(), before)
        with self.assertRaises(ValueError):
            self.service.pdf_path(self.pid, failed["id"])

    def test_timeout_kills_only_owned_process_and_preserves_source(self):
        original = dict(self.source.state)
        self.fake.timeout = True
        failed = self.wait(self.submit()["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("超时", failed["error"])
        self.assertTrue(self.fake.calls[0].killed)
        self.assertEqual(original, self.source.state)

    def test_pdf_lookup_is_project_bound_and_requires_completed_job(self):
        self.fake.release.clear()
        job = self.submit()
        self.assertTrue(self.fake.started.wait(2))
        with self.assertRaises(ValueError):
            self.service.pdf_path(self.pid, job["id"])
        self.assertIsNone(self.service.get("other-project", job["id"]))
        with self.assertRaises(ValueError):
            self.service.pdf_path("other-project", job["id"])
        self.fake.release.set()
        self.assertEqual(self.wait(job["id"])["status"], "completed")


if __name__ == "__main__":
    unittest.main()
