// 图框模板：从一个真实图框 DWG「学习」出图签字段（位置/字高/对齐/图层/字体），并另存一份去掉示例值的干净图框，供外部参照。
//
// 学习规则（以常见深化图图框为样本，兼容中文图框）：
//   1. 外框 = 面积最大的闭合矩形多段线；内框 = 外框里面积 ≥ 80% 的次大矩形。
//   2. 标签 = 文字内容和词典匹配（DRAWING NO / 图号、TITLE / 图名、SCALE / 比例 ……）。
//   3. 格子 = 标签所在、由横竖线围成的最小格（多段线矩形的边和直线都算）。
//   4. 格子里不是标签的文字 = 示例值 → 记成字段（位置、字高、对齐、图层、字体），干净图框里删掉。
//   5. 修订表（表头含 DESCRIPTION / 修改内容）：表头上方每一行按列记成 修订1-版次 / 修订1-说明 …，并按行距往上补到表格顶。
//   6. 外框以外的文字（如「图号格式说明」）不进干净图框，记成提示。
//   7. 视口可用区 = 内框去掉右侧整列的图签栏。
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace Thl.Acad.Sheets
{
    sealed class FieldDef
    {
        public string Key = "";        // 属性标记（Tag）/ 列名，如 图号、修订1-说明
        public string Label = "";      // 图框上的原标签文字
        public string Sample = "";     // 图框里原来的示例值
        public double X, Y;            // 插入点（图框坐标）
        public double H = 2.5;         // 字高（图框单位）
        public string Just = "BL";     // BL/BC/BR/ML/MC/MR/TL/TC/TR
        public double WidthFactor = 1;
        public double MaxW;            // 多行文字宽度（0 = 单行）
        public bool Multi;             // 多行
        public string Layer = "";
        public bool Global;            // 全套图通常相同（项目名、设计人、日期……）
        public double CellX0, CellX1;
        public ObjectId SourceId;       // 分析现有图纸时：这个字段当前值所在的文字（不存盘）  // 所在格子的左右边（图框坐标；0 = 不限），太长的值自动压扁宽度
    }

    sealed class FrameTemplate
    {
        public string Name = "";
        public string SourceDwg = "";  // 学习时的原图框
        public string CleanDwg = "";   // 干净图框（模板目录里）
        public double X0, Y0, X1, Y1;  // 外框（图框坐标）
        public double VX0, VY0, VX1, VY1; // 视口可用区（图框坐标）
        public string Paper = "A1";    // A0~A4
        public string StyleFont = "", StyleBigFont = "", StyleFace = "";
        public double StyleWidth = 1;
        public string Note = "";       // 图框外的说明文字（如图号格式）
        public List<double[]> Keepouts = new List<double[]>();   // 视口区里图框自带的小框（如「FOR SHOP DRAWING」章），视口/图例要避开（图框坐标 x0,y0,x1,y1）
        public List<FieldDef> Fields = new List<FieldDef>();

        public override string ToString() => Name;
        public double W => X1 - X0;
        public double H => Y1 - Y0;
        public bool Portrait => H > W;

        public static readonly string Root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo", "AutoCAD", "sheet-frames");
        public string Dir => Path.Combine(Root, SafeName(Name));
        public string File => Path.Combine(Dir, "template.txt");

        public static string SafeName(string s)
        {
            foreach (var c in Path.GetInvalidFileNameChars()) s = s.Replace(c, '_');
            return s.Trim();
        }

        /// <summary>图幅（横向长边、短边，mm）。</summary>
        public static (double L, double S) PaperSize(string p)
        {
            switch ((p ?? "A1").ToUpperInvariant())
            {
                case "A0": return (1189, 841);
                case "A2": return (594, 420);
                case "A3": return (420, 297);
                case "A4": return (297, 210);
                default: return (841, 594);
            }
        }

        /// <summary>图框缩放到图纸（mm）的比例：外框正好铺满图幅。</summary>
        public double PaperScale(string paper = null)
        {
            var (l, s) = PaperSize(paper ?? Paper);
            double pw = Portrait ? s : l, ph = Portrait ? l : s;
            return Math.Min(pw / W, ph / H);
        }

        public (double w, double h) PaperMm(string paper = null)
        {
            var (l, s) = PaperSize(paper ?? Paper);
            return Portrait ? (s, l) : (l, s);
        }

        // ------------------------------------------------------------ 存取（简单文本，免依赖）
        static readonly CultureInfo IC = CultureInfo.InvariantCulture;
        static string F(double v) => v.ToString("R", IC);
        static string Esc(string s) => (s ?? "").Replace("\r", "").Replace("\\", "\\\\").Replace("\n", "\\n").Replace("|", "\\p");
        static string Unesc(string s)
        {
            var sb = new StringBuilder();
            for (int i = 0; i < s.Length; i++)
            {
                if (s[i] == '\\' && i + 1 < s.Length)
                {
                    var c = s[++i];
                    sb.Append(c == 'n' ? '\n' : c == 'p' ? '|' : c);
                }
                else sb.Append(s[i]);
            }
            return sb.ToString();
        }

        public void Save()
        {
            Directory.CreateDirectory(Dir);
            var sb = new StringBuilder();
            sb.AppendLine("# 天河落 图框模板（acad-sheets）。可以手改：字段行 = 标记|标签|示例|X|Y|字高|对齐|宽度系数|多行宽|多行|图层|全套相同");
            sb.AppendLine("name=" + Esc(Name));
            sb.AppendLine("source=" + Esc(SourceDwg));
            sb.AppendLine("clean=" + Esc(CleanDwg));
            sb.AppendLine($"frame={F(X0)},{F(Y0)},{F(X1)},{F(Y1)}");
            sb.AppendLine($"viewport={F(VX0)},{F(VY0)},{F(VX1)},{F(VY1)}");
            sb.AppendLine("paper=" + Paper);
            sb.AppendLine($"style={Esc(StyleFont)}|{Esc(StyleBigFont)}|{Esc(StyleFace)}|{F(StyleWidth)}");
            sb.AppendLine("note=" + Esc(Note));
            if (Keepouts.Count > 0) sb.AppendLine("keepout=" + string.Join(";", Keepouts.Select(k => string.Join(",", k.Select(F)))));
            foreach (var f in Fields)
                sb.AppendLine("field=" + string.Join("|", Esc(f.Key), Esc(f.Label), Esc(f.Sample), F(f.X), F(f.Y), F(f.H), f.Just, F(f.WidthFactor), F(f.MaxW), f.Multi ? "1" : "0", Esc(f.Layer), f.Global ? "1" : "0", F(f.CellX0), F(f.CellX1)));
            System.IO.File.WriteAllText(File, sb.ToString(), new UTF8Encoding(true));
        }

        public static FrameTemplate Load(string file)
        {
            var t = new FrameTemplate();
            foreach (var raw in System.IO.File.ReadAllLines(file, Encoding.UTF8))
            {
                if (raw.StartsWith("#") || !raw.Contains("=")) continue;
                var k = raw.Substring(0, raw.IndexOf('='));
                var v = raw.Substring(k.Length + 1);
                double[] Nums() => v.Split(',').Select(x => double.Parse(x, IC)).ToArray();
                switch (k)
                {
                    case "name": t.Name = Unesc(v); break;
                    case "source": t.SourceDwg = Unesc(v); break;
                    case "clean": t.CleanDwg = Unesc(v); break;
                    case "frame": { var n = Nums(); t.X0 = n[0]; t.Y0 = n[1]; t.X1 = n[2]; t.Y1 = n[3]; break; }
                    case "viewport": { var n = Nums(); t.VX0 = n[0]; t.VY0 = n[1]; t.VX1 = n[2]; t.VY1 = n[3]; break; }
                    case "paper": t.Paper = v.Trim(); break;
                    case "style":
                        {
                            var p = v.Split('|');
                            t.StyleFont = Unesc(p[0]); t.StyleBigFont = Unesc(p[1]); t.StyleFace = Unesc(p[2]);
                            t.StyleWidth = double.Parse(p[3], IC); break;
                        }
                    case "note": t.Note = Unesc(v); break;
                    case "keepout": t.Keepouts = v.Split(';').Where(x => x.Length > 0).Select(x => x.Split(',').Select(n => double.Parse(n, IC)).ToArray()).ToList(); break;
                    case "field":
                        {
                            var p = v.Split('|');
                            t.Fields.Add(new FieldDef
                            {
                                Key = Unesc(p[0]), Label = Unesc(p[1]), Sample = Unesc(p[2]),
                                X = double.Parse(p[3], IC), Y = double.Parse(p[4], IC), H = double.Parse(p[5], IC),
                                Just = p[6], WidthFactor = double.Parse(p[7], IC), MaxW = double.Parse(p[8], IC),
                                Multi = p[9] == "1", Layer = Unesc(p[10]), Global = p.Length > 11 && p[11] == "1",
                                CellX0 = p.Length > 13 ? double.Parse(p[12], IC) : 0, CellX1 = p.Length > 13 ? double.Parse(p[13], IC) : 0,
                            });
                            break;
                        }
                }
            }
            if (string.IsNullOrEmpty(t.Name)) t.Name = Path.GetFileName(Path.GetDirectoryName(file));
            // 方案从别的电脑导入时，干净图框的绝对路径不对：用模板目录里的同名文件
            if (!System.IO.File.Exists(t.CleanDwg))
            {
                var local = Path.Combine(Path.GetDirectoryName(file), SafeName(t.Name) + ".dwg");
                if (System.IO.File.Exists(local)) t.CleanDwg = local;
            }
            return t;
        }

        public static List<FrameTemplate> All()
        {
            var list = new List<FrameTemplate>();
            if (!Directory.Exists(Root)) return list;
            foreach (var d in Directory.GetDirectories(Root))
            {
                var f = Path.Combine(d, "template.txt");
                if (!System.IO.File.Exists(f)) continue;
                try { list.Add(Load(f)); } catch { }
            }
            return list.OrderBy(t => t.Name, StringComparer.CurrentCulture).ToList();
        }
    }

    /// <summary>从图框 DWG 学习模板。</summary>
    static class FrameLearner
    {
        // 标签词典：字段名 → 标签写法（比较时去掉空格、冒号、句点，大写）
        static readonly (string key, bool global, string[] pats)[] Dict =
        {
            ("项目名称", true,  new[] { "PROJECT", "工程名称", "项目名称", "项目" }),
            ("图名",     false, new[] { "TITLE", "DRAWINGTITLE", "图名", "图纸名称" }),
            ("图号",     false, new[] { "DRAWINGNO", "DWGNO", "图号", "图纸编号" }),
            ("版本",     true,  new[] { "REV", "REVISION", "版本", "版次", "版本号" }),
            ("设计",     true,  new[] { "DESIGNED", "DESIGNEDBY", "设计", "设计人" }),
            ("制图",     true,  new[] { "DRAWN", "DRAWNBY", "制图", "绘图", "制图人" }),
            ("比例",     false, new[] { "SCALE", "比例" }),
            ("日期",     true,  new[] { "STARTDATE", "DATE", "日期", "出图日期" }),
            ("工程编号", true,  new[] { "PROJECTNO", "JOBNO", "工程编号", "项目编号", "工程号" }),
            ("校对",     true,  new[] { "CHECKED", "CHECKEDBY", "校对", "校核" }),
            ("审核",     true,  new[] { "APPROVED", "APPROVEDBY", "审核" }),
            ("审定",     true,  new[] { "审定", "批准" }),
            ("阶段",     true,  new[] { "PROJECTPHASE", "PHASE", "设计阶段", "阶段" }),
            ("张号",     false, new[] { "SHEETNO", "SHEET", "张号", "页码" }),
            ("专业",     true,  new[] { "DISCIPLINE", "专业" }),
            ("项目负责人", true, new[] { "项目负责人", "工程负责人" }),
            ("专业负责人", true, new[] { "专业负责人" }),
        };
        static readonly string[] RevHeaderDesc = { "DESCRIPTION", "修改内容", "修改说明", "变更内容", "说明" };
        static readonly Dictionary<string, string> RevCols = new Dictionary<string, string>
        {
            ["REV"] = "版次", ["NO"] = "版次", ["版次"] = "版次", ["序号"] = "版次",
            ["DESCRIPTION"] = "说明", ["修改内容"] = "说明", ["修改说明"] = "说明", ["变更内容"] = "说明", ["说明"] = "说明",
            ["DATE"] = "日期", ["日期"] = "日期",
            ["BY"] = "修订人", ["修改人"] = "修订人", ["签名"] = "修订人",
        };

        public static string Norm(string s) => Regex.Replace((s ?? "").ToUpperInvariant(), @"[\s:：\.。,，]", "");

        sealed class Txt
        {
            public ObjectId Id; public string S = ""; public double X, Y, H, Wf = 1; public string Just = "BL";
            public Extents3d Ext; public bool MText; public double MW; public string Layer = ""; public ObjectId Style;
            public double CX => (Ext.MinPoint.X + Ext.MaxPoint.X) / 2; public double CY => (Ext.MinPoint.Y + Ext.MaxPoint.Y) / 2;
            public string Label; // 匹配到的字段名
        }
        struct Seg { public bool V; public double C, A, B; } // 竖线：x=C, y∈[A,B]；横线：y=C, x∈[A,B]

        public static FrameTemplate Learn(string frameDwg, string name, Action<string> log)
        {
            var t = new FrameTemplate { Name = name, SourceDwg = frameDwg };
            Directory.CreateDirectory(t.Dir);
            var clean = Path.Combine(t.Dir, FrameTemplate.SafeName(name) + ".dwg");

            using (var side = new SideDb(frameDwg, true))
            {
                var db = side.Db;
                var erase = new List<ObjectId>();
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    var space = PickSpace(db, tr);
                    Analyze(db, tr, space, t, erase, log);

                    // 删掉示例值和外框外的东西 → 干净图框
                    foreach (var id in erase.Distinct())
                    {
                        var e = tr.GetObject(id, OpenMode.ForWrite);
                        e.Erase();
                    }
                    tr.Commit();
                }
                // 图片（logo）引用的文件一并复制到模板目录，改成只写文件名
                CopyImages(db, frameDwg, t.Dir, log);
                db.SaveAs(clean, DwgVersion.AC1027);   // 2013 格式：2018~2026 都能参照
            }
            t.CleanDwg = clean;
            t.Paper = "A1";
            t.Save();
            log?.Invoke("干净图框：" + clean);
            return t;
        }

        /// <summary>分析一个空间（模型或布局）里的图框：外框、视口区、字段（示例值的文字 Id 记在 FieldDef.SourceId）。
        /// erase 收集要从干净图框里删掉的对象（示例值、外框以外的东西）。不改图。</summary>
        public static void Analyze(Database db, Transaction tr, BlockTableRecord space, FrameTemplate t, List<ObjectId> erase, Action<string> log)
        {
            var texts = new List<Txt>();
            var segs = new List<Seg>();
            var rects = new List<Extents3d>();
            foreach (ObjectId id in space)
            {
                var e = tr.GetObject(id, OpenMode.ForRead) as Entity;
                if (e == null) continue;
                if (e is DBText dt)
                {
                    var x = new Txt { Id = id, S = dt.TextString, H = dt.Height, Wf = dt.WidthFactor, Layer = dt.Layer, Style = dt.TextStyleId };
                    bool baseLeft = dt.HorizontalMode == TextHorizontalMode.TextLeft && dt.VerticalMode == TextVerticalMode.TextBase
                                    || dt.HorizontalMode == TextHorizontalMode.TextAlign || dt.HorizontalMode == TextHorizontalMode.TextFit;
                    var p = baseLeft ? dt.Position : dt.AlignmentPoint;
                    x.X = p.X; x.Y = p.Y; x.Just = baseLeft ? "BL" : JustOf(dt.HorizontalMode, dt.VerticalMode);
                    try { x.Ext = dt.GeometricExtents; } catch { x.Ext = new Extents3d(p, p); }
                    texts.Add(x);
                }
                else if (e is MText mt)
                {
                    var x = new Txt { Id = id, S = (mt.Text ?? "").Replace("\r\n", "\n").Replace("\r", "\n"), H = mt.TextHeight, Layer = mt.Layer, Style = mt.TextStyleId, MText = true, MW = mt.Width };
                    x.X = mt.Location.X; x.Y = mt.Location.Y; x.Just = JustOf(mt.Attachment);
                    try { x.Ext = mt.GeometricExtents; } catch { x.Ext = new Extents3d(mt.Location, mt.Location); }
                    texts.Add(x);
                }
                else if (e is Polyline pl)
                {
                    for (int i = 0; i < pl.NumberOfVertices - (pl.Closed ? 0 : 1); i++)
                    {
                        if (pl.GetSegmentType(i) != SegmentType.Line) continue;
                        var ls = pl.GetLineSegment2dAt(i);
                        AddSeg(segs, ls.StartPoint.X, ls.StartPoint.Y, ls.EndPoint.X, ls.EndPoint.Y);
                    }
                    if (pl.Closed && pl.NumberOfVertices >= 4) { try { rects.Add(pl.GeometricExtents); } catch { } }
                }
                else if (e is Line ln) AddSeg(segs, ln.StartPoint.X, ln.StartPoint.Y, ln.EndPoint.X, ln.EndPoint.Y);
            }

            // 1. 外框 / 内框
            Extents3d outer;
            if (rects.Count > 0) outer = rects.OrderByDescending(Area).First();
            else
            {
                outer = new Extents3d();
                foreach (var s in segs) { outer.AddPoint(s.V ? new Point3d(s.C, s.A, 0) : new Point3d(s.A, s.C, 0)); outer.AddPoint(s.V ? new Point3d(s.C, s.B, 0) : new Point3d(s.B, s.C, 0)); }
            }
            t.X0 = outer.MinPoint.X; t.Y0 = outer.MinPoint.Y; t.X1 = outer.MaxPoint.X; t.Y1 = outer.MaxPoint.Y;
            var inner = rects.Where(r => Area(r) < Area(outer) * 0.999 && Area(r) > Area(outer) * 0.8 && Inside(r, outer)).OrderByDescending(Area).FirstOrDefault();
            if (Area(inner) <= 0) inner = outer;
            log?.Invoke($"外框 {t.W:0.##} × {t.H:0.##}（图框单位），内框 {inner.MaxPoint.X - inner.MinPoint.X:0.##} × {inner.MaxPoint.Y - inner.MinPoint.Y:0.##}");

            // 7. 视口可用区：内框里去掉右侧整列图签栏（该 x 上竖线累计覆盖 ≥ 90% 内框高度）
            double ih = inner.MaxPoint.Y - inner.MinPoint.Y, iw = inner.MaxPoint.X - inner.MinPoint.X;
            double titleX = inner.MaxPoint.X;
            foreach (var g in segs.Where(s => s.V && s.C > inner.MinPoint.X + iw * 0.5 && s.C < inner.MaxPoint.X - iw * 0.01).GroupBy(s => Math.Round(s.C / (iw * 0.002))))
            {
                var cov = Cover(g.Select(s => (Math.Max(s.A, inner.MinPoint.Y), Math.Min(s.B, inner.MaxPoint.Y))));
                if (cov >= ih * 0.9) titleX = Math.Min(titleX, g.Average(s => s.C));
            }
            t.VX0 = inner.MinPoint.X; t.VY0 = inner.MinPoint.Y; t.VX1 = titleX; t.VY1 = inner.MaxPoint.Y;
            {
                double vArea = (t.VX1 - t.VX0) * (t.VY1 - t.VY0);
                var ko = rects.Where(r => Area(r) < vArea * 0.1 && Area(r) > 0 && r.MinPoint.X >= t.VX0 - 1e-6 && r.MaxPoint.X <= t.VX1 + 1e-6 && r.MinPoint.Y >= t.VY0 - 1e-6 && r.MaxPoint.Y <= t.VY1 + 1e-6).ToList();
                // 相互挨着的合成一个
                var merged = new List<double[]>();
                foreach (var r in ko)
                {
                    var b = new[] { r.MinPoint.X, r.MinPoint.Y, r.MaxPoint.X, r.MaxPoint.Y };
                    double tol = t.W * 0.002;
                    var hit = merged.FirstOrDefault(m => b[0] <= m[2] + tol && b[2] >= m[0] - tol && b[1] <= m[3] + tol && b[3] >= m[1] - tol);
                    if (hit == null) merged.Add(b);
                    else { hit[0] = Math.Min(hit[0], b[0]); hit[1] = Math.Min(hit[1], b[1]); hit[2] = Math.Max(hit[2], b[2]); hit[3] = Math.Max(hit[3], b[3]); }
                }
                t.Keepouts = merged;
                if (merged.Count > 0) log?.Invoke($"视口区里有 {merged.Count} 个图框自带的小框（如出图章），出图时避开");
            }

            // 6. 外框以外的东西：不进干净图框；文字记成提示
            var outside = new List<string>();
            foreach (ObjectId id in space)
            {
                var e = tr.GetObject(id, OpenMode.ForRead) as Entity;
                if (e == null) continue;
                Extents3d ex; try { ex = e.GeometricExtents; } catch { continue; }
                double tolO = t.W * 0.01;
                if (ex.MinPoint.X < t.X0 - tolO || ex.MaxPoint.X > t.X1 + tolO || ex.MinPoint.Y < t.Y0 - tolO || ex.MaxPoint.Y > t.Y1 + tolO)
                {
                    erase.Add(id);
                    var tx = texts.FirstOrDefault(x => x.Id == id);
                    if (tx != null && tx.S.Trim().Length > 0) outside.Add(tx.S.Trim());
                }
            }
            t.Note = string.Join("\n", outside);
            texts = texts.Where(x => !erase.Contains(x.Id)).ToList();

            // 2. 标签
            foreach (var x in texts)
            {
                var n = Norm(x.S);
                if (n.Length == 0 || n.Length > 16) continue;
                foreach (var d in Dict)
                    if (d.pats.Any(p => n == p || n == p + "NO" && false)) { x.Label = d.key; break; }
            }

            // 5. 修订表
            var revHeader = texts.FirstOrDefault(x => RevHeaderDesc.Contains(Norm(x.S)) && x.Label == null);
            var used = new HashSet<ObjectId>();
            var fields = new List<FieldDef>();
            if (revHeader != null)
            {
                var row = texts.Where(x => Math.Abs(x.CY - revHeader.CY) < revHeader.H * 0.6 && RevCols.ContainsKey(Norm(x.S))).OrderBy(x => x.CX).ToList();
                // 修订表的范围：包住表头的最小闭合矩形（行之间常有分隔线，不能用单个格子）
                var boxRect = rects.Where(r => r.MinPoint.X <= revHeader.CX && r.MaxPoint.X >= revHeader.CX && r.MinPoint.Y <= revHeader.CY && r.MaxPoint.Y >= revHeader.CY
                                               && r.MaxPoint.Y > revHeader.CY + revHeader.H * 2 && Area(r) < Area(outer) * 0.2).OrderBy(Area).FirstOrDefault();
                (double x0, double y0, double x1, double y1) box = Area(boxRect) > 0 ? (boxRect.MinPoint.X, boxRect.MinPoint.Y, boxRect.MaxPoint.X, boxRect.MaxPoint.Y) : CellOf(segs, revHeader.CX, revHeader.CY, t);
                if (Area(boxRect) <= 0)
                {
                    // 没有闭合矩形：往上找第一条跨过整排表头的横线
                    double xa = row.Min(h => h.Ext.MinPoint.X), xb = row.Max(h => h.Ext.MaxPoint.X), top = t.Y1;
                    foreach (var s in segs) if (!s.V && s.A <= xa && s.B >= xb && s.C > revHeader.CY + revHeader.H * 2 && s.C < top) top = s.C;
                    box = (box.x0, box.y0, box.x1, top);
                }
                var vals = texts.Where(x => x.CY > revHeader.CY + revHeader.H * 0.5 && x.CX > box.x0 && x.CX < box.x1 && x.CY < box.y1 && !row.Contains(x)).ToList();
                var rowsY = Cluster(vals.Select(v => v.CY), revHeader.H * 0.4);   // 由下往上
                double pitch = rowsY.Count >= 2 ? (rowsY.Last() - rowsY.First()) / (rowsY.Count - 1) : revHeader.H * 2.3;
                foreach (var h in row) { used.Add(h.Id); h.Label = "#rev"; }
                int nRows = rowsY.Count;
                double baseY = rowsY.Count > 0 ? rowsY[0] : revHeader.CY + pitch;
                while (baseY + nRows * pitch < box.y1 - pitch * 0.4 && nRows < 8) nRows++;
                for (int r = 0; r < nRows; r++)
                {
                    double cy = baseY + r * pitch;
                    foreach (var h in row)
                    {
                        var col = RevCols[Norm(h.S)];
                        var sample = vals.Where(v => Math.Abs(v.CY - cy) < pitch * 0.4).OrderBy(v => Math.Abs(v.CX - h.CX)).FirstOrDefault(v => Nearest(row, v) == h);
                        var f = new FieldDef { Key = $"修订{r + 1}-{col}", Label = h.S.Trim() + $"（第{r + 1}行）", Global = true };
                        if (sample != null) { FromText(f, sample); used.Add(sample.Id); erase.Add(sample.Id); }
                        else { var tpl = vals.FirstOrDefault(v => Nearest(row, v) == h) ?? h; FromText(f, tpl); f.Sample = ""; f.Y = tpl.Y + (cy - tpl.CY); f.Multi = false; }
                        fields.Add(f);
                    }
                }
                log?.Invoke($"修订表：{row.Count} 列 × {nRows} 行（示例 {rowsY.Count} 行）");
            }

            // 3/4. 普通标签：同一格里的其它文字 = 示例值
            var labels = texts.Where(x => x.Label != null && x.Label != "#rev").ToList();
            var cells = labels.ToDictionary(l => l, l => CellOf(segs, l.CX, l.CY, t));
            var assigned = new Dictionary<Txt, List<Txt>>();
            foreach (var v in texts.Where(x => x.Label == null && !used.Contains(x.Id)))
            {
                Txt best = null; double bestScore = double.MaxValue;
                foreach (var l in labels)
                {
                    var c = cells[l];
                    if (v.CX < c.x0 || v.CX > c.x1 || v.CY < c.y0 || v.CY > c.y1) continue;
                    if (l.CY < v.CY - l.H * 0.6) continue;          // 值在标签同一行或下面
                    if (l.Ext.MinPoint.X > v.CX) continue;          // 值在标签右边或下面
                    double sc = Math.Abs(v.CX - l.CX) + 3 * Math.Abs(v.CY - l.CY);
                    if (sc < bestScore) { bestScore = sc; best = l; }
                }
                if (best == null) continue;
                if (!assigned.TryGetValue(best, out var lst)) assigned[best] = lst = new List<Txt>();
                lst.Add(v);
            }
            // 值在标签右边的相邻格子里（中文图签常见：「日期 | 05 | Oct | 2026」）：同一行、格子左边线 = 左邻格子右边线，顺着链往左找到标签
            var cellOfTxt = new Dictionary<Txt, (double x0, double y0, double x1, double y1)>();
            (double x0, double y0, double x1, double y1) CellT(Txt x) { if (!cellOfTxt.TryGetValue(x, out var c)) cellOfTxt[x] = c = CellOf(segs, x.CX, x.CY, t); return c; }
            var owner = new Dictionary<Txt, Txt>();
            foreach (var kv in assigned) foreach (var v in kv.Value) owner[v] = kv.Key;
            foreach (var l in labels) owner[l] = l;
            var loose = texts.Where(x => x.Label == null && !used.Contains(x.Id) && !owner.ContainsKey(x)).OrderBy(x => x.CX).ToList();
            foreach (var v in loose)
            {
                var cv = CellT(v);
                if (cv.x1 - cv.x0 > t.W * 0.25) continue;   // 大格子里的说明文字不算
                double tol = Math.Max(v.H * 0.3, t.W * 1e-4);
                var left = owner.Keys.Where(o => Math.Abs(o.CY - v.CY) < Math.Max(o.H, v.H) * 0.6 && o.CX < v.CX && Math.Abs(CellT(o).x1 - cv.x0) < tol)
                                     .OrderByDescending(o => o.CX).FirstOrDefault();
                if (left == null) continue;
                var lab = owner[left];
                if (!assigned.TryGetValue(lab, out var lst2)) assigned[lab] = lst2 = new List<Txt>();
                lst2.Add(v); owner[v] = lab;
            }
            var keyCount = new Dictionary<string, int>();
            foreach (var l in labels.OrderByDescending(l => l.CY).ThenBy(l => l.CX))
            {
                keyCount.TryGetValue(l.Label, out var k); keyCount[l.Label] = ++k;
                var key = k == 1 ? l.Label : l.Label + k;
                var g = Dict.First(d => d.key == l.Label).global;
                var vs = assigned.TryGetValue(l, out var lst) ? lst.OrderByDescending(v => Math.Round(v.CY / l.H)).ThenBy(v => v.CX).ToList() : new List<Txt>();
                if (vs.Count == 0)
                {
                    // 没有示例值：放在标签下面一行（格子够高）或右边
                    var c = cells[l];
                    var f = new FieldDef { Key = key, Label = l.S.Trim(), Global = g, H = l.H * 1.25, Layer = l.Layer, WidthFactor = l.Wf, Just = "ML" };
                    if (c.y1 - c.y0 > l.H * 3.2) { f.X = l.Ext.MinPoint.X + l.H * 0.5; f.Y = l.Ext.MinPoint.Y - l.H * 1.4; }
                    else { f.X = l.Ext.MaxPoint.X + l.H; f.Y = l.CY; }
                    fields.Add(f);
                    continue;
                }
                for (int i = 0; i < vs.Count; i++)
                {
                    var f = new FieldDef { Key = i == 0 ? key : key + "-" + (i + 1), Label = l.S.Trim(), Global = g };
                    FromText(f, vs[i]);
                    fields.Add(f);
                    erase.Add(vs[i].Id);
                }
            }
            t.Fields = fields.OrderBy(f => f.Key.StartsWith("修订") ? 1 : 0).ToList();
                    foreach (var f in t.Fields)
                    {
                        if (f.Multi) continue;
                        char hj = f.Just.Length > 1 ? f.Just[1] : 'L', vj = f.Just.Length > 0 ? f.Just[0] : 'B';
                        double px = f.X + (hj == 'L' ? f.H * 0.3 : hj == 'R' ? -f.H * 0.3 : 0), py = f.Y + (vj == 'T' ? -f.H * 0.5 : vj == 'M' ? 0 : f.H * 0.5);
                        var c = CellOf(segs, px, py, t);
                        if (c.x1 - c.x0 < t.W * 0.5) { f.CellX0 = c.x0; f.CellX1 = c.x1; }
                    }

            // 字体：取第一个示例值用的文字样式
            var styleId = texts.FirstOrDefault(x => erase.Contains(x.Id))?.Style ?? db.Textstyle;
            if (!styleId.IsNull && tr.GetObject(styleId, OpenMode.ForRead) is TextStyleTableRecord ts)
            {
                t.StyleFont = ts.FileName ?? ""; t.StyleBigFont = string.IsNullOrEmpty(ts.BigFontFileName) && (ts.FileName ?? "").EndsWith(".shx", StringComparison.OrdinalIgnoreCase) ? "gbcbig.shx" : ts.BigFontFileName ?? ""; t.StyleFace = ts.Font.TypeFace ?? "";
                t.StyleWidth = ts.XScale > 0 ? ts.XScale : 1;
            }
            log?.Invoke($"字段 {t.Fields.Count} 个：" + string.Join("、", t.Fields.Where(f => !f.Key.StartsWith("修订")).Select(f => f.Key + (f.Sample.Length > 0 ? "=" + Short(f.Sample) : ""))));

        }

        static string Short(string s) { s = s.Replace("\n", " "); return s.Length > 18 ? s.Substring(0, 18) + "…" : s; }

        /// <summary>图框在模型空间，或（模型空间几乎为空时）在文字最多的布局里。</summary>
        static BlockTableRecord PickSpace(Database db, Transaction tr)
        {
            var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
            int Count(BlockTableRecord b) => b.Cast<ObjectId>().Count(i => i.ObjectClass.DxfName == "TEXT" || i.ObjectClass.DxfName == "MTEXT");
            var best = ms; int n = Count(ms);
            var ld = (DBDictionary)tr.GetObject(db.LayoutDictionaryId, OpenMode.ForRead);
            foreach (DBDictionaryEntry de in ld)
            {
                var lo = (Layout)tr.GetObject(de.Value, OpenMode.ForRead);
                if (lo.ModelType) continue;
                var b = (BlockTableRecord)tr.GetObject(lo.BlockTableRecordId, OpenMode.ForRead);
                var c = Count(b);
                if (c > n * 2 && c > 10) { best = b; n = c; }
            }
            return best;
        }

        static void CopyImages(Database db, string srcDwg, string dir, Action<string> log)
        {
            try
            {
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    var dictId = RasterImageDef.GetImageDictionary(db);
                    if (dictId.IsNull) { tr.Commit(); return; }
                    var dict = (DBDictionary)tr.GetObject(dictId, OpenMode.ForRead);
                    foreach (DBDictionaryEntry de in dict)
                    {
                        if (!(tr.GetObject(de.Value, OpenMode.ForWrite) is RasterImageDef def)) continue;
                        var src = def.SourceFileName ?? "";
                        var cands = new[] { src, Path.Combine(Path.GetDirectoryName(srcDwg) ?? "", src), Path.Combine(Path.GetDirectoryName(srcDwg) ?? "", Path.GetFileName(src)) };
                        var found = cands.FirstOrDefault(c => c.Length > 0 && System.IO.File.Exists(c));
                        if (found == null)
                        {
                            // 找不到文件的图片 AutoCAD 会在图上画出它的路径（还暴露别人电脑的路径）：从干净图框里删掉
                            int n = 0;
                            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                            foreach (ObjectId bid in bt)
                            {
                                var b = (BlockTableRecord)tr.GetObject(bid, OpenMode.ForRead);
                                foreach (ObjectId eid in b)
                                {
                                    if (eid.ObjectClass.DxfName != "IMAGE") continue;
                                    var img = (RasterImage)tr.GetObject(eid, OpenMode.ForRead);
                                    if (img.ImageDefId == def.ObjectId) { img.UpgradeOpen(); img.Erase(); n++; }
                                }
                            }
                            log?.Invoke($"图框里有 {n} 处图片找不到文件，已从干净图框里删掉（不然图上会显示文件路径）。需要 logo 时把图片放到模板目录：" + dir);
                            continue;
                        }
                        var dst = Path.Combine(dir, Path.GetFileName(found));
                        System.IO.File.Copy(found, dst, true);
                        def.SourceFileName = Path.GetFileName(found);
                    }
                    tr.Commit();
                }
            }
            catch (Exception ex) { log?.Invoke("复制图框图片失败：" + ex.Message); }
        }

        static void FromText(FieldDef f, Txt x)
        {
            f.SourceId = x.Id; f.Sample = x.S; f.X = x.X; f.Y = x.Y; f.H = x.H; f.Just = x.Just; f.WidthFactor = x.Wf; f.Layer = x.Layer;
            f.Multi = x.MText && x.S.Contains("\n"); f.MaxW = x.MText ? Math.Max(x.MW, x.Ext.MaxPoint.X - x.Ext.MinPoint.X) : 0;
        }

        static Txt Nearest(List<Txt> row, Txt v) => row.OrderBy(h => Math.Abs(h.CX - v.CX)).First();

        static List<double> Cluster(IEnumerable<double> ys, double tol)
        {
            var res = new List<double>();
            foreach (var y in ys.OrderBy(y => y))
                if (res.Count == 0 || y - res[res.Count - 1] > tol) res.Add(y);
            return res;
        }

        static double Cover(IEnumerable<(double a, double b)> iv)
        {
            double total = 0, ca = double.NaN, cb = double.NaN;
            foreach (var (a, b) in iv.Where(i => i.b > i.a).OrderBy(i => i.a))
            {
                if (double.IsNaN(ca) || a > cb) { if (!double.IsNaN(ca)) total += cb - ca; ca = a; cb = b; }
                else cb = Math.Max(cb, b);
            }
            if (!double.IsNaN(ca)) total += cb - ca;
            return total;
        }

        static void AddSeg(List<Seg> segs, double x1, double y1, double x2, double y2)
        {
            double len = Math.Sqrt((x2 - x1) * (x2 - x1) + (y2 - y1) * (y2 - y1));
            if (len < 1e-9) return;
            if (Math.Abs(x2 - x1) < len * 1e-3) segs.Add(new Seg { V = true, C = (x1 + x2) / 2, A = Math.Min(y1, y2), B = Math.Max(y1, y2) });
            else if (Math.Abs(y2 - y1) < len * 1e-3) segs.Add(new Seg { V = false, C = (y1 + y2) / 2, A = Math.Min(x1, x2), B = Math.Max(x1, x2) });
        }

        /// <summary>点所在的最小格子（四个方向最近的、跨过该点的线）。</summary>
        static (double x0, double y0, double x1, double y1) CellOf(List<Seg> segs, double x, double y, FrameTemplate t)
        {
            double x0 = t.X0, x1 = t.X1, y0 = t.Y0, y1 = t.Y1, eps = 1e-6;
            foreach (var s in segs)
            {
                if (s.V && s.A - eps <= y && s.B + eps >= y)
                {
                    if (s.C < x && s.C > x0) x0 = s.C;
                    if (s.C > x && s.C < x1) x1 = s.C;
                }
                else if (!s.V && s.A - eps <= x && s.B + eps >= x)
                {
                    if (s.C < y && s.C > y0) y0 = s.C;
                    if (s.C > y && s.C < y1) y1 = s.C;
                }
            }
            return (x0, y0, x1, y1);
        }

        static double Area(Extents3d e) => Math.Max(0, e.MaxPoint.X - e.MinPoint.X) * Math.Max(0, e.MaxPoint.Y - e.MinPoint.Y);
        static bool Inside(Extents3d a, Extents3d b) => a.MinPoint.X >= b.MinPoint.X - 1e-6 && a.MinPoint.Y >= b.MinPoint.Y - 1e-6 && a.MaxPoint.X <= b.MaxPoint.X + 1e-6 && a.MaxPoint.Y <= b.MaxPoint.Y + 1e-6;

        public static string JustOf(TextHorizontalMode h, TextVerticalMode v)
        {
            char hc = h == TextHorizontalMode.TextCenter || h == TextHorizontalMode.TextMid ? 'C' : h == TextHorizontalMode.TextRight ? 'R' : 'L';
            char vc = h == TextHorizontalMode.TextMid ? 'M' : v == TextVerticalMode.TextTop ? 'T' : v == TextVerticalMode.TextVerticalMid ? 'M' : 'B';
            return "" + vc + hc;
        }

        public static string JustOf(AttachmentPoint a)
        {
            switch (a)
            {
                case AttachmentPoint.TopCenter: return "TC";
                case AttachmentPoint.TopRight: return "TR";
                case AttachmentPoint.MiddleLeft: return "ML";
                case AttachmentPoint.MiddleCenter: return "MC";
                case AttachmentPoint.MiddleRight: return "MR";
                case AttachmentPoint.BottomLeft: return "BL";
                case AttachmentPoint.BottomCenter: return "BC";
                case AttachmentPoint.BottomRight: return "BR";
                default: return "TL";
            }
        }
    }
}
