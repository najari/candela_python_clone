"""CDD 진단 동작(상태·서비스·DTC) 해석. 결과는 DTO이며 DB를 변경하지 않는다.

해석 경로 (CANdelaStudio 13 예제 ABS_ESP-Example.cdd로 확인):
  VAR/DIAGCLASS/DIAGINST ─tmplref→ DCLTMPL (SHSTATIC·SHPROXY 슬롯 정의)
  DIAGINST/SERVICE ─tmplref→ DCLSRVTMPL ─tmplref→ PROTOCOLSERVICE (REQ/POS/NEG 구성요소)
  PROTOCOLSERVICE 구성요소:
    CONSTCOMP        고정값 (bl, v)
    STATICCOMP       인스턴스 STATICVALUE로 채움. SHSTATIC/STATICCOMPREF가 구성요소를 가리킨다.
    SIMPLEPROXYCOMP  인스턴스 SIMPLECOMPCONT로 채움. SHPROXY/PROXYCOMPREF가 구성요소를 가리킨다.
                     dest=data → DIDDATAREF 또는 DATAOBJ, dest=resCode → SPECDATAOBJ/NEGRESCODEPROXY
  SERVICE mayBeExec/trans: STATEGROUPS의 전역 상태 번호(1부터, 문서 순서).

해석한 배치가 자체 고정 UDS 프로파일(0x10/0x11/0x22/0x2E/0x31/0x3E)과 바이트 단위로 같을 때만 가져온다.
그 밖의 서비스·구성요소는 원문을 보존하고 출력 차단 대상으로 둔다.
"""
from lxml import etree as ET

from .domain import DomainError, integer

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
META = ("NAME", "QUAL", "DESC")
PROFILE_SIDS = {0x10, 0x11, 0x22, 0x2E, 0x31, 0x3E}
STATE_KINDS = {"session", "security"}


def tuv(node, tag):
    # 빈 TUV(번역 안 됨, 예: uptodate="0")는 값이 아니다. 남기면 이름이 빈 문자열로 선택된다(IO_HIL Test.cdd의 en-US).
    texts = {t.get(XML_LANG, "en-US"): "".join(t.itertext()) for t in node.findall(f"{tag}/TUV")}
    return {k: v for k, v in texts.items() if v.strip()}


def preferred(locales):
    return locales.get("en-US", next(iter(locales.values()), "")) if locales else ""


def index_list(text):
    """'(2,3)' → [2, 3]. 형식이 다르면 DomainError."""
    if text is None: return None
    body = text.strip()
    if not (body.startswith("(") and body.endswith(")")): raise DomainError(f"상태 목록 형식 미지원: {text}")
    body = body[1:-1].strip()
    return [integer(x, 1, 10000) for x in body.split(",")] if body else []


def parse_bitfield(item, ids, type_ids, bits_of, common):
    """CDD STRUCT(비트필드) → {"kind": "bitfield", "container_bits": 8, "members": [...]}.

    멤버는 컨테이너의 최하위 비트부터 채운다. 근거: ABS 예제 DID 010A Direction_of_rotation(컨테이너 HexDump_1Byte,
    2비트 멤버 4개)의 Vector ODX 출력(Repository/ODXEnvelope/010A_Wheel_Speed.odx-d)이 BIT-POSITION 0, 2, 4, 6이다.
    같은 예제의 CommunicationControl CommType(1비트 Normal·Network·Diagnostic + 5비트 reserved)도 UDS 규격의 비트 0=정상 메시지와 맞는다.
    8비트 컨테이너만 근거가 있어 다른 크기는 미지원으로 둔다."""
    container = ids.get(item.get("dtref"))
    cv = container.find("CVALUETYPE") if container is not None else None
    if cv is None or cv.get("enc") != "uns" or cv.get("bl") not in ("8", "16", "24", "32"):
        raise DomainError("비트필드 컨테이너가 8~32비트(바이트 단위) 부호 없는 타입이 아님")
    container_bits = int(cv.get("bl"))
    # 8비트보다 큰 컨테이너(21차): 8비트와 같은 규칙(첫 멤버 = 컨테이너 값의 최하위 비트, big endian 바이트)으로 가져온다.
    # Vector 출력으로 확인하지 못했으므로 호출자가 미검증으로 표시한다. little endian 컨테이너는 바이트 배치를 정할 수 없어 보존한다.
    if container_bits > 8 and cv.get("bo") != "21": raise DomainError("little endian 비트필드 컨테이너 미지원")
    if item.get("spec") != "no": raise DomainError("비트필드 사양 미지원")
    members = []
    for m in item:
        if m.tag in META: continue
        if m.tag == "GAPDATAOBJ":
            # 비활성 비트(예: DTC 상태 바이트의 WarningIndicatorRequested). 근거: 도움말 100_ODXExport "StatusOfDtc" —
            # 활성화되지 않은 상태 비트는 RESERVED PARAM. Import/UDS-ExampleEcu.pdx(CANdelaStudio 5.0 출력)의 DtcStatusbyte도 같다.
            if any(x.tag not in META for x in m): raise DomainError("비트필드 빈 비트 확장 의미 미지원")
            members.append({**common(m), "kind": "gap", "bits": integer(m.get("bl"), 1, 32)})
            continue
        if m.tag != "DATAOBJ" or m.get("dtref") not in type_ids or m.get("spec") != "no": raise DomainError("비트필드 멤버 미지원")
        if any(x.tag not in META for x in m): raise DomainError("비트필드 멤버 확장 의미 미지원")
        members.append({**common(m), "type_external_id": m.get("dtref"), "kind": "data"})
    if any(m["kind"] == "data" and not bits_of(m["type_external_id"]) for m in members): raise DomainError("비트필드 멤버가 가변 길이 타입")
    used = sum(m["bits"] if m["kind"] == "gap" else bits_of(m["type_external_id"]) for m in members)
    if not any(m["kind"] == "data" for m in members) or used > container_bits:
        raise DomainError(f"비트필드 멤버 비트 합이 컨테이너({container_bits}비트)를 넘음")
    result = {**common(item), "kind": "bitfield", "container_bits": container_bits, "members": members}
    if container_bits > 8: result["unverified"] = f"{container_bits}비트 비트필드: 비트 순서를 Vector 출력으로 확인하지 못함"
    return result


ATTRIBUTE_TAGS = ("ENUM", "UNS", "SGN", "STR", "CSTR")


def user_attributes(node, ids):
    """사용자 정의 속성(ENUM/UNS/SGN/STR/CSTR attrref) → object_attributes 행. 값은 원문 그대로 보존한다."""
    result = []
    for a in node:
        if a.tag not in ATTRIBUTE_TAGS or not a.get("attrref"): continue
        definition = ids.get(a.get("attrref"))
        key = "cdd." + ((definition.findtext("QUAL") if definition is not None else None) or a.get("attrref"))
        if a.tag == "ENUM":
            label = next((preferred(tuv(t, ".")) for t in (definition.findall("ETAG") if definition is not None else []) if t.get("v") == a.get("v")), None)
            if label is not None: result.append({"key": key, "value_type": "text", "value_text": label})
            result.append({"key": key + "#raw" if label is not None else key, "value_type": "integer", "value_text": str(integer(a.get("v")))})
        elif a.tag in ("UNS", "SGN"):
            value = integer(a.get("v"))
            if definition is not None and definition.get("df") == "hex" and value >= 0:
                result.append({"key": key, "value_type": "hex", "value_text": f"0x{value:X}"})
            else:
                result.append({"key": key, "value_type": "integer", "value_text": str(value)})
        else:
            text = a.findtext("COMMONSTRING") if a.tag == "CSTR" else None
            texts = tuv(a, ".") if text is None else {}
            value = text if text is not None else preferred(texts) if texts else a.get("v", "")
            result.append({"key": key, "value_type": "text", "value_text": value})
    return result


class Model:
    def __init__(self, root, doc, tree, common, unsupported, type_ids, did_numbers, cancel, type_parser=None):
        self.root, self.doc, self.tree = root, doc, tree
        self.common, self.unsupported, self.cancel = common, unsupported, cancel
        self.type_ids, self.did_numbers = type_ids, did_numbers
        # 서비스 구성요소가 만드는 타입(GroupOfDtc 지역 표, 내용 없는 any 슬롯). 서비스 해석이 성공한 것만 모은다.
        self.type_parser, self.extra_types = type_parser, {}
        self.variable_types, self.variable_dids = set(), set()  # 가변 길이 타입·DID(interchange가 채운다)
        self.current_fault_memory = None  # 서비스를 해석 중인 인스턴스의 FAULTMEMORY 정의
        self.ids = {e.get("id"): e for e in root.iter() if e.get("id")}
        self.states = []         # 전역 번호 순서의 (group_index, state_record)
        self.groups = []
        self.nrc_by_id = {n.get("id"): integer(n.get("v"), 1, 255) for n in doc.findall("NEGRESCODES/NEGRESCODE") if n.get("v")}

    def ref(self, node, attr):
        target = self.ids.get(node.get(attr))
        if target is None: raise DomainError(f"해결되지 않은 참조 {attr}={node.get(attr)}")
        return target

    # ------------------------------------------------------------------ 상태
    def state_groups(self):
        for g in self.doc.findall("STATEGROUPS/STATEGROUP"):
            spec = g.get("spec")
            # 세션·보안 외 그룹(UDS 13 템플릿 authorization, 5.x 문서의 spec 없는 Communication 등)도 상태·조건·전이를 가져온다.
            # ODX STATE-CHART 대응은 확인하지 못해 그룹을 미검증(supported=0)으로 두고 출력을 막는다(21차).
            kind = spec if spec in STATE_KINDS or spec == "authorization" else "other:" + (self.common(g)["qualifier"] or str(len(self.groups)))
            record = {**self.common(g), "kind": kind, "spec": spec, "states": [], "supported": True}
            if spec not in STATE_KINDS: record["unverified"] = [f"상태 그룹 종류 {spec or '(없음)'}: ODX 상태 차트 대응 미확인"]
            for s in g.findall("STATE"):
                st = {**self.common(s), "code": None}
                record["states"].append(st)
                self.states.append((len(self.groups), st))
            if not record["states"]:
                record["supported"] = False
                self.unsupported(g, "상태가 없는 상태 그룹")
            self.groups.append(record)
        kinds = [g["kind"] for g in self.groups if g["supported"]]
        if len(kinds) != len(set(kinds)):
            raise DomainError("같은 종류의 상태 그룹이 여러 개입니다.")
        return self.groups

    def state(self, index):
        if not 1 <= index <= len(self.states): raise DomainError(f"상태 번호 {index}가 범위를 벗어남")
        group_index, st = self.states[index - 1]
        if not self.groups[group_index]["supported"]: raise DomainError("미지원 상태 그룹의 상태를 참조")
        return group_index, st

    def conditions(self, svc):
        """mayBeExec/trans → ({group_index: {state_ext_id: allowed}}, [(group_index, from_ext, to_ext)]).

        해석: 목록에 상태가 하나도 없는 그룹은 제한이 없다(모든 상태 허용).
        근거(CANdelaStudio 13 도움말 30 Features/State Dependency): 상태 그룹은 병렬로 적용되고 한 그룹에서라도 막히면
        실행이 막힌다. 한 그룹의 모든 상태에서 막힌 서비스는 일관성 오류[STTR-R200]로 출력이 거부되므로, 예제의
        mayBeExec="(4,5)"(보안 상태만)가 세션 그룹 전체 차단일 수는 없다. 전이도 차단도 없는 그룹은 "irrelevant"다.
        초기 상태는 도움말 정의대로 그룹의 첫 상태다.
        """
        allowed = index_list(svc.get("mayBeExec"))
        pairs = index_list(svc.get("trans"))
        matrix = {}
        if allowed:
            listed = {self.state(i)[0] for i in allowed}
            for g in listed:
                matrix[g] = {st["external_oid"]: False for st in self.groups[g]["states"]}
            for i in allowed:
                g, st = self.state(i)
                matrix[g][st["external_oid"]] = True
        transitions = []
        if pairs:
            if len(pairs) % 2: raise DomainError("상태 전이 목록의 쌍이 맞지 않음")
            seen = set()
            for a, b in zip(pairs[0::2], pairs[1::2]):
                ga, sa = self.state(a)
                gb, sb = self.state(b)
                if ga != gb: raise DomainError("서로 다른 상태 그룹 사이의 전이")
                if ga not in matrix:
                    matrix[ga] = {st["external_oid"]: True for st in self.groups[ga]["states"]}
                if not matrix[ga][sa["external_oid"]]: raise DomainError("허용되지 않은 출발 상태의 전이")
                if (ga, sa["external_oid"]) in seen: raise DomainError("같은 출발 상태의 전이가 여러 개")
                seen.add((ga, sa["external_oid"]))
                transitions.append((ga, sa["external_oid"], sb["external_oid"]))
        return matrix, transitions

    # ------------------------------------------------------------------ 서비스
    def type_bits(self, dtref):
        if dtref in self.variable_types: return 0
        t = self.ids.get(dtref)
        cv = t.find("CVALUETYPE") if t is not None else None
        if cv is None or cv.get("sz") != "no": raise DomainError("정적 구성요소의 타입 길이를 알 수 없음")
        return integer(cv.get("bl"), 1, 64)

    def mux(self, muxdt, selector, owner_tag):
        """MUXDT → MUX 멤버(21차). 선택자는 같은 전문의 앞쪽 데이터 객체(DATAOBJ@dataObjectRef 또는 MUXCOMP@selref).
        CASE s..e별 STRUCTURE와 기본 STRUCTURE(없으면 None). 선택자 값 범위는 정수만 받는다."""
        if selector is None: raise DomainError(f"{owner_tag}의 선택자 참조 없음")
        def items(structure):
            result = []
            for x in structure:
                if x.tag in META: continue
                if x.tag == "GAPDATAOBJ":
                    result.append({**self.common(x), "kind": "gap", "bits": integer(x.get("bl"), 1, 64)}); continue
                if x.tag == "STRUCT":
                    field = parse_bitfield(x, self.ids, self.type_ids, self.type_bits, self.common)
                    if field.get("unverified"): raise DomainError("MUX 케이스 안의 8비트보다 큰 비트필드 미지원")
                    result.append(field); continue
                if x.tag != "DATAOBJ" or x.get("spec") != "no" or x.get("dtref") not in self.type_ids or x.get("dtref") in self.variable_types:
                    raise DomainError(f"MUX 케이스 내용 미지원: {x.tag}")
                if any(y.tag not in META for y in x): raise DomainError("MUX 케이스 데이터 객체 확장 의미 미지원")
                result.append({**self.common(x), "type_external_id": x.get("dtref"), "kind": "data"})
            return result
        extra = {c.tag for c in muxdt} - set(META) - {"CVALUETYPE", "PVALUETYPE", "STRUCTURE", "CASE"}
        if extra: raise DomainError("MUX 확장 항목 미지원: " + ", ".join(sorted(extra)))
        cases = []
        for case in muxdt.findall("CASE"):
            structure = case.find("STRUCTURE")
            if structure is None or {c.tag for c in case} - set(META) - {"STRUCTURE"}: raise DomainError("MUX 케이스 형식 미지원")
            lower, upper = integer(case.get("s"), 0, (1 << 32) - 1), integer(case.get("e", case.get("s")), 0, (1 << 32) - 1)
            if upper < lower: raise DomainError("MUX 케이스 범위가 거꾸로임")
            cases.append({"lower": lower, "upper": upper, "members": items(structure)})
        if not cases: raise DomainError("케이스가 없는 MUX")
        default = muxdt.find("STRUCTURE")
        return {**self.common(muxdt), "kind": "mux", "muxdt_id": muxdt.get("id"), "selector": selector, "cases": cases,
                "default": items(default) if default is not None else None}

    def data_members(self, slot, allow_mux=False):
        members = []
        for item in slot:
            if item.tag in META: continue
            target = self.ids.get(item.get("dtref")) if item.tag == "DATAOBJ" else None
            if allow_mux and target is not None and target.tag == "MUXDT" and item.get("spec") == "no":
                # 슬롯 데이터 객체의 타입이 MUXDT: 선택자 = dataObjectRef(같은 슬롯·전문의 앞쪽 데이터 객체). 예: 0x23 addressAndSize.
                members.append({**self.mux(target, item.get("dataObjectRef"), "MUX 데이터 객체"), **self.common(item)})
                continue
            if item.tag == "STRUCT":
                members.append(parse_bitfield(item, self.ids, self.type_ids, self.type_bits, self.common))
                continue
            if item.tag == "DATAOBJ" and item.get("spec") == "no" and item.get("dtref") not in self.type_ids:
                target = self.ids.get(item.get("dtref"))
                raise DomainError(f"슬롯 데이터 객체의 타입을 가져오지 못함: {target.tag if target is not None else '참조 없음'}")
            if item.tag != "DATAOBJ" or item.get("spec") != "no" or item.get("dtref") not in self.type_ids:
                raise DomainError(f"슬롯 내용 미지원: {item.tag}{'/' + item.get('spec') if item.get('spec') else ''}")
            if any(x.tag not in META for x in item): raise DomainError("데이터 객체 확장 의미 미지원")
            members.append({**self.common(item), "type_external_id": item.get("dtref"), "kind": "data"})
        return members

    def resolve(self, inst, svc):
        """서비스 하나를 배치 목록으로 해석한다. 항목:
        ('literal', bytes) | ('did', ext_id) | ('data', member) | ('dtc', None) | ('iter', {qualifier, name, min_items, items}).
        반환: (부분별 배치, NRC 목록, 이 서비스가 만든 타입 목록)."""
        cls_tmpl = self.ref(inst, "tmplref")
        srv_tmpl = self.ref(svc, "tmplref")
        protocol = self.ref(srv_tmpl, "tmplref")
        if protocol.tag != "PROTOCOLSERVICE": raise DomainError("프로토콜 서비스가 아닌 템플릿")
        static_of, proxy_of = {}, {}
        for sh in cls_tmpl.findall("SHSTATIC"):
            for r in sh.findall("STATICCOMPREF"): static_of[r.get("idref")] = sh.get("id")
        for sh in cls_tmpl.findall("SHPROXY"):
            for r in sh.findall("PROXYCOMPREF"): proxy_of[r.get("idref")] = sh.get("id")
        values = {v.get("shstaticref"): v.get("v") for v in inst.findall("STATICVALUE")}
        slots = {c.get("shproxyref"): c for c in inst.findall("SIMPLECOMPCONT")}
        mux_slots = {c.get("shproxyref"): c for c in inst.findall("MUXCOMPCONT")}
        nrcs, new_types = [], {}

        def fixed_bits(comp, bits):
            if comp.get("minbl") != str(bits) or comp.get("maxbl") != str(bits):
                raise DomainError(f"{comp.tag} 길이가 {bits}비트가 아님")

        def components(node, part, nested):
            layout = []
            for comp in node:
                if comp.tag in META: continue
                slot = slots.get(proxy_of.get(comp.get("id")))
                if comp.tag == "CONSTCOMP":
                    bits = integer(comp.get("bl"), 1, 64)
                    if bits % 8: raise DomainError("바이트 경계가 아닌 고정 구성요소")
                    layout.append(("literal", integer(comp.get("v"), 0, (1 << bits) - 1).to_bytes(bits // 8, "big")))
                elif comp.tag == "STATICCOMP":
                    raw = values.get(static_of.get(comp.get("id")))
                    if raw is None: raise DomainError("정적 구성요소 값 없음")
                    # 5.x 문서는 타입 참조 대신 구성요소에 비트 길이(bl)를 둔다(CONSTCOMP@bl과 같은 의미).
                    bits = integer(comp.get("bl"), 1, 64) if comp.get("dtref") is None and comp.get("bl") else self.type_bits(comp.get("dtref"))
                    if bits % 8: raise DomainError("바이트 경계가 아닌 정적 구성요소")
                    layout.append(("literal", integer(raw, 0, (1 << bits) - 1).to_bytes(bits // 8, "big")))
                elif comp.tag == "EOSITERCOMP":
                    # PDU 끝까지 반복(ODX END-OF-PDU-FIELD). 근거: UDS-ExampleEcu.pdx(CANdelaStudio 5.0 출력)의
                    # ListOfDTC END-OF-PDU-FIELD → BASIC-STRUCTURE(DTC + StatusOfDtc).
                    if nested: raise DomainError("중첩 반복 구성요소 미지원")
                    if any(c.tag not in META for c in comp.itersiblings()): raise DomainError("반복 구성요소 뒤에 다른 구성요소가 있음")
                    items = components(comp, part, True)
                    if not items: raise DomainError("빈 반복 구성요소")
                    layout.append(("iter", {**self.common(comp), "min_items": integer(comp.get("minNumOfItems", "0"), 0, 65535), "items": items}))
                elif comp.tag == "STATUSDTCPROXYCOMP":
                    # DTC 상태 바이트: 인스턴스 슬롯의 STRUCT(8비트 비트필드). 도움말 100_ODXExport "StatusOfDtc".
                    if comp.get("dest") != "dtcStatus": raise DomainError(f"상태 구성요소 종류 미지원: {comp.get('dest')}")
                    fixed_bits(comp, 8)
                    content = [c for c in (slot if slot is not None else []) if c.tag not in META]
                    if len(content) != 1 or content[0].tag != "STRUCT": raise DomainError("DTC 상태 슬롯이 비트필드 하나가 아님")
                    layout.append(("data", parse_bitfield(content[0], self.ids, self.type_ids, self.type_bits, self.common)))
                elif comp.tag == "CONTENTCOMP":
                    # 프로토콜 서비스 템플릿에 내용이 고정된 구성요소(인스턴스 슬롯이 아님). 내용은 슬롯과 같은 SIMPLECOMPCONT(데이터 객체 나열)이므로
                    # 배치는 슬롯 내용과 같다. 예: Vector_UDS 템플릿 0x19 01 응답 DTCCount, 0x2C 01 SourceDID·Position·Size.
                    extra = {c.tag for c in comp} - set(META) - {"SIMPLECOMPCONT"}
                    content = comp.findall("SIMPLECOMPCONT")
                    if extra or len(content) != 1: raise DomainError("CONTENTCOMP 내용이 SIMPLECOMPCONT 하나가 아님")
                    members = self.data_members(content[0], allow_mux=not nested)
                    if not members: raise DomainError("빈 CONTENTCOMP")
                    layout.extend(("mux" if m.get("kind") == "mux" else "data", m) for m in members)
                elif comp.tag == "GROUPOFDTCPROXYCOMP":
                    layout.append(("data", self.group_of_dtc(comp, slot, new_types)))
                elif comp.tag == "SIMPLEPROXYCOMP":
                    dest = comp.get("dest")
                    if dest == "resCode":
                        if part != "NEG": raise DomainError("부정 응답 외의 응답 코드 슬롯")
                        if slot is not None:
                            for proxy in slot.iter("NEGRESCODEPROXY"):
                                code = self.nrc_by_id.get(proxy.get("idref"))
                                if code is None: raise DomainError("해결되지 않은 NRC 참조")
                                if code not in nrcs: nrcs.append(code)
                        continue
                    if dest == "dtc" and comp.get("minbl") == "16" and comp.get("maxbl") == "16":
                        # KWP2000의 16비트 DTC 자리(21차). 변형 DTC 표(24비트 DTC-DOP)와 연결할 근거가 없어 16비트 값 필드로 가져오고 미검증으로 둔다.
                        key = comp.get("id") + "#dtc16"
                        new_types[key] = {**self.common(comp), "external_id": key, "encoding": "unsigned", "bit_length": 16, "array_length": 1,
                                          "byte_order": "big", "conversion": "identity", "unit": "", "entries": []}
                        layout.append(("data", {**self.common(comp), "type_external_id": key, "kind": "data", "unverified": "16비트 DTC 자리: DTC 표 대응 미확인"}))
                        continue
                    if dest == "dtc":
                        # DTC 값: 슬롯의 고장 메모리 레코드(RECORDDATAOBJ rtSpec=faultMemory) → 변형의 DTC 표(DTC-DOP).
                        fixed_bits(comp, 24)
                        content = [c for c in (slot if slot is not None else []) if c.tag not in META]
                        if len(content) != 1 or content[0].tag != "RECORDDATAOBJ" or content[0].get("rtSpec") != "faultMemory":
                            raise DomainError("DTC 슬롯이 고장 메모리 레코드 하나가 아님")
                        layout.append(("dtc", None))
                        continue
                    if dest == "any" and slot is None and comp.get("maxbl") is None:
                        # 최대 길이 없는 자리 = PDU 끝까지 이어지는 가변 바이트 필드. 근거: UDS-ExampleEcu.pdx의
                        # DtcSnapshotRecordNumber DOP = A_BYTEFIELD, MIN-MAX-LENGTH-TYPE, TERMINATION=END-OF-PDU, MIN-LENGTH 1(minbl=8).
                        if nested: raise DomainError("반복 항목 안의 가변 길이 any 구성요소")
                        if any(c.tag not in META for c in comp.itersiblings()): raise DomainError("가변 길이 any 구성요소 뒤에 다른 구성요소가 있음")
                        minimum = integer(comp.get("minbl", "0"), 0, 8 * 1048576)
                        if minimum % 8: raise DomainError("가변 길이 any 구성요소의 최소 길이가 바이트 단위가 아님")
                        key = comp.get("id") + "#bytes"
                        new_types[key] = {**self.common(comp), "external_id": key, "encoding": "bytes", "bit_length": 8, "array_length": None,
                                          "min_length": minimum // 8, "byte_order": "big", "conversion": "identity", "unit": "", "entries": []}
                        layout.append(("data", {**self.common(comp), "type_external_id": key, "kind": "data"}))
                        continue
                    if dest == "any" and slot is None:
                        # 내용을 정하지 않은 고정 길이 자리(예: 0x19 01 DtcCount). 근거: UDS-ExampleEcu.pdx의 DtcCount DOP =
                        # A_BYTEFIELD, BIT-LENGTH 16, IDENTICAL(= 구성요소 길이).
                        bits = integer(comp.get("maxbl"), 8, 8 * 1048576)
                        if comp.get("minbl") != comp.get("maxbl") or bits % 8: raise DomainError("가변 길이 any 구성요소")
                        key = comp.get("id") + "#bytes"
                        new_types[key] = {**self.common(comp), "external_id": key, "encoding": "bytes", "bit_length": 8, "array_length": bits // 8,
                                          "byte_order": "big", "conversion": "identity", "unit": "", "entries": []}
                        layout.append(("data", {**self.common(comp), "type_external_id": key, "kind": "data"}))
                        continue
                    if dest in ("sr", "sd", "edr", "ed"):
                        # 고장 메모리 레코드 자리. 번호(sr/edr)는 8비트, 데이터(sd/ed)는 번호·DTC에 따라 정해지는 가변 길이.
                        # 내용은 인스턴스 FAULTMEMORY 정의(fault_memory)를 따른다. 슬롯 내용은 쓰지 않는다.
                        fm = self.current_fault_memory
                        needed = "snapshot_records" if dest in ("sr", "sd") else "extended"
                        if not fm or not fm[needed]: raise DomainError(f"고장 메모리 레코드 정의 없음: {dest}")
                        if slot is not None and any(c.tag not in META for c in slot): raise DomainError(f"레코드 자리에 슬롯 내용이 있음: {dest}")
                        if dest in ("sr", "edr"): fixed_bits(comp, 8)
                        elif comp.get("maxbl") is not None: raise DomainError(f"레코드 데이터 최대 길이 지정 미지원: {dest}")
                        layout.append(("record", dest))
                        continue
                    if dest not in ("data", "any"): raise DomainError(f"슬롯 종류 미지원: {dest}")
                    if slot is None or len(slot) == 0:
                        if comp.get("must") == "1": raise DomainError("필수 데이터 슬롯이 비어 있음")
                        continue
                    refs = slot.findall("DIDDATAREF")
                    if refs:
                        if len(slot) != 1: raise DomainError("DID 참조와 다른 데이터가 섞인 슬롯")
                        layout.append(("did", refs[0].get("didRef")))
                    else:
                        layout.extend(("mux" if m.get("kind") == "mux" else "data", m) for m in self.data_members(slot, allow_mux=not nested))
                elif comp.tag == "MUXCOMP":
                    # 템플릿 MUX 구성요소(21차): 내용은 인스턴스 MUXCOMPCONT의 지역 MUXDT, 선택자는 MUXCOMP@selref.
                    if nested: raise DomainError("반복 항목 안의 MUX 미지원")
                    content = [c for c in (mux_slots.get(proxy_of.get(comp.get("id"))) if mux_slots.get(proxy_of.get(comp.get("id"))) is not None else []) if c.tag not in META]
                    if len(content) != 1 or content[0].tag != "MUXDT": raise DomainError("MUX 슬롯이 MUXDT 하나가 아님")
                    layout.append(("mux", {**self.mux(content[0], comp.get("selref"), "MUXCOMP"), **self.common(comp)}))
                else:
                    raise DomainError(f"전문 구성요소 미지원: {comp.tag}")
            return layout

        parts = {}
        for part in ("REQ", "POS", "NEG"):
            node = protocol.find(part)
            if node is None: raise DomainError(f"{part} 전문 정의 없음")
            parts[part] = components(node, part, False)
        return parts, nrcs, list(new_types.values())

    def group_of_dtc(self, comp, slot, new_types):
        """0x14 GroupOfDtc: 슬롯의 GODTCDATAOBJ. 값 표는 그 안의 지역 TEXTTBL(없으면 구성요소 dtref).
        근거: 도움말 100_ODXExport "GroupOfDtc"(TEXTTABLE DATA-OBJECT-PROP), UDS-ExampleEcu.pdx의 LocalTable DOP(24비트, All groups)."""
        if comp.get("dest") != "groupOfDtc": raise DomainError(f"DTC 그룹 구성요소 종류 미지원: {comp.get('dest')}")
        if comp.get("minbl") != comp.get("maxbl") or comp.get("minbl") not in ("16", "24"): raise DomainError("DTC 그룹 길이가 16·24비트가 아님")
        group_bits = int(comp.get("minbl"))  # 16비트 = KWP2000 groupOfDTC(21차, 미검증)
        content = [c for c in (slot if slot is not None else []) if c.tag not in META]
        if len(content) != 1 or content[0].tag != "GODTCDATAOBJ": raise DomainError("DTC 그룹 슬롯이 GODTCDATAOBJ 하나가 아님")
        item = content[0]
        # individualDtcs=1이면 도움말상 DTC-DOP를 쓰는 서비스가 하나 더 만들어진다. 근거 예제가 없어 보존한다.
        if item.get("spec") != "no" or item.get("individualDtcs", "0") != "0": raise DomainError("개별 DTC 지정·사양이 있는 DTC 그룹 미지원")
        tables = [c for c in item if c.tag not in META]
        if len(tables) > 1 or (tables and tables[0].tag != "TEXTTBL"): raise DomainError("DTC 그룹 지역 표 형식 미지원")
        if tables:
            if self.type_parser is None: raise DomainError("지역 타입을 해석할 수 없음")
            local = self.type_parser(tables[0])
            if local["encoding"] != "unsigned" or local["bit_length"] != group_bits: raise DomainError(f"DTC 그룹 표가 {group_bits}비트 부호 없는 값이 아님")
            new_types[local["external_id"]] = local
            type_ref = local["external_id"]
        else:
            type_ref = comp.get("dtref")
            if type_ref not in self.type_ids or self.type_bits(type_ref) != group_bits: raise DomainError(f"DTC 그룹 타입이 {group_bits}비트가 아님")
        member = {**self.common(item), "type_external_id": type_ref, "kind": "data"}
        if item.get("def") is not None: member["default_text"] = str(integer(item.get("def"), 0, (1 << group_bits) - 1))
        if group_bits != 24: member["unverified"] = "16비트 DTC 그룹(KWP2000): ODX 대응 미확인"
        return member

    def profile(self, parts):
        """해석한 배치를 자체 고정 프로파일 서비스 필드로 바꾼다. 바이트 단위로 같지 않으면 DomainError."""
        req, pos, neg = parts["REQ"], parts["POS"], parts["NEG"]
        if not req or req[0][0] != "literal" or len(req[0][1]) != 1: raise DomainError("SID가 없는 요청")
        sid = req[0][1][0]
        if sid not in PROFILE_SIDS: raise DomainError(f"고정 프로파일 밖의 SID 0x{sid:02X}")
        if [x for x in neg if x[0] != "literal"] or b"".join(x[1] for x in neg) != bytes([0x7F, sid]):
            raise DomainError("부정 응답 배치가 7F SID NRC 형식이 아님")
        if not pos or pos[0] != ("literal", bytes([sid + 0x40])): raise DomainError("긍정 응답 SID 불일치")

        def literal(item, size):
            if item[0] != "literal" or len(item[1]) != size: raise DomainError("헤더 배치가 프로파일과 다름")
            return int.from_bytes(item[1], "big")

        def members(items):
            if any(x[0] != "data" for x in items): raise DomainError("헤더 뒤에 데이터 객체가 아닌 구성요소")
            return [x[1] for x in items]

        fields = {"sid": sid, "subfunction": None, "routine_identifier": None, "did_ref": None, "request_members": [], "positive_members": []}
        if sid in (0x22, 0x2E):
            if len(req) < 2 or len(pos) < 2: raise DomainError("DID 서비스 헤더 부족")
            number = literal(req[1], 2)
            if literal(pos[1], 2) != number: raise DomainError("요청·응답 DID 번호 불일치")
            data_side, other = (pos, req) if sid == 0x22 else (req, pos)
            if len(data_side) != 3 or data_side[2][0] != "did" or len(other) != 2: raise DomainError("DID 데이터 참조 배치가 프로파일과 다름")
            did = data_side[2][1]
            if self.did_numbers.get(did) is None: raise DomainError("가져오지 못한 DID를 참조")
            if self.did_numbers[did] != number: raise DomainError("DID 번호와 정적 식별자 불일치")
            fields["did_ref"] = did
        elif sid == 0x31:
            if len(req) < 3 or len(pos) < 3: raise DomainError("루틴 헤더 부족")
            sub, routine = literal(req[1], 1), literal(req[2], 2)
            if literal(pos[1], 1) != sub or literal(pos[2], 2) != routine: raise DomainError("요청·응답 루틴 헤더 불일치")
            fields.update(subfunction=integer(sub, 0, 127), routine_identifier=routine, request_members=members(req[3:]), positive_members=members(pos[3:]))
        else:
            if len(req) < 2 or len(pos) < 2: raise DomainError("subfunction 없음")
            sub = literal(req[1], 1)
            if literal(pos[1], 1) != sub: raise DomainError("요청·응답 subfunction 불일치")
            fields.update(subfunction=integer(sub, 0, 127), request_members=members(req[2:]), positive_members=members(pos[2:]))
        return fields

    ATTRIBUTE_TAGS = ("ENUM", "UNS", "SGN", "STR", "CSTR")

    def unverified_members(self, fields):
        """배치 안 멤버의 미검증 사유(16비트 이상 비트필드, 16비트 DTC 그룹 등)와 미검증 DID 참조를 모은다."""
        def items(layout):
            for kind, payload in layout:
                if kind == "iter": yield from items(payload["items"])
                else: yield kind, payload
        if fields.get("profile") == "raw":
            layout = list(items(fields["request_layout"])) + list(items(fields["positive_layout"]))
        else:
            layout = [("data", m) for m in fields["request_members"] + fields["positive_members"]] + ([("did", fields["did_ref"])] if fields.get("did_ref") else [])
        reasons = []
        for kind, payload in layout:
            if kind == "data" and isinstance(payload, dict):
                if payload.get("unverified"): reasons.append(payload["unverified"])
                reasons += [m["unverified"] for m in payload.get("members", []) if isinstance(m, dict) and m.get("unverified")]
            elif kind == "mux" and isinstance(payload, dict):
                reasons.append("MUX(선택자에 따른 케이스 구조): ODX MUX 출력 미구현")
        return reasons

    def check_variable(self, fields):
        """가변 길이 필드(PDU 끝까지)는 요청·응답의 마지막 필드여야 하고 반복 항목 안에 둘 수 없다(VAL-005와 같은 규칙)."""
        def variable(item):
            kind, payload = item
            return (kind == "data" and isinstance(payload, dict) and payload.get("type_external_id") in self.variable_types) \
                or (kind == "did" and payload in self.variable_dids) or kind == "mux"
        if fields.get("profile") == "raw":
            layouts = [fields["request_layout"], fields["positive_layout"]]
        else:
            req, pos = [("data", m) for m in fields["request_members"]], [("data", m) for m in fields["positive_members"]]
            if fields.get("did_ref"): (pos if fields["sid"] == 0x22 else req).append(("did", fields["did_ref"]))
            layouts = [req, pos]
        for layout in layouts:
            for i, item in enumerate(layout):
                if item[0] == "mux":
                    # 선택자는 같은 전문의 앞쪽 데이터 객체여야 한다(가져오기에서 선택자 데이터 객체를 연결한다).
                    earlier = {p.get("external_id") for k, p in layout[:i] if k == "data" and isinstance(p, dict)}
                    if item[1].get("selector") not in earlier: raise DomainError("MUX 선택자가 같은 전문의 앞쪽 데이터 객체가 아님")
                if item[0] == "iter":
                    if any(variable(x) for x in item[1]["items"]): raise DomainError("반복 항목 안의 가변 길이 필드")
                elif variable(item) and i != len(layout) - 1:
                    raise DomainError("가변 길이 필드가 전문의 마지막이 아님")

    def raw(self, parts, periodic=False):
        """고정 프로파일 밖 서비스의 요청·긍정 응답 배치를 그대로 쓴다. 부정 응답은 7F SID NRC 형식이어야 한다.
        21차: 템플릿 부정 응답의 SID가 요청 SID와 다른 문서(Vector 파일 자체의 불일치)와 주기 응답(PROTOCOLSERVICE periodicresp=1,
        긍정 응답에 SID 없이 주기 식별자부터 옴)도 가져오고 미검증으로 둔다."""
        req, pos, neg = parts["REQ"], parts["POS"], parts["NEG"]
        if not req or req[0][0] != "literal" or len(req[0][1]) != 1: raise DomainError("SID가 없는 요청")
        sid = req[0][1][0]
        unverified, extra = [], []
        neg_bytes = b"".join(x[1] for x in neg) if not [x for x in neg if x[0] != "literal"] else None
        if neg_bytes is None or len(neg_bytes) != 2 or neg_bytes[0] != 0x7F:
            raise DomainError("부정 응답 배치가 7F SID NRC 형식이 아님")
        if neg_bytes[1] != sid:
            extra.append({"key": "cdd.negative_response_sid", "value_type": "text", "value_text": f"0x{neg_bytes[1]:02X}"})
            unverified.append(f"템플릿 부정 응답 SID 0x{neg_bytes[1]:02X}가 요청 SID 0x{sid:02X}와 다름(미리보기·출력은 요청 SID 사용)")
        if periodic:
            extra.append({"key": "cdd.periodic_response", "value_type": "text", "value_text": "1"})
            unverified.append("주기 응답(SID 없는 긍정 응답): ODX 대응 미확인")
            if not pos: raise DomainError("주기 응답 배치 없음")
        elif not pos or pos[0] != ("literal", bytes([(sid + 0x40) & 0xFF])): raise DomainError("긍정 응답 SID 불일치")
        dids = [x[1] for x in req + pos if x[0] == "did"] + [y[1] for x in req + pos if x[0] == "iter" for y in x[1]["items"] if y[0] == "did"]
        missing = [d for d in dids if self.did_numbers.get(d) is None]
        if missing: raise DomainError("가져오지 못한 DID를 참조")
        return {"sid": sid, "subfunction": None, "routine_identifier": None, "did_ref": None, "did_refs": dids, "profile": "raw",
                "request_members": [], "positive_members": [], "request_layout": req, "positive_layout": pos,
                "unverified": unverified, "extra_attributes": extra}

    def attributes(self, node):
        """사용자 정의 속성(ENUM/UNS/SGN/STR/CSTR attrref) → object_attributes 행. 값은 원문 그대로 보존한다."""
        return user_attributes(node, self.ids)

    def owner_attributes(self, node):
        """XML 속성 중 저작 메타데이터(req, xauth)를 보존한다."""
        return [{"key": f"cdd.@{k}", "value_type": "text", "value_text": node.get(k)} for k in ("req", "xauth") if node.get(k) is not None]

    def service(self, inst, svc):
        extra = {c.tag for c in svc} - set(META) - {"SHORTCUTNAME", "SHORTCUTQUAL", "SEMANTIC", *self.ATTRIBUTE_TAGS}
        if extra: raise DomainError("서비스 확장 항목 미지원: " + ", ".join(sorted(extra)))
        # respOnX=0은 해당 주소 방식에서 응답하지 않는다는 뜻. 사용하지 않는 주소 방식의 값은 의미가 없다(예: WDBI func=0, respOnFunc=0).
        # 사용하는 주소 방식의 respOnX=0(21차): 응답하지 않는 서비스(예: Tester Present Send No Response). 값을 속성으로 남기고,
        # ODX 대응(응답 없음 표현)을 확인하지 못해 미검증으로 둔다.
        response_attributes, service_unverified = [], []
        for mode, answer in (("phys", "respOnPhys"), ("func", "respOnFunc")):
            if svc.get(mode) == "1" and svc.get(answer, "1") != "1":
                response_attributes.append({"key": f"cdd.{answer}", "value_type": "text", "value_text": svc.get(answer)})
                service_unverified.append(f"{answer}=0(응답 없음): ODX 대응 미확인")
        parts, nrcs, new_types = self.resolve(inst, svc)
        protocol = self.ref(self.ref(svc, "tmplref"), "tmplref")
        periodic = protocol.get("periodicresp") == "1"
        try:
            if periodic: raise DomainError("주기 응답 서비스")
            fields = {**self.profile(parts), "profile": "uds_fixed"}
            fields["did_refs"] = [fields["did_ref"]] if fields["did_ref"] else []
        except DomainError as mismatch:
            # 모든 구성요소를 해석했지만 고정 프로파일과 다르면 원문 배치(raw)로 가져온다. 바이트 단위로 손실이 없고, 편집은 막는다.
            fields = {**self.raw(parts, periodic), "profile_mismatch": str(mismatch)}
        self.check_variable(fields)
        unverified = service_unverified + list(fields.pop("unverified", [])) + self.unverified_members(fields)
        physical, functional = svc.get("phys") == "1", svc.get("func") == "1"
        # act="0": 인스턴스에서 비활성화한 서비스. 도움말 80_ODXExport "Deactivated Services ... are not exported to ODX".
        enabled = svc.get("act", "1") != "0"
        if not (physical or functional): raise DomainError("물리·기능 주소가 모두 꺼진 서비스")
        matrix, transitions = self.conditions(svc)
        base = self.common(svc)
        name = preferred(tuv(svc, "SHORTCUTNAME")) or base["name"]
        texts = dict(base["texts"])
        if svc.find("SHORTCUTNAME") is not None: texts["name"] = tuv(svc, "SHORTCUTNAME")
        shortcut = svc.findtext("SHORTCUTQUAL")
        # 서비스 Semantic 속성: 도움말 80_ODXExport "SEMANTIC — each CANdela-Service has the property Semantic ... specify the
        # SEMANTIC of the ODX DIAG-SERVICE directly". ODX 출력 때 클래스 유형 대응보다 우선한다(interchange.build_odx).
        semantic = (svc.findtext("SEMANTIC") or "").strip()
        semantic_attributes = [{"key": "cdd.semantic", "value_type": "text", "value_text": semantic}] if semantic else []
        shortcut_attributes = ([{"key": "cdd.shortcut_qualifier", "value_type": "text", "value_text": shortcut},
                                {"key": "cdd.service_qualifier", "value_type": "text", "value_text": base["qualifier"]}]
                               if shortcut else [])
        for typ in new_types:
            self.extra_types.setdefault(typ["external_id"], typ)
            self.type_ids.add(typ["external_id"])
        return {**base, "qualifier": shortcut or base["qualifier"], "name": name, "texts": texts,
                **fields, "physical": physical, "functional": functional, "enabled": enabled, "nrcs": nrcs,
                "conditions": matrix, "transitions": transitions, "unverified": unverified,
                "attributes": self.attributes(svc) + self.owner_attributes(svc) + shortcut_attributes + semantic_attributes + response_attributes + fields.pop("extra_attributes", [])}

    def assign_state_codes(self, instances):
        """상태 코드: 세션 상태는 그 상태로 전이시키는 0x10 서비스의 subfunction(UDS 세션 번호)을 쓴다.
        근거가 없는 상태(보안 등)는 그룹 안 순서 번호를 쓰며, 이는 자체 식별용 값이다(state_code_basis 속성으로 표시)."""
        for g_index, g in enumerate(self.groups):
            for st in g["states"]: st["code"], st["code_basis"] = None, None
        for inst in instances:
            for svc in inst["services"]:
                if svc["sid"] != 0x10: continue
                for g_index, _, to in svc["transitions"]:
                    if self.groups[g_index]["kind"] != "session": continue
                    st = next(x for x in self.groups[g_index]["states"] if x["external_oid"] == to)
                    if st["code"] not in (None, svc["subfunction"]): raise DomainError("세션 상태에 서로 다른 세션 번호가 대응됨")
                    st["code"], st["code_basis"] = svc["subfunction"], "diagnostic_session_control"
        for g in self.groups:
            used = {st["code"] for st in g["states"] if st["code"] is not None}
            ordinal = 0
            for st in g["states"]:
                if st["code"] is None:
                    while ordinal in used: ordinal += 1
                    st["code"], st["code_basis"] = ordinal, "ordinal"
                    used.add(ordinal)

    # ------------------------------------------------------------------ 인스턴스·DTC
    def instances(self, variants_xml):
        """변형별 진단 클래스·인스턴스 배치를 문서 순서대로 해석한다(자체 소유 DIAGINST + 공유 DIAGINSTREF).

        클래스 식별은 DCLTMPL(tmplref) 기준이다. 변형마다 있는 DIAGCLASS는 같은 템플릿의 변형 내 소속 표시다
        (예제 ABS_ESP: 네 변형의 DIAGCLASS가 같은 DCLTMPL을 가리키고 이름·QUAL이 같다).
        DCLTMPL single="1"은 싱글턴 클래스이며, 그 인스턴스는 DIAGCLASS 없이 변형 바로 아래 온다(Fault Memory, Tester Present).
        결과: 인스턴스 목록과 DTC 원천. 클래스는 self.classes, 변형별 배치는 self.layout에 둔다."""
        result, dtc_sources = {}, []
        self.classes, self.layout, self.owners = {}, {}, {}
        for var in variants_xml:
            vid = var.get("id")
            slots, seen = [], set()

            def resolve(node):
                if node.tag == "DIAGINST":
                    self.owners[node.get("id")] = vid  # 원본 위치. DIAGINSTREF는 참조다.
                    return node
                target = self.ids.get(node.get("idref"))
                if target is None or target.tag != "DIAGINST":
                    self.unsupported(node, "해결되지 않은 진단 인스턴스 참조", vid)
                    return None
                return target

            def template(node, ref):
                tmpl = self.ids.get(ref)
                if tmpl is None or tmpl.tag != "DCLTMPL":
                    self.unsupported(node, "진단 클래스 템플릿 참조를 해결할 수 없음", vid)
                    return None
                if ref in seen:
                    self.unsupported(node, "같은 변형에 같은 진단 클래스 템플릿이 두 번 나옴", vid)
                    return None
                seen.add(ref)
                if ref not in self.classes:
                    self.classes[ref] = {**self.common(tmpl), "singleton": tmpl.get("single") == "1", "template_class": tmpl.get("cls")}
                return ref

            for child in var:
                if child.tag == "DIAGCLASS":
                    extra = {c.tag for c in child} - set(META) - {"DIAGINST", "DIAGINSTREF"}
                    if extra: self.unsupported(child, "진단 클래스 확장 항목 미지원: " + ", ".join(sorted(extra)), vid)
                    key = template(child, child.get("tmplref"))
                    if key is None: continue
                    members = []
                    for node in child:
                        if node.tag not in ("DIAGINST", "DIAGINSTREF"): continue
                        target = resolve(node)
                        if target is None: continue
                        if target.get("tmplref") != key:
                            self.unsupported(node, "진단 클래스와 다른 템플릿의 진단 인스턴스", vid)
                            continue
                        members.append(target)
                    slots.append((key, members))
                elif child.tag in ("DIAGINST", "DIAGINSTREF"):
                    target = resolve(child)
                    if target is None: continue
                    tmpl = self.ids.get(target.get("tmplref"))
                    if tmpl is None or tmpl.tag != "DCLTMPL" or tmpl.get("single") != "1":
                        self.unsupported(child, "변형 바로 아래 진단 인스턴스의 템플릿이 싱글턴 클래스가 아님", vid)
                        continue
                    key = template(child, target.get("tmplref"))
                    if key is not None: slots.append((key, [target]))
            layout = []
            for key, members in slots:
                ids = []
                for inst in members:
                    self.cancel()
                    iid = inst.get("id")
                    if iid in result:
                        if vid not in result[iid]["variants"]: result[iid]["variants"].append(vid)
                    else:
                        result[iid] = self.instance(key, inst, vid, dtc_sources)
                    ids.append(iid)
                layout.append({"class": key, "instances": ids})
            self.layout[vid] = layout
        for iid, record in result.items(): record["owner"] = self.owners.get(iid)
        return list(result.values()), dtc_sources

    # ------------------------------------------------------------------ 템플릿 목록(읽기 전용)
    def nrcs(self):
        """NEGRESCODES → 코드·이름. 서비스 NRC 코드의 이름 표시용."""
        result, seen = [], set()
        for n in self.doc.findall("NEGRESCODES/NEGRESCODE"):
            try:
                code = integer(n.get("v"), 1, 255)
                if code in seen: raise DomainError("중복 NRC 코드")
                seen.add(code)
                result.append({**self.common(n), "code": code})
            except (DomainError, ValueError, TypeError) as e:
                self.unsupported(n, f"NRC 미지원: {e}")
        return result

    @staticmethod
    def component_text(part):
        """프로토콜 서비스 요청·응답 구성요소 요약: 상수는 16진 바이트, 고정 값 ‹Qual›, 데이터 자리 {Qual}, 그 밖은 [태그:Qual]."""
        if part is None: return ""
        items = []
        for c in part:
            if c.tag in META: continue
            q = c.findtext("QUAL") or c.tag
            if c.tag == "CONSTCOMP" and c.get("v") is not None and c.get("bl") and int(c.get("bl")) % 4 == 0:
                items.append(f"{int(c.get('v')):0{int(c.get('bl')) // 4}X}")
            elif c.tag == "STATICCOMP": items.append(f"‹{q}›")
            elif c.tag == "SIMPLEPROXYCOMP": items.append("{" + q + "}")
            else: items.append(f"[{c.tag.replace('COMP', '')}:{q}]")
        return " ".join(items)

    def protocol_services(self):
        """PROTOCOLSERVICES → 읽기 전용 요약. 사용 수: DCLSRVTMPL tmplref(템플릿), SERVICE → DCLSRVTMPL(문서의 서비스)."""
        templates = {}
        for t in self.doc.iter("DCLSRVTMPL"): templates.setdefault(t.get("tmplref"), []).append(t.get("id"))
        services = {}
        for svc in self.doc.iter("SERVICE"): services[svc.get("tmplref")] = services.get(svc.get("tmplref"), 0) + 1
        result = []
        for ps in self.doc.findall("PROTOCOLSERVICES/PROTOCOLSERVICE"):
            used = templates.get(ps.get("id"), [])
            result.append({**self.common(ps), "request_text": self.component_text(ps.find("REQ")), "positive_text": self.component_text(ps.find("POS")),
                           "negative_text": self.component_text(ps.find("NEG")), "template_count": len(used), "service_count": sum(services.get(t, 0) for t in used)})
        return result

    def jobs(self, variants_xml):
        """JOBCNR(원본)·JOBCNRREF(참조) → Job 컨테이너와 변형별 순서. 내용은 해석하지 않고 컨테이너 소유의 원문으로 보존한다."""
        found, layout = {}, {}
        for var in variants_xml:
            vid = var.get("id")
            order = []
            for child in var:
                if child.tag == "JOBCNR": target = child
                elif child.tag == "JOBCNRREF":
                    target = self.ids.get(child.get("idref"))
                    if target is None or target.tag != "JOBCNR":
                        self.unsupported(child, "해결되지 않은 Job 컨테이너 참조", vid)
                        continue
                else: continue
                key = target.get("id")
                if key not in found:
                    found[key] = {**self.common(target), "owner": None}
                    # 29차: Job이 없는 빈 컨테이너(이름·Qualifier·설명만)는 ODX에 나갈 것이 없다. 근거: 도움말 90_ODXExport_Jobs(Job마다
                    # SINGLE-ECU-JOB), Door CDD의 빈 Flash_Jobs·Generic_Jobs ↔ UDS-ExampleEcu-5.0.2.pdx에 SINGLE-ECU-JOB 없음.
                    if all(x.tag in ("NAME", "QUAL", "DESC") for x in target):
                        self.unsupported(target, "출력 영향 없음: Job이 없는 빈 컨테이너(ODX SINGLE-ECU-JOB 없음)", key, semantic=False)
                    else:
                        self.unsupported(target, "Job 컨테이너 내용 미지원(원문 보존)", key)
                if child.tag == "JOBCNR": found[key]["owner"] = vid
                if key not in order: order.append(key)
            layout[vid] = order
        return list(found.values()), layout

    def instance(self, class_key, inst, vid, dtc_sources):
        record = {**self.common(inst), "class": class_key, "variants": [vid], "services": [], "attributes": self.attributes(inst) + self.owner_attributes(inst)}
        owner = inst.get("id")
        for child in inst:
            # MUXCOMPCONT(21차)는 SIMPLECOMPCONT처럼 서비스 해석에서 쓰는 슬롯이다. 서비스를 가져오지 못하면 그 SERVICE가 원문 보존된다.
            if child.tag in META or child.tag in ("SERVICE", "STATICVALUE", "SIMPLECOMPCONT", "MUXCOMPCONT", *self.ATTRIBUTE_TAGS): continue
            if child.tag == "FAULTMEMORY":
                try:
                    record["fault_memory"] = self.fault_memory(child)
                    continue
                except (DomainError, ValueError, TypeError) as e:
                    self.unsupported(child, f"고장 메모리 레코드 정의 미지원: {e}", owner)
                    continue
            if child.tag == "EVENTDATA":
                # 29차: Event 기준 데이터(표시등·운행 주기·디바운스·조건). 도움말 1_ODXExport_Introduction [ODXE-D115]
                # "ODX export does not include Events" — ODX에 나가지 않으므로 원문만 보존한다.
                self.unsupported(child, "출력 영향 없음: Event 데이터(ODX 출력 대상 아님, 도움말 ODXE-D115)", owner, semantic=False)
                continue
            self.unsupported(child, f"진단 인스턴스 확장 의미 미지원: {child.tag}", owner)
        self.current_fault_memory = record.get("fault_memory")
        for slot in inst.findall("SIMPLECOMPCONT"):
            for rdo in slot.findall("RECORDDATAOBJ"):
                if rdo.get("rtSpec") == "faultMemory": dtc_sources.append((inst.get("id"), rdo))
        for svc in inst.findall("SERVICE"):
            try:
                record["services"].append(self.service(inst, svc))
            except (DomainError, ValueError, TypeError) as e:
                self.unsupported(svc, f"서비스 미지원: {e}", owner)
        return record

    def fault_memory(self, node):
        """DIAGINST/FAULTMEMORY → 스냅샷 레코드 번호·DID 목록(기본, DTC 레코드별), 확장 데이터 레코드 번호·구조.
        0x19 04(sr/sd)·0x19 06(edr/ed) 전문의 레코드 자리가 이 정의를 가리킨다. DID 목록의 DID는 가져온 DID여야 한다."""
        result = {"snapshot_records": [], "default_dids": [], "dtc_dids": [], "extended": []}
        known = {"SNAPSHOT", "EXTENDEDDATA"}
        extra = {c.tag for c in node} - known
        if extra: raise DomainError("고장 메모리 확장 항목: " + ", ".join(sorted(extra)))

        def did_list(parent):
            refs = []
            for c in parent:
                if c.tag in META: continue
                if c.tag != "SNAPSHOTDATADIDREF": raise DomainError(f"스냅샷 데이터 항목 미지원: {c.tag}")
                if self.did_numbers.get(c.get("didRef")) is None: raise DomainError("가져오지 못한 DID를 스냅샷에 사용")
                refs.append(c.get("didRef"))
            return refs

        def number(n):
            return integer(n.get("rn"), 0, 255)
        snap = node.find("SNAPSHOT")
        if snap is not None:
            for c in snap:
                if c.tag == "DEFAULTSNAPSHOTDATA": result["default_dids"] = did_list(c)
                elif c.tag == "SNAPSHOTRECORDS":
                    for r in c:
                        if r.tag != "SNAPSHOTRECORD" or any(x.tag not in META for x in r): raise DomainError("스냅샷 레코드 형식 미지원")
                        result["snapshot_records"].append({**self.common(r), "number": number(r)})
                elif c.tag == "SPECIFICSNAPSHOTDATA":
                    if not c.get("recordRef"): raise DomainError("DTC별 스냅샷 데이터에 DTC 레코드 참조 없음")
                    result["dtc_dids"].append((c.get("recordRef"), did_list(c)))
                elif c.tag not in META: raise DomainError(f"스냅샷 항목 미지원: {c.tag}")
        ext = node.find("EXTENDEDDATA")
        if ext is not None:
            for c in ext:
                if c.tag == "EXTENDEDDATARECORDS":
                    for r in c:
                        if r.tag != "EXTENDEDDATARECORD": raise DomainError("확장 데이터 레코드 형식 미지원")
                        extra = {x.tag for x in r} - set(META) - {"STRUCTURE"}
                        if extra: raise DomainError("확장 데이터 레코드 확장 항목: " + ", ".join(sorted(extra)))
                        structure = r.find("STRUCTURE")
                        members = self.data_members([x for x in structure if x.tag not in META]) if structure is not None else []
                        result["extended"].append({**self.common(r), "number": number(r), "members": members})
                elif c.tag not in META: raise DomainError(f"확장 데이터 항목 미지원: {c.tag}")
        for key in ("snapshot_records", "extended"):
            numbers = [x["number"] for x in result[key]]
            if len(numbers) != len(set(numbers)): raise DomainError("레코드 번호 중복")
        return result

    def dtcs(self, dtc_sources, instance_variants):
        """faultMemory RECORDDATAOBJ의 RECORD → DTC. 근거: 예제 ODXEnvelope 파일명 0x402011 = RECORD v 4202513."""
        found, per_variant = {}, {}
        for inst_id, rdo in dtc_sources:
            variants = instance_variants[inst_id]
            for rdt in rdo.findall("RECORDDT"):
                cv = rdt.find("CVALUETYPE")
                if cv is None or cv.get("bl") != "24" or cv.get("enc") != "uns":
                    self.unsupported(rdt, "24비트 부호 없는 DTC 코드가 아닌 레코드 타입", inst_id)
                    continue
                templates = {t.get("id"): t for t in self.doc.iter("TRRECORDITEMTMPL", "ENUMRECORDITEMTMPL", "UNSRECORDITEMTMPL")}
                for rec in rdt.findall("RECORD"):
                    self.cancel()
                    try:
                        code = integer(rec.get("v"), 0, 0xFFFFFF)
                        attrs = []
                        for item in rec:
                            if item.tag == "TEXT": continue
                            tmpl = templates.get(item.get("idref"))
                            if tmpl is None: raise DomainError(f"레코드 항목 미지원: {item.tag}")
                            key = "cdd." + (tmpl.findtext("QUAL") or item.get("idref"))
                            if item.tag == "TRRECORDITEM":
                                texts = tuv(item, "TEXT")
                                for locale, value in texts.items():
                                    attrs.append({"key": key if locale == "en-US" or len(texts) == 1 else f"{key}@{locale}", "value_type": "text", "value_text": value})
                            elif item.tag == "ENUMRECORDITEM":
                                label = next((preferred(tuv(t, ".")) for t in tmpl.findall("ETAG") if t.get("v") == item.get("v")), None)
                                if label is None: raise DomainError("열거 레코드 값에 대응하는 항목 없음")
                                attrs.append({"key": key, "value_type": "text", "value_text": label})
                                attrs.append({"key": key + "#raw", "value_type": "integer", "value_text": str(integer(item.get("v")))})
                            elif item.tag == "UNSRECORDITEM":
                                # 부호 없는 정수 레코드 항목(예: UDS 템플릿의 DTC_PRIORITY_VALUE). 표시 형식 df=hex면 16진수로 보존.
                                value = integer(item.get("v"), 0)
                                attrs.append({"key": key, "value_type": "hex" if tmpl.get("df") == "hex" else "integer",
                                              "value_text": f"0x{value:X}" if tmpl.get("df") == "hex" else str(value)})
                            else:
                                raise DomainError(f"레코드 항목 미지원: {item.tag}")
                        if len({a["key"] for a in attrs}) != len(attrs): raise DomainError("같은 레코드 항목이 여러 번 나옴")
                        names = tuv(rec, "TEXT")
                        key = rec.get("id")
                        if key in found:
                            found[key]["variants"] = sorted(set(found[key]["variants"]) | set(variants))
                            continue
                        for v in variants:
                            if code in per_variant.setdefault(v, set()): raise DomainError("같은 변형에 중복 DTC 코드")
                        for v in variants: per_variant[v].add(code)
                        q = f"DTC_{code:06X}"
                        found[key] = {"external_id": key, "external_oid": rec.get("oid"), "node_path": self.tree.getpath(rec),
                                      "qualifier": q, "name": preferred(names) or q, "description": "", "texts": {"name": names} if names else {},
                                      "code": code, "attributes": attrs, "variants": list(variants)}
                    except (DomainError, ValueError) as e:
                        self.unsupported(rec, f"DTC 미지원: {e}", inst_id)
        return list(found.values())
