using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Microsoft.Win32;

namespace THL.Host;

/// <summary>插件中心：搜索 / 运行 / 安装 / 更新 / 停用 / 卸载。纯代码构建的深色窗口。</summary>
public sealed class LauncherWindow : Window
{
    /// <summary>用户选择「运行」的插件；窗口关闭后由 OpenLauncher 执行。</summary>
    public Installed? RunTarget { get; private set; }

    static Brush B(string hex) => (Brush)new BrushConverter().ConvertFromString(hex)!;
    static readonly Brush Bg = B("#0B0B0E"), Card = B("#14141A"), Line = B("#26262F"), Fg = B("#F2F4F7"),
                          Mute = B("#8E96A3"), Blue = B("#3B9BFF"), Ok = B("#3DDC97"), Warn = B("#FFC531");

    readonly TextBox _search = new();
    readonly StackPanel _list = new();
    readonly TextBlock _status = new();
    readonly Button _tabInstalled = new(), _tabStore = new();
    bool _storeTab;
    List<CatalogItem>? _catalog;

    public LauncherWindow()
    {
        Title = "天河落 · 插件中心";
        Width = 760; Height = 600; MinWidth = 560; MinHeight = 420;
        WindowStartupLocation = WindowStartupLocation.CenterScreen;
        Background = Bg; Foreground = Fg;
        FontFamily = new FontFamily("Microsoft YaHei UI, Segoe UI");

        var root = new DockPanel { Margin = new Thickness(0) };

        // 搜索框
        var top = new Border { Padding = new Thickness(20, 16, 20, 12), BorderBrush = Line, BorderThickness = new Thickness(0, 0, 0, 1) };
        _search.FontSize = 18; _search.Background = Brushes.Transparent; _search.Foreground = Fg;
        _search.CaretBrush = Blue; _search.BorderThickness = new Thickness(0); _search.Padding = new Thickness(2, 6, 2, 6);
        _search.TextChanged += (_, _) => Render();
        _search.KeyDown += (_, e) =>
        {
            if (e.Key == Key.Enter && !_storeTab) { var first = Filter(Store.List()).FirstOrDefault(p => p.Enabled); if (first != null) Run(first); }
        };
        var ph = new TextBlock { Text = "搜索插件…", Foreground = B("#5D5D69"), FontSize = 18, IsHitTestVisible = false, Margin = new Thickness(8, 6, 0, 0) };
        _search.TextChanged += (_, _) => ph.Visibility = _search.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed;
        var logo = new System.Windows.Controls.Image { Source = Icons.Logo(64), Width = 30, Height = 30, Margin = new Thickness(0, 0, 12, 0), VerticalAlignment = VerticalAlignment.Center };
        RenderOptions.SetBitmapScalingMode(logo, BitmapScalingMode.HighQuality);
        var searchGrid = new Grid { Children = { _search, ph } };
        var topRow = new DockPanel();
        DockPanel.SetDock(logo, Dock.Left);
        topRow.Children.Add(logo); topRow.Children.Add(searchGrid);
        top.Child = topRow;
        DockPanel.SetDock(top, Dock.Top); root.Children.Add(top);

        // 标签
        var tabs = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(20, 10, 20, 0) };
        StyleTab(_tabInstalled, "已安装"); StyleTab(_tabStore, "商店");
        _tabInstalled.Click += (_, _) => { _storeTab = false; Render(); };
        _tabStore.Click += async (_, _) => { _storeTab = true; Render(); await LoadCatalogAsync(); };
        tabs.Children.Add(_tabInstalled); tabs.Children.Add(_tabStore);
        DockPanel.SetDock(tabs, Dock.Top); root.Children.Add(tabs);

        // 底栏
        var bottom = new Border { Padding = new Thickness(20, 10, 20, 10), BorderBrush = Line, BorderThickness = new Thickness(0, 1, 0, 0) };
        var bar = new DockPanel();
        var install = MakeButton("安装插件包…", false);
        install.Click += (_, _) => InstallFromFile();
        DockPanel.SetDock(install, Dock.Right);
        _status.Foreground = Mute; _status.VerticalAlignment = VerticalAlignment.Center; _status.TextTrimming = TextTrimming.CharacterEllipsis;
        bar.Children.Add(install); bar.Children.Add(_status);
        bottom.Child = bar;
        DockPanel.SetDock(bottom, Dock.Bottom); root.Children.Add(bottom);

        // 列表
        var scroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = _list, Padding = new Thickness(12, 8, 12, 8) };
        root.Children.Add(scroll);
        Content = root;

        KeyDown += (_, e) => { if (e.Key == Key.Escape) Close(); };
        Loaded += (_, _) => { _search.Focus(); Render(); };
    }

    // ---------- 渲染 ----------
    IEnumerable<Installed> Filter(IEnumerable<Installed> src)
    {
        var q = _search.Text.Trim();
        return q.Length == 0 ? src : src.Where(p => (p.Manifest.Name + p.Manifest.Id + p.Manifest.Description + p.Manifest.Category)
            .Contains(q, StringComparison.OrdinalIgnoreCase));
    }

    void Render()
    {
        _tabInstalled.Foreground = _storeTab ? Mute : Fg; _tabStore.Foreground = _storeTab ? Fg : Mute;
        _list.Children.Clear();
        if (_storeTab) RenderStore(); else RenderInstalled();
    }

    void RenderInstalled()
    {
        var items = Filter(Store.List()).ToList();
        if (items.Count == 0)
        {
            _list.Children.Add(Empty(Store.List().Count == 0
                ? "还没有安装任何插件。\n到「商店」页一键安装，或点右下角「安装插件包…」选择 .thlp 文件。"
                : "没有匹配的插件。"));
            return;
        }
        foreach (var p in items)
        {
            var run = MakeButton("运行", true); run.IsEnabled = p.Enabled; run.Click += (_, _) => Run(p);
            var toggle = MakeButton(p.Enabled ? "停用" : "启用", false);
            toggle.Click += (_, _) => { Store.SetEnabled(p.Manifest.Id, !p.Enabled); Flash("已" + (p.Enabled ? "停用" : "启用") + "「" + p.Manifest.Name + "」。功能区按钮在重启 Revit 后更新。"); Render(); };
            var del = MakeButton("卸载", false);
            del.Click += (_, _) =>
            {
                if (MessageBox.Show($"确定卸载「{p.Manifest.Name}」？", "天河落", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
                try { Store.Uninstall(p.Manifest.Id); Flash("已卸载「" + p.Manifest.Name + "」"); } catch (Exception ex) { Flash("卸载失败：" + ex.Message); }
                Render();
            };
            _list.Children.Add(Row(Icons.ForPlugin(p, 96), p.Manifest.Name, "v" + p.Manifest.Version, p.Manifest.Description, p.Manifest.Category, p.Enabled, run, toggle, del));
        }
    }

    void RenderStore()
    {
        if (_catalog == null) { _list.Children.Add(Empty("正在连接商店…")); return; }
        var installed = Store.List().ToDictionary(i => i.Manifest.Id, i => i.Manifest.Version);
        var q = _search.Text.Trim();
        var items = _catalog.Where(c => q.Length == 0 || (c.Name + c.Id + c.Summary).Contains(q, StringComparison.OrdinalIgnoreCase)).ToList();
        if (items.Count == 0) { _list.Children.Add(Empty("商店里暂时没有可安装的插件。")); return; }
        foreach (var c in items)
        {
            string label; bool can = true; bool primary = true;
            if (!installed.TryGetValue(c.Id, out var local)) label = "安装";
            else if (Store.IsNewer(c.Version, local)) label = "更新到 v" + c.Version;
            else { label = "已是最新"; can = false; primary = false; }
            var btn = MakeButton(label, primary); btn.IsEnabled = can;
            btn.Click += async (_, _) =>
            {
                btn.IsEnabled = false; Flash("正在下载「" + c.Name + "」…");
                try { var m = await Store.InstallFromCatalogAsync(c); Flash($"已安装「{m.Name}」v{m.Version}，现在就能用，无需重启。"); }
                catch (Exception ex) { Flash("安装失败：" + Store.Describe(ex)); }
                Render();
            };
            _list.Children.Add(Row(Icons.Generate(new Manifest { Name = c.Name, Category = c.Category, Glyph = c.Glyph, Color = c.Color }, 96), c.Name, "v" + c.Version, c.Summary, c.Category, true, btn));
        }
    }

    async Task LoadCatalogAsync()
    {
        Flash("正在读取商店…");
        try { _catalog = await Store.FetchCatalogAsync(); Flash("商店已更新，共 " + _catalog.Count + " 个可安装插件。"); }
        catch (Exception ex) { _catalog = new(); Flash("连接商店失败：" + Store.Describe(ex)); }
        if (_storeTab) Render();
    }

    // ---------- 操作 ----------
    void Run(Installed p) { RunTarget = p; Close(); }

    void InstallFromFile()
    {
        var dlg = new OpenFileDialog { Filter = "天河落插件包 (*.thlp)|*.thlp|所有文件|*.*", Title = "选择插件包" };
        if (dlg.ShowDialog(this) != true) return;
        try { var m = Store.InstallPackage(dlg.FileName); Flash($"已安装「{m.Name}」v{m.Version}，现在就能用，无需重启。"); }
        catch (Exception ex) { Flash("安装失败：" + ex.Message); }
        _storeTab = false; Render();
    }

    void Flash(string text) => _status.Text = text;

    // ---------- 控件工厂 ----------
    void StyleTab(Button b, string text)
    {
        b.Content = text; b.Background = Brushes.Transparent; b.BorderThickness = new Thickness(0);
        b.Padding = new Thickness(12, 6, 12, 6); b.FontSize = 14; b.Cursor = Cursors.Hand; b.Margin = new Thickness(0, 0, 4, 0);
        b.Template = FlatTemplate();
    }

    static Button MakeButton(string text, bool primary)
    {
        var b = new Button
        {
            Content = text, Padding = new Thickness(14, 6, 14, 6), Margin = new Thickness(6, 0, 0, 0), FontSize = 13, Cursor = Cursors.Hand,
            Foreground = primary ? Brushes.White : Fg, Background = primary ? Blue : B("#1C1C24"),
            BorderBrush = primary ? Blue : Line, BorderThickness = new Thickness(1),
        };
        b.Template = FlatTemplate(8);
        return b;
    }

    static ControlTemplate FlatTemplate(double radius = 6)
    {
        var f = new FrameworkElementFactory(typeof(Border));
        f.SetValue(Border.CornerRadiusProperty, new CornerRadius(radius));
        f.SetBinding(Border.BackgroundProperty, new System.Windows.Data.Binding("Background") { RelativeSource = System.Windows.Data.RelativeSource.TemplatedParent });
        f.SetBinding(Border.BorderBrushProperty, new System.Windows.Data.Binding("BorderBrush") { RelativeSource = System.Windows.Data.RelativeSource.TemplatedParent });
        f.SetBinding(Border.BorderThicknessProperty, new System.Windows.Data.Binding("BorderThickness") { RelativeSource = System.Windows.Data.RelativeSource.TemplatedParent });
        f.SetBinding(Border.PaddingProperty, new System.Windows.Data.Binding("Padding") { RelativeSource = System.Windows.Data.RelativeSource.TemplatedParent });
        var cp = new FrameworkElementFactory(typeof(ContentPresenter));
        cp.SetValue(FrameworkElement.HorizontalAlignmentProperty, HorizontalAlignment.Center);
        cp.SetValue(FrameworkElement.VerticalAlignmentProperty, VerticalAlignment.Center);
        f.AppendChild(cp);
        var t = new ControlTemplate(typeof(Button)) { VisualTree = f };
        var dis = new Trigger { Property = UIElement.IsEnabledProperty, Value = false };
        dis.Setters.Add(new Setter(UIElement.OpacityProperty, 0.45));
        t.Triggers.Add(dis);
        return t;
    }

    static UIElement Empty(string text) => new TextBlock
    {
        Text = text, Foreground = Mute, FontSize = 14, TextAlignment = TextAlignment.Center,
        Margin = new Thickness(0, 60, 0, 0), TextWrapping = TextWrapping.Wrap,
    };

    static UIElement Row(ImageSource icon, string name, string ver, string desc, string cat, bool enabled, params Button[] buttons)
    {
        var grid = new Grid();
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

        var left = new StackPanel { Opacity = enabled ? 1 : 0.5 };
        var head = new StackPanel { Orientation = Orientation.Horizontal };
        head.Children.Add(new TextBlock { Text = name, FontSize = 16, FontWeight = FontWeights.SemiBold, Foreground = Fg });
        head.Children.Add(new TextBlock { Text = ver, FontSize = 12, Foreground = Mute, Margin = new Thickness(8, 4, 0, 0) });
        if (!string.IsNullOrWhiteSpace(cat))
            head.Children.Add(new Border { Margin = new Thickness(8, 1, 0, 0), Padding = new Thickness(8, 1, 8, 1), CornerRadius = new CornerRadius(10), BorderBrush = Line, BorderThickness = new Thickness(1), Child = new TextBlock { Text = cat, FontSize = 11, Foreground = Mute } });
        if (!enabled) head.Children.Add(new TextBlock { Text = "已停用", FontSize = 11, Foreground = Warn, Margin = new Thickness(8, 3, 0, 0) });
        left.Children.Add(head);
        left.Children.Add(new TextBlock { Text = desc, Foreground = Mute, FontSize = 13, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 4, 12, 0) });
        var img = new System.Windows.Controls.Image { Source = icon, Width = 44, Height = 44, Margin = new Thickness(0, 0, 14, 0), VerticalAlignment = VerticalAlignment.Center, Opacity = enabled ? 1 : 0.45 };
        RenderOptions.SetBitmapScalingMode(img, BitmapScalingMode.HighQuality);
        Grid.SetColumn(img, 0); grid.Children.Add(img);
        Grid.SetColumn(left, 1); grid.Children.Add(left);

        var right = new StackPanel { Orientation = Orientation.Horizontal, VerticalAlignment = VerticalAlignment.Center };
        foreach (var b in buttons) right.Children.Add(b);
        Grid.SetColumn(right, 2); grid.Children.Add(right);

        return new Border { Background = Card, BorderBrush = Line, BorderThickness = new Thickness(1), CornerRadius = new CornerRadius(12), Padding = new Thickness(16, 14, 16, 14), Margin = new Thickness(0, 0, 0, 8), Child = grid };
    }
}
