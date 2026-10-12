using System.Reflection;
using System.Windows.Media.Imaging;
using Autodesk.Revit.UI;

namespace THL.Host;

/// <summary>Revit 启动时建立「天河落」功能区；之后安装/卸载/停用插件会即时同步功能区（无需重启）。</summary>
public class App : IExternalApplication
{
    const string TabName = "天河落";
    public const int MaxSlots = Store.MaxSlots;
    static RibbonPanel? _panel;
    static string _asmPath = "";
    static readonly Dictionary<int, PushButton> _buttons = new();

    public Result OnStartup(UIControlledApplication app)
    {
        try
        {
            var r = StartupCore(app);
            Log($"启动成功 · Revit {app.ControlledApplication.VersionNumber} ({app.ControlledApplication.VersionBuild}) · .NET {Environment.Version} · 宿主 {typeof(App).Assembly.GetName().Version}");
            return r;
        }
        catch (Exception ex)
        {
            Log($"启动失败 · Revit {SafeVersion(app)} · .NET {Environment.Version}\n{ex}");
            try { Autodesk.Revit.UI.TaskDialog.Show("天河落 · 启动失败", $"{ex.GetType().Name}: {ex.Message}\n\n详细信息已保存到：\n{LogPath}\n请把这个文件发给供应商。"); } catch { }
            return Result.Failed;
        }
    }

    static string SafeVersion(UIControlledApplication app)
    {
        try { return $"{app.ControlledApplication.VersionNumber} ({app.ControlledApplication.VersionBuild})"; } catch { return "?"; }
    }

    static string LogPath => Path.Combine(Store.Root, "logs", "startup.log");

    static void Log(string text)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);
            File.AppendAllText(LogPath, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {text}\n");
        }
        catch { }
    }

    Result StartupCore(UIControlledApplication app)
    {
        try { Directory.CreateDirectory(Store.PluginsDir); } catch { }
        _asmPath = Assembly.GetExecutingAssembly().Location;

        app.CreateRibbonTab(TabName);
        var hub = app.CreateRibbonPanel(TabName, "天河落");
        var open = (PushButton)hub.AddItem(new PushButtonData("THL_Open", "插件中心", _asmPath, typeof(OpenLauncher).FullName)
        {
            ToolTip = "搜索、运行、安装、更新、停用和卸载天河落插件",
            LongDescription = "天河落 THL：Revit 插件宿主。",
        });
        try { open.LargeImage = Icons.Logo(32); open.Image = Icons.Logo(16); } catch { }

        _panel = app.CreateRibbonPanel(TabName, "已装插件");
        SyncRibbon();
        return Result.Succeeded;
    }


    public Result OnShutdown(UIControlledApplication app) => Result.Succeeded;

    /// <summary>让功能区和磁盘上的已安装插件一致：新装的补按钮，卸载/停用的隐藏。</summary>
    public static void SyncRibbon()
    {
        if (_panel == null) return;
        try
        {
            var installed = Store.List();
            Store.ReleaseMissingSlots(installed.Select(i => i.Manifest.Id));
            var visible = new HashSet<int>();
            foreach (var p in installed.Where(i => i.Enabled))
            {
                int slot = Store.AssignSlot(p.Manifest.Id);
                if (slot == 0) continue;
                visible.Add(slot);
                if (!_buttons.TryGetValue(slot, out var btn))
                {
                    var data = new PushButtonData($"THL_Slot{slot:00}", p.Manifest.Name, _asmPath, $"THL.Host.Slot{slot:00}");
                    btn = (PushButton)_panel.AddItem(data);
                    _buttons[slot] = btn;
                }
                btn.ItemText = p.Manifest.Name;
                btn.ToolTip = string.IsNullOrWhiteSpace(p.Manifest.Description) ? p.Manifest.Name : p.Manifest.Description;
                try { btn.LargeImage = Icons.ForPlugin(p, 32); btn.Image = Icons.ForPlugin(p, 16); } catch { }
                btn.Visible = true;
            }
            foreach (var kv in _buttons) if (!visible.Contains(kv.Key)) kv.Value.Visible = false;
        }
        catch { /* 功能区同步失败不影响插件中心 */ }
    }
}
