"""빌드된 EXE로 CDD 가져오기 → DID 표시 → 속성 → 저장을 실제 Windows UI에서 확인한다.

실행: .venv\\Scripts\\python.exe build\\smoke_cdd_gui.py [CDD 경로]
결과: build\\checks\\gui-events.json, *.png. 사용자 프로필을 건드리지 않도록 LOCALAPPDATA를 격리한다.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from pywinauto import Desktop, clipboard

ROOT = Path(__file__).resolve().parents[1]
EXE = Path(os.environ.get("SMOKE_EXE", ROOT / "dist" / "DiagAuthor.dist" / "DiagAuthor.exe"))
OUT = ROOT / "build" / "checks" / os.environ.get("SMOKE_TAG", "")
OUT.mkdir(parents=True, exist_ok=True)
PROFILE = OUT / "profile"
PROFILE.mkdir(exist_ok=True)
CDD = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\Public\Documents\Vector\CANdelaStudio\13.0\Examples\ABS_ESP-Example.cdd")
PROJECT = OUT / "AbsImported.diagproj"
for stale in (PROJECT, PROJECT.with_name(PROJECT.name + ".bak")):
    if stale.exists(): stale.unlink()

events = []


def log(step, **data):
    events.append({"step": step, "t": round(time.monotonic() - START, 2), **data})


def until(predicate, seconds=30, what="UI"):
    limit = time.monotonic() + seconds
    while time.monotonic() < limit:
        try:
            value = predicate()
        except Exception:
            value = None
        if value:
            return value
        time.sleep(0.3)
    raise AssertionError(f"{what} 응답 시간 초과")


def windows(pid):
    return [w for w in Desktop(backend="uia").windows(process=pid) if w.is_visible()]


def find_window(pid, fragment):
    for top in windows(pid):
        if fragment in top.window_text():
            return top
        for child in top.descendants(control_type="Window"):
            if fragment in child.window_text():
                return child
    return None


def element_text(w):
    """Qt 읽기 전용 QPlainTextEdit은 UIA 노출 방식이 일정하지 않아 여러 경로로 읽는다."""
    candidates = [w.window_text()]
    for getter in (lambda: w.iface_value.CurrentValue,
                   lambda: w.iface_text.DocumentRange.GetText(-1),
                   lambda: w.legacy_properties().get("Value")):
        try: candidates.append(getter())
        except Exception: pass
    return max((c for c in candidates if isinstance(c, str)), key=len, default="")


def texts(window, kinds=("Text", "Edit", "Document")):
    result = []
    for kind in kinds:
        for w in window.descendants(control_type=kind):
            value = element_text(w)
            if value: result.append(value)
    return result


def press(window, *names):
    """포커스와 무관하게 UIA Invoke로 버튼을 누른다. 다른 창이 전면에 있어도 동작한다.

    주의: Qt의 UIA Invoke는 동기 실행이라 모달 대화상자를 여는 버튼(저장 등)에 쓰면 대화상자가 닫힐 때까지
    반환되지 않는다. 대화상자를 닫는 버튼(OK)에만 사용한다.
    """
    for b in window.descendants(control_type="Button"):
        if b.window_text().replace("&", "") in names:
            b.invoke()
            return b.window_text()
    raise AssertionError(f"버튼 없음: {names}")


def shot(window, name):
    window.capture_as_image().save(OUT / name)


START = time.monotonic()
env = os.environ.copy()
env["LOCALAPPDATA"] = str(PROFILE)
env["QT_QPA_PLATFORM"] = "windows"
process = subprocess.Popen([str(EXE), str(CDD)], cwd=EXE.parent, env=env)
ok = False
try:
    log("launch", exe=str(EXE), cdd=str(CDD), cdd_exists=CDD.is_file())
    box = until(lambda: find_window(process.pid, "CDD 가져오기 범위"), 60, "CDD 미리보기 창")
    log("preview_dialog", texts=texts(box, ("Text",))[:6])
    shot(box, "01-cdd-preview.png")
    log("pressed", button=press(box, "OK", "확인"))
    main = until(lambda: next((w for w in windows(process.pid) if "— DiagAuthor" in w.window_text()), None), 120, "가져오기 결과 창")
    until(lambda: "개 객체" in " ".join(texts(main, ("Text",))), 30, "DID 표")
    time.sleep(1)
    log("imported", title=main.window_text(), labels=[t for t in texts(main, ("Text",)) if "DID" in t or "객체" in t or "revision" in t])
    shot(main, "02-imported.png")

    cells = [c for c in main.descendants(control_type="DataItem") if c.window_text().startswith("0x")]
    log("did_cells", count=len(cells), first=[c.window_text() for c in cells[:5]])
    # 다른 창이 표를 가리면 클릭이 그 창으로 간다. 앱을 최대화·전면으로 올린 뒤 클릭한다.
    main.maximize(); main.set_focus(); time.sleep(0.5)
    cells = [c for c in main.descendants(control_type="DataItem") if c.window_text().startswith("0x")]
    target = next((c for c in cells if c.window_text() == "0x010B"), cells[0] if cells else None)
    if target is None: raise AssertionError("DID 셀을 찾지 못함")
    try:
        target.select()  # UIA SelectionItem: 포커스 불필요
        how = "select"
    except Exception:
        main.set_focus(); target.click_input()
        how = "click"
    log("select_did", method=how)
    time.sleep(2)
    try:
        props = until(lambda: [t for t in texts(main, ("Edit", "Document")) if "데이터 구조" in t], 10, "속성 창 데이터 구조")
    except AssertionError:
        props = []  # UIA로 읽지 못하면 03 캡처로 판정한다.
    log("details", did=target.window_text(), text=props[:1], readable=bool(props))
    if props: assert "0.1" in props[0] and "-13" in props[0], "Acceleration 변환식(×0.1, -13)이 속성 창에 없음"
    # U2: DID 마스터·디테일. 목록 아래 '데이터 배치' 탭의 멤버 표에 멤버 이름과 변환식 설명이 나와야 한다.
    member_cells = until(lambda: (lambda cells: cells if "Acceleration" in cells and any("×0.1 -13" in c for c in cells) else None)(
        [c.window_text() for c in main.descendants(control_type="DataItem")]), 15, "DID 멤버 표(데이터 배치)")
    log("did_layout", members=[c for c in member_cells if c in ("Voltage", "Acceleration")], formula=[c for c in member_cells if "×0.1" in c][:1])
    shot(main, "03-did-details.png")
    diag_items = []
    for e in main.descendants(control_type="Edit"):
        try: foc = e.has_keyboard_focus()
        except Exception: foc = "?"
        try: aid = e.element_info.automation_id
        except Exception: aid = "?"
        diag_items.append({"focus": foc, "aid": aid, "text": element_text(e)[:80]})
    log("edit_dump", edits=diag_items)

    # U1: 경로 창 기본값은 이름 경로("/..." 형식)이며, UUID(diag://) 경로는 "안정 경로 복사"가 클립보드에만 넣는다.
    # 경로 입력창은 Ctrl+L로 포커스를 주어 검색창과 구분한다(Qt objectName은 UIA automation_id로 노출되지 않는다).
    main.set_focus(); main.type_keys("^l"); time.sleep(0.5)
    def path_box():
        # Ctrl+L로 포커스를 받은 편집 상자. 포커스를 UIA로 읽지 못하면 objectName(objectPath)이 든 automation id로 찾는다.
        edits = main.descendants(control_type="Edit")
        for e in edits:
            try:
                if e.has_keyboard_focus(): return e
            except Exception: pass
        for e in edits:
            try:
                if (e.element_info.automation_id or "").endswith("objectPath"): return e
            except Exception: pass
        return None
    path_edit = until(path_box, 15, "경로 입력창")
    old_path = element_text(path_edit)
    press(main, "이름 경로 복사")
    path_text = until(lambda: (lambda t: t if t.startswith("/") and t != old_path else None)(element_text(path_edit)), 15, "이름 경로 복사 반영")
    log("copied_path", path=path_text)
    try: clipboard.EmptyClipboard()
    except Exception: pass
    press(main, "안정 경로 복사")
    stable_text = until(lambda: (lambda t: t if t.startswith("diag://") else None)(clipboard.GetData() or ""), 15, "안정 경로 클립보드")
    log("stable_path", path=stable_text)
    path_edit.set_edit_text(path_text)
    path_edit.type_keys("{ENTER}")
    time.sleep(2)
    try:
        wins = [(w.window_text()[:60], w.is_visible()) for w in Desktop(backend="uia").windows(process=process.pid)]
    except Exception as e:
        wins = [repr(e)]
    log("after_enter", windows=wins, entry=element_text(path_edit))
    until(lambda: any(c.window_text() == "0x010B" for c in main.descendants(control_type="DataItem")), 15, "경로 이동 후 DID 목록")
    log("navigated_path", target="0x010B")
    shot(main, "06-object-path.png")
    did_cell = next(c for c in main.descendants(control_type="DataItem") if c.window_text() == "0x010B")
    # 우클릭이 빗나가거나 메뉴가 늦게 열리면 열거가 메뉴바만 잡는다. "참조 영향…" 항목이 보일 때까지 다시 시도한다.
    # 24차(M2-4 [UI-R1230]): 다른 대화상자를 여는 메뉴 글자는 "…"로 끝난다.
    context_items = []
    log("rightclick_diag", cell=str(did_cell.rectangle()), main=str(main.rectangle()))
    for _ in range(6):
        main.set_focus(); time.sleep(0.3)  # 25차: 다른 모니터·원격 세션에서 첫 클릭이 창 활성화에만 쓰이는 경우
        did_cell.right_click_input()
        time.sleep(1.0)
        context_items = [x.window_text() for top in windows(process.pid) for x in top.descendants(control_type="MenuItem")]
        if "참조 영향…" in context_items:
            break
    log("did_context_menu", items=context_items)
    assert "경로 복사" in context_items and "관련 데이터 타입으로 이동" in context_items, "DID 컨텍스트 메뉴 누락"
    reference_item = next(x for top in windows(process.pid) for x in top.descendants(control_type="MenuItem") if x.window_text() == "참조 영향…")
    reference_item.click_input()
    impact = until(lambda: find_window(process.pid, "참조 영향 ·"), 15, "참조 영향 창")
    impact_cells = [x.window_text() for x in impact.descendants(control_type="DataItem")]
    assert "간접 영향" in impact_cells and "서비스" in impact_cells, "DID의 간접 서비스 영향 누락"
    log("reference_impact", title=impact.window_text(), cells=impact_cells[:30])
    shot(impact, "09-reference-impact.png")
    press(impact, "닫기")

    # 탐색 트리: 기본 변형의 진단 클래스 노드를 고르면 인스턴스 목록(식별자 F190 …)이 나와야 한다.
    node = until(lambda: next((t for t in main.descendants(control_type="TreeItem") if t.window_text().startswith("Identification (")), None), 10, "Identification 트리 노드")
    listed = lambda: any(c.window_text() == "F190" for c in main.descendants(control_type="DataItem"))
    how = "select"
    try:
        node.select()
        until(listed, 4, "클래스 인스턴스 목록(select)")
    except Exception:
        # Qt 트리 항목의 UIA Select가 선택 모델에 반영되지 않는 경우가 있어 실제 클릭으로 확인한다.
        how = "click"
        main.set_focus(); time.sleep(0.3); node.click_input()
    until(listed, 15, "클래스 인스턴스 목록")
    log("tree_class", node=node.window_text(), method=how,
        tree=[t.window_text() for t in main.descendants(control_type="TreeItem")][:24])
    shot(main, "05-tree-class.png")

    project_menu = next(m for m in main.descendants(control_type="MenuItem") if m.window_text() == "프로젝트")
    project_menu.click_input()
    menu_items = [x.window_text() for top in windows(process.pid) for x in top.descendants(control_type="MenuItem")]
    log("project_menu", items=menu_items)
    assert all(label in menu_items for label in ("Qualifier 개요…", "Attribute 개요…", "상태 그래프…", "저장된 프로젝트와 비교…")), "프로젝트 메뉴 누락"
    graph_item = next(x for top in windows(process.pid) for x in top.descendants(control_type="MenuItem") if x.window_text() == "상태 그래프…")
    graph_item.click_input()
    graph = until(lambda: find_window(process.pid, "상태 그래프"), 15, "상태 그래프 창")
    log("state_graph", title=graph.window_text())
    shot(graph, "07-state-graph.png")
    press(graph, "닫기")
    main.set_focus()
    next(m for m in main.descendants(control_type="MenuItem") if m.window_text() == "프로젝트").click_input()
    qualifier_item = next(x for top in windows(process.pid) for x in top.descendants(control_type="MenuItem") if x.window_text() == "Shortcut Qualifier 개요…")
    qualifier_item.click_input()
    shortcut = until(lambda: find_window(process.pid, "Shortcut Qualifier 개요"), 15, "Shortcut Qualifier 개요")
    log("shortcut_overview", title=shortcut.window_text(), visible_rows=len(shortcut.descendants(control_type="DataItem")))
    shot(shortcut, "08-shortcut-overview.png")
    press(shortcut, "닫기")

    main.set_focus()
    next(m for m in main.descendants(control_type="MenuItem") if m.window_text() == "가져오기 / 내보내기").click_input()
    next(x for top in windows(process.pid) for x in top.descendants(control_type="MenuItem") if x.window_text() == "현재 변형의 ODX 사전 검사").click_input()
    summary = until(lambda: next((t for t in texts(main, ("Text",)) if "출력 범위:" in t and "출력 차단" in t), None), 30, "변형 출력 사전 검사")
    # Qt 표 칸은 화면에 보이는 행만 UIA로 노출된다(보이지 않는 칸은 invisible — DID 19개 중 9칸만 읽힌 것과 같다).
    # 검증 결과가 나오면 앱이 결과 창을 늘리므로(24차) 늘어난 뒤의 칸을 다시 읽는다.
    scope_cells = until(lambda: (lambda cells: cells if "범위 밖" in cells else None)(
        [c.window_text() for c in main.descendants(control_type="DataItem")]), 10, "출력 범위 밖의 보존 항목 표시")
    log("export_preflight", summary=summary, outside_visible=scope_cells.count("범위 밖"))
    shot(main, "11-export-preflight.png")

    # Copy an imported editable type through the built UI, then verify Undo/Redo.
    # Qt 트리는 화면에 실현된 항목만 UIA로 보여 스크롤 위치에 따라 "데이터 타입" 노드를 찾지 못한다(04:29 실행 실패).
    # 앱의 노드 경로(diag://<프로젝트>/_/node/types)로 이동해 트리 표시 상태와 무관하게 타입 목록을 연다.
    main.set_focus()
    project_uuid = stable_text.split("/")[2]
    main.set_focus(); main.type_keys("^l"); time.sleep(0.5)
    path_edit = until(path_box, 15, "객체 경로 입력창")
    path_edit.set_edit_text(f"diag://{project_uuid}/_/node/types")
    path_edit.type_keys("{ENTER}")
    # 25차: 검증 결과로 결과 창이 늘어나 목록에 2~3행만 보이면(UIA는 보이는 칸만 노출) '_Byte' 칸이 없을 수 있다.
    # 목록 머리 글 '데이터 타입 (n)'과 페이지 글 'n개 객체'로 목록이 열렸는지 본다. 다음 단계의 검색이 대상 행을 보이게 한다.
    until(lambda: any(t.startswith("데이터 타입 (") for t in texts(main, ("Text",))) and any("개 객체" in t for t in texts(main, ("Text",))), 15, "데이터 타입 목록")
    log("types_node", via="node path")
    main.type_keys("^f")
    main.type_keys("Voltage_1_Byte", with_spaces=True)
    time.sleep(0.7)
    type_cell = until(lambda: next((c for c in main.descendants(control_type="DataItem") if c.window_text() == "Voltage_1_Byte"), None), 15, "복사할 타입")
    type_cell.click_input()
    next(m for m in main.descendants(control_type="MenuItem") if m.window_text() == "편집").click_input()
    next(x for top in windows(process.pid) for x in top.descendants(control_type="MenuItem") if x.window_text() == "데이터 타입 독립 복사…").click_input()
    copy_name = until(lambda: find_window(process.pid, "데이터 타입 독립 복사"), 15, "타입 사본 이름")
    next(b for b in copy_name.descendants(control_type="Button") if b.window_text().replace("&", "") in ("OK", "확인")).click_input()
    def qualifier_prompt():
        candidate = find_window(process.pid, "데이터 타입 독립 복사")
        return candidate if candidate and "새 Qualifier" in " ".join(texts(candidate, ("Text",))) else None
    copy_qualifier = until(qualifier_prompt, 15, "타입 사본 Qualifier")
    next(b for b in copy_qualifier.descendants(control_type="Button") if b.window_text().replace("&", "") in ("OK", "확인")).click_input()
    copied_visible = lambda: any(c.window_text() == "Voltage_1_Byte_Copy" for c in main.descendants(control_type="DataItem"))
    until(copied_visible, 15, "타입 사본 생성")
    main.set_focus();main.type_keys("^z")
    until(lambda: not copied_visible(), 15, "타입 복사 실행 취소")
    main.type_keys("^y")
    until(copied_visible, 15, "타입 복사 다시 실행")
    log("type_copy_undo_redo", qualifier="Voltage_1_Byte_Copy")
    shot(main, "10-independent-type.png")

    # 모달 저장 창을 여는 버튼은 Invoke가 막히므로 전면화 후 실제 클릭한다.
    main.set_focus(); time.sleep(0.5)
    next(b for b in main.descendants(control_type="Button") if b.window_text().replace("&", "") == "저장").click_input()
    dialog = until(lambda: find_window(process.pid, "프로젝트 저장"), 30, "저장 창")
    try:
        # Windows 공용 저장 창: 파일 이름 편집 상자 auto_id 1001, 저장 버튼 auto_id 1
        dialog.child_window(auto_id="1001", control_type="Edit").set_edit_text(str(PROJECT))
        dialog.child_window(auto_id="1", control_type="Button").invoke()
        log("save_dialog", method="uia")
    except Exception as e:
        log("save_dialog_fallback", error=repr(e))
        dialog.set_focus()
        dialog.type_keys("^a", set_foreground=True)
        dialog.type_keys(str(PROJECT), with_spaces=True, set_foreground=True)
        dialog.type_keys("{ENTER}")
    until(lambda: PROJECT.exists(), 30, "프로젝트 파일 생성")
    main = until(lambda: next((w for w in windows(process.pid) if "— DiagAuthor" in w.window_text() and " * " not in w.window_text()), None), 30, "저장 완료 표시")
    log("saved", path=str(PROJECT), bytes=PROJECT.stat().st_size, title=main.window_text())
    shot(main, "04-saved.png")
    ok = True
except Exception as exc:
    try:
        every = [(w.window_text(), w.is_visible()) for w in Desktop(backend="uia").windows(process=process.pid)]
    except Exception as e:
        every = [repr(e)]
    log("failure", error=repr(exc), returncode=process.poll(), windows=every)
    try:
        from PIL import ImageGrab
        ImageGrab.grab(all_screens=True).save(OUT / "99-failure.png")
    except Exception as e:
        log("screenshot_failed", error=repr(e))
finally:
    app_log = PROFILE / "DiagAuthor" / "logs" / "application.log"
    log("app_log", text=app_log.read_text(encoding="utf-8") if app_log.exists() else "")
    if process.poll() is None:
        process.kill()
    (OUT / "gui-events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(events, ensure_ascii=False, indent=2))
sys.exit(0 if ok else 1)
