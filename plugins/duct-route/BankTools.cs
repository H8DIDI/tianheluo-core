using System.Text.Json;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Autodesk.Revit.UI.Selection;
using TaskDialog = Autodesk.Revit.UI.TaskDialog;

namespace KaifaTools.DuctRoute;

/// <summary>Entry points of the 排管 (structural framing) plug-ins.</summary>
public static class BankTools
{
    sealed class BankFilter : ISelectionFilter
    {
        public bool AllowElement(Element e) => Jogger.IsBankLike(e);
        public bool AllowReference(Reference r, XYZ p) => true;
    }

    static List<Element>? Banks(UIDocument uidoc, string prompt, bool many)
    {
        var doc = uidoc.Document;
        var pre = uidoc.Selection.GetElementIds().Select(i => doc.GetElement(i)).Where(Jogger.IsBankLike).ToList();
        if (pre.Count > 0 && (many || pre.Count == 1)) return pre;
        try
        {
            if (many) return uidoc.Selection.PickObjects(ObjectType.Element, new BankFilter(), prompt + "（选完点完成）").Select(r => doc.GetElement(r.ElementId)).ToList();
            return new List<Element> { doc.GetElement(uidoc.Selection.PickObject(ObjectType.Element, new BankFilter(), prompt).ElementId) };
        }
        catch (Autodesk.Revit.Exceptions.OperationCanceledException) { return null; }
    }

    static bool Ask(string title, string text) => new TaskDialog(title) { MainContent = text, CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No }.Show() == TaskDialogResult.Yes;
    static void Say(string title, string text) => new TaskDialog(title) { MainContent = text }.Show();

    static Result Run(UIApplication app, string title, string what, List<BankPlan> plans, List<string> notes)
    {
        var uidoc = app.ActiveUIDocument; var doc = uidoc.Document;
        string nt = notes.Count == 0 ? "" : "\n\n不能处理：\n" + string.Join("\n", notes.Take(8)) + (notes.Count > 8 ? $"\n… 共 {notes.Count} 条" : "");
        if (plans.Count == 0) { Say(title, "没有能处理的。" + nt); return Result.Cancelled; }
        string list = string.Join("\n", plans.Take(10).Select(p => "· " + p.Summary)) + (plans.Count > 10 ? $"\n… 共 {plans.Count} 根" : "");
        if (!Ask(title, $"{what}\n\n{list}{nt}\n\n继续？（每根会切成几段，两端仍在原来的井里；可 Ctrl+Z，或用“排管还原”一次接回）")) return Result.Cancelled;
        var keep = uidoc.Selection.GetElementIds(); uidoc.Selection.SetElementIds(new List<ElementId>());
        (int ok, List<string> failed, string backup) res;
        try { res = Jogger.ApplyBanks(doc, plans, app); } finally { try { uidoc.Selection.SetElementIds(keep); } catch { } }
        string review = "";
        try
        {
            using var d = JsonDocument.Parse(File.ReadAllText(res.backup));
            var groups = d.RootElement.GetProperty("items").EnumerateArray().Select(it => new[] { it.GetProperty("id").GetInt64() }.Concat(it.GetProperty("news").EnumerateArray().Select(x => x.GetInt64())).ToList()).ToList();
            var left = Jogger.ReviewBanks(doc, groups);
            review = left.Count == 0 ? "\n改完复检：这些排管没有再碰到别的构件（两端的井不算）。" : $"\n改完复检：还有 {left.Count} 段碰到别的构件：" + string.Join("、", left.Take(5).Select(l => $"{l.bank}→{string.Join("/", l.with.Take(3))}"));
        }
        catch (Exception ex) { review = "\n复检出错：" + ex.Message; }
        Say(title, $"成功 {res.ok}/{plans.Count} 根。" + (res.failed.Count > 0 ? "\n失败：" + string.Join("；", res.failed.Take(5)) : "") + review);
        return res.failed.Count == 0 ? Result.Succeeded : Result.Failed;
    }

    public static Result Hop(UIApplication app)
    {
        var bs = Banks(app.ActiveUIDocument, "选要翻弯的排管", true); if (bs is null) return Result.Cancelled;
        var plans = new List<BankPlan>(); var notes = new List<string>();
        foreach (var b in bs) { var p = Jogger.PlanBank(app.ActiveUIDocument.Document, b, new JogOptions(), "vertical", null, out var err); if (p is null) notes.Add($"{b.Id}：{err}"); else plans.Add(p); }
        return Run(app, "排管过障翻弯", "从碰到的障碍上面或下面翻过去：先坡下去/上来，中间一段平着过，再坡回来（不改平面位置，标高取 0.1 m 一档）。", plans, notes);
    }

    static double Lateral(BankPlan p)
    {
        var u = new XYZ(p.P1.X - p.P0.X, p.P1.Y - p.P0.Y, 0).Normalize(); var n = new XYZ(-u.Y, u.X, 0);
        return (new XYZ(p.V[2].X - p.P0.X, p.V[2].Y - p.P0.Y, 0)).DotProduct(n) - 0;
    }

    public static Result Detour(UIApplication app)
    {
        var doc = app.ActiveUIDocument.Document;
        var bs = Banks(app.ActiveUIDocument, "选要平面绕行的排管", true); if (bs is null) return Result.Cancelled;
        var plans = new List<BankPlan>(); var notes = new List<string>();
        bool parallel = bs.Count > 1 && bs.All(b => { var l1 = (Line)((LocationCurve)bs[0].Location).Curve; var l2 = (Line)((LocationCurve)b.Location).Curve; return l1.Direction.CrossProduct(l2.Direction).GetLength() < 0.02; });
        if (parallel)
        {
            // one common sideways shift keeps the banks parallel and never makes them cross each other
            double best = double.MaxValue; double shift = 0; string why = "";
            foreach (var dir in new[] { "left", "right" })
            {
                var ps = new List<BankPlan>(); bool bad = false;
                foreach (var b in bs) { var p = Jogger.PlanBank(doc, b, new JogOptions { Direction = dir }, "plane", null, out _); if (p is null) continue; ps.Add(p); }
                if (ps.Count == 0) { bad = true; }
                if (bad) continue;
                double mx = ps.Select(p => Lateral(p)).OrderByDescending(Math.Abs).First();
                if (Math.Abs(mx) < best) { best = Math.Abs(mx); shift = mx; why = dir; }
            }
            if (best == double.MaxValue) { Say("排管平面绕行", "没有能处理的（没碰到东西，或太短放不下斜接）"); return Result.Cancelled; }
            foreach (var b in bs) { var p = Jogger.PlanBank(doc, b, new JogOptions(), "plane", shift, out var err); if (p is null) notes.Add($"{b.Id}：{err}"); else plans.Add(p); }
        }
        else
            foreach (var b in bs) { var p = Jogger.PlanBank(doc, b, new JogOptions(), "path", null, out var err); if (p is null) notes.Add($"{b.Id}：{err}"); else plans.Add(p); }
        if (plans.Count == 0 && parallel)
            foreach (var b in bs) { var p = Jogger.PlanBank(doc, b, new JogOptions(), "path", null, out var err); if (p is null) notes.Add($"{b.Id}：{err}"); else plans.Add(p); }
        return Run(app, "排管平面绕行", parallel ? "一束平行排管用同一个平移量一起绕开，保持互相平行；两端仍接原来的井。" : "沿障碍的真实外形外扩一圈绕过去，多个转折点，两端仍接原来的井。", plans, notes);
    }

    public static Result Break(UIApplication app)
    {
        var uidoc = app.ActiveUIDocument; var doc = uidoc.Document;
        var bs = Banks(uidoc, "选要打断的一根排管", false); if (bs is null) return Result.Cancelled;
        XYZ pt;
        try { pt = uidoc.Selection.PickPoint("在排管上点打断位置"); } catch (Autodesk.Revit.Exceptions.OperationCanceledException) { return Result.Cancelled; }
        var ln = (Line)((LocationCurve)bs[0].Location).Curve; var p0 = ln.GetEndPoint(0); var u = ln.Direction;
        double t = (pt - p0).DotProduct(u) * 0.3048, L = ln.Length * 0.3048;
        if (t < 0.5 || t > L - 0.5) { Say("排管打断", "断点离管端太近（两边都要留 0.5 m 以上）"); return Result.Cancelled; }
        var keep = uidoc.Selection.GetElementIds(); uidoc.Selection.SetElementIds(new List<ElementId>());
        (int cut, List<string> failed, string backup) r;
        try { r = Cutter.Apply(doc, new List<CutPlan> { new CutPlan(bs[0].Id.Value, "", L, new List<(double, double)> { (0, Math.Round(t, 3)), (Math.Round(t, 3), Math.Round(L, 3)) }, new List<string>()) }, app); }
        finally { try { uidoc.Selection.SetElementIds(keep); } catch { } }
        Say("排管打断", r.cut == 1 ? $"已在 {t:0.00} m 处断开，两段标高连续。可 Ctrl+Z，或用“排管还原”接回。" : "失败：" + string.Join("；", r.failed));
        return r.cut == 1 ? Result.Succeeded : Result.Failed;
    }

    public static Result Restore(UIApplication app)
    {
        var doc = app.ActiveUIDocument.Document; var f = Cutter.LatestBackup(doc);
        if (f is null) { Say("排管还原", "没有找到本文档的断开/绕行备份"); return Result.Cancelled; }
        if (!Ask("排管还原", "把最近一次排管绕行或打断接回原样（删掉多出来的段，原排管恢复原长度和两端标高）？")) return Result.Cancelled;
        var (ok, failed) = Cutter.Restore(doc, f, app);
        Say("排管还原", $"已还原 {ok} 根。" + (failed.Count > 0 ? "\n失败：" + string.Join("；", failed.Take(5)) : "")); return failed.Count == 0 ? Result.Succeeded : Result.Failed;
    }
}
