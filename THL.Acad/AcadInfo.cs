using Autodesk.AutoCAD.ApplicationServices.Core;

namespace THL.Host;

/// <summary>当前 AutoCAD 的年份和 .NET 运行时（决定插件包里用 net48\ 还是 net8\）。</summary>
public static class AcadInfo
{
    /// <summary>"net8"（AutoCAD 2025+，.NET 8）或 "net48"（2018~2024，.NET Framework）。</summary>
    public static string Fx => Environment.Version.Major >= 5 ? "net8" : "net48";

    static int _year;
    public static int Year
    {
        get
        {
            if (_year != 0) return _year;
            try
            {
                // ACADVER 形如 "25.1s (LMS Tech)"
                var s = (Application.GetSystemVariable("ACADVER") as string ?? "").Split(' ')[0].TrimEnd('s');
                var ab = s.Split('.');
                _year = YearOf(int.Parse(ab[0]), ab.Length > 1 ? int.Parse(new string(ab[1].TakeWhile(char.IsDigit).ToArray())) : 0);
            }
            catch { _year = Environment.Version.Major >= 5 ? 2025 : 2018; }
            return _year;
        }
    }

    /// <summary>R 号 → 年份：R22.0=2018、R23.0=2019、R23.1=2020、R24.0~24.3=2021~2024、R25.0=2025、R25.1=2026。</summary>
    public static int YearOf(int major, int minor) => major switch
    {
        < 22 => 2017,
        22 => 2018,
        23 => 2019 + minor,
        24 => 2021 + minor,
        _ => 2025 + (major - 25) * 2 + minor,   // 之后按每个 R 号两年推（R26.0 预计 2027）
    };
}
