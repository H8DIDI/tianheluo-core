using System.Diagnostics;
using System.IO.Compression;
using System.Reflection;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace THL.Host;

/// <summary>host.json 里的一个下载资源（每种 .NET 版本一个）。</summary>
public sealed class HostAsset
{
    /// <summary>下载地址（相对 host.json 所在地址）。走 /api/dl/host-net10 这类计数入口。</summary>
    [JsonPropertyName("url")] public string Url { get; set; } = "";
    [JsonPropertyName("sha256")] public string Sha256 { get; set; } = "";
    [JsonPropertyName("size")] public long Size { get; set; }
}

/// <summary>网站发布的宿主最新版本信息（https://tianheluo.com/host.json）。</summary>
public sealed class HostRelease
{
    [JsonPropertyName("version")] public string Version { get; set; } = "";
    [JsonPropertyName("released")] public string Released { get; set; } = "";
    [JsonPropertyName("notes")] public string Notes { get; set; } = "";
    [JsonPropertyName("assets")] public Dictionary<string, HostAsset> Assets { get; set; } = new();
}

/// <summary>已下载、等待 Revit 关闭后安装的更新。</summary>
public sealed class PendingUpdate
{
    [JsonPropertyName("version")] public string Version { get; set; } = "";
    [JsonPropertyName("build")] public string Build { get; set; } = "";
    [JsonPropertyName("staging")] public string Staging { get; set; } = "";
    [JsonPropertyName("created")] public string Created { get; set; } = "";
}

/// <summary>
/// 宿主自更新。流程：
///   1) 检查：读取 host.json，和当前版本比较；
///   2) 下载并暂存：下载与当前 .NET 版本匹配的更新包，校验 SHA256 → 防路径穿越解压 → 检查内容 → 写 pending.json；
///   3) 应用：Revit 退出时（OnShutdown）启动一个独立的 PowerShell（apply-update.ps1），等 Revit 进程结束后替换文件，
///      失败自动回滚。运行中的 DLL 被 Revit 占用，所以不能在运行时替换自己。
/// 全部逻辑不依赖 Revit API，可在 Revit 外测试。
/// </summary>
public static class HostUpdater
{
    public static Version Current => Norm(typeof(HostUpdater).Assembly.GetName().Version ?? new Version(0, 0, 0));
    /// <summary>当前宿主是哪套编译：Revit 2026.5+ 是 net10，2026.0~2026.4 是 net8。</summary>
    public static string Build => Environment.Version.Major >= 10 ? "net10" : "net8";

    public static string UpdatesDir => Path.Combine(Store.Root, "updates");
    static string PendingPath => Path.Combine(UpdatesDir, "pending.json");
    /// <summary>宿主 DLL 所在目录（…\Addins\2026\THL）。</summary>
    public static string HostDir => Path.GetDirectoryName(typeof(HostUpdater).Assembly.Location)!;
    /// <summary>.addin 所在目录（…\Addins\2026）。</summary>
    public static string TargetDir => Directory.GetParent(HostDir)!.FullName;

    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    public static Version Norm(Version v) => new(v.Major, v.Minor, Math.Max(v.Build, 0));

    public static bool IsNewer(string remote)
        => Version.TryParse(remote, out var r) && Norm(r) > Current;

    // ---------------------------------------------------------------- 1) 检查
    public static async Task<HostRelease?> CheckAsync()
    {
        var url = Store.LoadConfig().HostUpdateUrl;
        var text = await Store.ReadTextAsync(url);
        var rel = JsonSerializer.Deserialize<HostRelease>(text, Json);
        return rel != null && Version.TryParse(rel.Version, out _) ? rel : null;
    }

    // ---------------------------------------------------------------- 2) 下载并暂存
    public static async Task<PendingUpdate> DownloadAndStageAsync(HostRelease rel, Action<string>? progress = null)
    {
        if (!Version.TryParse(rel.Version, out _)) throw new InvalidDataException("host.json 的版本号无效");
        if (!rel.Assets.TryGetValue(Build, out var asset) || string.IsNullOrWhiteSpace(asset.Url))
            throw new InvalidDataException($"这个版本没有提供 {Build} 的更新包");
        if (string.IsNullOrWhiteSpace(asset.Sha256))
            throw new InvalidDataException("更新包缺少 SHA256 校验值，已拒绝安装"); // 没有校验值就不装

        var baseUrl = Store.LoadConfig().HostUpdateUrl;
        var full = Store.ResolveUrl(baseUrl, asset.Url);
        progress?.Invoke($"正在下载 v{rel.Version}（{Build}）…");
        var bytes = await Store.ReadBytesAsync(full);

        progress?.Invoke("正在校验…");
        var actual = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        if (!string.Equals(actual, asset.Sha256.Trim(), StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("更新包校验失败（SHA256 不一致），已放弃。可能是下载损坏或被篡改。");

        Directory.CreateDirectory(UpdatesDir);
        var staging = Path.Combine(UpdatesDir, $"host-{rel.Version}-{Build}");
        if (Directory.Exists(staging)) Directory.Delete(staging, true);
        Directory.CreateDirectory(staging);
        try
        {
            using (var ms = new MemoryStream(bytes)) using (var zip = new ZipArchive(ms, ZipArchiveMode.Read))
            {
                var root = Path.GetFullPath(staging) + Path.DirectorySeparatorChar;
                foreach (var e in zip.Entries)
                {
                    if (string.IsNullOrEmpty(e.Name)) continue;
                    var dest = Path.GetFullPath(Path.Combine(staging, e.FullName));
                    if (!dest.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("更新包含非法路径：" + e.FullName); // 防 zip-slip
                    Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
                    e.ExtractToFile(dest, true);
                }
            }
            var dll = Path.Combine(staging, "THL", "THL.Host.dll");
            if (!File.Exists(dll) || !File.Exists(Path.Combine(staging, "THL.addin")))
                throw new InvalidDataException("更新包内容不完整（缺少 THL.Host.dll 或 THL.addin）");
            var asmVer = Norm(AssemblyName.GetAssemblyName(dll).Version ?? new Version(0, 0, 0));
            if (asmVer != Norm(Version.Parse(rel.Version)))
                throw new InvalidDataException($"更新包里的程序版本（{asmVer}）与 host.json 声明（{rel.Version}）不一致");
        }
        catch { try { Directory.Delete(staging, true); } catch { } throw; }

        var pending = new PendingUpdate { Version = rel.Version, Build = Build, Staging = staging, Created = DateTime.UtcNow.ToString("o") };
        File.WriteAllText(PendingPath, JsonSerializer.Serialize(pending, Json));
        progress?.Invoke($"v{rel.Version} 已下载，关闭 Revit 后自动安装。");
        return pending;
    }

    // ---------------------------------------------------------------- 3) 应用
    public static PendingUpdate? Pending()
    {
        try
        {
            if (!File.Exists(PendingPath)) return null;
            var p = JsonSerializer.Deserialize<PendingUpdate>(File.ReadAllText(PendingPath), Json);
            return p != null && Directory.Exists(p.Staging) ? p : null;
        }
        catch { return null; }
    }

    public static void ClearPending()
    {
        try { var p = Pending(); if (p != null && Directory.Exists(p.Staging)) Directory.Delete(p.Staging, true); } catch { }
        try { if (File.Exists(PendingPath)) File.Delete(PendingPath); } catch { }
    }

    /// <summary>启动时调用：如果待安装的版本已经装好（或不比当前新），清掉暂存。</summary>
    public static void CleanupIfApplied()
    {
        var p = Pending();
        if (p == null) { try { if (File.Exists(PendingPath)) File.Delete(PendingPath); } catch { } return; }
        if (!Version.TryParse(p.Version, out var v) || Norm(v) <= Current) ClearPending();
    }

    /// <summary>
    /// Revit 退出时调用：如果有待安装的更新，启动独立的 PowerShell 等 Revit 结束后替换文件。
    /// 返回是否已启动。失败不抛异常（宿主关闭流程不能被拖垮）。
    /// </summary>
    public static bool LaunchApplierIfPending()
    {
        try
        {
            var p = Pending();
            if (p == null || !Version.TryParse(p.Version, out var v) || Norm(v) <= Current) return false;

            Directory.CreateDirectory(UpdatesDir);
            var script = Path.Combine(UpdatesDir, "apply-update.ps1");
            using (var s = typeof(HostUpdater).Assembly.GetManifestResourceStream("THL.Host.apply-update.ps1")
                   ?? throw new InvalidOperationException("缺少内置的 apply-update.ps1"))
            using (var f = File.Create(script)) s.CopyTo(f);
            // 带 BOM，Windows PowerShell 5.1 才会按 UTF-8 读中文
            var txt = File.ReadAllText(script);
            File.WriteAllText(script, txt, new System.Text.UTF8Encoding(true));

            var args = $"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{script}\" " +
                       $"-RevitPid {Environment.ProcessId} -Staging \"{p.Staging}\" -Target \"{TargetDir}\" " +
                       $"-Version \"{p.Version}\" -Log \"{Path.Combine(Store.Root, "logs", "update.log")}\" " +
                       $"-Backup \"{Path.Combine(UpdatesDir, "backup")}\" -Pending \"{PendingPath}\"";
            Process.Start(new ProcessStartInfo("powershell.exe", args) { UseShellExecute = true, WindowStyle = ProcessWindowStyle.Hidden });
            return true;
        }
        catch { return false; }
    }
}
