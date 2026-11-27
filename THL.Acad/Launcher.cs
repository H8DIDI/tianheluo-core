using System.Reflection;
#if !NETFRAMEWORK
using System.Runtime.Loader;
#endif
using Autodesk.AutoCAD.ApplicationServices;
using AcApp = Autodesk.AutoCAD.ApplicationServices.Application;

namespace THL.Host;

/// <summary>
/// 运行插件（热加载）：每次都从磁盘读 DLL 字节到内存加载，所以更新插件后不用重启 AutoCAD。
///   net8（2025+）：放进可回收的 AssemblyLoadContext，运行完卸载；
///   net48（2018~2024）：Assembly.Load(bytes)，按内容缓存（内容没变复用，变了加载新副本）。
///
/// 插件约定（不用引用宿主 DLL）：plugin.json 的 entry 指向一个 public 类，里面有其中一个方法：
///   public string Execute(Document doc, IDictionary&lt;string,string&gt; args)   // 推荐
///   public string Execute(Document doc)
/// doc 可能为 null（开始页没有打开图纸时）；args 来自 THLRUN id?key=value;key=value（批处理/自动测试用），平时为空。
/// 返回的文字写到命令行。
/// </summary>
public static class Launcher
{
#if NETFRAMEWORK
    static readonly Dictionary<string, Assembly> Loaded = new(StringComparer.OrdinalIgnoreCase);

    static Assembly LoadBytes(string path)
    {
        var bytes = File.ReadAllBytes(path);
        var key = path + "|" + NetCompat.ToHex(NetCompat.Sha256(bytes));
        lock (Loaded)
        {
            if (Loaded.TryGetValue(key, out var a)) return a;
            var pdb = Path.ChangeExtension(path, ".pdb");
            a = File.Exists(pdb) ? Assembly.Load(bytes, File.ReadAllBytes(pdb)) : Assembly.Load(bytes);
            Loaded[key] = a;
            return a;
        }
    }
#endif

    public static Dictionary<string, string> ParseArgs(string s)
    {
        var d = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var part in s.Split(';'))
        {
            var k = part.IndexOf('=');
            if (k > 0) d[part.Substring(0, k).Trim()] = part.Substring(k + 1).Trim();
            else if (part.Trim().Length > 0) d[part.Trim()] = "1";
        }
        return d;
    }

    public static void Run(Document? doc, string idAndArgs)
    {
        var q = idAndArgs.IndexOf('?');
        var id = (q < 0 ? idAndArgs : idAndArgs.Substring(0, q)).Trim();
        var args = q < 0 ? new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase) : ParseArgs(idAndArgs.Substring(q + 1));
        var ed = doc?.Editor;
        void Say(string m) { try { ed?.WriteMessage("\n" + m + "\n"); } catch { } }

        var p = Store.List().FirstOrDefault(x => x.Manifest.Id == id && x.Enabled);
        if (p == null) { Say($"天河落：没有已启用的插件「{id}」。命令 THL 打开插件中心。"); App.SyncRibbon(); return; }
        if (p.Manifest.HostApi > Store.HostApiVersion) { Say($"「{p.Manifest.Name}」需要更新的宿主（插件接口 v{p.Manifest.HostApi}）。请在插件中心更新宿主。"); return; }
        if (!File.Exists(p.DllPath)) { Say("找不到 " + p.DllPath); return; }

        var dir = Path.GetDirectoryName(p.DllPath)!;
        App.Log($"运行 {p.Manifest.Id} v{p.Manifest.Version}" + (args.Count > 0 ? "  参数 " + string.Join(";", args.Select(kv => kv.Key + "=" + kv.Value)) : ""));
#if NETFRAMEWORK
        ResolveEventHandler resolver = (_, a) =>
        {
            var f = Path.Combine(dir, new AssemblyName(a.Name).Name + ".dll");
            return File.Exists(f) ? LoadBytes(f) : null;
        };
        AppDomain.CurrentDomain.AssemblyResolve += resolver;
        try
        {
            var asm = LoadBytes(p.DllPath);
#else
        var ctx = new AssemblyLoadContext("THL-" + p.Manifest.Id + "-" + Guid.NewGuid().ToString("N"), isCollectible: true);
        ctx.Resolving += (c, name) =>
        {
            var f = Path.Combine(dir, name.Name + ".dll");
            return File.Exists(f) ? c.LoadFromStream(new MemoryStream(File.ReadAllBytes(f))) : null;
        };
        try
        {
            Assembly asm;
            using (var dll = new MemoryStream(File.ReadAllBytes(p.DllPath)))
            {
                var pdb = Path.ChangeExtension(p.DllPath, ".pdb");
                asm = File.Exists(pdb) ? ctx.LoadFromStream(dll, new MemoryStream(File.ReadAllBytes(pdb))) : ctx.LoadFromStream(dll);
            }
#endif
            var type = asm.GetType(p.Manifest.Entry, throwOnError: true)!;
            var obj = Activator.CreateInstance(type);
            var m2 = type.GetMethod("Execute", new[] { typeof(Document), typeof(IDictionary<string, string>) });
            var m1 = type.GetMethod("Execute", new[] { typeof(Document) });
            object? ret;
            if (m2 != null) ret = m2.Invoke(obj, new object?[] { doc, args });
            else if (m1 != null) ret = m1.Invoke(obj, new object?[] { doc });
            else { Say(p.Manifest.Entry + " 没有 Execute(Document[, IDictionary<string,string>]) 方法"); return; }
            if (ret is string s && s.Length > 0) { Say(s); App.Log($"{p.Manifest.Id}：{s}"); }
        }
        catch (System.Exception ex)
        {
            var inner = ex is TargetInvocationException { InnerException: not null } t ? t.InnerException! : ex;
            App.Log($"插件出错 {p.Manifest.Id}\n{inner}");
            Say($"天河落 · 插件出错：{p.Manifest.Name}\n{inner.Message}");
            if (args.Count == 0)
                try { System.Windows.MessageBox.Show(inner.ToString(), "天河落 · 插件出错：" + p.Manifest.Name); } catch { }
        }
#if NETFRAMEWORK
        finally { AppDomain.CurrentDomain.AssemblyResolve -= resolver; }
#else
        finally { ctx.Unload(); }
#endif
    }
}
