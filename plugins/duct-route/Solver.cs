using Google.OrTools.Sat;

namespace KaifaTools.DuctRoute;

/// <summary>
/// Chooses the two end elevations of every movable bank. Plan positions never change.
/// Model (CP-SAT, integer millimetres): for every obstacle a bank is ABOVE it, BELOW it or violates it (heavily penalised);
/// the clearance of a sloped bank over an obstacle is linearised with convex hulls; joints between banks are tied;
/// the objective keeps banks close to their original level, flat where they were flat, and avoids going deeper.
/// </summary>
public sealed class RouteSolver
{
    readonly Scene S; readonly RouteRules R; readonly Action<string> Log;
    readonly List<Samp> _fix = new();                       // wells, drains, fixed banks, (pipes)
    readonly Dictionary<long, BoxObs> _wellById = new();
    readonly Dictionary<(long, long), int> _padPair = new();
    const int PadFix = 2, PadPair0 = 25;
    long Wup => (long)R.WeightUp; long Wdown => (long)R.WeightDown;
    const long WViol = 1_000_000, WCover = 5000, WTie = 20000, WSlope = 1;

    public RouteSolver(Scene s, RouteRules r, Action<string> log)
    {
        S = s; R = r; Log = log;
        foreach (var w in s.Wells) { _fix.AddRange(Geo.BoxSamples(w)); _wellById[w.Id] = w; }
        foreach (var d in s.Drains) _fix.AddRange(Geo.BoxSamples(d));
        foreach (var b in s.Banks.Where(x => !x.Movable)) _fix.AddRange(Geo.BankSamples(b, b.O0, b.O1));
        foreach (var p in s.Pipes)
        {
            double dx = p.B[0] - p.A[0], dy = p.B[1] - p.A[1], dn = Math.Sqrt(dx * dx + dy * dy);
            double len = Math.Sqrt(dx * dx + dy * dy + Math.Pow(p.B[2] - p.A[2], 2)); int n = Math.Max(2, (int)(len / 0.25) + 1);
            for (int i = 0; i < n; i++)
            {
                double t = (double)i / (n - 1);
                _fix.Add(new Samp(p.A[0] + dx * t, p.A[1] + dy * t, p.A[2] + (p.B[2] - p.A[2]) * t, 0, p.R, dn > 1e-6 ? dx / dn : 0, dn > 1e-6 ? dy / dn : 0, dn > 1e-6, -1));
            }
        }
    }

    // ------------------------------------------------------------------ geometry of one bank vs a set of samples
    (bool[] act, double[] ve, double[] tt) Geom(Bank b, List<Samp> O)
    {
        int n = O.Count; var act = new bool[n]; var ve = new double[n]; var tt = new double[n];
        double crossCos = Math.Cos(R.CrossAngleDeg * Math.PI / 180);
        for (int i = 0; i < n; i++)
        {
            var s = O[i]; double rx = s.X - b.Ax, ry = s.Y - b.Ay;
            double sl = rx * b.Ux + ry * b.Uy, tl = rx * b.Nx + ry * b.Ny;
            double dh = Math.Sqrt(Math.Pow(Math.Max(Math.Max(-sl, sl - b.L), 0), 2) + Math.Pow(Math.Max(Math.Abs(tl) - b.W / 2, 0), 2));
            bool cross = !s.HasDir || Math.Abs(s.Dx * b.Ux + s.Dy * b.Uy) < crossCos;
            double Rr = s.R + (cross ? R.ClearCrossM : R.ClearParallelM);
            act[i] = dh < Rr; ve[i] = s.Hz + Math.Sqrt(Math.Max(Rr * Rr - dh * dh, 0)); tt[i] = Math.Min(Math.Max(sl, 0), b.L) / b.L;
        }
        return (act, ve, tt);
    }

    bool InLinkWell(Bank bank, Samp s, double e = 0.3)
    {
        foreach (var (w, _, _) in bank.Links) if (s.X >= w.MinX - e && s.X <= w.MaxX + e && s.Y >= w.MinY - e && s.Y <= w.MaxY + e) return true;
        return false;
    }

    /// <summary>Does the centreline of the bank lie inside the well only within the first/last EndZone metres (well-on-well overlap)?</summary>
    bool WellAtBankEnd(Bank b, BoxObs w)
    {
        double tmin = double.MaxValue, tmax = double.MinValue;
        for (double t = 0; t <= b.L + 1e-9; t += 0.1)
        {
            double x = b.Ax + t * b.Ux, y = b.Ay + t * b.Uy;
            if (x >= w.MinX && x <= w.MaxX && y >= w.MinY && y <= w.MaxY) { tmin = Math.Min(tmin, t); tmax = Math.Max(tmax, t); }
        }
        return tmax >= 0 && (tmax < R.EndZoneM || tmin > b.L - R.EndZoneM);
    }

    sealed class Prep
    {
        public List<(double t, double v)> Cover = new();
        public Dictionary<long, (List<(double, double)> above, List<(double, double)> below)> Fixed = new();
        public Dictionary<long, List<(double tb, double to, double sq)>> Pairs = new();
    }

    Prep PrepBank(Bank b, HashSet<long> movIds)
    {
        double lox = Math.Min(b.Ax, b.Bx) - b.W / 2 - 1.2, hix = Math.Max(b.Ax, b.Bx) + b.W / 2 + 1.2;
        double loy = Math.Min(b.Ay, b.By) - b.W / 2 - 1.2, hiy = Math.Max(b.Ay, b.By) + b.W / 2 + 1.2;
        var O = new List<Samp>();
        var farWells = new HashSet<long>();
        foreach (var w in S.Wells)
            if (!b.Wid.Contains(w.Id) && !(w.MaxX < lox || w.MinX > hix || w.MaxY < loy || w.MinY > hiy) && WellAtBankEnd(b, w)) farWells.Add(w.Id);
        foreach (var s in _fix)
        {
            if (s.X < lox || s.X > hix || s.Y < loy || s.Y > hiy) continue;
            if (b.Wid.Contains(s.Id) || farWells.Contains(s.Id)) continue;
            if (_wellById.TryGetValue(s.Id, out var w))
            {
                double d = Math.Min(Math.Min(s.X - w.MinX, w.MaxX - s.X), Math.Min(s.Y - w.MinY, w.MaxY - s.Y));
                if (d < R.WellTolM) continue;
            }
            O.Add(s);
        }
        foreach (var o in S.Banks.Where(x => x.Movable && !movIds.Contains(x.Id) && x.Id != b.Id))   // frozen movable banks sit at their current level
            foreach (var s in Geo.BankSamples(o, o.Z0, o.Z1))
            {
                if (s.X < lox || s.X > hix || s.Y < loy || s.Y > hiy) continue;
                bool nearJ = false; foreach (var (jid, px, py) in b.Junc) if (jid == o.Id && Math.Sqrt(Math.Pow(s.X - px, 2) + Math.Pow(s.Y - py, 2)) <= 1.5) nearJ = true;
                if (!nearJ) O.Add(s);
            }
        O = O.Where(s => !InLinkWell(b, s)).ToList();
        var g = Geom(b, O); var p = new Prep();
        var ab = new Dictionary<long, List<(double, double)>>(); var be = new Dictionary<long, List<(double, double)>>();
        for (int i = 0; i < O.Count; i++)
        {
            if (!g.act[i]) continue; var s = O[i];
            if (!ab.ContainsKey(s.Id)) { ab[s.Id] = new(); be[s.Id] = new(); }
            ab[s.Id].Add((g.tt[i], s.Zc + g.ve[i])); be[s.Id].Add((g.tt[i], s.Zc - g.ve[i] - b.H));
        }
        foreach (var id in ab.Keys) p.Fixed[id] = (Geo.HullUpper(ab[id]), Geo.HullLower(be[id]));
        // movable counterparts (their level is a variable too)
        foreach (var oid in movIds)
        {
            if (oid <= b.Id) continue; var o = S.ById[oid];
            var sm = Geo.BankSamples(o, o.O0, o.O1).Where(s => s.X >= lox && s.X <= hix && s.Y >= loy && s.Y <= hiy).ToList();
            sm = sm.Where(s => { foreach (var (jid, px, py) in b.Junc) if (jid == oid && Math.Sqrt(Math.Pow(s.X - px, 2) + Math.Pow(s.Y - py, 2)) <= 1.5) return false; return true; }).ToList();
            sm = sm.Where(s => !InLinkWell(b, s) && !InLinkWell(o, s)).ToList();
            if (sm.Count == 0) continue;
            var g2 = Geom(b, sm); var best = new Dictionary<int, (double tb, double to, double sq)>();
            for (int i = 0; i < sm.Count; i++)
            {
                if (!g2.act[i]) continue; var s = sm[i];
                double to = Math.Min(Math.Max(((s.X - o.Ax) * o.Ux + (s.Y - o.Ay) * o.Uy) / o.L, 0), 1);
                double sq = g2.ve[i] - s.Hz; int key = (int)Math.Round(g2.tt[i] * 48);
                if (!best.TryGetValue(key, out var cur) || sq > cur.sq) best[key] = (g2.tt[i], to, sq);
            }
            if (best.Count > 0) p.Pairs[oid] = best.Values.ToList();
        }
        // cover limit along the bank (top of bank must stay below this)
        int ns = Math.Max(2, (int)(b.L / 0.25) + 1); var lim = new List<(double, double)>();
        for (int i = 0; i < ns; i++)
        {
            double sv = b.L * i / (ns - 1), v = double.MaxValue;
            foreach (var tv in new[] { -b.W / 2, 0, b.W / 2 })
            {
                double x = b.Ax + sv * b.Ux + tv * b.Nx, y = b.Ay + sv * b.Uy + tv * b.Ny;
                var rin = Geo.RoadTop(S, x, y);
                v = Math.Min(v, rin is { } r0 ? r0 - R.CoverRoadM : Geo.Ground(S, R, x, y) - R.CoverOffM);
            }
            lim.Add((sv / b.L, v - b.H));
        }
        p.Cover = Geo.HullLower(lim);
        return p;
    }

    // ------------------------------------------------------------------ one CP-SAT solve
    public sealed class Outcome { public Dictionary<long, (double z0, double z1)> Z = new(); public HashSet<long> Violating = new(); public string Status = ""; }

    public Outcome Solve(HashSet<long> frozen, int seconds, Dictionary<long, (double, double)>? hint)
    {
        var mov = S.Movable.Where(b => !frozen.Contains(b.Id)).ToList(); var movIds = mov.Select(b => b.Id).ToHashSet();
        var m = new CpModel(); var V = new Dictionary<long, (LinearExpr z0, LinearExpr z1)>(); var U = new Dictionary<long, (IntVar u0, IntVar u1)>();
        long range = (long)Math.Round(R.SearchRangeM * 1000), floor = (long)Math.Round(R.MinBottomM * 1000); long step = Math.Max(1, R.StepMm);
        foreach (var b in mov)
        {
            long o0 = (long)Math.Round(b.O0 * 1000), o1 = (long)Math.Round(b.O1 * 1000);
            // lowest allowed level at each end: not deeper than MaxDepth below the ground there, and not below MinBottom; an end already deeper keeps its level
            long lo0 = Math.Max(Math.Max(floor, (long)Math.Ceiling((Geo.Ground(S, R, b.Ax, b.Ay) - R.MaxDepthM) * 1000)), o0 - range); lo0 = Math.Min(lo0, o0);
            long lo1 = Math.Max(Math.Max(floor, (long)Math.Ceiling((Geo.Ground(S, R, b.Bx, b.By) - R.MaxDepthM) * 1000)), o1 - range); lo1 = Math.Min(lo1, o1);
            long hi0 = o0 + range, hi1 = o1 + range;
            var u0 = m.NewIntVar((lo0 + step - 1) / step, Math.Max((lo0 + step - 1) / step, hi0 / step), $"u0_{b.Id}");
            var u1 = m.NewIntVar((lo1 + step - 1) / step, Math.Max((lo1 + step - 1) / step, hi1 / step), $"u1_{b.Id}");
            U[b.Id] = (u0, u1); V[b.Id] = (step * u0, step * u1);
        }
        var terms = new List<(LinearExpr e, long c)>(); var violVars = new List<(long bank, BoolVar x)>();
        foreach (var b in mov)
        {
            var (z0, z1) = V[b.Id]; long o0 = (long)Math.Round(b.O0 * 1000), o1 = (long)Math.Round(b.O1 * 1000);
            foreach (var (z, o) in new[] { (z0, o0), (z1, o1) })
            {
                var up = m.NewIntVar(0, range, ""); var dn = m.NewIntVar(0, range, "");
                m.Add(z - o == up - dn); terms.Add((up, Wup)); terms.Add((dn, Wdown));
            }
            var ds = m.NewIntVar(-2 * range, 2 * range, ""); m.Add(ds == (z1 - z0) - (o1 - o0));
            var ads = m.NewIntVar(0, 2 * range, ""); m.AddAbsEquality(ads, ds);
            var nf = m.NewBoolVar(""); m.Add(ads == 0).OnlyEnforceIf(nf.Not()); m.Add(ads >= 1).OnlyEnforceIf(nf);
            terms.Add((nf, (long)R.WeightNonFlat)); terms.Add((ads, WSlope));
            long smax = (long)(Math.Max(R.MaxSlopePct / 100.0, Math.Abs(b.O1 - b.O0) / b.L) * b.L * 1000);
            m.Add(z1 - z0 <= smax); m.Add(z0 - z1 <= smax);
            var p = PrepBank(b, movIds);
            var cs = m.NewIntVar(0, 6000, "");
            foreach (var (t, rhs) in p.Cover) { if (double.IsInfinity(rhs)) continue; long ti = (long)Math.Round(t * 1000); m.Add((1000 - ti) * z0 + ti * z1 <= (long)(rhs * 1e6) + cs * 1000); }
            terms.Add((cs, WCover));
            foreach (var (oid, (above, below)) in p.Fixed)
            {
                var A = m.NewBoolVar(""); var B = m.NewBoolVar(""); var X = m.NewBoolVar(""); m.AddExactlyOne(new ILiteral[] { A, B, X });
                terms.Add((X, WViol)); violVars.Add((b.Id, X));
                foreach (var (t, c) in above) { long ti = (long)Math.Round(t * 1000); m.Add((1000 - ti) * z0 + ti * z1 >= (long)Math.Ceiling((c * 1000 + PadFix) * 1000)).OnlyEnforceIf(A); }
                foreach (var (t, d) in below) { long ti = (long)Math.Round(t * 1000); m.Add((1000 - ti) * z0 + ti * z1 <= (long)Math.Floor((d * 1000 - PadFix) * 1000)).OnlyEnforceIf(B); }
            }
            foreach (var (oid, samp) in p.Pairs)
            {
                var (y0, y1) = V[oid]; double ho = S.ById[oid].H; int pp = _padPair.TryGetValue((b.Id, oid), out var pv) ? pv : PadPair0;
                var A = m.NewBoolVar(""); var B = m.NewBoolVar(""); var X = m.NewBoolVar(""); m.AddExactlyOne(new ILiteral[] { A, B, X });
                terms.Add((X, WViol)); violVars.Add((b.Id, X));
                foreach (var (tb, to, sq) in samp)
                {
                    long ib = (long)Math.Round(tb * 1000), io = (long)Math.Round(to * 1000);
                    var lhs = (1000 - ib) * z0 + ib * z1 - ((1000 - io) * y0 + io * y1);
                    m.Add(lhs >= (long)Math.Ceiling((ho * 1000 + sq * 1000 + pp) * 1000)).OnlyEnforceIf(A);
                    m.Add(lhs <= (long)Math.Floor((-b.H * 1000 - sq * 1000 - pp) * 1000)).OnlyEnforceIf(B);
                }
            }
            foreach (var (ties, zv) in new[] { (b.Nb0, z0), (b.Nb1, z1) })
                foreach (var tie in ties)
                {
                    long tol = (long)Math.Round(tie.TolM * 1000); LinearExpr zo;
                    if (V.TryGetValue(tie.OtherId, out var ov)) zo = tie.OtherStart ? ov.z0 : ov.z1;
                    else { var ob = S.ById[tie.OtherId]; zo = LinearExpr.Constant((long)Math.Round((tie.OtherStart ? ob.Z0 : ob.Z1) * 1000)); }
                    var sl = m.NewIntVar(0, 8000, ""); m.Add(zv - zo <= tol + sl); m.Add(zo - zv <= tol + sl); terms.Add((sl, WTie));
                }
        }
        if (hint is not null) foreach (var b in mov) if (hint.TryGetValue(b.Id, out var h)) { m.AddHint(U[b.Id].u0, (long)Math.Round(h.Item1 * 1000 / step)); m.AddHint(U[b.Id].u1, (long)Math.Round(h.Item2 * 1000 / step)); }
        m.Minimize(LinearExpr.WeightedSum(terms.Select(t => t.e), terms.Select(t => t.c)));
        var solver = new CpSolver { StringParameters = $"max_time_in_seconds:{seconds} num_workers:{Math.Max(2, Environment.ProcessorCount - 1)}" };
        var st = solver.Solve(m); var oc = new Outcome { Status = st.ToString() };
        if (st != CpSolverStatus.Optimal && st != CpSolverStatus.Feasible) return oc;
        foreach (var (id, (a, c)) in U) oc.Z[id] = (solver.Value(a) * step / 1000.0, solver.Value(c) * step / 1000.0);
        foreach (var (bid, x) in violVars) if (solver.Value(x) == 1) oc.Violating.Add(bid);
        return oc;
    }

    // ------------------------------------------------------------------ whole run: solve, freeze the unsolvable, verify, revert
    public void Run(Action<string> progress, CancellationToken ct)
    {
        foreach (var b in S.Movable) { b.Z0 = b.O0; b.Z1 = b.O1; b.Frozen = false; b.Status = ""; }
        var frozen = new HashSet<long>(); Dictionary<long, (double, double)>? hint = null; int lastResid = -1, same = 0;
        for (int rnd = 0; rnd < Math.Max(2, R.Rounds); rnd++)
        {
            ct.ThrowIfCancellationRequested();
            progress($"第 {rnd + 1} 轮求解（{S.Movable.Count() - frozen.Count} 根可调，已定位不动 {frozen.Count}）…");
            var oc = Solve(frozen, R.SecondsPerRound, hint);
            if (oc.Z.Count == 0) { progress(oc.Status is "Optimal" or "Feasible" ? "没有能调的排管了（剩下的靠标高都解不开，保持原位）" : "求解失败：" + oc.Status); break; }
            if (oc.Violating.Count > 0) { foreach (var id in oc.Violating) { frozen.Add(id); var b = S.ById[id]; b.Z0 = b.O0; b.Z1 = b.O1; } continue; }
            hint = oc.Z.ToDictionary(k => k.Key, k => (k.Value.z0, k.Value.z1));
            foreach (var (id, z) in oc.Z) { var b = S.ById[id]; b.Z0 = z.z0; b.Z1 = z.z1; }
            var res = Verify(); int fix = 0;
            foreach (var (id, issues) in res)
                foreach (var (kind, oid, _) in issues)
                    if (kind == "排管" && S.ById.TryGetValue(oid, out var ob) && ob.Movable && !frozen.Contains(id) && !frozen.Contains(oid))
                    {
                        if (_padPair.TryGetValue((id, oid), out var cur) && cur >= PadPair0 + 120) continue;   // already padded 3 times: leave it to the revert step
                        foreach (var pr in new[] { (id, oid), (oid, id) }) _padPair[pr] = (_padPair.TryGetValue(pr, out var v) ? v : PadPair0) + 40; fix++;
                    }
            progress($"第 {rnd + 1} 轮复核：仍有问题的排管 {res.Count} 根，需加余量的排管对 {fix}");
            same = res.Count == lastResid ? same + 1 : 0; lastResid = res.Count;
            if (fix == 0 || same >= 2) break;
        }
        foreach (var b in S.Movable) if (frozen.Contains(b.Id)) { b.Z0 = b.O0; b.Z1 = b.O1; b.Frozen = true; }
        // revert anything changed that still has a problem, then anything that broke a joint
        for (int it = 0; it < 10; it++)
        {
            var res = Verify(); var rev = new HashSet<long>();
            foreach (var (id, issues) in res)
            {
                var b = S.ById[id]; if (b.Changed) rev.Add(id);
                foreach (var (kind, oid, _) in issues) if (kind == "排管" && S.ById.TryGetValue(oid, out var ob) && ob.Movable && ob.Changed && !b.Changed) rev.Add(oid);
            }
            foreach (var b in S.Movable)
                foreach (var (ties, isStart) in new[] { (b.Nb0, true), (b.Nb1, false) })
                    foreach (var t in ties)
                    {
                        if (!S.ById.TryGetValue(t.OtherId, out var o)) continue;
                        if (Math.Abs((isStart ? b.Z0 : b.Z1) - (t.OtherStart ? o.Z0 : o.Z1)) > t.TolM + 0.0015) { if (b.Changed) rev.Add(b.Id); if (o.Movable && o.Changed) rev.Add(o.Id); }
                    }
            if (rev.Count == 0) break;
            foreach (var id in rev) { var b = S.ById[id]; b.Z0 = b.O0; b.Z1 = b.O1; b.Frozen = true; }
            progress($"撤回 {rev.Count} 根有问题的改动 …");
        }
        var final = Verify();
        foreach (var b in S.Movable)
        {
            double ex = CoverExcess(b);
            b.Status = final.ContainsKey(b.Id) ? (b.Changed ? "有碰撞" : "未改·仍有碰撞") : ex > 0.005 ? (b.Changed ? "覆土不足" : "未改·覆土不足") : b.Changed ? "已优化" : "无需改";
        }
    }

    Bank? _only;
    public List<(string kind, long other, double dist)> VerifyBank(Bank b)
    {
        _only = b;
        try { return Verify().TryGetValue(b.Id, out var l) ? l : new(); } finally { _only = null; }
    }

    static long Key(long id, int end) => id * 2 + end;

    /// <summary>
    /// Tool "标高取整 / 接头对齐": every group of tied ends (a joint, or the two halves of a cut line) gets ONE common level.
    /// snap = true puts that level on the StepMm grid (nearest, then one step above / below); snap = false keeps the plain mean.
    /// A change is only kept when the banks involved have no more problems than before and still meet floor, slope and cover.
    /// </summary>
    public (int groups, int changed, int refused) Normalize(bool snap, Action<string> progress)
    {
        double stepM = Math.Max(1, R.StepMm) / 1000.0; var par = new Dictionary<long, long>();
        long Find(long x) { while (par[x] != x) { par[x] = par[par[x]]; x = par[x]; } return x; }
        foreach (var b in S.Movable) { par[Key(b.Id, 0)] = Key(b.Id, 0); par[Key(b.Id, 1)] = Key(b.Id, 1); }
        foreach (var b in S.Movable)
            foreach (var (ties, e) in new[] { (b.Nb0, 0), (b.Nb1, 1) })
                foreach (var t in ties)
                    if (S.ById.TryGetValue(t.OtherId, out var o) && o.Movable) par[Find(Key(b.Id, e))] = Find(Key(o.Id, t.OtherStart ? 0 : 1));
        var groups = new Dictionary<long, List<long>>();
        foreach (var k in par.Keys) groups.GetOrAdd(Find(k), () => new()).Add(k);
        double Get(long k) { var b = S.ById[k / 2]; return k % 2 == 0 ? b.Z0 : b.Z1; }
        void Set(long k, double v) { var b = S.ById[k / 2]; if (k % 2 == 0) b.Z0 = v; else b.Z1 = v; }
        int changed = 0, refused = 0, n = 0;
        foreach (var g in groups.Values)
        {
            n++;
            var vals = g.Select(Get).ToList(); double mean = vals.Average();
            var targets = new List<double>();
            if (snap) { double r = Math.Round(mean / stepM) * stepM; targets.AddRange(new[] { r, r + stepM, r - stepM }); }
            else targets.Add(mean);
            if (vals.All(v => Math.Abs(v - targets[0]) < 1e-6)) continue;       // nothing to do
            var banks = g.Select(k => S.ById[k / 2]).Distinct().ToList();
            var before = g.Select(k => (k, Get(k))).ToList();
            int baseIssues = banks.Sum(b => VerifyBank(b).Count); double baseCover = banks.Max(b => CoverExcess(b));
            bool done = false;
            foreach (var t in targets.OrderBy(t => Math.Abs(t - mean)))
            {
                foreach (var k in g) Set(k, t);
                bool ok = banks.All(b => StaticOk(b)) && banks.Max(b => CoverExcess(b)) <= Math.Max(baseCover, 0.003) + 1e-6 && banks.Sum(b => VerifyBank(b).Count) <= baseIssues;
                if (ok) { done = true; changed++; break; }
                foreach (var (k, v) in before) Set(k, v);
            }
            if (!done) refused++;
        }
        // single ends that are not tied to anything: snap them alone
        if (snap)
            foreach (var b in S.Movable)
                foreach (var e in new[] { 0, 1 })
                {
                    if (par.TryGetValue(Key(b.Id, e), out var root) && groups[Find(Key(b.Id, e))].Count > 1) continue;
                    double v = e == 0 ? b.Z0 : b.Z1, r = Math.Round(v / stepM) * stepM; if (Math.Abs(v - r) < 1e-6) continue;
                    int baseIssues = VerifyBank(b).Count; double baseCover = CoverExcess(b);
                    bool done = false;
                    foreach (var t in new[] { r, r + stepM, r - stepM }.OrderBy(t => Math.Abs(t - v)))
                    {
                        Set(Key(b.Id, e), t);
                        if (StaticOk(b) && CoverExcess(b) <= Math.Max(baseCover, 0.003) + 1e-6 && VerifyBank(b).Count <= baseIssues) { done = true; changed++; break; }
                    }
                    if (!done) { Set(Key(b.Id, e), v); refused++; }
                }
        progress($"{(snap ? "取整" : "对齐")}：{n} 组接头/端点，改 {changed}，没改成 {refused}");
        return (n, changed, refused);
    }

    bool StaticOk(Bank b)
    {
        long floor = (long)Math.Round(R.MinBottomM * 1000);
        double f0 = Math.Max(R.MinBottomM, Geo.Ground(S, R, b.Ax, b.Ay) - R.MaxDepthM), f1 = Math.Max(R.MinBottomM, Geo.Ground(S, R, b.Bx, b.By) - R.MaxDepthM);
        if (b.Z0 < Math.Min(f0, b.O0) - 1e-6 || b.Z1 < Math.Min(f1, b.O1) - 1e-6) return false;
        double smax = Math.Max(R.MaxSlopePct / 100.0, Math.Abs(b.O1 - b.O0) / b.L) * b.L;
        return Math.Abs(b.Z1 - b.Z0) <= smax + 1e-6;
    }

    public double CoverExcess(Bank b)
    {
        var p = PrepBank(b, new HashSet<long>());
        double ex = double.MinValue;
        foreach (var (t, lim) in p.Cover) if (!double.IsInfinity(lim)) ex = Math.Max(ex, b.Z0 * (1 - t) + b.Z1 * t - lim);
        return ex;
    }

    // ------------------------------------------------------------------ independent check with exact distances
    public Dictionary<long, List<(string kind, long other, double dist)>> Verify()
    {
        var res = new Dictionary<long, List<(string, long, double)>>();
        double crossCos = Math.Cos(R.CrossAngleDeg * Math.PI / 180);
        foreach (var b in (_only is null ? S.Movable : new[] { _only }))
        {
            int ns = Math.Max(2, (int)(b.L / 0.2) + 1), nt = Math.Max(2, (int)(b.W / 0.2) + 1), nz = Math.Max(2, (int)(b.H / 0.2) + 1);
            var P = new List<(double x, double y, double z)>(ns * nt * nz);
            for (int i = 0; i < ns; i++)
            {
                double sv = b.L * i / (ns - 1), zb = b.Z0 + (b.Z1 - b.Z0) * sv / b.L;
                for (int j = 0; j < nt; j++) { double tv = -b.W / 2 + b.W * j / (nt - 1); for (int k = 0; k < nz; k++) P.Add((b.Ax + sv * b.Ux + tv * b.Nx, b.Ay + sv * b.Uy + tv * b.Ny, zb + b.H * k / (nz - 1))); }
            }
            double lox = P.Min(p => p.x) - 0.4, hix = P.Max(p => p.x) + 0.4, loy = P.Min(p => p.y) - 0.4, hiy = P.Max(p => p.y) + 0.4, loz = P.Min(p => p.z) - 0.4, hiz = P.Max(p => p.z) + 0.4;
            var worst = new Dictionary<(string, long), double>();
            void Note(string kind, long id, double d, double thr) { if (d < thr) { var k = (kind, id); if (!worst.TryGetValue(k, out var c) || d < c) worst[k] = d; } }
            foreach (var w in S.Wells)
            {
                if (b.Wid.Contains(w.Id)) continue;
                double wx0 = w.MinX + R.WellTolM, wy0 = w.MinY + R.WellTolM, wx1 = w.MaxX - R.WellTolM, wy1 = w.MaxY - R.WellTolM;
                if (wx0 > wx1 || wy0 > wy1 || wx0 > hix || wx1 < lox || wy0 > hiy || wy1 < loy || w.MinZ > hiz || w.MaxZ < loz) continue;
                if (WellAtBankEnd(b, w)) continue;
                Note("井", w.Id, BoxDist(P, wx0, wy0, w.MinZ, wx1, wy1, w.MaxZ), 0.001);
            }
            foreach (var d in S.Drains)
            {
                if (d.MinX > hix || d.MaxX < lox || d.MinY > hiy || d.MaxY < loy || d.MinZ > hiz || d.MaxZ < loz) continue;
                Note("雨水沟", d.Id, BoxDist(P, d.MinX, d.MinY, d.MinZ, d.MaxX, d.MaxY, d.MaxZ), 0.001);
            }
            foreach (var o in S.Banks)
            {
                if (o.Id == b.Id || b.Junc.Any(j => j.Id == o.Id)) continue;
                if (Math.Min(o.Ax, o.Bx) - o.W / 2 - 1 > hix || Math.Max(o.Ax, o.Bx) + o.W / 2 + 1 < lox || Math.Min(o.Ay, o.By) - o.W / 2 - 1 > hiy || Math.Max(o.Ay, o.By) + o.W / 2 + 1 < loy) continue;
                double d = double.MaxValue;
                foreach (var q in P)
                {
                    double rx = q.x - o.Ax, ry = q.y - o.Ay, s = rx * o.Ux + ry * o.Uy, t = rx * o.Nx + ry * o.Ny;
                    double dh = Math.Sqrt(Math.Pow(Math.Max(Math.Max(-s, s - o.L), 0), 2) + Math.Pow(Math.Max(Math.Abs(t) - o.W / 2, 0), 2));
                    double sc = Math.Min(Math.Max(s, 0), o.L) / o.L, bot = o.Z0 * (1 - sc) + o.Z1 * sc, top = bot + o.H;
                    double vg = Math.Max(Math.Max(bot - q.z, q.z - top), 0); d = Math.Min(d, Math.Sqrt(dh * dh + vg * vg));
                }
                bool cr = Math.Abs(o.Ux * b.Ux + o.Uy * b.Uy) < crossCos;
                Note("排管", o.Id, d, cr ? R.ClearCrossM - 0.02 : 0.001);
            }
            if (worst.Count > 0) res[b.Id] = worst.Select(k => (k.Key.Item1, k.Key.Item2, k.Value)).ToList();
        }
        return res;
    }

    static double BoxDist(List<(double x, double y, double z)> P, double x0, double y0, double z0, double x1, double y1, double z1)
    {
        double best = double.MaxValue;
        foreach (var p in P)
        {
            double dx = Math.Max(Math.Max(x0 - p.x, p.x - x1), 0), dy = Math.Max(Math.Max(y0 - p.y, p.y - y1), 0), dz = Math.Max(Math.Max(z0 - p.z, p.z - z1), 0);
            double d = Math.Sqrt(dx * dx + dy * dy + dz * dz); if (d < best) best = d;
        }
        return best;
    }
}
