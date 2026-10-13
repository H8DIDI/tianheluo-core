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

    /// <summary>Checks the given view. Returns every target element with its status.</summary>
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

        // 2) the usual tag family per category, across the whole model (used when no standard is set)
        var counts = new Dictionary<string, Dictionary<string, int>>();
        foreach (IndependentTag tag in new FilteredElementCollector(doc).OfClass(typeof(IndependentTag)).Cast<IndependentTag>())
        {
            string fam = FamilyOf(doc, tag.GetTypeId());
            if (fam.Length == 0) continue;
            foreach (var id in tag.GetTaggedLocalElementIds())
            {
                string cat = doc.GetElement(id)?.Category?.Name ?? "";
                if (cat.Length == 0) continue;
                if (!counts.TryGetValue(cat, out var c)) counts[cat] = c = new Dictionary<string, int>();
                c[fam] = c.TryGetValue(fam, out var n) ? n + 1 : 1;
            }
        }
        string Majority(string cat) =>
            counts.TryGetValue(cat, out var c) && c.Count > 0 ? c.OrderByDescending(kv => kv.Value).First().Key : "";

        // 3) every target element in the view
        var rows = new List<TagRow>();
        foreach (var (bic, label) in Targets)
        {
            foreach (var e in new FilteredElementCollector(doc, view.Id).OfCategory(bic).WhereElementIsNotElementType())
            {
                string catName = e.Category?.Name ?? label;
                string family = FamilyOf(doc, e.GetTypeId());
                string mark = e.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.AsString() ?? "";

                string expected = standard.Categories.TryGetValue(catName, out var allowed) && allowed.Count > 0
                    ? string.Join(" 或 ", allowed)
                    : Majority(catName);
                var allowedSet = standard.Categories.TryGetValue(catName, out var a2) && a2.Count > 0
                    ? new HashSet<string>(a2)
                    : new HashSet<string> { Majority(catName) };

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

        int missing = rows.Count(r => r.Status == "漏标"), wrong = rows.Count(r => r.Status == "用错"), ok = rows.Count(r => r.Status == "已标");
        summary = $"视图「{view.Name}」：共 {rows.Count} 个构件，漏标 {missing}，用错 {wrong}，已标 {ok}。";
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
