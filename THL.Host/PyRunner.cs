using System.Reflection;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using IronPython.Hosting;
using Microsoft.Scripting.Hosting;

namespace THL.Host;

/// <summary>
/// 运行原样的 pyRevit 脚本（script.py），不需要用户安装 pyRevit。
/// 宿主自带 IronPython，并提供一个很薄的 `pyrevit` 兼容模块（revit / forms / DB / UI）。
/// </summary>
internal static class PyRunner
{
    /// <summary>当前正在运行的命令上下文，供 Python 兼容层读取。</summary>
    public static UIApplication? UIApp { get; private set; }

    static string ShimDir => Path.Combine(Store.Root, "pylib");

    public static Result Run(Installed p, ExternalCommandData data, ref string message)
    {
        var script = Path.Combine(p.Dir, p.Manifest.Script);
        if (!File.Exists(script)) { message = "找不到脚本 " + script; return Result.Failed; }

        UIApp = data.Application;
        WriteShim();

        ScriptEngine? engine = null;
        try
        {
            engine = Python.CreateEngine();
            var paths = engine.GetSearchPaths().ToList();
            var hostDir = Path.GetDirectoryName(typeof(PyRunner).Assembly.Location)!;
            paths.Add(Path.Combine(hostDir, "lib")); // IronPython 标准库（随宿主一起部署）
            paths.Add(ShimDir);
            paths.Add(p.Dir);
            engine.SetSearchPaths(paths);
            // 把已加载的 Revit API / 宿主程序集直接登记给 IronPython，clr.AddReference 才能按名字找到
            engine.Runtime.LoadAssembly(typeof(Autodesk.Revit.DB.Document).Assembly);
            engine.Runtime.LoadAssembly(typeof(Autodesk.Revit.UI.UIApplication).Assembly);
            engine.Runtime.LoadAssembly(typeof(PyRunner).Assembly);
            // pyRevit 自带运行环境会预先引用这些 .NET 组件；这里同样登记，否则
            // `from System.ComponentModel import INotifyPropertyChanged` 之类的导入会失败
            foreach (var t in new[]
            {
                typeof(System.ComponentModel.INotifyPropertyChanged),
                typeof(System.Collections.ObjectModel.ObservableCollection<object>),
                typeof(System.Data.DataTable), typeof(System.Xml.XmlDocument),
                typeof(System.Linq.Enumerable), typeof(System.Diagnostics.Process),
                typeof(System.Windows.Markup.XamlReader), typeof(System.Windows.Window),
                typeof(System.Text.RegularExpressions.Regex), typeof(System.Threading.Tasks.Task),
            })
                engine.Runtime.LoadAssembly(t.Assembly);

            var stream = new MemoryStream();
            engine.Runtime.IO.SetOutput(stream, System.Text.Encoding.UTF8);
            engine.Runtime.IO.SetErrorOutput(stream, System.Text.Encoding.UTF8);

            var scope = engine.CreateScope();
            scope.SetVariable("__revit__", data.Application);
            scope.SetVariable("__file__", script);

            // 关键：必须在编译选项里把模块名设成 __main__，脚本末尾的
            // `if __name__ == '__main__': main()` 才会执行（仅 SetVariable 无效，会得到 "<module>"）。
            var opts = (IronPython.Compiler.PythonCompilerOptions)engine.GetCompilerOptions(scope);
            opts.ModuleName = "__main__";
            try { engine.CreateScriptSourceFromFile(script, System.Text.Encoding.UTF8).Compile(opts).Execute(scope); }
            catch (Exception ex) when (IsExit(ex)) { /* sys.exit() 视为正常结束 */ }

            SaveLog(p, stream);
            return Result.Succeeded;
        }
        catch (Exception ex)
        {
            string detail;
            try { detail = engine?.GetService<ExceptionOperations>().FormatException(ex) ?? ex.ToString(); }
            catch { detail = ex.ToString(); }
            TaskDialog.Show("天河落 · 脚本出错：" + p.Manifest.Name, detail);
            message = ex.Message;
            return Result.Failed;
        }
        finally { UIApp = null; }
    }

    static bool IsExit(Exception ex) => ex.GetType().Name == "SystemExitException";

    static void SaveLog(Installed p, MemoryStream s)
    {
        try
        {
            if (s.Length == 0) return;
            var dir = Path.Combine(Store.Root, "logs");
            Directory.CreateDirectory(dir);
            File.WriteAllBytes(Path.Combine(dir, p.Manifest.Id + ".log"), s.ToArray());
        }
        catch { }
    }

    /// <summary>把内置的 pyrevit 兼容模块写到用户目录（内容变了才覆盖）。</summary>
    static void WriteShim()
    {
        var dir = Path.Combine(ShimDir, "pyrevit");
        Directory.CreateDirectory(dir);
        var file = Path.Combine(dir, "__init__.py");
        using var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("THL.Host.pyrevit_shim.py")
            ?? throw new InvalidOperationException("缺少内置 pyrevit 兼容模块");
        using var r = new StreamReader(s, System.Text.Encoding.UTF8);
        var text = r.ReadToEnd();
        if (!File.Exists(file) || File.ReadAllText(file) != text) File.WriteAllText(file, text, new System.Text.UTF8Encoding(false));
    }
}
