// UI smoke test for 标注检查 (no Revit needed). Runs the built TagCheck.dll: standard-file text parsing,
// summary and CSV text, the real TagCheckForm with fake rows (filter, row colours), and screenshots.
// Usage: dotnet run -c Release -- [output folder]
using System.Drawing.Imaging;
using System.Reflection;
using System.Text.Json;
using System.Windows.Forms;
using KaifaTools.TagCheck;

Console.OutputEncoding = System.Text.Encoding.UTF8;
string outDir = args.Length > 0 ? args[0]
    : @"C:\Users\PCB\AppData\Local\Temp\claude\C--KAIFA\90a925ad-2baa-4535-a5f3-989e0ce735d2\scratchpad";
Directory.CreateDirectory(outDir);

int failed = 0;
void Check(string name, bool ok, string detail = "")
{
    Console.WriteLine($"{(ok ? "PASS" : "FAIL")}  {name}" + (detail.Length > 0 ? $"  [{detail}]" : ""));
    if (!ok) failed++;
}

// 1) standard file text. ParseStandard takes the text, so no file is read or written.
var oldFile = TagCheckEngine.ParseStandard("{ \"Categories\": { \"结构框架\": [\"结构框架标记-排管\"] }, \"说明\": \"旧文件\" }", out var e1);
Check("old file without layout loads with defaults",
    oldFile.Layout.OffsetMm == 1200 && oldFile.Layout.MinGapMm == 150 && oldFile.Layout.Side == "auto"
    && oldFile.Layout.MaxLeaderMm == 3000 && e1 == null && oldFile.Categories["结构框架"][0] == "结构框架标记-排管", e1 ?? "");

var full = TagCheckEngine.ParseStandard("{ \"layout\": { \"offsetMm\": 900, \"minGapMm\": 200, \"side\": \"Left\", \"maxLeaderMm\": 2500 } }", out var e2);
Check("layout section is read; side is case-insensitive",
    full.Layout.OffsetMm == 900 && full.Layout.MinGapMm == 200 && full.Layout.Side == "left" && full.Layout.MaxLeaderMm == 2500 && e2 == null, e2 ?? "");

var partial = TagCheckEngine.ParseStandard("{ \"layout\": { \"side\": \"up\" } }", out var e3);
Check("partial layout keeps defaults for missing keys",
    partial.Layout.Side == "up" && partial.Layout.OffsetMm == 1200 && partial.Layout.MinGapMm == 150 && partial.Layout.MaxLeaderMm == 3000 && e3 == null, e3 ?? "");

var badSide = TagCheckEngine.ParseStandard("{ \"layout\": { \"side\": \"diagonal\", \"offsetMm\": 800 } }", out var e4);
Check("bad side falls back to auto and is reported",
    badSide.Layout.Side == "auto" && badSide.Layout.OffsetMm == 800 && e4 != null && e4.Contains("side"), e4 ?? "");

var negative = TagCheckEngine.ParseStandard("{ \"layout\": { \"offsetMm\": -5, \"minGapMm\": -1, \"maxLeaderMm\": 0 } }", out var e5);
Check("bad numbers fall back to defaults and are reported",
    negative.Layout.OffsetMm == 1200 && negative.Layout.MinGapMm == 150 && negative.Layout.MaxLeaderMm == 3000 && e5 != null, e5 ?? "");

var nullLayout = TagCheckEngine.ParseStandard("{ \"layout\": null }", out var e6);
Check("null layout section uses defaults", nullLayout.Layout.OffsetMm == 1200 && e6 == null, e6 ?? "");

var broken = TagCheckEngine.ParseStandard("{ \"layout\": ", out var e7);
Check("broken json is reported and defaults are used", e7 != null && broken.Layout.OffsetMm == 1200, e7 ?? "");

string written = JsonSerializer.Serialize(new TagStandard(), new JsonSerializerOptions { WriteIndented = true });
Check("a new standard file is written with a layout section",
    written.Contains("\"layout\"") && written.Contains("\"offsetMm\": 1200") && written.Contains("\"maxLeaderMm\": 3000"));

// 2) summary and CSV text
var rows = FakeRows();
string summary = TagCheckEngine.BuildSummary("测试视图", rows);
Check("summary has the status counts and the separate position count",
    summary.Contains("共 10 个构件") && summary.Contains("漏标 2") && summary.Contains("用错 2")
    && summary.Contains("标注重叠 1") && summary.Contains("已标 5") && summary.Contains("另有位置不佳 4 个"), summary);

string csv = TagCheckEngine.CsvText(rows);
var lines = csv.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries);
Check("csv header has the two new columns",
    lines[0] == "ElementId,状态,标记,类别,族,当前标签,应为标签,位置,位置说明", lines[0]);
Check("csv has one line per row", lines.Length == rows.Count + 1, $"lines={lines.Length}");
Check("csv writes the position text and note",
    lines.Any(l => l.Contains("\"位置不佳\"") && l.Contains("\"引线约 3600 mm，超过 3000 mm\"")));

// 3) the real form with fake rows. The grid is filled once the window is shown, so the form checks run in the first tick.
Application.SetHighDpiMode(HighDpiMode.SystemAware);
Application.EnableVisualStyles();
var form = new TagCheckForm(rows, summary, null);
var flags = BindingFlags.NonPublic | BindingFlags.Instance;
var filterBox = (ComboBox)typeof(TagCheckForm).GetField("_filter", flags)!.GetValue(form)!;
var grid = (DataGridView)typeof(TagCheckForm).GetField("_grid", flags)!.GetValue(form)!;
var summaryLabel = (Label)typeof(TagCheckForm).GetField("_summary", flags)!.GetValue(form)!;

int step = 0;
var timer = new System.Windows.Forms.Timer { Interval = 700 };
timer.Tick += (_, _) =>
{
    timer.Stop();
    if (step++ == 0)
    {
        Check("default filter shows the rows that need work (not 已标 without position warning)",
            grid.Rows.Count == 8, $"rows={grid.Rows.Count}");
        Check("summary line shows the shown row count", summaryLabel.Text.Contains("当前列表显示 8 行"), summaryLabel.Text);

        var posColour = Color.FromArgb(255, 249, 196);
        var useColour = Color.FromArgb(255, 224, 178);
        bool posColourOk = true, useColourOk = true;
        int posRows = 0, useRows = 0;
        foreach (DataGridViewRow r in grid.Rows)
        {
            var row = (TagRow)r.DataBoundItem!;
            if (row.Position.Length > 0)
            {
                posRows++;
                if (row.Status == "已标" && r.DefaultCellStyle.BackColor != posColour) posColourOk = false;
                if (r.Cells["Position"].Style.BackColor != posColour) posColourOk = false;
            }
            else if (row.Status == "用错")
            {
                useRows++;
                if (r.DefaultCellStyle.BackColor != useColour) useColourOk = false;
            }
        }
        Check("position rows use the light yellow highlight", posColourOk && posRows == 4, $"rows={posRows}");
        Check("用错 rows keep their own colour (not yellow)", useColourOk && useRows == 1, $"rows={useRows}");

        Save(form, Path.Combine(outDir, "tagcheck_form_default.png"));
        filterBox.SelectedIndex = 4;                 // 只看位置不佳
        timer.Start();
    }
    else
    {
        Check("position filter shows the 4 position rows", grid.Rows.Count == 4, $"rows={grid.Rows.Count}");
        Save(form, Path.Combine(outDir, "tagcheck_form_position_filter.png"));
        form.Close();
    }
};
form.Shown += (_, _) => timer.Start();
Application.Run(form);

Console.WriteLine(failed == 0 ? "ALL PASS" : $"{failed} FAILED");
return failed == 0 ? 0 : 1;

static void Save(Form f, string path)
{
    using var bmp = new Bitmap(f.Width, f.Height);
    f.DrawToBitmap(bmp, new Rectangle(0, 0, f.Width, f.Height));
    bmp.Save(path, ImageFormat.Png);
    Console.WriteLine("saved " + path);
}

static List<TagRow> FakeRows() => new()
{
    new TagRow { ElementId = 1001, Mark = "L-001", Category = "结构框架", Family = "结构框架-排管", Status = "已标",
        Current = "结构框架标记-排管", Expected = "结构框架标记-排管", Position = TagRow.PositionBad, PositionNote = "引线约 3600 mm，超过 3000 mm" },
    new TagRow { ElementId = 1002, Mark = "L-002", Category = "结构框架", Family = "结构框架-排管", Status = "已标",
        Current = "结构框架标记-排管", Expected = "结构框架标记-排管" },
    new TagRow { ElementId = 1003, Mark = "L-003", Category = "结构框架", Family = "结构框架-排管", Status = "漏标",
        Current = "", Expected = "结构框架标记-排管" },
    new TagRow { ElementId = 1004, Mark = "E-011", Category = "常规模型", Family = "ELE-EM2-L", Status = "用错",
        Current = "常规模型标记2", Expected = "常规模型标记1" },
    new TagRow { ElementId = 1005, Mark = "E-012", Category = "常规模型", Family = "ELE-EM1-I", Status = "用错",
        Current = "常规模型标记2", Expected = "常规模型标记1", Position = TagRow.PositionBad, PositionNote = "标注超出视图裁剪框" },
    new TagRow { ElementId = 1006, Mark = "T-021", Category = "线管", Family = "ELE-TELCO(96WAY)", Status = "重叠",
        Current = "线管标记-排管", Expected = "线管标记-排管" },
    new TagRow { ElementId = 1007, Mark = "T-022", Category = "线管", Family = "TELCO-室内井", Status = "已标",
        Current = "线管标记-排管", Expected = "线管标记-排管", Position = TagRow.PositionBad, PositionNote = "标注超出视图裁剪框；引线约 3300 mm，超过 3000 mm" },
    new TagRow { ElementId = 1008, Mark = "V-031", Category = "常规模型", Family = "ELV PIT", Status = "已标",
        Current = "常规模型标记1", Expected = "常规模型标记1", Position = TagRow.PositionBad, PositionNote = "引线约 3050 mm，超过 3000 mm" },
    new TagRow { ElementId = 1009, Mark = "V-032", Category = "常规模型", Family = "ELV-MANHOLE", Status = "漏标",
        Current = "", Expected = "常规模型标记1" },
    new TagRow { ElementId = 1010, Mark = "T-023", Category = "线管", Family = "线管-室内", Status = "已标",
        Current = "线管标记-排管", Expected = "线管标记-排管" },
};
