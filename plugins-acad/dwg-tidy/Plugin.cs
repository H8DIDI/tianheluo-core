// 天河落 · 出图美化（dwg-tidy）：Revit 导出的 DWG 一键规范成能直接套图框出图的样子。
// 平时弹窗口；参数（THLRUN dwg-tidy?k=v;...）给批处理 / 导出后处理链 / 自动测试：
//   current=1                 处理当前图（界面里原地改，可 UNDO；无界面时另存）
//   files=a.dwg|b.dwg         后台批量处理（不在界面打开），另存「原名_出图.dwg」
//   outdir=<目录>             另存位置（默认原图同文件夹）
//   diag=<报告.txt>           只体检，不改图
//   fonts= linetypes= duplicates= darkenpale= bylayer= maskmtext= purge= audit=（0/1，覆盖规则表）
using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Forms;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;

namespace Thl.Acad.Tidy
{
    public class Plugin
    {
        static readonly string LogFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo", "AutoCAD", "logs", "dwg-tidy.log");
        static void Log(string s) { try { Directory.CreateDirectory(Path.GetDirectoryName(LogFile)); File.AppendAllText(LogFile, $"{DateTime.Now:MM-dd HH:mm:ss} {s}\n"); } catch { } }

        public string Execute(Document doc, IDictionary<string, string> args)
        {
            try
            {
                if (args != null && args.TryGetValue("diag", out var rep))
                {
                    if (doc == null) return "出图美化：请先打开图纸。";
                    File.WriteAllText(rep, Diagnose.Run(doc.Database), new UTF8Encoding(true));
                    return "体检报告：" + rep;
                }
                var o = TidyOptions.Load(Log);
                bool current; var files = new List<string>(); string outdir;
                if (args != null && (args.ContainsKey("current") || args.ContainsKey("files")))
                {
                    o.Override(args);
                    current = Args.B(args, "current", false);
                    files.AddRange(Args.S(args, "files").Split('|').Select(s => s.Trim()).Where(s => s.Length > 0));
                    outdir = Args.S(args, "outdir", "");
                }
                else
                {
                    if (Ui.IsConsole) return "出图美化：无界面运行时需要参数（current=1 或 files=...）。";
                    if (!Dialog(doc, o, out current, files, out outdir)) return "出图美化：已取消。";
                }
                var sb = new StringBuilder();
                if (current && doc != null)
                {
                    using (doc.LockDocument())
                    {
                        var r = Tidy.Run(doc.Database, o, Log);
                        sb.AppendLine($"{Path.GetFileName(doc.Name)}：\n{r}");
                        if (Ui.IsConsole || outdir.Length > 0)
                        {
                            var dst = OutPath(doc.Database.Filename, outdir);
                            doc.Database.SaveAs(dst, DwgVersion.Current);
                            sb.AppendLine("另存：" + dst);
                        }
                    }
                    try { doc.Editor.Regen(); } catch { }
                }
                foreach (var f in files)
                {
                    try
                    {
                        using (var side = new SideDb(f, true))
                        {
                            var r = Tidy.Run(side.Db, o, Log);
                            var dst = OutPath(f, outdir);
                            side.Db.SaveAs(dst, DwgVersion.Current);
                            sb.AppendLine($"{Path.GetFileName(f)} → {dst}\n{r}");
                        }
                    }
                    catch (Exception ex) { sb.AppendLine($"{Path.GetFileName(f)} 出错：{ex.Message}"); }
                }
                var res = "出图美化完成：\n" + sb.ToString().TrimEnd() + "\n规则表：" + TidyOptions.File;
                Log(res);
                return res;
            }
            catch (Exception ex) { Log("出错 " + ex); return "出图美化出错：" + ex.Message; }
        }

        static string OutPath(string src, string outdir)
        {
            var dir = string.IsNullOrEmpty(outdir) ? Path.GetDirectoryName(src) : outdir;
            if (string.IsNullOrEmpty(dir)) dir = Environment.GetFolderPath(Environment.SpecialFolder.Desktop);
            Directory.CreateDirectory(dir);
            return Path.Combine(dir, Path.GetFileNameWithoutExtension(src) + "_出图.dwg");
        }

        static bool Dialog(Document doc, TidyOptions o, out bool current, List<string> files, out string outdir)
        {
            var f = Ui.NewForm("天河落 · 出图美化", 640, 470);
            var tt = new ToolTip();
            var rCur = new RadioButton { Text = "当前图纸（原地修改，可 UNDO）" + (doc != null ? "：" + Path.GetFileName(doc.Name) : ""), Left = 16, Top = 14, AutoSize = true, Checked = doc != null, Enabled = doc != null };
            var rBatch = new RadioButton { Text = "批量：选几个 DWG，后台处理，另存「原名_出图.dwg」", Left = 16, Top = 40, AutoSize = true, Checked = doc == null };
            f.Controls.Add(rCur); f.Controls.Add(rBatch);
            var add = Ui.Btn(f, "选择文件…", 420, 36, 110);
            var lbl = Ui.Label(f, "", 36, 68, 580, SystemColors.GrayText);
            add.Click += (_, __) =>
            {
                using (var d = new OpenFileDialog { Filter = "DWG|*.dwg", Multiselect = true })
                    if (d.ShowDialog(f) == DialogResult.OK) { files.Clear(); files.AddRange(d.FileNames); lbl.Text = $"{files.Count} 个文件"; rBatch.Checked = true; }
            };
            int y = 104;
            CheckBox C(string text, bool v, string tip) { var c = Ui.Check(f, text, 16, y, v, tip, tt); y += 28; return c; }
            var cFont = C("修字体：缺字体的文字样式换成 gbenor + gbcbig（解决「?」和乱码）", o.Fonts, "只改找不到字体文件的样式；TrueType 字体不动。");
            var cLt = C("线型：虚线 / 点划线周期按纸面长度重排（Revit 导出的图）", o.Linetypes, "LTSCALE 不是 1 的图（设计院手画）不动，保留原设计。");
            var cDup = C("删重叠的重复直线、零长度线", o.Duplicates, null);
            var cPale = C("浅色加深：屏幕亮、白纸上看不清的图层颜色加深（色相不变）", o.DarkenPale, null);
            var cRule = Ui.Label(f, "图层规则表：场地、地形、CAD 底图淡显变细（规则表里可加：主管线加粗、某层不打印……）", 34, y, 590, SystemColors.GrayText); y += 30;
            var cBy = C("对象颜色 / 线型 / 线宽统一随层（有意的单独颜色会丢，慎用）", o.ByLayer, null);
            var cMask = C("多行文字加背景遮罩（字压在管线上更清楚）", o.MaskMText, null);
            var cPurge = C("清理没用到的块、图层、样式（PURGE）+ 审核（AUDIT）", o.Purge, null);
            var rules = Ui.Btn(f, "打开规则表", 16, 424, 110);
            rules.Click += (_, __) => { try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(TidyOptions.File) { UseShellExecute = true }); } catch { } };
            var ok = Ui.Btn(f, "开始", 430, 424, 96); ok.DialogResult = DialogResult.OK; f.AcceptButton = ok;
            var cancel = Ui.Btn(f, "取消", 532, 424, 92); cancel.DialogResult = DialogResult.Cancel;
            var res = Ui.Show(f, "dwg-tidy");
            current = rCur.Checked; outdir = "";
            o.Fonts = cFont.Checked; o.Linetypes = cLt.Checked; o.Duplicates = cDup.Checked; o.DarkenPale = cPale.Checked; o.ByLayer = cBy.Checked; o.MaskMText = cMask.Checked; o.Purge = o.Audit = cPurge.Checked;
            return res == DialogResult.OK && (current || files.Count > 0);
        }
    }
}
