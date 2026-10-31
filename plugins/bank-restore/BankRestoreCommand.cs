using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctRoute;

namespace KaifaTools.Commands;

/// <summary>排管还原: one job, no window. The work is in BankTools.Restore.</summary>
[Transaction(TransactionMode.Manual)]
public class BankRestoreCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => BankTools.Restore(commandData.Application);
}
