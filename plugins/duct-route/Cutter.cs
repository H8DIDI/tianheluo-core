using System.Text.Json;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace KaifaTools.DuctRoute;

public sealed record CutPlan(long BankId, string Kind, double L, List<(double a, double b)> Pieces, List<string> Wells);

/// <summary>
/// Tool "井处断开": a bank of another kind that runs through a TELCO well is cut where the well is; the part inside the well is removed,
/// each piece keeps the level the line had there (so both sides of the well are level), and the pieces end at the well wall.
/// </summary>
public static class Cutter
{
    const double Ft = 0.3048;
    static readonly BuiltInParameter P0 = BuiltInParameter.STRUCTURAL_BEAM_END0_ELEVATION, P1 = BuiltInParameter.STRUCTURAL_BEAM_END1_ELEVATION;

    /// <summary>Stops Revit from joining (and extending) this end of a framing member to its neighbours.</summary>
    public static void NoJoin(Element e, int end) { try { if (e is FamilyInstance fi) Autodesk.Revit.DB.Structure.StructuralFramingUtils.DisallowJoinAtEnd(fi, end); } catch { } }
    public static void AllowJoin(Element e, int end) { try { if (e is FamilyInstance fi) Autodesk.Revit.DB.Structure.StructuralFramingUtils.AllowJoinAtEnd(fi, end); } catch { } }

    public static List<CutPlan> Plan(Scene s, double minPieceM, out List<string> skipped)
    {
        skipped = new(); var plans = new List<CutPlan>(); var tw = s.Wells.Where(w => w.Kind == "TELCO").ToList();
        foreach (var b in s.Movable.Where(x => x.Kind != "TELCO"))
        {
            var cuts = new List<(double t0, double t1, string mark)>();
            foreach (var w in tw)
            {
                if (b.Wid.Contains(w.Id)) continue;
                double t0 = double.MaxValue, t1 = double.MinValue;
                for (double t = 0; t <= b.L + 1e-9; t += 0.05)
                {
                    double x = b.Ax + t * b.Ux, y = b.Ay + t * b.Uy;
                    if (x >= w.MinX && x <= w.MaxX && y >= w.MinY && y <= w.MaxY) { t0 = Math.Min(t0, t); t1 = Math.Max(t1, t); }
                }
                if (t1 >= t0 && t0 > 1.0 && t1 < b.L - 1.0) cuts.Add((t0, t1, string.IsNullOrEmpty(w.Mark) ? w.Id.ToString() : w.Mark));
            }
            if (cuts.Count == 0) continue;
            cuts.Sort(); var pcs = new List<(double, double)>(); double pos = 0;
            foreach (var (t0, t1, _) in cuts) { pcs.Add((Math.Round(pos, 3), Math.Round(t0, 3))); pos = t1; }
            pcs.Add((Math.Round(pos, 3), Math.Round(b.L, 3)));
            if (pcs.Any(p => p.Item2 - p.Item1 < minPieceM)) { skipped.Add($"{b.Id}：断开后有一段短于 {minPieceM:0.0} m，没断（井几乎在端头）"); continue; }
            plans.Add(new CutPlan(b.Id, b.Kind, b.L, pcs, cuts.Select(c => c.mark).ToList()));
        }
        return plans;
    }

    public static (int cut, List<string> failed, string backup) Apply(Document doc, List<CutPlan> plans, UIApplication? app)
    {
        using var guard = new Writer.DialogGuard(app);
        Directory.CreateDirectory(Writer.BackupDir);
        string file = Path.Combine(Writer.BackupDir, $"cut-{DateTime.Now:yyyyMMdd-HHmmss}.json");
        var items = new List<object>(); var failed = new List<string>(); int cut = 0;
        using var tg = new TransactionGroup(doc, "排管井处断开"); tg.Start();
        foreach (var pl in plans)
        {
            var e = doc.GetElement(new ElementId(pl.BankId)); if (e?.Location is not LocationCurve lc || lc.Curve is not Line ln) { failed.Add($"{pl.BankId}：找不到"); continue; }
            var p = ln.GetEndPoint(0); var q = ln.GetEndPoint(1); double e0 = e.get_Parameter(P0).AsDouble(), e1 = e.get_Parameter(P1).AsDouble();
            var pre = new Writer.DropWarnings(); using var t = new Transaction(doc, "断开");
            try
            {
                t.Start(); var fo = t.GetFailureHandlingOptions(); fo.SetFailuresPreprocessor(pre); t.SetFailureHandlingOptions(fo);
                var els = new List<Element> { e };
                for (int i = 1; i < pl.Pieces.Count; i++) els.Add(doc.GetElement(ElementTransformUtils.CopyElement(doc, e.Id, XYZ.Zero).First()));
                for (int i = 0; i < pl.Pieces.Count; i++)
                {
                    double fa = pl.Pieces[i].a / pl.L, fb = pl.Pieces[i].b / pl.L;
                    var a = i == 0 ? p : p + (q - p) * fa; var b = i == pl.Pieces.Count - 1 ? q : p + (q - p) * fb;
                    ((LocationCurve)els[i].Location).Curve = Line.CreateBound(a, b);
                    els[i].get_Parameter(P0).Set(e0 + (e1 - e0) * fa); els[i].get_Parameter(P1).Set(e0 + (e1 - e0) * fb);
                    if (i > 0) NoJoin(els[i], 0); if (i < pl.Pieces.Count - 1) NoJoin(els[i], 1);          // a joined end would be pulled back to the neighbour at regeneration: the cut would not stay where it was made
                }
                if (t.Commit() == TransactionStatus.Committed)
                {
                    cut++; items.Add(new { id = pl.BankId, p = new[] { p.X, p.Y, p.Z }, q = new[] { q.X, q.Y, q.Z }, e0, e1, news = els.Skip(1).Select(x => x.Id.Value).ToArray() });
                }
                else failed.Add($"{pl.BankId}：被回滚");
            }
            catch (Exception ex) { try { t.RollBack(); } catch { } failed.Add($"{pl.BankId}：{ex.Message}"); }
        }
        tg.Assimilate();
        File.WriteAllText(file, JsonSerializer.Serialize(new { doc = doc.Title, time = DateTime.Now, items }));
        return (cut, failed, file);
    }

    public static string? LatestBackup(Document doc) =>
        !Directory.Exists(Writer.BackupDir) ? null : Directory.GetFiles(Writer.BackupDir, "cut-*.json").OrderByDescending(f => f).FirstOrDefault(f =>
        { try { using var d = JsonDocument.Parse(File.ReadAllText(f)); return d.RootElement.GetProperty("doc").GetString() == doc.Title; } catch { return false; } });

    /// <summary>Deletes the extra pieces and puts the original piece back to its full length and levels.</summary>
    public static (int restored, List<string> failed) Restore(Document doc, string file, UIApplication? app)
    {
        using var guard = new Writer.DialogGuard(app);
        var failed = new List<string>(); int ok = 0; using var d = JsonDocument.Parse(File.ReadAllText(file));
        using var tg = new TransactionGroup(doc, "排管井处断开·还原"); tg.Start();
        foreach (var it in d.RootElement.GetProperty("items").EnumerateArray())
        {
            long id = it.GetProperty("id").GetInt64(); var e = doc.GetElement(new ElementId(id)); if (e is null) { failed.Add($"{id}：找不到"); continue; }
            var pp = it.GetProperty("p").EnumerateArray().Select(x => x.GetDouble()).ToArray(); var qq = it.GetProperty("q").EnumerateArray().Select(x => x.GetDouble()).ToArray();
            var pre = new Writer.DropWarnings(); using var t = new Transaction(doc, "还原断开");
            try
            {
                t.Start(); var fo = t.GetFailureHandlingOptions(); fo.SetFailuresPreprocessor(pre); t.SetFailureHandlingOptions(fo);
                foreach (var nid in it.GetProperty("news").EnumerateArray()) { var ne = doc.GetElement(new ElementId(nid.GetInt64())); if (ne is not null) doc.Delete(ne.Id); }
                ((LocationCurve)e.Location).Curve = Line.CreateBound(new XYZ(pp[0], pp[1], pp[2]), new XYZ(qq[0], qq[1], qq[2]));
                AllowJoin(e, 0); AllowJoin(e, 1);
                e.get_Parameter(P0).Set(it.GetProperty("e0").GetDouble()); e.get_Parameter(P1).Set(it.GetProperty("e1").GetDouble());
                if (t.Commit() == TransactionStatus.Committed) ok++; else failed.Add($"{id}：被回滚");
            }
            catch (Exception ex) { try { t.RollBack(); } catch { } failed.Add($"{id}：{ex.Message}"); }
        }
        tg.Assimilate();
        return (ok, failed);
    }
}
