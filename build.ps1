param([switch]$InstallDependencies, [ValidateSet('PyInstaller','Nuitka')][string]$Backend = 'PyInstaller')
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$activeExecutable = Join-Path $projectRoot 'dist\DiagAuthor.dist\DiagAuthor.exe'
if (Get-CimInstance Win32_Process -Filter "Name='DiagAuthor.exe'" | Where-Object { $_.ExecutablePath -eq $activeExecutable }) {
    throw '배포 폴더의 DiagAuthor가 실행 중입니다. 앱에서 작업을 저장하고 종료한 뒤 다시 빌드하세요.'
}
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$env:NUITKA_CACHE_DIR = Join-Path $projectRoot '.build-cache\nuitka'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $projectRoot '.build-cache\pyinstaller'
$env:PYTHONUTF8 = '1'
New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot 'build'), (Join-Path $projectRoot 'dist'), $env:NUITKA_CACHE_DIR | Out-Null
if ($InstallDependencies) {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        python -m venv --without-pip .venv
        if ($LASTEXITCODE -ne 0) { throw '가상환경 생성 실패' }
    }
    $ErrorActionPreference = 'Continue'
    uv pip install --python $pythonPath --cache-dir .build-cache\uv -r requirements-build.txt pip 2>&1 | ForEach-Object { "$_" }
    $installExit = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($installExit -ne 0) { throw '빌드 의존성 설치 실패' }
}
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '먼저 build.ps1 -InstallDependencies로 빌드 의존성을 준비하세요.' }
$compilerBin = 'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\14.44.35207\bin\Hostx64\x64'
if (Test-Path -LiteralPath $compilerBin) { $env:PATH = $compilerBin + ';' + $env:PATH }
$buildStart = Get-Date
# PyInstaller/Nuitka는 INFO 로그를 stderr로 쓴다. Windows PowerShell 5.1은 ErrorActionPreference=Stop에서
# 리디렉션된 native stderr 첫 줄을 종료 오류로 처리하므로, 실행 중에만 Continue로 두고 종료 코드로 판정한다.
$ErrorActionPreference = 'Continue'
if ($Backend -eq 'Nuitka') {
    & (Join-Path $projectRoot '.venv\Scripts\pyside6-deploy.exe') -c pysidedeploy.spec --force --keep-deployment-files --nuitka-version 4.2.2 --extra-ignore-dirs docs,.venv,.build-cache,build,dist 2>&1 | ForEach-Object { "$_" } | Set-Content -LiteralPath 'build\build.log' -Encoding utf8
} else {
    & $pythonPath -m PyInstaller --noconfirm --distpath dist --workpath build\pyinstaller DiagAuthor.spec 2>&1 | ForEach-Object { "$_" } | Set-Content -LiteralPath 'build\build.log' -Encoding utf8
}
$buildExit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
$executable = Join-Path $projectRoot 'dist\DiagAuthor.dist\DiagAuthor.exe'
if ($buildExit -ne 0 -or -not (Test-Path -LiteralPath $executable)) {
    Get-Content -LiteralPath 'build\build.log' -Tail 60
    throw "빌드 실패. build\build.log를 확인하세요. 종료 코드: $buildExit"
}
if ((Get-Item -LiteralPath $executable).LastWriteTime -lt $buildStart -or (Select-String -LiteralPath 'build\build.log' -Pattern '^FATAL:' -Quiet)) {
    Get-Content -LiteralPath 'build\build.log' -Tail 60
    throw '이번 빌드가 새 실행 파일을 만들지 못했습니다.'
}
Copy-Item -LiteralPath 'README.md','BUILD-NOTES.md','requirements-build.txt' -Destination 'dist\DiagAuthor.dist'
Copy-Item -LiteralPath 'docs' -Destination 'dist\DiagAuthor.dist' -Recurse -Force
$licenseFolder=Join-Path $projectRoot 'dist\DiagAuthor.dist\licenses'
New-Item -ItemType Directory -Force -Path $licenseFolder | Out-Null
Get-ChildItem -LiteralPath '.venv\Lib\site-packages' -Directory -Filter '*.dist-info' | ForEach-Object {
    $licenseSource=Join-Path $_.FullName 'licenses'
    if (Test-Path -LiteralPath $licenseSource) {
        Copy-Item -LiteralPath $licenseSource -Destination (Join-Path $licenseFolder $_.Name) -Recurse -Force
    }
}
$pythonLicense=Join-Path (Split-Path (Get-Command python).Source) 'LICENSE.txt'
if (Test-Path -LiteralPath $pythonLicense) { Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $licenseFolder 'Python-LICENSE.txt') }
$hash = Get-FileHash -LiteralPath $executable -Algorithm SHA256
[ordered]@{ version='0.1.0'; builtAt=(Get-Date).ToString('o'); executable=$executable; sha256=$hash.Hash; testsExecuted=$false; applicationLaunched=$false; builder=$Backend } | ConvertTo-Json | Set-Content -LiteralPath 'build\build-result.json' -Encoding utf8
Compress-Archive -LiteralPath 'dist\DiagAuthor.dist' -DestinationPath 'dist\DiagAuthor-0.1.0-win64.zip' -Force
Write-Output "빌드 완료: $executable"
Write-Output '테스트 및 앱 실행은 수행하지 않았습니다.'
