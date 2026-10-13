using System.Diagnostics;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.TagCheck;
using IWin32Window = System.Windows.Forms.IWin32Window;
using TaskDialog = Autodesk.Revit.UI.TaskDialog;

namespace KaifaTools.Commands;

/// <summary>
/// 标注检查: lists the elements of the active view that are missing a tag or carry a wrong one.
/// Read-only: it never changes the model. The window stays open; jumping and rechecking run on Revit's main thread.
/// </summary>
[Transaction(TransactionMode.Manual)]
public class TagCheckCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
    {
        var uidoc = commandData.Application.ActiveUIDocument;
        var doc = uidoc.Document;
        var view = doc.ActiveView;

        var open = System.Windows.Forms.Application.OpenForms.Cast<System.Windows.Forms.Form>()
            .FirstOrDefault(f => f.GetType().FullName == typeof(TagCheckForm).FullName);
        if (open is not null) { open.Activate(); return Result.Succeeded; }

        var rows = TagCheckEngine.Check(doc, view, out string summary);
        var standard = TagCheckEngine.LoadStandard(out string? stdError);

        var handler = new TagCheckHandler();
        var ev = ExternalEvent.Create(handler);
        var form = new TagCheckForm(rows, summary, stdError);
        handler.Form = form;
        form.Requested = req => { handler.Post(req); ev.Raise(); };
        form.Show(new RevitOwner());
        return Result.Succeeded;
    }

    private sealed class RevitOwner : IWin32Window
    {
        public IntPtr Handle => Process.GetCurrentProcess().MainWindowHandle;
    }
}

/// <summary>Runs the window's requests on Revit's main thread.</summary>
internal sealed class TagCheckHandler : IExternalEventHandler
{
    public TagCheckForm? Form { get; set; }
    private TagRequest? _pending;

    public void Post(TagRequest request) => _pending = request;

    public string GetName() => "KAIFA 标注检查";

    public void Execute(UIApplication app)
    {
        var req = _pending;
        _pending = null;
        if (req is null || Form is null || Form.IsDisposed) return;

        var uidoc = app.ActiveUIDocument;
        var doc = uidoc.Document;
        switch (req.Kind)
        {
            case TagRequestKind.Jump:
                var eid = new ElementId(req.Id);
                if (doc.GetElement(eid) is null)
                {
                    TaskDialog.Show("标注检查", "这个构件已经不在模型里了，请点“重新检查”。");
                    return;
                }
                uidoc.Selection.SetElementIds(new List<ElementId> { eid });
                uidoc.ShowElements(eid);
                break;
            case TagRequestKind.Arrange:
                int moved;
                using (var tx = new Transaction(doc, "KAIFA 整理标注位置"))
                {
                    tx.Start();
                    moved = TagLayout.Arrange(doc, doc.ActiveView);
                    tx.Commit();
                }
                var after = TagCheckEngine.Check(doc, doc.ActiveView, out string afterSummary);
                Form.Update(after, afterSummary, null);
                TaskDialog.Show("整理标注位置", $"已整理 {moved} 个标注。\n{afterSummary}\n不满意可按 Ctrl+Z 撤销。");
                break;
            case TagRequestKind.Recheck:
                var rows = TagCheckEngine.Check(doc, doc.ActiveView, out string summary);
                var standard = TagCheckEngine.LoadStandard(out string? stdError);
                Form.Update(rows, summary, stdError);
                break;
        }
    }
}
