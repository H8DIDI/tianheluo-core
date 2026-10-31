using System.Diagnostics;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Autodesk.Revit.DB.Mechanical;
using KaifaTools.DuctRoute;
using IWin32Window = System.Windows.Forms.IWin32Window;

namespace KaifaTools.Commands;

/// <summary>排管竖向避让优化: computes the two end levels of every duct bank so it clears wells, drains and other banks.</summary>
[Transaction(TransactionMode.Manual)]
public class DuctRouteCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => Open(commandData.Application);

    /// <summary>Opens the window (also used by tests that have no ExternalCommandData).</summary>
    public static Result Open(UIApplication app)
    {
        var uidoc = app.ActiveUIDocument; var doc = uidoc.Document;
        var open = System.Windows.Forms.Application.OpenForms.Cast<System.Windows.Forms.Form>().FirstOrDefault(f => f.GetType().FullName == typeof(DuctRouteForm).FullName);
        if (open is not null) { open.Activate(); return Result.Succeeded; }
        var rules = RouteRules.Load(out var err);
        var form = new DuctRouteForm(rules, uidoc.Selection.GetElementIds().Count);
        form.SetBoxes(new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_VolumeOfInterest).WhereElementIsNotElementType().Select(e => (e.Id.Value, e.Name)));
        var handler = new RouteHandler { Form = form };
        var ev = ExternalEvent.Create(handler);
        form.Requested = req => { if (req.Kind == ReqKind.Cancel) handler.Cancel(); else { handler.Post(req); ev.Raise(); } };
        if (err is not null) form.ShowMessage(err);
        form.Show(new RevitOwner());
        return Result.Succeeded;
    }

    private sealed class RevitOwner : IWin32Window { public IntPtr Handle => Process.GetCurrentProcess().MainWindowHandle; }
}

public sealed class RouteHandler : IExternalEventHandler
{
    public DuctRouteForm? Form { get; set; }
    RouteRequest? _req; Scene? _scene; CancellationTokenSource? _cts; Dictionary<long, List<long>> _blocked = new();
    public void Post(RouteRequest r) => _req = r;
    public void Cancel() => _cts?.Cancel();
    public string GetName() => "KAIFA 排管竖向避让优化";

    public void Execute(UIApplication app)
    {
        var req = _req; _req = null; var form = Form; if (req is null || form is null) return;
        var uidoc = app.ActiveUIDocument; var doc = uidoc.Document;
        try
        {
            switch (req.Kind)
            {
                case ReqKind.Compute: Compute(doc, uidoc, req, form); break;
                case ReqKind.ImportResult: ImportResult(doc, uidoc, req, form); break;
                case ReqKind.PickOne: PickOne(app, doc, uidoc, req, form); break;
                case ReqKind.CutWells: CutWells(app, doc, uidoc, req, form); break;
                case ReqKind.RestoreCuts:
                    {
                        var f = Cutter.LatestBackup(doc);
                        if (f is null) { form.ShowMessage("没有找到本文档的断开备份"); form.Busy(false); break; }
                        var keep = uidoc.Selection.GetElementIds(); uidoc.Selection.SetElementIds(new List<ElementId>());
                        int ok; List<string> failed;
                        try { (ok, failed) = Cutter.Restore(doc, f, app); } finally { try { uidoc.Selection.SetElementIds(keep); } catch { } }
                        form.ShowMessage($"已按 {Path.GetFileName(f)} 接回 {ok} 根，失败 {failed.Count} 根");
                        foreach (var x in failed.Take(30)) form.Log("  失败 " + x);
                        form.Busy(false); break;
                    }
                case ReqKind.Round: SolverTool(doc, uidoc, req, form, "round", "取整"); break;
                case ReqKind.Align: SolverTool(doc, uidoc, req, form, "align", "对齐"); break;
                case ReqKind.Check: SolverTool(doc, uidoc, req, form, "check", "检查"); break;
                case ReqKind.JogPick:
                case ReqKind.JogPlane:
                    {
                        try
                        {
                            var pre = uidoc.Selection.GetElementIds().Select(i => doc.GetElement(i)).OfType<MEPCurve>().ToList();
                            MEPCurve m;
                            if (pre.Count == 1) m = pre[0];     // select first, then click the tool
                            else
                            {
                                form.ShowMessage("请点一根要绕行的水管、风管、桥架或线管（Esc 取消）…");
                                var r = uidoc.Selection.PickObject(Autodesk.Revit.UI.Selection.ObjectType.Element, new MepFilter(), "点一根管线");
                                m = (MEPCurve)doc.GetElement(r.ElementId);
                            }
                            var plan = Jogger.Plan(doc, m, new JogOptions { Mode = req.Kind == ReqKind.JogPlane ? "plane" : "vertical" }, out var err);
                            if (plan is null) { form.ShowMessage("不能绕行：" + err); form.Busy(false); break; }
                            form.Log(plan.Summary);
                            var td = new Autodesk.Revit.UI.TaskDialog("管线绕行") { MainContent = plan.Summary + "\n\n会把这一段切开、中间段绕过去，4 个接头生成 45° 弯头，远端重新接好。出错会整体撤销。继续？", CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No };
                            if (td.Show() != TaskDialogResult.Yes) { form.ShowMessage("已取消"); form.Busy(false); break; }
                            var (ok, msg, backup) = Jogger.Apply(doc, plan, app);
                            form.ShowMessage(msg + (backup.Length > 0 ? "。备份：" + backup : ""));
                        }
                        catch (Autodesk.Revit.Exceptions.OperationCanceledException) { form.ShowMessage("已取消点选"); }
                        form.Busy(false); break;
                    }
                case ReqKind.JogPath:
                    {
                        var ms = uidoc.Selection.GetElementIds().Select(i => doc.GetElement(i)).OfType<MEPCurve>().ToList();
                        if (ms.Count == 0) { form.ShowMessage("请先在 Revit 里选要绕行的管线（可以多根）"); form.Busy(false); break; }
                        var plans = new List<JogPlan>(); var notes = new List<string>();
                        foreach (var m in ms) { var p = Jogger.PlanPath(doc, m, new JogOptions(), out var err); if (p is null) notes.Add($"{m.Id}：{err}"); else plans.Add(p); }
                        foreach (var n in notes) form.Log("  " + n);
                        foreach (var p in plans) form.Log("  " + p.Summary);
                        if (plans.Count == 0) { form.ShowMessage("没有能绕行的（见日志）"); form.Busy(false); break; }
                        var td = new Autodesk.Revit.UI.TaskDialog("沿障碍外沿绕行") { MainContent = $"{plans.Count} 根管线沿障碍外沿绕过去（障碍按真实外形外扩，走最短路，多个转折点，每个转折点生成弯头；会改平面位置）。可用“还原绕行”一次接回。继续？", CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No };
                        if (td.Show() != TaskDialogResult.Yes) { form.ShowMessage("已取消"); form.Busy(false); break; }
                        var keep = uidoc.Selection.GetElementIds(); uidoc.Selection.SetElementIds(new List<ElementId>());
                        (bool ok, string message) res;
                        try { res = Jogger.ApplyBundle(doc, plans, app); } finally { try { uidoc.Selection.SetElementIds(keep); } catch { } }
                        form.ShowMessage(res.message); form.Busy(false); break;
                    }
                case ReqKind.JogCorridor:
                    {
                        var ms = uidoc.Selection.GetElementIds().Select(i => doc.GetElement(i)).OfType<MEPCurve>().ToList();
                        if (ms.Count == 0) { form.ShowMessage("请先在 Revit 里选要协调的水管、风管、桥架、线管（可以很多根，互相交叉也行）"); form.Busy(false); break; }
                        form.ShowMessage("协调规划中 …"); form.Refresh();
                        var plans = Jogger.PlanCoordinated(doc, ms, new JogOptions(), out var notes);
                        foreach (var n in notes) form.Log("  " + n);
                        foreach (var p in plans) form.Log("    " + p.Summary);
                        if (plans.Count == 0) { form.ShowMessage("没有能绕行的（见日志）"); form.Busy(false); break; }
                        var td = new Autodesk.Revit.UI.TaskDialog("走廊协调绕行") { MainContent = $"选了 {ms.Count} 根，协调后 {plans.Count} 根要绕行（优先上下错开，不行才改平面；平行的并成一束；互相冲突的方案已排除）。一次确认全部执行，可用“还原绕行”一次接回。继续？", CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No };
                        if (td.Show() != TaskDialogResult.Yes) { form.ShowMessage("已取消"); form.Busy(false); break; }
                        var keep = uidoc.Selection.GetElementIds(); uidoc.Selection.SetElementIds(new List<ElementId>());
                        (bool ok, string message) res;
                        try { res = Jogger.ApplyBundle(doc, plans, app); } finally { try { uidoc.Selection.SetElementIds(keep); } catch { } }
                        form.ShowMessage(res.message); form.Busy(false); break;
                    }
                case ReqKind.JogAuto:
                    {
                        var ms = uidoc.Selection.GetElementIds().Select(i => doc.GetElement(i)).OfType<MEPCurve>().ToList();
                        if (ms.Count == 0) { form.ShowMessage("请先在 Revit 里选要处理的水管、风管、桥架、线管（可以很多根）"); form.Busy(false); break; }
                        var ask = new Autodesk.Revit.UI.TaskDialog("自动规划多根绕行") { MainInstruction = $"选了 {ms.Count} 根，怎么绕？", CommonButtons = TaskDialogCommonButtons.Cancel };
                        ask.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "竖向（从障碍上面或下面绕，不改平面位置）");
                        ask.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "平面（从障碍左边或右边绕，平行的并成一束一起平移）");
                        var choice = ask.Show(); if (choice != TaskDialogResult.CommandLink1 && choice != TaskDialogResult.CommandLink2) { form.ShowMessage("已取消"); form.Busy(false); break; }
                        var plans = Jogger.PlanAuto(doc, ms, new JogOptions { Mode = choice == TaskDialogResult.CommandLink2 ? "plane" : "vertical" }, out var notes);
                        foreach (var n in notes) form.Log("  " + n);
                        foreach (var p in plans) form.Log("  " + p.Summary);
                        if (plans.Count == 0) { form.ShowMessage("没有能绕行的（见日志）"); form.Busy(false); break; }
                        var td = new Autodesk.Revit.UI.TaskDialog("自动规划多根绕行") { MainContent = $"共 {plans.Count} 根要绕行，{notes.Count} 条说明见日志。一次确认后全部执行，出错的那根会撤销自己；可用“还原绕行”一次全部接回。继续？", CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No };
                        if (td.Show() != TaskDialogResult.Yes) { form.ShowMessage("已取消"); form.Busy(false); break; }
                        var keep = uidoc.Selection.GetElementIds(); uidoc.Selection.SetElementIds(new List<ElementId>());
                        (bool ok, string message) res;
                        try { res = Jogger.ApplyBundle(doc, plans, app); } finally { try { uidoc.Selection.SetElementIds(keep); } catch { } }
                        form.ShowMessage(res.message); form.Busy(false); break;
                    }
                case ReqKind.JogBundle:
                    {
                        var ms = uidoc.Selection.GetElementIds().Select(i => doc.GetElement(i)).OfType<MEPCurve>().ToList();
                        var plans = Jogger.PlanBundle(doc, ms, new JogOptions { Mode = "plane" }, out var err);
                        if (plans is null) { form.ShowMessage("不能一起绕行：" + err); form.Busy(false); break; }
                        foreach (var p in plans) form.Log("  " + p.Summary);
                        var td = new Autodesk.Revit.UI.TaskDialog("一束管线平面绕行") { MainContent = $"{plans.Count} 根平行管线一起平移绕开障碍（会改平面位置），保持互相平行。继续？", CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No };
                        if (td.Show() != TaskDialogResult.Yes) { form.ShowMessage("已取消"); form.Busy(false); break; }
                        var (ok, msg) = Jogger.ApplyBundle(doc, plans, app); form.ShowMessage(msg); form.Busy(false); break;
                    }
                case ReqKind.JogRestore:
                    {
                        var f = Jogger.LatestBackup(doc);
                        if (f is null) { form.ShowMessage("没有找到本文档的绕行备份"); form.Busy(false); break; }
                        var (ok, msg) = Jogger.Restore(doc, f, app); form.ShowMessage(msg); form.Busy(false); break;
                    }
                case ReqKind.GeneralCheck:
                    {
                        var ids = uidoc.Selection.GetElementIds();
                        if (ids.Count == 0) { form.ShowMessage("请先在 Revit 里选要检查的水管、风管、桥架、线管或排管"); form.Busy(false); break; }
                        form.ShowMessage("检查中（用 Revit 自带的实体相交，每个构件约零点几秒）…"); form.Refresh();
                        var list = GeneralCheck.Run(doc, ids, 400, m => form.Log(m));
                        var dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools"); Directory.CreateDirectory(dir);
                        var csv = Path.Combine(dir, $"碰撞检查_{DateTime.Now:yyyyMMdd-HHmmss}.csv"); File.WriteAllText(csv, GeneralCheck.ToCsv(list));
                        foreach (var cl in list.Take(40)) form.Log($"  {cl.Id} {cl.Category} {cl.System}：撞 {cl.With.Count} 个（{string.Join("、", cl.With.Take(4).Select(w => $"{w.other}·{w.category}"))}）");
                        form.ShowMessage($"检查了 {ids.Count} 个构件，{list.Count} 个有碰撞（只读，没改模型）。清单：{csv}");
                        if (list.Count > 0) uidoc.Selection.SetElementIds(list.Select(x => new ElementId(x.Id)).ToList());
                        form.Busy(false); break;
                    }
                case ReqKind.Apply:
                    {
                        if (_scene is null) { form.ShowMessage("请先计算"); form.Busy(false); break; }
                        form.ShowMessage("写入中，请稍候（Revit 会暂时不响应）…"); form.Refresh();
                        var keep = uidoc.Selection.GetElementIds();
                        uidoc.Selection.SetElementIds(new List<ElementId>());   // 选着几百根时，属性面板每提交一次就刷新一次，会慢几十倍
                        WriteResult r;
                        try { r = Writer.Apply(doc, _scene.Movable.Where(b => b.Changed), app); }
                        finally { try { uidoc.Selection.SetElementIds(keep); } catch { } }
                        form.ShowMessage($"已写入 {r.Written} 根，无变化 {r.Same} 根，失败 {r.Failed.Count} 根。备份：{r.BackupFile}。写入后可 Ctrl+Z 撤销。");
                        foreach (var f in r.Failed.Take(30)) form.Log("  失败 " + f);
                        _blocked = r.BlockedByDims; form.SetBlocked(_blocked.Count, _blocked.Values.SelectMany(x => x).Distinct().Count());
                        form.Busy(false); break;
                    }
                case ReqKind.DeleteDimsAndRetry:
                    {
                        if (_scene is null || _blocked.Count == 0) { form.Busy(false); break; }
                        var (n, file) = DimTools.DeleteWithBackup(doc, _blocked.Values.SelectMany(x => x));
                        form.Log($"已删除 {n} 个挡路的尺寸标注，信息备份在 {file}");
                        var ids = _blocked.Keys.ToHashSet();
                        var keep2 = uidoc.Selection.GetElementIds(); uidoc.Selection.SetElementIds(new List<ElementId>());
                        WriteResult r;
                        try { r = Writer.Apply(doc, _scene.Movable.Where(b => ids.Contains(b.Id)), app); }
                        finally { try { uidoc.Selection.SetElementIds(keep2); } catch { } }
                        form.ShowMessage($"重写 {r.Written} 根，仍失败 {r.Failed.Count} 根。");
                        foreach (var f in r.Failed.Take(30)) form.Log("  失败 " + f);
                        _blocked = r.BlockedByDims; form.SetBlocked(_blocked.Count, _blocked.Values.SelectMany(x => x).Distinct().Count());
                        form.Busy(false); break;
                    }
                case ReqKind.Restore:
                    {
                        var f = Writer.LatestBackup(doc);
                        if (f is null) { form.ShowMessage("没有找到本文档的备份"); form.Busy(false); break; }
                        form.ShowMessage("还原中，请稍候 …"); form.Refresh();
                        var keep3 = uidoc.Selection.GetElementIds(); uidoc.Selection.SetElementIds(new List<ElementId>());
                        int ok; List<string> failed;
                        try { (ok, failed) = Writer.Restore(doc, f, app); }
                        finally { try { uidoc.Selection.SetElementIds(keep3); } catch { } }
                        form.ShowMessage($"已按 {Path.GetFileName(f)} 还原 {ok} 根，失败 {failed.Count} 根");
                        foreach (var x in failed.Take(30)) form.Log("  失败 " + x);
                        form.Busy(false); break;
                    }
                case ReqKind.Select:
                    uidoc.Selection.SetElementIds(new[] { new ElementId(req.SelectId) });
                    try { uidoc.ShowElements(new ElementId(req.SelectId)); } catch { }
                    break;
            }
        }
        catch (Exception ex) { form.ShowMessage("出错：" + ex.Message); form.Log(ex.ToString()); form.Busy(false); }
    }

    static (Scene? scene, RouteRules rules) ReadScope(Document doc, UIDocument uidoc, RouteRequest req, DuctRouteForm form)
    {
        var rules = req.Rules ?? new RouteRules(); form.ShowMessage("正在读取模型 …");
        ICollection<ElementId>? sel = null; double[]? region = null;
        if (req.Scope == ScopeKind.Selection) { sel = uidoc.Selection.GetElementIds(); if (sel.Count == 0) { form.ShowMessage("没有选中构件，请先在 Revit 里选排管，或改选范围框"); form.Busy(false); return (null, rules); } }
        else if (req.Scope == ScopeKind.ScopeBox)
        {
            var bb = doc.GetElement(new ElementId(req.ScopeBoxId))?.get_BoundingBox(null);
            if (bb is null) { form.ShowMessage("请先选一个范围框"); form.Busy(false); return (null, rules); }
            region = new[] { bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y };
        }
        var scene = Extractor.Read(doc, rules, sel, region, m => form.BeginInvoke(() => form.Log(m)));
        if (!scene.Movable.Any()) { form.ShowMessage("范围内没有找到排管（族名要以 TELCO / ELV / LV- / ELE- 开头）"); form.Busy(false); return (null, rules); }
        return (scene, rules);
    }

    /// <summary>Like Jog in Thermist: click one bank, only that bank moves, everything else is an obstacle.</summary>
    void PickOne(UIApplication app, Document doc, UIDocument uidoc, RouteRequest req, DuctRouteForm form)
    {
        try
        {
            var pre = uidoc.Selection.GetElementIds().Select(i => doc.GetElement(i)).Where(e => new BankFilter().AllowElement(e)).ToList();
            if (pre.Count != 1)     // select first, then click the tool; otherwise ask for a click
            {
                form.ShowMessage("请在 Revit 里点一根排管（Esc 取消）…");
                var r = uidoc.Selection.PickObject(Autodesk.Revit.UI.Selection.ObjectType.Element, new BankFilter(), "点一根排管");
                uidoc.Selection.SetElementIds(new[] { r.ElementId });
            }
            Compute(doc, uidoc, req with { Scope = ScopeKind.Selection }, form);
        }
        catch (Autodesk.Revit.Exceptions.OperationCanceledException) { form.ShowMessage("已取消点选"); form.Busy(false); }
    }

    sealed class MepFilter : Autodesk.Revit.UI.Selection.ISelectionFilter
    {
        public bool AllowElement(Element e) => e is MEPCurve;
        public bool AllowReference(Reference r, XYZ p) => false;
    }

    sealed class BankFilter : Autodesk.Revit.UI.Selection.ISelectionFilter
    {
        public bool AllowElement(Element e) => e is FamilyInstance fi && fi.Category?.Id.Value == (long)BuiltInCategory.OST_StructuralFraming && Extractor.BankKind(fi.Symbol?.FamilyName ?? "") is not null;
        public bool AllowReference(Reference r, XYZ p) => false;
    }

    void CutWells(UIApplication app, Document doc, UIDocument uidoc, RouteRequest req, DuctRouteForm form)
    {
        var (scene, rules) = ReadScope(doc, uidoc, req, form); if (scene is null) return;
        var plans = Cutter.Plan(scene, 2.5, out var skipped);
        foreach (var s in skipped) form.Log("  " + s);
        if (plans.Count == 0) { form.ShowMessage("没有需要在 TELCO 井处断开的排管"); form.Busy(false); return; }
        foreach (var p in plans) form.Log($"  {p.BankId} {p.Kind}：在 {string.Join("、", p.Wells)} 处断开成 {p.Pieces.Count} 段（{string.Join(" / ", p.Pieces.Select(x => $"{x.b - x.a:0.0}m"))}）");
        var td = new Autodesk.Revit.UI.TaskDialog("井处断开") { MainContent = $"有 {plans.Count} 根排管穿过 TELCO 井，将在井处断开（井里那一段去掉，两边标高保持原样一致）。\n断开信息会先备份，可用“还原断开”接回。继续？", CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No };
        if (td.Show() != TaskDialogResult.Yes) { form.ShowMessage("已取消"); form.Busy(false); return; }
        var keep = uidoc.Selection.GetElementIds(); uidoc.Selection.SetElementIds(new List<ElementId>());
        (int cut, List<string> failed, string backup) r;
        try { r = Cutter.Apply(doc, plans, app); } finally { try { uidoc.Selection.SetElementIds(keep); } catch { } }
        form.ShowMessage($"已断开 {r.cut} 根，失败 {r.failed.Count} 根。备份：{r.backup}。断开后请再点“计算”重新求解。");
        foreach (var f in r.failed.Take(30)) form.Log("  失败 " + f);
        form.Busy(false);
    }

    /// <summary>Round / align / check run in the worker process on the levels the model has now; the result is shown like any other result.</summary>
    void SolverTool(Document doc, UIDocument uidoc, RouteRequest req, DuctRouteForm form, string mode, string title)
    {
        var (scene, rules) = ReadScope(doc, uidoc, req, form); if (scene is null) return;
        _scene = scene; var mov = scene.Movable.ToList(); var sw = Stopwatch.StartNew();
        Task.Run(() =>
        {
            try
            {
                SolverRunner.Run(scene, rules, m => form.BeginInvoke(() => form.ShowMessage(m)), m => form.BeginInvoke(() => form.Log(m)), CancellationToken.None, mode);
                int ch = mov.Count(b => b.Changed), bad = mov.Count(b => b.Status.Contains("碰撞")), cov = mov.Count(b => b.Status.Contains("覆土"));
                form.BeginInvoke(() => form.ShowResult(mov, mode == "check"
                    ? $"检查完成：{mov.Count} 根排管，有碰撞 {bad} 根，覆土不足 {cov} 根（悬停行可看碰撞对象，双击定位）。用时 {sw.Elapsed.TotalSeconds:0} s。"
                    : $"{title}完成：改 {ch} 根。确认后点“写入 Revit”。用时 {sw.Elapsed.TotalSeconds:0} s。"));
            }
            catch (Exception ex) { form.BeginInvoke(() => { form.ShowMessage(title + "出错：" + ex.Message); form.Log(ex.ToString()); form.Busy(false); }); }
        });
    }

    /// <summary>Reads a result file ([{id, z0, z1}, ...], bottom elevations in metres) made by another tool such as the Rhino optimiser.</summary>
    void ImportResult(Document doc, UIDocument uidoc, RouteRequest req, DuctRouteForm form)
    {
        var rules = req.Rules ?? new RouteRules(); form.ShowMessage("正在读取模型 …");
        ICollection<ElementId>? sel = null; double[]? region = null;
        if (req.Scope == ScopeKind.Selection) { sel = uidoc.Selection.GetElementIds(); if (sel.Count == 0) { form.ShowMessage("没有选中构件"); form.Busy(false); return; } }
        else if (req.Scope == ScopeKind.ScopeBox)
        {
            var bb = doc.GetElement(new ElementId(req.ScopeBoxId))?.get_BoundingBox(null);
            if (bb is null) { form.ShowMessage("请先选一个范围框"); form.Busy(false); return; }
            region = new[] { bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y };
        }
        var scene = Extractor.Read(doc, rules, sel, region, m => form.BeginInvoke(() => form.Log(m)));
        var mov = scene.Movable.ToList(); int n = 0, miss = 0;
        using (var jd = System.Text.Json.JsonDocument.Parse(File.ReadAllText(req.Path!)))
            foreach (var it in jd.RootElement.EnumerateArray())
            {
                if (!scene.ById.TryGetValue(it.GetProperty("id").GetInt64(), out var b) || !b.Movable) { miss++; continue; }
                b.Z0 = it.GetProperty("z0").GetDouble(); b.Z1 = it.GetProperty("z1").GetDouble(); n++;
            }
        foreach (var b in mov) b.Status = b.Changed ? "已优化(导入)" : "无需改";
        _scene = scene;
        form.ShowResult(mov, $"已导入 {n} 根（文件里有 {miss} 根在当前范围外或不是可调排管）：改 {mov.Count(b => b.Changed)} 根。确认后点“写入 Revit”。");
    }

    void Compute(Document doc, UIDocument uidoc, RouteRequest req, DuctRouteForm form)
    {
        var rules = req.Rules ?? new RouteRules(); form.ShowMessage("正在读取模型 …");
        ICollection<ElementId>? sel = null; double[]? region = null;
        if (req.Scope == ScopeKind.Selection) { sel = uidoc.Selection.GetElementIds(); if (sel.Count == 0) { form.ShowMessage("没有选中构件，请先在 Revit 里框选排管"); form.Busy(false); return; } }
        else if (req.Scope == ScopeKind.ScopeBox)
        {
            var bb = doc.GetElement(new ElementId(req.ScopeBoxId))?.get_BoundingBox(null);
            if (bb is null) { form.ShowMessage("请先选一个范围框"); form.Busy(false); return; }
            region = new[] { bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y };
        }
        var sw = Stopwatch.StartNew();
        var scene = Extractor.Read(doc, rules, sel, region, m => form.BeginInvoke(() => form.Log(m)));
        var mov = scene.Movable.ToList();
        if (mov.Count == 0) { form.ShowMessage("范围内没有找到排管（族名要以 TELCO / ELV / LV- / ELE- 开头）"); form.Busy(false); return; }
        form.Log($"读取用时 {sw.Elapsed.TotalSeconds:0.0} s，可调排管 {mov.Count} 根，开始求解（Revit 可以继续操作，但请先不要改模型）…");
        _scene = scene; _cts = new CancellationTokenSource(); var ct = _cts.Token;
        Task.Run(() =>
        {
            try
            {
                SolverRunner.Run(scene, rules, m => form.BeginInvoke(() => form.ShowMessage(m)), m => form.BeginInvoke(() => form.Log(m)), ct);
                int ch = mov.Count(b => b.Changed), bad = mov.Count(b => b.Status.Contains("碰撞")), cover = mov.Count(b => b.Status.Contains("覆土"));
                form.BeginInvoke(() => form.ShowResult(mov, $"完成：{mov.Count} 根排管 → 改 {ch} 根；仍有碰撞 {bad} 根，覆土不足 {cover} 根。用时 {sw.Elapsed.TotalSeconds:0} s。确认后点“写入 Revit”。"));
            }
            catch (OperationCanceledException) { form.BeginInvoke(() => { form.ShowMessage("已停止"); form.Busy(false); }); }
            catch (Exception ex) { form.BeginInvoke(() => { form.ShowMessage("求解出错：" + ex.Message); form.Log(ex.ToString()); form.Busy(false); }); }
        });
    }
}
