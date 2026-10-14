using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Windows.Forms;

namespace KaifaTools.DuctSnap;

public enum DuctSnapRequestKind { Jump, Recheck, Apply }

public sealed record DuctSnapRequest(DuctSnapRequestKind Kind, long Id = 0);

/// <summary>
/// Modeless list of the bank ends near wells. Revit work is only requested here, never done here, so the window
/// never blocks Revit and stays open after a jump, a recheck or an apply.
/// </summary>
public sealed class DuctSnapForm : Form
{
    public const string FilterTodo = "需处理（可修改 + 需人工）";
    public const string FilterReady = "只看可修改";
    public const string FilterManual = "只看需人工";
    public const string FilterFit = "只看已贴合";
    public const string FilterAll = "全部";

    static readonly Color ReadyColour = Color.FromArgb(232, 248, 236);
    static readonly Color ManualColour = Color.FromArgb(255, 238, 214);

    private SnapReport _report;
    private readonly BindingList<SnapRow> _shown = new();
    private readonly DataGridView _grid = new();
    private readonly Label _summary = new();
    private readonly Label _notes = new();
    private readonly ComboBox _filter = new();
    private readonly CheckBox _pin = new();

    public Action<DuctSnapRequest>? Requested { get; set; }

    public DuctSnapForm(SnapReport report)
    {
        _report = report;
        Text = "排管贴井内壁";
        Font = new Font("Microsoft YaHei UI", 9F);
        StartPosition = FormStartPosition.CenterScreen;
        Size = new Size(1280, 680);
        MinimumSize = new Size(820, 480);

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
        _filter.Items.AddRange(new object[] { FilterTodo, FilterReady, FilterManual, FilterFit, FilterAll });
        _filter.SelectedIndex = 0;
        _filter.Width = 240;
        _filter.SelectedIndexChanged += (_, _) => RefreshShown();
        top.Controls.Add(_filter);
        _pin.AutoSize = true;
        _pin.Text = "锁定在最前";
        _pin.Margin = new Padding(24, 4, 0, 0);
        _pin.CheckedChanged += (_, _) => TopMost = _pin.Checked;
        top.Controls.Add(_pin);
        root.Controls.Add(top, 0, 0);

        _summary.AutoSize = true;
        _summary.MaximumSize = new Size(1240, 0);
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
        void Col(string header, string prop, int width, bool fill = false)
        {
            _grid.Columns.Add(new DataGridViewTextBoxColumn
            {
                Name = prop, HeaderText = header, DataPropertyName = prop, Width = width,
                AutoSizeMode = fill ? DataGridViewAutoSizeColumnMode.Fill : DataGridViewAutoSizeColumnMode.NotSet,
            });
        }
        Col("状态", nameof(SnapRow.Status), 80);
        Col("动作", nameof(SnapRow.Action), 150);
        Col("移动(mm)", nameof(SnapRow.Move), 80);
        Col("排管ID", nameof(SnapRow.BankId), 90);
        Col("排管族", nameof(SnapRow.BankFamily), 230);
        Col("端点", nameof(SnapRow.End), 60);
        Col("井族", nameof(SnapRow.WellFamily), 180);
        Col("说明", nameof(SnapRow.Note), 320, fill: true);
        _grid.Columns[nameof(SnapRow.Note)]!.DefaultCellStyle.WrapMode = DataGridViewTriState.True;
        _grid.AutoSizeRowsMode = DataGridViewAutoSizeRowsMode.DisplayedCellsExceptHeaders;
        root.Controls.Add(_grid, 0, 2);

        _notes.AutoSize = true;
        _notes.MaximumSize = new Size(1240, 0);
        _notes.Padding = new Padding(10, 2, 10, 2);
        _notes.ForeColor = Color.FromArgb(70, 70, 70);
        root.Controls.Add(_notes, 0, 3);

        var buttons = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, Padding = new Padding(8, 4, 8, 8) };
        buttons.Controls.Add(Btn("重新检查", () => Requested?.Invoke(new DuctSnapRequest(DuctSnapRequestKind.Recheck))));
        buttons.Controls.Add(Btn("执行贴合…", ApplyWithConfirm));
        buttons.Controls.Add(Btn("定位到选中的排管", Jump));
        buttons.Controls.Add(Btn("导出 CSV", ExportCsv));
        buttons.Controls.Add(Btn("打开规则文件", OpenRules));
        buttons.Controls.Add(Btn("关闭", Close));
        root.Controls.Add(buttons, 0, 4);

        ShowReport(report, null);
    }

    /// <summary>Called by the command after a recheck or an apply: show the new result and keep the window open.</summary>
    public void Update(SnapReport report, string? message = null)
    {
        _report = report;
        ShowReport(report, message);
    }

    private void ShowReport(SnapReport report, string? message)
    {
        _summary.Text = report.Summary + (message is null ? "" : "\n" + message);
        var notes = new List<string>();
        if (report.RulesError is not null) notes.Add("⚠ " + report.RulesError);
        notes.AddRange(report.Notes);
        notes.Add("规则文件：" + DuctSnapRules.FilePath + "（改完点“重新检查”生效）");
        _notes.Text = string.Join("\n", notes);
        RefreshShown();
    }

    /// <summary>Shows a one-line message above the notes (used for errors and for requests that could not run).</summary>
    public void ShowMessage(string text)
    {
        _notes.Text = text + "\n" + _notes.Text;
    }

    private static Button Btn(string text, Action onClick)
    {
        var b = new Button { Text = text, AutoSize = true, Padding = new Padding(10, 4, 10, 4), Margin = new Padding(0, 0, 10, 0) };
        b.Click += (_, _) => onClick();
        return b;
    }

    private void RefreshShown()
    {
        string f = _filter.SelectedItem as string ?? FilterTodo;
        IEnumerable<SnapRow> q = f switch
        {
            FilterReady => _report.Rows.Where(r => r.Status == "可修改"),
            FilterManual => _report.Rows.Where(r => r.Status == "需人工"),
            FilterFit => _report.Rows.Where(r => r.Status == "已贴合"),
            FilterAll => _report.Rows,
            _ => _report.Rows.Where(r => r.Status != "已贴合"),
        };
        var list = q.ToList();
        _shown.RaiseListChangedEvents = false;
        _shown.Clear();
        foreach (var r in list) _shown.Add(r);
        _shown.RaiseListChangedEvents = true;
        _shown.ResetBindings();
    }

    private void ColourRows()
    {
        foreach (DataGridViewRow r in _grid.Rows)
        {
            if (r.DataBoundItem is not SnapRow row) continue;
            r.DefaultCellStyle.BackColor = row.Status switch
            {
                "可修改" => ReadyColour,
                "需人工" => ManualColour,
                _ => Color.White,
            };
        }
    }

    private void Jump()
    {
        if (_grid.CurrentRow?.DataBoundItem is not SnapRow row)
        {
            _notes.Text = "先在表格里点一行（排管）。\n" + _notes.Text;
            return;
        }
        Requested?.Invoke(new DuctSnapRequest(DuctSnapRequestKind.Jump, row.BankId));
    }

    private void ApplyWithConfirm()
    {
        if (_report.Edits.Count == 0)
        {
            _notes.Text = "当前没有可以自动修改的端点。\n" + _notes.Text;
            return;
        }
        string text = $"将修改 {_report.EndsChanged} 个端点（{_report.Edits.Count} 根排管）。\n"
            + "只改平面位置，端点标高保持不变。需人工的端点不会被改动。\n"
            + "一次操作，可用 Ctrl+Z 撤销。\n\n继续吗？";
        var r = MessageBox.Show(this, text, "排管贴井内壁", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
        if (r == DialogResult.Yes) Requested?.Invoke(new DuctSnapRequest(DuctSnapRequestKind.Apply));
    }

    private void ExportCsv()
    {
        try
        {
            string path = DuctSnapCsv.Write(_report.Rows);
            _notes.Text = "已导出：" + path + "\n" + _notes.Text;
        }
        catch (Exception ex)
        {
            _notes.Text = "导出失败：" + ex.Message + "\n" + _notes.Text;
        }
    }

    private void OpenRules()
    {
        try
        {
            DuctSnapRules.Load(out _); // creates the file with defaults when missing
            Process.Start(new ProcessStartInfo(DuctSnapRules.FilePath) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            _notes.Text = "打开失败：" + ex.Message + "\n" + _notes.Text;
        }
    }
}
