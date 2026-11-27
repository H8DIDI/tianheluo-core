// 设置：图例中文名（自动列出图里出现的图层，填名字就行）、出图美化规则（表格 + 开关 + 恢复默认）、方案导入导出（发给同事一份就同样出图）。
using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Windows.Forms;

namespace Thl.Acad.Sheets.Wizard
{
    public sealed class SettingsForm : Form
    {
        readonly IWizardBackend _b;
        readonly string _tpl;
        readonly TabControl _tabs = new TabControl { Left = 10, Top = 10, Width = 900, Height = 560 };
        readonly DataGridView _lg = G();
        readonly DataGridView _rg = G();
        readonly Dictionary<string, CheckBox> _sw = new Dictionary<string, CheckBox>();
        readonly TextBox _shx = new TextBox { Width = 140 }, _big = new TextBox { Width = 140 };
        readonly Label _status = new Label { Left = 12, Top = 584, Width = 640, Height = 24, ForeColor = Color.DimGray };

        static DataGridView G() => new DataGridView
        {
            AllowUserToDeleteRows = true, RowHeadersVisible = false, BackgroundColor = Color.White, BorderStyle = BorderStyle.FixedSingle,
            ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.AutoSize, EditMode = DataGridViewEditMode.EditOnEnter,
        };

        public SettingsForm(IWizardBackend b, string currentTemplate)
        {
            _b = b; _tpl = currentTemplate;
            Text = "天河落 · 出图设置";
            Font = new Font("Microsoft YaHei UI", 9.5f);
            AutoScaleMode = AutoScaleMode.None;
            ClientSize = new Size(920, 620); FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = MinimizeBox = false;
            StartPosition = FormStartPosition.CenterParent; ShowInTaskbar = false;
            Controls.Add(_tabs); Controls.Add(_status);
            var ok = new Button { Text = "保存并关闭", Left = 680, Top = 578, Width = 120, Height = 32 };
            var cancel = new Button { Text = "取消", Left = 810, Top = 578, Width = 100, Height = 32, DialogResult = DialogResult.Cancel };
            ok.Click += (_, __) => { if (SaveAll()) { DialogResult = DialogResult.OK; Close(); } };
            Controls.Add(ok); Controls.Add(cancel); CancelButton = cancel;
            _tabs.TabPages.Add(LegendPage());
            _tabs.TabPages.Add(TidyPage());
            _tabs.TabPages.Add(SchemePage());
            WizardForm.FitDpi(this);
        }

        public void ShowTab(int i) => _tabs.SelectedIndex = i;

        // ------------------------------------------------------------------ 图例
        TabPage LegendPage()
        {
            var p = new TabPage("图例中文名");
            p.Controls.Add(new Label { Text = "下面是这张图里出现的图层（按对象数排）。填上报审要显示的名字（如 给水管、消防管、强电排管）；不想进图例的去掉「进图例」。同名同色的会合成一行。", Left = 8, Top = 8, Width = 870, Height = 40 });
            _lg.Left = 8; _lg.Top = 52; _lg.Width = 876; _lg.Height = 470; _lg.AllowUserToAddRows = false;
            _lg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "颜色", Width = 50, ReadOnly = true });
            _lg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "图层", Width = 240, ReadOnly = true });
            _lg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "对象数", Width = 80, ReadOnly = true });
            _lg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "图例显示名（填这里）", Width = 340 });
            _lg.Columns.Add(new DataGridViewCheckBoxColumn { HeaderText = "进图例", Width = 70 });
            try
            {
                foreach (var r in _b.LegendRows())
                {
                    var i = _lg.Rows.Add("", r.Layer, r.Count, r.Name, r.Show);
                    _lg.Rows[i].Cells[0].Style.BackColor = Color.FromArgb(255, Color.FromArgb(r.Rgb));
                }
            }
            catch (Exception ex) { _status.Text = "读图层失败：" + ex.Message; }
            p.Controls.Add(_lg);
            return p;
        }

        // ------------------------------------------------------------------ 美化规则
        TabPage TidyPage()
        {
            var p = new TabPage("出图美化规则");
            var c = _b.LoadTidy();
            int x = 8, y = 8;
            void SW(string key, string text, bool v) { var cb = new CheckBox { Text = text, Left = x, Top = y, AutoSize = true, Checked = v }; p.Controls.Add(cb); _sw[key] = cb; y += 26; }
            SW("fonts", "补缺字体（修「?」乱码），换成", c.Fonts);
            _shx.Left = 330; _shx.Top = y - 28; _shx.Text = c.ShxFont; _big.Left = 480; _big.Top = y - 28; _big.Text = c.BigFont; p.Controls.Add(_shx); p.Controls.Add(_big);
            SW("linetypes", "虚线 / 点划线按纸面长度重排（只对 Revit 导出的图）", c.Linetypes);
            SW("duplicates", "删重叠的重复直线、零长度线", c.Duplicates);
            SW("darkenpale", "浅色（屏幕亮、白纸上看不清）加深", c.DarkenPale);
            SW("keeptext", "淡显的图层上，文字 / 标高 / 标注保持原色（只淡显线条）", c.KeepText);
            SW("bylayer", "对象颜色 / 线型 / 线宽强制随层（有意的单独颜色会丢，慎用）", c.ByLayer);
            SW("maskmtext", "多行文字加背景遮罩", c.MaskMText);
            SW("purge", "清理没用到的块、图层、样式（PURGE）", c.Purge);
            p.Controls.Add(new Label { Text = "图层规则（从上往下第一条命中的生效；图层名可用 * 通配；颜色写 ACI 号如 8，或 R,G,B；线宽写 mm；打印写 0 = 不打印）：", Left = 8, Top = y + 6, Width = 870, Height = 22 });
            _rg.Left = 8; _rg.Top = y + 30; _rg.Width = 876; _rg.Height = 500 - y - 36;
            _rg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "图层名（通配）", Width = 260 });
            _rg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "颜色", Width = 110 });
            _rg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "线宽 mm", Width = 90 });
            _rg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "线型", Width = 150 });
            _rg.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "打印（1/0）", Width = 90 });
            Fill(c);
            p.Controls.Add(_rg);
            var def = new Button { Text = "恢复默认", Left = 770, Top = 8, Width = 110, Height = 30 };
            def.Click += (_, __) => { var d = _b.DefaultTidy(); Fill(d); foreach (var kv in _sw) kv.Value.Checked = Get(d, kv.Key); _shx.Text = d.ShxFont; _big.Text = d.BigFont; _status.Text = "已恢复默认（点「保存并关闭」生效）"; };
            p.Controls.Add(def);
            return p;
        }

        void Fill(TidyConfig c)
        {
            _rg.Rows.Clear();
            foreach (var r in c.Rules) _rg.Rows.Add(r.Pattern, r.Color, r.Lw, r.Linetype, r.Plot);
        }

        static bool Get(TidyConfig c, string k)
        {
            switch (k)
            {
                case "fonts": return c.Fonts; case "linetypes": return c.Linetypes; case "duplicates": return c.Duplicates; case "darkenpale": return c.DarkenPale;
                case "keeptext": return c.KeepText; case "bylayer": return c.ByLayer; case "maskmtext": return c.MaskMText; default: return c.Purge;
            }
        }

        // ------------------------------------------------------------------ 方案
        TabPage SchemePage()
        {
            var p = new TabPage("方案（导入导出）");
            p.Controls.Add(new Label { Text = "「方案」= 当前图框模板 + 图例中文名 + 美化规则 + 向导里的选择。导出成一个文件发给同事，对方导入后出的图和你一样。", Left = 8, Top = 10, Width = 870, Height = 40 });
            var ex = new Button { Text = "导出方案…", Left = 8, Top = 60, Width = 140, Height = 34 };
            var im = new Button { Text = "导入方案…", Left = 160, Top = 60, Width = 140, Height = 34 };
            var op = new Button { Text = "打开设置文件夹", Left = 312, Top = 60, Width = 160, Height = 34 };
            ex.Click += (_, __) =>
            {
                using (var d = new SaveFileDialog { Filter = "天河落出图方案|*.thlscheme", FileName = (string.IsNullOrEmpty(_tpl) ? "出图方案" : _tpl) + ".thlscheme" })
                    if (d.ShowDialog(this) == DialogResult.OK) Try(() => _status.Text = _b.ExportScheme(d.FileName, _tpl));
            };
            im.Click += (_, __) =>
            {
                using (var d = new OpenFileDialog { Filter = "天河落出图方案|*.thlscheme" })
                    if (d.ShowDialog(this) == DialogResult.OK) Try(() => { _status.Text = _b.ImportScheme(d.FileName); MessageBox.Show(this, _status.Text + "\n重新打开向导后生效。", "导入方案"); });
            };
            op.Click += (_, __) => Try(() => _b.Open(_b.SettingsFolder));
            p.Controls.AddRange(new Control[] { ex, im, op });
            p.Controls.Add(new Label { Text = "设置都存在：" + _b.SettingsFolder + "\n（一般不用去那里改，界面里都能改。）", Left = 8, Top = 110, Width = 870, Height = 44, ForeColor = Color.DimGray });
            return p;
        }

        void Try(Action a)
        {
            try { a(); }
            catch (UserError e) { MessageBox.Show(this, e.Message, "出图设置"); }
            catch (Exception e) { MessageBox.Show(this, "没有完成：" + e.Message, "出图设置"); }
        }

        bool SaveAll()
        {
            try
            {
                _lg.EndEdit(); _rg.EndEdit();
                var rows = _lg.Rows.Cast<DataGridViewRow>().Select(r => new LegendRow { Layer = Convert.ToString(r.Cells[1].Value), Name = Convert.ToString(r.Cells[3].Value) ?? "", Show = Equals(r.Cells[4].Value, true) }).ToList();
                if (rows.Count > 0) _b.SaveLegend(rows);
                var c = new TidyConfig
                {
                    Fonts = _sw["fonts"].Checked, Linetypes = _sw["linetypes"].Checked, Duplicates = _sw["duplicates"].Checked, DarkenPale = _sw["darkenpale"].Checked,
                    KeepText = _sw["keeptext"].Checked, ByLayer = _sw["bylayer"].Checked, MaskMText = _sw["maskmtext"].Checked, Purge = _sw["purge"].Checked,
                    ShxFont = _shx.Text.Trim(), BigFont = _big.Text.Trim(),
                };
                foreach (DataGridViewRow r in _rg.Rows)
                {
                    if (r.IsNewRow) continue;
                    var pat = Convert.ToString(r.Cells[0].Value)?.Trim();
                    if (string.IsNullOrEmpty(pat)) continue;
                    c.Rules.Add(new TidyRule { Pattern = pat, Color = Convert.ToString(r.Cells[1].Value)?.Trim() ?? "", Lw = Convert.ToString(r.Cells[2].Value)?.Trim() ?? "", Linetype = Convert.ToString(r.Cells[3].Value)?.Trim() ?? "", Plot = Convert.ToString(r.Cells[4].Value)?.Trim() ?? "" });
                }
                _b.SaveTidy(c);
                return true;
            }
            catch (Exception e) { MessageBox.Show(this, "保存没成功：" + e.Message, "出图设置"); return false; }
        }

        public void Snap(string path)
        {
            Application.DoEvents();
            using (var bmp = new Bitmap(Width, Height)) { DrawToBitmap(bmp, new Rectangle(0, 0, Width, Height)); bmp.Save(path, System.Drawing.Imaging.ImageFormat.Png); }
        }
    }
}
