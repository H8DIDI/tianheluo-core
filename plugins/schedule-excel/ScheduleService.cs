using Autodesk.Revit.DB;

namespace THL.Plugins.ScheduleExcel;

public sealed class Change
{
    public Element Element = null!;
    public Parameter Param = null!;
    public string ScheduleName = "", Field = "", Old = "", New = "";
}

public sealed class ImportPlan
{
    public List<Change> Changes { get; } = new();
    public List<string> Issues { get; } = new();
    public int Rows, SheetsMatched;
    public int ElementsAffected => Changes.Select(c => c.Element.Id).Distinct().Count();
}

public sealed class ApplyResult
{
    public int Ok;
    public List<string> Failed { get; } = new();
}

/// <summary>明细表 ⇄ Excel：导出为 .xlsx，修改后按构件 ID 把改动写回 Revit。</summary>
public static class ScheduleService
{
    public const string MetaSheet = "__THL";
    const string IdHeader = "ElementId";

    // ------------------------------------------------------------ 公共小工具
    public static List<ViewSchedule> ListSchedules(Document doc) =>
        new FilteredElementCollector(doc).OfClass(typeof(ViewSchedule)).Cast<ViewSchedule>()
            .Where(s => !s.IsTitleblockRevisionSchedule && !s.IsInternalKeynoteSchedule && !s.Definition.IsKeySchedule)
            .OrderBy(s => s.Name, StringComparer.CurrentCultureIgnoreCase).ToList();

    sealed class Col
    {
        public string Heading = "";
        public ScheduleField Field = null!;
        public bool Editable;
    }

    static List<Col> Columns(ViewSchedule s)
    {
        var def = s.Definition; var cols = new List<Col>();
        for (int i = 0; i < def.GetFieldCount(); i++)
        {
            var f = def.GetField(i);
            if (f.IsHidden) continue;
            var h = f.GetName();
            cols.Add(new Col { Heading = h, Field = f, Editable = f.FieldType == ScheduleFieldType.Instance });
        }
        return cols;
    }

    /// <summary>找到字段对应的参数（实例参数在构件上，类型参数在类型上）。</summary>
    static Parameter? FindParam(Document doc, Element e, ScheduleField f)
    {
        Element? holder = f.FieldType switch
        {
            ScheduleFieldType.Instance => e,
            ScheduleFieldType.ElementType => doc.GetElement(e.GetTypeId()),
            _ => null,
        };
        if (holder == null) return null;
        foreach (Parameter p in holder.Parameters)
            if (p.Id == f.ParameterId) return p;
        return null;
    }

    static string ValueText(Parameter? p)
    {
        if (p == null || !p.HasValue) return "";
        return p.StorageType == StorageType.String ? (p.AsString() ?? "") : (p.AsValueString() ?? "");
    }

    // ------------------------------------------------------------ 导出
    public static int Export(Document doc, IReadOnlyList<ViewSchedule> schedules, string path)
    {
        var sheets = new List<Sheet>(); var map = new Sheet(MetaSheet) { Hidden = true };
        map.Rows.Add(new List<string> { "SheetName", "ScheduleName", "ScheduleUniqueId", "Project" });
        var used = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { MetaSheet };
        int totalRows = 0;

        foreach (var s in schedules)
        {
            var cols = Columns(s);
            var name = Xlsx.SafeSheetName(s.Name); var baseName = name; int k = 2;
            while (!used.Add(name)) name = Xlsx.SafeSheetName(baseName.Length > 27 ? baseName[..27] : baseName) + "_" + k++;
            var sh = new Sheet(name);
            var elems = new FilteredElementCollector(doc, s.Id).WhereElementIsNotElementType().ToList();
            // 只有“实例参数且参数本身可写”的列才标 RW（例如墙的长度是只读的，标 RO）
            foreach (var c in cols.Where(c => c.Editable))
                c.Editable = elems.Take(50).Select(e => FindParam(doc, e, c.Field)).Any(p => p != null && !p.IsReadOnly);
            sh.Rows.Add(new List<string> { IdHeader }.Concat(cols.Select(c => c.Heading)).ToList());
            sh.Rows.Add(new List<string> { "RO" }.Concat(cols.Select(c => c.Editable ? "RW" : "RO")).ToList());

            foreach (var e in elems)
            {
                var row = new List<string> { e.Id.Value.ToString() };
                foreach (var c in cols) row.Add(ValueText(FindParam(doc, e, c.Field)));
                sh.Rows.Add(row);
            }
            totalRows += elems.Count;
            sheets.Add(sh);
            map.Rows.Add(new List<string> { name, s.Name, s.UniqueId, doc.Title });
        }
        sheets.Add(map);
        Xlsx.Write(path, sheets);
        return totalRows;
    }

    // ------------------------------------------------------------ 导入：先出预览
    public static ImportPlan Plan(Document doc, string path)
    {
        var plan = new ImportPlan();
        var book = Xlsx.Read(path);
        var meta = book.FirstOrDefault(b => b.Name == MetaSheet);
        var byName = ListSchedules(doc).ToDictionary(s => s.Name, s => s, StringComparer.OrdinalIgnoreCase);
        var byUid = ListSchedules(doc).ToDictionary(s => s.UniqueId, s => s);

        foreach (var sh in book.Where(b => b.Name != MetaSheet))
        {
            ViewSchedule? sched = null;
            var m = meta?.Rows.Skip(1).FirstOrDefault(r => r.Count > 0 && r[0] == sh.Name);
            if (m != null && m.Count > 2 && byUid.TryGetValue(m[2], out var s1)) sched = s1;
            else if (m != null && m.Count > 1 && byName.TryGetValue(m[1], out var s2)) sched = s2;
            else if (byName.TryGetValue(sh.Name, out var s3)) sched = s3;
            if (sched == null) { plan.Issues.Add($"工作表「{sh.Name}」找不到对应的明细表，已跳过"); continue; }
            if (sh.Rows.Count < 3) continue;
            plan.SheetsMatched++;

            var header = sh.Rows[0]; var flags = sh.Rows[1];
            var cols = Columns(sched);
            // 按表头文字对应字段（允许用户调整列的顺序）；同名表头按先后顺序依次对应
            var taken = new HashSet<int>(); var colMap = new Dictionary<int, Col>();
            for (int c = 1; c < header.Count; c++)
            {
                var idx = cols.FindIndex(x => !taken.Contains(cols.IndexOf(x)) && x.Heading == header[c]);
                if (idx < 0) continue;
                taken.Add(idx); colMap[c] = cols[idx];
            }

            for (int r = 2; r < sh.Rows.Count; r++)
            {
                var row = sh.Rows[r];
                if (row.Count == 0 || !long.TryParse(row[0], out var id)) continue;
                plan.Rows++;
                var e = doc.GetElement(new ElementId(id));
                if (e == null) { plan.Issues.Add($"「{sh.Name}」第 {r + 1} 行：找不到构件 {id}（可能已被删除）"); continue; }

                foreach (var (c, col) in colMap)
                {
                    if (!col.Editable) continue;
                    if (c < flags.Count && flags[c] == "RO") continue;
                    var p = FindParam(doc, e, col.Field);
                    if (p == null || p.IsReadOnly) continue;
                    var cell = (c < row.Count ? row[c] : "").Trim();
                    var cur = ValueText(p).Trim();
                    if (cell == cur) continue;
                    if (cell.Length == 0 && p.StorageType != StorageType.String) continue; // 数值类参数不能置空
                    plan.Changes.Add(new Change { Element = e, Param = p, ScheduleName = sched.Name, Field = col.Heading, Old = cur, New = cell });
                }
            }
        }
        return plan;
    }

    // ------------------------------------------------------------ 导入：应用（单个事务，可一步撤销）
    public static ApplyResult Apply(Document doc, ImportPlan plan)
    {
        var res = new ApplyResult();
        using var tx = new Transaction(doc, "天河落 明细表导入");
        tx.Start();
        foreach (var ch in plan.Changes)
        {
            try
            {
                bool ok = ch.Param.StorageType == StorageType.String
                    ? ch.Param.Set(ch.New)
                    : ch.Param.SetValueString(ch.New);
                if (ok) res.Ok++;
                else res.Failed.Add($"{ch.ScheduleName} · {ch.Element.Id.Value} · {ch.Field}：值「{ch.New}」无法写入（格式不对或参数受限）");
            }
            catch (Exception ex)
            {
                res.Failed.Add($"{ch.ScheduleName} · {ch.Element.Id.Value} · {ch.Field}：{ex.Message}");
            }
        }
        tx.Commit();
        return res;
    }
}
