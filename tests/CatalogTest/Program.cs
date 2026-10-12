using THL.Host;
int fail = 0;
void Check(string n, bool ok, string x = "") { Console.WriteLine((ok ? "PASS " : "FAIL ") + n + (ok ? "" : "  " + x)); if (!ok) fail++; }

async Task Scenario(string label, string catalogUrl)
{
    Console.WriteLine("--- " + label + " : " + catalogUrl);
    var tmp = Path.Combine(Path.GetTempPath(), "thl-cat-" + Guid.NewGuid().ToString("N"));
    Environment.SetEnvironmentVariable("THL_HOME", tmp);
    Store.SaveConfig(new Store.Config { CatalogUrl = catalogUrl });
    List<CatalogItem> items;
    try { items = await Store.FetchCatalogAsync(); }
    catch (Exception e) { Check(label + " 连接商店", false, e.GetType().Name + ": " + e.Message); return; }
    Check(label + " 连接商店，读到 " + items.Count + " 个插件", items.Count >= 10);
    var pile = items.FirstOrDefault(i => i.Id == "pile-cushion");
    Check(label + " 找到 pile-cushion，下载地址=" + pile?.Download, pile != null && pile.Download.EndsWith("pile-cushion_0.1.0.thlp"));
    if (pile == null) return;
    try
    {
        var m = await Store.InstallFromCatalogAsync(pile);
        Check(label + " 下载并安装 " + m.Name, Store.List().Any(i => i.Manifest.Id == "pile-cushion" && File.Exists(Path.Combine(i.Dir, i.MainFile))));
    }
    catch (Exception e) { Check(label + " 下载并安装", false, e.GetType().Name + ": " + e.Message); }
    try { Directory.Delete(tmp, true); } catch { }
}

await Scenario("线上", "https://tianheluo.com/plugins.json");
await Scenario("本地文件", @"C:\KAIFA\tianheluo-web\plugins.json");
Console.WriteLine(fail == 0 ? "ALL PASS" : fail + " FAILED");
return fail;
