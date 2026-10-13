// UI smoke test: shows the review window with fake wells (no Revit needed) and saves a screenshot.
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using KaifaTools.WellDepth;

var rules = new DepthRules();
var wells = new List<WellResult>();
void Add(string mark, string fam, WellSystem sys, WellStatus st, double offset, double? low, double newB, double newD, double? curB, double? curD, string note = "")
{
    wells.Add(new WellResult { Id = wells.Count + 1000, Mark = mark, Family = fam, System = sys, Status = st,
        OffsetMm = offset, TopMm = offset, RefMm = 0, LowestPipeMm = low, NewBottomMm = newB, NewDepthMm = newD,
        CurBottomMm = curB, CurDepthMm = curD, Note = note });
}
Add("L-001", "LV-低压拉线井", WellSystem.Lv, WellStatus.Adjust, 5900, 3600, 3300, 2600, 3300, 2400);
Add("L-002", "LV-低压拉线井", WellSystem.Lv, WellStatus.Ok, 5900, 3600, 3300, 2600, 3300, 2600);
Add("L-003", "LV-低压拉线井", WellSystem.Lv, WellStatus.SyncOnly, 6600, 3900, 3590, 3010, 3600, 3010);
Add("E-011", "ELE-EM2-L", WellSystem.Em, WellStatus.Adjust, 7200, 4700, 4390, 2810, 4300, 2500);
Add("E-012", "ELE-EM1-I", WellSystem.Em, WellStatus.Review, 6300, 1700, 1390, 4910, 1700, 4600, "深度 6100 超过 6000");
Add("T-021", "ELE-TELCO(96WAY)", WellSystem.Telco, WellStatus.Adjust, 5900, 1700, 1390, 4510, 1700, 4200);
Add("T-022", "TELCO-室内井", WellSystem.Telco, WellStatus.NoPipe, 6800, null, 0, 0, 3820, 2980, "400 mm 内没有同专业排管");
Add("V-031", "ELV PIT", WellSystem.Elv, WellStatus.Review, 6050, 2770, 2460, 3590, 3000, 3050, "最低管比次低管还低 2600，疑似异常管；含有未贴井壁的排管(间距320)");
Add("V-032", "ELV-MANHOLE", WellSystem.Elv, WellStatus.Temporary, 5900, null, 0, 0, null, null, "注释含“临时”");
var rows = wells.Select(w => new WellRow(w)).ToList();
rows[1].Write = false;
rows[4].Write = true;                       // user ticked a review row
rows[0].NewDepthInput = "2650";             // user typed a manual depth

Application.SetHighDpiMode(HighDpiMode.SystemAware);
Application.EnableVisualStyles();
var form = new ReviewForm(rows, rules, null, @"C:\Users\PCB\Documents\KaifaTools\井深规则.json",
    new List<string> { "ELE-11KV 2层6孔排管 | ELE-11KV-新 : 3 根" }, 12, "范围：全模型（检查 9 口井）");
form.Shown += (_, _) =>
{
    var t = new System.Windows.Forms.Timer { Interval = 800 };
    t.Tick += (_, _) =>
    {
        t.Stop();
        using var bmp = new Bitmap(form.Width, form.Height);
        form.DrawToBitmap(bmp, new Rectangle(0, 0, form.Width, form.Height));
        var outPath = args.Length > 0 ? args[0] : "review.png";
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(outPath))!);
        bmp.Save(outPath, ImageFormat.Png);
        Console.WriteLine("saved; writeCount=" + rows.Count(r => r.Write && r.CanWrite));
        form.Close();
    };
    t.Start();
};
Application.Run(form);
