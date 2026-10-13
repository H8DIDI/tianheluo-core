using System.IO.Compression;
using System.Reflection;
using System.Reflection.Emit;
using System.Security.Cryptography;
using System.Text.Json;
using THL.Host;

int fail = 0;
void Check(string n, bool ok, string x = "") { Console.WriteLine((ok ? "PASS " : "FAIL ") + n + (ok ? "" : "  " + x)); if (!ok) fail++; }
async Task<string?> Throws(Func<Task> f) { try { await f(); return null; } catch (Exception e) { return e.Message; } }

var tmp = Path.Combine(Path.GetTempPath(), "thl-upd-" + Guid.NewGuid().ToString("N").Substring(0, 8));
Directory.CreateDirectory(tmp);
Environment.SetEnvironmentVariable("THL_HOME", Path.Combine(tmp, "Tianheluo"));

// 生成一个“指定程序集版本”的真实 DLL，用来模拟更新包里的 THL.Host.dll
byte[] MakeDll(string version)
{
    var ab = new PersistedAssemblyBuilder(new AssemblyName("THL.Host") { Version = Version.Parse(version) }, typeof(object).Assembly);
    ab.DefineDynamicModule("THL.Host");
    var ms = new MemoryStream(); ab.Save(ms); return ms.ToArray();
}
byte[] MakeZip(string dllVersion, Action<ZipArchive>? mutate = null, bool complete = true)
{
    var ms = new MemoryStream();
    using (var z = new ZipArchive(ms, ZipArchiveMode.Create, true))
    {
        if (complete)
        {
            void Put(string n, byte[] d) { var e = z.CreateEntry(n); using var s = e.Open(); s.Write(d); }
            Put("THL.addin", System.Text.Encoding.UTF8.GetBytes("<RevitAddIns/>"));
            Put("THL/THL.Host.dll", MakeDll(dllVersion));
            Put("THL/IronPython.dll", new byte[] { 1 });
        }
        mutate?.Invoke(z);
    }
    return ms.ToArray();
}
string Sha(byte[] b) => Convert.ToHexString(SHA256.HashData(b)).ToLowerInvariant();
string WriteRelease(string version, byte[]? zip, string? sha = null, bool noSha = false)
{
    var dir = Path.Combine(tmp, "site-" + Guid.NewGuid().ToString("N").Substring(0, 6)); Directory.CreateDirectory(dir);
    var assets = new Dictionary<string, object>();
    if (zip != null)
    {
        File.WriteAllBytes(Path.Combine(dir, "pkg.zip"), zip);
        var a = new Dictionary<string, object> { ["url"] = "/pkg.zip", ["size"] = zip.Length };
        if (!noSha) a["sha256"] = sha ?? Sha(zip);
        assets[HostUpdater.Build] = a;
    }
    var path = Path.Combine(dir, "host.json");
    File.WriteAllText(path, JsonSerializer.Serialize(new { version, released = "2026-10-13", notes = "测试说明", assets }));
    Store.SaveConfig(new Store.Config { HostUpdateUrl = path });
    return path;
}

Console.WriteLine($"当前宿主版本(测试程序集) = {HostUpdater.Current}，Build = {HostUpdater.Build}");
Check("当前版本是 0.2.1", HostUpdater.Current == new Version(0, 2, 1));

// ---- 版本比较
Check("0.2.2 比 0.2.1 新", HostUpdater.IsNewer("0.2.2"));
Check("0.3.0 比 0.2.1 新", HostUpdater.IsNewer("0.3.0"));
Check("1.0.0 比 0.2.1 新", HostUpdater.IsNewer("1.0.0"));
Check("0.2.1 不算更新（相同）", !HostUpdater.IsNewer("0.2.1"));
Check("0.1.9 不算更新（更旧）", !HostUpdater.IsNewer("0.1.9"));
Check("无效版本号不算更新", !HostUpdater.IsNewer("abc"));

// ---- 检查
var good = MakeZip("0.2.2");
WriteRelease("0.2.2", good);
var rel = await HostUpdater.CheckAsync();
Check("检查：读到 0.2.2，带说明", rel != null && rel.Version == "0.2.2" && rel.Notes == "测试说明");

// ---- 正常下载 + 暂存
var pending = await HostUpdater.DownloadAndStageAsync(rel!, null);
Check("暂存成功：pending.json 与暂存目录存在", HostUpdater.Pending() is { } p && p.Version == "0.2.2" && Directory.Exists(p.Staging));
Check("暂存内容完整（addin + DLL）", File.Exists(Path.Combine(pending.Staging, "THL.addin")) && File.Exists(Path.Combine(pending.Staging, "THL", "THL.Host.dll")));
HostUpdater.CleanupIfApplied();
Check("启动清理不会删掉更新版本的暂存", HostUpdater.Pending() != null);
HostUpdater.ClearPending();
Check("ClearPending 清干净", HostUpdater.Pending() == null && !Directory.Exists(pending.Staging));

// 待装版本 = 当前版本（已经装好了）→ 启动清理会删掉暂存
{
    WriteRelease("0.2.1", MakeZip("0.2.1"));
    var pp = await HostUpdater.DownloadAndStageAsync((await HostUpdater.CheckAsync())!, null);
    HostUpdater.CleanupIfApplied();
    Check("启动清理：待装版本 = 当前版本 → 已装好，清掉暂存", HostUpdater.Pending() == null && !Directory.Exists(pp.Staging));
}

// ---- 拒绝各种坏包
{
    WriteRelease("0.2.2", MakeZip("0.2.2"), sha: new string('0', 64));
    var msg = await Throws(async () => await HostUpdater.DownloadAndStageAsync((await HostUpdater.CheckAsync())!, null));
    Check("SHA256 不一致 → 拒绝", msg != null && msg.Contains("SHA256"), msg ?? "没抛异常");
    Check("拒绝后没有残留的 pending", HostUpdater.Pending() == null);
}
{
    WriteRelease("0.2.2", MakeZip("0.2.2"), noSha: true);
    var msg = await Throws(async () => await HostUpdater.DownloadAndStageAsync((await HostUpdater.CheckAsync())!, null));
    Check("缺少 SHA256 → 拒绝（不校验就不装）", msg != null && msg.Contains("SHA256"), msg ?? "没抛异常");
}
{
    var z = MakeZip("0.2.2", mutate: arc => { var e = arc.CreateEntry("../../escaped.txt"); using var s = e.Open(); s.Write(new byte[] { 65 }); });
    WriteRelease("0.2.2", z);
    var msg = await Throws(async () => await HostUpdater.DownloadAndStageAsync((await HostUpdater.CheckAsync())!, null));
    Check("zip 路径穿越 → 拒绝", msg != null && msg.Contains("非法路径"), msg ?? "没抛异常");
    Check("没有文件逃出暂存目录", !File.Exists(Path.Combine(tmp, "escaped.txt")) && !File.Exists(Path.Combine(tmp, "Tianheluo", "escaped.txt")));
}
{
    WriteRelease("0.2.2", MakeZip("0.2.2", complete: false, mutate: arc => { var e = arc.CreateEntry("readme.txt"); using var s = e.Open(); s.Write(new byte[] { 65 }); }));
    var msg = await Throws(async () => await HostUpdater.DownloadAndStageAsync((await HostUpdater.CheckAsync())!, null));
    Check("内容不完整（缺 DLL/addin）→ 拒绝", msg != null && msg.Contains("不完整"), msg ?? "没抛异常");
}
{
    WriteRelease("0.2.2", MakeZip("0.2.6"));   // host.json 说 0.2.2，包里 DLL 却是 0.2.6
    var msg = await Throws(async () => await HostUpdater.DownloadAndStageAsync((await HostUpdater.CheckAsync())!, null));
    Check("DLL 版本与 host.json 不一致 → 拒绝", msg != null && msg.Contains("不一致"), msg ?? "没抛异常");
    Check("拒绝后暂存目录被清理", !Directory.Exists(Path.Combine(HostUpdater.UpdatesDir, "host-0.2.2-" + HostUpdater.Build)));
}
{
    WriteRelease("0.2.2", null);   // 没有当前 .NET 版本的资源
    var msg = await Throws(async () => await HostUpdater.DownloadAndStageAsync((await HostUpdater.CheckAsync())!, null));
    Check("没有对应 .NET 版本的更新包 → 明确提示", msg != null && msg.Contains(HostUpdater.Build), msg ?? "没抛异常");
}
{
    Store.SaveConfig(new Store.Config { HostUpdateUrl = Path.Combine(tmp, "does-not-exist.json") });
    var msg = await Throws(async () => await HostUpdater.CheckAsync());
    Check("host.json 不存在 → 抛异常（界面会提示，不会崩）", msg != null);
}

// ---- 宿主内置了应用脚本
{
    using var s = typeof(HostUpdater).Assembly.GetManifestResourceStream("THL.Host.apply-update.ps1");
    Check("宿主内置了 apply-update.ps1", s != null && s.Length > 1000);
}

try { Directory.Delete(tmp, true); } catch { }
Console.WriteLine(fail == 0 ? "ALL PASS" : fail + " FAILED");
return fail;
