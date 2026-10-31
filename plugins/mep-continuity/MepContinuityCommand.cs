using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctRoute;

namespace KaifaTools.Commands;

/// <summary>管线连续性检查: one job, no window. The work is in MepTools.Continuity.</summary>
[Transaction(TransactionMode.Manual)]
public class MepContinuityCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => MepTools.Continuity(commandData.Application);
}
