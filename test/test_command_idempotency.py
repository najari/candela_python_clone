"""A command retry must never create a second edit or advance undo again."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagstudio.application import Application  # noqa: E402
from diagstudio.domain import DomainError  # noqa: E402


class CommandIdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.profile = tempfile.TemporaryDirectory(prefix="diag-retry-")
        self.previous = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.profile.name
        self.app = Application()
        self.variant = self.app.new_project("Retry ECU", "RetryECU")["variants"][0]["id"]

    def tearDown(self):
        self.app.close_project()
        if self.previous is None: os.environ.pop("LOCALAPPDATA", None)
        else: os.environ["LOCALAPPDATA"] = self.previous
        self.profile.cleanup()

    def test_create_retry_and_payload_conflict(self):
        command_id = str(uuid.uuid4())
        payload = {"variant_id": self.variant, "name": "Fault", "qualifier": "Fault", "code": "0x012345"}
        first = self.app.execute_command("create_dtc", payload, self.app.r.revision, command_id)
        revision = first["revision"]
        retry = self.app.execute_command("create_dtc", dict(reversed(list(payload.items()))), revision - 1, command_id)
        self.assertTrue(retry["already_applied"])
        self.assertEqual(retry["selected_id"], first["selected_id"])
        self.assertEqual(retry["revision"], revision)
        self.assertEqual(len(self.app.query_objects("dtc", variant=self.variant)["rows"]), 1)
        self.assertEqual(self.app.r.one("SELECT count(*) n FROM undo_stack")["n"], 1)
        with self.assertRaises(DomainError) as reused:
            self.app.execute_command("create_dtc", {**payload, "code": "0x012346"}, revision, command_id)
        self.assertEqual(reused.exception.code, "COMMAND_ID_REUSED")

    def test_failed_command_not_recorded_then_retry_after_fix(self):
        command_id = str(uuid.uuid4())
        bad = {"variant_id": self.variant, "name": "Bad", "qualifier": "Bad", "code": "0x1000000"}
        with self.assertRaises(DomainError):
            self.app.execute_command("create_dtc", bad, self.app.r.revision, command_id)
        self.assertIsNone(self.app.r.receipt(command_id))
        good = {**bad, "code": "0x100000"}
        result = self.app.execute_command("create_dtc", good, self.app.r.revision, command_id)
        self.assertFalse(result.get("already_applied", False))
        self.assertEqual(len(self.app.query_objects("dtc", variant=self.variant)["rows"]), 1)

    def test_undo_redo_retries_do_not_move_cursor_again(self):
        self.app.execute_command("create_dtc", {"variant_id": self.variant, "name": "Fault", "qualifier": "Fault", "code": 1}, self.app.r.revision)
        undo_id = str(uuid.uuid4())
        before = self.app.r.revision
        self.app.execute_command("undo", {}, before, undo_id)
        after_undo = self.app.r.revision
        self.assertEqual(len(self.app.query_objects("dtc", variant=self.variant)["rows"]), 0)
        self.assertTrue(self.app.execute_command("undo", {}, before, undo_id)["already_applied"])
        self.assertEqual(self.app.r.revision, after_undo)
        redo_id = str(uuid.uuid4())
        self.app.execute_command("redo", {}, after_undo, redo_id)
        after_redo = self.app.r.revision
        self.assertEqual(len(self.app.query_objects("dtc", variant=self.variant)["rows"]), 1)
        self.assertTrue(self.app.execute_command("redo", {}, after_undo, redo_id)["already_applied"])
        self.assertEqual(self.app.r.revision, after_redo)

    def test_receipt_survives_save_reopen(self):
        command_id = str(uuid.uuid4())
        payload = {"variant_id": self.variant, "name": "Fault", "qualifier": "Fault", "code": 2}
        first = self.app.execute_command("create_dtc", payload, self.app.r.revision, command_id)
        path = str(Path(self.profile.name) / "retry.diagproj")
        self.app.save_project(path)
        self.app.close_project()
        self.app.open_project(path)
        retry = self.app.execute_command("create_dtc", payload, first["revision"] - 1, command_id)
        self.assertTrue(retry["already_applied"])
        self.assertEqual(retry["selected_id"], first["selected_id"])
        self.assertEqual(len(self.app.query_objects("dtc", variant=self.variant)["rows"]), 1)


if __name__ == "__main__": unittest.main()
