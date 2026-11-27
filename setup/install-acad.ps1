# 天河落 THL · AutoCAD 宿主安装脚本（AutoCAD 2018 及以上；一份 THL.bundle 同时含 2018~2024 用的 net48 和 2025+ 用的 net8）。
# 用法：powershell -ExecutionPolicy Bypass -File install-acad.ps1 [-Bundle <THL.bundle 目录>] [-Plugins <放 .thlp 的目录>] [-Uninstall]
# 装到当前用户：%AppData%\Autodesk\ApplicationPlugins\THL.bundle（AutoCAD 启动时自动加载，不用 NETLOAD，不改 AutoCAD 的任何设置）。
# 插件装到 %AppData%\Tianheluo\AutoCAD\plugins\<id>。AutoCAD 开着时宿主 DLL 被占用：先关掉 AutoCAD。
param(
    [string]$Bundle = (Join-Path $PSScriptRoot "THL.bundle"),
    [string]$Plugins = "",
    [switch]$Uninstall,
    [switch]$Force          # AutoCAD 开着也装（宿主还没被加载时文件没被占用；已加载的话复制会失败）
)
$ErrorActionPreference = "Stop"
$apps = Join-Path $env:APPDATA "Autodesk\ApplicationPlugins"
$target = Join-Path $apps "THL.bundle"
$data = Join-Path $env:APPDATA "Tianheluo\AutoCAD"
if (-not $Force -and (Get-Process -Name acad -ErrorAction SilentlyContinue)) { Write-Host "AutoCAD 正在运行，请先关闭再安装。" -ForegroundColor Yellow; exit 2 }
if ($Uninstall) {
    if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
    Write-Host "已卸载天河落 AutoCAD 宿主（已装插件和设置保留在 $data）。"
    exit 0
}
if (-not (Test-Path -LiteralPath (Join-Path $Bundle "PackageContents.xml"))) { throw "找不到 $Bundle\PackageContents.xml" }
New-Item -ItemType Directory -Force -Path $apps | Out-Null
if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
Copy-Item -LiteralPath $Bundle -Destination $target -Recurse -Force
Write-Host "宿主已装到 $target"
if ($Plugins -and (Test-Path -LiteralPath $Plugins)) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    foreach ($p in Get-ChildItem -LiteralPath $Plugins -Filter *.thlp | Sort-Object Name) {
        $zip = [System.IO.Compression.ZipFile]::OpenRead($p.FullName)
        try {
            $mf = $zip.GetEntry("plugin.json")
            $sr = New-Object System.IO.StreamReader($mf.Open(), [System.Text.Encoding]::UTF8)
            $m = $sr.ReadToEnd() | ConvertFrom-Json; $sr.Close()
        } finally { $zip.Dispose() }
        if (-not $m.acad) { continue }   # 不是 AutoCAD 插件
        $dir = Join-Path $data ("plugins\" + $m.id)
        if (Test-Path -LiteralPath $dir) { Remove-Item -LiteralPath $dir -Recurse -Force }
        [System.IO.Compression.ZipFile]::ExtractToDirectory($p.FullName, $dir)
        Write-Host ("插件 {0} v{1} 已装到 {2}" -f $m.id, $m.version, $dir)
    }
}
# AutoCAD 年份检测（只是提示）
$found = @()
foreach ($k in Get-ChildItem "HKLM:\SOFTWARE\Autodesk\AutoCAD" -ErrorAction SilentlyContinue) { $found += $k.PSChildName }
Write-Host ("本机 AutoCAD 版本（R 号）：" + ($found -join ", ") + "。下次启动 AutoCAD 会出现「天河落」选项卡（命令 THL 打开插件中心）。")
