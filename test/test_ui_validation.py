"""검증 결과 UI·삭제 후속 선택·필터 복원(UI-10) 회귀 테스트. Qt offscreen으로 주 창을 확인한다.

PySide6가 없거나 주 창을 만들 수 없으면 건너뛴다.
"""
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

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - PySide6 없는 환경
    QApplication = None

from diagstudio.application import Application  # noqa: E402

_PROFILE = None
_PREVIOUS_LOCALAPPDATA = None


def setUpModule():
    global _PROFILE, _PREVIOUS_LOCALAPPDATA
    _PROFILE = tempfile.mkdtemp(prefix="diagauthor-validation-")
    _PREVIOUS_LOCALAPPDATA = os.environ.get("LOCALAPPDATA")
    os.environ["LOCALAPPDATA"] = _PROFILE


def tearDownModule():
    if _PREVIOUS_LOCALAPPDATA is None:
        os.environ.pop("LOCALAPPDATA", None)
    else:
        os.environ["LOCALAPPDATA"] = _PREVIOUS_LOCALAPPDATA
    shutil.rmtree(_PROFILE, ignore_errors=True)


@unittest.skipIf(QApplication is None, "PySide6 없음")
class ValidationUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        from diagstudio.ui import MainWindow
        cls.data = Application()
        cls.state = cls.data.new_project("Validation ECU", "ValidationECU")
        try:
            cls.window = MainWindow()
        except Exception as e:  # pragma: no cover - Qt 환경에 따라 창 생성 실패
            cls.data.close_project()
            raise unittest.SkipTest(f"MainWindow 생성 실패: {e}")
        cls.window.refresh = lambda *a, **k: None  # 목록 조회는 작업 스레드 대신 직접 확인한다.
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

    def test_severity_filter_and_columns(self):
        w = self.window
        issues = [
            {"rule_id": "R1", "message": "m1", "object_id": None, "path": "", "severity": "error", "remedy": "r", "blocks_export": True},
            {"rule_id": "R2", "message": "m2", "object_id": "fake-2", "path": "diag://x", "severity": "warning", "remedy": "r", "blocks_export": False},
            {"rule_id": "R3", "message": "m3", "object_id": "fake-3", "path": "diag://y", "severity": "info", "remedy": "r", "blocks_export": True},
        ]
        w.show_validation({"revision": 1, "issues": issues})
        self.assertEqual(w.issue_model.rowCount(), 3)
        self.assertEqual([label for _, label in w.issue_model.columns],
                         ["심각도", "규칙", "객체 경로", "내용", "수정 안내", "출력 차단", "revision"])
        w.issue_severity.setCurrentIndex(2)  # 경고
        self.assertEqual(w.issue_model.rowCount(), 1)
        w.issue_severity.setCurrentIndex(0)  # 전체
        self.assertEqual(w.issue_model.rowCount(), 3)

    def test_issue_columns_are_validation_columns_before_any_run(self):
        """검증을 실행하기 전에도 결과 표 머리는 검증 열이어야 한다(DID 열 결함 수정)."""
        from diagstudio.ui import ISSUE_COLUMNS
        w = self.window
        labels = [label for _, label in ISSUE_COLUMNS]
        self.assertEqual([label for _, label in w.issue_model.columns], labels)
        self.assertEqual(w.issue_model.columns and [field for field, _ in w.issue_model.columns],
                         [field for field, _ in ISSUE_COLUMNS])
        w.project_closed()
        self.assertEqual([label for _, label in w.issue_model.columns], labels)

    def test_stale_badge(self):
        w = self.window
        w.validation_revision = 1; w.state = {"open": True, "revision": 2}; w.update_validation_header()
        self.assertFalse(w.validation_stale.isHidden())
        w.state = {"open": True, "revision": 1}; w.update_validation_header()
        self.assertTrue(w.validation_stale.isHidden())
        w.state = {"open": False, "revision": 2}; w.update_validation_header()
        self.assertTrue(w.validation_stale.isHidden())

    def test_export_scope_is_visible_and_reset_by_project_validation(self):
        w = self.window
        w.show_validation({"revision": 1, "scope_label": "출력 범위: Base", "issues": [
            {"rule_id": "VAL-012", "message": "unsupported", "object_id": None,
             "severity": "warning", "blocks_export": False, "in_scope": False,
             "scope_reason": "다른 변형의 항목", "remedy": "수정"}]})
        self.assertIn("출력 범위: Base", w.validation_summary.text())
        self.assertEqual(w.issue_model.rows[0]["blocks_text"], "범위 밖")
        self.assertEqual(w.issue_model.rows[0]["remedy"], "다른 변형의 항목")
        w.show_validation({"revision": 1, "issues": []})
        self.assertIn("프로젝트 전체", w.validation_summary.text())

    def test_delete_fallback(self):
        w = self.window
        w.model.rows = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        self.assertEqual(w.delete_fallback("a"), ("b", "다음"))
        self.assertEqual(w.delete_fallback("b"), ("c", "다음"))
        self.assertEqual(w.delete_fallback("c"), ("b", "이전"))
        w.model.rows = [{"id": "a"}]
        self.assertEqual(w.delete_fallback("a"), (None, ""))

    def test_after_delete_overrides_selection(self):
        w = self.window
        recorded = []
        original = w.apply_state
        def fake(state):
            recorded.append(state); w.state = state
        w.apply_state = fake
        try:
            w.after_delete({"revision": 1, "open": True}, "fallback-id", "다음", "X")
            self.assertEqual(recorded[-1]["selected_id"], "fallback-id")
            self.assertIn("삭제", w.statusBar().currentMessage())
            self.assertIn("다음", w.statusBar().currentMessage())
            w.after_delete({"revision": 2, "open": True}, None, "", "Y")
            self.assertIsNone(recorded[-1]["selected_id"])
            self.assertIn("유지", w.statusBar().currentMessage())
        finally:
            w.apply_state = original

    def test_confirm_delete_wiring(self):
        from diagstudio import ui
        w = self.window
        submitted = []; deleted = []
        original_question = ui.QMessageBox.question
        original_after = w.after_delete; original_submit = w.submit
        w.model.rows = [{"id": "a"}, {"id": "b"}]; w.variant = "variant-x"; w.state = {"open": True, "revision": 5}
        ui.QMessageBox.question = lambda *a, **k: ui.QMessageBox.StandardButton.Yes
        w.after_delete = lambda state, fallback, direction, name: deleted.append((state, fallback, direction, name))
        w.submit = lambda method, args, callback=None: submitted.append((method, args, callback))
        try:
            w.confirm_delete({"id": "a", "kind": "did", "name": "A"})
            method, args, callback = submitted[-1]
            self.assertEqual(method, "execute_command")
            self.assertEqual(args["action"], "delete")
            callback({"open": True, "revision": 6})
            self.assertEqual(deleted[-1][0]["revision"], 6)
            self.assertEqual(deleted[-1][1], "b")
        finally:
            ui.QMessageBox.question = original_question; w.after_delete = original_after; w.submit = original_submit

    def test_restore_filter(self):
        w = self.window
        key = f"var:{self.base}:did"
        self.assertTrue(w.select_node(key))
        w.search.setText("abc")
        w.sort_key = "code"; w.descending = True; w.page = 1
        self.assertEqual(w.node_key, key)
        w.stash_filter()
        self.assertFalse(w.filter_back.isHidden())
        self.assertTrue(w.select_node("types"))
        self.assertEqual(w.node_key, "types")
        w.restore_filter()
        self.assertEqual(w.node_key, key)
        self.assertEqual(w.search.text(), "abc")
        self.assertEqual((w.sort_key, w.descending, w.page), ("code", True, 1))
        self.assertTrue(w.filter_back.isHidden())


if __name__ == "__main__":
    unittest.main(verbosity=2)
