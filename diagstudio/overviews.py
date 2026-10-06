"""Read-only explorer tables used by navigation and comparison views."""
from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtGui import QColor, QBrush, QPen, QPainterPath
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QTableWidget, QTableWidgetItem, QPushButton, QHeaderView,
                               QGraphicsScene, QGraphicsView, QComboBox, QCheckBox)

from .presentation import qualifier_last


class OverviewDialog(QDialog):
    def __init__(self, title, rows, columns, parent=None, hint="행을 두 번 클릭하면 해당 객체로 이동합니다."):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1000, 650)
        self.rows = list(rows)
        # Qualifier 열은 맨 뒤에 둔다(presentation.qualifier_last).
        self.columns = columns = qualifier_last(columns)
        self.result_row = None
        layout = QVBoxLayout(self)
        explanation = QLabel(hint)
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.search = QLineEdit()
        self.search.setPlaceholderText("표시된 열 검색")
        self.search.textChanged.connect(self.filter_rows)
        layout.addWidget(self.search)
        self.table = QTableWidget(len(self.rows), len(columns))
        self.table.setHorizontalHeaderLabels([label for _, label in columns])
        self.table.setSortingEnabled(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row_index, row in enumerate(self.rows):
            for col_index, (field, _) in enumerate(columns):
                value = row.get(field)
                item = QTableWidgetItem("—" if value is None else str(value))
                item.setData(Qt.ItemDataRole.UserRole, row_index)
                self.table.setItem(row_index, col_index, item)
        self.table.setSortingEnabled(True)
        self.table.cellDoubleClicked.connect(self.open_row)
        for index, width in enumerate((125, 220, 210, 260)):
            if index < len(columns): self.table.setColumnWidth(index, width)
        layout.addWidget(self.table)
        bottom = QHBoxLayout()
        self.count = QLabel(f"{len(rows):,}개 항목")
        bottom.addWidget(self.count)
        bottom.addStretch()
        close = QPushButton("닫기")
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        layout.addLayout(bottom)

    def filter_rows(self, text):
        term = text.casefold().strip()
        shown = 0
        for row in range(self.table.rowCount()):
            visible = not term or any(term in self.table.item(row, col).text().casefold() for col in range(self.table.columnCount()))
            self.table.setRowHidden(row, not visible)
            shown += int(visible)
        self.count.setText(f"{shown:,} / {len(self.rows):,}개 항목")

    def open_row(self, row, _column):
        index = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.result_row = self.rows[index]
        self.accept()


class ReferenceImpactDialog(OverviewDialog):
    def __init__(self, result, parent=None, mode="view"):
        labels = {"data_type": "데이터 타입", "data_object": "데이터 객체", "structure": "데이터 구조",
                  "did": "DID", "dtc": "DTC", "service": "서비스", "variant": "변형",
                  "diagnostic_instance": "진단 인스턴스", "diagnostic_class": "진단 클래스",
                  "state_group": "상태 그룹", "state": "상태", "snapshot_record": "스냅샷", "extended_record": "확장 데이터"}
        rows = [{**row, "category": labels.get(row["kind"], row["kind"])} for row in result["rows"]]
        hint = "저장된 참조를 따라 직접 사용처와 간접 영향을 표시합니다. 행을 두 번 클릭하면 해당 객체로 이동합니다."
        if result.get("uninterpreted"):
            hint += " 프로젝트의 미지원 원문에 담긴 의미는 이 목록에 포함되지 않습니다."
        if mode == "delete":
            hint += "\n" + (f"삭제 가능: 객체 {len(result['removed'])}개를 제거합니다. 공유 객체는 현재 변형의 연결만 제거할 수 있습니다."
                            if result["can_delete"] else result["reason"])
        elif mode == "edit":
            hint += "\n공유 타입 수정은 모든 사용처에 반영됩니다. 독립 복사는 새 타입을 만들며, 사용할 DID·서비스에서 새 타입을 직접 선택합니다."
        columns = [("reach", "관계"), ("category", "종류"), ("name", "사용 객체"),
                   ("relation", "참조 이유"), ("variants", "영향 변형"), ("path", "객체 경로")]
        super().__init__("참조 영향 · " + result["name"], rows, columns, parent, hint)
        actions = [("공유 타입 수정", "edit"), ("독립 복사 만들기", "copy")] if mode == "edit" else (
            [("삭제 적용", "delete")] if mode == "delete" and result["can_delete"] else [])
        bar = QHBoxLayout()
        for label, action in actions:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=action: self.choose(value))
            bar.addWidget(button)
        if actions: self.layout().insertLayout(self.layout().count() - 1, bar)

    def choose(self, action):
        self.result_row = {"action": action}
        self.accept()


# 전이 색 구분(M6-7 "Colored Transitions"): 같은 서비스의 전이는 같은 색
TRANSITION_COLORS = ("#205db8", "#2f7d5b", "#b3261e", "#a06b19", "#6a3fb5", "#00838f", "#c2185b", "#5d4037", "#558b2f", "#e65100", "#37474f", "#7b1fa2")


class StateGraphDialog(QDialog):
    """상태 그래프(M6-7, State Diagram). 표시 전용이다.
    - 변형·상태 그룹 선택([STUI-R200]): 변형을 바꾸면 variant_requested(변형 ID, 모든 변형이면 "")를 보내고 주 창이 set_graph로 다시 채운다.
    - 전이 글자: 서비스 이름 ↔ 요청 바이트(`10 01`, "Show Service Names"). 전이 색 구분("Colored Transitions"): 같은 서비스는 같은 색.
    - 상태 노드 툴팁: 그 상태에서 실행할 수 있는 서비스(그 그룹에 조건이 있고 이 상태를 허용)와 조건 없는 서비스 수."""
    variant_requested = Signal(str)

    def __init__(self, graph, parent=None):
        super().__init__(parent)
        self.setWindowTitle("상태 그래프")
        self.resize(1100, 780)
        self.setSizeGripEnabled(True)
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("변형"))
        self.variant = QComboBox(); self.variant.setObjectName("graphVariant"); self.variant.setMinimumWidth(180); bar.addWidget(self.variant)
        bar.addWidget(QLabel("상태 그룹"))
        self.group = QComboBox(); self.group.setObjectName("graphGroup"); self.group.setMinimumWidth(160); bar.addWidget(self.group)
        bar.addWidget(QLabel("전이 글자"))
        self.label_mode = QComboBox(); self.label_mode.setObjectName("graphLabels")
        self.label_mode.addItem("서비스 이름", "name"); self.label_mode.addItem("요청 바이트 (예: 10 01)", "request")
        bar.addWidget(self.label_mode)
        self.colored = QCheckBox("전이 색 구분"); self.colored.setChecked(True)
        self.colored.setToolTip("같은 서비스의 전이를 같은 색으로 그립니다.")
        bar.addWidget(self.colored)
        bar.addStretch()
        layout.addLayout(bar)
        hint = QLabel("화살표는 서비스 실행 뒤의 상태 전이입니다. 상태에 마우스를 올리면 그 상태에서 실행할 수 있는 서비스가 보입니다. 실행 조건 편집은 의존성 표에서 합니다.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        layout.addWidget(self.view, 1)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["출발 상태", "실행 서비스", "요청", "도착 상태"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMaximumHeight(220)
        layout.addWidget(self.table)
        bottom = QHBoxLayout()
        self.count = QLabel("")
        bottom.addWidget(self.count, 1)
        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self.graph = None
        self.state_items, self.edge_items = {}, []
        self.variant.currentIndexChanged.connect(self._variant_changed)
        self.group.currentIndexChanged.connect(lambda *_: self.draw())
        self.label_mode.currentIndexChanged.connect(lambda *_: self.draw())
        self.colored.toggled.connect(lambda *_: self.draw())
        self.set_graph(graph)

    def set_graph(self, graph):
        """조회 결과로 다시 그린다. 고른 상태 그룹은 남아 있으면 유지한다."""
        self.graph = graph
        chosen = self.group.currentData()
        for combo in (self.variant, self.group): combo.blockSignals(True)
        try:
            self.variant.clear()
            self.variant.addItem("모든 변형의 서비스", "")
            for v in graph.get("variants", []): self.variant.addItem(v["name"], v["id"])
            self.variant.setCurrentIndex(max(0, self.variant.findData(graph.get("variant") or "")))
            self.group.clear()
            self.group.addItem("모든 상태 그룹", "")
            for g in graph.get("groups", []): self.group.addItem(g["name"], g["id"])
            self.group.setCurrentIndex(max(0, self.group.findData(chosen or "")))
        finally:
            for combo in (self.variant, self.group): combo.blockSignals(False)
        self.draw()

    def _variant_changed(self, *_):
        wanted = self.variant.currentData() or ""
        if wanted != ((self.graph or {}).get("variant") or ""): self.variant_requested.emit(wanted)

    def edge_label(self, edge):
        if self.label_mode.currentData() == "request": return edge.get("request") or "(요청 바이트 없음)"
        return edge.get("service", "")

    def draw(self):
        graph = self.graph or {"groups": [], "edges": []}
        self.scene.clear()
        self.state_items, self.edge_items = {}, []
        chosen = self.group.currentData() or ""
        groups = [g for g in graph["groups"] if not chosen or g["id"] == chosen]
        positions, labels = {}, {}
        width, height = 210, 74
        for group_index, group in enumerate(groups):
            x = group_index * 420 + 24
            heading = self.scene.addText(group["name"])
            heading.setDefaultTextColor(QColor("#163668"))
            heading.setPos(x, 10)
            for state_index, state in enumerate(group["states"]):
                y = state_index * 145 + 58
                rect = self.scene.addRect(x, y, width, height, QPen(QColor("#4476b8")), QBrush(QColor("#dbe8ff") if state["initial"] else QColor("#ffffff")))
                rect.setToolTip(self.state_tip(group, state))
                label = self.scene.addText(("● " if state["initial"] else "") + state["name"])
                label.setPos(x + 10, y + 20)
                label.setToolTip(rect.toolTip())
                positions[state["id"]] = (x, y)
                labels[state["id"]] = state["name"]
                self.state_items[state["id"]] = rect
        colors = {}
        for edge in graph["edges"]:
            colors.setdefault(edge["service_id"], QColor(TRANSITION_COLORS[len(colors) % len(TRANSITION_COLORS)]))
        edges = [e for e in graph["edges"] if e["from"] in positions and e["to"] in positions]
        for index, edge in enumerate(edges):
            sx, sy = positions[edge["from"]]
            tx, ty = positions[edge["to"]]
            start = QPointF(sx + width, sy + height / 2)
            end = QPointF(tx + width, ty + height / 2)
            bend = max(sx, tx) + width + 55 + (index % 6) * 22
            path = QPainterPath(start)
            if start == end: path.cubicTo(QPointF(bend, start.y() - 32), QPointF(bend, end.y() + 32), end)
            else: path.cubicTo(QPointF(bend, start.y()), QPointF(bend, end.y()), end)
            color = colors[edge["service_id"]] if self.colored.isChecked() else QColor("#518268")
            pen = QPen(color, 1.6)
            curve = self.scene.addPath(path, pen)
            tip = f"{labels[edge['from']]} → {labels[edge['to']]}: {edge['service']}" + (f" ({edge['request']})" if edge.get("request") else "")
            curve.setToolTip(tip)
            self.scene.addLine(end.x() + 7, end.y() - 5, end.x(), end.y(), pen)
            self.scene.addLine(end.x() + 7, end.y() + 5, end.x(), end.y(), pen)
            text = self.scene.addText(self.edge_label(edge))
            text.setDefaultTextColor(color)
            text.setPos(bend - 20, (start.y() + end.y()) / 2 - 12 - (32 if start == end else 0))
            text.setToolTip(tip)
            self.edge_items.append((curve, text, edge))
        self.table.setRowCount(len(edges))
        for row_index, edge in enumerate(edges):
            for col_index, value in enumerate((labels.get(edge["from"], "?"), edge["service"], edge.get("request") or "", labels.get(edge["to"], "?"))):
                self.table.setItem(row_index, col_index, QTableWidgetItem(value))
        variant_text = self.variant.currentText() if self.variant.count() else ""
        self.count.setText(f"상태 {len(positions)}개 · 전이 {len(edges)}개 · {variant_text}")

    @staticmethod
    def state_tip(group, state):
        """상태 노드 툴팁: 이 상태에서 실행할 수 있는 서비스."""
        lines = [f"{group['name']}: {state['name']}" + (" (초기 상태)" if state.get("initial") else "")]
        services = state.get("services") or []
        if services:
            shown = services[:25]
            lines.append(f"이 상태에서 실행할 수 있는 서비스 {len(services)}개(조건 허용):")
            lines.extend("  " + name for name in shown)
            if len(services) > len(shown): lines.append(f"  … 외 {len(services) - len(shown)}개")
        else:
            lines.append("이 상태를 허용하는 조건이 있는 서비스가 없습니다.")
        if state.get("unrestricted"):
            lines.append(f"이 그룹에 조건이 없는 서비스 {state['unrestricted']}개는 어느 상태에서나 실행합니다.")
        return "\n".join(lines)
