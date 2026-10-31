using Autodesk.Revit.DB;
using Autodesk.Revit.DB.Plumbing;

namespace KaifaTools.DuctRoute;

/// <summary>
/// Planar detour around obstacles of any shape: each obstacle's footprint (convex hull of its solid in plan) is grown by clearance + half width,
/// then the shortest way from the start to the end of the run that stays out of every grown footprint is found on the visibility graph.
/// The result is a many-bend path (not just one box); the bends get elbow fittings like in the plain jog.
/// </summary>
public static partial class Jogger
{
    // ------------------------------------------------------------------ footprints
    static List<(double x, double y)> ConvexHull(List<(double x, double y)> pts)
    {
        var p = pts.Distinct().OrderBy(a => a.x).ThenBy(a => a.y).ToList(); if (p.Count < 3) return p;
        double Cross((double x, double y) o, (double x, double y) a, (double x, double y) b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
        var h = new List<(double x, double y)>();
        foreach (var q in p) { while (h.Count >= 2 && Cross(h[^2], h[^1], q) <= 1e-12) h.RemoveAt(h.Count - 1); h.Add(q); }
        int lo = h.Count + 1;
        for (int i = p.Count - 2; i >= 0; i--) { var q = p[i]; while (h.Count >= lo && Cross(h[^2], h[^1], q) <= 1e-12) h.RemoveAt(h.Count - 1); h.Add(q); }
        h.RemoveAt(h.Count - 1); return h;                                              // counter-clockwise
    }

    /// <summary>Plan outline of an element: convex hull of its geometry, or its bounding box when no geometry can be read.</summary>
    public static List<(double x, double y)> Footprint(Element e)
    {
        var pts = new List<(double x, double y)>();
        try
        {
            var opt = new Options { ComputeReferences = false, DetailLevel = ViewDetailLevel.Coarse };
            void Walk(GeometryElement? ge)
            {
                if (ge is null) return;
                foreach (GeometryObject go in ge)
                    switch (go)
                    {
                        case Solid s when s.Volume > 1e-9: foreach (Edge ed in s.Edges) foreach (XYZ q in ed.Tessellate()) pts.Add((q.X, q.Y)); break;
                        case GeometryInstance gi: Walk(gi.GetInstanceGeometry()); break;
                    }
            }
            Walk(e.get_Geometry(opt));
        }
        catch { }
        var hull = ConvexHull(pts);
        if (hull.Count >= 3) return hull;
        var bb = e.get_BoundingBox(null); if (bb is null) return new();
        return new() { (bb.Min.X, bb.Min.Y), (bb.Max.X, bb.Min.Y), (bb.Max.X, bb.Max.Y), (bb.Min.X, bb.Max.Y) };
    }

    /// <summary>
    /// Plan outline as a list of CONVEX pieces: the triangles of the upward faces of the element's solids (so an L-shaped slab stays L-shaped).
    /// Falls back to the convex hull when no face can be read.
    /// </summary>
    public static List<List<(double x, double y)>> Pieces(Element e)
    {
        var tris = new List<List<(double x, double y)>>();
        try
        {
            var opt = new Options { ComputeReferences = false, DetailLevel = ViewDetailLevel.Coarse };
            void Walk(GeometryElement? ge)
            {
                if (ge is null) return;
                foreach (GeometryObject go in ge)
                    switch (go)
                    {
                        case Solid s when s.Volume > 1e-9:
                            foreach (Face f in s.Faces)
                            {
                                if (f is not PlanarFace pf || Math.Abs(pf.FaceNormal.Z) < 0.5) continue;
                                var mesh = f.Triangulate(0.3);
                                for (int i = 0; i < mesh.NumTriangles; i++)
                                {
                                    var t = mesh.get_Triangle(i); var tri = new List<(double x, double y)> { (t.get_Vertex(0).X, t.get_Vertex(0).Y), (t.get_Vertex(1).X, t.get_Vertex(1).Y), (t.get_Vertex(2).X, t.get_Vertex(2).Y) };
                                    double cr = (tri[1].x - tri[0].x) * (tri[2].y - tri[0].y) - (tri[1].y - tri[0].y) * (tri[2].x - tri[0].x);
                                    if (Math.Abs(cr) < 1e-9) continue; if (cr < 0) tri.Reverse(); tris.Add(tri);
                                }
                            }
                            break;
                        case GeometryInstance gi: Walk(gi.GetInstanceGeometry()); break;
                    }
            }
            Walk(e.get_Geometry(opt));
        }
        catch { }
        // keep one face of each stacked pair (top and bottom triangles coincide in plan) and cap the count
        var uniq = new List<List<(double x, double y)>>(); var seen = new HashSet<string>();
        foreach (var t in tris) { var key = string.Join("|", t.Select(q => $"{Math.Round(q.x, 2)},{Math.Round(q.y, 2)}").OrderBy(z => z)); if (seen.Add(key)) uniq.Add(t); }
        if (uniq.Count == 0 || uniq.Count > 60) { var h = Footprint(e); return h.Count >= 3 ? new() { h } : new(); }
        return uniq;
    }

    /// <summary>The footprint grown by d on every side (octagon that contains the disk of radius d).</summary>
    static List<(double x, double y)> Grow(List<(double x, double y)> hull, double d)
    {
        double r = d / Math.Cos(Math.PI / 8); var all = new List<(double x, double y)>();
        foreach (var (x, y) in hull) for (int k = 0; k < 8; k++) { double a = (k + 0.5) * Math.PI / 4; all.Add((x + r * Math.Cos(a), y + r * Math.Sin(a))); }
        return ConvexHull(all);
    }

    /// <summary>Length of the part of segment a-b that lies strictly inside the convex counter-clockwise polygon.</summary>
    static double InsideLen((double x, double y) a, (double x, double y) b, List<(double x, double y)> poly)
    {
        double t0 = 0, t1 = 1, dx = b.x - a.x, dy = b.y - a.y;
        for (int i = 0; i < poly.Count; i++)
        {
            var p = poly[i]; var q = poly[(i + 1) % poly.Count]; double ex = q.x - p.x, ey = q.y - p.y;
            double num = ex * (a.y - p.y) - ey * (a.x - p.x), den = ex * dy - ey * dx;     // inside is the left side: cross(e, X - p) > 0
            if (Math.Abs(den) < 1e-12) { if (num <= 1e-9) return 0; continue; }
            double t = -num / den;
            if (den > 0) t0 = Math.Max(t0, t); else t1 = Math.Min(t1, t);
            if (t0 >= t1) return 0;
        }
        return (t1 - t0) * Math.Sqrt(dx * dx + dy * dy);
    }

    // ------------------------------------------------------------------ shortest path on the visibility graph
    public static List<XYZ>? ShortestPath(XYZ s, XYZ e, List<List<(double x, double y)>> grown, out string error)
    {
        error = ""; var nodes = new List<(double x, double y)> { (s.X, s.Y), (e.X, e.Y) };
        foreach (var poly in grown) foreach (var v in poly) if (!grown.Any(o => !ReferenceEquals(o, poly) && PointInside(v, o))) nodes.Add(v);
        foreach (var poly in grown)
        {
            if (InsideLen(nodes[0], nodes[0], poly) > 0) { }
            if (PointInside(nodes[0], poly) || PointInside(nodes[1], poly)) { error = "起点或终点离障碍太近，已经在安全范围内，没法绕"; return null; }
        }
        int n = nodes.Count; var dist = Enumerable.Repeat(double.MaxValue, n).ToArray(); var prev = Enumerable.Repeat(-1, n).ToArray(); var done = new bool[n]; dist[0] = 0;
        bool Clear(int i, int k) { foreach (var poly in grown) if (InsideLen(nodes[i], nodes[k], poly) > 1e-4) return false; return true; }
        for (int it = 0; it < n; it++)
        {
            int u = -1; for (int i = 0; i < n; i++) if (!done[i] && (u < 0 || dist[i] < dist[u])) u = i;
            if (u < 0 || dist[u] == double.MaxValue) break; done[u] = true; if (u == 1) break;
            for (int v = 0; v < n; v++)
            {
                if (done[v]) continue;
                double w = Math.Sqrt(Math.Pow(nodes[u].x - nodes[v].x, 2) + Math.Pow(nodes[u].y - nodes[v].y, 2)); if (dist[u] + w >= dist[v]) continue;
                if (Clear(u, v)) { dist[v] = dist[u] + w; prev[v] = u; }
            }
        }
        if (prev[1] < 0) { error = "找不到绕开障碍的路"; return null; }
        var path = new List<XYZ>(); for (int c = 1; c >= 0; c = prev[c] < 0 ? -1 : prev[c]) { path.Add(new XYZ(nodes[c].x, nodes[c].y, s.Z)); if (c == 0) break; }
        path.Reverse(); path[0] = s; path[^1] = new XYZ(e.X, e.Y, e.Z);
        // drop bends that are almost straight
        for (int i = path.Count - 2; i >= 1; i--) { var a = (path[i] - path[i - 1]).Normalize(); var b = (path[i + 1] - path[i]).Normalize(); if (a.CrossProduct(b).GetLength() < 0.01) path.RemoveAt(i); }
        return path;
    }

    static bool PointInside((double x, double y) p, List<(double x, double y)> poly)
    {
        for (int i = 0; i < poly.Count; i++) { var a = poly[i]; var b = poly[(i + 1) % poly.Count]; if ((b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x) <= 1e-9) return false; }
        return true;
    }

    /// <summary>Plan for one straight horizontal run: the shortest planar detour around everything that clashes with it.</summary>
    public static JogPlan? PlanPath(Document doc, MEPCurve m, JogOptions o, out string error)
    {
        error = ""; if (m.Location is not LocationCurve lc || lc.Curve is not Line ln) { error = "只支持直线段"; return null; }
        var p0 = ln.GetEndPoint(0); var p1 = ln.GetEndPoint(1); if (Math.Abs(p1.Z - p0.Z) > 0.15 * ln.Length) { error = "这一段坡度太大（超过 15%），先不支持"; return null; }
        var hits = new FilteredElementCollector(doc).WherePasses(new ElementMulticategoryFilter(Hits)).WherePasses(new ElementIntersectsElementFilter(m)).ToElements().Where(x => x.Id != m.Id && !SameRun(m, x)).ToList();
        if (hits.Count == 0) { error = "这一段没有碰撞"; return null; }
        double d = o.ClearanceM / Ft + HalfWidthFt(m); var grown = new List<List<(double x, double y)>>();
        foreach (var h in hits) foreach (var piece in Pieces(h)) if (piece.Count >= 3) grown.Add(Grow(piece, d));
        var path = ShortestPath(p0, p1, grown, out error); if (path is null) return null;
        { var u2 = new XYZ(p1.X - p0.X, p1.Y - p0.Y, 0); double L2 = u2.GetLength(); u2 = u2.Normalize(); for (int i = 1; i + 1 < path.Count; i++) { double t = Math.Max(0, Math.Min(1, (new XYZ(path[i].X - p0.X, path[i].Y - p0.Y, 0)).DotProduct(u2) / L2)); path[i] = new XYZ(path[i].X, path[i].Y, p0.Z + (p1.Z - p0.Z) * t); } }
        for (int i = 0; i + 1 < path.Count; i++) if ((path[i + 1] - path[i]).GetLength() < 0.6 / Ft) { error = "绕行路径里有太短的一段（小于 0.6 m），弯头放不下"; return null; }
        double extra = 0, straight = ln.Length; for (int i = 0; i + 1 < path.Count; i++) extra += (path[i + 1] - path[i]).GetLength(); extra -= straight;
        var obs = hits.Select(x => (x.Id.Value, x.Category?.Name ?? "")).ToList();
        string sum = $"{m.Category?.Name} {m.Id}：碰撞 {obs.Count} 个，沿障碍外沿绕，{path.Count - 2} 个转折点，多走 {extra * Ft:0.0} m（会改平面位置）";
        return new JogPlan(m.Id.Value, m.Category?.Name ?? "", p0, p1, XYZ.Zero, 0, 0, 0, obs, sum, path);
    }
}
