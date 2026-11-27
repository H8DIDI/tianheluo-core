// 一键出图向导的 AutoCAD 实现。原图不改：「准备」一步先把图另存为「原名_出图.dwg」，瘦身、美化、出图都在这个文件里。
// 重复运行：向导建的布局带标记（THL_ZONE），再次生成时先删掉旧的再建，不会越建越多。
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Thl.Acad.Tidy;

namespace Thl.Acad.Sheets.Wizard
{
    sealed class Backend : IWizardBackend
    {
        readonly Document _doc;
        Database Db => _doc.Database;
        public Backend(Document doc) { _doc = doc; }

        static readonly string SettingsDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo", "AutoCAD");
        public string SettingsFolder => SettingsDir;
        public string DocName => Path.GetFileName(_doc.Name);
        public string DocPath => Saved ? Db.Filename : "（还没保存过）";
        bool Saved => !string.IsNullOrEmpty(Db.Filename) && File.Exists(Db.Filename) && Db.Filename.EndsWith(".dwg", StringComparison.OrdinalIgnoreCase)
                      && !Path.GetFileName(Db.Filename).StartsWith("Drawing", StringComparison.OrdinalIgnoreCase);
        public double FileMB => Saved ? new FileInfo(Db.Filename).Length / 1048576.0 : 0;

        public bool HasCadUnderlay
        {
            get
            {
                var re = new Regex(@"_(dwg|dxf|dgn)-\d+-", RegexOptions.IgnoreCase);
                using (var tr = Db.TransactionManager.StartOpenCloseTransaction())
                {
                    var bt = (BlockTable)tr.GetObject(Db.BlockTableId, OpenMode.ForRead);
                    foreach (ObjectId id in bt)
                    {
                        var b = (BlockTableRecord)tr.GetObject(id, OpenMode.ForRead);
                        if (re.IsMatch(b.Name) && b.GetBlockReferenceIds(true, false).Count > 0) return true;
                    }
                }
                return false;
            }
        }

        public Dictionary<string, string> LoadPrefs() => Plugin.LoadSettings();
        public void SavePrefs(Dictionary<string, string> p) => Plugin.SaveSettings(p);

        // ------------------------------------------------------------------ 图框
        public List<string> Templates() => FrameTemplate.All().Select(t => t.Name).ToList();
        FrameTemplate Tpl(string name) => FrameTemplate.All().FirstOrDefault(t => t.Name == name) ?? throw new UserError($"找不到图框「{name}」。请在「③ 图框与方案」里重新选，或点「学习新图框…」。");

        public string LearnTemplate(string dwg, string name, Action<string> log)
        {
            try { return FrameLearner.Learn(dwg, name, log).Name; }
            catch (Autodesk.AutoCAD.Runtime.Exception ex) { throw new UserError($"读不了这个图框文件（{ex.ErrorStatus}）。可能是更高版本 AutoCAD 存的，或正被别人打开。另存一份再试。"); }
        }

        public string TemplateInfo(string name)
        {
            var t = FrameTemplate.All().FirstOrDefault(x => x.Name == name);
            if (t == null) return "";
            var k = t.PaperScale(); var a = Sheets.Area(t, k);
            return $"{t.Fields.Count} 个图签格子（图名、图号、比例、设计…）；视口可用区 {a.x1 - a.x0:0} × {a.y1 - a.y0:0} mm（{t.Paper}）";
        }

        // ------------------------------------------------------------------ 分区
        static ZoneInfo Info(Zone z, string src) => new ZoneInfo { Name = z.Name, Source = src, Poly = z.Poly.Select(p => new[] { p.X, p.Y }).ToList() };
        static Zone ToZone(ZoneInfo z) => new Zone { Name = z.Name, Poly = z.Poly.Select(p => new Point2d(p[0], p[1])).ToList() };

        public List<ZoneInfo> ZonesFromLayer() => Zones.FromLayer(Db, Zones.Layer).Select(z => Info(z, "图层")).ToList();

        public List<ZoneInfo> PickZones(IWin32Window owner)
        {
            var list = new List<Zone>();
            var ed = _doc.Editor;
            using (ed.StartUserInteraction(owner.Handle))
            {
                using (_doc.LockDocument()) if (LayoutManager.Current.CurrentLayout != "Model") LayoutManager.Current.CurrentLayout = "Model";
                int start = ZonesFromLayer().Count;
                while (true)
                {
                    var p1 = ed.GetPoint(new PromptPointOptions($"\n第 {list.Count + 1} 块：第一个角点（回车结束）") { AllowNone = true });
                    if (p1.Status != PromptStatus.OK) break;
                    var p2 = ed.GetCorner(new PromptCornerOptions("\n对角点", p1.Value));
                    if (p2.Status != PromptStatus.OK) break;
                    var nm = ed.GetString(new PromptStringOptions($"\n名称（如 1号楼）<分区{start + list.Count + 1}>") { AllowSpaces = true });
                    if (nm.Status == PromptStatus.Cancel) break;
                    var name = string.IsNullOrWhiteSpace(nm.StringResult) ? "分区" + (start + list.Count + 1) : nm.StringResult.Trim();
                    list.Add(Zone.Rect(name, p1.Value.X, p1.Value.Y, p2.Value.X, p2.Value.Y));
                }
            }
            if (list.Count > 0) using (_doc.LockDocument()) Zones.Save(Db, list);
            return list.Select(z => Info(z, "框选")).ToList();
        }

        public List<ZoneInfo> GridZones(WizardSettings s)
        {
            var t = Tpl(s.Template);
            var k = t.PaperScale(s.Paper); var a = Sheets.Avoid(t, k, Sheets.Area(t, k));
            int sc = s.ScaleMode == "fixed" ? s.FixedScale : 500;
            double mpu = Sheets.MmPerUnit(Db), side = (s.Legend || s.Index) ? 124 : 0;
            var e = Plugin.ModelExtents(Db);
            return Zones.Grid(Db, e.MinPoint.X, e.MinPoint.Y, e.MaxPoint.X, e.MaxPoint.Y, (a.x1 - a.x0 - side) * sc / mpu, (a.y1 - a.y0) * sc / mpu, 20.0 * sc / mpu, null)
                        .Select(z => Info(z, "网格")).ToList();
        }

        public List<ZoneInfo> WholeDrawing()
        {
            var e = Plugin.ModelExtents(Db);
            return new List<ZoneInfo> { ZoneInfo.Rect("总图", e.MinPoint.X, e.MinPoint.Y, e.MaxPoint.X, e.MaxPoint.Y, "整张图") };
        }

        public double[] Extents() { var e = Plugin.ModelExtents(Db); return new[] { e.MinPoint.X, e.MinPoint.Y, e.MaxPoint.X, e.MaxPoint.Y }; }

        ZoneOpts Opts(WizardSettings s) => new ZoneOpts
        {
            BaseTitle = s.BaseTitle, PerZoneScale = s.ScaleMode == "each", Scale = s.ScaleMode == "fixed" ? s.FixedScale : 0,
            MinFill = s.Split ? s.MinFill : 0, Legend = s.Legend, Index = s.Index, MatchLabels = s.Match,
        };

        public List<SheetPlan> Plan(List<ZoneInfo> zones, WizardSettings s, Action<string> log)
        {
            if (zones.Count == 0) throw new UserError("还没有分区。");
            var t = Tpl(s.Template);
            var plan = Zones.Plan(Db, t, zones.Select(ToZone).ToList(), Opts(s), s.Paper, log);
            return plan.Select(p => new SheetPlan { Zone = p.Zone.Name, Scale = p.Scale, Fill = p.Fill, SplitOf = p.SplitOf ?? "", Poly = p.Zone.Poly.Select(q => new[] { q.X, q.Y }).ToList() }).ToList();
        }

        // ------------------------------------------------------------------ 准备：另存 + 瘦身 + 美化
        /// <summary>出图用的文件（原名_出图.dwg）。注意：Database.SaveAs 之后 Database.Filename 不一定跟着变，所以自己记住，绝不往原图上存。</summary>
        string _target;

        string EnsureTarget(StringBuilder sb)
        {
            if (_target != null) return _target;
            if (!Saved) throw new UserError("这张图还没保存过。先保存一下（Ctrl+S），出图结果会放在它旁边。");
            var src = Db.Filename;
            _target = Path.GetFileNameWithoutExtension(src).EndsWith("_出图") ? src
                      : Path.Combine(Path.GetDirectoryName(src), Path.GetFileNameWithoutExtension(src) + "_出图.dwg");
            if (!string.Equals(_target, src, StringComparison.OrdinalIgnoreCase))
            {
                using (_doc.LockDocument()) Db.SaveAs(_target, DwgVersion.Current);
                sb?.AppendLine("原图不动，另存为：" + _target);
            }
            return _target;
        }

        public string Prepare(WizardSettings s, Action<int, string> progress)
        {
            var sb = new StringBuilder();
            var target = EnsureTarget(sb);
            using (_doc.LockDocument())
            {
                if (s.Slim)
                {
                    progress?.Invoke(0, "瘦身…");
                    var o = new Dictionary<string, string> { ["nocad"] = "1", ["wells"] = "1", ["join"] = "1", ["dedupe"] = "1", ["minsize"] = "50", ["tol"] = "0.5" };
                    Thl.DwgSlim.Slim.Process(Db, o, target, m => progress?.Invoke(0, m));
                    sb.AppendLine("瘦身完成");
                }
                if (s.Tidy)
                {
                    progress?.Invoke(0, "出图美化…");
                    var r = Tidy.Tidy.Run(Db, TidyOptions.Load(null), m => progress?.Invoke(0, m));
                    sb.AppendLine("出图美化完成");
                    Db.SaveAs(target, DwgVersion.Current);
                }
            }
            try { _doc.Editor.Regen(); } catch { }
            return sb.ToString().TrimEnd();
        }

        // ------------------------------------------------------------------ 生成
        public string Generate(List<SheetPlan> plans, List<ZoneInfo> zones, WizardSettings s, Action<int, string> progress, Func<bool> cancel)
        {
            var t = Tpl(s.Template);
            var zo = Opts(s);
            var st = Plugin.LoadSettings();
            var o = new Sheets.NewOpts { Paper = s.Paper, NameRule = "{图号} {图名}" };
            o.Plot.Device = st.TryGetValue("device", out var dv) ? dv : o.Plot.Device;
            o.Plot.Ctb = st.TryGetValue("ctb", out var cb) ? cb : "acad.ctb";
            foreach (var f in t.Fields.Where(f => f.Global)) if (st.TryGetValue("g." + f.Key, out var gv) && gv.Length > 0) o.Defaults[f.Key] = gv;
            var log = new List<string>();
            var sb = new StringBuilder();
            var target = EnsureTarget(sb);
            using (_doc.LockDocument())
            {
                // 1) 删掉上次向导建的布局（和目录），重新建 —— 重复运行不会越建越多
                var lm = LayoutManager.Current;
                lm.CurrentLayout = "Model";
                var old = Sheets.TaggedLayouts(Db).Keys.ToList();
                foreach (var n in old) try { lm.DeleteLayout(n); } catch { }
                if (old.Count > 0) progress?.Invoke(0, $"更新：删掉上次生成的 {old.Count} 张，重新生成");
                // 2) 按预览里的计划建图
                var all = zones.Select(ToZone).ToList();
                var planned = new List<Zones.Planned>();
                foreach (var p in plans)
                {
                    var z = new Zone { Name = p.Zone, Poly = p.Poly.Select(q => new Point2d(q[0], q[1])).ToList() };
                    planned.Add(new Zones.Planned { Zone = z, Scale = p.Scale, Fill = p.Fill, SplitOf = p.SplitOf, Title = p.Title, Number = p.Number, On = p.On });
                }
                var names = Zones.CreateFromPlan(_doc, t, planned, zo, o, log.Add, (i, m) => progress?.Invoke(i + 1, i < planned.Count(q => q.On) ? $"第 {i + 1} 张：{m}" : m), cancel);
                sb.AppendLine($"生成 {names.Count} 张图纸");
                // 3) 张号、图签里全套相同的字段
                var rows = Sheets.Read(Db, FrameTemplate.All()).Where(r => !r.TitleRef.IsNull && names.Contains(r.Layout)).ToList();
                for (int i = 0; i < rows.Count; i++)
                {
                    if (t.Fields.Any(f => f.Key == "张号")) { rows[i].Values["张号"] = $"{i + 1:00} OF {rows.Count:00}"; rows[i].Dirty = true; }
                }
                if (rows.Any(r => r.Dirty)) Sheets.Write(_doc, rows, "", log.Add);
                // 4) 目录
                if (s.Toc && names.Count > 0 && !(cancel?.Invoke() ?? false))
                {
                    progress?.Invoke(names.Count + 1, "图纸目录");
                    Sheets.Toc(_doc, t, rows, o, "图纸目录", s.NumPrefix + new string('0', Math.Max(1, s.NumDigits)), log.Add);
                    Sheets.TagLayout(Db, "图纸目录", "#目录");
                }
            }
            // 5) 保存
            try
            {
                using (_doc.LockDocument())
                {
                    // 正在编辑的就是这个文件：用「保存并留 .bak」的重载，否则文件被自己占着存不进去
                    if (string.Equals(Db.Filename, target, StringComparison.OrdinalIgnoreCase)) Db.SaveAs(target, true, DwgVersion.Current, Db.SecurityParameters);
                    else Db.SaveAs(target, DwgVersion.Current);
                }
                sb.AppendLine("图纸：" + target);
            }
            catch { sb.AppendLine("图纸没能自动保存（文件可能被占用），请用「另存为」存成：" + target); }
            // 6) PDF
            if (s.Pdf && !(cancel?.Invoke() ?? false))
            {
                progress?.Invoke(names0(), "打印 PDF…");
                var pdf = !string.IsNullOrEmpty(s.PdfPath) ? s.PdfPath : Path.Combine(Path.GetDirectoryName(target), Path.GetFileNameWithoutExtension(target).Replace("_出图", "") + "_图集.pdf");
                var tagged = Sheets.TaggedLayouts(Db);
                var rows = Sheets.Read(Db, FrameTemplate.All()).Where(r => tagged.ContainsKey(r.Layout)).ToList();
                try { var r = Sheets.PlotPdf(_doc, rows, pdf, null, log.Add); sb.AppendLine(r.Replace("PDF 已生成", "PDF")); }
                catch (Exception ex) { sb.AppendLine("PDF 没打出来：" + ex.Message + "（可能 PDF 正在别的软件里打开着，关掉再点一次「生成」）"); }
            }
            foreach (var l in log.Where(l => l.Contains("放不下") || l.Contains("拆成") || l.Contains("失败"))) sb.AppendLine(l);
            return sb.ToString().TrimEnd();
            int names0() => plans.Count(p => p.On) + 1;
        }

        public void Open(string path) { try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(path) { UseShellExecute = true }); } catch { } }

        // ------------------------------------------------------------------ 图例
        public List<LegendRow> LegendRows()
        {
            var e = Plugin.ModelExtents(Db);
            var whole = Zone.Rect("_all", e.MinPoint.X - 1, e.MinPoint.Y - 1, e.MaxPoint.X + 1, e.MaxPoint.Y + 1);
            var counts = Zones.LayersIn(Db, new List<Zone> { whole }, null)["_all"];
            var lib = LegendLib.Load(null);
            var rows = new List<LegendRow>();
            using (var tr = Db.TransactionManager.StartOpenCloseTransaction())
            {
                var lt = (LayerTable)tr.GetObject(Db.LayerTableId, OpenMode.ForRead);
                foreach (var kv in counts.OrderByDescending(k => k.Value))
                {
                    if (!lt.Has(kv.Key) || kv.Key.StartsWith("THL-")) continue;
                    var l = (LayerTableRecord)tr.GetObject(lt[kv.Key], OpenMode.ForRead);
                    var name = LegendLib.NameOf(kv.Key, l.Description, lib);
                    var c = l.Color.ColorValue;
                    rows.Add(new LegendRow { Layer = kv.Key, Count = kv.Value, Name = name == "-" ? "" : name, Show = name != "-", Rgb = (c.R << 16) | (c.G << 8) | c.B });
                }
                tr.Commit();
            }
            return rows;
        }

        public void SaveLegend(List<LegendRow> rows) => LegendLib.SaveRows(rows.Select(r => (r.Layer, r.Name, r.Show)));

        // ------------------------------------------------------------------ 美化规则
        static TidyConfig ToCfg(TidyOptions o) => new TidyConfig
        {
            Fonts = o.Fonts, Linetypes = o.Linetypes, Duplicates = o.Duplicates, DarkenPale = o.DarkenPale, ByLayer = o.ByLayer, MaskMText = o.MaskMText,
            KeepText = o.KeepText, Purge = o.Purge, ShxFont = o.ShxFont, BigFont = o.BigFont,
            Rules = o.Rules.Select(r => new TidyRule { Pattern = r.Raw, Color = r.Color ?? "", Lw = r.Lw >= 0 ? r.Lw.ToString(System.Globalization.CultureInfo.InvariantCulture) : "", Linetype = r.Linetype ?? "", Plot = r.Plot < 0 ? "" : r.Plot.ToString() }).ToList(),
        };
        public TidyConfig LoadTidy() => ToCfg(TidyOptions.Load(null));
        public TidyConfig DefaultTidy() => ToCfg(TidyOptions.Parse(TidyOptions.DefaultText));
        public void SaveTidy(TidyConfig c)
        {
            var sb = new StringBuilder();
            sb.AppendLine("# 天河落 · 出图美化 规则表（在「设置 → 出图美化规则」里改最方便）");
            void B(string k, bool v) => sb.AppendLine(k + "=" + (v ? "1" : "0"));
            B("fonts", c.Fonts); sb.AppendLine("shx=" + c.ShxFont); sb.AppendLine("bigfont=" + c.BigFont);
            B("linetypes", c.Linetypes); sb.AppendLine("dashmin=6"); sb.AppendLine("dashmax=18");
            B("duplicates", c.Duplicates); B("darkenpale", c.DarkenPale); B("bylayer", c.ByLayer); B("maskmtext", c.MaskMText); B("keeptext", c.KeepText); B("purge", c.Purge);
            foreach (var r in c.Rules) sb.AppendLine($"layer={r.Pattern}|{r.Color}|{r.Lw}|{r.Linetype}|{r.Plot}");
            Directory.CreateDirectory(Path.GetDirectoryName(TidyOptions.File));
            File.WriteAllText(TidyOptions.File, sb.ToString(), new UTF8Encoding(true));
        }

        // ------------------------------------------------------------------ 方案导入导出
        static readonly string[] SchemeFiles = { @"settings\acad-sheets.txt", @"settings\legend.txt", @"settings\dwg-tidy.txt" };

        public string ExportScheme(string zipPath, string template)
        {
            if (File.Exists(zipPath)) File.Delete(zipPath);
            using (var z = ZipFile.Open(zipPath, ZipArchiveMode.Create))
            {
                foreach (var f in SchemeFiles) { var p = Path.Combine(SettingsDir, f); if (File.Exists(p)) z.CreateEntryFromFile(p, f.Replace('\\', '/')); }
                if (!string.IsNullOrEmpty(template))
                {
                    var dir = Path.Combine(FrameTemplate.Root, FrameTemplate.SafeName(template));
                    if (Directory.Exists(dir))
                        foreach (var f in Directory.GetFiles(dir)) z.CreateEntryFromFile(f, "sheet-frames/" + FrameTemplate.SafeName(template) + "/" + Path.GetFileName(f));
                }
            }
            return "已导出方案：" + zipPath;
        }

        public string ImportScheme(string zipPath)
        {
            int n = 0;
            using (var z = ZipFile.OpenRead(zipPath))
                foreach (var e in z.Entries)
                {
                    if (e.FullName.EndsWith("/") || e.FullName.Contains("..")) continue;
                    var dst = Path.Combine(SettingsDir, e.FullName.Replace('/', '\\'));
                    if (!dst.StartsWith(SettingsDir, StringComparison.OrdinalIgnoreCase)) continue;
                    Directory.CreateDirectory(Path.GetDirectoryName(dst));
                    if (File.Exists(dst)) File.Copy(dst, dst + ".导入前备份", true);
                    e.ExtractToFile(dst, true); n++;
                }
            return $"已导入方案（{n} 个文件；原来的设置备份成 *.导入前备份）";
        }
    }
}
