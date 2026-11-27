// 天河落 · 图集管理（acad-sheets）：图框外部参照、图签批量填写、框选建视口、图纸列表（排序/编号/目录）、批量打印成一个 PDF。
// 平时弹窗口；带参数时（THLRUN acad-sheets?k=v;...）按参数无界面执行，给批处理和自动测试用：
//   learn=<图框.dwg>;tplname=<名>      学习图框 → 模板
//   tpl=<模板名>;paper=A1              选模板、图幅
//   new=x0,y0,x1,y1,比例,图名|...      框选范围建图纸（比例 0 = 自动）
//   all.<字段>=<值>                    所有图纸填同一个值（如 all.设计=张三）
//   number=<规则>;numfield=图号;start=1;step=1;sheetno=<张号规则>
//   rename=<布局名规则>                 如 {图号} {图名}
//   toc=1;tocno=<目录图号>              生成目录
//   pdf=<路径>                          全部图纸打一个 PDF
//   ui=1                               最后再打开窗口（测试截图用）
//   分区出图：zones=名称:x0,y0,x1,y1|名称:x,y x,y x,y ...  或 zonelayer=THL-分区  或 grid=1;gridscale=500;overlap=20
//            zbase=<图名基>;zscale=<统一比例，0=自动>;perzone=1;legend=1;index=1;match=1;savezones=1
//   Excel：export=<csv>   import=<csv>;apply=1（apply=0 只预览）
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Forms;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.PlottingServices;
using AcApp = Autodesk.AutoCAD.ApplicationServices.Application;
using Exception = System.Exception;

namespace Thl.Acad.Sheets
{
    public class Plugin
    {
        internal static readonly string SettingsFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo", "AutoCAD", "settings", "acad-sheets.txt");
        internal static readonly string LogFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo", "AutoCAD", "logs", "acad-sheets.log");

        internal static void Log(string s)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(LogFile));
                File.AppendAllText(LogFile, $"{DateTime.Now:MM-dd HH:mm:ss} {s}\n");
            }
            catch { }
        }

        /// <summary>调试：环境变量 THL_TRACE=1 时逐步写日志（排查崩溃位置）。</summary>
        internal static void Trace(string s) { if (Environment.GetEnvironmentVariable("THL_TRACE") == "1") Log("· " + s); }

        internal static Dictionary<string, string> LoadSettings()
        {
            var d = new Dictionary<string, string>();
            try
            {
                if (File.Exists(SettingsFile))
                    foreach (var l in File.ReadAllLines(SettingsFile, Encoding.UTF8))
                    {
                        var i = l.IndexOf('=');
                        if (i > 0) d[l.Substring(0, i)] = l.Substring(i + 1).Replace("\\n", "\n");
                    }
            }
            catch { }
            return d;
        }

        internal static void SaveSettings(Dictionary<string, string> d)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(SettingsFile));
                File.WriteAllLines(SettingsFile, d.Select(kv => kv.Key + "=" + (kv.Value ?? "").Replace("\r", "").Replace("\n", "\\n")), new UTF8Encoding(true));
            }
            catch { }
        }

        public string Execute(Document doc, IDictionary<string, string> args)
        {
            if (doc == null) return "图集管理：请先打开一张图纸。";
            try
            {
                if (args != null && args.TryGetValue("ui", out var ui) && ui == "list") { using (var f = new SheetForm(doc)) Ui.Show(f, "acad-sheets"); return null; }
                if (args != null && args.Count > 0 && !args.ContainsKey("ui")) return Headless(doc, args);
                if (Ui.IsConsole) return "一键出图：无界面运行时需要参数（THLRUN acad-sheets?...）。";
                using (var w = new Wizard.WizardForm(new Wizard.Backend(doc)))
                {
                    w.OpenList = owner => { using (var f = new SheetForm(doc)) Autodesk.AutoCAD.ApplicationServices.Application.ShowModalDialog(owner, f, false); };
                    // 自动测试（THL_SNAP_DIR）：用真实后端走 ①~④ 页（不生成），再开「设置」三页，逐页截图
                    var snap = Environment.GetEnvironmentVariable("THL_SNAP_DIR");
                    if (!string.IsNullOrEmpty(snap))
                        w.Shown += (_, __) =>
                        {
                            try
                            {
                                Directory.CreateDirectory(snap);
                                for (int i = 0; i < 4; i++) { w.GoTo(i); w.Snap(Path.Combine(snap, $"v3_acad_向导{i + 1}.png")); }
                                using (var sf = new Wizard.SettingsForm(new Wizard.Backend(doc), FrameTemplate.All().FirstOrDefault()?.Name ?? ""))
                                {
                                    sf.Show(w);
                                    for (int i = 0; i < 3; i++) { sf.ShowTab(i); sf.Snap(Path.Combine(snap, $"v3_acad_设置{i + 1}.png")); }
                                    sf.Close();
                                }
                            }
                            catch (Exception ex) { Log("向导截图失败 " + ex); }
                        };
                    Ui.Show(w, "wizard");
                }
                return null;
            }
            catch (Exception ex)
            {
                Log("出错 " + ex);
                return "图集管理出错：" + ex.Message;
            }
        }

        internal static Extents3d ModelExtents(Database db)
        {
            var ext = new Extents3d(); bool any = false;
            using (var tr = db.TransactionManager.StartOpenCloseTransaction())
            {
                var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
                foreach (ObjectId id in ms)
                {
                    if (!(tr.GetObject(id, OpenMode.ForRead) is Entity e) || e.Layer == Zones.Layer) continue;
                    var b = e.Bounds; if (b == null) continue;
                    if (!any) { ext = b.Value; any = true; } else ext.AddExtents(b.Value);
                }
                tr.Commit();
            }
            return ext;
        }

        static string Headless(Document doc, IDictionary<string, string> a)
        {
            var sb = new StringBuilder();
            Action<string> log = s => { sb.AppendLine(s); Log(s); };
            var st = LoadSettings();
            FrameTemplate t = null;
            // 向导后端自动测试（无界面）：wizard=1;tpl=;slim=0|1;tidy=0|1;prefix=;pdf=0|1
            if (Args.B(a, "wizard", false))
            {
                var b = new Wizard.Backend(doc);
                var ws = new Wizard.WizardSettings
                {
                    Template = Args.S(a, "tpl", FrameTemplate.All().FirstOrDefault()?.Name ?? ""), Slim = Args.B(a, "slim", false), Tidy = Args.B(a, "tidy", true),
                    NumPrefix = Args.S(a, "prefix", "TEST-"), Pdf = Args.B(a, "pdf", true), Toc = true,
                };
                if (a.TryGetValue("zones", out var zsave)) using (doc.LockDocument()) Zones.Save(doc.Database, Zones.Parse(zsave));
                var zones = b.ZonesFromLayer();
                log($"读到分区 {zones.Count} 块：" + string.Join("、", zones.Select(z => z.Name)));
                if ((ws.Slim || ws.Tidy) && !Args.B(a, "noprep", false)) log(b.Prepare(ws, (i, m) => log("  " + m)));
                var plans = b.Plan(zones, ws, log);
                for (int i = 0; i < plans.Count; i++) { plans[i].Number = ws.Number(i); plans[i].Title = ws.Title(plans[i].Zone); }
                log($"预览 {plans.Count} 张：" + string.Join("，", plans.Select(p => $"{p.Zone} 1:{p.Scale} {p.Fill:P0}")));
                log(b.Generate(plans, zones, ws, (i, m) => log("  " + m), () => false));
                log("布局：" + string.Join("、", Sheets.TaggedLayouts(doc.Database).Keys));
                return "一键出图（测试）：\n" + sb.ToString().TrimEnd();
            }
            if (a.TryGetValue("export", out var exp) || a.TryGetValue("import", out _))
            {
                var trs = ExcelSync.Scan(doc, FrameTemplate.All(), log);
                if (exp != null) log(ExcelSync.Export(trs, exp));
                if (a.TryGetValue("import", out var imp))
                {
                    var changes = ExcelSync.Diff(trs, imp);
                    log($"预览 {changes.Count} 处改动：");
                    foreach (var c in changes.Take(60)) log("  " + c);
                    if (Args.B(a, "apply", false)) log(ExcelSync.Apply(doc, changes, log));
                }
                if (!a.ContainsKey("learn") && !a.ContainsKey("new") && !a.ContainsKey("zones") && !a.ContainsKey("zonelayer") && !a.ContainsKey("grid") && !a.ContainsKey("pdf"))
                    return "图集管理：\n" + sb.ToString().TrimEnd();
            }
            using (doc.LockDocument())
            {
                if (a.TryGetValue("learn", out var learn))
                {
                    var name = Args.S(a, "tplname", Path.GetFileNameWithoutExtension(learn));
                    t = FrameLearner.Learn(learn, name, log);
                }
                var all = FrameTemplate.All();
                var tn = Args.S(a, "tpl", t?.Name ?? (st.TryGetValue("tpl", out var x) ? x : ""));
                t = t ?? all.FirstOrDefault(z => z.Name == tn) ?? all.FirstOrDefault();
                if (t == null) return "图集管理：还没有图框模板（先用 learn=<图框.dwg>）。";
                if (a.TryGetValue("paper", out var paper)) { t.Paper = paper; }

                var o = new Sheets.NewOpts { Paper = t.Paper, NameRule = Args.S(a, "rule", "{图号} {图名}") };
                o.Plot.Device = Args.S(a, "device", o.Plot.Device);
                o.Plot.Ctb = Args.S(a, "ctb", o.Plot.Ctb);
                foreach (var kv in a.Where(kv => kv.Key.StartsWith("all."))) o.Defaults[kv.Key.Substring(4)] = kv.Value;
                if (a.TryGetValue("freeze", out var fz)) o.FreezeLayers = fz.Split('|').Where(s => s.Length > 0).ToList();

                if (a.TryGetValue("new", out var news))
                {
                    foreach (var item in news.Split('|').Where(s => s.Trim().Length > 0))
                    {
                        var p = item.Split(',');
                        double D(int i) => double.Parse(p[i], CultureInfo.InvariantCulture);
                        var s = new Sheets.NewSheet
                        {
                            Min = new Point3d(Math.Min(D(0), D(2)), Math.Min(D(1), D(3)), 0), Max = new Point3d(Math.Max(D(0), D(2)), Math.Max(D(1), D(3)), 0),
                            Scale = p.Length > 4 ? int.Parse(p[4]) : 0, Title = p.Length > 5 ? string.Join(",", p.Skip(5)) : "",
                        };
                        Sheets.Create(doc, t, s, o, log);
                    }
                }

                List<Zone> zones = null;
                if (a.TryGetValue("zones", out var zs)) zones = Zones.Parse(zs);
                else if (a.TryGetValue("zonelayer", out var zl)) zones = Zones.FromLayer(doc.Database, zl);
                else if (Args.B(a, "grid", false))
                {
                    var db0 = doc.Database; double mpu = Sheets.MmPerUnit(db0);
                    int gs = (int)Args.D(a, "gridscale", 500);
                    var k0 = t.PaperScale(); var ar = Sheets.Area(t, k0);
                    double side0 = 124, ov = Args.D(a, "overlap", 20);
                    var ext = ModelExtents(db0);
                    zones = Zones.Grid(db0, ext.MinPoint.X, ext.MinPoint.Y, ext.MaxPoint.X, ext.MaxPoint.Y, (ar.x1 - ar.x0 - side0) * gs / mpu, (ar.y1 - ar.y0) * gs / mpu, ov * gs / mpu, log);
                }
                if (zones != null)
                {
                    if (zones.Count == 0) log("没有分区。");
                    else
                    {
                        if (Args.B(a, "savezones", false)) Zones.Save(doc.Database, zones);
                        var zo = new ZoneOpts
                        {
                            BaseTitle = Args.S(a, "zbase", "管线综合平面图"), Scale = (int)Args.D(a, "zscale", 0), PerZoneScale = Args.B(a, "perzone", false),
                            Legend = Args.B(a, "legend", true), Index = Args.B(a, "index", true), MatchLabels = Args.B(a, "match", true),
                        };
                        Zones.CreateSheets(doc, t, zones, zo, o, log);
                    }
                }

                var rows = Sheets.Read(doc.Database, FrameTemplate.All()).Where(r => !r.TitleRef.IsNull).ToList();
                foreach (var kv in o.Defaults) foreach (var r in rows) { r.Values[kv.Key] = kv.Value; r.Dirty = true; }
                if (a.TryGetValue("number", out var rule))
                {
                    var field = Args.S(a, "numfield", "图号");
                    int start = (int)Args.D(a, "start", 1), step = (int)Args.D(a, "step", 1);
                    for (int i = 0; i < rows.Count; i++) { rows[i].Values[field] = Sheets.Number(rule, i, rows.Count, start, step, rows[i]); rows[i].Dirty = true; }
                }
                if (a.TryGetValue("sheetno", out var sno))
                    for (int i = 0; i < rows.Count; i++) { rows[i].Values["张号"] = Sheets.Number(sno, i, rows.Count, 1, 1, rows[i]); rows[i].Dirty = true; }
                if (rows.Any(r => r.Dirty) || a.ContainsKey("rename"))
                    log($"写入图签 {Sheets.Write(doc, rows, Args.S(a, "rename", ""), log)} 张");
                if (Args.B(a, "toc", false))
                {
                    rows = Sheets.Read(doc.Database, FrameTemplate.All()).Where(r => !r.TitleRef.IsNull && r.Layout != "图纸目录").ToList();
                    Sheets.Toc(doc, t, rows, o, "图纸目录", Args.S(a, "tocno", ""), log);
                }
            }
            if (a.TryGetValue("pdf", out var pdf))
            {
                var rows = Sheets.Read(doc.Database, FrameTemplate.All()).Where(r => Args.B(a, "pdfall", false) || !r.TitleRef.IsNull).ToList();
                log(Sheets.PlotPdf(doc, rows, pdf, a.TryGetValue("pdfctb", out var pc) ? new PlotOpts { Device = null, Ctb = pc } : null, log));
            }
            if (Args.B(a, "ui", false))
                using (var f = new SheetForm(doc)) Ui.Show(f, "acad-sheets");
            return "图集管理：\n" + sb.ToString().TrimEnd();
        }
    }

    /// <summary>主窗口。</summary>
    sealed class SheetForm : Form
    {
        readonly Document _doc;
        readonly Dictionary<string, string> _st;
        List<FrameTemplate> _tpls = new List<FrameTemplate>();
        List<SheetRow> _rows = new List<SheetRow>();
        FrameTemplate _tpl;

        readonly ComboBox _cbTpl = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 260 };
        readonly ComboBox _cbPaper = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 70 };
        readonly TabControl _tabs = new TabControl { Dock = DockStyle.Fill };
        readonly DataGridView _grid = NewGrid();
        readonly DataGridView _fields = NewGrid();
        readonly Label _status = new Label { Dock = DockStyle.Bottom, Height = 26, TextAlign = ContentAlignment.MiddleLeft, ForeColor = Color.DimGray, Padding = new Padding(8, 0, 0, 0) };
        // 图框页
        readonly TextBox _tbFrame = new TextBox { Width = 560 };
        readonly TextBox _tbTplName = new TextBox { Width = 200 };
        readonly Label _lbFrameInfo = new Label { AutoSize = false, Width = 1060, Height = 60, ForeColor = Color.DimGray };
        readonly TextBox[] _vpBox = Enumerable.Range(0, 4).Select(_ => new TextBox { Width = 90, TextAlign = HorizontalAlignment.Right }).ToArray();
        // 出图设置页
        readonly ComboBox _cbScale = new ComboBox { Width = 110 };
        readonly CheckBox _ckLock = new CheckBox { Text = "锁定视口（防止误缩放）", AutoSize = true, Checked = true };
        readonly CheckedListBox _lbFreeze = new CheckedListBox { Width = 360, Height = 300, CheckOnClick = true };
        readonly TextBox _tbRule = new TextBox { Width = 260 };
        readonly ComboBox _cbDevice = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 300 };
        readonly ComboBox _cbCtb = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 300 };
        readonly TextBox _tbPdf = new TextBox { Width = 520 };

        static DataGridView NewGrid() => new DataGridView
        {
            Dock = DockStyle.Fill, AllowUserToAddRows = false, AllowUserToDeleteRows = false, RowHeadersWidth = 28,
            SelectionMode = DataGridViewSelectionMode.CellSelect, BackgroundColor = Color.White, BorderStyle = BorderStyle.None,
            AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.None, ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.AutoSize,
            EditMode = DataGridViewEditMode.EditOnKeystrokeOrF2,
        };

        public SheetForm(Document doc)
        {
            _doc = doc;
            _st = Plugin.LoadSettings();
            Text = "天河落 · 图集管理 — " + Path.GetFileName(doc.Name);
            StartPosition = FormStartPosition.CenterScreen;
            Font = new System.Drawing.Font("Microsoft YaHei UI", 9f);
            AutoScaleMode = AutoScaleMode.Dpi; AutoScaleDimensions = new SizeF(96f, 96f);
            ClientSize = new Size(1280, 760);
            MinimumSize = new Size(900, 560);
            ShowInTaskbar = false; MinimizeBox = false;

            // 顶栏：模板、图幅
            var top = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 40, Padding = new Padding(8, 8, 8, 0), WrapContents = false };
            top.Controls.Add(new Label { Text = "图框模板", AutoSize = true, Margin = new Padding(0, 6, 4, 0) });
            top.Controls.Add(_cbTpl);
            top.Controls.Add(new Label { Text = "图幅", AutoSize = true, Margin = new Padding(16, 6, 4, 0) });
            _cbPaper.Items.AddRange(new object[] { "A0", "A1", "A2", "A3", "A4" });
            top.Controls.Add(_cbPaper);
            var tip = new Label { Text = "图框按外部参照插入；图名、图号等写在叠在图框格子上的图签属性里。", AutoSize = true, ForeColor = Color.DimGray, Margin = new Padding(16, 6, 0, 0) };
            top.Controls.Add(tip);

            _tabs.TabPages.Add(BuildSheetsPage());
            _tabs.TabPages.Add(BuildFramePage());
            _tabs.TabPages.Add(BuildOutputPage());
            Controls.Add(_tabs);
            Controls.Add(top);
            Controls.Add(_status);

            LoadTemplates(_st.TryGetValue("tpl", out var tn) ? tn : null);
            _cbTpl.SelectedIndexChanged += (_, __) => { _tpl = _cbTpl.SelectedItem as FrameTemplate; if (_tpl != null) { _cbPaper.SelectedItem = _tpl.Paper; } ShowTemplate(); RebuildColumns(); FillGrid(); };
            _cbPaper.SelectedIndexChanged += (_, __) => { if (_tpl != null && _cbPaper.SelectedItem is string p) _tpl.Paper = p; };
            FillOutputLists();
            Reload();
            FormClosing += (_, __) => SaveUi();
            // 自动测试：每个页签各截一张图（Ui.Show 最后再截当前页并关闭）
            var snap = Environment.GetEnvironmentVariable("THL_SNAP_DIR");
            if (!string.IsNullOrEmpty(snap))
                Shown += (_, __) =>
                {
                    for (int i = 0; i < _tabs.TabPages.Count; i++)
                    {
                        _tabs.SelectedIndex = i;
                        System.Windows.Forms.Application.DoEvents();
                        try
                        {
                            Directory.CreateDirectory(snap);
                            using (var bmp = new Bitmap(Width, Height))
                            {
                                DrawToBitmap(bmp, new Rectangle(0, 0, Width, Height));
                                bmp.Save(Path.Combine(snap, $"acad-sheets-{i + 1}.png"), System.Drawing.Imaging.ImageFormat.Png);
                            }
                        }
                        catch { }
                    }
                    _tabs.SelectedIndex = 0;
                };
        }

        void Status(string s) { _status.Text = s; Plugin.Log(s); }

        // ================================================================== 图纸列表页
        TabPage BuildSheetsPage()
        {
            var page = new TabPage("图纸列表");
            var bar = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 84, Padding = new Padding(4), WrapContents = true };
            Button B(string text, string tip, EventHandler h, int w = 0)
            {
                var b = new Button { Text = text, AutoSize = w == 0, Width = w > 0 ? w : 80, Height = 30, Margin = new Padding(3) };
                b.Click += (s, e) => { try { h(s, e); } catch (Exception ex) { Status("出错：" + ex.Message); Plugin.Log(ex.ToString()); MessageBox.Show(this, ex.Message, "图集管理", MessageBoxButtons.OK, MessageBoxIcon.Warning); } };
                _tt.SetToolTip(b, tip); bar.Controls.Add(b); return b;
            }
            B("框选新建图纸", "到模型空间框选一个或多个范围（每框一次就是一张图，回车结束），按「出图设置」里的比例自动建布局、插图框、建视口。", (_, __) => PickNew());
            B("分区出图…", "大范围总图按楼栋 / 区域切成多张图：分区来自图层 THL-分区 的闭合多段线（名称写在多段线里面），或现在到图上框选，或按图幅自动网格。每张图带图例（只列本张用到的图层）、分区索引图、接图标记。", (_, __) => ZoneDlg()).Font = new System.Drawing.Font(Font, FontStyle.Bold);
            B("按命名视图新建", "模型空间里已经存的命名视图，每个视图建一张图纸。", (_, __) => ViewsNew());
            B("给选中布局加图框", "已有布局（自己画的视口）：插入当前模板的图框和图签，并设好打印。", (_, __) => AddFrame());
            B("上移", "调整图纸顺序（写入后布局选项卡、目录、PDF 都按这个顺序）。", (_, __) => Move(-1));
            B("下移", "", (_, __) => Move(1));
            B("按图号排序", "按图号自然顺序排列。", (_, __) => SortByNumber());
            B("批量编号…", "按规则给勾选的图纸编图号和张号，如 XM-JPS-PM-{n:000}、{i:00} OF {total:00}。", (_, __) => NumberDlg());
            B("统一填写…", "项目名称、设计、校对、审核、日期等全套相同的信息一次填到勾选的图纸。", (_, __) => FillAllDlg());
            B("生成目录", "新建/更新「图纸目录」布局：带图框，表格列出全部图纸。", (_, __) => MakeToc());
            B("写入图纸", "把表格里改过的内容写到各布局的图签，并按表格顺序排布局选项卡。", (_, __) => WriteBack(), 0).Font = new System.Drawing.Font(Font, FontStyle.Bold);
            B("打印 PDF…", "勾选的图纸按顺序打成一个 PDF（每页按各自图框的图幅；书签 = 布局名）。", (_, __) => Pdf());
            B("导出 Excel", "把图里每张图的图签（天河落图签、布局里的普通文字图签、模型空间的图框块属性）导出成表格，一行一张图。", (_, __) => ExportCsv());
            B("导入 Excel…", "读改好的表格，先预览「旧 → 新」，确认后备份原文件再批量替换图名、图号等和布局名。", (_, __) => ImportCsv());
            B("打开布局", "切换到选中行的布局。", (_, __) => GoTo());
            B("刷新", "重新读取图纸里的布局和图签。", (_, __) => Reload());

            _grid.CellValueChanged += (s, e) =>
            {
                if (e.RowIndex < 0 || _filling) return;
                var r = (SheetRow)_grid.Rows[e.RowIndex].Tag;
                var col = _grid.Columns[e.ColumnIndex];
                if (col.Tag is string key) { r.Values[key] = Convert.ToString(_grid.Rows[e.RowIndex].Cells[e.ColumnIndex].Value) ?? ""; r.Dirty = true; _grid.Rows[e.RowIndex].Cells[e.ColumnIndex].Style.BackColor = Color.FromArgb(255, 246, 214); }
            };
            _grid.CurrentCellDirtyStateChanged += (_, __) => { if (_grid.CurrentCell is DataGridViewCheckBoxCell) _grid.CommitEdit(DataGridViewDataErrorContexts.Commit); };
            _grid.KeyDown += GridPaste;
            _grid.CellDoubleClick += (_, e) => { if (e.RowIndex >= 0 && e.ColumnIndex == 2) GoTo(); };
            var hint = new Label { Dock = DockStyle.Bottom, Height = 22, ForeColor = Color.DimGray, Text = "提示：可以从 Excel 复制多行多列直接 Ctrl+V 粘贴到表格；改过的格子标黄，点「写入图纸」才会写进 DWG。双击布局名跳到该布局。" };
            page.Controls.Add(_grid);
            page.Controls.Add(hint);
            page.Controls.Add(bar);
            return page;
        }

        readonly ToolTip _tt = new ToolTip();
        bool _filling;

        void LoadTemplates(string select)
        {
            _tpls = FrameTemplate.All();
            _cbTpl.Items.Clear();
            foreach (var t in _tpls) _cbTpl.Items.Add(t);
            _cbTpl.DisplayMember = "Name";
            var sel = _tpls.FirstOrDefault(t => t.Name == select) ?? _tpls.FirstOrDefault();
            _tpl = sel;
            if (sel != null) { _cbTpl.SelectedItem = sel; _cbPaper.SelectedItem = sel.Paper; }
            ShowTemplate();
        }

        void Reload()
        {
            _rows = Sheets.Read(_doc.Database, _tpls);
            RebuildColumns();
            FillGrid();
            var n = _rows.Count(r => !r.TitleRef.IsNull);
            Status($"{_rows.Count} 个布局，其中 {n} 张带天河落图签。" + (_tpl == null ? "  还没有图框模板：到「图框模板」页学习一个。" : ""));
        }

        IEnumerable<string> FieldKeys()
        {
            var keys = new List<string>();
            if (_tpl != null) keys.AddRange(_tpl.Fields.Select(f => f.Key));
            foreach (var r in _rows) foreach (var k in r.Values.Keys) if (!keys.Contains(k)) keys.Add(k);
            // 常用列放前面
            string[] first = { "图号", "图名", "比例", "张号", "版本", "日期" };
            return first.Where(keys.Contains).Concat(keys.Where(k => !first.Contains(k)));
        }

        void RebuildColumns()
        {
            _grid.Columns.Clear();
            _grid.Columns.Add(new DataGridViewCheckBoxColumn { HeaderText = "✓", Width = 32, Frozen = true });
            _grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "序", Width = 36, ReadOnly = true, Frozen = true });
            _grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "布局", Width = 200, ReadOnly = true, Frozen = true });
            _grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "视口比例", Width = 90, ReadOnly = true });
            foreach (var k in FieldKeys())
            {
                int w = k == "图名" ? 260 : k == "图号" ? 210 : k.StartsWith("修订") ? 90 : k == "项目名称" ? 220 : 90;
                _grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = k, Width = w, Tag = k, SortMode = DataGridViewColumnSortMode.NotSortable });
            }
        }

        void FillGrid()
        {
            _filling = true;
            try
            {
                var keep = _grid.Rows.Cast<DataGridViewRow>().Where(r => r.Tag is SheetRow && Equals(r.Cells[0].Value, true)).Select(r => ((SheetRow)r.Tag).LayoutId).ToHashSet();
                _grid.Rows.Clear();
                int i = 1;
                foreach (var r in _rows)
                {
                    var cells = new List<object> { keep.Count == 0 ? !r.TitleRef.IsNull : keep.Contains(r.LayoutId), i++, r.Layout, r.Scale > 0 ? "1:" + r.Scale.ToString("0.##") : "" };
                    foreach (DataGridViewColumn c in _grid.Columns) if (c.Tag is string k) cells.Add(r.V(k));
                    var idx = _grid.Rows.Add(cells.ToArray());
                    var row = _grid.Rows[idx];
                    row.Tag = r;
                    if (r.TitleRef.IsNull) { row.DefaultCellStyle.ForeColor = Color.Gray; foreach (DataGridViewCell c in row.Cells) if (c.OwningColumn.Tag != null) c.ReadOnly = true; }
                }
            }
            finally { _filling = false; }
        }

        List<SheetRow> Checked() => _grid.Rows.Cast<DataGridViewRow>().Where(r => Equals(r.Cells[0].Value, true)).Select(r => (SheetRow)r.Tag).ToList();

        void GridPaste(object sender, KeyEventArgs e)
        {
            if (!(e.Control && e.KeyCode == Keys.V) || _grid.CurrentCell == null) return;
            var text = Clipboard.GetText();
            if (string.IsNullOrEmpty(text)) return;
            var lines = text.Replace("\r", "").TrimEnd('\n').Split('\n');
            int r0 = _grid.CurrentCell.RowIndex, c0 = _grid.CurrentCell.ColumnIndex;
            for (int i = 0; i < lines.Length && r0 + i < _grid.Rows.Count; i++)
            {
                var parts = lines[i].Split('\t');
                for (int j = 0; j < parts.Length && c0 + j < _grid.Columns.Count; j++)
                {
                    var cell = _grid.Rows[r0 + i].Cells[c0 + j];
                    if (cell.ReadOnly || !(cell.OwningColumn.Tag is string)) continue;
                    cell.Value = parts[j];
                }
            }
            e.Handled = true;
            Status($"已粘贴 {lines.Length} 行。点「写入图纸」保存到 DWG。");
        }

        bool NeedTpl()
        {
            if (_tpl != null) return true;
            MessageBox.Show(this, "还没有图框模板。请先到「图框模板」页选择图框 DWG 并点「学习图框」。", "图集管理");
            _tabs.SelectedIndex = 1;
            return false;
        }

        Sheets.NewOpts Opts()
        {
            var o = new Sheets.NewOpts
            {
                Paper = _cbPaper.SelectedItem as string ?? _tpl?.Paper, LockVp = _ckLock.Checked, NameRule = _tbRule.Text.Trim().Length > 0 ? _tbRule.Text.Trim() : "{图号} {图名}",
                FreezeLayers = _lbFreeze.CheckedItems.Cast<string>().ToList(),
            };
            o.Plot.Device = _cbDevice.SelectedItem as string ?? o.Plot.Device;
            o.Plot.Ctb = _cbCtb.SelectedItem as string ?? o.Plot.Ctb;
            // 全套相同的字段：沿用已有图纸的值（新图纸自动带上项目名、设计人……）
            var src = _rows.FirstOrDefault(r => !r.TitleRef.IsNull);
            if (_tpl != null) foreach (var f in _tpl.Fields.Where(f => f.Global))
                {
                    var v = src?.V(f.Key);
                    if (string.IsNullOrEmpty(v) && _st.TryGetValue("g." + f.Key, out var sv)) v = sv;
                    if (!string.IsNullOrEmpty(v)) o.Defaults[f.Key] = v;
                }
            return o;
        }

        int ScaleChoice()
        {
            var s = (_cbScale.Text ?? "").Replace("1:", "").Trim();
            return int.TryParse(s, out var n) ? n : 0;
        }

        void PickNew()
        {
            if (!NeedTpl()) return;
            var ed = _doc.Editor;
            var picks = new List<Sheets.NewSheet>();
            using (ed.StartUserInteraction(Handle))
            {
                using (_doc.LockDocument())
                    if (LayoutManager.Current.CurrentLayout != "Model") LayoutManager.Current.CurrentLayout = "Model";
                int scale = ScaleChoice();
                while (true)
                {
                    var p1 = ed.GetPoint(new PromptPointOptions($"\n第 {picks.Count + 1} 张图：框选范围的第一个角点（回车结束）") { AllowNone = true });
                    if (p1.Status != PromptStatus.OK) break;
                    var p2 = ed.GetCorner(new PromptCornerOptions("\n对角点", p1.Value));
                    if (p2.Status != PromptStatus.OK) break;
                    var tt = ed.GetString(new PromptStringOptions("\n图名（可回车先空着）") { AllowSpaces = true });
                    if (tt.Status == PromptStatus.Cancel) break;
                    var a = p1.Value; var b = p2.Value;
                    picks.Add(new Sheets.NewSheet { Min = new Point3d(Math.Min(a.X, b.X), Math.Min(a.Y, b.Y), 0), Max = new Point3d(Math.Max(a.X, b.X), Math.Max(a.Y, b.Y), 0), Scale = scale, Title = tt.StringResult?.Trim() ?? "" });
                }
            }
            CreateSheets(picks);
        }

        void CreateSheets(List<Sheets.NewSheet> picks)
        {
            if (picks.Count == 0) { Status("没有框选范围。"); return; }
            var o = Opts();
            var msgs = new List<string>();
            using (_doc.LockDocument())
                foreach (var s in picks) Sheets.Create(_doc, _tpl, s, o, m => msgs.Add(m));
            _doc.Editor.Regen();
            Reload();
            Status($"新建 {picks.Count} 张图纸。" + string.Join(" ", msgs.Where(m => m.Contains("放不下") || m.Contains("失败") || m.Contains("没保存"))));
        }

        void ZoneDlg()
        {
            if (!NeedTpl()) return;
            using (var f = Ui.NewForm("分区出图（按楼栋 / 区域切图）", 620, 470))
            {
                int y = 14;
                Ui.Label(f, "分区从哪来", 14, y); y += 26;
                var r1 = new RadioButton { Text = $"图层「{Zones.Layer}」上的闭合多段线（名称 = 多段线里面同图层的文字）", Left = 28, Top = y, AutoSize = true, Checked = true }; f.Controls.Add(r1); y += 28;
                var r2 = new RadioButton { Text = "现在到图上框选（每框一块、输名称，回车结束；可存到图层下次直接用）", Left = 28, Top = y, AutoSize = true }; f.Controls.Add(r2); y += 28;
                var r3 = new RadioButton { Text = "按图幅自动网格切块（比例用下面的统一比例，搭接 20mm，空块跳过）", Left = 28, Top = y, AutoSize = true }; f.Controls.Add(r3); y += 36;
                Ui.Label(f, "图名 = 分区名 +", 14, y + 3);
                var tb = new TextBox { Left = 130, Top = y, Width = 260, Text = _st.TryGetValue("zbase", out var zb) ? zb : "管线综合平面图" }; f.Controls.Add(tb); y += 36;
                Ui.Label(f, "比例", 14, y + 3);
                var cb = new ComboBox { Left = 130, Top = y, Width = 160 };
                cb.Items.Add("统一·自动"); cb.Items.Add("各块各自自动"); foreach (var sc in Sheets.StdScales.Where(x => x >= 50)) cb.Items.Add("1:" + sc);
                cb.Text = _st.TryGetValue("zscale", out var zsc) ? zsc : "统一·自动"; f.Controls.Add(cb);
                Ui.Label(f, "统一·自动 = 全套同一比例（能放下最大那块）", 300, y + 3, 0, Color.DimGray); y += 38;
                var cL = Ui.Check(f, "图例（只列本张图里实际出现的图层；文字在图例库 legend.txt 里改）", 28, y, true); y += 28;
                var cI = Ui.Check(f, "分区索引图（总图缩略，当前块红框）", 28, y, true); y += 28;
                var cM = Ui.Check(f, "接图标记（相邻分区在视口边写「接 XX」）", 28, y, true); y += 28;
                var cS = Ui.Check(f, "框选的分区存到图层 THL-分区（不打印）", 28, y, true); y += 36;
                var lib = Ui.Btn(f, "打开图例库", 14, y, 110);
                lib.Click += (_, __) => { LegendLib.Load(null); try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(LegendLib.File) { UseShellExecute = true }); } catch { } };
                var ok = Ui.Btn(f, "开始出图", 410, y, 96); ok.DialogResult = DialogResult.OK; f.AcceptButton = ok;
                var cancel = Ui.Btn(f, "取消", 514, y, 90); cancel.DialogResult = DialogResult.Cancel;
                if (f.ShowDialog(this) != DialogResult.OK) return;
                _st["zbase"] = tb.Text; _st["zscale"] = cb.Text;
                var zo = new ZoneOpts { BaseTitle = tb.Text.Trim(), Legend = cL.Checked, Index = cI.Checked, MatchLabels = cM.Checked };
                if (cb.Text == "各块各自自动") zo.PerZoneScale = true;
                else if (cb.Text.StartsWith("1:") && int.TryParse(cb.Text.Substring(2), out var n)) zo.Scale = n;
                List<Zone> zones = null;
                var msgs = new List<string>();
                if (r1.Checked)
                {
                    zones = Zones.FromLayer(_doc.Database, Zones.Layer);
                    if (zones.Count == 0) { MessageBox.Show(this, $"图层「{Zones.Layer}」上没有闭合多段线。\n先在模型空间用 PLINE / RECTANG 在这个图层画出各楼栋的范围，并在每个范围里写上名称（如 1号楼），或选「现在到图上框选」。", "分区出图"); return; }
                }
                else if (r2.Checked)
                {
                    zones = new List<Zone>();
                    var ed = _doc.Editor;
                    using (ed.StartUserInteraction(Handle))
                    {
                        using (_doc.LockDocument()) if (LayoutManager.Current.CurrentLayout != "Model") LayoutManager.Current.CurrentLayout = "Model";
                        while (true)
                        {
                            var p1 = ed.GetPoint(new PromptPointOptions($"\n第 {zones.Count + 1} 块：第一个角点（回车结束）") { AllowNone = true });
                            if (p1.Status != PromptStatus.OK) break;
                            var p2 = ed.GetCorner(new PromptCornerOptions("\n对角点", p1.Value));
                            if (p2.Status != PromptStatus.OK) break;
                            var nm = ed.GetString(new PromptStringOptions($"\n分区名称 <分区{zones.Count + 1}>") { AllowSpaces = true });
                            if (nm.Status == PromptStatus.Cancel) break;
                            var name = string.IsNullOrWhiteSpace(nm.StringResult) ? "分区" + (zones.Count + 1) : nm.StringResult.Trim();
                            zones.Add(Zone.Rect(name, p1.Value.X, p1.Value.Y, p2.Value.X, p2.Value.Y));
                        }
                    }
                    if (zones.Count > 0 && cS.Checked) using (_doc.LockDocument()) Zones.Save(_doc.Database, zones);
                }
                else
                {
                    var db = _doc.Database; double mpu = Sheets.MmPerUnit(db);
                    int gs = zo.Scale > 0 ? zo.Scale : 500;
                    var ar = Sheets.Area(_tpl, _tpl.PaperScale(_cbPaper.SelectedItem as string));
                    var ext = Plugin.ModelExtents(db);
                    zones = Zones.Grid(db, ext.MinPoint.X, ext.MinPoint.Y, ext.MaxPoint.X, ext.MaxPoint.Y, (ar.x1 - ar.x0 - zo.SideW - 4) * gs / mpu, (ar.y1 - ar.y0) * gs / mpu, 20.0 * gs / mpu, m => msgs.Add(m));
                    zo.Scale = gs;
                }
                if (zones == null || zones.Count == 0) { Status("没有分区。"); return; }
                Cursor = Cursors.WaitCursor;
                try { using (_doc.LockDocument()) Zones.CreateSheets(_doc, _tpl, zones, zo, Opts(), m => msgs.Add(m)); }
                finally { Cursor = Cursors.Default; }
                _doc.Editor.Regen();
                Reload();
                Status($"分区出图：{zones.Count} 张。" + string.Join(" ", msgs.Where(m => m.Contains("统一比例") || m.Contains("放不下") || m.Contains("图例"))));
            }
        }

        void ExportCsv()
        {
            var msgs = new List<string>();
            var rows = ExcelSync.Scan(_doc, _tpls, m => msgs.Add(m));
            if (rows.Count == 0) { MessageBox.Show(this, "这张图里没认出图签。", "图集管理"); return; }
            using (var d = new SaveFileDialog { Filter = "Excel 可打开的 CSV（UTF-8）|*.csv", FileName = Path.GetFileNameWithoutExtension(_doc.Name) + "_图签.csv" })
            {
                if (d.ShowDialog(this) != DialogResult.OK) return;
                Status(ExcelSync.Export(rows, d.FileName));
                try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(d.FileName) { UseShellExecute = true }); } catch { }
            }
        }

        void ImportCsv()
        {
            string path;
            using (var d = new OpenFileDialog { Filter = "CSV|*.csv", Title = "选择改好的图签表（导出的那份改完另存为 CSV UTF-8）" })
            {
                if (d.ShowDialog(this) != DialogResult.OK) return;
                path = d.FileName;
            }
            var msgs = new List<string>();
            var rows = ExcelSync.Scan(_doc, _tpls, m => msgs.Add(m));
            var changes = ExcelSync.Diff(rows, path);
            if (changes.Count == 0) { MessageBox.Show(this, "表格和图里一样，没有要改的。", "图集管理"); return; }
            using (var f = new Wizard.ImportPreviewForm(changes.Select(c => (c.Row.Locator, c.Field, c.Old, c.New)).ToList()))
            {
                if (f.ShowDialog(this) != DialogResult.OK) return;
                var sel = f.Selected.Select(i => changes[i]).ToList();
                var res = ExcelSync.Apply(_doc, sel, m => msgs.Add(m));
                Reload();
                Status(res.Replace("\n", " "));
                MessageBox.Show(this, res + (msgs.Any(m => m.StartsWith("跳过")) ? "\n\n" + string.Join("\n", msgs.Where(m => m.StartsWith("跳过")).Take(10)) : ""), "图集管理");
            }
        }

        void ViewsNew()
        {
            if (!NeedTpl()) return;
            var views = new List<(string name, Point3d min, Point3d max)>();
            using (var tr = _doc.Database.TransactionManager.StartOpenCloseTransaction())
            {
                var vt = (ViewTable)tr.GetObject(_doc.Database.ViewTableId, OpenMode.ForRead);
                foreach (ObjectId id in vt)
                {
                    var v = (ViewTableRecord)tr.GetObject(id, OpenMode.ForRead);
                    if (v.IsPaperspaceView) continue;
                    // 平面视图：视图中心就是 WCS 坐标（目标在原点、看向 -Z 时）
                    var c = v.CenterPoint; var t = v.Target;
                    var cx = c.X + t.X; var cy = c.Y + t.Y;
                    views.Add((v.Name, new Point3d(cx - v.Width / 2, cy - v.Height / 2, 0), new Point3d(cx + v.Width / 2, cy + v.Height / 2, 0)));
                }
                tr.Commit();
            }
            if (views.Count == 0) { MessageBox.Show(this, "模型空间里没有命名视图（VIEW 命令可以保存视图）。", "图集管理"); return; }
            using (var f = Ui.NewForm("按命名视图新建图纸", 420, 430))
            {
                Ui.Label(f, "勾选视图，每个视图建一张图纸（图名 = 视图名）：", 12, 12);
                var lb = new CheckedListBox { Left = 12, Top = 38, Width = 396, Height = 330, CheckOnClick = true };
                foreach (var v in views) lb.Items.Add(v.name, true);
                f.Controls.Add(lb);
                var ok = Ui.Btn(f, "新建", 220, 384); ok.DialogResult = DialogResult.OK; f.AcceptButton = ok;
                var cancel = Ui.Btn(f, "取消", 318, 384); cancel.DialogResult = DialogResult.Cancel;
                if (f.ShowDialog(this) != DialogResult.OK) return;
                var sel = lb.CheckedItems.Cast<string>().ToHashSet();
                CreateSheets(views.Where(v => sel.Contains(v.name)).Select(v => new Sheets.NewSheet { Min = v.min, Max = v.max, Scale = ScaleChoice(), Title = v.name }).ToList());
            }
        }

        void AddFrame()
        {
            if (!NeedTpl()) return;
            var sel = Checked().Where(r => r.TitleRef.IsNull).ToList();
            if (sel.Count == 0) { MessageBox.Show(this, "请勾选还没有天河落图签的布局（灰色行）。", "图集管理"); return; }
            var msgs = new List<string>();
            using (_doc.LockDocument())
                foreach (var r in sel) Sheets.AddFrameToLayout(_doc, r.LayoutId, _tpl, Opts().Plot, _cbPaper.SelectedItem as string, m => msgs.Add(m));
            Reload();
            Status($"给 {sel.Count} 个布局加了图框。图框按纸面 mm 放在原点，原有视口没动，可能需要自己挪到图框里。");
        }

        void Move(int d)
        {
            var cur = _grid.CurrentCell?.RowIndex ?? -1;
            if (cur < 0) return;
            var j = cur + d;
            if (j < 0 || j >= _rows.Count) return;
            var r = _rows[cur]; _rows.RemoveAt(cur); _rows.Insert(j, r);
            FillGrid();
            _grid.CurrentCell = _grid.Rows[j].Cells[Math.Max(2, _grid.CurrentCell?.ColumnIndex ?? 2)];
            Status("顺序已调整，点「写入图纸」生效。");
        }

        void SortByNumber()
        {
            _rows = _rows.OrderBy(r => r.TitleRef.IsNull ? 1 : 0).ThenBy(r => NatKey(r.V("图号")), StringComparer.Ordinal).ToList();
            FillGrid();
            Status("已按图号排序，点「写入图纸」生效。");
        }

        static string NatKey(string s) => System.Text.RegularExpressions.Regex.Replace(s ?? "", @"\d+", m => m.Value.PadLeft(10, '0'));

        void NumberDlg()
        {
            var rows = Checked().Where(r => !r.TitleRef.IsNull).ToList();
            if (rows.Count == 0) { MessageBox.Show(this, "请先勾选要编号的图纸。", "图集管理"); return; }
            using (var f = Ui.NewForm("批量编号", 560, 330))
            {
                var tt = new ToolTip();
                Ui.Label(f, $"按表格顺序给勾选的 {rows.Count} 张图纸编号。", 14, 12);
                Ui.Label(f, "图号规则", 14, 48);
                var tbRule = new TextBox { Left = 100, Top = 45, Width = 440, Text = _st.TryGetValue("numrule", out var nr) ? nr : (_tpl?.Note.Length > 0 ? "" : "{n:000}") };
                f.Controls.Add(tbRule);
                Ui.Label(f, "起始号", 14, 84); var tbStart = Ui.Num(f, 1, 100, 84, 60);
                Ui.Label(f, "步长", 180, 84); var tbStep = Ui.Num(f, 1, 220, 84, 50);
                Ui.Label(f, "张号规则", 14, 120);
                var tbSheet = new TextBox { Left = 100, Top = 117, Width = 200, Text = _st.TryGetValue("sheetrule", out var sr) ? sr : "{i:00} OF {total:00}" };
                f.Controls.Add(tbSheet);
                var ckSheet = Ui.Check(f, "同时编张号", 316, 118, true);
                Ui.Label(f, "{n} {n:000} = 起始号起递增（:000 = 补零到 3 位）；{i} {i:00} = 第几张；{total} = 共几张；{图名} 等 = 字段值。\n例：XM-JPS-PM-{n:000}、第{i}张 共{total}张", 14, 156, 530, Color.DimGray).Height = 48;
                if (_tpl != null && _tpl.Note.Length > 0) Ui.Label(f, "图框里的图号说明：" + _tpl.Note.Replace("\n", " "), 14, 206, 530, Color.SteelBlue).Height = 40;
                var preview = Ui.Label(f, "", 14, 250, 530, Color.DarkGreen);
                void Pv()
                {
                    int.TryParse(tbStart.Text, out var s0); int.TryParse(tbStep.Text, out var st);
                    preview.Text = "预览：" + string.Join("，", rows.Take(3).Select((r, i) => Sheets.Number(tbRule.Text, i, rows.Count, s0, st == 0 ? 1 : st, r) + (ckSheet.Checked ? " / " + Sheets.Number(tbSheet.Text, i, rows.Count, 1, 1, r) : ""))) + (rows.Count > 3 ? " …" : "");
                }
                foreach (var c in new Control[] { tbRule, tbStart, tbStep, tbSheet }) c.TextChanged += (_, __) => Pv();
                ckSheet.CheckedChanged += (_, __) => Pv();
                Pv();
                var ok = Ui.Btn(f, "编号", 350, 288); ok.DialogResult = DialogResult.OK; f.AcceptButton = ok;
                var cancel = Ui.Btn(f, "取消", 448, 288); cancel.DialogResult = DialogResult.Cancel;
                if (f.ShowDialog(this) != DialogResult.OK) return;
                int.TryParse(tbStart.Text, out var start); int.TryParse(tbStep.Text, out var step); if (step == 0) step = 1;
                for (int i = 0; i < rows.Count; i++)
                {
                    rows[i].Values["图号"] = Sheets.Number(tbRule.Text, i, rows.Count, start, step, rows[i]);
                    if (ckSheet.Checked) rows[i].Values["张号"] = Sheets.Number(tbSheet.Text, i, rows.Count, 1, 1, rows[i]);
                    rows[i].Dirty = true;
                }
                _st["numrule"] = tbRule.Text; _st["sheetrule"] = tbSheet.Text;
                FillGrid();
                Status($"已编号 {rows.Count} 张，点「写入图纸」生效。");
            }
        }

        void FillAllDlg()
        {
            var rows = Checked().Where(r => !r.TitleRef.IsNull).ToList();
            if (rows.Count == 0) { MessageBox.Show(this, "请先勾选图纸。", "图集管理"); return; }
            var keys = FieldKeys().Where(k => k != "图号" && k != "图名" && k != "比例" && k != "张号").ToList();
            var globals = _tpl?.Fields.Where(f => f.Global).Select(f => f.Key).ToHashSet() ?? new HashSet<string>();
            using (var f = Ui.NewForm($"统一填写（{rows.Count} 张）", 560, 560))
            {
                Ui.Label(f, "只填要改的格子；留空的字段不动。打勾的字段会记住，下次新建图纸自动带上。", 12, 10, 530, Color.DimGray);
                var g = new DataGridView { Left = 12, Top = 44, Width = 536, Height = 450, AllowUserToAddRows = false, RowHeadersVisible = false, BackgroundColor = Color.White };
                g.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "字段", Width = 150, ReadOnly = true });
                g.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "值", Width = 300 });
                g.Columns.Add(new DataGridViewCheckBoxColumn { HeaderText = "记住", Width = 60 });
                foreach (var k in keys.OrderBy(k => globals.Contains(k) ? 0 : 1).ThenBy(k => k.StartsWith("修订") ? 1 : 0))
                {
                    var common = rows.Select(r => r.V(k)).Distinct().ToList();
                    g.Rows.Add(k, common.Count == 1 ? common[0] : "", globals.Contains(k));
                }
                f.Controls.Add(g);
                var ok = Ui.Btn(f, "填入", 350, 512); ok.DialogResult = DialogResult.OK;
                var cancel = Ui.Btn(f, "取消", 448, 512); cancel.DialogResult = DialogResult.Cancel;
                if (f.ShowDialog(this) != DialogResult.OK) return;
                int n = 0;
                foreach (DataGridViewRow gr in g.Rows)
                {
                    var k = (string)gr.Cells[0].Value; var v = Convert.ToString(gr.Cells[1].Value) ?? "";
                    if (v.Length == 0) continue;
                    foreach (var r in rows) if (r.V(k) != v) { r.Values[k] = v; r.Dirty = true; n++; }
                    if (Equals(gr.Cells[2].Value, true)) _st["g." + k] = v;
                }
                FillGrid();
                Status($"改了 {n} 个格子，点「写入图纸」生效。");
            }
        }

        void WriteBack()
        {
            _grid.EndEdit();
            var rule = _st.TryGetValue("renameOnWrite", out var rw) && rw == "1" ? (_tbRule.Text.Trim().Length > 0 ? _tbRule.Text.Trim() : "{图号} {图名}") : "";
            var n = Sheets.Write(_doc, _rows, rule, Status);
            _doc.Editor.Regen();
            Reload();
            Status($"已写入 {n} 张图纸的图签，布局顺序已按表格排好。");
        }

        void MakeToc()
        {
            if (!NeedTpl()) return;
            if (_rows.Any(r => r.Dirty)) WriteBack();
            var rows = _rows.Where(r => !r.TitleRef.IsNull && r.Layout != "图纸目录").ToList();
            var msgs = new List<string>();
            using (_doc.LockDocument()) Sheets.Toc(_doc, _tpl, rows, Opts(), "图纸目录", _st.TryGetValue("tocno", out var tn) ? tn : "", m => msgs.Add(m));
            Reload();
            Status(msgs.LastOrDefault() ?? "目录已生成。");
        }

        void GoTo()
        {
            var r = _grid.CurrentRow?.Tag as SheetRow;
            if (r == null) return;
            using (_doc.LockDocument()) LayoutManager.Current.CurrentLayout = r.Layout;
            Status("已切换到布局「" + r.Layout + "」。");
        }

        void Pdf()
        {
            var rows = Checked();
            if (rows.Count == 0) { MessageBox.Show(this, "请先勾选要打印的图纸。", "图集管理"); return; }
            if (_rows.Any(r => r.Dirty)) WriteBack();
            var path = _tbPdf.Text.Trim();
            if (path.Length == 0)
            {
                var dir = Path.GetDirectoryName(_doc.Database.Filename);
                if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) dir = Environment.GetFolderPath(Environment.SpecialFolder.Desktop);
                path = Path.Combine(dir, Path.GetFileNameWithoutExtension(_doc.Name) + "_图集.pdf");
            }
            if (!Path.GetExtension(path).Equals(".pdf", StringComparison.OrdinalIgnoreCase)) path += ".pdf";
            var o = new PlotOpts { Device = _cbDevice.SelectedItem as string, Ctb = _cbCtb.SelectedItem as string };
            string res;
            using (_doc.Editor.StartUserInteraction(Handle)) res = Sheets.PlotPdf(_doc, rows.Select(r => _rows.First(x => x.LayoutId == r.LayoutId)).ToList(), path, o, Status);
            Status(res);
            if (File.Exists(path) && MessageBox.Show(this, res + "\n\n现在打开吗？", "图集管理", MessageBoxButtons.YesNo) == DialogResult.Yes)
                try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true }); } catch { }
        }

        // ================================================================== 图框模板页
        TabPage BuildFramePage()
        {
            var page = new TabPage("图框模板");
            var top = new Panel { Dock = DockStyle.Top, Height = 150 };
            Ui.Label(top, "图框 DWG", 10, 14);
            _tbFrame.Left = 86; _tbFrame.Top = 10; top.Controls.Add(_tbFrame);
            var bb = Ui.Btn(top, "浏览…", 656, 8, 70);
            bb.Click += (_, __) =>
            {
                using (var d = new OpenFileDialog { Filter = "图框 DWG|*.dwg", Title = "选择图框 DWG（会复制一份去掉示例值的干净图框，不改原文件）" })
                    if (d.ShowDialog(this) == DialogResult.OK) { _tbFrame.Text = d.FileName; if (_tbTplName.Text.Trim().Length == 0) _tbTplName.Text = Path.GetFileNameWithoutExtension(d.FileName); }
            };
            Ui.Label(top, "模板名", 740, 14);
            _tbTplName.Left = 794; _tbTplName.Top = 10; top.Controls.Add(_tbTplName);
            var learn = Ui.Btn(top, "学习图框", 1004, 8, 110);
            learn.Font = new System.Drawing.Font(Font, FontStyle.Bold);
            learn.Click += (_, __) => Learn();
            _lbFrameInfo.Left = 10; _lbFrameInfo.Top = 46; top.Controls.Add(_lbFrameInfo);
            Ui.Label(top, "视口可用区（图框坐标）  左下 X", 10, 116);
            int x = 200;
            string[] cap = { "", "Y", "右上 X", "Y" };
            for (int i = 0; i < 4; i++)
            {
                if (i > 0) { Ui.Label(top, cap[i], x, 116); x += cap[i].Length * 9 + 14; }
                _vpBox[i].Left = x; _vpBox[i].Top = 112; top.Controls.Add(_vpBox[i]); x += 100;
            }
            var save = Ui.Btn(top, "保存模板", 1004, 108, 110);
            save.Click += (_, __) => SaveTemplate();
            var hint = new Label { Dock = DockStyle.Bottom, Height = 40, ForeColor = Color.DimGray, Text = "学习规则：按标签（图号/DRAWING NO、图名/TITLE、比例/SCALE、设计、校对、审核、日期……）找格子，格子里原来的示例文字 → 字段（位置、字高、对齐照抄），干净图框里删掉示例值。\n识别不准的可以直接改表格里的 X / Y / 字高 / 对齐（BL 左下、MC 正中、TL 左上…），「全套相同」打勾的字段新建图纸时自动沿用。" };
            _fields.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "字段（属性标记）", Width = 150 });
            _fields.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "图框上的标签", Width = 150, ReadOnly = true });
            _fields.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "示例值", Width = 260, ReadOnly = true });
            foreach (var h in new[] { "X", "Y", "字高" }) _fields.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = h, Width = 90 });
            _fields.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "对齐", Width = 50 });
            _fields.Columns.Add(new DataGridViewCheckBoxColumn { HeaderText = "多行", Width = 50 });
            _fields.Columns.Add(new DataGridViewCheckBoxColumn { HeaderText = "全套相同", Width = 70 });
            _fields.AllowUserToDeleteRows = true;
            page.Controls.Add(_fields);
            page.Controls.Add(hint);
            page.Controls.Add(top);
            return page;
        }

        void ShowTemplate()
        {
            _fields.Rows.Clear();
            if (_tpl == null) { _lbFrameInfo.Text = "还没有模板：选择图框 DWG，点「学习图框」。"; return; }
            _tbFrame.Text = _tpl.SourceDwg; _tbTplName.Text = _tpl.Name;
            var k = _tpl.PaperScale();
            _lbFrameInfo.Text = $"外框 {_tpl.W:0.##} × {_tpl.H:0.##}（图框单位）→ {_tpl.Paper} {_tpl.PaperMm().w:0}×{_tpl.PaperMm().h:0} mm，缩放 {k:0.####}；视口可用区 {(_tpl.VX1 - _tpl.VX0) * k:0} × {(_tpl.VY1 - _tpl.VY0) * k:0} mm。字段 {_tpl.Fields.Count} 个。\n干净图框：{_tpl.CleanDwg}"
                                + (_tpl.Note.Length > 0 ? "\n图框外的说明：" + _tpl.Note.Replace("\n", " ") : "");
            var vals = new[] { _tpl.VX0, _tpl.VY0, _tpl.VX1, _tpl.VY1 };
            for (int i = 0; i < 4; i++) _vpBox[i].Text = vals[i].ToString("0.###", CultureInfo.InvariantCulture);
            foreach (var f in _tpl.Fields)
                _fields.Rows.Add(f.Key, f.Label.Replace("\n", " "), f.Sample.Replace("\n", " ⏎ "), f.X.ToString("0.###", CultureInfo.InvariantCulture), f.Y.ToString("0.###", CultureInfo.InvariantCulture), f.H.ToString("0.###", CultureInfo.InvariantCulture), f.Just, f.Multi, f.Global);
        }

        void Learn()
        {
            var path = _tbFrame.Text.Trim();
            if (!File.Exists(path)) { MessageBox.Show(this, "请选择图框 DWG。", "图集管理"); return; }
            var name = _tbTplName.Text.Trim(); if (name.Length == 0) name = Path.GetFileNameWithoutExtension(path);
            var msgs = new List<string>();
            Cursor = Cursors.WaitCursor;
            try { var t = FrameLearner.Learn(path, name, m => msgs.Add(m)); LoadTemplates(t.Name); RebuildColumns(); FillGrid(); }
            finally { Cursor = Cursors.Default; }
            Status("学习完成：" + string.Join("；", msgs.Take(3)));
        }

        void SaveTemplate()
        {
            if (_tpl == null) return;
            _fields.EndEdit();
            double P(string s, double d) => double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var v) ? v : d;
            var old = _tpl.Fields.ToDictionary(f => f.Key + "|" + f.Label, f => f);
            var list = new List<FieldDef>();
            for (int i = 0; i < _fields.Rows.Count; i++)
            {
                var r = _fields.Rows[i];
                var src = i < _tpl.Fields.Count ? _tpl.Fields[i] : new FieldDef();
                src.Key = Convert.ToString(r.Cells[0].Value)?.Trim() ?? src.Key;
                src.X = P(Convert.ToString(r.Cells[3].Value), src.X); src.Y = P(Convert.ToString(r.Cells[4].Value), src.Y); src.H = P(Convert.ToString(r.Cells[5].Value), src.H);
                src.Just = (Convert.ToString(r.Cells[6].Value) ?? "BL").Trim().ToUpperInvariant();
                src.Multi = Equals(r.Cells[7].Value, true); src.Global = Equals(r.Cells[8].Value, true);
                if (src.Key.Length > 0) list.Add(src);
            }
            _tpl.Fields = list;
            _tpl.VX0 = P(_vpBox[0].Text, _tpl.VX0); _tpl.VY0 = P(_vpBox[1].Text, _tpl.VY0); _tpl.VX1 = P(_vpBox[2].Text, _tpl.VX1); _tpl.VY1 = P(_vpBox[3].Text, _tpl.VY1);
            if (_cbPaper.SelectedItem is string p) _tpl.Paper = p;
            _tpl.Save();
            // 图里已有的图签块按新模板同步
            using (_doc.LockDocument())
            using (var tr = _doc.Database.TransactionManager.StartTransaction())
            {
                var bt = (BlockTable)tr.GetObject(_doc.Database.BlockTableId, OpenMode.ForRead);
                if (bt.Has(Sheets.TitlePrefix + FrameTemplate.SafeName(_tpl.Name))) Sheets.EnsureTitleDef(_doc.Database, tr, _tpl);
                tr.Commit();
            }
            ShowTemplate(); Reload();
            Status("模板已保存：" + _tpl.File);
        }

        // ================================================================== 出图设置页
        TabPage BuildOutputPage()
        {
            var page = new TabPage("出图设置");
            var p = new Panel { Dock = DockStyle.Fill, AutoScroll = true };
            int y = 14;
            Ui.Label(p, "视口", 12, y).Font = new System.Drawing.Font(Font, FontStyle.Bold); y += 30;
            Ui.Label(p, "比例 1:", 24, y + 3);
            _cbScale.Left = 84; _cbScale.Top = y; p.Controls.Add(_cbScale);
            _cbScale.Items.Add("自动"); foreach (var s in Sheets.StdScales.Where(s => s >= 20)) _cbScale.Items.Add("1:" + s);
            _cbScale.Text = _st.TryGetValue("scale", out var sc) ? sc : "1:100";
            Ui.Label(p, "自动 = 框选范围能放进图框可用区的最小标准比例。模型单位按图纸的 INSUNITS（mm / m）换算。", 210, y + 3, 0, Color.DimGray);
            y += 34;
            _ckLock.Left = 24; _ckLock.Top = y; _ckLock.Checked = !_st.TryGetValue("lock", out var lk) || lk == "1"; p.Controls.Add(_ckLock); y += 32;
            Ui.Label(p, "布局名规则", 24, y + 3);
            _tbRule.Left = 110; _tbRule.Top = y; _tbRule.Text = _st.TryGetValue("rule", out var ru) ? ru : "{图号} {图名}"; p.Controls.Add(_tbRule);
            var ckRen = Ui.Check(p, "写入图纸时按规则改布局名（PDF 书签 = 布局名）", 390, y + 2, _st.TryGetValue("renameOnWrite", out var rw) && rw == "1");
            ckRen.CheckedChanged += (_, __) => _st["renameOnWrite"] = ckRen.Checked ? "1" : "0";
            y += 36;
            Ui.Label(p, "新视口里冻结这些图层（如底图、辅助线）：", 24, y); y += 24;
            _lbFreeze.Left = 24; _lbFreeze.Top = y; p.Controls.Add(_lbFreeze);
            int y2 = y - 24;
            Ui.Label(p, "打印", 430, y2 - 4).Font = new System.Drawing.Font(Font, FontStyle.Bold); y2 += 26;
            Ui.Label(p, "打印机", 442, y2 + 3); _cbDevice.Left = 520; _cbDevice.Top = y2; p.Controls.Add(_cbDevice); y2 += 36;
            Ui.Label(p, "打印样式", 442, y2 + 3); _cbCtb.Left = 520; _cbCtb.Top = y2; p.Controls.Add(_cbCtb); y2 += 36;
            Ui.Label(p, "PDF 文件", 442, y2 + 3); _tbPdf.Left = 520; _tbPdf.Top = y2; _tbPdf.Width = 440; p.Controls.Add(_tbPdf);
            var bp = Ui.Btn(p, "浏览…", 968, y2 - 2, 70);
            bp.Click += (_, __) => { using (var d = new SaveFileDialog { Filter = "PDF|*.pdf", FileName = Path.GetFileNameWithoutExtension(_doc.Name) + "_图集.pdf" }) if (d.ShowDialog(this) == DialogResult.OK) _tbPdf.Text = d.FileName; };
            y2 += 34;
            Ui.Label(p, "留空 = 图纸同文件夹「<图名>_图集.pdf」。勾选的图纸按列表顺序合成一个 PDF；每页用各布局的图幅（图框建图时已设好：\n窗口 = 图框外框、1:1、居中）。打印机 / 样式在这里改了会覆盖布局里的设置；新建图纸也用这里的打印机和样式。", 442, y2, 600, Color.DimGray).Height = 90;
            page.Controls.Add(p);
            return page;
        }

        void FillOutputLists()
        {
            try
            {
                var psv = PlotSettingsValidator.Current;
                foreach (string d in psv.GetPlotDeviceList()) _cbDevice.Items.Add(d);
                foreach (string c in psv.GetPlotStyleSheetList()) _cbCtb.Items.Add(c);
            }
            catch { }
            void Sel(ComboBox cb, string key, params string[] prefs)
            {
                var want = _st.TryGetValue(key, out var v) ? new[] { v }.Concat(prefs) : prefs;
                foreach (var w in want) { var it = cb.Items.Cast<string>().FirstOrDefault(i => i.Equals(w, StringComparison.OrdinalIgnoreCase)); if (it != null) { cb.SelectedItem = it; return; } }
                if (cb.Items.Count > 0) cb.SelectedIndex = 0;
            }
            Sel(_cbDevice, "device", "DWG To PDF.pc3", "AutoCAD PDF (General Documentation).pc3");
            Sel(_cbCtb, "ctb", "monochrome.ctb", "acad.ctb");
            _tbPdf.Text = "";
            using (var tr = _doc.Database.TransactionManager.StartOpenCloseTransaction())
            {
                var lt = (LayerTable)tr.GetObject(_doc.Database.LayerTableId, OpenMode.ForRead);
                var saved = _st.TryGetValue("freeze", out var fz) ? fz.Split('|').ToHashSet() : new HashSet<string>();
                foreach (ObjectId id in lt)
                {
                    var l = (LayerTableRecord)tr.GetObject(id, OpenMode.ForRead);
                    if (l.IsDependent) continue;
                    _lbFreeze.Items.Add(l.Name, saved.Contains(l.Name));
                }
                tr.Commit();
            }
        }

        void SaveUi()
        {
            if (_tpl != null) _st["tpl"] = _tpl.Name;
            _st["scale"] = _cbScale.Text;
            _st["lock"] = _ckLock.Checked ? "1" : "0";
            _st["rule"] = _tbRule.Text;
            if (_cbDevice.SelectedItem is string d) _st["device"] = d;
            if (_cbCtb.SelectedItem is string c) _st["ctb"] = c;
            _st["freeze"] = string.Join("|", _lbFreeze.CheckedItems.Cast<string>());
            Plugin.SaveSettings(_st);
            if (_rows.Any(r => r.Dirty) && MessageBox.Show(this, "表格里有改动还没写入图纸，现在写入吗？", "图集管理", MessageBoxButtons.YesNo) == DialogResult.Yes) WriteBack();
        }
    }
}
