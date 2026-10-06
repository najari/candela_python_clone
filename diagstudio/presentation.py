"""Qt에 의존하지 않는 표시용 텍스트 생성. UI와 테스트가 같은 함수를 사용한다."""
import re

LABELS = {"id": "객체 ID", "qualifier": "Qualifier", "name": "이름", "description": "설명", "identifier": "DID", "code": "코드",
          "encoding": "인코딩", "bit_length": "비트 길이", "array_length": "배열 길이", "min_length": "최소 길이(가변)", "max_length": "최대 길이(가변)", "byte_order": "바이트 순서", "conversion": "변환",
          "factor_text": "계수", "offset_text": "오프셋", "unit": "단위", "sid": "SID", "subfunction": "Subfunction", "routine_identifier": "루틴 ID", "manufacturer": "제조사",
          "class_name": "진단 클래스", "identifier_text": "식별자", "services_text": "서비스", "share_text": "공유", "required_text": "필수(required)",
          "singleton_text": "싱글턴 클래스", "states_text": "상태", "owner_text": "원본 변형",
          "snapshot_text": "스냅샷 레코드", "snapshot_dids_text": "스냅샷 DID(기본)", "snapshot_dtc_text": "스냅샷 DID(DTC별 대체)", "extended_text": "확장 데이터 레코드",
          "request_text": "요청 구성", "positive_text": "긍정 응답 구성", "negative_text": "부정 응답 구성", "template_count": "사용 템플릿 수", "service_count": "문서 서비스 수",
          # U2·U5·U6 표시 항목
          "role_text": "역할", "protocol_text": "프로토콜 서비스", "request_pattern": "요청", "positive_pattern": "긍정 응답", "negative_pattern": "부정 응답",
          "length_text": "길이 (바이트:비트)", "usage_text": "사용", "template_text": "템플릿 타입", "formula_text": "원본 CDD 변환식",
          "kind_text": "종류", "negative_response": "상태 불일치 부정 응답", "source_text": "원본 파일", "revision_text": "revision", "summary_text": "개수 요약",
          "pattern_text": "식별 패턴"}
TELEGRAMS = {"request": "요청", "positive": "긍정 응답"}


def qualifier_last(columns):
    """표 열 순서: 'Qualifier' 열은 항상 맨 뒤에 둔다(평소에는 보지 않는 식별 이름이다). 나머지 열의 순서는 그대로다.
    머리가 정확히 'Qualifier'인 열만 옮긴다. 'Shortcut Qualifier'처럼 그 표의 주제인 열, 'qualifier' 필드를 '원본 경로'로 쓰는 열은 두지 않는다."""
    columns = [tuple(c) for c in columns]
    return [c for c in columns if c[1] != "Qualifier"] + [c for c in columns if c[1] == "Qualifier"]


def _hex(field, value, kind):
    if field == "identifier": return f"0x{value:04X}"
    if field == "code": return f"0x{value:02X}" if kind == "nrc" else f"0x{value:06X}  ({sae_dtc(value)})" if kind == "dtc" else f"0x{value:06X}"
    if field in ("sid", "subfunction"): return f"0x{value:02X}"
    if field == "routine_identifier": return f"0x{value:04X}"
    return value


def layout_lines(layout):
    """평탄화 배치를 '바이트.비트 / 길이 / 이름 / 타입·변환' 형식의 줄로 만든다."""
    if not layout: return ["  (멤버 없음)"]
    lines = []
    total = 0
    repeated = False
    for row in layout:
        if row["kind"] == "error":
            lines.append("  구조 오류: " + row["label"]); continue
        if row["kind"] == "iteration":
            repeated = True
            lines.append(f"  {row['bit_offset'] // 8:>5}.0  반복  {row['label']} — 아래 항목({row['item_bits'] // 8}바이트)을 PDU 끝까지 반복, 최소 {row['min_items']}개")
            continue
        # ODX와 같은 표기: 바이트 위치.비트 위치(바이트 안에서 최하위 비트 = 0). 바이트를 넘는 필드는 .0
        offset, bits = row["bit_offset"], row["bits"]
        bit = 8 - (offset % 8) - bits if 0 < bits < 8 and offset % 8 + bits <= 8 else offset % 8
        start = f"{offset // 8}.{bit}"
        indent = "    ↻ " if row.get("depth") else "  "
        text = f"{indent}{start:>7}  {row['bits']:>4}b  {row['label']}"
        if row["kind"] == "literal": text += f"  = {row['value']}"
        elif row["kind"] == "dtc": text += "  [DTC 표]"
        elif row["kind"] == "fault_record": text += "  [고장 메모리 레코드 · 가변 길이]" if row.get("variable") else "  [고장 메모리 레코드]"
        elif row["kind"] == "reserved": text += "  (reserved)"
        elif row["kind"] == "mux":
            cases = ", ".join(f"{lo}" if lo == hi else f"{lo}..{hi}" for lo, hi in row.get("cases", []))
            text += f"  [선택자 값별 케이스: {cases}{' · 기본 구조' if row.get('has_default') else ''} · 가변 길이]"
        elif row["kind"] == "data":
            detail = [row.get("type_name") or "", row.get("encoding") or ""]
            if row.get("encoding") in ("unsigned", "signed") and row["bits"] > 8: detail.append(row.get("byte_order") or "")
            if row.get("conversion") == "linear": detail.append(f"×{row.get('factor_text')} {row.get('offset_text')}")
            elif row.get("conversion") == "text_table": detail.append("텍스트 표")
            elif row.get("conversion") == "formula": detail.append("수식(ODX 동일값)")
            elif row.get("conversion") == "scale_linear": detail.append("구간별 선형")
            elif row.get("conversion") == "tab_intp": detail.append("특성 곡선")
            if row.get("unit"): detail.append(row["unit"])
            if row.get("variable"): detail.append(f"가변 길이 · PDU 끝까지 · 최소 {row.get('min_bytes')}바이트")
            text += "  [" + " · ".join(d for d in detail if d) + "]"
            if row.get("constant_text") is not None: text += f"  상수={row['constant_text']}"
            elif row.get("default_text") is not None: text += f"  기본값={row['default_text']}"
        lines.append(text)
        if not row.get("depth"): total += row["bits"]
    if repeated: lines.append(f"  고정 부분 {total}비트" + (f" ({total // 8}바이트)" if total % 8 == 0 else " (바이트 경계 아님)") + " + 반복 항목")
    else: lines.append(f"  전체 {total}비트" + (f" ({total // 8}바이트)" if total % 8 == 0 else " (바이트 경계 아님)"))
    return lines


def details_text(record):
    if record["kind"] == "unsupported":
        return (f"미지원 · 읽기 전용\n\n사유: {record['reason_code']}\n원본: {record['node_path']}\n"
                f"출력 차단: {'예' if record['semantic'] else '아니요'}\n\n더블 클릭하면 보존된 원본 XML을 볼 수 있습니다.")
    lines = []
    for field, label in LABELS.items():
        value = record.get(field)
        if value is None or value == "": continue
        lines.append(f"{label}\n{_hex(field, value, record['kind'])}\n")
    if record.get("layout") is not None:
        lines.append("데이터 구조  (바이트.비트 — 비트는 최하위=0 / 길이 / 이름)")
        lines.extend(layout_lines(record["layout"]))
        lines.append("")
    for kind, layout in (record.get("telegram_layouts") or {}).items():
        lines.append(f"{TELEGRAMS.get(kind, kind)} 전문")
        lines.extend(layout_lines(layout))
        lines.append("")
    if record.get("nrcs"):
        names = record.get("nrc_names") or {}
        lines.append("NRC  " + ", ".join(f"0x{c:02X}" + (f" {names[c]}" if c in names else "") for c in record["nrcs"]) + "\n")
    if record.get("kind") == "protocol_service":
        lines.append("읽기 전용 — CDD 템플릿에서 가져온 참고 정보이며 출력 의미에는 쓰지 않습니다.\n")
    if record.get("kind") == "nrc":
        lines.append("읽기 전용 — 서비스가 참조하는 부정 응답 코드 목록입니다. VAL-011 검증과 ODX 출력에 쓰입니다.\n")
    if record.get("kind") == "job_container":
        lines.append("읽기 전용 — Job 내용은 해석하지 않고 CDD 원문으로 보존합니다(출력 차단).\n")
    if record.get("kind") == "service" and record.get("profile") == "raw":
        lines.append("원문 배치 (읽기 전용) — 고정 UDS 프로파일 밖의 서비스로, 가져온 요청·응답 배치를 그대로 보존합니다.\n")
    if record.get("kind") == "service":
        address = [name for key, name in (("physical", "물리"), ("functional", "기능")) if record.get(key)]
        if address: lines.append("주소 방식  " + " · ".join(address) + "\n")
        views = record.get("condition_view") or []
        if views:
            lines.append("실행 가능 상태")
            groups = {}
            for c in views: groups.setdefault(c["group"], []).append(c)
            for group, rows in groups.items():
                lines.append(f"  {group}: " + ", ".join(("✓ " if c["allowed"] else "✗ ") + c["state"] for c in rows))
            lines.append("")
        elif "condition_view" in record:
            lines.append("실행 가능 상태  제한 없음\n")
        for t in record.get("transition_view") or []:
            lines.append(f"상태 전이  {t['group']}: {t['from']} → {t['to']}")
        if record.get("transition_view"): lines.append("")
    if record.get("kind") == "data_type" and (record.get("raw_lower") is not None or record.get("raw_upper") is not None):
        lines.append(f"유효 원시 범위  {record.get('raw_lower') if record.get('raw_lower') is not None else '최소'} .. {record.get('raw_upper') if record.get('raw_upper') is not None else '최대'}\n")
    exclusions = record.get("exclusions") or []
    if exclusions:
        lines.append("예외값")
        for e in exclusions:
            span = e["lower_raw"] if e["lower_raw"] == e["upper_raw"] else f"{e['lower_raw']}..{e['upper_raw']}"
            label = f"  [{e['locale']}] {e['label']}" if e["label"] else ""
            lines.append(f"  {span}  {e['kind']}{label}")
        lines.append("")
    entries = record.get("entries") or []
    if entries:
        lines.append("텍스트 표")
        for e in sorted(entries, key=lambda e: (e["locale"], e["ordinal"])):
            lower, upper = e["lower_raw"], e["upper_raw"]
            lines.append(f"  [{e['locale']}] {lower if lower == upper else f'{lower}..{upper}'} → {e['label']}")
        lines.append("")
    attributes = record.get("attributes") or []
    if any(not a["key"].endswith("#raw") and not a["key"].startswith("app.") for a in attributes):
        lines.append("속성")
        for a in attributes:
            if a["key"].endswith("#raw") or a["key"].startswith("app."): continue
            label = a["key"][4:] if a["key"].startswith("cdd.") else a["key"]
            lines.append(f"  {label}: {a['value_text']}")
        lines.append("")
    if record.get("variants"): lines.append("공유 변형\n" + "\n".join(record["variants"]))
    if record.get("sources"): lines.append("\n원본 대응\n" + "\n".join(x["node_path"] for x in record["sources"]))
    return "\n".join(lines)


# ---- U2: 인스턴스 페이지·DID 마스터·디테일 공용 배치 표시 (Qt 없음) ----
PLACEHOLDERS = ("zz", "yy", "xx", "ww", "vv", "uu", "tt", "ss")
MEMBER_COLUMNS = ("바이트", "비트", "이름", "데이터 타입", "기본값", "상수", "설명")


def _bit_pos(offset, bits):
    """ODX와 같은 비트 위치(바이트 안 최하위 비트 = 0). 바이트를 넘거나 채우는 필드는 0."""
    return 8 - (offset % 8) - bits if 0 < bits < 8 and offset % 8 + bits <= 8 else offset % 8


def telegram_pattern(layout):
    """CANdela 서비스 행의 요청·응답 표기. 고정 바이트는 16진, 연속한 비고정 필드 묶음마다 자리표시 하나(zz, yy…).
    돌려주는 값: (표기 문자열, [(자리표시, 첫 필드 이름, 필드 수)…]). 구조 오류면 (오류 문구, [])."""
    if not layout: return "", []
    if any(r["kind"] == "error" for r in layout): return "구조 오류", []
    tokens, groups, open_group = [], [], False
    for row in layout:
        if row.get("depth"): continue
        if row["kind"] == "literal" and row["bit_offset"] % 8 == 0 and row["bits"] % 8 == 0:
            tokens.append(row["value"]); open_group = False
        elif not open_group:
            name = PLACEHOLDERS[len(groups)] if len(groups) < len(PLACEHOLDERS) else f"p{len(groups) + 1}"
            groups.append([name, row["label"], 1]); tokens.append(name); open_group = True
        else:
            groups[-1][2] += 1
    return " ".join(tokens), [tuple(g) for g in groups]


def negative_pattern(sid):
    return f"7F {sid:02X} rc" if isinstance(sid, int) else "7F rc"


def member_rows(layout):
    """멤버 표 행: 바이트, 비트, 이름, 데이터 타입, 기본값, 상수, 설명. 반복 항목 안의 행은 들여쓰기로 구분한다."""
    rows = []
    for row in layout or []:
        if row["kind"] == "error":
            rows.append({"byte": "", "bit": "", "name": "구조 오류: " + row["label"], "type": "", "default": "", "constant": "", "description": "", "kind": "error", "depth": 0}); continue
        offset, bits = row["bit_offset"], row["bits"]
        desc = {"literal": "고정값", "reserved": "reserved", "dtc": "DTC 표", "iteration": f"PDU 끝까지 반복 · 최소 {row.get('min_items')}개",
                "fault_record": "고장 메모리 레코드" + (" · 가변 길이" if row.get("variable") else ""), "mux": "선택자 값별 케이스 · 가변 길이"}.get(row["kind"], "")
        if row["kind"] == "data":
            parts = [row.get("encoding") or ""]
            if row.get("conversion") == "linear": parts.append(f"×{row.get('factor_text')} {row.get('offset_text')}")
            elif row.get("conversion") == "text_table": parts.append("텍스트 표")
            elif row.get("conversion") == "formula": parts.append("수식(ODX 동일값)")
            elif row.get("conversion") == "scale_linear": parts.append("구간별 선형")
            elif row.get("conversion") == "tab_intp": parts.append("특성 곡선")
            if row.get("unit"): parts.append(row["unit"])
            if row.get("variable"): parts.append(f"가변 길이 · 최소 {row.get('min_bytes')}바이트")
            desc = " · ".join(p for p in parts if p)
        constant = row.get("value") if row["kind"] == "literal" else row.get("constant_text")
        # description = 타입 요약(인코딩·변환·단위), note = 데이터 객체의 설명(M5-2 설명 칸 편집). 표는 설명이 있으면 설명을 보인다.
        rows.append({"byte": str(offset // 8), "bit": str(_bit_pos(offset, bits)), "bits": bits, "name": ("  ↻ " if row.get("depth") else "") + row["label"],
                     "type": row.get("type_name") or ("" if row["kind"] == "data" else row["kind"]), "default": "" if row.get("default_text") is None else str(row["default_text"]),
                     "constant": "" if constant is None else str(constant), "description": desc, "kind": row["kind"], "depth": row.get("depth", 0),
                     "note": row.get("description") or "", "object_id": row.get("object_id"), "type_id": row.get("type_id"), "label": row["label"]})
    return rows


SHORTEN_BYTES = 24


def byte_grid(layout, limit=None):
    """바이트 × Bit7..Bit0 격자. 각 행은 {"byte": n, "label": 바이트 칸 글자, "cells": [(라벨, 필드 번호)…8개, Bit7부터]}.
    반복 항목 내부와 가변 길이 필드는 격자에 한 칸 행('…')으로만 표시한다.
    [DOUIG-R210] 최상위의 24바이트보다 긴 데이터·예약 필드는 앞 23바이트와 마지막 바이트만 보이고,
    23번째 바이트 번호 뒤와 마지막 바이트 번호 앞에 '…'를 붙인다(예: 4~33바이트 필드 → 4 … 25, '26…', '…33').
    limit(바이트)을 주면 그 뒤는 자른다(기본 없음)."""
    cells, labels = {}, {}
    tail = []

    def put(byte, pos, label, index):
        line = cells.get(byte)
        if line is None: line = cells[byte] = [("", None)] * 8
        line[pos] = (label, index)

    for index, row in enumerate(layout or []):
        if row["kind"] == "error" or row.get("depth"): continue
        if row["kind"] == "iteration" or row.get("variable"):
            tail.append(row["label"]); continue
        start, bits = row["bit_offset"], row["bits"]
        if row["kind"] in ("data", "reserved") and start % 8 == 0 and bits % 8 == 0 and bits // 8 > SHORTEN_BYTES:
            first, last = start // 8, start // 8 + bits // 8 - 1
            shown = list(range(first, first + SHORTEN_BYTES - 1)) + [last]
            labels[first + SHORTEN_BYTES - 2] = f"{first + SHORTEN_BYTES - 2}…"
            labels[last] = f"…{last}"
            for byte in shown:
                for pos in range(8): put(byte, pos, row["label"], index)
            continue
        for bit in range(start, start + bits):
            byte, pos = divmod(bit, 8)
            if limit is not None and byte >= limit: break
            put(byte, pos, row["label"], index)
    grid = [{"byte": b, "label": labels.get(b, str(b)), "cells": cells[b]} for b in sorted(cells) if limit is None or b < limit]
    for label in tail: grid.append({"byte": None, "label": "…", "cells": [(label + " …", -1)] * 8})
    return grid


# ---- 목록·페이지 표시 보조(U2·2.1·U5) ----
# UDS 서비스 이름(ISO 14229-1). 원본 CDD의 프로토콜 서비스 이름이 없을 때만 쓴다.
UDS_NAMES = {0x10: "DiagnosticSessionControl", 0x11: "ECUReset", 0x14: "ClearDiagnosticInformation", 0x19: "ReadDTCInformation",
             0x22: "ReadDataByIdentifier", 0x23: "ReadMemoryByAddress", 0x24: "ReadScalingDataByIdentifier", 0x27: "SecurityAccess",
             0x28: "CommunicationControl", 0x29: "Authentication", 0x2A: "ReadDataByPeriodicIdentifier", 0x2C: "DynamicallyDefineDataIdentifier",
             0x2E: "WriteDataByIdentifier", 0x2F: "InputOutputControlByIdentifier", 0x31: "RoutineControl", 0x34: "RequestDownload",
             0x35: "RequestUpload", 0x36: "TransferData", 0x37: "RequestTransferExit", 0x38: "RequestFileTransfer",
             0x3D: "WriteMemoryByAddress", 0x3E: "TesterPresent", 0x83: "AccessTimingParameter", 0x84: "SecuredDataTransmission",
             0x85: "ControlDTCSetting", 0x86: "ResponseOnEvent", 0x87: "LinkControl"}
# 고정 프로파일 서비스의 역할 이름(CANdela 클래스 템플릿 서비스 이름과 같은 말)
ROLE_NAMES = {0x10: "Start", 0x11: "Reset", 0x22: "Read", 0x2E: "Write", 0x31: "Start", 0x3E: "Send"}


def protocol_label(sid, name=None):
    """'(0x2E) WriteDataByIdentifier' 형식. 원본 프로토콜 서비스 이름이 있으면 그대로 쓴다."""
    if name: return name
    if not isinstance(sid, int): return ""
    return f"(0x{sid:02X}) {UDS_NAMES.get(sid, '')}".strip()


def sae_dtc(code):
    """24비트 DTC → SAE J2012 문자형(예: 0x402011 → C002011). 표시 전용이며 원시값은 바꾸지 않는다.
    첫 바이트의 상위 2비트 = 계통 문자(P/C/B/U), 다음 2비트 = 첫 숫자(0~3), 하위 4비트 = 둘째 자리(16진),
    둘째 바이트 = 셋째·넷째 자리, 셋째 바이트 = 고장 유형(FTB)."""
    if not isinstance(code, int) or not 0 <= code <= 0xFFFFFF: return ""
    high = code >> 16
    return "PCBU"[high >> 6] + str((high >> 4) & 3) + f"{high & 0xF:X}{(code >> 8) & 0xFF:02X}{code & 0xFF:02X}"


def parse_sae_dtc(text):
    """SAE 문자형 DTC(예: C002011) → 24비트 코드. 형식이 아니면 None. sae_dtc의 역이다(셀 편집에서 SAE 표시로 입력할 때)."""
    m = re.fullmatch(r"\s*([PCBUpcbu])([0-3])([0-9A-Fa-f])([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})\s*", str(text or ""))
    if not m: return None
    high = ("PCBU".index(m.group(1).upper()) << 6) | (int(m.group(2)) << 4) | int(m.group(3), 16)
    return (high << 16) | (int(m.group(4), 16) << 8) | int(m.group(5), 16)


def length_text(row):
    """데이터 타입 길이 표기(CANdela Bit Length 열): '바이트:비트', 고정 배열 '[17]', 가변 길이 '[2..7]'."""
    encoding = row.get("encoding")
    if encoding == "packet":
        bits = row.get("packet_bits")
        return f"{bits // 8}:{bits % 8} (packet)" if isinstance(bits, int) else "packet"
    bits = row.get("bit_length")
    if not isinstance(bits, int): return "—"
    text = f"{bits // 8}:{bits % 8}"
    count = row.get("array_length")
    if count is None and encoding == "bytes":
        low, high = row.get("min_length"), row.get("max_length")
        return text + f" [{low if low is not None else 0}..{high if high is not None else ''}]"
    if isinstance(count, int) and count > 1: text += f" [{count}]"
    return text


def formula_text(formula):
    """원본 CDD 선형 변환식(COMP f, div, o). Divisor는 원본에 있을 때만 그 형식으로 보인다."""
    if not formula: return None
    factor, divisor, offset = formula.get("f", "1"), formula.get("div"), formula.get("o", "0")
    scale = f"{factor} / {divisor}" if divisor not in (None, "1") else factor
    text = f"phys = {scale} × raw + {offset}"
    if formula.get("s") is not None or formula.get("e") is not None:
        text += f"  (유효 원시 범위 {formula.get('s', '')}..{formula.get('e', '')})"
    return text


# ---------------------------------------------------------------- 값 형식 전환(M2-5)
# 기본값·상수의 저장 값은 10진 원시값 문자열이다(작업 스레드 convert()가 만든 값). 형식 전환은 보이는 글자만 바꾼다.
VALUE_FORMAT_LABELS = {"dec": "10진", "hex": "16진", "bin": "2진"}


def value_formats(encoding):
    """쓸 수 있는 값 형식. [VALUI-R050] BCD는 10진·16진만, unsigned·signed는 10진·16진·2진([VALUI-R020-BCD]: 정수에는 BCD 형식을 두지 않는다).
    ascii·bytes·packet 값은 형식 전환이 없다."""
    if encoding == "bcd": return ("dec", "hex")
    if encoding in ("unsigned", "signed"): return ("dec", "hex", "bin")
    return ()


def format_value(text, encoding, bits, fmt, zero_fill=False):
    """저장 값(10진 원시값) → 보이는 글자. 형식을 쓸 수 없거나 원시값이 아니면 그대로 돌려준다.
    16진·2진은 부호화 값이다: signed 음수는 비트 길이의 2의 보수, BCD 16진은 자리마다 한 니블(1234 → 0x1234).
    zero_fill(설정 '16진 값을 앞자리 0으로 채우기')이면 16진을 값 크기(비트 길이 / 4)만큼 채운다. 2진은 늘 비트 길이만큼 쓴다."""
    if text is None or str(text).strip() == "": return ""
    text = str(text).strip()
    if fmt == "dec" or fmt not in value_formats(encoding): return text
    try: number = int(text)
    except ValueError: return text
    bits = int(bits or 0)
    if encoding == "bcd":
        if number < 0: return text
        digits = str(number)
        return "0x" + (digits.zfill(bits // 4) if zero_fill and bits else digits)
    if number < 0:
        if not bits or number < -(1 << (bits - 1)): return text
        number &= (1 << bits) - 1
    if bits and number >= (1 << bits): return text  # 비트 길이를 넘는 값은 고치지 않고 그대로 보인다(작업 스레드가 거부한다)
    if fmt == "hex":
        return "0x" + format(number, "X").zfill((bits + 3) // 4 if zero_fill and bits else 1)
    return "0b" + format(number, "b").zfill(bits or 1)


def parse_value(text, encoding, bits, fmt):
    """입력 글자 → 저장 값(10진 원시값). 0x·0b 접두어는 지금 형식과 상관없이 받고, 접두어가 없으면 지금 형식으로 읽는다.
    signed의 16진·2진은 부호화 값으로 읽는다(8비트 0xFF = -1). 앞에 -를 붙이면 그대로 음수다.
    BCD 16진은 자리마다 한 니블이므로 각 자리가 0~9여야 한다(0x1234 = 1234). 범위 검사는 작업 스레드(convert)가 한다.
    읽을 수 없으면 ValueError(이유)."""
    s = str(text or "").strip().replace("_", "").replace(" ", "")
    if not s: return ""
    if encoding not in ("unsigned", "signed", "bcd"): return str(text).strip()
    negative = s.startswith("-")
    body = s[1:] if s[:1] in "+-" else s
    low = body.lower()
    if low.startswith("0x"): base, digits = 16, body[2:]
    elif low.startswith("0b"): base, digits = 2, body[2:]
    else: base, digits = {"hex": 16, "bin": 2}.get(fmt, 10), body
    name = {16: "16진", 2: "2진", 10: "10진"}[base]
    if encoding == "bcd":
        if negative: raise ValueError("BCD 값은 음수일 수 없습니다.")
        if base == 2: raise ValueError("BCD 값은 10진·16진으로만 씁니다(2진 형식 없음).")
        if not re.fullmatch(r"[0-9]+", digits): raise ValueError("BCD 값의 각 자리는 0~9입니다" + (" (16진은 자리마다 한 니블: 0x1234 = 1234)." if base == 16 else "."))
        return str(int(digits))
    if not re.fullmatch({16: r"[0-9A-Fa-f]+", 2: r"[01]+", 10: r"[0-9]+"}[base], digits): raise ValueError(f"{name} 정수를 입력하세요.")
    number = int(digits, base)
    if negative: number = -number
    elif encoding == "signed" and base != 10 and bits and (1 << (int(bits) - 1)) <= number < (1 << int(bits)):
        number -= 1 << int(bits)
    return str(number)
