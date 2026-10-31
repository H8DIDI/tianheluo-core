using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctRoute;

namespace KaifaTools.Commands;

/// <summary>管线并线绕行: one job, no window. The work is in MepTools.Bundle.</summary>
[Transaction(TransactionMode.Manual)]
public class MepBundleCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => MepTools.Bundle(commandData.Application);
}
