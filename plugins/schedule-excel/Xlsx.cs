using System.IO.Compression;
using System.Text;
using System.Xml.Linq;

namespace THL.Plugins.ScheduleExcel;

/// <summary>一张工作表：行列都是字符串。第一行从下标 0 开始。</summary>
public sealed class Sheet
{
    public string Name { get; }
    public bool Hidden { get; set; }
    public List<List<string>> Rows { get; } = new();
    public Sheet(string name) { Name = name; }
}

/// <summary>
/// 极简 .xlsx 读写（只处理文本/数字单元格），不依赖任何第三方库。
/// 写出：所有值按文本写入（inlineStr），不会被 Excel 改写格式，导回时能原样比对。
/// 读取：支持 Excel 保存后的 sharedStrings、inlineStr 和数字。
/// </summary>
public static class Xlsx
{
    static readonly XNamespace Main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main";
    static readonly XNamespace Rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
    static readonly XNamespace PkgRel = "http://schemas.openxmlformats.org/package/2006/relationships";

    // ------------------------------------------------------------ 写
    public static void Write(string path, IReadOnlyList<Sheet> sheets)
    {
        if (File.Exists(path)) File.Delete(path);
        using var zip = ZipFile.Open(path, ZipArchiveMode.Create);

        Put(zip, "[Content_Types].xml", new XDocument(new XElement(XName.Get("Types", "http://schemas.openxmlformats.org/package/2006/content-types"),
            new XElement(XName.Get("Default", "http://schemas.openxmlformats.org/package/2006/content-types"), new XAttribute("Extension", "rels"), new XAttribute("ContentType", "application/vnd.openxmlformats-package.relationships+xml")),
            new XElement(XName.Get("Default", "http://schemas.openxmlformats.org/package/2006/content-types"), new XAttribute("Extension", "xml"), new XAttribute("ContentType", "application/xml")),
            new XElement(XName.Get("Override", "http://schemas.openxmlformats.org/package/2006/content-types"), new XAttribute("PartName", "/xl/workbook.xml"), new XAttribute("ContentType", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")),
            sheets.Select((s, i) => new XElement(XName.Get("Override", "http://schemas.openxmlformats.org/package/2006/content-types"), new XAttribute("PartName", $"/xl/worksheets/sheet{i + 1}.xml"), new XAttribute("ContentType", "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"))))));

        Put(zip, "_rels/.rels", new XDocument(new XElement(PkgRel + "Relationships",
            new XElement(PkgRel + "Relationship", new XAttribute("Id", "rId1"), new XAttribute("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"), new XAttribute("Target", "xl/workbook.xml")))));

        Put(zip, "xl/workbook.xml", new XDocument(new XElement(Main + "workbook", new XAttribute(XNamespace.Xmlns + "r", Rel.NamespaceName),
            new XElement(Main + "sheets", sheets.Select((s, i) => new XElement(Main + "sheet",
                new XAttribute("name", SafeSheetName(s.Name)), new XAttribute("sheetId", i + 1),
                s.Hidden ? new XAttribute("state", "hidden") : null,
                new XAttribute(Rel + "id", $"rId{i + 1}")))))));

        Put(zip, "xl/_rels/workbook.xml.rels", new XDocument(new XElement(PkgRel + "Relationships",
            sheets.Select((s, i) => new XElement(PkgRel + "Relationship", new XAttribute("Id", $"rId{i + 1}"),
                new XAttribute("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"),
                new XAttribute("Target", $"worksheets/sheet{i + 1}.xml"))))));

        for (int i = 0; i < sheets.Count; i++)
            Put(zip, $"xl/worksheets/sheet{i + 1}.xml", SheetXml(sheets[i]));
    }

    static XDocument SheetXml(Sheet s)
    {
        var data = new XElement(Main + "sheetData");
        for (int r = 0; r < s.Rows.Count; r++)
        {
            var row = new XElement(Main + "row", new XAttribute("r", r + 1));
            for (int c = 0; c < s.Rows[r].Count; c++)
            {
                var v = s.Rows[r][c];
                if (string.IsNullOrEmpty(v)) continue;
                row.Add(new XElement(Main + "c", new XAttribute("r", ColName(c) + (r + 1)), new XAttribute("t", "inlineStr"),
                    new XElement(Main + "is", new XElement(Main + "t", new XAttribute(XNamespace.Xml + "space", "preserve"), Clean(v)))));
            }
            data.Add(row);
        }
        // 第一行冻结（表头）+ 按内容给列一个合理的宽度
        int cols = s.Rows.Count == 0 ? 0 : s.Rows.Max(r => r.Count);
        var colsEl = new XElement(Main + "cols", Enumerable.Range(0, cols).Select(c =>
        {
            int w = s.Rows.Take(200).Select(r => c < r.Count ? DisplayWidth(r[c]) : 0).DefaultIfEmpty(8).Max();
            return new XElement(Main + "col", new XAttribute("min", c + 1), new XAttribute("max", c + 1), new XAttribute("width", Math.Clamp(w + 2, 8, 60)), new XAttribute("customWidth", 1));
        }));
        return new XDocument(new XElement(Main + "worksheet",
            new XElement(Main + "sheetViews", new XElement(Main + "sheetView", new XAttribute("workbookViewId", 0),
                new XElement(Main + "pane", new XAttribute("ySplit", 1), new XAttribute("topLeftCell", "A2"), new XAttribute("activePane", "bottomLeft"), new XAttribute("state", "frozen")))),
            cols > 0 ? colsEl : null, data));
    }

    static int DisplayWidth(string s) => s.Sum(ch => ch > 255 ? 2 : 1);

    /// <summary>去掉 XML 不允许的控制字符。</summary>
    static string Clean(string s) => new(s.Where(ch => ch is '\t' or '\n' or '\r' || ch >= 0x20).ToArray());

    public static string SafeSheetName(string n)
    {
        var bad = new[] { '\\', '/', '?', '*', '[', ']', ':' };
        var s = new string(n.Where(ch => !bad.Contains(ch)).ToArray()).Trim();
        if (s.Length == 0) s = "Sheet";
        return s.Length > 31 ? s[..31] : s;
    }

    static void Put(ZipArchive zip, string name, XDocument doc)
    {
        var e = zip.CreateEntry(name, CompressionLevel.Optimal);
        using var s = e.Open();
        using var w = new StreamWriter(s, new UTF8Encoding(false));
        doc.Save(w);
    }

    static string ColName(int c)
    {
        var sb = new StringBuilder();
        for (c++; c > 0; c = (c - 1) / 26) sb.Insert(0, (char)('A' + (c - 1) % 26));
        return sb.ToString();
    }

    // ------------------------------------------------------------ 读
    public static List<Sheet> Read(string path)
    {
        using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite); // Excel 打开着也能读
        using var zip = new ZipArchive(fs, ZipArchiveMode.Read);

        var shared = new List<string>();
        var ssEntry = zip.GetEntry("xl/sharedStrings.xml");
        if (ssEntry != null)
            foreach (var si in Load(ssEntry).Root!.Elements(Main + "si"))
                shared.Add(string.Concat(si.Descendants(Main + "t").Select(t => t.Value)));

        var wb = Load(zip.GetEntry("xl/workbook.xml") ?? throw new InvalidDataException("不是有效的 Excel 文件（缺少 workbook）"));
        var rels = Load(zip.GetEntry("xl/_rels/workbook.xml.rels")!).Root!.Elements(PkgRel + "Relationship")
            .ToDictionary(e => (string)e.Attribute("Id")!, e => (string)e.Attribute("Target")!);

        var result = new List<Sheet>();
        foreach (var sh in wb.Root!.Element(Main + "sheets")!.Elements(Main + "sheet"))
        {
            var rid = (string)sh.Attribute(Rel + "id")!;
            var target = rels[rid].TrimStart('/');
            var entry = zip.GetEntry(target.StartsWith("xl/") ? target : "xl/" + target);
            if (entry == null) continue;
            var sheet = new Sheet((string)sh.Attribute("name")!) { Hidden = (string?)sh.Attribute("state") == "hidden" };
            foreach (var row in Load(entry).Root!.Descendants(Main + "row"))
            {
                int r = (int?)row.Attribute("r") ?? (sheet.Rows.Count + 1);
                while (sheet.Rows.Count < r) sheet.Rows.Add(new List<string>());
                var cells = sheet.Rows[r - 1];
                foreach (var c in row.Elements(Main + "c"))
                {
                    int col = ColIndex((string?)c.Attribute("r") ?? "A1");
                    while (cells.Count <= col) cells.Add("");
                    cells[col] = CellText(c, shared);
                }
            }
            result.Add(sheet);
        }
        return result;
    }

    static string CellText(XElement c, List<string> shared)
    {
        var t = (string?)c.Attribute("t");
        if (t == "inlineStr") return string.Concat(c.Descendants(Main + "t").Select(x => x.Value));
        var v = c.Element(Main + "v")?.Value ?? "";
        if (t == "s" && int.TryParse(v, out var i) && i >= 0 && i < shared.Count) return shared[i];
        if (t == "b") return v == "1" ? "TRUE" : "FALSE";
        return v;
    }

    static XDocument Load(ZipArchiveEntry e) { using var s = e.Open(); return XDocument.Load(s); }

    static int ColIndex(string cellRef)
    {
        int n = 0;
        foreach (var ch in cellRef) { if (!char.IsLetter(ch)) break; n = n * 26 + (char.ToUpperInvariant(ch) - 'A' + 1); }
        return n - 1;
    }
}
