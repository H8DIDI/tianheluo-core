using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Windows.Forms;
using KaifaTools.TagCheck;

namespace KaifaTools.TagCheck;

public enum TagRequestKind { Jump, Recheck }

public sealed record TagRequest(TagRequestKind Kind, long Id = 0);

/// <summary>Modeless window: lists missing / wrong tags. Revit work is requested, never done here.</summary>
public sealed class TagCheckForm : Form
{
    private List<TagRow> _all;
    private readonly BindingList<TagRow> _shown = new();
    private readonly DataGridView _grid = new();
    private readonly Label _summary = new();
    private readonly ComboBox _filter = new();
    private readonly CheckBox _pin = new();
    private string _summaryText;

    public Action<TagRequest>? Requested { get; set; }

    public TagCheckForm(List<TagRow> rows, string summary, string? standardError)
    {
        _all = rows;
        _summaryText = summary;
        Text = "标注检查";
        Font = new Font("Microsoft YaHei UI", 9F);
        StartPosition = FormStartPosition.CenterScreen;
        Size = new Size(1100, 680);
        MinimumSize = new Size(800, 480);

        var root = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 5, ColumnCount = 1 };
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        Controls.Add(root);

        var top = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, Padding = new Padding(8, 6, 8, 2) };
        top.Controls.Add(new Label { AutoSize = true, Text = "显示：", Padding = new Padding(0, 6, 0, 0) });
        _filter.DropDownStyle = ComboBoxStyle.DropDownList;
        _filter.Items.AddRange(new object[] { "需处理（漏标+用错）", "只看漏标", "只看用错", "全部", "只看已标" });
        _filter.SelectedIndex = 0;
        _filter.Width = 170;
        _filter.SelectedIndexChanged += (_, _) => RefreshShown();
        top.Controls.Add(_filter);
        _pin.AutoSize = true;
        _pin.Text = "锁定在最前";
        _pin.Margin = new Padding(24, 4, 0, 0);
        _pin.CheckedChanged += (_, _) => TopMost = _pin.Checked;
        top.Controls.Add(_pin);
        root.Controls.Add(top, 0, 0);

        _summary.AutoSize = true;
        _summary.Padding = new Padding(10, 4, 10, 4);
        root.Controls.Add(_summary, 0, 1);

        _grid.Dock = DockStyle.Fill;
        _grid.AutoGenerateColumns = false;
        _grid.AllowUserToAddRows = false;
        _grid.AllowUserToDeleteRows = false;
        _grid.ReadOnly = true;
        _grid.RowHeadersVisible = false;
        _grid.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
        _grid.MultiSelect = false;
        _grid.BackgroundColor = Color.White;
        _grid.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.AutoSize;
        _grid.DataSource = _shown;
        _grid.CellDoubleClick += (_, e) => { if (e.RowIndex >= 0) Jump(); };
        _grid.DataBindingComplete += (_, _) => ColourRows();
        void Col(string header, string prop, int width)
        {
            _grid.Columns.Add(new DataGridViewTextBoxColumn { Name = prop, HeaderText = header, DataPropertyName = prop, Width = width });
        }
        Col("状态", nameof(TagRow.Status), 70);
        Col("标记", nameof(TagRow.Mark), 80);
        Col("类别", nameof(TagRow.Category), 90);
        Col("族", nameof(TagRow.Family), 220);
        Col("当前标签", nameof(TagRow.Current), 260);
        Col("应为标签", nameof(TagRow.Expected), 260);
        root.Controls.Add(_grid, 0, 2);

        var notes = new Label
        {
            AutoSize = true, Padding = new Padding(10, 2, 10, 2), ForeColor = Color.FromArgb(70, 70, 70),
            Text = standardError != null
                ? "⚠ " + standardError
                : "标准文件：" + TagCheckEngine.StandardPath + "（在里面写每类构件允许的标记族，改完点“重新检查”）",
        };
        root.Controls.Add(notes, 0, 3);

        var buttons = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, Padding = new Padding(8, 4, 8, 8) };
        buttons.Controls.Add(Btn("定位到选中的构件", () => Jump()));
        buttons.Controls.Add(Btn("重新检查", () => Requested?.Invoke(new TagRequest(TagRequestKind.Recheck))));
        buttons.Controls.Add(Btn("打开标准文件", OpenStandard));
        buttons.Controls.Add(Btn("导出 CSV", ExportCsv));
        buttons.Controls.Add(Btn("关闭", Close));
        root.Controls.Add(buttons, 0, 4);
        RefreshShown();
    }

    /// <summary>Called by the command after a recheck: show the new result, keep the window open.</summary>
    public void Update(List<TagRow> rows, string summary, string? standardError)
    {
        _all = rows;
        _summaryText = summary;
        RefreshShown();
    }

    private static Button Btn(string text, Action onClick)
    {
        var b = new Button { Text = text, AutoSize = true, Padding = new Padding(10, 4, 10, 4), Margin = new Padding(0, 0, 10, 0) };
        b.Click += (_, _) => onClick();
        return b;
    }

    private void RefreshShown()
    {
        string f = _filter.SelectedItem as string ?? "";
        IEnumerable<TagRow> q = _all;
        q = f switch
        {
            "只看漏标" => q.Where(r => r.Status == "漏标"),
            "只看用错" => q.Where(r => r.Status == "用错"),
            "只看已标" => q.Where(r => r.Status == "已标"),
            "全部" => q,
            _ => q.Where(r => r.Status != "已标"),
        };
        _shown.RaiseListChangedEvents = false;
        _shown.Clear();
        foreach (var r in q) _shown.Add(r);
        _shown.RaiseListChangedEvents = true;
        _shown.ResetBindings();
        _summary.Text = _summaryText;
    }

    private void ColourRows()
    {
        foreach (DataGridViewRow r in _grid.Rows)
        {
            if (r.DataBoundItem is not TagRow row) continue;
            r.DefaultCellStyle.BackColor = row.Status switch
            {
                "漏标" => Color.FromArgb(255, 235, 230),
                "用错" => Color.FromArgb(255, 243, 205),
                _ => Color.White,
            };
        }
    }

    private void Jump()
    {
        if (_grid.CurrentRow?.DataBoundItem is not TagRow row) { MessageBox.Show(this, "先在表格里点一个构件。", "标注检查"); return; }
        Requested?.Invoke(new TagRequest(TagRequestKind.Jump, row.ElementId));
    }

    private void OpenStandard()
    {
        // make sure the file exists, then open it in the default editor (Notepad for .json)
        TagCheckEngine.LoadStandard(out _);
        try { Process.Start(new ProcessStartInfo(TagCheckEngine.StandardPath) { UseShellExecute = true }); }
        catch (Exception ex) { MessageBox.Show(this, "打开失败：" + ex.Message, "标注检查"); }
    }

    private void ExportCsv()
    {
        string path = TagCheckEngine.ExportCsv(_all);
        MessageBox.Show(this, "已导出：\n" + path, "标注检查");
    }
}
