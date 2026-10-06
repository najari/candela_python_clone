"""Open a project saved by the packaged EXE and inspect its visible state."""
import json
import os
from pathlib import Path
import subprocess
import time
from pywinauto import Desktop

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "checks" / "t34release"
EXE = ROOT / "dist" / "DiagAuthor.dist" / "DiagAuthor.exe"
PROJECT = OUT / "AbsImported.diagproj"
PROFILE = OUT / "reopen-profile"
PROFILE.mkdir(exist_ok=True)
env = os.environ.copy()
env["LOCALAPPDATA"] = str(PROFILE)
env["QT_QPA_PLATFORM"] = "windows"
process = subprocess.Popen([str(EXE), str(PROJECT)], cwd=EXE.parent, env=env)
result = {"exe": str(EXE), "project": str(PROJECT), "success": False}
try:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        windows = [w for w in Desktop(backend="uia").windows(process=process.pid) if w.is_visible()]
        main = next((w for w in windows if "ABS/ESP" in w.window_text() and "DiagAuthor" in w.window_text()), None)
        if main:
            names = [w.window_text() for w in main.descendants()]
            if any("DID 19" in name and "DTC 31" in name for name in names):
                result.update(success=True, title=main.window_text(), counts="DID 19 · DTC 31")
                main.capture_as_image().save(OUT / "09-reopened.png")
                break
        if process.poll() is not None: break
        time.sleep(0.4)
finally:
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill()
    (OUT / "reopen.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
if not result["success"]: raise SystemExit(1)
