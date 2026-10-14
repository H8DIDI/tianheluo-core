namespace KaifaTools.DuctSnap;

// Plan-view geometry for 排管贴井内壁. No Revit types here, so the rules can be tested without Revit.
// All numbers are millimetres in model coordinates.
//
// The idea: a bank (排管, a structural framing line) has two ends. Each end that sits near a well is
// compared with the well's inner cavity, measured along the bank's own direction:
//   - the end stops short of the inner wall  -> Extend: move the end into the cavity up to the wall
//   - the end overshoots into the cavity     -> Trim:   move the end back out to the wall on the bank side
// Only the plan position changes. The end keeps its elevation (Z), so 标高 is unchanged.

/// <summary>A point in plan view, millimetres.</summary>
public readonly record struct Pt2(double X, double Y);

/// <summary>Inner cavity of a well: the inner faces of its four walls, millimetres.</summary>
public readonly record struct Cavity(double XLeft, double XRight, double YBottom, double YTop);

/// <summary>One vertical planar face of a well, in plan: normal (Nx, Ny), a point on it (X, Y), area in m².</summary>
public readonly record struct WallFace(double Nx, double Ny, double X, double Y, double AreaM2);

public enum SnapKind
{
    Fit,           // already on the inner wall, within tolerance: no change
    Extend,        // stops short of the inner wall: extend into the well
    Trim,          // overshoots into the well: trim back to the inner wall on the bank side
    TrimTooFar,    // would need a trim longer than the limit: end sits in the middle of the well, a person decides
    ExtendTooFar,  // would need an extension longer than the limit: a person decides
    Through,       // the well lies ahead of this end on the bank side: the bank passes through the well
    Inside,        // the whole bank lies inside the cavity: redundant, a person decides
    NoCavity,      // the well's inner walls could not be identified
    Unrelated,     // the bank's line does not meet this well
    Degenerate,    // zero-length bank
}

/// <summary>Result for one end. T is the signed move along the bank direction (mm): negative = toward the well when extending.</summary>
public readonly record struct SnapDecision(SnapKind Kind, double T);

/// <summary>User limits, from 排管贴井规则.json.</summary>
public sealed record SnapLimits(double MaxExtendMm, double MaxTrimMm, double ToleranceMm);

public static class DuctSnapMath
{
    // Numeric slack (mm): an end exactly on a face is not counted as inside or outside.
    private const double Eps = 1.0;

    /// <summary>Decide what happens to one end of a bank that is near a well.</summary>
    /// <param name="end">The end being checked.</param>
    /// <param name="other">The bank's other end: gives the bank direction (end -> other).</param>
    public static SnapDecision Classify(Pt2 end, Pt2 other, Cavity cav, SnapLimits lim)
    {
        double dx = other.X - end.X, dy = other.Y - end.Y;
        double len = Math.Sqrt(dx * dx + dy * dy);
        if (len < 1e-3) return new SnapDecision(SnapKind.Degenerate, 0);
        double ux = dx / len, uy = dy / len;

        // Parameter range (along the bank direction, from the end) where the line is inside the cavity.
        double lo = double.NegativeInfinity, hi = double.PositiveInfinity;
        if (!Slab(end.X, ux, cav.XLeft, cav.XRight, ref lo, ref hi)) return new SnapDecision(SnapKind.Unrelated, 0);
        if (!Slab(end.Y, uy, cav.YBottom, cav.YTop, ref lo, ref hi)) return new SnapDecision(SnapKind.Unrelated, 0);
        if (lo > hi) return new SnapDecision(SnapKind.Unrelated, 0);

        if (lo < -Eps && hi > Eps)
        {
            // The end is inside the cavity. Both ends inside -> the whole bank lies in the well.
            if (len <= hi - Eps) return new SnapDecision(SnapKind.Inside, len);
            // Otherwise the bank leaves the cavity at the wall on its own side: trim back to that wall.
            if (hi < lim.ToleranceMm) return new SnapDecision(SnapKind.Fit, 0);
            return hi <= lim.MaxTrimMm
                ? new SnapDecision(SnapKind.Trim, hi)
                : new SnapDecision(SnapKind.TrimTooFar, hi);
        }

        if (hi < -Eps)
        {
            // The cavity is behind the end (the bank leaves the well here): extend toward it up to the near wall.
            double t = hi; // negative: nearest boundary, moving the end back toward the well
            if (-t < lim.ToleranceMm) return new SnapDecision(SnapKind.Fit, 0);
            return -t <= lim.MaxExtendMm
                ? new SnapDecision(SnapKind.Extend, t)
                : new SnapDecision(SnapKind.ExtendTooFar, t);
        }

        if (lo > Eps)
        {
            // The cavity is ahead of the end, on the bank side: this bank crosses the well. Not auto-edited.
            return new SnapDecision(SnapKind.Through, lo);
        }

        // Within tolerance of the boundary on both sides.
        return new SnapDecision(SnapKind.Fit, 0);
    }

    /// <summary>The end moved by T along the bank direction. Plan position only.</summary>
    public static Pt2 Move(Pt2 end, Pt2 other, double t)
    {
        double dx = other.X - end.X, dy = other.Y - end.Y;
        double len = Math.Sqrt(dx * dx + dy * dy);
        if (len < 1e-3) return end;
        return new Pt2(end.X + t * dx / len, end.Y + t * dy / len);
    }

    /// <summary>
    /// Inner cavity of a rectangular well from its vertical faces. Each wall's inner face has its normal pointing
    /// into the cavity. The left inner face is the smallest X among +X faces (the outer right wall's +X face lies
    /// further right), the right inner face is the largest X among -X faces, and likewise for Y.
    /// Faces smaller than max(minAreaM2, areaRatio * largest) are ignored (brackets, fixtures).
    /// Returns null when the four sides cannot be found or the span is not plausible.
    /// </summary>
    public static Cavity? FindCavity(IReadOnlyList<WallFace> faces, double minAreaM2, double areaRatio,
        double minSpanMm = 500, double maxSpanMm = 6000)
    {
        if (faces.Count == 0) return null;
        double largest = 0;
        foreach (var f in faces) largest = Math.Max(largest, f.AreaM2);
        double floor = Math.Max(minAreaM2, areaRatio * largest);

        double? xl = null, xr = null, yb = null, yt = null;
        foreach (var f in faces)
        {
            if (f.AreaM2 < floor) continue;
            if (IsAxis(f.Nx, 1) && IsAxis(f.Ny, 0)) xl = xl is null ? f.X : Math.Min(xl.Value, f.X);
            else if (IsAxis(f.Nx, -1) && IsAxis(f.Ny, 0)) xr = xr is null ? f.X : Math.Max(xr.Value, f.X);
            else if (IsAxis(f.Nx, 0) && IsAxis(f.Ny, 1)) yb = yb is null ? f.Y : Math.Min(yb.Value, f.Y);
            else if (IsAxis(f.Nx, 0) && IsAxis(f.Ny, -1)) yt = yt is null ? f.Y : Math.Max(yt.Value, f.Y);
        }
        if (xl is null || xr is null || yb is null || yt is null) return null;

        var c = new Cavity(xl.Value, xr.Value, yb.Value, yt.Value);
        double sx = c.XRight - c.XLeft, sy = c.YTop - c.YBottom;
        if (sx < minSpanMm || sy < minSpanMm || sx > maxSpanMm || sy > maxSpanMm) return null;
        return c;
    }

    // Parametric slab test for one axis. Returns false when the bank is parallel to the axis and the end lies outside it.
    private static bool Slab(double p, double d, double a, double b, ref double lo, ref double hi)
    {
        if (Math.Abs(d) < 1e-9) return p >= a && p <= b;
        double t1 = (a - p) / d, t2 = (b - p) / d;
        if (t1 > t2) (t1, t2) = (t2, t1);
        lo = Math.Max(lo, t1);
        hi = Math.Min(hi, t2);
        return true;
    }

    private static bool IsAxis(double v, double target) => Math.Abs(v - target) < 0.005;
}
