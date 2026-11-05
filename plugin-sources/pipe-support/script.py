# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "一键布置\n管道支墩"
__author__ = "KAIFA"
__doc__ = ("一键布置明装管道（HDPE / MSCL）的混凝土支墩与垫层。\n"
           "先框选要布置的明装管道再点插件（没选就先框选一次）。并排同走向的管自动合成一组，共用一个统一墩。\n"
           "间距按规则表：单根 HDPE、单根 MSCL 各一张表，MSCL + HDPE 混合组按 HDPE 管径查统一墩表；超出表格的管径先跳过并列出。\n"
           "支墩：沿管长 200，管外两侧各外延 100，管底以下 300，向上做到管中心，管座按管外径挖弧槽；"
           "垫层：支墩四周各外延 100，厚 100。\n"
           "重新布置时先删这些管上旧的支墩和垫层；另有「删除选中管道的支墩」。规则运行前可改并保存。")

import math
import os
import json
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms
from Autodesk.Revit.UI.Selection import ISelectionFilter

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

# ---- ws_guard（天河落共用片段，打包时内联进脚本）--------------------------------
# 协同模型（中心文件）里，被别人占用、或中心文件里已经被改过（本地不是最新）的图元改不了；
# 只要一批里混进一个，Revit 整个事务回滚。写入前先分出来：能改的照常改，改不了的跳过并告诉用户是谁占着。
# 用法：ok, blocked = wg_split(doc, elems)；msg = wg_note(blocked) 放进确认框（没有被占用的返回空字符串）。
import Autodesk.Revit.DB as _WGDB


def wg_split(doc, elems):
    """返回 (能改的图元, 改不了的 [(图元, 原因)])。非协同模型全部能改。"""
    elems = [e for e in elems if e is not None]
    if not doc.IsWorkshared:
        return elems, []
    ok, blocked = [], []
    for e in elems:
        why = None
        try:
            st = _WGDB.WorksharingUtils.GetCheckoutStatus(doc, e.Id)
            if st == _WGDB.CheckoutStatus.OwnedByOtherUser:
                who = u''
                try:
                    who = _WGDB.WorksharingUtils.GetWorksharingTooltipInfo(doc, e.Id).Owner or u''
                except Exception:
                    pass
                why = u'被 %s 占用' % (who or u'别人')
            else:
                up = _WGDB.WorksharingUtils.GetModelUpdatesStatus(doc, e.Id)
                if up in (_WGDB.ModelUpdatesStatus.UpdatedInCentral, _WGDB.ModelUpdatesStatus.DeletedInCentral):
                    why = u'中心文件里已被改过，需先同步'
        except Exception:
            why = None
        if why:
            blocked.append((e, why))
        else:
            ok.append(e)
    return ok, blocked


def wg_note(blocked, label=None, n=6):
    """确认框里的一段说明：几个改不了、谁占着、示例。label(e) 给出图元的称呼（如井号）。"""
    if not blocked:
        return u''
    by = {}
    for e, why in blocked:
        by.setdefault(why, []).append(e)
    lines = [u'\n⚠ %d 个改不了，会跳过（其余照常写入）：' % len(blocked)]
    for why, es in sorted(by.items(), key=lambda kv: -len(kv[1])):
        names = []
        for e in es[:n]:
            try:
                names.append((label(e) if label else u'') or u'%s' % e.Id.Value)
            except Exception:
                names.append(u'%s' % e.Id.Value)
        lines.append(u'  · %s：%d 个（%s%s）' % (why, len(es), u'、'.join(names), u' 等' if len(es) > n else u''))
    lines.append(u'  请对方同步并释放，或先「与中心文件同步」后再运行一次。')
    return u'\n'.join(lines)
# ---- ws_guard 结束 ------------------------------------------------------------------

doc = revit.doc
uidoc = revit.uidoc

TITLE = u"一键布置管道支墩"
APP_ID = "PipeSupportPlugin"
FT = 304.8
MM = 1.0 / 304.8
RULES_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools")
RULES_PATH = os.path.join(RULES_DIR, u"管道支墩规则.json")
LOG_PATH = os.path.join(RULES_DIR, u"管道支墩日志.txt")
PIPE_CAT = int(DB.BuiltInCategory.OST_PipeCurves)
PIER_TAG = u"管道支墩"
PAD_TAG = u"支墩垫层"

DEFAULT_RULES = {
    "pier_len_mm": 200.0,        # 支墩沿管长
    "pier_side_mm": 100.0,       # 管外两侧外延
    "pier_below_mm": 300.0,      # 管底以下厚度
    "gasket_mm": 0.0,            # 橡胶垫厚（管座弧槽半径 = 管外径/2 + 垫厚）
    "pad_overhang_mm": 100.0,    # 垫层四周外延
    "pad_thick_mm": 100.0,       # 垫层厚
    "end_offset_mm": 300.0,      # 管段端头（弯头、三通处）第一个墩离端头的距离
    "group_gap_mm": 200.0,       # 两根管外壁净距不超过这个才算并排同组
    "group_dz_mm": 150.0,        # 同组管底高差上限
    "min_pier_gap_mm": 600.0,    # 两个墩中心距小于这个只留一个
    "hdpe_keys": [u"HDPE", u"PE"],
    "mscl_keys": [u"MSCL"],
    "mix_cap_by_hdpe": True,     # 混合组间距不超过组里最小 HDPE 的单管间距
    "material_key": u"混凝土",
    "hdpe_table": [u"50=0.9", u"65=1.0", u"80=1.2", u"100=1.3", u"125=1.5", u"150=1.7", u"200=2.0", u"250=2.3", u"300=2.5"],
    "mscl_table": [u"50=2.5", u"65=2.5", u"80=3.0", u"100=3.0", u"125=3.5", u"150=3.5", u"200=4.0", u"250=4.0", u"300=4.5"],
    "mix_table": [u"50=1.2", u"65=1.2", u"80=1.2", u"100=1.2", u"125=1.5", u"150=1.5", u"200=2.0", u"250=2.3", u"300=2.5"],
}


# ============================================================
# 小工具
# ============================================================
def _u(x):
    try:
        return u"%s" % x
    except Exception:
        return u"?"


def _idv(i):
    try:
        return i.Value
    except Exception:
        return i.IntegerValue


def _fmt(v):
    try:
        v = float(v)
        return (u"%d" % v) if abs(v - round(v)) < 1e-9 else (u"%g" % v)
    except Exception:
        return _u(v)


def parse_table(lines):
    """「DN=间距m」每行一项 -> {DN(int): 间距 mm}。写错的行抛 ValueError。"""
    out = {}
    for ln in lines or []:
        s = _u(ln).replace(u"：", u"=").replace(u":", u"=").replace(u"DN", u"").replace(u"dn", u"").strip()
        if not s:
            continue
        if u"=" not in s:
            raise ValueError(u"表格行「%s」要写成 DN=间距(m)，例如 200=2.0" % ln)
        a, b = s.split(u"=", 1)
        try:
            dn = int(round(float(a.strip())))
            sp = float(b.strip().rstrip(u"mM").strip()) * 1000.0
        except Exception:
            raise ValueError(u"表格行「%s」读不出数字" % ln)
        if dn <= 0 or sp <= 0:
            raise ValueError(u"表格行「%s」要大于 0" % ln)
        out[dn] = sp
    return out


def lookup(table, dn):
    """查间距 mm：正好有这个 DN 就用；在表的范围内但没有这一档，用比它小的最近一档（偏安全）；超出表格返回 None。"""
    if not table:
        return None
    if dn in table:
        return table[dn]
    keys = sorted(table)
    if dn < keys[0] or dn > keys[-1]:
        return None
    lower = [k for k in keys if k <= dn]
    return table[lower[-1]]


def load_rules():
    r = dict(DEFAULT_RULES)
    try:
        if os.path.isfile(RULES_PATH):
            with open(RULES_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            for k in DEFAULT_RULES:
                if k in d:
                    r[k] = d[k]
    except Exception:
        pass
    return r


def save_rules(r):
    try:
        if not os.path.isdir(RULES_DIR):
            os.makedirs(RULES_DIR)
        with open(RULES_PATH, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


FIELDS = [
    {"kind": "header", "label": u"支墩与垫层尺寸（mm）"},
    {"key": "pier_len_mm", "label": u"支墩沿管长", "kind": "num", "min": 50, "max": 2000},
    {"key": "pier_side_mm", "label": u"管外两侧外延", "kind": "num", "min": 0, "max": 1000},
    {"key": "pier_below_mm", "label": u"管底以下厚度", "kind": "num", "min": 50, "max": 3000},
    {"key": "gasket_mm", "label": u"橡胶垫厚度", "kind": "num", "min": 0, "max": 50,
     "hint": u"管座弧槽半径 = 管外径/2 + 垫厚；填 0 就是混凝土直接贴管。"},
    {"key": "pad_overhang_mm", "label": u"垫层四周外延", "kind": "num", "min": 0, "max": 1000},
    {"key": "pad_thick_mm", "label": u"垫层厚度", "kind": "num", "min": 0, "max": 500,
     "hint": u"填 0 不生成垫层。"},
    {"kind": "header", "label": u"布置与分组（mm）"},
    {"key": "end_offset_mm", "label": u"端头第一个墩离管端", "kind": "num", "min": 0, "max": 3000},
    {"key": "group_gap_mm", "label": u"并排同组的最大净距", "kind": "num", "min": 0, "max": 2000,
     "hint": u"两根平行管外壁之间的净距不超过这个，就合成一组共用统一墩。"},
    {"key": "group_dz_mm", "label": u"同组管底最大高差", "kind": "num", "min": 0, "max": 1000},
    {"key": "min_pier_gap_mm", "label": u"两墩最小中心距", "kind": "num", "min": 100, "max": 5000,
     "hint": u"比这个近的两个墩只留一个（保留覆盖管子多的那个）。"},
    {"kind": "header", "label": u"管材识别（类型名 / 管段名里包含，逗号分隔，不分大小写）"},
    {"key": "hdpe_keys", "label": u"算作 HDPE", "kind": "list"},
    {"key": "mscl_keys", "label": u"算作 MSCL", "kind": "list"},
    {"key": "material_key", "label": u"支墩材质名包含", "kind": "text",
     "hint": u"项目里找名字含这个的材质给支墩和垫层；找不到就不设材质。"},
    {"kind": "header", "label": u"支墩最大间距表（每行 DN=间距 m；不在表格范围的管径跳过）"},
    {"key": "hdpe_table", "label": u"单根 / 全 HDPE 组", "kind": "lines", "rows": 6},
    {"key": "mscl_table", "label": u"单根 / 全 MSCL 组", "kind": "lines", "rows": 6},
    {"key": "mix_table", "label": u"MSCL + HDPE 混合组\n（按组里最小 HDPE 的 DN）", "kind": "lines", "rows": 6},
    {"key": "mix_cap_by_hdpe", "label": u"混合组安全取值", "kind": "bool",
     "bool_text": u"混合组间距不超过该 HDPE 的单管间距",
     "hint": u"混合表 DN50~80 的 1.2 m 比 HDPE 单管表大；勾选后取两者较小值。"},
]


def rules_dialog(r, n_sel):
    while True:
        v = su_fields_dialog(TITLE + u" — 规则", u"已选管道 %d 根。改好点「保存」开始预布置（预布置不改模型，会先告诉你数量）。" % n_sel,
                             FIELDS, r, DEFAULT_RULES, width=720, height=680)
        if v is None:
            return None
        try:
            for k in ("hdpe_table", "mscl_table", "mix_table"):
                parse_table(v[k])
        except ValueError as ex:
            forms.alert(_u(ex), title=TITLE)
            r = dict(r)
            r.update(v)
            continue
        out = dict(r)
        out.update(v)
        return out


# ============================================================
# 读管道
# ============================================================
def pipe_kind(e, r):
    """'H'（HDPE）、'M'（MSCL）或 None。按类型名，再按管段名。"""
    names = []
    try:
        t = doc.GetElement(e.GetTypeId())
        names.append(_u(t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() or u""))
    except Exception:
        pass
    try:
        sp = e.get_Parameter(DB.BuiltInParameter.RBS_PIPE_SEGMENT_PARAM)
        seg = doc.GetElement(sp.AsElementId()) if sp is not None else None
        if seg is not None:
            names.append(_u(seg.Name))
    except Exception:
        pass
    txt = u" ".join(names).upper()
    for k in r.get("mscl_keys") or []:
        if k and _u(k).upper() in txt:
            return 'M'
    for k in r.get("hdpe_keys") or []:
        if k and _u(k).upper() in txt:
            return 'H'
    return None


def read_pipe(e, r):
    """管道 -> dict，或 (None, 跳过原因)。"""
    loc = e.Location
    c = getattr(loc, 'Curve', None)
    if c is None or not isinstance(c, DB.Line):
        return None, u"不是直管段"
    a, b = c.GetEndPoint(0), c.GetEndPoint(1)
    dxy = DB.XYZ(b.X - a.X, b.Y - a.Y, 0)
    if dxy.GetLength() < 0.05:
        return None, u"竖管"
    if abs(b.Z - a.Z) / dxy.GetLength() > 0.2:
        return None, u"坡度太大（>20%）"
    kind = pipe_kind(e, r)
    if kind is None:
        return None, u"不是 HDPE / MSCL"
    p = e.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    dn = int(round(p.AsDouble() * FT)) if p is not None and p.HasValue else 0
    p = e.get_Parameter(DB.BuiltInParameter.RBS_PIPE_OUTER_DIAMETER)
    od = p.AsDouble() if p is not None and p.HasValue and p.AsDouble() > 1e-4 else dn * MM
    table = TABLES['H' if kind == 'H' else 'M']
    if lookup(table, dn) is None:
        return None, u"DN%d 超出间距表" % dn
    return {'e': e, 'id': _idv(e.Id), 'kind': kind, 'dn': dn, 'od': od, 'a': a, 'b': b,
            'dir': dxy.Normalize(), 'dir3': (b - a).Normalize()}, None


TABLES = {}


def set_tables(r):
    TABLES['H'] = parse_table(r["hdpe_table"])
    TABLES['M'] = parse_table(r["mscl_table"])
    TABLES['X'] = parse_table(r["mix_table"])


# ============================================================
# 分组：平行、净距够近、管底高差小、投影有重叠 -> 一组
# ============================================================
def _uf_find(par, i):
    while par[i] != i:
        par[i] = par[par[i]]
        i = par[i]
    return i


def group_pipes(pipes, r):
    gap = r["group_gap_mm"] * MM
    dzmax = r["group_dz_mm"] * MM
    cell = 5.0 / 0.3048
    grid = {}
    for i, p in enumerate(pipes):
        x0, x1 = sorted((p['a'].X, p['b'].X))
        y0, y1 = sorted((p['a'].Y, p['b'].Y))
        for gx in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
            for gy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                grid.setdefault((gx, gy), set()).add(i)
    par = list(range(len(pipes)))
    seen = set()
    for cellset in grid.values():
        ids = sorted(cellset)
        for ii in range(len(ids)):
            for jj in range(ii + 1, len(ids)):
                i, j = ids[ii], ids[jj]
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                if _same_group(pipes[i], pipes[j], gap, dzmax):
                    ri, rj = _uf_find(par, i), _uf_find(par, j)
                    if ri != rj:
                        par[rj] = ri
    groups = {}
    for i in range(len(pipes)):
        groups.setdefault(_uf_find(par, i), []).append(pipes[i])
    return list(groups.values())


def _same_group(p, q, gap, dzmax):
    d = p['dir']
    if abs(d.X * q['dir'].X + d.Y * q['dir'].Y) < 0.985:      # 夹角 > 约 10°
        return False
    n = DB.XYZ(-d.Y, d.X, 0)

    def uv(pt):
        w = pt - p['a']
        return w.X * d.X + w.Y * d.Y, w.X * n.X + w.Y * n.Y
    ua, va = uv(q['a'])
    ub, vb = uv(q['b'])
    lp = (p['b'] - p['a']).X * d.X + (p['b'] - p['a']).Y * d.Y
    lo, hi = min(ua, ub), max(ua, ub)
    if hi < -0.05 or lo > lp + 0.05:                           # 走向上不重叠（容 15 mm）
        return False
    clear = abs((va + vb) / 2.0) - (p['od'] + q['od']) / 2.0
    if clear > gap:
        return False
    zp = (p['a'].Z + p['b'].Z - p['od']) / 2.0
    zq = (q['a'].Z + q['b'].Z - q['od']) / 2.0
    return abs(zp - zq) <= dzmax


# ============================================================
# 一组的布墩：沿组的公共轴线排墩位
# ============================================================
class Member(object):
    """组里的一根管在组轴线坐标下：u 范围、v（横向偏移）、z（中心）都按 u 线性插值。"""

    def __init__(self, p, origin, ax):
        n = DB.XYZ(-ax.Y, ax.X, 0)
        self.p = p

        def uvz(pt):
            w = pt - origin
            return w.X * ax.X + w.Y * ax.Y, w.X * n.X + w.Y * n.Y, pt.Z
        A, B = uvz(p['a']), uvz(p['b'])
        if A[0] > B[0]:
            A, B = B, A
        self.u0, self.v0, self.z0 = A
        self.u1, self.v1, self.z1 = B
        self.od = p['od']

    def has(self, t):
        return self.u0 - 1e-6 <= t <= self.u1 + 1e-6

    def at(self, t):
        span = self.u1 - self.u0
        k = (t - self.u0) / span if span > 1e-9 else 0.0
        return self.v0 + k * (self.v1 - self.v0), self.z0 + k * (self.z1 - self.z0)


def spacing_for(members, r):
    """一组墩位处的管子 -> 最大间距 mm。"""
    hd = [m.p['dn'] for m in members if m.p['kind'] == 'H']
    ms = [m.p['dn'] for m in members if m.p['kind'] == 'M']
    if hd and ms:
        s = lookup(TABLES['X'], min(hd))
        h = lookup(TABLES['H'], min(hd))
        if s is None:
            s = h
        elif r.get("mix_cap_by_hdpe", True) and h is not None:
            s = min(s, h)
        return s
    if hd:
        return lookup(TABLES['H'], min(hd))
    return lookup(TABLES['M'], min(ms))


def plan_group(group, r, avoid):
    """返回 (墩位列表, 跳过的墩数)。墩位 = dict(t, members, origin, ax)。"""
    longest = max(group, key=lambda p: (p['b'] - p['a']).GetLength())
    ax = longest['dir']
    origin = DB.XYZ(longest['a'].X, longest['a'].Y, 0)
    mem = [Member(p, origin, ax) for p in group]
    e = r["end_offset_mm"] * MM
    half = r["pier_len_mm"] * MM / 2.0
    cuts = sorted(set([m.u0 for m in mem] + [m.u1 for m in mem]))
    stations = []
    for x0, x1 in zip(cuts[:-1], cuts[1:]):
        if x1 - x0 < 1e-3:
            continue
        mid = (x0 + x1) / 2.0
        here = [m for m in mem if m.has(mid)]
        if not here:
            continue
        s = spacing_for(here, r) * MM
        span = x1 - x0
        if span <= 2 * e + 1e-6:
            ts = [mid]
        else:
            inner = span - 2 * e
            n = int(math.ceil(inner / s - 1e-9))
            ts = [x0 + e + inner * k / n for k in range(n + 1)] if n > 0 else [mid]
        for t in ts:
            stations.append(t)
    stations.sort()
    # 太近的合并：保留覆盖管子多的
    gapmin = r["min_pier_gap_mm"] * MM
    kept = []
    for t in stations:
        cov = len([m for m in mem if m.has(t)])
        if kept and t - kept[-1][0] < gapmin:
            if cov > kept[-1][1]:
                kept[-1] = (t, cov)
            continue
        kept.append((t, cov))
    out = []
    skipped = 0
    for t, _c in kept:
        t2 = _shift_off_fittings(t, mem, origin, ax, half, avoid)
        if t2 is None:
            skipped += 1
            continue
        here = [m for m in mem if m.has(t2)]
        if here:
            out.append({'t': t2, 'members': here, 'origin': origin, 'ax': ax})
    return out, skipped


def _world(origin, ax, t, v, z):
    n = DB.XYZ(-ax.Y, ax.X, 0)
    return DB.XYZ(origin.X + ax.X * t + n.X * v, origin.Y + ax.Y * t + n.Y * v, z)


def _hits(t, mem, origin, ax, half, avoid):
    for m in mem:
        for tt in (t - half, t, t + half):
            if not m.has(tt):
                continue
            v, z = m.at(tt)
            p = _world(origin, ax, tt, v, z)
            if _on_fitting(avoid, p.X, p.Y, p.Z):
                return True
    return False


def _shift_off_fittings(t, mem, origin, ax, half, avoid):
    """墩压到管件 / 阀门上就沿管挪开（每次 100 mm，最多 1 m）；挪不开返回 None。"""
    if not _hits(t, mem, origin, ax, half, avoid):
        return t
    step = 0.1 / 0.3048
    lo = min(m.u0 for m in mem)
    hi = max(m.u1 for m in mem)
    for k in range(1, 11):
        for sgn in (1, -1):
            t2 = t + sgn * k * step
            if lo <= t2 <= hi and any(m.has(t2) for m in mem) and not _hits(t2, mem, origin, ax, half, avoid):
                return t2
    return None


AVOID_CELL = 5.0 / 0.3048
AVOID_MARGIN = 0.1 / 0.3048
AVOID_HALF = 1.0 / 0.3048


def build_avoid_grid():
    """管件、管道附件（阀门等）的平面范围，分桶。"""
    grid = {}
    for cat in (DB.BuiltInCategory.OST_PipeFitting, DB.BuiltInCategory.OST_PipeAccessory):
        for e in DB.FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType():
            try:
                bb = e.get_BoundingBox(None)
            except Exception:
                bb = None
            if bb is None:
                continue
            pt = getattr(e.Location, 'Point', None)
            c = pt if pt is not None else (bb.Min + bb.Max) * 0.5
            hx = min((bb.Max.X - bb.Min.X) / 2.0, AVOID_HALF)
            hy = min((bb.Max.Y - bb.Min.Y) / 2.0, AVOID_HALF)
            box = (c.X - hx, c.Y - hy, max(bb.Min.Z, c.Z - AVOID_HALF), c.X + hx, c.Y + hy, min(bb.Max.Z, c.Z + AVOID_HALF))
            for gx in range(int(math.floor((box[0] - AVOID_MARGIN) / AVOID_CELL)), int(math.floor((box[3] + AVOID_MARGIN) / AVOID_CELL)) + 1):
                for gy in range(int(math.floor((box[1] - AVOID_MARGIN) / AVOID_CELL)), int(math.floor((box[4] + AVOID_MARGIN) / AVOID_CELL)) + 1):
                    grid.setdefault((gx, gy), []).append(box)
    return grid


def _on_fitting(grid, x, y, z):
    if not grid:
        return False
    for x0, y0, z0, x1, y1, z1 in grid.get((int(math.floor(x / AVOID_CELL)), int(math.floor(y / AVOID_CELL))), []):
        if (x0 - AVOID_MARGIN <= x <= x1 + AVOID_MARGIN and y0 - AVOID_MARGIN <= y <= y1 + AVOID_MARGIN
                and z0 - 1.0 <= z <= z1 + 1.0):
            return True
    return False


# ============================================================
# 几何：支墩（挖管座）+ 垫层
# ============================================================
def pier_dims(st, r):
    """一个墩位 -> 尺寸（内部单位 ft）：横向中心 vc、宽 W、底 zb、顶 zt。"""
    side = r["pier_side_mm"] * MM
    vs, zbs, zcs = [], [], []
    for m in st['members']:
        v, zc = m.at(st['t'])
        vs.append((v - m.od / 2.0, v + m.od / 2.0))
        zcs.append(zc)
        zbs.append(zc - m.od / 2.0)
    vmin = min(a for a, b in vs) - side
    vmax = max(b for a, b in vs) + side
    zb = min(zbs) - r["pier_below_mm"] * MM
    zt = min(zcs)
    return (vmin + vmax) / 2.0, vmax - vmin, zb, zt


def _box(origin, ax, t, half_len, vc, half_w, z0, h, opts):
    n = DB.XYZ(-ax.Y, ax.X, 0)
    pts = []
    for du, dv in ((-half_len, -half_w), (half_len, -half_w), (half_len, half_w), (-half_len, half_w)):
        pts.append(DB.XYZ(origin.X + ax.X * (t + du) + n.X * (vc + dv),
                          origin.Y + ax.Y * (t + du) + n.Y * (vc + dv), z0))
    loop = DB.CurveLoop()
    for i in range(4):
        loop.Append(DB.Line.CreateBound(pts[i], pts[(i + 1) % 4]))
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, h, opts)


def _cylinder(center, d3, radius, length):
    """沿 d3 方向、以 center 为中点的圆柱。"""
    ref = DB.XYZ.BasisZ if abs(d3.Z) < 0.9 else DB.XYZ.BasisX
    x = d3.CrossProduct(ref).Normalize()
    y = d3.CrossProduct(x).Normalize()
    c0 = center - d3 * (length / 2.0)
    loop = DB.CurveLoop()
    loop.Append(DB.Arc.Create(c0, radius, 0.0, math.pi, x, y))
    loop.Append(DB.Arc.Create(c0, radius, math.pi, 2 * math.pi, x, y))
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(List[DB.CurveLoop]([loop]), d3, length)


def build_pier_solids(st, r, mat_id):
    """返回 (支墩实体, 垫层实体或 None, 尺寸 dict, 管座是否挖成)。"""
    origin, ax, t = st['origin'], st['ax'], st['t']
    vc, W, zb, zt = pier_dims(st, r)
    L = r["pier_len_mm"] * MM
    opts = DB.SolidOptions(mat_id, DB.ElementId.InvalidElementId)
    pier = _box(origin, ax, t, L / 2.0, vc, W / 2.0, zb, zt - zb, opts)
    saddle = True
    rad_add = r["gasket_mm"] * MM
    for m in st['members']:
        v, zc = m.at(t)
        c = _world(origin, ax, t, v, zc)
        cyl = _cylinder(c, m.p['dir3'], m.od / 2.0 + rad_add, L + 0.4)
        try:
            DB.BooleanOperationsUtils.ExecuteBooleanOperationModifyingOriginalSolid(
                pier, cyl, DB.BooleanOperationsType.Difference)
        except Exception:
            saddle = False
    pad = None
    if r["pad_thick_mm"] > 0:
        o = r["pad_overhang_mm"] * MM
        th = r["pad_thick_mm"] * MM
        pad = _box(origin, ax, t, L / 2.0 + o, vc, W / 2.0 + o, zb - th, th, opts)
    dims = {'W': W * FT, 'L': L * FT, 'H': (zt - zb) * FT, 'zb': zb, 'zt': zt}
    return pier, pad, dims, saddle


def _ds(solid, name, comment, data_id):
    ds = DB.DirectShape.CreateElement(doc, DB.ElementId(DB.BuiltInCategory.OST_StructuralFoundation))
    ds.ApplicationId = APP_ID
    ds.ApplicationDataId = data_id
    ds.SetShape(List[DB.GeometryObject]([solid]))
    ds.Name = name
    p = ds.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    if p is not None and not p.IsReadOnly:
        p.Set(comment)
    return ds


def _host_text(st):
    return u",".join(u"%d" % m.p['id'] for m in st['members'])


def _desc(st):
    parts = []
    for m in sorted(st['members'], key=lambda m: m.at(st['t'])[0]):
        parts.append(u"%s DN%d" % (u"HDPE" if m.p['kind'] == 'H' else u"MSCL", m.p['dn']))
    return u" + ".join(parts)


def create_pier(st, r, mat_id):
    pier, pad, dims, saddle = build_pier_solids(st, r, mat_id)
    hosts = _host_text(st)
    tag = u"%dx%dx%d" % (round(dims['W']), round(dims['L']), round(dims['H']))
    made = 0
    if pier is not None and pier.Volume > 1e-6:
        _ds(pier, u"%s %s" % (PIER_TAG, tag),
            u"%s %s (%s) | 宿主: 管道 (Id:%s)" % (PIER_TAG, tag, _desc(st), hosts),
            u"Pier_Host_%s_%s" % (hosts.replace(u",", u"_"), System.Guid.NewGuid()))
        made += 1
    if pad is not None and pad.Volume > 1e-6:
        _ds(pad, u"%s (%dmm厚, 外挑%dmm)" % (PAD_TAG, round(r["pad_thick_mm"]), round(r["pad_overhang_mm"])),
            u"%s %d厚 | 宿主: 管道 (Id:%s)" % (PAD_TAG, round(r["pad_thick_mm"]), hosts),
            u"Pad_Host_%s_%s" % (hosts.replace(u",", u"_"), System.Guid.NewGuid()))
        made += 1
    return made, saddle, dims


def find_material(key):
    if not key:
        return DB.ElementId.InvalidElementId
    best = None
    for m in DB.FilteredElementCollector(doc).OfClass(DB.Material):
        nm = _u(m.Name)
        if key in nm and (best is None or len(nm) < len(_u(best.Name))):
            best = m
    return best.Id if best is not None else DB.ElementId.InvalidElementId


# ============================================================
# 已有支墩：按宿主管道 Id 找
# ============================================================
def _hosts_of(e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        c = _u(p.AsString() or u"") if p is not None else u""
    except Exception:
        c = u""
    i = c.find(u"(Id:")
    if i < 0:
        return set()
    j = c.find(u")", i)
    out = set()
    for s in c[i + 4:j].split(u","):
        try:
            out.add(int(s.strip()))
        except Exception:
            pass
    return out


def existing_supports(pipe_ids):
    """项目里本插件生成的、宿主含这些管道之一的支墩与垫层。"""
    out = []
    for e in DB.FilteredElementCollector(doc).OfClass(DB.DirectShape):
        try:
            if e.ApplicationId != APP_ID:
                continue
        except Exception:
            continue
        if _hosts_of(e) & pipe_ids:
            out.append(e)
    return out


# ============================================================
# 事务
# ============================================================
class _Fails(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, acc):
        err = False
        for f in acc.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                acc.DeleteWarning(f)
            else:
                err = True
        return DB.FailureProcessingResult.ProceedWithRollBack if err else DB.FailureProcessingResult.Continue


def _tx(name):
    t = DB.Transaction(doc, name)
    o = t.GetFailureHandlingOptions()
    o.SetFailuresPreprocessor(_Fails())
    o.SetClearAfterRollback(True)
    t.SetFailureHandlingOptions(o)
    return t


# ============================================================
# 流程
# ============================================================
class _PipeFilter(ISelectionFilter):
    def AllowElement(self, e):
        try:
            return e.Category is not None and _idv(e.Category.Id) == PIPE_CAT
        except Exception:
            return False

    def AllowReference(self, ref, p):
        return False


def selected_pipes():
    out = []
    try:
        for i in uidoc.Selection.GetElementIds():
            e = doc.GetElement(i)
            if e is not None and e.Category is not None and _idv(e.Category.Id) == PIPE_CAT:
                out.append(e)
    except Exception:
        pass
    return out


def pick_pipes():
    try:
        return list(uidoc.Selection.PickElementsByRectangle(_PipeFilter(), u"框选要布置支墩的明装管道，框完点「完成」（Esc 取消）"))
    except Exception:
        return None


def plan(elems, r, avoid=None):
    """只读：读管、分组、排墩位。返回 dict。"""
    set_tables(r)
    pipes, skipped = [], []
    for e in elems:
        p, why = read_pipe(e, r)
        if p is None:
            skipped.append((e, why))
        else:
            pipes.append(p)
    groups = group_pipes(pipes, r) if pipes else []
    if avoid is None:
        avoid = build_avoid_grid()
    stations, blocked = [], 0
    for g in groups:
        sts, sk = plan_group(g, r, avoid)
        stations.extend(sts)
        blocked += sk
    return {'pipes': pipes, 'skipped': skipped, 'groups': groups, 'stations': stations, 'blocked': blocked}


def execute(pl, r, old):
    """写模型：删旧支墩，按墩位生成。整次一个事务（每个墩一个子事务，坏一个只回滚它）。"""
    stats = {'piers': 0, 'pads': 0, 'deleted': 0, 'fail': 0, 'nosaddle': 0, 'errors': [], 'heights': []}
    mat = find_material(r.get("material_key"))
    t = _tx(TITLE)
    t.Start()
    try:
        if old:
            ids = List[DB.ElementId]([e.Id for e in old])
            doc.Delete(ids)
            stats['deleted'] = len(old)
        for st in pl['stations']:
            sub = DB.SubTransaction(doc)
            sub.Start()
            try:
                made, saddle, dims = create_pier(st, r, mat)
                sub.Commit()
                stats['piers'] += 1 if made else 0
                stats['pads'] += max(0, made - 1)
                stats['heights'].append(dims['H'])
                if not saddle:
                    stats['nosaddle'] += 1
            except Exception as ex:
                try:
                    sub.RollBack()
                except Exception:
                    pass
                stats['fail'] += 1
                if len(stats['errors']) < 10:
                    stats['errors'].append(u"%s：%s" % (_host_text(st), _u(ex)))
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return stats


def _skip_summary(skipped):
    by = {}
    for e, why in skipped:
        by.setdefault(why, []).append(e)
    lines = []
    for why, es in sorted(by.items(), key=lambda kv: -len(kv[1])):
        ids = u"、".join(u"%d" % _idv(e.Id) for e in es[:5])
        lines.append(u"  · %s：%d 根（%s%s）" % (why, len(es), ids, u" 等" if len(es) > 5 else u""))
    return lines


def write_log(pl, stats):
    try:
        if not os.path.isdir(RULES_DIR):
            os.makedirs(RULES_DIR)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(u"%s\n" % TITLE)
            for e, why in pl['skipped']:
                f.write(u"跳过 %d  %s\n" % (_idv(e.Id), why))
            for st in pl['stations']:
                vc, W, zb, zt = pier_dims(st, RULES)
                f.write(u"墩 t=%.0f  %s  宽%.0f 高%.0f  底标高%.0f  宿主 %s\n" % (st['t'] * FT, _desc(st), W * FT, (zt - zb) * FT, zb * FT, _host_text(st)))
            for x in (stats or {}).get('errors', []):
                f.write(u"失败 %s\n" % x)
    except Exception:
        pass


RULES = {}


def layout_flow():
    r = load_rules()
    elems = selected_pipes()
    if not elems:
        elems = pick_pipes()
        if not elems:
            return
    r2 = rules_dialog(r, len(elems))
    if r2 is None:
        return
    save_rules(r2)
    RULES.clear()
    RULES.update(r2)
    pl = plan(elems, r2)
    if not pl['stations']:
        msg = [u"没有可布置的支墩。"] + _skip_summary(pl['skipped'])
        forms.alert(u"\n".join(msg), title=TITLE)
        return
    ids = set(p['id'] for p in pl['pipes'])
    old = existing_supports(ids)
    old_ok, old_blocked = wg_split(doc, old)
    if old_blocked:
        forms.alert(u"这些管上已有的支墩有 %d 个被别人占用、删不了，为避免重复，本次不布置。%s" % (len(old_blocked), wg_note(old_blocked)), title=TITLE)
        return
    n_mix = sum(1 for g in pl['groups'] if len(set(p['kind'] for p in g)) > 1)
    n_multi = sum(1 for g in pl['groups'] if len(g) > 1)
    q = [u"预布置完成（还没有改模型）：",
         u"可布置管道 %d 根，分成 %d 组（多根并排的 %d 组，其中 MSCL + HDPE 混合 %d 组）。" % (len(pl['pipes']), len(pl['groups']), n_multi, n_mix),
         u"将生成支墩 %d 个%s。" % (len(pl['stations']), u"、垫层 %d 块" % len(pl['stations']) if r2["pad_thick_mm"] > 0 else u""),
         u"支墩：沿管长 %s，两侧外延 %s，管底以下 %s，做到管中心；垫层外延 %s、厚 %s（mm）。" % (
             _fmt(r2["pier_len_mm"]), _fmt(r2["pier_side_mm"]), _fmt(r2["pier_below_mm"]), _fmt(r2["pad_overhang_mm"]), _fmt(r2["pad_thick_mm"]))]
    if pl['blocked']:
        q.append(u"压在管件 / 阀门上、挪 1 m 内也避不开的墩位 %d 个，不布置。" % pl['blocked'])
    if old:
        q.append(u"这些管上已有支墩 / 垫层 %d 个，会先删除再重新生成。" % len(old))
    if pl['skipped']:
        q.append(u"跳过的管道 %d 根：" % len(pl['skipped']))
        q.extend(_skip_summary(pl['skipped']))
    q.append(u"确认后开始生成？（整次可 Ctrl+Z 撤销）")
    if not forms.alert(u"\n".join(q), title=TITLE, yes=True, no=True):
        return
    stats = execute(pl, r2, old_ok)
    write_log(pl, stats)
    hs = stats['heights']
    rep = [u"完成：支墩 %d 个，垫层 %d 块；删除旧的 %d 个。" % (stats['piers'], stats['pads'], stats['deleted'])]
    if hs:
        rep.append(u"支墩高度 %.0f ~ %.0f mm。" % (min(hs), max(hs)))
    if stats['nosaddle']:
        rep.append(u"有 %d 个墩管座弧槽没挖成（保留成方墩），请检查。" % stats['nosaddle'])
    if stats['fail']:
        rep.append(u"失败 %d 个：" % stats['fail'])
        rep.extend(u"  · " + x for x in stats['errors'])
    rep.append(u"明细日志：%s" % LOG_PATH)
    forms.alert(u"\n".join(rep), title=TITLE)


def delete_flow():
    elems = selected_pipes()
    if not elems:
        elems = pick_pipes()
        if not elems:
            return
    ids = set(_idv(e.Id) for e in elems)
    old = existing_supports(ids)
    if not old:
        forms.alert(u"选中的 %d 根管道上没有本插件生成的支墩。" % len(elems), title=TITLE)
        return
    ok, blocked = wg_split(doc, old)
    if not forms.alert(u"将删除选中管道上的支墩与垫层 %d 个。%s\n确认？" % (len(ok), wg_note(blocked)), title=TITLE, yes=True, no=True):
        return
    t = _tx(u"删除管道支墩")
    t.Start()
    doc.Delete(List[DB.ElementId]([e.Id for e in ok]))
    t.Commit()
    forms.alert(u"已删除 %d 个。" % len(ok), title=TITLE)


OP_LAYOUT = u"布置管道支墩（框选明装管道）"
OP_DELETE = u"删除选中管道的支墩与垫层"


def main():
    try:
        op = forms.CommandSwitchWindow.show([OP_LAYOUT, OP_DELETE], message=u"请选择操作：")
        if op == OP_LAYOUT:
            layout_flow()
        elif op == OP_DELETE:
            delete_flow()
    except Exception as ex:
        forms.alert(u"出错了（已回滚，模型没被改）：\n%s\n\n%s" % (_u(ex), _u(traceback.format_exc())), title=TITLE)


if __name__ == '__main__':
    main()
