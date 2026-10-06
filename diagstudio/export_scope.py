"""ODX 출력에 필요한 객체와 보존 원문의 영향 범위. DB·원본 파일을 변경하지 않는다."""
from collections import defaultdict, deque
from dataclasses import asdict
import re

from lxml import etree as ET

from .domain import DomainError, Issue, RULE_REMEDY, validate


def export_objects(s, variants, cancel=lambda: None):
    if not variants:
        raise DomainError("출력할 변형을 선택하세요.")
    for vid in variants:
        s.get("variants", vid)
    edges = defaultdict(set)

    def link(owner, *targets):
        edges[owner].update(t for t in targets if t)

    for table, field in (("variant_dids", "did_id"), ("variant_dtcs", "dtc_id"),
                         ("variant_instances", "instance_id"), ("variant_jobs", "job_id")):
        for row in s.rows(table):
            link(row["variant_id"], row[field])
    for row in s.rows("diagnostic_instances"):
        link(row["id"], row["class_id"])
    for row in s.rows("services"):
        if row["enabled"]:
            link(row["instance_id"], row["id"])
    for row in s.rows("telegrams"):
        link(row["service_id"], row["structure_id"])
    for table in ("dids", "data_types"):
        for row in s.rows(table):
            link(row["id"], row.get("structure_id"))
    for row in s.rows("structure_members"):
        link(row["structure_id"], row.get("data_object_id"), row.get("nested_structure_id"), row.get("did_id"))
    for row in s.rows("data_objects"):
        link(row["id"], row["data_type_id"])
    # schema 10: MUX 자리 → 선택자·기본 구조·케이스 구조
    for row in s.rows("muxes"):
        link(row["structure_id"], row["selector_object_id"], row.get("default_structure_id"))
    for row in s.rows("mux_cases"):
        link(row["structure_id"], row["case_structure_id"])
    for row in s.rows("service_conditions"):
        link(row["service_id"], row["group_id"], row["state_id"])
    for row in s.rows("service_transitions"):
        link(row["service_id"], row["group_id"], row["from_state_id"], row["to_state_id"])
    for row in s.rows("states"):
        link(row["group_id"], row["id"])
    for row in s.rows("state_groups"):
        link(row["id"], row["initial_state_id"])
    # 고장 메모리 정의는 인스턴스에 속한다. 해석되지 않은 레코드 의미를 누락시키지 않는다.
    for table in ("snapshot_records", "extended_records"):
        for row in s.rows(table):
            link(row["instance_id"], row["id"])
            link(row["id"], row.get("structure_id"))
    for table in ("snapshot_default_dids", "snapshot_dtc_dids"):
        for row in s.rows(table):
            link(row["instance_id"], row["did_id"], row.get("dtc_id"))
    nrcs = {row["code"]: row["id"] for row in s.rows("nrcs")}
    for row in s.rows("service_nrcs"):
        link(row["service_id"], nrcs.get(row["code"]))
    found = set()
    queue = deque([*variants, *(row["id"] for row in s.rows("ecu"))])
    while queue:
        cancel()
        oid = queue.popleft()
        if oid in found:
            continue
        found.add(oid)
        queue.extend(edges[oid] - found)
    return found


def unused_source_types(s, cancel=lambda: None):
    """소유 UUID가 없는 보존 타입: 원본 전체에서 참조되지 않음이 확인된 경우만 제외한다.

    29차: 프로토콜 서비스(PROTOCOLSERVICE)와 진단 클래스 템플릿(DCLTMPL, 서비스 템플릿·공유 구성요소 포함) 안의 참조는
    그 템플릿이 실제로 쓰일 때만 사용으로 본다(인스턴스·클래스 → DCLTMPL → PROTOCOLSERVICE). 근거: Door CDD ↔ Vector 출력
    UDS-ExampleEcu-5.0.2.pdx — 인스턴스가 쓰는 RD의 MUXDT Memory_Address_and_Size는 TABLE로 나가고, 인스턴스가 없는
    ResponseOnEvent·LinkControl 프로토콜 템플릿은 DIAG-SERVICE가 없다(템플릿 자체는 ODX에 나가지 않는다).
    그 밖의 원문 참조는 사용으로 취급한다. 알 수 없는 원문의 의미를 추측해서 범위를 줄이지 않으며,
    XML을 다시 해석하지 못하면 전역 차단을 유지한다.
    """
    unused = set()
    for source in s.rows("source_files"):
        cancel()
        if source["kind"] != "cdd":
            continue
        try:
            root = ET.fromstring(source["content"], ET.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
        except (ET.XMLSyntaxError, TypeError, ValueError):
            continue
        if any(isinstance(n, ET._Entity) for n in root.iter()):
            continue
        tree = root.getroottree()
        types = root.findall("ECUDOC/DATATYPES/*")
        owners = {n: top for top in types for n in top.iter()}
        # 템플릿 소유자: 쓰일 때만 안의 참조가 살아난다.
        templates = root.findall("ECUDOC/PROTOCOLSERVICES/PROTOCOLSERVICE") + root.findall("ECUDOC/DCLTMPLS/DCLTMPL")
        for top in templates:
            for n in top.iter():
                owners.setdefault(n, top)
        targets = {}
        ambiguous = False
        for node, owner in owners.items():
            for key in ("id", "oid"):
                value = node.get(key)
                if value:
                    if value in targets and targets[value] is not owner:
                        ambiguous = True
                    targets[value] = owner
        if ambiguous:
            continue
        dependencies = defaultdict(set)
        live = set()
        for node in root.iter():
            cancel()
            owner = owners.get(node)
            values = [v for k, v in node.attrib.items() if k not in ("id", "oid")]
            values += [node.text or ""]
            for value in values:
                for token in re.findall(r"[\w.-]+", value):
                    target = targets.get(token)
                    if target is not None:
                        if owner is None:
                            live.add(target)
                        else:
                            dependencies[owner].add(target)
        queue = deque(live)
        while queue:
            cancel()
            for target in dependencies[queue.popleft()] - live:
                live.add(target)
                queue.append(target)
        # ID가 없는 정의는 사용 여부를 입증할 수 없어 차단을 유지한다.
        unused.update((source["id"], tree.getpath(node)) for node in types
                      if node not in live and (node.get("id") or node.get("oid")))
    return unused


def variant_pattern_params(s, vid):
    """schema 11 변형 식별 패턴 → [[(서비스 Qualifier, 응답 PARAM SHORT-NAME, 기대값), ...], ...] (패턴 번호 순서).
    ODX 대응(Vector 출력 UDS-ExampleEcu-5.0.2.pdx): MATCHING-PARAMETER = EXPECTED-VALUE, DIAG-COMM-SNREF(서비스), OUT-PARAM-IF-SNREF(긍정 응답 PARAM).
    서비스는 이 변형이 쓰는 활성 서비스 중 같은 Qualifier(인스턴스 사본도 같은 이름), 파라미터는 긍정 응답 최상위의 같은 Qualifier 데이터 객체다.
    ASCII 값만 근거가 있으므로 데이터 타입이 ascii가 아니면 거부한다. 풀 수 없으면 DomainError."""
    rows = sorted((p for p in s.rows("variant_patterns") if p["variant_id"] == vid), key=lambda p: (p["pattern"], p["ordinal"]))
    if not rows: return []
    instances = {v["instance_id"] for v in s.rows("variant_instances") if v["variant_id"] == vid}
    services = {}
    for x in s.rows("services"):
        if x["instance_id"] in instances and x["enabled"]:
            services.setdefault(s.get("objects", x["id"])["qualifier"], []).append(x)
    patterns = {}
    for p in rows:
        name = s.get("objects", p["service_id"])["qualifier"]
        found = services.get(name, [])
        if len(found) != 1: raise DomainError(f"변형 식별 서비스 {name}가 이 변형에 없거나(비활성 포함) 여러 개입니다.")
        positive = [t for t in s.rows("telegrams") if t["service_id"] == found[0]["id"] and t["kind"] == "positive"]
        if len(positive) != 1: raise DomainError(f"변형 식별 서비스 {name}의 긍정 응답을 찾지 못했습니다.")
        fields = s.flatten(positive[0]["structure_id"])
        matches = [i for i, f in enumerate(fields) if f["kind"] == "data" and s.get("objects", f["object"]["id"])["qualifier"] == p["parameter"]]
        if len(matches) != 1: raise DomainError(f"변형 식별 값 {p['parameter']}을(를) {name}의 긍정 응답에서 하나로 찾지 못했습니다.")
        if fields[matches[0]]["type"]["encoding"] != "ascii": raise DomainError(f"변형 식별 값 {p['parameter']}이 ASCII 타입이 아닙니다(다른 타입의 ODX 대응은 확인하지 못함).")
        patterns.setdefault(p["pattern"], []).append((name, f"P_{matches[0]}", p["expected_value"]))
    return [patterns[k] for k in sorted(patterns)]


def validate_export(s, variants, cancel=lambda: None):
    included = export_objects(s, variants, cancel)
    report = validate(s, cancel)
    unused = unused_source_types(s, cancel)
    for issue in report["issues"]:
        oid = issue["object_id"]
        reason = ""
        if oid and oid in s.indices.get("objects", {}) and oid not in included:
            reason = "선택한 변형의 출력 참조 범위 밖입니다."
        elif not oid and (issue.get("source_file_id"), issue.get("node_path")) in unused:
            reason = "원본 전체에서 사용되지 않는 데이터 타입 정의입니다."
        issue["in_scope"] = not bool(reason)
        issue["scope_reason"] = reason
        if reason:
            issue["blocks_export"] = False
    # 출력기에서 이미 차단하는 의미를 파일 작성 전에 화면에도 드러낸다.
    def mapping_issue(oid, message, field=""):
        issue=asdict(Issue("VAL-012", message, oid, field, "warning", RULE_REMEDY["VAL-012"], True))
        issue.update(in_scope=True, scope_reason="")
        report["issues"].append(issue)
    restricted={c["service_id"] for c in s.rows("service_conditions") if not c["allowed"] and c["service_id"] in included}
    for oid in sorted(restricted):
        mapping_issue(oid,"실행 가능 상태가 제한된 서비스의 ODX 대응 근거가 없습니다.","conditions")
    for t in s.rows("data_types"):
        if t["id"] in included and t["encoding"] in ("unsigned","signed") and t["bit_length"]>32:
            mapping_issue(t["id"],"ODX 정수 타입 매핑은 현재 32비트까지입니다.","bit_length")
    for d in s.rows("data_objects"):
        if d["id"] in included and d["constant_text"] is not None:
            mapping_issue(d["id"],"데이터 객체 상수의 ODX 매핑은 추가 구현이 필요합니다.","constant_text")
    for vid in variants:
        try: variant_pattern_params(s, vid)
        except DomainError as e: mapping_issue(vid, str(e), "patterns")
    for m in s.rows("structure_members"):
        if m["structure_id"] in included and m["kind"]=="literal" and len(m["literal_bytes"])*8>32:
            mapping_issue(m["structure_id"],"32비트 초과 고정값은 ODX 변환 범위를 벗어납니다.")
    report.update(variants=list(variants), object_count=len(included),
                  blocking_count=sum(i["blocks_export"] for i in report["issues"]))
    return report
