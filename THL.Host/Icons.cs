using System.Globalization;
using System.IO;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace THL.Host;

/// <summary>
/// 图标工厂：品牌 Logo（THL）和每个插件的图标（圆角色块 + 一个字）。全部运行时绘制，不依赖图片文件；
/// 插件包里自带 icon.png 时优先使用。
/// </summary>
public static class Icons
{
    static readonly Dictionary<string, string> CategoryColor = new()
    {
        ["市政管线"] = "#2F8CFF", ["出图打印"] = "#8B5CF6", ["数据处理"] = "#10B981",
        ["导出"] = "#F59E0B", ["建模辅助"] = "#EC4899", ["视图管理"] = "#06B6D4",
    };

    static Color C(string hex) => (Color)ColorConverter.ConvertFromString(hex);
    static Color Lighten(Color c, double k) => Color.FromRgb((byte)(c.R + (255 - c.R) * k), (byte)(c.G + (255 - c.G) * k), (byte)(c.B + (255 - c.B) * k));
    static Color Darken(Color c, double k) => Color.FromRgb((byte)(c.R * (1 - k)), (byte)(c.G * (1 - k)), (byte)(c.B * (1 - k)));

    /// <summary>品牌 Logo：蓝色渐变圆角方块 + "THL"。</summary>
    public static BitmapSource Logo(int px) => Badge(px, "THL", C("#3AA4FF"), fontScale: 0.36, bold: true);

    /// <summary>插件图标。优先用插件包里的 icon.png，其次按清单的 glyph/color，最后按名字和分类推断。</summary>
    public static BitmapSource ForPlugin(Installed p, int px)
    {
        var png = Path.Combine(p.Dir, "icon.png");
        if (File.Exists(png))
        {
            try
            {
                var bi = new BitmapImage();
                bi.BeginInit(); bi.CacheOption = BitmapCacheOption.OnLoad; bi.UriSource = new Uri(png); bi.DecodePixelWidth = px; bi.EndInit(); bi.Freeze();
                return bi;
            }
            catch { /* 图片坏了就用生成的 */ }
        }
        return Generate(p.Manifest, px);
    }

    public static BitmapSource Generate(Manifest m, int px)
    {
        var glyph = !string.IsNullOrWhiteSpace(m.Glyph) ? m.Glyph : GuessGlyph(m.Name);
        var color = !string.IsNullOrWhiteSpace(m.Color) ? C(m.Color)
                  : CategoryColor.TryGetValue(m.Category ?? "", out var hex) ? C(hex) : C("#3B9BFF");
        return Badge(px, glyph, color, fontScale: glyph.Length > 1 ? 0.40 : 0.58, bold: true);
    }

    /// <summary>从名字里挑一个有辨识度的字：去掉“一键/清理/强电”等前缀后的第一个字。</summary>
    static string GuessGlyph(string name)
    {
        var n = name.Trim();
        foreach (var pre in new[] { "一键", "清理重复", "清理", "强电" })
            if (n.StartsWith(pre) && n.Length > pre.Length) { n = n[pre.Length..]; break; }
        return n.Length > 0 ? n[..1] : "T";
    }

    static BitmapSource Badge(int px, string text, Color baseColor, double fontScale, bool bold)
    {
        var dv = new DrawingVisual();
        using (var dc = dv.RenderOpen())
        {
            double s = px, r = s * 0.24, pad = s * 0.04;
            var rect = new Rect(pad, pad, s - 2 * pad, s - 2 * pad);
            var fill = new LinearGradientBrush(Lighten(baseColor, 0.18), Darken(baseColor, 0.22), 60);
            dc.DrawRoundedRectangle(fill, new Pen(new SolidColorBrush(Color.FromArgb(120, 255, 255, 255)), Math.Max(1, s / 40)), rect, r, r);
            // 顶部高光
            var gloss = new LinearGradientBrush(Color.FromArgb(70, 255, 255, 255), Color.FromArgb(0, 255, 255, 255), 90);
            dc.DrawRoundedRectangle(gloss, null, new Rect(rect.X + s * 0.04, rect.Y + s * 0.04, rect.Width - s * 0.08, rect.Height * 0.46), r * 0.7, r * 0.7);
            var ft = new FormattedText(text, CultureInfo.CurrentUICulture, FlowDirection.LeftToRight,
                new Typeface(new FontFamily("Microsoft YaHei UI, Segoe UI"), FontStyles.Normal, bold ? FontWeights.Bold : FontWeights.Normal, FontStretches.Normal),
                s * fontScale, Brushes.White, 1.0);
            dc.DrawText(ft, new Point((s - ft.Width) / 2, (s - ft.Height) / 2 + s * 0.01));
        }
        var rtb = new RenderTargetBitmap(px, px, 96, 96, PixelFormats.Pbgra32);
        rtb.Render(dv); rtb.Freeze();
        return rtb;
    }
}
