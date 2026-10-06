"""Exercise the packaged EXE through visible Windows UI controls."""
import json
import os
from pathlib import Path
import subprocess
import time

from pywinauto import Desktop


ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "dist" / "DiagAuthor.dist" / "DiagAuthor.exe"
OUT = ROOT / "build" / "runtime-smoke"
OUT.mkdir(parents=True, exist_ok=True)
PROFILE = OUT / f"profile-gui-{os.getpid()}"
PROFILE.mkdir(exist_ok=True)
PROJECT = OUT / "GuiSmoke.diagproj"


def until(predicate, seconds=20):
    limit = time.monotonic() + seconds
    while time.monotonic() < limit:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError("UI response timed out")


def windows(pid):
    return [w for w in Desktop(backend="uia").windows(process=pid) if w.is_visible()]


def named(pid, fragment):
    for top in windows(pid):
        if fragment in top.window_text():
            return top
        for child in top.descendants():
            if fragment in child.window_text() and child.element_info.control_type == "Window":
                return child
    return None


def control(window, title, kind="Button"):
    return next(w for w in window.descendants(control_type=kind) if w.window_text() == title)


env = os.environ.copy()
env["LOCALAPPDATA"] = str(PROFILE)
env["QT_QPA_PLATFORM"] = "windows"
process = subprocess.Popen([str(EXE)], cwd=EXE.parent, env=env)
events = []
try:
    main = until(lambda: named(process.pid, "DiagAuthor"))
    events.append({"step": "launch", "title": main.window_text()})
    until(lambda: control(main, "새 프로젝트").is_enabled())
    main.set_focus()
    main.type_keys("^n")
    events.append({"step": "clicked_new", "windows": [w.window_text() for w in windows(process.pid)]})
    main.capture_as_image().save(OUT / "after-new.png")
    events.append({"step": "dialog_candidates", "uia": [w.window_text() for w in main.descendants() if w.window_text() and ("프로젝트" in w.window_text() or w.window_text() == "적용")], "win32": [w.window_text() for w in Desktop(backend="win32").windows(process=process.pid)]})
    dialog = until(lambda: named(process.pid, "새 ECU 프로젝트"))
    edits = dialog.descendants(control_type="Edit")
    assert len(edits) >= 2, f"project fields: {len(edits)}"
    edits[0].set_edit_text("GUI Smoke ECU")
    edits[1].set_edit_text("GuiSmokeECU")
    control(dialog, "적용").click_input()
    main = until(lambda: named(process.pid, "GUI Smoke ECU"))
    events.append({"step": "create", "title": main.window_text()})
    main.capture_as_image().save(OUT / "project-created.png")
    until(lambda: control(main, "저장").is_enabled())
    control(main, "저장").click_input()
    main.capture_as_image().save(OUT / "after-save.png")
    events.append({"step": "clicked_save", "win32": [w.window_text() for w in Desktop(backend="win32").windows(process=process.pid)]})
    dialog = until(lambda: named(process.pid, "프로젝트 저장"))
    events.append({"step": "save_dialog", "title": dialog.window_text()})
    dialog.set_focus()
    dialog.type_keys(str(PROJECT), with_spaces=True, set_foreground=True)
    dialog.type_keys("{ENTER}")
    until(lambda: PROJECT.exists())
    events.append({"step": "saved", "path": str(PROJECT), "bytes": PROJECT.stat().st_size})
    until(lambda: named(process.pid, "GUI Smoke ECU —"))
    main.capture_as_image().save(OUT / "project-saved.png")
except Exception as exc:
    events.append({"step": "failure", "error": repr(exc)})
    raise
finally:
    events.append({"step": "final_windows", "windows": [w.window_text() for w in windows(process.pid)]})
    (OUT / "gui-events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
print(json.dumps(events, ensure_ascii=False))
