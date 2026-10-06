"""CDD 가져오기 → 표시 → 저장 → 다시 열기 회귀 테스트. Qt 없이 실행한다.

실행: .venv\\Scripts\\python.exe -m unittest discover -s test -v
예제 CDD는 저장소에 복사하지 않는다(Vector 배포물). 기본 위치에 없으면 해당 테스트를 건너뛴다.
다른 위치는 환경 변수 DIAGAUTHOR_CDD_EXAMPLES로 지정한다.
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
# CANoe 13 설치 예제의 CDD(다른 CANdela 형식 버전). 저장소에 복사하지 않고 설치 위치에서 읽는다.
CANOE_SAMPLES = Path(os.environ.get("DIAGAUTHOR_CANOE_SAMPLES", r"C:\Users\Public\Documents\Vector\CANoe\Sample Configurations 13.0.172\CAN"))
# Vector 공용 문서 폴더 전체(CANdelaStudio·CANoe 예제의 모든 CDD/CDDT). 저장소에 복사하지 않고 설치 위치에서 읽는다.
VECTOR_PUBLIC = Path(os.environ.get("DIAGAUTHOR_VECTOR_PUBLIC", r"C:\Users\Public\Documents\Vector"))
# 추가 시험 문서 폴더(os.pathsep로 구분). 기본값은 이 PC의 cdd_utils 예제(cantools 테스트 CDD 2.0.5·10.0.108, CddTools 12.0.104).
# 저장소에 복사하지 않는다. CANdela 문서가 아닌 수제 파일(doctype 없음)은 거부가 정상이다.
EXTRA_CDD_DIRS = [Path(x) for x in os.environ.get("DIAGAUTHOR_EXTRA_CDD_DIRS", r"C:\work\setting\cdd_utils\examples").split(os.pathsep) if x]

# 작업 DB·잠금·로그가 사용자 프로필을 건드리지 않도록 import 전에 격리한다.
_PROFILE = tempfile.mkdtemp(prefix="diagauthor-test-")
os.environ["LOCALAPPDATA"] = _PROFILE

from diagstudio.application import Application  # noqa: E402
from diagstudio.domain import DomainError  # noqa: E402
from diagstudio.interchange import build_odx, cdd_linear, cdd_preview  # noqa: E402
from diagstudio.cdd_model import tuv  # noqa: E402
import hashlib  # noqa: E402
from lxml import etree as ET  # noqa: E402
from diagstudio.persistence import Session  # noqa: E402
from diagstudio.presentation import details_text  # noqa: E402

# 설치 예제에서 확인한 값. 파서 범위를 넓히면 함께 갱신한다.
EXPECTED = {
    "Multilingual-Example.cdd": {"variants": 1, "dids": 2, "min_types": 20},
    # 19 DID 모두(010A의 STRUCT 비트필드 포함, 10차). 범위·예외값은 schema 2부터 가져온다.
    "ABS_ESP-Example.cdd": {"variants": 4, "dids": 19, "min_types": 85},
}


def tearDownModule():
    shutil.rmtree(_PROFILE, ignore_errors=True)


class CddImportTest(unittest.TestCase):
    def setUp(self):
        self.app = Application()
        self.out = Path(tempfile.mkdtemp(prefix="diagauthor-out-"))

    def tearDown(self):
        self.app.close_project()
        shutil.rmtree(self.out, ignore_errors=True)

    def load(self, name):
        path = EXAMPLES / name
        if not path.is_file(): self.skipTest(f"예제 CDD 없음: {path}")
        return self.app.apply_cdd(cdd_preview(path))

    def test_preview_and_apply(self):
        for name, expected in EXPECTED.items():
            with self.subTest(name=name):
                path = EXAMPLES / name
                if not path.is_file(): self.skipTest(f"예제 CDD 없음: {path}")
                preview = cdd_preview(path)
                self.assertEqual(preview["coverage"]["variants"], expected["variants"])
                self.assertEqual(preview["coverage"]["dids"], expected["dids"])
                self.assertGreaterEqual(preview["coverage"]["data_types"], expected["min_types"])
                state = self.app.apply_cdd(preview)
                self.assertTrue(state["open"])
                self.assertEqual(len(state["variants"]), expected["variants"])
                self.assertEqual(sum(v["is_base"] for v in state["variants"]), 1)
                self.assertEqual(state["counts"]["did"], expected["dids"])
                self.assertEqual(state["counts"]["unsupported"], preview["coverage"]["unsupported"])

    def test_base_variant_lists_dids_with_structure(self):
        state = self.load("ABS_ESP-Example.cdd")
        base = next(v["id"] for v in state["variants"] if v["is_base"])
        page = self.app.query_objects("did", base)
        self.assertGreater(page["total"], 0)
        for row in page["rows"]:
            details = self.app.details(row["id"])
            self.assertIsInstance(details["identifier"], int)
            self.assertTrue(details["layout"], row["qualifier"])
            self.assertNotEqual(details["layout"][0]["kind"], "error")
            text = details_text(details)
            self.assertIn("데이터 구조", text)
            self.assertIn(f"0x{details['identifier']:04X}", text)

    def test_every_listed_object_opens_details(self):
        state = self.load("Multilingual-Example.cdd")
        base = state["variants"][0]["id"]
        for kind in ("did", "data_type", "unsupported", "state"):
            with self.subTest(kind=kind):
                for row in self.app.query_objects(kind, base, page_size=1000)["rows"]:
                    self.assertTrue(details_text(self.app.details(row["id"])))

    def test_multilingual_names_preserved(self):
        state = self.load("Multilingual-Example.cdd")
        base = state["variants"][0]["id"]
        did = self.app.query_objects("did", base)["rows"][0]["id"]
        locales = {t["locale"] for t in self.app.details(did)["texts"]}
        self.assertTrue({"en-US", "de-DE", "fr-FR", "ja"} <= locales, locales)

    def test_import_is_not_undoable_to_empty_project(self):
        state = self.load("Multilingual-Example.cdd")
        self.assertFalse(state["undo"])
        self.assertTrue(state["dirty"])

    def test_save_and_reopen(self):
        state = self.load("ABS_ESP-Example.cdd")
        target = self.out / "abs.diagproj"
        saved = self.app.save_project(str(target))
        self.assertTrue(target.is_file())
        self.assertFalse(saved["dirty"])
        self.app.close_project()
        other = Application()
        try:
            reopened = other.open_project(str(target))
            self.assertEqual(reopened["counts"], state["counts"])
            self.assertFalse(reopened["dirty"])
        finally:
            other.close_project()

    def test_save_twice_creates_backup(self):
        self.load("Multilingual-Example.cdd")
        target = self.out / "ml.diagproj"
        self.app.save_project(str(target))
        self.app.save_project(str(target))
        self.assertTrue(target.with_name(target.name + ".bak").is_file())


class CddLinearTest(unittest.TestCase):
    """LINCOMP 해석. 기대값은 CANdelaStudio 13 예제의 ODXEnvelope 출력(Vector 도구 결과)에서 가져왔다."""
    U8 = {"encoding": "unsigned", "bit_length": 8}

    def lin(self, comp, t=None):
        return cdd_linear(ET.fromstring(f"<LINCOMP>{comp}</LINCOMP>"), t or self.U8)

    def test_divisor_scales_factor_only(self):
        self.assertEqual(self.lin('<COMP f="1" div="16" o="0"/>')[:2], ("0.0625", "0"))    # 0107 Voltage_1_Byte
        self.assertEqual(self.lin('<COMP f="1" div="10" o="-13"/>')[:2], ("0.1", "-13"))   # 010B Acceleration

    def test_inexact_divisor_is_kept_as_fraction_not_rounded(self):
        # 근거: 도움말 110_ODXExport "Linear: Export of Denominator ... only if it differs from 1"
        self.assertEqual(self.lin('<COMP f="359" div="255" o="0"/>')[:2], ("359/255", "0"))
        self.assertEqual(self.lin('<COMP f="2" div="6" o="1.5"/>')[:2], ("1/3", "1.5"))

    def test_range_limits(self):
        self.assertEqual(self.lin('<COMP f="35" o="0" s="0"/>'), ("35", "0", None, None))   # 010A RPM_1_Byte: 전체 범위 = 제한 없음
        self.assertEqual(self.lin('<COMP f="1" o="0" s="0" e="100"/>'), ("1", "0", None, "100"))  # 0100 Percent_1_Byte
        self.assertEqual(self.lin('<COMP f="1" o="0" e="730"/>', {"encoding": "unsigned", "bit_length": 16}), ("1", "0", None, "730"))  # 0112 Time_2_Byte
        with self.assertRaises(DomainError): self.lin('<COMP f="1" o="0" e="300"/>')  # 8비트 범위 밖

    def test_unknown_attribute_and_multiple_comps(self):
        with self.assertRaises(DomainError): self.lin('<COMP f="1" o="0" x="1"/>')
        with self.assertRaises(DomainError): self.lin('<COMP f="1" o="0"/><COMP f="2" o="0"/>')
        with self.assertRaises(DomainError): self.lin('<COMP f="1" div="0" o="0"/>')


class CddTypeFidelityTest(unittest.TestCase):
    def setUp(self):
        path = EXAMPLES / "ABS_ESP-Example.cdd"
        if not path.is_file(): self.skipTest(f"예제 CDD 없음: {path}")
        self.preview = cdd_preview(path)

    def test_linear_types_match_vector_odx_export(self):
        types = {t["qualifier"]: t for t in self.preview["types"]}
        self.assertEqual((types["Voltage_1_Byte"]["factor_text"], types["Voltage_1_Byte"]["offset_text"]), ("0.0625", "0"))
        self.assertEqual((types["Acceleration"]["factor_text"], types["Acceleration"]["offset_text"]), ("0.1", "-13"))
        self.assertEqual(types["RPM_1_Byte"]["factor_text"], "35")

    def test_ranges_and_exclusions_are_imported(self):
        types = {t["qualifier"]: t for t in self.preview["types"]}
        self.assertEqual((types["Percent_1_Byte"]["raw_lower"], types["Percent_1_Byte"]["raw_upper"]), (None, "100"))
        self.assertEqual(types["Time_2_Byte"]["raw_upper"], "730")
        self.assertEqual([(e["lower_raw"], e["upper_raw"], e["kind"]) for e in types["Percent_1_Byte"]["exclusions"]], [("101", "255", "undef")])
        self.assertEqual({e["kind"] for e in types["Lambda_rangeability_1_Byte"]["exclusions"]}, {"invalidSignal"})

    def test_inexact_coefficient_is_exact_fraction(self):
        from diagstudio.domain import convert
        position = next(t for t in self.preview["types"] if t["qualifier"] == "Position_1_Byte")
        self.assertEqual(position["factor_text"], "359/255")
        self.assertEqual(convert(position, "255"), ("255", "359"))
        self.assertEqual(convert(position, "1")[1], "1.40784313725…")          # 표시만 근삿값
        self.assertEqual(convert(position, "359/255", physical=True)[0], "1")    # 역변환은 정확한 분수
        with self.assertRaises(DomainError): convert(position, "1.40784", physical=True)  # 반올림하지 않는다
        self.assertNotIn("유한 소수", " ".join(n["reason_code"] for n in self.preview["unsupported"]))


class NewProjectSaveTest(unittest.TestCase):
    """예제 CDD 없이도 실행되는 저장 회귀 테스트(Windows fsync OSError)."""

    def test_new_project_save(self):
        app = Application()
        out = Path(tempfile.mkdtemp(prefix="diagauthor-out-"))
        try:
            app.new_project("Smoke ECU", "SmokeECU")
            result = app.save_project(str(out / "smoke.diagproj"))
            self.assertTrue(Path(result["saved_path"]).is_file())
            self.assertFalse(result["dirty"])
        finally:
            app.close_project()
            shutil.rmtree(out, ignore_errors=True)

    def test_no_recoveries_left_after_close(self):
        app = Application()
        app.new_project("Temp ECU", "TempECU")
        app.close_project()
        self.assertEqual([r for r in Session.recoveries() if r.get("source") is None and r.get("status") == "active"], [])

    def test_open_non_database_file_is_invalid_project(self):
        app = Application()
        out = Path(tempfile.mkdtemp(prefix="diagauthor-out-"))
        try:
            for name in ("broken.cddt", "broken.diagproj"):
                with self.subTest(name=name):
                    path = out / name
                    path.write_text("not a sqlite database", encoding="utf-8")
                    with self.assertRaises(DomainError) as caught:
                        app.open_project(str(path))
                    self.assertEqual(caught.exception.code, "INVALID_PROJECT")
                    self.assertIn("프로젝트 파일이 아닙니다", str(caught.exception))
        finally:
            app.close_project()
            shutil.rmtree(out, ignore_errors=True)


class CddtTemplateTest(unittest.TestCase):
    """CDDT 문서 템플릿으로 새 프로젝트 만들기. CANdelaStudio 13 설치 예제 템플릿을 쓴다(저장소에 복사하지 않음)."""

    def load(self, name):
        path = EXAMPLES / name
        if not path.is_file(): self.skipTest(f"예제 CDDT 없음: {path}")
        return cdd_preview(path)

    def test_uds_template_creates_project(self):
        preview = self.load("Vector_UDS_13.0.cddt")
        self.assertEqual((preview["document_type"], preview["version"]), ("CDDT", "13.0.100"))
        self.assertEqual(len(preview["variants"]), 1)
        self.assertTrue(preview["variants"][0]["base"])
        app = Application()
        try:
            state = app.apply_cdd(preview)
            r = app.r
            self.assertEqual(r.one("SELECT format_version FROM source_files")["format_version"], "13.0.100 CDDT")
            ecu = r.one("SELECT id FROM ecu")["id"]
            attrs = {a["key"]: a["value_text"] for a in r.all("SELECT key,value_text FROM object_attributes WHERE object_id=?", (ecu,))}
            self.assertEqual(attrs["cdd.document_type"], "CDDT")
            self.assertEqual(attrs["cdd.template_oid"], "{1DCA0988-4B31-4443-A70D-5056F6DD703B}")
            self.assertGreaterEqual(state["counts"]["service"], 50)
            self.assertEqual(r.one("SELECT count(*) n FROM protocol_services")["n"], 106)
            # 서비스 Semantic 속성(도움말 80_ODXExport)과 타입 사용자 속성, DTC의 부호 없는 레코드 항목
            self.assertEqual(r.one("SELECT value_text FROM object_attributes WHERE key='cdd.semantic'")["value_text"], "FAULTREAD")
            self.assertTrue(r.one("SELECT 1 FROM object_attributes a JOIN data_types t ON t.id=a.object_id WHERE a.key LIKE 'cdd.%'"))
            self.assertTrue(r.one("SELECT 1 FROM object_attributes a JOIN dtcs d ON d.id=a.object_id WHERE a.key='cdd.DTC_PRIORITY_VALUE'"))
            self.assertEqual({i["rule_id"] for i in app.validate_project()["issues"]} - {"WRN-002"}, {"VAL-012", "INF-001"})  # 30차 WRN-002: MUX 기본 구조(비차단)
            self.assertNotIn("데이터 타입 확장 항목 미지원: ENUM", {n["reason_code"] for n in preview["unsupported"]})
        finally:
            app.close_project()

    def test_other_templates_import(self):
        for name in ("Vector_KWPonCAN_1.3.cddt", "Vector_UDSonLIN_1.1.cddt"):
            with self.subTest(name=name):
                preview = self.load(name)
                self.assertEqual((preview["document_type"], preview["version"]), ("CDDT", "13.0.101"))
                app = Application()
                try:
                    app.apply_cdd(preview)
                    self.assertIsNone(app.r.db.execute("PRAGMA foreign_key_check").fetchone())
                finally:
                    app.close_project()


class OlderFormatTest(unittest.TestCase):
    """CANoe 13 예제 CDD: CANdela 형식 5.0.100~13.0.103. 같은 해석 경로로 열리고, 모르는 요소는 보존한다."""
    FILES = {  # 상대 경로: (형식 버전, 최소 서비스 수)
        "CANSystemDemo/CDD/CANSystem.cdd": ("5.0.100", 3),
        "CANSystemDemo/CDD/CANSystemDoor.cdd": ("6.0.120", 3),
        "Diagnostics/UDSBasic/Cdd/UDS-ExampleEcu-5.0.4.cdd": ("12.0.104", 85),  # CONTENTCOMP·가변 바이트 필드 포함(17차)
        "Diagnostics/UDSSystem/CDD/UDS-ExampleEcu-5.1.0.cdd": ("13.0.103", 90),
        "TestFeatureSet/CentralLockingSystem/DBs/SampleUDS.cdd": ("12.0.104", 20),
        "TestFeatureSet/SeatTest/DBs/kwp2000-seat.cdd": ("5.0.111", 10),
    }

    def test_samples_open(self):
        found = 0
        for rel, (version, services) in self.FILES.items():
            path = CANOE_SAMPLES / rel
            if not path.is_file(): continue
            found += 1
            with self.subTest(rel=rel):
                preview = cdd_preview(path)
                self.assertEqual(preview["version"], version)
                app = Application()
                try:
                    state = app.apply_cdd(preview)
                    self.assertGreaterEqual(state["counts"]["service"], services)
                    self.assertIsNone(app.r.db.execute("PRAGMA foreign_key_check").fetchone())
                    self.assertLessEqual({i["rule_id"] for i in app.validate_project()["issues"]}, {"VAL-012", "INF-001", "WRN-001", "WRN-002"})  # 29·30차 WRN-001 수식 타입, WRN-002 MUX 기본 구조(비차단)
                    ecu = app.r.one("SELECT id FROM ecu")["id"]
                    self.assertEqual(app.r.one("SELECT value_text FROM object_attributes WHERE object_id=? AND key='cdd.dtd_version'", (ecu,))["value_text"], version)
                finally:
                    app.close_project()
        if not found: self.skipTest(f"CANoe 예제 없음: {CANOE_SAMPLES}")

    def test_deactivated_services_are_disabled(self):
        # SERVICE@act="0" = 인스턴스에서 비활성화(도움말 80_ODXExport "Deactivated Services ... not exported").
        path = CANOE_SAMPLES / "Diagnostics/UDSSystem/CDD/UDS-ExampleEcu-5.1.0.cdd"
        if not path.is_file(): self.skipTest(f"CANoe 예제 없음: {path}")
        app = Application()
        try:
            app.apply_cdd(cdd_preview(path))
            self.assertGreater(app.r.one("SELECT count(*) n FROM services WHERE enabled=0")["n"], 0)
        finally:
            app.close_project()

    def test_version_5_static_component_bit_length(self):
        path = CANOE_SAMPLES / "TestFeatureSet/SeatTest/DBs/kwp2000-seat.cdd"
        if not path.is_file(): self.skipTest(f"CANoe 예제 없음: {path}")
        reasons = {n["reason_code"] for n in cdd_preview(path)["unsupported"]}
        self.assertNotIn("서비스 미지원: 정적 구성요소의 타입 길이를 알 수 없음", reasons)


class VectorCorpusTest(unittest.TestCase):
    """Vector 공용 문서 폴더의 모든 CDD/CDDT(CANoe 예제 17개·CANdelaStudio 예제 5개, 중복 제외 18개)를 가져오고 저장·다시 열기."""

    def test_every_document_opens(self):
        self.check_folder(VECTOR_PUBLIC, refusal_allowed=False)

    def test_extra_documents_open(self):
        for folder in EXTRA_CDD_DIRS: self.check_folder(folder, refusal_allowed=True)

    def check_folder(self, root, refusal_allowed):
        paths = sorted(p for p in root.rglob("*") if p.suffix.lower() in (".cdd", ".cddt") and p.is_file()) if root.is_dir() else []
        if not paths: self.skipTest(f"시험 문서 없음: {root}")
        seen = set()
        for path in paths:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in seen: continue
            seen.add(digest)
            with self.subTest(path=str(path.relative_to(root))):
                try:
                    preview = cdd_preview(path)
                except DomainError as e:
                    if refusal_allowed and e.code == "UNSUPPORTED_VERSION": continue
                    raise
                app = Application()
                out = Path(tempfile.mkdtemp(prefix="diagauthor-corpus-"))
                try:
                    app.apply_cdd(preview)
                    s = app.r.snapshot(app.locale)
                    self.assertTrue(s.name(s.rows("ecu")[0]["id"]).strip())  # 빈 번역(TUV)을 이름으로 고르지 않는다
                    self.assertIsNone(app.r.db.execute("PRAGMA foreign_key_check").fetchone())
                    self.assertLessEqual({i["rule_id"] for i in app.validate_project()["issues"]}, {"VAL-012", "INF-001", "WRN-001", "WRN-002"})  # 29·30차 WRN-001 수식 타입, WRN-002 MUX 기본 구조(비차단)
                    app.save_project(str(out / "c.diagproj")); app.close_project()
                    app = Application(); app.open_project(str(out / "c.diagproj"))
                finally:
                    app.close_project()
                    shutil.rmtree(out, ignore_errors=True)


class EmptyTranslationTest(unittest.TestCase):
    def test_empty_tuv_is_not_a_name(self):
        # IO_HIL Test.cdd: en-US TUV가 비어 있고(uptodate="0") de-DE만 값이 있다. 빈 번역을 이름으로 고르면 프로젝트 이름이 빈다.
        node = ET.fromstring('<X><NAME><TUV xml:lang="de-DE">Neues Steuergerät 1</TUV><TUV xml:lang="en-US" uptodate="0"/></NAME></X>')
        self.assertEqual(tuv(node, "NAME"), {"de-DE": "Neues Steuergerät 1"})


class TemplateComponentTest(unittest.TestCase):
    """Vector_UDS_13.0.cddt의 템플릿 고정 내용(CONTENTCOMP)·가변 바이트 필드(dest=any, maxbl 없음)."""

    @classmethod
    def setUpClass(cls):
        path = EXAMPLES / "Vector_UDS_13.0.cddt"
        if not path.is_file(): raise unittest.SkipTest(f"예제 CDDT 없음: {path}")
        cls.app = Application()
        state = cls.app.apply_cdd(cdd_preview(path))
        cls.base = next(v["id"] for v in state["variants"] if v["is_base"])
        cls.services = {r["qualifier"]: r["id"] for r in cls.app.query_objects("service", cls.base, page_size=1000)["rows"]}

    @classmethod
    def tearDownClass(cls):
        cls.app.close_project()

    def preview(self, qualifier, value_of):
        oid = self.services[qualifier]
        supplied = {f["id"]: {"value": value_of(f)} for f in self.app.preview_fields(oid)}
        return {t["kind"]: t["hex"] for t in self.app.build_telegram_preview(oid, supplied)["telegrams"]}

    def test_content_component_fields(self):
        # 0x19 01: 요청 상태 마스크, 응답 마스크 + DTCFormatIdentifier + DTCCount(CONTENTCOMP).
        hexes = self.preview("FaultMemory_ReadNumber", lambda f: "1")
        self.assertEqual((hexes["request"], hexes["positive"]), ("19 01 FF", "59 01 FF 01 00 01"))
        # 0x2C 01: 반복 항목(SourceDID·Position·Size)도 미리보기 입력 대상이다.
        hexes = self.preview("New_Dynamically_Define_Periodic_Data_Define_By_Identifier", lambda f: "1")
        self.assertEqual(hexes["request"], "2C 01 F2 01 00 01 01 01")

    def test_variable_byte_field(self):
        hexes = self.preview("Upload_Download_Transmit", lambda f: "01" if "Counter" in f["name"] else "AABBCC")
        self.assertEqual((hexes["request"], hexes["positive"]), ("36 01 AA BB CC", "76 01 AA BB CC"))
        with self.assertRaises(DomainError):  # 최소 1바이트(minbl=8)
            self.preview("Upload_Download_Transmit", lambda f: "01" if "Counter" in f["name"] else "")

    def test_variable_byte_field_odx(self):
        s = self.app.r.snapshot(self.app.locale)
        keep = self.services["Upload_Download_Transmit"]
        s.tables["services"] = [{**x, "enabled": int(x["id"] == keep)} for x in s.rows("services")]
        s.indices["services"] = {x["id"]: x for x in s.tables["services"]}
        s.tables["service_conditions"], s.tables["service_transitions"] = [], []
        root = build_odx(s, [self.base])
        types = [t for t in s.rows("data_types") if t["array_length"] is None]
        self.assertTrue(types)
        ct = root.find(f".//DATA-OBJECT-PROP[SHORT-NAME='{s.get('objects', types[0]['id'])['qualifier']}']/DIAG-CODED-TYPE")
        if ct is None: ct = next(d for d in root.iter("DIAG-CODED-TYPE") if d.get("TERMINATION"))
        self.assertEqual((ct.get("TERMINATION"), ct.get("BASE-DATA-TYPE"), ct.findtext("MIN-LENGTH")), ("END-OF-PDU", "A_BYTEFIELD", "1"))

    def test_packet_type_bitfield(self):
        # 0x28 CommunicationType = STRUCTDT(packet, 1바이트) 안의 8비트 비트필드. 도움말 110 표 30 Packet → STRUCTURE.
        # 첫 멤버(normalCommunicationMessages)가 최하위 비트다(ISO 14229-1 communicationType bit 0).
        hexes = self.preview("EnableRxAndDisableTx_Control", lambda f: "1" if f["name"] == "normalCommunicationMessages" else "0")
        self.assertEqual((hexes["request"], hexes["positive"]), ("28 01 01", "68 01"))
        hexes = self.preview("EnableRxAndDisableTx_Control", lambda f: "2" if f["name"] == "communicationTypeNetworks" else "0")
        self.assertEqual(hexes["request"], "28 01 20")

    def test_bcd_fields(self):
        # 0x22 0100 DevelopmentData: 16비트 BCD 5개(IDENT enc=bcd). 도움말 110 표 31 numBCD → BCD-P.
        hexes = self.preview("DevelopmentData_Read", lambda f: "1234")
        self.assertEqual(hexes["positive"], "62 01 00 12 34 12 34 12 34 12 34 12 34")
        with self.assertRaises(DomainError):  # 16비트 = 4자리
            self.preview("DevelopmentData_Read", lambda f: "12345")
        s = self.app.r.snapshot(self.app.locale)
        keep = self.services["DevelopmentData_Read"]
        s.tables["services"] = [{**x, "enabled": int(x["id"] == keep)} for x in s.rows("services")]
        s.indices["services"] = {x["id"]: x for x in s.tables["services"]}
        s.tables["service_conditions"], s.tables["service_transitions"] = [], []
        root = build_odx(s, [self.base])
        codings = {d.get("BASE-TYPE-ENCODING") for d in root.iter("DIAG-CODED-TYPE")}
        self.assertIn("BCD-P", codings)

    def test_variable_field_with_maximum(self):
        types = {t["qualifier"]: t for t in cdd_preview(EXAMPLES / "Vector_UDS_13.0.cddt")["types"]}
        hexdump = types["Hex_Dump_1_65536_Bytes"]  # IDENT uns qty=field minsz=1 maxsz=65536
        self.assertEqual((hexdump["encoding"], hexdump["array_length"], hexdump["min_length"], hexdump["max_length"]), ("bytes", None, 1, 65536))

    def test_fixed_byte_array_type(self):
        path = EXAMPLES / "ABS_ESP-Example.cdd"
        if not path.is_file(): self.skipTest(f"예제 CDD 없음: {path}")
        types = {t["qualifier"]: t for t in cdd_preview(path)["types"]}
        self.assertEqual((types["HexDump_5_Byte"]["encoding"], types["HexDump_5_Byte"]["array_length"]), ("bytes", 5))


class VariableFieldRuleTest(unittest.TestCase):
    """가변 바이트 필드(schema 9)는 전문의 마지막 필드여야 한다(VAL-005)."""

    def test_variable_field_must_be_last(self):
        app = Application()
        try:
            state = app.new_project("Var ECU", "VarECU")
            base = state["variants"][0]["id"]
            state = app.execute_command("create_type", {"name": "Tail", "qualifier": "Tail", "encoding": "bytes", "array_length": "", "min_length": 1, "conversion": "identity"}, app.r.revision)
            tail = state["selected_id"]
            u8 = app.r.one("SELECT o.id FROM objects o JOIN data_types t ON t.id=o.id WHERE o.qualifier='UInt8'")["id"]
            app.execute_command("create_did", {"variant_id": base, "name": "Ok", "qualifier": "Ok", "identifier": "0x0301",
                                               "members": [{"kind": "data", "data_type_id": u8, "name": "A", "qualifier": "A"},
                                                           {"kind": "data", "data_type_id": tail, "name": "B", "qualifier": "B"}]}, app.r.revision)
            self.assertNotIn("VAL-005", {i["rule_id"] for i in app.validate_project()["issues"]})
            app.execute_command("create_did", {"variant_id": base, "name": "Bad", "qualifier": "Bad", "identifier": "0x0302",
                                               "members": [{"kind": "data", "data_type_id": tail, "name": "B", "qualifier": "B"},
                                                           {"kind": "data", "data_type_id": u8, "name": "A", "qualifier": "A"}]}, app.r.revision)
            self.assertTrue(any(i["rule_id"] == "VAL-005" and "가변 길이" in i["message"] for i in app.validate_project()["issues"]))
        finally:
            app.close_project()

class CddRefusalTest(unittest.TestCase):
    """문서 종류·버전 판정. 합성 XML만 쓰고 예제 파일에 의존하지 않는다.
    CDD(doctype=inst)와 CDDT(doctype=tmpl)는 받고, 다른 버전·종류와 확장자 불일치는 거부한다."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="diagauthor-cddt-"))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, xml):
        path = self.dir / name
        path.write_text(xml, encoding="utf-8")
        return path

    def refusal(self, path):
        with self.assertRaises(DomainError) as caught:
            cdd_preview(path)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_VERSION")
        return str(caught.exception)

    def passes_gate(self, path):
        """문서 판정을 통과하면 이후 단계(ECU 정의 없음)에서 다른 코드로 실패한다."""
        with self.assertRaises(DomainError) as caught:
            cdd_preview(path)
        self.assertNotEqual(caught.exception.code, "UNSUPPORTED_VERSION", str(caught.exception))
        self.assertIn("ECU", str(caught.exception))

    def test_cddt_template_passes_gate_case_insensitively(self):
        xml = '<CANDELA dtdvers="13.0.101"><ECUDOC doctype="tmpl"/></CANDELA>'
        for name in ("template.cddt", "template.CDDT"):
            with self.subTest(name=name): self.passes_gate(self.write(name, xml))

    def test_dtdvers_13_0_100_is_accepted(self):
        # Vector_UDS_13.0.cddt(CANdelaStudio 13 설치 예제)는 13.0.100이다.
        self.passes_gate(self.write("uds.cddt", '<CANDELA dtdvers="13.0.100"><ECUDOC doctype="tmpl"/></CANDELA>'))

    def test_dtdvers_outside_known_range_is_refused(self):
        for version in ("1.9.0", "14.0.100", "13"):
            with self.subTest(version=version):
                message = self.refusal(self.write("doc.cdd", f'<CANDELA dtdvers="{version}"><ECUDOC doctype="inst"/></CANDELA>'))
                self.assertIn(version, message)

    def test_older_known_versions_pass_gate(self):
        for version in ("2.0.5", "4.2.0", "5.0.100", "6.0.120", "12.0.104", "13.0.103"):
            with self.subTest(version=version):
                self.passes_gate(self.write("doc.cdd", f'<CANDELA dtdvers="{version}"><ECUDOC doctype="inst"/></CANDELA>'))

    def test_unknown_doctype_is_refused(self):
        message = self.refusal(self.write("odd.cdd", '<CANDELA dtdvers="13.0.101"><ECUDOC doctype="lib"/></CANDELA>'))
        self.assertIn("doctype=lib", message)

    def test_suffix_must_match_document_kind(self):
        self.assertIn("문서 종류(CDDT)", self.refusal(self.write("template.cdd", '<CANDELA dtdvers="13.0.101"><ECUDOC doctype="tmpl"/></CANDELA>')))
        self.assertIn("문서 종류(CDD)", self.refusal(self.write("ecu.cddt", '<CANDELA dtdvers="13.0.101"><ECUDOC doctype="inst"/></CANDELA>')))

    def test_supported_instance_header_passes_refusal_gate(self):
        path = self.write("instance.cdd", '<CANDELA dtdvers="13.0.101"><ECUDOC doctype="inst"/></CANDELA>')
        with self.assertRaises(DomainError) as caught:
            cdd_preview(path)
        self.assertNotEqual(caught.exception.code, "UNSUPPORTED_VERSION")
        self.assertIn("ECU", str(caught.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
