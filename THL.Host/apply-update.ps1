# 天河落 THL 宿主更新应用脚本 —— 由宿主在 Revit 退出时启动（隐藏窗口）。
# 流程：等 Revit 进程结束 → 备份旧版 → 复制新版 → 自检 → 失败则回滚。成功后清理暂存。
# 文件必须是 UTF-8 带 BOM。
param(
    [Parameter(Mandatory)][int]$RevitPid,
    [Parameter(Mandatory)][string]$Staging,   # 暂存目录：里面有 THL.addin 和 THL\
    [Parameter(Mandatory)][string]$Target,    # …\Addins\2026
    [Parameter(Mandatory)][string]$Version,
    [Parameter(Mandatory)][string]$Log,
    [string]$Backup = "",                    # （已弃用，保留参数兼容）
    [Parameter(Mandatory)][string]$Pending,   # pending.json 路径
    [int]$WaitSeconds = 300,                  # 等 Revit 退出 / 文件被占用的最长时间
    [int]$RetrySeconds = 120
)
$ErrorActionPreference = "Stop"
function Log($m) {
    try { New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
          Add-Content -LiteralPath $Log -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m) -Encoding UTF8 } catch {}
}

$thl = Join-Path $Target "THL"
$addin = Join-Path $Target "THL.addin"
$stageThl = Join-Path $Staging "THL"
$stageAddin = Join-Path $Staging "THL.addin"
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
# 备份放在目标同级目录：同一磁盘上目录重命名是原子的（要么整个成功，要么什么都不动）。
$bakDir = Join-Path $Target ("THL.old-" + $ts)
$moved = $false

try {
    Log "开始应用宿主更新 -> v$Version（等待 Revit 进程 $RevitPid 退出）"
    # 1) 校验暂存内容（失败则什么都不动）
    foreach ($p in @((Join-Path $stageThl "THL.Host.dll"), $stageAddin)) {
        if (-not (Test-Path -LiteralPath $p)) { throw "暂存内容不完整：缺少 $p" }
    }
    # 2) 等 Revit 退出
    $proc = Get-Process -Id $RevitPid -ErrorAction SilentlyContinue
    if ($proc) {
        if (-not $proc.WaitForExit($WaitSeconds * 1000)) { throw "等待 Revit 退出超时（$WaitSeconds 秒），本次不更新，下次关闭 Revit 时再试" }
    }
    Start-Sleep -Milliseconds 800

    # 3) 备份并替换（文件仍被占用时重试，例如开了第二个 Revit）
    $deadline = (Get-Date).AddSeconds($RetrySeconds)
    while ($true) {
        try {
            # 注意：不能用 Move-Item——它搬目录失败时会“搬一半”，把旧版拆成两半。用 .NET 的原子重命名。
            if (Test-Path -LiteralPath $thl) { [System.IO.Directory]::Move($thl, $bakDir); $moved = $true }
            if ($moved -and (Test-Path -LiteralPath $addin)) { Copy-Item -LiteralPath $addin -Destination (Join-Path $bakDir "THL.addin") -Force }
            break
        } catch {
            if ((Get-Date) -gt $deadline) { throw "文件被占用，无法替换：$($_.Exception.Message)" }
            Log "文件被占用，5 秒后重试：$($_.Exception.Message)"
            Start-Sleep -Seconds 5
        }
    }
    Copy-Item -LiteralPath $stageThl -Destination $thl -Recurse -Force
    Copy-Item -LiteralPath $stageAddin -Destination $addin -Force
    try { Get-ChildItem -LiteralPath $thl -Recurse -File | Unblock-File; Unblock-File -LiteralPath $addin } catch {}

    # 4) 自检：关键文件在，版本对
    foreach ($f in @("THL.Host.dll", "IronPython.dll", "lib\os.py")) {
        if (-not (Test-Path -LiteralPath (Join-Path $thl $f))) { throw "更新后自检失败：缺少 $f" }
    }
    $ver = [System.Reflection.AssemblyName]::GetAssemblyName((Join-Path $thl "THL.Host.dll")).Version
    $want = [version]$Version
    if ($ver.Major -ne $want.Major -or $ver.Minor -ne $want.Minor -or [Math]::Max($ver.Build,0) -ne [Math]::Max($want.Build,0)) {
        throw "更新后自检失败：程序版本是 $ver，期望 $Version"
    }

    # 5) 成功：清理暂存和待安装标记；只保留最近 2 份备份
    Remove-Item -LiteralPath $Staging -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Pending -Force -ErrorAction SilentlyContinue
    Get-ChildItem -LiteralPath $Target -Directory -Filter "THL.old-*" -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -Skip 2 |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    Log "宿主已更新到 v$Version（旧版备份在 $bakDir）"
    exit 0
}
catch {
    Log ("更新失败：" + $_.Exception.Message)
    # 回滚：把备份还原回去
    try {
        if ($moved -and (Test-Path -LiteralPath $bakDir)) {
            if (Test-Path -LiteralPath $thl) { Remove-Item -LiteralPath $thl -Recurse -Force }
            [System.IO.Directory]::Move($bakDir, $thl)
            $oldAddin = Join-Path $thl "THL.addin"
            if (Test-Path -LiteralPath $oldAddin) { Move-Item -LiteralPath $oldAddin -Destination $addin -Force }
            Log "已回滚到更新前的版本"
        }
    } catch { Log ("回滚时出错：" + $_.Exception.Message) }
    exit 1
}
