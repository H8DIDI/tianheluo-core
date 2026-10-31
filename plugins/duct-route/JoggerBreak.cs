using System.Text.Json;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace KaifaTools.DuctRoute;

/// <summary>Small stand-alone tools on pipes / ducts / cable trays / conduits: break a run, check that a run is still one connected system.</summary>
public static partial class Jogger
{
    /// <summary>Breaks one run at a point: the part behind the point becomes a new piece, joined to the front part with a union fitting (or a direct connection when Revit has none for that category).</summary>
    public static (bool ok, string message) BreakAt(Document doc, MEPCurve m, XYZ pt, UIApplication? app, double minPieceM = 0.3)
    {
        if (m.Location is not LocationCurve lc || lc.Curve is not Line ln) return (false, "只支持直线段");
        var p0 = ln.GetEndPoint(0); var p1 = ln.GetEndPoint(1); var u = (p1 - p0).Normalize();
        double t = (pt - p0).DotProduct(u); double minFt = minPieceM / Ft;
        if (t < minFt || t > ln.Length - minFt) return (false, $"断点离管端太近（两边都要留 {minPieceM:0.0} m 以上）");
        var cut = p0 + u * t;
        using var guard = new Writer.DialogGuard(app);
        var far = new List<Connector>(); foreach (Connector c in m.ConnectorManager.Connectors) if (c.Origin.DistanceTo(p1) < 0.02) foreach (Connector r in c.AllRefs) if (r.Owner.Id != m.Id) far.Add(r);
        var farOwners = far.Select(c => c.Owner.Id.Value).ToList(); var ins = InsulationOf(doc, m);
        var created = new List<long>(); string file = Path.Combine(Writer.BackupDir, $"jog-{DateTime.Now:yyyyMMdd-HHmmssfff}-1.json"); Directory.CreateDirectory(Writer.BackupDir);
        string how = "接头件";
        using var tg = new TransactionGroup(doc, "管线打断"); tg.Start();
        try
        {
            using var tr = new Transaction(doc, "打断"); tr.Start();
            var fo = tr.GetFailureHandlingOptions(); fo.SetFailuresPreprocessor(new Writer.DropWarnings()); tr.SetFailureHandlingOptions(fo);
            ((LocationCurve)m.Location).Curve = Line.CreateBound(p0, cut);
            var piece = Create(doc, m, cut, p1); created.Add(piece.Id.Value); AddInsulation(doc, piece, ins);
            var a = Near(m, cut); var b = Near(piece, cut);
            try { created.Add(doc.Create.NewUnionFitting(a, b).Id.Value); }
            catch { a.ConnectTo(b); how = "直接对接（这个类别没有接头件）"; }
            var cc = Near(piece, p1); foreach (var r in far) { try { cc.ConnectTo(r); } catch { } }
            if (tr.Commit() != TransactionStatus.Committed) { tg.RollBack(); return (false, "被回滚（Revit 没接受这次打断）"); }
        }
        catch (Exception ex) { try { tg.RollBack(); } catch { } return (false, "失败，已撤销：" + ex.Message); }
        tg.Assimilate();
        File.WriteAllText(file, JsonSerializer.Serialize(new { doc = doc.Title, time = DateTime.Now, id = m.Id.Value, p0 = new[] { p0.X, p0.Y, p0.Z }, p1 = new[] { p1.X, p1.Y, p1.Z }, created, farOwners }));
        return (true, $"已在 {t * Ft:0.00} m 处断开，两段之间用{how}连接。可 Ctrl+Z 撤销，或用“还原绕行”接回。");
    }

    public sealed record ContinuityIssue(long Id, string Category, string System, string Problem);

    /// <summary>Read only: open ends, neighbours of another system, runs that do not meet their neighbour exactly.</summary>
    public static List<ContinuityIssue> Continuity(Document doc, IEnumerable<MEPCurve> members)
    {
        var list = new List<ContinuityIssue>();
        foreach (var m in members)
        {
            string sys = SysName(m); string cat = m.Category?.Name ?? "";
            foreach (Connector c in m.ConnectorManager.Connectors)
            {
                var refs = c.AllRefs.Cast<Connector>().Where(r => r.Owner.Id != m.Id).ToList();
                if (refs.Count == 0) { list.Add(new ContinuityIssue(m.Id.Value, cat, sys, "有一端没接任何东西（开口）")); continue; }
                foreach (var r in refs)
                {
                    if (c.Origin.DistanceTo(r.Origin) > 0.01) list.Add(new ContinuityIssue(m.Id.Value, cat, sys, $"和 {r.Owner.Id.Value} 的接口差 {c.Origin.DistanceTo(r.Origin) * Ft * 1000:0} mm"));
                    if (m is Autodesk.Revit.DB.Plumbing.Pipe or Autodesk.Revit.DB.Mechanical.Duct && SysName(r.Owner) is { Length: > 0 } s2 && sys.Length > 0 && s2 != sys) list.Add(new ContinuityIssue(m.Id.Value, cat, sys, $"邻接的 {r.Owner.Id.Value} 系统是“{s2}”，不是“{sys}”"));
                }
            }
        }
        return list;
    }
}
