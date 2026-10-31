using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctRoute;

namespace KaifaTools.Commands;

/// <summary>管线打断: one job, no window. The work is in MepTools.Break.</summary>
[Transaction(TransactionMode.Manual)]
public class MepBreakCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => MepTools.Break(commandData.Application);
}
