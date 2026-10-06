"""Nested type impact, atomic independent copies, and reversible delete preflight."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from fractions import Fraction

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagstudio.application import Application
from diagstudio.domain import DomainError, convert, physical_text


class ReferenceToolsTest(unittest.TestCase):
    def setUp(self):
        self.profile = tempfile.TemporaryDirectory(prefix="diag-reference-")
        self.previous = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.profile.name
        self.app = Application()
        self.base = self.app.new_project("Reference ECU", "ReferenceECU")["variants"][0]["id"]
        self.leaf = self.command("create_type", {"name": "Temperature", "qualifier": "Temperature",
            "encoding": "unsigned", "bit_length": 16, "conversion": "linear", "factor_text": "0.1",
            "offset_text": "-40", "unit": "C", "exclusions": [{"lower_raw": "65535", "kind": "undef", "label": "Unknown", "locale": "en-US"}]})
        self.packet = self.command("create_type", {"name": "Packet", "qualifier": "Packet", "encoding": "packet",
            "members": [{"name": "First", "qualifier": "First", "data_type_id": self.leaf, "default_text": "650"},
                        {"name": "Second", "qualifier": "Second", "data_type_id": self.leaf}]})
        self.did = self.command("create_did", {"variant_id": self.base, "name": "Reading", "qualifier": "Reading", "identifier": "0x1234",
            "members": [{"name": "Packet", "qualifier": "Packet", "data_type_id": self.packet}]})

    def tearDown(self):
        self.app.close_project()
        if self.previous is None: os.environ.pop("LOCALAPPDATA", None)
        else: os.environ["LOCALAPPDATA"] = self.previous
        self.profile.cleanup()

    def command(self, action, payload):
        return self.app.execute_command(action, payload, self.app.r.revision).get("selected_id")

    def test_nested_impact_includes_all_variants_and_service(self):
        high = self.command("create_variant", {"source_id": self.base, "name": "High", "qualifier": "High"})
        rows = self.app.reference_impact(self.leaf)["rows"]
        ids = {row["id"] for row in rows}
        self.assertTrue({self.packet, self.did, self.base, high}.issubset(ids))
        service = next(row for row in rows if row["kind"] == "service")
        self.assertIn("High", service["variants"])
        self.assertIn("기본 변형", service["variants"])
        self.assertEqual(next(row for row in rows if row["id"] == self.did)["reach"], "간접 영향")
        for row in rows:
            if row["path"]: self.app.resolve_path(row["path"])

    def test_copy_packet_clones_shared_child_once_and_undo_redo(self):
        self.command("set_attributes", {"ids": [self.leaf], "key": "user.review", "value": "keep"})
        before = self.app.r.one("SELECT count(*) n FROM objects")["n"]
        cursor = self.app.r.history()["cursor_position"]
        copied = self.command("copy_type", {"id": self.packet, "name": "Independent", "qualifier": "Independent"})
        source = self.app.details(self.packet)
        target = self.app.details(copied)
        child_ids = {member["data_type_id"] for member in target["members"]}
        self.assertEqual(len(child_ids), 1)
        copied_leaf = next(iter(child_ids))
        self.assertNotEqual(copied_leaf, self.leaf)
        self.assertNotEqual(target["structure_id"], source["structure_id"])
        self.assertEqual(target["members"][0]["default_text"], "650")
        self.assertEqual(self.app.details(copied_leaf)["exclusions"][0]["lower_raw"], "65535")
        self.assertEqual(self.app.r.one("SELECT value_text FROM object_attributes WHERE object_id=?", (copied_leaf,))["value_text"], "keep")
        self.assertEqual(self.app.r.history()["cursor_position"], cursor + 1)
        self.command("undo", {})
        self.assertEqual(self.app.r.one("SELECT count(*) n FROM objects")["n"], before)
        self.command("redo", {})
        self.assertEqual(self.app.details(copied)["qualifier"], "Independent")
        self.command("update_type", {"id": copied_leaf, "encoding": "unsigned", "bit_length": 24, "conversion": "identity", "exclusions": []})
        self.assertEqual(self.app.details(self.leaf)["bit_length"], 16)
        self.assertEqual(self.app.details(copied_leaf)["bit_length"], 24)

    def test_copy_rejects_external_did_reference_atomically(self):
        dependent = self.command("create_type", {"name": "External", "qualifier": "External", "encoding": "packet",
            "members": [{"kind": "did_data", "did_id": self.did}]})
        before = "\n".join(self.app.r.db.iterdump())
        with self.assertRaises(DomainError) as rejected:
            self.command("copy_type", {"id": dependent, "name": "Copy", "qualifier": "Copy"})
        self.assertEqual(rejected.exception.code, "UNSUPPORTED_COPY")
        self.assertEqual("\n".join(self.app.r.db.iterdump()), before)

    def test_readonly_child_prevents_partial_type_copy(self):
        self.app.r.db.execute("UPDATE objects SET read_only=1 WHERE id=?", (self.leaf,))
        before = "\n".join(self.app.r.db.iterdump())
        with self.assertRaises(DomainError) as rejected:
            self.command("copy_type", {"id": self.packet, "name": "Copy", "qualifier": "Copy"})
        self.assertEqual(rejected.exception.code, "READ_ONLY")
        self.assertEqual("\n".join(self.app.r.db.iterdump()), before)

    def test_delete_preview_has_no_db_or_history_effect(self):
        before = "\n".join(self.app.r.db.iterdump())
        blocked = self.app.deletion_impact(self.leaf, self.base)
        self.assertFalse(blocked["can_delete"])
        self.assertTrue(blocked["rows"])
        initial = self.app.r.one("SELECT initial_state_id FROM state_groups LIMIT 1")["initial_state_id"]
        self.assertFalse(self.app.deletion_impact(initial, self.base)["can_delete"])
        self.assertEqual("\n".join(self.app.r.db.iterdump()), before)
        dtc = self.command("create_dtc", {"variant_id": self.base, "name": "Fault", "qualifier": "Fault", "code": 1})
        before = "\n".join(self.app.r.db.iterdump())
        allowed = self.app.deletion_impact(dtc, self.base)
        self.assertTrue(allowed["can_delete"])
        self.assertEqual(allowed["removed"], ["Fault"])
        self.assertEqual("\n".join(self.app.r.db.iterdump()), before)
        self.command("delete", {"id": dtc, "variant_id": self.base})
        self.assertFalse(self.app.r.one("SELECT 1 FROM dtcs WHERE id=?", (dtc,)))

    def test_copy_save_reopen_keeps_independent_links(self):
        copied = self.command("copy_type", {"id": self.packet, "name": "Saved copy", "qualifier": "SavedCopy"})
        path = str(Path(self.profile.name) / "copy.diagproj")
        self.app.save_project(path)
        self.app.close_project()
        self.app.open_project(path)
        self.assertNotEqual(self.app.details(copied)["members"][0]["data_type_id"], self.leaf)
        self.assertEqual(self.app.r.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")


class NumericDisplayTest(unittest.TestCase):
    def test_decimal_scale_and_u64_physical_value(self):
        typ = {"encoding": "unsigned", "bit_length": 64, "conversion": "linear", "factor_text": "0.1", "offset_text": "-40"}
        self.assertEqual(convert(typ, "650")[1], "25.0")
        self.assertEqual(convert({**typ, "factor_text": "1", "offset_text": "0"}, str(2**64 - 1))[1], str(2**64 - 1))
        self.assertEqual(physical_text(Fraction(2**64 - 1)), str(2**64 - 1))

    def test_approximate_display_retains_integer_zeroes(self):
        self.assertEqual(physical_text(Fraction(10**18, 3)), "333333333333000000…")


if __name__ == "__main__": unittest.main()
