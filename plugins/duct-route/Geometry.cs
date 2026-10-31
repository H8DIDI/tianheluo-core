namespace KaifaTools.DuctRoute;

internal static class Geo
{
    public static bool Pip(double x, double y, double[][] poly)
    {
        bool c = false; int n = poly.Length;
        for (int i = 0; i < n; i++)
        {
            var p = poly[i]; var q = poly[(i + 1) % n];
            if ((p[1] > y) != (q[1] > y) && x < (q[0] - p[0]) * (y - p[1]) / (q[1] - p[1]) + p[0]) c = !c;
        }
        return c;
    }

    public static double? RoadTop(Scene s, double x, double y)
    {
        double? best = null;
        foreach (var r in s.Roads)
            if (x >= r.MinX && x <= r.MaxX && y >= r.MinY && y <= r.MaxY && Pip(x, y, r.Poly) && (best is null || r.Ztop > best)) best = r.Ztop;
        return best;
    }

    public static double Ground(Scene s, RouteRules rules, double x, double y)
    {
        var rt = RoadTop(s, x, y); if (rt is { } v) return v;
        if (rules.GroundM is { } g) return g;
        if (s.RoadVx.Length == 0) return 0;
        double bd = double.MaxValue; int bi = 0;
        for (int i = 0; i < s.RoadVx.Length; i++)
        {
            double d = Math.Pow(s.RoadVx[i] - x, 2) + Math.Pow(s.RoadVy[i] - y, 2);
            if (d < bd) { bd = d; bi = i; }
        }
        return s.RoadVz[bi];
    }

    public static IEnumerable<double> Range(double a, double b, double step)
    {
        var l = new List<double>(); for (double v = a; v < b - 1e-12; v += step) l.Add(v); l.Add(b); return l.Distinct();
    }

    public static List<Samp> BoxSamples(BoxObs w, double step = 0.25)
    {
        var o = new List<Samp>(); double zc = (w.MinZ + w.MaxZ) / 2, hz = (w.MaxZ - w.MinZ) / 2;
        foreach (var y in Range(w.MinY, w.MaxY, step)) foreach (var x in Range(w.MinX, w.MaxX, step))
            o.Add(new Samp(x, y, zc, hz, 0, w.Dx, w.Dy, w.HasDir, w.Id));
        return o;
    }

    public static List<Samp> BankSamples(Bank b, double z0, double z1, double step = 0.25)
    {
        int ns = Math.Max(2, (int)(b.L / step) + 1), nt = Math.Max(2, (int)(b.W / step) + 1);
        var o = new List<Samp>(ns * nt);
        for (int j = 0; j < nt; j++)
        {
            double tv = -b.W / 2 + b.W * j / (nt - 1);
            for (int i = 0; i < ns; i++)
            {
                double sv = b.L * i / (ns - 1);
                double zb = z0 + (z1 - z0) * (sv / b.L);
                o.Add(new Samp(b.Ax + sv * b.Ux + tv * b.Nx, b.Ay + sv * b.Uy + tv * b.Ny, zb + b.H / 2, b.H / 2, 0, b.Ux, b.Uy, true, b.Id));
            }
        }
        return o;
    }

    // ---- convex hulls (an affine function >= all points  <=>  >= the hull vertices) ----
    public static List<(double t, double c)> HullUpper(IEnumerable<(double t, double c)> pts)
    {
        var d = new Dictionary<double, double>();
        foreach (var (t0, c) in pts) { double t = Math.Round(t0, 4); if (!d.TryGetValue(t, out var cur) || c > cur) d[t] = c; }
        var P = d.OrderBy(k => k.Key).Select(k => (t: k.Key, c: k.Value)).ToList();
        var H = new List<(double t, double c)>();
        foreach (var p in P)
        {
            while (H.Count >= 2 && (H[^1].t - H[^2].t) * (p.c - H[^2].c) - (H[^1].c - H[^2].c) * (p.t - H[^2].t) >= 0) H.RemoveAt(H.Count - 1);
            H.Add(p);
        }
        return H;
    }

    public static List<(double t, double c)> HullLower(IEnumerable<(double t, double c)> pts) =>
        HullUpper(pts.Select(p => (p.t, -p.c))).Select(p => (p.t, -p.c)).ToList();
}
