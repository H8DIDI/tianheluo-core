using System.Drawing;
using System.Windows.Forms;

namespace KaifaTools.PlanBatch;

/// <summary>Two lists with checkboxes: plan view types on the left, levels on the right. Returns the ticked items.</summary>
public sealed class PlanPickerForm : Form
{
    private readonly CheckedListBox _types = new();
    private readonly CheckedListBox _levels = new();
    private readonly Label _count = new();

    public IReadOnlyList<string> ViewTypeNames { get; }
    public IReadOnlyList<string> LevelNames { get; }

    /// <summary>Indexes of the ticked rows, valid after the user clicks 确认.</summary>
    public List<int> TypeIndexes { get; } = new();
    public List<int> LevelIndexes { get; } = new();

    public PlanPickerForm(IReadOnlyList<string> viewTypes, IReadOnlyList<string> levels)
    {
        ViewTypeNames = viewTypes;
        LevelNames = levels;
        Text = "批量生成平面图";
        Font = new Font("Microsoft YaHei UI", 9F);
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        ClientSize = new Size(840, 560);
        BackColor = Color.White;

        var left = Panel("楼层平面类型", "为新建的平面视图选择一个或者多个平面视图类型。", _types, viewTypes, 20);
        var right = Panel("标高", "为新建的平面视图选择一个或者多个标高。", _levels, levels, 440);
        Controls.Add(left);
        Controls.Add(right);

        _count.AutoSize = true;
        _count.Location = new Point(20, 518);
        _count.ForeColor = Color.FromArgb(70, 70, 70);
        Controls.Add(_count);

        var ok = new Button { Text = "确认", Size = new Size(140, 36), Location = new Point(520, 508), BackColor = Color.FromArgb(47, 140, 255), ForeColor = Color.White, FlatStyle = FlatStyle.Flat };
        ok.Click += (_, _) => Confirm();
        var cancel = new Button { Text = "取消", Size = new Size(140, 36), Location = new Point(676, 508), FlatStyle = FlatStyle.Flat };
        cancel.Click += (_, _) => { DialogResult = DialogResult.Cancel; Close(); };
        Controls.Add(ok);
        Controls.Add(cancel);
        AcceptButton = ok;
        CancelButton = cancel;

        _types.ItemCheck += (_, _) => BeginInvoke(new Action(UpdateCount));
        _levels.ItemCheck += (_, _) => BeginInvoke(new Action(UpdateCount));
        UpdateCount();
    }

    private GroupBox Panel(string title, string hint, CheckedListBox list, IReadOnlyList<string> items, int x)
    {
        var box = new GroupBox { Text = title, Location = new Point(x, 12), Size = new Size(400, 488), BackColor = Color.White };
        box.Controls.Add(new Label { Text = hint, Location = new Point(14, 26), Size = new Size(370, 36), ForeColor = Color.FromArgb(90, 90, 90) });

        list.Location = new Point(14, 66);
        list.Size = new Size(370, 330);
        list.CheckOnClick = true;
        list.IntegralHeight = false;
        list.BorderStyle = BorderStyle.FixedSingle;
        for (int i = 0; i < items.Count; i++) list.Items.Add(items[i]);
        box.Controls.Add(list);

        var all = new Button { Text = "全选", Location = new Point(14, 410), Size = new Size(90, 30), FlatStyle = FlatStyle.Flat };
        all.Click += (_, _) => SetAll(list, true);
        var none = new Button { Text = "全不选", Location = new Point(112, 410), Size = new Size(90, 30), FlatStyle = FlatStyle.Flat };
        none.Click += (_, _) => SetAll(list, false);
        box.Controls.Add(all);
        box.Controls.Add(none);
        return box;
    }

    private static void SetAll(CheckedListBox list, bool on)
    {
        for (int i = 0; i < list.Items.Count; i++) list.SetItemChecked(i, on);
    }

    private void UpdateCount()
    {
        int t = _types.CheckedItems.Count, l = _levels.CheckedItems.Count;
        _count.Text = $"{t} 种类型 × {l} 个标高 = 生成 {t * l} 个视图";
    }

    private void Confirm()
    {
        if (_types.CheckedIndices.Count == 0 || _levels.CheckedIndices.Count == 0)
        {
            MessageBox.Show(this, "请在左右两边各至少选择一项。", "批量生成平面图", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        TypeIndexes.Clear();
        TypeIndexes.AddRange(_types.CheckedIndices.Cast<int>());
        LevelIndexes.Clear();
        LevelIndexes.AddRange(_levels.CheckedIndices.Cast<int>());
        DialogResult = DialogResult.OK;
        Close();
    }
}
