using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctRoute;

namespace KaifaTools.Commands;

/// <summary>管线多根协调绕行: one job, no window. The work is in MepTools.Corridor.</summary>
[Transaction(TransactionMode.Manual)]
public class MepCorridorCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => MepTools.Corridor(commandData.Application);
}
