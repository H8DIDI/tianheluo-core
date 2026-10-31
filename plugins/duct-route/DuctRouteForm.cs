using System.Drawing;
using System.Windows.Forms;

namespace KaifaTools.DuctRoute;

public enum ScopeKind { Selection, ScopeBox, WholeModel }
public enum ReqKind { Compute, Apply, Restore, Select, Cancel, DeleteDimsAndRetry, ImportResult, PickOne, CutWells, RestoreCuts, Round, Align, Check, GeneralCheck, JogPick, JogRestore, JogPlane, JogBundle, JogAuto, JogCorridor }
public sealed record RouteRequest(ReqKind Kind, ScopeKind Scope = ScopeKind.Selection, long ScopeBoxId = 0, long SelectId = 0, RouteRules? Rules = null, string? Path = null);

public sealed class DuctRouteForm : Form
{
    public Action<RouteRequest>? Requested;
    readonly RadioButton _rSel = new() { Text = "选中的排管", Checked = true, AutoSize = true };
    readonly RadioButton _rBox = new() { Text = "范围框", AutoSize = true };
    readonly RadioButton _rAll = new() { Text = "整个模型（慢）", AutoSize = true };
    readonly ComboBox _boxes = new() { DropDownStyle = ComboBoxStyle.DropDownList, Width = 220 };
    readonly TextBox _road = new() { Width = 80, Text = "道路" };
    readonly NumericUpDown _coverRoad = Num(1.2m, 0, 5, 0.1m), _coverOff = Num(0.7m, 0, 5, 0.1m), _cross = Num(0.2m, 0, 2, 0.05m), _slope = Num(8, 1, 30, 1), _floor = Num(0, -10, 10, 0.5m);
    readonly TextBox _ground = new() { Width = 70 };
    readonly CheckBox _pipes = new() { Text = "把水管当障碍", AutoSize = true };
    readonly Button _go = new() { Text = "1 计算", Width = 90, Height = 30 }, _apply = new() { Text = "2 写入 Revit", Width = 110, Height = 30, Enabled = false },
        _restore = new() { Text = "还原上一次", Width = 100, Height = 30 }, _dims = new() { Text = "删标注并重写", Width = 170, Height = 30, Visible = false }, _import = new() { Text = "导入 Rhino 结果…", Width = 150, Height = 30 },
        _pick = new() { Text = "点选一根避让", Width = 140, Height = 30 }, _cut = new() { Text = "井处断开", Width = 100, Height = 30 }, _restoreCut = new() { Text = "还原断开", Width = 100, Height = 30 },
        _round = new() { Text = "标高取整", Width = 100, Height = 30 }, _align = new() { Text = "接头对齐", Width = 100, Height = 30 }, _check = new() { Text = "碰撞检查", Width = 100, Height = 30 }, _gcheck = new() { Text = "水管/风管/桥架/线管碰撞检查", Width = 260, Height = 30 }, _jog = new() { Text = "点选管线绕行(Jog)", Width = 170, Height = 30 }, _jogRestore = new() { Text = "还原绕行", Width = 100, Height = 30 }, _jogPlane = new() { Text = "点选管线平面绕行", Width = 170, Height = 30 }, _jogBundle = new() { Text = "选中一束一起平面绕行", Width = 220, Height = 30 }, _jogAuto = new() { Text = "自动规划多根绕行(并线)", Width = 230, Height = 30 }, _jogCorridor = new() { Text = "走廊协调绕行(自动选上/下/左/右)", Width = 290, Height = 30 }, _stop = new() { Text = "停止", Width = 60, Height = 30, Enabled = false };
    readonly DataGridView _grid = new() { Dock = DockStyle.Fill, ReadOnly = true, AllowUserToAddRows = false, RowHeadersVisible = false, SelectionMode = DataGridViewSelectionMode.FullRowSelect, AutoGenerateColumns = false };
    readonly Label _status = new() { Dock = DockStyle.Fill, AutoSize = false, TextAlign = ContentAlignment.MiddleLeft };
    readonly TextBox _log = new() { Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, Dock = DockStyle.Fill };
    readonly CheckBox _onlyChanged = new() { Text = "只看有变化/有问题的", Checked = true, AutoSize = true };
    List<Bank> _all = new();

    static NumericUpDown Num(decimal v, decimal lo, decimal hi, decimal step) => new() { Minimum = lo, Maximum = hi, DecimalPlaces = step < 1 ? 2 : 0, Increment = step, Value = v, Width = 62 };

    public DuctRouteForm(RouteRules rules, int selectedCount)
    {
        Text = "排管竖向避让优化"; Width = 980; Height = 760; MinimumSize = new Size(820, 560); StartPosition = FormStartPosition.CenterScreen; TopMost = false;
        _rSel.Text = $"选中的排管（现选 {selectedCount} 根）";
        _coverRoad.Value = (decimal)rules.CoverRoadM; _coverOff.Value = (decimal)rules.CoverOffM; _cross.Value = (decimal)rules.ClearCrossM; _slope.Value = (decimal)rules.MaxSlopePct;
        _floor.Value = (decimal)rules.MinBottomM; _road.Text = rules.RoadKeyword; _pipes.Checked = rules.AvoidPipes; _ground.Text = rules.GroundM?.ToString("0.###") ?? "";
        if (selectedCount == 0) _rBox.Checked = true;

        var top = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, FlowDirection = FlowDirection.LeftToRight, WrapContents = true, Padding = new Padding(8) };
        top.Controls.AddRange(new Control[] { new Label { Text = "范围：", AutoSize = true, Margin = new Padding(0, 6, 0, 0) }, _rSel, _rBox, _boxes, _rAll });
        var rules1 = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, WrapContents = true, Padding = new Padding(8, 0, 8, 4) };
        void Pair(string t, Control c) { rules1.Controls.Add(new Label { Text = t, AutoSize = true, Margin = new Padding(8, 6, 0, 0) }); rules1.Controls.Add(c); }
        Pair("路面楼板类型含", _road); Pair("路面覆土 m", _coverRoad); Pair("路外覆土 m", _coverOff); Pair("交叉净距 m", _cross); Pair("最大坡度 %", _slope); Pair("底标高下限 m", _floor); Pair("路外地面标高 m（空=取最近路面）", _ground);
        rules1.Controls.Add(_pipes);
        var btns = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(8, 0, 8, 4) };
        var tools = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(8, 0, 8, 4) };
        btns.Controls.AddRange(new Control[] { _go, _import, _apply, _restore, _stop, _dims, _onlyChanged });

        _grid.Columns.AddRange(
            new DataGridViewTextBoxColumn { HeaderText = "编号", DataPropertyName = "Id", Width = 80 },
            new DataGridViewTextBoxColumn { HeaderText = "类别", Width = 55 },
            new DataGridViewTextBoxColumn { HeaderText = "族", Width = 200 },
            new DataGridViewTextBoxColumn { HeaderText = "长 m", Width = 55 },
            new DataGridViewTextBoxColumn { HeaderText = "原 起/止 底标高", Width = 120 },
            new DataGridViewTextBoxColumn { HeaderText = "新 起/止 底标高", Width = 120 },
            new DataGridViewTextBoxColumn { HeaderText = "起/止 变化 m", Width = 100 },
            new DataGridViewTextBoxColumn { HeaderText = "状态", Width = 110 });
        _grid.CellDoubleClick += (_, e) => { if (e.RowIndex >= 0 && _grid.Rows[e.RowIndex].Tag is long id) Requested?.Invoke(new RouteRequest(ReqKind.Select, SelectId: id)); };
        _grid.EnableHeadersVisualStyles = false; _grid.ColumnHeadersDefaultCellStyle.BackColor = Color.FromArgb(240, 240, 240);

        var split = new SplitContainer { Dock = DockStyle.Fill, Orientation = Orientation.Horizontal, SplitterDistance = 400 };
        split.Panel1.Controls.Add(_grid); split.Panel2.Controls.Add(_log);
        var bottom = new Panel { Dock = DockStyle.Bottom, Height = 34 }; bottom.Controls.Add(_status);
        tools.Controls.AddRange(new Control[] { _pick, _cut, _restoreCut, _round, _align, _check, _gcheck, _jog, _jogPlane, _jogBundle, _jogAuto, _jogCorridor, _jogRestore });
        Controls.Add(split); Controls.Add(bottom); Controls.Add(tools); Controls.Add(btns); Controls.Add(rules1); Controls.Add(top);

        _go.Click += (_, _) => { Busy(true); Requested?.Invoke(new RouteRequest(ReqKind.Compute, CurrentScope(), BoxId(), Rules: ReadRules(rules))); };
        _apply.Click += (_, _) => { if (MessageBox.Show(this, $"把计算结果写进 Revit？\n将先备份原标高，写入后可 Ctrl+Z 撤销，也可点“还原上一次”。", "写入", MessageBoxButtons.OKCancel) == DialogResult.OK) { Busy(true); Requested?.Invoke(new RouteRequest(ReqKind.Apply)); } };
        _restore.Click += (_, _) => { if (MessageBox.Show(this, "按最近一次备份还原标高？", "还原", MessageBoxButtons.OKCancel) == DialogResult.OK) { Busy(true); Requested?.Invoke(new RouteRequest(ReqKind.Restore)); } };
        _stop.Click += (_, _) => Requested?.Invoke(new RouteRequest(ReqKind.Cancel));
        _import.Click += (_, _) =>
        {
            using var dlg = new OpenFileDialog { Title = "选择 Rhino 导出的标高结果", Filter = "JSON|*.json", InitialDirectory = System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools") };
            if (dlg.ShowDialog(this) == DialogResult.OK) { Busy(true); Requested?.Invoke(new RouteRequest(ReqKind.ImportResult, CurrentScope(), BoxId(), Rules: ReadRules(rules), Path: dlg.FileName)); }
        };
        _dims.Click += (_, _) => { if (MessageBox.Show(this, $"有 {_dims.Tag} 个尺寸标注引用了这些排管，改标高会让它们失效。\n删除这些标注（信息先备份到文件），然后重写这些排管？", "删标注并重写", MessageBoxButtons.OKCancel) == DialogResult.OK) { Busy(true); Requested?.Invoke(new RouteRequest(ReqKind.DeleteDimsAndRetry)); } };
        void Tool(Button b, ReqKind k, string? ask = null)
        {
            b.Click += (_, _) =>
            {
                if (ask is not null && MessageBox.Show(this, ask, b.Text, MessageBoxButtons.OKCancel) != DialogResult.OK) return;
                Busy(true); Requested?.Invoke(new RouteRequest(k, CurrentScope(), BoxId(), Rules: ReadRules(rules)));
            };
        }
        Tool(_pick, ReqKind.PickOne); Tool(_cut, ReqKind.CutWells); Tool(_round, ReqKind.Round); Tool(_align, ReqKind.Align); Tool(_check, ReqKind.Check); Tool(_gcheck, ReqKind.GeneralCheck); Tool(_jog, ReqKind.JogPick); Tool(_jogAuto, ReqKind.JogAuto); Tool(_jogCorridor, ReqKind.JogCorridor); Tool(_jogBundle, ReqKind.JogBundle, "先在 Revit 里选中平行的几根管线（一束），再点确定。它们会一起平移，保持平行。继续？"); Tool(_jogPlane, ReqKind.JogPlane, "平面绕行会改这根管线的平面位置（向左或向右绕开障碍）。继续点选？"); Tool(_jogRestore, ReqKind.JogRestore, "按最近一次备份，撤销上一次绕行？");
        Tool(_restoreCut, ReqKind.RestoreCuts, "按最近一次备份，把断开的排管接回去（删除多出来的段）？");
        _onlyChanged.CheckedChanged += (_, _) => Fill(_all);
        DpiFit.Apply(this);
    }

    ScopeKind CurrentScope() => _rBox.Checked ? ScopeKind.ScopeBox : _rAll.Checked ? ScopeKind.WholeModel : ScopeKind.Selection;
    long BoxId() => _boxes.SelectedItem is BoxItem bi ? bi.Id : 0;
    sealed record BoxItem(long Id, string Name) { public override string ToString() => Name; }
    public void SetBoxes(IEnumerable<(long id, string name)> boxes) { _boxes.Items.Clear(); foreach (var (id, n) in boxes) _boxes.Items.Add(new BoxItem(id, n)); if (_boxes.Items.Count > 0) _boxes.SelectedIndex = 0; }

    RouteRules ReadRules(RouteRules baseRules)
    {
        var r = RouteRules.Load(out _);
        r.CoverRoadM = (double)_coverRoad.Value; r.CoverOffM = (double)_coverOff.Value; r.ClearCrossM = (double)_cross.Value; r.MaxSlopePct = (double)_slope.Value;
        r.MinBottomM = (double)_floor.Value; r.RoadKeyword = _road.Text.Trim(); r.AvoidPipes = _pipes.Checked;
        r.GroundM = double.TryParse(_ground.Text, out var g) ? g : null;
        return r;
    }

    public void Busy(bool b) { _go.Enabled = !b; _restore.Enabled = !b; _stop.Enabled = b; if (b) _apply.Enabled = false; UseWaitCursor = false; }
    public void SetBlocked(int banks, int dims) { _dims.Visible = banks > 0; _dims.Tag = dims; _dims.Text = $"删标注并重写（{banks} 根）"; }
    public void ShowMessage(string msg) { _status.Text = msg; Log(msg); }
    public void Log(string msg) { _log.AppendText($"{DateTime.Now:HH:mm:ss}  {msg}\r\n"); }

    public void ShowResult(List<Bank> banks, string summary)
    {
        _all = banks; Fill(banks); _status.Text = summary; Log(summary); Busy(false);
        _apply.Enabled = banks.Any(b => b.Changed);
    }

    void Fill(List<Bank> banks)
    {
        _grid.Rows.Clear();
        foreach (var b in banks.OrderByDescending(x => x.Status.Contains("碰撞") || x.Status.Contains("覆土")).ThenByDescending(x => x.Changed).ThenBy(x => x.Id))
        {
            if (_onlyChanged.Checked && !b.Changed && b.Status is "无需改") continue;
            int i = _grid.Rows.Add(b.Id, b.Kind, b.Family.Length > 22 ? b.Family[..22] : b.Family, b.L.ToString("0.0"), $"{b.O0:0.00} / {b.O1:0.00}", $"{b.Z0:0.00} / {b.Z1:0.00}",
                $"{b.Z0 - b.O0:+0.00;-0.00;0} / {b.Z1 - b.O1:+0.00;-0.00;0}", b.Status);
            _grid.Rows[i].Tag = b.Id;
            if (b.Detail.Length > 0) foreach (DataGridViewCell c in _grid.Rows[i].Cells) c.ToolTipText = b.Detail;
            if (b.Status.Contains("碰撞") || b.Status.Contains("覆土")) _grid.Rows[i].DefaultCellStyle.BackColor = Color.FromArgb(255, 235, 200);
            else if (b.Changed) _grid.Rows[i].DefaultCellStyle.BackColor = Color.FromArgb(225, 245, 225);
        }
    }
}
