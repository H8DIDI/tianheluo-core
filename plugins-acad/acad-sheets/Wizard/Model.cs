// 一键出图向导：界面和 AutoCAD 之间的约定。界面（WizardForm / SettingsForm）只认这里的类型，
// AutoCAD 侧由 Backend 实现；测试时换成假后端，界面可以脱离 AutoCAD 点一遍、截图。
using System;
using System.Collections.Generic;
using System.Windows.Forms;

namespace Thl.Acad.Sheets.Wizard
{
    public sealed class ZoneInfo
    {
        public string Name = "";
        public List<double[]> Poly = new List<double[]>();   // 每点 {x, y}
        public string Source = "";                            // 图层 / 框选 / 网格 / 整张图
        public double X0 { get { double m = double.MaxValue; foreach (var p in Poly) m = Math.Min(m, p[0]); return m; } }
        public double X1 { get { double m = double.MinValue; foreach (var p in Poly) m = Math.Max(m, p[0]); return m; } }
        public double Y0 { get { double m = double.MaxValue; foreach (var p in Poly) m = Math.Min(m, p[1]); return m; } }
        public double Y1 { get { double m = double.MinValue; foreach (var p in Poly) m = Math.Max(m, p[1]); return m; } }
        public static ZoneInfo Rect(string name, double x0, double y0, double x1, double y1, string src) => new ZoneInfo
        {
            Name = name, Source = src,
            Poly = new List<double[]> { new[] { x0, y0 }, new[] { x1, y0 }, new[] { x1, y1 }, new[] { x0, y1 } },
        };
    }

    public sealed class SheetPlan
    {
        public bool On = true;
        public string Zone = "", Title = "", Number = "", SplitOf = "";
        public int Scale;
        public double Fill;
        public List<double[]> Poly = new List<double[]>();
    }

    public sealed class WizardSettings
    {
        public bool Slim, Tidy = true;
        public string Template = "", Paper = "A1";
        public string ScaleMode = "each";          // each = 各张自动；uniform = 全套统一（自动）；fixed = 指定
        public int FixedScale = 500;
        public double MinFill = 0.7;
        public bool Split = true, Legend = true, Index = true, Match = true, Toc = true, Pdf = true;
        public string NumPrefix = "", BaseTitle = "管线综合平面图", TitleRule = "{分区} {图名}";
        public int NumDigits = 3, NumStart = 1;
        public string PdfPath = "";
        public string Number(int i) => NumPrefix + (NumStart + i).ToString(new string('0', Math.Max(1, NumDigits)));
        public string Title(string zone) => TitleRule.Replace("{分区}", zone).Replace("{图名}", BaseTitle).Trim();
    }

    public sealed class LegendRow { public string Layer = "", Name = ""; public int Count; public bool Show = true; public int Rgb; }

    public sealed class TidyRule { public string Pattern = "", Color = "", Linetype = "", Plot = ""; public string Lw = ""; }

    public sealed class TidyConfig
    {
        public bool Fonts = true, Linetypes = true, Duplicates = true, DarkenPale = true, ByLayer, MaskMText, KeepText = true, Purge = true;
        public string ShxFont = "gbenor.shx", BigFont = "gbcbig.shx";
        public List<TidyRule> Rules = new List<TidyRule>();
    }

    /// <summary>向导要 AutoCAD 做的事。所有方法出错时抛 UserError（话说给人听），界面直接显示。</summary>
    public interface IWizardBackend
    {
        string DocName { get; }
        string DocPath { get; }
        double FileMB { get; }
        bool HasCadUnderlay { get; }

        Dictionary<string, string> LoadPrefs();
        void SavePrefs(Dictionary<string, string> p);

        List<string> Templates();
        string LearnTemplate(string dwg, string name, Action<string> log);
        string TemplateInfo(string name);

        List<ZoneInfo> ZonesFromLayer();
        List<ZoneInfo> PickZones(IWin32Window owner);
        List<ZoneInfo> GridZones(WizardSettings s);
        List<ZoneInfo> WholeDrawing();
        double[] Extents();                                  // 模型范围 x0,y0,x1,y1（缩略图用）

        List<SheetPlan> Plan(List<ZoneInfo> zones, WizardSettings s, Action<string> log);
        string Prepare(WizardSettings s, Action<int, string> progress);
        string Generate(List<SheetPlan> plans, List<ZoneInfo> zones, WizardSettings s, Action<int, string> progress, Func<bool> cancel);
        void Open(string path);

        List<LegendRow> LegendRows();
        void SaveLegend(List<LegendRow> rows);
        TidyConfig LoadTidy();
        TidyConfig DefaultTidy();
        void SaveTidy(TidyConfig c);
        string ExportScheme(string zipPath, string template);
        string ImportScheme(string zipPath);
        string SettingsFolder { get; }
    }

    /// <summary>给用户看的错误：Message 说清楚发生了什么、怎么办。</summary>
    public sealed class UserError : Exception { public UserError(string msg) : base(msg) { } }
}
