"""CDD 서비스·상태·DTC 가져오기, 스키마 2(범위·예외값)와 마이그레이션, ODX 제약 출력 회귀 테스트. Qt 없이 실행한다.

기대값 근거: CANdelaStudio 13 설치 예제 ABS_ESP-Example.cdd와 Repository/ODXEnvelope(Vector 출력).
실행: .venv\\Scripts\\python.exe -m unittest discover -s test -v
"""
from pathlib import Path
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import uuid
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXAMPLES = Path(os.environ.get("DIAGAUTHOR_CDD_EXAMPLES", r"C:\Users\Public\Documents\Vector\CANdelaStudio\13.0\Examples"))
_PROFILE = tempfile.mkdtemp(prefix="diagauthor-test-")
os.environ["LOCALAPPDATA"] = _PROFILE

from diagstudio import migrations, persistence  # noqa: E402
from diagstudio.application import Application  # noqa: E402
from diagstudio.domain import DomainError, convert  # noqa: E402
from diagstudio.interchange import XSI, build_odx, cdd_preview  # noqa: E402
from diagstudio.presentation import details_text  # noqa: E402

ABS = EXAMPLES / "ABS_ESP-Example.cdd"


def tearDownModule():
    shutil.rmtree(_PROFILE, ignore_errors=True)


def without_record_services(s):
    """ODX 출력 테스트용: 고장 메모리 레코드 자리(0x19 04/06)가 있는 서비스를 끈다(그 서비스는 출력 근거가 없어 차단된다)."""
    items = {m["nested_structure_id"]: m["structure_id"] for m in s.rows("structure_members") if m["kind"] == "iteration"}
    blocked = {m["structure_id"] for m in s.rows("structure_members") if m["kind"] == "fault_record"}
    blocked |= {items[x] for x in blocked if x in items}
    off = {t["service_id"] for t in s.rows("telegrams") if t["structure_id"] in blocked}
    s.tables["services"] = [{**x, "enabled": 0} if x["id"] in off else x for x in s.rows("services")]
    s.indices["services"] = {x["id"]: x for x in s.tables["services"]}
    return s


class ImportedAbs(unittest.TestCase):
    """ABS 예제를 한 번 가져와 여러 검사를 공유한다."""

    @classmethod
    def setUpClass(cls):
        if not ABS.is_file(): raise unittest.SkipTest(f"예제 CDD 없음: {ABS}")
        cls.app = Application()
        cls.state = cls.app.apply_cdd(cdd_preview(ABS))
        cls.base = next(v["id"] for v in cls.state["variants"] if v["is_base"])
        cls.services = {r["qualifier"]: r["id"] for r in cls.app.query_objects("service", cls.base, page_size=1000)["rows"]}

    @classmethod
    def tearDownClass(cls):
        cls.app.close_project()

    def telegrams(self, qualifier, values=None):
        oid = self.services[qualifier]
        fields = self.app.preview_fields(oid)
        supplied = {f["id"]: {"value": "0"} for f in fields}
        supplied.update(values or {})
        return {t["kind"]: t["hex"] for t in self.app.build_telegram_preview(oid, supplied)["telegrams"]}


class ExplorerImportTest(ImportedAbs):
    def test_shortcut_attributes_and_variant_comparison(self):
        shortcuts = self.app.qualifier_overview(shortcut=True)["rows"]
        self.assertTrue(shortcuts)
        self.assertTrue(all(self.app.r.one("SELECT value_text FROM object_attributes WHERE object_id=? AND key='cdd.shortcut_qualifier'", (row["id"],))["value_text"] == row["qualifier"] for row in shortcuts))
        c_sample = next(v["id"] for v in self.state["variants"] if v["name"].startswith("C"))
        compact = self.app.compact_view(self.base, c_sample)["rows"]
        self.assertTrue(any(row["name"] == "Yaw Rate Sensor" and row["right"] == "원본" for row in compact))
        self.assertTrue(self.app.attribute_overview()["rows"])

    def test_instance_go_to_did_uses_reference(self):
        service = self.app.details(self.services["VIN_Read"])
        related = self.app.related_targets(service["instance_id"], "did")
        self.assertTrue(any(row["qualifier"] == "VIN" for row in related))


class PlacementTest(ImportedAbs):
    """schema 4 변형 배치. 근거: ABS 예제 VAR의 DIAGCLASS/DIAGINST 순서, DCLTMPL tmplref·single 속성."""

    def layout(self, variant):
        rows = self.app.r.all("""SELECT o.qualifier cls, c.singleton, oi.qualifier inst FROM variant_classes vc
            JOIN diagnostic_classes c ON c.id=vc.class_id JOIN objects o ON o.id=c.id
            LEFT JOIN (SELECT vi.variant_id, vi.instance_id, vi.ordinal, di.class_id FROM variant_instances vi JOIN diagnostic_instances di ON di.id=vi.instance_id) x
              ON x.variant_id=vc.variant_id AND x.class_id=vc.class_id
            LEFT JOIN objects oi ON oi.id=x.instance_id
            WHERE vc.variant_id=? ORDER BY vc.ordinal, x.ordinal""", (variant,))
        result = {}
        for r in rows: result.setdefault((r["cls"], r["singleton"]), []).append(r["inst"])
        return result

    def test_one_class_per_template(self):
        names = [r["qualifier"] for r in self.app.r.all("SELECT o.qualifier FROM diagnostic_classes c JOIN objects o ON o.id=c.id")]
        self.assertEqual(len(names), 13)
        self.assertEqual(len(names), len(set(names)))

    def test_base_variant_order_and_singletons(self):
        layout = self.layout(self.base)
        self.assertEqual([k[0] for k in layout], ["Sessions", "Identification", "SecurityAccess", "FaultMemory", "ControlDTCSetting", "DynamicData", "StoredData",
                                                  "VariantCoding", "IOControl", "Routine", "CommunicationControl", "EcuReset", "TesterPresent"])
        self.assertEqual({k[0] for k in layout if k[1]}, {"FaultMemory", "TesterPresent"})
        self.assertEqual(layout[("Sessions", 0)], ["DefaultSession", "ProgrammingSession", "ExtendedDiagnosticSession"])

    def test_instances_without_importable_services_are_kept(self):
        self.assertEqual(self.app.r.one("SELECT count(*) n FROM diagnostic_instances")["n"], 42)
        layout = self.layout(self.base)
        self.assertEqual(len(layout[("CommunicationControl", 0)]), 4)
        self.assertIn("Wheel_Speed", layout[("DynamicData", 0)])
        fm = self.app.r.one("SELECT i.id FROM diagnostic_instances i JOIN objects o ON o.id=i.id WHERE o.qualifier='FaultMemory'")["id"]
        self.assertTrue(self.app.r.one("SELECT 1 FROM unsupported_nodes WHERE owner_object_id=?", (fm,)))

    def test_local_instance_keeps_document_position(self):
        c_sample = next(v["id"] for v in self.state["variants"] if v["qualifier"].startswith("C"))
        self.assertEqual(self.layout(c_sample)[("DynamicData", 0)][:3], ["Braking", "Yaw_Rate_Sensor", "Supply_Voltage_ECU"])

    def test_placement_is_valid(self):
        self.assertNotIn("VAL-021", {i["rule_id"] for i in self.app.validate_project()["issues"]})


class TreeDataTest(ImportedAbs):
    """탐색 트리 데이터와 트리 노드가 여는 목록(Qt 없이). 근거: 도움말 Navigation Tree [TREE-R010], Icons [TREE-R400]."""

    def test_tree_instances_have_identifier_share_and_warnings(self):
        tree = self.state["tree"]
        base = next(v for v in tree["variants"] if v["is_base"])
        classes = {c["qualifier"]: c for c in base["classes"]}
        self.assertEqual([(i["identifier_text"], i["share"]) for i in classes["Sessions"]["instances"]], [("01", "base_shared"), ("02", "base_shared"), ("03", "base_shared")])
        self.assertTrue(classes["Sessions"]["instances"][0]["required"])
        self.assertTrue(classes["FaultMemory"]["singleton"])
        # 29차: FaultMemory 인스턴스의 보존 원문은 EVENTDATA(Event 데이터, ODX 출력 대상 아님)뿐이라 경고가 없다.
        self.assertEqual(classes["FaultMemory"]["instances"][0]["warnings"], 0)
        c_sample = next(v for v in tree["variants"] if v["name"].startswith("C"))
        local = {i["name"]: i["share"] for c in c_sample["classes"] for i in c["instances"]}
        self.assertEqual(local["Yaw Rate Sensor"], "local")

    def test_session_group_comes_first_with_initial_state(self):
        groups = self.state["tree"]["state_groups"]
        self.assertEqual([st["name"] for st in groups[0]["states"]], ["Default", "Programming", "Extended"])
        self.assertEqual(groups[1]["states"][0]["name"], "Locked")

    def test_dependency_matrix(self):
        rows = self.app.query_objects("dependency", self.base, page_size=1000)["rows"]
        self.assertEqual(rows[0]["qualifier"], "DefaultSession_Start")  # 문서 순서
        key = next(r for r in rows if r["qualifier"] == "KeyLevel1_Send")
        self.assertEqual(sorted(v for k, v in key.items() if k.startswith("st:")), sorted(["✗", "✓", "✓", "✓ → Unlocked L1", "✓"]))
        read = next(r for r in rows if r["qualifier"] == "VIN_Read")
        self.assertEqual({v for k, v in read.items() if k.startswith("st:")}, {"—"})

    def test_overviews(self):
        result = self.app.query_objects("did_overview", page_size=1000)
        self.assertEqual(len([c for c in result["columns"] if c[0].startswith("v:")]), 4)
        self.assertEqual(result["total"], 19)
        self.assertEqual(self.app.query_objects("dtc_overview", page_size=1000)["total"], 31)

    def test_class_and_instance_details(self):
        base = next(v for v in self.state["tree"]["variants"] if v["is_base"])
        ident = next(c for c in base["classes"] if c["qualifier"] == "Identification")
        text = details_text(self.app.details(ident["instances"][0]["id"]))
        self.assertIn("F190", text)
        self.assertIn("VIN Read", text)
        self.assertIn("Identification", details_text(self.app.details(ident["id"])))


class TemplateListTest(ImportedAbs):
    """schema 5 읽기 전용 목록과 원본·참조. 근거: ABS 예제 NEGRESCODES·PROTOCOLSERVICES·JOBCNR/JOBCNRREF·DIAGINST/DIAGINSTREF."""

    def test_nrc_names_are_shown_on_services(self):
        self.assertEqual(self.app.query_objects("nrc", page_size=1000)["total"], 40)
        text = details_text(self.app.details(self.services["VIN_Read"]))
        self.assertIn("0x22 Conditions not correct", text)

    def test_protocol_service_summary(self):
        rows = {r["qualifier"]: r for r in self.app.query_objects("protocol_service", page_size=1000)["rows"]}
        self.assertEqual(len(rows), 31)
        dsc = rows["DSC"]
        self.assertEqual((dsc["request_text"], dsc["negative_text"]), ("10 ‹DiagSessionType›", "7F 10 {RC}"))
        self.assertEqual((dsc["template_count"], dsc["service_count"]), (1, 3))

    def test_jobs_are_placed_with_owner_and_references(self):
        tree = self.state["tree"]
        base = next(v for v in tree["variants"] if v["is_base"])
        self.assertEqual([(j["name"], j["reference"]) for j in base["jobs"]], [("Generic Jobs", False), ("Flash Jobs", False)])
        ext = next(v for v in tree["variants"] if not v["is_base"])
        self.assertTrue(all(j["reference"] for j in ext["jobs"]))
        job = base["jobs"][0]["id"]
        self.assertEqual(self.app.query_objects("unsupported", scope=job)["total"], 1)

    def test_instance_owner_follows_diaginst_position(self):
        tree = self.state["tree"]
        shares = {(v["name"], i["name"]): i["share"] for v in tree["variants"] for c in v["classes"] for i in c["instances"]}
        self.assertEqual(shares[("Base Variant", "VIN")], "base_shared")
        self.assertEqual(shares[("A Sample", "DTC Setting Mode On")], "local")
        base = next(v["id"] for v in tree["variants"] if v["is_base"])
        owners = {r["owner_variant_id"] for r in self.app.r.all("SELECT owner_variant_id FROM diagnostic_instances")}
        self.assertIn(base, owners)
        self.assertNotIn(None, owners)


class ImportedOverviewTest(ImportedAbs):
    """가져온 ABS 예제에서 T3·T4 개요가 끝까지 계산되고, 모든 경로가 다시 열리는지 확인한다."""

    def test_qualifier_overview_paths_resolve(self):
        rows = self.app.qualifier_overview()["rows"]
        self.assertGreater(len(rows), 500)
        tree_keys = set()
        for v in self.state["tree"]["variants"]:
            prefix = f"var:{v['id']}"
            tree_keys |= {prefix, prefix + ":did", prefix + ":dtc"}
            for c in v["classes"]:
                tree_keys.add(f"{prefix}:cls:{c['id']}")
                tree_keys |= {f"{prefix}:inst:{i['id']}" for i in c["instances"]}
            tree_keys |= {f"{prefix}:job:{j['id']}" for j in v["jobs"]}
        for row in rows:
            key = self.app.resolve_path(row["path"])["key"]
            if key.startswith("var:"): self.assertIn(key, tree_keys, row)

    def test_extended_only_instance_is_not_located_in_base(self):
        c_sample = next(v for v in self.state["tree"]["variants"] if v["name"].startswith("C"))
        local = next(i for c in c_sample["classes"] for i in c["instances"] if i["share"] == "local")
        with self.assertRaises(DomainError): self.app.locate_object(local["id"], self.base)
        self.assertEqual(self.app.locate_object(local["id"])["variant"], c_sample["id"])

    def test_file_compare_of_reimported_cdd_and_variants(self):
        out = Path(tempfile.mkdtemp(prefix="diagauthor-cmp-"))
        other = Application()
        try:
            state = other.apply_cdd(cdd_preview(ABS))
            path = str(out / "abs.diagproj")
            other.save_project(path)
            saved = self.app.compare_file_variants(path)
            same = next(v["id"] for v in saved["variants"] if v["is_base"])
            # 같은 CDD를 다시 가져온 파일: UUID가 달라도 차이가 없어야 한다.
            self.assertEqual(self.app.compare_project_file(path, self.base, same, saved["digest"])["rows"], [])
            c_saved = next(v["id"] for v in saved["variants"] if v["name"].startswith("C"))
            rows = self.app.compare_project_file(path, self.base, c_saved, saved["digest"])["rows"]
            codes = {r["code"]: r["relation"] for r in rows}
            self.assertEqual(codes["DynamicData / Yaw_Rate_Sensor"], "대상에만 있음")
            self.assertIn("원본 변형", codes["ControlDTCSetting / DTC_Setting_Mode_On"])
            self.assertNotIn("Sessions / DefaultSession", codes)
        finally:
            other.close_project()
            shutil.rmtree(out, ignore_errors=True)

    def test_other_overviews(self):
        expected = self.app.r.one("""SELECT count(*) n FROM object_attributes a JOIN services s ON s.id=a.object_id
            JOIN variant_instances vi ON vi.instance_id=s.instance_id WHERE a.key='cdd.shortcut_qualifier'""")["n"]
        self.assertEqual(len(self.app.qualifier_overview(True)["rows"]), expected)  # 서비스 × 사용 변형
        self.assertGreaterEqual(expected, 185)
        self.assertEqual(len(self.app.attribute_overview()["rows"]), self.app.r.one("SELECT count(*) n FROM object_attributes")["n"])
        self.assertTrue(self.app.translation_overview()["rows"])
        graph = self.app.state_graph()
        self.assertIn("Unlocked L1", {s["name"] for g in graph["groups"] for s in g["states"]})

    def test_reference_impact_includes_fault_memory_and_resolves_paths(self):
        did = self.app.r.one("SELECT id FROM dids WHERE identifier=266")["id"]
        impact = self.app.reference_impact(did)
        self.assertTrue(any(row["relation"] == "스냅샷 DID 사용" for row in impact["rows"]))
        self.assertTrue(any(row["kind"] == "service" for row in impact["rows"]))
        for row in impact["rows"]:
            if row["path"]: self.app.resolve_path(row["path"])
        self.assertFalse(self.app.deletion_impact(did, self.base)["can_delete"])


class ManualPlacementTest(unittest.TestCase):
    def test_new_service_and_variant_copy_keep_placement(self):
        app = Application()
        try:
            state = app.new_project("Manual ECU", "ManualECU")
            base = state["variants"][0]["id"]
            state = app.execute_command("create_type", {"name": "Raw", "qualifier": "Raw_T", "encoding": "unsigned", "bit_length": 8, "conversion": "identity"}, app.r.revision)
            for n in range(2):
                app.execute_command("create_did", {"variant_id": base, "name": f"D{n}", "qualifier": f"D{n}", "identifier": f"0x010{n}",
                                                   "members": [{"kind": "data", "data_type_id": state["selected_id"], "name": "V", "qualifier": "V"}]}, app.r.revision)
            app.execute_command("create_variant", {"source_id": base, "name": "Copy", "qualifier": "Copy"}, app.r.revision)
            rows = app.r.all("SELECT variant_id, ordinal FROM variant_classes")
            self.assertEqual(len(rows), 2)
            self.assertEqual({r["ordinal"] for r in rows}, {0})
            self.assertEqual(sorted(r["ordinal"] for r in app.r.all("SELECT ordinal FROM variant_instances WHERE variant_id=?", (base,))), [0, 1])
            self.assertNotIn("VAL-021", {i["rule_id"] for i in app.validate_project()["issues"]})
            # 기본 변형에서 DID를 분리하면 기본 변형은 새 사본을 원본으로 갖고, 옛 인스턴스의 원본은 남은 변형(Copy)으로 넘어간다.
            copy = next(v["id"] for v in app.overview()["variants"] if not v["is_base"])
            did = app.r.one("SELECT id FROM dids WHERE identifier=256")["id"]
            state = app.execute_command("detach_did", {"id": did, "variant_id": base}, app.r.revision)
            shares = {(v["id"], i["name"]): i["share"] for v in state["tree"]["variants"] for c in v["classes"] for i in c["instances"]}
            self.assertEqual(shares[(base, "D0 읽기")], "base")
            self.assertEqual(shares[(copy, "D0 읽기")], "local")
            self.assertEqual(shares[(copy, "D1 읽기")], "base_shared")
        finally:
            app.close_project()


class ServiceImportTest(ImportedAbs):
    def test_profile_services_are_imported(self):
        self.assertGreaterEqual(len(self.services), 29)
        for q in ("TesterPresent_Send", "DefaultSession_Start", "HardReset_Reset", "Supply_Voltage_ECU_Read", "VIN_Write", "ECU_Check_Start"):
            self.assertIn(q, self.services)

    def test_telegram_bytes_follow_uds(self):
        self.assertEqual(self.telegrams("Supply_Voltage_ECU_Read")["request"], "22 01 07")
        self.assertTrue(self.telegrams("Supply_Voltage_ECU_Read")["positive"].startswith("62 01 07"))
        self.assertEqual(self.telegrams("DefaultSession_Start")["request"], "10 01")
        self.assertEqual(self.telegrams("DefaultSession_Start")["positive"], "50 01 00 00 00 00")  # P2, P2*
        self.assertEqual(self.telegrams("ECU_Check_Start")["request"], "31 01 01 00 00")
        self.assertEqual(self.telegrams("TesterPresent_Send"), {"request": "3E 00", "positive": "7E 00"})
        self.assertEqual(self.telegrams("HardReset_Reset")["negative NRC 0x22"], "7F 11 22")

    def test_variant_specific_instance(self):
        variants = {v["qualifier"]: v["id"] for v in self.state["variants"]}
        c_sample = {r["qualifier"] for r in self.app.query_objects("service", variants["C_Sample"], page_size=1000)["rows"]}
        self.assertIn("Yaw_Rate_Sensor_Read", c_sample)
        self.assertNotIn("Yaw_Rate_Sensor_Read", self.services)

    def test_states_and_conditions(self):
        states = {(r["qualifier"], r["code"]) for r in self.app.query_objects("state", page_size=100)["rows"]}
        self.assertTrue({("Default", 1), ("Programming", 2), ("Extended", 3)} <= states)  # 0x10 subfunction에서 도출
        text = details_text(self.app.details(self.services["HardReset_Reset"]))
        self.assertIn("SecurityAccess: ✓ Locked, ✓ Unlocked L1", text)   # mayBeExec=(4,5): 세션 그룹은 제한 없음
        self.assertIn("Unlocked L1 → Locked", text)                         # trans=(5,4)

    def test_import_is_structurally_valid(self):
        issues = self.app.validate_project()["issues"]
        # 남는 것은 보존된 원문과 수식 타입 알림(29차 WRN-001: FORMULADT 3개)뿐
        self.assertEqual({i["rule_id"] for i in issues}, {"VAL-012", "INF-001", "WRN-001"})
        by_rule = {i["rule_id"]: (i["severity"], i["blocks_export"]) for i in issues}
        self.assertEqual(by_rule, {"VAL-012": ("warning", True), "INF-001": ("info", False), "WRN-001": ("warning", False)})
        self.assertEqual(sum(i["rule_id"] == "WRN-001" for i in issues), self.app.r.one("SELECT count(*) n FROM data_types WHERE conversion='formula'")["n"])
        self.assertEqual(sum(i["rule_id"] == "VAL-012" for i in issues), self.app.r.one("SELECT count(*) n FROM unsupported_nodes WHERE semantic=1")["n"]
                         + self.app.r.one("SELECT count(*) n FROM objects WHERE supported=0")["n"]
                         + self.app.r.one("SELECT count(*) n FROM structure_members WHERE record_role IN ('sd','ed')")["n"])  # 레코드 데이터의 ODX 근거 없음

    def test_ecu_attributes(self):
        text = details_text(self.app.details(self.state["ecu_id"]))
        self.assertIn("Diagnostic_CAN.ReqCanId: 0xA1", text)


class ProfileRuleTest(ImportedAbs):
    """VAL-011: 고정 프로파일이 증명하지 않은 subfunction·NRC는 경고/출력 차단이다."""

    def issues(self, s):
        from diagstudio.domain import validate
        return [(i["rule_id"], i["object_id"], i["field"], i["severity"], i["blocks_export"]) for i in validate(s)["issues"] if i["rule_id"] == "VAL-011"]

    def service(self, s, q):
        return next(r for r in s.rows("services") if s.get("objects", r["id"])["qualifier"] == q)

    def test_abs_has_no_profile_warnings(self):
        self.assertEqual(self.issues(self.app.r.snapshot()), [])

    def test_unproven_subfunction_is_warning(self):
        s = self.app.r.snapshot()
        reset, present, raw = self.service(s, "HardReset_Reset"), self.service(s, "TesterPresent_Send"), self.service(s, "SeedLevel1_Request")
        reset["subfunction"], present["subfunction"] = 0x40, 0x01
        self.assertEqual(sorted(self.issues(s)), sorted([("VAL-011", reset["id"], "subfunction", "warning", True),
                                                         ("VAL-011", present["id"], "subfunction", "warning", True)]))
        self.assertNotIn(raw["id"], [i[1] for i in self.issues(s)])  # 원문 서비스는 검사하지 않는다

    def test_nrc_outside_template_list(self):
        s = self.app.r.snapshot()
        vin = self.service(s, "VIN_Read")
        s.tables["service_nrcs"].append({"service_id": vin["id"], "code": 0xF0})
        self.assertEqual(self.issues(s), [("VAL-011", vin["id"], "nrcs", "warning", True)])
        s.tables["nrcs"] = []  # 템플릿 목록이 없으면 기본 프로파일 목록(=ABS 템플릿 40개)을 쓴다
        self.assertEqual(len(self.issues(s)), 1)
        s.tables["service_nrcs"][-1]["code"] = 0x78
        self.assertEqual(self.issues(s), [])


class FaultMemoryTest(ImportedAbs):
    """0x19 ReadDTCInformation·0x14 ClearDiagnosticInformation: DTC 값, DTC 상태 바이트, DTC 그룹, PDU 끝까지 반복(schema 7).
    근거: Import/UDS-ExampleEcu.pdx(CANdelaStudio 5.0 ODX 출력), 도움말 100_ODXExport "Data objects for Fault Memory"."""

    def members(self, qualifier, kind):
        s = self.app.r.snapshot()
        tel = next(t for t in s.rows("telegrams") if t["service_id"] == self.services[qualifier] and t["kind"] == kind)
        return s, s.members(tel["structure_id"])

    def odx(self):
        s = without_record_services(self.app.r.snapshot(self.app.locale))
        s.tables["service_conditions"], s.tables["service_transitions"] = [], []
        return s, build_odx(s, [self.base])

    def test_services_import_read_only(self):
        for q in ("FaultMemory_ReadNumber", "FaultMemory_ReadAllIdentified", "FaultMemory_ReadAllSupported", "FaultMemory_Clear",
                  "FaultMemory_ReadEnvironmentData", "FaultMemory_Read_extended_data"):
            self.assertIn(q, self.services)
            self.assertTrue(self.app.details(self.services[q])["read_only"])
        self.assertEqual(self.app.r.all("SELECT reason_code FROM unsupported_nodes WHERE node_path LIKE '%/DIAGINST[1]/%'"),
                         [{"reason_code": "출력 영향 없음: Event 데이터(ODX 출력 대상 아님, 도움말 ODXE-D115)"}])

    def test_read_all_identified_iterates_dtc_and_status(self):
        s, members = self.members("FaultMemory_ReadAllIdentified", "positive")
        self.assertEqual([m["kind"] for m in members], ["literal", "literal", "structure", "iteration"])
        loop = members[-1]
        self.assertEqual((loop["min_items"], s.get("objects", loop["nested_structure_id"])["qualifier"]), (0, "ListOfDTC"))
        item = s.members(loop["nested_structure_id"])
        self.assertEqual([m["kind"] for m in item], ["dtc", "structure"])
        status = s.flatten(item[1]["nested_structure_id"])
        # 첫 멤버 TestFailed = 비트 0, 빈 비트 WarningIndicatorRequested = 비트 7(예약). 전문은 상위 비트부터 쌓는다.
        self.assertEqual([f["kind"] for f in status], ["data"] * 7 + ["reserved"])
        text = details_text(self.app.details(self.services["FaultMemory_ReadAllIdentified"]))
        self.assertIn("반복  ListOfDTC", text)
        self.assertIn("↻     0.0    24b  DTC  [DTC 표]", text)
        self.assertIn("↻     3.0     1b  Test failed", text)
        self.assertIn("↻     3.7     1b  WarningIndicatorRequested  (reserved)", text)  # 빈 비트 이름 보존(schema 8)

    def test_read_number_count_is_byte_field(self):
        s, members = self.members("FaultMemory_ReadNumber", "positive")
        count = s.get("data_types", s.get("data_objects", members[-1]["data_object_id"])["data_type_id"])
        self.assertEqual((s.get("objects", count["id"])["qualifier"], count["encoding"], count["array_length"]), ("DtcCount", "bytes", 2))

    def test_clear_group_of_dtc(self):
        s, members = self.members("FaultMemory_Clear", "request")
        obj = s.get("data_objects", members[-1]["data_object_id"])
        typ = s.get("data_types", obj["data_type_id"])
        self.assertEqual((s.get("objects", typ["id"])["qualifier"], typ["bit_length"], typ["conversion"], obj["default_text"]), ("LocalTable", 24, "text_table", "16777215"))
        self.assertEqual([e["label"] for e in s.rows("conversion_entries") if e["data_type_id"] == typ["id"]], ["All groups"])
        self.assertEqual(self.telegrams("FaultMemory_Clear", {obj["id"]: {"value": "16777215"}})["request"], "14 FF FF FF")

    def test_odx_end_of_pdu_field_and_dtc_dop(self):
        _, root = self.odx()
        dictionary = root.find(".//DIAG-DATA-DICTIONARY-SPEC")
        self.assertEqual([c.tag for c in dictionary], ["DTC-DOPS", "DATA-OBJECT-PROPS", "STRUCTURES", "END-OF-PDU-FIELDS", "UNIT-SPEC", "TABLES"])
        ids = {e.get("ID"): e for e in root.iter() if e.get("ID")}
        service = next(d for d in root.iter("DIAG-SERVICE") if d.findtext("SHORT-NAME") == "FaultMemory_ReadAllIdentified")
        response = ids[service.find("POS-RESPONSE-REFS/POS-RESPONSE-REF").get("ID-REF")]
        last = response.findall("PARAMS/PARAM")[-1]
        field = ids[last.find("DOP-REF").get("ID-REF")]
        self.assertEqual((field.tag, field.findtext("SHORT-NAME"), last.findtext("BYTE-POSITION")), ("END-OF-PDU-FIELD", "ListOfDTC", "3"))
        item = ids[field.find("BASIC-STRUCTURE-REF").get("ID-REF")].findall("PARAMS/PARAM")
        self.assertEqual(ids[item[0].find("DOP-REF").get("ID-REF")].tag, "DTC-DOP")
        reserved = [(p.findtext("BYTE-POSITION"), p.findtext("BIT-POSITION"), p.findtext("BIT-LENGTH")) for p in item if p.get(f"{{{XSI}}}type") == "RESERVED"]
        self.assertEqual(reserved, [("3", "7", "1")])
        failed = next(p for p in item if p.findtext("LONG-NAME") == "Test failed")
        self.assertEqual((failed.findtext("BYTE-POSITION"), failed.findtext("BIT-POSITION")), ("3", "0"))

    def test_odx_dtc_without_table_is_reserved(self):
        s = without_record_services(self.app.r.snapshot(self.app.locale))
        s.tables["service_conditions"], s.tables["service_transitions"], s.tables["variant_dtcs"] = [], [], []
        root = build_odx(s, [self.base])
        self.assertIsNone(root.find(".//DTC-DOPS"))
        item = root.find(".//END-OF-PDU-FIELD")
        ids = {e.get("ID"): e for e in root.iter() if e.get("ID")}
        first = ids[item.find("BASIC-STRUCTURE-REF").get("ID-REF")].find("PARAMS/PARAM")
        self.assertEqual((first.get(f"{{{XSI}}}type"), first.findtext("BIT-LENGTH")), ("RESERVED", "24"))

    def test_iteration_must_end_telegram(self):
        from diagstudio.domain import validate
        s, members = self.members("FaultMemory_ReadAllIdentified", "positive")
        loop = members[-1]
        rows = [m for m in s.rows("structure_members") if m["structure_id"] == loop["structure_id"]]
        loop["ordinal"], rows[2]["ordinal"] = rows[2]["ordinal"], loop["ordinal"]  # 반복을 상태 바이트 앞으로
        s.member_index[loop["structure_id"]].sort(key=lambda r: r["ordinal"])
        issues = [(i["rule_id"], i["message"]) for i in validate(s)["issues"] if i["rule_id"] == "VAL-005"]
        self.assertIn(("VAL-005", "반복은 전문의 마지막 멤버여야 합니다(PDU 끝까지 반복)."), issues)


class FaultRecordTest(ImportedAbs):
    """0x19 04/06 스냅샷·확장 데이터 레코드(schema 8). 정의는 DIAGINST/FAULTMEMORY, 전문의 sr/sd/edr/ed 자리가 가리킨다."""

    def test_fault_memory_definitions(self):
        r = self.app.r
        self.assertEqual([(x["qualifier"], x["number"]) for x in r.all("SELECT o.qualifier,s.number FROM snapshot_records s JOIN objects o ON o.id=s.id ORDER BY number")],
                         [("First_Occurrence", 16), ("Last_Occurrence", 32)])
        self.assertEqual([x["identifier"] for x in r.all("SELECT d.identifier FROM snapshot_default_dids s JOIN dids d ON d.id=s.did_id ORDER BY ordinal")], [0x010A, 0x010D])
        per_dtc = r.all("SELECT t.code,count(*) n FROM snapshot_dtc_dids s JOIN dtcs t ON t.id=s.dtc_id GROUP BY t.code ORDER BY t.code")
        self.assertEqual(per_dtc, [{"code": 0x402011, "n": 3}, {"code": 0x402012, "n": 3}])
        ext = r.all("SELECT o.qualifier,e.number,e.structure_id FROM extended_records e JOIN objects o ON o.id=e.id ORDER BY number")
        self.assertEqual([(x["qualifier"], x["number"]) for x in ext], [("OBD_Counters", 0), ("Occurrence_Counter", 10), ("DTC_Aging_Counter", 11), ("Uncompleted_Test_Counters", 12)])
        self.assertEqual(r.one("SELECT count(*) n FROM structure_members WHERE structure_id=?", (ext[0]["structure_id"],))["n"], 2)
        text = details_text(self.app.details(self.app.r.one("SELECT instance_id FROM snapshot_records")["instance_id"]))
        self.assertIn("스냅샷 레코드\n0x10 First_Occurrence, 0x20 Last_Occurrence", text)
        self.assertIn("스냅샷 DID(기본)\n0x010A Wheel_Speed, 0x010D Brake_Pedal", text)
        self.assertIn("스냅샷 DID(DTC별 대체)\n0x402011: 0x010A Wheel_Speed, 0x010D Brake_Pedal, 0x0107 Supply_Voltage_ECU", text)
        self.assertIn("확장 데이터 레코드\n0x00 OBD_Counters (2바이트)\n0x0A Occurrence_Counter (1바이트)", text)

    def test_snapshot_service_layout(self):
        s = self.app.r.snapshot()
        tel = {t["kind"]: t["structure_id"] for t in s.rows("telegrams") if t["service_id"] == self.services["FaultMemory_ReadEnvironmentData"]}
        self.assertEqual([(m["kind"], m["record_role"]) for m in s.members(tel["request"])][2:], [("dtc", None), ("fault_record", "sr")])
        loop = s.members(tel["positive"])[-1]
        self.assertEqual([(m["kind"], m["record_role"]) for m in s.members(loop["nested_structure_id"])], [("fault_record", "sr"), ("data", None), ("fault_record", "sd")])
        issues = [i for i in self.app.validate_project()["issues"] if i["object_id"] == self.services["FaultMemory_ReadEnvironmentData"]]
        self.assertEqual([(i["rule_id"], i["blocks_export"]) for i in issues], [("VAL-012", True)])

    def test_record_services_block_odx(self):
        s = self.app.r.snapshot(self.app.locale)
        s.tables["service_conditions"], s.tables["service_transitions"] = [], []
        with self.assertRaises(DomainError) as e: build_odx(s, [self.base])
        self.assertIn("스냅샷·확장 데이터 레코드", str(e.exception))


class StateChartExportTest(ImportedAbs):
    """상태 모델 ODX 출력. 근거: 도움말 180_ODXExport(STATE-CHART·STATE·STATE-TRANSITION, START-STATE)."""

    def snapshot(self):
        s = without_record_services(self.app.r.snapshot(self.app.locale))
        restricted = {c["service_id"] for c in s.rows("service_conditions") if not c["allowed"]}
        s.tables["service_conditions"] = [c for c in s.rows("service_conditions") if c["service_id"] not in restricted]
        s.tables["service_transitions"] = [x for x in s.rows("service_transitions") if x["service_id"] not in restricted]
        return s, restricted

    def test_restricted_service_blocks_export(self):
        with self.assertRaises(DomainError) as e: build_odx(self.app.r.snapshot(self.app.locale), [self.base])
        self.assertIn("실행 가능 상태가 제한된 서비스", str(e.exception))

    def test_transitions_export_as_state_chart(self):
        s, _ = self.snapshot()
        self.assertTrue(s.rows("service_transitions"))
        root = build_odx(s, [self.base])
        ids = {e.get("ID"): e for e in root.iter() if e.get("ID")}
        chart = next(root.iter("STATE-CHART"))  # 세션 그룹이 먼저
        self.assertEqual([c.tag for c in chart], ["SHORT-NAME", "LONG-NAME", "SEMANTIC", "STATE-TRANSITIONS", "START-STATE-SNREF", "STATES"])
        self.assertEqual(chart.findtext("SEMANTIC"), "SESSION")
        states = [x.findtext("SHORT-NAME") for x in chart.iter("STATE")]
        self.assertEqual(chart.find("START-STATE-SNREF").get("SHORT-NAME"), states[0])
        service = next(d for d in root.iter("DIAG-SERVICE") if d.findtext("SHORT-NAME") == "ExtendedDiagnosticSession_Start")
        refs = [ids[r.get("ID-REF")] for r in service.iter("STATE-TRANSITION-REF")]
        self.assertTrue(refs)
        in_session = [r for r in refs if r in list(chart.iter("STATE-TRANSITION"))]
        self.assertEqual({r.find("TARGET-SNREF").get("SHORT-NAME") for r in in_session}, {"Extended"})
        self.assertEqual(len(in_session), 3)  # Default·Programming·Extended → Extended
        self.assertEqual(len(refs), 4)         # + 보안 그룹 UnlockedL1 → Locked
        self.assertEqual([c.tag for c in service][:3], ["SHORT-NAME", "LONG-NAME", "STATE-TRANSITION-REFS"])
        names = [x.findtext("SHORT-NAME") for x in chart.iter("STATE-TRANSITION")]
        self.assertEqual(len(names), len(set(names)))  # 같은 전이는 한 번만


class RawServiceTest(ImportedAbs):
    """고정 프로파일 밖 서비스(0x27·0x2F·0x85, 비표준 0x22)는 원문 배치로 가져와 읽기 전용으로 둔다."""

    def test_raw_services_keep_exact_bytes(self):
        self.assertEqual(self.telegrams("KeyLevel1_Send")["request"], "27 02 00 00 00 00")
        self.assertEqual(self.telegrams("KeyLevel1_Send")["positive"], "67 02")
        self.assertTrue(self.telegrams("Switches_Set")["request"].startswith("2F 01 10 03"))
        self.assertEqual(self.telegrams("DTC_Setting_Mode_Off_Send")["request"], "85 02")

    def test_raw_services_are_read_only_but_keep_state_transitions(self):
        oid = self.services["KeyLevel1_Send"]
        self.assertIn("Locked → Unlocked L1", details_text(self.app.details(oid)))
        with self.assertRaises(DomainError):
            self.app.execute_command("update_common", {"id": oid, "name": "x"}, self.app.r.revision)


class DtcImportTest(ImportedAbs):
    def test_dtcs_and_record_items(self):
        rows = {r["code"]: r for r in self.app.query_objects("dtc", self.base, page_size=1000)["rows"]}
        self.assertEqual(len(rows), 31)
        d = self.app.details(rows[0x402011]["id"])
        self.assertEqual(d["name"], "ABS Pump Motor Control - Circuit short to ground")
        attrs = {a["key"]: a["value_text"] for a in d["attributes"]}
        self.assertEqual(attrs["cdd.CorrectiveAction"], "No known corrective action")
        self.assertTrue(attrs["cdd.SetCondition"].startswith("Value < 0,2V"))
        self.assertEqual(len(d["variants"]), 4)


class RangeTest(ImportedAbs):
    def type_row(self, q):
        s = self.app.r.snapshot()
        t = next(x for x in s.rows("data_types") if s.get("objects", x["id"])["qualifier"] == q)
        return t, [e for e in s.rows("raw_exclusions") if e["data_type_id"] == t["id"]]

    def test_range_and_exclusion_conversion(self):
        t, ex = self.type_row("Lambda_rangeability_1_Byte")
        self.assertEqual(convert(t, 0, exclusions=ex)[1], "below rangeability")
        self.assertEqual(convert(t, 120, exclusions=ex)[1], "620")
        t, ex = self.type_row("Time_2_Byte")
        with self.assertRaises(DomainError): convert(t, 800, exclusions=ex)

    def test_type_edit_keeps_range(self):
        oid = next(r["id"] for r in self.app.query_objects("data_type", page_size=1000)["rows"] if r["qualifier"] == "Time_2_Byte")
        d = self.app.details(oid)
        payload = {k: d[k] for k in ("id", "encoding", "bit_length", "byte_order", "conversion", "factor_text", "offset_text", "unit")}
        payload.update(name="Time (2 Byte) edited", qualifier=d["qualifier"])
        self.app.execute_command("update_type", payload, self.app.r.revision)
        self.assertEqual(self.app.details(oid)["raw_upper"], "730")


class OdxConstraintTest(ImportedAbs):
    def build(self):
        s = without_record_services(self.app.r.snapshot(self.app.locale))
        s.tables["service_conditions"], s.tables["service_transitions"] = [], []
        return s

    def dop(self, root, name):
        return next(d for d in root.iter("DATA-OBJECT-PROP") if d.findtext("SHORT-NAME") == name)

    def constraints(self, dop):
        return [(c.get("VALIDITY"), c.find("SHORT-LABEL") is not None, c.findtext("LOWER-LIMIT"), c.findtext("UPPER-LIMIT")) for c in dop.iter("SCALE-CONSTR")]

    def exclusion_as(self, kind):
        s = self.build()
        percent = next(t["id"] for t in s.rows("data_types") if s.get("objects", t["id"])["qualifier"] == "Percent_1_Byte")  # 0x0100 Braking에서 사용
        s.tables["raw_exclusions"] = [{**e, "kind": kind} if e["data_type_id"] == percent else e for e in s.tables["raw_exclusions"] if e["kind"] == "undef"]
        return s

    def test_invalid_signal_maps_to_not_valid(self):
        # 근거: 도움말 110_ODXExport Table 40 invalidSignal → notValid
        root = build_odx(self.exclusion_as("invalidSignal"), [self.base])
        self.assertEqual(self.constraints(self.dop(root, "Percent_1_Byte")), [("NOT-VALID", True, "101", "255")])

    def test_unknown_exclusion_kind_blocks_export(self):
        with self.assertRaises(DomainError): build_odx(self.exclusion_as("somethingElse"), [self.base])

    def test_no_signal_maps_to_not_available(self):
        # 근거: Import/UDS-ExampleEcu.pdx Frequency_Counter EXCL noSignal → VALIDITY=NOT-AVAILABLE + SHORT-LABEL
        root = build_odx(self.exclusion_as("noSignal"), [self.base])
        self.assertEqual(self.constraints(self.dop(root, "Percent_1_Byte")), [("NOT-AVAILABLE", True, "101", "255")])

    def odx(self):
        s = self.build()
        s.tables["raw_exclusions"] = [e for e in s.tables["raw_exclusions"] if e["kind"] == "undef"]
        return s, build_odx(s, [self.base])

    def test_service_semantic_follows_class_type(self):
        # 근거: 도움말 60_ODXExport 표 9(클래스 유형 → SEMANTIC). ABS 예제의 DCLTMPL cls 코드로 유형을 정한다.
        from collections import Counter
        _, root = self.odx()
        services = {d.findtext("SHORT-NAME"): d.get("SEMANTIC") for d in root.iter("DIAG-SERVICE")}
        self.assertEqual(services["VIN_Read"], "IDENTIFICATION")
        self.assertEqual(services["DefaultSession_Start"], "SESSION")
        self.assertEqual(services["TesterPresent_Send"], "TESTERPRESENT")
        counts = Counter(services.values())
        self.assertEqual((counts["IDENTIFICATION"], counts["ACTUAL-DATA"], counts["CONTROL"], counts["COMMUNICATION"]), (14, 9, 5, 4))
        self.assertNotIn(None, counts)

    def test_service_semantic_attribute_overrides_class_type(self):
        # 근거: 도움말 80_ODXExport "SEMANTIC ... specify the SEMANTIC of the ODX DIAG-SERVICE directly"
        s, _ = self.odx()
        vin = next(o["id"] for o in s.rows("objects") if o["kind"] == "service" and o["qualifier"] == "VIN_Read")
        s.tables["object_attributes"] = s.rows("object_attributes") + [{"object_id": vin, "key": "cdd.semantic", "value_type": "text", "value_text": "FAULTREAD"}]
        services = {d.findtext("SHORT-NAME"): d.get("SEMANTIC") for d in build_odx(s, [self.base]).iter("DIAG-SERVICE")}
        self.assertEqual((services["VIN_Read"], services["VIN_Write"]), ("FAULTREAD", "IDENTIFICATION"))

    def test_semantic_without_class_type_falls_back_to_sid(self):
        s, _ = self.odx()
        s.tables["object_attributes"] = [a for a in s.rows("object_attributes") if a["key"] != "cdd.class_type"]
        services = {d.findtext("SHORT-NAME"): d.get("SEMANTIC") for d in build_odx(s, [self.base]).iter("DIAG-SERVICE")}
        self.assertEqual(services["VIN_Read"], "READ-DATA")
        self.assertEqual(services["DefaultSession_Start"], "SESSION")

    def test_fraction_coefficient_exports_denominator(self):
        s = self.build()
        s.tables["raw_exclusions"] = [e for e in s.tables["raw_exclusions"] if e["kind"] == "undef"]
        position = next(t for t in s.rows("data_types") if s.get("objects", t["id"])["qualifier"] == "Position_1_Byte")
        voltage = next(t for t in s.rows("data_types") if s.get("objects", t["id"])["qualifier"] == "Voltage_1_Byte")
        # 사용 중인 DID 멤버의 타입을 분수 계수 타입으로 바꿔 DOP를 출력하게 한다.
        obj = next(o for o in s.rows("data_objects") if o["data_type_id"] == voltage["id"])
        obj["data_type_id"] = position["id"]
        root = build_odx(s, [self.base])
        coeffs = self.dop(root, "Position_1_Byte").find(".//COMPU-RATIONAL-COEFFS")
        self.assertEqual([v.text for v in coeffs.find("COMPU-NUMERATOR")], ["0", "359"])
        self.assertEqual([v.text for v in coeffs.find("COMPU-DENOMINATOR")], ["255"])

    def test_constraints_match_vector_export(self):
        s = self.build()
        s.tables["raw_exclusions"] = [e for e in s.tables["raw_exclusions"] if e["kind"] == "undef"]
        root = build_odx(s, [self.base])
        self.assertEqual(self.constraints(self.dop(root, "Time_2_Byte")), [("NOT-DEFINED", False, "731", "65535")])
        self.assertEqual(self.constraints(self.dop(root, "Percent_1_Byte")), [("NOT-DEFINED", True, "101", "255")])
        self.assertEqual(self.constraints(self.dop(root, "Wheel_type")), [("NOT-DEFINED", False, "8", "255")])
        self.assertEqual(self.constraints(self.dop(root, "Voltage_1_Byte")), [])
        dop = next(root.iter("DTC-DOP"))
        self.assertEqual([c.tag for c in dop], ["SHORT-NAME", "DIAG-CODED-TYPE", "PHYSICAL-TYPE", "COMPU-METHOD", "DTCS"])
        dtc = next(d for d in root.iter("DTC") if d.findtext("TROUBLE-CODE") == "4202513")
        self.assertEqual(dtc.findtext("DISPLAY-TROUBLE-CODE"), "0x00402011")
        self.assertIn("SetCondition", [c.findtext("SHORT-NAME") for c in dtc.iter("SDG-CAPTION")])


class BitfieldTest(ImportedAbs):
    """CDD STRUCT 비트필드. 근거: Repository/ODXEnvelope/010A_Wheel_Speed.odx-d(Vector 출력)의 BYTE-POSITION 8, BIT-POSITION 0·2·4·6."""
    build = OdxConstraintTest.build

    def test_did_010a_is_imported_with_service(self):
        self.assertIn("Wheel_Speed_Read", self.services)
        did = next(r for r in self.app.query_objects("did", self.base, page_size=100)["rows"] if r["identifier"] == 0x010A)
        text = details_text(self.app.details(did["id"]))
        self.assertIn("8.0     2b  Direction Of Rotation FR", text)
        self.assertIn("8.6     2b  Direction Of Rotation RL", text)

    def test_odx_bit_positions_match_vector(self):
        s = self.build()
        s.tables["raw_exclusions"] = [e for e in s.tables["raw_exclusions"] if e["kind"] == "undef"]
        root = build_odx(s, [self.base])
        did = next(r for r in self.app.query_objects("did", self.base, page_size=100)["rows"] if r["identifier"] == 0x010A)
        st = next(x for x in root.iter("STRUCTURE") if x.findtext("SHORT-NAME") == "Wheel_Speed")
        positions = {p.findtext("LONG-NAME"): (p.findtext("BYTE-POSITION"), p.findtext("BIT-POSITION")) for p in st.iter("PARAM")}
        self.assertEqual(positions["Direction Of Rotation FR"], ("8", "0"))
        self.assertEqual(positions["Direction Of Rotation FL"], ("8", "2"))
        self.assertEqual(positions["Direction Of Rotation RR"], ("8", "4"))
        self.assertEqual(positions["Direction Of Rotation RL"], ("8", "6"))

    def test_telegram_packs_first_member_in_lowest_bits(self):
        fields = self.app.preview_fields(self.services["Wheel_Speed_Read"])
        values = {f["id"]: {"value": "0"} for f in fields}
        names = {f["id"]: f.get("name") or f.get("label") for f in fields}
        fr = next(k for k, v in names.items() if v == "Direction Of Rotation FR")
        rl = next(k for k, v in names.items() if v == "Direction Of Rotation RL")
        values[fr] = {"value": "1"}; values[rl] = {"value": "2"}
        hexes = {t["kind"]: t["hex"] for t in self.app.build_telegram_preview(self.services["Wheel_Speed_Read"], values)["telegrams"]}
        last = hexes["positive"].replace(" ", "")[-2:]
        self.assertEqual(int(last, 16), (2 << 6) | 1)  # RL=2 at bits 6-7, FR=1 at bits 0-1

    def test_communication_control_commtype_bits(self):
        """CommType: Normal(비트 0)·Network(비트 1)·Diagnostic(비트 2) 1비트 + reserved 5비트, 첫 멤버가 최하위 비트."""
        oid = self.services["DisableRxAndEnableTx_Control"]
        fields = self.app.preview_fields(oid)
        by_name = {(f.get("name") or f.get("label")): f["id"] for f in fields}
        values = {f["id"]: {"value": "0"} for f in fields}
        values[by_name["NetworkCommunicationCommunicationMessages"]] = {"value": "1"}
        request = next(t["hex"] for t in self.app.build_telegram_preview(oid, values)["telegrams"] if t["kind"] == "request")
        self.assertEqual(request.replace(" ", "")[-2:].upper(), "02")

    def test_editing_did_keeps_bitfield_and_removal_drops_it(self):
        did = next(r for r in self.app.query_objects("did", self.base, page_size=100)["rows"] if r["identifier"] == 0x010A)
        before = self.app.r.one("SELECT count(*) n FROM structures WHERE kind='bitfield'")["n"]
        members = self.app.details(did["id"])["members"]
        self.assertEqual(members[-1]["structure_kind"], "bitfield")
        self.app.r.db.execute("SAVEPOINT keep")
        try:
            self.app._members(self.app.r.one("SELECT structure_id FROM dids WHERE id=?", (did["id"],))["structure_id"], members[:-1] + [members[-1]])
            self.assertEqual(self.app.r.one("SELECT count(*) n FROM structures WHERE kind='bitfield'")["n"], before)
        finally:
            self.app.r.db.execute("ROLLBACK TO keep"); self.app.r.db.execute("RELEASE keep")


class LegacyApplication(Application):
    """구 스키마 파일을 만들 때 쓴다. 스키마 3 이하에는 변형별 클래스 배치(variant_classes)가 없고, 이후 추가된 열은 기록하지 않는다."""
    def _place_instance(self, variant, class_id, instance, ordinal=None):
        if "variant_classes" in self.r.tables: return super()._place_instance(variant, class_id, instance, ordinal)
        self.r.insert("variant_instances", variant_id=variant, instance_id=instance)

    def tree(self):
        return {}

    def new_project(self, *args, **kwargs):
        result = super().new_project(*args, **kwargs)
        # 이후 스키마에서 추가된 열은 구 스키마 파일에 없으므로 빼고 기록한다.
        insert = self.r.insert
        self.r.insert = lambda table, **values: insert(table, **{k: v for k, v in values.items() if k in {c["name"] for c in self.r.columns[table]}})
        return result


class MigrationTest(unittest.TestCase):
    def test_v1_file_opens_and_saves_as_current(self):
        out = Path(tempfile.mkdtemp(prefix="diagauthor-mig-"))
        schema, version = persistence.SCHEMA_FILE, migrations.CURRENT_VERSION
        try:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = ROOT / "test" / "fixtures" / "schema_v1.sql", 1
            old = LegacyApplication(); old.new_project("Old ECU", "OldECU"); old.save_project(str(out / "old.diagproj")); old.close_project()
        finally:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = schema, version
        try:
            with closing(sqlite3.connect(out / "old.diagproj")) as db: self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            app = Application()
            state = app.open_project(str(out / "old.diagproj"))
            self.assertEqual(state["migrated"], list(range(2, migrations.CURRENT_VERSION + 1)))
            self.assertEqual(app.r.one("SELECT schema_version FROM project_meta")["schema_version"], migrations.CURRENT_VERSION)
            with closing(sqlite3.connect(out / "old.diagproj")) as db: self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)  # 저장 전 원본 유지
            app.save_project(str(out / "old.diagproj")); app.close_project()
            with closing(sqlite3.connect(out / "old.diagproj")) as db: self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], migrations.CURRENT_VERSION)
            self.assertTrue((out / "old.diagproj.bak").is_file())
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_v2_file_keeps_services_and_exclusions(self):
        out = Path(tempfile.mkdtemp(prefix="diagauthor-mig-"))
        schema, version = persistence.SCHEMA_FILE, migrations.CURRENT_VERSION
        try:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = ROOT / "test" / "fixtures" / "schema_v2.sql", 2
            old = LegacyApplication(); old.new_project("V2 ECU", "V2ECU")
            state = old.execute_command("create_type", {"name": "Percent", "qualifier": "Percent", "encoding": "unsigned", "bit_length": 8, "conversion": "linear",
                                                        "factor_text": "1", "offset_text": "0", "raw_upper": "100",
                                                        "exclusions": [{"lower_raw": "255", "upper_raw": "255", "kind": "noSignal", "locale": "en-US", "label": "n/a"}]}, old.r.revision)
            type_id = state["selected_id"]
            old.execute_command("create_did", {"variant_id": state["variants"][0]["id"], "name": "Level", "qualifier": "Level", "identifier": "0x0200",
                                               "members": [{"kind": "data", "data_type_id": type_id, "name": "Level", "qualifier": "Level"}]}, old.r.revision)
            old.save_project(str(out / "v2.diagproj")); old.close_project()
        finally:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = schema, version
        try:
            app = Application()
            state = app.open_project(str(out / "v2.diagproj"))
            self.assertEqual(state["migrated"], list(range(3, migrations.CURRENT_VERSION + 1)))
            self.assertEqual(state["counts"]["service"], 1)
            self.assertEqual(app.r.one("SELECT count(*) n FROM variant_classes")["n"], 1)
            self.assertEqual(app.r.one("SELECT profile FROM services")["profile"], "uds_fixed")
            self.assertEqual(app.r.one("SELECT kind,label FROM raw_exclusions"), {"kind": "noSignal", "label": "n/a"})
            self.assertIsNone(app.r.db.execute("PRAGMA foreign_key_check").fetchone())
            self.assertEqual({i["rule_id"] for i in app.validate_project()["issues"]}, set())
            app.close_project()
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_v3_file_gets_class_placement(self):
        out = Path(tempfile.mkdtemp(prefix="diagauthor-mig-"))
        schema, version = persistence.SCHEMA_FILE, migrations.CURRENT_VERSION
        try:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = ROOT / "test" / "fixtures" / "schema_v3.sql", 3
            old = LegacyApplication(); state = old.new_project("V3 ECU", "V3ECU")
            variant = state["variants"][0]["id"]
            for n, q in enumerate(("First", "Second")):
                state = old.execute_command("create_type", {"name": q, "qualifier": q + "_T", "encoding": "unsigned", "bit_length": 8, "conversion": "identity"}, old.r.revision)
                old.execute_command("create_did", {"variant_id": variant, "name": q, "qualifier": q, "identifier": f"0x02{n:02X}",
                                                   "members": [{"kind": "data", "data_type_id": state["selected_id"], "name": q, "qualifier": q}]}, old.r.revision)
            old.save_project(str(out / "v3.diagproj")); old.close_project()
        finally:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = schema, version
        try:
            app = Application()
            state = app.open_project(str(out / "v3.diagproj"))
            self.assertEqual(state["migrated"], list(range(4, migrations.CURRENT_VERSION + 1)))
            self.assertEqual(app.r.all("SELECT ordinal,singleton FROM variant_classes JOIN diagnostic_classes c ON c.id=class_id"), [{"ordinal": 0, "singleton": 0}])
            self.assertEqual(sorted(r["ordinal"] for r in app.r.all("SELECT ordinal FROM variant_instances")), [0, 1])
            self.assertIsNone(app.r.db.execute("PRAGMA foreign_key_check").fetchone())
            self.assertEqual({i["rule_id"] for i in app.validate_project()["issues"]}, set())
            app.close_project()
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_v4_file_gets_instance_owner(self):
        out = Path(tempfile.mkdtemp(prefix="diagauthor-mig-"))
        schema, version = persistence.SCHEMA_FILE, migrations.CURRENT_VERSION
        try:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = ROOT / "test" / "fixtures" / "schema_v4.sql", 4
            old = LegacyApplication(); state = old.new_project("V4 ECU", "V4ECU")
            base = state["variants"][0]["id"]
            state = old.execute_command("create_type", {"name": "T", "qualifier": "T", "encoding": "unsigned", "bit_length": 8, "conversion": "identity"}, old.r.revision)
            old.execute_command("create_did", {"variant_id": base, "name": "D", "qualifier": "D", "identifier": "0x0300",
                                               "members": [{"kind": "data", "data_type_id": state["selected_id"], "name": "V", "qualifier": "V"}]}, old.r.revision)
            old.save_project(str(out / "v4.diagproj")); old.close_project()
            # 확장 변형 하나가 기본 변형의 인스턴스를 같이 쓰는 v4 파일(현재 코드의 변형 복사는 v5 테이블을 쓰므로 SQL로 만든다).
            with closing(sqlite3.connect(out / "v4.diagproj")) as db:
                ext = str(uuid.uuid4()); ecu = db.execute("SELECT id FROM ecu").fetchone()[0]
                db.execute("INSERT INTO objects(id,kind,qualifier,qualifier_key) VALUES(?,?,?,?)", (ext, "variant", "Ext", "ext"))
                db.execute("INSERT INTO variants(id,ecu_id,is_base,ordinal) VALUES(?,?,0,1)", (ext, ecu))
                for table in ("variant_dids", "variant_classes", "variant_instances"):
                    cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})") if r[1] != "variant_id"]
                    db.execute(f"INSERT INTO {table}(variant_id,{','.join(cols)}) SELECT ?,{','.join(cols)} FROM {table} WHERE variant_id=?", (ext, base))
                db.commit()
        finally:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = schema, version
        try:
            app = Application()
            state = app.open_project(str(out / "v4.diagproj"))
            self.assertEqual(state["migrated"], list(range(5, migrations.CURRENT_VERSION + 1)))
            self.assertEqual({r["owner_variant_id"] for r in app.r.all("SELECT owner_variant_id FROM diagnostic_instances")}, {base})
            share = {i["share"] for v in state["tree"]["variants"] for c in v["classes"] for i in c["instances"]}
            self.assertEqual(share, {"base_shared"})
            self.assertIsNone(app.r.db.execute("PRAGMA foreign_key_check").fetchone())
            self.assertEqual({i["rule_id"] for i in app.validate_project()["issues"]}, set())
            app.close_project()
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_v5_file_gets_command_receipts(self):
        out = Path(tempfile.mkdtemp(prefix="diagauthor-mig-"))
        schema, version = persistence.SCHEMA_FILE, migrations.CURRENT_VERSION
        try:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = ROOT / "test" / "fixtures" / "schema_v5.sql", 5
            old = LegacyApplication(); old.new_project("V5 ECU", "V5ECU")
            old.save_project(str(out / "v5.diagproj")); old.close_project()
        finally:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = schema, version
        try:
            app = Application()
            state = app.open_project(str(out / "v5.diagproj"))
            self.assertEqual(state["migrated"], list(range(6, migrations.CURRENT_VERSION + 1)))
            command = str(uuid.uuid4())
            first = app.execute_command("create_type", {"name": "T", "qualifier": "T", "encoding": "unsigned", "bit_length": 8, "conversion": "identity"}, app.r.revision, command)
            again = app.execute_command("create_type", {"name": "T", "qualifier": "T", "encoding": "unsigned", "bit_length": 8, "conversion": "identity"}, first["revision"], command)
            self.assertTrue(again.get("already_applied"))
            self.assertEqual(again["revision"], first["revision"])
            app.close_project()
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_v6_file_keeps_structure_members(self):
        out = Path(tempfile.mkdtemp(prefix="diagauthor-mig-"))
        schema, version = persistence.SCHEMA_FILE, migrations.CURRENT_VERSION
        try:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = ROOT / "test" / "fixtures" / "schema_v6.sql", 6
            old = LegacyApplication(); state = old.new_project("V6 ECU", "V6ECU")
            state = old.execute_command("create_type", {"name": "T", "qualifier": "T", "encoding": "unsigned", "bit_length": 8, "conversion": "identity"}, old.r.revision)
            old.execute_command("create_did", {"variant_id": state["variants"][0]["id"], "name": "D", "qualifier": "D", "identifier": "0x0400",
                                               "members": [{"kind": "data", "data_type_id": state["selected_id"], "name": "V", "qualifier": "V"},
                                                           {"kind": "reserved", "reserved_bits": 8}]}, old.r.revision)
            before = old.r.all("SELECT structure_id,ordinal,kind,reserved_bits FROM structure_members ORDER BY structure_id,ordinal")
            old.save_project(str(out / "v6.diagproj")); old.close_project()
        finally:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = schema, version
        try:
            app = Application()
            state = app.open_project(str(out / "v6.diagproj"))
            self.assertEqual(state["migrated"], [7, 8, 9, 10, 11, 12, 13])
            self.assertEqual(app.r.all("SELECT structure_id,ordinal,kind,reserved_bits FROM structure_members ORDER BY structure_id,ordinal"), before)
            self.assertIn("min_items", {c["name"] for c in app.r.columns["structure_members"]})
            self.assertIsNone(app.r.db.execute("PRAGMA foreign_key_check").fetchone())
            self.assertEqual({i["rule_id"] for i in app.validate_project()["issues"]}, set())
            app.close_project()
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_v7_file_gets_fault_record_tables(self):
        out = Path(tempfile.mkdtemp(prefix="diagauthor-mig-"))
        schema, version = persistence.SCHEMA_FILE, migrations.CURRENT_VERSION
        try:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = ROOT / "test" / "fixtures" / "schema_v7.sql", 7
            old = LegacyApplication(); state = old.new_project("V7 ECU", "V7ECU")
            state = old.execute_command("create_type", {"name": "T", "qualifier": "T", "encoding": "unsigned", "bit_length": 8, "conversion": "identity"}, old.r.revision)
            old.execute_command("create_did", {"variant_id": state["variants"][0]["id"], "name": "D", "qualifier": "D", "identifier": "0x0500",
                                               "members": [{"kind": "data", "data_type_id": state["selected_id"], "name": "V", "qualifier": "V"},
                                                           {"kind": "reserved", "reserved_bits": 8}]}, old.r.revision)
            before = old.r.all("SELECT structure_id,ordinal,kind,reserved_bits FROM structure_members ORDER BY structure_id,ordinal")
            old.save_project(str(out / "v7.diagproj")); old.close_project()
        finally:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = schema, version
        try:
            app = Application()
            state = app.open_project(str(out / "v7.diagproj"))
            self.assertEqual(state["migrated"], [8, 9, 10, 11, 12, 13])
            self.assertEqual(app.r.all("SELECT structure_id,ordinal,kind,reserved_bits FROM structure_members ORDER BY structure_id,ordinal"), before)
            self.assertTrue({"snapshot_records", "snapshot_default_dids", "snapshot_dtc_dids", "extended_records"} <= set(app.r.tables))
            self.assertIsNone(app.r.db.execute("PRAGMA foreign_key_check").fetchone())
            self.assertEqual({i["rule_id"] for i in app.validate_project()["issues"]}, set())
            app.close_project()
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_v8_file_gets_variable_bytes_column(self):
        out = Path(tempfile.mkdtemp(prefix="diagauthor-mig-"))
        schema, version = persistence.SCHEMA_FILE, migrations.CURRENT_VERSION
        try:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = ROOT / "test" / "fixtures" / "schema_v8.sql", 8
            old = LegacyApplication(); old.new_project("V8 ECU", "V8ECU")
            old.execute_command("create_type", {"name": "B", "qualifier": "B", "encoding": "bytes", "array_length": 4, "conversion": "identity"}, old.r.revision)
            before = old.r.all("SELECT id,encoding,array_length FROM data_types ORDER BY id")
            old.save_project(str(out / "v8.diagproj")); old.close_project()
        finally:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = schema, version
        try:
            app = Application()
            state = app.open_project(str(out / "v8.diagproj"))
            self.assertEqual(state["migrated"], [9, 10, 11, 12, 13])
            self.assertEqual(app.r.all("SELECT id,encoding,array_length FROM data_types ORDER BY id"), before)
            self.assertTrue(all(r["min_length"] is None for r in app.r.all("SELECT min_length FROM data_types")))
            self.assertIsNone(app.r.db.execute("PRAGMA foreign_key_check").fetchone())
            # 새 스키마: 가변 바이트 필드(배열 길이 비움)를 만들 수 있다.
            app.execute_command("create_type", {"name": "V", "qualifier": "V", "encoding": "bytes", "array_length": "", "min_length": 1, "conversion": "identity"}, app.r.revision)
            self.assertEqual(app.r.one("SELECT array_length,min_length FROM data_types WHERE encoding='bytes' AND array_length IS NULL"), {"array_length": None, "min_length": 1})
            self.assertEqual({i["rule_id"] for i in app.validate_project()["issues"]}, set())
            app.close_project()
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_v10_file_gets_pattern_and_global_nrc_tables(self):
        """schema 11: 변형 식별 패턴·미지원 서비스 NRC 표. 이전 파일은 빈 표로 올라가고, 새 프로젝트와 같은 스키마가 된다."""
        out = Path(tempfile.mkdtemp(prefix="diagauthor-mig-"))
        schema, version = persistence.SCHEMA_FILE, migrations.CURRENT_VERSION
        try:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = ROOT / "test" / "fixtures" / "schema_v10.sql", 10
            old = LegacyApplication(); old.new_project("V10 ECU", "V10ECU")
            old.save_project(str(out / "v10.diagproj")); old.close_project()
        finally:
            persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = schema, version
        try:
            app = Application()
            state = app.open_project(str(out / "v10.diagproj"))
            self.assertEqual(state["migrated"], [11, 12, 13])
            self.assertEqual(app.r.all("SELECT * FROM variant_patterns"), [])
            self.assertEqual(app.r.all("SELECT * FROM unsupported_service_nrcs"), [])
            migrated = {(r["type"], r["name"]): " ".join(r["sql"].split()) for r in app.r.all("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")}
            app.close_project()
            fresh = Application(); fresh.new_project("New ECU", "NewECU")
            expected = {(r["type"], r["name"]): " ".join(r["sql"].split()) for r in fresh.r.all("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")}
            fresh.close_project()
            for name in ("variant_patterns", "unsupported_service_nrcs", "ix_variant_patterns_service"):
                entry = next(k for k in expected if k[1] == name)
                self.assertEqual(migrated.get(entry), expected[entry])
        finally:
            shutil.rmtree(out, ignore_errors=True)

    def test_newer_file_is_rejected(self):
        out = Path(tempfile.mkdtemp(prefix="diagauthor-mig-"))
        try:
            app = Application(); app.new_project("New ECU", "NewECU"); app.save_project(str(out / "new.diagproj")); app.close_project()
            with closing(sqlite3.connect(out / "new.diagproj")) as db: db.execute("PRAGMA user_version=99")
            with self.assertRaises(DomainError): Application().open_project(str(out / "new.diagproj"))
        finally:
            shutil.rmtree(out, ignore_errors=True)


class TemplateArtifactQueryTest(unittest.TestCase):
    """NRC·데이터 타입·프로토콜 서비스 목록의 사용 수·정렬·검색. 예제 파일 없이 만든 데이터로 확인한다."""

    def setUp(self):
        self.app = Application()
        self.state = self.app.new_project("Query ECU", "QueryECU")
        self.base = next(v["id"] for v in self.state["variants"] if v["is_base"])

    def tearDown(self):
        self.app.close_project()

    def nrc(self, code, qualifier):
        oid = self.app._object("nrc", qualifier, qualifier)
        self.app.r.update("objects", {"id": oid}, {"read_only": 1})
        self.app.r.insert("nrcs", id=oid, code=code)
        return oid

    def service_without_nrcs(self, qualifier):
        state = self.app.execute_command("create_service", {"variant_id": self.base, "name": qualifier, "qualifier": qualifier, "sid": 62, "nrcs": []}, self.app.r.revision)
        return state["selected_id"]

    def test_nrc_usage_count_and_sort(self):
        self.nrc(0x22, "NRC_22"); self.nrc(0x31, "NRC_31")
        service = self.service_without_nrcs("TP")
        self.app.r.insert("service_nrcs", service_id=service, code=0x22)
        rows = {r["code"]: r for r in self.app.query_objects("nrc", page_size=100)["rows"]}
        self.assertEqual(rows[0x22]["usage"], 1)
        self.assertEqual(rows[0x31]["usage"], 0)
        ordered = [r["code"] for r in self.app.query_objects("nrc", sort="usage", descending=True, page_size=100)["rows"]]
        self.assertEqual(ordered[0], 0x22)
        self.assertEqual([r["code"] for r in self.app.query_objects("nrc", filter_text="NRC_31", page_size=100)["rows"]], [0x31])

    def test_data_type_usage_and_search(self):
        type_id = self.app.query_objects("data_type")["rows"][0]["id"]
        self.app.execute_command("update_type", {"id": type_id, "encoding": "unsigned",
                                                 "bit_length": 8, "conversion": "linear", "factor_text": "1", "offset_text": "0", "unit": "degC"}, self.app.r.revision)
        self.app.execute_command("create_did", {"variant_id": self.base, "name": "Sample", "qualifier": "Sample", "identifier": "0x1234",
                                                "members": [{"kind": "data", "name": "Reading", "qualifier": "Reading", "data_type_id": type_id}]}, self.app.r.revision)
        rows = {r["id"]: r for r in self.app.query_objects("data_type", page_size=100)["rows"]}
        self.assertGreaterEqual(rows[type_id]["usage"], 1)
        self.assertEqual([r["id"] for r in self.app.query_objects("data_type", filter_text="degC", page_size=100)["rows"]], [type_id])
        self.assertIn(type_id, [r["id"] for r in self.app.query_objects("data_type", filter_text="linear", page_size=100)["rows"]])
        self.assertEqual([r["id"] for r in self.app.query_objects("data_type", sort="conversion", descending=True, page_size=100)["rows"]][0], type_id)

    def test_protocol_service_sort_and_search(self):
        for qualifier, request in (("PS_B", "BB"), ("PS_A", "AA")):
            oid = self.app._object("protocol_service", qualifier, qualifier)
            self.app.r.update("objects", {"id": oid}, {"read_only": 1})
            self.app.r.insert("protocol_services", id=oid, request_text=request, positive_text="", negative_text="", template_count=0, service_count=0)
        self.assertEqual([r["request_text"] for r in self.app.query_objects("protocol_service", sort="request_text", page_size=100)["rows"]], ["AA", "BB"])
        self.assertEqual([r["request_text"] for r in self.app.query_objects("protocol_service", filter_text="BB", page_size=100)["rows"]], ["BB"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
