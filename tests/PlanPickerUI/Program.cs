// UI smoke test for the 批量生成平面图 picker (no Revit).
// Usage: dotnet run -- <screenshot.png>   (a second screenshot "<name>_min.png" is saved next to it)
// Checks naming rules, the memory file (in the output folder, never the real Documents), restore,
// the live summary, search with kept ticks, 全选/全不选, validation, layout overlaps; exit code 1 on any failure.
using System.Drawing;
using System.Drawing.Imaging;
using System.Windows.Forms;
using KaifaTools.PlanBatch;

if (args.Length < 1)
{
    Console.WriteLine("usage: PlanPickerUI <screenshot.png>");
    return 2;
}

string shotDefault = Path.GetFullPath(args[0]);
string outDir = Path.GetDirectoryName(shotDefault)!;
string shotMin = Path.Combine(outDir, Path.GetFileNameWithoutExtension(shotDefault) + "_min.png");
Directory.CreateDirectory(outDir);
Application.EnableVisualStyles();

var failures = new List<string>();
void Check(bool ok, string what)
{
    Console.WriteLine((ok ? "PASS  " : "FAIL  ") + what);
    if (!ok) failures.Add(what);
}

T Get<T>(Control root, string name) where T : Control => (T)root.Controls.Find(name, true)[0];

// names of the ticked rows that are currently shown
List<string> Ticked(CheckedListBox box) => box.CheckedItems.Cast<object>().Select(o => o.ToString()!).ToList();

string Describe(Control c) => string.IsNullOrEmpty(c.Name) ? c.GetType().Name + "[" + c.Text + "]" : c.Name;

// overlapping siblings, or children that stick out of their parent
List<string> LayoutProblems(Control parent)
{
    var problems = new List<string>();
    void Walk(Control p)
    {
        var kids = p.Controls.Cast<Control>().Where(c => c.Visible).ToList();
        foreach (var c in kids)
            if (c.Left < 0 || c.Top < 0 || c.Right > p.ClientSize.Width || c.Bottom > p.ClientSize.Height)
                problems.Add($"{Describe(c)} outside {Describe(p)}");
        for (int i = 0; i < kids.Count; i++)
            for (int j = i + 1; j < kids.Count; j++)
                if (kids[i].Bounds.IntersectsWith(kids[j].Bounds))
                    problems.Add($"{Describe(kids[i])} overlaps {Describe(kids[j])}");
        foreach (var c in kids) Walk(c);
    }
    Walk(parent);
    return problems;
}

void Snapshot(Form f, string path)
{
    Application.DoEvents();
    using var bmp = new Bitmap(f.Width, f.Height);
    f.DrawToBitmap(bmp, new Rectangle(0, 0, f.Width, f.Height));
    bmp.Save(path, ImageFormat.Png);
}

// shows the form, runs the body once it is on screen, then closes it
void RunForm(Form form, Action<Form> body)
{
    form.Shown += (_, _) =>
    {
        try { body(form); }
        catch (Exception ex) { Check(false, "exception: " + ex); }
        finally { form.Close(); }
    };
    Application.Run(form);
}

// 1. naming rules (pure)
Check(PlanNaming.Build("{标高}_{样板}", "一层", "PV建模视图") == "一层_PV建模视图", "name: level_template");
Check(PlanNaming.Build("{标高}_{样板}", "一层", null) == "一层_平面", "name: no template gives 标高_平面");
Check(PlanNaming.Build("{样板}-{标高}", "二层", null) == "平面-二层", "name: placeholders can change place");
Check(PlanNaming.Validate("{标高}_{样板}") is null, "validate: default pattern ok");
Check(PlanNaming.Validate("{标高}") is null, "validate: level only ok");
Check(PlanNaming.Validate("{样板}") is not null, "validate: {标高} is required");
Check(PlanNaming.Validate("{标高}_{名称}") is not null, "validate: unknown placeholder rejected");
Check(PlanNaming.Validate("{标高}:{样板}") is not null, "validate: character Revit refuses rejected");
Check(PlanNaming.Validate("   ") is not null, "validate: blank rejected");

// 2. memory file: round trip, missing, broken and odd content (scratch folder, not Documents)
string memFile = Path.Combine(outDir, "memory-test", "批量平面图记忆.json");
Directory.CreateDirectory(Path.GetDirectoryName(memFile)!);
if (File.Exists(memFile)) File.Delete(memFile);
Check(PickerMemoryStore.DefaultPath.EndsWith(Path.Combine("KaifaTools", "批量平面图记忆.json")), "memory: default path is Documents\\KaifaTools\\批量平面图记忆.json");
Check(PickerMemoryStore.Load(memFile) is null, "memory: missing file gives null");
var saved = new PickerMemory(new[] { "PV建模视图", "楼层平面 2" }, new[] { "一层", "三层" }, "{标高}-{样板}");
Check(PickerMemoryStore.Save(saved, memFile), "memory: save ok");
Check(File.ReadAllText(memFile).Contains("一层"), "memory: Chinese names readable in the file");
var loaded = PickerMemoryStore.Load(memFile);
Check(loaded is not null && loaded.Templates.SequenceEqual(saved.Templates) && loaded.Levels.SequenceEqual(saved.Levels)
      && loaded.NamePattern == saved.NamePattern, "memory: round trip");
File.WriteAllText(memFile, "{ this is not json");
Check(PickerMemoryStore.Load(memFile) is null, "memory: broken json gives null, no exception");
File.WriteAllText(memFile, "{\"templates\": 5, \"levels\": \"x\"}");
Check(PickerMemoryStore.Load(memFile) is null, "memory: wrong types give null, no exception");
File.WriteAllText(memFile, "{\"templates\": null, \"levels\": [\"一层\", null, \" \"], \"namePattern\": null}");
var odd = PickerMemoryStore.Load(memFile);
Check(odd is not null && odd.Templates.Count == 0 && odd.Levels.SequenceEqual(new[] { "一层" }) && odd.NamePattern == "",
      "memory: null and blank entries cleaned");

// 3. picker window with a remembered selection
string[] types = { "（不套用样板）", "楼层平面", "PV建模视图", "暖通建模视图", "电照建模视图", "楼层平面 2", "CDA建模视图",
                   "暖通出图视图", "楼层平面1", "楼层平面 3", "暖通出图视图1", "暖通出图视图11", "暖通出图视图111" };
string[] levels = { "一层", "二层", "三层", "四层" };
var existing = new[] { "一层-PV建模视图" };
var remembered = new PickerMemory(new[] { "PV建模视图", "楼层平面 2", "已删除的样板" }, new[] { "一层", "三层", "不存在的标高" }, "{标高}-{样板}");

var form1 = new PlanPickerForm(types, levels, existing, remembered);
RunForm(form1, f =>
{
    var tl = Get<CheckedListBox>(f, "typesList");
    var ll = Get<CheckedListBox>(f, "levelsList");
    var tsearch = Get<TextBox>(f, "typesSearch");
    var tSummary = Get<Label>(f, "typesSummary");
    var pattern = Get<TextBox>(f, "pattern");
    var line1 = Get<Label>(f, "statusLine1");
    var line2 = Get<Label>(f, "statusLine2");
    var line3 = Get<Label>(f, "statusLine3");

    Check(Ticked(tl).SequenceEqual(new[] { "PV建模视图", "楼层平面 2" }), "restore: template ticks come back, unknown name ignored");
    Check(Ticked(ll).SequenceEqual(new[] { "一层", "三层" }), "restore: level ticks come back, unknown level ignored");
    Check(pattern.Text == "{标高}-{样板}", "restore: naming pattern comes back");
    Check(tSummary.Text == "已选 2 / 13", "summary: template count");

    Check(line1.Text == "计划生成 4 个视图（2 种类型 × 2 个标高）" && line1.ForeColor != PlanPickerForm.WarningColor, "summary: 4 views, no warning");
    Check(line2.Text == "同名已存在 1 个，将跳过；实际新建 3 个", "summary: 1 existing name skipped, 3 new");
    Check(line3.Text == "示例：一层-PV建模视图", "summary: example is the first planned name");

    var problems = LayoutProblems(f);
    Check(problems.Count == 0, "layout 840x560: no overlap or clipping" + (problems.Count > 0 ? " -> " + string.Join(" | ", problems.Take(4)) : ""));
    Snapshot(f, shotDefault);

    // search: a tick made while filtered is kept when the filter is cleared
    tsearch.Text = "暖通";
    Check(tl.Items.Count == 5, "search 暖通: 5 rows shown");
    tl.SetItemChecked(tl.Items.IndexOf("暖通出图视图"), true);
    tsearch.Text = "";
    Check(tl.Items.Count == 13 && Ticked(tl).SequenceEqual(new[] { "PV建模视图", "楼层平面 2", "暖通出图视图" }),
          "search: hidden ticks kept after clearing the filter");
    Check(tSummary.Text == "已选 3 / 13", "summary: 3 of 13 after the tick");

    // 全选 / 全不选 act on the shown rows only
    tsearch.Text = "暖通";
    Get<Button>(f, "typesAll").PerformClick();
    Check(tl.CheckedItems.Count == 5, "全选 while filtered: the 5 shown rows ticked");
    tsearch.Text = "";
    Check(Ticked(tl).Count == 7 && Ticked(tl).Contains("PV建模视图") && Ticked(tl).Contains("楼层平面 2") && !Ticked(tl).Contains("楼层平面"),
          "全选 while filtered: hidden ticks kept (7 ticked in total)");
    tsearch.Text = "暖通";
    Get<Button>(f, "typesNone").PerformClick();
    tsearch.Text = "";
    Check(Ticked(tl).SequenceEqual(new[] { "PV建模视图", "楼层平面 2" }), "全不选 while filtered: only the shown rows cleared");

    // naming validation and duplicate warning
    pattern.Text = "{样板}";
    Check(line2.Text.Contains("必须包含 {标高}") && line2.ForeColor == PlanPickerForm.WarningColor, "pattern without {标高}: orange message");
    pattern.Text = "{标高}";
    Check(line1.Text == "计划生成 2 个视图（2 种类型 × 2 个标高）" && line3.Text.Contains("重复") && line3.ForeColor == PlanPickerForm.WarningColor,
          "pattern {标高} only: duplicate names warned");
    pattern.Text = "{标高}-{样板}";
    Check(line3.Text == "示例：一层-PV建模视图", "pattern restored: example back");

    // layout at the minimum size and at a large size
    f.Size = f.MinimumSize;
    Check(LayoutProblems(f).Count == 0 && tl.Height >= 200, "layout at minimum size: no overlap, list still usable");
    Snapshot(f, shotMin);
    f.Size = new Size(1100, 760);
    Check(LayoutProblems(f).Count == 0, "layout at 1100x760: no overlap");
    f.Size = new Size(840, 560);

    // 确认 returns the ticks and the pattern
    Get<Button>(f, "ok").PerformClick();
});
Check(form1.DialogResult == DialogResult.OK, "确认 closes the window with DialogResult.OK");
Check(form1.TypeIndexes.SequenceEqual(new[] { 2, 5 }) && form1.LevelIndexes.SequenceEqual(new[] { 0, 2 }) && form1.NamePattern == "{标高}-{样板}",
      "确认 returns the ticked indexes and the pattern");

// 4. nothing remembered: 0 views in orange, then 120 views in orange, 不套用样板 example
var manyLevels = Enumerable.Range(1, 30).Select(i => $"L{i:00}").ToArray();
var form2 = new PlanPickerForm(new[] { "（不套用样板）", "楼层平面", "PV建模视图", "暖通建模视图" }, manyLevels);
RunForm(form2, f =>
{
    var line1 = Get<Label>(f, "statusLine1");
    var line3 = Get<Label>(f, "statusLine3");
    Check(line1.Text == "计划生成 0 个视图" && line1.ForeColor == PlanPickerForm.WarningColor, "nothing remembered: 0 views in orange");
    Check(Get<CheckedListBox>(f, "typesList").CheckedItems.Count == 0, "nothing remembered: nothing ticked");
    Check(Get<TextBox>(f, "pattern").Text == "{标高}_{样板}", "nothing remembered: default naming pattern");
    Get<Button>(f, "typesAll").PerformClick();
    Get<Button>(f, "levelsAll").PerformClick();
    Check(line1.Text == "计划生成 120 个视图（4 种类型 × 30 个标高）" && line1.ForeColor == PlanPickerForm.WarningColor, "120 views: orange warning");
    Check(line3.Text == "示例：L01_平面", "不套用样板 row gives 标高_平面");
    Get<Button>(f, "levelsNone").PerformClick();
    Check(line1.Text == "计划生成 0 个视图" && line1.ForeColor == PlanPickerForm.WarningColor, "no level ticked: 0 views in orange");
    Check(LayoutProblems(f).Count == 0, "layout with 30 levels: no overlap");
});

// 5. a remembered pattern that is no longer valid falls back to the default
var form3 = new PlanPickerForm(new[] { "（不套用样板）" }, new[] { "一层" }, null, new PickerMemory(Array.Empty<string>(), Array.Empty<string>(), "{样板}"));
Check(Get<TextBox>(form3, "pattern").Text == "{标高}_{样板}", "invalid remembered pattern falls back to default");
form3.Dispose();

Console.WriteLine(failures.Count == 0 ? "ALL PASS" : $"{failures.Count} FAILED");
Console.WriteLine("screenshots: " + shotDefault + " | " + shotMin);
return failures.Count == 0 ? 0 : 1;
