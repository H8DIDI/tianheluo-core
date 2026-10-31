using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Autodesk.Revit.UI.Selection;
using TaskDialog = Autodesk.Revit.UI.TaskDialog;

namespace KaifaTools.DuctRoute;

/// <summary>Entry points of the small one-job plug-ins (each plug-in is one command that calls one of these).</summary>
public static class MepTools
{
    sealed class MepFilter : ISelectionFilter
    {
        public bool AllowElement(Element e) => e is MEPCurve and (Autodesk.Revit.DB.Plumbing.Pipe or Autodesk.Revit.DB.Mechanical.Duct or Autodesk.Revit.DB.Electrical.Conduit or Autodesk.Revit.DB.Electrical.CableTray);
        public bool AllowReference(Reference r, XYZ p) => true;
    }

    static List<MEPCurve>? Members(UIDocument uidoc, string prompt, bool many)
    {
        var doc = uidoc.Document;
        var pre = uidoc.Selection.GetElementIds().Select(i => doc.GetElement(i)).OfType<MEPCurve>().Where(e => new MepFilter().AllowElement(e)).ToList();
        if (pre.Count > 0 && (many || pre.Count == 1)) return pre;
        try
        {
            if (many) return uidoc.Selection.PickObjects(ObjectType.Element, new MepFilter(), prompt + "（选完点完成）").Select(r => (MEPCurve)doc.GetElement(r.ElementId)).ToList();
            return new List<MEPCurve> { (MEPCurve)doc.GetElement(uidoc.Selection.PickObject(ObjectType.Element, new MepFilter(), prompt).ElementId) };
        }
        catch (Autodesk.Revit.Exceptions.OperationCanceledException) { return null; }
    }

    static bool Ask(string title, string text) => new TaskDialog(title) { MainContent = text, CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No }.Show() == TaskDialogResult.Yes;
    static void Say(string title, string text) => new TaskDialog(title) { MainContent = text }.Show();

    static string Notes(IEnumerable<string> notes) { var l = notes.Take(8).ToList(); return l.Count == 0 ? "" : "\n\n说明：\n" + string.Join("\n", l); }

    static Result ApplyPlans(UIApplication app, string title, string what, List<JogPlan> plans, IEnumerable<string> notes)
    {
        var uidoc = app.ActiveUIDocument; var doc = uidoc.Document;
        if (plans.Count == 0) { Say(title, "没有能处理的。" + Notes(notes)); return Result.Cancelled; }
        string list = string.Join("\n", plans.Take(10).Select(p => "· " + p.Summary)) + (plans.Count > 10 ? $"\n… 共 {plans.Count} 根" : "");
        if (!Ask(title, $"{what}\n\n{list}{Notes(notes)}\n\n继续？（出错的那根会自己撤销，之后可用“管线还原”一次接回）")) return Result.Cancelled;
        var keep = uidoc.Selection.GetElementIds(); uidoc.Selection.SetElementIds(new List<ElementId>());
        (bool ok, string message) res;
        try { res = Jogger.ApplyBundle(doc, plans, app); } finally { try { uidoc.Selection.SetElementIds(keep); } catch { } }
        Say(title, res.message); return res.ok ? Result.Succeeded : Result.Failed;
    }

    public static Result Hop(UIApplication app)
    {
        var ms = Members(app.ActiveUIDocument, "选要翻弯的管线", true); if (ms is null) return Result.Cancelled;
        var plans = Jogger.PlanAuto(app.ActiveUIDocument.Document, ms, new JogOptions { Mode = "vertical" }, out var notes);
        return ApplyPlans(app, "管线过障翻弯", "从障碍上面或下面翻过去（不改平面位置），接头自动生成 45° 弯头。", plans, notes);
    }

    public static Result Detour(UIApplication app)
    {
        var ms = Members(app.ActiveUIDocument, "选要平面绕行的管线", true); if (ms is null) return Result.Cancelled;
        var plans = Jogger.PlanAuto(app.ActiveUIDocument.Document, ms, new JogOptions { Mode = "plane" }, out var notes);
        return ApplyPlans(app, "管线平面绕行", "从障碍左边或右边绕过去，平行的管线并成一束一起平移，接头自动生成 45° 弯头。", plans, notes);
    }

    public static Result Bundle(UIApplication app)
    {
        var ms = Members(app.ActiveUIDocument, "选一束平行的管线", true); if (ms is null) return Result.Cancelled;
        var plans = Jogger.PlanBundle(app.ActiveUIDocument.Document, ms, new JogOptions { Mode = "plane" }, out var err);
        if (plans is null) { Say("并线绕行", "不能一起绕行：" + err); return Result.Cancelled; }
        return ApplyPlans(app, "并线绕行", "这一束平行管线保持间距，一起平移绕开障碍。", plans, new List<string>());
    }

    public static Result Outline(UIApplication app)
    {
        var doc = app.ActiveUIDocument.Document;
        var ms = Members(app.ActiveUIDocument, "选要沿障碍外沿绕行的管线", true); if (ms is null) return Result.Cancelled;
        var plans = new List<JogPlan>(); var notes = new List<string>();
        foreach (var m in ms) { var p = Jogger.PlanPath(doc, m, new JogOptions(), out var err); if (p is null) notes.Add($"{m.Id}：{err}"); else plans.Add(p); }
        return ApplyPlans(app, "沿障碍外沿绕行", "按障碍的真实外形外扩，走最短路，每个转折点生成弯头（会改平面位置）。", plans, notes);
    }

    public static Result Corridor(UIApplication app)
    {
        var ms = Members(app.ActiveUIDocument, "选要协调的一批管线", true); if (ms is null) return Result.Cancelled;
        var plans = Jogger.PlanCoordinated(app.ActiveUIDocument.Document, ms, new JogOptions(), out var notes);
        return ApplyPlans(app, "多根协调绕行", "很多根互相交叉也行：自动选上/下/左/右，保证改完之后互相不冲突。", plans, notes);
    }

    public static Result Break(UIApplication app)
    {
        var uidoc = app.ActiveUIDocument; var doc = uidoc.Document;
        var ms = Members(uidoc, "选要打断的一根管线", false); if (ms is null) return Result.Cancelled;
        XYZ pt;
        try { pt = uidoc.Selection.PickPoint("在管线上点打断位置"); } catch (Autodesk.Revit.Exceptions.OperationCanceledException) { return Result.Cancelled; }
        var keep = uidoc.Selection.GetElementIds(); uidoc.Selection.SetElementIds(new List<ElementId>());
        (bool ok, string message) res;
        try { res = Jogger.BreakAt(doc, ms[0], pt, app); } finally { try { uidoc.Selection.SetElementIds(keep); } catch { } }
        Say("管线打断", res.message); return res.ok ? Result.Succeeded : Result.Failed;
    }

    public static Result Restore(UIApplication app)
    {
        var doc = app.ActiveUIDocument.Document; var f = Jogger.LatestBackup(doc);
        if (f is null) { Say("管线还原", "没有找到本文档的绕行/打断备份"); return Result.Cancelled; }
        if (!Ask("管线还原", "把最近一次绕行或打断接回原样（删掉新生成的管线和接头）？")) return Result.Cancelled;
        var (ok, msg) = Jogger.Restore(doc, f, app); Say("管线还原", msg); return ok ? Result.Succeeded : Result.Failed;
    }

    public static Result Clash(UIApplication app)
    {
        var uidoc = app.ActiveUIDocument; var doc = uidoc.Document; var ids = uidoc.Selection.GetElementIds();
        if (ids.Count == 0) { Say("管线碰撞检查", "请先在 Revit 里选要检查的水管、风管、桥架、线管或排管，再点这个按钮。"); return Result.Cancelled; }
        var list = GeneralCheck.Run(doc, ids, 400, null);
        var dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools"); Directory.CreateDirectory(dir);
        var csv = Path.Combine(dir, $"碰撞检查_{DateTime.Now:yyyyMMdd-HHmmss}.csv"); File.WriteAllText(csv, GeneralCheck.ToCsv(list));
        if (list.Count > 0) uidoc.Selection.SetElementIds(list.Select(x => new ElementId(x.Id)).ToList());
        Say("管线碰撞检查", $"检查了 {ids.Count} 个构件，{list.Count} 个有碰撞（只读，没改模型）。有碰撞的已选中。\n清单：{csv}"); return Result.Succeeded;
    }

    public static Result Continuity(UIApplication app)
    {
        var uidoc = app.ActiveUIDocument; var doc = uidoc.Document;
        var ms = uidoc.Selection.GetElementIds().Select(i => doc.GetElement(i)).OfType<MEPCurve>().ToList();
        if (ms.Count == 0) { Say("管线连续性检查", "请先在 Revit 里选要检查的管线，再点这个按钮。"); return Result.Cancelled; }
        var issues = Jogger.Continuity(doc, ms);
        var dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools"); Directory.CreateDirectory(dir);
        var csv = Path.Combine(dir, $"连续性检查_{DateTime.Now:yyyyMMdd-HHmmss}.csv");
        File.WriteAllText(csv, "﻿id,类别,系统,问题\r\n" + string.Join("\r\n", issues.Select(i => $"{i.Id},{i.Category},\"{i.System}\",\"{i.Problem}\"")));
        if (issues.Count > 0) uidoc.Selection.SetElementIds(issues.Select(i => new ElementId(i.Id)).Distinct().ToList());
        Say("管线连续性检查", $"检查了 {ms.Count} 根，发现 {issues.Count} 处问题（只读）。有问题的已选中。\n" + string.Join("\n", issues.Take(8).Select(i => $"· {i.Id} {i.Problem}")) + $"\n清单：{csv}");
        return Result.Succeeded;
    }
}
