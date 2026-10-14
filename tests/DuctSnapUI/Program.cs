// UI smoke test for 排管贴井内壁: builds the real DuctSnapForm from DuctSnap.dll with sample rows, shows it,
// and saves a screenshot. Revit is not started. Usage: dotnet run -c Release -- [output folder]
using System.Drawing;
using System.Drawing.Imaging;
using System.Windows.Forms;
using KaifaTools.DuctSnap;

internal static class Program
{
[STAThread]
static void Main(string[] args)
{
    Application.EnableVisualStyles();
    string outDir = args.Length > 0 ? args[0] : Environment.CurrentDirectory;
    Directory.CreateDirectory(outDir);

    var rep = new SnapReport { Done = true, BanksScanned = 1262, Seconds = 22.4 };
    void Add(long bank, int end, string bankFam, long well, string wellFam, SnapKind kind, double move, string status,
             string action, string note, string endXyz, string otherXyz, double len, double turn, string cav,
             double sz0, double sz1)
    {
        rep.Rows.Add(new SnapRow
        {
            BankId = bank, EndIndex = end, BankFamily = bankFam, WellId = well, WellFamily = wellFam, Kind = kind,
            MoveMm = move, Status = status, Action = action, Note = note, Checked = status == "可修改",
            EndXyz = endXyz, OtherXyz = otherXyz, LengthMm = len, TurnDeg = turn, CavityText = cav,
            StartZBeforeMm = sz0, StartZAfterMm = sz0, EndZBeforeMm = sz1, EndZAfterMm = sz1,
        });
    }
    const string fam11 = "ELE-11KV 3层6排18孔Φ250排管";
    const string famTel = "TELCO-6层8排48孔Φ100排管";
    Add(8737181, 0, fam11, 8978902, "ELE-EM2-L", SnapKind.Fit, 0, "已贴合", "不动", "端点已经在井内壁上（误差内）",
        "-135120.1, -196559.7, 2600.0", "-111362.0, -196559.7, 3300.0", 23758, -0.01, "X -137145..-135120, Y -197612..-195587", 2600, 3300);
    Add(8737181, 1, fam11, 8978902, "ELE-EM2-L", SnapKind.Fit, 0, "已贴合", "不动", "端点已经在井内壁上（误差内）",
        "-111362.0, -196559.7, 3300.0", "-135120.1, -196559.7, 2600.0", 23758, -0.01, "X -137145..-135120, Y -197612..-195587", 2600, 3300);
    Add(8737137, 1, fam11, 8979011, "ELE-EM1-I", SnapKind.Extend, -87.8, "可修改", "延伸到内壁", "端点离内壁 88 mm，延伸到内壁",
        "547040.1, -182694.2, 2600.0", "517117.1, -182694.2, 4400.0", 30020, 0.00, "X 547128..549428, Y -183485..-180985", 4400, 2600);
    Add(8737144, 0, fam11, 8978973, "ELE-EM2-L", SnapKind.Trim, 227.4, "可修改", "修剪伸入井内的部分", "端点伸入井内 227 mm，修剪回内壁",
        "472578.3, -276465.1, 2226.7", "464699.3, -276465.1, 1301.5", 7880, 0.00, "X 472351..475476, Y -278425..-275300", 2226.7, 1301.5);
    Add(8737231, 1, famTel, 8978936, "ELE-EM2-L", SnapKind.TrimTooFar, 1150, "需人工", "不动", "端点在井的中部（伸入 1150 mm，超过修剪上限）",
        "223077.0, -338105.0, 2400.0", "163077.0, -338105.0, 2400.0", 60000, 0.00, "X 221928..223953, Y -339087..-337062", 2400, 2400);
    Add(8737278, 1, fam11, 8979098, "ELE-EM1-I", SnapKind.Through, 0, "需人工", "不动", "排管穿过这口井，未自动处理",
        "218043.0, -1023175.0, 2800.0", "170000.0, -1023175.0, 2800.0", 46738, 0.00, "X 216506..219630, Y -1024664..-1020464", 2800, 2800);
    Add(8737417, 1, famTel, 8979100, "TELCO-1600(L)x2000(W)", SnapKind.ExtendTooFar, -1200, "需人工", "不动",
        "端点离内壁 1200 mm，超过延伸上限 1000 mm，请手动处理", "98765.0, 12345.0, 3000.0", "120000.0, 12345.0, 3000.0", 21235, 0.00,
        "X 97000..99800, Y 11000..13400", 3000, 3000);
    Add(8737482, 0, fam11, 8979220, "ELV MANHOLE 1500MM X 1500MM", SnapKind.Unrelated, 0, "需人工", "不动",
        "端点在井的墙体里，排管与井内腔不相交；整段都在井内或多出的长度超过上限，需人工确认",
        "-1234.0, 4567.0, 1850.0", "45000.0, 4567.0, 1850.0", 46234, 0.00, "X -2100..-300, Y 3800..5200", 1850, 1850);
    Add(8738118, 0, famTel, 8978965, "TELCO-室内井", SnapKind.ToInnerWall, -800.8, "可修改", "延伸到井的内壁",
        "排管端点已在井外缘，延伸 801 mm 回到井的内壁", "183054.9, -255327.5, 3200.0", "116580.4, -255327.5, 3200.0", 66474, 0.00,
        "X 182805..186430, Y -258712..-255087", 3200, 3200);
    Add(8738118, 1, famTel, 8979035, "ELE-EM1-I", SnapKind.Fit, 0, "已贴合", "不动", "端点已经在井内壁上（误差内）",
        "116580.4, -255327.5, 3200.0", "183054.9, -255327.5, 3200.0", 66474, 0.00, "X 113330..116831, Y -257800..-254825", 3200, 3200);
    Add(8737501, 0, fam11, 8979014, "ELE-EM1-I", SnapKind.Trim, 1500, "可修改", "修剪伸入井内的部分", "端点伸入井内 1500 mm，修剪回内壁",
        "476000.0, -212000.0, 3100.0", "400000.0, -212000.0, 3100.0", 76000, 4.58, "X 473526..476651, Y -213633..-210508", 3100, 3100);

    var form = new KaifaTools.DuctSnap.DuctSnapForm(rep);
    form.StartPosition = FormStartPosition.Manual;
    form.Location = new Point(0, 0);
    form.Size = new Size(1280, 800);
    form.Show();
    for (int i = 0; i < 5; i++) Application.DoEvents();
    string path = Path.Combine(outDir, "duct-snap-window.png");
    using (var bmp = new Bitmap(form.Width, form.Height))
    {
        form.DrawToBitmap(bmp, new Rectangle(0, 0, bmp.Width, bmp.Height));
        bmp.Save(path, ImageFormat.Png);
    }
    var layout = (System.Windows.Forms.TableLayoutPanel)form.Controls[0];
    Console.WriteLine("client " + form.ClientSize + " | rows " + string.Join(", ", layout.GetRowHeights()) + " | grid " + layout.GetControlFromPosition(0, 2)?.Size);
    Console.WriteLine("saved " + path);
    form.Close();
}
}
