"""25차 P3 도구 대화상자. 작업 스레드는 부르지 않고, 다시 조회가 필요하면 신호로 주 창에 넘긴다(주 창이 set_*로 채운다).

- ReplaceTypeDialog: 데이터 타입 바꾸기(M6-3, Replace Data Type)
- VariantWizardDialog: 변형 마법사(M6-4, Variant Wizard)
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .domain import make_qualifier, qualifier as check_qualifier, DomainError

CONFLICT = QColor("#b3261e")
GREY = QColor("#8a94a6")


def _item(text, tip=None):
    cell = QTableWidgetItem("" if text is None else str(text))
    cell.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    if tip: cell.setToolTip(tip)
    return cell


def _buttons(dialog):
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    box.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
    box.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
    box.accepted.connect(dialog.accept); box.rejected.connect(dialog.reject)
    return box


# ---------------------------------------------------------------- 데이터 타입 바꾸기(M6-3)
class ReplaceTypeDialog(QDialog):
    """Replace Data Type: 바꿀 타입(대화상자를 연 타입, 고정)의 사용처 목록에서 행을 골라 새 타입으로 한 명령에 연결한다.
    충돌 열에 이유가 있는 행은 고를 수 없다. 모두 선택 = 충돌 없는 행 전부, 모두 해제. 길이가 바뀌어 뒤 필드가 움직이는 것은 영향 열에 보인다.
    새 타입을 바꾸면 target_requested(타입 ID)를 보내고, 주 창이 새 미리보기로 set_preview를 부른다."""
    target_requested = Signal(str)
    CHECK, KIND, PATH, OBJECT, IMPACT, CONFLICT_COL = range(6)

    def __init__(self, preview, parent=None):
        super().__init__(parent)
        self.setWindowTitle("데이터 타입 바꾸기")
        self.resize(1080, 600); self.setSizeGripEnabled(True)
        self.preview = None
        self.initial = None
        self.unchecked = set()   # 사용자가 끈 행(새 타입을 바꿔도 유지)
        self._filling = False
        box = QVBoxLayout(self)
        form = QFormLayout()
        old = preview["old"]
        self.old_label = QLabel(f"<b>{self._esc(old['name'])}</b> · {self._esc(old.get('length_text') or '')} · {self._esc(old.get('encoding') or '')}"
                                f"{(' · ' + self._esc(old['unit'])) if old.get('unit') else ''} <span style='color:#677a92'>(이 대화상자를 연 타입, 바꿀 수 없음)</span>")
        form.addRow("바꿀 타입", self.old_label)
        self.target = QComboBox(); self.target.setObjectName("replaceTarget"); self.target.setEditable(True)
        self.target.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.target.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.target.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.target.addItem("(새 타입을 고르세요)", None)
        for t in preview["types"]:
            self.target.addItem(f"{t['name']} · {t.get('length_text') or ''} · {t['encoding']}" + (f" · {t['unit']}" if t.get("unit") else ""), t["id"])
        self.target.currentIndexChanged.connect(self._target_changed)
        form.addRow("새 타입", self.target)
        box.addLayout(form)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("<b>아래에서 고른 사용처에 적용</b>"))
        hint = QLabel("충돌이 있는 행은 고를 수 없습니다. 적용은 한 명령(실행 취소 한 번)입니다."); hint.setStyleSheet("color:#677a92")
        bar.addWidget(hint, 1)
        self.select_all = QPushButton("모두 선택 (충돌 제외)"); self.select_all.clicked.connect(self.check_all); bar.addWidget(self.select_all)
        self.select_none = QPushButton("모두 해제"); self.select_none.clicked.connect(self.check_none); bar.addWidget(self.select_none)
        box.addLayout(bar)
        self.table = QTableWidget(0, 6); self.table.setObjectName("replaceUses")
        self.table.setHorizontalHeaderLabels(["", "종류", "경로", "데이터 객체", "영향", "충돌"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        for col, mode in ((self.CHECK, QHeaderView.ResizeMode.ResizeToContents), (self.KIND, QHeaderView.ResizeMode.ResizeToContents),
                          (self.PATH, QHeaderView.ResizeMode.Interactive), (self.OBJECT, QHeaderView.ResizeMode.Interactive),
                          (self.IMPACT, QHeaderView.ResizeMode.Interactive), (self.CONFLICT_COL, QHeaderView.ResizeMode.Stretch)):
            header.setSectionResizeMode(col, mode)
        self.table.setColumnWidth(self.PATH, 330); self.table.setColumnWidth(self.OBJECT, 170); self.table.setColumnWidth(self.IMPACT, 260)
        self.table.itemChanged.connect(self._item_changed)
        box.addWidget(self.table, 1)
        note = QLabel("사본을 만든 뒤 사용처를 하나씩 고치는 대신 고른 사용처의 연결을 한 번에 바꿉니다. 기본값·상수의 원시값은 그대로 두고 새 타입으로 다시 검사합니다. "
                      "원본 보존 항목·비트필드 컨테이너를 넘는 길이 같은 행은 막습니다. 비슷한 타입 합치기(Replace Similar Data Types)는 범위 밖입니다.")
        note.setWordWrap(True); note.setStyleSheet("color:#677a92"); box.addWidget(note)
        bottom = QHBoxLayout()
        self.count = QLabel(""); self.count.setObjectName("replaceCount"); bottom.addWidget(self.count, 1)
        self.buttons = _buttons(self); bottom.addWidget(self.buttons)
        box.addLayout(bottom)
        self.set_preview(preview)

    @staticmethod
    def _esc(text):
        import html
        return html.escape(str(text or ""))

    def _target_changed(self, *_):
        target = self.target.currentData()
        if target and target != (self.preview or {}).get("new_id"):
            self.set_busy(True)
            self.target_requested.emit(target)
        elif not target and self.initial is not None:
            self.set_preview(self.initial)

    def set_busy(self, busy):
        for w in (self.table, self.select_all, self.select_none):
            w.setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False if busy else self._can_accept())
        if busy: self.count.setText("영향·충돌을 계산하는 중…")

    def set_preview(self, preview):
        """미리보기(행·충돌·영향)로 표를 다시 채운다. 사용자가 끈 행은 계속 끈 채로 둔다."""
        if not preview.get("new_id") and getattr(self, "initial", None) is None: self.initial = preview  # 새 타입을 고르기 전 상태(되돌릴 때 쓴다)
        self.preview = preview
        if preview.get("new_id") and self.target.currentData() != preview["new_id"]:
            self.target.blockSignals(True); self.target.setCurrentIndex(max(0, self.target.findData(preview["new_id"]))); self.target.blockSignals(False)
        self._filling = True
        try:
            rows = preview["rows"]
            self.table.setRowCount(len(rows))
            for i, row in enumerate(rows):
                conflict = row.get("conflict")
                check = QTableWidgetItem()
                flags = Qt.ItemFlag.ItemIsSelectable
                if not conflict and preview.get("new_id"): flags |= Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                check.setFlags(flags)
                check.setData(Qt.ItemDataRole.UserRole, row["id"])
                selected = bool(preview.get("new_id")) and not conflict and row["id"] not in self.unchecked
                check.setCheckState(Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
                if conflict: check.setToolTip(conflict)
                self.table.setItem(i, self.CHECK, check)
                shared = f"\n공유 변형 {row['shared']}개" if (row.get("shared") or 1) > 1 else ""
                values = []
                if row.get("default_text") is not None: values.append(f"기본값 {row['default_text']}")
                if row.get("constant_text") is not None: values.append(f"상수 {row['constant_text']}")
                self.table.setItem(i, self.KIND, _item(row.get("kind_text")))
                self.table.setItem(i, self.PATH, _item(row.get("path"), (row.get("path") or "") + shared))
                self.table.setItem(i, self.OBJECT, _item(row.get("name"), " · ".join(values) or None))
                impact = _item(row.get("impact") or ("없음" if preview.get("new_id") and not conflict else ""), row.get("impact"))
                if not row.get("impact"): impact.setForeground(GREY)
                self.table.setItem(i, self.IMPACT, impact)
                cell = _item(conflict or ("없음" if preview.get("new_id") else ""), conflict)
                cell.setForeground(CONFLICT if conflict else GREY)
                self.table.setItem(i, self.CONFLICT_COL, cell)
                for col in range(1, 6):
                    if conflict: self.table.item(i, col).setBackground(QColor("#fbf4f3"))
        finally:
            self._filling = False
        self.set_busy(False)
        self._update_count()

    def _item_changed(self, cell):
        if self._filling or cell.column() != self.CHECK: return
        oid = cell.data(Qt.ItemDataRole.UserRole)
        if cell.checkState() == Qt.CheckState.Checked: self.unchecked.discard(oid)
        else: self.unchecked.add(oid)
        self._update_count()

    def check_all(self):
        """Select All: 충돌 없는 행을 모두 고른다."""
        self.unchecked.clear(); self._set_all(True)

    def check_none(self):
        self.unchecked = {r["id"] for r in (self.preview or {}).get("rows", [])}; self._set_all(False)

    def _set_all(self, on):
        self._filling = True
        try:
            for i in range(self.table.rowCount()):
                check = self.table.item(i, self.CHECK)
                if check.flags() & Qt.ItemFlag.ItemIsUserCheckable: check.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        finally:
            self._filling = False
        self._update_count()

    def selected_ids(self):
        return [self.table.item(i, self.CHECK).data(Qt.ItemDataRole.UserRole) for i in range(self.table.rowCount())
                if self.table.item(i, self.CHECK).checkState() == Qt.CheckState.Checked]

    def selected_rows(self):
        wanted = set(self.selected_ids())
        return [r for r in (self.preview or {}).get("rows", []) if r["id"] in wanted]

    def _can_accept(self):
        return bool((self.preview or {}).get("new_id")) and bool(self.selected_ids())

    def _update_count(self):
        rows = (self.preview or {}).get("rows", [])
        blocked = sum(1 for r in rows if r.get("conflict"))
        text = f"사용처 {len(rows)}곳 · {len(self.selected_ids())}곳 선택" + (f" · 충돌 {blocked}곳" if blocked else "")
        if not (self.preview or {}).get("new_id"): text += " · 새 타입을 고르세요"
        self.count.setText(text)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self._can_accept())

    def values(self):
        return {"old_type_id": self.preview["old"]["id"], "new_type_id": self.preview["new_id"], "data_object_ids": self.selected_ids()}


# ---------------------------------------------------------------- 변형 마법사(M6-4)
ACTION_LABELS = {"ref": "참조", "copy": "사본", "none": "사용 안 함"}
ACTION_ICONS = {"ref": "↪", "copy": "⧉", "none": "○", "mixed": "◐"}
MODES = (("ref", "참조로 (기본)"), ("copy", "사본으로"), ("empty", "빈 변형 (필수 인스턴스만)"))


class VariantWizardDialog(QDialog):
    """Variant Wizard([VW-R040]·[VW-R050]·[VW-R100]·[VW-R200]·[VW-R500]·[VW-R510]).
    만들기: 참조로(기본) / 사본으로(필수·사본 불가 인스턴스는 참조) / 빈 변형(기본 변형의 필수 인스턴스만, 기준 변형 비활성).
    왼쪽 = 기준 변형의 진단 클래스(묶음 동작), 오른쪽 = 고른 클래스의 인스턴스(이름·필수·동작). 동작은 참조·사본·사용 안 함이고 섞이면 (혼합)이다.
    필수 인스턴스는 사용 안 함을 고를 수 없다. copy_allowed=False(설정 '변형 마법사 사본 허용' 끔)면 사본을 두지 않는다.
    기준 변형을 바꾸면 source_requested(변형 ID)를 보내고 주 창이 set_items로 다시 채운다. 확인하면 values()가 create_variant 명령 입력이다."""
    source_requested = Signal(str)
    ICON, NAME, REQUIRED, ACTION = range(4)

    def __init__(self, items, parent=None, copy_allowed=True, source=None):
        super().__init__(parent)
        self.setWindowTitle("변형 마법사")
        self.resize(1000, 640); self.setSizeGripEnabled(True)
        self.copy_allowed = bool(copy_allowed)
        self.items = None
        self.actions = {}
        self.current_class = None
        self._updating = False
        variants = items.get("variants", [])
        self._qualifiers = {str(v.get("qualifier", "")).casefold() for v in variants}
        box = QVBoxLayout(self)
        form = QFormLayout()
        n = len(variants) + 1
        self.name = QLineEdit(f"Variant {n}"); self.name.setObjectName("wizardName")
        self.qualifier = QLineEdit(make_qualifier(self.name.text(), "variant")); self.qualifier.setObjectName("wizardQualifier")
        self._generated = self.qualifier.text()
        self.name.textChanged.connect(self._name_changed); self.qualifier.textChanged.connect(self._check)
        form.addRow("이름", self.name)
        qrow = QHBoxLayout(); qrow.addWidget(self.qualifier, 1)
        self.qualifier_state = QLabel(""); self.qualifier_state.setStyleSheet("color:#677a92"); qrow.addWidget(self.qualifier_state)
        form.addRow("Qualifier", qrow)
        mode_row = QHBoxLayout(); self.mode_group = QButtonGroup(self); self.mode_buttons = {}
        for mode, label in MODES:
            button = QRadioButton(label); button.setObjectName(f"wizardMode_{mode}")
            self.mode_group.addButton(button); self.mode_buttons[mode] = button; mode_row.addWidget(button)
            button.toggled.connect(lambda on, m=mode: on and self.set_mode(m))
        mode_row.addStretch()
        if not self.copy_allowed:
            self.mode_buttons["copy"].setEnabled(False)
            self.mode_buttons["copy"].setToolTip("설정(편집 탭)의 '변형 마법사에서 인스턴스를 사본으로 만들기 허용'이 꺼져 있습니다.")
        form.addRow("만들기", mode_row)
        self.source = QComboBox(); self.source.setObjectName("wizardSource")
        for v in variants: self.source.addItem(v["name"] + (" (기본 변형)" if v.get("is_base") else ""), v["id"])
        self.source.currentIndexChanged.connect(self._source_changed)
        form.addRow("기준 변형", self.source)
        all_row = QHBoxLayout()
        self.all_actions = self._action_combo(required=False, blocked=False); self.all_actions.setObjectName("wizardAllActions")
        self.all_actions.activated.connect(lambda *_: self._set_many(self._all_ids(), self.all_actions.currentData()))
        all_row.addWidget(self.all_actions)
        hint = QLabel("여기서 바꾸면 아래 모든 인스턴스가 바뀝니다. 아래가 섞이면 (혼합)입니다."); hint.setStyleSheet("color:#677a92"); all_row.addWidget(hint, 1)
        form.addRow("모든 인스턴스", all_row)
        box.addLayout(form)
        split = QSplitter(Qt.Orientation.Horizontal); split.setChildrenCollapsible(False)
        self.class_list = QListWidget(); self.class_list.setObjectName("wizardClasses")
        self.class_list.currentRowChanged.connect(self._class_chosen)
        split.addWidget(self.class_list)
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 4); self.table.setObjectName("wizardInstances")
        self.table.setHorizontalHeaderLabels(["", "인스턴스", "필수", "동작"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.ICON, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.REQUIRED, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.ACTION, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(self.ACTION, 150)
        rl.addWidget(self.table)
        split.addWidget(right); split.setSizes([280, 700])
        box.addWidget(split, 1)
        legend = QLabel("아이콘은 새 변형에서의 상태입니다: ↪ 참조, ⧉ 사본, ○ 사용 안 함, ◐ 혼합. 필수 인스턴스는 사용 안 함을 고를 수 없습니다. "
                        "사본은 '인스턴스 사본' 명령과 같은 규칙이며(DID는 공유), 보존 원문·원문 배치·고장 메모리 레코드가 걸린 인스턴스는 참조만 됩니다. "
                        "모든 인스턴스가 참조 또는 사본이면 기준 변형의 설명·사용자 속성도 복사합니다.")
        legend.setWordWrap(True); legend.setStyleSheet("color:#677a92"); box.addWidget(legend)
        bottom = QHBoxLayout()
        self.summary = QLabel(""); self.summary.setObjectName("wizardSummary"); bottom.addWidget(self.summary, 1)
        self.buttons = _buttons(self); bottom.addWidget(self.buttons)
        box.addLayout(bottom)
        self.mode = "ref"
        self.set_items(items)
        self.mode_buttons["ref"].setChecked(True)
        if source: self.source.setCurrentIndex(max(0, self.source.findData(source)))
        self._check()

    # ------------------------------------------------------------ 자료
    def set_items(self, items):
        """기준 변형의 클래스·인스턴스로 다시 채운다. 동작은 지금 방식의 처음 값으로 둔다."""
        self.items = items
        self.source.blockSignals(True); self.source.setCurrentIndex(max(0, self.source.findData(items["source"]))); self.source.blockSignals(False)
        self.instances = {i["id"]: i for c in items["classes"] for i in c["instances"]}
        self.init_actions()
        self.class_list.blockSignals(True)
        self.class_list.clear()
        for c in items["classes"]:
            entry = QListWidgetItem(c["name"]); entry.setData(Qt.ItemDataRole.UserRole, c["id"]); self.class_list.addItem(entry)
        self.class_list.blockSignals(False)
        if self.class_list.count(): self.class_list.setCurrentRow(0)
        else: self._class_chosen(-1)
        self.refresh_summary()

    def init_actions(self):
        """방식별 처음 동작([VW-R040]): 참조로 = 모두 참조, 사본으로 = 사본(필수·사본 불가는 참조), 빈 변형 = 필수만 참조."""
        self.actions = {}
        for inst in self.instances.values():
            if self.mode == "ref" or inst["required"]: action = "ref"
            elif self.mode == "copy": action = "ref" if inst.get("copy_block") or not self.copy_allowed else "copy"
            else: action = "none"
            self.actions[inst["id"]] = action

    def set_mode(self, mode):
        if mode == self.mode and self.actions: return
        self.mode = mode
        self.source.setEnabled(mode != "empty")  # [VW-R050] 빈 변형은 기준 변형을 고르지 않는다(기본 변형의 필수 인스턴스)
        self.source.setToolTip("빈 변형은 기본 변형의 필수 인스턴스로 만듭니다." if mode == "empty" else "새 변형의 기준(on basis of) 변형")
        base = (self.items or {}).get("base")
        if mode == "empty" and self.items and self.items["source"] != base:
            self.source_requested.emit(base); return  # 주 창이 기본 변형 자료로 set_items를 부른다
        self.init_actions()
        self._class_chosen(self.class_list.currentRow())
        self.refresh_summary()

    def _source_changed(self, *_):
        wanted = self.source.currentData()
        if wanted and self.items and wanted != self.items["source"]: self.source_requested.emit(wanted)

    # ------------------------------------------------------------ 표
    def _action_combo(self, required, blocked, value=None):
        combo = QComboBox()
        for action in ("ref", "copy", "none"):
            if action == "copy" and (blocked or not self.copy_allowed): continue
            if action == "none" and required: continue
            combo.addItem(ACTION_LABELS[action], action)
        if value == "mixed": combo.addItem("(혼합)", "mixed")
        if value is not None: combo.setCurrentIndex(max(0, combo.findData(value)))
        return combo

    def _class_ids(self, class_id):
        cls = next((c for c in (self.items or {}).get("classes", []) if c["id"] == class_id), None)
        return [i["id"] for i in cls["instances"]] if cls else []

    def _all_ids(self):
        return list(self.instances)

    def summary_of(self, ids):
        values = {self.actions[i] for i in ids if i in self.actions}
        return values.pop() if len(values) == 1 else "mixed" if values else "ref"

    def _class_chosen(self, row):
        self.current_class = self.class_list.item(row).data(Qt.ItemDataRole.UserRole) if 0 <= row < self.class_list.count() else None
        cls = next((c for c in (self.items or {}).get("classes", []) if c["id"] == self.current_class), None)
        self.table.setRowCount(0)
        if cls is None: return
        self.table.setHorizontalHeaderLabels(["", cls["name"], "필수", "동작"])
        self.table.setRowCount(len(cls["instances"]) + 1)
        whole = self._class_ids(cls["id"])
        self.table.setItem(0, self.ICON, _item(ACTION_ICONS[self.summary_of(whole)]))
        head = _item("(클래스 전체)"); font = head.font(); font.setBold(True); head.setFont(font)
        self.table.setItem(0, self.NAME, head); self.table.setItem(0, self.REQUIRED, _item(""))
        combo = self._action_combo(False, False, self.summary_of(whole)); combo.setObjectName("wizardClassAction")
        combo.activated.connect(lambda *_, c=combo: self._set_many(self._class_ids(self.current_class), c.currentData()))
        self.table.setCellWidget(0, self.ACTION, combo)
        for r, inst in enumerate(cls["instances"], start=1):
            tip = f"{inst['name']}\n서비스: {inst.get('services') or '-'}" + (f"\n사본 불가: {inst['copy_block']}" if inst.get("copy_block") else "")
            self.table.setItem(r, self.ICON, _item(ACTION_ICONS[self.actions[inst["id"]]]))
            label = (inst["identifier_text"] + " " if inst.get("identifier_text") else "") + inst["name"]
            self.table.setItem(r, self.NAME, _item(label, tip))
            self.table.setItem(r, self.REQUIRED, _item("예" if inst["required"] else ""))
            combo = self._action_combo(inst["required"], bool(inst.get("copy_block")), self.actions[inst["id"]])
            combo.setToolTip("필수 인스턴스는 사용 안 함을 고를 수 없습니다." if inst["required"] else ("사본 불가: " + inst["copy_block"]) if inst.get("copy_block") else "")
            combo.activated.connect(lambda *_, i=inst["id"], c=combo: self._set_many([i], c.currentData()))
            self.table.setCellWidget(r, self.ACTION, combo)

    def _set_many(self, ids, action):
        """동작을 여러 인스턴스에 넣는다. 필수 인스턴스의 사용 안 함, 사본 불가 인스턴스의 사본은 참조로 둔다."""
        if action not in ACTION_LABELS: return
        for i in ids:
            inst = self.instances.get(i)
            if inst is None: continue
            value = action
            if value == "none" and inst["required"]: value = "ref"
            if value == "copy" and (inst.get("copy_block") or not self.copy_allowed): value = "ref"
            self.actions[i] = value
        # 콤보 신호 안에서 표를 다시 만들지 않도록 다음 이벤트에서 그린다.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: (self._class_chosen(self.class_list.currentRow()), self.refresh_summary()))

    def refresh_summary(self):
        for row in range(self.class_list.count()):
            entry = self.class_list.item(row)
            cls = next((c for c in self.items["classes"] if c["id"] == entry.data(Qt.ItemDataRole.UserRole)), None)
            state = self.summary_of(self._class_ids(cls["id"])) if cls else "ref"
            entry.setText(f"{ACTION_ICONS[state]}  {cls['name']}  ·  {'(혼합)' if state == 'mixed' else ACTION_LABELS[state]}" if cls else "")
        total = self.summary_of(self._all_ids())
        self.all_actions.blockSignals(True)
        self.all_actions.clear()
        for action in ("ref", "copy", "none"):
            if action == "copy" and not self.copy_allowed: continue
            self.all_actions.addItem(ACTION_LABELS[action], action)
        if total == "mixed": self.all_actions.addItem("(혼합)", "mixed")
        self.all_actions.setCurrentIndex(max(0, self.all_actions.findData(total)))
        self.all_actions.blockSignals(False)
        counts = {a: sum(1 for v in self.actions.values() if v == a) for a in ACTION_LABELS}
        counts_text = (self.items or {}).get("counts", {})
        shared = "DID·DTC·Job은 기준 변형의 것을 참조" if self.mode != "empty" else "DID는 넣은 인스턴스가 쓰는 것만, DTC·Job 없음"
        keep = " · 변형 설명·사용자 속성 복사" if all(v != "none" for v in self.actions.values()) else ""
        self.summary.setText(f"참조 {counts['ref']} · 사본 {counts['copy']} · 사용 안 함 {counts['none']} · {shared}"
                             + (f" (DID {counts_text.get('dids', 0)} · DTC {counts_text.get('dtcs', 0)} · Job {counts_text.get('jobs', 0)})" if self.mode != "empty" else "") + keep)
        self._check()

    # ------------------------------------------------------------ 이름·Qualifier
    def _name_changed(self, text):
        # M2-1 규칙: Qualifier가 이름에서 만든 값과 같으면 이름을 따라간다(직접 고치면 멈춘다).
        if self.qualifier.text() == self._generated:
            self._generated = make_qualifier(text, "variant")
            self.qualifier.setText(self._generated)
        else:
            self._generated = make_qualifier(text, "variant")
        self._check()

    def problem(self):
        if not self.name.text().strip(): return "이름을 입력하세요."
        try: check_qualifier(self.qualifier.text().strip())
        except DomainError as e: return str(e)
        if self.qualifier.text().strip().casefold() in self._qualifiers: return "같은 Qualifier의 변형이 이미 있습니다."
        if not self.items: return "기준 변형 자료를 읽는 중입니다."
        return None

    def _check(self, *_):
        problem = self.problem() if hasattr(self, "buttons") else None
        if hasattr(self, "qualifier_state"):
            self.qualifier_state.setText(problem or ("이름을 따라 자동" if self.qualifier.text() == self._generated else "직접 입력"))
            self.qualifier_state.setStyleSheet("color:#b3261e" if problem else "color:#677a92")
        if hasattr(self, "buttons"): self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(problem is None)

    def accept(self):
        problem = self.problem()
        if problem: QMessageBox.warning(self, "변형 마법사", problem); return
        super().accept()

    def values(self):
        return {"name": self.name.text().strip(), "qualifier": self.qualifier.text().strip(), "mode": self.mode,
                "source_id": self.items["source"], "instances": dict(self.actions)}
