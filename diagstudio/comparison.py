"""Read-only comparison of the working project and a saved project file."""
from pathlib import Path
import difflib
import sqlite3

from .domain import DomainError, Snapshot
from .persistence import Session, connect, digest


TABLES = ("project_meta", "objects", "localized_texts", "variants", "variant_dids", "variant_dtcs",
          "dids", "dtcs", "structures", "structure_members", "data_objects", "data_types",
          "conversion_entries", "raw_exclusions",
          # 인스턴스·서비스 비교(스키마 5 이상)
          "diagnostic_classes", "diagnostic_instances", "variant_classes", "variant_instances",
          "services", "service_nrcs", "telegrams", "service_conditions", "service_transitions",
          "state_groups", "states",
          # 고장 메모리 레코드(스키마 8). 이전 파일에는 없으므로 있는 표만 읽는다.
          "snapshot_records", "snapshot_default_dids", "snapshot_dtc_dids", "extended_records",
          # 25차 비교 보기(M6-6): 속성 값·레코드 항목(스키마 1부터), MUX(스키마 10)
          "object_attributes", "muxes", "mux_cases")
OPTIONAL = {"snapshot_records", "snapshot_default_dids", "snapshot_dtc_dids", "extended_records", "muxes", "mux_cases"}


def saved_project(path, expected_digest=None):
    """Load only comparison data. Never create a work session or migrate the file."""
    path = Path(path).resolve()
    if not path.is_file():
        raise DomainError("비교할 프로젝트 파일이 없습니다.", "INVALID_PROJECT")
    if path.suffix.lower() != ".diagproj":
        raise DomainError(".diagproj 파일을 선택하세요.", "INVALID_PROJECT")
    fingerprint = digest(path)
    if expected_digest is not None and fingerprint != expected_digest:
        raise DomainError("변형 선택 후 비교 파일이 변경되었습니다. 다시 선택하세요.", "FILE_CHANGED")
    try:
        db = connect(path, True)
    except sqlite3.DatabaseError as exc:
        raise DomainError("프로젝트 파일을 읽을 수 없습니다.", "INVALID_PROJECT") from exc
    try:
        try:
            Session.check_file(db)
            present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            tables = {name: [dict(row) for row in db.execute(f'SELECT * FROM "{name}"')] for name in TABLES if name in present or name not in OPTIONAL}
        except sqlite3.DatabaseError as exc:
            raise DomainError("프로젝트 파일을 읽을 수 없습니다.", "INVALID_PROJECT") from exc
    finally:
        db.close()
    if digest(path) != fingerprint:
        raise DomainError("비교 중 파일이 변경되었습니다. 다시 선택하세요.", "FILE_CHANGED")
    meta = tables["project_meta"][0]
    snap = Snapshot(tables, meta["revision"], meta["default_locale"])
    variants = [{"id": row["id"], "name": snap.name(row["id"]), "is_base": row["is_base"]}
                for row in sorted(snap.rows("variants"), key=lambda row: row["ordinal"])]
    return {"snapshot": snap, "variants": variants, "digest": fingerprint,
            "project_uuid": meta["project_uuid"], "path": str(path)}


def definition_signature(snap, kind, object_id):
    """DID·DTC 정의 서명(이름·Qualifier·지원 상태, DID 구조). 같은 프로젝트의 사본 비교에도 쓴다."""
    return _signature(snap, kind, object_id)


def _signature(snap, kind, object_id):
    obj = snap.get("objects", object_id)
    value = {"이름": snap.name(object_id), "Qualifier": obj["qualifier"],
             "지원 상태": (obj["supported"], obj["read_only"])}
    if kind == "did":
        did = snap.get("dids", object_id)
        value["데이터 구조"] = _structure(snap, did["structure_id"], ())
    return value


def _structure(snap, structure_id, stack):
    if structure_id in stack or len(stack) > 128:
        raise DomainError("비교 대상의 데이터 구조에 순환 또는 과도한 중첩이 있습니다.", "BROKEN_REFERENCE")
    stack = (*stack, structure_id)
    result = []
    for member in snap.members(structure_id):
        kind = member["kind"]
        if kind == "data":
            obj = snap.get("data_objects", member["data_object_id"])
            typ = snap.get("data_types", obj["data_type_id"])
            entries = [(x["ordinal"], x["locale"], x["lower_raw"], x["upper_raw"], x["label"])
                       for x in snap.rows("conversion_entries") if x["data_type_id"] == typ["id"]]
            exclusions = [(x["ordinal"], x["locale"], x["lower_raw"], x["upper_raw"], x["kind"], x["label"])
                          for x in snap.rows("raw_exclusions") if x["data_type_id"] == typ["id"]]
            fields = ("encoding", "bit_length", "byte_order", "array_length", "conversion", "factor_text",
                      "offset_text", "unit", "raw_lower", "raw_upper", "min_length", "max_length", "formula_expression")
            profile = tuple(typ.get(field) for field in fields)
            nested = _structure(snap, typ["structure_id"], stack) if typ["structure_id"] else None
            result.append((kind, snap.get("objects", obj["id"])["qualifier"], snap.name(obj["id"]),
                           obj["default_kind"], obj["default_text"], obj["constant_kind"], obj["constant_text"],
                           snap.get("objects", typ["id"])["qualifier"], profile, tuple(sorted(entries)),
                           tuple(sorted(exclusions)), nested))
        elif kind == "structure":
            result.append((kind, _structure(snap, member["nested_structure_id"], stack)))
        elif kind in ("did_data", "did_with_identifier"):
            did = snap.get("dids", member["did_id"])
            result.append((kind, did["identifier"], _structure(snap, did["structure_id"], stack)))
        elif kind == "iteration":
            result.append((kind, member["min_items"], snap.get("objects", member["nested_structure_id"])["qualifier"],
                           _structure(snap, member["nested_structure_id"], stack)))
        elif kind == "dtc":
            result.append((kind,))
        elif kind == "fault_record":
            result.append((kind, member["record_role"]))
        elif kind == "reserved":
            result.append((kind, member["reserved_bits"], member.get("label")))
        elif kind == "literal":
            result.append((kind, bytes(member["literal_bytes"])))
        elif kind == "mux":
            mux = snap.mux_index.get((member["structure_id"], member["ordinal"])) or {}
            cases = tuple((c["lower_raw"], c["upper_raw"], _structure(snap, c["case_structure_id"], stack))
                          for c in snap.mux_case_index[(member["structure_id"], member["ordinal"])])
            default = _structure(snap, mux["default_structure_id"], stack) if mux.get("default_structure_id") else None
            selector = snap.get("objects", mux["selector_object_id"])["qualifier"] if mux.get("selector_object_id") else None
            result.append((kind, selector, cases, default))
        else:
            raise DomainError(f"비교할 수 없는 구조 멤버: {kind}", "UNSUPPORTED_COMPARE")
    return tuple(result)


def _items(snap, variant_id, kind):
    if not any(row["id"] == variant_id for row in snap.rows("variants")):
        raise DomainError("선택한 변형이 없습니다.", "INVALID_VARIANT")
    table, membership, id_column, code_column = (("dids", "variant_dids", "did_id", "identifier")
                                                 if kind == "did" else ("dtcs", "variant_dtcs", "dtc_id", "code"))
    owned = {row["id"]: row for row in snap.rows(table)}
    result = {}
    for row in snap.rows(membership):
        if row["variant_id"] != variant_id:
            continue
        item = owned[row[id_column]]
        code = (row["scope_id"], item[code_column])
        if code in result:
            raise DomainError("비교 대상에 중복 식별자가 있습니다.", "INVALID_PROJECT")
        result[code] = {"id": item["id"], "name": snap.name(item["id"]),
                        "signature": _signature(snap, kind, item["id"])}
    return result


def _q(snap, object_id):
    return snap.get("objects", object_id)["qualifier"] if object_id else None


def _by_service(snap, table):
    """서비스별 행 색인(스냅샷마다 한 번). 서비스 수 × 표 크기의 반복 검색을 피한다."""
    cache = snap.__dict__.setdefault("_service_rows", {})
    if table not in cache:
        index = {}
        for row in snap.rows(table): index.setdefault(row["service_id"], []).append(row)
        cache[table] = index
    return cache[table].get


def _service_signature(snap, service):
    """서비스 정의: 헤더 필드, 주소, 프로파일, NRC, 전문 구조, 상태 조건·전이. 상태는 그룹·상태 Qualifier로 대응한다."""
    sid = service["id"]
    value = {"이름": snap.name(sid), "Qualifier": _q(snap, sid)}
    value["헤더"] = tuple(service[f] for f in ("sid", "subfunction", "routine_identifier", "suppress_positive", "profile"))
    value["주소·사용"] = (service["physical"], service["functional"], service["enabled"])
    value["NRC"] = tuple(sorted(n["code"] for n in _by_service(snap, "service_nrcs")(sid, [])))
    value["전문 구조"] = tuple(sorted((t["kind"], _structure(snap, t["structure_id"], ())) for t in _by_service(snap, "telegrams")(sid, [])))
    value["상태 조건"] = tuple(sorted((_q(snap, c["group_id"]), _q(snap, c["state_id"]), c["allowed"])
                                  for c in _by_service(snap, "service_conditions")(sid, [])))
    value["상태 전이"] = tuple(sorted((_q(snap, t["group_id"]), _q(snap, t["from_state_id"]), _q(snap, t["to_state_id"]))
                                  for t in _by_service(snap, "service_transitions")(sid, [])))
    return value


def _fault_memory(snap, instance_id):
    """스냅샷·확장 데이터 레코드 정의(schema 8). 이전 파일에는 표가 없으므로 None."""
    if "snapshot_records" not in snap.tables: return None
    did = lambda i: snap.get("dids", i)["identifier"]
    snaps = sorted((r["number"], _q(snap, r["id"])) for r in snap.rows("snapshot_records") if r["instance_id"] == instance_id)
    defaults = tuple(did(r["did_id"]) for r in sorted((r for r in snap.rows("snapshot_default_dids") if r["instance_id"] == instance_id), key=lambda r: r["ordinal"]))
    per_dtc = {}
    for r in sorted(snap.rows("snapshot_dtc_dids"), key=lambda r: r["ordinal"]):
        if r["instance_id"] == instance_id: per_dtc.setdefault(snap.get("dtcs", r["dtc_id"])["code"], []).append(did(r["did_id"]))
    ext = sorted((r["number"], _q(snap, r["id"]), _structure(snap, r["structure_id"], ())) for r in snap.rows("extended_records") if r["instance_id"] == instance_id)
    if not (snaps or defaults or per_dtc or ext): return None
    return (tuple(snaps), defaults, tuple(sorted((k, tuple(v)) for k, v in per_dtc.items())), tuple(ext))


def _instances(snap, variant_id):
    """변형의 인스턴스를 (클래스 Qualifier, 인스턴스 Qualifier, 같은 이름 순번)으로 대응시킨다. 객체 UUID는 파일마다 다르다."""
    if "variant_instances" not in snap.tables or "variant_classes" not in snap.tables:
        raise DomainError("이전 스키마 파일은 앱에서 연 뒤 현재 형식으로 저장해야 인스턴스를 비교할 수 있습니다.", "UNSUPPORTED_VERSION")
    instances = {i["id"]: i for i in snap.rows("diagnostic_instances")}
    placed = sorted((vi for vi in snap.rows("variant_instances") if vi["variant_id"] == variant_id),
                    key=lambda vi: (instances[vi["instance_id"]]["class_id"], vi["ordinal"]))
    services = {}
    for svc in snap.rows("services"): services.setdefault(svc["instance_id"], []).append(svc)
    result, seen = {}, {}
    for vi in placed:
        inst = instances[vi["instance_id"]]
        base = (_q(snap, inst["class_id"]), _q(snap, inst["id"]))
        seen[base] = seen.get(base, 0) + 1
        owner = inst.get("owner_variant_id")
        svc_map, svc_seen = {}, {}
        for svc in services.get(inst["id"], []):
            q = _q(snap, svc["id"]); svc_seen[q] = svc_seen.get(q, 0) + 1
            svc_map[(q, svc_seen[q])] = _service_signature(snap, svc)
        result[(*base, seen[base])] = {"id": inst["id"], "name": snap.name(inst["id"]),
                                       "signature": {"이름": snap.name(inst["id"]),
                                                     # 원본 변형 Qualifier로 비교한다. 공유 인스턴스는 양쪽 모두 같은 원본을 가리키고, 사본은 다르게 나온다.
                                                     "원본 변형": _q(snap, owner) if owner else None,
                                                     "고장 메모리 레코드": _fault_memory(snap, inst["id"])},
                                       "services": svc_map}
    return result


def _compare_instances(left, left_variant, right, right_variant, cancel):
    rows = []
    before, after = _instances(left, left_variant), _instances(right, right_variant)
    for key in sorted(before.keys() | after.keys()):
        cancel()
        a, b = before.get(key), after.get(key)
        label = f"{key[0]} / {key[1]}" + (f" #{key[2]}" if key[2] > 1 else "")
        if a and b:
            changed = [field for field in a["signature"] if a["signature"][field] != b["signature"][field]]
            for skey in sorted(a["services"].keys() | b["services"].keys()):
                sa, sb = a["services"].get(skey), b["services"].get(skey)
                if sa is None: changed.append(f"서비스 {skey[0]}: 대상에만 있음")
                elif sb is None: changed.append(f"서비스 {skey[0]}: 현재에만 있음")
                else:
                    fields = [f for f in sa if sa[f] != sb[f]]
                    if fields: changed.append(f"서비스 {skey[0]}: " + "·".join(fields))
            if not changed: continue
            relation = "; ".join(changed)
        else: relation = "대상에만 있음" if b else "현재 프로젝트에만 있음"
        rows.append({"category": "인스턴스", "code": label, "scope": "", "left": a["name"] if a else "없음", "right": b["name"] if b else "없음",
                     "relation": relation, "id": a["id"] if a else None, "variant": left_variant if a else None})
    return rows


def compare_snapshots(left, left_variant, right, right_variant, cancel=lambda: None):
    """Compare DID/DTC definitions by protocol scope and business identifier, then instances and services by qualifier."""
    rows = []
    for kind in ("did", "dtc"):
        cancel()
        before, after = _items(left, left_variant, kind), _items(right, right_variant, kind)
        for scope, code in sorted(before.keys() | after.keys()):
            cancel()
            a, b = before.get((scope, code)), after.get((scope, code))
            if a and b and a["signature"] == b["signature"]:
                continue
            changed = [field for field in a["signature"] if a["signature"][field] != b["signature"][field]] if a and b else []
            rows.append({"category": kind.upper(), "code": f"0x{code:04X}" if kind == "did" else f"0x{code:06X}",
                         "scope": scope, "left": a["name"] if a else "없음", "right": b["name"] if b else "없음",
                         "relation": ", ".join(changed) if changed else ("대상에만 있음" if b else "현재 프로젝트에만 있음"),
                         "id": a["id"] if a else None, "variant": left_variant if a else None})
    rows.extend(_compare_instances(left, left_variant, right, right_variant, cancel))
    return rows


# ---------------------------------------------------------------- 비교 보기(M6-6) 트리
# 차이 행의 범주([CMP-R010]·[CMP-R012]). TextID·OID는 DiagAuthor에 없어 두지 않는다.
CATEGORY_LABELS = {"reference": "참조 객체", "record": "레코드 항목", "attribute": "속성 값", "transition": "상태 전이", "language": "다른 언어"}
STATUS_LABELS = {"left": "왼쪽만", "changed": "바뀜", "right": "오른쪽만"}
_SKIP_ATTRIBUTES = ("app.",)  # 앱 내부 값(Rework 목록 등)은 비교하지 않는다


def _cut(text, limit=140):
    text = " ".join(str("" if text is None else text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _shown(value):
    return "(없음)" if value is None or value == "" else str(value)


class _Side:
    """비교 한쪽(스냅샷 + 변형)의 색인."""
    def __init__(self, snap, variant):
        if not any(row["id"] == variant for row in snap.rows("variants")):
            raise DomainError("선택한 변형이 없습니다.", "INVALID_VARIANT")
        self.snap, self.variant = snap, variant
        self.attrs = {}
        for a in snap.rows("object_attributes"): self.attrs.setdefault(a["object_id"], {})[a["key"]] = a["value_text"]
        self.types = {t["id"]: t for t in snap.rows("data_types")}
        self.type_by_key = {self.q(t).casefold(): t for t in self.types}
        self.entries, self.exclusions = {}, {}
        for e in snap.rows("conversion_entries"): self.entries.setdefault(e["data_type_id"], []).append(e)
        for e in snap.rows("raw_exclusions"): self.exclusions.setdefault(e["data_type_id"], []).append(e)
        self.dids = {d["id"]: d for d in snap.rows("dids")}

    def q(self, oid): return self.snap.get("objects", oid)["qualifier"]
    def name(self, oid, field="name"): return self.snap.name(oid, field)

    def display_locale(self, oid, field):
        rows = sorted(self.snap.texts(oid, field), key=lambda r: (r["locale"] != self.snap.locale, r["locale"] != "en-US", r["locale"]))
        return rows[0]["locale"] if rows else None


def _aspect(label, cats, left, right, detail=None):
    left_text, right_text = _shown(left), _shown(right)
    return {"label": label, "kind": "aspect", "status": "changed", "cats": list(cats), "left": _cut(left_text), "right": _cut(right_text),
            "detail": detail if detail is not None else f"{label}\n\n왼쪽: {left_text}\n오른쪽: {right_text}", "children": []}


def _list_aspect(label, cats, left_lines, right_lines, note=""):
    """여러 줄 값(구조 멤버·상태 조건 등)의 차이. 왼쪽·오른쪽 칸에는 처음 다른 줄, 상세에는 줄 단위 차이(- 왼쪽만, + 오른쪽만)."""
    diff = [line for line in difflib.ndiff(list(left_lines), list(right_lines)) if not line.startswith("? ")]
    only_left = [line[2:].strip() for line in diff if line.startswith("- ")]
    only_right = [line[2:].strip() for line in diff if line.startswith("+ ")]
    short = lambda lines: (lines[0] + (f" 외 {len(lines) - 1}줄" if len(lines) > 1 else "")) if lines else "(같음)"
    detail = f"{label}{(' — ' + note) if note else ''}\n\n- 왼쪽에만 있는 줄, + 오른쪽에만 있는 줄\n\n" + "\n".join(diff)
    return {**_aspect(label, cats, short(only_left), short(only_right), detail)}


def _support_text(obj):
    return "지원" if obj["supported"] and not obj["read_only"] else "읽기 전용" if obj["supported"] else "미지원"


def _common_aspects(A, a, B, b, record_keys=False):
    """이름·설명(화면 언어), Qualifier, 지원 상태, 다른 언어의 이름·설명, 속성 값(레코드 항목)."""
    out = []
    for field, label in (("name", "이름"), ("description", "설명")):
        va, vb = A.name(a, field), B.name(b, field)
        if va != vb: out.append(_aspect(label, [], va, vb))
    if A.q(a) != B.q(b): out.append(_aspect("Qualifier", [], A.q(a), B.q(b)))
    oa, ob = A.snap.get("objects", a), B.snap.get("objects", b)
    if (oa["supported"], oa["read_only"]) != (ob["supported"], ob["read_only"]): out.append(_aspect("지원 상태", [], _support_text(oa), _support_text(ob)))
    # [CMP-R012]·[CMP-R035] 다른 언어: 화면에 보이는 언어가 아닌 언어의 이름·설명 차이(처음에는 숨김)
    for field, label in (("name", "이름"), ("description", "설명")):
        ta = {r["locale"]: r["value"] for r in A.snap.texts(a, field)}
        tb = {r["locale"]: r["value"] for r in B.snap.texts(b, field)}
        shown = {A.display_locale(a, field), B.display_locale(b, field)}
        for locale in sorted(ta.keys() | tb.keys()):
            if ta.get(locale) == tb.get(locale): continue
            if locale in shown and len(shown) == 1: continue  # 위 이름·설명 행에서 보인다
            out.append(_aspect(f"{label} ({locale or '언어 없음'})", ["language"], ta.get(locale), tb.get(locale)))
    ka, kb = A.attrs.get(a, {}), B.attrs.get(b, {})
    bases = sorted({k.split("#")[0] for k in ka.keys() | kb.keys() if not k.startswith(_SKIP_ATTRIBUTES)})
    for base in bases:
        va, vb = (ka.get(base), ka.get(base + "#raw")), (kb.get(base), kb.get(base + "#raw"))
        if va == vb: continue
        text = lambda v: None if v[0] is None and v[1] is None else f"{v[0] if v[0] is not None else ''}" + (f" (원시값 {v[1]})" if v[1] is not None else "")
        key, _, locale = base.partition("@")
        if record_keys and key.startswith("cdd."):
            out.append(_aspect(f"레코드 항목 {key[4:]}" + (f" ({locale})" if locale else ""), ["record"] + (["language"] if locale else []), text(va), text(vb)))
        else:
            out.append(_aspect(f"속성 {base}", ["attribute"] + (["language"] if locale else []), text(va), text(vb)))
    return out


def _shape_lines(side, structure_id, depth=0, stack=()):
    """구조 멤버를 읽는 줄로 만든다. 데이터 타입·DID는 이름·Qualifier로만 적고 정의는 참조 객체 행에서 비교한다([CMP-R005])."""
    if structure_id in stack or len(stack) > 128:
        raise DomainError("비교 대상의 데이터 구조에 순환 또는 과도한 중첩이 있습니다.", "BROKEN_REFERENCE")
    stack = (*stack, structure_id)
    snap, pad, lines = side.snap, "  " * depth, []
    for m in snap.members(structure_id):
        kind = m["kind"]
        if kind == "data":
            obj = snap.get("data_objects", m["data_object_id"])
            t = obj["data_type_id"]
            text = f"{pad}{snap.name(obj['id'])} [{side.q(obj['id'])}] : {snap.name(t)} [{side.q(t)}]"
            if obj["default_text"] is not None: text += f" · 기본값 {obj['default_text']}"
            if obj["constant_text"] is not None: text += f" · 상수 {obj['constant_text']}"
            lines.append(text)
        elif kind == "structure":
            nested = snap.get("structures", m["nested_structure_id"])
            lines.append(f"{pad}{'비트필드' if nested['kind'] == 'bitfield' else '구조'} {snap.name(m['nested_structure_id'])}:")
            lines.extend(_shape_lines(side, m["nested_structure_id"], depth + 1, stack))
        elif kind in ("did_data", "did_with_identifier"):
            did = snap.get("dids", m["did_id"])
            lines.append(f"{pad}{'DID 번호 + ' if kind == 'did_with_identifier' else ''}DID 0x{did['identifier']:04X} {snap.name(did['id'])} 데이터")
        elif kind == "iteration":
            lines.append(f"{pad}반복 {snap.name(m['nested_structure_id'])} (최소 {m['min_items']}개):")
            lines.extend(_shape_lines(side, m["nested_structure_id"], depth + 1, stack))
        elif kind == "literal": lines.append(f"{pad}고정값 {bytes(m['literal_bytes']).hex(' ').upper()}")
        elif kind == "reserved": lines.append(f"{pad}예약 {m['reserved_bits']}비트" + (f" {m['label']}" if m.get("label") else ""))
        elif kind == "dtc": lines.append(f"{pad}DTC")
        elif kind == "fault_record": lines.append(f"{pad}고장 메모리 레코드 {m['record_role']}")
        elif kind == "mux":
            mux = snap.mux_index.get((m["structure_id"], m["ordinal"])) or {}
            lines.append(f"{pad}MUX 선택자 {side.q(mux['selector_object_id']) if mux.get('selector_object_id') else '?'}:")
            for c in snap.mux_case_index[(m["structure_id"], m["ordinal"])]:
                lines.append(f"{pad}  케이스 {c['lower_raw']}..{c['upper_raw']}:")
                lines.extend(_shape_lines(side, c["case_structure_id"], depth + 2, stack))
            if mux.get("default_structure_id"):
                lines.append(f"{pad}  기본 구조:")
                lines.extend(_shape_lines(side, mux["default_structure_id"], depth + 2, stack))
        else: lines.append(f"{pad}{kind}")
    return lines


def _references(side, structure_id, types=None, dids=None, objects=None, stack=()):
    """구조가 직접 참조하는 데이터 타입·DID와 데이터 객체(문서 순서). 중첩 구조·반복·MUX 케이스는 따라가고, DID·packet 타입 안은 그 객체의 정의로 본다."""
    types = [] if types is None else types
    dids = [] if dids is None else dids
    objects = [] if objects is None else objects
    if structure_id in stack or len(stack) > 128: return types, dids, objects
    stack = (*stack, structure_id)
    snap = side.snap
    for m in snap.members(structure_id):
        if m["kind"] == "data":
            obj = snap.get("data_objects", m["data_object_id"])
            objects.append(obj["id"])
            if obj["data_type_id"] not in types: types.append(obj["data_type_id"])
        elif m["kind"] in ("did_data", "did_with_identifier"):
            if m["did_id"] not in dids: dids.append(m["did_id"])
        elif m["kind"] in ("structure", "iteration"):
            _references(side, m["nested_structure_id"], types, dids, objects, stack)
        elif m["kind"] == "mux":
            mux = snap.mux_index.get((m["structure_id"], m["ordinal"])) or {}
            for c in snap.mux_case_index[(m["structure_id"], m["ordinal"])]: _references(side, c["case_structure_id"], types, dids, objects, stack)
            if mux.get("default_structure_id"): _references(side, mux["default_structure_id"], types, dids, objects, stack)
    return types, dids, objects


def _member_language(A, a_objects, B, b_objects):
    """구조 멤버(데이터 객체) 이름·설명의 다른 언어 차이. 두 구조의 멤버가 같은 순서·같은 Qualifier일 때만 짝을 짓는다(아니면 구조 행이 보인다)."""
    if [A.q(x) for x in a_objects] != [B.q(x) for x in b_objects]: return []
    lines_a, lines_b = [], []
    for x, y in zip(a_objects, b_objects):
        for field, label in (("name", "이름"), ("description", "설명")):
            ta = {r["locale"]: r["value"] for r in A.snap.texts(x, field)}
            tb = {r["locale"]: r["value"] for r in B.snap.texts(y, field)}
            shown = {A.display_locale(x, field), B.display_locale(y, field)}
            for locale in sorted(ta.keys() | tb.keys()):
                if ta.get(locale) == tb.get(locale) or (locale in shown and len(shown) == 1): continue
                lines_a.append(f"{A.q(x)} {label} ({locale}): {_shown(ta.get(locale))}")
                lines_b.append(f"{B.q(y)} {label} ({locale}): {_shown(tb.get(locale))}")
    return [_list_aspect("멤버 이름·설명 (다른 언어)", ["language"], lines_a, lines_b)] if lines_a else []


_TYPE_GROUPS = (("인코딩·길이", ("encoding", "bit_length", "array_length", "byte_order", "min_length", "max_length")),
                ("변환", ("conversion", "factor_text", "offset_text", "raw_lower", "raw_upper", "formula_expression")), ("단위", ("unit",)))
_TYPE_FIELDS = {"encoding": "인코딩", "bit_length": "비트", "array_length": "배열", "byte_order": "순서", "min_length": "최소 바이트", "max_length": "최대 바이트",
                "conversion": "변환", "factor_text": "계수", "offset_text": "오프셋", "raw_lower": "하한", "raw_upper": "상한", "formula_expression": "수식", "unit": "단위"}


def _type_aspects(A, ta, B, tb):
    """데이터 타입 정의의 차이(데이터 타입 장과 참조 객체 행이 함께 쓴다)."""
    out = _common_aspects(A, ta, B, tb)
    fa, fb = A.types[ta], B.types[tb]
    for label, fields in _TYPE_GROUPS:
        if tuple(fa.get(f) for f in fields) != tuple(fb.get(f) for f in fields):
            if len(fields) == 1: text = lambda t: t.get(fields[0])
            else: text = lambda t: " · ".join(f"{_TYPE_FIELDS[f]} {t.get(f)}" for f in fields if t.get(f) not in (None, ""))
            out.append(_aspect(label, [], text(fa), text(fb)))
    for label, index, fields in (("열거값", "entries", ("lower_raw", "upper_raw", "label")), ("예외값", "exclusions", ("lower_raw", "upper_raw", "kind", "label"))):
        rows_a = getattr(A, index).get(ta, []); rows_b = getattr(B, index).get(tb, [])
        line = lambda e: f"[{e['locale'] or '-'}] " + " · ".join(str(e[f]) for f in fields)
        la, lb = sorted(line(e) for e in rows_a), sorted(line(e) for e in rows_b)
        if la == lb: continue
        changed = {e["locale"] for e in rows_a if line(e) not in lb} | {e["locale"] for e in rows_b if line(e) not in la}
        shown = {A.snap.locale, "en-US"}
        out.append(_list_aspect(label, ["language"] if changed and not changed & shown else [], la, lb))
    sa, sb = fa.get("structure_id"), fb.get("structure_id")
    if sa or sb:
        la = _shape_lines(A, sa) if sa else []
        lb = _shape_lines(B, sb) if sb else []
        if la != lb: out.append(_list_aspect("Packet 구조", [], la, lb))
    return out


def _did_aspects(A, a, B, b, cache):
    out = _common_aspects(A, a, B, b)
    da, db = A.dids[a], B.dids[b]
    la, lb = _shape_lines(A, da["structure_id"]), _shape_lines(B, db["structure_id"])
    if la != lb: out.append(_list_aspect("데이터 구조", [], la, lb))
    types_a, _, objects_a = _references(A, da["structure_id"])
    types_b, _, objects_b = _references(B, db["structure_id"])
    out.extend(_member_language(A, objects_a, B, objects_b))
    out.extend(_type_references(A, types_a, B, types_b, cache))
    return out


def _type_references(A, types_a, B, types_b, cache):
    """[CMP-R005] 참조한 데이터 타입의 정의가 다르면 참조한 쪽에 한 줄(🔗, 범주 '참조 객체')."""
    out = []
    right = {B.q(t).casefold(): t for t in types_b}
    for ta in types_a:
        tb = right.get(A.q(ta).casefold())
        if tb is None or (A.snap is B.snap and ta == tb): continue
        aspects = cache.setdefault(("type", ta, tb), _type_aspects(A, ta, B, tb))
        if aspects:
            labels = ", ".join(x["label"] for x in aspects)
            detail = f"참조한 데이터 타입 {A.name(ta)} [{A.q(ta)}]의 정의가 다릅니다. 참조 대상의 차이는 이 한 줄로 보입니다.\n\n" + "\n\n".join(x["detail"] for x in aspects)
            out.append(_aspect(f"🔗 데이터 타입 {A.name(ta)}", ["reference"], labels, labels, detail))
    return out


def _conditions(side, service_id, table):
    rows = [c for c in side.snap.rows(table) if c["service_id"] == service_id]
    if table == "service_conditions":
        return sorted(f"{side.q(c['group_id'])} / {side.q(c['state_id'])}: {'허용' if c['allowed'] else '차단'}" for c in rows)
    return sorted(f"{side.q(t['group_id'])} / {side.q(t['from_state_id'])} → {side.q(t['to_state_id'])}" for t in rows)


def _service_aspects(A, a, B, b, cache):
    out = _common_aspects(A, a, B, b)
    sa, sb = A.snap.get("services", a), B.snap.get("services", b)
    header = lambda s: " · ".join(x for x in (f"SID 0x{s['sid']:02X}", f"Subfunction 0x{s['subfunction']:02X}" if s["subfunction"] is not None else "",
                                            f"Routine 0x{s['routine_identifier']:04X}" if s["routine_identifier"] is not None else "",
                                            "SPRMIB" if s["suppress_positive"] else "", "원문 배치" if s.get("profile") == "raw" else "") if x)
    if header(sa) != header(sb): out.append(_aspect("헤더", [], header(sa), header(sb)))
    use = lambda s: " · ".join(x for x in ("physical" if s["physical"] else "", "functional" if s["functional"] else "", "사용" if s["enabled"] else "비활성") if x)
    if use(sa) != use(sb): out.append(_aspect("주소·사용", [], use(sa), use(sb)))
    nrc = lambda side, sid: sorted(f"0x{n['code']:02X}" for n in side.snap.rows("service_nrcs") if n["service_id"] == sid)
    if nrc(A, a) != nrc(B, b): out.append(_aspect("NRC", [], ", ".join(nrc(A, a)), ", ".join(nrc(B, b))))
    telegrams = lambda side, sid: {t["kind"]: t["structure_id"] for t in side.snap.rows("telegrams") if t["service_id"] == sid}
    tel_a, tel_b = telegrams(A, a), telegrams(B, b)
    types_a, dids_a, objects_a, types_b, dids_b, objects_b = [], [], [], [], [], []
    for kind, label in (("request", "요청 전문"), ("positive", "긍정 응답 전문"), ("negative", "부정 응답 전문")):
        la = _shape_lines(A, tel_a[kind]) if kind in tel_a else []
        lb = _shape_lines(B, tel_b[kind]) if kind in tel_b else []
        if la != lb: out.append(_list_aspect(label, [], la, lb))
        if kind in tel_a: _references(A, tel_a[kind], types_a, dids_a, objects_a)
        if kind in tel_b: _references(B, tel_b[kind], types_b, dids_b, objects_b)
    out.extend(_member_language(A, objects_a, B, objects_b))
    ca, cb = _conditions(A, a, "service_conditions"), _conditions(B, b, "service_conditions")
    if ca != cb: out.append(_list_aspect("상태 조건", ["transition"], ca, cb))
    ta, tb = _conditions(A, a, "service_transitions"), _conditions(B, b, "service_transitions")
    if ta != tb: out.append(_list_aspect("상태 전이", ["transition"], ta, tb))
    out.extend(_type_references(A, types_a, B, types_b, cache))
    # 참조한 DID의 정의(번호로 대응)가 다르면 한 줄([CMP-R005])
    right = {B.dids[d]["identifier"]: d for d in dids_b}
    for da in dids_a:
        db = right.get(A.dids[da]["identifier"])
        if db is None or (A.snap is B.snap and da == db): continue
        aspects = cache.setdefault(("did", da, db), _did_aspects(A, da, B, db, cache))
        if aspects:
            labels = ", ".join(x["label"] for x in aspects)
            detail = f"참조한 DID 0x{A.dids[da]['identifier']:04X} {A.name(da)}의 정의가 다릅니다.\n\n" + "\n\n".join(x["detail"] for x in aspects)
            out.append(_aspect(f"🔗 DID 0x{A.dids[da]['identifier']:04X} {A.name(da)}", ["reference"], labels, labels, detail))
    return out


def _fault_text(value):
    if value is None: return None
    snaps, defaults, per_dtc, ext = value
    parts = []
    if snaps: parts.append("스냅샷 " + ", ".join(f"0x{n:02X}" for n, _ in snaps))
    if defaults: parts.append("스냅샷 DID " + ", ".join(f"0x{d:04X}" for d in defaults))
    if per_dtc: parts.append(f"DTC별 스냅샷 DID {len(per_dtc)}개")
    if ext: parts.append("확장 데이터 " + ", ".join(f"0x{n:02X}" for n, _, _ in ext))
    return " · ".join(parts)


def _object_node(label, status, left, right, detail, left_id, right_id, object_kind, children=()):
    return {"label": label, "kind": "object", "status": status, "cats": [], "left": _cut(left), "right": _cut(right), "detail": detail,
            "left_id": left_id, "right_id": right_id, "object_kind": object_kind, "children": list(children)}


def _pair_node(label, a_label, b_label, aspects, left_id, right_id, object_kind, what):
    if not aspects: return None
    detail = f"{what} {label}\n\n차이 {len(aspects)}개: " + ", ".join(x["label"] for x in aspects)
    return _object_node(label, "changed", a_label, b_label, detail, left_id, right_id, object_kind, aspects)


def _only_node(label, side_name, other_name, left, object_kind, oid, what):
    status = "left" if left else "right"
    where, missing = (side_name, other_name) if left else (other_name, side_name)
    detail = f"{what} {label}\n\n{where}에만 있습니다. {missing}에는 없습니다."
    return _object_node(label, status, label if left else "", "" if left else label, detail, oid if left else None, None if left else oid, object_kind)


def compare_tree(left, left_variant, right, right_variant, cancel=lambda: None):
    """비교 보기(M6-6) 트리 노드 목록. 같은 스냅샷이면 두 변형 비교, 아니면 작업본과 저장된 파일 비교다.
    대응: DID = 번호, DTC = 코드, 인스턴스 = (클래스 Qualifier, 인스턴스 Qualifier, 같은 이름 순번), 서비스 = (Qualifier, 순번), 데이터 타입 = Qualifier.
    같은 스냅샷에서 같은 객체를 공유하면 차이가 없다. 노드: id·parent·level·label·kind(group/object/aspect)·status(left/right/changed)·
    cats(reference/record/attribute/transition/language)·left·right·detail·left_id·right_id. 순서는 문서 트리 순서([CMP-R110])."""
    A, B = _Side(left, left_variant), _Side(right, right_variant)
    same = left is right
    cache = {}
    left_name, right_name = "왼쪽", "오른쪽"
    roots = []
    variant_label = A.name(left_variant) if A.name(left_variant) == B.name(right_variant) else f"{A.name(left_variant)} ↔ {B.name(right_variant)}"
    variant_node = {"label": f"변형 {variant_label}", "kind": "group", "children": []}
    # 인스턴스(클래스별, 문서 순서)
    before, after = _instances(left, left_variant), _instances(right, right_variant)
    class_order = []
    for side, variant in ((left, left_variant), (right, right_variant)):
        for vc in sorted((x for x in side.rows("variant_classes") if x["variant_id"] == variant), key=lambda x: x["ordinal"]):
            q = _q(side, vc["class_id"])
            if q not in class_order: class_order.append(q)
    def placed(side, variant):
        instances = {i["id"]: i for i in side.rows("diagnostic_instances")}
        return {vi["instance_id"]: vi["ordinal"] for vi in side.rows("variant_instances") if vi["variant_id"] == variant and vi["instance_id"] in instances}
    order_a, order_b = placed(left, left_variant), placed(right, right_variant)
    class_nodes = {}
    for key_ in sorted(before.keys() | after.keys(), key=lambda k: (class_order.index(k[0]) if k[0] in class_order else len(class_order),
                                                                   order_a.get((before.get(k) or {}).get("id"), 10 ** 6), order_b.get((after.get(k) or {}).get("id"), 10 ** 6), k)):
        cancel()
        a, b = before.get(key_), after.get(key_)
        if same and a and b and a["id"] == b["id"]: continue
        side = left if a else right
        label = (a or b)["name"] + (f" #{key_[2]}" if key_[2] > 1 else "")
        if a and b:
            aspects = _common_aspects(A, a["id"], B, b["id"])
            if a["signature"]["원본 변형"] != b["signature"]["원본 변형"]:
                aspects.append(_aspect("원본 변형", [], _variant_name(left, a["signature"]["원본 변형"]), _variant_name(right, b["signature"]["원본 변형"])))
            if a["signature"]["고장 메모리 레코드"] != b["signature"]["고장 메모리 레코드"]:
                aspects.append(_aspect("고장 메모리 레코드", [], _fault_text(a["signature"]["고장 메모리 레코드"]), _fault_text(b["signature"]["고장 메모리 레코드"])))
            services_a = {k: svc for k, svc in _service_ids(left, a["id"]).items()}
            services_b = {k: svc for k, svc in _service_ids(right, b["id"]).items()}
            for skey in sorted(services_a.keys() | services_b.keys()):
                sa, sb = services_a.get(skey), services_b.get(skey)
                slabel = (left.name(sa) if sa else right.name(sb)) + (f" #{skey[1]}" if skey[1] > 1 else "")
                if sa and sb:
                    if same and sa == sb: continue
                    node = _pair_node(slabel, left.name(sa), right.name(sb), _service_aspects(A, sa, B, sb, cache), sa, sb, "service", "서비스")
                    if node: aspects.append(node)
                else:
                    aspects.append(_only_node(slabel, left_name, right_name, bool(sa), "service", sa or sb, "서비스"))
            node = _pair_node(label, a["name"], b["name"], aspects, a["id"], b["id"], "diagnostic_instance", "진단 인스턴스")
        else:
            node = _only_node(label, left_name, right_name, bool(a), "diagnostic_instance", (a or b)["id"], "진단 인스턴스")
        if node is None: continue
        cls = class_nodes.get(key_[0])
        if cls is None:
            class_id = next((i["class_id"] for i in side.rows("diagnostic_instances") if i["id"] == (a or b)["id"]), None)
            cls = class_nodes[key_[0]] = {"label": side.name(class_id) if class_id else key_[0], "kind": "group", "children": []}
        cls["children"].append(node)
    for q in class_order + sorted(set(class_nodes) - set(class_order)):
        if q in class_nodes: variant_node["children"].append(class_nodes[q])
    # DID·DTC(번호·코드로 대응)
    for kind, title in (("did", "DID"), ("dtc", "DTC")):
        items_a, items_b = _variant_items(left, left_variant, kind), _variant_items(right, right_variant, kind)
        group = {"label": title, "kind": "group", "children": []}
        for code in sorted(items_a.keys() | items_b.keys()):
            cancel()
            a, b = items_a.get(code), items_b.get(code)
            if same and a and b and a == b: continue
            oid = a or b
            label = (f"0x{code[1]:04X} " if kind == "did" else f"0x{code[1]:06X} ") + (left.name(a) if a else right.name(b))
            if a and b:
                aspects = _did_aspects(A, a, B, b, cache) if kind == "did" else _common_aspects(A, a, B, b, record_keys=True)
                node = _pair_node(label, left.name(a), right.name(b), aspects, a, b, kind, title)
            else:
                node = _only_node(label, left_name, right_name, bool(a), kind, oid, title)
            if node: group["children"].append(node)
        if group["children"]: variant_node["children"].append(group)
    if variant_node["children"]: roots.append(variant_node)
    # 데이터 타입(파일 비교): 프로젝트 전체, Qualifier로 대응
    if not same:
        chapter = {"label": "데이터 타입", "kind": "group", "children": []}
        keys = sorted(A.type_by_key.keys() | B.type_by_key.keys(), key=lambda k: ((A.name(A.type_by_key[k]) if k in A.type_by_key else B.name(B.type_by_key[k])).casefold(), k))
        for k in keys:
            cancel()
            ta, tb = A.type_by_key.get(k), B.type_by_key.get(k)
            label = A.name(ta) if ta else B.name(tb)
            if ta and tb:
                node = _pair_node(label, A.name(ta), B.name(tb), cache.setdefault(("type", ta, tb), _type_aspects(A, ta, B, tb)), ta, tb, "data_type", "데이터 타입")
            else:
                node = _only_node(label, left_name, right_name, bool(ta), "data_type", ta or tb, "데이터 타입")
            if node: chapter["children"].append(node)
        if chapter["children"]: roots.append(chapter)
    return _flatten(roots)


def _variant_name(snap, qualifier):
    """변형 Qualifier → 이름(없으면 Qualifier 그대로)."""
    for v in snap.rows("variants"):
        if _q(snap, v["id"]) == qualifier: return snap.name(v["id"])
    return qualifier


def _service_ids(snap, instance_id):
    """인스턴스의 서비스 (Qualifier, 같은 Qualifier 순번) → 서비스 ID."""
    out, seen = {}, {}
    for svc in sorted((s for s in snap.rows("services") if s["instance_id"] == instance_id), key=lambda s: (_q(snap, s["id"]), s["id"])):
        q = _q(snap, svc["id"]); seen[q] = seen.get(q, 0) + 1
        out[(q, seen[q])] = svc["id"]
    return out


def _variant_items(snap, variant, kind):
    """변형의 DID·DTC: (범위, 번호·코드) → 객체 ID."""
    membership, id_column, table, code_column = (("variant_dids", "did_id", "dids", "identifier") if kind == "did" else ("variant_dtcs", "dtc_id", "dtcs", "code"))
    owned = {row["id"]: row for row in snap.rows(table)}
    out = {}
    for row in snap.rows(membership):
        if row["variant_id"] != variant: continue
        item = owned[row[id_column]]
        code = (row["scope_id"], item[code_column])
        if code in out: raise DomainError("비교 대상에 중복 식별자가 있습니다.", "INVALID_PROJECT")
        out[code] = item["id"]
    return out


def _flatten(roots):
    nodes = []

    def walk(node, parent, level):
        nid = f"n{len(nodes)}"
        entry = {"id": nid, "parent": parent, "level": level, "label": node["label"], "kind": node.get("kind", "group"), "status": node.get("status"),
                 "cats": list(node.get("cats") or []), "left": node.get("left", ""), "right": node.get("right", ""), "detail": node.get("detail", ""),
                 "left_id": node.get("left_id"), "right_id": node.get("right_id"), "object_kind": node.get("object_kind")}
        nodes.append(entry)
        for child in node.get("children") or []: walk(child, nid, level + 1)

    for root in roots: walk(root, None, 0)
    return nodes


def tree_counts(nodes):
    """차이 요약: 왼쪽만·오른쪽만·바뀐 객체 수와 범주별 차이 행 수."""
    objects = [n for n in nodes if n["kind"] == "object"]
    leaves = [n for n in nodes if n["kind"] == "aspect"]
    counts = {"left": sum(1 for n in objects if n["status"] == "left"), "right": sum(1 for n in objects if n["status"] == "right"),
              "changed": sum(1 for n in objects if n["status"] == "changed"), "aspects": len(leaves)}
    for cat in CATEGORY_LABELS: counts[cat] = sum(1 for n in leaves if cat in n["cats"])
    return counts


# ---------------------------------------------------------------- 차이 내보내기([CMP-R131]: 화면 필터와 상관없이 모든 차이)
def _export_rows(result):
    for node in result["nodes"]:
        yield node, "  " * node["level"]


def _status_text(node):
    return STATUS_LABELS.get(node.get("status") or "", "")


def export_text(result):
    """텍스트: 들여쓰기 트리. 차이 행은 [상태·범주] 왼쪽 → 오른쪽."""
    left, right = result["left"], result["right"]
    lines = ["DiagAuthor 비교 결과", f"왼쪽: {left.get('source','')} · {left.get('project','')} · {left.get('variant','')} · revision {left.get('revision','')} · {left.get('path','')}",
             f"오른쪽: {right.get('source','')} · {right.get('project','')} · {right.get('variant','')} · revision {right.get('revision','')} · {right.get('path','')}",
             "화면 필터는 적용하지 않았습니다(모든 차이).", ""]
    for node, pad in _export_rows(result):
        tags = ", ".join([_status_text(node)] + [CATEGORY_LABELS[c] for c in node.get("cats") or []]) if node.get("status") else ""
        line = f"{pad}{node['label']}" + (f"  [{tags}]" if tags else "")
        if node["kind"] != "group": line += f"  왼쪽: {node.get('left') or '—'}  |  오른쪽: {node.get('right') or '—'}"
        lines.append(line)
    if not result["nodes"]: lines.append("차이가 없습니다.")
    return "\n".join(lines) + "\n"


def export_xml(result):
    """XML: <comparison><side/>…<node label= kind= status= categories=><left/><right/><detail/>…</node></comparison> (중첩)."""
    from xml.etree import ElementTree as ET
    root = ET.Element("comparison", {"tool": "DiagAuthor", "filters": "none"})
    for name in ("left", "right"):
        info = result[name]
        ET.SubElement(root, name, {k: str(info.get(k, "")) for k in ("source", "project", "variant", "revision", "path")})
    elements = {}
    for node in result["nodes"]:
        parent = elements.get(node["parent"], root)
        attrs = {"label": node["label"], "kind": node["kind"]}
        if node.get("status"): attrs["status"] = node["status"]
        if node.get("cats"): attrs["categories"] = " ".join(node["cats"])
        element = ET.SubElement(parent, "node", attrs)
        if node["kind"] != "group":
            ET.SubElement(element, "left").text = node.get("left") or ""
            ET.SubElement(element, "right").text = node.get("right") or ""
            ET.SubElement(element, "detail").text = node.get("detail") or ""
        elements[node["id"]] = element
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def export_xhtml(result):
    """XHTML: 표(객체·상태·왼쪽·오른쪽), 들여쓰기와 상태 색. 브라우저로 연다."""
    import html
    colors = {"left": "#fff1ef", "right": "#eaf7ee", "changed": "#fff8e6"}
    left, right = result["left"], result["right"]
    rows = []
    for node, _ in _export_rows(result):
        style = f"background:{colors[node['status']]}" if node.get("status") in colors else "font-weight:bold"
        tags = ", ".join([_status_text(node)] + [CATEGORY_LABELS[c] for c in node.get("cats") or []]) if node.get("status") else ""
        rows.append(f'<tr style="{style}"><td style="padding-left:{8 + node["level"] * 18}px">{html.escape(node["label"])}</td><td>{html.escape(tags)}</td>'
                    f'<td>{html.escape(node.get("left") or "")}</td><td>{html.escape(node.get("right") or "")}</td></tr>')
    head = lambda info: html.escape(f"{info.get('source','')} · {info.get('project','')} · {info.get('variant','')} · revision {info.get('revision','')} · {info.get('path','')}")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" lang="ko"><head><meta charset="UTF-8" /><title>DiagAuthor 비교 결과</title>'
            '<style>body{font-family:sans-serif;font-size:13px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #dce3ed;padding:4px 6px;text-align:left;vertical-align:top}</style></head><body>'
            f'<h1>비교 결과</h1><p>왼쪽: {head(left)}<br />오른쪽: {head(right)}<br />화면 필터는 적용하지 않았습니다(모든 차이).</p>'
            '<table><thead><tr><th>객체</th><th>상태</th><th>왼쪽</th><th>오른쪽</th></tr></thead><tbody>' + "".join(rows)
            + ('<tr><td colspan="4">차이가 없습니다.</td></tr>' if not rows else "") + "</tbody></table></body></html>\n")
