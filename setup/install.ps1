# 天河落 THL 安装脚本（Windows PowerShell 5.1 / 7 都可运行）。文件必须是 UTF-8 带 BOM。
param(
    [int]$RevitMinor = -1,          # 测试用：强制指定 Revit 2026 的小版本号
    [string]$AppData = $env:APPDATA, # 测试用：改变安装目标
    [switch]$SkipRevitCheck          # 测试用：跳过「Revit 正在运行」检查
)
$ErrorActionPreference = "Stop"
$log = Join-Path $env:TEMP "thl-install.log"
function Log($m) { try { Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding UTF8 } catch {} }
function Say($m, $c = "White") { Write-Host $m -ForegroundColor $c; Log $m }

try {
    Set-Content -LiteralPath $log -Value "" -Encoding UTF8
    Say ""
    Say "  天河落 THL —— Revit 2026 插件宿主" Cyan
    Log "PowerShell $($PSVersionTable.PSVersion) | OS $([Environment]::OSVersion.VersionString) | 脚本目录 $PSScriptRoot"

    # 1) Revit 不能在运行
    if (-not $SkipRevitCheck -and (Get-Process -Name Revit -ErrorAction SilentlyContinue)) {
        Say "  [!] 检测到 Revit 正在运行，请先关闭 Revit，再重新运行安装。" Yellow
        exit 2
    }

    # 2) 选择版本：Revit 2026.5 及以上用 .NET 10，2026.0 ~ 2026.4 用 .NET 8
    $build = $null
    if ($RevitMinor -ge 0) { $minor = $RevitMinor }
    else {
        $minor = $null
        $candidates = @(
            (Join-Path $env:ProgramFiles "Autodesk\Revit 2026\Revit.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "Autodesk\Revit 2026\Revit.exe"),
            "D:\Program Files\Autodesk\Revit 2026\Revit.exe", "E:\Program Files\Autodesk\Revit 2026\Revit.exe"
        )
        foreach ($c in $candidates) {
            if ($c -and (Test-Path -LiteralPath $c)) {
                try { $minor = [int]((Get-Item -LiteralPath $c).VersionInfo.FileVersion.Split(".")[1]); Log "Revit: $c  FileVersion=$((Get-Item -LiteralPath $c).VersionInfo.FileVersion)"; break } catch { Log "读取版本失败: $c  $_" }
            }
        }
    }
    if ($null -ne $minor) {
        $build = if ($minor -ge 5) { "net10" } else { "net8" }
        Say "  检测到 Revit 2026 更新 $minor，将安装 $build 版本。"
    } else {
        Say "  没有自动检测到 Revit 版本。请选择你的 Revit 2026 版本：" Yellow
        Say "    1 = 2026.5 及以上"
        Say "    2 = 2026.0 ~ 2026.4"
        $ans = Read-Host "  请输入 1 或 2"
        $build = if ($ans.Trim() -eq "2") { "net8" } else { "net10" }
        Log "手动选择: $build"
    }

    $src = Join-Path $PSScriptRoot $build
    if (-not (Test-Path -LiteralPath (Join-Path $src "THL\THL.Host.dll"))) { throw "安装包不完整：找不到 $src\THL\THL.Host.dll。请把整个压缩包完整解压后再运行。" }

    # 3) 复制
    $dest = Join-Path $AppData "Autodesk\Revit\Addins\2026"
    Say "  安装到：$dest"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $thl = Join-Path $dest "THL"
    if (Test-Path -LiteralPath $thl) { Remove-Item -LiteralPath $thl -Recurse -Force }
    Copy-Item -LiteralPath (Join-Path $src "THL") -Destination $thl -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $src "THL.addin") -Destination (Join-Path $dest "THL.addin") -Force

    # 4) 解除「来自网络」标记，避免被 Windows 拦截
    try { Get-ChildItem -LiteralPath $thl -Recurse -File | Unblock-File; Unblock-File -LiteralPath (Join-Path $dest "THL.addin") } catch { Log "Unblock 失败（可忽略）: $_" }

    # 5) 自检
    foreach ($f in @("THL.Host.dll", "IronPython.dll", "lib\os.py")) {
        if (-not (Test-Path -LiteralPath (Join-Path $thl $f))) { throw "安装后自检失败：缺少 $f" }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $dest "THL.addin"))) { throw "安装后自检失败：缺少 THL.addin" }

    Say ""
    Say "  安装完成！" Green
    Say "  下一步：打开 Revit 2026，顶部会出现「天河落」选项卡。"
    Say "  首次打开时 Revit 会弹出加载项安全提示，请选「始终加载」。"
    Say ""
    Say "  如果 Revit 提示「无法运行外部应用程序」，请把这个文件发给供应商："
    Say "    $env:APPDATA\Tianheluo\logs\startup.log"
    exit 0
}
catch {
    Say ""
    Say "  [!] 安装失败：$($_.Exception.Message)" Red
    Log ($_ | Out-String)
    Say "  详细记录：$log"
    exit 1
}
