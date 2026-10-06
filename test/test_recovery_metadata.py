"""UI-12 복구 메타데이터 회귀 테스트. 실제 작업본을 만들고 작업 폴더를 위조해 확인한다."""
from pathlib import Path
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diagstudio import migrations  # noqa: E402
from diagstudio.application import Application  # noqa: E402
from diagstudio.persistence import Session, app_home, digest  # noqa: E402


class RecoveryMetadataTest(unittest.TestCase):
    def setUp(self):
        self.profile = tempfile.TemporaryDirectory(prefix="diag-recovery-")
        self.previous = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.profile.name
        self.out = Path(self.profile.name) / "out"
        self.out.mkdir()
        self.app = Application()
        self.app.new_project("Recovery ECU", "RecoveryECU")
        self.saved = Path(self.app.save_project(str(self.out / "recovery.diagproj"))["saved_path"])
        self.app.close_project()

    def tearDown(self):
        if self.previous is None: os.environ.pop("LOCALAPPDATA", None)
        else: os.environ["LOCALAPPDATA"] = self.previous
        self.profile.cleanup()

    def workspace(self, session_id, source=None, source_hash=None, saved_at=None, working=None):
        folder = app_home() / "workspaces" / session_id
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(working or self.saved, folder / "working.sqlite")
        manifest = {"session_id": session_id, "source": source, "source_hash": source_hash, "status": "active", "updated_at": "2026-01-01T00:00:00+00:00"}
        if saved_at is not None: manifest["saved_at"] = saved_at
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return folder

    def entry(self, session_id):
        return next(r for r in Session.recoveries() if r["session_id"] == session_id)

    def test_recoveries_include_saved_at_integrity_version_and_source_state(self):
        sid = str(uuid.uuid4())
        self.workspace(sid, source=str(self.saved), source_hash=digest(self.saved), saved_at="2026-02-02T00:00:00+00:00")
        row = self.entry(sid)
        self.assertEqual(row["integrity"], "정상")
        self.assertEqual(row["schema_version"], migrations.CURRENT_VERSION)
        self.assertFalse(row["source_changed"])
        self.assertEqual(row["saved_at"], "2026-02-02T00:00:00+00:00")
        with self.saved.open("ab") as f: f.write(b" ")
        self.assertTrue(self.entry(sid)["source_changed"])

    def test_recoveries_handle_missing_source_and_corrupt_workspace(self):
        sid_missing = str(uuid.uuid4())
        self.workspace(sid_missing, source=str(self.out / "missing.diagproj"), source_hash="deadbeef")
        row = self.entry(sid_missing)
        self.assertIsNone(row["source_changed"])
        self.assertIn("saved_at", row)
        self.assertIsNone(row["saved_at"])
        sid_bad = str(uuid.uuid4())
        bad = self.profile.name + "/not-a-db.sqlite"
        with open(bad, "w", encoding="utf-8") as f: f.write("this is not a sqlite database")
        self.workspace(sid_bad, working=Path(bad))
        self.assertEqual(self.entry(sid_bad)["integrity"], "손상")


    def test_probe_does_not_modify_workspace(self):
        sid = str(uuid.uuid4())
        folder = self.workspace(sid, source=str(self.saved), source_hash=digest(self.saved))
        before = {p.name: p.read_bytes() for p in folder.iterdir()}
        self.entry(sid)
        self.assertEqual({p.name: p.read_bytes() for p in folder.iterdir()}, before)


    def test_quarantine_moves_workspace_out_of_recovery_list(self):
        sid = str(uuid.uuid4())
        folder = self.workspace(sid, source=str(self.saved), source_hash=digest(self.saved))
        content = (folder / "working.sqlite").read_bytes()
        result = Session.quarantine_recovery(sid)
        self.assertFalse(folder.exists())
        self.assertNotIn(sid, [r["session_id"] for r in Session.recoveries()])
        moved = Path(result["path"])
        self.assertEqual(moved.parent, app_home() / "quarantine")
        self.assertEqual((moved / "working.sqlite").read_bytes(), content)  # 지우지 않고 옮긴다

    def test_quarantine_refuses_busy_or_unknown_workspace(self):
        from diagstudio.domain import DomainError
        from diagstudio.persistence import FileLock
        sid = str(uuid.uuid4())
        self.workspace(sid)
        lock = FileLock("session:" + sid)
        try:
            with self.assertRaises(DomainError) as busy: Session.quarantine_recovery(sid)
            self.assertEqual(busy.exception.code, "BUSY")
        finally:
            lock.close()
        with self.assertRaises(DomainError): Session.quarantine_recovery("../../etc")
        with self.assertRaises(DomainError): Session.quarantine_recovery(str(uuid.uuid4()))


if __name__ == "__main__":
    unittest.main()
