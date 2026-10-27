# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "一键单构件\n快速配筋"
__author__ = "KAIFA"
__doc__ = ("框选一根结构柱或一个混凝土基础，在图形窗口里选布置方式（按面布置或按周长均分），\n"
           "调主筋根数和直径、箍筋直径和间距，右边实时画出截面和立面，确认后生成整套钢筋笼。\n"
           "主筋竖向布置，箍筋封闭排布（135° 弯钩）。目前支持结构柱和混凝土基础；梁以后再加。钢构件不能布钢筋。")

import math
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

TITLE = u"一键单构件快速配筋"
MM_TO_FT = 1.0 / 304.8
CAT_COLUMN = int(DB.BuiltInCategory.OST_StructuralColumns)
CAT_FOUNDATION = int(DB.BuiltInCategory.OST_StructuralFoundation)
MODE_FACE = u'按面布置（每边根数，角筋共用）'
MODE_PERIM = u'按周长均分（主筋总根数）'
FACE_CHOICES = [3, 4, 5]          # 每边根数
PERIM_CHOICES = [4, 8, 12, 16]    # 主筋总根数
STIRRUP_CHOICES = [100, 150, 200]
MIN_CLEAR_MM = 25.0
MAX_STIRRUP_MM = 400.0


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

def _eid(x):
    try:
        return int(x.Value)
    except Exception:
        try:
            return int(x.IntegerValue)
        except Exception:
            return -1


# ============================================================
# 几何（英尺）：纯计算，不碰 Revit，可离线测试
# ============================================================
def perimeter_point(mx, my, s):
    """矩形（半宽 mx、半高 my）周长上距起点 s 的点。起点为 (mx, my)，逆时针走。"""
    segs = [((mx, my), (-mx, my), 2.0 * mx),
            ((-mx, my), (-mx, -my), 2.0 * my),
            ((-mx, -my), (mx, -my), 2.0 * mx),
            ((mx, -my), (mx, my), 2.0 * my)]
    for a, b, L in segs:
        if s <= L + 1e-12:
            u = (s / L) if L > 1e-12 else 0.0
            return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u)
        s -= L
    return (mx, my)


def main_bar_points(mx, my, n):
    """按周长均分：n 根主筋在矩形周长上均匀排布（从角点开始），返回相对中心的 (x, y) 列表。"""
    L = 4.0 * (mx + my)
    if n <= 0 or L <= 0:
        return []
    return [perimeter_point(mx, my, i * L / float(n)) for i in range(n)]


def face_bar_points(mx, my, k):
    """按面布置：每一面（每边）k 根，四个角的钢筋两边共用，总数 4×(k−1)。返回相对中心的 (x, y) 列表。"""
    if k < 2:
        return []
    corners = [(mx, my), (-mx, my), (-mx, -my), (mx, -my)]
    pts = []
    for j in range(4):
        a = corners[j]
        b = corners[(j + 1) % 4]
        for i in range(k - 1):
            u = i / float(k - 1)
            pts.append((a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u))
    return pts


def stirrup_heights(zs, ze, spacing):
    """箍筋高程：从 zs 开始按 spacing 排，最后一道正好在 ze 上。"""
    if ze < zs - 1e-9:
        return []
    if spacing <= 1e-9:
        return [zs, ze] if ze > zs + 1e-9 else [zs]
    n = int(math.floor((ze - zs) / spacing + 1e-9)) + 1
    hs = [zs + i * spacing for i in range(n)]
    if hs[-1] < ze - 1e-6:
        hs.append(ze)
    return hs


def cage_layout(W, D, zb, zt, cover, ds, dm, n_main, spacing, per_face=None):
    """纯几何（英尺）。W、D 为平面宽深，zb、zt 为底、顶高程，cover 保护层，ds 箍筋直径，dm 主筋直径。
    per_face 有值时按面布置（每边 per_face 根），否则主筋 n_main 根沿周长均分。
    返回 (箍筋半宽 sx, 箍筋半深 sy, 主筋列表 [(x, y, z0, z1)] 相对中心, 箍筋高程列表)。"""
    sx = W / 2.0 - cover - ds / 2.0          # 箍筋中心线的半宽
    sy = D / 2.0 - cover - ds / 2.0
    mx = sx - ds / 2.0 - dm / 2.0            # 主筋中心的半宽（在箍筋内侧）
    my = sy - ds / 2.0 - dm / 2.0
    bars = []
    z0 = zb + cover
    z1 = zt - cover
    pts = face_bar_points(mx, my, per_face) if per_face else main_bar_points(mx, my, n_main)
    for x, y in pts:
        bars.append((x, y, z0, z1))
    heights = stirrup_heights(zb + cover + ds / 2.0, zt - cover - ds / 2.0, spacing)
    return sx, sy, bars, heights


def clear_spacing_mm(W, D, cover, ds, dm, n_main, per_face=None):
    """主筋之间的净距（mm）。按面：每一边上相邻两根的中心距减直径，取两个方向较小的；
    周长均分：周长平均分给每根主筋的中心距，减直径。"""
    sx = W / 2.0 - cover - ds / 2.0
    sy = D / 2.0 - cover - ds / 2.0
    mx = sx - ds / 2.0 - dm / 2.0
    my = sy - ds / 2.0 - dm / 2.0
    if per_face:
        if per_face < 2:
            return 0.0
        gap_x = 2.0 * mx / (per_face - 1) - dm
        gap_y = 2.0 * my / (per_face - 1) - dm
        return min(gap_x, gap_y) * 304.8
    if n_main <= 0:
        return 0.0
    per = 4.0 * (mx + my) / n_main
    return (per - dm) * 304.8


# ============================================================
# Revit：钢筋类型、弯钩、保护层、生成
# ============================================================
def _bar_types():
    return list(DB.FilteredElementCollector(doc).OfClass(DB.Structure.RebarBarType).ToElements())


def available_diameters():
    """项目里有的钢筋直径（mm，升序去重）。"""
    ds = set()
    for t in _bar_types():
        ds.add(round(t.BarNominalDiameter * 304.8, 1))
    return sorted(ds)


def _pick_bar_type(d_mm):
    """按直径找钢筋类型：直径相同的里优先 HRB400；没有就取直径最接近的。返回 (类型, 实际直径 mm)。"""
    target = d_mm * MM_TO_FT
    types = _bar_types()
    if not types:
        return None, 0.0
    same = [t for t in types if abs(t.BarNominalDiameter - target) < 1e-5]
    if same:
        pref = [t for t in same if u'400' in t.Name]
        t = (pref or same)[0]
    else:
        t = min(types, key=lambda x: abs(x.BarNominalDiameter - target))
    return t, t.BarNominalDiameter / MM_TO_FT


def _pick_hook_135():
    hooks = list(DB.FilteredElementCollector(doc).OfClass(DB.Structure.RebarHookType).ToElements())
    for h in hooks:
        n = h.Name
        if u'135' in n and (u'箍' in n or u'系' in n):
            return h
    for h in hooks:
        if u'135' in h.Name:
            return h
    return None


def _cover_ft():
    covers = list(DB.FilteredElementCollector(doc).OfClass(DB.Structure.RebarCoverType).ToElements())
    if not covers:
        return None
    return covers[0].CoverDistance


def _local_box(host):
    """构件自身坐标系下的外形范围。柱子旋转过时，世界坐标的外包框比柱子大，钢筋会跑到混凝土外面，
    所以把实体的顶点换到族的坐标系里再求范围。返回 (变换, 最小点, 最大点)，最小/最大点是局部坐标。"""
    tf = DB.Transform.Identity
    try:
        tf = host.GetTransform()
    except Exception:
        pass
    inv = tf.Inverse
    opt = DB.Options()
    opt.DetailLevel = DB.ViewDetailLevel.Fine
    pts = []

    def solids(geo):
        for g in geo:
            if isinstance(g, DB.Solid) and g.Volume > 1e-9:
                yield g
            elif isinstance(g, DB.GeometryInstance):
                for x in solids(g.GetInstanceGeometry()):
                    yield x
    try:
        for so in solids(host.get_Geometry(opt)):
            for ed in so.Edges:
                for q in ed.Tessellate():
                    pts.append(inv.OfPoint(q))
    except Exception:
        pts = []
    if not pts:
        bb = host.get_BoundingBox(None)
        return DB.Transform.Identity, bb.Min, bb.Max
    lo = DB.XYZ(min(q.X for q in pts), min(q.Y for q in pts), min(q.Z for q in pts))
    hi = DB.XYZ(max(q.X for q in pts), max(q.Y for q in pts), max(q.Z for q in pts))
    return tf, lo, hi


def build_cage(host, cover, ds, dm, n_main, spacing_mm, bar_type, stir_type, hook, per_face=None):
    """在 host 里生成主筋和箍筋（调用方负责事务）。返回 (主筋根数, 箍筋道数)。
    按构件自身坐标系布置，旋转过的柱子钢筋也在混凝土里。"""
    tf, lo, hi = _local_box(host)
    cx = (lo.X + hi.X) / 2.0
    cy = (lo.Y + hi.Y) / 2.0
    W = hi.X - lo.X
    D = hi.Y - lo.Y
    sx, sy, bars, heights = cage_layout(W, D, lo.Z, hi.Z, cover, ds, dm, n_main,
                                        spacing_mm * MM_TO_FT, per_face)
    P = lambda x, y, z: tf.OfPoint(DB.XYZ(x, y, z))
    up = tf.OfVector(DB.XYZ(0, 0, 1))
    n_bars = 0
    for x, y, z0, z1 in bars:
        line = DB.Line.CreateBound(P(cx + x, cy + y, z0), P(cx + x, cy + y, z1))
        DB.Structure.Rebar.CreateFromCurves(doc, DB.Structure.RebarStyle.Standard, bar_type,
                                            None, None, host, tf.OfVector(DB.XYZ(1, 0, 0)),
                                            List[DB.Curve]([line]), DB.Structure.RebarHookOrientation.Left,
                                            DB.Structure.RebarHookOrientation.Left, True, False)
        n_bars += 1
    n_stir = 0
    for z in heights:
        pts = [P(cx - sx, cy - sy, z), P(cx + sx, cy - sy, z),
               P(cx + sx, cy + sy, z), P(cx - sx, cy + sy, z)]
        curves = List[DB.Curve]([DB.Line.CreateBound(pts[i], pts[(i + 1) % 4]) for i in range(4)])
        DB.Structure.Rebar.CreateFromCurves(doc, DB.Structure.RebarStyle.StirrupTie, stir_type,
                                            hook, hook, host, up, curves,
                                            DB.Structure.RebarHookOrientation.Left,
                                            DB.Structure.RebarHookOrientation.Left, True, True)
        n_stir += 1
    return n_bars, n_stir


# ============================================================
# 参数（纯函数，可离线测试）
# ============================================================
def parse_params(mode, count_txt, dm_txt, ds_txt, s_txt):
    """读取输入。mode 为 MODE_FACE 或 MODE_PERIM。返回 (参数字典, 提示列表)；不能生成时参数字典为 None。
    参数字典：n（主筋总根数）、per_face（按面时每边根数，否则 None）、dm、ds（mm）、s（箍筋间距 mm）。"""
    issues = []
    try:
        cnt = int(float(count_txt))
    except Exception:
        return None, [u'根数不是数字']
    try:
        dm = float(dm_txt)
        ds = float(ds_txt)
        s = float(s_txt)
    except Exception:
        return None, [u'直径或间距不是数字']
    if mode == MODE_FACE:
        if cnt < 2:
            return None, [u'每边根数至少 2 根（每边两端各一根角筋）']
        per_face = cnt
        n = 4 * (cnt - 1)
    else:
        if cnt < 4:
            return None, [u'主筋总根数至少 4 根（四个角各一根）']
        per_face = None
        n = cnt
    if s <= 0:
        return None, [u'箍筋间距必须大于 0']
    if s > MAX_STIRRUP_MM:
        issues.append(u'箍筋间距超过 %d mm，规范上一般不允许' % int(MAX_STIRRUP_MM))
    return {'n': n, 'per_face': per_face, 'dm': dm, 'ds': ds, 's': s}, issues


# ============================================================
# 图形：截面（平面）+ 立面
# ============================================================
def draw_preview(g, w_px, h_px, geo, p):
    """在 g 上画截面（左）和立面（右），按面板大小缩放。geo：W、D、H、cover（英尺）；p：参数字典。"""
    from System.Drawing import Pen, SolidBrush, Color, Font, PointF
    ink = SolidBrush(Color.FromArgb(50, 50, 50))
    grey = Pen(Color.FromArgb(120, 120, 120), 1.5)
    blue = Pen(Color.FromArgb(47, 140, 255), 2.0)
    bar_brush = SolidBrush(Color.FromArgb(200, 70, 40))
    small = Font(u"Microsoft YaHei UI", 9.0)
    bold = Font(u"Microsoft YaHei UI", 10.0, SD.FontStyle.Bold)

    W, D, H, cover = geo['W'], geo['D'], geo['H'], geo['cover']
    ds_ft = p['ds'] * MM_TO_FT
    dm_ft = p['dm'] * MM_TO_FT
    sx, sy, bars, heights = cage_layout(W, D, 0.0, H, cover, ds_ft, dm_ft, p['n'], p['s'] * MM_TO_FT, p['per_face'])

    margin = 18.0
    top = 40.0                       # 标题行高度
    bottom_txt = 48.0                # 底部说明行高度
    avail_h = max(120.0, h_px - top - bottom_txt - margin)
    avail_w = max(160.0, w_px - 2 * margin)
    gap = 34.0                       # 截面和立面之间的间距
    # 截面占约 55%，立面占其余
    plan_w_max = (avail_w - gap) * 0.55
    k = min(plan_w_max / max(W, D), avail_h / max(W, D))
    pw = W * k
    ph = D * k
    ox = margin
    oy = top
    g.DrawString(u"截面（平面）", bold, ink, PointF(ox, 10))
    g.DrawRectangle(grey, ox, oy, pw, ph)
    cx = ox + pw / 2.0
    cy = oy + ph / 2.0
    g.DrawRectangle(blue, cx - sx * k, cy - sy * k, 2 * sx * k, 2 * sy * k)
    r_bar = max(4.0, dm_ft * k / 2.0)
    for x, y, z0, z1 in bars:
        g.FillEllipse(bar_brush, cx + x * k - r_bar, cy - y * k - r_bar, 2 * r_bar, 2 * r_bar)
    g.DrawString(u"%.0f × %.0f mm" % (W * 304.8, D * 304.8), small, ink, PointF(ox, oy + ph + 6))
    g.DrawString(u"保护层 %.0f mm" % (cover * 304.8), small, ink, PointF(ox, oy + ph + 24))

    # 立面
    ex0 = ox + pw + gap
    ew_max = w_px - margin - ex0
    ke = avail_h / max(H, 1e-9)
    ew = min(max(W * ke, 40.0), max(ew_max, 40.0))
    eh = H * ke
    ey0 = oy
    g.DrawString(u"立面", bold, ink, PointF(ex0, 10))
    g.DrawRectangle(grey, ex0, ey0, ew, eh)
    for z in heights:
        yy = ey0 + eh - z * ke
        g.DrawLine(blue, ex0, yy, ex0 + ew, yy)
    g.DrawLine(grey, ex0, ey0 + cover * ke, ex0 + ew, ey0 + cover * ke)
    g.DrawLine(grey, ex0, ey0 + eh - cover * ke, ex0 + ew, ey0 + eh - cover * ke)
    g.DrawString(u"箍筋 Φ%.0f @%.0f（%d 道）" % (p['ds'], p['s'], len(heights)), small, ink,
                 PointF(margin, h_px - bottom_txt + 4))
    g.DrawString(u"主筋 %d 根 Φ%.0f（%s）" % (p['n'], p['dm'],
                                       (u'每边 %d 根' % p['per_face']) if p['per_face'] else u'周长均分'),
                 small, ink, PointF(margin, h_px - bottom_txt + 22))


# ============================================================
# 图形窗口：可拉伸；左边输入、右边预览；按钮固定在底部
# ============================================================
def build_rebar_form(geo, result):
    """建窗口（不弹出）。确定时把参数写入 result['p']。返回 form。"""
    from System.Windows.Forms import (Form, Label, TextBox, Button, Panel, ComboBox, ComboBoxStyle,
                                      DialogResult, FormBorderStyle, FormStartPosition, AnchorStyles,
                                      DockStyle)
    from System.Drawing import Size, Point, Font, Color, SolidBrush, PointF

    FONT = Font(u"Microsoft YaHei UI", 9.0)
    form = Form()
    form.Text = TITLE
    form.Font = FONT
    form.StartPosition = FormStartPosition.CenterScreen
    form.FormBorderStyle = FormBorderStyle.Sizable
    form.MinimumSize = Size(980, 620)
    form.ClientSize = Size(1120, 680)

    left = Panel()
    left.Dock = DockStyle.Left
    left.Width = 330
    left.Padding = System.Windows.Forms.Padding(12)
    form.Controls.Add(left)

    right = Panel()
    right.Dock = DockStyle.Fill
    right.BackColor = Color.White
    form.Controls.Add(right)

    def add_label(parent, text, x, y, w=None):
        lb = Label()
        lb.Text = text
        lb.Location = Point(x, y + 3)
        lb.AutoSize = True
        if w:
            lb.MaximumSize = Size(w, 0)
        parent.Controls.Add(lb)
        return lb

    diams = [int(d) if abs(d - int(d)) < 1e-6 else d for d in available_diameters()] or [20]

    y = 14
    add_label(left, u"布置方式", 12, y)
    cb_mode = ComboBox()
    cb_mode.DropDownStyle = ComboBoxStyle.DropDownList
    cb_mode.Location = Point(12, y + 26)
    cb_mode.Width = 300
    cb_mode.Items.Add(MODE_FACE)
    cb_mode.Items.Add(MODE_PERIM)
    cb_mode.SelectedIndex = 0
    left.Controls.Add(cb_mode)
    y += 64

    lb_count = add_label(left, u"每边根数", 12, y)
    tb_count = TextBox()
    tb_count.Location = Point(120, y)
    tb_count.Width = 60
    tb_count.Text = u'4'
    left.Controls.Add(tb_count)
    quick_count = []
    x = 12
    y += 34
    for _ in range(4):
        b = Button()
        b.Location = Point(x, y)
        b.Size = Size(68, 26)
        left.Controls.Add(b)
        quick_count.append(b)
        x += 74
    y += 42

    add_label(left, u"主筋直径 (mm)", 12, y)
    cb_dm = ComboBox()
    cb_dm.DropDownStyle = ComboBoxStyle.DropDownList
    cb_dm.Location = Point(150, y)
    cb_dm.Width = 80
    for d in diams:
        cb_dm.Items.Add(u'%s' % d)
    cb_dm.SelectedIndex = diams.index(20) if 20 in diams else min(range(len(diams)), key=lambda i: abs(diams[i] - 20))
    left.Controls.Add(cb_dm)
    y += 36

    add_label(left, u"箍筋直径 (mm)", 12, y)
    cb_ds = ComboBox()
    cb_ds.DropDownStyle = ComboBoxStyle.DropDownList
    cb_ds.Location = Point(150, y)
    cb_ds.Width = 80
    for d in diams:
        cb_ds.Items.Add(u'%s' % d)
    cb_ds.SelectedIndex = min(range(len(diams)), key=lambda i: abs(diams[i] - 8))
    left.Controls.Add(cb_ds)
    y += 36

    add_label(left, u"箍筋间距 (mm)", 12, y)
    tb_s = TextBox()
    tb_s.Location = Point(150, y)
    tb_s.Width = 80
    tb_s.Text = u'150'
    left.Controls.Add(tb_s)
    x = 12
    y += 34
    for v in STIRRUP_CHOICES:
        b = Button()
        b.Text = u'%d' % v
        b.Tag = v
        b.Location = Point(x, y)
        b.Size = Size(68, 26)
        left.Controls.Add(b)
        x += 74
        b.Click += System.EventHandler(lambda s, a: _set_text(tb_s, s))
    y += 40

    add_label(left, u"保护层：%.0f mm（项目设置）" % (geo['cover'] * 304.8), 12, y)
    y += 30

    note = Label()
    note.Location = Point(12, y)
    note.Size = Size(300, 200)
    note.ForeColor = Color.FromArgb(40, 120, 60)
    left.Controls.Add(note)

    btn_ok = Button()
    btn_ok.Text = u'生成钢筋笼'
    btn_ok.Size = Size(140, 36)
    btn_ok.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
    btn_ok.Location = Point(12, form.ClientSize.Height - 60)
    form.Controls.Add(btn_ok)
    btn_cancel = Button()
    btn_cancel.Text = u'取消'
    btn_cancel.Size = Size(100, 36)
    btn_cancel.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
    btn_cancel.Location = Point(170, form.ClientSize.Height - 60)
    btn_cancel.DialogResult = DialogResult.Cancel
    form.Controls.Add(btn_cancel)
    form.AcceptButton = btn_ok
    form.CancelButton = btn_cancel

    def read_p():
        return parse_params(cb_mode.SelectedItem, tb_count.Text, cb_dm.SelectedItem, cb_ds.SelectedItem, tb_s.Text)

    def set_quick_for_mode(sender=None, args=None):
        face = (cb_mode.SelectedItem == MODE_FACE)
        lb_count.Text = u'每边根数' if face else u'主筋根数'
        choices = FACE_CHOICES if face else PERIM_CHOICES
        for i, b in enumerate(quick_count):
            v = choices[i] if i < len(choices) else choices[-1]
            b.Text = u'%d' % v
            b.Tag = v
        tb_count.Text = u'%d' % (choices[1] if face else choices[1])
        recompute()

    def on_quick_count(sender, args):
        tb_count.Text = u'%s' % sender.Tag
        recompute()

    for b in quick_count:
        b.Click += System.EventHandler(on_quick_count)

    def recompute(sender=None, args=None):
        p, issues = read_p()
        lines = []
        if p is None:
            note.ForeColor = Color.Firebrick
            lines.append(u'⚠ 不能生成：')
            lines += [u'· ' + it for it in issues]
            note.Text = u'\n'.join(lines)
            right.Invalidate()
            return
        clear = clear_spacing_mm(geo['W'], geo['D'], geo['cover'], p['ds'] * MM_TO_FT, p['dm'] * MM_TO_FT,
                                 p['n'], p['per_face'])
        stir_n = len(stirrup_heights(geo['cover'] + p['ds'] * MM_TO_FT / 2.0,
                                     geo['H'] - geo['cover'] - p['ds'] * MM_TO_FT / 2.0,
                                     p['s'] * MM_TO_FT))
        lines.append(u'主筋 %d 根 Φ%.0f，箍筋 %d 道 Φ%.0f @%.0f' % (p['n'], p['dm'], stir_n, p['ds'], p['s']))
        if p['per_face']:
            lines.append(u'按面：每边 %d 根（角筋共用）' % p['per_face'])
        lines.append(u'主筋净距约 %.0f mm' % clear)
        warn = list(issues)
        if clear < MIN_CLEAR_MM:
            warn.append(u'主筋净距 %.0f mm 小于 %d mm，主筋太密，建议减少根数或加大截面' % (clear, int(MIN_CLEAR_MM)))
        if warn:
            note.ForeColor = Color.FromArgb(176, 96, 0)
            lines.append(u'')
            lines.append(u'⚠ 注意：')
            lines += [u'· ' + w for w in warn]
        else:
            note.ForeColor = Color.FromArgb(40, 120, 60)
            lines.append(u'')
            lines.append(u'✓ 参数合理，可以生成')
        note.Text = u'\n'.join(lines)
        right.Invalidate()

    def on_paint(sender, e):
        try:
            p, _ = read_p()
            if p is None:
                e.Graphics.DrawString(u'参数不完整，无法预览', Font(u"Microsoft YaHei UI", 10.0),
                                      SolidBrush(Color.Firebrick), PointF(20, 20))
                return
            draw_preview(e.Graphics, right.Width, right.Height, geo, p)
        except Exception as ex:
            e.Graphics.DrawString(u'预览出错：%s' % ex, Font(u"Microsoft YaHei UI", 9.0),
                                  SolidBrush(Color.Firebrick), PointF(20, 20))

    right.Paint += on_paint
    right.Resize += System.EventHandler(lambda s, a: right.Invalidate())
    cb_mode.SelectedIndexChanged += set_quick_for_mode
    tb_count.TextChanged += recompute
    tb_s.TextChanged += recompute
    cb_dm.SelectedIndexChanged += recompute
    cb_ds.SelectedIndexChanged += recompute
    def on_ok(sender, args):
        p, issues = read_p()
        if p is None:
            WF.MessageBox.Show(u'\n'.join(issues), TITLE)
            return
        result['p'] = p
        form.DialogResult = DialogResult.OK

    btn_ok.Click += System.EventHandler(on_ok)
    set_quick_for_mode()
    return form


def _set_text(tb, btn):
    tb.Text = u'%s' % btn.Tag


def rebar_dialog(geo):
    """图形参数窗口。返回参数字典（确定）或 None（取消）。"""
    result = {}
    form = build_rebar_form(geo, result)
    if ff_fit(form).ShowDialog() == WF.DialogResult.OK and 'p' in result:
        return result['p']
    return None


def _geo_of(host, cover_ft):
    _tf, lo, hi = _local_box(host)
    return {'W': hi.X - lo.X, 'D': hi.Y - lo.Y, 'H': hi.Z - lo.Z, 'cover': cover_ft}


def main():
    try:
        sel = list(uidoc.Selection.GetElementIds())
        if len(sel) != 1:
            forms.alert(u"请先框选一根结构柱或一个混凝土基础（只选一个）。", title=TITLE)
            return
        host = doc.GetElement(sel[0])
        cat = _eid(host.Category.Id) if host is not None and host.Category is not None else -1
        if cat not in (CAT_COLUMN, CAT_FOUNDATION):
            forms.alert(u"目前只支持结构柱和混凝土基础（当前选中的是「%s」）。" %
                        (host.Category.Name if host is not None and host.Category is not None else u'未知'), title=TITLE)
            return
        cover = _cover_ft()
        if cover is None:
            forms.alert(u"项目里没有钢筋保护层类型，请先在 Revit 里设置保护层。", title=TITLE)
            return
        geo = _geo_of(host, cover)
        p = rebar_dialog(geo)
        if p is None:
            return
        bar_type, bar_actual = _pick_bar_type(p['dm'])
        stir_type, stir_actual = _pick_bar_type(p['ds'])
        hook = _pick_hook_135()
        if bar_type is None or stir_type is None:
            forms.alert(u"项目里没有钢筋类型。", title=TITLE)
            return
        if hook is None:
            forms.alert(u"项目里没有 135° 箍筋弯钩类型，无法生成箍筋。", title=TITLE)
            return
        t = DB.Transaction(doc, u'一键单构件快速配筋')
        t.Start()
        try:
            n_bars, n_stir = build_cage(host, cover, p['ds'] * MM_TO_FT, p['dm'] * MM_TO_FT,
                                        p['n'], p['s'], bar_type, stir_type, hook, p['per_face'])
            t.Commit()
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            forms.alert(u"生成钢筋失败，已回滚：\n%s\n\n%s" % (ex, traceback.format_exc()), title=TITLE)
            return
        lines = [u'已生成钢筋笼：主筋 %d 根（Φ%s），箍筋 %d 道（Φ%s，间距 %d mm）。' %
                 (n_bars, round(bar_actual, 1), n_stir, round(stir_actual, 1), int(p['s'])),
                 u'保护层：%.0f mm。' % (cover / MM_TO_FT)]
        if abs(bar_actual - p['dm']) > 0.5 or abs(stir_actual - p['ds']) > 0.5:
            lines.append(u'注意：项目里没有完全相同直径的钢筋，已用最接近的类型。')
        forms.alert(u'\n'.join(lines), title=TITLE)
    except Exception as e:
        forms.alert(u'配筋过程发生异常：\n%s\n\n%s' % (e, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
