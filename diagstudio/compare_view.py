"""비교 보기(M6-6, Compare View). 비모달 창이며 작업 스레드는 부르지 않는다: 비교·파일 고르기·이동은 신호로 주 창에 넘기고,
주 창이 결과를 set_result·set_file로 채운다. 옮기기(Transfer) 모드는 범위 밖이다.

- 한 창에서 양쪽 선택: 왼쪽 = 작업본의 변형, 오른쪽 = 작업본의 다른 변형 또는 저장된 파일의 변형
- 차이 트리([CMP-R110] 문서 순서), 필터([CMP-R010] 왼쪽만·바뀜·오른쪽만·참조 객체, [CMP-R012] 레코드 항목·속성 값·상태 전이·다른 언어(처음엔 숨김)),
  모두 보이기([CMP-R016]), 자식이 모두 숨으면 부모도 숨김·남은 차이가 없으면 안내([CMP-R018]), 펼침 단계([CMP-R020]), 상세 창([CMP-R030]),
  양쪽 파일·revision·경로([CMP-R070]), 색 범례([CMP-R080]), 왼쪽·오른쪽 객체로 이동(창은 열어 둠, [CMP-R100]), 내보내기([CMP-R131])
"""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                               QSplitter, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .comparison import CATEGORY_LABELS, STATUS_LABELS, export_text, export_xhtml, export_xml

STATUS_COLORS = {"left": QColor("#fff1ef"), "right": QColor("#eaf7ee"), "changed": QColor("#fff8e6")}
LEVELS = (("variant", "변형까지", 0), ("class", "클래스까지", 1), ("instance", "인스턴스까지", 2), ("all", "모두", 99))
NODE_ROLE = Qt.ItemDataRole.UserRole


class CompareDialog(QDialog):
    compare_requested = Signal(dict)      # {"left_variant", "right_variant", "path", "expected_digest"}
    file_requested = Signal()             # 저장된 파일 고르기(주 창이 파일 대화상자와 compare_file_variants 조회 뒤 set_file)
    navigate_requested = Signal(str, str)  # (객체 ID, 변형 ID): 작업본의 그 객체로 이동(이 창은 열어 둔다)

    def __init__(self, variants, current_variant=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("비교")
        self.setModal(False)
        self.resize(1260, 800); self.setSizeGripEnabled(True)
        self.variants = list(variants)
        self.file = None          # {"path", "digest", "variants"}
        self.result = None
        self.items = {}
        self.filters = {"left": True, "changed": True, "right": True, "reference": True, "record": True, "attribute": True, "transition": True, "language": False}
        self.current_revision = None
        box = QVBoxLayout(self)
        # 양쪽 선택(한 창에서)
        pick = QHBoxLayout()
        pick.addWidget(QLabel("왼쪽: 작업본"))
        self.left_variant = QComboBox(); self.left_variant.setObjectName("compareLeft"); pick.addWidget(self.left_variant)
        pick.addSpacing(18)
        pick.addWidget(QLabel("오른쪽:"))
        self.right_source = QComboBox(); self.right_source.setObjectName("compareSource")
        self.right_source.addItem("작업본의 다른 변형", "project"); self.right_source.addItem("저장된 파일", "file")
        pick.addWidget(self.right_source)
        self.file_button = QPushButton("파일…"); self.file_button.setToolTip("비교할 저장된 프로젝트(.diagproj)를 고릅니다. 파일은 읽기만 합니다.")
        self.file_button.clicked.connect(lambda: self.file_requested.emit()); pick.addWidget(self.file_button)
        self.file_label = QLabel(""); self.file_label.setStyleSheet("color:#677a92"); pick.addWidget(self.file_label)
        self.right_variant = QComboBox(); self.right_variant.setObjectName("compareRight"); pick.addWidget(self.right_variant)
        self.compare_button = QPushButton("비교"); self.compare_button.setObjectName("compareRun"); self.compare_button.setDefault(True)
        self.compare_button.clicked.connect(self.request_compare); pick.addWidget(self.compare_button)
        pick.addStretch()
        box.addLayout(pick)
        # 필터 도구([CMP-R010]·[CMP-R012]·[CMP-R016])·펼침([CMP-R020])·내보내기([CMP-R131])
        tools = QHBoxLayout()
        self.filter_buttons = {}
        tips = {"left": "왼쪽에만 있는 객체", "changed": "양쪽에 있고 다른 객체·차이", "right": "오른쪽에만 있는 객체",
                "reference": "참조한 객체(데이터 타입·DID)의 차이 [CMP-R005]", "record": "DTC 레코드 항목 차이", "attribute": "속성 값 차이",
                "transition": "상태 조건·전이 차이", "language": "화면에 보이는 언어가 아닌 언어의 차이(처음에는 숨김)"}
        for key in ("left", "changed", "right", "reference", "record", "attribute", "transition", "language"):
            if key == "reference": tools.addSpacing(12)
            label = STATUS_LABELS.get(key) or CATEGORY_LABELS[key]
            button = QToolButton(); button.setText(("🔗 " if key == "reference" else "") + label); button.setCheckable(True); button.setChecked(self.filters[key])
            button.setObjectName(f"compareFilter_{key}"); button.setToolTip(tips[key])
            button.toggled.connect(lambda on, k=key: self.set_filter(k, on))
            self.filter_buttons[key] = button; tools.addWidget(button)
        self.reset_button = QToolButton(); self.reset_button.setText("모두 보이기"); self.reset_button.setToolTip("모든 필터를 켭니다 [CMP-R016].")
        self.reset_button.clicked.connect(self.show_everything); tools.addWidget(self.reset_button)
        tools.addSpacing(12); tools.addWidget(QLabel("펼침"))
        self.level = QComboBox(); self.level.setObjectName("compareLevel")
        for key, label, _ in LEVELS: self.level.addItem(label, key)
        self.level.setCurrentIndex(self.level.findData("instance"))
        self.level.currentIndexChanged.connect(lambda *_: self.apply_level()); tools.addWidget(self.level)
        tools.addStretch()
        self.export_button = QPushButton("차이 내보내기…"); self.export_button.setToolTip("모든 차이를 텍스트·XML·XHTML로 내보냅니다. 화면 필터는 적용하지 않습니다 [CMP-R131].")
        self.export_button.clicked.connect(self.export); tools.addWidget(self.export_button)
        box.addLayout(tools)
        # 범례([CMP-R080])와 양쪽 정보([CMP-R070])
        legend = QHBoxLayout()
        for status in ("left", "right", "changed"):
            swatch = QLabel(f"  {STATUS_LABELS[status]}  "); swatch.setStyleSheet(f"background:{STATUS_COLORS[status].name()}; border:1px solid #dce3ed")
            legend.addWidget(swatch)
        legend.addWidget(QLabel("🔗 참조 객체 차이"))
        self.info = QLabel(""); self.info.setObjectName("compareInfo"); self.info.setStyleSheet("color:#677a92"); self.info.setWordWrap(True)
        legend.addWidget(self.info, 1)
        box.addLayout(legend)
        self.stale = QLabel(""); self.stale.setStyleSheet("color:#a06b19"); self.stale.setVisible(False); box.addWidget(self.stale)
        # 트리 | 상세([CMP-R030])
        split = QSplitter(Qt.Orientation.Horizontal); split.setChildrenCollapsible(False)
        self.tree = QTreeWidget(); self.tree.setObjectName("compareTree")
        self.tree.setHeaderLabels(["객체", "왼쪽", "오른쪽"]); self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive); self.tree.setColumnWidth(0, 420); self.tree.setColumnWidth(1, 260)
        self.tree.currentItemChanged.connect(lambda *_: self.show_detail())
        self.tree.itemDoubleClicked.connect(lambda *_: self.go(prefer_left=True))
        split.addWidget(self.tree)
        side = QWidget(); sl = QVBoxLayout(side); sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(QLabel("상세"))
        self.detail = QPlainTextEdit(); self.detail.setReadOnly(True); self.detail.setObjectName("compareDetail")
        self.detail.setPlaceholderText("차이 행을 고르면 여러 줄 내용이 보입니다. 묶음 행(▸)은 펼쳐 봅니다.")
        sl.addWidget(self.detail, 1)
        moves = QHBoxLayout()
        self.go_left = QPushButton("왼쪽 객체로 이동"); self.go_left.clicked.connect(lambda: self.go(True)); moves.addWidget(self.go_left)
        self.go_right = QPushButton("오른쪽 객체로 이동"); self.go_right.clicked.connect(lambda: self.go(False)); moves.addWidget(self.go_right)
        sl.addLayout(moves)
        split.addWidget(side); split.setSizes([860, 380])
        box.addWidget(split, 1)
        bottom = QHBoxLayout()
        self.count = QLabel("비교할 두 쪽을 고르고 '비교'를 누르세요."); self.count.setObjectName("compareCount"); bottom.addWidget(self.count, 1)
        close = QPushButton("닫기"); close.clicked.connect(self.close); bottom.addWidget(close)
        box.addLayout(bottom)
        self.right_source.currentIndexChanged.connect(self._source_changed)
        self.set_variants(self.variants, current_variant)
        self._source_changed()
        self.show_detail()

    # ------------------------------------------------------------ 선택
    def set_variants(self, variants, current=None):
        """작업본 변형 목록(주 창 상태가 바뀌면 다시 준다). 고른 값은 남아 있으면 유지한다."""
        self.variants = list(variants)
        keep_left = current or self.left_variant.currentData()
        keep_right = self.right_variant.currentData() if self.right_source.currentData() == "project" else None
        self.left_variant.blockSignals(True); self.left_variant.clear()
        for v in self.variants: self.left_variant.addItem(v["name"], v["id"])
        self.left_variant.setCurrentIndex(max(0, self.left_variant.findData(keep_left)))
        self.left_variant.blockSignals(False)
        if self.right_source.currentData() == "project": self._fill_right(self.variants, keep_right)

    def _fill_right(self, variants, keep=None):
        self.right_variant.clear()
        for v in variants: self.right_variant.addItem(v["name"], v["id"])
        index = self.right_variant.findData(keep) if keep else -1
        if index < 0 and self.right_source.currentData() == "project":
            index = next((i for i, v in enumerate(variants) if v["id"] != self.left_variant.currentData()), 0)
        if index < 0 and self.right_source.currentData() == "file":
            # 저장된 파일: 왼쪽 변형과 같은 Qualifier·이름이 있으면 그것, 없으면 첫 변형
            left_name = self.left_variant.currentText()
            index = next((i for i, v in enumerate(variants) if v["name"] == left_name), 0)
        self.right_variant.setCurrentIndex(max(0, index))

    def _source_changed(self, *_):
        file_mode = self.right_source.currentData() == "file"
        self.file_button.setVisible(file_mode); self.file_label.setVisible(file_mode)
        if file_mode:
            if self.file: self._fill_right(self.file["variants"])
            else:
                self.right_variant.clear(); self.file_label.setText("(파일을 고르세요)")
        else:
            self._fill_right(self.variants)
        self._update_buttons()

    def choose_file(self):
        """저장된 파일과 비교로 바꾸고 파일을 고르게 한다(메뉴 '저장된 프로젝트와 비교…')."""
        self.right_source.setCurrentIndex(self.right_source.findData("file"))
        self.file_requested.emit()

    def set_file(self, project):
        """compare_file_variants 결과: 파일 경로·지문·변형."""
        self.file = {"path": project["path"], "digest": project["digest"], "variants": project["variants"]}
        self.file_label.setText(Path(project["path"]).name); self.file_label.setToolTip(project["path"])
        if self.right_source.currentData() != "file":
            self.right_source.blockSignals(True); self.right_source.setCurrentIndex(self.right_source.findData("file")); self.right_source.blockSignals(False)
            self.file_button.setVisible(True); self.file_label.setVisible(True)
        self._fill_right(project["variants"])
        self._update_buttons()

    def request_args(self):
        args = {"left_variant": self.left_variant.currentData(), "right_variant": self.right_variant.currentData()}
        if self.right_source.currentData() == "file" and self.file:
            args.update(path=self.file["path"], expected_digest=self.file["digest"])
        return args

    def request_compare(self):
        args = self.request_args()
        if not args["left_variant"] or not args["right_variant"]: return
        if "path" not in args and args["left_variant"] == args["right_variant"]:
            QMessageBox.information(self, "비교", "서로 다른 두 변형을 고르세요."); return
        self.set_busy(True)
        self.compare_requested.emit(args)

    def set_busy(self, busy):
        self.compare_button.setEnabled(not busy)
        if busy: self.count.setText("비교하는 중…")

    def failed(self, error):
        self.set_busy(False)
        self.count.setText("비교하지 못했습니다: " + str((error or {}).get("message", "")))
        QMessageBox.warning(self, "비교", str((error or {}).get("message", "")))

    def _update_buttons(self):
        ready = bool(self.left_variant.currentData()) and bool(self.right_variant.currentData())
        self.compare_button.setEnabled(ready)

    # ------------------------------------------------------------ 결과
    def set_result(self, result):
        """compare_view 결과로 트리를 새로 만든다. 필터·펼침 단계는 그대로 둔다."""
        self.result = result
        self.set_busy(False)
        self.current_revision = result.get("revision")
        self.stale.setVisible(False)
        left, right = result["left"], result["right"]
        describe = lambda info: f"{info.get('source','')} · {info.get('project','')} · 변형 {info.get('variant','')} · revision {info.get('revision','')} · {info.get('path','')}"
        self.info.setText(f"왼쪽: {describe(left)}\n오른쪽: {describe(right)}")
        self.tree.clear(); self.items = {}
        bold = QFont(); bold.setBold(True)
        for node in result["nodes"]:
            parent = self.items.get(node["parent"])
            item = QTreeWidgetItem([node["label"], node.get("left") or "", node.get("right") or ""])
            item.setData(0, NODE_ROLE, node)
            tip = node.get("detail") or node["label"]
            for col in range(3): item.setToolTip(col, tip[:600])
            color = STATUS_COLORS.get(node.get("status") or "")
            if color is not None and node["kind"] != "group":
                for col in range(3): item.setBackground(col, QBrush(color))
            if node["kind"] == "group":
                for col in range(3): item.setFont(col, bold)
            (parent.addChild(item) if parent is not None else self.tree.addTopLevelItem(item))
            self.items[node["id"]] = item
        self.placeholder = QTreeWidgetItem(["", "", ""])
        self.placeholder.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.tree.addTopLevelItem(self.placeholder)
        self.apply_filters()
        self.apply_level()
        first = next((self.items[n["id"]] for n in result["nodes"] if n["kind"] != "group" and not self.items[n["id"]].isHidden()), None)
        if first is not None: self.tree.setCurrentItem(first)
        self.show_detail()

    def set_current_revision(self, revision):
        """작업본이 바뀌면 결과가 이전 revision임을 알린다(다시 비교)."""
        if self.result is None or revision is None: return
        old = self.result.get("revision")
        self.stale.setText(f"작업본이 바뀌었습니다(revision {old} → {revision}). '비교'를 다시 누르면 새로 비교합니다.")
        self.stale.setVisible(old != revision)

    # ------------------------------------------------------------ 필터([CMP-R010]·[CMP-R012]·[CMP-R016]·[CMP-R018])
    def set_filter(self, key, on):
        self.filters[key] = bool(on)
        self.apply_filters()

    def show_everything(self):
        for key, button in self.filter_buttons.items():
            button.blockSignals(True); button.setChecked(True); button.blockSignals(False)
            self.filters[key] = True
        self.apply_filters()

    def passes(self, node):
        """차이 행(잎)이 보이는지: 상태 필터가 켜져 있고 범주 필터가 모두 켜져 있어야 한다."""
        return bool(self.filters.get(node.get("status"), True)) and all(self.filters.get(cat, True) for cat in node.get("cats") or [])

    def apply_filters(self):
        if self.result is None: return
        children = {}
        for node in self.result["nodes"]: children.setdefault(node["parent"], []).append(node)
        visible = {}

        def walk(node):
            kids = children.get(node["id"], [])
            if not kids:
                count = 1 if node["kind"] != "group" and self.passes(node) else 0
            else:
                count = sum(walk(k) for k in kids)
            visible[node["id"]] = count
            return count

        total = sum(walk(root) for root in children.get(None, []))
        for node in self.result["nodes"]:
            item = self.items[node["id"]]
            count = visible[node["id"]]
            item.setHidden(count == 0)
            if children.get(node["id"]):
                item.setText(0, f"{node['label']}  ({count})")
        counts = self.result.get("counts", {})
        empty = not self.result["nodes"]
        self.placeholder.setText(0, "두 쪽에 차이가 없습니다." if empty else "보일 차이가 남아 있지 않습니다. 필터를 켜거나 '모두 보이기'를 누르세요 [CMP-R018].")
        self.placeholder.setHidden(total > 0)
        summary = f"왼쪽만 {counts.get('left', 0)} · 오른쪽만 {counts.get('right', 0)} · 바뀜 {counts.get('changed', 0)} · 차이 행 {counts.get('aspects', 0)}"
        hidden = [CATEGORY_LABELS[c] for c in CATEGORY_LABELS if not self.filters[c] and counts.get(c)] + [STATUS_LABELS[s] for s in STATUS_LABELS if not self.filters[s]]
        self.count.setText(f"보이는 차이 {total}개 · {summary}" + (f" · 숨김: {', '.join(hidden)}" if hidden else ""))

    # ------------------------------------------------------------ 펼침([CMP-R020])
    def apply_level(self):
        depth = next((d for key, _, d in LEVELS if key == self.level.currentData()), 2)
        self.tree.collapseAll()
        if depth >= 99: self.tree.expandAll()
        elif depth > 0: self.tree.expandToDepth(depth - 1)

    # ------------------------------------------------------------ 상세·이동([CMP-R030]·[CMP-R100])
    def current_node(self):
        item = self.tree.currentItem()
        return item.data(0, NODE_ROLE) if item is not None else None

    def target_ids(self):
        """고른 행(차이 행이면 가장 가까운 객체 행)의 왼쪽·오른쪽 객체 ID."""
        item = self.tree.currentItem()
        while item is not None:
            node = item.data(0, NODE_ROLE)
            if node and node.get("kind") == "object": return node.get("left_id"), node.get("right_id")
            item = item.parent()
        return None, None

    def show_detail(self):
        node = self.current_node()
        self.detail.setPlainText(node.get("detail") or node["label"] if node else "")
        left, right = self.target_ids()
        same = bool(self.result and self.result.get("same_project"))
        self.go_left.setEnabled(bool(left))
        self.go_right.setEnabled(bool(right) and same)
        self.go_right.setToolTip("" if same else "저장된 파일의 객체는 작업본에서 열 수 없습니다(파일은 읽기만 합니다).")

    def go(self, prefer_left=True):
        if self.result is None: return
        left, right = self.target_ids()
        same = self.result.get("same_project")
        if prefer_left and left: self.navigate_requested.emit(left, self.result["left_variant"])
        elif right and same: self.navigate_requested.emit(right, self.result["right_variant"])
        elif left: self.navigate_requested.emit(left, self.result["left_variant"])

    # ------------------------------------------------------------ 내보내기([CMP-R131])
    def export(self):
        if self.result is None:
            QMessageBox.information(self, "차이 내보내기", "먼저 비교하세요."); return
        filters = {"텍스트 (*.txt)": (".txt", export_text), "XML (*.xml)": (".xml", export_xml), "XHTML (*.xhtml)": (".xhtml", export_xhtml)}
        path, chosen = QFileDialog.getSaveFileName(self, "차이 내보내기", "비교 결과.txt", ";;".join(filters))
        if not path: return
        suffix, writer = filters.get(chosen) or next(((s, w) for s, w in filters.values() if path.lower().endswith(s)), filters["텍스트 (*.txt)"])
        if not path.lower().endswith(suffix): path += suffix
        try:
            Path(path).write_text(writer(self.result), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "차이 내보내기", f"파일을 쓰지 못했습니다: {e}"); return
        self.count.setText(f"내보냈습니다(화면 필터 미적용): {path}")
