"""T3 paths and overview navigation against a real working project."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diagstudio.application import Application  # noqa: E402
from diagstudio.domain import DomainError  # noqa: E402


class NavigationTest(unittest.TestCase):
    def setUp(self):
        self.profile = tempfile.TemporaryDirectory(prefix="diag-nav-")
        self.previous = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.profile.name
        self.app = Application()
        self.state = self.app.new_project("Navigation ECU", "NavigationECU")
        self.base = self.state["variants"][0]["id"]
        self.type_id = self.app.query_objects("data_type")["rows"][0]["id"]
        self.state = self.app.execute_command("create_did", {"variant_id": self.base, "name": "Sample", "qualifier": "SampleDid", "identifier": "0x1234", "members": [{"kind": "data", "name": "Reading", "qualifier": "Reading", "data_type_id": self.type_id}]}, self.app.r.revision)
        self.did_id = self.state["selected_id"]

    def tearDown(self):
        self.app.close_project()
        if self.previous is None: os.environ.pop("LOCALAPPDATA", None)
        else: os.environ["LOCALAPPDATA"] = self.previous
        self.profile.cleanup()

    def test_path_round_trip_and_reject_foreign_project(self):
        path = self.app.object_path(self.did_id, self.base)["path"]
        target = self.app.resolve_path(path)
        self.assertEqual((target["key"], target["id"], target["variant"]), (f"var:{self.base}:did", self.did_id, self.base))
        with self.assertRaises(DomainError): self.app.resolve_path(path.replace("diag://", "diag://foreign-"))

    def test_node_path_round_trip_and_bad_node(self):
        path = self.app.node_path(f"var:{self.base}:did")["path"]
        self.assertEqual(self.app.resolve_path(path)["key"], f"var:{self.base}:did")
        with self.assertRaises(DomainError): self.app.resolve_path(path.replace("did", "missing"))

    def test_overview_and_related_type(self):
        rows = self.app.qualifier_overview()["rows"]
        self.assertTrue(any(row["id"] == self.did_id and row["qualifier"] == "SampleDid" for row in rows))
        self.assertEqual([row["id"] for row in self.app.related_targets(self.did_id, "data_type")], [self.type_id])
        self.assertEqual(self.app.qualifier_overview(shortcut=True)["rows"], [])

    def test_reverse_relations_and_nrc_impact(self):
        self.assertEqual([row["id"] for row in self.app.related_targets(self.type_id, "did")], [self.did_id])
        nrc = self.app._object("nrc", "NRC_Link", "NRC_Link")
        self.app.r.update("objects", {"id": nrc}, {"read_only": 1})
        self.app.r.insert("nrcs", id=nrc, code=0x7F)
        state = self.app.execute_command("create_service", {"variant_id": self.base, "name": "TP", "qualifier": "TP", "sid": 62, "nrcs": []}, self.app.r.revision)
        service = state["selected_id"]
        self.app.r.insert("service_nrcs", service_id=service, code=0x7F)
        self.assertEqual([row["id"] for row in self.app.related_targets(nrc, "service")], [service])
        self.assertIn(service, {row["id"] for row in self.app.reference_impact(nrc)["rows"]})
        with self.assertRaises(DomainError):
            self.app.execute_command("update_common", {"id": nrc, "name": "x"}, self.app.r.revision)

    def test_jump_finds_object_beyond_first_page(self):
        result = self.app.query_objects("data_type", page_size=1, target_id=self.type_id)
        self.assertEqual(result["rows"][0]["id"], self.type_id)

    def test_compact_compare_and_translation_undo(self):
        state = self.app.execute_command("create_variant", {"name": "Second", "qualifier": "Second", "source_id": self.base}, self.app.r.revision)
        other = state["selected_id"]
        shared_rows = [row for row in self.app.qualifier_overview()["rows"] if row["id"] == self.did_id]
        self.assertEqual({row["variant"] for row in shared_rows}, {self.base, other})
        self.assertEqual(len({row["path"] for row in shared_rows}), 2)
        compact = self.app.compact_view(self.base, other)
        self.assertTrue(all("relation" in row for row in compact["rows"]))
        self.assertEqual(self.app.compare_variants(self.base, other)["rows"], [])
        self.app.execute_command("set_translation", {"id": self.did_id, "field": "name", "locale": "de-DE", "value": "Messwert"}, self.app.r.revision)
        rows = self.app.translation_overview()["rows"]
        self.assertTrue(any(row["id"] == self.did_id and row["locale"] == "de-DE" and row["value"] == "Messwert" for row in rows))
        self.app.execute_command("undo", {}, self.app.r.revision)
        self.assertFalse(any(row["id"] == self.did_id and row["locale"] == "de-DE" for row in self.app.translation_overview()["rows"]))

    def test_compare_marks_identical_and_changed_copies(self):
        state = self.app.execute_command("create_variant", {"name": "Second", "qualifier": "Second", "source_id": self.base}, self.app.r.revision)
        other = state["selected_id"]
        self.app.execute_command("detach_did", {"id": self.did_id, "variant_id": other}, self.app.r.revision)
        rows = [r for r in self.app.compare_variants(self.base, other)["rows"] if r["category"] == "DID"]
        self.assertEqual([r["relation"] for r in rows], ["사본, 정의 같음"])
        copy = self.app.r.one("SELECT did_id FROM variant_dids WHERE variant_id=?", (other,))["did_id"]
        self.app.execute_command("update_did", {"id": copy, "name": "Renamed"}, self.app.r.revision)
        rows = [r for r in self.app.compare_variants(self.base, other)["rows"] if r["category"] == "DID"]
        self.assertEqual([r["relation"] for r in rows], ["사본, 정의 다름: 이름"])

    def test_state_graph_uses_stored_states(self):
        graph = self.app.state_graph()
        self.assertEqual(len(graph["groups"]), 2)
        self.assertTrue(all(any(state["initial"] for state in group["states"]) for group in graph["groups"]))

    def test_object_path_survives_save_and_reopen(self):
        path = self.app.object_path(self.did_id, self.base)["path"]
        destination = str(Path(self.profile.name) / "Navigation.diagproj")
        self.app.save_project(destination)
        self.app.close_project()
        self.app.open_project(destination)
        target = self.app.resolve_path(path)
        self.assertEqual((target["id"], target["variant"]), (self.did_id, self.base))

    def test_bulk_attribute_is_one_undoable_command(self):
        type_id = self.type_id
        self.app.execute_command("set_attributes", {"ids": [self.did_id, type_id], "key": "user.review", "value": "ready"}, self.app.r.revision)
        rows = [r for r in self.app.attribute_overview()["rows"] if r["key"] == "user.review"]
        self.assertEqual({r["id"] for r in rows}, {self.did_id, type_id})
        scoped = self.app.attribute_overview(scope_variant=self.base)["rows"]
        self.assertEqual([r["id"] for r in scoped if r["key"] == "user.review"], [self.did_id])
        self.app.execute_command("undo", {}, self.app.r.revision)
        self.assertFalse(any(r["key"] == "user.review" for r in self.app.attribute_overview()["rows"]))

    def test_validate_project_reports_object_path(self):
        state_id = self.app.r.one("SELECT id FROM states LIMIT 1")["id"]
        self.app.r.update("objects", {"id": state_id}, {"qualifier": "1bad"})
        report = self.app.validate_project()
        issue = next(i for i in report["issues"] if i["rule_id"] == "VAL-013" and i["object_id"] == state_id)
        self.assertEqual(issue["path"], self.app.object_path(state_id)["path"])
        self.assertIn("severity", issue)
        self.assertIn("blocks_export", issue)

    def test_name_path_round_trip_suffix_and_invalid(self):
        path = self.app.node_name_path(f"var:{self.base}:did")["path"]
        self.assertTrue(path.startswith("/"))
        self.assertEqual(self.app.resolve_name_path(path)["key"], f"var:{self.base}:did")
        suffix = "/".join(path.strip("/").split("/")[1:])
        self.assertEqual(self.app.resolve_name_path(suffix)["key"], f"var:{self.base}:did")
        with self.assertRaises(DomainError): self.app.resolve_name_path("/no/such/node")
        copied = self.app.object_name_path(self.did_id, self.base)
        self.assertTrue(copied["path"].endswith("/Sample"))
        # 복사한 객체 이름 경로를 다시 넣으면 그 목록 노드와 객체로 간다(GUI 스모크 06 단계).
        found = self.app.resolve_name_path(copied["path"])
        self.assertEqual((found["key"], found["id"]), (f"var:{self.base}:did", self.did_id))

    def test_name_path_ambiguity_returns_candidates(self):
        entries = self.app._tree_name_paths()
        target = entries["ecu"]
        original = self.app._tree_name_paths
        self.app._tree_name_paths = lambda: {**entries, "dup-node": target}
        try:
            result = self.app.resolve_name_path(target)
        finally:
            self.app._tree_name_paths = original
        self.assertIn("candidates", result)
        self.assertEqual({c["key"] for c in result["candidates"]}, {"ecu", "dup-node"})

    def test_find_document_text_and_request_bytes(self):
        found = self.app.find_document("Sample")
        self.assertFalse(found["byte_query"])
        self.assertTrue(any(row["id"] == self.did_id for row in found["rows"]))
        # DID 0x1234 읽기 서비스의 고정 요청 바이트 22 12 34.
        byte_found = self.app.find_document("22 12 34")
        self.assertTrue(byte_found["byte_query"])
        sids = [self.app.r.one("SELECT sid FROM services WHERE id=?", (row["id"],))["sid"] for row in byte_found["rows"]]
        self.assertIn(34, sids)
        self.assertEqual(self.app.find_document("zzz-no-such-name")["rows"], [])
        with self.assertRaises(DomainError): self.app.find_document("   ")

    def test_did_list_adds_candela_columns(self):
        result = self.app.query_objects("did", self.base)
        labels = [label for _, label in result["columns"]]
        for label in ("진단 클래스", "고장 메모리 사용", "DID 데이터"):
            self.assertIn(label, labels)
        row = next(r for r in result["rows"] if r["id"] == self.did_id)
        self.assertEqual(row["did_data"], "Reading")
        self.assertFalse(row["fault_memory"])
        self.assertEqual(row["diagnostic_class"], "Read")

    def test_service_list_shows_candela_patterns(self):
        result = self.app.query_objects("service", self.base)
        labels = [label for _, label in result["columns"]]
        for label in ("요청", "긍정 응답", "부정 응답"): self.assertIn(label, labels)
        read = next(r for r in result["rows"] if r["sid"] == 0x22)
        self.assertEqual(read["request_pattern"], "22 12 34")
        self.assertEqual(read["positive_pattern"], "62 12 34 zz")
        self.assertEqual(read["negative_pattern"], "7F 22 rc")

    def test_did_layout_member_rows_and_byte_grid(self):
        from diagstudio.presentation import member_rows, byte_grid
        record = self.app.details(self.did_id)
        rows = member_rows(record["layout"])
        self.assertEqual([r["name"] for r in rows], ["Reading"])
        self.assertEqual(rows[0]["byte"], "0")
        grid = byte_grid(record["layout"])
        self.assertTrue(grid and all(label == "Reading" for label, _ in grid[0]["cells"]))

    def test_did_fault_memory_flag_from_fault_memory_instance(self):
        fm_class = self.app._object("diagnostic_class", "FaultMemory", "FaultMemory")
        self.app.r.insert("diagnostic_classes", id=fm_class, role="fault_memory")
        instance = self.app._object("diagnostic_instance", "FM_Instance", "FM Instance")
        self.app.r.insert("diagnostic_instances", id=instance, class_id=fm_class, owner_variant_id=self.base)
        self.app.r.insert("variant_instances", variant_id=self.base, instance_id=instance, ordinal=50)
        self.app.r.insert("instance_dids", instance_id=instance, did_id=self.did_id, role="data")
        row = next(r for r in self.app.query_objects("did", self.base)["rows"] if r["id"] == self.did_id)
        self.assertTrue(row["fault_memory"])


if __name__ == "__main__": unittest.main()
