"""27차: UI 개선 3단계(P3) 새 기능 회귀 테스트 — 값 형식(M2-5), 데이터 타입 바꾸기(M6-3), 변형 마법사(M6-4),
비교 보기(M6-6), 상태 그래프·의존성 옵션(M6-7), 인스턴스 이름(M5-4), 대화상자 위젯.

근거: docs/ui-manual-review-2026-09-26.md 3단계와 CANdelaStudio 13 도움말 FeatureID([VALUI-R050], [VW-R040]·[VW-R200]·[VW-R510], [CMP-R070] 등).
ABS_ESP 예제가 필요한 테스트는 예제가 없으면 건너뛴다. Qt 위젯 테스트는 PySide6가 없으면 건너뛴다.
"""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if "DIAGAUTHOR_SETTINGS" not in os.environ:
    os.environ["DIAGAUTHOR_SETTINGS"] = os.path.join(tempfile.mkdtemp(prefix="diagauthor-settings-"), "settings.ini")
_PROFILE = tempfile.mkdtemp(prefix="diagauthor-p3-")
os.environ["LOCALAPPDATA"] = _PROFILE
EXAMPLES = Path(os.environ.get("DIAGAUTHOR_CDD_EXAMPLES", r"C:\Users\Public\Documents\Vector\CANdelaStudio\13.0\Examples"))
ABS = EXAMPLES / "ABS_ESP-Example.cdd"

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

from diagstudio.application import Application  # noqa: E402
from diagstudio.domain import DomainError  # noqa: E402
from diagstudio.presentation import format_value, parse_value, value_formats  # noqa: E402


def tearDownModule():
    shutil.rmtree(_PROFILE, ignore_errors=True)


class ValueFormatTest(unittest.TestCase):
    """M2-5: 저장 값은 10진 원시값, 보이는 글자만 10진·16진·2진. [VALUI-R050] BCD는 10진·16진(자리마다 한 니블)."""

    def test_formats_per_encoding(self):
        self.assertEqual(value_formats("unsigned"), ("dec", "hex", "bin"))
        self.assertEqual(value_formats("bcd"), ("dec", "hex"))
        self.assertEqual(value_formats("ascii"), ())

    def test_format_and_parse_round_trip(self):
        self.assertEqual(format_value("65", "unsigned", 8, "hex"), "0x41")
        self.assertEqual(format_value("65", "unsigned", 16, "hex", zero_fill=True), "0x0041")
        self.assertEqual(format_value("5", "unsigned", 4, "bin"), "0b0101")
        self.assertEqual(format_value("-1", "signed", 8, "hex"), "0xFF")  # 2의 보수
        self.assertEqual(format_value("1234", "bcd", 16, "hex"), "0x1234")  # 자리마다 한 니블
        self.assertEqual(format_value("300", "unsigned", 8, "hex"), "300")  # 비트 길이를 넘으면 그대로(작업 스레드가 거부)
        self.assertEqual(format_value("abc", "ascii", 24, "hex"), "abc")
        self.assertEqual(parse_value("0x41", "unsigned", 8, "dec"), "65")  # 접두어는 형식과 상관없이
        self.assertEqual(parse_value("41", "unsigned", 8, "hex"), "65")  # 접두어 없으면 지금 형식
        self.assertEqual(parse_value("0xFF", "signed", 8, "dec"), "-1")
        self.assertEqual(parse_value("-3", "signed", 8, "hex"), "-3")
        self.assertEqual(parse_value("0b101", "unsigned", 8, "hex"), "5")
        self.assertEqual(parse_value("0x0099", "bcd", 16, "hex"), "99")
        for fmt in ("dec", "hex", "bin"):
            for value in ("0", "7", "200"):
                self.assertEqual(parse_value(format_value(value, "unsigned", 8, fmt), "unsigned", 8, fmt), value)

    def test_parse_rejects(self):
        with self.assertRaises(ValueError): parse_value("0x1A", "bcd", 16, "hex")  # BCD 니블은 0~9
        with self.assertRaises(ValueError): parse_value("0b11", "bcd", 16, "dec")  # BCD에 2진 없음
        with self.assertRaises(ValueError): parse_value("-1", "bcd", 16, "dec")
        with self.assertRaises(ValueError): parse_value("12G", "unsigned", 8, "hex")


class ReplaceTypeTest(unittest.TestCase):
    """M6-3 데이터 타입 바꾸기: 미리보기의 영향·충돌과 한 명령(실행 취소 한 번) 적용."""

    def setUp(self):
        self.app = Application()
        state = self.app.new_project("Replace ECU", "ReplaceECU")
        self.base = state["variants"][0]["id"]
        t = lambda **kw: self.run_command("create_type", {"conversion": "identity", **kw})["selected_id"]
        self.u8 = t(name="U8", qualifier="U8", encoding="unsigned", bit_length=8)
        self.u16 = t(name="U16", qualifier="U16", encoding="unsigned", bit_length=16)
        self.pct = t(name="Pct", qualifier="Pct", encoding="unsigned", bit_length=8, conversion="linear", factor_text="1", offset_text="0", raw_upper="100")
        self.a4 = t(name="A4", qualifier="A4", encoding="ascii", bit_length=8, array_length=4)
        self.a2 = t(name="A2", qualifier="A2", encoding="ascii", bit_length=8, array_length=2)
        self.run_command("create_did", {"variant_id": self.base, "name": "D1", "qualifier": "D1", "identifier": "0x0101", "members": [
            {"kind": "data", "data_type_id": self.u8, "name": "V", "qualifier": "V", "default_text": "5"},
            {"kind": "data", "data_type_id": self.u8, "name": "W", "qualifier": "W", "default_text": "150"},
            {"kind": "data", "data_type_id": self.u8, "name": "Z", "qualifier": "Z"}]})
        self.run_command("create_did", {"variant_id": self.base, "name": "D2", "qualifier": "D2", "identifier": "0x0102", "members": [
            {"kind": "data", "data_type_id": self.a4, "name": "S", "qualifier": "S", "default_text": "ABCD"}]})
        self.objects = {r["name"]: r["id"] for r in self.app.r.all(
            "SELECT d.id, t.value name FROM data_objects d JOIN localized_texts t ON t.object_id=d.id AND t.field='name'")}

    def tearDown(self):
        self.app.close_project()

    def run_command(self, action, payload):
        return self.app.execute_command(action, payload, self.app.r.revision)

    def rows(self, old, new=None):
        return {r["name"]: r for r in self.app.replace_type_preview(old, new)["rows"]}

    def test_preview_lists_uses_and_length_impact(self):
        rows = self.rows(self.u8)
        self.assertEqual(set(rows), {"V", "W", "Z"})
        self.assertTrue(all(r["conflict"] is None and r["impact"] is None for r in rows.values()))
        rows = self.rows(self.u8, self.u16)
        self.assertIn("길이 8비트 → 16비트 (+8비트)", rows["V"]["impact"])
        self.assertIn("뒤 필드 2개 위치 이동", rows["V"]["impact"])
        self.assertNotIn("위치 이동", rows["Z"]["impact"])  # 마지막 멤버
        preview = self.app.replace_type_preview(self.u8)
        self.assertNotIn(self.u8, {t["id"] for t in preview["types"]})
        with self.assertRaises(DomainError): self.app.replace_type_preview(self.u8, self.u8)

    def test_conflicts(self):
        rows = self.rows(self.u8, self.pct)
        self.assertIsNone(rows["V"]["conflict"])
        self.assertIn("맞지 않습니다", rows["W"]["conflict"])  # 원시값 150 > raw_upper 100
        rows = self.rows(self.u8, self.a4)
        self.assertIn("값의 종류가 바뀌는", rows["V"]["conflict"])  # 정수 → 글자
        self.assertIsNone(rows["Z"]["conflict"])  # 값이 없으면 종류가 바뀌어도 된다
        rows = self.rows(self.a4, self.a2)
        self.assertIn("4바이트인데 새 타입은 2바이트", rows["S"]["conflict"])  # 26차 검토: 글자 길이
        # 비트필드 안에서 길이가 바뀌면 막는다(가져온 비트필드 구조를 흉내 낸다).
        structure = self.app.r.one("SELECT structure_id FROM dids WHERE identifier=257")["structure_id"]
        self.app.r.update("structures", {"id": structure}, {"kind": "bitfield"})
        self.assertIn("비트필드", self.rows(self.u8, self.u16)["V"]["conflict"])

    def test_apply_is_one_command_and_rechecks(self):
        revision = self.app.r.revision
        state = self.run_command("replace_data_type", {"old_type_id": self.u8, "new_type_id": self.u16, "data_object_ids": [self.objects["V"], self.objects["Z"]]})
        self.assertEqual(state["selected_id"], self.u16)
        self.assertEqual(self.app.r.revision, revision + 1)
        typed = lambda name: self.app.r.one("SELECT data_type_id, default_text FROM data_objects WHERE id=?", (self.objects[name],))
        self.assertEqual((typed("V")["data_type_id"], typed("V")["default_text"]), (self.u16, "5"))  # 원시값 유지
        self.assertEqual(typed("W")["data_type_id"], self.u8)  # 고르지 않은 사용처는 그대로
        self.run_command("undo", {})
        self.assertEqual(typed("V")["data_type_id"], self.u8)
        with self.assertRaises(DomainError):  # 충돌 행이 하나라도 있으면 전체 거부
            self.run_command("replace_data_type", {"old_type_id": self.u8, "new_type_id": self.pct, "data_object_ids": [self.objects["V"], self.objects["W"]]})
        self.assertEqual(typed("V")["data_type_id"], self.u8)
        with self.assertRaises(DomainError):
            self.run_command("replace_data_type", {"old_type_id": self.u8, "new_type_id": self.u16, "data_object_ids": []})


@unittest.skipUnless(ABS.is_file(), f"예제 CDD 없음: {ABS}")
class AbsP3Test(unittest.TestCase):
    """ABS_ESP 예제: 변형 마법사, 비교 보기, 상태 그래프, 의존성 주소 방식, 인스턴스 머리."""

    @classmethod
    def setUpClass(cls):
        from diagstudio.interchange import cdd_preview
        cls.app = Application()
        state = cls.app.apply_cdd(cdd_preview(str(ABS)))
        cls.base = next(v["id"] for v in state["variants"] if v["is_base"])
        cls.others = [v["id"] for v in state["variants"] if not v["is_base"]]

    @classmethod
    def tearDownClass(cls):
        cls.app.close_project()

    def run_command(self, action, payload):
        return self.app.execute_command(action, payload, self.app.r.revision)

    def count(self, table, variant, extra=""):
        return self.app.r.one(f"SELECT count(*) n FROM {table} WHERE variant_id=?{extra}", (variant,))["n"]

    # ---------------------------------------------------------------- M6-4
    def test_wizard_items(self):
        items = self.app.variant_wizard_items(self.base)
        instances = [i for c in items["classes"] for i in c["instances"]]
        self.assertEqual(items["source"], self.base)
        self.assertEqual(len(instances), self.count("variant_instances", self.base))
        self.assertEqual({i["name"] for i in instances if i["required"]}, {"Default Session", "Tester Present"})
        self.assertTrue(any(i["copy_block"] for i in instances))  # 원문 배치·보존 원문이 걸린 인스턴스
        self.assertEqual(items["counts"]["dids"], self.count("variant_dids", self.base))
        with self.assertRaises(DomainError): self.app.variant_wizard_items("no-such-variant")

    def test_wizard_modes_and_undo(self):
        base = self.base
        instances = self.count("variant_instances", base)
        blocked = sum(1 for c in self.app.variant_wizard_items(base)["classes"] for i in c["instances"] if i["copy_block"] or i["required"])
        made = {}
        for mode in ("ref", "copy", "empty"):
            state = self.run_command("create_variant", {"name": f"W {mode}", "qualifier": f"W_{mode}", "source_id": base, "mode": mode})
            made[mode] = state["selected_id"]
        ref, copy, empty = made["ref"], made["copy"], made["empty"]
        self.assertEqual(self.count("variant_instances", ref), instances)
        self.assertEqual(self.app.r.one("SELECT count(*) n FROM diagnostic_instances WHERE owner_variant_id=?", (ref,))["n"], 0)
        self.assertEqual(self.count("variant_instances", copy), instances)
        # 사본으로: 필수·사본 불가 인스턴스만 참조, 나머지는 이 변형이 원본인 사본
        self.assertEqual(self.app.r.one("SELECT count(*) n FROM diagnostic_instances WHERE owner_variant_id=?", (copy,))["n"], instances - blocked)
        self.assertEqual(self.count("variant_dtcs", copy), self.count("variant_dtcs", base))
        # 빈 변형: 기본 변형의 필수 인스턴스만, DTC 없음
        self.assertEqual(self.count("variant_instances", empty), 2)
        self.assertEqual(self.count("variant_dtcs", empty), 0)
        # [VW-R510] 모든 인스턴스가 참조·사본이면 기준 변형의 설명을 복사한다
        description = lambda v: {t["value"] for t in self.app.r.all("SELECT value FROM localized_texts WHERE object_id=? AND field='description'", (v,))}
        self.assertEqual(description(ref), description(base))
        self.assertNotEqual(description(empty), description(base))
        for _ in range(3): self.run_command("undo", {})
        self.assertFalse(self.app.r.one("SELECT 1 FROM variants WHERE id IN (?,?,?)", (ref, copy, empty)))

    def test_wizard_rules(self):
        items = self.app.variant_wizard_items(self.base)
        instances = [i for c in items["classes"] for i in c["instances"]]
        required = next(i for i in instances if i["required"])
        blocked = next(i for i in instances if i["copy_block"])
        with self.assertRaises(DomainError):
            self.run_command("create_variant", {"name": "X", "qualifier": "X1", "source_id": self.base, "mode": "empty", "instances": {required["id"]: "none"}})
        with self.assertRaises(DomainError):
            self.run_command("create_variant", {"name": "X", "qualifier": "X2", "source_id": self.base, "mode": "ref", "instances": {blocked["id"]: "copy"}})
        with self.assertRaises(DomainError):
            self.run_command("create_variant", {"name": "X", "qualifier": "X3", "source_id": self.base, "mode": "other"})
        # 인스턴스별 동작: 필수가 아닌 하나를 빼면 설명은 복사하지 않는다([VW-R510]).
        optional = next(i for i in instances if not i["required"])
        state = self.run_command("create_variant", {"name": "Partial", "qualifier": "Partial", "source_id": self.base, "mode": "ref", "instances": {optional["id"]: "none"}})
        vid = state["selected_id"]
        self.assertEqual(self.count("variant_instances", vid), len(instances) - 1)
        self.assertFalse(self.app.r.one("SELECT 1 FROM variant_instances WHERE variant_id=? AND instance_id=?", (vid, optional["id"])))
        self.run_command("undo", {})

    # ---------------------------------------------------------------- M6-6
    def test_compare_view_variants(self):
        result = self.app.compare_view(self.base, self.others[0])
        self.assertTrue(result["same_project"])
        self.assertTrue(result["nodes"])
        self.assertEqual(set(result["counts"]) >= {"left", "right", "changed", "aspects", "reference", "record", "attribute", "transition", "language"}, True)
        self.assertEqual(result["left"]["source"], "작업본")
        self.assertTrue(all("level" in n and "status" in n for n in result["nodes"]))
        with self.assertRaises(DomainError): self.app.compare_view(self.base, self.base)

    def test_compare_view_saved_file(self):
        path = os.path.join(_PROFILE, "abs-compare.diagproj")
        self.app.save_project(path)
        dtc = self.app.r.one("SELECT d.id FROM dtcs d JOIN variant_dtcs v ON v.dtc_id=d.id WHERE v.variant_id=? LIMIT 1", (self.base,))["id"]
        self.run_command("update_common", {"id": dtc, "name": "Renamed For Compare"})
        try:
            saved = self.app.compare_file_variants(path)
            right = next(v["id"] for v in saved["variants"] if v["is_base"])
            result = self.app.compare_view(self.base, right, path=path, expected_digest=saved["digest"])
            self.assertEqual(result["right"]["source"], "저장된 파일")
            self.assertFalse(result["same_project"])
            changed = [n for n in result["nodes"] if n["status"] == "changed"]
            self.assertTrue(any("Renamed For Compare" in (n.get("left") or "") + (n.get("label") or "") for n in changed))
            from diagstudio.comparison import export_text
            self.assertIn("Renamed For Compare", export_text(result))
        finally:
            self.run_command("undo", {})

    # ---------------------------------------------------------------- M6-7
    def test_state_graph_variant_and_requests(self):
        whole = self.app.state_graph()
        graph = self.app.state_graph(self.base)
        self.assertTrue(graph["groups"] and graph["edges"])
        self.assertLessEqual(len(graph["edges"]), len(whole["edges"]))
        self.assertTrue(any(e["request"].startswith("10 ") for e in graph["edges"]))  # 세션 전이의 요청 바이트
        self.assertTrue(all("unrestricted" in s for g in graph["groups"] for s in g["states"]))
        self.assertEqual(graph["variant"], self.base)
        with self.assertRaises(DomainError): self.app.state_graph("no-such-variant")

    def test_dependency_rows_carry_addressing(self):
        rows = self.app.query_objects("dependency", self.base, page_size=0)["rows"]
        self.assertTrue(rows)
        self.assertTrue(all(r.get("addressing") in ("P", "F", "P/F", "", None) for r in rows))
        self.assertTrue(any(r.get("addressing") for r in rows))

    # ---------------------------------------------------------------- M5-4
    def test_instance_page_header_and_rename(self):
        instance = next(r for r in self.app.query_objects("diagnostic_instance", self.base, page_size=0)["rows"] if r.get("name") == "Default Session")
        page = self.app.query_objects("service", self.base, scope=instance["id"], page_size=0)["page_info"]
        self.assertEqual(page["kind"], "instance")
        self.assertIn("editable", page)
        if not page["editable"]: return  # 원문 인스턴스면 이름 편집 대상이 아니다
        self.run_command("update_common", {"id": instance["id"], "name": "Default Session Renamed"})
        page = self.app.query_objects("service", self.base, scope=instance["id"], page_size=0)["page_info"]
        self.assertEqual(page["title"], "Default Session Renamed")
        self.run_command("undo", {})


@unittest.skipIf(QApplication is None, "PySide6 없음")
class P3WidgetTest(unittest.TestCase):
    """대화상자 위젯: 값 형식 단추, 데이터 타입 바꾸기 표, 변형 마법사 동작 값, 비교 창."""

    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_members_editor_value_format(self):
        from diagstudio.dialogs import MembersEditor
        catalog = {"types": [{"id": "u8", "name": "UInt8", "encoding": "unsigned", "bit_length": 8},
                             {"id": "b16", "name": "BCD16", "encoding": "bcd", "bit_length": 16}]}
        editor = MembersEditor(catalog, [{"kind": "data", "name": "T", "qualifier": "T", "data_type_id": "u8", "default_text": "65", "constant_text": ""},
                                         {"kind": "data", "name": "B", "qualifier": "B", "data_type_id": "b16", "default_text": "12", "constant_text": ""}], zero_fill=True)
        editor.set_value_format("hex")
        self.assertEqual(editor.table.item(0, 2).text(), "0x41")
        self.assertEqual(editor.values()[0]["default_text"], "65")  # 저장 값은 그대로
        editor.table.item(1, 2).setText("0x1A")
        self.assertTrue(editor.invalid())  # BCD 니블 9 초과
        editor.table.item(1, 2).setText("0x0099")
        self.assertIsNone(editor.invalid())
        self.assertEqual(editor.values()[1]["default_text"], "99")

    def test_dtc_code_format_buttons(self):
        from diagstudio.dialogs import ObjectDialog
        dialog = ObjectDialog("dtc", {}, {"id": "x", "name": "D", "qualifier": "D", "code": 0x402011})
        self.assertIn("C002011", dialog.code_hint.text())
        dialog.set_code_format("dec")
        self.assertEqual(dialog.form.fields["code"].text(), str(0x402011))
        self.assertEqual(dialog.dtc_code(), 0x402011)
        dialog.form.fields["code"].setText("40201G"); dialog.set_code_format("hex")
        self.assertIsNotNone(dialog.invalid_values())

    def test_replace_type_dialog_checks_only_free_rows(self):
        from diagstudio.tools import ReplaceTypeDialog
        preview = {"old": {"id": "t1", "name": "U8", "encoding": "unsigned", "bits": 8, "length_text": "1:0"}, "new_id": "t2",
                   "types": [{"id": "t2", "name": "U16", "encoding": "unsigned", "bits": 16, "length_text": "2:0"}],
                   "rows": [{"id": "a", "name": "A", "owner": "D", "kind_text": "DID", "path": "Base / D / A", "conflict": None, "impact": "길이"},
                            {"id": "b", "name": "B", "owner": "D", "kind_text": "DID", "path": "Base / D / B", "conflict": "비트필드", "impact": None}]}
        dialog = ReplaceTypeDialog(preview)
        dialog.check_all()
        self.assertEqual(dialog.selected_ids(), ["a"])
        self.assertEqual(dialog.values(), {"old_type_id": "t1", "new_type_id": "t2", "data_object_ids": ["a"]})
        dialog.check_none()
        self.assertEqual(dialog.selected_ids(), [])

    def test_variant_wizard_values(self):
        from diagstudio.tools import VariantWizardDialog
        items = {"source": "v1", "base": "v1", "counts": {"dids": 1, "dtcs": 0, "jobs": 0},
                 "variants": [{"id": "v1", "name": "Base", "qualifier": "Base", "is_base": True}],
                 "classes": [{"id": "c1", "name": "Sessions", "instances": [
                     {"id": "i1", "name": "Default", "identifier_text": "01", "required": True, "copy_block": None, "services": ""},
                     {"id": "i2", "name": "Extended", "identifier_text": "03", "required": False, "copy_block": None, "services": ""},
                     {"id": "i3", "name": "Raw", "identifier_text": "", "required": False, "copy_block": "원문", "services": ""}]}]}
        dialog = VariantWizardDialog(items)
        values = dialog.values()
        self.assertEqual(values["mode"], "ref")
        self.assertEqual(values["source_id"], "v1")
        self.assertTrue(values["qualifier"])
        dialog.set_mode("copy")
        actions = dialog.values()["instances"]
        self.assertEqual((actions.get("i1"), actions.get("i2"), actions.get("i3")), ("ref", "copy", "ref"))  # 필수·사본 불가는 참조
        dialog.set_mode("empty")
        actions = dialog.values()["instances"]
        self.assertEqual((actions.get("i1"), actions.get("i2")), ("ref", "none"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
