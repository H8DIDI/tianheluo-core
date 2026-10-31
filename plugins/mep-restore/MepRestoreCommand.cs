using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctRoute;

namespace KaifaTools.Commands;

/// <summary>管线还原: one job, no window. The work is in MepTools.Restore.</summary>
[Transaction(TransactionMode.Manual)]
public class MepRestoreCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => MepTools.Restore(commandData.Application);
}
