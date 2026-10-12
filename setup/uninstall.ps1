# 天河落 THL 卸载脚本。文件必须是 UTF-8 带 BOM。
param([string]$AppData = $env:APPDATA, [switch]$SkipRevitCheck)
$ErrorActionPreference = "Stop"
try {
    if (-not $SkipRevitCheck -and (Get-Process -Name Revit -ErrorAction SilentlyContinue)) {
        Write-Host "  [!] 请先关闭 Revit，再运行卸载。" -ForegroundColor Yellow
        exit 2
    }
    $dest = Join-Path $AppData "Autodesk\Revit\Addins\2026"
    $a = Join-Path $dest "THL.addin"; $d = Join-Path $dest "THL"
    if (Test-Path -LiteralPath $a) { Remove-Item -LiteralPath $a -Force }
    if (Test-Path -LiteralPath $d) { Remove-Item -LiteralPath $d -Recurse -Force }
    Write-Host "  宿主已卸载。" -ForegroundColor Green
    Write-Host "  已安装的插件仍保留在 $AppData\Tianheluo，如需清除请手动删除该文件夹。"
    exit 0
}
catch { Write-Host "  [!] 卸载失败：$($_.Exception.Message)" -ForegroundColor Red; exit 1 }
