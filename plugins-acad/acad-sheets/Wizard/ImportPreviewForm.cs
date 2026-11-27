// Excel 改图签：导入前的「旧 → 新」预览（勾选要写入的改动）。只依赖 WinForms，图纸列表窗口和测试共用。
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Windows.Forms;

namespace Thl.Acad.Sheets.Wizard
{
    public sealed class ImportPreviewForm : Form
    {
        readonly ListView _lv = new ListView { View = View.Details, FullRowSelect = true, CheckBoxes = true, GridLines = true };

        /// <param name="rows">每行：位置（布局名 / 块句柄）、字段、旧值、新值</param>
        public ImportPreviewForm(IList<(string where, string field, string oldV, string newV)> rows)
        {
            Text = $"导入预览：{rows.Count} 处改动";
            Font = new Font("Microsoft YaHei UI", 9.5f);
            AutoScaleMode = AutoScaleMode.None;
            ClientSize = new Size(780, 540); FormBorderStyle = FormBorderStyle.FixedDialog; MaximizeBox = MinimizeBox = false;
            StartPosition = FormStartPosition.CenterParent; ShowInTaskbar = false;
            Controls.Add(new Label { Text = "确认后会先把原文件（最近一次保存的版本）复制成「原名.THL备份-时间.dwg」，再写入勾选的改动：", Left = 12, Top = 10, Width = 756, Height = 24, ForeColor = Color.DimGray });
            _lv.Left = 12; _lv.Top = 40; _lv.Width = 756; _lv.Height = 440;
            _lv.Columns.Add("位置", 230); _lv.Columns.Add("字段", 100); _lv.Columns.Add("旧", 200); _lv.Columns.Add("新", 200);
            foreach (var r in rows) _lv.Items.Add(new ListViewItem(new[] { r.where, r.field, r.oldV.Replace("\n", " "), r.newV.Replace("\n", " ") }) { Checked = true });
            Controls.Add(_lv);
            var all = new Button { Text = "全选 / 全不选", Left = 12, Top = 494, Width = 130, Height = 32 };
            all.Click += (_, __) => { bool on = _lv.Items.Cast<ListViewItem>().Any(i => !i.Checked); foreach (ListViewItem i in _lv.Items) i.Checked = on; };
            var ok = new Button { Text = "写入勾选的改动", Left = 540, Top = 494, Width = 130, Height = 32, DialogResult = DialogResult.OK };
            var cancel = new Button { Text = "取消", Left = 678, Top = 494, Width = 90, Height = 32, DialogResult = DialogResult.Cancel };
            Controls.AddRange(new Control[] { all, ok, cancel }); AcceptButton = ok; CancelButton = cancel;
            WizardForm.FitDpi(this);
        }

        /// <summary>勾选的行号（对应构造时传入的顺序）。</summary>
        public List<int> Selected => _lv.Items.Cast<ListViewItem>().Where(i => i.Checked).Select(i => i.Index).ToList();

        public void Snap(string path)
        {
            Application.DoEvents();
            using (var bmp = new Bitmap(Width, Height)) { DrawToBitmap(bmp, new Rectangle(0, 0, Width, Height)); bmp.Save(path, System.Drawing.Imaging.ImageFormat.Png); }
        }
    }
}
