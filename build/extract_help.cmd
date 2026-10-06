@echo off
rem Extract the local CANdelaStudio help (CHM) into build\reference\candela_help for analysis.
rem build\ is git-ignored: Vector help content must not be committed or redistributed.
cd /d "%~dp0.."
set CHM=C:\Program Files\Vector CANdelaStudio 13\Bin\CANdelaStudioen-US.chm
set ZIP=C:\Program Files\7-Zip\7z.exe
set OUT=build\reference\candela_help
if not exist "%CHM%" (echo CHM not found: %CHM% & if /i not "%1"=="nopause" pause & exit /b 1)
if not exist "%ZIP%" (echo 7-Zip not found: %ZIP% & if /i not "%1"=="nopause" pause & exit /b 1)
if not exist %OUT% mkdir %OUT%
echo Extracting "%CHM%" to %OUT% ...
"%ZIP%" x -y -o"%OUT%" "%CHM%" > build\reference\extract.log 2>&1
echo exit=%errorlevel% >> build\reference\extract.log
dir /s /b %OUT% | find /c /v "" > build\reference\help-count.txt
echo Files extracted:
type build\reference\help-count.txt
if /i not "%1"=="nopause" pause
