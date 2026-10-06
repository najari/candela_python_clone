"""U2·U4·U6 보조 화면. 작업 기록, 세부 표, 읽기 전용 표 대화상자, Shortcut 행렬, Compact View, 고장 메모리 탭,
Rework 목록, 상태 조건 일괄 변경. 데이터는 작업 스레드(Application)가 만들고 여기서는 표시·선택만 한다."""
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .layout_view import LayoutPane
from .presentation import qualifier_last, sae_dtc

GREY = QColor("#7a8699")


def cell(value, tip=None):
    item = QTableWidgetItem("—" if value is None else str(value))
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    if tip: item.setToolTip(tip)
    return item


def fill_table(table, rows, columns, formatter=None):
    """rows(dict)·columns([(필드, 머리)])로 읽기 전용 QTableWidget을 채운다. 각 행 첫 칸 UserRole = 행 번호.
    Qualifier 열은 맨 뒤에 둔다(qualifier_last)."""
    columns = qualifier_last(columns)
    table.setSortingEnabled(False)
    table.clear()
    table.setColumnCount(len(columns))
    table.setHorizontalHeaderLabels([label for _, label in columns])
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, (field, _) in enumerate(columns):
            value = formatter(row, field) if formatter else row.get(field)
            item = cell(value)
            item.setData(Qt.ItemDataRole.UserRole, r)
            table.setItem(r, c, item)
    # 문서 순서를 유지한다. 정렬 표시가 없으면(-1) 정렬을 켜도 즉시 정렬하지 않고, 머리를 누를 때만 정렬한다.
    table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    table.setSortingEnabled(True)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    header.setStretchLastSection(True)
    table.resizeColumnsToContents()


def readonly_table():
    table = QTableWidget()
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setVisible(False)
    return table


def copy_table(table):
    """보이는 행을 탭 구분 텍스트로 클립보드에 복사한다(머리 포함)."""
    lines = ["\t".join(table.horizontalHeaderItem(c).text() for c in range(table.columnCount()))]
    for r in range(table.rowCount()):
        if table.isRowHidden(r): continue
        lines.append("\t".join((table.item(r, c).text() if table.item(r, c) else "") for c in range(table.columnCount())))
    QApplication.clipboard().setText("\n".join(lines))
    return len(lines) - 1


class WorkLog(QWidget):
    """출력 창(M4-1, 작업 기록 탭). 열: 종류·시각(1/100초)·출처·링크(↗)·내용. [OW-R100~R140]
    필터는 보이기만 바꾸고 메시지는 모두 모은다([OW-R300]). 종류 단추·출처·내용 필터는 AND다([OW-R310]).
    '상태' 종류(일상 작업 완료)는 처음에 숨긴다([OW-R335]). 오른쪽 상세 영역은 기본 꺼짐([OW-R430]), 자동 스크롤은 기본 켜짐([OW-R420]).
    링크가 있는 행은 ↗ 칸을 누르거나 행을 두 번 누르면 navigate_requested(대상)을 보낸다([OW-R240]).
    settings(QSettings)를 주면 필터·단추 상태·열 너비를 다음 실행까지 기억한다([OW-R700]).
    QPlainTextEdit와 같은 appendPlainText(text, kind)/toPlainText()를 유지한다."""
    KINDS = ("오류", "경고", "정보", "상태")
    DEFAULT_KINDS = {"오류": True, "경고": True, "정보": True, "상태": False}
    COLUMNS = ("종류", "시각", "출처", "", "내용")
    navigate_requested = Signal(object)

    def __init__(self, parent=None, limit=1000, settings=None):
        super().__init__(parent)
        self.entries, self.limit, self.settings = [], limit, settings
        self._restoring = False
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.kind_buttons = {}
        for kind in self.KINDS:
            button = QToolButton(); button.setText(kind); button.setCheckable(True); button.setChecked(self.DEFAULT_KINDS[kind])
            button.setToolTip(f"'{kind}' 메시지 보이기/숨기기 (필터는 보이기만 바꿉니다)")
            button.toggled.connect(self._filter_changed)
            self.kind_buttons[kind] = button; bar.addWidget(button)
        self.origin_filter = QComboBox(); self.origin_filter.addItem("모든 출처", None); self.origin_filter.setToolTip("출처 필터")
        self.origin_filter.currentIndexChanged.connect(self._filter_changed)
        self.text_filter = QLineEdit(); self.text_filter.setPlaceholderText("내용 필터"); self.text_filter.setClearButtonEnabled(True)
        self.text_filter.textChanged.connect(self._filter_changed)
        reset = QPushButton("필터 초기화"); reset.setToolTip("종류·출처·내용 필터를 처음 값으로 되돌립니다."); reset.clicked.connect(self.reset_filters)
        self.detail_button = QToolButton(); self.detail_button.setText("상세"); self.detail_button.setCheckable(True)
        self.detail_button.setToolTip("오른쪽에 선택 메시지 전체를 보입니다."); self.detail_button.toggled.connect(self._toggle_detail)
        self.autoscroll_button = QToolButton(); self.autoscroll_button.setText("자동 스크롤"); self.autoscroll_button.setCheckable(True)
        self.autoscroll_button.setChecked(True); self.autoscroll_button.toggled.connect(lambda *_: self._save_state())
        copy = QPushButton("복사"); copy.setToolTip("선택한 행(없으면 보이는 행 전체)을 탭 구분 텍스트로 복사합니다."); copy.clicked.connect(self.copy_rows)
        clear = QPushButton("지우기"); clear.clicked.connect(self.clear)
        bar.addWidget(self.origin_filter); bar.addWidget(self.text_filter, 1); bar.addWidget(reset)
        bar.addWidget(self.detail_button); bar.addWidget(self.autoscroll_button); bar.addWidget(copy); bar.addWidget(clear)
        box.addLayout(bar)
        self.splitter = QSplitter(Qt.Orientation.Horizontal); self.splitter.setChildrenCollapsible(False)
        self.table = readonly_table(); self.table.setObjectName("workLog")
        self.table.setColumnCount(len(self.COLUMNS)); self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.setWordWrap(False); self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        header = self.table.horizontalHeader(); header.setStretchLastSection(True); header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in enumerate((52, 92, 90, 28)): self.table.setColumnWidth(col, width)
        self.table.cellClicked.connect(self._cell_clicked); self.table.cellDoubleClicked.connect(self._row_activated)
        self.table.itemSelectionChanged.connect(self._show_detail)
        header.sectionResized.connect(lambda *_: self._save_state())
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True); self.detail.setObjectName("workLogDetail")
        self.detail.setPlaceholderText("메시지를 고르면 전체 내용이 보입니다.")
        self.splitter.addWidget(self.table); self.splitter.addWidget(self.detail)
        self.splitter.setStretchFactor(0, 3); self.splitter.setStretchFactor(1, 1)
        self.detail.setVisible(False)
        box.addWidget(self.splitter, 1)
        self._shown = []
        self._restore_state()

    # ---------------------------------------------------------------- 메시지
    def appendPlainText(self, text, kind=None, origin=None, target=None):
        kind = kind or ("오류" if any(w in str(text) for w in ("오류", "실패", "INTERNAL_ERROR")) else "정보")
        if kind not in self.KINDS: kind = "정보"
        entry = {"time": datetime.now().strftime("%H:%M:%S.%f")[:11], "kind": kind, "origin": origin or "작업", "message": str(text), "target": target}
        self.entries.append(entry)
        if len(self.entries) > self.limit:
            self.entries = self.entries[-self.limit:]
            self.apply_filter(); return
        if self.origin_filter.findData(entry["origin"]) < 0: self.origin_filter.addItem(entry["origin"], entry["origin"])
        if self._visible(entry):
            self._shown.append(entry); self._put_row(self.table.rowCount(), entry, append=True)
            if self.autoscroll_button.isChecked(): self.table.scrollToBottom()

    add = appendPlainText

    def toPlainText(self):
        return "\n".join(f"{e['time']} [{e['kind']}] {e['origin']}: {e['message']}" for e in self.entries)

    def clear(self):
        self.entries = []
        self.apply_filter()

    # ---------------------------------------------------------------- 필터·표
    def _visible(self, entry):
        origin = self.origin_filter.currentData()
        term = self.text_filter.text().strip().casefold()
        return (self.kind_buttons[entry["kind"]].isChecked() and (origin is None or entry["origin"] == origin)
                and (not term or term in entry["message"].casefold()))

    def _filter_changed(self, *_):
        self.apply_filter(); self._save_state()

    def apply_filter(self, *_):
        self._shown = [e for e in self.entries if self._visible(e)]
        self.table.setRowCount(0)
        self.table.setRowCount(len(self._shown))
        for r, entry in enumerate(self._shown): self._put_row(r, entry)
        if self._shown and self.autoscroll_button.isChecked(): self.table.scrollToBottom()
        self._show_detail()

    def _put_row(self, r, entry, append=False):
        if append: self.table.insertRow(r)
        link = "↗" if entry.get("target") else ""
        for c, value in enumerate((entry["kind"], entry["time"], entry["origin"], link, entry["message"])):
            item = cell(value, entry["message"] if c == 4 else ("누르면 대상으로 이동합니다." if c == 3 and link else None))
            if entry["kind"] == "오류": item.setForeground(QBrush(QColor("#b3261e")))
            elif entry["kind"] == "경고": item.setForeground(QBrush(QColor("#a06b19")))
            elif entry["kind"] == "상태": item.setForeground(QBrush(GREY))
            if c == 3 and link: item.setForeground(QBrush(QColor("#205db8"))); item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, c, item)

    def visible_entries(self):
        return list(self._shown)

    def _cell_clicked(self, row, column):
        if column == 3: self._row_activated(row, column)

    def _row_activated(self, row, _column=0):
        if 0 <= row < len(self._shown) and self._shown[row].get("target"):
            self.navigate_requested.emit(self._shown[row]["target"])

    def _toggle_detail(self, on):
        self.detail.setVisible(bool(on)); self._show_detail(); self._save_state()

    def _show_detail(self):
        if not self.detail.isVisible(): return
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows or rows[0] >= len(self._shown): self.detail.clear(); return
        e = self._shown[rows[0]]
        target = e.get("target") or {}
        lines = [f"{e['kind']} · {e['time']} · {e['origin']}", "", e["message"]]
        if target: lines += ["", "대상: " + (target.get("path") or target.get("oid") or target.get("key") or "")]
        self.detail.setPlainText("\n".join(lines))

    def copy_rows(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}) or list(range(len(self._shown)))
        lines = ["\t".join(("종류", "시각", "출처", "내용"))]
        for r in rows:
            if r < len(self._shown):
                e = self._shown[r]; lines.append("\t".join((e["kind"], e["time"], e["origin"], e["message"].replace("\n", " "))))
        QApplication.clipboard().setText("\n".join(lines))
        return len(lines) - 1

    def reset_filters(self):
        """필터 초기화([OW-R360]): 종류 단추 기본값, 모든 출처, 내용 필터 비움."""
        self._restoring = True
        try:
            for kind, button in self.kind_buttons.items(): button.setChecked(self.DEFAULT_KINDS[kind])
            self.origin_filter.setCurrentIndex(0); self.text_filter.clear()
        finally:
            self._restoring = False
        self.apply_filter(); self._save_state()

    def reset_view(self):
        """보기 초기화(M4-3): 필터와 단추를 처음 값으로, 상세 끔, 자동 스크롤 켬, 열 너비 처음 값."""
        self._restoring = True
        try:
            self.detail_button.setChecked(False); self.autoscroll_button.setChecked(True)
            for col, width in enumerate((52, 92, 90, 28)): self.table.setColumnWidth(col, width)
        finally:
            self._restoring = False
        self.reset_filters()

    # ---------------------------------------------------------------- 기억([OW-R700])
    def _save_state(self):
        if self.settings is None or self._restoring: return
        self.settings.setValue("output/kinds", [k for k, b in self.kind_buttons.items() if b.isChecked()] or ["-"])
        self.settings.setValue("output/text", self.text_filter.text())
        self.settings.setValue("output/detail", self.detail_button.isChecked())
        self.settings.setValue("output/autoscroll", self.autoscroll_button.isChecked())
        self.settings.setValue("output/header", self.table.horizontalHeader().saveState())

    def _restore_state(self):
        if self.settings is None: return
        self._restoring = True
        try:
            kinds = self.settings.value("output/kinds")
            if isinstance(kinds, str): kinds = [kinds]
            if kinds:
                for k, b in self.kind_buttons.items(): b.setChecked(k in kinds)
            self.text_filter.setText(str(self.settings.value("output/text", "") or ""))
            self.detail_button.setChecked(str(self.settings.value("output/detail", "false")).lower() == "true")
            self.autoscroll_button.setChecked(str(self.settings.value("output/autoscroll", "true")).lower() != "false")
            header = self.settings.value("output/header")
            if header: self.table.horizontalHeader().restoreState(header)
        finally:
            self._restoring = False
        self.detail.setVisible(self.detail_button.isChecked())

    def clear_saved_state(self):
        if self.settings is None: return
        for k in ("output/kinds", "output/text", "output/detail", "output/autoscroll", "output/header"): self.settings.remove(k)


class DetailTable(QWidget):
    """선택 객체 창(목록 아래)의 '세부 표' 탭: 마스터·디테일의 디테일(상태 그룹의 상태, 변형 식별 패턴 등)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel(""); self.title.setWordWrap(True)
        self.table = readonly_table(); self.table.setObjectName("detailTable")
        box.addWidget(self.title); box.addWidget(self.table, 1)
        self.data = None

    def set_table(self, data):
        self.data = data
        if not data:
            self.title.setText(""); self.table.setRowCount(0); self.table.setColumnCount(0); return
        self.title.setText(data.get("title", ""))
        fill_table(self.table, data.get("rows", []), data.get("columns", []))


class TablesDialog(QDialog):
    """탭마다 읽기 전용 표 하나(개정 이력의 작성자·이력 등). 두 번 누른 행(경로가 있는 행)은 result_row가 된다."""

    def __init__(self, title, tabs, parent=None, hint=""):
        super().__init__(parent)
        self.setWindowTitle(title); self.resize(1040, 640)
        self.result_row = None
        box = QVBoxLayout(self)
        if hint:
            label = QLabel(hint); label.setWordWrap(True); box.addWidget(label)
        self.tabs = QTabWidget(); box.addWidget(self.tabs, 1)
        self.tables = []
        for tab_title, rows, columns in tabs:
            table = readonly_table()
            fill_table(table, rows, columns)
            table.cellDoubleClicked.connect(lambda r, c, t=table, data=rows: self.open_row(t, data, r))
            self.tabs.addTab(table, f"{tab_title} ({len(rows)})")
            self.tables.append(table)
        bar = QHBoxLayout()
        copy = QPushButton("표 복사"); copy.clicked.connect(lambda: copy_table(self.tables[self.tabs.currentIndex()]) if self.tables else None)
        close = QPushButton("닫기"); close.clicked.connect(self.reject)
        bar.addWidget(copy); bar.addStretch(); bar.addWidget(close)
        box.addLayout(bar)

    def open_row(self, table, rows, r):
        item = table.item(r, 0)
        if item is None: return
        row = rows[item.data(Qt.ItemDataRole.UserRole)]
        if row.get("id") and not str(row["id"]).startswith("src:"):
            self.result_row = row; self.accept()


class ShortcutMatrixDialog(QDialog):
    """Shortcut Qualifier 개요 행렬(U4): 행 = Shortcut Qualifier, 열 = 선택한 변형(예/아니요), 경로.
    변형 부분집합 선택, '모든 선택 변형에 있는 행 숨기기', 표 복사. 행을 두 번 누르면 그 서비스로 이동한다."""

    def __init__(self, result, parent=None, current=None):
        super().__init__(parent)
        self.setWindowTitle("Shortcut Qualifier 개요"); self.resize(1100, 680)
        self.rows, self.variants = result["rows"], result["variants"]
        self.result_row = None
        box = QVBoxLayout(self)
        hint = QLabel("원본 CDD에 명시된 SHORTCUTQUAL만 표시합니다. 비교할 변형을 고르면 선택한 변형 중 하나라도 쓰는 행만 보입니다. 행을 두 번 누르면 이동합니다.")
        hint.setWordWrap(True); box.addWidget(hint)
        picks = QHBoxLayout(); picks.addWidget(QLabel("변형"))
        self.checks = []
        for v in self.variants:
            check = QCheckBox(v["name"]); check.setChecked(True); check.toggled.connect(self.rebuild)
            self.checks.append((v, check)); picks.addWidget(check)
        picks.addStretch(); box.addLayout(picks)
        options = QHBoxLayout()
        self.hide_common = QCheckBox("모든 선택 변형에 있는 행 숨기기"); self.hide_common.toggled.connect(self.rebuild)
        self.search = QLineEdit(); self.search.setPlaceholderText("Shortcut·이름·경로 검색"); self.search.textChanged.connect(self.rebuild)
        options.addWidget(self.hide_common); options.addWidget(self.search, 1); box.addLayout(options)
        self.table = readonly_table(); self.table.setObjectName("shortcutMatrix")
        self.table.cellDoubleClicked.connect(self.open_row)
        box.addWidget(self.table, 1)
        bar = QHBoxLayout()
        self.count = QLabel("")
        copy = QPushButton("표 복사"); copy.clicked.connect(lambda: copy_table(self.table))
        close = QPushButton("닫기"); close.clicked.connect(self.reject)
        bar.addWidget(self.count); bar.addStretch(); bar.addWidget(copy); bar.addWidget(close); box.addLayout(bar)
        self.rebuild()

    def chosen(self):
        return [v for v, check in self.checks if check.isChecked()]

    def rebuild(self, *_):
        chosen = self.chosen()
        ids = [v["id"] for v in chosen]
        term = self.search.text().strip().casefold()
        shown = []
        for row in self.rows:
            present = [vid for vid in ids if vid in row["variants"]]
            if not present: continue
            if self.hide_common.isChecked() and len(present) == len(ids): continue
            if term and term not in " ".join((row["qualifier"], row["name"], row["path"])).casefold(): continue
            shown.append(row)
        columns = [("qualifier", "Shortcut Qualifier"), ("name", "이름")] + [(f"v:{v['id']}", v["name"]) for v in chosen] + [("path", "경로")]
        self.shown = shown
        fill_table(self.table, shown, columns, lambda row, field: ("예" if field[2:] in row["variants"] else "아니요") if field.startswith("v:") else row.get(field))
        self.count.setText(f"{len(shown):,} / {len(self.rows):,}개 항목 · 변형 {len(chosen)}개")

    def open_row(self, r, _c):
        item = self.table.item(r, 0)
        if item is None: return
        row = self.shown[item.data(Qt.ItemDataRole.UserRole)]
        chosen = [v["id"] for v in self.chosen() if v["id"] in row["variants"]]
        self.result_row = {**row, "variant": chosen[0] if chosen else (row["variants"][0] if row["variants"] else None)}
        self.accept()


class CompactTreeDialog(QDialog):
    """Compact View(U4): 고른 변형에서 기본 변형과 공유하지 않는 인스턴스만 클래스별 트리로 보인다."""

    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Compact View · {result['name']}"); self.resize(760, 620)
        self.result_row = None
        box = QVBoxLayout(self)
        text = ("기본 변형에만 있고 확장 변형과 공유하지 않는 인스턴스입니다." if result["is_base"]
                else "이 변형에서 기본 변형과 공유하지 않는 인스턴스입니다(이 변형 원본, 다른 확장 변형 원본 참조).")
        label = QLabel(f"{text} {result['count']}개. 두 번 누르면 이동합니다."); label.setWordWrap(True); box.addWidget(label)
        self.tree = QTreeWidget(); self.tree.setObjectName("compactTree")
        self.tree.setColumnCount(3); self.tree.setHeaderLabels(["클래스 / 인스턴스", "공유", "서비스"])
        for cls in result["classes"]:
            top = QTreeWidgetItem([f"{cls['name']} ({len(cls['instances'])})", "", ""])
            f = top.font(0); f.setBold(True); top.setFont(0, f)
            for inst in cls["instances"]:
                label_text = (f"{inst['identifier_text']} " if inst["identifier_text"] else "") + inst["name"]
                child = QTreeWidgetItem([label_text, inst["share_text"], inst["services"]])
                child.setData(0, Qt.ItemDataRole.UserRole, inst)
                top.addChild(child)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        if not result["classes"]:
            self.tree.addTopLevelItem(QTreeWidgetItem(["(공유하지 않는 인스턴스 없음)", "", ""]))
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemDoubleClicked.connect(self.open_item)
        box.addWidget(self.tree, 1)
        close = QPushButton("닫기"); close.clicked.connect(self.reject)
        bar = QHBoxLayout(); bar.addStretch(); bar.addWidget(close); box.addLayout(bar)

    def open_item(self, item, _column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            self.result_row = data; self.accept()


class FaultMemoryDialog(QDialog):
    """고장 메모리 인스턴스의 읽기 전용 탭(U2): DtcStatusMask (zz), DTC 표 (xx), 스냅샷 레코드, 확장 데이터 레코드.
    편집은 DTCSTATUSMASK 의미 검토(high) 뒤로 미룬다."""

    def __init__(self, data, parent=None, dtc_format="hex"):
        super().__init__(parent)
        self.setWindowTitle(f"고장 메모리 레코드 · {data['name']}"); self.resize(1080, 700)
        self.data = data
        box = QVBoxLayout(self)
        note = QLabel(f"변형 {data['variant'] or '—'} 기준 · 읽기 전용입니다. 상태 마스크는 원본 CDD의 DTCSTATUSMASK에서 보입니다.")
        note.setWordWrap(True); box.addWidget(note)
        self.tabs = QTabWidget(); box.addWidget(self.tabs, 1)
        # DtcStatusMask
        mask = readonly_table(); mask.setObjectName("dtcStatusMask")
        mask_rows = data["status_bits"]
        fill_table(mask, mask_rows, [("bit", "비트"), ("used_text", "사용"), ("name", "이름"), ("qualifier", "Qualifier"), ("conv_text", "설정")],
                   lambda row, field: ("예" if row["used"] else "아니요") if field == "used_text" else row.get(field))
        for r in range(mask.rowCount()):
            if not mask_rows[mask.item(r, 0).data(Qt.ItemDataRole.UserRole)]["used"]:
                for c in range(mask.columnCount()): mask.item(r, c).setForeground(QBrush(GREY))
        if not mask_rows: mask.setToolTip("원본 CDD가 없어 상태 마스크를 보일 수 없습니다.")
        self.tabs.addTab(mask, "DtcStatusMask (zz)")
        # DTC 표
        dtcs = readonly_table(); dtcs.setObjectName("faultMemoryDtcs")
        fill_table(dtcs, data["dtcs"], data["dtc_columns"],
                   lambda row, field: (sae_dtc(row["code"]) if dtc_format == "sae" else f"0x{row['code']:06X}") if field == "code" else row.get(field))
        self.tabs.addTab(dtcs, f"DTC 표 (xx) {len(data['dtcs'])}")
        # 스냅샷
        snap = QWidget(); sl = QVBoxLayout(snap)
        records = readonly_table(); fill_table(records, data["snapshots"], [("number", "번호"), ("name", "이름"), ("qualifier", "Qualifier")],
                                               lambda row, field: f"0x{row['number']:02X}" if field == "number" else row.get(field))
        common = readonly_table(); fill_table(common, data["snapshot_dids"], [("identifier", "DID"), ("name", "이름")],
                                              lambda row, field: f"0x{row['identifier']:04X}" if field == "identifier" else row.get(field))
        per_dtc = readonly_table(); fill_table(per_dtc, data["snapshot_dtc_dids"], [("code", "DTC"), ("dtc", "DTC 이름"), ("identifier", "DID"), ("name", "DID 이름")],
                                               lambda row, field: f"0x{row['code']:06X}" if field == "code" else f"0x{row['identifier']:04X}" if field == "identifier" else row.get(field))
        sl.addWidget(QLabel("스냅샷 레코드")); sl.addWidget(records, 1)
        sl.addWidget(QLabel("공통 스냅샷 DID")); sl.addWidget(common, 1)
        sl.addWidget(QLabel("DTC별 스냅샷 DID(대체 목록)")); sl.addWidget(per_dtc, 1)
        self.tabs.addTab(snap, f"스냅샷 레코드 {len(data['snapshots'])}")
        # 확장 데이터
        ext = QWidget(); el = QVBoxLayout(ext)
        split = QSplitter(Qt.Orientation.Vertical)
        self.extended = readonly_table(); self.extended.setObjectName("extendedRecords")
        fill_table(self.extended, data["extended"], [("number", "번호"), ("name", "이름"), ("qualifier", "Qualifier"), ("size_text", "크기")],
                   lambda row, field: f"0x{row['number']:02X}" if field == "number" else row.get(field))
        self.extended_layout = QWidget(); QVBoxLayout(self.extended_layout).setContentsMargins(0, 0, 0, 0)
        split.addWidget(self.extended); split.addWidget(self.extended_layout)
        el.addWidget(split)
        self.extended.itemSelectionChanged.connect(self.show_extended)
        self.tabs.addTab(ext, f"확장 데이터 레코드 {len(data['extended'])}")
        close = QPushButton("닫기"); close.clicked.connect(self.reject)
        bar = QHBoxLayout(); bar.addStretch(); bar.addWidget(close); box.addLayout(bar)
        if data["extended"]: self.extended.selectRow(0)

    def show_extended(self):
        rows = self.extended.selectionModel().selectedRows()
        layout = self.extended_layout.layout()
        while layout.count():
            w = layout.takeAt(0).widget()
            if w is not None: w.deleteLater()
        if not rows: return
        record = self.data["extended"][self.extended.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)]
        layout.addWidget(QLabel(f"0x{record['number']:02X} {record['name']} 데이터 배치"))
        layout.addWidget(LayoutPane(record["layout"], self.extended_layout))


class ReworkDialog(QDialog):
    """Rework 목록(U6): 완료, 내용, 연결 경로. 적용하면 한 명령(set_rework)으로 저장하며 실행 취소할 수 있다.
    '연결로 이동'은 선택 행의 경로(이름 경로 또는 diag:// 경로)로 이동한다."""

    def __init__(self, items, parent=None, current_link=""):
        super().__init__(parent)
        self.setWindowTitle("Rework 목록"); self.resize(900, 560)
        self.current_link = current_link
        self.goto_link = None
        box = QVBoxLayout(self)
        label = QLabel("할 일을 기록합니다. 프로젝트와 함께 저장되고 ODX에는 출력되지 않습니다. 새 항목의 연결 경로는 현재 선택 위치입니다.")
        label.setWordWrap(True); box.addWidget(label)
        self.table = QTableWidget(0, 3); self.table.setObjectName("reworkTable")
        self.table.setHorizontalHeaderLabels(["완료", "내용", "연결 경로"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        box.addWidget(self.table, 1)
        for item in items: self.add(item)
        bar = QHBoxLayout()
        add = QPushButton("+ 항목"); add.clicked.connect(lambda: self.add({"message": "", "link": self.current_link}, edit=True))
        remove = QPushButton("− 삭제"); remove.clicked.connect(self.remove)
        goto = QPushButton("연결로 이동"); goto.clicked.connect(self.goto)
        bar.addWidget(add); bar.addWidget(remove); bar.addWidget(goto); bar.addStretch()
        box.addLayout(bar)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("적용")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

    def add(self, item, edit=False):
        r = self.table.rowCount(); self.table.insertRow(r)
        done = QTableWidgetItem("")
        done.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
        done.setCheckState(Qt.CheckState.Checked if item.get("done") else Qt.CheckState.Unchecked)
        self.table.setItem(r, 0, done)
        self.table.setItem(r, 1, QTableWidgetItem(item.get("message", "")))
        self.table.setItem(r, 2, QTableWidgetItem(item.get("link", "")))
        if edit:
            self.table.setCurrentCell(r, 1); self.table.editItem(self.table.item(r, 1))

    def remove(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True): self.table.removeRow(r)

    def goto(self):
        r = self.table.currentRow()
        if r < 0 or not self.table.item(r, 2): return
        link = self.table.item(r, 2).text().strip()
        if link:
            self.goto_link = link; self.done(2)

    def values(self):
        items = []
        for r in range(self.table.rowCount()):
            message = self.table.item(r, 1).text().strip() if self.table.item(r, 1) else ""
            if not message: continue
            items.append({"done": self.table.item(r, 0).checkState() == Qt.CheckState.Checked, "message": message,
                          "link": self.table.item(r, 2).text().strip() if self.table.item(r, 2) else ""})
        return items


class BulkConditionDialog(QDialog):
    """의존성 편집 모드(U4): 선택한 서비스들의 한 상태 칸을 한 번에 바꾼다(CANdela F2 일괄 변경)."""
    VALUES = (("허용 (✓)", "allow"), ("차단 (✗)", "block"), ("허용 + 전이 (✓ →)", "transition"), ("전이 제거", "no_transition"))

    def __init__(self, groups, count, parent=None, state_id=None):
        super().__init__(parent)
        self.setWindowTitle("상태 조건 일괄 변경"); self.resize(520, 240)
        self.groups = groups
        box = QVBoxLayout(self)
        label = QLabel(f"선택한 서비스 {count}개의 한 상태 칸을 바꿉니다. 한 명령으로 적용되며 실행 취소할 수 있습니다.")
        label.setWordWrap(True); box.addWidget(label)
        form = QFormLayout(); box.addLayout(form)
        self.state = QComboBox()
        for g in groups:
            for st in g["states"]: self.state.addItem(f"{g['name']}: {st['name']}", (g["id"], st["id"]))
        index = next((i for i in range(self.state.count()) if self.state.itemData(i)[1] == state_id), 0)
        self.state.setCurrentIndex(index)
        self.value = QComboBox()
        for text, data in self.VALUES: self.value.addItem(text, data)
        self.target = QComboBox()
        form.addRow("상태", self.state); form.addRow("값", self.value); form.addRow("전이 도착 상태", self.target)
        self.state.currentIndexChanged.connect(self.update_targets); self.value.currentIndexChanged.connect(self.update_targets)
        self.update_targets()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("적용")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        box.addWidget(buttons)

    def update_targets(self, *_):
        data = self.state.currentData()
        self.target.clear()
        group = next((g for g in self.groups if data and g["id"] == data[0]), None)
        for st in (group or {}).get("states", []): self.target.addItem(st["name"], st["id"])
        self.target.setEnabled(self.value.currentData() == "transition")

    def values(self):
        data = self.state.currentData()
        result = {"state_id": data[1] if data else None, "value": self.value.currentData()}
        if result["value"] == "transition": result["target_state_id"] = self.target.currentData()
        return result


def group_font(font):
    font = QFont(font); font.setBold(True)
    return font
