using System.Text;
using System.Text.Json;
using Autodesk.Revit.DB;

namespace KaifaTools.WellDepth;

// ---------------------------------------------------------------------------
// Rule (set by the user, editable in Documents\KaifaTools\井深规则.json):
//   well bottom elevation = lowest connected duct-bank bottom - ClearanceMm
//   well depth            = well top offset (相对标高的偏移) - well bottom elevation
// Only the families listed in the rules file are processed. Storm / sewage / valve wells are never touched.
// ---------------------------------------------------------------------------

/// <summary>User-editable rules. The file is read on every click, so edits take effect without rebuilding.</summary>
public sealed class DepthRules
{
    // ---- numbers (mm) ----
    public double ClearanceMm { get; set; } = 300;   // well bottom sits this far below the lowest pipe
    public double ToleranceMm { get; set; } = 50;    // within this error the well counts as already correct
    public double TightMm { get; set; } = 50;        // pipe must touch the well footprint (+ this margin)
    public double MaxGapMm { get; set; } = 1000;     // a pipe END that stops short of a well still counts if closer than this
    public double MaxDepthMm { get; set; } = 6000;   // deeper than this -> needs a human review, not auto-written
    public double MinDepthMm { get; set; } = 500;    // shallower than this -> needs review
    public double MaxSpreadMm { get; set; } = 2000;  // lowest pipe is this much below the next one -> suspicious, review
    public double MaxRefGapMm { get; set; } = 1000;  // |well top - offset - reference| above this -> the well's levels look wrong, review
    public bool ShowPlanFirst { get; set; } = true;  // true = show rules + scope and ask before writing

    // ---- which wells we may touch (family name prefixes) ----
    public string[] WellLv { get; set; } = { "LV-低压拉线井" };
    public string[] WellEm { get; set; } = { "ELE-EM" };
    public string[] WellTelco { get; set; } = { "ELE-TELCO", "TELCO" };
    public string[] WellElv { get; set; } = { "ELV PIT", "ELV-MANHOLE" };

    // ---- which pipes belong to which system (keywords, matched inside family name or instance name) ----
    public string[] PipeEm { get; set; } = { "11KV", "275KV" };
    public string[] PipeTelco { get; set; } = { "TELCO" };
    public string[] PipeElv { get; set; } = { "ELV" };
    public string[] PipeLv { get; set; } = { "LV" };

    // ---- never touched (checked first, for wells and pipes alike) ----
    public string[] Exclude { get; set; } = { "雨水", "排水", "污水", "阀门", "井盖", "mold_" };
}

/// <summary>Reads and writes the rules file in Documents\KaifaTools.</summary>
public static class RulesFile
{
    public static string Path => System.IO.Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools", "井深规则.json");

    static readonly JsonSerializerOptions Opt = new()
    {
        WriteIndented = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
    };

    /// <summary>Returns the rules from the file. Creates the file with defaults if missing. On a broken file, falls back to defaults and reports why.</summary>
    public static DepthRules Load(out string? error)
    {
        error = null;
        try
        {
            if (!File.Exists(Path))
            {
                Save(new DepthRules());
                return new DepthRules();
            }
            return JsonSerializer.Deserialize<DepthRules>(File.ReadAllText(Path, Encoding.UTF8), Opt) ?? new DepthRules();
        }
        catch (Exception ex)
        {
            error = "井深规则.json 格式有误，已临时使用默认规则：" + ex.Message;
            return new DepthRules();
        }
    }

    public static void Save(DepthRules rules)
    {
        Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
        File.WriteAllText(Path, JsonSerializer.Serialize(rules, Opt), new UTF8Encoding(encoderShouldEmitUTF8Identifier: true));
    }
}

public enum WellSystem { Lv, Em, Telco, Elv }

public enum WellStatus
{
    Ok,             // already correct
    Adjust,         // depth must change
    SyncOnly,       // depth is right, but the "井底标高" parameter is stale
    Review,         // change needed, but the numbers look odd -> skipped, check by hand
    NoPipe,         // no pipe found at this well
    Temporary,      // "临时电" -> skipped
    NoParameter,    // family has no depth parameter we can read
}

/// <summary>One duct bank (structural framing) or conduit, in millimetres (absolute model coordinates).</summary>
internal sealed record PipeInfo(long Id, WellSystem System, double X0, double Y0, double Z0, double X1, double Y1, double Z1);

internal sealed record PipeHit(long PipeId, double BottomZ, bool IsGap, double GapMm, bool Ambiguous = false);

/// <summary>Everything we know about one well: current values, matched pipes, proposed values.</summary>
public sealed class WellResult
{
    public long Id;
    public string Mark = "";
    public string Family = "";
    public WellSystem System;
    public double OffsetMm;          // parameter "相对标高的偏移"
    public double TopMm;             // real top of the well (absolute, from its bounding box)
    public double RefMm;             // TopMm - OffsetMm: the height the offset is measured from (normally ~0)
    public double? CurBottomMm;      // parameter "井底标高"
    public double? CurDepthMm;       // parameter "井内深度" (or "深度")
    public double? CurInnerBottomMm; // parameter "井内底标高" (manual annotation value)
    public double MinX, MinY, MaxX, MaxY;
    internal List<PipeHit> Hits = new();

    public double? LowestPipeMm;     // absolute
    public double NewBottomMm;       // new 井底标高 (absolute, = lowest pipe bottom - clearance)
    public double NewDepthMm;
    public WellStatus Status;
    public string Note = "";
    public int PipeCount => Hits.Count;
    public bool HasGapPipe => Hits.Any(h => h.IsGap);   // a pipe end stops short of the well and was matched by distance
    public string PipeIds => string.Join(" ", Hits.Select(h => h.PipeId));
}

public static class WellDepthEngine
{
    private const string ParamBottom = "井底标高";
    private const string ParamDepth = "井内深度";
    private const string ParamDepth2 = "深度";
    private const string ParamInnerBottom = "井内底标高";
    private const string ParamOffsetA = "相对标高的偏移";
    private const string ParamOffsetB = "相对主体的偏移";

    /// <summary>Rules used by the last Analyse call.</summary>
    public static DepthRules Rules { get; private set; } = new();
    /// <summary>Problem with the rules file (null = fine).</summary>
    public static string? RulesError { get; private set; }
    /// <summary>Pipes that matched no system: "族 | 实例名 : 数量". Shown to the user so missing keywords can be added.</summary>
    public static List<string> UnmatchedPipes { get; } = new();
    /// <summary>Pipes ignored because they lie above the well top.</summary>
    public static int IgnoredAbovePipes { get; private set; }

    private static double ToMm(double feet) => UnitUtils.ConvertFromInternalUnits(feet, UnitTypeId.Millimeters);
    public static double ToFeet(double mm) => UnitUtils.ConvertToInternalUnits(mm, UnitTypeId.Millimeters);

    // ---- which families are "wells we may touch" ---------------------------------

    public static WellSystem? GetWellSystem(string family)
    {
        if (Rules.Exclude.Any(k => family.Contains(k))) return null;
        if (Rules.WellLv.Any(p => family.StartsWith(p))) return WellSystem.Lv;
        if (Rules.WellEm.Any(p => family.StartsWith(p))) return WellSystem.Em;
        if (Rules.WellTelco.Any(p => family.StartsWith(p))) return WellSystem.Telco;
        if (Rules.WellElv.Any(p => family.StartsWith(p))) return WellSystem.Elv;
        return null;
    }

    /// <summary>Counts the wells that are deliberately left alone, for the plan dialog.</summary>
    public static Dictionary<string, int> CountExcluded(Document doc)
    {
        var counts = new Dictionary<string, int> { ["雨水井"] = 0, ["污水/排水井"] = 0, ["阀门井"] = 0 };
        foreach (var e in new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType())
        {
            if (e is not FamilyInstance fi) continue;
            string f = fi.Symbol.FamilyName;
            if (f.Contains("雨水")) counts["雨水井"]++;
            else if (f.Contains("排水") || f.Contains("污水")) counts["污水/排水井"]++;
            else if (f.Contains("阀门")) counts["阀门井"]++;
        }
        return counts;
    }

    // Which system does one string (family name or instance name) belong to?
    private static WellSystem? ClassifyPipeText(string text)
    {
        if (Rules.PipeEm.Any(k => text.Contains(k))) return WellSystem.Em;
        if (Rules.PipeTelco.Any(k => text.Contains(k))) return WellSystem.Telco;
        if (Rules.PipeElv.Any(k => text.Contains(k))) return WellSystem.Elv;
        if (Rules.PipeLv.Any(k => text.Contains(k))) return WellSystem.Lv;
        return null;
    }

    // The family name decides first; the instance name is only a fallback.
    // (A low-voltage duct bank can be named "ELV-..." on the instance while its family is "LV-...")
    private static WellSystem? GetPipeSystem(string family, string instanceName)
    {
        return ClassifyPipeText(family) ?? ClassifyPipeText(instanceName);
    }

    private static bool IsExcludedPipe(string family, string instanceName) =>
        Rules.Exclude.Any(k => (family + "|" + instanceName).Contains(k));

    private static bool IsTemporary(Element e)
    {
        string comments = e.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)?.AsString() ?? "";
        string mark = e.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.AsString() ?? "";
        return comments.Contains("临时") || mark.Contains("临时") || e.Name.Contains("临时");
    }

    private static Parameter? DoubleParam(Element e, string name)
    {
        var p = e.LookupParameter(name);
        return p is { StorageType: StorageType.Double } ? p : null;
    }

    // ---- main entry --------------------------------------------------------------

    /// <param name="onlyIds">If not null/empty, only these elements are analysed.</param>
    public static List<WellResult> Analyse(Document doc, ICollection<ElementId>? onlyIds)
    {
        Rules = RulesFile.Load(out var err);
        RulesError = err;
        UnmatchedPipes.Clear();
        IgnoredAbovePipes = 0;

        var pipes = CollectPipes(doc);
        var wells = CollectWells(doc, onlyIds, out var skippedTemporary);

        MatchPipesToWells(wells, pipes);
        foreach (var w in wells) Evaluate(w);
        wells.AddRange(skippedTemporary);
        return wells.OrderBy(w => w.System).ThenBy(w => w.Mark, StringComparer.Ordinal).ToList();
    }

    // ---- step 1: collect pipes ---------------------------------------------------

    private static List<PipeInfo> CollectPipes(Document doc)
    {
        var list = new List<PipeInfo>();
        var unmatched = new Dictionary<string, int>();
        foreach (var cat in new[] { BuiltInCategory.OST_StructuralFraming, BuiltInCategory.OST_Conduit })
        {
            foreach (var e in new FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType())
            {
                if (e.Location is not LocationCurve lc) continue;
                string family = (doc.GetElement(e.GetTypeId()) as ElementType)?.FamilyName ?? "";
                string name = e.Name;
                if (IsExcludedPipe(family, name) || IsTemporary(e)) continue;

                var system = GetPipeSystem(family, name);
                if (system is null)
                {
                    string key = family + " | " + name;
                    unmatched[key] = unmatched.TryGetValue(key, out var n) ? n + 1 : 1;
                    continue;
                }

                XYZ a = lc.Curve.GetEndPoint(0), b = lc.Curve.GetEndPoint(1);

                // Duct banks use "Z justification = bottom": the location line IS the pipe bottom.
                // Conduits are round: the location line is the centre, so subtract the radius.
                double zOffset = 0;
                if (cat == BuiltInCategory.OST_Conduit)
                {
                    var d = e.get_Parameter(BuiltInParameter.RBS_CONDUIT_OUTER_DIAM_PARAM);
                    if (d is null || d.AsDouble() <= 0) d = e.get_Parameter(BuiltInParameter.RBS_CONDUIT_DIAMETER_PARAM);
                    zOffset = d is null ? 0 : -ToMm(d.AsDouble()) / 2.0;
                }

                list.Add(new PipeInfo(e.Id.Value, system.Value,
                    ToMm(a.X), ToMm(a.Y), ToMm(a.Z) + zOffset,
                    ToMm(b.X), ToMm(b.Y), ToMm(b.Z) + zOffset));
            }
        }
        foreach (var kv in unmatched.OrderByDescending(kv => kv.Value))
            UnmatchedPipes.Add($"{kv.Key} : {kv.Value} 根");
        return list;
    }

    // ---- step 2: collect wells ---------------------------------------------------

    private static List<WellResult> CollectWells(Document doc, ICollection<ElementId>? onlyIds, out List<WellResult> temporary)
    {
        temporary = new List<WellResult>();
        var wells = new List<WellResult>();
        var onlySet = onlyIds is { Count: > 0 } ? new HashSet<ElementId>(onlyIds) : null;

        foreach (var e in new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType())
        {
            if (e is not FamilyInstance fi) continue;
            if (onlySet != null && !onlySet.Contains(fi.Id)) continue;

            string family = fi.Symbol.FamilyName;
            var system = GetWellSystem(family);
            if (system is null) continue;

            var bb = fi.get_BoundingBox(null);
            var offset = DoubleParam(fi, ParamOffsetA) ?? DoubleParam(fi, ParamOffsetB);
            var w = new WellResult
            {
                Id = fi.Id.Value,
                Mark = fi.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.AsString() ?? "",
                Family = family,
                System = system.Value,
            };

            if (IsTemporary(fi)) { w.Status = WellStatus.Temporary; w.Note = "注释含“临时”"; temporary.Add(w); continue; }
            if (bb is null || offset is null) { w.Status = WellStatus.NoParameter; w.Note = "没有包围盒或偏移参数"; temporary.Add(w); continue; }

            w.OffsetMm = ToMm(offset.AsDouble());
            w.TopMm = ToMm(bb.Max.Z);
            w.RefMm = w.TopMm - w.OffsetMm;
            w.MinX = ToMm(bb.Min.X); w.MinY = ToMm(bb.Min.Y); w.MaxX = ToMm(bb.Max.X); w.MaxY = ToMm(bb.Max.Y);
            w.CurBottomMm = DoubleParam(fi, ParamBottom) is { } pb ? ToMm(pb.AsDouble()) : null;
            w.CurInnerBottomMm = DoubleParam(fi, ParamInnerBottom) is { } pi ? ToMm(pi.AsDouble()) : null;
            var pd = DoubleParam(fi, ParamDepth) ?? DoubleParam(fi, ParamDepth2);
            w.CurDepthMm = pd is null ? null : ToMm(pd.AsDouble());
            wells.Add(w);
        }
        return wells;
    }

    // ---- step 3: which pipes reach which well ------------------------------------

    private static void MatchPipesToWells(List<WellResult> wells, List<PipeInfo> pipes)
    {
        var bySystem = wells.GroupBy(w => w.System).ToDictionary(g => g.Key, g => g.ToList());

        foreach (var p in pipes)
        {
            if (!bySystem.TryGetValue(p.System, out var candidates)) continue;
            var touched = new HashSet<long>();

            // (a) the pipe touches / crosses the well footprint
            foreach (var w in candidates)
            {
                if (!Clip(p, w.MinX - Rules.TightMm, w.MinY - Rules.TightMm, w.MaxX + Rules.TightMm, w.MaxY + Rules.TightMm, out double t0, out double t1))
                    continue;
                // The connection point is the bank END that lies in the well, with its exact elevation (not a point
                // interpolated along the clipped part). A bank with both ends in the well, or one that simply passes
                // through it, has no single connection point: keep it, but flag it for a person to check.
                bool in0 = InFootprint(p.X0, p.Y0, w), in1 = InFootprint(p.X1, p.Y1, w);
                if (in0 != in1)
                    AddHit(w, new PipeHit(p.Id, in0 ? p.Z0 : p.Z1, false, 0));
                else
                    AddHit(w, new PipeHit(p.Id, Math.Min(p.Z0, p.Z1), false, 0, Ambiguous: true));
                touched.Add(w.Id);
            }

            // (b) the pipe END stops short of the well: give it to the NEAREST well of the same system
            foreach (var (x, y, z) in new[] { (p.X0, p.Y0, p.Z0), (p.X1, p.Y1, p.Z1) })
            {
                WellResult? best = null;
                double bestDist = double.MaxValue;
                foreach (var w in candidates)
                {
                    double d = DistanceToBox(x, y, w);
                    if (d < bestDist) { bestDist = d; best = w; }
                }
                if (best != null && bestDist <= Rules.MaxGapMm && bestDist > Rules.TightMm && !touched.Contains(best.Id))
                    AddHit(best, new PipeHit(p.Id, z, true, bestDist));
            }
        }
    }

    private static void AddHit(WellResult w, PipeHit hit)
    {
        // a pipe whose bottom is above the well top is not buried in this well -> not a real connection
        if (hit.BottomZ >= w.TopMm) { IgnoredAbovePipes++; return; }
        w.Hits.Add(hit);
    }

    private static bool InFootprint(double x, double y, WellResult w) =>
        x >= w.MinX - Rules.TightMm && x <= w.MaxX + Rules.TightMm && y >= w.MinY - Rules.TightMm && y <= w.MaxY + Rules.TightMm;

    private static double DistanceToBox(double x, double y, WellResult w)
    {
        double dx = Math.Max(Math.Max(w.MinX - x, 0), x - w.MaxX);
        double dy = Math.Max(Math.Max(w.MinY - y, 0), y - w.MaxY);
        return Math.Sqrt(dx * dx + dy * dy);
    }

    /// <summary>Liang-Barsky: part of the segment (t0..t1) that lies inside the rectangle, in plan view.</summary>
    private static bool Clip(PipeInfo p, double xmin, double ymin, double xmax, double ymax, out double t0, out double t1)
    {
        t0 = 0; t1 = 1;
        double dx = p.X1 - p.X0, dy = p.Y1 - p.Y0;
        var checks = new[] { (-dx, p.X0 - xmin), (dx, xmax - p.X0), (-dy, p.Y0 - ymin), (dy, ymax - p.Y0) };
        foreach (var (pp, q) in checks)
        {
            if (Math.Abs(pp) < 1e-9) { if (q < 0) return false; continue; }
            double t = q / pp;
            if (pp < 0) { if (t > t1) return false; if (t > t0) t0 = t; }
            else { if (t < t0) return false; if (t < t1) t1 = t; }
        }
        return true;
    }

    // ---- step 4: apply the rule --------------------------------------------------

    private static void Evaluate(WellResult w)
    {
        if (w.Hits.Count == 0) { w.Status = WellStatus.NoPipe; w.Note = $"{Rules.MaxGapMm:0} mm 内没有同专业排管"; return; }
        if (w.CurDepthMm is null) { w.Status = WellStatus.NoParameter; w.Note = "族里没有“井内深度/深度”参数"; return; }

        // the offset must be measured from a sensible height; a big gap means the family or its levels are wrong
        if (Math.Abs(w.RefMm) > Rules.MaxRefGapMm)
        {
            w.Status = WellStatus.Review;
            w.Note = $"井顶与偏移不一致（差 {w.RefMm:0} mm），请人工核对";
            return;
        }

        var bottoms = w.Hits.Select(h => h.BottomZ).OrderBy(z => z).ToList();
        w.LowestPipeMm = Math.Round(bottoms[0]);
        if (w.Hits.Any(h => h.Ambiguous))
        {
            w.Status = WellStatus.Review;
            w.Note = "有排管穿过井或两端都在井内，接入点不确定，请人工核对";
            return;
        }
        // rule: 井底 = 最低接入管底 - 300 (same absolute frame as the pipes and as the values typed by hand)
        //       井深 = 偏移 - 井底
        w.NewBottomMm = Math.Round(bottoms[0] - Rules.ClearanceMm);
        w.NewDepthMm = Math.Round(w.OffsetMm - w.NewBottomMm);

        bool depthWrong = Math.Abs(w.NewDepthMm - w.CurDepthMm.Value) > Rules.ToleranceMm;
        bool bottomStale = w.CurBottomMm is { } cb && Math.Abs(w.NewBottomMm - cb) > Rules.ToleranceMm;

        if (!depthWrong && !bottomStale) { w.Status = WellStatus.Ok; return; }

        // numbers that look odd: do not auto-write, let the user decide
        var warnings = new List<string>();
        if (w.NewDepthMm > Rules.MaxDepthMm) warnings.Add($"井深 {w.NewDepthMm:0} 超过 {Rules.MaxDepthMm:0}");
        if (w.NewDepthMm < Rules.MinDepthMm) warnings.Add($"井深 {w.NewDepthMm:0} 小于 {Rules.MinDepthMm:0}");
        if (bottoms.Count > 1 && bottoms[1] - bottoms[0] > Rules.MaxSpreadMm) warnings.Add($"最低管比次低管还低 {bottoms[1] - bottoms[0]:0}，疑似异常管");
        if (w.Hits.Any(h => h.IsGap)) w.Note = "含有未贴井壁的排管(间距" + string.Join("/", w.Hits.Where(h => h.IsGap).Select(h => h.GapMm.ToString("0"))) + ")";

        if (warnings.Count > 0 && depthWrong)
        {
            w.Status = WellStatus.Review;
            w.Note = string.Join("；", warnings) + (w.Note.Length > 0 ? "；" + w.Note : "");
            return;
        }
        w.Status = depthWrong ? WellStatus.Adjust : WellStatus.SyncOnly;
    }

    // ---- step 5: write -----------------------------------------------------------

    /// <summary>
    /// Writes the bottom (and optionally the depth) in millimetres. Call inside a Transaction.
    /// The values come from the review window, so the user's manual overrides are honoured.
    /// Returns an error text, or null if fine.
    /// </summary>
    public static string? Apply(Document doc, WellResult w, double bottomMm, double? depthMm)
    {
        if (doc.GetElement(new ElementId(w.Id)) is not Element e) return "找不到图元";

        if (doc.IsWorkshared &&
            WorksharingUtils.GetCheckoutStatus(doc, e.Id) == CheckoutStatus.OwnedByOtherUser)
            return "被他人借用，已跳过";

        var errors = new List<string>();
        if (depthMm is { } depth)
        {
            // "井内深度" drives the well geometry; "深度" exists only in some families (e.g. LV)
            bool wrote = SetMm(e, ParamDepth, depth, errors);
            wrote |= SetMm(e, ParamDepth2, depth, errors);
            if (!wrote) errors.Add("没有可写的深度参数");
        }
        SetMm(e, ParamBottom, bottomMm, errors);
        // 井内底标高 is a hand-typed annotation value; only keep it in step if the user is already using it
        if (w.CurInnerBottomMm is { } ib && Math.Abs(ib) > 1)
            SetMm(e, ParamInnerBottom, bottomMm, errors);

        return errors.Count == 0 ? null : string.Join("；", errors);
    }

    /// <summary>Returns true if the parameter exists and was written.</summary>
    private static bool SetMm(Element e, string name, double valueMm, List<string> errors)
    {
        var p = DoubleParam(e, name);
        if (p is null) return false;
        if (p.IsReadOnly) { errors.Add($"“{name}”只读"); return false; }
        p.Set(ToFeet(valueMm));
        return true;
    }
}
