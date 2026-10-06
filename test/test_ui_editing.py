"""26차 1단계(P1) UI 상호작용 회귀 테스트: 칸 편집·여러 행 입력·필터 규칙·노드 진입·새 번호·Qualifier 자동 생성·찾기·키보드·바이트 격자.

근거: docs/ui-manual-review-2026-09-26.md(M1-1~M1-5, M2-1, M3-1, M3-3, M5-1)와 CANdelaStudio 13 도움말 FeatureID.
작업 스레드까지 실제로 도는 주 창 테스트는 Qt offscreen에서 이벤트를 돌려 결과를 기다린다. PySide6가 없으면 건너뛴다.
"""
from contextlib import closing
from pathlib import Path
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 사용자 설정(QSettings)을 건드리지 않도록 테스트용 ini 파일을 쓴다.
if "DIAGAUTHOR_SETTINGS" not in os.environ:
    os.environ["DIAGAUTHOR_SETTINGS"] = os.path.join(tempfile.mkdtemp(prefix="diagauthor-settings-"), "settings.ini")
_PROFILE = tempfile.mkdtemp(prefix="diagauthor-editing-")
os.environ["LOCALAPPDATA"] = _PROFILE
EXAMPLES = Path(os.environ.get("DIAGAUTHOR_CDD_EXAMPLES", r"C:\Users\Public\Documents\Vector\CANdelaStudio\13.0\Examples"))
ABS = EXAMPLES / "ABS_ESP-Example.cdd"

try:
    from PySide6.QtCore import Qt, QItemSelectionModel
    from PySide6.QtWidgets import QApplication, QAbstractItemView, QComboBox, QMenu
    from PySide6.QtTest import QTest
except ImportError:  # pragma: no cover - PySide6 없는 환경
    QApplication = None

from diagstudio.application import Application, filter_terms  # noqa: E402
from diagstudio.domain import DomainError, make_qualifier, qualifier_follows  # noqa: E402
from diagstudio.presentation import byte_grid, parse_sae_dtc, sae_dtc  # noqa: E402


def tearDownModule():
    shutil.rmtree(_PROFILE, ignore_errors=True)


class QualifierAndFilterRules(unittest.TestCase):
    """Qt 없이 도는 규칙: [QUAL-R050]·결정 D3, [LIST-R500]·결정 D2, [DOUIG-R210], SAE 역변환."""

    def test_qualifier_generation(self):
        self.assertEqual(make_qualifier("Äpfel über Straße"), "Aepfel_ueber_Strasse")  # 로마자
        self.assertEqual(make_qualifier("Wheel  speed -- FL_"), "Wheel_speed_FL")  # 연속·끝 _
        self.assertEqual(make_qualifier("12V supply"), "_12V_supply")  # 숫자로 시작
        self.assertEqual(make_qualifier("Café"), "Cafe")
        self.assertEqual(len(make_qualifier("x" * 300)), 128)  # [QUAL-R130]
        self.assertEqual(make_qualifier("Wheel", case="upper"), "WHEEL")
        # 결정 D3: 한글만 있는 이름 → 종류 + 식별 번호(없으면 순번). 섞이면 영문·숫자만 남는다.
        self.assertEqual(make_qualifier("휠 속도 센서", "dtc", 0x401011), "DTC_401011")
        self.assertEqual(make_qualifier("차대번호", "did", 0xF190), "DID_F190")
        self.assertEqual(make_qualifier("온도", "data_object", 7), "DataObject_7")
        self.assertEqual(make_qualifier("휠 Wheel 속도 speed"), "Wheel_speed")
        self.assertTrue(qualifier_follows("휠", "DTC_401011", "dtc", 0x401011))
        self.assertFalse(qualifier_follows("Wheel", "Custom", "dtc", 1))

    def test_filter_terms(self):
        self.assertEqual(filter_terms('pump  "Short To" front'), ["pump", "short to", "front"])
        self.assertEqual(filter_terms('"open phrase'), ["open phrase"])
        self.assertEqual(filter_terms('  '), [])

    def test_byte_grid_shortens_long_fields(self):
        """[DOUIG-R210] 24바이트는 그대로, 25바이트부터 앞 23바이트 + 마지막 바이트('26…', '…33' 형식)."""
        def field(first, count):
            return [{"kind": "data", "label": "F", "bit_offset": first * 8, "bits": count * 8}]
        self.assertEqual([line["byte"] for line in byte_grid(field(0, 24))], list(range(24)))
        grid = byte_grid(field(4, 30))  # 4~33
        self.assertEqual(len(grid), 24)
        self.assertEqual([line["label"] for line in grid[-3:]], ["25", "26…", "…33"])
        self.assertEqual(grid[-1]["byte"], 33)
        grid = byte_grid(field(0, 25))
        self.assertEqual([line["label"] for line in grid[-2:]], ["22…", "…24"])
        # 64바이트를 넘는 필드도 끝까지 보인다(예전 limit=64 자르기 제거).
        grid = byte_grid(field(0, 80) + [{"kind": "data", "label": "Tail", "bit_offset": 80 * 8, "bits": 8}])
        self.assertEqual(grid[-1]["byte"], 80)
        self.assertEqual(grid[-1]["cells"][0][0], "Tail")

    def test_sae_round_trip(self):
        for code in (0x402011, 0x000001, 0xC1D2E3, 0xFFFFFF):
            self.assertEqual(parse_sae_dtc(sae_dtc(code)), code)
        self.assertIsNone(parse_sae_dtc("0x402011"))


class ListQueries(unittest.TestCase):
    """작업 스레드 목록 규칙: 필터 OR·구절, 번호순 진입 정렬, 여러 열 정렬, 한 목록, 새 번호([UI-R335]), 묶음 명령, 찾기 조건."""

    def setUp(self):
        self.app = Application()
        state = self.app.new_project("List ECU", "ListECU")
        self.base = state["variants"][0]["id"]
        for code, name in ((0x401013, "Pump motor short to ground"), (0x401011, "Wheel speed front"), (0x401012, "Valve"), (0x401015, "Pump current")):
            self.run_command("create_dtc", {"variant_id": self.base, "name": name, "qualifier": make_qualifier(name), "code": hex(code)})

    def tearDown(self):
        self.app.close_project()

    def run_command(self, action, payload):
        return self.app.execute_command(action, payload, self.app.r.revision)

    def names(self, **kw):
        return [r["name"] for r in self.app.query_objects("dtc", self.base, **kw)["rows"]]

    def test_filter_or_and_phrase(self):
        self.assertEqual(sorted(self.names(filter_text='valve front')), ["Valve", "Wheel speed front"])
        self.assertEqual(self.names(filter_text='"short to"'), ["Pump motor short to ground"])
        self.assertEqual(self.names(filter_text='0x401012 wheel', sort=None), ["Wheel speed front", "Valve"])  # 번호도 단어마다
        result = self.app.query_objects("dtc", self.base, filter_text="pump", page_size=0)
        self.assertEqual((result["total"], result["unfiltered"]), (2, 4))

    def test_default_multi_sort_and_single_list(self):
        result = self.app.query_objects("dtc", self.base, sort=None, page_size=0)
        self.assertEqual([r["code"] for r in result["rows"]], [0x401011, 0x401012, 0x401013, 0x401015])  # 결정 D5 번호순
        self.assertEqual(result["sort"], [["code", False], ["name", False]])
        self.assertEqual(result["page_size"], 4)
        rows = self.app.query_objects("dtc", self.base, sorts=[["shared", False], ["name", True]], page_size=0)["rows"]
        self.assertEqual([r["name"] for r in rows], ["Wheel speed front", "Valve", "Pump motor short to ground", "Pump current"])
        self.assertEqual(Application.default_sort("diagnostic_instance"), [("ordinal", False)])
        self.assertEqual(Application.default_sort("did")[0], ("identifier", False))

    def test_suggest_number_cases(self):
        suggest = lambda ref: self.app.suggest_number("dtc", self.base, reference=ref)["number"]
        self.assertEqual(suggest(0x401011), 0x401014)  # R+1부터 올라가며 가장 작은 빈 번호
        self.assertEqual(suggest(0x401015), 0x401016)
        self.assertEqual(suggest("max"), 0x401016)  # 필터로 보이는 행이 없으면 가장 큰 번호의 객체
        other = self.run_command("create_variant", {"name": "Empty", "qualifier": "Empty", "source_id": self.base, "mode": "empty"})["selected_id"]
        self.assertEqual(self.app.suggest_number("dtc", other, reference=None)["number"], 1)  # 빈 목록: 첫 번호
        self.run_command("create_dtc", {"variant_id": other, "name": "Top", "qualifier": "Top", "code": "0xFFFFFF"})
        self.run_command("create_dtc", {"variant_id": other, "name": "Below", "qualifier": "Below", "code": "0xFFFFFE"})
        self.assertEqual(self.app.suggest_number("dtc", other, reference=0xFFFFFE)["number"], 0xFFFFFD)  # 위가 다 차면 아래로
        defaults = self.app.add_defaults("dtc", self.base, reference=0x401013)
        self.assertEqual(defaults["number"], 0x401014)
        self.assertIn("types", defaults["catalog"])

    def test_bulk_edit_commands_are_single_undo(self):
        rows = self.app.query_objects("dtc", self.base, page_size=0)["rows"]
        ids = [r["id"] for r in rows[:3]]
        self.run_command("bulk_edit", {"ids": ids, "field": "name", "value": "Same"})
        self.assertEqual(self.names().count("Same"), 3)
        self.run_command("undo", {})
        self.assertEqual(self.names().count("Same"), 0)
        with self.assertRaises(DomainError):  # 레코드 항목 템플릿이 없는 프로젝트는 cdd.* 속성을 거부한다
            self.run_command("set_dtc_attributes", {"ids": ids, "attributes": [{"key": "cdd.SetCondition", "value": "x"}]})
        self.run_command("set_dtc_attributes", {"ids": ids, "attributes": [{"key": "user.review", "value": "ok"}]})
        values = self.app.r.all("SELECT object_id FROM object_attributes WHERE key='user.review'")
        self.assertEqual({v["object_id"] for v in values}, set(ids))
        self.run_command("undo", {})
        self.assertEqual(self.app.r.all("SELECT object_id FROM object_attributes WHERE key='user.review'"), [])

    def test_qualifier_length_rules(self):
        dtc = self.app.query_objects("dtc", self.base, page_size=0)["rows"][0]["id"]
        with self.assertRaises(DomainError): self.run_command("update_common", {"id": dtc, "qualifier": "Q" * 129})
        with self.assertRaises(DomainError):
            self.run_command("create_dtc", {"variant_id": self.base, "name": "Long", "qualifier": "L" * 129, "code": "0x1"})
        # 가져온 긴 Qualifier(검사 전 값)는 그대로 두면 다른 속성을 고칠 수 있다.
        self.app.r.update("objects", {"id": dtc}, {"qualifier": "Q" * 140})
        self.run_command("update_common", {"id": dtc, "name": "Renamed", "qualifier": "Q" * 140})
        self.assertIn("Renamed", self.names())

    def test_find_document_options_and_order(self):
        find = lambda query, **kw: [r["name"] for r in self.app.find_document(query, **kw)["rows"]]
        self.assertEqual(find("pump", scope="name"), ["Pump current", "Pump motor short to ground"])  # 트리 순서 안의 이름순
        self.assertEqual(find("Pump", scope="name", case=True, whole=True), [])
        self.assertEqual(find("pump current", scope="name", whole=True), ["Pump current"])
        self.assertEqual(find("short", scope="name", word=True), ["Pump motor short to ground"])
        self.assertEqual(find("shor", scope="name", word=True), [])
        self.assertEqual(find("Valve", scope="qualifier"), ["Valve"])
        self.assertEqual(find("0x401012"), ["Valve"])
        rows = self.app.find_document("Pump", scope="name")["rows"]
        self.assertTrue(all(r["key"] == f"var:{self.base}:dtc" for r in rows))
        self.assertEqual(self.app.find_document("3E 00", scope="bytes")["rows"], [])
        with self.assertRaises(DomainError): self.app.find_document("nothex", scope="bytes")


def _wait(window, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if not window.pending and not window._command_queue and not window.selection_timer.isActive():
            for _ in range(6):
                QApplication.processEvents(); time.sleep(0.005)
            if not window.pending and not window._command_queue and not window.selection_timer.isActive(): return
        time.sleep(0.005)
    raise AssertionError("작업 스레드 응답 시간 초과")


@unittest.skipIf(QApplication is None, "PySide6 없음")
class MainWindowEditingTest(unittest.TestCase):
    """실제 작업 스레드를 쓰는 주 창: 칸 편집(F2·Enter·Tab·마지막 칸 Tab), 여러 행 입력, 진입 규칙, 정렬, 복사, 찾기, 오른쪽 버튼."""

    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        import diagstudio.ui as ui
        cls.ui = ui
        cls.messages = []
        messages = cls.messages

        class FakeBox:
            StandardButton = ui.QMessageBox.StandardButton
            ButtonRole = ui.QMessageBox.ButtonRole
            @staticmethod
            def warning(*a, **k): messages.append(("warning",) + a[1:])
            @staticmethod
            def information(*a, **k): messages.append(("information",) + a[1:])
            @staticmethod
            def question(*a, **k): messages.append(("question",) + a[1:]); return FakeBox.StandardButton.Yes
        cls.original_box = ui.QMessageBox
        ui.QMessageBox = FakeBox
        cls.window = ui.MainWindow()
        cls.window.show()
        _wait(cls.window)
        w = cls.window
        w.submit("new_project", {"name": "Editing ECU", "technical_name": "EditingECU"}, w.apply_state); _wait(w)
        cls.base = w.state["variants"][0]["id"]
        for code, name in ((0x401013, "Pump motor short to ground"), (0x401011, "Wheel speed front"), (0x401012, "Valve")):
            w.command("create_dtc", {"variant_id": cls.base, "name": name, "qualifier": make_qualifier(name), "code": hex(code)})
        _wait(w)

    @classmethod
    def tearDownClass(cls):
        cls.window.thread.quit(); cls.window.thread.wait()
        cls.ui.QMessageBox = cls.original_box

    def setUp(self):
        w = self.window
        self.assertTrue(w.select_node(f"var:{self.base}:dtc"))
        w.tree_selected(); _wait(w)

    def column(self, field):
        return [f for f, _ in self.window.model.columns].index(field)

    def row_of(self, code):
        return next(i for i, r in enumerate(self.window.model.rows) if r["code"] == code)

    def edit(self, row, field, text=None, key=Qt.Key.Key_Return):
        w = self.window
        w.table.setCurrentIndex(w.model.index(row, self.column(field)))
        w.edit_cell()
        self.assertEqual(w.table.state(), QAbstractItemView.State.EditingState)
        editor = self.editor()
        if text is not None: editor.setText(text)
        QTest.keyClick(editor, key)

    def editor(self):
        """열린 칸 편집기(창이 비활성이어도 찾는다)."""
        return self.window.table.indexWidget(self.window.table.currentIndex())

    def test_entry_selects_first_row_sorted_by_number(self):
        """[UI-R100-Selection]·[LIST-R600] 첫 행 선택, [UI-R100-Sort]·결정 D5 번호순, [LIST-R250] 정렬 표시."""
        w = self.window
        self.assertEqual([r["code"] for r in w.model.rows], [0x401011, 0x401012, 0x401013])
        self.assertEqual(w.selected_rows()[0]["code"], 0x401011)
        self.assertIn("▲", w.model.headerData(0, Qt.Orientation.Horizontal))
        self.assertEqual(w.page_label.text().split("  ·  ")[0], "3개 객체")

    def test_cell_edit_name_follows_qualifier_and_undo(self):
        """[LIST-R410] F2 → 입력 → Enter = 명령 하나, [QUAL-R020] 이름에서 만든 Qualifier는 따라간다. Undo 한 번에 되돌린다."""
        w = self.window
        self.edit(self.row_of(0x401012), "name", "Valve stuck"); _wait(w)
        row = w.model.rows[self.row_of(0x401012)]
        self.assertEqual((row["name"], row["qualifier"]), ("Valve stuck", "Valve_stuck"))
        w.command("undo", {}); _wait(w)
        row = w.model.rows[self.row_of(0x401012)]
        self.assertEqual((row["name"], row["qualifier"]), ("Valve", "Valve"))

    def test_escape_cancels_and_non_editable_column_reports_reason(self):
        w = self.window
        revision = w.state["revision"]
        self.edit(0, "name", "Discarded", Qt.Key.Key_Escape); _wait(w)
        self.assertEqual(w.state["revision"], revision)
        self.assertEqual(w.model.rows[0]["name"], "Wheel speed front")
        w.table.setCurrentIndex(w.model.index(0, self.column("shared")))
        w.edit_cell()
        self.assertNotEqual(w.table.state(), QAbstractItemView.State.EditingState)
        self.assertIn("속성 대화상자", w.statusBar().currentMessage())

    def test_tab_moves_to_next_editable_cell_and_last_tab_creates(self):
        """[UI-R300-TAB] Tab = 확정 후 다음 편집 칸, [UI-R300-TABLast] 마지막 행 마지막 편집 칸의 Tab = 새 요소([UI-R335] 번호)."""
        w = self.window
        self.edit(self.row_of(0x401011), "code", "0x401021", Qt.Key.Key_Tab)
        for _ in range(10): QApplication.processEvents(); time.sleep(0.01)
        self.assertEqual(w.table.state(), QAbstractItemView.State.EditingState)
        self.assertEqual(w.model.columns[w.table.currentIndex().column()][0], "name")
        QTest.keyClick(self.editor(), Qt.Key.Key_Escape); _wait(w)
        self.assertEqual([r["code"] for r in w.model.rows], [0x401012, 0x401013, 0x401021])
        last = len(w.model.rows) - 1
        self.edit(last, "qualifier", None, Qt.Key.Key_Tab); _wait(w); _wait(w)
        codes = [r["code"] for r in w.model.rows]
        self.assertEqual(codes[-1], 0x401022)
        new = w.model.rows[-1]
        self.assertEqual(new["qualifier"], "DTC_401022")  # 결정 D3
        self.assertEqual(w.table.state(), QAbstractItemView.State.EditingState)
        QTest.keyClick(self.editor(), Qt.Key.Key_Escape); _wait(w)
        w.command("undo", {}); _wait(w); w.command("undo", {}); _wait(w)
        self.assertEqual([r["code"] for r in w.model.rows], [0x401011, 0x401012, 0x401013])

    def test_multi_row_input_is_one_command_and_keeps_selection(self):
        """[LIST-R420] 여러 행을 고르고 한 칸을 F2로 확정하면 모든 선택 행에 넣는다(Undo 한 번). 선택은 그대로 남는다."""
        w = self.window
        w.table.selectAll()
        w.table.selectionModel().setCurrentIndex(w.model.index(2, self.column("name")), QItemSelectionModel.SelectionFlag.NoUpdate)
        revision = w.state["revision"]
        w.edit_cell(); editor = self.editor(); editor.setText("Same"); QTest.keyClick(editor, Qt.Key.Key_Return); _wait(w)
        self.assertEqual([r["name"] for r in w.model.rows], ["Same"] * 3)
        self.assertEqual(w.state["revision"], revision + 1)
        self.assertEqual(len(w.selected_rows()), 3)
        w.command("undo", {}); _wait(w)
        self.assertNotIn("Same", [r["name"] for r in w.model.rows])

    def test_rejected_edit_restores_value_and_reopens_editor(self):
        w = self.window
        self.edit(self.row_of(0x401013), "code", "0x401011"); _wait(w)  # 중복 코드 → 거부
        self.assertEqual(sorted(r["code"] for r in w.model.rows), [0x401011, 0x401012, 0x401013])
        for _ in range(10): QApplication.processEvents(); time.sleep(0.01)
        self.assertEqual(w.table.state(), QAbstractItemView.State.EditingState)
        self.assertEqual(self.editor().text(), "0x401011")
        QTest.keyClick(self.editor(), Qt.Key.Key_Escape); _wait(w)
        self.assertFalse(any(m[0] == "warning" for m in self.messages))  # 칸 편집 거부는 모달 창 대신 상태 줄·출력 창

    def test_header_sort_multi_and_memory(self):
        """[LIST-R200]·[LIST-R210] 머리 누르기·Ctrl+누르기, [LIST-R240] 같은 목록에 다시 들어오면 정렬 유지."""
        w = self.window
        w.header_clicked(self.column("name")); _wait(w)
        self.assertEqual([r["name"] for r in w.model.rows], ["Pump motor short to ground", "Valve", "Wheel speed front"])
        w.header_clicked(self.column("name")); _wait(w)
        self.assertEqual(w.model.rows[0]["name"], "Wheel speed front")
        self.assertIn("▼", w.model.headerData(self.column("name"), Qt.Orientation.Horizontal))
        self.assertTrue(w.select_node("types")); w.tree_selected(); _wait(w)
        self.assertTrue(w.select_node(f"var:{self.base}:dtc")); w.tree_selected(); _wait(w)
        self.assertEqual(w.model.rows[0]["name"], "Wheel speed front")
        w._sort_memory.clear(); w.tree_selected(); _wait(w)

    def test_copy_table_uses_visible_headers(self):
        """[LIST-R100] Ctrl+C는 보이는 그대로: 머리 줄은 화면 열 이름."""
        w = self.window
        w.table.selectRow(0); w.copy_table()
        header = QApplication.clipboard().text().splitlines()[0].split("\t")
        self.assertEqual(header, [label for _, label in w.model.columns])
        self.assertNotIn("qualifier", header)

    def test_keyboard_shortcuts(self):
        w = self.window
        self.assertEqual(w.actions["edit_cell"].shortcut().toString(), "F2")
        self.assertIn("F9", [k.toString() for k in w.actions["validate"].shortcuts()])
        self.assertIn("F5", [k.toString() for k in w.actions["validate"].shortcuts()])
        self.assertEqual(w.actions["find_next"].shortcut().toString(), "F3")
        self.assertIn("Alt+Return", [k.toString() for k in w.actions["edit"].shortcuts()])
        w.tree.setFocus(); w.cycle_focus(1)
        self.assertIs(w.focusWidget(), w.table)
        w.cycle_focus(1)
        self.assertTrue(w.detail_tabs.isAncestorOf(w.focusWidget()))
        w.cycle_focus(1)
        self.assertIs(w.focusWidget(), w.tree)

    def test_filter_chip_and_node_entry_reset(self):
        w = self.window
        w.search.setText("pump valve"); w.search_now(); _wait(w)
        self.assertEqual(len(w.model.rows), 2)
        self.assertEqual(w.filter_chip.text(), "2 / 3 표시")
        self.assertFalse(w.filter_chip.isHidden())
        w.tree_selected(); _wait(w)  # [UI-R100-Filter] 노드에 들어가면 필터를 비운다
        self.assertEqual(w.search.text(), "")
        self.assertTrue(w.filter_chip.isHidden())

    def test_right_click_keeps_multi_selection_and_empty_area_menu(self):
        """[UI-R350-Element] 선택된 행 위의 오른쪽 버튼은 여러 행 선택을 유지, [UI-R350-Empty] 빈 곳은 새로 만들기 메뉴."""
        w = self.window
        captured = {}

        class FakeMenu(QMenu):
            def exec(self, *a):
                captured["items"] = [x.text() for x in self.actions() if x.text()]
        self.ui.QMenu = FakeMenu
        try:
            w.table.selectAll(); _wait(w)
            w.table_menu(w.table.visualRect(w.model.index(1, 1)).center())
            self.assertEqual(len(w.selected_rows()), 3)
            self.assertIn("참조 영향…", captured["items"])
            self.assertEqual(w.table.currentIndex().row(), 1)
            w.table.selectRow(0); _wait(w)
            w.table_menu(w.table.visualRect(w.model.index(2, 1)).center())
            self.assertEqual([r["code"] for r in w.selected_rows()], [0x401013])  # 새 행으로 선택이 바뀐다
            below = w.table.visualRect(w.model.index(w.model.rowCount() - 1, 0)).bottomLeft()
            below.setY(below.y() + 40)
            w.table_menu(below)
            self.assertIn("새로 만들기…", captured["items"])
        finally:
            self.ui.QMenu = QMenu

    def test_find_dialog_next_and_all(self):
        """[SRCH-R150] 다음 찾기, [SRCH-R150-Closed] 창을 닫아도 F3, [SRCH-R200] 모두 찾기는 출력 창 링크."""
        w = self.window
        w.find_document_dialog(); d = w.find_dialog
        self.assertFalse(d.isModal())
        d.query.setCurrentText("pump"); d.scope.setCurrentIndex(d.scope.findData("name"))
        d.request("first"); _wait(w); _wait(w)
        self.assertTrue(d.status.text().startswith("1 / 1"))
        self.assertEqual(w.selected_rows()[0]["code"], 0x401013)
        before = len(w.log.entries)
        d.request("all"); _wait(w)
        links = [e for e in w.log.entries[before:] if e["target"]]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["origin"], "찾기")
        d.close()
        w.find_next(); _wait(w); _wait(w)
        self.assertIn("처음부터", w.statusBar().currentMessage())
        self.assertEqual(d.history()[0], "pump")


@unittest.skipIf(QApplication is None, "PySide6 없음")
class RecordItemEditingTest(unittest.TestCase):
    """ABS 예제의 DTC 레코드 항목: 열거 칸은 콤보, 여러 DTC에 한 번에 넣기, '선택한 DTC에 이 값 복사'([DTCUI-R035])."""

    @classmethod
    def setUpClass(cls):
        if not ABS.is_file(): raise unittest.SkipTest(f"예제 CDD 없음: {ABS}")
        cls.qt = QApplication.instance() or QApplication([])
        import diagstudio.ui as ui
        cls.ui = ui
        cls.original_box = ui.QMessageBox

        class FakeBox:
            StandardButton = ui.QMessageBox.StandardButton
            @staticmethod
            def warning(*a, **k): pass
            @staticmethod
            def information(*a, **k): pass
        ui.QMessageBox = FakeBox
        cls.window = w = ui.MainWindow(); w.show(); _wait(w)
        got = {}
        w.submit("cdd_preview", {"path": str(ABS)}, lambda p: got.update(p=p)); _wait(w)
        w.submit("apply_cdd", {"preview_id": got["p"]["preview_id"]}, w.apply_state); _wait(w, 60)
        cls.base = next(v for v in w.state["variants"] if v["is_base"])["id"]

    @classmethod
    def tearDownClass(cls):
        cls.window.thread.quit(); cls.window.thread.wait()
        cls.ui.QMessageBox = cls.original_box

    def test_enum_combo_bulk_and_copy_value(self):
        w = self.window
        self.assertTrue(w.select_node(f"var:{self.base}:dtc")); w.tree_selected(); _wait(w)
        fields = [f for f, _ in w.model.columns]
        action = fields.index("attr:cdd.CorrectiveAction")
        w.table.selectRow(0); _wait(w)
        w.table.setCurrentIndex(w.model.index(0, action)); w.edit_cell()
        editor = w.table.indexWidget(w.table.currentIndex())
        self.assertIsInstance(editor, QComboBox)
        self.assertEqual(editor.findData(None), -1)  # 필수 열거는 비울 수 없다
        editor.setCurrentIndex(editor.findData(0)); QTest.keyClick(editor, Qt.Key.Key_Return); _wait(w)
        self.assertEqual((w.model.rows[0]["attr:cdd.CorrectiveAction"], w.model.rows[0]["raw:cdd.CorrectiveAction"]), ("Replace ECU", "0"))
        selection = w.table.selectionModel()
        for row in (1, 2): selection.select(w.model.index(row, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        _wait(w)
        revision = w.state["revision"]
        w.copy_value_to_selection(w.model.rows[0], "attr:cdd.CorrectiveAction"); _wait(w)
        self.assertEqual([w.model.rows[i]["attr:cdd.CorrectiveAction"] for i in range(3)], ["Replace ECU"] * 3)
        self.assertEqual(w.state["revision"], revision + 1)
        selection.setCurrentIndex(w.model.index(2, fields.index("attr:cdd.SetCondition")), QItemSelectionModel.SelectionFlag.NoUpdate)
        w.edit_cell(); editor = w.table.indexWidget(w.table.currentIndex()); editor.setText("Voltage < 9V"); QTest.keyClick(editor, Qt.Key.Key_Return); _wait(w)
        self.assertEqual([w.model.rows[i]["attr:cdd.SetCondition"] for i in range(3)], ["Voltage < 9V"] * 3)
        for _ in range(3): w.command("undo", {}); _wait(w)
        self.assertEqual(w.model.rows[0]["attr:cdd.CorrectiveAction"], "No known corrective action")


@unittest.skipIf(QApplication is None, "PySide6 없음")
class LayoutPaneLinkTest(unittest.TestCase):
    def setUp(self):
        self.qt = QApplication.instance() or QApplication([])

    def test_grid_and_member_selection_are_linked(self):
        """[DOUIG-R120] 격자 칸 → 멤버 행, 멤버 행 → 그 필드의 격자 칸 모두."""
        from diagstudio.layout_view import LayoutPane
        layout = [{"kind": "data", "label": "Speed", "bit_offset": 0, "bits": 16, "encoding": "unsigned", "type_name": "U16"},
                  {"kind": "data", "label": "Mode", "bit_offset": 16, "bits": 8, "encoding": "unsigned", "type_name": "U8"}]
        pane = LayoutPane(layout)
        pane._grid_clicked(1, 1)  # 두 번째 바이트 = Speed
        self.assertEqual(pane.members.currentRow(), 0)
        # 멤버 표는 칸 단위 단일 선택(SelectItems·SingleSelection)이라 selectRow는 아무것도 고르지 않는다. 사용자처럼 칸을 고른다.
        pane.members.setCurrentCell(1, 2)
        cells = {(i.row(), i.column()) for i in pane.grid.selectedIndexes()}
        self.assertTrue(cells and all(r == 2 for r, _ in cells))


if __name__ == "__main__":
    unittest.main(verbosity=2)
