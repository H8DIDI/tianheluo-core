using System.Text;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.WellDepth;

namespace KaifaTools.Commands;

/// <summary>
/// One-click "adjust well depth":
///   1. analyse every electrical / telecom well (storm, sewage and valve wells are excluded)
///   2. write a CSV list (old values, new values, matched pipe ids)
///   3. ask once, then write all corrections in ONE transaction (Ctrl+Z undoes everything)
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

        var results = WellDepthEngine.Analyse(doc, selection);
        if (results.Count == 0)
        {
            TaskDialog.Show("调整井深", "没有找到需要检查的井（雨水井、污水井、阀门井不在范围内）。");
            return Result.Cancelled;
        }

        string csvPath = WriteCsv(doc, results);
        var count = results.GroupBy(r => r.Status).ToDictionary(g => g.Key, g => g.Count());
        int Get(WellStatus s) => count.TryGetValue(s, out var n) ? n : 0;
        int toWrite = Get(WellStatus.Adjust) + Get(WellStatus.SyncOnly);

        // ---- show the plan (rules + scope) and let the user decide -------------------
        if (DepthSettings.ShowPlanFirst)
        {
            var choice = ShowPlan(doc, results, selection.Count > 0, count, csvPath);
            if (choice == PlanChoice.Cancel) return Result.Cancelled;
            if (choice == PlanChoice.ListOnly) return Result.Succeeded;
        }

        // ---- write everything in ONE transaction (Ctrl+Z undoes it all) -------------
        int written = 0;
        var failed = new List<string>();
        if (toWrite > 0)
        {
            using var tx = new Transaction(doc, "KAIFA 调整井深");
            tx.Start();
            foreach (var w in results.Where(r => r.Status is WellStatus.Adjust or WellStatus.SyncOnly))
            {
                string? error = WellDepthEngine.Apply(doc, w);
                if (error is null) written++;
                else failed.Add($"{w.Mark} ({w.Id}): {error}");
            }
            tx.Commit();
        }

        var summary = new StringBuilder();
        summary.AppendLine(selection.Count > 0 ? $"范围：选中的构件（检查 {results.Count} 口井）" : $"范围：全模型（检查 {results.Count} 口井）");
        summary.AppendLine("规则：井底标高 = 最低排管底 - 300；井深 = 相对偏移 - 井底标高");
        summary.AppendLine();
        summary.AppendLine($"本次写入：{written} 口（改井深 {Get(WellStatus.Adjust)}，仅同步井底标高 {Get(WellStatus.SyncOnly)}）");
        summary.AppendLine($"原本合规：{Get(WellStatus.Ok)}");
        summary.AppendLine($"需人工复核（未修改）：{Get(WellStatus.Review)}");
        summary.AppendLine($"找不到排管：{Get(WellStatus.NoPipe)}");
        summary.AppendLine($"临时电/无参数（跳过）：{Get(WellStatus.Temporary) + Get(WellStatus.NoParameter)}");
        if (failed.Count > 0)
            summary.AppendLine($"\n写入失败 {failed.Count} 口：\n" + string.Join("\n", failed.Take(10)) + (failed.Count > 10 ? "\n…" : ""));
        summary.AppendLine($"\n清单（含旧值，可核对）：{csvPath}");
        summary.AppendLine("不满意可按 Ctrl+Z 一次撤销。");

        TaskDialog.Show("调整井深", summary.ToString());
        return Result.Succeeded;
    }

    private enum PlanChoice { Apply, ListOnly, Cancel }

    private static PlanChoice ShowPlan(Document doc, List<WellResult> results, bool selectionOnly,
        Dictionary<WellStatus, int> count, string csvPath)
    {
        int Get(WellStatus s) => count.TryGetValue(s, out var n) ? n : 0;
        int toWrite = Get(WellStatus.Adjust) + Get(WellStatus.SyncOnly);
        var excluded = WellDepthEngine.CountExcluded(doc);

        string SystemLine(WellSystem sys, string label)
        {
            int n = results.Count(r => r.System == sys && r.Status != WellStatus.Temporary && r.Status != WellStatus.NoParameter);
            return $"  {label}：{n} 口";
        }

        var scope = new StringBuilder();
        scope.AppendLine(selectionOnly ? "【范围】只处理你选中的井" : "【范围】全模型所有电力/电信/弱电/低压井");
        scope.AppendLine(SystemLine(WellSystem.Em, "11KV/275KV 强电井 (ELE-EM1-I / EM2-L)"));
        scope.AppendLine(SystemLine(WellSystem.Telco, "TELCO 电信井 (含室内井)"));
        scope.AppendLine(SystemLine(WellSystem.Elv, "ELV 弱电井 (MANHOLE / PIT)"));
        scope.AppendLine(SystemLine(WellSystem.Lv, "LV 低压拉线井"));
        scope.AppendLine($"  不处理：雨水井 {excluded["雨水井"]}、污水/排水井 {excluded["污水/排水井"]}、阀门井 {excluded["阀门井"]}（始终排除）");
        scope.AppendLine($"  跳过：注释含“临时”的井 {Get(WellStatus.Temporary)} 口");
        scope.AppendLine();
        scope.AppendLine("【预览】");
        scope.AppendLine($"  需改井深 {Get(WellStatus.Adjust)}　仅同步井底标高 {Get(WellStatus.SyncOnly)}　已合规 {Get(WellStatus.Ok)}");
        scope.AppendLine($"  需人工复核(不改) {Get(WellStatus.Review)}　找不到排管 {Get(WellStatus.NoPipe)}");

        var rules = new StringBuilder();
        rules.AppendLine($"1. 井底标高 = 最低接入排管底 - {DepthSettings.ClearanceMm:0} mm");
        rules.AppendLine("2. 井深 = 相对标高的偏移 - 井底标高（写入“井内深度”，LV 井同时写“深度”，并同步“井底标高”）");
        rules.AppendLine("3. 接入排管：同专业的排管/线管，碰到井的平面范围；管头没贴井壁时，" +
                         $"距离 {DepthSettings.MaxGapMm:0} mm 内、且该井是最近的同专业井，也算接入");
        rules.AppendLine("4. 排管取管底（底对正），线管取中心线减半径；斜管取进井处的标高");
        rules.AppendLine($"5. 误差在 ±{DepthSettings.ToleranceMm:0} mm 内视为已合规，不修改");
        rules.AppendLine($"6. 以下情况只列入清单、不自动修改：新井深大于 {DepthSettings.MaxDepthMm:0} 或小于 {DepthSettings.MinDepthMm:0} mm；" +
                         $"最低排管比次低的还低 {DepthSettings.MaxSpreadMm:0} mm 以上");
        rules.AppendLine("7. 被他人借用的井会跳过；全部修改在一个事务里，Ctrl+Z 可一次撤销");

        var dialog = new TaskDialog("调整井深 - 确认规则与范围")
        {
            MainInstruction = toWrite == 0 ? "没有需要自动修改的井" : $"准备调整 {toWrite} 口井",
            MainContent = scope.ToString(),
            ExpandedContent = "【规则】" + Environment.NewLine + rules + Environment.NewLine + "清单：" + csvPath,
            CommonButtons = TaskDialogCommonButtons.Cancel,
            DefaultButton = TaskDialogResult.Cancel,
        };
        if (toWrite > 0)
            dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink1, $"开始调整（{toWrite} 口）", "按上面的规则和范围写入模型");
        dialog.AddCommandLink(TaskDialogCommandLinkId.CommandLink2, "只导出清单，不修改", "清单已保存，可用 Excel 核对后再来");

        return dialog.Show() switch
        {
            TaskDialogResult.CommandLink1 => PlanChoice.Apply,
            TaskDialogResult.CommandLink2 => PlanChoice.ListOnly,
            _ => PlanChoice.Cancel,
        };
    }

    private static string WriteCsv(Document doc, List<WellResult> rows)
    {
        string dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools");
        Directory.CreateDirectory(dir);
        string path = Path.Combine(dir, $"井深调整清单_{DateTime.Now:yyyyMMdd_HHmmss}.csv");

        string StatusText(WellStatus s) => s switch
        {
            WellStatus.Ok => "已合规",
            WellStatus.Adjust => "需改井深",
            WellStatus.SyncOnly => "仅同步井底标高",
            WellStatus.Review => "需复核",
            WellStatus.NoPipe => "无排管",
            WellStatus.Temporary => "临时电跳过",
            _ => "无参数",
        };
        static string N(double? v) => v is null ? "" : v.Value.ToString("0");
        static string Q(string s) => "\"" + s.Replace("\"", "\"\"") + "\"";

        var sb = new StringBuilder();
        sb.AppendLine("ElementId,标记,族,专业,状态,相对偏移mm,最低管底mm,当前井底标高mm,新井底标高mm,当前井深mm,新井深mm,井深变化mm,匹配管数,匹配管ID,说明");
        foreach (var r in rows)
        {
            bool hasNew = r.LowestPipeMm is not null;
            double? change = hasNew && r.CurDepthMm is { } cd ? r.NewDepthMm - cd : null;
            sb.AppendLine(string.Join(",",
                r.Id, Q(r.Mark), Q(r.Family), r.System, StatusText(r.Status),
                N(r.OffsetMm), N(r.LowestPipeMm), N(r.CurBottomMm), hasNew ? N(r.NewBottomMm) : "",
                N(r.CurDepthMm), hasNew ? N(r.NewDepthMm) : "", N(change),
                r.PipeCount, Q(r.PipeIds), Q(r.Note)));
        }
        File.WriteAllText(path, sb.ToString(), new UTF8Encoding(encoderShouldEmitUTF8Identifier: true));
        return path;
    }
}
