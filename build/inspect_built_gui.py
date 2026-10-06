"""Inspect the packaged executable through Windows UI Automation."""
import json
import os
from pathlib import Path
import subprocess
import time

from pywinauto import Desktop


root = Path(__file__).resolve().parents[1]
exe = root / "dist" / "DiagAuthor.dist" / "DiagAuthor.exe"
out = root / "build" / "runtime-smoke"
out.mkdir(parents=True, exist_ok=True)
profile = out / "profile"
profile.mkdir(exist_ok=True)
environment = os.environ.copy()
environment["LOCALAPPDATA"] = str(profile)
environment["QT_QPA_PLATFORM"] = "windows"
process = subprocess.Popen([str(exe)], cwd=exe.parent, env=environment)
result = {"pid": process.pid, "exe": str(exe), "windows": []}
try:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        windows = Desktop(backend="uia").windows(process=process.pid)
        if windows or process.poll() is not None:
            break
        time.sleep(0.4)
    result["exit_code_before_inspection"] = process.poll()
    for window in Desktop(backend="uia").windows(process=process.pid):
        info = {"title": window.window_text(), "control_type": window.element_info.control_type, "visible": window.is_visible(), "children": []}
        for child in window.descendants()[:150]:
            info["children"].append({"name": child.window_text(), "type": child.element_info.control_type})
        result["windows"].append(info)
        if info["visible"]:
            window.capture_as_image().save(out / "initial-window.png")
    (out / "initial-state.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pid": process.pid, "exit_code": result["exit_code_before_inspection"], "windows": [{"title": w["title"], "visible": w["visible"], "children": len(w["children"])} for w in result["windows"]]}, ensure_ascii=False))
finally:
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill()
