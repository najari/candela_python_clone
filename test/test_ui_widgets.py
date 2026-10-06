"""U2·U4·U6 위젯 회귀 테스트(Qt offscreen). 22차에서 작성했고 실행하지 않았다. PySide6가 없으면 건너뛴다."""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 25차: 사용자 설정(Windows 레지스트리) 대신 임시 ini를 쓴다(panels.app_settings).
os.environ.setdefault("DIAGAUTHOR_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="diagauthor-settings-"), "settings.ini"))
_PROFILE = tempfile.mkdtemp(prefix="diagauthor-widgets-")
os.environ["LOCALAPPDATA"] = _PROFILE

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - PySide6 없는 환경
    QApplication = None

LAYOUT = [
    {"kind": "literal", "label": "고정값", "bit_offset": 0, "bits": 8, "value": "62"},
    {"kind": "literal", "label": "DID", "bit_offset": 8, "bits": 16, "value": "01 0B"},
    {"kind": "data", "label": "Voltage", "bit_offset": 24, "bits": 8, "encoding": "unsigned", "type_name": "Voltage (1 Byte)",
     "conversion": "linear", "factor_text": "0.0625", "offset_text": "0", "unit": "V"},
    {"kind": "data", "label": "Acceleration", "bit_offset": 32, "bits": 8, "encoding": "unsigned", "type_name": "Acceleration",
     "conversion": "linear", "factor_text": "0.1", "offset_text": "-13", "unit": "m/s²"},
]


def tearDownModule():
    shutil.rmtree(_PROFILE, ignore_errors=True)


@unittest.skipIf(QApplication is None, "PySide6 없음")
class WidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_object_model_sae_new_row_and_checks(self):
        from diagstudio.ui import ObjectModel
        model = ObjectModel()
        model.new_row_label = "＋ 새 항목"
        model.replace("dtc", [{"id": "d", "kind": "dtc", "code": 0x402011, "name": "D", "qualifier": "D", "shared": 1}])
        self.assertEqual(model.rowCount(), 2)
        self.assertTrue(model.is_new_row(1))
        self.assertEqual(model.data(model.index(0, 0)), "0x402011")
        model.dtc_format = "sae"
        self.assertEqual(model.data(model.index(0, 0)), "C002011")
        self.assertIn("0x402011", model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole))
        self.assertEqual(model.data(model.index(1, 0)), "＋ 새 항목")
        checks = ObjectModel()
        checks.replace("supported_classes", [{"id": "src:class:0", "kind": "supported_classes", "checked": True, "name": "Sessions"}],
                       [("checked", "지원"), ("name", "진단 클래스")])
        self.assertEqual(checks.data(checks.index(0, 0), Qt.ItemDataRole.CheckStateRole), Qt.CheckState.Checked)
        states = ObjectModel()
        states.replace("state", [{"id": "s", "kind": "state", "code": 1, "name": "Default", "qualifier": "Default"}])
        self.assertEqual(states.data(states.index(0, 0)), "0x01")

    def test_layout_view_member_table_and_grid(self):
        from diagstudio.layout_view import LayoutView
        view = LayoutView()
        view.set_record({"kind": "did", "id": "x", "identifier": 0x010B, "name": "Acceleration Sensor", "layout": LAYOUT[2:]})
        pane = view.tabs.widget(0)
        names = [pane.members.item(r, 2).text() for r in range(pane.members.rowCount())]
        self.assertEqual(names, ["Voltage", "Acceleration"])
        self.assertIn("×0.1 -13", pane.members.item(1, 6).text())
        self.assertEqual(pane.grid.rowCount(), 2)  # 필드가 있는 바이트(3, 4)만 행이 된다
        view.apply_mode(2)
        self.assertTrue(pane.members.isHidden())
        view.set_record({"kind": "service", "id": "s", "sid": 0x22, "name": "Read", "telegram_layouts": {"request": LAYOUT[:2], "positive": LAYOUT},
                         "nrcs": [0x13, 0x31], "nrc_names": {0x31: "requestOutOfRange"}})
        self.assertEqual(view.tabs.count(), 3)  # 요청, 긍정 응답, NR
        self.assertIn("응답 데이터 (zz)", view.tabs.tabText(1))
        self.assertIn("7F 22 rc", view.header.text())

    def test_work_log_filter_and_plain_text(self):
        from diagstudio.pages import WorkLog
        log = WorkLog()
        log.appendPlainText("save_project: 완료")
        log.appendPlainText("검증 결과가 이전 revision입니다.", "경고")
        log.appendPlainText("query: INTERNAL_ERROR", "오류")
        self.assertIn("save_project: 완료", log.toPlainText())
        for kind, button in log.kind_buttons.items():
            button.setChecked(kind == "오류")
        self.assertEqual(log.table.rowCount(), 1)
        log.text_filter.setText("없는 말")
        self.assertEqual(log.table.rowCount(), 0)

    def test_shortcut_matrix_hides_common_rows(self):
        from diagstudio.pages import ShortcutMatrixDialog
        result = {"variants": [{"id": "b", "name": "Base"}, {"id": "a", "name": "A"}],
                  "rows": [{"id": "1", "kind": "service", "qualifier": "Both", "name": "Both", "variants": ["b", "a"], "path": "/x"},
                           {"id": "2", "kind": "service", "qualifier": "OnlyA", "name": "OnlyA", "variants": ["a"], "path": "/y"}]}
        dialog = ShortcutMatrixDialog(result)
        self.assertEqual(dialog.table.rowCount(), 2)
        dialog.hide_common.setChecked(True)
        self.assertEqual([r["qualifier"] for r in dialog.shown], ["OnlyA"])
        dialog.checks[1][1].setChecked(False)  # A 변형 열을 빼면 Base만 남고 Both는 공통이라 숨는다
        self.assertEqual(dialog.shown, [])

    def test_object_dialog_navigation_and_in_use_lock(self):
        from diagstudio.dialogs import ObjectDialog
        record = {"id": "t", "name": "T", "qualifier": "T", "encoding": "unsigned", "bit_length": 8, "array_length": 1, "byte_order": "big",
                  "conversion": "identity", "usage": 3, "attributes": [{"key": "cdd.X", "value_type": "text", "value_text": "1"}]}
        dialog = ObjectDialog("data_type", {"types": []}, record, navigation=(True, False))
        self.assertFalse(dialog.form.fields["bit_length"].isEnabled())
        self.assertFalse(dialog.members.isEnabled())
        self.assertFalse(dialog.changed())
        dialog.form.fields["unit"].setText("V")
        self.assertTrue(dialog.changed())
        dialog.go(-1)
        self.assertEqual(dialog.navigate, -1)
        self.assertTrue(any(dialog.tabs.tabText(i).startswith("속성") for i in range(dialog.tabs.count())))

    def test_qualifier_column_is_always_last(self):
        """Qualifier 열은 목록·세부 표·구조 편집 표에서 항상 맨 뒤다. 'Shortcut Qualifier'(그 표의 주제)와 '원본 경로'는 옮기지 않는다."""
        from diagstudio.presentation import qualifier_last
        from diagstudio.ui import ObjectModel
        from diagstudio.pages import DetailTable
        from diagstudio.dialogs import MembersEditor
        self.assertEqual(qualifier_last([("code", "DTC"), ("qualifier", "Qualifier"), ("name", "이름"), ("shared", "공유 변형")]),
                         [("code", "DTC"), ("name", "이름"), ("shared", "공유 변형"), ("qualifier", "Qualifier")])
        self.assertEqual(qualifier_last([("qualifier", "Shortcut Qualifier"), ("name", "이름")]), [("qualifier", "Shortcut Qualifier"), ("name", "이름")])
        self.assertEqual(qualifier_last([("name", "사유"), ("qualifier", "원본 경로"), ("semantic", "출력 차단")])[1], ("qualifier", "원본 경로"))
        model = ObjectModel()
        model.replace("dtc", [{"id": "d", "kind": "dtc", "code": 0x402011, "name": "D", "qualifier": "DTC_402011", "shared": 1}])
        self.assertEqual([label for _, label in model.columns], ["DTC", "이름", "공유 변형", "Qualifier"])
        self.assertEqual(model.data(model.index(0, 3)), "DTC_402011")
        model.replace("did", [], [("identifier", "DID"), ("name", "이름"), ("qualifier", "Qualifier"), ("attr:x", "X")])
        self.assertEqual(model.columns[-1], ("qualifier", "Qualifier"))
        detail = DetailTable()
        detail.set_table({"title": "상태", "columns": [("code", "코드"), ("name", "이름"), ("qualifier", "Qualifier"), ("initial", "초기 상태")],
                          "rows": [{"code": 1, "name": "Default", "qualifier": "Default", "initial": "예"}]})
        self.assertEqual([detail.table.horizontalHeaderItem(c).text() for c in range(detail.table.columnCount())], ["코드", "이름", "초기 상태", "Qualifier"])
        editor = MembersEditor({"types": [{"id": "t", "name": "Byte"}]}, [{"kind": "data", "name": "Speed", "qualifier": "Speed_Q", "data_type_id": "t", "default_text": "1"}])
        self.assertEqual(editor.table.horizontalHeaderItem(editor.table.columnCount() - 1).text(), "Qualifier")
        self.assertEqual(editor.values(), [{"kind": "data", "name": "Speed", "qualifier": "Speed_Q", "data_type_id": "t", "default_text": "1", "constant_text": ""}])

    def test_dtc_attribute_editor(self):
        """DTC 속성 탭: 레코드 항목은 편집, 열거는 목록 선택(#raw는 열거 행에 합침), 가져오기 메타데이터는 읽기 전용. 바뀐 행만 보낸다."""
        from diagstudio.dialogs import ObjectDialog
        items = [{"key": "cdd.SetCondition", "name": "Set Condition", "kind": "text", "required": False, "hex": False},
                 {"key": "cdd.CorrectiveAction", "name": "Corrective action", "kind": "enum", "required": True, "hex": False,
                  "choices": [{"raw": 0, "label": "Replace ECU"}, {"raw": 5, "label": "No known corrective action"}]},
                 {"key": "cdd.SpecialInstruction", "name": "Special instruction", "kind": "text", "required": False, "hex": False}]
        attributes = [{"key": "cdd.SetCondition", "value_type": "text", "value_text": "old"},
                      {"key": "cdd.SetCondition@de-DE", "value_type": "text", "value_text": "alt"},
                      {"key": "cdd.CorrectiveAction", "value_type": "text", "value_text": "No known corrective action"},
                      {"key": "cdd.CorrectiveAction#raw", "value_type": "integer", "value_text": "5"},
                      {"key": "cdd.unverified", "value_type": "text", "value_text": "x"}]
        record = {"id": "d", "kind": "dtc", "name": "D", "qualifier": "D", "code": 0x402011, "attributes": attributes, "record_items": items}
        dialog = ObjectDialog("dtc", {}, record)
        editor = dialog.attribute_editor
        keys = [editor.table.item(r, editor.KEY).text() for r in range(editor.table.rowCount())]
        self.assertEqual(keys, ["cdd.SetCondition", "cdd.CorrectiveAction", "cdd.SpecialInstruction", "cdd.SetCondition@de-DE", "cdd.unverified"])
        self.assertFalse(dialog.changed())
        self.assertNotIn("attributes", dialog.values())
        combo = editor.table.cellWidget(1, editor.VALUE)
        self.assertEqual(combo.findData(None), -1)  # 필수 열거는 비울 수 없다
        self.assertEqual(combo.currentData(), 5)
        editor.table.item(0, editor.VALUE).setText("new")
        combo.setCurrentIndex(combo.findData(0))
        editor.table.item(2, editor.VALUE).setText("See manual")
        self.assertTrue(dialog.changed())
        self.assertEqual(dialog.values()["attributes"], [{"key": "cdd.SetCondition", "value": "new"}, {"key": "cdd.CorrectiveAction", "raw": 0},
                                                         {"key": "cdd.SpecialInstruction", "value": "See manual"}])
        self.assertFalse(editor.table.item(4, editor.VALUE).flags() & Qt.ItemFlag.ItemIsEditable)
        self.assertTrue(editor.table.item(3, editor.VALUE).flags() & Qt.ItemFlag.ItemIsEditable)


@unittest.skipIf(QApplication is None, "PySide6 없음")
class MainWindowPagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        from diagstudio.application import Application
        from diagstudio.ui import MainWindow
        cls.data = Application()
        cls.state = cls.data.new_project("Pages ECU", "PagesECU")
        cls.window = MainWindow()
        cls.window.refresh = lambda *a, **k: None
        cls.window.apply_state(cls.state)

    @classmethod
    def tearDownClass(cls):
        cls.window.thread.quit(); cls.window.thread.wait()
        cls.data.close_project()

    def test_dependency_groups_aggregate_cells(self):
        w = self.window
        rows = [{"id": "s1", "kind": "service", "name": "A", "group_key": "c|Start", "group_label": "Sessions / * / Start", "st:1": "✓", "st:2": "✗"},
                {"id": "s2", "kind": "service", "name": "B", "group_key": "c|Start", "group_label": "Sessions / * / Start", "st:1": "✓", "st:2": "✓"}]
        grouped = w.group_dependency_rows(rows, [("name", "서비스"), ("st:1", "S: 1"), ("st:2", "S: 2")])
        self.assertEqual(len(grouped), 1)
        self.assertEqual((grouped[0]["st:1"], grouped[0]["st:2"]), ("✓", "(혼합)"))
        w._dep_expanded.add("c|Start")
        self.assertEqual(len(w.group_dependency_rows(rows, [("st:1", "S: 1")])), 3)
        w._dep_expanded.clear()

    def test_page_header_instance_and_chapter(self):
        w = self.window
        w.set_page_info({"kind": "instance", "title": "Fault Memory", "identifier": "", "fault_memory": True, "text": "설명"})
        self.assertTrue(w.fault_memory_button.isVisibleTo(w))
        # 25차 M5-4: 인스턴스 이름은 머리의 이름 칸에 있다(머리 글에는 식별자·Qualifier·설명).
        self.assertTrue(w.instance_bar.isVisibleTo(w))
        self.assertEqual(w.instance_name.text(), "Fault Memory")
        self.assertIn("설명", w.page_header.text())
        self.assertTrue(w.select_node("types"))
        self.assertFalse(w.fault_memory_button.isVisibleTo(w))
        self.assertFalse(w.instance_bar.isVisibleTo(w))
        self.assertIn("데이터 타입", w.page_header.text())

    def test_local_rows_are_not_command_targets(self):
        w = self.window
        w.model.replace("interfaces", [{"id": "src:iface:0", "kind": "interfaces", "name": "Diagnostic CAN", "supported_check": True}],
                        [("supported_check", "지원"), ("name", "인터페이스")])
        w.table.selectRow(0)
        self.assertEqual(w.selected_ids(), [])
        self.assertEqual(len(w.selected_rows()), 1)
        w.show_local_details(w.selected_rows()[0])
        self.assertIn("Diagnostic CAN", w.properties.toPlainText())

    def test_selection_pane_is_below_list(self):
        """선택 객체 창(데이터 배치·세부 표·속성 탭과 속성 편집 버튼)은 목록 옆이 아니라 목록 아래에 있다."""
        w = self.window
        self.assertEqual(w.work_splitter.orientation(), Qt.Orientation.Vertical)
        self.assertTrue(w.work_splitter.widget(0).isAncestorOf(w.table))
        self.assertTrue(w.work_splitter.widget(1).isAncestorOf(w.detail_tabs))
        self.assertIs(w.detail_tabs.cornerWidget(Qt.Corner.TopRightCorner), w.edit_button)
        self.assertEqual(w.splitter.count(), 2)  # 트리 | (목록 / 선택 객체)


if __name__ == "__main__":
    unittest.main(verbosity=2)
