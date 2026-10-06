"""Stored dependency graph for reference impact, independent of Qt and SQL."""
from collections import defaultdict, deque


def reference_graph(snapshot):
    graph = defaultdict(dict)

    def link(target, dependent, relation):
        if target and dependent:
            graph[target][dependent] = relation

    for row in snapshot.rows("data_objects"):
        link(row["data_type_id"], row["id"], "데이터 타입 사용")
    for row in snapshot.rows("structure_members"):
        for field in ("data_object_id", "nested_structure_id", "did_id"):
            link(row.get(field), row["structure_id"], "구조 멤버")
    for table in ("dids", "data_types", "extended_records"):
        for row in snapshot.rows(table):
            link(row.get("structure_id"), row["id"], "데이터 구조 사용")
    for row in snapshot.rows("telegrams"):
        link(row["structure_id"], row["service_id"], "요청·응답 구조 사용")
    for row in snapshot.rows("muxes"):
        link(row.get("default_structure_id"), row["structure_id"], "MUX 기본 구조")
        link(row["selector_object_id"], row["structure_id"], "MUX 선택자")
    for row in snapshot.rows("mux_cases"):
        link(row["case_structure_id"], row["structure_id"], "MUX 케이스 구조")
    for row in snapshot.rows("instance_dids"):
        link(row["did_id"], row["instance_id"], "인스턴스 DID 연결")
    for row in snapshot.rows("services"):
        link(row["instance_id"], row["id"], "인스턴스의 서비스")
    # 부정 응답 코드 → 사용 서비스: service_nrcs.code ↔ nrcs.code(export_scope와 같은 연결).
    nrc_by_code = {row["code"]: row["id"] for row in snapshot.rows("nrcs")}
    for row in snapshot.rows("service_nrcs"):
        link(nrc_by_code.get(row["code"]), row["service_id"], "NRC 사용")
    for table in ("snapshot_default_dids", "snapshot_dtc_dids"):
        for row in snapshot.rows(table):
            link(row["did_id"], row["instance_id"], "스냅샷 DID 사용")
            link(row.get("dtc_id"), row["instance_id"], "DTC별 스냅샷 사용")
    for table in ("snapshot_records", "extended_records"):
        for row in snapshot.rows(table):
            link(row["id"], row["instance_id"], "고장 메모리 레코드 사용")
    for row in snapshot.rows("service_conditions"):
        link(row["state_id"], row["service_id"], "실행 조건")
        link(row["group_id"], row["service_id"], "실행 조건 그룹")
    for row in snapshot.rows("service_transitions"):
        link(row["from_state_id"], row["service_id"], "상태 전이 출발")
        link(row["to_state_id"], row["service_id"], "상태 전이 도착")
    for row in snapshot.rows("state_groups"):
        link(row["initial_state_id"], row["id"], "초기 상태")
    for table, column in (("variant_dids", "did_id"), ("variant_dtcs", "dtc_id"),
                          ("variant_instances", "instance_id"), ("variant_classes", "class_id"),
                          ("variant_jobs", "job_id")):
        for row in snapshot.rows(table):
            link(row[column], row["variant_id"], "변형에 포함")
    for row in snapshot.rows("diagnostic_instances"):
        link(row["class_id"], row["id"], "클래스의 인스턴스")
    # Services inherit variant membership without linking back to the instance.
    variants = defaultdict(set)
    for row in snapshot.rows("variant_instances"):
        variants[row["instance_id"]].add(row["variant_id"])
    for row in snapshot.rows("services"):
        for variant in variants[row["instance_id"]]:
            link(row["id"], variant, "변형에 포함")
    return graph


def reference_impact(snapshot, object_id):
    snapshot.get("objects", object_id)
    graph = reference_graph(snapshot)
    # Propagate variant membership through all nested structures/types, once per edge/variant.
    incoming = defaultdict(set)
    for target, dependents in graph.items():
        for dependent in dependents:
            incoming[dependent].add(target)
    memberships = defaultdict(set)
    pending = deque()
    for variant in snapshot.rows("variants"):
        vid = variant["id"]
        memberships[vid].add(vid)
        pending.append((vid, vid))
    while pending:
        oid, vid = pending.popleft()
        for target in incoming[oid]:
            if vid not in memberships[target]:
                memberships[target].add(vid)
                pending.append((target, vid))
    rows, visited = [], {object_id}
    pending = deque([(object_id, 0)])
    while pending:
        oid, depth = pending.popleft()
        for dependent, relation in sorted(graph[oid].items()):
            if dependent in visited:
                continue
            visited.add(dependent)
            pending.append((dependent, depth + 1))
            obj = snapshot.get("objects", dependent)
            vids = sorted(memberships[dependent], key=lambda vid: (snapshot.name(vid), vid))
            rows.append({"id": dependent, "kind": obj["kind"], "name": snapshot.name(dependent),
                         "qualifier": obj["qualifier"], "relation": relation, "depth": depth + 1,
                         "reach": "직접 사용" if depth == 0 else "간접 영향",
                         "variants": ", ".join(snapshot.name(vid) for vid in vids),
                         "variant": vids[0] if vids else None})
    return sorted(rows, key=lambda row: (row["depth"], row["kind"], row["name"], row["id"]))
