using System.Text;
using System.Text.Json;
using Autodesk.Revit.DB;

namespace KaifaTools.TagCheck;

/// <summary>One row of the result: an element that is missing a tag, has a wrong tag, or is fine.</summary>
public sealed class TagRow
{
    public long ElementId { get; init; }
    public string Mark { get; init; } = "";
    public string Category { get; init; } = "";
    public string Family { get; init; } = "";
    public string Status { get; init; } = "";      // 漏标 / 用错 / 已标
    public string Current { get; init; } = "";     // tags found on the element (family : type)
    public string Expected { get; init; } = "";    // the tag family it should carry
}

/// <summary>User-editable standard: category name -> allowed tag family names. Stored in Documents\KaifaTools\标注标准.json.</summary>
public sealed class TagStandard
{
    public Dictionary<string, List<string>> Categories { get; set; } = new();
    public string 说明 { get; set; } =
        "每个类别对应允许使用的标记族名（族名完全一致）。例：\"结构框架\": [\"结构框架标记-排管\"]。" +
        "留空或不写的类别，会按模型里该类构件用得最多的标记族来对比。";
}

public static class TagCheckEngine
{
    static readonly (BuiltInCategory Cat, string Name)[] Targets =
    {
        (BuiltInCategory.OST_StructuralFraming, "结构框架"),
        (BuiltInCategory.OST_Conduit, "线管"),
        (BuiltInCategory.OST_GenericModel, "常规模型"),
    };

    static readonly JsonSerializerOptions Opt = new()
    {
        WriteIndented = true, ReadCommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true,
    };

    public static string StandardPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools", "标注标准.json");

    /// <summary>Loads the standard. Creates the file with an empty standard if it does not exist.</summary>
    public static TagStandard LoadStandard(out string? error)
    {
        error = null;
        try
        {
            if (!File.Exists(StandardPath))
            {
                SaveStandard(new TagStandard());
                return new TagStandard();
            }
            return JsonSerializer.Deserialize<TagStandard>(File.ReadAllText(StandardPath, Encoding.UTF8), Opt) ?? new TagStandard();
        }
        catch (Exception ex)
        {
            error = "标注标准.json 格式有误，本次按模型里的多数标记族对比：" + ex.Message;
            return new TagStandard();
        }
    }

    public static void SaveStandard(TagStandard s)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(StandardPath)!);
        File.WriteAllText(StandardPath, JsonSerializer.Serialize(s, Opt), new UTF8Encoding(encoderShouldEmitUTF8Identifier: true));
    }

    static string FamilyOf(Document doc, ElementId typeId) =>
        (doc.GetElement(typeId) as ElementType)?.FamilyName ?? "";

    static string GroupKey(string category, string family) => category + "|" + family;

    /// <summary>
    /// Checks the given view. Elements are grouped by (category, element family) so that, for example,
    /// sewage wells tagged with "常规模型标记1" are compared with the other wells of their own family,
    /// not with every generic model in the model. A tag family is "acceptable" for a group when it is used
    /// by at least 5% of that group in the whole model; the standard file (family name first, then category name) overrides this.
    /// </summary>
    public static List<TagRow> Check(Document doc, Autodesk.Revit.DB.View view, out string summary)
    {
        var standard = LoadStandard(out _);

        // 1) tags placed in this view: element id -> family names of the tags on it
        var tagsOnElement = new Dictionary<long, List<string>>();
        foreach (IndependentTag tag in new FilteredElementCollector(doc, view.Id).OfClass(typeof(IndependentTag)).Cast<IndependentTag>())
        {
            string fam = FamilyOf(doc, tag.GetTypeId());
            foreach (var id in tag.GetTaggedLocalElementIds())
            {
                if (!tagsOnElement.TryGetValue(id.Value, out var list)) tagsOnElement[id.Value] = list = new List<string>();
                list.Add(fam);
            }
        }

        // 2) model-wide usage: group (category|element family) -> tag family -> count
        var usage = new Dictionary<string, Dictionary<string, int>>();
        var totals = new Dictionary<string, int>();
        foreach (IndependentTag tag in new FilteredElementCollector(doc).OfClass(typeof(IndependentTag)).Cast<IndependentTag>())
        {
            string fam = FamilyOf(doc, tag.GetTypeId());
            if (fam.Length == 0) continue;
            foreach (var id in tag.GetTaggedLocalElementIds())
            {
                var el = doc.GetElement(id);
                if (el == null) continue;
                string g = GroupKey(el.Category?.Name ?? "", FamilyOf(doc, el.GetTypeId()));
                if (!usage.TryGetValue(g, out var c)) usage[g] = c = new Dictionary<string, int>();
                c[fam] = c.TryGetValue(fam, out var n) ? n + 1 : 1;
                totals[g] = totals.TryGetValue(g, out var t) ? t + 1 : 1;
            }
        }

        // 3) every target element in the view
        var rows = new List<TagRow>();
        foreach (var (bic, label) in Targets)
        {
            foreach (var e in new FilteredElementCollector(doc, view.Id).OfCategory(bic).WhereElementIsNotElementType())
            {
                string catName = e.Category?.Name ?? label;
                string family = FamilyOf(doc, e.GetTypeId());
                string mark = e.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.AsString() ?? "";
                string g = GroupKey(catName, family);

                List<string> allowed;
                if (standard.Categories.TryGetValue(family, out var byFamily) && byFamily.Count > 0) allowed = byFamily;
                else if (standard.Categories.TryGetValue(catName, out var byCat) && byCat.Count > 0) allowed = byCat;
                else
                {
                    // the tag families this group really uses (5% of the group or more)
                    allowed = new List<string>();
                    if (usage.TryGetValue(g, out var c) && totals.TryGetValue(g, out var total))
                        allowed = c.Where(kv => kv.Value * 20 >= total).OrderByDescending(kv => kv.Value).Select(kv => kv.Key).ToList();
                }
                var allowedSet = new HashSet<string>(allowed);
                string expected = allowed.Count == 0 ? "（模型里没有这类构件的标记，请在标准文件里指定）" : string.Join("、", allowed);

                tagsOnElement.TryGetValue(e.Id.Value, out var tags);
                string status, current = tags == null ? "" : string.Join("、", tags);
                if (tags == null || tags.Count == 0)
                    status = "漏标";
                else if (allowedSet.Count > 0 && !tags.Any(t => allowedSet.Contains(t)))
                    status = "用错";
                else
                    status = "已标";

                rows.Add(new TagRow
                {
                    ElementId = e.Id.Value, Mark = mark, Category = catName, Family = family,
                    Status = status, Current = current, Expected = expected,
                });
            }
        }

        // layout: a correctly tagged element whose tag overlaps another tag is shown as 重叠
        var overlapping = TagLayout.OverlappingElements(doc, view);
        for (int i = 0; i < rows.Count; i++)
        {
            if (rows[i].Status == "已标" && overlapping.Contains(rows[i].ElementId))
                rows[i] = new TagRow { ElementId = rows[i].ElementId, Mark = rows[i].Mark, Category = rows[i].Category,
                    Family = rows[i].Family, Status = "重叠", Current = rows[i].Current, Expected = rows[i].Expected };
        }
        int overlap = rows.Count(r => r.Status == "重叠");
        int missing = rows.Count(r => r.Status == "漏标"), wrong = rows.Count(r => r.Status == "用错"), ok = rows.Count(r => r.Status == "已标");
        summary = $"视图「{view.Name}」：共 {rows.Count} 个构件，漏标 {missing}，用错 {wrong}，标注重叠 {overlap}，已标 {ok}。";
        return rows.OrderBy(r => r.Status == "已标").ThenBy(r => r.Status == "用错" ? 0 : 1).ThenBy(r => r.Category).ThenBy(r => r.Mark, StringComparer.Ordinal).ToList();
    }

    public static string ExportCsv(List<TagRow> rows)
    {
        string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools");
        Directory.CreateDirectory(dir);
        string path = Path.Combine(dir, $"标注检查_{DateTime.Now:yyyyMMdd_HHmmss}.csv");
        static string Q(string s) => "\"" + s.Replace("\"", "\"\"") + "\"";
        var sb = new StringBuilder();
        sb.AppendLine("ElementId,状态,标记,类别,族,当前标签,应为标签");
        foreach (var r in rows)
            sb.AppendLine(string.Join(",", r.ElementId, Q(r.Status), Q(r.Mark), Q(r.Category), Q(r.Family), Q(r.Current), Q(r.Expected)));
        File.WriteAllText(path, sb.ToString(), new UTF8Encoding(encoderShouldEmitUTF8Identifier: true));
        return path;
    }
}
