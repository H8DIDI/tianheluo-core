# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "一键图纸归类\n与图框编号"
__author__ = "KAIFA"
__doc__ = ("把井展开图生成的图纸按系统归类（强电、弱电、通信、低压、预制、室内各组），\n"
           "统一改成「前缀 + 系统代码 + 序号」的图号；同时给每张图纸写入「图纸系统」参数，\n"
           "项目浏览器按这个参数分组后，同一系统的图纸就在一个文件夹里。也可以统一替换图框。\n"
           "先预览「旧图名 → 新图号」，确认后再应用。")

import io
import os
import re
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from Autodesk.Revit import DB
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms
import System.Windows.Forms as WF
import System.Drawing as SD

doc = revit.doc
uidoc = revit.uidoc

TITLE = u"一键图纸归类与图框编号"
SHEET_PREFIX_NAME = u"井大样_"
DEFAULT_PREFIX = u"AG-"
SYSTEM_PARAM = u"图纸系统"
SHARED_GROUP = u"KAIFA"
OUT_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools")

# (图名里的系列前缀, 系统名, 图号里的系统代码)。顺序很重要：长的前缀放前面（AA 在 A 前，室内-X 在最前）
SYSTEM_MAP = [
    (u"室内-A", u"室内强电井", u"NQ"),
    (u"室内-T", u"室内通信井", u"NT"),
    (u"室内-E", u"室内弱电井", u"NR"),
    (u"室内-L", u"室内低压拉线井", u"ND"),
    (u"AA", u"强电井", u"QD"),
    (u"A", u"强电井", u"QD"),
    (u"BB", u"弱电井", u"RD"),
    (u"B", u"弱电井", u"RD"),
    (u"T", u"通信井", u"TX"),
    (u"L", u"低压拉线井", u"DY"),
    (u"E", u"预制井", u"YZ"),
]
OTHER = (u"其他", u"QT")
_NUM = re.compile(r"(\d+)")

# 图名、图号模板里可用的字段，写成 {字段名}
TOKEN_HINT = u"{旧名}  {井号}  {系统}  {系统码}  {序号}"
DEFAULT_NAME_TPL = u"{旧名}"
DEFAULT_NUM_TPL = u"AG-{系统码}-{序号}"
NAME_PRESETS = [u"{旧名}", u"BTP-IF1 11KV-{井号} Detail Drawing", u"井大样_{井号}"]
NUM_PRESETS = [u"AG-{系统码}-{序号}", u"AG-{序号}", u"BTP-{系统码}-{序号}"]
# Revit 不允许出现在图名、图号里的字符（花括号另外检查）
BAD_CHARS = u":[]|;<>?`~"
# 系统代码默认值（来自 SYSTEM_MAP），窗口里可以逐个改
SYSTEM_CODES = {}
for _prefix, _sysname, _code in SYSTEM_MAP:
    SYSTEM_CODES.setdefault(_sysname, _code)
SYSTEM_CODES[OTHER[0]] = OTHER[1]


# ============================================================
# 纯逻辑：识别、编号、分组（可离线测试）
# ============================================================
_MARK_RE = re.compile(u"[A-Za-z]{1,3}-\\d+(?:-\\d+)?")


def _classify_by_hint(s, system_hint):
    """改过名的图纸（图名已经不是「井大样_…」）：系统用「图纸系统」参数。
    序号取图名里最后的井号（如 T-084-1 取 84），取不到就取图名里最后的数字。"""
    for i, (_prefix, sysname, code) in enumerate(SYSTEM_MAP):
        if sysname == system_hint:
            found = _MARK_RE.findall(s)
            if found:
                nums = _NUM.findall(found[-1])
                num = int(nums[0]) if nums else 0
            else:
                nums = _NUM.findall(s)
                num = int(nums[-1]) if nums else 0
            return {"series": u"", "system": sysname, "code": code, "num": num, "order": i}
    return None


def classify(sheet_name, system_hint=None):
    """从图名识别系统。返回 dict(series, system, code, num, order)；认不出的归到「其他」。
    改过名的图纸（图名不再以「井大样_」开头）若有「图纸系统」参数，就以参数为准，不看图名。"""
    s = sheet_name or u""
    if s.startswith(SHEET_PREFIX_NAME):
        s = s[len(SHEET_PREFIX_NAME):]
    elif system_hint:
        hinted = _classify_by_hint(s, system_hint)
        if hinted is not None:
            return hinted
    for i, (prefix, sysname, code) in enumerate(SYSTEM_MAP):
        if s.startswith(prefix):
            rest = s[len(prefix):]
            m = _NUM.search(rest)
            num = int(m.group(1)) if m else 0
            return {"series": prefix, "system": sysname, "code": code, "num": num, "order": i}
    m = _NUM.search(s)
    num = int(m.group(1)) if m else 0
    return {"series": u"", "system": OTHER[0], "code": OTHER[1], "num": num, "order": len(SYSTEM_MAP)}


def sort_key(info):
    """排序：系统顺序 → 系列 → 井号。同一系统的图纸排在一起。"""
    return (info["order"], info["series"], info["num"])


def well_part(name):
    """{井号}：「井大样_」后面的部分；改过名的图纸取图名里最后的井号（如 T-068），取不到就取最后一段。"""
    s = name or u""
    if s.startswith(SHEET_PREFIX_NAME):
        return s[len(SHEET_PREFIX_NAME):]
    found = _MARK_RE.findall(s)
    if found:
        return found[-1]
    if u"-" in s:
        return s.split(u"-")[-1]
    return s


def fill_template(tpl, values):
    """把模板里的 {字段} 换成对应的值；认不出的 {…} 原样保留，交给检查提示。"""
    out = tpl
    for key in sorted(values.keys(), key=len, reverse=True):
        out = out.replace(u"{%s}" % key, values[key])
    return out


def make_cfg(name_tpl, num_tpl, start_txt, digits_txt, per_system):
    """校验界面上的输入，返回 (cfg, 错误说明)。错误说明为空字符串表示没问题。"""
    name_tpl = (name_tpl or u"").strip()
    num_tpl = (num_tpl or u"").strip()
    if not name_tpl:
        return None, u"图纸名称模板不能为空（不想改名就填 {旧名}）。"
    if not num_tpl:
        return None, u"图号模板不能为空。"
    try:
        start = int((start_txt or u"").strip())
        digits = int((digits_txt or u"").strip())
    except ValueError:
        return None, u"起始序号和位数必须是整数。"
    if start < 0 or digits < 0:
        return None, u"起始序号和位数不能是负数。"
    cfg = {
        "name_tpl": name_tpl,
        "num_tpl": num_tpl,
        "start": start,
        "digits": digits,
        "per_system": bool(per_system),
    }
    return cfg, u""


def build_plan(rows, cfg):
    """rows：每张选中的图纸一行 {name, sid, sysname, code, gidx, order, series, num}。
    sysname 是归类名（也写进「图纸系统」参数），code 是该归类的系统代码，gidx 是归类在列表里的顺序。
    返回 [(旧图名, 图纸id, 归类名, 新图号, 新图名), ...]，按「归类顺序 → 系列 → 井号」排好。
    per_system 为 True 时每个归类各自从起始序号编号；否则所有选中的图纸连续编号。"""
    ordered = sorted(rows, key=lambda r: (r["gidx"], r["order"], r["series"], r["num"]))
    counters = {}
    plan = []
    for r in ordered:
        key = r["sysname"] if cfg["per_system"] else u"ALL"
        seq = counters.get(key, cfg["start"])
        counters[key] = seq + 1
        if cfg["digits"] > 0:
            seq_txt = u"%0*d" % (cfg["digits"], seq)
        else:
            seq_txt = u"%d" % seq
        vals = {
            u"旧名": r["name"] or u"",
            u"井号": well_part(r["name"]),
            u"系统": r["sysname"],
            u"系统码": r["code"],
            u"序号": seq_txt,
        }
        new_no = fill_template(cfg["num_tpl"], vals)
        new_name = fill_template(cfg["name_tpl"], vals)
        plan.append((r["name"], r["sid"], r["sysname"], new_no, new_name))
    return plan


def plan_warnings(plan, other_names, other_numbers):
    """检查新图名、新图号，返回提示列表（空列表表示没问题）：
    字段名写错、Revit 不允许的字符、本次内部重复、和模型里没选中的图纸重复。"""
    out = []
    unknown = set()
    bad = set()
    names = {}
    numbers = {}
    for _name, _sid, _sys, new_no, new_name in plan:
        for s in (new_no, new_name):
            for f in re.findall(u"\\{[^{}]*\\}", s):
                unknown.add(f)
            for ch in s:
                if ch in BAD_CHARS or ch in u"{}":
                    bad.add(ch)
        names[new_name] = names.get(new_name, 0) + 1
        numbers[new_no] = numbers.get(new_no, 0) + 1
    if unknown:
        out.append(u"有认不出的字段：%s（可用字段：%s）" % (u"、".join(sorted(unknown)), TOKEN_HINT))
    if bad:
        out.append(u"含 Revit 不允许的字符：%s" % u" ".join(sorted(bad)))
    dup_names = [n for n, c in names.items() if c > 1]
    if dup_names:
        out.append(u"图名重复 %d 组（例如「%s」），改名会失败。" % (len(dup_names), dup_names[0]))
    dup_nums = [n for n, c in numbers.items() if c > 1]
    if dup_nums:
        out.append(u"图号重复 %d 组（例如「%s」）。" % (len(dup_nums), dup_nums[0]))
    clash_n = len([n for n in names if n in other_names])
    clash_no = len([n for n in numbers if n in other_numbers])
    if clash_n or clash_no:
        out.append(u"和模型里没选中的图纸撞名：图名 %d 个，图号 %d 个。" % (clash_n, clash_no))
    return out


def auto_layout(items, hints=None):
    """初始归类：按图名认系统（改过名的按「图纸系统」参数），分好组。
    返回 [(归类名, 系统代码, [行, ...]), ...]；组的顺序与 SYSTEM_MAP 一致，「其他」放最后。
    行是 {name, sid, sysname, code, order, series, num}；gidx 由窗口按列表里的组顺序补上。"""
    hints = hints or {}
    system_order = {}
    for i, (_prefix, sysname, _code) in enumerate(SYSTEM_MAP):
        system_order.setdefault(sysname, i)
    system_order.setdefault(OTHER[0], len(SYSTEM_MAP))
    rows_by = {}
    for name, sid in items:
        info = classify(name, hints.get(int(sid.Value)))
        rows_by.setdefault(info["system"], []).append({
            "name": name,
            "sid": sid,
            "sysname": info["system"],
            "code": SYSTEM_CODES.get(info["system"], info["code"]),
            "order": info["order"],
            "series": info["series"],
            "num": info["num"],
        })
    out = []
    for sysname in sorted(rows_by.keys(), key=lambda s: system_order.get(s, len(SYSTEM_MAP))):
        lst = sorted(rows_by[sysname], key=lambda r: (r["order"], r["series"], r["num"]))
        out.append((sysname, SYSTEM_CODES.get(sysname, OTHER[1]), lst))
    return out


# ============================================================
# Revit：图纸、图框、「图纸系统」参数、应用
# ============================================================
def _sheets_wanted():
    out = []
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
        try:
            if s.IsTemplate:
                continue
            out.append((s.Name, s.Id))
        except Exception:
            continue
    return out


def _system_hints():
    """已经写过「图纸系统」参数的图纸：{图纸 ElementId 数字: 系统名}。图名被改过以后，靠它认系统。"""
    out = {}
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
        try:
            p = s.LookupParameter(SYSTEM_PARAM)
            if p is None or not p.HasValue:
                continue
            v = p.AsString()
            if v:
                out[int(s.Id.Value)] = v
        except Exception:
            continue
    return out


def _titleblock_types():
    return list(DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                .OfClass(DB.FamilySymbol).ToElements())


def _titleblock_by_sheet():
    result = {}
    for tb in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_TitleBlocks) \
            .WhereElementIsNotElementType().ToElements():
        try:
            result[int(tb.OwnerViewId.Value)] = tb
        except Exception:
            continue
    return result


def _has_system_param():
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
        try:
            return s.LookupParameter(SYSTEM_PARAM) is not None
        except Exception:
            return False
    return False


def ensure_system_param():
    """确保图纸上有「图纸系统」参数（图纸类别的实例参数）。没有就用共享参数文件创建。
    返回 (是否可用, 说明)。创建过程中会临时切换共享参数文件，结束后恢复原来的设置。"""
    if _has_system_param():
        return True, u""
    app = doc.Application
    old_file = app.SharedParametersFilename
    if not os.path.exists(OUT_DIR):
        os.makedirs(OUT_DIR)
    shared_path = os.path.join(OUT_DIR, u"图纸分组共享参数.txt")
    guid = System.Guid.NewGuid().ToString()
    lines = [
        u"# This is a Revit shared parameter file.",
        u"*META\tVERSION\tMINVERSION",
        u"META\t2\t1",
        u"*GROUP\tID\tNAME",
        u"GROUP\t1\t" + SHARED_GROUP,
        u"*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE",
        u"PARAM\t%s\t%s\tTEXT\t\t1\t1\t井展开图的系统分组，用于项目浏览器分组\t1\t0" % (guid, SYSTEM_PARAM),
        u"",
    ]
    # Revit 读共享参数文件：UTF-8 会把中文读成乱码；用 CRLF 换行会报 readParamDatabase 错误，所以用 UTF-16 + LF
    with io.open(shared_path, "w", encoding="utf-16") as f:
        f.write(u"\n".join(lines))
    try:
        app.SharedParametersFilename = shared_path
        defs = app.OpenSharedParameterFile()
        if defs is None:
            return False, u"无法打开共享参数文件"
        ext_def = None
        for g in defs.Groups:
            if g.Name == SHARED_GROUP:
                for d in g.Definitions:
                    if d.Name == SYSTEM_PARAM:
                        ext_def = d
        if ext_def is None:
            return False, u"共享参数文件里没有找到「%s」" % SYSTEM_PARAM
        cats = DB.CategorySet()
        cats.Insert(doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Sheets))
        binding = app.Create.NewInstanceBinding(cats)
        t = DB.Transaction(doc, u"添加图纸系统参数")
        t.Start()
        try:
            group = getattr(DB.GroupTypeId, "Data", None) if hasattr(DB, "GroupTypeId") else None
            if group is not None:
                ok = doc.ParameterBindings.Insert(ext_def, binding, group)
            else:
                ok = doc.ParameterBindings.Insert(ext_def, binding, DB.BuiltInParameterGroup.PG_DATA)
            t.Commit()
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            return False, u"添加参数失败：%s" % ex
        if not ok:
            return False, u"Revit 没有接受这个参数绑定"
        return True, u""
    finally:
        try:
            app.SharedParametersFilename = old_file
        except Exception:
            pass


def apply_plan(plan, tb_type, keep_tb=True, set_system=True):
    """应用：改图框（可选）、写「图纸系统」参数（可选）、改图名和图号。
    先把所有选中的图纸临时改成 TMP 名、TMP 号，再统一改成新的，避免新旧名字互相撞车。
    每张图纸单独子事务，出错只跳过那一张。返回 (成功数, [失败说明])。"""
    tb_map = _titleblock_by_sheet()
    done = 0
    fails = []
    t = DB.Transaction(doc, u"图纸归类与图框编号")
    t.Start()
    try:
        temp_ok = []
        for name, sid, sysname, new_no, new_name in plan:
            st = DB.SubTransaction(doc)
            st.Start()
            try:
                sheet = doc.GetElement(sid)
                if not keep_tb and tb_type is not None:
                    tb = tb_map.get(int(sid.Value))
                    if tb is not None and tb.GetTypeId().Value != tb_type.Id.Value:
                        tb.ChangeTypeId(tb_type.Id)
                if set_system:
                    p = sheet.LookupParameter(SYSTEM_PARAM)
                    if p is not None and not p.IsReadOnly:
                        p.Set(sysname)
                sheet.Name = u"TMP-NAME-%d" % int(sid.Value)
                sheet.SheetNumber = u"TMP-%d" % int(sid.Value)
                st.Commit()
                temp_ok.append((name, sid, new_no, new_name))
            except Exception as ex:
                if st.HasStarted() and not st.HasEnded():
                    st.RollBack()
                fails.append(u"%s：%s" % (name, ex))
        for name, sid, new_no, new_name in temp_ok:
            st = DB.SubTransaction(doc)
            st.Start()
            try:
                sh = doc.GetElement(sid)
                sh.SheetNumber = new_no
                sh.Name = new_name
                st.Commit()
                done += 1
            except Exception as ex:
                if st.HasStarted() and not st.HasEnded():
                    st.RollBack()
                fails.append(u"%s 改为「%s」/ %s 失败：%s" % (name, new_name, new_no, ex))
        t.Commit()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise ex
    return done, fails


def _sheet_info():
    """模型里所有图纸：[(ElementId 的数字, 图名, 图号)]。用来检查新图名、图号会不会和没选中的图纸撞车。"""
    out = []
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
        try:
            if s.IsTemplate:
                continue
            out.append((int(s.Id.Value), s.Name or u"", s.SheetNumber or u""))
        except Exception:
            continue
    return out


# ============================================================
# 窗口：表格布局（不用停靠抢空间，窗口拉大缩小都不会把按钮挡住）
#   左：图纸树（系统分组，勾系统名整组选中，展开后可单张勾选）
#   右：设置（图名模板、图号模板、编号、系统代码、目标图框）+ 预览 + 提示
#   底：按钮行
# ============================================================
def form_dialog(owner, title, rows, widths):
    """通用小对话框：每行一个说明 + 若干个文本框。点确定返回 [[文本, ...], ...]，点取消返回 None。
    rows：[(说明文字, [初始文本, ...]), ...]；widths：每个文本框的宽度。"""
    from System.Windows.Forms import (Form, Label, TextBox, Button, DialogResult, TableLayoutPanel,
                                      FlowLayoutPanel, FlowDirection, DockStyle, Padding, FormBorderStyle,
                                      FormStartPosition)
    from System.Drawing import Size, Font

    f = Form()
    f.Text = title
    f.Font = Font(u"Microsoft YaHei UI", 9.0)
    f.FormBorderStyle = FormBorderStyle.FixedDialog
    f.StartPosition = FormStartPosition.CenterParent
    f.MaximizeBox = False
    f.MinimizeBox = False
    f.ClientSize = Size(max(360, 200 + sum(widths) + 40), 110 + 38 * len(rows))

    root = TableLayoutPanel()
    root.Dock = DockStyle.Fill
    root.Padding = Padding(14, 12, 14, 10)
    root.ColumnCount = 1
    root.RowCount = 2
    root.RowStyles.Add(WF.RowStyle(WF.SizeType.Percent, 100))
    root.RowStyles.Add(WF.RowStyle(WF.SizeType.Absolute, 46))
    f.Controls.Add(root)

    body = TableLayoutPanel()
    body.Dock = DockStyle.Fill
    body.ColumnCount = 1 + len(widths)
    body.RowCount = 0
    body.ColumnStyles.Add(WF.ColumnStyle(WF.SizeType.Absolute, 180))
    for w in widths:
        body.ColumnStyles.Add(WF.ColumnStyle(WF.SizeType.Absolute, w))
    root.Controls.Add(body, 0, 0)

    boxes = []
    for label_text, values in rows:
        lb = Label()
        lb.Text = label_text
        lb.TextAlign = SD.ContentAlignment.MiddleLeft
        lb.Dock = DockStyle.Fill
        lb.Margin = Padding(0, 4, 0, 4)
        body.RowCount += 1
        body.RowStyles.Add(WF.RowStyle(WF.SizeType.AutoSize))
        row = body.RowCount - 1
        body.Controls.Add(lb, 0, row)
        row_boxes = []
        for j, v in enumerate(values):
            tb = TextBox()
            tb.Text = v
            tb.Width = widths[j]
            body.Controls.Add(tb, 1 + j, row)
            row_boxes.append(tb)
        boxes.append(row_boxes)

    btns = FlowLayoutPanel()
    btns.Dock = DockStyle.Fill
    btns.FlowDirection = FlowDirection.RightToLeft
    root.Controls.Add(btns, 0, 1)
    b_cancel = Button()
    b_cancel.Text = u"取消"
    b_cancel.Size = Size(96, 30)
    b_cancel.DialogResult = DialogResult.Cancel
    btns.Controls.Add(b_cancel)
    b_ok = Button()
    b_ok.Text = u"确定"
    b_ok.Size = Size(96, 30)
    b_ok.DialogResult = DialogResult.OK
    btns.Controls.Add(b_ok)
    f.AcceptButton = b_ok
    f.CancelButton = b_cancel

    out = None
    if f.ShowDialog(owner) == DialogResult.OK:
        out = [[tb.Text.strip() for tb in rb] for rb in boxes]
    return out


def build_form(result):
    # 开启视觉样式，否则列表的分组标题（归类名）可能不显示
    WF.Application.EnableVisualStyles()
    from System.Windows.Forms import (Form, Label, TextBox, Button, ComboBox, ComboBoxStyle, ListView, ListViewItem,
                                      ListViewGroup, ColumnHeader, ListBox, CheckBox, DialogResult,
                                      FormBorderStyle, FormStartPosition, DockStyle, FlowLayoutPanel, FlowDirection,
                                      TableLayoutPanel, Padding, MessageBoxButtons, ContextMenuStrip,
                                      ToolStripMenuItem, View)
    from System.Drawing import Size, Font, Color, Point

    FONT = Font(u"Microsoft YaHei UI", 9.0)
    GREY = Color.FromArgb(90, 90, 90)
    form = Form()
    form.Text = TITLE
    form.Font = FONT
    form.StartPosition = FormStartPosition.CenterScreen
    form.FormBorderStyle = FormBorderStyle.Sizable
    form.MinimumSize = Size(1080, 700)
    form.ClientSize = Size(1180, 800)

    # 根布局：第 1 行左右两块，第 2 行是按钮（横跨两列）
    root = TableLayoutPanel()
    root.Dock = DockStyle.Fill
    root.Padding = Padding(14, 12, 14, 10)
    root.ColumnCount = 2
    root.RowCount = 2
    root.ColumnStyles.Add(WF.ColumnStyle(WF.SizeType.Absolute, 460))
    root.ColumnStyles.Add(WF.ColumnStyle(WF.SizeType.Percent, 100))
    root.RowStyles.Add(WF.RowStyle(WF.SizeType.Percent, 100))
    root.RowStyles.Add(WF.RowStyle(WF.SizeType.Absolute, 46))
    form.Controls.Add(root)

    # ---------- 左：图纸列表（分组 = 归类，勾选 = 参与出图，高亮选中后可移动归类） ----------
    left = TableLayoutPanel()
    left.Dock = DockStyle.Fill
    left.ColumnCount = 1
    left.RowCount = 4
    left.Margin = Padding(0, 0, 14, 0)
    left.RowStyles.Add(WF.RowStyle(WF.SizeType.Absolute, 26))
    left.RowStyles.Add(WF.RowStyle(WF.SizeType.Percent, 100))
    left.RowStyles.Add(WF.RowStyle(WF.SizeType.Absolute, 36))
    left.RowStyles.Add(WF.RowStyle(WF.SizeType.Absolute, 36))
    root.Controls.Add(left, 0, 0)

    lb_title = Label()
    lb_title.Text = u"1. 选择图纸与归类（勾选 = 参与；高亮选中后可移动归类）"
    lb_title.Dock = DockStyle.Fill
    lb_title.TextAlign = SD.ContentAlignment.MiddleLeft
    left.Controls.Add(lb_title, 0, 0)

    lv = ListView()
    lv.Dock = DockStyle.Fill
    lv.View = View.Details
    lv.CheckBoxes = True
    lv.FullRowSelect = True
    lv.HideSelection = False
    lv.ShowGroups = True
    col_name = ColumnHeader()
    col_name.Text = u"图名"
    col_name.Width = 300
    lv.Columns.Add(col_name)
    col_no = ColumnHeader()
    col_no.Text = u"原图号"
    col_no.Width = 120
    lv.Columns.Add(col_no)
    left.Controls.Add(lv, 0, 1)

    row_a = FlowLayoutPanel()
    row_a.Dock = DockStyle.Fill
    row_a.FlowDirection = FlowDirection.LeftToRight
    left.Controls.Add(row_a, 0, 2)
    row_b = FlowLayoutPanel()
    row_b.Dock = DockStyle.Fill
    row_b.FlowDirection = FlowDirection.LeftToRight
    left.Controls.Add(row_b, 0, 3)

    def small_button(holder, text, width):
        b = Button()
        b.Text = text
        b.Size = Size(width, 28)
        holder.Controls.Add(b)
        return b

    btn_all = small_button(row_a, u"全选", 64)
    btn_none = small_button(row_a, u"全不选", 72)
    btn_check_sel = small_button(row_a, u"勾选所选", 86)
    btn_uncheck_sel = small_button(row_a, u"取消所选", 86)
    btn_move = small_button(row_b, u"移到归类…", 96)
    btn_new = small_button(row_b, u"新建归类…", 96)
    btn_settings = small_button(row_b, u"归类设置…", 96)

    # 原图号：从模型里读，方便对照
    sheet_info = _sheet_info()
    num_by_id = {}
    for (v, _n, num) in sheet_info:
        num_by_id[v] = num

    # 初始归类：按系统分组（改过名的按「图纸系统」参数认），每个归类一个分组
    hints = _system_hints()
    for sysname, code, rows in auto_layout(_sheets_wanted(), hints):
        g = ListViewGroup(sysname)
        g.Tag = {"name": sysname, "code": code}
        lv.Groups.Add(g)
        for r in rows:
            it = ListViewItem(r["name"])
            it.SubItems.Add(num_by_id.get(int(r["sid"].Value), u""))
            it.Checked = (r["name"] or u"").startswith(SHEET_PREFIX_NAME)
            it.Tag = r
            it.Group = g
            lv.Items.Add(it)

    # ---------- 右：设置 + 预览 + 提示 ----------
    right = TableLayoutPanel()
    right.Dock = DockStyle.Fill
    right.ColumnCount = 1
    right.RowCount = 5
    right.Margin = Padding(0)
    right.RowStyles.Add(WF.RowStyle(WF.SizeType.AutoSize))
    right.RowStyles.Add(WF.RowStyle(WF.SizeType.AutoSize))
    right.RowStyles.Add(WF.RowStyle(WF.SizeType.AutoSize))
    right.RowStyles.Add(WF.RowStyle(WF.SizeType.Percent, 100))
    right.RowStyles.Add(WF.RowStyle(WF.SizeType.AutoSize))
    root.Controls.Add(right, 1, 0)

    set_title = Label()
    set_title.Text = u"2. 设置"
    set_title.AutoSize = True
    set_title.Margin = Padding(0, 0, 0, 4)
    right.Controls.Add(set_title, 0, 0)

    opts = TableLayoutPanel()
    opts.Dock = DockStyle.Fill
    opts.AutoSize = True
    opts.ColumnCount = 2
    opts.ColumnStyles.Add(WF.ColumnStyle(WF.SizeType.Absolute, 100))
    opts.ColumnStyles.Add(WF.ColumnStyle(WF.SizeType.Percent, 100))
    right.Controls.Add(opts, 0, 1)

    def add_row(label_text, control):
        lb = Label()
        lb.Text = label_text
        lb.TextAlign = SD.ContentAlignment.MiddleLeft
        lb.Dock = DockStyle.Fill
        lb.Margin = Padding(0, 4, 0, 4)
        opts.RowCount += 1
        opts.RowStyles.Add(WF.RowStyle(WF.SizeType.AutoSize))
        row = opts.RowCount - 1
        opts.Controls.Add(lb, 0, row)
        opts.Controls.Add(control, 1, row)

    def add_hint(text):
        hint = Label()
        hint.Text = text
        hint.AutoSize = True
        hint.ForeColor = GREY
        hint.Margin = Padding(0, 0, 0, 8)
        opts.RowCount += 1
        opts.RowStyles.Add(WF.RowStyle(WF.SizeType.AutoSize))
        row = opts.RowCount - 1
        opts.Controls.Add(Label(), 0, row)
        opts.Controls.Add(hint, 1, row)

    cb_name = ComboBox()
    cb_name.DropDownStyle = ComboBoxStyle.DropDown
    cb_name.Dock = DockStyle.Fill
    for p in NAME_PRESETS:
        cb_name.Items.Add(p)
    cb_name.Text = DEFAULT_NAME_TPL
    add_row(u"图纸名称", cb_name)
    add_hint(u"可用字段：" + TOKEN_HINT + u"\n下拉可选预设，也可以直接改。")

    cb_num = ComboBox()
    cb_num.DropDownStyle = ComboBoxStyle.DropDown
    cb_num.Dock = DockStyle.Fill
    for p in NUM_PRESETS:
        cb_num.Items.Add(p)
    cb_num.Text = DEFAULT_NUM_TPL
    add_row(u"图号", cb_num)
    add_hint(u"{系统} 是归类名，{系统码} 是归类的系统代码（在「归类设置」里改）。\n"
             u"不要中间的代码（TX、NT 这些）？把 {系统码} 删掉即可。")

    cb_per = CheckBox()
    cb_per.Text = u"每个归类单独编号（每个归类都从起始序号开始）"
    cb_per.Checked = True
    cb_per.AutoSize = True
    add_row(u"编号方式", cb_per)

    tb_start = TextBox()
    tb_start.Text = u"0"
    tb_start.Width = 90
    add_row(u"起始序号", tb_start)

    tb_digits = TextBox()
    tb_digits.Text = u"3"
    tb_digits.Width = 90
    add_row(u"序号位数", tb_digits)
    add_hint(u"位数 3 → 000、001……；填 0 则不补零。")

    cb_tb = ComboBox()
    cb_tb.DropDownStyle = ComboBoxStyle.DropDownList
    cb_tb.Dock = DockStyle.Fill
    cb_tb.Items.Add(u"（不替换，保持原图框）")
    tb_types = _titleblock_types()
    for t in tb_types:
        cb_tb.Items.Add(u"%s / %s" % (t.FamilyName, t.Name))
    cb_tb.SelectedIndex = 0
    add_row(u"目标图框", cb_tb)

    cb_sys = CheckBox()
    cb_sys.Text = u"写入「图纸系统」参数（项目浏览器按它分组）"
    cb_sys.Checked = True
    cb_sys.AutoSize = True
    add_row(u"分组参数", cb_sys)

    # 预览区
    prev_title = Label()
    prev_title.Text = u"3. 预览（旧图名  →  新图名    [新图号]）"
    prev_title.AutoSize = True
    prev_title.Margin = Padding(0, 10, 0, 4)
    right.Controls.Add(prev_title, 0, 2)

    lb_prev = ListBox()
    lb_prev.Dock = DockStyle.Fill
    right.Controls.Add(lb_prev, 0, 3)

    lb_note = Label()
    lb_note.AutoSize = True
    lb_note.ForeColor = GREY
    lb_note.MaximumSize = Size(640, 0)
    lb_note.Margin = Padding(0, 6, 0, 0)
    right.Controls.Add(lb_note, 0, 4)

    # ---------- 底部按钮（横跨两列，靠右） ----------
    btns = FlowLayoutPanel()
    btns.Dock = DockStyle.Fill
    btns.FlowDirection = FlowDirection.RightToLeft
    root.Controls.Add(btns, 0, 1)
    root.SetColumnSpan(btns, 2)

    btn_cancel = Button()
    btn_cancel.Text = u"取消"
    btn_cancel.Size = Size(96, 32)
    btn_cancel.DialogResult = DialogResult.Cancel
    btns.Controls.Add(btn_cancel)

    btn_ok = Button()
    btn_ok.Text = u"应用"
    btn_ok.Size = Size(120, 32)
    btns.Controls.Add(btn_ok)
    form.AcceptButton = btn_ok
    form.CancelButton = btn_cancel

    # ---------- 列表辅助 ----------
    def items_all():
        return [lv.Items[i] for i in range(lv.Items.Count)]

    def items_selected():
        return [lv.SelectedItems[i] for i in range(lv.SelectedItems.Count)]

    def group_list():
        return [lv.Groups[i] for i in range(lv.Groups.Count)]

    def group_named(name):
        for g in group_list():
            if g.Tag["name"] == name:
                return g
        return None

    def row_of(it):
        r = dict(it.Tag)
        g = it.Group
        r["sysname"] = g.Tag["name"]
        r["code"] = g.Tag["code"]
        r["gidx"] = lv.Groups.IndexOf(g)
        return r

    # 勾选变化时重算预览。busy 防止批量勾选时每张都重算一次。
    busy = [False]

    def on_item_checked(sender, args):
        if busy[0]:
            return
        recompute()

    def set_checked(items, value):
        busy[0] = True
        try:
            for it in items:
                it.Checked = value
        finally:
            busy[0] = False
        recompute()

    def move_selected_to(group):
        sel = items_selected()
        if not sel:
            WF.MessageBox.Show(u"先在列表里高亮选中要移动的图纸。", TITLE)
            return
        for it in sel:
            it.Group = group
        recompute()

    def make_move(group):
        def handler(sender, args):
            move_selected_to(group)
        return handler

    def on_move_menu(sender, args):
        cms = ContextMenuStrip()
        for g in group_list():
            mi = ToolStripMenuItem(g.Tag["name"])
            mi.Click += System.EventHandler(make_move(g))
            cms.Items.Add(mi)
        cms.Show(btn_move, Point(0, btn_move.Height))

    def on_new_group(sender, args):
        if not items_selected():
            WF.MessageBox.Show(u"先在列表里高亮选中要归到新归类的图纸，再点「新建归类」。", TITLE)
            return
        out = form_dialog(form, u"新建归类", [(u"归类名称 / 系统代码", [u"新归类", u"XX"])], [150, 90])
        if out is None:
            return
        name, code = out[0][0], out[0][1]
        if not name or not code:
            WF.MessageBox.Show(u"归类名称和系统代码都不能为空。", TITLE)
            return
        if group_named(name) is not None:
            WF.MessageBox.Show(u"已经有叫「%s」的归类了。" % name, TITLE)
            return
        g = ListViewGroup(name)
        g.Tag = {"name": name, "code": code}
        lv.Groups.Add(g)
        for it in items_selected():
            it.Group = g
        recompute()

    def on_group_settings(sender, args):
        groups = group_list()
        counts = {}
        for it in items_all():
            counts[it.Group.Tag["name"]] = counts.get(it.Group.Tag["name"], 0) + 1
        rows = [(u"%s（%d 张）" % (g.Tag["name"], counts.get(g.Tag["name"], 0)), [g.Tag["name"], g.Tag["code"]])
                for g in groups]
        out = form_dialog(form, u"归类设置（可改名字和系统代码）", rows, [150, 90])
        if out is None:
            return
        names = [r[0] for r in out]
        if not all(names):
            WF.MessageBox.Show(u"归类名称不能为空。", TITLE)
            return
        if len(set(names)) != len(names):
            WF.MessageBox.Show(u"归类名称有重复。", TITLE)
            return
        if not all([r[1] for r in out]):
            WF.MessageBox.Show(u"系统代码不能为空。", TITLE)
            return
        for g, r in zip(groups, out):
            g.Tag = {"name": r[0], "code": r[1]}
        recompute()

    def selected_rows():
        return [row_of(it) for it in items_all() if it.Checked]

    def recompute(sender=None, args=None):
        # 组标题上显示「已选 X / 共 Y」
        all_items = items_all()
        for g in group_list():
            members = [it for it in all_items if it.Group == g]
            picked = len([it for it in members if it.Checked])
            g.Header = u"%s    已选 %d / 共 %d" % (g.Tag["name"], picked, len(members))
        cfg, err = make_cfg(cb_name.Text, cb_num.Text, tb_start.Text, tb_digits.Text, cb_per.Checked)
        if err:
            lb_prev.Items.Clear()
            lb_note.ForeColor = Color.Firebrick
            lb_note.Text = err
            return
        rows = selected_rows()
        plan = build_plan(rows, cfg)
        chosen = set([int(r["sid"].Value) for r in rows])
        others = [(n, num) for (v, n, num) in sheet_info if v not in chosen]
        warns = plan_warnings(plan, set([n for n, _num in others]), set([num for _n, num in others]))
        lb_prev.Items.Clear()
        last_sys = None
        for name, sid, sysname, new_no, new_name in plan:
            if sysname != last_sys:
                lb_prev.Items.Add(u"── %s ──" % sysname)
                last_sys = sysname
            lb_prev.Items.Add(u"%s  →  %s    [%s]" % (name, new_name, new_no))
        tb_text = u"图框保持不变" if cb_tb.SelectedIndex == 0 else u"图框改为：%s" % cb_tb.SelectedItem
        text = u"共 %d 张图纸。%s。" % (len(plan), tb_text)
        for w in warns:
            text += u"\n注意：" + w
        lb_note.Text = text
        lb_note.ForeColor = Color.Firebrick if warns else GREY

    def on_ok(sender, args):
        cfg, err = make_cfg(cb_name.Text, cb_num.Text, tb_start.Text, tb_digits.Text, cb_per.Checked)
        if err:
            WF.MessageBox.Show(err, TITLE)
            return
        rows = selected_rows()
        if not rows:
            WF.MessageBox.Show(u"没有勾选图纸。", TITLE)
            return
        plan = build_plan(rows, cfg)
        chosen = set([int(r["sid"].Value) for r in rows])
        others = [(n, num) for (v, n, num) in sheet_info if v not in chosen]
        warns = plan_warnings(plan, set([n for n, _num in others]), set([num for _n, num in others]))
        if warns:
            ans = WF.MessageBox.Show(u"有几处需要注意：\n" + u"\n".join(warns) + u"\n\n仍然应用吗？",
                                     TITLE, MessageBoxButtons.YesNo)
            if ans != DialogResult.Yes:
                return
        result["plan"] = plan
        result["tb_index"] = cb_tb.SelectedIndex
        result["set_system"] = cb_sys.Checked
        form.DialogResult = DialogResult.OK

    lv.ItemChecked += WF.ItemCheckedEventHandler(on_item_checked)
    btn_all.Click += System.EventHandler(lambda s, a: set_checked(items_all(), True))
    btn_none.Click += System.EventHandler(lambda s, a: set_checked(items_all(), False))
    btn_check_sel.Click += System.EventHandler(lambda s, a: set_checked(items_selected(), True))
    btn_uncheck_sel.Click += System.EventHandler(lambda s, a: set_checked(items_selected(), False))
    btn_move.Click += System.EventHandler(on_move_menu)
    btn_new.Click += System.EventHandler(on_new_group)
    btn_settings.Click += System.EventHandler(on_group_settings)
    cb_name.TextChanged += recompute
    cb_num.TextChanged += recompute
    tb_start.TextChanged += recompute
    tb_digits.TextChanged += recompute
    cb_per.CheckedChanged += recompute
    cb_tb.SelectedIndexChanged += recompute
    btn_ok.Click += System.EventHandler(on_ok)
    recompute()
    return form, tb_types


def browser_steps_text():
    return (u"图纸已经写入「图纸系统」参数。请在 Revit 里做一次浏览器设置（只需做一次）：\n"
            u"1. 项目浏览器里右键「图纸（全部）」→「浏览器组织」。\n"
            u"2. 在「图纸」标签里：分组依据选「图纸系统」，排序依据选「图纸编号」。\n"
            u"3. 点「确定」。之后同一系统的图纸会自动归在一个文件夹里。")


def main():
    try:
        if not _sheets_wanted():
            forms.alert(u"模型里没有图纸。", title=TITLE)
            return
        result = {}
        form, tb_types = build_form(result)
        if form.ShowDialog() != WF.DialogResult.OK or "plan" not in result:
            return
        plan = result["plan"]
        tb_index = result.get("tb_index", 0)
        tb_type = tb_types[tb_index - 1] if tb_index > 0 else None
        notes = []
        set_system = bool(result.get("set_system", True))
        if set_system:
            ok, why = ensure_system_param()
            if not ok:
                set_system = False
                notes.append(u"「图纸系统」参数没能添加（%s），本次只改图号和图框，项目浏览器分组需要这个参数。" % why)
        done, fails = apply_plan(plan, tb_type, keep_tb=(tb_type is None), set_system=set_system)
        lines = [u"已处理 %d 张图纸，共 %d 张。" % (done, len(plan))]
        lines += notes
        if fails:
            lines.append(u"以下 %d 张没有改成功（已跳过，其它照常）：" % len(fails))
            lines += [u"· " + f for f in fails[:15]]
        if set_system and not notes:
            lines.append(u"")
            lines.append(browser_steps_text())
        forms.alert(u"\n".join(lines), title=TITLE)
    except Exception as e:
        forms.alert(u"图纸归类过程发生异常：\n%s\n\n%s" % (e, traceback.format_exc()), title=TITLE)


if __name__ == "__main__":
    main()
