// Offline check for 排管贴井内壁 (plan-view maths). No Revit is started.
// Synthetic cases cover every decision; the last group uses numbers read from the live model
// (wells ELE-EM1-I, LV 1200 handhole, TELCO 3320) so the inner-wall rule is checked on real geometry.
// Usage: dotnet run -c Release
using KaifaTools.DuctSnap;

Console.OutputEncoding = System.Text.Encoding.UTF8;
int failed = 0;
void Check(string name, bool ok, string detail = "")
{
    Console.WriteLine($"{(ok ? "PASS" : "FAIL")}  {name}" + (detail.Length > 0 ? $"  [{detail}]" : ""));
    if (!ok) failed++;
}
bool Near(double a, double b, double tol = 0.01) => Math.Abs(a - b) <= tol;

var lim = new SnapLimits(MaxExtendMm: 1000, MaxTrimMm: 600, ToleranceMm: 5);
var box = new Cavity(0, 3000, 0, 3000); // a 3 m square cavity, walls outside it

// ---- Classify: one end, bank running along +X toward (10000, 1500) ----
var far = new Pt2(10000, 1500);

var d = DuctSnapMath.Classify(new Pt2(3120, 1500), far, box, lim);
Check("short by 120 mm -> Extend, moves -120 along the bank", d.Kind == SnapKind.Extend && Near(d.T, -120), $"{d.Kind} {d.T:0.0}");

d = DuctSnapMath.Classify(new Pt2(2900, 1500), far, box, lim);
Check("overshoot 100 mm into the well -> Trim, moves +100", d.Kind == SnapKind.Trim && Near(d.T, 100), $"{d.Kind} {d.T:0.0}");

d = DuctSnapMath.Classify(new Pt2(1500, 1500), far, box, lim);
Check("end in the middle of the well -> TrimTooFar (a person decides)", d.Kind == SnapKind.TrimTooFar && Near(d.T, 1500), $"{d.Kind} {d.T:0.0}");

d = DuctSnapMath.Classify(new Pt2(500, 1500), new Pt2(1000, 1500), box, lim);
Check("whole bank inside the cavity -> Inside", d.Kind == SnapKind.Inside, $"{d.Kind}");

d = DuctSnapMath.Classify(new Pt2(-120, 1500), far, box, lim);
Check("well ahead on the bank side (bank passes through) -> Through", d.Kind == SnapKind.Through, $"{d.Kind}");

d = DuctSnapMath.Classify(new Pt2(5000, 5000), new Pt2(10000, 5000), box, lim);
Check("bank line misses the well -> Unrelated", d.Kind == SnapKind.Unrelated, $"{d.Kind}");

d = DuctSnapMath.Classify(new Pt2(3002, 1500), far, box, lim);
Check("2 mm short (within tolerance) -> Fit, no move", d.Kind == SnapKind.Fit && d.T == 0, $"{d.Kind} {d.T:0.0}");

d = DuctSnapMath.Classify(new Pt2(4200, 1500), far, box, lim);
Check("1200 mm short (over the 1000 mm limit) -> ExtendTooFar", d.Kind == SnapKind.ExtendTooFar && Near(d.T, -1200), $"{d.Kind} {d.T:0.0}");

d = DuctSnapMath.Classify(new Pt2(5, 5), new Pt2(5, 5), box, lim);
Check("zero-length bank -> Degenerate", d.Kind == SnapKind.Degenerate, $"{d.Kind}");

// Diagonal bank: the end is 120 mm short on both axes, so the move is along the diagonal and ends at the corner.
var diagEnd = new Pt2(3120, 3120);
d = DuctSnapMath.Classify(diagEnd, new Pt2(10000, 10000), box, lim);
var moved = DuctSnapMath.Move(diagEnd, new Pt2(10000, 10000), d.T);
Check("diagonal bank: Extend lands on the inner corner (3000, 3000)",
    d.Kind == SnapKind.Extend && Near(moved.X, 3000, 0.05) && Near(moved.Y, 3000, 0.05), $"{d.Kind} -> ({moved.X:0.0},{moved.Y:0.0})");

// Same case with a smaller extend limit: it must be refused.
var tight = new SnapLimits(MaxExtendMm: 100, MaxTrimMm: 600, ToleranceMm: 5);
d = DuctSnapMath.Classify(new Pt2(3120, 1500), far, box, tight);
Check("extend limit is respected (120 > 100 -> ExtendTooFar)", d.Kind == SnapKind.ExtendTooFar, $"{d.Kind}");

// Trim limit: an overshoot of 700 mm is refused when the limit is 600.
d = DuctSnapMath.Classify(new Pt2(2300, 1500), far, box, lim);
Check("trim limit is respected (700 > 600 -> TrimTooFar)", d.Kind == SnapKind.TrimTooFar && Near(d.T, 700), $"{d.Kind} {d.T:0.0}");

// Move keeps the bank straight: the new end is on the same line as the old end and the other end.
var m2 = DuctSnapMath.Move(new Pt2(3120, 1500), far, -120);
Check("Move keeps the end on the bank line", Near(m2.X, 3000) && Near(m2.Y, 1500), $"({m2.X:0.0},{m2.Y:0.0})");

// ---- FindCavity: synthetic walls around a 3000 x 3000 cavity, wall outer faces 150 mm out ----
WallFace W(double nx, double ny, double x, double y, double a) => new(nx, ny, x, y, a);
var walls = new List<WallFace>
{
    W(-1, 0, -150, 1500, 4), W(1, 0, 0, 1500, 4),       // left: outer, inner
    W(1, 0, 3150, 1500, 4), W(-1, 0, 3000, 1500, 4),    // right: outer, inner
    W(0, -1, 1500, -150, 4), W(0, 1, 1500, 0, 4),       // bottom: outer, inner
    W(0, 1, 1500, 3150, 4), W(0, -1, 1500, 3000, 4),    // top: outer, inner
};
var c1 = DuctSnapMath.FindCavity(walls, 0.9, 0.4);
Check("synthetic well: cavity is the inner faces", c1 is { } a1 && Near(a1.XLeft, 0) && Near(a1.XRight, 3000) && Near(a1.YBottom, 0) && Near(a1.YTop, 3000), $"{c1}");

var withSmall = new List<WallFace>(walls) { W(1, 0, -20, 1500, 0.1) };
var c2 = DuctSnapMath.FindCavity(withSmall, 0.9, 0.4);
Check("small bracket face (0.1 m2) is ignored", c2 is { } a2 && Near(a2.XLeft, 0), $"{c2}");

var withPartition = new List<WallFace>(walls) { W(1, 0, 1500, 1500, 4), W(-1, 0, 1200, 1500, 4) };
var c3 = DuctSnapMath.FindCavity(withPartition, 0.9, 0.4);
Check("partition inside the cavity does not move the walls", c3 is { } a3 && Near(a3.XLeft, 0) && Near(a3.XRight, 3000), $"{c3}");

var noRight = walls.Where(f => !(f.Nx == -1 && f.X == 3000)).ToList();
Check("missing inner wall -> no cavity", DuctSnapMath.FindCavity(noRight, 0.9, 0.4) is null);

// ---- Real geometry read from the live model (values in mm, from Revit API faces) ----
// ELE-EM1-I, 3125 x 3125 cavity; inner faces (normal into the cavity) and outer faces, areas in m2.
var em = new List<WallFace>
{
    W(1, 0, 473525.9, -213632.6, 8.28), W(0, -1, 473525.9, -210507.6, 8.28),
    W(-1, 0, 476650.9, -210507.6, 8.28), W(0, 1, 476650.9, -213632.6, 8.28),
    W(0, -1, 473375.9, -213782.6, 9.59), W(1, 0, 476800.9, -213782.6, 9.59),
    W(0, 1, 476800.9, -210357.6, 9.59), W(-1, 0, 473375.9, -210357.6, 9.59),
};
var cem = DuctSnapMath.FindCavity(em, 0.9, 0.4);
Check("model well ELE-EM1-I: cavity 473525.9..476650.9 x -213632.6..-210507.6",
    cem is { } e1 && Near(e1.XLeft, 473525.9, 0.05) && Near(e1.XRight, 476650.9, 0.05)
    && Near(e1.YBottom, -213632.6, 0.05) && Near(e1.YTop, -210507.6, 0.05), $"{cem}");

// LV handhole 1200 x 1200: inner cavity 1000 x 1000 (faces 2.65 m2 inside, 3.40 m2 outer).
var lv = new List<WallFace>
{
    W(0, 1, 270933, 5634, 3.40), W(-1, 0, 269733, 5634, 3.40), W(0, -1, 269733, 4434, 3.40), W(1, 0, 270933, 4434, 3.40),
    W(-1, 0, 270833, 5534, 2.65), W(0, 1, 270833, 4534, 2.65), W(1, 0, 269833, 4534, 2.65), W(0, -1, 269833, 5534, 2.65),
};
var clv = DuctSnapMath.FindCavity(lv, 0.9, 0.4);
Check("model LV handhole: cavity 269833..270833 x 4534..5534",
    clv is { } l1 && Near(l1.XLeft, 269833) && Near(l1.XRight, 270833) && Near(l1.YBottom, 4534) && Near(l1.YTop, 5534), $"{clv}");

// TELCO 3320 well: bounding box is 7110 mm, but the wall faces give a 3320 cavity. Bounding-box zones would fail here.
var tel = new List<WallFace>
{
    W(1, 0, -72511, 8050, 12.86), W(0, -1, -72511, 11370, 12.86), W(-1, 0, -69191, 11370, 12.86), W(0, 1, -69191, 8050, 12.86),
    W(0, -1, -72661, 7900, 14.75), W(1, 0, -69041, 7900, 14.75), W(0, 1, -69041, 11520, 14.75), W(-1, 0, -72661, 11520, 14.75),
    W(0, 1, -69041, 11520, 0.90), W(-1, 0, -72661, 11520, 0.90), W(0, -1, -72661, 7900, 0.90), W(1, 0, -69041, 7900, 0.90),
};
var ctel = DuctSnapMath.FindCavity(tel, 0.9, 0.4);
Check("model TELCO 3320 well: cavity -72511..-69191 x 8050..11370 (0.9 m2 faces ignored)",
    ctel is { } t1 && Near(t1.XLeft, -72511) && Near(t1.XRight, -69191) && Near(t1.YBottom, 8050) && Near(t1.YTop, 11370), $"{ctel}");

// ---- Elevation check after a write: read-back ends against the planned ends ----
var pa = new Pt3(3000, 1500, 3300);
var pb = new Pt3(10000, 1500, 4350);
var same = DuctSnapMath.CompareEnds(pa, pb, pa, pb);
Check("read-back equal to plan: no elevation change, no plan error", same.ElevationDiffMm == 0 && same.PlanErrorMm == 0, $"{same}");

var swapped = DuctSnapMath.CompareEnds(pa, pb, pb, pa);
Check("ends read back in reverse order still match (order does not matter)", swapped.ElevationDiffMm == 0 && swapped.PlanErrorMm == 0, $"{swapped}");

var zMoved = DuctSnapMath.CompareEnds(pa, pb, new Pt3(3000, 1500, 3600), pb);
Check("a 300 mm elevation change at the start is reported", Near(zMoved.ElevationDiffMm, 300), $"{zMoved}");

var planOff = DuctSnapMath.CompareEnds(pa, pb, new Pt3(3040, 1500, 3300), pb);
Check("a 40 mm plan error is reported, elevation unchanged", Near(planOff.PlanErrorMm, 40) && planOff.ElevationDiffMm == 0, $"{planOff}");

Console.WriteLine();
Console.WriteLine(failed == 0 ? "ALL PASS" : $"{failed} FAILED");
return failed == 0 ? 0 : 1;
