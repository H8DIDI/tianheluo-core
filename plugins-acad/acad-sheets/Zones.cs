// 分区出图：一张很大的模型空间总图，按楼栋 / 区域切成多块，每块一个布局。
//   分区来源：① 模型空间图层 THL-分区 上的闭合多段线（矩形或任意形状），名称取多段线里面同图层的文字；
//            ② 界面里框选 / 参数直接给坐标；③ 按图幅 + 比例自动网格（带搭接，空块跳过）。
//   每块：新建布局 → 外部参照图框 → 主视口（统一比例或各自自动）→ 非矩形分区用多边形裁剪视口 → 锁定；
//        右侧栏：图例（只列本块里实际出现的图层）+ 分区索引图（总图缩略、当前块红框、各块名称）；视口边上标「接 XX」。
//   不复制模型几何：视口直接看模型空间（大图也不会翻倍）。
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using AcColor = Autodesk.AutoCAD.Colors.Color;
using AcColorMethod = Autodesk.AutoCAD.Colors.ColorMethod;

namespace Thl.Acad.Sheets
{
    sealed class Zone
    {
        public string Name = "";
        public List<Point2d> Poly = new List<Point2d>();   // 逆/顺时针都行，闭合（不重复首点）
        public ObjectId Source;                            // 来自图层多段线时
        public double X0 => Poly.Min(p => p.X); public double X1 => Poly.Max(p => p.X);
        public double Y0 => Poly.Min(p => p.Y); public double Y1 => Poly.Max(p => p.Y);
        public Point2d Center => new Point2d((X0 + X1) / 2, (Y0 + Y1) / 2);
        public bool IsRect
        {
            get
            {
                if (Poly.Count != 4) return false;
                double tol = Math.Max(X1 - X0, Y1 - Y0) * 1e-6;
                return Poly.All(p => (Math.Abs(p.X - X0) < tol || Math.Abs(p.X - X1) < tol) && (Math.Abs(p.Y - Y0) < tol || Math.Abs(p.Y - Y1) < tol));
            }
        }
        public static Zone Rect(string name, double x0, double y0, double x1, double y1) => new Zone
        {
            Name = name,
            Poly = new List<Point2d> { new Point2d(Math.Min(x0, x1), Math.Min(y0, y1)), new Point2d(Math.Max(x0, x1), Math.Min(y0, y1)), new Point2d(Math.Max(x0, x1), Math.Max(y0, y1)), new Point2d(Math.Min(x0, x1), Math.Max(y0, y1)) },
        };
        public bool Contains(Point2d p)
        {
            bool inside = false;
            for (int i = 0, j = Poly.Count - 1; i < Poly.Count; j = i++)
            {
                var a = Poly[i]; var b = Poly[j];
                if ((a.Y > p.Y) != (b.Y > p.Y) && p.X < (b.X - a.X) * (p.Y - a.Y) / (b.Y - a.Y) + a.X) inside = !inside;
            }
            return inside;
        }
    }

    sealed class ZoneOpts
    {
        public string BaseTitle = "管线综合平面图";   // 图名 = 分区名 + 空格 + 基名
        public int Scale;                            // 0 = 统一取各分区自动比例里最大的那个（全套同一比例）
        public bool PerZoneScale;                    // true = 每块各自选能放下的标准比例
        public bool Legend = true, Index = true, MatchLabels = true;
        public double SideW = 120, IndexH = 95;      // 右侧栏宽、索引图高（纸面 mm）
        public double MinFill = 0.7;                 // 各块自动比例时：主图占可用区不到这个比例就沿长边拆成 2~3 张
    }

    static class Zones
    {
        public const string Layer = "THL-分区";

        /// <summary>参数格式：名称:x0,y0,x1,y1（矩形）或 名称:x,y x,y x,y ...（多边形），多个用 | 分开。</summary>
        public static List<Zone> Parse(string s)
        {
            var list = new List<Zone>();
            foreach (var item in (s ?? "").Split('|').Select(x => x.Trim()).Where(x => x.Length > 0))
            {
                var i = item.LastIndexOf(':');
                var name = i > 0 ? item.Substring(0, i).Trim() : "分区" + (list.Count + 1);
                var body = i > 0 ? item.Substring(i + 1) : item;
                var nums = Regex.Matches(body, @"-?\d+(\.\d+)?").Cast<Match>().Select(m => double.Parse(m.Value, System.Globalization.CultureInfo.InvariantCulture)).ToList();
                if (body.Contains(" ") && nums.Count >= 6)
                {
                    var z = new Zone { Name = name };
                    for (int k = 0; k + 1 < nums.Count; k += 2) z.Poly.Add(new Point2d(nums[k], nums[k + 1]));
                    list.Add(z);
                }
                else if (nums.Count == 4) list.Add(Zone.Rect(name, nums[0], nums[1], nums[2], nums[3]));
            }
            return list;
        }

        /// <summary>读模型空间某图层上的闭合多段线作为分区；名称 = 多段线里面同图层的文字（没有就「分区 n」）。</summary>
        public static List<Zone> FromLayer(Database db, string layer)
        {
            var zs = new List<Zone>();
            var texts = new List<(Point2d p, string s)>();
            using (var tr = db.TransactionManager.StartOpenCloseTransaction())
            {
                var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
                foreach (ObjectId id in ms)
                {
                    var dx = id.ObjectClass.DxfName;
                    if (dx != "LWPOLYLINE" && dx != "TEXT" && dx != "MTEXT") continue;
                    var e = (Entity)tr.GetObject(id, OpenMode.ForRead);
                    if (!string.Equals(e.Layer, layer, StringComparison.OrdinalIgnoreCase)) continue;
                    if (e is Polyline pl && pl.Closed && pl.NumberOfVertices >= 3)
                    {
                        var z = new Zone { Source = id };
                        for (int i = 0; i < pl.NumberOfVertices; i++) z.Poly.Add(pl.GetPoint2dAt(i));
                        zs.Add(z);
                    }
                    else if (e is DBText t) texts.Add((new Point2d(t.Position.X, t.Position.Y), t.TextString.Trim()));
                    else if (e is MText m) texts.Add((new Point2d(m.Location.X, m.Location.Y), m.Text.Trim()));
                }
                tr.Commit();
            }
            int n = 1;
            foreach (var z in zs.OrderByDescending(z => z.Y1).ThenBy(z => z.X0))
            {
                var inside = texts.Where(t => t.s.Length > 0 && z.Contains(t.p)).OrderBy(t => t.p.GetDistanceTo(z.Center)).FirstOrDefault();
                z.Name = inside.s ?? "";
                if (z.Name.Length == 0) z.Name = "分区" + n;
                n++;
            }
            return zs.OrderByDescending(z => z.Y1).ThenBy(z => z.X0).ToList();
        }

        /// <summary>把分区存成 THL-分区 图层上的多段线 + 名称文字（以后在 CAD 里直接改形状、改名，再出图）。已有同名的先删。</summary>
        public static void Save(Database db, List<Zone> zones)
        {
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead);
                if (!lt.Has(Layer))
                {
                    lt.UpgradeOpen();
                    var l = new LayerTableRecord { Name = Layer, IsPlottable = false, Color = AcColor.FromColorIndex(AcColorMethod.ByAci, 6) };
                    lt.Add(l); tr.AddNewlyCreatedDBObject(l, true);
                }
                var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForWrite);
                var names = new HashSet<string>(zones.Select(z => z.Name));
                foreach (var old in FromLayer(db, Layer).Where(z => names.Contains(z.Name)))
                {
                    tr.GetObject(old.Source, OpenMode.ForWrite).Erase();
                    foreach (ObjectId id in ms)
                    {
                        if (id.ObjectClass.DxfName != "TEXT") continue;
                        var t = (DBText)tr.GetObject(id, OpenMode.ForRead);
                        if (t.Layer == Layer && t.TextString.Trim() == old.Name && old.Contains(new Point2d(t.Position.X, t.Position.Y))) { t.UpgradeOpen(); t.Erase(); }
                    }
                }
                foreach (var z in zones)
                {
                    var pl = new Polyline();
                    pl.SetDatabaseDefaults(db);
                    for (int i = 0; i < z.Poly.Count; i++) pl.AddVertexAt(i, z.Poly[i], 0, 0, 0);
                    pl.Closed = true; pl.Layer = Layer;
                    double w = Math.Max(z.X1 - z.X0, z.Y1 - z.Y0);
                    pl.ConstantWidth = w * 0.004;
                    ms.AppendEntity(pl); tr.AddNewlyCreatedDBObject(pl, true);
                    var t = new DBText { TextString = z.Name, Height = w * 0.04, Layer = Layer };
                    t.SetDatabaseDefaults(db); t.Layer = Layer; t.Height = w * 0.04;
                    t.HorizontalMode = TextHorizontalMode.TextCenter; t.VerticalMode = TextVerticalMode.TextVerticalMid;
                    t.AlignmentPoint = new Point3d(z.Center.X, z.Center.Y, 0); t.Position = t.AlignmentPoint;
                    ms.AppendEntity(t); tr.AddNewlyCreatedDBObject(t, true);
                    using (new WorkingDbScope(db)) t.AdjustAlignment(db);
                    z.Source = pl.ObjectId;
                }
                tr.Commit();
            }
        }

        // ------------------------------------------------------------------ 模型扫描：每个分区里出现了哪些图层
        /// <summary>扫一遍模型空间：每个分区里各图层的对象数（块按块内对象的图层计，结果缓存到块定义）。</summary>
        public static Dictionary<string, Dictionary<string, int>> LayersIn(Database db, List<Zone> zones, Action<string> log)
        {
            var res = zones.ToDictionary(z => z.Name, z => new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase));
            var blockLayers = new Dictionary<ObjectId, HashSet<string>>();
            int n = 0;
            using (var tr = db.TransactionManager.StartOpenCloseTransaction())
            {
                HashSet<string> LayersOfBlock(ObjectId btrId, int depth)
                {
                    if (blockLayers.TryGetValue(btrId, out var hs)) return hs;
                    hs = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                    blockLayers[btrId] = hs;
                    var b = (BlockTableRecord)tr.GetObject(btrId, OpenMode.ForRead);
                    if (b.IsFromExternalReference || depth > 4) return hs;
                    foreach (ObjectId id in b)
                    {
                        if (!(tr.GetObject(id, OpenMode.ForRead) is Entity e)) continue;
                        if (e is AttributeDefinition) continue;
                        if (e is BlockReference br)
                            foreach (var l in LayersOfBlock(br.BlockTableRecord, depth + 1)) hs.Add(l == "0" ? "0" : l);
                        else hs.Add(e.Layer);
                    }
                    return hs;
                }
                var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
                foreach (ObjectId id in ms)
                {
                    if (!(tr.GetObject(id, OpenMode.ForRead) is Entity e) || e.Layer == Layer) continue;
                    Extents3d ex;
                    try { ex = e.Bounds ?? default; } catch { continue; }
                    if (ex.MinPoint == ex.MaxPoint && ex.MinPoint == Point3d.Origin) continue;
                    var c = new Point2d((ex.MinPoint.X + ex.MaxPoint.X) / 2, (ex.MinPoint.Y + ex.MaxPoint.Y) / 2);
                    IEnumerable<string> lays = null;
                    foreach (var z in zones)
                    {
                        // 对象中心在分区里，或对象和分区外包相交且有一个角在分区里
                        if (ex.MaxPoint.X < z.X0 || ex.MinPoint.X > z.X1 || ex.MaxPoint.Y < z.Y0 || ex.MinPoint.Y > z.Y1) continue;
                        if (!z.Contains(c) && !z.Contains(new Point2d(ex.MinPoint.X, ex.MinPoint.Y)) && !z.Contains(new Point2d(ex.MaxPoint.X, ex.MaxPoint.Y))) continue;
                        if (lays == null)
                        {
                            if (e is BlockReference br)
                            {
                                var inner = LayersOfBlock(br.BlockTableRecord, 0);
                                lays = inner.Select(l => l == "0" ? br.Layer : l).Distinct(StringComparer.OrdinalIgnoreCase).ToList();
                            }
                            else lays = new[] { e.Layer };
                        }
                        var d = res[z.Name];
                        foreach (var l in lays) { d.TryGetValue(l, out var k); d[l] = k + 1; }
                    }
                    n++;
                }
                tr.Commit();
            }
            log?.Invoke($"扫描模型空间 {n} 个对象，统计各分区用到的图层");
            return res;
        }

        /// <summary>按图幅可用区 + 比例自动网格分块（带搭接），没有对象的块跳过。</summary>
        public static List<Zone> Grid(Database db, double x0, double y0, double x1, double y1, double tileW, double tileH, double overlap, Action<string> log)
        {
            var zs = new List<Zone>();
            double stepX = tileW - overlap, stepY = tileH - overlap;
            int nx = Math.Max(1, (int)Math.Ceiling((x1 - x0 - overlap) / stepX)), ny = Math.Max(1, (int)Math.Ceiling((y1 - y0 - overlap) / stepY));
            // 居中排布
            double ox = x0 - (nx * stepX + overlap - (x1 - x0)) / 2, oy = y0 - (ny * stepY + overlap - (y1 - y0)) / 2;
            var occupied = new bool[nx, ny];
            using (var tr = db.TransactionManager.StartOpenCloseTransaction())
            {
                var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
                foreach (ObjectId id in ms)
                {
                    if (!(tr.GetObject(id, OpenMode.ForRead) is Entity e)) continue;
                    Extents3d ex; try { ex = e.Bounds ?? default; } catch { continue; }
                    double cx = (ex.MinPoint.X + ex.MaxPoint.X) / 2, cy = (ex.MinPoint.Y + ex.MaxPoint.Y) / 2;
                    int i = (int)Math.Floor((cx - ox) / stepX), j = (int)Math.Floor((cy - oy) / stepY);
                    if (i >= 0 && i < nx && j >= 0 && j < ny) occupied[i, j] = true;
                }
                tr.Commit();
            }
            for (int j = ny - 1; j >= 0; j--)
                for (int i = 0; i < nx; i++)
                {
                    if (!occupied[i, j]) continue;
                    var name = ((char)('A' + (ny - 1 - j) % 26)).ToString() + (i + 1);
                    zs.Add(Zone.Rect("分块" + name, ox + i * stepX, oy + j * stepY, ox + i * stepX + tileW, oy + j * stepY + tileH));
                }
            log?.Invoke($"自动网格 {nx}×{ny}，有内容的 {zs.Count} 块");
            return zs;
        }

        // ------------------------------------------------------------------ 出图
        public sealed class Planned
        {
            public Zone Zone; public int Scale; public double Fill; public string SplitOf;
            public string Title, Number;    // 空 = 用默认（分区名 + 基名）
            public bool On = true;
        }

        /// <summary>只计算不改图：每块用的比例、主图占可用区的比例；太空的长条楼栋沿长边拆成 2~3 张。</summary>
        public static List<Planned> Plan(Database db, FrameTemplate t, List<Zone> zones, ZoneOpts zo, string paper, Action<string> log)
        {
            double k = t.PaperScale(paper ?? t.Paper);
            double mpu = Sheets.MmPerUnit(db);
            var a = Sheets.Avoid(t, k, Sheets.Area(t, k));
            double side = (zo.Legend || zo.Index) ? zo.SideW + 4 : 0;
            double mw = a.x1 - a.x0 - side, mh = a.y1 - a.y0;
            int Fit(Zone z)
            {
                foreach (var c in Sheets.StdScales) if ((z.X1 - z.X0) * mpu / c <= mw && (z.Y1 - z.Y0) * mpu / c <= mh) return c;
                return (int)Math.Ceiling(Math.Max((z.X1 - z.X0) * mpu / mw, (z.Y1 - z.Y0) * mpu / mh));
            }
            double FillAt(Zone q, int sc) { double w = Math.Min(mw, (q.X1 - q.X0) * mpu / sc), h = Math.Min(mh, (q.Y1 - q.Y0) * mpu / sc); return w * h / (mw * mh); }
            var list = new List<Planned>();
            bool each = zo.PerZoneScale && zo.Scale <= 0;
            foreach (var z in zones)
            {
                if (each && zo.MinFill > 0)
                {
                    var f1 = FillAt(z, Fit(z));
                    if (f1 < zo.MinFill)
                    {
                        List<Zone> best = null; double bestF = f1;
                        for (int nk = 2; nk <= 3; nk++)
                        {
                            var parts = Split(z, nk, Math.Max(z.X1 - z.X0, z.Y1 - z.Y0) * 0.02);
                            var f = parts.Min(q => FillAt(q, Fit(q)));
                            if (f > bestF + 0.1) { bestF = f; best = parts; }
                        }
                        if (best != null)
                        {
                            log?.Invoke($"「{z.Name}」主图只占可用区 {f1:P0}，拆成 {best.Count} 张（每张约 {bestF:P0}）");
                            foreach (var q in best) list.Add(new Planned { Zone = q, Scale = Fit(q), Fill = FillAt(q, Fit(q)), SplitOf = z.Name });
                            continue;
                        }
                    }
                }
                list.Add(new Planned { Zone = z, Scale = each ? Fit(z) : 0 });
            }
            if (!each)
            {
                int uniform = zo.Scale > 0 ? zo.Scale : list.Max(p => Fit(p.Zone));
                foreach (var p in list) p.Scale = uniform;
                if (zo.Scale <= 0) log?.Invoke($"统一比例 1:{uniform}（能放下最大那块的标准比例）");
            }
            foreach (var p in list) p.Fill = FillAt(p.Zone, p.Scale);
            return list;
        }

        /// <summary>每个分区建一张图纸（先规划再建）。返回新布局名列表。</summary>
        public static List<string> CreateSheets(Document doc, FrameTemplate t, List<Zone> zones, ZoneOpts zo, Sheets.NewOpts o, Action<string> log)
            => CreateFromPlan(doc, t, Plan(doc.Database, t, zones, zo, o.Paper, log), zo, o, log, null, null);

        /// <summary>按计划建图纸。progress(第几张, 说明)；cancel() 为真时停下（已建的保留）。</summary>
        public static List<string> CreateFromPlan(Document doc, FrameTemplate t, List<Planned> plan, ZoneOpts zo, Sheets.NewOpts o, Action<string> log,
                                                  Action<int, string> progress, Func<bool> cancel)
        {
            var db = doc.Database;
            var on = plan.Where(p => p.On).ToList();
            var all = on.Select(p => p.Zone).ToList();
            var layers = zo.Legend ? LayersIn(db, all, log) : null;
            var lib = zo.Legend ? LegendLib.Load(log) : null;
            var names = new List<string>();
            for (int i = 0; i < on.Count; i++)
            {
                if (cancel != null && cancel()) { log?.Invoke($"已取消：建了 {names.Count} 张"); break; }
                var p = on[i]; var z = p.Zone;
                progress?.Invoke(i, z.Name);
                var s = new Sheets.NewSheet
                {
                    Min = new Point3d(z.X0, z.Y0, 0), Max = new Point3d(z.X1, z.Y1, 0), Scale = p.Scale,
                    Title = string.IsNullOrEmpty(p.Title) ? (z.Name + " " + zo.BaseTitle).Trim() : p.Title,
                    Number = p.Number ?? "",
                    Zone = z, AllZones = all, ZoneOpts = zo,
                    Layers = layers != null && layers.TryGetValue(z.Name, out var d) ? d : null, Legend = lib,
                };
                var name = Sheets.Create(doc, t, s, o, log);
                Sheets.TagLayout(db, name, z.Name);
                names.Add(name);
            }
            progress?.Invoke(on.Count, "完成");
            return names;
        }

        /// <summary>沿长边切成 k 块（相邻块搭接 overlap），多边形分区按条带裁剪。名称 = 原名（一）（二）…</summary>
        static List<Zone> Split(Zone z, int k, double overlap)
        {
            string[] cn = { "一", "二", "三", "四" };
            bool vertical = (z.Y1 - z.Y0) >= (z.X1 - z.X0);
            var res = new List<Zone>();
            double a0 = vertical ? z.Y0 : z.X0, a1 = vertical ? z.Y1 : z.X1, step = (a1 - a0) / k;
            for (int i = 0; i < k; i++)
            {
                double s0 = a0 + i * step - (i > 0 ? overlap / 2 : 0), s1 = a0 + (i + 1) * step + (i < k - 1 ? overlap / 2 : 0);
                // 从上往下 / 从左往右编号
                int idx = vertical ? k - 1 - i : i;
                var name = z.Name + "（" + cn[idx] + "）";
                var poly = z.IsRect ? null : ClipPoly(z.Poly, vertical, s0, s1);
                var part = poly != null && poly.Count >= 3 ? new Zone { Name = name, Poly = poly }
                         : vertical ? Zone.Rect(name, z.X0, s0, z.X1, s1) : Zone.Rect(name, s0, z.Y0, s1, z.Y1);
                res.Add(part);
            }
            return vertical ? res.OrderByDescending(q => q.Y1).ToList() : res;
        }

        /// <summary>多边形和条带求交（Sutherland–Hodgman，两条平行边各裁一次）。</summary>
        static List<Point2d> ClipPoly(List<Point2d> poly, bool vertical, double lo, double hi)
        {
            List<Point2d> Clip(List<Point2d> pts, Func<Point2d, double> f, double c, bool keepAbove)
            {
                var o = new List<Point2d>();
                for (int i = 0; i < pts.Count; i++)
                {
                    var a = pts[i]; var b = pts[(i + 1) % pts.Count];
                    bool ina = keepAbove ? f(a) >= c : f(a) <= c, inb = keepAbove ? f(b) >= c : f(b) <= c;
                    if (ina) o.Add(a);
                    if (ina != inb) { var t = (c - f(a)) / (f(b) - f(a)); o.Add(new Point2d(a.X + (b.X - a.X) * t, a.Y + (b.Y - a.Y) * t)); }
                }
                return o;
            }
            Func<Point2d, double> g = vertical ? (Func<Point2d, double>)(p => p.Y) : (p => p.X);
            var r = Clip(poly, g, lo, true);
            return r.Count < 3 ? r : Clip(r, g, hi, false);
        }

        // ------------------------------------------------------------------ 图纸上的附加内容（由 Sheets.Create 调用）
        internal static Point3d ToPaper(Point2d m, Point2d viewCenter, Point3d vpCenter, double cs) =>
            new Point3d(vpCenter.X + (m.X - viewCenter.X) * cs, vpCenter.Y + (m.Y - viewCenter.Y) * cs, 0);

        internal static ObjectId EnsureLayer(Database db, Transaction tr, string name, short aci, bool plot)
        {
            var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead);
            if (lt.Has(name)) return lt[name];
            lt.UpgradeOpen();
            var l = new LayerTableRecord { Name = name, IsPlottable = plot, Color = AcColor.FromColorIndex(AcColorMethod.ByAci, aci) };
            var id = lt.Add(l); tr.AddNewlyCreatedDBObject(l, true);
            return id;
        }

        static DBText Text(Database db, BlockTableRecord ps, Transaction tr, string s, Point3d p, double h, string layer, TextHorizontalMode hm = TextHorizontalMode.TextLeft, TextVerticalMode vm = TextVerticalMode.TextBase, ObjectId style = default)
        {
            var t = new DBText();
            t.SetDatabaseDefaults(db);
            t.TextString = s; t.Height = h; t.Layer = layer;
            if (!style.IsNull) t.TextStyleId = style;
            t.HorizontalMode = hm; t.VerticalMode = vm;
            t.Position = p; if (hm != TextHorizontalMode.TextLeft || vm != TextVerticalMode.TextBase) t.AlignmentPoint = p;
            ps.AppendEntity(t); tr.AddNewlyCreatedDBObject(t, true);
            if (hm != TextHorizontalMode.TextLeft || vm != TextVerticalMode.TextBase) using (new WorkingDbScope(db)) t.AdjustAlignment(db);
            return t;
        }

        static Polyline Rect(Database db, BlockTableRecord ps, Transaction tr, double x0, double y0, double x1, double y1, string layer, double width = 0)
        {
            var pl = new Polyline();
            pl.SetDatabaseDefaults(db);
            pl.AddVertexAt(0, new Point2d(x0, y0), 0, width, width); pl.AddVertexAt(1, new Point2d(x1, y0), 0, width, width);
            pl.AddVertexAt(2, new Point2d(x1, y1), 0, width, width); pl.AddVertexAt(3, new Point2d(x0, y1), 0, width, width);
            pl.Closed = true; pl.Layer = layer;
            ps.AppendEntity(pl); tr.AddNewlyCreatedDBObject(pl, true);
            return pl;
        }

        /// <summary>非矩形分区：按分区形状裁剪视口。</summary>
        internal static void ClipViewport(Database db, Transaction tr, BlockTableRecord ps, Viewport vp, Zone z, Point2d viewCenter, double cs)
        {
            if (z == null || z.IsRect) return;
            var pl = new Polyline();
            pl.SetDatabaseDefaults(db);
            for (int i = 0; i < z.Poly.Count; i++)
            {
                var p = ToPaper(z.Poly[i], viewCenter, vp.CenterPoint, cs);
                pl.AddVertexAt(i, new Point2d(p.X, p.Y), 0, 0, 0);
            }
            pl.Closed = true; pl.Layer = Sheets.VpLayer;
            ps.AppendEntity(pl); tr.AddNewlyCreatedDBObject(pl, true);
            vp.NonRectClipEntityId = pl.ObjectId;
            vp.NonRectClipOn = true;
        }

        /// <summary>右侧栏：图例（上）+ 分区索引图（下）；视口边上标「接 XX」。</summary>
        internal static void Decorate(Database db, Transaction tr, BlockTableRecord ps, Sheets.NewSheet s, Viewport main, (double x0, double y0, double x1, double y1) side, ObjectId style, Action<string> log)
        {
            var zo = s.ZoneOpts ?? new ZoneOpts();
            const string L = "THL-出图辅助";
            EnsureLayer(db, tr, L, 7, true);
            EnsureLayer(db, tr, "THL-接图", 1, true);
            var lyr = (LayerTableRecord)tr.GetObject(((LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead))["THL-接图"], OpenMode.ForWrite);
            lyr.LineWeight = LineWeight.LineWeight035;
            if (style.IsNull) style = db.Textstyle;
            double gap = 4;
            // ---- 分区索引图
            double legendTop = side.y1, legendBottom = side.y0;
            if (zo.Index && s.AllZones != null && s.AllZones.Count > 0)
            {
                double ix0 = side.x0, ix1 = side.x1, iy0 = side.y0, iy1 = side.y0 + zo.IndexH;
                legendBottom = iy1 + gap;
                Rect(db, ps, tr, ix0, iy0, ix1, iy1, L);
                Text(db, ps, tr, "分区索引", new Point3d(ix0 + 2, iy1 - 6, 0), 3.5, L, style: style);
                double bx0 = ix0 + 3, by0 = iy0 + 3, bx1 = ix1 - 3, by1 = iy1 - 9;
                double ex0 = s.AllZones.Min(z => z.X0), ex1 = s.AllZones.Max(z => z.X1), ey0 = s.AllZones.Min(z => z.Y0), ey1 = s.AllZones.Max(z => z.Y1);
                double pad = Math.Max(ex1 - ex0, ey1 - ey0) * 0.04; ex0 -= pad; ex1 += pad; ey0 -= pad; ey1 += pad;
                double cs = Math.Min((bx1 - bx0) / (ex1 - ex0), (by1 - by0) / (ey1 - ey0));
                var vc = new Point2d((ex0 + ex1) / 2, (ey0 + ey1) / 2);
                var c = new Point3d((bx0 + bx1) / 2, (by0 + by1) / 2, 0);
                var vp = new Viewport();
                vp.SetDatabaseDefaults(db);
                vp.CenterPoint = c; vp.Width = (ex1 - ex0) * cs; vp.Height = (ey1 - ey0) * cs; vp.Layer = Sheets.VpLayer;
                ps.AppendEntity(vp); tr.AddNewlyCreatedDBObject(vp, true);
                vp.ViewDirection = Vector3d.ZAxis; vp.ViewTarget = Point3d.Origin; vp.TwistAngle = 0;
                vp.ViewCenter = vc; vp.CustomScale = cs; vp.On = true; vp.Locked = true;
                // 冻结文字/标注类图层，索引图只看轮廓
                var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead);
                var fz = new List<ObjectId>();
                foreach (ObjectId lid in lt)
                {
                    var l = (LayerTableRecord)tr.GetObject(lid, OpenMode.ForRead);
                    if (Regex.IsMatch(l.Name, "TEXT|DIM|ANNO|TAG|文字|标注|说明|IDEN|NOTE", RegexOptions.IgnoreCase)) fz.Add(lid);
                }
                if (fz.Count > 0) vp.FreezeLayersInViewport(fz.GetEnumerator());
                foreach (var z in s.AllZones)
                {
                    bool cur = z == s.Zone;
                    var pl = new Polyline();
                    pl.SetDatabaseDefaults(db);
                    for (int i = 0; i < z.Poly.Count; i++) { var p = ToPaper(z.Poly[i], vc, c, cs); pl.AddVertexAt(i, new Point2d(p.X, p.Y), 0, cur ? 0.7 : 0.3, cur ? 0.7 : 0.3); }
                    pl.Closed = true; pl.Layer = L; pl.ColorIndex = cur ? 1 : 250;
                    ps.AppendEntity(pl); tr.AddNewlyCreatedDBObject(pl, true);
                    if (cur)
                    {
                        // 当前块：红色半透明填充
                        var h = new Hatch();
                        h.SetDatabaseDefaults(db);
                        ps.AppendEntity(h); tr.AddNewlyCreatedDBObject(h, true);
                        h.SetHatchPattern(HatchPatternType.PreDefined, "SOLID");
                        h.Layer = L; h.ColorIndex = 1;
                        h.Transparency = new Autodesk.AutoCAD.Colors.Transparency((byte)(255 * 0.45));
                        h.AppendLoop(HatchLoopTypes.Outermost, new ObjectIdCollection { pl.ObjectId });
                        h.EvaluateHatch(true);
                    }
                    // 字高按分区在索引图上的宽度定（中文字宽≈字高），放不下就缩小，太小就不写（当前块一定写）
                    double zw = (z.X1 - z.X0) * cs, zh = (z.Y1 - z.Y0) * cs;
                    var label = z.Name;
                    double th = Math.Min(cur ? 4.5 : 3.2, Math.Min(zw * 0.9 / Math.Max(1, label.Length), zh * 0.45));
                    if (th < 1.6 && !cur) continue;
                    var t = Text(db, ps, tr, label, ToPaper(z.Center, vc, c, cs), Math.Max(th, 2.0), L, TextHorizontalMode.TextCenter, TextVerticalMode.TextVerticalMid, style);
                    t.ColorIndex = cur ? 1 : 250;
                }
            }
            // ---- 图例
            if (zo.Legend && s.Layers != null)
                LegendLib.Draw(db, tr, ps, s.Layers, s.Legend, side.x0, legendBottom, side.x1, legendTop, L, style, log);
            // ---- 接图标记
            if (zo.MatchLabels && s.Zone != null && s.AllZones != null)
            {
                var z = s.Zone;
                double tol = Math.Max(z.X1 - z.X0, z.Y1 - z.Y0) * 0.03;
                double vx0 = main.CenterPoint.X - main.Width / 2, vx1 = main.CenterPoint.X + main.Width / 2, vy0 = main.CenterPoint.Y - main.Height / 2, vy1 = main.CenterPoint.Y + main.Height / 2;
                var cs = main.CustomScale; var vc = main.ViewCenter;
                foreach (var o in s.AllZones.Where(o => o != z))
                {
                    if (o.X0 > z.X1 + tol || o.X1 < z.X0 - tol || o.Y0 > z.Y1 + tol || o.Y1 < z.Y0 - tol) continue;   // 不相邻
                    // 共享边的中点（两个外包的重叠段）
                    double ox0 = Math.Max(o.X0, z.X0), ox1 = Math.Min(o.X1, z.X1), oy0 = Math.Max(o.Y0, z.Y0), oy1 = Math.Min(o.Y1, z.Y1);
                    var dx = o.Center.X - z.Center.X; var dy = o.Center.Y - z.Center.Y;
                    Point3d p; TextHorizontalMode hm; TextVerticalMode vm;
                    bool horiz = Math.Abs(dx) * (z.Y1 - z.Y0) > Math.Abs(dy) * (z.X1 - z.X0);
                    if (horiz)
                    {
                        var my = ToPaper(new Point2d(0, (Math.Max(oy0, z.Y0) + Math.Min(oy1, z.Y1)) / 2), vc, main.CenterPoint, cs).Y;
                        my = Math.Max(vy0 + 6, Math.Min(vy1 - 6, my));
                        p = new Point3d(dx > 0 ? vx1 - 2 : vx0 + 2, my, 0); hm = dx > 0 ? TextHorizontalMode.TextRight : TextHorizontalMode.TextLeft; vm = TextVerticalMode.TextVerticalMid;
                    }
                    else
                    {
                        var mx = ToPaper(new Point2d((Math.Max(ox0, z.X0) + Math.Min(ox1, z.X1)) / 2, 0), vc, main.CenterPoint, cs).X;
                        mx = Math.Max(vx0 + 20, Math.Min(vx1 - 20, mx));
                        p = new Point3d(mx, dy > 0 ? vy1 - 3 : vy0 + 3, 0); hm = TextHorizontalMode.TextCenter; vm = dy > 0 ? TextVerticalMode.TextTop : TextVerticalMode.TextBottom;
                    }
                    var t = Text(db, ps, tr, "接 " + o.Name + " 图", p, 6, "THL-接图", hm, vm, style);
                    // 箭头：从视口边向外指（粗线 + 三角）
                    var dir = horiz ? new Vector2d(dx > 0 ? 1 : -1, 0) : new Vector2d(0, dy > 0 ? 1 : -1);
                    var edge = horiz ? new Point2d(dx > 0 ? vx1 : vx0, p.Y) : new Point2d(p.X, dy > 0 ? vy1 : vy0);
                    var tail = edge - dir * 14;
                    var arrow = new Polyline();
                    arrow.SetDatabaseDefaults(db);
                    arrow.AddVertexAt(0, tail, 0, 0.7, 0.7);
                    arrow.AddVertexAt(1, edge - dir * 4, 0, 3, 0);
                    arrow.AddVertexAt(2, edge, 0, 0, 0);
                    arrow.Layer = "THL-接图";
                    ps.AppendEntity(arrow); tr.AddNewlyCreatedDBObject(arrow, true);
                    // 文字挪到箭头尾巴旁边
                    var off = horiz ? new Vector3d(-dir.X * 16, 5, 0) : new Vector3d(14, -dir.Y * 2, 0);
                    t.HorizontalMode = horiz ? (dx > 0 ? TextHorizontalMode.TextRight : TextHorizontalMode.TextLeft) : TextHorizontalMode.TextLeft;
                    t.VerticalMode = TextVerticalMode.TextVerticalMid;
                    t.AlignmentPoint = new Point3d(edge.X, edge.Y, 0) + off;
                    using (new WorkingDbScope(db)) t.AdjustAlignment(db);
                }
            }
        }
    }

    /// <summary>
    /// 图例库：%AppData%\Tianheluo\AutoCAD\settings\legend.txt，一行一条「图层通配|图例文字」（文字写 - 表示不进图例）。
    /// 没写到的图层：用图层说明，没有说明就用图层名。图例线画在该图层上，颜色、线型、线宽自然和图里一致。
    /// </summary>
    static class LegendLib
    {
        public static readonly string File = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo", "AutoCAD", "settings", "legend.txt");
        static readonly string[] DefaultHide = { "0", "Defpoints", "THL-*", "*TEXT*", "*DIM*", "*ANNO*", "*TAG*", "*IDEN*", "*NOTE*", "*文字*", "*标注*", "*轴号*", "*AXIS*", "*图框*", "*TITLE*", "*VIEWPORT*", "*视口*", "*-CNTR*", "*CENTER*", "*中心线*" };
        // 没写进图例库时：按 Revit / AIA 图层前缀给一个看得懂的中文名（同名同色的合成一行）
        static readonly (string pat, string name)[] DefaultNames =
        {
            ("P-PROT-*", "消防管道"), ("P-SANR-*", "污水管道"), ("P-STRM-*", "雨水管道"), ("P-DOMW-*", "给水管道"), ("P-PIPE*", "给排水管道"),
            ("P-EQPM*", "给排水设备"), ("P-FIXT*", "卫生器具"), ("E-POWR-CNDT*", "电力排管"), ("E-POWR-WIRE*", "电力电缆"), ("E-POWR-EQPM*", "电力设备"),
            ("E-LITE-EQPM*", "照明设备"), ("E-LITE*", "照明"), ("E-ELEC-EQPM*", "电气设备"), ("E-COMM*", "通信"), ("E-CTRL*", "弱电"), ("E-CABL*", "电缆桥架"),
            ("E-*", "电气"), ("M-DUCT*", "风管"), ("M-HVAC*", "暖通"), ("M-PIPE*", "暖通水管"), ("M-EQPM*", "暖通设备"), ("M-*", "暖通"),
            ("A-GENM*", "常规模型（井、构筑物等）"), ("S-BEAM*", "结构框架"), ("S-COLS*", "结构柱"), ("S-FNDN*", "基础"), ("C-TOPO*", "地形"), ("L-SITE*", "场地"),
        };

        public static List<(Regex re, string text)> Load(Action<string> log)
        {
            var list = new List<(Regex, string)>();
            try
            {
                if (!System.IO.File.Exists(File))
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(File));
                    System.IO.File.WriteAllText(File,
                        "# 天河落图集管理 · 图例库。一行一条：图层名（可用 * ? 通配）|图例文字；文字写 - 表示这个图层不进图例。\n" +
                        "# 例：\n# E-POWR-CNDT|电力排管\n# P-PROT-PIPE|消防给水管\n# *-TEXT|-\n" +
                        "# 管综出图默认：建筑 / 结构 / 场地 / 地形这些底图图层不进图例（要进就删掉对应行）\n" +
                        "A-*|-\nS-*|-\nC-*|-\nL-*|-\nQ-*|-\nG-*|-\n" +
                        "# 中文显示名（按自己项目的系统改；同名同色自动合成一行）：\n" +
                        "# P-PIPE-1|给水管\n# P-PIPE-3|消防管\n# E-POWR-CNDT-1|强电排管\n", new UTF8Encoding(true));
                }
                foreach (var raw in System.IO.File.ReadAllLines(File, Encoding.UTF8))
                {
                    var l = raw.Trim();
                    if (l.Length == 0 || l.StartsWith("#")) continue;
                    var i = l.IndexOf('|'); if (i <= 0) continue;
                    list.Add((Wild(l.Substring(0, i).Trim()), l.Substring(i + 1).Trim()));
                }
            }
            catch (Exception ex) { log?.Invoke("读图例库失败：" + ex.Message); }
            foreach (var h in DefaultHide) list.Add((Wild(h), "-"));
            return list;
        }

        /// <summary>图层的图例显示名：图例库 → 图层说明 → 按前缀的默认中文名 → 图层名；返回 "-" 表示不进图例。</summary>
        public static string NameOf(string layer, string description, List<(Regex re, string text)> lib)
        {
            var hit = lib?.FirstOrDefault(x => x.re.IsMatch(layer)) ?? default;
            if (hit.text != null) return hit.text;
            if (!string.IsNullOrWhiteSpace(description)) return description.Trim();
            return DefaultNames.FirstOrDefault(d => Wild(d.pat).IsMatch(layer)).name ?? layer;
        }

        /// <summary>把界面里编辑的图例表写回 legend.txt：表格里的图层一行一条（不进图例写 -），原文件里的通配规则保留。</summary>
        public static void SaveRows(IEnumerable<(string layer, string name, bool show)> rows)
        {
            var listed = new HashSet<string>(rows.Select(r => r.layer), StringComparer.OrdinalIgnoreCase);
            var keep = new List<string>();
            if (System.IO.File.Exists(File))
                foreach (var raw in System.IO.File.ReadAllLines(File, Encoding.UTF8))
                {
                    var l = raw.Trim();
                    if (l.StartsWith("#") || l.Length == 0) continue;
                    var i = l.IndexOf('|'); if (i <= 0) continue;
                    var pat = l.Substring(0, i).Trim();
                    if (pat.Contains("*") || pat.Contains("?") || !listed.Contains(pat)) keep.Add(l);
                }
            var sb = new StringBuilder();
            sb.AppendLine("# 天河落图集管理 · 图例库（在「设置 → 图例」表格里改最方便）。一行一条：图层名（可用 * ? 通配）|图例文字；文字写 - 表示不进图例。");
            foreach (var r in rows) sb.AppendLine(r.layer + "|" + (r.show ? (string.IsNullOrWhiteSpace(r.name) ? r.layer : r.name.Trim()) : "-"));
            sb.AppendLine("# ---- 通配规则");
            foreach (var l in keep) sb.AppendLine(l);
            Directory.CreateDirectory(Path.GetDirectoryName(File));
            System.IO.File.WriteAllText(File, sb.ToString(), new UTF8Encoding(true));
        }

        static bool SameColor(LayerTableRecord l, Autodesk.AutoCAD.Colors.Color c)
        {
            var a = l.Color.ColorValue; var b = c.ColorValue;
            return a.R == b.R && a.G == b.G && a.B == b.B;
        }

        static Regex Wild(string w) => new Regex("^" + Regex.Escape(w).Replace("\\*", ".*").Replace("\\?", ".") + "$", RegexOptions.IgnoreCase);

        public static void Draw(Database db, Transaction tr, BlockTableRecord ps, Dictionary<string, int> layers, List<(Regex re, string text)> lib,
                                double x0, double y0, double x1, double y1, string frameLayer, ObjectId style, Action<string> log)
        {
            var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead);
            var rows = new List<(string layer, string text)>();
            foreach (var kv in layers.OrderByDescending(kv => kv.Value))
            {
                if (!lt.Has(kv.Key)) continue;
                var ltr = (LayerTableRecord)tr.GetObject(lt[kv.Key], OpenMode.ForRead);
                if (ltr.IsDependent || ltr.IsOff || ltr.IsFrozen) continue;
                var hit = lib?.FirstOrDefault(x => x.re.IsMatch(kv.Key)) ?? default;
                if (hit.text == "-") continue;
                var text = hit.text;
                if (text == null && !string.IsNullOrWhiteSpace(ltr.Description)) text = ltr.Description.Trim();
                if (text == null)
                {
                    var dn = DefaultNames.FirstOrDefault(d => Wild(d.pat).IsMatch(kv.Key)).name;
                    text = dn ?? kv.Key;
                }
                // 同名同色合成一行；同名不同色：后面加序号区分（图例库里写清楚就不会出现）
                var color = ltr.Color;
                if (rows.Any(r => r.text == text && SameColor((LayerTableRecord)tr.GetObject(lt[r.layer], OpenMode.ForRead), color))) continue;
                int same = rows.Count(r => r.text == text || r.text.StartsWith(text + "（"));
                if (same > 0) text = text + "（" + (same + 1) + "）";
                rows.Add((kv.Key, text));
            }
            double rowH = 7.5, titleH = 11;
            int perCol = Math.Max(1, (int)Math.Floor((y1 - y0 - titleH - 2) / rowH));
            int cols = (x1 - x0) >= 160 ? 2 : 1;
            int max = perCol * cols;
            var lineType = db.LinetypeTableId;
            var frame = new Polyline(); frame.SetDatabaseDefaults(db);
            frame.AddVertexAt(0, new Point2d(x0, y0), 0, 0, 0); frame.AddVertexAt(1, new Point2d(x1, y0), 0, 0, 0);
            frame.AddVertexAt(2, new Point2d(x1, y1), 0, 0, 0); frame.AddVertexAt(3, new Point2d(x0, y1), 0, 0, 0);
            frame.Closed = true; frame.Layer = frameLayer; ps.AppendEntity(frame); tr.AddNewlyCreatedDBObject(frame, true);
            var title = new DBText(); title.SetDatabaseDefaults(db); title.TextString = "图  例"; title.Height = 5; title.Layer = frameLayer; title.TextStyleId = style;
            title.Position = new Point3d(x0 + 3, y1 - 7.5, 0); ps.AppendEntity(title); tr.AddNewlyCreatedDBObject(title, true);
            double colW = (x1 - x0) / cols;
            for (int i = 0; i < Math.Min(rows.Count, max); i++)
            {
                int col = i / perCol, r = i % perCol;
                double cx = x0 + col * colW + 3, cy = y1 - titleH - 4 - r * rowH;
                // 线样画成和图上一样的粗彩色线（图层颜色，宽 0.8mm）
                var ln = new Polyline();
                ln.SetDatabaseDefaults(db);
                ln.AddVertexAt(0, new Point2d(cx, cy), 0, 0.8, 0.8); ln.AddVertexAt(1, new Point2d(cx + 16, cy), 0, 0.8, 0.8);
                ln.Layer = rows[i].layer;
                ps.AppendEntity(ln); tr.AddNewlyCreatedDBObject(ln, true);
                var t = new DBText(); t.SetDatabaseDefaults(db);
                t.TextString = rows[i].text; t.Height = 3.5; t.Layer = frameLayer; t.TextStyleId = style;
                t.Position = new Point3d(cx + 19, cy - 1.75, 0);
                // 太长压扁
                double room = colW - 24;
                ps.AppendEntity(t); tr.AddNewlyCreatedDBObject(t, true);
                try { var e = t.GeometricExtents; var w = e.MaxPoint.X - e.MinPoint.X; if (w > room) t.WidthFactor = Math.Max(0.4, room / w); } catch { }
            }
            if (rows.Count > max) log?.Invoke($"图例 {rows.Count} 项，放得下 {max} 项（其余在图例库里写 - 隐藏，或加大右侧栏）");
        }
    }
}
