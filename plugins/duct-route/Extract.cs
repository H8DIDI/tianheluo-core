using Autodesk.Revit.DB;

namespace KaifaTools.DuctRoute;

/// <summary>Reads the Revit model once into plain in-memory objects (meters). All later work never touches Revit.</summary>
public static class Extractor
{
    const double Ft = 0.3048;

    public static string? BankKind(string fam)
    {
        foreach (var k in new[] { "TELCO", "ELV", "LV-", "ELE-" }) if (fam.StartsWith(k)) return k.TrimEnd('-');
        return null;
    }

    public static string? WellKind(string fam)
    {
        if (fam.Contains("TELCO")) return "TELCO";
        if (fam.StartsWith("ELV")) return "ELV";
        if (fam.StartsWith("LV-")) return "LV";
        if (fam.StartsWith("ELE-EM")) return "ELE";
        return null;
    }

    /// <param name="selection">selected element ids (movable = the selected banks) or null</param>
    /// <param name="region">plan rectangle (minX,minY,maxX,maxY) in feet, e.g. a scope box; null = whole model</param>
    public static Scene Read(Document doc, RouteRules rules, ICollection<ElementId>? selection, double[]? region, Action<string>? log = null)
    {
        var s = new Scene();
        var sel = selection?.Select(i => i.Value).ToHashSet();
        var frames = new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType().OfType<FamilyInstance>().ToList();
        foreach (var f in frames)
        {
            string fam = f.Symbol?.FamilyName ?? "";
            if (f.Location is not LocationCurve lc || lc.Curve is not Line ln) continue;
            var box = f.get_BoundingBox(null); if (box is null) continue;
            var a = ln.GetEndPoint(0); var b = ln.GetEndPoint(1);
            if (fam.StartsWith("雨水沟"))
            {
                double dx = b.X - a.X, dy = b.Y - a.Y, dn = Math.Sqrt(dx * dx + dy * dy);
                s.Drains.Add(new BoxObs { Id = f.Id.Value, Family = fam, IsDrain = true, MinX = box.Min.X * Ft, MinY = box.Min.Y * Ft, MinZ = box.Min.Z * Ft, MaxX = box.Max.X * Ft, MaxY = box.Max.Y * Ft, MaxZ = box.Max.Z * Ft, HasDir = dn > 1e-6, Dx = dn > 1e-6 ? dx / dn : 0, Dy = dn > 1e-6 ? dy / dn : 0 });
                continue;
            }
            var kind = BankKind(fam); if (kind is null) continue;
            double L = Math.Sqrt(Math.Pow(b.X - a.X, 2) + Math.Pow(b.Y - a.Y, 2)) * Ft; if (L < 0.3) continue;
            double ux = (b.X - a.X) * Ft / L, uy = (b.Y - a.Y) * Ft / L, sn = Math.Abs(uy), cs = Math.Abs(ux);
            double bw = (box.Max.X - box.Min.X) * Ft, bd = (box.Max.Y - box.Min.Y) * Ft, bh = (box.Max.Z - box.Min.Z) * Ft;
            double w = Math.Max(bw * sn + bd * cs - 2 * L * sn * cs, 0.1);
            double h = bh - Math.Abs(b.Z - a.Z) * Ft; double off = (box.Min.Z - Math.Min(a.Z, b.Z)) * Ft;
            var bk = new Bank
            {
                Id = f.Id.Value, Kind = kind, Family = fam, Comment = f.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)?.AsString() ?? "",
                Ax = a.X * Ft, Ay = a.Y * Ft, Bx = b.X * Ft, By = b.Y * Ft, L = L, Ux = ux, Uy = uy, Nx = -uy, Ny = ux, W = w, H = h,
                O0 = a.Z * Ft + off, O1 = b.Z * Ft + off
            };
            bk.Z0 = bk.O0; bk.Z1 = bk.O1;
            bk.Movable = sel is not null ? sel.Contains(bk.Id) : region is null || SegInRect(a.X, a.Y, b.X, b.Y, region);
            s.Banks.Add(bk); s.ById[bk.Id] = bk;
        }
        var mv = s.Banks.Where(x => x.Movable).ToList();
        if (mv.Count == 0) return s;
        double pad = rules.MarginM;
        double x0 = mv.Min(x => Math.Min(x.Ax, x.Bx)) - pad, x1 = mv.Max(x => Math.Max(x.Ax, x.Bx)) + pad;
        double y0 = mv.Min(x => Math.Min(x.Ay, x.By)) - pad, y1 = mv.Max(x => Math.Max(x.Ay, x.By)) + pad;
        bool InRegion(double ax, double ay, double bx, double by) => !(Math.Max(ax, bx) < x0 || Math.Min(ax, bx) > x1 || Math.Max(ay, by) < y0 || Math.Min(ay, by) > y1);
        foreach (var gone in s.Banks.Where(x => !x.Movable && !InRegion(x.Ax, x.Ay, x.Bx, x.By)).ToList()) { s.Banks.Remove(gone); s.ById.Remove(gone.Id); }
        s.Drains.RemoveAll(d => !InRegion(d.MinX, d.MinY, d.MaxX, d.MaxY));

        foreach (var g in new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType().OfType<FamilyInstance>())
        {
            string tn = g.Symbol?.Name ?? ""; if (tn.StartsWith("路缘石")) continue;
            var box = g.get_BoundingBox(null); if (box is null) continue;
            if (!InRegion(box.Min.X * Ft, box.Min.Y * Ft, box.Max.X * Ft, box.Max.Y * Ft)) continue;
            string fam = g.Symbol?.FamilyName ?? "";
            s.Wells.Add(new BoxObs
            {
                Id = g.Id.Value, Family = fam, Mark = g.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.AsString() ?? "", Kind = WellKind(fam),
                MinX = box.Min.X * Ft, MinY = box.Min.Y * Ft, MinZ = box.Min.Z * Ft, MaxX = box.Max.X * Ft, MaxY = box.Max.Y * Ft, MaxZ = box.Max.Z * Ft
            });
        }

        var opt = new Options { ComputeReferences = false };
        foreach (var fl in new FilteredElementCollector(doc).OfClass(typeof(Floor)).WhereElementIsNotElementType().Cast<Floor>())
        {
            string tn = fl.FloorType?.Name ?? ""; if (!tn.Contains(rules.RoadKeyword)) continue;
            var box = fl.get_BoundingBox(null); if (box is null || !InRegion(box.Min.X * Ft, box.Min.Y * Ft, box.Max.X * Ft, box.Max.Y * Ft)) continue;
            var road = TopLoop(fl, opt, box.Max.Z); if (road is not null) s.Roads.Add(road);
        }
        s.RoadVx = s.Roads.SelectMany(r => r.Poly.Select(p => p[0])).ToArray();
        s.RoadVy = s.Roads.SelectMany(r => r.Poly.Select(p => p[1])).ToArray();
        s.RoadVz = s.Roads.SelectMany(r => r.Poly.Select(_ => r.Ztop)).ToArray();

        if (rules.AvoidPipes)
            foreach (var p in new FilteredElementCollector(doc).OfClass(typeof(Autodesk.Revit.DB.Plumbing.Pipe)).Cast<Autodesk.Revit.DB.Plumbing.Pipe>())
                if (p.Location is LocationCurve pl && pl.Curve is Line l)
                {
                    var A = l.GetEndPoint(0); var B = l.GetEndPoint(1);
                    if (InRegion(A.X * Ft, A.Y * Ft, B.X * Ft, B.Y * Ft))
                        s.Pipes.Add((new[] { A.X * Ft, A.Y * Ft, A.Z * Ft }, new[] { B.X * Ft, B.Y * Ft, B.Z * Ft }, (p.get_Parameter(BuiltInParameter.RBS_PIPE_OUTER_DIAMETER)?.AsDouble() ?? 0.2) * Ft / 2));
                }
        Link(s, rules); CutPairTies(s);
        log?.Invoke($"排管 {s.Banks.Count}（可调 {s.Movable.Count()}）· 井 {s.Wells.Count} · 雨水沟 {s.Drains.Count} · 路面 {s.Roads.Count}" + (rules.AvoidPipes ? $" · 水管 {s.Pipes.Count}" : ""));
        return s;
    }

    static bool SegInRect(double ax, double ay, double bx, double by, double[] r)
    {
        double t0 = 0, t1 = 1, dx = bx - ax, dy = by - ay;
        double[] p = { -dx, dx, -dy, dy }, q = { ax - r[0], r[2] - ax, ay - r[1], r[3] - ay };
        for (int i = 0; i < 4; i++)
        {
            if (Math.Abs(p[i]) < 1e-12) { if (q[i] < 0) return false; }
            else
            {
                double t = q[i] / p[i];
                if (p[i] < 0) { if (t > t1) return false; t0 = Math.Max(t0, t); }
                else { if (t < t0) return false; t1 = Math.Min(t1, t); }
            }
        }
        return true;
    }

    static Road? TopLoop(Floor fl, Options opt, double ztopFt)
    {
        var ge = fl.get_Geometry(opt); if (ge is null) return null;
        foreach (GeometryObject go in ge)
            if (go is Solid sd)
                foreach (Face f in sd.Faces)
                    if (f is PlanarFace pf && pf.FaceNormal.Z > 0.99 && Math.Abs(pf.Origin.Z - ztopFt) < 0.05)
                    {
                        var loops = pf.GetEdgesAsCurveLoops(); if (loops.Count == 0) continue;
                        var pts = new List<double[]>();
                        foreach (var c in loops[0]) { var tess = c.Tessellate(); for (int i = 0; i < tess.Count - 1; i++) pts.Add(new[] { tess[i].X * Ft, tess[i].Y * Ft }); }
                        if (pts.Count < 3) continue;
                        return new Road { Ztop = ztopFt * Ft, Poly = pts.ToArray(), MinX = pts.Min(p => p[0]), MaxX = pts.Max(p => p[0]), MinY = pts.Min(p => p[1]), MaxY = pts.Max(p => p[1]) };
                    }
        return null;
    }

    /// <summary>Which wells each bank reaches, which bank ends meet, and which same-kind ends must stay level.</summary>
    static void Link(Scene s, RouteRules rules)
    {
        foreach (var b in s.Banks)
            foreach (var w in s.Wells.Where(w => w.Kind == b.Kind))
                if (SegBox(b, w, 0.2) is { } t) { b.Links.Add((w, t.Item1, t.Item2)); b.Wid.Add(w.Id); }
        foreach (var b in s.Banks.Where(x => x.Movable))
            foreach (var o in s.Banks)
            {
                if (o == b) continue;
                foreach (var (px, py) in new[] { (b.Ax, b.Ay), (b.Bx, b.By) })
                    foreach (var (qx, qy) in new[] { (o.Ax, o.Ay), (o.Bx, o.By) })
                        if (Math.Sqrt((px - qx) * (px - qx) + (py - qy) * (py - qy)) < 0.6) b.Junc.Add((o.Id, px, py));
                if (o.Kind != b.Kind) continue;
                foreach (var (isStart, px, py) in new[] { (true, b.Ax, b.Ay), (false, b.Bx, b.By) })
                    foreach (var (oStart, qx, qy) in new[] { (true, o.Ax, o.Ay), (false, o.Bx, o.By) })
                        if (Math.Sqrt((px - qx) * (px - qx) + (py - qy) * (py - qy)) < 0.6)
                        {
                            double mine = isStart ? b.O0 : b.O1, theirs = oStart ? o.O0 : o.O1;
                            (isStart ? b.Nb0 : b.Nb1).Add(new Tie(o.Id, oStart, Math.Max(rules.JointTolM, Math.Abs(mine - theirs))));
                        }
            }
    }

    /// <summary>Two collinear banks of the same kind facing each other across a TELCO well are the two halves of a cut line: keep their levels equal.</summary>
    static void CutPairTies(Scene s)
    {
        var tw = s.Wells.Where(w => w.Kind == "TELCO").ToList(); if (tw.Count == 0) return;
        foreach (var b in s.Banks.Where(x => x.Movable))
            foreach (var o in s.Banks)
            {
                if (o == b || o.Kind != b.Kind) continue;
                if (Math.Abs(b.Ux * o.Uy - b.Uy * o.Ux) > 0.01) continue;                               // parallel
                if (Math.Abs((o.Ax - b.Ax) * b.Nx + (o.Ay - b.Ay) * b.Ny) > 0.1) continue;            // on the same line
                double best = double.MaxValue; bool bs = false, os = false;
                foreach (var (be, bx, by) in new[] { (true, b.Ax, b.Ay), (false, b.Bx, b.By) })
                    foreach (var (oe, ox, oy) in new[] { (true, o.Ax, o.Ay), (false, o.Bx, o.By) })
                    {
                        double d = Math.Sqrt((bx - ox) * (bx - ox) + (by - oy) * (by - oy));
                        if (d < best) { best = d; bs = be; os = oe; }
                    }
                if (best < 0.6 || best > 14) continue;                                                  // joined directly (handled elsewhere) or too far
                double mx = ((bs ? b.Ax : b.Bx) + (os ? o.Ax : o.Bx)) / 2, my = ((bs ? b.Ay : b.By) + (os ? o.Ay : o.By)) / 2;
                if (!tw.Any(w => mx >= w.MinX && mx <= w.MaxX && my >= w.MinY && my <= w.MaxY)) continue;
                (bs ? b.Nb0 : b.Nb1).Add(new Tie(o.Id, os, 0.02));
            }
    }

    public static (double, double)? SegBox(Bank b, BoxObs w, double e)
    {
        double t0 = 0, t1 = b.L; double[] lo = { w.MinX - e, w.MinY - e }, hi = { w.MaxX + e, w.MaxY + e };
        double[] a = { b.Ax, b.Ay }, u = { b.Ux, b.Uy };
        for (int k = 0; k < 2; k++)
        {
            if (Math.Abs(u[k]) < 1e-9) { if (a[k] < lo[k] || a[k] > hi[k]) return null; }
            else
            {
                double ta = (lo[k] - a[k]) / u[k], tb = (hi[k] - a[k]) / u[k];
                if (ta > tb) (ta, tb) = (tb, ta);
                t0 = Math.Max(t0, ta); t1 = Math.Min(t1, tb); if (t0 > t1) return null;
            }
        }
        return (t0, t1);
    }
}
