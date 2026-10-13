using System.IO.Compression;
using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace THL.Host;

/// <summary>插件清单：plugin.json</summary>
public sealed class Manifest
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("version")] public string Version { get; set; } = "0.0.0";
    [JsonPropertyName("description")] public string Description { get; set; } = "";
    [JsonPropertyName("category")] public string Category { get; set; } = "";
    [JsonPropertyName("revit")] public string Revit { get; set; } = "2026";
    /// <summary>主 DLL 文件名（在插件目录内）</summary>
    [JsonPropertyName("dll")] public string Dll { get; set; } = "";
    /// <summary>入口类的完整类型名，必须实现 IExternalCommand</summary>
    [JsonPropertyName("entry")] public string Entry { get; set; } = "";
    /// <summary>"dotnet"（默认，加载 dll）或 "python"（运行原样 pyRevit 脚本）</summary>
    [JsonPropertyName("type")] public string Type { get; set; } = "dotnet";
    /// <summary>图标上的一个字（可选）与主色（可选，如 #2F8CFF）；包里有 icon.png 时优先用图片</summary>
    [JsonPropertyName("glyph")] public string Glyph { get; set; } = "";
    [JsonPropertyName("color")] public string Color { get; set; } = "";
    /// <summary>type=python 时的脚本文件名</summary>
    [JsonPropertyName("script")] public string Script { get; set; } = "";
    public bool IsPython => string.Equals(Type, "python", StringComparison.OrdinalIgnoreCase);
}

/// <summary>已安装插件（磁盘上的一个文件夹）</summary>
public sealed class Installed
{
    public Manifest Manifest { get; init; } = new();
    public string Dir { get; init; } = "";
    public bool Enabled { get; set; } = true;
    public string DllPath => Path.Combine(Dir, Manifest.Dll);
    public string MainFile => Manifest.IsPython ? Manifest.Script : Manifest.Dll;
}

/// <summary>商店里的一个条目（来自网站 plugins.json）</summary>
public sealed class CatalogItem
{
    [JsonPropertyName("id")] public string Id { get; set; } = "";
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("version")] public string Version { get; set; } = "";
    [JsonPropertyName("summary")] public string Summary { get; set; } = "";
    [JsonPropertyName("category")] public string Category { get; set; } = "";
    [JsonPropertyName("status")] public string Status { get; set; } = "";
    [JsonPropertyName("download")] public string Download { get; set; } = "";
    [JsonPropertyName("glyph")] public string Glyph { get; set; } = "";
    [JsonPropertyName("color")] public string Color { get; set; } = "";
}

/// <summary>用户数据目录 + 已安装插件管理（安装 / 卸载 / 启用 / 停用）</summary>
public static class Store
{
    /// <summary>用户数据目录。可用环境变量 THL_HOME 覆盖（测试用）。</summary>
    public static readonly string Root =
        Environment.GetEnvironmentVariable("THL_HOME")
        ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo");
    public static string PluginsDir => Path.Combine(Root, "plugins");
    static string ConfigPath => Path.Combine(Root, "config.json");

    static readonly JsonSerializerOptions Json = new() { WriteIndented = true, ReadCommentHandling = JsonCommentHandling.Skip };

    public sealed class Config
    {
        [JsonPropertyName("catalogUrl")] public string CatalogUrl { get; set; } = "https://tianheluo.com/plugins.json";
        /// <summary>宿主最新版本信息（可改成本地路径或内网地址，便于离线/测试）。</summary>
        [JsonPropertyName("hostUpdateUrl")] public string HostUpdateUrl { get; set; } = "https://tianheluo.com/host.json";
        [JsonPropertyName("disabled")] public List<string> Disabled { get; set; } = new();
        /// <summary>功能区槽位 → 插件 id。装卸插件后槽位保持稳定，按钮不会串。</summary>
        [JsonPropertyName("slots")] public Dictionary<string, string> Slots { get; set; } = new();
    }

    public const int MaxSlots = 12;

    /// <summary>给插件分配（或取回）一个功能区槽位；没有空位返回 0。</summary>
    public static int AssignSlot(string id)
    {
        var cfg = LoadConfig();
        foreach (var kv in cfg.Slots) if (kv.Value == id && int.TryParse(kv.Key, out var n)) return n;
        for (int i = 1; i <= MaxSlots; i++)
            if (!cfg.Slots.ContainsKey(i.ToString())) { cfg.Slots[i.ToString()] = id; SaveConfig(cfg); return i; }
        return 0;
    }

    public static string? PluginInSlot(int slot)
    {
        var cfg = LoadConfig();
        return cfg.Slots.TryGetValue(slot.ToString(), out var id) ? id : null;
    }

    /// <summary>释放已不存在的插件占用的槽位。</summary>
    public static void ReleaseMissingSlots(IEnumerable<string> installedIds)
    {
        var cfg = LoadConfig(); var keep = installedIds.ToHashSet();
        var drop = cfg.Slots.Where(kv => !keep.Contains(kv.Value)).Select(kv => kv.Key).ToList();
        if (drop.Count == 0) return;
        foreach (var k in drop) cfg.Slots.Remove(k);
        SaveConfig(cfg);
    }

    public static Config LoadConfig()
    {
        try { if (File.Exists(ConfigPath)) return JsonSerializer.Deserialize<Config>(File.ReadAllText(ConfigPath), Json) ?? new(); }
        catch { /* 配置损坏时用默认值 */ }
        return new();
    }

    public static void SaveConfig(Config c)
    {
        Directory.CreateDirectory(Root);
        File.WriteAllText(ConfigPath, JsonSerializer.Serialize(c, Json));
    }

    /// <summary>扫描所有已安装插件（每次调用都重新读磁盘，所以装完立刻可见）。</summary>
    public static List<Installed> List()
    {
        var result = new List<Installed>();
        if (!Directory.Exists(PluginsDir)) return result;
        var cfg = LoadConfig();
        foreach (var dir in Directory.GetDirectories(PluginsDir).OrderBy(d => d, StringComparer.OrdinalIgnoreCase))
        {
            var mf = Path.Combine(dir, "plugin.json");
            if (!File.Exists(mf)) continue;
            try
            {
                var m = JsonSerializer.Deserialize<Manifest>(File.ReadAllText(mf), Json);
                if (m == null || string.IsNullOrWhiteSpace(m.Id) || string.IsNullOrWhiteSpace(m.IsPython ? m.Script : m.Dll)) continue;
                result.Add(new Installed { Manifest = m, Dir = dir, Enabled = !cfg.Disabled.Contains(m.Id) });
            }
            catch { /* 跳过损坏的清单 */ }
        }
        return result;
    }

    public static void SetEnabled(string id, bool enabled)
    {
        var cfg = LoadConfig();
        cfg.Disabled.Remove(id);
        if (!enabled) cfg.Disabled.Add(id);
        SaveConfig(cfg);
    }

    static bool ValidId(string id) =>
        id.Length is > 0 and <= 64 && id.All(c => char.IsAsciiLetterOrDigit(c) || c is '-' or '_');

    /// <summary>从 .thlp（zip）安装；同 id 已存在则原地更新。返回安装后的清单。</summary>
    public static Manifest InstallPackage(string thlpPath)
    {
        using var zip = ZipFile.OpenRead(thlpPath);
        var mfEntry = zip.GetEntry("plugin.json") ?? throw new InvalidDataException("插件包里没有 plugin.json");
        Manifest m;
        using (var s = mfEntry.Open()) m = JsonSerializer.Deserialize<Manifest>(s, Json) ?? throw new InvalidDataException("plugin.json 无法解析");
        if (!ValidId(m.Id)) throw new InvalidDataException("插件 id 只能包含字母、数字、- 和 _");
        if (m.IsPython) { if (string.IsNullOrWhiteSpace(m.Script)) throw new InvalidDataException("plugin.json 缺少 script"); }
        else if (string.IsNullOrWhiteSpace(m.Dll) || string.IsNullOrWhiteSpace(m.Entry)) throw new InvalidDataException("plugin.json 缺少 dll 或 entry");

        var target = Path.Combine(PluginsDir, m.Id);
        var staging = target + ".new";
        try
        {
            if (Directory.Exists(staging)) Directory.Delete(staging, true);
            Directory.CreateDirectory(staging);
            var stagingFull = Path.GetFullPath(staging) + Path.DirectorySeparatorChar;

            foreach (var e in zip.Entries)
            {
                if (string.IsNullOrEmpty(e.Name)) continue; // 目录项
                var dest = Path.GetFullPath(Path.Combine(staging, e.FullName));
                if (!dest.StartsWith(stagingFull, StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("插件包含非法路径：" + e.FullName); // 防 zip-slip
                Directory.CreateDirectory(Path.GetDirectoryName(dest)!);
                e.ExtractToFile(dest, overwrite: true);
            }
            var main = m.IsPython ? m.Script : m.Dll;
            if (!File.Exists(Path.Combine(staging, main))) throw new InvalidDataException("插件包里找不到 " + main);

            // 热更新：宿主每次运行都是从内存加载 DLL，所以磁盘文件可以直接替换
            if (Directory.Exists(target)) Directory.Delete(target, true);
            Directory.Move(staging, target);
        }
        catch
        {
            try { if (Directory.Exists(staging)) Directory.Delete(staging, true); } catch { }
            throw;
        }
        return m;
    }

    public static void Uninstall(string id)
    {
        if (!ValidId(id)) return;
        var dir = Path.Combine(PluginsDir, id);
        if (Directory.Exists(dir)) Directory.Delete(dir, true);
        SetEnabled(id, true); // 清掉停用记录
    }

    // ---------- 商店 ----------
    // 每次请求新建连接并自动重试：Revit 进程里长期复用的连接可能已被服务器关闭
    static HttpClient NewClient() => new(new SocketsHttpHandler
    {
        PooledConnectionLifetime = TimeSpan.FromSeconds(20),
        PooledConnectionIdleTimeout = TimeSpan.FromSeconds(5),
    }) { Timeout = TimeSpan.FromSeconds(30) };

    static async Task<T> WithRetry<T>(Func<HttpClient, Task<T>> op)
    {
        for (int attempt = 1; ; attempt++)
        {
            try { using var c = NewClient(); c.DefaultRequestHeaders.UserAgent.ParseAdd("THL-Host/0.1"); return await op(c); }
            catch (Exception) when (attempt < 3) { await Task.Delay(700 * attempt); }
        }
    }

    /// <summary>把异常链里每一层的消息拼起来，界面上才能看到真实原因。</summary>
    public static string Describe(Exception ex)
    {
        var parts = new List<string>();
        for (var e = ex; e != null; e = e.InnerException)
            if (!string.IsNullOrWhiteSpace(e.Message) && !parts.Contains(e.Message)) parts.Add(e.Message);
        return string.Join(" ← ", parts);
    }


    // ---------- 通用读取（支持 http(s) 地址、本地路径、file:// ；带重试）----------
    static string ToUrl(string urlOrPath) => urlOrPath.Contains("://") ? urlOrPath : new Uri(Path.GetFullPath(urlOrPath)).AbsoluteUri;

    public static async Task<string> ReadTextAsync(string urlOrPath)
    {
        var u = new Uri(ToUrl(urlOrPath));
        return u.IsFile ? await File.ReadAllTextAsync(u.LocalPath) : await WithRetry(c => c.GetStringAsync(u));
    }

    public static async Task<byte[]> ReadBytesAsync(string urlOrPath)
    {
        var u = new Uri(ToUrl(urlOrPath));
        return u.IsFile ? await File.ReadAllBytesAsync(u.LocalPath) : await WithRetry(c => c.GetByteArrayAsync(u));
    }

    /// <summary>把相对地址（/dl/x、/api/dl/x）按基准地址补全；本地清单里的 /xxx 相对清单所在目录。</summary>
    public static string ResolveUrl(string baseUrlOrPath, string relative)
    {
        if (relative.Contains("://")) return relative;
        var b = new Uri(ToUrl(baseUrlOrPath));
        return new Uri(b, b.IsFile ? relative.TrimStart('/') : relative).ToString();
    }

    /// <summary>各插件的下载数（来自网站 /api/stats）。本地清单或联网失败时返回空，不影响商店。</summary>
    public static async Task<Dictionary<string, long>> FetchDownloadCountsAsync()
    {
        var result = new Dictionary<string, long>();
        try
        {
            var cat = ToUrl(LoadConfig().CatalogUrl);
            if (new Uri(cat).IsFile) return result;
            var json = await ReadTextAsync(ResolveUrl(cat, "/api/stats"));
            using var doc = JsonDocument.Parse(json);
            foreach (var p in doc.RootElement.GetProperty("plugins").EnumerateObject())
                result[p.Name] = p.Value.GetProperty("total").GetInt64();
        }
        catch { }
        return result;
    }

    public static async Task<List<CatalogItem>> FetchCatalogAsync()
    {
        var url = LoadConfig().CatalogUrl;
        // 支持本地路径（C:\...\plugins.json）或 file:// 地址，方便离线/内网使用
        if (!url.Contains("://")) url = new Uri(Path.GetFullPath(url)).AbsoluteUri;
        var text = new Uri(url).IsFile ? await File.ReadAllTextAsync(new Uri(url).LocalPath) : await WithRetry(c => c.GetStringAsync(url));
        using var doc = JsonDocument.Parse(text);
        var arr = doc.RootElement.GetProperty("plugins");
        var list = JsonSerializer.Deserialize<List<CatalogItem>>(arr.GetRawText(), Json) ?? new();
        var baseUri = new Uri(url);
        foreach (var c in list) // 站内相对路径（/dl/xxx.thlp）→ 绝对地址
            if (!string.IsNullOrWhiteSpace(c.Download))
            {
                // 本地清单里的 /dl/xxx 要相对清单所在目录，而不是磁盘根目录
                var rel = baseUri.IsFile ? c.Download.TrimStart('/') : c.Download;
                c.Download = new Uri(baseUri, rel).ToString();
            }
        return list.Where(c => c.Status != "planned" && !string.IsNullOrWhiteSpace(c.Download)).ToList();
    }

    /// <summary>下载并安装一个商店条目。</summary>
    public static async Task<Manifest> InstallFromCatalogAsync(CatalogItem item)
    {
        var tmp = Path.Combine(Path.GetTempPath(), "thl-" + Guid.NewGuid().ToString("N") + ".thlp");
        try
        {
            var du = new Uri(item.Download);
            var bytes = du.IsFile ? await File.ReadAllBytesAsync(du.LocalPath) : await WithRetry(c => c.GetByteArrayAsync(item.Download));
            await File.WriteAllBytesAsync(tmp, bytes);
            return InstallPackage(tmp);
        }
        finally { try { File.Delete(tmp); } catch { } }
    }

    public static bool IsNewer(string remote, string local) =>
        Version.TryParse(remote, out var r) && Version.TryParse(local, out var l) && r > l;
}
