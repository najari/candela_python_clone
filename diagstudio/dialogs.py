"""명령 DTO를 만드는 Qt Widgets 대화상자. SQL/XML 처리는 하지 않는다."""
import json
import re
from .domain import DomainError, convert, decimal_text, ratio_text, make_qualifier, qualifier_follows
from .presentation import VALUE_FORMAT_LABELS, format_value, parse_value, sae_dtc, value_formats, parse_sae_dtc
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QPlainTextEdit,QComboBox,QCheckBox,QSpinBox,QDialogButtonBox,QTabWidget,QWidget,QLabel,QTableWidget,QTableWidgetItem,QTableView,QPushButton,QHeaderView,QMessageBox,QFileDialog,QAbstractItemView,QAbstractSpinBox,QSplitter,QToolButton,QButtonGroup)


def item(text=""):
    return QTableWidgetItem(str(text))


class Form:
    def __init__(self,parent_layout,fields=None):
        self.widget=QWidget()
        self.layout=QFormLayout(self.widget)
        self.layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        parent_layout.addWidget(self.widget)
        # fields를 넘기면 여러 탭의 폼이 한 값 사전을 함께 쓴다(속성 대화상자 탭 구성, U6).
        self.fields=fields if fields is not None else {}

    def text(self,key,label,value="",multiline=False):
        value="" if value is None else str(value)  # 0(상태 코드 0 등)도 그대로 보인다
        widget=QPlainTextEdit(value) if multiline else QLineEdit(value)
        if multiline: widget.setMaximumHeight(90)
        self.fields[key]=widget
        self.layout.addRow(label,widget)
        return widget

    def combo(self,key,label,choices,value=None):
        combo=QComboBox()
        for text,data in choices: combo.addItem(str(text),data)
        if value is not None:
            index=combo.findData(value)
            if index>=0: combo.setCurrentIndex(index)
        self.fields[key]=combo
        self.layout.addRow(label,combo)
        return combo

    def check(self,key,label,value=False):
        c=QCheckBox(label)
        c.setChecked(bool(value))
        self.fields[key]=c
        self.layout.addRow("",c)
        return c

    def values(self):
        data={}
        for key,w in self.fields.items():
            if isinstance(w,QLineEdit): data[key]=w.text().strip()
            elif isinstance(w,QPlainTextEdit): data[key]=w.toPlainText()
            elif isinstance(w,QComboBox): data[key]=w.currentData()
            elif isinstance(w,QCheckBox): data[key]=w.isChecked()
        return data


class BaseDialog(QDialog):
    """대화상자 공통 규칙(M2-4): 단추 순서 확인 / 취소 / 적용([UI-R1220], 적용은 닫지 않음), 오른쪽 아래 크기 조절 표시([UI-R1120-Gripper]),
    처음 포커스는 첫 입력 칸([UI-R1130], focus_field를 주면 그 칸), Esc = 바꾸지 않고 닫기([UI-R1300]), 입력 칸의 Enter = 확인([UI-R1340]).
    적용 단추는 apply=True일 때만 둔다. 누르면 apply_handler(대화상자, 값)를 부르고, 호출자가 명령이 끝나면 mark_applied()를 부른다."""
    INPUTS=(QLineEdit,QPlainTextEdit,QComboBox,QAbstractSpinBox,QCheckBox,QAbstractItemView)

    def __init__(self,title,parent=None,apply=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(660,520)
        self.setSizeGripEnabled(True)
        self.layout=QVBoxLayout(self)
        flags=QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel
        if apply:flags|=QDialogButtonBox.StandardButton.Apply
        self.buttons=QDialogButtonBox(flags)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("확인")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        self.apply_button=self.buttons.button(QDialogButtonBox.StandardButton.Apply) if apply else None
        if self.apply_button is not None:
            self.apply_button.setText("적용");self.apply_button.setToolTip("바꾼 값을 반영하고 대화상자는 열어 둡니다.")
            self.apply_button.clicked.connect(self.apply)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.initial_focus=None;self._focused=False;self.apply_handler=None;self.applied=False

    def finish(self): self.layout.addWidget(self.buttons)

    def showEvent(self,event):
        super().showEvent(event)
        if not self._focused:
            self._focused=True
            QTimer.singleShot(0,self.focus_first)

    def focus_first(self):
        """요청한 칸, 없으면 포커스 순서의 첫 입력 칸(보이고 켜진 것)에 포커스를 준다."""
        target=self.initial_focus
        if target is None:
            w=self.nextInFocusChain();guard=0
            while w is not None and w is not self and guard<400:
                guard+=1
                if isinstance(w,self.INPUTS) and w.isVisible() and w.isEnabled() and w.focusPolicy() in (Qt.FocusPolicy.TabFocus,Qt.FocusPolicy.StrongFocus,Qt.FocusPolicy.WheelFocus):
                    target=w;break
                w=w.nextInFocusChain()
        if target is not None and target.isVisible():target.setFocus()

    def apply(self):
        if self.apply_handler is None:return
        try:values=self.values()
        except Exception as e:
            QMessageBox.warning(self,"적용",str(e));return
        self.apply_handler(self,values)

    def set_busy(self,busy):
        for button in (self.buttons.button(QDialogButtonBox.StandardButton.Ok),self.apply_button):
            if button is not None:button.setEnabled(not busy)

    def mark_applied(self):
        self.applied=True


class ProjectDialog(BaseDialog):
    def __init__(self,parent=None):
        super().__init__("새 ECU 프로젝트",parent)
        self.resize(500,290)
        self.layout.addWidget(QLabel("ECU 정보와 기본 변형을 포함한 프로젝트를 만듭니다."))
        self.form=Form(self.layout)
        self.form.text("name","프로젝트 / ECU 이름","새 ECU")
        self.form.text("technical_name","ECU Qualifier","NewECU")
        self.form.combo("locale","기본 언어",[("한국어","ko-KR"),("English","en-US"),("Deutsch","de-DE"),("日本語","ja")],"ko-KR")
        self.finish()

    def values(self): return self.form.values()


class MembersEditor(QWidget):
    """행 순서가 wire 순서다. 정렬 기능을 노출하지 않는다. Qualifier 열은 표의 맨 뒤에 둔다.
    값 형식(M2-5): 정수 타입(unsigned·signed·bcd) 멤버의 기본값·상수를 10진·16진·2진으로 보고 넣는다. 보이는 글자만 바뀌고 저장 값(10진 원시값)은 같다.
    BCD는 10진·16진만 쓴다([VALUI-R050]). zero_fill이면 16진을 값 크기만큼 0으로 채운다(설정). 읽을 수 없는 값은 칸을 붉게 두고 invalid()가 이유를 돌려준다."""
    NAME,TYPE,DEFAULT,CONSTANT,KIND,QUALIFIER=range(6)
    VALUE_ROLE=Qt.ItemDataRole.UserRole+1   # 기본값·상수 칸의 저장 값(정수 타입은 10진 원시값, 읽을 수 없으면 입력 글자)
    ERROR_ROLE=Qt.ItemDataRole.UserRole+2   # 읽을 수 없는 이유(없으면 None)
    INTEGER_ENCODINGS=("unsigned","signed","bcd")
    def __init__(self,catalog,rows=(),parent=None,zero_fill=False,value_format="dec"):
        super().__init__(parent)
        self.catalog=catalog
        self.types={t["id"]:t for t in (catalog or {}).get("types",[])}
        self.zero_fill=bool(zero_fill)
        self.value_format=value_format if value_format in VALUE_FORMAT_LABELS else "dec"
        self._rendering=False
        layout=QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        top=QHBoxLayout()
        hint=QLabel("순서대로 배치됩니다. 기본값·상수는 원시값입니다(0x 16진·0b 2진 입력 가능). bytes는 공백 구분 16진수, ascii는 글자입니다.")
        hint.setWordWrap(True)
        top.addWidget(hint,1)
        top.addWidget(QLabel("값 형식"))
        self.format_group=QButtonGroup(self);self.format_group.setExclusive(True)
        self.format_buttons={}
        for fmt,label in VALUE_FORMAT_LABELS.items():
            b=QPushButton(label);b.setCheckable(True);b.setObjectName(f"valueFormat_{fmt}");b.setChecked(fmt==self.value_format)
            b.setToolTip(f"정수 멤버의 기본값·상수를 {label}으로 봅니다. 저장 값은 같습니다."+(" BCD 멤버는 2진 형식이 없어 10진으로 보입니다." if fmt=="bin" else ""))
            b.clicked.connect(lambda _=False,f=fmt:self.set_value_format(f))
            self.format_group.addButton(b);self.format_buttons[fmt]=b;top.addWidget(b)
        layout.addLayout(top)
        self.table=QTableWidget(0,6)
        self.table.setHorizontalHeaderLabels(["이름","타입","기본값","상수값","종류","Qualifier"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(self._item_changed)
        layout.addWidget(self.table)
        bar=QHBoxLayout()
        for label,fn in (("+ 데이터",lambda:self.add()),("+ 예약 비트",self.add_reserved),("− 삭제",self.remove),("↑",lambda:self.move(-1)),("↓",lambda:self.move(1))):
            b=QPushButton(label);b.clicked.connect(fn);bar.addWidget(b)
        layout.addLayout(bar)
        for row in rows: self.add(row)

    # ------------------------------------------------------------ 값 형식(M2-5)
    def _row_type(self,r):
        combo=self.table.cellWidget(r,self.TYPE)
        t=self.types.get(combo.currentData()) if isinstance(combo,QComboBox) else None
        return (t.get("encoding"),t.get("bit_length")) if t else (None,None)

    def _is_data(self,r):
        k=self.table.item(r,self.KIND)
        return k is not None and k.text()=="data"

    def _render(self,cell,value,error=None):
        """칸 하나를 저장 값과 지금 형식으로 다시 쓴다. 읽을 수 없는 값은 입력 글자 그대로 붉게 둔다."""
        encoding,bits=self._row_type(cell.row())
        self._rendering=True
        try:
            cell.setData(self.VALUE_ROLE,value);cell.setData(self.ERROR_ROLE,error)
            if error:
                cell.setText(value);cell.setBackground(QColor("#fde2df"));cell.setToolTip(error)
            else:
                cell.setText(format_value(value,encoding,bits,self.value_format,self.zero_fill) if encoding in self.INTEGER_ENCODINGS else value)
                cell.setBackground(QColor(0,0,0,0))
                tip=""
                if encoding in self.INTEGER_ENCODINGS and value:
                    tip=f"저장 값(10진 원시값): {value}"
                    if self.value_format not in value_formats(encoding):tip+=" · BCD는 2진 형식이 없어 10진으로 보입니다."
                cell.setToolTip(tip)
        finally:
            self._rendering=False

    def _read(self,cell,text):
        """입력 글자를 지금 행 타입·형식으로 읽어 칸을 다시 쓴다."""
        encoding,bits=self._row_type(cell.row())
        if encoding not in self.INTEGER_ENCODINGS:
            self._render(cell,text);return
        try:self._render(cell,parse_value(text,encoding,bits,self.value_format))
        except ValueError as e:self._render(cell,str(text).strip(),str(e))

    def _item_changed(self,cell):
        if self._rendering or cell.column() not in (self.DEFAULT,self.CONSTANT) or not self._is_data(cell.row()):return
        self._read(cell,cell.text())

    def _retype(self,combo):
        """타입을 바꾸면 그 행의 기본값·상수를 새 타입으로 다시 보인다(저장 값은 그대로, 읽을 수 없던 값은 다시 읽는다)."""
        for r in range(self.table.rowCount()):
            if self.table.cellWidget(r,self.TYPE) is combo and self._is_data(r):self._refresh_row(r,reparse=True)

    def _refresh_row(self,r,reparse=False):
        for col in (self.DEFAULT,self.CONSTANT):
            cell=self.table.item(r,col)
            if cell is None:continue
            value=cell.data(self.VALUE_ROLE);value=cell.text() if value is None else value
            if cell.data(self.ERROR_ROLE):self._read(cell,value)  # 읽을 수 없던 값은 지금 형식·타입으로 다시 읽는다
            elif reparse:
                encoding,bits=self._row_type(r)
                if encoding in self.INTEGER_ENCODINGS:
                    try:self._render(cell,parse_value(value,encoding,bits,"dec"))
                    except ValueError as e:self._render(cell,value,str(e))
                else:self._render(cell,value)
            else:self._render(cell,value)

    def set_value_format(self,fmt):
        if fmt not in VALUE_FORMAT_LABELS:return
        self.value_format=fmt
        if not self.format_buttons[fmt].isChecked():self.format_buttons[fmt].setChecked(True)
        for r in range(self.table.rowCount()):
            if self._is_data(r):self._refresh_row(r)

    def invalid(self):
        """읽을 수 없는 기본값·상수가 있으면 첫 이유('이름: 이유'), 없으면 None."""
        for r in range(self.table.rowCount()):
            if not self._is_data(r):continue
            for col,label in ((self.DEFAULT,"기본값"),(self.CONSTANT,"상수값")):
                cell=self.table.item(r,col)
                if cell is not None and cell.data(self.ERROR_ROLE):return f"{self.table.item(r,self.NAME).text()} {label}: {cell.data(self.ERROR_ROLE)}"
        return None

    def add(self,row=None):
        row=row or {}
        i=self.table.rowCount()
        self.table.insertRow(i)
        kind=row.get("kind","data")
        self._rendering=True
        try:
            self.table.setItem(i,self.NAME,item(row.get("name",f"Field {i+1}")))
            self.table.setItem(i,self.QUALIFIER,item(row.get("qualifier",f"Field_{i+1}")))
            combo=QComboBox()
            for t in self.catalog.get("types",[]): combo.addItem(t["name"],t["id"])
            index=combo.findData(row.get("data_type_id"))
            if index>=0: combo.setCurrentIndex(index)
            self.table.setCellWidget(i,self.TYPE,combo)
            self.table.setItem(i,self.DEFAULT,item(row.get("default_text") or ""))
            self.table.setItem(i,self.CONSTANT,item(row.get("constant_text") or ""))
            k=item(kind)
            k.setData(Qt.ItemDataRole.UserRole,dict(row))
            k.setFlags(k.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i,self.KIND,k)
        finally:
            self._rendering=False
        if kind=="data":
            for col,key in ((self.DEFAULT,"default_text"),(self.CONSTANT,"constant_text")):self._render(self.table.item(i,col),str(row.get(key) or ""))
            combo.currentIndexChanged.connect(lambda _=0,c=combo:self._retype(c))
        if kind!="data":
            combo.setEnabled(False)
            self.table.item(i,self.NAME).setText(("예약 비트"+(": "+row["label"] if row.get("label") else "")) if kind=="reserved" else ("비트필드: " if row.get("structure_kind")=="bitfield" else "구조: ")+row.get("name","") if kind=="structure" else "반복: "+row.get("name","") if kind=="iteration" else "DTC" if kind=="dtc" else "고장 메모리 레코드: "+str(row.get("record_role")) if kind=="fault_record" else kind)
            self.table.item(i,self.DEFAULT).setText(str(row.get("reserved_bits",8)) if kind=="reserved" else row.get("hex",""))
            for col in (self.NAME,self.QUALIFIER,self.CONSTANT): self.table.item(i,col).setFlags(self.table.item(i,col).flags() & ~Qt.ItemFlag.ItemIsEditable)
            if kind not in ("reserved","literal"): self.table.item(i,self.DEFAULT).setFlags(self.table.item(i,self.DEFAULT).flags() & ~Qt.ItemFlag.ItemIsEditable)

    def add_reserved(self): self.add({"kind":"reserved","reserved_bits":8})

    def remove(self):
        rows=sorted({i.row() for i in self.table.selectedIndexes()},reverse=True)
        for i in rows: self.table.removeRow(i)

    def move(self,direction):
        index=self.table.currentRow()
        target=index+direction
        if index<0 or target<0 or target>=self.table.rowCount(): return
        rows=self.values();rows[index],rows[target]=rows[target],rows[index]
        self.table.setRowCount(0)
        for row in rows: self.add(row)
        self.table.selectRow(target)

    def values(self):
        rows=[]
        for i in range(self.table.rowCount()):
            kind=self.table.item(i,self.KIND).text()
            old=self.table.item(i,self.KIND).data(Qt.ItemDataRole.UserRole) or {}
            if kind=="data":
                stored=lambda col:(lambda cell:cell.text() if cell.data(self.VALUE_ROLE) is None else str(cell.data(self.VALUE_ROLE)))(self.table.item(i,col))
                row={"kind":kind,"name":self.table.item(i,self.NAME).text(),"qualifier":self.table.item(i,self.QUALIFIER).text(),"data_type_id":self.table.cellWidget(i,self.TYPE).currentData(),
                     "default_text":stored(self.DEFAULT),"constant_text":stored(self.CONSTANT)}
            elif kind=="reserved": row={"kind":kind,"reserved_bits":self.table.item(i,self.DEFAULT).text()}
            elif kind=="literal": row={"kind":kind,"hex":self.table.item(i,self.DEFAULT).text()}
            else: row=old
            rows.append(row)
        return rows


class DtcAttributeEditor(QWidget):
    """DTC 속성 탭. 원본 CDD의 레코드 항목 템플릿마다 한 행(항목 · 값 · 키)이다.
    텍스트(Set/Reset condition 등)·부호 없는 정수는 값 칸을 두 번 눌러 고치고, 열거(Corrective action 등)는 템플릿 값 목록에서 고른다.
    열거의 원시값(#raw)은 고른 값을 따라 저장되므로 따로 보이지 않는다. 다국어 복제(@언어)와 user.* 속성은 텍스트로 고친다.
    그 밖의 속성(가져오기 메타데이터 등)은 회색·읽기 전용이다. 값을 비우면 항목을 지운다(필수 항목은 적용할 때 거부된다)."""
    NAME,VALUE,KEY=range(3)

    def __init__(self,items,attributes,parent=None):
        super().__init__(parent)
        box=QVBoxLayout(self);box.setContentsMargins(0,0,0,0)
        hint=QLabel("값 칸을 두 번 눌러 고칩니다. 열거 항목은 목록에서 고릅니다. 비우면 그 항목을 지웁니다. 회색 행은 읽기 전용입니다. "
                    "여러 줄 값은 '상세 보기'에서 고칩니다. 키는 항목 이름의 툴팁에 있습니다.")
        hint.setWordWrap(True);box.addWidget(hint)
        # M2-3: 상세 보기(오른쪽 큰 편집 영역), 빈 항목 숨기기, 항목 ▲▼(Alt+↑/↓)
        bar=QHBoxLayout()
        self.detail_button=QPushButton("상세 보기");self.detail_button.setCheckable(True)
        self.detail_button.setToolTip("선택한 항목의 값을 오른쪽 큰 편집 영역에서 봅니다(여러 줄 텍스트 편집).")
        self.hide_empty=QCheckBox("빈 항목 숨기기")
        self.up_button=QToolButton();self.up_button.setText("▲");self.up_button.setToolTip("이전 항목 (Alt+↑)")
        self.down_button=QToolButton();self.down_button.setText("▼");self.down_button.setToolTip("다음 항목 (Alt+↓)")
        for w in (self.detail_button,self.hide_empty):bar.addWidget(w)
        bar.addStretch();bar.addWidget(self.up_button);bar.addWidget(self.down_button)
        box.addLayout(bar)
        self.table=QTableWidget(0,3);self.table.setObjectName("dtcAttributes")
        self.table.setHorizontalHeaderLabels(["항목","값","키"]);self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked|QAbstractItemView.EditTrigger.EditKeyPressed|QAbstractItemView.EditTrigger.AnyKeyPressed)
        header=self.table.horizontalHeader();header.setStretchLastSection(False)
        for col,mode in ((self.NAME,QHeaderView.ResizeMode.ResizeToContents),(self.VALUE,QHeaderView.ResizeMode.Stretch),(self.KEY,QHeaderView.ResizeMode.ResizeToContents)):
            header.setSectionResizeMode(col,mode)
        self.table.setColumnHidden(self.KEY,True)  # 키 열은 툴팁으로(M2-3)
        self.detail=QPlainTextEdit();self.detail.setObjectName("dtcAttributeDetail");self.detail.setVisible(False)
        self.detail.setPlaceholderText("항목을 고르면 값 전체가 보입니다.")
        split=QSplitter(Qt.Orientation.Horizontal);split.setChildrenCollapsible(False)
        split.addWidget(self.table);split.addWidget(self.detail);split.setStretchFactor(0,3);split.setStretchFactor(1,2)
        box.addWidget(split,1)
        self._syncing=False
        self.detail_button.toggled.connect(self._toggle_detail)
        self.hide_empty.toggled.connect(self.apply_hide_empty)
        self.up_button.clicked.connect(lambda:self.step(-1));self.down_button.clicked.connect(lambda:self.step(1))
        for sequence,step in (("Alt+Up",-1),("Alt+Down",1)):
            shortcut=QShortcut(QKeySequence(sequence),self);shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda s=step:self.step(s))
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.table.itemChanged.connect(self._item_changed)
        self.detail.textChanged.connect(self._detail_changed)
        self.rows=[]
        values={a["key"]:a for a in attributes}
        done=set()
        for it in items:
            key=it["key"];current=values.get(key)
            done.add(key)
            tip=f"{it['name']} · {'필수' if it['required'] else '선택'} 항목"
            if it["kind"]=="enum":
                done.add(key+"#raw")
                combo=QComboBox()
                if not it["required"] or current is None:combo.addItem("(없음)",None)
                for c in it["choices"]:combo.addItem(f"{c['label']} ({c['raw']})",c["raw"])
                raw=values.get(key+"#raw",{}).get("value_text")
                try:initial=None if raw is None else int(str(raw).strip(),16 if str(raw).strip().lower().startswith("0x") else 10)
                except ValueError:initial=None
                index=combo.findData(initial)
                if initial is not None and index<0:  # 템플릿에 없는 원시값(원문 그대로 보존): 목록 끝에 두고 그대로 둔다
                    combo.addItem(f"{(current or {}).get('value_text','')} ({initial}) · 템플릿에 없음",initial);index=combo.count()-1
                combo.setCurrentIndex(max(0,index))
                self._add(it["name"],key,tip,combo=combo,kind="enum",initial=combo.currentData())
            else:
                self._add(it["name"],key,tip+(" · 0 이상 정수(0x 16진 가능)" if it["kind"]=="uns" else ""),text=(current or {}).get("value_text",""),kind=it["kind"])
        names={it["key"]:it["name"] for it in items}
        text_keys={it["key"] for it in items if it["kind"]=="text"}
        for a in attributes:
            key=a["key"]
            if key in done:continue
            base,_,locale=key.partition("@")
            if key.startswith("user."):
                self._add(key[5:],key,"사용자 속성",text=a["value_text"],kind="text")
            elif locale and base in text_keys:
                self._add(f"{names[base]} ({locale})",key,f"{names[base]} · {locale} 번역",text=a["value_text"],kind="text")
            else:
                self._add(key[4:] if key.startswith("cdd.") else key,key,"원본 CDD에서 가져온 값 · 읽기 전용(레코드 항목 템플릿이 없거나 가져오기 메타데이터)",text=a["value_text"],kind=None)

    def _add(self,name,key,tip,text="",combo=None,kind="text",initial=None):
        r=self.table.rowCount();self.table.insertRow(r)
        fixed=Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable
        for col,value in ((self.NAME,name),(self.KEY,key)):
            cell=item(value);cell.setFlags(fixed);cell.setToolTip(f"{tip}\n키: {key}" if col==self.NAME else tip);self.table.setItem(r,col,cell)
        if combo is not None:
            self.table.setCellWidget(r,self.VALUE,combo)
        else:
            cell=item(text);cell.setToolTip(text or tip)
            if kind is None:
                cell.setFlags(fixed)
                for col in (self.NAME,self.VALUE,self.KEY):
                    target=cell if col==self.VALUE else self.table.item(r,col)
                    target.setForeground(QColor("#8a94a6"))
            self.table.setItem(r,self.VALUE,cell)
        if kind is not None:self.rows.append({"row":r,"key":key,"kind":kind,"initial":initial if combo is not None else text,"combo":combo})

    # ------------------------------------------------------------ M2-3 보조
    def _row_value(self,r):
        widget=self.table.cellWidget(r,self.VALUE)
        if isinstance(widget,QComboBox):return widget.currentText() if widget.currentData() is not None else ""
        cell=self.table.item(r,self.VALUE)
        return cell.text() if cell is not None else ""

    def apply_hide_empty(self,*_):
        hide=self.hide_empty.isChecked()
        for r in range(self.table.rowCount()):self.table.setRowHidden(r,hide and not self._row_value(r).strip())

    def step(self,delta):
        """항목 사이 이동(Alt+↑/↓). 숨긴 행은 건너뛴다."""
        rows=[r for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)]
        if not rows:return
        current=self.table.currentRow()
        if current not in rows:target=rows[0] if delta>0 else rows[-1]
        else:target=rows[max(0,min(len(rows)-1,rows.index(current)+delta))]
        self.table.setCurrentCell(target,self.VALUE);self.table.selectRow(target)

    def _toggle_detail(self,on):
        self.detail.setVisible(bool(on));self._show_detail()

    def _editable_text(self,r):
        cell=self.table.item(r,self.VALUE)
        return self.table.cellWidget(r,self.VALUE) is None and cell is not None and bool(cell.flags()&Qt.ItemFlag.ItemIsEditable)

    def _show_detail(self):
        if not self.detail.isVisible():return
        r=self.table.currentRow()
        self._syncing=True
        try:
            self.detail.setPlainText(self._row_value(r) if r>=0 else "")
            self.detail.setReadOnly(not (r>=0 and self._editable_text(r)))
        finally:
            self._syncing=False

    def _detail_changed(self):
        if self._syncing:return
        r=self.table.currentRow()
        if r>=0 and self._editable_text(r):
            self._syncing=True
            try:self.table.item(r,self.VALUE).setText(self.detail.toPlainText())
            finally:self._syncing=False

    def _item_changed(self,cell):
        if self._syncing or cell.column()!=self.VALUE:return
        if cell.row()==self.table.currentRow():self._show_detail()

    def mark_saved(self):
        """적용 뒤: 지금 값을 처음 값으로 삼는다(다시 보내지 않는다)."""
        for row in self.rows:
            row["initial"]=row["combo"].currentData() if row["combo"] is not None else self.table.item(row["row"],self.VALUE).text()

    def changes(self):
        """바뀐 행만: 열거 {"key","raw"}(None = 비움), 그 밖 {"key","value"}."""
        out=[]
        for row in self.rows:
            if row["combo"] is not None:
                current=row["combo"].currentData()
                if current!=row["initial"]:out.append({"key":row["key"],"raw":current})
            else:
                current=self.table.item(row["row"],self.VALUE).text()
                if current!=row["initial"]:out.append({"key":row["key"],"value":current})
        return out


class ObjectDialog(BaseDialog):
    """객체 속성 대화상자. 탭: 기본 속성 / (타입) 변환·예외값·열거값·Packet / (DID·서비스) 구조 / 속성(Attributes: DTC는 편집, 그 밖은 읽기 전용).
    navigation=(이전 있음, 다음 있음)이면 ◀ 이전 · 다음 ▶ 버튼을 둔다(U6). 누르면 navigate=-1/+1로 닫히고, 호출자가
    바뀐 값이 있으면 저장한 뒤 이웃 객체의 대화상자를 연다."""
    def __init__(self,kind,catalog,record=None,variant=None,parent=None,focus_field=None,nrcs=None,navigation=(False,False),siblings=None,defaults=None):
        labels={"did":"DID","dtc":"DTC","data_type":"데이터 타입","service":"서비스","variant":"변형","state":"상태","ecu":"ECU","diagnostic_instance":"진단 인스턴스"}
        record=record or {}
        defaults=defaults or {}
        # 적용(닫지 않음, M2-4)은 기존 객체를 고칠 때만 둔다. 새 객체는 확인으로 만든다.
        super().__init__(labels[kind]+(" 편집" if record else " 추가"),parent,apply=bool(record.get("id")))
        self.resize(820,680)
        self.kind,self.record,self.variant=kind,record,variant
        self.navigate=0;self.navigate_id=None;self.initial_values=None
        number=defaults.get("number")  # [UI-R335] 주 창이 제안한 새 번호(M1-5). 없으면 첫 번호
        # 값 형식(M2-5): 구조 표의 처음 형식과 16진 0 채우기(설정 '표시' 탭)
        member_options={"zero_fill":bool(defaults.get("hex_zero_fill",False)),"value_format":defaults.get("value_format","dec")}
        if kind=="dtc" and record.get("id") and siblings:
            # M2-3(DTC Property Page): 위쪽 DTC 콤보 + ▲▼로 DTC 사이를 옮긴다. 바꾼 값이 있으면 적용한 뒤 옮긴다.
            bar=QHBoxLayout();bar.addWidget(QLabel("DTC"))
            self.sibling_combo=QComboBox();self.sibling_combo.setObjectName("dtcNavigator")
            for sid,label in siblings:self.sibling_combo.addItem(str(label),sid)
            self.sibling_combo.setCurrentIndex(max(0,self.sibling_combo.findData(record["id"])))
            self.sibling_combo.activated.connect(self._sibling_chosen)
            bar.addWidget(self.sibling_combo,1)
            previous,following=navigation
            for text,step,enabled,tip in (("▲",-1,previous,"이전 DTC"),("▼",1,following,"다음 DTC")):
                button=QToolButton();button.setText(text);button.setEnabled(bool(enabled));button.setToolTip(tip+" (바꾼 값이 있으면 적용한 뒤 옮깁니다)")
                button.clicked.connect(lambda _=False,s=step:self.go(s));bar.addWidget(button)
            self.layout.addLayout(bar)
            navigation=(False,False)  # 아래 ◀ 이전·다음 ▶ 단추 대신 위쪽 콤보·▲▼를 쓴다
        self.tabs=QTabWidget()
        self.layout.addWidget(self.tabs)
        general=QWidget();gl=QVBoxLayout(general)
        self.form=Form(gl)
        self.form.text("name","이름",record.get("name",""))
        self.form.text("qualifier","Qualifier",record.get("qualifier",""))
        self.form.text("description","설명",record.get("description",""),True)
        self.tabs.addTab(general,"기본 속성")
        self.members=None
        if kind=="did":
            self.form.text("identifier","DID 번호 (hex / dec)",f"0x{record['identifier']:04X}" if record else f"0x{number if number is not None else 1:04X}")
            if not record: self.form.check("read_service","읽기 서비스 (0x22)도 생성",True)
            self.members=MembersEditor(catalog,record.get("members",[]),**member_options)
            self.tabs.addTab(self.members,"데이터 구조")
        elif kind=="dtc":
            # 값 형식(M2-5): DTC 코드를 16진·10진으로 보고 넣는다. 저장 값(24비트 정수)은 같다. 접두어 없는 입력은 지금 형식으로 읽는다.
            self.code_format="hex"
            code_edit=QLineEdit(f"0x{record['code']:06X}" if record else f"0x{number if number is not None else 1:06X}");code_edit.setObjectName("dtcCode")
            self.form.fields["code"]=code_edit
            code_row=QHBoxLayout();code_row.addWidget(code_edit,1)
            self.code_buttons={}
            code_group=QButtonGroup(self)
            for fmt,label in (("hex","16진"),("dec","10진")):
                button=QPushButton(label);button.setCheckable(True);button.setChecked(fmt=="hex");button.setObjectName(f"codeFormat_{fmt}")
                button.setToolTip(f"DTC 코드를 {label}으로 봅니다. 저장 값은 같습니다.")
                button.clicked.connect(lambda _=False,f=fmt:self.set_code_format(f));code_group.addButton(button);self.code_buttons[fmt]=button;code_row.addWidget(button)
            self.code_hint=QLabel("");self.code_hint.setStyleSheet("color:#677a92");code_row.addWidget(self.code_hint)
            code_edit.textChanged.connect(self._code_changed);self._code_changed()
            self.form.layout.addRow("DTC 코드 (24비트)",code_row)
        elif kind=="variant":
            self.form.combo("source_id","기준 변형",[(v["name"],v["id"]) for v in catalog["variants"]],variant)
            self.layout.addWidget(QLabel("새 변형은 기준 변형의 DID·DTC·서비스를 공유합니다."))
        elif kind=="ecu": self.form.text("manufacturer","제조사",record.get("manufacturer",""))
        elif kind=="state":
            self.form.combo("group_id","상태 그룹",[(g["name"],g["id"]) for g in catalog["groups"]],record.get("group_id") or defaults.get("group_id"))
            self.form.text("code","상태 코드 (0..255)",record.get("code",str(number) if number is not None else "1"))
        elif kind=="data_type":
            usage=int(record.get("usage") or 0) if record else 0
            self.form.combo("encoding","인코딩",[(x,x) for x in ("unsigned","signed","ascii","bytes","bcd","float","packet")],record.get("encoding","unsigned"))
            self.form.text("bit_length","비트 길이 (정수 1..64)",record.get("bit_length",16))
            self.form.text("array_length","고정 배열 길이 (ascii / bytes, bytes는 비우면 PDU 끝까지 가변)",record.get("array_length",1) if record.get("array_length",1) is not None else "")
            # schema 13: 가변 바이트 필드의 앞 길이 바이트(CDD sz=lszbyte~lsz4bytes, ODX LEADING-LENGTH-INFO-TYPE). 비우면 PDU 끝까지.
            self.form.combo("leading_length_bits","앞 길이 바이트 (가변 bytes만)",[("없음 (PDU 끝까지)",""),("1바이트","8"),("2바이트","16"),("3바이트","24"),("4바이트","32")],
                            "" if record.get("leading_length_bits") is None else str(record.get("leading_length_bits")))
            self.form.combo("byte_order","바이트 순서",[("Big endian","big"),("Little endian","little")],record.get("byte_order",defaults.get("byte_order","big")))
            self.form.text("unit","단위",record.get("unit",""))
            if record.get("length_text"):gl.addWidget(QLabel("길이 (바이트:비트): "+record["length_text"]))
            self.length_lock=None
            if usage:
                # 사용 중 타입 보호(U6): CANdela "Data type is already in use. Properties affecting the length cannot be modified."
                for key in ("encoding","bit_length","array_length","leading_length_bits"):self.form.fields[key].setEnabled(False)
                self.length_lock=QLabel(f"사용 중인 타입입니다(데이터 객체 {usage}곳). 길이에 영향을 주는 속성(인코딩·비트 길이·배열 길이·Packet 구조)은 바꿀 수 없습니다. 바꾸려면 독립 복사로 새 타입을 만드세요.")
                self.length_lock.setWordWrap(True);self.length_lock.setStyleSheet("color:#a06b19");gl.addWidget(self.length_lock)
            # 변환 탭(U6): 변환 종류·선형 계수·유효 범위·미리보기. 원본 CDD 변환식은 Divisor가 있을 때만 그 형식으로 보인다.
            conversion=QWidget();cl=QVBoxLayout(conversion)
            self.conversion_form=Form(cl,self.form.fields)
            self.conversion_form.combo("conversion","변환",[("동일값","identity"),("선형","linear"),("열거 테이블","text_table"),("수식 (CDD, ODX 동일값)","formula"),("구간별 선형 (CDD COMPTBL)","scale_linear"),("특성 곡선 (CDD VALTBL)","tab_intp")],record.get("conversion","identity"))
            self.conversion_form.text("factor_text","선형 계수 (10진수 또는 분수, 예: 359/255)",record.get("factor_text") or "1")
            self.conversion_form.text("offset_text","선형 오프셋",record.get("offset_text") or "0")
            self.conversion_form.text("raw_lower","선형 유효 범위 하한 원시값 (비우면 제한 없음)",record.get("raw_lower") or "")
            self.conversion_form.text("raw_upper","선형 유효 범위 상한 원시값 (비우면 제한 없음)",record.get("raw_upper") or "")
            # schema 12: CDD FORMULADT 수식 원문. 계산하지 않고 ODX에 IDENTICAL + 설명으로 낸다(Vector 출력과 같음).
            self.conversion_form.text("formula_expression","수식 coded→phys (수식 변환만, 예: x/170 · 계산하지 않음)",record.get("formula_expression") or "")
            if record.get("formula_text"):
                source=QLabel("원본 CDD 변환식: "+record["formula_text"]);source.setWordWrap(True);cl.addWidget(source)
            cl.addWidget(QLabel("phys = 계수 × raw + 오프셋 (계수는 분수로 정확히 저장합니다)"))
            preview=QHBoxLayout();preview.addWidget(QLabel("미리보기 (저장하지 않음)"))
            self.raw_preview=QLineEdit();self.raw_preview.setPlaceholderText("원시값");preview.addWidget(self.raw_preview)
            preview.addWidget(QLabel("→"));self.phys_preview=QLabel("—");preview.addWidget(self.phys_preview,1)
            preview.addWidget(QLabel("선형·동일값 변환만 계산합니다."));cl.addLayout(preview);cl.addStretch()
            self.tabs.addTab(conversion,"변환")
            for key in ("factor_text","offset_text","bit_length","raw_lower","raw_upper"):self.form.fields[key].textChanged.connect(self.update_type_preview)
            for key in ("encoding","conversion"):self.form.fields[key].currentIndexChanged.connect(self.update_type_preview)
            self.raw_preview.textChanged.connect(self.update_type_preview);self.update_type_preview()
            self.excl=QTableWidget(0,5)
            self.excl.setHorizontalHeaderLabels(["하한 원시값","상한 원시값","종류","언어","표시명"])
            self.excl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            xw=QWidget();xl=QVBoxLayout(xw);xl.addWidget(QLabel("예외값: undef=정의되지 않음, noSignal=신호 없음, invalidSignal=무효 신호, notImplemented=미구현, unexpectedResult=예상 밖 결과. 같은 범위·종류의 행은 언어별 표시명입니다."));xl.addWidget(self.excl)
            addx=QPushButton("+ 예외값");addx.clicked.connect(self.add_exclusion);xl.addWidget(addx)
            removex=QPushButton("− 선택 예외 삭제");removex.clicked.connect(lambda:self.excl.removeRow(self.excl.currentRow()));xl.addWidget(removex)
            for e in record.get("exclusions",[]): self.add_exclusion(e)
            self.tabs.addTab(xw,"예외값")
            self.enum=QTableWidget(0,4)
            self.enum.setHorizontalHeaderLabels(["하한 원시값","상한 원시값","언어","표시명"])
            self.enum.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            ew=QWidget();el=QVBoxLayout(ew);el.addWidget(self.enum)
            add=QPushButton("+ 열거 범위");add.clicked.connect(self.add_enum);el.addWidget(add)
            remove=QPushButton("− 선택 범위 삭제");remove.clicked.connect(lambda:self.enum.removeRow(self.enum.currentRow()));el.addWidget(remove)
            for e in record.get("entries",[]): self.add_enum(e)
            self.tabs.addTab(ew,"열거값")
            self.members=MembersEditor(catalog,record.get("members",[]),**member_options)
            if usage:self.members.setEnabled(False)
            self.packet_index=self.tabs.addTab(self.members,"Packet 구조")
            self.form.fields["encoding"].currentIndexChanged.connect(self.update_packet_tab)
            self.update_packet_tab()
        elif kind=="service":
            self.form.combo("sid","서비스 SID",[("0x10 · Session",16),("0x11 · Reset",17),("0x22 · Read DID",34),("0x2E · Write DID",46),("0x31 · Routine",49),("0x3E · Tester present",62)],record.get("sid",34))
            self.form.text("subfunction","Subfunction (0..127)",record.get("subfunction") or 0)
            self.form.text("routine_identifier","Routine ID",f"0x{record['routine_identifier']:04X}" if record.get("routine_identifier") is not None else "0x0001")
            self.form.combo("did_id","DID (0x22 / 0x2E)",[(f"0x{d['identifier']:04X} · {d['name']}",d["id"]) for d in catalog["dids"]],record.get("did_id"))
            self.form.check("physical","Physical addressing",record.get("physical",True))
            self.form.check("functional","Functional addressing",record.get("functional",False))
            self.form.check("suppress_positive","Suppress positive response",record.get("suppress_positive",False))
            self.form.check("enabled","사용",record.get("enabled",True))
            selected=record.get("nrcs") or [19,34,49]
            catalog_nrcs={n["code"]:(n.get("name") or "") for n in (nrcs or [])}
            self.nrc_codes=sorted(set(catalog_nrcs)|set(selected))
            self.nrc_table=QTableWidget(len(self.nrc_codes),2)
            self.nrc_table.setHorizontalHeaderLabels(["선택","NRC"])
            self.nrc_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            self.nrc_table.verticalHeader().setVisible(False)
            for i,code in enumerate(self.nrc_codes):
                cell=item();cell.setFlags(Qt.ItemFlag.ItemIsUserCheckable|Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable)
                cell.setCheckState(Qt.CheckState.Checked if code in selected else Qt.CheckState.Unchecked)
                self.nrc_table.setItem(i,0,cell)
                name=catalog_nrcs.get(code,"")
                self.nrc_table.setItem(i,1,item(f"0x{code:02X}"+(f" · {name}" if name else "")))
            self.nrc_table.setMaximumHeight(150)
            self.form.layout.addRow("NRC 선택",self.nrc_table)
            self.request=MembersEditor(catalog,record.get("request_members",[]),**member_options)
            self.positive=MembersEditor(catalog,record.get("positive_members",[]),**member_options)
            self.tabs.addTab(self.request,"요청 payload")
            self.tabs.addTab(self.positive,"정응답 payload")
            hint=QLabel("SID·Subfunction·DID 헤더는 자동 생성됩니다. 0x22 / 0x2E의 데이터는 연결한 DID에서 가져옵니다.")
            hint.setWordWrap(True);self.layout.addWidget(hint)
        gl.addStretch()
        attributes=[a for a in record.get("attributes") or [] if not a["key"].startswith("app.")]
        self.attribute_editor=None
        if kind=="dtc" and record.get("id") and (attributes or record.get("record_items")):
            # DTC 속성 탭: 원본 레코드 항목(Set/Reset condition, Corrective action 등)과 user.* 속성을 여기서 바꾼다.
            self.attribute_editor=DtcAttributeEditor(record.get("record_items") or [],attributes)
            self.tabs.addTab(self.attribute_editor,f"속성 ({self.attribute_editor.table.rowCount()})")
        elif kind=="dtc" and not record.get("id") and (catalog or {}).get("dtc_record_items"):
            # DTC 추가 대화상자에도 속성 탭(M2-3). 넣은 항목만 새 DTC에 적용한다.
            self.attribute_editor=DtcAttributeEditor(catalog["dtc_record_items"],[])
            self.tabs.addTab(self.attribute_editor,f"속성 ({self.attribute_editor.table.rowCount()})")
        elif attributes:
            # 속성(Attributes) 탭(U6): DTC 밖의 객체는 원본·사용자 속성을 읽기 전용으로 보인다. 사용자 속성은 Attribute 개요에서 일괄 설정한다.
            table=QTableWidget(len(attributes),3);table.setHorizontalHeaderLabels(["키","타입","값"])
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);table.verticalHeader().setVisible(False)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            for i,a in enumerate(attributes):
                for j,value in enumerate((a["key"],a["value_type"],a["value_text"])):table.setItem(i,j,item(value))
            self.tabs.addTab(table,f"속성 ({len(attributes)})")
        if record.get("variants"):
            info=QLabel("공유 영향: "+", ".join(record["variants"]));info.setWordWrap(True);self.layout.addWidget(info)
        self._setup_qualifier(expanded=focus_field=="qualifier")
        if focus_field and focus_field in self.form.fields:
            self.initial_focus=self.form.fields[focus_field];self.form.fields[focus_field].setFocus()
        else:self.initial_focus=self.form.fields["name"]  # [UI-R1130] 첫 칸(이름). 위쪽 DTC 콤보보다 먼저
        previous,following=navigation
        if previous or following:
            for label,step,enabled in (("◀ 이전",-1,previous),("다음 ▶",1,following)):
                button=QPushButton(label);button.setEnabled(bool(enabled));button.setToolTip("바꾼 값이 있으면 적용한 뒤 목록의 이전·다음 객체를 엽니다.")
                button.clicked.connect(lambda _=False,s=step:self.go(s))
                self.buttons.addButton(button,QDialogButtonBox.ButtonRole.ActionRole)
        self.finish()
        self.initial_values=self._snapshot()

    def go(self,step):
        self.navigate=step
        self.accept()

    # ---- Qualifier 자동 생성(M2-1): [QUAL-R010] 새 객체는 이름에서, [QUAL-R020] 이름을 바꾸면 아직 이름에서 만든 값일 때만 따라간다.
    def _number(self):
        """D3 Qualifier(종류 + 번호)에 쓰는 번호: DTC 코드·DID 번호·상태 코드. 읽을 수 없으면 None."""
        if self.kind=="dtc":
            try:return self.dtc_code()
            except ValueError:return parse_sae_dtc(self.form.fields["code"].text())
        field={"did":"identifier","state":"code"}.get(self.kind)
        widget=self.form.fields.get(field) if field else None
        if widget is None:return None
        text=widget.text().strip()
        try:return int(text,16 if text.lower().startswith("0x") else 10)
        except ValueError:return None

    def _setup_qualifier(self,expanded=False):
        """Qualifier 줄은 접어 두고 현재 값과 자동 여부만 보인다. 펼치면 직접 고치고 '이름에서 다시 만들기'로 되돌린다.
        사용자가 고치면 따라가기를 멈춘다. 가져온 CDD 객체(원본 대응이 있는 객체)는 따라가지 않는다."""
        record=self.record
        name_edit,q_edit=self.form.fields["name"],self.form.fields["qualifier"]
        imported=bool(record.get("sources"))
        self.follow_qualifier=not record or (not imported and qualifier_follows(record.get("name",""),record.get("qualifier",""),self.kind,self._number()))
        if not record:q_edit.setText(make_qualifier(name_edit.text(),self.kind,self._number()))
        layout=self.form.layout
        row,_=layout.getWidgetPosition(q_edit)
        self.qualifier_toggle=QToolButton();self.qualifier_toggle.setObjectName("qualifierToggle");self.qualifier_toggle.setCheckable(True);self.qualifier_toggle.setAutoRaise(True)
        self.qualifier_toggle.setToolTip("Qualifier(식별 이름)를 펼쳐 직접 고칩니다. 새 객체는 이름에서 만들고, 이름을 바꾸면 따라 바뀝니다.")
        layout.insertRow(row,"Qualifier",self.qualifier_toggle)
        label=layout.labelForField(q_edit)
        if label is not None:label.setText("Qualifier 입력")
        self.regenerate_button=QPushButton("이름에서 다시 만들기");self.regenerate_button.setObjectName("regenerateQualifier")
        layout.insertRow(row+2,"",self.regenerate_button)
        self.qualifier_toggle.toggled.connect(self._show_qualifier)
        name_edit.textChanged.connect(self._follow_name)
        q_edit.textEdited.connect(self._qualifier_edited)
        self.regenerate_button.clicked.connect(self.regenerate_qualifier)
        number=self.form.fields.get({"did":"identifier","dtc":"code","state":"code"}.get(self.kind,""))
        if number is not None:number.textChanged.connect(self._follow_name)
        self.qualifier_toggle.setChecked(bool(expanded));self._show_qualifier(bool(expanded))

    def _show_qualifier(self,on):
        layout=self.form.layout
        layout.setRowVisible(self.form.fields["qualifier"],bool(on));layout.setRowVisible(self.regenerate_button,bool(on))
        self._qualifier_label()

    def _qualifier_label(self):
        value=self.form.fields["qualifier"].text() or "(비어 있음)"
        mode="이름에서 자동" if self.follow_qualifier else "직접 입력"
        self.qualifier_toggle.setText(f"{value}   ·   {mode}   {'▾' if self.qualifier_toggle.isChecked() else '▸'}")

    def _follow_name(self,*_):
        if self.follow_qualifier:self.form.fields["qualifier"].setText(make_qualifier(self.form.fields["name"].text(),self.kind,self._number()))
        self._qualifier_label()

    def _qualifier_edited(self,*_):
        self.follow_qualifier=False;self._qualifier_label()

    def regenerate_qualifier(self):
        self.follow_qualifier=True;self._follow_name()

    # ------------------------------------------------------------ 값 형식(M2-5)
    def dtc_code(self):
        """DTC 코드 칸 → 정수. 0x 접두어는 형식과 상관없이 16진, 접두어가 없으면 지금 형식으로 읽는다. 읽을 수 없으면 ValueError."""
        text=self.form.fields["code"].text().strip().replace(" ","").replace("_","")
        base=16 if text.lower().startswith("0x") or self.code_format=="hex" else 10
        digits=text[2:] if text.lower().startswith("0x") else text
        if not re.fullmatch(r"[0-9A-Fa-f]+" if base==16 else r"[0-9]+",digits or ""):raise ValueError(("16진" if base==16 else "10진")+" 정수를 입력하세요.")
        value=int(digits,base)
        if value>0xFFFFFF:raise ValueError("DTC 코드는 24비트(0 ~ 0xFFFFFF)입니다.")
        return value

    def _code_changed(self,*_):
        try:self.code_hint.setText(f"SAE {sae_dtc(self.dtc_code())}")
        except ValueError as e:self.code_hint.setText(str(e))

    def set_code_format(self,fmt):
        try:value=self.dtc_code()
        except ValueError:value=None
        self.code_format=fmt
        if not self.code_buttons[fmt].isChecked():self.code_buttons[fmt].setChecked(True)
        if value is not None:self.form.fields["code"].setText(f"0x{value:06X}" if fmt=="hex" else str(value))

    def invalid_values(self):
        """확인·적용 전에 읽을 수 없는 값(구조 표의 기본값·상수, DTC 코드)의 이유. 없으면 None."""
        for editor in (self.members,getattr(self,"request",None),getattr(self,"positive",None)):
            problem=editor.invalid() if editor is not None and editor.isEnabled() else None
            if problem:return problem
        if self.kind=="dtc":
            try:self.dtc_code()
            except ValueError as e:return f"DTC 코드: {e}"
        return None

    def accept(self):
        problem=self.invalid_values()
        if problem:
            self.navigate=0;self.navigate_id=None
            QMessageBox.warning(self,"값 형식",problem);return
        super().accept()

    def apply(self):
        problem=self.invalid_values()
        if problem:QMessageBox.warning(self,"값 형식",problem);return
        super().apply()

    def _sibling_chosen(self,index):
        target=self.sibling_combo.itemData(index)
        if target and target!=self.record.get("id"):
            self.navigate_id=target;self.accept()

    def mark_applied(self):
        """적용(닫지 않음)이 끝난 뒤: 지금 값을 처음 값으로 삼아 확인을 눌러도 같은 명령을 다시 보내지 않는다."""
        super().mark_applied()
        if self.attribute_editor is not None:self.attribute_editor.mark_saved()
        self.initial_values=self._snapshot()

    def _snapshot(self):
        """이전·다음 이동 때 바뀐 값을 판단하는 비교 문자열. values()가 실패하면(예: id 없는 record) None이다."""
        try:return json.dumps(self.values(),sort_keys=True,ensure_ascii=False,default=str)
        except Exception:return None

    def changed(self):
        """열 때와 값이 다른지. 비교할 수 없으면 바뀐 것으로 본다."""
        current=self._snapshot()
        return self.initial_values is None or current is None or current!=self.initial_values

    def update_packet_tab(self,*_):
        if self.kind!="data_type":return
        self.tabs.setTabVisible(self.packet_index,self.form.fields["encoding"].currentData()=="packet")

    def update_type_preview(self,*_):
        if self.kind!="data_type":return
        fields=self.form.values()
        encoding=fields.get("encoding");conversion=fields.get("conversion")
        if conversion=="formula":
            self.phys_preview.setText("수식은 계산하지 않습니다. ODX에는 원시값 그대로(IDENTICAL) 나갑니다.");return
        if conversion in ("scale_linear","tab_intp"):
            self.phys_preview.setText("구간별 선형·특성 곡선은 구간(점)이 CDD에서 온 그대로입니다. 미리보기는 목록의 값 입력에서 확인하세요.");return
        if encoding not in ("unsigned","signed","float") or conversion=="text_table":
            self.phys_preview.setText("선형·동일값 변환만 미리보기합니다.");return
        raw_text=self.raw_preview.text().strip()
        if not raw_text:
            self.phys_preview.setText("—");return
        try:
            t={"encoding":encoding,"bit_length":int(fields.get("bit_length") or 0),"conversion":conversion,
               "factor_text":ratio_text(fields.get("factor_text")),"offset_text":decimal_text(fields.get("offset_text")),
               "raw_lower":int(fields["raw_lower"]) if fields.get("raw_lower") else None,
               "raw_upper":int(fields["raw_upper"]) if fields.get("raw_upper") else None}
            self.phys_preview.setText(convert(t,raw_text)[1])
        except (DomainError,ValueError) as e:self.phys_preview.setText(str(e))

    def add_exclusion(self,entry=None):
        entry=entry if isinstance(entry,dict) else {}
        i=self.excl.rowCount();self.excl.insertRow(i)
        self.excl.setItem(i,0,item(entry.get("lower_raw","")));self.excl.setItem(i,1,item(entry.get("upper_raw","")))
        kind=QComboBox()
        for k in ("undef","noSignal","invalidSignal","notImplemented","unexpectedResult"):kind.addItem(k,k)
        kind.setCurrentIndex(max(0,kind.findData(entry.get("kind","undef"))));self.excl.setCellWidget(i,2,kind)
        self.excl.setItem(i,3,item(entry.get("locale","") or ("ko-KR" if not entry else "")));self.excl.setItem(i,4,item(entry.get("label","")))

    def add_enum(self,entry=None):
        entry=entry if isinstance(entry,dict) else {}
        i=self.enum.rowCount();self.enum.insertRow(i)
        for col,k in enumerate(("lower_raw","upper_raw","locale","label")): self.enum.setItem(i,col,item(entry.get(k,"ko-KR" if k=="locale" else "")))

    def values(self):
        p=self.form.values()
        if self.kind=="dtc":
            try:p["code"]=f"0x{self.dtc_code():06X}"  # 보이는 형식과 상관없이 16진으로 보낸다(M2-5)
            except ValueError:pass  # 읽을 수 없으면 입력 그대로(확인 때 invalid_values가 막는다)
        if self.record: p["id"]=self.record["id"]
        p["variant_id"]=self.variant
        if self.members: p["members"]=self.members.values()
        if self.kind=="data_type":
            p["entries"]=[{k:self.enum.item(i,j).text() for j,k in enumerate(("lower_raw","upper_raw","locale","label"))} for i in range(self.enum.rowCount())]
            p["exclusions"]=[{"lower_raw":self.excl.item(i,0).text(),"upper_raw":self.excl.item(i,1).text(),"kind":self.excl.cellWidget(i,2).currentData(),"locale":self.excl.item(i,3).text(),"label":self.excl.item(i,4).text()} for i in range(self.excl.rowCount())]
            if self.record.get("structure_id"): p["structure_id"]=self.record["structure_id"]
        if self.kind=="service":
            p["request_members"]=self.request.values()
            p["positive_members"]=self.positive.values()
            p["nrcs"]=[f"0x{code:02X}" for i,code in enumerate(self.nrc_codes) if self.nrc_table.item(i,0).checkState()==Qt.CheckState.Checked]
        if self.attribute_editor is not None:
            changes=self.attribute_editor.changes()
            if changes:p["attributes"]=changes
        return p


class RecoveryDialog(BaseDialog):
    """비정상 종료로 남은 작업본 목록. 행을 두 번 클릭하거나 선택 후 적용하면 복구한다."""
    def __init__(self,rows,parent=None):
        super().__init__("작업 복구",parent)
        self.resize(920,520)
        self.rows=list(rows)
        self.result_row=None
        self.columns=[("updated_at","갱신 시각"),("saved_at","저장 시각"),("source","원본 경로"),("integrity","무결성"),("source_state","원본 상태")]
        self.layout.addWidget(QLabel("복구하면 작업본을 열고 원본 파일은 자동으로 덮어쓰지 않습니다. 원본이 변경된 작업본을 저장할 때는 다른 이름이 필요합니다."))
        self.table=QTableWidget(len(self.rows),len(self.columns))
        self.table.setHorizontalHeaderLabels([label for _,label in self.columns])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row_index,row in enumerate(self.rows):
            for col_index,(field,_) in enumerate(self.columns):
                value=row.get(field)
                cell=QTableWidgetItem("—" if value is None else str(value))
                cell.setData(Qt.ItemDataRole.UserRole,row_index)
                self.table.setItem(row_index,col_index,cell)
        self.table.cellDoubleClicked.connect(self.open_row)
        # 복구하지 않을 작업본(손상 등)은 목록에서 빼서 격리 폴더로 옮긴다. 파일은 지우지 않는다.
        self.quarantine=QPushButton("선택 작업본 격리(목록에서 제외)…")
        self.quarantine.clicked.connect(self.quarantine_selected)
        self.buttons.addButton(self.quarantine,QDialogButtonBox.ButtonRole.ActionRole)
        for index,width in enumerate((155,155,300,130,120)):
            if index<len(self.columns):self.table.setColumnWidth(index,width)
        self.layout.addWidget(self.table)
        self.finish()

    def quarantine_selected(self):
        row=self.table.currentRow()
        if row<0:return
        index=self.table.item(row,0).data(Qt.ItemDataRole.UserRole)
        self.result_row={**self.rows[index],"action":"quarantine"}
        super().accept()

    def open_row(self,row,_column):
        index=self.table.item(row,0).data(Qt.ItemDataRole.UserRole)
        self.result_row=self.rows[index];self.accept()

    def accept(self):
        if self.result_row is None:
            row=self.table.currentRow()
            if row<0:return
            index=self.table.item(row,0).data(Qt.ItemDataRole.UserRole)
            self.result_row=self.rows[index]
        super().accept()


class ConditionsDialog(BaseDialog):
    def __init__(self,record,catalog,parent=None):
        super().__init__("서비스 상태 조건·전이",parent)
        self.record=record
        self.layout.addWidget(QLabel("그룹 간 AND 조건입니다. 허용한 출발 상태에만 전이를 지정할 수 있습니다."))
        self.table=QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels(["그룹","상태","허용","실행 후 상태"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.rows=[]
        for group in catalog["groups"]:
            states=[s for s in catalog["states"] if s["group_id"]==group["id"]]
            for state in states:
                i=self.table.rowCount();self.table.insertRow(i)
                self.table.setItem(i,0,item(group["name"]));self.table.setItem(i,1,item(state["name"]))
                check=QCheckBox();check.setChecked(next((bool(c["allowed"]) for c in record.get("conditions",[]) if c["state_id"]==state["id"]),True));self.table.setCellWidget(i,2,check)
                target=QComboBox();target.addItem("변경 없음",None)
                for s in states: target.addItem(s["name"],s["id"])
                transition=next((t["to_state_id"] for t in record.get("transitions",[]) if t["from_state_id"]==state["id"]),None)
                target.setCurrentIndex(max(0,target.findData(transition)));self.table.setCellWidget(i,3,target)
                self.rows.append((group["id"],state["id"]))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.layout.addWidget(self.table);self.finish()

    def values(self):
        conditions=[];transitions=[]
        for i,(gid,sid) in enumerate(self.rows):
            conditions.append({"group_id":gid,"state_id":sid,"allowed":int(self.table.cellWidget(i,2).isChecked())})
            target=self.table.cellWidget(i,3).currentData()
            if target: transitions.append({"group_id":gid,"from_state_id":sid,"to_state_id":target})
        return {"id":self.record["id"],"conditions":conditions,"transitions":transitions}


class PreviewDialog(BaseDialog):
    def __init__(self,fields,parent=None):
        super().__init__("전문 미리보기 값",parent)
        self.fields=fields
        self.layout.addWidget(QLabel("선택한 서비스의 요청·정응답 바이트를 계산합니다. ECU로 전송하지 않습니다."))
        self.table=QTableWidget(len(fields),3)
        self.table.setHorizontalHeaderLabels(["데이터 객체","값","입력 단위"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for i,f in enumerate(fields):
            self.table.setItem(i,0,item(f["name"]));self.table.setItem(i,1,item(f["value"]))
            self.table.item(i,0).setFlags(self.table.item(i,0).flags() & ~Qt.ItemFlag.ItemIsEditable)
            combo=QComboBox();combo.addItem("Raw",False)
            if f["encoding"] in ("unsigned","signed"):combo.addItem("Physical",True)
            if f["constant"]:
                self.table.item(i,1).setFlags(self.table.item(i,1).flags() & ~Qt.ItemFlag.ItemIsEditable)
                combo.setEnabled(False)
            self.table.setCellWidget(i,2,combo)
        self.layout.addWidget(self.table);self.finish()

    def values(self): return {f["id"]:{"value":self.table.item(i,1).text(),"physical":self.table.cellWidget(i,2).currentData()} for i,f in enumerate(self.fields)}


class ReportDialog(QDialog):
    def __init__(self,title,text,parent=None):
        super().__init__(parent);self.setWindowTitle(title);self.resize(880,620)
        layout=QVBoxLayout(self);view=QPlainTextEdit();view.setReadOnly(True);view.setPlainText(text);layout.addWidget(view)
        bar=QHBoxLayout();save=QPushButton("보고서 저장…");save.clicked.connect(lambda:self.save(text));bar.addWidget(save)
        close=QPushButton("닫기");close.clicked.connect(self.accept);bar.addWidget(close);layout.addLayout(bar)

    def save(self,text):
        path,_=QFileDialog.getSaveFileName(self,"보고서 저장","report.txt","Text (*.txt);;JSON (*.json)")
        if path:
            try:
                from pathlib import Path
                Path(path).write_text(text,encoding="utf-8")
            except OSError as e: QMessageBox.warning(self,"저장 실패",str(e))


class CsvMappingDialog(BaseDialog):
    def __init__(self,data,parent=None):
        super().__init__("DTC CSV 열 매핑",parent)
        self.resize(780,560)
        self.form=Form(self.layout)
        choices=[("선택 안 함",None)]+[(h,h) for h in data["headers"]]
        for field,label in (("code","DTC 코드 *"),("name","이름 *"),("qualifier","Qualifier"),("description","설명")):
            self.form.combo(field,label,choices,field if field in data["headers"] else None)
        self.form.combo("base","숫자 표현",[("10진수 또는 0x 접두어","auto"),("16진수","hex")],"auto")
        preview=QTableWidget(min(20,len(data["rows"])),len(data["headers"]))
        preview.setHorizontalHeaderLabels(data["headers"])
        preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for i,row in enumerate(data["rows"][:20]):
            for j,text in enumerate(row[:len(data["headers"])]):preview.setItem(i,j,item(text))
        self.layout.addWidget(QLabel(f"총 {len(data['rows']):,}행 · 앞 20행 미리보기"))
        self.layout.addWidget(preview);self.finish()

    def values(self):
        d=self.form.values();base=d.pop("base");return {"mapping":d,"base":base}


class CsvApplyModel(QAbstractTableModel):
    """CSV 적용 미리보기 모델. 행마다 위젯 항목을 만들지 않아 10만 행도 화면에 보이는 행만 그린다."""
    HEADERS=["선택","행","코드","이름","오류"]

    def __init__(self,rows):
        super().__init__();self.rows=rows
        self.checked=[not r["error"] for r in rows]

    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.rows)
    def columnCount(self,parent=QModelIndex()):return 0 if parent.isValid() else 5

    def headerData(self,section,orientation,role=Qt.ItemDataRole.DisplayRole):
        if role==Qt.ItemDataRole.DisplayRole and orientation==Qt.Orientation.Horizontal:return self.HEADERS[section]
        return None

    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():return None
        r=self.rows[index.row()];col=index.column()
        if col==0 and role==Qt.ItemDataRole.CheckStateRole and not r["error"]:
            return Qt.CheckState.Checked if self.checked[index.row()] else Qt.CheckState.Unchecked
        if role==Qt.ItemDataRole.DisplayRole and col:
            return [str(r["line"]),f"0x{r['code']:06X}" if "code" in r else "",r.get("name",""),r["error"]][col-1]
        return None

    def flags(self,index):
        if index.column()==0:
            return Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable if not self.rows[index.row()]["error"] else Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable

    def setData(self,index,value,role=Qt.ItemDataRole.EditRole):
        if index.column()!=0 or role!=Qt.ItemDataRole.CheckStateRole or self.rows[index.row()]["error"]:return False
        self.checked[index.row()]=Qt.CheckState(value)==Qt.CheckState.Checked
        self.dataChanged.emit(index,index,[role]);return True

    def set_all(self,value):
        self.beginResetModel();self.checked=[value and not r["error"] for r in self.rows];self.endResetModel()


class CsvApplyDialog(BaseDialog):
    def __init__(self,rows,parent=None):
        super().__init__("DTC CSV 적용 미리보기",parent);self.resize(900,620);self.rows=rows
        self.layout.addWidget(QLabel(f"전체 {len(rows):,}행 / 오류 {sum(bool(r['error']) for r in rows):,}행 · 선택한 정상 행을 한 명령으로 등록합니다."))
        self.model=CsvApplyModel(rows)
        self.table=QTableView();self.table.setModel(self.model)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        bar=QHBoxLayout()
        for label,value in (("정상 행 모두 선택",True),("모두 해제",False)):
            b=QPushButton(label);b.clicked.connect(lambda _=False,v=value:self.model.set_all(v));bar.addWidget(b)
        bar.addStretch();self.layout.addLayout(bar)
        self.layout.addWidget(self.table);self.finish()

    def values(self):return [{k:v for k,v in row.items() if k not in ("line","error")} for i,row in enumerate(self.rows) if not row["error"] and self.model.checked[i]]


class ExportDialog(BaseDialog):
    def __init__(self,variants,current,xsd,parent=None):
        super().__init__("ODX 2.2.0 출력",parent)
        self.checks=[]
        self.layout.addWidget(QLabel("선택한 변형의 전체 데이터와 참조를 출력합니다. 공식 XSD가 필요합니다."))
        for v in variants:
            check=QCheckBox(v["name"]);check.setChecked(v["id"]==current);self.checks.append((v["id"],check));self.layout.addWidget(check)
        self.form=Form(self.layout)
        self.form.text("xsd_path","ODX 2.2.0 odx.xsd",xsd)
        choose=QPushButton("스키마 찾아보기…");choose.clicked.connect(self.choose_schema);self.layout.addWidget(choose)
        self.form.text("destination","출력 파일","")
        dest=QPushButton("출력 위치…");dest.clicked.connect(self.choose_destination);self.layout.addWidget(dest)
        self.layout.addStretch();self.finish()

    def choose_schema(self):
        path,_=QFileDialog.getOpenFileName(self,"ODX 2.2.0 XSD","","XML schema (*.xsd)")
        if path:self.form.fields["xsd_path"].setText(path)

    def choose_destination(self):
        path,_=QFileDialog.getSaveFileName(self,"ODX 출력","project.odx-d","ODX (*.odx-d)")
        if path:self.form.fields["destination"].setText(path)

    def values(self):return {**self.form.values(),"variants":[vid for vid,check in self.checks if check.isChecked()]}
