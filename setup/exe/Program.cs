using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Windows.Forms;

namespace THLSetup
{
    /// <summary>
    /// 天河落宿主安装程序。安装包里（嵌入资源 payload.zip）：
    ///   builds/net472/...  Revit 2020        builds/net8/...  Revit 2026.0 ~ 2026.4        builds/net10/...  Revit 2026.5+
    ///   lib/...            三套共用的 IronPython 标准库（只存一份，装的时候复制进每个 THL\lib）
    /// 只写当前用户的 %AppData%\Autodesk\Revit\Addins\&lt;年份&gt;，不需要管理员权限。
    /// </summary>
    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            // 命令行静默安装（测试 / 批量部署用）：THL-Setup.exe /S [/years=2020,2026] [/appdata=路径] [/minor=5]
            if (args.Any(a => a.Equals("/S", StringComparison.OrdinalIgnoreCase)))
            {
                Environment.Exit(Silent(args));
                return;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm());
        }

        private static string Arg(string[] args, string key)
        {
            var a = args.FirstOrDefault(x => x.StartsWith("/" + key + "=", StringComparison.OrdinalIgnoreCase));
            return a == null ? null : a.Substring(key.Length + 2);
        }

        private static int Silent(string[] args)
        {
            try
            {
                var appData = Arg(args, "appdata") ?? Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
                var years = (Arg(args, "years") ?? "").Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries).Select(s => s.Trim()).ToList();
                var found = Installer.Detect();
                if (years.Count == 0) years = found.Select(f => f.Year).ToList();
                if (args.Any(a => a.Equals("/uninstall", StringComparison.OrdinalIgnoreCase)))
                {
                    foreach (var y in new[] { "2020", "2026" }) Installer.Uninstall(appData, y);
                    return 0;
                }
                foreach (var y in years)
                {
                    var t = found.FirstOrDefault(f => f.Year == y) ?? new RevitTarget { Year = y };
                    var minor = Arg(args, "minor");
                    if (minor != null) t.Minor = int.Parse(minor);
                    Installer.Install(appData, t, _ => { });
                }
                return 0;
            }
            catch (Exception ex)
            {
                Installer.Log("静默安装失败：" + ex);
                return 1;
            }
        }
    }

    internal sealed class RevitTarget
    {
        public string Year;
        public string ExePath;
        public int Minor = -1;            // 2026 的小版本（FileVersion 第二段），决定 net8 / net10
        public string Build => Year == "2020" ? "net472" : (Minor >= 5 || Minor < 0 ? "net10" : "net8");
        public string Label => Year == "2020" ? "Revit 2020" : (Minor >= 0 ? $"Revit 2026（更新 {Minor}）" : "Revit 2026");
    }

    internal static class Installer
    {
        public static readonly string LogPath = Path.Combine(Path.GetTempPath(), "thl-install.log");

        public static void Log(string m)
        {
            try { File.AppendAllText(LogPath, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {m}\r\n"); } catch { }
        }

        /// <summary>在常见安装位置找 Revit 2020 / 2026。</summary>
        public static List<RevitTarget> Detect()
        {
            var res = new List<RevitTarget>();
            var pf = new[] { Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), @"C:\Program Files", @"D:\Program Files", @"E:\Program Files" };
            foreach (var year in new[] { "2026", "2020" })
            {
                foreach (var root in pf.Distinct())
                {
                    var exe = Path.Combine(root, "Autodesk", "Revit " + year, "Revit.exe");
                    if (!File.Exists(exe)) continue;
                    var t = new RevitTarget { Year = year, ExePath = exe };
                    if (year == "2026")
                    {
                        try { t.Minor = int.Parse(FileVersionInfo.GetVersionInfo(exe).FileVersion.Split('.')[1]); } catch { }
                    }
                    res.Add(t);
                    break;
                }
            }
            return res;
        }

        public static bool RevitRunning() => Process.GetProcessesByName("Revit").Length > 0;

        private static ZipArchive OpenPayload()
        {
            var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip")
                    ?? throw new InvalidOperationException("安装程序不完整（缺少内置安装包），请重新下载。");
            return new ZipArchive(s, ZipArchiveMode.Read);
        }

        public static void Install(string appData, RevitTarget t, Action<string> progress)
        {
            var dest = Path.Combine(appData, "Autodesk", "Revit", "Addins", t.Year);
            var thl = Path.Combine(dest, "THL");
            Log($"安装 {t.Label}（{t.Build}）→ {dest}");
            Directory.CreateDirectory(dest);
            if (Directory.Exists(thl)) Directory.Delete(thl, true);
            Directory.CreateDirectory(thl);
            using (var zip = OpenPayload())
            {
                var pre = $"builds/{t.Build}/";
                var entries = zip.Entries.Where(e => !string.IsNullOrEmpty(e.Name) && (e.FullName.StartsWith(pre) || e.FullName.StartsWith("lib/"))).ToList();
                if (!entries.Any(e => e.FullName == pre + "THL/THL.Host.dll")) throw new InvalidOperationException("安装程序里没有 " + t.Build + " 版本");
                int i = 0;
                foreach (var e in entries)
                {
                    string rel = e.FullName.StartsWith("lib/") ? "THL/" + e.FullName : e.FullName.Substring(pre.Length);
                    var path = Path.GetFullPath(Path.Combine(dest, rel.Replace('/', Path.DirectorySeparatorChar)));
                    if (!path.StartsWith(Path.GetFullPath(dest), StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("安装包含非法路径");
                    Directory.CreateDirectory(Path.GetDirectoryName(path));
                    e.ExtractToFile(path, true);
                    if (++i % 200 == 0) progress($"{t.Label}：已复制 {i} / {entries.Count} 个文件…");
                }
            }
            foreach (var f in new[] { @"THL\THL.Host.dll", @"THL\IronPython.dll", @"THL\lib\os.py", "THL.addin" })
                if (!File.Exists(Path.Combine(dest, f))) throw new InvalidOperationException($"安装后自检失败（{t.Label}）：缺少 {f}");
            Log($"{t.Label} 安装完成");
        }

        public static void Uninstall(string appData, string year)
        {
            var dest = Path.Combine(appData, "Autodesk", "Revit", "Addins", year);
            var a = Path.Combine(dest, "THL.addin");
            var d = Path.Combine(dest, "THL");
            if (File.Exists(a)) File.Delete(a);
            if (Directory.Exists(d)) Directory.Delete(d, true);
            Log($"已卸载 Revit {year} 的宿主");
        }
    }

    internal sealed class MainForm : Form
    {
        private readonly List<RevitTarget> _found = Installer.Detect();
        private readonly List<CheckBox> _checks = new List<CheckBox>();
        private readonly Label _status = new Label();
        private readonly Button _install = new Button(), _uninstall = new Button(), _close = new Button();
        private readonly string _appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);

        public MainForm()
        {
            Text = "天河落 THL 安装程序";
            Font = new Font("Microsoft YaHei UI", 9.5f);
            AutoScaleMode = AutoScaleMode.Dpi;
            AutoScaleDimensions = new SizeF(96f, 96f);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(520, 370);
            BackColor = Color.White;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }

            var head = new Panel { Dock = DockStyle.Top, Height = 84, BackColor = Color.FromArgb(11, 79, 196) };
            head.Controls.Add(new Label { Text = "天河落 THL", ForeColor = Color.White, Font = new Font("Microsoft YaHei UI", 16f, FontStyle.Bold), Location = new Point(22, 6), AutoSize = true });
            head.Controls.Add(new Label { Text = "Revit 插件宿主 · 支持 Revit 2020 / 2026 · tianheluo.com", ForeColor = Color.FromArgb(220, 234, 255), Font = new Font("Microsoft YaHei UI", 9f), Location = new Point(24, 54), AutoSize = true });
            Controls.Add(head);

            var y = 100;
            Controls.Add(new Label { Text = "选择要安装到哪个 Revit（已自动检测）：", Location = new Point(24, y), AutoSize = true });
            y += 30;
            var all = new List<RevitTarget>(_found);
            foreach (var yr in new[] { "2026", "2020" })
                if (!all.Any(t => t.Year == yr)) all.Add(new RevitTarget { Year = yr });
            foreach (var t in all.OrderByDescending(t => t.Year))
            {
                bool det = _found.Contains(t);
                var cb = new CheckBox
                {
                    Text = t.Label + (det ? "    ✓ 已检测到" : "    （没检测到，确定装了可以手动勾选）"),
                    Checked = det, Tag = t, Location = new Point(40, y), AutoSize = true,
                };
                _checks.Add(cb);
                Controls.Add(cb);
                y += 30;
            }
            if (!_found.Any(t => t.Year == "2026"))
            {
                var cb26 = _checks.First(c => ((RevitTarget)c.Tag).Year == "2026");
                cb26.Text = "Revit 2026（没检测到，手动勾选时按 2026.5+ 安装）";
            }
            y += 6;
            _status.Location = new Point(24, y);
            _status.Size = new Size(472, 70);
            _status.ForeColor = Color.FromArgb(90, 90, 90);
            Controls.Add(_status);

            _install.Text = "安装";
            _install.Size = new Size(120, 36);
            _install.Location = new Point(250, 314);
            _install.BackColor = Color.FromArgb(22, 104, 220);
            _install.ForeColor = Color.White;
            _install.FlatStyle = FlatStyle.Flat;
            _install.Click += (s, e) => DoInstall();
            _uninstall.Text = "卸载";
            _uninstall.Size = new Size(90, 36);
            _uninstall.Location = new Point(24, 314);
            _uninstall.Click += (s, e) => DoUninstall();
            _close.Text = "关闭";
            _close.Size = new Size(110, 36);
            _close.Location = new Point(386, 314);
            _close.Click += (s, e) => Close();
            Controls.AddRange(new Control[] { _install, _uninstall, _close });
            AcceptButton = _install;
            Shown += (s, e) => RefreshState();
        }

        private void RefreshState()
        {
            if (Installer.RevitRunning())
                SetStatus("检测到 Revit 正在运行。请先保存并关闭 Revit，再点「安装」。", Color.FromArgb(200, 110, 0));
            else
                SetStatus("安装位置：%AppData%\\Autodesk\\Revit\\Addins\\<年份>（只装给当前用户，不需要管理员权限）。", Color.FromArgb(90, 90, 90));
        }

        private void SetStatus(string text, Color c)
        {
            _status.Text = text;
            _status.ForeColor = c;
            _status.Refresh();
        }

        private void Busy(bool b)
        {
            _install.Enabled = _uninstall.Enabled = _close.Enabled = !b;
            foreach (var c in _checks) c.Enabled = !b;
            Cursor = b ? Cursors.WaitCursor : Cursors.Default;
        }

        private void DoInstall()
        {
            var targets = _checks.Where(c => c.Checked).Select(c => (RevitTarget)c.Tag).ToList();
            if (targets.Count == 0) { SetStatus("请至少勾选一个 Revit 版本。", Color.FromArgb(200, 110, 0)); return; }
            if (Installer.RevitRunning()) { RefreshState(); return; }
            Busy(true);
            try
            {
                Installer.Log("==== 开始安装（exe）");
                foreach (var t in targets)
                {
                    SetStatus($"正在安装到 {t.Label}…", Color.FromArgb(22, 104, 220));
                    Installer.Install(_appData, t, m => SetStatus(m, Color.FromArgb(22, 104, 220)));
                }
                SetStatus("安装完成！打开 Revit，顶部会出现「天河落」选项卡。\n第一次打开时 Revit 会弹出加载项安全提示，请选「始终加载」。", Color.FromArgb(16, 140, 70));
                _install.Text = "重新安装";
            }
            catch (Exception ex)
            {
                Installer.Log("安装失败：" + ex);
                SetStatus("安装失败：" + ex.Message + "\n详细记录：" + Installer.LogPath, Color.FromArgb(200, 40, 40));
            }
            finally { Busy(false); }
        }

        private void DoUninstall()
        {
            if (Installer.RevitRunning()) { RefreshState(); return; }
            var years = _checks.Where(c => c.Checked).Select(c => ((RevitTarget)c.Tag).Year).ToList();
            if (years.Count == 0) years = new List<string> { "2020", "2026" };
            if (MessageBox.Show(this, "将卸载 " + string.Join(" / ", years.Select(y => "Revit " + y)) + " 的天河落宿主。\n已安装的插件保留在 %AppData%\\Tianheluo。\n\n确定卸载？", Text, MessageBoxButtons.OKCancel, MessageBoxIcon.Question) != DialogResult.OK) return;
            Busy(true);
            try
            {
                foreach (var y in years) Installer.Uninstall(_appData, y);
                SetStatus("已卸载。", Color.FromArgb(16, 140, 70));
            }
            catch (Exception ex) { SetStatus("卸载失败：" + ex.Message, Color.FromArgb(200, 40, 40)); }
            finally { Busy(false); }
        }
    }
}
