using System.Text.Json;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Electrical;
using Autodesk.Revit.DB.Mechanical;
using Autodesk.Revit.DB.Plumbing;
using Autodesk.Revit.UI;

namespace KaifaTools.DuctRoute;

public sealed class JogOptions
{
    public string Mode { get; set; } = "vertical";    // vertical = over/under the obstacle, plane = to the side (changes the plan position)
    public string Direction { get; set; } = "auto";   // auto = the shorter way; up/down (vertical) or left/right (plane)
    public double ClearanceM { get; set; } = 0.10;    // clear gap to the obstacle (outer surface)
    public double AngleDeg { get; set; } = 45;        // bend angle of the two slopes
    public double MarginM { get; set; } = 0.30;
    public double MinOffsetM { get; set; } = 0.60;
    public double? FixedOffsetM { get; set; }         // bundle: the common signed shift (plane) or lift (vertical)    // smallest lift / shift (the slopes must be long enough for the elbow fittings)       // extra length kept on each side of the clash window
}

public sealed record JogPlan(long Id, string Category, XYZ P0, XYZ P1, XYZ Offset, double TaFt, double TbFt, double RunFt, List<(long id, string cat)> Obstacles, string Summary);

/// <summary>
/// Tool "绕行 (Jog)" for pipes, ducts, conduits and cable trays (the straight run is cut, the middle part goes over or under the obstacle on two slopes,
/// the four joints get elbow fittings, the far end is reconnected). Everything happens in one transaction group; any failure rolls it all back.
/// </summary>
public static class Jogger
{
    const double Ft = 0.3048;
    static readonly BuiltInCategory[] Hits =
    {
        BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_DuctCurves, BuiltInCategory.OST_CableTray, BuiltInCategory.OST_Conduit,
        BuiltInCategory.OST_StructuralFraming, BuiltInCategory.OST_StructuralColumns, BuiltInCategory.OST_Floors, BuiltInCategory.OST_Walls, BuiltInCategory.OST_GenericModel,
        BuiltInCategory.OST_MechanicalEquipment, BuiltInCategory.OST_ElectricalEquipment
    };

    /// <summary>Half of the cross-section height (m·ft): pipe/conduit diameter, duct or tray height.</summary>
    static double HalfHeightFt(MEPCurve m)
    {
        double? v = m switch
        {
            Pipe p => p.get_Parameter(BuiltInParameter.RBS_PIPE_OUTER_DIAMETER)?.AsDouble() ?? p.Diameter,
            Conduit c => c.get_Parameter(BuiltInParameter.RBS_CONDUIT_OUTER_DIAM_PARAM)?.AsDouble() ?? c.Diameter,
            Duct d => d.get_Parameter(BuiltInParameter.RBS_CURVE_HEIGHT_PARAM)?.AsDouble() is { } h && h > 0 ? h : d.Diameter,
            CableTray t => t.get_Parameter(BuiltInParameter.RBS_CABLETRAY_HEIGHT_PARAM)?.AsDouble(),
            _ => null
        };
        return (v ?? 0.3) / 2;
    }

    static double HalfWidthFt(MEPCurve m)
    {
        double? v = m switch
        {
            Pipe p => p.get_Parameter(BuiltInParameter.RBS_PIPE_OUTER_DIAMETER)?.AsDouble() ?? p.Diameter,
            Conduit c => c.get_Parameter(BuiltInParameter.RBS_CONDUIT_OUTER_DIAM_PARAM)?.AsDouble() ?? c.Diameter,
            Duct d => d.get_Parameter(BuiltInParameter.RBS_CURVE_WIDTH_PARAM)?.AsDouble() is { } w && w > 0 ? w : d.Diameter,
            CableTray t => t.get_Parameter(BuiltInParameter.RBS_CABLETRAY_WIDTH_PARAM)?.AsDouble(),
            _ => null
        };
        return (v ?? 0.3) / 2;
    }

    public static JogPlan? Plan(Document doc, MEPCurve m, JogOptions o, out string error)
    {
        error = ""; if (m.Location is not LocationCurve lc || lc.Curve is not Line ln) { error = "只支持直线段"; return null; }
        var p0 = ln.GetEndPoint(0); var p1 = ln.GetEndPoint(1); double L = ln.Length; var u = (p1 - p0).Normalize();
        if (Math.Abs(u.Z) > 0.02) { error = "这一段本身有坡度，先不支持"; return null; }
        var nrm = new XYZ(-u.Y, u.X, 0).Normalize(); bool plane = o.Mode == "plane";
        double r = HalfHeightFt(m), z0 = p0.Z, clr = o.ClearanceM / Ft;
        var hits = new FilteredElementCollector(doc).WherePasses(new ElementMulticategoryFilter(Hits)).WherePasses(new ElementIntersectsElementFilter(m)).ToElements()
            .Where(x => x.Id != m.Id && !SameRun(m, x)).ToList();
        if (hits.Count == 0) { error = "这一段没有碰撞"; return null; }
        double tmin = double.MaxValue, tmax = double.MinValue, top = double.MinValue, bot = double.MaxValue, lmax = double.MinValue, lmin = double.MaxValue; var obs = new List<(long, string)>();
        foreach (var h in hits)
        {
            var bb = h.get_BoundingBox(null); if (bb is null) continue;
            double a = double.MaxValue, b = double.MinValue;
            foreach (var x in new[] { bb.Min.X, bb.Max.X }) foreach (var y in new[] { bb.Min.Y, bb.Max.Y })
                { var q = new XYZ(x, y, z0) - p0; double t = q.DotProduct(u); a = Math.Min(a, t); b = Math.Max(b, t); double l = q.DotProduct(nrm); lmax = Math.Max(lmax, l); lmin = Math.Min(lmin, l); }
            tmin = Math.Min(tmin, Math.Max(a, 0)); tmax = Math.Max(tmax, Math.Min(b, L)); top = Math.Max(top, bb.Max.Z); bot = Math.Min(bot, bb.Min.Z); obs.Add((h.Id.Value, h.Category?.Name ?? ""));
        }
        double up = top + clr + r - z0, down = bot - clr - r - z0;        // down is negative
        double dz = o.Direction == "up" ? up : o.Direction == "down" ? down : (Math.Abs(down) < up ? down : up);
        double half = HalfWidthFt(m), left = lmax + clr + half, right = lmin - clr - half;   // right is negative
        double dl = o.Direction == "left" ? left : o.Direction == "right" ? right : (Math.Abs(right) < left ? right : left);
        if (o.FixedOffsetM is { } fx) { if (plane) dl = fx / Ft; else dz = fx / Ft; }
        double minD = o.MinOffsetM / Ft;                                   // very short slopes do not fit an elbow fitting: Revit refuses them
        if (plane) dl = (dl < 0 ? -1 : 1) * Math.Max(Math.Abs(dl), minD); else dz = (dz < 0 ? -1 : 1) * Math.Max(Math.Abs(dz), minD);
        var off = plane ? nrm * dl : new XYZ(0, 0, dz); double dist = plane ? Math.Abs(dl) : Math.Abs(dz);
        double run = dist / Math.Tan(o.AngleDeg * Math.PI / 180), margin = o.MarginM / Ft;
        double ta = tmin - margin, tb = tmax + margin;
        if (ta - run < 0.5 || tb + run > L - 0.5) { error = $"这一段太短，放不下绕行（需要在碰撞两侧各留 {(run + margin) * Ft:0.0} m）"; return null; }
        string way = plane ? (dl > 0 ? "左边" : "右边") : (dz > 0 ? "上面" : "下面");
        string sum = $"{m.Category?.Name} {m.Id}：碰撞 {obs.Count} 个，从{way}绕，{(plane ? "平移" : "抬/压")} {dist * Ft:0.00} m，窗口 {(tb - ta) * Ft:0.0} m，斜段各 {run * Ft:0.0} m" + (plane ? "（会改平面位置）" : "");
        return new JogPlan(m.Id.Value, m.Category?.Name ?? "", p0, p1, off, ta, tb, run, obs, sum);
    }

    /// <summary>Several parallel straight runs (a bundle) get ONE common shift so that they stay parallel and never cross each other.</summary>
    public static List<JogPlan>? PlanBundle(Document doc, IList<MEPCurve> ms, JogOptions o, out string error)
    {
        error = ""; if (ms.Count < 2) { error = "至少选 2 根"; return null; }
        var lines = new List<(MEPCurve m, Line ln)>();
        foreach (var m in ms) { if (m.Location is not LocationCurve lc || lc.Curve is not Line ln) { error = $"{m.Id} 不是直线段"; return null; } lines.Add((m, ln)); }
        var u0 = (lines[0].ln.GetEndPoint(1) - lines[0].ln.GetEndPoint(0)).Normalize();
        foreach (var (m, ln) in lines)
        {
            var u = (ln.GetEndPoint(1) - ln.GetEndPoint(0)).Normalize();
            if (Math.Abs(u.CrossProduct(u0).GetLength()) > 0.02 || Math.Abs(u.Z) > 0.02) { error = $"{m.Id} 和第一根不平行或有坡度，不能当一束"; return null; }
        }
        var nrm = new XYZ(-u0.Y, u0.X, 0).Normalize(); var ids = ms.Select(x => x.Id).ToHashSet(); double clr = o.ClearanceM / Ft;
        var hits = new Dictionary<ElementId, Element>();
        foreach (var m in ms)
            foreach (var x in new FilteredElementCollector(doc).WherePasses(new ElementMulticategoryFilter(Hits)).WherePasses(new ElementIntersectsElementFilter(m)).ToElements())
                if (!ids.Contains(x.Id) && !ms.Any(mm => SameRun(mm, x))) hits[x.Id] = x;
        if (hits.Count == 0) { error = "这一束没有碰撞"; return null; }
        var org = lines[0].ln.GetEndPoint(0); double lmax = double.MinValue, lmin = double.MaxValue, top = double.MinValue, bot = double.MaxValue;
        foreach (var h in hits.Values)
        {
            var bb = h.get_BoundingBox(null); if (bb is null) continue;
            foreach (var x in new[] { bb.Min.X, bb.Max.X }) foreach (var y in new[] { bb.Min.Y, bb.Max.Y }) { double l = (new XYZ(x, y, org.Z) - org).DotProduct(nrm); lmax = Math.Max(lmax, l); lmin = Math.Min(lmin, l); }
            top = Math.Max(top, bb.Max.Z); bot = Math.Min(bot, bb.Min.Z);
        }
        bool plane = o.Mode == "plane"; double d = 0;
        if (plane)
        {
            double left = double.MinValue, right = double.MaxValue;
            foreach (var (m, ln) in lines)
            {
                double l = (ln.GetEndPoint(0) - org).DotProduct(nrm), half = HalfWidthFt(m);
                left = Math.Max(left, lmax - (l - half) + clr); right = Math.Min(right, lmin - (l + half) - clr);
            }
            d = o.Direction == "left" ? left : o.Direction == "right" ? right : (Math.Abs(right) < left ? right : left);
        }
        else
        {
            double up = double.MinValue, down = double.MaxValue;
            foreach (var (m, ln) in lines) { double z = ln.GetEndPoint(0).Z, r = HalfHeightFt(m); up = Math.Max(up, top + clr + r - z); down = Math.Min(down, bot - clr - r - z); }
            d = o.Direction == "up" ? up : o.Direction == "down" ? down : (Math.Abs(down) < up ? down : up);
        }
        d = (d < 0 ? -1 : 1) * Math.Max(Math.Abs(d), o.MinOffsetM / Ft);
        var plans = new List<JogPlan>();
        foreach (var (m, ln) in lines)
        {
            var po = new JogOptions { Mode = o.Mode, Direction = o.Direction, ClearanceM = o.ClearanceM, AngleDeg = o.AngleDeg, MarginM = o.MarginM, MinOffsetM = o.MinOffsetM, FixedOffsetM = d * Ft };
            var p = Plan(doc, m, po, out var err);
            if (p is null) { error = $"{m.Id}：{err}"; return null; }
            plans.Add(p);
        }
        // one common window so that the slopes of neighbouring runs stay side by side
        return plans;
    }

    /// <summary>
    /// Several runs at once: each gets its own plan first; runs that are parallel, close to each other and whose clash windows overlap are
    /// merged into one bundle (common shift, stay parallel); the others keep their own plan. Returns the plans in apply order, plus a note per skipped run.
    /// </summary>
    public static List<JogPlan> PlanAuto(Document doc, IList<MEPCurve> ms, JogOptions o, out List<string> notes)
    {
        notes = new(); var singles = new List<(MEPCurve m, JogPlan p)>();
        foreach (var m in ms)
        {
            var p = Plan(doc, m, o, out var err);
            if (p is null) notes.Add($"{m.Id}：{err}"); else singles.Add((m, p));
        }
        var par = Enumerable.Range(0, singles.Count).ToArray();
        int Find(int x) { while (par[x] != x) { par[x] = par[par[x]]; x = par[x]; } return x; }
        for (int i = 0; i < singles.Count; i++)
            for (int k = i + 1; k < singles.Count; k++)
            {
                var a = singles[i].p; var b = singles[k].p;
                var ua = (a.P1 - a.P0).Normalize(); var ub = (b.P1 - b.P0).Normalize();
                if (ua.CrossProduct(ub).GetLength() > 0.02) continue;
                var n = new XYZ(-ua.Y, ua.X, 0).Normalize(); double lat = Math.Abs((b.P0 - a.P0).DotProduct(n));
                if (lat > 6 / Ft) continue;
                double sa0 = a.TaFt - a.RunFt, sa1 = a.TbFt + a.RunFt, off = (b.P0 - a.P0).DotProduct(ua) * Math.Sign(ua.DotProduct(ub));
                double sb0 = b.TaFt - b.RunFt + off, sb1 = b.TbFt + b.RunFt + off;
                if (Math.Max(sa0, sb0) <= Math.Min(sa1, sb1)) par[Find(i)] = Find(k);
            }
        var plans = new List<JogPlan>();
        foreach (var g in singles.Select((s, i) => (s, i)).GroupBy(x => Find(x.i)))
        {
            var members = g.Select(x => x.s.m).ToList();
            if (members.Count == 1) { plans.Add(g.First().s.p); continue; }
            var bundle = PlanBundle(doc, members, o, out var err);
            if (bundle is null) { notes.Add($"{string.Join("、", members.Select(x => x.Id))} 没法并成一束（{err}），各自绕行"); plans.AddRange(g.Select(x => x.s.p)); }
            else { notes.Add($"{members.Count} 根并成一束一起绕行：{string.Join("、", members.Select(x => x.Id))}"); plans.AddRange(bundle); }
        }
        return plans;
    }

    // ------------------------------------------------------------------------------------------------------------------
    // Corridor coordination: several runs at once. Every unit (a run, or a bundle of parallel runs) gets candidates
    // (up, down, left, right); candidates whose plan path crosses a path that another unit keeps or takes are refused;
    // the cheapest consistent choice wins. Staying in plan (up / down) is preferred, moving in plan is the fallback.
    // ------------------------------------------------------------------------------------------------------------------
    sealed record Cand(string Way, List<JogPlan> Plans, double Cost, List<(XYZ a, XYZ b)> PlanPath, double HalfWidthFt, List<(double r, double hw)> Dims);

    static List<(XYZ a, XYZ b)> PathOf(JogPlan p)
    {
        var u = (p.P1 - p.P0).Normalize(); var xy = new XYZ(p.Offset.X, p.Offset.Y, 0);
        var pa = p.P0 + u * (p.TaFt - p.RunFt); var pa2 = p.P0 + u * p.TaFt + xy; var pb2 = p.P0 + u * p.TbFt + xy; var pb = p.P0 + u * (p.TbFt + p.RunFt);
        var pts = new[] { p.P0, pa, pa2, pb2, pb, p.P1 }; var l = new List<(XYZ, XYZ)>();
        for (int i = 0; i + 1 < pts.Length; i++) l.Add((new XYZ(pts[i].X, pts[i].Y, 0), new XYZ(pts[i + 1].X, pts[i + 1].Y, 0)));
        return l;
    }

    static double SegDist2D(XYZ a, XYZ b, XYZ c, XYZ d)
    {
        double Pt(XYZ p, XYZ s, XYZ e) { var v = e - s; double L2 = v.X * v.X + v.Y * v.Y; double t = L2 < 1e-12 ? 0 : Math.Max(0, Math.Min(1, ((p.X - s.X) * v.X + (p.Y - s.Y) * v.Y) / L2)); return Math.Sqrt(Math.Pow(p.X - s.X - t * v.X, 2) + Math.Pow(p.Y - s.Y - t * v.Y, 2)); }
        bool Cross(XYZ p1, XYZ p2, XYZ p3, XYZ p4)
        {
            double d1 = (p4 - p3).X * (p1 - p3).Y - (p4 - p3).Y * (p1 - p3).X, d2 = (p4 - p3).X * (p2 - p3).Y - (p4 - p3).Y * (p2 - p3).X, d3 = (p2 - p1).X * (p3 - p1).Y - (p2 - p1).Y * (p3 - p1).X, d4 = (p2 - p1).X * (p4 - p1).Y - (p2 - p1).Y * (p4 - p1).X;
            return d1 * d2 < 0 && d3 * d4 < 0;
        }
        if (Cross(a, b, c, d)) return 0;
        return Math.Min(Math.Min(Pt(a, c, d), Pt(b, c, d)), Math.Min(Pt(c, a, b), Pt(d, a, b)));
    }

    /// <summary>Height of the run's axis above its own start level at a plan point: 0 outside the jog, the full lift on the flat part, linear on the slopes.</summary>
    static double LiftAt(JogPlan p, XYZ pt)
    {
        var u = (p.P1 - p.P0).Normalize(); double t = (new XYZ(pt.X, pt.Y, 0) - new XYZ(p.P0.X, p.P0.Y, 0)).DotProduct(u), dz = p.Offset.Z;
        if (Math.Abs(dz) < 1e-9) return 0;
        double a0 = p.TaFt - p.RunFt, a1 = p.TaFt, b0 = p.TbFt, b1 = p.TbFt + p.RunFt;
        if (t <= a0 || t >= b1) return 0; if (t < a1) return dz * (t - a0) / (a1 - a0); if (t <= b0) return dz; return dz * (b1 - t) / (b1 - b0);
    }

    static bool Intersect2D(XYZ a, XYZ b, XYZ c, XYZ d, out XYZ pt)
    {
        pt = a; double r = (b - a).X * (d - c).Y - (b - a).Y * (d - c).X; if (Math.Abs(r) < 1e-12) return false;
        double t = ((c - a).X * (d - c).Y - (c - a).Y * (d - c).X) / r, s = ((c - a).X * (b - a).Y - (c - a).Y * (b - a).X) / r;
        if (t < 0 || t > 1 || s < 0 || s > 1) return false; pt = a + (b - a) * t; return true;
    }

    /// <summary>Two choices conflict when, at a crossing (or where they run side by side), the two axes are closer in height than their sizes plus the clearance.</summary>
    static bool Conflict(Cand x, Cand y, double clrFt)
    {
        for (int i = 0; i < x.Plans.Count; i++)
            for (int k = 0; k < y.Plans.Count; k++)
            {
                var px = x.Plans[i]; var py = y.Plans[k]; var (rx, hx) = x.Dims[i]; var (ry, hy) = y.Dims[k];
                foreach (var (a, b) in PathOf(px)) foreach (var (c, d) in PathOf(py))
                {
                    if (Intersect2D(a, b, c, d, out var pt))
                    {
                        double zx = px.P0.Z + LiftAt(px, pt), zy = py.P0.Z + LiftAt(py, pt);
                        if (Math.Abs(zx - zy) < rx + ry + clrFt) return true;
                    }
                    else if (SegDist2D(a, b, c, d) < hx + hy + clrFt)
                    {
                        var ma = (a + b) / 2; var mc = (c + d) / 2;
                        if (Math.Abs(px.P0.Z + LiftAt(px, ma) - (py.P0.Z + LiftAt(py, mc))) < rx + ry + clrFt) return true;
                    }
                }
            }
        return false;
    }

    public static List<JogPlan> PlanCoordinated(Document doc, IList<MEPCurve> ms, JogOptions o, out List<string> notes)
    {
        notes = new(); var result = new List<JogPlan>();
        // units: bundles of parallel, close runs with overlapping windows; everything else alone
        var first = new List<(MEPCurve m, JogPlan p)>();
        foreach (var m in ms) { var p = Plan(doc, m, new JogOptions { Mode = "vertical", Direction = "auto", ClearanceM = o.ClearanceM, AngleDeg = o.AngleDeg, MarginM = o.MarginM, MinOffsetM = o.MinOffsetM }, out var err); if (p is null) { var q = Plan(doc, m, new JogOptions { Mode = "plane", ClearanceM = o.ClearanceM, AngleDeg = o.AngleDeg, MarginM = o.MarginM, MinOffsetM = o.MinOffsetM }, out var err2); if (q is null) { notes.Add($"{m.Id}：{err}"); continue; } p = q; } first.Add((m, p)); }
        var par = Enumerable.Range(0, first.Count).ToArray(); int Find(int x) { while (par[x] != x) { par[x] = par[par[x]]; x = par[x]; } return x; }
        for (int i = 0; i < first.Count; i++) for (int k = i + 1; k < first.Count; k++)
        {
            var a = first[i].p; var b = first[k].p; var ua = (a.P1 - a.P0).Normalize(); var ub = (b.P1 - b.P0).Normalize();
            if (ua.CrossProduct(ub).GetLength() > 0.02) continue;
            var nn = new XYZ(-ua.Y, ua.X, 0).Normalize(); if (Math.Abs((b.P0 - a.P0).DotProduct(nn)) > 6 / Ft) continue;
            double off = (b.P0 - a.P0).DotProduct(ua) * Math.Sign(ua.DotProduct(ub));
            if (Math.Max(a.TaFt - a.RunFt, b.TaFt - b.RunFt + off) <= Math.Min(a.TbFt + a.RunFt, b.TbFt + b.RunFt + off)) par[Find(i)] = Find(k);
        }
        var units = new List<List<MEPCurve>>();
        foreach (var g in first.Select((s, i) => (s, i)).GroupBy(x => Find(x.i))) units.Add(g.Select(x => x.s.m).ToList());
        double clr = o.ClearanceM / Ft;
        var cands = new List<List<Cand>>();
        foreach (var u in units)
        {
            var list = new List<Cand>();
            foreach (var (mode, dir) in new[] { ("vertical", "up"), ("vertical", "down"), ("plane", "left"), ("plane", "right") })
            {
                var oo = new JogOptions { Mode = mode, Direction = dir, ClearanceM = o.ClearanceM, AngleDeg = o.AngleDeg, MarginM = o.MarginM, MinOffsetM = o.MinOffsetM };
                List<JogPlan>? plans = u.Count == 1 ? (Plan(doc, u[0], oo, out _) is { } p ? new List<JogPlan> { p } : null) : PlanBundle(doc, u, oo, out _);
                if (plans is null) continue;
                double dist = plans.Max(p => p.Offset.GetLength()) * Ft, cost = mode == "vertical" ? dist : dist + 5;
                list.Add(new Cand($"{mode}-{dir}", plans, cost, plans.SelectMany(PathOf).ToList(), u.Max(HalfWidthFt), u.Select(mm => (HalfHeightFt(mm), HalfWidthFt(mm))).ToList()));
            }
            cands.Add(list);
        }
        // straight paths of the runs that stay as they are (selected but with no way out) also count as obstacles for the others
        int n = units.Count; var best = new int[n]; double bestCost = double.MaxValue; var cur = new int[n];
        void Dfs(int i, double cost)
        {
            if (cost >= bestCost) return;
            if (i == n) { bestCost = cost; Array.Copy(cur, best, n); return; }
            if (cands[i].Count == 0) { cur[i] = -1; Dfs(i + 1, cost + 1000); return; }
            foreach (var (c, k) in cands[i].Select((c, k) => (c, k)).OrderBy(x => x.c.Cost))
            {
                bool bad = false; for (int j = 0; j < i && !bad; j++) if (cur[j] >= 0) bad = Conflict(c, cands[j][cur[j]], clr);
                if (bad) continue; cur[i] = k; Dfs(i + 1, cost + c.Cost);
            }
            cur[i] = -1; Dfs(i + 1, cost + 1000);      // nothing consistent: leave this unit alone
        }
        if (n <= 10) Dfs(0, 0);
        else for (int i = 0; i < n; i++) { best[i] = cands[i].Count > 0 ? 0 : -1; }
        for (int i = 0; i < n; i++)
        {
            if (best[i] < 0 || cands[i].Count == 0) { notes.Add($"{string.Join("、", units[i].Select(x => x.Id))}：没有互不冲突的绕法，保持原样"); continue; }
            var c = cands[i][best[i]]; result.AddRange(c.Plans);
            string way = c.Way switch { "vertical-up" => "从上面", "vertical-down" => "从下面", "plane-left" => "从左边（改平面）", _ => "从右边（改平面）" };
            notes.Add($"{(units[i].Count > 1 ? $"{units[i].Count} 根并成一束：" : "")}{string.Join("、", units[i].Select(x => x.Id))} → {way}，{c.Cost:0.0} 分");
        }
        return result;
    }

    static bool SameRun(Element a, Element b)
    {
        try
        {
            if (a is MEPCurve ma && b is MEPCurve mb)
                foreach (Connector c in ma.ConnectorManager.Connectors) foreach (Connector d in mb.ConnectorManager.Connectors) if (c.Origin.DistanceTo(d.Origin) < 0.02) return true;
        }
        catch { }
        return false;
    }

    static MEPCurve Create(Document doc, MEPCurve src, XYZ a, XYZ b)
    {
        var lvl = src.ReferenceLevel.Id; var typeId = src.GetTypeId(); MEPCurve n;
        switch (src)
        {
            case Pipe p:
                n = Pipe.Create(doc, p.get_Parameter(BuiltInParameter.RBS_PIPING_SYSTEM_TYPE_PARAM).AsElementId(), typeId, lvl, a, b); break;
            case Duct d:
                n = Duct.Create(doc, d.get_Parameter(BuiltInParameter.RBS_DUCT_SYSTEM_TYPE_PARAM).AsElementId(), typeId, lvl, a, b); break;
            case Conduit: n = Conduit.Create(doc, typeId, a, b, lvl); break;
            case CableTray: n = CableTray.Create(doc, typeId, a, b, lvl); break;
            default: throw new NotSupportedException(src.Category?.Name);
        }
        foreach (var bp in new[] { BuiltInParameter.RBS_PIPE_DIAMETER_PARAM, BuiltInParameter.RBS_CONDUIT_DIAMETER_PARAM, BuiltInParameter.RBS_CURVE_DIAMETER_PARAM, BuiltInParameter.RBS_CURVE_WIDTH_PARAM, BuiltInParameter.RBS_CURVE_HEIGHT_PARAM, BuiltInParameter.RBS_CABLETRAY_WIDTH_PARAM, BuiltInParameter.RBS_CABLETRAY_HEIGHT_PARAM })
        {
            var sp = src.get_Parameter(bp); var dp = n.get_Parameter(bp);
            if (sp is not null && dp is not null && !dp.IsReadOnly && sp.StorageType == StorageType.Double) dp.Set(sp.AsDouble());
        }
        return n;
    }

    static Connector Near(Element e, XYZ pt)
    {
        var cm = e is MEPCurve m ? m.ConnectorManager : ((FamilyInstance)e).MEPModel.ConnectorManager; Connector? best = null; double bd = double.MaxValue;
        foreach (Connector c in cm.Connectors) { double d = c.Origin.DistanceTo(pt); if (d < bd) { bd = d; best = c; } }
        return best!;
    }

    static string SysName(Element e) => e.get_Parameter(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)?.AsString() ?? "";

    public static (bool ok, string message, string backup) Apply(Document doc, JogPlan pl, UIApplication? app, string? stamp = null, int index = 1)
    {
        using var guard = new Writer.DialogGuard(app);
        var m = (MEPCurve)doc.GetElement(new ElementId(pl.Id)); var u = (pl.P1 - pl.P0).Normalize();
        var far = new List<Connector>(); foreach (Connector c in m.ConnectorManager.Connectors) if (c.Origin.DistanceTo(pl.P1) < 0.02) foreach (Connector r in c.AllRefs) if (r.Owner.Id != m.Id) far.Add(r);
        var farOwners = far.Select(c => c.Owner.Id.Value).ToList();
        var xyz = pl.Offset;
        var pa = pl.P0 + u * (pl.TaFt - pl.RunFt); var pa2 = pl.P0 + u * pl.TaFt + xyz; var pb2 = pl.P0 + u * pl.TbFt + xyz; var pb = pl.P0 + u * (pl.TbFt + pl.RunFt);
        var created = new List<long>(); stamp ??= DateTime.Now.ToString("yyyyMMdd-HHmmss"); string file = Path.Combine(Writer.BackupDir, $"jog-{stamp}-{index}.json"); Directory.CreateDirectory(Writer.BackupDir);
        string sysBefore = SysName(m); var ins = InsulationOf(doc, m);
        using var tg = new TransactionGroup(doc, "排管/管线绕行"); tg.Start();
        try
        {
            using var t = new Transaction(doc, "绕行"); t.Start();
            var fo = t.GetFailureHandlingOptions(); fo.SetFailuresPreprocessor(new Writer.DropWarnings()); t.SetFailureHandlingOptions(fo);
            ((LocationCurve)m.Location).Curve = Line.CreateBound(pl.P0, pa);
            var s1 = Create(doc, m, pa, pa2); var b = Create(doc, m, pa2, pb2); var s2 = Create(doc, m, pb2, pb); var c = Create(doc, m, pb, pl.P1);
            created.AddRange(new[] { s1.Id.Value, b.Id.Value, s2.Id.Value, c.Id.Value });
            foreach (var piece in new[] { s1, b, s2, c }) AddInsulation(doc, piece, ins);
            var seq = new (Element a, XYZ pt, Element b)[] { (m, pa, s1), (s1, pa2, b), (b, pb2, s2), (s2, pb, c) };
            foreach (var (x, pt, y) in seq) created.Add(doc.Create.NewElbowFitting(Near(x, pt), Near(y, pt)).Id.Value);
            var cc = Near(c, pl.P1); foreach (var r in far) { try { cc.ConnectTo(r); } catch { } }
            if (t.Commit() != TransactionStatus.Committed) { tg.RollBack(); return (false, "被回滚（Revit 没接受这次绕行）", ""); }
        }
        catch (Exception ex) { try { tg.RollBack(); } catch { } return (false, "失败，已全部撤销：" + ex.Message, ""); }
        tg.Assimilate();
        var report = Review(doc, m, created, pl, sysBefore, ins is not null);
        File.WriteAllText(file, JsonSerializer.Serialize(new { doc = doc.Title, time = DateTime.Now, id = pl.Id, p0 = new[] { pl.P0.X, pl.P0.Y, pl.P0.Z }, p1 = new[] { pl.P1.X, pl.P1.Y, pl.P1.Z }, created, farOwners }));
        return (true, $"已绕行：新建 {created.Count} 个（4 段管线 + 4 个弯头）。{report}可 Ctrl+Z 撤销或用“还原绕行”", file);
    }

    sealed record Ins(ElementId TypeId, double Thickness, bool IsPipe);

    static Ins? InsulationOf(Document doc, MEPCurve m)
    {
        try
        {
            foreach (var id in InsulationLiningBase.GetInsulationIds(doc, m.Id))
                switch (doc.GetElement(id))
                {
                    case PipeInsulation pi: return new Ins(pi.GetTypeId(), pi.Thickness, true);
                    case DuctInsulation di: return new Ins(di.GetTypeId(), di.Thickness, false);
                }
        }
        catch { }
        return null;
    }

    static void AddInsulation(Document doc, MEPCurve piece, Ins? ins)
    {
        if (ins is null) return;
        try { if (ins.IsPipe) PipeInsulation.Create(doc, piece.Id, ins.TypeId, ins.Thickness); else DuctInsulation.Create(doc, piece.Id, ins.TypeId, ins.Thickness); } catch { }
    }

    /// <summary>After the jog: is the system still one system, did the insulation follow, does the new route clash with something else?</summary>
    static string Review(Document doc, MEPCurve m, List<long> created, JogPlan pl, string sysBefore, bool hadInsulation)
    {
        var parts = new List<string>();
        try
        {
            var pieces = created.Select(i => doc.GetElement(new ElementId(i))).OfType<MEPCurve>().ToList();
            if (m is Pipe or Duct)
            {
                var bad = pieces.Count(p => SysName(p) != sysBefore);
                parts.Add(bad == 0 ? (sysBefore.Length > 0 ? $"系统保持“{sysBefore}”" : "系统连续") : $"有 {bad} 段系统与原来不同，请检查");
            }
            if (hadInsulation) parts.Add($"保温层已加到 {pieces.Count(p => InsulationOf(doc, p) is not null)}/{pieces.Count} 段");
            var obstacleIds = pl.Obstacles.Select(o => o.id).ToHashSet();
            var again = GeneralCheck.Run(doc, created.Select(i => new ElementId(i)), 100, null).SelectMany(c => c.With).Where(w => !created.Contains(w.other) && w.other != m.Id.Value).Select(w => w.other).Distinct().ToList();
            parts.Add(again.Count == 0 ? "绕行后新位置没有再碰到别的构件" : $"绕行后新位置还碰到 {again.Count} 个构件：{string.Join("、", again.Take(5))}");
        }
        catch (Exception ex) { parts.Add("复检出错：" + ex.Message); }
        return string.Join("；", parts) + "。";
    }

    /// <summary>All pieces of a bundle are applied under one stamp so that one click on 还原绕行 undoes the whole bundle.</summary>
    public static (bool ok, string message) ApplyBundle(Document doc, List<JogPlan> plans, UIApplication? app)
    {
        string stamp = DateTime.Now.ToString("yyyyMMdd-HHmmss"); int ok = 0; var msgs = new List<string>();
        for (int i = 0; i < plans.Count; i++)
        {
            var (o, msg, _) = Apply(doc, plans[i], app, stamp, i + 1);
            if (o) ok++; else { msgs.Add($"{plans[i].Id}：{msg}"); break; }
            if (i == plans.Count - 1) msgs.Add(msg);
        }
        return (ok == plans.Count, $"一束 {plans.Count} 根，成功 {ok} 根。" + string.Join(" ", msgs));
    }

    public static string? LatestBackup(Document doc) =>
        !Directory.Exists(Writer.BackupDir) ? null : Directory.GetFiles(Writer.BackupDir, "jog-*.json").OrderByDescending(f => f).FirstOrDefault(f =>
        { try { using var d = JsonDocument.Parse(File.ReadAllText(f)); return d.RootElement.GetProperty("doc").GetString() == doc.Title; } catch { return false; } });

    public static (bool ok, string message) Restore(Document doc, string file, UIApplication? app)
    {
        string prefix = Path.GetFileName(file)[..Path.GetFileName(file).LastIndexOf('-')];
        var all = Directory.GetFiles(Path.GetDirectoryName(file)!, prefix + "-*.json").OrderByDescending(f => f).ToList();
        if (all.Count > 1) { int n = 0; foreach (var f in all) { var (o, m) = RestoreOne(doc, f, app); if (!o) return (false, m); n++; } return (true, $"已还原这一束 {n} 根"); }
        return RestoreOne(doc, file, app);
    }

    static (bool ok, string message) RestoreOne(Document doc, string file, UIApplication? app)
    {
        using var guard = new Writer.DialogGuard(app); using var d = JsonDocument.Parse(File.ReadAllText(file)); var root = d.RootElement;
        var m = doc.GetElement(new ElementId(root.GetProperty("id").GetInt64())) as MEPCurve; if (m is null) return (false, "找不到原来的构件");
        var p0 = root.GetProperty("p0").EnumerateArray().Select(x => x.GetDouble()).ToArray(); var p1 = root.GetProperty("p1").EnumerateArray().Select(x => x.GetDouble()).ToArray();
        var P0 = new XYZ(p0[0], p0[1], p0[2]); var P1 = new XYZ(p1[0], p1[1], p1[2]);
        var owners = root.GetProperty("farOwners").EnumerateArray().Select(x => x.GetInt64()).ToList();
        using var tg = new TransactionGroup(doc, "还原绕行"); tg.Start();
        try
        {
            using var t = new Transaction(doc, "还原"); t.Start();
            foreach (var id in root.GetProperty("created").EnumerateArray()) { var e = doc.GetElement(new ElementId(id.GetInt64())); if (e is not null) doc.Delete(e.Id); }
            ((LocationCurve)m.Location).Curve = Line.CreateBound(P0, P1);
            foreach (var oid in owners)
            {
                var ow = doc.GetElement(new ElementId(oid)); if (ow is null) continue;
                try { Near(m, P1).ConnectTo(Near(ow, P1)); } catch { }
            }
            if (t.Commit() != TransactionStatus.Committed) { tg.RollBack(); return (false, "还原被回滚"); }
        }
        catch (Exception ex) { try { tg.RollBack(); } catch { } return (false, "还原失败：" + ex.Message); }
        tg.Assimilate(); return (true, "已还原到绕行之前");
    }
}
