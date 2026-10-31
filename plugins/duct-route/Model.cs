using System.Text.Json;
using System.Text.Json.Serialization;

namespace KaifaTools.DuctRoute;

/// <summary>User rules. Read from Documents\KaifaTools\管综排管路由规则.json; every value has a default.</summary>
public sealed class RouteRules
{
    public double CoverRoadM { get; set; } = 1.2;        // 路面楼板范围内：排管顶距路面顶
    public double CoverOffM { get; set; } = 0.7;         // 路面外：排管顶距地面
    public double ClearCrossM { get; set; } = 0.2;       // 交叉处净距
    public double ClearParallelM { get; set; } = 0.05;   // 平行/贴邻处净距
    public double CrossAngleDeg { get; set; } = 30;      // 夹角大于该值算交叉
    public double MinBottomM { get; set; } = 0.0;        // 排管底标高下限
    public double MaxSlopePct { get; set; } = 8;         // 最大坡度（原坡度更大的按原坡度）
    public double SearchRangeM { get; set; } = 4.0;
    public int StepMm { get; set; } = 100;               // 两端标高取整到多少毫米（100=0.1 m 一档，0=不取整）
    public double MaxDepthM { get; set; } = 5.0;         // 排管底最深到地面（路面或最近路面）以下多少米      // 每端标高相对原值最多上下调多少
    public double WellTolM { get; set; } = 0.30;         // 平面上与井叠不到这个值，当作蹭边不算碰撞
    public double EndZoneM { get; set; } = 5.0;          // 起点/终点这个范围内被别的井压到，当作井压井不躲
    public double JointTolM { get; set; } = 0.02;        // 首尾相连的排管接头标高容差
    public double MarginM { get; set; } = 80;            // 收集障碍的外扩范围
    public string RoadKeyword { get; set; } = "道路";   // 路面楼板类型名关键字
    public double? GroundM { get; set; } = null;         // 路面楼板之外的地面标高；空=取最近一块路面
    public bool AvoidPipes { get; set; } = false;        // 是否把水管当障碍
    public int SecondsPerRound { get; set; } = 90;       // 每轮求解时限
    public int Rounds { get; set; } = 6;
    public double WeightUp { get; set; } = 1;
    public double WeightDown { get; set; } = 5;
    public double WeightNonFlat { get; set; } = 1500;

    public static string FilePath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "KaifaTools", "管综排管路由规则.json");

    public static RouteRules Load(out string? error)
    {
        error = null;
        try
        {
            if (File.Exists(FilePath))
                return JsonSerializer.Deserialize<RouteRules>(File.ReadAllText(FilePath), new JsonSerializerOptions { ReadCommentHandling = JsonCommentHandling.Skip, AllowTrailingCommas = true }) ?? new RouteRules();
            Directory.CreateDirectory(Path.GetDirectoryName(FilePath)!);
            File.WriteAllText(FilePath, JsonSerializer.Serialize(new RouteRules(), new JsonSerializerOptions { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }));
        }
        catch (Exception ex) { error = "规则文件读取失败，已用默认值：" + ex.Message; }
        return new RouteRules();
    }
}

/// <summary>An obstacle point: horizontal position, vertical centre, half height, radius, optional direction.</summary>
public readonly struct Samp
{
    public readonly double X, Y, Zc, Hz, R, Dx, Dy; public readonly bool HasDir; public readonly long Id;
    public Samp(double x, double y, double zc, double hz, double r, double dx, double dy, bool hasDir, long id)
    { X = x; Y = y; Zc = zc; Hz = hz; R = r; Dx = dx; Dy = dy; HasDir = hasDir; Id = id; }
}

public sealed class Road { public double Ztop; public double[][] Poly = Array.Empty<double[]>(); public double MinX, MinY, MaxX, MaxY; }

public sealed class BoxObs
{
    public long Id; public string Mark = ""; public string Family = ""; public string? Kind;
    public double MinX, MinY, MinZ, MaxX, MaxY, MaxZ;
    public double Dx, Dy; public bool HasDir; public bool IsDrain;
}

public sealed record Tie(long OtherId, bool OtherStart, double TolM);

public sealed class Bank
{
    public long Id; public string Kind = ""; public string Family = ""; public string Comment = "";
    public double Ax, Ay, Bx, By, L, Ux, Uy, Nx, Ny, W, H;
    public double O0, O1;            // original bottom elevation at the two ends (m)
    public double Z0, Z1;            // current / solved bottom elevation (m)
    public bool Movable; public bool Frozen;
    public HashSet<long> Wid = new();                        // wells this bank reaches (same kind)
    public List<(BoxObs Well, double S0, double S1)> Links = new();
    public List<(long Id, double Px, double Py)> Junc = new();
    public List<Tie> Nb0 = new(), Nb1 = new();
    public string Status = ""; public string Detail = "";
    public bool Changed => Math.Abs(Z0 - O0) > 1e-6 || Math.Abs(Z1 - O1) > 1e-6;
}

public static class DictExt
{
    public static V GetOrAdd<K, V>(this Dictionary<K, V> d, K key, Func<V> make) where K : notnull { if (!d.TryGetValue(key, out var v)) d[key] = v = make(); return v; }
}

public sealed class Scene
{
    public List<Bank> Banks = new();                 // all banks (movable and fixed)
    public List<BoxObs> Wells = new(), Drains = new();
    public List<Road> Roads = new();
    public List<(double[] A, double[] B, double R)> Pipes = new();
    public double[] RoadVx = Array.Empty<double>(), RoadVy = Array.Empty<double>(), RoadVz = Array.Empty<double>();
    [JsonIgnore] public Dictionary<long, Bank> ById = new();
    public void Rebuild() { ById = Banks.ToDictionary(b => b.Id); }
    public IEnumerable<Bank> Movable => Banks.Where(b => b.Movable);
}
