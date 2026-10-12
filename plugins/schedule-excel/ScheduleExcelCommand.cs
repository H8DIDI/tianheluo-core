using System.Diagnostics;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Microsoft.Win32;
using TaskDialog = Autodesk.Revit.UI.TaskDialog;

namespace THL.Plugins.ScheduleExcel;

/// <summary>明细表导出 Excel / 从 Excel 导入。入口：一个命令里选「导出」或「导入」。</summary>
[Transaction(TransactionMode.Manual)]
public class ScheduleExcelCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
    {
        var doc = data.Application.ActiveUIDocument.Document;
        var dlg = new TaskDialog("明细表 ⇄ Excel")
        {
            MainInstruction = "要做什么？",
            MainContent = "导出：把明细表导成 Excel，在 Excel 里批量修改。\n导入：把改好的 Excel 写回 Revit（先预览改动，确认后才写入，Ctrl+Z 可整体撤销）。",
            CommonButtons = TaskDialogCommonButtons.Cancel,
        };
        dlg.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "导出明细表到 Excel");
        dlg.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "从 Excel 导入修改");
        return dlg.Show() switch
        {
            TaskDialogResult.CommandLink1 => DoExport(doc),
            TaskDialogResult.CommandLink2 => DoImport(doc),
            _ => Result.Cancelled,
        };
    }

    static Result DoExport(Document doc)
    {
        var all = ScheduleService.ListSchedules(doc);
        if (all.Count == 0) { TaskDialog.Show("明细表导出", "当前项目里没有可导出的明细表。"); return Result.Cancelled; }
        var picked = PickSchedules(all);
        if (picked == null || picked.Count == 0) return Result.Cancelled;

        var save = new SaveFileDialog
        {
            Title = "导出明细表", Filter = "Excel 工作簿 (*.xlsx)|*.xlsx",
            FileName = $"{doc.Title}_明细表_{DateTime.Now:yyyyMMdd_HHmm}.xlsx",
        };
        if (save.ShowDialog() != true) return Result.Cancelled;

        try
        {
            int rows = ScheduleService.Export(doc, picked, save.FileName);
            var done = new TaskDialog("导出完成")
            {
                MainInstruction = $"已导出 {picked.Count} 张明细表，共 {rows} 行",
                MainContent = save.FileName + "\n\n提示：只有「RW」列（实例参数）导回时才会生效；表头第 2 行的 RW/RO 和第 1 列 ElementId 请不要修改。",
                CommonButtons = TaskDialogCommonButtons.Close,
            };
            done.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, "打开 Excel 文件");
            if (done.Show() == TaskDialogResult.CommandLink1)
                Process.Start(new ProcessStartInfo(save.FileName) { UseShellExecute = true });
            return Result.Succeeded;
        }
        catch (Exception ex) { TaskDialog.Show("导出失败", ex.Message); return Result.Failed; }
    }

    static Result DoImport(Document doc)
    {
        var open = new OpenFileDialog { Title = "选择要导入的 Excel", Filter = "Excel 工作簿 (*.xlsx)|*.xlsx" };
        if (open.ShowDialog() != true) return Result.Cancelled;

        ImportPlan plan;
        try { plan = ScheduleService.Plan(doc, open.FileName); }
        catch (Exception ex) { TaskDialog.Show("读取失败", "无法读取这个 Excel 文件：\n" + ex.Message); return Result.Failed; }

        if (plan.Changes.Count == 0)
        {
            TaskDialog.Show("没有可导入的改动",
                $"已读取 {plan.SheetsMatched} 张明细表、{plan.Rows} 行，没有发现与 Revit 不同的可写入单元格。" +
                (plan.Issues.Count > 0 ? "\n\n注意：\n" + string.Join("\n", plan.Issues.Take(10)) : ""));
            return Result.Cancelled;
        }

        var sb = new StringBuilder();
        sb.AppendLine($"将修改 {plan.Changes.Count} 个单元格，涉及 {plan.ElementsAffected} 个构件。");
        sb.AppendLine("示例：");
        foreach (var c in plan.Changes.Take(8)) sb.AppendLine($"  {c.Element.Id.Value} · {c.Field}：「{Short(c.Old)}」→「{Short(c.New)}」");
        if (plan.Changes.Count > 8) sb.AppendLine($"  …… 还有 {plan.Changes.Count - 8} 项");
        if (plan.Issues.Count > 0) sb.AppendLine($"\n另有 {plan.Issues.Count} 个提示（已跳过）：\n  " + string.Join("\n  ", plan.Issues.Take(5)));
        sb.AppendLine("\n写入在一个操作里完成，Ctrl+Z 可整体撤销。");

        var confirm = new TaskDialog("确认导入")
        {
            MainInstruction = "确认把这些改动写回 Revit？", MainContent = sb.ToString(),
            CommonButtons = TaskDialogCommonButtons.Yes | TaskDialogCommonButtons.No,
        };
        if (confirm.Show() != TaskDialogResult.Yes) return Result.Cancelled;

        var res = ScheduleService.Apply(doc, plan);
        var msg = $"成功写入 {res.Ok} 项" + (res.Failed.Count > 0 ? $"，{res.Failed.Count} 项失败。" : "。");
        if (res.Failed.Count > 0)
        {
            var log = Path.ChangeExtension(open.FileName, null) + "_导入失败.txt";
            try { File.WriteAllLines(log, res.Failed, new UTF8Encoding(true)); msg += $"\n\n失败明细已保存：\n{log}\n\n" + string.Join("\n", res.Failed.Take(5)); } catch { }
        }
        TaskDialog.Show("导入完成", msg);
        return Result.Succeeded;
    }

    static string Short(string s) => s.Length > 24 ? s[..24] + "…" : s;

    // ------------------------------------------------------------ 选择明细表的小窗口
    static List<ViewSchedule>? PickSchedules(List<ViewSchedule> all)
    {
        List<ViewSchedule>? result = null;
        Brush B(string h) => (Brush)new BrushConverter().ConvertFromString(h)!;
        var win = new Window
        {
            Title = "选择要导出的明细表", Width = 480, Height = 560, WindowStartupLocation = WindowStartupLocation.CenterScreen,
            Background = B("#0B0B0E"), Foreground = B("#F2F4F7"), FontFamily = new FontFamily("Microsoft YaHei UI, Segoe UI"), FontSize = 14, ShowInTaskbar = false,
        };
        var root = new DockPanel { Margin = new Thickness(16) };
        var boxes = all.Select(s => new System.Windows.Controls.CheckBox { Content = s.Name, Foreground = B("#D8DBE3"), Margin = new Thickness(0, 3, 0, 3), IsChecked = false }).ToList();

        var allBox = new System.Windows.Controls.CheckBox { Content = "全选", Foreground = B("#F2F4F7"), Margin = new Thickness(0, 0, 0, 8), FontWeight = FontWeights.SemiBold };
        allBox.Click += (_, _) => boxes.ForEach(b => b.IsChecked = allBox.IsChecked);
        DockPanel.SetDock(allBox, Dock.Top); root.Children.Add(allBox);

        var ok = new System.Windows.Controls.Button { Content = "导出", Padding = new Thickness(22, 7, 22, 7), Margin = new Thickness(8, 0, 0, 0), Background = B("#3B9BFF"), Foreground = Brushes.White, BorderBrush = B("#3B9BFF") };
        var cancel = new System.Windows.Controls.Button { Content = "取消", Padding = new Thickness(18, 7, 18, 7), Background = B("#1C1C24"), Foreground = Brushes.White, BorderBrush = B("#2A2A33") };
        ok.Click += (_, _) => { result = all.Where((s, i) => boxes[i].IsChecked == true).ToList(); win.Close(); };
        cancel.Click += (_, _) => win.Close();
        var row = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 12, 0, 0) };
        row.Children.Add(cancel); row.Children.Add(ok);
        DockPanel.SetDock(row, Dock.Bottom); root.Children.Add(row);

        var panel = new StackPanel(); boxes.ForEach(b => panel.Children.Add(b));
        root.Children.Add(new ScrollViewer { Content = panel, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        win.Content = root; win.KeyDown += (_, e) => { if (e.Key == System.Windows.Input.Key.Escape) win.Close(); };
        win.ShowDialog();
        return result;
    }
}
