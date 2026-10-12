using THL.Plugins.ScheduleExcel;
int fail = 0;
void Check(string n, bool ok, string x = "") { Console.WriteLine((ok ? "PASS " : "FAIL ") + n + (ok ? "" : "  " + x)); if (!ok) fail++; }
var dir = Path.Combine(Path.GetTempPath(), "thl-xlsx-test"); Directory.CreateDirectory(dir);

// 1) 写出
var a = new Sheet("墙 明细表");
a.Rows.Add(new() { "ElementId", "类型", "标记", "长度" });
a.Rows.Add(new() { "RO", "RO", "RW", "RO" });
a.Rows.Add(new() { "123456", "常规 - 200mm", "W-01", "3500" });
a.Rows.Add(new() { "123457", "常规 - 200mm", "", "4200" });     // 空单元格
a.Rows.Add(new() { "123458", "含特殊字符 <&>\"'", "多行\n文字", "1,234.5" });
var meta = new Sheet("__THL") { Hidden = true };
meta.Rows.Add(new() { "SheetName", "ScheduleName", "ScheduleUniqueId" });
meta.Rows.Add(new() { "墙 明细表", "墙明细表", "abc-123" });
var path = Path.Combine(dir, "out.xlsx");
Xlsx.Write(path, new[] { a, meta });
Check("文件已生成", File.Exists(path) && new FileInfo(path).Length > 500);

// 2) 自己读回来
var back = Xlsx.Read(path);
Check("读回 2 张表", back.Count == 2);
var b0 = back[0];
Check("表名一致", b0.Name == "墙 明细表");
Check("单元格逐个一致", a.Rows.Select((r, i) => r.Select((v, j) => (b0.Rows[i].Count > j ? b0.Rows[i][j] : "") == v).All(x => x)).All(x => x));
Check("多行/特殊字符保留", b0.Rows[4][1] == "含特殊字符 <&>\"'" && b0.Rows[4][2] == "多行\n文字");
Check("隐藏表识别", back[1].Hidden && back[1].Name == "__THL");

// 3) 让 Python 的 openpyxl 读取我写的文件（独立实现，等同于 Excel 兼容性检查）
Console.WriteLine("OUT=" + path);
// 4) 读 openpyxl 生成的、带 sharedStrings 的“真实 Excel 风格”文件
var ext = Path.Combine(dir, "from_openpyxl.xlsx");
if (File.Exists(ext))
{
    var s = Xlsx.Read(ext)[0];
    Check("读取 openpyxl 文件（sharedStrings + 数字）", s.Rows[0][0] == "ElementId" && s.Rows[2][0] == "42" && s.Rows[2][2] == "新标记-9", string.Join("|", s.Rows[2]));
}
Console.WriteLine(fail == 0 ? "ALL PASS" : fail + " FAILED");
return fail;
