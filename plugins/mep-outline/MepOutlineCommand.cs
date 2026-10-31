using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctRoute;

namespace KaifaTools.Commands;

/// <summary>管线沿障碍外沿绕行: one job, no window. The work is in MepTools.Outline.</summary>
[Transaction(TransactionMode.Manual)]
public class MepOutlineCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => MepTools.Outline(commandData.Application);
}
