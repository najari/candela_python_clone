"""외부 파일을 DTO로 해석하고 고정 스냅샷으로 출력한다. DB를 변경하지 않는다."""
from pathlib import Path
import csv
import hashlib
import io
import json
import os
import re
import uuid
from decimal import Decimal, InvalidOperation, localcontext
from lxml import etree as ET

from .domain import DomainError, FLOAT_LIMIT_TEXT, convert, FLOAT_MAX, SEGMENT_CONVERSIONS, check_type, decimal_text, integer, ratio, ratio_text, raw_bounds, terminating, validate
from .cdd_model import ATTRIBUTE_TAGS, Model, parse_bitfield, user_attributes
from .export_scope import validate_export, variant_pattern_params

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


# 해석이 증명된 CDD 데이터 타입 구성 요소. 그 밖의 자식·속성은 의미를 모르므로 조용히 버리지 않고 미지원으로 보존한다.
TYPE_CHILDREN = {"NAME", "QUAL", "DESC", "CVALUETYPE", "PVALUETYPE", "COMP", "TEXTMAP", "EXCL", "FORMULA", "VALMAP"}
# CANdela DTD 버전(dtdvers = 주.부.빌드). CANdelaStudio 13은 이전 버전 문서를 열어 변환한다(도움말 Getting Started: 더 새 문서는
# Converters가 필요). 확인한 문서: 5.0.100·5.0.111·6.0.120·12.0.104·13.0.100·13.0.101·13.0.103(CANoe 13 예제·CANdela 13 예제).
# 요소·속성 이름을 비교한 결과 이전 버전도 13과 같은 어휘이고 추가된 것만 있다(STATICCOMP@bl, STATUSDTCPROXYCOMP@dtref, SERVICE@act,
# FLTDEF 등). 같은 해석 경로로 읽고, 모르는 요소는 원문으로 보존한다. 13보다 새 문서와 5 이전 문서는 확인 자료가 없어 거부한다.
# 2.x: cantools 테스트 CDD(2.0.5)의 요소·속성 어휘가 5.x~13.x와 같다(추가: UNION, RECORDITEMTMPL, 일부 id 속성). 3.x·4.x는 파일로
# 확인하지 못했지만 2.x와 5.x가 같은 어휘라 같은 경로로 읽는다. 모르는 요소는 원문 보존·출력 차단이다.
SUPPORTED_MAJOR = range(2, 14)
SUPPORTED_DTD = {"13.0.100", "13.0.101"}  # 확인에 쓴 CANdelaStudio 13 설치 예제의 버전(표시·테스트용)


def dtd_supported(dtdvers):
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", dtdvers or "")
    return bool(m) and int(m.group(1)) in SUPPORTED_MAJOR
DOCUMENT_KINDS = {"inst": "CDD", "tmpl": "CDDT"}
EXCL_ATTRIBUTES = {"s", "e", "inv"}
# CANdelaStudio 13 도움말 30 Features/Export/110_ODXExport "Table 40: Mapping of invalid values to ODX invalidity".
# 예제 확인: 0100 Percent undef → NOT-DEFINED, UDS-ExampleEcu.pdx Frequency_Counter noSignal → NOT-AVAILABLE.
EXCLUSION_VALIDITY = {"undef": "NOT-DEFINED", "noSignal": "NOT-AVAILABLE", "notImplemented": "NOT-AVAILABLE",
                      "unexpectedResult": "NOT-AVAILABLE", "invalidSignal": "NOT-VALID"}
COMP_ATTRIBUTES = {"f", "o", "div", "s", "e"}


def cdd_linear(n, t):
    """LINCOMP COMP → (factor_text, offset_text).

    근거: CANdelaStudio 13 예제 ODXEnvelope 출력 대조.
      Voltage_1_Byte  f=1 div=16 o=0    → ODX 분자 V0=0,   V1=0.0625 (0107_Supply_Voltage_ECU.odx-d)
      Acceleration    f=1 div=10 o=-13  → ODX 분자 V0=-13, V1=0.1    (010B_Acceleration_Sensor.odx-d)
    따라서 phys = raw * (f/div) + o 이며 오프셋은 div로 나누지 않는다.
    s/e는 ODX COMPU-SCALE의 LOWER/UPPER-LIMIT(원시값 유효 범위)로 출력된다 (0112 Time_2_Byte e=730 → UPPER-LIMIT 730,
    LOWER-LIMIT INFINITE). schema 2부터 raw_lower/raw_upper에 저장한다. 전체 원시 범위를 덮는 값은 제한 없음(NULL)으로 둔다.
    반환: (factor_text, offset_text, raw_lower, raw_upper)
    """
    comps = n.findall("COMP")
    if len(comps) != 1: raise DomainError("선형 변환식이 없거나 여러 개(구간별 변환)")
    comp = comps[0]
    extra = set(comp.keys()) - COMP_ATTRIBUTES
    if extra: raise DomainError("선형 변환 속성 미지원: " + ", ".join(sorted(extra)))
    # f/div가 유한 소수가 아니면(예: Position_1_Byte 359/255) 기약분수로 정확히 저장한다. 근거: 도움말 110_ODXExport
    # "Linear: Export of Denominator at COMPU-RATIONAL-COEFFICIENTS only if it differs from 1" — ODX는 분모로 정확히 표현한다.
    try:
        divisor = ratio(comp.get("div", "1"))
        if divisor == 0: raise DomainError("0으로 나누는 선형 변환")
        scaled = ratio(comp.get("f")) / divisor
        offset = decimal_text(comp.get("o"))
    except (DomainError, TypeError):
        raise DomainError("선형 변환 계수를 해석할 수 없음")
    if t["encoding"] == "float":
        lo, hi = -FLOAT_MAX[t["bit_length"]], FLOAT_MAX[t["bit_length"]]
    else:
        lo, hi = raw_bounds(t, -(1 << (t["bit_length"] - 1)) if t["encoding"] == "signed" else 0), raw_bounds(t, (1 << (t["bit_length"] - (t["encoding"] == "signed"))) - 1)
    start = integer(comp.get("s")) if comp.get("s") is not None else None
    end = integer(comp.get("e")) if comp.get("e") is not None else None
    if (start is not None and not lo <= start <= hi) or (end is not None and not lo <= end <= hi):
        raise DomainError(f"선형 변환 유효 범위가 원시값 범위를 벗어남 (s={start}, e={end})")
    raw_lower = str(start) if start is not None and start > lo else None
    raw_upper = str(end) if end is not None and end < hi else None
    return ratio_text(scaled), offset, raw_lower, raw_upper


def cdd_formula(n, encoding):
    """FORMULADT → coded→phys 수식 원문(29차). 수식은 계산하지 않는다.

    근거: KWP 템플릿 FORMULADT EngineTemperature(uns 8비트, FORMULA usage=coded2Phys, 단위 K) ↔ Vector 출력
    UDS-ExampleEcu.pdx DOP EngineTemperature: COMPU-METHOD IDENTICAL, PHYSICAL-TYPE = 부호화 타입(A_UINT32), UNIT-REF 없음,
    DESC "Generated from formula data type '…'. CodedToPhys formula: f(x)=…; PhysToCodedFormula: f(x)=".
    Vector 출력에 역수식이 있는 예가 없어 phys2Coded가 있는 문서와 정수가 아닌 타입은 보존한다.
    """
    if encoding not in ("unsigned", "signed"): raise DomainError("정수가 아닌 수식 타입")
    formulas = n.findall("FORMULA")
    if len(formulas) != 1 or formulas[0].get("usage") != "coded2Phys" or set(formulas[0].keys()) - {"usage"} or len(formulas[0]):
        raise DomainError("coded2Phys 수식 하나만 있는 수식 타입만 지원(역수식 출력 근거 없음)")
    text = (formulas[0].text or "").strip()
    if not text: raise DomainError("빈 수식")
    return text


# 29·30차: 예전에 보존(출력 차단)하던 CDD 요소를 근거를 찾아 가져오게 된 경우, 객체 속성으로 해결 내역을 남긴다.
# 보존 항목 목록의 "해결됨(가져옴)" 행과 속성 창에 보인다. ODX에는 나가지 않는다(cdd. 접두어가 아님).
RESOLVED_KEY = "import.resolved"


def resolved_attribute(text):
    return {"key": RESOLVED_KEY, "value_type": "text", "value_text": text}


def cdd_segments(n):
    """COMPTBL(여러 COMP) → 구간 행, VALTBL(VALMAP) → 점 행(schema 13 conversion_segments).
    COMP는 LINCOMP와 같이 phys = raw·(f/div) + o. VALMAP은 s=e인 점만(Vector 출력 TAB-INTP는 점마다 LOWER-LIMIT와 V 하나)."""
    rows = []
    if n.tag == "COMPTBL":
        for i, comp in enumerate(n.findall("COMP")):
            extra = set(comp.keys()) - COMP_ATTRIBUTES
            if extra: raise DomainError("선형 변환 속성 미지원: " + ", ".join(sorted(extra)))
            if comp.get("s") is None or comp.get("e") is None: raise DomainError("구간의 시작·끝(s·e)이 없는 COMP")
            divisor = ratio(comp.get("div", "1"))
            if divisor == 0: raise DomainError("0으로 나누는 선형 변환")
            rows.append({"ordinal": i, "lower_raw": str(integer(comp.get("s"))), "upper_raw": str(integer(comp.get("e"))),
                         "factor_text": ratio_text(ratio(comp.get("f", "1")) / divisor), "offset_text": decimal_text(comp.get("o", "0")), "value_text": None})
    else:
        for i, point in enumerate(n.findall("VALMAP")):
            if set(point.keys()) - {"s", "e", "v"} or point.get("s") != point.get("e", point.get("s")):
                raise DomainError("특성 곡선 점이 한 원시값(s=e)이 아님(Vector 출력 근거 없음)")
            rows.append({"ordinal": i, "lower_raw": str(integer(point.get("s"))), "upper_raw": None, "factor_text": None, "offset_text": None,
                         "value_text": decimal_text(point.get("v"))})
        rows.sort(key=lambda r: int(r["lower_raw"]))
        for i, r in enumerate(rows): r["ordinal"] = i
    return rows


def cdd_exclusions(n):
    """EXCL → raw_exclusions 행. 근거: 0100 Percent_1_Byte EXCL s=101 e=255 inv=undef → ODX SCALE-CONSTR NOT-DEFINED 101..255."""
    rows = []
    for ordinal, x in enumerate(n.findall("EXCL")):
        extra = set(x.keys()) - EXCL_ATTRIBUTES
        if extra: raise DomainError("예외값 속성 미지원: " + ", ".join(sorted(extra)))
        if any(c.tag != "TEXT" for c in x): raise DomainError("예외값 확장 항목 미지원")
        lower, upper = str(integer(x.get("s"))), str(integer(x.get("e", x.get("s"))))
        labels = {t.get(XML_LANG, "en-US"): "".join(t.itertext()) for t in x.findall("TEXT/TUV")}
        for locale, label in (labels.items() if labels else [("", "")]):
            rows.append({"ordinal": ordinal, "locale": locale, "lower_raw": lower, "upper_raw": upper, "kind": x.get("inv"), "label": label})
    return rows


def secure_parser():
    return ET.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False, remove_comments=True)


# ---------------------------------------------------------------- 27차: Vector 출력으로 확인한 문서 수준 정의
# 근거 쌍: CANoe 13 예제 UDSSystem/UDSBasic의 UDS-ExampleEcu-5.0.4·5.1.0.cdd(Door) ↔ 같은 문서를 CANdelaStudio(ODXExport201 8.5)가
# 출력한 UDS-ExampleEcu-5.0.2.pdx, CANdela 13 예제 ABS_ESP-Example.cdd ↔ Repository/ODXEnvelope(DTC). 파일은 저장소에 복사하지 않는다.
def _qual(element):
    return (element.findtext("QUAL") or "").strip() if element is not None else ""


def _qpath(text):
    body=(text or "").split(":",1)[-1] if (text or "").startswith(("qpath:","npath:")) else (text or "")
    return [p for p in body.split("/") if p]


def _int_list(text):
    body=(text or "").strip()
    if not (body.startswith("(") and body.endswith(")")): return None
    try: return [int(x) for x in body[1:-1].split(",") if x.strip()]
    except ValueError: return None


def _resolve_service(doc,path):
    """qpath:/<변형>/<클래스>/<인스턴스>/[SERVICE]<서비스> → CDD SERVICE 요소. 조각마다 자식 요소의 QUAL을 따라간다."""
    parts=_qpath(path)
    if len(parts)<2 or not parts[-1].startswith("[SERVICE]"): return None
    node=doc.find("ECU")
    for index,part in enumerate(parts[:-1]):
        tags=("VAR",) if index==0 else ("DIAGCLASS","DIAGINST")
        node=next((c for c in node if c.tag in tags and _qual(c)==part),None) if node is not None else None
    if node is None: return None
    return next((c for c in node.findall("SERVICE") if _qual(c)==parts[-1][len("[SERVICE]"):]),None)


def identification_features(doc,ids,kept_services):
    """IDENTIFICATIONFEATURESET → {특징 id: {"service", "parameter"}}, {특징 id: 해석하지 못한 이유}.
    Vector 출력(UDS-ExampleEcu-5.0.2)에서 식별 특징은 DIAG-COMM-SNREF(서비스)와 OUT-PARAM-IF-SNREF(응답 데이터 객체 Qualifier)가 된다.
    값 해석은 ASCII 타입만 확인했으므로(Part_Number = "Developer ECU") 다른 타입은 이유를 남긴다."""
    features,errors={},{}
    for f in doc.findall("IDENTIFICATIONFEATURESET/IDENTIFICATIONFEATURE"):
        fid=f.get("id")
        if set(f.attrib)-{"id","oid","dtref","dataObjectPath","diagCommPath"} or len(f):
            errors[fid]="식별 특징 확장 속성"; continue
        t=ids.get(f.get("dtref"))
        cv=t.find("CVALUETYPE") if t is not None else None
        if cv is None or cv.get("enc")!="asc":
            errors[fid]="ASCII가 아닌 식별 값의 ODX 대응은 확인하지 못함"; continue
        service=_resolve_service(doc,f.get("diagCommPath"))
        if service is None or service.get("id") not in kept_services:
            errors[fid]="식별 서비스를 찾지 못했거나 가져오지 못함"; continue
        data=_qpath(f.get("dataObjectPath"))
        if not data:
            errors[fid]="식별 데이터 객체 경로가 없음"; continue
        features[fid]={"service":service.get("id"),"parameter":data[-1]}
    return features,errors


def identification_patterns(node,features,feature_errors):
    """VAR/IDENTIFICATIONSET → [[{"service", "parameter", "expected"}, ...], ...]. 패턴 하나 = ECU-VARIANT-PATTERN 하나.
    IDENTIFICATIONDETAIL v는 바이트 목록이고 ASCII 특징이면 글자가 기대값이다(Vector 출력의 EXPECTED-VALUE)."""
    patterns=[]
    for ident in node:
        if ident.tag!="IDENTIFICATION" or ident.attrib: raise DomainError(f"식별 패턴 확장 의미 {ident.tag}")
        details=[]
        for d in ident:
            if d.tag!="IDENTIFICATIONDETAIL" or set(d.attrib)-{"idref","v"} or len(d): raise DomainError("식별 조건 확장 의미")
            feature=features.get(d.get("idref"))
            if feature is None: raise DomainError(feature_errors.get(d.get("idref"),"식별 특징을 찾지 못함"))
            values=_int_list(d.get("v"))
            if not values or not all(32<=x<127 for x in values): raise DomainError("인쇄 가능한 ASCII가 아닌 식별 값")
            details.append({**feature,"expected":"".join(chr(x) for x in values)})
        if not details: raise DomainError("조건이 없는 식별 패턴")
        patterns.append(details)
    if not patterns: raise DomainError("식별 패턴이 없음")
    return patterns


def _feature_set(node,ids,features,feature_errors,**_):
    if feature_errors: raise DomainError(next(iter(feature_errors.values())))
    return "해석함: 식별 특징 → 변형 식별 패턴(ODX ECU-VARIANT-PATTERN)에서 사용"


def _unsupported_service_nrcs(node,ids,out,**_):
    """UNSUPPSRVNEG → 7F · 요청 SID · NRC(ODX GLOBAL-NEG-RESPONSE). 근거: UDS-ExampleEcu-5.0.2.pdx UNSUPPORTED_SERVICE_NR
    (SID_NR CODED-CONST 127, SID_RQ_NR MATCHING-REQUEST-PARAM, NRC TEXTTABLE). 같은 모양이 아니면 해석하지 않는다."""
    comps=[c for c in node if c.tag not in ("NAME","QUAL","DESC")]
    if [c.tag for c in comps]!=["CONSTCOMP","STATICCOMP","CONTENTCOMP"]: raise DomainError("구성요소가 7F·SID·NRC 순서가 아님")
    const,static,content=comps
    if (const.get("spec"),const.get("bl"),const.get("v"))!=("sid","8","127"): raise DomainError("첫 바이트가 7F가 아님")
    if static.get("spec")!="id": raise DomainError("둘째 바이트가 요청 SID가 아님")
    proxies=list(content.iter("NEGRESCODEPROXY"))
    if not proxies: raise DomainError("NRC 목록이 없음")
    for p in proxies:
        target=ids.get(p.get("idref"))
        if target is None or target.tag!="NEGRESCODE": raise DomainError("NRC 참조를 찾지 못함")
        out.append(p.get("idref"))
    return "해석함: 미지원 서비스 부정 응답 → ODX GLOBAL-NEG-RESPONSE"


def _dtc_status_mask(node,ids,**_):
    """DTCSTATUSMASK: 템플릿이 정한 상태 비트 사용 여부(optyes/optno). Vector 출력(UDS-ExampleEcu 5.0.4 CDD는 8비트 모두 optno)에서도
    DtcStatusbyte의 VALUE·RESERVED는 인스턴스 구조를 따르므로 ODX에 따로 나가는 것이 없다. 원문은 보존한다."""
    if set(node.attrib)-{"dtref"} or any(c.tag!="DTCSTATUSBITGROUP" or set(c.attrib)-{"conv","dtref"} or len(c) for c in node):
        raise DomainError("상태 마스크 확장 의미")
    return "출력 영향 없음: DTC 상태 마스크(ODX 상태 비트는 인스턴스 구조로 정해짐)"


def _record_pool(node,ids,dtc_codes,**_):
    """RECORDDTPOOL: faultMemory RECORDDT(24비트 부호 없는 16진) = DTC 정의 모음. ABS의 Vector 출력(ODXEnvelope)은 DTC-DOP를
    24비트·HEX로 내고, 풀의 RECORD는 모두 DTC로 가져온다. 다른 레코드 타입에 RECORD가 있으면(OBD DTC 등) 해석하지 않는다."""
    fault=[r for r in node.findall("RECORDDT") if r.get("rtSpec")=="faultMemory"]
    if len(fault)!=1: raise DomainError("faultMemory 레코드 타입이 하나가 아님")
    cv=fault[0].find("CVALUETYPE")
    if cv is None or (cv.get("enc"),cv.get("bl"))!=("uns","24"): raise DomainError("24비트 부호 없는 DTC 코드가 아님")
    for r in node:
        if r.tag!="RECORDDT" or any(c.tag not in ("NAME","QUAL","DESC","CVALUETYPE","PVALUETYPE","RECORD") for c in r): raise DomainError("레코드 풀 확장 의미")
        records=r.findall("RECORD")
        if r is not fault[0] and records: raise DomainError(f"{r.get('rtSpec')} 레코드(가져오지 않음)")
        for rec in records:
            try: code=int(rec.get("v"))
            except (TypeError,ValueError): raise DomainError("DTC 코드를 읽을 수 없음")
            if code not in dtc_codes: raise DomainError(f"가져오지 않은 DTC 0x{code:06X}")
    return "해석함: DTC 레코드 풀 → DTC 정의·24비트 DTC-DOP"


DOCUMENT_DEFINITIONS={"IDENTIFICATIONFEATURESET":_feature_set,"UNSUPPSRVNEG":_unsupported_service_nrcs,"DTCSTATUSMASK":_dtc_status_mask,"RECORDDTPOOL":_record_pool}


def cdd_preview(path, cancel=lambda: None):
    path = Path(path)
    if path.stat().st_size > 100 * 1024 * 1024: raise DomainError("CDD 입력은 100 MiB 이하여야 합니다.")
    content = path.read_bytes()
    try: root = ET.fromstring(content, secure_parser())
    except ET.XMLSyntaxError as e: raise DomainError(f"CDD XML을 읽을 수 없습니다: {e}", "INVALID_XML")
    if any(isinstance(n, ET._Entity) for n in root.iter()): raise DomainError("XML 엔터티는 지원하지 않습니다.")
    doc = root.find("ECUDOC")
    doctype = doc.get("doctype") if doc is not None else None
    dtdvers = root.get("dtdvers")
    # 문서 종류: inst = CDD(ECU 문서), tmpl = CDDT(문서 템플릿). 둘은 같은 CANDELA 형식이다. 템플릿은 프로토콜 서비스·클래스 템플릿·
    # 타입·상태·NRC와 기본 변형의 기본 인스턴스를 담고, 새 문서가 이를 바탕으로 만들어진다(도움말 Data Model Overview "Template and
    # Document", Getting Started "File|New ... specify the CANdela template"). 따라서 CDDT는 같은 해석 경로로 새 프로젝트의 시작점이 된다.
    # 버전: 13.0.101(ABS 예제 CDD, UDSonLIN·KWPonCAN 템플릿)과 13.0.100(Vector_UDS_13.0 템플릿) — 모두 CANdelaStudio 13 설치본의 파일.
    if root.tag != "CANDELA" or not dtd_supported(dtdvers):
        raise DomainError(f"CANdela 문서 형식 2.x~13.x만 지원합니다(예: 13.0.101). (root={root.tag}, dtdvers={dtdvers}, doctype={doctype})", "UNSUPPORTED_VERSION")
    if doc is None or doctype not in DOCUMENT_KINDS:
        raise DomainError(f"CDD(inst) 또는 CDDT(tmpl) 문서가 아닙니다. (doctype={doctype})", "UNSUPPORTED_VERSION")
    expected = ".cddt" if doctype == "tmpl" else ".cdd"
    if path.suffix.lower() in (".cdd", ".cddt") and path.suffix.lower() != expected:
        raise DomainError(f"파일 확장자({path.suffix})와 문서 종류({DOCUMENT_KINDS[doctype]})가 다릅니다.", "UNSUPPORTED_VERSION")
    tree = root.getroottree()
    nodes = list(root.iter())
    if len(nodes) > 500000: raise DomainError("CDD 노드 수 제한을 초과했습니다.")
    for n in nodes:
        cancel()
        if len(list(n.iterancestors())) > 128 or len(n.text or "") > 8 * 1024 * 1024:
            raise DomainError("CDD 깊이 또는 텍스트 길이 제한을 초과했습니다.")
    def texts(node):
        result = {}
        for tag, field in (("NAME", "name"), ("DESC", "description")):
            locales = {}
            for t in node.findall(f"{tag}/TUV"):
                text = "".join(t.itertext())
                if text.strip(): locales[t.get(XML_LANG, "en-US")] = text  # 빈 TUV = 번역 없음(uptodate="0")
            if locales: result[field] = locales
        return result
    def common(node):
        multilingual = texts(node)
        names = multilingual.get("name", {})
        q = node.findtext("QUAL") or "Imported_" + str(len(unsupported))
        return {"external_id": node.get("id"), "external_oid": node.get("oid"), "node_path": tree.getpath(node), "qualifier": q, "name": names.get("en-US", next(iter(names.values()), q)), "description": "", "texts": multilingual}
    unsupported = []
    def unsupported_node(node, reason, owner=None, semantic=True):
        unsupported.append({"node_path": tree.getpath(node), "reason_code": reason, "raw_xml": ET.tostring(node, encoding="unicode"), "owner_external_id": owner, "semantic": int(semantic)})
    element_ids={e.get("id"):e for e in doc.iter() if e.get("id")}
    def parse_type(n):
        """CDD 단순 타입(IDENT·LINCOMP·TEXTTBL·COMPTBL·VALTBL·FORMULADT) → 타입 레코드. 지원하지 않으면 DomainError."""
        if n.tag not in ("IDENT", "LINCOMP", "TEXTTBL", "COMPTBL", "FORMULADT", "VALTBL"): raise DomainError("복합·가변 데이터 타입")
        extra = {c.tag for c in n if not (c.tag in ATTRIBUTE_TAGS and c.get("attrref"))} - TYPE_CHILDREN
        if extra: raise DomainError("데이터 타입 확장 항목 미지원: " + ", ".join(sorted(extra)))
        # 30차: 구간이 여러 개인 COMPTBL → 구간별 선형(scale_linear). 근거: Door TestDatatypePiecewiseLinear(COMP 8개, EXCL 129..255 undef)
        # ↔ UDS-ExampleEcu-5.0.2.pdx SCALE-LINEAR(구간마다 COMPU-SCALE·계수), NOT-DEFINED 129..255.
        piecewise = n.tag == "COMPTBL" and len(n.findall("COMP")) > 1
        linear = n.tag in ("LINCOMP", "COMPTBL") and not piecewise
        if n.tag not in ("LINCOMP", "COMPTBL") and n.find("COMP") is not None: raise DomainError("선형 타입이 아닌 변환식")
        if n.tag not in ("LINCOMP", "COMPTBL", "TEXTTBL", "VALTBL") and n.find("EXCL") is not None: raise DomainError("선형·열거 타입이 아닌 예외값")
        if n.tag != "FORMULADT" and n.find("FORMULA") is not None: raise DomainError("수식 타입이 아닌 수식")
        if n.tag != "VALTBL" and n.find("VALMAP") is not None: raise DomainError("특성 곡선 타입이 아닌 점 목록")
        cv = n.find("CVALUETYPE")
        leading = {"lszbyte":8, "lsz2bytes":16, "lsz3bytes":24, "lsz4bytes":32}.get(cv.get("sz")) if cv is not None else None
        if cv is None or (cv.get("sz") != "no" and leading is None) or cv.get("bo") not in ("21", "12"): raise DomainError("길이·바이트 순서 미지원")
        if leading is not None:
            # 30차: 앞 길이 바이트가 붙는 바이트 필드(sz=lszbyte·lsz2bytes·lsz3bytes·lsz4bytes, uns 8비트 요소). 근거: Door Memory_1~4_Byte_Size_Info ↔
            # 5.0.2 PDX A_BYTEFIELD LEADING-LENGTH-INFO-TYPE BIT-LENGTH 8/16/24/32. ASCII 문자열은 Vector 출력이 없어 보존한다.
            if n.tag!="IDENT" or cv.get("enc")!="uns" or cv.get("qty")!="field" or cv.get("bl")!="8" or n.get("bm") not in (None,"255"):
                raise DomainError("앞 길이 바이트가 붙는 필드는 부호 없는 바이트 필드만 지원(ASCII 등은 Vector 출력 근거 없음)")
            low,high=integer(cv.get("minsz"),0,1048576),integer(cv.get("maxsz"),0,1048576)
            if high<low: raise DomainError("가변 바이트 필드의 최대 길이가 최소보다 작음")
            p={**common(n),"encoding":"bytes","bit_length":8,"array_length":None,"min_length":low,"max_length":high,"leading_length_bits":leading,
               "byte_order":"big","conversion":"identity","unit":"","entries":[]}
            check_type(p)
            p["attributes"]=user_attributes(n,element_ids)+[resolved_attribute(f"30차 · 앞 길이 {leading//8}바이트 바이트 필드 (Vector 출력 LEADING-LENGTH-INFO-TYPE)")]
            return p
        encoding = {"uns":"unsigned", "sgn":"signed", "asc":"ascii", "bcd":"bcd", "flt":"float"}.get(cv.get("enc"))
        if encoding is None: raise DomainError(f"원시 인코딩 미지원: {cv.get('enc')}")
        if encoding=="bcd":
            # packed BCD(한 자리 4비트, 동일값). 근거: 도움말 110_ODXExport 표 31 numBCD → A_UINT32, BCD-P.
            if n.tag!="IDENT": raise DomainError("BCD 변환 타입(선형·텍스트 표) 미지원")
            if cv.get("qty")!="atom": raise DomainError("BCD 배열 미지원")
            bits=integer(cv.get("bl",0),1,64)
            if bits%4 or bits>32: raise DomainError("BCD 길이가 4비트 배수(32비트 이하)가 아님")
            # 비트 마스크는 타입 길이를 덮으면 전체 마스크다(2.0.5 파일은 길이와 무관하게 0xFFFFFFFF를 쓴다). 정수 타입과 같은 판정.
            if n.get("bm") is not None and integer(n.get("bm"))&((1<<bits)-1)!=(1<<bits)-1: raise DomainError("BCD 부분 비트 마스크 미지원")
            p={**common(n),"encoding":"bcd","bit_length":bits,"array_length":1,"byte_order":"big" if cv.get("bo")=="21" else "little","conversion":"identity","unit":n.findtext("PVALUETYPE/UNIT") or "","entries":[]}
            if p["byte_order"]=="little" and bits>8: raise DomainError("Little endian BCD 미지원")
            p["attributes"]=user_attributes(n,element_ids)
            return p
        # 가변 길이 바이트 필드(IDENT uns, 요소 8비트, qty=field, minsz≠maxsz): PDU 끝까지 이어지는 바이트 배열(최소·최대 길이).
        # 근거: UDS-ExampleEcu.pdx MIN-MAX-LENGTH-TYPE(TERMINATION=END-OF-PDU, MIN-LENGTH·MAX-LENGTH). 전문의 마지막 필드일 때만 쓸 수 있다.
        if (n.tag=="IDENT" and encoding=="unsigned" and cv.get("qty")=="field" and cv.get("bl")=="8" and cv.get("sz")=="no"
                and cv.get("minsz") is not None and cv.get("maxsz") is not None and cv.get("minsz")!=cv.get("maxsz") and n.get("bm") in (None,"255")):
            low,high=integer(cv.get("minsz"),0,1048576),integer(cv.get("maxsz"),0,1048576)
            if high<low: raise DomainError("가변 바이트 필드의 최대 길이가 최소보다 작음")
            p={**common(n),"encoding":"bytes","bit_length":8,"array_length":None,"min_length":low,"max_length":high,"byte_order":"big","conversion":"identity","unit":"","entries":[]}
            p["attributes"]=user_attributes(n,element_ids)
            return p
        # 고정 길이 바이트 필드(IDENT uns, 요소 8비트, qty=field, minsz=maxsz) → 바이트 배열 타입.
        # 근거: 도움말 110_ODXExport 고정 필드 → STANDARD-LENGTH-TYPE, UDS-ExampleEcu.pdx DtcCount 등 A_BYTEFIELD.
        if (n.tag=="IDENT" and encoding=="unsigned" and cv.get("qty")=="field" and cv.get("bl")=="8"
                and cv.get("minsz") is not None and cv.get("minsz")==cv.get("maxsz") and n.get("bm") in (None,"255")):
            encoding="bytes"
        if encoding in ("unsigned","signed") and cv.get("qty") != "atom": raise DomainError("정수 배열 미지원")
        if encoding=="float":
            # 30차: IEEE 754 부동소수. 근거: Door VehicleSpeed(LINCOMP, enc=flt bl=32, f=1 div=10, s=-300 e=3000) ↔ 5.0.2 PDX
            # DIAG-CODED-TYPE A_FLOAT32 BIT-LENGTH 32, LINEAR(분모 10), NOT-DEFINED ±3.402823e+038 쪽 빈 구간.
            if cv.get("qty")!="atom" or cv.get("bl") not in ("32","64") or n.tag not in ("IDENT","LINCOMP") or n.get("bm") not in (None,"4294967295","18446744073709551615"):
                raise DomainError("부동소수는 32·64비트 단일 값의 동일값·선형 타입만 지원")
        length=integer(cv.get("bl",0),1,64)
        count=1
        if encoding=="bytes":
            count=integer(cv.get("minsz"),1,1048576)
        if encoding=="ascii":
            if cv.get("minsz")!=cv.get("maxsz"): raise DomainError("가변 ASCII 배열")
            count=integer(cv.get("minsz"),1,1048576)
            if length!=8: raise DomainError("ASCII 길이 미지원")
        p={**common(n),"encoding":encoding,"bit_length":length,"array_length":count,"byte_order":"big" if cv.get("bo")=="21" else "little","conversion":"scale_linear" if piecewise else {"IDENT":"identity","LINCOMP":"linear","COMPTBL":"linear","TEXTTBL":"text_table","FORMULADT":"formula","VALTBL":"tab_intp"}[n.tag],"unit":n.findtext("PVALUETYPE/UNIT") or "","entries":[]}
        if encoding in ("unsigned","signed") and n.get("bm") is not None:
            full=(1<<length)-1
            if integer(n.get("bm"))&full!=full: raise DomainError(f"부분 비트 마스크 미지원 (bm={n.get('bm')}, {length}비트)")
        if n.tag=="FORMULADT":
            p["formula_expression"]=cdd_formula(n,encoding)
        if linear:
            # 29차: 구간 하나의 COMPTBL은 LINCOMP와 같은 선형 변환이다. 근거: KWP 템플릿 COMPTBL OdometerValue(s=0 e=65534 f=16,
            # EXCL 65535 noSignal) ↔ UDS-ExampleEcu.pdx DOP OdometerValue(LINEAR, 0..65534, V1=16, SCALE-CONSTR NOT-AVAILABLE 65535).
            if encoding not in ("unsigned","signed","float"): raise DomainError("정수가 아닌 선형 변환")
            factor,offset,raw_lower,raw_upper=cdd_linear(n,p)
            p.update(factor_text=factor,offset_text=offset,raw_lower=raw_lower,raw_upper=raw_upper,exclusions=cdd_exclusions(n))
        if piecewise or n.tag=="VALTBL":
            if encoding not in ("unsigned","signed"): raise DomainError("정수가 아닌 구간별 선형·특성 곡선")
            p["segments"]=cdd_segments(n)
            p["exclusions"]=cdd_exclusions(n)
        if n.tag=="TEXTTBL" and n.find("EXCL") is not None:
            # 30차: 열거 테이블의 예외값. 근거: Door CDD DTC_Significance(TEXTMAP 0·1, EXCL 2..255 noSignal "Reserved") ↔
            # UDS-ExampleEcu-5.0.2.pdx TEXTTABLE + SCALE-CONSTR NOT-AVAILABLE 2..255 SHORT-LABEL Reserved,
            # TestDatatypeMuxValues(EXCL notImplemented 5..6, undef 16..255) → NOT-AVAILABLE·NOT-DEFINED, 빈 구간 10..15 NOT-DEFINED.
            p["exclusions"]=cdd_exclusions(n)
        for i,m in enumerate(n.findall("TEXTMAP")):
            for t in m.findall("TEXT/TUV"):
                p["entries"].append({"ordinal":i,"locale":t.get(XML_LANG,"en-US"),"lower_raw":m.get("s"),"upper_raw":m.get("e"),"label":"".join(t.itertext())})
        check_type(p,p["entries"],p.get("exclusions",[]),p.get("segments"))
        p["attributes"]=user_attributes(n,element_ids)  # 사용자 정의 속성(ENUM 등, 예: UDS 템플릿의 타입 분류)
        note={"COMPTBL":"29차 · 구간 하나 COMPTBL → 선형 변환 (Vector 출력 LINEAR)",
              "FORMULADT":"29차 · FORMULADT → 수식 변환 (Vector 출력 IDENTICAL + 수식 설명)"}.get(n.tag)
        if n.tag=="TEXTTBL" and p.get("exclusions"): note="30차 · 예외값이 있는 열거 테이블 (Vector 출력 TEXTTABLE + SCALE-CONSTR)"
        if piecewise: note="30차 · 구간이 여러 개인 COMPTBL → 구간별 선형 (Vector 출력 SCALE-LINEAR)"
        if n.tag=="VALTBL": note="30차 · VALTBL → 특성 곡선 (Vector 출력 TAB-INTP)"
        if encoding=="float": note="30차 · 부동소수 원시값 (Vector 출력 A_FLOAT32/64)"
        if note: p["attributes"].append(resolved_attribute(note))
        return p
    types = []
    type_ids = set()
    for n in doc.findall("DATATYPES/*"):
        cancel()
        try:
            types.append(parse_type(n))
            type_ids.add(n.get("id"))
        except (DomainError, ValueError, TypeError) as e: unsupported_node(n, str(e))
    # STRUCTDT(packet 타입): 단순 타입을 모두 읽은 뒤 멤버를 해석한다. 근거: 도움말 110_ODXExport 표 30 Packet → STRUCTURE.
    # 멤버는 DID STRUCTURE와 같은 DATAOBJ·STRUCT(8비트 비트필드) 나열이고, CVALUETYPE(qty=field, 8비트 × minsz)이 전체 바이트 수다.
    # 29차: STRUCTDT 수준 바이트 순서(bo)는 ODX에 나가지 않는다. 근거: Door CDD TestDatatypeStructureLevel1(bo=12, 6바이트, 멤버
    # HexDump_2Byte bo=21) ↔ UDS-ExampleEcu-5.0.2.pdx STRUCTURE BYTE-SIZE 6·BYTE-POSITION 0/2/4, 멤버 DOP는 자기 바이트 순서(high-low).
    # KWP 템플릿 EnvironmentData(bo=12, 4바이트)도 같은 모양. 멤버의 바이트 순서는 각 멤버 타입이 정한다.
    simple_bits={t["external_id"]:t["bit_length"]*t["array_length"] for t in types if t.get("array_length")}
    # 30차: packet 멤버로 packet(중첩 STRUCTDT)도 허용한다. 근거: Door TestDatatypeStructureLevel2(멤버 Level1) ↔ 5.0.2 PDX STRUCTURE 안의
    # STRUCTURE 참조. 안쪽 packet이 먼저 만들어지도록 진행이 없을 때까지 여러 번 돈다.
    pending=list(doc.findall("DATATYPES/STRUCTDT"))
    struct_ids={n.get("id") for n in pending}
    errors={}
    while pending:
        progressed=False
        for n in list(pending):
            cancel()
            try:
                cv=n.find("CVALUETYPE")
                if cv is None or cv.get("qty")!="field" or cv.get("bl")!="8" or cv.get("sz")!="no" or cv.get("minsz")!=cv.get("maxsz"):
                    raise DomainError("packet 길이 형식 미지원")
                total=integer(cv.get("minsz"),1,1048576)*8
                if cv.get("bo") not in ("21","12"): raise DomainError("packet 바이트 순서 미지원")
                if n.get("bm") is not None and integer(n.get("bm"))&0xFF!=0xFF: raise DomainError("packet 부분 비트 마스크 미지원")
                members=[]
                waiting=False
                for item in n:
                    if item.tag in ("NAME","QUAL","DESC","CVALUETYPE","PVALUETYPE"): continue
                    if item.tag=="STRUCT":
                        field=parse_bitfield(item,element_ids,type_ids,lambda ref:simple_bits.get(ref,0),common)
                        if field.get("unverified"): raise DomainError("packet 안의 8비트보다 큰 비트필드 미지원")
                        members.append(field); continue
                    if item.tag=="DATAOBJ" and item.get("spec")=="no" and item.get("dtref") in struct_ids and item.get("dtref") not in simple_bits:
                        waiting=True; break
                    if item.tag!="DATAOBJ" or item.get("spec")!="no" or item.get("dtref") not in simple_bits: raise DomainError(f"packet 멤버 미지원: {item.tag}")
                    if any(x.tag not in ("NAME","QUAL","DESC") for x in item): raise DomainError("packet 멤버 확장 의미 미지원")
                    members.append({**common(item),"type_external_id":item.get("dtref"),"kind":"data"})
                if waiting:
                    errors[n]="packet 멤버의 packet 타입을 가져오지 못함"; continue
                used=sum(m["container_bits"] if m.get("kind")=="bitfield" else simple_bits[m["type_external_id"]] for m in members)
                if not members or used!=total: raise DomainError(f"packet 멤버 길이 합({used}비트)이 타입 길이({total}비트)와 다름")
                attrs=user_attributes(n,element_ids)
                nested=any(m.get("type_external_id") in struct_ids for m in members)
                if nested: attrs.append(resolved_attribute("30차 · packet 안의 packet → 중첩 STRUCTURE (Vector 출력과 같음)"))
                elif total>8 and cv.get("bo")=="12": attrs.append(resolved_attribute("29차 · bo=12 여러 바이트 packet → STRUCTURE (packet 바이트 순서는 출력되지 않음)"))
                types.append({**common(n),"encoding":"packet","bit_length":None,"array_length":None,"byte_order":"big","conversion":"identity","unit":"","entries":[],
                              "members":members,"packet_bits":total,"attributes":attrs})
                type_ids.add(n.get("id")); simple_bits[n.get("id")]=total
                errors.pop(n,None); pending.remove(n); progressed=True
            except (DomainError,ValueError,TypeError) as e:
                errors[n]=str(e); pending.remove(n); progressed=True
        if not progressed: break
    for n in doc.findall("DATATYPES/STRUCTDT"):
        unsupported[:]=[u for u in unsupported if u["node_path"]!=tree.getpath(n)]
        if n in errors: unsupported_node(n,errors[n])
    dids=[]
    did_ids=set()
    # 가변 길이 타입(array_length 없음)과, 마지막 멤버가 가변인 DID. 전문의 마지막에만 둘 수 있다(VAL-005).
    variable_types={t["external_id"] for t in types if t["array_length"] is None and t["encoding"]!="packet"}
    variable_dids=set()
    type_bits={t["external_id"]:t.get("packet_bits") or t["bit_length"]*(t.get("array_length") or 0) for t in types}
    def bitfield(item):
        return parse_bitfield(item,element_ids,type_ids,lambda ref:type_bits[ref],common)
    # 30차: DID 안의 MUX(DATAOBJ dtref=MUXDT, dataObjectRef = 앞쪽 선택자 DATAOBJ). 근거: Door DID_MuxedTestData(Selector TEXTTBL + MUXDT TestDatatype)
    # ↔ UDS-ExampleEcu-5.0.2.pdx STRUCTURE DID_MuxedTestData(Selector VALUE + MUX DOP, SWITCH-KEY = 선택자 DOP, CASE = 선택자 물리값).
    mux_model=Model(root,doc,tree,common,unsupported_node,type_ids,{},cancel,type_parser=parse_type)
    mux_model.variable_types=variable_types
    for n in doc.findall("DIDS/DID"):
        cancel()
        try:
            p={**common(n),"identifier":integer(n.get("n"),0,65535),"members":[],"read_service":False}
            structure=n.find("STRUCTURE")
            if structure is None: raise DomainError("DID 구조 없음")
            for item in structure:
                if item.tag=="STRUCT":
                    p["members"].append(bitfield(item))
                    continue
                target=element_ids.get(item.get("dtref")) if item.tag=="DATAOBJ" else None
                if target is not None and target.tag=="MUXDT" and item.get("spec")=="no":
                    selector=item.get("dataObjectRef")
                    if not any(m.get("kind")=="data" and m.get("external_id")==selector for m in p["members"]): raise DomainError("DID MUX의 선택자가 앞쪽 데이터 객체가 아님")
                    p["members"].append({**mux_model.mux(target,selector,"DID MUX"),**common(item)})
                    continue
                if item.tag!="DATAOBJ" or item.get("dtref") not in type_ids or item.get("spec")!="no": raise DomainError("복합·상수·가변 DID 멤버 미지원")
                if any(x.tag not in ("NAME","QUAL","DESC") for x in item): raise DomainError("DID 멤버 확장 의미 미지원")
                p["members"].append({**common(item),"type_external_id":item.get("dtref"),"kind":"data"})
            reasons=[m["unverified"] for m in p["members"] if m.get("unverified")]
            if reasons: p["unverified"]=reasons  # 8비트보다 큰 비트필드 등(21차)
            flags=[m.get("type_external_id") in variable_types or m.get("kind")=="mux" for m in p["members"]]
            if any(flags[:-1]): raise DomainError("가변 길이 DID 멤버가 마지막이 아님")
            if flags and flags[-1]: variable_dids.add(n.get("id"))
            dids.append(p)
            did_ids.add(n.get("id"))
        except (DomainError,ValueError) as e: unsupported_node(n,str(e),n.get("id"))
    variants=[]
    pending_patterns=[]
    ecu=doc.find("ECU")
    if ecu is None: raise DomainError("ECU 정의가 없습니다.")
    for n in ecu.findall("VAR"):
        refs=[]
        for ref in n.findall("DIDREFS/DIDREF"):
            if ref.get("didRef") in did_ids: refs.append(ref.get("didRef"))
            else: unsupported_node(ref,"해결되지 않은 DID 참조",n.get("id"))
        variants.append({**common(n),"base":n.get("base")=="1","did_refs":refs,"patterns":[]})
        for child in n:
            if child.tag=="IDENTIFICATIONSET":
                pending_patterns.append((variants[-1],n,child));continue  # 서비스를 다 해석한 뒤 판정한다(27차)
            if child.tag not in ("NAME","QUAL","DESC","DIDREFS","DIAGCLASS","DIAGINST","DIAGINSTREF","JOBCNR","JOBCNRREF"):
                unsupported_node(child,f"변형 확장 의미 미지원: {child.tag}",n.get("id"))
    if not variants or sum(v["base"] for v in variants)!=1: raise DomainError("기본 변형을 정확히 하나 식별할 수 없습니다.")
    for child in ecu:
        if child.tag not in ("NAME","QUAL","DESC","VAR",*Model.ATTRIBUTE_TAGS):
            unsupported_node(child,f"ECU 확장 의미 미지원: {child.tag}",ecu.get("id"))
    # 상태·서비스·DTC: 템플릿 참조를 따라 해석한다(cdd_model 참고).
    did_numbers={d["external_id"]:d["identifier"] for d in dids}
    model=Model(root,doc,tree,common,unsupported_node,type_ids,did_numbers,cancel,type_parser=parse_type)
    model.variable_types,model.variable_dids=variable_types,variable_dids
    groups=model.state_groups()
    instances,dtc_sources=model.instances(ecu.findall("VAR"))
    classes=list(model.classes.values())
    jobs,job_layout=model.jobs(ecu.findall("VAR"))
    for v in variants: v["layout"],v["jobs"]=model.layout.get(v["external_id"],[]),job_layout.get(v["external_id"],[])
    nrcs=model.nrcs()
    protocol_services=model.protocol_services()
    # DID 서비스는 인스턴스를 쓰는 모든 변형에 그 DID가 포함되어야 한다(VAL-008). 아니면 서비스를 보존만 한다.
    variant_dids={v["external_id"]:set(v["did_refs"]) for v in variants}
    for inst in instances:
        kept=[]
        for svc in inst["services"]:
            if any(d not in variant_dids[v] for d in svc["did_refs"] for v in inst["variants"]):
                unsupported_node(model.ids[svc["external_id"]],"서비스 미지원: 사용하는 변형 중 DID를 포함하지 않는 변형이 있음",inst["external_id"])
            else: kept.append(svc)
        inst["services"]=kept
    # 서비스 구성요소가 만든 타입(GroupOfDtc 지역 표, any 자리 바이트 필드). 가져오기에서 서비스보다 먼저 만든다.
    types.extend(model.extra_types.values())
    model.assign_state_codes(instances)
    dtcs=model.dtcs(dtc_sources,{i["external_id"]:i["variants"] for i in instances})
    services=sum(len(i["services"]) for i in instances)
    ecu_record={**common(ecu),"attributes":model.attributes(ecu)}
    # 27차: Vector 출력으로 대응을 확인한 문서 수준 정의(변형 식별·미지원 서비스 NRC·DTC 레코드 풀·상태 마스크)를 해석한다.
    kept_services={svc["external_id"] for inst in instances for svc in inst["services"]}
    features,feature_errors=identification_features(doc,model.ids,kept_services)
    for v,var_node,node in pending_patterns:
        try:
            v["patterns"]=identification_patterns(node,features,feature_errors)
            unsupported_node(node,"해석함: 변형 식별 패턴 → ODX ECU-VARIANT-PATTERN",var_node.get("id"),semantic=False)
        except DomainError as e:
            unsupported_node(node,f"변형 식별 패턴 미지원: {e}",var_node.get("id"))
    # 30차: 가져온 DID·서비스의 MUX로 쓰인 MUXDT는 사용처에서 해석했다(DID → ODX MUX, 서비스 → ODX TABLE). 원문은 참조용으로 남긴다.
    used_muxdt={m["muxdt_id"] for d in dids for m in d["members"] if m.get("kind")=="mux"}
    for inst in instances:
        for svc in inst["services"]:
            for layout in (svc.get("request_layout") or [],svc.get("positive_layout") or []):
                for kind,item in layout:
                    if kind=="mux" and isinstance(item,dict): used_muxdt.add(item.get("muxdt_id"))
    for u in unsupported:
        node=tree.xpath(u["node_path"])[0] if u["semantic"] and u["node_path"].rsplit("/",1)[-1].startswith("MUXDT") else None
        if node is not None and node.get("id") in used_muxdt:
            u.update(semantic=0,reason_code="해석함: MUXDT → 사용처의 MUX(DID: ODX MUX, 서비스 전문: ODX TABLE)")
    unsupported_service_nrcs=[]
    dtc_codes={d["code"] for d in dtcs}
    # 가져오기에서 해석하지 않은 의미는 원문을 남기고 출력 차단 사유로 표시한다.
    structural={"DATATYPES","DIDS","ECU"}
    metadata={"EXTSTORAGEITEMS","DESC","SPECOWNER","DTID","QUALGENOPTIONS","AUTHORS","HISTITEMS","TARGETGROUPS","REQDOC"}
    # 인스턴스를 통해 해석하는 템플릿·정의. 인스턴스에서 쓰지 못한 의미는 인스턴스·서비스 단위로 따로 보존된다.
    interpreted={"PROTOCOLSERVICES","DCLTMPLS","NEGRESCODES","STATEGROUPS","RECORDTMPLS","ATTRCATS","DEFATTS"}
    for child in doc:
        if child.tag in structural: continue
        # ABS 설치 예제에서 확인한 단순 출처 정보만 비차단으로 분류한다.
        # 같은 이름의 요소라도 확장 속성·자식이 있으면 기존 차단 경로를 유지한다.
        attrs=[]
        # 30차: KWP 등 다른 값도 같은 출처 정보(한 단어 원문)다. ODX에는 프로토콜 표준 요소가 없다(도움말 Export 표 무언급, Door PDX에 없음).
        if child.tag=="PROTOCOLSTANDARD" and not child.attrib and len(child)==0 and re.fullmatch(r"[A-Za-z0-9_./ -]{1,40}",(child.text or "").strip()):
            attrs=[("cdd.protocol_standard",(child.text or "").strip())]
        elif child.tag=="DOCTMPL" and set(child.attrib)<={"oid","saveno"} and all(n.tag in ("NAME","QUAL","LABEL") for n in child):
            attrs=[("cdd.document_template_"+key,value) for key,value in child.attrib.items()]
            attrs.extend(("cdd.document_template_"+key,value) for key,value in (("qualifier",child.findtext("QUAL")),("name",child.findtext("NAME/TUV"))) if value)
        elif child.tag=="VCKMGR" and len(child)==0 and set(child.attrib)<={"vckmode","vckmin","vckmax","vcknext"} and child.get("vckmode")=="none" and all(child.get(k,"0")=="0" for k in ("vckmin","vckmax","vcknext")):
            attrs=[("cdd.variant_coding_mode","none")]
        if child.tag in DOCUMENT_DEFINITIONS:
            try:
                reason=DOCUMENT_DEFINITIONS[child.tag](child,model.ids,features=features,feature_errors=feature_errors,dtc_codes=dtc_codes,out=unsupported_service_nrcs)
                unsupported_node(child,reason,semantic=False)
            except DomainError as e:
                unsupported_node(child,f"{child.tag} 미지원: {e}")
            continue
        if child.tag=="JOBFILES":
            # 30차: Job 코드 파일(내장 원문). Job(JOB)이 하나도 참조하지 않으면 ODX에 나갈 것이 없다(PROG-CODE는 Job마다 나간다,
            # 도움말 90_ODXExport_Jobs). 근거: Door CDD JOBFILES(Read_ECU_Fingerprint.cs), JOB 0개 ↔ 5.0.2 PDX에 SINGLE-ECU-JOB·파일 없음.
            file_ids={f.get("id") for f in child.iter() if f.get("id")}
            used=any(v in file_ids for j in doc.iter("JOB") for x in j.iter() for k,v in x.attrib.items() if k not in ("id","oid"))
            if not used:
                unsupported_node(child,"출력 영향 없음: Job이 참조하지 않는 Job 코드 파일",semantic=False)
                continue
        if attrs:
            ecu_record["attributes"].extend({"key":key,"value_type":"text","value_text":value} for key,value in attrs)
            unsupported_node(child,"출처·비활성 설정 원문 보존",semantic=False)
        elif child.tag in metadata: unsupported_node(child,"메타데이터 보존",semantic=False)
        elif child.tag in interpreted: unsupported_node(child,"템플릿·정의 원문 보존(인스턴스 단위로 해석)",semantic=False)
        else: unsupported_node(child,"프로토콜·템플릿·상태 확장 의미의 대조 필요")
    semantic=sum(n["semantic"] for n in unsupported)
    return {"path":str(path.resolve()),"basename":path.name,"sha256":hashlib.sha256(content).hexdigest(),"content":content,"version":dtdvers,"document_type":DOCUMENT_KINDS[doctype],"template_oid":doc.get("temploid") if doctype=="inst" else doc.get("oid"),"ecu":ecu_record,"manufacturer":doc.get("manufacturer",""),"types":types,"dids":dids,"variants":variants,"state_groups":groups,"classes":classes,"instances":instances,"dtcs":dtcs,"nrcs":nrcs,"protocol_services":protocol_services,"jobs":jobs,"unsupported":unsupported,"unsupported_service_nrcs":unsupported_service_nrcs,"coverage":{"data_types":len(types),"dids":len(dids),"variants":len(variants),"unsupported":len(unsupported),"blocking":semantic,"services":services,"classes":len(classes),"instances":len(instances),"jobs":len(jobs),"nrcs":len(nrcs),"protocol_services":len(protocol_services),"dtcs":len(dtcs),"states":sum(len(g["states"]) for g in groups if g["supported"])}}


def csv_read(path, delimiter=",", encoding="utf-8-sig"):
    p=Path(path)
    if p.stat().st_size>50*1024*1024: raise DomainError("CSV 입력은 50 MiB 이하여야 합니다.")
    if delimiter not in (",",";","\t"): raise DomainError("지원하지 않는 구분자입니다.")
    try:
        with p.open("r",encoding=encoding,newline="") as f:
            reader=csv.reader(f,delimiter=delimiter)
            headers=next(reader,[])
            if not headers or len(headers)>100: raise DomainError("CSV 열 수는 1~100개여야 합니다.")
            if len(headers)!=len(set(headers)): raise DomainError("중복된 열 이름이 있습니다.")
            rows=[]
            for i,row in enumerate(reader):
                if i>=100000: raise DomainError("CSV는 10만 행 이하여야 합니다.")
                rows.append(row)
            return {"headers":headers,"rows":rows}
    except UnicodeError: raise DomainError("CSV 인코딩을 확인하세요.")


def csv_map(data,mapping,existing=(),base="auto"):
    headers=data["headers"]
    if not mapping.get("code") or not mapping.get("name"): raise DomainError("코드와 이름 열을 지정하세요.")
    seen=set(existing)
    result=[]
    for i,row in enumerate(data["rows"],2):
        entry={"line":i,"error":""}
        try:
            if len(row)!=len(headers): raise DomainError("열 수가 헤더와 다릅니다.")
            values={field:row[headers.index(col)].strip() if col else "" for field,col in mapping.items()}
            raw=values["code"]
            code=int(raw,16 if base=="hex" or raw.lower().startswith("0x") else 10)
            integer(code,0,0xFFFFFF)
            if code in seen: raise DomainError("동일 변형에 중복 DTC 코드가 있습니다.")
            if not values["name"]: raise DomainError("이름이 비어 있습니다.")
            q=values.get("qualifier") or f"DTC_{code:06X}"
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",q): raise DomainError("Qualifier 형식이 올바르지 않습니다.")
            seen.add(code)
            entry.update(code=code,name=values["name"],qualifier=q,description=values.get("description",""))
        except (DomainError,ValueError) as e: entry["error"]=str(e)
        result.append(entry)
    return result


def export_csv(snapshot,variant,destination):
    ids={m["dtc_id"] for m in snapshot.rows("variant_dtcs") if m["variant_id"]==variant}
    output=io.StringIO(newline="")
    writer=csv.writer(output)
    writer.writerow(["code","name","qualifier","description"])
    for d in sorted(snapshot.rows("dtcs"),key=lambda x:x["code"]):
        if d["id"] in ids:
            writer.writerow([f"0x{d['code']:06X}",snapshot.name(d["id"]),snapshot.get("objects",d["id"])["qualifier"],snapshot.name(d["id"],"description")])
    atomic_bytes(Path(destination),output.getvalue().encode("utf-8-sig"))
    return {"path":str(destination),"count":len(ids),"revision":snapshot.revision}


def atomic_bytes(path,data):
    temp=path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if temp.exists(): temp.unlink()


def raw_limits(t):
    if t["encoding"]=="float": return FLOAT_LIMIT_TEXT[t["bit_length"]]
    lo=-(1<<(t["bit_length"]-1)) if t["encoding"]=="signed" else 0
    return lo,(1<<(t["bit_length"]-(t["encoding"]=="signed")))-1


def subtract(intervals,covered):
    """정수 구간 목록에서 covered 구간들을 뺀다."""
    result=[]
    for lo,hi in intervals:
        pieces=[(lo,hi)]
        for clo,chi in covered:
            nxt=[]
            for a,b in pieces:
                if chi<a or clo>b: nxt.append((a,b));continue
                if a<clo: nxt.append((a,clo-1))
                if chi<b: nxt.append((chi+1,b))
            pieces=nxt
        result.extend(pieces)
    return result


def scale_constraints(s,t,locale_entries,segments=None):
    """DOP INTERNAL-CONSTR/SCALE-CONSTR 목록 [(lower, upper, validity, label|None)].

    근거(CANdelaStudio 13 예제 ODXEnvelope):
      0112 Time_2_Byte   COMP e=730, 16비트           → NOT-DEFINED 731..65535, SHORT-LABEL 없음
      0100 Percent_1_Byte COMP e=100, EXCL 101..255 undef → NOT-DEFINED 101..255, 빈 SHORT-LABEL
      0100 active_not_active TEXTMAP 0,1, 8비트        → NOT-DEFINED 2..255
      Import/UDS-ExampleEcu.pdx Frequency_Counter COMP s=1 e=255, EXCL 0 noSignal
                                                       → COMPU-SCALE 1..255, NOT-AVAILABLE 0..0 + SHORT-LABEL(EXCL 문구),
                                                         EXCL이 덮은 구간은 NOT-DEFINED를 따로 만들지 않음
    예외 종류별 VALIDITY는 EXCLUSION_VALIDITY(도움말 Table 40). 미대응 범위 NOT-DEFINED 생성은 도움말 110_ODXExport
    "Uncovered value ranges"(Checker-Rule 140-1)와 같다.
    """
    lo,hi=raw_limits(t)
    result=[]
    exclusions={}
    segments=segments or []
    for e in s.rows("raw_exclusions"):
        if e["data_type_id"]!=t["id"]: continue
        item=exclusions.setdefault(e["ordinal"],{"lo":int(e["lower_raw"]),"hi":int(e["upper_raw"]),"kind":e["kind"],"labels":{}})
        if e["label"]: item["labels"][e["locale"]]=e["label"]
    for x in exclusions.values():
        if x["kind"] not in EXCLUSION_VALIDITY:
            raise DomainError(f"예외값 종류 {x['kind']}의 ODX 대응이 없어 출력을 차단합니다.","UNSUPPORTED_FEATURE")
        label=x["labels"].get(s.locale) or x["labels"].get("en-US") or next(iter(x["labels"].values()),"")
        result.append((x["lo"],x["hi"],EXCLUSION_VALIDITY[x["kind"]],label))
    covered=[(x["lo"],x["hi"]) for x in exclusions.values()]
    if t["encoding"]=="float":
        # schema 13: 부동소수는 정수 빈 구간을 ±최대값 글자로 잇는다. 근거: Door VehicleSpeed(s=-300 e=3000) ↔ Vector NOT-DEFINED
        # -3.402823e+038..-301, 3001..3.402823e+038.
        low_text,high_text=FLOAT_LIMIT_TEXT[t["bit_length"]]
        if t["conversion"]=="linear":
            if t.get("raw_lower") is not None: result.append((low_text,str(int(t["raw_lower"])-1),"NOT-DEFINED",None))
            if t.get("raw_upper") is not None: result.append((str(int(t["raw_upper"])+1),high_text,"NOT-DEFINED",None))
        return sorted(result,key=lambda r:(float(r[0]),float(r[1])))
    if t["conversion"]=="linear":
        valid=(int(t["raw_lower"]) if t.get("raw_lower") is not None else lo,int(t["raw_upper"]) if t.get("raw_upper") is not None else hi)
        covered.append(valid)
    elif t["conversion"]=="scale_linear":
        covered+=[(int(x["lower_raw"]),int(x["upper_raw"])) for x in segments]
    elif t["conversion"]=="tab_intp":
        if segments: covered.append((int(segments[0]["lower_raw"]),int(segments[-1]["lower_raw"])))
    elif t["conversion"]=="text_table":
        covered+= [(int(e["lower_raw"]),int(e["upper_raw"])) for e in locale_entries]
    else:
        covered.append((lo,hi))
    result+=[(a,b,"NOT-DEFINED",None) for a,b in subtract([(lo,hi)],covered)]
    return sorted(result)


def export_odx(s,variants,destination,xsd_path,cancel=lambda:None):
    if not xsd_path or not Path(xsd_path).is_file():
        raise DomainError("공식 ODX 2.2.0 odx.xsd와 의존 스키마 폴더를 먼저 지정하세요.","VALIDATION_UNAVAILABLE")
    report=validate_export(s,variants,cancel)
    if report["blocking_count"]:
        messages=[i["message"] for i in report["issues"] if i["blocks_export"]]
        raise DomainError(f"선택한 출력 범위의 차단 항목 {report['blocking_count']}개를 해결하세요.\n"+"\n".join(messages[:5]),"EXPORT_BLOCKED")
    root=build_odx(s,variants,cancel)
    cancel()
    try:
        schema=ET.XMLSchema(ET.parse(str(Path(xsd_path).resolve()),secure_parser()))
        schema.assertValid(root)
    except (ET.XMLSchemaParseError,ET.DocumentInvalid,ET.XMLSyntaxError,OSError) as e:
        raise DomainError("ODX 스키마 검사 실패: "+str(e),"EXPORT_BLOCKED")
    data=ET.tostring(root,encoding="utf-8",xml_declaration=True,pretty_print=True)
    cancel()
    atomic_bytes(Path(destination),data)
    result={"revision":s.revision,"path":str(destination),"sha256":hashlib.sha256(data).hexdigest(),"schema":str(xsd_path),"variants":variants,"schema_validation":"passed","external_tool_validation":"not_run","preflight":report}
    atomic_bytes(Path(str(destination)+".report.json"),json.dumps(result,ensure_ascii=False,indent=2).encode("utf-8"))
    return result


# CDD DCLTMPL cls 코드 → 클래스 유형 → DIAG-SERVICE SEMANTIC.
# 근거: 도움말 "Mapping CANdela diagnostic classes to ODX elements" 표 9(유형 → SEMANTIC)와 "Diagnostic Class Template"의
# 유형 목록(같은 순서). XML 코드는 유형 이름의 약어다(ABS 예제: ses=Sessions, idn=Identification, ftm=Fault Memory, act=Dynamic Data …).
# 예제에 없는 유형(calibration, upload)의 코드는 확인하지 못해 넣지 않는다 → 해당 서비스는 SEMANTIC을 생략한다.
# 알려진 차이: Vector PDX 예제(UDS-ExampleEcu, ODX 2.0.1, "CANdelaStudio::ASAMOdxExport201.dll 5.0" 출력)는 DynamicData에
# CURRENTDATA를 쓴다. 현재(13.0) 도움말 표 9는 ACTUAL-DATA이고 이 앱은 ODX 2.2.0을 출력하므로 현재 도움말을 따른다.
CLASS_SEMANTIC={"com":"COMMUNICATION","ses":"SESSION","sec":"SECURITY","idn":"IDENTIFICATION","ftm":"FAULTMEMORY","vcd":"VARCODING",
                "act":"ACTUAL-DATA","std":"STORED-DATA","mem":"MEMORY","ctl":"CONTROL","rtn":"ROUTINE","fun":"FUNCTION",
                "dwn":"UP-DOWNLOAD","job":"JOB","tpr":"TESTERPRESENT","flashjob":"FLASHJOB"}


def build_odx(s,variants,cancel=lambda:None):
    """검증을 통과한 스냅샷에서 ODX 2.2.0 트리를 만든다. 스키마 검사·파일 쓰기는 export_odx가 한다."""
    if not variants: raise DomainError("출력할 변형을 선택하세요.")
    class_types={a["object_id"]:a["value_text"] for a in s.rows("object_attributes") if a["key"]=="cdd.class_type"}
    service_semantics={a["object_id"]:a["value_text"] for a in s.rows("object_attributes") if a["key"]=="cdd.semantic"}
    def child(parent,tag,value=None,**attrs):
        n=ET.SubElement(parent,tag,attrs)
        if value is not None: n.text=str(value)
        return n
    root=ET.Element("ODX",{"MODEL-VERSION":"2.2.0"},nsmap={"xsi":XSI})
    root.set(f"{{{XSI}}}noNamespaceSchemaLocation","odx.xsd")
    project=s.rows("project_meta")[0]
    dlc=child(root,"DIAG-LAYER-CONTAINER",ID="DLC_"+project["project_uuid"].replace("-",""))
    child(dlc,"SHORT-NAME","DiagAuthor")
    child(dlc,"LONG-NAME",s.name(s.rows("ecu")[0]["id"]))
    layers=child(dlc,"ECU-VARIANTS")
    for vid in variants:
        cancel()
        variant=s.get("variants",vid)
        prefix="V_"+vid.replace("-","")+"_"
        def refid(oid): return prefix+oid.replace("-", "")
        def transition_id(x): return prefix+"ST_"+x["from_state_id"].replace("-","")+"_"+x["to_state_id"].replace("-","")
        layer=child(layers,"ECU-VARIANT",ID=refid(vid))
        child(layer,"SHORT-NAME",s.get("objects",vid)["qualifier"])
        child(layer,"LONG-NAME",s.name(vid))
        instance_ids={v["instance_id"] for v in s.rows("variant_instances") if v["variant_id"]==vid}
        services=[x for x in s.rows("services") if x["instance_id"] in instance_ids and x["enabled"]]
        service_ids={x["id"] for x in services}
        # 상태 모델: 상태 그룹 → STATE-CHART, 상태 → STATE, 서비스의 전이 → STATE-TRANSITION(서비스가 참조), 초기 상태 → START-STATE.
        # 근거: 도움말 180_ODXExport(ODX 2.1.0 이후 STATE-Model). 실행 가능 상태(일부 상태에서만 허용)의 ODX 대응은 도움말에 없어
        # 그런 서비스가 있으면 출력을 막는다(모든 상태 허용 = 제한 없음은 출력 가능).
        restricted=sorted({s.get("objects",c["service_id"])["qualifier"] for c in s.rows("service_conditions") if c["service_id"] in service_ids and not c["allowed"]})
        if restricted:
            raise DomainError("실행 가능 상태가 제한된 서비스는 ODX 대응 근거가 없어 출력이 차단되었습니다: "+", ".join(restricted[:5])+(" 외" if len(restricted)>5 else ""),"UNSUPPORTED_FEATURE")
        transitions=[x for x in s.rows("service_transitions") if x["service_id"] in service_ids]
        did_ids={v["did_id"] for v in s.rows("variant_dids") if v["variant_id"]==vid}
        telegrams=[t for t in s.rows("telegrams") if t["service_id"] in service_ids]
        structures=[s.get("dids",d)["structure_id"] for d in did_ids]+[t["structure_id"] for t in telegrams]
        def leaves(fields):
            for f in fields:
                if f["kind"]=="iteration": yield from leaves(f["item"])
                elif f["kind"]=="mux":
                    # 30차: MUX 케이스·기본 구조의 데이터 객체 타입도 사전에 나간다.
                    for _,_,case in f["cases"]: yield from leaves(case)
                    if f.get("default"): yield from leaves(f["default"])
                else: yield f
        used_types={f["type"]["id"] for st in structures for f in leaves(s.flatten(st)) if f["kind"]=="data"}
        dtcs=[d for d in s.rows("dtcs") if any(v["variant_id"]==vid and v["dtc_id"]==d["id"] for v in s.rows("variant_dtcs"))]
        dictionary=child(layer,"DIAG-DATA-DICTIONARY-SPEC")
        def section(tag):
            found=dictionary.find(tag)
            return found if found is not None else child(dictionary,tag)
        if used_types or did_ids:
            dops=child(dictionary,"DATA-OBJECT-PROPS")
            for tid in sorted(used_types):
                t=s.get("data_types",tid)
                if t["encoding"] in ("unsigned","signed") and t["bit_length"]>32:
                    raise DomainError("ODX 정수 타입 매핑은 현재 32비트까지입니다. 큰 정수를 변환하지 않고 출력을 차단합니다.","UNSUPPORTED_FEATURE")
                entries=[e for e in s.rows("conversion_entries") if e["data_type_id"]==tid]
                locales=sorted({e["locale"] for e in entries},key=lambda l:(l!=s.locale,l!="en-US",l))
                locale_entries=[e for e in entries if locales and e["locale"]==locales[0]]
                dop=child(dops,"DATA-OBJECT-PROP",ID=refid(tid))
                child(dop,"SHORT-NAME",s.get("objects",tid)["qualifier"])
                child(dop,"LONG-NAME",s.name(tid))
                if t["conversion"]=="formula":
                    # schema 12: CDD FORMULADT → IDENTICAL + 수식 설명. Vector 출력(UDS-ExampleEcu.pdx EngineTemperature)과 같은 문구.
                    qual=s.get("objects",tid)["qualifier"]
                    child(child(dop,"DESC"),"p",f"Generated from formula data type '{qual}'.\nCodedToPhys formula: f(x)={t['formula_expression']}; \nPhysToCodedFormula: f(x)=")
                method=child(dop,"COMPU-METHOD")
                child(method,"CATEGORY",{"identity":"IDENTICAL","linear":"LINEAR","text_table":"TEXTTABLE","formula":"IDENTICAL",
                                         "scale_linear":"SCALE-LINEAR","tab_intp":"TAB-INTP"}[t["conversion"]])
                segments=sorted((x for x in s.rows("conversion_segments") if x["data_type_id"]==tid),key=lambda x:x["ordinal"])
                def linear_scale(scales,minimum,maximum,factor_text,offset_text):
                    scale=child(scales,"COMPU-SCALE")
                    child(scale,"LOWER-LIMIT",minimum,**{"INTERVAL-TYPE":"CLOSED"})
                    child(scale,"UPPER-LIMIT",maximum,**{"INTERVAL-TYPE":"CLOSED"})
                    rational=child(scale,"COMPU-RATIONAL-COEFFS")
                    num=child(rational,"COMPU-NUMERATOR")
                    factor=ratio(factor_text)
                    if terminating(factor):
                        child(num,"V",offset_text)
                        child(num,"V",factor_text)
                        child(child(rational,"COMPU-DENOMINATOR"),"V",1)
                    else:
                        # 분수 계수: phys = (offset·q + p·raw) / q. 근거: 도움말 110_ODXExport(분모가 1이 아니면 출력).
                        child(num,"V",ratio_text(ratio(offset_text)*factor.denominator))
                        child(num,"V",factor.numerator)
                        child(child(rational,"COMPU-DENOMINATOR"),"V",factor.denominator)
                if t["conversion"] not in ("identity","formula"):
                    scales=child(child(method,"COMPU-INTERNAL-TO-PHYS"),"COMPU-SCALES")
                    if t["conversion"]=="linear":
                        minimum,maximum=raw_limits(t)
                        if t.get("raw_lower") is not None: minimum=t["raw_lower"]
                        if t.get("raw_upper") is not None: maximum=t["raw_upper"]
                        linear_scale(scales,minimum,maximum,t["factor_text"],t["offset_text"])
                    elif t["conversion"]=="scale_linear":
                        # schema 13: 구간마다 COMPU-SCALE(Door TestDatatypePiecewiseLinear ↔ Vector SCALE-LINEAR).
                        for x in segments: linear_scale(scales,x["lower_raw"],x["upper_raw"],x["factor_text"],x["offset_text"])
                    elif t["conversion"]=="tab_intp":
                        # schema 13: 점마다 LOWER-LIMIT(원시값)와 COMPU-CONST/V(물리값). Door TestDatatypeCharacteristicCurve ↔ Vector TAB-INTP.
                        for x in segments:
                            scale=child(scales,"COMPU-SCALE")
                            child(scale,"LOWER-LIMIT",x["lower_raw"],**{"INTERVAL-TYPE":"CLOSED"})
                            child(child(scale,"COMPU-CONST"),"V",x["value_text"])
                    else:
                        for e in locale_entries:
                            scale=child(scales,"COMPU-SCALE")
                            child(scale,"LOWER-LIMIT",e["lower_raw"],**{"INTERVAL-TYPE":"CLOSED"})
                            child(scale,"UPPER-LIMIT",e["upper_raw"],**{"INTERVAL-TYPE":"CLOSED"})
                            child(child(scale,"COMPU-CONST"),"VT",e["label"])
                base={"unsigned":"A_UINT32","signed":"A_INT32","ascii":"A_ASCIISTRING","bytes":"A_BYTEFIELD","bcd":"A_UINT32",
                      "float":"A_FLOAT32" if t["bit_length"]==32 else "A_FLOAT64"}[t["encoding"]]
                if t.get("leading_length_bits"):
                    # schema 13: 앞 길이 바이트. 근거: Door Memory_2_Byte_Size_Info ↔ 5.0.2 PDX A_BYTEFIELD LEADING-LENGTH-INFO-TYPE BIT-LENGTH 16.
                    ct=child(dop,"DIAG-CODED-TYPE",**{"BASE-TYPE-ENCODING":"NONE","BASE-DATA-TYPE":base,f"{{{XSI}}}type":"LEADING-LENGTH-INFO-TYPE"})
                    child(ct,"BIT-LENGTH",t["leading_length_bits"])
                elif t["encoding"]=="float":
                    # schema 13: Vector 출력(VehicleSpeed)은 BASE-TYPE-ENCODING 없이 A_FLOAT32.
                    ct=child(dop,"DIAG-CODED-TYPE",**{"BASE-DATA-TYPE":base,**({} if t["byte_order"]=="big" else {"IS-HIGHLOW-BYTE-ORDER":"false"}),f"{{{XSI}}}type":"STANDARD-LENGTH-TYPE"})
                    child(ct,"BIT-LENGTH",t["bit_length"])
                elif t["array_length"] is None:
                    # 가변 바이트 필드(PDU 끝까지). 근거: UDS-ExampleEcu.pdx DtcSnapshotRecordNumber
                    # (A_BYTEFIELD, TERMINATION=END-OF-PDU, MIN-MAX-LENGTH-TYPE, MIN-LENGTH).
                    ct=child(dop,"DIAG-CODED-TYPE",**{"BASE-DATA-TYPE":base,"TERMINATION":"END-OF-PDU",f"{{{XSI}}}type":"MIN-MAX-LENGTH-TYPE"})
                    if t.get("max_length") is not None: child(ct,"MAX-LENGTH",t["max_length"])  # 순서: MAX-LENGTH, MIN-LENGTH(UDS-ExampleEcu.pdx ASCII_17)
                    child(ct,"MIN-LENGTH",t["min_length"])
                else:
                    ct=child(dop,"DIAG-CODED-TYPE",**{"BASE-DATA-TYPE":base,"BASE-TYPE-ENCODING":"ISO-8859-1" if t["encoding"]=="ascii" else "2C" if t["encoding"]=="signed" else "BCD-P" if t["encoding"]=="bcd" else "NONE","IS-HIGHLOW-BYTE-ORDER":"true" if t["byte_order"]=="big" else "false",f"{{{XSI}}}type":"STANDARD-LENGTH-TYPE"})
                    child(ct,"BIT-LENGTH",t["bit_length"]*t["array_length"])
                physical="A_FLOAT64" if t["conversion"] in ("linear","scale_linear","tab_intp") else "A_UNICODE2STRING" if t["conversion"]=="text_table" else base
                child(dop,"PHYSICAL-TYPE",**{"BASE-DATA-TYPE":physical})
                constraints=scale_constraints(s,t,locale_entries,segments) if t["encoding"] in ("unsigned","signed","float") else []
                if constraints:
                    sc=child(child(dop,"INTERNAL-CONSTR"),"SCALE-CONSTRS")
                    for lower,upper,validity,label in constraints:
                        c=child(sc,"SCALE-CONSTR",VALIDITY=validity)
                        if label is not None: child(c,"SHORT-LABEL",label or None)
                        child(c,"LOWER-LIMIT",lower)
                        child(c,"UPPER-LIMIT",upper)
                if t["unit"] and t["conversion"]!="formula":  # Vector는 수식 타입의 단위를 내지 않는다(물리값이 원시값이므로)
                    child(dop,"UNIT-REF",**{"ID-REF":refid(tid)+"_Unit"})
            if did_ids:
                dop=child(dops,"DATA-OBJECT-PROP",ID=prefix+"DID_Key")
                child(dop,"SHORT-NAME","DID_Key")
                child(child(dop,"COMPU-METHOD"),"CATEGORY","IDENTICAL")
                ct=child(dop,"DIAG-CODED-TYPE",**{"BASE-DATA-TYPE":"A_UINT32",f"{{{XSI}}}type":"STANDARD-LENGTH-TYPE"})
                child(ct,"BIT-LENGTH",16)
                child(dop,"PHYSICAL-TYPE",**{"BASE-DATA-TYPE":"A_UINT32"})
        def case_structure(sid,label,fields):
            """MUX 케이스 구조 → STRUCTURE(한 번만)."""
            item=refid(sid)
            if dictionary.find(f"STRUCTURES/STRUCTURE[@ID='{item}']") is None:
                st=child(section("STRUCTURES"),"STRUCTURE",ID=item)
                child(st,"SHORT-NAME",s.get("objects",sid)["qualifier"])
                child(st,"LONG-NAME",label)
                params(st,sid,fields)
            return item
        def mux_table(f,selector_field,key_id):
            """30차: 서비스 전문의 MUX → TABLE(KEY-DOP = 선택자 DOP, 선택자 값마다 TABLE-ROW → 케이스 STRUCTURE).
            근거: Door CDD Download_Request(MUXDT Memory_Address_and_Size, 선택자 ALFID) ↔ UDS-ExampleEcu-5.0.2.pdx
            TABLE Memory_Address_and_Size(KEY-DOP-REF AddressAndLengthFormatIdentifier, TABLE-ROW TR_0x11 … KEY = 선택자 물리값, STRUCTURE-REF),
            요청 PARAM TABLE-KEY(ALFID, TABLE-REF) + TABLE-STRUCT(TABLE-KEY-REF).
            기본 구조: 같은 MUXDT의 기본 STRUCTURE(Address·Size)가 Vector TABLE에는 없다 → 내지 않고 검증 WRN-002로 알린다."""
            table_id=refid(f["mux_key"])+"_TABLE"
            if dictionary.find(f"TABLES/TABLE[@ID='{table_id}']") is None:
                table=child(section("TABLES"),"TABLE",ID=table_id)
                child(table,"SHORT-NAME","MUX_"+s.get("objects",selector_field["object"]["id"])["qualifier"])
                child(table,"LONG-NAME",f["label"])
                child(table,"KEY-DOP-REF",**{"ID-REF":refid(selector_field["type"]["id"])})
                t=selector_field["type"]
                entries=[e for e in s.rows("conversion_entries") if e["data_type_id"]==t["id"]]
                exclusions=[e for e in s.rows("raw_exclusions") if e["data_type_id"]==t["id"]]
                for (lo,hi,fields),sid in zip(f["cases"],f["case_structures"]):
                    if hi-lo>255: raise DomainError("값 범위가 넓은 MUX 케이스의 ODX TABLE 대응은 확인하지 못해 출력이 차단되었습니다.","UNSUPPORTED_FEATURE")
                    item=case_structure(sid,f"{f['label']} {lo}..{hi}",fields)
                    for v in range(lo,hi+1):
                        row=child(table,"TABLE-ROW",ID=f"{table_id}_{v}")
                        child(row,"SHORT-NAME",f"TR_0x{v:X}")
                        key=convert({**t,"segments":[x for x in s.rows("conversion_segments") if x["data_type_id"]==t["id"]]},v,entries=entries,locale=s.locale,exclusions=exclusions)[1] if t["encoding"] in ("unsigned","signed") else str(v)
                        child(row,"LONG-NAME",key)
                        child(row,"KEY",key)
                        child(row,"STRUCTURE-REF",**{"ID-REF":item})
            return table_id
        def params(parent,structure,fields=None):
            container=child(parent,"PARAMS")
            offset=0
            fields=s.flatten(structure) if fields is None else fields
            muxes={f["selector"]:f for f in fields if f["kind"]=="mux" and not f.get("in_did")}
            keys={}
            selector_offsets={}
            for index,f in enumerate(fields):
                if f["kind"]=="data": selector_offsets[f["object"]["id"]]=(offset,f)
                if f["kind"]=="mux" and f.get("in_did"):
                    # 30차: DID 안의 MUX → MUX DOP. 근거: Door DID_MuxedTestData ↔ 5.0.2 PDX: DID STRUCTURE에 Selector VALUE(0)와
                    # MUX 참조 VALUE(선택자 위치, 0), MUX BYTE-POSITION = 케이스 시작 − 선택자 위치(1), SWITCH-KEY BYTE-POSITION 0 +
                    # 선택자 DOP, 빈 기본 구조 → DEFAULT-CASE(SHORT-NAME만), CASE Case_0x.. LOWER/UPPER-LIMIT = 선택자 물리값.
                    if f["selector"] not in selector_offsets: raise DomainError("MUX 선택자가 앞쪽 필드에 없어 출력이 차단되었습니다.","UNSUPPORTED_FEATURE")
                    start,sel=selector_offsets[f["selector"]]
                    if offset%8 or start%8: raise DomainError("MUX·선택자 시작이 바이트 경계가 아닙니다.","UNSUPPORTED_FEATURE")
                    mux_id=refid(f["mux_key"])+"_MUX"
                    if dictionary.find(f"MUXS/MUX[@ID='{mux_id}']") is None:
                        t=sel["type"]
                        entries=[e for e in s.rows("conversion_entries") if e["data_type_id"]==t["id"]]
                        exclusions=[e for e in s.rows("raw_exclusions") if e["data_type_id"]==t["id"]]
                        def physical(v):
                            if t["encoding"] not in ("unsigned","signed"): return str(v)
                            return convert({**t,"segments":[x for x in s.rows("conversion_segments") if x["data_type_id"]==t["id"]]},v,entries=entries,locale=s.locale,exclusions=exclusions)[1]
                        mux=child(section("MUXS"),"MUX",ID=mux_id)
                        child(mux,"SHORT-NAME","MUX_"+s.get("objects",sel["object"]["id"])["qualifier"])
                        child(mux,"LONG-NAME",f["label"])
                        child(mux,"BYTE-POSITION",(offset-start)//8)
                        key=child(mux,"SWITCH-KEY")
                        child(key,"BYTE-POSITION",0)
                        child(key,"DATA-OBJECT-PROP-REF",**{"ID-REF":refid(t["id"])})
                        if f.get("default_structure"):
                            dc=child(mux,"DEFAULT-CASE")
                            child(dc,"SHORT-NAME","Case_Default")
                            if f["default"]: child(dc,"STRUCTURE-REF",**{"ID-REF":case_structure(f["default_structure"],"Case_Default",f["default"])})
                        cases=child(mux,"CASES")
                        for (lo,hi,case_fields),sid in zip(f["cases"],f["case_structures"]):
                            c=child(cases,"CASE")
                            child(c,"SHORT-NAME",f"Case_0x{lo:X}" if lo==hi else f"Case_0x{lo:X}_0x{hi:X}")
                            child(c,"STRUCTURE-REF",**{"ID-REF":case_structure(sid,f"{f['label']} {lo}..{hi}",case_fields)})
                            child(c,"LOWER-LIMIT",physical(lo))
                            child(c,"UPPER-LIMIT",physical(hi))
                    p=child(container,"PARAM",**{f"{{{XSI}}}type":"VALUE"})
                    child(p,"SHORT-NAME",f"P_{index}")
                    child(p,"LONG-NAME",f["label"])
                    child(p,"BYTE-POSITION",start//8)
                    child(p,"DOP-REF",**{"ID-REF":mux_id})
                    continue
                if f["kind"]=="mux":
                    if f["selector"] not in keys: raise DomainError("MUX 선택자가 앞쪽 필드에 없어 출력이 차단되었습니다.","UNSUPPORTED_FEATURE")
                    if offset%8: raise DomainError("MUX 케이스 시작이 바이트 경계가 아닙니다.","UNSUPPORTED_FEATURE")
                    p=child(container,"PARAM",**{f"{{{XSI}}}type":"TABLE-STRUCT"})
                    child(p,"SHORT-NAME",f"P_{index}")
                    child(p,"LONG-NAME",f["label"])
                    child(p,"BYTE-POSITION",offset//8)
                    child(p,"TABLE-KEY-REF",**{"ID-REF":keys[f["selector"]]})
                    continue
                if f["kind"]=="data" and f["object"]["id"] in muxes and f["object"]["constant_text"] is None:
                    key_id=f"{refid(structure)}_K{index}"
                    table_id=mux_table(muxes[f["object"]["id"]],f,key_id)
                    keys[f["object"]["id"]]=key_id
                    p=child(container,"PARAM",ID=key_id,**{f"{{{XSI}}}type":"TABLE-KEY"})
                    child(p,"SHORT-NAME",f"P_{index}")
                    child(p,"LONG-NAME",f["label"])
                    child(p,"BYTE-POSITION",offset//8)
                    if f["bits"]%8 or offset%8: child(p,"BIT-POSITION",8-(offset%8)-f["bits"] if f["bits"]<8 else 0)
                    child(p,"TABLE-REF",**{"ID-REF":table_id})
                    offset+=f["bits"]
                    continue
                if f["kind"]=="fault_record" or (f["kind"]=="iteration" and any(g["kind"]=="fault_record" for g in f["item"])):
                    raise DomainError("고장 메모리 스냅샷·확장 데이터 레코드의 ODX 대응은 근거가 없어 출력이 차단되었습니다.","UNSUPPORTED_FEATURE")
                # 예약 비트·빈 DTC 표는 RESERVED PARAM(BIT-LENGTH만). 근거: UDS-ExampleEcu.pdx DtcStatusbyte의 비활성 비트,
                # 도움말 100_ODXExport "DTC table"(빈 고장 메모리의 DTC 데이터 객체 → RESERVED), 190(2.2.0 RESERVED에 코드 타입 없음).
                reserved=f["kind"]=="reserved" or (f["kind"]=="dtc" and not dtcs)
                constant=f["kind"]=="literal" or (f["kind"]=="data" and f["object"]["constant_text"] is not None)
                if f["kind"]=="data" and constant:
                    raise DomainError("데이터 객체 상수의 ODX 매핑은 추가 구현이 필요합니다.","UNSUPPORTED_FEATURE")
                p=child(container,"PARAM",**{f"{{{XSI}}}type":"RESERVED" if reserved else "CODED-CONST" if constant else "VALUE"})
                child(p,"SHORT-NAME",f"P_{index}")
                child(p,"LONG-NAME",f["label"])
                child(p,"BYTE-POSITION",offset//8)
                if f["bits"]%8 or offset%8: child(p,"BIT-POSITION",8-(offset%8)-f["bits"] if f["bits"]<8 else 0)
                if reserved: child(p,"BIT-LENGTH",f["bits"])
                elif f["kind"]=="dtc": child(p,"DOP-REF",**{"ID-REF":prefix+"DTC_DOP"})
                elif f["kind"]=="iteration":
                    # 반복 → END-OF-PDU-FIELD(BASIC-STRUCTURE = 항목 구조). 근거: UDS-ExampleEcu.pdx ListOfDTC.
                    if offset%8: raise DomainError("반복 시작이 바이트 경계가 아닙니다.","UNSUPPORTED_FEATURE")
                    item=refid(f["structure_id"])
                    if dictionary.find(f"STRUCTURES/STRUCTURE[@ID='{item}']") is None:
                        st=child(section("STRUCTURES"),"STRUCTURE",ID=item)
                        child(st,"SHORT-NAME",s.get("objects",f["structure_id"])["qualifier"])
                        child(st,"LONG-NAME",f["label"])
                        params(st,f["structure_id"])
                        field=child(section("END-OF-PDU-FIELDS"),"END-OF-PDU-FIELD",ID=item+"_EOPF")
                        child(field,"SHORT-NAME",s.get("objects",f["structure_id"])["qualifier"])
                        child(field,"LONG-NAME",f["label"])
                        child(field,"BASIC-STRUCTURE-REF",**{"ID-REF":item})
                        if f["min_items"]: child(field,"MIN-NUMBER-OF-ITEMS",f["min_items"])
                    child(p,"DOP-REF",**{"ID-REF":item+"_EOPF"})
                elif constant:
                    ct=child(p,"DIAG-CODED-TYPE",**{"BASE-DATA-TYPE":"A_UINT32",f"{{{XSI}}}type":"STANDARD-LENGTH-TYPE"})
                    if f["bits"]>32: raise DomainError("32비트 초과 고정값은 ODX 변환 범위를 벗어납니다.","UNSUPPORTED_FEATURE")
                    child(ct,"BIT-LENGTH",f["bits"])
                    child(p,"CODED-VALUE",int.from_bytes(f.get("bytes",b"\0"),"big"))
                else: child(p,"DOP-REF",**{"ID-REF":refid(f["type"]["id"])})
                offset+=f["bits"]
        if did_ids:
            sts=section("STRUCTURES")
            for did in sorted(did_ids):
                d=s.get("dids",did)
                st=child(sts,"STRUCTURE",ID=refid(did))
                child(st,"SHORT-NAME",s.get("objects",did)["qualifier"])
                child(st,"LONG-NAME",s.name(did))
                params(st,d["structure_id"])
        if dtcs:
            # 자식 순서·표시 형식은 예제 ODXEnvelope/00402011_*.odx-d(Vector 출력)를 따른다.
            dd=child(child(dictionary,"DTC-DOPS"),"DTC-DOP",ID=prefix+"DTC_DOP")
            child(dd,"SHORT-NAME","DTCs")
            ct=child(dd,"DIAG-CODED-TYPE",**{"BASE-TYPE-ENCODING":"NONE","BASE-DATA-TYPE":"A_UINT32",f"{{{XSI}}}type":"STANDARD-LENGTH-TYPE"}); child(ct,"BIT-LENGTH",24)
            child(dd,"PHYSICAL-TYPE",**{"BASE-DATA-TYPE":"A_UINT32","DISPLAY-RADIX":"HEX"})
            method=child(dd,"COMPU-METHOD"); child(method,"CATEGORY","IDENTICAL")
            dtc_list=child(dd,"DTCS")
            attributes={}
            for a in s.rows("object_attributes"): attributes.setdefault(a["object_id"],[]).append(a)
            for d in sorted(dtcs,key=lambda x:x["code"]):
                n=child(dtc_list,"DTC",ID=refid(d["id"]))
                child(n,"SHORT-NAME",s.get("objects",d["id"])["qualifier"])
                child(n,"TROUBLE-CODE",d["code"])
                child(n,"DISPLAY-TROUBLE-CODE",f"0x{d['code']:08X}")
                child(n,"TEXT",s.name(d["id"]))
                # CDD 레코드 항목(설정·해제 조건 등) → SDG. 원시값(#raw)·언어별 복제(@locale)·저작 메타데이터(@req)는 제외.
                items=[a for a in sorted(attributes.get(d["id"],[]),key=lambda a:a["key"]) if a["key"].startswith("cdd.") and "#" not in a["key"] and "@" not in a["key"]]
                if items:
                    sdgs=child(n,"SDGS")
                    for a in items:
                        name=a["key"][4:]
                        sdg=child(sdgs,"SDG")
                        cap=child(sdg,"SDG-CAPTION",ID=refid(d["id"])+"_"+re.sub(r"[^A-Za-z0-9_]","_",name))
                        child(cap,"SHORT-NAME",re.sub(r"[^A-Za-z0-9_]","_",name))
                        child(sdg,"SD",a["value_text"] or None)
        if did_ids:
            table=child(section("TABLES"),"TABLE",ID=prefix+"DIDs",SEMANTIC="DATA-ID")
            child(table,"SHORT-NAME","DIDs")
            child(table,"KEY-DOP-REF",**{"ID-REF":prefix+"DID_Key"})
            for did in sorted(did_ids):
                d=s.get("dids",did)
                row=child(table,"TABLE-ROW",ID=refid(did)+"_Row")
                child(row,"SHORT-NAME",s.get("objects",did)["qualifier"])
                child(row,"LONG-NAME",s.name(did))
                child(row,"KEY",d["identifier"])
                child(row,"STRUCTURE-REF",**{"ID-REF":refid(did)})
        units=[s.get("data_types",tid) for tid in sorted(used_types) if s.get("data_types",tid)["unit"] and s.get("data_types",tid)["conversion"]!="formula"]
        if units:
            un=child(child(dictionary,"UNIT-SPEC"),"UNITS")
            for t in units:
                unit=child(un,"UNIT",ID=refid(t["id"])+"_Unit")
                child(unit,"SHORT-NAME","Unit_"+t["id"].replace("-",""))
                child(unit,"DISPLAY-NAME",t["unit"])
        if services:
            comms=child(layer,"DIAG-COMMS")
            for service in services:
                attrs={"ADDRESSING":"PHYSICAL-OR-FUNCTIONAL" if service["functional"] and service["physical"] else "FUNCTIONAL" if service["functional"] else "PHYSICAL"}
                # SEMANTIC: CDD에서 가져온 진단 클래스 유형이 있으면 도움말 표 9(클래스 유형 → SEMANTIC)를 따른다.
                # 유형이 없으면(자체 생성 클래스) 고정 프로파일 서비스만 SID 기준 값을 쓰고, 원문 배치 서비스는 생략한다(선택 속성).
                class_id=s.get("diagnostic_instances",service["instance_id"])["class_id"]
                class_type=class_types.get(class_id)
                if service["id"] in service_semantics: semantic=service_semantics[service["id"]]  # 서비스 Semantic 속성(도움말 80_ODXExport)
                elif class_type is not None: semantic=CLASS_SEMANTIC.get(class_type)
                else: semantic={16:"SESSION",17:"ECU-RESET",34:"READ-DATA",46:"WRITE-DATA",49:"ROUTINE",62:"TESTER-PRESENT"}.get(service["sid"]) if service.get("profile","uds_fixed")=="uds_fixed" else None
                if semantic: attrs={"SEMANTIC":semantic,**attrs}
                n=child(comms,"DIAG-SERVICE",ID=refid(service["id"]),**attrs)
                child(n,"SHORT-NAME",s.get("objects",service["id"])["qualifier"])
                child(n,"LONG-NAME",s.name(service["id"]))
                own=[x for x in transitions if x["service_id"]==service["id"]]
                if own:
                    refs=child(n,"STATE-TRANSITION-REFS")
                    for x in sorted(own,key=lambda x:(x["group_id"],x["from_state_id"])):
                        child(refs,"STATE-TRANSITION-REF",**{"ID-REF":transition_id(x)})
                for t in telegrams:
                    if t["service_id"]!=service["id"]: continue
                    if t["kind"]=="request": child(n,"REQUEST-REF",**{"ID-REF":refid(t["id"])})
                    elif t["kind"]=="positive": child(child(n,"POS-RESPONSE-REFS"),"POS-RESPONSE-REF",**{"ID-REF":refid(t["id"])})
                codes=[c["code"] for c in s.rows("service_nrcs") if c["service_id"]==service["id"]]
                if codes:
                    refs=child(n,"NEG-RESPONSE-REFS")
                    for code in codes:child(refs,"NEG-RESPONSE-REF",**{"ID-REF":refid(service["id"])+f"_NRC_{code}"})
            for kind,plural,tag in (("request","REQUESTS","REQUEST"),("positive","POS-RESPONSES","POS-RESPONSE")):
                container=child(layer,plural)
                for t in telegrams:
                    if t["kind"]==kind:
                        n=child(container,tag,ID=refid(t["id"]))
                        child(n,"SHORT-NAME",kind+"_"+t["id"].replace("-",""))
                        params(n,t["structure_id"])
            if any(n["service_id"] in service_ids for n in s.rows("service_nrcs")):
                negatives=child(layer,"NEG-RESPONSES")
                for service in services:
                    for code in (c["code"] for c in s.rows("service_nrcs") if c["service_id"]==service["id"]):
                        n=child(negatives,"NEG-RESPONSE",ID=refid(service["id"])+f"_NRC_{code}")
                        child(n,"SHORT-NAME",f"NRC_{service['id'].replace('-','')}_{code}")
                        ps=child(n,"PARAMS")
                        for offset,(name,value) in enumerate((("NegativeResponse",127),("RequestSID",service["sid"]),("ResponseCode",code))):
                            p=child(ps,"PARAM",**{f"{{{XSI}}}type":"CODED-CONST"})
                            child(p,"SHORT-NAME",name);child(p,"BYTE-POSITION",offset)
                            ct=child(p,"DIAG-CODED-TYPE",**{"BASE-DATA-TYPE":"A_UINT32",f"{{{XSI}}}type":"STANDARD-LENGTH-TYPE"})
                            child(ct,"BIT-LENGTH",8);child(p,"CODED-VALUE",value)
        global_nrcs=sorted(s.rows("unsupported_service_nrcs"),key=lambda x:x["ordinal"])
        if global_nrcs:
            # 27차: CDD UNSUPPSRVNEG → GLOBAL-NEG-RESPONSE. 모양은 Vector 출력(UDS-ExampleEcu-5.0.2.pdx UNSUPPORTED_SERVICE_NR)을 따른다:
            # SID_NR(CODED-CONST 0x7F, SEMANTIC=SERVICE-ID), SID_RQ_NR(MATCHING-REQUEST-PARAM, 요청 0번째 바이트), NRC(VALUE, TEXTTABLE DOP).
            codes=[(s.get("nrcs",x["nrc_id"])["code"],s.name(x["nrc_id"])) for x in global_nrcs]
            dop=child(section("DATA-OBJECT-PROPS"),"DATA-OBJECT-PROP",ID=prefix+"NRC_DOP")
            child(dop,"SHORT-NAME","NRC_DOP")
            method=child(dop,"COMPU-METHOD");child(method,"CATEGORY","TEXTTABLE")
            scales=child(child(method,"COMPU-INTERNAL-TO-PHYS"),"COMPU-SCALES")
            for code,label in codes:
                scale=child(scales,"COMPU-SCALE")
                child(scale,"LOWER-LIMIT",code,**{"INTERVAL-TYPE":"CLOSED"});child(scale,"UPPER-LIMIT",code,**{"INTERVAL-TYPE":"CLOSED"})
                child(child(scale,"COMPU-CONST"),"VT",label)
            ct=child(dop,"DIAG-CODED-TYPE",**{"BASE-DATA-TYPE":"A_UINT32",f"{{{XSI}}}type":"STANDARD-LENGTH-TYPE"});child(ct,"BIT-LENGTH",8)
            child(dop,"PHYSICAL-TYPE",**{"BASE-DATA-TYPE":"A_UNICODE2STRING"})
            gaps,low=[],0
            for code in sorted(c for c,_ in codes):
                if code>low: gaps.append((low,code-1))
                low=code+1
            if low<=255: gaps.append((low,255))
            if gaps:
                sc=child(child(dop,"INTERNAL-CONSTR"),"SCALE-CONSTRS")
                for lo,hi in gaps:
                    c=child(sc,"SCALE-CONSTR",VALIDITY="NOT-DEFINED");child(c,"LOWER-LIMIT",lo);child(c,"UPPER-LIMIT",hi)
            response=child(child(layer,"GLOBAL-NEG-RESPONSES"),"GLOBAL-NEG-RESPONSE",ID=prefix+"UNSUPPORTED_SERVICE_NR")
            child(response,"SHORT-NAME","UNSUPPORTED_SERVICE_NR")
            ps=child(response,"PARAMS")
            p=child(ps,"PARAM",SEMANTIC="SERVICE-ID",**{f"{{{XSI}}}type":"CODED-CONST"})
            child(p,"SHORT-NAME","SID_NR");child(p,"BYTE-POSITION",0);child(p,"CODED-VALUE",127)
            ct=child(p,"DIAG-CODED-TYPE",**{"BASE-DATA-TYPE":"A_UINT32",f"{{{XSI}}}type":"STANDARD-LENGTH-TYPE"});child(ct,"BIT-LENGTH",8)
            p=child(ps,"PARAM",**{f"{{{XSI}}}type":"MATCHING-REQUEST-PARAM"})
            child(p,"SHORT-NAME","SID_RQ_NR");child(p,"BYTE-POSITION",1);child(p,"REQUEST-BYTE-POS",0);child(p,"BYTE-LENGTH",1)
            p=child(ps,"PARAM",SEMANTIC="DATA",**{f"{{{XSI}}}type":"VALUE"})
            child(p,"SHORT-NAME","NRC");child(p,"BYTE-POSITION",2);child(p,"DOP-REF",**{"ID-REF":prefix+"NRC_DOP"})
        if transitions:
            # 전이가 있는 상태 그룹만 차트로 낸다. 같은 전이는 한 번 만들고 여러 서비스가 참조한다(도움말 180).
            charts=child(layer,"STATE-CHARTS")
            for gid in sorted({x["group_id"] for x in transitions},key=lambda g:(s.get("state_groups",g)["kind"]!="session",s.get("objects",g)["qualifier"])):
                g=s.get("state_groups",gid)
                if g["kind"] not in ("session","security"):
                    raise DomainError(f"상태 그룹 종류 {g['kind']}의 ODX STATE-CHART 대응은 확인하지 못해 출력이 차단되었습니다.","UNSUPPORTED_FEATURE")
                states=sorted((st for st in s.rows("states") if st["group_id"]==gid),key=lambda st:st["code"])
                chart=child(charts,"STATE-CHART",ID=refid(gid))
                child(chart,"SHORT-NAME",s.get("objects",gid)["qualifier"])
                child(chart,"LONG-NAME",s.name(gid))
                child(chart,"SEMANTIC",g["kind"].upper())
                st_list=child(chart,"STATE-TRANSITIONS")
                seen=set()
                for x in sorted((x for x in transitions if x["group_id"]==gid),key=lambda x:(x["from_state_id"],x["to_state_id"])):
                    key_=(x["from_state_id"],x["to_state_id"])
                    if key_ in seen: continue
                    seen.add(key_)
                    src,dst=(s.get("objects",i)["qualifier"] for i in key_)
                    tr=child(st_list,"STATE-TRANSITION",ID=transition_id(x))
                    child(tr,"SHORT-NAME",f"{src}_to_{dst}")
                    child(tr,"SOURCE-SNREF",**{"SHORT-NAME":src})
                    child(tr,"TARGET-SNREF",**{"SHORT-NAME":dst})
                child(chart,"START-STATE-SNREF",**{"SHORT-NAME":s.get("objects",g["initial_state_id"])["qualifier"]})
                sts=child(chart,"STATES")
                for st in states:
                    node=child(sts,"STATE",ID=refid(st["id"]))
                    child(node,"SHORT-NAME",s.get("objects",st["id"])["qualifier"])
                    child(node,"LONG-NAME",s.name(st["id"]))
        patterns=variant_pattern_params(s,vid)
        if patterns:
            # 27차: 변형 식별(CDD IDENTIFICATIONSET) → ECU-VARIANT-PATTERN. DIAG-LAYER 요소 뒤(ECU-VARIANT 고유 요소)에 온다.
            container=child(layer,"ECU-VARIANT-PATTERNS")
            for conditions in patterns:
                matching=child(child(container,"ECU-VARIANT-PATTERN"),"MATCHING-PARAMETERS")
                for service_name,param_name,expected in conditions:
                    m=child(matching,"MATCHING-PARAMETER")
                    child(m,"EXPECTED-VALUE",expected)
                    child(m,"DIAG-COMM-SNREF",**{"SHORT-NAME":service_name})
                    child(m,"OUT-PARAM-IF-SNREF",**{"SHORT-NAME":param_name})
        # 사전 요소 순서: Vector 출력(UDS-ExampleEcu.pdx: DTC-DOPS, ENV-DATA-DESCS, DATA-OBJECT-PROPS, STRUCTURES, END-OF-PDU-FIELDS, UNIT-SPEC;
        # ODXEnvelope 0112: DATA-OBJECT-PROPS, STRUCTURES, UNIT-SPEC, TABLES). 반복 구조는 전문을 만들 때 추가되므로 마지막에 정렬한다.
        order=["DTC-DOPS","DATA-OBJECT-PROPS","STRUCTURES","END-OF-PDU-FIELDS","MUXS","UNIT-SPEC","TABLES"]
        dictionary[:]=sorted(dictionary,key=lambda n:order.index(n.tag))
    return root
