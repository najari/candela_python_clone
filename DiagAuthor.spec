# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

project = Path(SPECPATH)
a = Analysis(
    [str(project / 'main.py')],
    pathex=[str(project)],
    binaries=[],
    datas=[(str(project / 'diagstudio' / 'schema.sql'), 'diagstudio')],
    hiddenimports=['lxml.etree'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'tkinter', 'unittest'],
    noarchive=False,
)
# Qt 6 uses the unversioned ICU exports provided by Windows.  PyInstaller may
# collect an unrelated, version-suffixed icuuc.dll from the build host's PATH;
# bundling it makes Qt6Core fail to load with WinError 127.
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() != 'icuuc.dll']
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='DiagAuthor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    version=str(project / 'packaging' / 'version.txt'),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='DiagAuthor.dist')
