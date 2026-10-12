using IronPython.Hosting;
var engine = Python.CreateEngine();
foreach (var d in new[] { "hello-world", "count-by-category", "set-comments" })
{
    var f = Path.Combine(@"C:\KAIFA\tianheluo-web\examples", d, "script.py");
    try { engine.CreateScriptSourceFromFile(f, System.Text.Encoding.UTF8).Compile(); Console.WriteLine("IronPython 3.4 编译通过: " + d); }
    catch (Exception e) { Console.WriteLine("FAIL " + d + " :: " + e.Message.Split('\n')[0]); }
}
