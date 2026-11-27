using System.Reflection;
using System.Windows.Input;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Runtime;
using Autodesk.Windows;
using AcApp = Autodesk.AutoCAD.ApplicationServices.Application;

[assembly: ExtensionApplication(typeof(THL.Host.App))]
[assembly: CommandClass(typeof(THL.Host.Commands))]

namespace THL.Host;

/// <summary>
/// AutoCAD 启动时（THL.bundle 自动加载）建立「天河落」功能区选项卡：
///   面板「天河落」= 插件中心；面板「已装插件」= 每个已启用的插件一个按钮（装/卸/停用后立刻同步，不用重启）。
/// 按钮发命令 THLRUN &lt;插件id&gt;，在文档上下文里运行（命令行也能直接敲）。
/// </summary>
public class App : IExtensionApplication
{
    const string TabId = "THL_TAB";
    const string PluginPanelId = "THL_PANEL_PLUGINS";
    static bool _ribbonHooked;

    public void Initialize()
    {
        try
        {
            Directory.CreateDirectory(Store.PluginsDir);
            try { HostUpdater.CleanupIfApplied(); } catch { }
#if NETFRAMEWORK
            InstallDependencyResolver();
#endif
            Log($"启动成功 · AutoCAD {AcadInfo.Year} ({AcApp.GetSystemVariable("ACADVER")}) · .NET {Environment.Version} · {AcadInfo.Fx} · 宿主 {typeof(App).Assembly.GetName().Version}");
            HookRibbon();
            // 切换工作空间时 AutoCAD 会重建功能区，自建的选项卡会丢：重新补上
            AcApp.SystemVariableChanged += (_, e) => { if (string.Equals(e.Name, "WSCURRENT", StringComparison.OrdinalIgnoreCase)) HookRibbon(); };
            TestInbox.Start();
        }
        catch (System.Exception ex)
        {
            Log("启动失败\n" + ex);
            try { AcApp.DocumentManager.MdiActiveDocument?.Editor.WriteMessage("\n天河落启动失败：" + ex.Message + "\n详细信息：" + LogPath + "\n"); } catch { }
        }
    }

    public void Terminate()
    {
        // 有已下载的宿主更新：AutoCAD 退出后由独立脚本替换 THL.bundle（运行中的 DLL 无法替换自己）
        if (HostUpdater.LaunchApplierIfPending()) Log("已启动宿主更新程序，AutoCAD 退出后将自动安装更新。");
    }

    public static string LogPath => Path.Combine(Store.Root, "logs", "startup.log");

    public static void Log(string text)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(LogPath)!);
            File.AppendAllText(LogPath, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {text}\n");
        }
        catch { }
    }

#if NETFRAMEWORK
    /// <summary>.NET Framework 只在 acad.exe 目录里找依赖：System.Text.Json 等随宿主带的 DLL 从宿主目录兜底加载。</summary>
    static void InstallDependencyResolver()
    {
        var dir = Path.GetDirectoryName(typeof(App).Assembly.Location)!;
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

    // ---------------------------------------------------------------- 功能区
    /// <summary>功能区在 AutoCAD 启动后期才建好：等空闲时再建选项卡。</summary>
    static void HookRibbon()
    {
        if (_ribbonHooked) return;
        _ribbonHooked = true;
        AcApp.Idle += OnIdle;
    }

    static void OnIdle(object? sender, EventArgs e)
    {
        if (ComponentManager.Ribbon == null) return;   // 还没建好，下次空闲再试
        AcApp.Idle -= OnIdle;
        _ribbonHooked = false;
        try { BuildRibbon(); } catch (System.Exception ex) { Log("建功能区失败\n" + ex); }
    }

    public static void EnsureRibbon() { try { BuildRibbon(); } catch (System.Exception ex) { Log("建功能区失败 " + ex); } }

    static void BuildRibbon()
    {
        var rc = ComponentManager.Ribbon;
        if (rc == null) return;
        var tab = rc.FindTab(TabId);
        if (tab == null)
        {
            tab = new RibbonTab { Title = "天河落", Id = TabId };
            rc.Tabs.Add(tab);
            var hub = new RibbonPanelSource { Title = "天河落", Id = "THL_PANEL_HUB" };
            hub.Items.Add(MakeButton("THL_OPEN", "插件中心", "THL", "搜索、运行、安装、更新、停用和卸载天河落插件（命令：THL）",
                Icons.Logo(32), Icons.Logo(16)));
            tab.Panels.Add(new RibbonPanel { Source = hub });
            tab.Panels.Add(new RibbonPanel { Source = new RibbonPanelSource { Title = "已装插件", Id = PluginPanelId } });
            Log("功能区已建立：选项卡「天河落」");
        }
        SyncRibbon();
    }

    static RibbonButton MakeButton(string id, string text, string command, string tip, System.Windows.Media.ImageSource? large, System.Windows.Media.ImageSource? small) => new()
    {
        Id = id, Text = text, ShowText = true, ShowImage = true, Size = RibbonItemSize.Large, Orientation = System.Windows.Controls.Orientation.Vertical,
        LargeImage = large, Image = small, ToolTip = tip, CommandParameter = command, CommandHandler = RunCommandHandler.Instance,
    };

    /// <summary>让功能区和磁盘上的已安装插件一致（AutoCAD 的按钮是动态的，不受 Revit 那样的 12 个槽位限制）。</summary>
    public static void SyncRibbon()
    {
        try
        {
            var src = ComponentManager.Ribbon?.FindTab(TabId)?.Panels.FirstOrDefault(p => p.Source?.Id == PluginPanelId)?.Source;
            if (src == null) return;
            src.Items.Clear();
            var installed = Store.List().Where(i => i.Enabled).ToList();
            foreach (var p in installed)
            {
                var name = Store.DisplayName(p.Manifest);
                var pitch = Store.PitchOf(p.Manifest.Id);
                var tip = pitch?.Tip ?? (string.IsNullOrWhiteSpace(p.Manifest.Description) ? name : p.Manifest.Description);
                System.Windows.Media.ImageSource? l = null, s = null;
                try { l = Icons.ForPlugin(p, 32); s = Icons.ForPlugin(p, 16); } catch { }
                src.Items.Add(MakeButton("THL_P_" + p.Manifest.Id, name, "THLRUN " + p.Manifest.Id, tip + "\n命令：THLRUN " + p.Manifest.Id, l, s));
            }
            if (installed.Count == 0)
                src.Items.Add(MakeButton("THL_P_EMPTY", "去商店\n装插件", "THL", "还没有安装插件。打开插件中心 → 商店。", Icons.Logo(32), Icons.Logo(16)));
        }
        catch (System.Exception ex) { Log("同步功能区失败\n" + ex); }
    }
}

/// <summary>
/// 自动测试收件箱：只有环境变量 THL_TEST=1 且设置了 THL_TEST_INBOX=&lt;目录&gt; 时才启用。
/// 空闲时每秒看一次目录，有 *.cmd 文件就读出内容（UTF-8，AutoCAD 命令行文字）、删掉文件、发给当前文档执行。
/// 用来在不碰屏幕的情况下驱动一个专门开的测试实例；正常使用不会启用。
/// </summary>
static class TestInbox
{
    static string? _dir;
    static DateTime _next;

    public static void Start()
    {
        if (Environment.GetEnvironmentVariable("THL_TEST") != "1") return;
        _dir = Environment.GetEnvironmentVariable("THL_TEST_INBOX");
        if (string.IsNullOrEmpty(_dir)) return;
        Directory.CreateDirectory(_dir);
        AcApp.Idle += Poll;
        App.Log("测试收件箱已启用：" + _dir);
    }

    static void Poll(object? sender, EventArgs e)
    {
        if (DateTime.Now < _next) return;
        _next = DateTime.Now.AddSeconds(1);
        try
        {
            var f = Directory.GetFiles(_dir!, "*.cmd").OrderBy(x => x, StringComparer.Ordinal).FirstOrDefault();
            if (f == null) return;
            var text = File.ReadAllText(f);
            File.Delete(f);
            var doc = AcApp.DocumentManager.MdiActiveDocument;
            App.Log("测试收件箱执行 " + Path.GetFileName(f) + "：" + text.Replace("\n", "⏎"));
            if (doc != null) doc.SendStringToExecute(text, true, false, false);
            else if (text.StartsWith("OPEN ", StringComparison.Ordinal)) AcApp.DocumentManager.Open(text.Substring(5).Trim(), false);
        }
        catch (System.Exception ex) { App.Log("测试收件箱出错 " + ex.Message); }
    }
}

/// <summary>功能区按钮 → 往当前文档发命令（文档上下文里才能安全改图）；没有打开的图时直接在应用上下文里跑。</summary>
sealed class RunCommandHandler : ICommand
{
    public static readonly RunCommandHandler Instance = new();
    public event EventHandler? CanExecuteChanged { add { } remove { } }
    public bool CanExecute(object? parameter) => true;

    public void Execute(object? parameter)
    {
        var cmd = (parameter as RibbonButton)?.CommandParameter as string ?? parameter as string;
        if (string.IsNullOrWhiteSpace(cmd)) return;
        var doc = AcApp.DocumentManager.MdiActiveDocument;
        if (doc != null) { doc.SendStringToExecute("_" + cmd + "\n", true, false, true); return; }
        // 开始页（没有图纸）：插件中心、批量类插件也能用
        if (cmd == "THL") Commands.OpenCenter(null);
        else if (cmd.StartsWith("THLRUN ")) Launcher.Run(null, cmd.Substring(7));
    }
}

public class Commands
{
    /// <summary>THL：打开插件中心。</summary>
    [CommandMethod("THL", "THL", CommandFlags.Modal)]
    public void Center() => OpenCenter(AcApp.DocumentManager.MdiActiveDocument);

    /// <summary>THLRUN &lt;插件id&gt;[?参数]：运行一个已装插件。参数（key=value;key=value）给批处理/测试用，插件可以跳过对话框。</summary>
    [CommandMethod("THL", "THLRUN", CommandFlags.Modal)]
    public void RunPlugin()
    {
        var doc = AcApp.DocumentManager.MdiActiveDocument;
        if (doc == null) return;
        var r = doc.Editor.GetString(new PromptStringOptions("\n插件 id") { AllowSpaces = true });
        if (r.Status != PromptStatus.OK || string.IsNullOrWhiteSpace(r.StringResult)) return;
        Launcher.Run(doc, r.StringResult.Trim());
    }

    /// <summary>自动测试用（只有设置了环境变量 THL_TEST=1 才可用）：按 Id 找功能区按钮，走它真正的点击处理（和用户点按钮完全同一条路），并把功能区内容写进日志。</summary>
    [CommandMethod("THL", "THLTESTPRESS", CommandFlags.Modal)]
    public void TestPress()
    {
        if (Environment.GetEnvironmentVariable("THL_TEST") != "1") return;
        var doc = AcApp.DocumentManager.MdiActiveDocument;
        if (ComponentManager.Ribbon?.FindTab("THL_TAB") == null) App.EnsureRibbon();
        var tab = ComponentManager.Ribbon?.FindTab("THL_TAB");
        if (tab != null) tab.IsActive = true;
        var dump = tab == null ? "功能区里没有「天河落」选项卡" :
            $"选项卡「{tab.Title}」：" + string.Join("；", tab.Panels.Select(p => p.Source.Title + " = " + string.Join("、", p.Source.Items.Select(i => i.Text?.Replace("\n", "") + "[" + i.Id + "]"))));
        App.Log("功能区检查：" + dump);
        var r = doc.Editor.GetString(new PromptStringOptions("\n按钮 Id") { AllowSpaces = false });
        if (r.Status != PromptStatus.OK) return;
        var btn = tab?.Panels.SelectMany(p => p.Source.Items).OfType<RibbonButton>().FirstOrDefault(b => b.Id == r.StringResult);
        if (btn == null) { App.Log("没找到按钮 " + r.StringResult); return; }
        App.Log($"测试点击按钮 {btn.Id}（{btn.Text}）→ {btn.CommandParameter}");
        btn.CommandHandler.Execute(btn);
    }

    public static void OpenCenter(Document? doc)
    {
        var win = new LauncherWindow();
        var snap = Environment.GetEnvironmentVariable("THL_SNAP_DIR");
        if (!string.IsNullOrEmpty(snap))
        {
            // 自动测试：窗口显示几秒（等商店清单加载）后截图存 launcher.png 并关闭
            var timer = new System.Windows.Threading.DispatcherTimer { Interval = TimeSpan.FromSeconds(6) };
            timer.Tick += (_, _) =>
            {
                timer.Stop();
                try
                {
                    Directory.CreateDirectory(snap);
                    var dpi = System.Windows.Media.VisualTreeHelper.GetDpi(win);
                    var rtb = new System.Windows.Media.Imaging.RenderTargetBitmap((int)(win.ActualWidth * dpi.DpiScaleX), (int)(win.ActualHeight * dpi.DpiScaleY), dpi.PixelsPerInchX, dpi.PixelsPerInchY, System.Windows.Media.PixelFormats.Pbgra32);
                    // 先铺窗口背景（只渲染内容时背景是透明的，深色主题的白字会看不见）
                    var bg = new System.Windows.Media.DrawingVisual();
                    using (var dc = bg.RenderOpen())
                        dc.DrawRectangle(win.Background ?? System.Windows.Media.Brushes.White, null, new System.Windows.Rect(0, 0, win.ActualWidth, win.ActualHeight));
                    rtb.Render(bg);
                    rtb.Render((System.Windows.Media.Visual)win.Content);
                    var enc = new System.Windows.Media.Imaging.PngBitmapEncoder();
                    enc.Frames.Add(System.Windows.Media.Imaging.BitmapFrame.Create(rtb));
                    using var fs = File.Create(Path.Combine(snap, "launcher.png"));
                    enc.Save(fs);
                }
                catch (System.Exception ex) { App.Log("截图失败 " + ex.Message); }
                win.Close();
            };
            win.Loaded += (_, _) => timer.Start();
        }
        AcApp.ShowModalWindow(win);
        App.SyncRibbon();   // 装/卸/停用后，功能区立刻跟上
        if (win.RunTarget is { } p) Launcher.Run(doc, p.Manifest.Id);
    }
}
