# 用 didi 代码签名证书（颁发者 tianheluo.com）给宿主和 C# 插件的 DLL 签名。
# 用法：powershell -ExecutionPolicy Bypass -File tools\sign.ps1
# 私钥只在当前用户的个人证书库里，不要导出、不要提交到仓库。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object { $_.Subject -eq "CN=didi" -and $_.Issuer -eq "CN=tianheluo.com" } | Select-Object -First 1
if (-not $cert) { throw "找不到签名证书 CN=didi（颁发者 CN=tianheluo.com）" }

$files = @(
    "$root\THL.Host\bin\Release\net8.0-windows\THL.Host.dll",
    "$root\THL.Host\bin\Release\net10.0-windows\THL.Host.dll"
) + (Get-ChildItem "$root\plugins\*\bin\Release\net8.0-windows\*.dll" -ErrorAction SilentlyContinue |
        Where-Object { $_.BaseName -in @("WellDepth", "ScheduleExcel") } | ForEach-Object FullName)

foreach ($f in $files) {
    if (-not (Test-Path $f)) { Write-Host "跳过（不存在）: $f"; continue }
    try {
        # 先尝试带时间戳，网络不通时退回无时间戳
        try { $r = Set-AuthenticodeSignature -FilePath $f -Certificate $cert -HashAlgorithm SHA256 -TimestampServer "http://timestamp.digicert.com" }
        catch { $r = Set-AuthenticodeSignature -FilePath $f -Certificate $cert -HashAlgorithm SHA256 }
    } catch { Write-Host ("跳过（文件被占用，关闭 Revit 后重试）: " + $f); continue }
    $sig = Get-AuthenticodeSignature $f
    "{0,-22} 发布者={1}  颁发者={2}  时间戳={3}" -f (Split-Path $f -Leaf), $sig.SignerCertificate.Subject, $sig.SignerCertificate.Issuer, ($sig.TimeStamperCertificate -ne $null)
}
