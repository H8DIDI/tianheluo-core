using System.Diagnostics;
using System.Text;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.WellDepth;
using IWin32Window = System.Windows.Forms.IWin32Window;
using TaskDialog = Autodesk.Revit.UI.TaskDialog;

namespace KaifaTools.Commands;

/// <summary>
/// "调整井深" with a review window that stays open while you work in Revit:
///   1. analyse every electrical / telecom / low-voltage well (storm, sewage and valve wells are excluded)
///   2. show the review window (modeless): choose which wells to adjust, see old vs new values,
///      edit values by hand, see why a well is uncertain, and jump to a well in Revit
///   3. write the ticked wells in ONE transaction (Ctrl+Z undoes everything)
/// Revit work (select / write) is requested from the window and runs on Revit's main thread (ExternalEvent).
/// </summary>
[Transaction(TransactionMode.Manual)]
public class AdjustWellDepthCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
    {
        var uidoc = commandData.Application.ActiveUIDocument;
        var doc = uidoc.Document;
        var selection = uidoc.Selection.GetElementIds();

        // a window from an earlier click is still open: bring it to the front instead of opening a second one
        var open = System.Windows.Forms.Application.OpenForms.Cast<System.Windows.Forms.Form>()
            .FirstOrDefault(f => f.GetType().FullName == typeof(ReviewForm).FullName);
        if (open is not null)
        {
            open.Activate();
            return Result.Succeeded;
        }

        var results = WellDepthEngine.Analyse(doc, selection);
        if (results.Count == 0)
        {
            TaskDialog.Show("调整井深", "没有找到需要检查的井（雨水井、污水井、阀门井不在范围内）。");
            return Result.Cancelled;
        }

        var handler = new ReviewHandler { Scope = selection.Count > 0 ? selection.ToList() : null, SelectionCount = selection.Count };
        var ev = ExternalEvent.Create(handler);
        var rows = results.Select(r => new WellRow(r)).ToList();
        var form = new ReviewForm(rows, WellDepthEngine.Rules, WellDepthEngine.RulesError, RulesFile.Path,
            WellDepthEngine.UnmatchedPipes, WellDepthEngine.IgnoredAbovePipes, ReviewHandler.ScopeText(selection.Count, rows.Count));
        handler.Form = form;
        form.Requested = req => { handler.Post(req); ev.Raise(); };
        form.Show(new RevitOwner());
        return Result.Succeeded;
    }

    /// <summary>Owner window for the review form, so it stays on top of Revit.</summary>
    private sealed class RevitOwner : IWin32Window
    {
        public IntPtr Handle => Process.GetCurrentProcess().MainWindowHandle;
    }
}

/// <summary>Runs the window's requests on Revit's main thread.</summary>
internal sealed class ReviewHandler : IExternalEventHandler
{
    public ReviewForm? Form { get; set; }
    public List<ElementId>? Scope { get; set; }   // selection when the window was opened (null = whole model)
    public int SelectionCount { get; set; }
    private ReviewRequest? _pending;

    public void Post(ReviewRequest request) => _pending = request;

    public string GetName() => "KAIFA 调整井深";

    public static string ScopeText(int selectionCount, int wells) =>
        selectionCount > 0 ? $"范围：选中的 {selectionCount} 个构件（检查 {wells} 口井）" : $"范围：全模型（检查 {wells} 口井）";

    public void Execute(UIApplication app)
    {
        var req = _pending;
        _pending = null;
        if (req is null || Form is null || Form.IsDisposed) return;

        var uidoc = app.ActiveUIDocument;
        var doc = uidoc.Document;
        switch (req.Kind)
        {
            case ReviewRequestKind.Jump:
                Jump(uidoc, req.Id);
                break;
            case ReviewRequestKind.Write:
                Write(doc, Form.Rows);
                Reanalyse(doc);     // show the new state of the wells
                break;
            case ReviewRequestKind.Reanalyse:
                Reanalyse(doc);
                break;
        }
    }

    private void Reanalyse(Document doc)
    {
        var results = WellDepthEngine.Analyse(doc, Scope);
        var rows = results.Select(r => new WellRow(r)).ToList();
        Form!.UpdateData(rows, ScopeText(SelectionCount, rows.Count), WellDepthEngine.UnmatchedPipes, WellDepthEngine.IgnoredAbovePipes);
    }

    // ---- jump: select the well and show it. The window stays open. ----

    private static void Jump(UIDocument uidoc, long id)
    {
        var eid = new ElementId(id);
        if (uidoc.Document.GetElement(eid) is null)
        {
            TaskDialog.Show("调整井深", "这口井已经不在模型里了，请重新检查（点“保存规则并重新分析”）。");
            return;
        }
        uidoc.Selection.SetElementIds(new List<ElementId> { eid });
        uidoc.ShowElements(eid);
    }

    // ---- write the ticked wells in ONE transaction ----

    private static void Write(Document doc, List<WellRow> rows)
    {
        var todo = rows.Where(r => r.Write && r.CanWrite).ToList();
        if (todo.Count == 0)
        {
            TaskDialog.Show("调整井深", "没有勾选可写入的井。");
            return;
        }

        int written = 0;
        var failed = new List<string>();
        using (var tx = new Transaction(doc, "KAIFA 调整井深"))
        {
            tx.Start();
            foreach (var r in todo)
            {
                // a SyncOnly well keeps its depth unless the user typed one by hand
                bool wantDepth = r.W.Status is not (WellStatus.SyncOnly or WellStatus.Ok) || !string.IsNullOrWhiteSpace(r.NewDepthInput);
                string? err = WellDepthEngine.Apply(doc, r.W, r.NewBottom!.Value, wantDepth ? r.NewDepth : null);
                if (err is null) written++;
                else failed.Add($"{r.W.Mark} ({r.W.Id}): {err}");
            }
            tx.Commit();
        }

        string csv = ReviewCsv.Write(rows);
        var sb = new StringBuilder();
        sb.AppendLine($"本次写入：{written} 口");
        int manual = todo.Count(r => !string.IsNullOrWhiteSpace(r.NewBottomInput) || !string.IsNullOrWhiteSpace(r.NewDepthInput));
        if (manual > 0) sb.AppendLine($"其中手动覆盖的：{manual} 口");
        int uncertain = todo.Count(r => r.Uncertain.Length > 0);
        if (uncertain > 0) sb.AppendLine($"写入的井里有 {uncertain} 口带“不确定原因”，请在 Revit 里核对。");
        if (failed.Count > 0)
            sb.AppendLine($"\n写入失败 {failed.Count} 口：\n" + string.Join("\n", failed.Take(10)) + (failed.Count > 10 ? "\n…" : ""));
        sb.AppendLine($"\n清单（含写入前的旧值）：{csv}");
        sb.AppendLine("不满意可按 Ctrl+Z 一次撤销。");
        TaskDialog.Show("调整井深", sb.ToString());
    }
}
