"""정확한 수치, 고정 길이 전문, 불변 스냅샷의 진단 규칙."""
from dataclasses import dataclass, asdict
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from collections import defaultdict
import re
import struct
import unicodedata


class DomainError(Exception):
    def __init__(self, message, code="VALIDATION_FAILED"):
        super().__init__(message)
        self.code = code


def key(value):
    return unicodedata.normalize("NFC", str(value)).casefold()


def integer(value, minimum=None, maximum=None):
    try:
        s = str(value).strip()
        result = int(s, 16 if s.lower().startswith(("0x", "-0x")) else 10)
    except (ValueError, TypeError):
        raise DomainError("정수 또는 0x로 시작하는 16진수를 입력하세요.")
    if minimum is not None and result < minimum or maximum is not None and result > maximum:
        raise DomainError(f"허용 범위: {minimum} ~ {maximum}")
    return result


def decimal_text(value):
    try:
        d = Decimal(str(value).strip())
        if not d.is_finite():
            raise InvalidOperation
        s = format(d, "f")
        left, _, right = s.lstrip("-").partition(".")
        if len(left.lstrip("0")) > 18 or len(right.rstrip("0")) > 18 or len(d.as_tuple().digits) > 28:
            raise InvalidOperation
        return s.rstrip("0").rstrip(".") if "." in s else s
    except (InvalidOperation, ValueError):
        raise DomainError("계수는 유효숫자 28자리, 정수부·소수부 각각 18자리 이내여야 합니다.")


def ratio(value):
    """계수 텍스트 → Fraction. 10진수("0.0625") 또는 분수("359/255")."""
    text = str(value).strip()
    try:
        if "/" in text:
            num, _, den = text.partition("/")
            result = Fraction(integer(num)) / Fraction(integer(den))
        else:
            result = Fraction(Decimal(decimal_text(text)))
    except (ZeroDivisionError, ValueError, InvalidOperation, DomainError):
        raise DomainError("계수는 10진수 또는 정수 분수(예: 359/255)여야 합니다.")
    return result


def terminating(value):
    """분모가 2와 5만으로 이루어지면 유한 소수로 정확히 쓸 수 있다."""
    den = value.denominator
    for p in (2, 5):
        while den % p == 0: den //= p
    return den == 1


def fraction_decimal(value):
    """유한 소수 Fraction → 정확한 10진 텍스트."""
    den, twos, fives = value.denominator, 0, 0
    while den % 2 == 0: den //= 2; twos += 1
    while den % 5 == 0: den //= 5; fives += 1
    if den != 1: raise DomainError("유한 소수로 표현할 수 없는 값입니다.")
    places = max(twos, fives)
    scaled = abs(value.numerator) * (2 ** (places - twos)) * (5 ** (places - fives))
    digits = str(scaled).zfill(places + 1)
    text = (digits[:-places] + "." + digits[-places:]).rstrip("0").rstrip(".") if places else digits
    return ("-" if value < 0 else "") + text


def ratio_text(value):
    """계수의 저장 형식: 유한 소수는 10진수, 아니면 기약분수 "p/q". 반올림하지 않는다."""
    r = value if isinstance(value, Fraction) else ratio(value)
    if terminating(r): return decimal_text(fraction_decimal(r))
    if max(abs(r.numerator), r.denominator) >= 10 ** 18: raise DomainError("분수 계수의 분자·분모는 18자리 이내여야 합니다.")
    return f"{r.numerator}/{r.denominator}"


def physical_text(value):
    """물리값 표시. 유한 소수는 정확히, 아니면 유효숫자 12자리 근삿값에 '…'를 붙인다(저장·역변환은 정확한 분수)."""
    if terminating(value): return fraction_decimal(value)
    with localcontext() as ctx:
        ctx.prec = 12
        text = format(Decimal(value.numerator) / Decimal(value.denominator), "f")
        return (text.rstrip("0").rstrip(".") if "." in text else text) + "…"


def qualifier(value):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise DomainError("Qualifier는 영문 또는 _로 시작하며 영문·숫자·_만 사용할 수 있습니다.")
    return value


QUALIFIER_MAX = 128
# 이름을 Qualifier로 바꿀 때의 로마자 대체([QUAL-R050]: ä → ae). 그 밖의 발음 기호는 NFKD로 떼어 낸다.
_ROMAN = {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss", "æ": "ae", "Æ": "Ae", "ø": "oe", "Ø": "Oe", "å": "aa", "Å": "Aa"}
# 이름에 영문·숫자가 없을 때(한글만 있는 이름 등, 결정 D3 ①)의 종류 접두어
QUALIFIER_KIND_PREFIX = {"dtc": "DTC", "did": "DID", "data_object": "DataObject", "data_type": "DataType", "service": "Service",
                         "diagnostic_instance": "Instance", "diagnostic_class": "Class", "variant": "Variant", "state": "State",
                         "state_group": "StateGroup", "structure": "Structure", "ecu": "ECU", "nrc": "NRC", "protocol_service": "ProtocolService",
                         "job_container": "Job"}


def make_qualifier(name, kind=None, ident=None, max_length=QUALIFIER_MAX, case="name"):
    """이름에서 Qualifier를 만든다([QUAL-R050]·[QUAL-R120]·[QUAL-R130]).
    로마자로 바꾸고(ä → ae), 바꿀 수 없는 글자는 _, 연속 _는 하나, 앞뒤 _ 제거, 숫자로 시작하면 앞에 _, 길이 제한, 대소문자(name/upper/lower).
    남는 글자가 없으면(한글만 있는 이름) 종류 + 식별 번호(DTC_401011, DID_F190), 번호가 없으면 종류 + 순번(ident)이다(결정 D3 ①).
    같은 이름이면 같은 Qualifier가 나온다. 중복 번호는 붙이지 않는다."""
    text = "".join(_ROMAN.get(ch, ch) for ch in str(name or ""))
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9_]", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        prefix = QUALIFIER_KIND_PREFIX.get(kind or "", "Object")
        if isinstance(ident, int):
            digits = 6 if kind == "dtc" else 4 if kind == "did" else 2 if kind in ("nrc", "state") else 0
            text = f"{prefix}_{ident:0{digits}X}" if digits else f"{prefix}_{ident}"
        else:
            text = f"{prefix}_{ident}" if ident not in (None, "") else prefix
    if text[0].isdigit(): text = "_" + text
    if case == "upper": text = text.upper()
    elif case == "lower": text = text.lower()
    limit = max(1, min(int(max_length or QUALIFIER_MAX), QUALIFIER_MAX))
    text = text[:limit].rstrip("_") or text[:limit]
    return text


def qualifier_follows(name, value, kind=None, ident=None):
    """[QUAL-R020] Qualifier가 아직 이름에서 만든 값과 같은지(같으면 이름을 바꿀 때 따라 바꾼다)."""
    return bool(value) and value == make_qualifier(name, kind, ident)


# CANdela "invalid values" 종류. ODX 대응은 도움말 110_ODXExport Table 40 (interchange.EXCLUSION_VALIDITY).
EXCLUSION_KINDS = ("undef", "noSignal", "invalidSignal", "notImplemented", "unexpectedResult")


def exclusion_intervals(exclusions):
    """언어별 행을 예외 하나로 묶는다: {ordinal: (lower, upper, kind, {locale: label})}."""
    result = {}
    for e in exclusions:
        lo, hi = integer(e["lower_raw"]), integer(e["upper_raw"])
        item = result.setdefault(e["ordinal"], (lo, hi, e["kind"], {}))
        if item[:3] != (lo, hi, e["kind"]): raise DomainError("같은 예외의 언어별 범위·종류가 다릅니다.")
        if e["label"] or e["locale"]: item[3][e["locale"]] = e["label"]
    return result


# schema 13: IEEE 754 부동소수 원시값의 최대 크기(ODX Vector 출력 VehicleSpeed의 빈 구간 끝값 ±3.402823e+038).
FLOAT_MAX = {32: Decimal("3.402823e+038"), 64: Decimal("1.797693e+308")}
FLOAT_LIMIT_TEXT = {32: ("-3.402823e+038", "3.402823e+038"), 64: ("-1.797693e+308", "1.797693e+308")}
SEGMENT_CONVERSIONS = ("scale_linear", "tab_intp")


def check_segments(t, segments):
    """schema 13 구간별 선형·특성 곡선 검사. 구간은 겹치지 않고 원시 범위 안, 점은 원시값이 오름차순."""
    mode = t["conversion"]
    rows = sorted(segments, key=lambda x: x["ordinal"])
    if not rows: raise DomainError("구간별 선형·특성 곡선 변환에는 구간(점)이 하나 이상 필요합니다.")
    if mode == "scale_linear":
        spans = []
        for x in rows:
            if x.get("upper_raw") is None or x.get("factor_text") is None: raise DomainError("구간별 선형 변환의 구간에는 상한·계수·오프셋이 필요합니다.")
            lo, hi = raw_bounds(t, x["lower_raw"]), raw_bounds(t, x["upper_raw"])
            if hi < lo: raise DomainError("구간의 상한이 하한보다 작습니다.")
            ratio(x["factor_text"]); decimal_text(x["offset_text"])
            spans.append((lo, hi))
        spans.sort()
        if any(a[1] >= b[0] for a, b in zip(spans, spans[1:])): raise DomainError("변환 구간이 겹칩니다.")
    else:
        points = []
        for x in rows:
            if x.get("value_text") is None or x.get("upper_raw") is not None: raise DomainError("특성 곡선의 점에는 원시값과 물리값만 있습니다.")
            points.append(raw_bounds(t, x["lower_raw"])); decimal_text(x["value_text"])
        if any(a >= b for a, b in zip(points, points[1:])): raise DomainError("특성 곡선의 점은 원시값이 커지는 순서여야 합니다.")


def check_type(t, entries=(), exclusions=(), segments=None):
    enc = t["encoding"]
    if enc == "float":
        # schema 13: IEEE 754. 근거: Door VehicleSpeed(enc=flt, 32비트) ↔ Vector 출력 A_FLOAT32.
        if integer(t["bit_length"], 32, 64) not in (32, 64): raise DomainError("부동소수 타입은 32비트 또는 64비트입니다.")
        if t["conversion"] not in ("identity", "linear"): raise DomainError("부동소수 타입은 동일값·선형 변환만 지원합니다.")
    elif enc in ("signed", "unsigned"):
        integer(t["bit_length"], 1, 64)
    elif enc == "bytes" and t.get("array_length") is None:
        # 가변 바이트 필드(schema 9): PDU 끝까지, 최소 min_length·최대 max_length(없으면 제한 없음) 바이트.
        low = integer(t.get("min_length"), 0, 1048576)
        if t.get("max_length") is not None and integer(t["max_length"], 0, 1048576) < low:
            raise DomainError("최대 길이가 최소 길이보다 작습니다.")
    elif enc == "bcd":
        # packed BCD(schema 9): 한 자리 4비트, 32비트(8자리)까지, 동일값 변환.
        bits = integer(t["bit_length"], 4, 32)
        if bits % 4: raise DomainError("BCD 비트 길이는 4의 배수여야 합니다.")
        if t["conversion"] != "identity": raise DomainError("BCD 타입은 동일값 변환만 지원합니다.")
    elif enc in ("ascii", "bytes"):
        integer(t["array_length"], 1, 1048576)
        if t.get("min_length") is not None: raise DomainError("최소 길이는 가변 바이트 필드에만 지정합니다.")
    elif enc == "packet":
        if not t.get("structure_id"):
            raise DomainError("Packet 구조가 필요합니다.")
    else:
        raise DomainError("지원하지 않는 인코딩입니다.")
    if t["conversion"] not in ("identity", "linear", "text_table", "formula", *SEGMENT_CONVERSIONS):
        raise DomainError("지원하지 않는 변환입니다.")
    if t["conversion"] in SEGMENT_CONVERSIONS:
        if enc not in ("unsigned", "signed"): raise DomainError("구간별 선형·특성 곡선 변환은 정수 타입에만 지정할 수 있습니다.")
        if segments is not None: check_segments(t, segments)
    if t.get("leading_length_bits") is not None:
        if enc != "bytes" or t.get("array_length") is not None: raise DomainError("앞 길이 바이트는 가변 바이트 필드에만 지정합니다.")
        if t["leading_length_bits"] not in (8, 16, 24, 32): raise DomainError("앞 길이 바이트는 1~4바이트입니다.")
        if t.get("max_length") is not None and t["max_length"] >= 1 << t["leading_length_bits"]: raise DomainError("최대 길이가 앞 길이 바이트로 나타낼 수 있는 값보다 큽니다.")
    if t["conversion"] == "formula":
        # schema 12: CDD FORMULADT 수식 원문. 정수 타입에만, 계산하지 않는다(ODX는 IDENTICAL + 수식 설명).
        if enc not in ("unsigned", "signed"): raise DomainError("수식 변환은 정수 타입에만 지정할 수 있습니다.")
        if not str(t.get("formula_expression") or "").strip(): raise DomainError("수식 변환에는 수식(coded→phys)이 필요합니다.")
    if t["conversion"] == "linear":
        if ratio(t["factor_text"]) == 0:
            raise DomainError("선형 계수는 0일 수 없습니다.")
        decimal_text(t["offset_text"])
    lower, upper = t.get("raw_lower"), t.get("raw_upper")
    if lower is not None or upper is not None:
        if t["conversion"] != "linear": raise DomainError("원시값 유효 범위는 선형 변환에만 지정할 수 있습니다.")
        lo = raw_bounds(t, lower) if lower is not None else None
        hi = raw_bounds(t, upper) if upper is not None else None
        if lo is not None and hi is not None and hi < lo: raise DomainError("유효 범위의 상한이 하한보다 작습니다.")
    intervals = sorted((lo, hi, kind) for lo, hi, kind, _ in exclusion_intervals(exclusions).values())
    for lo, hi, kind in intervals:
        if t["encoding"] not in ("unsigned", "signed", "float"): raise DomainError("예외값은 정수·부동소수 타입에만 지정할 수 있습니다.")
        if kind not in EXCLUSION_KINDS: raise DomainError(f"예외 종류 미지원: {kind}")
        if hi < lo: raise DomainError("예외 범위의 상한이 하한보다 작습니다.")
        raw_bounds(t, lo); raw_bounds(t, hi)
    if any(a[1] >= b[0] for a, b in zip(intervals, intervals[1:])): raise DomainError("예외값 범위가 겹칩니다.")
    ranges = {}
    for e in entries:
        lo, hi = integer(e["lower_raw"]), integer(e["upper_raw"])
        if hi < lo:
            raise DomainError("열거 범위의 상한이 하한보다 작습니다.")
        raw_bounds(t, lo)
        raw_bounds(t, hi)
        ranges.setdefault(e["locale"], []).append((lo, hi))
    for intervals in ranges.values():
        intervals.sort()
        if any(a[1] >= b[0] for a, b in zip(intervals, intervals[1:])):
            raise DomainError("열거값 범위가 겹칩니다.")


def raw_bounds(t, value):
    bits = t["bit_length"]
    if t["encoding"] == "float":
        # 부동소수 원시값: 10진수 글자(정수면 int). 범위는 IEEE 754 최대 크기.
        try: number = Decimal(str(value).strip())
        except (InvalidOperation, ValueError): raise DomainError(f"부동소수 값이 아닙니다: {value}")
        if not number.is_finite() or abs(number) > FLOAT_MAX[bits]: raise DomainError(f"부동소수 {bits}비트 범위를 벗어났습니다: {value}")
        return int(number) if number == number.to_integral_value() else number
    lo = -(1 << (bits - 1)) if t["encoding"] == "signed" else 0
    hi = (1 << (bits - (t["encoding"] == "signed"))) - 1
    return integer(value, lo, hi)


def exclusion_label(t, raw, exclusions, locale):
    for lo, hi, kind, labels in exclusion_intervals(exclusions).values():
        if lo <= raw <= hi:
            for key in (locale, "en-US", *sorted(labels)):
                if labels.get(key): return labels[key]
            return f"<{kind}>"
    return None


def in_range(t, raw):
    lower, upper = t.get("raw_lower"), t.get("raw_upper")
    return (lower is None or raw >= integer(lower)) and (upper is None or raw <= integer(upper))


def convert(t, value, physical=False, entries=(), locale="ko-KR", exclusions=()):
    with localcontext() as ctx:
        ctx.prec = 80
        if t["encoding"] == "bcd":
            digits = t["bit_length"] // 4
            number = integer(value, 0, 10 ** digits - 1)
            return str(number), str(number)
        if t["encoding"] not in ("unsigned", "signed", "float"):
            return str(value), str(value)
        mode = t["conversion"]
        if physical and mode == "tab_intp":
            raise DomainError("특성 곡선 타입은 물리값에서 원시값을 계산하지 않습니다. 원시값으로 입력하세요.")
        if physical and mode == "formula":
            # 수식은 계산하지 않는다(역수식 없음). 원시값으로 입력한다.
            raise DomainError("수식 변환 타입은 물리값을 계산하지 않습니다. 원시값으로 입력하세요.")
        if physical:
            if mode == "text_table":
                matches = {e["lower_raw"] for e in entries if e["label"] == str(value) and e["lower_raw"] == e["upper_raw"]}
                if len(matches) != 1:
                    raise DomainError("단일 원시값에 대응하는 열거 항목이 필요합니다.")
                raw = integer(matches.pop())
            elif any(lo == hi and str(value) in labels.values() for lo, hi, _, labels in exclusion_intervals(exclusions).values()):
                # 단일 원시값 예외의 라벨(예: "not available")을 물리값으로 입력한 경우
                raw = next(lo for lo, hi, _, labels in exclusion_intervals(exclusions).values() if lo == hi and str(value) in labels.values())
            else:
                try:
                    # 정확한 유리수 역변환(분수 계수 포함). 물리값은 10진수 또는 분수로 입력할 수 있다.
                    d = ratio(value) if mode == "linear" or "/" in str(value) else Fraction(Decimal(str(value)))
                    if mode == "linear":
                        d = (d - ratio(t["offset_text"])) / ratio(t["factor_text"])
                    elif mode == "scale_linear":
                        # 구간마다 역변환해 그 구간 안의 정수 원시값이 되는 첫 구간을 쓴다.
                        found = None
                        for x in sorted(t.get("segments") or [], key=lambda x: integer(x["lower_raw"])):
                            factor = ratio(x["factor_text"])
                            if factor == 0: continue
                            c = (d - ratio(x["offset_text"])) / factor
                            if c.denominator == 1 and integer(x["lower_raw"]) <= c <= integer(x["upper_raw"]): found = c; break
                        if found is None: raise InvalidOperation
                        d = found
                    if t["encoding"] == "float":
                        raw = int(d) if d.denominator == 1 else Decimal(d.numerator) / Decimal(d.denominator)
                    else:
                        if d.denominator != 1:
                            raise InvalidOperation
                        raw = int(d)
                except (InvalidOperation, ValueError, ZeroDivisionError, DomainError):
                    raise DomainError("물리값이 정확한 정수 원시값으로 변환되지 않습니다." if t["encoding"] != "float" else "물리값을 원시값으로 변환하지 못했습니다.")
        else:
            raw = raw_bounds(t, value) if t["encoding"] == "float" else integer(value)
        raw_bounds(t, raw)
        special = exclusion_label(t, raw, exclusions, locale)
        if special is not None:
            return str(raw), special
        if not in_range(t, raw):
            raise DomainError(f"원시값 {raw}은 유효 범위({t.get('raw_lower') if t.get('raw_lower') is not None else '최소'}~{t.get('raw_upper') if t.get('raw_upper') is not None else '최대'}) 밖입니다.")
        if mode == "linear":
            if "/" not in t["factor_text"] and "/" not in t["offset_text"]:
                result = format(Decimal(raw) * Decimal(t["factor_text"]) + Decimal(t["offset_text"]), "f")
            else:
                result = physical_text(Fraction(raw) * ratio(t["factor_text"]) + ratio(t["offset_text"]))
        elif mode == "scale_linear":
            hit = next((x for x in t.get("segments") or [] if integer(x["lower_raw"]) <= raw <= integer(x["upper_raw"])), None)
            if hit is None: raise DomainError(f"원시값 {raw}이 어느 변환 구간에도 없습니다.")
            result = physical_text(raw * ratio(hit["factor_text"]) + ratio(hit["offset_text"]))
        elif mode == "tab_intp":
            points = sorted(((integer(x["lower_raw"]), Fraction(Decimal(x["value_text"]))) for x in t.get("segments") or []))
            if not points or not points[0][0] <= raw <= points[-1][0]: raise DomainError(f"원시값 {raw}이 특성 곡선 범위 밖입니다.")
            for (x0, y0), (x1, y1) in zip(points, points[1:] + [points[-1]]):
                if x0 <= raw <= x1:
                    result = physical_text(y0 if x1 == x0 else y0 + (y1 - y0) * (raw - x0) / (x1 - x0)); break
        elif mode == "text_table":
            hits = [e for e in entries if integer(e["lower_raw"]) <= raw <= integer(e["upper_raw"])]
            hits.sort(key=lambda e: (e["locale"] != locale, e["locale"] != "en-US", e["locale"]))
            result = hits[0]["label"] if hits else str(raw)
        else:
            result = str(raw)
        return str(raw), result


@dataclass(frozen=True)
class Issue:
    rule_id: str
    message: str
    object_id: str | None = None
    field: str = ""
    severity: str = "error"
    remedy: str = "해당 객체의 속성을 수정하세요."
    blocks_export: bool = True


RECORD_LABELS = {"sr": "스냅샷 레코드 번호", "sd": "스냅샷 레코드 데이터", "edr": "확장 데이터 레코드 번호", "ed": "확장 데이터 레코드"}


class Snapshot:
    def __init__(self, tables, revision=0, locale="ko-KR"):
        self.tables = tables
        self.revision = revision
        self.locale = locale
        self.indices = {k: {r["id"]: r for r in v if "id" in r} for k, v in tables.items()}
        self.text_index=defaultdict(list)
        self.member_index=defaultdict(list)
        for r in self.rows("localized_texts"):self.text_index[(r["object_id"],r["field"])].append(r)
        for r in self.rows("structure_members"):self.member_index[r["structure_id"]].append(r)
        for values in self.member_index.values():values.sort(key=lambda r:r["ordinal"])
        # schema 10: MUX 자리의 선택자·케이스(구조 멤버 (structure_id, ordinal) 기준)
        self.mux_index={(r["structure_id"],r["ordinal"]):r for r in self.rows("muxes")}
        self.did_structures={r["structure_id"] for r in self.rows("dids")}  # 30차: DID 안의 MUX는 ODX MUX DOP, 서비스 전문의 MUX는 TABLE
        self.mux_case_index=defaultdict(list)
        for r in self.rows("mux_cases"):self.mux_case_index[(r["structure_id"],r["ordinal"])].append(r)
        for values in self.mux_case_index.values():values.sort(key=lambda r:r["case_ordinal"])

    def rows(self, table):
        return self.tables.get(table, [])

    def get(self, table, object_id):
        try:
            return self.indices[table][object_id]
        except KeyError:
            raise DomainError(f"필수 참조가 없습니다: {table} / {object_id}", "BROKEN_REFERENCE")

    def texts(self, object_id, field="name"):
        return self.text_index[(object_id,field)]

    def name(self, object_id, field="name"):
        values = sorted(self.texts(object_id, field), key=lambda r: (r["locale"] != self.locale, r["locale"] != "en-US", r["locale"]))
        return values[0]["value"] if values else (self.indices.get("objects", {}).get(object_id, {}).get("qualifier", "") if field=="name" else "")

    def members(self, structure_id):
        return self.member_index[structure_id]

    def flatten(self, structure_id, stack=()):
        if structure_id in stack or len(stack) > 128:
            raise DomainError("데이터 구조에 순환 또는 과도한 중첩이 있습니다.")
        stack = (*stack, structure_id)
        result = []
        for m in self.members(structure_id):
            kind = m["kind"]
            if kind == "literal":
                result.append({"kind": kind, "bits": len(m["literal_bytes"]) * 8, "bytes": m["literal_bytes"], "label": "고정값"})
            elif kind == "reserved":
                result.append({"kind": kind, "bits": m["reserved_bits"], "label": m.get("label") or "Reserved"})
            elif kind == "fault_record":
                role = m["record_role"]
                # 번호(sr/edr)는 8비트, 데이터(sd/ed)는 번호·DTC에 따라 길이가 정해지는 가변 자리(고정 배치 길이 0).
                result.append({"kind": "fault_record", "role": role, "bits": 8 if role in ("sr", "edr") else 0, "variable": role in ("sd", "ed"),
                               "label": RECORD_LABELS[role]})
            elif kind == "structure":
                nested = self.flatten(m["nested_structure_id"], stack)
                if self.get("structures", m["nested_structure_id"])["kind"] == "bitfield":
                    # 비트필드는 첫 멤버가 최하위 비트다(CDD STRUCT, Vector ODX BIT-POSITION 0부터). 전문은 상위 비트부터 쌓으므로 순서를 뒤집는다.
                    if sum(f["bits"] for f in nested) % 8:
                        raise DomainError("비트필드 크기가 바이트 단위가 아닙니다.")
                    nested = [{**f, "bitfield": True} for f in reversed(nested)]
                result.extend(nested)
            elif kind == "dtc":
                result.append({"kind": "dtc", "bits": 24, "label": "DTC"})
            elif kind == "mux":
                # 선택자 값에 따라 케이스 구조가 이어진다(가변 길이, 전문의 마지막). 케이스·기본 구조는 미리 평탄화해 둔다.
                mux = self.mux_index.get((m["structure_id"], m["ordinal"]))
                if mux is None: raise DomainError("MUX 정의가 없습니다.")
                cases = [(int(c["lower_raw"]), int(c["upper_raw"]), self.flatten(c["case_structure_id"], stack))
                         for c in self.mux_case_index[(m["structure_id"], m["ordinal"])]]
                default = self.flatten(mux["default_structure_id"], stack) if mux["default_structure_id"] else None
                result.append({"kind": "mux", "bits": 0, "variable": True, "selector": mux["selector_object_id"], "cases": cases,
                               "default": default, "label": f"MUX (선택자 {self.name(mux['selector_object_id'])})",
                               # 30차: ODX 출력(TABLE·STRUCTURE)용 케이스 구조 ID
                               "case_structures": [c["case_structure_id"] for c in self.mux_case_index[(m["structure_id"], m["ordinal"])]],
                               "default_structure": mux["default_structure_id"], "mux_key": f"{m['structure_id']}_{m['ordinal']}",
                               "in_did": m["structure_id"] in self.did_structures})
            elif kind == "iteration":
                # PDU 끝까지 반복. 고정 길이 배치에는 길이 0으로 두고, 항목 배치는 item에 둔다(ODX END-OF-PDU-FIELD).
                item = self.flatten(m["nested_structure_id"], stack)
                if any(f["kind"] == "iteration" for f in item): raise DomainError("반복 항목 안에 반복을 둘 수 없습니다.")
                result.append({"kind": "iteration", "bits": 0, "label": self.name(m["nested_structure_id"]), "structure_id": m["nested_structure_id"],
                               "min_items": m["min_items"], "item": item, "item_bits": sum(f["bits"] for f in item)})
            elif kind in ("did_data", "did_with_identifier"):
                did = self.get("dids", m["did_id"])
                if kind == "did_with_identifier":
                    result.append({"kind": "literal", "bits": 16, "bytes": did["identifier"].to_bytes(2, "big"), "label": "DID"})
                result.extend(self.flatten(did["structure_id"], stack))
            else:
                obj = self.get("data_objects", m["data_object_id"])
                typ = self.get("data_types", obj["data_type_id"])
                if typ["encoding"] == "packet":
                    result.extend(self.flatten(typ["structure_id"], stack))
                elif typ["array_length"] is None:
                    # 가변 바이트 필드: 고정 배치 길이 0, PDU 끝까지(ODX MIN-MAX-LENGTH-TYPE END-OF-PDU).
                    # schema 13: 앞 길이 바이트가 있으면 길이가 스스로 정해지므로 전문 가운데에도 둘 수 있다(leading, ODX LEADING-LENGTH-INFO-TYPE).
                    result.append({"kind": "data", "bits": 0, "variable": True, "min_bytes": typ["min_length"], "max_bytes": typ.get("max_length"), "object": obj, "type": typ, "label": self.name(obj["id"]),
                                   **({"leading_bits": typ["leading_length_bits"]} if typ.get("leading_length_bits") else {})})
                else:
                    result.append({"kind": "data", "bits": typ["bit_length"] * typ["array_length"], "object": obj, "type": typ, "label": self.name(obj["id"])})
        return result

    def encode(self, structure_id, values=None):
        """전문 바이트와 배치. 반복(iteration)은 최소 반복 수만큼 항목을 넣는다. DTC 값은 values["dtc"]["value"](16진)."""
        values = values or {}
        acc, total, layout = 0, 0, []
        fields = []
        for f in self.flatten(structure_id):
            if f["kind"] == "iteration":
                layout.append({"name": f"{f['label']} (반복 {f['min_items']}회 이상, PDU 끝까지)", "bit_offset": sum(x["bits"] for x in fields), "bits": 0, "physical": ""})
                fields.extend(f["item"] * f["min_items"])
            else: fields.append(f)
        selected = {}  # 데이터 객체 → 원시 정수값(MUX 선택자 조회용)
        for index, f in enumerate(fields):
            bits = f["bits"]
            if f.get("variable") and not f.get("leading_bits") and f["kind"] in ("data", "mux") and index != len(fields) - 1:
                raise DomainError(f"{f['label']}: 가변 길이 필드는 전문 마지막에만 둘 수 있습니다.")
            if f["kind"] == "mux":
                # 선택자 값에 맞는 케이스(없으면 기본 구조)의 필드를 이어서 인코딩한다.
                if f["selector"] not in selected: raise DomainError(f"{f['label']}: 선택자 값이 앞쪽 필드에 없습니다.")
                value = selected[f["selector"]]
                chosen = next((case for lo, hi, case in f["cases"] if lo <= value <= hi), f["default"])
                if chosen is None: raise DomainError(f"{f['label']}: 선택자 값 {value}에 맞는 케이스가 없습니다.")
                layout.append({"name": f"{f['label']} = {value}", "bit_offset": total, "bits": 0, "physical": ""})
                fields.extend(chosen)
                continue
            if bits > 8 * 1048576 or total + bits > 8 * 1048576:
                raise DomainError("전문은 1 MiB 이내여야 합니다.")
            physical_value = ""
            if f["kind"] == "literal":
                encoded = int.from_bytes(f["bytes"], "big")
            elif f["kind"] == "reserved":
                encoded = 0
            elif f["kind"] == "fault_record":
                if f["variable"]: raise DomainError(f"{f['label']}: 가변 길이 레코드 데이터는 미리보기할 수 없습니다.")
                encoded = integer(values.get(f["role"], {}).get("value", ""), 0, 255) if values.get(f["role"], {}).get("value") not in (None, "") else None
                if encoded is None: raise DomainError(f"{f['label']}: 값을 입력하세요.")
            elif f["kind"] == "dtc":
                text = values.get("dtc", {}).get("value")
                if text in (None, ""): raise DomainError("DTC: 값을 입력하세요.")
                try: encoded = int(str(text).strip().lower().removeprefix("0x"), 16)
                except ValueError: raise DomainError("DTC: 24비트 16진 값을 입력하세요.")
                if not 0 <= encoded <= 0xFFFFFF: raise DomainError("DTC: 24비트 16진 값을 입력하세요.")
            else:
                obj, t = f["object"], f["type"]
                supplied = values.get(obj["id"], {})
                value = obj["constant_text"] if obj["constant_text"] is not None else supplied.get("value", obj["default_text"])
                if value is None:
                    raise DomainError(f"{f['label']}: 값 또는 기본값을 입력하세요.")
                if t["encoding"] == "bcd":
                    raw, physical_value = convert(t, value)
                    encoded = int(raw, 16)  # 10진 자릿수를 4비트씩 담는다(packed BCD).
                elif t["encoding"] == "float":
                    # schema 13: IEEE 754(Vector 출력 A_FLOAT32/64). 원시값은 10진수 글자.
                    exclusions = [e for e in self.rows("raw_exclusions") if e["data_type_id"] == t["id"]]
                    raw, physical_value = convert(t, value, supplied.get("physical", False) and obj["constant_text"] is None, (), self.locale, exclusions)
                    packed = struct.pack(("<" if t["byte_order"] == "little" else ">") + ("f" if bits == 32 else "d"), float(Decimal(raw)))
                    if total % 8: raise DomainError("부동소수는 바이트 경계에 정렬해야 합니다.")
                    encoded = int.from_bytes(packed, "big")
                elif t["encoding"] in ("unsigned", "signed"):
                    entries = [e for e in self.rows("conversion_entries") if e["data_type_id"] == t["id"]]
                    exclusions = [e for e in self.rows("raw_exclusions") if e["data_type_id"] == t["id"]]
                    if t["conversion"] in SEGMENT_CONVERSIONS:
                        t = {**t, "segments": [x for x in self.rows("conversion_segments") if x["data_type_id"] == t["id"]]}
                    raw, physical_value = convert(t, value, supplied.get("physical", False) and obj["constant_text"] is None, entries, self.locale, exclusions)
                    encoded = int(raw) % (1 << bits)
                    selected[obj["id"]] = int(raw)
                    if t["byte_order"] == "little" and bits > 8:
                        if bits % 8 or total % 8:
                            raise DomainError("Little endian 정수는 바이트 경계에 정렬해야 합니다.")
                        encoded = int.from_bytes(encoded.to_bytes(bits // 8, "little"), "big")
                else:
                    try:
                        data = str(value).encode("ascii") if t["encoding"] == "ascii" else bytes.fromhex(str(value))
                    except (UnicodeError, ValueError):
                        raise DomainError(f"{f['label']}: ASCII 또는 16진수 바이트 형식을 확인하세요.")
                    if f.get("variable"):
                        if len(data) < f["min_bytes"]:
                            raise DomainError(f"{f['label']}: {f['min_bytes']}바이트 이상이 필요합니다.")
                        if f.get("max_bytes") is not None and len(data) > f["max_bytes"]:
                            raise DomainError(f"{f['label']}: {f['max_bytes']}바이트 이하여야 합니다.")
                        bits = len(data) * 8
                        if f.get("leading_bits"):
                            # 앞 길이 바이트(바이트 수, big endian) + 데이터.
                            if len(data) >= 1 << f["leading_bits"]: raise DomainError(f"{f['label']}: 앞 길이 바이트로 나타낼 수 없는 길이입니다.")
                            data = len(data).to_bytes(f["leading_bits"] // 8, "big") + data
                            bits = len(data) * 8
                        if total + bits > 8 * 1048576: raise DomainError("전문은 1 MiB 이내여야 합니다.")
                    elif len(data) * 8 != bits:
                        raise DomainError(f"{f['label']}: 정확히 {bits // 8}바이트가 필요합니다.")
                    encoded = int.from_bytes(data, "big")
            layout.append({"name": f["label"], "bit_offset": total, "bits": bits, "physical": physical_value})
            acc = (acc << bits) | encoded
            total += bits
        if total % 8:
            raise DomainError("전체 전문 길이가 바이트 경계에 맞지 않습니다.")
        return acc.to_bytes(total // 8, "big"), layout


# 규칙별 심각도·출력 차단(docs/11-validation-and-testing.md 표). 표에 없는 규칙은 error/차단이다.
RULE_POLICY = {
    "VAL-011": ("warning", True),
    "VAL-012": ("warning", True),
    "INF-001": ("info", False),
    "WRN-001": ("warning", False),
    "WRN-002": ("warning", False),
}
RULE_REMEDY = {
    "VAL-011": "프로파일이 정의한 값으로 바꾸거나, 원문 서비스로 읽기 전용 보존하세요.",
    "VAL-012": "지원되는 범위를 선택하거나 구현을 확장하세요. 원문은 보존되어 있습니다.",
    "INF-001": "조치할 필요가 없습니다. 원문은 저장 파일에 보존됩니다.",
    "WRN-001": "물리값이 필요하면 선형·열거 변환으로 바꾸세요. 그대로 두면 Vector 출력과 같은 IDENTICAL로 나갑니다.",
    "WRN-002": "기본 구조가 필요하면 선택자 값별 케이스로 옮기세요. 그대로 두면 Vector 출력과 같이 기본 구조 없이 나갑니다.",
}


# VAL-011 고정 UDS 프로파일이 증명하는 subfunction(sid → 허용 값).
# 0x3E 00, 0x31 01·02·03: CDD 템플릿 프로토콜 서비스(ABS 예제 TP "3E 00", RC_STR/STPR/RRR "31 01/02/03")에 고정된 값.
# 0x10·0x11: 템플릿은 매개변수(‹DiagSessionType›·‹ResetType›)로만 두므로 ISO 14229-1이 정의한 값만 인정한다
# (0x10: 01 default·02 programming·03 extended·04 safety, 0x11: 01 hard·02 keyOffOn·03 soft·04/05 rapid power shutdown).
# 제조사·공급사 정의 범위(0x40 이상)는 의미를 증명할 수 없으므로 경고한다.
PROFILE_SUBFUNCTIONS = {16: {1, 2, 3, 4}, 17: {1, 2, 3, 4, 5}, 49: {1, 2, 3}, 62: {0}}
# 프로젝트에 NRC 목록(CDD 템플릿 NEGRESCODES)이 없을 때 쓰는 기본 목록. ABS 예제 템플릿의 NRC 40개와 같다.
PROFILE_NRCS = frozenset([0x10, 0x11, 0x12, 0x13, 0x14, 0x21, 0x22, 0x24, 0x25, 0x26, 0x31, 0x33, 0x35, 0x36, 0x37,
                          0x70, 0x71, 0x72, 0x73, 0x78, 0x7E, 0x7F, *range(0x81, 0x8E), *range(0x8F, 0x94)])


def validate(snapshot, cancel=None):
    s = snapshot
    issues = []
    def add(rule, message, oid=None, field="", remedy=None):
        severity, blocks = RULE_POLICY.get(rule, ("error", True))
        issues.append(asdict(Issue(rule, message, oid, field, severity, remedy or RULE_REMEDY.get(rule, "해당 객체의 속성을 수정하세요."), blocks)))
    if len(s.rows("ecu")) != 1 or sum(v["is_base"] for v in s.rows("variants")) != 1:
        add("VAL-014", "ECU와 기본 변형은 각각 하나여야 합니다.")
    for obj in s.rows("objects"):
        if cancel: cancel()
        try: qualifier(obj["qualifier"])
        except DomainError as e: add("VAL-013", str(e), obj["id"], "qualifier")
        if not obj["supported"]:
            add("VAL-012", "미지원 의미가 포함된 객체입니다.", obj["id"])
    for t in s.rows("data_types"):
        try: check_type(t, [e for e in s.rows("conversion_entries") if e["data_type_id"] == t["id"]], [e for e in s.rows("raw_exclusions") if e["data_type_id"] == t["id"]],
                        [x for x in s.rows("conversion_segments") if x["data_type_id"] == t["id"]] if t["conversion"] in SEGMENT_CONVERSIONS else None)
        except DomainError as e: add("VAL-007", str(e), t["id"])
        if t["conversion"] == "formula":
            # schema 12: 수식은 계산하지 않고 Vector처럼 IDENTICAL + DESC 수식 문구로 낸다. 물리값·단위가 ODX에 없다는 것을 알린다.
            add("WRN-001", "수식 변환 타입은 ODX에 IDENTICAL(원시값 그대로)로 나갑니다. 수식은 설명(DESC)에만 남고 단위는 나가지 않습니다.", t["id"], "conversion")
    for st in s.rows("structures"):
        if cancel: cancel()
        try:
            fields = s.flatten(st["id"])
            length = sum(f["bits"] for f in fields)
            if length % 8: add("VAL-005", "구조 길이가 바이트 경계에 맞지 않습니다.", st["id"])
            if length > 8 * 1048576: add("VAL-005", "구조 크기가 1 MiB를 초과합니다.", st["id"])
        except DomainError as e: add("VAL-004", str(e), st["id"])
    # 반복(iteration, schema 7): 전문 구조의 마지막 멤버로만 두고, 항목은 바이트 단위 길이의 전용 구조여야 한다.
    telegram_structures = {t["structure_id"] for t in s.rows("telegrams")}
    item_users = {}
    for m in s.rows("structure_members"):
        if m["kind"] == "iteration": item_users.setdefault(m["nested_structure_id"], []).append(m["structure_id"])
        elif m["nested_structure_id"]: item_users.setdefault(m["nested_structure_id"], []).append(None)
    for st in s.rows("structures"):
        members = s.members(st["id"])
        for m in members:
            if m["kind"] != "iteration": continue
            if st["id"] not in telegram_structures: add("VAL-005", "반복은 요청·응답 전문 구조에만 둘 수 있습니다.", st["id"])
            elif m is not members[-1]: add("VAL-005", "반복은 전문의 마지막 멤버여야 합니다(PDU 끝까지 반복).", st["id"])
            if len(item_users.get(m["nested_structure_id"], [])) != 1: add("VAL-005", "반복 항목 구조를 다른 곳에서 함께 쓰고 있습니다.", m["nested_structure_id"])
            try:
                bits = sum(f["bits"] for f in s.flatten(m["nested_structure_id"]))
                if bits == 0 or bits % 8: add("VAL-005", "반복 항목의 길이가 0이거나 바이트 경계에 맞지 않습니다.", m["nested_structure_id"])
            except DomainError: pass  # 구조 오류는 위 구조 검사에서 보고한다.
    # 가변 바이트 필드(schema 9): PDU 끝까지 이어지므로 전문의 마지막 필드여야 하고 반복 항목 안에 둘 수 없다.
    for tg in s.rows("telegrams"):
        try: fields = s.flatten(tg["structure_id"])
        except DomainError: continue
        for i, f in enumerate(fields):
            inner = f["item"] if f["kind"] == "iteration" else []
            if any(g.get("variable") and g["kind"] == "data" for g in inner):
                add("VAL-005", "가변 길이 필드는 반복 항목 안에 둘 수 없습니다.", tg["service_id"])
            if f.get("variable") and not f.get("leading_bits") and f["kind"] in ("data", "mux") and i != len(fields) - 1:
                add("VAL-005", f"가변 길이 필드({f['label']})는 전문의 마지막 필드여야 합니다.", tg["service_id"])
            if any(g["kind"] == "mux" for g in inner):
                add("VAL-005", "MUX는 반복 항목 안에 둘 수 없습니다.", tg["service_id"])
            if f["kind"] == "mux" and f.get("default") and not f.get("in_did"):
                # 30차: 서비스 MUX → ODX TABLE에는 기본 구조 자리가 없다(Door Memory_Address_and_Size의 기본 STRUCTURE가 Vector TABLE에 없음).
                add("WRN-002", f"{f['label']}: MUX 기본 구조는 ODX TABLE에 나가지 않습니다(Vector 출력과 같음).", tg["service_id"])
            if f["kind"] == "mux" and not any(g["kind"] == "data" and g["object"]["id"] == f["selector"] for g in fields[:i]):
                add("VAL-005", f"{f['label']}: 선택자가 같은 전문의 앞쪽 데이터 필드가 아닙니다.", tg["service_id"])
    # 고장 메모리 레코드 자리(schema 8): 가변 데이터(sd/ed)는 구조의 마지막 멤버, 레코드 정의가 서비스 인스턴스에 있어야 한다.
    service_of_structure = {t["structure_id"]: t["service_id"] for t in s.rows("telegrams")}
    parent_of = {m["nested_structure_id"]: m["structure_id"] for m in s.rows("structure_members") if m["kind"] == "iteration"}
    has_snapshot = {r["instance_id"] for r in s.rows("snapshot_records")}
    has_extended = {r["instance_id"] for r in s.rows("extended_records")}
    for st in s.rows("structures"):
        members = s.members(st["id"])
        for m in members:
            if m["kind"] != "fault_record": continue
            if m["record_role"] in ("sd", "ed") and m is not members[-1]:
                add("VAL-005", "가변 길이 레코드 데이터는 구조의 마지막 멤버여야 합니다.", st["id"])
            service = service_of_structure.get(st["id"]) or service_of_structure.get(parent_of.get(st["id"]))
            if service is None:
                add("VAL-005", "고장 메모리 레코드 자리는 서비스 전문(또는 그 반복 항목)에만 둘 수 있습니다.", st["id"]); continue
            instance = s.get("services", service)["instance_id"]
            if instance not in (has_snapshot if m["record_role"] in ("sr", "sd") else has_extended):
                add("VAL-021", "서비스 인스턴스에 고장 메모리 레코드 정의가 없습니다.", service)
            if m["record_role"] in ("sd", "ed"):
                add("VAL-012", f"{RECORD_LABELS[m['record_role']]}의 ODX 대응(ENV-DATA-DESC·MUX 구성)은 근거가 없어 출력을 막습니다.", service)
    memberships = {(m["variant_id"], m["did_id"]) for m in s.rows("variant_dids")}
    for vi in s.rows("variant_instances"):
        for ref in s.rows("instance_dids"):
            if ref["instance_id"] == vi["instance_id"] and (vi["variant_id"], ref["did_id"]) not in memberships:
                add("VAL-008", "서비스가 사용하는 DID가 변형에 없습니다.", vi["instance_id"])
    # 탐색 트리 배치(schema 4): 변형의 인스턴스는 그 클래스가 변형에 속해야 하고, 싱글턴 클래스는 변형마다 인스턴스가 하나다.
    class_of = {i["id"]: i["class_id"] for i in s.rows("diagnostic_instances")}
    singleton = {c["id"] for c in s.rows("diagnostic_classes") if c.get("singleton")}
    placed = {(vc["variant_id"], vc["class_id"]) for vc in s.rows("variant_classes")}
    per_slot = {}
    for vi in s.rows("variant_instances"):
        cid = class_of[vi["instance_id"]]
        if (vi["variant_id"], cid) not in placed: add("VAL-021", "인스턴스의 진단 클래스가 변형에 포함되어 있지 않습니다.", vi["instance_id"])
        per_slot.setdefault((vi["variant_id"], cid), []).append(vi["instance_id"])
    for (vid, cid), ids in per_slot.items():
        if cid in singleton and len(ids) > 1: add("VAL-021", "싱글턴 진단 클래스에 인스턴스가 둘 이상입니다.", ids[1])
    # schema 5: 원본 변형은 그 인스턴스를 포함해야 한다.
    members = {(vi["variant_id"], vi["instance_id"]) for vi in s.rows("variant_instances")}
    for i in s.rows("diagnostic_instances"):
        if i.get("owner_variant_id") and (i["owner_variant_id"], i["id"]) not in members:
            add("VAL-021", "인스턴스의 원본 변형이 그 인스턴스를 포함하지 않습니다.", i["id"])
    for service in s.rows("services"):
        kinds = {t["kind"] for t in s.rows("telegrams") if t["service_id"] == service["id"]}
        if not {"request", "positive"} <= kinds:
            add("VAL-009", "요청·정응답 전문이 필요합니다.", service["id"])
        rows = [c for c in s.rows("service_conditions") if c["service_id"] == service["id"]]
        for gid in {c["group_id"] for c in rows}:
            expected = {st["id"] for st in s.rows("states") if st["group_id"] == gid}
            if {c["state_id"] for c in rows if c["group_id"] == gid} != expected:
                add("VAL-020", "상태 허용 행렬이 불완전합니다.", service["id"])
        for tr in s.rows("service_transitions"):
            if tr["service_id"] == service["id"] and not any(c["state_id"] == tr["from_state_id"] and c["allowed"] for c in rows):
                add("VAL-010", "허용되지 않은 출발 상태에 전이가 있습니다.", service["id"])
    # VAL-011: 고정 프로파일 서비스의 subfunction과 모든 서비스의 NRC가 프로파일(또는 프로젝트 NRC 목록)에 있는지 본다.
    # 원문(raw) 서비스는 CDD 원문 배치 그대로 읽기 전용 보존이므로 subfunction은 검사하지 않는다.
    known_nrcs = {n["code"] for n in s.rows("nrcs")} or PROFILE_NRCS
    nrcs_of = {}
    for c in s.rows("service_nrcs"): nrcs_of.setdefault(c["service_id"], []).append(c["code"])
    for service in s.rows("services"):
        allowed = PROFILE_SUBFUNCTIONS.get(service["sid"])
        if service.get("profile", "uds_fixed") == "uds_fixed" and allowed and service["subfunction"] not in allowed:
            add("VAL-011", f"SID 0x{service['sid']:02X}의 subfunction 0x{service['subfunction']:02X}는 프로파일이 정의하지 않은 값입니다.", service["id"], "subfunction")
        unknown = sorted(c for c in nrcs_of.get(service["id"], []) if c not in known_nrcs)
        if unknown:
            add("VAL-011", "프로파일에 없는 NRC: " + ", ".join(f"0x{c:02X}" for c in unknown), service["id"], "nrcs")
    for n in s.rows("unsupported_nodes"):
        if n["semantic"]: add("VAL-012", f"미지원 CDD 의미: {n['reason_code']} ({n['node_path']})", n["owner_object_id"])
        else: add("INF-001", f"보존된 CDD 원문(출력 영향 없음): {n['reason_code']} ({n['node_path']})", n["owner_object_id"])
        issues[-1].update(source_file_id=n["source_file_id"], node_path=n["node_path"])
    reported = {(n["source_file_id"], n["node_path"]) for n in s.rows("unsupported_nodes")}
    for n in s.rows("import_issues"):
        # 같은 원본 노드는 unsupported_nodes에서 이미 보고했다.
        if (n["source_file_id"], n["node_path"]) in reported: continue
        if n["blocks_export"]: add("VAL-012", n["message"], n["object_id"])
        else: add("INF-001", n["message"], n["object_id"])
        issues[-1].update(source_file_id=n["source_file_id"], node_path=n["node_path"])
    return {"revision": s.revision, "issues": issues}
