// Excel 批量改图签：一个 DWG 里的多张图 → 导出表格（一行一张图）→ 在 Excel 里改 → 导入：先预览「旧 → 新」，确认后备份原文件再写入。
// 能认的图签：
//   ① 天河落图签块（THL图签_*）的属性；
//   ② 布局里画出来的图签（普通文字，不是属性）：用学习图框的同一套规则找格子和格子里的值；
//   ③ 模型空间里的图框块（设计院常见）：属性标记像 图号 / 图名 / DRAWING_NO / TITLE 的块，每个块一行。
// 文件是 UTF-8（带 BOM）的 CSV，Excel 直接打开、另存时选「CSV UTF-8」。
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace Thl.Acad.Sheets
{
    sealed class TitleRow
    {
        public string Kind = "";     // 天河落图签 / 布局文字 / 模型图框块
        public string Locator = "";  // 布局名，或模型块句柄
        public string Layout = "";   // 布局名（模型块为空）
        public Dictionary<string, string> Values = new Dictionary<string, string>();
        public Dictionary<string, ObjectId> Targets = new Dictionary<string, ObjectId>();   // 字段 → 文字/属性对象（null = 空格子，导入时新建文字）
        public Dictionary<string, FieldDef> Empty = new Dictionary<string, FieldDef>();      // 空格子的位置
        public ObjectId Space;
    }

    static class ExcelSync
    {
        static readonly Regex TitleTags = new Regex(@"^(图号|图名|图纸名称|图纸编号|DRAWING_?NO|DWG_?NO|DRAWINGNO|TITLE|TUHAO|TUMING|TH|TM)$", RegexOptions.IgnoreCase);

        public static List<TitleRow> Scan(Document doc, List<FrameTemplate> tpls, Action<string> log)
        {
            var db = doc.Database;
            var rows = new List<TitleRow>();
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var ld = (DBDictionary)tr.GetObject(db.LayoutDictionaryId, OpenMode.ForRead);
                var layouts = Sheets.Entries(ld).Select(de => (Layout)tr.GetObject(de.Value, OpenMode.ForRead)).Where(l => !l.ModelType).OrderBy(l => l.TabOrder).ToList();
                foreach (var lo in layouts)
                {
                    var ps = (BlockTableRecord)tr.GetObject(lo.BlockTableRecordId, OpenMode.ForRead);
                    TitleRow row = null;
                    // ① 天河落图签
                    foreach (ObjectId id in ps)
                    {
                        if (id.ObjectClass.DxfName != "INSERT") continue;
                        var br = (BlockReference)tr.GetObject(id, OpenMode.ForRead);
                        var b = (BlockTableRecord)tr.GetObject(br.BlockTableRecord, OpenMode.ForRead);
                        if (!b.Name.StartsWith(Sheets.TitlePrefix, StringComparison.OrdinalIgnoreCase)) continue;
                        var tpl = tpls.FirstOrDefault(x => FrameTemplate.SafeName(x.Name) == b.Name.Substring(Sheets.TitlePrefix.Length));
                        row = new TitleRow { Kind = "天河落图签", Locator = lo.LayoutName, Layout = lo.LayoutName, Space = ps.ObjectId };
                        foreach (ObjectId aid in br.AttributeCollection)
                        {
                            var ar = (AttributeReference)tr.GetObject(aid, OpenMode.ForRead);
                            var key = tpl?.Fields.FirstOrDefault(f => Sheets.TagOf(f.Key) == ar.Tag.ToUpperInvariant())?.Key ?? ar.Tag;
                            row.Values[key] = ar.IsMTextAttribute ? ar.MTextAttribute.Text : ar.TextString;
                            row.Targets[key] = aid;
                        }
                        break;
                    }
                    // ③' 布局里的普通属性图签块
                    if (row == null) row = FromAttBlocks(tr, ps, "布局图签块", lo.LayoutName, lo.LayoutName).FirstOrDefault();
                    // ② 布局里画出来的图签（普通文字）
                    if (row == null)
                    {
                        try
                        {
                            var t = new FrameTemplate { Name = "_scan" };
                            FrameLearner.Analyze(db, tr, ps, t, new List<ObjectId>(), null);
                            var good = t.Fields.Count(f => f.Key == "图号" || f.Key == "图名");
                            if (good > 0 && t.Fields.Count >= 3)
                            {
                                row = new TitleRow { Kind = "布局文字", Locator = lo.LayoutName, Layout = lo.LayoutName, Space = ps.ObjectId };
                                foreach (var f in t.Fields)
                                {
                                    row.Values[f.Key] = f.Sample ?? "";
                                    if (!f.SourceId.IsNull) row.Targets[f.Key] = f.SourceId; else row.Empty[f.Key] = f;
                                }
                            }
                        }
                        catch (Exception ex) { log?.Invoke($"布局「{lo.LayoutName}」图签识别失败：{ex.Message}"); }
                    }
                    if (row != null) rows.Add(row);
                }
                // ③ 模型空间的图框块
                var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
                var mrows = FromAttBlocks(tr, ms, "模型图框块", null, "");
                // 模型图框按位置排：从上到下、从左到右
                rows.AddRange(mrows);
                tr.Commit();
            }
            log?.Invoke($"找到 {rows.Count} 张图的图签：" + string.Join("，", rows.GroupBy(r => r.Kind).Select(g => g.Key + " " + g.Count())));
            return rows;
        }

        static List<TitleRow> FromAttBlocks(Transaction tr, BlockTableRecord space, string kind, string locator, string layout)
        {
            var list = new List<(TitleRow row, Point3d pos)>();
            foreach (ObjectId id in space)
            {
                if (id.ObjectClass.DxfName != "INSERT") continue;
                var br = (BlockReference)tr.GetObject(id, OpenMode.ForRead);
                if (br.AttributeCollection.Count < 2) continue;
                var b = (BlockTableRecord)tr.GetObject(br.BlockTableRecord, OpenMode.ForRead);
                if (b.Name.StartsWith(Sheets.TitlePrefix, StringComparison.OrdinalIgnoreCase)) continue;
                var atts = br.AttributeCollection.Cast<ObjectId>().Select(a => (AttributeReference)tr.GetObject(a, OpenMode.ForRead)).ToList();
                if (!atts.Any(a => TitleTags.IsMatch(a.Tag))) continue;
                var row = new TitleRow { Kind = kind, Locator = locator ?? br.Handle.ToString(), Layout = layout, Space = space.ObjectId };
                foreach (var a in atts)
                {
                    var key = a.Tag;
                    if (row.Values.ContainsKey(key)) continue;
                    row.Values[key] = a.IsMTextAttribute ? a.MTextAttribute.Text : a.TextString;
                    row.Targets[key] = a.ObjectId;
                }
                list.Add((row, br.Position));
            }
            return list.OrderByDescending(x => Math.Round(x.pos.Y / 1000)).ThenBy(x => x.pos.X).Select(x => x.row).ToList();
        }

        // ------------------------------------------------------------------ CSV
        static string Q(string s)
        {
            s = (s ?? "").Replace("\r", "");
            return s.IndexOfAny(new[] { ',', '"', '\n' }) >= 0 ? "\"" + s.Replace("\"", "\"\"") + "\"" : s;
        }

        public static string Export(List<TitleRow> rows, string path)
        {
            string[] first = { "图号", "图名", "比例", "张号", "版本", "日期" };
            var keys = new List<string>();
            foreach (var f in first) if (rows.Any(r => r.Values.ContainsKey(f))) keys.Add(f);
            foreach (var r in rows) foreach (var k in r.Values.Keys) if (!keys.Contains(k)) keys.Add(k);
            var sb = new StringBuilder();
            sb.AppendLine(string.Join(",", new[] { "序号", "类型", "定位（勿改）", "布局名" }.Concat(keys).Select(Q)));
            int i = 1;
            foreach (var r in rows)
                sb.AppendLine(string.Join(",", new[] { (i++).ToString(), r.Kind, r.Locator, r.Layout }.Concat(keys.Select(k => r.Values.TryGetValue(k, out var v) ? v : "")).Select(Q)));
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, sb.ToString(), new UTF8Encoding(true));
            return $"已导出 {rows.Count} 张图、{keys.Count} 个字段：{path}";
        }

        public static List<List<string>> ReadCsv(string path)
        {
#if NET
            Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
#endif
            var text = File.ReadAllText(path, Encoding.UTF8);   // UTF-8；没有 BOM 时 .NET 也按 UTF-8 读
            if (text.IndexOf('\uFFFD') >= 0) text = File.ReadAllText(path, Encoding.GetEncoding(936));   // Excel「CSV（逗号分隔）」存成 GBK 的情况
            var rows = new List<List<string>>(); var cur = new List<string>(); var cell = new StringBuilder(); bool q = false;
            for (int i = 0; i < text.Length; i++)
            {
                char c = text[i];
                if (q)
                {
                    if (c == '"') { if (i + 1 < text.Length && text[i + 1] == '"') { cell.Append('"'); i++; } else q = false; }
                    else cell.Append(c);
                }
                else if (c == '"') q = true;
                else if (c == ',') { cur.Add(cell.ToString()); cell.Clear(); }
                else if (c == '\n' || c == '\r')
                {
                    if (c == '\r' && i + 1 < text.Length && text[i + 1] == '\n') i++;
                    cur.Add(cell.ToString()); cell.Clear();
                    if (cur.Any(x => x.Length > 0)) rows.Add(cur);
                    cur = new List<string>();
                }
                else cell.Append(c);
            }
            if (cell.Length > 0 || cur.Count > 0) { cur.Add(cell.ToString()); if (cur.Any(x => x.Length > 0)) rows.Add(cur); }
            return rows;
        }

        public sealed class Change { public TitleRow Row; public string Field; public string Old; public string New; public override string ToString() => $"{Row.Locator}　{Field}：「{Old}」→「{New}」"; }

        /// <summary>按「定位」列（布局名 / 块句柄）匹配；没有定位列时按行序。只列有变化的格子。</summary>
        public static List<Change> Diff(List<TitleRow> rows, string csv)
        {
            var t = ReadCsv(csv);
            if (t.Count < 2) return new List<Change>();
            var head = t[0].Select(h => h.Trim()).ToList();
            int iLoc = head.FindIndex(h => h.StartsWith("定位")), iLay = head.IndexOf("布局名");
            var skip = new HashSet<string> { "序号", "类型", "布局名" };
            var changes = new List<Change>();
            for (int r = 1; r < t.Count; r++)
            {
                var line = t[r];
                string Get(int i) => i >= 0 && i < line.Count ? line[i] : "";
                var row = iLoc >= 0 ? rows.FirstOrDefault(x => x.Locator == Get(iLoc).Trim()) : (r - 1 < rows.Count ? rows[r - 1] : null);
                if (row == null) continue;
                for (int c = 0; c < head.Count; c++)
                {
                    var k = head[c];
                    if (c == iLoc || skip.Contains(k) || k.Length == 0) continue;
                    if (!row.Values.ContainsKey(k)) continue;
                    var nv = Get(c).Replace("\r", "");
                    var ov = row.Values[k] ?? "";
                    if (Norm(nv) != Norm(ov)) changes.Add(new Change { Row = row, Field = k, Old = ov, New = nv });
                }
                if (iLay >= 0 && row.Layout.Length > 0 && Get(iLay).Trim().Length > 0 && Get(iLay).Trim() != row.Layout)
                    changes.Add(new Change { Row = row, Field = "布局名", Old = row.Layout, New = Get(iLay).Trim() });
            }
            return changes;
        }

        static string Norm(string s) => (s ?? "").Replace("\r", "").Trim();

        /// <summary>写入改动。原文件（已保存的那一版）先复制一份「原名.THL备份-时间.dwg」。</summary>
        public static string Apply(Document doc, List<Change> changes, Action<string> log)
        {
            if (changes.Count == 0) return "没有要改的内容。";
            var db = doc.Database;
            string backup = null;
            var fn = db.Filename;
            if (!string.IsNullOrEmpty(fn) && File.Exists(fn) && fn.EndsWith(".dwg", StringComparison.OrdinalIgnoreCase))
            {
                backup = Path.Combine(Path.GetDirectoryName(fn), Path.GetFileNameWithoutExtension(fn) + $".THL备份-{DateTime.Now:yyyyMMdd-HHmmss}.dwg");
                File.Copy(fn, backup);
            }
            int n = 0;
            using (doc.LockDocument())
            {
                using (var tr = db.TransactionManager.StartTransaction())
                {
                    foreach (var ch in changes.Where(c => c.Field != "布局名"))
                    {
                        if (ch.Row.Targets.TryGetValue(ch.Field, out var id) && !id.IsNull)
                        {
                            var o = tr.GetObject(id, OpenMode.ForWrite);
                            if (o is AttributeReference ar) Sheets.SetAttPublic(db, ar, ch.New);
                            else if (o is DBText dt) { dt.TextString = ch.New.Replace("\n", " "); if (dt.HorizontalMode != TextHorizontalMode.TextLeft || dt.VerticalMode != TextVerticalMode.TextBase) using (new WorkingDbScope(db)) dt.AdjustAlignment(db); }
                            else if (o is MText mt) mt.Contents = ch.New.Replace("\\", "\\\\").Replace("{", "\\{").Replace("}", "\\}").Replace("\n", "\\P");
                            n++;
                        }
                        else if (ch.Row.Empty.TryGetValue(ch.Field, out var f) && ch.New.Length > 0)
                        {
                            // 空格子：照学到的位置、字高新建文字
                            var ps = (BlockTableRecord)tr.GetObject(ch.Row.Space, OpenMode.ForWrite);
                            var t = new DBText();
                            t.SetDatabaseDefaults(db);
                            t.TextString = ch.New.Replace("\n", " "); t.Height = f.H; t.Layer = string.IsNullOrEmpty(f.Layer) ? "0" : f.Layer;
                            var p = new Point3d(f.X, f.Y, 0);
                            t.Position = p;
                            if (f.Just != "BL")
                            {
                                var v = f.Just[0]; var h = f.Just.Length > 1 ? f.Just[1] : 'L';
                                t.HorizontalMode = h == 'C' ? TextHorizontalMode.TextCenter : h == 'R' ? TextHorizontalMode.TextRight : TextHorizontalMode.TextLeft;
                                t.VerticalMode = v == 'T' ? TextVerticalMode.TextTop : v == 'M' ? TextVerticalMode.TextVerticalMid : TextVerticalMode.TextBase;
                                t.AlignmentPoint = p;
                            }
                            try { ps.AppendEntity(t); tr.AddNewlyCreatedDBObject(t, true); } catch { t.Layer = "0"; }
                            if (f.Just != "BL") using (new WorkingDbScope(db)) t.AdjustAlignment(db);
                            n++;
                        }
                        else log?.Invoke($"跳过：{ch}（这一格在图里找不到可写的对象）");
                    }
                    tr.Commit();
                }
                var lm = LayoutManager.Current;
                foreach (var ch in changes.Where(c => c.Field == "布局名"))
                {
                    var nn = Sheets.SafeLayoutName(ch.New);
                    try { lm.RenameLayout(ch.Old, nn); n++; } catch (Exception ex) { log?.Invoke($"布局「{ch.Old}」改名失败：{ex.Message}"); }
                }
            }
            doc.Editor.Regen();
            return $"已改 {n} 处。" + (backup != null ? "\n原文件备份：" + backup : "\n（图纸还没保存过，没有做文件备份；可以用 UNDO 撤销）");
        }
    }
}
