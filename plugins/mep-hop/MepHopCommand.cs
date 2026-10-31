using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctRoute;

namespace KaifaTools.Commands;

/// <summary>管线过障翻弯: one job, no window. The work is in MepTools.Hop.</summary>
[Transaction(TransactionMode.Manual)]
public class MepHopCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => MepTools.Hop(commandData.Application);
}
