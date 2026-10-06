"""찾기 창(M3-1, CANdelaStudio Search Dialog). 비모달 창이며 주 창이 작업 스레드로 찾고 결과로 이동한다.

- 찾을 내용과 최근 검색어 목록(▾), 범위(이름 / Qualifier / 이름·Qualifier·설명·식별자·DTC·서비스·NRC / 요청 바이트),
  대소문자 구분·단어 단위·전체 일치.
- 찾기: 문서 처음부터 첫 결과. 다음 찾기(F3, [SRCH-R150]·[SRCH-R150-F3]): 마지막으로 찾은 곳 다음부터(끝이면 처음부터).
  창을 닫은 뒤에도 F3이 같은 조건으로 다음을 찾는다([SRCH-R150-Closed]).
- 모두 찾기([SRCH-R200]): 출력 창에 이름 경로와 이동 링크(↗)로 쓴다.
- 닫기는 마지막으로 찾은 객체의 선택을 그대로 둔다.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout)

SCOPES = (("이름·Qualifier·설명·식별자·DTC·서비스·NRC", "all"), ("이름", "name"), ("Qualifier", "qualifier"), ("요청 바이트 (예: 22 F1 90)", "bytes"))
HISTORY_LIMIT = 12


class FindDialog(QDialog):
    requested = Signal(str, dict)   # (mode: first|next|all, 조건)

    def __init__(self, parent=None, history=()):
        super().__init__(parent)
        self.setWindowTitle("찾기")
        self.setModal(False)
        self.setSizeGripEnabled(True)
        self.resize(560, 230)
        box = QVBoxLayout(self)
        form = QFormLayout()
        self.query = QComboBox(); self.query.setEditable(True); self.query.setObjectName("findQuery")
        self.query.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.query.lineEdit().setPlaceholderText("찾을 내용 (▾ 최근 검색어)")
        for text in history: self.query.addItem(text)
        self.query.setCurrentText("")
        self.scope = QComboBox()
        for label, data in SCOPES: self.scope.addItem(label, data)
        form.addRow("찾을 내용", self.query); form.addRow("범위", self.scope)
        box.addLayout(form)
        options = QHBoxLayout()
        self.case = QCheckBox("대소문자 구분"); self.word = QCheckBox("단어 단위"); self.whole = QCheckBox("전체 일치")
        for check in (self.case, self.word, self.whole): options.addWidget(check)
        options.addStretch(); box.addLayout(options)
        self.status = QLabel(""); self.status.setObjectName("findStatus"); self.status.setWordWrap(True); box.addWidget(self.status)
        buttons = QHBoxLayout()
        self.find_button = QPushButton("찾기"); self.next_button = QPushButton("다음 찾기  F3"); self.all_button = QPushButton("모두 찾기")
        close = QPushButton("닫기")
        self.find_button.setDefault(True)
        self.find_button.clicked.connect(lambda: self.request("first"))
        self.next_button.clicked.connect(lambda: self.request("next"))
        self.all_button.clicked.connect(lambda: self.request("all"))
        close.clicked.connect(self.close)
        buttons.addWidget(self.find_button); buttons.addWidget(self.next_button); buttons.addWidget(self.all_button); buttons.addStretch(); buttons.addWidget(close)
        box.addLayout(buttons)
        self.scope.currentIndexChanged.connect(self._scope_changed)
        self.query.setFocus()

    def _scope_changed(self, *_):
        is_bytes = self.scope.currentData() == "bytes"
        for check in (self.case, self.word, self.whole): check.setEnabled(not is_bytes)

    def criteria(self):
        return {"query": self.query.currentText().strip(), "scope": self.scope.currentData(),
                "case": self.case.isChecked(), "word": self.word.isChecked(), "whole": self.whole.isChecked()}

    def set_criteria(self, criteria):
        self.query.setCurrentText(criteria.get("query", ""))
        index = self.scope.findData(criteria.get("scope", "all")); self.scope.setCurrentIndex(max(0, index))
        self.case.setChecked(bool(criteria.get("case"))); self.word.setChecked(bool(criteria.get("word"))); self.whole.setChecked(bool(criteria.get("whole")))

    def remember(self, text):
        """최근 검색어 목록 맨 앞에 넣는다(같은 말은 한 번)."""
        if not text: return
        index = self.query.findText(text, Qt.MatchFlag.MatchExactly)
        if index >= 0: self.query.removeItem(index)
        self.query.insertItem(0, text)
        while self.query.count() > HISTORY_LIMIT: self.query.removeItem(self.query.count() - 1)
        self.query.setCurrentText(text)

    def history(self):
        return [self.query.itemText(i) for i in range(self.query.count())]

    def request(self, mode):
        criteria = self.criteria()
        if not criteria["query"]:
            self.status.setText("찾을 내용을 넣으세요."); return
        self.remember(criteria["query"])
        self.requested.emit(mode, criteria)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F3:
            self.request("next"); return
        super().keyPressEvent(event)
