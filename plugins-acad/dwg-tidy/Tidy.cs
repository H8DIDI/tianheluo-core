// 出图美化：把 Revit 导出（或别人给的）DWG 规范成能直接套图框出图的样子。只改「出图观感」，不动几何位置。
// 规则表：%AppData%\Tianheluo\AutoCAD\settings\dwg-tidy.txt（第一次运行自动生成，带说明，可手改）。
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Autodesk.AutoCAD.Colors;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace Thl.Acad.Tidy
{
    sealed class LayerRule
    {
        public Regex Pattern; public string Raw = "";
        public string Color;      // ACI 号 / R,G,B / 空 = 不改
        public double Lw = -1;    // mm，-1 = 不改
        public string Linetype;   // 空 = 不改
        public int Plot = -1;     // 1/0，-1 = 不改
    }

    sealed class TidyOptions
    {
        public bool Fonts = true;
        public string ShxFont = "gbenor.shx", BigFont = "gbcbig.shx";
        public bool Linetypes = true;            // 只在 LTSCALE=1 的图（Revit 导出）上做
        public double DashMin = 6, DashMax = 18;  // 线型一个周期打在纸上的长度（mm）范围
        public bool Duplicates = true;
        public bool DarkenPale = true;            // 浅色（屏幕上亮、纸上看不清）加深
        public double PaleL = 0.62, TargetL = 0.40;
        public bool ByLayer = false;              // 对象颜色/线型/线宽统一成随层
        public bool MaskMText = false;            // 多行文字加背景遮罩
        public bool KeepText = true;              // 图层按规则淡显时，图层上的文字/标注保持原色（只淡显线条）
        public bool Purge = true, Audit = true;
        public List<LayerRule> Rules = new List<LayerRule>();

        public static readonly string File = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo", "AutoCAD", "settings", "dwg-tidy.txt");

        const string Default =
"# 天河落 · 出图美化 规则表（改完保存，下次运行生效）\n" +
"# ---- 开关（1 开 / 0 关）\n" +
"fonts=1            # 缺字体的文字样式换成下面的字体（修「?」和乱码）\n" +
"shx=gbenor.shx\n" +
"bigfont=gbcbig.shx\n" +
"linetypes=1        # 虚线/点划线周期太长或太短的，按纸面长度重排（只对 LTSCALE=1 的图，即 Revit 导出）\n" +
"dashmin=6\n" +
"dashmax=18\n" +
"duplicates=1       # 删重叠的重复直线、零长度线\n" +
"darkenpale=1       # 屏幕上很亮、白纸上看不清的浅色图层加深（色相不变）\n" +
"bylayer=0          # 对象颜色/线型/线宽强制随层（设计院图里有意的灰色底图会变，默认不开）\n" +
"maskmtext=0        # 多行文字加背景遮罩，压在管线上的字更清楚\n" +
"keeptext=1         # 图层按下面的规则淡显时，图层上的文字、标高、标注保持原来的颜色（只淡显线条）\n" +
"purge=1\n" +
"audit=1\n" +
"# ---- 图层规则：图层名通配|颜色|线宽mm|线型|打印(1/0)   留空 = 不改；颜色写 ACI 号或 R,G,B\n" +
"# 越靠上越优先。默认把场地、地形、空间、导入的 CAD 底图淡显变细：\n" +
"layer=C-TOPO*|8|0.13||\n" +
"layer=L-SITE*|8|0.13||\n" +
"layer=Q-*|8|0.13||\n" +
"layer=*底图*|8|0.13||\n" +
"layer=*_dwg-*|8|0.13||\n" +
"layer=A-ANNO-DIMS*|7|0.18||\n" +
"# 例：主管线加粗  layer=P-PIPE-*||0.35||\n" +
"# 例：不打印的辅助线  layer=*辅助*||||0\n";

        public static string DefaultText => Default;

        public static TidyOptions Load(Action<string> log)
        {
            try
            {
                if (!System.IO.File.Exists(File)) { Directory.CreateDirectory(Path.GetDirectoryName(File)); System.IO.File.WriteAllText(File, Default, new UTF8Encoding(true)); }
                return Parse(System.IO.File.ReadAllText(File, Encoding.UTF8));
            }
            catch (Exception ex) { log?.Invoke("读规则表失败，用默认规则：" + ex.Message); return Parse(Default); }
        }

        public static TidyOptions Parse(string text)
        {
            var o = new TidyOptions();
            {
                foreach (var raw in text.Replace("\r", "").Split('\n'))
                {
                    var l = raw; var h = l.IndexOf('#'); if (h >= 0) l = l.Substring(0, h);
                    l = l.Trim(); if (l.Length == 0) continue;
                    var i = l.IndexOf('='); if (i <= 0) continue;
                    var k = l.Substring(0, i).Trim().ToLowerInvariant(); var v = l.Substring(i + 1).Trim();
                    bool B() => v == "1" || v.Equals("true", StringComparison.OrdinalIgnoreCase);
                    double D(double d) => double.TryParse(v, NumberStyles.Float, CultureInfo.InvariantCulture, out var x) ? x : d;
                    switch (k)
                    {
                        case "fonts": o.Fonts = B(); break;
                        case "shx": o.ShxFont = v; break;
                        case "bigfont": o.BigFont = v; break;
                        case "linetypes": o.Linetypes = B(); break;
                        case "dashmin": o.DashMin = D(o.DashMin); break;
                        case "dashmax": o.DashMax = D(o.DashMax); break;
                        case "duplicates": o.Duplicates = B(); break;
                        case "darkenpale": o.DarkenPale = B(); break;
                        case "bylayer": o.ByLayer = B(); break;
                        case "maskmtext": o.MaskMText = B(); break;
                        case "keeptext": o.KeepText = B(); break;
                        case "purge": o.Purge = B(); break;
                        case "audit": o.Audit = B(); break;
                        case "layer":
                            {
                                var p = v.Split('|');
                                var r = new LayerRule { Raw = p[0].Trim(), Pattern = Wild(p[0].Trim()) };
                                if (p.Length > 1 && p[1].Trim().Length > 0) r.Color = p[1].Trim();
                                if (p.Length > 2 && double.TryParse(p[2].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out var lw)) r.Lw = lw;
                                if (p.Length > 3 && p[3].Trim().Length > 0) r.Linetype = p[3].Trim();
                                if (p.Length > 4 && p[4].Trim().Length > 0) r.Plot = p[4].Trim() == "0" ? 0 : 1;
                                o.Rules.Add(r);
                                break;
                            }
                    }
                }
            }
            return o;
        }

        public void Override(IDictionary<string, string> a)
        {
            if (a == null) return;
            bool G(string k, bool d) => a.TryGetValue(k, out var v) ? v == "1" : d;
            Fonts = G("fonts", Fonts); Linetypes = G("linetypes", Linetypes); Duplicates = G("duplicates", Duplicates); DarkenPale = G("darkenpale", DarkenPale);
            ByLayer = G("bylayer", ByLayer); MaskMText = G("maskmtext", MaskMText); KeepText = G("keeptext", KeepText); Purge = G("purge", Purge); Audit = G("audit", Audit);
        }

        static Regex Wild(string w) => new Regex("^" + Regex.Escape(w).Replace("\\*", ".*").Replace("\\?", ".") + "$", RegexOptions.IgnoreCase);
    }

    static class Tidy
    {
        public static string Run(Database db, TidyOptions o, Action<string> log)
        {
            var rep = new List<string>();
            void R(string s) { rep.Add(s); log?.Invoke(s); }
            using (new WorkingDbScope(db))
            {
                if (o.Fonts) Fonts(db, o, R);
                if (o.Linetypes) Linetypes(db, o, R);
                Layers(db, o, R);
                Entities(db, o, R);
                if (o.Purge) Purge(db, R);
            }
            return string.Join("\n", rep);
        }

        static bool FontExists(Database db, string f)
        {
            if (string.IsNullOrWhiteSpace(f)) return true;
            try { return !string.IsNullOrEmpty(HostApplicationServices.Current.FindFile(f, db, FindFileHint.FontFile)); }
            catch { }
            try { return !string.IsNullOrEmpty(HostApplicationServices.Current.FindFile(Path.ChangeExtension(f, ".shx"), db, FindFileHint.FontFile)); }
            catch { return false; }
        }

        static void Fonts(Database db, TidyOptions o, Action<string> R)
        {
            int n = 0; var names = new List<string>();
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var st = (TextStyleTable)tr.GetObject(db.TextStyleTableId, OpenMode.ForRead);
                foreach (ObjectId id in st)
                {
                    var s = (TextStyleTableRecord)tr.GetObject(id, OpenMode.ForRead);
                    if (s.IsShapeFile || s.IsDependent) continue;
                    if (!string.IsNullOrEmpty(s.Font.TypeFace)) continue;   // TrueType：Windows 按字体名找，不处理
                    bool fontMissing = !FontExists(db, s.FileName), bigMissing = !string.IsNullOrEmpty(s.BigFontFileName) && !FontExists(db, s.BigFontFileName);
                    bool noBig = string.IsNullOrEmpty(s.BigFontFileName) && s.FileName != null && s.FileName.EndsWith(".shx", StringComparison.OrdinalIgnoreCase) == false && !s.FileName.Contains(".");
                    if (!fontMissing && !bigMissing) continue;
                    s.UpgradeOpen();
                    if (fontMissing) s.FileName = o.ShxFont;
                    if (bigMissing || fontMissing && string.IsNullOrEmpty(s.BigFontFileName)) s.BigFontFileName = o.BigFont;
                    n++; names.Add(s.Name);
                }
                tr.Commit();
            }
            if (n > 0) R($"字体：{n} 个文字样式缺字体，换成 {o.ShxFont} + {o.BigFont}（{string.Join("、", names.Take(8))}{(names.Count > 8 ? "…" : "")}）");
        }

        static void Linetypes(Database db, TidyOptions o, Action<string> R)
        {
            if (Math.Abs(db.Ltscale - 1) > 1e-9) { R($"线型：LTSCALE={db.Ltscale}（不是 Revit 导出的 1），保持原设计不动"); return; }
            int n = 0; var names = new List<string>();
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var lt = (LinetypeTable)tr.GetObject(db.LinetypeTableId, OpenMode.ForRead);
                foreach (ObjectId id in lt)
                {
                    var l = (LinetypeTableRecord)tr.GetObject(id, OpenMode.ForRead);
                    if (l.IsDependent || l.NumDashes < 2 || l.PatternLength <= 0) continue;
                    bool complex = false;
                    for (int i = 0; i < l.NumDashes; i++) { if (!l.ShapeStyleAt(i).IsNull) complex = true; else { try { if (!string.IsNullOrEmpty(l.TextAt(i))) complex = true; } catch { } } }
                    if (complex) continue;
                    double len = l.PatternLength, target = Math.Max(o.DashMin, Math.Min(o.DashMax, len));
                    if (Math.Abs(target - len) < 1e-6) continue;
                    double k = target / len;
                    l.UpgradeOpen();
                    for (int i = 0; i < l.NumDashes; i++) l.SetDashLengthAt(i, l.DashLengthAt(i) * k);
                    l.PatternLength = len * k;
                    n++; names.Add($"{l.Name} {len:0.#}→{target:0.#}");
                }
                tr.Commit();
            }
            db.Psltscale = true;
            if (n > 0) R($"线型：{n} 个线型周期按纸面重排（{string.Join("，", names.Take(6))}）；PSLTSCALE=1");
        }

        // ------------------------------------------------------------------ 图层：规则表 + 浅色加深
        static Color Parse(string c)
        {
            var p = c.Split(',');
            if (p.Length == 3) return Color.FromRgb(byte.Parse(p[0]), byte.Parse(p[1]), byte.Parse(p[2]));
            return Color.FromColorIndex(ColorMethod.ByAci, short.Parse(c));
        }

        /// <summary>按规则改过颜色的图层 → 原来的颜色（文字/标注保持原色用）。</summary>
        static readonly Dictionary<string, Color> _origColor = new Dictionary<string, Color>(StringComparer.OrdinalIgnoreCase);

        static void Layers(Database db, TidyOptions o, Action<string> R)
        {
            _origColor.Clear();
            int ruled = 0, darkened = 0; var ex = new List<string>();
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead);
                var ltt = (LinetypeTable)tr.GetObject(db.LinetypeTableId, OpenMode.ForRead);
                foreach (ObjectId id in lt)
                {
                    var l = (LayerTableRecord)tr.GetObject(id, OpenMode.ForRead);
                    if (l.IsDependent) continue;
                    var rule = o.Rules.FirstOrDefault(r => r.Pattern.IsMatch(l.Name));
                    if (rule != null)
                    {
                        l.UpgradeOpen();
                        try { if (rule.Color != null) { _origColor[l.Name] = l.Color; l.Color = Parse(rule.Color); } } catch { }
                        if (rule.Lw >= 0) l.LineWeight = Lw(rule.Lw);
                        if (rule.Linetype != null && ltt.Has(rule.Linetype)) l.LinetypeObjectId = ltt[rule.Linetype];
                        if (rule.Plot >= 0) l.IsPlottable = rule.Plot == 1;
                        ruled++;
                        if (ex.Count < 6) ex.Add(l.Name + "←" + rule.Raw);
                        continue;
                    }
                    if (o.DarkenPale)
                    {
                        var c = l.Color;
                        if (c.IsByAci && (c.ColorIndex == 7 || c.ColorIndex == 0 || c.ColorIndex == 256)) continue;   // 白/黑 = 打印黑
                        var v = c.ColorValue;
                        Hsl(v.R, v.G, v.B, out var h, out var s, out var lig);
                        if (s < 0.15 || lig <= o.PaleL) continue;   // 灰色系 / 本来就够深
                        Rgb(h, Math.Min(1, s * 1.05), o.TargetL, out var r, out var g, out var b);
                        l.UpgradeOpen();
                        l.Color = Color.FromRgb(r, g, b);
                        darkened++;
                    }
                }
                tr.Commit();
            }
            if (ruled > 0) R($"图层规则：{ruled} 个图层按规则表改了颜色/线宽/线型（{string.Join("，", ex)}{(ruled > ex.Count ? "…" : "")}）");
            if (darkened > 0) R($"浅色加深：{darkened} 个图层的颜色在白纸上看不清，按原色相加深");
        }

        static LineWeight Lw(double mm)
        {
            int v = (int)Math.Round(mm * 100);
            var all = Enum.GetValues(typeof(LineWeight)).Cast<LineWeight>().Where(x => (int)x >= 0).OrderBy(x => Math.Abs((int)x - v));
            return all.First();
        }

        static void Hsl(byte R, byte G, byte B, out double h, out double s, out double l)
        {
            double r = R / 255.0, g = G / 255.0, b = B / 255.0, max = Math.Max(r, Math.Max(g, b)), min = Math.Min(r, Math.Min(g, b));
            l = (max + min) / 2; h = 0; s = 0;
            if (max == min) return;
            double d = max - min;
            s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
            if (max == r) h = (g - b) / d + (g < b ? 6 : 0); else if (max == g) h = (b - r) / d + 2; else h = (r - g) / d + 4;
            h /= 6;
        }

        static void Rgb(double h, double s, double l, out byte R, out byte G, out byte B)
        {
            double Hue(double p, double q, double t) { if (t < 0) t += 1; if (t > 1) t -= 1; if (t < 1.0 / 6) return p + (q - p) * 6 * t; if (t < 0.5) return q; if (t < 2.0 / 3) return p + (q - p) * (2.0 / 3 - t) * 6; return p; }
            double q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
            R = (byte)Math.Round(Hue(p, q, h + 1.0 / 3) * 255); G = (byte)Math.Round(Hue(p, q, h) * 255); B = (byte)Math.Round(Hue(p, q, h - 1.0 / 3) * 255);
        }

        // ------------------------------------------------------------------ 对象：重复线、零长度、随层、遮罩
        static void Entities(Database db, TidyOptions o, Action<string> R)
        {
            int dup = 0, zero = 0, byl = 0, mask = 0, kept = 0;
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                foreach (ObjectId bid in bt)
                {
                    var b = (BlockTableRecord)tr.GetObject(bid, OpenMode.ForRead);
                    if (b.IsFromExternalReference || b.IsDependent) continue;
                    var seen = new HashSet<string>();
                    double tol = 0.01;
                    foreach (ObjectId id in b)
                    {
                        if (!(tr.GetObject(id, OpenMode.ForRead) is Entity e)) continue;
                        if (o.Duplicates && e is Line l)
                        {
                            if (l.Length < 1e-6) { l.UpgradeOpen(); l.Erase(); zero++; continue; }
                            var a = l.StartPoint; var z = l.EndPoint;
                            if (a.X > z.X + tol || (Math.Abs(a.X - z.X) <= tol && a.Y > z.Y)) { var t = a; a = z; z = t; }
                            string Q(double v) => Math.Round(v / tol).ToString(CultureInfo.InvariantCulture);
                            var key = l.Layer + "|" + l.ColorIndex + "|" + l.Linetype + "|" + Q(a.X) + "," + Q(a.Y) + "," + Q(a.Z) + "," + Q(z.X) + "," + Q(z.Y) + "," + Q(z.Z);
                            if (!seen.Add(key)) { l.UpgradeOpen(); l.Erase(); dup++; continue; }
                        }
                        if (o.ByLayer && !(e is BlockReference) && !(e is Viewport))
                        {
                            if (!e.Color.IsByLayer || e.Linetype != "ByLayer" || e.LineWeight != LineWeight.ByLayer)
                            {
                                e.UpgradeOpen();
                                if (!e.Color.IsByBlock) e.ColorIndex = 256;
                                if (e.Linetype != "ByBlock") e.Linetype = "ByLayer";
                                if (e.LineWeight != LineWeight.ByBlock) e.LineWeight = LineWeight.ByLayer;
                                byl++;
                            }
                        }
                        if (o.KeepText && _origColor.Count > 0 && (e is DBText || e is MText || e is Dimension || e is Leader || e is MLeader || e is AttributeDefinition)
                            && e.Color.IsByLayer && _origColor.TryGetValue(e.Layer, out var oc))
                        {
                            e.UpgradeOpen(); e.Color = oc; kept++;
                        }
                        if (o.MaskMText && e is MText mt && !mt.BackgroundFill)
                        {
                            mt.UpgradeOpen(); mt.BackgroundFill = true; mt.UseBackgroundColor = true; mt.BackgroundScaleFactor = 1.2; mask++;
                        }
                    }
                }
                tr.Commit();
            }
            if (dup + zero > 0) R($"清理：删重叠的重复直线 {dup} 条、零长度线 {zero} 条");
            if (byl > 0) R($"随层：{byl} 个对象的颜色/线型/线宽改成随层");
            if (mask > 0) R($"遮罩：{mask} 个多行文字加背景遮罩");
            if (kept > 0) R($"淡显图层上的文字/标注 {kept} 个保持原色（只淡显线条）");
        }

        static void Purge(Database db, Action<string> R)
        {
            int total = 0;
            for (int pass = 0; pass < 6; pass++)
            {
                var ids = new ObjectIdCollection();
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    void Add(ObjectId tableId) { var t = (SymbolTable)tr.GetObject(tableId, OpenMode.ForRead); foreach (ObjectId id in t) ids.Add(id); }
                    Add(db.BlockTableId); Add(db.LayerTableId); Add(db.TextStyleTableId); Add(db.LinetypeTableId); Add(db.DimStyleTableId); Add(db.RegAppTableId);
                    tr.Commit();
                }
                db.Purge(ids);
                if (ids.Count == 0) break;
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    foreach (ObjectId id in ids) { try { var o = tr.GetObject(id, OpenMode.ForWrite); o.Erase(); } catch { } }
                    tr.Commit();
                }
                total += ids.Count;
            }
            if (total > 0) R($"清理（PURGE）：{total} 项没用到的块/图层/样式/线型");
        }
    }
}
