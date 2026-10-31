using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctRoute;

namespace KaifaTools.Commands;

/// <summary>排管打断: one job, no window. The work is in BankTools.Break.</summary>
[Transaction(TransactionMode.Manual)]
public class BankBreakCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => BankTools.Break(commandData.Application);
}
