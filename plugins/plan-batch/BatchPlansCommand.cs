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

        // left: the plan view templates in this model (the only plan view type in most models is "楼层平面")
        var templates = new FilteredElementCollector(doc).OfClass(typeof(Autodesk.Revit.DB.View))
            .Cast<Autodesk.Revit.DB.View>()
            .Where(v => v.IsTemplate && v.ViewType == ViewType.FloorPlan)
            .OrderBy(v => v.Name, StringComparer.CurrentCulture).ToList();
        var floorType = new FilteredElementCollector(doc).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>()
            .FirstOrDefault(t => t.ViewFamily == ViewFamily.FloorPlan);
        var levels = new FilteredElementCollector(doc).OfClass(typeof(Level)).Cast<Level>()
            .OrderBy(l => l.Elevation).ToList();
        if (floorType is null || levels.Count == 0)
        {
            TaskDialog.Show("批量生成平面图", "模型里没有找到平面视图类型或标高。");
            return Result.Cancelled;
        }

        // first row = no template; then one row per model template
        var typeNames = new List<string> { "（不套用样板）" };
        typeNames.AddRange(templates.Select(t => t.Name));
        using var form = new PlanPickerForm(typeNames, levels.Select(l => l.Name).ToList());
        if (form.ShowDialog(new RevitOwner()) != System.Windows.Forms.DialogResult.OK) return Result.Cancelled;

        var chosenTemplates = form.TypeIndexes.Select(i => i == 0 ? null : templates[i - 1]).ToList();
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
            foreach (var template in chosenTemplates)
            {
                string name = template is null ? $"{level.Name}_平面" : $"{level.Name}_{template.Name}";
                if (taken.Contains(name)) { skipped.Add(name); continue; }
                try
                {
                    var view = ViewPlan.Create(doc, floorType.Id, level.Id);
                    try
                    {
                        if (template is not null) view.ViewTemplateId = template.Id;
                        view.Name = name;
                    }
                    catch
                    {
                        doc.Delete(view.Id);      // do not leave a half-made view behind (wrong name or template failed)
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
