using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using KaifaTools.DuctRoute;

namespace KaifaTools.Commands;

/// <summary>排管过障翻弯: one job, no window. The work is in BankTools.Hop.</summary>
[Transaction(TransactionMode.Manual)]
public class BankHopCommand : IExternalCommand
{
    public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) => BankTools.Hop(commandData.Application);
}
