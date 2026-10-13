using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Autodesk.Revit.DB;

namespace KaifaTools.TagCheck;

/// <summary>
/// One row of the result: an element that is missing a tag, has a wrong tag, is crowded, or is fine.
/// Status (漏标 / 用错 / 重叠 / 已标) and Position (位置不佳 or empty) are independent flags.
/// </summary>
public sealed record TagRow
{
    public const string PositionBad = "位置不佳";

    public long ElementId { get; init; }
    public string Mark { get; init; } = "";
    public string Category { get; init; } = "";
    public string Family { get; init; } = "";
    public string Status { get; init; } = "";        // 漏标 / 用错 / 重叠 / 已标
    public string Current { get; init; } = "";       // tags found on the element (family : type)
    public string Expected { get; init; } = "";      // the tag family it should carry
    public string Position { get; init; } = "";      // 位置不佳, or "" when the tag position is fine
    public string PositionNote { get; init; } = "";  // why: leader too long / tag head outside the crop box
}

/// <summary>The "layout" section of the standard file. Keys that are missing use these defaults.</summary>
public sealed class TagLayoutSettings
{
    public const double DefaultOffsetMm = 1200;
    public const double DefaultMinGapMm = 150;
    public const double DefaultMaxLeaderMm = 3000;
    public static readonly string[] Sides = { "auto", "up", "down", "left", "right" };

    /// <summary>Distance from the element centre to the tag head (mm).</summary>
    [JsonPropertyName("offsetMm")]
    public double OffsetMm { get; set; } = DefaultOffsetMm;

    /// <summary>Smallest gap between two tag boxes (mm). Tags closer than this are pushed apart and shown as 重叠.</summary>
    [JsonPropertyName("minGapMm")]
    public double MinGapMm { get; set; } = DefaultMinGapMm;

    /// <summary>"auto" keeps each tag on the side it is already on; up / down / left / right put every tag on that side of the sheet.</summary>
    [JsonPropertyName("side")]
    public string Side { get; set; } = "auto";

    /// <summary>A tag head farther than this from its element (mm) is shown as 位置不佳.</summary>
    [JsonPropertyName("maxLeaderMm")]
    public double MaxLeaderMm { get; set; } = DefaultMaxLeaderMm;

    /// <summary>Puts bad values back to the defaults and returns a note for each one that was wrong.</summary>
    public List<string> Normalize()
    {
        var problems = new List<string>();
        string side = (Side ?? "").Trim().ToLowerInvariant();
        if (side.Length == 0) side = "auto";
        if (Array.IndexOf(Sides, side) < 0)
        {
            problems.Add($"side 应为 auto/up/down/left/right，现为“{Side}”，已按 auto 处理");
            side = "auto";
        }
        Side = side;
        if (!(OffsetMm > 0))
        {
            problems.Add($"offsetMm 应大于 0，已按 {DefaultOffsetMm:0}");
            OffsetMm = DefaultOffsetMm;
        }
        if (!(MinGapMm >= 0))
        {
            problems.Add($"minGapMm 不能为负，已按 {DefaultMinGapMm:0}");
            MinGapMm = DefaultMinGapMm;
        }
        if (!(MaxLeaderMm > 0))
        {
            problems.Add($"maxLeaderMm 应大于 0，已按 {DefaultMaxLeaderMm:0}");
            MaxLeaderMm = DefaultMaxLeaderMm;
        }
        return problems;
    }
}

/// <summary>User-editable standard: category name -> allowed tag family names. Stored in Documents\KaifaTools\标注标准.json.</summary>
public sealed class TagStandard
{
    public Dictionary<string, List<string>> Categories { get; set; } = new();

    public string 说明 { get; set; } =
        "每个类别对应允许使用的标记族名（族名完全一致）。例：\"结构框架\": [\"结构框架标记-排管\"]。" +
        "留空或不写的类别，会按模型里该类构件用得最多的标记族来对比。" +
        "layout 段控制“整理标注位置”：offsetMm 标注离构件中心的距离（mm）；minGapMm 标注框之间的最小间距（mm）；" +
        "side 填 auto（保持每个标注现在的一侧）或 up / down / left / right（固定放到图上的上 / 下 / 左 / 右）；" +
        "maxLeaderMm 标注头离构件超过这个距离（mm）就标为“位置不佳”。不写 layout 段时用默认值。";

    [JsonPropertyName("layout")]
    public TagLayoutSettings Layout { get; set; } = new();
}

public static class TagCheckEngine
{
    // a method, not a static field: the standard-file code (ParseStandard) must not need the Revit types to load
    static (BuiltInCategory Cat, string Name)[] Targets() => new[]
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
            return ParseStandard(File.ReadAllText(StandardPath, Encoding.UTF8), out error);
        }
        catch (Exception ex)
        {
            error = "标注标准.json 读取失败，本次按模型里的多数标记族对比：" + ex.Message;
            return new TagStandard();
        }
    }

    /// <summary>
    /// Parses the text of the standard file. A file without a "layout" section uses the defaults.
    /// Bad layout values fall back to the defaults and are reported in error; the categories still load.
    /// </summary>
    public static TagStandard ParseStandard(string json, out string? error)
    {
        error = null;
        TagStandard s;
        try
        {
            s = JsonSerializer.Deserialize<TagStandard>(json, Opt) ?? new TagStandard();
        }
        catch (Exception ex)
        {
            error = "标注标准.json 格式有误，本次按模型里的多数标记族对比：" + ex.Message;
            return new TagStandard();
        }
        s.Categories ??= new Dictionary<string, List<string>>();
        s.Layout ??= new TagLayoutSettings();
        var problems = s.Layout.Normalize();
        if (problems.Count > 0) error = "标注标准.json 的 layout 设置有误：" + string.Join("；", problems);
        return s;
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
    /// Position flags use the "layout" section of the standard file.
    /// </summary>
    public static List<TagRow> Check(Document doc, Autodesk.Revit.DB.View view, out string summary)
    {
        var standard = LoadStandard(out _);
        var layout = standard.Layout;

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
        foreach (var (bic, label) in Targets())
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

        // layout: a correctly tagged element whose tag is closer than minGapMm to another tag is shown as 重叠
        var overlapping = TagLayout.OverlappingElements(doc, view, layout.MinGapMm);
        for (int i = 0; i < rows.Count; i++)
        {
            if (rows[i].Status == "已标" && overlapping.Contains(rows[i].ElementId))
                rows[i] = rows[i] with { Status = "重叠" };
        }

        // position: a leader longer than maxLeaderMm, or a tag head outside the view crop box
        var positions = TagLayout.PositionWarnings(doc, view, layout);
        for (int i = 0; i < rows.Count; i++)
        {
            if (positions.TryGetValue(rows[i].ElementId, out var note))
                rows[i] = rows[i] with { Position = TagRow.PositionBad, PositionNote = note };
        }

        summary = BuildSummary(view.Name, rows);
        return rows.OrderBy(r => r.Status == "已标" && r.Position.Length == 0)
                   .ThenBy(r => r.Status == "用错" ? 0 : 1).ThenBy(r => r.Category).ThenBy(r => r.Mark, StringComparer.Ordinal).ToList();
    }

    /// <summary>One line for the window and the task dialog: status counts first, then the separate position count.</summary>
    public static string BuildSummary(string viewName, IReadOnlyCollection<TagRow> rows)
    {
        int missing = rows.Count(r => r.Status == "漏标");
        int wrong = rows.Count(r => r.Status == "用错");
        int overlap = rows.Count(r => r.Status == "重叠");
        int ok = rows.Count(r => r.Status == "已标");
        int position = rows.Count(r => r.Position.Length > 0);
        return $"视图「{viewName}」共 {rows.Count} 个构件。状态：漏标 {missing}，用错 {wrong}，标注重叠 {overlap}，已标 {ok}。另有位置不佳 {position} 个。";
    }

    /// <summary>The CSV text of the rows (UTF-8 with BOM when written by ExportCsv).</summary>
    public static string CsvText(IEnumerable<TagRow> rows)
    {
        static string Q(string s) => "\"" + s.Replace("\"", "\"\"") + "\"";
        var sb = new StringBuilder();
        sb.AppendLine("ElementId,状态,标记,类别,族,当前标签,应为标签,位置,位置说明");
        foreach (var r in rows)
            sb.AppendLine(string.Join(",", r.ElementId, Q(r.Status), Q(r.Mark), Q(r.Category), Q(r.Family), Q(r.Current), Q(r.Expected),
                Q(r.Position), Q(r.PositionNote)));
        return sb.ToString();
    }

    public static string ExportCsv(List<TagRow> rows)
    {
        string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools");
        Directory.CreateDirectory(dir);
        string path = Path.Combine(dir, $"标注检查_{DateTime.Now:yyyyMMdd_HHmmss}.csv");
        File.WriteAllText(path, CsvText(rows), new UTF8Encoding(encoderShouldEmitUTF8Identifier: true));
        return path;
    }
}
