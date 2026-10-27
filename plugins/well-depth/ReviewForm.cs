using System.ComponentModel;
using System.Globalization;
using System.Text;
using System.Windows.Forms;
using KaifaTools.WellDepth;

namespace KaifaTools.WellDepth;

/// <summary>What the window asks Revit to do. Revit work runs on Revit's main thread via ExternalEvent.</summary>
public enum ReviewRequestKind { Jump, Write, Reanalyse }

public sealed record ReviewRequest(ReviewRequestKind Kind, long Id = 0);

/// <summary>One row of the review grid. Wraps a WellResult and holds the user's choices for it.</summary>
public sealed class WellRow
{
    public WellRow(WellResult w)
    {
        W = w;
        Write = w.Status is WellStatus.Adjust or WellStatus.SyncOnly;
        Uncertain = BuildUncertain(w);
    }

    public WellResult W { get; }
    public string Mark => W.Mark;
    public string Family => W.Family;
    public bool Write { get; set; }
    public string NewBottomInput { get; set; } = "";   // manual override (mm). blank = automatic
    public string NewDepthInput { get; set; } = "";

    public string Uncertain { get; }

    public string SystemText => W.System switch
    {
        WellSystem.Em => "强电",
        WellSystem.Telco => "电信",
        WellSystem.Elv => "弱电",
        _ => "低压",
    };

    public string StatusText => W.Status switch
    {
        WellStatus.Ok => "已合规",
        WellStatus.Adjust => "需改井深",
        WellStatus.SyncOnly => "仅同步井底",
        WellStatus.Review => "需复核",
        WellStatus.NoPipe => "无排管",
        WellStatus.Temporary => "临时电",
        _ => "无参数",
    };

    public string OffsetText => Fmt(W.OffsetMm);
    public string LowestText => Fmt(W.LowestPipeMm);
    public string CurBottomText => Fmt(W.CurBottomMm);
    public string CurDepthText => Fmt(W.CurDepthMm);

    /// <summary>Automatic value, or the manual override if one was typed.</summary>
    public double? NewBottom => ParseOr(NewBottomInput, W.LowestPipeMm is null ? null : W.NewBottomMm);
    public double? NewDepth => ParseOr(NewDepthInput, W.LowestPipeMm is null ? null : W.NewDepthMm);

    public string NewBottomText => Fmt(NewBottom);
    public string NewDepthText => Fmt(NewDepth);

    public string ChangeText => NewDepth is { } nd && W.CurDepthMm is { } cd ? (nd - cd).ToString("+0;-0;0", CultureInfo.InvariantCulture) : "";

    /// <summary>Only rows with a computed (or typed) value can be written.</summary>
    public bool CanWrite => NewBottom is not null && W.Status is not (WellStatus.Temporary or WellStatus.NoParameter or WellStatus.NoPipe);

    private static string BuildUncertain(WellResult w)
    {
        var reasons = new List<string>();
        if (w.Status == WellStatus.NoPipe) reasons.Add("找不到排管");
        if (w.Status == WellStatus.Review && !string.IsNullOrEmpty(w.Note)) reasons.Add(w.Note);
        if (w.HasGapPipe) reasons.Add("有排管头未贴井壁，按距离归属");
        if (w.PipeCount > 1) reasons.Add($"接入 {w.PipeCount} 根管");
        return string.Join("；", reasons);
    }

    private static string Fmt(double? v) => v is null ? "" : v.Value.ToString("0", CultureInfo.InvariantCulture);

    private static double? ParseOr(string input, double? fallback)
    {
        if (!string.IsNullOrWhiteSpace(input) &&
            double.TryParse(input.Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var v))
            return v;
        return fallback;
    }
}

/// <summary>Modeless review window. It stays open while the user works in Revit.</summary>
public sealed class ReviewForm : Form
{
    private List<WellRow> _all;
    private readonly DepthRules _rules;
    private readonly string _rulesPath;
    private string _scope;
    private readonly BindingList<WellRow> _shown = new();

    private readonly DataGridView _grid = new();
    private readonly Label _summary = new();
    private readonly Label _notes = new();
    private readonly ComboBox _filter = new();
    private readonly CheckBox _pin = new();
    private readonly Dictionary<string, NumericUpDown> _numbers = new();
    private readonly Dictionary<string, TextBox> _keywords = new();

    private static readonly WellSystem[] Systems = { WellSystem.Em, WellSystem.Telco, WellSystem.Elv, WellSystem.Lv };
    private static readonly string[] SystemNames = { "强电 11KV/275KV", "电信 TELCO", "弱电 ELV", "低压 LV" };

    /// <summary>Set by the command. Called on the UI thread; the command forwards it to Revit.</summary>
    public Action<ReviewRequest>? Requested { get; set; }

    /// <summary>Rows the window shows. Edits (ticks, manual values) live on these objects.</summary>
    public List<WellRow> Rows => _all;

    public ReviewForm(List<WellRow> rows, DepthRules rules, string? rulesError, string rulesPath,
        IReadOnlyList<string> unmatched, int ignoredAbove, string scopeText)
    {
        _all = rows;
        _rules = rules;
        _rulesPath = rulesPath;
        _scope = scopeText;
        Text = "调整井深 · 检查台";
        StartPosition = FormStartPosition.CenterScreen;
        Font = new Font("Microsoft YaHei UI", 9F);
        MinimumSize = new Size(1100, 700);
        Size = new Size(1280, 800);
        ShowInTaskbar = true;

        var root = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 5, ColumnCount = 1 };
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        root.RowStyles.Add(new RowStyle(SizeType.Absolute, 268));
        root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        Controls.Add(root);

        // ---- row 1: scope + filter + pin ----
        var top = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, Padding = new Padding(8, 6, 8, 2) };
        top.Controls.Add(new Label { AutoSize = true, Text = "只调整：", Padding = new Padding(0, 6, 0, 0) });
        for (int i = 0; i < Systems.Length; i++)
        {
            var sys = Systems[i];
            var cb = new CheckBox { AutoSize = true, Text = SystemNames[i], Checked = true, Margin = new Padding(6, 4, 6, 0) };
            cb.CheckedChanged += (_, _) => ApplySystemScope(sys, cb.Checked);
            top.Controls.Add(cb);
        }
        top.Controls.Add(new Label { AutoSize = true, Text = "    显示：", Padding = new Padding(0, 6, 0, 0) });
        _filter.DropDownStyle = ComboBoxStyle.DropDownList;
        _filter.Items.AddRange(new object[] { "全部", "只看需改", "只看不确定", "需复核", "无排管", "已合规" });
        _filter.SelectedIndex = 0;
        _filter.SelectedIndexChanged += (_, _) => RefreshShown();
        _filter.Width = 130;
        _filter.Margin = new Padding(0, 2, 0, 0);
        top.Controls.Add(_filter);

        _pin.AutoSize = true;
        _pin.Text = "锁定在最前";
        _pin.Margin = new Padding(24, 4, 0, 0);
        _pin.CheckedChanged += (_, _) => TopMost = _pin.Checked;
        top.Controls.Add(_pin);
        root.Controls.Add(top, 0, 0);

        // ---- row 2: summary ----
        _summary.AutoSize = true;
        _summary.Padding = new Padding(10, 2, 10, 4);
        root.Controls.Add(_summary, 0, 1);

        // ---- row 3: grid ----
        SetupGrid();
        root.Controls.Add(_grid, 0, 2);

        // ---- row 4: rules ----
        root.Controls.Add(BuildRules(rulesError, unmatched, ignoredAbove), 0, 3);

        // ---- row 5: buttons ----
        var buttons = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, Padding = new Padding(8, 4, 8, 8) };
        buttons.Controls.Add(Btn("写入勾选的井", true, Write));
        buttons.Controls.Add(Btn("定位到选中的井（保持窗口）", false, Jump));
        buttons.Controls.Add(Btn("保存规则并重新分析", false, SaveAndReanalyse));
        buttons.Controls.Add(Btn("导出清单 CSV", false, Export));
        buttons.Controls.Add(Btn("关闭", false, Close));
        root.Controls.Add(buttons, 0, 4);

        DpiFit.Apply(this);
        RefreshShown();
    }

    /// <summary>After Revit re-analysed (or wrote) the model: show the new rows, keep the user's ticks and manual values.</summary>
    public void UpdateData(List<WellRow> fresh, string scope, IReadOnlyList<string> unmatched, int ignoredAbove)
    {
        var old = _all.ToDictionary(r => r.W.Id);
        foreach (var r in fresh)
        {
            if (!old.TryGetValue(r.W.Id, out var prev)) continue;
            r.Write = prev.Write;
            r.NewBottomInput = prev.NewBottomInput;
            r.NewDepthInput = prev.NewDepthInput;
        }
        _all = fresh;
        _scope = scope;
        _notes.Text = BuildNotes(unmatched, ignoredAbove);
        RefreshShown();
    }

    // ---------------- grid ----------------

    private void SetupGrid()
    {
        _grid.Dock = DockStyle.Fill;
        _grid.AutoGenerateColumns = false;
        _grid.AllowUserToAddRows = false;
        _grid.AllowUserToDeleteRows = false;
        _grid.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
        _grid.MultiSelect = false;
        _grid.RowHeadersVisible = false;
        _grid.BackgroundColor = Color.White;
        _grid.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.AutoSize;
        _grid.DataSource = _shown;
        _grid.CellContentClick += (_, e) => { if (e.RowIndex >= 0) _grid.EndEdit(); };
        _grid.CellValueChanged += (_, _) => { ColourRows(); UpdateSummary(); };
        _grid.CellDoubleClick += (_, e) => { if (e.RowIndex >= 0) Jump(); };
        _grid.DataBindingComplete += (_, _) => ColourRows();

        void Col(string header, string prop, int width, bool readOnly = true, bool isCheck = false)
        {
            if (isCheck)
                _grid.Columns.Add(new DataGridViewCheckBoxColumn { Name = prop, HeaderText = header, DataPropertyName = prop, Width = width });
            else
                _grid.Columns.Add(new DataGridViewTextBoxColumn { Name = prop, HeaderText = header, DataPropertyName = prop, Width = width, ReadOnly = readOnly });
        }
        Col("写入", nameof(WellRow.Write), 46, false, true);
        Col("标记", nameof(WellRow.Mark), 62);
        Col("族", nameof(WellRow.Family), 180);
        Col("专业", nameof(WellRow.SystemText), 50);
        Col("状态", nameof(WellRow.StatusText), 96);
        Col("偏移", nameof(WellRow.OffsetText), 56);
        Col("最低管底", nameof(WellRow.LowestText), 70);
        Col("当前井底", nameof(WellRow.CurBottomText), 70);
        Col("新井底", nameof(WellRow.NewBottomText), 66);
        Col("手动井底", nameof(WellRow.NewBottomInput), 70, false);
        Col("当前井深", nameof(WellRow.CurDepthText), 70);
        Col("新井深", nameof(WellRow.NewDepthText), 66);
        Col("手动井深", nameof(WellRow.NewDepthInput), 70, false);
        Col("变化", nameof(WellRow.ChangeText), 62);
        Col("不确定原因", nameof(WellRow.Uncertain), 300);
        _grid.Columns[_grid.Columns.Count - 1].AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
    }

    private static readonly Color ChangeBack = Color.FromArgb(255, 183, 77);   // orange: a value that will be written
    private static readonly Color ManualBack = Color.FromArgb(255, 241, 118);   // yellow: typed by hand
    private static readonly Color ChangeText = Color.FromArgb(120, 53, 0);

    /// <summary>Row tint by status, then highlight every value that will change (and every hand-typed value).</summary>
    private void ColourRows()
    {
        foreach (DataGridViewRow r in _grid.Rows)
        {
            if (r.DataBoundItem is not WellRow row) continue;
            r.DefaultCellStyle.BackColor = row.W.Status switch
            {
                WellStatus.Review => Color.FromArgb(255, 243, 205),
                WellStatus.NoPipe => Color.FromArgb(235, 235, 235),
                WellStatus.Temporary or WellStatus.NoParameter => Color.FromArgb(235, 235, 235),
                WellStatus.Adjust or WellStatus.SyncOnly => Color.FromArgb(232, 245, 255),
                _ => Color.White,
            };

            bool willWrite = row.Write && row.CanWrite;
            var newB = row.NewBottom;
            var newD = row.NewDepth;
            bool bottomChanges = willWrite && newB is { } nb && (row.W.CurBottomMm is not { } cb || Math.Abs(nb - cb) > 0.5);
            bool depthChanges = willWrite && newD is { } nd && (row.W.CurDepthMm is not { } cd || Math.Abs(nd - cd) > 0.5);

            Tint(r, nameof(WellRow.NewBottomText), bottomChanges, ChangeBack, true);
            Tint(r, nameof(WellRow.NewDepthText), depthChanges, ChangeBack, true);
            Tint(r, nameof(WellRow.ChangeText), depthChanges, ChangeBack, true);
            Tint(r, nameof(WellRow.NewBottomInput), !string.IsNullOrWhiteSpace(row.NewBottomInput), ManualBack, true);
            Tint(r, nameof(WellRow.NewDepthInput), !string.IsNullOrWhiteSpace(row.NewDepthInput), ManualBack, true);
        }
    }

    private static void Tint(DataGridViewRow r, string column, bool on, Color back, bool bold)
    {
        var style = r.Cells[column].Style;
        style.BackColor = on ? back : Color.Empty;
        style.SelectionBackColor = on ? back : Color.Empty;   // still visible when the row is selected
        style.SelectionForeColor = on ? ChangeText : Color.Empty;
        style.ForeColor = on ? ChangeText : Color.Empty;
        style.Font = on && bold ? BoldFont(r) : null;
    }

    private static Font? _bold;
    private static Font BoldFont(DataGridViewRow r) => _bold ??= new Font(r.DataGridView!.Font, FontStyle.Bold);

    private void RefreshShown()
    {
        string f = _filter.SelectedItem as string ?? "全部";
        IEnumerable<WellRow> q = _all;
        q = f switch
        {
            "只看需改" => q.Where(r => r.W.Status is WellStatus.Adjust or WellStatus.SyncOnly),
            "只看不确定" => q.Where(r => r.Uncertain.Length > 0),
            "需复核" => q.Where(r => r.W.Status == WellStatus.Review),
            "无排管" => q.Where(r => r.W.Status == WellStatus.NoPipe),
            "已合规" => q.Where(r => r.W.Status == WellStatus.Ok),
            _ => q,
        };
        _shown.RaiseListChangedEvents = false;
        _shown.Clear();
        foreach (var r in q) _shown.Add(r);
        _shown.RaiseListChangedEvents = true;
        _shown.ResetBindings();
        UpdateSummary();
    }

    /// <summary>Checks/unchecks the writable wells of one system.</summary>
    private void ApplySystemScope(WellSystem sys, bool include)
    {
        foreach (var r in _all)
            if (r.W.System == sys && r.CanWrite && r.W.Status is WellStatus.Adjust or WellStatus.SyncOnly or WellStatus.Review)
                r.Write = include;
        ColourRows();
        _grid.Refresh();
        UpdateSummary();
    }

    private void UpdateSummary()
    {
        int Count(WellStatus s) => _all.Count(r => r.W.Status == s);
        int picked = _all.Count(r => r.Write && r.CanWrite);
        int uncertain = _all.Count(r => r.Uncertain.Length > 0);
        _summary.Text =
            $"{_scope}\n" +
            $"共 {_all.Count} 口井：需改井深 {Count(WellStatus.Adjust)}　仅同步井底 {Count(WellStatus.SyncOnly)}　" +
            $"已合规 {Count(WellStatus.Ok)}　需复核 {Count(WellStatus.Review)}　无排管 {Count(WellStatus.NoPipe)}　" +
            $"有不确定原因 {uncertain} 口　｜　当前勾选写入 {picked} 口\n" +
            "双击行或点“定位”会在 Revit 里选中并显示该井，窗口保持打开，你可以直接去手动改；改完点“写入”或再次分析即可刷新。";
    }

    // ---------------- rules ----------------

    private Control BuildRules(string? rulesError, IReadOnlyList<string> unmatched, int ignoredAbove)
    {
        var box = new GroupBox { Text = "规则（改完点“保存规则并重新分析”，立刻生效）", Dock = DockStyle.Fill, Padding = new Padding(8) };
        var panel = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, AutoScroll = true };
        box.Controls.Add(panel);

        if (rulesError != null)
            panel.Controls.Add(new Label { AutoSize = true, ForeColor = Color.Firebrick, Text = "⚠ " + rulesError });

        var numbers = new TableLayoutPanel { AutoSize = true, ColumnCount = 4 };
        void Num(string key, string label, double value, double max)
        {
            var nud = new NumericUpDown { Minimum = 0, Maximum = (decimal)max, DecimalPlaces = 0, Increment = 10, Width = 72, Value = (decimal)Math.Min(value, max) };
            _numbers[key] = nud;
            numbers.Controls.Add(new Label { AutoSize = true, Text = label, Padding = new Padding(10, 6, 2, 0), MaximumSize = new Size(230, 0) });
            numbers.Controls.Add(nud);
        }
        Num("ClearanceMm", "井底低于管底 (mm)", _rules.ClearanceMm, 5000);
        Num("ToleranceMm", "误差视为合规 (mm)", _rules.ToleranceMm, 1000);
        Num("TightMm", "管压井边缘容差 (mm)", _rules.TightMm, 1000);
        Num("MaxGapMm", "管头距井的最大间距 (mm)", _rules.MaxGapMm, 10000);
        Num("MaxDepthMm", "井深超过此值→复核 (mm)", _rules.MaxDepthMm, 30000);
        Num("MinDepthMm", "井深小于此值→复核 (mm)", _rules.MinDepthMm, 30000);
        Num("MaxSpreadMm", "最低管比次低管低超过→复核 (mm)", _rules.MaxSpreadMm, 30000);
        Num("MaxRefGapMm", "井顶与偏移不一致→复核 (mm)", _rules.MaxRefGapMm, 30000);
        panel.Controls.Add(numbers);

        var keys = new TableLayoutPanel { AutoSize = true, ColumnCount = 4 };
        void Key(string key, string label, string[] value)
        {
            var tb = new TextBox { Width = 210, Text = string.Join(", ", value) };
            _keywords[key] = tb;
            keys.Controls.Add(new Label { AutoSize = true, Text = label, Padding = new Padding(10, 6, 2, 0), MaximumSize = new Size(120, 0) });
            keys.Controls.Add(tb);
        }
        Key("PipeEm", "强电排管关键字", _rules.PipeEm);
        Key("PipeTelco", "电信排管关键字", _rules.PipeTelco);
        Key("PipeElv", "弱电排管关键字", _rules.PipeElv);
        Key("PipeLv", "低压排管关键字", _rules.PipeLv);
        Key("Exclude", "永不处理的关键字", _rules.Exclude);
        panel.Controls.Add(keys);

        _notes.AutoSize = true;
        _notes.MaximumSize = new Size(900, 0);
        _notes.Text = BuildNotes(unmatched, ignoredAbove);
        panel.Controls.Add(_notes);
        return box;
    }

    private string BuildNotes(IReadOnlyList<string> unmatched, int ignoredAbove)
    {
        var sb = new StringBuilder();
        sb.AppendLine("规则文件：" + _rulesPath + "（关键字用英文逗号分隔）");
        sb.AppendLine($"位于井顶以上、未接入的排管：{ignoredAbove} 根");
        sb.AppendLine(unmatched.Count == 0
            ? "未识别的排管：无（所有排管都归入了专业）"
            : "未识别的排管（请把族名里的关键字加进上面的排管关键字，再保存重新分析）：\n  " + string.Join("\n  ", unmatched.Take(15)));
        return sb.ToString();
    }

    private void SaveRules()
    {
        _rules.ClearanceMm = (double)_numbers["ClearanceMm"].Value;
        _rules.ToleranceMm = (double)_numbers["ToleranceMm"].Value;
        _rules.TightMm = (double)_numbers["TightMm"].Value;
        _rules.MaxGapMm = (double)_numbers["MaxGapMm"].Value;
        _rules.MaxDepthMm = (double)_numbers["MaxDepthMm"].Value;
        _rules.MinDepthMm = (double)_numbers["MinDepthMm"].Value;
        _rules.MaxSpreadMm = (double)_numbers["MaxSpreadMm"].Value;
        _rules.MaxRefGapMm = (double)_numbers["MaxRefGapMm"].Value;
        _rules.PipeEm = Split(_keywords["PipeEm"].Text);
        _rules.PipeTelco = Split(_keywords["PipeTelco"].Text);
        _rules.PipeElv = Split(_keywords["PipeElv"].Text);
        _rules.PipeLv = Split(_keywords["PipeLv"].Text);
        _rules.Exclude = Split(_keywords["Exclude"].Text);
        RulesFile.Save(_rules);
    }

    private static string[] Split(string s) =>
        s.Split(new[] { ',', '，' }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);

    // ---------------- actions (all Revit work is requested, never done here) ----------------

    private Button Btn(string text, bool primary, Action onClick)
    {
        var b = new Button { Text = text, AutoSize = true, Padding = new Padding(10, 4, 10, 4), Margin = new Padding(0, 0, 10, 0) };
        if (primary) { b.BackColor = Color.FromArgb(47, 140, 255); b.ForeColor = Color.White; b.FlatStyle = FlatStyle.Flat; }
        b.Click += (_, _) => onClick();
        return b;
    }

    private void Write()
    {
        int n = _all.Count(r => r.Write && r.CanWrite);
        if (n == 0) { MessageBox.Show(this, "没有勾选可写入的井。", "调整井深"); return; }
        if (MessageBox.Show(this, $"确定写入勾选的 {n} 口井吗？\n写入后可用 Ctrl+Z 一次撤销。", "确认写入",
                MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return;
        Requested?.Invoke(new ReviewRequest(ReviewRequestKind.Write));
    }

    private void Jump()
    {
        if (_grid.CurrentRow?.DataBoundItem is not WellRow row) { MessageBox.Show(this, "先在表格里点一口井。", "调整井深"); return; }
        Requested?.Invoke(new ReviewRequest(ReviewRequestKind.Jump, row.W.Id));
    }

    private void SaveAndReanalyse()
    {
        SaveRules();
        Requested?.Invoke(new ReviewRequest(ReviewRequestKind.Reanalyse));
    }

    private void Export()
    {
        string path = ReviewCsv.Write(_all);
        MessageBox.Show(this, "清单已导出（含旧值、新值、手动修改、不确定原因）：\n" + path, "调整井深");
    }
}

/// <summary>CSV list: old values, new values, manual edits, uncertainty.</summary>
public static class ReviewCsv
{
    public static string Write(List<WellRow> rows)
    {
        string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools");
        Directory.CreateDirectory(dir);
        string path = Path.Combine(dir, $"井深调整清单_{DateTime.Now:yyyyMMdd_HHmmss}.csv");

        static string N(double? v) => v is null ? "" : v.Value.ToString("0", CultureInfo.InvariantCulture);
        static string Q(string s) => "\"" + s.Replace("\"", "\"\"") + "\"";

        var sb = new StringBuilder();
        sb.AppendLine("ElementId,标记,族,专业,状态,写入,手动修改,相对偏移mm,最低管底mm,当前井底标高mm,新井底标高mm,当前井深mm,新井深mm,井深变化mm,匹配管数,匹配管ID,不确定原因");
        foreach (var row in rows)
        {
            var r = row.W;
            bool manual = !string.IsNullOrWhiteSpace(row.NewBottomInput) || !string.IsNullOrWhiteSpace(row.NewDepthInput);
            double? nb = row.NewBottom, nd = row.NewDepth;
            double? change = nd is { } d && r.CurDepthMm is { } cd ? d - cd : null;
            sb.AppendLine(string.Join(",",
                r.Id, Q(r.Mark), Q(r.Family), row.SystemText, row.StatusText,
                row.Write ? "是" : "否", manual ? "是" : "否",
                N(r.OffsetMm), N(r.LowestPipeMm), N(r.CurBottomMm), N(nb),
                N(r.CurDepthMm), N(nd), N(change),
                r.PipeCount, Q(r.PipeIds), Q(row.Uncertain)));
        }
        File.WriteAllText(path, sb.ToString(), new UTF8Encoding(encoderShouldEmitUTF8Identifier: true));
        return path;
    }
}
