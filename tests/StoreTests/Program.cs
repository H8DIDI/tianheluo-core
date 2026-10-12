using System.IO.Compression;
using THL.Host;

int fail = 0;
void Check(string name, bool ok) { Console.WriteLine((ok ? "PASS " : "FAIL ") + name); if (!ok) fail++; }

// 用独立的 APPDATA，避免动到真实数据
var tmp = Path.Combine(Path.GetTempPath(), "thl-test-" + Guid.NewGuid().ToString("N"));
Environment.SetEnvironmentVariable("THL_HOME", Path.Combine(tmp, "Tianheluo"));
Directory.CreateDirectory(tmp);
Console.WriteLine("Root = " + Store.Root);
Check("Root 在测试目录下", Store.Root.StartsWith(tmp));

var dist = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../../../../../dist"));
var pkgs = Directory.GetFiles(dist, "*.thlp");
Check("dist 里有插件包", pkgs.Length >= 4);

foreach (var p in pkgs) Store.InstallPackage(p);
var list = Store.List();
Check("安装后列出全部插件", list.Count == pkgs.Length);
Check("每个插件的主文件都存在", list.All(i => File.Exists(Path.Combine(i.Dir, i.MainFile))));
Check("识别出 Python 型插件", list.Count(i => i.Manifest.IsPython) >= 10);

Store.SetEnabled("well-depth", false);
Check("停用生效", !Store.List().First(i => i.Manifest.Id == "well-depth").Enabled);
Store.SetEnabled("well-depth", true);
Check("再启用生效", Store.List().First(i => i.Manifest.Id == "well-depth").Enabled);

// 原地更新：改版本号后重装，旧文件被替换
Store.InstallPackage(pkgs[0]);
Check("重复安装不产生重复项", Store.List().Count == pkgs.Length);

Store.Uninstall("well-number");
Check("卸载后少一个", Store.List().Count == pkgs.Length - 1);

// 恶意包：zip-slip
var evil = Path.Combine(tmp, "evil.thlp");
using (var z = ZipFile.Open(evil, ZipArchiveMode.Create))
{
    var e = z.CreateEntry("plugin.json");
    using (var w = new StreamWriter(e.Open())) w.Write("{\"id\":\"evil\",\"name\":\"x\",\"version\":\"1.0.0\",\"dll\":\"a.dll\",\"entry\":\"A.B\"}");
    z.CreateEntry("a.dll");
    var bad = z.CreateEntry("../../escaped.txt");
    using (var w = new StreamWriter(bad.Open())) w.Write("pwned");
}
bool threw = false; try { Store.InstallPackage(evil); } catch (InvalidDataException) { threw = true; }
Check("路径穿越的包被拒绝", threw);
Check("没有文件逃出安装目录", !File.Exists(Path.Combine(tmp, "Tianheluo", "escaped.txt")) && !File.Exists(Path.Combine(tmp, "escaped.txt")));
Check("失败的安装不留残余", !Directory.Exists(Path.Combine(Store.PluginsDir, "evil")) && !Directory.Exists(Path.Combine(Store.PluginsDir, "evil.new")));

// 非法 id
var badId = Path.Combine(tmp, "badid.thlp");
using (var z = ZipFile.Open(badId, ZipArchiveMode.Create))
{
    var e = z.CreateEntry("plugin.json");
    using (var w = new StreamWriter(e.Open())) w.Write("{\"id\":\"../x\",\"name\":\"x\",\"version\":\"1.0.0\",\"dll\":\"a.dll\",\"entry\":\"A.B\"}");
}
threw = false; try { Store.InstallPackage(badId); } catch (InvalidDataException) { threw = true; }
Check("非法 id 被拒绝", threw);

Check("版本比较 0.2.0 > 0.1.0", Store.IsNewer("0.2.0", "0.1.0"));
Check("版本比较 0.1.0 不大于 0.1.0", !Store.IsNewer("0.1.0", "0.1.0"));

Directory.Delete(tmp, true);
Console.WriteLine(fail == 0 ? "ALL PASS" : fail + " FAILED");
return fail;
