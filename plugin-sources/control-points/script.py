# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "控制点\n坐标表"
__author__ = "didi"
__doc__ = ("测量放样用的控制点坐标表，两个方向都能做：\n"
           "① 导出：选中构件、在模型上逐个点取，或按族名找全部控制点族，导出 Excel 坐标表\n"
           "   （测量坐标 E/N/RL 米、Revit 共享坐标与内部坐标毫米，附给测量员的校正步骤）；\n"
           "② 导入：读测量员给的 CSV/TXT（编号、E、N、高程），按共享坐标在模型里放出控制点标记，\n"
           "   并导出核对表。任何项目通用，只要项目的共享坐标已经设好。")

import io
import os
import re
import json
import math
import time
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from Autodesk.Revit import DB
from Autodesk.Revit.UI.Selection import ObjectType
from System.Collections.Generic import List
from pyrevit import revit, forms
import System.Windows.Forms as WF
import System.Drawing as SD

doc = revit.doc
uidoc = revit.uidoc

TITLE = u"控制点坐标表"
FT_TO_MM = 304.8
FT_TO_M = 0.3048
APP_ID = u"THL"
APP_DATA_ID = u"control-point"
MARK_PREFIX = u"THL控制点 "
DOC_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools")
SETTINGS_FILE = os.path.join(DOC_DIR, u"控制点坐标表设置.json")
FAR_WARN_M = 20000.0   # 离内部原点超过 20 km 的点，Revit 精度会变差，多半是单位或 E/N 顺序弄错了

DEFAULTS = {
    "source": "selection",       # selection / pick / family
    "family_kw": u"控制点",
    "where": "location",         # location / bbox_top / bbox_bottom
    "prefix": u"CP-",
    "start": 1,
    "digits": 2,
    "use_mark": True,
    "add_pbp": True,
    "add_survey": True,
    "also_csv": False,
    "marker_radius_mm": 200.0,
    "marker_height_mm": 600.0,
}

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

# ---- xlsx_lite（天河落共用片段，打包时内联进脚本）--------------------------------
# 不依赖 Excel，用标准库 zipfile 直接写 .xlsx。IronPython 3.4 可用。
# 用法：xlsx_write(path, [(工作表名, 行列表, {"widths": [..], "header_rows": n, "title_rows": n}), ...])
#   行列表里的数字写成数字单元格，其余写成文字；header_rows 行加粗带底色，title_rows 行加粗（放在表头前）。
import zipfile as _xl_zip


def _xl_esc(s):
    s = u'%s' % s
    return (s.replace(u'&', u'&amp;').replace(u'<', u'&lt;').replace(u'>', u'&gt;')
             .replace(u'"', u'&quot;'))


def _xl_col(i):
    s = u''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = u'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[r] + s
    return s


def _xl_is_num(v):
    if isinstance(v, bool):
        return False
    try:
        return isinstance(v, (int, float)) or type(v).__name__ in ('long', 'Double', 'Int32', 'Int64')
    except Exception:
        return False


def _xl_sheet_xml(rows, opts):
    widths = opts.get('widths') or []
    title_rows = int(opts.get('title_rows', 0))
    header_rows = int(opts.get('header_rows', 1))
    x = [u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
         u'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if title_rows + header_rows > 0:
        x.append(u'<sheetViews><sheetView workbookViewId="0"><pane ySplit="%d" topLeftCell="A%d" '
                 u'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                 % (title_rows + header_rows, title_rows + header_rows + 1))
    if widths:
        x.append(u'<cols>')
        for i, w in enumerate(widths):
            x.append(u'<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, w))
        x.append(u'</cols>')
    x.append(u'<sheetData>')
    for r, row in enumerate(rows):
        style = 0
        if r < title_rows:
            style = 1
        elif r < title_rows + header_rows:
            style = 2
        x.append(u'<row r="%d">' % (r + 1))
        for c, v in enumerate(row):
            if v is None or v == u'':
                continue
            ref = u'%s%d' % (_xl_col(c), r + 1)
            st = u' s="%d"' % style if style else u''
            if _xl_is_num(v) and style != 2:
                x.append(u'<c r="%s"%s><v>%s</v></c>' % (ref, st, repr(float(v)) if isinstance(v, float) else v))
            else:
                x.append(u'<c r="%s" t="inlineStr"%s><is><t xml:space="preserve">%s</t></is></c>'
                         % (ref, st, _xl_esc(v)))
        x.append(u'</row>')
    x.append(u'</sheetData></worksheet>')
    return u''.join(x)


def xlsx_write(path, sheets):
    """sheets: [(name, rows, opts)]。返回 path。"""
    names = []
    for i, (name, _rows, _o) in enumerate(sheets):
        n = (u'%s' % name)
        for ch in u'[]:*?/\\':
            n = n.replace(ch, u'_')
        n = n[:31] or (u'Sheet%d' % (i + 1))
        names.append(n)
    ct = (u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          u'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          u'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          u'<Default Extension="xml" ContentType="application/xml"/>'
          u'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          u'<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>')
    for i in range(len(sheets)):
        ct += (u'<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1))
    ct += u'</Types>'
    rels = (u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            u'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            u'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            u'</Relationships>')
    wb = (u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          u'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          u'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>')
    wbrels = (u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              u'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">')
    for i, n in enumerate(names):
        wb += u'<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (_xl_esc(n), i + 1, i + 1)
        wbrels += (u'<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1))
    wb += u'</sheets></workbook>'
    wbrels += (u'<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>' % (len(names) + 1))
    wbrels += u'</Relationships>'
    styles = (u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              u'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              u'<fonts count="2"><font><sz val="11"/><name val="等线"/></font>'
              u'<font><b/><sz val="11"/><name val="等线"/></font></fonts>'
              u'<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              u'<fill><patternFill patternType="solid"><fgColor rgb="FFDDEBF7"/><bgColor indexed="64"/></patternFill></fill></fills>'
              u'<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              u'<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              u'<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              u'<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
              u'<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1">'
              u'<alignment wrapText="1" vertical="center"/></xf></cellXfs>'
              u'<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
              u'</styleSheet>')
    try:
        mode = _xl_zip.ZIP_DEFLATED
        z = _xl_zip.ZipFile(path, 'w', mode)
    except Exception:
        z = _xl_zip.ZipFile(path, 'w', _xl_zip.ZIP_STORED)
    try:
        z.writestr('[Content_Types].xml', ct.encode('utf-8'))
        z.writestr('_rels/.rels', rels.encode('utf-8'))
        z.writestr('xl/workbook.xml', wb.encode('utf-8'))
        z.writestr('xl/_rels/workbook.xml.rels', wbrels.encode('utf-8'))
        z.writestr('xl/styles.xml', styles.encode('utf-8'))
        for i, (_n, rows, opts) in enumerate(sheets):
            z.writestr('xl/worksheets/sheet%d.xml' % (i + 1), _xl_sheet_xml(rows, opts or {}).encode('utf-8'))
    finally:
        z.close()
    return path
# ---- xlsx_lite 结束 ------------------------------------------------------------

# ---------------------------------------------------------------- 设置

def load_settings():
    s = dict(DEFAULTS)
    try:
        if os.path.exists(SETTINGS_FILE):
            with io.open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
            for k in DEFAULTS:
                if k in d:
                    s[k] = d[k]
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        if not os.path.isdir(DOC_DIR):
            os.makedirs(DOC_DIR)
        with io.open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            f.write(json.dumps(s, ensure_ascii=False, indent=2))
    except Exception:
        pass


# ---------------------------------------------------------------- 坐标换算

def shared_transform():
    """内部坐标 → 共享坐标 的变换（含正北偏角和高程）。
    注意：ProjectLocation.GetTotalTransform() 是「共享 → 内部」，要取它的逆（已在真实项目里用项目基点核对过）。"""
    return doc.ActiveProjectLocation.GetTotalTransform().Inverse


def true_north_deg():
    try:
        pp = doc.ActiveProjectLocation.GetProjectPosition(DB.XYZ.Zero)
        return math.degrees(pp.Angle)
    except Exception:
        return 0.0


def site_name():
    try:
        return doc.ActiveProjectLocation.Name
    except Exception:
        return u''


def row_for(no, desc, internal_pt, tf, eid=u'', note=u''):
    sp = tf.OfPoint(internal_pt)
    return {
        "no": no, "desc": desc, "eid": eid, "note": note,
        "E": sp.X * FT_TO_M, "N": sp.Y * FT_TO_M, "RL": sp.Z * FT_TO_M,
        "sE": sp.X * FT_TO_MM, "sN": sp.Y * FT_TO_MM, "sZ": sp.Z * FT_TO_MM,
        "iX": internal_pt.X * FT_TO_MM, "iY": internal_pt.Y * FT_TO_MM, "iZ": internal_pt.Z * FT_TO_MM,
    }


# ---------------------------------------------------------------- 取点

def _eid_int(eid):
    try:
        return eid.Value
    except Exception:
        return eid.IntegerValue


def _param_str(e, bip):
    try:
        p = e.get_Parameter(bip)
        if p is not None and p.HasValue:
            return (p.AsString() or u'').strip()
    except Exception:
        pass
    return u''


def _fam_type(e):
    try:
        t = doc.GetElement(e.GetTypeId())
        if t is not None:
            fam = getattr(t, 'FamilyName', u'') or u''
            return (fam + u' : ' + t.Name) if fam else t.Name
    except Exception:
        pass
    try:
        return e.Name
    except Exception:
        return u''


def _is_marker(e):
    try:
        return isinstance(e, DB.DirectShape) and e.ApplicationDataId == APP_DATA_ID
    except Exception:
        return False


def element_point(e, where):
    """返回 (点, 说明)。标记（本插件放的锥体）取锥尖；其余按 where 取。"""
    bb = e.get_BoundingBox(None)
    if _is_marker(e) and bb is not None:
        return DB.XYZ((bb.Min.X + bb.Max.X) / 2.0, (bb.Min.Y + bb.Max.Y) / 2.0, bb.Min.Z), u'控制点标记锥尖'
    if where == 'location':
        loc = e.Location
        if isinstance(loc, DB.LocationPoint):
            return loc.Point, u'定位点'
        if isinstance(loc, DB.LocationCurve):
            c = loc.Curve
            return c.Evaluate(0.5, True), u'线中点'
    if bb is None:
        return None, u''
    cx = (bb.Min.X + bb.Max.X) / 2.0
    cy = (bb.Min.Y + bb.Max.Y) / 2.0
    if where == 'bbox_top':
        return DB.XYZ(cx, cy, bb.Max.Z), u'包围盒顶面中心'
    return DB.XYZ(cx, cy, bb.Min.Z), u'包围盒底面中心' if where == 'bbox_bottom' else u'无定位点，取包围盒底面中心'


def collect_selection(s):
    ids = list(uidoc.Selection.GetElementIds())
    if not ids:
        try:
            refs = uidoc.Selection.PickObjects(ObjectType.Element, u"框选要列入坐标表的构件，选完点左上角「完成」")
            ids = [r.ElementId for r in refs]
        except Exception:
            return None
    out = []
    for i in ids:
        e = doc.GetElement(i)
        if e is None or e.Category is None:
            continue
        out.append(e)
    return out


def collect_family(s):
    kw = (s.get('family_kw') or u'').strip().lower()
    if not kw:
        return []
    out = []
    # 只看族实例和 DirectShape（控制点标记、桩、井都在这两类里）：大模型里逐个翻全部图元要几分钟
    cand = list(DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance)) + \
        list(DB.FilteredElementCollector(doc).OfClass(DB.DirectShape))
    for e in cand:
        try:
            if e.Category is None or e.Category.CategoryType != DB.CategoryType.Model:
                continue
        except Exception:
            continue
        txt = (_fam_type(e) + u' ' + _param_str(e, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)).lower()
        if kw in txt or (_is_marker(e) and u'控制点' in kw):
            out.append(e)
    return out


def pick_points():
    """逐个点取模型表面上的点，按 Esc 结束。返回 [(点, 所在构件)]。"""
    pts = []
    while True:
        try:
            r = uidoc.Selection.PickObject(ObjectType.PointOnElement,
                                           u"点取第 %d 个控制点（落在构件表面/边上），按 Esc 结束" % (len(pts) + 1))
        except Exception:
            break
        if r is None or r.GlobalPoint is None:
            break
        pts.append((r.GlobalPoint, doc.GetElement(r.ElementId)))
    return pts


def natural_key(text):
    parts = []
    for tok in re.split(r'(\d+)', text or u''):
        parts.append((0, int(tok), u'') if tok.isdigit() else (1, 0, tok.lower()))
    return parts


def numbering(s, k):
    try:
        digits = int(s.get('digits', 2))
    except Exception:
        digits = 2
    try:
        start = int(s.get('start', 1))
    except Exception:
        start = 1
    return u'%s%s' % (s.get('prefix', u''), str(start + k).zfill(digits))


def build_rows(s):
    tf = shared_transform()
    rows = []
    src = s.get('source')
    if src == 'pick':
        picked = pick_points()
        for k, (p, host) in enumerate(picked):
            desc = u'点取 · ' + (host.Category.Name if host is not None and host.Category is not None else u'')
            rows.append(row_for(numbering(s, k), desc, p, tf,
                                _eid_int(host.Id) if host is not None else u'', u'模型表面点'))
    else:
        elems = collect_selection(s) if src == 'selection' else collect_family(s)
        if elems is None:
            return None
        items = []
        for e in elems:
            p, how = element_point(e, s.get('where', 'location'))
            if p is None:
                continue
            mark = _param_str(e, DB.BuiltInParameter.ALL_MODEL_MARK)
            items.append((p, e, how, mark))
        # 排序：先按标记（JG2 排在 JG10 前面），没有标记的按坐标（从西到东、从南到北）
        items.sort(key=lambda t: (t[3] == u'', natural_key(t[3]), round(t[0].X, 2), round(t[0].Y, 2)))
        k = 0
        for p, e, how, mark in items:
            if s.get('use_mark') and mark:
                no = mark
            else:
                no = numbering(s, k)
                k += 1
            desc = _fam_type(e)
            if _is_marker(e):
                c = _param_str(e, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                desc = c[len(MARK_PREFIX):].strip() if c.startswith(MARK_PREFIX) else u''
                desc = desc or u'控制点标记'
            rows.append(row_for(no, desc, p, tf, _eid_int(e.Id), how))
    if s.get('add_pbp'):
        try:
            bp = DB.BasePoint.GetProjectBasePoint(doc)
            rows.append(row_for(u'PBP', u'项目基点 Project Base Point', bp.Position, tf, _eid_int(bp.Id), u'参考'))
        except Exception:
            pass
    if s.get('add_survey'):
        try:
            sp = DB.BasePoint.GetSurveyPoint(doc)
            rows.append(row_for(u'SP', u'测量点 Survey Point', sp.Position, tf, _eid_int(sp.Id), u'参考'))
        except Exception:
            pass
    return rows


# ---------------------------------------------------------------- 输出

HEADER = [u'编号 No.', u'点位描述 Description', u'E 东坐标 (m)', u'N 北坐标 (m)', u'RL 高程 (m)',
          u'Revit 共享 E (mm)', u'Revit 共享 N (mm)', u'Revit 共享 Z (mm)',
          u'Revit 内部 X (mm)', u'Revit 内部 Y (mm)', u'Revit 内部 Z (mm)', u'ElementId', u'备注 Note']
WIDTHS = [11, 34, 15, 15, 12, 17, 17, 15, 16, 16, 14, 11, 22]

GUIDE = [
    [u'给测量员的使用步骤 Instructions for the surveyor'],
    [u'1. 先用本表控制点在 RTK/全站仪里做点校正（Site Calibration / Localization），至少用 3 个以上、围住施工区的点，平面和高程一起校正。'],
    [u'2. 校正后复测其余控制点：平面偏差应在 ±2~3 cm 内，高程偏差应在 ±2 cm 内；超限先查原因，不要放样。'],
    [u'3. 放样点坐标取自 Revit 共享坐标：E(m) = 共享 E(mm) ÷ 1000；N(m) = 共享 N(mm) ÷ 1000；RL(m) = 共享 Z(mm) ÷ 1000。'],
    [u'4. 「Revit 内部坐标」只供建模人员核对，不要拿去放样。'],
    [u'5. PBP = 项目基点，SP = 测量点，仅作参考，不是现场控制点。'],
    [u''],
    [u'1. Calibrate the rover/total station on at least 3 control points that surround the site (horizontal and vertical).'],
    [u'2. Check the remaining control points afterwards: ±20–30 mm horizontal, ±20 mm vertical. Do not set out if exceeded.'],
    [u'3. Set-out coordinates come from Revit shared coordinates: E(m) = shared E(mm)/1000, N(m) = shared N(mm)/1000.'],
    [u'4. Revit internal coordinates are for the modeller only — never use them for setting out.'],
]


def _r(v, n):
    try:
        return round(float(v), n)
    except Exception:
        return v


def table_rows(rows, source_txt):
    head = [
        [u'控制点坐标表 Control Point List — 项目：%s' % doc.Title],
        [u'坐标系：Revit 共享坐标（站点「%s」，正北偏角 %.4f°）；E/N/RL 单位 m，Revit 坐标单位 mm。来源：%s。生成时间 %s'
         % (site_name(), true_north_deg(), source_txt, time.strftime('%Y-%m-%d %H:%M'))],
        [u''],
        HEADER,
    ]
    body = []
    for r in rows:
        body.append([r['no'], r['desc'], _r(r['E'], 3), _r(r['N'], 3), _r(r['RL'], 3),
                     _r(r['sE'], 1), _r(r['sN'], 1), _r(r['sZ'], 1),
                     _r(r['iX'], 1), _r(r['iY'], 1), _r(r['iZ'], 1), r['eid'], r['note']])
    return head + body


def write_csv(path, rows):
    with io.open(path, 'w', encoding='utf-8-sig', newline=u'') as f:
        f.write(u','.join([h.replace(u',', u' ') for h in HEADER]) + u'\r\n')
        for r in rows:
            vals = [r['no'], r['desc'], u'%.3f' % r['E'], u'%.3f' % r['N'], u'%.3f' % r['RL'],
                    u'%.1f' % r['sE'], u'%.1f' % r['sN'], u'%.1f' % r['sZ'],
                    u'%.1f' % r['iX'], u'%.1f' % r['iY'], u'%.1f' % r['iZ'], u'%s' % r['eid'], r['note']]
            f.write(u','.join([(u'%s' % v).replace(u',', u' ') for v in vals]) + u'\r\n')


def ask_save(default_name, ext=u'xlsx', filt=u'Excel 表格 (*.xlsx)|*.xlsx'):
    dlg = WF.SaveFileDialog()
    dlg.Title = TITLE
    dlg.Filter = filt
    dlg.DefaultExt = ext
    dlg.FileName = default_name
    try:
        if not os.path.isdir(DOC_DIR):
            os.makedirs(DOC_DIR)
        dlg.InitialDirectory = DOC_DIR
    except Exception:
        pass
    if dlg.ShowDialog() != WF.DialogResult.OK:
        return None
    return dlg.FileName


def export_table(rows, source_txt, s, default_stem):
    path = ask_save(u'%s_%s.xlsx' % (default_stem, time.strftime('%Y%m%d_%H%M')))
    if not path:
        return None
    n_title = 3
    xlsx_write(path, [
        (u'控制点表', table_rows(rows, source_txt), {'widths': WIDTHS, 'title_rows': n_title, 'header_rows': 1}),
        (u'使用说明', GUIDE, {'widths': [140], 'title_rows': 1, 'header_rows': 0}),
    ])
    if s.get('also_csv'):
        try:
            write_csv(os.path.splitext(path)[0] + u'.csv', rows)
        except Exception:
            pass
    return path


# ---------------------------------------------------------------- 导出窗口

def export_dialog(s):
    f = WF.Form()
    f.Text = TITLE + u' · 导出'
    f.StartPosition = WF.FormStartPosition.CenterScreen
    f.Font = SD.Font(u'Microsoft YaHei UI', 9.0)
    f.AutoScaleMode = WF.AutoScaleMode.Dpi
    f.FormBorderStyle = WF.FormBorderStyle.FixedDialog
    f.MaximizeBox = False
    f.MinimizeBox = False
    f.ClientSize = SD.Size(520, 470)

    y = [12]

    def label(text, x=16, w=480, h=20, bold=False):
        l = WF.Label()
        l.Text = text
        l.Location = SD.Point(x, y[0])
        l.Size = SD.Size(w, h)
        if bold:
            l.Font = SD.Font(f.Font, SD.FontStyle.Bold)
        f.Controls.Add(l)
        return l

    def radio(text, checked):
        r = WF.RadioButton()
        r.Text = text
        r.Checked = checked
        r.Location = SD.Point(28, y[0])
        r.Size = SD.Size(470, 22)
        f.Controls.Add(r)
        y[0] += 24
        return r

    label(u'取点来源', bold=True); y[0] += 22
    sel_n = len(list(uidoc.Selection.GetElementIds()))
    r_sel = radio(u'选中的构件（当前已选 %d 个；没选则运行后框选）' % sel_n, s['source'] == 'selection')
    r_pick = radio(u'在模型上逐个点取（点在构件表面或边上，按 Esc 结束）', s['source'] == 'pick')
    r_fam = radio(u'族名 / 类型名 / 注释里含以下文字的全部构件：', s['source'] == 'family')
    t_kw = WF.TextBox()
    t_kw.Text = s.get('family_kw', u'')
    t_kw.Location = SD.Point(48, y[0])
    t_kw.Size = SD.Size(220, 24)
    f.Controls.Add(t_kw)
    y[0] += 34

    label(u'构件取点位置', bold=True); y[0] += 22
    cb_where = WF.ComboBox()
    cb_where.DropDownStyle = WF.ComboBoxStyle.DropDownList
    opts = [('location', u'定位点（线构件取中点；没有定位点时取包围盒底面中心）'),
            ('bbox_top', u'包围盒顶面中心（如井盖面、桩顶）'),
            ('bbox_bottom', u'包围盒底面中心（如井底、桩底）')]
    for _k, t in opts:
        cb_where.Items.Add(t)
    cb_where.SelectedIndex = max(0, [k for k, _t in opts].index(s.get('where', 'location')) if s.get('where') in [k for k, _t in opts] else 0)
    cb_where.Location = SD.Point(28, y[0])
    cb_where.Size = SD.Size(470, 24)
    f.Controls.Add(cb_where)
    y[0] += 36

    label(u'编号', bold=True); y[0] += 22
    label(u'前缀', x=28, w=40)
    t_pre = WF.TextBox(); t_pre.Text = s.get('prefix', u''); t_pre.Location = SD.Point(70, y[0] - 3); t_pre.Size = SD.Size(70, 24); f.Controls.Add(t_pre)
    label(u'起始', x=156, w=40)
    n_start = WF.NumericUpDown(); n_start.Minimum = 0; n_start.Maximum = 99999; n_start.Value = int(s.get('start', 1))
    n_start.Location = SD.Point(198, y[0] - 3); n_start.Size = SD.Size(70, 24); f.Controls.Add(n_start)
    label(u'位数', x=284, w=40)
    n_dig = WF.NumericUpDown(); n_dig.Minimum = 1; n_dig.Maximum = 6; n_dig.Value = int(s.get('digits', 2))
    n_dig.Location = SD.Point(326, y[0] - 3); n_dig.Size = SD.Size(50, 24); f.Controls.Add(n_dig)
    y[0] += 28
    c_mark = WF.CheckBox(); c_mark.Text = u'构件填了「标记」的，直接用标记作编号'; c_mark.Checked = bool(s.get('use_mark'))
    c_mark.Location = SD.Point(28, y[0]); c_mark.Size = SD.Size(470, 22); f.Controls.Add(c_mark)
    y[0] += 32

    label(u'附加', bold=True); y[0] += 22
    c_pbp = WF.CheckBox(); c_pbp.Text = u'列出项目基点（PBP）'; c_pbp.Checked = bool(s.get('add_pbp'))
    c_pbp.Location = SD.Point(28, y[0]); c_pbp.Size = SD.Size(200, 22); f.Controls.Add(c_pbp)
    c_sp = WF.CheckBox(); c_sp.Text = u'列出测量点（SP）'; c_sp.Checked = bool(s.get('add_survey'))
    c_sp.Location = SD.Point(240, y[0]); c_sp.Size = SD.Size(200, 22); f.Controls.Add(c_sp)
    y[0] += 24
    c_csv = WF.CheckBox(); c_csv.Text = u'同时输出 CSV（给全站仪/RTK 导入用）'; c_csv.Checked = bool(s.get('also_csv'))
    c_csv.Location = SD.Point(28, y[0]); c_csv.Size = SD.Size(400, 22); f.Controls.Add(c_csv)
    y[0] += 36

    ok = WF.Button(); ok.Text = u'导出…'; ok.DialogResult = WF.DialogResult.OK
    ok.Location = SD.Point(316, y[0]); ok.Size = SD.Size(90, 30); f.Controls.Add(ok)
    cancel = WF.Button(); cancel.Text = u'取消'; cancel.DialogResult = WF.DialogResult.Cancel
    cancel.Location = SD.Point(414, y[0]); cancel.Size = SD.Size(90, 30); f.Controls.Add(cancel)
    f.AcceptButton = ok
    f.CancelButton = cancel
    f.ClientSize = SD.Size(520, y[0] + 46)

    if ff_fit(f).ShowDialog() != WF.DialogResult.OK:
        return None
    s = dict(s)
    s['source'] = 'pick' if r_pick.Checked else ('family' if r_fam.Checked else 'selection')
    s['family_kw'] = t_kw.Text.strip()
    s['where'] = opts[cb_where.SelectedIndex][0]
    s['prefix'] = t_pre.Text
    s['start'] = int(n_start.Value)
    s['digits'] = int(n_dig.Value)
    s['use_mark'] = c_mark.Checked
    s['add_pbp'] = c_pbp.Checked
    s['add_survey'] = c_sp.Checked
    s['also_csv'] = c_csv.Checked
    return s


SOURCE_TXT = {'selection': u'选中的构件', 'pick': u'模型上点取', 'family': u'按名称筛选'}


def run_export():
    s = export_dialog(load_settings())
    if s is None:
        return
    save_settings(s)
    rows = build_rows(s)
    if rows is None:
        return
    real = [r for r in rows if r['no'] not in (u'PBP', u'SP')]
    if not real:
        forms.alert(u'没有取到任何点。\n\n选中构件、点取，或检查族名关键字「%s」。' % s.get('family_kw', u''), title=TITLE)
        return
    dup = sorted(set([r['no'] for r in real if [x['no'] for x in real].count(r['no']) > 1]))
    path = export_table(rows, SOURCE_TXT.get(s['source'], u''), s, u'控制点坐标表')
    if not path:
        return
    msg = u'已导出 %d 个控制点。\n\n%s' % (len(real), path)
    if dup:
        msg += u'\n\n注意：编号重复：%s' % u'、'.join(dup[:20])
    far = [r['no'] for r in real if math.hypot(r['iX'], r['iY']) / 1000.0 > FAR_WARN_M]
    if far:
        msg += u'\n\n注意：%d 个点离 Revit 内部原点超过 20 km，建模精度会变差：%s' % (len(far), u'、'.join(far[:10]))
    forms.alert(msg, title=TITLE)
    try:
        System.Diagnostics.Process.Start(u'explorer.exe', u'/select,"%s"' % path)
    except Exception:
        pass


# ---------------------------------------------------------------- 导入测量坐标 → 放标记

def _to_float(t):
    t = (t or u'').strip().replace(u'，', u'').replace(u' ', u'')
    if not t:
        raise ValueError(u'空')
    return float(t)


def _split(line):
    for sep in (u'\t', u',', u';', u'，'):
        if sep in line:
            return [x.strip() for x in line.split(sep)]
    return [x for x in line.split() if x]


def parse_points(path, order):
    """order: 'ENZ' 或 'NEZ'。返回 (点列表, 跳过行)。点 = (编号, E, N, Z, 描述)。"""
    raw = None
    for enc in ('utf-8-sig', 'gbk', 'latin-1'):
        try:
            with io.open(path, 'r', encoding=enc) as f:
                raw = f.read()
            break
        except Exception:
            continue
    if raw is None:
        raise IOError(u'读不出文件：%s' % path)
    pts, bad = [], []
    k = 0
    for ln, line in enumerate(raw.splitlines()):
        if not line.strip():
            continue
        cells = _split(line)
        # 第一列是数字 → 没有编号列
        try:
            _to_float(cells[0])
            has_name = False
        except Exception:
            has_name = True
        nums = cells[1:4] if has_name else cells[0:3]
        if len(nums) < 3:
            bad.append(ln + 1)
            continue
        try:
            a, b, z = _to_float(nums[0]), _to_float(nums[1]), _to_float(nums[2])
        except Exception:
            bad.append(ln + 1)     # 表头或注释行
            continue
        k += 1
        name = cells[0] if has_name else u'P%02d' % k
        desc = u' '.join(cells[4:]) if has_name else u' '.join(cells[3:])
        e, n = (a, b) if order == 'ENZ' else (b, a)
        pts.append((name, e, n, z, desc))
    return pts, bad


def make_marker_solid(tip, r_ft, h_ft):
    """倒锥：锥尖在 tip，向上张开。"""
    frame = DB.Frame(tip, DB.XYZ.BasisX, DB.XYZ.BasisY, DB.XYZ.BasisZ)
    p0 = tip
    p1 = DB.XYZ(tip.X + r_ft, tip.Y, tip.Z + h_ft)
    p2 = DB.XYZ(tip.X, tip.Y, tip.Z + h_ft)
    loop = DB.CurveLoop()
    loop.Append(DB.Line.CreateBound(p0, p1))
    loop.Append(DB.Line.CreateBound(p1, p2))
    loop.Append(DB.Line.CreateBound(p2, p0))
    loops = List[DB.CurveLoop]()
    loops.Add(loop)
    return DB.GeometryCreationUtilities.CreateRevolvedGeometry(frame, loops, 0.0, 2.0 * math.pi)


def existing_markers():
    out = []
    for e in DB.FilteredElementCollector(doc).OfClass(DB.DirectShape):
        if _is_marker(e):
            out.append(e)
    return out


def _set_param(e, bip, val):
    try:
        p = e.get_Parameter(bip)
        if p is not None and not p.IsReadOnly:
            p.Set(val)
            return True
    except Exception:
        pass
    return False


def run_import():
    dlg = WF.OpenFileDialog()
    dlg.Title = TITLE + u' · 选择测量坐标文件'
    dlg.Filter = u'坐标文件 (*.csv;*.txt)|*.csv;*.txt|所有文件 (*.*)|*.*'
    if dlg.ShowDialog() != WF.DialogResult.OK:
        return
    path = dlg.FileName
    order_txt = forms.CommandSwitchWindow.show(
        [u'编号, E(东), N(北), 高程', u'编号, N(北), E(东), 高程'],
        message=u'文件里的列顺序（没有编号列也可以，会自动编 P01、P02…）：')
    if not order_txt:
        return
    order = 'ENZ' if order_txt.startswith(u'编号, E') else 'NEZ'
    unit_txt = forms.CommandSwitchWindow.show([u'米 (m)', u'毫米 (mm)'], message=u'坐标单位：')
    if not unit_txt:
        return
    to_ft = (1.0 / FT_TO_M) if unit_txt.startswith(u'米') else (1.0 / FT_TO_MM)
    try:
        pts, bad = parse_points(path, order)
    except Exception as ex:
        forms.alert(u'读取失败：%s' % ex, title=TITLE)
        return
    if not pts:
        forms.alert(u'文件里没有读到坐标行（每行至少 3 个数字：E、N、高程）。', title=TITLE)
        return

    s = load_settings()
    tf = shared_transform()
    inv = tf.Inverse
    placed = []
    for name, e, n, z, desc in pts:
        internal = inv.OfPoint(DB.XYZ(e * to_ft, n * to_ft, z * to_ft))
        placed.append((name, desc, internal))
    far = [p[0] for p in placed if math.hypot(p[2].X, p[2].Y) * FT_TO_M > FAR_WARN_M]

    old = existing_markers()
    names = set([p[0] for p in placed])
    old_same = [m for m in old if _param_str(m, DB.BuiltInParameter.ALL_MODEL_MARK) in names]
    msg = u'读到 %d 个点' % len(pts)
    if bad:
        msg += u'（跳过 %d 行表头/无法识别的行）' % len(bad)
    msg += u'。\n将按共享坐标在模型里放出控制点标记（常规模型，倒锥，锥尖就是点位）。'
    if far:
        msg += (u'\n\n⚠ 有 %d 个点换算后离内部原点超过 20 km（%s…）。\n多半是单位、E/N 顺序或项目共享坐标没设好，建议先取消检查。'
                % (len(far), u'、'.join(far[:5])))
    if old_same:
        msg += u'\n\n模型里已有同编号的标记 %d 个，会先删除再放新的。' % len(old_same)
    msg += u'\n\n整次操作可以 Ctrl+Z 撤销。继续？'
    if not forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
        return

    r_ft = float(s.get('marker_radius_mm', 200.0)) / FT_TO_MM
    h_ft = float(s.get('marker_height_mm', 600.0)) / FT_TO_MM
    cat_id = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)
    made, failed = [], []
    t = DB.Transaction(doc, u'天河落 · 放置控制点标记')
    t.Start()
    try:
        if old_same:
            ids = List[DB.ElementId]([m.Id for m in old_same])
            doc.Delete(ids)
        for name, desc, p in placed:
            try:
                solid = make_marker_solid(p, r_ft, h_ft)
                ds = DB.DirectShape.CreateElement(doc, cat_id)
                ds.ApplicationId = APP_ID
                ds.ApplicationDataId = APP_DATA_ID
                shapes = List[DB.GeometryObject]()
                shapes.Add(solid)
                ds.SetShape(shapes)
                try:
                    ds.SetName(MARK_PREFIX + name)
                except Exception:
                    pass
                _set_param(ds, DB.BuiltInParameter.ALL_MODEL_MARK, name)
                _set_param(ds, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, (MARK_PREFIX + desc).strip())
                made.append((name, desc, p, ds))
            except Exception as ex:
                failed.append(u'%s：%s' % (name, ex))
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        forms.alert(u'放置失败，已撤销：\n' + traceback.format_exc(), title=TITLE)
        return

    rows = [row_for(name, desc or u'测量控制点', p, tf, _eid_int(ds.Id), u'由坐标文件放置') for name, desc, p, ds in made]
    # 核对：放好的点再换回共享坐标，与文件里的值比
    src = dict([(q[0], q) for q in pts])
    worst = 0.0
    for r in rows:
        q = src.get(r['no'])
        if q is None:
            continue
        de = r['E'] - q[1] * to_ft * FT_TO_M
        dn = r['N'] - q[2] * to_ft * FT_TO_M
        dz = r['RL'] - q[3] * to_ft * FT_TO_M
        worst = max(worst, abs(de), abs(dn), abs(dz))
    msg = u'已放置 %d 个控制点标记（「标记」= 编号，可直接用标记族标注）。\n换算回共享坐标的最大偏差 %.4f mm。' % (len(made), worst * 1000.0)
    if failed:
        msg += u'\n\n失败 %d 个：\n%s' % (len(failed), u'\n'.join(failed[:10]))
    msg += u'\n\n要不要同时导出核对表（Excel）？'
    if forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
        out = export_table(rows, u'坐标文件 ' + os.path.basename(path), s, u'控制点核对表')
        if out:
            try:
                System.Diagnostics.Process.Start(u'explorer.exe', u'/select,"%s"' % out)
            except Exception:
                pass
    if made:
        try:
            uidoc.Selection.SetElementIds(List[DB.ElementId]([m[3].Id for m in made]))
        except Exception:
            pass


def main():
    if doc is None or doc.IsFamilyDocument:
        forms.alert(u'请在项目文件里运行。', title=TITLE)
        return
    choice = forms.CommandSwitchWindow.show(
        [u'导出控制点坐标表（选中 / 点取 / 按族名）',
         u'按测量坐标放置控制点标记（读 CSV/TXT）',
         u'查看本项目的坐标设置'],
        message=u'控制点坐标表：')
    if not choice:
        return
    try:
        if choice.startswith(u'导出'):
            run_export()
        elif choice.startswith(u'按测量'):
            run_import()
        else:
            tf = shared_transform()
            o = tf.OfPoint(DB.XYZ.Zero)
            bp = DB.BasePoint.GetProjectBasePoint(doc)
            sp = DB.BasePoint.GetSurveyPoint(doc)
            b = tf.OfPoint(bp.Position)
            c = tf.OfPoint(sp.Position)
            forms.alert(
                u'站点：%s\n正北偏角：%.4f°\n\n内部原点 → 共享坐标：E %.3f  N %.3f  Z %.3f (m)\n'
                u'项目基点 → 共享坐标：E %.3f  N %.3f  Z %.3f (m)\n测量点 → 共享坐标：E %.3f  N %.3f  Z %.3f (m)\n\n'
                u'如果共享坐标还没设（三行都接近 0），先在「管理 → 坐标」里获取或指定坐标，再用本插件。'
                % (site_name(), true_north_deg(),
                   o.X * FT_TO_M, o.Y * FT_TO_M, o.Z * FT_TO_M,
                   b.X * FT_TO_M, b.Y * FT_TO_M, b.Z * FT_TO_M,
                   c.X * FT_TO_M, c.Y * FT_TO_M, c.Z * FT_TO_M), title=TITLE)
    except Exception:
        forms.alert(u'出错了：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
