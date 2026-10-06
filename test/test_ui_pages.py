"""U2~U6 화면 데이터 회귀 테스트(작업 스레드 계층, Qt 없이 실행). 22차에서 작성했고 실행하지 않았다.

기대값 근거: CANdelaStudio 13 설치 예제 ABS_ESP-Example.cdd의 원문(ATTRCATS·DEFATTS/ECUATTS·IDENTIFICATION*·DCLTMPLS·
UNSUPPSRVNEG·DTCSTATUSMASK·HISTITEMS)과 docs/ui-candela13-screen-review-2026-09-26.md의 CANdela 화면 기록.
"""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXAMPLES = Path(os.environ.get("DIAGAUTHOR_CDD_EXAMPLES", r"C:\Users\Public\Documents\Vector\CANdelaStudio\13.0\Examples"))
_PROFILE = tempfile.mkdtemp(prefix="diagauthor-pages-")
os.environ["LOCALAPPDATA"] = _PROFILE

from diagstudio.application import Application  # noqa: E402
from diagstudio.domain import DomainError  # noqa: E402
from diagstudio.interchange import cdd_preview  # noqa: E402
from diagstudio.presentation import length_text, sae_dtc, formula_text  # noqa: E402

ABS = EXAMPLES / "ABS_ESP-Example.cdd"


def tearDownModule():
    shutil.rmtree(_PROFILE, ignore_errors=True)


class PresentationHelpersTest(unittest.TestCase):
    def test_sae_dtc_letter_form(self):
        self.assertEqual(sae_dtc(0x402011), "C002011")  # 리뷰 문서의 CANdela 표기
        self.assertEqual(sae_dtc(0x000000), "P000000")
        self.assertEqual(sae_dtc(0xC00000), "U000000")
        self.assertEqual(sae_dtc(0x9A1234), "B1A1234")
        self.assertEqual(sae_dtc(None), "")

    def test_length_text_byte_bit_array_range(self):
        self.assertEqual(length_text({"encoding": "unsigned", "bit_length": 16, "array_length": 1}), "2:0")
        self.assertEqual(length_text({"encoding": "unsigned", "bit_length": 4, "array_length": 1}), "0:4")
        self.assertEqual(length_text({"encoding": "ascii", "bit_length": 8, "array_length": 17}), "1:0 [17]")
        self.assertEqual(length_text({"encoding": "bytes", "bit_length": 8, "array_length": None, "min_length": 2, "max_length": 7}), "1:0 [2..7]")

    def test_formula_shows_divisor_only_when_present(self):
        self.assertEqual(formula_text({"f": "1", "div": "16", "o": "0"}), "phys = 1 / 16 × raw + 0")
        self.assertEqual(formula_text({"f": "0.5", "o": "-40"}), "phys = 0.5 × raw + -40")
        self.assertIsNone(formula_text(None))


class AbsPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ABS.is_file(): raise unittest.SkipTest(f"예제 CDD 없음: {ABS}")
        cls.app = Application()
        cls.state = cls.app.apply_cdd(cdd_preview(ABS))
        cls.variants = {v["name"]: v["id"] for v in cls.state["variants"]}
        cls.base = next(v["id"] for v in cls.state["variants"] if v["is_base"])
        cls.a_sample = next(v["id"] for v in cls.state["variants"] if v["name"].startswith("A"))
        cls.c_sample = next(v["id"] for v in cls.state["variants"] if v["name"].startswith("C"))
        cls.services = {r["qualifier"]: r["id"] for r in cls.app.query_objects("service", cls.base, page_size=1000)["rows"]}

    @classmethod
    def tearDownClass(cls):
        cls.app.close_project()

    def rows(self, kind, variant=None, scope=None):
        return self.app.query_objects(kind, variant, scope=scope, page_size=1000)

    def instance(self, qualifier):
        return self.app.r.one("SELECT i.id FROM diagnostic_instances i JOIN objects o ON o.id=i.id WHERE o.qualifier=?", (qualifier,))["id"]


class SourceViewTest(AbsPages):
    """U5: 보존 원문의 읽기 전용 표."""

    def test_tree_has_source_nodes(self):
        tree = self.state["tree"]
        self.assertTrue(tree["has_interfaces"])
        self.assertEqual(tree["interfaces"], [{"qualifier": "Diagnostic_CAN", "name": "Diagnostic CAN"}])
        self.assertTrue(tree["has_defaults"])
        self.assertEqual(tree["unsupported_nrcs"], 2)
        base = next(v for v in tree["variants"] if v["is_base"])
        self.assertEqual(base["identification"], "features")
        a_sample = next(v for v in tree["variants"] if v["id"] == self.a_sample)
        self.assertEqual(a_sample["identification"], "patterns")
        for key in ("ifaces", "iface:Diagnostic_CAN", "sgroups", "sdefaults", "nrcs_unsupported", f"var:{self.base}:classes", f"var:{self.base}:ident"):
            self.assertIn(key, tree["paths"], key)
            self.assertEqual(self.app.node_path(key)["key"], key)

    def test_interfaces_and_comm_params(self):
        rows = {r["qualifier"]: r for r in self.rows("interfaces")["rows"]}
        self.assertTrue(rows["Diagnostic_CAN"]["supported_check"])
        self.assertFalse(rows["Comfort_CAN"]["supported_check"])
        self.assertFalse(rows["CAN_FD"]["supported_check"])
        params = {r["qualifier"]: r for r in self.rows("comm_params", scope="Diagnostic_CAN")["rows"]}
        self.assertEqual((params["ReqCanId"]["default_text"], params["ReqCanId"]["value_text"]), ("0x700", "0xA1"))  # ECU가 덮어쓴 값
        self.assertEqual((params["P2Client"]["default_text"], params["P2Client"]["value_text"]), ("150", ""))
        self.assertEqual(params["P2Client"]["category"], "Timing")

    def test_identification_features_and_patterns(self):
        features = self.rows("ident_features", self.base, self.base)["rows"]
        self.assertEqual([(r["name"], r["instance"], r["service"]) for r in features], [("SoftwareVersion", "Software_Version", "Read")])
        patterns = self.rows("ident_patterns", self.a_sample, self.a_sample)["rows"]
        self.assertEqual([r["value_text"] for r in patterns], ["0.0.1", "0.0.2", "0.0.3"])
        variants = {r["id"]: r for r in self.rows("variant")["rows"]}
        self.assertEqual(variants[self.a_sample]["pattern_text"], "0.0.1, 0.0.2, 0.0.3")
        details = self.app.details(self.a_sample)
        self.assertEqual(len(details["detail_table"]["rows"]), 3)

    def test_supported_classes_checklist(self):
        rows = {r["qualifier"]: r for r in self.rows("supported_classes", self.base, self.base)["rows"]}
        self.assertEqual(len(rows), 17)  # DCLTMPL 17개
        self.assertTrue(rows["Sessions"]["checked"])
        self.assertTrue(rows["Generic_Jobs"]["checked"])
        self.assertFalse(rows["Download"]["checked"])
        self.assertFalse(rows["Memory"]["checked"])

    def test_protocol_service_patterns(self):
        rows = {r["name"]: r for r in self.rows("protocol_service")["rows"]}
        session = rows["(0x10) DiagnosticSessionControl"]
        self.assertEqual((session["request_pattern"], session["positive_pattern"], session["negative_pattern"]), ("10 LL", "50 LL zz", "7F 10 rc"))
        self.assertTrue(session["required"])
        self.assertTrue(session["used"])

    def test_state_defaults_from_template(self):
        result = self.rows("state_defaults")
        row = next(r for r in result["rows"] if r["class"] == "Security Access" and r["name"] == "Request")
        self.assertEqual([row[f"st:{i}"] for i in range(1, 6)], ["✗", "✓", "✓", "—", "—"])  # mayBeExec (2,3)

    def test_unsupported_service_nrcs_and_status_mask(self):
        self.assertEqual([r["code"] for r in self.rows("unsupported_nrc")["rows"]], [0x11, 0x12])
        view = self.app.fault_memory_view(self.instance("FaultMemory"), self.base)
        bits = view["status_bits"]
        self.assertEqual(len(bits), 8)
        self.assertEqual((bits[0]["name"], bits[0]["used"], bits[1]["used"]), ("Test failed", True, False))
        self.assertTrue(view["dtcs"])
        self.assertTrue(view["extended"])
        with self.assertRaises(DomainError):
            self.app.fault_memory_view(self.instance("VIN"), self.base)

    def test_revision_history(self):
        history = self.app.revision_history()
        self.assertEqual(len(history["history"]), 14)
        self.assertEqual(history["history"][0]["version"], "1.0")
        self.assertEqual(history["authors"][0]["short"], "Sb")

    def test_jobs_are_listed_read_only(self):
        job = self.app.r.one("SELECT j.id FROM job_containers j JOIN objects o ON o.id=j.id WHERE o.qualifier='Generic_Jobs'")["id"]
        result = self.rows("job", self.base, job)
        self.assertIsInstance(result["rows"], list)  # 예제의 Job 컨테이너는 비어 있다


class PageDataTest(AbsPages):
    """U2·U4·2.1 목록 열과 페이지 머리."""

    def test_service_rows_show_candela_patterns_and_unused_template_services(self):
        switches = self.instance("Switches")
        result = self.rows("service", self.base, switches)
        self.assertEqual([r["role"] for r in result["rows"]], ["Read", "Set", "Freeze", "Reset", "Return Control"])  # 템플릿 순서
        unused = [r for r in result["rows"] if r["use"] == "미사용"]
        self.assertEqual([r["name"] for r in unused], ["Freeze", "Return Control"])
        self.assertTrue(all(not r["use_check"] and r["kind"] == "template_service" for r in unused))
        preserved = [r for r in result["rows"] if r["use"] == "보존 원문"]
        self.assertTrue(all(r["kind"] == "template_service" and r["use_check"] for r in preserved))
        self.assertEqual(result["page_info"]["kind"], "instance")
        vin = {r["qualifier"]: r for r in self.rows("service", self.base, self.instance("VIN"))["rows"]}
        self.assertEqual((vin["VIN_Write"]["request_pattern"], vin["VIN_Write"]["positive_pattern"]), ("2E F1 90 zz", "6E F1 90"))
        self.assertEqual((vin["VIN_Read"]["request_pattern"], vin["VIN_Read"]["positive_pattern"]), ("22 F1 90", "62 F1 90 zz"))
        self.assertEqual(vin["VIN_Write"]["protocol"], "(0x2E) WriteDataByIdentifier")

    def test_dtc_columns_and_did_overview(self):
        result = self.rows("dtc", self.base)
        labels = [label for _, label in result["columns"]]
        self.assertIn("Set Condition", labels)
        self.assertTrue(any(r.get("attr:cdd.SetCondition") for r in result["rows"]))
        overview = self.rows("did_overview")
        fields = [field for field, _ in overview["columns"]]
        self.assertIn("used", fields)
        self.assertIn("did_data", fields)

    def test_data_type_length_template_and_formula(self):
        rows = {r["qualifier"]: r for r in self.rows("data_type")["rows"]}
        self.assertEqual(rows["Voltage_1_Byte"]["length_text"], "1:0")
        self.assertFalse(rows["Voltage_1_Byte"]["template"])
        self.assertTrue(any(r["template"] for r in rows.values()))
        details = self.app.details(rows["Voltage_1_Byte"]["id"])
        self.assertGreater(details["usage"], 0)
        self.assertEqual(details["formula_text"], "phys = 1 / 16 × raw + 0")

    def test_state_group_master_detail(self):
        rows = self.rows("state_group")["rows"]
        session = next(r for r in rows if r["group_kind"] == "session")
        self.assertIn("Default", session["states_text"])
        details = self.app.details(session["id"])
        self.assertEqual([r["name"] for r in details["detail_table"]["rows"]][0], "Default")  # 첫 상태 = 초기 상태

    def test_dependency_rows_carry_group_keys(self):
        rows, _ = self.app._dependency_rows(self.base, self.app._names())
        start = [r for r in rows if r["sid"] == 0x10]
        self.assertTrue(start)
        self.assertEqual(len({r["group_key"] for r in start}), 1)
        self.assertEqual(start[0]["group_label"], "Sessions / * / Start")

    def test_compact_tree_and_shortcut_matrix(self):
        compact = self.app.compact_tree(self.c_sample)
        names = [i["name"] for c in compact["classes"] for i in c["instances"]]
        self.assertIn("Yaw Rate Sensor", names)
        matrix = self.app.shortcut_matrix()
        self.assertEqual([v["id"] for v in matrix["variants"]][0], self.base)
        self.assertTrue(all(isinstance(r["variants"], list) for r in matrix["rows"]))


class CommandTest(AbsPages):
    """U3·U4·U6 명령: 인스턴스 사본 교체, 상태 조건 일괄 변경, Rework 목록. 모두 실행 취소된다."""

    def undo(self):
        self.app.execute_command("undo", {}, self.app.r.revision)

    def test_bulk_conditions_block_and_undo(self):
        ids = [self.services["VIN_Read"], self.services["VIN_Write"]]
        default = self.app.r.one("SELECT s.id FROM states s JOIN objects o ON o.id=s.id WHERE o.qualifier='Default'")["id"]
        before = self.app.r.all("SELECT service_id,state_id,allowed FROM service_conditions WHERE service_id IN (?,?) ORDER BY service_id,state_id", ids)
        self.app.execute_command("bulk_conditions", {"ids": ids, "state_id": default, "value": "block"}, self.app.r.revision)
        for sid in ids:
            self.assertEqual(self.app.r.one("SELECT allowed FROM service_conditions WHERE service_id=? AND state_id=?", (sid, default))["allowed"], 0)
        self.undo()
        after = self.app.r.all("SELECT service_id,state_id,allowed FROM service_conditions WHERE service_id IN (?,?) ORDER BY service_id,state_id", ids)
        self.assertEqual(before, after)

    def test_bulk_conditions_rejects_bad_value(self):
        with self.assertRaises(DomainError):
            self.app.execute_command("bulk_conditions", {"ids": [self.services["VIN_Read"]], "state_id": "x", "value": "block"}, self.app.r.revision)

    def test_detach_instance_replaces_by_copy_in_variant(self):
        vin = self.instance("VIN")
        state = self.app.execute_command("detach_instance", {"id": self.services["VIN_Write"], "variant_id": self.a_sample}, self.app.r.revision)
        copy = state["selected_id"]
        self.assertNotEqual(copy, self.services["VIN_Write"])
        new_instance = self.app.r.one("SELECT instance_id FROM services WHERE id=?", (copy,))["instance_id"]
        self.assertEqual(self.app.r.one("SELECT owner_variant_id FROM diagnostic_instances WHERE id=?", (new_instance,))["owner_variant_id"], self.a_sample)
        self.assertIsNone(self.app.r.one("SELECT 1 FROM variant_instances WHERE variant_id=? AND instance_id=?", (self.a_sample, vin)))
        self.assertTrue(self.app.r.one("SELECT 1 FROM variant_instances WHERE variant_id=? AND instance_id=?", (self.base, vin)))
        self.assertEqual(self.app.r.one("SELECT count(*) n FROM services WHERE instance_id=?", (new_instance,))["n"], 2)
        self.undo()
        self.assertTrue(self.app.r.one("SELECT 1 FROM variant_instances WHERE variant_id=? AND instance_id=?", (self.a_sample, vin)))

    def test_detach_instance_refuses_preserved_semantics(self):
        fault_memory = self.instance("FaultMemory")
        with self.assertRaises(DomainError):
            self.app.execute_command("detach_instance", {"id": fault_memory, "variant_id": self.a_sample}, self.app.r.revision)

    def dtc_attributes(self, oid):
        return {a["key"]: a["value_text"] for a in self.app.r.all("SELECT key,value_text FROM object_attributes WHERE object_id=?", (oid,))}

    def test_dtc_record_items_edit_and_undo(self):
        """DTC 속성 편집. 근거: ABS_ESP 예제의 레코드 항목 템플릿(TR SetCondition·ResetCondition·SpecialInstruction(opt),
        ENUM CorrectiveAction(req, ETAG 0 'Replace ECU' … 5 'No known corrective action'))과 0x402011 레코드."""
        oid = self.app.r.one("SELECT id FROM dtcs WHERE code=?", (0x402011,))["id"]
        items = {i["key"]: i for i in self.app.details(oid)["record_items"]}
        self.assertEqual(set(items), {"cdd.SetCondition", "cdd.ResetCondition", "cdd.SpecialInstruction", "cdd.CorrectiveAction"})
        self.assertEqual((items["cdd.CorrectiveAction"]["kind"], items["cdd.CorrectiveAction"]["required"]), ("enum", True))
        self.assertIn({"raw": 0, "label": "Replace ECU"}, items["cdd.CorrectiveAction"]["choices"])
        self.assertEqual((items["cdd.SetCondition"]["name"], items["cdd.SetCondition"]["required"]), ("Set Condition", False))
        before = self.dtc_attributes(oid)
        self.assertEqual((before["cdd.CorrectiveAction"], before["cdd.CorrectiveAction#raw"]), ("No known corrective action", "5"))
        self.app.execute_command("update_dtc", {"id": oid, "attributes": [
            {"key": "cdd.SetCondition", "value": "Value < 0,3V"}, {"key": "cdd.CorrectiveAction", "raw": 0},
            {"key": "cdd.ResetCondition", "value": ""}, {"key": "cdd.SpecialInstruction", "value": "Check harness"}]}, self.app.r.revision)
        after = self.dtc_attributes(oid)
        self.assertEqual(after["cdd.SetCondition"], "Value < 0,3V")
        self.assertEqual((after["cdd.CorrectiveAction"], after["cdd.CorrectiveAction#raw"]), ("Replace ECU", "0"))
        self.assertNotIn("cdd.ResetCondition", after)  # 선택 항목을 비우면 지운다
        self.assertEqual(after["cdd.SpecialInstruction"], "Check harness")
        row = next(r for r in self.rows("dtc", self.base)["rows"] if r["id"] == oid)
        self.assertEqual(row["attr:cdd.CorrectiveAction"], "Replace ECU")
        self.undo()
        self.assertEqual(self.dtc_attributes(oid), before)

    def test_dtc_attribute_edit_refusals(self):
        oid = self.app.r.one("SELECT id FROM dtcs WHERE code=?", (0x402011,))["id"]
        before = self.dtc_attributes(oid)
        for bad in ([{"key": "cdd.CorrectiveAction", "raw": 99}],       # 템플릿에 없는 열거 값
                    [{"key": "cdd.CorrectiveAction", "raw": None}],     # 필수 항목 비우기
                    [{"key": "cdd.CorrectiveAction#raw", "value": "1"}],  # 원시값 직접 수정
                    [{"key": "cdd.NoSuchItem", "value": "x"}]):          # 템플릿에 없는 키
            with self.subTest(bad=bad), self.assertRaises(DomainError):
                self.app.execute_command("update_dtc", {"id": oid, "attributes": bad}, self.app.r.revision)
        self.assertEqual(self.dtc_attributes(oid), before)

    def test_rework_items_round_trip(self):
        items = [{"done": False, "message": "NRC 확인", "link": "/ABS/ESP/Base Variant/DID"}]
        self.app.execute_command("set_rework", {"items": items}, self.app.r.revision)
        self.assertEqual(self.app.rework_items()["items"], items)
        with self.assertRaises(DomainError):
            self.app.execute_command("set_rework", {"items": [{"message": " "}]}, self.app.r.revision)
        self.undo()
        self.assertEqual(self.app.rework_items()["items"], [])


class NoSourceProjectTest(unittest.TestCase):
    """원본 CDD가 없는 프로젝트: 원문 목록은 비어 있고 안내 문장을 돌려준다. 지원 클래스는 프로젝트 클래스로 만든다."""

    @classmethod
    def setUpClass(cls):
        cls.app = Application()
        cls.state = cls.app.new_project("No Source", "NoSource")
        cls.base = cls.state["variants"][0]["id"]

    @classmethod
    def tearDownClass(cls):
        cls.app.close_project()

    def test_source_lists_are_empty_with_note(self):
        result = self.app.query_objects("interfaces")
        self.assertEqual(result["rows"], [])
        self.assertIn("원본 CDD", result["page_info"]["text"])
        self.assertFalse(self.state["tree"]["has_interfaces"])
        self.assertEqual(self.app.revision_history()["history"], [])

    def test_supported_classes_without_source(self):
        result = self.app.query_objects("supported_classes", self.base, scope=self.base)
        self.assertNotIn("page_info", result)

    def test_dtc_attributes_without_source(self):
        """원본 CDD가 없으면 레코드 항목 템플릿이 없어 cdd.* 속성은 바꿀 수 없다. user.* 속성은 바꿀 수 있다."""
        state = self.app.execute_command("create_dtc", {"variant_id": self.base, "name": "T", "qualifier": "T_DTC", "code": "0x123456"}, self.app.r.revision)
        oid = state["selected_id"]
        self.assertEqual(self.app.details(oid)["record_items"], [])
        with self.assertRaises(DomainError):
            self.app.execute_command("update_dtc", {"id": oid, "attributes": [{"key": "cdd.SetCondition", "value": "x"}]}, self.app.r.revision)
        self.app.execute_command("update_dtc", {"id": oid, "attributes": [{"key": "user.Owner", "value": "QA"}]}, self.app.r.revision)
        self.assertEqual(self.app.r.one("SELECT value_text FROM object_attributes WHERE object_id=? AND key='user.Owner'", (oid,))["value_text"], "QA")


if __name__ == "__main__":
    unittest.main(verbosity=2)
