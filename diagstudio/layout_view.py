"""U2·M5-2·M5-3: 진단 인스턴스 페이지·DID 마스터·디테일이 함께 쓰는 배치 위젯.

탭마다 멤버 표(바이트, 비트, 이름, 데이터 타입, 기본값, 상수, 설명)와 바이트 × Bit7..Bit0 격자를 보인다.
서비스는 요청·긍정 응답 탭과 NR 탭, DID는 데이터 탭 하나다. 탭 줄 오른쪽 버튼으로 전체 / 멤버 표만 / 격자만을 바꾼다.
M5-2: 데이터 멤버의 이름·데이터 타입(콤보)·기본값·상수·설명(여러 줄: Enter 줄바꿈, Ctrl+Enter 확정)을 F2·두 번 눌러 고친다.
바이트·비트 위치는 길이에서 계산하므로 고칠 수 없다. 고정값·예약·구조 행과 읽기 전용 소유자(원문 배치 등)의 멤버도 고칠 수 없다.
확정하면 member_edited를 보내고 주 창이 공유 편집 선택(U3)과 명령(update_member)을 맡는다.
M5-3: NR 탭에 서비스 NRC와 상태 그룹 NRC(아이콘, 표시 전용, [DIUI-R420])를 함께 보이고 'NRC ≫' 단추로 열고 닫는다.
"""
import html

from PySide6.QtCore import Qt, QRectF, Signal, QItemSelectionModel
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget, QTableWidget, QTableWidgetItem,
                               QSplitter, QPushButton, QHeaderView, QAbstractItemView, QButtonGroup)

from .panels import EditTable, SheetDelegate
from .presentation import MEMBER_COLUMNS, member_rows, byte_grid, telegram_pattern, negative_pattern

TELEGRAM_TITLES = {"request": "요청", "positive": "긍정 응답"}
FIELD_COLORS = ("#dbe8ff", "#e3f4e1", "#fdf0d5", "#f4e1f0", "#e0f2f4", "#ece6fb", "#fbe3e0", "#eef1d8")
LITERAL_COLOR = "#eceff4"
GREY = QColor("#6b778a")


def _item(text, tip=None, editable=False):
    item = QTableWidgetItem(text)
    flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    if editable: flags |= Qt.ItemFlag.ItemIsEditable
    item.setFlags(flags)
    if tip: item.setToolTip(tip)
    return item


_STATE_ICON = []


def state_nrc_icon():
    """상태 그룹에서 자동으로 붙은 NRC 표시([DIUI-R420-Icon]): 주황 둥근 네모 안의 S."""
    if not _STATE_ICON:
        size = 14; pix = QPixmap(size, size); pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#a06b19"), 1.2)); painter.setBrush(QBrush(QColor("#fdf0d5")))
        painter.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), 3, 3)
        font = QFont(); font.setPixelSize(9); font.setBold(True); painter.setFont(font)
        painter.drawText(QRectF(0, 0, size, size), int(Qt.AlignmentFlag.AlignCenter), "S")
        painter.end(); _STATE_ICON.append(QIcon(pix))
    return _STATE_ICON[0]


class LayoutPane(QWidget):
    """멤버 표 + 바이트 격자 한 쌍. CANdela처럼 멤버 표 오른쪽에 격자를 둔다(선택 객체 창이 목록 아래의 넓은 줄이다).
    owners = {데이터 객체 ID: {"owner", "owner_kind", "shared", "editable"}}, type_choices = [{"id", "name"}] 이면 멤버 칸을 고칠 수 있다."""
    EDIT_COLUMNS = {2: "name", 3: "data_type_id", 4: "default_text", 5: "constant_text", 6: "description"}
    member_edited = Signal(object)
    message = Signal(str)

    def __init__(self, layout, parent=None, owners=None, type_choices=None, key=None):
        super().__init__(parent)
        self.key = key
        self.owners = owners or {}
        self.type_choices = [(t["name"], t["id"]) for t in (type_choices or [])]
        box = QVBoxLayout(self); box.setContentsMargins(0, 4, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal); self.splitter.setChildrenCollapsible(False)
        self.members = EditTable(); self.members.setObjectName("layoutMembers")
        self.grid = QTableWidget(); self.grid.setObjectName("layoutGrid")
        for table in (self.members, self.grid):
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            table.verticalHeader().setVisible(False)
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.members.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.members.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.delegate = SheetDelegate(self.members); self.members.setItemDelegate(self.delegate)
        self.delegate.committed.connect(self._committed)
        self.members.reason = self._reason
        self.members.blocked.connect(self.message)
        self.splitter.addWidget(self.members); self.splitter.addWidget(self.grid)
        self.splitter.setStretchFactor(0, 3); self.splitter.setStretchFactor(1, 2)
        box.addWidget(self.splitter)
        self.rows = member_rows(layout)
        self.cells = byte_grid(layout)
        self._linking = False
        self._fill_members(); self._fill_grid()
        # [DOUIG-R120] 선택 연동(M5-1): 멤버 표 → 격자(그 필드의 칸 모두), 격자 칸 → 멤버 행.
        self.members.itemSelectionChanged.connect(self._member_selected)
        self.grid.cellClicked.connect(self._grid_clicked)

    def editable_row(self, r):
        row = self.rows[r] if 0 <= r < len(self.rows) else {}
        oid = row.get("object_id")
        return row.get("kind") == "data" and bool(oid) and bool(self.owners.get(oid, {}).get("editable"))

    def _fill_members(self):
        keys = ("byte", "bit", "name", "type", "default", "constant", "description")
        self.members.setColumnCount(len(keys)); self.members.setHorizontalHeaderLabels(list(MEMBER_COLUMNS))
        self.members.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            editable = self.editable_row(r)
            for c, k in enumerate(keys):
                text = row[k]
                if k == "description" and row.get("note"): text = row["note"].replace("\n", " ⏎ ")
                tip = f"{row.get('bits', 0)}비트" if k == "name" and row.get("bits") else None
                if k == "description" and row["kind"] == "data":
                    tip = (row.get("note") or "(설명 없음)") + (f"\n\n타입: {row['description']}" if row.get("description") else "")
                cell = _item(text, tip, editable and c in self.EDIT_COLUMNS)
                if row["kind"] in ("literal", "reserved"): cell.setForeground(QBrush(GREY))
                if row["kind"] == "literal" and k == "name":
                    f = cell.font(); f.setItalic(True); cell.setFont(f)
                if k == "description" and row["kind"] == "data" and not row.get("note"):
                    cell.setForeground(QBrush(GREY))  # 설명이 없으면 타입 요약을 회색으로 보인다
                if editable and c in self.EDIT_COLUMNS:
                    field = self.EDIT_COLUMNS[c]
                    if field == "data_type_id":
                        cell.setData(SheetDelegate.INFO, {"kind": "enum", "choices": self.type_choices})
                        cell.setData(SheetDelegate.VALUE, row.get("type_id"))
                    else:
                        value = {"name": row.get("label"), "default_text": row["default"], "constant_text": row["constant"],
                                 "description": row.get("note") or ""}[field]
                        cell.setData(SheetDelegate.INFO, {"kind": "multiline" if field == "description" else "text"})
                        cell.setData(SheetDelegate.VALUE, value)
                self.members.setItem(r, c, cell)
        header = self.members.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents); header.setStretchLastSection(True)

    def _fill_grid(self):
        self.grid.setColumnCount(9)
        self.grid.setHorizontalHeaderLabels(["바이트"] + [f"Bit{b}" for b in range(7, -1, -1)])
        self.grid.setRowCount(len(self.cells))
        self._cell_rows = {}
        self._cell_member = {}
        for r, line in enumerate(self.cells):
            # [DOUIG-R210] 24바이트보다 긴 필드는 앞 23바이트와 마지막 바이트만 보인다(바이트 칸에 'n…'·'…m').
            byte_label = line.get("label") or ("…" if line["byte"] is None else str(line["byte"]))
            self.grid.setItem(r, 0, _item(byte_label, "긴 필드는 앞 23바이트와 마지막 바이트만 보입니다(가운데 생략)." if "…" in byte_label and line["byte"] is not None else None))
            c = 0
            while c < 8:
                label, index = line["cells"][c]
                span = 1
                while c + span < 8 and line["cells"][c + span][1] == index: span += 1
                cell = _item(label, label)
                if index is not None:
                    kind = self.rows[index]["kind"] if 0 <= index < len(self.rows) else "data"
                    color = LITERAL_COLOR if kind in ("literal", "reserved") else FIELD_COLORS[index % len(FIELD_COLORS)]
                    cell.setBackground(QBrush(QColor(color)))
                    self._cell_rows.setdefault(index, []).append((r, c + 1))
                    if 0 <= index < len(self.rows): self._cell_member[(r, c + 1)] = index
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.grid.setItem(r, c + 1, cell)
                if span > 1: self.grid.setSpan(r, c + 1, 1, span)
                c += span
        header = self.grid.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)

    def _member_selected(self):
        """멤버 행 → 그 필드의 격자 칸을 모두 고른다."""
        if self._linking: return
        rows = sorted({i.row() for i in self.members.selectedIndexes()})
        if not rows: return
        spots = self._cell_rows.get(rows[0])
        if not spots: return
        self._linking = True
        try:
            self.grid.clearSelection()
            model = self.grid.model(); selection = self.grid.selectionModel()
            self.grid.setCurrentCell(*spots[0])
            for r, c in spots: selection.select(model.index(r, c), QItemSelectionModel.SelectionFlag.Select)
            self.grid.scrollToItem(self.grid.item(*spots[0]))
        finally:
            self._linking = False

    def _grid_clicked(self, r, c):
        """격자 칸 → 그 필드의 멤버 행을 고른다(같은 필드의 다른 칸도 함께 표시된다)."""
        index = self._cell_member.get((r, c))
        if index is None or index >= self.members.rowCount(): return
        column = max(0, self.members.currentColumn())
        self.members.setCurrentCell(index, column)
        self.members.scrollToItem(self.members.item(index, column) or self.members.item(index, 0))

    def _reason(self, r, c):
        if c in (0, 1): return "바이트·비트 위치는 길이에서 계산하므로 직접 고칠 수 없습니다."
        row = self.rows[r] if 0 <= r < len(self.rows) else {}
        if row.get("kind") != "data": return "고정값·예약 비트·구조·반복 행은 속성 대화상자(Alt+Enter)의 구조 탭에서 고칩니다."
        if not self.owners.get(row.get("object_id"), {}).get("editable"):
            return "읽기 전용 소유자(원문 배치·템플릿·미지원 객체)의 멤버이거나 이 창에서 고칠 수 없는 멤버입니다."
        return ""

    def _committed(self, r, c, value):
        if not self.editable_row(r) or c not in self.EDIT_COLUMNS: return
        row = self.rows[r]
        owner = self.owners.get(row["object_id"], {})
        field = self.EDIT_COLUMNS[c]
        if field == "name": value = str(value).strip()
        self.member_edited.emit({"payload": {"id": row["object_id"], field: value}, "owner": owner.get("owner"), "owner_kind": owner.get("owner_kind"),
                                 "shared": owner.get("shared", 1), "tab": self.key, "index": r, "cell": (r, c), "label": row.get("label")})


class LayoutView(QWidget):
    """레코드(details 결과)를 받아 탭을 만든다. set_record(None)이면 비운다. 같은 객체를 다시 받으면 탭·칸 위치를 되살린다."""
    MODES = ("전체", "멤버 표만", "격자만")
    member_edited = Signal(object)
    message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        self.header = QLabel(""); self.header.setObjectName("layoutHeader"); self.header.setWordWrap(True)
        self.header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.header)
        self.tabs = QTabWidget(); self.tabs.setObjectName("layoutTabs"); box.addWidget(self.tabs, 1)
        self.empty = QLabel("DID나 서비스를 선택하면 데이터 배치를 표시합니다."); self.empty.setObjectName("subheading")
        box.addWidget(self.empty)
        # 전체 / 멤버 표만 / 격자만 버튼은 탭 줄 오른쪽에 둔다(선택 객체 창은 목록 아래의 낮은 줄이라 높이를 아낀다).
        modes_box = QWidget(); modes = QHBoxLayout(modes_box); modes.setContentsMargins(0, 0, 0, 2)
        self.nrc_button = QPushButton("NRC ≫"); self.nrc_button.setCheckable(True); self.nrc_button.setChecked(True)
        self.nrc_button.setToolTip("NRC 목록(NR 탭)을 열고 닫습니다. 서비스 행의 부정 응답(rc) 칸을 눌러도 됩니다.")
        self.nrc_button.toggled.connect(self.set_nrc_visible); modes.addWidget(self.nrc_button)
        self.mode_group = QButtonGroup(self); self.mode_group.setExclusive(True)
        for i, label in enumerate(self.MODES):
            button = QPushButton(label); button.setCheckable(True); button.setChecked(i == 0)
            self.mode_group.addButton(button, i); modes.addWidget(button)
        self.tabs.setCornerWidget(modes_box, Qt.Corner.TopRightCorner)
        self.mode_group.idClicked.connect(self.apply_mode)
        self.mode = 0
        self.nrc_visible = True   # 설정 '서비스를 열 때 NRC 목록'(M6-2)을 주 창이 넣는다
        self.keys = []
        self.record_id = None
        self.set_record(None)

    def apply_mode(self, mode=None):
        if mode is not None: self.mode = mode
        for i in range(self.tabs.count()):
            pane = self.tabs.widget(i)
            if isinstance(pane, LayoutPane):
                pane.members.setVisible(self.mode in (0, 1)); pane.grid.setVisible(self.mode in (0, 2))

    def clear(self):
        while self.tabs.count():
            w = self.tabs.widget(0); self.tabs.removeTab(0); w.deleteLater()
        self.keys = []
        self.header.clear()

    def _pane(self, key, layout, record, label):
        pane = LayoutPane(layout, self, record.get("layout_owners"), record.get("type_choices"), key)
        pane.member_edited.connect(self.member_edited); pane.message.connect(self.message)
        self.tabs.addTab(pane, label); self.keys.append(key)
        return pane

    def _snapshot(self):
        key = self.keys[self.tabs.currentIndex()] if 0 <= self.tabs.currentIndex() < len(self.keys) else None
        pane = self.tabs.currentWidget()
        cell = (pane.members.currentRow(), pane.members.currentColumn()) if isinstance(pane, LayoutPane) else None
        return key, cell, isinstance(pane, LayoutPane) and pane.members.hasFocus()

    def set_record(self, record):
        same = bool(record) and record.get("id") is not None and record.get("id") == self.record_id
        key, cell, focused = self._snapshot() if same else (None, None, False)
        self.clear()
        self.record_id = (record or {}).get("id")
        kind = (record or {}).get("kind")
        has = bool(record) and (record.get("layout") is not None or bool(record.get("telegram_layouts")))
        self.tabs.setVisible(has); self.empty.setVisible(not has)
        self.nrc_button.setVisible(False)
        if not has: return
        if kind == "service":
            sid = record.get("sid")
            patterns = []
            for tkind, layout in (record.get("telegram_layouts") or {}).items():
                text, groups = telegram_pattern(layout)
                title = TELEGRAM_TITLES.get(tkind, tkind)
                patterns.append(f"{title}  {text}")
                # 탭 이름 규칙(U2): 자리표시 묶음이 필드 하나면 그 이름(CDD 데이터 객체), 여러 필드면 '요청 데이터'/'응답 데이터'.
                # 묶음이 여럿이면 자리표시를 모두 붙인다(예: 응답 데이터 (zz, yy)).
                if len(groups) == 1 and groups[0][2] == 1 and groups[0][1]: name = groups[0][1]
                else: name = "요청 데이터" if tkind == "request" else "응답 데이터"
                label = f"{title}: {name} ({', '.join(g[0] for g in groups)})" if groups else title
                self._pane(tkind, layout, record, label)
            patterns.append(f"부정 응답  {negative_pattern(sid)}")
            nrcs = record.get("nrcs") or []
            state_nrcs = record.get("state_nrcs") or []
            if nrcs or state_nrcs:
                table = QTableWidget(len(nrcs) + len(state_nrcs), 3); table.setObjectName("layoutNrcs")
                table.setHorizontalHeaderLabels(["응답 코드", "이름", "출처"]); table.verticalHeader().setVisible(False)
                table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
                names = record.get("nrc_names") or {}
                for r, code in enumerate(nrcs):
                    table.setItem(r, 0, _item(f"0x{code:02X}")); table.setItem(r, 1, _item(names.get(code, ""))); table.setItem(r, 2, _item("서비스"))
                tip = ("상태 그룹 NRC([DIUI-R420]): 이 서비스를 막는 상태가 있는 상태 그룹의 '상태 불일치 부정 응답'이 자동으로 붙습니다. "
                       "원본 CDD에서 읽은 표시 전용 값이며 출력 의미에는 쓰지 않습니다.")
                for j, extra in enumerate(state_nrcs):
                    r = len(nrcs) + j
                    code = extra.get("code")
                    first = _item(f"0x{code:02X}" if isinstance(code, int) else "—", tip); first.setIcon(state_nrc_icon())
                    cells = (first, _item(extra.get("text") or "", tip), _item(f"상태 그룹 '{extra.get('group', '')}' (자동)", tip))
                    for c, cell in enumerate(cells):
                        cell.setForeground(QBrush(QColor("#a06b19"))); table.setItem(r, c, cell)
                table.horizontalHeader().setStretchLastSection(True)
                table.resizeColumnsToContents()
                label = f"NR (rc) {len(nrcs)}" + (f" + 상태 {len(state_nrcs)}" if state_nrcs else "")
                index = self.tabs.addTab(table, label); self.keys.append("nrc")
                self.tabs.setTabVisible(index, self.nrc_visible)
                self.nrc_button.blockSignals(True); self.nrc_button.setChecked(self.nrc_visible); self.nrc_button.blockSignals(False)
                self.nrc_button.setVisible(True)
            meta = " · ".join(x for x in (record.get("role_text"), record.get("protocol_text")) if x)
            self.header.setText(f"<b>{html.escape(record.get('name') or '')}</b>" + (f" &nbsp;<span style='color:#677a92'>{html.escape(meta)}</span>" if meta else "")
                                + "<br>" + "<br>".join(f"<tt>{html.escape(p)}</tt>" for p in patterns))
        else:
            ident = record.get("identifier")
            title = f"0x{ident:04X} " if isinstance(ident, int) and kind == "did" else ""
            self.header.setText(f"<b>{html.escape(title + (record.get('name') or ''))}</b>")
            self._pane("did", record["layout"], record, f"{record.get('name') or '데이터'} 데이터")
        self.apply_mode()
        if key in self.keys and self.tabs.isTabVisible(self.keys.index(key)):
            self.tabs.setCurrentIndex(self.keys.index(key))
            pane = self.tabs.currentWidget()
            if isinstance(pane, LayoutPane) and cell and cell[0] >= 0 and cell[0] < pane.members.rowCount():
                pane.members.setCurrentCell(cell[0], max(0, cell[1]))
                if focused: pane.members.setFocus()  # [DOUI-R830-Focus] 편집을 마친 칸에 포커스가 남는다

    def set_nrc_visible(self, visible):
        self.nrc_visible = bool(visible)
        if "nrc" in self.keys:
            index = self.keys.index("nrc")
            self.tabs.setTabVisible(index, self.nrc_visible)
            if self.nrc_visible: self.tabs.setCurrentIndex(index)
        if self.nrc_button.isChecked() != self.nrc_visible:
            self.nrc_button.blockSignals(True); self.nrc_button.setChecked(self.nrc_visible); self.nrc_button.blockSignals(False)

    def show_telegram(self, key):
        """서비스 행의 자리표시(zz·yy…)·rc 칸을 누르면 그 전문 표를 연다(M5-3). rc는 NRC 목록을 열고, 열려 있으면 닫는다."""
        if key == "nrc":
            if "nrc" not in self.keys: return False
            index = self.keys.index("nrc")
            if self.tabs.isTabVisible(index) and self.tabs.currentIndex() == index: self.set_nrc_visible(False)
            else: self.set_nrc_visible(True)
            return True
        if key in self.keys:
            self.tabs.setCurrentIndex(self.keys.index(key)); return True
        return False
