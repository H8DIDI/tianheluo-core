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
    /// <summary>插件所需的宿主接口版本（缺省 1）。高于宿主支持的版本时拒绝安装/运行，而不是悄悄出错。</summary>
    [JsonPropertyName("hostApi")] public int HostApi { get; set; } = 1;
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
    /// <summary>安装包的 SHA256（小写十六进制）。有就校验，不符拒绝安装。</summary>
    [JsonPropertyName("sha256")] public string Sha256 { get; set; } = "";
    [JsonPropertyName("hostApi")] public int HostApi { get; set; } = 1;
    /// <summary>official（官方）/ community（社区投稿）</summary>
    [JsonPropertyName("tier")] public string Tier { get; set; } = "";
    [JsonPropertyName("author")] public string Author { get; set; } = "";
    public bool IsCommunity => string.Equals(Tier, "community", StringComparison.OrdinalIgnoreCase);
    /// <summary>支持的 Revit 版本，写法同 plugin.json 的 revit（"2020"、"2020,2026"、"2020-2026"、"2024+"）。缺省按 2026。</summary>
    [JsonPropertyName("revit")] public string Revit { get; set; } = "2026";
    /// <summary>一句话看懂：【动作】对象 → 结果（网站 content/plugins/&lt;id&gt;.json 写的，清单里带下来）。</summary>
    [JsonPropertyName("pitch")] public Pitch? Pitch { get; set; }
    /// <summary>改名前的旧名字：搜旧名也能搜到。</summary>
    [JsonPropertyName("aliases")] public List<string> Aliases { get; set; } = new();
    public string SearchText => Name + " " + Id + " " + Summary + " " + string.Join(" ", Aliases) + " " + (Pitch?.Text ?? "");
}

/// <summary>插件一句话：verb=动作、obj=对象、result=结果、how=怎么用（一键/框选/点选/先选中/对话框）、safe=只读/先预览/可撤销/可还原/可恢复。</summary>
public sealed class Pitch
{
    [JsonPropertyName("verb")] public string Verb { get; set; } = "";
    [JsonPropertyName("obj")] public string Obj { get; set; } = "";
    [JsonPropertyName("result")] public string Result { get; set; } = "";
    [JsonPropertyName("how")] public string How { get; set; } = "";
    [JsonPropertyName("safe")] public string Safe { get; set; } = "";
    public bool Valid => Verb.Length > 0 && Obj.Length > 0;
    public string Text => Valid ? $"【{Verb}】{Obj} → {Result}" : "";
    /// <summary>功能区按钮的提示：第一行一句话，第二行怎么用 / 会不会改模型。</summary>
    public string Tip => Valid ? Text + "\n" + string.Join(" · ", new[] { How, Safe }.Where(s => s.Length > 0)) : "";
}

/// <summary>用户数据目录 + 已安装插件管理（安装 / 卸载 / 启用 / 停用）</summary>
public static class Store
{
    /// <summary>本宿主支持的插件接口版本。改动插件接口（破坏兼容）时加 1，并在发布说明里写明。</summary>
    public const int HostApiVersion = 1;

    /// <summary>用户数据目录。可用环境变量 THL_HOME 覆盖（测试用）。</summary>
    public static readonly string Root =
        Environment.GetEnvironmentVariable("THL_HOME")
#if NETFRAMEWORK
        // Revit 2020 宿主用独立的数据目录：2026 版插件（.NET 8/10）不能在 2020 里运行，两边不能共用一份已装插件
        ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo", "Revit2020");
#else
        ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo");
#endif
    public static string PluginsDir => Path.Combine(Root, "plugins");
    static string ConfigPath => Path.Combine(Root, "config.json");

    static readonly JsonSerializerOptions Json = new() { WriteIndented = true, ReadCommentHandling = JsonCommentHandling.Skip };

    public sealed class Config
    {
#if NETFRAMEWORK
        // Revit 2020 宿主：单独的商店清单和宿主更新地址（2026 的插件 / 宿主更新包不能用在 2020）
        [JsonPropertyName("catalogUrl")] public string CatalogUrl { get; set; } = "https://tianheluo.com/plugins2020.json";
#else
        [JsonPropertyName("catalogUrl")] public string CatalogUrl { get; set; } = "https://tianheluo.com/plugins.json";
#endif
        /// <summary>宿主最新版本信息（可改成本地路径或内网地址，便于离线/测试）。</summary>
#if NETFRAMEWORK
        [JsonPropertyName("hostUpdateUrl")] public string HostUpdateUrl { get; set; } = "https://tianheluo.com/host2020.json";
#else
        [JsonPropertyName("hostUpdateUrl")] public string HostUpdateUrl { get; set; } = "https://tianheluo.com/host.json";
#endif
        [JsonPropertyName("disabled")] public List<string> Disabled { get; set; } = new();
        /// <summary>功能区槽位 → 插件 id。装卸插件后槽位保持稳定，按钮不会串。</summary>
        [JsonPropertyName("slots")] public Dictionary<string, string> Slots { get; set; } = new();
    }

    public const int MaxSlots = 12;

    /// <summary>这个宿主跑在哪个 Revit 年份上：net472 版 = 2020，其余 = 2026。</summary>
#if NETFRAMEWORK
    public const int HostRevitYear = 2020;
#else
    public const int HostRevitYear = 2026;
#endif

    /// <summary>版本说明（"2020"、"2020,2026"、"2020-2026"、"2024+"）是否包含当前宿主的 Revit 版本。空 = 只算 2026。</summary>
    public static bool SupportsRevit(string? spec, int year = 0)
    {
        if (year == 0) year = HostRevitYear;
        if (string.IsNullOrWhiteSpace(spec)) spec = "2026";
        foreach (var raw in spec!.Split(new[] { ',', ';', ' ', '、', '，' }, StringSplitOptions.RemoveEmptyEntries))
        {
            var t = raw.Trim();
            if (t.EndsWith("+")) { if (int.TryParse(t.TrimEnd('+'), out var lo) && year >= lo) return true; }
            else if (t.Contains("-")) { var ab = t.Split('-'); if (ab.Length == 2 && int.TryParse(ab[0], out var a) && int.TryParse(ab[1], out var b) && year >= a && year <= b) return true; }
            else if (int.TryParse(t, out var y) && y == year) return true;
        }
        return false;
    }

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
                if (!SupportsRevit(m.Revit)) continue; // 不支持当前 Revit 版本的插件不显示、不出按钮
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
        id.Length is > 0 and <= 64 && id.All(c => (c < 128 && char.IsLetterOrDigit(c)) || c is '-' or '_');

    /// <summary>从 .thlp（zip）安装；同 id 已存在则原地更新。返回安装后的清单。</summary>
    public static Manifest InstallPackage(string thlpPath)
    {
        using var zip = ZipFile.OpenRead(thlpPath);
        var mfEntry = zip.GetEntry("plugin.json") ?? throw new InvalidDataException("插件包里没有 plugin.json");
        Manifest m;
        using (var s = mfEntry.Open()) m = JsonSerializer.Deserialize<Manifest>(s, Json) ?? throw new InvalidDataException("plugin.json 无法解析");
        if (!ValidId(m.Id)) throw new InvalidDataException("插件 id 只能包含字母、数字、- 和 _");
        if (!SupportsRevit(m.Revit)) throw new InvalidDataException($"「{m.Name}」不支持 Revit {HostRevitYear}（适用版本：{m.Revit}）。");
        if (m.HostApi > HostApiVersion) throw new InvalidDataException($"「{m.Name}」需要更新的宿主（插件接口 v{m.HostApi}，当前宿主支持 v{HostApiVersion}）。请先在插件中心更新宿主。");
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
#if NETFRAMEWORK
    static HttpClient NewClient()
    {
        // .NET Framework 默认只开 TLS 1.0/1.1；Cloudflare 要 TLS 1.2
        System.Net.ServicePointManager.SecurityProtocol |= System.Net.SecurityProtocolType.Tls12;
        return new HttpClient(new HttpClientHandler()) { Timeout = TimeSpan.FromSeconds(30) };
    }
#else
    static HttpClient NewClient() => new(new SocketsHttpHandler
    {
        PooledConnectionLifetime = TimeSpan.FromSeconds(20),
        PooledConnectionIdleTimeout = TimeSpan.FromSeconds(5),
    }) { Timeout = TimeSpan.FromSeconds(30) };
#endif

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
        return u.IsFile ? await ReadAllTextAsyncC(u.LocalPath) : await WithRetry(c => c.GetStringAsync(u));
    }

    public static async Task<byte[]> ReadBytesAsync(string urlOrPath)
    {
        var u = new Uri(ToUrl(urlOrPath));
        return u.IsFile ? await ReadAllBytesAsyncC(u.LocalPath) : await WithRetry(c => c.GetByteArrayAsync(u));
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
        var text = new Uri(url).IsFile ? await ReadAllTextAsyncC(new Uri(url).LocalPath) : await WithRetry(c => c.GetStringAsync(url));
        var list = ParseCatalog(text);
        // 存一份到本地：功能区按钮名字、提示用它（离线时也有），不用为改个名字重新发插件包
        try { Directory.CreateDirectory(Root); File.WriteAllText(CatalogCachePath, text); _cached = null; } catch { }
        var baseUri = new Uri(url);
        foreach (var c in list) // 站内相对路径（/dl/xxx.thlp）→ 绝对地址
            if (!string.IsNullOrWhiteSpace(c.Download))
            {
                // 本地清单里的 /dl/xxx 要相对清单所在目录，而不是磁盘根目录
                var rel = baseUri.IsFile ? c.Download.TrimStart('/') : c.Download;
                c.Download = new Uri(baseUri, rel).ToString();
            }
        return list.Where(c => c.Status != "planned" && !string.IsNullOrWhiteSpace(c.Download) && SupportsRevit(c.Revit)).ToList();
    }

    static List<CatalogItem> ParseCatalog(string text)
    {
        using var doc = JsonDocument.Parse(text);
        return JsonSerializer.Deserialize<List<CatalogItem>>(doc.RootElement.GetProperty("plugins").GetRawText(), Json) ?? new();
    }

    static string CatalogCachePath => Path.Combine(Root, "catalog-cache.json");
    static Dictionary<string, CatalogItem>? _cached;

    /// <summary>上次成功读到的商店清单（本地缓存）里的这个插件；没有缓存返回 null。</summary>
    public static CatalogItem? Cached(string id)
    {
        if (_cached == null)
        {
            _cached = new();
            try { if (File.Exists(CatalogCachePath)) foreach (var c in ParseCatalog(File.ReadAllText(CatalogCachePath))) _cached[c.Id] = c; }
            catch { /* 缓存坏了就当没有 */ }
        }
        return _cached.TryGetValue(id, out var item) ? item : null;
    }

    /// <summary>显示用的名字：商店里改过名就用新名字，否则用插件包里的名字。</summary>
    public static string DisplayName(Manifest m)
    {
        var c = Cached(m.Id);
        return c != null && !string.IsNullOrWhiteSpace(c.Name) ? c.Name : m.Name;
    }

    public static Pitch? PitchOf(string id) => Cached(id)?.Pitch is { Valid: true } p ? p : null;

    /// <summary>下载并安装一个商店条目。</summary>
    public static async Task<Manifest> InstallFromCatalogAsync(CatalogItem item)
    {
        var tmp = Path.Combine(Path.GetTempPath(), "thl-" + Guid.NewGuid().ToString("N") + ".thlp");
        try
        {
            var du = new Uri(item.Download);
            var bytes = du.IsFile ? await ReadAllBytesAsyncC(du.LocalPath) : await WithRetry(c => c.GetByteArrayAsync(item.Download));
            if (!string.IsNullOrWhiteSpace(item.Sha256))
            {
                var got = Sha256Hex(bytes);
                if (!string.Equals(got, item.Sha256.Trim(), StringComparison.OrdinalIgnoreCase))
                    throw new InvalidDataException("下载的文件校验失败（SHA256 与商店清单不一致），已拒绝安装。请稍后重试；若反复出现请联系维护者。");
            }
            await WriteAllBytesAsyncC(tmp, bytes);
            return InstallPackage(tmp);
        }
        finally { try { File.Delete(tmp); } catch { } }
    }

    // 读写文件的异步包装：.NET Framework 没有 File.ReadAllTextAsync 等
    static Task<string> ReadAllTextAsyncC(string p) => Task.Run(() => File.ReadAllText(p));
    static Task<byte[]> ReadAllBytesAsyncC(string p) => Task.Run(() => File.ReadAllBytes(p));
    static Task WriteAllBytesAsyncC(string p, byte[] b) => Task.Run(() => File.WriteAllBytes(p, b));
    public static string Sha256Hex(byte[] data)
    {
        using var h = System.Security.Cryptography.SHA256.Create();
        var sb = new System.Text.StringBuilder(64);
        foreach (var b in h.ComputeHash(data)) sb.Append(b.ToString("x2"));
        return sb.ToString();
    }

    public static bool IsNewer(string remote, string local) =>
        Version.TryParse(remote, out var r) && Version.TryParse(local, out var l) && r > l;
}
