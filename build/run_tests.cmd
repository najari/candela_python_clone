@echo off
rem Unit tests only. Output is shown here and saved to build\checks\unittest.log
chcp 65001 >nul
cd /d "%~dp0.."
set PYTHONUTF8=1
set OUT=build\checks
if not exist %OUT% mkdir %OUT%
set PS=powershell
where pwsh >nul 2>&1 && set PS=pwsh
echo [unittest] python -m unittest discover -s test -v
%PS% -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference=\"Continue\"; & .venv\Scripts\python.exe -m unittest discover -s test -v 2>&1 | ForEach-Object { \"$_\" } | Tee-Object -FilePath %OUT%\unittest.log; exit $LASTEXITCODE"
set RESULT=%errorlevel%
echo.
if "%RESULT%"=="0" (echo === TESTS PASSED ===) else (echo === TESTS FAILED: exit %RESULT% ===)
> %OUT%\tests-status.txt echo unittest_exit=%RESULT% %date% %time%
if /i not "%1"=="nopause" pause
exit /b %RESULT%
