"""Saved-file comparison must use business keys and leave both projects intact."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagstudio.application import Application  # noqa: E402
from diagstudio.domain import DomainError  # noqa: E402
from diagstudio.persistence import digest  # noqa: E402


class ProjectComparisonTest(unittest.TestCase):
    def setUp(self):
        self.profile = tempfile.TemporaryDirectory(prefix="diag-compare-")
        self.previous = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.profile.name
        self.left = Application()
        self.right = Application()
        self.path = str(Path(self.profile.name) / "saved.diagproj")

    def tearDown(self):
        self.left.close_project()
        self.right.close_project()
        if self.previous is None: os.environ.pop("LOCALAPPDATA", None)
        else: os.environ["LOCALAPPDATA"] = self.previous
        self.profile.cleanup()

    @staticmethod
    def command(app, action, payload):
        return app.execute_command(action, payload, app.r.revision)

    def make_project(self, app, did_name="Reading", bit_length=16):
        variant = app.new_project("ECU", "CompareECU")["variants"][0]["id"]
        if bit_length == 16:
            type_id = next(row["id"] for row in app.query_objects("data_type")["rows"] if row["qualifier"] == "UInt16")
        else:
            type_id = next(row["id"] for row in app.query_objects("data_type")["rows"] if row["qualifier"] == "UInt8")
        self.command(app, "create_did", {"variant_id": variant, "name": did_name, "qualifier": "ReadingDid",
                                          "identifier": "0x1234", "read_service": False,
                                          "members": [{"kind": "data", "name": "Value", "qualifier": "Value", "data_type_id": type_id}]})
        self.command(app, "create_dtc", {"variant_id": variant, "name": "Failure", "qualifier": "FailureDtc", "code": "0x010203"})
        return variant

    def test_same_semantics_ignore_different_uuids_and_keep_file_unchanged(self):
        left_variant = self.make_project(self.left)
        right_variant = self.make_project(self.right)
        self.right.save_project(self.path)
        before = digest(self.path)
        selected = self.left.compare_file_variants(self.path)
        result = self.left.compare_project_file(self.path, left_variant, right_variant, selected["digest"])
        self.assertEqual(result["rows"], [])
        self.assertEqual(digest(self.path), before)

    def test_content_and_presence_changes(self):
        left_variant = self.make_project(self.left)
        right_variant = self.make_project(self.right, did_name="New reading", bit_length=8)
        self.command(self.right, "create_dtc", {"variant_id": right_variant, "name": "Extra", "qualifier": "ExtraDtc", "code": "0x010204"})
        self.right.save_project(self.path)
        selected = self.left.compare_file_variants(self.path)
        rows = self.left.compare_project_file(self.path, left_variant, right_variant, selected["digest"])["rows"]
        self.assertEqual([(x["category"], x["code"]) for x in rows], [("DID", "0x1234"), ("DTC", "0x010204")])
        self.assertIn("이름", rows[0]["relation"])
        self.assertIn("데이터 구조", rows[0]["relation"])
        self.assertEqual(rows[1]["relation"], "대상에만 있음")
        self.assertIsNone(rows[1]["id"])

    def test_file_change_and_invalid_file_are_rejected(self):
        self.make_project(self.left)
        self.make_project(self.right)
        self.right.save_project(self.path)
        selected = self.left.compare_file_variants(self.path)
        with open(self.path, "ab") as stream: stream.write(b"changed")
        with self.assertRaises(DomainError) as changed:
            self.left.compare_project_file(self.path, self.left.overview()["variants"][0]["id"],
                                           selected["variants"][0]["id"], selected["digest"])
        self.assertEqual(changed.exception.code, "FILE_CHANGED")
        bad = Path(self.profile.name) / "invalid.diagproj"
        bad.write_text("not a database", encoding="utf-8")
        with self.assertRaises(DomainError) as invalid: self.left.compare_file_variants(str(bad))
        self.assertEqual(invalid.exception.code, "INVALID_PROJECT")

    def test_instances_and_services_are_compared_by_qualifier(self):
        left_variant = self.make_project(self.left)
        right_variant = self.make_project(self.right)
        for app, variant in ((self.left, left_variant), (self.right, right_variant)):
            type_id = next(row["id"] for row in app.query_objects("data_type")["rows"] if row["qualifier"] == "UInt8")
            self.command(app, "create_did", {"variant_id": variant, "name": "Speed", "qualifier": "SpeedDid", "identifier": "0x2000",
                                              "members": [{"kind": "data", "name": "Value", "qualifier": "Value", "data_type_id": type_id}]})
        self.right.save_project(self.path)
        selected = self.left.compare_file_variants(self.path)
        self.assertEqual(self.left.compare_project_file(self.path, left_variant, right_variant, selected["digest"])["rows"], [])
        service = next(r for r in self.right.query_objects("service", variant=right_variant)["rows"] if r["qualifier"].startswith("SpeedDid"))
        details = self.right.details(service["id"])
        self.command(self.right, "update_service", {"id": service["id"], "variant_id": right_variant, "name": details["name"], "qualifier": details["qualifier"],
                                                    "sid": 34, "did_id": details["did_id"], "nrcs": [19, 49]})
        self.right.save_project(self.path)
        selected = self.left.compare_file_variants(self.path)
        rows = self.left.compare_project_file(self.path, left_variant, right_variant, selected["digest"])["rows"]
        self.assertEqual([r["category"] for r in rows], ["인스턴스"])
        self.assertIn("NRC", rows[0]["relation"])
        self.assertIsNotNone(rows[0]["id"])

    def test_current_unsaved_changes_compare_against_saved_checkpoint(self):
        variant = self.make_project(self.left)
        self.left.save_project(self.path)
        did = self.left.query_objects("did", variant=variant)["rows"][0]["id"]
        self.command(self.left, "update_did", {"id": did, "name": "Working value"})
        selected = self.left.compare_file_variants(self.path)
        result = self.left.compare_project_file(self.path, variant, variant, selected["digest"])
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["relation"], "이름")
        self.assertEqual(result["rows"][0]["left"], "Working value")
        self.assertEqual(result["rows"][0]["right"], "Reading")


if __name__ == "__main__": unittest.main()
