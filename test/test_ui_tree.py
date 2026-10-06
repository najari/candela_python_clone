"""탐색 트리(T1) 회귀 테스트. Qt offscreen으로 주 창의 트리 구성·노드 선택·펼침 유지를 확인한다.

기대 구조 근거: CANdelaStudio 13 도움말 Navigation Tree [TREE-R010], ABS_ESP 예제의 VAR 배치.
PySide6가 없거나 예제 CDD가 없으면 건너뛴다.
"""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXAMPLES = Path(os.environ.get("DIAGAUTHOR_CDD_EXAMPLES", r"C:\Users\Public\Documents\Vector\CANdelaStudio\13.0\Examples"))
ABS = EXAMPLES / "ABS_ESP-Example.cdd"
_PROFILE = tempfile.mkdtemp(prefix="diagauthor-test-")
os.environ["LOCALAPPDATA"] = _PROFILE
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 25차: 사용자 설정(Windows 레지스트리) 대신 임시 ini를 쓴다(panels.app_settings).
os.environ.setdefault("DIAGAUTHOR_SETTINGS", os.path.join(tempfile.mkdtemp(prefix="diagauthor-settings-"), "settings.ini"))

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - PySide6 없는 환경
    QApplication = None

from diagstudio.application import Application  # noqa: E402
from diagstudio.interchange import cdd_preview  # noqa: E402


def tearDownModule():
    shutil.rmtree(_PROFILE, ignore_errors=True)


@unittest.skipIf(QApplication is None, "PySide6 없음")
class TreeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ABS.is_file(): raise unittest.SkipTest(f"예제 CDD 없음: {ABS}")
        cls.qt = QApplication.instance() or QApplication([])
        from diagstudio.ui import MainWindow
        cls.data = Application()
        cls.state = cls.data.apply_cdd(cdd_preview(ABS))
        cls.window = MainWindow()
        cls.window.refresh = lambda *a, **k: None  # 목록 조회는 작업 스레드 대신 cls.data로 직접 확인한다.
        cls.window.apply_state(cls.state)
        cls.base = next(v for v in cls.state["tree"]["variants"] if v["is_base"])

    @classmethod
    def tearDownClass(cls):
        cls.window.thread.quit(); cls.window.thread.wait()
        cls.data.close_project()

    def children(self, key):
        item = self.window.tree_items[key]
        return [item.child(i).text() for i in range(item.rowCount())]

    def rows(self):
        w = self.window
        return self.data.query_objects(w.kind, w.variant, scope=w.scope, page_size=1000)["rows"]

    def test_top_level_order_follows_candela(self):
        top = self.children("ecu")
        self.assertEqual(top[0], "지원 인터페이스")  # ECU Information 하위(U5)
        self.assertEqual(top[1], "고장 메모리")
        self.assertTrue(top[2].startswith("DID 개요"))
        self.assertEqual(top[3], "◆ Base Variant")
        self.assertEqual(top[4], "변형 (3)")
        self.assertEqual(top[5], "상태")
        self.assertTrue(top[6].startswith("데이터 타입 ("))
        self.assertEqual(top[7:9], ["프로토콜 서비스 (31)", "부정 응답 코드 (40)"])
        self.assertTrue(top[9].startswith("보존 항목"))

    def test_intermediate_nodes(self):
        """U4·U5 중간 노드: 지원 인터페이스 → 인터페이스, 상태 하위 3개, NRC 하위(미지원 서비스)."""
        self.assertEqual(self.children("ifaces"), ["Diagnostic CAN"])
        self.assertEqual(self.children("states"), ["상태 그룹", "의존성", "기본 상태 전이"])
        self.assertEqual(len(self.children("sgroups")), 2)
        self.assertEqual(self.children("nrcs"), ["미지원 서비스 NRC (2)"])
        self.assertTrue(self.window.select_node("sgroups"))
        self.assertEqual(self.window.kind, "state_group")
        self.assertTrue(self.window.select_node("iface:Diagnostic_CAN"))
        self.assertEqual((self.window.kind, self.window.scope), ("comm_params", "Diagnostic_CAN"))

    def test_base_variant_shows_classes_and_singletons_in_document_order(self):
        labels = self.children(f"var:{self.base['id']}")
        self.assertEqual(labels[:3], ["식별 특징", "DID (19)", "DTC (31)"])
        self.assertTrue(labels[3].startswith("지원 진단 클래스 ("), labels[3])
        self.assertTrue(labels[-1].startswith("모든 서비스"))
        classes = self.children(f"var:{self.base['id']}:classes")
        self.assertEqual(classes[:3], ["Sessions (3)", "Identification (7)", "Security Access (2)"])
        # 29차: Fault Memory의 EVENTDATA와 빈 Job 컨테이너는 출력 영향 없음(비차단)이라 ⚠가 없다.
        self.assertTrue(classes[3].startswith("Fault Memory") and not classes[3].endswith("⚠"), classes[3])
        self.assertTrue(classes[-3].startswith("Tester Present"), classes[-3])
        self.assertEqual(classes[-2:], ["🔒 Generic Jobs", "🔒 Flash Jobs"])
        a_sample = next(v for v in self.state["tree"]["variants"] if v["name"].startswith("A"))
        self.assertEqual(self.children(f"var:{a_sample['id']}")[0], "식별 패턴")
        self.assertIn("🔒 Generic Jobs ↪", self.children(f"var:{a_sample['id']}:classes"))
        ident = next(c for c in self.base["classes"] if c["qualifier"] == "Identification")
        self.assertEqual(self.children(f"var:{self.base['id']}:cls:{ident['id']}")[0], "F190 VIN")

    def test_class_node_lists_its_instances(self):
        ident = next(c for c in self.base["classes"] if c["qualifier"] == "Identification")
        self.assertTrue(self.window.select_node(f"var:{self.base['id']}:cls:{ident['id']}"))
        self.assertEqual((self.window.kind, self.window.scope), ("diagnostic_instance", ident["id"]))
        self.assertEqual([r["identifier_text"] for r in self.rows()][:2], ["F190", "F191"])

    def test_instance_node_lists_its_services(self):
        inst = next(c for c in self.base["classes"] if c["qualifier"] == "Identification")["instances"][0]
        self.assertTrue(self.window.select_node(f"var:{self.base['id']}:inst:{inst['id']}"))
        self.assertEqual(self.window.kind, "service")
        self.assertEqual(sorted(r["qualifier"] for r in self.rows()), ["VIN_Read", "VIN_Write"])

    def test_local_instance_in_extended_variant(self):
        c_sample = next(v for v in self.state["tree"]["variants"] if v["name"].startswith("C"))
        dyn = next(c for c in c_sample["classes"] if c["qualifier"] == "DynamicData")
        self.assertEqual(self.children(f"var:{c_sample['id']}:cls:{dyn['id']}")[1], "0113 Yaw Rate Sensor")

    def test_instance_labels_use_candela_notation_and_share_icons(self):
        """CANdela 표기: 괄호 없는 16진 식별자. 싱글턴은 이름만. 공유 구분은 텍스트 기호 대신 아이콘이다."""
        w = self.window
        marks = ("●", "◉", "○", "◈", "◎")
        instances = [(key, item) for key, item in w.tree_items.items() if ":inst:" in key]
        self.assertTrue(instances)
        for key, item in instances:
            self.assertFalse(any(mark in item.text() for mark in marks), item.text())
            self.assertFalse(item.icon().isNull(), item.text())
            self.assertTrue(item.toolTip(), item.text())
        tester_present = next(item for item in w.tree_items.values() if item.text().startswith("Tester Present"))
        self.assertNotIn("00", tester_present.text())
        self.assertIn("식별자 00", tester_present.toolTip())

    def test_property_pane_clears_when_tree_node_changes(self):
        """트리 노드를 옮기면 선택 객체 창에 이전 객체가 남지 않아야 한다(U1 결함)."""
        w = self.window
        ident = next(c for c in self.base["classes"] if c["qualifier"] == "Identification")
        w.select_node(f"var:{self.base['id']}:did")
        w.show_details({"kind": "did", "id": "did-x", "name": "Acceleration_Sensor", "qualifier": "Acceleration_Sensor"})
        self.assertIn("Acceleration_Sensor", w.properties.toPlainText())
        self.assertTrue(w.select_node(f"var:{self.base['id']}:cls:{ident['id']}"))
        self.assertNotIn("Acceleration_Sensor", w.properties.toPlainText())
        self.assertIsNone(w.details_record)

    def test_dependency_node_uses_current_variant(self):
        self.window.select_node(f"var:{self.base['id']}:did")
        self.assertTrue(self.window.select_node("dep"))
        self.assertIn("Base Variant", self.window.category.text())
        rows = self.rows()
        send_key = next(r for r in rows if r["qualifier"] == "KeyLevel1_Send")
        self.assertIn("✓ → Unlocked L1", [v for k, v in send_key.items() if k.startswith("st:")])

    def test_selection_without_current_change_opens_node(self):
        """UIA 선택(Select)처럼 현재 항목은 그대로 두고 선택만 바꿔도 노드가 열려야 한다."""
        from PySide6.QtCore import QItemSelectionModel
        w = self.window
        w.select_node(f"var:{self.base['id']}:did")
        ident = next(c for c in self.base["classes"] if c["qualifier"] == "Identification")
        index = w.tree_items[f"var:{self.base['id']}:cls:{ident['id']}"].index()
        w.tree.selectionModel().select(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.assertEqual((w.kind, w.scope), ("diagnostic_instance", ident["id"]))

    def test_rebuild_keeps_expansion_and_selection(self):
        w = self.window
        ident = next(c for c in self.base["classes"] if c["qualifier"] == "Identification")
        key = f"var:{self.base['id']}:cls:{ident['id']}"
        w.select_node(key)
        w.tree.setExpanded(w.tree_items[key].index(), True)
        w.apply_state(self.state)
        self.assertEqual(w.node_key, key)
        self.assertTrue(w.tree.isExpanded(w.tree_items[key].index()))
        self.assertEqual(w.tree.currentIndex().data(Qt.ItemDataRole.UserRole)["key"], key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
