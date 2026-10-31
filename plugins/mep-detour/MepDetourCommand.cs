using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctRoute;

namespace KaifaTools.Commands;

/// <summary>管线平面绕行: one job, no window. The work is in MepTools.Detour.</summary>
[Transaction(TransactionMode.Manual)]
public class MepDetourCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => MepTools.Detour(commandData.Application);
}
