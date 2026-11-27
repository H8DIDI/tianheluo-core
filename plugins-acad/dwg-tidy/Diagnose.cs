// 出图体检：只读，统计 Revit 导出 DWG 里影响出图美观的问题（给规则定方向，也给用户看「改前」状况）。
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace Thl.Acad.Tidy
{
    static class Diagnose
    {
        public static string Run(Database db)
        {
            var sb = new StringBuilder();
            void W(string s) => sb.AppendLine(s);
            W($"INSUNITS={db.Insunits} LTSCALE={db.Ltscale} PSLTSCALE={db.Psltscale} CELTSCALE={db.Celtscale} LWDISPLAY={db.LineWeightDisplay} 当前样式={db.Textstyle}");
            using (var tr = db.TransactionManager.StartOpenCloseTransaction())
            {
                var types = new Dictionary<string, int>();
                var layerUse = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                int colorOverride = 0, ltOverride = 0, lwOverride = 0, ltsNot1 = 0, total = 0, zeroLen = 0, dupLines = 0;
                var colorVals = new Dictionary<string, int>();
                var textH = new Dictionary<double, int>(); var textStyles = new Dictionary<string, int>(); var wf = new Dictionary<double, int>();
                int qmark = 0; var hatchPat = new Dictionary<string, int>();
                var lineKeys = new HashSet<string>();
                var depthMax = 0;
                var bt = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
                var visited = new Dictionary<ObjectId, int>();
                void Walk(BlockTableRecord b, int depth, string inheritLayer)
                {
                    depthMax = Math.Max(depthMax, depth);
                    foreach (ObjectId id in b)
                    {
                        if (!(tr.GetObject(id, OpenMode.ForRead) is Entity e)) continue;
                        total++;
                        var tn = e.GetType().Name; types.TryGetValue(tn, out var c); types[tn] = c + 1;
                        var lay = e.Layer == "0" && inheritLayer != null ? inheritLayer : e.Layer;
                        layerUse.TryGetValue(lay, out var lu); layerUse[lay] = lu + 1;
                        if (!(e is BlockReference))
                        {
                            if (!e.Color.IsByLayer && !e.Color.IsByBlock) { colorOverride++; var k = e.Color.ColorMethod + ":" + (e.Color.IsByAci ? e.Color.ColorIndex.ToString() : e.Color.ColorValue.R + "," + e.Color.ColorValue.G + "," + e.Color.ColorValue.B); colorVals.TryGetValue(k, out var cv); colorVals[k] = cv + 1; }
                            if (e.Linetype != "ByLayer" && e.Linetype != "ByBlock") ltOverride++;
                            if (e.LineWeight != LineWeight.ByLayer && e.LineWeight != LineWeight.ByBlock) lwOverride++;
                            if (Math.Abs(e.LinetypeScale - 1) > 1e-9) ltsNot1++;
                        }
                        if (e is Line l)
                        {
                            if (l.Length < 1e-6) zeroLen++;
                            if (depth == 0)
                            {
                                var a = l.StartPoint; var z = l.EndPoint;
                                if (a.X > z.X || (a.X == z.X && a.Y > z.Y)) { var t = a; a = z; z = t; }
                                var key = $"{a.X:0.0},{a.Y:0.0},{z.X:0.0},{z.Y:0.0}";
                                if (!lineKeys.Add(key)) dupLines++;
                            }
                        }
                        else if (e is DBText dt) Text(dt.Height, dt.TextStyleName, dt.WidthFactor, dt.TextString);
                        else if (e is MText mt) Text(mt.TextHeight, mt.TextStyleName, 1, mt.Text);
                        else if (e is Hatch h) { var k = h.PatternName + (h.PatternName == "SOLID" ? "(实体)" : ""); hatchPat.TryGetValue(k, out var hc); hatchPat[k] = hc + 1; }
                        else if (e is BlockReference br && depth < 8)
                        {
                            var bb = (BlockTableRecord)tr.GetObject(br.BlockTableRecord, OpenMode.ForRead);
                            if (bb.IsFromExternalReference) continue;
                            if (!visited.ContainsKey(bb.ObjectId)) { visited[bb.ObjectId] = 1; Walk(bb, depth + 1, br.Layer); }
                        }
                    }
                }
                void Text(double h, string st, double w, string s)
                {
                    var hh = Math.Round(h, 1); textH.TryGetValue(hh, out var a); textH[hh] = a + 1;
                    textStyles.TryGetValue(st, out var b); textStyles[st] = b + 1;
                    var ww = Math.Round(w, 2); wf.TryGetValue(ww, out var c); wf[ww] = c + 1;
                    if (s != null && s.Contains("?")) qmark++;
                }
                Walk(bt, 0, null);
                W($"对象 {total}（含块内，每种块只展开一次）；类型：" + string.Join("，", types.OrderByDescending(x => x.Value).Take(14).Select(x => x.Key + " " + x.Value)));
                W($"不随层：颜色 {colorOverride}，线型 {ltOverride}，线宽 {lwOverride}；线型比例≠1：{ltsNot1}；零长度线 {zeroLen}；模型空间重复直线 {dupLines}；块嵌套最深 {depthMax}");
                W("覆盖颜色前 10：" + string.Join("，", colorVals.OrderByDescending(x => x.Value).Take(10).Select(x => x.Key + "×" + x.Value)));
                W("文字高度：" + string.Join("，", textH.OrderByDescending(x => x.Value).Take(12).Select(x => x.Key + "×" + x.Value)));
                W("文字样式：" + string.Join("，", textStyles.OrderByDescending(x => x.Value).Take(10).Select(x => x.Key + "×" + x.Value)) + $"；含「?」的文字 {qmark}");
                W("宽度因子：" + string.Join("，", wf.OrderByDescending(x => x.Value).Take(6).Select(x => x.Key + "×" + x.Value)));
                W("填充：" + string.Join("，", hatchPat.OrderByDescending(x => x.Value).Take(10).Select(x => x.Key + "×" + x.Value)));

                // 图层
                var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead);
                int nl = 0, nDep = 0;
                var rows = new List<string>();
                foreach (ObjectId lid in lt)
                {
                    var l = (LayerTableRecord)tr.GetObject(lid, OpenMode.ForRead);
                    if (l.IsDependent) { nDep++; continue; }
                    nl++;
                    layerUse.TryGetValue(l.Name, out var u);
                    var ltn = ((LinetypeTableRecord)tr.GetObject(l.LinetypeObjectId, OpenMode.ForRead)).Name;
                    rows.Add($"  {u,7}  {l.Name}  色={l.Color.ColorIndex}{(l.Color.IsByAci ? "" : "(" + l.Color.ColorValue.R + "," + l.Color.ColorValue.G + "," + l.Color.ColorValue.B + ")")} 线型={ltn} 线宽={l.LineWeight} {(l.IsOff ? "关" : "")}{(l.IsFrozen ? "冻" : "")}{(l.IsPlottable ? "" : "不打印")} 说明={l.Description}");
                }
                W($"图层 {nl} 个（外部参照图层 {nDep}）；按使用量：");
                foreach (var r in rows.OrderByDescending(r => int.Parse(r.Substring(0, 9).Trim()))) W(r);
                // 文字样式 / 字体
                var st = (TextStyleTable)tr.GetObject(db.TextStyleTableId, OpenMode.ForRead);
                W("文字样式定义：");
                foreach (ObjectId sid in st)
                {
                    var s = (TextStyleTableRecord)tr.GetObject(sid, OpenMode.ForRead);
                    if (s.IsShapeFile || s.IsDependent) continue;
                    string Find(string f) { if (string.IsNullOrEmpty(f)) return ""; try { return HostApplicationServices.Current.FindFile(f, db, FindFileHint.FontFile) != null ? "" : "（缺）"; } catch { return "（缺）"; } }
                    W($"  {s.Name}: 字体={s.FileName}{Find(s.FileName)} 大字体={s.BigFontFileName}{Find(s.BigFontFileName)} TTF={s.Font.TypeFace} 高={s.TextSize} 宽={s.XScale}");
                }
                var ds = (DimStyleTable)tr.GetObject(db.DimStyleTableId, OpenMode.ForRead);
                W("标注样式：" + string.Join("，", ds.Cast<ObjectId>().Select(i => (DimStyleTableRecord)tr.GetObject(i, OpenMode.ForRead)).Where(d => !d.IsDependent).Select(d => $"{d.Name}(字高{d.Dimtxt} 全局{d.Dimscale} 箭头{d.Dimasz})")));
                var ltt = (LinetypeTable)tr.GetObject(db.LinetypeTableId, OpenMode.ForRead);
                W("线型：" + string.Join("，", ltt.Cast<ObjectId>().Select(i => (LinetypeTableRecord)tr.GetObject(i, OpenMode.ForRead)).Where(x => !x.IsDependent).Select(x => $"{x.Name}({x.PatternLength:0.##})")));
                tr.Commit();
            }
            return sb.ToString();
        }
    }
}
