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


# ============================================================
# 纯逻辑：识别、编号、分组（可离线测试）
# ============================================================
def classify(sheet_name):
    """从图名识别系统。返回 dict(series, system, code, num, order)；认不出的归到「其他」。"""
    s = sheet_name or u""
    if s.startswith(SHEET_PREFIX_NAME):
        s = s[len(SHEET_PREFIX_NAME):]
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


def build_plan(items, prefix=DEFAULT_PREFIX, start=1, per_system=True):
    """items：[(图名, 图纸id), ...]。返回 [(图名, 图纸id, 系统名, 新图号), ...]（按新顺序）。
    per_system 为 True 时每个系统单独从 start 编号；否则全部连续编号。"""
    infos = []
    for name, sid in items:
        infos.append((classify(name), name, sid))
    infos.sort(key=lambda t: sort_key(t[0]))
    counters = {}
    plan = []
    for info, name, sid in infos:
        key = info["code"] if per_system else u"ALL"
        seq = counters.get(key, start)
        counters[key] = seq + 1
        if per_system:
            new_no = u"%s%s-%03d" % (prefix, info["code"], seq)
        else:
            new_no = u"%s%03d" % (prefix, seq)
        plan.append((name, sid, info["system"], new_no))
    return plan


def group_by_system(items):
    """按系统名分组（AA 和 A 都是强电井，合成一组），返回 [(系统名, [(图名, 图纸id), ...]), ...]。
    组的顺序与 SYSTEM_MAP 一致，「其他」放最后。"""
    system_order = {}
    for i, (_prefix, sysname, _code) in enumerate(SYSTEM_MAP):
        system_order.setdefault(sysname, i)
    system_order.setdefault(OTHER[0], len(SYSTEM_MAP))
    groups = {}
    for name, sid in items:
        groups.setdefault(classify(name)["system"], []).append((name, sid))
    out = []
    for sysname in sorted(groups.keys(), key=lambda s: system_order.get(s, len(SYSTEM_MAP))):
        out.append((sysname, sorted(groups[sysname], key=lambda t: sort_key(classify(t[0])))))
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
    """应用：改图框（可选）、写「图纸系统」参数（可选）、两遍改图号（先临时号避开冲突）。
    每张图纸单独子事务，出错只跳过那一张。返回 (成功数, [失败说明])。"""
    tb_map = _titleblock_by_sheet()
    done = 0
    fails = []
    t = DB.Transaction(doc, u"图纸归类与图框编号")
    t.Start()
    try:
        temp_ok = []
        for name, sid, sysname, new_no in plan:
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
                sheet.SheetNumber = u"TMP-%d" % int(sid.Value)
                st.Commit()
                temp_ok.append((name, sid, new_no))
            except Exception as ex:
                if st.HasStarted() and not st.HasEnded():
                    st.RollBack()
                fails.append(u"%s：%s" % (name, ex))
        for name, sid, new_no in temp_ok:
            st = DB.SubTransaction(doc)
            st.Start()
            try:
                doc.GetElement(sid).SheetNumber = new_no
                st.Commit()
                done += 1
            except Exception as ex:
                if st.HasStarted() and not st.HasEnded():
                    st.RollBack()
                fails.append(u"%s 改号为 %s 失败：%s" % (name, new_no, ex))
        t.Commit()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise ex
    return done, fails


# ============================================================
# 窗口：停靠式布局（不用绝对坐标，窗口拉大缩小都不会把按钮挡住）
#   左：图纸列表（按系统分组，可勾选）
#   右上：设置    右中：预览    右下：说明 + 按钮行
# ============================================================
def build_form(result):
    from System.Windows.Forms import (Form, Label, TextBox, Button, ComboBox, ComboBoxStyle, CheckedListBox,
                                      ListBox, CheckBox, DialogResult, FormBorderStyle, FormStartPosition,
                                      DockStyle, FlowLayoutPanel, FlowDirection, TableLayoutPanel, Padding,
                                      AnchorStyles)
    from System.Drawing import Size, Point, Font, Color

    FONT = Font(u"Microsoft YaHei UI", 9.0)
    form = Form()
    form.Text = TITLE
    form.Font = FONT
    form.StartPosition = FormStartPosition.CenterScreen
    form.FormBorderStyle = FormBorderStyle.Sizable
    form.MinimumSize = Size(1000, 620)
    form.ClientSize = Size(1120, 680)

    # ---------- 左：图纸列表 ----------
    left = TableLayoutPanel()
    left.Dock = DockStyle.Left
    left.Width = 430
    left.ColumnCount = 1
    left.RowCount = 3
    left.Padding = Padding(10)
    left.RowStyles.Add(WF.RowStyle(WF.SizeType.Absolute, 28))
    left.RowStyles.Add(WF.RowStyle(WF.SizeType.Percent, 100))
    left.RowStyles.Add(WF.RowStyle(WF.SizeType.Absolute, 34))
    form.Controls.Add(left)

    lb_title = Label()
    lb_title.Text = u"选择图纸（按系统分组，默认勾井大样）"
    lb_title.Dock = DockStyle.Fill
    lb_title.TextAlign = SD.ContentAlignment.MiddleLeft
    left.Controls.Add(lb_title, 0, 0)

    clb = CheckedListBox()
    clb.Dock = DockStyle.Fill
    clb.CheckOnClick = True
    left.Controls.Add(clb, 0, 1)

    sel_row = FlowLayoutPanel()
    sel_row.Dock = DockStyle.Fill
    sel_row.FlowDirection = FlowDirection.LeftToRight
    left.Controls.Add(sel_row, 0, 2)

    items = _sheets_wanted()
    groups = group_by_system(items)
    row_map = []
    for sysname, lst in groups:
        for name, sid in lst:
            clb.Items.Add(u"[%s] %s" % (sysname, name), (name or u"").startswith(SHEET_PREFIX_NAME))
            row_map.append((name, sid))
    override = {}

    # ---------- 右：设置 + 预览 + 底部按钮 ----------
    right = TableLayoutPanel()
    right.Dock = DockStyle.Fill
    right.ColumnCount = 1
    right.RowCount = 4
    right.Padding = Padding(12)
    right.RowStyles.Add(WF.RowStyle(WF.SizeType.AutoSize))
    right.RowStyles.Add(WF.RowStyle(WF.SizeType.AutoSize))
    right.RowStyles.Add(WF.RowStyle(WF.SizeType.Percent, 100))
    right.RowStyles.Add(WF.RowStyle(WF.SizeType.Absolute, 92))
    form.Controls.Add(right)

    # 设置区：两列，标签在左、控件在右
    opts = TableLayoutPanel()
    opts.Dock = DockStyle.Top
    opts.AutoSize = True
    opts.ColumnCount = 2
    opts.ColumnStyles.Add(WF.ColumnStyle(WF.SizeType.Absolute, 92))
    opts.ColumnStyles.Add(WF.ColumnStyle(WF.SizeType.Percent, 100))
    right.Controls.Add(opts, 0, 0)

    def add_row(label_text, control):
        lb = Label()
        lb.Text = label_text
        lb.TextAlign = SD.ContentAlignment.MiddleLeft
        lb.Dock = DockStyle.Fill
        opts.RowCount += 1
        opts.Controls.Add(lb)
        opts.Controls.Add(control)

    tb_prefix = TextBox()
    tb_prefix.Text = DEFAULT_PREFIX
    tb_prefix.Width = 200
    add_row(u"图号前缀", tb_prefix)

    tb_start = TextBox()
    tb_start.Text = u"1"
    tb_start.Width = 90
    add_row(u"起始序号", tb_start)

    cb_per = CheckBox()
    cb_per.Text = u"每个系统单独编号（图号 = 前缀 + 系统代码 + 序号）"
    cb_per.Checked = True
    cb_per.AutoSize = True
    add_row(u"编号方式", cb_per)

    cb_tb = ComboBox()
    cb_tb.DropDownStyle = ComboBoxStyle.DropDownList
    cb_tb.Width = 380
    cb_tb.Items.Add(u"（保持不变）")
    tb_types = _titleblock_types()
    for t in tb_types:
        cb_tb.Items.Add(u"%s / %s" % (t.FamilyName, t.Name))
    cb_tb.SelectedIndex = 0
    add_row(u"替换图框", cb_tb)

    cb_sys = CheckBox()
    cb_sys.Text = u"写入「图纸系统」参数（项目浏览器按它分组）"
    cb_sys.Checked = True
    cb_sys.AutoSize = True
    add_row(u"分组参数", cb_sys)

    # 预览区
    prev_title = Label()
    prev_title.Text = u"预览（旧图名  →  新图号）"
    prev_title.AutoSize = True
    prev_title.Margin = Padding(0, 8, 0, 4)
    right.Controls.Add(prev_title, 0, 1)

    lb_prev = ListBox()
    lb_prev.Dock = DockStyle.Fill
    right.Controls.Add(lb_prev, 0, 2)

    # 底部：说明 + 按钮行（按钮靠右）
    bottom = TableLayoutPanel()
    bottom.Dock = DockStyle.Fill
    bottom.ColumnCount = 1
    bottom.RowCount = 2
    bottom.RowStyles.Add(WF.RowStyle(WF.SizeType.Percent, 100))
    bottom.RowStyles.Add(WF.RowStyle(WF.SizeType.Absolute, 40))
    right.Controls.Add(bottom, 0, 3)

    lb_note = Label()
    lb_note.Dock = DockStyle.Fill
    lb_note.ForeColor = Color.FromArgb(70, 70, 70)
    bottom.Controls.Add(lb_note, 0, 0)

    btns = FlowLayoutPanel()
    btns.Dock = DockStyle.Fill
    btns.FlowDirection = FlowDirection.RightToLeft
    bottom.Controls.Add(btns, 0, 1)

    btn_cancel = Button()
    btn_cancel.Text = u"取消"
    btn_cancel.Size = Size(96, 30)
    btn_cancel.DialogResult = DialogResult.Cancel
    btns.Controls.Add(btn_cancel)

    btn_ok = Button()
    btn_ok.Text = u"应用"
    btn_ok.Size = Size(120, 30)
    btns.Controls.Add(btn_ok)
    form.AcceptButton = btn_ok
    form.CancelButton = btn_cancel

    sel_all = Button()
    sel_all.Text = u"全选"
    sel_all.Size = Size(70, 28)
    sel_row.Controls.Add(sel_all)
    sel_none = Button()
    sel_none.Text = u"全不选"
    sel_none.Size = Size(80, 28)
    sel_row.Controls.Add(sel_none)

    def selected_items():
        out = []
        for i, (name, sid) in enumerate(row_map):
            checked = override[i] if i in override else clb.GetItemChecked(i)
            if checked:
                out.append((name, sid))
        return out

    def on_item_check(sender, args):
        override[args.Index] = (args.NewValue == WF.CheckState.Checked)
        recompute()

    def set_all(value):
        def handler(sender, args):
            for i in range(clb.Items.Count):
                override[i] = value
                clb.SetItemChecked(i, value)
            recompute()
        return handler

    def recompute(sender=None, args=None):
        try:
            start = int(tb_start.Text)
        except Exception:
            lb_prev.Items.Clear()
            lb_note.Text = u"起始序号必须是整数"
            return
        plan = build_plan(selected_items(), tb_prefix.Text or u"", start, cb_per.Checked)
        lb_prev.Items.Clear()
        last_sys = None
        for name, sid, sysname, new_no in plan:
            if sysname != last_sys:
                lb_prev.Items.Add(u"── %s ──" % sysname)
                last_sys = sysname
            lb_prev.Items.Add(u"%s  →  %s" % (name, new_no))
        tb_text = u"图框保持不变" if cb_tb.SelectedIndex == 0 else u"图框改为：%s" % cb_tb.SelectedItem
        lb_note.Text = u"共 %d 张图纸。%s。" % (len(plan), tb_text)

    def on_ok(sender, args):
        try:
            start = int(tb_start.Text)
        except Exception:
            WF.MessageBox.Show(u"起始序号必须是整数", TITLE)
            return
        sel = selected_items()
        if not sel:
            WF.MessageBox.Show(u"没有勾选图纸。", TITLE)
            return
        result["plan"] = build_plan(sel, tb_prefix.Text or u"", start, cb_per.Checked)
        result["tb_index"] = cb_tb.SelectedIndex
        result["set_system"] = cb_sys.Checked
        form.DialogResult = DialogResult.OK

    clb.ItemCheck += WF.ItemCheckEventHandler(on_item_check)
    sel_all.Click += System.EventHandler(set_all(True))
    sel_none.Click += System.EventHandler(set_all(False))
    tb_prefix.TextChanged += recompute
    tb_start.TextChanged += recompute
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
