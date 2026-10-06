"""30차: Vector 예제에서 근거를 찾은 보존 항목 해석 — 열거 테이블 예외값, KWP 프로토콜 표준, 쓰지 않는 Job 파일, 중첩 packet,
부동소수, 앞 길이 바이트, 구간별 선형·특성 곡선(스키마 13), DID MUX(ODX MUX)·서비스 MUX(ODX TABLE), 해결된 보존 항목 목록.
근거는 BUILD-NOTES 30차(Door CDD ↔ UDS-ExampleEcu-5.0.2.pdx). Vector 파일은 저장소에 복사하지 않고 설치 경로에서 읽는다."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diagstudio import migrations, persistence  # noqa: E402
from diagstudio.application import Application  # noqa: E402
from diagstudio.domain import DomainError, check_type, convert  # noqa: E402
from diagstudio.interchange import XSI, build_odx, cdd_preview  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CANOE_SAMPLES = Path(os.environ.get("DIAGAUTHOR_CANOE_SAMPLES", r"C:\Users\Public\Documents\Vector\CANoe\Sample Configurations 13.0.172\CAN"))
DOOR = CANOE_SAMPLES / "Diagnostics" / "UDSBasic" / "Cdd" / "UDS-ExampleEcu-5.0.4.cdd"
SEAT = CANOE_SAMPLES / "TestFeatureSet" / "SeatTest" / "DBs" / "kwp2000-seat.cdd"
TYPE = f"{{{XSI}}}type"


class Isolated:
    @classmethod
    def isolate(cls):
        cls.temp = tempfile.mkdtemp(prefix="diag-30-")
        cls.previous = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = cls.temp

    @classmethod
    def restore(cls):
        if cls.previous is None: os.environ.pop("LOCALAPPDATA", None)
        else: os.environ["LOCALAPPDATA"] = cls.previous
        shutil.rmtree(cls.temp, ignore_errors=True)


class ConversionRulesTest(unittest.TestCase):
    """스키마 13 모델 규칙: 부동소수, 구간별 선형, 특성 곡선, 앞 길이 바이트."""
    def test_float(self):
        t = {"encoding": "float", "bit_length": 32, "array_length": 1, "conversion": "linear", "factor_text": "1/10", "offset_text": "0",
             "raw_lower": "-300", "raw_upper": "3000"}
        check_type(t)
        self.assertEqual(convert(t, "-300"), ("-300", "-30"))
        self.assertEqual(convert(t, "12.5")[1], "1.25")
        self.assertEqual(convert(t, "25", physical=True)[0], "250")
        with self.assertRaises(DomainError): convert(t, "3001")
        with self.assertRaises(DomainError): check_type({**t, "bit_length": 16})
        with self.assertRaises(DomainError): check_type({**t, "conversion": "text_table", "factor_text": None, "offset_text": None})

    def test_scale_linear_and_tab_intp(self):
        segments = [{"ordinal": 0, "lower_raw": "0", "upper_raw": "16", "factor_text": "1", "offset_text": "0", "value_text": None},
                    {"ordinal": 1, "lower_raw": "17", "upper_raw": "32", "factor_text": "1.5", "offset_text": "-9", "value_text": None}]
        t = {"encoding": "unsigned", "bit_length": 8, "array_length": 1, "conversion": "scale_linear", "segments": segments}
        check_type(t, segments=segments)
        self.assertEqual(convert(t, 20)[1], "21")          # 20·1.5 − 9
        self.assertEqual(convert(t, "21", physical=True)[0], "20")
        with self.assertRaises(DomainError): convert(t, 40)  # 구간 밖
        with self.assertRaises(DomainError): check_type(t, segments=[{**segments[1], "lower_raw": "10"}, segments[0]])  # 겹침
        points = [{"ordinal": i, "lower_raw": str(x), "upper_raw": None, "factor_text": None, "offset_text": None, "value_text": str(v)}
                  for i, (x, v) in enumerate([(0, 0), (4, 2), (9, 3)])]
        curve = {"encoding": "unsigned", "bit_length": 8, "array_length": 1, "conversion": "tab_intp", "segments": points}
        check_type(curve, segments=points)
        self.assertEqual(convert(curve, 2)[1], "1")      # 0..4 사이 선형 보간
        self.assertEqual(convert(curve, 9)[1], "3")
        with self.assertRaises(DomainError): convert(curve, 10)
        with self.assertRaises(DomainError): convert(curve, "1", physical=True)
        with self.assertRaises(DomainError): check_type(curve, segments=[{**x, "ordinal": i} for i, x in enumerate(reversed(points))])

    def test_leading_length(self):
        t = {"encoding": "bytes", "bit_length": 8, "array_length": None, "min_length": 1, "max_length": 255, "conversion": "identity", "leading_length_bits": 8}
        check_type(t)
        with self.assertRaises(DomainError): check_type({**t, "max_length": 256})
        with self.assertRaises(DomainError): check_type({**t, "array_length": 4, "min_length": None, "max_length": None})


class NewProjectTelegramTest(unittest.TestCase, Isolated):
    """앞 길이 바이트 필드는 전문 가운데에 둘 수 있고, 부동소수는 IEEE 754로 인코딩된다."""
    @classmethod
    def setUpClass(cls): cls.isolate()

    @classmethod
    def tearDownClass(cls): cls.restore()

    def test_encode(self):
        app = Application()
        try:
            base = app.new_project("ECU", "ECU")["variants"][0]["id"]
            cmd = lambda a, p: app.execute_command(a, p, app.r.revision).get("selected_id")
            blob = cmd("create_type", {"name": "Blob", "qualifier": "Blob", "encoding": "bytes", "array_length": "", "min_length": 1, "max_length": 100, "leading_length_bits": "16"})
            speed = cmd("create_type", {"name": "Speed", "qualifier": "Speed", "encoding": "float", "bit_length": 32})
            tail = cmd("create_type", {"name": "Tail", "qualifier": "Tail", "encoding": "unsigned", "bit_length": 8})
            did = cmd("create_did", {"variant_id": base, "name": "D", "qualifier": "D", "identifier": "0x0200",
                                     "members": [{"name": "B", "qualifier": "B", "data_type_id": blob, "default_text": "AABB"},
                                                 {"name": "S", "qualifier": "S", "data_type_id": speed, "default_text": "1.5"},
                                                 {"name": "T", "qualifier": "T", "data_type_id": tail, "default_text": "7"}]})
            self.assertEqual(app.r.one("SELECT leading_length_bits n FROM data_types WHERE id=?", (blob,))["n"], 16)
            s = app.r.snapshot()
            data, _ = s.encode(s.get("dids", did)["structure_id"])
            self.assertEqual(data.hex(), "0002aabb" + "3fc00000" + "07")
            self.assertFalse([i for i in app.validate_project()["issues"] if i["rule_id"] == "VAL-005"])
            root = build_odx(s, [base])
            dops = {d.findtext("SHORT-NAME"): d for d in root.iter("DATA-OBJECT-PROP")}
            ct = dops["Blob"].find("DIAG-CODED-TYPE")
            self.assertEqual((ct.get(TYPE), ct.get("BASE-DATA-TYPE"), ct.findtext("BIT-LENGTH")), ("LEADING-LENGTH-INFO-TYPE", "A_BYTEFIELD", "16"))
            ct = dops["Speed"].find("DIAG-CODED-TYPE")
            self.assertEqual((ct.get("BASE-DATA-TYPE"), ct.get("BASE-TYPE-ENCODING"), ct.findtext("BIT-LENGTH")), ("A_FLOAT32", None, "32"))
        finally:
            app.close_project()


class MigrationTo13Test(unittest.TestCase):
    def test_v12_file_keeps_types(self):
        out = Path(tempfile.mkdtemp(prefix="diagauthor-mig13-"))
        schema, version = persistence.SCHEMA_FILE, migrations.CURRENT_VERSION
        previous = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(out)
        try:
            try:
                persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = ROOT / "test" / "fixtures" / "schema_v12.sql", 12
                old = Application(); old.new_project("V12 ECU", "V12ECU")
                old.execute_command("create_type", {"name": "F", "qualifier": "F", "encoding": "unsigned", "bit_length": 8,
                                                    "conversion": "formula", "formula_expression": "x/170"}, old.r.revision)
                old.save_project(str(out / "v12.diagproj")); old.close_project()
            finally:
                persistence.SCHEMA_FILE, migrations.CURRENT_VERSION = schema, version
            app = Application()
            state = app.open_project(str(out / "v12.diagproj"))
            self.assertEqual(state["migrated"], [13])
            row = app.r.one("SELECT t.* FROM data_types t JOIN objects o ON o.id=t.id WHERE o.qualifier='F'")
            self.assertEqual((row["conversion"], row["formula_expression"], row["leading_length_bits"]), ("formula", "x/170", None))
            self.assertEqual(app.r.all("SELECT * FROM conversion_segments"), [])
            columns = [c["name"] for c in app.r.all('PRAGMA table_info("data_types")')]
            app.close_project()
            fresh = Application(); fresh.new_project("New", "New")
            self.assertEqual(columns, [c["name"] for c in fresh.r.all('PRAGMA table_info("data_types")')])
            fresh.close_project()
        finally:
            if previous is None: os.environ.pop("LOCALAPPDATA", None)
            else: os.environ["LOCALAPPDATA"] = previous
            shutil.rmtree(out, ignore_errors=True)


@unittest.skipUnless(DOOR.is_file(), f"CANoe 예제 CDD 없음: {DOOR}")
class DoorVectorMappingTest(unittest.TestCase, Isolated):
    """Door CDD를 가져와 같은 문서의 Vector 출력(5.0.2 PDX)과 같은 모양으로 내는지 본다."""
    @classmethod
    def setUpClass(cls):
        cls.isolate()
        cls.preview = cdd_preview(DOOR)
        cls.app = Application()
        state = cls.app.apply_cdd(cls.preview)
        cls.base = next(v["id"] for v in state["variants"] if v["is_base"])
        cls.types = {r["qualifier"]: r for r in cls.app.r.all("SELECT t.*,o.qualifier FROM data_types t JOIN objects o ON o.id=t.id")}
        s = cls.app.r.snapshot(cls.app.locale)
        s.tables["service_conditions"], s.tables["service_transitions"] = [], []
        names = {o["id"]: o["qualifier"] for o in s.rows("objects")}
        s.tables["services"] = [{**x, "enabled": int(names[x["id"]] in ("Download_Request", "DID_MuxedTestData_Read"))} for x in s.rows("services")]
        cls.root = build_odx(s, [cls.base])
        cls.names = names

    @classmethod
    def tearDownClass(cls):
        cls.app.close_project()
        cls.restore()

    def dop(self, name):
        return next(d for d in self.root.iter("DATA-OBJECT-PROP") if d.findtext("SHORT-NAME") == name)

    def test_types_imported(self):
        t = self.types
        self.assertEqual((t["VehicleSpeed"]["encoding"], t["VehicleSpeed"]["bit_length"], t["VehicleSpeed"]["conversion"]), ("float", 32, "linear"))
        self.assertEqual(t["TestDatatypePiecewiseLinear"]["conversion"], "scale_linear")
        self.assertEqual(t["TestDatatypeCharacteristicCurve"]["conversion"], "tab_intp")
        self.assertEqual([t[f"Memory_{n}_Byte_Size_Info"]["leading_length_bits"] for n in (1, 2, 3, 4)], [8, 16, 24, 32])
        self.assertEqual(t["DTC_Significance"]["conversion"], "text_table")
        self.assertEqual(t["TestDatatypeStructureLevel2"]["encoding"], "packet")
        segments = self.app.r.all("SELECT * FROM conversion_segments WHERE data_type_id=? ORDER BY ordinal", (t["TestDatatypePiecewiseLinear"]["id"],))
        self.assertEqual([(x["lower_raw"], x["upper_raw"], x["factor_text"], x["offset_text"]) for x in segments][:2], [("0", "16", "1", "0"), ("17", "32", "1.5", "-9")])

    def test_scale_linear_tab_intp_and_texttable_exclusions(self):
        d = self.dop("TestDatatypePiecewiseLinear")
        self.assertEqual(d.findtext("COMPU-METHOD/CATEGORY"), "SCALE-LINEAR")
        scales = d.findall("COMPU-METHOD/COMPU-INTERNAL-TO-PHYS/COMPU-SCALES/COMPU-SCALE")
        self.assertEqual(len(scales), 8)
        self.assertEqual([v.text for v in scales[1].findall("COMPU-RATIONAL-COEFFS/COMPU-NUMERATOR/V")], ["-9", "1.5"])
        self.assertEqual([(c.get("VALIDITY"), c.findtext("LOWER-LIMIT"), c.findtext("UPPER-LIMIT")) for c in d.iter("SCALE-CONSTR")], [("NOT-DEFINED", "129", "255")])
        d = self.dop("TestDatatypeCharacteristicCurve")
        self.assertEqual(d.findtext("COMPU-METHOD/CATEGORY"), "TAB-INTP")
        scales = d.findall("COMPU-METHOD/COMPU-INTERNAL-TO-PHYS/COMPU-SCALES/COMPU-SCALE")
        self.assertEqual([(x.findtext("LOWER-LIMIT"), x.findtext("COMPU-CONST/V")) for x in scales][:3], [("0", "0"), ("1", "1"), ("4", "2")])
        self.assertEqual([(c.get("VALIDITY"), c.findtext("LOWER-LIMIT"), c.findtext("UPPER-LIMIT")) for c in d.iter("SCALE-CONSTR")], [("NOT-DEFINED", "101", "255")])
        d = self.dop("TestDatatypeMuxValues")
        self.assertEqual(sorted((c.get("VALIDITY"), c.findtext("LOWER-LIMIT"), c.findtext("UPPER-LIMIT")) for c in d.iter("SCALE-CONSTR")),
                         [("NOT-AVAILABLE", "5", "6"), ("NOT-DEFINED", "10", "15"), ("NOT-DEFINED", "16", "255")])

    def test_did_mux(self):
        mux = next(m for m in self.root.iter("MUX"))
        self.assertEqual((mux.findtext("BYTE-POSITION"), mux.findtext("SWITCH-KEY/BYTE-POSITION")), ("1", "0"))
        self.assertEqual(self.dop("TestDatatypeMuxValues").get("ID"), mux.find("SWITCH-KEY/DATA-OBJECT-PROP-REF").get("ID-REF"))
        self.assertEqual(mux.findtext("DEFAULT-CASE/SHORT-NAME"), "Case_Default")
        self.assertIsNone(mux.find("DEFAULT-CASE/STRUCTURE-REF"))  # 빈 기본 구조
        cases = mux.findall("CASES/CASE")
        self.assertEqual([(c.findtext("SHORT-NAME"), c.findtext("LOWER-LIMIT")) for c in cases][:3],
                         [("Case_0x0", "Mux_Identity"), ("Case_0x1", "Mux_Texttable"), ("Case_0x2", "Mux_Linear")])
        did = next(st for st in self.root.iter("STRUCTURE") if st.findtext("SHORT-NAME") == "DID_MuxedTestData")
        params = did.findall("PARAMS/PARAM")
        self.assertEqual([(p.findtext("BYTE-POSITION"), p.find("DOP-REF").get("ID-REF") == mux.get("ID")) for p in params], [("0", False), ("0", True)])

    def test_service_mux_table(self):
        request = next(r for r in self.root.iter("REQUEST") if any(p.get(TYPE) == "TABLE-KEY" for p in r.iter("PARAM")))
        params = request.findall("PARAMS/PARAM")
        self.assertEqual([(p.get(TYPE), p.findtext("BYTE-POSITION")) for p in params], [("CODED-CONST", "0"), ("VALUE", "1"), ("TABLE-KEY", "2"), ("TABLE-STRUCT", "3")])
        self.assertEqual(params[3].find("TABLE-KEY-REF").get("ID-REF"), params[2].get("ID"))
        table = next(t for t in self.root.iter("TABLE") if t.get("ID") == params[2].find("TABLE-REF").get("ID-REF"))
        rows = table.findall("TABLE-ROW")
        self.assertEqual(len(rows), 17)
        self.assertEqual((rows[0].findtext("SHORT-NAME"), rows[0].findtext("KEY")), ("TR_0x11", "Length: memoryAddress 1 byte, memorySize 1 byte"))
        issues = self.app.validate_project(variants=[self.base])["issues"]
        self.assertTrue(any(i["rule_id"] == "WRN-002" and not i["blocks_export"] for i in issues))

    def test_preserved_classification(self):
        blocking = {u["node_path"].split("ECUDOC")[1]: u["reason_code"] for u in self.preview["unsupported"] if u["semantic"]}
        quiet = {u["node_path"].split("ECUDOC")[1]: u["reason_code"] for u in self.preview["unsupported"] if not u["semantic"]}
        self.assertTrue(quiet["/JOBFILES"].startswith("출력 영향 없음"))
        self.assertTrue(quiet["/DATATYPES/MUXDT[1]"].startswith("해석함"))   # Download_Request가 쓰는 MUXDT
        self.assertTrue(quiet["/DATATYPES/MUXDT[5]"].startswith("해석함"))   # DID_MuxedTestData
        self.assertIn("/DATATYPES/MUXDT[3]", blocking)                        # 쓰는 곳 없는 스냅샷 MUXDT(출력 범위 밖)
        self.assertFalse([p for p in blocking if "/TEXTTBL" in p or "/STRUCTDT" in p or "/COMPTBL" in p or "/VALTBL" in p or "/LINCOMP" in p])

    def test_resolved_rows_in_preserved_list(self):
        page = self.app.query_objects("unsupported", page_size=0, blocking_only=True, include_resolved=True)
        resolved = [r for r in page["rows"] if r["category"] == "해결됨(가져옴)"]
        self.assertGreaterEqual(len(resolved), 17)
        self.assertTrue(all(r["kind"] == "data_type" and not str(r["id"]).startswith("unsupported:") for r in resolved))
        self.assertIn("30차", " ".join(r["name"] for r in resolved))
        self.assertTrue(any(r["category"] == "해석됨(원문 참조용)" for r in page["rows"]))  # MUXDT·변형 식별 해석
        hidden = self.app.query_objects("unsupported", page_size=0, blocking_only=True)
        self.assertTrue(all(r["category"] == "출력 차단" for r in hidden["rows"]))
        self.assertEqual(self.app.tree()["counts"]["unsupported_resolved"], len(resolved))


@unittest.skipUnless(SEAT.is_file(), f"CANoe 예제 CDD 없음: {SEAT}")
class KwpProtocolStandardTest(unittest.TestCase):
    def test_kwp_standard_is_source_information(self):
        preview = cdd_preview(SEAT)
        attrs = {a["key"]: a["value_text"] for a in preview["ecu"]["attributes"]}
        self.assertEqual(attrs["cdd.protocol_standard"], "KWP")
        node = next(n for n in preview["unsupported"] if n["node_path"].endswith("/PROTOCOLSTANDARD"))
        self.assertEqual(node["semantic"], 0)


if __name__ == "__main__":
    unittest.main()
