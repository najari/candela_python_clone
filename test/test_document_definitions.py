"""27차: 보존 항목이던 문서 수준 정의의 해석 — 변형 식별 패턴(ECU-VARIANT-PATTERN), 미지원 서비스 부정 응답(GLOBAL-NEG-RESPONSE),
DTC 레코드 풀, DTC 상태 마스크 — 과 보존 항목 목록 구분.

근거(저장소에 복사하지 않고 설치 위치에서 읽는다):
- CANdela 13 예제 ABS_ESP-Example.cdd, Repository/ODXEnvelope(DTC-DOP 24비트·HEX).
- CANoe 13 예제 UDS-ExampleEcu-5.0.4.cdd(Door) ↔ 같은 문서의 Vector 출력 UDS-ExampleEcu-5.0.2.pdx:
  ECU-VARIANT-PATTERN(EXPECTED-VALUE "Developer ECU", DIAG-COMM-SNREF EcuIdentification_Read, OUT-PARAM-IF-SNREF Part_Number),
  GLOBAL-NEG-RESPONSE UNSUPPORTED_SERVICE_NR(7F · SID · NRC 17, 18, 49). 아래 기대값은 그 Vector 출력에서 옮긴 값이다.
"""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_PROFILE = tempfile.mkdtemp(prefix="diagauthor-docdefs-")
os.environ["LOCALAPPDATA"] = _PROFILE
EXAMPLES = Path(os.environ.get("DIAGAUTHOR_CDD_EXAMPLES", r"C:\Users\Public\Documents\Vector\CANdelaStudio\13.0\Examples"))
ABS = EXAMPLES / "ABS_ESP-Example.cdd"
CANOE_SAMPLES = Path(os.environ.get("DIAGAUTHOR_CANOE_SAMPLES", r"C:\Users\Public\Documents\Vector\CANoe\Sample Configurations 13.0.172\CAN"))
DOOR = CANOE_SAMPLES / "Diagnostics" / "UDSBasic" / "Cdd" / "UDS-ExampleEcu-5.0.4.cdd"

from lxml import etree as ET  # noqa: E402

from diagstudio.application import Application  # noqa: E402
from diagstudio.domain import DomainError  # noqa: E402
from diagstudio.export_scope import validate_export, variant_pattern_params  # noqa: E402
from diagstudio import interchange  # noqa: E402
from diagstudio.interchange import build_odx, cdd_preview  # noqa: E402

TAGS = ("IDENTIFICATIONSET", "IDENTIFICATIONFEATURESET", "UNSUPPSRVNEG", "DTCSTATUSMASK", "RECORDDTPOOL")


def tearDownModule():
    shutil.rmtree(_PROFILE, ignore_errors=True)


def xml(text):
    return ET.fromstring(text)


class DefinitionRulesTest(unittest.TestCase):
    """해석 규칙: Vector 출력과 같은 모양일 때만 해석하고, 다르면 이유를 남겨 차단을 유지한다."""

    DOC = """<ECUDOC><DATATYPES><IDENT id="asc"><CVALUETYPE enc="asc" bl="8"/></IDENT><IDENT id="num"><CVALUETYPE enc="uns" bl="8"/></IDENT></DATATYPES>
      <ECU><VAR id="v1" base="1"><QUAL>Base</QUAL><DIAGCLASS><QUAL>Ident</QUAL><DIAGINST><QUAL>Info</QUAL><SERVICE id="svc"><QUAL>Read</QUAL></SERVICE></DIAGINST></DIAGCLASS></VAR></ECU>
      <IDENTIFICATIONFEATURESET usage="variant">
        <IDENTIFICATIONFEATURE id="f1" dtref="asc" dataObjectPath="qpath:/Base/Ident/Info/Data/PartNo" diagCommPath="qpath:/Base/Ident/Info/[SERVICE]Read"/>
        <IDENTIFICATIONFEATURE id="f2" dtref="num" dataObjectPath="qpath:/Base/Ident/Info/Data/Number" diagCommPath="qpath:/Base/Ident/Info/[SERVICE]Read"/>
        <IDENTIFICATIONFEATURE id="f3" dtref="asc" dataObjectPath="qpath:/Base/Ident/Info/Data/Other" diagCommPath="qpath:/Base/Ident/Other/[SERVICE]Read"/>
      </IDENTIFICATIONFEATURESET></ECUDOC>"""

    def test_identification_features_and_patterns(self):
        doc = xml(self.DOC)
        ids = {e.get("id"): e for e in doc.iter() if e.get("id")}
        features, errors = interchange.identification_features(doc, ids, {"svc"})
        self.assertEqual(features, {"f1": {"service": "svc", "parameter": "PartNo"}})
        self.assertIn("ASCII", errors["f2"])  # 숫자 타입은 근거가 없어 해석하지 않는다
        self.assertIn("찾지 못", errors["f3"])
        _, dropped = interchange.identification_features(doc, ids, set())  # 가져오지 못한 서비스
        self.assertIn("f1", dropped)
        node = xml('<IDENTIFICATIONSET><IDENTIFICATION><IDENTIFICATIONDETAIL idref="f1" v="(48,46,49)"/></IDENTIFICATION>'
                   '<IDENTIFICATION><IDENTIFICATIONDETAIL idref="f1" v="(50)"/></IDENTIFICATION></IDENTIFICATIONSET>')
        self.assertEqual(interchange.identification_patterns(node, features, errors),
                         [[{"service": "svc", "parameter": "PartNo", "expected": "0.1"}], [{"service": "svc", "parameter": "PartNo", "expected": "2"}]])
        for bad in ('<IDENTIFICATIONSET><IDENTIFICATION><IDENTIFICATIONDETAIL idref="f2" v="(1)"/></IDENTIFICATION></IDENTIFICATIONSET>',
                    '<IDENTIFICATIONSET><IDENTIFICATION><IDENTIFICATIONDETAIL idref="f1" v="(0,255)"/></IDENTIFICATION></IDENTIFICATIONSET>',
                    '<IDENTIFICATIONSET><IDENTIFICATION/></IDENTIFICATIONSET>', '<IDENTIFICATIONSET/>'):
            with self.assertRaises(DomainError): interchange.identification_patterns(xml(bad), features, errors)
        with self.assertRaises(DomainError): interchange._feature_set(doc, ids, features=features, feature_errors=errors)

    def test_unsupported_service_negative_response_shape(self):
        ids = {"n17": xml('<NEGRESCODE id="n17" v="17"/>'), "n18": xml('<NEGRESCODE id="n18" v="18"/>')}
        good = ('<UNSUPPSRVNEG><QUAL>U</QUAL><CONSTCOMP spec="sid" bl="8" v="127"/><STATICCOMP spec="id"/><CONTENTCOMP><SIMPLECOMPCONT><SPECDATAOBJ>'
                '<NEGRESCODEPROXIES><NEGRESCODEPROXY idref="n17"/><NEGRESCODEPROXY idref="n18"/></NEGRESCODEPROXIES></SPECDATAOBJ></SIMPLECOMPCONT></CONTENTCOMP></UNSUPPSRVNEG>')
        out = []
        self.assertIn("GLOBAL-NEG-RESPONSE", interchange._unsupported_service_nrcs(xml(good), ids, out=out))
        self.assertEqual(out, ["n17", "n18"])
        for bad in (good.replace('v="127"', 'v="126"'), good.replace('spec="id"', 'spec="sid"'), good.replace('idref="n18"', 'idref="x"'),
                    good.replace("<STATICCOMP spec=\"id\"/>", "")):
            with self.assertRaises(DomainError): interchange._unsupported_service_nrcs(xml(bad), ids, out=[])

    def test_record_pool_and_status_mask(self):
        pool = ('<RECORDDTPOOL><RECORDDT rtSpec="faultMemory"><QUAL>OBDRecordDataType</QUAL><CVALUETYPE enc="uns" bl="24" df="hex"/>'
                '<RECORD v="4202513"/></RECORDDT><RECORDDT rtSpec="obdFaultMemory"><QUAL>RecordDataType</QUAL><CVALUETYPE enc="uns" bl="16"/></RECORDDT></RECORDDTPOOL>')
        self.assertIn("DTC-DOP", interchange._record_pool(xml(pool), {}, dtc_codes={0x402011}))
        with self.assertRaises(DomainError): interchange._record_pool(xml(pool), {}, dtc_codes=set())  # 가져오지 않은 DTC
        with self.assertRaises(DomainError): interchange._record_pool(xml(pool.replace('bl="24"', 'bl="16"')), {}, dtc_codes={0x402011})
        with self.assertRaises(DomainError):  # OBD 레코드에 DTC가 있으면 가져오지 않았으므로 차단
            interchange._record_pool(xml(pool.replace('<CVALUETYPE enc="uns" bl="16"/>', '<CVALUETYPE enc="uns" bl="16"/><RECORD v="1"/>')), {}, dtc_codes={0x402011})
        mask = '<DTCSTATUSMASK dtref="t"><DTCSTATUSBITGROUP conv="optno" dtref="b0"/><DTCSTATUSBITGROUP conv="optyes" dtref="b1"/></DTCSTATUSMASK>'
        self.assertIn("출력 영향 없음", interchange._dtc_status_mask(xml(mask), {}))
        with self.assertRaises(DomainError): interchange._dtc_status_mask(xml(mask.replace('dtref="b1"', 'dtref="b1" extra="1"')), {})


class ExportTest(unittest.TestCase):
    """ODX 출력: 변형 식별 패턴 → ECU-VARIANT-PATTERN, 미지원 서비스 NRC → GLOBAL-NEG-RESPONSE(Vector 출력과 같은 모양)."""

    def setUp(self):
        self.app = Application()
        state = self.app.new_project("Pattern ECU", "PatternECU")
        self.base = state["variants"][0]["id"]
        run = lambda action, payload: self.app.execute_command(action, payload, self.app.r.revision)
        text = run("create_type", {"name": "Part", "qualifier": "Part_T", "encoding": "ascii", "bit_length": 8, "array_length": 4, "conversion": "identity"})["selected_id"]
        run("create_did", {"variant_id": self.base, "name": "Ident", "qualifier": "Ident", "identifier": "0xF187",
                           "members": [{"kind": "data", "data_type_id": text, "name": "Part", "qualifier": "Part_Number"}]})
        self.variant = run("create_variant", {"name": "Dev", "qualifier": "Dev", "source_id": self.base})["selected_id"]
        self.service = self.app.r.one("SELECT s.id FROM services s JOIN objects o ON o.id=s.id WHERE o.qualifier='Ident_Read'")["id"]
        r = self.app.r
        with r.command("테스트 자료"):
            for code, name in ((0x11, "Service not supported"), (0x7F, "Service not supported in active session")):
                oid = self.app._object("nrc", f"NRC_{code:02X}", name)
                r.insert("nrcs", id=oid, code=code)
                r.insert("unsupported_service_nrcs", nrc_id=oid, ordinal=code)
            r.insert("variant_patterns", variant_id=self.variant, pattern=1, ordinal=1, service_id=self.service, parameter="Part_Number", expected_value="DEV1")
            r.insert("variant_patterns", variant_id=self.variant, pattern=2, ordinal=1, service_id=self.service, parameter="Part_Number", expected_value="DEV2")

    def tearDown(self):
        self.app.close_project()

    def test_pattern_and_global_negative_response(self):
        s = self.app.r.snapshot(self.app.locale, include_sources=True)
        self.assertEqual(variant_pattern_params(s, self.variant), [[("Ident_Read", "P_2", "DEV1")], [("Ident_Read", "P_2", "DEV2")]])
        self.assertEqual(variant_pattern_params(s, self.base), [])
        self.assertFalse([i for i in validate_export(s, [self.variant])["issues"] if i["field"] == "patterns"])
        root = build_odx(s, [self.variant])
        layer = root.find(".//ECU-VARIANT")
        tags = [c.tag for c in layer]
        self.assertEqual(tags[-1], "ECU-VARIANT-PATTERNS")  # DIAG-LAYER 요소 뒤
        self.assertLess(tags.index("GLOBAL-NEG-RESPONSES"), len(tags) - 1)
        matching = layer.findall("ECU-VARIANT-PATTERNS/ECU-VARIANT-PATTERN/MATCHING-PARAMETERS/MATCHING-PARAMETER")
        self.assertEqual([(m.findtext("EXPECTED-VALUE"), m.find("DIAG-COMM-SNREF").get("SHORT-NAME"), m.find("OUT-PARAM-IF-SNREF").get("SHORT-NAME")) for m in matching],
                         [("DEV1", "Ident_Read", "P_2"), ("DEV2", "Ident_Read", "P_2")])
        # 참조한 서비스·응답 파라미터가 같은 층에 실제로 있다.
        service = next(d for d in layer.iter("DIAG-SERVICE") if d.findtext("SHORT-NAME") == "Ident_Read")
        response = layer.find(f"POS-RESPONSES/POS-RESPONSE[@ID='{service.find('POS-RESPONSE-REFS/POS-RESPONSE-REF').get('ID-REF')}']")
        self.assertIn("P_2", [p.findtext("SHORT-NAME") for p in response.iter("PARAM")])
        glob = layer.find("GLOBAL-NEG-RESPONSES/GLOBAL-NEG-RESPONSE")
        params = glob.findall("PARAMS/PARAM")
        xsi = "{http://www.w3.org/2001/XMLSchema-instance}type"
        self.assertEqual([(p.get(xsi), p.findtext("SHORT-NAME"), p.findtext("BYTE-POSITION")) for p in params],
                         [("CODED-CONST", "SID_NR", "0"), ("MATCHING-REQUEST-PARAM", "SID_RQ_NR", "1"), ("VALUE", "NRC", "2")])
        self.assertEqual((params[0].get("SEMANTIC"), params[0].findtext("CODED-VALUE"), params[1].findtext("REQUEST-BYTE-POS"), params[1].findtext("BYTE-LENGTH")),
                         ("SERVICE-ID", "127", "0", "1"))
        dop = layer.find(f".//DATA-OBJECT-PROP[@ID='{params[2].find('DOP-REF').get('ID-REF')}']")
        self.assertEqual(dop.findtext("COMPU-METHOD/CATEGORY"), "TEXTTABLE")
        self.assertEqual([(s_.findtext("LOWER-LIMIT"), s_.findtext("COMPU-CONST/VT")) for s_ in dop.iter("COMPU-SCALE")],
                         [("17", "Service not supported"), ("127", "Service not supported in active session")])
        self.assertEqual([(c.findtext("LOWER-LIMIT"), c.findtext("UPPER-LIMIT")) for c in dop.iter("SCALE-CONSTR")], [("0", "16"), ("18", "126"), ("128", "255")])

    def test_unresolvable_pattern_blocks_export(self):
        self.app.execute_command("update_common", {"id": self.service, "qualifier": "Renamed_Read"}, self.app.r.revision)
        s = self.app.r.snapshot(self.app.locale, include_sources=True)
        self.assertEqual(variant_pattern_params(s, self.variant)[0][0][0], "Renamed_Read")  # 이름을 바꿔도 같은 서비스
        self.app.r.db.execute("UPDATE variant_patterns SET parameter='Missing'")
        s = self.app.r.snapshot(self.app.locale, include_sources=True)
        issues = [i for i in validate_export(s, [self.variant])["issues"] if i["field"] == "patterns"]
        self.assertTrue(issues and issues[0]["blocks_export"])
        with self.assertRaises(DomainError): build_odx(s, [self.variant])

    def test_service_used_by_pattern_cannot_be_deleted_silently(self):
        impact = self.app.deletion_impact(self.service, self.base)
        self.assertFalse(impact["can_delete"])


@unittest.skipUnless(ABS.is_file(), f"예제 CDD 없음: {ABS}")
class AbsImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Application()
        state = cls.app.apply_cdd(cdd_preview(str(ABS)))
        cls.names = {v["id"]: v["name"] for v in state["variants"]}

    @classmethod
    def tearDownClass(cls):
        cls.app.close_project()

    def test_patterns_global_nrcs_and_reclassified_nodes(self):
        r = self.app.r
        rows = r.all("SELECT p.*,o.qualifier service FROM variant_patterns p JOIN objects o ON o.id=p.service_id ORDER BY p.pattern")
        by_variant = {}
        for row in rows: by_variant.setdefault(self.names[row["variant_id"]], []).append(row["expected_value"])
        self.assertEqual(by_variant, {"A Sample": ["0.0.1", "0.0.2", "0.0.3"], "B Sample": ["0.1.0", "0.2.0"], "C Sample": ["1.0.0", "1.0.1"]})
        self.assertEqual({(row["service"], row["parameter"]) for row in rows}, {("Software_Version_Read", "SoftwareVersion")})
        self.assertEqual([x["code"] for x in r.all("SELECT n.code FROM unsupported_service_nrcs u JOIN nrcs n ON n.id=u.nrc_id ORDER BY u.ordinal")], [0x11, 0x12])
        nodes = {n["node_path"].rsplit("/", 1)[-1]: n for n in r.all("SELECT * FROM unsupported_nodes")}
        for tag in ("IDENTIFICATIONSET", "IDENTIFICATIONFEATURESET", "UNSUPPSRVNEG", "DTCSTATUSMASK", "RECORDDTPOOL"):
            self.assertEqual(nodes[tag]["semantic"], 0, tag)
            self.assertTrue(nodes[tag]["raw_xml"])  # 원문은 그대로 보존
        # 29차: EVENTDATA(도움말 ODXE-D115)·빈 Job 컨테이너(Door ↔ Vector 출력)는 비차단, 남는 차단은 MUXDT 하나(출력 범위 밖)
        self.assertEqual(nodes["EVENTDATA"]["semantic"], 0)
        self.assertTrue(nodes["EVENTDATA"]["reason_code"].startswith("출력 영향 없음"))
        jobs = [n for n in r.all("SELECT * FROM unsupported_nodes") if "/JOBCNR" in n["node_path"]]
        self.assertEqual([(n["semantic"], n["reason_code"].startswith("출력 영향 없음")) for n in jobs], [(0, True), (0, True)])
        self.assertEqual([n["node_path"].rsplit("/", 1)[-1] for n in r.all("SELECT node_path FROM unsupported_nodes WHERE semantic=1")], ["MUXDT"])
        s = r.snapshot(self.app.locale, include_sources=True)
        a_sample = next(v for v, n in self.names.items() if n == "A Sample")
        self.assertEqual(len(variant_pattern_params(s, a_sample)), 3)

    def test_preserved_list_hides_non_blocking_by_default(self):
        everything = self.app.query_objects("unsupported", page_size=0)
        blocking = self.app.query_objects("unsupported", page_size=0, blocking_only=True)
        self.assertEqual(blocking["total"], 1)
        self.assertGreater(everything["total"], blocking["total"])
        self.assertTrue(all(row["category"] == "출력 차단" for row in blocking["rows"]))
        self.assertIn("원문", blocking["page_info"]["text"])
        categories = {row["category"] for row in everything["rows"]}
        self.assertLessEqual({"출력 차단", "해석됨(원문 참조용)", "출력 영향 없음", "메타데이터·출처 정보"}, categories)
        self.assertEqual(self.app.tree()["counts"]["unsupported_blocking"], 1)


@unittest.skipUnless(DOOR.is_file(), f"CANoe 예제 CDD 없음: {DOOR}")
class DoorVectorOutputTest(unittest.TestCase):
    """UDS-ExampleEcu(Door) CDD를 가져온 결과가 같은 문서의 Vector ODX 출력(5.0.2)과 같은 식별 패턴·전역 부정 응답을 만든다."""

    def test_matches_vector_output(self):
        app = Application()
        try:
            state = app.apply_cdd(cdd_preview(str(DOOR)))
            dev = next(v["id"] for v in state["variants"] if v["name"] == "DevSample")
            row = app.r.one("SELECT p.*,o.qualifier service FROM variant_patterns p JOIN objects o ON o.id=p.service_id")
            # Vector: EXPECTED-VALUE Developer ECU, DIAG-COMM-SNREF EcuIdentification_Read, OUT-PARAM-IF-SNREF Part_Number
            self.assertEqual((row["variant_id"], row["expected_value"], row["service"], row["parameter"]), (dev, "Developer ECU", "EcuIdentification_Read", "Part_Number"))
            # Vector: NRC_DOP 17 Service not supported, 18 Subfunction not supported, 49 Request out of range
            names = [(x["code"], app._names()[x["nrc_id"]]) for x in app.r.all("SELECT u.nrc_id,n.code FROM unsupported_service_nrcs u JOIN nrcs n ON n.id=u.nrc_id ORDER BY u.ordinal")]
            self.assertEqual(names, [(17, "Service not supported"), (18, "Subfunction not supported"), (49, "Request out of range")])
        finally:
            app.close_project()


if __name__ == "__main__":
    unittest.main(verbosity=2)
