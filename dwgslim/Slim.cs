// 天河落 · DWG 瘦身（AutoCAD 后台命令 THLSLIM，在 accoreconsole 里运行）
//
// Revit 导出的 DWG 大，主要不是"没清理"，而是三件事：
//   1. Revit 给每个族实例单独生成一个块（块名带元素 ID）。几十口同型号的井，几何一模一样，却存了几十份。
//      → 几何相同的块合并成一份共用（同一型号只存一次）。图层颜色按实例保留（见 Dedupe 的说明）。
//   2. 导入/链接的 CAD 底图（块名形如「建筑底图-xxx_dwg-<ID>-<视图>」）整张塞进 DWG。→ 可选去掉。
//   3. 曲线被 Revit 炸成上万条首尾相连的短直线。→ 连成多段线（可选按容差抽稀，默认 0.5mm 肉眼不可见）。
// 最后反复清理没用到的块/图层/线型等，另存为 AutoCAD 2018 格式（比 Revit 默认的 2007 格式更紧凑）。
//
// 参数通过环境变量 THL_SLIM 传入（由插件启动 accoreconsole 前设置），格式：key=value;key=value
//   out=输出路径（必填）  log=日志路径  nocad=1  dedupe=1  join=1  tol=0.5（图形单位，mm）
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Autodesk.AutoCAD.ApplicationServices.Core;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;

#if !THL_PLUGIN
// 作为 accoreconsole 命令程序集时注册命令；编进天河落 AutoCAD 插件（THL_PLUGIN）时不注册，由插件直接调用 Slim.Process
[assembly: CommandClass(typeof(Thl.DwgSlim.Slim))]
[assembly: CommandClass(typeof(Thl.DwgSlim.Dump))]
#endif

namespace Thl.DwgSlim
{
    public class Slim
    {
        static readonly CultureInfo IC = CultureInfo.InvariantCulture;
        static StreamWriter _log;
        static Action<string> _sink;

        static void Log(string m)
        {
            try { _log?.WriteLine(DateTime.Now.ToString("HH:mm:ss ") + m); _log?.Flush(); } catch { }
            try { _sink?.Invoke(m); } catch { }
        }

        static Dictionary<string, string> Opts()
        {
            var d = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            var s = Environment.GetEnvironmentVariable("THL_SLIM") ?? "";
            foreach (var part in s.Split(';'))
            {
                var k = part.IndexOf('=');
                if (k > 0) d[part.Substring(0, k).Trim()] = part.Substring(k + 1).Trim();
            }
            return d;
        }

        static bool On(IDictionary<string, string> o, string k, bool def) =>
            o.TryGetValue(k, out var v) ? v == "1" || v.Equals("true", StringComparison.OrdinalIgnoreCase) : def;

        // 块名像井的：中文「井」、MANHOLE/HANDHOLE、EM1-/EM2-、TELCO_96WAY 这类（排管另外排除）
        const string DefaultWellPat = @"井|MANHOLE|HANDHOLE|\bEM\d-|_\d+WAY";

        static bool _done;

        [CommandMethod("THLSLIM", CommandFlags.Session)]
        public static void Run()
        {
            if (_done) return;        // 脚本末尾的空行会让 AutoCAD 重复上一个命令，只跑一次
            _done = true;
            var o = Opts();
            o.TryGetValue("log", out var logPath);
            if (!string.IsNullOrEmpty(logPath))
                _log = new StreamWriter(logPath, true, new UTF8Encoding(false));
            try
            {
                var acDoc = Application.DocumentManager.MdiActiveDocument;
                using var docLock = acDoc.LockDocument();
                if (!o.TryGetValue("out", out var outPath) || string.IsNullOrEmpty(outPath))
                    throw new ArgumentException("缺少 out= 输出路径");
                Process(acDoc.Database, o, outPath, null);
            }
            catch (System.Exception ex)
            {
                Log("失败：" + ex);
            }
            finally
            {
                _log?.Dispose();
                _log = null;
            }
        }

        /// <summary>瘦身一个数据库并另存到 outPath（AutoCAD 2018 格式）。会改动 db 本身：传进来的应是副本（侧数据库 / Wblock 出来的）。
        /// o：nocad、join、tol、minsize、wells、wellpat、wellgap、dedupe（含义见文件头）。log：每一步的结果。</summary>
        public static void Process(Database db, IDictionary<string, string> o, string outPath, Action<string> log)
        {
            _sink = log;
            try
            {
                double tol = o.TryGetValue("tol", out var ts) && double.TryParse(ts, NumberStyles.Float, IC, out var tv) ? tv : 0.5;
                Log("开始：" + db.Filename);
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    double minsize = o.TryGetValue("minsize", out var ms) && double.TryParse(ms, NumberStyles.Float, IC, out var mv) ? mv : 0;
                    if (On(o, "nocad", false)) RemoveImportedCad(db, tr);
                    // 先连线、去碎图形，再合并块：同型号的块处理结果相同，合并照样成立
                    if (On(o, "join", true)) JoinAll(db, tr, tol);
                    if (minsize > 0) DropTiny(db, tr, minsize);
                    if (On(o, "wells", false))
                        WellClean(db, tr, new Regex(o.TryGetValue("wellpat", out var wp) && wp.Length > 0 ? wp : DefaultWellPat, RegexOptions.IgnoreCase),
                                  o.TryGetValue("wellgap", out var wg) && double.TryParse(wg, NumberStyles.Float, IC, out var wgv) ? wgv : 100);
                    if (On(o, "dedupe", true)) Dedupe(db, tr);
                    tr.Commit();
                }
                PurgeAll(db);
                db.SaveAs(outPath, DwgVersion.AC1032);
                Log("完成：" + outPath + "  " + (new FileInfo(outPath).Length / 1048576.0).ToString("0.0", IC) + " MB");
            }
            finally { _sink = null; }
        }

        // ---------------- 1. 导入的 CAD ----------------
        // Revit 把导入/链接的 DWG 导出成块，块名 = 文件名（点换成下划线）+ "-<元素ID>-<视图名>"，如「建筑底图-20261024_dwg-13944889-…」
        static readonly Regex CadBlock = new Regex(@"_(dwg|dxf|dgn)-\d+-", RegexOptions.IgnoreCase);

        static void RemoveImportedCad(Database db, Transaction tr)
        {
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            var cad = new HashSet<ObjectId>();
            foreach (ObjectId id in bt)
            {
                var b = (BlockTableRecord)tr.GetObject(id, OpenMode.ForRead);
                if (!b.IsLayout && CadBlock.IsMatch(b.Name)) cad.Add(id);
            }
            int n = 0;
            foreach (var owner in AllOwners(db, tr))
                foreach (ObjectId eid in owner)
                {
                    if (!eid.ObjectClass.IsDerivedFrom(RXObject.GetClass(typeof(BlockReference)))) continue;
                    var br = (BlockReference)tr.GetObject(eid, OpenMode.ForRead);
                    if (cad.Contains(br.BlockTableRecord)) { br.UpgradeOpen(); br.Erase(); n++; }
                }
            Log($"去掉导入的 CAD：{cad.Count} 个块、{n} 处引用");
        }

        /// <summary>模型空间 + 模型空间（递归）引用到的普通块。图框（图纸空间里的块）、标注/匿名块不在内，连线和去碎图形只动这些。</summary>
        static List<BlockTableRecord> ModelOwners(Database db, Transaction tr)
        {
            var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
            var seen = new HashSet<ObjectId> { ms.ObjectId };
            var res = new List<BlockTableRecord> { ms };
            var brClass = RXObject.GetClass(typeof(BlockReference));
            for (int i = 0; i < res.Count; i++)
                foreach (ObjectId eid in res[i])
                {
                    if (!eid.ObjectClass.IsDerivedFrom(brClass) || eid.ObjectClass.DxfName != "INSERT") continue;
                    var br = (BlockReference)tr.GetObject(eid, OpenMode.ForRead);
                    var bid = br.BlockTableRecord;
                    if (!seen.Add(bid)) continue;
                    var b = (BlockTableRecord)tr.GetObject(bid, OpenMode.ForRead);
                    if (b.IsAnonymous || b.IsLayout || b.IsFromExternalReference || b.IsDependent) continue;
                    res.Add(b);
                }
            return res;
        }

        static IEnumerable<BlockTableRecord> AllOwners(Database db, Transaction tr)
        {
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            foreach (ObjectId id in bt)
            {
                var b = (BlockTableRecord)tr.GetObject(id, OpenMode.ForRead);
                if (b.IsFromExternalReference || b.IsDependent || b.IsErased) continue;
                yield return b;
            }
        }

        // ---------------- 2. 合并几何相同的块 ----------------
        // Revit 的每个实例块里，内容通常全在一个图层上（A-GENM-5、A-GENM-11…，图层号随视图里的颜色覆盖变化）。
        // 只差图层的块也合并：共用的那份内容改到 0 层（颜色/线型都随层），引用改放到原来的图层上，
        // 这样 CAD 里看到的颜色、线型、开关图层都和原来一样。内容跨多个图层的块，只有图层也完全相同才合并。
        static void Dedupe(Database db, Transaction tr)
        {
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            var groups = new Dictionary<string, List<(ObjectId id, ObjectId layer)>>();
            int total = 0;
            foreach (ObjectId id in bt)
            {
                var b = (BlockTableRecord)tr.GetObject(id, OpenMode.ForRead);
                if (b.IsLayout || b.IsAnonymous || b.IsFromExternalReference || b.IsDependent || b.IsDynamicBlock) continue;
                var sig = Signature(tr, b, out var singleLayer);
                if (sig == null) continue;
                total++;
                var key = singleLayer.IsNull ? "M|" + sig : "S|" + sig;
                if (!groups.TryGetValue(key, out var list)) groups[key] = list = new List<(ObjectId, ObjectId)>();
                list.Add((id, singleLayer));
            }
            var remap = new Dictionary<ObjectId, (ObjectId master, ObjectId layer)>();
            var zero = db.LayerZero;
            foreach (var kv in groups)
            {
                var list = kv.Value;
                if (list.Count < 2) continue;
                var master = list[0].id;
                bool single = kv.Key.StartsWith("S|");
                if (single)
                {
                    var mb = (BlockTableRecord)tr.GetObject(master, OpenMode.ForRead);
                    foreach (ObjectId eid in mb)
                    {
                        var e = (Entity)tr.GetObject(eid, OpenMode.ForWrite);
                        e.LayerId = zero;
                    }
                }
                foreach (var (id, layer) in list)
                    remap[id] = (master, single ? layer : ObjectId.Null);
            }
            int refs = 0;
            foreach (var owner in AllOwners(db, tr))
                foreach (ObjectId eid in owner)
                {
                    if (!eid.ObjectClass.IsDerivedFrom(RXObject.GetClass(typeof(BlockReference)))) continue;
                    var br = (BlockReference)tr.GetObject(eid, OpenMode.ForRead);
                    if (!remap.TryGetValue(br.BlockTableRecord, out var m)) continue;
                    br.UpgradeOpen();
                    if (br.BlockTableRecord != m.master) br.BlockTableRecord = m.master;
                    if (!m.layer.IsNull) br.LayerId = m.layer;
                    refs++;
                }
            int merged = remap.Count(kv => kv.Key != kv.Value.master);
            Log($"合并相同的块：{total} 个块 → {total - merged} 份（{merged} 个重复的并掉，改了 {refs} 处引用）");
        }

        static string R(double v) => Math.Round(v, 2).ToString("0.##", IC);
        static string P(Point3d p) => R(p.X) + "," + R(p.Y) + "," + R(p.Z);

        /// <summary>块内容的几何签名（不含句柄）；遇到识别不了的图元返回 null（不合并，安全第一）。
        /// singleLayer：内容全在同一个非 0 图层上时返回该图层，否则 Null。</summary>
        static string Signature(Transaction tr, BlockTableRecord b, out ObjectId singleLayer)
        {
            singleLayer = ObjectId.Null;
            var layers = new HashSet<ObjectId>();
            var sb = new StringBuilder();
            sb.Append(P(b.Origin)).Append('|');
            var parts = new List<string>();
            foreach (ObjectId eid in b)
            {
                var e = tr.GetObject(eid, OpenMode.ForRead) as Entity;
                if (e == null) continue;
                layers.Add(e.LayerId);
                string g;
                switch (e)
                {
                    case Line l: g = "L" + P(l.StartPoint) + ";" + P(l.EndPoint); break;
                    case Arc a: g = "A" + P(a.Center) + ";" + R(a.Radius) + ";" + R(a.StartAngle * 1000) + ";" + R(a.EndAngle * 1000) + ";" + R(a.Normal.Z); break;
                    case Circle c: g = "C" + P(c.Center) + ";" + R(c.Radius) + ";" + R(c.Normal.Z); break;
                    case Ellipse el: g = "E" + P(el.Center) + ";" + R(el.MajorAxis.X) + "," + R(el.MajorAxis.Y) + ";" + R(el.RadiusRatio * 1000) + ";" + R(el.StartAngle * 1000) + ";" + R(el.EndAngle * 1000); break;
                    case Polyline pl:
                        {
                            var s = new StringBuilder("P" + (pl.Closed ? "c" : "o") + R(pl.Elevation));
                            for (int i = 0; i < pl.NumberOfVertices; i++) s.Append(';').Append(R(pl.GetPoint2dAt(i).X)).Append(',').Append(R(pl.GetPoint2dAt(i).Y)).Append(',').Append(R(pl.GetBulgeAt(i) * 1000));
                            g = s.ToString(); break;
                        }
                    case Spline sp:
                        {
                            var s = new StringBuilder("S" + sp.Degree);
                            for (int i = 0; i < sp.NumControlPoints; i++) s.Append(';').Append(P(sp.GetControlPointAt(i)));
                            for (int i = 0; i < sp.NumFitPoints; i++) s.Append(';').Append(P(sp.GetFitPointAt(i)));
                            g = s.ToString(); break;
                        }
                    case BlockReference br: g = "B" + br.BlockTableRecord.Handle + ";" + P(br.Position) + ";" + R(br.Rotation * 1000) + ";" + R(br.ScaleFactors.X * 1000) + "," + R(br.ScaleFactors.Y * 1000) + "," + R(br.ScaleFactors.Z * 1000); break;
                    case DBText t: g = "T" + t.TextString + ";" + P(t.Position) + ";" + R(t.Height) + ";" + R(t.Rotation * 1000) + ";" + t.TextStyleId.Handle; break;
                    case MText mt: g = "M" + mt.Contents + ";" + P(mt.Location) + ";" + R(mt.TextHeight) + ";" + R(mt.Rotation * 1000) + ";" + mt.TextStyleId.Handle; break;
                    case Hatch h:
                        {
                            var ext = h.Bounds;
                            if (ext == null) return null;
                            g = "H" + h.PatternName + ";" + R(h.PatternScale * 1000) + ";" + R(h.PatternAngle * 1000) + ";" + h.NumberOfLoops + ";" + P(ext.Value.MinPoint) + ";" + P(ext.Value.MaxPoint); break;
                        }
                    case DBPoint pt: g = "D" + P(pt.Position); break;
                    default: return null;
                }
                parts.Add(g + "|" + e.Color.ColorValue.ToArgb() + "|" + e.Linetype + "|" + (int)e.LineWeight + "|" + R(e.LinetypeScale * 100) + "|#" + e.LayerId.Handle);
            }
            if (parts.Count == 0) return null;
            ObjectId only = layers.Count == 1 ? layers.First() : ObjectId.Null;
            bool single = !only.IsNull && only != b.Database.LayerZero;
            if (single)
            {
                singleLayer = only;
                var tag = "|#" + only.Handle;
                for (int i = 0; i < parts.Count; i++) parts[i] = parts[i].Replace(tag, "|#L");
            }
            parts.Sort(StringComparer.Ordinal);       // 顺序无关
            foreach (var p in parts) sb.Append(p).Append('\n');
            // 用 SHA1 压成短字符串，字典省内存
            using var sha = System.Security.Cryptography.SHA1.Create();
            return parts.Count + ":" + Convert.ToBase64String(sha.ComputeHash(Encoding.UTF8.GetBytes(sb.ToString())));
        }

        // ---------------- 3. 短直线连成多段线 ----------------
        static void JoinAll(Database db, Transaction tr, double tol)
        {
            long before = 0, after = 0;
            foreach (var owner in ModelOwners(db, tr))
            {
                var (b, a) = Join(tr, owner, tol);
                before += b; after += a;
            }
            Log($"短直线连成多段线：{before} 条直线 → {after} 条多段线（容差 {tol.ToString(IC)}）");
        }

        sealed class Seg { public ObjectId Id; public Point3d A, B; public bool Used; }

        static (int, int) Join(Transaction tr, BlockTableRecord owner, double tol)
        {
            // 按「图层/颜色/线型/线宽/线型比例/标高」分组，只有完全相同的才连
            var groups = new Dictionary<string, List<Seg>>();
            foreach (ObjectId eid in owner)
            {
                if (eid.ObjectClass.DxfName != "LINE") continue;
                var l = (Line)tr.GetObject(eid, OpenMode.ForRead);
                if (Math.Abs(l.StartPoint.Z - l.EndPoint.Z) > 1e-6 || l.Normal.Z < 0.999999 || l.Thickness != 0) continue;
                if (l.StartPoint.DistanceTo(l.EndPoint) < 1e-9) continue;
                var key = l.LayerId.Handle + "|" + l.Color.ColorValue.ToArgb() + "|" + l.LinetypeId.Handle + "|" + (int)l.LineWeight + "|" + l.LinetypeScale.ToString("R", IC) + "|" + l.StartPoint.Z.ToString("R", IC) + "|" + l.Transparency.ToString();
                if (!groups.TryGetValue(key, out var list)) groups[key] = list = new List<Seg>();
                list.Add(new Seg { Id = eid, A = l.StartPoint, B = l.EndPoint });
            }
            int nLines = 0, nPolys = 0;
            if (groups.Count == 0) return (0, 0);
            owner.UpgradeOpen();
            foreach (var list in groups.Values)
            {
                if (list.Count < 2) continue;
                // 端点按 0.001 取整做索引
                var ends = new Dictionary<(long, long), List<Seg>>();
                (long, long) K(Point3d p) => ((long)Math.Round(p.X * 1000), (long)Math.Round(p.Y * 1000));
                foreach (var s in list)
                {
                    foreach (var k in new[] { K(s.A), K(s.B) })
                    {
                        if (!ends.TryGetValue(k, out var l)) ends[k] = l = new List<Seg>(2);
                        l.Add(s);
                    }
                }
                var first = (Line)tr.GetObject(list[0].Id, OpenMode.ForRead);
                // 先从「不是两两相接」的端点出发走链，剩下的是闭合环
                var starts = list.Where(s => ends[K(s.A)].Count != 2 || ends[K(s.B)].Count != 2).Concat(list);
                foreach (var s0 in starts)
                {
                    if (s0.Used) continue;
                    var pts = new List<Point3d>();
                    var segs = new List<Seg>();
                    // 起点选度数不为 2 的那端
                    bool fwd = ends[K(s0.A)].Count != 2 || ends[K(s0.B)].Count == 2;
                    Point3d cur = fwd ? s0.A : s0.B;
                    pts.Add(cur);
                    var s = s0;
                    while (s != null)
                    {
                        s.Used = true; segs.Add(s);
                        var nxt = K(s.A) == K(cur) ? s.B : s.A;
                        pts.Add(nxt);
                        cur = nxt;
                        var cand = ends[K(cur)];
                        s = cand.Count == 2 ? cand.FirstOrDefault(x => !x.Used) : null;   // 只在正好两条相接处继续（分叉处断开，保证不丢线）
                    }
                    if (segs.Count < 2) { s0.Used = segs.Count == 1 ? true : s0.Used; continue; }
                    bool closed = K(pts[0]) == K(pts[pts.Count - 1]);
                    if (closed) pts.RemoveAt(pts.Count - 1);
                    var simp = tol > 0 ? Simplify(pts, tol, closed) : pts;
                    var pl = new Polyline(simp.Count);
                    pl.SetDatabaseDefaults(owner.Database);
                    for (int i = 0; i < simp.Count; i++) pl.AddVertexAt(i, new Point2d(simp[i].X, simp[i].Y), 0, 0, 0);
                    pl.Closed = closed;
                    pl.Elevation = pts[0].Z;
                    pl.LayerId = first.LayerId; pl.Color = first.Color; pl.LinetypeId = first.LinetypeId;
                    pl.LineWeight = first.LineWeight; pl.LinetypeScale = first.LinetypeScale; pl.Transparency = first.Transparency;
                    pl.Plinegen = true;
                    owner.AppendEntity(pl); tr.AddNewlyCreatedDBObject(pl, true);
                    foreach (var x in segs) ((Entity)tr.GetObject(x.Id, OpenMode.ForWrite)).Erase();
                    nLines += segs.Count; nPolys++;
                }
            }
            return (nLines, nPolys);
        }

        /// <summary>Douglas-Peucker 抽稀：偏差不超过 tol 的中间点去掉（只去"几乎在一条直线上"的点，轮廓不变）。</summary>
        static List<Point3d> Simplify(List<Point3d> pts, double tol, bool closed)
        {
            if (pts.Count < 3) return pts;
            if (closed)
            {
                // 闭合环：从最远的两点切成两段分别抽稀
                int far = 0; double best = -1;
                for (int i = 1; i < pts.Count; i++) { var d = pts[i].DistanceTo(pts[0]); if (d > best) { best = d; far = i; } }
                var a = DP(pts.GetRange(0, far + 1), tol);
                var b2 = pts.GetRange(far, pts.Count - far); b2.Add(pts[0]);
                var b = DP(b2, tol);
                a.RemoveAt(a.Count - 1);
                b.RemoveAt(b.Count - 1);
                a.AddRange(b);
                return a.Count >= 3 ? a : pts;
            }
            return DP(pts, tol);
        }

        static List<Point3d> DP(List<Point3d> pts, double tol)
        {
            var keep = new bool[pts.Count];
            keep[0] = keep[pts.Count - 1] = true;
            var stack = new Stack<(int, int)>();
            stack.Push((0, pts.Count - 1));
            while (stack.Count > 0)
            {
                var (i0, i1) = stack.Pop();
                if (i1 - i0 < 2) continue;
                double maxd = -1; int idx = -1;
                var p0 = pts[i0]; var p1 = pts[i1];
                var dx = p1.X - p0.X; var dy = p1.Y - p0.Y; var len = Math.Sqrt(dx * dx + dy * dy);
                for (int i = i0 + 1; i < i1; i++)
                {
                    double d = len < 1e-12 ? pts[i].DistanceTo(p0) : Math.Abs(dy * (pts[i].X - p0.X) - dx * (pts[i].Y - p0.Y)) / len;
                    if (d > maxd) { maxd = d; idx = i; }
                }
                if (maxd > tol) { keep[idx] = true; stack.Push((i0, idx)); stack.Push((idx, i1)); }
            }
            var r = new List<Point3d>();
            for (int i = 0; i < pts.Count; i++) if (keep[i]) r.Add(pts[i]);
            return r;
        }

        // ---------------- 3b. 去掉出图比例下看不见的碎图形 ----------------
        // 井盖浮雕字（DANGER 之类的标识）、防滑方格这类 25mm 大小的细节，每口井几千条线；1:500 出图时只有 0.05mm，打印出来是一团灰。
        // 规则：整个外包框的对角线小于 minsize（图形单位）的线、多段线、圆弧、圆、椭圆、样条去掉。文字、块、填充、标注不动。
        static void DropTiny(Database db, Transaction tr, double minsize)
        {
            long n = 0;
            var curveClass = RXObject.GetClass(typeof(Curve));
            foreach (var owner in ModelOwners(db, tr))
            {
                foreach (ObjectId eid in owner)
                {
                    if (!eid.ObjectClass.IsDerivedFrom(curveClass)) continue;
                    var c = (Curve)tr.GetObject(eid, OpenMode.ForRead);
                    Extents3d ext;
                    try { ext = c.GeometricExtents; } catch { continue; }
                    var dx = ext.MaxPoint.X - ext.MinPoint.X; var dy = ext.MaxPoint.Y - ext.MinPoint.Y;
                    if (Math.Sqrt(dx * dx + dy * dy) >= minsize) continue;
                    c.UpgradeOpen(); c.Erase(); n++;
                }
            }
            Log($"去掉碎图形（外包小于 {minsize.ToString(IC)}）：{n} 个");
        }

        // ---------------- 3c. 井只留外壁、内壁、井盖外框 ----------------
        // 井族在平面里还有井盖多重框线、长条孔、井型大字（EM1/LV…）、爬梯等，管综图只要三圈线：外壁、内壁、井盖外框。
        // 识别：块里首尾相连的曲线归成一组，「框形」= 整组线都贴着这组的外包框走（矩形、圆角矩形）；闭合曲线（圆、闭合多段线）也算一圈。
        // 圈按面积从大到小，被已保留的圈包住、且四周离它都不到 gap 的（同一井盖的多重框线）不要；太小的（< 块尺寸 12%）不要。
        // 其余曲线（字、碎线、孔）删掉；块里的文字、填充、嵌套块不动。只处理块名像井的块（wellpat）。
        static void WellClean(Database db, Transaction tr, Regex wellPat, double gap)
        {
            int blocks = 0; long removed = 0;
            foreach (var b in ModelOwners(db, tr))
            {
                if (b.IsLayout || !wellPat.IsMatch(b.Name) || b.Name.Contains("排管")) continue;
                var curves = CurvesOf(tr, b, null);
                var keep = WellKeep(curves, false);
                if (keep == null) continue;
                b.UpgradeOpen();
                for (int i = 0; i < curves.Count; i++)
                    if (!keep.Contains(i)) { ((Entity)tr.GetObject(curves[i].id, OpenMode.ForWrite)).Erase(); removed++; }
                blocks++;
            }
            // 模型空间里直接画着的井（没导成块的）：在「常规模型」图层上找「外壁+同心内壁」的井轮廓，框内的线按同样规则处理
            var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
            var all = CurvesOf(tr, ms, l => l.StartsWith("A-GENM", StringComparison.OrdinalIgnoreCase));
            int msWells = 0;
            if (all.Count > 0)
            {
                var loops = FindLoops(all, Enumerable.Range(0, all.Count).ToList(), 300);
                var wells = loops.Where(L => { double w = L.x1 - L.x0, h = L.y1 - L.y0; return Math.Max(w, h) <= 6000 && Math.Min(w, h) >= 800; })
                                 .Where(L => loops.Any(I => Concentric(I, L, 50, 400)))
                                 .OrderByDescending(L => (L.x1 - L.x0) * (L.y1 - L.y0)).ToList();
                var done = new List<Loop>();
                var dead = new HashSet<int>();
                foreach (var Wl in wells)
                {
                    if (done.Any(D => Inside(Wl, D))) continue;
                    done.Add(Wl);
                    var idx = Enumerable.Range(0, all.Count).Where(i => !dead.Contains(i) &&
                        all[i].ext.MinPoint.X >= Wl.x0 - 1 && all[i].ext.MinPoint.Y >= Wl.y0 - 1 && all[i].ext.MaxPoint.X <= Wl.x1 + 1 && all[i].ext.MaxPoint.Y <= Wl.y1 + 1).ToList();
                    var sub = idx.Select(i => all[i]).ToList();
                    var keep = WellKeep(sub, true);
                    if (keep == null) continue;
                    msWells++;
                    for (int k = 0; k < sub.Count; k++)
                        if (!keep.Contains(k)) { ((Entity)tr.GetObject(sub[k].id, OpenMode.ForWrite)).Erase(); dead.Add(idx[k]); removed++; }
                }
            }
            Log($"井只留外壁/内壁/井盖外框：井块 {blocks} 个、模型空间里的井 {msWells} 口，删掉 {removed} 条线");
        }

        struct Loop { public List<int> m; public double x0, y0, x1, y1; }

        static bool Inside(Loop a, Loop c) => a.x0 >= c.x0 - 1 && a.y0 >= c.y0 - 1 && a.x1 <= c.x1 + 1 && a.y1 <= c.y1 + 1;

        /// <summary>I 在 O 里、同心、长宽比一样、四周壁厚在 (minG, maxG) 之间（内壁 vs 外壁）。</summary>
        static bool Concentric(Loop I, Loop O, double minG, double maxG)
        {
            double g1 = I.x0 - O.x0, g2 = O.x1 - I.x1, g3 = I.y0 - O.y0, g4 = O.y1 - I.y1;
            double ar0 = (O.x1 - O.x0) / Math.Max(1e-6, O.y1 - O.y0), ar = (I.x1 - I.x0) / Math.Max(1e-6, I.y1 - I.y0);
            return Math.Min(Math.Min(g1, g2), Math.Min(g3, g4)) > minG && Math.Max(Math.Max(g1, g2), Math.Max(g3, g4)) < maxG
                && Math.Abs(ar - ar0) < 0.1 * ar0 && Math.Abs(g1 - g2) < 30 && Math.Abs(g3 - g4) < 30;
        }

        static List<(ObjectId id, Curve c, Extents3d ext)> CurvesOf(Transaction tr, BlockTableRecord b, Func<string, bool> layerOk)
        {
            var curves = new List<(ObjectId id, Curve c, Extents3d ext)>();
            var cc = RXObject.GetClass(typeof(Curve));
            foreach (ObjectId eid in b)
            {
                if (!eid.ObjectClass.IsDerivedFrom(cc)) continue;
                var c = (Curve)tr.GetObject(eid, OpenMode.ForRead);
                if (layerOk != null && !layerOk(c.Layer)) continue;
                try { curves.Add((eid, c, c.GeometricExtents)); } catch { }
            }
            return curves;
        }

        /// <summary>首尾相连的曲线归组，找出「框」（四面围住的矩形/圆角矩形外框，或一条闭合曲线）。minDim：框最长边下限。</summary>
        static List<Loop> FindLoops(List<(ObjectId id, Curve c, Extents3d ext)> curves, List<int> ids, double minDim)
        {
            var parent = new Dictionary<int, int>();
            foreach (var i in ids) parent[i] = i;
            int Find(int i) { while (parent[i] != i) i = parent[i] = parent[parent[i]]; return i; }
            var ends = new Dictionary<(long, long), int>();
            (long, long) K(Point3d p) => ((long)Math.Round(p.X * 2), (long)Math.Round(p.Y * 2));   // 0.5 容差
            foreach (var i in ids)
            {
                var c = curves[i].c;
                if (c.Closed) continue;
                foreach (var p in new[] { c.StartPoint, c.EndPoint })
                {
                    var k = K(p);
                    if (ends.TryGetValue(k, out var j)) parent[Find(i)] = Find(j); else ends[k] = i;
                }
            }
            double Len(int i) { try { var c = curves[i].c; return c.GetDistanceAtParameter(c.EndParam); } catch { return 0; } }
            var loops = new List<Loop>();
            foreach (var g in ids.GroupBy(Find).Select(x => x.ToList()))
            {
                double x0 = g.Min(i => curves[i].ext.MinPoint.X), y0 = g.Min(i => curves[i].ext.MinPoint.Y);
                double x1 = g.Max(i => curves[i].ext.MaxPoint.X), y1 = g.Max(i => curves[i].ext.MaxPoint.Y);
                double w = x1 - x0, h = y1 - y0;
                if (Math.Max(w, h) < minDim || Math.Min(w, h) < minDim * 0.4) continue;
                if (g.Count == 1 && curves[g[0]].c.Closed) { loops.Add(new Loop { m = g, x0 = x0, y0 = y0, x1 = x1, y1 = y1 }); continue; }
                // 贴边：线上的采样点都离这组的外包框边不远（圆角矩形的圆角允许 8% 偏离）
                double tolB = Math.Max(w, h) * 0.08;
                bool OnEdge(int i)
                {
                    var c = curves[i].c;
                    for (int k = 0; k <= 8; k++)
                    {
                        Point3d p;
                        try { p = c.GetPointAtParameter(c.StartParam + (c.EndParam - c.StartParam) * k / 8.0); } catch { return false; }
                        if (Math.Min(Math.Min(p.X - x0, x1 - p.X), Math.Min(p.Y - y0, y1 - p.Y)) > tolB) return false;
                    }
                    return true;
                }
                // 整组都贴边 = 一个框；几个框连在一起（井盖多重框线交叠）时，只取贴着外轮廓的那些线 = 外框
                var members = g.Where(OnEdge).ToList();
                // 四面都围住：上下左右每条边都有一半以上长度有线（字母 E、M 和零散的边过不了）
                var side = new double[4];
                foreach (var (a, b2) in members.SelectMany(i => Segs(curves[i].c)))
                {
                    if (Math.Abs(a.Y - y0) <= tolB && Math.Abs(b2.Y - y0) <= tolB) side[0] += Math.Abs(b2.X - a.X);
                    if (Math.Abs(a.Y - y1) <= tolB && Math.Abs(b2.Y - y1) <= tolB) side[1] += Math.Abs(b2.X - a.X);
                    if (Math.Abs(a.X - x0) <= tolB && Math.Abs(b2.X - x0) <= tolB) side[2] += Math.Abs(b2.Y - a.Y);
                    if (Math.Abs(a.X - x1) <= tolB && Math.Abs(b2.X - x1) <= tolB) side[3] += Math.Abs(b2.Y - a.Y);
                }
                bool ring = members.Count > 0 && side[0] >= w * 0.5 && side[1] >= w * 0.5 && side[2] >= h * 0.5 && side[3] >= h * 0.5;
                if (ring && members.Count < g.Count)
                {
                    // 空心字（E、M）外轮廓也贴边，但里面的线和外轮廓差不多长；交叠的井盖框里面的线少
                    var mset = new HashSet<int>(members);
                    ring = g.Where(i => !mset.Contains(i)).Sum(Len) <= 0.6 * members.Sum(Len);
                }
                if (ring) loops.Add(new Loop { m = members, x0 = x0, y0 = y0, x1 = x1, y1 = y1 });
            }
            return loops;
        }

        /// <summary>一口井的曲线里要留的（外壁、内壁、井盖外框）。认不出框返回 null（不动）；requireInner：认不出内壁也不动。</summary>
        static HashSet<int> WellKeep(List<(ObjectId id, Curve c, Extents3d ext)> curves, bool requireInner)
        {
            if (curves.Count < 4) return null;
            double bx0 = curves.Min(x => x.ext.MinPoint.X), by0 = curves.Min(x => x.ext.MinPoint.Y);
            double bx1 = curves.Max(x => x.ext.MaxPoint.X), by1 = curves.Max(x => x.ext.MaxPoint.Y);
            double bsize = Math.Max(bx1 - bx0, by1 - by0);
            if (bsize <= 0) return null;
            var loops = FindLoops(curves, Enumerable.Range(0, curves.Count).ToList(), bsize * 0.12);
            if (loops.Count == 0) return null;
            loops.Sort((a, c2) => ((c2.x1 - c2.x0) * (c2.y1 - c2.y0)).CompareTo((a.x1 - a.x0) * (a.y1 - a.y0)));
            // 最大一圈 = 外壁；和外壁同心、长宽比一样的最大一圈 = 内壁（井盖框可能比内壁还大，但长宽比/位置不同）；
            // 其余（井盖框）只留不被别的井盖框套住的最外一圈
            var o0 = loops[0];
            var kept = new List<Loop> { o0 };
            var rest = loops.Skip(1).Where(L => Inside(L, o0)).ToList();
            int innerIdx = rest.FindIndex(L => Concentric(L, o0, 20, bsize * 0.2));
            if (innerIdx >= 0) { kept.Add(rest[innerIdx]); rest.RemoveAt(innerIdx); }
            else if (requireInner) return null;
            foreach (var L in rest)
                if (!rest.Any(o2 => !ReferenceEquals(o2.m, L.m) && Inside(L, o2) && (o2.x1 - o2.x0) * (o2.y1 - o2.y0) > (L.x1 - L.x0) * (L.y1 - L.y0)))
                    kept.Add(L);
            return new HashSet<int>(kept.SelectMany(k => k.m));
        }

        /// <summary>曲线拆成直线段（多段线按顶点、其它曲线按 8 段折线），用来判断贴在外框哪条边上。</summary>
        static IEnumerable<(Point3d, Point3d)> Segs(Curve c)
        {
            var r = new List<(Point3d, Point3d)>();
            try
            {
                if (c is Line l) r.Add((l.StartPoint, l.EndPoint));
                else if (c is Polyline pl)
                {
                    int nv = pl.NumberOfVertices;
                    for (int i = 0; i < (pl.Closed ? nv : nv - 1); i++) r.Add((pl.GetPoint3dAt(i), pl.GetPoint3dAt((i + 1) % nv)));
                }
                else
                {
                    Point3d prev = c.GetPointAtParameter(c.StartParam);
                    for (int k = 1; k <= 8; k++) { var p = c.GetPointAtParameter(c.StartParam + (c.EndParam - c.StartParam) * k / 8.0); r.Add((prev, p)); prev = p; }
                }
            }
            catch { }
            return r;
        }

        // ---------------- 4. 清理 ----------------
        static void PurgeAll(Database db)
        {
            for (int pass = 0; pass < 6; pass++)
            {
                int n = 0;
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    var ids = new ObjectIdCollection();
                    foreach (var tid in new[] { db.BlockTableId, db.LayerTableId, db.LinetypeTableId, db.TextStyleTableId, db.DimStyleTableId, db.RegAppTableId })
                    {
                        var t = (SymbolTable)tr.GetObject(tid, OpenMode.ForRead);
                        foreach (ObjectId id in t) ids.Add(id);
                    }
                    db.Purge(ids);
                    foreach (ObjectId id in ids)
                    {
                        var o = tr.GetObject(id, OpenMode.ForWrite);
                        try { o.Erase(); n++; } catch { }
                    }
                    tr.Commit();
                }
                Log($"清理第 {pass + 1} 遍：{n} 项");
                if (n == 0) break;
            }
        }
    }

    /// <summary>测试用：把窗口内的曲线（块展开到世界坐标）离散成线段写到文本，方便出图比对。THL_DUMP=out=..;x0=..;y0=..;x1=..;y1=..</summary>
    public class Dump
    {
        [CommandMethod("THLDUMP", CommandFlags.Session)]
        public static void Run()
        {
            var o = new Dictionary<string, string>();
            foreach (var p in (Environment.GetEnvironmentVariable("THL_DUMP") ?? "").Split(';'))
            { var k = p.IndexOf('='); if (k > 0) o[p.Substring(0, k)] = p.Substring(k + 1); }
            double D(string k) => double.Parse(o[k], CultureInfo.InvariantCulture);
            var win = new Extents3d(new Point3d(D("x0"), D("y0"), -1e9), new Point3d(D("x1"), D("y1"), 1e9));
            var doc = Application.DocumentManager.MdiActiveDocument;
            using var lk = doc.LockDocument();
            var db = doc.Database;
            using var w = new StreamWriter(o["out"], false, new UTF8Encoding(false));
            using var tr = db.TransactionManager.StartTransaction();
            var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
            foreach (ObjectId id in ms) Emit((Entity)tr.GetObject(id, OpenMode.ForRead), w, win, 0);
            tr.Commit();
        }

        /// <summary>测试用：块名匹配 THL_BLK 正则的前几个块，按块内坐标逐个图元写出（E 序号 类型 闭合 层 + 离散点）。</summary>
        [CommandMethod("THLBLK", CommandFlags.Session)]
        public static void Blocks()
        {
            var o = new Dictionary<string, string>();
            foreach (var p in (Environment.GetEnvironmentVariable("THL_BLK") ?? "").Split(';'))
            { var k = p.IndexOf('='); if (k > 0) o[p.Substring(0, k)] = p.Substring(k + 1); }
            var doc = Application.DocumentManager.MdiActiveDocument;
            using var lk = doc.LockDocument();
            var db = doc.Database;
            var rx = new Regex(o["pat"]);
            int max = int.Parse(o.TryGetValue("max", out var m) ? m : "1");
            using var w = new StreamWriter(o["out"], false, new UTF8Encoding(false));
            using var tr = db.TransactionManager.StartTransaction();
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            var got = new HashSet<string>();
            foreach (ObjectId id in bt)
            {
                var b = (BlockTableRecord)tr.GetObject(id, OpenMode.ForRead);
                if (b.IsLayout || !rx.IsMatch(b.Name)) continue;
                var fam = rx.Match(b.Name).Value;
                if (got.Count(g => g == fam) >= max) continue;
                got.Add(fam + "#" + got.Count); got.Add(fam);
                w.WriteLine("B " + b.Name.Replace(' ', '_'));
                int i = 0;
                foreach (ObjectId eid in b)
                {
                    var e = tr.GetObject(eid, OpenMode.ForRead) as Entity;
                    i++;
                    if (e is Curve c)
                    {
                        var pts = new List<Point3d>();
                        try
                        {
                            if (c is Line l) { pts.Add(l.StartPoint); pts.Add(l.EndPoint); }
                            else
                            {
                                double a = c.StartParam, bb = c.EndParam; int n = c is Polyline pl ? Math.Max(1, (pl.NumberOfVertices - (pl.Closed ? 0 : 1)) * 4) : 24;
                                for (int k = 0; k <= n; k++) pts.Add(c.GetPointAtParameter(a + (bb - a) * k / n));
                            }
                        }
                        catch { continue; }
                        w.WriteLine("E " + i + " " + e.GetType().Name + " " + (c.Closed ? 1 : 0) + " " + string.Join(" ", pts.Select(p => p.X.ToString("0.#", CultureInfo.InvariantCulture) + "," + p.Y.ToString("0.#", CultureInfo.InvariantCulture))));
                    }
                    else w.WriteLine("O " + i + " " + e.GetType().Name);
                }
            }
            tr.Commit();
        }

        static bool Hit(Extents3d a, Extents3d b) =>
            a.MinPoint.X <= b.MaxPoint.X && a.MaxPoint.X >= b.MinPoint.X && a.MinPoint.Y <= b.MaxPoint.Y && a.MaxPoint.Y >= b.MinPoint.Y;

        static void Emit(Entity e, StreamWriter w, Extents3d win, int depth)
        {
            Extents3d ext;
            try { ext = e.GeometricExtents; } catch { return; }
            if (!Hit(ext, win) || depth > 6) return;
            if (e is BlockReference br)
            {
                if (depth == 0) w.WriteLine("I " + br.Name.Replace(' ', '_') + " " + F(br.Position) + " " + br.Layer);
                var col = new DBObjectCollection();
                try { br.Explode(col); } catch { return; }
                foreach (DBObject x in col) { if (x is Entity xe) Emit(xe, w, win, depth + 1); x.Dispose(); }
                return;
            }
            if (e is Curve c)
            {
                if (c is Line l) { w.WriteLine("L " + e.Layer + " " + F(l.StartPoint) + " " + F(l.EndPoint)); return; }
                try
                {
                    double a = c.StartParam, b = c.EndParam; int n = c is Polyline pl ? Math.Max(1, (pl.NumberOfVertices - 1) * 4) : 24;
                    Point3d prev = c.GetPointAtParameter(a);
                    for (int i = 1; i <= n; i++) { var p = c.GetPointAtParameter(a + (b - a) * i / n); w.WriteLine("L " + e.Layer + " " + F(prev) + " " + F(p)); prev = p; }
                }
                catch { }
            }
            else if (e is DBText t) w.WriteLine("T " + e.Layer + " " + F(t.Position) + " " + t.TextString.Replace(' ', '_'));
            else if (e is MText m) w.WriteLine("T " + e.Layer + " " + F(m.Location) + " " + m.Text.Replace(' ', '_').Replace('\n', '_'));
        }

        static string F(Point3d p) => p.X.ToString("0.#", CultureInfo.InvariantCulture) + " " + p.Y.ToString("0.#", CultureInfo.InvariantCulture);
    }

    static class Ext
    {
        public static TR Let<T, TR>(this T v, Func<T, TR> f) => f(v);
    }
}
