using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace KaifaTools.DuctSnap;

/// <summary>
/// User-editable rules in Documents\KaifaTools\排管贴井规则.json. Read on every run, so edits apply without rebuilding.
/// Units: millimetres.
/// </summary>
public sealed class DuctSnapRules
{
    public double MaxExtendMm { get; set; } = 1000;      // an end this far (or less) short of the inner wall is extended to it
    public double MaxTrimMm { get; set; } = 600;         // an end this far (or less) inside the well is trimmed back; deeper -> a person decides
    public double MaxEdgeTrimMm { get; set; } = 2500;    // a bank running inside a well's wall is trimmed back to the well's edge if the excess is this long or less
    public double ToleranceMm { get; set; } = 5;         // within this distance the end already sits on the wall: no change
    public double MinLengthMm { get; set; } = 500;       // a bank shorter than this after the change is not edited
    public double MinFaceAreaM2 { get; set; } = 0.9;     // wall faces smaller than this are ignored
    public double FaceAreaRatio { get; set; } = 0.4;     // ...and so are faces below this share of the largest wall face

    // Which structural framing counts as a 排管 (matched inside the family name)
    public string[] DuctKeywords { get; set; } = { "排管", "HDPE" };

    // Which wells the ends are snapped to (family name prefixes)
    public string[] WellPrefixes { get; set; } =
    {
        "ELE-EM", "ELE-TELCO", "TELCO", "LV-低压拉线井", "ELV PIT", "ELV-MANHOLE", "ELE-LV-", "ELV-1000",
    };

    // Never touched (checked against family names of banks and wells)
    public string[] Exclude { get; set; } = { "雨水", "排水", "污水", "阀门", "井盖", "mold_" };

    [JsonIgnore]
    public SnapLimits Limits => new(MaxExtendMm, MaxTrimMm, ToleranceMm);

    static readonly JsonSerializerOptions Opt = new()
    {
        WriteIndented = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
    };

    public static string FilePath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools", "排管贴井规则.json");

    /// <summary>Reads the rules. Creates the file with defaults when missing. A broken file falls back to defaults and says why.</summary>
    public static DuctSnapRules Load(out string? error)
    {
        error = null;
        try
        {
            if (!File.Exists(FilePath))
            {
                Save(new DuctSnapRules());
                return new DuctSnapRules();
            }
            return JsonSerializer.Deserialize<DuctSnapRules>(File.ReadAllText(FilePath, Encoding.UTF8), Opt) ?? new DuctSnapRules();
        }
        catch (Exception ex)
        {
            error = "排管贴井规则.json 格式有误，已临时使用默认规则：" + ex.Message;
            return new DuctSnapRules();
        }
    }

    public static void Save(DuctSnapRules rules)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
        File.WriteAllText(FilePath, JsonSerializer.Serialize(rules, Opt), new UTF8Encoding(encoderShouldEmitUTF8Identifier: true));
    }
}

/// <summary>One bank end that lies near a well, with what the plugin would do to it.</summary>
public sealed class SnapRow
{
    public long BankId { get; init; }
    public int EndIndex { get; init; }            // 0 = start, 1 = end (curve order)
    public string BankFamily { get; init; } = "";
    public long WellId { get; init; }
    public string WellFamily { get; init; } = "";
    public SnapKind Kind { get; init; }
    public double MoveMm { get; init; }           // signed; + = along the bank toward its other end
    public string Status { get; init; } = "";     // 可修改 / 已贴合 / 需人工
    public string Action { get; init; } = "";     // 延伸到内壁 / 修剪伸入井内的部分 / 不动
    public string Note { get; init; } = "";       // why, in words

    // Elevations (标高) of both ends of the bank, mm: before the change and as planned after it
    public double StartZBeforeMm { get; init; }
    public double StartZAfterMm { get; init; }
    public double EndZBeforeMm { get; init; }
    public double EndZAfterMm { get; init; }

    // Display helpers for the grid
    public string End => EndIndex == 0 ? "起点" : "终点";
    public string Move => Kind is SnapKind.Extend or SnapKind.Trim or SnapKind.ToInnerWall or SnapKind.ExtendTooFar or SnapKind.TrimTooFar
        ? Math.Abs(MoveMm).ToString("0", System.Globalization.CultureInfo.InvariantCulture) : "";
    public string StartZ => ZText(StartZBeforeMm, StartZAfterMm);
    public string EndZ => ZText(EndZBeforeMm, EndZAfterMm);

    private static string ZText(double before, double after) =>
        $"{before.ToString("0.0", System.Globalization.CultureInfo.InvariantCulture)} → {after.ToString("0.0", System.Globalization.CultureInfo.InvariantCulture)}";
}

/// <summary>
/// Plain-text log of each run in Documents\KaifaTools\排管贴井日志.txt: start, timing, result and any exception.
/// Logging never throws, so it cannot break the command.
/// </summary>
public static class DuctSnapLog
{
    public static string FilePath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools", "排管贴井日志.txt");

    public static void Write(string text)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
            File.AppendAllText(FilePath, $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {text}{Environment.NewLine}",
                new UTF8Encoding(encoderShouldEmitUTF8Identifier: true));
        }
        catch
        {
            // logging must not break the command
        }
    }
}

public static class DuctSnapCsv
{
    /// <summary>Writes the rows to Documents\KaifaTools\排管贴井_<time>.csv and returns the path. UTF-8 with BOM for Excel.</summary>
    public static string Write(IEnumerable<SnapRow> rows)
    {
        string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools");
        Directory.CreateDirectory(dir);
        string path = Path.Combine(dir, $"排管贴井_{DateTime.Now:yyyyMMdd_HHmmss}.csv");
        var sb = new StringBuilder();
        sb.AppendLine("状态,排管ID,排管族,端点,井ID,井族,动作,移动(mm),起点标高改前(mm),起点标高改后(mm),终点标高改前(mm),终点标高改后(mm),说明");
        var inv = System.Globalization.CultureInfo.InvariantCulture;
        foreach (var r in rows)
        {
            sb.AppendLine(string.Join(",", new[]
            {
                r.Status, r.BankId.ToString(), Csv(r.BankFamily), r.End, r.WellId.ToString(), Csv(r.WellFamily),
                r.Action, r.Move,
                r.StartZBeforeMm.ToString("0.0", inv), r.StartZAfterMm.ToString("0.0", inv),
                r.EndZBeforeMm.ToString("0.0", inv), r.EndZAfterMm.ToString("0.0", inv),
                Csv(r.Note),
            }));
        }
        File.WriteAllText(path, sb.ToString(), new UTF8Encoding(encoderShouldEmitUTF8Identifier: true));
        return path;
    }

    private static string Csv(string s) => s.Contains(',') || s.Contains('"') || s.Contains('\n')
        ? "\"" + s.Replace("\"", "\"\"") + "\""
        : s;
}
