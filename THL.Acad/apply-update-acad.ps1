# 天河落 THL · AutoCAD 宿主更新应用脚本 —— 由宿主在 AutoCAD 退出时启动（隐藏窗口）。
# 流程：等 AutoCAD 进程结束 → 原子改名备份旧的 THL.bundle → 复制新版 → 自检 → 失败则回滚。成功后清理暂存。
# 参数名沿用 Revit 版（-RevitPid = 宿主进程号），宿主的启动代码两边共用。文件必须是 UTF-8 带 BOM。
param(
    [Parameter(Mandatory)][int]$RevitPid,
    [Parameter(Mandatory)][string]$Staging,   # 暂存目录：PackageContents.xml + Contents\net48\ + Contents\net8\
    [Parameter(Mandatory)][string]$Target,    # …\ApplicationPlugins\THL.bundle
    [Parameter(Mandatory)][string]$Version,
    [Parameter(Mandatory)][string]$Log,
    [string]$Backup = "",
    [Parameter(Mandatory)][string]$Pending,
    [int]$WaitSeconds = 300,
    [int]$RetrySeconds = 120
)
$ErrorActionPreference = "Stop"
function Log($m) {
    try { New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
          Add-Content -LiteralPath $Log -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding UTF8 } catch {}
}
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$bak = $Target + ".old-" + $ts
$moved = $false
try {
    Log "开始应用 AutoCAD 宿主更新 -> v$Version（等待进程 $RevitPid 退出）"
    foreach ($p in @((Join-Path $Staging "PackageContents.xml"), (Join-Path $Staging "Contents\net48\THL.Acad.dll"), (Join-Path $Staging "Contents\net8\THL.Acad.dll"))) {
        if (-not (Test-Path -LiteralPath $p)) { throw "暂存内容不完整：缺少 $p" }
    }
    $proc = Get-Process -Id $RevitPid -ErrorAction SilentlyContinue
    if ($proc) { if (-not $proc.WaitForExit($WaitSeconds * 1000)) { throw "等待 AutoCAD 退出超时（$WaitSeconds 秒），下次关闭时再试" } }
    Start-Sleep -Milliseconds 800
    $deadline = (Get-Date).AddSeconds($RetrySeconds)
    while ($true) {
        try {
            if (Test-Path -LiteralPath $Target) { [System.IO.Directory]::Move($Target, $bak); $moved = $true }
            break
        } catch {
            if ((Get-Date) -gt $deadline) { throw "THL.bundle 一直被占用（可能还开着别的 AutoCAD），本次不更新：$($_.Exception.Message)" }
            Start-Sleep -Seconds 2
        }
    }
    Copy-Item -LiteralPath $Staging -Destination $Target -Recurse -Force
    if (-not (Test-Path -LiteralPath (Join-Path $Target "Contents\net8\THL.Acad.dll"))) { throw "复制后自检失败" }
    Log "已更新到 v$Version"
    try { Remove-Item -LiteralPath $Staging -Recurse -Force } catch {}
    try { Remove-Item -LiteralPath $Pending -Force } catch {}
    # 旧版备份只留最近一份
    Get-ChildItem -LiteralPath (Split-Path $Target) -Directory -Filter "THL.bundle.old-*" | Sort-Object Name -Descending | Select-Object -Skip 1 |
        ForEach-Object { try { Remove-Item -LiteralPath $_.FullName -Recurse -Force } catch {} }
} catch {
    Log "更新失败：$($_.Exception.Message)"
    if ($moved) {
        try { if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }; [System.IO.Directory]::Move($bak, $Target); Log "已回滚到旧版" }
        catch { Log "回滚失败：$($_.Exception.Message)；旧版在 $bak" }
    }
    exit 1
}
