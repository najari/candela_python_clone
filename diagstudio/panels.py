"""24차 P2 화면 보강. 작업 스레드는 부르지 않고 명령은 신호로 주 창에 넘긴다.

- 사용자 설정 도우미(QSettings, M6-2)와 최근 파일 목록(M6-1)
- WheelGuard: 포커스 없는 콤보·스핀 상자 위 휠 막기(M2-4 [UI-R1410]·[UI-R1420])
- SheetDelegate·EditTable: 칸 편집기(열거 = 콤보, 여러 줄 = Enter 줄바꿈·Ctrl+Enter 확정, Esc 취소, Tab 다음 칸)
- PropertySheet: 선택 객체 창의 속성 시트(M2-2)
- StartPage: 시작 화면(M6-1), SettingsDialog: 설정 대화상자(M6-2)
- QualifierOverviewDialog·RecreateQualifierDialog: Qualifier 개요 편집·다시 만들기(M6-5)
- ColumnInfoDialog: 열 정보(M1-7)
"""
import os
from pathlib import Path

from PySide6.QtCore import Qt, QEvent, QObject, QStandardPaths, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QKeyEvent
from PySide6.QtWidgets import (QAbstractItemDelegate, QAbstractItemView, QAbstractSpinBox, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QRadioButton, QSpinBox, QSplitter, QStyledItemDelegate, QTabWidget,
                               QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .domain import QUALIFIER_KIND_PREFIX, QUALIFIER_MAX, DomainError, key as text_key, make_qualifier, qualifier as check_qualifier

GREY = QColor("#8a94a6")
CHANGED = QColor("#205db8")

# ---------------------------------------------------------------- 사용자 설정(M6-2)
# 사용자 설정은 이 PC(QSettings)에 두고 프로젝트 파일에는 넣지 않는다(04 명세). DTC 표시 형식은 기존 키 'dtc_format'을 쓴다.
OPTION_DEFAULTS = {
    "recent_count": 4,              # Options General: 최근 파일 수 0~16, 기본 4 [MENU-R340-Number]
    "grid_lines": True,             # Options General: Grid Lines
    "colored_rows": True,           # Options General: Colored Rows
    "tree_identifiers": True,       # Options Display: 트리의 인스턴스 식별자
    "path_display": "name",         # Options Display: 경로를 이름(npath)·Qualifier(qpath)로
    "nrc_on_open": True,            # Options Edit: 인스턴스(서비스)를 열 때 NRC 목록
    "new_type_byte_order": "big",   # Options Edit: 새 데이터 타입 바이트 순서
    "confirm_shared_edit": True,    # Options Confirmation: 여러 변형이 참조하는 객체 수정
    "notify_migration": True,       # Options Confirmation: 이전 형식 파일을 열 때 알림
    "examples_folder": "",          # [OPEN-R310-VectorExamples] 빠른 접근의 예제 폴더(비우면 기본 위치를 찾는다)
    "new_row": True,                # 표 끝 새 항목 행(＋)
    "fit_columns": True,            # 목록을 열 때 열 너비를 내용에 맞춘다
    # 25차(P3)
    "hex_zero_fill": True,          # Options Display: 16진 값을 앞자리 0으로 채우기(값 크기만큼, M2-5)
    "wizard_copy": True,            # Options Edit: 변형 마법사에서 인스턴스 사본 허용 [VW-R040-CopyInsens](M6-4)
}
RECENT_MAX = 16
DEFAULT_EXAMPLES = r"C:\Users\Public\Documents\Vector\CANdelaStudio\13.0\Examples"
DTC_FORMAT_CHOICES = (("0x 16진 (0x402011)", "hex"), ("SAE (C002011)", "sae"))


def app_settings():
    """사용자 설정 저장소(QSettings). 환경 변수 DIAGAUTHOR_SETTINGS(ini 파일 경로)가 있으면 그 파일을 쓴다.
    테스트가 Windows 사용자 설정(레지스트리)을 읽거나 바꾸지 않게 한다(24차 남은 것)."""
    from PySide6.QtCore import QSettings
    path = os.environ.get("DIAGAUTHOR_SETTINGS")
    return QSettings(path, QSettings.Format.IniFormat) if path else QSettings("DiagAuthor", "DiagAuthor")


def option(settings, name):
    default = OPTION_DEFAULTS[name]
    if settings is None: return default
    value = settings.value("options/" + name, default)
    if isinstance(default, bool):
        return value if isinstance(value, bool) else str(value).strip().lower() in ("true", "1", "yes", "on")
    if isinstance(default, int):
        try: return int(value)
        except (TypeError, ValueError): return default
    return default if value is None else str(value)


def set_option(settings, name, value):
    if settings is not None: settings.setValue("options/" + name, value)


def examples_folder(settings):
    folder = option(settings, "examples_folder").strip()
    if folder: return folder
    return DEFAULT_EXAMPLES if os.path.isdir(DEFAULT_EXAMPLES) else ""


def _paths(value):
    if value is None: return []
    if isinstance(value, str): return [value] if value else []
    try: return [str(v) for v in value if v]
    except TypeError: return []


def _same(a, b):
    try: return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))
    except (TypeError, ValueError): return False


def recent_files(settings):
    return _paths(settings.value("recent/files", [])) if settings is not None else []


def add_recent_file(settings, path):
    """[MENU-R340] 최근 파일 맨 앞에 넣는다. 같은 파일은 한 번만, 최대 16개를 저장하고 설정 개수만큼 보인다."""
    if settings is None or not path: return
    files = [p for p in recent_files(settings) if not _same(p, path)]
    settings.setValue("recent/files", [str(path)] + files[:RECENT_MAX - 1])


def remove_recent_file(settings, path):
    """[MENU-R340-RemoveFile] 목록에서 빼기. 파일은 지우지 않는다."""
    if settings is None: return
    settings.setValue("recent/files", [p for p in recent_files(settings) if not _same(p, path)])


def recent_folders(files):
    """[Open-R340] 최근 폴더 = 최근 파일의 폴더(순서 유지, 중복 제거)."""
    out = []
    for p in files:
        folder = str(Path(p).parent)
        if folder and not any(_same(folder, f) for f in out): out.append(folder)
    return out


# ---------------------------------------------------------------- 휠 막기(M2-4)
class WheelGuard(QObject):
    """[UI-R1410]·[UI-R1420] 포커스 없는 콤보·스핀 상자 위에서 휠을 굴려도 값이 바뀌지 않는다. 휠은 부모(스크롤 영역·표)로 넘어간다.
    Qt 기본 포커스 정책(WheelFocus)은 휠이 먼저 포커스를 주므로 StrongFocus로 바꾼다(Polish 때, 설치 때 이미 있는 위젯)."""
    TYPES = (QComboBox, QAbstractSpinBox)

    def install(self, app, root=None):
        app.installEventFilter(self)
        if root is not None:
            for kind in self.TYPES:
                for widget in root.findChildren(kind): self.fix(widget)

    @staticmethod
    def fix(widget):
        if widget.focusPolicy() == Qt.FocusPolicy.WheelFocus: widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Type.Wheel:
            if isinstance(obj, self.TYPES) and not obj.hasFocus():
                event.ignore()  # 무시한 채 True: 이 위젯은 받지 않고 QApplication이 부모로 넘긴다.
                return True
        elif kind == QEvent.Type.Polish and isinstance(obj, self.TYPES):
            self.fix(obj)
        return False


# ---------------------------------------------------------------- 칸 편집기
MULTIPLE = "__diagauthor_multiple__"  # 속성 시트의 '(여러 값)' 표시값


def later(signal, *args):
    """이벤트 처리가 끝난 뒤 신호를 보낸다. 그 사이 보낸 객체가 지워졌으면 아무것도 하지 않는다."""
    def fire():
        try: signal.emit(*args)
        except RuntimeError: pass
    QTimer.singleShot(0, fire)


class SheetDelegate(QStyledItemDelegate):
    """칸 편집기. 칸의 INFO 역할에 {"kind": "enum"|"multiline"|"text", "choices": [(글자, 값)…]}, VALUE 역할에 지금 값을 둔다.
    열거는 콤보, 여러 줄은 QPlainTextEdit(Enter 줄바꿈, Ctrl+Enter 확정 [DOUI-R830]), 그 밖은 한 줄 입력이다.
    Esc는 바꾸지 않고 닫고 Tab·Shift+Tab은 확정 후 다음·이전 칸이다(Qt 기본, [DOUI-R810]·[DOUI-R815]).
    확정하면 모델을 바꾸지 않고 committed(행, 열, 값)을 보낸다. 명령이 끝나면 호출자가 표를 다시 채운다.
    값이 그대로면 보내지 않는다. '(여러 값)' 칸은 값을 넣었을 때만 보낸다([UI-R450-multi])."""
    INFO = Qt.ItemDataRole.UserRole + 1
    VALUE = Qt.ItemDataRole.UserRole + 2
    committed = Signal(int, int, object)

    def createEditor(self, parent, option_, index):
        info = index.data(self.INFO) or {}
        kind = info.get("kind")
        if kind == "enum":
            combo = QComboBox(parent)
            for label, data in info.get("choices", []): combo.addItem(str(label), data)
            return combo
        if kind == "multiline":
            edit = QPlainTextEdit(parent)
            edit.setTabChangesFocus(True)
            edit.setToolTip("Enter = 줄바꿈, Ctrl+Enter = 확정, Esc = 취소")
            return edit
        return QLineEdit(parent)

    def updateEditorGeometry(self, editor, option_, index):
        rect = option_.rect
        if isinstance(editor, QPlainTextEdit): rect.setHeight(max(rect.height(), 96))
        editor.setGeometry(rect)

    def setEditorData(self, editor, index):
        value = index.data(self.VALUE)
        if value == MULTIPLE: value = None
        if isinstance(editor, QComboBox):
            found = editor.findData(value)
            editor.setCurrentIndex(found if found >= 0 else 0)
        elif isinstance(editor, QPlainTextEdit):
            editor.setPlainText("" if value is None else str(value))
        else:
            editor.setText("" if value is None else str(value))

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox): value = editor.currentData()
        elif isinstance(editor, QPlainTextEdit): value = editor.toPlainText()
        else: value = editor.text()
        old = index.data(self.VALUE)
        if old == MULTIPLE:
            if value in (None, "", MULTIPLE): return
        elif value == old or (value in (None, "") and old in (None, "")):
            return
        # 편집기가 닫힌 뒤에 알린다(받는 쪽이 확인 창을 열거나 표를 다시 채워도 편집 중인 표를 건드리지 않게).
        later(self.committed, index.row(), index.column(), value)

    def eventFilter(self, editor, event):
        if isinstance(editor, QPlainTextEdit) and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.commitData.emit(editor)
                self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)
                return True
        return super().eventFilter(editor, event)


class EditTable(QTableWidget):
    """F2로 칸 편집을 시작하는 표. 주 창의 F2(목록 칸 편집) 단축키보다 먼저 F2를 받는다.
    고칠 수 없는 칸에서 F2를 누르면 blocked(이유)를 보낸다(D1: 상태 줄·출력 창에 이유)."""
    blocked = Signal(str)

    def __init__(self, rows=0, columns=0, parent=None):
        super().__init__(rows, columns, parent)
        self.reason = None  # (행, 열) -> 이유 글자

    def current_editable(self):
        cell = self.currentItem()
        return cell is not None and bool(cell.flags() & Qt.ItemFlag.ItemIsEditable)

    def event(self, e):
        if e.type() == QEvent.Type.ShortcutOverride and isinstance(e, QKeyEvent) and e.key() == Qt.Key.Key_F2 \
                and self.state() != QAbstractItemView.State.EditingState:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_F2 and self.state() != QAbstractItemView.State.EditingState and not self.current_editable():
            row, col = self.currentRow(), self.currentColumn()
            text = self.reason(row, col) if self.reason and row >= 0 else ""
            self.blocked.emit(text or "이 칸은 고칠 수 없습니다.")
            return
        super().keyPressEvent(e)


def _cell(text, tip=None, editable=False):
    cell = QTableWidgetItem("" if text is None else str(text))
    flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    if editable: flags |= Qt.ItemFlag.ItemIsEditable
    cell.setFlags(flags)
    if tip: cell.setToolTip(tip)
    return cell


# ---------------------------------------------------------------- 속성 시트(M2-2)
class PropertySheet(QWidget):
    """선택 객체 창의 속성 시트([LIST-R1400]·[LIST-R1420], M2-2). 선택한 객체들의 공통 속성과 값이다.
    값이 다르면 '(여러 값)'(회색 기울임, [UI-D410]), 값을 넣으면 선택한 모두에 적용한다(되돌리기는 실행 취소, [UI-R450-multi]).
    고칠 수 없는 행은 회색이다. 값 칸을 두 번 누르거나 F2로 고치고, 확정하면 edit_requested(키, 값, 시트)를 보낸다."""
    edit_requested = Signal(str, object, object)
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("표에서 객체를 선택하면 고칠 수 있는 속성이 보입니다."); self.title.setObjectName("sheetTitle")
        self.title.setStyleSheet("color:#50617b")
        box.addWidget(self.title)
        self.table = EditTable(0, 2); self.table.setObjectName("propertySheet")
        self.table.setHorizontalHeaderLabels(["속성", "값"]); self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        header = self.table.horizontalHeader(); header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents); header.setStretchLastSection(True)
        self.delegate = SheetDelegate(self.table); self.table.setItemDelegateForColumn(1, self.delegate)
        self.delegate.committed.connect(self._committed)
        self.table.reason = self._reason
        self.table.blocked.connect(self.message)
        box.addWidget(self.table, 1)
        self.sheet = None

    def clear(self):
        self.sheet = None
        self.table.setRowCount(0)
        self.title.setText("표에서 객체를 선택하면 고칠 수 있는 속성이 보입니다.")

    def set_sheet(self, sheet):
        self.sheet = sheet if sheet and sheet.get("rows") else None
        rows = (self.sheet or {}).get("rows", [])
        self.table.setRowCount(len(rows))
        if not self.sheet:
            self.clear(); return
        count = sheet.get("count", 1)
        kinds = ", ".join(sheet.get("kinds") or [])
        state = "" if sheet.get("editable") else " · 읽기 전용"
        self.title.setText((f"{count}개 선택 · 공통 속성 ({kinds}){state} — 값을 넣으면 모두에 적용합니다." if count > 1
                            else f"선택 객체 속성{state} — 값 칸을 두 번 누르거나 F2로 고칩니다."))
        for r, row in enumerate(rows):
            name = _cell(row["label"], row["key"])
            self.table.setItem(r, 0, name)
            value = self._value_cell(row)
            self.table.setItem(r, 1, value)
            if not row.get("editable"): name.setForeground(QBrush(GREY))
        self.table.resizeRowsToContents()

    @staticmethod
    def _display(row):
        value = row.get("value")
        if row.get("kind") == "enum":
            if row.get("text"): return row["text"]
            choice = next((c for c in row.get("choices", []) if c["raw"] == value), None)
            return f"{choice['label']} ({choice['raw']})" if choice else ("" if value is None else str(value))
        text = "" if value is None else str(value)
        if row.get("multiline") and "\n" in text: text = text.split("\n", 1)[0] + " …"
        return text

    def _value_cell(self, row):
        multiple = row.get("multiple")
        text = "(여러 값)" if multiple else self._display(row)
        tip = ("선택한 객체마다 값이 다릅니다. 값을 넣으면 선택한 모두에 적용합니다." if multiple
               else (str(row.get("value") or "") if row.get("multiline") else None))
        cell = _cell(text, tip, editable=bool(row.get("editable")))
        if row.get("kind") == "enum":
            choices = ([("(여러 값)", MULTIPLE)] if multiple else []) + ([] if row.get("required") and not multiple else [("(없음)", None)])
            choices += [(f"{c['label']} ({c['raw']})", c["raw"]) for c in row.get("choices", [])]
            cell.setData(SheetDelegate.INFO, {"kind": "enum", "choices": choices})
        else:
            cell.setData(SheetDelegate.INFO, {"kind": "multiline" if row.get("multiline") else "text"})
        cell.setData(SheetDelegate.VALUE, MULTIPLE if multiple else row.get("value"))
        if multiple:
            font = cell.font(); font.setItalic(True); cell.setFont(font); cell.setForeground(QBrush(GREY))
        elif not row.get("editable"):
            cell.setForeground(QBrush(GREY))
        return cell

    def _reason(self, row, _col):
        if not self.sheet or row >= len(self.sheet["rows"]): return ""
        entry = self.sheet["rows"][row]
        if entry["key"] == "kind": return "종류는 바꿀 수 없습니다."
        if entry["key"] in ("qualifier", "code") and self.sheet.get("count", 1) > 1:
            return "Qualifier·DTC 코드는 객체 하나를 골랐을 때만 고칩니다."
        return "읽기 전용 객체입니다(원본 템플릿·원문 배치·미지원 객체 또는 읽기 전용 프로젝트)."

    def _committed(self, row, _column, value):
        if self.sheet and 0 <= row < len(self.sheet["rows"]):
            self.edit_requested.emit(self.sheet["rows"][row]["key"], value, self.sheet)


# ---------------------------------------------------------------- 시작 화면(M6-1)
class StartPage(QWidget):
    """프로젝트가 없을 때 주 창 가운데의 시작 화면(M6-1). CANdelaStudio의 File 탭 Open 페이지 역할을 한다.
    최근 파일([MENU-R340], ✕ = 목록에서 빼기 [MENU-R340-RemoveFile], 개수는 설정 [MENU-R340-Number]), 최근 폴더([Open-R340]),
    빠른 접근(예제 폴더 [OPEN-R310-VectorExamples]·바탕 화면·문서), 새 프로젝트·열기·CDD 가져오기·설정 단추, 끌어 놓기 안내.
    파일을 주 창 어디에 놓아도 연다(주 창이 처리): .diagproj는 열고 .cdd·.cddt는 가져오기 미리보기로 보낸다."""
    open_requested = Signal(str)
    folder_requested = Signal(str)
    command_requested = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setObjectName("startPage")
        outer = QVBoxLayout(self); outer.setContentsMargins(8, 8, 8, 8)
        body = QHBoxLayout(); outer.addLayout(body, 1)
        left = QVBoxLayout()
        for key_, label, tip in (("new", "새 프로젝트…", "ECU 정보와 기본 변형으로 새 프로젝트를 만듭니다 (Ctrl+N)."),
                                 ("open", "열기…", ".diagproj 프로젝트를 열거나 CDD·CDDT를 가져옵니다 (Ctrl+O)."),
                                 ("cdd", "CDD·CDDT 가져오기…", "CANdela 문서·템플릿을 가져오기 미리보기로 엽니다."),
                                 ("settings", "설정…", "최근 파일 수·표시·편집·확인·목록 설정")):
            button = QPushButton(label); button.setToolTip(tip)
            button.clicked.connect(lambda _=False, k=key_: self.command_requested.emit(k))
            left.addWidget(button)
        left.addSpacing(10)
        left.addWidget(QLabel("빠른 접근"))
        self.quick = QTreeWidget(); self.quick.setObjectName("quickAccess"); self.quick.setHeaderHidden(True); self.quick.setRootIsDecorated(False)
        self.quick.itemActivated.connect(self._quick_activated)
        left.addWidget(self.quick, 1)
        body.addLayout(left, 1)
        right = QVBoxLayout()
        self.files_label = QLabel("최근 파일"); right.addWidget(self.files_label)
        self.files = QTreeWidget(); self.files.setObjectName("recentFiles"); self.files.setColumnCount(3)
        self.files.setHeaderLabels(["파일", "폴더", ""]); self.files.setRootIsDecorated(False); self.files.setUniformRowHeights(True)
        header = self.files.header(); header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents); header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.files.itemActivated.connect(self._file_activated)
        self.files.itemClicked.connect(self._file_clicked)
        self.files.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.files.customContextMenuRequested.connect(self._file_menu)
        right.addWidget(self.files, 2)
        right.addWidget(QLabel("최근 폴더"))
        self.folders = QTreeWidget(); self.folders.setObjectName("recentFolders"); self.folders.setHeaderHidden(True); self.folders.setRootIsDecorated(False)
        self.folders.itemActivated.connect(lambda item, _c: later(self.folder_requested, item.data(0, Qt.ItemDataRole.UserRole)))
        right.addWidget(self.folders, 1)
        body.addLayout(right, 3)
        self.drop_hint = QLabel("주 창 어디에나 파일을 끌어 놓으세요 — .diagproj는 열고, .cdd·.cddt는 가져오기 미리보기로 보냅니다.")
        self.drop_hint.setObjectName("dropHint"); self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter); self.drop_hint.setWordWrap(True)
        self.drop_hint.setStyleSheet("border: 2px dashed #b9c7dc; border-radius: 8px; padding: 16px; color: #50617b;")
        outer.addWidget(self.drop_hint)
        self.refresh()

    def refresh(self):
        count = max(0, min(RECENT_MAX, option(self.settings, "recent_count")))
        files = recent_files(self.settings)[:count]
        self.files_label.setText("최근 파일" if count else "최근 파일 (설정에서 개수를 0으로 두어 목록을 쓰지 않습니다)")
        self.files.clear()
        for path in files:
            p = Path(path)
            item = QTreeWidgetItem([p.name, str(p.parent), "✕"])
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            item.setToolTip(0, path); item.setToolTip(1, str(p.parent)); item.setToolTip(2, "목록에서 빼기 (파일은 지우지 않습니다)")
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignCenter)
            item.setForeground(2, QBrush(QColor("#b3261e")))
            if not p.exists():
                for col in (0, 1): item.setForeground(col, QBrush(GREY))
                item.setToolTip(0, path + "\n파일이 없습니다.")
            self.files.addTopLevelItem(item)
        self.folders.clear()
        for folder in recent_folders(recent_files(self.settings)[:max(count, 1) if count else 0]):
            item = QTreeWidgetItem([folder]); item.setData(0, Qt.ItemDataRole.UserRole, folder); item.setToolTip(0, "이 폴더에서 열기…")
            self.folders.addTopLevelItem(item)
        self.quick.clear()
        places = [("예제 폴더", examples_folder(self.settings)),
                  ("바탕 화면", QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation)),
                  ("문서", QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation))]
        for label, folder in places:
            item = QTreeWidgetItem([label]); item.setData(0, Qt.ItemDataRole.UserRole, folder or "")
            if folder: item.setToolTip(0, folder)
            else:
                item.setForeground(0, QBrush(GREY)); item.setToolTip(0, "설정 → 일반에서 예제 폴더를 지정하세요.")
            self.quick.addTopLevelItem(item)

    def _quick_activated(self, item, _column=0):
        folder = item.data(0, Qt.ItemDataRole.UserRole)
        if folder: later(self.folder_requested, folder)
        else: later(self.command_requested, "settings")

    def _file_activated(self, item, column=0):
        if column != 2: later(self.open_requested, item.data(0, Qt.ItemDataRole.UserRole))

    def _file_clicked(self, item, column):
        # 누른 항목을 신호 처리 중에 지우지 않도록 목록 갱신은 이벤트 처리 뒤에 한다.
        if column == 2:
            path = item.data(0, Qt.ItemDataRole.UserRole)
            QTimer.singleShot(0, lambda: self.remove(path))

    def remove(self, path):
        remove_recent_file(self.settings, path)
        self.refresh()

    def _file_menu(self, pos):
        item = self.files.itemAt(pos)
        if item is None: return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction("열기", lambda: self.open_requested.emit(path))
        menu.addAction("폴더에서 열기…", lambda: self.folder_requested.emit(str(Path(path).parent)))
        menu.addAction("목록에서 빼기", lambda: self.remove(path))
        menu.exec(self.files.viewport().mapToGlobal(pos))


# ---------------------------------------------------------------- 설정 대화상자(M6-2)
class SettingsDialog(QDialog):
    """설정(Options) 대화상자(M6-2): 일반·표시·편집·확인·목록 탭. 값은 이 PC(QSettings)에 저장한다.
    확인 = 저장하고 닫기, 적용 = 저장하고 열어 둠(applied 신호), 취소·Esc = 바꾸지 않고 닫기.
    개정 기록 묻기(결정 D6)는 출력 의미를 확인한 뒤 넣는다. 16진 0 채우기(M2-5)·변형 마법사 사본 허용(M6-4)은 25차에 넣었다."""
    applied = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("설정"); self.resize(600, 440); self.setSizeGripEnabled(True)
        box = QVBoxLayout(self)
        self.tabs = QTabWidget(); box.addWidget(self.tabs, 1)
        general = QWidget(); form = QFormLayout(general)
        self.recent_count = QSpinBox(); self.recent_count.setRange(0, RECENT_MAX); self.recent_count.setToolTip("시작 화면·파일 메뉴에 보일 최근 파일 수(0~16, 기본 4)")
        form.addRow("최근 파일 수", self.recent_count)
        self.grid_lines = QCheckBox("목록에 격자 선 보이기"); form.addRow("", self.grid_lines)
        self.colored_rows = QCheckBox("목록 행 배경을 번갈아 칠하기"); form.addRow("", self.colored_rows)
        folder = QHBoxLayout(); self.examples = QLineEdit(); self.examples.setPlaceholderText(f"비우면 기본 위치: {DEFAULT_EXAMPLES}")
        browse = QPushButton("찾아보기…"); browse.clicked.connect(self._browse); folder.addWidget(self.examples, 1); folder.addWidget(browse)
        form.addRow("예제 폴더", folder)
        self.tabs.addTab(general, "일반")
        display = QWidget(); form = QFormLayout(display)
        self.dtc_format = QComboBox()
        for label, data in DTC_FORMAT_CHOICES: self.dtc_format.addItem(label, data)
        form.addRow("DTC 코드 표시", self.dtc_format)
        self.tree_identifiers = QCheckBox("트리의 인스턴스 이름 앞에 식별자 보이기 (예: F190 VIN)"); form.addRow("", self.tree_identifiers)
        self.path_display = QComboBox(); self.path_display.addItem("이름 경로 (npath)", "name"); self.path_display.addItem("Qualifier 경로 (qpath)", "qualifier")
        self.path_display.setToolTip("목록 오른쪽 버튼의 '경로 복사'가 만드는 경로 형식입니다. '이름 경로 복사' 단추는 늘 이름 경로입니다.")
        form.addRow("경로 복사 형식", self.path_display)
        self.hex_zero_fill = QCheckBox("16진 값을 앞자리 0으로 채우기 (값 크기만큼, 예: 16비트 0x0041)")
        self.hex_zero_fill.setToolTip("구조 표의 기본값·상수를 16진으로 볼 때 비트 길이만큼 앞자리를 0으로 채웁니다. 저장 값은 같습니다.")
        form.addRow("", self.hex_zero_fill)
        self.tabs.addTab(display, "표시")
        edit = QWidget(); form = QFormLayout(edit)
        self.nrc_on_open = QCheckBox("서비스를 열 때 NRC 목록(NR 탭) 보이기"); form.addRow("", self.nrc_on_open)
        self.byte_order = QComboBox(); self.byte_order.addItem("Big endian", "big"); self.byte_order.addItem("Little endian", "little")
        form.addRow("새 데이터 타입 바이트 순서", self.byte_order)
        self.wizard_copy = QCheckBox("변형 마법사에서 인스턴스를 사본으로 만들기 허용")
        self.wizard_copy.setToolTip("끄면 변형 마법사는 참조로·빈 변형만 만들고, 인스턴스 동작은 참조·사용 안 함만 고릅니다.")
        form.addRow("", self.wizard_copy)
        self.tabs.addTab(edit, "편집")
        confirm = QWidget(); form = QFormLayout(confirm)
        self.confirm_shared = QCheckBox("여러 변형이 쓰는 객체를 선택 객체 창·데이터 배치에서 고칠 때 묻기"); form.addRow("", self.confirm_shared)
        self.notify_migration = QCheckBox("이전 형식 프로젝트를 열어 변환했을 때 알리기"); form.addRow("", self.notify_migration)
        self.tabs.addTab(confirm, "확인")
        lists = QWidget(); form = QFormLayout(lists)
        self.new_row = QCheckBox("표 끝에 새 항목 행(＋) 보이기"); form.addRow("", self.new_row)
        self.fit_columns = QCheckBox("목록을 열 때 열 너비를 내용에 맞추기"); form.addRow("", self.fit_columns)
        self.tabs.addTab(lists, "목록")
        note = QLabel("사용자 설정은 이 PC에 저장하며 프로젝트 파일에는 넣지 않습니다."); note.setStyleSheet("color:#677a92")
        box.addWidget(note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setText("적용")
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        box.addWidget(self.buttons)
        self.load()

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "예제 폴더", self.examples.text() or examples_folder(self.settings))
        if folder: self.examples.setText(folder)

    def load(self):
        s = self.settings
        self.recent_count.setValue(option(s, "recent_count")); self.grid_lines.setChecked(option(s, "grid_lines"))
        self.colored_rows.setChecked(option(s, "colored_rows")); self.examples.setText(option(s, "examples_folder"))
        fmt = str(s.value("dtc_format", "hex")) if s is not None else "hex"
        self.dtc_format.setCurrentIndex(max(0, self.dtc_format.findData(fmt)))
        self.tree_identifiers.setChecked(option(s, "tree_identifiers"))
        self.path_display.setCurrentIndex(max(0, self.path_display.findData(option(s, "path_display"))))
        self.nrc_on_open.setChecked(option(s, "nrc_on_open"))
        self.byte_order.setCurrentIndex(max(0, self.byte_order.findData(option(s, "new_type_byte_order"))))
        self.confirm_shared.setChecked(option(s, "confirm_shared_edit")); self.notify_migration.setChecked(option(s, "notify_migration"))
        self.new_row.setChecked(option(s, "new_row")); self.fit_columns.setChecked(option(s, "fit_columns"))
        self.hex_zero_fill.setChecked(option(s, "hex_zero_fill")); self.wizard_copy.setChecked(option(s, "wizard_copy"))

    def save(self):
        s = self.settings
        for name, value in (("recent_count", self.recent_count.value()), ("grid_lines", self.grid_lines.isChecked()),
                            ("colored_rows", self.colored_rows.isChecked()), ("examples_folder", self.examples.text().strip()),
                            ("tree_identifiers", self.tree_identifiers.isChecked()), ("path_display", self.path_display.currentData()),
                            ("nrc_on_open", self.nrc_on_open.isChecked()), ("new_type_byte_order", self.byte_order.currentData()),
                            ("confirm_shared_edit", self.confirm_shared.isChecked()), ("notify_migration", self.notify_migration.isChecked()),
                            ("new_row", self.new_row.isChecked()), ("fit_columns", self.fit_columns.isChecked()),
                            ("hex_zero_fill", self.hex_zero_fill.isChecked()), ("wizard_copy", self.wizard_copy.isChecked())):
            set_option(s, name, value)
        if s is not None: s.setValue("dtc_format", self.dtc_format.currentData())

    def apply(self):
        self.save(); self.applied.emit()

    def accept(self):
        self.save(); self.applied.emit()
        super().accept()


# ---------------------------------------------------------------- Qualifier 개요(M6-5)
VARIANT_KINDS = {"did", "dtc", "diagnostic_class", "diagnostic_instance", "service", "job_container"}
KIND_LABELS = {"did": "DID", "dtc": "DTC", "service": "서비스", "data_type": "데이터 타입", "data_object": "데이터 객체", "structure": "데이터 구조",
               "variant": "변형", "state": "상태", "state_group": "상태 그룹", "ecu": "ECU", "diagnostic_class": "진단 클래스",
               "diagnostic_instance": "진단 인스턴스", "job_container": "Job 컨테이너", "nrc": "부정 응답 코드", "protocol_service": "프로토콜 서비스",
               "snapshot_record": "스냅샷 레코드", "extended_record": "확장 데이터 레코드"}


def needs_sequence(name, kind):
    """이름에 영문·숫자가 없어 Qualifier가 종류 접두어로만 만들어지는지(결정 D3 ①: 종류 + 순번)."""
    return make_qualifier(name, kind, 1) != make_qualifier(name, kind, 2)


class RecreateQualifierDialog(QDialog):
    """Qualifier 다시 만들기([RCRQ-R200]·[QUAL-R200]): 최대 길이(8~128), 알고리즘(이름대로·대문자·소문자),
    대상(선택한 행 / 보이는 행 전체 / 문서 전체). 선택이 없으면 '선택한 행'은 비활성이고 '보이는 행 전체'가 기본이다."""
    def __init__(self, has_selection, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Qualifier 다시 만들기"); self.setSizeGripEnabled(True); self.resize(460, 330)
        box = QVBoxLayout(self)
        form = QFormLayout()
        self.length = QSpinBox(); self.length.setRange(8, QUALIFIER_MAX); self.length.setValue(QUALIFIER_MAX)
        form.addRow("최대 길이", self.length)
        box.addLayout(form)
        algorithm = QGroupBox("알고리즘"); al = QVBoxLayout(algorithm); self.case_group = QButtonGroup(self)
        for i, (label, data) in enumerate((("이름대로 (로마자로 바꾸고 _로 잇기)", "name"), ("대문자", "upper"), ("소문자", "lower"))):
            radio = QRadioButton(label); radio.setProperty("value", data); radio.setChecked(i == 0)
            self.case_group.addButton(radio, i); al.addWidget(radio)
        box.addWidget(algorithm)
        target = QGroupBox("대상"); tl = QVBoxLayout(target); self.target_group = QButtonGroup(self)
        for i, (label, data) in enumerate((("선택한 행", "selected"), ("보이는 행 전체", "visible"), ("문서 전체", "all"))):
            radio = QRadioButton(label); radio.setProperty("value", data)
            self.target_group.addButton(radio, i); tl.addWidget(radio)
        self.target_group.button(0).setEnabled(bool(has_selection))
        self.target_group.button(0 if has_selection else 1).setChecked(True)
        box.addWidget(target)
        note = QLabel("한글만 있는 이름은 종류 + 식별 번호(DTC_401011, DID_F190), 번호가 없으면 종류 + 순번입니다. 고칠 수 없는 객체는 건너뜁니다.")
        note.setWordWrap(True); note.setStyleSheet("color:#677a92"); box.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("확인"); buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

    def values(self):
        return {"max_length": self.length.value(), "case": self.case_group.checkedButton().property("value"),
                "target": self.target_group.checkedButton().property("value")}


class QualifierOverviewDialog(QDialog):
    """Qualifier 개요(M6-5, Qualifier Overview). 범위(문서 전체 / 현재 변형), '이름에서 만든 Qualifier 숨기기'(기본 꺼짐),
    '하위 객체의 Qualifier 보이기'(데이터 객체·구조·상태), 검색, 여러 행 선택·정렬. Qualifier 칸은 두 번 누르거나 F2로 고친다
    (고칠 수 없는 객체 제외, 검사 [QUAL-R120]·[QUAL-R130]). '다시 만들기…'는 대상 행의 Qualifier를 이름에서 다시 만든다.
    바꾼 값은 굵은 파란 글자로 보이고, 확인·적용을 누르면 한 명령(set_qualifiers)으로 반영한다. 다른 칸을 두 번 누르면 그 객체로 이동한다."""
    apply_requested = Signal(object)
    COLUMNS = (("kind_text", "종류"), ("name", "이름"), ("breadcrumb", "경로"), ("path", "객체 경로"), ("qualifier", "Qualifier"))
    QCOL = 4

    def __init__(self, result, parent=None, variant=None, variant_name="", readonly=False):
        super().__init__(parent)
        self.setWindowTitle("Qualifier 개요"); self.resize(1100, 680); self.setSizeGripEnabled(True)
        self.rows = [dict(r) for r in result.get("rows", [])]
        self.variant, self.readonly = variant, bool(readonly)
        self.result_row = None
        self.original = {}
        for r in self.rows:
            self.original.setdefault(r["id"], r["qualifier"])
            r["kind_text"] = KIND_LABELS.get(r["kind"], r["kind"])
            if not r.get("from_name") and needs_sequence(r.get("name", ""), r["kind"]):
                prefix = QUALIFIER_KIND_PREFIX.get(r["kind"], "Object")
                tail = str(r["qualifier"])[len(prefix) + 1:]
                r["from_name"] = str(r["qualifier"]).startswith(prefix + "_") and tail.isdigit()
        self.pending = {}
        self._filling = False
        box = QVBoxLayout(self)
        hint = QLabel("Qualifier 칸을 두 번 누르거나 F2로 고칩니다(영문·숫자·_, 숫자로 시작하지 않음, 128자까지). 다른 칸을 두 번 누르면 객체로 이동합니다. "
                      "바꾼 값은 확인·적용을 누를 때 한 명령으로 반영합니다(실행 취소 한 번).")
        hint.setWordWrap(True); box.addWidget(hint)
        bar = QHBoxLayout()
        self.scope = QComboBox(); self.scope.addItem("문서 전체", None)
        if variant: self.scope.addItem(f"현재 변형 ({variant_name})" if variant_name else "현재 변형", variant)
        self.scope.setToolTip("현재 변형 = 이 변형의 DID·DTC·클래스·인스턴스·서비스·Job(현재 namespace)")
        self.hide_generated = QCheckBox("이름에서 만든 Qualifier 숨기기")
        self.subordinate = QCheckBox("하위 객체의 Qualifier 보이기"); self.subordinate.setToolTip("데이터 객체·데이터 구조·상태")
        self.search = QLineEdit(); self.search.setPlaceholderText("검색 (종류·이름·경로·Qualifier)"); self.search.setClearButtonEnabled(True)
        for widget in (QLabel("범위"), self.scope, self.hide_generated, self.subordinate): bar.addWidget(widget)
        bar.addWidget(self.search, 1)
        box.addLayout(bar)
        self.table = QTableWidget(len(self.rows), len(self.COLUMNS)); self.table.setObjectName("qualifierOverview")
        self.table.setHorizontalHeaderLabels([label for _, label in self.COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        header = self.table.horizontalHeader(); header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive); header.setStretchLastSection(True)
        for col, width in enumerate((110, 220, 260, 280)): self.table.setColumnWidth(col, width)
        self._fill()
        self.table.itemChanged.connect(self._item_changed)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        box.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.recreate_button = QPushButton("다시 만들기…"); self.recreate_button.clicked.connect(self.recreate)
        self.recreate_button.setEnabled(not self.readonly)
        bottom.addWidget(self.recreate_button)
        self.status = QLabel(""); bottom.addWidget(self.status, 1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("확인"); self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        self.apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Apply); self.apply_button.setText("적용")
        self.apply_button.setVisible(not self.readonly); self.apply_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject)
        self.apply_button.clicked.connect(lambda: self.apply_requested.emit(self.pending_items()))
        bottom.addWidget(self.buttons)
        box.addLayout(bottom)
        for signal in (self.scope.currentIndexChanged, self.hide_generated.toggled, self.subordinate.toggled, self.search.textChanged):
            signal.connect(self.apply_filter)
        self.apply_filter()

    # -------------------------------------------------------------- 표
    def _fill(self):
        self._filling = True
        self.table.setSortingEnabled(False)
        try:
            for r, row in enumerate(self.rows):
                for c, (field, _) in enumerate(self.COLUMNS):
                    value = row.get(field)
                    editable = c == self.QCOL and bool(row.get("editable")) and not self.readonly
                    cell = _cell("—" if value in (None, "") and c != self.QCOL else value, None, editable)
                    cell.setData(Qt.ItemDataRole.UserRole, r)
                    if c == self.QCOL and not editable:
                        cell.setForeground(QBrush(GREY)); cell.setToolTip("고칠 수 없는 객체입니다(원본 템플릿·원문 배치·미지원 객체 또는 읽기 전용 프로젝트).")
                    self.table.setItem(r, c, cell)
        finally:
            self.table.setSortingEnabled(True)
            self._filling = False

    def _row_of(self, table_row):
        cell = self.table.item(table_row, 0)
        return None if cell is None else cell.data(Qt.ItemDataRole.UserRole)

    def current_qualifier(self, oid):
        return self.pending.get(oid, self.original.get(oid))

    def apply_filter(self, *_):
        scope = self.scope.currentData()
        hide_generated, subordinate = self.hide_generated.isChecked(), self.subordinate.isChecked()
        term = self.search.text().strip().casefold()
        shown = 0
        for t in range(self.table.rowCount()):
            index = self._row_of(t)
            if index is None: continue
            row = self.rows[index]
            visible = True
            if scope and not ((row["kind"] in VARIANT_KINDS and row.get("variant") == scope) or (row["kind"] == "variant" and row["id"] == scope)): visible = False
            if visible and row.get("subordinate") and not subordinate: visible = False
            if visible and hide_generated and row.get("from_name") and row["id"] not in self.pending: visible = False
            if visible and term:
                text = " ".join(str(row.get(f) or "") for f in ("kind_text", "name", "breadcrumb", "path")) + " " + str(self.current_qualifier(row["id"]))
                visible = term in text.casefold()
            self.table.setRowHidden(t, not visible)
            shown += int(visible)
        self._update_status(shown)

    def _update_status(self, shown=None):
        if shown is None: shown = sum(1 for t in range(self.table.rowCount()) if not self.table.isRowHidden(t))
        changes = len(self.pending_items())
        text = f"표시 {shown:,} / 전체 {len(self.rows):,}"
        if changes: text += f" · 바꾼 Qualifier {changes}개 (확인·적용 전)"
        self.status.setText(text)
        self.apply_button.setEnabled(bool(changes) and not self.readonly)

    def _double_clicked(self, table_row, column):
        index = self._row_of(table_row)
        if index is None: return
        if column == self.QCOL and self.rows[index].get("editable") and not self.readonly: return  # 칸 편집이 시작된다
        self.result_row = self.rows[index]
        self.accept()

    def _item_changed(self, cell):
        if self._filling or cell.column() != self.QCOL: return
        index = cell.data(Qt.ItemDataRole.UserRole)
        if index is None: return
        text = cell.text().strip()
        # 칸 편집이 끝난 뒤 검사한다(편집 확정 중에 경고 창을 열지 않는다).
        QTimer.singleShot(0, lambda: self._apply_edit(index, text))

    def _apply_edit(self, index, text):
        row = self.rows[index]
        try:
            if len(text) > QUALIFIER_MAX: raise DomainError(f"Qualifier는 {QUALIFIER_MAX}자까지입니다.")
            check_qualifier(text)
        except DomainError as e:
            QMessageBox.warning(self, "Qualifier", f"{text or '(빈 값)'}: {e}")
            self._show({row["id"]}); return
        self._set_pending(row["id"], text)

    def _set_pending(self, oid, value, show=True):
        if value == self.original.get(oid): self.pending.pop(oid, None)
        else: self.pending[oid] = value
        if show:
            self._show({oid})
            self._update_status()

    def _show(self, oids):
        """객체들의 모든 행(변형별 행)에 지금 값을 보인다(표를 한 번만 돈다). 바꾼 값은 굵은 파란 글자.
        값을 바꾸는 동안 정렬을 멈춘다(행이 움직이지 않게)."""
        oids = set(oids)
        if not oids: return
        self._filling = True
        self.table.setSortingEnabled(False)
        try:
            for t in range(self.table.rowCount()):
                index = self._row_of(t)
                if index is None or self.rows[index]["id"] not in oids: continue
                oid = self.rows[index]["id"]
                value = self.current_qualifier(oid)
                changed = oid in self.pending
                cell = self.table.item(t, self.QCOL)
                cell.setText(str(value))
                font = cell.font(); font.setBold(changed); cell.setFont(font)
                if changed:
                    cell.setForeground(QBrush(CHANGED)); cell.setToolTip(f"바꿈: {self.original.get(oid)} → {value} (확인·적용 전)")
                elif self.rows[index].get("editable") and not self.readonly:
                    cell.setForeground(QBrush(QColor("#1b283d"))); cell.setToolTip("")
        finally:
            self.table.setSortingEnabled(True)
            self._filling = False

    def pending_items(self):
        return [{"id": oid, "qualifier": q} for oid, q in self.pending.items() if q != self.original.get(oid)]

    def mark_applied(self):
        """적용한 값을 원래 값으로 삼는다(다시 확인을 눌러도 같은 명령을 또 보내지 않는다)."""
        done = list(self.pending)
        for oid in done: self.original[oid] = self.pending.pop(oid)
        self._show(done)
        self._update_status()

    def set_busy(self, busy):
        for button in (self.buttons.button(QDialogButtonBox.StandardButton.Ok), self.apply_button, self.recreate_button):
            button.setEnabled(not busy)
        if not busy:
            self.recreate_button.setEnabled(not self.readonly); self._update_status()

    # -------------------------------------------------------------- 다시 만들기
    def recreate(self):
        selected = sorted({self._row_of(i.row()) for i in self.table.selectionModel().selectedRows() if self._row_of(i.row()) is not None})
        dialog = RecreateQualifierDialog(bool(selected), self)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        v = dialog.values()
        if v["target"] == "selected": targets = selected
        elif v["target"] == "visible":
            targets = [self._row_of(t) for t in range(self.table.rowCount()) if not self.table.isRowHidden(t) and self._row_of(t) is not None]
            targets.sort()
        else: targets = list(range(len(self.rows)))
        target_ids = {self.rows[i]["id"] for i in targets}
        used = {}
        for row in self.rows:
            if row["id"] not in target_ids: used.setdefault(row["kind"], set()).add(text_key(self.current_qualifier(row["id"])))
        seq, seen, changed, skipped, touched = {}, set(), 0, 0, set()
        for i in targets:
            row = self.rows[i]
            oid = row["id"]
            if oid in seen: continue
            seen.add(oid)
            if not row.get("editable") or self.readonly:
                skipped += 1; continue
            ident = row.get("ident") if isinstance(row.get("ident"), int) else None
            if ident is None and needs_sequence(row.get("name", ""), row["kind"]):
                taken = used.setdefault(row["kind"], set())
                while True:
                    seq[row["kind"]] = seq.get(row["kind"], 0) + 1
                    value = make_qualifier(row.get("name", ""), row["kind"], seq[row["kind"]], v["max_length"], v["case"])
                    if text_key(value) not in taken: break
            else:
                value = make_qualifier(row.get("name", ""), row["kind"], ident, v["max_length"], v["case"])
            used.setdefault(row["kind"], set()).add(text_key(value))
            if value != self.current_qualifier(oid):
                self._set_pending(oid, value, show=False); touched.add(oid); changed += 1
        self._show(touched)
        self.apply_filter()
        self.status.setText(self.status.text() + f" · 다시 만듦 {changed}개" + (f", 고칠 수 없어 건너뜀 {skipped}개" if skipped else ""))


# ---------------------------------------------------------------- 열 정보(M1-7)
FIELD_TEXT = {"code": "DTC 코드(24비트 원시값). 표시 형식(0x·SAE)은 목록 위 선택 상자·설정에서 바꿉니다.", "name": "이름(현재 언어)",
              "qualifier": "Qualifier(식별 이름). 영문·숫자·_, 128자까지.", "shared": "이 객체를 함께 쓰는 변형 수", "identifier": "DID 번호(16비트)",
              "sid": "서비스 ID", "subfunction": "Subfunction", "diagnostic_class": "DID를 쓰는 진단 클래스", "did_data": "DID 데이터 객체 이름",
              "supported": "지원 여부", "fault_memory": "고장 메모리 레코드에서 쓰는지"}


class ColumnInfoDialog(QDialog):
    """열 정보(M1-7, DTCs 'Show Column Info'): 목록 열의 이름·Qualifier·종류·필수·설명·값 목록.
    DTC 레코드 항목 열은 원본 CDD 템플릿에서 읽은 값이며 표시 전용이다."""
    def __init__(self, title, columns, info, current=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"열 정보 · {title}"); self.resize(900, 520); self.setSizeGripEnabled(True)
        box = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(len(columns), 5); self.table.setObjectName("columnInfo")
        self.table.setHorizontalHeaderLabels(["열", "Qualifier / 필드", "종류", "필수", "설명"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False); self.table.horizontalHeader().setStretchLastSection(True)
        self.details = []
        for r, (field, label) in enumerate(columns):
            meta = (info or {}).get(field) or {}
            required = "" if not meta else ("예" if meta.get("required") else "아니요")
            description = meta.get("description") or FIELD_TEXT.get(field, "")
            values = (f"{label}", f"{meta.get('qualifier') or field}", meta.get("kind") or "목록 열", required or "—", description.replace("\n", " "))
            for c, value in enumerate(values): self.table.setItem(r, c, _cell(value, value if c == 4 else None))
            lines = [f"{label}  ({meta.get('qualifier') or field})"]
            if meta.get("source"): lines.append("출처: " + meta["source"])
            if description: lines += ["", description]
            if meta.get("choices"): lines += ["", "값 목록:"] + [f"  {c}" for c in meta["choices"]]
            self.details.append("\n".join(lines))
        self.table.resizeColumnsToContents()
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True)
        splitter.addWidget(self.table); splitter.addWidget(self.detail); splitter.setStretchFactor(0, 3); splitter.setStretchFactor(1, 1)
        box.addWidget(splitter, 1)
        self.table.itemSelectionChanged.connect(self._selected)
        close = QPushButton("닫기"); close.clicked.connect(self.reject)
        row = QHBoxLayout(); row.addStretch(); row.addWidget(close); box.addLayout(row)
        if current is not None and 0 <= current < len(columns): self.table.selectRow(current)
        elif columns: self.table.selectRow(0)

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        self.detail.setPlainText(self.details[rows[0].row()] if rows and rows[0].row() < len(self.details) else "")
