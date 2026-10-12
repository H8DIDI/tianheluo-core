using IronPython.Hosting;
var engine = Python.CreateEngine();
var root = @"C:\Users\PCB\AppData\Roaming\pyRevit\Extensions\Sendit.extension";
int bad = 0;
var files = Directory.GetFiles(Path.Combine(root, "lib", "sendit"), "*.py").Concat(Directory.GetFiles(Path.Combine(root, "Sendit.tab"), "script.py", SearchOption.AllDirectories));
foreach (var f in files)
{
    try { engine.CreateScriptSourceFromFile(f, System.Text.Encoding.UTF8).Compile(); Console.WriteLine("OK   " + Path.GetFileName(Path.GetDirectoryName(f)) + "/" + Path.GetFileName(f)); }
    catch (Exception e) { bad++; Console.WriteLine("FAIL " + f + " :: " + e.Message.Split('\n')[0]); }
}
Console.WriteLine(bad == 0 ? "ALL COMPILE" : bad + " failed");
