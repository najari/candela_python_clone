[app]
title = DiagAuthor
project_dir = .
input_file = main.py
exec_directory = dist
project_file = pyproject.toml
icon = C:\Users\admin\candela_clone\.venv\Lib\site-packages\PySide6\scripts\deploy_lib\pyside_icon.ico

[python]
python_path = C:\Users\admin\candela_clone\.venv\Scripts\python.exe
packages = Nuitka==4.2.2,ordered-set==4.1.0,zstandard==0.25.0
android_packages = buildozer==1.5.0,cython==0.29.33

[qt]
qml_files = 
excluded_qml_plugins = QtCharts,QtSensors,QtWebEngine
modules = Core,Gui,Widgets
plugins = platforms,styles,imageformats,iconengines

[android]
wheel_pyside = 
wheel_shiboken = 
plugins = 

[nuitka]
macos.permissions = 
mode = standalone
extra_args = --assume-yes-for-downloads --msvc=latest --jobs=8 --windows-console-mode=disable --noinclude-qt-translations --include-data-files=diagstudio/schema.sql=diagstudio/schema.sql --output-filename=DiagAuthor.exe --company-name=DiagAuthor --product-name=DiagAuthor --file-version=0.1.0 --product-version=0.1.0 --report=build/compilation-report.xml

[buildozer]
mode = debug
recipe_dir = 
jars_dir = 
ndk_path = 
sdk_path = 
local_libs = 
arch = 

