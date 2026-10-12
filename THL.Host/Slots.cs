using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace THL.Host;

// Revit 需要真实的类才能做功能区按钮；这里预置 12 个“槽位”，启动时按已安装插件分配。

[Transaction(TransactionMode.Manual)]
public class Slot01 : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        => Launcher.RunSlot(1, data, ref message, elements);
}

[Transaction(TransactionMode.Manual)]
public class Slot02 : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        => Launcher.RunSlot(2, data, ref message, elements);
}

[Transaction(TransactionMode.Manual)]
public class Slot03 : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        => Launcher.RunSlot(3, data, ref message, elements);
}

[Transaction(TransactionMode.Manual)]
public class Slot04 : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        => Launcher.RunSlot(4, data, ref message, elements);
}

[Transaction(TransactionMode.Manual)]
public class Slot05 : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        => Launcher.RunSlot(5, data, ref message, elements);
}

[Transaction(TransactionMode.Manual)]
public class Slot06 : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        => Launcher.RunSlot(6, data, ref message, elements);
}

[Transaction(TransactionMode.Manual)]
public class Slot07 : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        => Launcher.RunSlot(7, data, ref message, elements);
}

[Transaction(TransactionMode.Manual)]
public class Slot08 : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        => Launcher.RunSlot(8, data, ref message, elements);
}

[Transaction(TransactionMode.Manual)]
public class Slot09 : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        => Launcher.RunSlot(9, data, ref message, elements);
}

[Transaction(TransactionMode.Manual)]
public class Slot10 : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        => Launcher.RunSlot(10, data, ref message, elements);
}

[Transaction(TransactionMode.Manual)]
public class Slot11 : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        => Launcher.RunSlot(11, data, ref message, elements);
}

[Transaction(TransactionMode.Manual)]
public class Slot12 : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        => Launcher.RunSlot(12, data, ref message, elements);
}
