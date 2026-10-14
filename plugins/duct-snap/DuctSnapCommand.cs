using System.Diagnostics;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctSnap;
using IWin32Window = System.Windows.Forms.IWin32Window;

namespace KaifaTools.Commands;

/// <summary>
/// 排管贴井内壁: moves the ends of 排管 (structural framing) onto the inner walls of the wells they reach.
/// Only the plan position changes; end elevations are kept. The window is modeless: it lists what would change,
/// and "执行贴合" changes the model in one undoable transaction (Ctrl+Z).
/// </summary>
[Transaction(TransactionMode.Manual)]
public class DuctSnapCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
    {
        var uidoc = commandData.Application.ActiveUIDocument;
        var doc = uidoc.Document;

        var open = System.Windows.Forms.Application.OpenForms.Cast<System.Windows.Forms.Form>()
            .FirstOrDefault(f => f.GetType().FullName == typeof(DuctSnapForm).FullName);
        if (open is not null) { open.Activate(); return Result.Succeeded; }

        var report = DuctSnapEngine.Analyse(doc, uidoc.Selection.GetElementIds());

        var handler = new DuctSnapHandler();
        var ev = ExternalEvent.Create(handler);
        var form = new DuctSnapForm(report);
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
internal sealed class DuctSnapHandler : IExternalEventHandler
{
    public DuctSnapForm? Form { get; set; }
    private DuctSnapRequest? _pending;

    public void Post(DuctSnapRequest request) => _pending = request;

    public string GetName() => "KAIFA 排管贴井内壁";

    public void Execute(UIApplication app)
    {
        var req = _pending;
        _pending = null;
        if (req is null || Form is null || Form.IsDisposed) return;

        var uidoc = app.ActiveUIDocument;
        var doc = uidoc.Document;
        try
        {
            switch (req.Kind)
            {
                case DuctSnapRequestKind.Jump:
                    var eid = new ElementId(req.Id);
                    if (doc.GetElement(eid) is null)
                    {
                        Form.ShowMessage("这根排管已经不在模型里了，请点“重新检查”。");
                        return;
                    }
                    uidoc.Selection.SetElementIds(new List<ElementId> { eid });
                    uidoc.ShowElements(eid);
                    break;

                case DuctSnapRequestKind.Recheck:
                    Form.Update(DuctSnapEngine.Analyse(doc, uidoc.Selection.GetElementIds()));
                    break;

                case DuctSnapRequestKind.Apply:
                    // Analyse again right before writing, so the edits match the model as it is now.
                    var fresh = DuctSnapEngine.Analyse(doc, uidoc.Selection.GetElementIds());
                    var result = DuctSnapEngine.Apply(doc, fresh.Edits);
                    var after = DuctSnapEngine.Analyse(doc, uidoc.Selection.GetElementIds());
                    string text = $"已修改 {result.BanksChanged} 根排管。可按 Ctrl+Z 一次撤销。";
                    if (result.Failed.Count > 0)
                        text += $"\n失败 {result.Failed.Count} 根（Revit 拒绝修改，多为端点已连接其他构件）：" + string.Join("；", result.Failed.Take(3));
                    Form.Update(after, text);
                    break;
            }
        }
        catch (Exception ex)
        {
            Form.ShowMessage("出错：" + ex.Message);
        }
    }
}
