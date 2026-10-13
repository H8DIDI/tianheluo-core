// UI smoke test for the picker window (no Revit): screenshot to the path given as argument.
using System.Drawing.Imaging;
using KaifaTools.PlanBatch;

var types = new[] { "楼层平面", "PV建模视图", "暖通建模视图", "电照建模视图", "楼层平面 2", "CDA建模视图", "暖通出图视图", "楼层平面1", "楼层平面 3", "暖通出图视图1", "暖通出图视图1", "暖通出图视图11", "暖通出图视图111" };
var levels = new[] { "一层", "二层", "三层", "四层" };
var form = new PlanPickerForm(types, levels);
Application.EnableVisualStyles();
form.Shown += (_, _) =>
{
    // tick the same items as the reference screenshot
    foreach (var i in new[] { 1, 2, 3, 5, 6 }) form.Controls.OfType<GroupBox>().First().Controls.OfType<CheckedListBox>().First().SetItemChecked(i, true);
    foreach (var i in new[] { 0, 1, 2, 3 }) form.Controls.OfType<GroupBox>().Skip(1).First().Controls.OfType<CheckedListBox>().First().SetItemChecked(i, true);
    var t = new System.Windows.Forms.Timer { Interval = 800 };
    t.Tick += (_, _) =>
    {
        t.Stop();
        using var bmp = new Bitmap(form.Width, form.Height);
        form.DrawToBitmap(bmp, new Rectangle(0, 0, form.Width, form.Height));
        var p = args[0];
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(p))!);
        bmp.Save(p, ImageFormat.Png);
        Console.WriteLine("saved");
        form.Close();
    };
    t.Start();
};
Application.Run(form);
