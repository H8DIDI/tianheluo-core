# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '井族\n体检'
__author__ = 'didi'
__doc__ = ('你手动改井族（调井盖位置、井脖子高度、增删参数）时的「护栏」：不替你改族，只负责防止参数错乱。\n'
           '① 体检：把项目里所有井族的参数和「标准参数表」对一遍——缺哪个、GUID 不一致、同名重复、不该有的参数、没人引用的参数。\n'
           '② 改族前拍快照：记下每个族的参数（名字、GUID、实例/类型、公式）、每口井的关键参数值，并抽样记几何（体积、面数、外包盒），可同时把族另存备份。\n'
           '③ 改族后对比：载回项目后点这个，列出哪些参数被增删改、哪些井的值变了或丢了、哪些族的样子变了——该变的（你调的井盖位置）和不该变的一目了然。\n'
           '标准参数、要盯的井参数、不该有的参数名都在「设置…」里改（文档\\KaifaTools\\井族体检设置.json）。')

import io
import os
import re
import time
import traceback
import collections
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
from Autodesk.Revit import DB
from pyrevit import revit, forms
import System.Windows.Forms as WF
import json

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
TITLE = '井族体检'
FT = 304.8
DOCS = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), 'KaifaTools')
CFG_FILE = os.path.join(DOCS, '井族体检设置.json')
SNAP_DIR = os.path.join(DOCS, '井族快照')

DEFAULTS = {
    '说明': ('井族体检的设置。standard=每个井族都应该有的参数（name 参数名；guid 共享参数 GUID，空=不查；instance=是否实例参数）；'
            'banned=不该再出现的参数名；track=拍快照时记录每口井值的参数名（含内置的「相对标高的偏移」）；'
            'mark_regex=井号写法，用来认出哪些常规模型是井；sample_per_family=每族抽几口井比几何；backup_rfa=拍快照时是否把每个族另存一份 RFA。'),
    'standard': [
        {'name': '井底标高', 'guid': '84dc9842-cd46-480a-89e0-e0d6fa347ec9', 'instance': True},
        {'name': '井内深度', 'guid': '5a1b9fb3-964e-4686-9267-a6969a123044', 'instance': True},
        {'name': '井盖面标高', 'guid': 'afa45dfb-8a56-4dc7-8365-121d8a85efc4', 'instance': True},
        {'name': '井整体高度', 'guid': '56f126f5-aad6-4f53-b2f1-ad1339915370', 'instance': True},
    ],
    'banned': ['井内底标高'],
    'track': ['标记', '分区', '相对标高的偏移', '井底标高', '井内深度', '井盖面标高', '井整体高度',
              '北面开洞', '南面开洞', '东面开洞', '西面开洞', '开洞总数'],
    'mark_regex': '^[A-Za-z]{1,3}-?\\d',
    'category': 'OST_GenericModel',
    'sample_per_family': 3,
    'backup_rfa': True,
    'family_filter': '',
    'geom_tol_mm3': 1000000.0,
}

BUILTIN_TYPE_PARAMS = set(['URL', '制造商', '型号', '成本', '说明', '类型注释', '部件代码', '默认高程', '注释记号', '类型图像',
                           '导出类型到 IFC 作为', '类型 IFC 预定义类型'])


# ============================================================
# 设置
# ============================================================

def save_cfg(c):
    if not os.path.isdir(DOCS):
        os.makedirs(DOCS)
    txt = json.dumps(c, ensure_ascii=False, indent=1)
    if not isinstance(txt, type('')):
        txt = txt.decode('utf-8')
    with io.open(CFG_FILE, 'w', encoding='utf-8') as f:
        f.write(txt)


def load_cfg():
    c = json.loads(json.dumps(DEFAULTS))
    try:
        if (not os.path.isfile(CFG_FILE)) or os.path.getsize(CFG_FILE) == 0:
            save_cfg(c)
            return c
        with io.open(CFG_FILE, 'r', encoding='utf-8') as f:
            u = json.load(f)
        if isinstance(u, dict):
            for k, v in u.items():
                if k in c and k != '说明':
                    c[k] = v
    except Exception:
        pass
    return c


def settings_fields():
    return [
        {'kind': 'header', 'label': '哪些族算「井族」'},
        {'key': 'mark_regex', 'label': '井号的写法（正则）', 'kind': 'text', 'hint': '常规模型里「标记」符合这个写法的，它的族就算井族。'},
        {'key': 'family_filter', 'label': '只看族名包含这段文字的族', 'kind': 'text', 'hint': '留空 = 全部井族。'},
        {'kind': 'header', 'label': '快照与对比'},
        {'key': 'track', 'label': '每口井要记录哪些参数（逗号隔开）', 'kind': 'list',
         'hint': '拍快照时记它们的值，改族后对比，发现哪些井的值变了或丢了。'},
        {'key': 'sample_per_family', 'label': '每族抽几口井比几何', 'kind': 'num', 'min': 1, 'max': 10, 'int': True},
        {'key': 'backup_rfa', 'label': '拍快照时把每个族另存一份', 'kind': 'bool', 'bool_text': '另存（放在快照文件夹里，改坏了可以从这里载回）'},
        {'kind': 'header', 'label': '不该出现的参数'},
        {'key': 'banned', 'label': '不该再出现的参数名（逗号隔开）', 'kind': 'list', 'hint': '体检时发现就报出来。例如 井内底标高。'},
    ]


def open_settings():
    c = load_cfg()
    res = su_fields_dialog('井族体检 设置', '「标准参数」（每个族都该有的参数和 GUID）请在设置文件里改，点下面按钮用记事本打开。',
                           settings_fields(), c, DEFAULTS,
                           extra_buttons=[('用记事本打开文件', lambda: su_open_file(CFG_FILE))])
    if res is None:
        return False
    out = json.loads(json.dumps(c))
    out.update(res)
    save_cfg(out)
    return True


# ============================================================
# 取井与族
# ============================================================

def collect_wells(cfg):
    """{族名: [(元素, 井号)]}"""
    rx = re.compile(cfg['mark_regex'])
    cat = getattr(DB.BuiltInCategory, cfg['category'])
    flt = (cfg.get('family_filter') or '').strip()
    out = collections.OrderedDict()
    for e in DB.FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType().ToElements():
        try:
            p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
            mk = p.AsString() if p is not None else None
            if not mk or not rx.match(mk):
                continue
            sym = doc.GetElement(e.GetTypeId())
            fam = sym.FamilyName
            if flt and flt not in fam:
                continue
            out.setdefault(fam, []).append((e, mk))
        except Exception:
            continue
    return out


def _fam_element(name):
    for f in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        if f.Name == name:
            return f
    return None


def _data_type_name(p):
    try:
        return str(p.Definition.GetDataType().TypeId).split(':')[-1].split('-')[0]
    except Exception:
        return '?'


def read_family(fam_name):
    """打开族（只读），返回 {'params': [{name, guid, inst, shared, formula, dtype, assoc, labels}], 'types': n}。"""
    fam = _fam_element(fam_name)
    if fam is None:
        return None
    fd = doc.EditFamily(fam)
    try:
        fm = fd.FamilyManager
        params = list(fm.Parameters)
        names = [p.Definition.Name for p in params]
        used_by = collections.defaultdict(list)
        for p in params:
            f = p.Formula
            if not f:
                continue
            for nm in names:
                if nm != p.Definition.Name and re.search(r'(?<![\w一-鿿])' + re.escape(nm) + r'(?![\w一-鿿])', f):
                    used_by[nm].append(p.Definition.Name)
        labels = collections.Counter()
        try:
            for d in DB.FilteredElementCollector(fd).OfClass(DB.Dimension):
                try:
                    fp = d.FamilyLabel
                    if fp is not None:
                        labels[fp.Definition.Name] += 1
                except Exception:
                    pass
        except Exception:
            pass
        rows = []
        for p in params:
            nm = p.Definition.Name
            try:
                assoc = len(list(p.AssociatedParameters))
            except Exception:
                assoc = 0
            rows.append({
                'name': nm, 'guid': (str(p.GUID) if p.IsShared else None), 'inst': bool(p.IsInstance), 'shared': bool(p.IsShared),
                'builtin': int(p.Id.Value) < 0, 'formula': p.Formula or '', 'dtype': _data_type_name(p),
                'assoc': assoc, 'labels': labels.get(nm, 0), 'used_by': used_by.get(nm, []),
            })
        return {'params': rows, 'types': fm.Types.Size}
    finally:
        fd.Close(False)


def backup_family(fam_name, folder):
    fam = _fam_element(fam_name)
    if fam is None:
        return None
    fd = doc.EditFamily(fam)
    try:
        path = os.path.join(folder, re.sub(r'[\\/:*?"<>|]', '_', fam_name) + '.rfa')
        so = DB.SaveAsOptions()
        so.OverwriteExistingFile = True
        fd.SaveAs(path, so)
        return path
    finally:
        fd.Close(False)


# ============================================================
# 体检
# ============================================================

def audit(cfg, fams, progress=None):
    """对每个族检查：缺标准参数、GUID 不一致、实例/类型不对、不该有的、没人引用的；跨族同名不同 GUID。"""
    report = []
    guids_by_name = collections.defaultdict(lambda: collections.defaultdict(list))
    infos = {}
    for i, fam in enumerate(fams):
        if progress is not None and progress(i, len(fams)):
            return None, None
        try:
            infos[fam] = read_family(fam)
        except Exception as ex:
            infos[fam] = None
            report.append((fam, '读取失败', str(ex).split('\n')[0]))
            continue
        for p in infos[fam]['params']:
            if p['shared']:
                guids_by_name[p['name']][p['guid']].append(fam)
    std = dict((s['name'], s) for s in cfg['standard'])
    banned = set(cfg['banned'])
    sched_names = set()           # 项目里明细表在用的字段名：这些参数就算族里没人引用也不能删
    try:
        for vs in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule).ToElements():
            try:
                d = vs.Definition
                for k in range(d.GetFieldCount()):
                    sched_names.add(d.GetField(k).GetName())
            except Exception:
                continue
    except Exception:
        pass
    for fam in fams:
        info = infos.get(fam)
        if info is None:
            continue
        by_name = collections.defaultdict(list)
        for p in info['params']:
            by_name[p['name']].append(p)
        for nm, s in std.items():
            ps = by_name.get(nm)
            if not ps:
                report.append((fam, '缺少标准参数', nm))
                continue
            p = ps[0]
            if s.get('guid') and p['guid'] != s['guid']:
                report.append((fam, 'GUID 不是标准的', '%s：族里是 %s，标准是 %s' % (nm, p['guid'], s['guid'])))
            if 'instance' in s and p['inst'] != bool(s['instance']):
                report.append((fam, '实例/类型不对', '%s 应为%s参数，族里是%s' % (nm, '实例' if s['instance'] else '类型', '实例' if p['inst'] else '类型')))
        for p in info['params']:
            if p['name'] in banned:
                report.append((fam, '不该再有的参数', p['name']))
            if (not p['builtin']) and p['name'] not in std and p['name'] not in BUILTIN_TYPE_PARAMS \
                    and not p['used_by'] and not p['assoc'] and not p['labels']:
                if p['name'] in sched_names:
                    report.append((fam, '族里没人引用，但明细表在用（别删）', p['name']))
                    continue
                report.append((fam, '没人引用（可考虑删）', '%s（%s%s，公式「%s」）' % (p['name'], '实例' if p['inst'] else '类型', '·共享' if p['shared'] else '', p['formula'])))
    for nm, g in guids_by_name.items():
        if len(g) > 1:
            parts = ['%s…(%d 个族)' % (k[:8], len(v)) for k, v in g.items()]
            report.append(('(全部井族)', '同名参数有多个 GUID', '%s：%s' % (nm, '；'.join(parts))))
    return report, infos


# ============================================================
# 快照与对比
# ============================================================

def _read_val(e, name):
    try:
        if name == '相对标高的偏移':
            p = e.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
        elif name == '标记':
            p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        else:
            p = e.LookupParameter(name)
        if p is None:
            return None
        if not p.HasValue:
            return ''
        if p.StorageType == DB.StorageType.Double:
            return round(p.AsDouble() * FT, 1)
        if p.StorageType == DB.StorageType.String:
            return p.AsString() or ''
        if p.StorageType == DB.StorageType.Integer:
            return p.AsInteger()
        return p.AsValueString()
    except Exception:
        return None


def geom_sig(e):
    opt = DB.Options()
    opt.DetailLevel = DB.ViewDetailLevel.Fine
    solids = []

    def walk(ge):
        for o in ge:
            if isinstance(o, DB.GeometryInstance):
                walk(o.GetInstanceGeometry())
            elif isinstance(o, DB.Solid) and o.Faces.Size > 0:
                solids.append(o)
    walk(e.get_Geometry(opt))
    vol = sum(s.Volume for s in solids) * FT * FT * FT
    nf = sum(s.Faces.Size for s in solids)
    bb = e.get_BoundingBox(None)
    return {'vol_mm3': round(vol, 0), 'faces': nf,
            'bbox_mm': [round(bb.Min.X * FT, 1), round(bb.Min.Y * FT, 1), round(bb.Min.Z * FT, 1),
                        round(bb.Max.X * FT, 1), round(bb.Max.Y * FT, 1), round(bb.Max.Z * FT, 1)]}


def take_snapshot(cfg, wells_by_fam, infos, progress=None):
    snap = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'families': {}, 'wells': {}}
    for fam, info in infos.items():
        if info:
            snap['families'][fam] = [dict((k, p[k]) for k in ('name', 'guid', 'inst', 'shared', 'formula', 'dtype')) for p in info['params']]
    n = 0
    total = sum(len(v) for v in wells_by_fam.values())
    for fam, lst in wells_by_fam.items():
        for k, (e, mk) in enumerate(lst):
            n += 1
            if progress is not None and n % 50 == 0 and progress(n, total):
                return None
            vals = dict((nm, _read_val(e, nm)) for nm in cfg['track'])
            rec = {'mark': mk, 'family': fam, 'vals': vals}
            if k < int(cfg['sample_per_family']):
                try:
                    rec['geom'] = geom_sig(e)
                except Exception:
                    pass
            snap['wells'][str(int(e.Id.Value))] = rec
    return snap


def save_snapshot(snap, backup_paths=None):
    if not os.path.isdir(SNAP_DIR):
        os.makedirs(SNAP_DIR)
    stamp = time.strftime('%Y%m%d_%H%M%S')
    path = os.path.join(SNAP_DIR, '快照_%s.json' % stamp)
    if backup_paths:
        snap['backups'] = backup_paths
    txt = json.dumps(snap, ensure_ascii=False, indent=1)
    if not isinstance(txt, type('')):
        txt = txt.decode('utf-8')
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(txt)
    return path


def latest_snapshot():
    if not os.path.isdir(SNAP_DIR):
        return None
    files = sorted([f for f in os.listdir(SNAP_DIR) if f.startswith('快照_') and f.endswith('.json')])
    if not files:
        return None
    path = os.path.join(SNAP_DIR, files[-1])
    with io.open(path, 'r', encoding='utf-8') as f:
        return path, json.load(f)


def compare_snapshot(cfg, old, wells_by_fam, infos):
    """返回报告行列表 [(族, 类别, 说明)]。"""
    rep = []
    tol = float(cfg.get('geom_tol_mm3', 1000000.0))
    # 参数
    for fam, info in infos.items():
        if not info:
            continue
        cur = dict((p['name'], p) for p in info['params'])
        oldp = dict((p['name'], p) for p in old['families'].get(fam, []))
        if fam not in old['families']:
            rep.append((fam, '新族', '快照里没有这个族'))
            continue
        for nm in sorted(set(cur) - set(oldp)):
            p = cur[nm]
            rep.append((fam, '新增参数', '%s（%s%s%s）' % (nm, '实例' if p['inst'] else '类型', '·共享' if p['shared'] else '', ('·GUID ' + p['guid'][:8]) if p['guid'] else '')))
        for nm in sorted(set(oldp) - set(cur)):
            rep.append((fam, '参数被删', nm))
        for nm in sorted(set(cur) & set(oldp)):
            a, b = oldp[nm], cur[nm]
            if a['guid'] != b['guid']:
                rep.append((fam, '参数 GUID 变了', '%s：%s → %s' % (nm, (a['guid'] or '无')[:8], (b['guid'] or '无')[:8])))
            if a['inst'] != b['inst']:
                rep.append((fam, '实例/类型变了', '%s：%s → %s' % (nm, '实例' if a['inst'] else '类型', '实例' if b['inst'] else '类型')))
            if (a['formula'] or '') != (b['formula'] or ''):
                rep.append((fam, '公式变了', '%s：「%s」→「%s」' % (nm, a['formula'], b['formula'])))
    # 井的值
    lost = collections.defaultdict(list)
    changed = collections.defaultdict(list)
    missing_wells = []
    geom_rows = []
    cur_ids = {}
    for fam, lst in wells_by_fam.items():
        for e, mk in lst:
            cur_ids[str(int(e.Id.Value))] = (e, mk, fam)
    for wid, rec in old['wells'].items():
        if wid not in cur_ids:
            missing_wells.append(rec['mark'])
            continue
        e, mk, fam = cur_ids[wid]
        for nm, ov in rec['vals'].items():
            nv = _read_val(e, nm)
            if ov in (None, '') and nv in (None, ''):
                continue
            if nv in (None, '') and ov not in (None, ''):
                lost[(rec['family'], nm)].append(mk)
            elif nv != ov:
                changed[(rec['family'], nm)].append((mk, ov, nv))
        if rec.get('geom'):
            try:
                g = geom_sig(e)
                o = rec['geom']
                dv = g['vol_mm3'] - o['vol_mm3']
                bbd = max(abs(a - b) for a, b in zip(g['bbox_mm'], o['bbox_mm']))
                if abs(dv) > tol or g['faces'] != o['faces'] or bbd > 1.0:
                    geom_rows.append((rec['family'], mk, dv, g['faces'] - o['faces'], bbd))
            except Exception:
                pass
    for (fam, nm), lst in sorted(lost.items()):
        rep.append((fam, '井参数值丢了', '%s：%d 口井原来有值、现在是空，例如 %s' % (nm, len(lst), '、'.join(lst[:6]))))
    for (fam, nm), lst in sorted(changed.items()):
        def _f(x):
            return ('%.1f' % x) if isinstance(x, float) else ('%s' % x)
        ex = '；'.join('%s %s→%s' % (m, _f(a), _f(b)) for m, a, b in lst[:3])
        rep.append((fam, '井参数值变了', '%s：%d 口井，例如 %s' % (nm, len(lst), ex)))
    if missing_wells:
        rep.append(('(全部)', '井不见了', '%d 口，例如 %s' % (len(missing_wells), '、'.join(missing_wells[:8]))))
    for fam, mk, dv, df, bbd in geom_rows:
        rep.append((fam, '样子变了（抽样井）', '%s：体积 %+.0f mm³，面数 %+d，外包盒最大偏移 %.1f mm' % (mk, dv, df, bbd)))
    return rep


# ============================================================
# 报告
# ============================================================

def write_report(title, rows, extra=None):
    if not os.path.isdir(DOCS):
        os.makedirs(DOCS)
    path = os.path.join(DOCS, '井族报告_%s.txt' % time.strftime('%Y%m%d_%H%M%S'))
    lines = [title, time.strftime('%Y-%m-%d %H:%M:%S'), '']
    if extra:
        lines += extra + ['']
    by = collections.OrderedDict()
    for fam, kind, msg in rows:
        by.setdefault(fam, []).append((kind, msg))
    for fam, items in by.items():
        lines.append('■ ' + fam)
        for kind, msg in items:
            lines.append('   [%s] %s' % (kind, msg))
        lines.append('')
    if not rows:
        lines.append('没有发现问题。')
    with io.open(path, 'w', encoding='utf-8-sig') as f:
        f.write('\r\n'.join(lines))
    return path


def summarize(rows, limit=14):
    kinds = collections.Counter(k for _f, k, _m in rows)
    if not rows:
        return '没有发现问题。'
    s = '共 {} 条：\n'.format(len(rows)) + '\n'.join('  · {}：{}'.format(k, n) for k, n in kinds.most_common(limit))
    return s


# ============================================================
# 入口
# ============================================================

def _progress(title):
    try:
        pb = forms.ProgressBar(title=title, cancellable=True)
    except Exception:
        pb = None

    def cb(i, n):
        try:
            if pb is not None:
                pb.update_progress(i, max(n, 1))
                return bool(pb.cancelled)
        except Exception:
            pass
        return False
    return pb, cb


def _close(pb):
    try:
        if pb is not None:
            pb.close()
    except Exception:
        pass


def do_audit(cfg):
    wells = collect_wells(cfg)
    if not wells:
        forms.alert('没有找到井（常规模型「标记」符合 {} 的元素）。'.format(cfg['mark_regex']), title=TITLE)
        return
    pb, cb = _progress('正在逐个打开井族读参数（只读，不改族）…可取消')
    try:
        rows, infos = audit(cfg, list(wells.keys()), cb)
    finally:
        _close(pb)
    if rows is None:
        return
    path = write_report('井族体检（对照标准参数）', rows, ['%d 个井族，%d 口井' % (len(wells), sum(len(v) for v in wells.values()))])
    forms.alert('体检完成：{} 个井族。\n{}\n\n详细报告：{}'.format(len(wells), summarize(rows), path), title=TITLE)
    try:
        su_open_file(path)
    except Exception:
        pass


def do_snapshot(cfg):
    wells = collect_wells(cfg)
    if not wells:
        forms.alert('没有找到井。', title=TITLE)
        return
    nwell = sum(len(v) for v in wells.values())
    ans = WF.MessageBox.Show('将给 {} 个井族、{} 口井拍快照（记参数、井的值、抽样几何{}）。\n\n只读，不改任何东西。确定吗？'.format(
        len(wells), nwell, '，并把每个族另存一份 RFA' if cfg.get('backup_rfa') else ''), TITLE, WF.MessageBoxButtons.OKCancel, WF.MessageBoxIcon.Question)
    if ans != WF.DialogResult.OK:
        return
    pb, cb = _progress('正在拍快照…可取消')
    try:
        infos = {}
        fams = list(wells.keys())
        for i, f in enumerate(fams):
            if cb(i, len(fams)):
                return
            infos[f] = read_family(f)
        snap = take_snapshot(cfg, wells, infos, cb)
        if snap is None:
            return
        backups = {}
        if cfg.get('backup_rfa'):
            folder = os.path.join(SNAP_DIR, 'RFA_' + time.strftime('%Y%m%d_%H%M%S'))
            if not os.path.isdir(folder):
                os.makedirs(folder)
            for i, f in enumerate(fams):
                if cb(i, len(fams)):
                    break
                try:
                    backups[f] = backup_family(f, folder)
                except Exception as ex:
                    backups[f] = '失败：' + str(ex).split('\n')[0]
        path = save_snapshot(snap, backups)
    finally:
        _close(pb)
    forms.alert('快照已保存：\n{}\n\n现在可以去改族了。改完载回项目，再点「改族后对比」。'.format(path), title=TITLE)


def do_compare(cfg):
    got = latest_snapshot()
    if got is None:
        forms.alert('还没有快照。请先点「改族前：拍快照」。', title=TITLE)
        return
    path, old = got
    wells = collect_wells(cfg)
    pb, cb = _progress('正在对比（逐个打开井族读参数）…可取消')
    try:
        infos = {}
        fams = list(wells.keys())
        for i, f in enumerate(fams):
            if cb(i, len(fams)):
                return
            infos[f] = read_family(f)
        rows = compare_snapshot(cfg, old, wells, infos)
    finally:
        _close(pb)
    rpath = write_report('改族后对比（快照 %s）' % old.get('time'), rows)
    forms.alert('对比完成（快照时间 {}）。\n{}\n\n详细报告：{}'.format(old.get('time'), summarize(rows), rpath), title=TITLE)
    try:
        su_open_file(rpath)
    except Exception:
        pass


def main():
    try:
        cfg = load_cfg()
        labels = ['① 体检（对照标准参数）', '② 改族前：拍快照', '③ 改族后：对比快照', '⚙ 设置…']
        while True:
            pick = forms.CommandSwitchWindow.show(labels, message='井族体检：手动改族时防止参数错乱')
            if pick == labels[3]:
                if open_settings():
                    cfg = load_cfg()
                continue
            break
        if pick == labels[0]:
            do_audit(cfg)
        elif pick == labels[1]:
            do_snapshot(cfg)
        elif pick == labels[2]:
            do_compare(cfg)
    except Exception as ex:
        forms.alert('发生错误：\n{}\n\n{}'.format(ex, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
