using System.Linq;
using System.Windows;
using System.Windows.Media;

namespace THL.Host;

/// <summary>
/// 天河落 Logo 的几何：天河一滴水 × 0.618 黄金对数螺线。与网站 tianheluo-web/tools/make_logo.py 用同一套方程：
///   水珠：x = S·sin t·sin^φ(t/2)，y = S·cos t，t ∈ [0, 2π]，S = 200；
///   螺线：中心 (0, −0.236S)，r(θ) = 100·φ^(−2θ/π)，x = −r·sin θ，y = c_y + r·cos θ，逆时针旋入。
/// 坐标为 512×512 画布，Y 轴朝下，中心 (256, 262)。
/// </summary>
internal static class LogoGeometry
{
    const double Phi = 1.6180339887498949;   // (1 + √5) / 2
    const double S = 200.0, Cx = 256.0, Cy = 262.0, R0 = 100.0;
    const double SpiralCy = -0.236 * S;

    public static readonly Point[] Drop = BuildDrop(720);
    public static readonly Point[] SpiralMain = BuildSpiral(3.5 * Math.PI, 720);   // 主图版
    public static readonly Point[] SpiralSmall = BuildSpiral(2.3 * Math.PI, 720);  // 16–32 px 小尺寸版

    static Point[] BuildDrop(int n)
    {
        var pts = new Point[n];
        for (int i = 0; i < n; i++)
        {
            double t = 2 * Math.PI * i / n;
            double x = S * Math.Sin(t) * Math.Pow(Math.Sin(t / 2), Phi);   // sin(t/2) ≥ 0 on [0, 2π]
            double y = S * Math.Cos(t);
            pts[i] = new Point(Cx + x, Cy - y);
        }
        return pts;
    }

    static Point[] BuildSpiral(double thetaMax, int n)
    {
        var pts = new Point[n + 1];
        for (int i = 0; i <= n; i++)
        {
            double th = thetaMax * i / n;
            double r = R0 * Math.Pow(Phi, -2 * th / Math.PI);
            double x = -r * Math.Sin(th);
            double y = SpiralCy + r * Math.Cos(th);
            pts[i] = new Point(Cx + x, Cy - y);
        }
        return pts;
    }

    /// <summary>把一串点连成折线几何；closed 为 true 时是闭合填充的外形，否则是可描边的曲线。</summary>
    public static Geometry Polyline(Point[] pts, bool closed)
    {
        var fig = new PathFigure { StartPoint = pts[0], IsClosed = closed, IsFilled = closed };
        fig.Segments.Add(new PolyLineSegment(pts.Skip(1), true));
        var g = new PathGeometry();
        g.Figures.Add(fig);
        g.Freeze();
        return g;
    }
}
