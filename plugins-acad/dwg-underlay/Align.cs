// 自动对位：两组直线段（A = 要套进来的底图，B = 参照）求相似变换 B ≈ s·R(θ)·A + d。
// 思路沿用 Revit 插件 cad-align（按走向分组 + 位移投票），并加上缩放（Revit 导入 CAD 时常被缩放，比如本项目的 74.43 倍）：
//   1. 走向：两边按长度加权的角度直方图（0~180°）做循环互相关 → 旋转候选（加 180° 的翻转）；
//   2. 缩放：两边 ln(长度) 直方图互相关 → 缩放候选（不允许缩放时固定 1）；
//   3. 平移：每个 (θ, s) 下，A 的长线变换后和 B 里走向、长度都对得上的线配对，中点差在网格里投票，取票最多的格再求平均；
//   4. 验证：A 的长线变换后，中点附近（容差内）有同走向同长度的 B 线算「对上」，对上最多的那组解胜出。
using System;
using System.Collections.Generic;
using System.Linq;

namespace Thl.Acad.Underlay
{
    struct Seg
    {
        public double Ax, Ay, Bx, By, Len, Ang, Mx, My;
        public Seg(double ax, double ay, double bx, double by)
        {
            Ax = ax; Ay = ay; Bx = bx; By = by;
            var dx = bx - ax; var dy = by - ay;
            Len = Math.Sqrt(dx * dx + dy * dy);
            var a = Math.Atan2(dy, dx); if (a < 0) a += Math.PI; if (a >= Math.PI) a -= Math.PI;
            Ang = a; Mx = (ax + bx) / 2; My = (ay + by) / 2;
        }
    }

    sealed class AlignResult
    {
        public double Theta, Scale = 1, Dx, Dy;
        public int Matched, Tested;
        public double Rms;
        public double Ratio => Tested == 0 ? 0 : (double)Matched / Tested;
        public string Describe() =>
            $"旋转 {Theta * 180 / Math.PI:0.####}°，缩放 {Scale:0.######}，平移 ({Dx:0.##}, {Dy:0.##})；长线对上 {Matched}/{Tested}（{Ratio * 100:0.#}%），偏差均方根 {Rms:0.##}";
        public (double x, double y) Apply(double x, double y)
        {
            double c = Math.Cos(Theta) * Scale, s = Math.Sin(Theta) * Scale;
            return (x * c - y * s + Dx, x * s + y * c + Dy);
        }
    }

    static class Aligner
    {
        const int AngBins = 3600;            // 0.05°
        const double LnStep = 0.002;         // 0.2%

        static List<Seg> Longest(List<Seg> s, int n) => s.OrderByDescending(x => x.Len).Take(n).ToList();

        static double[] AngleHist(List<Seg> s)
        {
            var h = new double[AngBins];
            foreach (var x in s) { int i = (int)(x.Ang / Math.PI * AngBins) % AngBins; h[i] += x.Len; h[(i + 1) % AngBins] += x.Len * 0.5; h[(i + AngBins - 1) % AngBins] += x.Len * 0.5; }
            var sum = h.Sum(); if (sum > 0) for (int i = 0; i < AngBins; i++) h[i] /= sum;
            return h;
        }

        static List<double> TopPeaks(double[] score, int k, int sep, Func<int, double> toValue)
        {
            var res = new List<double>(); var used = new List<int>();
            foreach (var i in Enumerable.Range(0, score.Length).OrderByDescending(i => score[i]))
            {
                if (used.Any(u => Math.Min(Math.Abs(u - i), score.Length - Math.Abs(u - i)) < sep)) continue;
                used.Add(i); res.Add(toValue(i));
                if (res.Count >= k) break;
            }
            return res;
        }

        /// <summary>A → B。allowScale=false 时缩放固定 1。返回最好的解（可能对得很差，调用方看 Ratio）。</summary>
        public static AlignResult Solve(List<Seg> A, List<Seg> B, bool allowScale, Action<string> log)
        {
            var a = Longest(A, 3000); var b = Longest(B, 6000);
            if (a.Count < 4 || b.Count < 4) return null;
            // 1. 旋转候选
            var ha = AngleHist(a); var hb = AngleHist(b);
            var corr = new double[AngBins];
            for (int k = 0; k < AngBins; k++) { double s = 0; for (int i = 0; i < AngBins; i++) if (ha[i] != 0) s += ha[i] * hb[(i + k) % AngBins]; corr[k] = s; }
            var thetas = TopPeaks(corr, 3, 20, k => k * Math.PI / AngBins).SelectMany(t => new[] { t, t + Math.PI }).ToList();
            // 2. 缩放候选
            var scales = new List<double> { 1.0 };
            if (allowScale)
            {
                double lo = Math.Log(Math.Max(1e-9, Math.Min(a.Min(x => x.Len), b.Min(x => x.Len)))), hi = Math.Log(Math.Max(a.Max(x => x.Len), b.Max(x => x.Len)));
                int n = (int)((hi - lo) / LnStep) + 3;
                var la = new double[n]; var lb = new double[n];
                foreach (var x in a) la[(int)((Math.Log(x.Len) - lo) / LnStep)] += 1;
                foreach (var x in b) lb[(int)((Math.Log(x.Len) - lo) / LnStep)] += 1;
                int maxShift = n;
                var sc = new double[2 * maxShift + 1];
                for (int sh = -maxShift; sh <= maxShift; sh++)
                {
                    double s = 0;
                    for (int i = Math.Max(0, -sh); i < n && i + sh < n; i++)
                    {
                        if (la[i] == 0) continue;
                        int j = i + sh;
                        s += la[i] * (lb[j] + 0.5 * ((j > 0 ? lb[j - 1] : 0) + (j < n - 1 ? lb[j + 1] : 0)));
                    }
                    sc[sh + maxShift] = s;
                }
                scales = TopPeaks(sc, 4, 10, i => Math.Exp((i - maxShift) * LnStep));
                if (!scales.Any(s => Math.Abs(s - 1) < 0.002)) scales.Add(1.0);
            }
            // B 的空间索引（中点）和走向索引
            double bx0 = b.Min(x => x.Mx), bx1 = b.Max(x => x.Mx), by0 = b.Min(x => x.My), by1 = b.Max(x => x.My);
            double diag = Math.Sqrt((bx1 - bx0) * (bx1 - bx0) + (by1 - by0) * (by1 - by0));
            double tol = Math.Max(diag * 2e-4, 1e-6);
            var byAng = new List<Seg>[1800];
            for (int i = 0; i < 1800; i++) byAng[i] = new List<Seg>();
            foreach (var x in b) byAng[(int)(x.Ang / Math.PI * 1800) % 1800].Add(x);

            AlignResult best = null;
            var probe = a.Take(1500).ToList();
            foreach (var th in thetas)
                foreach (var s in scales)
                {
                    double c = Math.Cos(th) * s, si = Math.Sin(th) * s;
                    var votes = new Dictionary<(long, long), int>();
                    var ds = new List<(double, double)>();
                    foreach (var x in probe)
                    {
                        double mx = x.Mx * c - x.My * si, my = x.Mx * si + x.My * c;
                        double ang = (x.Ang + th) % Math.PI; if (ang < 0) ang += Math.PI;
                        double len = x.Len * s;
                        int bi = (int)(ang / Math.PI * 1800) % 1800;
                        for (int d = -1; d <= 1; d++)
                            foreach (var y in byAng[(bi + d + 1800) % 1800])
                            {
                                if (Math.Abs(y.Len - len) > 0.01 * y.Len) continue;
                                double ddx = y.Mx - mx, ddy = y.My - my;
                                var key = ((long)Math.Floor(ddx / tol), (long)Math.Floor(ddy / tol));
                                votes.TryGetValue(key, out var v); votes[key] = v + 1;
                                ds.Add((ddx, ddy));
                            }
                    }
                    if (votes.Count == 0) continue;
                    // 取 3x3 邻域票数最多的格
                    (long, long) peak = default; int pv = -1;
                    foreach (var kv in votes.Where(kv => kv.Value >= 2 || votes.Count < 50))
                    {
                        int sum = 0;
                        for (int i = -1; i <= 1; i++) for (int j = -1; j <= 1; j++) if (votes.TryGetValue((kv.Key.Item1 + i, kv.Key.Item2 + j), out var vv)) sum += vv;
                        if (sum > pv) { pv = sum; peak = kv.Key; }
                    }
                    if (pv < 0) continue;
                    double cx = (peak.Item1 + 0.5) * tol, cy = (peak.Item2 + 0.5) * tol;
                    var near = ds.Where(d => Math.Abs(d.Item1 - cx) <= 2 * tol && Math.Abs(d.Item2 - cy) <= 2 * tol).ToList();
                    if (near.Count == 0) continue;
                    var r = new AlignResult { Theta = th, Scale = s, Dx = near.Average(d => d.Item1), Dy = near.Average(d => d.Item2) };
                    Score(r, probe, byAng, tol);
                    if (best == null || r.Matched > best.Matched) best = r;
                }
            if (best != null)
            {
                // 用对上的线微调缩放和平移（最小二乘，中点对中点）
                Refine(best, probe, byAng, tol, allowScale);
                Score(best, probe, byAng, tol);
                best.Theta %= 2 * Math.PI;
                log?.Invoke("自动对位：" + best.Describe() + $"（容差 {tol:0.##}）");
            }
            return best;
        }

        static IEnumerable<(Seg a, Seg b)> Pairs(AlignResult r, List<Seg> probe, List<Seg>[] byAng, double tol)
        {
            foreach (var x in probe)
            {
                var (mx, my) = r.Apply(x.Mx, x.My);
                double ang = (x.Ang + r.Theta) % Math.PI; if (ang < 0) ang += Math.PI;
                double len = x.Len * r.Scale;
                int bi = (int)(ang / Math.PI * 1800) % 1800;
                Seg? hit = null; double bd = double.MaxValue;
                for (int d = -1; d <= 1; d++)
                    foreach (var y in byAng[(bi + d + 1800) % 1800])
                    {
                        if (Math.Abs(y.Len - len) > 0.02 * y.Len + tol) continue;
                        double dd = (y.Mx - mx) * (y.Mx - mx) + (y.My - my) * (y.My - my);
                        if (dd < bd) { bd = dd; hit = y; }
                    }
                if (hit.HasValue && Math.Sqrt(bd) <= tol * 3) yield return (x, hit.Value);
            }
        }

        static void Score(AlignResult r, List<Seg> probe, List<Seg>[] byAng, double tol)
        {
            int n = 0; double e2 = 0;
            foreach (var (x, y) in Pairs(r, probe, byAng, tol))
            {
                var (mx, my) = r.Apply(x.Mx, x.My);
                e2 += (y.Mx - mx) * (y.Mx - mx) + (y.My - my) * (y.My - my); n++;
            }
            r.Matched = n; r.Tested = probe.Count; r.Rms = n > 0 ? Math.Sqrt(e2 / n) : double.NaN;
        }

        /// <summary>中点对中点的相似变换最小二乘（Umeyama 2D）。</summary>
        static void Refine(AlignResult r, List<Seg> probe, List<Seg>[] byAng, double tol, bool allowScale)
        {
            var pr = Pairs(r, probe, byAng, tol).ToList();
            if (pr.Count < 3) return;
            double ax = pr.Average(p => p.a.Mx), ay = pr.Average(p => p.a.My), bx = pr.Average(p => p.b.Mx), by = pr.Average(p => p.b.My);
            double sxx = 0, sxy = 0, va = 0;
            foreach (var (p, q) in pr)
            {
                double x1 = p.Mx - ax, y1 = p.My - ay, x2 = q.Mx - bx, y2 = q.My - by;
                sxx += x1 * x2 + y1 * y2; sxy += x1 * y2 - y1 * x2; va += x1 * x1 + y1 * y1;
            }
            double th = Math.Atan2(sxy, sxx);
            double s = allowScale && va > 0 ? Math.Sqrt(sxx * sxx + sxy * sxy) / va : 1;
            r.Theta = th; r.Scale = s;
            double c = Math.Cos(th) * s, si = Math.Sin(th) * s;
            r.Dx = bx - (ax * c - ay * si); r.Dy = by - (ax * si + ay * c);
        }

        /// <summary>两点对位：p1→q1、p2→q2（allowScale=false 时只转不缩，按两点中点对齐）。</summary>
        public static AlignResult TwoPoint(double p1x, double p1y, double p2x, double p2y, double q1x, double q1y, double q2x, double q2y, bool allowScale)
        {
            double th = Math.Atan2(q2y - q1y, q2x - q1x) - Math.Atan2(p2y - p1y, p2x - p1x);
            double lp = Math.Sqrt((p2x - p1x) * (p2x - p1x) + (p2y - p1y) * (p2y - p1y)), lq = Math.Sqrt((q2x - q1x) * (q2x - q1x) + (q2y - q1y) * (q2y - q1y));
            double s = allowScale && lp > 1e-9 ? lq / lp : 1;
            double c = Math.Cos(th) * s, si = Math.Sin(th) * s;
            double pmx = (p1x + p2x) / 2, pmy = (p1y + p2y) / 2, qmx = (q1x + q2x) / 2, qmy = (q1y + q2y) / 2;
            return new AlignResult { Theta = th, Scale = s, Dx = qmx - (pmx * c - pmy * si), Dy = qmy - (pmx * si + pmy * c), Matched = 2, Tested = 2, Rms = 0 };
        }
    }
}
