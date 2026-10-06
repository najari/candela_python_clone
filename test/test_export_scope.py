"""선택 변형의 출력 차단, 미사용 원문 타입, 정보 메시지의 비차단 회귀."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagstudio.application import Application
from diagstudio.domain import DomainError, validate
from diagstudio.export_scope import export_objects, unused_source_types, validate_export
from diagstudio.interchange import export_odx, cdd_preview


class ExportScopeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="diag-export-")
        self.previous = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.temp.name
        self.app = Application()
        self.base = self.app.new_project("ECU", "ECU")["variants"][0]["id"]
        self.other = self.command("create_variant", {"source_id": self.base, "name": "Other", "qualifier": "Other"})
        self.leaf = self.command("create_type", {"name": "Value", "qualifier": "Value", "encoding": "unsigned", "bit_length": 16})
        self.packet = self.command("create_type", {"name": "Packet", "qualifier": "Packet", "encoding": "packet",
            "members": [{"name": "Value", "qualifier": "Value", "data_type_id": self.leaf}]})
        self.did = self.command("create_did", {"variant_id": self.base, "name": "Reading", "qualifier": "Reading", "identifier": "0x1234",
            "members": [{"name": "Packet", "qualifier": "Packet", "data_type_id": self.packet}]})

    def tearDown(self):
        self.app.close_project()
        if self.previous is None: os.environ.pop("LOCALAPPDATA", None)
        else: os.environ["LOCALAPPDATA"] = self.previous
        self.temp.cleanup()

    def command(self, action, payload):
        return self.app.execute_command(action, payload, self.app.r.revision).get("selected_id")

    def snapshot(self, owner=None, semantic=1):
        s = self.app.r.snapshot()
        s.tables["unsupported_nodes"] = [{"source_file_id": "source", "node_path": "/CANDELA/ECUDOC/EXTENSION",
            "owner_object_id": owner, "semantic": semantic, "reason_code": "Example unsupported meaning"}]
        return s

    def test_nested_shared_reference_and_selected_variants(self):
        s = self.snapshot(self.leaf)
        self.assertTrue({self.did, self.packet, self.leaf} <= export_objects(s, [self.base]))
        self.assertEqual(validate_export(s, [self.base])["blocking_count"], 1)
        other = validate_export(s, [self.other])
        self.assertEqual(other["blocking_count"], 0)
        issue = next(i for i in other["issues"] if i["rule_id"] == "VAL-012")
        self.assertFalse(issue["in_scope"])
        self.assertTrue(issue["scope_reason"])
        self.assertEqual(validate_export(s, [self.base, self.other])["blocking_count"], 1)
        self.assertTrue(validate(s)["issues"][-1]["blocks_export"])

    def test_unresolved_and_document_owners_remain_blocking(self):
        for owner in (None, "missing-owner", self.app.r.snapshot().rows("ecu")[0]["id"]):
            self.assertEqual(validate_export(self.snapshot(owner), [self.other])["blocking_count"], 1)

    def test_disabled_service_is_outside_but_its_did_is_included(self):
        s = self.app.r.snapshot()
        service = s.rows("services")[0]["id"]
        s.tables["services"][0]["enabled"] = 0
        s.tables["objects"] = [{**o, "supported": 0} if o["id"] == service else o for o in s.rows("objects")]
        included = export_objects(s, [self.base])
        self.assertNotIn(service, included)
        self.assertIn(self.did, included)
        self.assertEqual(validate_export(s, [self.base])["blocking_count"], 0)

    def test_invalid_selection_and_cancellation_do_not_mutate(self):
        s = self.app.r.snapshot()
        before = copy.deepcopy(s.tables)
        for variants in ([], ["missing"]):
            with self.assertRaises(DomainError): validate_export(s, variants)
        def cancel(): raise DomainError("cancel", "CANCELLED")
        with self.assertRaises(DomainError) as e: validate_export(s, [self.base], cancel)
        self.assertEqual(e.exception.code, "CANCELLED")
        self.assertEqual(before, s.tables)

    def test_information_only_export_reaches_schema_and_writes_report(self):
        s = self.snapshot(semantic=0)
        destination = Path(self.temp.name) / "out.odx-d"
        # 경로·차단·보고서 배선 검사 전용 스키마. 공식 ODX 스키마 검증 성공으로 취급하지 않는다.
        xsd = Path(self.temp.name) / "test-only.xsd"
        xsd.write_text('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="ODX"><xs:complexType><xs:sequence><xs:any minOccurs="0" maxOccurs="unbounded" processContents="skip"/></xs:sequence><xs:anyAttribute processContents="skip"/></xs:complexType></xs:element></xs:schema>')
        result = export_odx(s, [self.base], destination, xsd)
        self.assertTrue(destination.is_file())
        self.assertEqual(result["preflight"]["blocking_count"], 0)
        saved = json.loads(Path(str(destination) + ".report.json").read_text())
        self.assertEqual(saved["preflight"]["issues"][0]["rule_id"], "INF-001")
        original = destination.read_bytes()
        with self.assertRaises(DomainError): export_odx(self.snapshot(), [self.base], destination, xsd)
        self.assertEqual(destination.read_bytes(), original)

    def test_unused_raw_type_chains_and_cycles_keep_external_dependencies(self):
        xml = b'''<CANDELA><ECUDOC><DATATYPES>
        <FORMULADT id="unused"><REF dtref="cycle"/></FORMULADT>
        <STRUCTDT id="cycle"><REF dtref="unused"/></STRUCTDT>
        <IDENT id="used"/><STRUCTDT id="container"><REF dtref="used"/></STRUCTDT>
        </DATATYPES><PROTOCOLSERVICES><SERVICE dtref="container"/></PROTOCOLSERVICES></ECUDOC></CANDELA>'''
        s = self.snapshot()
        s.tables["source_files"] = [{"id": "source", "kind": "cdd", "content": xml}]
        unused = unused_source_types(s)
        self.assertEqual(unused, {("source", "/CANDELA/ECUDOC/DATATYPES/FORMULADT"),
                                  ("source", "/CANDELA/ECUDOC/DATATYPES/STRUCTDT[1]")})
        s.tables["unsupported_nodes"][0]["node_path"] = "/CANDELA/ECUDOC/DATATYPES/FORMULADT"
        self.assertEqual(validate_export(s, [self.base])["blocking_count"], 0)
        s.tables["source_files"][0]["content"] = b"broken xml"
        self.assertEqual(validate_export(s, [self.base])["blocking_count"], 1)

    def test_source_path_deduplication_is_per_source(self):
        s = self.snapshot(semantic=0)
        s.tables["import_issues"] = [{"source_file_id": "another-source", "node_path": "/CANDELA/ECUDOC/EXTENSION",
            "object_id": None, "blocks_export": 1, "message": "Different source"}]
        self.assertEqual(validate_export(s, [self.base])["blocking_count"], 1)

    def test_output_only_numeric_limit_is_scoped(self):
        s = self.app.r.snapshot()
        s.get("data_types", self.leaf)["bit_length"] = 64
        own = validate_export(s, [self.base])
        self.assertTrue(any(i["object_id"] == self.leaf and i["field"] == "bit_length" for i in own["issues"]))
        self.assertEqual(validate_export(s, [self.other])["blocking_count"], 0)


class SourceMetadataTest(unittest.TestCase):
    def test_installed_abs_metadata_and_unknown_extension(self):
        path = Path(os.environ.get("DIAGAUTHOR_CDD_EXAMPLES", r"C:\Users\Public\Documents\Vector\CANdelaStudio\13.0\Examples")) / "ABS_ESP-Example.cdd"
        if not path.is_file(): self.skipTest("ABS 설치 예제 없음")
        preview = cdd_preview(path)
        attrs = {a["key"]: a["value_text"] for a in preview["ecu"]["attributes"]}
        self.assertEqual(attrs["cdd.protocol_standard"], "UDS")
        self.assertEqual(attrs["cdd.variant_coding_mode"], "none")
        self.assertEqual(attrs["cdd.document_template_qualifier"], "New_ECU")
        for tag in ("PROTOCOLSTANDARD", "DOCTMPL", "VCKMGR"):
            row = next(n for n in preview["unsupported"] if n["node_path"].endswith("/" + tag))
            self.assertEqual(row["semantic"], 0)
            self.assertIn(tag, row["raw_xml"])
        with tempfile.TemporaryDirectory() as folder:
            previous = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = folder
            app = Application()
            try:
                state = app.apply_cdd(preview)
                base = next(v["id"] for v in state["variants"] if v["is_base"])
                report = app.validate_project(variants=[base])
                excluded_types = [i for i in report["issues"] if i["in_scope"] is False and "/DATATYPES/" in i.get("node_path", "")]
                # 19차: 13 → 8. 29차: COMPTBL 3·FORMULADT 3·packet 2를 가져오고, MUXDT는 인스턴스가 쓰지 않는
                # 프로토콜 템플릿(RD·RMBA·WMBA·RequestUpload)에서만 참조되므로 범위 밖 → MUXDT 하나.
                self.assertEqual([i["node_path"].rsplit("/", 1)[-1] for i in excluded_types], ["MUXDT"])
                self.assertFalse(any(i["blocks_export"] for i in excluded_types))
                self.assertTrue(any(i["field"]=="conditions" for i in report["issues"]))
                self.assertEqual(app.r.snapshot().rows("source_files"), [])
                self.assertTrue(app.r.snapshot(include_sources=True).rows("source_files"))
            finally:
                app.close_project()
                if previous is None: os.environ.pop("LOCALAPPDATA", None)
                else: os.environ["LOCALAPPDATA"] = previous
        # 설치 예제를 복제하지 않고 자체 최소 문서의 미지원 확장만 검사한다.
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "extension.cdd"
            source.write_text('<CANDELA dtdvers="13.0.101"><ECUDOC doctype="inst"><DATATYPES/><DIDS/><ECU id="ecu"><QUAL>ECU</QUAL><VAR id="base" base="1"><QUAL>Base</QUAL></VAR></ECU><PROTOCOLSTANDARD unknown="1">UDS</PROTOCOLSTANDARD><VCKMGR vckmode="active"/></ECUDOC></CANDELA>')
            rows = cdd_preview(source)["unsupported"]
            self.assertTrue(all(n["semantic"] for n in rows))


if __name__ == "__main__": unittest.main()
