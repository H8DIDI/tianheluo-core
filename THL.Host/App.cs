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
        try { HostUpdater.CleanupIfApplied(); } catch { }
        _asmPath = Assembly.GetExecutingAssembly().Location;
#if NETFRAMEWORK
        InstallDependencyResolver();
#endif

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


#if NETFRAMEWORK
    /// <summary>
    /// .NET Framework 不会按“版本号不同但名字相同”自动重定向（Revit 没有给我们的 DLL 配 binding redirect）：
    /// IronPython、System.Text.Json 各自引用的 System.Memory 等版本略有差别时会报“找不到程序集”。
    /// 这里按名字从宿主目录兜底加载，保证随宿主一起部署的依赖都能用。
    /// </summary>
    static void InstallDependencyResolver()
    {
        var dir = Path.GetDirectoryName(_asmPath)!;
        AppDomain.CurrentDomain.AssemblyResolve += (_, args) =>
        {
            try
            {
                var name = new AssemblyName(args.Name).Name;
                if (name.EndsWith(".resources", StringComparison.OrdinalIgnoreCase)) return null;
                var f = Path.Combine(dir, name + ".dll");
                if (!File.Exists(f)) return null;
                foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
                    if (string.Equals(a.GetName().Name, name, StringComparison.OrdinalIgnoreCase) && !a.IsDynamic) return a;
                return Assembly.LoadFrom(f);
            }
            catch { return null; }
        };
    }
#endif

    public Result OnShutdown(UIControlledApplication app)
    {
        // 有已下载的宿主更新：Revit 退出后由独立脚本替换文件（运行中的 DLL 无法替换自己）
        if (HostUpdater.LaunchApplierIfPending()) Log("已启动宿主更新程序，Revit 退出后将自动安装更新。");
        return Result.Succeeded;
    }

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
                var name = Store.DisplayName(p.Manifest);
                var pitch = Store.PitchOf(p.Manifest.Id);
                btn.ItemText = name;
                // 提示第一行就是一句话看懂；详细说明放到按住不放才出来的长说明里
                btn.ToolTip = pitch?.Tip ?? (string.IsNullOrWhiteSpace(p.Manifest.Description) ? name : p.Manifest.Description);
                if (pitch != null && !string.IsNullOrWhiteSpace(p.Manifest.Description)) btn.LongDescription = p.Manifest.Description;
                try { btn.LargeImage = Icons.ForPlugin(p, 32); btn.Image = Icons.ForPlugin(p, 16); } catch { }
                btn.Visible = true;
            }
            foreach (var kv in _buttons) if (!visible.Contains(kv.Key)) kv.Value.Visible = false;
        }
        catch { /* 功能区同步失败不影响插件中心 */ }
    }
}
