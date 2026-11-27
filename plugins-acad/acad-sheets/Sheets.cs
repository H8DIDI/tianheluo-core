// 图纸（布局）操作：建布局（外部参照图框 + 图签属性块 + 视口 + 打印设置）、读写图签、编号、目录、批量打印 PDF。
// 约定：
//   · 图框 = 模板里的干净图框 DWG，复制到「图纸所在文件夹\图框\」后按相对路径外部参照（不炸开、不复制成块）；块名 THL图框_<模板名>。
//   · 图签 = 块 THL图签_<模板名>，每个字段一个属性，和图框用同一个插入变换叠在格子上；值就存在属性里（图纸是唯一数据源）。
//   · 布局纸面单位 = mm：图框缩放到正好铺满图幅，打印 1:1、窗口 = 图幅。视口比例 1:N 就是纸上真实比例。
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.PlottingServices;
using AcApp = Autodesk.AutoCAD.ApplicationServices.Core.Application;   // Core：界面版和 accoreconsole 都能用

namespace Thl.Acad.Sheets
{
    sealed class SheetRow
    {
        public ObjectId LayoutId;
        public string Layout = "";
        public int Tab;
        public string Template = "";       // 图签块对应的模板名（空 = 这个布局没有天河落图签）
        public ObjectId TitleRef;
        public double Scale;               // 主视口 1:N（0 = 没有视口）
        public Dictionary<string, string> Values = new Dictionary<string, string>();
        public bool Dirty;
        public string V(string k) => Values.TryGetValue(k, out var v) ? v : "";
    }

    sealed class PlotOpts
    {
        public string Device = "DWG To PDF.pc3";
        public string Ctb = "monochrome.ctb";
        public string Paper;               // null = 用模板的
    }

    static class Sheets
    {
        public const string TitlePrefix = "THL图签_";
        public const string FramePrefix = "THL图框_";
        public const string VpLayer = "THL-视口";
        public const string TitleLayer = "THL-图签";
        public const string FrameLayer = "THL-图框";
        public static readonly int[] StdScales = { 1, 2, 5, 10, 20, 25, 30, 50, 75, 100, 150, 200, 250, 300, 400, 500, 600, 750, 800, 1000, 1200, 1500, 2000, 2500, 3000, 5000, 10000 };

        public const string ZoneTag = "THL_ZONE";

        /// <summary>在布局上记一笔「这张是分区出图生成的，分区名 = …」（重复运行时据此更新而不是重复新建）。</summary>
        public static void TagLayout(Database db, string layoutName, string zone)
        {
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var ld = (DBDictionary)tr.GetObject(db.LayoutDictionaryId, OpenMode.ForRead);
                if (!ld.Contains(layoutName)) { tr.Commit(); return; }
                var lo = (Layout)tr.GetObject(ld.GetAt(layoutName), OpenMode.ForWrite);
                if (lo.ExtensionDictionary.IsNull) lo.CreateExtensionDictionary();
                var ext = (DBDictionary)tr.GetObject(lo.ExtensionDictionary, OpenMode.ForWrite);
                var xr = new Xrecord { Data = new ResultBuffer(new TypedValue((int)DxfCode.Text, zone)) };
                if (ext.Contains(ZoneTag)) ext.Remove(ZoneTag);
                ext.SetAt(ZoneTag, xr); tr.AddNewlyCreatedDBObject(xr, true);
                tr.Commit();
            }
        }

        /// <summary>分区出图生成过的布局（布局名 → 分区名）。</summary>
        public static Dictionary<string, string> TaggedLayouts(Database db)
        {
            var d = new Dictionary<string, string>();
            using (var tr = db.TransactionManager.StartOpenCloseTransaction())
            {
                var ld = (DBDictionary)tr.GetObject(db.LayoutDictionaryId, OpenMode.ForRead);
                foreach (var de in Entries(ld))
                {
                    var lo = (Layout)tr.GetObject(de.Value, OpenMode.ForRead);
                    if (lo.ExtensionDictionary.IsNull) continue;
                    var ext = (DBDictionary)tr.GetObject(lo.ExtensionDictionary, OpenMode.ForRead);
                    if (!ext.Contains(ZoneTag)) continue;
                    var xr = (Xrecord)tr.GetObject(ext.GetAt(ZoneTag), OpenMode.ForRead);
                    d[lo.LayoutName] = xr.Data?.AsArray().FirstOrDefault().Value as string ?? "";
                }
                tr.Commit();
            }
            return d;
        }

        /// <summary>DBDictionary 的枚举器给的是非泛型 DictionaryEntry，不能 Cast，逐个转。</summary>
        public static List<DBDictionaryEntry> Entries(DBDictionary d) { var l = new List<DBDictionaryEntry>(); foreach (DBDictionaryEntry e in d) l.Add(e); return l; }

        public static string TagOf(string key) => Regex.Replace(key ?? "", @"[\s<>/\\"":;?*|,=`]", "_").ToUpperInvariant();

        /// <summary>模型单位 → 纸面 mm 的倍数。</summary>
        public static double MmPerUnit(Database db)
        {
            switch (db.Insunits)
            {
                case UnitsValue.Meters: return 1000;
                case UnitsValue.Centimeters: return 10;
                case UnitsValue.Decimeters: return 100;
                case UnitsValue.Inches: return 25.4;
                case UnitsValue.Feet: return 304.8;
                default: return 1;
            }
        }

        // ------------------------------------------------------------------ 基础对象
        static ObjectId EnsureLayer(Database db, Transaction tr, string name, short color, bool plot)
        {
            var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead);
            if (lt.Has(name)) return lt[name];
            lt.UpgradeOpen();
            var l = new LayerTableRecord { Name = name, IsPlottable = plot, Color = Autodesk.AutoCAD.Colors.Color.FromColorIndex(Autodesk.AutoCAD.Colors.ColorMethod.ByAci, color) };
            var id = lt.Add(l); tr.AddNewlyCreatedDBObject(l, true);
            return id;
        }

        internal static ObjectId EnsureStyle(Database db, Transaction tr, FrameTemplate t)
        {
            var name = "THL_" + FrameTemplate.SafeName(t.Name);
            if (name.Length > 60) name = name.Substring(0, 60);
            var st = (TextStyleTable)tr.GetObject(db.TextStyleTableId, OpenMode.ForRead);
            if (st.Has(name)) return st[name];
            st.UpgradeOpen();
            var r = new TextStyleTableRecord { Name = name, XScale = t.StyleWidth > 0 ? t.StyleWidth : 1 };
            if (!string.IsNullOrEmpty(t.StyleFace)) r.Font = new Autodesk.AutoCAD.GraphicsInterface.FontDescriptor(t.StyleFace, false, false, 0, 0);
            else
            {
                r.FileName = string.IsNullOrEmpty(t.StyleFont) ? "txt.shx" : t.StyleFont;
                r.BigFontFileName = string.IsNullOrEmpty(t.StyleBigFont) ? "gbcbig.shx" : t.StyleBigFont;
            }
            var id = st.Add(r); tr.AddNewlyCreatedDBObject(r, true);
            return id;
        }

        static (TextHorizontalMode, TextVerticalMode, AttachmentPoint) Modes(string just)
        {
            var v = just.Length > 0 ? just[0] : 'B'; var h = just.Length > 1 ? just[1] : 'L';
            var hm = h == 'C' ? TextHorizontalMode.TextCenter : h == 'R' ? TextHorizontalMode.TextRight : TextHorizontalMode.TextLeft;
            var vm = v == 'T' ? TextVerticalMode.TextTop : v == 'M' ? TextVerticalMode.TextVerticalMid : TextVerticalMode.TextBase;
            AttachmentPoint ap;
            if (v == 'T') ap = h == 'C' ? AttachmentPoint.TopCenter : h == 'R' ? AttachmentPoint.TopRight : AttachmentPoint.TopLeft;
            else if (v == 'M') ap = h == 'C' ? AttachmentPoint.MiddleCenter : h == 'R' ? AttachmentPoint.MiddleRight : AttachmentPoint.MiddleLeft;
            else ap = h == 'C' ? AttachmentPoint.BottomCenter : h == 'R' ? AttachmentPoint.BottomRight : AttachmentPoint.BottomLeft;
            return (hm, vm, ap);
        }

        /// <summary>图签块定义（图框坐标）。已存在时按模板重建属性定义，并同步已插入的图签（按标记保留原值）。</summary>
        public static ObjectId EnsureTitleDef(Database db, Transaction tr, FrameTemplate t)
        {
            var name = TitlePrefix + FrameTemplate.SafeName(t.Name);
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            var style = EnsureStyle(db, tr, t);
            BlockTableRecord btr;
            bool existed = bt.Has(name);
            if (existed)
            {
                btr = (BlockTableRecord)tr.GetObject(bt[name], OpenMode.ForWrite);
                var have = btr.Cast<ObjectId>().Select(i => tr.GetObject(i, OpenMode.ForRead)).OfType<AttributeDefinition>().Select(a => a.Tag).ToList();
                var want = t.Fields.Select(f => TagOf(f.Key)).ToList();
                if (have.SequenceEqual(want)) return btr.ObjectId;
                foreach (ObjectId id in btr) tr.GetObject(id, OpenMode.ForWrite).Erase();
            }
            else
            {
                bt.UpgradeOpen();
                btr = new BlockTableRecord { Name = name, Origin = Point3d.Origin };
                bt.Add(btr); tr.AddNewlyCreatedDBObject(btr, true);
            }
            foreach (var f in t.Fields)
            {
                var (hm, vm, ap) = Modes(f.Just);
                var p = new Point3d(f.X, f.Y, 0);
                var ad = new AttributeDefinition();
                ad.SetDatabaseDefaults(db);
                ad.Tag = TagOf(f.Key);
                ad.Prompt = string.IsNullOrEmpty(f.Label) ? f.Key : f.Key + "（" + f.Label.Replace("\n", " ") + "）";
                ad.TextString = "";
                ad.TextStyleId = style;
                ad.Height = f.H;
                ad.WidthFactor = f.WidthFactor > 0 ? f.WidthFactor : 1;
                ad.Layer = "0";
                ad.ColorIndex = 0; // ByBlock
                ad.LockPositionInBlock = true;
                ad.Position = p;
                ad.HorizontalMode = hm; ad.VerticalMode = vm;
                if (hm != TextHorizontalMode.TextLeft || vm != TextVerticalMode.TextBase) ad.AlignmentPoint = p;
                btr.AppendEntity(ad); tr.AddNewlyCreatedDBObject(ad, true);
                if (f.Multi)
                {
                    ad.IsMTextAttributeDefinition = true;
                    var m = ad.MTextAttributeDefinition;
                    m.Location = p; m.Attachment = ap; m.TextHeight = f.H; m.Width = f.MaxW > 0 ? f.MaxW : 0; m.TextStyleId = style;
                    ad.MTextAttributeDefinition = m;
                    ad.UpdateMTextAttributeDefinition();
                }
                else if (hm != TextHorizontalMode.TextLeft || vm != TextVerticalMode.TextBase)
                {
                    using (new WorkingDbScope(db)) ad.AdjustAlignment(db);
                }
            }
            if (existed)
            {
                // 同步已插入的图签：按标记保留值
                foreach (ObjectId rid in btr.GetBlockReferenceIds(true, false))
                {
                    var br = (BlockReference)tr.GetObject(rid, OpenMode.ForWrite);
                    var vals = ReadAtts(tr, br);
                    foreach (ObjectId aid in br.AttributeCollection) tr.GetObject(aid, OpenMode.ForWrite).Erase();
                    AddAtts(db, tr, br, btr, vals, t);
                }
            }
            return btr.ObjectId;
        }

        public static Dictionary<string, string> ReadAtts(Transaction tr, BlockReference br)
        {
            var d = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (ObjectId aid in br.AttributeCollection)
            {
                if (!(tr.GetObject(aid, OpenMode.ForRead) is AttributeReference ar) || ar.IsErased) continue;
                d[ar.Tag] = ar.IsMTextAttribute ? ar.MTextAttribute.Text : ar.TextString;
            }
            return d;
        }

        static void AddAtts(Database db, Transaction tr, BlockReference br, BlockTableRecord def, Dictionary<string, string> vals, FrameTemplate t = null)
        {
            foreach (ObjectId id in def)
            {
                if (!(tr.GetObject(id, OpenMode.ForRead) is AttributeDefinition ad) || ad.Constant) continue;
                var ar = new AttributeReference();
                ar.SetAttributeFromBlock(ad, br.BlockTransform);
                br.AttributeCollection.AppendAttribute(ar);
                tr.AddNewlyCreatedDBObject(ar, true);
                SetAtt(db, ar, vals != null && vals.TryGetValue(ad.Tag, out var v) ? v : "");
                Fit(db, ar, br, t);
            }
        }

        public static void SetAttPublic(Database db, AttributeReference ar, string v) => SetAtt(db, ar, v);

        static void SetAtt(Database db, AttributeReference ar, string v)
        {
            v = v ?? "";
            if (ar.IsMTextAttribute)
            {
                var m = ar.MTextAttribute;
                m.Contents = v.Replace("\r", "").Replace("\n", "\\P");
                ar.MTextAttribute = m;
                ar.UpdateMTextAttribute();
            }
            else
            {
                ar.TextString = v.Replace("\r", "").Replace("\n", " ");
                if (ar.HorizontalMode != TextHorizontalMode.TextLeft || ar.VerticalMode != TextVerticalMode.TextBase)
                    using (new WorkingDbScope(db)) ar.AdjustAlignment(db);
            }
        }

        static List<FrameTemplate> _tplCache; static DateTime _tplAt;
        static List<FrameTemplate> Tpls()
        {
            if (_tplCache == null || (DateTime.Now - _tplAt).TotalSeconds > 5) { _tplCache = FrameTemplate.All(); _tplAt = DateTime.Now; }
            return _tplCache;
        }

        /// <summary>单行值比格子长：压扁宽度系数（最小 0.35）让它留在格子里。</summary>
        static void Fit(Database db, AttributeReference ar, BlockReference br, FrameTemplate t)
        {
            if (t == null || ar.IsMTextAttribute) return;
            var f = t.Fields.FirstOrDefault(x => TagOf(x.Key) == ar.Tag.ToUpperInvariant());
            if (f == null || f.CellX1 <= f.CellX0) return;
            char hj = f.Just.Length > 1 ? f.Just[1] : 'L';
            double room = hj == 'L' ? f.CellX1 - f.X : hj == 'R' ? f.X - f.CellX0 : 2 * Math.Min(f.CellX1 - f.X, f.X - f.CellX0);
            double allowed = room * Math.Abs(br.ScaleFactors.X) * 0.92;
            double wf0 = f.WidthFactor > 0 ? f.WidthFactor : 1;
            void Apply(double wf)
            {
                ar.WidthFactor = wf;
                if (ar.HorizontalMode != TextHorizontalMode.TextLeft || ar.VerticalMode != TextVerticalMode.TextBase)
                    using (new WorkingDbScope(db)) ar.AdjustAlignment(db);
            }
            if (Math.Abs(ar.WidthFactor - wf0) > 1e-9) Apply(wf0);
            if (string.IsNullOrEmpty(ar.TextString) || allowed <= 0) return;
            double w;
            try { var e = ar.GeometricExtents; w = e.MaxPoint.X - e.MinPoint.X; } catch { return; }
            if (w > allowed) Apply(Math.Max(0.35, wf0 * allowed / w));
        }

        /// <summary>把干净图框复制到图纸旁的「图框」文件夹并外部参照（已参照过就直接用）。</summary>
        public static ObjectId EnsureFrameXref(Database db, Transaction tr, FrameTemplate t, Action<string> log)
        {
            var name = FramePrefix + FrameTemplate.SafeName(t.Name);
            var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
            if (bt.Has(name)) return bt[name];
            if (!File.Exists(t.CleanDwg)) throw new InvalidOperationException("模板的图框文件不见了：" + t.CleanDwg + "\n请在「图框」页重新学习一次。");
            string path = t.CleanDwg, rel = null;
            var docPath = db.Filename;
            bool saved = !string.IsNullOrEmpty(docPath) && File.Exists(docPath) && !Path.GetFileName(docPath).StartsWith("Drawing", StringComparison.OrdinalIgnoreCase)
                         && !docPath.EndsWith(".dwt", StringComparison.OrdinalIgnoreCase);
            if (saved)
            {
                var dir = Path.Combine(Path.GetDirectoryName(docPath), "图框");
                Directory.CreateDirectory(dir);
                var dst = Path.Combine(dir, FrameTemplate.SafeName(t.Name) + ".dwg");
                if (!File.Exists(dst) || File.GetLastWriteTimeUtc(dst) < File.GetLastWriteTimeUtc(t.CleanDwg)) File.Copy(t.CleanDwg, dst, true);
                foreach (var img in Directory.GetFiles(Path.GetDirectoryName(t.CleanDwg)).Where(f => !f.EndsWith(".dwg", StringComparison.OrdinalIgnoreCase) && !f.EndsWith(".txt", StringComparison.OrdinalIgnoreCase) && !f.EndsWith(".bak", StringComparison.OrdinalIgnoreCase)))
                {
                    var d = Path.Combine(dir, Path.GetFileName(img));
                    if (!File.Exists(d)) File.Copy(img, d);
                }
                path = dst; rel = ".\\图框\\" + Path.GetFileName(dst);
            }
            else log?.Invoke("图纸还没保存：图框按绝对路径参照（保存后再建的图纸会改用相对路径 .\\图框\\）。");
            var id = db.OverlayXref(path, name);   // 覆盖型：别人参照这张图时不会把图框带过去
            if (rel != null) { var x = (BlockTableRecord)tr.GetObject(id, OpenMode.ForWrite); x.PathName = rel; }
            log?.Invoke("外部参照图框：" + (rel ?? path));
            return id;
        }

        // ------------------------------------------------------------------ 建图纸
        public sealed class NewSheet
        {
            public Point3d Min, Max;         // 模型范围
            public int Scale;                // 1:N（0 = 自动挑能放下的标准比例）
            public string Title = "";
            public string Number = "";
            public string LayoutName;        // null = 按规则
            // 分区出图
            public Zone Zone;
            public List<Zone> AllZones;
            public ZoneOpts ZoneOpts;
            public Dictionary<string, int> Layers;
            public List<(System.Text.RegularExpressions.Regex re, string text)> Legend;
        }

        /// <summary>避开图框自带的小框（出图章等）：挨着下边的抬高下边，挨着右边的收窄右边，取损失小的那种。</summary>
        public static (double x0, double y0, double x1, double y1) Avoid(FrameTemplate t, double k, (double x0, double y0, double x1, double y1) a)
        {
            foreach (var ko in t.Keepouts)
            {
                double kx0 = (ko[0] - t.X0) * k - 2, ky0 = (ko[1] - t.Y0) * k - 2, kx1 = (ko[2] - t.X0) * k + 2, ky1 = (ko[3] - t.Y0) * k + 2;
                if (kx1 <= a.x0 || kx0 >= a.x1 || ky1 <= a.y0 || ky0 >= a.y1) continue;
                double lossUp = (ky1 - a.y0) * (a.x1 - a.x0), lossRight = (a.x1 - kx0) * (a.y1 - a.y0), lossDown = (a.y1 - ky0) * (a.x1 - a.x0), lossLeft = (kx1 - a.x0) * (a.y1 - a.y0);
                var min = new[] { lossUp, lossRight, lossDown, lossLeft }.Min();
                if (min == lossUp) a.y0 = ky1; else if (min == lossRight) a.x1 = kx0; else if (min == lossDown) a.y1 = ky0; else a.x0 = kx1;
            }
            return a;
        }

        /// <summary>图框里放视口的可用区（纸面 mm），四周留 1mm。</summary>
        public static (double x0, double y0, double x1, double y1) Area(FrameTemplate t, double k) =>
            ((t.VX0 - t.X0) * k + 1, (t.VY0 - t.Y0) * k + 1, (t.VX1 - t.X0) * k - 1, (t.VY1 - t.Y0) * k - 1);

        public sealed class NewOpts
        {
            public string Paper;             // null = 模板默认
            public bool LockVp = true;
            public List<string> FreezeLayers = new List<string>();
            public string NameRule = "{图号} {图名}";
            public Dictionary<string, string> Defaults = new Dictionary<string, string>();
            public PlotOpts Plot = new PlotOpts();
        }

        public static string SafeLayoutName(string s)
        {
            s = Regex.Replace(s ?? "", @"[<>/\\"":;?*|,=`]", "_").Trim();
            if (s.Length > 250) s = s.Substring(0, 250);
            return s.Length == 0 ? "图纸" : s;
        }

        public static string ApplyRule(string rule, Func<string, string> get) =>
            Regex.Replace(rule ?? "", @"\{([^{}]+)\}", m => get(m.Groups[1].Value) ?? "").Trim();

        static string UniqueLayout(Transaction tr, Database db, string name)
        {
            var ld = (DBDictionary)tr.GetObject(db.LayoutDictionaryId, OpenMode.ForRead);
            var n = name; int i = 2;
            while (ld.Contains(n)) n = name + " (" + i++ + ")";
            return n;
        }

        /// <summary>按模型范围建一张图纸（新布局）。返回布局名。</summary>
        public static string Create(Document doc, FrameTemplate t, NewSheet s, NewOpts o, Action<string> log)
        {
            var db = doc.Database;
            var paper = o.Paper ?? t.Paper;
            double k = t.PaperScale(paper);
            var (pw, ph) = t.PaperMm(paper);
            // 视口可用区（纸面 mm），四周留 1mm
            var (ax0, ay0, ax1, ay1) = Area(t, k);
            (double x0, double y0, double x1, double y1) side = default;
            var zo = s.ZoneOpts;
            if (zo != null && (zo.Legend || zo.Index))
            {
                side = (ax1 - zo.SideW, ay0, ax1, ay1);
                ax1 -= zo.SideW + 4;
                side = Avoid(t, k, side);
            }
            (ax0, ay0, ax1, ay1) = Avoid(t, k, (ax0, ay0, ax1, ay1));
            double aw = ax1 - ax0, ah = ay1 - ay0;
            double mpu = MmPerUnit(db);
            double ww = Math.Abs(s.Max.X - s.Min.X), wh = Math.Abs(s.Max.Y - s.Min.Y);
            int n = s.Scale;
            if (n <= 0)
            {
                n = StdScales.FirstOrDefault(c => ww * mpu / c <= aw && wh * mpu / c <= ah);
                if (n == 0) n = (int)Math.Ceiling(Math.Max(ww * mpu / aw, wh * mpu / ah));
            }
            double vw = Math.Min(aw, ww * mpu / n), vh = Math.Min(ah, wh * mpu / n);
            if (ww * mpu / n > aw + 0.5 || wh * mpu / n > ah + 0.5)
                log?.Invoke($"「{s.Title}」按 1:{n} 放不下框选范围，视口按可用区大小裁剪（可以改比例或在布局里调视口）。");
            var values = new Dictionary<string, string>(o.Defaults ?? new Dictionary<string, string>());
            if (!string.IsNullOrEmpty(s.Title)) values["图名"] = s.Title;
            if (!string.IsNullOrEmpty(s.Number)) values["图号"] = s.Number;
            values["比例"] = "1:" + n;

            string layoutName;
            using (var tr = db.TransactionManager.StartTransaction())
            {
                layoutName = UniqueLayout(tr, db, SafeLayoutName(s.LayoutName ?? ApplyRule(o.NameRule, key => values.TryGetValue(key, out var v) ? v : "")));
                tr.Commit();
            }
            var lm = LayoutManager.Current;
            var prevCv = AcApp.GetSystemVariable("LAYOUTCREATEVIEWPORT");
            ObjectId loId;
            try
            {
                Plugin.Trace("建布局 " + layoutName);
                AcApp.SetSystemVariable("LAYOUTCREATEVIEWPORT", 0);
                loId = lm.CreateLayout(layoutName);
                Plugin.Trace("切到布局");
                lm.CurrentLayout = layoutName;   // 视口要在当前布局里才能打开
            }
            finally { try { AcApp.SetSystemVariable("LAYOUTCREATEVIEWPORT", prevCv); } catch { } }

            using (var tr = db.TransactionManager.StartTransaction())
            {
                var lo = (Layout)tr.GetObject(loId, OpenMode.ForWrite);
                Plugin.Trace("打印设置");
                ApplyPlot(lo, t, o.Plot, paper, log);
                var ps = (BlockTableRecord)tr.GetObject(lo.BlockTableRecordId, OpenMode.ForWrite);
                // 去掉自动生成的视口（保留第一个 = 图纸空间总视口）
                var vps = ps.Cast<ObjectId>().Where(i => i.ObjectClass.DxfName == "VIEWPORT").ToList();
                foreach (var id in vps.Skip(1)) tr.GetObject(id, OpenMode.ForWrite).Erase();

                Plugin.Trace("图框");
                PlaceFrame(db, tr, ps, t, k, values, log);

                EnsureLayer(db, tr, VpLayer, 8, false);
                var vp = new Viewport();
                vp.SetDatabaseDefaults(db);
                vp.CenterPoint = new Point3d((ax0 + ax1) / 2, (ay0 + ay1) / 2, 0);
                vp.Width = vw; vp.Height = vh;
                vp.Layer = VpLayer;
                ps.AppendEntity(vp); tr.AddNewlyCreatedDBObject(vp, true);
                vp.ViewDirection = Vector3d.ZAxis;
                vp.ViewTarget = Point3d.Origin;
                vp.TwistAngle = 0;
                vp.ViewCenter = new Point2d((s.Min.X + s.Max.X) / 2, (s.Min.Y + s.Max.Y) / 2);
                vp.CustomScale = mpu / n;
                Plugin.Trace("视口打开");
                vp.On = true;
                if (o.FreezeLayers != null && o.FreezeLayers.Count > 0)
                {
                    var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead);
                    var ids = o.FreezeLayers.Where(lt.Has).Select(x => lt[x]).ToList();
                    if (ids.Count > 0) vp.FreezeLayersInViewport(ids.GetEnumerator());
                }
                Plugin.Trace("裁剪/附加");
                Zones.ClipViewport(db, tr, ps, vp, s.Zone, vp.ViewCenter, vp.CustomScale);
                vp.Locked = o.LockVp;
                if (zo != null) Zones.Decorate(db, tr, ps, s, vp, side, EnsureStyle(db, tr, t), log);
                tr.Commit();
            }
            log?.Invoke($"建图纸「{layoutName}」：{paper} {pw:0}×{ph:0}，视口 {vw:0.#}×{vh:0.#} mm，1:{n}");
            return layoutName;
        }

        /// <summary>在图纸空间放图框（外部参照）和图签（属性块），同一个变换：图框外框左下角 → (0,0)，缩放 k。</summary>
        static ObjectId PlaceFrame(Database db, Transaction tr, BlockTableRecord ps, FrameTemplate t, double k, Dictionary<string, string> values, Action<string> log)
        {
            var pos = new Point3d(-t.X0 * k, -t.Y0 * k, 0);
            EnsureLayer(db, tr, FrameLayer, 7, true);
            EnsureLayer(db, tr, TitleLayer, 7, true);
            var xid = EnsureFrameXref(db, tr, t, log);
            var xr = new BlockReference(pos, xid) { ScaleFactors = new Scale3d(k), Layer = FrameLayer };
            ps.AppendEntity(xr); tr.AddNewlyCreatedDBObject(xr, true);

            var defId = EnsureTitleDef(db, tr, t);
            var def = (BlockTableRecord)tr.GetObject(defId, OpenMode.ForRead);
            var br = new BlockReference(pos, defId) { ScaleFactors = new Scale3d(k), Layer = TitleLayer };
            ps.AppendEntity(br); tr.AddNewlyCreatedDBObject(br, true);
            var byTag = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (var kv in values) byTag[TagOf(kv.Key)] = kv.Value;
            AddAtts(db, tr, br, def, byTag, t);
            return br.ObjectId;
        }

        /// <summary>给已有布局加图框 + 图签（不动它原来的视口）。</summary>
        public static void AddFrameToLayout(Document doc, ObjectId loId, FrameTemplate t, PlotOpts plot, string paper, Action<string> log)
        {
            var db = doc.Database;
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var lo = (Layout)tr.GetObject(loId, OpenMode.ForWrite);
                var ps = (BlockTableRecord)tr.GetObject(lo.BlockTableRecordId, OpenMode.ForWrite);
                var p = paper ?? t.Paper;
                PlaceFrame(db, tr, ps, t, t.PaperScale(p), new Dictionary<string, string>(), log);
                ApplyPlot(lo, t, plot, p, log);
                tr.Commit();
            }
        }

        // ------------------------------------------------------------------ 打印设置
        public static void ApplyPlot(Layout lo, FrameTemplate t, PlotOpts o, string paper, Action<string> log)
        {
            var (pw, ph) = t.PaperMm(paper);
            var psv = PlotSettingsValidator.Current;
            try
            {
                psv.SetPlotConfigurationName(lo, o.Device, null);
                psv.RefreshLists(lo);
                var medias = psv.GetCanonicalMediaNameList(lo).Cast<string>().ToList();
                string dim(double a, double b) => $"{a:0.00}_x_{b:0.00}";
                var media = medias.FirstOrDefault(m => m.Contains(dim(pw, ph)) && m.IndexOf("full_bleed", StringComparison.OrdinalIgnoreCase) >= 0)
                            ?? medias.FirstOrDefault(m => m.Contains(dim(pw, ph)));
                var rot = PlotRotation.Degrees000;
                if (media == null)
                {
                    media = medias.FirstOrDefault(m => m.Contains(dim(ph, pw)) && m.IndexOf("full_bleed", StringComparison.OrdinalIgnoreCase) >= 0)
                            ?? medias.FirstOrDefault(m => m.Contains(dim(ph, pw)));
                    if (media != null) rot = PlotRotation.Degrees090;
                }
                if (media == null) media = medias.FirstOrDefault(m => m.IndexOf("_" + (paper ?? t.Paper) + "_", StringComparison.OrdinalIgnoreCase) >= 0);
                if (media == null) { log?.Invoke($"打印机「{o.Device}」没有 {paper} 图幅，打印设置没改。"); return; }
                psv.SetPlotConfigurationName(lo, o.Device, media);
                psv.SetPlotPaperUnits(lo, PlotPaperUnit.Millimeters);
                psv.SetPlotWindowArea(lo, new Extents2d(0, 0, pw, ph));
                psv.SetPlotType(lo, Autodesk.AutoCAD.DatabaseServices.PlotType.Window);
                psv.SetUseStandardScale(lo, true);
                psv.SetStdScaleType(lo, StdScaleType.StdScale1To1);
                psv.SetPlotCentered(lo, true);
                psv.SetPlotRotation(lo, rot);
                if (!string.IsNullOrEmpty(o.Ctb))
                {
                    var ctbs = psv.GetPlotStyleSheetList().Cast<string>().ToList();
                    if (ctbs.Any(c => c.Equals(o.Ctb, StringComparison.OrdinalIgnoreCase))) psv.SetCurrentStyleSheet(lo, o.Ctb);
                    else log?.Invoke($"打印样式「{o.Ctb}」不在本机打印样式目录里，保留原样式。");
                }
                lo.PrintLineweights = true;
                lo.PlotTransparency = true;
                lo.ScaleLineweights = false;
            }
            catch (Exception ex) { log?.Invoke($"设置打印失败（{o.Device}）：{ex.Message}"); }
        }

        // ------------------------------------------------------------------ 读写
        public static List<SheetRow> Read(Database db, List<FrameTemplate> tpls)
        {
            var rows = new List<SheetRow>();
            double mpu = MmPerUnit(db);
            using (var tr = db.TransactionManager.StartOpenCloseTransaction())
            {
                var ld = (DBDictionary)tr.GetObject(db.LayoutDictionaryId, OpenMode.ForRead);
                foreach (DBDictionaryEntry de in ld)
                {
                    var lo = (Layout)tr.GetObject(de.Value, OpenMode.ForRead);
                    if (lo.ModelType) continue;
                    var r = new SheetRow { LayoutId = lo.ObjectId, Layout = lo.LayoutName, Tab = lo.TabOrder };
                    var ps = (BlockTableRecord)tr.GetObject(lo.BlockTableRecordId, OpenMode.ForRead);
                    Viewport main = null; double best = 0; bool first = true;
                    foreach (ObjectId id in ps)
                    {
                        var e = tr.GetObject(id, OpenMode.ForRead);
                        if (e is BlockReference br && r.TitleRef.IsNull)
                        {
                            var b = (BlockTableRecord)tr.GetObject(br.BlockTableRecord, OpenMode.ForRead);
                            if (b.Name.StartsWith(TitlePrefix, StringComparison.OrdinalIgnoreCase))
                            {
                                r.TitleRef = br.ObjectId;
                                r.Template = b.Name.Substring(TitlePrefix.Length);
                                var tpl = tpls.FirstOrDefault(x => FrameTemplate.SafeName(x.Name) == r.Template);
                                var atts = ReadAtts(tr, br);
                                foreach (var kv in atts)
                                {
                                    var f = tpl?.Fields.FirstOrDefault(x => TagOf(x.Key) == kv.Key.ToUpperInvariant());
                                    r.Values[f?.Key ?? kv.Key] = kv.Value;
                                }
                            }
                        }
                        else if (e is Viewport vp)
                        {
                            if (first && vp.Layer != VpLayer) { first = false; continue; }   // 图纸空间总视口
                            first = false;
                            var a = vp.Width * vp.Height;
                            if (a > best) { best = a; main = vp; }
                        }
                    }
                    if (main != null && main.CustomScale > 0) r.Scale = Math.Round(mpu / main.CustomScale, 2);
                    rows.Add(r);
                }
                tr.Commit();
            }
            return rows.OrderBy(r => r.Tab).ToList();
        }

        /// <summary>把列表写回图纸：图签属性、布局顺序（按列表顺序）、可选按规则改布局名。</summary>
        public static int Write(Document doc, List<SheetRow> rows, string renameRule, Action<string> log)
        {
            var db = doc.Database;
            int changed = 0;
            using (doc.LockDocument())
            using (var tr = db.TransactionManager.StartTransaction())
            {
                foreach (var r in rows.Where(x => x.Dirty && !x.TitleRef.IsNull))
                {
                    var br = (BlockReference)tr.GetObject(r.TitleRef, OpenMode.ForWrite);
                    var tpl = Tpls().FirstOrDefault(x => FrameTemplate.SafeName(x.Name) == r.Template);
                    foreach (ObjectId aid in br.AttributeCollection)
                    {
                        var ar = (AttributeReference)tr.GetObject(aid, OpenMode.ForWrite);
                        var key = r.Values.Keys.FirstOrDefault(k => TagOf(k) == ar.Tag.ToUpperInvariant());
                        if (key == null) continue;
                        var cur = ar.IsMTextAttribute ? ar.MTextAttribute.Text : ar.TextString;
                        if (cur != r.Values[key]) { SetAtt(db, ar, r.Values[key]); Fit(db, ar, br, tpl); }
                    }
                    changed++;
                }
                // 顺序：列表顺序 = 布局选项卡顺序
                int tab = 1;
                foreach (var r in rows)
                {
                    var lo = (Layout)tr.GetObject(r.LayoutId, OpenMode.ForRead);
                    if (lo.TabOrder != tab) { lo.UpgradeOpen(); lo.TabOrder = tab; }
                    r.Tab = tab++;
                }
                tr.Commit();
            }
            if (!string.IsNullOrWhiteSpace(renameRule))
            {
                var lm = LayoutManager.Current;
                using (doc.LockDocument())
                    foreach (var r in rows.Where(x => !x.TitleRef.IsNull))
                    {
                        var nn = SafeLayoutName(ApplyRule(renameRule, r.V));
                        if (nn == r.Layout || string.IsNullOrWhiteSpace(ApplyRule(renameRule, r.V).Replace(" ", ""))) continue;
                        using (var tr = db.TransactionManager.StartTransaction()) { nn = UniqueLayout(tr, db, nn); tr.Commit(); }
                        try { lm.RenameLayout(r.Layout, nn); r.Layout = nn; }
                        catch (Exception ex) { log?.Invoke($"布局「{r.Layout}」改名失败：{ex.Message}"); }
                    }
            }
            foreach (var r in rows) r.Dirty = false;
            return changed;
        }

        // ------------------------------------------------------------------ 编号
        /// <summary>编号规则：{n} {n:000} = 起始号起每张加步长；{i} {i:00} = 第几张；{total} {total:00} = 共几张；{图名} 等 = 字段值。</summary>
        public static string Number(string rule, int idx, int total, int start, int step, SheetRow r)
        {
            return Regex.Replace(rule ?? "", @"\{([^{}:]+)(?::(0+))?\}", m =>
            {
                var name = m.Groups[1].Value; var fmt = m.Groups[2].Success ? m.Groups[2].Value : "0";
                switch (name)
                {
                    case "n": return (start + idx * step).ToString(fmt);
                    case "i": return (idx + 1).ToString(fmt);
                    case "total": return total.ToString(fmt);
                    default: return r?.V(name) ?? "";
                }
            });
        }

        // ------------------------------------------------------------------ 目录
        /// <summary>生成（或重建）图纸目录布局：图框 + 图签 + 表格（太多行就分几栏）。</summary>
        public static string Toc(Document doc, FrameTemplate t, List<SheetRow> rows, NewOpts o, string tocName, string tocNumber, Action<string> log)
        {
            var db = doc.Database;
            var paper = o.Paper ?? t.Paper;
            double k = t.PaperScale(paper);
            string layout = SafeLayoutName(tocName);
            var lm = LayoutManager.Current;
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var ld = (DBDictionary)tr.GetObject(db.LayoutDictionaryId, OpenMode.ForRead);
                if (ld.Contains(layout)) { tr.Commit(); lm.DeleteLayout(layout); }
                else tr.Commit();
            }
            var prevCv = AcApp.GetSystemVariable("LAYOUTCREATEVIEWPORT");
            ObjectId loId;
            try
            {
                AcApp.SetSystemVariable("LAYOUTCREATEVIEWPORT", 0);
                loId = lm.CreateLayout(layout);
                lm.CurrentLayout = layout;
            }
            finally { try { AcApp.SetSystemVariable("LAYOUTCREATEVIEWPORT", prevCv); } catch { } }

            using (var tr = db.TransactionManager.StartTransaction())
            {
                var lo = (Layout)tr.GetObject(loId, OpenMode.ForWrite);
                var ldd = (DBDictionary)tr.GetObject(db.LayoutDictionaryId, OpenMode.ForRead);
                var others = Entries(ldd).Select(de => (Layout)tr.GetObject(de.Value, OpenMode.ForRead))
                                .Where(l => !l.ModelType && l.ObjectId != loId).OrderBy(l => l.TabOrder).ToList();
                int to = 2;
                foreach (var l in others) { if (l.TabOrder != to) { l.UpgradeOpen(); l.TabOrder = to; } to++; }
                lo.TabOrder = 1;
                ApplyPlot(lo, t, o.Plot, paper, log);
                var ps = (BlockTableRecord)tr.GetObject(lo.BlockTableRecordId, OpenMode.ForWrite);
                foreach (var id in ps.Cast<ObjectId>().Where(i => i.ObjectClass.DxfName == "VIEWPORT").Skip(1).ToList()) tr.GetObject(id, OpenMode.ForWrite).Erase();
                var vals = new Dictionary<string, string>(o.Defaults ?? new Dictionary<string, string>()) { ["图名"] = "图纸目录", ["图号"] = tocNumber ?? "", ["比例"] = "" };
                // 全套相同的字段沿用第一张图的值
                var firstRow = rows.FirstOrDefault(r => !r.TitleRef.IsNull);
                if (firstRow != null) foreach (var f in t.Fields.Where(f => f.Global)) if (!vals.ContainsKey(f.Key) || vals[f.Key] == "") vals[f.Key] = firstRow.V(f.Key);
                PlaceFrame(db, tr, ps, t, k, vals, log);

                double ax0 = (t.VX0 - t.X0) * k + 10, ay0 = (t.VY0 - t.Y0) * k + 10, ax1 = (t.VX1 - t.X0) * k - 10, ay1 = (t.VY1 - t.Y0) * k - 10;
                double rowH = 8, txtH = 3.5;
                string[] heads = { "序号", "图号", "图名", "比例", "版本", "图幅" };
                double[] rel = { 0.08, 0.30, 0.38, 0.08, 0.08, 0.08 };
                int perTable = Math.Max(3, (int)Math.Floor((ay1 - ay0) / rowH) - 2);
                var list = rows.Where(r => !r.TitleRef.IsNull).ToList();
                int nTables = Math.Max(1, (int)Math.Ceiling(list.Count / (double)perTable));
                double gap = 10, tw = Math.Min(420, (ax1 - ax0 - gap * (nTables - 1)) / nTables);
                for (int ti = 0; ti < nTables; ti++)
                {
                    var part = list.Skip(ti * perTable).Take(perTable).ToList();
                    var tb = new Table();
                    tb.SetDatabaseDefaults(db);
                    tb.TableStyle = db.Tablestyle;
                    tb.SetSize(part.Count + 2, heads.Length);
                    tb.Position = new Point3d(ax0 + ti * (tw + gap), ay1, 0);
                    tb.Layer = TitleLayer;
                    for (int c = 0; c < heads.Length; c++) tb.Columns[c].Width = tw * rel[c];
                    for (int r = 0; r < part.Count + 2; r++) tb.Rows[r].Height = rowH;
                    void Cell(int r, int c, string s)
                    {
                        tb.Cells[r, c].TextString = s ?? "";
                        tb.Cells[r, c].TextHeight = txtH;
                        tb.Cells[r, c].Alignment = c == 2 ? CellAlignment.MiddleLeft : CellAlignment.MiddleCenter;
                    }
                    tb.Cells[0, 0].TextString = nTables > 1 ? $"图纸目录（{ti + 1}/{nTables}）" : "图纸目录";
                    tb.Cells[0, 0].TextHeight = 5;
                    for (int c = 0; c < heads.Length; c++) Cell(1, c, heads[c]);
                    for (int i = 0; i < part.Count; i++)
                    {
                        var r = part[i];
                        Cell(i + 2, 0, (ti * perTable + i + 1).ToString());
                        Cell(i + 2, 1, r.V("图号"));
                        Cell(i + 2, 2, r.V("图名").Replace("\n", " "));
                        Cell(i + 2, 3, r.Scale > 0 ? "1:" + r.Scale : r.V("比例"));
                        Cell(i + 2, 4, r.V("版本"));
                        Cell(i + 2, 5, paper);
                    }
                    ps.AppendEntity(tb); tr.AddNewlyCreatedDBObject(tb, true);
                    tb.GenerateLayout();
                }
                tr.Commit();
                log?.Invoke($"目录「{layout}」：{list.Count} 张图，{nTables} 栏");
            }
            return layout;
        }

        // ------------------------------------------------------------------ 打印 PDF
        /// <summary>按列表顺序把布局打成一个多页 PDF（用各布局自己的打印设置；可统一换打印机/样式）。PDF 书签 = 布局名。</summary>
        public static string PlotPdf(Document doc, List<SheetRow> rows, string pdfPath, PlotOpts overrideOpts, Action<string> log)
        {
            if (rows.Count == 0) return "没有要打印的图纸。";
            if (PlotFactory.ProcessPlotState != ProcessPlotState.NotPlotting) return "AutoCAD 正在打印别的东西，请稍后再试。";
            var db = doc.Database;
            var bgp = AcApp.GetSystemVariable("BACKGROUNDPLOT");
            var lm = LayoutManager.Current;
            var prevLayout = lm.CurrentLayout;
            Directory.CreateDirectory(Path.GetDirectoryName(pdfPath));
            try
            {
                AcApp.SetSystemVariable("BACKGROUNDPLOT", 0);
                using (doc.LockDocument())
                using (var pe = PlotFactory.CreatePublishEngine())
                {
                    // accoreconsole（无界面批处理）里没有进度窗口
                    var console = System.Diagnostics.Process.GetCurrentProcess().ProcessName.IndexOf("accoreconsole", StringComparison.OrdinalIgnoreCase) >= 0;
                    PlotProgressDialog ppd = console ? null : new PlotProgressDialog(false, rows.Count, true);
                    try
                    {
                    if (ppd != null)
                    {
                    ppd.set_PlotMsgString(PlotMessageIndex.DialogTitle, "天河落 · 批量打印 PDF");
                    ppd.set_PlotMsgString(PlotMessageIndex.CancelJobButtonMessage, "取消");
                    ppd.set_PlotMsgString(PlotMessageIndex.CancelSheetButtonMessage, "取消本张");
                    ppd.set_PlotMsgString(PlotMessageIndex.SheetSetProgressCaption, "图纸");
                    ppd.set_PlotMsgString(PlotMessageIndex.SheetProgressCaption, "进度");
                    ppd.LowerPlotProgressRange = 0; ppd.UpperPlotProgressRange = 100; ppd.PlotProgressPos = 0;
                    ppd.OnBeginPlot(); ppd.IsVisible = true;
                    }
                    pe.BeginPlot(ppd, null);
                    for (int i = 0; i < rows.Count; i++)
                    {
                        var r = rows[i];
                        lm.CurrentLayout = r.Layout;
                        using (var tr = db.TransactionManager.StartTransaction())
                        {
                            var lo = (Layout)tr.GetObject(r.LayoutId, OpenMode.ForRead);
                            var ps = new PlotSettings(lo.ModelType);
                            ps.CopyFrom(lo);
                            var psv = PlotSettingsValidator.Current;
                            if (overrideOpts != null && !string.IsNullOrEmpty(overrideOpts.Device) && !string.Equals(lo.PlotConfigurationName, overrideOpts.Device, StringComparison.OrdinalIgnoreCase))
                            {
                                psv.SetPlotConfigurationName(ps, overrideOpts.Device, null);
                                psv.RefreshLists(ps);
                                var media = psv.GetCanonicalMediaNameList(ps).Cast<string>().FirstOrDefault(m => m == lo.CanonicalMediaName);
                                if (media != null) psv.SetPlotConfigurationName(ps, overrideOpts.Device, media);
                            }
                            if (overrideOpts != null && !string.IsNullOrEmpty(overrideOpts.Ctb))
                                try { psv.SetCurrentStyleSheet(ps, overrideOpts.Ctb); } catch { }
                            var pi = new PlotInfo { Layout = lo.ObjectId, OverrideSettings = ps };
                            var piv = new PlotInfoValidator { MediaMatchingPolicy = MatchingPolicy.MatchEnabled };
                            piv.Validate(pi);
                            if (i == 0) pe.BeginDocument(pi, doc.Name, null, 1, true, pdfPath);
                            ppd?.set_PlotMsgString(PlotMessageIndex.Status, $"第 {i + 1}/{rows.Count} 张：{r.Layout}");
                            ppd?.OnBeginSheet();
                            if (ppd != null) ppd.LowerSheetProgressRange = 0; if (ppd != null) ppd.UpperSheetProgressRange = 100; if (ppd != null) ppd.SheetProgressPos = 0;
                            var ppi = new PlotPageInfo();
                            pe.BeginPage(ppi, pi, i == rows.Count - 1, null);
                            pe.BeginGenerateGraphics(null);
                            if (ppd != null) ppd.SheetProgressPos = 50;
                            pe.EndGenerateGraphics(null);
                            pe.EndPage(null);
                            if (ppd != null) ppd.SheetProgressPos = 100;
                            ppd?.OnEndSheet();
                            if (ppd != null) ppd.PlotProgressPos = (int)(100.0 * (i + 1) / rows.Count);
                            tr.Commit();
                        }
                        log?.Invoke($"打印 {i + 1}/{rows.Count}：{r.Layout}");
                    }
                    pe.EndDocument(null);
                    if (ppd != null) ppd.PlotProgressPos = 100;
                    ppd?.OnEndPlot();
                    pe.EndPlot(null);
                    }
                    finally { ppd?.Dispose(); }
                }
            }
            finally
            {
                try { AcApp.SetSystemVariable("BACKGROUNDPLOT", bgp); } catch { }
                try { lm.CurrentLayout = prevLayout; } catch { }
            }
            return $"PDF 已生成（{rows.Count} 页）：{pdfPath}";
        }
    }
}
