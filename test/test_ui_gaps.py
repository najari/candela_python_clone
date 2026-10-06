"""UI-06 타입 미리보기·UI-12 복구 대화상자·검증 오류 필드 포커스 회귀 테스트. Qt offscreen."""
from pathlib import Path
from types import SimpleNamespace
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

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - PySide6 없는 환경
    QApplication = None

from diagstudio.application import Application  # noqa: E402

_PROFILE = None
_PREVIOUS_LOCALAPPDATA = None


def setUpModule():
    global _PROFILE, _PREVIOUS_LOCALAPPDATA
    _PROFILE = tempfile.mkdtemp(prefix="diagauthor-gaps-")
    _PREVIOUS_LOCALAPPDATA = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = _PROFILE


def tearDownModule():
    if _PREVIOUS_LOCALAPPDATA is None:
        os.environ.pop("LOCALAPPDATA", None)
    else:
        os.environ["LOCALAPPDATA"] = _PREVIOUS_LOCALAPPDATA
    shutil.rmtree(_PROFILE, ignore_errors=True)


@unittest.skipIf(QApplication is None, "PySide6 없음")
class UiGapsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        from diagstudio.ui import MainWindow
        cls.data = Application()
        cls.state = cls.data.new_project("Gaps ECU", "GapsECU")
        try:
            cls.window = MainWindow()
        except Exception as e:  # pragma: no cover - Qt 환경에 따라 창 생성 실패
            cls.data.close_project()
            raise unittest.SkipTest(f"MainWindow 생성 실패: {e}")
        cls.window.refresh = lambda *a, **k: None
        cls.base = next(v for v in cls.state["tree"]["variants"] if v["is_base"])["id"]

    @classmethod
    def tearDownClass(cls):
        cls.window.thread.quit(); cls.window.thread.wait()
        cls.data.close_project()

    def setUp(self):
        w = self.window
        w.apply_state(self.state)
        w.search.blockSignals(True); w.search.clear(); w.search.blockSignals(False)
        w.validation_revision = None; w._validation_issues = []; w.filter_stash = None
        w.validation_stale.setVisible(False); w.filter_back.setVisible(False)
        w.issue_severity.setCurrentIndex(0)
        w._focus_oid = ""; w._focus_field = ""; w._focus_remedy = ""; w._focus_shown = False; w._inspect_field = ""

    def test_type_preview(self):
        from diagstudio.dialogs import ObjectDialog
        d = ObjectDialog("data_type", {}, None)
        enc = d.form.fields["encoding"]; conv = d.form.fields["conversion"]
        enc.setCurrentIndex(enc.findData("unsigned")); conv.setCurrentIndex(conv.findData("linear"))
        d.form.fields["bit_length"].setText("16")
        d.form.fields["factor_text"].setText("0.1")
        d.form.fields["offset_text"].setText("-40")
        d.raw_preview.setText("650")
        self.assertEqual(d.phys_preview.text(), "25.0")
        d.form.fields["bit_length"].setText("8")
        self.assertIn("범위", d.phys_preview.text())
        enc.setCurrentIndex(enc.findData("ascii"))
        self.assertIn("선형·동일값", d.phys_preview.text())
        enc.setCurrentIndex(enc.findData("unsigned"))
        d.raw_preview.setText("")
        self.assertEqual(d.phys_preview.text(), "—")

    def test_focus_hint_and_goto_issue(self):
        w = self.window
        w._focus_oid = "obj-1"; w._focus_field = "qualifier"; w._focus_remedy = "값을 고치세요"; w._focus_shown = False
        w.show_focus_hint("obj-1")
        self.assertIn("수정 안내", w.statusBar().currentMessage())
        self.assertIn("수정 안내", w.log.toPlainText())
        first = w.statusBar().currentMessage()
        w.show_focus_hint("obj-1")
        self.assertEqual(w.statusBar().currentMessage(), first)
        self.assertTrue(w._focus_shown)
        w.issue_model.rows = [{"id": "x", "object_id": "obj-2", "field": "bit_length", "remedy": "고치세요"}]
        captured = []; original = w.submit
        w.submit = lambda method, args, callback=None: captured.append((method, args))
        try:
            w.goto_issue(SimpleNamespace(row=lambda: 0))
        finally:
            w.submit = original
        self.assertEqual(w._focus_oid, "obj-2")
        self.assertEqual(captured[-1][0], "locate_object")
        self.assertEqual(captured[-1][1], {"oid": "obj-2"})

    def test_object_dialog_focus_field(self):
        from diagstudio.dialogs import ObjectDialog
        d = ObjectDialog("variant", {"variants": []}, record={"name": "V"}, focus_field="qualifier")
        d.show(); QApplication.processEvents()
        self.assertIs(d.focusWidget(), d.form.fields["qualifier"])
        d.close()

    def test_recovery_dialog_columns_and_rows(self):
        from diagstudio.dialogs import RecoveryDialog
        rows = [{"updated_at": "t1", "saved_at": "s1", "source": "p1", "integrity": "정상 · v6", "source_state": "원본 그대로", "session_id": "a"},
                {"updated_at": "t2", "saved_at": "—", "source": "저장 전", "integrity": "손상", "source_state": "저장 전", "session_id": "b"}]
        d = RecoveryDialog(rows)
        self.assertEqual([d.table.horizontalHeaderItem(i).text() for i in range(d.table.columnCount())],
                         ["갱신 시각", "저장 시각", "원본 경로", "무결성", "원본 상태"])
        self.assertEqual(d.table.rowCount(), 2)
        self.assertEqual(d.table.item(0, 3).text(), "정상 · v6")
        self.assertEqual(d.table.item(1, 3).text(), "손상")

    def test_cdd_suffix_routing_and_dialog_filters(self):
        from diagstudio.ui import CDD_IMPORT_FILTER, OPEN_FILTER, is_cdd_path
        for name in ("model.cdd", "template.cddt", "TEMPLATE.CDDT"):
            self.assertTrue(is_cdd_path(name), name)
        for name in ("project.diagproj", "backup.diagproj.bak", "notes.txt"):
            self.assertFalse(is_cdd_path(name), name)
        self.assertIn("*.cddt", OPEN_FILTER)
        self.assertIn("*.cddt", CDD_IMPORT_FILTER)

    def test_object_model_localizes_enum_and_usage_cells(self):
        from diagstudio.ui import ObjectModel
        model = ObjectModel()
        model.replace("data_type", [{"id": "t", "kind": "data_type", "name": "T", "qualifier": "T", "encoding": "unsigned",
                                     "byte_order": "little", "conversion": "linear", "unit": "degC", "usage": 3}])
        fields = [c[0] for c in model.columns]
        def cell(field): return model.data(model.index(0, fields.index(field)))
        self.assertEqual(cell("conversion"), "선형")
        self.assertEqual(cell("byte_order"), "Little endian")
        self.assertEqual(cell("encoding"), "unsigned")
        self.assertEqual(cell("usage"), "3개")
        nrc_model = ObjectModel()
        nrc_model.replace("nrc", [{"id": "n", "kind": "nrc", "name": "N", "qualifier": "N", "code": 19, "usage": 2}])
        nrc_fields = [c[0] for c in nrc_model.columns]
        self.assertEqual(nrc_model.data(nrc_model.index(0, nrc_fields.index("usage"))), "2개")

    def test_service_nrc_picker_keeps_values_shape(self):
        from PySide6.QtCore import Qt
        from diagstudio.dialogs import ObjectDialog
        nrcs = [{"code": 19, "name": "a"}, {"code": 34, "name": "b"}, {"code": 49, "name": "c"}]
        dialog = ObjectDialog("service", {"dids": [], "types": []}, record={"id": "s1", "name": "S", "nrcs": [19, 49]}, nrcs=nrcs)
        self.assertEqual(dialog.values()["nrcs"], ["0x13", "0x31"])
        dialog.nrc_table.item(dialog.nrc_codes.index(34), 0).setCheckState(Qt.CheckState.Checked)
        dialog.nrc_table.item(dialog.nrc_codes.index(19), 0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(dialog.values()["nrcs"], ["0x22", "0x31"])
        self.assertTrue(all(v.startswith("0x") for v in dialog.values()["nrcs"]))

    def test_packet_tab_only_for_packet_type(self):
        from diagstudio.dialogs import ObjectDialog
        dialog = ObjectDialog("data_type", {}, None)
        index = dialog.tabs.indexOf(dialog.members)
        self.assertFalse(dialog.tabs.isTabVisible(index))
        encoding = dialog.form.fields["encoding"]
        encoding.setCurrentIndex(encoding.findData("packet"))
        self.assertTrue(dialog.tabs.isTabVisible(index))
        encoding.setCurrentIndex(encoding.findData("unsigned"))
        self.assertFalse(dialog.tabs.isTabVisible(index))

    def test_readonly_kinds_disable_add_and_report_reason(self):
        import diagstudio.ui as ui
        w = self.window
        w.kind = "nrc"
        w.update_enabled()
        self.assertFalse(w.actions["add"].isEnabled())
        self.assertFalse(w.actions["delete"].isEnabled())
        self.assertIn("읽기 전용", w.actions["add"].toolTip())
        self.assertIn("읽기 전용", w.actions["delete"].toolTip())
        self.assertFalse(w.edit_button.isEnabled())
        captured = []
        class FakeBox:
            @staticmethod
            def information(*args, **kwargs): captured.append(args[2] if len(args) > 2 else "")
        original = ui.QMessageBox; ui.QMessageBox = FakeBox
        try:
            w.add_object("nrc")
            w.edit_inspected({"details": {"kind": "nrc", "id": "x", "supported": 1, "read_only": 1}})
        finally:
            ui.QMessageBox = original
        self.assertEqual(len(captured), 2)
        self.assertTrue(all("읽기 전용" in message for message in captured))
        w.kind = "did"; w.update_enabled()

    def test_type_to_did_jump_uses_relation_query(self):
        w = self.window
        w.pending = False
        captured = []; original = w.submit
        w.submit = lambda method, args, callback=None: captured.append((method, args))
        try:
            w.go_related("did", "type-1")
        finally:
            w.submit = original
        self.assertEqual(captured[-1], ("related_targets", {"oid": "type-1", "kind": "did"}))

    def test_property_pane_clears_on_node_change(self):
        w = self.window
        w.select_node(f"var:{self.base}:did")
        w.show_details({"kind": "data_type", "id": "t", "name": "OldType", "qualifier": "OldType", "supported": 1, "read_only": 0})
        self.assertIn("OldType", w.properties.toPlainText())
        self.assertTrue(w.select_node("types"))
        self.assertNotIn("OldType", w.properties.toPlainText())
        self.assertIsNone(w.details_record)

    def test_path_bar_shows_name_path_and_copy_routes(self):
        w = self.window
        w.apply_state(self.state)
        self.assertTrue(w.select_node(f"var:{self.base}:did"))
        self.assertTrue(w.path_entry.text().startswith("/"))
        self.assertNotIn("diag://", w.path_entry.text())
        captured = []; original = w.submit
        w.submit = lambda method, args, callback=None: captured.append((method, args))
        w.pending = False
        try:
            w.copy_path()
            w.copy_stable_path()
        finally:
            w.submit = original
        self.assertEqual(captured[-2][0], "node_name_path")
        self.assertEqual(captured[-1][0], "node_path")

    def test_selection_history_back_forward_dedupe_and_cap(self):
        w = self.window
        w.clear_history()
        did = f"var:{self.base}:did"
        w.select_node(did); w.record_history(did)
        w.select_node("types"); w.record_history("types")
        w.record_history("types")
        self.assertEqual(len(w.history), 2)
        self.assertEqual(w.history_pos, 1)
        w.history_back()
        self.assertEqual((w.node_key, w.history_pos), (did, 0))
        w.history_forward()
        self.assertEqual((w.node_key, w.history_pos), ("types", 1))
        w.clear_history()
        for i in range(150):
            w.record_history(f"k{i % 7}")
        self.assertEqual(len(w.history), 100)
        self.assertEqual(w.history_pos, 99)

    def test_history_starts_at_opened_node(self):
        """프로젝트를 연 직후의 위치가 이력의 첫 항목이다. 첫 이동 뒤 ‹(뒤로)로 그 위치에 돌아올 수 있어야 한다."""
        w = self.window
        w.pending = False
        w.clear_history()
        w.apply_state(self.state)
        start = w.node_key
        self.assertEqual([e["node_key"] for e in w.history], [start])
        self.assertFalse(w.history_back_button.isEnabled())
        w.select_node("types"); w.record_history("types")
        self.assertTrue(w.history_back_button.isEnabled())
        w.history_back()
        self.assertEqual(w.node_key, start)
        self.assertTrue(w.history_forward_button.isEnabled())

    def test_history_shortcuts_and_buttons_exist(self):
        w = self.window
        w.pending = False
        w.clear_history()
        self.assertEqual(w.actions["back"].shortcut().toString(), "Alt+Left")
        self.assertEqual(w.actions["forward"].shortcut().toString(), "Alt+Right")
        self.assertFalse(w.history_back_button.isEnabled())
        self.assertFalse(w.history_forward_button.isEnabled())

    def test_find_document_empty_result_message(self):
        import diagstudio.ui as ui
        captured = []
        class FakeBox:
            @staticmethod
            def information(*args, **kwargs): captured.append(args[2] if len(args) > 2 else "")
        original = ui.QMessageBox; ui.QMessageBox = FakeBox
        try:
            self.window.show_find_results({"query": "zzz", "rows": []})
        finally:
            ui.QMessageBox = original
        self.assertTrue(captured and "없습니다" in captured[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)


@unittest.skipIf(QApplication is None, "PySide6 없음")
class CsvApplyVirtualTest(unittest.TestCase):
    """CSV 적용 미리보기: 10만 행을 위젯 항목 없이 모델로 보인다(계획서 성능 항목)."""

    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_large_preview_is_model_backed(self):
        import time
        from PySide6.QtCore import Qt
        from diagstudio.dialogs import CsvApplyDialog
        rows = [{"line": i + 2, "code": i, "name": f"DTC {i}", "qualifier": f"DTC_{i}", "description": "", "error": "" if i % 10 else "중복 코드"}
                for i in range(100000)]
        start = time.perf_counter()
        dialog = CsvApplyDialog(rows)
        self.assertLess(time.perf_counter() - start, 5.0)
        self.assertEqual(dialog.model.rowCount(), 100000)
        self.assertEqual(len(dialog.values()), 90000)  # 오류 행 제외, 정상 행은 기본 선택
        index = dialog.model.index(1, 0)
        self.assertTrue(dialog.model.setData(index, Qt.CheckState.Unchecked.value, Qt.ItemDataRole.CheckStateRole))
        self.assertEqual(len(dialog.values()), 89999)
        self.assertFalse(dialog.model.setData(dialog.model.index(0, 0), Qt.CheckState.Checked.value, Qt.ItemDataRole.CheckStateRole))
        dialog.model.set_all(False)
        self.assertEqual(dialog.values(), [])
        dialog.deleteLater()
