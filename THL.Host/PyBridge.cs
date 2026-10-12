using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;
using UIApplication = Autodesk.Revit.UI.UIApplication;
using Microsoft.Win32;

namespace THL.Host;

/// <summary>供 Python 兼容层调用的原生对话框（深色主题，和插件中心风格一致）。</summary>
public static class PyBridge
{
    public static UIApplication? UIApp => PyRunner.UIApp;

    static Brush B(string hex) => (Brush)new BrushConverter().ConvertFromString(hex)!;
    static Window Base(string title, double w, double h)
    {
        var win = new Window
        {
            Title = title, Width = w, Height = h, WindowStartupLocation = WindowStartupLocation.CenterScreen,
            Background = B("#0B0B0E"), Foreground = B("#F2F4F7"), ShowInTaskbar = false, Topmost = true,
            FontFamily = new FontFamily("Microsoft YaHei UI, Segoe UI"), FontSize = 14, ResizeMode = ResizeMode.CanResizeWithGrip,
        };
        win.KeyDown += (_, e) => { if (e.Key == System.Windows.Input.Key.Escape) win.Close(); };
        return win;
    }

    static Button Btn(string text, bool primary, Action click)
    {
        var b = new Button
        {
            Content = text, Padding = new Thickness(18, 7, 18, 7), Margin = new Thickness(8, 0, 0, 0),
            Background = primary ? B("#3B9BFF") : B("#1C1C24"), Foreground = Brushes.White,
            BorderBrush = primary ? B("#3B9BFF") : B("#2A2A33"), MinWidth = 76,
        };
        b.Click += (_, _) => click();
        return b;
    }

    static StackPanel ButtonRow(params Button[] bs)
    {
        var sp = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 12, 0, 0) };
        foreach (var b in bs) sp.Children.Add(b);
        return sp;
    }

    // ---- 提示 / 确认 ----
    public static bool Alert(string msg, string title, bool yes, bool no, bool cancel)
    {
        bool result = !(yes || no || cancel); bool done = false;
        var win = Base(title, 520, 280);
        var root = new DockPanel { Margin = new Thickness(20) };
        var buttons = new List<Button>();
        if (yes || no || cancel)
        {
            if (yes) buttons.Add(Btn("是", true, () => { result = true; done = true; win.Close(); }));
            if (no) buttons.Add(Btn("否", false, () => { result = false; done = true; win.Close(); }));
            if (cancel) buttons.Add(Btn("取消", false, () => { result = false; done = true; win.Close(); }));
        }
        else buttons.Add(Btn("确定", true, () => { result = true; done = true; win.Close(); }));
        var row = ButtonRow(buttons.ToArray()); DockPanel.SetDock(row, Dock.Bottom); root.Children.Add(row);
        root.Children.Add(new ScrollViewer
        {
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Content = new TextBox { Text = msg, IsReadOnly = true, TextWrapping = TextWrapping.Wrap, Background = Brushes.Transparent, Foreground = B("#D8DBE3"), BorderThickness = new Thickness(0) },
        });
        win.Content = root; win.ShowDialog();
        return done && result;
    }

    // ---- 列表选择（返回被选项的索引；取消返回 null） ----
    public static int[]? Select(string[] labels, string title, string button, bool multi)
    {
        int[]? result = null;
        var win = Base(title, 640, 560);
        var root = new DockPanel { Margin = new Thickness(16) };
        var list = new ListBox
        {
            SelectionMode = multi ? SelectionMode.Extended : SelectionMode.Single, Background = B("#101014"), Foreground = B("#F2F4F7"),
            BorderBrush = B("#26262F"), Padding = new Thickness(4),
        };
        var filter = new TextBox { Margin = new Thickness(0, 0, 0, 8), Padding = new Thickness(8, 6, 8, 6), Background = B("#14141A"), Foreground = B("#F2F4F7"), BorderBrush = B("#26262F"), CaretBrush = B("#3B9BFF") };
        var shown = Enumerable.Range(0, labels.Length).ToList();
        void Fill()
        {
            var q = filter.Text.Trim();
            shown = Enumerable.Range(0, labels.Length).Where(i => q.Length == 0 || labels[i].Contains(q, StringComparison.OrdinalIgnoreCase)).ToList();
            list.Items.Clear();
            foreach (var i in shown) list.Items.Add(labels[i]);
            if (!multi && list.Items.Count > 0) list.SelectedIndex = 0;
        }
        filter.TextChanged += (_, _) => Fill();
        void Ok() { var sel = list.SelectedIndices(); if (sel.Count == 0) return; result = sel.Select(k => shown[k]).ToArray(); win.Close(); }
        list.MouseDoubleClick += (_, _) => { if (!multi) Ok(); };
        var row = ButtonRow(Btn("取消", false, () => win.Close()), Btn(button, true, Ok));
        DockPanel.SetDock(row, Dock.Bottom); root.Children.Add(row);
        DockPanel.SetDock(filter, Dock.Top); root.Children.Add(filter);
        root.Children.Add(list);
        win.Content = root; Fill();
        win.Loaded += (_, _) => filter.Focus();
        win.ShowDialog();
        return result;
    }

    static List<int> SelectedIndices(this ListBox l)
    {
        var r = new List<int>();
        foreach (var o in l.SelectedItems) r.Add(l.Items.IndexOf(o));
        return r;
    }

    // ---- 按钮式选择（pyRevit CommandSwitchWindow）；取消返回 -1 ----
    public static int Choose(string[] options, string message)
    {
        int result = -1;
        var win = Base("选择", 460, 200 + options.Length * 48);
        var sp = new StackPanel { Margin = new Thickness(20) };
        if (!string.IsNullOrWhiteSpace(message)) sp.Children.Add(new TextBlock { Text = message, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 14), Foreground = B("#D8DBE3") });
        for (int i = 0; i < options.Length; i++)
        {
            int k = i;
            var b = Btn(options[i], false, () => { result = k; win.Close(); });
            b.Margin = new Thickness(0, 0, 0, 8); b.HorizontalContentAlignment = HorizontalAlignment.Left; b.Padding = new Thickness(14, 10, 14, 10);
            sp.Children.Add(b);
        }
        win.Content = new ScrollViewer { Content = sp };
        win.ShowDialog();
        return result;
    }

    // ---- 文本输入；取消返回 null ----
    public static string? AskString(string prompt, string title, string def)
    {
        string? result = null;
        var win = Base(string.IsNullOrWhiteSpace(title) ? "输入" : title, 480, 260);
        var root = new DockPanel { Margin = new Thickness(20) };
        var box = new TextBox { Text = def, Padding = new Thickness(8, 6, 8, 6), Background = B("#14141A"), Foreground = B("#F2F4F7"), BorderBrush = B("#26262F"), CaretBrush = B("#3B9BFF") };
        void Ok() { result = box.Text; win.Close(); }
        box.KeyDown += (_, e) => { if (e.Key == System.Windows.Input.Key.Enter) Ok(); };
        var row = ButtonRow(Btn("取消", false, () => win.Close()), Btn("确定", true, Ok));
        DockPanel.SetDock(row, Dock.Bottom); root.Children.Add(row);
        DockPanel.SetDock(box, Dock.Bottom); root.Children.Add(box);
        root.Children.Add(new TextBlock { Text = prompt, TextWrapping = TextWrapping.Wrap, Foreground = B("#D8DBE3"), Margin = new Thickness(0, 0, 0, 12) });
        win.Content = root;
        win.Loaded += (_, _) => { box.Focus(); box.SelectAll(); };
        win.ShowDialog();
        return result;
    }

    /// <summary>选择文件；ext 例如 "csv"，为空则所有文件。取消返回 null。</summary>
    public static string? PickFile(string ext, string title)
    {
        var dlg = new OpenFileDialog { Title = string.IsNullOrWhiteSpace(title) ? "选择文件" : title };
        if (!string.IsNullOrWhiteSpace(ext)) dlg.Filter = $"{ext.ToUpper()} 文件 (*.{ext})|*.{ext}|所有文件|*.*";
        return dlg.ShowDialog() == true ? dlg.FileName : null;
    }

    public static string? SaveFile(string ext, string defaultName, string title)
    {
        var dlg = new SaveFileDialog { Title = string.IsNullOrWhiteSpace(title) ? "保存文件" : title, FileName = defaultName ?? "" };
        if (!string.IsNullOrWhiteSpace(ext)) { dlg.Filter = $"{ext.ToUpper()} 文件 (*.{ext})|*.{ext}|所有文件|*.*"; dlg.DefaultExt = "." + ext; }
        return dlg.ShowDialog() == true ? dlg.FileName : null;
    }

    /// <summary>每个文档一份的数据文件路径（存在用户数据目录下）。</summary>
    public static string DataFile(string docKey, string name, string ext)
    {
        var dir = Path.Combine(Store.Root, "data"); Directory.CreateDirectory(dir);
        var safe = string.Concat((docKey ?? "doc").Select(c => char.IsLetterOrDigit(c) || c is '-' or '_' ? c : '_'));
        return Path.Combine(dir, $"{safe}_{name}.{ext}");
    }

    public static string? PickFolder(string title)
    {
        var dlg = new OpenFolderDialog { Title = title };
        return dlg.ShowDialog() == true ? dlg.FolderName : null;
    }

    // ---- 进度条（非模态；update 时处理界面消息，让窗口能刷新、能点取消） ----
    public sealed class Progress
    {
        readonly Window _win; readonly ProgressBar _bar; readonly TextBlock _txt; readonly string _title;
        public bool Cancelled { get; private set; }
        internal Progress(string title, bool cancellable)
        {
            _title = title;
            _win = Base("天河落", 520, 170); _win.Topmost = true;
            _bar = new ProgressBar { Height = 10, Minimum = 0, Maximum = 100, Foreground = B("#3B9BFF"), Background = B("#1B1B22"), BorderThickness = new Thickness(0) };
            _txt = new TextBlock { Text = title.Replace("{value}", "0").Replace("{max_value}", "?"), Margin = new Thickness(0, 0, 0, 12), TextWrapping = TextWrapping.Wrap };
            var sp = new StackPanel { Margin = new Thickness(20) };
            sp.Children.Add(_txt); sp.Children.Add(_bar);
            if (cancellable) sp.Children.Add(ButtonRow(Btn("取消", false, () => { Cancelled = true; })));
            _win.Content = sp; _win.Show(); Pump();
        }
        public void Update(int value, int max)
        {
            _bar.Maximum = Math.Max(1, max); _bar.Value = Math.Min(value, max);
            _txt.Text = _title.Replace("{value}", value.ToString()).Replace("{max_value}", max.ToString());
            Pump();
        }
        public void Close() { try { _win.Close(); } catch { } }
        static void Pump()
        {
            var frame = new DispatcherFrame();
            Dispatcher.CurrentDispatcher.BeginInvoke(DispatcherPriority.Background, new Action(() => frame.Continue = false));
            Dispatcher.PushFrame(frame);
        }
    }
    public static Progress OpenProgress(string title, bool cancellable) => new(title, cancellable);
}
