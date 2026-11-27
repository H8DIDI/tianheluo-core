// 一键出图向导：① 准备图纸（瘦身 + 美化）→ ② 分区 → ③ 图框与方案 → ④ 预览（可改图名/比例/勾掉）→ ⑤ 生成（进度、可取消）。
// 每页都有默认值，连点「下一步」就能出合格的图；细调在每页里改，或「设置…」。
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Windows.Forms;

namespace Thl.Acad.Sheets.Wizard
{
    public sealed class WizardForm : Form
    {
        readonly IWizardBackend _b;
        readonly WizardSettings _s = new WizardSettings();
        readonly Dictionary<string, string> _prefs;
        List<ZoneInfo> _zones = new List<ZoneInfo>();
        List<SheetPlan> _plans = new List<SheetPlan>();
        double[] _ext;
        int _page;
        bool _cancel, _running, _prepared;

        readonly Panel[] _pages = new Panel[5];
        readonly Label[] _steps = new Label[5];
        readonly Button _back = new Button { Text = "上一步", Width = 100, Height = 34 };
        readonly Button _next = new Button { Text = "下一步", Width = 120, Height = 34 };
        readonly Button _close = new Button { Text = "关闭", Width = 90, Height = 34 };
        readonly Button _settings = new Button { Text = "设置…", Width = 90, Height = 34 };
        readonly Button _list = new Button { Text = "图纸列表…", Width = 100, Height = 34, Visible = false };
        readonly Label _hint = new Label { AutoSize = false, Height = 34, ForeColor = Color.DimGray, TextAlign = ContentAlignment.MiddleLeft };
        static readonly string[] StepNames = { "① 准备图纸", "② 分区", "③ 图框与方案", "④ 预览", "⑤ 生成" };

        // ① 准备
        readonly CheckBox _ckSlim = new CheckBox { AutoSize = true, Text = "先瘦身：去掉导入的 CAD 底图、连短线、删碎图形、井只留三圈、合并相同块" };
        readonly CheckBox _ckTidy = new CheckBox { AutoSize = true, Text = "出图美化：补缺字体、虚线按纸面长度、删重复线、浅色加深、底图淡显（规则在「设置 → 美化」）" };
        readonly Label _docInfo = new Label { AutoSize = false, Width = 900, Height = 70 };
        // ② 分区
        readonly DataGridView _zg = Grid();
        readonly Label _zoneHint = new Label { AutoSize = false, Width = 920, Height = 46, ForeColor = Color.FromArgb(160, 90, 0) };
        // ③ 方案
        readonly ComboBox _cbTpl = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 300 };
        readonly ComboBox _cbPaper = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 70 };
        readonly Label _tplInfo = new Label { AutoSize = false, Width = 600, Height = 40, ForeColor = Color.DimGray };
        readonly RadioButton _rEach = new RadioButton { AutoSize = true, Text = "每张自动选比例（太空的长条楼栋自动拆成 2~3 张）" };
        readonly RadioButton _rUni = new RadioButton { AutoSize = true, Text = "全套统一比例（自动取能放下最大那块的）" };
        readonly RadioButton _rFix = new RadioButton { AutoSize = true, Text = "全套指定比例 1:" };
        readonly ComboBox _cbFix = new ComboBox { Width = 80 };
        readonly NumericUpDown _nFill = new NumericUpDown { Minimum = 0, Maximum = 95, Increment = 5, Width = 60 };
        readonly CheckBox _ckLegend = new CheckBox { AutoSize = true, Text = "图例（只列本张图用到的图层）" };
        readonly CheckBox _ckIndex = new CheckBox { AutoSize = true, Text = "分区索引图" };
        readonly CheckBox _ckMatch = new CheckBox { AutoSize = true, Text = "接图标记" };
        readonly CheckBox _ckToc = new CheckBox { AutoSize = true, Text = "图纸目录" };
        readonly CheckBox _ckPdf = new CheckBox { AutoSize = true, Text = "合成一个 PDF" };
        readonly TextBox _tbPrefix = new TextBox { Width = 220 };
        readonly NumericUpDown _nDigits = new NumericUpDown { Minimum = 1, Maximum = 5, Width = 50 };
        readonly NumericUpDown _nStart = new NumericUpDown { Minimum = 0, Maximum = 9999, Width = 70 };
        readonly TextBox _tbBase = new TextBox { Width = 220 };
        readonly Label _numPreview = new Label { AutoSize = true, ForeColor = Color.DarkGreen };
        // ④ 预览
        readonly DataGridView _pg = Grid();
        readonly Label _sum = new Label { AutoSize = false, Width = 740, Height = 24, AutoEllipsis = true };
        // ⑤ 生成
        readonly ProgressBar _bar = new ProgressBar { Width = 920, Height = 22 };
        readonly TextBox _log = new TextBox { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, Width = 920, Height = 340, BackColor = Color.White };
        readonly Button _openPdf = new Button { Text = "打开 PDF", Width = 110, Height = 32, Visible = false };
        readonly Button _openDir = new Button { Text = "打开文件夹", Width = 110, Height = 32, Visible = false };
        string _resultPdf, _resultDwg;

        static DataGridView Grid() => new DataGridView
        {
            AllowUserToAddRows = false, AllowUserToDeleteRows = false, RowHeadersVisible = false, BackgroundColor = Color.White,
            BorderStyle = BorderStyle.FixedSingle, SelectionMode = DataGridViewSelectionMode.FullRowSelect, MultiSelect = false,
            ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.AutoSize, EditMode = DataGridViewEditMode.EditOnEnter,
        };

        public WizardForm(IWizardBackend backend)
        {
            _b = backend;
            _prefs = _b.LoadPrefs();
            Text = "天河落 · 一键出图 — " + _b.DocName;
            Font = new Font("Microsoft YaHei UI", 9.5f);
            AutoScaleMode = AutoScaleMode.None;
            ClientSize = new Size(980, 680);
            FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = false; MinimizeBox = false;
            StartPosition = FormStartPosition.CenterScreen; ShowInTaskbar = false;

            // 顶部步骤条
            var top = new Panel { Left = 0, Top = 0, Width = 980, Height = 54, BackColor = Color.FromArgb(246, 247, 250) };
            for (int i = 0; i < 5; i++)
            {
                _steps[i] = new Label { Text = StepNames[i], Left = 20 + i * 190, Top = 14, Width = 180, Height = 26, TextAlign = ContentAlignment.MiddleCenter };
                top.Controls.Add(_steps[i]);
            }
            Controls.Add(top);
            for (int i = 0; i < 5; i++) { _pages[i] = new Panel { Left = 20, Top = 66, Width = 940, Height = 540, Visible = false }; Controls.Add(_pages[i]); }
            BuildPrepare(_pages[0]); BuildZones(_pages[1]); BuildScheme(_pages[2]); BuildPreview(_pages[3]); BuildRun(_pages[4]);

            // 底部按钮
            _settings.Left = 20; _settings.Top = 626;
            _list.Left = 116; _list.Top = 626;
            _hint.Left = 226; _hint.Top = 626; _hint.Width = 330;
            _back.Left = 560; _back.Top = 626; _next.Left = 670; _next.Top = 626; _close.Left = 800; _close.Top = 626;
            Controls.AddRange(new Control[] { _settings, _list, _hint, _back, _next, _close });
            _list.Click += (_, __) => Safe(() => _openList?.Invoke(this));
            _back.Click += (_, __) => Safe(() => GoTo(_page - 1));
            _next.Click += (_, __) => Safe(Next);
            _close.Click += (_, __) => { if (_running) { _cancel = true; return; } Close(); };
            _settings.Click += (_, __) => Safe(() => { using (var f = new SettingsForm(_b, _s.Template)) f.ShowDialog(this); });
            AcceptButton = _next;
            FormClosing += (_, e) => { if (_running) { e.Cancel = true; _cancel = true; } else SavePrefs(); };
            LoadPrefs();
            FitDpi(this);
            GoTo(0);
        }

        /// <summary>高 DPI（AutoCAD 按显示器缩放运行）：字号按点数自动变大，这里把位置、尺寸、表格列宽按同一比例放大，排版不挤。</summary>
        internal static void FitDpi(Form f)
        {
            float k = f.DeviceDpi / 96f;
            if (k < 1.01f) return;
            f.Scale(new SizeF(k, k));
            void Walk(Control c)
            {
                if (c is DataGridView g)
                {
                    foreach (DataGridViewColumn col in g.Columns) col.Width = (int)(col.Width * k);
                    g.RowTemplate.Height = (int)(g.RowTemplate.Height * k);
                }
                foreach (Control ch in c.Controls) Walk(ch);
            }
            Walk(f);
        }


        // ------------------------------------------------------------------ 通用
        void Safe(Action a)
        {
            try { a(); }
            catch (UserError ex) { MessageBox.Show(this, ex.Message, "一键出图", MessageBoxButtons.OK, MessageBoxIcon.Information); }
            catch (Exception ex)
            {
                MessageBox.Show(this, "出了点问题，没有完成这一步：\n" + ex.Message + "\n\n图纸没有被改坏（出图都在另存的「_出图」文件里进行）。可以再试一次；还不行请把日志发给我们：\n" + _b.SettingsFolder, "一键出图", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        public void GoTo(int i)
        {
            if (i < 0 || i > 4) return;
            _page = i;
            for (int k = 0; k < 5; k++)
            {
                _pages[k].Visible = k == i;
                _steps[k].BackColor = k == i ? Color.FromArgb(37, 99, 235) : Color.Transparent;
                _steps[k].ForeColor = k == i ? Color.White : k < i ? Color.FromArgb(37, 99, 235) : Color.Gray;
                _steps[k].Font = new Font(Font, k == i ? FontStyle.Bold : FontStyle.Regular);
            }
            _back.Enabled = i > 0 && i < 4;
            _next.Text = i == 3 ? "生成" : i == 4 ? "完成" : "下一步";
            _next.Enabled = !(i == 4 && _running);
            _hint.Text = new[] { "原图不会被改：处理和出图都在另存的「_出图.dwg」里。", "每个分区出一张（或几张）图。名称可以直接改。", "上次的选择已经记住了，不用每次都选。", "可以改图名、比例，勾掉不要的图。", "" }[i];
            if (i == 1 && _zones.Count == 0) Safe(() => SetZones(_b.ZonesFromLayer(), true));
            if (i == 3) Safe(Replan);
        }

        void Next()
        {
            switch (_page)
            {
                case 0: _s.Slim = _ckSlim.Checked; _s.Tidy = _ckTidy.Checked; GoTo(1); break;
                case 1:
                    ReadZones();
                    if (_zones.Count == 0) throw new UserError("还没有分区。点上面三个按钮之一：在图上框选、按网格自动分块，或整张图出一张。");
                    GoTo(2); break;
                case 2:
                    ReadScheme();
                    if (string.IsNullOrEmpty(_s.Template)) throw new UserError("还没有图框。点「学习新图框…」选一个图框 DWG（比如设计院给的图框），插件会自动认出图名、图号等格子。");
                    GoTo(3); break;
                case 3:
                    ReadPreview();
                    if (!_plans.Any(p => p.On)) throw new UserError("一张图都没勾选。");
                    GoTo(4); Run(); break;
                case 4: Close(); break;
            }
        }

        static Label L(Control parent, string text, int x, int y, bool bold = false, Color? color = null)
        {
            var l = new Label { Text = text, Left = x, Top = y, AutoSize = true, ForeColor = color ?? SystemColors.ControlText };
            if (bold) l.Font = new Font(parent.FindForm()?.Font ?? SystemFonts.DefaultFont, FontStyle.Bold);
            parent.Controls.Add(l); return l;
        }
        static Button B(Control parent, string text, int x, int y, int w, EventHandler h) { var b = new Button { Text = text, Left = x, Top = y, Width = w, Height = 32 }; b.Click += h; parent.Controls.Add(b); return b; }

        // ------------------------------------------------------------------ ① 准备
        void BuildPrepare(Panel p)
        {
            L(p, "这张图", 0, 0, true);
            _docInfo.Left = 0; _docInfo.Top = 26; p.Controls.Add(_docInfo);
            L(p, "出图前先处理一下（推荐都勾上）", 0, 110, true);
            _ckSlim.Left = 10; _ckSlim.Top = 140; p.Controls.Add(_ckSlim);
            _ckTidy.Left = 10; _ckTidy.Top = 172; p.Controls.Add(_ckTidy);
            L(p, "两样都只在另存的「原名_出图.dwg」里做，原图不动。已经处理过的图再跑一遍也没关系（不会重复处理出问题）。", 30, 212, false, Color.DimGray);
            L(p, "处理完接着在同一个文件里出图。下一步选分区。", 30, 240, false, Color.DimGray);
        }

        // ------------------------------------------------------------------ ② 分区
        void BuildZones(Panel p)
        {
            L(p, "分区（每块出一张图；太长的楼栋在预览里会自动拆成几张）", 0, 0, true);
            B(p, "读图层「THL-分区」", 0, 30, 170, (_, __) => Safe(() => SetZones(_b.ZonesFromLayer(), true)));
            B(p, "到图上框选…", 180, 30, 130, (_, __) => Safe(() => { var z = _b.PickZones(this); if (z.Count > 0) SetZones(_zones.Concat(z).ToList(), false); }));
            B(p, "按网格自动分块", 320, 30, 140, (_, __) => Safe(() => { ReadScheme(); SetZones(_b.GridZones(_s), false); }));
            B(p, "整张图一张", 470, 30, 110, (_, __) => Safe(() => SetZones(_b.WholeDrawing(), false)));
            B(p, "删除选中", 590, 30, 100, (_, __) => { foreach (DataGridViewRow r in _zg.SelectedRows) _zg.Rows.Remove(r); ReadZones(); });
            _zoneHint.Left = 0; _zoneHint.Top = 70; p.Controls.Add(_zoneHint);
            _zg.Left = 0; _zg.Top = 120; _zg.Width = 600; _zg.Height = 410;
            _zg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "分区名称（可改）", Width = 260 });
            _zg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "来源", Width = 100, ReadOnly = true });
            _zg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "范围（m）", Width = 220, ReadOnly = true });
            p.Controls.Add(_zg);
            var pic = new PictureBox { Left = 615, Top = 120, Width = 320, Height = 410, BorderStyle = BorderStyle.FixedSingle, BackColor = Color.White };
            p.Controls.Add(pic);
            _zg.SelectionChanged += (_, __) => pic.Image = Thumb(pic.Width, pic.Height, _zg.CurrentRow?.Index ?? -1, _zones);
            p.VisibleChanged += (_, __) => { if (p.Visible) pic.Image = Thumb(pic.Width, pic.Height, _zg.CurrentRow?.Index ?? -1, _zones); };
        }

        void SetZones(List<ZoneInfo> zs, bool fromLayer)
        {
            _zones = zs;
            _zg.Rows.Clear();
            foreach (var z in zs) _zg.Rows.Add(z.Name, z.Source, $"{(z.X1 - z.X0) / 1000:0} × {(z.Y1 - z.Y0) / 1000:0}");
            _zoneHint.Text = zs.Count > 0 ? (fromLayer ? $"从图层「THL-分区」读到 {zs.Count} 块。名称在 CAD 里改（多段线里面的文字），或直接在下面表格里改。" : $"{zs.Count} 块。")
                : "图里还没有分区。三选一：① 点「到图上框选」，一框一块、输名称（会存到 THL-分区 图层，下次直接用）；② 在 CAD 里用多段线在图层 THL-分区 上画出楼栋范围、里面写名称，再点「读图层」；③ 点「按网格自动分块」。";
            _ext = _b.Extents();
        }

        void ReadZones()
        {
            var list = new List<ZoneInfo>();
            for (int i = 0; i < _zg.Rows.Count && i < _zones.Count; i++)
            {
                var name = Convert.ToString(_zg.Rows[i].Cells[0].Value)?.Trim();
                _zones[i].Name = string.IsNullOrEmpty(name) ? "分区" + (i + 1) : name;
                list.Add(_zones[i]);
            }
            _zones = list;
        }

        /// <summary>缩略图：总图外框 + 各分区（当前红色填充）。</summary>
        Image Thumb(int w, int h, int cur, List<ZoneInfo> zones, List<double[]> highlight = null)
        {
            var bmp = new Bitmap(w, h);
            using (var g = Graphics.FromImage(bmp))
            {
                g.Clear(Color.White); g.SmoothingMode = SmoothingMode.AntiAlias;
                var ext = _ext ?? new double[] { 0, 0, 1, 1 };
                if (zones.Count > 0)
                    ext = new[] { Math.Min(ext[0], zones.Min(z => z.X0)), Math.Min(ext[1], zones.Min(z => z.Y0)), Math.Max(ext[2], zones.Max(z => z.X1)), Math.Max(ext[3], zones.Max(z => z.Y1)) };
                double sx = (w - 16) / Math.Max(1e-9, ext[2] - ext[0]), sy = (h - 16) / Math.Max(1e-9, ext[3] - ext[1]), sc = Math.Min(sx, sy);
                double ox = 8 + ((w - 16) - (ext[2] - ext[0]) * sc) / 2, oy = 8 + ((h - 16) - (ext[3] - ext[1]) * sc) / 2;
                PointF P(double[] q) => new PointF((float)(ox + (q[0] - ext[0]) * sc), (float)(h - oy - (q[1] - ext[1]) * sc));
                using (var pen = new Pen(Color.Gainsboro, 1)) g.DrawRectangle(pen, (float)ox, (float)(h - oy - (ext[3] - ext[1]) * sc), (float)((ext[2] - ext[0]) * sc), (float)((ext[3] - ext[1]) * sc));
                using (var f = new Font("Microsoft YaHei UI", 7.5f))
                    for (int i = 0; i < zones.Count; i++)
                    {
                        var pts = zones[i].Poly.Select(P).ToArray();
                        if (pts.Length < 3) continue;
                        if (i == cur) using (var br = new SolidBrush(Color.FromArgb(110, 220, 38, 38))) g.FillPolygon(br, pts);
                        using (var pen = new Pen(i == cur ? Color.FromArgb(220, 38, 38) : Color.FromArgb(120, 120, 120), i == cur ? 2 : 1)) g.DrawPolygon(pen, pts);
                        var c = new PointF(pts.Average(q => q.X), pts.Average(q => q.Y));
                        var sz = g.MeasureString(zones[i].Name, f);
                        var bw = pts.Max(q => q.X) - pts.Min(q => q.X);
                        if (sz.Width < bw * 1.1 || i == cur) g.DrawString(zones[i].Name, f, i == cur ? Brushes.DarkRed : Brushes.DimGray, c.X - sz.Width / 2, c.Y - sz.Height / 2);
                    }
                if (highlight != null && highlight.Count >= 3)
                {
                    var pts = highlight.Select(P).ToArray();
                    using (var br = new SolidBrush(Color.FromArgb(130, 220, 38, 38))) g.FillPolygon(br, pts);
                    using (var pen = new Pen(Color.FromArgb(220, 38, 38), 2)) g.DrawPolygon(pen, pts);
                }
            }
            return bmp;
        }

        // ------------------------------------------------------------------ ③ 方案
        void BuildScheme(Panel p)
        {
            int y = 0;
            L(p, "图框", 0, y + 4, true);
            _cbTpl.Left = 60; _cbTpl.Top = y; p.Controls.Add(_cbTpl);
            L(p, "图幅", 375, y + 4); _cbPaper.Left = 415; _cbPaper.Top = y; _cbPaper.Items.AddRange(new object[] { "A0", "A1", "A2", "A3" }); p.Controls.Add(_cbPaper);
            B(p, "学习新图框…", 500, y - 2, 120, (_, __) => Safe(LearnFrame));
            _tplInfo.Left = 60; _tplInfo.Top = y + 32; p.Controls.Add(_tplInfo);
            _cbTpl.SelectedIndexChanged += (_, __) => { _tplInfo.Text = _cbTpl.SelectedItem is string t ? _b.TemplateInfo(t) : ""; };
            y = 84;
            L(p, "比例", 0, y, true);
            _rEach.Left = 60; _rEach.Top = y - 2; _rUni.Left = 60; _rUni.Top = y + 26; _rFix.Left = 60; _rFix.Top = y + 54;
            _cbFix.Left = 200; _cbFix.Top = y + 52; foreach (var c in new[] { 100, 150, 200, 250, 300, 400, 500, 600, 750, 800, 1000, 1500, 2000 }) _cbFix.Items.Add(c.ToString());
            p.Controls.AddRange(new Control[] { _rEach, _rUni, _rFix, _cbFix });
            L(p, "主图占图框可用区不到", 470, y); _nFill.Left = 615; _nFill.Top = y - 3; p.Controls.Add(_nFill); L(p, "% 就拆成几张", 680, y);
            y = 180;
            L(p, "图上加", 0, y, true);
            _ckLegend.Left = 60; _ckLegend.Top = y - 2; _ckIndex.Left = 300; _ckIndex.Top = y - 2; _ckMatch.Left = 420; _ckMatch.Top = y - 2;
            _ckToc.Left = 540; _ckToc.Top = y - 2; _ckPdf.Left = 650; _ckPdf.Top = y - 2;
            p.Controls.AddRange(new Control[] { _ckLegend, _ckIndex, _ckMatch, _ckToc, _ckPdf });
            y = 230;
            L(p, "图名", 0, y + 3, true); L(p, "分区名 +", 60, y + 3); _tbBase.Left = 125; _tbBase.Top = y; p.Controls.Add(_tbBase);
            y = 270;
            L(p, "图号", 0, y + 3, true); L(p, "前缀", 60, y + 3); _tbPrefix.Left = 100; _tbPrefix.Top = y; p.Controls.Add(_tbPrefix);
            L(p, "位数", 335, y + 3); _nDigits.Left = 370; _nDigits.Top = y; p.Controls.Add(_nDigits);
            L(p, "起始", 435, y + 3); _nStart.Left = 470; _nStart.Top = y; p.Controls.Add(_nStart);
            _numPreview.Left = 560; _numPreview.Top = y + 3; p.Controls.Add(_numPreview);
            EventHandler pv = (_, __) => { ReadScheme(); _numPreview.Text = "例：" + _s.Number(0) + "、" + _s.Number(1) + " …"; };
            _tbPrefix.TextChanged += pv; _nDigits.ValueChanged += pv; _nStart.ValueChanged += pv;
            L(p, "图号规则参考：报审图号一般是「项目-专业-图类-序号」，例如 XM-JPS-PM-001。前缀写到序号前一位为止。", 60, y + 36, false, Color.DimGray);
            L(p, "这一页的选择会记住，下次打开就是这些。图例中文名、美化规则、方案导入导出在左下角「设置…」。", 0, 500, false, Color.DimGray);
        }

        void LearnFrame()
        {
            using (var d = new OpenFileDialog { Filter = "图框 DWG|*.dwg", Title = "选择图框 DWG（只读取，不改原文件）" })
            {
                if (d.ShowDialog(this) != DialogResult.OK) return;
                var name = Path.GetFileNameWithoutExtension(d.FileName);
                var logs = new List<string>();
                Cursor = Cursors.WaitCursor;
                try { name = _b.LearnTemplate(d.FileName, name, logs.Add); } finally { Cursor = Cursors.Default; }
                FillTemplates(name);
                MessageBox.Show(this, "图框学好了：\n" + string.Join("\n", logs.Take(6)), "学习图框");
            }
        }

        void FillTemplates(string select)
        {
            _cbTpl.Items.Clear();
            foreach (var t in _b.Templates()) _cbTpl.Items.Add(t);
            var sel = _cbTpl.Items.Cast<string>().FirstOrDefault(t => t == select) ?? _cbTpl.Items.Cast<string>().FirstOrDefault();
            if (sel != null) _cbTpl.SelectedItem = sel;
        }

        void ReadScheme()
        {
            _s.Template = _cbTpl.SelectedItem as string ?? "";
            _s.Paper = _cbPaper.SelectedItem as string ?? "A1";
            _s.ScaleMode = _rFix.Checked ? "fixed" : _rUni.Checked ? "uniform" : "each";
            _s.FixedScale = int.TryParse(_cbFix.Text.Replace("1:", ""), out var f) && f > 0 ? f : 500;
            _s.MinFill = (double)_nFill.Value / 100;
            _s.Split = _nFill.Value > 0;
            _s.Legend = _ckLegend.Checked; _s.Index = _ckIndex.Checked; _s.Match = _ckMatch.Checked; _s.Toc = _ckToc.Checked; _s.Pdf = _ckPdf.Checked;
            _s.NumPrefix = _tbPrefix.Text; _s.NumDigits = (int)_nDigits.Value; _s.NumStart = (int)_nStart.Value;
            _s.BaseTitle = _tbBase.Text.Trim();
        }

        // ------------------------------------------------------------------ ④ 预览
        void BuildPreview(Panel p)
        {
            _sum.Left = 0; _sum.Top = 0; p.Controls.Add(_sum);
            B(p, "按当前设置重新计算", 760, -4, 170, (_, __) => Safe(Replan));
            _pg.Left = 0; _pg.Top = 30; _pg.Width = 940; _pg.Height = 500; _pg.RowTemplate.Height = 74;
            _pg.SelectionMode = DataGridViewSelectionMode.CellSelect;
            _pg.Columns.Add(new DataGridViewCheckBoxColumn { HeaderText = "出图", Width = 46 });
            _pg.Columns.Add(new DataGridViewImageColumn { HeaderText = "位置", Width = 96, ImageLayout = DataGridViewImageCellLayout.Zoom, ReadOnly = true });
            _pg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "图号", Width = 190 });
            _pg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "图名（可改）", Width = 280 });
            _pg.Columns.Add(new DataGridViewComboBoxColumn { HeaderText = "比例", Width = 90, DataSource = new[] { "100", "150", "200", "250", "300", "400", "500", "600", "750", "800", "1000", "1500", "2000", "2500", "3000", "5000" }.ToList(), FlatStyle = FlatStyle.Flat });
            _pg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "主图占图框", Width = 90, ReadOnly = true });
            _pg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "说明", Width = 140, ReadOnly = true });
            _pg.DataError += (_, e) => e.ThrowException = false;
            _pg.CurrentCellDirtyStateChanged += (_, __) => { if (_pg.CurrentCell is DataGridViewCheckBoxCell) _pg.CommitEdit(DataGridViewDataErrorContexts.Commit); };
            p.Controls.Add(_pg);
        }

        void Replan()
        {
            ReadScheme();
            var logs = new List<string>();
            _plans = _b.Plan(_zones, _s, logs.Add);
            for (int i = 0; i < _plans.Count; i++) { _plans[i].Number = _s.Number(i); _plans[i].Title = _s.Title(_plans[i].Zone); }
            _pg.Rows.Clear();
            var scaleList = ((List<string>)((DataGridViewComboBoxColumn)_pg.Columns[4]).DataSource);
            foreach (var pl in _plans)
            {
                var sc = pl.Scale.ToString(CultureInfo.InvariantCulture);
                if (!scaleList.Contains(sc)) scaleList.Add(sc);
                var note = pl.SplitOf.Length > 0 ? "由「" + pl.SplitOf + "」拆出" : pl.Fill < _s.MinFill ? "图偏小，可改小比例数" : "";
                _pg.Rows.Add(pl.On, Thumb(_pg.Columns[1].Width - 4, _pg.RowTemplate.Height - 4, -1, _zones, pl.Poly), pl.Number, pl.Title, sc, pl.Fill.ToString("P0"), note);
            }
            _sum.Text = $"共 {_plans.Count} 张，图框「{_s.Template}」{_s.Paper}。" + (logs.Count > 0 ? "  " + string.Join("；", logs.Take(3)) : "");
        }

        void ReadPreview()
        {
            _pg.EndEdit();
            for (int i = 0; i < _plans.Count && i < _pg.Rows.Count; i++)
            {
                var r = _pg.Rows[i];
                _plans[i].On = Equals(r.Cells[0].Value, true);
                _plans[i].Number = Convert.ToString(r.Cells[2].Value) ?? "";
                _plans[i].Title = Convert.ToString(r.Cells[3].Value) ?? "";
                if (int.TryParse(Convert.ToString(r.Cells[4].Value), out var sc) && sc > 0) _plans[i].Scale = sc;
            }
        }

        // ------------------------------------------------------------------ ⑤ 生成
        void BuildRun(Panel p)
        {
            _bar.Left = 0; _bar.Top = 0; p.Controls.Add(_bar);
            _log.Left = 0; _log.Top = 34; p.Controls.Add(_log);
            _openPdf.Left = 0; _openPdf.Top = 386; _openDir.Left = 120; _openDir.Top = 386;
            _openPdf.Click += (_, __) => { if (_resultPdf != null) _b.Open(_resultPdf); };
            _openDir.Click += (_, __) => { var f = _resultPdf ?? _resultDwg; if (f != null) _b.Open(Path.GetDirectoryName(f)); };
            p.Controls.Add(_openPdf); p.Controls.Add(_openDir);
            L(p, "生成中可以点「取消」：已经建好的图纸保留，下次再点会更新而不是重复新建。", 0, 430, false, Color.DimGray);
        }

        void Log(string s) { _log.AppendText(s.Replace("\n", Environment.NewLine) + Environment.NewLine); Application.DoEvents(); }

        void Run()
        {
            _running = true; _cancel = false; _close.Text = "取消"; _next.Enabled = false; _back.Enabled = false; _settings.Enabled = false;
            _log.Clear(); _openPdf.Visible = _openDir.Visible = false;
            try
            {
                if ((_s.Slim || _s.Tidy) && !_prepared)
                {
                    _bar.Style = ProgressBarStyle.Marquee;
                    Log("准备图纸（瘦身 / 美化）…");
                    Log(_b.Prepare(_s, (i, m) => { Log("  " + m); }));
                    _prepared = true;
                }
                _bar.Style = ProgressBarStyle.Continuous; _bar.Maximum = Math.Max(1, _plans.Count(p => p.On) + 2); _bar.Value = 0;
                var res = _b.Generate(_plans, _zones, _s, (i, m) => { _bar.Value = Math.Min(_bar.Maximum, i); Log(m); }, () => { Application.DoEvents(); return _cancel; });
                _bar.Value = _bar.Maximum;
                Log(res);
                foreach (var line in res.Split('\n'))
                {
                    if (line.Contains(".pdf")) _resultPdf = ExtractPath(line, ".pdf");
                    if (line.Contains(".dwg")) _resultDwg = ExtractPath(line, ".dwg");
                }
                _openPdf.Visible = _resultPdf != null; _openDir.Visible = _resultPdf != null || _resultDwg != null;
            }
            catch (UserError ex) { Log("没有完成：" + ex.Message); }
            catch (Exception ex) { Log("出了点问题，没有完成：" + ex.Message + Environment.NewLine + "原图没有被改（出图在「_出图.dwg」里进行）。再点一次「生成」可以接着做。"); }
            finally
            {
                _running = false; _close.Text = "关闭"; _next.Enabled = true; _settings.Enabled = true; _back.Enabled = true;
                SavePrefs();
            }
        }

        static string ExtractPath(string line, string ext)
        {
            var i = line.IndexOf(ext, StringComparison.OrdinalIgnoreCase);
            var j = line.LastIndexOf(':', i) - 1;
            return j >= 0 ? line.Substring(j, i + ext.Length - j).Trim() : null;
        }

        // ------------------------------------------------------------------ 记住上次
        void LoadPrefs()
        {
            string P(string k, string d) => _prefs.TryGetValue("w." + k, out var v) && v.Length > 0 ? v : d;
            bool PB(string k, bool d) => _prefs.TryGetValue("w." + k, out var v) ? v == "1" : d;
            _docInfo.Text = $"{_b.DocName}　{_b.FileMB:0.0} MB" + (_b.HasCadUnderlay ? "　·　里面带着导入的 CAD 底图（建议瘦身）" : "") + $"\n{_b.DocPath}";
            _ckSlim.Checked = PB("slim", _b.FileMB > 20 || _b.HasCadUnderlay);
            _ckTidy.Checked = PB("tidy", true);
            FillTemplates(P("tpl", P("tplFallback", "")));
            _cbPaper.SelectedItem = P("paper", "A1");
            var mode = P("scale", "each");
            _rEach.Checked = mode == "each"; _rUni.Checked = mode == "uniform"; _rFix.Checked = mode == "fixed";
            _cbFix.Text = P("fixed", "500");
            _nFill.Value = Math.Max(0, Math.Min(95, int.TryParse(P("fill", "70"), out var f) ? f : 70));
            _ckLegend.Checked = PB("legend", true); _ckIndex.Checked = PB("index", true); _ckMatch.Checked = PB("match", true); _ckToc.Checked = PB("toc", true); _ckPdf.Checked = PB("pdf", true);
            _tbPrefix.Text = P("prefix", ""); _nDigits.Value = int.TryParse(P("digits", "3"), out var d) ? Math.Max(1, Math.Min(5, d)) : 3;
            _nStart.Value = int.TryParse(P("start", "1"), out var st) ? Math.Max(0, st) : 1;
            _tbBase.Text = P("base", "管线综合平面图");
            ReadScheme(); _numPreview.Text = "例：" + _s.Number(0) + "、" + _s.Number(1) + " …";
        }

        void SavePrefs()
        {
            ReadScheme();
            void S(string k, string v) => _prefs["w." + k] = v ?? "";
            S("slim", _ckSlim.Checked ? "1" : "0"); S("tidy", _ckTidy.Checked ? "1" : "0");
            S("tpl", _s.Template); S("paper", _s.Paper); S("scale", _s.ScaleMode); S("fixed", _s.FixedScale.ToString()); S("fill", ((int)(_s.MinFill * 100)).ToString());
            S("legend", _s.Legend ? "1" : "0"); S("index", _s.Index ? "1" : "0"); S("match", _s.Match ? "1" : "0"); S("toc", _s.Toc ? "1" : "0"); S("pdf", _s.Pdf ? "1" : "0");
            S("prefix", _s.NumPrefix); S("digits", _s.NumDigits.ToString()); S("start", _s.NumStart.ToString()); S("base", _s.BaseTitle);
            try { _b.SavePrefs(_prefs); } catch { }
        }

        /// <summary>测试用：把当前窗口画成图片。</summary>
        public void Snap(string path)
        {
            Application.DoEvents();
            using (var bmp = new Bitmap(Width, Height)) { DrawToBitmap(bmp, new Rectangle(0, 0, Width, Height)); bmp.Save(path, System.Drawing.Imaging.ImageFormat.Png); }
        }
        public void TestNext() => Next();
        /// <summary>打开原来的「图纸列表」（排序、编号、Excel 改图签、单张框选建图）。</summary>
        public Action<IWin32Window> OpenList { set { _openList = value; _list.Visible = value != null; } }
        Action<IWin32Window> _openList;
        public IReadOnlyList<SheetPlan> Plans => _plans;
    }
}
