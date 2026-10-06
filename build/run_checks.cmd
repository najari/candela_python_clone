@echo off
rem unittest, rebuild, GUI CDD smoke on the built EXE. Output is shown here; logs in build\checks
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
set OUT=build\checks
if not exist %OUT% mkdir %OUT%
set PS=powershell
where pwsh >nul 2>&1 && set PS=pwsh
> %OUT%\status.txt echo started %date% %time%

echo [1/3] unittest
%PS% -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference=\"Continue\"; & .venv\Scripts\python.exe -m unittest discover -s test -v 2>&1 | ForEach-Object { \"$_\" } | Tee-Object -FilePath %OUT%\unittest.log; exit $LASTEXITCODE"
set RESULT=%errorlevel%
>> %OUT%\status.txt echo unittest_exit=%RESULT%
if not "%RESULT%"=="0" goto finish

echo [2/3] build
%PS% -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference=\"Continue\"; & .\build.ps1 2>&1 | ForEach-Object { \"$_\" } | Tee-Object -FilePath %OUT%\build-console.log; exit $LASTEXITCODE"
set RESULT=%errorlevel%
>> %OUT%\status.txt echo build_exit=%RESULT% shell=%PS%
if not "%RESULT%"=="0" goto finish

echo [3/3] GUI CDD smoke - do not touch mouse/keyboard until the app window closes
%PS% -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference=\"Continue\"; & .venv\Scripts\python.exe build\smoke_cdd_gui.py 2>&1 | ForEach-Object { \"$_\" } | Tee-Object -FilePath %OUT%\gui.log; exit $LASTEXITCODE"
set RESULT=%errorlevel%
>> %OUT%\status.txt echo gui_exit=%RESULT%
:finish
>> %OUT%\status.txt echo finished %date% %time%
echo.
type %OUT%\status.txt
if /i not "%1"=="nopause" pause
exit /b %RESULT%
