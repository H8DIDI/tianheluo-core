# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "一键单构件\n快速配筋"
__author__ = "KAIFA"
__doc__ = ("框选一根结构柱或一个混凝土基础，弹窗填主筋根数、主筋直径、箍筋直径和间距，\n"
           "直接生成整套钢筋笼：主筋沿构件四周布置，箍筋按间距封闭排布（135° 弯钩）。\n"
           "目前支持结构柱和混凝土基础（独立基础、承台）；梁以后再加。钢构件不能布钢筋。")

import math
import traceback
import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit import DB
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms

doc = revit.doc
uidoc = revit.uidoc

TITLE = u"一键单构件快速配筋"
MM_TO_FT = 1.0 / 304.8
CAT_COLUMN = int(DB.BuiltInCategory.OST_StructuralColumns)
CAT_FOUNDATION = int(DB.BuiltInCategory.OST_StructuralFoundation)


def _eid(x):
    try:
        return int(x.Value)
    except Exception:
        try:
            return int(x.IntegerValue)
        except Exception:
            return -1


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
    """n 根主筋在矩形周长上均匀排布（从角点开始），返回相对中心的 (x, y) 列表。"""
    L = 4.0 * (mx + my)
    if n <= 0 or L <= 0:
        return []
    return [perimeter_point(mx, my, i * L / float(n)) for i in range(n)]


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


def cage_layout(W, D, zb, zt, cover, ds, dm, n_main, spacing):
    """纯几何（英尺）。W、D 为构件平面宽深，zb、zt 为底、顶高程，cover 保护层，ds 箍筋直径，dm 主筋直径。
    返回 (箍筋半宽 sx, 箍筋半深 sy, 主筋列表 [(x, y, z0, z1)] 相对中心, 箍筋高程列表)。"""
    sx = W / 2.0 - cover - ds / 2.0          # 箍筋中心线的半宽
    sy = D / 2.0 - cover - ds / 2.0
    mx = sx - ds / 2.0 - dm / 2.0            # 主筋中心的半宽（在箍筋内侧）
    my = sy - ds / 2.0 - dm / 2.0
    bars = []
    z0 = zb + cover
    z1 = zt - cover
    for x, y in main_bar_points(mx, my, n_main):
        bars.append((x, y, z0, z1))
    heights = stirrup_heights(zb + cover + ds / 2.0, zt - cover - ds / 2.0, spacing)
    return sx, sy, bars, heights


def _pick_bar_type(d_mm):
    """按直径找钢筋类型：直径相同的里优先 HRB400；没有就取直径最接近的。返回 (类型, 实际直径 mm)。"""
    target = d_mm * MM_TO_FT
    types = list(DB.FilteredElementCollector(doc).OfClass(DB.Structure.RebarBarType).ToElements())
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


def _ask_number(prompt, default):
    s = forms.ask_for_string(default=str(default), prompt=prompt, title=TITLE)
    if s is None:
        return None
    try:
        return float(s)
    except Exception:
        raise ValueError(u'「%s」不是数字' % s)


def build_cage(host, cover, ds, dm, n_main, spacing_mm, bar_type, stir_type, hook):
    """在 host 里生成主筋和箍筋（一个事务里调用）。返回 (主筋根数, 箍筋道数)。"""
    bb = host.get_BoundingBox(None)
    cx = (bb.Min.X + bb.Max.X) / 2.0
    cy = (bb.Min.Y + bb.Max.Y) / 2.0
    W = bb.Max.X - bb.Min.X
    D = bb.Max.Y - bb.Min.Y
    sx, sy, bars, heights = cage_layout(W, D, bb.Min.Z, bb.Max.Z, cover, ds, dm, n_main, spacing_mm * MM_TO_FT)
    up = DB.XYZ(0, 0, 1)
    n_bars = 0
    for x, y, z0, z1 in bars:
        p0 = DB.XYZ(cx + x, cy + y, z0)
        p1 = DB.XYZ(cx + x, cy + y, z1)
        line = DB.Line.CreateBound(p0, p1)
        DB.Structure.Rebar.CreateFromCurves(doc, DB.Structure.RebarStyle.Standard, bar_type,
                                            None, None, host, DB.XYZ(1, 0, 0),
                                            List[DB.Curve]([line]), DB.Structure.RebarHookOrientation.Left,
                                            DB.Structure.RebarHookOrientation.Left, True, False)
        n_bars += 1
    n_stir = 0
    for z in heights:
        pts = [DB.XYZ(cx - sx, cy - sy, z), DB.XYZ(cx + sx, cy - sy, z),
               DB.XYZ(cx + sx, cy + sy, z), DB.XYZ(cx - sx, cy + sy, z)]
        curves = List[DB.Curve]([DB.Line.CreateBound(pts[i], pts[(i + 1) % 4]) for i in range(4)])
        DB.Structure.Rebar.CreateFromCurves(doc, DB.Structure.RebarStyle.StirrupTie, stir_type,
                                            hook, hook, host, up, curves,
                                            DB.Structure.RebarHookOrientation.Left,
                                            DB.Structure.RebarHookOrientation.Left, True, True)
        n_stir += 1
    return n_bars, n_stir


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
        n_main = _ask_number(u'主筋根数（如 8、12、16）', 8)
        if n_main is None:
            return
        dm_mm = _ask_number(u'主筋直径 (mm)', 20)
        if dm_mm is None:
            return
        ds_mm = _ask_number(u'箍筋直径 (mm)', 8)
        if ds_mm is None:
            return
        spacing_mm = _ask_number(u'箍筋间距 (mm)', 150)
        if spacing_mm is None:
            return
        n_main = int(n_main)
        if n_main < 4:
            forms.alert(u"主筋至少 4 根（四个角各一根）。", title=TITLE)
            return
        bar_type, bar_actual = _pick_bar_type(dm_mm)
        stir_type, stir_actual = _pick_bar_type(ds_mm)
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
            n_bars, n_stir = build_cage(host, cover, ds_mm * MM_TO_FT, dm_mm * MM_TO_FT,
                                        n_main, spacing_mm, bar_type, stir_type, hook)
            t.Commit()
        except Exception as ex:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            forms.alert(u"生成钢筋失败，已回滚：\n%s\n\n%s" % (ex, traceback.format_exc()), title=TITLE)
            return
        lines = [u'已生成钢筋笼：主筋 %d 根（Φ%s），箍筋 %d 道（Φ%s，间距 %d mm）。' %
                 (n_bars, round(bar_actual, 1), n_stir, round(stir_actual, 1), int(spacing_mm)),
                 u'保护层：%.0f mm。' % (cover / MM_TO_FT)]
        if abs(bar_actual - dm_mm) > 0.5 or abs(stir_actual - ds_mm) > 0.5:
            lines.append(u'注意：项目里没有完全相同直径的钢筋，已用最接近的类型。')
        forms.alert(u'\n'.join(lines), title=TITLE)
    except ValueError as ex:
        forms.alert(u'输入有误：%s' % ex, title=TITLE)
    except Exception as e:
        forms.alert(u'配筋过程发生异常：\n%s\n\n%s' % (e, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
