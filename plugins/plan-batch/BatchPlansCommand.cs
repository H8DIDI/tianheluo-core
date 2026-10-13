using System.Diagnostics;
using System.Text;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.PlanBatch;
using IWin32Window = System.Windows.Forms.IWin32Window;
using TaskDialog = Autodesk.Revit.UI.TaskDialog;

namespace KaifaTools.Commands;

/// <summary>
/// 批量生成平面图: pick floor-plan view types on the left and levels on the right,
/// then create one plan view for every combination. Names are "标高_视图类型"; a name that already exists is skipped.
/// </summary>
[Transaction(TransactionMode.Manual)]
public class BatchPlansCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
    {
        var doc = commandData.Application.ActiveUIDocument.Document;

        var types = new FilteredElementCollector(doc).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>()
            .Where(t => t.ViewFamily == ViewFamily.FloorPlan)
            .OrderBy(t => t.Name, StringComparer.CurrentCulture).ToList();
        var levels = new FilteredElementCollector(doc).OfClass(typeof(Level)).Cast<Level>()
            .OrderBy(l => l.Elevation).ToList();
        if (types.Count == 0 || levels.Count == 0)
        {
            TaskDialog.Show("批量生成平面图", "模型里没有找到平面视图类型或标高。");
            return Result.Cancelled;
        }

        using var form = new PlanPickerForm(types.Select(t => t.Name).ToList(), levels.Select(l => l.Name).ToList());
        if (form.ShowDialog(new RevitOwner()) != System.Windows.Forms.DialogResult.OK) return Result.Cancelled;

        var chosenTypes = form.TypeIndexes.Select(i => types[i]).ToList();
        var chosenLevels = form.LevelIndexes.Select(i => levels[i]).ToList();

        // view names must be unique across all views in the model
        var taken = new HashSet<string>(
            new FilteredElementCollector(doc).OfClass(typeof(Autodesk.Revit.DB.View)).Cast<Autodesk.Revit.DB.View>().Select(v => v.Name),
            StringComparer.OrdinalIgnoreCase);

        int created = 0;
        var skipped = new List<string>();
        var failed = new List<string>();
        using (var tx = new Transaction(doc, "KAIFA 批量生成平面图"))
        {
            tx.Start();
            foreach (var level in chosenLevels)
            foreach (var type in chosenTypes)
            {
                string name = $"{level.Name}_{type.Name}";
                if (taken.Contains(name)) { skipped.Add(name); continue; }
                try
                {
                    var view = ViewPlan.Create(doc, type.Id, level.Id);
                    try
                    {
                        view.Name = name;
                    }
                    catch
                    {
                        doc.Delete(view.Id);      // do not leave a view with Revit's default name behind
                        throw;
                    }
                    taken.Add(name);
                    created++;
                }
                catch (Exception ex)
                {
                    failed.Add($"{name}：{ex.Message}");
                }
            }
            tx.Commit();
        }

        var sb = new StringBuilder();
        sb.AppendLine($"已生成 {created} 个平面视图。");
        if (skipped.Count > 0) sb.AppendLine($"\n同名已存在，跳过 {skipped.Count} 个：\n" + string.Join("\n", skipped.Take(12)) + (skipped.Count > 12 ? "\n…" : ""));
        if (failed.Count > 0) sb.AppendLine($"\n失败 {failed.Count} 个：\n" + string.Join("\n", failed.Take(12)) + (failed.Count > 12 ? "\n…" : ""));
        sb.AppendLine("\n不满意可按 Ctrl+Z 一次撤销。");
        TaskDialog.Show("批量生成平面图", sb.ToString());
        return Result.Succeeded;
    }

    /// <summary>Owner window for the picker, so it sits on top of Revit.</summary>
    private sealed class RevitOwner : IWin32Window
    {
        public IntPtr Handle => Process.GetCurrentProcess().MainWindowHandle;
    }
}
