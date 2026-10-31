using System.Text;
using Autodesk.Revit.DB;

namespace KaifaTools.DuctRoute;

/// <summary>
/// Stage 2–4 starting point (read only): clash check for any pipe / duct / cable tray / conduit (and duct banks) with Revit's own solid test.
/// It never changes the model. The adapters that move these members (break, offset, elbows) come next; this tells which ones need it.
/// </summary>
public static class GeneralCheck
{
    static readonly BuiltInCategory[] Mep = { BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_DuctCurves, BuiltInCategory.OST_CableTray, BuiltInCategory.OST_Conduit, BuiltInCategory.OST_FlexPipeCurves, BuiltInCategory.OST_FlexDuctCurves };
    static readonly BuiltInCategory[] Hits =
    {
        BuiltInCategory.OST_PipeCurves, BuiltInCategory.OST_DuctCurves, BuiltInCategory.OST_CableTray, BuiltInCategory.OST_Conduit,
        BuiltInCategory.OST_StructuralFraming, BuiltInCategory.OST_StructuralColumns, BuiltInCategory.OST_StructuralFoundation, BuiltInCategory.OST_Floors, BuiltInCategory.OST_Walls, BuiltInCategory.OST_GenericModel,
        BuiltInCategory.OST_MechanicalEquipment, BuiltInCategory.OST_ElectricalEquipment
    };

    public sealed record Clash(long Id, string Category, string System, List<(long other, string category, string name)> With);

    public static bool IsMember(Element e) => e.Category is not null && Mep.Contains((BuiltInCategory)e.Category.Id.Value);

    public static List<Clash> Run(Document doc, IEnumerable<ElementId> ids, int max = 400, Action<string>? log = null)
    {
        var res = new List<Clash>(); int n = 0;
        var filter = new ElementMulticategoryFilter(Hits);
        foreach (var id in ids)
        {
            var e = doc.GetElement(id); if (e is null || !(IsMember(e) || e.Category?.Id.Value == (long)BuiltInCategory.OST_StructuralFraming)) continue;
            if (++n > max) { log?.Invoke($"只检查前 {max} 个构件"); break; }
            var hit = new FilteredElementCollector(doc).WherePasses(filter).WherePasses(new ElementIntersectsElementFilter(e)).ToElements()
                .Where(o => o.Id != e.Id && !IsSameRun(e, o)).Select(o => (o.Id.Value, o.Category?.Name ?? "", o.Name)).ToList();
            if (hit.Count > 0) res.Add(new Clash(e.Id.Value, e.Category?.Name ?? "", SystemOf(e), hit));
        }
        return res;
    }

    static string SystemOf(Element e) =>
        e.get_Parameter(BuiltInParameter.RBS_SYSTEM_NAME_PARAM)?.AsString() ?? e.get_Parameter(BuiltInParameter.RBS_SYSTEM_CLASSIFICATION_PARAM)?.AsString() ?? (e as FamilyInstance)?.Symbol?.FamilyName ?? "";

    /// <summary>Two pieces of one run touch at their joint: that is a connection, not a clash.</summary>
    static bool IsSameRun(Element a, Element b)
    {
        try
        {
            if (a is MEPCurve ma && b is MEPCurve mb)
                foreach (Connector c in ma.ConnectorManager.Connectors)
                    foreach (Connector d in mb.ConnectorManager.Connectors)
                        if (c.Origin.DistanceTo(d.Origin) < 0.02) return true;
        }
        catch { }
        return false;
    }

    public static string ToCsv(List<Clash> list)
    {
        var sb = new StringBuilder("﻿id,类别,系统/族,碰撞对象数,碰撞对象\r\n");
        foreach (var c in list) sb.AppendLine($"{c.Id},{c.Category},\"{c.System}\",{c.With.Count},\"{string.Join(" ; ", c.With.Take(8).Select(w => $"{w.other}({w.category})"))}\"");
        return sb.ToString();
    }
}
