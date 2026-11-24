# 天河落 THL 安装脚本（Windows PowerShell 5.1 / 7 都可运行）。文件必须是 UTF-8 带 BOM。
# 支持 Revit 2026（.NET 8 / .NET 10 两套，按小版本自动选）和 Revit 2020（.NET Framework 版）。
# 机器上装了哪几个就装哪几个；都没检测到就问用户。
param(
    [int]$RevitMinor = -1,          # 测试用：强制指定 Revit 2026 的小版本号
    [string]$AppData = $env:APPDATA, # 测试用：改变安装目标
    [string]$Years = "",            # 测试用：强制指定要装的版本，如 "2020,2026"
    [switch]$SkipRevitCheck          # 测试用：跳过「Revit 正在运行」检查
)
$ErrorActionPreference = "Stop"
$log = Join-Path $env:TEMP "thl-install.log"
function Log($m) { try { Add-Content -LiteralPath $log -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding UTF8 } catch {} }
function Say($m, $c = "White") { Write-Host $m -ForegroundColor $c; Log $m }

function Find-Revit([string]$year) {
    foreach ($c in @(
        (Join-Path $env:ProgramFiles "Autodesk\Revit $year\Revit.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Autodesk\Revit $year\Revit.exe"),
        "D:\Program Files\Autodesk\Revit $year\Revit.exe", "E:\Program Files\Autodesk\Revit $year\Revit.exe")) {
        if ($c -and (Test-Path -LiteralPath $c)) { return $c }
    }
    return $null
}

function Install-Build([string]$build, [string]$year) {
    $src = Join-Path $PSScriptRoot $build
    if (-not (Test-Path -LiteralPath (Join-Path $src "THL\THL.Host.dll"))) { throw "安装包不完整：找不到 $src\THL\THL.Host.dll。请把整个压缩包完整解压后再运行。" }
    $dest = Join-Path $AppData "Autodesk\Revit\Addins\$year"
    Say "  Revit $year：安装 $build 版本到 $dest"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $thl = Join-Path $dest "THL"
    if (Test-Path -LiteralPath $thl) { Remove-Item -LiteralPath $thl -Recurse -Force }
    Copy-Item -LiteralPath (Join-Path $src "THL") -Destination $thl -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $src "THL.addin") -Destination (Join-Path $dest "THL.addin") -Force
    # 解除「来自网络」标记，避免被 Windows 拦截
    try { Get-ChildItem -LiteralPath $thl -Recurse -File | Unblock-File; Unblock-File -LiteralPath (Join-Path $dest "THL.addin") } catch { Log "Unblock 失败（可忽略）: $_" }
    foreach ($f in @("THL.Host.dll", "IronPython.dll", "lib\os.py")) {
        if (-not (Test-Path -LiteralPath (Join-Path $thl $f))) { throw "安装后自检失败（Revit $year）：缺少 $f" }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $dest "THL.addin"))) { throw "安装后自检失败（Revit $year）：缺少 THL.addin" }
}

try {
    Set-Content -LiteralPath $log -Value "" -Encoding UTF8
    Say ""
    Say "  天河落 THL —— Revit 插件宿主（支持 Revit 2020 / 2026）" Cyan
    Log "PowerShell $($PSVersionTable.PSVersion) | OS $([Environment]::OSVersion.VersionString) | 脚本目录 $PSScriptRoot"

    # 1) Revit 不能在运行
    if (-not $SkipRevitCheck -and (Get-Process -Name Revit -ErrorAction SilentlyContinue)) {
        Say "  [!] 检测到 Revit 正在运行，请先关闭 Revit，再重新运行安装。" Yellow
        exit 2
    }

    # 2) 要装哪几个版本
    $want = @()
    if ($Years) { $want = $Years.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ } }
    else {
        foreach ($y in @("2026", "2020")) { $p = Find-Revit $y; if ($p) { $want += $y; Log "检测到 Revit $y：$p" } }
    }
    if ($want.Count -eq 0) {
        Say "  没有自动检测到 Revit。请选择要安装的版本：" Yellow
        Say "    1 = Revit 2026"
        Say "    2 = Revit 2020"
        Say "    3 = 两个都装"
        $ans = (Read-Host "  请输入 1、2 或 3").Trim()
        $want = switch ($ans) { "2" { @("2020") } "3" { @("2026", "2020") } default { @("2026") } }
        Log "手动选择: $($want -join ',')"
    }

    foreach ($y in $want) {
        if ($y -eq "2020") { Install-Build "net472" "2020"; continue }
        if ($y -ne "2026") { Say "  [!] 暂不支持 Revit $y，跳过。" Yellow; continue }
        # Revit 2026.5 及以上用 .NET 10，2026.0 ~ 2026.4 用 .NET 8
        $minor = $null
        if ($RevitMinor -ge 0) { $minor = $RevitMinor }
        else {
            $exe = Find-Revit "2026"
            if ($exe) { try { $minor = [int]((Get-Item -LiteralPath $exe).VersionInfo.FileVersion.Split(".")[1]); Log "Revit 2026 FileVersion=$((Get-Item -LiteralPath $exe).VersionInfo.FileVersion)" } catch { Log "读取版本失败: $_" } }
        }
        if ($null -eq $minor) {
            Say "  请选择你的 Revit 2026 版本：" Yellow
            Say "    1 = 2026.5 及以上"
            Say "    2 = 2026.0 ~ 2026.4"
            $a2 = Read-Host "  请输入 1 或 2"
            $minor = if ($a2.Trim() -eq "2") { 0 } else { 5 }
        }
        $build = if ($minor -ge 5) { "net10" } else { "net8" }
        Install-Build $build "2026"
    }

    Say ""
    Say "  安装完成！" Green
    Say "  下一步：打开 Revit（$($want -join ' / ')），顶部会出现「天河落」选项卡。"
    Say "  首次打开时 Revit 会弹出加载项安全提示，请选「始终加载」。"
    Say ""
    Say "  如果 Revit 提示「无法运行外部应用程序」，请把这个文件发给供应商："
    Say "    Revit 2026：$env:APPDATA\Tianheluo\logs\startup.log"
    Say "    Revit 2020：$env:APPDATA\Tianheluo\Revit2020\logs\startup.log"
    exit 0
}
catch {
    Say ""
    Say "  [!] 安装失败：$($_.Exception.Message)" Red
    Log ($_ | Out-String)
    Say "  详细记录：$log"
    exit 1
}
