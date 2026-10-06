"""사용 사례·편집 명령. UI/어댑터는 이 서비스로만 프로젝트를 변경한다."""
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from urllib.parse import quote, unquote, urlsplit

from . import __version__
from .domain import QUALIFIER_MAX, make_qualifier, DomainError, check_type, convert, decimal_text, integer, key, qualifier, ratio_text, validate
from .persistence import Session, uid, now, digest
from . import migrations
from .references import reference_impact as build_reference_impact
from .comparison import saved_project, compare_snapshots, definition_signature, compare_tree, tree_counts
from .presentation import telegram_pattern, negative_pattern, length_text, protocol_label, ROLE_NAMES
from .source_views import SourceDocument
from .cdd_model import preferred as cdd_preferred, tuv as cdd_tuv


# [TREE-R400] 진단 인스턴스 아이콘 5종: 원본 변형(owner)과 참조 변형으로 구분한다.
SHARE_TEXT = {"base": "기본 변형에만 있음", "base_shared": "기본 변형 원본, 확장 변형에서 참조", "local": "이 확장 변형에만 있음",
              "ext_ref_base": "확장 변형 원본, 기본 변형에서 참조", "ext_shared": "확장 변형 원본, 다른 확장 변형에서만 참조"}
ROLE = {16: "session", 17: "reset", 34: "read", 46: "write", 49: "routine", 62: "tester_present"}


# 읽기 전용 목록(U5·U4): 보존 원문 또는 템플릿에서 유도한 표. 추가·편집·삭제는 없고 출력 의미에도 쓰지 않는다.
SOURCE_KINDS = {"interfaces", "comm_params", "ident_features", "ident_patterns", "supported_classes", "job", "state_defaults", "unsupported_nrc"}
# 트리 순서(문서 순서)를 기본 정렬로 쓰는 목록
ORDERED_KINDS = {"diagnostic_class", "diagnostic_instance", "dependency", *SOURCE_KINDS, "state_group"}
# [UI-R100-Sort]·결정 D5: 번호가 식별 속성인 목록은 들어갈 때 번호순으로 보인다.
NUMBER_SORT = {"did": "identifier", "dtc": "code", "did_overview": "identifier", "dtc_overview": "code", "nrc": "code", "state": "code", "service": "sid"}
# 목록 행에 가져온 객체 여부(imported)를 싣는 종류([QUAL-R020] 이름 따라 Qualifier 바꾸기에 쓴다).
EDIT_KINDS = {"did", "dtc", "service", "data_type", "state", "variant", "diagnostic_instance", "ecu"}


# 27차: 보존 항목의 구분(목록 열). 가져오기 사유 글(reason_code)로 정한다. 출력 차단 여부는 semantic이 기준이다.
def preserved_category(row):
    reason = row.get("reason_code") or ""
    if row.get("semantic"): return "출력 차단"
    if reason.startswith("해석함") or reason.startswith("템플릿·정의 원문 보존"): return "해석됨(원문 참조용)"
    if reason.startswith("출력 영향 없음"): return "출력 영향 없음"
    return "메타데이터·출처 정보"


def preserved_label(reason):
    """보존 항목 사유의 화면 글. 인스턴스 단위로 이미 해석한 템플릿 정의는 '해석됨'으로 보인다(원문은 그대로)."""
    return "해석됨: 템플릿·정의(인스턴스 단위로 해석, 원문 참조용)" if reason == "템플릿·정의 원문 보존(인스턴스 단위로 해석)" else reason


def filter_terms(text):
    """[LIST-R500]·결정 D2: 필터 입력 → 비교할 말 목록(NFC·casefold). 띄어 쓴 단어는 따로, 큰따옴표 안은 한 구절이다.
    닫지 않은 따옴표는 끝까지를 구절로 본다."""
    terms, text, i = [], str(text or ""), 0
    while i < len(text):
        if text[i].isspace(): i += 1; continue
        if text[i] == '"':
            end = text.find('"', i + 1)
            phrase = text[i + 1:] if end < 0 else text[i + 1:end]
            i = len(text) if end < 0 else end + 1
        else:
            end = i
            while end < len(text) and not text[end].isspace() and text[end] != '"': end += 1
            phrase, i = text[i:end], end
        phrase = key(phrase.strip())
        if phrase and phrase not in terms: terms.append(phrase)
    return terms
REWORK_KEY = "app.rework"


# 경로 입력 문법(M3-2)의 종류 선택자. [DID]·[DTC]처럼 조각 앞에 쓴다.
PATH_KINDS = {"DID": "did", "DTC": "dtc", "SERVICE": "service", "SRV": "service", "INSTANCE": "diagnostic_instance", "DIAGINST": "diagnostic_instance",
              "CLASS": "diagnostic_class", "DIAGCLASS": "diagnostic_class", "TYPE": "data_type", "DATATYPE": "data_type", "DT": "data_type",
              "VARIANT": "variant", "VAR": "variant", "STATE": "state", "STATEGROUP": "state_group", "NRC": "nrc",
              "PROTOCOLSERVICE": "protocol_service", "PS": "protocol_service", "JOB": "job_container", "ECU": "ecu"}
# Qualifier 경로를 만들 때 붙이는 선택자(변형 아래 DID·DTC·Job, 문서 수준 타입·NRC·프로토콜 서비스)
PATH_SELECTOR = {"did": "DID", "dtc": "DTC", "job_container": "JOB", "data_type": "TYPE", "nrc": "NRC", "protocol_service": "PS"}


def parse_object_path(text):
    """경로 입력(M3-2): 'npath:'(이름)·'qpath:'(Qualifier) 접두어, '/' 구분, 큰따옴표·작은따옴표로 감싼 조각('/' 포함 가능),
    '\\'로 다음 글자 이스케이프, 조각 앞 '[DID]' 같은 종류 선택자. 접두어가 없으면 이름 경로다(결정 D7 ①).
    돌려주는 값: (mode, [{"text": 조각, "kind": 선택자 종류 또는 None}, ...])."""
    s = str(text or "").strip()
    mode = "npath"
    if s.lower().startswith("npath:"): s = s[6:]
    elif s.lower().startswith("qpath:"): mode, s = "qpath", s[6:]
    segments, cur, kind, i = [], [], None, 0

    def flush():
        nonlocal cur, kind
        value = "".join(cur).strip()
        if value or kind: segments.append({"text": value, "kind": kind})
        cur, kind = [], None

    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            cur.append(s[i + 1]); i += 2; continue
        if ch in "\"'":
            end, part = i + 1, []
            while end < len(s) and s[end] != ch:
                if s[end] == "\\" and end + 1 < len(s): part.append(s[end + 1]); end += 2; continue
                part.append(s[end]); end += 1
            if end >= len(s):  # 닫는 따옴표가 없으면 글자 그대로다(이름 안의 ' 등).
                cur.append(ch); i += 1; continue
            cur.append("".join(part)); i = end + 1; continue
        if ch == "[" and not "".join(cur).strip() and kind is None:
            end = s.find("]", i)
            if end < 0: raise DomainError("종류 선택자의 ']'가 없습니다.")
            name = s[i + 1:end].strip().upper()
            if name not in PATH_KINDS: raise DomainError(f"알 수 없는 종류 선택자입니다: [{name}]")
            kind = PATH_KINDS[name]; cur = []; i = end + 1; continue
        if ch == "/":
            flush(); i += 1; continue
        cur.append(ch); i += 1
    flush()
    return mode, segments


class Application:
    def __init__(self):
        self.session = None
        self.locale = "ko-KR"
        self._source_cache = None

    @property
    def r(self):
        if not self.session: raise DomainError("프로젝트를 먼저 여세요.")
        return self.session.repo

    def new_project(self, name, technical_name="ECU", locale="ko-KR"):
        if not name.strip(): raise DomainError("프로젝트 이름을 입력하세요.")
        qualifier(technical_name)
        candidate = Session()
        candidate.initialize()
        previous = self.session
        self.session = candidate
        self.locale = locale
        try:
            r = self.r
            timestamp = now()
            r.insert("project_meta", singleton=1, project_uuid=uid(), schema_version=migrations.CURRENT_VERSION, revision=0, default_locale=locale, profile_id="UDS_FIXED", profile_version="1", saved_app_version=__version__, created_at=timestamp, updated_at=timestamp)
            r.insert("history_state", singleton=1, cursor_position=0, current_token=uid(), saved_token=None)
            with r.command("프로젝트 생성"):
                ecu = self._object("ecu", technical_name, name)
                r.insert("ecu", id=ecu, project_singleton=1, manufacturer="")
                base = self._object("variant", "Base", "기본 변형")
                r.insert("variants", id=base, ecu_id=ecu, is_base=1, ordinal=0, locked=0)
                for scope, bits in (("did", 16), ("dtc", 24), ("routine", 16)):
                    r.insert("identifier_scopes", id=scope, protocol="UDS", kind=scope, bit_length=bits)
                for bits in (8, 16, 24, 32, 64):
                    self._type({"name": f"Unsigned {bits}", "qualifier": f"UInt{bits}", "encoding": "unsigned", "bit_length": bits})
                for kind, label, code in (("session", "DefaultSession", 1), ("security", "Locked", 0)):
                    group = self._object("state_group", kind.title(), kind.title())
                    state = self._object("state", label, label)
                    r.insert("state_groups", id=group, kind=kind, initial_state_id=state)
                    r.insert("states", id=state, group_id=group, code=code)
            r.db.execute("DELETE FROM undo_stack")
            r.db.execute("UPDATE history_state SET cursor_position=0")
            candidate.touch()
        except Exception:
            candidate.close(discard=True)
            self.session = previous
            raise
        if previous: previous.close(discard=True)
        return self.overview()

    def open_project(self, path=None, recovery=None):
        candidate = Session(recovery)
        candidate.initialize(source=path, recover=bool(recovery))
        old, self.session = self.session, candidate
        self.locale = self.r.one("SELECT default_locale FROM project_meta")["default_locale"]
        if old: old.close(discard=True)
        return self.overview()

    def close_project(self):
        if self.session:
            self.session.close(discard=True)
            self.session = None
        self._source_cache = None

    # ------------------------------------------------------------------ 보존 원문(원본 CDD) — 표시 전용
    def _source(self):
        """가져온 원본 CDD. 없거나 읽을 수 없으면 None. 읽기 전용 표시(U5)에만 쓰고 출력 의미에는 쓰지 않는다."""
        row = self.r.one("SELECT id,basename,format_version,sha256 FROM source_files WHERE kind='cdd' ORDER BY imported_at LIMIT 1")
        if not row: return None
        cache_key = (id(self.session), row["id"], row["sha256"], self.locale)
        if self._source_cache and self._source_cache[0] == cache_key: return self._source_cache[1]
        content = self.r.one("SELECT content FROM source_files WHERE id=?", (row["id"],))["content"]
        try:
            doc = SourceDocument(content, self.locale, row["basename"], row["format_version"])
        except Exception:  # 손상된 원문은 표시만 생략한다(가져온 데이터에는 영향 없음)
            doc = None
        self._source_cache = (cache_key, doc)
        return doc

    def _source_element(self, doc, oid):
        """객체에 대응하는 원본 요소(source_mappings.external_id → 요소 id, 없으면 node_path)."""
        if doc is None or not oid: return None
        for m in self.r.all("SELECT external_id,node_path FROM source_mappings WHERE object_id=? ORDER BY id", (oid,)):
            if m["external_id"] and m["external_id"] in doc.ids: return doc.ids[m["external_id"]]
            try:
                found = doc.root.getroottree().xpath(m["node_path"])
            except Exception:
                found = []
            if found and not isinstance(found[0], str): return found[0]
        return None

    def _external_ids(self, kind):
        """kind 객체 ID → 원본 요소 id(external_id)."""
        return {m["object_id"]: m["external_id"] for m in self.r.all(
            "SELECT m.object_id,m.external_id FROM source_mappings m JOIN objects o ON o.id=m.object_id WHERE o.kind=? AND m.external_id IS NOT NULL ORDER BY m.id DESC", (kind,))}

    def _object(self, kind, q, name, description="", object_id=None, texts=None):
        oid = object_id or uid()
        self.r.insert("objects", id=oid, kind=kind, qualifier=q, qualifier_key=key(q), supported=1, read_only=0)
        if texts:
            for field, locales in texts.items():
                for locale, text in locales.items(): self._text(oid, field, locale, text)
        else:
            self._text(oid, "name", self.locale, name)
            self._text(oid, "description", self.locale, description)
        return oid

    def _text(self, oid, field, locale, value):
        self.r.db.execute("INSERT INTO localized_texts VALUES(?,?,?,?,?) ON CONFLICT(object_id,field,locale) DO UPDATE SET value=excluded.value,search_key=excluded.search_key", (oid, field, locale, value, key(value)))

    def _common(self, oid, data):
        current=self.r.one("SELECT kind,qualifier FROM objects WHERE id=?",(oid,)) if "qualifier" in data else None
        # 바뀌지 않은 Qualifier는 다시 검사하지 않는다(가져온 CDD의 128자 넘는 Qualifier도 그대로 두고 다른 속성을 고칠 수 있다).
        if current is not None and str(data["qualifier"]).strip()!=current["qualifier"]:
            q = qualifier(str(data["qualifier"]).strip())
            if len(q) > QUALIFIER_MAX: raise DomainError(f"Qualifier는 {QUALIFIER_MAX}자까지입니다.")
            kind=current["kind"]
            if kind in ("variant","data_type","state") and self.r.one("SELECT 1 FROM objects WHERE kind=? AND qualifier_key=? AND id!=?",(kind,key(q),oid)):
                raise DomainError("동일한 Qualifier가 이미 있습니다.")
            self.r.update("objects", {"id": oid}, {"qualifier": q, "qualifier_key": key(q)})
        for field in ("name", "description"):
            if field in data:
                if field == "name" and not data[field].strip(): raise DomainError("이름을 입력하세요.")
                self._text(oid, field, data.get("locale", self.locale), data[field])

    def _editable(self, oid):
        obj = self.r.one("SELECT * FROM objects WHERE id=?", (oid,))
        if not obj: raise DomainError("객체가 없습니다.")
        if obj["kind"] in ("nrc", "protocol_service"): raise DomainError("읽기 전용 목록입니다. CDD 템플릿에서 가져온 참고 정보입니다.", "READ_ONLY")
        if not obj["supported"]: raise DomainError("미지원 객체는 읽기 전용입니다.", "READ_ONLY")
        if obj["read_only"]: raise DomainError("원문 배치로 가져온 객체는 읽기 전용입니다. 고정 프로파일로 새로 만들어 편집하세요.", "READ_ONLY")
        return obj

    def _variant_editable(self, vid):
        v = self.r.one("SELECT * FROM variants WHERE id=?", (vid,))
        if not v or v["locked"]: raise DomainError("변형이 없거나 잠겨 있습니다.")

    def _new_name(self, payload, kind):
        name = payload.get("name", "").strip()
        if not name: raise DomainError("이름을 입력하세요.")
        q = qualifier(payload.get("qualifier", ""))
        if len(q) > QUALIFIER_MAX: raise DomainError(f"Qualifier는 {QUALIFIER_MAX}자까지입니다.")  # [QUAL-R130] 새 객체
        if kind in ("variant", "data_type", "state") and self.r.one("SELECT id FROM objects WHERE kind=? AND qualifier_key=?", (kind, key(q))):
            raise DomainError("동일한 Qualifier가 이미 있습니다.")
        return self._object(kind, q, name, payload.get("description", ""), texts=payload.get("texts"))

    def _resolved_rows(self, names):
        """보존 목록의 '해결됨(가져옴)' 행: 속성 import.resolved가 있는 객체와 원본 경로."""
        paths = {m["object_id"]: m["node_path"] for m in self.r.all("SELECT object_id,node_path FROM source_mappings ORDER BY id")}
        rows = []
        for a in self.r.all("SELECT a.object_id,a.value_text,o.kind,o.qualifier FROM object_attributes a JOIN objects o ON o.id=a.object_id WHERE a.key='import.resolved' ORDER BY a.object_id"):
            rows.append({"id": a["object_id"], "name": f"{names.get(a['object_id'], a['qualifier'])} — {a['value_text']}",
                         "qualifier": paths.get(a["object_id"], a["qualifier"]), "node_path": paths.get(a["object_id"], ""), "reason_code": a["value_text"],
                         "owner_object_id": None, "semantic": 0, "kind": a["kind"], "supported": 1, "shared": 0, "category": "해결됨(가져옴)"})
        return rows

    def _with_segments(self, typ):
        """schema 13 구간별 선형·특성 곡선 타입이면 구간(점)을 붙인다(convert가 쓴다)."""
        if typ and typ.get("conversion") in ("scale_linear", "tab_intp"):
            return {**typ, "segments": self.r.all("SELECT * FROM conversion_segments WHERE data_type_id=? ORDER BY ordinal", (typ["id"],))}
        return typ

    def _has_column(self, table, column):
        return any(c["name"] == column for c in self.r.all(f'PRAGMA table_info("{table}")'))

    def _type(self, p):
        oid = p.get("id") or self._new_name(p, "data_type")
        enc = p.get("encoding", "unsigned")
        mode = p.get("conversion", "identity")
        # 가변 바이트 필드(schema 9): 배열 길이를 비우면 PDU 끝까지, 최소 min_length(·최대 max_length) 바이트.
        variable = enc == "bytes" and p.get("array_length", 1) in (None, "")
        previous = self.r.one("SELECT min_length,max_length FROM data_types WHERE id=?", (oid,)) if p.get("id") and self._has_column("data_types", "min_length") else None
        previous_min = previous["min_length"] if previous and previous["min_length"] is not None else 0
        previous_max = previous["max_length"] if previous else None
        values = dict(encoding=enc, bit_length=None if enc == "packet" else (8 if enc in ("ascii", "bytes") else integer(p.get("bit_length", 32 if enc == "float" else 16), 1, 64)), byte_order=p.get("byte_order", "big"), array_length=None if enc == "packet" or variable else (integer(p.get("array_length", 1), 1, 1048576) if enc in ("ascii", "bytes") else 1), min_length=integer(p.get("min_length", previous_min), 0, 1048576) if variable else None, max_length=(None if p.get("max_length", previous_max) in (None, "") else integer(p.get("max_length", previous_max), 0, 1048576)) if variable else None, conversion=mode, factor_text=ratio_text(p.get("factor_text", "1")) if mode == "linear" else None, offset_text=decimal_text(p.get("offset_text", "0")) if mode == "linear" else None, unit=p.get("unit", ""), structure_id=p.get("structure_id") if enc == "packet" else None)
        # schema 13: 앞 길이 바이트(가변 바이트 필드). 대화상자가 보내지 않으면 기존 값을 유지한다.
        if self._has_column("data_types", "leading_length_bits"):
            kept_leading = self.r.one("SELECT leading_length_bits FROM data_types WHERE id=?", (oid,))["leading_length_bits"] if p.get("id") else None
            leading = p.get("leading_length_bits", kept_leading)
            values["leading_length_bits"] = integer(leading, 8, 32) if variable and leading not in (None, "") else None
        # schema 13: 구간별 선형·특성 곡선의 구간(점). 대화상자가 보내지 않으면 기존 구간을 유지한다.
        segments = None
        if mode in ("scale_linear", "tab_intp"):
            segments = p["segments"] if "segments" in p else (self.r.all("SELECT * FROM conversion_segments WHERE data_type_id=? ORDER BY ordinal", (oid,)) if p.get("id") else [])
            segments = [{"ordinal": i, "lower_raw": str(x["lower_raw"]), "upper_raw": None if x.get("upper_raw") in (None, "") else str(x["upper_raw"]),
                         "factor_text": ratio_text(x["factor_text"]) if x.get("factor_text") not in (None, "") else None,
                         "offset_text": decimal_text(x["offset_text"]) if x.get("offset_text") not in (None, "") else None,
                         "value_text": decimal_text(x["value_text"]) if x.get("value_text") not in (None, "") else None} for i, x in enumerate(segments)]
        # schema 12: 수식 변환(CDD FORMULADT)은 원문 수식을 그대로 둔다. 대화상자가 보내지 않으면 기존 수식을 유지한다.
        if mode == "formula":
            kept = self.r.one("SELECT formula_expression FROM data_types WHERE id=?", (oid,)) if p.get("id") else None
            values["formula_expression"] = (p.get("formula_expression") or (kept or {}).get("formula_expression") or "").strip() or None
            if not values["formula_expression"]: raise DomainError("수식 변환에는 수식(coded→phys)이 필요합니다.")
        elif self._has_column("data_types", "formula_expression"):
            values["formula_expression"] = None
        if enc == "packet" and not values["structure_id"]:
            values["structure_id"] = self._structure(p["qualifier"] + "_Packet", p.get("members", []), "packet")
        elif enc == "packet" and "members" in p:
            self._members(values["structure_id"],p["members"])
        # 선형 변환의 원시값 유효 범위와 예외값(schema 2). 대화상자가 보내지 않으면 기존 값을 유지한다.
        existing = self.r.one("SELECT raw_lower,raw_upper FROM data_types WHERE id=?", (oid,)) if p.get("id") else {"raw_lower": None, "raw_upper": None}
        for bound in ("raw_lower", "raw_upper"):
            text = p.get(bound, existing[bound])
            values[bound] = None if text in (None, "") else str(integer(text))
        if "exclusions" in p:
            exclusions = self._exclusion_rows(p["exclusions"])
        else:
            exclusions = self.r.all("SELECT * FROM raw_exclusions WHERE data_type_id=?", (oid,)) if p.get("id") else []
        check_type(values, p.get("entries", []), exclusions, segments)
        # schema 9 이전 파일(마이그레이션 테스트의 옛 스키마)에는 min_length·max_length 열이 없다.
        if values["min_length"] is None and values["max_length"] is None and not self._has_column("data_types", "min_length"):
            values.pop("min_length"); values.pop("max_length")
        if p.get("id"):
            self._common(oid, p)
            self.r.update("data_types", {"id": oid}, values)
            self.r.delete("conversion_entries", data_type_id=oid)
        else:
            # 값이 없는 schema 2 열은 생략한다(기본값 NULL과 같음).
            self.r.insert("data_types", id=oid, **{k: v for k, v in values.items() if v is not None or k not in ("raw_lower", "raw_upper", "min_length", "max_length", "formula_expression", "leading_length_bits")})
        if "exclusions" in p:
            self.r.db.execute("DELETE FROM raw_exclusions WHERE data_type_id=?", (oid,))
            for e in exclusions: self.r.insert("raw_exclusions", **{**e, "data_type_id": oid})
        if "conversion_segments" in self.r.tables and (segments is not None or p.get("id")):
            self.r.db.execute("DELETE FROM conversion_segments WHERE data_type_id=?", (oid,))
            for x in segments or []: self.r.insert("conversion_segments", data_type_id=oid, **x)
        if mode == "text_table":
            for i, entry in enumerate(p.get("entries", [])):
                self.r.insert("conversion_entries", data_type_id=oid, ordinal=entry.get("ordinal", i), locale=entry.get("locale", self.locale), lower_raw=str(integer(entry["lower_raw"])), upper_raw=str(integer(entry["upper_raw"])), label=entry["label"])
        return oid

    @staticmethod
    def _exclusion_rows(items):
        """대화상자 행(범위·종류·언어·라벨) → raw_exclusions 행. 범위·종류가 같은 행은 한 예외의 언어별 라벨이다."""
        ordinals, rows = {}, []
        for e in items:
            lo, hi = str(integer(e["lower_raw"])), str(integer(e.get("upper_raw") or e["lower_raw"]))
            ordinal = ordinals.setdefault((lo, hi, e["kind"]), len(ordinals))
            label = e.get("label", "") or ""
            rows.append({"ordinal": ordinal, "locale": (e.get("locale") or "") if label else "", "lower_raw": lo, "upper_raw": hi, "kind": e["kind"], "label": label})
        return rows

    def _structure(self, q, members=(), kind="payload"):
        oid = self._object("structure", q, q)
        self.r.insert("structures", id=oid, kind=kind)
        self._members(oid, members)
        return oid

    def _members(self, structure_id, members):
        # 이 구조가 단독으로 가진 비트필드는 새 멤버에서 빠지면 함께 지운다(공유 packet 구조는 건드리지 않는다).
        # 반복(iteration) 항목 구조도 그 멤버가 단독으로 가진다.
        nested_old = [r["nested_structure_id"] for r in self.r.all("SELECT m.nested_structure_id FROM structure_members m JOIN structures s ON s.id=m.nested_structure_id WHERE m.structure_id=? AND (s.kind='bitfield' OR m.kind='iteration')", (structure_id,))]
        old = self.r.all("SELECT data_object_id FROM structure_members WHERE structure_id=? AND data_object_id IS NOT NULL", (structure_id,))
        self.r.delete("structure_members", structure_id=structure_id)
        for row in old:
            self.r.delete("data_objects", id=row["data_object_id"])
            self.r.delete("objects", id=row["data_object_id"])
        for i, m in enumerate(members):
            kind = m.get("kind", "data")
            fields = {}
            if kind == "data":
                type_id = m["data_type_id"]
                typ = self.r.one("SELECT * FROM data_types WHERE id=?", (type_id,))
                if not typ: raise DomainError("데이터 타입이 없습니다.")
                oid = self._object("data_object", qualifier(m.get("qualifier") or f"Field_{i+1}"), m.get("name") or f"Field {i+1}", texts=m.get("texts"))
                values = {}
                for value_key in ("default", "constant"):
                    text = m.get(value_key + "_text")
                    lit_kind = "integer" if typ["encoding"] in ("unsigned", "signed", "bcd") else "decimal" if typ["encoding"] == "float" else "text" if typ["encoding"] == "ascii" else "hex"
                    if text not in (None, "") and lit_kind in ("integer", "decimal"):
                        text = convert(self._with_segments(typ), text, exclusions=self.r.all("SELECT * FROM raw_exclusions WHERE data_type_id=?", (type_id,)))[0]
                    values[value_key + "_kind"] = lit_kind if text not in (None, "") else None
                    values[value_key + "_text"] = str(text) if text not in (None, "") else None
                self.r.insert("data_objects", id=oid, data_type_id=type_id, **values)
                fields["data_object_id"] = oid
            elif kind == "literal": fields["literal_bytes"] = bytes.fromhex(m["hex"]) if "hex" in m else m["literal_bytes"]
            elif kind == "reserved":
                fields["reserved_bits"] = integer(m["reserved_bits"], 1, 8388608)
                if m.get("label"): fields["label"] = str(m["label"])
            elif kind == "fault_record":
                if m.get("record_role") not in ("sr", "sd", "edr", "ed"): raise DomainError("고장 메모리 레코드 종류가 올바르지 않습니다.")
                fields["record_role"] = m["record_role"]
            elif kind == "structure": fields["nested_structure_id"] = m["nested_structure_id"]
            elif kind in ("did_data", "did_with_identifier"): fields["did_id"] = m["did_id"]
            elif kind == "iteration": fields.update(nested_structure_id=m["nested_structure_id"], min_items=integer(m.get("min_items", 0), 0, 65535))
            elif kind in ("dtc", "mux"): pass  # mux의 선택자·케이스는 호출자가 muxes·mux_cases에 넣는다(CDD 가져오기 전용)
            else: raise DomainError("지원하지 않는 멤버 종류입니다.")
            self.r.insert("structure_members", structure_id=structure_id, ordinal=i, kind=kind, **fields)
        for nested in nested_old:
            if not self.r.one("SELECT 1 FROM structure_members WHERE nested_structure_id=?", (nested,)): self._drop_structure(nested)

    def _did(self, p):
        self._variant_editable(p["variant_id"])
        oid = self._new_name(p, "did")
        structure = self._structure(p["qualifier"] + "_Data", p.get("members", []))
        number = integer(p["identifier"], 0, 65535)
        self.r.insert("dids", id=oid, scope_id="did", identifier=number, structure_id=structure)
        self.r.insert("variant_dids", variant_id=p["variant_id"], did_id=oid, scope_id="did", identifier=number)
        if p.get("read_service", True):
            self._service({"variant_id": p["variant_id"], "name": p["name"] + " 읽기", "qualifier": p["qualifier"] + "_Read", "sid": 34, "did_id": oid})
        return oid

    def _dtc(self, p):
        self._variant_editable(p["variant_id"])
        oid = self._new_name(p, "dtc")
        code = integer(p["code"], 0, 0xFFFFFF)
        self.r.insert("dtcs", id=oid, scope_id="dtc", code=code)
        self.r.insert("variant_dtcs", variant_id=p["variant_id"], dtc_id=oid, scope_id="dtc", code=code)
        if p.get("attributes"): self._set_dtc_attributes(oid, p["attributes"])  # 추가 대화상자의 속성 탭(M2-3)
        return oid

    def _dtc_record_items(self):
        """DTC 레코드 항목 템플릿(원본 CDD의 TR/ENUM/UNS RECORDITEMTMPL, 문서 순서). 원본 CDD가 없으면 빈 목록.
        키·표시명은 가져오기(cdd_model.Model.dtcs)와 같은 규칙이다: 키 = 'cdd.' + QUAL, 열거 표시명 = ETAG의 en-US(없으면 첫 값).
        ODX 출력은 이 키(원시값 #raw·다국어 @locale 제외)를 DTC의 SDG로 쓴다(예제 ODXEnvelope에서 확인한 대응)."""
        doc = self._source()
        if doc is None: return []
        kinds = {"TRRECORDITEMTMPL": "text", "ENUMRECORDITEMTMPL": "enum", "UNSRECORDITEMTMPL": "uns"}
        items, seen = [], set()
        for t in doc.doc.iter(*kinds):
            key_ = "cdd." + (t.findtext("QUAL") or t.get("id"))
            if key_ in seen: continue
            seen.add(key_)
            item = {"key": key_, "name": doc.name(t) or key_[4:], "kind": kinds[t.tag], "required": t.get("conv") == "req", "hex": t.get("df") == "hex",
                    "qualifier": t.findtext("QUAL") or "", "description": doc.text(t, "DESC")}
            if item["kind"] == "enum":
                item["choices"] = []
                for e in t.findall("ETAG"):
                    try: raw = integer(e.get("v"))
                    except DomainError: continue
                    item["choices"].append({"raw": raw, "label": cdd_preferred(cdd_tuv(e, "."))})
            items.append(item)
        return items

    def _set_dtc_attributes(self, oid, entries):
        """DTC 속성(원본 레코드 항목) 편집. 텍스트(TR)·다국어 복제(@locale)는 문자열, 열거(ENUM)는 템플릿 ETAG 값만 받아
        표시명과 원시값(#raw)을 함께 바꾸고, 부호 없는 정수(UNS)는 0 이상 정수만 받는다. 사용자 속성(user.*)은 문자열이다.
        값을 비우면 항목을 지운다(필수 항목은 거부). 그 밖의 키(#raw 직접 수정, 가져오기 메타데이터)는 거부한다."""
        r = self.r
        if r.one("SELECT kind FROM objects WHERE id=?", (oid,))["kind"] != "dtc": raise DomainError("DTC가 아닙니다.")
        items = {i["key"]: i for i in self._dtc_record_items()}

        def put(key_, value_type, value):
            if r.one("SELECT 1 FROM object_attributes WHERE object_id=? AND key=?", (oid, key_)):
                r.update("object_attributes", {"object_id": oid, "key": key_}, {"value_type": value_type, "value_text": value})
            else: r.insert("object_attributes", object_id=oid, key=key_, value_type=value_type, value_text=value)

        def drop(key_, item):
            if item and item["required"]: raise DomainError(f"'{item['name']}'은(는) 필수 항목이라 비울 수 없습니다.")
            r.delete("object_attributes", object_id=oid, key=key_)
            if item and item["kind"] == "enum": r.delete("object_attributes", object_id=oid, key=key_ + "#raw")

        for entry in entries:
            key_ = str(entry.get("key", ""))
            base, _, locale = key_.partition("@")
            item = items.get(base)
            if re.fullmatch(r"user\.[A-Za-z][A-Za-z0-9_.-]*", key_):
                value = str(entry.get("value", ""))
                if value.strip(): put(key_, "text", value)
                else: r.delete("object_attributes", object_id=oid, key=key_)
            elif item is None or "#" in key_:
                raise DomainError(f"바꿀 수 없는 속성입니다: {key_}")
            elif locale:
                if item["kind"] != "text" or not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", locale):
                    raise DomainError(f"바꿀 수 없는 속성입니다: {key_}")
                value = str(entry.get("value", ""))
                if value.strip(): put(key_, "text", value)
                else: r.delete("object_attributes", object_id=oid, key=key_)
            elif item["kind"] == "text":
                value = str(entry.get("value", ""))
                if value.strip(): put(key_, "text", value)
                else: drop(key_, item)
            elif item["kind"] == "enum":
                raw = entry.get("raw")
                if raw is None or str(raw).strip() == "": drop(key_, item); continue
                raw = integer(raw)
                choice = next((c for c in item["choices"] if c["raw"] == raw), None)
                if choice is None: raise DomainError(f"'{item['name']}'의 템플릿에 없는 값입니다: {raw}")
                put(key_, "text", choice["label"]); put(key_ + "#raw", "integer", str(raw))
            else:  # uns
                text = str(entry.get("value", "")).strip()
                if not text: drop(key_, item); continue
                value = integer(text, 0)
                put(key_, "hex", f"0x{value:X}") if item["hex"] else put(key_, "integer", str(value))

    def _set_qualifiers(self, p):
        """Qualifier 여러 개를 한 명령으로 바꾼다(M6-5 Qualifier 개요의 칸 편집·다시 만들기). 형식·중복 검사는 _common과 같다."""
        items = p.get("items") or []
        if not isinstance(items, list) or not items: raise DomainError("바꿀 Qualifier가 없습니다.")
        for item in items:
            oid = item.get("id") if isinstance(item, dict) else None
            if not oid: raise DomainError("Qualifier를 바꿀 객체가 없습니다.")
            self._editable(oid)
            self._common(oid, {"qualifier": str(item.get("qualifier", ""))})

    def _update_member(self, p):
        """데이터 배치 멤버 칸 편집(M5-2): 데이터 객체 하나의 이름·설명·데이터 타입·기본값·상수를 제자리에서 바꾼다.
        구조를 다시 만들지 않으므로 다른 멤버의 번역·속성·원본 연결은 그대로다. 소유자(DID·서비스)가 편집 가능해야 한다.
        packet 타입·고장 메모리 레코드 안의 멤버는 여러 곳에서 쓰이므로 이 명령으로 바꾸지 않는다."""
        r = self.r
        oid = p.get("id")
        obj = self._editable(oid)
        if obj["kind"] != "data_object": raise DomainError("데이터 객체가 아닙니다.")
        owner = self._structure_owner(oid, "data_object")
        owner_kind = r.one("SELECT kind FROM objects WHERE id=?", (owner,))["kind"] if owner else None
        if owner_kind not in ("did", "service"):
            raise DomainError("DID·서비스의 멤버만 여기서 바꿀 수 있습니다. packet 타입·레코드 멤버는 해당 속성 대화상자에서 바꾸세요.", "READ_ONLY")
        self._editable(owner)
        if owner_kind == "service" and r.one("SELECT profile FROM services WHERE id=?", (owner,))["profile"] == "raw":
            raise DomainError("원문 배치로 가져온 서비스는 읽기 전용입니다.", "READ_ONLY")
        self._common(oid, {k: str(p[k]) for k in ("name", "description") if k in p})
        if any(k in p for k in ("data_type_id", "default_text", "constant_text")):
            row = r.one("SELECT * FROM data_objects WHERE id=?", (oid,))
            type_id = p.get("data_type_id") or row["data_type_id"]
            typ = r.one("SELECT * FROM data_types WHERE id=?", (type_id,))
            if not typ: raise DomainError("데이터 타입이 없습니다.")
            values = {"data_type_id": type_id}
            lit_kind = "integer" if typ["encoding"] in ("unsigned", "signed", "bcd") else "decimal" if typ["encoding"] == "float" else "text" if typ["encoding"] == "ascii" else "hex"
            for value_key in ("default", "constant"):
                text = p[value_key + "_text"] if value_key + "_text" in p else row[value_key + "_text"]
                text = None if text is None else str(text).strip()
                if text and lit_kind in ("integer", "decimal"):
                    text = convert(self._with_segments(typ), text, exclusions=r.all("SELECT * FROM raw_exclusions WHERE data_type_id=?", (type_id,)))[0]
                values[value_key + "_kind"] = lit_kind if text else None
                values[value_key + "_text"] = str(text) if text else None
            r.update("data_objects", {"id": oid}, values)
        return owner

    def _layout_owners(self, object_ids):
        """배치 행의 데이터 객체 → 소유자(DID·서비스)·공유 변형 수·편집 가능 여부(M5-2 멤버 칸 편집)."""
        r = self.r
        result = {}
        for oid in dict.fromkeys(object_ids):
            owner = self._structure_owner(oid, "data_object")
            if not owner: continue
            o = r.one("SELECT kind,supported,read_only FROM objects WHERE id=?", (owner,))
            do = r.one("SELECT supported,read_only FROM objects WHERE id=?", (oid,))
            if not o or not do: continue
            kind = o["kind"]
            editable = bool(kind in ("did", "service") and o["supported"] and not o["read_only"] and do["supported"] and not do["read_only"])
            if kind == "did": shared = r.one("SELECT count(*) n FROM variant_dids WHERE did_id=?", (owner,))["n"]
            elif kind == "service":
                shared = r.one("SELECT count(*) n FROM variant_instances vi JOIN services s ON s.instance_id=vi.instance_id WHERE s.id=?", (owner,))["n"]
                editable = editable and r.one("SELECT profile FROM services WHERE id=?", (owner,))["profile"] != "raw"
            else: shared = 1
            result[oid] = {"owner": owner, "owner_kind": kind, "shared": shared, "editable": editable}
        return result

    def _service(self, p):
        self._variant_editable(p["variant_id"])
        sid = integer(p["sid"])
        if sid not in ROLE: raise DomainError("지원하지 않는 SID입니다.")
        oid = p.get("id")
        if oid:
            old = self.r.one("SELECT * FROM services WHERE id=?", (oid,))
            instance = old["instance_id"]
            self._common(oid, p)
            # 한 인스턴스가 읽기·쓰기 서비스를 함께 가질 수 있으므로 이 서비스의 DID 역할만 제거한다.
            if old["sid"] in (34, 46): self.r.delete("instance_dids", instance_id=instance, role="read" if old["sid"] == 34 else "write")
            for t in self.r.all("SELECT * FROM telegrams WHERE service_id=?", (oid,)):
                self.r.delete("telegrams", id=t["id"])
                self._drop_structure(t["structure_id"])
            self.r.delete("service_nrcs", service_id=oid)
        else:
            cl = self.r.one("SELECT id FROM diagnostic_classes WHERE role=?", (ROLE[sid],))
            if not cl:
                cid = self._object("diagnostic_class", ROLE[sid].title(), ROLE[sid].title())
                self.r.insert("diagnostic_classes", id=cid, role=ROLE[sid])
            else: cid = cl["id"]
            instance = self._object("diagnostic_instance", p["qualifier"] + "_Instance", p["name"])
            self.r.insert("diagnostic_instances", id=instance, class_id=cid, owner_variant_id=p["variant_id"])
            self._place_instance(p["variant_id"], cid, instance)
            oid = self._new_name(p, "service")
        sub = integer(p.get("subfunction", 1 if sid != 62 else 0), 0, 127) if sid in (16,17,49,62) else None
        routine = integer(p.get("routine_identifier", 1), 0, 65535) if sid == 49 else None
        fields = dict(instance_id=instance, sid=sid, subfunction=sub, routine_identifier=routine, physical=int(p.get("physical", True)), functional=int(p.get("functional", False)), suppress_positive=int(p.get("suppress_positive", False)) if sub is not None else 0, enabled=int(p.get("enabled", True)))
        if p.get("id"): self.r.update("services", {"id": oid}, fields)
        else: self.r.insert("services", id=oid, **fields)
        did = p.get("did_id")
        if sid in (34, 46) and not self.r.one("SELECT 1 FROM variant_dids WHERE variant_id=? AND did_id=?", (p["variant_id"], did)):
            raise DomainError("현재 변형의 DID를 선택하세요.")
        self._telegrams(oid, instance, p["qualifier"], fields, did, p.get("request_members", []), p.get("positive_members", []))
        for nrc in p.get("nrcs", [19,34,49]): self.r.insert("service_nrcs", service_id=oid, code=integer(nrc, 1, 255))
        return oid

    def _reassign_owner(self, instance):
        """원본 변형이 인스턴스를 더 이상 포함하지 않으면 남은 사용 변형(기본 변형 우선, 그다음 순서)을 원본으로 정한다."""
        row = self.r.one("SELECT owner_variant_id FROM diagnostic_instances WHERE id=?", (instance,))
        if row is None: return
        if row["owner_variant_id"] and self.r.one("SELECT 1 FROM variant_instances WHERE variant_id=? AND instance_id=?", (row["owner_variant_id"], instance)): return
        nxt = self.r.one("SELECT vi.variant_id FROM variant_instances vi JOIN variants v ON v.id=vi.variant_id WHERE vi.instance_id=? ORDER BY v.is_base DESC, v.ordinal LIMIT 1", (instance,))
        self.r.update("diagnostic_instances", {"id": instance}, {"owner_variant_id": nxt["variant_id"] if nxt else None})

    def _place_instance(self, variant, class_id, instance, ordinal=None):
        """인스턴스를 변형에 넣는다. 클래스가 변형에 없으면 끝에 추가하고, 인스턴스는 클래스 안 끝에 둔다."""
        if not self.r.one("SELECT 1 FROM variant_classes WHERE variant_id=? AND class_id=?", (variant, class_id)):
            n = self.r.one("SELECT coalesce(max(ordinal)+1,0) n FROM variant_classes WHERE variant_id=?", (variant,))["n"]
            self.r.insert("variant_classes", variant_id=variant, class_id=class_id, ordinal=n)
        if ordinal is None:
            ordinal = self.r.one("SELECT coalesce(max(v.ordinal)+1,0) n FROM variant_instances v JOIN diagnostic_instances i ON i.id=v.instance_id WHERE v.variant_id=? AND i.class_id=?", (variant, class_id))["n"]
        self.r.insert("variant_instances", variant_id=variant, instance_id=instance, ordinal=ordinal)

    def _telegrams(self, oid, instance, q, fields, did, request_members, positive_members):
        """고정 UDS 프로파일의 요청·긍정 응답 구조를 만든다. 수동 생성과 CDD 가져오기가 같은 규칙을 쓴다."""
        sid, sub, routine = fields["sid"], fields["subfunction"], fields["routine_identifier"]
        request, response = [{"kind": "literal", "literal_bytes": bytes([sid])}], [{"kind": "literal", "literal_bytes": bytes([sid+64])}]
        if sid in (34, 46):
            self.r.insert("instance_dids", instance_id=instance, did_id=did, role="read" if sid == 34 else "write")
            number = integer(self.r.one("SELECT identifier FROM dids WHERE id=?", (did,))["identifier"]).to_bytes(2, "big")
            # DID 헤더는 참조에서 생성하여 DID 번호 수정과 동기화한다.
            request.append({"kind": "did_with_identifier", "did_id": did} if sid == 46 else {"kind": "literal", "literal_bytes": number})
            response.append({"kind": "did_with_identifier", "did_id": did} if sid == 34 else {"kind": "literal", "literal_bytes": number})
        else:
            request.append({"kind": "literal", "literal_bytes": bytes([sub | (128 if fields["suppress_positive"] else 0)])})
            response.append({"kind": "literal", "literal_bytes": bytes([sub])})
            if routine is not None:
                for container in (request, response): container.append({"kind": "literal", "literal_bytes": routine.to_bytes(2, "big")})
            request.extend(request_members)
            response.extend(positive_members)
        for kind, members in (("request", request), ("positive", response)):
            st = self._structure(q + "_" + kind, members)
            self.r.insert("telegrams", id=uid(), service_id=oid, kind=kind, structure_id=st)

    def _drop_structure(self, oid):
        self._members(oid, [])
        self.r.delete("structures", id=oid)
        self.r.delete("objects", id=oid)

    def execute_command(self, action, payload, expected_revision, command_id=None):
        r = self.r
        try:
            command_id = str(uuid.UUID(str(command_id))) if command_id is not None else uid()
        except (ValueError, AttributeError):
            raise DomainError("명령 ID는 UUID여야 합니다.", "INVALID_COMMAND_ID")
        try:
            request = json.dumps({"action": action, "payload": payload}, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            raise DomainError("명령 입력에 직렬화할 수 없는 값이 있습니다.", "INVALID_COMMAND")
        request_sha256 = hashlib.sha256(request).hexdigest()
        receipt = r.receipt(command_id)
        if receipt:
            if receipt["request_sha256"] != request_sha256 or receipt["action"] != action:
                raise DomainError("이미 사용한 명령 ID에 다른 요청을 보낼 수 없습니다.", "COMMAND_ID_REUSED")
            return {**self.overview(), "selected_id": receipt["selected_id"], "already_applied": True}
        if self.session.readonly: raise DomainError("읽기 전용 프로젝트입니다.", "READ_ONLY")
        if action in ("undo", "redo"):
            if r.revision != expected_revision: raise DomainError("문서가 변경되었습니다.", "REVISION_CONFLICT")
            r.replay(action == "redo", command_id, request_sha256)
            return self.overview()
        result = None
        outcome = {}
        with r.command(action, expected_revision, command_id, request_sha256, outcome):
            oid = payload.get("id")
            if oid: self._editable(oid)
            if action == "create_did": result = self._did(payload)
            elif action == "create_dtc": result = self._dtc(payload)
            elif action in ("create_type", "update_type"): result = self._type(payload)
            elif action == "copy_type": result = self._copy_type(payload)
            elif action in ("create_service", "update_service"): result = self._service(payload)
            elif action == "create_variant": result = self._create_variant(payload)  # M6-4 변형 마법사(참조로·사본으로·빈 변형)
            elif action == "update_did":
                self._common(oid, payload)
                if "identifier" in payload:
                    number = integer(payload["identifier"], 0, 65535)
                    r.update("dids", {"id": oid}, {"identifier": number})
                    for service in r.all("SELECT s.* FROM services s JOIN instance_dids i ON i.instance_id=s.instance_id WHERE i.did_id=?", (oid,)):
                        kind = "request" if service["sid"] == 34 else "positive"
                        t = r.one("SELECT structure_id FROM telegrams WHERE service_id=? AND kind=?", (service["id"], kind))
                        if t: r.update("structure_members", {"structure_id": t["structure_id"], "ordinal": 1}, {"literal_bytes": number.to_bytes(2, "big")})
                if "members" in payload: self._members(r.one("SELECT structure_id FROM dids WHERE id=?", (oid,))["structure_id"], payload["members"])
            elif action == "update_dtc":
                self._common(oid, payload)
                if "code" in payload: r.update("dtcs", {"id": oid}, {"code": integer(payload["code"], 0, 0xFFFFFF)})
                if payload.get("attributes"): self._set_dtc_attributes(oid, payload["attributes"])
            elif action == "update_common":
                self._common(oid, payload)
                if "manufacturer" in payload: r.update("ecu", {"id": oid}, {"manufacturer": payload["manufacturer"]})
            elif action == "create_state":
                result = self._new_name(payload, "state")
                r.insert("states", id=result, group_id=payload["group_id"], code=integer(payload["code"], 0, 255))
                for service in r.all("SELECT DISTINCT service_id FROM service_conditions WHERE group_id=?", (payload["group_id"],)):
                    r.insert("service_conditions", service_id=service["service_id"], group_id=payload["group_id"], state_id=result, allowed=0)
            elif action == "update_state":
                self._common(oid,payload)
                old=r.one("SELECT group_id FROM states WHERE id=?",(oid,))
                if payload["group_id"]!=old["group_id"]:raise DomainError("기존 상태의 그룹은 변경할 수 없습니다. 새 상태를 만드세요.")
                r.update("states",{"id":oid},{"code":integer(payload["code"],0,255)})
            elif action == "set_conditions":
                r.delete("service_transitions", service_id=oid)
                r.delete("service_conditions", service_id=oid)
                for row in payload["conditions"]: r.insert("service_conditions", service_id=oid, **row)
                for gid in {c["group_id"] for c in payload["conditions"]}:
                    expected={x["id"] for x in r.all("SELECT id FROM states WHERE group_id=?",(gid,))}
                    if expected!={c["state_id"] for c in payload["conditions"] if c["group_id"]==gid}:raise DomainError("상태 그룹의 모든 상태를 지정하세요.")
                for row in payload["transitions"]:
                    allowed = next((c["allowed"] for c in payload["conditions"] if c["state_id"] == row["from_state_id"]), False)
                    if not allowed: raise DomainError("전이 출발 상태가 허용되어야 합니다.")
                    r.insert("service_transitions", service_id=oid, **row)
            elif action == "import_dtcs":
                for row in payload["rows"]: self._dtc({**row, "variant_id": payload["variant_id"]})
            elif action == "bulk_edit":
                if payload["field"] not in ("name","description"):raise DomainError("이름·설명만 일괄 편집할 수 있습니다.")
                for target in payload["ids"]:
                    self._editable(target)
                    self._common(target, {payload["field"]: payload["value"], "locale": self.locale})
            elif action == "set_translation":
                field = payload.get("field")
                locale = payload.get("locale", "")
                if field not in ("name", "description") or not re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", locale):
                    raise DomainError("필드 또는 언어 코드가 올바르지 않습니다.")
                self._text(oid, field, locale, str(payload.get("value", "")))
            elif action == "set_attributes":
                attribute_key = payload.get("key", "")
                if not re.fullmatch(r"user\.[A-Za-z][A-Za-z0-9_.-]*", attribute_key):
                    raise DomainError("사용자 속성 키는 user. 뒤에 영문자로 시작해야 합니다.")
                ids = list(dict.fromkeys(payload.get("ids", [])))
                if not ids: raise DomainError("속성을 설정할 객체를 선택하세요.")
                value = str(payload.get("value", ""))
                for target in ids:
                    self._editable(target)
                    if r.one("SELECT 1 FROM object_attributes WHERE object_id=? AND key=?", (target, attribute_key)):
                        r.update("object_attributes", {"object_id": target, "key": attribute_key}, {"value_type": "text", "value_text": value})
                    else:r.insert("object_attributes", object_id=target, key=attribute_key, value_type="text", value_text=value)
            elif action == "set_qualifiers": self._set_qualifiers(payload)
            elif action == "set_dtc_attributes":
                # 여러 DTC에 같은 레코드 항목 값을 넣는 묶음 명령(M2-2 속성 시트, M1-2 값 복사). 검사는 _set_dtc_attributes 규칙 그대로다.
                ids = list(dict.fromkeys(payload.get("ids") or []))
                if not ids: raise DomainError("DTC를 선택하세요.")
                for target in ids:
                    self._editable(target)
                    self._set_dtc_attributes(target, payload.get("attributes") or [])
            elif action == "update_member": result = self._update_member(payload)  # 선택은 멤버의 소유자(DID·서비스) 행에 남긴다
            elif action == "replace_data_type": result = self._replace_data_type(payload)  # M6-3: 선택은 새 타입
            elif action == "detach_did": result = self._detach(payload["variant_id"], oid)
            elif action == "detach_instance": result = self._detach_instance(payload["variant_id"], oid)
            elif action == "bulk_conditions": self._bulk_conditions(payload)
            elif action == "set_rework": self._set_rework(payload)
            elif action == "delete": self._delete(payload)
            else: raise DomainError("지원하지 않는 명령입니다.")
            # 구조 순환은 불완전 초안에도 허용하지 않는다.
            if action in ("create_did", "update_did", "create_type", "update_type", "copy_type", "create_service", "update_service", "detach_did", "detach_instance", "update_member",
                          "replace_data_type", "create_variant"):
                snap = r.snapshot(self.locale)
                for structure in snap.rows("structures"): snap.flatten(structure["id"])
                missing=r.one("SELECT vi.variant_id FROM variant_instances vi JOIN instance_dids i ON i.instance_id=vi.instance_id LEFT JOIN variant_dids vd ON vd.variant_id=vi.variant_id AND vd.did_id=i.did_id WHERE vd.did_id IS NULL LIMIT 1")
                if missing:raise DomainError("공유 서비스의 DID가 모든 사용 변형에 포함되어야 합니다.")
            outcome["selected_id"] = result or payload.get("id")
        self.session.touch()
        return {**self.overview(), "selected_id": outcome["selected_id"]}

    def _copy_object(self, oid):
        row = self.r.one("SELECT * FROM objects WHERE id=?", (oid,))
        new = uid()
        self.r.insert("objects", **{**row, "id": new})
        for t in self.r.all("SELECT * FROM localized_texts WHERE object_id=?", (oid,)):
            self.r.insert("localized_texts", **{**t, "object_id": new})
        for table in ("source_mappings", "object_attributes"):
            for row in self.r.all(f"SELECT * FROM {table} WHERE object_id=?", (oid,)):
                if table == "source_mappings": row.pop("id")
                self.r.insert(table, **{**row, "object_id": new})
        return new

    def _copy_type(self, payload):
        """Clone the complete type/structure dependency graph in the caller's command."""
        root = payload["id"]
        if not self.r.one("SELECT 1 FROM data_types WHERE id=?", (root,)):
            raise DomainError("데이터 타입을 선택하세요.")
        name = payload.get("name", "").strip()
        q = qualifier(payload.get("qualifier", ""))
        if not name: raise DomainError("사본 이름을 입력하세요.")
        if self.r.one("SELECT 1 FROM objects WHERE kind='data_type' AND qualifier_key=?", (key(q),)):
            raise DomainError("동일한 데이터 타입 Qualifier가 이미 있습니다.")
        types, structures, active = {}, {}, set()
        used = {row["qualifier_key"] for row in self.r.all("SELECT qualifier_key FROM objects WHERE kind='data_type'")}
        used.add(key(q))

        def copy_object(oid):
            self._editable(oid)
            if self.r.one("SELECT 1 FROM unsupported_nodes WHERE owner_object_id=? AND semantic=1", (oid,)):
                raise DomainError("미지원 의미가 연결된 타입·구조는 독립 복사할 수 없습니다.", "UNSUPPORTED_COPY")
            return self._copy_object(oid)

        def enter(oid):
            if oid in active or len(active) >= 128:
                raise DomainError("순환 또는 과도하게 중첩된 타입은 복사할 수 없습니다.", "BROKEN_REFERENCE")
            active.add(oid)

        def clone_structure(oid):
            if oid in structures: return structures[oid]
            enter(oid)
            row = self.r.one("SELECT * FROM structures WHERE id=?", (oid,))
            if not row: raise DomainError("필수 데이터 구조가 없습니다.", "BROKEN_REFERENCE")
            new = copy_object(oid)
            self.r.insert("structures", **{**row, "id": new})
            for member in self.r.all("SELECT * FROM structure_members WHERE structure_id=? ORDER BY ordinal", (oid,)):
                if member["kind"] in ("did_data", "did_with_identifier", "dtc", "fault_record"):
                    raise DomainError("DID·고장 메모리를 참조하는 타입은 현재 독립 복사 범위에 포함되지 않습니다.", "UNSUPPORTED_COPY")
                values = {**member, "structure_id": new}
                if member["data_object_id"]:
                    obj = self.r.one("SELECT * FROM data_objects WHERE id=?", (member["data_object_id"],))
                    child_type = clone_type(obj["data_type_id"])
                    child = copy_object(obj["id"])
                    self.r.insert("data_objects", **{**obj, "id": child, "data_type_id": child_type})
                    values["data_object_id"] = child
                if member["nested_structure_id"]:
                    values["nested_structure_id"] = clone_structure(member["nested_structure_id"])
                self.r.insert("structure_members", **values)
            active.remove(oid)
            structures[oid] = new
            return new

        def clone_type(oid):
            if oid in types: return types[oid]
            enter(oid)
            row = self.r.one("SELECT * FROM data_types WHERE id=?", (oid,))
            if not row: raise DomainError("필수 데이터 타입이 없습니다.", "BROKEN_REFERENCE")
            new = copy_object(oid)
            if oid == root:
                new_q = q
            else:
                base = self.r.one("SELECT qualifier FROM objects WHERE id=?", (oid,))["qualifier"] + "_Copy"
                new_q, number = base, 2
                while key(new_q) in used:
                    new_q, number = f"{base}_{number}", number + 1
                used.add(key(new_q))
            self._common(new, {"qualifier": new_q, **({"name": name} if oid == root else {})})
            structure = clone_structure(row["structure_id"]) if row["structure_id"] else None
            self.r.insert("data_types", **{**row, "id": new, "structure_id": structure})
            for table in ("conversion_entries", "raw_exclusions") + (("conversion_segments",) if "conversion_segments" in self.r.tables else ()):
                for entry in self.r.all(f"SELECT * FROM {table} WHERE data_type_id=?", (oid,)):
                    self.r.insert(table, **{**entry, "data_type_id": new})
            active.remove(oid)
            types[oid] = new
            return new

        return clone_type(root)

    # ------------------------------------------------------------------ 데이터 타입 바꾸기(M6-3)
    @staticmethod
    def _literal_kind(encoding):
        """데이터 객체 기본값·상수의 저장 종류(_members와 같은 규칙)."""
        return "integer" if encoding in ("unsigned", "signed", "bcd") else "text" if encoding == "ascii" else "hex"

    @staticmethod
    def _type_bits(t, s):
        """타입의 고정 비트 길이. 가변 길이(bytes, 배열 길이 없음)는 None, packet은 구조 합(구조 오류면 None)."""
        if t["encoding"] == "packet":
            try: return sum(f["bits"] for f in s.flatten(t["structure_id"]))
            except DomainError: return None
        if t["encoding"] in ("ascii", "bytes"): return None if t["array_length"] is None else 8 * t["array_length"]
        return t["bit_length"]

    def _data_object_place(self, oid):
        """데이터 객체가 놓인 곳: 담은 구조와 그 종류, 최상위 소유자(DID·서비스·packet 타입·확장 데이터 레코드)와 그 최상위 구조, 전문 종류.
        MUX 케이스·기본 구조 안이면 mux=True(소유자를 찾지 않는다). 구조에 없으면 None."""
        r = self.r
        row = r.one("SELECT m.structure_id,s.kind FROM structure_members m JOIN structures s ON s.id=m.structure_id WHERE m.data_object_id=?", (oid,))
        if not row: return None
        place = {"container": row["structure_id"], "container_kind": row["kind"], "owner": None, "owner_kind": None, "root": None, "telegram": None, "mux": False}
        structure = row["structure_id"]
        for _ in range(130):
            found = r.one("SELECT id FROM dids WHERE structure_id=?", (structure,))
            if found: return {**place, "owner": found["id"], "owner_kind": "did", "root": structure}
            found = r.one("SELECT service_id,kind FROM telegrams WHERE structure_id=?", (structure,))
            if found: return {**place, "owner": found["service_id"], "owner_kind": "service", "root": structure, "telegram": found["kind"]}
            found = r.one("SELECT id FROM data_types WHERE structure_id=?", (structure,))
            if found: return {**place, "owner": found["id"], "owner_kind": "data_type", "root": structure}
            found = r.one("SELECT id FROM extended_records WHERE structure_id=?", (structure,))
            if found: return {**place, "owner": found["id"], "owner_kind": "extended_record", "root": structure}
            parent = r.one("SELECT structure_id FROM structure_members WHERE nested_structure_id=?", (structure,))
            if not parent:
                mux = r.one("SELECT 1 FROM mux_cases WHERE case_structure_id=? UNION SELECT 1 FROM muxes WHERE default_structure_id=?", (structure, structure))
                return {**place, "mux": bool(mux)}
            structure = parent["structure_id"]
        return place

    def _reachable_structures(self, structure_id):
        """구조에서 닿는 모든 구조(중첩 구조·반복 항목·packet 타입 구조·DID 구조·MUX 케이스). 순환 검사용."""
        r = self.r
        seen, todo = set(), [structure_id]
        while todo and len(seen) < 100000:
            current = todo.pop()
            if current in seen or current is None: continue
            seen.add(current)
            for m in r.all("SELECT m.*,d.data_type_id FROM structure_members m LEFT JOIN data_objects d ON d.id=m.data_object_id WHERE m.structure_id=?", (current,)):
                if m["nested_structure_id"]: todo.append(m["nested_structure_id"])
                if m["did_id"]: todo.append(r.one("SELECT structure_id FROM dids WHERE id=?", (m["did_id"],))["structure_id"])
                if m["data_type_id"]:
                    t = r.one("SELECT structure_id FROM data_types WHERE id=?", (m["data_type_id"],))
                    if t and t["structure_id"]: todo.append(t["structure_id"])
                if m["kind"] == "mux":
                    for c in r.all("SELECT case_structure_id FROM mux_cases WHERE structure_id=? AND ordinal=?", (current, m["ordinal"])): todo.append(c["case_structure_id"])
                    x = r.one("SELECT default_structure_id FROM muxes WHERE structure_id=? AND ordinal=?", (current, m["ordinal"]))
                    if x and x["default_structure_id"]: todo.append(x["default_structure_id"])
        return seen

    def replace_type_preview(self, old_type_id, new_type_id=None):
        """데이터 타입 바꾸기 대화상자(M6-3, Replace Data Type) 자료: old_type_id를 쓰는 데이터 객체(사용처)와, new_type_id로 바꿀 때의 영향·충돌.
        충돌(고를 수 없음, 이유를 conflict에): 원본 보존 항목(읽기 전용·미지원·원문 배치·보존 원문 출력 차단), 고장 메모리 레코드·MUX 케이스 구조, MUX 선택자([D002]),
        packet ↔ 단순 타입, 순환, 비트필드 안의 길이 변경(비트필드 컨테이너), 사용 중인 packet 타입의 길이 변경, 가변 길이를 마지막이 아닌 자리에,
        기본값·상수의 종류가 바뀜(정수 ↔ 글자·바이트), 기본값·상수가 새 타입 범위 밖, 전문 1 MiB 초과.
        영향(고를 수 있음, impact에): 길이 변화와 뒤 필드 위치 이동, 기본값·상수의 물리값 변화(원시값은 그대로), 공유 변형 수."""
        r = self.r
        s = r.snapshot(self.locale)
        names = self._names()
        old = r.one("SELECT t.*,o.qualifier,o.supported FROM data_types t JOIN objects o ON o.id=t.id WHERE t.id=?", (old_type_id,))
        if not old: raise DomainError("데이터 타입이 없습니다.")
        new = None
        if new_type_id:
            new = r.one("SELECT t.*,o.qualifier,o.supported FROM data_types t JOIN objects o ON o.id=t.id WHERE t.id=?", (new_type_id,))
            if not new: raise DomainError("새 데이터 타입이 없습니다.")
            if new_type_id == old_type_id: raise DomainError("바꿀 타입과 다른 데이터 타입을 고르세요.")
        old_bits = self._type_bits(old, s)
        new_bits = self._type_bits(new, s) if new else None
        selectors = {m["selector_object_id"] for m in r.all("SELECT selector_object_id FROM muxes")}
        preserved = {n["owner_object_id"] for n in r.all("SELECT owner_object_id FROM unsupported_nodes WHERE semantic=1 AND owner_object_id IS NOT NULL")}
        usage = {x["data_type_id"]: x["n"] for x in r.all("SELECT data_type_id,count(*) n FROM data_objects GROUP BY data_type_id")}
        kind_text = {"did": "DID", "service": "서비스", "data_type": "packet 타입", "extended_record": "고장 메모리 레코드"}
        entries = lambda t: [e for e in s.rows("conversion_entries") if e["data_type_id"] == t["id"]]
        exclusions = lambda t: [e for e in s.rows("raw_exclusions") if e["data_type_id"] == t["id"]]
        layouts, reach = {}, None
        rows = []
        for d in r.all("SELECT d.* FROM data_objects d WHERE d.data_type_id=?", (old_type_id,)):
            oid = d["id"]
            place = self._data_object_place(oid)
            owner = place["owner"] if place else None
            conflict = ""
            if place is None: conflict = "구조에 놓이지 않은 데이터 객체입니다."
            elif place["mux"]: conflict = "MUX 케이스 구조 안의 데이터 객체는 바꿀 수 없습니다(원본 CDD에서 가져온 배치)."
            elif owner is None: conflict = "담은 DID·서비스·타입을 찾지 못했습니다."
            elif place["owner_kind"] == "extended_record": conflict = "고장 메모리 확장 데이터 레코드의 구조는 표시 전용입니다."
            if not conflict:
                try:
                    self._editable(oid); self._editable(owner)
                    if place["owner_kind"] == "service":
                        service = r.one("SELECT instance_id,profile FROM services WHERE id=?", (owner,))
                        if service["profile"] == "raw": raise DomainError("원문 배치로 가져온 서비스는 읽기 전용입니다.")
                        blocked = {oid, owner, service["instance_id"]} & preserved
                    else: blocked = {oid, owner} & preserved
                    if blocked: raise DomainError("보존 원문(출력 차단)이 연결된 원본 보존 항목이라 바꿀 수 없습니다.")
                except DomainError as e: conflict = str(e)
            if not conflict and oid in selectors: conflict = "MUX 선택자는 바꿀 수 없습니다."
            impact = []
            if new and not conflict:
                if (old["encoding"] == "packet") != (new["encoding"] == "packet"):
                    conflict = "packet 타입과 단순 타입 사이에서는 바꿀 수 없습니다(구조가 달라짐)."
                elif new["encoding"] == "packet":
                    reach = reach if reach is not None else self._reachable_structures(new["structure_id"])
                    if place["container"] in reach or place["root"] in reach: conflict = "순환 구조가 됩니다(새 packet 타입 안에 이 구조가 있습니다)."
                if not conflict and new_bits != old_bits:
                    if place["container_kind"] == "bitfield":
                        conflict = f"비트필드 안에서 길이가 바뀝니다({self._bits_text(old_bits)} → {self._bits_text(new_bits)}). 비트필드 컨테이너를 넘거나 비트 위치가 어긋납니다."
                    elif place["owner_kind"] == "data_type" and usage.get(owner):
                        conflict = f"사용 중인 packet 타입(데이터 객체 {usage[owner]}곳)의 길이가 바뀝니다. 먼저 독립 복사로 새 타입을 만드세요."
                if not conflict and new_bits is None:
                    members = s.members(place["container"])
                    last = bool(members) and members[-1].get("data_object_id") == oid
                    if not (last and place["container"] == place["root"] and place["owner_kind"] in ("did", "service")):
                        conflict = "가변 길이 타입은 DID·전문 구조의 마지막 멤버에만 둘 수 있습니다."
                if not conflict:
                    values = [(label, d[k + "_text"]) for k, label in (("default", "기본값"), ("constant", "상수")) if d[k + "_text"] is not None]
                    if values and self._literal_kind(old["encoding"]) != self._literal_kind(new["encoding"]):
                        conflict = f"{'·'.join(label for label, _ in values)}이 있어 값의 종류가 바뀌는 타입({old['encoding']} → {new['encoding']})으로 바꿀 수 없습니다. 먼저 값을 지우세요."
                    elif values and self._literal_kind(new["encoding"]) != "integer":
                        # 26차 검토: 글자(ascii)·바이트(bytes) 값은 새 타입의 길이에 맞아야 전문을 만들 수 있다(domain.build_telegram와 같은 규칙).
                        for label, text in values:
                            try: size = len(str(text).encode("ascii") if new["encoding"] == "ascii" else bytes.fromhex(str(text)))
                            except (UnicodeError, ValueError): conflict = f"{label} {text}을(를) 새 타입의 값으로 읽을 수 없습니다."; break
                            if new_bits is not None and size * 8 != new_bits:
                                conflict = f"{label}이(가) {size}바이트인데 새 타입은 {new_bits // 8}바이트입니다. 먼저 값을 고치세요."; break
                            if new_bits is None and (size < (new["min_length"] or 0) or (new["max_length"] is not None and size > new["max_length"])):
                                conflict = f"{label}이(가) {size}바이트라 새 타입의 길이 범위({new['min_length'] or 0}~{new['max_length'] if new['max_length'] is not None else '끝까지'}바이트)를 벗어납니다."; break
                    elif values and self._literal_kind(new["encoding"]) == "integer":
                        for label, text in values:
                            try:
                                raw, after = convert(new, text, entries=entries(new), locale=self.locale, exclusions=exclusions(new))
                                before = convert(old, text, entries=entries(old), locale=self.locale, exclusions=exclusions(old))[1]
                                if after != before: impact.append(f"{label} 원시값 {raw}: {before}{' ' + old['unit'] if old['unit'] else ''} → {after}{' ' + new['unit'] if new['unit'] else ''}")
                            except DomainError as e:
                                conflict = f"{label} {text}이(가) 새 타입에 맞지 않습니다: {e}"; break
                if not conflict and new_bits != old_bits:
                    root = place["root"]
                    if root not in layouts:
                        try: layouts[root] = s.flatten(root)
                        except DomainError: layouts[root] = []
                    fields = layouts[root]
                    position = next((i for i, f in enumerate(fields) if f["kind"] == "data" and f["object"]["id"] == oid), None)
                    total = sum(f["bits"] for f in fields)
                    delta = (new_bits or 0) - (old_bits or 0)
                    if total + delta > 8 * 1048576: conflict = "전문 최대 길이(1 MiB)를 넘습니다."
                    else:
                        after = sum(1 for f in fields[position + 1:] if f["bits"] or f.get("variable")) if position is not None else 0
                        change = f"길이 {self._bits_text(old_bits)} → {self._bits_text(new_bits)}"
                        if delta and new_bits is not None and old_bits is not None: change += f" ({'+' if delta > 0 else ''}{delta}비트)"
                        impact.insert(0, change + (f" · 뒤 필드 {after}개 위치 이동" if after else ""))
                        if new_bits is not None and old_bits is not None and (total + delta) % 8:
                            impact.insert(1, f"⚠ 전체 길이 {total + delta}비트가 바이트 경계에 맞지 않게 됩니다(검증 VAL-005)")
            shared = 1
            if owner and place["owner_kind"] == "did": shared = r.one("SELECT count(*) n FROM variant_dids WHERE did_id=?", (owner,))["n"]
            elif owner and place["owner_kind"] == "service":
                shared = r.one("SELECT count(*) n FROM variant_instances vi JOIN services x ON x.instance_id=vi.instance_id WHERE x.id=?", (owner,))["n"]
            if new and not conflict and shared > 1: impact.append(f"공유 변형 {shared}개에 반영")
            try: path = self.object_path(oid, None, names)["breadcrumb"] if owner else ""
            except DomainError: path = ""
            telegram = {"request": " 요청", "positive": " 긍정 응답"}.get(place["telegram"], "") if place else ""
            rows.append({"id": oid, "name": names.get(oid, ""), "owner_id": owner, "owner": names.get(owner, "") if owner else "",
                         "kind_text": kind_text.get(place["owner_kind"] if place else None, "—") + telegram, "path": path or (names.get(owner, "") if owner else ""),
                         "shared": shared, "default_text": d["default_text"], "constant_text": d["constant_text"],
                         "conflict": conflict or None, "impact": " · ".join(impact) or None})
        rows.sort(key=lambda x: (key(x["path"]), key(x["name"]), x["id"]))
        types = []
        for t in r.all("SELECT t.*,o.qualifier,o.supported FROM data_types t JOIN objects o ON o.id=t.id WHERE t.id!=? AND o.supported=1", (old_type_id,)):
            types.append({"id": t["id"], "name": names.get(t["id"], t["qualifier"]), "qualifier": t["qualifier"], "encoding": t["encoding"],
                          "bits": self._type_bits(t, s), "unit": t["unit"], "length_text": length_text({**t, "packet_bits": self._type_bits(t, s) if t["encoding"] == "packet" else None})})
        types.sort(key=lambda t: (key(t["name"]), t["id"]))
        return {"old": {"id": old_type_id, "name": names.get(old_type_id, old["qualifier"]), "qualifier": old["qualifier"], "encoding": old["encoding"],
                        "bits": old_bits, "unit": old["unit"], "length_text": length_text({**old, "packet_bits": old_bits if old["encoding"] == "packet" else None})},
                "new_id": new_type_id, "rows": rows, "types": types, "revision": r.revision}

    @staticmethod
    def _bits_text(bits):
        return "가변" if bits is None else f"{bits}비트"

    def _replace_data_type(self, p):
        """데이터 타입 바꾸기(M6-3): 고른 사용처(데이터 객체)의 타입 참조를 새 타입으로 한 명령에서 바꾼다(Undo 한 번).
        미리보기와 같은 검사를 다시 하고 충돌이 있는 사용처가 하나라도 있으면 전체를 거부한다. 기본값·상수의 원시값은 그대로 두고 새 타입으로 다시 검사한다.
        바꾼 뒤 execute_command의 구조 검사(순환·길이)를 거친다."""
        r = self.r
        old, new = p.get("old_type_id"), p.get("new_type_id")
        if not old or not new: raise DomainError("바꿀 타입과 새 타입을 고르세요.")
        new_type = r.one("SELECT t.*,o.supported FROM data_types t JOIN objects o ON o.id=t.id WHERE t.id=?", (new,))
        if not new_type: raise DomainError("새 데이터 타입이 없습니다.")
        if not new_type["supported"]: raise DomainError("미지원 데이터 타입으로는 바꿀 수 없습니다.")
        ids = list(dict.fromkeys(p.get("data_object_ids") or []))
        if not ids: raise DomainError("바꿀 사용처를 하나 이상 고르세요.")
        preview = {row["id"]: row for row in self.replace_type_preview(old, new)["rows"]}
        exclusions = r.all("SELECT * FROM raw_exclusions WHERE data_type_id=?", (new,))
        for oid in ids:
            row = preview.get(oid)
            if row is None: raise DomainError("이 타입을 쓰는 사용처가 아닙니다.")
            if row["conflict"]: raise DomainError(f"{row['path'] or row['name']}: {row['conflict']}")
            current = r.one("SELECT * FROM data_objects WHERE id=?", (oid,))
            values = {"data_type_id": new}
            for value_key in ("default", "constant"):
                text = current[value_key + "_text"]
                if text is not None and self._literal_kind(new_type["encoding"]) == "integer":
                    text = convert(new_type, text, exclusions=exclusions)[0]
                values[value_key + "_kind"] = self._literal_kind(new_type["encoding"]) if text is not None else None
                values[value_key + "_text"] = text
            r.update("data_objects", {"id": oid}, values)
        return new

    def reference_impact(self, oid):
        snapshot = self.r.snapshot(self.locale)
        rows = build_reference_impact(snapshot, oid)
        names = self._names()
        for row in rows:
            try: row["path"] = self.object_path(row["id"], row["variant"], names)["path"]
            except DomainError: row["path"] = ""
        return {"rows": rows, "id": oid, "name": snapshot.name(oid), "revision": snapshot.revision,
                "uninterpreted": sum(1 for row in snapshot.rows("unsupported_nodes") if row["semantic"])}

    def deletion_impact(self, oid, variant=None):
        """Preview the real delete and its constraints under an always-rolled-back savepoint."""
        result = self.reference_impact(oid)
        result["record"] = self.details(oid)
        result.update(can_delete=False, reason="", removed=[])
        before = self._names()
        self.r.db.execute("SAVEPOINT delete_preview")
        try:
            if self.session.readonly: raise DomainError("읽기 전용 프로젝트입니다.", "READ_ONLY")
            if variant and result["record"]["kind"] in ("did", "dtc"):
                self._variant_editable(variant)
            self._delete({"id": oid, "variant_id": variant})
            if self.r.db.execute("PRAGMA foreign_key_check").fetchone():
                raise DomainError("필수 참조가 남아 있어 삭제할 수 없습니다.", "REFERENCE_IN_USE")
            remaining = {row["id"] for row in self.r.all("SELECT id FROM objects")}
            result.update(can_delete=True, removed=[name for target, name in before.items() if target not in remaining])
        except DomainError as exc:
            result["reason"] = str(exc)
        except sqlite3.IntegrityError:
            result["reason"] = "아래 사용처에 필수 참조가 남아 있어 삭제할 수 없습니다. 먼저 연결을 수정하세요."
        finally:
            self.r.db.execute("ROLLBACK TO delete_preview")
            self.r.db.execute("RELEASE delete_preview")
        return result

    def _clone_structure(self, oid, did_map=None):
        did_map = did_map or {}
        new = self._copy_object(oid)
        row = self.r.one("SELECT * FROM structures WHERE id=?", (oid,))
        self.r.insert("structures", **{**row, "id": new})
        for m in self.r.all("SELECT * FROM structure_members WHERE structure_id=? ORDER BY ordinal", (oid,)):
            m["structure_id"] = new
            if m["data_object_id"]:
                old_data = m["data_object_id"]
                m["data_object_id"] = self._copy_object(old_data)
                data = self.r.one("SELECT * FROM data_objects WHERE id=?", (old_data,))
                self.r.insert("data_objects", **{**data, "id": m["data_object_id"]})
            if m["nested_structure_id"]: m["nested_structure_id"] = self._clone_structure(m["nested_structure_id"], did_map)
            if m["did_id"] in did_map: m["did_id"] = did_map[m["did_id"]]
            self.r.insert("structure_members", **m)
        return new

    def _detach(self, variant, did):
        self._variant_editable(variant)
        source = self.r.one("SELECT * FROM dids WHERE id=?", (did,))
        if not self.r.one("SELECT 1 FROM variant_dids WHERE variant_id=? AND did_id=?", (variant,did)): raise DomainError("변형에 DID가 없습니다.")
        if self.r.one("SELECT count(*) n FROM variant_dids WHERE did_id=?", (did,))["n"] < 2: return did
        new = self._copy_object(did)
        self.r.insert("dids", **{**source, "id": new, "structure_id": self._clone_structure(source["structure_id"])})
        self.r.delete("variant_dids", variant_id=variant, did_id=did)
        self.r.insert("variant_dids", variant_id=variant, did_id=new, scope_id=source["scope_id"], identifier=source["identifier"])
        affected = self.r.all("SELECT DISTINCT i.* FROM diagnostic_instances i JOIN instance_dids d ON d.instance_id=i.id JOIN variant_instances v ON v.instance_id=i.id WHERE d.did_id=? AND v.variant_id=?", (did, variant))
        for ins in affected:
            new_ins = self._copy_object(ins["id"])
            self.r.insert("diagnostic_instances", id=new_ins, class_id=ins["class_id"], owner_variant_id=variant)
            # 사본은 원래 인스턴스 자리(같은 클래스·같은 순서)를 이어받는다.
            place = self.r.one("SELECT ordinal FROM variant_instances WHERE variant_id=? AND instance_id=?", (variant, ins["id"]))
            self.r.delete("variant_instances", variant_id=variant, instance_id=ins["id"])
            self._place_instance(variant, ins["class_id"], new_ins, place["ordinal"])
            self._reassign_owner(ins["id"])
            for ref in self.r.all("SELECT * FROM instance_dids WHERE instance_id=?", (ins["id"],)):
                self.r.insert("instance_dids", **{**ref, "instance_id": new_ins, "did_id": new if ref["did_id"] == did else ref["did_id"]})
            for service in self.r.all("SELECT * FROM services WHERE instance_id=?", (ins["id"],)):
                new_service = self._copy_object(service["id"])
                self.r.insert("services", **{**service, "id": new_service, "instance_id": new_ins})
                for table in ("service_nrcs", "service_conditions", "service_transitions"):
                    for row in self.r.all(f"SELECT * FROM {table} WHERE service_id=?", (service["id"],)):
                        self.r.insert(table, **{**row, "service_id": new_service})
                for t in self.r.all("SELECT * FROM telegrams WHERE service_id=?", (service["id"],)):
                    self.r.insert("telegrams", **{**t, "id": uid(), "service_id": new_service, "structure_id": self._clone_structure(t["structure_id"], {did:new})})
        return new

    def _detach_instance(self, variant, oid):
        """공유 인스턴스를 이 변형에서 사본으로 바꾼다(U3, CANdela "Replace by copy in this variant").
        사본은 같은 클래스 자리를 이어받고 원본 변형은 이 변형이 된다. DID는 그대로 공유한다(DID 분리는 detach_did).
        oid가 서비스면 그 서비스의 사본 ID를, 인스턴스면 사본 인스턴스 ID를 돌려준다.
        보존 원문·원문 배치·고장 메모리 레코드가 연결된 인스턴스는 사본에 그 의미가 따라가지 않으므로 거부한다."""
        r = self.r
        self._variant_editable(variant)
        kind = r.one("SELECT kind FROM objects WHERE id=?", (oid,))["kind"]
        if kind == "service": instance = r.one("SELECT instance_id FROM services WHERE id=?", (oid,))["instance_id"]
        elif kind == "diagnostic_instance": instance = oid
        else: raise DomainError("서비스나 진단 인스턴스를 선택하세요.")
        if not r.one("SELECT 1 FROM variant_instances WHERE variant_id=? AND instance_id=?", (variant, instance)):
            raise DomainError("현재 변형에 이 진단 인스턴스가 없습니다.")
        if r.one("SELECT count(*) n FROM variant_instances WHERE instance_id=?", (instance,))["n"] < 2: return oid
        services = r.all("SELECT s.*,o.supported,o.read_only FROM services s JOIN objects o ON o.id=s.id WHERE s.instance_id=?", (instance,))
        owned = [instance] + [x["id"] for x in services]
        marks = ",".join("?" for _ in owned)
        if r.one(f"SELECT 1 FROM unsupported_nodes WHERE owner_object_id IN ({marks})", owned):
            raise DomainError("보존 원문이 연결된 인스턴스는 사본으로 바꿀 수 없습니다. 원문의 출력 차단 정보가 사본에 따라가지 않습니다.", "UNSUPPORTED_COPY")
        if any(x["profile"] == "raw" or x["read_only"] or not x["supported"] for x in services):
            raise DomainError("원문 배치·미지원 서비스가 있는 인스턴스는 사본으로 바꿀 수 없습니다.", "UNSUPPORTED_COPY")
        if r.one("SELECT 1 FROM snapshot_records WHERE instance_id=? UNION SELECT 1 FROM extended_records WHERE instance_id=? "
                 "UNION SELECT 1 FROM snapshot_default_dids WHERE instance_id=? UNION SELECT 1 FROM snapshot_dtc_dids WHERE instance_id=?", (instance,) * 4):
            raise DomainError("고장 메모리 레코드 정의가 있는 인스턴스는 사본으로 바꿀 수 없습니다.", "UNSUPPORTED_COPY")
        source = r.one("SELECT * FROM diagnostic_instances WHERE id=?", (instance,))
        new_instance = self._copy_object(instance)
        r.insert("diagnostic_instances", **{**source, "id": new_instance, "owner_variant_id": variant})
        place = r.one("SELECT ordinal FROM variant_instances WHERE variant_id=? AND instance_id=?", (variant, instance))
        r.delete("variant_instances", variant_id=variant, instance_id=instance)
        self._place_instance(variant, source["class_id"], new_instance, place["ordinal"])
        self._reassign_owner(instance)
        for ref in r.all("SELECT * FROM instance_dids WHERE instance_id=?", (instance,)):
            r.insert("instance_dids", **{**ref, "instance_id": new_instance})
        copies = {}
        for service in services:
            row = {k: v for k, v in service.items() if k not in ("supported", "read_only")}
            new_service = self._copy_object(service["id"])
            r.insert("services", **{**row, "id": new_service, "instance_id": new_instance})
            for table in ("service_nrcs", "service_conditions", "service_transitions"):
                for item in r.all(f"SELECT * FROM {table} WHERE service_id=?", (service["id"],)):
                    r.insert(table, **{**item, "service_id": new_service})
            for telegram in r.all("SELECT * FROM telegrams WHERE service_id=?", (service["id"],)):
                r.insert("telegrams", **{**telegram, "id": uid(), "service_id": new_service, "structure_id": self._clone_structure(telegram["structure_id"])})
            copies[service["id"]] = new_service
        return copies.get(oid, new_instance)

    # ------------------------------------------------------------------ 변형 마법사(M6-4)
    def _instance_copy_block(self, instance):
        """인스턴스를 사본으로 만들 수 없는 이유(_detach_instance와 같은 규칙). 만들 수 있으면 None."""
        r = self.r
        services = r.all("SELECT s.id,s.profile,o.supported,o.read_only FROM services s JOIN objects o ON o.id=s.id WHERE s.instance_id=?", (instance,))
        owned = [instance] + [x["id"] for x in services]
        marks = ",".join("?" for _ in owned)
        if r.one(f"SELECT 1 FROM unsupported_nodes WHERE owner_object_id IN ({marks})", owned): return "보존 원문이 연결되어 있습니다(원문의 출력 차단 정보가 사본에 따라가지 않음)."
        if any(x["profile"] == "raw" or x["read_only"] or not x["supported"] for x in services): return "원문 배치·미지원 서비스가 있습니다."
        if r.one("SELECT 1 FROM snapshot_records WHERE instance_id=? UNION SELECT 1 FROM extended_records WHERE instance_id=? "
                 "UNION SELECT 1 FROM snapshot_default_dids WHERE instance_id=? UNION SELECT 1 FROM snapshot_dtc_dids WHERE instance_id=?", (instance,) * 4):
            return "고장 메모리 레코드 정의가 있습니다."
        return None

    def variant_wizard_items(self, source=None):
        """변형 마법사(M6-4) 자료: 기준 변형(없으면 기본 변형)의 클래스·인스턴스(문서 순서), 필수 여부, 사본으로 만들 수 없는 이유, DID·DTC·Job 수."""
        r = self.r
        base = r.one("SELECT id FROM variants WHERE is_base=1")["id"]
        source = source or base
        if not r.one("SELECT 1 FROM variants WHERE id=?", (source,)): raise DomainError("기준 변형이 없습니다.")
        names = self._names()
        info = self._instance_info(names)
        classes = []
        for c in r.all("SELECT vc.class_id,o.qualifier FROM variant_classes vc JOIN objects o ON o.id=vc.class_id WHERE vc.variant_id=? ORDER BY vc.ordinal", (source,)):
            instances = []
            for x in r.all("SELECT vi.instance_id FROM variant_instances vi JOIN diagnostic_instances i ON i.id=vi.instance_id WHERE vi.variant_id=? AND i.class_id=? ORDER BY vi.ordinal", (source, c["class_id"])):
                inst = info.get(x["instance_id"], {})
                instances.append({"id": x["instance_id"], "name": inst.get("name", ""), "identifier_text": inst.get("identifier_text") or "",
                                  "required": bool(inst.get("required")), "copy_block": self._instance_copy_block(x["instance_id"]), "services": inst.get("services", "")})
            classes.append({"id": c["class_id"], "name": names.get(c["class_id"], c["qualifier"]), "instances": instances})
        count = lambda table: r.one(f"SELECT count(*) n FROM {table} WHERE variant_id=?", (source,))["n"]
        return {"source": source, "base": base, "classes": classes, "counts": {"dids": count("variant_dids"), "dtcs": count("variant_dtcs"), "jobs": count("variant_jobs")},
                "variants": [{"id": v["id"], "name": names.get(v["id"], ""), "qualifier": v["qualifier"], "is_base": bool(v["is_base"])}
                             for v in r.all("SELECT v.id,v.is_base,o.qualifier FROM variants v JOIN objects o ON o.id=v.id ORDER BY v.ordinal")],
                "revision": r.revision}

    def _create_variant(self, p):
        """변형 추가·변형 마법사(M6-4, [VW-R040]·[VW-R500]). 한 명령(실행 취소 한 번)이다.
        mode: ref(참조로, 기본) = 기준 변형의 인스턴스를 모두 참조, copy(사본으로) = 사본(필수 인스턴스와 사본으로 만들 수 없는 인스턴스는 참조),
        empty(빈 변형) = 기본 변형의 필수 인스턴스만 참조([VW-R050]: 기준 변형을 고르지 않는다).
        instances = {인스턴스 ID: ref|copy|none}로 인스턴스별 동작을 바꾼다([VW-R200]). 필수 인스턴스는 none으로 뺄 수 없고, 사본은 _detach_instance와 같은 규칙이다.
        DID·DTC·Job: ref·copy는 기준 변형의 것을 모두 참조한다. empty는 넣은 인스턴스가 쓰는 DID(스냅샷 DID 포함)만 참조하고 DTC·Job은 넣지 않는다.
        [VW-R510] 모든 인스턴스가 참조 또는 사본이면 기준 변형의 설명(모든 언어, 입력한 설명이 없을 때)과 사용자 속성(user.*)을 복사한다.
        원본 CDD 속성(cdd.*)·식별 패턴은 원본 변형의 것이므로 복사하지 않는다."""
        r = self.r
        mode = p.get("mode") or "ref"
        if mode not in ("ref", "copy", "empty"): raise DomainError("변형을 만드는 방식은 참조로·사본으로·빈 변형 중 하나입니다.")
        base = r.one("SELECT id FROM variants WHERE is_base=1")["id"]
        source = base if mode == "empty" else p.get("source_id") or base
        self._variant_editable(source)
        overrides = p.get("instances") or {}
        if not isinstance(overrides, dict): raise DomainError("인스턴스 동작 형식이 올바르지 않습니다.")
        variant = self._new_name(p, "variant")
        ecu = r.one("SELECT id FROM ecu")["id"]
        ordinal = r.one("SELECT coalesce(max(ordinal),0)+1 AS n FROM variants")["n"]
        r.insert("variants", id=variant, ecu_id=ecu, is_base=0, ordinal=ordinal, locked=0, created_from_variant_id=source)
        names = self._names()
        info = self._instance_info(names)
        source_instances = r.all("SELECT * FROM variant_instances WHERE variant_id=? ORDER BY ordinal", (source,))
        unknown = set(overrides) - {x["instance_id"] for x in source_instances}
        if unknown: raise DomainError("기준 변형에 없는 인스턴스의 동작이 있습니다.")
        actions = {}
        for row in source_instances:
            inst = row["instance_id"]
            label = names.get(inst, inst)
            required = bool(info.get(inst, {}).get("required"))
            block = self._instance_copy_block(inst)
            default = "ref" if mode == "ref" or required else ("ref" if block else "copy") if mode == "copy" else "none"
            action = overrides.get(inst, default)
            if action not in ("ref", "copy", "none"): raise DomainError("인스턴스 동작은 참조·사본·사용 안 함 중 하나입니다.")
            if required and action == "none": raise DomainError(f"필수 인스턴스는 사용 안 함으로 둘 수 없습니다: {label}")
            if action == "copy" and block: raise DomainError(f"{label}: 사본으로 만들 수 없습니다. {block}")
            actions[inst] = action
        class_of = {x["id"]: x["class_id"] for x in r.all("SELECT id,class_id FROM diagnostic_instances")}
        used_classes = {class_of[i] for i, a in actions.items() if a != "none"}
        for row in r.all("SELECT * FROM variant_classes WHERE variant_id=? ORDER BY ordinal", (source,)):
            if mode != "empty" or row["class_id"] in used_classes: r.insert("variant_classes", **{**row, "variant_id": variant})
        for row in source_instances:
            if actions[row["instance_id"]] != "none": r.insert("variant_instances", **{**row, "variant_id": variant})
        if mode != "empty":
            for table in ("variant_dids", "variant_dtcs", "variant_jobs"):
                for row in r.all(f"SELECT * FROM {table} WHERE variant_id=?", (source,)):
                    r.insert(table, **{**row, "variant_id": variant})
        else:
            included = [i for i, a in actions.items() if a != "none"]
            wanted = set()
            for inst in included:
                for x in r.all("SELECT did_id FROM instance_dids WHERE instance_id=? UNION SELECT did_id FROM snapshot_default_dids WHERE instance_id=? "
                               "UNION SELECT did_id FROM snapshot_dtc_dids WHERE instance_id=?", (inst,) * 3): wanted.add(x["did_id"])
            for did in sorted(wanted):
                d = r.one("SELECT id,scope_id,identifier FROM dids WHERE id=?", (did,))
                r.insert("variant_dids", variant_id=variant, did_id=d["id"], scope_id=d["scope_id"], identifier=d["identifier"])
        for inst, action in actions.items():
            if action == "copy": self._detach_instance(variant, inst)
        if all(a != "none" for a in actions.values()):
            if not str(p.get("description") or "").strip():
                # 입력한 설명이 없으면 _new_name이 넣은 빈 설명을 지우고 기준 변형의 설명(모든 언어)을 넣는다(빈 설명이 표시 언어를 가리지 않게).
                r.db.execute("DELETE FROM localized_texts WHERE object_id=? AND field='description' AND value=''", (variant,))
                for t in r.all("SELECT locale,value FROM localized_texts WHERE object_id=? AND field='description'", (source,)): self._text(variant, "description", t["locale"], t["value"])
            for a in r.all("SELECT * FROM object_attributes WHERE object_id=? AND key LIKE 'user.%'", (source,)):
                r.insert("object_attributes", **{**a, "object_id": variant})
        return variant

    def _bulk_conditions(self, p):
        """여러 서비스의 한 상태 칸을 한 명령으로 바꾼다(U4 의존성 편집 모드, CANdela F2 일괄 변경).
        value: allow(허용) | block(차단) | transition(허용 + 도착 상태 전이) | no_transition(전이 제거).
        조건이 없는 그룹(제한 없음)에서 차단·전이를 정하면 그룹의 모든 상태를 허용으로 채운 뒤 그 칸만 바꾼다."""
        r = self.r
        ids = list(dict.fromkeys(p.get("ids") or []))
        if not ids: raise DomainError("서비스를 선택하세요.")
        state = r.one("SELECT id,group_id FROM states WHERE id=?", (p.get("state_id"),))
        if not state: raise DomainError("상태를 선택하세요.")
        group = state["group_id"]
        group_states = [x["id"] for x in r.all("SELECT id FROM states WHERE group_id=? ORDER BY code", (group,))]
        value = p.get("value")
        if value not in ("allow", "block", "transition", "no_transition"): raise DomainError("바꿀 값이 올바르지 않습니다.")
        target = p.get("target_state_id")
        if value == "transition" and target not in group_states: raise DomainError("전이 도착 상태는 같은 상태 그룹이어야 합니다.")
        for sid in ids:
            if self._editable(sid)["kind"] != "service": raise DomainError("서비스만 일괄 변경할 수 있습니다.")
            existing = r.all("SELECT state_id FROM service_conditions WHERE service_id=? AND group_id=?", (sid, group))
            if not existing:
                if value in ("allow", "no_transition"):
                    r.delete("service_transitions", service_id=sid, from_state_id=state["id"])
                    continue  # 제한 없음 = 모든 상태 허용
                for x in group_states: r.insert("service_conditions", service_id=sid, group_id=group, state_id=x, allowed=1)
            elif state["id"] not in {x["state_id"] for x in existing}:
                r.insert("service_conditions", service_id=sid, group_id=group, state_id=state["id"], allowed=1)
            if value == "no_transition":
                r.delete("service_transitions", service_id=sid, from_state_id=state["id"])
                continue
            r.update("service_conditions", {"service_id": sid, "group_id": group, "state_id": state["id"]}, {"allowed": 0 if value == "block" else 1})
            if value in ("block", "transition"): r.delete("service_transitions", service_id=sid, from_state_id=state["id"])
            if value == "transition":
                r.insert("service_transitions", service_id=sid, group_id=group, from_state_id=state["id"], to_state_id=target)

    def _set_rework(self, p):
        """Rework 목록(U6): 완료 여부·내용·연결 경로. ECU 객체의 앱 속성(app.rework, JSON)으로 저장하며 실행 취소된다. ODX에는 나가지 않는다."""
        items = p.get("items", [])
        if not isinstance(items, list) or len(items) > 1000: raise DomainError("Rework 항목은 1000개까지입니다.")
        clean = []
        for item in items:
            if not isinstance(item, dict): raise DomainError("Rework 항목 형식이 올바르지 않습니다.")
            message = str(item.get("message", "")).strip()
            if not message: raise DomainError("Rework 항목의 내용을 입력하세요.")
            clean.append({"done": bool(item.get("done")), "message": message[:2000], "link": str(item.get("link") or "")[:500]})
        ecu = self.r.one("SELECT id FROM ecu")["id"]
        value = json.dumps(clean, ensure_ascii=False)
        if self.r.one("SELECT 1 FROM object_attributes WHERE object_id=? AND key=?", (ecu, REWORK_KEY)):
            self.r.update("object_attributes", {"object_id": ecu, "key": REWORK_KEY}, {"value_type": "text", "value_text": value})
        else:
            self.r.insert("object_attributes", object_id=ecu, key=REWORK_KEY, value_type="text", value_text=value)

    def rework_items(self):
        ecu = self.r.one("SELECT id FROM ecu")["id"]
        row = self.r.one("SELECT value_text FROM object_attributes WHERE object_id=? AND key=?", (ecu, REWORK_KEY))
        try:
            items = json.loads(row["value_text"]) if row else []
        except ValueError:
            items = []
        return {"items": [i for i in items if isinstance(i, dict)] if isinstance(items, list) else [], "revision": self.r.revision}

    def _delete(self, p):
        oid = p["id"]
        kind = self._editable(oid)["kind"]
        if kind in ("did", "dtc"):
            self._variant_editable(p["variant_id"])
            if not self.r.one(f"SELECT 1 FROM variant_{kind}s WHERE variant_id=? AND {kind}_id=?", (p["variant_id"], oid)):
                raise DomainError("현재 변형에 해당 객체가 없습니다.")
        if kind in ("ecu", "state_group"): raise DomainError("이 객체는 삭제할 수 없습니다.")
        if kind == "variant":
            if self.r.one("SELECT is_base FROM variants WHERE id=?", (oid,))["is_base"]: raise DomainError("기본 변형은 삭제할 수 없습니다.")
            owned = [i["id"] for i in self.r.all("SELECT id FROM diagnostic_instances WHERE owner_variant_id=?", (oid,))]
            self.r.delete("variants", id=oid)
            for ins in owned: self._reassign_owner(ins)
        elif kind == "did":
            if self.r.one("SELECT 1 FROM instance_dids WHERE did_id=?", (oid,)): raise DomainError("사용 중인 서비스가 있습니다. 먼저 서비스를 삭제하세요.", "REFERENCE_IN_USE")
            if self.r.one("SELECT 1 FROM snapshot_default_dids WHERE did_id=? UNION SELECT 1 FROM snapshot_dtc_dids WHERE did_id=?", (oid, oid)):
                raise DomainError("고장 메모리 스냅샷 정의에서 사용하는 DID입니다.", "REFERENCE_IN_USE")
            row = self.r.one("SELECT * FROM dids WHERE id=?", (oid,))
            self.r.delete("variant_dids", variant_id=p["variant_id"], did_id=oid)
            if self.r.one("SELECT 1 FROM variant_dids WHERE did_id=?", (oid,)): return
            self.r.delete("dids", id=oid)
            self._drop_structure(row["structure_id"])
        elif kind == "dtc":
            self.r.delete("variant_dtcs", variant_id=p["variant_id"], dtc_id=oid)
            if self.r.one("SELECT 1 FROM variant_dtcs WHERE dtc_id=?", (oid,)): return
            self.r.delete("dtcs", id=oid)
        elif kind == "service":
            row = self.r.one("SELECT * FROM services WHERE id=?", (oid,))
            count = self.r.one("SELECT count(*) n FROM variant_instances WHERE instance_id=?", (row["instance_id"],))["n"]
            if count > 1: raise DomainError("공유 서비스입니다. DID를 현재 변형으로 분리한 후 삭제하세요.")
            if row["sid"] in (34, 46): self.r.delete("instance_dids", instance_id=row["instance_id"], role="read" if row["sid"] == 34 else "write")
            for t in self.r.all("SELECT * FROM telegrams WHERE service_id=?", (oid,)):
                self.r.delete("telegrams", id=t["id"])
                self._drop_structure(t["structure_id"])
            self.r.delete("services", id=oid)
            ins = row["instance_id"]
            # 서비스 생성으로 함께 만든 인스턴스만 정리한다. 가져온 인스턴스는 원본 연결·보존 원문이 있어 남긴다.
            if not self.r.one("SELECT 1 FROM services WHERE instance_id=?", (ins,)) and not self.r.one("SELECT 1 FROM source_mappings WHERE object_id=?", (ins,)):
                self.r.delete("variant_instances", instance_id=ins)
                self.r.delete("diagnostic_instances", id=ins)
                self.r.delete("objects", id=ins)
        elif kind == "data_type": self.r.delete("data_types", id=oid)
        elif kind == "state": self.r.delete("states", id=oid)
        else: raise DomainError("소유 객체의 편집 화면에서 삭제하세요.")
        self.r.delete("objects", id=oid)

    def overview(self):
        if not self.session: return {"open": False}
        r = self.r
        state = r.history()
        snap = r.snapshot(self.locale)
        ecu = r.one("SELECT id,manufacturer FROM ecu")
        variants = [{**v, "name": snap.name(v["id"])} for v in r.all("SELECT v.*,o.qualifier FROM variants v JOIN objects o ON o.id=v.id ORDER BY ordinal")]
        return {"open": True, "migrated": self.session.meta.get("migrated_to") or [], "revision": r.revision, "dirty": state["current_token"] != state["saved_token"], "readonly": self.session.readonly, "path": self.session.meta.get("source"), "name": snap.name(ecu["id"]), "ecu_id": ecu["id"], "variants": variants, "locale": self.locale, "undo": state["cursor_position"] > 0, "redo": bool(r.one("SELECT 1 FROM undo_stack WHERE position>?", (state["cursor_position"],))), "counts": {k: len(snap.rows(t)) for k,t in (("did","dids"),("dtc","dtcs"),("service","services"),("data_type","data_types"),("unsupported","unsupported_nodes"))}, "tree": self.tree()}

    # ------------------------------------------------------------------ 탐색 트리·목록
    def _names(self):
        names = {}
        texts = self.r.all("SELECT object_id,locale,value FROM localized_texts WHERE field='name'")
        texts.sort(key=lambda x: (x["locale"] != self.locale, x["locale"] != "en-US", x["locale"]))
        for t in texts: names.setdefault(t["object_id"], t["value"])
        for o in self.r.all("SELECT id,qualifier FROM objects"): names.setdefault(o["id"], o["qualifier"])
        return names

    @staticmethod
    def _identifier_text(services, dids):
        """인스턴스 식별자(CANdela 트리의 Identifier). 서비스 필드와 DID 참조에서 유도한다."""
        if dids: return f"{dids[0]:04X}"
        for svc in services:
            if svc["routine_identifier"] is not None: return f"{svc['routine_identifier']:04X}"
            if svc["subfunction"] is not None: return f"{svc['subfunction']:02X}"
        return ""

    def _instance_info(self, names):
        """인스턴스별 서비스·식별자·공유·필수·보존 경고. 트리와 목록이 같은 값을 쓴다."""
        r = self.r
        base = r.one("SELECT id FROM variants WHERE is_base=1")["id"]
        members = {}
        for vi in r.all("SELECT variant_id,instance_id FROM variant_instances"): members.setdefault(vi["instance_id"], set()).add(vi["variant_id"])
        services = {}
        for svc in r.all("SELECT s.*,o.qualifier FROM services s JOIN objects o ON o.id=s.id ORDER BY o.qualifier"): services.setdefault(svc["instance_id"], []).append(svc)
        dids = {}
        for d in r.all("SELECT x.instance_id,d.identifier FROM instance_dids x JOIN dids d ON d.id=x.did_id ORDER BY x.role"): dids.setdefault(d["instance_id"], []).append(d["identifier"])
        required = {a["object_id"] for a in r.all("SELECT object_id FROM object_attributes WHERE key='cdd.@req' AND value_text='req'")}
        warnings = {w["owner_object_id"]: w["n"] for w in r.all("SELECT owner_object_id,count(*) n FROM unsupported_nodes WHERE semantic=1 AND owner_object_id IS NOT NULL GROUP BY owner_object_id")}
        info = {}
        for i in r.all("SELECT i.id,i.class_id,i.owner_variant_id,o.qualifier FROM diagnostic_instances i JOIN objects o ON o.id=i.id"):
            used = members.get(i["id"], set())
            owner = i["owner_variant_id"] or (base if base in used else None)
            if owner == base: share = "base" if len(used) <= 1 else "base_shared"
            elif len(used) <= 1: share = "local"
            else: share = "ext_ref_base" if base in used else "ext_shared"
            svcs = services.get(i["id"], [])
            info[i["id"]] = {"id": i["id"], "class_id": i["class_id"], "qualifier": i["qualifier"], "name": names.get(i["id"], i["qualifier"]),
                             "identifier_text": self._identifier_text(svcs, dids.get(i["id"], [])), "share": share, "shared": len(used),
                             "owner": owner, "required": i["id"] in required, "warnings": warnings.get(i["id"], 0),
                             "services": ", ".join(names.get(x["id"], x["qualifier"]) for x in svcs), "service_count": len(svcs)}
        return info

    def tree(self):
        """탐색 트리 데이터. 순서는 CANdelaStudio [TREE-R010]을 따르고, 서비스는 트리에 넣지 않는다(인스턴스 목록에서 연다)."""
        r = self.r
        names = self._names()
        info = self._instance_info(names)
        singleton = {c["id"]: bool(c["singleton"]) for c in r.all("SELECT id,singleton FROM diagnostic_classes")}
        class_q = {c["id"]: c["qualifier"] for c in r.all("SELECT c.id,o.qualifier FROM diagnostic_classes c JOIN objects o ON o.id=c.id")}
        placed = {}
        for vi in r.all("SELECT vi.variant_id,vi.instance_id,vi.ordinal,i.class_id FROM variant_instances vi JOIN diagnostic_instances i ON i.id=vi.instance_id ORDER BY vi.ordinal"):
            placed.setdefault((vi["variant_id"], vi["class_id"]), []).append(info[vi["instance_id"]])
        did_n = {x["variant_id"]: x["n"] for x in r.all("SELECT variant_id,count(*) n FROM variant_dids GROUP BY variant_id")}
        dtc_n = {x["variant_id"]: x["n"] for x in r.all("SELECT variant_id,count(*) n FROM variant_dtcs GROUP BY variant_id")}
        svc_n = {x["variant_id"]: x["n"] for x in r.all("SELECT v.variant_id,count(*) n FROM variant_instances v JOIN services s ON s.instance_id=v.instance_id GROUP BY v.variant_id")}
        warnings = {w["owner_object_id"]: w["n"] for w in r.all("SELECT owner_object_id,count(*) n FROM unsupported_nodes WHERE semantic=1 AND owner_object_id IS NOT NULL GROUP BY owner_object_id")}
        variants = []
        for v in r.all("SELECT v.id,v.is_base,v.locked FROM variants v ORDER BY v.ordinal"):
            classes = []
            for c in r.all("SELECT class_id FROM variant_classes WHERE variant_id=? ORDER BY ordinal", (v["id"],)):
                cid = c["class_id"]
                classes.append({"id": cid, "name": names.get(cid, class_q[cid]), "qualifier": class_q[cid], "singleton": singleton[cid], "instances": placed.get((v["id"], cid), [])})
            jobs = [{"id": j["id"], "name": names.get(j["id"], j["qualifier"]), "reference": j["owner_variant_id"] != v["id"], "warnings": warnings.get(j["id"], 0)}
                    for j in r.all("SELECT j.id,j.owner_variant_id,o.qualifier FROM variant_jobs vj JOIN job_containers j ON j.id=vj.job_id JOIN objects o ON o.id=j.id WHERE vj.variant_id=? ORDER BY vj.ordinal", (v["id"],))]
            variants.append({"id": v["id"], "name": names.get(v["id"], ""), "is_base": bool(v["is_base"]), "locked": bool(v["locked"]),
                             "dids": did_n.get(v["id"], 0), "dtcs": dtc_n.get(v["id"], 0), "services": svc_n.get(v["id"], 0), "classes": classes, "jobs": jobs})
        groups = []
        for g in r.all("SELECT g.id,g.initial_state_id FROM state_groups g ORDER BY g.kind='security'"):
            states = r.all("SELECT id,code FROM states WHERE group_id=? ORDER BY code", (g["id"],))
            states.sort(key=lambda st: st["id"] != g["initial_state_id"])
            groups.append({"id": g["id"], "name": names.get(g["id"], ""), "states": [{"id": st["id"], "name": names.get(st["id"], ""), "code": st["code"]} for st in states]})
        counts = {k: r.one(f"SELECT count(*) n FROM {t}")["n"] for k, t in (("did", "dids"), ("dtc", "dtcs"), ("data_type", "data_types"), ("unsupported", "unsupported_nodes"), ("nrc", "nrcs"), ("protocol_service", "protocol_services"))}
        counts["unsupported_blocking"] = r.one("SELECT count(*) n FROM unsupported_nodes WHERE semantic=1")["n"]
        counts["unsupported_resolved"] = r.one("SELECT count(*) n FROM object_attributes WHERE key='import.resolved'")["n"]
        root_name = names.get(r.one("SELECT id FROM ecu")["id"], "")
        # 보존 원문에서 유도한 읽기 전용 노드(U4·U5): 지원 인터페이스, 식별 특징(기본)·식별 패턴(확장), 기본 상태 전이, 미지원 서비스 NRC.
        doc = self._source()
        extra = {"interfaces": [], "has_interfaces": False, "has_defaults": False, "unsupported_nrcs": 0}
        if doc is not None:
            interfaces = doc.interfaces()
            extra.update(interfaces=[{"qualifier": i["qualifier"], "name": i["name"]} for i in interfaces if i["supported"]],
                         has_interfaces=bool(interfaces), has_defaults=any(tm["services"] for tm in doc.class_templates()),
                         unsupported_nrcs=len(doc.unsupported_service_nrcs()))
            features = bool(doc.identification_features())
            for v in variants:
                if v["is_base"]: v["identification"] = "features" if features else None
                else:
                    element = self._source_element(doc, v["id"])
                    v["identification"] = "patterns" if element is not None and element.find("IDENTIFICATIONSET") is not None else None
        return {"variants": variants, "state_groups": groups, "counts": counts, **extra,
                "paths": self._path_map(variants, groups, root_name, extra)}

    def _path_map(self, variants, groups, root_name, extra=None):
        """노드 키 → 사람이 읽는 이름 경로. 트리 상태 데이터(이름 표·변형·클래스·인스턴스)에서 만들고
        화면에 표시된 라벨(장식 문자 포함)은 파싱하지 않는다. 구분자는 '/'이고 첫 조각은 ECU 이름이다."""
        paths = {}

        def add(key, segments):
            paths[key] = "/" + "/".join(str(s) for s in segments)

        add("ecu", [root_name])
        add("fm", [root_name, "고장 메모리"])
        add("dtc_ov", [root_name, "고장 메모리", "DTC 개요"])
        add("did_ov", [root_name, "DID 개요"])
        add("variants", [root_name, "변형"])
        add("states", [root_name, "상태"])
        add("dep", [root_name, "상태", "의존성"])
        add("types", [root_name, "데이터 타입"])
        add("psvc", [root_name, "프로토콜 서비스"])
        add("nrcs", [root_name, "부정 응답 코드"])
        add("unsupported", [root_name, "보존 항목"])
        # 중간 노드(U4·U5). 이름 경로는 CANdela npath처럼 데이터 모델을 따르므로 클래스·인스턴스 경로에는 중간 노드 이름을 넣지 않는다.
        add("sgroups", [root_name, "상태", "상태 그룹"])
        extra = extra or {}
        if extra.get("has_interfaces"):
            add("ifaces", [root_name, "지원 인터페이스"])
            for item in extra.get("interfaces", []):
                add(f"iface:{item['qualifier']}", [root_name, "지원 인터페이스", item["name"]])
        if extra.get("has_defaults"): add("sdefaults", [root_name, "상태", "기본 상태 전이"])
        if extra.get("unsupported_nrcs"): add("nrcs_unsupported", [root_name, "부정 응답 코드", "미지원 서비스 NRC"])
        for v in variants:
            prefix = f"var:{v['id']}"
            base = [root_name, v["name"]]
            add(prefix, base)
            add(prefix + ":did", base + ["DID"])
            add(prefix + ":dtc", base + ["DTC"])
            add(prefix + ":svc", base + ["모든 서비스"])
            add(prefix + ":classes", base + ["지원 진단 클래스"])
            if v.get("identification") == "features": add(prefix + ":ident", base + ["식별 특징"])
            elif v.get("identification") == "patterns": add(prefix + ":ident", base + ["식별 패턴"])
            for c in v["classes"]:
                if c["singleton"] and len(c["instances"]) == 1:
                    # 싱글턴은 클래스 노드 없이 인스턴스가 바로 온다(application.tree와 같은 키 규칙).
                    add(prefix + f":inst:{c['instances'][0]['id']}", base + [c["instances"][0]["name"]])
                    continue
                add(prefix + f":cls:{c['id']}", base + [c["name"]])
                for inst in c["instances"]:
                    add(prefix + f":inst:{inst['id']}", base + [c["name"], inst["name"]])
            for job in v.get("jobs", []):
                add(prefix + f":job:{job['id']}", base + [job["name"]])
        for group in groups:
            add(f"sg:{group['id']}", [root_name, "상태", group["name"]])
        return paths

    def _tree_name_paths(self):
        return self.tree()["paths"]

    def node_name_path(self, node_key):
        """탐색 항목의 이름 경로. 안정 UUID 경로(node_path)와 달리 사람이 읽고 입력할 수 있다."""
        entries = self._tree_name_paths()
        if node_key not in entries:
            raise DomainError("경로의 탐색 항목이 없습니다.")
        return {"key": node_key, "path": entries[node_key]}

    def resolve_name_path(self, path):
        """경로 입력을 트리 항목·객체로 되돌린다(M3-2). 이름 경로(npath, 기본)는 트리 항목 → 객체 순서로 찾고,
        정확히 일치 → 끝부분 일치(ECU 조각은 있어도 없어도 된다). qpath:는 Qualifier 경로다. 정확히 하나면 이동, 여럿이면 후보, 없으면 오류."""
        mode, segs = parse_object_path(path)
        if not segs: raise DomainError("이름 경로를 입력하세요.")
        if mode == "qpath": return self.resolve_qualifier_path(segs)
        wanted = [key(sg["text"]).strip() for sg in segs if sg["text"]]
        if not wanted: raise DomainError("이름 경로를 입력하세요.")
        entries = self._tree_name_paths()

        def norm(value):
            return "/".join(key(x).strip() for x in str(value).lstrip("/").split("/") if x.strip())

        if not any(sg["kind"] for sg in segs):
            wanted_text = "/".join(wanted)
            for exact in (True, False):
                hits = [k for k, v in entries.items() if (norm(v) == wanted_text if exact else ("/" + norm(v)).endswith("/" + wanted_text))]
                if len(hits) == 1:
                    return {"key": hits[0], "path": entries[hits[0]]}
                if len(hits) > 1:
                    return {"candidates": [{"key": k, "path": entries[k]} for k in hits]}
        # 트리 항목이 아니면 객체 이름 경로(object_name_path가 만든 '트리 항목 경로/객체 이름')로 찾는다. 마지막 조각의 선택자로 종류를 거른다.
        found = self._resolve_object_name_path(wanted, entries, segs[-1]["kind"])
        if found: return found
        raise DomainError("이름 경로와 일치하는 항목이 없습니다.")

    def _resolve_object_name_path(self, wanted, entries, selector=None):
        """object_name_path의 역. 마지막 부분을 객체 이름으로 보고 후보 객체의 이름 경로와 비교한다(정확히 일치 → 끝부분 일치).
        이름에 '/'가 있어도 되도록 정규화한 문자열로 비교한다. 목록 행이 아닌 구조 멤버·레코드는 대상에서 뺀다."""
        def norm(text):
            return "/".join(key(s).strip() for s in str(text).strip().strip("/").split("/") if s.strip())
        wanted_text = "/".join(wanted)
        kinds = {o["id"]: o["kind"] for o in self.r.all("SELECT id,kind FROM objects")}
        labels = {}
        for oid, name in self._names().items():
            if kinds.get(oid) in ("data_object", "structure", "snapshot_record", "extended_record"): continue
            if selector and kinds.get(oid) != selector: continue
            label = norm(name)
            if label and (wanted_text == label or wanted_text.endswith("/" + label)): labels[oid] = name
        if not labels: return None
        variants = [v["id"] for v in self.r.all("SELECT id FROM variants ORDER BY ordinal")] or [None]
        found = {}
        for oid, name in labels.items():
            for variant in variants:
                try: target = self.locate_object(oid, variant)
                except DomainError: continue
                base = entries.get(target["key"])
                if base is None: continue
                path = base if base.endswith("/" + name) else base + "/" + name
                # CANdela 경로처럼 목록 노드(DID·DTC) 조각을 빼고 선택자로 쓰는 입력도 받는다: /Base Variant/[DID]Speed
                short = None
                if target["key"].endswith((":did", ":dtc")) and entries.get(target["key"].rsplit(":", 1)[0]):
                    short = entries[target["key"].rsplit(":", 1)[0]] + "/" + name
                found.setdefault((target["key"], oid), {**target, "path": path, "_short": short})

        def hit(entry, exact):
            forms = [entry["path"]] + ([entry["_short"]] if entry.get("_short") else [])
            return any(norm(f) == wanted_text if exact else ("/" + norm(f)).endswith("/" + wanted_text) for f in forms)

        for exact in (True, False):
            hits = [{k: v for k, v in t.items() if k != "_short"} for t in found.values() if hit(t, exact)]
            if len(hits) == 1: return hits[0]
            if len(hits) > 1: return {"candidates": hits}
        return None

    def _qualifier_chains(self, oid, kind):
        """객체의 Qualifier 경로 후보: [(변형 ID 또는 None, [(Qualifier, 종류), ...])]. ECU 조각은 넣지 않는다(CANdela Object Paths)."""
        r = self.r
        q = lambda i: r.one("SELECT qualifier FROM objects WHERE id=?", (i,))["qualifier"]
        variants = r.all("SELECT v.id,o.qualifier FROM variants v JOIN objects o ON o.id=v.id ORDER BY v.ordinal")
        if kind == "variant": return [(oid, [(q(oid), "variant")])]
        if kind in ("did", "dtc"):
            table = "variant_dids" if kind == "did" else "variant_dtcs"
            return [(v["id"], [(v["qualifier"], "variant"), (q(oid), kind)]) for v in variants
                    if r.one(f"SELECT 1 FROM {table} WHERE variant_id=? AND {kind}_id=?", (v["id"], oid))]
        if kind == "diagnostic_class":
            return [(v["id"], [(v["qualifier"], "variant"), (q(oid), kind)]) for v in variants
                    if r.one("SELECT 1 FROM variant_classes WHERE variant_id=? AND class_id=?", (v["id"], oid))]
        if kind in ("diagnostic_instance", "service"):
            inst = oid if kind == "diagnostic_instance" else r.one("SELECT instance_id FROM services WHERE id=?", (oid,))["instance_id"]
            cls = r.one("SELECT class_id FROM diagnostic_instances WHERE id=?", (inst,))["class_id"]
            tail = [(q(cls), "diagnostic_class"), (q(inst), "diagnostic_instance")] + ([(q(oid), "service")] if kind == "service" else [])
            return [(v["id"], [(v["qualifier"], "variant")] + tail) for v in variants
                    if r.one("SELECT 1 FROM variant_instances WHERE variant_id=? AND instance_id=?", (v["id"], inst))]
        if kind == "job_container":
            return [(v["id"], [(v["qualifier"], "variant"), (q(oid), kind)]) for v in variants
                    if r.one("SELECT 1 FROM variant_jobs WHERE variant_id=? AND job_id=?", (v["id"], oid))]
        if kind == "state":
            group = r.one("SELECT group_id FROM states WHERE id=?", (oid,))["group_id"]
            return [(None, [(q(group), "state_group"), (q(oid), "state")])]
        if kind in ("state_group", "data_type", "nrc", "protocol_service", "ecu"): return [(None, [(q(oid), kind)])]
        return []

    @staticmethod
    def _qualifier_path_text(chain):
        return "qpath:/" + "/".join((f"[{PATH_SELECTOR[k]}]" if k in PATH_SELECTOR else "") + qual for qual, k in chain)

    def object_qualifier_path(self, oid, variant=None):
        """객체의 Qualifier 경로(qpath). 설정 '경로 표시 = Qualifier'에서 경로 복사에 쓴다."""
        obj = self.r.one("SELECT kind FROM objects WHERE id=?", (oid,))
        if not obj: raise DomainError("객체가 없습니다.")
        chains = self._qualifier_chains(oid, obj["kind"])
        if not chains: raise DomainError("이 객체는 Qualifier 경로가 없습니다.")
        chosen = next(((v, c) for v, c in chains if variant and v == variant), chains[0])
        return {"path": self._qualifier_path_text(chosen[1]), "id": oid, "variant": chosen[0]}

    def resolve_qualifier_path(self, segs):
        """qpath 해석(M3-2): 마지막 조각의 Qualifier로 후보를 찾고 각 후보의 Qualifier 경로와 비교한다(정확히 일치 → 끝부분 일치).
        선택자([DTC] 등)는 그 조각의 객체 종류와 맞아야 한다. 경로 앞의 ECU Qualifier는 있어도 된다."""
        r = self.r
        wanted = [(key(sg["text"]).strip(), sg["kind"]) for sg in segs if sg["text"]]
        if not wanted: raise DomainError("Qualifier 경로를 입력하세요.")
        ecu = r.one("SELECT o.qualifier FROM ecu e JOIN objects o ON o.id=e.id")
        forms = [wanted]
        if ecu and len(wanted) > 1 and wanted[0][0] == key(ecu["qualifier"]) and wanted[0][1] in (None, "ecu"): forms.append(wanted[1:])

        def matches(form, chain, exact):
            if exact and len(form) != len(chain): return False
            if len(form) > len(chain): return False
            tail = chain[-len(form):]
            return all(fq == key(cq) and (fk is None or fk == ck) for (fq, fk), (cq, ck) in zip(form, tail))

        last_q, last_kind = wanted[-1]
        candidates = r.all("SELECT id,kind FROM objects WHERE qualifier_key=?", (last_q,))
        for exact in (True, False):
            hits = {}
            for c in candidates:
                if last_kind and c["kind"] != last_kind: continue
                for variant, chain in self._qualifier_chains(c["id"], c["kind"]):
                    if any(matches(form, chain, exact) for form in forms):
                        try: target = self.locate_object(c["id"], variant)
                        except DomainError: continue
                        hits.setdefault((target["key"], c["id"]), {**target, "path": self._qualifier_path_text(chain)})
            if len(hits) == 1: return next(iter(hits.values()))
            if len(hits) > 1: return {"candidates": list(hits.values())}
        raise DomainError("Qualifier 경로와 일치하는 객체가 없습니다.")

    def object_name_path(self, oid, variant=None):
        """객체의 이름 경로. 그 객체가 놓인 트리 항목의 이름 경로 끝에 객체 이름을 붙인다."""
        if str(oid).startswith("unsupported:"):
            raise DomainError("보존 항목은 이름 경로가 없습니다.")
        names = self._names()
        target = self.locate_object(oid, variant)
        base = self._tree_name_paths().get(target["key"])
        if base is None:
            raise DomainError("이 객체의 이름 경로가 없습니다.")
        label = names.get(oid)
        if label and not base.endswith("/" + label):
            base = base + "/" + label
        return {"key": target["key"], "path": base, "id": oid, "variant": target.get("variant")}

    def _dependency_rows(self, variant, names):
        """상태 의존성 표: 행 = 변형의 서비스, 열 = 상태. 셀 = 허용(✓)·차단(✗)·전이(→ 대상), 그룹 조건이 없으면 제한 없음(—)."""
        r = self.r
        states = r.all("SELECT s.id,s.group_id,s.code FROM states s JOIN state_groups g ON g.id=s.group_id ORDER BY g.kind='security',s.code")
        columns = [("identifier_text", "ID"), ("name", "서비스"), ("instance", "인스턴스")]
        columns += [(f"st:{st['id']}", f"{names.get(st['group_id'], '')}: {names.get(st['id'], '')}") for st in states]
        info = self._instance_info(names)
        cond = {}
        for c in r.all("SELECT * FROM service_conditions"): cond[(c["service_id"], c["state_id"])] = c["allowed"]
        groups_of = {}
        for c in r.all("SELECT DISTINCT service_id,group_id FROM service_conditions"): groups_of.setdefault(c["service_id"], set()).add(c["group_id"])
        trans = {(t["service_id"], t["from_state_id"]): t["to_state_id"] for t in r.all("SELECT * FROM service_transitions")}
        # 개요 모드의 클래스별 묶음(U4): CANdela 'Sessions/*/Start'처럼 클래스와 템플릿 서비스(역할)로 묶는다.
        doc = self._source()
        roles = doc.service_roles() if doc else {}
        external = self._external_ids("service") if doc else {}
        rows = []
        for svc in r.all("SELECT s.*,o.qualifier,o.supported,o.read_only,vi.ordinal io,vc.ordinal co,di.class_id FROM services s JOIN objects o ON o.id=s.id "
                         "JOIN variant_instances vi ON vi.instance_id=s.instance_id AND vi.variant_id=? "
                         "JOIN diagnostic_instances di ON di.id=s.instance_id JOIN variant_classes vc ON vc.variant_id=vi.variant_id AND vc.class_id=di.class_id "
                         "ORDER BY vc.ordinal,vi.ordinal,o.qualifier", (variant,)):
            inst = info[svc["instance_id"]]
            ext = external.get(svc["id"])
            role = (roles[ext][0] if ext in roles else "") or ROLE_NAMES.get(svc["sid"], "") or f"0x{svc['sid']:02X}"
            class_name = names.get(svc["class_id"], "")
            row = {**svc, "kind": "service", "name": names.get(svc["id"], svc["qualifier"]), "instance": inst["name"], "identifier_text": inst["identifier_text"], "shared": inst["shared"], "ordinal": (svc["co"], svc["io"], svc["qualifier"]),
                   "role": role, "class_name": class_name, "group_key": f"{svc['class_id']}|{role}", "group_label": f"{class_name} / * / {role}",
                   # M6-7 'Show Service Addressing': P = physical, F = functional
                   "addressing": "P/F" if svc["physical"] and svc["functional"] else "P" if svc["physical"] else "F"}
            for st in states:
                if st["group_id"] not in groups_of.get(svc["id"], ()): value = "—"
                elif not cond.get((svc["id"], st["id"])): value = "✗"
                else:
                    target = trans.get((svc["id"], st["id"]))
                    value = f"✓ → {names.get(target, '')}" if target else "✓"
                row[f"st:{st['id']}"] = value
            rows.append(row)
        return rows, columns

    def _overview_rows(self, table, field, names):
        """DID·DTC 개요: 행 = 객체, 열 = 변형 포함 여부. 같은 객체를 참조하는 변형이 한 줄에 모인다."""
        r = self.r
        variants = r.all("SELECT id FROM variants ORDER BY ordinal")
        member = {(m["variant_id"], m[f"{field}_id"]) for m in r.all(f"SELECT * FROM variant_{table}")}
        key_field = "identifier" if field == "did" else "code"
        columns = [(key_field, "DID" if field == "did" else "DTC"), ("name", "이름"), ("qualifier", "Qualifier")]
        columns += [(f"v:{v['id']}", names.get(v["id"], "")) for v in variants]
        used, data_names = set(), {}
        if field == "did":
            # DID 개요의 Used·DID Data 열(2.1): 진단 인스턴스·고장 메모리 스냅샷이 참조하면 사용, DID 데이터는 데이터 객체 이름 목록.
            columns += [("used", "사용"), ("did_data", "DID 데이터")]
            used = {x["did_id"] for x in r.all("SELECT did_id FROM instance_dids")} | self._fault_memory_dids()
            data_names = self._did_data_names(names)
        rows = []
        for t in r.all(f"SELECT t.*,o.qualifier,o.supported,o.read_only FROM {table} t JOIN objects o ON o.id=t.id"):
            row = {**t, "kind": field, "name": names.get(t["id"], t["qualifier"])}
            for v in variants: row[f"v:{v['id']}"] = "예" if (v["id"], t["id"]) in member else "아니요"
            row["shared"] = sum(1 for v in variants if (v["id"], t["id"]) in member)
            if field == "did":
                row["used"] = t["id"] in used
                row["did_data"] = ", ".join(data_names.get(t.get("structure_id"), [])) or None
            rows.append(row)
        return rows, columns

    def locate_object(self, oid, variant=None):
        """Resolve a stored object to a visible tree node, retaining variant context."""
        r = self.r
        obj = r.one("SELECT id,kind FROM objects WHERE id=?", (oid,))
        if not obj: raise DomainError("이동할 객체가 없습니다.")
        variants = [v["id"] for v in r.all("SELECT id FROM variants ORDER BY ordinal")]
        if variant is not None and variant not in variants: raise DomainError("변형이 없습니다.")
        choices = [variant] if variant else variants
        kind = obj["kind"]
        if kind in ("did", "dtc"):
            table = "variant_dids" if kind == "did" else "variant_dtcs"
            vid = next((v for v in choices if r.one(f"SELECT 1 FROM {table} WHERE variant_id=? AND {kind}_id=?", (v, oid))), None)
            if vid is None: raise DomainError("객체를 포함하는 변형이 없습니다.")
            return {"key": f"var:{vid}:{kind}", "id": oid, "variant": vid}
        if kind in ("service", "diagnostic_instance", "diagnostic_class", "job_container"):
            if kind == "service":
                inst = r.one("SELECT instance_id FROM services WHERE id=?", (oid,))["instance_id"]
            elif kind == "diagnostic_instance": inst = oid
            else: inst = None
            if kind == "diagnostic_class":
                vid = next((v for v in choices if r.one("SELECT 1 FROM variant_classes WHERE variant_id=? AND class_id=?", (v, oid))), None)
                key = f"var:{vid}" if vid else None
                row_id = oid
            elif kind == "job_container":
                vid = next((v for v in choices if r.one("SELECT 1 FROM variant_jobs WHERE variant_id=? AND job_id=?", (v, oid))), None)
                key = f"var:{vid}:job:{oid}" if vid else None
                row_id = None
            else:
                vid = next((v for v in choices if r.one("SELECT 1 FROM variant_instances WHERE variant_id=? AND instance_id=?", (v, inst))), None)
                single = False  # 변형에 없는 인스턴스도 아래 row_id 계산에서 DomainError로 끝나야 한다.
                if kind == "diagnostic_instance" and vid:
                    cid = r.one("SELECT class_id FROM diagnostic_instances WHERE id=?", (inst,))["class_id"]
                    c = r.one("SELECT singleton FROM diagnostic_classes WHERE id=?", (cid,))
                    one = r.one("SELECT count(*) n FROM variant_instances vi JOIN diagnostic_instances i ON i.id=vi.instance_id WHERE vi.variant_id=? AND i.class_id=?", (vid, cid))
                    single = c["singleton"] and one["n"] == 1
                    key = f"var:{vid}:inst:{inst}" if single else f"var:{vid}:cls:{cid}"
                else: key = f"var:{vid}:inst:{inst}" if vid else None
                row_id = oid if kind == "service" else None if single else inst
            if not key: raise DomainError("객체를 포함하는 변형이 없습니다.")
            return {"key": key, "id": row_id, "details_id": oid, "variant": vid}
        if kind in ("snapshot_record", "extended_record"):
            # 고장 메모리 레코드 정의는 그 고장 메모리 인스턴스 화면에서 본다.
            inst = r.one(f"SELECT instance_id FROM {kind}s WHERE id=?", (oid,))["instance_id"]
            target = self.locate_object(inst, variant)
            return {**target, "details_id": inst, "member_id": oid}
        if kind in ("data_object", "structure"):
            # 데이터 객체·구조(비트필드, 반복 항목 등)는 그것을 담은 DID·서비스·packet 타입 화면으로 이동한다.
            owner = self._structure_owner(oid, kind)
            if owner is None: raise DomainError("이 객체로 이동할 화면이 없습니다.")
            target = self.locate_object(owner, variant)
            return {**target, "details_id": target.get("details_id", owner), "member_id": oid}
        static = {"ecu": "ecu", "variant": f"var:{oid}", "data_type": "types", "state": "states", "state_group": f"sg:{oid}", "nrc": "nrcs", "protocol_service": "psvc"}
        if kind not in static: raise DomainError("이 객체로 이동할 화면이 없습니다.")
        return {"key": static[kind], "id": oid if kind not in ("state_group", "variant") else None, "details_id": oid, "variant": variant}

    def _structure_owner(self, oid, kind):
        """데이터 객체·구조를 담은 최상위 소유자(DID, 서비스, packet 데이터 타입)의 ID. 없으면 None."""
        r = self.r
        if kind == "data_object":
            row = r.one("SELECT structure_id FROM structure_members WHERE data_object_id=?", (oid,))
            if not row: return None
            structure = row["structure_id"]
        else: structure = oid
        for _ in range(130):
            did = r.one("SELECT id FROM dids WHERE structure_id=?", (structure,))
            if did: return did["id"]
            tel = r.one("SELECT service_id FROM telegrams WHERE structure_id=?", (structure,))
            if tel: return tel["service_id"]
            typ = r.one("SELECT id FROM data_types WHERE structure_id=?", (structure,))
            if typ: return typ["id"]
            record = r.one("SELECT id FROM extended_records WHERE structure_id=?", (structure,))
            if record: return record["id"]
            parent = r.one("SELECT structure_id FROM structure_members WHERE nested_structure_id=?", (structure,))
            if not parent: return None
            structure = parent["structure_id"]
        return None

    def object_path(self, oid, variant=None, names=None):
        target = self.locate_object(oid, variant)
        project = self.r.one("SELECT project_uuid FROM project_meta")["project_uuid"]
        vid = target["variant"] or "_"
        url = f"diag://{project}/{vid}/object/{oid}"
        names = names if names is not None else self._names()  # 개요는 이름 표를 한 번만 읽어 넘긴다.
        owner = target.get("details_id") if target.get("member_id") else None  # 구조 멤버는 소유 DID·서비스 이름을 넣는다
        breadcrumb = " / ".join(x for x in (names.get(vid) if vid != "_" else None, names.get(owner) if owner else None, names.get(oid)) if x)
        return {**target, "path": url, "breadcrumb": breadcrumb}

    def node_path(self, node_key):
        # 트리 항목 키는 이름 경로 표(_path_map)와 같은 집합이다(중간 노드·원문 노드 포함).
        if node_key not in self._tree_name_paths(): raise DomainError("경로의 탐색 항목이 없습니다.")
        project = self.r.one("SELECT project_uuid FROM project_meta")["project_uuid"]
        return {"key": node_key, "path": f"diag://{project}/_/node/{quote(node_key, safe='')}"}

    def resolve_path(self, path):
        parsed = urlsplit(path.strip())
        parts = [unquote(x) for x in parsed.path.strip("/").split("/")]
        project = self.r.one("SELECT project_uuid FROM project_meta")["project_uuid"]
        if parsed.scheme != "diag" or parsed.netloc != project or len(parts) != 3 or parts[1] not in ("object", "node"):
            raise DomainError("이 프로젝트의 올바른 객체 경로를 입력하세요.")
        if parts[1] == "node": return self.node_path(parts[2])
        return self.locate_object(parts[2], None if parts[0] == "_" else parts[0])

    @staticmethod
    def _hex_bytes(text, minimum=2):
        tokens = text.split()
        if len(tokens) < minimum or not all(re.fullmatch(r"[0-9a-fA-F]{2}", token) for token in tokens):
            return None
        return bytes(int(token, 16) for token in tokens)

    def _service_request_bytes(self, s, service_id):
        # 요청 바이트 검색은 고정 배치(리터럴)만 있는 서비스에만 적용한다. 사용자 값이 필요한 자리(데이터·예약)가
        # 하나라도 있으면 값을 알 수 없으므로 그 서비스는 바이트 검색 대상에서 제외한다.
        structure = None
        for telegram in s.rows("telegrams"):
            if telegram["service_id"] == service_id and telegram["kind"] == "request":
                structure = telegram["structure_id"]; break
        if structure is None: return None
        data = bytearray()
        for row in self.structure_layout(structure, s):
            if row["kind"] != "literal": return None
            data.extend(bytes.fromhex(row["value"]))
        return bytes(data)

    def _find_row(self, oid, names, entries):
        obj = self.r.one("SELECT kind,qualifier FROM objects WHERE id=?", (oid,))
        name = names.get(oid, obj["qualifier"])
        path, variant, node = "", None, None
        try:
            target = self.locate_object(oid)
            variant, node = target.get("variant"), target["key"]
            base = entries.get(target["key"])
            if base: path = base if base.endswith("/" + name) else base + "/" + name
        except DomainError:
            path = ""
        return {"id": oid, "kind": obj["kind"], "name": name, "qualifier": obj["qualifier"], "path": path, "variant": variant, "key": node}

    FIND_SCOPES = ("all", "name", "qualifier", "bytes")

    def find_document(self, query, cancel=lambda: None, scope="all", case=False, word=False, whole=False):
        """문서 전체 찾기(Search Dialog, M3-1). scope: all = 이름·Qualifier·설명·식별자·DTC·서비스 shortcut·NRC,
        name = 이름(모든 언어), qualifier = Qualifier, bytes = 고정 요청 바이트('22 10 03', 한 바이트도 됨).
        all에서 두 바이트 이상의 16진 토큰은 요청 바이트로 찾는다(예전 동작). case = 대소문자 구분, word = 단어 단위, whole = 칸 전체 일치.
        결과는 문서 트리 순서(트리 항목 순서 → 그 안의 이름순)다."""
        text = str(query or "").strip()
        if not text:
            raise DomainError("검색어를 입력하세요.")
        if scope not in self.FIND_SCOPES: raise DomainError("찾을 범위가 올바르지 않습니다.")
        names = self._names()
        tree = self.tree()
        entries = tree["paths"]
        needle = self._hex_bytes(text) if scope == "all" else self._hex_bytes(text, 1) if scope == "bytes" else None
        if scope == "bytes" and needle is None: raise DomainError("요청 바이트는 공백으로 나눈 두 자리 16진수로 넣으세요(예: 22 F1 90).")
        rows, seen = [], set()
        if needle is None:
            fold = (lambda value: unicodedata.normalize("NFC", str(value))) if case else key
            term = fold(text)
            pattern = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)") if word and not whole else None

            def match(value):
                value = fold(value)
                if whole: return value == term
                return bool(pattern.search(value)) if pattern else term in value
            fields = {}
            wanted = ("'name'",) if scope == "name" else ("'name'", "'description'") if scope == "all" else ()
            if wanted:
                for item in self.r.all(f"SELECT object_id,value FROM localized_texts WHERE field IN ({','.join(wanted)})"):
                    fields.setdefault(item["object_id"], []).append(item["value"])
            if scope == "all":
                for did in self.r.all("SELECT id,identifier FROM dids"): fields.setdefault(did["id"], []).append(f"0x{did['identifier']:04X}")
                for dtc in self.r.all("SELECT id,code FROM dtcs"): fields.setdefault(dtc["id"], []).append(f"0x{dtc['code']:06X}")
                for nrc in self.r.all("SELECT id,code FROM nrcs"): fields.setdefault(nrc["id"], []).append(f"0x{nrc['code']:02X}")
                for a in self.r.all("SELECT object_id,value_text FROM object_attributes WHERE key='cdd.shortcut_qualifier'"): fields.setdefault(a["object_id"], []).append(a["value_text"])
            for obj in self.r.all("SELECT id,qualifier FROM objects"):
                cancel()
                values = fields.get(obj["id"], []) + ([obj["qualifier"]] if scope in ("all", "qualifier") else [])
                if obj["id"] not in seen and any(match(v) for v in values if v):
                    seen.add(obj["id"]); rows.append(self._find_row(obj["id"], names, entries))
        else:
            payload = bytes(needle)
            s = self.r.snapshot(self.locale)
            for service in s.rows("services"):
                cancel()
                data = self._service_request_bytes(s, service["id"])
                if data and payload in data and service["id"] not in seen:
                    seen.add(service["id"]); rows.append(self._find_row(service["id"], names, entries))
        rank = {k: i for i, k in enumerate(self._tree_order(tree))}
        rows.sort(key=lambda row: (rank.get(row.get("key"), len(rank)), key(row["name"]), row["id"]))
        return {"query": text, "rows": rows, "byte_query": needle is not None, "revision": self.r.revision,
                "scope": scope, "case": bool(case), "word": bool(word), "whole": bool(whole)}

    @staticmethod
    def _tree_order(tree):
        """화면 트리(ui.build_tree)와 같은 항목 순서. 찾기 결과를 문서 트리 순서로 늘어놓을 때 쓴다."""
        keys = ["ecu"]
        if tree.get("has_interfaces"):
            keys.append("ifaces"); keys += [f"iface:{i['qualifier']}" for i in tree.get("interfaces", [])]
        keys += ["fm", "dtc_ov", "did_ov"]
        variants = sorted(tree["variants"], key=lambda v: not v["is_base"])
        for n, v in enumerate(variants):
            if n == 1: keys.append("variants")
            prefix = f"var:{v['id']}"
            keys += [prefix, prefix + ":ident", prefix + ":did", prefix + ":dtc", prefix + ":classes"]
            for c in v["classes"]:
                keys.append(f"{prefix}:cls:{c['id']}")
                keys += [f"{prefix}:inst:{i['id']}" for i in c["instances"]]
            keys += [f"{prefix}:job:{j['id']}" for j in v.get("jobs", [])]
            keys.append(prefix + ":svc")
        keys += ["states", "sgroups"] + [f"sg:{g['id']}" for g in tree.get("state_groups", [])]
        keys += ["dep", "sdefaults", "types", "psvc", "nrcs", "nrcs_unsupported", "unsupported"]
        return keys

    def qualifier_overview(self, shortcut=False, scope_variant=None):
        """Qualifier 개요. 편집(M6-5)용으로 행마다 편집 가능 여부, 하위 객체 여부(데이터 객체·구조·상태),
        이름에서 만든 Qualifier(make_qualifier)와 같은지(from_name)를 싣는다. scope_variant면 그 변형의 객체만(현재 namespace)."""
        names = self._names()
        rows = []
        if shortcut:
            source = self.r.all("SELECT s.id,a.value_text shortcut,o.qualifier FROM services s JOIN objects o ON o.id=s.id JOIN object_attributes a ON a.object_id=s.id AND a.key='cdd.shortcut_qualifier'")
            for x in source:
                for v in self.r.all("SELECT vi.variant_id FROM variant_instances vi WHERE vi.instance_id=(SELECT instance_id FROM services WHERE id=?)", (x["id"],)):
                    target = self.object_path(x["id"], v["variant_id"], names)
                    rows.append({"id": x["id"], "variant": v["variant_id"], "kind": "service", "name": names[x["id"]], "qualifier": x["shortcut"], "path": target["path"], "breadcrumb": target["breadcrumb"]})
        else:
            variants=[v["id"] for v in self.r.all("SELECT id FROM variants ORDER BY ordinal")]
            if scope_variant and scope_variant not in variants: raise DomainError("변형이 없습니다.")
            variant_kinds={"did","dtc","diagnostic_class","diagnostic_instance","service","job_container"}
            idents = {d["id"]: d["identifier"] for d in self.r.all("SELECT id,identifier FROM dids")}
            idents.update({d["id"]: d["code"] for d in self.r.all("SELECT id,code FROM dtcs")})
            for obj in self.r.all("SELECT id,kind,qualifier,supported,read_only FROM objects ORDER BY kind,qualifier"):
                if scope_variant and obj["kind"] not in variant_kinds and obj["id"] != scope_variant: continue
                choices = ([scope_variant] if scope_variant else variants) if obj["kind"] in variant_kinds else [None]
                for vid in choices:
                    try:target = self.object_path(obj["id"],vid,names)
                    except DomainError:continue
                    if scope_variant and obj["kind"] in variant_kinds and target["variant"] != scope_variant: continue
                    generated = make_qualifier(names[obj["id"]], obj["kind"], idents.get(obj["id"]))
                    rows.append({"id": obj["id"], "variant": target["variant"], "kind": obj["kind"], "name": names[obj["id"]], "qualifier": obj["qualifier"],
                                 "path": target["path"], "breadcrumb": target["breadcrumb"], "ident": idents.get(obj["id"]),
                                 "editable": bool(obj["supported"] and not obj["read_only"] and obj["kind"] not in ("nrc", "protocol_service")),
                                 "subordinate": obj["kind"] in ("data_object", "structure", "state"), "generated": generated,
                                 "from_name": generated == obj["qualifier"]})
        return {"rows": rows, "revision": self.r.revision, "shortcut": shortcut, "scope_variant": scope_variant}

    def property_sheet(self, ids):
        """선택 객체 창의 속성 시트(M2-2). 선택한 객체들의 공통 속성과 값이다. 값이 다르면 multiple=True(화면은 '(여러 값)').
        이름·설명은 편집 가능한 객체 모두, Qualifier·DTC 코드는 하나만 골랐을 때, 레코드 항목은 모두 DTC일 때 편집한다.
        편집은 기존 명령을 쓴다: bulk_edit(이름·설명), update_common(Qualifier), update_dtc(코드), set_dtc_attributes, set_attributes(user.*)."""
        r = self.r
        ids = [i for i in dict.fromkeys(ids or []) if isinstance(i, str) and not i.startswith(("unsupported:", "src:", "template:", "group:"))]
        objects = []
        for oid in ids:
            o = r.one("SELECT id,kind,qualifier,supported,read_only FROM objects WHERE id=?", (oid,))
            if o: objects.append(o)
        if not objects: return {"ids": [], "rows": [], "count": 0}
        names, descriptions = self._names(), self._descriptions()
        editable = self.session is not None and not self.session.readonly and all(o["supported"] and not o["read_only"] and o["kind"] not in ("nrc", "protocol_service") for o in objects)
        kinds = [o["kind"] for o in objects]

        def row(key_, label, values, can_edit, **extra):
            values = list(values)
            first = values[0] if values else ""
            multiple = any(v != first for v in values)
            return {"key": key_, "label": label, "value": "" if multiple else first, "multiple": multiple, "editable": bool(can_edit), **extra}

        labels = {"did": "DID", "dtc": "DTC", "service": "서비스", "data_type": "데이터 타입", "variant": "변형", "state": "상태", "ecu": "ECU",
                  "diagnostic_class": "진단 클래스", "diagnostic_instance": "진단 인스턴스", "state_group": "상태 그룹", "job_container": "Job 컨테이너"}
        rows = [row("kind", "종류", (labels.get(k, k) for k in kinds), False),
                row("name", "이름", (names.get(o["id"], o["qualifier"]) for o in objects), editable),
                row("description", "설명", (descriptions.get(o["id"], "") for o in objects), editable, multiline=True),
                row("qualifier", "Qualifier", (o["qualifier"] for o in objects), editable and len(objects) == 1)]
        if all(k == "dtc" for k in kinds):
            codes = {d["id"]: d["code"] for d in r.all(f"SELECT id,code FROM dtcs WHERE id IN ({','.join('?' for _ in objects)})", [o["id"] for o in objects])}
            rows.insert(1, row("code", "DTC 코드", (f"0x{codes.get(o['id'], 0):06X}" for o in objects), editable and len(objects) == 1))
            attrs = {}
            for a in r.all(f"SELECT object_id,key,value_text FROM object_attributes WHERE object_id IN ({','.join('?' for _ in objects)})", [o["id"] for o in objects]):
                attrs.setdefault(a["object_id"], {})[a["key"]] = a["value_text"]
            for item in self._dtc_record_items():
                k = item["key"]
                if item["kind"] == "enum":
                    raws = []
                    for o in objects:
                        raw = attrs.get(o["id"], {}).get(k + "#raw")
                        try: raws.append(None if raw is None else integer(raw))
                        except DomainError: raws.append(raw)
                    entry = row("attr:" + k, item["name"], raws, editable, kind="enum", required=item["required"],
                                choices=[{"raw": c["raw"], "label": c["label"]} for c in item["choices"]])
                    entry["text"] = "" if entry["multiple"] else attrs.get(objects[0]["id"], {}).get(k, "")
                else:
                    entry = row("attr:" + k, item["name"], (attrs.get(o["id"], {}).get(k, "") for o in objects), editable, kind=item["kind"], required=item["required"])
                rows.append(entry)
        user_keys = sorted({a["key"] for a in r.all(f"SELECT key FROM object_attributes WHERE key LIKE 'user.%' AND object_id IN ({','.join('?' for _ in objects)})", [o["id"] for o in objects])})
        for k in user_keys:
            values = [r.one("SELECT value_text FROM object_attributes WHERE object_id=? AND key=?", (o["id"], k)) for o in objects]
            rows.append(row("user:" + k, k[5:], ((v or {}).get("value_text", "") for v in values), editable, kind="user"))
        return {"ids": [o["id"] for o in objects], "count": len(objects), "kinds": sorted(set(kinds)), "editable": editable, "rows": rows, "revision": r.revision}

    def related_targets(self, oid, kind):
        if not self.r.one("SELECT 1 FROM objects WHERE id=?", (oid,)): raise DomainError("객체가 없습니다.")
        source_kind = self.r.one("SELECT kind FROM objects WHERE id=?", (oid,))["kind"]
        if kind == "did":
            if source_kind == "service":
                inst = self.r.one("SELECT instance_id FROM services WHERE id=?", (oid,))["instance_id"]
            elif source_kind == "diagnostic_instance":inst=oid
            else:inst=None
            if source_kind == "data_type":
                # 타입을 쓰는 DID: 참조 그래프의 역방향(데이터 객체 → 구조 → DID)을 그대로 쓴다.
                ids = [row["id"] for row in build_reference_impact(self.r.snapshot(self.locale), oid) if row["kind"] == "did"]
            elif inst:
                ids = [row["did_id"] for row in self.r.all("SELECT did_id FROM instance_dids WHERE instance_id=? ORDER BY did_id", (inst,))]
            else: ids = []
        elif kind == "service":
            # NRC를 쓰는 서비스: service_nrcs.code ↔ nrcs.code.
            if source_kind == "nrc":
                code = self.r.one("SELECT code FROM nrcs WHERE id=?", (oid,))["code"]
                ids = [row["service_id"] for row in self.r.all("SELECT DISTINCT service_id FROM service_nrcs WHERE code=? ORDER BY service_id", (code,))]
            else: ids = []
        elif kind == "data_type":
            did = self.r.one("SELECT structure_id FROM dids WHERE id=?", (oid,))
            ids = [row["data_type_id"] for row in self.r.all("SELECT d.data_type_id FROM structure_members m JOIN data_objects d ON d.id=m.data_object_id WHERE m.structure_id=? AND d.data_type_id IS NOT NULL ORDER BY m.ordinal", (did["structure_id"],))] if did else []
        else: raise DomainError("지원하지 않는 관련 객체 종류입니다.")
        names = self._names()
        return [{"id": item, "name": names[item], "qualifier": self.r.one("SELECT qualifier FROM objects WHERE id=?", (item,))["qualifier"]} for item in dict.fromkeys(ids)]

    def attribute_overview(self, scope_variant=None):
        names = self._names()
        if scope_variant and not self.r.one("SELECT 1 FROM variants WHERE id=?", (scope_variant,)):raise DomainError("변형이 없습니다.")
        rows = []
        for a in self.r.all("SELECT a.object_id,a.key,a.value_type,a.value_text,o.kind,o.qualifier FROM object_attributes a JOIN objects o ON o.id=a.object_id WHERE a.key NOT LIKE 'app.%' ORDER BY o.kind,o.qualifier,a.key"):  # app.*(Rework 등 앱 내부 값)은 제외
            if scope_variant and a["kind"] not in ("variant","did","dtc","diagnostic_class","diagnostic_instance","service","job_container"):continue
            if scope_variant and a["kind"] == "variant" and a["object_id"] != scope_variant:continue
            try:path = self.object_path(a["object_id"],scope_variant,names)["path"]
            except DomainError:
                if scope_variant:continue
                path = ""
            rows.append({**a, "id": a["object_id"], "name": names[a["object_id"]], "path": path})
        return {"rows": rows, "revision": self.r.revision, "scope_variant":scope_variant}

    def compact_view(self, left, right):
        if left == right: raise DomainError("서로 다른 변형 두 개를 선택하세요.")
        variants = {v["id"]: v for v in self.r.all("SELECT id FROM variants")}
        if left not in variants or right not in variants: raise DomainError("변형이 없습니다.")
        names = self._names()
        membership = {}
        for row in self.r.all("SELECT vi.variant_id,vi.instance_id,i.owner_variant_id,i.class_id,o.qualifier FROM variant_instances vi JOIN diagnostic_instances i ON i.id=vi.instance_id JOIN objects o ON o.id=i.id WHERE vi.variant_id IN (?,?)", (left,right)):
            membership.setdefault(row["instance_id"], {})[row["variant_id"]] = row
        rows = []
        for oid, presence in membership.items():
            item = next(iter(presence.values()))
            owner = item["owner_variant_id"]
            def state(vid):
                if vid not in presence: return "없음"
                return "원본" if owner == vid else "참조"
            a, b = state(left), state(right)
            if a == "참조" and b == "참조": relation = "제3 변형 원본 참조"
            elif a == "없음" or b == "없음": relation = "한쪽만 포함"
            else: relation = "같은 객체 공유"
            rows.append({"id": oid, "kind": "diagnostic_instance", "name": names[oid], "qualifier": item["qualifier"], "class": names[item["class_id"]], "left": a, "right": b, "relation": relation,
                         "variant": left if left in presence else right})
        rows.sort(key=lambda x: (x["class"].casefold(), x["name"].casefold(), x["id"]))
        return {"rows": rows, "left": names[left], "right": names[right], "revision": self.r.revision}

    def compact_tree(self, variant):
        """Compact View(U4): 변형 하나에서 기본 변형과 공유하지 않는 인스턴스만 클래스별 트리로 보인다(인스턴스 share 데이터).
        기본 변형을 고르면 확장 변형과 공유하지 않는(기본 변형에만 있는) 인스턴스를 보인다."""
        r = self.r
        if not r.one("SELECT 1 FROM variants WHERE id=?", (variant,)): raise DomainError("변형이 없습니다.")
        base = r.one("SELECT id FROM variants WHERE is_base=1")["id"]
        names = self._names()
        info = self._instance_info(names)
        members = {}
        for vi in r.all("SELECT variant_id,instance_id FROM variant_instances"): members.setdefault(vi["instance_id"], set()).add(vi["variant_id"])
        classes, index = [], {}
        for x in r.all("SELECT vi.instance_id,i.class_id FROM variant_instances vi JOIN diagnostic_instances i ON i.id=vi.instance_id "
                       "JOIN variant_classes vc ON vc.variant_id=vi.variant_id AND vc.class_id=i.class_id WHERE vi.variant_id=? ORDER BY vc.ordinal,vi.ordinal", (variant,)):
            used = members.get(x["instance_id"], set())
            if (variant != base and base in used) or (variant == base and len(used) > 1): continue
            cls = index.get(x["class_id"])
            if cls is None:
                cls = index[x["class_id"]] = {"id": x["class_id"], "name": names.get(x["class_id"], ""), "instances": []}
                classes.append(cls)
            item = info[x["instance_id"]]
            cls["instances"].append({"id": item["id"], "name": item["name"], "identifier_text": item["identifier_text"],
                                     "share_text": SHARE_TEXT[item["share"]], "services": item["services"], "variant": variant})
        return {"variant": variant, "name": names.get(variant, ""), "is_base": variant == base, "classes": classes,
                "count": sum(len(c["instances"]) for c in classes), "revision": r.revision}

    def shortcut_matrix(self):
        """Shortcut Qualifier 개요 행렬(U4): 행 = 명시된 Shortcut Qualifier(서비스), 열 = 변형 포함 여부.
        변형 부분집합 선택과 '모든 선택 변형에 있는 행 숨기기'는 화면에서 variants 목록으로 계산한다."""
        r = self.r
        names = self._names()
        entries = self._tree_name_paths()
        variants = [{"id": v["id"], "name": names.get(v["id"], "")} for v in r.all("SELECT id FROM variants ORDER BY ordinal")]
        membership = {}
        for x in r.all("SELECT s.id,vi.variant_id FROM services s JOIN variant_instances vi ON vi.instance_id=s.instance_id"):
            membership.setdefault(x["id"], set()).add(x["variant_id"])
        rows = []
        for x in r.all("SELECT s.id,a.value_text shortcut,o.qualifier FROM services s JOIN objects o ON o.id=s.id "
                       "JOIN object_attributes a ON a.object_id=s.id AND a.key='cdd.shortcut_qualifier' ORDER BY a.value_text,s.id"):
            used = [v["id"] for v in variants if v["id"] in membership.get(x["id"], set())]
            name = names.get(x["id"], x["qualifier"])
            path = ""
            if used:
                try:
                    target = self.locate_object(x["id"], used[0])
                    base = entries.get(target["key"], "")
                    path = base if not base or base.endswith("/" + name) else base + "/" + name
                except DomainError:
                    path = ""
            rows.append({"id": x["id"], "kind": "service", "qualifier": x["shortcut"], "name": name, "variants": used, "path": path})
        return {"rows": rows, "variants": variants, "revision": r.revision}

    def revision_history(self):
        """개정 이력(U6, 읽기 전용): 원본 CDD의 AUTHORS·HISTITEMS 보존 원문."""
        doc = self._source()
        if doc is None: return {"authors": [], "history": [], "note": "원본 CDD가 없는 프로젝트입니다. 개정 이력은 가져온 CDD의 보존 원문에서 만듭니다."}
        data = doc.revision_history()
        data["note"] = f"{doc.basename} · 보존 원문(읽기 전용). 상태는 원본 stid 값입니다."
        return data

    def fault_memory_view(self, oid, variant=None):
        """고장 메모리 인스턴스의 읽기 전용 탭(U2): DtcStatusMask 비트, DTC 표(변형 DTC), 스냅샷 레코드·DID, 확장 데이터 레코드 배치.
        편집은 DTCSTATUSMASK 의미 검토 뒤로 미룬다."""
        r = self.r
        s = r.snapshot(self.locale)
        inst = s.get("diagnostic_instances", oid)
        if s.get("diagnostic_classes", inst["class_id"])["role"] != "fault_memory":
            raise DomainError("고장 메모리 인스턴스가 아닙니다. 고장 메모리 클래스(예: Fault Memory)의 인스턴스를 여세요.")
        doc = self._source()
        variants = [v["variant_id"] for v in r.all("SELECT vi.variant_id FROM variant_instances vi JOIN variants v ON v.id=vi.variant_id WHERE vi.instance_id=? ORDER BY v.ordinal", (oid,))]
        vid = variant if variant in variants else (variants[0] if variants else None)
        dtcs = []
        if vid:
            for d in r.all("SELECT t.id,t.code,o.qualifier FROM variant_dtcs v JOIN dtcs t ON t.id=v.dtc_id JOIN objects o ON o.id=t.id WHERE v.variant_id=? ORDER BY t.code", (vid,)):
                dtcs.append({"id": d["id"], "kind": "dtc", "code": d["code"], "name": s.name(d["id"]), "qualifier": d["qualifier"]})
        dtc_columns = self._dtc_columns(dtcs) if dtcs else [("code", "DTC"), ("name", "이름")]
        q = lambda i: s.get("objects", i)["qualifier"]
        snapshots = [{"id": x["id"], "number": x["number"], "name": s.name(x["id"]), "qualifier": q(x["id"])}
                     for x in sorted((x for x in s.rows("snapshot_records") if x["instance_id"] == oid), key=lambda x: x["number"])]
        did = lambda i: s.get("dids", i)
        defaults = [{"ordinal": x["ordinal"], "id": x["did_id"], "identifier": did(x["did_id"])["identifier"], "name": s.name(x["did_id"])}
                    for x in sorted((x for x in s.rows("snapshot_default_dids") if x["instance_id"] == oid), key=lambda x: x["ordinal"])]
        per_dtc = [{"code": s.get("dtcs", x["dtc_id"])["code"], "dtc": s.name(x["dtc_id"]), "ordinal": x["ordinal"], "id": x["did_id"],
                    "identifier": did(x["did_id"])["identifier"], "name": s.name(x["did_id"])}
                   for x in sorted((x for x in s.rows("snapshot_dtc_dids") if x["instance_id"] == oid), key=lambda x: (s.get("dtcs", x["dtc_id"])["code"], x["ordinal"]))]
        extended = []
        for x in sorted((x for x in s.rows("extended_records") if x["instance_id"] == oid), key=lambda x: x["number"]):
            layout = self.structure_layout(x["structure_id"], s)
            bits = sum(row["bits"] for row in layout if not row.get("depth") and row["kind"] != "error")
            extended.append({"id": x["id"], "number": x["number"], "name": s.name(x["id"]), "qualifier": q(x["id"]),
                             "size_text": f"{bits // 8}바이트" if bits % 8 == 0 else f"{bits}비트", "layout": layout})
        return {"id": oid, "name": s.name(oid), "variant": s.name(vid) if vid else "", "status_bits": doc.dtc_status_mask() if doc else [],
                "has_source": doc is not None, "dtcs": dtcs, "dtc_columns": dtc_columns, "snapshots": snapshots, "snapshot_dids": defaults,
                "snapshot_dtc_dids": per_dtc, "extended": extended, "revision": r.revision}

    def compare_variants(self, left, right):
        compact = self.compact_view(left, right)
        rows = [{**row, "category": "인스턴스"} for row in compact["rows"] if row["relation"] != "같은 객체 공유"]
        names = self._names()
        snap = None
        for kind, table, value_col in (("did","dids","identifier"),("dtc","dtcs","code")):
            group = {}
            for vid in (left,right):
                for row in self.r.all(f"SELECT t.id,t.{value_col},o.qualifier FROM variant_{table} v JOIN {table} t ON t.id=v.{kind}_id JOIN objects o ON o.id=t.id WHERE v.variant_id=?", (vid,)):
                    group.setdefault(row[value_col], {})[vid] = row
            for code, pair in group.items():
                a,b = pair.get(left),pair.get(right)
                if a and b and a["id"] == b["id"]: continue
                item = a or b
                if a and b:
                    # 코드가 같은 별개 객체(사본): 파일 비교와 같은 정의 서명으로 내용 차이를 구분한다.
                    snap = snap or self.r.snapshot(self.locale)
                    sa, sb = definition_signature(snap, kind, a["id"]), definition_signature(snap, kind, b["id"])
                    changed = [field for field in sa if sa[field] != sb[field]]
                    relation = "사본, 정의 다름: " + ", ".join(changed) if changed else "사본, 정의 같음"
                else: relation = "한쪽만 포함"
                rows.append({"id": item["id"], "variant": left if a else right, "category": kind.upper(), "code": f"0x{code:X}", "name": names[item["id"]], "qualifier": item["qualifier"],
                             "left": names[a["id"]] if a else "없음", "right": names[b["id"]] if b else "없음", "relation": relation})
        return {"rows": rows, "left": compact["left"], "right": compact["right"], "revision": self.r.revision}

    def compare_file_variants(self, path):
        project = saved_project(path)
        return {key: project[key] for key in ("variants", "digest", "project_uuid", "path")}

    def compare_project_file(self, path, left_variant, right_variant, expected_digest, cancel=lambda: None):
        left = self.r.snapshot(self.locale)
        project = saved_project(path, expected_digest)
        right = project["snapshot"]
        rows = compare_snapshots(left, left_variant, right, right_variant, cancel)
        return {"rows": rows, "revision": left.revision,
                "left": left.name(left_variant), "right": right.name(right_variant),
                "path": project["path"], "project_uuid": project["project_uuid"]}

    def compare_view(self, left_variant, right_variant, path=None, expected_digest=None, cancel=lambda: None):
        """비교 보기(M6-6): 작업본의 left_variant와, path가 없으면 작업본의 right_variant(두 변형 비교), 있으면 저장된 파일의 right_variant를 비교한다.
        결과는 차이 트리(comparison.compare_tree)와 양쪽 정보([CMP-R070]: 파일·revision·경로)다. 저장된 파일은 바꾸지 않는다(읽기 전용으로만 연다)."""
        r = self.r
        left = r.snapshot(self.locale)
        project_path = self.session.meta.get("source") if self.session else None
        left_info = {"source": "작업본", "project": left.name(r.one("SELECT id FROM ecu")["id"]), "path": project_path or "저장 전",
                     "revision": left.revision, "variant": left.name(left_variant) if left_variant in left.indices.get("variants", {}) else ""}
        if path:
            project = saved_project(path, expected_digest)
            right = project["snapshot"]
            right.locale = left.locale  # 같은 화면 언어로 이름을 고른다(파일의 기본 언어가 달라도 가짜 차이가 나지 않게)
            ecu = next((v["ecu_id"] for v in right.rows("variants")), None)
            right_info = {"source": "저장된 파일", "project": right.name(ecu) if ecu else "", "path": project["path"], "revision": right.revision,
                          "variant": right.name(right_variant) if right_variant in right.indices.get("variants", {}) else "", "digest": project["digest"]}
        else:
            if left_variant == right_variant: raise DomainError("서로 다른 두 변형을 고르세요.")
            right = left
            right_info = {**left_info, "variant": left.name(right_variant) if right_variant in left.indices.get("variants", {}) else ""}
        nodes = compare_tree(left, left_variant, right, right_variant, cancel)
        return {"nodes": nodes, "counts": tree_counts(nodes), "left": left_info, "right": right_info, "same_project": not path,
                "left_variant": left_variant, "right_variant": right_variant, "revision": r.revision, "locale": self.locale}

    def state_graph(self, variant=None):
        """상태 그래프(M6-7): 상태 그룹·상태와 서비스 실행 뒤 전이. variant를 주면 그 변형이 쓰는 서비스만 본다([STUI-R200]), 없으면 모든 서비스.
        상태마다 그 상태에서 실행할 수 있는 서비스(그 그룹에 조건이 있고 이 상태를 허용), 그 그룹에 조건이 없어 어느 상태에서나 실행하는 서비스 수,
        전이마다 서비스 이름과 요청 바이트(고정 부분, 예: `10 01`)를 싣는다. 표시 전용이다."""
        r = self.r
        names = self._names()
        if variant is not None and not r.one("SELECT 1 FROM variants WHERE id=?", (variant,)): raise DomainError("변형이 없습니다.")
        if variant:
            services = {x["id"] for x in r.all("SELECT s.id FROM services s JOIN variant_instances vi ON vi.instance_id=s.instance_id WHERE vi.variant_id=?", (variant,))}
        else:
            services = {x["id"] for x in r.all("SELECT id FROM services")}
        groups_of, allowed_in = {}, {}
        for c in r.all("SELECT service_id,group_id,state_id,allowed FROM service_conditions"):
            if c["service_id"] not in services: continue
            groups_of.setdefault(c["service_id"], set()).add(c["group_id"])
            if c["allowed"]: allowed_in.setdefault(c["state_id"], []).append(c["service_id"])
        groups = []
        for group in r.all("SELECT id,kind,initial_state_id FROM state_groups ORDER BY kind"):
            free = sum(1 for sid in services if group["id"] not in groups_of.get(sid, ()))
            states = [{"id": x["id"], "name": names[x["id"]], "initial": x["id"] == group["initial_state_id"], "code": x["code"],
                       "services": sorted((names.get(sid, "") for sid in allowed_in.get(x["id"], [])), key=key), "unrestricted": free}
                      for x in r.all("SELECT id,code FROM states WHERE group_id=? ORDER BY code", (group["id"],))]
            groups.append({"id": group["id"], "name": names[group["id"]], "states": states})
        transitions = [x for x in r.all("SELECT service_id,group_id,from_state_id,to_state_id FROM service_transitions") if x["service_id"] in services]
        requests = {}
        if transitions:
            s = r.snapshot(self.locale)
            wanted = {x["service_id"] for x in transitions}
            for t in s.rows("telegrams"):
                if t["service_id"] in wanted and t["kind"] == "request":
                    try: requests[t["service_id"]] = telegram_pattern(self.structure_layout(t["structure_id"], s))[0]
                    except DomainError: requests[t["service_id"]] = ""
        edges = [{"from": x["from_state_id"], "to": x["to_state_id"], "group_id": x["group_id"], "service_id": x["service_id"], "service": names[x["service_id"]],
                  "request": requests.get(x["service_id"], "")} for x in transitions]
        edges.sort(key=lambda e: (key(e["service"]), e["service_id"], e["from"]))
        variants = [{"id": v["id"], "name": names.get(v["id"], "")} for v in r.all("SELECT id FROM variants ORDER BY ordinal")]
        return {"groups": groups, "edges": edges, "variant": variant, "variants": variants, "revision": r.revision}

    def translation_overview(self):
        names=self._names()
        rows=[]
        paths={}
        for x in self.r.all("SELECT t.object_id,t.field,t.locale,t.value,o.kind,o.qualifier FROM localized_texts t JOIN objects o ON o.id=t.object_id ORDER BY o.kind,o.qualifier,t.field,t.locale"):
            if x["object_id"] not in paths:
                try:paths[x["object_id"]]=self.object_path(x["object_id"],None,names)["path"]
                except DomainError:paths[x["object_id"]]=""
            path=paths[x["object_id"]]
            rows.append({**x,"id":x["object_id"],"name":names[x["object_id"]],"path":path,"source":"저장된 번역"})
        return {"rows":rows,"revision":self.r.revision}

    @staticmethod
    def default_sort(kind, scope=None):
        """[UI-R100-Sort]·결정 D5: 노드에 들어갔을 때의 정렬. 문서 순서가 뜻을 가지는 목록(클래스·인스턴스·의존성·보존 원문 목록·
        인스턴스 페이지)은 문서 순서, 번호가 있는 목록은 번호순(같으면 이름순), 그 밖은 이름순."""
        if kind in ORDERED_KINDS or (kind == "service" and scope): return [("ordinal", False)]
        number = NUMBER_SORT.get(kind)
        return [(number, False), ("name", False)] if number else [("name", False)]

    def query_objects(self, kind, variant=None, filter_text="", sort="name", descending=False, page=0, page_size=200, scope=None, target_id=None, strict_target=True, sorts=None, blocking_only=False, include_resolved=False):
        """목록 조회. scope는 트리 노드의 범위(클래스·인스턴스·상태 그룹 ID)다. 동적 열이 있는 목록은 columns를 함께 돌려준다.
        page(선택)는 목록 위 머리 영역(인스턴스 머리·클래스 설명·원본 안내)이다. 작업 스레드 한 번 호출로 함께 받는다.
        filter_text: [LIST-R500] 대소문자 무시, 띄어 쓴 단어는 하나라도 맞으면(OR), 큰따옴표 안은 구절 그대로(결정 D2). 번호(10진·0x 16진)는 단어마다 비교한다.
        sorts: [[필드, 내림차순], ...] 여러 열 정렬([LIST-R210]). 없으면 sort·descending 한 열, sort=None이면 default_sort.
        page_size가 0(또는 None)이면 페이지를 나누지 않는다(결정 D4, 화면 목록)."""
        r = self.r
        columns = None
        page_info = None
        column_info = None  # 열 머리 툴팁·열 정보(M1-7): 필드 → Qualifier·종류·필수·설명
        names = self._names()
        if kind == "unsupported":
            rows = r.all("SELECT id,node_path,reason_code,owner_object_id,semantic FROM unsupported_nodes" + (" WHERE owner_object_id=?" if scope else "") + " ORDER BY id", (scope,) if scope else ())
            rows = [{**row,"id":f"unsupported:{row['id']}","name":preserved_label(row["reason_code"]),"qualifier":row["node_path"],"kind":"unsupported","supported":0,"shared":0,
                     "category":preserved_category(row)} for row in rows]
            # 29·30차: 해결된 보존 항목 = 근거를 찾아 해석했거나(해석함) 출력 영향이 없다고 판정한 원문, 그리고 가져오게 된 객체.
            def settled(row):
                return not row["semantic"] and (row["reason_code"].startswith("해석함") or row["reason_code"].startswith("출력 영향 없음"))
            if blocking_only: rows = [row for row in rows if row["semantic"] or (include_resolved and settled(row))]  # 27차: 기본은 출력 차단 항목만
            hidden = sum(1 for row in r.all("SELECT semantic,reason_code FROM unsupported_nodes" + (" WHERE owner_object_id=?" if scope else ""), (scope,) if scope else ())
                         if not row["semantic"]) - sum(1 for row in rows if not row["semantic"])
            # 예전에 보존하던 CDD 요소 중 근거를 찾아 가져온 객체(속성 import.resolved). 행을 열면 그 객체로 간다.
            resolved = [] if scope else self._resolved_rows(names)
            if include_resolved: rows = rows + resolved
            texts = []
            if blocking_only and hidden:
                texts.append(f"출력을 막는 보존 항목{'과 해결된 항목' if include_resolved else ''}만 보입니다. 그 밖의 출력에 영향이 없는 원문 {hidden}개(메타데이터·출처 정보·해석한 정의, 원문 참조용)는 "
                             "'출력 영향 없는 원문도 보이기'를 켜면 보입니다.")
            if resolved or include_resolved:
                texts.append(f"해결된 항목(가져오게 된 객체 {len(resolved)}개 — 구분 '해결됨(가져옴)', 해석했거나 출력 영향이 없다고 판정한 원문 — 구분 '해석됨'·'출력 영향 없음')은 "
                             + ("함께 보입니다. 가져온 객체 행을 열면 그 객체가 열립니다." if include_resolved else "'해결된 항목 보이기'를 켜면 보입니다."))
            if texts: page_info = {"title": "", "text": " ".join(texts)}
        elif kind in SOURCE_KINDS:
            rows, columns, note = self._source_rows(kind, variant, scope, names)
            if note: page_info = {"title": "", "text": note}
        elif kind == "state_group":
            rows, columns = self._state_group_rows(names)
        elif kind == "dependency":
            rows, columns = self._dependency_rows(variant, names)
        elif kind in ("did_overview", "dtc_overview"):
            rows, columns = self._overview_rows(kind[:3] + "s", kind[:3], names)
        elif kind == "diagnostic_class":
            count = {}
            for vi in r.all("SELECT i.class_id,count(*) n FROM variant_instances vi JOIN diagnostic_instances i ON i.id=vi.instance_id WHERE vi.variant_id=? GROUP BY i.class_id", (variant,)): count[vi["class_id"]] = vi["n"]
            rows = r.all("SELECT c.*,o.qualifier,o.supported,o.read_only,o.kind,vc.ordinal FROM variant_classes vc JOIN diagnostic_classes c ON c.id=vc.class_id JOIN objects o ON o.id=c.id WHERE vc.variant_id=?", (variant,))
            for row in rows: row.update(name=names.get(row["id"], row["qualifier"]), instances=count.get(row["id"], 0), shared=1)
        elif kind == "diagnostic_instance":
            info = self._instance_info(names)
            args = [variant] + ([scope] if scope else [])
            rows = []
            for x in r.all("SELECT vi.instance_id,vi.ordinal,vc.ordinal co,o.supported,o.read_only FROM variant_instances vi JOIN diagnostic_instances i ON i.id=vi.instance_id "
                           "JOIN variant_classes vc ON vc.variant_id=vi.variant_id AND vc.class_id=i.class_id JOIN objects o ON o.id=i.id WHERE vi.variant_id=?" + (" AND i.class_id=?" if scope else ""), args):
                rows.append({**info[x["instance_id"]], "kind": "diagnostic_instance", "ordinal": (x["co"], x["ordinal"]), "supported": x["supported"], "read_only": x["read_only"]})
            if scope:
                # 진단 클래스 페이지: 설명 행(2.2). 설명이 없으면 클래스 이름만 보인다.
                page_info = {"title": names.get(scope, ""), "text": self._descriptions().get(scope, "")}
        else:
            table = {"did":"dids","dtc":"dtcs","service":"services","data_type":"data_types","state":"states","variant":"variants","ecu":"ecu","nrc":"nrcs","protocol_service":"protocol_services"}[kind]
            join, where, args = "", "", []
            if kind in ("did","dtc") and variant:
                join = f"JOIN variant_{table} v ON v.{kind}_id=t.id AND v.variant_id=?"
                args.append(variant)
            if kind == "service" and variant:
                join = "JOIN variant_instances v ON v.instance_id=t.instance_id AND v.variant_id=?"
                args.append(variant)
            if kind == "service" and scope:
                where = " WHERE t.instance_id=?"
                args.append(scope)
            if kind == "state" and scope:
                where = " WHERE t.group_id=?"
                args.append(scope)
            rows = r.all(f"SELECT t.*,o.qualifier,o.supported,o.read_only,o.kind FROM {table} t JOIN objects o ON o.id=t.id {join}{where}", args)
            shared = {}
            if kind in ("did","dtc"):
                shared = {v["target"]:v["n"] for v in r.all(f"SELECT {kind}_id target,count(*) n FROM variant_{table} GROUP BY {kind}_id")}
            elif kind == "service":
                shared = {v["id"]:v["n"] for v in r.all("SELECT s.id,count(*) n FROM services s JOIN variant_instances v ON v.instance_id=s.instance_id GROUP BY s.id")}
            usage = {}
            if kind == "nrc":
                usage = {v["code"]:v["usage"] for v in r.all("SELECT nrcs.code code,count(*) usage FROM nrcs JOIN service_nrcs ON service_nrcs.code=nrcs.code GROUP BY nrcs.code")}
            elif kind == "data_type":
                usage = {v["data_type_id"]:v["usage"] for v in r.all("SELECT data_type_id,count(*) usage FROM data_objects GROUP BY data_type_id")}
            for row in rows:
                row["name"] = names.get(row["id"],row["qualifier"])
                row["shared"] = shared.get(row["id"],1)
                if kind == "nrc": row["usage"] = usage.get(row["code"],0)
                elif kind == "data_type": row["usage"] = usage.get(row["id"],0)
            if kind == "did":
                # DID 표의 CANdela 열. 진단 클래스는 이 DID를 참조하는 인스턴스 중 문서 순서(변형·클래스·인스턴스)가
                # 가장 앞선 것의 클래스다. 고장 메모리 사용은 고장 메모리 역할 인스턴스가 참조하거나 스냅샷 DID 목록에
                # 있는 경우다. DID 데이터는 구조의 직속 데이터 객체 이름을 순서대로 붙인 값이다.
                columns = [("identifier","DID"),("name","이름"),("diagnostic_class","진단 클래스"),("fault_memory","고장 메모리 사용"),
                           ("did_data","DID 데이터"),("qualifier","Qualifier"),("shared","공유 변형"),("supported","지원")]
                class_names = {c["id"]: names.get(c["id"], c["qualifier"]) for c in r.all("SELECT c.id,o.qualifier FROM diagnostic_classes c JOIN objects o ON o.id=c.id")}
                first_class = {}
                for link in r.all("SELECT x.did_id,i.class_id FROM instance_dids x JOIN diagnostic_instances i ON i.id=x.instance_id "
                                  "JOIN variant_instances vi ON vi.instance_id=i.id JOIN variants v ON v.id=vi.variant_id "
                                  "LEFT JOIN variant_classes vc ON vc.variant_id=vi.variant_id AND vc.class_id=i.class_id "
                                  "ORDER BY v.ordinal, coalesce(vc.ordinal,2147483647), vi.ordinal"):
                    first_class.setdefault(link["did_id"], link["class_id"])
                fault_memory = self._fault_memory_dids()
                data_members = self._did_data_names(names)
                for row in rows:
                    class_id = first_class.get(row["id"])
                    row["diagnostic_class"] = class_names.get(class_id) if class_id else None
                    row["fault_memory"] = row["id"] in fault_memory
                    row["did_data"] = ", ".join(data_members.get(row.get("structure_id"), [])) or None
            elif kind == "dtc":
                columns = self._dtc_columns(rows)
                column_info = self._dtc_column_info()
            elif kind == "data_type":
                # 길이 표기(바이트:비트, 배열·범위)와 템플릿 소유 표시(2.1). 템플릿 타입은 원본 요소의 temploid로 판단한다.
                doc = self._source()
                external = self._external_ids("data_type") if doc else {}
                packet_bits = {}
                packets = [row for row in rows if row.get("encoding") == "packet" and row.get("structure_id")]
                if packets:
                    snap = r.snapshot(self.locale)
                    for row in packets:
                        try: packet_bits[row["id"]] = sum(f["bits"] for f in snap.flatten(row["structure_id"]))
                        except DomainError: packet_bits[row["id"]] = None
                for row in rows:
                    row["packet_bits"] = packet_bits.get(row["id"])
                    row["length_text"] = length_text(row)
                    info = doc.type_info(external.get(row["id"])) if doc else {}
                    row["template"] = bool(info.get("template"))
                columns = [("name","이름"),("qualifier","Qualifier"),("encoding","인코딩"),("length_text","길이 (바이트:비트)"),("byte_order","순서"),
                           ("conversion","변환"),("unit","단위"),("usage","사용")]
            elif kind == "protocol_service":
                # 요청·응답 패턴(자리표시 LL/zz/rc)과 사용·필수 열(2.1). 패턴은 원본 PROTOCOLSERVICE 구성요소에서 만든다.
                doc = self._source()
                patterns = doc.protocol_services() if doc else {}
                external = self._external_ids("protocol_service") if doc else {}
                for row in rows:
                    p = patterns.get(external.get(row["id"]), {})
                    row.update(request_pattern=p.get("request") or None, positive_pattern=p.get("positive") or None, negative_pattern=p.get("negative") or None,
                               used=row["service_count"] > 0, required=bool(p.get("required")))
                columns = [("name","이름"),("request_pattern","요청"),("positive_pattern","긍정 응답"),("negative_pattern","부정 응답"),("used","사용"),("required","필수"),
                           ("qualifier","Qualifier"),("request_text","요청 구성"),("template_count","템플릿"),("service_count","서비스")]
            elif kind == "variant":
                doc = self._source()
                descriptions = self._descriptions()
                for row in rows:
                    row["pattern_text"] = doc.pattern_summary(self._source_element(doc, row["id"])) if doc and not row["is_base"] else None
                    row["description"] = descriptions.get(row["id"]) or None
                    row["ordinal"] = row.get("ordinal", 0)
                columns = [("name","이름"),("qualifier","Qualifier"),("is_base","기본 변형"),("pattern_text","식별 패턴"),("description","설명")]
            elif kind == "service":
                columns, page_info = self._service_rows(rows, names, variant, scope)
        if kind in EDIT_KINDS and rows:
            # [QUAL-R020] 이름을 따라 Qualifier를 바꿀지 판단할 때 쓴다: 가져온 CDD 객체의 Qualifier는 바꾸지 않는다.
            imported = {m["object_id"] for m in r.all("SELECT DISTINCT object_id FROM source_mappings")}
            for row in rows: row["imported"] = row["id"] in imported
        unfiltered = len(rows)
        terms = filter_terms(filter_text)
        if terms:
            fields=("name","qualifier","identifier_text","services","request_pattern","request_text","positive_text","negative_text","conversion","unit","encoding","byte_order")+tuple(c[0] for c in columns or ())
            def hit(x):
                text=key(" ".join(str(x.get(f,"") or "") for f in fields))
                numbers=[(str(x[f]),f"0x{x[f]:X}".lower()) for f in ("identifier","code","sid") if isinstance(x.get(f),int)]
                return any(term in text or any(term in pair for pair in numbers) for term in terms)
            rows=[x for x in rows if hit(x)]
        allowed={"name","qualifier","identifier","code","sid","encoding","bit_length","shared","supported","identifier_text","services","instances","instance","ordinal","template_count","service_count","byte_order","conversion","unit","request_text","positive_text","negative_text","usage"}|{c[0] for c in columns or ()}
        if sorts:
            order=[(str(f),bool(d)) for f,d in sorts]
        elif sort is None:
            order=self.default_sort(kind,scope)
        else:
            # 예전 호출(한 열): 트리 순서가 있는 목록의 'name'은 문서 순서다(필터가 없을 때). 인스턴스 페이지는 템플릿 서비스 순서다.
            if sort == "name" and not terms and (kind in ORDERED_KINDS or (kind == "service" and scope)): sort = "ordinal"
            order=[(sort,bool(descending))]
        order=[(f,d) for f,d in order if f in allowed] or [("name",False)]
        rows.sort(key=lambda x: str(x["id"]))
        for field,desc in reversed(order):
            try: rows.sort(key=lambda x: (x.get(field) is None, key(x[field]) if isinstance(x.get(field),str) else x.get(field,0)),reverse=desc)
            except TypeError: rows.sort(key=lambda x: (x.get(field) is None, key(str(x.get(field)))),reverse=desc)
        page_size=max(1,min(page_size,1000)) if page_size else max(1,len(rows))
        if target_id is not None:
            position=next((i for i,row in enumerate(rows) if row["id"]==target_id),None)
            if position is None:
                # 화면 갱신(명령 뒤 선택 유지)은 대상이 목록에 없어도 오류 없이 첫 페이지를 보인다.
                if strict_target: raise DomainError("선택한 객체가 이 목록에 없습니다.")
            else: page=position//page_size
        result={"rows":rows[page*page_size:(page+1)*page_size],"total":len(rows),"unfiltered":unfiltered,"revision":r.revision,"page":page,"page_size":page_size,
                "sort":[[f,d] for f,d in order]}
        if columns: result["columns"]=columns
        if page_info: result["page_info"]=page_info
        if column_info: result["column_info"]=column_info
        if kind == "dtc": result["record_items"]=self._dtc_record_items()
        return result

    # ------------------------------------------------------------------ 새 번호(M1-5)
    NUMBER_RANGES = {"dtc": (1, 0xFFFFFF), "did": (1, 0xFFFF), "state": (0, 255)}

    def suggest_number(self, kind, variant=None, scope=None, reference=None):
        """[UI-R335] 새 객체의 번호. 기준 번호 R(reference)이 있으면 R+1부터 올라가며 가장 작은 빈 번호, 없으면 R−1부터 내려가며
        가장 큰 빈 번호, 둘 다 없으면 실패. reference='max'는 가장 큰 번호의 객체(필터로 보이는 행이 없을 때), None은 빈 목록(첫 번호).
        빈 번호는 그 변형(상태는 그 상태 그룹) 전체에서 찾는다(필터와 무관)."""
        r = self.r
        if kind not in self.NUMBER_RANGES: raise DomainError("번호를 제안하지 않는 목록입니다.")
        low, high = self.NUMBER_RANGES[kind]
        if kind == "dtc": used = {x["code"] for x in r.all("SELECT code FROM variant_dtcs WHERE variant_id=?", (variant,))}
        elif kind == "did": used = {x["identifier"] for x in r.all("SELECT identifier FROM variant_dids WHERE variant_id=?", (variant,))}
        else: used = {x["code"] for x in r.all("SELECT code FROM states WHERE group_id=?", (scope,))}
        if reference == "max": reference = max(used) if used else None
        if reference is None:
            number = next((n for n in range(low, high + 1) if n not in used), None)
        else:
            reference = integer(reference)
            number = next((n for n in range(max(low, reference + 1), high + 1) if n not in used), None)
            if number is None: number = next((n for n in range(min(high, reference - 1), low - 1, -1) if n not in used), None)
        if number is None: raise DomainError("비어 있는 번호가 없습니다.")
        return {"number": number}

    def add_defaults(self, kind, variant=None, scope=None, reference=None):
        """추가 대화상자 자료: 카탈로그와 [UI-R335] 제안 번호(번호가 없는 목록이면 None). 번호를 찾지 못하면 이유를 함께 돌려준다."""
        result = {"catalog": self.catalog(), "number": None, "number_error": None}
        if kind in self.NUMBER_RANGES and (kind != "state" or scope):
            try: result["number"] = self.suggest_number(kind, variant, scope, reference)["number"]
            except DomainError as e: result["number_error"] = str(e)
        if kind == "dtc": result["record_items"] = self._dtc_record_items()
        return result

    def _dtc_column_info(self):
        """DTC 레코드 항목 열의 정보(M1-7 헤더 툴팁·열 정보): 원본 템플릿의 Qualifier·종류·필수·설명·열거 값."""
        kinds = {"text": "텍스트", "enum": "열거", "uns": "부호 없는 정수"}
        info = {}
        for item in self._dtc_record_items():
            info["attr:" + item["key"]] = {"qualifier": item.get("qualifier") or item["key"][4:], "name": item["name"], "kind": kinds.get(item["kind"], item["kind"]),
                                           "required": item["required"], "description": item.get("description") or "",
                                           "choices": [f"{c['raw']} = {c['label']}" for c in item.get("choices", [])], "source": "원본 CDD 레코드 항목 템플릿"}
        return info

    # ------------------------------------------------------------------ 목록 보조(U2·U4·U5·2.1)
    def _descriptions(self):
        """객체 ID → 설명(현재 언어 → en-US → 첫 값). 비어 있는 설명은 빠진다."""
        result = {}
        texts = self.r.all("SELECT object_id,locale,value FROM localized_texts WHERE field='description' AND value!=''")
        texts.sort(key=lambda x: (x["locale"] != self.locale, x["locale"] != "en-US", x["locale"]))
        for t in texts: result.setdefault(t["object_id"], t["value"])
        return result

    def _fault_memory_dids(self):
        r = self.r
        found = {x["did_id"] for x in r.all("SELECT x.did_id FROM instance_dids x JOIN diagnostic_instances i ON i.id=x.instance_id JOIN diagnostic_classes c ON c.id=i.class_id WHERE c.role='fault_memory'")}
        found |= {x["did_id"] for x in r.all("SELECT did_id FROM snapshot_default_dids")}
        found |= {x["did_id"] for x in r.all("SELECT did_id FROM snapshot_dtc_dids")}
        return found

    def _did_data_names(self, names):
        data_members = {}
        for member in self.r.all("SELECT structure_id,data_object_id FROM structure_members WHERE data_object_id IS NOT NULL ORDER BY ordinal"):
            data_members.setdefault(member["structure_id"], []).append(names.get(member["data_object_id"], member["data_object_id"]))
        return data_members

    def _dtc_columns(self, rows):
        """DTC 표 열: 코드·이름 다음에 원본 DTC 레코드 항목(Set/Reset condition, Corrective action 등, cdd.* 속성)을 둔다(2.1).
        값은 행에 attr:<키>로 넣는다. 열 이름은 원본 레코드 항목 템플릿 이름, 없으면 키 이름이다."""
        ids = [row["id"] for row in rows]
        attrs = {}
        if ids:
            marks = ",".join("?" for _ in ids)
            by_id = {row["id"]: row for row in rows}
            for a in self.r.all(f"SELECT object_id,key,value_text FROM object_attributes WHERE object_id IN ({marks}) AND key LIKE 'cdd.%'", ids):
                if a["key"].endswith("#raw"):
                    # 열거 레코드 항목의 원시값: 열은 아니지만 '선택한 DTC에 이 값 복사'(M1-2)가 원시값으로 넣는다.
                    by_id[a["object_id"]]["raw:" + a["key"][:-4]] = a["value_text"]
                    continue
                if "#" in a["key"] or "@" in a["key"]: continue
                attrs.setdefault(a["object_id"], {})[a["key"]] = a["value_text"]
        keys = []
        for row in rows:
            for k in attrs.get(row["id"], {}):
                if k not in keys: keys.append(k)
            for k, v in attrs.get(row["id"], {}).items(): row["attr:" + k] = v
        labels = {}
        doc = self._source()
        if doc is not None:
            for tag in ("TRRECORDITEMTMPL", "ENUMRECORDITEMTMPL", "UNSRECORDITEMTMPL"):
                for tmpl in doc.root.iter(tag):
                    labels.setdefault("cdd." + doc.qual(tmpl), doc.name(tmpl))
        return ([("code","DTC"),("name","이름")] + [("attr:" + k, labels.get(k) or k[4:]) for k in keys]
                + [("qualifier","Qualifier"),("shared","공유 변형")])

    def _state_group_rows(self, names):
        """State Groups 마스터 표(U4): 이름, Semantic, 상태 불일치 부정 응답(원본), 상태(첫 상태 = 초기 상태), 설명."""
        doc = self._source()
        source_groups = {g["qualifier"]: g for g in doc.states()[1]} if doc else {}
        descriptions = self._descriptions()
        rows = []
        for n, g in enumerate(self.r.all("SELECT g.id,g.kind group_kind,g.initial_state_id,o.qualifier,o.supported,o.read_only FROM state_groups g JOIN objects o ON o.id=g.id ORDER BY g.kind='security',g.kind")):
            states = self.r.all("SELECT id FROM states WHERE group_id=? ORDER BY code", (g["id"],))
            states.sort(key=lambda st: st["id"] != g["initial_state_id"])
            source = source_groups.get(g["qualifier"], {})
            rows.append({**g, "kind": "state_group", "ordinal": n, "name": names.get(g["id"], g["qualifier"]),
                         "negative_response": source.get("negative_response") or None,
                         "states_text": ", ".join(names.get(st["id"], "") for st in states), "state_count": len(states),
                         "description": descriptions.get(g["id"]) or None})
        columns = [("name","이름"),("group_kind","Semantic"),("negative_response","부정 응답"),("states_text","상태 (첫 상태 = 초기 상태)"),
                   ("description","설명"),("qualifier","Qualifier")]
        return rows, columns

    def _source_rows(self, kind, variant, scope, names):
        """보존 원문에서 유도한 읽기 전용 목록(U5). 원본 CDD가 없으면 빈 목록과 안내 문장을 돌려준다."""
        doc = self._source()
        note = None if doc is not None else "원본 CDD가 없는 프로젝트입니다. 이 목록은 가져온 CDD의 보존 원문에서 만듭니다."
        rows, columns = [], []

        def add(items, prefix):
            for n, item in enumerate(items):
                rows.append({"ordinal": n, **item, "id": f"src:{prefix}:{n}", "kind": kind, "supported": 1, "read_only": 1})
        if kind == "interfaces":
            columns = [("supported_check","지원"),("name","인터페이스"),("qualifier","Qualifier"),("value_text","값"),("description","설명")]
            if doc: add([{**i, "supported_check": i["supported"]} for i in doc.interfaces()], "iface")
        elif kind == "comm_params":
            columns = [("name","이름"),("category","범주"),("default_text","기본값"),("value_text","덮어쓴 값"),("description","설명"),("qualifier","Qualifier")]
            if doc: add(doc.interface_parameters(scope or ""), "param")
        elif kind == "ident_features":
            columns = [("name","데이터 객체"),("instance","진단 인스턴스"),("service","서비스"),("data_type","데이터 타입"),("path","경로")]
            if doc: add(doc.identification_features(), "feature")
        elif kind == "ident_patterns":
            columns = [("pattern","패턴"),("feature","식별 특징"),("value_text","값"),("raw","원시 바이트")]
            if doc: add(doc.identification_patterns(self._source_element(doc, scope or variant)), "pattern")
        elif kind == "supported_classes":
            columns = [("checked","지원"),("name","진단 클래스"),("qualifier","Qualifier"),("cls_text","종류"),("single","싱글턴"),("services","템플릿 서비스"),("description","설명")]
            if doc:
                add(doc.supported_classes(self._source_element(doc, scope or variant)), "class")
            else:
                note = None  # 원본이 없어도 프로젝트의 클래스로 체크 목록을 만든다.
                used = {c["class_id"] for c in self.r.all("SELECT class_id FROM variant_classes WHERE variant_id=?", (scope or variant,))}
                classes = self.r.all("SELECT c.id,c.role,c.singleton,o.qualifier FROM diagnostic_classes c JOIN objects o ON o.id=c.id ORDER BY o.qualifier")
                add([{"name": names.get(c["id"], c["qualifier"]), "qualifier": c["qualifier"], "cls_text": c["role"], "single": bool(c["singleton"]),
                      "checked": c["id"] in used, "services": None, "description": ""} for c in classes], "class")
        elif kind == "job":
            columns = [("name","Job"),("kind_text","종류"),("qualifier","Qualifier"),("file","파일"),("entry_point","진입점"),("inputs","입력"),("outputs","출력"),("description","설명")]
            if doc: add(doc.jobs(self._source_element(doc, scope)), "job")
        elif kind == "state_defaults":
            columns = [("class","클래스"),("name","서비스"),("protocol","프로토콜 서비스"),("prefix","요청")]
            if doc:
                items, columns, _ = doc.state_defaults()
                add(items, "default")
        elif kind == "unsupported_nrc":
            columns = [("code","NRC"),("name","이름"),("qualifier","Qualifier"),("description","설명")]
            if doc: add(doc.unsupported_service_nrcs(), "nrc")
        return rows, columns, note

    def _service_rows(self, rows, names, variant, scope):
        """서비스 목록(U2): CANdela 서비스 행 표기(요청 `2E F1 90 zz`, 긍정 `6E F1 90`, 부정 `7F 2E rc`), 역할·프로토콜 서비스.
        인스턴스 페이지(scope)는 템플릿이 정한 서비스를 모두 보이고, 쓰지 않는 템플릿 서비스는 회색·읽기 전용 행으로 둔다
        (DCLTMPL 보존 원문에서 유도, 켜는 편집은 막는다). 머리(page)에 인스턴스 이름·설명·식별자·공유 상태를 싣는다."""
        r = self.r
        s = r.snapshot(self.locale)
        wanted = {row["id"] for row in rows}
        found = {}
        for t in s.rows("telegrams"):
            if t["service_id"] in wanted:
                found.setdefault(t["service_id"], {})[t["kind"]] = telegram_pattern(self.structure_layout(t["structure_id"], s))[0]
        doc = self._source()
        roles = doc.service_roles() if doc else {}
        external = self._external_ids("service") if doc else {}
        protocol_objects = {}
        if doc:
            for oid, ext in self._external_ids("protocol_service").items(): protocol_objects[ext] = oid
        template_order, template_rows, required = {}, [], set()
        if scope and doc:
            element = self._source_element(doc, scope)
            template_rows = doc.instance_template_services(element) if element is not None else []
            for n, t in enumerate(template_rows):
                template_order[t["template_id"]] = n
                if t["required"] and t["service_external_id"]: required.add(t["service_external_id"])
        for n, row in enumerate(rows):
            patterns = found.get(row["id"], {})
            row["request_pattern"] = patterns.get("request") or None
            row["positive_pattern"] = patterns.get("positive") or None
            row["negative_pattern"] = negative_pattern(row.get("sid"))
            ext = external.get(row["id"])
            role, protocol, template_id = "", "", None
            if ext in roles:
                role, protocol = roles[ext][0], roles[ext][1]
                template_id = roles[ext][2] if len(roles[ext]) > 2 else None
            row["role"] = role or ROLE_NAMES.get(row.get("sid"), "")
            row["protocol"] = protocol_label(row.get("sid"), protocol)
            row["protocol_id"] = protocol_objects.get(roles[ext][3]) if ext in roles and len(roles[ext]) > 3 else None
            row["use"] = ("필수" if ext in required else "사용") if row.get("enabled", 1) else "비활성"
            row["use_check"] = bool(row.get("enabled", 1))
            row["ordinal"] = template_order.get(template_id, 1000 + n)
        page_info = None
        if scope:
            present = {external.get(row["id"]) for row in rows}
            for n, t in enumerate(template_rows):
                # 쓰지 않는 템플릿 서비스(미사용)와, CDD에는 있지만 가져오지 못해 원문으로만 보존한 서비스(보존 원문)를 회색 행으로 둔다.
                if t["used"] and t["service_external_id"] in present: continue
                preserved = t["used"]
                rows.append({"id": f"template:{t['template_id']}", "kind": "template_service", "ordinal": n, "name": t["name"], "qualifier": t["qualifier"],
                             "role": t["name"], "protocol": t["protocol"], "request_pattern": t["request_pattern"] or None,
                             "positive_pattern": t["positive_pattern"] or None, "negative_pattern": t["negative_pattern"] or None,
                             "use": "보존 원문" if preserved else "미사용", "use_check": preserved, "supported": 1, "read_only": 1, "shared": 0, "sid": None,
                             "conv_text": {"req": "필수", "optyes": "선택", "optno": "선택"}.get(t["conv"], t["conv"])})
            page_info = self._instance_header(scope, names)
            columns = [("use_check","사용"),("role","역할"),("protocol","프로토콜 서비스"),("request_pattern","요청"),("positive_pattern","긍정 응답"),
                       ("negative_pattern","부정 응답"),("name","이름"),("qualifier","Qualifier"),("shared","공유 변형")]
        else:
            columns = [("sid","SID"),("name","이름"),("role","역할"),("request_pattern","요청"),("positive_pattern","긍정 응답"),("negative_pattern","부정 응답"),
                       ("qualifier","Qualifier"),("subfunction","Subfunction"),("shared","공유 변형")]
        return columns, page_info

    def _instance_header(self, oid, names):
        """인스턴스 페이지 머리: 이름·설명·식별자·클래스·공유 상태·고장 메모리 여부."""
        r = self.r
        inst = r.one("SELECT i.*,o.qualifier,o.supported,o.read_only,c.role FROM diagnostic_instances i JOIN objects o ON o.id=i.id JOIN diagnostic_classes c ON c.id=i.class_id WHERE i.id=?", (oid,))
        if not inst: return None
        info = self._instance_info(names).get(oid, {})
        identifier = info.get("identifier_text") or ""
        variants = [names.get(v["variant_id"], "") for v in r.all("SELECT vi.variant_id FROM variant_instances vi JOIN variants v ON v.id=vi.variant_id WHERE vi.instance_id=? ORDER BY v.ordinal", (oid,))]
        return {"kind": "instance", "id": oid, "title": names.get(oid, inst["qualifier"]), "qualifier": inst["qualifier"],
                "text": self._descriptions().get(oid, ""), "identifier": ("0x" + identifier) if identifier else "",
                "class_name": names.get(inst["class_id"], ""), "share_text": SHARE_TEXT.get(info.get("share"), ""),
                "owner": names.get(inst["owner_variant_id"], "") if inst["owner_variant_id"] else "", "variants": variants,
                "required": bool(info.get("required")), "warnings": info.get("warnings", 0), "fault_memory": inst["role"] == "fault_memory",
                # M5-4: 페이지 머리 이름 칸을 고칠 수 있는지(원문으로 가져온 인스턴스·미지원 인스턴스는 읽기 전용)
                "editable": bool(inst["supported"] and not inst["read_only"]), "class_id": inst["class_id"]}

    def details(self, oid):
        r=self.r
        if oid.startswith("unsupported:"):
            return {"kind":"unsupported",**r.one("SELECT * FROM unsupported_nodes WHERE id=?",(int(oid.split(":")[1]),))}
        s=r.snapshot(self.locale)
        obj=dict(s.get("objects",oid))
        table={"did":"dids","dtc":"dtcs","service":"services","data_type":"data_types","variant":"variants","state":"states","ecu":"ecu","nrc":"nrcs","protocol_service":"protocol_services"}.get(obj["kind"])
        if table: obj.update(s.get(table,oid))
        obj.update(name=s.name(oid),description=s.name(oid,"description"),texts=s.texts(oid)+s.texts(oid,"description"),sources=r.all("SELECT * FROM source_mappings WHERE object_id=?",(oid,)))
        if obj["kind"] in ("did","data_type") and obj.get("structure_id"):
            obj["members"]=self.structure_fields(obj["structure_id"],s)
            obj["layout"]=self.structure_layout(obj["structure_id"],s)
        if obj["kind"]=="data_type":
            obj["entries"]=[e for e in s.rows("conversion_entries") if e["data_type_id"]==oid]
            obj["exclusions"]=sorted((e for e in s.rows("raw_exclusions") if e["data_type_id"]==oid),key=lambda e:(e["ordinal"],e["locale"]))
        if obj["kind"]=="service":
            obj["conditions"]=[c for c in s.rows("service_conditions") if c["service_id"]==oid]
            obj["transitions"]=[c for c in s.rows("service_transitions") if c["service_id"]==oid]
            obj["nrcs"]=[c["code"] for c in s.rows("service_nrcs") if c["service_id"]==oid]
            nrc_names={n["code"]:s.name(n["id"]) for n in s.rows("nrcs")}
            obj["nrc_names"]={c:nrc_names[c] for c in obj["nrcs"] if c in nrc_names}
            refs=[d["did_id"] for d in s.rows("instance_dids") if d["instance_id"]==obj["instance_id"]]
            obj["did_id"]=refs[0] if refs else None
            headers=3 if obj["sid"]==49 else 2
            obj["telegram_layouts"]={}
            for t in s.rows("telegrams"):
                if t["service_id"]==oid:
                    obj[t["kind"]+"_members"]=self.structure_fields(t["structure_id"],s)[headers:]
                    obj["telegram_layouts"][t["kind"]]=self.structure_layout(t["structure_id"],s)
            obj["variants"]=[s.name(v["variant_id"]) for v in s.rows("variant_instances") if v["instance_id"]==obj["instance_id"]]
            obj["routine_identifier"]=obj.get("routine_identifier")
            obj["condition_view"]=[{"group":s.name(c["group_id"]),"state":s.name(c["state_id"]),"allowed":bool(c["allowed"])} for c in sorted(obj["conditions"],key=lambda c:(c["group_id"],s.get("states",c["state_id"])["code"]))]
            obj["transition_view"]=[{"group":s.name(t["group_id"]),"from":s.name(t["from_state_id"]),"to":s.name(t["to_state_id"])} for t in obj["transitions"]]
        if obj["kind"] in ("did","dtc"):
            obj["variants"]=[s.name(v["variant_id"]) for v in s.rows("variant_"+obj["kind"]+"s") if v[obj["kind"]+"_id"]==oid]
        if obj["kind"]=="diagnostic_instance":
            inst=s.get("diagnostic_instances",oid)
            info=self._instance_info(self._names())[oid]
            obj.update(class_id=inst["class_id"],class_name=s.name(inst["class_id"]),owner_text=s.name(inst["owner_variant_id"]) if inst.get("owner_variant_id") else None,identifier_text=info["identifier_text"] or None,services_text=info["services"] or "(가져온 서비스 없음)",
                       share_text=SHARE_TEXT[info["share"]],required_text="예" if info["required"] else None,
                       variants=[s.name(v["variant_id"]) for v in s.rows("variant_instances") if v["instance_id"]==oid])
            # 고장 메모리 레코드(schema 8)
            q=lambda i:s.get("objects",i)["qualifier"]
            snaps=sorted((x for x in s.rows("snapshot_records") if x["instance_id"]==oid),key=lambda x:x["number"])
            if snaps: obj["snapshot_text"]=", ".join(f"0x{x['number']:02X} {q(x['id'])}" for x in snaps)
            did_text=lambda rows:", ".join(f"0x{s.get('dids',x['did_id'])['identifier']:04X} {q(x['did_id'])}" for x in sorted(rows,key=lambda x:x["ordinal"]))
            defaults=[x for x in s.rows("snapshot_default_dids") if x["instance_id"]==oid]
            if defaults: obj["snapshot_dids_text"]=did_text(defaults)
            per_dtc={}
            for x in s.rows("snapshot_dtc_dids"):
                if x["instance_id"]==oid: per_dtc.setdefault(x["dtc_id"],[]).append(x)
            if per_dtc: obj["snapshot_dtc_text"]="\n".join(f"0x{s.get('dtcs',d)['code']:06X}: "+did_text(rows) for d,rows in sorted(per_dtc.items(),key=lambda kv:s.get("dtcs",kv[0])["code"]))
            ext=sorted((x for x in s.rows("extended_records") if x["instance_id"]==oid),key=lambda x:x["number"])
            if ext:
                def size(st):
                    try: return f"{sum(f['bits'] for f in s.flatten(st))//8}바이트"
                    except DomainError as e: return str(e)
                obj["extended_text"]="\n".join(f"0x{x['number']:02X} {q(x['id'])} ({size(x['structure_id'])})" for x in ext)
        if obj["kind"]=="diagnostic_class":
            c=s.get("diagnostic_classes",oid)
            obj.update(role=c["role"],singleton_text="예(변형마다 인스턴스 하나)" if c["singleton"] else "아니요",
                       variants=[s.name(v["variant_id"]) for v in s.rows("variant_classes") if v["class_id"]==oid])
        if obj["kind"]=="job_container":
            j=s.get("job_containers",oid)
            obj.update(owner_text=s.name(j["owner_variant_id"]) if j["owner_variant_id"] else None,
                       variants=[s.name(v["variant_id"]) for v in s.rows("variant_jobs") if v["job_id"]==oid])
        if obj["kind"]=="state_group":
            g=s.get("state_groups",oid)
            obj["states_text"]=", ".join(s.name(st["id"])+(" (초기)" if st["id"]==g["initial_state_id"] else "") for st in sorted((x for x in s.rows("states") if x["group_id"]==oid),key=lambda x:x["code"]))
        obj["attributes"]=r.all("SELECT key,value_type,value_text FROM object_attributes WHERE object_id=? ORDER BY key",(oid,))
        self._details_extra(obj, s)
        if obj["kind"] in ("did","service"):self._attach_layout_owners(obj, s)
        return obj

    def _details_extra(self, obj, s):
        """페이지·속성 창 보강(U2·U5·U6): ECU 요약, 타입 길이·사용·원본 변환식, 상태 그룹 상태 표, 서비스 역할, 변형 식별 패턴."""
        r = self.r
        oid, kind = obj["id"], obj["kind"]
        doc = self._source()
        if kind == "ecu":
            source = r.one("SELECT basename,format_version,sha256,imported_at FROM source_files WHERE kind='cdd' ORDER BY imported_at LIMIT 1")
            if source: obj["source_text"] = f"{source['basename']} · {source['format_version'] or ''} · 가져온 시각 {source['imported_at']}"
            obj["revision_text"] = str(r.revision)
            counts = [(label, r.one(f"SELECT count(*) n FROM {table}")["n"]) for label, table in
                      (("변형", "variants"), ("DID", "dids"), ("DTC", "dtcs"), ("진단 클래스", "diagnostic_classes"), ("인스턴스", "diagnostic_instances"),
                       ("서비스", "services"), ("데이터 타입", "data_types"), ("보존 항목", "unsupported_nodes"))]
            obj["summary_text"] = " · ".join(f"{label} {n:,}" for label, n in counts)
        elif kind == "data_type":
            obj["usage"] = r.one("SELECT count(*) n FROM data_objects WHERE data_type_id=?", (oid,))["n"]
            obj["usage_text"] = f"데이터 객체 {obj['usage']}곳에서 사용" if obj["usage"] else "사용하지 않음"
            packet_bits = None
            if obj.get("encoding") == "packet" and obj.get("structure_id"):
                try: packet_bits = sum(f["bits"] for f in s.flatten(obj["structure_id"]))
                except DomainError: packet_bits = None
            obj["length_text"] = length_text({**obj, "packet_bits": packet_bits})
            info = doc.type_info(self._external_ids("data_type").get(oid)) if doc else {}
            if info.get("template"): obj["template_text"] = "예 (템플릿에서 온 타입)"
            if info.get("formula"):
                from .presentation import formula_text
                obj["formula"] = info["formula"]
                obj["formula_text"] = formula_text(info["formula"])
        elif kind == "state_group":
            g = s.get("state_groups", oid)
            obj["kind_text"] = g["kind"]
            source = {}
            if doc:
                source = next((x for x in doc.states()[1] if x["qualifier"] == obj.get("qualifier")), {})
            if source.get("negative_response"): obj["negative_response"] = source["negative_response"]
            states = sorted((x for x in s.rows("states") if x["group_id"] == oid), key=lambda x: (x["id"] != g["initial_state_id"], x["code"]))
            obj["detail_table"] = {"title": f"{s.name(oid)} 상태 (첫 상태 = 초기 상태)",
                                   "columns": [("code", "코드"), ("name", "이름"), ("qualifier", "Qualifier"), ("initial", "초기 상태")],
                                   "rows": [{"id": x["id"], "code": x["code"], "name": s.name(x["id"]), "qualifier": s.get("objects", x["id"])["qualifier"],
                                             "initial": "예" if x["id"] == g["initial_state_id"] else ""} for x in states]}
        elif kind == "service":
            roles = doc.service_roles() if doc else {}
            ext = self._external_ids("service").get(oid) if doc else None
            role, protocol = (roles[ext][0], roles[ext][1]) if ext in roles else ("", "")
            obj["role_text"] = role or ROLE_NAMES.get(obj.get("sid"), "") or None
            obj["protocol_text"] = protocol_label(obj.get("sid"), protocol) or None
            # 상태 그룹 NRC(M5-3, 표시 전용): 이 서비스를 막는 상태가 있는 그룹의 '상태 불일치 부정 응답'(원본 NEGRESCODEPROXY)
            blocked = {c["group_id"] for c in obj.get("conditions", []) if not c["allowed"]}
            if blocked:
                source_groups = {g["qualifier"]: g for g in doc.states()[1]} if doc else {}
                state_nrcs = []
                for gid in sorted(blocked, key=lambda g: s.name(g)):
                    text = source_groups.get(s.get("objects", gid)["qualifier"], {}).get("negative_response") or ""
                    match = re.match(r"0x([0-9A-Fa-f]{1,2})\b", text)
                    state_nrcs.append({"group": s.name(gid), "code": int(match.group(1), 16) if match else None,
                                       "text": text or "(원본 CDD에 상태 불일치 NRC 없음)"})
                obj["state_nrcs"] = state_nrcs
        elif kind == "dtc":
            # DTC 속성 편집(속성 탭): 원본 CDD의 레코드 항목 템플릿(Set/Reset condition, Corrective action 등)
            obj["record_items"] = self._dtc_record_items()
        elif kind == "variant" and doc and not obj.get("is_base"):
            element = self._source_element(doc, oid)
            patterns = doc.identification_patterns(element)
            if patterns:
                obj["pattern_text"] = doc.pattern_summary(element)
                obj["detail_table"] = {"title": "식별 패턴 (원본 CDD, 읽기 전용)",
                                       "columns": [("pattern", "패턴"), ("feature", "식별 특징"), ("value_text", "값"), ("raw", "원시 바이트")],
                                       "rows": [{"id": f"src:pattern:{n}", **row} for n, row in enumerate(patterns)]}

    def _attach_layout_owners(self, obj, s=None):
        """배치 멤버 칸 편집(M5-2): 배치 행 데이터 객체의 소유자·공유·편집 가능 여부, 데이터 타입 칸의 선택지(이름순)."""
        ids = [row["object_id"] for row in obj.get("layout") or [] if row.get("object_id")]
        for layout in (obj.get("telegram_layouts") or {}).values():
            ids += [row["object_id"] for row in layout if row.get("object_id")]
        obj["layout_owners"] = self._layout_owners(ids) if ids else {}
        if any(o["editable"] for o in obj["layout_owners"].values()):
            s = s or self.r.snapshot(self.locale)
            obj["type_choices"] = sorted(({"id": t["id"], "name": s.name(t["id"])} for t in s.rows("data_types")), key=lambda t: key(t["name"]))

    def member_counterpart(self, object_id, old_owner, new_owner):
        """U3 '사본으로 바꾼 뒤 수정'(M5-2): 원래 소유자(DID·서비스) 배치에서 멤버의 위치를 찾아 사본의 같은 위치 멤버(데이터 객체 ID)를 돌려준다."""
        r = self.r
        s = r.snapshot(self.locale)

        def members(owner):
            o = r.one("SELECT kind FROM objects WHERE id=?", (owner,))
            if not o: raise DomainError("객체가 없습니다.")
            if o["kind"] == "did":
                layouts = [("did", self.structure_layout(r.one("SELECT structure_id FROM dids WHERE id=?", (owner,))["structure_id"], s))]
            elif o["kind"] == "service":
                layouts = [(t["kind"], self.structure_layout(t["structure_id"], s)) for t in sorted((t for t in s.rows("telegrams") if t["service_id"] == owner), key=lambda t: t["kind"])]
            else: raise DomainError("DID·서비스의 멤버가 아닙니다.")
            return [(k, i, row.get("object_id")) for k, layout in layouts for i, row in enumerate(layout)]

        position = next(((k, i) for k, i, oid in members(old_owner) if oid == object_id), None)
        if position is None: raise DomainError("원래 멤버를 찾지 못했습니다.")
        match = next((oid for k, i, oid in members(new_owner) if (k, i) == position and oid), None)
        if not match: raise DomainError("사본에서 같은 위치의 멤버를 찾지 못했습니다.")
        return {"id": match}

    def selection_details(self, oid, ids=None):
        """선택 객체 창(M2-2): 첫 선택 객체의 상세와 선택한 모든 객체의 속성 시트를 작업 스레드 한 번 호출로 돌려준다."""
        return {"details": self.details(oid), "sheet": self.property_sheet(ids or [oid])}

    def structure_fields(self, structure, s=None):
        s=s or self.r.snapshot(self.locale)
        rows=[]
        for m in s.members(structure):
            item=dict(m)
            if m["data_object_id"]:
                item.update(s.get("data_objects",m["data_object_id"]))
                item.update(name=s.name(m["data_object_id"]),qualifier=s.get("objects",m["data_object_id"])["qualifier"])
            if m["literal_bytes"]: item["hex"]=m["literal_bytes"].hex(" ").upper()
            if m["nested_structure_id"]: item.update(name=s.name(m["nested_structure_id"]),structure_kind=s.get("structures",m["nested_structure_id"])["kind"])
            rows.append(item)
        return rows

    @staticmethod
    def structure_layout(structure, s):
        """표시용 평탄화 배치. 순환 등 구조 오류는 예외 대신 오류 행으로 돌려 속성 창이 열리게 한다."""
        rows=[];position=0
        try: fields=s.flatten(structure)
        except DomainError as e: return [{"kind":"error","label":str(e),"bit_offset":0,"bits":0}]
        def row_of(f,offset,depth):
            row={"kind":f["kind"],"label":f["label"],"bit_offset":offset,"bits":f["bits"],"depth":depth}
            if f["kind"]=="literal": row["value"]=f["bytes"].hex(" ").upper()
            elif f["kind"]=="data":
                t,o=f["type"],f["object"]
                row.update(variable=f.get("variable",False),min_bytes=f.get("min_bytes"))
                row.update(type_name=s.name(t["id"]),encoding=t["encoding"],byte_order=t["byte_order"],conversion=t["conversion"],factor_text=t["factor_text"],offset_text=t["offset_text"],unit=t["unit"],default_text=o["default_text"],constant_text=o["constant_text"])
                row.update(object_id=o["id"],type_id=t["id"],description=s.name(o["id"],"description") or "")
            elif f["kind"]=="iteration": row.update(min_items=f["min_items"],item_bits=f["item_bits"])
            elif f["kind"]=="fault_record": row.update(role=f["role"],variable=f["variable"])
            elif f["kind"]=="mux": row.update(variable=True,cases=[(lo,hi) for lo,hi,_ in f["cases"]],has_default=f["default"] is not None)
            return row
        for f in fields:
            rows.append(row_of(f,position,0))
            if f["kind"]=="iteration":
                # 반복 항목은 항목 시작을 0으로 한 상대 위치로 보인다.
                inner=0
                for g in f["item"]: rows.append(row_of(g,inner,1));inner+=g["bits"]
            position+=f["bits"]
        return rows

    def catalog(self):
        s=self.r.snapshot(self.locale)
        return {"dtc_record_items":self._dtc_record_items(),"types":[{**t,"name":s.name(t["id"])} for t in s.rows("data_types")],"dids":[{**d,"name":s.name(d["id"])} for d in s.rows("dids")],"states":[{**d,"name":s.name(d["id"])} for d in s.rows("states")],"groups":[{**d,"name":s.name(d["id"])} for d in s.rows("state_groups")],"variants":[{**d,"name":s.name(d["id"])} for d in s.rows("variants")],"structures":[{**d,"name":s.name(d["id"])} for d in s.rows("structures")]}

    def preview_fields(self, oid):
        s=self.r.snapshot(self.locale)
        service=s.get("services",oid)
        telegrams=[t for t in s.rows("telegrams") if t["service_id"]==oid]
        fields={}
        for t in telegrams:
            # 반복 항목의 데이터 객체도 입력 대상이다(미리보기는 최소 반복 횟수만큼 항목을 넣는다).
            def expand(items):
                for f in items:
                    if f["kind"]=="iteration": yield from expand(f["item"])
                    elif f["kind"]=="mux":  # 모든 케이스·기본 구조의 데이터 객체(선택한 케이스만 미리보기에 쓰인다)
                        for _,_,case in f["cases"]: yield from expand(case)
                        if f["default"]: yield from expand(f["default"])
                    else: yield f
            for f in expand(s.flatten(t["structure_id"])):
                if f["kind"]=="data": fields[f["object"]["id"]]={"id":f["object"]["id"],"name":f["label"],"value":f["object"]["constant_text"] or f["object"]["default_text"] or "", "constant": f["object"]["constant_text"] is not None,"encoding":f["type"]["encoding"]}
        return list(fields.values())

    def build_telegram_preview(self, oid, values):
        s=self.r.snapshot(self.locale)
        result={"revision":s.revision,"telegrams":[]}
        for t in s.rows("telegrams"):
            if t["service_id"]==oid:
                data,layout=s.encode(t["structure_id"],values)
                result["telegrams"].append({"kind":t["kind"],"hex":data.hex(" ").upper(),"layout":layout})
        service=s.get("services",oid)
        for nrc in s.rows("service_nrcs"):
            if nrc["service_id"]==oid:result["telegrams"].append({"kind":f"negative NRC 0x{nrc['code']:02X}","hex":bytes([127,service["sid"],nrc["code"]]).hex(" ").upper(),"layout":[]})
        return result

    def save_project(self,path,cancel=lambda:None):
        result=self.session.save(path,cancel)
        return {**self.overview(),"saved_path":result}

    def validate_project(self,cancel=lambda:None,variants=None):
        snapshot=self.r.snapshot(self.locale,include_sources=variants is not None)
        if variants is None:
            report=validate(snapshot,cancel)
        else:
            from .export_scope import validate_export
            report=validate_export(snapshot,variants,cancel)
            report["scope_label"]="출력 범위: "+", ".join(snapshot.name(v) for v in variants)
        names=self._names()
        for issue in report["issues"]:
            oid=issue.get("object_id");path=""
            if oid and not str(oid).startswith("unsupported:"):
                try: path=self.object_path(oid,names=names)["path"]
                except DomainError: path=""
            issue["path"]=path
        return report

    def set_locale(self,locale):
        self.locale=locale
        return self.overview()

    def apply_cdd(self,preview,cancel=lambda:None):
        if digest(preview["path"])!=preview["sha256"]:
            raise DomainError("미리보기 이후 원본 파일이 변경되었습니다.","FILE_CHANGED")
        previous=self.session
        self.session=None
        try:
            eq=preview["ecu"]["qualifier"]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",eq): eq="ImportedECU"
            self.new_project(preview["ecu"]["name"].strip() or preview["ecu"]["qualifier"] or eq,eq,"en-US")
            r=self.r
            mapped={}
            with r.command("CDD 가져오기"):
                source=uid()
                # CDDT(템플릿)도 같은 CANDELA 형식이라 원본 종류는 cdd로 두고, 문서 종류는 형식 버전과 ECU 속성에 남긴다.
                document_type=preview.get("document_type","CDD")
                r.insert("source_files",id=source,kind="cdd",basename=preview["basename"],format_version=f"{preview['version']} {document_type}",sha256=preview["sha256"],content=preview["content"],imported_at=now())
                def map_node(record,oid):
                    if record.get("external_id"): mapped[record["external_id"]]=oid
                    r.insert("source_mappings",object_id=oid,source_file_id=source,node_path=record["node_path"],external_id=record.get("external_id"),external_oid=record.get("external_oid"))
                    for field,locales in record.get("texts",{}).items():
                        for locale,value in locales.items(): self._text(oid,field,locale,value)
                def attributes(oid,rows):
                    for a in rows: r.insert("object_attributes",object_id=oid,**a)
                def unverified(oid,record):
                    # 바이트 배치는 가져왔지만 의미(ODX 대응)를 예제·도움말로 확인하지 못한 객체(21차). supported=0 → VAL-012로 출력을 막는다.
                    reasons=record.get("unverified") or []
                    if not reasons: return
                    r.update("objects",{"id":oid},{"supported":0})
                    attributes(oid,[{"key":"cdd.unverified","value_type":"text","value_text":"; ".join(dict.fromkeys(reasons))}])
                def import_members(members):
                    result=[]
                    for index,m in enumerate(members):
                        q=m["qualifier"]
                        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",q or ""): q=f"Field_{index+1}"
                        if m.get("kind")=="bitfield":
                            # 비트필드: 전용 중첩 구조(kind=bitfield). 남는 상위 비트는 예약 비트로 채운다.
                            # 빈 비트(GAPDATAOBJ)는 예약 비트다.
                            inner=[{"kind":"reserved","reserved_bits":x["bits"],"label":x.get("qualifier")} if x["kind"]=="gap" else import_members([x])[0] for x in m["members"]]
                            used=sum(x["reserved_bits"] if x["kind"]=="reserved" else r.one("SELECT bit_length*array_length n FROM data_types WHERE id=?",(x["data_type_id"],))["n"] for x in inner)
                            if used<m["container_bits"]: inner.append({"kind":"reserved","reserved_bits":m["container_bits"]-used})
                            sid=self._object("structure",q,m["name"],texts=m.get("texts") or None)
                            r.insert("structures",id=sid,kind="bitfield")
                            self._members(sid,inner)
                            for sub,row in zip([x for x in m["members"] if x["kind"]=="data"],r.all("SELECT data_object_id FROM structure_members WHERE structure_id=? AND data_object_id IS NOT NULL ORDER BY ordinal",(sid,))):
                                map_node(sub,row["data_object_id"])
                            map_node(m,sid)
                            result.append({"kind":"structure","nested_structure_id":sid})
                            continue
                        result.append({**m,"qualifier":q,"data_type_id":mapped[m["type_external_id"]]})
                    return result
                ecu=r.one("SELECT id FROM ecu")["id"]
                map_node(preview["ecu"],ecu)
                attributes(ecu,preview["ecu"].get("attributes",[])+[{"key":"cdd.document_type","value_type":"text","value_text":document_type},
                                                                   {"key":"cdd.dtd_version","value_type":"text","value_text":preview["version"]}]
                           +([{"key":"cdd.template_oid","value_type":"text","value_text":preview["template_oid"]}] if preview.get("template_oid") else []))
                r.update("ecu",{"id":ecu},{"manufacturer":preview["manufacturer"]})
                # 상태 그룹: CDD에 정의가 있으면 새 프로젝트의 기본 그룹을 대체한다.
                states={}
                groups=[g for g in preview.get("state_groups",[]) if g["supported"]]
                if groups:
                    for g in r.all("SELECT id FROM state_groups"):
                        olds=[x["id"] for x in r.all("SELECT id FROM states WHERE group_id=?",(g["id"],))]
                        for sid_ in olds: r.delete("states",id=sid_)
                        r.delete("state_groups",id=g["id"])
                        for oid in olds+[g["id"]]: r.delete("localized_texts",object_id=oid); r.delete("objects",id=oid)
                group_ids={}
                for index,g in enumerate(preview.get("state_groups",[])):
                    if not g["supported"]: continue
                    gid=self._object("state_group",g["qualifier"],g["name"],texts=g["texts"])
                    ids_=[self._object("state",st["qualifier"],st["name"],texts=st["texts"]) for st in g["states"]]
                    r.insert("state_groups",id=gid,kind=g["kind"],initial_state_id=ids_[0])
                    for st,oid in zip(g["states"],ids_):
                        r.insert("states",id=oid,group_id=gid,code=st["code"])
                        r.insert("source_mappings",object_id=oid,source_file_id=source,node_path=st["node_path"],external_id=None,external_oid=st["external_oid"])
                        attributes(oid,[{"key":"cdd.state_code_basis","value_type":"text","value_text":st["code_basis"]}])
                        states[st["external_oid"]]=oid
                    r.insert("source_mappings",object_id=gid,source_file_id=source,node_path=g["node_path"],external_id=None,external_oid=g["external_oid"])
                    attributes(gid,[{"key":"cdd.initial_state","value_type":"text","value_text":"first_state_in_document"}]+([{"key":"cdd.state_group_spec","value_type":"text","value_text":g["spec"] or ""}] if g.get("spec")!=g["kind"] else []))
                    unverified(gid,g)
                    group_ids[index]=gid
                base=r.one("SELECT id FROM variants WHERE is_base=1")["id"]
                for v in sorted(preview["variants"],key=lambda x:not x["base"]):
                    cancel()
                    if v["base"]:
                        vid=base
                        self._common(vid,{"name":v["name"]})
                        r.update("objects",{"id":vid},{"qualifier":v["qualifier"],"qualifier_key":key(v["qualifier"])})
                    else:
                        vid=self._object("variant",v["qualifier"],v["name"],texts=v["texts"])
                        ordinal=r.one("SELECT max(ordinal)+1 n FROM variants")["n"]
                        r.insert("variants",id=vid,ecu_id=ecu,is_base=0,ordinal=ordinal,locked=0)
                    map_node(v,vid)
                # packet(STRUCTDT)은 멤버 타입이 먼저 있어야 하므로 뒤에 만든다.
                for t in sorted(preview["types"],key=lambda t:t["encoding"]=="packet"):
                    cancel()
                    # 원본 qualifier는 보존한다. 신규 입력의 형식 제한은 원본 읽기에 적용하지 않는다.
                    oid=self._object("data_type",t["qualifier"],t["name"],texts=t["texts"])
                    p={**t,"id":oid}
                    p.pop("qualifier",None)
                    values={k:t.get(k) for k in ("encoding","bit_length","byte_order","array_length","conversion","factor_text","offset_text","unit","raw_lower","raw_upper","min_length","max_length")}
                    if t.get("formula_expression"): values["formula_expression"]=t["formula_expression"]  # schema 12 FORMULADT
                    if t.get("leading_length_bits"): values["leading_length_bits"]=t["leading_length_bits"]  # schema 13 앞 길이 바이트
                    structure=self._structure(t["qualifier"]+"_Packet",import_members(t["members"]),"packet") if t["encoding"]=="packet" else None
                    r.insert("data_types",id=oid,structure_id=structure,**values)
                    for e in t["entries"]: r.insert("conversion_entries",data_type_id=oid,**e)
                    for e in t.get("exclusions",[]): r.insert("raw_exclusions",data_type_id=oid,**e)
                    for x in t.get("segments",[]): r.insert("conversion_segments",data_type_id=oid,**x)  # schema 13
                    map_node(t,oid)
                    attributes(oid,t.get("attributes",[]))
                def did_members(members):
                    # 30차: DID 안의 MUX는 케이스·기본 구조를 만들고, 선택자·케이스 행은 구조를 만든 뒤 넣는다.
                    result,pending=[],[]
                    for m in members:
                        if m.get("kind")!="mux": result.extend(import_members([m])); continue
                        def case_structure(name,items):
                            sid=self._object("structure",name,name)
                            r.insert("structures",id=sid,kind="payload")
                            self._members(sid,[{"kind":"reserved","reserved_bits":x["bits"],"label":x.get("qualifier")} if x.get("kind")=="gap" else import_members([x])[0] for x in items])
                            return sid
                        cases=[(c["lower"],c["upper"],case_structure(f"{m['qualifier']}_Case_{c['lower']}_{c['upper']}",c["members"])) for c in m["cases"]]
                        default=case_structure(f"{m['qualifier']}_Case_Default",m["default"]) if m.get("default") is not None else None
                        pending.append((len(result),m,cases,default))
                        result.append({"kind":"mux"})
                    return result,pending
                for d in preview["dids"]:
                    cancel()
                    members,pending=did_members(d["members"])
                    st=self._structure("ImportedPayload_"+uid().replace("-", ""),members)
                    rows=r.all("SELECT ordinal,data_object_id FROM structure_members WHERE structure_id=? ORDER BY ordinal",(st,))
                    for index,m,cases,default in pending:
                        selector=next((row["data_object_id"] for y,row in zip(d["members"],rows) if y.get("kind","data")=="data" and y.get("external_id")==m["selector"]),None)
                        if selector is None: raise DomainError("DID MUX 선택자를 찾을 수 없습니다.")
                        r.insert("muxes",structure_id=st,ordinal=rows[index]["ordinal"],selector_object_id=selector,default_structure_id=default)
                        for n,(lo,hi,case) in enumerate(cases):
                            r.insert("mux_cases",structure_id=st,ordinal=rows[index]["ordinal"],case_ordinal=n,lower_raw=str(lo),upper_raw=str(hi),case_structure_id=case)
                    oid=self._object("did",d["qualifier"],d["name"],texts=d["texts"])
                    r.insert("dids",id=oid,scope_id="did",identifier=d["identifier"],structure_id=st)
                    map_node(d,oid)
                    unverified(oid,d)
                    field_rows=r.all("SELECT data_object_id FROM structure_members WHERE structure_id=? ORDER BY ordinal",(st,))
                    for m,field in zip(d["members"],field_rows):
                        if field["data_object_id"]: map_node(m,field["data_object_id"])  # 비트필드 멤버는 import_members에서 대응시켰다
                for v in preview["variants"]:
                    for ref in v["did_refs"]:
                        did=mapped[ref]
                        num=r.one("SELECT identifier FROM dids WHERE id=?",(did,))["identifier"]
                        r.insert("variant_dids",variant_id=mapped[v["external_id"]],did_id=did,scope_id="did",identifier=num)
                # 진단 클래스(DCLTMPL 기준)·인스턴스·서비스. 공유 인스턴스는 변형마다 같은 객체를 참조한다.
                # 가져올 수 있는 서비스가 없는 인스턴스도 만든다. 그 서비스는 인스턴스 소유의 보존 원문으로 남는다.
                by_class={}
                for inst in preview.get("instances",[]): by_class.setdefault(inst["class"],[]).append(inst)
                classes,instance_ids={},{}
                for cls in preview.get("classes",[]):
                    cancel()
                    sids=[svc["sid"] for inst in by_class.get(cls["external_id"],[]) for svc in inst["services"] if svc["profile"]=="uds_fixed"]
                    # 역할은 자체 편집 기능이 쓰는 분류다. 고정 프로파일 서비스가 없으면 템플릿 분류(cls="ftm")만 근거로 쓴다.
                    role=ROLE.get(sids[0],"other") if sids else ("fault_memory" if cls.get("template_class")=="ftm" else "other")
                    cid=self._object("diagnostic_class",cls["qualifier"],cls["name"],texts=cls["texts"])
                    r.insert("diagnostic_classes",id=cid,role=role,singleton=int(cls["singleton"]))
                    map_node(cls,cid)
                    # 클래스 유형(DCLTMPL cls): ODX DIAG-SERVICE SEMANTIC의 근거(interchange.CLASS_SEMANTIC)
                    if cls.get("template_class"): attributes(cid,[{"key":"cdd.class_type","value_type":"text","value_text":cls["template_class"]}])
                    classes[cls["external_id"]]=cid
                for v in preview["variants"]:
                    for n,slot in enumerate(v.get("layout",[])):
                        r.insert("variant_classes",variant_id=mapped[v["external_id"]],class_id=classes[slot["class"]],ordinal=n)
                for inst in preview.get("instances",[]):
                    cancel()
                    iid=self._object("diagnostic_instance",inst["qualifier"],inst["name"],texts=inst["texts"])
                    r.insert("diagnostic_instances",id=iid,class_id=classes[inst["class"]],owner_variant_id=mapped.get(inst.get("owner")))
                    map_node(inst,iid)
                    attributes(iid,inst["attributes"])
                    instance_ids[inst["external_id"]]=iid
                for v in preview["variants"]:
                    for slot in v.get("layout",[]):
                        for k,ext in enumerate(slot["instances"]):
                            r.insert("variant_instances",variant_id=mapped[v["external_id"]],instance_id=instance_ids[ext],ordinal=k)
                for inst in preview.get("instances",[]):
                    iid=instance_ids[inst["external_id"]]
                    for svc in inst["services"]:
                        oid=self._object("service",svc["qualifier"],svc["name"],texts=svc["texts"])
                        fields=dict(instance_id=iid,sid=svc["sid"],subfunction=svc["subfunction"],routine_identifier=svc["routine_identifier"],physical=int(svc["physical"]),functional=int(svc["functional"]),suppress_positive=0,enabled=int(svc.get("enabled",True)),profile=svc["profile"])
                        r.insert("services",id=oid,**fields)
                        if svc["profile"]=="raw":
                            # 원문 배치: 헤더 자동 생성 없이 해석한 바이트·데이터·DID 참조를 그대로 구조로 만든다. 편집은 막는다.
                            r.update("objects",{"id":oid},{"read_only":1})
                            def layout_members(layout):
                                data=iter(import_members([v for k,v in layout if k=="data"]))
                                members=[]
                                for k,v in layout:
                                    if k=="literal": members.append({"kind":"literal","literal_bytes":v})
                                    elif k=="did": members.append({"kind":"did_data","did_id":mapped[v]})
                                    elif k=="dtc": members.append({"kind":"dtc"})
                                    elif k=="record": members.append({"kind":"fault_record","record_role":v})
                                    elif k=="iter":
                                        # 반복 항목은 전용 구조(이름 = CDD 반복 구성요소). 반복 멤버가 그 구조를 소유한다.
                                        item=self._object("structure",v["qualifier"] if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",v["qualifier"] or "") else "Items",v["name"],texts=v.get("texts") or None)
                                        r.insert("structures",id=item,kind="payload")
                                        self._members(item,layout_members(v["items"]))
                                        map_node(v,item)
                                        members.append({"kind":"iteration","nested_structure_id":item,"min_items":v["min_items"]})
                                    elif k=="mux":
                                        # MUX(21차): 케이스·기본 구조를 만들고, 선택자·케이스 행은 구조를 만든 뒤 넣는다(make_structure).
                                        def case_structure(name,items):
                                            sid=self._object("structure",name,name)
                                            r.insert("structures",id=sid,kind="payload")
                                            self._members(sid,case_members(items))
                                            return sid
                                        cases=[(c["lower"],c["upper"],case_structure(f"Case_{c['lower']}_{c['upper']}",c["members"])) for c in v["cases"]]
                                        default=case_structure("Case_Default",v["default"]) if v.get("default") is not None else None
                                        members.append({"kind":"mux","selector_external_id":v["selector"],"cases":cases,"default_structure_id":default})
                                    else: members.append(next(data))
                                return members
                            def case_members(items):
                                # 케이스 구조의 멤버: 데이터 객체·비트필드(import_members)와 빈 비트(GAPDATAOBJ → 예약 비트)
                                result=[]
                                for m in items:
                                    if m.get("kind")=="gap": result.append({"kind":"reserved","reserved_bits":m["bits"],"label":m.get("qualifier")})
                                    else: result.extend(import_members([m]))
                                return result
                            def make_structure(name,layout):
                                members=layout_members(layout)
                                st=self._structure(name,members)
                                rows=r.all("SELECT ordinal,data_object_id FROM structure_members WHERE structure_id=? ORDER BY ordinal",(st,))
                                for m,row in zip(members,rows):
                                    if m.get("kind")!="mux": continue
                                    selector=next((x["data_object_id"] for y,x in zip(members,rows) if y.get("kind","data")=="data" and y.get("external_id")==m["selector_external_id"]),None)
                                    if selector is None: raise DomainError("MUX 선택자를 같은 전문에서 찾을 수 없습니다.")
                                    r.insert("muxes",structure_id=st,ordinal=row["ordinal"],selector_object_id=selector,default_structure_id=m["default_structure_id"])
                                    for n,(lo,hi,case) in enumerate(m["cases"]):
                                        r.insert("mux_cases",structure_id=st,ordinal=row["ordinal"],case_ordinal=n,lower_raw=str(lo),upper_raw=str(hi),case_structure_id=case)
                                return st
                            for kind,layout in (("request",svc["request_layout"]),("positive",svc["positive_layout"])):
                                st=make_structure(svc["qualifier"]+"_"+kind,layout)
                                r.insert("telegrams",id=uid(),service_id=oid,kind=kind,structure_id=st)
                            for d in dict.fromkeys(svc["did_refs"]):
                                if not r.one("SELECT 1 FROM instance_dids WHERE instance_id=? AND did_id=? AND role='data'",(iid,mapped[d])):
                                    r.insert("instance_dids",instance_id=iid,did_id=mapped[d],role="data")
                            attributes(oid,[{"key":"cdd.profile_mismatch","value_type":"text","value_text":svc["profile_mismatch"]}])
                        else:
                            self._telegrams(oid,iid,svc["qualifier"],fields,mapped.get(svc["did_ref"]),import_members(svc["request_members"]),import_members(svc["positive_members"]))
                        for code in svc["nrcs"]: r.insert("service_nrcs",service_id=oid,code=code)
                        for g_index,row in svc["conditions"].items():
                            for state_oid,allowed in row.items():
                                r.insert("service_conditions",service_id=oid,group_id=group_ids[g_index],state_id=states[state_oid],allowed=int(allowed))
                        for g_index,src,dst in svc["transitions"]:
                            r.insert("service_transitions",service_id=oid,group_id=group_ids[g_index],from_state_id=states[src],to_state_id=states[dst])
                        map_node(svc,oid)
                        attributes(oid,svc["attributes"])
                        unverified(oid,svc)
                # 템플릿 목록(읽기 전용): NRC, 프로토콜 서비스 요약. Job 컨테이너는 이름·배치만 가져오고 내용은 원문으로 둔다.
                for n in preview.get("nrcs",[]):
                    oid=self._object("nrc",n["qualifier"],n["name"],texts=n["texts"] or None)
                    r.update("objects",{"id":oid},{"read_only":1})
                    r.insert("nrcs",id=oid,code=n["code"])
                    map_node(n,oid)
                for ps in preview.get("protocol_services",[]):
                    oid=self._object("protocol_service",ps["qualifier"],ps["name"],texts=ps["texts"] or None)
                    r.update("objects",{"id":oid},{"read_only":1})
                    r.insert("protocol_services",id=oid,**{k:ps[k] for k in ("request_text","positive_text","negative_text","template_count","service_count")})
                    map_node(ps,oid)
                for j in preview.get("jobs",[]):
                    oid=self._object("job_container",j["qualifier"],j["name"],texts=j["texts"] or None)
                    r.update("objects",{"id":oid},{"read_only":1,"supported":0})
                    r.insert("job_containers",id=oid,owner_variant_id=mapped.get(j["owner"]))
                    map_node(j,oid)
                for v in preview["variants"]:
                    for k,ext in enumerate(v.get("jobs",[])): r.insert("variant_jobs",variant_id=mapped[v["external_id"]],job_id=mapped[ext],ordinal=k)
                # DTC: faultMemory 레코드. 변형 소속은 그 레코드를 가진 고장 메모리 인스턴스의 소속을 따른다.
                for d in preview.get("dtcs",[]):
                    cancel()
                    oid=self._object("dtc",d["qualifier"],d["name"],texts=d["texts"] or None)
                    r.insert("dtcs",id=oid,scope_id="dtc",code=d["code"])
                    for v in d["variants"]: r.insert("variant_dtcs",variant_id=mapped[v],dtc_id=oid,scope_id="dtc",code=d["code"])
                    map_node(d,oid)
                    attributes(oid,d["attributes"])
                # 고장 메모리 레코드(schema 8): 스냅샷 레코드 번호·DID 목록(기본, DTC별), 확장 데이터 레코드 번호·구조.
                for inst in preview.get("instances",[]):
                    fm=inst.get("fault_memory")
                    if not fm: continue
                    iid=instance_ids[inst["external_id"]]
                    for rec in fm["snapshot_records"]:
                        oid=self._object("snapshot_record",rec["qualifier"],rec["name"],texts=rec["texts"] or None)
                        r.insert("snapshot_records",id=oid,instance_id=iid,number=rec["number"])
                        map_node(rec,oid)
                    for n,did in enumerate(fm["default_dids"]): r.insert("snapshot_default_dids",instance_id=iid,ordinal=n,did_id=mapped[did])
                    for record_ref,dids in fm["dtc_dids"]:
                        dtc=mapped.get(record_ref)
                        if dtc is None: continue  # 가져오지 못한 DTC 레코드의 대체 목록은 원문(FAULTMEMORY)에만 남는다
                        for n,did in enumerate(dids): r.insert("snapshot_dtc_dids",instance_id=iid,dtc_id=dtc,ordinal=n,did_id=mapped[did])
                    for rec in fm["extended"]:
                        st=self._structure(rec["qualifier"]+"_Data",import_members(rec["members"]))
                        oid=self._object("extended_record",rec["qualifier"],rec["name"],texts=rec["texts"] or None)
                        r.insert("extended_records",id=oid,instance_id=iid,number=rec["number"],structure_id=st)
                        map_node(rec,oid)
                # 27차: 변형 식별 패턴(ODX ECU-VARIANT-PATTERN)과 미지원 서비스 NRC(ODX GLOBAL-NEG-RESPONSE).
                for v in preview["variants"]:
                    for p_index,details in enumerate(v.get("patterns") or [],1):
                        for o_index,det in enumerate(details,1):
                            r.insert("variant_patterns",variant_id=mapped[v["external_id"]],pattern=p_index,ordinal=o_index,service_id=mapped[det["service"]],
                                     parameter=det["parameter"],expected_value=det["expected"])
                for k,ext in enumerate(preview.get("unsupported_service_nrcs") or []):
                    if mapped.get(ext) and not r.one("SELECT 1 FROM unsupported_service_nrcs WHERE nrc_id=?",(mapped[ext],)):
                        r.insert("unsupported_service_nrcs",nrc_id=mapped[ext],ordinal=k)
                for n in preview["unsupported"]:
                    cancel()
                    owner=mapped.get(n["owner_external_id"])
                    r.insert("unsupported_nodes",source_file_id=source,owner_object_id=owner,node_path=n["node_path"],reason_code=n["reason_code"],semantic=n["semantic"],raw_xml=n["raw_xml"])
                    r.insert("import_issues",source_file_id=source,object_id=owner,node_path=n["node_path"],code="UNSUPPORTED_FEATURE" if n["semantic"] else "PRESERVED_METADATA",severity="warning" if n["semantic"] else "info",blocks_export=n["semantic"],message=n["reason_code"])
            # 가져오기는 새 프로젝트의 시작점이다. 실행 취소로 빈 프로젝트가 되지 않도록 이력을 비운다.
            r.db.execute("DELETE FROM undo_stack")
            r.db.execute("UPDATE history_state SET cursor_position=0")
            self.session.touch()
        except Exception:
            if self.session: self.session.close(discard=True)
            self.session=previous
            raise
        if previous: previous.close(discard=True)
        return self.overview()
