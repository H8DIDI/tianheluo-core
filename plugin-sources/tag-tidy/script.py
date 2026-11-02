# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '构件标记\n一键整理'
__author__ = 'didi'
__doc__ = ('一键整理当前平面视图里的构件标记（出图用）：\n'
           '① 引线末端设到它自己的构件上（解决标记挪了以后引线还指着别的构件）；\n'
           '② 每个标记自动选空的一侧（8 个方向），不压别的标记、不压别的构件；放不下就沿引线引出去；\n'
           '③ 同一侧的标记对齐到同一行/同一列；\n'
           '④ 悬空标记（构件没了）可以删除；视图里该标但没标的构件可以补标；\n'
           '写入后逐个回读核对，引线末端不在自己构件上的整次回滚。整次一个事务，Ctrl+Z 可撤销。\n'
           '标记离构件的距离、引线上限、对齐容差、是否补标等都能在「设置…」里改。')

import io
import os
import json
import math
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
    '说明': ('gap_mm：标记离构件的最小距离（图纸 mm）；max_leader_mm：引线最长（图纸 mm）；step_mm：往外试探一步；'
           'align_mm：同侧标记对齐的容差；text_w_mm / text_h_mm：标记头图纸尺寸估计（读不到时用）；'
           'add_missing：是否补标缺标构件；delete_orphans：是否删除悬空标记；min_pipe_mm：线性构件短于这个长度（模型 mm）不补标。'),
    'gap_mm': 2.0,
    'max_leader_mm': 40.0,
    'step_mm': 3.0,
    'align_mm': 4.0,
    'text_w_mm': 15.0,
    'text_h_mm': 8.0,
    'add_missing': False,
    'delete_orphans': True,
    'min_pipe_mm': 1500.0,
}
DIRS = [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)]


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
        {'key': 'step_mm', 'label': '往外试探步长 (图纸 mm)', 'kind': 'num', 'min': 0.5, 'max': 30, 'hint': '找空位时每次往外走多远。'},
        {'key': 'align_mm', 'label': '对齐容差 (图纸 mm)', 'kind': 'num', 'min': 0, 'max': 30, 'hint': '同一侧的标记，位置差在这个范围内就对齐到同一行/列。'},
        {'key': 'text_w_mm', 'label': '标记头宽度估计 (图纸 mm)', 'kind': 'num', 'min': 3, 'max': 100, 'hint': '读不到标记头大小时用这个值。'},
        {'key': 'text_h_mm', 'label': '标记头高度估计 (图纸 mm)', 'kind': 'num', 'min': 2, 'max': 50, 'hint': '同上。'},
        {'key': 'add_missing', 'label': '补标缺标的构件', 'kind': 'bool', 'hint': '视图里同类别、同族已有标记，但某些构件没标的，用同样的标记类型补上。'},
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


def _ccw(px, py, qx, qy, rx, ry):
    return (ry - py) * (qx - px) > (qy - py) * (rx - px)


def seg_cross(ax, ay, bx, by, cx, cy, dx, dy):
    return _ccw(ax, ay, cx, cy, dx, dy) != _ccw(bx, by, cx, cy, dx, dy) and _ccw(ax, ay, bx, by, cx, cy) != _ccw(ax, ay, bx, by, dx, dy)


def seg_hits_rect(seg, r):
    x1, y1, x2, y2 = seg
    if r[0] <= x1 <= r[2] and r[1] <= y1 <= r[3]:
        return True
    if r[0] <= x2 <= r[2] and r[1] <= y2 <= r[3]:
        return True
    for ex1, ey1, ex2, ey2 in [(r[0], r[1], r[2], r[1]), (r[2], r[1], r[2], r[3]), (r[2], r[3], r[0], r[3]), (r[0], r[3], r[0], r[1])]:
        if seg_cross(x1, y1, x2, y2, ex1, ey1, ex2, ey2):
            return True
    return False


def rect(cx, cy, w, h):
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def el_segments(el, scale_ft):
    """构件在平面上的真实线段（线式）或小方块（点式）。"""
    loc = el.Location
    if isinstance(loc, DB.LocationCurve):
        c = loc.Curve
        p0, p1 = c.GetEndPoint(0), c.GetEndPoint(1)
        return [(p0.X, p0.Y, p1.X, p1.Y)]
    if isinstance(loc, DB.LocationPoint):
        p = loc.Point
        s = scale_ft
        return [(p.X - s, p.Y - s, p.X + s, p.Y + s)]
    bb = el.get_BoundingBox(None)
    if bb is not None:
        return [(bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y)]
    return []


def on_element_point(el, head):
    """构件上离标记头最近的点（引线末端要落在这里）。取不到返回 None。"""
    loc = el.Location
    if isinstance(loc, DB.LocationCurve):
        p = loc.Curve.Project(head)
        if p is not None:
            return p.XYZPoint
        return loc.Curve.Evaluate(0.5, True)
    if isinstance(loc, DB.LocationPoint):
        return loc.Point
    bb = el.get_BoundingBox(None)
    if bb is not None:
        return DB.XYZ((bb.Min.X + bb.Max.X) / 2.0, (bb.Min.Y + bb.Max.Y) / 2.0, bb.Min.Z)
    return None


def dist_to_element(el, pt):
    loc = el.Location
    if isinstance(loc, DB.LocationCurve):
        p = loc.Curve.Project(pt)
        if p is not None:
            return math.hypot(pt.X - p.XYZPoint.X, pt.Y - p.XYZPoint.Y)
    elif isinstance(loc, DB.LocationPoint):
        return math.hypot(pt.X - loc.Point.X, pt.Y - loc.Point.Y)
    return 0.0


def plan(view, cfg):
    scale = max(int(view.Scale), 1)
    m = lambda mm: mm * scale / FT          # 图纸 mm → 模型 ft
    p_mm = lambda ft: ft * FT / scale       # 模型 ft → 图纸 mm
    gap, step, max_len, tol = m(cfg['gap_mm']), m(cfg['step_mm']), m(cfg['max_leader_mm']), m(cfg['align_mm'])
    tw, th = m(cfg['text_w_mm']), m(cfg['text_h_mm'])
    tags = list(DB.FilteredElementCollector(doc, view.Id).OfClass(DB.IndependentTag))
    orphans, items, skipped = [], [], 0
    for t in tags:
        try:
            if t.IsOrphaned:
                orphans.append(t)
                continue
        except Exception:
            pass
        ids = list(t.GetTaggedLocalElementIds())
        if len(ids) != 1:
            skipped += 1
            continue
        el = doc.GetElement(ids[0])
        if el is None:
            orphans.append(t)
            continue
        try:
            refs = list(t.GetTaggedReferences())
        except Exception:
            refs = []
        if not refs:
            skipped += 1
            continue
        head = t.TagHeadPosition
        a = on_element_point(el, head)
        if a is None:
            skipped += 1
            continue
        items.append({'t': t, 'el': el, 'ref': refs[0], 'a': a, 'head': head, 'own': el.Id.Value})
    obstacles = []
    for it in items:
        for s in el_segments(it['el'], m(1.0)):
            obstacles.append((s, it['own']))
    items.sort(key=lambda x: (round(x['a'].Y, 1), round(x['a'].X, 1)))
    placed = []
    for x in items:
        a = x['a']
        base = max(tw, th) / 2.0 + gap
        if base > max_len:
            base = max_len
        best = None
        k = 0
        r = base
        while r <= max_len + 1e-9:
            for dx, dy in DIRS:
                n = math.hypot(dx, dy)
                hx, hy = a.X + dx / n * r, a.Y + dy / n * r
                rc = rect(hx, hy, tw, th)
                c_tag = sum(1 for p in placed if rect_overlap(rc, p))
                c_obs = sum(1 for s, eid in obstacles if eid != x['own'] and seg_hits_rect(s, rc))
                score = (c_tag + c_obs) * 1000 + k + (0.1 if (dx and dy) else 0)
                if best is None or score < best[0]:
                    best = (score, hx, hy, c_tag + c_obs, dx, dy)
            if best is not None and best[3] == 0:
                break
            k += 1
            r = base + k * step
        _, hx, hy, c, dx, dy = best
        x['new'] = (hx, hy)
        x['dir'] = (dx, dy)
        x['conf'] = c
        placed.append(rect(hx, hy, tw, th))
    # 对齐：上下方向对齐 X，左右方向对齐 Y（容差内、移动不超过 15 mm）
    shift_max = m(15.0)
    for axis in ('x', 'y'):
        if axis == 'x':
            grp = [x for x in items if x['dir'][0] == 0 and x['dir'][1] != 0]
            key = lambda x: x['new'][0]
        else:
            grp = [x for x in items if x['dir'][1] == 0 and x['dir'][0] != 0]
            key = lambda x: x['new'][1]
        grp.sort(key=key)
        cur = []
        for x in grp + [None]:
            if x is not None and (not cur or abs(key(x) - key(cur[-1])) <= tol):
                cur.append(x)
                continue
            if len(cur) > 1:
                mean = sum(key(y) for y in cur) / len(cur)
                for y in cur:
                    orig = y['new'][0] if axis == 'x' else y['new'][1]
                    if abs(mean - orig) <= shift_max:
                        y['new'] = (mean, y['new'][1]) if axis == 'x' else (y['new'][0], mean)
            cur = [x] if x is not None else []
    rects = [rect(x['new'][0], x['new'][1], tw, th) for x in items]
    for it in range(4):
        moved = 0
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if not rect_overlap(rects[i], rects[j]):
                    continue
                y = items[j]
                dx, dy = y['dir']
                if dx == 0 and dy == 0:
                    continue
                n = math.hypot(dx, dy)
                nx, ny = y['new'][0] + dx / n * step, y['new'][1] + dy / n * step
                if math.hypot(nx - y['a'].X, ny - y['a'].Y) > max_len:
                    continue
                y['new'] = (nx, ny)
                rects[j] = rect(nx, ny, tw, th)
                moved += 1
        if moved == 0:
            break
    overlap = sum(1 for i in range(len(rects)) for j in range(i + 1, len(rects)) if rect_overlap(rects[i], rects[j]))
    hit = 0
    for x, rc in zip(items, rects):
        if any(seg_hits_rect(s, rc) for s, eid in obstacles if eid != x['own']):
            hit += 1
    far = sum(1 for x in items if p_mm(math.hypot(x['new'][0] - x['a'].X, x['new'][1] - x['a'].Y)) > cfg['max_leader_mm'] + 1e-6)
    return {'items': items, 'orphans': orphans, 'skipped': skipped, 'overlap': overlap, 'hit': hit, 'far': far, 'scale': scale}


def missing_elements(view, cfg, items):
    """视图里同类别、同族已经有标记，但自己没标的构件：[(el, 标记类型Id)]。"""
    tagged = set(x['own'] for x in items)
    fam_type = {}
    for x in items:
        e = x['el']
        try:
            k = (e.Category.Id.Value, e.GetTypeId().Value)
        except Exception:
            continue
        fam_type.setdefault(k, {}).setdefault(x['t'].GetTypeId().Value, 0)
        fam_type[k][x['t'].GetTypeId().Value] += 1
    cat_type = {}
    for (cid, _tid), cnt in fam_type.items():
        for ttid, n in cnt.items():
            cat_type.setdefault(cid, {}).setdefault(ttid, 0)
            cat_type[cid][ttid] += n
    out = []
    min_len = cfg['min_pipe_mm'] / FT
    for cid, tmap in cat_type.items():
        tt = max(tmap.items(), key=lambda kv: kv[1])[0]
        col = DB.FilteredElementCollector(doc, view.Id).OfCategoryId(DB.ElementId(cid)).WhereElementIsNotElementType()
        for e in col:
            if e.Id.Value in tagged:
                continue
            loc = e.Location
            if isinstance(loc, DB.LocationCurve) and loc.Curve.Length < min_len:
                continue
            if loc is None:
                continue
            out.append((e, DB.ElementId(tt)))
    return out


def verify(items, tol_ft):
    """回读：每个标记的引线末端必须落在自己构件上（离构件 tol 内）。返回不合格的标记 id 列表。"""
    bad = []
    for x in items:
        try:
            le = x['t'].GetLeaderEnd(x['ref'])
        except Exception:
            bad.append(x['t'].Id.Value)
            continue
        if dist_to_element(x['el'], le) > tol_ft:
            bad.append(x['t'].Id.Value)
    return bad


def apply(view, pl, cfg, add_list):
    scale = pl['scale']
    tol_ft = 5.0 / FT * 1.0 + 20.0 / FT          # 末端离自己构件 ≤ 25 mm 才算对
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
        for x in pl['items']:
            tg = x['t']
            tg.HasLeader = True
            try:
                tg.LeaderEndCondition = DB.LeaderEndCondition.Free
            except Exception:
                pass
            tg.TagHeadPosition = DB.XYZ(x['new'][0], x['new'][1], x['head'].Z)
            try:
                tg.SetLeaderEnd(x['ref'], DB.XYZ(x['a'].X, x['a'].Y, x['head'].Z))
            except Exception:
                pass
        doc.Regenerate()
        n_add = 0
        added_items = []
        if cfg['add_missing'] and add_list:
            for e, ttid in add_list:
                try:
                    p = on_element_point(e, DB.XYZ(0, 0, 0))
                    if p is None:
                        continue
                    ref = DB.Reference(e)
                    ntag = DB.IndependentTag.Create(doc, ttid, view.Id, ref, True, DB.TagOrientation.Horizontal, DB.XYZ(p.X, p.Y, p.Z))
                    n_add += 1
                    added_items.append(ntag)
                except Exception:
                    continue
        bad = verify(pl['items'], tol_ft)
        if bad:
            raise Exception('回读核对失败：%d 个标记的引线末端没落在自己的构件上（如 %s），整次回滚，模型未改。' % (len(bad), bad[:3]))
        st = t.Commit()
        if st != DB.TransactionStatus.Committed:
            raise Exception('事务没有提交（%s），模型未改。' % st)
        return n_del, n_add
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise


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
        msg = ('当前视图（比例 1:%d）：\n'
               '  单构件标记 %d 个，将重排并把引线末端设到各自构件上\n'
               '  悬空标记 %d 个%s\n'
               '  缺标构件 %d 个%s\n'
               '  多构件标记 %d 个（不动）\n\n'
               '整理后预计：标记重叠 %d 对；压到其它构件的 %d 个；引线超过上限的 %d 个。\n'
               '整次一个事务，写入后会回读核对，Ctrl+Z 可撤销。执行？') % (
            pl['scale'], len(pl['items']), len(pl['orphans']), '（将删除）' if cfg['delete_orphans'] else '（不删）',
            len(add_list), '（将补标）' if cfg['add_missing'] else '（设置里勾选才补标）', pl['skipped'],
            pl['overlap'], pl['hit'], pl['far'])
        if not forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
            return
        n_del, n_add = apply(view, pl, cfg, add_list)
        forms.alert('完成：整理 %d 个标记，删除悬空 %d 个，补标 %d 个。Ctrl+Z 可整次撤销。' % (len(pl['items']), n_del, n_add), title=TITLE)
    except Exception:
        forms.alert('出错了（已回滚，模型没被改）：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
