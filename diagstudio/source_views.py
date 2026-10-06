"""보존 원문(가져온 CDD)의 읽기 전용 보기(U5). Qt와 DB에 의존하지 않는다.

가져오기 때 해석하지 않고 원문으로만 보존한 CANdela 페이지를 원본 CDD(source_files.content)에서 다시 읽어 표로 만든다.
출력 의미(ODX)에는 쓰지 않고 표시만 한다. 해석하지 못한 값은 버리지 않고 원문 그대로 보인다.

해석 규칙(CANdelaStudio 13 설치 예제 ABS_ESP-Example.cdd와 Vector_UDS_13.0.cddt로 확인):
  ECU 인터페이스      DEFATTS/ECUATTS의 속성 정의 중 범주(ATTRCAT) QUAL이 COM.INTERFACES인 ENUMDEF. 값은 ECU의 ENUM(attrref)이
                      있으면 그 값, 없으면 정의의 기본값 v. ETAG 표시명 'supported'가 지원이다(예제: Diagnostic CAN만 지원).
  통신 파라미터        같은 정의 중 QUAL이 '<인터페이스 QUAL>.'로 시작하는 것(Diagnostic_CAN.P2Client 등). 범주는 ATTRCAT 이름,
                      기본값은 정의의 v(df=hex면 16진), 덮어쓴 값은 ECU 요소의 UNS/ENUM/... attrref 값.
  식별 특징           IDENTIFICATIONFEATURESET/IDENTIFICATIONFEATURE(dtref, dataObjectPath, diagCommPath).
  식별 패턴           VAR/IDENTIFICATIONSET/IDENTIFICATION/IDENTIFICATIONDETAIL(idref=특징, v='(48,46,48,46,49)' = 바이트 목록).
                      특징의 타입이 ASCII(CVALUETYPE enc=asc)이고 모두 인쇄 가능한 문자면 문자열(예: 0.0.1)로 보인다.
  진단 클래스 템플릿   DCLTMPLS/DCLTMPL(cls, single)과 DCLSRVTMPL(conv=req/optyes/optno, tmplref=PROTOCOLSERVICE, mayBeExec/trans).
  지원 진단 클래스     VAR의 DIAGCLASS·DIAGINST·DIAGINSTREF·JOBCNR·JOBCNRREF가 가리키는 DCLTMPL.
  기본 상태 전이       DCLSRVTMPL의 mayBeExec/trans(새 서비스의 기본값). 상태 번호는 STATEGROUPS의 문서 순서(1부터)다.
  개정 이력           AUTHORS/AUTHOR, HISTITEMS/HISTITEM(LABEL, MOD, REASON, dt, tool, stid, authorref).
  미지원 서비스 NRC    UNSUPPSRVNEG 안의 NEGRESCODEPROXY(idref → NEGRESCODES/NEGRESCODE).
  DTC 상태 마스크      DTCSTATUSMASK/DTCSTATUSBITGROUP(문서 순서 = 비트 0..7, conv=optyes 사용, optno 미사용).
  프로토콜 서비스 패턴 REQ/POS/NEG 구성요소: CONSTCOMP = 16진 값, STATICCOMP = LL, SIMPLEPROXYCOMP dest=resCode = rc,
                      그 밖의 데이터 자리 = zz, yy, xx, ww … (예제 (0x10): 10 LL / 50 LL zz / 7F 10 rc).
"""
from lxml import etree as ET

XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
META = ("NAME", "QUAL", "DESC")
ATTRIBUTE_TAGS = ("ENUM", "UNS", "SGN", "STR", "CSTR")
DEFINITION_TAGS = ("ENUMDEF", "UNSDEF", "SGNDEF", "STRDEF", "CSTRDEF")
PLACEHOLDERS = ("zz", "yy", "xx", "ww", "vv", "uu", "tt", "ss")
CLASS_KINDS = {"ses": "세션", "idn": "식별", "sec": "보안 접근", "ftm": "고장 메모리", "ctl": "제어", "act": "동적 데이터",
               "std": "저장 데이터", "vcd": "변형 코딩", "rtn": "루틴", "dwn": "다운로드", "mem": "메모리", "com": "통신 제어",
               "fun": "기능", "tpr": "Tester Present", "job": "Job", "flashjob": "Flash Job"}
CONV_TEXT = {"req": "필수", "optyes": "선택(기본 사용)", "optno": "선택(기본 미사용)"}


def _parser():
    return ET.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False, remove_comments=True)


class SourceDocument:
    """원본 CDD 한 개. 요소 id 색인과 다국어 텍스트 선택을 제공한다."""

    def __init__(self, content, locale="en-US", basename="", format_version=""):
        if isinstance(content, str): content = content.encode("utf-8")
        self.root = ET.fromstring(content, _parser())
        self.doc = self.root.find("ECUDOC")
        if self.doc is None: raise ValueError("ECUDOC 없음")
        self.ids = {e.get("id"): e for e in self.root.iter() if isinstance(e.tag, str) and e.get("id")}
        self.locale = locale
        self.basename = basename
        self.format_version = format_version

    # ------------------------------------------------------------------ 공통
    def text(self, node, tag="NAME"):
        """TUV 다국어 텍스트: 현재 언어 → en-US → 첫 비어 있지 않은 값."""
        if node is None: return ""
        values = {}
        for t in node.findall(f"{tag}/TUV"):
            text = "".join(t.itertext())
            if text.strip(): values.setdefault(t.get(XML_LANG, "en-US"), text)
        if not values:
            plain = node.findtext(tag)
            return plain.strip() if plain and plain.strip() else ""
        return values.get(self.locale) or values.get("en-US") or next(iter(values.values()))

    def name(self, node):
        return self.text(node, "NAME") or (node.findtext("QUAL") if node is not None else "") or ""

    @staticmethod
    def qual(node):
        return (node.findtext("QUAL") or "") if node is not None else ""

    def ref(self, node, attr):
        return self.ids.get(node.get(attr)) if node is not None and node.get(attr) else None

    @staticmethod
    def int_list(text):
        """'(48,46,49)' → [48, 46, 49]. 형식이 다르면 None."""
        body = (text or "").strip()
        if not (body.startswith("(") and body.endswith(")")): return None
        try:
            return [int(x) for x in body[1:-1].split(",") if x.strip()]
        except ValueError:
            return None

    # ------------------------------------------------------------------ 값 표시
    def _definition_value(self, definition, element=None):
        """속성 정의(ENUMDEF 등)의 값 표시. element가 있으면 그 값(덮어쓴 값), 없으면 정의 기본값."""
        source = element if element is not None else definition
        tag = definition.tag
        if tag == "ENUMDEF":
            raw = source.get("v")
            label = next((self.text(t, ".") for t in definition.findall("ETAG") if t.get("v") == raw), "")
            return (f"{label} ({raw})" if label else raw or "—"), raw, label
        if tag in ("UNSDEF", "SGNDEF"):
            raw = source.get("v")
            if raw is None: return "—", None, ""
            try:
                value = int(raw)
                if definition.get("df") == "hex" and value >= 0: return f"0x{value:X}", raw, ""
                if definition.get("df") == "bin" and value >= 0: return f"0b{value:b}", raw, ""
            except ValueError:
                pass
            return raw, raw, ""
        # STRDEF / CSTRDEF: STRING/TUV 또는 COMMONSTRING
        common = source.findtext("COMMONSTRING")
        text = common if common is not None else self.text(source, "STRING")
        if element is not None and not text: text = element.get("v", "")
        return (text or "—"), text, ""

    # ------------------------------------------------------------------ ECU 인터페이스·통신 파라미터
    def _ecu(self):
        return self.doc.find("ECU")

    def _categories(self):
        cats = {}
        for c in self.doc.findall("ATTRCATS/ATTRCAT"):
            cats[c.get("id")] = (self.name(c), self.qual(c))
        return cats

    def _ecu_definitions(self):
        return [d for d in self.doc.findall("DEFATTS/ECUATTS/*") if d.tag in DEFINITION_TAGS]

    def _ecu_overrides(self):
        ecu = self._ecu()
        return {a.get("attrref"): a for a in (ecu if ecu is not None else []) if a.tag in ATTRIBUTE_TAGS and a.get("attrref")}

    def interfaces(self):
        """지원 인터페이스 체크 목록: [{qualifier, name, supported, value_text, overridden, description}]."""
        cats = self._categories()
        overrides = self._ecu_overrides()
        rows = []
        for d in self._ecu_definitions():
            if cats.get(d.get("attrcatref"), ("", ""))[1] != "COM.INTERFACES" or d.tag != "ENUMDEF": continue
            element = overrides.get(d.get("id"))
            text, raw, label = self._definition_value(d, element)
            supported = label.strip().casefold() == "supported" if label else raw not in (None, "0")
            rows.append({"qualifier": self.qual(d), "name": self.name(d), "supported": supported, "value_text": text,
                         "overridden": element is not None, "description": self.text(d, "DESC")})
        return rows

    def interface_parameters(self, interface_qualifier):
        """인터페이스의 통신 파라미터: [{name, qualifier, category, default_text, value_text, description}]."""
        cats = self._categories()
        overrides = self._ecu_overrides()
        prefix = interface_qualifier + "."
        rows = []
        for d in self._ecu_definitions():
            q = self.qual(d)
            if not q.startswith(prefix): continue
            element = overrides.get(d.get("id"))
            default_text = self._definition_value(d)[0]
            value_text = self._definition_value(d, element)[0] if element is not None else ""
            rows.append({"name": self.name(d), "qualifier": q[len(prefix):], "category": cats.get(d.get("attrcatref"), ("", ""))[0],
                         "default_text": default_text, "value_text": value_text, "effective_text": value_text or default_text,
                         "usage": d.get("usage") or "", "description": self.text(d, "DESC")})
        return rows

    # ------------------------------------------------------------------ 식별 특징·패턴
    def _type_is_ascii(self, dtref):
        t = self.ids.get(dtref)
        cv = t.find("CVALUETYPE") if t is not None else None
        return cv is not None and cv.get("enc") == "asc"

    @staticmethod
    def _qpath_tail(path):
        body = (path or "").split(":", 1)[-1]
        return [p for p in body.split("/") if p]

    def identification_features(self):
        rows = []
        for n, f in enumerate(self.doc.findall("IDENTIFICATIONFEATURESET/IDENTIFICATIONFEATURE"), 1):
            data_path = self._qpath_tail(f.get("dataObjectPath"))
            comm_path = self._qpath_tail(f.get("diagCommPath"))
            instance = comm_path[-2] if len(comm_path) >= 2 else ""
            service = comm_path[-1].replace("[SERVICE]", "") if comm_path else ""
            rows.append({"id": f.get("id"), "ordinal": n, "name": data_path[-1] if data_path else f.get("id", ""),
                         "instance": instance, "service": service, "data_type": self.name(self.ids.get(f.get("dtref"))),
                         "path": f.get("dataObjectPath") or "", "service_path": f.get("diagCommPath") or ""})
        return rows

    def _pattern_value(self, feature_id, values):
        feature = self.ids.get(feature_id)
        if values is None: return "해석 불가", ""
        raw = " ".join(f"{v & 0xFF:02X}" for v in values)
        if feature is not None and self._type_is_ascii(feature.get("dtref")) and all(32 <= v < 127 for v in values):
            return "".join(chr(v) for v in values), raw
        return raw or "—", raw

    def identification_patterns(self, variant_element):
        """변형의 식별 패턴: [{pattern, feature, value_text, raw}]. 패턴 번호는 문서 순서(1부터)."""
        rows = []
        if variant_element is None: return rows
        features = {f["id"]: f["name"] for f in self.identification_features()}
        for n, ident in enumerate(variant_element.findall("IDENTIFICATIONSET/IDENTIFICATION"), 1):
            for detail in ident.findall("IDENTIFICATIONDETAIL"):
                text, raw = self._pattern_value(detail.get("idref"), self.int_list(detail.get("v")))
                rows.append({"pattern": n, "feature": features.get(detail.get("idref"), detail.get("idref") or ""),
                             "value_text": text, "raw": raw})
        return rows

    def pattern_summary(self, variant_element):
        """변형 목록의 식별 패턴 열: 패턴마다 값들을 '·'로 잇고, 패턴 사이는 ', '(예: 0.0.1, 0.0.2, 0.0.3)."""
        by_pattern = {}
        for row in self.identification_patterns(variant_element):
            by_pattern.setdefault(row["pattern"], []).append(row["value_text"])
        return ", ".join(" · ".join(values) for _, values in sorted(by_pattern.items()))

    # ------------------------------------------------------------------ 진단 클래스 템플릿
    def class_templates(self):
        result = []
        for t in self.doc.findall("DCLTMPLS/DCLTMPL"):
            services = []
            for s in t.findall("DCLSRVTMPL"):
                protocol = self.ids.get(s.get("tmplref"))
                services.append({"id": s.get("id"), "name": self.name(s), "qualifier": self.qual(s), "conv": s.get("conv") or "",
                                 "protocol": self.name(protocol) if protocol is not None else "", "protocol_id": s.get("tmplref"),
                                 "may_be_exec": s.get("mayBeExec"), "trans": s.get("trans")})
            result.append({"id": t.get("id"), "name": self.name(t), "qualifier": self.qual(t), "cls": t.get("cls") or "",
                           "single": t.get("single") == "1", "services": services, "description": self.text(t, "DESC")})
        return result

    def supported_classes(self, variant_element):
        """변형이 쓰는 클래스 템플릿에 체크한 목록(템플릿 전체, 문서 순서)."""
        used = set()
        if variant_element is not None:
            for child in variant_element:
                if child.tag in ("DIAGCLASS", "DIAGINST", "JOBCNR"):
                    if child.get("tmplref"): used.add(child.get("tmplref"))
                elif child.tag in ("DIAGINSTREF", "JOBCNRREF"):
                    target = self.ids.get(child.get("idref"))
                    if target is not None and target.get("tmplref"): used.add(target.get("tmplref"))
        rows = []
        for t in self.class_templates():
            rows.append({"id": t["id"], "name": t["name"], "qualifier": t["qualifier"], "cls_text": CLASS_KINDS.get(t["cls"], t["cls"]),
                         "single": t["single"], "checked": t["id"] in used, "services": len(t["services"]), "description": t["description"]})
        return rows

    def instance_template_services(self, instance_element):
        """인스턴스 클래스 템플릿의 서비스 전체와 사용 여부. 사용한 서비스는 SERVICE@tmplref로 찾는다."""
        if instance_element is None: return []
        template = self.ids.get(instance_element.get("tmplref"))
        if template is None: return []
        used = {}
        for svc in instance_element.findall("SERVICE"):
            used.setdefault(svc.get("tmplref"), svc)
        rows = []
        for s in template.findall("DCLSRVTMPL"):
            protocol = self.ids.get(s.get("tmplref"))
            svc = used.get(s.get("id"))
            rows.append({"template_id": s.get("id"), "name": self.name(s), "qualifier": self.qual(s), "conv": s.get("conv") or "",
                         "required": s.get("conv") == "req", "used": svc is not None,
                         "active": svc is not None and svc.get("act", "1") != "0",
                         "service_external_id": svc.get("id") if svc is not None else None,
                         "protocol": self.name(protocol) if protocol is not None else "",
                         "request_pattern": self.protocol_pattern(protocol, "REQ") if protocol is not None else "",
                         "positive_pattern": self.protocol_pattern(protocol, "POS") if protocol is not None else "",
                         "negative_pattern": self.protocol_pattern(protocol, "NEG") if protocol is not None else ""})
        return rows

    def service_roles(self):
        """문서의 SERVICE 요소 id → (역할 이름 = DCLSRVTMPL 이름, 프로토콜 서비스 이름, DCLSRVTMPL id, PROTOCOLSERVICE id)."""
        roles = {}
        for svc in self.doc.iter("SERVICE"):
            template = self.ids.get(svc.get("tmplref"))
            protocol = self.ids.get(template.get("tmplref")) if template is not None else None
            roles[svc.get("id")] = (self.name(template) if template is not None else "", self.name(protocol) if protocol is not None else "",
                                    svc.get("tmplref"), template.get("tmplref") if template is not None else None)
        return roles

    # ------------------------------------------------------------------ 프로토콜 서비스
    def protocol_pattern(self, protocol, part):
        node = protocol.find(part) if protocol is not None else None
        if node is None: return ""
        tokens, proxies = [], 0
        for c in node:
            if not isinstance(c.tag, str) or c.tag in META: continue
            if c.tag == "CONSTCOMP" and c.get("v") is not None:
                try:
                    bits = int(c.get("bl") or 8)
                    value = int(c.get("v"))
                    tokens.append(" ".join(f"{b:02X}" for b in value.to_bytes(max(1, (bits + 7) // 8), "big")))
                    continue
                except (ValueError, OverflowError):
                    tokens.append(c.get("v")); continue
            if c.tag == "STATICCOMP": tokens.append("LL"); continue
            if c.get("dest") == "resCode": tokens.append("rc"); continue
            tokens.append(PLACEHOLDERS[proxies] if proxies < len(PLACEHOLDERS) else f"p{proxies + 1}")
            proxies += 1
        return " ".join(tokens)

    def protocol_services(self):
        """PROTOCOLSERVICE id → {request, positive, negative 패턴, required(필수 템플릿 서비스가 있음)}."""
        required = {}
        for s in self.doc.iter("DCLSRVTMPL"):
            if s.get("conv") == "req": required[s.get("tmplref")] = True
        result = {}
        for ps in self.doc.findall("PROTOCOLSERVICES/PROTOCOLSERVICE"):
            result[ps.get("id")] = {"request": self.protocol_pattern(ps, "REQ"), "positive": self.protocol_pattern(ps, "POS"),
                                    "negative": self.protocol_pattern(ps, "NEG"), "required": bool(required.get(ps.get("id")))}
        return result

    # ------------------------------------------------------------------ Job
    def jobs(self, container_element):
        rows = []
        if container_element is None: return rows
        for n, job in enumerate([c for c in container_element if c.tag in ("JOB", "FLASHJOB")], 1):
            function = job.find("FUNCTION")
            file = self.ids.get(function.get("jobFileRef")) if function is not None else None
            file_text = ""
            if file is not None:
                file_text = " ".join((e.text or "").strip() for e in file.iter() if isinstance(e.tag, str) and (e.text or "").strip()) or self.name(file) or file.get("id", "")
            inputs = sum(1 for c in job.findall("SIMPLECOMPCONT") if c.get("usage") == "input")
            outputs = sum(1 for c in job.findall("SIMPLECOMPCONT") if c.get("usage") == "output")
            rows.append({"ordinal": n, "kind_text": "Flash Job" if job.tag == "FLASHJOB" else "Job", "name": self.text(job, "SHORTCUTNAME") or self.name(job),
                         "qualifier": job.findtext("SHORTCUTQUAL") or self.qual(job), "file": file_text,
                         "entry_point": function.get("entryPoint", "") if function is not None else "",
                         "inputs": inputs, "outputs": outputs, "description": self.text(job, "DESC")})
        return rows

    # ------------------------------------------------------------------ 상태 기본값
    def states(self):
        """전역 상태 번호(1부터, 문서 순서) → (그룹 이름, 상태 이름, 그룹 인덱스). 그룹 목록도 돌려준다."""
        numbered, groups = [], []
        for g_index, g in enumerate(self.doc.findall("STATEGROUPS/STATEGROUP")):
            names = [self.name(s) for s in g.findall("STATE")]
            groups.append({"name": self.name(g), "qualifier": self.qual(g), "spec": g.get("spec") or "", "states": names,
                           "negative_response": self._group_nrc(g), "description": self.text(g, "DESC")})
            for s in names: numbered.append((self.name(g), s, g_index))
        return numbered, groups

    def _group_nrc(self, group):
        proxy = group.find("NEGRESCODEPROXY")
        target = self.ids.get(proxy.get("idref")) if proxy is not None else None
        if target is None: return ""
        try: code = f"0x{int(target.get('v')):02X}"
        except (TypeError, ValueError): code = target.get("v") or ""
        return f"{code} {self.name(target)}".strip()

    def state_defaults(self):
        """템플릿 서비스의 기본 상태 조건·전이(새 서비스에 적용되는 값). 행 = 템플릿 서비스, 열 = 상태."""
        numbered, groups = self.states()
        columns = [("class", "클래스"), ("name", "서비스"), ("protocol", "프로토콜 서비스"), ("prefix", "요청")]
        columns += [(f"st:{i}", f"{g}: {s}") for i, (g, s, _) in enumerate(numbered, 1)]
        rows = []
        for t in self.class_templates():
            for s in t["services"]:
                protocol = self.ids.get(s["protocol_id"])
                row = {"id": f"template:{s['id']}", "kind": "state_default", "class": t["name"], "name": s["name"], "qualifier": s["qualifier"],
                       "protocol": s["protocol"], "prefix": self.protocol_pattern(protocol, "REQ") if protocol is not None else ""}
                row.update(self._default_cells(s["may_be_exec"], s["trans"], numbered))
                rows.append(row)
        return rows, columns, groups

    def _default_cells(self, may_be_exec, trans, numbered):
        """mayBeExec/trans → 상태별 셀. 목록에 없는 그룹은 제한 없음(—). cdd_model.Model.conditions와 같은 규칙이다."""
        cells = {f"st:{i}": "—" for i in range(1, len(numbered) + 1)}
        allowed = self.int_list(may_be_exec) if may_be_exec is not None else []
        pairs = self.int_list(trans) if trans is not None else []
        if allowed is None or pairs is None:
            return {key: "해석 불가" for key in cells}
        valid = lambda i: 1 <= i <= len(numbered)
        listed_groups = {numbered[i - 1][2] for i in allowed if valid(i)}
        for i, (_, _, g_index) in enumerate(numbered, 1):
            if g_index in listed_groups: cells[f"st:{i}"] = "✓" if i in allowed else "✗"
        for a, b in zip(pairs[0::2], pairs[1::2]):
            if not (valid(a) and valid(b)): continue
            if cells[f"st:{a}"] == "—": cells[f"st:{a}"] = "✓"
            cells[f"st:{a}"] = f"✓ → {numbered[b - 1][1]}"
        return cells

    # ------------------------------------------------------------------ 개정 이력
    def revision_history(self):
        authors = {}
        author_rows = []
        for a in self.doc.findall("AUTHORS/AUTHOR"):
            full = " ".join(x for x in (a.findtext("FIRSTNAME"), a.findtext("LASTNAME")) if x)
            short = a.findtext("SHORTNAME") or full
            authors[a.get("id")] = short
            author_rows.append({"short": short, "name": full, "company": a.findtext("COMPANY") or "", "department": a.findtext("DEPT") or "",
                                "responsible": "예" if a.get("responsible") == "1" else "아니요"})
        history = []
        for n, h in enumerate(self.doc.findall("HISTITEMS/HISTITEM"), 1):
            history.append({"ordinal": n, "version": h.findtext("LABEL") or "", "author": authors.get(h.get("authorref"), h.get("authorref") or ""),
                            "date": h.get("dt") or "", "status": h.get("stid") or "", "tool": h.get("tool") or "",
                            "modification": (h.findtext("MOD") or "").strip(), "reason": (h.findtext("REASON") or "").strip()})
        return {"authors": author_rows, "history": history}

    # ------------------------------------------------------------------ NRC·DTC 상태 마스크
    def unsupported_service_nrcs(self):
        rows = []
        for container in self.doc.findall("UNSUPPSRVNEG"):
            for proxy in container.iter("NEGRESCODEPROXY"):
                target = self.ids.get(proxy.get("idref"))
                if target is None: continue
                try: code = int(target.get("v"))
                except (TypeError, ValueError): continue
                rows.append({"code": code, "name": self.name(target), "qualifier": self.qual(target), "description": self.text(target, "DESC")})
        return rows

    def dtc_status_mask(self):
        rows = []
        for bit, g in enumerate(self.doc.findall("DTCSTATUSMASK/DTCSTATUSBITGROUP")):
            t = self.ids.get(g.get("dtref"))
            rows.append({"bit": bit, "name": self.name(t) if t is not None else g.get("dtref", ""), "qualifier": self.qual(t),
                         "used": g.get("conv") in ("optyes", "req"), "conv_text": CONV_TEXT.get(g.get("conv") or "", g.get("conv") or "")})
        return rows

    # ------------------------------------------------------------------ 데이터 타입
    def type_info(self, element_id):
        """데이터 타입의 원본 표시 정보: 템플릿 소유(temploid) 여부, 선형 변환식 원문(f, div, o)."""
        t = self.ids.get(element_id)
        if t is None: return {}
        info = {"template": t.get("temploid") is not None}
        comp = t.find("COMP") if t.tag in ("LINCOMP", "COMPTBL") else None
        if comp is not None:
            info["formula"] = {k: comp.get(k) for k in ("f", "div", "o", "s", "e") if comp.get(k) is not None}
        return info
