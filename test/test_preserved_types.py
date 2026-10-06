"""29차: 보존 항목 해석 — 구간 하나의 COMPTBL, bo=12 packet, FORMULADT(스키마 12), Event 데이터, 빈 Job 컨테이너,
인스턴스가 쓰지 않는 템플릿 참조. 근거는 BUILD-NOTES 29차(Vector 예제 파일은 저장소에 복사하지 않고 설치 경로에서 읽는다)."""
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagstudio import migrations, persistence  # noqa: E402
from diagstudio.application import Application  # noqa: E402
from diagstudio.domain import DomainError, check_type, convert  # noqa: E402
from diagstudio.export_scope import unused_source_types, validate_export  # noqa: E402
from diagstudio.interchange import build_odx, cdd_preview  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = Path(os.environ.get("DIAGAUTHOR_CDD_EXAMPLES", r"C:\Users\Public\Documents\Vector\CANdelaStudio\13.0\Examples"))
ABS = EXAMPLES / "ABS_ESP-Example.cdd"
CANOE_SAMPLES = Path(os.environ.get("DIAGAUTHOR_CANOE_SAMPLES", r"C:\Users\Public\Documents\Vector\CANoe\Sample Configurations 13.0.172\CAN"))
DOOR = CANOE_SAMPLES / "Diagnostics" / "UDSBasic" / "Cdd" / "UDS-ExampleEcu-5.0.4.cdd"


class Isolated(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="diag-29-")
        self.previous = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self.temp.name
        self.app = Application()

    def tearDown(self):
        if self.app.session: self.app.close_project()
        if self.previous is None: os.environ.pop("LOCALAPPDATA", None)
        else: os.environ["LOCALAPPDATA"] = self.previous
        self.temp.cleanup()

    def command(self, action, payload):
        return self.app.execute_command(action, payload, self.app.r.revision).get("selected_id")


class FormulaTypeTest(Isolated):
    """schema 12 수식 변환: 모델 규칙과 Vector 모양의 ODX 출력(IDENTICAL + DESC, 단위 없음, 물리 타입 = 부호화 타입)."""
    def setUp(self):
        super().setUp()
        self.base = self.app.new_project("ECU", "ECU")["variants"][0]["id"]

    def test_rules(self):
        base = {"encoding": "unsigned", "bit_length": 8, "conversion": "formula", "formula_expression": "x/170"}
        check_type(base)
        for bad in ({"formula_expression": " "}, {"encoding": "ascii", "array_length": 1}):
            with self.assertRaises(DomainError): check_type({**base, **bad})
        with self.assertRaises(DomainError): check_type({**base, "conversion": "curve"})
        self.assertEqual(convert(base, 51), ("51", "51"))  # 계산하지 않음 = ODX IDENTICAL
        with self.assertRaises(DomainError): convert(base, "0.3", physical=True)
        with self.assertRaises(DomainError):
            self.command("create_type", {"name": "Empty", "qualifier": "Empty", "encoding": "unsigned", "bit_length": 8, "conversion": "formula"})

    def test_odx_matches_vector_formula_dop(self):
        tid = self.command("create_type", {"name": "Engine Temperature", "qualifier": "EngineTemperature", "encoding": "unsigned",
                                           "bit_length": 8, "conversion": "formula", "unit": "K",
                                           "formula_expression": "270+510*(1-pow(e,-0.0255*x))"})
        self.command("create_did", {"variant_id": self.base, "name": "Temp", "qualifier": "Temp", "identifier": "0x0101",
                                    "members": [{"name": "Value", "qualifier": "Value", "data_type_id": tid}]})
        row = self.app.r.one("SELECT * FROM data_types WHERE id=?", (tid,))
        self.assertEqual((row["conversion"], row["formula_expression"], row["factor_text"]), ("formula", "270+510*(1-pow(e,-0.0255*x))", None))
        # 다른 속성만 고쳐도(대화상자가 수식을 보내지 않아도) 수식은 남는다.
        self.command("update_type", {"id": tid, "name": "Engine Temperature", "qualifier": "EngineTemperature", "encoding": "unsigned",
                                     "bit_length": 8, "conversion": "formula", "unit": "K"})
        self.assertEqual(self.app.r.one("SELECT formula_expression f FROM data_types WHERE id=?", (tid,))["f"], "270+510*(1-pow(e,-0.0255*x))")
        s = self.app.r.snapshot()
        root = build_odx(s, [self.base])
        dop = next(d for d in root.iter("DATA-OBJECT-PROP") if d.findtext("SHORT-NAME") == "EngineTemperature")
        self.assertEqual([c.tag for c in dop], ["SHORT-NAME", "LONG-NAME", "DESC", "COMPU-METHOD", "DIAG-CODED-TYPE", "PHYSICAL-TYPE"])
        self.assertEqual(dop.findtext("DESC/p"), "Generated from formula data type 'EngineTemperature'.\n"
                         "CodedToPhys formula: f(x)=270+510*(1-pow(e,-0.0255*x)); \nPhysToCodedFormula: f(x)=")
        self.assertEqual(dop.findtext("COMPU-METHOD/CATEGORY"), "IDENTICAL")
        self.assertIsNone(dop.find("COMPU-METHOD/COMPU-INTERNAL-TO-PHYS"))
        self.assertEqual(dop.find("PHYSICAL-TYPE").get("BASE-DATA-TYPE"), "A_UINT32")
        self.assertIsNone(root.find(".//UNIT-SPEC"))
        report = validate_export(s, [self.base])
        warning = [i for i in report["issues"] if i["rule_id"] == "WRN-001"]
        self.assertEqual([(i["object_id"], i["severity"], i["blocks_export"]) for i in warning], [(tid, "warning", False)])
        self.assertEqual(report["blocking_count"], 0)

    def test_signed_formula_uses_signed_physical_type(self):
        tid = self.command("create_type", {"name": "T", "qualifier": "T", "encoding": "signed", "bit_length": 8,
                                           "conversion": "formula", "formula_expression": "x+50"})
        self.command("create_did", {"variant_id": self.base, "name": "D", "qualifier": "D", "identifier": "0x0102",
                                    "members": [{"name": "V", "qualifier": "V", "data_type_id": tid}]})
        dop = next(d for d in build_odx(self.app.r.snapshot(), [self.base]).iter("DATA-OBJECT-PROP") if d.findtext("SHORT-NAME") == "T")
        self.assertEqual(dop.find("PHYSICAL-TYPE").get("BASE-DATA-TYPE"), "A_INT32")  # 도움말 110 표 32 sgn → A_INT32


class MigrationTo12Test(unittest.TestCase):
    def test_v11_file_keeps_types_and_accepts_formula(self):
        out = Path(tempfile.mkdtemp(prefix="diagauthor-mig12-"))
        schema, version = persistence.SCHEMA_FILE, migrations.CURRENT_VERSION
        previous = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(out)
        try:
            try:
                persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = ROOT / "test" / "fixtures" / "schema_v11.sql", 11
                old = Application(); old.new_project("V11 ECU", "V11ECU")
                old.execute_command("create_type", {"name": "Speed", "qualifier": "Speed", "encoding": "unsigned", "bit_length": 16,
                                                    "conversion": "linear", "factor_text": "0.5", "offset_text": "-3", "raw_upper": "500"}, old.r.revision)
                old.save_project(str(out / "v11.diagproj")); old.close_project()
            finally:
                persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = schema, version
            app = Application()
            state = app.open_project(str(out / "v11.diagproj"))
            self.assertEqual(state["migrated"], [12, 13])
            row = app.r.one("SELECT t.* FROM data_types t JOIN objects o ON o.id=t.id WHERE o.qualifier='Speed'")
            self.assertEqual((row["conversion"], row["factor_text"], row["offset_text"], row["raw_upper"], row["formula_expression"]),
                             ("linear", "0.5", "-3", "500", None))
            app.execute_command("create_type", {"name": "F", "qualifier": "F", "encoding": "unsigned", "bit_length": 8,
                                                "conversion": "formula", "formula_expression": "x/170"}, app.r.revision)
            columns = [c["name"] for c in app.r.all('PRAGMA table_info("data_types")')]
            app.save_project(str(out / "v12.diagproj")); app.close_project()
            fresh = Application(); fresh.new_project("New", "New")
            self.assertEqual(columns, [c["name"] for c in fresh.r.all('PRAGMA table_info("data_types")')])
            fresh.close_project()
            with closing(sqlite3.connect(out / "v12.diagproj")) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], migrations.CURRENT_VERSION)
                with self.assertRaises(sqlite3.IntegrityError):  # 새 CHECK: 수식이 없는 formula는 거부
                    db.execute("UPDATE data_types SET formula_expression=NULL WHERE conversion='formula'")
        finally:
            if previous is None: os.environ.pop("LOCALAPPDATA", None)
            else: os.environ["LOCALAPPDATA"] = previous
            shutil.rmtree(out, ignore_errors=True)


class TemplateScopeTest(unittest.TestCase):
    """인스턴스가 쓰지 않는 프로토콜·클래스 템플릿 안의 참조는 사용이 아니다. 쓰이는 템플릿의 참조는 사용이다."""
    XML = b'''<CANDELA><ECUDOC><DATATYPES>
    <MUXDT id="mux"/><IDENT id="direct"/><IDENT id="via_used"/>
    </DATATYPES>
    <PROTOCOLSERVICES>
      <PROTOCOLSERVICE id="ps_unused"><REQ><DATAOBJ dtref="mux"/></REQ></PROTOCOLSERVICE>
      <PROTOCOLSERVICE id="ps_used"><REQ><DATAOBJ dtref="via_used"/></REQ></PROTOCOLSERVICE>
    </PROTOCOLSERVICES>
    <DCLTMPLS>
      <DCLTMPL id="cls_unused"><DCLSRVTMPL id="srv_unused" tmplref="ps_unused"/><SHPROXY><PROXYCOMPREF idref="ps_unused"/></SHPROXY></DCLTMPL>
      <DCLTMPL id="cls_used"><DCLSRVTMPL id="srv_used" tmplref="ps_used"/></DCLTMPL>
    </DCLTMPLS>
    <ECU><VAR><DIAGINST><SERVICE tmplref="srv_used"/><DATAOBJ dtref="direct"/></DIAGINST></VAR></ECU>
    </ECUDOC></CANDELA>'''

    def test_unused_template_reference_is_not_use(self):
        class S:
            def rows(self, table): return [{"id": "src", "kind": "cdd", "content": TemplateScopeTest.XML}] if table == "source_files" else []
        self.assertEqual(unused_source_types(S()), {("src", "/CANDELA/ECUDOC/DATATYPES/MUXDT")})


@unittest.skipUnless(ABS.is_file(), f"예제 CDD 없음: {ABS}")
class AbsPreservedTypesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.preview = cdd_preview(ABS)
        cls.types = {t["qualifier"]: t for t in cls.preview["types"]}

    def test_single_segment_comptbl_is_linear(self):
        odo = self.types["OdometerValue"]
        self.assertEqual((odo["conversion"], odo["factor_text"], odo["offset_text"], odo["raw_lower"], odo["raw_upper"]), ("linear", "16", "0", None, "65534"))
        self.assertEqual([(e["lower_raw"], e["upper_raw"], e["kind"]) for e in odo["exclusions"]][:1], [("65535", "65535", "noSignal")])
        self.assertEqual((self.types["Remaining_Distance_1_Byte"]["factor_text"], self.types["Counter"]["factor_text"]), ("100", "1"))

    def test_formula_types(self):
        got = {q: (self.types[q]["conversion"], self.types[q]["encoding"], self.types[q]["formula_expression"])
               for q in ("Temperature_1_Byte", "Pressure_1_5bar_1_Byte", "EngineTemperature")}
        self.assertEqual(got, {"Temperature_1_Byte": ("formula", "signed", "x+50"), "Pressure_1_5bar_1_Byte": ("formula", "unsigned", "x/170"),
                               "EngineTemperature": ("formula", "unsigned", "270+510*(1-pow(e,-0.0255*x))")})

    def test_bo12_packets(self):
        env, glob = self.types["EnvironmentData"], self.types["GlobalData"]
        self.assertEqual((env["encoding"], env["packet_bits"], len(env["members"])), ("packet", 32, 3))
        self.assertEqual((glob["encoding"], glob["packet_bits"], len(glob["members"])), ("packet", 48, 4))

    def test_remaining_blocking_nodes(self):
        blocking = [u["node_path"].split("ECUDOC")[1] for u in self.preview["unsupported"] if u["semantic"]]
        self.assertEqual(blocking, ["/DATATYPES/MUXDT"])
        quiet = {u["node_path"].rsplit("/", 1)[-1]: u["reason_code"] for u in self.preview["unsupported"] if not u["semantic"]}
        self.assertTrue(quiet["EVENTDATA"].startswith("출력 영향 없음"))
        self.assertTrue(quiet["JOBCNR[1]"].startswith("출력 영향 없음") and quiet["JOBCNR[2]"].startswith("출력 영향 없음"))


@unittest.skipUnless(DOOR.is_file(), f"CANoe 예제 CDD 없음: {DOOR}")
class DoorPreservedTypesTest(unittest.TestCase):
    def test_door_packets_jobs_and_used_mux(self):
        preview = cdd_preview(DOOR)
        types = {t["qualifier"]: t for t in preview["types"]}
        # bo=12 여러 바이트 packet(Vector: STRUCTURE BYTE-SIZE 6, 위치 0/2/4)
        self.assertEqual((types["TestDatatypeStructureLevel1"]["packet_bits"], len(types["TestDatatypeStructureLevel1"]["members"])), (48, 3))
        blocking = {u["node_path"].split("ECUDOC")[1]: u["reason_code"] for u in preview["unsupported"] if u["semantic"]}
        self.assertNotIn("/DATATYPES/COMPTBL", blocking)  # 30차: 여러 구간 → 구간별 선형(SCALE-LINEAR)
        self.assertFalse(any("/JOBCNR" in p for p in blocking))  # 빈 컨테이너(Vector 출력에 SINGLE-ECU-JOB 없음)
        with tempfile.TemporaryDirectory() as folder:
            previous = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = folder
            app = Application()
            try:
                app.apply_cdd(preview)
                unused = {p.split("ECUDOC")[1] for _, p in unused_source_types(app.r.snapshot(include_sources=True))}
                # 인스턴스가 쓰는 RD·WMBA의 MUX(Vector 출력: TABLE)는 사용, 참조 없는 스냅샷 MUX는 미사용
                self.assertNotIn("/DATATYPES/MUXDT[1]", unused)
                self.assertNotIn("/DATATYPES/MUXDT[2]", unused)
                self.assertLessEqual({"/DATATYPES/MUXDT[3]", "/DATATYPES/MUXDT[4]"}, unused)
            finally:
                app.close_project()
                if previous is None: os.environ.pop("LOCALAPPDATA", None)
                else: os.environ["LOCALAPPDATA"] = previous


if __name__ == "__main__":
    unittest.main()
