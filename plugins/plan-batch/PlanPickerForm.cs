using System.Drawing;
using System.Windows.Forms;

namespace KaifaTools.PlanBatch;

/// <summary>
/// Picker window: plan view templates on the left (with a search box), levels on the right,
/// a naming pattern on top and a live summary at the bottom. The command saves the returned selection
/// through <see cref="PickerMemoryStore"/>. No Revit references here, so the UI smoke test can build this file alone.
/// </summary>
public sealed class PlanPickerForm : Form
{
    /// <summary>Orange used for the warnings in the summary.</summary>
    public static readonly Color WarningColor = Color.FromArgb(214, 104, 0);

    private static readonly Color TextColor = Color.FromArgb(70, 70, 70);
    private static readonly Color HintColor = Color.FromArgb(110, 110, 110);
    private const int Pad = 20;
    private const int Gap = 16;
    private const int ButtonWidth = 140;

    private readonly HashSet<string> _existing;
    private readonly ToolTip _tips = new();
    private readonly PickerColumn _types;
    private readonly PickerColumn _levels;
    private readonly Label _patternLabel = new() { Text = "命名规则", AutoSize = true, ForeColor = TextColor };
    private readonly TextBox _pattern = new() { Name = "pattern", MaxLength = 120 };
    private readonly Label _patternHint = new() { Text = "{标高} 必填；{样板} 可选。不套用样板时，{样板} 显示为“平面”。", ForeColor = HintColor };
    private readonly Label _line1 = StatusLine("statusLine1");
    private readonly Label _line2 = StatusLine("statusLine2");
    private readonly Label _line3 = StatusLine("statusLine3");
    private readonly Button _ok = new() { Name = "ok", Text = "确认", Size = new Size(ButtonWidth, 36), BackColor = Color.FromArgb(47, 140, 255), ForeColor = Color.White, FlatStyle = FlatStyle.Flat };
    private readonly Button _cancel = new() { Name = "cancel", Text = "取消", Size = new Size(ButtonWidth, 36), FlatStyle = FlatStyle.Flat };

    public IReadOnlyList<string> ViewTypeNames { get; }
    public IReadOnlyList<string> LevelNames { get; }

    /// <summary>Indexes (into ViewTypeNames) of the ticked rows, valid after 确认.</summary>
    public List<int> TypeIndexes { get; } = new();

    /// <summary>Indexes (into LevelNames) of the ticked rows, valid after 确认.</summary>
    public List<int> LevelIndexes { get; } = new();

    /// <summary>Naming pattern chosen by the user, valid after 确认.</summary>
    public string NamePattern { get; private set; } = PlanNaming.DefaultPattern;

    /// <param name="viewTypes">First item should be <see cref="PlanNaming.NoTemplateLabel"/>, then one row per model template.</param>
    /// <param name="levels">Level names in display order.</param>
    /// <param name="existingViewNames">Names of the views already in the model. Planned names among them are counted as skipped.</param>
    /// <param name="remembered">Last selection to restore. Names that are not in the lists are ignored. Null = nothing ticked.</param>
    public PlanPickerForm(IReadOnlyList<string> viewTypes, IReadOnlyList<string> levels,
        IEnumerable<string>? existingViewNames = null, PickerMemory? remembered = null)
    {
        ViewTypeNames = viewTypes;
        LevelNames = levels;
        _existing = new HashSet<string>(existingViewNames ?? Array.Empty<string>(), StringComparer.OrdinalIgnoreCase);
        _types = new PickerColumn("types", "楼层平面类型", "选择平面视图类型（可多选）", viewTypes,
            remembered?.Templates ?? Array.Empty<string>(), searchable: true, _tips);
        _levels = new PickerColumn("levels", "标高", "选择标高（可多选）", levels,
            remembered?.Levels ?? Array.Empty<string>(), searchable: false, _tips);

        Text = "批量生成平面图";
        Font = new Font("Microsoft YaHei UI", 9F);
        StartPosition = FormStartPosition.CenterScreen;
        AutoScaleMode = AutoScaleMode.None;
        FormBorderStyle = FormBorderStyle.Sizable;
        MaximizeBox = true;
        MinimizeBox = false;
        BackColor = Color.White;
        ClientSize = new Size(840, 560);
        MinimumSize = SizeFromClientSize(new Size(760, 520));

        // a remembered pattern that is no longer valid falls back to the default
        string saved = remembered?.NamePattern.Trim() ?? "";
        _pattern.Text = PlanNaming.Validate(saved) is null ? saved : PlanNaming.DefaultPattern;

        Controls.Add(_patternLabel);
        Controls.Add(_pattern);
        Controls.Add(_patternHint);
        Controls.Add(_types.Box);
        Controls.Add(_levels.Box);
        Controls.Add(_line1);
        Controls.Add(_line2);
        Controls.Add(_line3);
        Controls.Add(_ok);
        Controls.Add(_cancel);
        AcceptButton = _ok;
        CancelButton = _cancel;

        _ok.Click += (_, _) => Confirm();
        _cancel.Click += (_, _) => { DialogResult = DialogResult.Cancel; Close(); };
        _types.Changed += (_, _) => UpdateStatus();
        _levels.Changed += (_, _) => UpdateStatus();
        _pattern.TextChanged += (_, _) => UpdateStatus();
        ClientSizeChanged += (_, _) => LayoutAll();

        LayoutAll();
        UpdateStatus();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) _tips.Dispose();
        base.Dispose(disposing);
    }

    // Ctrl+F jumps to the template search box
    protected override bool ProcessCmdKey(ref Message msg, Keys keyData)
    {
        if (keyData == (Keys.Control | Keys.F) && _types.Search is { } search)
        {
            search.Focus();
            search.SelectAll();
            return true;
        }
        return base.ProcessCmdKey(ref msg, keyData);
    }

    // while typing in the search box: Enter moves to the list, Esc clears the search instead of closing the window
    protected override bool ProcessDialogKey(Keys keyData)
    {
        if (_types.Search is { Focused: true } search)
        {
            if (keyData == Keys.Enter) { _types.FocusList(); return true; }
            if (keyData == Keys.Escape && search.TextLength > 0) { search.Clear(); return true; }
        }
        return base.ProcessDialogKey(keyData);
    }

    private void LayoutAll()
    {
        int w = ClientSize.Width, h = ClientSize.Height;
        int fieldLeft = Pad + 74;

        _patternLabel.Location = new Point(Pad, 16);
        _pattern.Bounds = new Rectangle(fieldLeft, 12, w - fieldLeft - Pad, 24);
        _patternHint.Bounds = new Rectangle(fieldLeft, 40, w - fieldLeft - Pad, 20);

        // the two columns fill the space between the name row and the summary
        int top = 66, bottom = h - 88;
        int colWidth = (w - Pad * 2 - Gap) / 2;
        _types.Place(new Rectangle(Pad, top, colWidth, bottom - top));
        _levels.Place(new Rectangle(Pad + colWidth + Gap, top, colWidth, bottom - top));

        // buttons bottom right, the three summary lines bottom left
        _cancel.Location = new Point(w - Pad - ButtonWidth, h - 48);
        _ok.Location = new Point(_cancel.Left - Gap - ButtonWidth, h - 48);
        int lineWidth = _ok.Left - Gap - Pad;
        _line1.Bounds = new Rectangle(Pad, h - 78, lineWidth, 22);
        _line2.Bounds = new Rectangle(Pad, h - 55, lineWidth, 22);
        _line3.Bounds = new Rectangle(Pad, h - 32, lineWidth, 22);
    }

    /// <summary>Summary under the lists: view count, existing names, example name, warnings in orange.</summary>
    private void UpdateStatus()
    {
        int types = _types.TickedCount, levels = _levels.TickedCount;
        string pattern = _pattern.Text.Trim();
        string? error = PlanNaming.Validate(pattern);
        bool picked = types > 0 && levels > 0;

        // distinct planned names in list order (level by level, template by template)
        var planned = new List<string>();
        int pairs = 0;
        if (picked && error is null)
        {
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (int li in _levels.TickedIndexes)
            foreach (int ti in _types.TickedIndexes)
            {
                pairs++;
                string name = PlanNaming.Build(pattern, LevelNames[li], TemplateNameAt(ti));
                if (seen.Add(name)) planned.Add(name);
            }
        }
        int total = planned.Count;
        int exists = planned.Count(n => _existing.Contains(n));

        // line 1: how many views
        SetLine(_line1, total == 0 ? "计划生成 0 个视图" : $"计划生成 {total} 个视图（{types} 种类型 × {levels} 个标高）", total == 0 || total > 100);

        // line 2: what happens to them, or what is wrong
        if (!picked) SetLine(_line2, "请在左右两边各至少选择一项", true);
        else if (error is not null) SetLine(_line2, error, true);
        else if (exists > 0) SetLine(_line2, $"同名已存在 {exists} 个，将跳过；实际新建 {total - exists} 个", false);
        else SetLine(_line2, "没有同名视图，将全部新建", false);

        // line 3: example of the first name, or a duplicate-name warning
        if (error is not null) SetLine(_line3, "", false);
        else if (total == 0) SetLine(_line3, "示例：—", false);
        else if (total < pairs) SetLine(_line3, $"示例：{planned[0]}（有 {pairs - total} 个名称重复，只生成一个）", true);
        else SetLine(_line3, $"示例：{planned[0]}", false);
    }

    private static void SetLine(Label label, string text, bool warn)
    {
        label.Text = text;
        label.ForeColor = warn ? WarningColor : TextColor;
    }

    /// <summary>Template for a row of the type list. The 不套用样板 row gives null (plain plan view).</summary>
    private string? TemplateNameAt(int row)
        => ViewTypeNames[row] == PlanNaming.NoTemplateLabel ? null : ViewTypeNames[row];

    private void Confirm()
    {
        string pattern = _pattern.Text.Trim();
        string? error = PlanNaming.Validate(pattern);
        if (error is not null)
        {
            MessageBox.Show(this, error, "批量生成平面图", MessageBoxButtons.OK, MessageBoxIcon.Information);
            _pattern.Focus();
            return;
        }
        if (_types.TickedCount == 0 || _levels.TickedCount == 0)
        {
            MessageBox.Show(this, "请在左右两边各至少选择一项。", "批量生成平面图", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        TypeIndexes.Clear();
        TypeIndexes.AddRange(_types.TickedIndexes);
        LevelIndexes.Clear();
        LevelIndexes.AddRange(_levels.TickedIndexes);
        NamePattern = pattern;
        DialogResult = DialogResult.OK;
        Close();
    }

    private static Label StatusLine(string name)
        => new() { Name = name, AutoSize = false, AutoEllipsis = true, ForeColor = TextColor };

    /// <summary>
    /// One list with its title, a summary, an optional search box and 全选 / 全不选.
    /// The ticks of every item are kept in <see cref="_ticked"/>, so filtering the list never loses a tick.
    /// 全选 / 全不选 only change the rows that are shown.
    /// </summary>
    private sealed class PickerColumn
    {
        private readonly string[] _names;
        private readonly bool[] _ticked;
        private readonly List<int> _shown = new();      // list row -> index into _names
        private readonly Label _hint = new() { ForeColor = HintColor };
        private readonly Label _summary = new() { TextAlign = ContentAlignment.MiddleRight, ForeColor = TextColor };
        private readonly Label _searchLabel = new() { Text = "搜索", TextAlign = ContentAlignment.MiddleLeft, ForeColor = HintColor };
        private readonly Label _searchCount = new() { TextAlign = ContentAlignment.MiddleRight, ForeColor = HintColor };
        private readonly CheckedListBox _list = new() { CheckOnClick = true, IntegralHeight = false, BorderStyle = BorderStyle.FixedSingle };
        private readonly Button _all = new() { Text = "全选", Size = new Size(90, 30), FlatStyle = FlatStyle.Flat };
        private readonly Button _none = new() { Text = "全不选", Size = new Size(90, 30), FlatStyle = FlatStyle.Flat };
        private TextBox? _search;
        private bool _syncing;
        private string _filter = "";

        public PickerColumn(string key, string title, string hint, IReadOnlyList<string> names,
            IEnumerable<string> ticked, bool searchable, ToolTip tips)
        {
            _names = names.ToArray();
            var remembered = new HashSet<string>(ticked, StringComparer.OrdinalIgnoreCase);
            _ticked = _names.Select(n => remembered.Contains(n)).ToArray();

            Box.Name = key + "Box";
            Box.Text = title;
            Box.BackColor = Color.White;
            _hint.Text = hint;
            _hint.Name = key + "Hint";
            _summary.Name = key + "Summary";
            _searchCount.Name = key + "SearchCount";
            _list.Name = key + "List";
            _all.Name = key + "All";
            _none.Name = key + "None";

            _list.ItemCheck += OnItemCheck;
            _list.KeyDown += OnListKeyDown;
            _all.Click += (_, _) => SetShownTicks(true);
            _none.Click += (_, _) => SetShownTicks(false);
            tips.SetToolTip(_all, "只作用于列表里当前显示的项");
            tips.SetToolTip(_none, "只作用于列表里当前显示的项");

            Box.Controls.Add(_hint);
            Box.Controls.Add(_summary);
            if (searchable)
            {
                var search = new TextBox { Name = key + "Search" };
                search.TextChanged += (_, _) => ApplyFilter(search.Text);
                search.KeyDown += (_, e) =>
                {
                    if (e.KeyCode != Keys.Down) return;
                    FocusList();
                    e.Handled = true;
                    e.SuppressKeyPress = true;
                };
                _search = search;
                _searchLabel.Name = key + "SearchLabel";
                Box.Controls.Add(_searchLabel);
                Box.Controls.Add(search);
                Box.Controls.Add(_searchCount);
            }
            Box.Controls.Add(_list);
            Box.Controls.Add(_all);
            Box.Controls.Add(_none);
            Rebuild();
        }

        public GroupBox Box { get; } = new();

        public TextBox? Search => _search;

        /// <summary>Raised when the ticks change (not when the filter changes).</summary>
        public event EventHandler? Changed;

        public int TickedCount => _ticked.Count(t => t);

        /// <summary>Indexes into the names passed to the constructor, in list order.</summary>
        public List<int> TickedIndexes => Enumerable.Range(0, _names.Length).Where(i => _ticked[i]).ToList();

        public void Place(Rectangle bounds)
        {
            Box.Bounds = bounds;
            int inner = bounds.Width - 28;      // 14 px padding on each side
            _hint.Bounds = new Rectangle(14, 26, inner - 130, 22);
            _summary.Bounds = new Rectangle(14 + inner - 130, 26, 130, 22);
            if (_search is not null)
            {
                _searchLabel.Bounds = new Rectangle(14, 52, 40, 24);
                _search.Bounds = new Rectangle(54, 52, inner - 54 - 96, 24);
                _searchCount.Bounds = new Rectangle(14 + inner - 92, 52, 92, 24);
            }
            _list.Bounds = new Rectangle(14, 84, inner, bounds.Height - 84 - 52);
            _all.Location = new Point(14, bounds.Height - 44);
            _none.Location = new Point(112, bounds.Height - 44);
        }

        public void FocusList()
        {
            if (_list.Items.Count > 0 && _list.SelectedIndex < 0) _list.SelectedIndex = 0;
            _list.Focus();
        }

        private void ApplyFilter(string text)
        {
            _filter = text.Trim();
            Rebuild();
        }

        /// <summary>Refills the list with the rows that match the filter, each with its kept tick.</summary>
        private void Rebuild()
        {
            _syncing = true;
            _list.BeginUpdate();
            _list.Items.Clear();
            _shown.Clear();
            for (int i = 0; i < _names.Length; i++)
            {
                if (_filter.Length > 0 && !_names[i].Contains(_filter, StringComparison.OrdinalIgnoreCase)) continue;
                _shown.Add(i);
                _list.Items.Add(_names[i], _ticked[i]);
            }
            _list.EndUpdate();
            _syncing = false;
            UpdateLabels();
        }

        private void UpdateLabels()
        {
            _summary.Text = $"已选 {TickedCount} / {_names.Length}";
            _searchCount.Text = _filter.Length > 0 ? $"匹配 {_shown.Count} 项" : "";
        }

        private void OnItemCheck(object? sender, ItemCheckEventArgs e)
        {
            if (_syncing || e.Index < 0 || e.Index >= _shown.Count) return;
            _ticked[_shown[e.Index]] = e.NewValue == CheckState.Checked;
            UpdateLabels();
            Changed?.Invoke(this, EventArgs.Empty);
        }

        // Space toggles the highlighted row, the same as clicking its box
        private void OnListKeyDown(object? sender, KeyEventArgs e)
        {
            if (e.KeyCode != Keys.Space || _list.SelectedIndex < 0) return;
            int row = _list.SelectedIndex;
            _list.SetItemChecked(row, !_list.GetItemChecked(row));
            e.Handled = true;
            e.SuppressKeyPress = true;
        }

        private void SetShownTicks(bool on)
        {
            _syncing = true;
            foreach (int i in _shown) _ticked[i] = on;
            for (int row = 0; row < _shown.Count; row++) _list.SetItemChecked(row, on);
            _syncing = false;
            UpdateLabels();
            Changed?.Invoke(this, EventArgs.Empty);
        }
    }
}
