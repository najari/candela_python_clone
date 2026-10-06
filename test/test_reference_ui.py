"""Reference dialog guards destructive actions and preserves object identity on navigation."""
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QPushButton, QDialog
    from diagstudio.overviews import ReferenceImpactDialog
except ImportError:  # pragma: no cover - PySide6 없는 환경
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 없음")
class ReferenceDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def result(self, can_delete=False):
        return {"name": "Temperature", "revision": 1, "can_delete": can_delete, "reason": "사용 중입니다.",
                "removed": ["Temperature"] if can_delete else [], "rows": [
                    {"id": "stable-id", "kind": "did", "name": "Reading", "relation": "데이터 구조 사용",
                     "reach": "간접 영향", "variants": "Base, High", "variant": "base-id", "path": "diag://test"}]}

    def test_blocked_delete_cannot_be_accepted_as_delete(self):
        dialog = ReferenceImpactDialog(self.result(), mode="delete")
        self.assertNotIn("삭제 적용", [button.text() for button in dialog.findChildren(QPushButton)])
        dialog.open_row(0, 0)
        self.assertEqual(dialog.result_row["id"], "stable-id")
        self.assertNotIn("action", dialog.result_row)

    def test_edit_copy_and_cancel_are_distinct(self):
        dialog = ReferenceImpactDialog(self.result(), mode="edit")
        button = next(button for button in dialog.findChildren(QPushButton) if button.text() == "독립 복사 만들기")
        button.click()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.result_row, {"action": "copy"})
        cancelled = ReferenceImpactDialog(self.result(), mode="edit")
        cancelled.reject()
        self.assertIsNone(cancelled.result_row)

    def test_allowed_delete_has_explicit_action(self):
        dialog = ReferenceImpactDialog(self.result(True), mode="delete")
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "삭제 적용").click()
        self.assertEqual(dialog.result_row, {"action": "delete"})


if __name__ == "__main__": unittest.main()
