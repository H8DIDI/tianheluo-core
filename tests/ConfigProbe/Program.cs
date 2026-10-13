using System.Text.Json;
using THL.Host;

var tmp = Path.Combine(Path.GetTempPath(), "thl-cfgprobe-" + Guid.NewGuid().ToString("N").Substring(0, 6));
Directory.CreateDirectory(tmp);
Environment.SetEnvironmentVariable("THL_HOME", tmp);

// 1) 用 Python 的风格写：无 slots 的 JSON（和 prepare_e2e.py 写的一样）
File.WriteAllText(Path.Combine(tmp, "config.json"),
    "{\n  \"catalogUrl\": \"C:\\\\KAIFA\\\\thl\\\\_e2e\\\\site\\\\plugins.json\",\n  \"disabled\": [],\n  \"hostUpdateUrl\": \"C:\\\\KAIFA\\\\thl\\\\_e2e\\\\site\\\\host.json\"\n}");
var c1 = Store.LoadConfig();
Console.WriteLine("python-style file -> catalogUrl=" + c1.CatalogUrl);

// 2) 对一个“没有 slots”的配置调用 AssignSlot（宿主启动时 SyncRibbon 会做这件事），看会不会把配置写回默认
var slot = Store.AssignSlot("some-plugin");
var c2 = Store.LoadConfig();
Console.WriteLine($"after AssignSlot -> slot={slot} catalogUrl={c2.CatalogUrl} hostUpdateUrl={c2.HostUpdateUrl}");
Console.WriteLine(File.ReadAllText(Path.Combine(tmp, "config.json")));
