"""목록 칸 편집(M1-1·M1-2·M2-2). 어느 칸을 고칠 수 있는지, 편집기, 확정한 값을 명령으로 바꾸는 규칙.

도움말 근거: [LIST-R410] F2로 목록에서 바로 편집, [UI-R300] 편집 중 Tab·Shift+Tab은 다음·이전 편집 칸,
[UI-R300-TABLast] 마지막 행 마지막 칸의 Tab은 새 요소, [LIST-R420] 여러 행을 골라 F2 → 모든 선택 행에 넣기,
Keyboard Shortcuts: ENTER 확정, ESC 취소. 결정 D1: F2 = 칸 편집, 속성 대화상자는 두 번 클릭·Alt+Enter·'속성…' 단추.
명령은 작업 스레드의 기존 명령(update_common·update_dtc·update_did·update_state·bulk_edit·set_dtc_attributes)을 쓴다.
"""
from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtWidgets import QStyledItemDelegate, QLineEdit, QComboBox

from .domain import make_qualifier, qualifier_follows
from .presentation import parse_sae_dtc

LOCAL_PREFIXES = ("src:", "template:", "group:")
# 이름(과 Qualifier)을 목록에서 고칠 수 있는 객체 종류. update_common으로 바꾼다.
NAME_KINDS = {"did", "dtc", "service", "data_type", "state", "variant", "ecu", "diagnostic_instance"}
# 번호 칸: (객체 종류, 필드) → 비트 수. 한 행에만 넣는다(번호는 목록 안에서 유일).
NUMBER_FIELDS = {("dtc", "code"): 24, ("did", "identifier"): 16, ("state", "code"): 8}
READONLY_ROW = "이 행은 객체가 아니어서(원문·템플릿·묶음 행) 고칠 수 없습니다."


def is_local(row):
    return str(row.get("id", "")).startswith(LOCAL_PREFIXES)


def cell_spec(row, field, label="", record_items=None, list_kind=None):
    """칸 편집 규칙 → (spec, 이유). spec = {"type": text|qualifier|number|enum|uns, "multi": 여러 행 입력 가능, ...}.
    고칠 수 없으면 (None, 사용자에게 보일 이유)."""
    kind = row.get("kind")
    if list_kind == "dependency":
        return None, "의존성 칸은 여러 행을 골라 F2로 한 상태 칸을 일괄 변경합니다."
    if is_local(row): return None, READONLY_ROW
    if str(row.get("id", "")).startswith("unsupported:"): return None, "보존 항목은 원문 그대로 두는 읽기 전용 항목입니다."
    if row.get("read_only") or not row.get("supported", 1):
        return None, "읽기 전용 객체입니다(원문 배치·미지원 의미를 보존한 객체)."
    if kind not in NAME_KINDS:
        return None, "이 목록은 읽기 전용입니다."
    label = label or field
    if field == "name": return {"type": "text", "multi": True}, None
    if field == "qualifier": return {"type": "qualifier", "multi": False}, None
    if field == "description" and kind == "variant": return {"type": "text", "multi": True}, None
    if field == "manufacturer" and kind == "ecu": return {"type": "text", "multi": False}, None
    if (kind, field) in NUMBER_FIELDS: return {"type": "number", "multi": False, "bits": NUMBER_FIELDS[(kind, field)]}, None
    if kind == "dtc" and field.startswith("attr:"):
        item = (record_items or {}).get(field[5:])
        if item is None:
            return None, f"'{label}'은(는) 원본 레코드 항목 템플릿이 없는 속성이라 읽기 전용입니다."
        return {"type": "enum" if item["kind"] == "enum" else "uns" if item["kind"] == "uns" else "text", "multi": True,
                "choices": item.get("choices") or [], "required": bool(item.get("required")), "key": item["key"]}, None
    return None, f"'{label}' 열은 목록에서 고칠 수 없습니다. 속성 대화상자(Alt+Enter)에서 고칩니다."


def editor_text(row, field, spec, dtc_format="hex"):
    """편집기를 열 때의 글자. 번호는 표시 형식(0x·SAE)대로, 열거는 원시값."""
    value = row.get(field)
    if spec["type"] == "number":
        if not isinstance(value, int): return ""
        if spec["bits"] == 24:
            if dtc_format == "sae":
                from .presentation import sae_dtc
                return sae_dtc(value)
            return f"0x{value:06X}"
        return f"0x{value:0{spec['bits'] // 4}X}"
    if spec["type"] == "enum":
        raw = row.get("raw:" + spec["key"])
        try: return None if raw in (None, "") else int(str(raw), 0)
        except ValueError: return None
    return "" if value is None else str(value)


def parse_number(text, bits):
    """번호 칸 입력: 0x 16진·10진, DTC는 SAE 문자형(C002011)도 받는다. 범위는 작업 스레드가 검사한다."""
    text = str(text or "").strip()
    if bits == 24:
        sae = parse_sae_dtc(text)
        if sae is not None: return f"0x{sae:06X}"
    return text


def display_value(spec, value):
    """낙관적 갱신(명령 결과 전)에 칸에 보일 값."""
    if spec["type"] == "number":
        try: return int(str(value), 0)
        except ValueError: return value
    if spec["type"] == "enum":
        choice = next((c for c in spec.get("choices") or [] if c["raw"] == value), None)
        return choice["label"] if choice else ""
    return value


def attribute_entry(spec, value):
    """레코드 항목 한 칸 → _set_dtc_attributes 항목. 열거는 원시값(None = 비움), 그 밖은 글자."""
    if spec["type"] == "enum": return {"key": spec["key"], "raw": value}
    return {"key": spec["key"], "value": "" if value is None else str(value)}


def build_command(rows, field, spec, value):
    """확정한 값 → (명령, payload). rows가 둘 이상이면 여러 행 입력(한 명령, 실행 취소 한 번)이다.
    이름을 바꿀 때 Qualifier가 아직 이름에서 만든 값이면 함께 바꾼다([QUAL-R020], 가져온 CDD 객체는 제외)."""
    if len(rows) > 1:
        ids = [row["id"] for row in rows]
        if field in ("name", "description"): return "bulk_edit", {"ids": ids, "field": field, "value": value}
        if field.startswith("attr:"): return "set_dtc_attributes", {"ids": ids, "attributes": [attribute_entry(spec, value)]}
        raise ValueError("이 열은 한 행씩만 고칠 수 있습니다.")
    row = rows[0]
    kind, oid = row.get("kind"), row["id"]
    if field.startswith("attr:"): return "update_dtc", {"id": oid, "attributes": [attribute_entry(spec, value)]}
    if spec["type"] == "number":
        number = parse_number(value, spec["bits"])
        if kind == "dtc": return "update_dtc", {"id": oid, "code": number}
        if kind == "did": return "update_did", {"id": oid, "identifier": number}
        return "update_state", {"id": oid, "group_id": row.get("group_id"), "code": number}
    payload = {"id": oid, field: value}
    if field == "name" and not row.get("imported"):
        number = row.get("code") if kind in ("dtc", "state") else row.get("identifier") if kind == "did" else None
        if qualifier_follows(row.get("name", ""), row.get("qualifier", ""), kind, number):
            payload["qualifier"] = make_qualifier(value, kind, number)
    return "update_common", payload


class CellDelegate(QStyledItemDelegate):
    """목록 칸 편집기. 열거 레코드 항목은 콤보, 그 밖은 한 줄 입력이다. Tab·Shift+Tab은 확정 후 다음·이전 편집 칸으로
    옮겨 다시 편집한다(host.move_edit). Enter 확정, Esc 취소는 Qt 기본 동작이다.
    host: spec_at(index) → spec, editor_value(index, spec), move_edit(row, column, step)를 가진 주 창."""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self.host = host

    def createEditor(self, parent, option, index):
        spec = self.host.spec_at(index)
        if spec and spec["type"] == "enum":
            combo = QComboBox(parent)
            if not spec.get("required"): combo.addItem("(없음)", None)
            for c in spec.get("choices") or []: combo.addItem(f"{c['label']} ({c['raw']})", c["raw"])
            editor = combo
        else:
            editor = QLineEdit(parent)
            if spec and spec["type"] == "number": editor.setPlaceholderText("0x 16진 또는 10진")
        editor.setProperty("cell", (index.row(), index.column()))
        return editor

    def setEditorData(self, editor, index):
        spec = self.host.spec_at(index)
        value = self.host.editor_value(index, spec) if spec else ""
        if isinstance(editor, QComboBox):
            position = editor.findData(value)
            if position < 0 and value is not None:
                editor.addItem(f"{index.data()} ({value}) · 템플릿에 없음", value); position = editor.count() - 1
            editor.setCurrentIndex(max(0, position))
        else:
            editor.setText("" if value is None else str(value))
        retry = getattr(self.host, "_cell_retry_text", None)
        if retry is not None and isinstance(editor, QLineEdit):
            # 거부된 입력을 칸에 되살린다(작업 스레드가 명령을 거부한 경우).
            editor.setText(retry); self.host._cell_retry_text = None

    def setModelData(self, editor, model, index):
        value = editor.currentData() if isinstance(editor, QComboBox) else editor.text().strip()
        model.setData(index, value, Qt.ItemDataRole.EditRole)

    def eventFilter(self, editor, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            cell = editor.property("cell")
            step = -1 if event.key() == Qt.Key.Key_Backtab or event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            self.commitData.emit(editor)
            self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)
            if cell is not None: QTimer.singleShot(0, lambda: self.host.move_edit(cell[0], cell[1], step))
            return True
        return super().eventFilter(editor, event)
