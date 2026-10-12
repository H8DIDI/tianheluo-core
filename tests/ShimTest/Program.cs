using System.IO;
using System.Reflection;
using IronPython.Hosting;

// Revit 之外运行：让运行时能在 Revit 安装目录里找到依赖程序集
AppDomain.CurrentDomain.AssemblyResolve += (_, a) =>
{
    var f = Path.Combine(@"C:\Program Files\Autodesk\Revit 2026", new System.Reflection.AssemblyName(a.Name).Name + ".dll");
    return File.Exists(f) ? System.Reflection.Assembly.LoadFrom(f) : null;
};
int fail = 0;
void Check(string n, bool ok, string extra = "") { Console.WriteLine((ok ? "PASS " : "FAIL ") + n + (ok ? "" : "  " + extra)); if (!ok) fail++; }

var shimDir = Path.Combine(Path.GetTempPath(), "thl-shim-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(Path.Combine(shimDir, "pyrevit"));
var host = typeof(THL.Host.PyBridge).Assembly;
using (var s = host.GetManifestResourceStream("THL.Host.pyrevit_shim.py")!) using (var r = new StreamReader(s))
    File.WriteAllText(Path.Combine(shimDir, "pyrevit", "__init__.py"), r.ReadToEnd());

var e = Python.CreateEngine();
e.Runtime.LoadAssembly(typeof(Autodesk.Revit.DB.Document).Assembly);
e.Runtime.LoadAssembly(host);
e.SetSearchPaths(new[] { shimDir, @"C:\KAIFA\thl\tests\ShimTest\bin\Release\net10.0-windows\lib" });
string Run(string code) { try { e.Execute(code); return "ok"; } catch (Exception ex) { return ex.Message; } }

Check("导入 pyrevit 兼容层", Run("from pyrevit import revit, DB, UI, forms") == "ok", Run("from pyrevit import revit, DB, UI, forms"));
Check("DB 是 Autodesk.Revit.DB", Run("from pyrevit import DB\nassert DB.XYZ(1,2,3).X == 1") == "ok", Run("from pyrevit import DB\nassert DB.XYZ(1,2,3).X == 1"));
Check("可子类化 TemplateListItem 并自定义 name", Run(@"
from pyrevit import forms
class Opt(forms.TemplateListItem):
    @property
    def name(self): return 'X-' + str(self.item)
o = Opt(5)
assert o.name == 'X-5' and str(o) == 'X-5' and o.item == 5") == "ok");
Check("import pyrevit.forms 形式也可用", Run("import pyrevit.forms as f\nassert hasattr(f,'alert')") == "ok", Run("import pyrevit.forms as f\nassert hasattr(f,'alert')"));
Check("forms 的各个入口都存在", Run(@"
from pyrevit import forms
for n in ('alert','SelectFromList','CommandSwitchWindow','ask_for_string','pick_folder','ProgressBar','TemplateListItem'):
    assert hasattr(forms, n), n") == "ok");
Check("revit.Transaction 与 revit.query 存在", Run("from pyrevit import revit\nassert revit.Transaction and revit.query.get_name") == "ok");
Check("标准库 os/traceback/math 可用", Run("import os, traceback, math, sys") == "ok");

// 10 个原脚本：能否在兼容层下完成“导入阶段”（执行到第一个真正需要 Revit 文档的地方之前）
var root = @"C:\Users\PCB\Documents\revit_chajian\Antigravity.extension\Antigravity.tab\工具箱.panel";
foreach (var f in Directory.GetFiles(root, "script.py", SearchOption.AllDirectories).OrderBy(x => x))
{
    var name = Path.GetFileName(Path.GetDirectoryName(f))!;
    var scope = e.CreateScope();
    string res;
    try { e.CreateScriptSourceFromFile(f, System.Text.Encoding.UTF8).Execute(scope); res = "ok"; }
    catch (Exception ex) { res = ex.Message; }
    // 没有 Revit 文档时，脚本在访问 revit.doc 处失败是预期的；其它错误（导入失败、语法等）才是问题
    bool expected = res == "ok" || res.Contains("NoneType") || res.Contains("PyBridge") || res.Contains("UIApp") || res.Contains("ActiveUIDocument") || res.Contains("Object reference") || res.Contains("未将对象引用");
    Check("脚本可加载：" + name, expected, res);
}
Console.WriteLine(fail == 0 ? "ALL PASS" : fail + " FAILED");
return fail;
