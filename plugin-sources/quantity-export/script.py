# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "一键框选\n提量表格"
__author__ = "KAIFA"
__doc__ = ("框选（或整个项目、当前视图）里的构件，先在窗口里按标签页给出表格，看完可导出 Excel（xlsx）：\n"
           "结构：混凝土体积（m³）、钢材重量（t，按材料密度算）、钢筋按直径汇总（根数、总长、重量）；\n"
           "小市政：井按系列、族类型、深度档汇总并附逐口明细，排管和导管按族类型汇总根数、长度、孔数、孔·米，松木桩根数和垫层块数、体积。\n"
           "排管、松木桩、垫层各自单算，不会混进混凝土表。也可以在 Revit 里生成明细表（之后模型改了自动更新）。只读，不改模型（生成明细表除外）。")

import re
import math
import io
import os
import time
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
from Autodesk.Revit.UI.Selection import ISelectionFilter

doc = revit.doc
uidoc = revit.uidoc

TITLE = u"一键框选提量表格"
FT3_TO_M3 = 0.3048 ** 3
FT_TO_M = 0.3048
FT_TO_MM = 304.8
STEEL_DENSITY = 7850.0      # kg/m³，钢筋统一用这个密度
STEEL_MIN_DENSITY = 6000.0  # 材料密度达到这个值，按钢材算
OUT_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools")

QTO_BICS = [
    DB.BuiltInCategory.OST_StructuralColumns,
    DB.BuiltInCategory.OST_StructuralFraming,
    DB.BuiltInCategory.OST_Walls,
    DB.BuiltInCategory.OST_StructuralFoundation,
    DB.BuiltInCategory.OST_Floors,
    DB.BuiltInCategory.OST_Rebar,
    DB.BuiltInCategory.OST_GenericModel,      # 只认井（有井号的）
    DB.BuiltInCategory.OST_Conduit,
]
QTO_CAT_IDS = set(int(b) for b in QTO_BICS)

# 明细表：(表名, 类别, 字段候选名)。字段名按 Revit 里显示的名字匹配，中英文都试。
LEVEL_FIELD = [u'标高', u'基准标高', u'基准约束', u'Level', u'Base Level', u'Base Constraint']
SCHEDULE_SPECS = [
    (u'KAIFA-钢筋明细表', DB.BuiltInCategory.OST_Rebar, [
        [u'钢筋直径', u'Bar Diameter'],
        [u'总钢筋长度', u'Total Bar Length', u'Total Length'],
        [u'数量', u'数量(按钢筋集)', u'Quantity'],
    ]),
    (u'KAIFA-结构柱明细表', DB.BuiltInCategory.OST_StructuralColumns, [
        [u'底部标高', u'Base Level'], [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
    (u'KAIFA-结构框架明细表', DB.BuiltInCategory.OST_StructuralFraming, [
        LEVEL_FIELD, [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
    (u'KAIFA-墙体混凝土明细表', DB.BuiltInCategory.OST_Walls, [
        [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
    (u'KAIFA-结构基础明细表', DB.BuiltInCategory.OST_StructuralFoundation, [
        LEVEL_FIELD, [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
    (u'KAIFA-楼板明细表', DB.BuiltInCategory.OST_Floors, [
        LEVEL_FIELD, [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
]


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

# ---- well_config（天河落共用片段，打包时内联进脚本）-----------------------------
# 井大样系列插件（一键井展开图 / 一键井大样标注 / 图纸归类与图框编号 / 导出井大样DWG）共用的项目配置。
# 配置文件：文档\KaifaTools\井大样配置.json。第一次运行时自动写出一份默认配置（与原来写死的规则完全一样），
# 换项目时只改这个文件：图纸名前缀、图号前缀、井编号系列（前缀 → 系统名 → 系统代码）、图框 logo 位置。
import io as _wc_io
import os as _wc_os
import json as _wc_json
import re as _wc_re

WCFG_FILE = _wc_os.path.join(
    __import__('System').Environment.GetFolderPath(__import__('System').Environment.SpecialFolder.MyDocuments),
    u"KaifaTools", u"井大样配置.json")

WCFG_DEFAULTS = {
    u"说明": (u"井大样系列插件的项目配置。改完保存，下次点插件生效；删掉这个文件会恢复默认。"
            u"series 里每一项是一个井编号系列：prefix=井号前缀（井号形如 AA-01、T-068、室内-A-01，前缀后面可以有一个 -，再跟数字），"
            u"system=系统名（写进图纸的「图纸系统」参数、用来归类），code=系统代码（图号模板里的 {系统码}），label=菜单里显示的名字。"
            u"顺序就是菜单和排序的顺序；匹配时长的前缀优先（AA 先于 A）。"),
    u"sheet_name_prefix": u"井大样_",
    u"sheet_number_prefix": u"AG-",
    u"indoor_prefix": u"室内-",
    u"system_param": u"图纸系统",
    u"series": [
        {u"prefix": u"室内-A", u"system": u"室内强电井", u"code": u"NQ", u"label": u"室内-A- 强电井"},
        {u"prefix": u"室内-T", u"system": u"室内通信井", u"code": u"NT", u"label": u"室内-T- 通信井"},
        {u"prefix": u"室内-E", u"system": u"室内弱电井", u"code": u"NR", u"label": u"室内-E- 弱电井"},
        {u"prefix": u"室内-L", u"system": u"室内低压拉线井", u"code": u"ND", u"label": u"室内-L- 低压拉线井"},
        {u"prefix": u"AA", u"system": u"强电井", u"code": u"QD", u"label": u"AA- 强电特型井"},
        {u"prefix": u"A", u"system": u"强电井", u"code": u"QD", u"label": u"A- 强电井"},
        {u"prefix": u"BB", u"system": u"弱电井", u"code": u"RD", u"label": u"BB- 弱电特型井"},
        {u"prefix": u"B", u"system": u"弱电井", u"code": u"RD", u"label": u"B- 弱电井"},
        {u"prefix": u"T", u"system": u"通信井", u"code": u"TX", u"label": u"T- 通信井"},
        {u"prefix": u"L", u"system": u"低压拉线井", u"code": u"DY", u"label": u"L- 低压拉线井"},
        {u"prefix": u"E", u"system": u"预制井", u"code": u"YZ", u"label": u"E- 预制井"},
        {u"prefix": u"Y", u"system": u"雨水井", u"code": u"YS", u"label": u"Y- 雨水井"},
    ],
    u"other": {u"system": u"其他", u"code": u"QT"},
    # 图名模板（按井号前缀，长前缀优先）：{井号} 换成井号，如 T-001。没有模板的井沿用「井大样_」前缀。
    # 图纸集合（按井号前缀）：新图放进对应的图纸集合，便于按集合导出 PDF。名字须与项目里的图纸集合一致。
    u"sheet_collection_by_prefix": {
        u"AA": u"275KV井大样", u"BB": u"275KV井大样", u"A": u"11KV井大样", u"B": u"11KV井大样",
        u"E": u"ELV井大样", u"L": u"LV井大样", u"T": u"TELCO井大样",
    },
    u"sheet_name_templates": {
        u"AA": u"BTP-IF1 275Kv-{井号} Detail Drawing",
        u"BB": u"BTP-IF1 275Kv-{井号} Detail Drawing",
        u"A": u"BTP-IF1 11Kv-{井号} Detail Drawing",
        u"B": u"BTP-IF1 11Kv-{井号} Detail Drawing",
        u"E": u"BTP-IF1 ELV-{井号} Detail Drawing",
        u"L": u"BTP-IF1 Lv-{井号} Detail Drawing",
        u"T": u"BTP-IF1 Telco-{井号} Detail Drawing",
    },
    u"logo": {u"enabled": True, u"file": u"frame_logo.jpg", u"x0": 736.54, u"y0": 141.10, u"w": 91.49,
              u"说明": u"导出 DWG 后补插的图框 logo：x0/y0 = logo 左下角在 Layout1 里的坐标，w = 宽度（按 A1 横向图框标定）。换图框要改；enabled=false 不补 logo。"},
}

_WCFG_CACHE = [None]
# 本次运行之前配置文件还不存在（第一次在这台电脑上用井大样插件）。wcfg_load 会写出默认文件，所以要在调用它之前记下来。
WCFG_FIRST_RUN = not _wc_os.path.exists(WCFG_FILE)


def _wcfg_merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _wcfg_merge(out[k], v)
        else:
            out[k] = v
    return out


def wcfg_load(write_default=True):
    """读项目配置（缺的项用默认值补齐）。文件不存在时写出默认配置，方便用户找到并修改。"""
    if _WCFG_CACHE[0] is not None:
        return _WCFG_CACHE[0]
    cfg = _wcfg_merge(WCFG_DEFAULTS, {})
    try:
        if _wc_os.path.exists(WCFG_FILE):
            with _wc_io.open(WCFG_FILE, 'r', encoding='utf-8-sig') as f:
                cfg = _wcfg_merge(cfg, _wc_json.load(f))
        elif write_default:
            d = _wc_os.path.dirname(WCFG_FILE)
            if not _wc_os.path.isdir(d):
                _wc_os.makedirs(d)
            with _wc_io.open(WCFG_FILE, 'w', encoding='utf-8') as f:
                f.write(_wc_json.dumps(WCFG_DEFAULTS, ensure_ascii=False, indent=2))
    except Exception:
        cfg = _wcfg_merge(WCFG_DEFAULTS, {})
    # series 至少要有 prefix
    good = []
    for s in cfg.get(u"series") or []:
        try:
            p = (s.get(u"prefix") or u"").strip()
        except Exception:
            continue
        if not p:
            continue
        s = dict(s)
        s[u"prefix"] = p
        s.setdefault(u"system", p)
        s.setdefault(u"code", p)
        s.setdefault(u"label", p + u"-")
        good.append(s)
    cfg[u"series"] = good or list(WCFG_DEFAULTS[u"series"])
    _WCFG_CACHE[0] = cfg
    return cfg


def wcfg_series_of(mark, cfg=None, strict=False):
    """井号属于哪个系列：前缀（长的优先）后面可以有一个 -，必须紧跟数字。认不出返回 None。
    strict=True 时前缀后面必须是「-数字」（井的标记用这个：雨水井的 A1、BB24 这类不带 - 的不算强电/弱电井）。"""
    cfg = cfg or wcfg_load()
    m = (mark or u"").strip()
    for s in sorted(cfg[u"series"], key=lambda x: -len(x[u"prefix"])):
        p = s[u"prefix"]
        if m.startswith(p):
            rest = m[len(p):]
            if rest[:1] == u"-":
                rest = rest[1:]
            elif strict:
                continue
            if rest[:1].isdigit():
                return s
    return None


def wcfg_series_index(s, cfg=None):
    cfg = cfg or wcfg_load()
    for i, x in enumerate(cfg[u"series"]):
        if x is s:
            return i
    return len(cfg[u"series"])


def wcfg_natural_key(text):
    out = []
    for tok in _wc_re.split(r'(\d+)', text or u''):
        out.append((0, int(tok), u'') if tok.isdigit() else (1, 0, tok.lower()))
    return out


def wcfg_well_sort_key(mark, cfg=None, strict=True):
    """先按系列在配置里的顺序，同系列按号（自然排序：A-2 在 A-10 前面）。"""
    s = wcfg_series_of(mark, cfg, strict)
    return (wcfg_series_index(s, cfg) if s is not None else 9999, wcfg_natural_key(mark))


def wcfg_sheet_name(mark, cfg=None):
    """井号 → 图名（按配置里的图名模板）。没有对应模板时返回 None，调用方沿用「井大样_」前缀。
    模板前缀按长度从长到短匹配，且前缀后面必须紧跟 "-"（AA-04 不会误判成 A 系列）。"""
    cfg = cfg or wcfg_load()
    tpls = cfg.get(u"sheet_name_templates") or {}
    m = (mark or u"").strip()
    for key in sorted(tpls.keys(), key=lambda k: -len(k)):
        if m.startswith(key + u"-"):
            return tpls[key].replace(u"{井号}", m)
    return None
# ---- well_config 结束 ----------------------------------------------------------


def wcfg_collection_name(mark, cfg=None):
    """井号 → 图纸集合名（按配置 sheet_collection_by_prefix，长前缀优先，前缀后紧跟 -）。没有对应返回 None。"""
    cfg = cfg or wcfg_load()
    maps = cfg.get(u"sheet_collection_by_prefix") or {}
    m = (mark or u"").strip()
    for key in sorted(maps.keys(), key=lambda k: -len(k)):
        if m.startswith(key + u"-"):
            return maps[key]
    return None


# ---- 井位置缓存（井没动就不重新生成）：Documents\KaifaTools\井大样位置缓存.json ----
def wcfg_cache_path():
    return _wc_os.path.join(_wc_os.path.dirname(WCFG_FILE), u"井大样位置缓存.json")


def wcfg_cache_load():
    """读位置缓存：{井号: {bbox, sheet_id, name, number}}。文件不存在或坏了返回空。"""
    try:
        with _wc_io.open(wcfg_cache_path(), 'r', encoding='utf-8-sig') as f:
            d = _wc_json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def wcfg_cache_save(d):
    """先把内容转成文字，转成功才写文件（避免转换失败时把旧缓存清成空文件）。"""
    try:
        txt = _wc_json.dumps(d, ensure_ascii=False, indent=1)
    except Exception:
        return
    try:
        p = wcfg_cache_path()
        dd = _wc_os.path.dirname(p)
        if not _wc_os.path.isdir(dd):
            _wc_os.makedirs(dd)
        with _wc_io.open(p, 'w', encoding='utf-8') as f:
            f.write(txt)
    except Exception:
        pass

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

WCFG = wcfg_load()
DEPTH_STEP = 500.0         # 井深度按 500 mm 一档汇总（预制井按档订货）
_HOLES = re.compile(u'(\\d+)\\s*孔')
PILE_KEYS = (u'松木桩', u'Φ75')
PAD_KEYS = (u'垫层',)
# 排管（含雨水沟）：族名以这些前缀开头，或名字里有「排管」「N孔」
BANK_PREFIXES = (u'TELCO', u'ELV', u'LV-', u'ELE-', u'雨水沟')


def _u(x):
    if x is None:
        return u''
    return x if isinstance(x, type(u'')) else u'%s' % x


def _eid(x):
    """ElementId 的整数值；取不到返回 -1。"""
    if x is None:
        return -1
    try:
        return int(x.Value)
    except Exception:
        try:
            return int(x.IntegerValue)
        except Exception:
            return -1


def _mark(e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        return (p.AsString() or u'').strip() if p is not None else u''
    except Exception:
        return u''


def _is_well(e):
    """井：常规模型族实例，标记（井号）属于配置里的某个井系列。"""
    try:
        return (isinstance(e, DB.FamilyInstance) and _eid(e.Category.Id) == int(DB.BuiltInCategory.OST_GenericModel)
                and wcfg_series_of(_mark(e), WCFG, True) is not None)
    except Exception:
        return False


def _ftname(e):
    try:
        s = e.Symbol
        return _u(s.FamilyName), _u(s.Name)
    except Exception:
        try:
            t = doc.GetElement(e.GetTypeId())
            return (_u(t.FamilyName) if t is not None else u''), (_u(t.Name) if t is not None else u'')
        except Exception:
            return u'', u''


def _is_bank(e):
    """排管（含雨水沟）：结构框架里族名像排管的；真正的梁仍按混凝土/钢材算。"""
    f, t = _ftname(e)
    fu = f.upper()
    if any(fu.startswith(k) for k in BANK_PREFIXES):
        return True
    return u'排管' in f or u'排管' in t or bool(_HOLES.search(f + u' ' + t))


def _pile_or_pad(e):
    """结构基础里的松木桩 / 垫层；其他返回 None。"""
    f, t = _ftname(e)
    txt = f + u' ' + t + u' ' + _u(e.Name)
    if any(k in txt for k in PILE_KEYS):
        return u'松木桩'
    if any(k in txt for k in PAD_KEYS):
        return u'垫层'
    return None


def _is_qto(e):
    try:
        if e is None or e.Category is None:
            return False
        c = _eid(e.Category.Id)
        if c not in QTO_CAT_IDS:
            return False
        if c == int(DB.BuiltInCategory.OST_GenericModel):
            return _is_well(e)
        return True
    except Exception:
        return False


class _QtoFilter(ISelectionFilter):
    def AllowElement(self, e):
        return _is_qto(e)

    def AllowReference(self, r, p):
        return False


def _depth_mm(e):
    """井内深度（mm）：优先「井内深度」参数，没有就用 偏移 − 井底标高。"""
    p = e.LookupParameter(u'井内深度')
    if p is not None and p.HasValue and p.StorageType == DB.StorageType.Double and p.AsDouble() > 0:
        return p.AsDouble() * FT_TO_MM
    b = e.LookupParameter(u'井底标高')
    o = e.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
    if b is not None and b.HasValue and o is not None and o.HasValue:
        return (o.AsDouble() - b.AsDouble()) * FT_TO_MM
    return None


def _length_m(e):
    try:
        loc = e.Location
        if isinstance(loc, DB.LocationCurve):
            return loc.Curve.Length * FT_TO_M
    except Exception:
        pass
    try:
        p = e.get_Parameter(DB.BuiltInParameter.CURVE_ELEM_LENGTH)
        if p is not None and p.HasValue:
            return p.AsDouble() * FT_TO_M
    except Exception:
        pass
    return 0.0


def _level_name(e):
    """楼层（标高）名。钢筋取所在主体的标高；没有标高的记为「(无标高)」。"""
    try:
        if e.Category is not None and _eid(e.Category.Id) == int(DB.BuiltInCategory.OST_Rebar):
            host = doc.GetElement(e.GetHostId())
            if host is not None:
                e = host
        lid = e.LevelId
        if _eid(lid) > 0:
            lvl = doc.GetElement(lid)
            if lvl is not None:
                return _u(lvl.Name)
    except Exception:
        pass
    return u'(无标高)'


def _type_name(e):
    try:
        t = doc.GetElement(e.GetTypeId())
        if t is not None:
            return _u(t.Name)
    except Exception:
        pass
    return _u(e.Name)


def _volume_m3(e):
    """构件体积（m³）。取不到返回 0。"""
    p = e.get_Parameter(DB.BuiltInParameter.HOST_VOLUME_COMPUTED)
    if p is None or not p.HasValue:
        p = e.LookupParameter(u'Volume')
    if p is not None and p.HasValue and p.AsDouble() > 0:
        return p.AsDouble() * FT3_TO_M3
    try:                                        # 体积参数取不到（嵌套族等）：累加几何实体
        tot = 0.0
        for g in e.get_Geometry(DB.Options()):
            if isinstance(g, DB.Solid):
                tot += g.Volume
            elif isinstance(g, DB.GeometryInstance):
                for x in g.GetInstanceGeometry():
                    if isinstance(x, DB.Solid):
                        tot += x.Volume
        return tot * FT3_TO_M3
    except Exception:
        return 0.0


def _density(e):
    """材料的密度（kg/m³）；取不到返回 None。"""
    try:
        mid = e.StructuralMaterialId
        if _eid(mid) < 0:
            return None
        mat = doc.GetElement(mid)
        if mat is None:
            return None
        pse = doc.GetElement(mat.StructuralAssetId)
        if pse is None:
            return None
        # Revit 返回的密度单位是 kg/ft³（混凝土约 68），这里统一换算成 kg/m³（约 2400）
        return float(pse.GetStructuralAsset().Density) / FT3_TO_M3
    except Exception:
        return None


def _material_name(e):
    try:
        mid = e.StructuralMaterialId
        if _eid(mid) >= 0:
            mat = doc.GetElement(mid)
            if mat is not None:
                return _u(mat.Name)
    except Exception:
        pass
    return u''


def _pick_elements(sel_ids):
    """要统计的构件：有选中就用选中的；没有就问：现在框选 / 整个项目 / 当前视图。取消返回 (None, None)。"""
    if sel_ids:
        elems = [e for e in (doc.GetElement(i) for i in sel_ids) if _is_qto(e)]
        return elems, u'框选的构件'
    options = [u'现在框选', u'整个项目', u'当前视图']
    pick = forms.CommandSwitchWindow.show(options, message=u'没有选中构件，统计哪个范围？')
    if not pick:
        return None, None
    if pick == u'现在框选':
        try:
            got = list(uidoc.Selection.PickElementsByRectangle(_QtoFilter(), u'框选要统计的构件（梁柱墙板基础、钢筋、井、排管、导管、松木桩、垫层），框完点 Finish'))
        except Exception:
            return None, None
        return [e for e in got if _is_qto(e)], u'框选的构件'
    if pick == u'当前视图':
        col = DB.FilteredElementCollector(doc, uidoc.ActiveView.Id)
        src = u'当前视图'
    else:
        col = DB.FilteredElementCollector(doc)
        src = u'整个项目'
    flt = DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](QTO_BICS))
    return [e for e in col.WherePasses(flt).WhereElementIsNotElementType().ToElements() if _is_qto(e)], src


def _quantities(elems):
    """汇总：混凝土（楼层、类别、类型）、钢材（同上，含重量）、钢筋（楼层、直径）。
    构件多时显示进度条（读几何体积较慢），可以取消；取消返回 None（只读，不改模型）。"""
    res = {'conc': {}, 'steel': {}, 'rebar': {}, 'wells': [], 'banks': {}, 'piles': {}, 'skipped': 0}
    pb = forms.ProgressBar(title=u'正在统计工程量…（点取消可停止）', cancellable=True) if len(elems) > 300 else None
    try:
        for i, e in enumerate(elems):
            if pb is not None and i % 20 == 0:
                pb.update_progress(i, len(elems))
                if pb.cancelled:
                    return None
            try:
                _add_one(e, res)
            except Exception:
                res['skipped'] += 1
    finally:
        if pb is not None:
            try:
                pb.close()
            except Exception:
                pass
    return res


def _add_one(e, res):
    """把一个构件的量累加进汇总表。"""
    if _eid(e.Category.Id) == int(DB.BuiltInCategory.OST_Rebar):
        lvl = _level_name(e)
        t = doc.GetElement(e.GetTypeId())
        d_ft = t.BarNominalDiameter if t is not None else 0.0
        dia = round(d_ft * FT_TO_MM, 1)
        bars = int(e.NumberOfBarPositions) if int(e.NumberOfBarPositions) > 0 else 1
        length_m = e.TotalLength * FT_TO_M
        r_m = d_ft * FT_TO_M / 2.0
        kg = length_m * math.pi * r_m * r_m * STEEL_DENSITY
        row = res['rebar'].setdefault((lvl, dia), [0, 0.0, 0.0])
        row[0] += bars
        row[1] += length_m
        row[2] += kg
        return

    c = _eid(e.Category.Id)
    if c == int(DB.BuiltInCategory.OST_GenericModel):          # 井
        mk = _mark(e)
        sr = wcfg_series_of(mk, WCFG, True)
        f, t = _ftname(e)
        res['wells'].append((mk, sr[u'label'] if sr else u'其他', f, t, _depth_mm(e), e.Id.Value))
        return
    if c == int(DB.BuiltInCategory.OST_Conduit) or (c == int(DB.BuiltInCategory.OST_StructuralFraming) and _is_bank(e)):
        f, t = _ftname(e)
        cat = u'导管' if c == int(DB.BuiltInCategory.OST_Conduit) else (u'雨水沟' if f.startswith(u'雨水沟') else u'排管')
        a = res['banks'].setdefault((cat, f, t), [0, 0.0, 0])
        a[0] += 1
        a[1] += _length_m(e)
        m = _HOLES.search(f + u' ' + t)
        if m:
            a[2] = int(m.group(1))
        return
    if c == int(DB.BuiltInCategory.OST_StructuralFoundation):
        kind = _pile_or_pad(e)
        if kind:
            f, t = _ftname(e)
            a = res['piles'].setdefault((kind, (f + u' / ' + t) if (f or t) else _u(e.Name)), [0, 0.0])
            a[0] += 1
            a[1] += _volume_m3(e) if kind == u'垫层' else 0.0
            return
    lvl = _level_name(e)
    cat = _u(e.Category.Name)
    typ = _type_name(e)
    vol = _volume_m3(e)
    dens = _density(e)
    mname = _material_name(e)
    is_steel = (dens is not None and dens >= STEEL_MIN_DENSITY) or (
        dens is None and (u'钢' in mname or u'steel' in mname.lower()))
    if is_steel:
        d = dens if (dens is not None and dens >= STEEL_MIN_DENSITY) else STEEL_DENSITY
        row = res['steel'].setdefault((lvl, cat, typ), [0, 0.0, 0.0])
        row[0] += 1
        row[1] += vol
        row[2] += vol * d  # kg，导出时再换算成 t
    else:
        row = res['conc'].setdefault((lvl, cat, typ), [0, 0.0])
        row[0] += 1
        row[1] += vol


# ============================================================
# 表格数据（窗口和 Excel 共用）
# ============================================================
def _table_rows(res):
    """返回三张表：[(标题, 表头, 行, 合计行)]。行和合计行的数字都已经是最终显示的单位。"""
    conc_rows = []
    conc_sum = 0.0
    for (lvl, cat, typ), (n, vol) in sorted(res['conc'].items()):
        conc_rows.append([lvl, cat, typ, n, round(vol, 3)])
        conc_sum += vol
    steel_rows = []
    steel_sum = 0.0
    for (lvl, cat, typ), (n, vol, kg) in sorted(res['steel'].items()):
        steel_rows.append([lvl, cat, typ, n, round(kg / 1000.0, 3)])
        steel_sum += kg / 1000.0
    rebar_rows = []
    rebar_sum = [0, 0.0, 0.0]
    for (lvl, dia), (bars, length_m, kg) in sorted(res['rebar'].items()):
        rebar_rows.append([lvl, dia, bars, round(length_m, 3), round(kg, 3)])
        rebar_sum[0] += bars
        rebar_sum[1] += length_m
        rebar_sum[2] += kg
    tables = [
        (u'混凝土', [u'楼层', u'构件类别', u'类型', u'数量', u'体积(m³)'], conc_rows,
         [u'合计', u'', u'', sum(r[3] for r in conc_rows), round(conc_sum, 3)]),
        (u'钢材', [u'楼层', u'构件类别', u'类型', u'数量', u'重量(t)'], steel_rows,
         [u'合计', u'', u'', sum(r[3] for r in steel_rows), round(steel_sum, 3)]),
        (u'钢筋', [u'楼层', u'直径(mm)', u'根数', u'总长(m)', u'重量(kg)'], rebar_rows,
         [u'合计', u'', rebar_sum[0], round(rebar_sum[1], 3), round(rebar_sum[2], 3)]),
    ]
    # ---- 小市政：井、排管导管、桩与垫层（没有的不出标签页）
    by, detail = {}, []
    for mk, lab, f, t, d, eid in sorted(res['wells'], key=lambda w: wcfg_well_sort_key(w[0], WCFG)):
        step = (math.ceil((d - 1e-6) / DEPTH_STEP) * DEPTH_STEP) if d else None
        by[(lab, f, t, step)] = by.get((lab, f, t, step), 0) + 1
        detail.append([mk, lab, f, t, round(d) if d else u'', int(step) if step else u'', eid])
    if by:
        wrows = [[k[0], k[1], k[2], int(k[3]) if k[3] else u'未取到深度', by[k]] for k in sorted(by, key=lambda k: (k[0], k[1], k[2], k[3] or 0))]
        tables.append((u'井汇总', [u'系列', u'族', u'类型', u'深度档(mm)', u'数量'], wrows,
                       [u'合计', u'', u'', u'', len(res['wells'])]))
        tables.append((u'井明细', [u'井号', u'系列', u'族', u'类型', u'井内深度(mm)', u'深度档(mm)', u'构件ID'], detail, None))
    if res['banks']:
        brows, tl, th = [], 0.0, 0.0
        for k in sorted(res['banks']):
            n, L, h = res['banks'][k]
            brows.append([k[0], k[1], k[2], n, round(L, 2), h if h else u'', round(L * h, 1) if h else u''])
            tl += L
            th += L * h
        tables.append((u'排管导管', [u'类别', u'族', u'类型', u'根数', u'总长度(m)', u'孔数', u'孔·米'], brows,
                       [u'合计', u'', u'', sum(a[0] for a in res['banks'].values()), round(tl, 2), u'', round(th, 1)]))
    if res['piles']:
        prows = [[k[0], k[1], v[0], round(v[1], 3) if k[0] == u'垫层' else u''] for k, v in sorted(res['piles'].items())]
        tables.append((u'桩与垫层', [u'项目', u'族 / 类型', u'数量', u'体积(m³)'], prows, None))
    summary = {'conc': conc_sum, 'steel': steel_sum, 'rebar': rebar_sum,
               'wells': len(res['wells']), 'banks': sum(a[0] for a in res['banks'].values()),
               'piles': sum(v[0] for k, v in res['piles'].items() if k[0] == u'松木桩'),
               'pads': sum(v[0] for k, v in res['piles'].items() if k[0] == u'垫层')}
    return tables, summary


# ============================================================
# Excel（真 xlsx，标准库直接写，不需要装 Excel）
# ============================================================
def _write_xlsx(tables, path):
    """把所有标签页写成一个 xlsx，每页一个工作表。"""
    sheets = []
    for name, header, rows, total in tables:
        data = [list(header)] + [list(r) for r in rows] + ([list(total)] if total else [])
        widths = []
        for i in range(len(header)):
            w = max(len(_u(r[i])) if i < len(r) else 0 for r in data)
            widths.append(min(max(w * 1.6 + 2, 8), 50))
        sheets.append((name, data, {'widths': widths, 'header_rows': 1}))
    xlsx_write(path, sheets)
    return path


def _ask_save_path():
    """弹出「另存为」对话框让用户选路径。取消返回 None。"""
    dlg = WF.SaveFileDialog()
    dlg.Title = u'导出提量表格'
    dlg.Filter = u'Excel 表格 (*.xlsx)|*.xlsx'
    dlg.DefaultExt = u'xlsx'
    dlg.AddExtension = True
    dlg.FileName = u'提量_%s.xlsx' % time.strftime('%Y%m%d_%H%M')
    dlg.InitialDirectory = System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments)
    if dlg.ShowDialog() == WF.DialogResult.OK:
        return dlg.FileName
    return None


# ============================================================
# 明细表（Revit 里的明细表，随模型自动更新）
# ============================================================
def _existing_schedule_names():
    names = set()
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule).ToElements():
        try:
            names.add(_u(s.Name))
        except Exception:
            pass
    return names


def _has_elements(bic):
    try:
        return DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType().GetElementCount() > 0
    except Exception:
        return False


def _find_field(defn, names):
    """按候选名的先后顺序找字段（前面的优先）。"""
    by_name = {}
    for sf in defn.GetSchedulableFields():
        try:
            by_name.setdefault(_u(sf.GetName(doc)), sf)
        except Exception:
            continue
    for n in names:
        if n in by_name:
            return by_name[n]
    return None


def _make_schedules():
    """按 SCHEDULE_SPECS 建明细表（已经有同名的跳过）。返回 (新建的表名, 跳过的表名, 缺的字段说明)。"""
    existing = _existing_schedule_names()
    created = []
    skipped = []
    missing = []
    t = DB.Transaction(doc, u'生成提量明细表')
    t.Start()
    try:
        for name, bic, field_names in SCHEDULE_SPECS:
            if name in existing:
                skipped.append(name)
                continue
            if not _has_elements(bic):
                continue
            sch = DB.ViewSchedule.CreateSchedule(doc, DB.ElementId(bic))
            sch.Name = name
            defn = sch.Definition
            for names in field_names:
                sf = _find_field(defn, names)
                if sf is None:
                    missing.append(u'%s：找不到字段 %s' % (name, u'/'.join(names)))
                    continue
                defn.AddField(sf)
            created.append(name)
        t.Commit()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise ex
    return created, skipped, missing


# ============================================================
# 表格窗口
# ============================================================
def _grid(headers, rows, total):
    g = WF.DataGridView()
    g.Dock = WF.DockStyle.Fill
    g.ReadOnly = True
    g.AllowUserToAddRows = False
    g.RowHeadersVisible = False
    g.AutoSizeColumnsMode = WF.DataGridViewAutoSizeColumnsMode.AllCells
    for i, h in enumerate(headers):
        g.Columns.Add(u'c%d' % i, _u(h))
    for r in rows + ([total] if total else []):
        idx = g.Rows.Add()
        for i, v in enumerate(r):
            g.Rows[idx].Cells[i].Value = v if not isinstance(v, str) else _u(v)
    if total and g.Rows.Count:
        g.Rows[g.Rows.Count - 1].DefaultCellStyle.Font = SD.Font(g.Font, SD.FontStyle.Bold)
    return g


def _show_window(tables, summary, src_txt, res):
    form = WF.Form()
    form.Text = u'%s — %s' % (TITLE, src_txt)
    form.Width = 920
    form.Height = 580
    form.StartPosition = WF.FormStartPosition.CenterScreen

    tabs = WF.TabControl()
    tabs.Dock = WF.DockStyle.Fill
    for title, headers, rows, total in tables:
        page = WF.TabPage(title)
        page.Controls.Add(_grid(headers, rows, total))
        tabs.TabPages.Add(page)

    bottom = WF.Panel()
    bottom.Dock = WF.DockStyle.Bottom
    bottom.Height = 48

    def _button(text, left, handler):
        b = WF.Button()
        b.Text = text
        b.Left = left
        b.Top = 9
        b.Width = 150
        b.Height = 30
        b.Click += System.EventHandler(handler)
        bottom.Controls.Add(b)
        return b

    msg = []

    def _on_export(sender, args):
        try:
            path = _ask_save_path()
            if not path:
                return
            _write_xlsx(tables, path)
            forms.alert(u'已导出：\n' + path, title=TITLE)
        except Exception as ex:
            forms.alert(u'导出失败：\n%s' % ex, title=TITLE)

    def _on_schedule(sender, args):
        try:
            created, skipped, missing = _make_schedules()
            lines = []
            if created:
                lines.append(u'已新建明细表：\n  ' + u'\n  '.join(created))
            if skipped:
                lines.append(u'已存在（自动更新，不重复建）：\n  ' + u'\n  '.join(skipped))
            if not created and not skipped:
                lines.append(u'没有可建的明细表（对应类别里没有构件）。')
            if missing:
                lines.append(u'有字段没找到，已跳过：\n  ' + u'\n  '.join(missing))
            forms.alert(u'\n'.join(lines), title=TITLE)
        except Exception as ex:
            forms.alert(u'生成明细表失败，已回滚：\n%s' % ex, title=TITLE)

    _button(u'导出 Excel', 12, _on_export)
    _button(u'生成/更新明细表', 172, _on_schedule)
    _button(u'关闭', 332, lambda s, a: form.Close())

    form.Controls.Add(tabs)
    form.Controls.Add(bottom)
    ff_fit(form).ShowDialog()


def main():
    try:
        sel_ids = list(uidoc.Selection.GetElementIds())
        elems, src_txt = _pick_elements(sel_ids)
        if elems is None:
            return
        if not elems:
            forms.alert(u"没有找到可统计的构件（梁柱墙板基础、钢筋、井、排管、导管、松木桩、垫层）。", title=TITLE)
            return
        res = _quantities(elems)
        if res is None:
            return          # 用户取消了统计（只读，没有改模型）
        tables, summary = _table_rows(res)
        if res['skipped']:
            forms.alert(u'有 %d 个构件读取失败，已跳过。' % res['skipped'], title=TITLE)
        _show_window(tables, summary, u'%s（%d 个构件）' % (src_txt, len(elems)), res)
    except Exception as e:
        forms.alert(u'提量过程发生异常：\n%s\n\n%s' % (e, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
