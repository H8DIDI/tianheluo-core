# -*- coding: utf-8 -*-
__title__ = '一键井展开图'
__doc__ = '选中一个井，自动生成平面及四面剖面排版到图纸上。出图前可在「规则与预览」里调整比例、裁剪余量、剖面框、视图间距，并实时看到排版效果（本按钮不再自动标注，标注请用「一键井大样标注」）。'

from pyrevit import revit, DB, UI, forms
import System
import math
import traceback


class DimensionFailurePreprocessor(DB.IFailuresPreprocessor):
    """静默处理警告与错误，彻底消除弹窗阻断。

    刻意内联在本脚本内、不依赖扩展的 lib 目录：扩展新增文件需要 pyRevit
    重新扫描后才可导入，放在导入期会让按钮直接报错。
    """

    def PreprocessFailures(self, failuresAccessor):
        for f in failuresAccessor.GetFailureMessages():
            severity = f.GetSeverity()
            if severity == DB.FailureSeverity.Warning:
                failuresAccessor.DeleteWarning(f)
            elif severity == DB.FailureSeverity.Error:
                try:
                    failuresAccessor.ResolveFailure(f)
                except:
                    pass
        return DB.FailureProcessingResult.Continue

# ---------------------------------------------------------------------------
# 可调规则（保存在 Documents\KaifaTools\井展开图规则.json，默认值与原来写死的数值一致）
# 单位都是毫米；“图纸mm”指打印到图纸上的毫米。
# ---------------------------------------------------------------------------
import json as _json
import os as _os

FT_MM = 304.8
RULES_PATH = _os.path.join(
    System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools", "井展开图规则.json")
DEFAULT_RULES = {
    "view_scale": 50,               # 视图比例 1:N
    "crop_margin_mm": 1371.6,       # 平面裁剪框在井外的余量（原 4.5 ft）
    "face_out_mm": 100,             # 剖切面在井外墙外移的距离
    "face_depth_mm": 1100,          # 剖面框沿视线方向的厚度
    "spacing_x_mm": 228.6,          # 左右剖面视图离平面的距离（图纸mm，原 0.75 ft）
    "spacing_y_mm": 167.64,         # 前后剖面视图离平面的距离（图纸mm，原 0.55 ft）
    "sheet_shift_x_mm": 76.2,       # 整组视图相对图框中心的左移（图纸mm，原 0.25 ft）
    "hide_grids_levels": True,      # 隐藏轴网和标高
}
RULE_LABELS = [
    ("view_scale", "视图比例 1:", 1, 1000),
    ("crop_margin_mm", "平面裁剪余量 (mm)", 0, 20000),
    ("face_out_mm", "剖切面外移 (mm)", 0, 5000),
    ("face_depth_mm", "剖面框厚度 (mm)", 1, 20000),
    ("spacing_x_mm", "左右视图间距 (图纸mm)", 0, 2000),
    ("spacing_y_mm", "前后视图间距 (图纸mm)", 0, 2000),
    ("sheet_shift_x_mm", "整组左移 (图纸mm)", -2000, 2000),
]


def load_rules():
    rules = dict(DEFAULT_RULES)
    try:
        if _os.path.exists(RULES_PATH):
            with open(RULES_PATH, "r") as f:
                data = _json.load(f)
            for k in DEFAULT_RULES:
                if k in data:
                    rules[k] = data[k]
    except Exception:
        rules = dict(DEFAULT_RULES)
    return rules


def save_rules(rules):
    folder = _os.path.dirname(RULES_PATH)
    if not _os.path.exists(folder):
        _os.makedirs(folder)
    with open(RULES_PATH, "w") as f:
        _json.dump(rules, f, indent=2, ensure_ascii=False)


def _log(msg):
    # write one line to Documents/KaifaTools/井展开图日志.txt (for diagnosing dialog problems)
    try:
        import datetime as _dt
        folder = _os.path.dirname(RULES_PATH)
        if not _os.path.exists(folder):
            _os.makedirs(folder)
        with open(_os.path.join(folder, "井展开图日志.txt"), "a") as f:
            f.write("[%s] %s\n" % (_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def layout_rects(sample, rules):
    """各视图在图纸上的矩形（图纸mm，平面中心为原点）。预览用，与生成时的几何一致。
    返回 ({名称: (cx, cy, 宽, 高)}, (半宽, 半长, 半高, 剖面框厚) 毫米)。"""
    bb = sample.get_BoundingBox(None)
    hx = (bb.Max.X - bb.Min.X) / 2.0 * FT_MM
    hy = (bb.Max.Y - bb.Min.Y) / 2.0 * FT_MM
    hz = (bb.Max.Z - bb.Min.Z) / 2.0 * FT_MM
    sc = float(rules["view_scale"])
    crop = rules["crop_margin_mm"]
    fo = rules["face_out_mm"]
    fd = rules["face_depth_mm"]
    sx, sy = rules["spacing_x_mm"], rules["spacing_y_mm"]
    rects = {}
    rects["平面"] = (0.0, 0.0, 2 * (hx + crop) / sc, 2 * (hy + crop) / sc)
    rects["南"] = (0.0, -sy, 2 * (hx + fo) / sc, 2 * (hz + fo) / sc)
    rects["北"] = (0.0, sy, 2 * (hx + fo) / sc, 2 * (hz + fo) / sc)
    rects["西"] = (-sx, 0.0, 2 * (hy + fo) / sc, 2 * (hz + fo) / sc)
    rects["东"] = (sx, 0.0, 2 * (hy + fo) / sc, 2 * (hz + fo) / sc)
    return rects, (hx, hy, hz, fd)


def rules_preview_dialog(sample):
    """规则与预览窗口：改参数，右边立刻画出按这些规则排版的图纸效果。
    保存并返回规则；取消返回 None。"""
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System.Windows.Forms import (Form, Label, TextBox, Button, Panel, CheckBox,
                                      DialogResult, FormBorderStyle, FormStartPosition)
    from System.Drawing import Size, Point, Font, Color, Pen, SolidBrush, PointF
    from System.Drawing.Drawing2D import SmoothingMode

    rules = load_rules()
    form = Form()
    form.Text = "一键井展开图 · 规则与预览"
    form.Font = Font("Microsoft YaHei UI", 9.0)
    form.StartPosition = FormStartPosition.CenterScreen
    form.FormBorderStyle = FormBorderStyle.FixedDialog
    form.MaximizeBox = False
    form.ClientSize = Size(980, 600)

    boxes = {}
    y = 16
    for key, label, lo, hi in RULE_LABELS:
        lb = Label()
        lb.Text = label
        lb.Location = Point(16, y + 4)
        lb.AutoSize = True
        tb = TextBox()
        tb.Location = Point(200, y)
        tb.Width = 110
        tb.Text = str(rules[key])
        form.Controls.Add(lb)
        form.Controls.Add(tb)
        boxes[key] = tb
        y += 36
    chk = CheckBox()
    chk.Text = "隐藏轴网和标高"
    chk.Checked = bool(rules["hide_grids_levels"])
    chk.Location = Point(16, y + 4)
    chk.AutoSize = True
    form.Controls.Add(chk)
    y += 40

    note = Label()
    note.Location = Point(16, y)
    note.Size = Size(310, 170)
    note.ForeColor = Color.FromArgb(70, 70, 70)
    form.Controls.Add(note)

    panel = Panel()
    panel.Location = Point(340, 16)
    panel.Size = Size(620, 520)
    panel.BackColor = Color.White
    form.Controls.Add(panel)

    state = {"rects": {}, "warn": "", "rules": dict(DEFAULT_RULES)}

    def read_rules():
        out = dict(DEFAULT_RULES)
        for key, label, lo, hi in RULE_LABELS:
            try:
                v = float(boxes[key].Text.strip())
                if key == "view_scale":
                    v = int(round(v))
                out[key] = max(lo, min(hi, v))
            except Exception:
                out[key] = DEFAULT_RULES[key]
        out["hide_grids_levels"] = bool(chk.Checked)
        return out

    def recompute():
        r = read_rules()
        rects, dims = layout_rects(sample, r)
        names = list(rects.keys())
        warn = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = rects[names[i]], rects[names[j]]
                if abs(a[0] - b[0]) < (a[2] + b[2]) / 2.0 and abs(a[1] - b[1]) < (a[3] + b[3]) / 2.0:
                    warn.append(names[i] + " 与 " + names[j] + " 重叠")
        state["rects"] = rects
        state["warn"] = "；".join(warn)
        state["rules"] = r
        hx, hy, hz, fd = dims
        text = ("预览尺寸取自你选中的井：半宽 %.0f / 半长 %.0f / 半高 %.0f mm\n"
                "视图比例 1:%d，剖面框厚 %.0f mm\n") % (hx, hy, hz, r["view_scale"], fd)
        if warn:
            text += "⚠ " + state["warn"] + "（请加大间距）"
            note.ForeColor = Color.Firebrick
        else:
            text += "✓ 各视图没有重叠，可以生成"
            note.ForeColor = Color.FromArgb(40, 120, 60)
        note.Text = text
        panel.Invalidate()

    def on_paint(sender, e):
        try:
            _paint(e)
        except Exception as ex:
            _log("预览绘制出错：" + str(ex))

    def _paint(e):
        g = e.Graphics
        g.SmoothingMode = SmoothingMode.AntiAlias
        rects = state["rects"]
        if not rects:
            return
        minx = min(r[0] - r[2] / 2.0 for r in rects.values())
        maxx = max(r[0] + r[2] / 2.0 for r in rects.values())
        miny = min(r[1] - r[3] / 2.0 for r in rects.values())
        maxy = max(r[1] + r[3] / 2.0 for r in rects.values())
        W = panel.Width - 40.0
        H = panel.Height - 40.0
        k = min(W / max(maxx - minx, 1.0), H / max(maxy - miny, 1.0))
        ox = 20 + (W - (maxx - minx) * k) / 2.0 - minx * k
        oy = 20 + (H - (maxy - miny) * k) / 2.0 + maxy * k

        bad = set()
        for nm in rects:
            if "与 " + nm in state["warn"] or nm + " 与" in state["warn"]:
                bad.add(nm)
        pen_ok = Pen(Color.FromArgb(47, 140, 255), 2.0)
        pen_bad = Pen(Color.Firebrick, 2.0)
        pen_plan = Pen(Color.FromArgb(139, 92, 246), 2.0)
        brush = SolidBrush(Color.FromArgb(40, 60, 90))
        font = Font("Microsoft YaHei UI", 9.0)
        for nm, (cx, cy, w, h) in rects.items():
            x0 = ox + (cx - w / 2.0) * k
            y0 = oy - (cy + h / 2.0) * k
            pen = pen_plan if nm == "平面" else (pen_bad if nm in bad else pen_ok)
            g.DrawRectangle(pen, x0, y0, w * k, h * k)
            g.DrawString(nm, font, brush, PointF(x0 + 4, y0 + 2))
        px, py = ox, oy
        g.DrawLine(Pen(Color.Gray, 1.0), px - 6, py, px + 6, py)
        g.DrawLine(Pen(Color.Gray, 1.0), px, py - 6, px, py + 6)

    panel.Paint += on_paint

    def changed(sender, args):
        recompute()

    for tb in boxes.values():
        tb.TextChanged += changed
    chk.CheckedChanged += changed

    result = {"rules": None}

    def on_ok(sender, args):
        r = read_rules()
        save_rules(r)
        result["rules"] = r
        _log("规则窗口：用户点了保存并生成，规则=" + str(r))
        form.DialogResult = DialogResult.OK
        form.Close()

    def on_default(sender, args):
        for key, label, lo, hi in RULE_LABELS:
            boxes[key].Text = str(DEFAULT_RULES[key])
        chk.Checked = bool(DEFAULT_RULES["hide_grids_levels"])
        recompute()

    def on_cancel(sender, args):
        _log("规则窗口：用户点了取消")
        form.DialogResult = DialogResult.Cancel
        form.Close()

    ok = Button()
    ok.Text = "保存规则并生成"
    ok.Location = Point(340, 548)
    ok.Size = Size(200, 36)
    ok.Click += on_ok
    cancel = Button()
    cancel.Text = "取消"
    cancel.Location = Point(556, 548)
    cancel.Size = Size(120, 36)
    cancel.Click += on_cancel
    default = Button()
    default.Text = "恢复默认"
    default.Location = Point(16, 548)
    default.Size = Size(120, 36)
    default.Click += on_default
    form.Controls.Add(ok)
    form.Controls.Add(cancel)
    form.Controls.Add(default)
    form.AcceptButton = ok
    form.CancelButton = cancel

    form.TopMost = True
    recompute()
    _log("规则窗口：打开")
    answer = form.ShowDialog()
    _log("规则窗口：关闭，返回 " + str(answer))
    if answer != DialogResult.OK:
        return None
    return result["rules"]


doc = revit.doc
uidoc = revit.uidoc




def main():
    try:
        global doc, uidoc
        doc = revit.doc
        uidoc = revit.uidoc
        # 读取视图中用户的预选构件：选中井后直接生成；未选中时进入批量出图向导
        try:
            selected_ids = list(uidoc.Selection.GetElementIds())
        except:
            selected_ids = []
        # 自动收集模型中的所有通用模型井
        all_model_wells = DB.FilteredElementCollector(doc).OfCategory(
            DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType().ToElements()

        valid_elems = []
        if selected_ids:
            for eid in selected_ids:
                e = doc.GetElement(eid)
                if e and e.Category and e.Category.Id.Value == int(DB.BuiltInCategory.OST_GenericModel):
                    valid_elems.append(e)

        if not valid_elems:
            # 未在视图中手动选井时，弹出向导选择生成范围
            menu_opts = [
                '【1】先行测试：生成前 3 座样板井 (AA-01 ~ AA-03)',
                '【2】AA- 强电井全系列 (AA-01 ~ AA-22, 共22座)',
                '【3】BB- 弱电井全系列 (BB-01 ~ BB-24, 共24座)',
                '【4】A- 强电井全系列 (A-01 ~ A-127, 共127座)',
                '【5】B- 弱电井全系列 (B-01 ~ B-126, 共126座)',
                '【6】T- 通信井全系列 (T-001 ~ T-196, 共196座)',
                '【7】L- 低压拉线井全系列 (L-001 ~ L-166, 共166座)',
                '【8】E- 预制井全系列 (E-001 ~ E-163, 共163座)',
                '【9】全量 824 座室外井排队连续生成',
                '【10】室内-A- 强电井全系列 (室内-A-01 ~ 室内-A-42, 共42座)',
                '【11】室内-T- 通信井全系列 (室内-T-01 ~ 室内-T-41, 共41座)',
                '【12】室内-E- 弱电井全系列 (室内-E-01 ~ 室内-E-23, 共23座)',
                '【13】室内-L- 低压拉线井全系列 (室内-L-01 ~ 室内-L-17, 共17座)',
                '【14】全量 123 座室内井排队连续生成',
                '【15】全模型 947 座井 (824室外 + 123室内) 全量排队连续生成',
                '【16】清理全部井大样图纸与视图（只清理，不出图）'
            ]
            choice = forms.SelectFromList.show(
                menu_opts,
                title='一键井展开图 - 批量出图向导',
                button_name='开始生成')
            if not choice:
                return

            if '【16】' in choice:
                # 独立清理：不动井，只把模型中所有「井大样_」图纸连同其平面/剖面视图删掉。
                # 专门用来清掉那批"对应井已不存在（Mark 改过或井已删）"的老图纸，
                # 这类图纸在生成流程里不会被逐井替换命中。
                _cnt_before = len([s for s in DB.FilteredElementCollector(doc)
                                   .OfClass(DB.ViewSheet).ToElements()
                                   if (s.Name or "").startswith("井大样_")])
                _ok = forms.alert(
                    '将删除模型中全部「井大样_」图纸及其视图，当前共 {} 张，操作不可撤销。\n'
                    '注意：若要用新剖面框重做全部井，请先执行本清理，再运行【1】~【15】生成；\n'
                    '刚生成完的新图也会被本项删掉。\n确定继续？'.format(_cnt_before),
                    title='清理井大样图纸', ok=False, yes=True, no=True)
                if _ok:
                    _t = DB.Transaction(doc, "清理井大样图纸")
                    _t.Start()
                    _removed = 0
                    _removed_views = 0
                    try:
                        _vp_by_sheet = {}
                        for _vp in DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements():
                            try:
                                _vp_by_sheet.setdefault(_vp.OwnerViewId.Value, []).append(_vp.ViewId)
                            except:
                                pass
                        for _s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
                            try:
                                if not (_s.Name or "").startswith("井大样_"):
                                    continue
                                _kids = _vp_by_sheet.get(_s.Id.Value, [])
                                doc.Delete(_s.Id)
                                for _vid in _kids:
                                    try:
                                        _vv = doc.GetElement(_vid)
                                        if _vv and not _vv.IsTemplate:
                                            doc.Delete(_vid)
                                            _removed_views += 1
                                    except:
                                        pass
                                _removed += 1
                            except:
                                pass

                        # 第二轮：清掉"没有任何图纸归属"的井大样残留视图。
                        # 生成中断、或图纸被单独删除后，这些视图会变成没主的孤儿，
                        # 只删图纸是清不掉它们的 —— 这正是"清理没清干净"的来源。
                        # 判定：名称形如 平面_AG- / 南_AG- / 北_AG- / 西_AG- / 东_AG- 且当前未被任何图纸引用。
                        _placed_now = {}
                        for _vp in DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements():
                            try:
                                _placed_now[_vp.ViewId.Value] = True
                            except:
                                pass
                        for _v in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
                            try:
                                if _v.IsTemplate:
                                    continue
                                _nm = _v.Name or ""
                                _is_agy = False
                                for _pre in ("平面_", "南_", "北_", "西_", "东_"):
                                    if _nm.startswith(_pre):
                                        _is_agy = _nm[len(_pre):].startswith("AG-")
                                        break
                                if not _is_agy:
                                    continue
                                if _v.Id.Value in _placed_now:
                                    continue
                                doc.Delete(_v.Id)
                                _removed_views += 1
                            except:
                                pass
                        _t.Commit()
                    except:
                        try:
                            if _t.HasStarted() and not _t.HasEnded():
                                _t.RollBack()
                        except:
                            pass
                    forms.alert('清理完成：已删除「井大样_」图纸 {} 张、游离视图 {} 个。'.format(_removed, _removed_views),
                                title='清理结果')
                return

            def get_mark(elem):
                p = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
                return p.AsString().strip() if (p and p.AsString()) else ""

            wells_with_mark = [e for e in all_model_wells if get_mark(e) and get_mark(e) != "室内"]

            if '【1】' in choice:
                targets = ["AA-01", "AA-02", "AA-03"]
                valid_elems = [e for e in wells_with_mark if get_mark(e) in targets]
                valid_elems.sort(key=lambda e: get_mark(e))
            elif '【2】' in choice:
                valid_elems = [e for e in wells_with_mark if get_mark(e).startswith("AA-")]
                valid_elems.sort(key=lambda e: get_mark(e))
            elif '【3】' in choice:
                valid_elems = [e for e in wells_with_mark if get_mark(e).startswith("BB-")]
                valid_elems.sort(key=lambda e: get_mark(e))
            elif '【4】' in choice:
                valid_elems = [e for e in wells_with_mark if get_mark(e).startswith("A-") and not get_mark(e).startswith("AA-")]
                valid_elems.sort(key=lambda e: (len(get_mark(e)), get_mark(e)))
            elif '【5】' in choice:
                valid_elems = [e for e in wells_with_mark if get_mark(e).startswith("B-") and not get_mark(e).startswith("BB-")]
                valid_elems.sort(key=lambda e: (len(get_mark(e)), get_mark(e)))
            elif '【6】' in choice:
                valid_elems = [e for e in wells_with_mark if get_mark(e).startswith("T-")]
                valid_elems.sort(key=lambda e: get_mark(e))
            elif '【7】' in choice:
                valid_elems = [e for e in wells_with_mark if get_mark(e).startswith("L-")]
                valid_elems.sort(key=lambda e: get_mark(e))
            elif '【8】' in choice:
                valid_elems = [e for e in wells_with_mark if get_mark(e).startswith("E-")]
                valid_elems.sort(key=lambda e: get_mark(e))
            elif '【9】' in choice:
                valid_elems = [e for e in wells_with_mark if not get_mark(e).startswith("室内-")]
            elif '【10】' in choice:
                valid_elems = [e for e in wells_with_mark if get_mark(e).startswith("室内-A-")]
                valid_elems.sort(key=lambda e: get_mark(e))
            elif '【11】' in choice:
                valid_elems = [e for e in wells_with_mark if get_mark(e).startswith("室内-T-")]
                valid_elems.sort(key=lambda e: get_mark(e))
            elif '【12】' in choice:
                valid_elems = [e for e in wells_with_mark if get_mark(e).startswith("室内-E-")]
                valid_elems.sort(key=lambda e: get_mark(e))
            elif '【13】' in choice:
                valid_elems = [e for e in wells_with_mark if get_mark(e).startswith("室内-L-")]
                valid_elems.sort(key=lambda e: get_mark(e))
            elif '【14】' in choice:
                valid_elems = [e for e in wells_with_mark if get_mark(e).startswith("室内-")]
                valid_elems.sort(key=lambda e: get_mark(e))
            elif '【15】' in choice:
                valid_elems = wells_with_mark

        if not valid_elems:
            forms.alert('未找到符合条件的预制井！', title='错误')
            return

        # 规则与预览：按选中井的真实尺寸实时看排版效果，保存后用新规则生成
        try:
            R = rules_preview_dialog(valid_elems[0])
        except Exception as ex:
            _log("规则窗口异常：" + traceback.format_exc())
            forms.alert("规则与预览窗口出错，已停止生成：\n" + str(ex), title="一键井展开图")
            return
        if not R:
            _log("未生成：规则窗口返回空（取消或关闭）")
            return
        _log("开始生成")
        R_CROP = R["crop_margin_mm"] / FT_MM
        R_FACE_OUT = R["face_out_mm"] / FT_MM
        R_FACE_DEPTH = R["face_depth_mm"] / FT_MM
        R_SCALE = int(R["view_scale"])
        R_SPACING_X = R["spacing_x_mm"] / FT_MM
        R_SPACING_Y = R["spacing_y_mm"] / FT_MM
        R_SHIFT_X = R["sheet_shift_x_mm"] / FT_MM
        R_HIDE = bool(R["hide_grids_levels"])

        # 图框选择：允许选任意已载入图框，也允许无图框出图。
        titleblocks = DB.FilteredElementCollector(doc).OfCategory(
            DB.BuiltInCategory.OST_TitleBlocks).OfClass(DB.FamilySymbol).ToElements()
        frame_mode = forms.CommandSwitchWindow.show(
            ['选择图框', '无图框'], message='选择本次井大样图纸是否使用图框')
        if not frame_mode:
            return
        # 跨引擎兼容：IronPython 2.7 有 long、CPython 3 已无 long（本机引擎为 IronPython 2.7.12）。
        # CommandSwitchWindow 正常返回选项文本，这里仅对"索引形式"做兜底收敛。
        if frame_mode not in ('选择图框', '无图框'):
            try:
                frame_mode = ['选择图框', '无图框'][int(frame_mode)]
            except (TypeError, ValueError, IndexError):
                frame_mode = None
        selected_tb = None
        if frame_mode == '选择图框':
            if not titleblocks:
                forms.alert('模型中没有可用图框；可返回后选择“无图框”。', title='提示')
                return

            class TitleblockOption(forms.TemplateListItem):
                @property
                def name(self):
                    try:
                        family_name = self.item.get_Parameter(
                            DB.BuiltInParameter.SYMBOL_FAMILY_NAME_PARAM).AsString()
                        type_name = self.item.get_Parameter(
                            DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
                        return "{} - {} [ID {}]".format(family_name, type_name, self.item.Id.Value)
                    except:
                        return "图框 ID: " + str(self.item.Id.Value)

            selected_tb = forms.SelectFromList.show(
                [TitleblockOption(tb) for tb in titleblocks],
                title='选择批量排版图框', button_name='使用此图框')
            if not selected_tb:
                return

        # 视图样板：一次列出全部样板（标注各自适用的视图类型），只弹一次、只选一次。
        # 选中的样板按"样板自身的视图类型"自动分配给新建的对应类型视图，
        # 不再像以前那样每种类型各弹一个"是/否"再各选一次。
        view_templates = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.View)
                          if v.IsTemplate]

        def _template_kind(vt):
            if vt == DB.ViewType.FloorPlan:
                return 'FloorPlan'
            if vt == DB.ViewType.Section:
                return 'Section'
            if vt == DB.ViewType.Detail:
                return 'Detail'
            return None

        _kind_label = {'FloorPlan': '平面', 'Section': '剖面', 'Detail': '详图'}

        class ViewTemplateOption(forms.TemplateListItem):
            @property
            def name(self):
                try:
                    kind = _template_kind(self.item.ViewType)
                except:
                    kind = None
                return "{}  【{}】  [ID {}]".format(
                    self.item.Name, _kind_label.get(kind, '其他'), self.item.Id.Value)

        chosen_templates = {}     # 视图类型 -> 样板Id（按类型分别选时使用）
        picked_template_ids = []  # 按勾选顺序记录，供"统一套同一个样板"回退使用
        if view_templates:
            picked_templates = forms.SelectFromList.show(
                [ViewTemplateOption(v) for v in view_templates],
                title='选择视图样板（只选一个 → 平面+四面统一套它；选多个 → 各自按类型匹配，缺的用第一个兜底）',
                button_name='应用样板', multiselect=True)
            if picked_templates:
                if not isinstance(picked_templates, (list, tuple)):
                    picked_templates = [picked_templates]
                for _t in picked_templates:
                    try:
                        _tid_new = _t.Id
                    except:
                        continue
                    if _tid_new not in picked_template_ids:
                        picked_template_ids.append(_tid_new)
                    try:
                        _k = _template_kind(_t.ViewType)
                        if _k and _k not in chosen_templates:
                            chosen_templates[_k] = _tid_new
                    except:
                        pass

        # 附加过滤器排除集合留空：显示范围交由视图样板控制。只保留新建图纸上的井大样视图，不改模型构件隐藏状态。

        # 分批提交：把全量井塞进单个事务会让文档元素持续累积，每处理一批就要重建
        # 一次全文档图形缓存。journal 实测过 updateGReps 18988 elements 耗时 249 秒
        # 且随进度递增，最终把 Revit 拖成无响应。改成每 BATCH_SIZE 口井提交一次，
        # 让 Revit 及时收尾 regenerate，进度也能逐批落盘、崩溃不白跑。
        BATCH_SIZE = 20

        def _start_tx(tag):
            _t = DB.Transaction(doc, "批量自动生成井大样图 " + tag)
            _fho = _t.GetFailureHandlingOptions()
            _fho.SetFailuresPreprocessor(DimensionFailurePreprocessor())
            _fho.SetClearAfterRollback(True)
            _t.SetFailureHandlingOptions(_fho)
            _t.Start()
            return _t

        t = _start_tx("(1/{})".format(len(valid_elems)))
        try:
            if selected_tb is not None and not selected_tb.IsActive:
                selected_tb.Activate()

            success_count = 0
            last_sheet = None
            generated_sheet_ids = []
            replaced_count = 0

            # 查找视图类型
            plan_type_id = DB.ElementId.InvalidElementId
            detail_type_id = DB.ElementId.InvalidElementId
            section_type_id = DB.ElementId.InvalidElementId
            for vt in DB.FilteredElementCollector(doc).OfClass(DB.ViewFamilyType):
                if vt.ViewFamily == DB.ViewFamily.FloorPlan and plan_type_id == DB.ElementId.InvalidElementId:
                    plan_type_id = vt.Id
                if vt.ViewFamily == DB.ViewFamily.Detail and detail_type_id == DB.ElementId.InvalidElementId:
                    detail_type_id = vt.Id
                if vt.ViewFamily == DB.ViewFamily.Section and section_type_id == DB.ElementId.InvalidElementId:
                    section_type_id = vt.Id

            # 查找配色主视图（提供井/管线专业配色的过滤器）：
            # 优先级：井大样剖面框 > 管道综合 底图 > 过滤器最多的管道综合视图
            master_view = None
            try:
                pk = None
                base = None
                pool = []
                for cand in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan):
                    nm = cand.Name or ""
                    if pk is None and "井大样剖面框" in nm:
                        pk = cand
                    if base is None and "管道综合 底图" in nm:
                        base = cand
                    if "管道综合" in nm:
                        pool.append(cand)
                if pk is not None:
                    master_view = pk
                elif base is not None:
                    master_view = base
                else:
                    best_n = -1
                    for c in pool:
                        try:
                            n = c.GetFilters().Count
                        except:
                            n = 0
                        if n > best_n:
                            best_n = n
                            master_view = c
            except:
                master_view = None

            # ---- 预扫描（只做一次）：建立「井编号 -> 该井既有井大样图纸」索引 ----
            # 历史图纸可能已被改过图纸名/图号（例如 图纸名=井大样_LV-L-026、图号=LV-L-026，
            # 而井的 Mark 其实是 L-026），单靠图纸名匹配不到。因此：
            #   1) 优先用图纸上视图名里的 "AG-<井编号>-<4位随机>" 反推井编号；
            #   2) 取不到时退回用图纸名 "井大样_" 之后的后缀。
            stale_sheets = {}
            try:
                _vp_by_sheet = {}
                for _vp in DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements():
                    try:
                        _vp_by_sheet.setdefault(_vp.OwnerViewId.Value, []).append(_vp.ViewId)
                    except:
                        pass
                for _s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
                    try:
                        _nm = _s.Name or ""
                        if not _nm.startswith("井大样_"):
                            continue
                        _suffix = _nm[len("井大样_"):]
                        _kids = _vp_by_sheet.get(_s.Id.Value, [])
                        _tag = None
                        for _vid in _kids:
                            _vv = doc.GetElement(_vid)
                            if not (_vv and _vv.Name):
                                continue
                            _parts = _vv.Name.split("_", 1)
                            if len(_parts) != 2 or not _parts[1].startswith("AG-"):
                                continue
                            _seg = _parts[1].split("-")
                            if len(_seg) >= 3:
                                _tag = "-".join(_seg[1:-1])
                            break
                        # 双键索引：视图名反推的 tag 与图纸名后缀都登记。
                        # 井 Mark 可能写作 "L-151"，也可能写作 "LV-L-151"（图纸被改过名），
                        # 两个键都挂上才能都命中；重复时第二次删除会被 try 吞掉，不影响计数。
                        _keys = []
                        if _tag and _tag not in _keys:
                            _keys.append(_tag)
                        if _suffix not in _keys:
                            _keys.append(_suffix)
                        for _k in _keys:
                            stale_sheets.setdefault(_k, []).append((_s.Id, _kids))
                    except:
                        pass
            except:
                pass

            pb = forms.ProgressBar(title='正在批量生成井大样图... ({value}/{max_value})', cancellable=True)
            for idx, elem in enumerate(valid_elems):
                if pb.cancelled:
                    break

                # 批次边界：先提交上一批，再开新事务。
                # 事务内的修改会让 Revit 反复重建全文档图形缓存（journal 实测单次
                # 249 秒），分批后每批只触发一次；进度条也在这个事务外的时间点刷新，
                # 界面才会真的动起来。
                if idx > 0 and idx % BATCH_SIZE == 0:
                    try:
                        t.Commit()
                    except:
                        try:
                            if t.HasStarted() and not t.HasEnded():
                                t.RollBack()
                        except:
                            pass
                    t = _start_tx("({}/{})".format(idx + 1, len(valid_elems)))
                    try:
                        doc.Regenerate()
                    except:
                        pass
                    try:
                        pb.update_progress(idx, len(valid_elems))
                    except:
                        pass

                pb.update_progress(idx + 1, len(valid_elems))
                try:
                    mark_param = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
                    mark_val = mark_param.AsString() if mark_param else None
                    base_name = mark_val if mark_val else elem.Name

                    # 「重新生成即替换」：按预扫描索引清掉这口井既有的井大样图纸，
                    # 连同落在该图纸上的平面/东南西北剖面视图一起删除，再由下面重建。
                    for _sid, _kids in stale_sheets.get(base_name, []):
                        try:
                            doc.Delete(_sid)
                            for _vid in _kids:
                                try:
                                    _vv = doc.GetElement(_vid)
                                    if _vv and not _vv.IsTemplate:
                                        doc.Delete(_vid)
                                except:
                                    pass
                            replaced_count += 1
                        except:
                            pass

                    # 1. 新建图纸（无图框模式用 InvalidElementId；不在图纸加入标题栏实例）
                    if selected_tb is not None:
                        sheet = DB.ViewSheet.Create(doc, selected_tb.Id)
                    else:
                        sheet = DB.ViewSheet.Create(doc, DB.ElementId.InvalidElementId)

                    rand_suffix = System.Guid.NewGuid().ToString()[:4]

                    sheet.Name = "井大样_" + base_name
                    sheet.SheetNumber = "AG-" + base_name + "-" + rand_suffix
                    for param_name in ["专业", "所属专业"]:
                        try:
                            sp = sheet.LookupParameter(param_name)
                            if sp and not sp.IsReadOnly:
                                sp.Set("井大样")
                        except:
                            pass
                    # 默认不显示在图纸目录明细表中，彻底隔离不影响其他专业出图
                    try:
                        p_sched = sheet.get_Parameter(DB.BuiltInParameter.SHEET_SCHEDULED)
                        if p_sched and not p_sched.IsReadOnly:
                            p_sched.Set(0)
                    except:
                        pass

                    bbox = elem.get_BoundingBox(None)
                    if not bbox:
                        continue

                    # 1. Z 轴标高与高度：直接由构件 BBox.Min.Z / Max.Z 确定（100% 精确吻合实体，绝无符号线虚高）
                    center_z = (bbox.Min.Z + bbox.Max.Z) / 2.0
                    pit_dz = (bbox.Max.Z - bbox.Min.Z) / 2.0

                    # 2. 平面中心 (X, Y)：Generic Model 族的 LocationPoint 100% 为几何对称中心
                    loc = elem.Location
                    if isinstance(loc, DB.LocationPoint) and loc.Point:
                        center_x = loc.Point.X
                        center_y = loc.Point.Y
                    else:
                        center_x = (bbox.Min.X + bbox.Max.X) / 2.0
                        center_y = (bbox.Min.Y + bbox.Max.Y) / 2.0

                    # 3. 平面外轮廓半长宽 (pit_dx, pit_dy)：从 Solid 真实顶点提取，消除 2D 符号线虚高
                    pit_dx = (bbox.Max.X - bbox.Min.X) / 2.0
                    pit_dy = (bbox.Max.Y - bbox.Min.Y) / 2.0
                    try:
                        opt = DB.Options()
                        opt.ComputeReferences = False
                        opt.DetailLevel = DB.ViewDetailLevel.Fine
                        geom = elem.get_Geometry(opt)
                        if geom:
                            s_min_x, s_max_x = float('inf'), float('-inf')
                            s_min_y, s_max_y = float('inf'), float('-inf')
                            found_s = False
                            for obj in geom:
                                if isinstance(obj, DB.GeometryInstance):
                                    igeom = obj.GetInstanceGeometry()
                                    if igeom:
                                        for s in igeom:
                                            if isinstance(s, DB.Solid) and s.Volume > 0.001:
                                                found_s = True
                                                for f in s.Faces:
                                                    mesh = f.Triangulate()
                                                    if mesh:
                                                        for pt in mesh.Vertices:
                                                            if pt.X < s_min_x: s_min_x = pt.X
                                                            if pt.X > s_max_x: s_max_x = pt.X
                                                            if pt.Y < s_min_y: s_min_y = pt.Y
                                                            if pt.Y > s_max_y: s_max_y = pt.Y
                            if found_s and s_max_x > s_min_x and s_max_y > s_min_y:
                                pit_dx = (s_max_x - s_min_x) / 2.0
                                pit_dy = (s_max_y - s_min_y) / 2.0
                                center_x = (s_min_x + s_max_x) / 2.0
                                center_y = (s_min_y + s_max_y) / 2.0
                    except:
                        pass

                    center = DB.XYZ(center_x, center_y, center_z)
                    offset = R_CROP  # 平面裁剪余量（规则）
                    # 剖面框：紧贴井的东西南北四条边生成，剖切面落在井外墙向外 100mm 处，
                    # 框厚（沿视线方向）1100mm —— 既切到外墙，又能看进井内一段。
                    face_out = R_FACE_OUT  # 剖切面外移（规则）
                    face_depth = R_FACE_DEPTH  # 剖面框厚度（规则）

                    # ============================================
                    # 2. 生成平面图 (ViewPlan)
                    # ============================================
                    level_id = elem.LevelId
                    if not level_id or level_id == DB.ElementId.InvalidElementId:
                        levels = sorted(
                            DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements(),
                            key=lambda l: abs(l.Elevation - center.Z))
                        level_id = levels[0].Id if levels else DB.ElementId.InvalidElementId

                    if plan_type_id != DB.ElementId.InvalidElementId and level_id != DB.ElementId.InvalidElementId:
                        view_plan = DB.ViewPlan.Create(doc, plan_type_id, level_id)
                        view_plan.CropBoxActive = True
                        view_plan.CropBoxVisible = False

                        crop = DB.BoundingBoxXYZ()
                        crop.Min = DB.XYZ(center.X - pit_dx - offset,
                                          center.Y - pit_dy - offset,
                                          center.Z - pit_dz - offset)
                        crop.Max = DB.XYZ(center.X + pit_dx + offset,
                                          center.Y + pit_dy + offset,
                                          center.Z + pit_dz + offset)
                        view_plan.CropBox = crop

                        level_elev = doc.GetElement(level_id).Elevation
                        view_range = view_plan.GetViewRange()
                        # 剖切面置于井顶之上：井整体落在剖切面以下，平面图以俯视投影
                        # 完整显示井身（含顶面轮廓）。原来 Cut 放在井中心高度，Revit
                        # 平面图不显示剖切面之上的部分，导致"看不到井顶"。
                        view_range.SetOffset(DB.PlanViewPlane.TopClipPlane,
                                             (center.Z + pit_dz + 3.0) - level_elev)
                        view_range.SetOffset(DB.PlanViewPlane.CutPlane,
                                             (center.Z + pit_dz + 0.6) - level_elev)
                        view_range.SetOffset(DB.PlanViewPlane.BottomClipPlane,
                                             (center.Z - pit_dz - 2.0) - level_elev)
                        view_range.SetOffset(DB.PlanViewPlane.ViewDepthPlane,
                                             (center.Z - pit_dz - 2.0) - level_elev)
                        view_plan.SetViewRange(view_range)
                    else:
                        t = DB.Transform.Identity
                        t.Origin = center
                        t.BasisZ = DB.XYZ(0, 0, -1)
                        t.BasisY = DB.XYZ(0, 1, 0)
                        t.BasisX = DB.XYZ(1, 0, 0)
                        sbox = DB.BoundingBoxXYZ()
                        sbox.Transform = t
                        sbox.Min = DB.XYZ(-pit_dx - offset, -pit_dy - offset, -pit_dz - offset)
                        sbox.Max = DB.XYZ(pit_dx + offset, pit_dy + offset, pit_dz + offset)
                        if detail_type_id != DB.ElementId.InvalidElementId:
                            view_plan = DB.ViewSection.CreateDetail(doc, detail_type_id, sbox)
                        else:
                            view_plan = DB.ViewSection.CreateSection(doc, section_type_id, sbox)

                    view_plan.Name = "{}_{}".format("平面", sheet.SheetNumber)

                    # ============================================
                    # 3. 生成四个立面剖面视图
                    # ============================================
                    def create_elevation(look_z, up_y, right_x, name_suffix, min_z_depth, max_z_depth):
                        t = DB.Transform.Identity
                        t.Origin = center
                        t.BasisZ = look_z
                        t.BasisY = up_y
                        t.BasisX = right_x

                        sbox = DB.BoundingBoxXYZ()
                        sbox.Transform = t

                        # 横向/竖向同样只留 face_out(100mm)：剖面框紧贴井的该条外墙，
                        # 不再使用 1.37m 的大余量，避免图面大量空白。
                        if name_suffix in ["南", "北"]:
                            sbox.Min = DB.XYZ(-pit_dx - face_out, -pit_dz - face_out, min_z_depth)
                            sbox.Max = DB.XYZ(pit_dx + face_out, pit_dz + face_out, max_z_depth)
                        elif name_suffix in ["东", "西"]:
                            sbox.Min = DB.XYZ(-pit_dy - face_out, -pit_dz - face_out, min_z_depth)
                            sbox.Max = DB.XYZ(pit_dy + face_out, pit_dz + face_out, max_z_depth)

                        if detail_type_id != DB.ElementId.InvalidElementId:
                            v = DB.ViewSection.CreateDetail(doc, detail_type_id, sbox)
                        else:
                            v = DB.ViewSection.CreateSection(doc, section_type_id, sbox)
                        v.Name = "{}_{}".format(name_suffix, sheet.SheetNumber)
                        return v

                    # 实测(Revit 2026)：View.ViewDirection == CropBox.Transform.BasisZ == -输入BasisZ，
                    # RightDirection == -输入BasisX；观察者(视点)位于剖面框局部 Z 的 Min 端、朝 Max 端看，
                    # 剖切面即观察者所在端面。
                    # 「北」「南」两视图统一采用“朝北看”呈现约定（与历史图纸一致）：
                    #   BasisZ=(0,1,0) => ViewDirection=(0,-1,0)、Right=(1,0,0)，东在右。
                    # 2026-10-07 曾把「北」改成 BasisZ=(0,-1,0)、BasisX=(1,0,0)（“观察者在北”读法），
                    # 造成北视图相对历史版本左右镜像（东在左）；2026-10-08 已恢复，勿再改动。
                    # 四面剖面框统一：剖面框均外延 100mm(face_out)，框深 face_depth(1100mm)。
                    # 南/西/东：观察者在井外 → 局部 Min.Z = -half - face_out（剖切在外墙面外 100mm）。
                    # 北：沿用历史“井内向北看” → 局部 Z 取 [+(half+face_out-face_depth), +(half+face_out)]。
                    _z_near_ns = -pit_dy - face_out
                    _z_near_ew = -pit_dx - face_out

                    # 北视图：BasisZ=(0,1,0) → ViewDirection=(0,-1,0)、Right=(1,0,0)（东在右），
                    # 局部 Z 正向 = 世界 +Y = 北；观察者在井内侧向北看（与历史图纸一致）。
                    view_back = create_elevation(
                        DB.XYZ(0, 1, 0), DB.XYZ(0, 0, 1), DB.XYZ(-1, 0, 0),
                        "北", -_z_near_ns - face_depth, -_z_near_ns)

                    # 南立面 (下面)：观察者在南侧朝北看，ViewDirection=(0,-1,0)
                    view_front = create_elevation(
                        DB.XYZ(0, 1, 0), DB.XYZ(0, 0, 1), DB.XYZ(-1, 0, 0),
                        "南", _z_near_ns, _z_near_ns + face_depth)

                    # 西立面 (左边)：观察者在西侧朝东看，ViewDirection=(-1,0,0)
                    view_left = create_elevation(
                        DB.XYZ(1, 0, 0), DB.XYZ(0, 0, 1), DB.XYZ(0, 1, 0),
                        "西", _z_near_ew, _z_near_ew + face_depth)

                    # 东立面 (右边)：观察者在东侧朝西看，ViewDirection=(1,0,0)
                    view_right = create_elevation(
                        DB.XYZ(-1, 0, 0), DB.XYZ(0, 0, 1), DB.XYZ(0, -1, 0),
                        "东", _z_near_ew, _z_near_ew + face_depth)

                    all_views = [view_plan, view_front, view_back, view_left, view_right]

                    # 套视图样板：优先用与本视图同类型的样板；该类型没选时，
                    # 回退到用户勾选的第一个样板。实测 Revit 允许跨类型赋值
                    # （Detail 样板可直接套到 FloorPlan 视图），
                    # 所以"只勾一个样板"也能让平面 + 四个立面成套统一。
                    for v in all_views:
                        try:
                            if v.ViewType == DB.ViewType.FloorPlan:
                                template_key = 'FloorPlan'
                            elif v.ViewType == DB.ViewType.Section:
                                template_key = 'Section'
                            elif v.ViewType == DB.ViewType.Detail:
                                template_key = 'Detail'
                            else:
                                template_key = None
                        except:
                            template_key = None

                        tid = chosen_templates.get(template_key) if template_key else None
                        if tid is None and picked_template_ids:
                            tid = picked_template_ids[0]
                        if tid is None:
                            continue
                        try:
                            v.ViewTemplateId = tid
                        except:
                            # 该样板确实套不上时，再依次试用户勾选的其他样板
                            for _alt in picked_template_ids:
                                try:
                                    v.ViewTemplateId = _alt
                                    break
                                except:
                                    continue

                    # ============================================
                    # 4. 统一设置视图样式 & 隐藏轴网、标高、剖面标记
                    # ============================================
                    for v in all_views:
                        # 视图套用样板后，被样板接管的 Scale/DisplayStyle/DetailLevel 再赋值
                        # 会抛 "DisplayStyle cannot be modified."（journal 实证 line 597），
                        # 导致每口井都被判失败、最后"没有图纸生成成功"。逐项保护，让样板说话。
                        try:
                            v.Scale = R_SCALE
                        except:
                            pass
                        try:
                            v.DisplayStyle = DB.DisplayStyle.Wireframe
                        except:
                            pass
                        try:
                            v.DetailLevel = DB.ViewDetailLevel.Fine
                        except:
                            pass
                        # 隐藏轴网与标高
                        for cat in ([DB.BuiltInCategory.OST_Grids, DB.BuiltInCategory.OST_Levels] if R_HIDE else []):
                            try:
                                v.SetCategoryHidden(DB.ElementId(cat), True)
                            except:
                                pass
                        # 强力隐藏视图中的所有剖面标记/轴号标签 (OST_Viewers)
                        try:
                            viewers = [e.Id for e in DB.FilteredElementCollector(doc, v.Id)
                                       .OfCategory(DB.BuiltInCategory.OST_Viewers).ToElements()
                                       if e.CanBeHidden(v)]
                            if viewers:
                                v.HideElements(System.Collections.Generic.List[DB.ElementId](viewers))
                        except:
                            pass
                        # 自动归类至“井大样”子规程与专业，彻底隔离不影响其他专业
                        for param_name in ["子规程", "所属专业"]:
                            try:
                                vp = v.LookupParameter(param_name)
                                if vp and not vp.IsReadOnly:
                                    vp.Set("井大样")
                            except:
                                pass

                    # ============================================
                    # 4.1 隐藏剖面取样标记等注释残留 + 复制主视图过滤器（井外观专业配色）
                    # ============================================
                    for v in all_views:
                        try:
                            v.SetCategoryHidden(DB.ElementId(DB.BuiltInCategory.OST_Sections), True)
                        except:
                            pass
                        if master_view is not None and v.Id != master_view.Id and not v.ViewTemplateId.Value:
                            try:
                                for fid in master_view.GetFilters():
                                    try:
                                        v.AddFilter(fid)
                                        v.SetFilterOverrides(fid, master_view.GetFilterOverrides(fid))
                                        v.SetFilterVisibility(fid, master_view.GetFilterVisibility(fid))
                                    except:
                                        pass
                            except:
                                pass


                    # ============================================
                    # 6. 排版到图纸
                    # ============================================
                    tb_instances = DB.FilteredElementCollector(
                        doc, sheet.Id).OfCategory(
                        DB.BuiltInCategory.OST_TitleBlocks).ToElements()
                    tb_center_x, tb_center_y = 1.5, 1.0
                    if tb_instances:
                        tb_box = tb_instances[0].get_BoundingBox(sheet)
                        if tb_box:
                            tb_center_x = (tb_box.Min.X + tb_box.Max.X) / 2.0 - R_SHIFT_X
                            tb_center_y = (tb_box.Min.Y + tb_box.Max.Y) / 2.0

                    spacing_x = R_SPACING_X
                    spacing_y = R_SPACING_Y

                    DB.Viewport.Create(doc, sheet.Id, view_plan.Id,
                                       DB.XYZ(tb_center_x, tb_center_y, 0))
                    DB.Viewport.Create(doc, sheet.Id, view_front.Id,
                                       DB.XYZ(tb_center_x, tb_center_y - spacing_y, 0))
                    DB.Viewport.Create(doc, sheet.Id, view_back.Id,
                                       DB.XYZ(tb_center_x, tb_center_y + spacing_y, 0))
                    DB.Viewport.Create(doc, sheet.Id, view_left.Id,
                                       DB.XYZ(tb_center_x - spacing_x, tb_center_y, 0))
                    DB.Viewport.Create(doc, sheet.Id, view_right.Id,
                                       DB.XYZ(tb_center_x + spacing_x, tb_center_y, 0))

                    success_count += 1
                    generated_sheet_ids.append(sheet.Id)
                    last_sheet = sheet
                except Exception as inner_e:
                    forms.alert(
                        "创建图纸过程崩溃，具体原因:\n" + str(inner_e) +
                        "\n\n" + traceback.format_exc(),
                        title="底层API报错")
            t.Commit()
            pb.close()
        except Exception as trans_e:
            pb.close()
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            raise trans_e

        if last_sheet:
            try:
                uidoc.ActiveView = last_sheet
            except:
                pass

        if success_count > 0:
            # 导出只包含本次生成的图纸；视图中已隐藏的元素本身不会出现在图纸输出中。
            export_choice = forms.alert(
                '是否立即导出本次生成的图纸为 PDF？\n已隐藏的构件不会导出。',
                title='导出图纸', ok=False, yes=True, no=True)
            export_result = ''
            if export_choice and generated_sheet_ids:
                try:
                    folder = forms.pick_folder(title='选择 PDF 导出文件夹')
                    if folder:
                        opt = DB.PDFExportOptions()
                        opt.Combine = False
                        opt.FileName = ''
                        ok = doc.Export(folder, System.Collections.Generic.List[DB.ElementId](generated_sheet_ids), opt)
                        export_result = '\nPDF 导出' + ('完成' if ok else '未成功') + '：' + folder
                except Exception as export_err:
                    export_result = '\nPDF 导出失败：' + str(export_err)
            # 汇总本次实际生效的视图样板，避免"以为套上了、其实没套"
            _tpl_desc = []
            for _tk, _tlabel in [('FloorPlan', '平面'), ('Section', '剖面'), ('Detail', '详图')]:
                _tid = chosen_templates.get(_tk)
                if _tid is None and picked_template_ids:
                    _tid = picked_template_ids[0]   # 与套用逻辑一致的回退
                if _tid is None:
                    continue
                try:
                    _tname = doc.GetElement(_tid).Name
                except:
                    _tname = str(_tid)
                _tpl_desc.append('{}→{}'.format(_tlabel, _tname))
            if _tpl_desc:
                _tpl_msg = '；'.join(_tpl_desc)
            else:
                _tpl_msg = '未选择样板（所有视图均未套）'

            forms.alert(
                '批量生成大样图完毕！\n共成功生成 {} 张图纸，替换（删除重建）旧图 {} 张。\n'
                '视图样板：{}\n'
                '提示：套了样板的视图，其"视图范围/显示样式"由样板决定，脚本不再覆盖。{}'.format(
                    success_count, replaced_count, _tpl_msg, export_result),
                title='成功')
        else:
            forms.alert('执行完成，但没有图纸生成成功。请查看上面的打印日志。', title='警告')

    except System.Exception as e:
        forms.alert(
            "发生严重系统错误:\n" + str(e) + "\n\n详细信息:\n" + traceback.format_exc(),
            title="报错追踪")
    except Exception as e:
        forms.alert(
            "发生严重Python错误:\n" + str(e) + "\n\n详细信息:\n" + traceback.format_exc(),
            title="报错追踪")


if __name__ == '__main__':
    main()
