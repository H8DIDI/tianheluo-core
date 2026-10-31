using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctRoute;

namespace KaifaTools.Commands;

/// <summary>排管平面绕行: one job, no window. The work is in BankTools.Detour.</summary>
[Transaction(TransactionMode.Manual)]
public class BankDetourCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => BankTools.Detour(commandData.Application);
}
