using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctRoute;

namespace KaifaTools.Commands;

/// <summary>管线碰撞检查: one job, no window. The work is in MepTools.Clash.</summary>
[Transaction(TransactionMode.Manual)]
public class MepClashCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => MepTools.Clash(commandData.Application);
}
