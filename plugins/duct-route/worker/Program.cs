using System.Text.Json;

namespace KaifaTools.DuctRoute;

/// <summary>
/// Separate solver process. Usage: DuctRoute.Worker scene.json rules.json result.json
/// Progress is written to stdout as lines "P|message". Running the native OR-Tools code outside Revit keeps Revit safe.
/// </summary>
internal static class Program
{
    static int Main(string[] args)
    {
        if (args.Length < 3) { Console.Error.WriteLine("用法：DuctRoute.Worker scene.json rules.json result.json"); return 2; }
        Console.OutputEncoding = System.Text.Encoding.UTF8;
        var opt = new JsonSerializerOptions { IncludeFields = true };
        try
        {
            var scene = JsonSerializer.Deserialize<Scene>(File.ReadAllText(args[0]), opt)!; scene.Rebuild();
            var rules = JsonSerializer.Deserialize<RouteRules>(File.ReadAllText(args[1]))!;
            var solver = new RouteSolver(scene, rules, m => Console.WriteLine("L|" + m));
            string mode = args.Length > 3 ? args[3] : "solve";
            Action<string> prog = m => { Console.WriteLine("P|" + m); Console.Out.Flush(); };
            if (mode == "check")
            {
                foreach (var b in scene.Movable) { b.Z0 = b.O0; b.Z1 = b.O1; }       // the scene's O values are the current levels
                var issues = solver.Verify();
                var chk = scene.Movable.ToDictionary(b => b.Id, b => new { z0 = b.Z0, z1 = b.Z1, frozen = false, status = issues.ContainsKey(b.Id) ? "有碰撞" : (solver.CoverExcess(b) > 0.005 ? "覆土不足" : "无问题"),
                    issues = issues.TryGetValue(b.Id, out var l) ? l.Select(i => new { kind = i.kind, other = i.other, dist = Math.Round(i.dist, 3) }).ToArray() : Array.Empty<object>() });
                File.WriteAllText(args[2], JsonSerializer.Serialize(chk));
                Console.WriteLine("D|done"); return 0;
            }
            if (mode == "round" || mode == "align")
            {
                foreach (var b in scene.Movable) { b.Z0 = b.O0; b.Z1 = b.O1; }
                solver.Normalize(mode == "round", prog);
                foreach (var b in scene.Movable) b.Status = b.Changed ? (mode == "round" ? "已取整" : "已对齐") : "无需改";
            }
            else solver.Run(prog, CancellationToken.None);
            var res = scene.Movable.ToDictionary(b => b.Id, b => new { z0 = b.Z0, z1 = b.Z1, frozen = b.Frozen, status = b.Status });
            File.WriteAllText(args[2], JsonSerializer.Serialize(res));
            Console.WriteLine("D|done");
            return 0;
        }
        catch (Exception ex) { Console.WriteLine("E|" + ex); return 1; }
    }
}
