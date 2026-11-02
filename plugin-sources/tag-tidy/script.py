# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '构件标记\n一键整理'
__author__ = 'didi'
__doc__ = ('一键整理当前平面视图里的构件标记（出图用）：\n'
           '① 一根构件只留一个标记（重复的可删），引线末端设到它自己构件的中心（线式取线段中点），不会再指到别的构件；标记方向跟管道平行（竖管竖排）；\n'
           '② 每个标记自动选空的一侧（8 个方向），不压别的标记、不压别的构件；放不下就沿引线引出去；\n'
           '③ 同一侧的标记对齐到同一行/同一列；\n'
           '④ 悬空标记（构件没了）可以删除；视图里该标但没标的构件可以补标；\n'
           '写入后逐个回读核对，引线末端不在自己构件上的整次回滚。整次一个事务，Ctrl+Z 可撤销。\n'
           '标记离构件的距离、引线上限、对齐容差、是否补标等都能在「设置…」里改。')

import io
import os
import json
import math
import time
import traceback
import clr
import System
clr.AddReference('RevitAPI')
from Autodesk.Revit import DB
from System.Collections.Generic import List
from pyrevit import revit, forms

# ---- form_fit（天河落共用片段，打包时内联进脚本）--------------------------------
# 让手工摆坐标的 WinForms 窗口在高分屏（125%、150% 缩放）上不截字、能拖大小。
# 插件窗口原来都按 100% 缩放的像素写死：缩放 150% 时字变大了框没变，文字就被截掉。
#   1. 按屏幕 DPI 等比放大全部控件（.NET 不管的列表列宽、表格行高另外放大）；
#   2. 放不下的说明文字、勾选项自动换行，下面的控件依次下移；
#   3. 固定边框改成可拖动：列表/表格/预览区跟着窗口变大，底部按钮贴底，右侧按钮贴右，输入框横向拉伸；
#   4. 不超过屏幕可用区域，屏幕太小时出滚动条。
# 用法：form.ShowDialog() 之前调用 ff_fit(form)。已经可拖动、自己排好版的窗口只做第 1 步。
import clr as _ff_clr
_ff_clr.AddReference('System.Windows.Forms')
_ff_clr.AddReference('System.Drawing')
import System.Windows.Forms as _FFW
import System.Drawing as _FFD

_FF_DOCK_NONE = getattr(_FFW.DockStyle, 'None')
_FF_SCALE_NONE = getattr(_FFW.AutoScaleMode, 'None')
_FF_A = _FFW.AnchorStyles
_FF_FIXED = (_FFW.FormBorderStyle.FixedDialog, _FFW.FormBorderStyle.FixedSingle,
             _FFW.FormBorderStyle.Fixed3D, _FFW.FormBorderStyle.FixedToolWindow)
_FF_WRAP_FLAGS = (_FFW.TextFormatFlags.WordBreak | _FFW.TextFormatFlags.TextBoxControl)


def _ff_kids(parent):
    return [c for c in parent.Controls]


def _ff_walk(parent):
    for c in parent.Controls:
        yield c
        for x in _ff_walk(c):
            yield x


def _ff_dpi(f):
    try:
        d = float(f.DeviceDpi)
        if d > 0:
            return d
    except Exception:
        pass
    g = f.CreateGraphics()
    try:
        return float(g.DpiX)
    finally:
        g.Dispose()


def _ff_scale(f, k):
    """整窗等比放大。窗口自己设了 AutoScaleDimensions 的（会自动缩放）不重复放大。"""
    try:
        # 只有写明了设计时的 DPI（AutoScaleDimensions，且和当前不同）窗口才会自己缩放；
        # 只设 AutoScaleMode 不设它，Revit 里其实不会缩放
        asd = f.AutoScaleDimensions
        if f.AutoScaleMode != _FF_SCALE_NONE and not asd.IsEmpty and \
                abs(asd.Width - f.CurrentAutoScaleDimensions.Width) > 0.5:
            return False
    except Exception:
        pass
    mn, mx = f.MinimumSize, f.MaximumSize
    f.MinimumSize = _FFD.Size.Empty
    f.MaximumSize = _FFD.Size.Empty
    f.AutoScaleMode = _FF_SCALE_NONE
    f.Scale(_FFD.SizeF(k, k))      # 控件位置/大小、表格布局的固定列宽、最小最大尺寸由 .NET 一起放大
    if not mn.IsEmpty:
        f.MinimumSize = _FFD.Size(int(mn.Width * k), int(mn.Height * k))
    if not mx.IsEmpty:
        f.MaximumSize = _FFD.Size(int(mx.Width * k), int(mx.Height * k))
    for c in _ff_walk(f):
        try:
            if isinstance(c, _FFW.ListView):
                for col in c.Columns:
                    if col.Width > 0:
                        col.Width = int(col.Width * k)
            elif isinstance(c, _FFW.DataGridView):
                for col in c.Columns:
                    if col.AutoSizeMode != _FFW.DataGridViewAutoSizeColumnMode.Fill:
                        col.Width = int(col.Width * k)
                        if col.MinimumWidth > 2:
                            col.MinimumWidth = max(2, int(col.MinimumWidth * k))
                c.RowTemplate.Height = int(c.RowTemplate.Height * k)
                for r in c.Rows:
                    r.Height = max(r.Height, c.RowTemplate.Height)
                if c.ColumnHeadersHeightSizeMode != _FFW.DataGridViewColumnHeadersHeightSizeMode.AutoSize:
                    c.ColumnHeadersHeight = int(c.ColumnHeadersHeight * k)
                if c.RowHeadersVisible:
                    c.RowHeadersWidth = max(4, int(c.RowHeadersWidth * k))
        except Exception:
            pass
    return True


def _ff_absolute(parent):
    """容器里的控件是手工摆坐标的（不是表格/流式布局、也不是停靠）。"""
    if isinstance(parent, (_FFW.TableLayoutPanel, _FFW.FlowLayoutPanel)):
        return False
    kids = _ff_kids(parent)
    return bool(kids) and all(c.Dock == _FF_DOCK_NONE for c in kids)


def _ff_grow(ctrl, delta, f):
    """ctrl 变高了 delta：它下面的兄弟控件下移，父容器跟着变高（一直传到窗口）。"""
    parent = ctrl.Parent
    if parent is None or delta <= 0:
        return
    old_bottom = ctrl.Bottom - delta
    for s in _ff_kids(parent):
        if s is not ctrl and s.Dock == _FF_DOCK_NONE and s.Top >= old_bottom - 3:
            s.Top += delta
    if isinstance(parent, _FFW.Form):
        parent.ClientSize = _FFD.Size(parent.ClientSize.Width, parent.ClientSize.Height + delta)
    elif getattr(parent, 'AutoScroll', False) or parent.Dock != _FF_DOCK_NONE or isinstance(parent, _FFW.TabPage):
        return
    else:
        parent.Height += delta
        _ff_grow(parent, delta, f)


def _ff_reflow(parent, k, f):
    """放不下的文字：自动换行、加高，下面的控件顺延；按钮文字放不下就加宽。"""
    if not _ff_absolute(parent):
        for c in _ff_kids(parent):
            if c.Controls.Count:
                _ff_reflow(c, k, f)
        return
    for c in sorted(_ff_kids(parent), key=lambda x: (x.Top, x.Left)):
        text = c.Text or u''
        try:
            if isinstance(c, (_FFW.Label, _FFW.CheckBox, _FFW.RadioButton)) and text and not isinstance(c, _FFW.LinkLabel):
                pw = parent.ClientSize.Width
                if c.AutoSize:
                    one = _FFW.TextRenderer.MeasureText(text, c.Font)
                    if c.Left + one.Width <= pw - 2:
                        continue
                    c.AutoSize = False
                    c.Width = max(40, pw - c.Left - int(8 * k))
                    c.Height = one.Height + int(4 * k)
                box = int(20 * k) if isinstance(c, (_FFW.CheckBox, _FFW.RadioButton)) else int(2 * k)
                avail = max(20, c.Width - box - c.Padding.Horizontal - 2)
                need = _FFW.TextRenderer.MeasureText(text, c.Font, _FFD.Size(avail, 100000), _FF_WRAP_FLAGS).Height
                need += c.Padding.Vertical + int(4 * k)
                if need > c.Height + 1:
                    delta = need - c.Height
                    c.Height = need
                    if isinstance(c, (_FFW.CheckBox, _FFW.RadioButton)):
                        c.CheckAlign = _FFD.ContentAlignment.TopLeft
                        c.TextAlign = _FFD.ContentAlignment.TopLeft
                    _ff_grow(c, delta, f)
            elif isinstance(c, _FFW.Button) and text and not c.AutoSize:
                need = _FFW.TextRenderer.MeasureText(text, c.Font).Width + int(18 * k)
                if need > c.Width:
                    d = need - c.Width
                    if c.Left > parent.ClientSize.Width // 2:
                        c.Left -= d
                    c.Width = need
            elif c.Controls.Count and not isinstance(c, (_FFW.DataGridView, _FFW.ListView, _FFW.NumericUpDown, _FFW.ComboBox)):
                _ff_reflow(c, k, f)
        except Exception:
            pass


def _ff_is_grow(c):
    if isinstance(c, (_FFW.ListView, _FFW.DataGridView, _FFW.TreeView, _FFW.ListBox, _FFW.RichTextBox,
                      _FFW.PictureBox, _FFW.TabControl, _FFW.SplitContainer)):
        return True
    if isinstance(c, _FFW.TextBox) and c.Multiline:
        return True
    if isinstance(c, _FFW.Panel) and not isinstance(c, (_FFW.FlowLayoutPanel, _FFW.TableLayoutPanel)):
        return bool(c.AutoScroll) or c.Controls.Count == 0      # 可滚动的内容区，或代码自绘的预览区
    return False


def _ff_has(a, flag):
    return int(a & flag) != 0


def _ff_vover(a, b):
    return a.Top < b.Bottom and b.Top < a.Bottom


def _ff_anchor(parent, k, vertical=True):
    """按位置给控件定锚点：最大的列表/表格/预览区随窗口变大，它下面的控件贴底；贴右边的拉伸或贴右。"""
    if not _ff_absolute(parent):
        return
    cw, ch = parent.ClientSize.Width, parent.ClientSize.Height
    kids = _ff_kids(parent)
    edge = max(int(30 * k), int(cw * 0.05))
    grow = None
    if vertical:
        cand = [c for c in kids if _ff_is_grow(c) and c.Width * c.Height >= 0.10 * cw * ch]
        if cand:
            grow = max(cand, key=lambda c: c.Width * c.Height)
    rows_grow = [c for c in kids if grow is not None and c is not grow and _ff_is_grow(c) and _ff_vover(c, grow)
                 and c.Height >= grow.Height * 0.6]
    for c in kids:
        a = _FF_A.Top
        if vertical:
            if c is grow or c in rows_grow:
                a = _FF_A.Top | _FF_A.Bottom
            elif grow is not None and c.Top >= grow.Bottom - 2:
                a = _FF_A.Bottom
            elif grow is None and isinstance(c, _FFW.Button) and c.Top >= ch - int(64 * k):
                a = _FF_A.Bottom
        right_gap = cw - c.Right
        right_of = [o for o in kids if o is not c and o.Left >= c.Right - 2 and _ff_vover(o, c)]
        small = isinstance(c, (_FFW.Button, _FFW.CheckBox, _FFW.RadioButton)) or c.Width < cw * 0.25
        if right_of and not small and all(isinstance(o, _FFW.Button) or o.Width < cw * 0.3 for o in right_of) \
                and cw - max(o.Right for o in right_of) <= edge and not any(o in rows_grow or o is grow for o in right_of):
            a |= _FF_A.Left | _FF_A.Right
        elif right_gap <= edge and not small and c.Width >= cw * 0.3:
            a |= _FF_A.Left | _FF_A.Right
        elif c.Left > cw * 0.5 and (right_gap <= edge or isinstance(c, _FFW.Button) or
                                    any(o.Right >= cw - edge for o in right_of)):
            a |= _FF_A.Right
        else:
            a |= _FF_A.Left
        c.Anchor = a
        stretched_w = _ff_has(a, _FF_A.Left) and _ff_has(a, _FF_A.Right)
        stretched_h = _ff_has(a, _FF_A.Top) and _ff_has(a, _FF_A.Bottom)
        if isinstance(c, _FFW.TabControl):
            for p in c.TabPages:
                _ff_anchor(p, k, True)
        elif isinstance(c, _FFW.SplitContainer):
            _ff_anchor(c.Panel1, k, True)
            _ff_anchor(c.Panel2, k, True)
        elif (stretched_w or stretched_h) and isinstance(c, (_FFW.Panel, _FFW.GroupBox)) \
                and not isinstance(c, (_FFW.TableLayoutPanel, _FFW.FlowLayoutPanel)):
            _ff_anchor(c, k, stretched_h and not getattr(c, 'AutoScroll', False))


def ff_fit(f):
    """在 ShowDialog() 前调用。出错时什么都不改，窗口照常弹出。"""
    try:
        k = _ff_dpi(f) / 96.0
        fixed = f.FormBorderStyle in _FF_FIXED
        if k > 1.01:
            _ff_scale(f, k)
        if fixed:
            _ff_reflow(f, max(k, 1.0), f)
            _ff_anchor(f, max(k, 1.0), True)
            f.FormBorderStyle = _FFW.FormBorderStyle.Sizable
            f.SizeGripStyle = _FFW.SizeGripStyle.Show
            f.MaximizeBox = True
        try:
            wa = _FFW.Screen.FromPoint(_FFW.Cursor.Position).WorkingArea
        except Exception:
            wa = _FFW.Screen.PrimaryScreen.WorkingArea
        design = f.ClientSize
        if fixed:
            f.AutoScrollMinSize = design      # 屏幕放不下时出滚动条，控件不挤在一起
            f.AutoScroll = True
            f.MinimumSize = _FFD.Size(min(f.Width, wa.Width), min(f.Height, wa.Height))
        if f.Width > wa.Width or f.Height > wa.Height:
            f.AutoScroll = True
            f.Size = _FFD.Size(min(f.Width, wa.Width), min(f.Height, wa.Height))
            if not f.MinimumSize.IsEmpty:
                f.MinimumSize = _FFD.Size(min(f.MinimumSize.Width, wa.Width), min(f.MinimumSize.Height, wa.Height))
    except Exception:
        pass
    return f
# ---- form_fit 结束 ----------------------------------------------------------------

# ---- settings_ui（天河落共用片段，打包时内联进脚本）-----------------------------
# 图形化「设置」窗口：每个参数一行（中文名、输入框/下拉框/勾选、单位和说明），保存、恢复默认、取消。
# 依赖 form_fit（插件里要先 include _shared/form_fit.py）。
# 用法：su_fields_dialog(标题, 说明, 字段列表, 当前值, 默认值) -> 新值字典 或 None（取消）。
# 字段 dict：key、label、kind（header 分组标题 / num 数字 / text 文本 / list 逗号分隔的列表 / choice 下拉（只能选）/
#           combo 下拉（可选可手填）/ bool 勾选 / palette 颜色列表「R,G,B; R,G,B」/ lines 多行文本，每行一项，rows 行高）、hint 说明、
#           choices [(值, 显示文字), ...]、min、max、int（数字取整）。
import re as _su_re
import clr as _su_clr
_su_clr.AddReference('System.Windows.Forms')
_su_clr.AddReference('System.Drawing')
import System.Windows.Forms as _SUW
import System.Drawing as _SUD
import System.Diagnostics as _SUDG


def su_open_file(path):
    """用记事本打开设置文件。"""
    try:
        _SUDG.Process.Start(u"notepad.exe", path)
    except Exception:
        pass


def _su_split(text):
    return [x.strip() for x in _su_re.split(u"[,，;；\\n\\r]+", text or u"") if x.strip()]


def _su_to_text(fld, val):
    kind = fld.get(u"kind", u"text")
    if kind == u"list":
        return u", ".join(u"%s" % x for x in (val or []))
    if kind == u"lines":
        return u"\r\n".join(u"%s" % x for x in (val or []))
    if kind == u"palette":
        return u"; ".join(u"%d,%d,%d" % tuple(c[:3]) for c in (val or []))
    if kind == u"num":
        try:
            v = float(val)
            return (u"%d" % v) if v == int(v) else (u"%g" % v)
        except Exception:
            return u"" if val is None else u"%s" % val
    return u"" if val is None else u"%s" % val


def su_fields_dialog(title, intro, fields, values, defaults=None, extra_buttons=None, width=680, height=640):
    """弹出设置窗口。extra_buttons: [(按钮文字, 回调)]，回调不带参数（如「用记事本打开文件」）。返回新值 dict 或 None。"""
    f = _SUW.Form()
    f.Text = title
    f.Font = _SUD.Font(u"Microsoft YaHei UI", 9.0)
    f.StartPosition = _SUW.FormStartPosition.CenterScreen
    f.FormBorderStyle = _SUW.FormBorderStyle.FixedDialog
    f.MaximizeBox = False
    f.MinimizeBox = False
    f.ClientSize = _SUD.Size(width, height)
    lb = _SUW.Label()
    lb.Text = intro
    lb.Location = _SUD.Point(14, 10)
    lb.Size = _SUD.Size(width - 28, 44)
    f.Controls.Add(lb)
    panel = _SUW.Panel()
    panel.Location = _SUD.Point(10, 58)
    panel.Size = _SUD.Size(width - 20, height - 58 - 54)
    panel.AutoScroll = True
    panel.BorderStyle = _SUW.BorderStyle.FixedSingle
    f.Controls.Add(panel)

    ctrls = {}
    y = 8
    for fld in fields:
        kind = fld.get(u"kind", u"text")
        if kind == u"header":
            l = _SUW.Label()
            l.Text = fld[u"label"]
            l.Font = _SUD.Font(f.Font, _SUD.FontStyle.Bold)
            l.Location = _SUD.Point(6, y + 6)
            l.Size = _SUD.Size(width - 60, 22)
            panel.Controls.Add(l)
            y += 34
            continue
        l = _SUW.Label()
        l.Text = fld[u"label"]
        l.Location = _SUD.Point(6, y + 3)
        l.Size = _SUD.Size(250, 34)
        panel.Controls.Add(l)
        key = fld[u"key"]
        if kind in (u"choice", u"combo"):
            c = _SUW.ComboBox()
            c.DropDownStyle = _SUW.ComboBoxStyle.DropDownList if kind == u"choice" else _SUW.ComboBoxStyle.DropDown
            for _v, _t in fld.get(u"choices", []):
                c.Items.Add(_t)
            c.Location = _SUD.Point(264, y)
            c.Size = _SUD.Size(width - 310, 24)
        elif kind == u"bool":
            c = _SUW.CheckBox()
            c.Text = fld.get(u"bool_text", u"启用")
            c.Location = _SUD.Point(264, y + 2)
            c.Size = _SUD.Size(width - 310, 22)
        elif kind == u"lines":
            c = _SUW.TextBox()
            c.Multiline = True
            c.ScrollBars = _SUW.ScrollBars.Both
            c.WordWrap = False
            c.AcceptsReturn = True
            c.Font = _SUD.Font(u"Consolas", 9.0)
            rows = int(fld.get(u"rows", 6))
            c.Location = _SUD.Point(264, y)
            c.Size = _SUD.Size(width - 310, rows * 18 + 8)
        else:
            c = _SUW.TextBox()
            c.Location = _SUD.Point(264, y)
            c.Size = _SUD.Size(width - 310, 24)
        panel.Controls.Add(c)
        ctrls[key] = c
        y += (int(fld.get(u"rows", 6)) * 18 + 14) if kind == u"lines" else 30
        if fld.get(u"hint"):
            h = _SUW.Label()
            h.Text = fld[u"hint"]
            h.ForeColor = _SUD.Color.FromArgb(110, 110, 110)
            h.Location = _SUD.Point(264, y - 2)
            h.Size = _SUD.Size(width - 310, 34)
            panel.Controls.Add(h)
            y += 36
        y += 6

    def put(fld, val):
        c = ctrls.get(fld[u"key"])
        if c is None:
            return
        kind = fld.get(u"kind", u"text")
        if kind == u"bool":
            c.Checked = bool(val)
        elif kind in (u"choice", u"combo"):
            chs = fld.get(u"choices", [])
            idx = [i for i, (v, t) in enumerate(chs) if v == val]
            if idx:
                c.SelectedIndex = idx[0]
            elif kind == u"combo":
                c.Text = u"" if val is None else u"%s" % val
            elif chs:
                c.SelectedIndex = 0
        else:
            c.Text = _su_to_text(fld, val)

    def get(fld):
        """读控件的值并校验；不合格抛 ValueError(说明)。"""
        c = ctrls[fld[u"key"]]
        kind = fld.get(u"kind", u"text")
        name = fld[u"label"]
        if kind == u"bool":
            return bool(c.Checked)
        if kind == u"choice":
            chs = fld.get(u"choices", [])
            return chs[c.SelectedIndex][0] if 0 <= c.SelectedIndex < len(chs) else None
        if kind == u"combo":
            txt = (c.Text or u"").strip()
            for v, t in fld.get(u"choices", []):
                if t == txt:
                    return v
            return txt
        txt = (c.Text or u"").strip()
        if kind == u"num":
            try:
                v = float(txt)
            except Exception:
                raise ValueError(u"「%s」要填数字。" % name)
            if fld.get(u"min") is not None and v < fld[u"min"]:
                raise ValueError(u"「%s」不能小于 %s。" % (name, fld[u"min"]))
            if fld.get(u"max") is not None and v > fld[u"max"]:
                raise ValueError(u"「%s」不能大于 %s。" % (name, fld[u"max"]))
            return int(round(v)) if fld.get(u"int") else v
        if kind == u"list":
            return _su_split(txt)
        if kind == u"lines":
            return [x.strip() for x in (c.Text or u"").splitlines() if x.strip()]
        if kind == u"palette":
            out = []
            for part in [x.strip() for x in _su_re.split(u"[;；\n\r]+", txt) if x.strip()]:
                nums = [x for x in _su_re.split(u"[ ,]+", part) if x]
                try:
                    rgb = [int(x) for x in nums[:3]]
                except Exception:
                    rgb = []
                if len(rgb) != 3 or any(n < 0 or n > 255 for n in rgb):
                    raise ValueError(u"「%s」里「%s」不是有效的颜色，格式是 R,G,B（0–255），多个颜色用分号隔开。" % (name, part))
                out.append(rgb)
            if not out:
                raise ValueError(u"「%s」至少要有一个颜色。" % name)
            return out
        if fld.get(u"required") and not txt:
            raise ValueError(u"「%s」不能为空。" % name)
        return txt

    for fld in fields:
        if fld.get(u"kind") != u"header":
            put(fld, values.get(fld[u"key"]))

    result = {u"v": None}
    b_ok = _SUW.Button()
    b_ok.Text = u"保存"
    b_ok.Name = u"su_ok"
    b_ok.Location = _SUD.Point(width - 200, height - 44)
    b_ok.Size = _SUD.Size(88, 30)
    b_ca = _SUW.Button()
    b_ca.Text = u"取消"
    b_ca.Name = u"su_cancel"
    b_ca.Location = _SUD.Point(width - 100, height - 44)
    b_ca.Size = _SUD.Size(88, 30)
    f.Controls.Add(b_ok)
    f.Controls.Add(b_ca)
    f.CancelButton = b_ca
    bx = 14
    if defaults:
        b_def = _SUW.Button()
        b_def.Text = u"恢复默认"
        b_def.Name = u"su_default"
        b_def.Location = _SUD.Point(bx, height - 44)
        b_def.Size = _SUD.Size(88, 30)
        f.Controls.Add(b_def)
        bx += 96

        def _on_def(s, e):
            for fld in fields:
                if fld.get(u"kind") != u"header" and fld[u"key"] in defaults:
                    put(fld, defaults[fld[u"key"]])
        b_def.Click += _on_def
    for text, cb in (extra_buttons or []):
        b = _SUW.Button()
        b.Text = text
        b.Location = _SUD.Point(bx, height - 44)
        b.Size = _SUD.Size(130, 30)
        f.Controls.Add(b)
        bx += 138
        b.Click += (lambda s, e, cb=cb: cb())

    def _on_ok(s, e):
        out = {}
        try:
            for fld in fields:
                if fld.get(u"kind") != u"header":
                    out[fld[u"key"]] = get(fld)
        except ValueError as ex:
            _SUW.MessageBox.Show(u"%s" % ex, title)
            return
        result[u"v"] = out
        f.Close()
    b_ok.Click += _on_ok
    b_ca.Click += (lambda s, e: f.Close())
    ff_fit(f).ShowDialog()
    return result[u"v"]

doc = revit.doc
uidoc = revit.uidoc
TITLE = '构件标记一键整理'
FT = 304.8
DOC_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), 'KaifaTools')
SETTINGS = os.path.join(DOC_DIR, '构件标记整理设置.json')
DEFAULTS = {
    '说明': ('side_offset_mm：标记头离管边缘再往外留多少（图纸 mm）；bundle_gap_m：并行路由之间多远以内算一组（米），组内最左的标左边、最右的标右边；gap_mm：标记离任何构件的最小距离（图纸 mm）；max_leader_mm：引线最长（图纸 mm）；step_mm：往外试探一步；'
           'align_mm：同侧标记吸附到同一行/列的容差；text_w_mm / text_h_mm：读不到标记头实际大小时的估计值；'
           'add_missing：是否补标缺标构件；delete_orphans：是否删除悬空标记；min_pipe_mm：线性构件短于这个长度（模型 mm）不补标。'),
    'gap_mm': 2.0,
    'max_leader_mm': 150.0,
    'side_offset_mm': 5.0,
    'bundle_gap_m': 20.0,
    'step_mm': 3.0,
    'align_mm': 10.0,
    'text_w_mm': 60.0,
    'text_h_mm': 8.0,
    'add_missing': True,
    'delete_orphans': True,
    'min_pipe_mm': 1500.0,
}
DIRS = [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1),
        (0.38, 0.92), (0.92, 0.38), (0.92, -0.38), (0.38, -0.92), (-0.38, -0.92), (-0.92, -0.38), (-0.92, 0.38), (-0.38, 0.92)]
# 障碍只取机电管线/设备类；楼板、墙、柱等大面积建筑背景不当障碍（标记压在楼板上是正常的）
OBSTACLE_CATS = ('管道', '管件', '管道附件', '风管', '风管管件', '风管附件', '线管', '线管配件', '电缆桥架', '电缆桥架配件', '机械设备', '电气设备',
                 '电气装置', '卫浴装置', '喷头', '常规模型', '专用设备', '照明设备', '火警设备', '数据设备', '通讯设备', '安全设备', '灯具')


def load():
    s = dict(DEFAULTS)
    try:
        if os.path.exists(SETTINGS):
            with io.open(SETTINGS, 'r', encoding='utf-8-sig') as f:
                s.update(json.load(f))
    except Exception:
        pass
    return s


def open_settings():
    cur = load()
    fields = [
        {'key': 'gap_mm', 'label': '标记离构件最小距离 (图纸 mm)', 'kind': 'num', 'min': 0, 'max': 30, 'hint': '标记头离构件的最小距离。'},
        {'key': 'max_leader_mm', 'label': '引线最长 (图纸 mm)', 'kind': 'num', 'min': 5, 'max': 300, 'hint': '放不下时沿引线引出去，最远到这里。'},
        {'key': 'side_offset_mm', 'label': '标记离管边缘 (图纸 mm)', 'kind': 'num', 'min': 0, 'max': 100, 'hint': '标记头离管边缘再往外留多少。'},
        {'key': 'bundle_gap_m', 'label': '并行路由成组距离 (米)', 'kind': 'num', 'min': 1, 'max': 500, 'hint': '并行、相邻的路由相距在这个范围内算一组：最左的标左边、最右的标右边，中间留空。'},
        {'key': 'step_mm', 'label': '往外试探步长 (图纸 mm)', 'kind': 'num', 'min': 0.5, 'max': 30, 'hint': '找空位时每次往外走多远。'},
        {'key': 'align_mm', 'label': '对齐容差 (图纸 mm)', 'kind': 'num', 'min': 0, 'max': 30, 'hint': '同一侧的标记，位置差在这个范围内就对齐到同一行/列。'},
        {'key': 'text_w_mm', 'label': '标记头宽度估计 (图纸 mm)', 'kind': 'num', 'min': 3, 'max': 100, 'hint': '读不到标记头大小时用这个值。'},
        {'key': 'text_h_mm', 'label': '标记头高度估计 (图纸 mm)', 'kind': 'num', 'min': 2, 'max': 50, 'hint': '同上。'},
        {'key': 'add_missing', 'label': '补标缺标的构件', 'kind': 'bool', 'hint': '视图里某个族已经有标记，但同族的另一些构件没标，就用同样的标记类型补上，补完一起重新排布。'},
        {'key': 'delete_orphans', 'label': '删除悬空标记', 'kind': 'bool', 'hint': '标记的构件已经不存在（显示问号）的，删掉。'},
        {'key': 'min_pipe_mm', 'label': '线性构件补标最短长度 (模型 mm)', 'kind': 'num', 'min': 0, 'max': 100000, 'hint': '太短的管不补标。'},
    ]
    res = su_fields_dialog(TITLE + ' · 设置', '改完点保存，下次运行生效。设置文件：' + SETTINGS, fields, cur, DEFAULTS,
                           extra_buttons=[('用记事本打开文件', lambda: su_open_file(SETTINGS))], width=700, height=560)
    if res is None:
        return False
    out = dict(DEFAULTS)
    out.update(cur)
    out.update(res)
    if not os.path.isdir(DOC_DIR):
        os.makedirs(DOC_DIR)
    with io.open(SETTINGS, 'w', encoding='utf-8') as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    return True


class _Fails(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        for m in fa.GetFailureMessages():
            if m.GetSeverity() == DB.FailureSeverity.Warning:
                fa.DeleteWarning(m)
        return DB.FailureProcessingResult.Continue

def rect_overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def rect(cx, cy, w, h):
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def grow(r, g):
    return (r[0] - g, r[1] - g, r[2] + g, r[3] + g)


def _ccw(px, py, qx, qy, rx, ry):
    return (ry - py) * (qx - px) > (qy - py) * (rx - px)


def _seg_cross(ax, ay, bx, by, cx, cy, dx, dy):
    return _ccw(ax, ay, cx, cy, dx, dy) != _ccw(bx, by, cx, cy, dx, dy) and _ccw(ax, ay, bx, by, cx, cy) != _ccw(ax, ay, bx, by, dx, dy)


def seg_hits_rect(seg, r):
    """线段 (x1,y1,x2,y2) 与矩形 r 相交（端点在内，或和四条边相交）。"""
    x1, y1, x2, y2 = seg
    if r[0] <= x1 <= r[2] and r[1] <= y1 <= r[3]:
        return True
    if r[0] <= x2 <= r[2] and r[1] <= y2 <= r[3]:
        return True
    for ex1, ey1, ex2, ey2 in ((r[0], r[1], r[2], r[1]), (r[2], r[1], r[2], r[3]), (r[2], r[3], r[0], r[3]), (r[0], r[3], r[0], r[1])):
        if _seg_cross(x1, y1, x2, y2, ex1, ey1, ex2, ey2):
            return True
    return False


class Obstacles(object):
    """视图里所有模型构件（含被标记构件自己）的平面障碍：线式按线段（带管径半宽），其余按平面包围框。网格索引加速。"""
    CELL = 10.0     # ft

    def __init__(self):
        self.cells = {}
        self.items = []

    def _keys(self, x0, y0, x1, y1):
        c = self.CELL
        for i in range(int(math.floor(x0 / c)), int(math.floor(x1 / c)) + 1):
            for j in range(int(math.floor(y0 / c)), int(math.floor(y1 / c)) + 1):
                yield (i, j)

    def add_seg(self, x1, y1, x2, y2, half, eid):
        idx = len(self.items)
        self.items.append(('s', (x1, y1, x2, y2), half, eid))
        for k in self._keys(min(x1, x2) - half, min(y1, y2) - half, max(x1, x2) + half, max(y1, y2) + half):
            self.cells.setdefault(k, []).append(idx)

    def add_rect(self, r, eid):
        idx = len(self.items)
        self.items.append(('r', r, 0.0, eid))
        for k in self._keys(r[0], r[1], r[2], r[3]):
            self.cells.setdefault(k, []).append(idx)

    def hits(self, rc, margin=0.0, skip_eid=None):
        """矩形 rc（外扩 margin）压到的构件个数。"""
        q = grow(rc, margin)
        seen = set()
        n = 0
        for k in self._keys(q[0], q[1], q[2], q[3]):
            for idx in self.cells.get(k, ()):
                if idx in seen:
                    continue
                seen.add(idx)
                kind, g, half, eid = self.items[idx]
                if skip_eid is not None and eid == skip_eid:
                    continue
                if kind == 'r':
                    if rect_overlap(q, g):
                        n += 1
                else:
                    if seg_hits_rect(g, grow(q, half)):
                        n += 1
        return n


def _half_width(el):
    """线式构件的半宽（ft）：管径/风管宽/线管径，取不到用 0.1 m。"""
    for bip in (DB.BuiltInParameter.RBS_PIPE_OUTER_DIAMETER, DB.BuiltInParameter.RBS_CONDUIT_OUTER_DIAM_PARAM,
                DB.BuiltInParameter.RBS_CURVE_WIDTH_PARAM):
        try:
            p = el.get_Parameter(bip)
            if p is not None and p.HasValue and p.AsDouble() > 0:
                return p.AsDouble() / 2.0
        except Exception:
            pass
    return 0.1 / 0.3048


def build_obstacles(view, extra_cats=()):
    ob = Obstacles()
    skip_cats = set()
    n = 0
    for e in DB.FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
        try:
            c = e.Category
            if c is None or c.CategoryType != DB.CategoryType.Model:
                continue
            if c.Name not in OBSTACLE_CATS and c.Id.Value not in extra_cats:
                continue
            if isinstance(e, (DB.IndependentTag,)):
                continue
            loc = e.Location
            if isinstance(loc, DB.LocationCurve):
                cv = loc.Curve
                p0, p1 = cv.GetEndPoint(0), cv.GetEndPoint(1)
                ob.add_seg(p0.X, p0.Y, p1.X, p1.Y, _half_width(e), e.Id.Value)
                n += 1
                continue
            bb = e.get_BoundingBox(view)
            if bb is None:
                continue
            ob.add_rect((bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y), e.Id.Value)
            n += 1
        except Exception:
            continue
    return ob, n


def view_tags(view):
    """属于这个视图的全部标记（文档级收集 + OwnerViewId）。视图级收集器会漏掉构件中点在裁剪框外、被视图过滤的标记。"""
    out = []
    for t in DB.FilteredElementCollector(doc).OfClass(DB.IndependentTag):
        try:
            if t.OwnerViewId == view.Id:
                out.append(t)
        except Exception:
            continue
    return out


def center_of(el):
    """构件中心：线式取线段中点，点式取插入点，其它取平面包围框中心。"""
    loc = el.Location
    if isinstance(loc, DB.LocationCurve):
        return loc.Curve.Evaluate(0.5, True)
    if isinstance(loc, DB.LocationPoint):
        return loc.Point
    bb = el.get_BoundingBox(None)
    if bb is not None:
        return DB.XYZ((bb.Min.X + bb.Max.X) / 2.0, (bb.Min.Y + bb.Max.Y) / 2.0, bb.Min.Z)
    return None


def measure_tag_heads(view, items, est_w, est_h):
    """实测每种（标记类型 + 文字）的标记头大小和「包围框中心 − 标记头位置」偏移：临时去掉引线、重新生成、读包围框，回滚。"""
    cache = {}
    t = DB.Transaction(doc, '测量标记头')
    t.Start()
    try:
        for x in items:
            tg = x['t']
            try:
                key = (tg.GetTypeId().Value, tg.TagText, x.get('orient'))
            except Exception:
                key = (tg.GetTypeId().Value, '', x.get('orient'))
            if key in cache:
                x['size'] = cache[key]
                continue
            res = (est_w, est_h, 0.0, 0.0)
            try:
                had = tg.HasLeader
                tg.HasLeader = False
                if x.get('orient') is not None:
                    tg.TagOrientation = x['orient']
                doc.Regenerate()
                bb = tg.get_BoundingBox(view)
                if bb is not None:
                    h = tg.TagHeadPosition
                    w, hh = bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y
                    if w > 0 and hh > 0:
                        res = (w, hh, (bb.Min.X + bb.Max.X) / 2.0 - h.X, (bb.Min.Y + bb.Max.Y) / 2.0 - h.Y)
                tg.HasLeader = had
            except Exception:
                pass
            cache[key] = res
            x['size'] = res
    finally:
        t.RollBack()
    return len(cache)


def build_routes(ob):
    """线式构件按首尾相接（端点 0.3 m 内）连成路由。返回 (eid→路由号, 路由信息列表)。"""
    segs = [(g, eid) for kind, g, half, eid in ob.items if kind == 's']
    tol = 0.3 / 0.3048
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    for g, eid in segs:
        parent.setdefault(eid, eid)
    grid = {}
    for g, eid in segs:
        for (x, y) in ((g[0], g[1]), (g[2], g[3])):
            k = (int(math.floor(x / 2.0)), int(math.floor(y / 2.0)))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for (x2, y2, e2) in grid.get((k[0] + dx, k[1] + dy), ()):
                        if e2 != eid and math.hypot(x - x2, y - y2) <= tol:
                            union(eid, e2)
            grid.setdefault(k, []).append((x, y, eid))
    routes = {}
    for g, eid in segs:
        r = find(eid)
        info = routes.setdefault(r, {'len_x': 0.0, 'len_y': 0.0, 'xs': [], 'ys': [], 'eids': set()})
        info['len_x'] += abs(g[2] - g[0])
        info['len_y'] += abs(g[3] - g[1])
        info['xs'].extend((g[0], g[2]))
        info['ys'].extend((g[1], g[3]))
        info['eids'].add(eid)
    eid2route = {}
    out = []
    for r, info in routes.items():
        info['axis'] = 'v' if info['len_y'] >= info['len_x'] else 'h'
        info['xm'] = sum(info['xs']) / len(info['xs'])
        info['ym'] = sum(info['ys']) / len(info['ys'])
        info['x0'], info['x1'] = min(info['xs']), max(info['xs'])
        info['y0'], info['y1'] = min(info['ys']), max(info['ys'])
        info['side'] = None
        info['id'] = len(out)
        for eid in info['eids']:
            eid2route[eid] = info['id']
        out.append(info)
    return eid2route, out


def assign_sides(routes, bundle_ft):
    """并行相邻的路由成组：竖向路由按 x 排，最左标左边、最右标右边、中间的按靠哪边；横向路由按 y 排，最下标下边、最上标上边。"""
    for axis in ('v', 'h'):
        rs = [r for r in routes if r['axis'] == axis]
        n = len(rs)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(n):
            for j in range(i + 1, n):
                a, b = rs[i], rs[j]
                if axis == 'v':
                    ov = min(a['y1'], b['y1']) - max(a['y0'], b['y0'])
                    gap = abs(a['xm'] - b['xm'])
                else:
                    ov = min(a['x1'], b['x1']) - max(a['x0'], b['x0'])
                    gap = abs(a['ym'] - b['ym'])
                if ov > 0 and gap <= bundle_ft:
                    parent[find(i)] = find(j)
        groups = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(rs[i])
        for g in groups.values():
            if len(g) < 2:
                continue
            key = (lambda r: r['xm']) if axis == 'v' else (lambda r: r['ym'])
            g.sort(key=key)
            mid = (key(g[0]) + key(g[-1])) / 2.0
            for k, r in enumerate(g):
                low = (k == 0) or (k < len(g) - 1 and key(r) < mid)
                if axis == 'v':
                    r['side'] = (-1, 0) if low else (1, 0)
                else:
                    r['side'] = (0, -1) if low else (0, 1)


def fix_leaders(items, ob, fixed, gap, step, max_len):
    """引线冲突修复：引线（头中心→构件中心）穿过别的标记文字框、或和别的引线交叉的标记，换到不冲突的位置。返回 (修好个数, 仍冲突个数)。"""
    def rc_of(x):
        return rect(x['c'][0], x['c'][1], x['size'][0], x['size'][1])

    def leader(x):
        return (x['c'][0], x['c'][1], x['a'].X, x['a'].Y)

    def conflicts_of(i, cand_c=None):
        x = items[i]
        cx, cy = cand_c if cand_c is not None else x['c']
        w, h = x['size'][0], x['size'][1]
        mine = (cx, cy, x['a'].X, x['a'].Y)
        mr = rect(cx, cy, w, h)
        n = 0
        for j, y in enumerate(items):
            if j == i:
                continue
            if seg_hits_rect(mine, rc_of(y)):
                n += 1
            ly = leader(y)
            if _seg_cross(mine[0], mine[1], mine[2], mine[3], ly[0], ly[1], ly[2], ly[3]):
                n += 1
            if seg_hits_rect(ly, mr):
                n += 1
        return n

    bad = [i for i in range(len(items)) if conflicts_of(i) > 0]
    fixed_n = 0
    for i in bad:
        if conflicts_of(i) == 0:
            continue
        x = items[i]
        a = x['a']
        w, h = x['size'][0], x['size'][1]
        base = math.hypot(x['c'][0] - a.X, x['c'][1] - a.Y)
        best = None
        for k in range(0, 24):
            r = max(base * 0.6, max(w, h) / 2.0 + gap) + k * step
            if r > max_len * 1.5:
                break
            for dx, dy in DIRS:
                nn = math.hypot(dx, dy)
                cx, cy = a.X + dx / nn * r, a.Y + dy / nn * r
                rc = rect(cx, cy, w, h)
                if ob.hits(rc, gap, None) > 0:
                    continue
                if any(rect_overlap(grow(rc, gap), rc_of(y)) for j, y in enumerate(items) if j != i):
                    continue
                if any(rect_overlap(grow(rc, gap), fx) for fx in fixed):
                    continue
                c = conflicts_of(i, (cx, cy))
                if c == 0:
                    score = k + (0.4 if (dx and dy) else 0.0)
                    if best is None or score < best[0]:
                        best = (score, cx, cy, dx, dy)
            if best is not None:
                break
        if best is not None:
            _, cx, cy, dx, dy = best
            x['c'] = (cx, cy)
            x['dir'] = (dx, dy)
            fixed_n += 1
    left = sum(1 for i in range(len(items)) if conflicts_of(i) > 0)
    return fixed_n, left


def stack_dense(items, ob, fixed, gap, step, max_len, m):
    """密集区整队：挤在一起的一团标记（压构件、互相压、或被推得很远的），整队成一列（左/右）或一行（上/下），
    间距相等、对齐、按位置排序（引线不交叉）；四个侧各试一遍，挑不压任何东西且引线最短的。返回整队的标记数。"""
    def cur_rect(x):
        return rect(x['c'][0], x['c'][1], x['size'][0], x['size'][1])

    crowded = []
    for x in items:
        rc = cur_rect(x)
        far = math.hypot(x['c'][0] - x['a'].X, x['c'][1] - x['a'].Y) > max(x['size'][0], x['size'][1]) * 0.9 + gap
        over_tag = any(y is not x and rect_overlap(grow(rc, gap), cur_rect(y)) for y in items)
        if x.get('conf', 0) > 0 or over_tag or far:
            crowded.append(x)
    if len(crowded) < 3:
        return 0
    # 按锚点距离聚类
    link = max(max(x['size'][0], x['size'][1]) for x in crowded) * 1.2
    parent = list(range(len(crowded)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(crowded)):
        for j in range(i + 1, len(crowded)):
            if math.hypot(crowded[i]['a'].X - crowded[j]['a'].X, crowded[i]['a'].Y - crowded[j]['a'].Y) <= link:
                parent[find(i)] = find(j)
    groups = {}
    for i, x in enumerate(crowded):
        groups.setdefault(find(i), []).append(x)
    n_done = 0
    for g in groups.values():
        if len(g) < 3:
            continue
        in_g = set(id(x) for x in g)
        w = max(x['size'][0] for x in g)
        h = max(x['size'][1] for x in g)
        horiz = w >= h            # 宽标记：左右排成一列；窄（竖排）标记：上下排成一行
        xs = [x['a'].X for x in g]
        ys = [x['a'].Y for x in g]
        cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        others = list(fixed) + [cur_rect(y) for y in items if id(y) not in in_g]
        sides = ((1, 0), (-1, 0), (0, 1), (0, -1)) if horiz else ((0, 1), (0, -1), (1, 0), (-1, 0))
        best = None
        for sd in sides:
            for k in range(0, 14):
                off = m(5.0) + k * step
                slots = []
                n = len(g)
                if sd[0] != 0:
                    pitch = h + gap
                    gx = (max(xs) + off + w / 2.0) if sd[0] > 0 else (min(xs) - off - w / 2.0)
                    gs = sorted(g, key=lambda x: x['a'].Y)
                    for i in range(n):
                        slots.append((gx, cy + (i - (n - 1) / 2.0) * pitch))
                else:
                    pitch = w + gap
                    gy = (max(ys) + off + h / 2.0) if sd[1] > 0 else (min(ys) - off - h / 2.0)
                    gs = sorted(g, key=lambda x: x['a'].X)
                    for i in range(n):
                        slots.append((cx + (i - (n - 1) / 2.0) * pitch, gy))
                conf = 0
                longest = 0.0
                for (sx, sy), x in zip(slots, gs):
                    rc = rect(sx, sy, x['size'][0], x['size'][1])
                    if ob.hits(rc, gap, None) > 0:
                        conf += 1
                    if any(rect_overlap(grow(rc, gap), o) for o in others):
                        conf += 1
                    longest = max(longest, math.hypot(sx - x['a'].X, sy - x['a'].Y))
                if conf == 0 and longest <= max_len * 2.0:
                    cand = (longest + k, slots, gs, sd)
                    if best is None or cand[0] < best[0]:
                        best = cand
                    break
        if best is None:
            continue
        _, slots, gs, sd = best
        for (sx, sy), x in zip(slots, gs):
            x['c'] = (sx, sy)
            x['dir'] = sd
            x['conf'] = 0
        n_done += len(g)
    return n_done


def relax(items, ob, fixed, gap, step, max_len, rounds=5, budget_s=12.0):
    """全局冲突消解（最小冲突局部搜索）：前面各步是逐个贪心放置，密的地方会堆叠、引线乱叉。
    这里统一打分，让「有冲突」的标记换到总代价最低的候选位置：
      标记框互相重叠 / 压构件 / 压固定标记 ≫ 引线穿别的标记框、引线互相交叉 ≫ 引线超上限 > 斜引线 > 引线长。
    候选 = 以构件中心为圆心、16 个方向 × 十来圈由近到远的半径（先在 1.3 倍引线上限内找，还冲突的再放宽到 2.6 倍）。
    只处理有冲突的标记，邻居只看附近的，总耗时有上限（budget_s 秒，到点就停，不会卡住 Revit）。返回仍有冲突的标记个数。"""
    n = len(items)
    if n < 2:
        return 0
    t_end = time.time() + budget_s
    far_lim = max_len * 2.6
    reach = far_lim + max(max(x['size'][0], x['size'][1]) for x in items) * 1.5
    # 邻居：网格索引，只看锚点相距 reach 以内的
    cell = max(reach, 1.0)
    grid = {}
    for i, x in enumerate(items):
        grid.setdefault((int(math.floor(x['a'].X / cell)), int(math.floor(x['a'].Y / cell))), []).append(i)
    nb = []
    for i, x in enumerate(items):
        gx, gy = int(math.floor(x['a'].X / cell)), int(math.floor(x['a'].Y / cell))
        lst = []
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                for j in grid.get((gx + ddx, gy + ddy), ()):
                    if j != i and abs(items[j]['a'].X - x['a'].X) <= reach and abs(items[j]['a'].Y - x['a'].Y) <= reach:
                        lst.append(j)
        nb.append(lst)

    def cost(i, cx, cy, cap=1e18, hard_only=False):
        """cap：已知的更优代价，累计超过就提前返回（省掉最贵的构件碰撞检查）。"""
        x = items[i]
        w, h = x['size'][0], x['size'][1]
        ax_, ay_ = x['a'].X, x['a'].Y
        rc = rect(cx, cy, w, h)
        grc = grow(rc, gap)
        c = 0.0
        d = math.hypot(cx - ax_, cy - ay_)
        if d > max_len:
            c += 60.0 * (d - max_len) / max_len
        c += 8.0 * d / max_len
        dx, dy = cx - ax_, cy - ay_
        if d > 1e-9:
            c += 6.0 * min(abs(dx), abs(dy)) / d      # 0 = 正横/正竖，0.7 = 45°
            axis = x.get('axis')
            if (axis == 'h' and abs(dy) < abs(dx)) or (axis == 'v' and abs(dx) < abs(dy)):
                c += 3.0
        for fx in fixed:
            if rect_overlap(grc, fx):
                c += 1000.0
        if c >= cap:
            return c
        for j in nb[i]:
            y = items[j]
            yr = rect(y['c'][0], y['c'][1], y['size'][0], y['size'][1])
            if rect_overlap(grc, yr):
                c += 1000.0
            ly = (y['c'][0], y['c'][1], y['a'].X, y['a'].Y)
            if seg_hits_rect((cx, cy, ax_, ay_), yr):
                c += 300.0
            if seg_hits_rect(ly, rc):
                c += 300.0
            if _seg_cross(cx, cy, ax_, ay_, ly[0], ly[1], ly[2], ly[3]):
                c += 300.0
            if c >= cap:
                return c
        if hard_only:
            return c
        # 压到别的构件：软约束（密集的管线区很难完全避开），自己的构件不算
        c += 40.0 * ob.hits(rc, gap, x.get('own'))
        return c

    def now(i, hard_only=False):
        return cost(i, items[i]['c'][0], items[i]['c'][1], 1e18, hard_only)

    for _r in range(rounds * 2):
        tier = _r >= rounds
        scored = [(now(i), i) for i in range(n) if now(i, True) >= 300.0]
        bad = scored
        if not bad or time.time() > t_end:
            break
        bad.sort(reverse=True)
        moved = 0
        for cur, i in bad:
            if time.time() > t_end:
                break
            x = items[i]
            a = x['a']
            w, h = x['size'][0], x['size'][1]
            cur = now(i)
            if now(i, True) < 300.0:
                continue
            best = (cur, x['c'][0], x['c'][1], x.get('dir'))
            r0 = max(w, h) / 2.0 + gap
            reach_i = far_lim if tier else max_len * 1.3
            n_r = 12
            radii = [r0 + (reach_i - r0) * k / (n_r - 1.0) for k in range(n_r)] if reach_i > r0 else [r0]
            for r in radii:
                for dx, dy in DIRS:
                    nn = math.hypot(dx, dy)
                    cx, cy = a.X + dx / nn * r, a.Y + dy / nn * r
                    cs = cost(i, cx, cy, best[0])
                    if cs < best[0] - 1e-6:
                        best = (cs, cx, cy, (dx, dy))
                if best[0] < 40.0:
                    break
            if best[1] != x['c'][0] or best[2] != x['c'][1]:
                x['c'] = (best[1], best[2])
                if best[3] is not None:
                    x['dir'] = best[3]
                moved += 1
        if not moved and tier:
            break
    return sum(1 for i in range(n) if now(i, True) >= 300.0)


def plan(view, cfg):
    scale = max(int(view.Scale), 1)
    m = lambda mm: mm * scale / FT
    p_mm = lambda ft: ft * FT / scale
    gap, step, max_len, tol = m(cfg['gap_mm']), m(cfg['step_mm']), m(cfg['max_leader_mm']), m(cfg['align_mm'])
    all_tags = view_tags(view)
    tags, in_group = [], []
    for t in all_tags:
        try:
            if t.GroupId != DB.ElementId.InvalidElementId:
                in_group.append(t)
                continue
        except Exception:
            pass
        tags.append(t)
    orphans, items, multi = [], [], []
    for t in tags:
        try:
            if t.IsOrphaned:
                orphans.append(t)
                continue
        except Exception:
            pass
        ids = list(t.GetTaggedLocalElementIds())
        if len(ids) != 1:
            multi.append(t)
            continue
        el = doc.GetElement(ids[0])
        if el is None:
            orphans.append(t)
            continue
        try:
            refs = list(t.GetTaggedReferences())
        except Exception:
            refs = []
        a = center_of(el)
        if not refs or a is None:
            multi.append(t)
            continue
        items.append({'t': t, 'el': el, 'ref': refs[0], 'a': a, 'head': t.TagHeadPosition, 'own': el.Id.Value})
    # 一管一标：同一构件上有多个标记，只留离构件中心最近的那个，其余当重复标记（可删除）
    by_el = {}
    for x in items:
        by_el.setdefault(x['own'], []).append(x)
    dups, keep = [], []
    for eid, xs in by_el.items():
        xs.sort(key=lambda x: math.hypot(x['head'].X - x['a'].X, x['head'].Y - x['a'].Y))
        keep.append(xs[0])
        dups.extend(y['t'] for y in xs[1:])
    items = keep
    # 标记朝向与管道平行：沿 Y 的竖向，其余横向（Revit 标记只有横/竖两种）
    for x in items:
        x['orient'] = None
        loc = x['el'].Location
        if isinstance(loc, DB.LocationCurve):
            dd = loc.Curve.GetEndPoint(1) - loc.Curve.GetEndPoint(0)
            x['orient'] = DB.TagOrientation.Vertical if abs(dd.Y) > abs(dd.X) else DB.TagOrientation.Horizontal
            x['axis'] = 'v' if abs(dd.Y) > abs(dd.X) else 'h'
        else:
            x['axis'] = None
    tagged_cats = set()
    for x in items:
        try:
            tagged_cats.add(x['el'].Category.Id.Value)
        except Exception:
            pass
    ob, n_ob = build_obstacles(view, tagged_cats)
    # 多构件标记不动，当作固定障碍
    fixed = []
    locked_tagged = set()
    for t in in_group:
        for i in t.GetTaggedLocalElementIds():
            locked_tagged.add(i.Value)
    for t in list(multi) + list(in_group):
        try:
            bb = t.get_BoundingBox(view)
            if bb is not None:
                fixed.append((bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y))
        except Exception:
            pass
    eid2route, routes = build_routes(ob)
    assign_sides(routes, cfg['bundle_gap_m'] * 1000.0 / FT)
    for x in items:
        ri = eid2route.get(x['own'])
        x['side'] = None
        if ri is not None:
            r = routes[ri]
            sd = r['side']
            # 只有标记所在管段的方向和路由方向一致，才按路由的侧
            if sd is not None and x.get('axis') == r['axis']:
                x['side'] = sd
    n_sizes = measure_tag_heads(view, items, m(cfg['text_w_mm']), m(cfg['text_h_mm']))
    items.sort(key=lambda x: (round(x['a'].Y, 1), round(x['a'].X, 1)))
    placed = list(fixed)

    def free(rc, skip_own=None):
        if ob.hits(rc, gap, None) > 0:
            return False
        for p in placed:
            if rect_overlap(grow(rc, gap), p):
                return False
        return True

    for x in items:
        w, h, ox, oy = x['size']
        a = x['a']
        base = max(w, h) / 2.0 + gap
        # 优先垂直于管道的两个方向（横管放上下、竖管放左右），其次斜向
        side = x.get('side')
        if side is not None:
            hw = _half_width(x['el'])
            ext = (w / 2.0) if side[0] != 0 else (h / 2.0)
            base = hw + ext + gap + m(cfg['side_offset_mm'])
        best = None
        k = 0
        r = base
        limit = max(max_len, base)
        # 标记头很宽时（如 123 mm），搜索半径至少覆盖一个头宽
        limit = max(limit, base + w)
        while r <= limit + 1e-9:
            for dx, dy in DIRS:
                n = math.hypot(dx, dy)
                cx, cy = a.X + dx / n * r, a.Y + dy / n * r
                rc = rect(cx, cy, w, h)
                c_obs = ob.hits(rc, gap, None)
                c_tag = sum(1 for p in placed if rect_overlap(grow(rc, gap), p))
                conf = c_obs + c_tag
                perp_ok = x.get('axis') is None or (x['axis'] == 'h' and abs(dx) < 0.3) or (x['axis'] == 'v' and abs(dy) < 0.3)
                side_pen = 0.0
                if side is not None:
                    side_pen = 0.0 if (abs(dx - side[0]) < 0.05 and abs(dy - side[1]) < 0.05) else 6.0
                score = conf * 1000 + k + (0.5 if (dx and dy) else 0.0) + (0.0 if perp_ok else 3.0) + side_pen
                if best is None or score < best[0]:
                    best = (score, cx, cy, conf, dx, dy)
            if best is not None and best[3] == 0:
                break
            k += 1
            r = base + k * step
        _, cx, cy, conf, dx, dy = best
        x['c'] = (cx, cy)
        x['dir'] = (dx, dy)
        x['conf'] = conf
        placed.append(rect(cx, cy, w, h))
        x['rect_idx'] = len(placed) - 1
    n_snapped = 0
    n_slid = 0

    def do_align():
        nonlocal n_snapped, n_slid
        # 严格平齐（按坐标做）：横向标记按 y 聚成「行线」，竖向标记按 x 聚成「列线」，行线/列线取簇内中位数；
        # 成员的这一个坐标改成完全相同；压到构件或别的标记时，沿行线/列线滑动找空位（不放弃对齐）。
        for axis in ('x', 'y'):
            if axis == 'x':
                grp = [x for x in items if x.get('axis') == 'v']
                key = lambda x: x['c'][0]
                other = lambda x: x['c'][1]
            else:
                # 横排的头（管线横标记 + 没有方向的点式构件，如井）一律按行线（y）对齐
                grp = [x for x in items if x.get('axis') in ('h', None)]
                key = lambda x: x['c'][1]
                other = lambda x: x['c'][0]
            grp.sort(key=key)
            clusters, cur = [], []
            for x in grp:
                if cur and key(x) - key(cur[-1]) > tol:
                    clusters.append(cur)
                    cur = []
                cur.append(x)
            if cur:
                clusters.append(cur)
            for cl in clusters:
                if len(cl) < 2:
                    continue
                vals = sorted(key(y) for y in cl)
                target = vals[len(vals) // 2]
                for y in sorted(cl, key=lambda q: abs(key(q) - target)):
                    cx, cy = y['c']
                    w, h = y['size'][0], y['size'][1]
                    if abs(key(y) - target) < 1e-9:
                        continue
                    chosen = None
                    for k in range(0, 40):
                        for sgn in ((0,) if k == 0 else (1, -1)):
                            o = other(y) + sgn * k * step
                            ncx, ncy = (target, o) if axis == 'x' else (o, target)
                            if math.hypot(ncx - y['a'].X, ncy - y['a'].Y) > max_len * 1.5:
                                continue
                            rc = rect(ncx, ncy, w, h)
                            if ob.hits(rc, gap, None) > 0:
                                continue
                            old = placed[y['rect_idx']]
                            placed[y['rect_idx']] = (0, 0, 0, 0)
                            ok = all(not rect_overlap(grow(rc, gap), pp) for pp in placed if pp != (0, 0, 0, 0))
                            placed[y['rect_idx']] = old
                            if ok:
                                chosen = (ncx, ncy, rc, k)
                                break
                        if chosen is not None:
                            break
                    if chosen is not None:
                        placed[y['rect_idx']] = chosen[2]
                        y['c'] = (chosen[0], chosen[1])
                        n_snapped += 1
                        if chosen[3] > 0:
                            n_slid += 1

    do_align()
    n_stacked = stack_dense(items, ob, fixed, gap, step, max_len, m)
    n_lead_fixed, n_lead_left = fix_leaders(items, ob, fixed, gap, step, max_len)
    # 引线修复挪动了一些标记：再对齐一次，再修复一次（两个目标互相迁就）
    for _round in range(2):
        do_align()
        f2, n_lead_left = fix_leaders(items, ob, fixed, gap, step, max_len)
        n_lead_fixed += f2
    n_relax_left = relax(items, ob, fixed, gap, step, max_len)
    n_lead_left = n_relax_left
    # 头位置 = 包围框中心 − 偏移
    for x in items:
        w, h, ox, oy = x['size']
        x['new'] = (x['c'][0] - ox, x['c'][1] - oy)
    stat = measure_result(items, ob, placed, gap, p_mm, cfg)
    stat.update({'in_group': len(in_group), 'locked_tagged': locked_tagged, 'lead_fixed': n_lead_fixed, 'lead_left': n_lead_left, 'slid': n_slid, 'stacked': n_stacked, 'routes': routes, 'items': items, 'dups': dups, 'orphans': orphans, 'multi': len(multi), 'scale': scale, 'ob': n_ob, 'snapped': n_snapped, 'sizes': n_sizes})
    return stat


def measure_result(items, ob, placed, gap, p_mm, cfg):
    over_el = 0
    for x in items:
        w, h = x['size'][0], x['size'][1]
        if ob.hits(rect(x['c'][0], x['c'][1], w, h), 0.0, x.get('own')) > 0:
            over_el += 1
    rects = [rect(x['c'][0], x['c'][1], x['size'][0], x['size'][1]) for x in items]
    over_tag = sum(1 for i in range(len(rects)) for j in range(i + 1, len(rects)) if rect_overlap(rects[i], rects[j]))
    far = sum(1 for x in items if p_mm(math.hypot(x['c'][0] - x['a'].X, x['c'][1] - x['a'].Y)) > cfg['max_leader_mm'] + 1e-6)
    return {'over_el': over_el, 'over_tag': over_tag, 'far': far}


def _fam_key(e):
    """（类别, 族名）：补标只补「视图里这个族已经有标记」的同族构件。"""
    try:
        cat = e.Category.Id.Value
    except Exception:
        return None
    try:
        fam = e.Symbol.FamilyName
    except Exception:
        try:
            fam = doc.GetElement(e.GetTypeId()).FamilyName
        except Exception:
            fam = ''
    return (cat, fam)


def missing_elements(view, cfg, items, multi_tagged_ids=()):
    """视图里同类别同族已经有标记、但自己没标的构件：[(el, 标记类型Id)]。标记类型取该族现有标记里用得最多的。"""
    tagged = set(x['own'] for x in items)
    tagged.update(multi_tagged_ids)
    fam_type = {}
    for x in items:
        k = _fam_key(x['el'])
        if k is None:
            continue
        fam_type.setdefault(k, {}).setdefault(x['t'].GetTypeId().Value, 0)
        fam_type[k][x['t'].GetTypeId().Value] += 1
    out = []
    min_len = cfg['min_pipe_mm'] / FT
    for cid in set(k[0] for k in fam_type):
        col = DB.FilteredElementCollector(doc, view.Id).OfCategoryId(DB.ElementId(cid)).WhereElementIsNotElementType()
        for e in col:
            if e.Id.Value in tagged:
                continue
            k = _fam_key(e)
            if k not in fam_type:
                continue
            loc = e.Location
            if loc is None:
                continue
            if isinstance(loc, DB.LocationCurve) and loc.Curve.Length < min_len:
                continue
            tmap = fam_type[k]
            out.append((e, DB.ElementId(max(tmap.items(), key=lambda kv: kv[1])[0])))
    return out


def create_missing(view, add_list):
    """建缺失的标记（先放在构件中心，随后由排布挪到空位）。返回 (成功个数, 失败列表[(构件id, 原因)])。要在事务里调用。"""
    n = 0
    fails = []
    for e, ttid in add_list:
        try:
            pt = center_of(e)
            if pt is None:
                fails.append((e.Id.Value, '取不到构件中心'))
                continue
            DB.IndependentTag.Create(doc, ttid, view.Id, DB.Reference(e), True, DB.TagOrientation.Horizontal, DB.XYZ(pt.X, pt.Y, pt.Z))
            n += 1
        except Exception as ex:
            fails.append((e.Id.Value, ('%s' % ex).splitlines()[0][:60]))
    return n, fails


def untagged_report(view, cfg, pl):
    """视图里没有标记的构件（排除中心线、相机、线、标识、楼板等非标注对象），按（类别, 族）统计，
    并说明会不会补：该族视图里已有标记的补；一个标记都没有的族不补（口径：只补同族已有标记的）。返回 [(类别, 族, 个数, '补'/'不补')]。"""
    tagged = set(x['own'] for x in pl['items']) | set(pl.get('locked_tagged', set()))
    for t in view_tags(view):
        for i in t.GetTaggedLocalElementIds():
            tagged.add(i.Value)
    fam_has = set()
    for x in pl['items']:
        k = _fam_key(x['el'])
        if k is not None:
            fam_has.add(k)
    skip = ('中心线', '相机', '线', '楼板', '楼板洞口剪切', '标识', '参照平面', '详图项目')
    cnt = {}
    for e in DB.FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
        try:
            c = e.Category
            if c is None or c.CategoryType != DB.CategoryType.Model or c.Name in skip:
                continue
            if e.Id.Value in tagged:
                continue
            k = _fam_key(e)
            if k is None:
                continue
            will = k in fam_has
            if will and isinstance(e.Location, DB.LocationCurve) and e.Location.Curve.Length < cfg['min_pipe_mm'] / FT:
                will = False
            key = (c.Name, k[1][:30] if k[1] else '（无族名）', '补' if will else '不补')
            cnt[key] = cnt.get(key, 0) + 1
        except Exception:
            continue
    return sorted([(k[0], k[1], v, k[2]) for k, v in cnt.items()], key=lambda r: (-(r[3] == '补'), -r[2]))


def verify(items, tol_ft=1.0 / FT):
    """回读：引线末端必须在自己构件的中心（偏差 ≤ 1 mm）。返回不合格的标记 id。"""
    bad = []
    for x in items:
        try:
            le = x['t'].GetLeaderEnd(x['ref'])
        except Exception:
            bad.append(x['t'].Id.Value)
            continue
        if math.hypot(le.X - x['a'].X, le.Y - x['a'].Y) > tol_ft:
            bad.append(x['t'].Id.Value)
    return bad


def apply(view, pl, cfg, add_list):
    t = DB.Transaction(doc, '天河落 · 构件标记一键整理')
    fh = t.GetFailureHandlingOptions()
    fh.SetFailuresPreprocessor(_Fails())
    t.SetFailureHandlingOptions(fh)
    t.Start()
    try:
        n_del = 0
        if cfg['delete_orphans'] and pl['orphans']:
            doc.Delete(List[DB.ElementId]([o.Id for o in pl['orphans']]))
            n_del = len(pl['orphans'])
        n_dup = 0
        if cfg['delete_orphans'] and pl['dups']:
            doc.Delete(List[DB.ElementId]([o.Id for o in pl['dups']]))
            n_dup = len(pl['dups'])
        for x in pl['items']:
            tg = x['t']
            if x.get('orient') is not None:
                try:
                    tg.TagOrientation = x['orient']
                except Exception:
                    pass
            tg.HasLeader = True
            try:
                tg.LeaderEndCondition = DB.LeaderEndCondition.Free
            except Exception:
                pass
            tg.TagHeadPosition = DB.XYZ(x['new'][0], x['new'][1], x['head'].Z)
            tg.SetLeaderEnd(x['ref'], DB.XYZ(x['a'].X, x['a'].Y, x['head'].Z))
        doc.Regenerate()
        n_add = 0
        bad = verify(pl['items'])
        if bad:
            raise Exception('回读核对失败：%d 个标记的引线末端没落在自己构件的中心（如 %s），整次回滚，模型未改。' % (len(bad), bad[:3]))
        st = t.Commit()
        if st != DB.TransactionStatus.Committed:
            raise Exception('事务没有提交（%s），模型未改。' % st)
        return n_del + n_dup, n_add
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise


def run_all(view, cfg):
    """整次操作：补标缺标构件 → 重新规划 → 排布并写入。事务组包起来，Ctrl+Z 一次撤销。返回 (整理数, 删重复/悬空数, 补标数, 规划结果)。"""
    tg = DB.TransactionGroup(doc, '天河落 · 构件标记一键整理')
    tg.Start()
    try:
        # 被「在视图中隐藏」的标记等于没标：先取消隐藏，再一起整理
        hidden = [t.Id for t in view_tags(view) if t.IsHidden(view) and t.GroupId == DB.ElementId.InvalidElementId]
        if hidden:
            th = DB.Transaction(doc, '取消隐藏的标记')
            th.Start()
            view.UnhideElements(System.Collections.Generic.List[DB.ElementId](hidden))
            if th.Commit() != DB.TransactionStatus.Committed:
                raise Exception('取消隐藏标记的事务没有提交，已回滚。')
        pl = plan(view, cfg)
        n_add = 0
        fail_total = []
        if cfg['add_missing']:
            add_list = [a for a in missing_elements(view, cfg, pl['items']) if a[0].Id.Value not in pl['locked_tagged']]
            if add_list:
                t = DB.Transaction(doc, '补标缺标构件')
                fh = t.GetFailureHandlingOptions()
                fh.SetFailuresPreprocessor(_Fails())
                t.SetFailureHandlingOptions(fh)
                t.Start()
                n_add, add_fails = create_missing(view, add_list)
                fail_total.extend(add_fails)
                st = t.Commit()
                if st != DB.TransactionStatus.Committed:
                    raise Exception('补标事务没有提交（%s），已回滚。' % st)
                pl = plan(view, cfg)          # 重新规划：新标记也一起排布
        n_del, _ = apply(view, pl, cfg, [])
        tg.Assimilate()
        pl['add_fails'] = fail_total
        return len(pl['items']), n_del, n_add, pl
    except Exception:
        try:
            tg.RollBack()
        except Exception:
            pass
        raise


def untagged_text(view, cfg, pl):
    rows = untagged_report(view, cfg, pl)
    if not rows:
        return '  没标记的构件：无'
    lines = ['  视图里没标记的构件：']
    for cat, fam, n, act in rows[:6]:
        lines.append('    %s / %s：%d 个（%s）' % (cat, fam, n, '会补' if act == '补' else '不补：该族视图里没有任何标记或太短'))
    if len(rows) > 6:
        lines.append('    …另有 %d 类' % (len(rows) - 6))
    return '\n'.join(lines)


def main():
    try:
        view = uidoc.ActiveView
        if not isinstance(view, DB.ViewPlan):
            forms.alert('请在平面视图里运行（当前是「%s」）。' % view.Name, title=TITLE)
            return
        pick = forms.CommandSwitchWindow.show(['整理当前视图的构件标记', '⚙ 设置…'], message=TITLE)
        if not pick:
            return
        if pick.startswith('⚙'):
            if open_settings():
                forms.alert('设置已保存，下次运行生效。', title=TITLE)
            return
        cfg = load()
        pl = plan(view, cfg)
        if not pl['items'] and not pl['orphans']:
            forms.alert('当前视图里没有单构件标记可整理。', title=TITLE)
            return
        add_list = missing_elements(view, cfg, pl['items']) if cfg['add_missing'] else []
        if cfg['add_missing']:
            multi_ids = set()
            for t_ in view_tags(view):
                ids_ = list(t_.GetTaggedLocalElementIds())
                if len(ids_) > 1:
                    multi_ids.update(i.Value for i in ids_)
            add_list = [a for a in add_list if a[0].Id.Value not in multi_ids and a[0].Id.Value not in pl['locked_tagged']]
        msg = ('当前视图（比例 1:%d）：\n'
               '  单构件标记 %d 个：引线末端设到构件中心，标记头选空的一侧、不压任何构件、同侧平齐（已吸附 %d 个）\n'
               '  悬空标记 %d 个、同一构件上的重复标记 %d 个%s\n'
               '  缺标构件 %d 个%s（同族已有标记的才补，补完一起重新排布）\n'
               '  多构件标记 %d 个、组内标记 %d 个（不动，当作障碍）\n%s\n'
               '整理后预计：压到构件的标记 %d 个；标记互相重叠 %d 对；引线超过上限 %d 个。\n'
               '整次一个事务，写入后回读核对，Ctrl+Z 可撤销。执行？') % (
            pl['scale'], len(pl['items']), pl['snapped'], len(pl['orphans']), len(pl['dups']), '（将删除）' if cfg['delete_orphans'] else '（不删）',
            len(add_list), '（将补标）' if cfg['add_missing'] else '（设置里勾选才补标）', pl['multi'], pl['in_group'], untagged_text(view, cfg, pl),
            pl['over_el'], pl['over_tag'], pl['far'])
        if not forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
            return
        n_items, n_del, n_add, pl2 = run_all(view, cfg)
        msg2 = '完成：整理 %d 个标记，删除悬空/重复 %d 个，补标成功 %d 个（补标后已一起重新排布）。\n整理后：压到构件 %d 个、标记重叠 %d 对、引线超限 %d 个。Ctrl+Z 可整次撤销。' % (
            n_items, n_del, n_add, pl2['over_el'], pl2['over_tag'], pl2['far'])
        if pl2.get('add_fails'):
            msg2 += '\n\n有 %d 个构件补标失败：\n' % len(pl2['add_fails']) + '\n'.join('  %d：%s' % f for f in pl2['add_fails'][:8])
        forms.alert(msg2, title=TITLE)
    except Exception:
        forms.alert('出错了（已回滚，模型没被改）：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
