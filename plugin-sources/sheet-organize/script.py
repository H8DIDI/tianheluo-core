# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "一键图纸归类\n与图框编号"
__author__ = "KAIFA"
__doc__ = ("把井展开图生成的图纸按系统归类（强电、弱电、通信、低压、预制、室内各组），\n"
           "统一改成「前缀 + 系统代码 + 序号」的图号，浏览器里同一系统的图纸排在一起；\n"
           "也可以统一替换图框类型。先在窗口里预览「旧图号 → 新图号」，确认后再应用。")

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
    """items：[(图名, 图纸id), ...]。返回 [(图名, 图纸id, 旧系统, 新图号), ...]（按新顺序）。
    per_system 为 True 时每个系统单独从 start 编号；否则全部连续编号。"""
    infos = []
    for name, sid in items:
        info = classify(name)
        infos.append((info, name, sid))
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
        info = classify(name)
        groups.setdefault(info["system"], []).append((name, sid))
    out = []
    for sysname in sorted(groups.keys(), key=lambda s: system_order.get(s, len(SYSTEM_MAP))):
        lst = sorted(groups[sysname], key=lambda t: sort_key(classify(t[0])))
        out.append((sysname, lst))
    return out


# ============================================================
# Revit：图纸、图框、应用
# ============================================================
def _titleblock_types():
    return list(DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                .OfClass(DB.FamilySymbol).ToElements())


def _sheets_wanted():
    sheets = list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements())
    out = []
    for s in sheets:
        try:
            if s.IsTemplate:
                continue
            out.append((s.Name, s.Id))
        except Exception:
            continue
    return out


def _titleblock_by_sheet():
    """每张图纸上的图框实例：{图纸id整数: 图框实例}。只收集一次。"""
    result = {}
    for tb in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_TitleBlocks) \
            .WhereElementIsNotElementType().ToElements():
        try:
            result[int(tb.OwnerViewId.Value)] = tb
        except Exception:
            continue
    return result


def apply_plan(plan, tb_type, keep_tb=False):
    """应用：先改图框（可选），再两遍改图号（先临时号，避免重号冲突）。每张图纸单独子事务，出错只跳过那一张。
    返回 (成功数, [失败说明])。"""
    tb_map = _titleblock_by_sheet()
    done = 0
    fails = []
    t = DB.Transaction(doc, u"图纸归类与图框编号")
    t.Start()
    try:
        # 第一遍：临时图号，避开已有图号
        temp_ok = []
        for name, sid, sysname, new_no in plan:
            st = DB.SubTransaction(doc)
            st.Start()
            try:
                sheet = doc.GetElement(sid)
                if not keep_tb and tb_type is not None:
                    tb = tb_map.get(int(sid.Value))
                    if tb is not None and tb.GetTypeId() != tb_type.Id:
                        tb.ChangeTypeId(tb_type.Id)
                sheet.SheetNumber = u"TMP-%d" % int(sid.Value)
                st.Commit()
                temp_ok.append((name, sid, new_no))
            except Exception as ex:
                if st.HasStarted() and not st.HasEnded():
                    st.RollBack()
                fails.append(u"%s：%s" % (name, ex))
        # 第二遍：正式图号
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
# 窗口：左边按系统分组勾选，右边设置与预览
# ============================================================
def build_form(result):
    from System.Windows.Forms import (Form, Label, TextBox, Button, ComboBox, ComboBoxStyle, CheckedListBox,
                                      ListBox, CheckBox, DialogResult, FormBorderStyle, FormStartPosition,
                                      AnchorStyles, DockStyle, Panel)
    from System.Drawing import Size, Point, Font

    FONT = Font(u"Microsoft YaHei UI", 9.0)
    form = Form()
    form.Text = TITLE
    form.Font = FONT
    form.StartPosition = FormStartPosition.CenterScreen
    form.FormBorderStyle = FormBorderStyle.Sizable
    form.MinimumSize = Size(980, 600)
    form.ClientSize = Size(1100, 660)

    items = _sheets_wanted()
    groups = group_by_system(items)

    left = Panel()
    left.Dock = DockStyle.Left
    left.Width = 430
    left.Padding = System.Windows.Forms.Padding(10)
    form.Controls.Add(left)
    lb_title = Label()
    lb_title.Text = u"选择要处理的图纸（按系统分组，默认全选）"
    lb_title.Location = Point(10, 10)
    lb_title.AutoSize = True
    left.Controls.Add(lb_title)
    clb = CheckedListBox()
    clb.Location = Point(10, 40)
    clb.Size = Size(405, 560)
    clb.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
    clb.CheckOnClick = True
    left.Controls.Add(clb)
    row_map = []
    for sysname, lst in groups:
        for name, sid in lst:
            # 默认只勾选井展开图生成的图纸，其它图纸默认不勾，避免误改
            clb.Items.Add(u"[%s] %s" % (sysname, name), (name or u"").startswith(SHEET_PREFIX_NAME))
            row_map.append((name, sid))
    override = {}

    right = Panel()
    right.Dock = DockStyle.Fill
    form.Controls.Add(right)

    def add_label(text, x, y):
        lb = Label()
        lb.Text = text
        lb.Location = Point(x, y + 3)
        lb.AutoSize = True
        right.Controls.Add(lb)

    add_label(u"图纸前缀", 16, 16)
    tb_prefix = TextBox()
    tb_prefix.Location = Point(120, 16)
    tb_prefix.Width = 160
    tb_prefix.Text = DEFAULT_PREFIX
    right.Controls.Add(tb_prefix)

    add_label(u"起始序号", 16, 52)
    tb_start = TextBox()
    tb_start.Location = Point(120, 52)
    tb_start.Width = 80
    tb_start.Text = u"1"
    right.Controls.Add(tb_start)

    cb_per = CheckBox()
    cb_per.Text = u"每个系统单独编号（系统代码 + 序号）"
    cb_per.Checked = True
    cb_per.Location = Point(16, 90)
    cb_per.AutoSize = True
    right.Controls.Add(cb_per)

    add_label(u"替换图框", 16, 130)
    cb_tb = ComboBox()
    cb_tb.DropDownStyle = ComboBoxStyle.DropDownList
    cb_tb.Location = Point(120, 126)
    cb_tb.Width = 420
    tb_types = _titleblock_types()
    cb_tb.Items.Add(u"（保持不变）")
    for t in tb_types:
        cb_tb.Items.Add(u"%s / %s" % (t.FamilyName, t.Name))
    cb_tb.SelectedIndex = 0
    right.Controls.Add(cb_tb)

    add_label(u"预览（旧图名 → 新图号）", 16, 170)
    lb_prev = ListBox()
    lb_prev.Location = Point(16, 196)
    lb_prev.Size = Size(560, 380)
    lb_prev.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
    right.Controls.Add(lb_prev)

    lb_note = Label()
    lb_note.Location = Point(16, 586)
    lb_note.Size = Size(560, 40)
    right.Controls.Add(lb_note)

    btn_ok = Button()
    btn_ok.Text = u"应用"
    btn_ok.Size = Size(120, 34)
    btn_ok.Location = Point(600, 600)
    btn_ok.Anchor = AnchorStyles.Bottom | AnchorStyles.Right
    right.Controls.Add(btn_ok)
    btn_cancel = Button()
    btn_cancel.Text = u"取消"
    btn_cancel.Size = Size(100, 34)
    btn_cancel.Location = Point(730, 600)
    btn_cancel.Anchor = AnchorStyles.Bottom | AnchorStyles.Right
    btn_cancel.DialogResult = DialogResult.Cancel
    right.Controls.Add(btn_cancel)
    form.AcceptButton = btn_ok
    form.CancelButton = btn_cancel

    def selected_items():
        out = []
        for i, (name, sid) in enumerate(row_map):
            checked = override[i] if i in override else clb.GetItemChecked(i)
            if checked:
                out.append((name, sid))
        return out

    def on_item_check(sender, args):
        # ItemCheck 在状态真正改变之前触发，这里直接用新值，保证预览马上跟上
        override[args.Index] = (args.NewValue == WF.CheckState.Checked)
        recompute()

    def recompute(sender=None, args=None):
        prefix = tb_prefix.Text or u""
        try:
            start = int(tb_start.Text)
        except Exception:
            lb_prev.Items.Clear()
            lb_note.Text = u"起始序号必须是整数"
            return
        sel = selected_items()
        plan = build_plan(sel, prefix, start, cb_per.Checked)
        lb_prev.Items.Clear()
        last_sys = None
        for name, sid, sysname, new_no in plan:
            if sysname != last_sys:
                lb_prev.Items.Add(u"── %s ──" % sysname)
                last_sys = sysname
            lb_prev.Items.Add(u"%s  →  %s" % (name, new_no))
        if cb_tb.SelectedIndex > 0:
            lb_note.Text = u"将替换图框为：%s。共 %d 张。" % (cb_tb.SelectedItem, len(plan))
        else:
            lb_note.Text = u"图框保持不变。共 %d 张。" % len(plan)

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
        form.DialogResult = DialogResult.OK

    tb_prefix.TextChanged += recompute
    tb_start.TextChanged += recompute
    cb_per.CheckedChanged += recompute
    cb_tb.SelectedIndexChanged += recompute
    clb.ItemCheck += System.Windows.Forms.ItemCheckEventHandler(on_item_check)
    btn_ok.Click += System.EventHandler(on_ok)
    recompute()
    return form, tb_types


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
        done, fails = apply_plan(plan, tb_type, keep_tb=(tb_type is None))
        lines = [u"已处理 %d 张图纸，共 %d 张。" % (done, len(plan))]
        if fails:
            lines.append(u"以下 %d 张没有改成功（已跳过，其它照常）：" % len(fails))
            lines += [u"· " + f for f in fails[:15]]
        forms.alert(u"\n".join(lines), title=TITLE)
    except Exception as e:
        forms.alert(u"图纸归类过程发生异常：\n%s\n\n%s" % (e, traceback.format_exc()), title=TITLE)


if __name__ == "__main__":
    main()
