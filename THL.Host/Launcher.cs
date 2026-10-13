using System.Reflection;
using System.Runtime.Loader;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace THL.Host;

/// <summary>
/// 热加载：每次点击都从磁盘读取插件 DLL 到内存、放进一个可回收的 AssemblyLoadContext，运行后卸载。
/// 所以替换/更新插件文件后，不需要重启 Revit。
/// </summary>
internal static class HotLoader
{
    public static Result Run(Installed p, ExternalCommandData data, ref string message, ElementSet elements)
    {
        if (p.Manifest.HostApi > Store.HostApiVersion) { message = $"「{p.Manifest.Name}」需要更新的宿主（插件接口 v{p.Manifest.HostApi}）。请在插件中心更新宿主。"; return Result.Failed; }
        if (p.Manifest.IsPython) return PyRunner.Run(p, data, ref message);
        if (!File.Exists(p.DllPath)) { message = "找不到 " + p.DllPath; return Result.Failed; }

        var dir = p.Dir;
        var ctx = new AssemblyLoadContext("THL-" + p.Manifest.Id + "-" + Guid.NewGuid().ToString("N"), isCollectible: true);
        // 插件目录里的依赖 DLL 也从内存加载，避免文件被占用
        ctx.Resolving += (c, name) =>
        {
            var f = Path.Combine(dir, name.Name + ".dll");
            return File.Exists(f) ? c.LoadFromStream(new MemoryStream(File.ReadAllBytes(f))) : null;
        };
        try
        {
            using var dll = new MemoryStream(File.ReadAllBytes(p.DllPath));
            var pdb = Path.ChangeExtension(p.DllPath, ".pdb");
            Assembly asm = File.Exists(pdb)
                ? ctx.LoadFromStream(dll, new MemoryStream(File.ReadAllBytes(pdb)))
                : ctx.LoadFromStream(dll);

            var type = asm.GetType(p.Manifest.Entry, throwOnError: true)!;
            if (Activator.CreateInstance(type) is not IExternalCommand cmd)
            { message = p.Manifest.Entry + " 没有实现 IExternalCommand"; return Result.Failed; }
            return cmd.Execute(data, ref message, elements);
        }
        catch (Exception ex)
        {
            var inner = ex is TargetInvocationException { InnerException: not null } t ? t.InnerException! : ex;
            TaskDialog.Show("天河落 · 插件出错：" + p.Manifest.Name, inner.ToString());
            message = inner.Message;
            return Result.Failed;
        }
        finally { ctx.Unload(); }
    }
}

public static class Launcher
{
    /// <summary>功能区槽位按钮 → 第 n 个已启用的插件。</summary>
    public static Result RunSlot(int slot, ExternalCommandData data, ref string message, ElementSet elements)
    {
        var id = Store.PluginInSlot(slot);
        var plugin = id == null ? null : Store.List().FirstOrDefault(p => p.Manifest.Id == id && p.Enabled);
        if (plugin == null)
        {
            TaskDialog.Show("天河落", "这个按钮对应的插件已被卸载或停用。请打开「插件中心」。");
            App.SyncRibbon();
            return Result.Cancelled;
        }
        return HotLoader.Run(plugin, data, ref message, elements);
    }
}

/// <summary>「插件中心」按钮：打开启动器窗口，选中后运行。</summary>
[Transaction(TransactionMode.Manual)]
public class OpenLauncher : IExternalCommand
{
    public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
    {
        var win = new LauncherWindow();
        PyBridge.AttachToRevit(win);
        win.ShowDialog();
        App.SyncRibbon(); // 装/卸/停用后，功能区立刻跟上，不用重启
        if (win.RunTarget is { } p) return HotLoader.Run(p, data, ref message, elements);
        return Result.Succeeded;
    }
}
