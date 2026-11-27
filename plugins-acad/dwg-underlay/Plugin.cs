// 天河落 · 套图（AutoCAD 插件）
// 瘦身时去掉了 Revit 导出带进来的建筑 CAD 底图；套图把底图按坐标重新套进管综 DWG：
//   ① 从原图取回：读瘦身前的原图（后台读，不打开），找到「建筑底图-xxx_dwg-<ID>-<视图>」这类块，
//      按原来的插入位置/旋转/缩放套回当前图。默认作为外部参照：底图另存到当前图旁边的「底图」文件夹，管综图本身不变大。
//   ② 套外部 DWG：作为外部参照附着，再对位——按原坐标；自动（和参照里的同一张底图匹配，求旋转/缩放/平移）；或两点对位。
// 只往当前图里加一个外部参照（或块）和一个图层，不动图里已有的东西；撤销（U）可以退回。
using System;
using System.Collections.Generic;
using System.Drawing;
using Color = Autodesk.AutoCAD.Colors.Color;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.Colors;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using AcApp = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace Thl.Acad.Underlay
{
    public class Plugin
    {
        /// <summary>Revit 导出的导入/链接 CAD 块：文件名（点换下划线）+ "-<元素ID>-<视图名>"</summary>
        static readonly Regex CadBlock = new Regex(@"_(dwg|dxf|dgn)-\d+-", RegexOptions.IgnoreCase);
        static string LogPath => Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Tianheluo", "AutoCAD", "logs", "dwg-underlay.log");

        sealed class CadRef { public string Name, Layer; public Matrix3d Xf; public ObjectId Btr; public short LayerColor; public int Count; }

        Editor _ed;
        readonly StringBuilder _log = new StringBuilder();
        void Say(string m)
        {
            _log.AppendLine(m);
            try { _ed?.WriteMessage("\n  " + m); } catch { }
            try { Directory.CreateDirectory(Path.GetDirectoryName(LogPath)); File.AppendAllText(LogPath, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + m + "\n", Encoding.UTF8); } catch { }
        }

        /// <summary>
        /// args（测试/批处理，有就不弹对话框）：
        ///   mode=restore;orig=原图.dwg;as=xref|block;pick=块名正则
        ///   mode=attach;file=底图.dwg;align=none|ref|layers|points;ref=原图.dwg;refpick=块名正则;layers=图层正则;scale=1;p1=x,y;p2=..;q1=..;q2=..
        ///   通用：lock=1;gray=0;saveas=另存路径（测试用）
        /// </summary>
        public string Execute(Document doc, IDictionary<string, string> args)
        {
            if (doc == null) return "套图：请先打开要套底图的管综 DWG。";
            _ed = doc.Editor;
            var a = args != null && args.ContainsKey("mode") ? new Dictionary<string, string>(args, StringComparer.OrdinalIgnoreCase) : null;
            bool auto = a != null;
            if (!auto && Ui.IsConsole) return "套图：无界面运行时需要参数（mode=restore|attach;...）。";
            if (!auto && (a = Dialog(doc)) == null) return "套图：已取消。";
            string result;
            using (doc.LockDocument())
            {
                result = Args.S(a, "mode") == "restore" ? Restore(doc, a) : Attach(doc, a);
                var saveas = Args.S(a, "saveas");
                if (saveas.Length > 0) { doc.Database.SaveAs(saveas, DwgVersion.Current); Say("已另存：" + saveas); }
            }
            try { _ed.Regen(); } catch { }
            if (!auto) MessageBox.Show(result, "天河落 · 套图");
            return result;
        }

        // ---------------------------------------------------------------- ① 从原图取回
        static List<CadRef> FindCadRefs(Database db)
        {
            var res = new List<CadRef>();
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
                var brClass = RXObject.GetClass(typeof(BlockReference));
                foreach (ObjectId id in ms)
                {
                    if (!id.ObjectClass.IsDerivedFrom(brClass)) continue;
                    var br = (BlockReference)tr.GetObject(id, OpenMode.ForRead);
                    var b = (BlockTableRecord)tr.GetObject(br.BlockTableRecord, OpenMode.ForRead);
                    if (b.IsLayout || !CadBlock.IsMatch(b.Name)) continue;
                    var lay = (LayerTableRecord)tr.GetObject(br.LayerId, OpenMode.ForRead);
                    int n = 0; foreach (ObjectId _ in b) n++;
                    res.Add(new CadRef { Name = b.Name, Layer = br.Layer, Xf = br.BlockTransform, Btr = b.ObjectId, LayerColor = lay.Color.ColorIndex, Count = n });
                }
                tr.Commit();
            }
            return res;
        }

        /// <summary>块名「建筑底图-20261024_dwg-13091535-视图」→ 文件名「建筑底图-20261024」</summary>
        static string ShortName(string blockName)
        {
            var m = CadBlock.Match(blockName);
            var s = m.Success ? blockName.Substring(0, m.Index) : blockName;
            foreach (var c in Path.GetInvalidFileNameChars()) s = s.Replace(c, '_');
            return s.Length > 0 ? s : "底图";
        }

        string Restore(Document doc, Dictionary<string, string> a)
        {
            var orig = Args.S(a, "orig");
            if (!File.Exists(orig)) return "套图：找不到原图 " + orig;
            bool asXref = Args.S(a, "as", "xref") != "block";
            var pick = new Regex(Args.S(a, "pick", "."), RegexOptions.IgnoreCase);
            var t0 = DateTime.Now;
            Say("读取原图（后台，不打开）：" + orig);
            var db = doc.Database;
            var lines = new List<string>();
            using (var side = new SideDb(orig, false))
            {
                var refs = FindCadRefs(side.Db).Where(r => pick.IsMatch(r.Name)).ToList();
                if (refs.Count == 0) return "套图：原图里没有找到导入的 CAD 底图块（块名形如「xxx_dwg-<ID>-<视图>」）。";
                var hostDir = File.Exists(db.Filename) ? Path.GetDirectoryName(db.Filename) : Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
                foreach (var r in refs)
                {
                    var shortName = ShortName(r.Name);
                    using (var w = side.Db.Wblock(r.Btr))
                    {
                        ObjectId btrId;
                        if (asXref)
                        {
                            var dir = Path.Combine(hostDir, "底图");
                            Directory.CreateDirectory(dir);
                            var file = Path.Combine(dir, shortName + ".dwg");
                            w.SaveAs(file, DwgVersion.AC1032);
                            Say($"底图另存：{file}（{new FileInfo(file).Length / 1048576.0:0.0} MB，{r.Count} 个图元）");
                            btrId = AttachXref(db, file, shortName, hostDir);
                        }
                        else
                        {
                            btrId = db.Insert(UniqueBlockName(db, shortName), w, false);
                        }
                        PlaceRef(db, btrId, r.Xf, r.Layer, r.LayerColor, a);
                        var pos = r.Xf.CoordinateSystem3d.Origin;
                        lines.Add($"{shortName}：{(asXref ? "外部参照" : "块")}，插入点 ({pos.X:0.##}, {pos.Y:0.##})，缩放 {r.Xf.CoordinateSystem3d.Xaxis.Length:0.######}，旋转 {Math.Atan2(r.Xf.CoordinateSystem3d.Xaxis.Y, r.Xf.CoordinateSystem3d.Xaxis.X) * 180 / Math.PI:0.####}°，图层 {r.Layer}");
                    }
                }
            }
            var msg = $"套图完成（按原图位置，{(int)(DateTime.Now - t0).TotalSeconds} 秒）：\n" + string.Join("\n", lines);
            Say(msg);
            return msg;
        }

        static string UniqueBlockName(Database db, string name)
        {
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                var n = name; int i = 2;
                while (bt.Has(n)) n = name + "_" + i++;
                tr.Commit();
                return n;
            }
        }

        /// <summary>附着外部参照（覆盖型：别人再参照这张管综图时不会把底图连带进去）。同名已存在时换个名字。和管综图同盘时存相对路径。</summary>
        static ObjectId AttachXref(Database db, string file, string name, string hostDir)
        {
            var id = db.OverlayXref(file, UniqueBlockName(db, name));
            try
            {
                if (File.Exists(db.Filename))
                {
                    var rel = MakeRelative(hostDir, file);
                    if (rel != null)
                        using (var tr = db.TransactionManager.StartTransaction())
                        {
                            var b = (BlockTableRecord)tr.GetObject(id, OpenMode.ForWrite);
                            b.PathName = rel;
                            tr.Commit();
                        }
                }
            }
            catch { }
            return id;
        }

        static string MakeRelative(string dir, string file)
        {
            var d = Path.GetFullPath(dir).TrimEnd('\\') + "\\"; var f = Path.GetFullPath(file);
            return f.StartsWith(d, StringComparison.OrdinalIgnoreCase) ? ".\\" + f.Substring(d.Length) : null;
        }

        /// <summary>模型空间里放一个引用（按 xf），放在 layer 图层上（没有就建，颜色 color；可锁定、灰显）。</summary>
        static ObjectId PlaceRef(Database db, ObjectId btrId, Matrix3d xf, string layer, short color, IDictionary<string, string> a)
        {
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var lt = (LayerTable)tr.GetObject(db.LayerTableId, OpenMode.ForRead);
                ObjectId layId;
                if (lt.Has(layer)) layId = lt[layer];
                else
                {
                    lt.UpgradeOpen();
                    var l = new LayerTableRecord { Name = layer, Color = Color.FromColorIndex(ColorMethod.ByAci, color <= 0 || color > 255 ? (short)7 : color) };
                    layId = lt.Add(l); tr.AddNewlyCreatedDBObject(l, true);
                }
                var lay = (LayerTableRecord)tr.GetObject(layId, OpenMode.ForWrite);
                if (Args.B(a, "gray", false)) lay.Color = Color.FromColorIndex(ColorMethod.ByAci, 8);
                var ms = (BlockTableRecord)tr.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForWrite);
                var br = new BlockReference(Point3d.Origin, btrId);
                br.SetDatabaseDefaults(db);
                br.BlockTransform = xf;
                br.LayerId = layId;
                var id = ms.AppendEntity(br); tr.AddNewlyCreatedDBObject(br, true);
                // 底图放到最底下，不挡管线
                try
                {
                    var dot = (DrawOrderTable)tr.GetObject(ms.DrawOrderTableId, OpenMode.ForWrite);
                    dot.MoveToBottom(new ObjectIdCollection { id });
                }
                catch { }
                if (Args.B(a, "lock", true)) lay.IsLocked = true;
                tr.Commit();
                return id;
            }
        }

        // ---------------------------------------------------------------- ② 套外部 DWG + 对位
        string Attach(Document doc, Dictionary<string, string> a)
        {
            var file = Args.S(a, "file");
            if (!File.Exists(file)) return "套图：找不到底图 " + file;
            var db = doc.Database;
            var align = Args.S(a, "align", "none");
            bool allowScale = Args.B(a, "scale", true);
            var hostDir = File.Exists(db.Filename) ? Path.GetDirectoryName(db.Filename) : Path.GetDirectoryName(file);
            var t0 = DateTime.Now;
            AlignResult sol = null;
            string how = "按原坐标";
            if (align == "ref" || align == "layers")
            {
                Say("读取底图里的直线：" + file);
                List<Seg> A;
                using (var side = new SideDb(file, false)) A = Segments(side.Db, null, Matrix3d.Identity);
                List<Seg> B;
                if (align == "ref")
                {
                    var refPath = Args.S(a, "ref");
                    if (!File.Exists(refPath)) return "套图：找不到参照原图 " + refPath;
                    Say("读取参照原图里的底图：" + refPath);
                    using (var side = new SideDb(refPath, false))
                    {
                        var refs = FindCadRefs(side.Db);
                        var pat = Args.S(a, "refpick");
                        var key = Path.GetFileNameWithoutExtension(file);
                        var r = (pat.Length > 0 ? refs.Where(x => Regex.IsMatch(x.Name, pat, RegexOptions.IgnoreCase)) : refs.Where(x => ShortName(x.Name).Equals(key, StringComparison.OrdinalIgnoreCase)))
                                .Concat(refs.OrderByDescending(x => x.Count)).FirstOrDefault();
                        if (r == null) return "套图：参照原图里没有 CAD 底图块，无法自动对位。";
                        Say($"参照：{r.Name}（{r.Count} 个图元）");
                        B = Segments(side.Db, r.Btr, r.Xf);
                    }
                    how = "自动对位（参照原图里的底图）";
                }
                else
                {
                    var pat = new Regex(Args.S(a, "layers", "GRID|AXIS|轴|DOTE"), RegexOptions.IgnoreCase);
                    B = Segments(db, null, Matrix3d.Identity, pat);
                    how = "自动对位（参照当前图图层 " + pat + "）";
                }
                Say($"直线：底图 {A.Count} 条，参照 {B.Count} 条");
                sol = Aligner.Solve(A, B, allowScale, Say);
                if (sol == null || sol.Matched < 8 || sol.Ratio < 0.05)
                    return "套图：自动对位没有找到可靠的解" + (sol != null ? "（" + sol.Describe() + "）" : "") + "，底图没有套入。可以改用两点对位。";
            }
            else if (align == "points")
            {
                double[] P(string k) { var s = Args.S(a, k).Split(','); return new[] { double.Parse(s[0], Args.IC), double.Parse(s[1], Args.IC) }; }
                double[] p1, p2, q1, q2;
                if (a.ContainsKey("p1")) { p1 = P("p1"); p2 = P("p2"); q1 = P("q1"); q2 = P("q2"); }
                else
                {
                    // 交互：先按原坐标附着，让用户在图上点底图上的两点和管综图上对应的两点
                    var idTmp = AttachXref(db, file, Path.GetFileNameWithoutExtension(file), hostDir);
                    var refTmp = PlaceRef(db, idTmp, Matrix3d.Identity, "THL-底图", 8, new Dictionary<string, string> { ["lock"] = "0" });
                    var pts = PickPoints(doc);
                    if (pts == null)
                    {
                        using (var tr = db.TransactionManager.StartTransaction()) { tr.GetObject(refTmp, OpenMode.ForWrite).Erase(); tr.Commit(); }
                        try { db.DetachXref(idTmp); } catch { }
                        return "套图：已取消两点对位。";
                    }
                    p1 = pts[0]; q1 = pts[1]; p2 = pts[2]; q2 = pts[3];
                    sol = Aligner.TwoPoint(p1[0], p1[1], p2[0], p2[1], q1[0], q1[1], q2[0], q2[1], allowScale);
                    using (var tr = db.TransactionManager.StartTransaction())
                    {
                        var br = (BlockReference)tr.GetObject(refTmp, OpenMode.ForWrite);
                        br.BlockTransform = ToMatrix(sol);
                        var lay = (LayerTableRecord)tr.GetObject(br.LayerId, OpenMode.ForWrite);
                        if (Args.B(a, "lock", true)) lay.IsLocked = true;
                        tr.Commit();
                    }
                    var m2 = "套图完成（两点对位）：" + Path.GetFileName(file) + "\n" + sol.Describe();
                    Say(m2); return m2;
                }
                sol = Aligner.TwoPoint(p1[0], p1[1], p2[0], p2[1], q1[0], q1[1], q2[0], q2[1], allowScale);
                how = "两点对位";
            }
            var xf = sol == null ? Matrix3d.Identity : ToMatrix(sol);
            var id = AttachXref(db, file, Path.GetFileNameWithoutExtension(file), hostDir);
            PlaceRef(db, id, xf, "THL-底图", 8, a);
            var msg = $"套图完成（{how}，{(int)(DateTime.Now - t0).TotalSeconds} 秒）：{Path.GetFileName(file)} 作为外部参照套入，图层 THL-底图" + (sol != null ? "\n" + sol.Describe() : "");
            Say(msg);
            return msg;
        }

        static Matrix3d ToMatrix(AlignResult r) =>
            Matrix3d.Displacement(new Vector3d(r.Dx, r.Dy, 0)) * Matrix3d.Rotation(r.Theta, Vector3d.ZAxis, Point3d.Origin) * Matrix3d.Scaling(r.Scale, Point3d.Origin);

        double[][] PickPoints(Document doc)
        {
            var ed = doc.Editor;
            var res = new double[4][];
            string[] prompts = { "\n底图上的第 1 点", "\n管综图上对应的第 1 点", "\n底图上的第 2 点", "\n管综图上对应的第 2 点" };
            for (int i = 0; i < 4; i++)
            {
                var o = new PromptPointOptions(prompts[i]);
                if (i % 2 == 1) { o.UseBasePoint = true; o.BasePoint = new Point3d(res[i - 1][0], res[i - 1][1], 0); }
                var r = ed.GetPoint(o);
                if (r.Status != PromptStatus.OK) return null;
                var p = r.Value.TransformBy(ed.CurrentUserCoordinateSystem);
                res[i] = new[] { p.X, p.Y };
            }
            return res;
        }

        /// <summary>取直线段（世界坐标）：模型空间（或指定块）里的直线和多段线直线段，展开嵌套块（最多 4 层，外部参照不进）。layerPat：只要这些图层。</summary>
        static List<Seg> Segments(Database db, ObjectId? blockId, Matrix3d xf, Regex layerPat = null)
        {
            var res = new List<Seg>();
            using (var tr = db.TransactionManager.StartTransaction())
            {
                var root = (BlockTableRecord)tr.GetObject(blockId ?? SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
                void Walk(BlockTableRecord b, Matrix3d m, int depth)
                {
                    foreach (ObjectId id in b)
                    {
                        if (res.Count > 3_000_000) return;
                        var e = tr.GetObject(id, OpenMode.ForRead) as Entity;
                        if (e == null) continue;
                        if (e is BlockReference br)
                        {
                            if (depth >= 4) continue;
                            var bb = (BlockTableRecord)tr.GetObject(br.BlockTableRecord, OpenMode.ForRead);
                            if (bb.IsFromExternalReference || bb.IsDependent || bb.IsLayout) continue;
                            Walk(bb, m * br.BlockTransform, depth + 1);
                            continue;
                        }
                        if (layerPat != null && !layerPat.IsMatch(e.Layer)) continue;
                        if (e is Line l) Add(l.StartPoint.TransformBy(m), l.EndPoint.TransformBy(m));
                        else if (e is Polyline pl)
                        {
                            int nv = pl.NumberOfVertices;
                            for (int i = 0; i < (pl.Closed ? nv : nv - 1); i++)
                                if (Math.Abs(pl.GetBulgeAt(i)) < 1e-9) Add(pl.GetPoint3dAt(i).TransformBy(m), pl.GetPoint3dAt((i + 1) % nv).TransformBy(m));
                        }
                    }
                }
                void Add(Point3d p, Point3d q) { var s = new Seg(p.X, p.Y, q.X, q.Y); if (s.Len > 1e-6 && !double.IsNaN(s.Len)) res.Add(s); }
                Walk(root, xf, 0);
                tr.Commit();
            }
            return res;
        }

        // ---------------------------------------------------------------- 对话框
        Dictionary<string, string> Dialog(Document doc)
        {
            var f = Ui.NewForm("天河落 · 套图", 600, 500);
            var tt = new ToolTip();
            var rRestore = new RadioButton { Text = "① 从瘦身前的原图取回底图（按原来的位置，推荐）", Left = 14, Top = 12, AutoSize = true, Checked = true, Font = new System.Drawing.Font(f.Font, FontStyle.Bold) };
            f.Controls.Add(rRestore);
            Ui.Label(f, "原图（Revit 导出、还带着建筑底图的 DWG）：", 34, 40);
            var tOrig = new TextBox { Left = 34, Top = 60, Width = 450 }; f.Controls.Add(tOrig);
            var bOrig = Ui.Btn(f, "浏览…", 492, 58);
            var rXref = new RadioButton { Text = "作为外部参照（底图另存到「底图」文件夹，管综图不变大）", Left = 34, Top = 92, AutoSize = true, Checked = true };
            var rBlock = new RadioButton { Text = "插入为块（管综图会变大）", Left = 34, Top = 116, AutoSize = true };
            f.Controls.Add(rXref); f.Controls.Add(rBlock);

            var rAttach = new RadioButton { Text = "② 套外部建筑 DWG（作为外部参照）", Left = 14, Top = 156, AutoSize = true, Font = new System.Drawing.Font(f.Font, FontStyle.Bold) };
            f.Controls.Add(rAttach);
            Ui.Label(f, "建筑底图 DWG：", 34, 184);
            var tFile = new TextBox { Left = 34, Top = 204, Width = 450 }; f.Controls.Add(tFile);
            var bFile = Ui.Btn(f, "浏览…", 492, 202);
            var cbAlign = new ComboBox { Left = 34, Top = 240, Width = 450, DropDownStyle = ComboBoxStyle.DropDownList };
            cbAlign.Items.AddRange(new object[] {
                "按原坐标（底图和管综图是同一套坐标）",
                "自动对位：和上面「原图」里的同一张底图匹配（求旋转、缩放、平移）",
                "自动对位：和当前图里的轴网图层匹配",
                "两点对位：在图上点底图两点 → 管综图对应两点" });
            cbAlign.SelectedIndex = 1; f.Controls.Add(cbAlign);
            var cScale = Ui.Check(f, "允许缩放（Revit 导入 CAD 时被缩放过就要勾）", 34, 274, true);
            Ui.Label(f, "轴网图层关键字：", 34, 304);
            var tLayers = new TextBox { Left = 150, Top = 301, Width = 200, Text = "GRID|AXIS|轴|DOTE" }; f.Controls.Add(tLayers);

            var cLock = Ui.Check(f, "锁定底图图层（不会误选、误删）", 14, 346, true);
            var cGray = Ui.Check(f, "底图图层改成灰色（8 号色），突出管线", 14, 372, false);
            Ui.Label(f, "只往当前图里加一个外部参照（或块）和一个图层，图里已有的东西不动；不满意可以撤销（U）。", 14, 404, 570, SystemColors.GrayText);

            void Sync()
            {
                bool r1 = rRestore.Checked;
                foreach (Control c in new Control[] { tOrig, bOrig, rXref, rBlock }) c.Enabled = r1 || (cbAlign.SelectedIndex == 1 && (c == tOrig || c == bOrig));
                foreach (Control c in new Control[] { tFile, bFile, cbAlign, cScale, tLayers }) c.Enabled = !r1;
                tLayers.Enabled = !r1 && cbAlign.SelectedIndex == 2;
                cScale.Enabled = !r1 && cbAlign.SelectedIndex != 0;
            }
            rRestore.CheckedChanged += (_, __) => Sync(); rAttach.CheckedChanged += (_, __) => Sync(); cbAlign.SelectedIndexChanged += (_, __) => Sync();
            // ① 和 ② 是一组，外部参照/块是另一组：用 Tag 手动互斥
            rRestore.Click += (_, __) => rAttach.Checked = false; rAttach.Click += (_, __) => rRestore.Checked = false;
            rXref.Click += (_, __) => rBlock.Checked = false; rBlock.Click += (_, __) => rXref.Checked = false;
            rRestore.AutoCheck = rAttach.AutoCheck = rXref.AutoCheck = rBlock.AutoCheck = false;
            rRestore.Click += (_, __) => { rRestore.Checked = true; Sync(); }; rAttach.Click += (_, __) => { rAttach.Checked = true; Sync(); };
            rXref.Click += (_, __) => rXref.Checked = true; rBlock.Click += (_, __) => rBlock.Checked = true;
            string Pick(string title) { using (var d = new OpenFileDialog { Filter = "DWG 文件 (*.dwg)|*.dwg", Title = title }) return d.ShowDialog(f) == DialogResult.OK ? d.FileName : null; }
            bOrig.Click += (_, __) => { var p = Pick("选择瘦身前的原图"); if (p != null) tOrig.Text = p; };
            bFile.Click += (_, __) => { var p = Pick("选择建筑底图 DWG"); if (p != null) tFile.Text = p; };
            // 默认原图：当前图名去掉「_瘦身」
            try
            {
                var cur = doc.Database.Filename;
                if (cur.EndsWith("_瘦身.dwg", StringComparison.OrdinalIgnoreCase)) { var o = cur.Substring(0, cur.Length - "_瘦身.dwg".Length) + ".dwg"; if (File.Exists(o)) tOrig.Text = o; }
            }
            catch { }
            Sync();

            var go = Ui.Btn(f, "套图", 400, 456, 90); var cancel = Ui.Btn(f, "取消", 498, 456, 90);
            go.DialogResult = DialogResult.OK; cancel.DialogResult = DialogResult.Cancel; f.AcceptButton = go; f.CancelButton = cancel;
            go.Click += (_, __) =>
            {
                string err = null;
                if (rRestore.Checked && !File.Exists(tOrig.Text.Trim())) err = "请选择瘦身前的原图。";
                if (rAttach.Checked && !File.Exists(tFile.Text.Trim())) err = "请选择建筑底图 DWG。";
                if (rAttach.Checked && cbAlign.SelectedIndex == 1 && !File.Exists(tOrig.Text.Trim())) err = "自动对位要参照原图：请在上面选择原图。";
                if (err != null) { MessageBox.Show(f, err, "套图"); f.DialogResult = DialogResult.None; }
            };
            if (Ui.Show(f, "dwg-underlay") != DialogResult.OK) return null;
            var a = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                ["lock"] = cLock.Checked ? "1" : "0", ["gray"] = cGray.Checked ? "1" : "0",
            };
            if (rRestore.Checked) { a["mode"] = "restore"; a["orig"] = tOrig.Text.Trim(); a["as"] = rBlock.Checked ? "block" : "xref"; }
            else
            {
                a["mode"] = "attach"; a["file"] = tFile.Text.Trim(); a["scale"] = cScale.Checked ? "1" : "0";
                a["align"] = new[] { "none", "ref", "layers", "points" }[cbAlign.SelectedIndex];
                a["ref"] = tOrig.Text.Trim(); a["layers"] = tLayers.Text.Trim();
            }
            return a;
        }
    }
}
