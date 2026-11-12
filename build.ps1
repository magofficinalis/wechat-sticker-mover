param([string]$Python = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = if ($Python) { $Python } else { Join-Path $PSScriptRoot '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $projectPython)) { throw '先创建 .venv 并安装 requirements-dev.txt。' }
$runtimeRoot = (& $projectPython -c 'import sys; print(sys.base_prefix)').Trim()
if ($LASTEXITCODE -ne 0) { throw '读取 Python 运行时路径失败' }
$previousBuildPath = $env:PATH
try {
    # Avoid collecting unrelated DLLs from image/video tools on the developer PATH.
    $env:PATH = (@((Join-Path $runtimeRoot 'Library/bin'), (Join-Path $runtimeRoot 'DLLs'), $runtimeRoot, (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot) -join ';')
    & $projectPython -m PyInstaller --noconfirm --clean --windowed --onedir --name StickerImporter --add-data "xhs_importer/assets;xhs_importer/assets" --add-data "xhs_importer/_vendor/wechat_meme_export/LICENSE;xhs_importer/_vendor/wechat_meme_export" --collect-all uiautomator2 --collect-all adbutils --collect-all retry --collect-all PIL --collect-all cryptography launch.py
    if ($LASTEXITCODE -ne 0) { throw '打包失败' }
} finally {
    $env:PATH = $previousBuildPath
}
& $projectPython -c 'from xhs_importer.help_view import write_guides; write_guides("dist/StickerImporter")'
if ($LASTEXITCODE -ne 0) { throw '离线教程生成失败' }
Copy-Item -LiteralPath 'LICENSE','NOTICE','THIRD_PARTY_NOTICES.md','README.md' -Destination 'dist/StickerImporter'
Copy-Item -LiteralPath 'docs/QUICKSTART.md' -Destination 'dist/StickerImporter/新手教程.md'
New-Item -ItemType Directory -Path 'dist/StickerImporter/docs' -Force | Out-Null
Copy-Item -LiteralPath 'docs/QUICKSTART.md' -Destination 'dist/StickerImporter/docs/QUICKSTART.md'
& $projectPython 'packaging/windows/collect_licenses.py' 'dist/StickerImporter/dependency-licenses'
if ($LASTEXITCODE -ne 0) { throw '第三方许可收集失败' }
