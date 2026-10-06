@echo off
rem GUI CDD import smoke only (no rebuild). Results: build\checks\run2\
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
rem close leftover smoke-test app instances only (dist exe started with the example CDD argument)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='DiagAuthor.exe'\" | Where-Object { $_.CommandLine -like '*ABS_ESP-Example.cdd*' -and $_.ExecutablePath -like '*candela_clone\dist\*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
set SMOKE_TAG=run2
if not exist build\checks\run2 mkdir build\checks\run2
rem let the launcher (remote control session) release the screen first
timeout /t 15 /nobreak >nul
.venv\Scripts\python.exe build\smoke_cdd_gui.py > build\checks\run2\gui.log 2>&1
>> build\checks\run2\gui.log echo gui_exit=%errorlevel%
