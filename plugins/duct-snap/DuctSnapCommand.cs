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

        // Show the window at once. The first check reads the wells (about 20-30 s), so it runs in the external
        // event right after this command returns. Errors are shown in the window and written to the log.
        var handler = new DuctSnapHandler();
        var ev = ExternalEvent.Create(handler);
        var form = new DuctSnapForm(new SnapReport());
        handler.Form = form;
        form.Requested = req => { handler.Post(req); ev.Raise(); };
        form.ShowMessage("正在读取模型，第一次约 20–30 秒（期间 Revit 会暂停响应）。");
        form.Show(new RevitOwner());
        DuctSnapLog.Write($"打开窗口 · 文档：{doc.Title}");
        handler.Post(new DuctSnapRequest(DuctSnapRequestKind.Recheck));
        ev.Raise();
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
            DuctSnapLog.Write($"请求：{req.Kind}");
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
                    var checkedReport = DuctSnapEngine.Analyse(doc, uidoc.Selection.GetElementIds());
                    DuctSnapLog.Write($"检查完成 {checkedReport.Seconds:0.0} 秒 · {checkedReport.Summary}" +
                                      (checkedReport.RulesError is null ? "" : " · 规则文件：" + checkedReport.RulesError));
                    Form.Update(checkedReport);
                    break;

                case DuctSnapRequestKind.Apply:
                    // Analyse again right before writing, so the edits match the model as it is now.
                    var fresh = DuctSnapEngine.Analyse(doc, uidoc.Selection.GetElementIds());
                    // Ends the user unchecked in the 执行 column are put back to where they are in the model.
                    var chosenEdits = DuctSnapEngine.WithoutEnds(doc, fresh.Edits, req.Excluded ?? new List<(long BankId, int End)>());
                    var result = DuctSnapEngine.Apply(doc, chosenEdits);
                    var after = DuctSnapEngine.Analyse(doc, uidoc.Selection.GetElementIds());
                    string text;
                    if (result.RolledBack)
                    {
                        text = $"标高核对没通过，已全部撤销，模型未改动。核对 {result.Checked} 根，问题 {result.Failed.Count} 根：" + string.Join("；", result.Failed.Take(3));
                    }
                    else
                    {
                        text = $"已修改 {result.BanksChanged} 根排管。已从模型读回核对 {result.Checked} 根的两端：" +
                               $"标高最大变化 {result.MaxElevationDiffMm:0.00} mm（应为 0）。可按 Ctrl+Z 一次撤销。";
                        if (result.Failed.Count > 0)
                            text += $"\n未改成功 {result.Failed.Count} 根（Revit 拒绝修改，多为端点已连接其他构件）：" + string.Join("；", result.Failed.Take(3));
                    }
                    DuctSnapLog.Write($"执行：{text}");
                    Form.Update(after, text);
                    break;
            }
        }
        catch (Exception ex)
        {
            DuctSnapLog.Write("出错：" + ex);
            Form.ShowMessage("出错：" + ex.Message + "（详情见 Documents\\KaifaTools\\排管贴井日志.txt）");
        }
    }
}
