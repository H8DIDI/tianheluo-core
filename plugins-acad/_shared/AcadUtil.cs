// 天河落 AutoCAD 插件共用的小工具：侧数据库、参数读取、窗体（按 DPI 放大）、测试截图。
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Windows.Forms;
using Autodesk.AutoCAD.DatabaseServices;

namespace Thl.Acad
{
    static class Args
    {
        public static readonly CultureInfo IC = CultureInfo.InvariantCulture;
        public static string S(IDictionary<string, string> a, string k, string def = "") => a != null && a.TryGetValue(k, out var v) ? v : def;
        public static bool B(IDictionary<string, string> a, string k, bool def) => a != null && a.TryGetValue(k, out var v) ? v == "1" || v.Equals("true", StringComparison.OrdinalIgnoreCase) : def;
        public static double D(IDictionary<string, string> a, string k, double def) => a != null && a.TryGetValue(k, out var v) && double.TryParse(v, NumberStyles.Float, IC, out var d) ? d : def;
        public static string F(double v) => v.ToString("0.###", IC);
    }

    /// <summary>后台读一个 DWG（不在界面里打开）。用完 Dispose。</summary>
    sealed class SideDb : IDisposable
    {
        public readonly Database Db;
        readonly Database _prevWorking;
        public SideDb(string path, bool makeWorking)
        {
            Db = new Database(false, true);
            Db.ReadDwgFile(path, FileOpenMode.OpenForReadAndAllShare, true, "");
            Db.CloseInput(true);
            if (makeWorking) { _prevWorking = HostApplicationServices.WorkingDatabase; HostApplicationServices.WorkingDatabase = Db; }
        }
        public void Dispose()
        {
            if (_prevWorking != null) HostApplicationServices.WorkingDatabase = _prevWorking;
            Db.Dispose();
        }
    }

    /// <summary>临时把某个数据库设成工作数据库（有些操作——几何范围、清理——按工作数据库取设置）。</summary>
    sealed class WorkingDbScope : IDisposable
    {
        readonly Database _prev;
        public WorkingDbScope(Database db) { _prev = HostApplicationServices.WorkingDatabase; HostApplicationServices.WorkingDatabase = db; }
        public void Dispose() => HostApplicationServices.WorkingDatabase = _prev;
    }

    static class Ui
    {
        public static float Scale(Control c) { using (var g = c.CreateGraphics()) return g.DpiX / 96f; }

        public static Form NewForm(string title, int w, int h)
        {
            var f = new Form
            {
                Text = title, StartPosition = FormStartPosition.CenterScreen, FormBorderStyle = FormBorderStyle.FixedDialog,
                MaximizeBox = false, MinimizeBox = false, ShowInTaskbar = false, Font = new System.Drawing.Font("Microsoft YaHei UI", 9f),
                AutoScaleMode = AutoScaleMode.Dpi, AutoScaleDimensions = new SizeF(96f, 96f),
            };
            f.ClientSize = new Size(w, h);
            return f;
        }

        public static Label Label(Control parent, string text, int x, int y, int w = 0, Color? color = null)
        {
            var l = new Label { Text = text, Left = x, Top = y, AutoSize = w == 0, ForeColor = color ?? SystemColors.ControlText };
            if (w > 0) { l.Width = w; l.Height = 34; }
            parent.Controls.Add(l); return l;
        }

        public static CheckBox Check(Control parent, string text, int x, int y, bool on, string tip = null, ToolTip tt = null)
        {
            var c = new CheckBox { Text = text, Left = x, Top = y, AutoSize = true, Checked = on };
            parent.Controls.Add(c); if (tip != null) tt?.SetToolTip(c, tip); return c;
        }

        public static TextBox Num(Control parent, double v, int x, int y, int w = 70)
        {
            var t = new TextBox { Text = Args.F(v), Left = x, Top = y - 3, Width = w, TextAlign = HorizontalAlignment.Right };
            parent.Controls.Add(t); return t;
        }

        public static Button Btn(Control parent, string text, int x, int y, int w = 90)
        {
            var b = new Button { Text = text, Left = x, Top = y, Width = w, Height = 28 };
            parent.Controls.Add(b); return b;
        }

        public static double Val(TextBox t, double def) => double.TryParse(t.Text.Trim(), NumberStyles.Float, Args.IC, out var d) ? d : def;

        /// <summary>弹出对话框。测试时（环境变量 THL_SNAP_DIR）：显示后把窗口截图存成 &lt;name&gt;.png 并关闭（返回 Cancel）。</summary>
        /// <summary>在 accoreconsole（无界面批处理）里运行。</summary>
        public static bool IsConsole => System.Diagnostics.Process.GetCurrentProcess().ProcessName.IndexOf("accoreconsole", StringComparison.OrdinalIgnoreCase) >= 0;

        public static DialogResult Show(Form f, string snapName)
        {
            // 无界面批处理里弹窗会让 accoreconsole 访问冲突崩溃：直接当「取消」
            if (IsConsole) return DialogResult.Cancel;
            var dir = Environment.GetEnvironmentVariable("THL_SNAP_DIR");
            if (!string.IsNullOrEmpty(dir))
            {
                f.Shown += (_, __) =>
                {
                    Application.DoEvents();
                    try
                    {
                        Directory.CreateDirectory(dir);
                        using (var bmp = new Bitmap(f.Width, f.Height))
                        {
                            f.DrawToBitmap(bmp, new Rectangle(0, 0, f.Width, f.Height));
                            bmp.Save(Path.Combine(dir, snapName + ".png"), System.Drawing.Imaging.ImageFormat.Png);
                        }
                    }
                    catch { }
                    f.DialogResult = DialogResult.Cancel;
                    f.Close();
                };
            }
            return Autodesk.AutoCAD.ApplicationServices.Application.ShowModalDialog(f);
        }
    }
}
