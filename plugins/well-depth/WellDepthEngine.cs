using Autodesk.Revit.DB;

namespace KaifaTools.WellDepth;

// ---------------------------------------------------------------------------
// Rule (set by the user):
//   well bottom elevation = lowest connected duct-bank bottom - 300 mm
//   well depth            = well top offset (相对标高的偏移) - well bottom elevation
// Storm wells, sewage wells and valve wells are never touched: only the families
// listed in GetWellSystem() are processed (a whitelist is safer than a blacklist).
// ---------------------------------------------------------------------------

/// <summary>Tunable numbers. Change them here, rebuild, done.</summary>
public static class DepthSettings
{
    public const double ClearanceMm = 300;    // well bottom sits this far below the lowest pipe
    public const double ToleranceMm = 50;     // a well within this error is treated as "already correct"
    public const double TightMm = 50;         // pipe must touch the well footprint (+ this margin) to count as "connected"
    public const double MaxGapMm = 1000;      // a pipe END that stops short of a well still counts if closer than this
    public const double MaxDepthMm = 6000;    // deeper than this -> needs a human review, not auto-written
    public const double MinDepthMm = 500;     // shallower than this -> needs review
    public const bool ShowPlanFirst = true;      // true = show rules + scope and ask before writing
    public const double MaxSpreadMm = 2000;   // lowest pipe is this much below the next one -> suspicious, review
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

/// <summary>One duct bank (structural framing) or conduit, in millimetres.</summary>
internal sealed record PipeInfo(long Id, WellSystem System, double X0, double Y0, double Z0, double X1, double Y1, double Z1);

internal sealed record PipeHit(long PipeId, double BottomZ, bool IsGap, double GapMm);

/// <summary>Everything we know about one well: current values, matched pipes, proposed values.</summary>
public sealed class WellResult
{
    public long Id;
    public string Mark = "";
    public string Family = "";
    public WellSystem System;
    public double OffsetMm;
    public double LevelMm;
    public double? CurBottomMm;      // parameter "井底标高"
    public double? CurDepthMm;       // parameter "井内深度" (or "深度")
    public double? CurInnerBottomMm; // parameter "井内底标高" (manual annotation value)
    public double MinX, MinY, MaxX, MaxY;
    internal List<PipeHit> Hits = new();

    public double? LowestPipeMm;
    public double NewBottomMm;
    public double NewDepthMm;
    public WellStatus Status;
    public string Note = "";
    public int PipeCount => Hits.Count;
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

    private static double ToMm(double feet) => UnitUtils.ConvertFromInternalUnits(feet, UnitTypeId.Millimeters);
    public static double ToFeet(double mm) => UnitUtils.ConvertToInternalUnits(mm, UnitTypeId.Millimeters);

    // ---- which families are "wells we may touch" ---------------------------------

    public static WellSystem? GetWellSystem(string family)
    {
        // explicit deny list, in case a family name ever contains a whitelisted word
        string[] deny = { "雨水", "排水", "污水", "阀门", "井盖", "mold_" };
        if (deny.Any(family.Contains)) return null;

        if (family == "LV-低压拉线井") return WellSystem.Lv;
        if (family.StartsWith("ELE-EM")) return WellSystem.Em;
        if (family.StartsWith("ELE-TELCO") || family == "TELCO" || family.StartsWith("TELCO-")) return WellSystem.Telco;
        if (family == "ELV PIT" || family == "ELV-MANHOLE") return WellSystem.Elv;
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

    // Which system does a pipe belong to? A well only accepts pipes of its own system.
    private static WellSystem? GetPipeSystem(string family, string typeName)
    {
        string both = family + "|" + typeName;
        if (both.Contains("雨水") || both.Contains("排水")) return null;
        if (both.Contains("TELCO")) return WellSystem.Telco;
        if (both.Contains("11KV") || both.Contains("275KV")) return WellSystem.Em;
        if (family.StartsWith("ELV")) return WellSystem.Elv;
        // family decides for LV (one LV family has a type that is misnamed "ELV-...")
        if (family.StartsWith("LV") || typeName.Contains("ELE-LV")) return WellSystem.Lv;
        return null;
    }

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
        foreach (var cat in new[] { BuiltInCategory.OST_StructuralFraming, BuiltInCategory.OST_Conduit })
        {
            foreach (var e in new FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType())
            {
                if (e.Location is not LocationCurve lc) continue;
                string family = (doc.GetElement(e.GetTypeId()) as ElementType)?.FamilyName ?? "";
                var system = GetPipeSystem(family, e.Name);
                if (system is null || IsTemporary(e)) continue;

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
                LevelMm = doc.GetElement(fi.LevelId) is Level lvl ? ToMm(lvl.Elevation) : 0,
            };

            if (IsTemporary(fi)) { w.Status = WellStatus.Temporary; w.Note = "注释含“临时”"; temporary.Add(w); continue; }
            if (bb is null || offset is null) { w.Status = WellStatus.NoParameter; w.Note = "没有包围盒或偏移参数"; temporary.Add(w); continue; }

            w.OffsetMm = ToMm(offset.AsDouble());
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
                if (!Clip(p, w.MinX - DepthSettings.TightMm, w.MinY - DepthSettings.TightMm, w.MaxX + DepthSettings.TightMm, w.MaxY + DepthSettings.TightMm, out double t0, out double t1))
                    continue;
                double za = p.Z0 + (p.Z1 - p.Z0) * t0, zb = p.Z0 + (p.Z1 - p.Z0) * t1;
                AddHit(w, new PipeHit(p.Id, Math.Min(za, zb), false, 0));
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
                if (best != null && bestDist <= DepthSettings.MaxGapMm && bestDist > DepthSettings.TightMm && !touched.Contains(best.Id))
                    AddHit(best, new PipeHit(p.Id, z, true, bestDist));
            }
        }
    }

    private static void AddHit(WellResult w, PipeHit hit)
    {
        // ignore pipes that are not buried below the well top (bad data, not a real connection)
        if (hit.BottomZ >= w.LevelMm + w.OffsetMm) return;
        w.Hits.Add(hit);
    }

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
        if (w.Hits.Count == 0) { w.Status = WellStatus.NoPipe; w.Note = $"{DepthSettings.MaxGapMm:0} mm 内没有同专业排管"; return; }
        if (w.CurDepthMm is null) { w.Status = WellStatus.NoParameter; w.Note = "族里没有“井内深度/深度”参数"; return; }

        var bottoms = w.Hits.Select(h => h.BottomZ).OrderBy(z => z).ToList();
        w.LowestPipeMm = bottoms[0];
        w.NewBottomMm = Math.Round(bottoms[0] - DepthSettings.ClearanceMm - w.LevelMm);
        w.NewDepthMm = Math.Round(w.OffsetMm - w.NewBottomMm);

        bool depthWrong = Math.Abs(w.NewDepthMm - w.CurDepthMm.Value) > DepthSettings.ToleranceMm;
        bool bottomStale = w.CurBottomMm is { } cb && Math.Abs(w.NewBottomMm - cb) > DepthSettings.ToleranceMm;

        if (!depthWrong && !bottomStale) { w.Status = WellStatus.Ok; return; }

        // numbers that look odd: do not auto-write, let the user decide
        var warnings = new List<string>();
        if (w.NewDepthMm > DepthSettings.MaxDepthMm) warnings.Add($"井深 {w.NewDepthMm:0} 超过 {DepthSettings.MaxDepthMm:0}");
        if (w.NewDepthMm < DepthSettings.MinDepthMm) warnings.Add($"井深 {w.NewDepthMm:0} 小于 {DepthSettings.MinDepthMm:0}");
        if (bottoms.Count > 1 && bottoms[1] - bottoms[0] > DepthSettings.MaxSpreadMm) warnings.Add($"最低管比次低管还低 {bottoms[1] - bottoms[0]:0}，疑似异常管");
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

    /// <summary>Writes the new values. Call inside a Transaction. Returns an error text, or null if fine.</summary>
    public static string? Apply(Document doc, WellResult w)
    {
        if (doc.GetElement(new ElementId(w.Id)) is not Element e) return "找不到图元";

        if (doc.IsWorkshared &&
            WorksharingUtils.GetCheckoutStatus(doc, e.Id) == CheckoutStatus.OwnedByOtherUser)
            return "被他人借用，已跳过";

        var errors = new List<string>();
        if (w.Status == WellStatus.Adjust)
        {
            // "井内深度" drives the well geometry; "深度" exists only in some families (e.g. LV)
            bool wrote = SetMm(e, ParamDepth, w.NewDepthMm, errors);
            wrote |= SetMm(e, ParamDepth2, w.NewDepthMm, errors);
            if (!wrote) errors.Add("没有可写的深度参数");
        }
        SetMm(e, ParamBottom, w.NewBottomMm, errors);
        // 井内底标高 is a hand-typed annotation value; only keep it in step if the user is already using it
        if (w.CurInnerBottomMm is { } ib && Math.Abs(ib) > 1)
            SetMm(e, ParamInnerBottom, w.NewBottomMm, errors);

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
