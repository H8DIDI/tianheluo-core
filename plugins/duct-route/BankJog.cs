using System.Text.Json;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace KaifaTools.DuctRoute;

public sealed record BankVertex(double X, double Y, double Z, double E);
public sealed record BankPlan(long Id, XYZ P0, XYZ P1, double E0, double E1, List<BankVertex> V, List<(long id, string cat)> Obstacles, string Summary);

/// <summary>
/// 排管 (structural framing) detours. A bank is never bent in Revit: it is cut into straight pieces that share their end points, each piece with its own
/// two end elevations. Vertical hop = lead-in slope, flat part over/under the obstacle, lead-out slope. Plan detour = diagonal, parallel part, diagonal.
/// Both ends of the bank stay where they are (they sit in wells). Backups use the format of 井处断开, so 排管还原 can undo them.
/// </summary>
public static partial class Jogger
{
    static readonly BuiltInParameter BP0 = BuiltInParameter.STRUCTURAL_BEAM_END0_ELEVATION, BP1 = BuiltInParameter.STRUCTURAL_BEAM_END1_ELEVATION;

    public static bool IsBankLike(Element e) => e is FamilyInstance && e.Category?.Id.Value == (long)BuiltInCategory.OST_StructuralFraming && e.Location is LocationCurve { Curve: Line };

    /// <summary>Everything that touches the bank, except the wells at its ends and the neighbour pieces that share an end point.</summary>
    static List<Element> BankObstacles(Document doc, Element f, XYZ p0, XYZ p1)
    {
        var hits = new FilteredElementCollector(doc).WherePasses(new ElementMulticategoryFilter(Hits)).WherePasses(new ElementIntersectsElementFilter(f)).ToElements();
        var res = new List<Element>();
        foreach (var h in hits)
        {
            if (h.Id == f.Id) continue;
            var bb = h.get_BoundingBox(null); if (bb is null) continue;
            bool atEnd = false;
            foreach (var p in new[] { p0, p1 })
                if (p.X >= bb.Min.X - 0.5 && p.X <= bb.Max.X + 0.5 && p.Y >= bb.Min.Y - 0.5 && p.Y <= bb.Max.Y + 0.5) atEnd = true;
            if (atEnd) continue;
            if (h.Location is LocationCurve { Curve: Line hl } && (new[] { hl.GetEndPoint(0), hl.GetEndPoint(1) }.Any(q => (q - p0).GetLength() < 0.3 || (q - p1).GetLength() < 0.3))) continue;
            res.Add(h);
        }
        return res;
    }

    /// <param name="mode">"vertical" or "plane"</param>
    public static BankPlan? PlanBank(Document doc, Element f, JogOptions o, string mode, double? commonShiftFt, out string error)
    {
        error = ""; if (!IsBankLike(f)) { error = "不是直线的结构框架"; return null; }
        var ln = (Line)((LocationCurve)f.Location).Curve; var p0 = ln.GetEndPoint(0); var p1 = ln.GetEndPoint(1); double L = ln.Length; var u = (p1 - p0).Normalize();
        if (Math.Abs(u.Z) > 0.15) { error = "坡度太大（超过 15%），先不支持"; return null; }
        var box = f.get_BoundingBox(null); if (box is null) { error = "读不到范围"; return null; }
        double e0 = f.get_Parameter(BP0).AsDouble(), e1 = f.get_Parameter(BP1).AsDouble();
        double hFt = (box.Max.Z - box.Min.Z) - Math.Abs(p1.Z - p0.Z);
        double wFt = Math.Max((box.Max.X - box.Min.X) * Math.Abs(u.Y) + (box.Max.Y - box.Min.Y) * Math.Abs(u.X) - 2 * L * Math.Abs(u.X * u.Y), 0.3);   // plan width of the bank (same estimate as the solver uses)
        double off = box.Min.Z - Math.Min(p0.Z, p1.Z);
        double BottomAt(double t) => p0.Z + (p1.Z - p0.Z) * t / L + off;
        double EAt(double t) => e0 + (e1 - e0) * t / L;
        var hits = BankObstacles(doc, f, p0, p1); if (hits.Count == 0) { error = "没有碰到什么（两端井不算）"; return null; }
        var nrm = new XYZ(-u.Y, u.X, 0).Normalize(); double clr = o.ClearanceM / Ft;
        double tmin = double.MaxValue, tmax = double.MinValue, top = double.MinValue, bot = double.MaxValue, lmax = double.MinValue, lmin = double.MaxValue; var obs = new List<(long, string)>();
        foreach (var h in hits)
        {
            var bb = h.get_BoundingBox(null)!; double a = double.MaxValue, b = double.MinValue;
            foreach (var x in new[] { bb.Min.X, bb.Max.X }) foreach (var y in new[] { bb.Min.Y, bb.Max.Y })
            { var q = new XYZ(x, y, p0.Z) - p0; double t = q.DotProduct(u); a = Math.Min(a, t); b = Math.Max(b, t); double l = q.DotProduct(nrm); lmax = Math.Max(lmax, l); lmin = Math.Min(lmin, l); }
            tmin = Math.Min(tmin, Math.Max(a, 0)); tmax = Math.Max(tmax, Math.Min(b, L)); top = Math.Max(top, bb.Max.Z); bot = Math.Min(bot, bb.Min.Z); obs.Add((h.Id.Value, h.Category?.Name ?? ""));
        }
        double margin = o.MarginM / Ft, ta = Math.Max(tmin - margin, 0), tb = Math.Min(tmax + margin, L);
        var vs = new List<BankVertex>(); string way; double amount;
        if (mode == "vertical")
        {
            double bMin = Math.Min(BottomAt(tmin), BottomAt(tmax)), tMax = Math.Max(BottomAt(tmin), BottomAt(tmax)) + hFt;
            double up = top + clr - bMin, down = bot - clr - tMax;
            double dz = o.Direction == "up" ? up : o.Direction == "down" ? down : (Math.Abs(down) < up ? down : up);
            if (commonShiftFt is { } cs) dz = cs;
            double grid = 0.1 / Ft; dz = (dz < 0 ? -1 : 1) * Math.Ceiling(Math.Abs(dz) / grid - 1e-9) * grid;     // 0.1 m steps, always away from the obstacle
            double slope = o.BankSlopePct / 100.0, want = Math.Abs(dz) / slope;
            double runA = Math.Min(want, ta), runB = Math.Min(want, L - tb);
            if (runA < 1e-6 && ta < 1e-6) runA = 0; if (runB < 1e-6) runB = 0;
            if ((runA > 0 && Math.Abs(dz) / runA > 0.15) || (runB > 0 && Math.Abs(dz) / runB > 0.15) || (runA == 0 || runB == 0) && ta > 0.01 && tb < L - 0.01)
            { error = $"这根排管太短，放不下坡（要在碰撞两侧各留 {want * Ft:0} m，最陡 15%）"; return null; }
            if (runA == 0 || runB == 0) { error = "碰撞就在管端附近，放不下坡"; return null; }
            double[] ts = { 0, ta - runA, ta, tb, tb + runB, L }; double[] w = { 0, 0, 1, 1, 0, 0 };
            for (int i = 0; i < ts.Length; i++) { var pt = p0 + u * ts[i]; vs.Add(new BankVertex(pt.X, pt.Y, pt.Z, EAt(ts[i]) + dz * w[i])); }
            way = dz > 0 ? "上面" : "下面"; amount = Math.Abs(dz);
            double newBot = (BottomAt((ta + tb) / 2) + dz) * Ft;
            return Finish(f, p0, p1, e0, e1, vs, obs, $"{f.Id}：碰撞 {obs.Count} 个，从{way}翻过去，{(dz > 0 ? "抬高" : "压低")} {amount * Ft:0.0} m（取 0.1 m 一档），坡 {o.BankSlopePct:0}%，中间段底标高约 {newBot:0.0} m（相对工程原点）", ref error);
        }
        else if (mode == "path")
        {
            double d = clr + wFt / 2; var grown = new List<List<(double x, double y)>>();
            foreach (var h in hits) foreach (var piece in Pieces(h)) if (piece.Count >= 3) grown.Add(Grow(piece, d));
            var path = ShortestPath(p0, p1, grown, out error); if (path is null) return null;
            double Lh = new XYZ(u.X, u.Y, 0).GetLength();
            foreach (var pt in path)
            {
                double t = Math.Max(0, Math.Min(L, new XYZ(pt.X - p0.X, pt.Y - p0.Y, 0).DotProduct(new XYZ(u.X, u.Y, 0)) / (Lh * Lh)));
                vs.Add(new BankVertex(pt.X, pt.Y, p0.Z + (p1.Z - p0.Z) * t / L, EAt(t)));
            }
            for (int i = 0; i + 1 < vs.Count; i++) if (new XYZ(vs[i + 1].X - vs[i].X, vs[i + 1].Y - vs[i].Y, 0).GetLength() < 1.5) { error = "沿外沿的路径里有太短的一段（小于 0.5 m）"; return null; }
            double extra = 0; for (int i = 0; i + 1 < vs.Count; i++) extra += new XYZ(vs[i + 1].X - vs[i].X, vs[i + 1].Y - vs[i].Y, 0).GetLength(); extra -= L;
            return Finish(f, p0, p1, e0, e1, vs, obs, $"{f.Id}：碰撞 {obs.Count} 个，沿障碍外沿绕，{vs.Count - 2} 个转折点，多走 {extra * Ft:0.0} m（按障碍真实外形外扩，会改平面位置）", ref error);
        }
        else
        {
            double half = wFt / 2, left = lmax + clr + half, right = lmin - clr - half;
            double dl = o.Direction == "left" ? left : o.Direction == "right" ? right : (Math.Abs(right) < left ? right : left);
            if (commonShiftFt is { } cs) dl = cs;
            double grid = 0.1 / Ft; dl = (dl < 0 ? -1 : 1) * Math.Ceiling(Math.Abs(dl) / grid - 1e-9) * grid;
            double run = Math.Abs(dl) / Math.Tan(o.BankPlanAngleDeg * Math.PI / 180);
            if (ta - run < 0 || tb + run > L) { error = $"这根排管太短，放不下平面斜接（两侧各要 {run * Ft:0} m，斜角 {o.BankPlanAngleDeg:0}°）"; return null; }
            double[] ts = { 0, ta - run, ta, tb, tb + run, L }; double[] lat = { 0, 0, dl, dl, 0, 0 };
            for (int i = 0; i < ts.Length; i++) { var pt = p0 + u * ts[i] + nrm * lat[i]; vs.Add(new BankVertex(pt.X, pt.Y, p0.Z + (p1.Z - p0.Z) * ts[i] / L, EAt(ts[i]))); }
            way = dl > 0 ? "左边" : "右边"; amount = Math.Abs(dl);
            return Finish(f, p0, p1, e0, e1, vs, obs, $"{f.Id}：碰撞 {obs.Count} 个，从{way}绕，平移 {amount * Ft:0.0} m（0.1 m 一档），斜角 {o.BankPlanAngleDeg:0}°，两端仍在原井（会改平面位置）", ref error);
        }
    }

    static BankPlan? Finish(Element f, XYZ p0, XYZ p1, double e0, double e1, List<BankVertex> vs, List<(long, string)> obs, string sum, ref string error)
    {
        var clean = new List<BankVertex>();
        foreach (var v in vs) if (clean.Count == 0 || new XYZ(v.X - clean[^1].X, v.Y - clean[^1].Y, 0).GetLength() > 0.05 || Math.Abs(v.E - clean[^1].E) > 1e-4) clean.Add(v);
        if (clean.Count < 2) { error = "没有需要改的"; return null; }
        return new BankPlan(f.Id.Value, p0, p1, e0, e1, clean, obs, sum);
    }

    /// <summary>Cuts each bank into the pieces of its plan; one backup file (format of 井处断开) holds the whole batch.</summary>
    public static (int ok, List<string> failed, string backup) ApplyBanks(Document doc, List<BankPlan> plans, UIApplication? app)
    {
        using var guard = new Writer.DialogGuard(app);
        Directory.CreateDirectory(Writer.BackupDir);
        string file = Path.Combine(Writer.BackupDir, $"cut-{DateTime.Now:yyyyMMdd-HHmmssfff}.json");
        var items = new List<object>(); var failed = new List<string>(); int ok = 0;
        using var tg = new TransactionGroup(doc, "排管绕行"); tg.Start();
        foreach (var pl in plans)
        {
            var e = doc.GetElement(new ElementId(pl.Id)); if (e is null) { failed.Add($"{pl.Id}：找不到"); continue; }
            var pre = new Writer.DropWarnings(); using var t = new Transaction(doc, "排管绕行");
            try
            {
                t.Start(); var fo = t.GetFailureHandlingOptions(); fo.SetFailuresPreprocessor(pre); t.SetFailureHandlingOptions(fo);
                int n = pl.V.Count - 1; var els = new List<Element> { e };
                for (int i = 1; i < n; i++) els.Add(doc.GetElement(ElementTransformUtils.CopyElement(doc, e.Id, XYZ.Zero).First()));
                for (int i = 0; i < n; i++)
                {
                    ((LocationCurve)els[i].Location).Curve = Line.CreateBound(new XYZ(pl.V[i].X, pl.V[i].Y, pl.V[i].Z), new XYZ(pl.V[i + 1].X, pl.V[i + 1].Y, pl.V[i + 1].Z));
                    els[i].get_Parameter(BP0).Set(pl.V[i].E); els[i].get_Parameter(BP1).Set(pl.V[i + 1].E);
                }
                if (t.Commit() == TransactionStatus.Committed)
                { ok++; items.Add(new { id = pl.Id, p = new[] { pl.P0.X, pl.P0.Y, pl.P0.Z }, q = new[] { pl.P1.X, pl.P1.Y, pl.P1.Z }, e0 = pl.E0, e1 = pl.E1, news = els.Skip(1).Select(x => x.Id.Value).ToArray() }); }
                else failed.Add($"{pl.Id}：被回滚");
            }
            catch (Exception ex) { try { t.RollBack(); } catch { } failed.Add($"{pl.Id}：{ex.Message}"); }
        }
        tg.Assimilate();
        File.WriteAllText(file, JsonSerializer.Serialize(new { doc = doc.Title, time = DateTime.Now, items }));
        return (ok, failed, file);
    }

    /// <summary>After the change: what do the pieces of each bank still touch (end wells and its own neighbour pieces do not count)?</summary>
    public static List<(long bank, List<long> with)> ReviewBanks(Document doc, List<List<long>> groups)
    {
        var res = new List<(long, List<long>)>();
        foreach (var g in groups)
        {
            var own = g.ToHashSet();
            foreach (var id in g)
            {
                var e = doc.GetElement(new ElementId(id)); if (e is null || !IsBankLike(e)) continue;
                var ln = (Line)((LocationCurve)e.Location).Curve;
                var hits = BankObstacles(doc, e, ln.GetEndPoint(0), ln.GetEndPoint(1)).Where(h => !own.Contains(h.Id.Value)).Select(h => h.Id.Value).ToList();
                if (hits.Count > 0) res.Add((id, hits));
            }
        }
        return res;
    }
}
