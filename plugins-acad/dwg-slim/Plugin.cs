// 天河落 · DWG 瘦身（AutoCAD 插件）
// 点按钮 → 选「当前图」或「批量选文件」→ 调参数 → 另存为「原名_瘦身.dwg」。原图一律不改：
//   当前图：图纸已保存且没改动时，后台重新读一份文件来处理；有未保存的改动时，在内存里复制一份（Wblock）来处理。
//   批量：每个文件在后台单独读一份处理（不在界面里打开）。
// 算法在 ..\..\dwgslim\Slim.cs（和 Revit 导出后台调用的 THLSLIM 同一份）。
using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Forms;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using AcApp = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace Thl.Acad.DwgSlim
{
    public class Plugin
    {
        static string SettingsPath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo", "AutoCAD", "settings", "dwg-slim.txt");
        static string LogPath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo", "AutoCAD", "logs", "dwg-slim.log");

        sealed class Opt
        {
            public bool NoCad = true, Wells = true, Join = true, Dedupe = true;
            public double MinSize = 50, Tol = 0.5;
            public string OutDir = "";
            public Dictionary<string, string> ToSlim() => new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                ["nocad"] = NoCad ? "1" : "0", ["wells"] = Wells ? "1" : "0", ["join"] = Join ? "1" : "0", ["dedupe"] = Dedupe ? "1" : "0",
                ["minsize"] = Args.F(MinSize), ["tol"] = Args.F(Tol),
            };
            public void Read(IDictionary<string, string> a)
            {
                NoCad = Args.B(a, "nocad", NoCad); Wells = Args.B(a, "wells", Wells); Join = Args.B(a, "join", Join); Dedupe = Args.B(a, "dedupe", Dedupe);
                MinSize = Args.D(a, "minsize", MinSize); Tol = Args.D(a, "tol", Tol); OutDir = Args.S(a, "outdir", OutDir);
            }
            public static Opt Load()
            {
                var o = new Opt();
                try
                {
                    if (File.Exists(SettingsPath))
                    {
                        var d = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                        foreach (var line in File.ReadAllLines(SettingsPath, Encoding.UTF8)) { var k = line.IndexOf('='); if (k > 0) d[line.Substring(0, k)] = line.Substring(k + 1); }
                        o.Read(d);
                    }
                }
                catch { }
                return o;
            }
            public void Save()
            {
                try
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(SettingsPath));
                    var d = ToSlim(); d["outdir"] = OutDir;
                    File.WriteAllLines(SettingsPath, d.Select(kv => kv.Key + "=" + kv.Value), Encoding.UTF8);
                }
                catch { }
            }
        }

        /// <summary>args（测试/批处理）：current=1 或 files=a.dwg|b.dwg；nocad wells join dedupe minsize tol outdir。有 args 就不弹对话框。</summary>
        public string Execute(Document doc, IDictionary<string, string> args)
        {
            var opt = Opt.Load();
            bool useCurrent; var files = new List<string>();
            bool auto = args != null && (args.ContainsKey("current") || args.ContainsKey("files"));
            if (!auto && Ui.IsConsole) return "DWG 瘦身：无界面运行时需要参数（current=1 或 files=...）。";
            if (auto)
            {
                opt.Read(args);
                useCurrent = Args.B(args, "current", false);
                files.AddRange(Args.S(args, "files").Split('|').Select(s => s.Trim()).Where(s => s.Length > 0));
            }
            else
            {
                if (!Dialog(doc, opt, out useCurrent, files)) return "DWG 瘦身：已取消。";
                opt.Save();
            }
            if (useCurrent && doc == null) return "DWG 瘦身：没有打开的图纸。";

            var ed = doc?.Editor;
            var log = new StringBuilder();
            void Say(string m) { log.AppendLine(m); try { ed?.WriteMessage("\n  " + m); } catch { } AppendLog(m); }
            var results = new List<string>();
            var jobs = new List<string>();
            if (useCurrent) jobs.Add(null);
            jobs.AddRange(files);
            int ok = 0;
            foreach (var src in jobs)
            {
                var t0 = DateTime.Now;
                string name = src ?? doc.Name;
                try
                {
                    string outPath;
                    long before;
                    if (src == null)
                    {
                        var db = doc.Database;
                        string file = db.Filename;
                        bool saved = File.Exists(file) && file.EndsWith(".dwg", StringComparison.OrdinalIgnoreCase)
                                     && Convert.ToInt32(AcApp.GetSystemVariable("DBMOD")) == 0;
                        outPath = OutPathFor(File.Exists(file) ? file : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), Path.GetFileNameWithoutExtension(doc.Name) + ".dwg"), opt.OutDir);
                        before = File.Exists(file) ? new FileInfo(file).Length : 0;
                        Say($"瘦身当前图：{Path.GetFileName(name)}" + (saved ? "（从已保存的文件读一份副本处理）" : "（有未保存的改动：复制一份内存副本处理）"));
                        if (saved)
                            using (var side = new SideDb(file, true)) Thl.DwgSlim.Slim.Process(side.Db, opt.ToSlim(), outPath, Say);
                        else
                            using (var copy = db.Wblock())
                            using (new WorkingDbScope(copy)) Thl.DwgSlim.Slim.Process(copy, opt.ToSlim(), outPath, Say);
                    }
                    else
                    {
                        if (!File.Exists(src)) { Say("找不到：" + src); continue; }
                        outPath = OutPathFor(src, opt.OutDir);
                        before = new FileInfo(src).Length;
                        Say($"瘦身：{src}");
                        using (var side = new SideDb(src, true)) Thl.DwgSlim.Slim.Process(side.Db, opt.ToSlim(), outPath, Say);
                    }
                    var after = new FileInfo(outPath).Length;
                    var line = $"{Path.GetFileName(name)}：{Mb(before)} → {Mb(after)}，用时 {(int)(DateTime.Now - t0).TotalSeconds} 秒 → {outPath}";
                    results.Add(line); ok++;
                    Say("完成 " + line);
                }
                catch (System.Exception ex)
                {
                    results.Add($"{Path.GetFileName(name)}：失败（{ex.Message}）");
                    Say("失败：" + ex);
                }
            }
            var summary = $"DWG 瘦身完成 {ok}/{jobs.Count}：\n" + string.Join("\n", results);
            if (!auto) MessageBox.Show(summary + "\n\n原图没有改动。日志：" + LogPath, "天河落 · DWG 瘦身");
            return summary;
        }

        static string Mb(long b) => b <= 0 ? "?" : (b / 1048576.0).ToString("0.0", Args.IC) + " MB";

        static string OutPathFor(string src, string outDir)
        {
            var dir = string.IsNullOrWhiteSpace(outDir) ? Path.GetDirectoryName(src) : outDir;
            Directory.CreateDirectory(dir);
            var p = Path.Combine(dir, Path.GetFileNameWithoutExtension(src) + "_瘦身.dwg");
            if (string.Equals(Path.GetFullPath(p), Path.GetFullPath(src), StringComparison.OrdinalIgnoreCase)) throw new IOException("输出文件和原文件相同");
            return p;
        }

        static void AppendLog(string m)
        {
            try { Directory.CreateDirectory(Path.GetDirectoryName(LogPath)); File.AppendAllText(LogPath, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + m + "\n", Encoding.UTF8); } catch { }
        }

        // ---------------------------------------------------------------- 对话框
        static bool Dialog(Document doc, Opt opt, out bool useCurrent, List<string> files)
        {
            var f = Ui.NewForm("天河落 · DWG 瘦身", 740, 600);
            var tt = new ToolTip();
            var g1 = new GroupBox { Text = "处理哪些图", Left = 12, Top = 10, Width = 716, Height = 214 };
            f.Controls.Add(g1);
            var rCur = new RadioButton { Text = "当前图纸" + (doc != null ? "：" + Path.GetFileName(doc.Name) : "（没有打开的图纸）"), Left = 14, Top = 24, AutoSize = true, Enabled = doc != null, Checked = doc != null };
            var rBatch = new RadioButton { Text = "批量：选择多个 DWG 文件（后台处理，不在界面里打开）", Left = 14, Top = 50, AutoSize = true, Checked = doc == null };
            g1.Controls.Add(rCur); g1.Controls.Add(rBatch);
            var list = new ListBox { Left = 32, Top = 80, Width = 560, Height = 120, HorizontalScrollbar = true, SelectionMode = SelectionMode.MultiExtended };
            g1.Controls.Add(list);
            var add = Ui.Btn(g1, "添加…", 604, 80, 96); var del = Ui.Btn(g1, "移除", 604, 116, 96);
            add.Click += (_, __) =>
            {
                using (var d = new OpenFileDialog { Filter = "DWG 文件 (*.dwg)|*.dwg", Multiselect = true, Title = "选择要瘦身的 DWG" })
                    if (d.ShowDialog(f) == DialogResult.OK) foreach (var p in d.FileNames) if (!list.Items.Contains(p)) list.Items.Add(p);
                rBatch.Checked = list.Items.Count > 0 || rBatch.Checked;
            };
            del.Click += (_, __) => { foreach (var it in list.SelectedItems.Cast<object>().ToList()) list.Items.Remove(it); };

            var g2 = new GroupBox { Text = "瘦身选项", Left = 12, Top = 232, Width = 716, Height = 238 };
            f.Controls.Add(g2);
            var cNoCad = Ui.Check(g2, "去掉导入的 CAD 底图（Revit 导出时带进来的建筑底图块）", 14, 26, opt.NoCad, "块名形如「建筑底图-xxx_dwg-<ID>-<视图>」。需要底图时可用「套图」按原位置套回。", tt);
            var cWells = Ui.Check(g2, "井只留三圈线：外壁、内壁、井盖外框（去掉浮雕字、方格、爬梯等）", 14, 56, opt.Wells, "只处理块名像井的块（井 / MANHOLE / HANDHOLE / EM1- / _96WAY…），排管不动。", tt);
            var cJoin = Ui.Check(g2, "首尾相连的短直线连成多段线，抽稀容差", 14, 86, opt.Join, "Revit 把曲线炸成上万条短直线；偏差不超过容差的中间点去掉（0.5mm 肉眼不可见）。", tt);
            var tTol = Ui.Num(g2, opt.Tol, 400, 88, 60); Ui.Label(g2, "（图形单位）", 466, 88);
            var cDedupe = Ui.Check(g2, "几何相同的块合并成一份（同型号的井只存一次，颜色图层保持不变）", 14, 116, opt.Dedupe, null, tt);
            Ui.Label(g2, "碎图形阈值：外包对角线小于", 32, 152);
            var tMin = Ui.Num(g2, opt.MinSize, 250, 152, 60);
            Ui.Label(g2, "的线、圆弧去掉（0 = 不去）", 318, 152);
            Ui.Label(g2, "图形单位一般是 mm。建议 = 打印 0.1mm × 出图比例，1:500 填 50。文字、块、填充、标注不动。", 32, 184, 670, SystemColors.GrayText).Height = 44;

            Ui.Label(f, "输出：", 14, 486);
            var tOut = new TextBox { Left = 64, Top = 483, Width = 560, Text = opt.OutDir };
            f.Controls.Add(tOut);
            var bOut = Ui.Btn(f, "浏览…", 632, 481, 96);
            bOut.Click += (_, __) => { using (var d = new FolderBrowserDialog { Description = "输出文件夹（留空 = 和原图同一文件夹）" }) if (d.ShowDialog(f) == DialogResult.OK) tOut.Text = d.SelectedPath; };
            Ui.Label(f, "留空 = 和原图同一文件夹；文件名加「_瘦身」。原图不改。", 64, 514, 660, SystemColors.GrayText);

            var go = Ui.Btn(f, "开始瘦身", 524, 556, 100); var cancel = Ui.Btn(f, "取消", 632, 556, 96);
            go.DialogResult = DialogResult.OK; cancel.DialogResult = DialogResult.Cancel;
            f.AcceptButton = go; f.CancelButton = cancel;
            go.Click += (_, __) =>
            {
                if (rBatch.Checked && list.Items.Count == 0) { MessageBox.Show(f, "请先「添加…」要瘦身的 DWG。", "DWG 瘦身"); f.DialogResult = DialogResult.None; }
            };
            var r = Ui.Show(f, "dwg-slim");
            useCurrent = rCur.Checked;
            if (r != DialogResult.OK) return false;
            opt.NoCad = cNoCad.Checked; opt.Wells = cWells.Checked; opt.Join = cJoin.Checked; opt.Dedupe = cDedupe.Checked;
            opt.Tol = Ui.Val(tTol, opt.Tol); opt.MinSize = Ui.Val(tMin, opt.MinSize); opt.OutDir = tOut.Text.Trim();
            if (rBatch.Checked) files.AddRange(list.Items.Cast<string>());
            return true;
        }
    }
}
