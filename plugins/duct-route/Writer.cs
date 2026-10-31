using System.Text.Json;
using System.Text.RegularExpressions;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace KaifaTools.DuctRoute;

public sealed record WriteResult(int Written, int Same, List<string> Failed, string BackupFile, double MaxDiffMm, Dictionary<long, List<long>> BlockedByDims);

/// <summary>Writes the solved levels back as end-offset changes (plan positions untouched). Backs up first; one undoable group.</summary>
public static class Writer
{
    const double Ft = 0.3048;
    static readonly BuiltInParameter P0 = BuiltInParameter.STRUCTURAL_BEAM_END0_ELEVATION, P1 = BuiltInParameter.STRUCTURAL_BEAM_END1_ELEVATION;

    internal sealed class DropWarnings : IFailuresPreprocessor
    {
        public readonly List<string> Errors = new();
        public readonly List<long> FailingIds = new();
        public FailureProcessingResult PreprocessFailures(FailuresAccessor fa)
        {
            foreach (var f in fa.GetFailureMessages())
            {
                if (f.GetSeverity() == FailureSeverity.Warning) fa.DeleteWarning(f);
                else { Errors.Add(f.GetDescriptionText()); foreach (var id in f.GetFailingElementIds()) FailingIds.Add(id.Value); }
            }
            // An error Revit cannot ignore would open a dialog and freeze the session: roll that one element back quietly instead.
            return Errors.Count > 0 ? FailureProcessingResult.ProceedWithRollBack : FailureProcessingResult.Continue;
        }
    }

    /// <summary>Element id -> ids of the dimensions that reference it. A bank with a dimension on it can raise a Revit error dialog
    /// ("尺寸标注的参照不再平行") that freezes the session, so such banks are never written silently.</summary>
    public static Dictionary<long, List<long>> DimensionMap(Document doc, ISet<long> wanted)
    {
        var map = new Dictionary<long, List<long>>();
        foreach (var d in new FilteredElementCollector(doc).OfClass(typeof(Dimension)).WhereElementIsNotElementType().Cast<Dimension>())
        {
            try
            {
                foreach (Reference r in d.References)
                {
                    long id = r.ElementId.Value;
                    if (!wanted.Contains(id)) continue;
                    if (!map.TryGetValue(id, out var l)) map[id] = l = new List<long>();
                    l.Add(d.Id.Value);
                }
            }
            catch { }
        }
        return map;
    }

    /// <summary>While writing, answer any dialog Revit tries to show with Cancel instead of waiting for a person.</summary>
    internal sealed class DialogGuard : IDisposable
    {
        readonly UIApplication? _app; public readonly List<string> Seen = new(); public readonly List<string> Errors = new();
        public DialogGuard(UIApplication? app)
        {
            _app = app;
            if (_app is null) return;
            _app.DialogBoxShowing += OnDialog;
            _app.Application.FailuresProcessing += OnFailures;   // answers every failure of every transaction, so no error dialog can open
        }
        void OnFailures(object? sender, Autodesk.Revit.DB.Events.FailuresProcessingEventArgs e)
        {
            var fa = e.GetFailuresAccessor(); bool err = false;
            foreach (var f in fa.GetFailureMessages())
            {
                if (f.GetSeverity() == FailureSeverity.Warning) fa.DeleteWarning(f); else { err = true; Errors.Add(f.GetDescriptionText()); }
            }
            if (err) e.SetProcessingResult(FailureProcessingResult.ProceedWithRollBack);
        }
        void OnDialog(object? sender, Autodesk.Revit.UI.Events.DialogBoxShowingEventArgs e) { Seen.Add(e.DialogId); try { e.OverrideResult(2); } catch { } }
        public void Dispose() { if (_app is not null) { _app.DialogBoxShowing -= OnDialog; _app.Application.FailuresProcessing -= OnFailures; } }
    }

    public static string BackupDir => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools", "duct-route-backup");

    public static WriteResult Apply(Document doc, IEnumerable<Bank> banks, UIApplication? app = null)
    {
        using var guard = new DialogGuard(app);
        var list = banks.Where(b => b.Changed).ToList(); var failed = new List<string>(); var blocked = new Dictionary<long, List<long>>();
        Directory.CreateDirectory(BackupDir);
        string file = Path.Combine(BackupDir, $"{DateTime.Now:yyyyMMdd-HHmmss}.json");
        var bk = new List<object>();
        foreach (var b in list)
        {
            var e = doc.GetElement(new ElementId(b.Id)); if (e is null) continue;
            bk.Add(new { id = b.Id, end0 = e.get_Parameter(P0).AsDouble(), end1 = e.get_Parameter(P1).AsDouble() });
        }
        File.WriteAllText(file, JsonSerializer.Serialize(new { doc = doc.Title, time = DateTime.Now, items = bk }));
        var dimMap = DimensionMap(doc, list.Select(b => b.Id).ToHashSet());
        int written = 0, same = 0; double maxDiff = 0;
        using var tg = new TransactionGroup(doc, "排管竖向避让");
        tg.Start();
        foreach (var b in list)
        {
            var e = doc.GetElement(new ElementId(b.Id)); if (e is null) { failed.Add($"{b.Id}：找不到"); continue; }
            var p0 = e.get_Parameter(P0); var p1 = e.get_Parameter(P1);
            double n0 = p0.AsDouble() + (b.Z0 - b.O0) / Ft, n1 = p1.AsDouble() + (b.Z1 - b.O1) / Ft;
            if (Math.Abs(n0 - p0.AsDouble()) < 1e-6 && Math.Abs(n1 - p1.AsDouble()) < 1e-6) { same++; continue; }
            var pre = new DropWarnings();
            using var t = new Transaction(doc, "排管标高");
            try
            {
                t.Start();
                var fo = t.GetFailureHandlingOptions(); fo.SetFailuresPreprocessor(pre); t.SetFailureHandlingOptions(fo);
                p0.Set(n0); p1.Set(n1);
                if (t.Commit() == TransactionStatus.Committed)
                {
                    written++;
                    double d = Math.Max(Math.Abs(p0.AsDouble() - n0), Math.Abs(p1.AsDouble() - n1)) * Ft * 1000; maxDiff = Math.Max(maxDiff, d);
                }
                else { failed.Add($"{b.Id}：被回滚" + Hint(pre)); Block(doc, b.Id, pre, blocked, dimMap); }
            }
            catch (Exception ex) { try { t.RollBack(); } catch { } failed.Add($"{b.Id}：{ex.Message}" + Hint(pre)); Block(doc, b.Id, pre, blocked, dimMap); }
        }
        tg.Assimilate();
        return new WriteResult(written, same, failed, file, maxDiff, blocked);
    }

    static void Block(Document doc, long bankId, DropWarnings pre, Dictionary<long, List<long>> blocked, Dictionary<long, List<long>> dimMap)
    {
        var dims = pre.FailingIds.Where(i => doc.GetElement(new ElementId(i)) is Dimension).Distinct().ToList();
        if (dims.Count == 0 && dimMap.TryGetValue(bankId, out var all)) dims = all;
        if (dims.Count > 0) blocked[bankId] = dims;
    }

    static string Hint(DropWarnings pre)
    {
        if (pre.Errors.Count == 0) return "（原因不明，多半是被尺寸标注引用了）";
        var s = pre.Errors[0];
        if (s.Contains("过期")) return "（文件已过期：先点“协作 → 重新载入最新工作集”）";
        if (s.Contains("尺寸标注")) return "（被尺寸标注挡住，可用“删标注并重写”）";
        return "（" + s + "）";
    }

    public static string? LatestBackup(Document doc)
    {
        if (!Directory.Exists(BackupDir)) return null;
        return Directory.GetFiles(BackupDir, "*.json").Where(f => Regex.IsMatch(Path.GetFileName(f), @"^\d{8}-\d{6}\.json$")).OrderByDescending(f => f).FirstOrDefault(f =>
        {
            try { using var d = JsonDocument.Parse(File.ReadAllText(f)); return d.RootElement.GetProperty("doc").GetString() == doc.Title; } catch { return false; }
        });
    }

    public static (int restored, List<string> failed) Restore(Document doc, string file, UIApplication? app = null)
    {
        using var guard = new DialogGuard(app);
        var failed = new List<string>(); int ok = 0;
        using var d = JsonDocument.Parse(File.ReadAllText(file));
        var dimMap = DimensionMap(doc, d.RootElement.GetProperty("items").EnumerateArray().Select(i => i.GetProperty("id").GetInt64()).ToHashSet());
        using var tg = new TransactionGroup(doc, "排管竖向避让·还原"); tg.Start();
        foreach (var it in d.RootElement.GetProperty("items").EnumerateArray())
        {
            long id = it.GetProperty("id").GetInt64(); var e = doc.GetElement(new ElementId(id)); if (e is null) { failed.Add($"{id}：找不到"); continue; }
            if (dimMap.ContainsKey(id)) { var cur0 = e.get_Parameter(P0).AsDouble(); var cur1 = e.get_Parameter(P1).AsDouble(); if (Math.Abs(cur0 - it.GetProperty("end0").GetDouble()) < 1e-6 && Math.Abs(cur1 - it.GetProperty("end1").GetDouble()) < 1e-6) { ok++; continue; } failed.Add($"{id}：有尺寸标注引用它，没有还原"); continue; }
            var pre = new DropWarnings(); using var t = new Transaction(doc, "还原");
            try
            {
                t.Start(); var fo = t.GetFailureHandlingOptions(); fo.SetFailuresPreprocessor(pre); t.SetFailureHandlingOptions(fo);
                e.get_Parameter(P0).Set(it.GetProperty("end0").GetDouble()); e.get_Parameter(P1).Set(it.GetProperty("end1").GetDouble());
                if (t.Commit() == TransactionStatus.Committed) ok++; else failed.Add($"{id}：被回滚" + Hint(pre));
            }
            catch (Exception ex) { try { t.RollBack(); } catch { } failed.Add($"{id}：{ex.Message}"); }
        }
        tg.Assimilate();
        return (ok, failed);
    }
}

public static class DimTools
{
    /// <summary>Deletes the given dimensions after saving id / view / text / references to a JSON file next to the level backups.</summary>
    public static (int deleted, string file) DeleteWithBackup(Document doc, IEnumerable<long> dimIds)
    {
        Directory.CreateDirectory(Writer.BackupDir);
        string file = Path.Combine(Writer.BackupDir, $"dims-{DateTime.Now:yyyyMMdd-HHmmss}.json"); var rec = new List<object>(); var ids = dimIds.Distinct().ToList();
        foreach (var id in ids)
            if (doc.GetElement(new ElementId(id)) is Dimension d)
            {
                var v = doc.GetElement(d.OwnerViewId);
                // some dimensions (spot dimensions, multi-segment ones) throw on Origin / ValueString: keep what can be read
                string? text = null; long[]? refs = null; double[]? origin = null;
                try { text = d.ValueString; } catch { }
                try { refs = d.References.Cast<Reference>().Select(r => r.ElementId.Value).ToArray(); } catch { }
                try { origin = new[] { d.Origin.X, d.Origin.Y, d.Origin.Z }; } catch { }
                rec.Add(new { id, view = v?.Name, type = d.DimensionType?.Name, text, refs, origin });
            }
        File.WriteAllText(file, JsonSerializer.Serialize(rec));
        int n = 0;
        using var tg = new TransactionGroup(doc, "删除挡路的尺寸标注"); tg.Start();
        foreach (var id in ids)
        {
            using var t = new Transaction(doc, "删标注");
            try { t.Start(); doc.Delete(new ElementId(id)); if (t.Commit() == TransactionStatus.Committed) n++; } catch { try { t.RollBack(); } catch { } }
        }
        tg.Assimilate();
        return (n, file);
    }
}
