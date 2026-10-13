using System.Diagnostics;
using System.Text;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using IWin32Window = System.Windows.Forms.IWin32Window;
using TaskDialog = Autodesk.Revit.UI.TaskDialog;
using KaifaTools.WellDepth;

namespace KaifaTools.Commands;

/// <summary>
/// "调整井深" with a review window:
///   1. analyse every electrical / telecom / low-voltage well (storm, sewage and valve wells are excluded)
///   2. show the review window: choose which wells to adjust, see old vs new values, edit values by hand,
///      see why a well is uncertain, and jump to a well in Revit
///   3. write the ticked wells in ONE transaction (Ctrl+Z undoes everything)
/// If wells are selected before clicking, only those wells are processed.
/// </summary>
[Transaction(TransactionMode.Manual)]
public class AdjustWellDepthCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
    {
        var uidoc = commandData.Application.ActiveUIDocument;
        var doc = uidoc.Document;
        var selection = uidoc.Selection.GetElementIds();

        List<WellRow>? rows = null;   // kept across the window's "export" and re-open, so the user's choices survive
        string scope = "";

        while (true)
        {
            if (rows is null)
            {
                var results = WellDepthEngine.Analyse(doc, selection);
                if (results.Count == 0)
                {
                    TaskDialog.Show("调整井深", "没有找到需要检查的井（雨水井、污水井、阀门井不在范围内）。");
                    return Result.Cancelled;
                }
                rows = results.Select(r => new WellRow(r)).ToList();
                scope = selection.Count > 0 ? $"范围：选中的 {selection.Count} 个构件（检查 {rows.Count} 口井）" : $"范围：全模型（检查 {rows.Count} 口井）";
            }

            using var form = new ReviewForm(rows, WellDepthEngine.Rules, WellDepthEngine.RulesError, RulesFile.Path,
                WellDepthEngine.UnmatchedPipes, WellDepthEngine.IgnoredAbovePipes, scope);
            form.ShowDialog(new RevitOwner());

            switch (form.Action)
            {
                case ReviewAction.Reanalyse:
                    rows = null;          // rules changed: analyse again
                    continue;
                case ReviewAction.Export:
                    string csv = ExportCsv(rows);
                    TaskDialog.Show("调整井深", "清单已导出（含旧值、新值、手动修改、不确定原因）：\n" + csv);
                    continue;              // back to the window with the same choices
                case ReviewAction.Jump:
                    return JumpTo(uidoc, form.JumpId);
                case ReviewAction.Write:
                    return WriteRows(doc, rows, scope);
                default:
                    return Result.Cancelled;
            }
        }
    }

    // ---- jump to a well so the user can edit it by hand -------------------------

    private static Result JumpTo(UIDocument uidoc, long id)
    {
        var eid = new ElementId(id);
        if (uidoc.Document.GetElement(eid) is null)
        {
            TaskDialog.Show("调整井深", "这口井已经不在模型里了，请重新检查。");
            return Result.Cancelled;
        }
        uidoc.Selection.SetElementIds(new List<ElementId> { eid });
        uidoc.ShowElements(eid);
        var label = uidoc.Document.GetElement(eid).get_Parameter(BuiltInParameter.ALL_MODEL_MARK)?.AsString();
        TaskDialog.Show("调整井深",
            $"已定位到井 {(string.IsNullOrEmpty(label) ? id.ToString() : label)}。\n\n" +
            "在 Revit 里用属性面板手动修改后，再点一次“调整井深”即可重新检查。");
        return Result.Succeeded;
    }

    // ---- write the ticked wells in ONE transaction -----------------------------------

    private static Result WriteRows(Document doc, List<WellRow> rows, string scope)
    {
        var todo = rows.Where(r => r.Write && r.CanWrite).ToList();
        if (todo.Count == 0)
        {
            TaskDialog.Show("调整井深", "没有勾选可写入的井。");
            return Result.Cancelled;
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

        string csv = ExportCsv(rows);
        var sb = new StringBuilder();
        sb.AppendLine(scope);
        sb.AppendLine($"规则文件：{RulesFile.Path}");
        sb.AppendLine();
        sb.AppendLine($"本次写入：{written} 口");
        int manual = todo.Count(r => !string.IsNullOrWhiteSpace(r.NewBottomInput) || !string.IsNullOrWhiteSpace(r.NewDepthInput));
        if (manual > 0) sb.AppendLine($"其中手动覆盖的：{manual} 口");
        int review = todo.Count(r => r.Uncertain.Length > 0);
        if (review > 0) sb.AppendLine($"写入的井里有 {review} 口带“不确定原因”，请回到 Revit 核对。");
        if (failed.Count > 0)
            sb.AppendLine($"\n写入失败 {failed.Count} 口：\n" + string.Join("\n", failed.Take(10)) + (failed.Count > 10 ? "\n…" : ""));
        sb.AppendLine($"\n清单（含写入前的旧值）：{csv}");
        sb.AppendLine("不满意可按 Ctrl+Z 一次撤销。");

        TaskDialog.Show("调整井深", sb.ToString());
        return Result.Succeeded;
    }

    // ---- CSV list (old values, new values, manual edits, uncertainty) -----------------

    private static string ExportCsv(List<WellRow> rows)
    {
        string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools");
        Directory.CreateDirectory(dir);
        string path = Path.Combine(dir, $"井深调整清单_{DateTime.Now:yyyyMMdd_HHmmss}.csv");

        static string N(double? v) => v is null ? "" : v.Value.ToString("0");
        static string Q(string s) => "\"" + s.Replace("\"", "\"\"") + "\"";

        var sb = new StringBuilder();
        sb.AppendLine("ElementId,标记,族,专业,状态,写入,手动修改,相对偏移mm,最低管底mm,当前井底标高mm,新井底标高mm,当前井深mm,新井深mm,井深变化mm,匹配管数,匹配管ID,不确定原因");
        foreach (var row in rows)
        {
            var r = row.W;
            bool manual = !string.IsNullOrWhiteSpace(row.NewBottomInput) || !string.IsNullOrWhiteSpace(row.NewDepthInput);
            double? nb = row.NewBottom, nd = row.NewDepth;
            double? change = nd is { } d && r.CurDepthMm is { } cd ? d - cd : null;
            sb.AppendLine(string.Join(",",
                r.Id, Q(r.Mark), Q(r.Family), row.SystemText, row.StatusText,
                row.Write ? "是" : "否", manual ? "是" : "否",
                N(r.OffsetMm), N(r.LowestPipeMm), N(r.CurBottomMm), N(nb),
                N(r.CurDepthMm), N(nd), N(change),
                r.PipeCount, Q(r.PipeIds), Q(row.Uncertain)));
        }
        File.WriteAllText(path, sb.ToString(), new UTF8Encoding(encoderShouldEmitUTF8Identifier: true));
        return path;
    }

    /// <summary>Owner window for the review form, so it sits on top of Revit.</summary>
    private sealed class RevitOwner : IWin32Window
    {
        public IntPtr Handle => Process.GetCurrentProcess().MainWindowHandle;
    }
}
