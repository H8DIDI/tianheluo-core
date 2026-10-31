using System.Diagnostics;
using System.Text.Json;

namespace KaifaTools.DuctRoute;

/// <summary>Runs the solver in a separate process (DuctRoute.Worker.exe) and copies its answer back onto the scene.</summary>
public static class SolverRunner
{
    /// <summary>The folder that holds worker\DuctRoute.Worker.exe: the installed plugin folder, or DUCTROUTE_WORKER for tests.</summary>
    public static string? FindWorker()
    {
        var cands = new List<string>();
        var env = Environment.GetEnvironmentVariable("DUCTROUTE_WORKER"); if (!string.IsNullOrEmpty(env)) cands.Add(env);
        cands.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo", "plugins", "duct-route", "worker"));
        return cands.Select(d => Path.Combine(d, "DuctRoute.Worker.exe")).FirstOrDefault(File.Exists);
    }

    public static void Run(Scene scene, RouteRules rules, Action<string> progress, Action<string> log, CancellationToken ct, string mode = "solve")
    {
        var exe = FindWorker() ?? throw new FileNotFoundException("找不到求解组件 worker\\DuctRoute.Worker.exe，请重新安装插件。");
        string tmp = Path.Combine(Path.GetTempPath(), "duct-route-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(tmp);
        string sc = Path.Combine(tmp, "scene.json"), ru = Path.Combine(tmp, "rules.json"), rs = Path.Combine(tmp, "result.json");
        try
        {
            File.WriteAllText(sc, JsonSerializer.Serialize(scene, new JsonSerializerOptions { IncludeFields = true }));
            File.WriteAllText(ru, JsonSerializer.Serialize(rules));
            var psi = new ProcessStartInfo(exe) { UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = System.Text.Encoding.UTF8, WorkingDirectory = Path.GetDirectoryName(exe)! };
            psi.ArgumentList.Add(sc); psi.ArgumentList.Add(ru); psi.ArgumentList.Add(rs); psi.ArgumentList.Add(mode);
            using var p = Process.Start(psi)!; string? err = null;
            p.OutputDataReceived += (_, e) =>
            {
                if (e.Data is null) return; var t = e.Data;
                if (t.StartsWith("P|")) progress(t[2..]); else if (t.StartsWith("L|")) log(t[2..]); else if (t.StartsWith("E|")) err = t[2..];
            };
            p.BeginOutputReadLine(); p.ErrorDataReceived += (_, _) => { }; p.BeginErrorReadLine();
            using (ct.Register(() => { try { p.Kill(true); } catch { } }))
                p.WaitForExit();
            ct.ThrowIfCancellationRequested();
            if (p.ExitCode != 0 || !File.Exists(rs)) throw new InvalidOperationException("求解进程失败（代码 " + p.ExitCode + "）：" + (err ?? "无输出"));
            using var doc = JsonDocument.Parse(File.ReadAllText(rs));
            foreach (var prop in doc.RootElement.EnumerateObject())
            {
                if (!scene.ById.TryGetValue(long.Parse(prop.Name), out var b)) continue;
                b.Z0 = prop.Value.GetProperty("z0").GetDouble(); b.Z1 = prop.Value.GetProperty("z1").GetDouble();
                b.Frozen = prop.Value.GetProperty("frozen").GetBoolean(); b.Status = prop.Value.GetProperty("status").GetString() ?? "";
                if (prop.Value.TryGetProperty("issues", out var iss) && iss.ValueKind == JsonValueKind.Array)
                    b.Detail = string.Join("；", iss.EnumerateArray().Select(i => $"{i.GetProperty("kind").GetString()} {i.GetProperty("other").GetInt64()}（净距 {i.GetProperty("dist").GetDouble():0.00} m）"));
            }
        }
        finally { try { Directory.Delete(tmp, true); } catch { } }
    }
}
