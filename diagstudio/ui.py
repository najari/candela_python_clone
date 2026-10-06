from pathlib import Path
import json
import uuid
from PySide6.QtCore import Qt,QAbstractTableModel,QModelIndex,QThread,Signal,QTimer,QSettings,QRectF,QPointF,QItemSelection,QItemSelectionModel,QEvent
from PySide6.QtGui import QAction,QKeySequence,QColor,QStandardItemModel,QStandardItem,QFont,QIcon,QPixmap,QPainter,QPen,QBrush,QPolygonF,QShortcut
from PySide6.QtWidgets import (QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QComboBox,QSplitter,QTreeView,QTableView,QHeaderView,QAbstractItemView,QToolBar,QDockWidget,QTabWidget,QPlainTextEdit,QProgressBar,QPushButton,QFileDialog,QMessageBox,QDialog,QInputDialog,QApplication,QMenu,QStackedWidget,QToolButton,QAbstractItemDelegate,QCheckBox)

from . import __version__
from .dialogs import ProjectDialog,ObjectDialog,ConditionsDialog,PreviewDialog,ReportDialog,CsvMappingDialog,CsvApplyDialog,ExportDialog,RecoveryDialog
from .workers import Worker
from .presentation import details_text,sae_dtc,qualifier_last
from .domain import key as text_key,make_qualifier
from .cell_edit import CellDelegate,cell_spec,editor_text,build_command,display_value,attribute_entry
from .find_dialog import FindDialog
from .overviews import OverviewDialog,StateGraphDialog,ReferenceImpactDialog
from .layout_view import LayoutView
from .pages import WorkLog,DetailTable,TablesDialog,ShortcutMatrixDialog,CompactTreeDialog,FaultMemoryDialog,ReworkDialog,BulkConditionDialog,group_font
from .panels import (PropertySheet,StartPage,SettingsDialog,QualifierOverviewDialog,ColumnInfoDialog,WheelGuard,option,add_recent_file,
                     remove_recent_file,recent_files,app_settings)
from .tools import ReplaceTypeDialog,VariantWizardDialog
from .compare_view import CompareDialog


STYLE="""
QMainWindow, QDialog { background: #f4f6fa; color: #1b283d; }
QWidget { font-family: 'Malgun Gothic'; font-size: 10pt; }
QMenuBar, QMenu, QToolBar { background: white; color: #22334b; }
QToolBar { border: none; border-bottom: 1px solid #dce3ed; spacing: 5px; padding: 7px; }
QToolButton { padding: 7px 10px; border: 1px solid transparent; border-radius: 4px; }
QToolButton:hover { background: #e6efff; border-color: #bfd2fa; }
QPushButton { background: white; border: 1px solid #cbd5e3; border-radius: 5px; padding: 7px 13px; }
QPushButton:hover { background: #eaf1ff; border-color: #7d9ed2; }
QPushButton:disabled { color: #98a2b3; background: #f0f2f5; }
QLineEdit, QComboBox, QPlainTextEdit { background: white; border: 1px solid #cbd5e3; border-radius: 4px; padding: 6px; selection-background-color: #c9dcff; selection-color: #132746; }
QTableView, QTreeView { background: white; alternate-background-color: #f8fafd; border: 1px solid #dce3ed; selection-background-color: #dbe8ff; selection-color: #163668; gridline-color: #edf0f5; }
QHeaderView::section { background: #edf2f9; color: #50617b; padding: 8px; border: none; border-right: 1px solid #e1e7f0; border-bottom: 1px solid #d4dfed; font-weight: 600; }
QTreeView::item { padding: 5px 1px; }
QDockWidget::title { background: #e9eff7; padding: 7px; }
QTabBar::tab { padding: 9px 16px; background: #e8edf5; border: 1px solid #d5deeb; }
QTabBar::tab:selected { background: white; color: #205db8; }
QStatusBar { background: #e9eef6; color: #405673; }
QSplitter::handle { background: #e1e7f0; }
QLabel#heading { font-size: 20pt; font-weight: 700; color: #142c4d; }
QLabel#subheading { color: #677a92; padding-bottom: 4px; }
QLabel#badge { background: #e6efff; color: #245ba2; border-radius: 4px; padding: 5px 10px; }
"""

KINDS={"did":"DID","dtc":"DTC","service":"서비스","data_type":"데이터 타입","state":"상태","variant":"변형","ecu":"ECU","unsupported":"보존 항목",
 "diagnostic_class":"진단 클래스","diagnostic_instance":"진단 인스턴스","dependency":"상태 의존성","did_overview":"DID 개요","dtc_overview":"DTC 개요",
 "nrc":"부정 응답 코드","protocol_service":"프로토콜 서비스","job_container":"Job 컨테이너",
 "interfaces":"지원 인터페이스","comm_params":"통신 파라미터","ident_features":"식별 특징","ident_patterns":"식별 패턴","supported_classes":"지원 진단 클래스",
 "job":"Job","state_group":"상태 그룹","state_defaults":"기본 상태 전이","unsupported_nrc":"미지원 서비스 NRC","template_service":"템플릿 서비스"}
# 목록에서 추가 명령이 만드는 객체 종류. 개요·배치 목록에서는 해당 객체를 만든다.
ADD_KIND={"diagnostic_class":"service","diagnostic_instance":"service","dependency":"service","did_overview":"did","dtc_overview":"dtc"}
EDITABLE={"did","dtc","data_type","service","variant","state","ecu"}
# 읽기 전용 템플릿 목록. 추가·편집·삭제를 막고 정확한 사유를 알린다.
READONLY_REASON={"nrc":"부정 응답 코드는 CDD 템플릿에서 가져온 읽기 전용 목록입니다.",
                 "protocol_service":"프로토콜 서비스는 CDD 템플릿에서 가져온 읽기 전용 목록입니다.",
                 "state_group":"상태 그룹은 읽기 전용 목록입니다. 상태는 그룹 노드의 상태 목록에서 추가·편집합니다."}
# 보존 원문에서 유도한 읽기 전용 목록(U5). 행은 객체가 아니어서 속성 창에 행 값만 보인다.
SOURCE_KINDS={"interfaces","comm_params","ident_features","ident_patterns","supported_classes","job","state_defaults","unsupported_nrc"}
for _kind in SOURCE_KINDS:READONLY_REASON[_kind]="원본 CDD의 보존 원문에서 만든 읽기 전용 목록입니다. 출력 의미에는 쓰지 않습니다."
# 체크 표시 열(읽기 전용). 사용자가 바꿀 수 없다.
CHECK_FIELDS={"supported_check","checked","use_check"}
YES_NO_FIELDS={"supported","is_base","semantic","singleton","fault_memory","used","required","single"}
# 장 노드 소개 문장(U6). Previous/Next는 넣지 않는다.
CHAPTER_TEXT={
 "ecu":"ECU 정보입니다. 행을 고르면 원본 파일·revision·개수 요약을 속성 창에 보입니다. 속성 편집(F2)으로 이름·제조사를 바꿉니다.",
 "ifaces":"ECU가 지원하는 통신 인터페이스입니다(원본 CDD, 읽기 전용). 지원하는 인터페이스 노드에서 통신 파라미터를 봅니다.",
 "fm":"고장 메모리 장입니다. DTC 개요는 DTC × 변형 포함 여부입니다. DTC 목록과 속성 열은 각 변형의 DTC 노드에서 봅니다.",
 "dtc_ov":"DTC × 변형 포함 여부입니다. 같은 DTC 객체를 쓰는 변형이 한 줄에 모입니다.",
 "did_ov":"DID × 변형 포함 여부, 사용 여부(인스턴스가 참조), DID 데이터(데이터 객체 이름)입니다.",
 "variants":"확장 변형 목록입니다. 식별 패턴은 원본 CDD의 읽기 전용 값입니다.",
 "states":"상태 그룹과 상태입니다. 서비스 실행 조건과 전이는 의존성에서, 새 서비스의 기본값은 기본 상태 전이에서 봅니다.",
 "sgroups":"상태 그룹 목록입니다. 그룹을 고르면 아래 세부 표에 상태가 보입니다(첫 상태 = 초기 상태).",
 "dep":"서비스 × 상태: ✓ 허용, ✗ 차단, → 전이, — 제한 없음. 보기를 '클래스별 묶음'으로 바꾸면 클래스·역할별로 묶어 보고, 여러 행을 골라 F2로 한 칸을 일괄 변경합니다. 모두 펼치기·접기와 주소 방식은 아래 단추로, 상태 열 접기는 열 머리 오른쪽 버튼으로 합니다.",
 "sdefaults":"클래스 템플릿 서비스의 기본 상태 조건·전이입니다(새 서비스에 적용되는 값, 원본 CDD 읽기 전용).",
 "types":"데이터 타입 목록입니다. 회색은 템플릿에서 온 타입입니다. 사용 중인 타입은 길이에 영향을 주는 속성을 바꿀 수 없습니다.",
 "psvc":"템플릿의 프로토콜 서비스(읽기 전용)입니다. LL = 인스턴스 고정값, zz·yy = 데이터 자리, rc = 응답 코드.",
 "nrcs":"템플릿의 부정 응답 코드(읽기 전용)입니다. 미지원 서비스에 쓰는 NRC는 하위 노드에 있습니다.",
 "nrcs_unsupported":"지원하지 않는 서비스 요청에 대한 부정 응답 코드입니다(원본 CDD 읽기 전용).",
 "unsupported":"가져오지 못한 CDD 원문입니다. 출력 차단 항목이 있으면 ODX 출력이 막힙니다. 두 번 누르면 원문 XML을 봅니다."}
DTC_FORMATS=(("0x 16진","hex"),("SAE (C002011)","sae"))
# 목록 셀의 표시값. dialogs.py의 편집 콤보와 같은 용어를 쓴다.
ENCODING_TEXT={"unsigned":"unsigned","signed":"signed","ascii":"ascii","bytes":"bytes","bcd":"bcd","packet":"packet"}
BYTE_ORDER_TEXT={"big":"Big endian","little":"Little endian"}
CONVERSION_TEXT={"identity":"동일값","linear":"선형","text_table":"열거 테이블","formula":"수식(CDD)","scale_linear":"구간별 선형","tab_intp":"특성 곡선"}
# [TREE-R400] 인스턴스 아이콘 5종. 원본 변형(CDD DIAGINST 위치)과 참조 변형으로 구분한다.
SHARE_MARK={"base":"●","base_shared":"◉","local":"○","ext_ref_base":"◈","ext_shared":"◎"}
SHARE_TIP={"base":"기본 변형에만 있음","base_shared":"기본 변형 원본, 확장 변형에서 참조","local":"이 확장 변형에만 있음",
 "ext_ref_base":"확장 변형 원본, 기본 변형(및 다른 변형)에서 참조","ext_shared":"확장 변형 원본, 다른 확장 변형에서만 참조"}
# 검증 결과 표의 열. 검증을 실행하지 않아도 이 머리를 보인다(DID 열이 나오던 결함 수정).
ISSUE_COLUMNS=[("severity_text","심각도"),("rule_id","규칙"),("path","객체 경로"),("message","내용"),("remedy","수정 안내"),("blocks_text","출력 차단"),("revision_text","revision")]
_SHARE_ICONS={}
def share_icon(share):
    """공유 상태 5종 아이콘. 채운 원·반원·빈 원과 채운 마름모·반 마름모로 구분한다(텍스트 기호 대체)."""
    icon=_SHARE_ICONS.get(share)
    if icon is None:
        size=13;pix=QPixmap(size,size);pix.fill(Qt.GlobalColor.transparent)
        painter=QPainter(pix);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color={"base":QColor("#205db8"),"base_shared":QColor("#205db8"),"local":QColor("#7a8699"),
               "ext_ref_base":QColor("#2f7d5b"),"ext_shared":QColor("#2f7d5b")}.get(share,QColor("#7a8699"))
        painter.setPen(QPen(color,1.3))
        if share in ("base","base_shared","local"):
            if share=="base":painter.setBrush(QBrush(color))
            elif share=="base_shared":painter.setBrush(QBrush(color,Qt.BrushStyle.Dense4Pattern))
            else:painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(1.5,1.5,size-3,size-3))
        else:
            painter.setBrush(QBrush(color) if share=="ext_ref_base" else QBrush(color,Qt.BrushStyle.Dense4Pattern))
            painter.drawPolygon(QPolygonF([QPointF(size/2,1.0),QPointF(size-1.0,size/2),QPointF(size/2,size-1.0),QPointF(1.0,size/2)]))
        painter.end();icon=QIcon(pix);_SHARE_ICONS[share]=icon
    return icon
COLUMNS={
 "did":[("identifier","DID"),("name","이름"),("diagnostic_class","진단 클래스"),("fault_memory","고장 메모리 사용"),("did_data","DID 데이터"),("qualifier","Qualifier"),("shared","공유 변형"),("supported","지원")],
 "dtc":[("code","DTC"),("name","이름"),("qualifier","Qualifier"),("shared","공유 변형")],
 "service":[("sid","SID"),("name","이름"),("qualifier","Qualifier"),("subfunction","Subfunction"),("shared","공유 변형")],
 "data_type":[("name","이름"),("qualifier","Qualifier"),("encoding","인코딩"),("bit_length","비트"),("byte_order","순서"),("conversion","변환"),("unit","단위"),("usage","사용")],
 "state":[("code","코드"),("name","이름"),("qualifier","Qualifier")],
 "variant":[("name","이름"),("qualifier","Qualifier"),("is_base","기본 변형")],
 "ecu":[("name","이름"),("qualifier","Qualifier"),("manufacturer","제조사")],
 "unsupported":[("name","사유"),("category","구분"),("qualifier","원본 경로"),("semantic","출력 차단")],
 "diagnostic_class":[("name","이름"),("qualifier","Qualifier"),("instances","인스턴스"),("singleton","싱글턴")],
 "diagnostic_instance":[("identifier_text","식별자"),("name","이름"),("qualifier","Qualifier"),("services","서비스"),("shared","공유 변형")],
 "dependency":[("identifier_text","ID"),("name","서비스"),("instance","인스턴스")],
 "did_overview":[("identifier","DID"),("name","이름"),("qualifier","Qualifier")],
 "dtc_overview":[("code","DTC"),("name","이름"),("qualifier","Qualifier")],
 "nrc":[("code","NRC"),("name","이름"),("qualifier","Qualifier"),("usage","사용 서비스")],
 "protocol_service":[("name","이름"),("qualifier","Qualifier"),("request_text","요청"),("positive_text","긍정 응답"),("negative_text","부정 응답"),("template_count","템플릿"),("service_count","서비스")],
 "state_group":[("name","이름"),("group_kind","Semantic"),("states_text","상태")],
}
for _kind in SOURCE_KINDS:COLUMNS.setdefault(_kind,[("name","이름")])

# 서비스 행의 요청·응답 표기 칸(M5-3): 누르면 그 전문 표(요청·긍정 응답) 또는 NRC 목록(rc)을 연다.
LINK_FIELDS={"request_pattern":"request","positive_pattern":"positive","negative_pattern":"nrc"}
LINK_TEXT={"request":"요청 전문 표","positive":"긍정 응답 전문 표","nrc":"NRC 목록(열기·닫기)"}
# 출력 창(M4-1) 출처와 명령 이름
ORIGINS={"execute_command":"편집","save_project":"저장","open_project":"열기","new_project":"열기","close_project":"열기","cdd_preview":"가져오기",
         "apply_cdd":"가져오기","csv_read":"가져오기","csv_map":"가져오기","export_odx":"출력","export_csv":"출력","validate_project":"검증",
         "find_document":"찾기","resolve_name_path":"경로","resolve_path":"경로","locate_object":"경로","recoveries":"복구","quarantine_recovery":"복구"}
COMMAND_TEXT={"create_did":"DID 추가","update_did":"DID 수정","create_dtc":"DTC 추가","update_dtc":"DTC 수정","create_type":"데이터 타입 추가",
              "update_type":"데이터 타입 수정","copy_type":"데이터 타입 독립 복사","create_service":"서비스 추가","update_service":"서비스 수정",
              "create_variant":"변형 추가","update_common":"속성 수정","create_state":"상태 추가","update_state":"상태 수정","set_conditions":"상태 조건 수정",
              "import_dtcs":"DTC 가져오기","bulk_edit":"일괄 편집","set_translation":"번역 수정","set_attributes":"사용자 속성 설정",
              "set_qualifiers":"Qualifier 바꾸기","set_dtc_attributes":"DTC 속성 수정","update_member":"멤버 수정","detach_did":"DID 분리",
              "replace_data_type":"데이터 타입 바꾸기",
              "detach_instance":"인스턴스 사본","bulk_conditions":"상태 조건 일괄 변경","set_rework":"Rework 목록 저장","delete":"삭제",
              "undo":"실행 취소","redo":"다시 실행"}
# 상태 줄 설명(M4-2, Status Bar): 메뉴·도구 단추에 마우스를 올리면 상태 줄에 한 줄 설명이 나온다(QAction.setStatusTip).
STATUS_TIPS={
 "new":"새 ECU 프로젝트를 만듭니다.","open":"진단 프로젝트(.diagproj)를 열거나 CDD·CDDT 파일을 가져오기 미리보기로 엽니다.",
 "save":"프로젝트를 저장합니다.","save_as":"프로젝트를 다른 이름으로 저장합니다.","recover":"비정상 종료로 남은 작업본을 복구합니다.",
 "close":"현재 프로젝트를 닫습니다.","quit":"DiagAuthor를 종료합니다.","undo":"마지막 명령을 되돌립니다.","redo":"되돌린 명령을 다시 실행합니다.",
 "add":"현재 목록에 새 객체를 추가합니다.","edit":"선택 객체의 속성 대화상자를 엽니다 (Alt+Enter · 두 번 클릭).",
 "edit_cell":"목록의 현재 칸을 바로 고칩니다 (F2). 고칠 수 없는 칸이면 이유를 알립니다.",
 "find_next":"같은 조건으로 다음을 찾습니다 (F3).","focus_next":"트리 → 목록 → 선택 객체 창 순서로 옮깁니다 (F6).",
 "focus_prev":"선택 객체 창 → 목록 → 트리 순서로 옮깁니다 (Shift+F6).",
 "delete":"선택 객체를 삭제합니다. 먼저 삭제 영향을 보입니다.","detach":"현재 변형의 DID를 사본으로 바꿔 다른 변형과 분리합니다.",
 "detach_instance":"여러 변형이 쓰는 인스턴스를 현재 변형에서 사본으로 바꿉니다.",
 "bulk_conditions":"의존성 목록에서 고른 서비스들의 한 상태 칸을 한 번에 바꿉니다.",
 "copy_type":"선택 데이터 타입과 하위 타입·구조를 독립 사본으로 만듭니다.",
 "replace_type":"선택 데이터 타입의 사용처를 골라 다른 데이터 타입으로 한 번에 연결합니다.",
 "references":"선택 객체를 참조하는 곳과 간접 영향을 봅니다.","bulk":"선택 행들의 이름·설명을 한 번에 바꿉니다.",
 "copy":"선택 행을 표 형식으로 클립보드에 복사합니다.","paste":"클립보드의 DTC 표를 열 매핑으로 가져옵니다.",
 "search":"목록 필터 칸으로 갑니다 (Ctrl+F).","find":"찾기 창을 엽니다: 문서 전체에서 이름·Qualifier·식별자·요청 바이트를 찾습니다 (Ctrl+Shift+F).",
 "back":"이전 위치로 돌아갑니다.","forward":"다음 위치로 갑니다.","path":"선택 객체(또는 노드)의 이름 경로를 복사합니다.",
 "stable_path":"선택 객체의 안정 경로(diag://…)를 복사합니다.","goto":"경로 입력 칸으로 갑니다 (이름 경로 · qpath: · diag://).",
 "goto_did":"선택 객체와 관련된 DID로 이동합니다.","goto_type":"선택 객체와 관련된 데이터 타입으로 이동합니다.",
 "qualifiers":"Qualifier 개요에서 Qualifier를 보고 고치거나 이름에서 다시 만듭니다.","shortcuts":"Shortcut Qualifier × 변형 표를 봅니다.",
 "attributes":"원본·사용자 속성을 한 표로 봅니다.","compact":"기본 변형과 공유하지 않는 인스턴스만 봅니다.",
 "compare":"두 변형, 또는 작업본과 저장된 파일의 차이를 트리로 봅니다.","compare_file":"현재 작업본과 저장된 프로젝트 파일의 차이를 봅니다.",
 "graph":"상태와 서비스 실행 뒤의 상태 전이를 그림으로 봅니다.","translations":"저장된 번역을 한 표로 봅니다.",
 "translate":"선택 객체의 이름·설명 번역을 추가하거나 고칩니다.","fault_memory":"고장 메모리 인스턴스의 DTC 표·스냅샷·확장 데이터 레코드를 봅니다.",
 "revisions":"원본 CDD의 개정 이력을 봅니다.","rework":"Rework 목록을 보고 고칩니다.","validate":"프로젝트 전체를 검증합니다 (F9 · F5).",
 "validate_export":"현재 변형의 ODX 출력 가능 여부를 미리 검사합니다.","preview":"선택 서비스의 요청·응답 전문을 값과 함께 미리 봅니다.",
 "conditions":"선택 서비스의 상태 조건·전이를 고칩니다.","new_variant":"변형 마법사로 새 변형을 만듭니다 (참조로 · 사본으로 · 빈 변형).",
 "ecu":"ECU 이름·Qualifier·제조사를 고칩니다.","cdd":"CDD·CDDT 파일을 가져와 새 프로젝트를 만듭니다.",
 "csv_import":"CSV·TSV 파일에서 DTC를 가져옵니다.","csv_export":"현재 변형의 DTC를 CSV로 내보냅니다.","odx":"ODX 2.2.0 파일로 내보냅니다.",
 "about":"프로그램 정보를 봅니다.","settings":"이 PC의 사용자 설정을 고칩니다.",
 "list_max":"트리·선택 객체 창·결과 창을 숨기고 목록을 크게 봅니다 (Esc로 되돌림).",
 "column_info":"목록 열의 이름·Qualifier·종류·설명을 봅니다.","reset_view":"분할 위치·결과 창 높이·열 너비·출력 창 필터를 처음 값으로 되돌립니다."}
# 작업 기록에 남기지 않는 조회(선택·목록·경로 조회 등)
QUIET_METHODS={"query_objects","details","inspect","catalog","overview","recoveries","set_locale","locate_object","fault_memory_view",
               "shortcut_matrix","compact_tree","revision_history","rework_items","node_name_path","object_name_path","selection_details",
               "property_sheet","object_qualifier_path","resolve_name_path","resolve_path","qualifier_overview","related_targets","node_path","object_path",
               "reference_impact","deletion_impact","preview_fields","attribute_overview","translation_overview","state_graph","compare_variants",
               "compact_view","compare_file_variants","find_document","csv_read","csv_map","cdd_preview",
               "replace_type_preview","variant_wizard_items","compare_view","add_defaults","suggest_number"}
# 열 머리 도움말(M1-1·M1-4). DTC 레코드 항목 열은 열 정보(column_info)를 쓴다.
COLUMN_HELP={"name":"이름(현재 언어). F2로 목록에서 바로 고칩니다.","qualifier":"Qualifier: 영문·숫자·_로 된 식별 이름(최대 128자). 새 객체는 이름에서 만듭니다.",
 "code":"코드(식별 번호). DTC는 24비트, F2로 고칩니다(0x 16진·10진, DTC는 SAE 문자형도 받음).","identifier":"DID 번호(16비트). F2로 고칩니다."}
_DISABLED_ICON=[]
def disabled_icon():
    """[SVUI-R400-Display] 비활성 서비스 = 체크 + 빨간 ✕(미사용 템플릿 서비스의 빈 체크와 구분)."""
    if not _DISABLED_ICON:
        size=14;pix=QPixmap(size,size);pix.fill(Qt.GlobalColor.transparent)
        painter=QPainter(pix);painter.setRenderHint(QPainter.RenderHint.Antialiasing);painter.setPen(QPen(QColor("#c62828"),2.2))
        painter.drawLine(QPointF(3.0,3.0),QPointF(size-3.0,size-3.0));painter.drawLine(QPointF(size-3.0,3.0),QPointF(3.0,size-3.0));painter.end()
        _DISABLED_ICON.append(QIcon(pix))
    return _DISABLED_ICON[0]

def service_tip(row,field=None):
    """[SVUI-R500] 서비스 행 툴팁: 주소 방식(phys·func)별 요청·응답 줄과 SPRMIB 안내. 표시 전용."""
    lines=[row.get("name","")]
    modes=[label for key,label in (("physical","phys"),("functional","func")) if row.get(key)] or ["phys"]
    req,pos,neg=row.get("request_pattern"),row.get("positive_pattern"),row.get("negative_pattern")
    for mode in modes:
        if req:lines.append(f"{mode} 요청: {req}")
        if pos:lines.append(f"{mode} 긍정 응답: {pos}"+(" (SPRMIB=1이면 보내지 않음)" if row.get("suppress_positive") else ""))
        if neg:lines.append(f"{mode} 부정 응답: {neg}")
    if row.get("suppress_positive"):
        lines.append("SPRMIB: 요청 subfunction의 최상위 비트(0x80)를 켜면 ECU가 긍정 응답을 보내지 않습니다(suppressPosRspMsgIndicationBit).")
    if field in LINK_FIELDS and row.get(field):lines.append("누르면 "+LINK_TEXT[LINK_FIELDS[field]]+"를 엽니다.")
    lines.append(f"ID: {row['id']}")
    return "\n".join(lines)

_CONVERSION_ICONS={}
def conversion_icon(conversion):
    """데이터 타입 변환 종류 아이콘(2.1): = 동일값, / 선형, ≡ 열거 테이블."""
    icon=_CONVERSION_ICONS.get(conversion)
    if icon is None:
        size=14;pix=QPixmap(size,size);pix.fill(Qt.GlobalColor.transparent)
        painter=QPainter(pix);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color={"identity":QColor("#7a8699"),"linear":QColor("#205db8"),"text_table":QColor("#2f7d5b"),"formula":QColor("#8a5a00"),"scale_linear":QColor("#205db8"),"tab_intp":QColor("#6a3fa0")}.get(conversion,QColor("#7a8699"))
        painter.setPen(QPen(color,1.2));painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawRoundedRect(QRectF(0.5,0.5,size-1,size-1),3,3)
        font=QFont();font.setPixelSize(10);font.setBold(True);painter.setFont(font)
        painter.drawText(QRectF(0,0,size,size),int(Qt.AlignmentFlag.AlignCenter),{"identity":"=","linear":"/","text_table":"≡","formula":"ƒ","scale_linear":"⟋","tab_intp":"∿"}.get(conversion,"?"))
        painter.end();icon=QIcon(pix);_CONVERSION_ICONS[conversion]=icon
    return icon


# CDD 계열(.cdd ECU 문서, .cddt 문서 템플릿)은 프로젝트로 직접 열지 않고 가져오기 미리보기로 보낸다.
# .cddt 독립 템플릿 문서는 미리보기 단계에서 UNSUPPORTED_VERSION으로 거부된다(해석·보존하지 않음).
CDD_SUFFIXES=(".cdd",".cddt")
def is_cdd_path(path):
    return str(path).lower().endswith(CDD_SUFFIXES)
OPEN_FILTER="프로젝트 또는 CANdela 문서 (*.diagproj *.cdd *.cddt);;진단 프로젝트 (*.diagproj);;CANdela document (*.cdd);;CANdela template (*.cddt)"
CDD_IMPORT_FILTER="CANdela document·template (*.cdd *.cddt);;CANdela document (*.cdd);;CANdela template (*.cddt)"


class ObjectModel(QAbstractTableModel):
    sort_requested=Signal(str,bool)
    # 칸 편집 확정(M1-1): (행 번호, 필드, 값). 명령은 주 창이 만든다.
    cell_edited=Signal(int,str,object)
    def __init__(self,parent=None):
        super().__init__(parent);self.rows=[];self.columns=qualifier_last(COLUMNS["did"])
        # DTC 코드 표시 형식(2.1): hex = 0x402011, sae = C002011(표시 전용, 원시값은 툴팁)
        self.dtc_format="hex"
        # 표 끝 새 항목 행(U6). None이면 없다. 행은 rows에 넣지 않는다(선택·정렬·삭제 대상이 아님).
        self.new_row_label=None
        # 열 정보(M1-7): 필드 → Qualifier·종류·필수·설명(DTC 레코드 항목 열). 헤더 툴팁과 '열 정보' 창에 쓴다.
        self.column_info={}
        # 정렬 표시([LIST-R250]): 필드 → (순서, 내림차순). 여러 열 정렬이면 순서 번호를 함께 보인다.
        self.sort_marks={}
        # 칸 편집 가능 여부(주 창의 spec_at). None이면 편집하지 않는다(검증 결과 표 등).
        self.spec_for=None

    def replace(self,kind,rows,columns=None):
        # Qualifier 열은 어느 목록이든 맨 뒤에 둔다(qualifier_last).
        self.beginResetModel();self.columns=qualifier_last(columns if columns else COLUMNS.get(kind,[("name","이름")]));self.rows=rows;self.endResetModel()

    def set_new_row(self,label):
        if label==self.new_row_label:return
        self.beginResetModel();self.new_row_label=label;self.endResetModel()

    def is_new_row(self,row):return self.new_row_label is not None and row==len(self.rows)
    def rowCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.rows)+(1 if self.new_row_label else 0)
    def columnCount(self,parent=QModelIndex()):return 0 if parent.isValid() else len(self.columns)
    def headerData(self,section,orientation,role=Qt.ItemDataRole.DisplayRole):
        if role==Qt.ItemDataRole.DisplayRole:
            if orientation==Qt.Orientation.Horizontal:
                if not 0<=section<len(self.columns):return None
                field,label=self.columns[section]
                mark=self.sort_marks.get(field)
                if mark:label+=(" ▼" if mark[1] else " ▲")+(str(mark[0]) if len(self.sort_marks)>1 else "")
                return label
            return "＋" if section>=len(self.rows) else section+1
        if role==Qt.ItemDataRole.ToolTipRole and orientation==Qt.Orientation.Horizontal and 0<=section<len(self.columns):
            return self.column_tip(section)

    def column_tip(self,section):
        """헤더 툴팁(M1-7): 열 이름, Qualifier, 종류·필수, 설명, 값 목록 일부."""
        field,label=self.columns[section]
        info=self.column_info.get(field)
        if not info:return f"{label}\n"+(COLUMN_HELP.get(field)+"\n" if COLUMN_HELP.get(field) else "")+f"필드: {field}\n머리를 누르면 정렬, Ctrl+누르면 여러 열 정렬 · 오른쪽 버튼: 열 정보…"
        lines=[label,f"Qualifier: {info.get('qualifier') or field}",f"종류: {info.get('kind','')} · {'필수' if info.get('required') else '선택'}"]
        if info.get("description"):lines.append("설명: "+str(info["description"])[:300])
        choices=info.get("choices") or []
        if choices:lines.append("값: "+", ".join(choices[:8])+(" …" if len(choices)>8 else ""))
        lines.append("오른쪽 버튼: 열 정보…")
        return "\n".join(lines)

    def flags(self,index):
        base=super().flags(index)
        if index.isValid() and self.spec_for is not None and index.row()<len(self.rows) and self.spec_for(index):
            return base|Qt.ItemFlag.ItemIsEditable
        return base

    def setData(self,index,value,role=Qt.ItemDataRole.EditRole):
        if role!=Qt.ItemDataRole.EditRole or not index.isValid() or index.row()>=len(self.rows):return False
        self.cell_edited.emit(index.row(),self.columns[index.column()][0],value)
        return True

    def refresh_rows(self):
        """낙관적 갱신 뒤 보이는 칸을 다시 그린다."""
        if not self.rows or not self.columns:return
        self.dataChanged.emit(self.index(0,0),self.index(len(self.rows)-1,len(self.columns)-1))

    def code_text(self,row,value):
        # DTC는 24비트(0x402011 또는 SAE C002011), NRC·상태 코드는 8비트
        if row.get("kind")=="dtc":return sae_dtc(value) if self.dtc_format=="sae" else f"0x{value:06X}"
        return f"0x{value:02X}"

    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():return None
        if index.row()>=len(self.rows):
            # 새 항목 행: 첫 열에 안내 문구, 누르면 추가 대화상자를 연다.
            if index.column()!=0:return None
            if role==Qt.ItemDataRole.DisplayRole:return self.new_row_label
            if role==Qt.ItemDataRole.ForegroundRole:return QColor("#205db8")
            if role==Qt.ItemDataRole.ToolTipRole:return "누르면 새 항목 추가 대화상자를 엽니다."
            if role==Qt.ItemDataRole.FontRole:
                f=QFont();f.setItalic(True);return f
            return None
        row=self.rows[index.row()];field=self.columns[index.column()][0];value=row.get(field,"")
        kind=row.get("kind")
        if role==Qt.ItemDataRole.EditRole:role=Qt.ItemDataRole.DisplayRole
        if role==Qt.ItemDataRole.DisplayRole:
            if field in CHECK_FIELDS:
                if field=="use_check":return row.get("use","")
                if field=="supported_check":return "지원" if value else "미지원"
                return ""
            if value is None:return "—"
            if field=="code" and isinstance(value,int):return self.code_text(row,value)
            if field in ("identifier","sid","subfunction") and isinstance(value,int):
                return f"0x{value:0{4 if field=='identifier' else 2}X}"
            if field in YES_NO_FIELDS:return "예" if value else "아니요"
            if field=="shared":return f"{value}개" if value>1 else "독립" if value==1 else "—"
            if field=="usage":return f"{value or 0}개"
            if field=="encoding":return ENCODING_TEXT.get(value,value)
            if field=="byte_order":return BYTE_ORDER_TEXT.get(value,value)
            if field=="conversion":return CONVERSION_TEXT.get(value,value)
            return str(value)
        if role==Qt.ItemDataRole.CheckStateRole and field in CHECK_FIELDS:
            # [SVUI-R400-Display] 켜짐 = 체크, 비활성 = 체크 + 빨간 ✕, 없음(미사용 템플릿) = 빈 체크
            if field=="use_check" and row.get("use")=="비활성":return Qt.CheckState.Checked
            return Qt.CheckState.Checked if value else Qt.CheckState.Unchecked
        if role==Qt.ItemDataRole.UserRole:return row["id"]
        if role==Qt.ItemDataRole.DecorationRole and kind=="data_type" and field=="name":return conversion_icon(row.get("conversion"))
        if role==Qt.ItemDataRole.DecorationRole and field=="use_check" and row.get("use")=="비활성":return disabled_icon()
        if role==Qt.ItemDataRole.FontRole and kind=="dependency_group":return group_font(QFont())
        if role==Qt.ItemDataRole.FontRole and kind=="service" and field in LINK_FIELDS and value:
            f=QFont();f.setUnderline(True);return f
        if role==Qt.ItemDataRole.BackgroundRole and kind=="dependency_group":return QColor("#eef3fb")
        if role==Qt.ItemDataRole.ForegroundRole:
            if row.get("severity")=="error":return QColor("#b3261e")
            if row.get("severity")=="warning":return QColor("#a06b19")
            # 쓰지 않는 템플릿 서비스·비활성 서비스·템플릿 소유 타입은 회색(읽기 전용 표시)
            if kind=="template_service" or row.get("use")=="비활성" or (kind=="data_type" and row.get("template")):return QColor("#8a94a6")
            if kind=="service" and field in LINK_FIELDS and value:return QColor("#205db8")
            if not row.get("supported",1) or row.get("warnings"):return QColor("#a06b19")
        if role==Qt.ItemDataRole.ToolTipRole:
            if field=="code" and isinstance(value,int) and kind=="dtc":return f"{row.get('name','')}\n원시값 0x{value:06X} · SAE {sae_dtc(value)}"
            if kind=="template_service" and row.get("use")=="보존 원문":return f"'{row.get('name','')}' — CDD에는 있지만 가져오지 못해 원문으로 보존한 서비스입니다(읽기 전용, 출력 차단). 보존 항목에서 원문을 봅니다."
            if kind=="template_service":return f"템플릿 서비스 '{row.get('name','')}' — 이 인스턴스에서 쓰지 않습니다(읽기 전용). 켜는 편집은 템플릿 의미 매핑이 확인될 때까지 막혀 있습니다."
            if kind=="data_type" and row.get("template"):return f"{row.get('name','')}\n템플릿에서 온 타입"
            if kind=="service":return service_tip(row,field)
            return f"{row.get('name','')}\nID: {row['id']}"

    def sort(self,column,order=Qt.SortOrder.AscendingOrder):
        if 0<=column<len(self.columns):self.sort_requested.emit(self.columns[column][0],order==Qt.SortOrder.DescendingOrder)


class InstanceNameEdit(QLineEdit):
    """인스턴스 페이지 머리의 이름 칸(M5-4). Enter·포커스 이동 = committed(새 이름), Esc = 처음 값으로 되돌림."""
    committed=Signal(str)
    def __init__(self,parent=None):
        super().__init__(parent);self._original=""
        self.editingFinished.connect(self._finish)
    def set_original(self,text):
        self._original=str(text or "");self.setText(self._original)
    def original(self):return self._original
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:
            self.setText(self._original);self.clearFocus();event.accept();return
        super().keyPressEvent(event)
    def _finish(self):
        text=self.text().strip()
        if self.isReadOnly() or text==self._original.strip():return
        self._original=text  # 한 번만 보낸다(Enter 뒤 포커스가 떠나도 다시 보내지 않음). 실패하면 주 창이 되돌린다.
        self.committed.emit(text)


class MainWindow(QMainWindow):
    request=Signal(str,str,object)
    def __init__(self,initial_path=None):
        super().__init__()
        self.resize(1440,930);self.setMinimumSize(1120,720)
        self.setStyleSheet(STYLE)
        self.settings=app_settings()  # DIAGAUTHOR_SETTINGS가 있으면 그 ini(테스트 격리)
        self.state={"open":False};self.variant=None;self.kind="did";self.scope=None;self.node_key=None;self.page=0;self.sorts=None
        # M1-4: 목록 종류별 정렬([LIST-R240])·열 너비([LIST-R720])를 세션 동안 기억한다. 작업 스레드가 돌려준 실제 정렬은 _effective_sort.
        self._sort_memory={};self._effective_sort=[];self._widths={};self._sizing=False;self._unfiltered=0
        # M1-1: 칸 편집 명령 줄(작업 중이면 끝난 뒤 보낸다)과 결과 반영 보류(편집 중·명령 대기 중에는 목록을 다시 싣지 않는다)
        self._command_queue=[];self._edit_pending=0;self._deferred_page=None;self._cell_retry=None;self._cell_retry_text=None;self._record_items={}
        # M3-1: 찾기 창·조건·결과(최근 검색어는 사용자 설정에 남긴다)
        self.find_dialog=None;self._find={};self._find_criteria=None
        self.name_paths={};self._applied_node=None;self.history=[];self.history_pos=-1;self._history_guard=False
        self.pending=False;self._pending_method=None;self.callbacks={};self.details_record=None;self.closing=False;self.validation_revision=None;self._validation_issues=[];self.filter_stash=None;self.initial_path=initial_path
        self._focus_oid="";self._focus_field="";self._focus_remedy="";self._focus_shown=False;self._inspect_field=""
        # 목록 갱신이 끝난 뒤 한 번 실행할 일(편집 대화상자 앞뒤 이동, 인스턴스 사본 뒤 편집). 오류가 나면 버린다.
        self._after_refresh=None;self._node_fallback=False;self._dep_expanded=set();self._last_columns=None;self._navigation_selection=None
        # 24차(P2): 여러 행 선택 유지, 서비스 행 자리표시 링크, 적용 중인 대화상자, 최근 파일, 경로 기록, 목록 크게 보기, 이전 형식 알림
        self._keep_selection=None;self._pending_telegram=None;self._busy_dialog=None;self._recent_path=None;self._import_path=None
        self._path_history=[];self._list_max=None;self._migration_seen=None
        self._tree_identifiers=option(self.settings,"tree_identifiers")  # 결정 D4: 목록은 페이지를 나누지 않는다
        self.wheel_guard=WheelGuard(self)
        self.thread=QThread(self);self.worker=Worker();self.worker.moveToThread(self.thread)
        self.request.connect(self.worker.run);self.worker.finished.connect(self.finished)
        self.thread.finished.connect(self.worker.deleteLater);self.thread.start()
        self.actions={};self.mutations=[];self.project_actions=[]
        self.create_actions();self.create_ui();self.update_enabled()
        app=QApplication.instance()
        if app is not None:self.wheel_guard.install(app,self)
        self.setAcceptDrops(True)
        self.apply_settings();self.show_start(True)
        self.setWindowTitle(f"DiagAuthor {__version__} · 진단 데이터 작성")
        geometry=self.settings.value("geometry")
        if geometry:self.restoreGeometry(geometry)
        QTimer.singleShot(0,self.initial_layout)
        QTimer.singleShot(0,self.startup)

    def initial_layout(self):
        """창이 보인 뒤 한 번: 결과 창을 최소 높이로 두고 목록·선택 객체 창을 나눈다. 사용자가 옮긴 분할 위치는 설정에서 되살린다."""
        self.resizeDocks([self.results_dock],[self.results_dock.minimumHeight()],Qt.Orientation.Vertical)
        saved=self.settings.value("work_splitter")
        if not (saved and self.work_splitter.restoreState(saved)):
            h=max(self.work_splitter.height(),400);self.work_splitter.setSizes([h*11//20,h-h*11//20])
        saved=self.settings.value("main_splitter")
        if saved:self.splitter.restoreState(saved)

    def action(self,key,label,fn,shortcut=None,mutation=False,project=False):
        a=QAction(label,self);a.triggered.connect(fn)
        a.setStatusTip(STATUS_TIPS.get(key) or label.rstrip("…"))  # M4-2 상태 줄 설명
        if shortcut:a.setShortcut(QKeySequence(shortcut))
        self.actions[key]=a
        if mutation:self.mutations.append(a)
        if project:self.project_actions.append(a)
        return a

    def create_actions(self):
        a=self.action
        a("new","새 프로젝트…",self.new_project,"Ctrl+N")
        a("open","열기…",self.open_project,"Ctrl+O")
        a("save","저장",self.save,"Ctrl+S",True,True)
        a("save_as","다른 이름 저장…",lambda:self.save(as_new=True),"Ctrl+Shift+S",True,True)
        a("recover","작업 복구…",self.recover)
        a("close","프로젝트 닫기",lambda:self.maybe_leave(lambda:self.submit("close_project",{},self.project_closed)),project=True)
        a("quit","종료",self.close,"Alt+F4")
        a("undo","실행 취소",lambda:self.command("undo",{}),"Ctrl+Z",True,True)
        a("redo","다시 실행",lambda:self.command("redo",{}),"Ctrl+Y",True,True)
        a("add","추가…",self.add_object,"Ctrl+Insert",True,True)
        # 결정 D1: F2 = 칸 편집([LIST-R410]), 속성 대화상자는 두 번 클릭·Alt+Enter·선택 객체 창의 '속성…' 단추.
        a("edit","속성…",self.edit_object,"Alt+Return",True,True)
        self.actions["edit"].setShortcuts([QKeySequence("Alt+Return"),QKeySequence("Alt+Enter")])
        a("edit_cell","칸 편집",self.edit_cell,"F2",project=True)
        a("delete","삭제…",self.delete_object,"Delete",True,True)
        a("detach","현재 변형에서 DID 분리",self.detach,mutation=True,project=True)
        a("detach_instance","현재 변형에서 인스턴스를 사본으로 바꾸기",self.detach_instance,mutation=True,project=True)
        a("bulk_conditions","선택 서비스 상태 조건 일괄 변경…",self.bulk_conditions,mutation=True,project=True)
        a("copy_type","데이터 타입 독립 복사…",self.copy_selected_type,mutation=True,project=True)
        a("replace_type","데이터 타입 바꾸기…",self.replace_selected_type,mutation=True,project=True)
        a("references","선택 객체의 참조 영향…",self.open_reference_impact,project=True)
        a("bulk","선택 행 일괄 편집…",self.bulk_edit,mutation=True,project=True)
        a("copy","표 복사",self.copy_table,"Ctrl+C",project=True)
        a("paste","DTC 표 붙여넣기…",self.paste_dtcs,"Ctrl+V",True,True)
        a("search","검색",lambda:self.search.setFocus(),"Ctrl+F",project=True)
        a("find","찾기…",self.find_document_dialog,"Ctrl+Shift+F",project=True)
        a("find_next","다음 찾기",self.find_next,"F3",project=True)
        a("back","뒤로",self.history_back,"Alt+Left",project=True)
        a("forward","앞으로",self.history_forward,"Alt+Right",project=True)
        a("path","이름 경로 복사",self.copy_path,project=True)
        a("stable_path","안정 경로 복사",self.copy_stable_path,project=True)
        a("goto","객체 경로로 이동",lambda:self.path_entry.setFocus(),"Ctrl+L",project=True)
        a("goto_did","관련 DID로 이동",lambda:self.go_related("did"),project=True)
        a("goto_type","관련 데이터 타입으로 이동",lambda:self.go_related("data_type"),project=True)
        a("qualifiers","Qualifier 개요…",lambda:self.open_qualifier_overview(False),project=True)
        a("shortcuts","Shortcut Qualifier 개요…",self.open_shortcut_matrix,project=True)
        a("attributes","Attribute 개요…",self.open_attribute_overview,project=True)
        a("compact","변형 Compact View…",self.open_compact_tree,project=True)
        a("compare","변형 Compare…",lambda:self.open_compare(False),project=True)
        a("compare_file","저장된 프로젝트와 비교…",lambda:self.open_compare(True),project=True)
        a("graph","상태 그래프…",lambda:self.open_explorer("state_graph"),project=True)
        a("translations","번역 표…",lambda:self.open_explorer("translation_overview"),project=True)
        a("translate","선택 객체 번역 추가·수정…",self.add_translation,mutation=True,project=True)
        a("fault_memory","고장 메모리 레코드…",self.open_fault_memory,project=True)
        a("revisions","개정 이력…",self.open_revision_history,project=True)
        a("rework","Rework 목록…",self.open_rework,project=True)
        a("validate","프로젝트 검증",self.validate_project,"F9",project=True)
        self.actions["validate"].setShortcuts([QKeySequence("F9"),QKeySequence("F5")])  # Keyboard Shortcuts: F9 = 일관성 검사(F5도 유지)
        a("validate_export","현재 변형의 ODX 사전 검사",self.validate_export,project=True)
        a("preview","전문 미리보기…",self.preview,"Ctrl+P",project=True)
        a("conditions","상태 조건…",self.conditions,mutation=True,project=True)
        a("new_variant","변형 추가…",self.open_variant_wizard,mutation=True,project=True)
        a("ecu","ECU 속성…",lambda:self.inspect_edit(self.state.get("ecu_id")),mutation=True,project=True)
        a("cdd","CDD·CDDT 가져오기…",self.import_cdd)
        a("csv_import","DTC CSV 가져오기…",self.import_csv,mutation=True,project=True)
        a("csv_export","DTC CSV 내보내기…",self.export_csv,project=True)
        a("odx","ODX 2.2.0 내보내기…",self.export_odx,project=True)
        a("about","프로그램 정보…",self.about)
        # 보기(M1-6·M1-7·M4-3)와 설정(M6-2)
        a("settings","설정…",self.open_settings)
        a("list_max","목록 크게 보기",self.toggle_list_max,project=True);self.actions["list_max"].setCheckable(True)
        self.actions["list_max"].setToolTip("트리·선택 객체 창·결과 창을 숨기고 목록을 크게 봅니다. Esc로 되돌립니다.")
        a("column_info","열 정보…",lambda:self.show_column_info(),project=True)
        a("reset_view","보기 초기화",self.reset_view)
        # F6·Shift+F6: 트리 → 목록 → 선택 객체 창을 돈다(Keyboard Shortcuts).
        a("focus_next","다음 창으로",lambda:self.cycle_focus(1),"F6")
        a("focus_prev","이전 창으로",lambda:self.cycle_focus(-1),"Shift+F6")
        self.actions["reset_view"].setToolTip("분할 위치·결과 창 높이·열 너비·출력 창 필터를 처음 값으로 되돌립니다.")
        menus={"파일":["new","open","save","save_as","recover","close","settings","quit"],"편집":["undo","redo","add","edit","edit_cell","delete","detach","detach_instance","copy_type","replace_type","bulk","bulk_conditions","copy","paste","search","find","find_next","back","forward","translate"],"보기":["list_max","column_info","focus_next","focus_prev","reset_view"],"프로젝트":["ecu","new_variant","validate","preview","conditions","references","path","stable_path","goto","goto_did","goto_type","qualifiers","shortcuts","attributes","compact","compare","compare_file","graph","translations","fault_memory","revisions","rework"],"가져오기 / 내보내기":["cdd","csv_import","csv_export","odx"],"도움말":["about"]}
        menus["가져오기 / 내보내기"].insert(-1,"validate_export")
        for title,keys in menus.items():
            menu=self.menuBar().addMenu(title)
            for k in keys:menu.addAction(self.actions[k])
            if title=="파일":
                # [MENU-R340] 최근 파일(개수는 설정)
                self.recent_menu=QMenu("최근 파일",self);self.recent_menu.aboutToShow.connect(self.fill_recent_menu)
                menu.insertMenu(self.actions["save"],self.recent_menu)
            if title=="보기":self.view_menu=menu
        toolbar=QToolBar("주 도구",self);toolbar.setMovable(False);self.addToolBar(toolbar)
        # 참조(U6·P2): CANdela 리본의 References에 해당하는 도구 버튼
        for k in ("new","open","cdd","save","undo","redo","add","edit","references","validate","preview","odx"):
            if k in ("undo","add","validate"):toolbar.addSeparator()
            toolbar.addAction(self.actions[k])

    def create_ui(self):
        central=QWidget();layout=QVBoxLayout(central);layout.setContentsMargins(18,14,18,12)
        top=QHBoxLayout();titles=QVBoxLayout()
        self.heading=QLabel("진단 데이터 작업 공간");self.heading.setObjectName("heading");titles.addWidget(self.heading)
        self.subtitle=QLabel("프로젝트를 만들거나 .diagproj / CDD 파일을 열어 시작하세요.");self.subtitle.setObjectName("subheading");titles.addWidget(self.subtitle)
        top.addLayout(titles,1);self.badge=QLabel("OFFLINE  ·  UDS");self.badge.setObjectName("badge");top.addWidget(self.badge);layout.addLayout(top)
        # 시작 화면(M6-1)과 작업 영역을 겹쳐 두고 프로젝트가 열리면 작업 영역을 보인다.
        self.work_area=QWidget();self.work_area.setObjectName("workArea");work=QVBoxLayout(self.work_area);work.setContentsMargins(0,0,0,0)
        path_bar=QHBoxLayout();path_bar.addWidget(QLabel("경로"))
        self.history_back_button=QPushButton("‹");self.history_back_button.setToolTip("뒤로 (Alt+←)");self.history_back_button.setMaximumWidth(34);self.history_back_button.clicked.connect(self.history_back);path_bar.addWidget(self.history_back_button)
        self.history_forward_button=QPushButton("›");self.history_forward_button.setToolTip("앞으로 (Alt+→)");self.history_forward_button.setMaximumWidth(34);self.history_forward_button.clicked.connect(self.history_forward);path_bar.addWidget(self.history_forward_button)
        self.path_entry=QLineEdit();self.path_entry.setObjectName("objectPath")
        self.path_entry.setPlaceholderText("이름 경로 또는 qpath:를 입력하고 Enter (예: /Base Variant/[DID]VIN, qpath:/Base_Variant/[DTC]DTC_403012, Ctrl+L)")
        self.path_entry.setToolTip("문법 없는 입력은 이름 경로입니다. qpath:는 Qualifier 경로, [DID]·[DTC] 같은 선택자로 종류를 고릅니다. "
                                   "이름은 큰따옴표, Qualifier는 작은따옴표로 감쌀 수 있고 \\로 특수 글자를 이스케이프합니다. ECU 조각은 있어도 없어도 됩니다.")
        self.path_entry.returnPressed.connect(self.go_path);path_bar.addWidget(self.path_entry,1)
        # [TB-R310] 오른쪽 끝 ▾ = 이번 세션에 넣은 경로, '이동' 단추
        self.path_history_button=QToolButton();self.path_history_button.setText("▾");self.path_history_button.setToolTip("이번 세션에 넣은 경로")
        self.path_history_menu=QMenu(self);self.path_history_menu.aboutToShow.connect(self.fill_path_history)
        self.path_history_button.setMenu(self.path_history_menu);self.path_history_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.path_history_button.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")  # ▾ 글자만(메뉴 화살표 중복 방지)
        path_bar.addWidget(self.path_history_button)
        self.path_go_button=QPushButton("이동");self.path_go_button.setToolTip("경로로 이동 (Enter)");self.path_go_button.clicked.connect(self.go_path);path_bar.addWidget(self.path_go_button)
        copy_path=QPushButton("이름 경로 복사");copy_path.clicked.connect(self.copy_path);path_bar.addWidget(copy_path)
        stable_path=QPushButton("안정 경로 복사");stable_path.clicked.connect(self.copy_stable_path);path_bar.addWidget(stable_path)
        work.addLayout(path_bar)
        self.splitter=QSplitter()
        self.tree_model=QStandardItemModel(self);self.tree_model.setHorizontalHeaderLabels(["프로젝트 탐색"])
        self.tree=QTreeView();self.tree.setModel(self.tree_model);self.tree.setMinimumWidth(240);self.tree.setUniformRowHeights(True);self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.tree.customContextMenuRequested.connect(self.tree_menu)
        # 선택 변경으로 받는다. UIA SelectionItem.Select는 현재 항목(current)을 바꾸지 않고 선택만 바꾼다.
        self.tree.selectionModel().selectionChanged.connect(lambda *_:self.tree_selected());self.splitter.addWidget(self.tree)
        # [UI-R100-Focus] 노드를 눌러 들어가면 목록에 포커스. 키보드 화살표로 트리를 옮길 때는 트리에 남고, Enter로 목록에 들어간다.
        self.tree.clicked.connect(lambda *_:QTimer.singleShot(0,self.focus_list));self.tree.installEventFilter(self)
        center=QWidget();cl=QVBoxLayout(center);cl.setContentsMargins(6,0,6,0)
        filters=QHBoxLayout();self.category=QLabel("DID");filters.addWidget(self.category)
        self.search=QLineEdit();self.search.setObjectName("listFilter");self.search.setPlaceholderText("번호 · 이름 · Qualifier 필터 (Ctrl+F) · 띄어 쓴 단어는 하나라도, \"구절\"은 그대로");filters.addWidget(self.search,1)
        self.search.setClearButtonEnabled(True);self.search.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.search.customContextMenuRequested.connect(self.search_menu)
        # M1-3: 필터가 걸리면 보이는 행 / 전체 행 칩을 늘 보인다(결정 D2: OR는 좁히려는 사용자에게 반대로 느껴질 수 있다).
        self.filter_chip=QLabel("");self.filter_chip.setObjectName("filterChip");self.filter_chip.setStyleSheet("background:#fff4d6;color:#7a5200;border-radius:4px;padding:4px 8px;")
        self.filter_chip.setVisible(False);filters.addWidget(self.filter_chip)
        self.filter_back=QPushButton("← 이전 필터");self.filter_back.setToolTip("검증 결과로 이동하기 전의 검색 필터로 돌아갑니다.");self.filter_back.setVisible(False);self.filter_back.clicked.connect(self.restore_filter);filters.addWidget(self.filter_back)
        self.locale=QComboBox()
        for loc in ("ko-KR","en-US","de-DE","fr-FR","ja"):self.locale.addItem(loc)
        # DTC 코드 표시 형식(2.1): 기본 0x 16진, SAE 문자형은 표시 전용(원시값은 툴팁)
        self.dtc_format=QComboBox();self.dtc_format.setToolTip("DTC 코드 표시 형식. SAE 문자형은 표시 전용이며 원시값은 툴팁에 보입니다.")
        for label,data in DTC_FORMATS:self.dtc_format.addItem(label,data)
        saved=self.settings.value("dtc_format","hex")
        self.dtc_format.setCurrentIndex(max(0,self.dtc_format.findData(saved)))
        self.dtc_format.currentIndexChanged.connect(self.dtc_format_changed);filters.addWidget(self.dtc_format)
        # 의존성 보기(U4): 서비스별 평면 목록(편집 모드) / 클래스·역할별 묶음(개요 모드)
        # 27차: 보존 항목 목록은 처음에 출력 차단 항목만 보인다. 켜면 해석됨(원문 참조용)·메타데이터도 보인다.
        self.preserved_all=QCheckBox("출력 영향 없는 원문도 보이기");self.preserved_all.setObjectName("preservedAll");self.preserved_all.setVisible(False)
        self.preserved_all.setToolTip("메타데이터·출처 정보와 이미 해석한 정의(원문 참조용)도 목록에 보입니다. 출력 차단에는 영향이 없습니다.")
        self.preserved_all.toggled.connect(lambda *_:self.queue_job(self.refresh) if self.state.get("open") and self.kind=="unsupported" else None);filters.addWidget(self.preserved_all)
        # 29·30차: 예전에 보존하던 요소 중 근거를 찾아 가져온 객체(구분 '해결됨(가져옴)'). 처음부터 보인다.
        self.preserved_resolved=QCheckBox("해결된 항목 보이기");self.preserved_resolved.setObjectName("preservedResolved");self.preserved_resolved.setChecked(True);self.preserved_resolved.setVisible(False)
        self.preserved_resolved.setToolTip("예전에 원문으로 보존하던 CDD 요소 중 Vector 출력·도움말 근거를 찾아 가져온 객체입니다. 행을 열면 가져온 객체가 열립니다.")
        self.preserved_resolved.toggled.connect(lambda *_:self.queue_job(self.refresh) if self.state.get("open") and self.kind=="unsupported" else None);filters.addWidget(self.preserved_resolved)
        self.dep_mode=QComboBox();self.dep_mode.addItem("서비스별 (일괄 변경: 여러 행 선택 후 F2)","flat");self.dep_mode.addItem("클래스별 묶음","group")
        self.dep_mode.currentIndexChanged.connect(self.dep_mode_changed);self.dep_mode.setVisible(False);filters.addWidget(self.dep_mode)
        self.locale.currentTextChanged.connect(self.locale_changed);filters.addWidget(self.locale)
        # M1-6: 목록 크게 보기(트리·선택 객체 창·결과 창 숨김, Esc로 되돌림)
        self.list_max_button=QPushButton("⤢ 목록 크게");self.list_max_button.setToolTip("트리·선택 객체 창·결과 창을 숨기고 목록을 크게 봅니다. Esc로 되돌립니다.")
        self.list_max_button.clicked.connect(lambda:self.toggle_list_max());filters.addWidget(self.list_max_button);cl.addLayout(filters)
        # 페이지 머리(U2·U6): 인스턴스 머리, 클래스 설명, 장 소개 문장, 원본 안내
        self.page_panel=QWidget();self.page_panel.setObjectName("pagePanel");pv=QVBoxLayout(self.page_panel);pv.setContentsMargins(0,2,0,4);pv.setSpacing(3)
        # M5-4: 인스턴스 페이지 머리의 이름 칸(Enter·포커스 이동 = 확정, Esc = 되돌림)과 속성 대화상자 단추
        self.instance_bar=QWidget();self.instance_bar.setObjectName("instanceBar");ib=QHBoxLayout(self.instance_bar);ib.setContentsMargins(0,0,0,0)
        ib.addWidget(QLabel("이름"))
        self.instance_name=InstanceNameEdit();self.instance_name.setObjectName("instanceName");self.instance_name.setMinimumWidth(260)
        self.instance_name.setToolTip("인스턴스 이름. Enter로 확정, Esc로 되돌립니다. 이름에서 만든 Qualifier는 이름을 따라 바뀝니다.")
        self.instance_name.committed.connect(self.rename_instance);ib.addWidget(self.instance_name,1)
        self.instance_props=QPushButton("속성…");self.instance_props.setObjectName("instanceProperties")
        self.instance_props.setToolTip("인스턴스 속성 대화상자(이름·Qualifier·설명)");self.instance_props.clicked.connect(self.edit_instance)
        ib.addWidget(self.instance_props);ib.addStretch(1)
        self.instance_bar.setVisible(False);pv.addWidget(self.instance_bar)
        self._instance_info=None
        ph=QHBoxLayout();ph.setContentsMargins(0,0,0,0);pv.addLayout(ph)
        self.page_header=QLabel("");self.page_header.setObjectName("pageHeader");self.page_header.setWordWrap(True);self.page_header.setTextFormat(Qt.TextFormat.RichText)
        self.page_header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);ph.addWidget(self.page_header,1)
        self.fault_memory_button=QPushButton("고장 메모리 레코드…");self.fault_memory_button.setToolTip("DtcStatusMask·DTC 표·스냅샷·확장 데이터 레코드(읽기 전용)")
        self.fault_memory_button.clicked.connect(self.open_fault_memory);self.fault_memory_button.setVisible(False);ph.addWidget(self.fault_memory_button)
        self.page_panel.setVisible(False);cl.addWidget(self.page_panel)
        # M6-7 의존성 표 옵션: 모두 펼치기·접기(클래스별 묶음), 주소 방식 표시(첫 열 뒤), 모든 열 펼치기(상태 열 머리 오른쪽 버튼으로 접은 열)
        self.dep_bar=QWidget();self.dep_bar.setObjectName("dependencyBar");db=QHBoxLayout(self.dep_bar);db.setContentsMargins(0,0,0,2)
        self.dep_expand_all=QPushButton("모두 펼치기");self.dep_expand_all.setToolTip("클래스별 묶음의 모든 묶음 행을 펼칩니다(Extend All Nodes).")
        self.dep_expand_all.clicked.connect(lambda:self.expand_dependency_groups(True));db.addWidget(self.dep_expand_all)
        self.dep_collapse_all=QPushButton("모두 접기");self.dep_collapse_all.setToolTip("클래스별 묶음의 모든 묶음 행을 접습니다(Collapse All Nodes).")
        self.dep_collapse_all.clicked.connect(lambda:self.expand_dependency_groups(False));db.addWidget(self.dep_collapse_all)
        self.dep_address=QPushButton("주소 방식 표시");self.dep_address.setCheckable(True);self.dep_address.setObjectName("dependencyAddressing")
        self.dep_address.setToolTip("첫 열 뒤에 서비스 주소 방식(P = physical, F = functional)을 보입니다(Show Service Addressing).")
        self.dep_address.toggled.connect(self.dep_address_changed);db.addWidget(self.dep_address)
        self.dep_all_columns=QPushButton("모든 열 펼치기");self.dep_all_columns.setEnabled(False)
        self.dep_all_columns.setToolTip("상태 열 머리의 오른쪽 버튼 '왼쪽 상태 열 접기'로 숨긴 열을 모두 다시 보입니다(Extend all Columns).")
        self.dep_all_columns.clicked.connect(self.show_all_state_columns);db.addWidget(self.dep_all_columns)
        hint=QLabel("상태 열 머리를 오른쪽 버튼으로 누르면 그 왼쪽 상태 열을 접습니다.");hint.setStyleSheet("color:#677a92");db.addWidget(hint,1)
        self.dep_bar.setVisible(False);cl.addWidget(self.dep_bar)
        self._dep_address=False;self._dep_hidden=set();self._dep_raw=[]
        self.model=ObjectModel(self);self.table=QTableView();self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True);self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # M1-1: 칸 편집(F2는 동작으로, 글자를 치면 그 글자로, 선택된 칸을 천천히 한 번 더 누르면). 두 번 클릭은 속성 대화상자다.
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.SelectedClicked|QAbstractItemView.EditTrigger.AnyKeyPressed|QAbstractItemView.EditTrigger.EditKeyPressed)
        self.delegate=CellDelegate(self,self.table);self.table.setItemDelegate(self.delegate);self.delegate.closeEditor.connect(self._editor_closed)
        self.table.setSortingEnabled(False)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.table.customContextMenuRequested.connect(self.table_menu)
        # 정렬은 작업 스레드가 한다. 머리를 누르면 한 열, Ctrl+누르면 여러 열([LIST-R200]·[LIST-R210]). 정렬 표시는 머리 글자에 ▲▼(순서 번호)로 한다([LIST-R250]).
        header=self.table.horizontalHeader();header.setSortIndicatorShown(False);header.setSectionsClickable(True);header.sectionClicked.connect(self.header_clicked)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive);header.setStretchLastSection(True);header.sectionResized.connect(self.column_resized)
        # M1-7: 헤더 툴팁(모델 headerData)과 오른쪽 버튼 '열 정보…'
        self.table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self.header_menu)
        self.table.doubleClicked.connect(self.edit_object);self.table.selectionModel().selectionChanged.connect(self.selection_changed)
        self.table.clicked.connect(self.table_clicked);self.model.dtc_format=self.dtc_format.currentData() or "hex"
        self.model.sort_requested.connect(self.sort_changed);self.model.cell_edited.connect(self.cell_edited);self.model.spec_for=self.spec_at;cl.addWidget(self.table,1)
        # 결정 D4: 페이지를 나누지 않고 한 목록으로 싣는다. 아래 줄은 개수·revision만 보인다.
        pager=QHBoxLayout();self.page_label=QLabel("프로젝트 없음");self.page_label.setObjectName("countLabel");pager.addWidget(self.page_label,1);cl.addLayout(pager)
        # 선택 객체 창은 목록 옆이 아니라 목록 아래에 둔다(목록 | 선택 객체 세로 분할). 넓은 폭을 멤버 표·바이트 격자에 쓴다.
        properties=QWidget();properties.setObjectName("selectionPane");pl=QVBoxLayout(properties);pl.setContentsMargins(6,6,6,0)
        self.detail_tabs=QTabWidget();self.detail_tabs.setObjectName("detailTabs")
        # 탭 줄 한 줄에 '선택 객체'(왼쪽)와 '속성 편집 F2'(오른쪽)를 함께 둔다. 제목·버튼 줄을 따로 두지 않아 표 높이를 아낀다.
        title=QLabel("선택 객체");title.setObjectName("selectionTitle");title.setContentsMargins(2,0,12,0)
        self.detail_tabs.setCornerWidget(title,Qt.Corner.TopLeftCorner)
        self.edit_button=QPushButton("속성…  Alt+Enter");self.edit_button.setToolTip("선택 객체의 속성 대화상자 (Alt+Enter · 두 번 클릭). 목록 칸은 F2로 바로 고칩니다.");self.edit_button.clicked.connect(self.edit_object)
        self.detail_tabs.setCornerWidget(self.edit_button,Qt.Corner.TopRightCorner)
        self.properties=QPlainTextEdit();self.properties.setReadOnly(True);self.properties.setPlaceholderText("표에서 객체를 선택하면 상세 속성과 공유 영향을 표시합니다.")
        self.properties.setAcceptDrops(False)  # 끌어 놓은 파일은 주 창이 연다(M6-1)
        # M2-2: 속성 탭 = 편집 가능한 속성 시트(위) + 상세 글(아래, 읽기 전용)
        self.property_sheet=PropertySheet();self.property_sheet.edit_requested.connect(self.sheet_edit);self.property_sheet.message.connect(self.show_reason)
        self.property_tab=QSplitter(Qt.Orientation.Vertical);self.property_tab.setObjectName("propertyTab");self.property_tab.setChildrenCollapsible(False)
        self.property_tab.addWidget(self.property_sheet);self.property_tab.addWidget(self.properties)
        self.property_tab.setStretchFactor(0,3);self.property_tab.setStretchFactor(1,2)
        # U2: DID·서비스의 멤버 표·바이트 격자(인스턴스 페이지·DID 마스터·디테일 공용 위젯). M5-2 멤버 칸 편집
        self.layout_view=LayoutView();self.detail_tabs.addTab(self.layout_view,"데이터 배치")
        self.layout_view.member_edited.connect(self.member_edit);self.layout_view.message.connect(self.show_reason)
        # 마스터·디테일의 디테일 표(U4): 상태 그룹의 상태, 변형 식별 패턴
        self.detail_table=DetailTable();self.detail_tabs.addTab(self.detail_table,"세부 표")
        self.detail_tabs.addTab(self.property_tab,"속성");self.detail_tabs.setCurrentWidget(self.property_tab)
        title.setMinimumHeight(self.detail_tabs.tabBar().sizeHint().height())  # 탭 글자와 같은 높이에 맞춘다
        pl.addWidget(self.detail_tabs,1)
        self.work_splitter=QSplitter(Qt.Orientation.Vertical);self.work_splitter.setObjectName("workSplitter");self.work_splitter.setChildrenCollapsible(False)
        self.work_splitter.addWidget(center);self.work_splitter.addWidget(properties)
        self.work_splitter.setStretchFactor(0,3);self.work_splitter.setStretchFactor(1,2);self.work_splitter.setSizes([380,300])
        self.splitter.addWidget(self.work_splitter);self.splitter.setStretchFactor(1,1);self.splitter.setSizes([280,1110])
        work.addWidget(self.splitter,1)
        self.start_page=StartPage(self.settings)
        self.start_page.open_requested.connect(self.open_recent);self.start_page.folder_requested.connect(self.open_in_folder)
        self.start_page.command_requested.connect(self.start_command)
        self.stack=QStackedWidget();self.stack.setObjectName("centralStack");self.stack.addWidget(self.start_page);self.stack.addWidget(self.work_area)
        layout.addWidget(self.stack,1);self.setCentralWidget(central)
        dock=QDockWidget("검증 · 작업 결과",self);dock.setObjectName("resultsDock")
        container=QWidget();box=QVBoxLayout(container);box.setContentsMargins(6,4,6,4)
        header=QHBoxLayout();header.addWidget(QLabel("심각도"));self.issue_severity=QComboBox()
        for label,data in (("전체",None),("오류","error"),("경고","warning"),("정보","info")):self.issue_severity.addItem(label,data)
        self.issue_severity.currentIndexChanged.connect(self.apply_issue_filter);header.addWidget(self.issue_severity)
        self.validation_summary=QLabel("검증을 실행하세요.");header.addWidget(self.validation_summary,1)
        self.validation_stale=QLabel("이전 revision 결과 · F5로 재검증");self.validation_stale.setStyleSheet("color:#a06b19");self.validation_stale.setVisible(False);header.addWidget(self.validation_stale)
        box.addLayout(header)
        tabs=QTabWidget();self.issue_model=ObjectModel(self);self.issue_model.replace("issue",[],ISSUE_COLUMNS);self.issue_table=QTableView();self.issue_table.setModel(self.issue_model);self.issue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.issue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.issue_table.doubleClicked.connect(self.goto_issue);self.issue_table.horizontalHeader().setStretchLastSection(True)
        tabs.addTab(self.issue_table,"검증 결과 (더블 클릭으로 이동)");self.log=WorkLog(settings=self.settings);tabs.addTab(self.log,"작업 기록");box.addWidget(tabs);dock.setWidget(container);self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea,dock);dock.setMinimumHeight(170)
        self.results_tabs=tabs;self.log.navigate_requested.connect(self.log_navigate)
        dock.toggleViewAction().setStatusTip("검증 결과·작업 기록 창을 보이거나 숨깁니다.")
        if hasattr(self,"view_menu"):self.view_menu.insertAction(self.actions["column_info"],dock.toggleViewAction())
        # 목록과 선택 객체 창이 세로로 나뉘므로 결과 창의 처음 높이는 최소로 둔다(끌어서 늘릴 수 있다). initial_layout에서 적용한다.
        self.results_dock=dock
        self.progress=QProgressBar();self.progress.setRange(0,0);self.progress.setMaximumWidth(170);self.progress.setVisible(False)
        self.cancel=QPushButton("작업 취소");self.cancel.clicked.connect(lambda:self.worker.cancel_event.set());self.cancel.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress);self.statusBar().addPermanentWidget(self.cancel)
        self.filter_timer=QTimer(self);self.filter_timer.setSingleShot(True);self.filter_timer.setInterval(250);self.filter_timer.timeout.connect(self.search_now);self.search.textChanged.connect(self.search_edited)
        # 선택이 바뀌면 잠깐 기다렸다가 마지막 선택으로 한 번 조회한다(Shift 범위 선택 중 작업이 겹쳐 선택을 놓치지 않게).
        self.selection_timer=QTimer(self);self.selection_timer.setSingleShot(True);self.selection_timer.setInterval(60);self.selection_timer.timeout.connect(self.selection_now)
        self.esc_shortcut=QShortcut(QKeySequence("Esc"),self);self.esc_shortcut.setEnabled(False);self.esc_shortcut.activated.connect(self.escape_pressed)

    def submit(self,method,args,callback=None,on_error=None):
        """작업 스레드 호출. on_error를 주면 오류 때 대화상자 대신 그 함수를 부른다(예: 틀린 경로는 출력 창에만, M3-2)."""
        if self.pending:return False
        job=uuid.uuid4().hex
        self.callbacks[job]=(callback,method,args,on_error)
        self.pending=True;self._pending_method=method;self.update_enabled();self.progress.setVisible(True);self.cancel.setVisible(method in ("cdd_preview","apply_cdd","validate_project","save_project","export_odx"))
        if method not in QUIET_METHODS:self.statusBar().showMessage("처리 중…")
        self.request.emit(job,method,args)
        return True

    def finished(self,job,result,error):
        callback,method,args,on_error=self.callbacks.pop(job,(None,"",{},None));self.pending=False;self._pending_method=None;self.progress.setVisible(False);self.cancel.setVisible(False);self.update_enabled()
        busy=self._busy_dialog;self._busy_dialog=None
        if busy is not None:busy.set_busy(False)
        origin=ORIGINS.get(method,"작업")
        if error:
            self._after_refresh=None
            if on_error:on_error(error);return
            action=(args or {}).get("action") if method=="execute_command" else None
            self.log.appendPlainText(f"{COMMAND_TEXT.get(action,action) if action else method}: {error['code']} · {error['message']}","오류",origin)
            self.statusBar().showMessage(error["message"],10000)
            if error["code"]!="CANCELLED":QMessageBox.warning(QApplication.activeModalWidget() or self,"작업을 완료하지 못했습니다",error["message"])
        else:
            # 조용한 조회(목록·선택·경로)는 상태 줄을 덮지 않는다(칸 편집 거부 이유·찾기 위치 같은 앞 메시지가 남게).
            if method not in QUIET_METHODS:self.statusBar().showMessage("완료",2500)
            self.log_completion(method,args or {},result,origin)
            if callback:callback(result)
        self._drain_commands()

    def log_completion(self,method,args,result,origin):
        """출력 창(M4-1): 명령 완료는 '정보'(대상 객체 링크), 저장·가져오기·출력·검증은 '정보', 그 밖의 일상 작업은 '상태'(처음에 숨김)."""
        if method in QUIET_METHODS:return
        if method=="execute_command":
            action=args.get("action","")
            selected=(result or {}).get("selected_id") if isinstance(result,dict) else None
            target={"oid":selected,"variant":self.variant} if selected and not str(selected).startswith(("unsupported:","src:","template:","group:")) else None
            revision=(result or {}).get("revision") if isinstance(result,dict) else None
            self.log.appendPlainText(f"{COMMAND_TEXT.get(action,action)} 완료"+(f" · revision {revision}" if revision is not None else ""),"정보",origin,target)
            return
        if method=="save_project":
            self.log.appendPlainText(f"저장했습니다: {args.get('path','')}","정보",origin);return
        if method in ("open_project","new_project","apply_cdd"):
            self.log.appendPlainText({"open_project":"프로젝트를 열었습니다.","new_project":"새 프로젝트를 만들었습니다.","apply_cdd":"CDD를 가져왔습니다."}[method]
                                     +(f" {result.get('path') or ''}" if isinstance(result,dict) and result.get("path") else ""),"정보",origin);return
        if method=="validate_project" and isinstance(result,dict):
            issues=result.get("issues") or []
            self.log.appendPlainText(f"검증 완료 · {result.get('scope_label','프로젝트 전체')} · {len(issues)}개 항목 · 출력 차단 {sum(1 for i in issues if i.get('blocks_export'))}개","정보",origin);return
        if method in ("export_odx","export_csv"):
            path=(result or {}).get("path") if isinstance(result,dict) else None
            self.log.appendPlainText(f"출력했습니다{': '+str(path) if path else ''}","정보",origin);return
        self.log.appendPlainText(f"{method}: 완료","상태",origin)

    def when_idle(self,fn,tries=400):
        """작업 중이면 끝난 뒤 실행한다(대화상자의 적용 뒤 목록 갱신이 도는 중에 확인을 누른 경우 등)."""
        if not self.pending:fn();return
        if tries>0:QTimer.singleShot(50,lambda:self.when_idle(fn,tries-1))

    def show_reason(self,text):
        """고칠 수 없는 칸의 F2 등(D1): 상태 줄과 출력 창에 이유를 쓴다."""
        if not text:return
        self.statusBar().showMessage(text,6000);self.log.appendPlainText(text,"정보","편집")

    def log_navigate(self,target):
        """출력 창 링크(↗, [OW-R240]): 메시지의 대상 객체로 이동한다."""
        if self.pending or not self.state.get("open") or not isinstance(target,dict):return
        if target.get("oid"):self.submit("locate_object",{"oid":target["oid"],"variant":target.get("variant") or self.variant},self.navigate_target)
        elif target.get("key") and self.select_node(target["key"]):self.page=0;self.refresh()

    def startup(self):
        if self.initial_path and is_cdd_path(self.initial_path):
            self._import_path=str(self.initial_path);self.submit("cdd_preview",{"path":self.initial_path},self.cdd_ready)
        elif self.initial_path:self.submit("open_project",{"path":self.initial_path},self.apply_state)
        else:self.submit("recoveries",{},self.recovery_choices)

    def update_enabled(self):
        opened=self.state.get("open",False);writable=opened and not self.state.get("readonly",False)
        for a in self.actions.values():a.setEnabled(not self.pending)
        for a in self.project_actions:a.setEnabled(opened and not self.pending)
        for a in self.mutations:a.setEnabled(writable and not self.pending)
        if "undo" in self.actions:
            # 선택 조회 같은 짧은 조회 중에도 실행 취소·다시 실행 키를 받는다(command가 끝난 뒤 실행). 저장·검증 같은 긴 작업 중에는 막는다.
            free=not self.pending or getattr(self,"_pending_method",None) in QUIET_METHODS
            self.actions["undo"].setEnabled(writable and free and self.state.get("undo",False));self.actions["redo"].setEnabled(writable and free and self.state.get("redo",False))
        readonly_kind=self.kind in READONLY_REASON
        if hasattr(self,"edit_button"):
            self.edit_button.setEnabled(writable and not self.pending and self.kind!="unsupported" and self.kind not in READONLY_REASON)
            self.search.setEnabled(opened);self.locale.setEnabled(opened and not self.pending);self.path_entry.setEnabled(opened and not self.pending)
            self.path_go_button.setEnabled(opened and not self.pending);self.path_history_button.setEnabled(opened and not self.pending)
            self.list_max_button.setEnabled(opened)
        # 칸 편집·다음 찾기·창 이동은 작업 중에도 받는다(확정한 칸 명령은 줄에 세워 두었다가 보낸다).
        for name in ("edit_cell","find_next"):
            if name in self.actions:self.actions[name].setEnabled(opened)
        for name in ("focus_next","focus_prev"):
            if name in self.actions:self.actions[name].setEnabled(True)
        if "add" in self.actions:
            for name in ("add","edit","delete"):
                self.actions[name].setEnabled(writable and not self.pending and not readonly_kind)
                self.actions[name].setToolTip(READONLY_REASON.get(self.kind,"") if readonly_kind else "")
        self.update_history_buttons()

    def apply_state(self,state):
        previous_name=self.state.get("name")
        self.state=state
        if not state.get("open"):self.project_closed();return
        if previous_name!=state["name"]:
            self.clear_history()
            dialog=getattr(self,"compare_dialog",None)  # 다른 프로젝트를 열면 이전 비교 창을 닫는다
            if dialog is not None and previous_name is not None:dialog.close();dialog.deleteLater();self.compare_dialog=None
        self.show_start(False)
        if state.get("path") and state["path"]!=self._recent_path:
            self._recent_path=state["path"];add_recent_file(self.settings,state["path"])
        if self.variant not in {v["id"] for v in state["variants"]}:self.variant=state["variants"][0]["id"]
        suffix=" *" if state["dirty"] else ""
        read=" · 읽기 전용" if state["readonly"] else ""
        self.setWindowTitle(f"{state['name']}{suffix}{read} — DiagAuthor {__version__}")
        self.heading.setText(state["name"])
        self.subtitle.setText((state["path"] or "새 프로젝트 · 저장 전")+f"  ·  revision {state['revision']}")
        c=state["counts"];self.badge.setText(f"DID {c['did']:,}  ·  DTC {c['dtc']:,}  ·  서비스 {c['service']:,}")
        self.locale.blockSignals(True);self.locale.setCurrentText(state["locale"]);self.locale.blockSignals(False)
        self.build_tree(state["tree"])
        dialog=getattr(self,"compare_dialog",None)
        if dialog is not None:dialog.set_variants(state["variants"]);dialog.set_current_revision(state["revision"])
        # 프로젝트를 연 직후의 첫 위치도 이력의 시작점이다. 없으면 첫 이동 뒤 ‹(뒤로)로 돌아올 수 없다.
        if not self.history and self.node_key:self.record_history(self.node_key,None)
        if state.get("migrated") and self._migration_seen!=(state.get("path"),tuple(state["migrated"])):
            # 같은 파일의 변환 알림은 한 번만 남긴다(명령마다 되풀이하지 않는다). 알림 창은 설정(확인 탭)에 따른다.
            self._migration_seen=(state.get("path"),tuple(state["migrated"]))
            message=f"이전 스키마 파일을 버전 {state['migrated'][-1]}로 변환해 열었습니다. 저장하면 새 형식으로 기록되고 원본은 .bak으로 남습니다."
            self.log.appendPlainText(message,"경고","열기")
            if option(self.settings,"notify_migration"):QTimer.singleShot(0,lambda:QMessageBox.information(self,"이전 형식 파일",message))
        if self.validation_revision is not None and self.validation_revision!=state["revision"]:self.log.appendPlainText("검증 결과가 이전 revision입니다. F5로 다시 검증하세요.","경고","검증")
        selected=state.get("selected_id")
        if self._node_fallback and selected and not str(selected).startswith("unsupported:"):
            # 선택 노드가 사라졌다(예: 인스턴스를 사본으로 바꿈). 선택 객체가 있는 노드로 이동한다.
            self._node_fallback=False
            self.update_enabled();self.update_validation_header()
            self.submit("locate_object",{"oid":selected,"variant":self.variant},self.navigate_target)
            return
        self.update_enabled();self.refresh(selected);self.update_validation_header()

    # ------------------------------------------------------------------ 탐색 트리
    def tree_node(self,parent,label,key,kind,variant=None,scope=None,tip=None,bold=False,warn=False):
        item=QStandardItem(label);item.setEditable(False)
        item.setData({"key":key,"kind":kind,"variant":variant,"scope":scope,"label":label},Qt.ItemDataRole.UserRole)
        if tip:item.setToolTip(tip)
        if bold:
            f=QFont();f.setBold(True);item.setFont(f)
        if warn:item.setForeground(QColor("#a06b19"))
        parent.appendRow(item);self.tree_items[key]=item
        return item

    def variant_nodes(self,parent,v):
        """변형 하위(U4): 식별 특징(기본)·식별 패턴(확장), DID, DTC, 지원 진단 클래스(→ 클래스·싱글턴·Job), 모든 서비스.
        서비스는 인스턴스 페이지에서 연다."""
        vid=v["id"];prefix=f"var:{vid}"
        if v.get("identification")=="features":
            self.tree_node(parent,"식별 특징",prefix+":ident","ident_features",vid,vid,"Identifying Features: 변형을 식별하는 데이터(원본 CDD, 읽기 전용)")
        elif v.get("identification")=="patterns":
            self.tree_node(parent,"식별 패턴",prefix+":ident","ident_patterns",vid,vid,"Identifying Patterns: 이 변형의 식별 값(원본 CDD, 읽기 전용)")
        self.tree_node(parent,f"DID ({v['dids']})",prefix+":did","did",vid)
        self.tree_node(parent,f"DTC ({v['dtcs']})",prefix+":dtc","dtc",vid)
        jobs=v.get("jobs",[])
        classes=self.tree_node(parent,f"지원 진단 클래스 ({len(v['classes'])+len(jobs)})",prefix+":classes","supported_classes",vid,vid,
                               "Supported Diagnostic Classes: 템플릿 클래스 체크 목록(읽기 전용). 하위에 이 변형의 클래스·인스턴스가 있습니다.")
        for c in v["classes"]:
            if c["singleton"] and len(c["instances"])==1:
                self.instance_node(classes,vid,c["instances"][0],f"싱글턴 클래스 {c['name']}",show_identifier=False)
                continue
            warn=sum(i["warnings"] for i in c["instances"])
            node=self.tree_node(classes,f"{c['name']} ({len(c['instances'])})"+(" ⚠" if warn else ""),f"{prefix}:cls:{c['id']}","diagnostic_instance",vid,c["id"],
                                f"진단 클래스 {c['qualifier']}"+(f"\n보존 원문이 연결된 인스턴스 {warn}건" if warn else ""))
            for inst in c["instances"]:self.instance_node(node,vid,inst)
        for j in jobs:
            # Job 표는 원본 CDD에서 읽기 전용으로 보인다(Name, File, Description). Job 내용은 해석하지 않고 원문으로 보존한다.
            self.tree_node(classes,"🔒 "+j["name"]+(" ↪" if j["reference"] else "")+(" ⚠" if j["warnings"] else ""),f"{prefix}:job:{j['id']}","job",vid,j["id"],
                           "Job 컨테이너(읽기 전용, 내용은 원문 보존)"+(" · 다른 변형 원본 참조" if j["reference"] else ""),warn=bool(j["warnings"]))
        self.tree_node(parent,f"모든 서비스 ({v['services']})",prefix+":svc","service",vid)

    def instance_node(self,parent,vid,inst,extra=None,show_identifier=True):
        # CANdela 표기: 괄호 없이 '01 Default Session', 'F190 VIN'. 식별자 없는 싱글턴은 이름만 쓰고
        # 서비스에서 유도한 식별자는 툴팁에만 둔다(결정 #1). 공유 구분은 텍스트 기호 대신 아이콘이다.
        prefix=f"{inst['identifier_text']} " if show_identifier and self._tree_identifiers and inst["identifier_text"] else ""
        label=prefix+inst["name"]+(" ⚠" if inst["warnings"] else "")
        tip=[SHARE_TIP[inst["share"]]+f" · 사용 변형 {inst['shared']}개",f"서비스: {inst['services'] or '(가져온 서비스 없음)'}"]
        if extra:tip.insert(0,extra)
        if not show_identifier and inst["identifier_text"]:tip.append(f"식별자 {inst['identifier_text']} (서비스에서 유도)")
        if inst["required"]:tip.append("필수(required) 인스턴스")
        if inst["warnings"]:tip.append(f"가져오지 못한 원문 {inst['warnings']}건 보존(출력 차단)")
        item=self.tree_node(parent,label,f"var:{vid}:inst:{inst['id']}","service",vid,inst["id"],"\n".join(tip),bold=inst["required"],warn=bool(inst["warnings"]))
        item.setIcon(share_icon(inst["share"]))
        return item

    def build_tree(self,tree):
        """상태가 바뀔 때마다 다시 그리되 펼침·선택을 노드 키로 되살린다."""
        expanded={k for k,item in getattr(self,"tree_items",{}).items() if self.tree.isExpanded(item.index())}
        first=not getattr(self,"tree_items",None)
        self.tree_items={}
        self.name_paths=tree.get("paths",{})
        self.tree.selectionModel().blockSignals(True);self.tree_model.removeRows(0,self.tree_model.rowCount())
        c=tree["counts"];state=self.state
        root=self.tree_node(self.tree_model.invisibleRootItem(),state["name"],"ecu","ecu",tip="ECU 정보")
        if tree.get("has_interfaces"):
            # ECU Information 하위(U5): 지원 인터페이스 체크 목록과 지원 인터페이스별 통신 파라미터(원본 CDD, 읽기 전용)
            ifaces=self.tree_node(root,"지원 인터페이스","ifaces","interfaces",tip="Supported Interfaces: 인터페이스 체크 목록(원본 CDD, 읽기 전용)")
            for item in tree.get("interfaces",[]):
                self.tree_node(ifaces,item["name"],f"iface:{item['qualifier']}","comm_params",None,item["qualifier"],"통신 파라미터(원본 CDD, 읽기 전용)")
        fm=self.tree_node(root,"고장 메모리","fm","dtc_overview",tip="DTC 개요: 변형별 포함 여부")
        self.tree_node(fm,f"DTC 개요 ({c['dtc']})","dtc_ov","dtc_overview")
        self.tree_node(root,f"DID 개요 ({c['did']})","did_ov","did_overview",tip="DID 개요: 변형별 포함 여부")
        base=next(v for v in tree["variants"] if v["is_base"])
        node=self.tree_node(root,"◆ "+base["name"],f"var:{base['id']}","diagnostic_class",base["id"],tip="기본 변형")
        self.variant_nodes(node,base)
        others=[v for v in tree["variants"] if not v["is_base"]]
        group=self.tree_node(root,f"변형 ({len(others)})","variants","variant",tip="확장 변형 목록")
        for v in others:
            node=self.tree_node(group,"◇ "+v["name"]+(" 🔒" if v["locked"] else ""),f"var:{v['id']}","diagnostic_class",v["id"],tip="확장 변형")
            self.variant_nodes(node,v)
        # 상태 하위 3개(U4): State Groups(마스터·디테일), Dependencies, Defaults. 그룹별 노드는 상태 그룹 아래에 남긴다(결정 4).
        states=self.tree_node(root,"상태","states","state")
        sgroups=self.tree_node(states,"상태 그룹","sgroups","state_group",tip="State Groups: 그룹을 고르면 아래 세부 표에 상태가 보입니다.")
        for g in tree["state_groups"]:
            self.tree_node(sgroups,f"{g['name']} ("+" · ".join(st["name"] for st in g["states"])+")",f"sg:{g['id']}","state",None,g["id"],"첫 상태가 초기 상태")
        self.tree_node(states,"의존성","dep","dependency",tip="서비스 × 상태: ✓ 허용, ✗ 차단, → 전이, — 제한 없음. 현재 변형 기준")
        if tree.get("has_defaults"):
            self.tree_node(states,"기본 상태 전이","sdefaults","state_defaults",tip="Defaults: 템플릿 서비스의 기본 상태 조건·전이(원본 CDD, 읽기 전용)")
        self.tree_node(root,f"데이터 타입 ({c['data_type']})","types","data_type")
        self.tree_node(root,f"프로토콜 서비스 ({c['protocol_service']})","psvc","protocol_service",tip="템플릿의 프로토콜 서비스(읽기 전용): 요청·응답 패턴, 사용·필수")
        nrcs=self.tree_node(root,f"부정 응답 코드 ({c['nrc']})","nrcs","nrc",tip="템플릿의 NRC 목록(읽기 전용)")
        if tree.get("unsupported_nrcs"):
            self.tree_node(nrcs,f"미지원 서비스 NRC ({tree['unsupported_nrcs']})","nrcs_unsupported","unsupported_nrc",tip="Negative Response Codes for unsupported services(원본 CDD, 읽기 전용)")
        blocking=c.get("unsupported_blocking",c["unsupported"]);resolved=c.get("unsupported_resolved",0)
        self.tree_node(root,f"보존 항목 ({c['unsupported']} · 차단 {blocking}"+(f" · 해결 {resolved}" if resolved else "")+")","unsupported","unsupported",
                       tip=f"원문으로 보존한 CDD 조각 {c['unsupported']}개 중 출력을 막는 것 {blocking}개. 목록은 처음에 출력 차단 항목과 해결된 항목(예전에 보존하다 가져오게 된 객체 {resolved}개)이 보입니다.")
        if first or not expanded:expanded={"ecu",f"var:{base['id']}",f"var:{base['id']}:classes"}
        for k in expanded:
            if k in self.tree_items:self.tree.setExpanded(self.tree_items[k].index(),True)
        # 선택 노드가 사라지면(인스턴스 사본 교체 등) 대체 노드로 가고, apply_state가 선택 객체의 위치를 다시 찾는다.
        self._node_fallback=bool(self.node_key) and self.node_key not in self.tree_items
        key=self.node_key if self.node_key in self.tree_items else f"var:{self.variant}:did" if f"var:{self.variant}:did" in self.tree_items else f"var:{base['id']}:did"
        item=self.tree_items[key]
        parent=item.parent()
        while parent is not None:
            self.tree.setExpanded(parent.index(),True);parent=parent.parent()
        self.tree.setCurrentIndex(item.index());self.tree.scrollTo(item.index())
        self.apply_node(item.data(Qt.ItemDataRole.UserRole))
        self.tree.selectionModel().blockSignals(False)

    def apply_node(self,data):
        changed=data["key"]!=self._applied_node
        self._applied_node=data["key"]
        self.node_key=data["key"];self.kind=data["kind"];self.scope=data["scope"]
        if data["variant"]:self.variant=data["variant"]
        label=data["label"]
        if data["kind"]=="dependency":label+=" · "+next((v["name"] for v in self.state["tree"]["variants"] if v["id"]==self.variant),"")
        self.category.setText(label)
        if hasattr(self,"dep_mode"):self.dep_mode.setVisible(self.kind=="dependency")
        if hasattr(self,"preserved_all"):self.preserved_all.setVisible(self.kind=="unsupported")
        if hasattr(self,"preserved_resolved"):self.preserved_resolved.setVisible(self.kind=="unsupported")
        if hasattr(self,"dep_bar"):self.dep_bar.setVisible(self.kind=="dependency")
        if changed:
            # 다른 노드로 옮기면 선택 객체 창(목록 아래)에 이전 객체가 남지 않게 비우고 이름 경로를 보인다.
            self.clear_properties()
            if hasattr(self,"layout_view"):self.layout_view.nrc_visible=option(self.settings,"nrc_on_open")  # 설정: 서비스를 열 때 NRC 목록
            path=self.name_paths.get(data["key"])
            if path:self.path_entry.setText(path)
            if hasattr(self,"page_header"):self.set_page_info(None)

    def clear_properties(self):
        self.details_record=None
        self.properties.clear()
        if hasattr(self,"property_sheet"):self.property_sheet.clear()
        if hasattr(self,"layout_view"):self.layout_view.set_record(None)
        if hasattr(self,"detail_table"):self.detail_table.set_table(None)

    # ------------------------------------------------------------------ 페이지 머리(U2·U6)
    KIND_TEXT={
     "supported_classes":"템플릿의 진단 클래스 체크 목록입니다(읽기 전용). 체크는 이 변형이 쓰는 클래스입니다. 클래스·인스턴스는 하위 노드에서 봅니다.",
     "ident_features":"변형을 식별하는 데이터(Identifying Features)입니다. 원본 CDD의 읽기 전용 값입니다.",
     "ident_patterns":"이 변형의 식별 패턴입니다. 패턴마다 식별 특징의 값이 모두 맞으면 이 변형으로 식별합니다(원본 CDD, 읽기 전용).",
     "comm_params":"이 인터페이스의 통신 파라미터입니다. 덮어쓴 값은 ECU에서 바꾼 값이고, 비어 있으면 기본값을 씁니다(원본 CDD, 읽기 전용).",
     "job":"Job 목록입니다(원본 CDD, 읽기 전용). Job 내용은 해석하지 않고 원문으로 보존하며 출력은 차단됩니다.",
     "diagnostic_class":"이 변형의 진단 클래스입니다. 클래스를 고르면 인스턴스 목록, 인스턴스를 고르면 인스턴스 페이지가 열립니다.",
     "service":"이 변형의 모든 서비스입니다. 서비스를 고르면 아래에 요청·응답 배치(멤버 표·바이트 격자)가 보입니다.",
     "did":"DID 목록입니다. DID를 고르면 아래에 데이터 배치(멤버 표·바이트 격자)가 보입니다.",
     "dtc":"이 변형의 DTC입니다. 코드 표시 형식(0x/SAE)은 위의 선택 상자에서 바꿉니다. 속성 열은 원본 DTC 레코드 항목입니다."}

    def set_page_info(self,info):
        import html
        text="";fault_memory=False
        instance=info if info and info.get("kind")=="instance" else None
        self._instance_info=instance
        if hasattr(self,"instance_bar"):
            # M5-4: 이름 칸은 인스턴스 페이지에서만 보인다. 읽기 전용 프로젝트·원문 인스턴스는 고칠 수 없다.
            self.instance_bar.setVisible(instance is not None)
            if instance is not None:
                editable=bool(instance.get("editable",True)) and not self.state.get("readonly")
                if not self.instance_name.hasFocus() or self.instance_name.text()==instance.get("title",""):self.instance_name.set_original(instance.get("title",""))
                self.instance_name.setReadOnly(not editable)
                self.instance_name.setToolTip("인스턴스 이름. Enter로 확정, Esc로 되돌립니다. 이름에서 만든 Qualifier는 이름을 따라 바뀝니다." if editable
                                              else "읽기 전용 인스턴스입니다(읽기 전용 프로젝트 또는 원문으로 가져온 인스턴스).")
                self.instance_props.setEnabled(not self.pending)
        if instance is not None:
            # 이름은 위 이름 칸에 있으므로 머리 글에는 식별자와 Qualifier만 굵게 쓴다.
            head=f"<b>{html.escape(info['identifier'])}</b>" if info.get("identifier") else ""
            head+=(" &nbsp;" if head else "")+f"<span style='color:#677a92'>Qualifier {html.escape(info.get('qualifier') or '')}</span>"
            meta=[x for x in (info.get("class_name") and "클래스 "+info["class_name"],info.get("share_text"),
                              info.get("owner") and "원본 변형 "+info["owner"],info.get("variants") and "사용 변형 "+", ".join(info["variants"]),
                              info.get("required") and "필수 인스턴스",info.get("warnings") and f"보존 원문 {info['warnings']}건(출력 차단)") if x]
            parts=[head]
            if info.get("text"):parts.append(html.escape(info["text"]))
            if meta:parts.append("<span style='color:#677a92'>"+html.escape(" · ".join(meta))+"</span>")
            parts.append("<span style='color:#677a92'>회색 행 = 템플릿에 있지만 쓰지 않는 서비스(미사용) 또는 원문으로만 보존한 서비스(보존 원문), 읽기 전용. 서비스를 고르면 아래에 파라미터 탭·바이트 격자가 보입니다.</span>")
            text="<br>".join(parts);fault_memory=bool(info.get("fault_memory"))
        elif info and (info.get("title") or info.get("text")):
            title,body=info.get("title") or "",info.get("text") or ""
            text=(f"<b>{html.escape(title)}</b>" if title else "")+("<br>" if title and body else "")+html.escape(body)
        if not text:
            intro=CHAPTER_TEXT.get(self.node_key or "") or self.KIND_TEXT.get(self.kind)
            if intro:text="<span style='color:#677a92'>"+html.escape(intro)+"</span>"
        self.page_header.setText(text)
        self.fault_memory_button.setVisible(fault_memory)
        self.page_panel.setVisible(bool(text) or fault_memory)

    def select_node(self,key):
        item=self.tree_items.get(key)
        if item is None:return False
        parent=item.parent()
        while parent is not None:
            self.tree.setExpanded(parent.index(),True);parent=parent.parent()
        self.tree.selectionModel().blockSignals(True);self.tree.setCurrentIndex(item.index());self.tree.selectionModel().blockSignals(False)
        self.tree.scrollTo(item.index());self.apply_node(item.data(Qt.ItemDataRole.UserRole))
        return True

    def project_closed(self,_=None):
        dialog=getattr(self,"compare_dialog",None)
        if dialog is not None:dialog.close();dialog.deleteLater();self.compare_dialog=None
        self.state={"open":False};self.tree_model.removeRows(0,self.tree_model.rowCount());self.tree_items={};self.node_key=None;self.scope=None;self.model.replace("did",[]);self.clear_properties();self.variant=None;self.details_record=None
        self.name_paths={};self._applied_node=None;self.clear_history();self._after_refresh=None;self._dep_expanded=set();self._dep_hidden=set();self._dep_raw=[]
        if hasattr(self,"page_panel"):self.page_header.clear();self.fault_memory_button.setVisible(False);self.page_panel.setVisible(False)
        self.heading.setText("진단 데이터 작업 공간");self.subtitle.setText("새 프로젝트 또는 파일 열기로 시작하세요.");self.badge.setText("OFFLINE · UDS");self.setWindowTitle(f"DiagAuthor {__version__}");self.update_enabled()
        self.path_entry.clear();self._recent_path=None;self._keep_selection=None;self._pending_telegram=None
        if hasattr(self,"stack"):self.toggle_list_max(False);self.show_start(True)
        self.validation_revision=None;self._validation_issues=[];self.filter_stash=None
        if hasattr(self,"validation_stale"):self.validation_stale.setVisible(False)
        if hasattr(self,"validation_summary"):self.validation_summary.setText("검증을 실행하세요.")
        if hasattr(self,"filter_back"):self.filter_back.setVisible(False)
        self._focus_oid="";self._focus_field="";self._focus_remedy="";self._focus_shown=False;self._inspect_field=""
        if hasattr(self,"issue_model"):self.reset_issue_model()
        if hasattr(self,"issue_severity"):self.issue_severity.setCurrentIndex(0)
        self._command_queue=[];self._edit_pending=0;self._deferred_page=None;self._find={}
        if getattr(self,"find_dialog",None) is not None:self.find_dialog.close()

    def refresh(self,selected=None):
        if not self.state.get("open") or self.pending:return
        # 명령 뒤 선택 유지 대상이 이 목록에 없어도(다른 노드의 객체) 오류 없이 목록을 보인다.
        # 결정 D4: 페이지를 나누지 않는다(page_size 0). 정렬은 sorts(여러 열), 없으면 작업 스레드의 기본 정렬(결정 D5).
        args={"kind":self.kind,"variant":self.variant,"scope":self.scope,"filter_text":self.search.text(),"sort":None,"page":0,"page_size":0,
              "target_id":selected,"strict_target":False}
        if self.sorts:args["sorts"]=[list(x) for x in self.sorts]
        if self.kind=="unsupported":args.update(blocking_only=not self.preserved_all.isChecked(),include_resolved=self.preserved_resolved.isChecked())
        self.submit("query_objects",args,lambda result:self.show_page(result,selected))

    # 정렬 호환(예전 한 열 정렬 필드). sorts의 첫 열이다.
    @property
    def sort_key(self):return (self.sorts or [("name",False)])[0][0]
    @sort_key.setter
    def sort_key(self,value):self.sorts=[(value,self.descending if self.sorts else False)]+list((self.sorts or [])[1:])
    @property
    def descending(self):return bool((self.sorts or [("name",False)])[0][1])
    @descending.setter
    def descending(self,value):self.sorts=[(self.sort_key,bool(value))]+list((self.sorts or [])[1:])

    def list_key(self):
        """정렬·열 너비를 기억하는 목록 종류. 인스턴스 페이지와 모든 서비스 목록은 다른 목록이다."""
        return self.kind+(":page" if self.kind=="service" and self.scope else "")

    def show_page(self,result,selected=None):
        # M1-1: 칸을 편집하는 중이거나 확정한 칸 명령이 남아 있으면 목록을 다시 싣지 않고 보류한다(편집기가 사라지지 않게).
        if self._edit_pending>0 or self.table.state()==QAbstractItemView.State.EditingState:
            self._deferred_page=(result,selected);return
        self._deferred_page=None
        rows=result["rows"];self._last_columns=result.get("columns")
        columns=result.get("columns")
        if self.kind=="dependency":
            self._dep_raw=list(rows)
            if self.dep_mode.currentData()=="group":rows=self.group_dependency_rows(rows,columns or [])
            if self._dep_address and columns:columns=list(columns[:1])+[("addressing","주소 방식")]+list(columns[1:])  # M6-7: 첫 열 뒤
        # [UI-R340-SelMod] 수정 뒤 선택 유지: 다시 싣기 전의 선택·현재 칸을 기억한다.
        previous=[row["id"] for row in self.selected_rows()];current=self.table.currentIndex()
        current_id=self.model.rows[current.row()]["id"] if current.isValid() and current.row()<len(self.model.rows) else None
        column=current.column() if current.isValid() else 0
        keep=self._keep_selection;self._keep_selection=None
        if keep and len(keep)>1:previous=list(keep)  # 속성 시트에서 여러 객체를 고친 뒤(M2-2) 같은 행들을 다시 고른다.
        self._record_items={item["key"]:item for item in result.get("record_items") or []}
        self.table.selectionModel().blockSignals(True)
        self.model.new_row_label=self.new_row_text()
        self.model.column_info=result.get("column_info") or {}
        self._effective_sort=[tuple(x) for x in result.get("sort") or []]
        self.model.replace(self.kind,rows,columns)
        visible={f for f,_ in self.model.columns};marks=[x for x in self._effective_sort if x[0] in visible]
        self.model.sort_marks={field:(n+1,desc) for n,(field,desc) in enumerate(marks)}
        self.table.selectionModel().blockSignals(False)
        self.apply_hidden_columns()
        total=result["total"];self._unfiltered=result.get("unfiltered",total)
        self.page=0
        self.page_label.setText(f"{total:,}개 객체  ·  revision {result['revision']}")
        filtered=bool(self.search.text().strip())
        self.filter_chip.setText(f"{total:,} / {self._unfiltered:,} 표시");self.filter_chip.setVisible(filtered)
        self.filter_chip.setToolTip("필터에 맞는 행 / 전체 행. 띄어 쓴 단어는 하나라도 맞으면 보이고(OR), 큰따옴표로 감싼 구절은 그대로 찾습니다.")
        self.apply_column_widths()
        self.set_page_info(result.get("page_info"))
        self.restore_selection(selected,previous,current_id,column)
        self.show_focus_hint(selected)
        hook=self._after_refresh;self._after_refresh=None
        if hook:QTimer.singleShot(0,hook)

    def restore_selection(self,selected,previous,current_id,column):
        """[LIST-R600] 비어 있지 않은 목록은 늘 선택이 있다: 지정 대상 → 다시 싣기 전 선택 → 첫 행 순서로 고른다.
        선택 변경 신호로 선택 객체 창이 갱신된다."""
        position={row["id"]:i for i,row in enumerate(self.model.rows)}
        targets=[position[selected]] if selected in position else [position[i] for i in previous if i in position]
        if not targets and self.model.rows:targets=[0]
        if not targets:return
        focus=position.get(selected) if selected in position else position.get(current_id,targets[0])
        if focus not in targets:focus=targets[0]
        selection=QItemSelection()
        for row in sorted(set(targets)):selection.select(self.model.index(row,0),self.model.index(row,max(0,self.model.columnCount()-1)))
        model=self.table.selectionModel()
        model.select(selection,QItemSelectionModel.SelectionFlag.ClearAndSelect|QItemSelectionModel.SelectionFlag.Rows)
        model.setCurrentIndex(self.model.index(focus,min(column,max(0,self.model.columnCount()-1))),QItemSelectionModel.SelectionFlag.NoUpdate)
        self.table.scrollTo(self.model.index(focus,0))

    def width_key(self):
        return (self.list_key(),tuple(field for field,_ in self.model.columns))

    def apply_column_widths(self):
        """[LIST-R710] 처음 여는 목록만 내용에 맞추고(설정 '열 너비를 내용에 맞추기'), [LIST-R720] 다시 들어오면(또는 명령 뒤 다시 실어도)
        사용자가 끈 너비를 그대로 둔다(이번 세션 동안)."""
        key=self.width_key();saved=self._widths.get(key)
        self._sizing=True
        try:
            if saved:
                for column,(field,_) in enumerate(self.model.columns):
                    if field in saved:self.table.setColumnWidth(column,saved[field])
            else:
                if option(self.settings,"fit_columns"):self.fit_columns()
                self._widths[key]={field:self.table.columnWidth(column) for column,(field,_) in enumerate(self.model.columns)}
        finally:
            self._sizing=False

    def column_resized(self,index,old,new):
        if self._sizing or not (0<=index<len(self.model.columns)):return
        self._widths.setdefault(self.width_key(),{})[self.model.columns[index][0]]=new

    def header_clicked(self,section):
        """[LIST-R200] 머리를 누르면 그 열 오름차순, 다시 누르면 반대([LIST-R220]). [LIST-R210] Ctrl+누르면 정렬 열을 더한다."""
        if not (0<=section<len(self.model.columns)) or not self.state.get("open"):return
        field=self.model.columns[section][0]
        visible={f for f,_ in self.model.columns}
        current=[tuple(x) for x in (self.sorts or [x for x in self._effective_sort if x[0] in visible])]
        if QApplication.keyboardModifiers()&Qt.KeyboardModifier.ControlModifier and current:
            fields=[f for f,_ in current]
            current=[(f,(not d) if f==field else d) for f,d in current] if field in fields else current+[(field,False)]
        elif current and current[0][0]==field and (len(current)==1 or not self.sorts):current=[(field,not current[0][1])]
        else:current=[(field,False)]
        self.sorts=current;self._sort_memory[self.list_key()]=current
        self.queue_job(self.refresh)  # 선택 조회 중에 눌러도 버리지 않고 끝난 뒤 다시 싣는다

    def fit_columns(self):
        """열 너비를 내용에 맞춘다(열마다 최대 320, 이름 열은 250~360, 첫 식별자 열은 90~160 — 표 끝 새 항목 행 문구는 줄여 보인다).
        Qualifier는 맨 뒤 열이라 남는 폭만 채우고, 폭이 모자라면 가로 스크롤 끝으로 밀린다."""
        self.table.resizeColumnsToContents()
        for column,(field,_) in enumerate(self.model.columns):
            low,high=(250,360) if field=="name" else (90,160) if column==0 else (60,320)
            self.table.setColumnWidth(column,max(low,min(self.table.columnWidth(column),high)))

    def new_row_text(self):
        """표 끝 새 항목 행(U6). 추가 대화상자를 여는 방식이다. 읽기 전용 목록·읽기 전용 프로젝트에는 두지 않는다."""
        writable=self.state.get("open") and not self.state.get("readonly")
        if not writable or self.kind in READONLY_REASON or self.kind in ("unsupported","ecu","state"):return None
        if not option(self.settings,"new_row"):return None
        return "＋ 새 항목을 만들려면 여기를 누르세요"

    def table_clicked(self,index):
        # 표 끝 새 항목 행(magic line): [UI-R335] 기준 행은 마지막 보이는 행, [UI-R330-MagicLine] 새 요소는 목록 끝에 온다.
        if index.isValid() and self.model.is_new_row(index.row()) and not self.pending:self.add_object(magic=True);return
        if not index.isValid() or index.row()>=len(self.model.rows) or index.column()>=len(self.model.columns):return
        # M5-3: 서비스 행의 자리표시(zz·yy…)·rc 칸을 누르면 그 전문 표·NRC 목록을 연다.
        field=self.model.columns[index.column()][0];row=self.model.rows[index.row()]
        target=LINK_FIELDS.get(field)
        if not target or row.get("kind")!="service" or not row.get(field):return
        if self.details_record and self.details_record.get("id")==row["id"]:
            if self.layout_view.show_telegram(target):self.detail_tabs.setCurrentWidget(self.layout_view)
        else:self._pending_telegram=(row["id"],target)

    def group_dependency_rows(self,rows,columns):
        """의존성 개요 모드(U4): 클래스·역할(템플릿 서비스)별 묶음 행. 셀은 구성원 값이 같으면 그 값, 다르면 (혼합).
        묶음 행을 두 번 누르면 인스턴스별 행을 펼치거나 접는다."""
        fields=[c[0] for c in columns if c[0].startswith("st:")]
        groups={};order=[]
        for row in rows:
            key=row.get("group_key") or row["id"]
            if key not in groups:groups[key]=[];order.append(key)
            groups[key].append(row)
        result=[]
        for key in order:
            members=groups[key];first=members[0]
            expanded=key in self._dep_expanded
            header={"id":f"group:{key}","kind":"dependency_group","group_key":key,"name":("▾ " if expanded else "▸ ")+first.get("group_label",first.get("name","")),
                    "identifier_text":"","instance":f"인스턴스 {len(members)}개","member_ids":[m["id"] for m in members],"supported":1}
            for field in fields+["addressing"]:
                values={m.get(field) for m in members}
                header[field]=values.pop() if len(values)==1 else "(혼합)"
            result.append(header)
            if expanded:result.extend({**m,"name":"    "+m.get("name","")} for m in members)
        return result

    def toggle_dependency_group(self,row):
        key=row.get("group_key")
        if key in self._dep_expanded:self._dep_expanded.discard(key)
        else:self._dep_expanded.add(key)
        self.refresh()

    def dep_mode_changed(self,*_):
        if self.kind=="dependency" and self.state.get("open"):self.page=0;self.refresh()

    # ------------------------------------------------------------------ 의존성 표 옵션(M6-7)
    def expand_dependency_groups(self,expand):
        """모두 펼치기·접기(Extend/Collapse All Nodes). 서비스별 보기에서 누르면 클래스별 묶음으로 바꾼다."""
        if self.kind!="dependency" or not self.state.get("open"):return
        self._dep_expanded={row.get("group_key") or row["id"] for row in self._dep_raw} if expand else set()
        if self.dep_mode.currentData()!="group":
            self.dep_mode.setCurrentIndex(self.dep_mode.findData("group"));return  # dep_mode_changed가 새로 고친다
        self.refresh()

    def dep_address_changed(self,on):
        self._dep_address=bool(on)
        if self.kind=="dependency" and self.state.get("open"):self.refresh()

    def apply_hidden_columns(self):
        """의존성 표에서 접은 상태 열을 숨긴다(목록을 다시 그려도 유지). 다른 목록은 모든 열을 보인다."""
        hidden=self._dep_hidden if self.kind=="dependency" else set()
        for col,(field,_) in enumerate(self.model.columns):self.table.setColumnHidden(col,field in hidden)
        if hasattr(self,"dep_all_columns"):
            count=len(self._dep_hidden)
            self.dep_all_columns.setEnabled(bool(count));self.dep_all_columns.setText(f"모든 열 펼치기 ({count}열 접힘)" if count else "모든 열 펼치기")

    def collapse_state_columns_left(self,section):
        """Collapse State Columns to the Left: 누른 상태 열보다 왼쪽의 상태 열을 숨긴다."""
        if not (0<=section<len(self.model.columns)):return
        target=self.model.columns[section][0]
        states=[field for field,_ in self.model.columns if field.startswith("st:")]
        if target not in states:return
        self._dep_hidden=set(states[:states.index(target)])
        self.apply_hidden_columns()
        if self._dep_hidden:self.statusBar().showMessage(f"상태 열 {len(self._dep_hidden)}개를 접었습니다. '모든 열 펼치기'로 되돌립니다.",6000)

    def show_all_state_columns(self):
        self._dep_hidden=set();self.apply_hidden_columns()

    def dtc_format_changed(self,*_):
        self.model.dtc_format=self.dtc_format.currentData() or "hex"
        self.settings.setValue("dtc_format",self.model.dtc_format)
        if self.model.rowCount():
            self.model.dataChanged.emit(self.model.index(0,0),self.model.index(self.model.rowCount()-1,max(0,self.model.columnCount()-1)))

    def show_focus_hint(self,oid):
        if self._focus_oid and oid==self._focus_oid and not self._focus_shown:
            message=f"수정 안내: {self._focus_remedy}"+(f" · 대상 필드: {self._focus_field}" if self._focus_field else "")
            self.statusBar().showMessage(message,10000);self.log.appendPlainText(message,"정보","검증",{"oid":oid,"variant":self.variant});self._focus_shown=True

    def tree_selected(self):
        selected=self.tree.selectionModel().selectedIndexes()
        index=selected[0] if selected else self.tree.currentIndex()
        data=index.data(Qt.ItemDataRole.UserRole) if index.isValid() else None
        if not data:return
        if self.pending:
            # 작업 중에는 선택을 되돌린다. 목록과 트리가 어긋나지 않게 한다.
            QTimer.singleShot(0,lambda:self.select_node(self.node_key));return
        self.apply_node(data);self.reset_list_view();self.refresh()
        self.record_history(data["key"],None)

    def reset_list_view(self):
        """[UI-R100-Filter] 노드에 들어가면 필터를 비우고, 정렬은 이번 세션에서 그 목록에 남긴 정렬([LIST-R240]) 또는 기본 정렬(결정 D5)."""
        self.search.blockSignals(True);self.search.clear();self.search.blockSignals(False)
        self.page=0;self.sorts=self._sort_memory.get(self.list_key())

    def focus_list(self):
        """[UI-R100-Focus] 목록에 포커스를 준다. 선택이 없고 행이 있으면 첫 행을 고른다([UI-R100-Selection])."""
        self.table.setFocus()
        if self.model.rows and not self.table.selectionModel().selectedRows():self.table.selectRow(0)

    def eventFilter(self,obj,event):
        if obj is getattr(self,"tree",None) and event.type()==QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):
            self.focus_list();return True
        return super().eventFilter(obj,event)

    def search_menu(self,pos):
        """[Available DTCs Filter] 필터 칸의 오른쪽 버튼 메뉴에서 필터를 지운다."""
        menu=self.search.createStandardContextMenu();menu.addSeparator()
        clear=menu.addAction("필터 지우기",self.search.clear);clear.setEnabled(bool(self.search.text()))
        menu.exec(self.search.mapToGlobal(pos))

    def sort_changed(self,key,descending):
        self.sorts=[(key,bool(descending))];self._sort_memory[self.list_key()]=self.sorts;self.page=0
        if hasattr(self,"search"):self.refresh()

    def search_now(self):self.page=0;self.refresh()
    def search_edited(self):
        if self.filter_stash and self.filter_stash["search"]!=self.search.text():self.filter_stash=None;self.filter_back.setVisible(False)
        self.filter_timer.start()

    # ------------------------------------------------------------------ 찾기(M3-1)
    def find_document_dialog(self):
        """찾기 창(Search Dialog)을 연다. 비모달이며 결과로 옮겨 가도 창은 열려 있다. Ctrl+F는 목록 필터 칸이다."""
        if not self.state.get("open"):return
        if self.find_dialog is None:
            self.find_dialog=FindDialog(self,self.find_history())
            self.find_dialog.requested.connect(self.find_requested)
            if self._find_criteria:self.find_dialog.set_criteria(self._find_criteria)
        self.find_dialog.show();self.find_dialog.raise_();self.find_dialog.activateWindow()
        self.find_dialog.query.setFocus();self.find_dialog.query.lineEdit().selectAll()

    def find_history(self):
        value=self.settings.value("find_history")
        if value in (None,""):return []
        if isinstance(value,str):return [value]
        return [str(x) for x in value if x]

    def find_requested(self,mode,criteria):
        """mode: first = 문서 처음부터, next = 마지막으로 찾은 곳 다음부터([SRCH-R150]), all = 출력 창에 모두([SRCH-R200])."""
        self._find_criteria=dict(criteria)
        if self.find_dialog is not None:self.settings.setValue("find_history",self.find_dialog.history())
        same=self._find.get("criteria")==criteria and self._find.get("revision")==self.state.get("revision")
        if mode=="next" and same and self._find.get("rows"):
            self._goto_find(self._find["index"]+1);return
        last=self._find.get("last_id") if self._find.get("criteria")==criteria else None
        def got(result):
            rows=result["rows"]
            position=next((i for i,row in enumerate(rows) if row["id"]==last),-1) if last else -1
            self._find={"criteria":dict(criteria),"rows":rows,"revision":result["revision"],"index":position,"last_id":last}
            if mode=="all":self.show_find_all(result);return
            if not rows:self.show_find_results(result);return
            start=0
            if mode=="next" and last:
                position=next((i for i,row in enumerate(rows) if row["id"]==last),None)
                start=position+1 if position is not None else 0
            self._goto_find(start)
        def failed(error):
            if self.find_dialog is not None:self.find_dialog.status.setText(error["message"])
            self.show_reason(error["message"])
        self.queue_job(lambda:self.submit("find_document",{"query":criteria["query"],"scope":criteria.get("scope","all"),"case":bool(criteria.get("case")),
                                                          "word":bool(criteria.get("word")),"whole":bool(criteria.get("whole"))},got,failed))

    def _goto_find(self,index):
        rows=self._find.get("rows") or []
        if not rows:return
        wrapped=index>=len(rows)
        index%=len(rows)
        self._find["index"]=index;row=rows[index];self._find["last_id"]=row["id"]
        status=f"{index+1} / {len(rows)} · {KINDS.get(row['kind'],row['kind'])} {row['name']}"+(" · 문서 끝까지 찾아 처음부터 다시 찾았습니다." if wrapped else "")
        if self.find_dialog is not None:self.find_dialog.status.setText(status)
        self.statusBar().showMessage("찾기: "+status,8000)
        self.goto_found(row)

    def goto_found(self,row):
        if str(row["id"]).startswith("unsupported:"):self.queue_job(lambda:self.submit("details",{"oid":row["id"]},self.navigate_details));return
        self.queue_job(lambda:self.submit("locate_object",{"oid":row["id"],"variant":row.get("variant")},self.navigate_target))

    def find_next(self):
        """F3: 같은 조건으로 다음을 찾는다. 창을 닫은 뒤에도 된다([SRCH-R150-Closed]). 조건이 없으면 찾기 창을 연다."""
        if not self.state.get("open"):return
        if not self._find_criteria:self.find_document_dialog();return
        self.find_requested("next",self._find_criteria)

    def show_find_all(self,result):
        rows=result["rows"]
        if not rows:self.show_find_results(result);return
        self.log.appendPlainText(f"찾기 '{result['query']}': {len(rows):,}개 (↗를 누르면 이동)","정보","찾기")
        for row in rows:
            self.log.appendPlainText(f"{KINDS.get(row['kind'],row['kind'])} · {row['name']} — {row.get('path') or row.get('qualifier') or ''}","정보","찾기",
                                     {"oid":row["id"],"variant":row.get("variant")})
        self.results_dock.show();self.results_tabs.setCurrentWidget(self.log)
        if self.find_dialog is not None:self.find_dialog.status.setText(f"{len(rows):,}개를 출력 창에 썼습니다.")

    def show_find_results(self,result):
        rows=result["rows"]
        if not rows:
            message=f"'{result['query']}'에 해당하는 항목이 없습니다."
            if self.find_dialog is not None:self.find_dialog.status.setText(message)
            QMessageBox.information(self.find_dialog if self.find_dialog is not None and self.find_dialog.isVisible() else self,"찾기",message);return
        self.show_find_all(result)

    def stash_filter(self):
        if self.node_key in self.tree_items and (self.search.text().strip() or self.sorts or self.page>0):
            self.filter_stash={"node_key":self.node_key,"search":self.search.text(),"sorts":list(self.sorts) if self.sorts else None,"page":self.page}
            self.filter_back.setVisible(True)

    def restore_filter(self):
        if not self.filter_stash:return
        stash=self.filter_stash;self.filter_stash=None;self.filter_back.setVisible(False)
        if not self.select_node(stash["node_key"]):
            self.statusBar().showMessage("이전 필터의 목록 위치가 더 이상 없습니다.",6000);return
        self.search.blockSignals(True);self.search.setText(stash["search"]);self.search.blockSignals(False)
        self.sorts=stash["sorts"];self.page=stash["page"]
        self.refresh()
    def locale_changed(self,locale):
        if self.state.get("open") and not self.pending:self.submit("set_locale",{"locale":locale},self.apply_state)

    @staticmethod
    def is_local_row(row):
        """객체가 아닌 행: 원문 유도 행(src:), 쓰지 않는 템플릿 서비스(template:), 의존성 묶음(group:)."""
        return str(row.get("id","")).startswith(("src:","template:","group:"))

    def selected_rows(self):
        return [self.model.rows[index.row()] for index in self.table.selectionModel().selectedRows() if index.row()<len(self.model.rows)]

    def selected_ids(self):
        # 새 항목 행과 객체가 아닌 행은 편집·삭제·경로 명령의 대상이 아니다.
        return [row["id"] for row in self.selected_rows() if not self.is_local_row(row)]

    def selection_changed(self,*_):
        rows=self.selected_rows()
        if not rows:return
        if self.is_local_row(rows[0]):
            self.selection_timer.stop();self.show_local_details(rows[0]);return
        self.selection_timer.start()

    def selection_now(self):
        """선택 객체 창(M2-2): 첫 행의 상세와 선택한 모든 객체의 속성 시트를 한 번에 조회한다."""
        rows=[row for row in self.selected_rows() if not self.is_local_row(row)]
        if not rows:return
        if self.pending:self.selection_timer.start();return
        ids=[row["id"] for row in rows][:500]
        self.submit("selection_details",{"oid":rows[0]["id"],"ids":ids},self.show_selection)

    def show_selection(self,result):
        self.show_details(result["details"])
        sheet=result.get("sheet")
        self.property_sheet.set_sheet(sheet)
        if (sheet or {}).get("count",0)>1:self.detail_tabs.setCurrentWidget(self.property_tab)

    def show_local_details(self,row):
        """객체가 아닌 행은 보이는 열 값을 속성 창에 보인다(작업 스레드 호출 없음)."""
        self.details_record=None
        lines=[]
        for col,(field,label) in enumerate(self.model.columns):
            index=self.model.index(self.model.rows.index(row),col)
            value=self.model.data(index)
            if value not in (None,"","—"):lines.append(f"{label}\n{value}\n")
        kind=row.get("kind")
        if kind=="template_service" and row.get("use")=="보존 원문":lines.append("읽기 전용 — CDD에는 있지만 가져오지 못해 원문으로 보존한 서비스입니다. 출력 차단 대상이며 원문은 보존 항목에서 봅니다.")
        elif kind=="template_service":lines.append("읽기 전용 — 템플릿에 있지만 이 인스턴스에서 쓰지 않는 서비스입니다. 켜는 편집은 템플릿 의미 매핑이 확인될 때까지 막혀 있습니다.")
        elif kind=="dependency_group":lines.append("묶음 행 — 두 번 누르면 인스턴스별 행을 펼치거나 접습니다. 여러 행을 골라 F2로 한 상태 칸을 일괄 변경합니다.")
        else:lines.append("읽기 전용 — 원본 CDD의 보존 원문에서 만든 행입니다. 출력 의미에는 쓰지 않습니다.")
        self.properties.setPlainText("\n".join(lines))
        self.property_sheet.clear()
        self.layout_view.set_record(None);self.detail_table.set_table(None)
        self.detail_tabs.setCurrentWidget(self.property_tab)

    def show_details(self,record):
        self.details_record=record
        self.properties.setPlainText(details_text(record))
        self.layout_view.set_record(record)
        self.detail_table.set_table(record.get("detail_table"))
        has_layout=record.get("layout") is not None or bool(record.get("telegram_layouts"))
        self.detail_tabs.setCurrentWidget(self.layout_view if has_layout else self.detail_table if record.get("detail_table") else self.property_tab)
        pending=self._pending_telegram
        if pending and pending[0]==record.get("id"):
            self._pending_telegram=None
            if self.layout_view.show_telegram(pending[1]):self.detail_tabs.setCurrentWidget(self.layout_view)

    # ------------------------------------------------------------------ 칸 편집(M1-1·M1-2)
    def escape_pressed(self):
        """Esc(목록 크게 보기 중): 칸을 편집하는 중이면 편집만 취소하고(ESC = 취소), 아니면 원래 배치로 되돌린다."""
        if self.table.state()==QAbstractItemView.State.EditingState:
            editor=self.table.indexWidget(self.table.currentIndex())
            if editor is not None:self.delegate.closeEditor.emit(editor,QAbstractItemDelegate.EndEditHint.RevertModelCache);return
        self.toggle_list_max(False)

    def spec_at(self,index):
        """칸 편집 규칙(cell_edit.cell_spec). 읽기 전용 프로젝트는 편집하지 않는다."""
        if not index.isValid() or index.row()>=len(self.model.rows) or index.column()>=len(self.model.columns):return None
        if not self.state.get("open") or self.state.get("readonly"):return None
        row=self.model.rows[index.row()];field,label=self.model.columns[index.column()]
        return cell_spec(row,field,label,self._record_items,self.kind)[0]

    def editor_value(self,index,spec):
        row=self.model.rows[index.row()];field=self.model.columns[index.column()][0]
        return editor_text(row,field,spec,self.model.dtc_format)

    def edit_cell(self):
        """F2(결정 D1): 현재 칸을 편집한다. 편집할 수 없는 칸이면 이유를 알린다. 의존성 목록은 여러 행 일괄 변경이다.
        데이터 배치 멤버 표·속성 시트의 F2는 그 표가 먼저 받는다(panels.EditTable)."""
        if not self.state.get("open"):return
        if self.kind=="dependency":self.edit_object();return
        index=self.table.currentIndex()
        if not index.isValid():
            rows=self.table.selectionModel().selectedRows()
            if rows:index=rows[-1]
        if not index.isValid():self.show_reason("고칠 칸을 먼저 고르세요.");return
        if self.model.is_new_row(index.row()):self.add_object(magic=True);return
        if self.state.get("readonly"):self.show_reason("읽기 전용 프로젝트입니다. 칸을 고칠 수 없습니다.");return
        row=self.model.rows[index.row()];field,label=self.model.columns[index.column()]
        spec,reason=cell_spec(row,field,label,self._record_items,self.kind)
        if not spec:
            if self.kind in READONLY_REASON and reason=="이 목록은 읽기 전용입니다.":reason=READONLY_REASON[self.kind]
            self.show_reason(reason);return
        self.table.setFocus();self.table.edit(index)

    def cell_edited(self,row_index,field,value):
        """칸 확정 → 명령 하나(실행 취소 한 번). 여러 행을 고르고 그중 한 행의 칸을 확정하면 선택한 모든 행에 넣는다([LIST-R420]).
        결과가 오기 전에도 칸에 새 값을 보인다(거부되면 다시 읽어 되돌리고 입력을 칸에 되살린다)."""
        if not (0<=row_index<len(self.model.rows)):return
        row=self.model.rows[row_index];label=dict(self.model.columns).get(field,field)
        spec,reason=cell_spec(row,field,label,self._record_items,self.kind)
        if not spec:self.show_reason(reason);return
        if value==editor_text(row,field,spec,self.model.dtc_format):return  # 바꾼 것이 없으면 명령을 보내지 않는다
        targets=[row]
        selected=self.selected_rows()
        if spec["multi"] and len(selected)>1 and any(r is row for r in selected):
            targets=[r for r in selected if cell_spec(r,field,label,self._record_items,self.kind)[0]]
        try:action,payload=build_command(targets,field,spec,value)
        except ValueError as e:self.show_reason(str(e));return
        shown=display_value(spec,value)
        for target in targets:
            target[field]=shown
            if spec["type"]=="enum":target["raw:"+spec["key"]]=value
            if len(targets)==1 and "qualifier" in payload:target["qualifier"]=payload["qualifier"]
        self.model.refresh_rows()
        if len(targets)>1:
            shared=sum(1 for t in targets if (t.get("shared") or 0)>1)
            self.statusBar().showMessage(f"{len(targets)}개 행에 넣습니다"+(f" · 공유 객체 {shared}개는 모든 사용 변형에 반영됩니다." if shared else "."),8000)
        self._edit_pending+=1
        oid,text=row["id"],value
        def done(state):
            self._edit_pending=max(0,self._edit_pending-1)
            # 선택은 다시 싣기 전의 선택으로 되살린다([UI-R340-SelMod]): 여러 행 선택이 한 행으로 줄지 않게 한다.
            self.apply_state({**state,"selected_id":None})
        def failed(error):
            self._edit_pending=max(0,self._edit_pending-1)
            self.show_reason(f"칸 값을 넣지 못했습니다: {error['message']}")
            self._cell_retry=(oid,field,text if isinstance(text,str) else None)
            self._after_refresh=self.retry_cell
            self._deferred_page=None;self.refresh()
        self.command(action,payload,done,failed)

    def retry_cell(self):
        """거부된 칸 입력을 되살려 다시 편집하게 한다."""
        retry=self._cell_retry;self._cell_retry=None
        if not retry:return
        oid,field,text=retry
        row=next((i for i,r in enumerate(self.model.rows) if r["id"]==oid),None)
        column=next((i for i,(f,_) in enumerate(self.model.columns) if f==field),None)
        if row is None or column is None:return
        index=self.model.index(row,column)
        self.table.selectionModel().setCurrentIndex(index,QItemSelectionModel.SelectionFlag.NoUpdate)
        self._cell_retry_text=text
        self.table.setFocus();self.table.edit(index)

    def _editor_closed(self,*_):
        QTimer.singleShot(0,self._flush_page)

    def _flush_page(self):
        if self._deferred_page and self._edit_pending==0 and self.table.state()!=QAbstractItemView.State.EditingState:
            result,selected=self._deferred_page;self.show_page(result,selected)

    def move_edit(self,row,column,step):
        """[UI-R300-TAB] 다음(이전) 편집 칸으로 옮겨 편집한다. [UI-R300-TABLast] 마지막 행의 마지막 편집 칸에서 Tab은 새 요소를 만든다."""
        columns=len(self.model.columns);count=len(self.model.rows)
        r,c=row,column
        while True:
            c+=step
            if c>=columns:r+=1;c=0
            elif c<0:r-=1;c=columns-1
            if r<0:return
            if r>=count:
                if step>0:self.create_via_tab()
                return
            index=self.model.index(r,c)
            if not self.table.isColumnHidden(c) and self.spec_at(index):break
        model=self.table.selectionModel()
        if r!=row and len(model.selectedRows())<=1:self.table.selectRow(r)
        model.setCurrentIndex(index,QItemSelectionModel.SelectionFlag.NoUpdate)
        self.table.setFocus();self.table.edit(index)

    def create_via_tab(self):
        """[UI-R300-TABLast] DTC·DID 목록은 [UI-R335] 번호로 새 요소를 바로 만들고 그 첫 편집 칸을 편집한다. 그 밖의 목록은 추가 대화상자."""
        if self.state.get("readonly"):return
        if self.kind not in ("dtc","did"):self.add_object(magic=True);return
        kind=self.kind
        def got(data):
            number=data.get("number")
            if number is None:self.show_reason(data.get("number_error") or "새 번호를 찾지 못했습니다.");return
            # 한글 이름이라 Qualifier는 결정 D3(종류 + 번호)로 만든다. 사용자가 이름을 고치면 Qualifier가 따라간다.
            name={"dtc":"새 고장 코드","did":"새 데이터 식별자"}[kind]
            payload={"variant_id":self.variant,"name":name,"qualifier":make_qualifier(name,kind,number)}
            if kind=="dtc":action="create_dtc";payload["code"]=f"0x{number:06X}"
            else:action="create_did";payload.update(identifier=f"0x{number:04X}",members=[],read_service=True)
            def done(state):
                new_id=state.get("selected_id")
                if new_id:self._after_refresh=lambda:self.edit_first_cell(new_id)
                self.apply_state(state)
            self.command(action,payload,done)
        reference=self.reference_number(True)
        self.queue_job(lambda:self.submit("add_defaults",{"kind":kind,"variant":self.variant,"scope":None,"reference":reference},got))

    def edit_first_cell(self,oid):
        row=next((i for i,r in enumerate(self.model.rows) if r["id"]==oid),None)
        if row is None:return
        column=next((c for c in range(len(self.model.columns)) if self.spec_at(self.model.index(row,c))),None)
        if column is None:return
        self.table.selectRow(row)
        index=self.model.index(row,column)
        self.table.selectionModel().setCurrentIndex(index,QItemSelectionModel.SelectionFlag.NoUpdate)
        self.table.setFocus();self.table.edit(index)

    def copy_value_to_selection(self,row,field):
        """[DTCUI-R035] 선택한 DTC에 이 값 복사: 누른 DTC의 그 열 값을 다른 선택 DTC에 넣는다(한 명령)."""
        label=dict(self.model.columns).get(field,field)
        spec,reason=cell_spec(row,field,label,self._record_items,self.kind)
        if not spec:self.show_reason(reason);return
        others=[r for r in self.selected_rows() if r is not row and r.get("kind")=="dtc" and not self.is_local_row(r)]
        if not others:return
        value=editor_text(row,field,spec,self.model.dtc_format)
        self.command("set_dtc_attributes",{"ids":[r["id"] for r in others],"attributes":[attribute_entry(spec,value)]},
                     lambda state:self.apply_state({**state,"selected_id":None}))

    def cycle_focus(self,step=1):
        """F6·Shift+F6: 트리 → 목록 → 선택 객체 창 → 트리 순서로 포커스를 옮긴다(목록 크게 보기에서는 숨긴 창을 건너뛴다)."""
        regions=[w for w in (self.tree,self.table,self.detail_tabs) if w.isVisible()]
        if not regions:return
        focus=self.focusWidget()
        current=next((i for i,w in enumerate(regions) if focus is not None and (w is focus or w.isAncestorOf(focus))),None)
        target=regions[(current+step)%len(regions)] if current is not None else (self.table if self.table.isVisible() else regions[0])
        if target is self.detail_tabs:
            page=self.detail_tabs.currentWidget()
            candidates=[w for w in page.findChildren(QWidget) if w.isVisible() and w.focusPolicy()&Qt.FocusPolicy.TabFocus]
            (candidates[0] if candidates else page).setFocus()
        else:target.setFocus()

    # Stable object paths use project UUID + variant UUID + object UUID. Labels are
    # shown separately, so translated or duplicate names cannot change the target.
    def copy_path(self, oid=None, variant=None):
        if not self.state.get("open") or self.pending:return
        if not isinstance(oid,str):oid=self.selected_ids()[0] if self.selected_ids() else None
        if oid and not oid.startswith("unsupported:"):
            self.submit("object_name_path",{"oid":oid,"variant":variant or self.variant},self.path_copied)
        elif self.node_key:
            self.submit("node_name_path",{"node_key":self.node_key},self.path_copied)

    def copy_stable_path(self, oid=None, variant=None):
        if not self.state.get("open") or self.pending:return
        if not isinstance(oid,str):oid=self.selected_ids()[0] if self.selected_ids() else None
        if oid and not oid.startswith("unsupported:"):
            self.submit("object_path",{"oid":oid,"variant":variant or self.variant},self.stable_path_copied)
        elif self.node_key:
            self.submit("node_path",{"node_key":self.node_key},self.stable_path_copied)

    def path_copied(self,result):
        self.path_entry.setText(result["path"])
        QApplication.clipboard().setText(result["path"])
        self.statusBar().showMessage(("Qualifier 경로" if str(result["path"]).startswith("qpath:") else "이름 경로")+"를 복사했습니다.",4000)

    def copy_display_path(self,oid):
        """목록 오른쪽 버튼 '경로 복사': 설정(표시 탭)의 경로 형식을 따른다(이름 경로 / Qualifier 경로)."""
        if self.pending or not oid:return
        if option(self.settings,"path_display")=="qualifier" and not str(oid).startswith("unsupported:"):
            self.submit("object_qualifier_path",{"oid":oid,"variant":self.variant},self.path_copied)
        else:self.copy_path(oid,self.variant)

    def stable_path_copied(self,result):
        QApplication.clipboard().setText(result["path"])
        self.statusBar().showMessage("안정 UUID 경로를 복사했습니다.",4000)

    def go_path(self,*_):
        """경로 입력(M3-2): Enter·'이동'. 이름 경로(문법 없음, 결정 D7), qpath:, [DID] 같은 선택자, diag:// 안정 경로.
        틀린 경로는 선택을 유지하고 상태 줄·출력 창에 오류를 남긴다(대화상자 없음)."""
        if not (self.state.get("open") and not self.pending):return
        text=self.path_entry.text().strip()
        if not text:return
        self.remember_path(text)
        fail=lambda error,t=text:self.path_failed(t,error)
        if text.lower().startswith("diag://"):self.submit("resolve_path",{"path":text},self.navigate_target,on_error=fail)
        else:self.submit("resolve_name_path",{"path":text},self.name_path_resolved,on_error=fail)

    def path_failed(self,text,error):
        message=f"경로로 이동하지 못했습니다: {text} — {error.get('message','')}"
        self.statusBar().showMessage(message,10000)
        self.log.appendPlainText(message,"오류","경로")
        if hasattr(self,"results_tabs"):self.results_tabs.setCurrentWidget(self.log)

    def remember_path(self,text):
        """[TB-R310] 이번 세션에 넣은 경로(최근 것이 위, 25개)."""
        self._path_history=[text]+[p for p in self._path_history if p!=text][:24]

    def fill_path_history(self):
        self.path_history_menu.clear()
        if not self._path_history:
            empty=self.path_history_menu.addAction("(이번 세션에 넣은 경로 없음)");empty.setEnabled(False);return
        for text in self._path_history:
            self.path_history_menu.addAction(text,self._path_opener(text))

    def _path_opener(self,text):
        return lambda:(self.path_entry.setText(text),self.go_path())

    def name_path_resolved(self,result):
        if result.get("candidates"):
            labels=[c["path"] for c in result["candidates"]]
            choice,ok=QInputDialog.getItem(self,"이름 경로 이동","같은 이름의 항목이 여러 개 있습니다.",labels,0,False)
            if not ok:return
            result=result["candidates"][labels.index(choice)]
        self.navigate_target(result)

    def navigate_target(self,target):
        if not self.select_node(target["key"]):
            QMessageBox.warning(self,"경로 이동","현재 트리에 대상 항목이 없습니다.")
            return
        self.reset_list_view()  # [UI-R100-NavFilter] 대상이 보이게 필터를 푼다
        oid=target.get("id")
        details_id=target.get("details_id") or oid
        self.record_history(target["key"],oid)
        if details_id and not str(details_id).startswith("unsupported:"):
            self._navigation_selection=oid
            self.submit("details",{"oid":details_id},self.navigation_details)
        else:self.refresh()

    def navigation_details(self,record):
        self.show_details(record)
        self.refresh(self._navigation_selection)

    def clear_history(self):
        self.history=[];self.history_pos=-1;self.update_history_buttons()

    def record_history(self,node_key,selected=None):
        if self._history_guard or not node_key:return
        entry={"node_key":node_key,"selected":selected}
        if 0<=self.history_pos<len(self.history) and self.history[self.history_pos]==entry:return
        del self.history[self.history_pos+1:]
        self.history.append(entry)
        if len(self.history)>100:self.history=self.history[-100:]
        self.history_pos=len(self.history)-1
        self.update_history_buttons()

    def update_history_buttons(self):
        enabled=not getattr(self,"pending",False)
        if hasattr(self,"history_back_button"):
            self.history_back_button.setEnabled(enabled and self.history_pos>0)
            self.history_forward_button.setEnabled(enabled and self.history_pos+1<len(self.history))
        if "back" in self.actions:
            self.actions["back"].setEnabled(enabled and self.history_pos>0)
            self.actions["forward"].setEnabled(enabled and self.history_pos+1<len(self.history))

    def history_back(self):
        if self.history_pos<=0:return
        self.history_pos-=1;self.restore_history(self.history[self.history_pos])

    def history_forward(self):
        if self.history_pos+1>=len(self.history):return
        self.history_pos+=1;self.restore_history(self.history[self.history_pos])

    def restore_history(self,entry):
        self._history_guard=True
        try:
            if not self.select_node(entry["node_key"]):
                self.statusBar().showMessage("이전 위치가 더 이상 없습니다.",5000);return
            self.reset_list_view()
            selected=entry.get("selected")
            if selected and not str(selected).startswith("unsupported:"):
                self._navigation_selection=selected
                self.submit("details",{"oid":selected},self.navigation_details)
            else:self.refresh(selected)
        finally:
            self._history_guard=False
        self.update_history_buttons()

    def go_related(self,kind,oid=None):
        if not isinstance(oid,str):oid=self.selected_ids()[0] if self.selected_ids() else None
        if not oid or self.pending:return
        self.submit("related_targets",{"oid":oid,"kind":kind},self.choose_related)

    def choose_related(self,rows):
        if not rows:
            self.statusBar().showMessage("연결된 객체가 없습니다.",5000);return
        if len(rows)==1:chosen=rows[0]
        else:
            labels=[f"{r['name']} · {r['qualifier']} · {r['id']}" for r in rows]
            choice,ok=QInputDialog.getItem(self,"관련 객체로 이동","대상",labels,0,False)
            if not ok:return
            chosen=rows[labels.index(choice)]
        self.submit("locate_object",{"oid":chosen["id"],"variant":self.variant},self.navigate_target)

    def open_qualifier_overview(self,shortcut):
        self.submit("qualifier_overview",{"shortcut":shortcut},self.show_qualifier_overview)

    def open_shortcut_matrix(self):
        self.submit("shortcut_matrix",{},self.show_shortcut_matrix)

    def show_shortcut_matrix(self,result):
        """Shortcut Qualifier 개요(U4): 행 = shortcut, 열 = 변형(예/아니요). 두 번 누른 행으로 이동한다."""
        dialog=ShortcutMatrixDialog(result,self,self.variant)
        if dialog.exec()==QDialog.DialogCode.Accepted and dialog.result_row:
            row=dialog.result_row
            self.submit("locate_object",{"oid":row["id"],"variant":row.get("variant")},self.navigate_target)

    def open_compact_tree(self,variant=None):
        variants=self.state.get("variants",[])
        if not variants:return
        if not isinstance(variant,str):
            options=[f"{v['name']} · {v['id']}" for v in variants]
            current=next((i for i,v in enumerate(variants) if v["id"]==self.variant),0)
            choice,ok=QInputDialog.getItem(self,"변형 Compact View","변형 (기본 변형과 공유하지 않는 인스턴스를 봅니다)",options,current,False)
            if not ok:return
            variant=variants[options.index(choice)]["id"]
        self.submit("compact_tree",{"variant":variant},self.show_compact_tree)

    def show_compact_tree(self,result):
        dialog=CompactTreeDialog(result,self)
        if dialog.exec()==QDialog.DialogCode.Accepted and dialog.result_row:
            row=dialog.result_row
            self.submit("locate_object",{"oid":row["id"],"variant":row.get("variant")},self.navigate_target)

    def show_qualifier_overview(self,result):
        if not result.get("shortcut"):
            self.show_qualifier_editor(result);return
        shortcut=result["shortcut"]
        title="Shortcut Qualifier 개요" if shortcut else "Qualifier 개요"
        rows=result["rows"]
        columns=[("kind","종류"),("name","이름"),("qualifier","Shortcut Qualifier" if shortcut else "Qualifier"),("breadcrumb","경로"),("path","객체 경로")]
        hint="원본 CDD에서 명시된 SHORTCUTQUAL만 표시합니다. 행을 두 번 클릭하면 이동합니다." if shortcut else "행을 두 번 클릭하면 객체로 이동합니다. 공유 객체는 경로의 변형으로 구분합니다."
        dialog=OverviewDialog(title,rows,columns,self,hint)
        if dialog.exec()==QDialog.DialogCode.Accepted and dialog.result_row:
            row=dialog.result_row
            self.submit("locate_object",{"oid":row["id"],"variant":row["variant"]},self.navigate_target)

    def show_qualifier_editor(self,result):
        """Qualifier 개요 편집·다시 만들기(M6-5). 바꾼 값은 확인·적용 때 한 명령(set_qualifiers)으로 반영한다."""
        variant_name=next((v["name"] for v in self.state.get("variants",[]) if v["id"]==self.variant),"")
        dialog=QualifierOverviewDialog(result,self,self.variant,variant_name,readonly=bool(self.state.get("readonly")))
        dialog.apply_requested.connect(lambda items:self.apply_qualifiers(dialog,items))
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        items=dialog.pending_items();row=dialog.result_row
        def run():
            locate=(lambda:self.submit("locate_object",{"oid":row["id"],"variant":row.get("variant")},self.navigate_target)) if row else None
            if items:
                self._after_refresh=locate
                self.command("set_qualifiers",{"items":items})
            elif locate:locate()
        self.when_idle(run)

    def apply_qualifiers(self,dialog,items):
        if not items or self.pending or self.state.get("readonly"):return
        self._busy_dialog=dialog;dialog.set_busy(True)
        def done(state):
            dialog.mark_applied();self.apply_state(state)
        self.submit("execute_command",{"action":"set_qualifiers","payload":{"items":items},"expected_revision":self.state["revision"],"command_id":str(uuid.uuid4())},done)

    def open_variant_view(self,method):
        variants=self.state.get("variants",[])
        if len(variants)<2:
            QMessageBox.information(self,"변형 비교","비교하려면 변형이 두 개 이상 필요합니다.");return
        options=[f"{v['name']} · {v['id']}" for v in variants]
        left,ok=QInputDialog.getItem(self,"변형 선택","첫 번째 변형",options,0,False)
        if not ok:return
        right,ok=QInputDialog.getItem(self,"변형 선택","두 번째 변형",options,1,False)
        if not ok:return
        self.submit(method,{"left":variants[options.index(left)]["id"],"right":variants[options.index(right)]["id"]},lambda result:self.show_explorer(method,result))

    # ------------------------------------------------------------------ 비교 보기(M6-6)
    def open_compare(self,file_mode=False):
        """비교 창(비모달). 양쪽을 한 창에서 고르고, 이동해도 창은 열어 둔다([CMP-R100])."""
        if not self.state.get("open"):return
        dialog=getattr(self,"compare_dialog",None)
        if dialog is None:
            dialog=self.compare_dialog=CompareDialog(self.state.get("variants",[]),self.variant,self)
            dialog.compare_requested.connect(self.run_compare)
            dialog.file_requested.connect(self.choose_compare_file)
            dialog.navigate_requested.connect(lambda oid,vid:self.when_idle(lambda:self.submit("locate_object",{"oid":oid,"variant":vid},self.navigate_target)))
        else:dialog.set_variants(self.state.get("variants",[]))
        dialog.show();dialog.raise_();dialog.activateWindow()
        if file_mode:dialog.choose_file()

    def run_compare(self,args):
        dialog=self.compare_dialog
        self.when_idle(lambda:self.submit("compare_view",args,dialog.set_result,on_error=dialog.failed))

    def choose_compare_file(self):
        dialog=self.compare_dialog
        path,_=QFileDialog.getOpenFileName(dialog,"비교할 저장 프로젝트 선택",self.state.get("path") or "","진단 프로젝트 (*.diagproj)")
        if not path:return
        self.when_idle(lambda:self.submit("compare_file_variants",{"path":path},dialog.set_file,on_error=dialog.failed))

    def open_file_compare(self):
        path,_=QFileDialog.getOpenFileName(self,"비교할 저장 프로젝트 선택",self.state.get("path") or "","진단 프로젝트 (*.diagproj)")
        if path:self.submit("compare_file_variants",{"path":path},self.choose_file_compare_variants)

    def choose_file_compare_variants(self,project):
        current=self.state.get("variants",[])
        saved=project["variants"]
        if not current or not saved:
            QMessageBox.warning(self,"프로젝트 비교","양쪽 프로젝트에 변형이 필요합니다.");return
        left_options=[f"{v['name']} · {v['id']}" for v in current]
        left_default=next((i for i,v in enumerate(current) if v["id"]==self.variant),0)
        left,ok=QInputDialog.getItem(self,"프로젝트 비교","현재 작업본의 변형",left_options,left_default,False)
        if not ok:return
        right_options=[f"{v['name']} · {v['id']}" for v in saved]
        right,ok=QInputDialog.getItem(self,"프로젝트 비교","저장된 파일의 변형",right_options,0,False)
        if not ok:return
        self.submit("compare_project_file",{"path":project["path"],"left_variant":current[left_options.index(left)]["id"],
                                            "right_variant":saved[right_options.index(right)]["id"],
                                            "expected_digest":project["digest"]},self.show_file_compare)

    def show_file_compare(self,result):
        title=f"프로젝트 비교 · {result['left']} / {result['right']}"
        columns=[("category","종류"),("code","코드"),("scope","범위"),("left","현재 작업본"),
                 ("right","저장된 파일"),("relation","차이")]
        hint="DID·DTC는 범위와 코드, 인스턴스는 클래스·인스턴스 Qualifier로 대응시킵니다. DID 구조, 서비스 헤더·주소·NRC·전문 구조·상태 조건/전이, 원본 변형의 차이를 표시합니다. 설명·번역·사용자 속성은 비교하지 않습니다. 저장된 파일은 변경하지 않습니다."
        dialog=OverviewDialog(title,result["rows"],columns,self,hint)
        if dialog.exec()==QDialog.DialogCode.Accepted and dialog.result_row and dialog.result_row.get("id"):
            row=dialog.result_row
            self.submit("locate_object",{"oid":row["id"],"variant":row["variant"]},self.navigate_target)

    def open_attribute_overview(self):
        option,ok=QInputDialog.getItem(self,"Attribute 개요","범위",["프로젝트 전체","현재 변형"],0,False)
        if not ok:return
        self.submit("attribute_overview",{"scope_variant":self.variant if option=="현재 변형" else None},lambda result:self.show_explorer("attribute_overview",result))

    def open_explorer(self,method):
        args={"variant":self.variant} if method=="state_graph" and self.variant else {}  # M6-7: 상태 그래프는 현재 변형의 서비스로 연다
        self.submit(method,args,lambda result:self.show_explorer(method,result))

    def show_explorer(self,method,result):
        if method=="state_graph":
            dialog=StateGraphDialog(result,self)
            # [STUI-R200] 변형을 바꾸면 그 변형의 서비스로 다시 조회한다(모든 변형 = "").
            dialog.variant_requested.connect(lambda vid:self.when_idle(lambda:self.submit("state_graph",{"variant":vid or None},dialog.set_graph)))
            dialog.exec();return
        if method=="attribute_overview":
            title="Attribute 개요"
            columns=[("kind","종류"),("name","객체"),("key","속성"),("value_type","타입"),("value_text","값"),("path","객체 경로")]
            hint="원본과 사용자 속성을 읽기 전용으로 표시합니다. 경로가 있는 행은 두 번 클릭해 이동합니다."
        elif method=="translation_overview":
            title="번역 표"
            columns=[("kind","종류"),("name","객체"),("field","필드"),("locale","언어"),("value","저장된 번역"),("path","객체 경로")]
            hint="이 표에는 저장된 번역만 표시됩니다. 편집 가능한 객체의 행을 두 번 클릭하면 값을 수정합니다."
        elif method=="compact_view":
            title=f"Compact View · {result['left']} / {result['right']}"
            columns=[("class","클래스"),("name","인스턴스"),("qualifier","Qualifier"),("left",result["left"]),("right",result["right"]),("relation","관계")]
            hint="같은 객체 ID의 원본·참조 관계를 표시합니다. 이름 일치만으로 공유 여부를 판단하지 않습니다."
        else:
            title=f"Compare · {result['left']} / {result['right']}"
            columns=[("category","종류"),("code","코드"),("name","객체"),("left",result["left"]),("right",result["right"]),("relation","차이")]
            hint="같은 프로젝트의 두 변형을 비교합니다. DID·DTC 코드가 같아도 객체 ID가 다르면 차이로 표시합니다."
        dialog=OverviewDialog(title,result["rows"],columns,self,hint)
        if method=="attribute_overview" and not self.state.get("readonly"):
            bulk=QPushButton("선택 객체에 사용자 속성 일괄 설정")
            bulk.clicked.connect(lambda:self.prepare_bulk_attributes(dialog))
            dialog.layout().insertWidget(dialog.layout().count()-1,bulk)
        if dialog.exec()!=QDialog.DialogCode.Accepted or not dialog.result_row:return
        row=dialog.result_row
        if row.get("bulk_attributes"):
            self.command("set_attributes",row["bulk_attributes"])
            return
        if method=="translation_overview":
            if self.state.get("readonly"):
                self.statusBar().showMessage("읽기 전용 프로젝트의 번역은 수정할 수 없습니다.",5000);return
            value,ok=QInputDialog.getMultiLineText(self,"번역 수정",f"{row['name']} · {row['field']} · {row['locale']}",row["value"])
            if ok:self.command("set_translation",{"id":row["id"],"field":row["field"],"locale":row["locale"],"value":value})
        elif row.get("path") or method in ("compact_view","compare_variants"):
            self.submit("locate_object",{"oid":row["id"],"variant":row.get("variant") or self.variant},self.navigate_target)

    def prepare_bulk_attributes(self,dialog):
        indices=sorted({index.row() for index in dialog.table.selectionModel().selectedRows()})
        ids=list(dict.fromkeys(dialog.rows[dialog.table.item(index,0).data(Qt.ItemDataRole.UserRole)]["id"] for index in indices))
        if not ids:
            QMessageBox.information(dialog,"속성 일괄 설정","하나 이상의 행을 선택하세요.");return
        key_text,ok=QInputDialog.getText(dialog,"속성 일괄 설정","사용자 속성 키 (user.로 시작)")
        if not ok:return
        value,ok=QInputDialog.getMultiLineText(dialog,"속성 일괄 설정",f"{len(ids)}개 객체에 적용할 텍스트 값")
        if not ok:return
        dialog.result_row={"bulk_attributes":{"ids":ids,"key":key_text,"value":value}}
        dialog.accept()

    def add_translation(self):
        oid=self.selected_ids()[0] if self.selected_ids() else None
        if not oid or str(oid).startswith("unsupported:"):return
        field,ok=QInputDialog.getItem(self,"번역 추가·수정","필드",["name","description"],0,False)
        if not ok:return
        locale,ok=QInputDialog.getItem(self,"번역 추가·수정","언어",["ko-KR","en-US","de-DE","fr-FR","ja"],0,True)
        if not ok:return
        value,ok=QInputDialog.getMultiLineText(self,"번역 추가·수정",f"{field} · {locale}")
        if ok:self.command("set_translation",{"id":oid,"field":field,"locale":locale,"value":value})

    def tree_menu(self,pos):
        # 선택 조회 같은 짧은 조회 중에도 메뉴는 연다(25차: 스모크의 오른쪽 버튼이 조회와 겹쳐 버려짐). 긴 작업 중에는 막는다.
        if (self.pending and self._pending_method not in QUIET_METHODS) or not self.state.get("open"):return
        index=self.tree.indexAt(pos)
        if not index.isValid():return
        data=index.data(Qt.ItemDataRole.UserRole)
        menu=QMenu(self)
        menu.addAction("항목 열기",lambda:self._open_tree_context(data))
        menu.addAction("이름 경로 복사",lambda:self.submit("node_name_path",{"node_key":data["key"]},self.path_copied))
        menu.addAction("안정 경로 복사",lambda:self.submit("node_path",{"node_key":data["key"]},self.stable_path_copied))
        # 표와 같은 명령 집합(U6·P2): 변형 노드는 Compact View·ODX 사전 검사·Attribute 개요·속성, 현재 목록 노드는 새로 만들기
        writable=not self.state.get("readonly")
        key=data["key"];vid=data.get("variant")
        if key.startswith("var:") and key.count(":")==1 and vid:
            menu.addSeparator()
            menu.addAction("Compact View…",lambda:self.open_compact_tree(vid))
            menu.addAction("이 변형의 ODX 사전 검사",lambda:self.submit("validate_project",{"variants":[vid]},self.show_validation))
            menu.addAction("Attribute 개요 (이 변형)…",lambda:self.submit("attribute_overview",{"scope_variant":vid},lambda result:self.show_explorer("attribute_overview",result)))
            if writable:menu.addAction("변형 속성 편집…",lambda:self.inspect_edit(vid))
        if key==self.node_key and self.new_row_text():
            menu.addSeparator();menu.addAction("새로 만들기…",self.add_object)
        if data.get("kind")=="service" and data.get("scope") and key.startswith("var:") and ":inst:" in key:
            menu.addSeparator()
            menu.addAction("고장 메모리 레코드…",lambda:self.submit("fault_memory_view",{"oid":data["scope"],"variant":vid or self.variant},lambda result:FaultMemoryDialog(result,self,self.dtc_format.currentData() or "hex").exec()))
            if writable:menu.addAction("현재 변형에서 인스턴스를 사본으로 바꾸기",lambda:self.command("detach_instance",{"id":data["scope"],"variant_id":vid or self.variant}))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _open_tree_context(self,data):
        if self.select_node(data["key"]):self.page=0;self.refresh();self.record_history(data["key"],None)

    def table_menu(self,pos):
        # 선택 조회 같은 짧은 조회 중에도 메뉴는 연다(25차: 스모크의 오른쪽 버튼이 조회와 겹쳐 버려짐). 긴 작업 중에는 막는다.
        if (self.pending and self._pending_method not in QUIET_METHODS) or not self.state.get("open"):return
        # [UI-R350-Element] 오른쪽 버튼은 누른 요소로 포커스를 옮기고 그 요소의 메뉴를 보인다. 이미 선택된 행이면 여러 행 선택을 그대로 두고,
        # 새 행이면 그 행을 골라 선택 객체 창도 바꾼다. [UI-R350-Empty] 빈 곳은 선택 없이 쓰는 메뉴(새로 만들기·붙여넣기·표 복사).
        index=self.table.indexAt(pos)
        writable=not self.state.get("readonly")
        menu=QMenu(self)
        if not index.isValid():
            if self.new_row_text():menu.addAction("새로 만들기…",self.add_object)
            if self.kind=="dtc" and writable:menu.addAction("DTC 표 붙여넣기…",self.paste_dtcs)
            menu.addAction("표 복사",self.copy_table)
            menu.exec(self.table.viewport().mapToGlobal(pos));return
        if self.model.is_new_row(index.row()):
            menu.addAction("새로 만들기…",lambda:self.add_object(magic=True));menu.exec(self.table.viewport().mapToGlobal(pos));return
        selection=self.table.selectionModel()
        if index.row() in {i.row() for i in selection.selectedRows()}:
            selection.setCurrentIndex(index,QItemSelectionModel.SelectionFlag.NoUpdate)
        else:
            selection.setCurrentIndex(index,QItemSelectionModel.SelectionFlag.ClearAndSelect|QItemSelectionModel.SelectionFlag.Rows)
        row=self.model.rows[index.row()]
        oid=row["id"];field,label=self.model.columns[index.column()]
        if self.is_local_row(row):
            menu.addAction("행 값 보기",lambda:self.show_local_details(row))
            if row.get("kind")=="dependency_group":
                menu.addAction("펼치기 / 접기",lambda:self.toggle_dependency_group(row))
                if writable:menu.addAction("묶음의 상태 조건 일괄 변경…",self.bulk_conditions)
            menu.addAction("표 복사",self.copy_table)
            menu.exec(self.table.viewport().mapToGlobal(pos));return
        spec,_=cell_spec(row,field,label,self._record_items,self.kind)
        if spec and writable:
            edit=menu.addAction(f"'{label}' 칸 편집",lambda:(self.table.setCurrentIndex(index),self.edit_cell()));edit.setShortcut(QKeySequence("F2"))
        # [DTCUI-R035] 여러 DTC를 고르고, 레코드 항목 열(코드·이름이 아닌 열)이며 값을 여러 DTC가 같이 가질 수 있을 때만 켠다.
        dtcs=[r for r in self.selected_rows() if r.get("kind")=="dtc" and not self.is_local_row(r)]
        if self.kind=="dtc" and field.startswith("attr:"):
            copy_value=menu.addAction("선택한 DTC에 이 값 복사",lambda:self.copy_value_to_selection(row,field))
            copy_value.setEnabled(bool(spec) and writable and len(dtcs)>1)
            if len(dtcs)<=1:copy_value.setToolTip("DTC를 둘 이상 고르면 켜집니다.")
        menu.addAction("속성 보기",lambda:self.submit("details",{"oid":oid},self.show_details))
        if row.get("kind") in EDITABLE and writable and not row.get("read_only") and row.get("supported",1):
            action=menu.addAction("속성…",lambda:self.inspect_edit(oid));action.setShortcut(QKeySequence("Alt+Return"))
        if not str(oid).startswith("unsupported:"):
            menu.addAction("경로 복사",lambda:self.copy_display_path(oid))
            menu.addAction("참조 영향…",lambda:self.open_reference_impact(oid))
            if row.get("kind")=="data_type" and not self.state.get("readonly"):
                menu.addAction("독립 복사 만들기…",lambda:self.copy_selected_type(oid))
                menu.addAction("데이터 타입 바꾸기…",lambda:self.replace_selected_type(oid))
            if row.get("kind") in ("service","diagnostic_instance"):menu.addAction("관련 DID로 이동",lambda:self.go_related("did",oid))
            if row.get("kind")=="did":menu.addAction("관련 데이터 타입으로 이동",lambda:self.go_related("data_type",oid))
            if row.get("kind")=="data_type":menu.addAction("관련 DID로 이동",lambda:self.go_related("did",oid))
            if row.get("kind")=="nrc":menu.addAction("사용 서비스로 이동",lambda:self.go_related("service",oid))
            if row.get("protocol_id"):menu.addAction("프로토콜 서비스로 이동",lambda:self.submit("locate_object",{"oid":row["protocol_id"]},self.navigate_target))
        # 표와 트리의 명령 집합을 맞춘다(U6·P2): 새로 만들기·삭제·표 복사·일괄 변경·사본 교체·고장 메모리
        menu.addSeparator()
        if self.new_row_text():menu.addAction("새로 만들기…",self.add_object)
        if writable and row.get("kind") in EDITABLE and self.kind not in READONLY_REASON and not str(oid).startswith("unsupported:"):menu.addAction("삭제…",self.delete_object)
        if writable and row.get("kind") in ("service","diagnostic_instance") and (row.get("shared") or 0)>1:
            menu.addAction("현재 변형에서 인스턴스를 사본으로 바꾸기",lambda:self.command("detach_instance",{"id":oid,"variant_id":self.variant}))
        if row.get("kind")=="diagnostic_instance":menu.addAction("고장 메모리 레코드…",self.open_fault_memory)
        if self.kind=="dependency" and writable:menu.addAction("상태 조건 일괄 변경…",self.bulk_conditions)
        menu.addAction("표 복사",self.copy_table)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def maybe_leave(self,continuation):
        if self.pending:return
        if self.state.get("dirty"):
            result=QMessageBox.question(self,"변경 사항 저장","현재 프로젝트의 변경 사항을 저장하시겠습니까?",QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Save)
            if result==QMessageBox.StandardButton.Cancel:return
            if result==QMessageBox.StandardButton.Save:self.save(after=continuation);return
        continuation()

    def new_project(self):
        def start():
            dialog=ProjectDialog(self)
            if dialog.exec()==QDialog.DialogCode.Accepted:self.page=0;self.kind="did";self.node_key=None;self.variant=None;self.submit("new_project",dialog.values(),self.apply_state)
        self.maybe_leave(start)

    def open_project(self):
        def start():
            path,_=QFileDialog.getOpenFileName(self,"프로젝트 열기","",OPEN_FILTER)
            if not path:return
            self.open_path_now(path)
        self.maybe_leave(start)

    def open_path_now(self,path):
        """.diagproj는 열고, CDD는 직접 열지 않고 가져오기 미리보기를 거쳐 새 작업 프로젝트로 만든다."""
        self.page=0
        if is_cdd_path(path):
            self._import_path=str(path);self.submit("cdd_preview",{"path":path},self.cdd_ready)
        else:self.submit("open_project",{"path":path},self.apply_state)

    def open_path(self,path):
        if self.pending or not path:return
        self.maybe_leave(lambda:self.open_path_now(path))

    def save(self,checked=False,as_new=False,after=None):
        path=None if as_new else self.state.get("path")
        if not path:path,_=QFileDialog.getSaveFileName(self,"프로젝트 저장",self.state.get("name","Project")+".diagproj","진단 프로젝트 (*.diagproj)")
        if not path:return
        def saved(state):
            if after:self.state=state;after()
            else:self.apply_state(state)
        self.submit("save_project",{"path":path},saved)

    def recover(self):self.maybe_leave(lambda:self.submit("recoveries",{},self.recovery_choices))
    def recovery_choices(self,rows):
        if not rows:
            if self.state.get("open"):self.statusBar().showMessage("복구 가능한 작업본이 없습니다.",4000)
            return
        display=[]
        for r in rows:
            source=r.get("source");integrity=r.get("integrity","확인 불가")+(f" · v{r['schema_version']}" if r.get("schema_version") else "")
            state="저장 전" if not source else "원본 없음" if r.get("source_changed") is None else "원본 변경됨" if r.get("source_changed") else "원본 그대로"
            display.append({"updated_at":r.get("updated_at",""),"saved_at":r.get("saved_at") or "—","source":source or "저장 전","integrity":integrity,"source_state":state,"session_id":r["session_id"]})
        dialog=RecoveryDialog(display,self)
        if dialog.exec()==QDialog.DialogCode.Accepted and dialog.result_row:
            row=dialog.result_row
            if row.get("action")=="quarantine":
                text=f"작업본을 복구 목록에서 빼서 격리 폴더로 옮깁니다. 파일은 삭제하지 않습니다.\n\n갱신 시각: {row['updated_at']}\n원본: {row['source']}\n무결성: {row['integrity']}"
                if QMessageBox.question(self,"작업본 격리",text)==QMessageBox.StandardButton.Yes:
                    self.submit("quarantine_recovery",{"session_id":row["session_id"]},self.recovery_quarantined)
                return
            self.submit("open_project",{"recovery":row["session_id"]},self.apply_state)

    def recovery_quarantined(self,result):
        message=f"작업본을 격리했습니다: {result['path']}"
        self.statusBar().showMessage(message,10000);self.log.appendPlainText(message,"정보","복구")
        self.submit("recoveries",{},self.recovery_choices)

    def command(self,action,payload,callback=None,on_error=None):
        """편집 명령. 작업 중이면 줄에 세워 두었다가 끝난 뒤 순서대로 보낸다(선택 조회 중의 Ctrl+Z, 칸 편집을 빠르게 이어 확정한 경우 등).
        expected_revision은 보낼 때의 revision이다. callback이 없으면 apply_state."""
        self.queue_job(lambda:self.submit("execute_command",{"action":action,"payload":payload,"expected_revision":self.state["revision"],"command_id":str(uuid.uuid4())},
                                          callback or self.apply_state,on_error))

    def queue_job(self,job):
        """작업 줄: 작업 중이면 끝난 뒤 순서대로 보낸다. job은 submit을 부르는 함수다."""
        self._command_queue.append(job);self._drain_commands()

    def _drain_commands(self):
        """줄의 일을 순서대로 보낸다. 작업 스레드에 보내지 않은 일(조건이 바뀌어 건너뜀)이면 다음 일을 이어 보낸다."""
        while not self.pending and self._command_queue:
            if not self.state.get("open"):self._command_queue=[];return
            self._command_queue.pop(0)()

    def add_object(self,kind=None,magic=False):
        if not isinstance(kind,str):kind=self.kind
        if kind in READONLY_REASON:
            QMessageBox.information(self,"추가",READONLY_REASON[kind]+"\n추가할 수 없습니다.");return
        if kind in ("unsupported","ecu","state"):return
        kind=ADD_KIND.get(kind,kind)
        if kind=="variant":self.open_variant_wizard();return  # M6-4: 새 변형은 변형 마법사로 만든다
        # [UI-R335] 새 번호 제안: 기준 행(R)은 새 항목 행이면 마지막 보이는 행, 첫 행을 골랐으면 그 행, 아니면 첫 선택 행 바로 위 행.
        reference=self.reference_number(magic) if kind==ADD_KIND.get(self.kind,self.kind) else None
        self.queue_job(lambda:self.submit("add_defaults",{"kind":kind,"variant":self.variant,"scope":None,"reference":reference},
                    lambda data:self.object_dialog(kind,data["catalog"],defaults={"number":data.get("number"),"number_error":data.get("number_error")})))

    def reference_number(self,magic=False):
        """[UI-R335] 새 번호의 기준 번호 R. 목록이 비면 None(첫 번호), 필터로 보이는 행이 없으면 'max'(가장 큰 번호의 객체)."""
        field={"dtc":"code","did":"identifier","did_overview":"identifier","dtc_overview":"code"}.get(self.kind)
        if not field:return None
        rows=[row for row in self.model.rows if isinstance(row.get(field),int)]
        if not rows:return "max" if self._unfiltered else None
        if magic:reference=rows[-1]
        else:
            selected=sorted(i.row() for i in self.table.selectionModel().selectedRows() if i.row()<len(self.model.rows))
            if not selected:reference=rows[-1]
            elif selected[0]==0:reference=self.model.rows[0]
            else:reference=self.model.rows[selected[0]-1]
        value=reference.get(field)
        return value if isinstance(value,int) else None

    def object_dialog(self,kind,catalog,record=None,focus_field=None,defaults=None):
        # 서비스 NRC는 자유 입력 대신 NRC 카탈로그에서 고른다.
        if kind=="service":
            self.submit("query_objects",{"kind":"nrc","page_size":1000},lambda result:self.open_object_dialog(kind,catalog,record,focus_field,result["rows"],defaults))
        else:
            self.open_object_dialog(kind,catalog,record,focus_field,None,defaults)

    def neighbours(self,oid):
        """편집 대화상자 앞뒤 이동(U6) 대상: 현재 표의 이전·다음 객체 행."""
        ids=[row["id"] for row in self.model.rows if not self.is_local_row(row) and not str(row["id"]).startswith("unsupported:")]
        if oid not in ids:return (None,None)
        i=ids.index(oid)
        return (ids[i-1] if i>0 else None,ids[i+1] if i+1<len(ids) else None)

    def open_object_dialog(self,kind,catalog,record=None,focus_field=None,nrcs=None,added=None):
        previous,following=self.neighbours(record["id"]) if record and record.get("id") else (None,None)
        siblings=None
        if kind=="dtc" and record and record.get("id"):
            # M2-3: DTC 대화상자 위쪽 콤보의 대상 = 지금 목록의 DTC 행
            siblings=[(row["id"],(self.model.code_text(row,row["code"])+" " if isinstance(row.get("code"),int) else "")+str(row.get("name",""))) for row in self.model.rows if row.get("kind")=="dtc"]
        defaults={"byte_order":option(self.settings,"new_type_byte_order"),"hex_zero_fill":option(self.settings,"hex_zero_fill"),**(added or {})}
        if added and added.get("number_error"):self.show_reason(f"새 번호를 찾지 못했습니다: {added['number_error']}")
        dialog=ObjectDialog(kind,catalog,record,self.variant,self,focus_field,nrcs,navigation=(previous is not None,following is not None),siblings=siblings,defaults=defaults)
        actions={"did":"did","dtc":"dtc","data_type":"type","service":"service","variant":"variant","state":"state"}
        if record and kind in ("ecu","variant","diagnostic_instance"):action="update_common"
        elif record and kind=="state":action="update_state"
        else:action=("update_" if record else "create_")+actions[kind]
        if record and record.get("id"):dialog.apply_handler=lambda d,values:self.dialog_apply(d,action,values)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        target=dialog.navigate_id or (previous if dialog.navigate<0 else following if dialog.navigate>0 else None)
        changed=dialog.changed()
        if target and not changed:
            # 바꾼 값이 없으면 저장 없이 이웃 객체의 편집 대화상자를 연다.
            self.when_idle(lambda:self.inspect_edit(target));return
        if dialog.applied and not changed:return  # 적용한 뒤 더 바꾸지 않았다
        values=dialog.values()
        def run():
            if target:self._after_refresh=lambda:self.inspect_edit(target)
            self.command(action,values)
        self.when_idle(run)

    def dialog_apply(self,dialog,action,values):
        """대화상자의 적용(닫지 않음, M2-4): 명령을 보내고 끝나면 대화상자의 처음 값을 지금 값으로 삼는다."""
        if self.pending:
            self.statusBar().showMessage("작업이 끝난 뒤 다시 적용하세요.",4000);return
        if not dialog.changed():
            self.statusBar().showMessage("바꾼 값이 없습니다.",3000);return
        self._busy_dialog=dialog;dialog.set_busy(True)
        def done(state):
            dialog.mark_applied();self.apply_state(state)
        self.submit("execute_command",{"action":action,"payload":values,"expected_revision":self.state["revision"],"command_id":str(uuid.uuid4())},done)

    def edit_object(self,*_):
        rows=self.selected_rows()
        if not rows:return
        if rows[0].get("kind")=="dependency_group" and len(rows)==1:self.toggle_dependency_group(rows[0]);return
        if self.kind=="dependency" and len(rows)>1:self.bulk_conditions();return
        if self.is_local_row(rows[0]):
            QMessageBox.information(self,"읽기 전용",READONLY_REASON.get(self.kind) or "이 행은 객체가 아니어서 편집할 수 없습니다.");return
        self.inspect_edit(rows[0]["id"])

    def inspect_edit(self,oid):
        if not oid or self.pending:return
        self._inspect_field=self._focus_field if oid==self._focus_oid else ""
        self.submit("inspect",{"oid":oid},self.edit_inspected)

    def edit_inspected(self,data):
        record=data["details"]
        if record["kind"]=="unsupported":ReportDialog("보존된 CDD 원본 XML",record["raw_xml"],self).exec();return
        if record["kind"] in READONLY_REASON:QMessageBox.information(self,"읽기 전용",READONLY_REASON[record["kind"]]);return
        if self.state.get("readonly") or record.get("read_only") or not record.get("supported",1):QMessageBox.information(self,"읽기 전용","이 객체는 편집할 수 없습니다.");return
        if record["kind"] not in EDITABLE and record["kind"]!="diagnostic_instance":QMessageBox.information(self,"편집",f"{KINDS.get(record['kind'],record['kind'])}은(는) 아직 이 창에서 편집할 수 없습니다. 하위 서비스를 선택해 편집하세요.");return
        if record["kind"]=="data_type" and data.get("impact",{}).get("rows"):
            dialog=ReferenceImpactDialog(data["impact"],self,"edit")
            if dialog.exec()!=QDialog.DialogCode.Accepted:return
            chosen=dialog.result_row or {}
            if chosen.get("action")=="copy":self.prepare_type_copy(record);return
            if chosen.get("action")!="edit":self.navigate_reference(chosen);return
        if record["kind"] in ("did","service") and len(record.get("variants",[]))>1:
            # U3: CANdela "referenced in multiple variants" — 참조하는 모든 변형 수정 / 이 변형에서 사본으로 바꾼 뒤 수정
            what="DID" if record["kind"]=="did" else "진단 인스턴스(서비스)"
            box=QMessageBox(self);box.setWindowTitle(f"공유 {what} 편집")
            box.setText(f"이 {what}를 여러 변형이 참조합니다: "+", ".join(record["variants"])+"\n\n어떻게 수정할까요?")
            box.setInformativeText("사본으로 바꾸면 현재 변형만 새 객체를 쓰고 다른 변형은 원래 객체를 그대로 씁니다. 실행 취소할 수 있습니다."
                                   +("" if record["kind"]=="did" else "\n인스턴스 사본은 DID를 그대로 공유합니다. 보존 원문·원문 배치·고장 메모리 레코드가 연결된 인스턴스는 사본으로 바꿀 수 없습니다."))
            copy=box.addButton("이 변형에서 사본으로 바꾼 뒤 수정",QMessageBox.ButtonRole.AcceptRole)
            all_=box.addButton("참조하는 모든 변형 수정",QMessageBox.ButtonRole.ActionRole)
            box.addButton("취소",QMessageBox.ButtonRole.RejectRole);box.setDefaultButton(copy);box.exec()
            if box.clickedButton()==copy:
                if record["kind"]=="did":
                    self.submit("execute_command",{"action":"detach_did","payload":{"id":record["id"],"variant_id":self.variant},"expected_revision":self.state["revision"],"command_id":str(uuid.uuid4())},lambda state:self.detached_edit(state))
                else:self.detach_and_edit(record["id"])
                return
            if box.clickedButton()!=all_:return
        elif len(record.get("variants",[]))>1:
            if QMessageBox.question(self,"공유 객체 수정","다음 변형에 모두 반영됩니다:\n"+", ".join(record["variants"]))!=QMessageBox.StandardButton.Yes:return
        self.object_dialog(record["kind"],data["catalog"],record,self._inspect_field or "")
        self._inspect_field=""

    def detached_edit(self,state):
        self.state=state
        self.update_enabled();self.update_validation_header()
        self.inspect_edit(state["selected_id"])

    def detach(self):
        ids=self.selected_ids()
        if self.kind=="did" and ids:self.command("detach_did",{"id":ids[0],"variant_id":self.variant})

    def detach_and_edit(self,oid):
        """U3: 공유 인스턴스를 이 변형에서 사본으로 바꾸고, 목록이 갱신되면 사본 서비스의 편집 대화상자를 연다."""
        def done(state):
            new_id=state.get("selected_id")
            if new_id:self._after_refresh=lambda:self.inspect_edit(new_id)
            self.apply_state(state)
        self.submit("execute_command",{"action":"detach_instance","payload":{"id":oid,"variant_id":self.variant},"expected_revision":self.state["revision"],"command_id":str(uuid.uuid4())},done)

    def detach_instance(self):
        """편집 메뉴: 선택한 서비스·인스턴스(또는 열린 인스턴스 페이지)를 이 변형에서 사본으로 바꾼다(U3)."""
        rows=[row for row in self.selected_rows() if not self.is_local_row(row)]
        oid=rows[0]["id"] if rows and rows[0].get("kind") in ("service","diagnostic_instance") else self.scope if self.kind=="service" and self.scope else None
        if not oid:
            QMessageBox.information(self,"인스턴스 사본","서비스나 진단 인스턴스를 고르세요.");return
        self.command("detach_instance",{"id":oid,"variant_id":self.variant})

    def current_instance_id(self):
        if self.kind=="service" and self.scope:return self.scope
        rows=[row for row in self.selected_rows() if row.get("kind")=="diagnostic_instance"]
        return rows[0]["id"] if rows else None

    # ------------------------------------------------------------------ 인스턴스 이름 바로 고치기(M5-4)
    def instance_name_clash(self,iid,name):
        """이름은 클래스·변형 안에서 유일하다(Diagnostic Instance Name). 이 인스턴스를 쓰는 변형의 같은 클래스에 같은 이름이 있으면 그 변형 이름."""
        wanted=text_key(name)
        for v in (self.state.get("tree") or {}).get("variants",[]):
            for c in v.get("classes",[]):
                ids={i["id"] for i in c["instances"]}
                if iid in ids and any(i["id"]!=iid and text_key(i["name"])==wanted for i in c["instances"]):return v["name"]
        return None

    def rename_instance(self,name):
        """이름 칸 확정: 한 명령(update_common). Qualifier가 옛 이름에서 만든 값과 같으면 새 이름을 따라간다(M2-1 규칙). 직접 고친 Qualifier는 그대로다."""
        info=self._instance_info
        if not info or not self.state.get("open"):return
        old=info.get("title","")
        restore=lambda:self.instance_name.set_original(old)
        if self.state.get("readonly") or not info.get("editable",True):
            restore();self.show_reason("이 인스턴스는 읽기 전용입니다.");return
        name=str(name or "").strip()
        if not name:
            restore();self.show_reason("인스턴스 이름을 입력하세요.");return
        if name==old:return
        clash=self.instance_name_clash(info["id"],name)
        if clash:
            restore();QMessageBox.warning(self,"인스턴스 이름",f"'{clash}' 변형의 같은 클래스에 '{name}' 인스턴스가 이미 있습니다. 이름은 클래스 안에서 유일해야 합니다.");return
        shared=len(info.get("variants") or [])
        if shared>1 and option(self.settings,"confirm_shared_edit"):
            answer=QMessageBox.question(self,"공유 인스턴스 이름",f"이 인스턴스를 {shared}개 변형이 함께 씁니다({', '.join(info['variants'])}). 모든 변형에서 이름이 바뀝니다. 계속할까요?\n"
                                        "(이 확인은 설정 → 확인 탭에서 끌 수 있습니다.)")
            if answer!=QMessageBox.StandardButton.Yes:restore();return
        payload={"id":info["id"],"name":name}
        current=info.get("qualifier") or ""
        if current and current==make_qualifier(old,"diagnostic_instance"):
            follow=make_qualifier(name,"diagnostic_instance")
            if follow and follow!=current:payload["qualifier"]=follow
        def failed(error):
            restore()
            self.log.appendPlainText(f"인스턴스 이름 바꾸기: {error.get('code','')} · {error.get('message','')}","오류","편집")
            QMessageBox.warning(self,"인스턴스 이름",error.get("message",""))
        def run():
            self.submit("execute_command",{"action":"update_common","payload":payload,"expected_revision":self.state["revision"],"command_id":str(uuid.uuid4())},
                        self.apply_state,on_error=failed)
        self.when_idle(run)

    def edit_instance(self):
        """인스턴스 페이지의 '속성…': 이름·Qualifier·설명 대화상자."""
        if self._instance_info:self.inspect_edit(self._instance_info["id"])

    def open_fault_memory(self):
        oid=self.current_instance_id()
        if not oid:
            QMessageBox.information(self,"고장 메모리 레코드","고장 메모리 인스턴스(예: Fault Memory)를 여세요.");return
        self.submit("fault_memory_view",{"oid":oid,"variant":self.variant},lambda data:FaultMemoryDialog(data,self,self.dtc_format.currentData() or "hex").exec())

    def open_revision_history(self):
        self.submit("revision_history",{},self.show_revision_history)

    def show_revision_history(self,data):
        TablesDialog("개정 이력",[("이력",data["history"],[("version","버전"),("author","작성자"),("date","날짜"),("status","상태"),("modification","변경 내용"),("reason","이유"),("tool","도구")]),
                                ("작성자",data["authors"],[("short","약칭"),("name","이름"),("company","회사"),("department","부서"),("responsible","책임자")])],
                     self,data.get("note","")).exec()

    def open_rework(self):
        self.submit("rework_items",{},self.show_rework)

    def show_rework(self,data):
        dialog=ReworkDialog(data["items"],self,self.path_entry.text().strip())
        result=dialog.exec()
        if result==2 and dialog.goto_link:
            self.path_entry.setText(dialog.goto_link);self.go_path();return
        if result!=QDialog.DialogCode.Accepted:return
        items=dialog.values()
        if items==data["items"]:return
        if self.state.get("readonly"):
            QMessageBox.information(self,"Rework 목록","읽기 전용 프로젝트에는 저장할 수 없습니다.");return
        self.command("set_rework",{"items":items})

    def bulk_conditions(self):
        """의존성 편집 모드(U4): 여러 서비스의 한 상태 칸을 한 명령으로 바꾼다. 현재 열이 상태 열이면 그 상태를 미리 고른다."""
        if self.kind!="dependency":
            QMessageBox.information(self,"상태 조건 일괄 변경","의존성 목록에서 서비스를 여러 개 고르세요.");return
        ids=[]
        for row in self.selected_rows():
            if row.get("kind")=="dependency_group":ids.extend(row.get("member_ids",[]))
            elif row.get("kind")=="service":ids.append(row["id"])
        ids=list(dict.fromkeys(ids))
        if not ids:return
        column=self.table.currentIndex().column()
        field=self.model.columns[column][0] if 0<=column<len(self.model.columns) else ""
        dialog=BulkConditionDialog(self.state["tree"]["state_groups"],len(ids),self,field[3:] if field.startswith("st:") else None)
        if dialog.exec()==QDialog.DialogCode.Accepted:self.command("bulk_conditions",{"ids":ids,**dialog.values()})

    def delete_object(self):
        ids=self.selected_ids()
        if not ids:return
        self.submit("deletion_impact",{"oid":ids[0],"variant":self.variant},self.show_delete_impact)

    def open_reference_impact(self,oid=None):
        if not isinstance(oid,str):
            ids=self.selected_ids();oid=ids[0] if ids else None
        if oid and not oid.startswith("unsupported:"):
            self.submit("reference_impact",{"oid":oid},self.show_reference_impact)

    def navigate_reference(self,row):
        if row.get("path"):
            self.submit("locate_object",{"oid":row["id"],"variant":row.get("variant")},self.navigate_target)

    def show_reference_impact(self,result):
        dialog=ReferenceImpactDialog(result,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:self.navigate_reference(dialog.result_row or {})

    def show_delete_impact(self,result):
        dialog=ReferenceImpactDialog(result,self,"delete")
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        chosen=dialog.result_row or {}
        if chosen.get("action")=="delete":self.apply_delete(result["record"])
        else:self.navigate_reference(chosen)

    def copy_selected_type(self,oid=None):
        if not isinstance(oid,str):
            ids=self.selected_ids();oid=ids[0] if ids else None
        if oid:self.submit("details",{"oid":oid},self.prepare_type_copy)

    def prepare_type_copy(self,record):
        if record.get("kind")!="data_type":
            QMessageBox.information(self,"독립 복사","데이터 타입 목록에서 타입을 선택하세요.");return
        if self.state.get("readonly") or record.get("read_only") or not record.get("supported",1):
            QMessageBox.information(self,"독립 복사","읽기 전용 또는 미지원 타입은 독립 복사할 수 없습니다.");return
        name,ok=QInputDialog.getText(self,"데이터 타입 독립 복사","새 타입 이름",text=record["name"]+" 사본")
        if not ok:return
        qualifier,ok=QInputDialog.getText(self,"데이터 타입 독립 복사","새 Qualifier (중복 불가)",text=record["qualifier"]+"_Copy")
        if not ok:return
        self.submit("execute_command",{"action":"copy_type","payload":{"id":record["id"],"name":name,"qualifier":qualifier},
                                      "expected_revision":self.state["revision"],"command_id":str(uuid.uuid4())},self.type_copied)

    # ------------------------------------------------------------------ 변형 마법사(M6-4)
    def open_variant_wizard(self,*_):
        if not self.state.get("open"):return
        if self.state.get("readonly"):
            QMessageBox.information(self,"변형 마법사","읽기 전용 프로젝트입니다.");return
        source=self.variant or next((v["id"] for v in self.state.get("variants",[]) if v.get("is_base")),None)
        self.when_idle(lambda:self.submit("variant_wizard_items",{"source":source},self.show_variant_wizard))

    def show_variant_wizard(self,items):
        dialog=VariantWizardDialog(items,self,copy_allowed=option(self.settings,"wizard_copy"))
        def requery(vid):
            def failed(error):
                dialog.set_items(dialog.items);QMessageBox.warning(dialog,"변형 마법사",error.get("message",""))
            self.when_idle(lambda:self.submit("variant_wizard_items",{"source":vid},dialog.set_items,on_error=failed))
        dialog.source_requested.connect(requery)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        values=dialog.values()
        def done(state):
            new_id=state.get("selected_id")
            if new_id:self.variant=new_id;self.node_key=f"var:{new_id}:did"  # 새 변형의 DID 노드로 간다
            self.apply_state(state)
        self.when_idle(lambda:self.submit("execute_command",{"action":"create_variant","payload":values,"expected_revision":self.state["revision"],"command_id":str(uuid.uuid4())},done))

    # ------------------------------------------------------------------ 데이터 타입 바꾸기(M6-3)
    def replace_selected_type(self,oid=None):
        """데이터 타입 목록의 선택 타입(또는 oid)을 여는 Replace Data Type 대화상자."""
        if not isinstance(oid,str):
            rows=[row for row in self.selected_rows() if row.get("kind")=="data_type"]
            oid=rows[0]["id"] if rows else None
        if not oid:
            QMessageBox.information(self,"데이터 타입 바꾸기","데이터 타입 목록에서 바꿀 타입을 고르세요.");return
        if self.state.get("readonly"):
            QMessageBox.information(self,"데이터 타입 바꾸기","읽기 전용 프로젝트입니다.");return
        self.when_idle(lambda:self.submit("replace_type_preview",{"old_type_id":oid},self.show_replace_type))

    def show_replace_type(self,preview):
        if not preview["rows"]:
            QMessageBox.information(self,"데이터 타입 바꾸기",f"'{preview['old']['name']}' 타입을 쓰는 데이터 객체가 없습니다.");return
        dialog=ReplaceTypeDialog(preview,self)
        old=preview["old"]["id"]
        def requery(target):
            def failed(error):
                dialog.set_preview(dialog.preview);QMessageBox.warning(dialog,"데이터 타입 바꾸기",error.get("message",""))
            self.when_idle(lambda:self.submit("replace_type_preview",{"old_type_id":old,"new_type_id":target},dialog.set_preview,on_error=failed))
        dialog.target_requested.connect(requery)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        values=dialog.values()
        if not values["new_type_id"] or not values["data_object_ids"]:return
        shared=[row for row in dialog.selected_rows() if (row.get("shared") or 1)>1]
        if shared and option(self.settings,"confirm_shared_edit"):
            answer=QMessageBox.question(self,"데이터 타입 바꾸기",f"고른 사용처 중 {len(shared)}곳은 여러 변형이 함께 쓰는 DID·서비스에 있습니다. 모든 사용 변형에 반영됩니다. 계속할까요?\n"
                                        "(이 확인은 설정 → 확인 탭에서 끌 수 있습니다.)")
            if answer!=QMessageBox.StandardButton.Yes:return
        self.when_idle(lambda:self.command("replace_data_type",values))

    def type_copied(self,state):
        self.node_key="types"
        self.apply_state(state)
        self.log.appendPlainText("독립 타입과 하위 타입·구조를 복사했습니다. 사용할 DID·서비스의 데이터 구조에서 새 타입을 선택하세요.","정보","편집",
                                 {"oid":state.get("selected_id"),"variant":self.variant} if state.get("selected_id") else None)

    def confirm_delete(self,record):
        names=record.get("variants",[])
        text=f"'{record.get('name','')}' 객체를 삭제하시겠습니까?\n사용 중인 참조가 있으면 삭제가 차단됩니다."
        if names:text+="\n공유 변형: "+", ".join(names)
        if record["kind"] in ("did","dtc"):text+="\n현재 변형에서 제거하며 다른 변형의 객체는 유지합니다."
        if QMessageBox.question(self,"삭제 영향 확인",text)==QMessageBox.StandardButton.Yes:
            self.apply_delete(record)

    def apply_delete(self,record):
        fallback,direction=self.delete_fallback(record["id"])
        self.submit("execute_command",{"action":"delete","payload":{"id":record["id"],"variant_id":self.variant},"expected_revision":self.state["revision"],"command_id":str(uuid.uuid4())},lambda state:self.after_delete(state,fallback,direction,record.get("name","")))

    def delete_fallback(self,oid):
        rows=self.model.rows
        index=next((i for i,row in enumerate(rows) if row["id"]==oid),None)
        if index is None or len(rows)<=1:return (None,"")
        if index+1<len(rows):return (rows[index+1]["id"],"다음")
        return (rows[index-1]["id"],"이전")

    def after_delete(self,state,fallback,direction,name):
        state={**state,"selected_id":fallback};self.apply_state(state)
        message=f"'{name}' 삭제됨 · {direction} 객체로 이동했습니다. (선택한 객체가 삭제되어 이동)" if fallback else f"'{name}' 삭제됨 · 선택한 객체가 삭제되어 트리 위치를 유지합니다."
        self.statusBar().showMessage(message,8000);self.log.appendPlainText(message,"정보","편집")

    def bulk_edit(self):
        ids=self.selected_ids()
        if not ids:return
        field,ok=QInputDialog.getItem(self,"일괄 편집","모든 선택 객체에 적용할 필드",["description","name"],0,False)
        if not ok:return
        value,ok=QInputDialog.getMultiLineText(self,"일괄 편집",f"{len(ids)}개 객체의 {field} 값. 공유 객체는 모든 사용 변형에 반영됩니다.")
        if ok:self.command("bulk_edit",{"ids":ids,"field":field,"value":value})

    def copy_table(self):
        """[LIST-R100] 목록을 보이는 그대로 복사한다: 머리 줄은 화면 열 이름(숨긴 열 제외), 칸은 화면 글자(선택 행, 없으면 모든 행).
        칸 편집 중이면(Ctrl+C) 편집기가 먼저 받는다."""
        rows=sorted({i.row() for i in self.table.selectedIndexes() if i.row()<len(self.model.rows)}) or list(range(len(self.model.rows)))
        if not rows:return
        cols=[c for c in range(len(self.model.columns)) if not self.table.isColumnHidden(c)]
        lines=["\t".join(self.model.columns[c][1] for c in cols)]
        for row in rows:lines.append("\t".join(str(self.model.data(self.model.index(row,col)) or "") for col in cols))
        QApplication.clipboard().setText("\n".join(lines))

    def paste_dtcs(self):
        if self.kind!="dtc":QMessageBox.information(self,"표 붙여넣기","DTC 표에서 열 매핑을 통해 붙여넣을 수 있습니다.");return
        import csv,io
        try:
            rows=list(csv.reader(io.StringIO(QApplication.clipboard().text()),delimiter="\t"))
            if len(rows)<2:raise ValueError("헤더와 데이터 행이 필요합니다.")
            self.csv_mapping({"headers":rows[0],"rows":rows[1:]})
        except ValueError as e:QMessageBox.warning(self,"붙여넣기",str(e))

    def validate_project(self):self.submit("validate_project",{},self.show_validation)
    def validate_export(self):
        variant=self.variant or next(v["id"] for v in self.state["variants"] if v["is_base"])
        self.submit("validate_project",{"variants":[variant]},self.show_validation)
    def show_validation(self,report):
        self.validation_scope=report.get("scope_label","프로젝트 전체")
        self.validation_revision=report["revision"];self._validation_issues=report["issues"]
        self.apply_issue_filter();self.update_validation_header()
        blocked=sum(1 for i in self._validation_issues if i.get("blocks_export"))
        self.statusBar().showMessage(f"revision {report['revision']} 검증 완료 · {len(self._validation_issues)}개 항목 · 출력 차단 {blocked}개",10000)
        self.reveal_results()

    def reveal_results(self):
        """검증 결과가 나오면 결과 창의 검증 결과 탭을 앞에 보인다. 결과 창이 최소 높이로 접혀 있으면(23차부터 처음 높이)
        여러 행이 보이도록 늘린다(창 높이의 35%, 최대 360). 늘리기만 하고 줄이지는 않는다."""
        if self._list_max is not None:self.toggle_list_max(False)
        self.results_dock.setVisible(True)
        self.results_tabs.setCurrentIndex(0)
        target=max(self.results_dock.minimumHeight(),min(360,self.height()*35//100))
        if self.results_dock.height()<target-20:self.resizeDocks([self.results_dock],[target],Qt.Orientation.Vertical)

    def reset_issue_model(self):
        self.issue_model.beginResetModel();self.issue_model.columns=[tuple(c) for c in ISSUE_COLUMNS];self.issue_model.rows=[];self.issue_model.endResetModel()

    def apply_issue_filter(self,*_):
        severity=self.issue_severity.currentData()
        issues=[i for i in self._validation_issues if severity is None or i.get("severity","error")==severity]
        labels={"error":"오류","warning":"경고","info":"정보"};revision=self.validation_revision if self.validation_revision is not None else "—"
        rows=[{**issue,"id":issue.get("object_id"),"name":issue["message"],"kind":"issue","severity_text":labels.get(issue.get("severity","error"),issue.get("severity","")),"blocks_text":"범위 밖" if issue.get("in_scope") is False else "예" if issue.get("blocks_export") else "아니요","remedy":issue.get("scope_reason") or issue.get("remedy",""),"revision_text":revision} for issue in issues]
        self.issue_model.beginResetModel();self.issue_model.columns=[tuple(c) for c in ISSUE_COLUMNS];self.issue_model.rows=rows;self.issue_model.endResetModel()
        for col,width in enumerate((70,90,300,420,260,80,70)):self.issue_table.setColumnWidth(col,width)
        total=len(self._validation_issues);blocked=sum(1 for i in self._validation_issues if i.get("blocks_export"))
        outside=sum(1 for i in self._validation_issues if i.get("in_scope") is False)
        scope=getattr(self,"validation_scope","프로젝트 전체")
        self.validation_summary.setText(f"{scope} · revision {revision} · 전체 {total}개 · 표시 {len(rows)}개 · 출력 차단 {blocked}개"+(f" · 범위 밖 {outside}개" if outside else ""))

    def update_validation_header(self):
        if not hasattr(self,"validation_stale"):return
        if not self.state.get("open",False) or self.validation_revision is None:self.validation_stale.setVisible(False)
        else:self.validation_stale.setVisible(self.validation_revision!=self.state.get("revision"))

    def goto_issue(self,index):
        row=self.issue_model.rows[index.row()]
        oid=row.get("object_id")
        if not oid:return
        self.stash_filter()
        if str(oid).startswith("unsupported:"):self.submit("details",{"oid":oid},self.navigate_details)
        else:
            self._focus_oid=oid;self._focus_field=row.get("field") or "";self._focus_remedy=row.get("remedy") or "";self._focus_shown=False
            self.submit("locate_object",{"oid":oid},self.navigate_target)

    def navigate_details(self,record):
        """검증 결과 등에서 객체로 이동: 트리의 해당 노드를 펼쳐 선택하고 목록에서 행을 고른다."""
        self.show_details(record)
        variants=[self.variant]+[v["id"] for v in self.state["tree"]["variants"] if v["id"]!=self.variant]
        kind,oid=record["kind"],record["id"]
        keys=[]
        if kind=="service":keys=[f"var:{v}:inst:{record['instance_id']}" for v in variants]
        elif kind=="diagnostic_instance":
            keys=[f"var:{v}:cls:{record['class_id']}" for v in variants]+[f"var:{v}:inst:{oid}" for v in variants]
        elif kind=="diagnostic_class":keys=[f"var:{v}:cls:{oid}" for v in variants]
        elif kind in ("did","dtc"):keys=[f"var:{v}:{kind}" for v in variants]
        elif kind=="data_type":keys=["types"]
        elif kind=="state":keys=[f"sg:{record['group_id']}"]
        elif kind=="unsupported":keys=["unsupported"]
        elif kind=="nrc":keys=["nrcs"]
        elif kind=="protocol_service":keys=["psvc"]
        elif kind=="job_container":keys=[f"var:{v}:job:{oid}" for v in variants]
        if any(self.select_node(k) for k in keys):
            self.reset_list_view()
            # 검증 대상은 필터와 변형 범위에 숨겨져 있어도 속성 창에서 확인할 수 있다.
            self.record_history(self.node_key,None)
            self.refresh(oid)

    def conditions(self):
        ids=self.selected_ids()
        if self.kind not in ("service","dependency") or not ids:return
        def got(data):
            dialog=ConditionsDialog(data["details"],data["catalog"],self)
            if dialog.exec()==QDialog.DialogCode.Accepted:self.command("set_conditions",dialog.values())
        self.submit("inspect",{"oid":ids[0]},got)

    def preview(self):
        ids=self.selected_ids()
        if self.kind not in ("service","dependency") or not ids:QMessageBox.information(self,"전문 미리보기","서비스 표에서 객체를 선택하세요.");return
        oid=ids[0]
        def got(fields):
            dialog=PreviewDialog(fields,self)
            if dialog.exec()==QDialog.DialogCode.Accepted:self.submit("build_telegram_preview",{"oid":oid,"values":dialog.values()},lambda result:ReportDialog("전문 미리보기",self.preview_text(result),self).exec())
        self.submit("preview_fields",{"oid":oid},got)

    @staticmethod
    def preview_text(result):
        lines=[f"Revision {result['revision']}"]
        for t in result["telegrams"]:
            lines.extend(["",t["kind"].upper(),t["hex"],"","위치(bit) / 길이(bit) / 이름 / 물리값"])
            lines.extend(f"{r['bit_offset']:>8} / {r['bits']:>8} / {r['name']} / {r['physical']}" for r in t["layout"])
        return "\n".join(lines)

    def import_cdd(self):
        def start():
            path,_=QFileDialog.getOpenFileName(self,"CDD 가져오기","",CDD_IMPORT_FILTER)
            if path:self._import_path=path;self.submit("cdd_preview",{"path":path},self.cdd_ready)
        self.maybe_leave(start)

    def cdd_ready(self,preview):
        c=preview["coverage"]
        template=preview.get("document_type")=="CDDT"
        kind=("CDDT 문서 템플릿 — 템플릿의 프로토콜 서비스·진단 클래스 템플릿·타입·상태·NRC와 기본 변형의 기본 인스턴스로 새 프로젝트를 시작합니다.\n"
              if template else "")
        if not str(preview.get("version") or "13.").startswith("13."):
            kind+=f"이전 CANdela 형식({preview.get('version')}) 문서 — CANdelaStudio 13과 같은 요소로 해석하고, 모르는 요소는 원문으로 보존합니다.\n"
        text=(f"{preview['basename']}  ({preview.get('document_type','CDD')} {preview.get('version') or ''})\n{kind}\n타입 {c['data_types']} · DID {c['dids']} · 변형 {c['variants']}\n"
              f"진단 클래스 {c.get('classes',0)} · 인스턴스 {c.get('instances',0)} · Job 컨테이너 {c.get('jobs',0)} · NRC {c.get('nrcs',0)} · 프로토콜 서비스 {c.get('protocol_services',0)}\n"
              f"서비스 {c.get('services',0)} · DTC {c.get('dtcs',0)} · 상태 {c.get('states',0)}\n"
              f"보존 항목 {c['unsupported']} (출력 차단 {c.get('blocking',c['unsupported'])})\n\n"
              "고정 UDS 프로파일(0x10/0x11/0x22/0x2E/0x31/0x3E) 밖의 서비스와 해석하지 못한 구조는 원문으로 보존하며, 출력 차단 항목이 있으면 ODX 출력이 막힙니다.\n새 프로젝트로 가져오시겠습니까?")
        box=QMessageBox(self);box.setWindowTitle("CDDT 템플릿으로 새 프로젝트" if template else "CDD 가져오기 범위");box.setText(text);box.setDetailedText("\n".join(i["path"]+": "+i["reason"] for i in preview["issues"]));box.setStandardButtons(QMessageBox.StandardButton.Ok|QMessageBox.StandardButton.Cancel)
        if box.exec()==QMessageBox.StandardButton.Ok:
            if self._import_path:add_recent_file(self.settings,self._import_path)  # [MENU-R340] 가져온 CDD도 최근 파일
            self.kind="did";self.node_key=None;self.variant=None;self.page=0;self.submit("apply_cdd",{"preview_id":preview["preview_id"]},self.apply_state)

    def import_csv(self):
        path,_=QFileDialog.getOpenFileName(self,"DTC CSV 가져오기","","CSV (*.csv);;TSV (*.tsv);;모든 파일 (*)")
        if not path:return
        delimiter,ok=QInputDialog.getItem(self,"CSV 구분자","구분자",["쉼표 ( , )","세미콜론 ( ; )","탭"],2 if path.lower().endswith(".tsv") else 0,False)
        if ok:self.submit("csv_read",{"path":path,"delimiter":{"쉼표 ( , )":",","세미콜론 ( ; )":";","탭":"\t"}[delimiter]},self.csv_mapping)

    def csv_mapping(self,data):
        dialog=CsvMappingDialog(data,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:self.submit("csv_map",{"data":data,"variant_id":self.variant,**dialog.values()},self.csv_apply)

    def csv_apply(self,rows):
        dialog=CsvApplyDialog(rows,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            selected=dialog.values()
            if selected:self.kind="dtc";self.page=0;self.command("import_dtcs",{"rows":selected,"variant_id":self.variant})

    def export_csv(self):
        path,_=QFileDialog.getSaveFileName(self,"DTC CSV 출력","dtcs.csv","CSV (*.csv)")
        if path:self.submit("export_csv",{"variant":self.variant,"destination":path},lambda r:self.statusBar().showMessage(f"{r['count']}개 DTC 출력: {r['path']}",10000))

    def export_odx(self):
        dialog=ExportDialog(self.state["variants"],self.variant,self.settings.value("odx_schema",""),self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            values=dialog.values()
            if not values["destination"]:QMessageBox.warning(self,"출력 경로","출력 파일을 지정하세요.");return
            self.settings.setValue("odx_schema",values["xsd_path"])
            self.submit("export_odx",values,lambda r:ReportDialog("ODX 출력 보고서",json.dumps(r,ensure_ascii=False,indent=2),self).exec())

    # ------------------------------------------------------------------ 시작 화면·최근 파일·끌어 놓기(M6-1)
    def show_start(self,on):
        if not hasattr(self,"stack"):return
        self.stack.setCurrentWidget(self.start_page if on else self.work_area)
        if on:self.start_page.refresh()

    def start_command(self,key):
        {"new":self.new_project,"open":self.open_project,"cdd":self.import_cdd,"settings":self.open_settings}.get(key,lambda:None)()

    def open_recent(self,path,*_):
        if not path or self.pending:return
        if not Path(path).exists():
            if QMessageBox.question(self,"최근 파일",f"파일이 없습니다:\n{path}\n\n목록에서 뺄까요?")==QMessageBox.StandardButton.Yes:
                remove_recent_file(self.settings,path);self.start_page.refresh()
            return
        self.open_path(path)

    def open_in_folder(self,folder,*_):
        """최근 폴더·빠른 접근: 그 폴더에서 열기 대화상자."""
        def start():
            path,_=QFileDialog.getOpenFileName(self,"열기",str(folder or ""),OPEN_FILTER)
            if path:self.open_path_now(path)
        if not self.pending:self.maybe_leave(start)

    def fill_recent_menu(self):
        self.recent_menu.clear()
        files=recent_files(self.settings)[:max(0,option(self.settings,"recent_count"))]
        for i,path in enumerate(files):
            action=self.recent_menu.addAction(f"&{i+1}  {Path(path).name}",self._recent_opener(path));action.setToolTip(path);action.setStatusTip(path)
        if not files:
            empty=self.recent_menu.addAction("(최근 파일 없음)");empty.setEnabled(False)

    def _recent_opener(self,path):
        return lambda:self.open_recent(path)

    @staticmethod
    def dropped_path(event):
        mime=event.mimeData()
        if mime is None or not mime.hasUrls():return None
        for url in mime.urls():
            path=url.toLocalFile()
            if path and (path.lower().endswith(".diagproj") or is_cdd_path(path)):return path
        return None

    def dragEnterEvent(self,event):
        if self.dropped_path(event) and not self.pending:event.acceptProposedAction()
        else:event.ignore()

    def dragMoveEvent(self,event):
        if self.dropped_path(event) and not self.pending:event.acceptProposedAction()
        else:event.ignore()

    def dropEvent(self,event):
        """Drag and Drop of Files: .diagproj는 열고 .cdd·.cddt는 가져오기 미리보기로 보낸다(끌기가 끝난 뒤 실행)."""
        path=self.dropped_path(event)
        if not path or self.pending:event.ignore();return
        event.acceptProposedAction()
        QTimer.singleShot(0,lambda:self.open_path(path))

    # ------------------------------------------------------------------ 보기(M1-6·M1-7·M4-3)
    def toggle_list_max(self,on=None):
        """목록 크게 보기(M1-6, DTCs Full Screen): 트리·선택 객체 창·결과 창을 숨기고 Esc로 되돌린다."""
        if not isinstance(on,bool):on=self._list_max is None
        pane=self.work_splitter.widget(1)
        if on and self._list_max is None and self.state.get("open"):
            self._list_max={"dock":self.results_dock.isVisible()}
            self.tree.setVisible(False);pane.setVisible(False);self.results_dock.setVisible(False)
            self.list_max_button.setText("⤡ 원래대로 (Esc)");self.table.setFocus()
        elif not on and self._list_max is not None:
            saved=self._list_max;self._list_max=None
            self.tree.setVisible(True);pane.setVisible(True);self.results_dock.setVisible(saved.get("dock",True))
            self.list_max_button.setText("⤢ 목록 크게")
        self.esc_shortcut.setEnabled(self._list_max is not None)
        action=self.actions.get("list_max")
        if action is not None and action.isChecked()!=(self._list_max is not None):
            action.blockSignals(True);action.setChecked(self._list_max is not None);action.blockSignals(False)

    def reset_view(self,*_):
        """보기 초기화(M4-3, [MENU-R1135]): 분할 위치·결과 창 높이·열 너비·출력 창 필터를 처음 값으로 되돌린다."""
        self.toggle_list_max(False)
        for name in ("work_splitter","main_splitter"):self.settings.remove(name)
        self.log.clear_saved_state();self.log.reset_view()
        self.results_dock.setFloating(False);self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea,self.results_dock);self.results_dock.setVisible(True)
        self.resizeDocks([self.results_dock],[self.results_dock.minimumHeight()],Qt.Orientation.Vertical)
        h=max(self.work_splitter.height(),400);self.work_splitter.setSizes([h*11//20,h-h*11//20])
        w=max(self.splitter.width(),800);self.splitter.setSizes([280,w-280])
        h=max(self.property_tab.height(),200);self.property_tab.setSizes([h*3//5,h-h*3//5])
        self.layout_view.mode_group.button(0).setChecked(True);self.layout_view.apply_mode(0)
        self.results_tabs.setCurrentIndex(0)
        if self.model.columns:self.fit_columns()
        self.statusBar().showMessage("보기를 처음 배치로 되돌렸습니다.",5000)

    def header_menu(self,pos):
        header=self.table.horizontalHeader()
        section=header.logicalIndexAt(pos)
        menu=QMenu(self)
        menu.addAction("열 정보…",lambda:self.show_column_info(section))
        menu.addAction("열 너비를 내용에 맞추기",self.fit_columns)
        if self.kind=="dependency" and 0<=section<len(self.model.columns):
            # M6-7: 상태 열 머리의 오른쪽 버튼 — 왼쪽 상태 열 접기 / 모든 열 펼치기
            menu.addSeparator()
            states=[field for field,_ in self.model.columns if field.startswith("st:")]
            field=self.model.columns[section][0]
            left=menu.addAction("왼쪽 상태 열 접기",lambda:self.collapse_state_columns_left(section))
            left.setEnabled(field in states and states.index(field)>0)
            every=menu.addAction("모든 열 펼치기",self.show_all_state_columns);every.setEnabled(bool(self._dep_hidden))
        menu.exec(header.mapToGlobal(pos))

    def show_column_info(self,section=None):
        """열 정보(M1-7): 목록 열의 이름·Qualifier·종류·필수·설명(DTC 레코드 항목은 원본 템플릿, 표시 전용)."""
        if not self.model.columns:return
        if not isinstance(section,int) or section<0:section=self.table.currentIndex().column()
        ColumnInfoDialog(self.category.text(),self.model.columns,self.model.column_info,section,self).exec()

    # ------------------------------------------------------------------ 설정(M6-2)
    def open_settings(self,*_):
        dialog=SettingsDialog(self.settings,self)
        dialog.applied.connect(self.apply_settings)
        dialog.exec()

    def apply_settings(self):
        s=self.settings
        self.table.setShowGrid(option(s,"grid_lines"));self.table.setAlternatingRowColors(option(s,"colored_rows"))
        fmt=str(s.value("dtc_format","hex") or "hex")
        if self.dtc_format.currentData()!=fmt:self.dtc_format.setCurrentIndex(max(0,self.dtc_format.findData(fmt)))
        identifiers=option(s,"tree_identifiers")
        tree_changed=identifiers!=self._tree_identifiers
        self._tree_identifiers=identifiers
        if hasattr(self,"start_page"):self.start_page.refresh()
        if not self.state.get("open"):return
        if tree_changed and self.state.get("tree"):self.build_tree(self.state["tree"])
        if not self.pending:
            self.refresh()

    # ------------------------------------------------------------------ 선택 객체 창에서 고치기(M2-2)
    def sheet_edit(self,key,value,sheet):
        """속성 시트 값 확정: 기존 명령으로 한 번에 적용한다(여러 객체면 모두, 실행 취소 한 번)."""
        if self.state.get("readonly") or not self.state.get("open"):return
        if self.pending:
            self.statusBar().showMessage("작업이 끝난 뒤 다시 고치세요.",4000);self.property_sheet.set_sheet(sheet);return
        ids=list((sheet or {}).get("ids") or [])
        if not ids:return
        text="" if value is None else str(value)
        if key in ("name","description"):action,payload="bulk_edit",{"ids":ids,"field":key,"value":text}
        elif key=="qualifier":action,payload="update_common",{"id":ids[0],"qualifier":text.strip()}
        elif key=="code":action,payload="update_dtc",{"id":ids[0],"code":text.strip()}
        elif key.startswith("attr:"):
            entry=next((r for r in sheet.get("rows",[]) if r.get("key")==key),{})
            item={"key":key[5:],"raw":value} if entry.get("kind")=="enum" else {"key":key[5:],"value":text}
            action,payload="set_dtc_attributes",{"ids":ids,"attributes":[item]}
        elif key.startswith("user:"):action,payload="set_attributes",{"ids":ids,"key":key[5:],"value":text}
        else:return
        shared=[row for row in self.selected_rows() if row.get("id") in ids and (row.get("shared") or 0)>1]
        if shared and option(self.settings,"confirm_shared_edit"):
            answer=QMessageBox.question(self,"공유 객체 수정",f"고치려는 객체 중 {len(shared)}개를 여러 변형이 함께 씁니다. 모든 사용 변형에 반영됩니다. 계속할까요?\n"
                                        "(이 확인은 설정 → 확인 탭에서 끌 수 있습니다.)")
            if answer!=QMessageBox.StandardButton.Yes:self.property_sheet.set_sheet(sheet);return
        self._keep_selection=ids
        self.submit("execute_command",{"action":action,"payload":payload,"expected_revision":self.state["revision"],"command_id":str(uuid.uuid4())},
                    lambda state:self.apply_state({**state,"selected_id":ids[0]}))

    # ------------------------------------------------------------------ 멤버 칸 편집(M5-2)
    def member_edit(self,info):
        """데이터 배치 멤버 칸 확정. 여러 변형이 쓰는 DID·인스턴스면 U3 선택(이 변형에서 사본으로 바꾼 뒤 수정 / 모든 변형 수정)."""
        if self.state.get("readonly") or not self.state.get("open"):return
        if self.pending:
            self.statusBar().showMessage("작업이 끝난 뒤 다시 고치세요.",4000);return
        payload=dict(info["payload"])
        if int(info.get("shared") or 1)>1 and info.get("owner_kind") in ("did","service"):
            what="DID" if info["owner_kind"]=="did" else "진단 인스턴스(서비스)"
            box=QMessageBox(self);box.setWindowTitle(f"공유 {what} 멤버 수정")
            box.setText(f"이 {what}를 {info['shared']}개 변형이 함께 씁니다. '{info.get('label') or ''}' 멤버를 어떻게 고칠까요?")
            box.setInformativeText("사본으로 바꾸면 현재 변형만 새 객체를 쓰고 다른 변형은 원래 객체를 그대로 씁니다. 실행 취소할 수 있습니다.")
            copy=box.addButton("이 변형에서 사본으로 바꾼 뒤 수정",QMessageBox.ButtonRole.AcceptRole)
            all_=box.addButton("참조하는 모든 변형 수정",QMessageBox.ButtonRole.ActionRole)
            box.addButton("취소",QMessageBox.ButtonRole.RejectRole);box.setDefaultButton(copy);box.exec()
            if box.clickedButton()==copy:self.detach_member_edit(info);return
            if box.clickedButton()!=all_:return
        self.member_command(payload)

    def member_command(self,payload,keep=None):
        """멤버 수정 명령. 목록 선택(DID·서비스 행)을 그대로 두어 데이터 배치가 같은 칸으로 다시 열린다."""
        keep=keep or (self.selected_ids()[:1] or [None])[0]
        def done(state):
            self.apply_state({**state,"selected_id":keep or state.get("selected_id")})
        self.submit("execute_command",{"action":"update_member","payload":payload,"expected_revision":self.state["revision"],"command_id":str(uuid.uuid4())},done)

    def detach_member_edit(self,info):
        """U3 '사본으로 바꾼 뒤 수정': 소유자를 이 변형에서 사본으로 바꾸고, 사본의 같은 위치 멤버에 같은 값을 넣는다."""
        action="detach_did" if info["owner_kind"]=="did" else "detach_instance"
        current=(self.selected_ids()[:1] or [None])[0]
        payload=dict(info["payload"])
        def done(state):
            new_id=state.get("selected_id")
            keep=new_id if current in (None,info["owner"]) else current
            if new_id:
                self._after_refresh=lambda:self.submit("member_counterpart",{"object_id":payload["id"],"old_owner":info["owner"],"new_owner":new_id},
                                                       lambda result:self.member_command({**payload,"id":result["id"]},keep))
            self.apply_state({**state,"selected_id":keep})
        self.submit("execute_command",{"action":action,"payload":{"id":info["owner"],"variant_id":self.variant},"expected_revision":self.state["revision"],"command_id":str(uuid.uuid4())},done)

    def about(self):
        QMessageBox.about(self,"DiagAuthor",f"DiagAuthor {__version__}\n\nPython · PySide6 · SQLite\nWindows 오프라인 진단 데이터 작성 도구\n\n실제 ECU 통신은 수행하지 않습니다.\n개발 빌드이며 출시 검증은 진행 중입니다.\n실행 검사 결과와 CDD·ODX 지원 범위는 BUILD-NOTES.md를 참고하세요.")

    def closeEvent(self,event):
        if self.closing:event.accept();return
        event.ignore()
        if self.pending:
            self.statusBar().showMessage("작업 완료 또는 취소 후 창을 닫으세요.",6000);return
        self.maybe_leave(lambda:self.submit("shutdown",{},self.finish_close))

    def finish_close(self,_):
        self.settings.setValue("geometry",self.saveGeometry())
        self.settings.setValue("work_splitter",self.work_splitter.saveState());self.settings.setValue("main_splitter",self.splitter.saveState())
        self.thread.quit();self.thread.wait();self.closing=True;self.close()
