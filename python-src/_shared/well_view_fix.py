# -*- coding: utf-8 -*-
# 井大样视图「原位修复」：按井现在的位置重设已有视图的裁剪框/剖切位置，视图、图纸、视口都保留。
# 被 @include 进 well-sheets（一键井展开图），也可以单独 exec 做测试。依赖全局的 DB、System、FT_MM。
import Autodesk.Revit.DB as DB
import System

_WVF_TOL = 0.02          # 英尺，约 6 mm：井超出裁剪框不到这个数不算被裁
_WVF_DIRS = (u"平面", u"南", u"北", u"西", u"东")


def wvf_mark(elem):
    p = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
    return p.AsString().strip() if (p and p.AsString()) else u""


def wvf_geom(elem):
    """井的平面中心和半尺寸（英尺）：与出图时同一算法（实体真实顶点；薄壳族只取竖直大墙面），
    取不到实体时退回包围盒。返回 dict(cx,cy,cz,dx,dy,dz)。"""
    bb = elem.get_BoundingBox(None)
    cz = (bb.Min.Z + bb.Max.Z) / 2.0
    dz = (bb.Max.Z - bb.Min.Z) / 2.0
    loc = elem.Location
    if isinstance(loc, DB.LocationPoint) and loc.Point:
        cx, cy = loc.Point.X, loc.Point.Y
    else:
        cx, cy = (bb.Min.X + bb.Max.X) / 2.0, (bb.Min.Y + bb.Max.Y) / 2.0
    dx = (bb.Max.X - bb.Min.X) / 2.0
    dy = (bb.Max.Y - bb.Min.Y) / 2.0
    try:
        opt = DB.Options()
        opt.ComputeReferences = False
        opt.DetailLevel = DB.ViewDetailLevel.Fine
        geom = elem.get_Geometry(opt)
        if geom:
            x0, x1, y0, y1 = float('inf'), float('-inf'), float('inf'), float('-inf')
            found = False
            for obj in geom:
                if isinstance(obj, DB.GeometryInstance):
                    ig = obj.GetInstanceGeometry()
                    if ig:
                        for s in ig:
                            if isinstance(s, DB.Solid) and s.Faces.Size > 0:
                                real = s.Volume > 0.001
                                for f in s.Faces:
                                    if not real:
                                        try:
                                            if f.Area < 10.0 or abs(f.ComputeNormal(DB.UV(0.5, 0.5)).Z) > 0.2:
                                                continue
                                        except:
                                            continue
                                    found = True
                                    mesh = f.Triangulate()
                                    if mesh:
                                        for pt in mesh.Vertices:
                                            x0 = min(x0, pt.X); x1 = max(x1, pt.X)
                                            y0 = min(y0, pt.Y); y1 = max(y1, pt.Y)
            if found and x1 > x0 and y1 > y0:
                dx, dy = (x1 - x0) / 2.0, (y1 - y0) / 2.0
                cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    except:
        pass
    return {'cx': cx, 'cy': cy, 'cz': cz, 'dx': dx, 'dy': dy, 'dz': dz}


def wvf_view_dir(view):
    """视图名前缀（平面/南/北/西/东），不是井大样视图返回 None。"""
    nm = view.Name or u""
    for d in _WVF_DIRS:
        if nm.startswith(d + u"_"):
            return d
    return None


_WVF_FACE_OUT = 100 / 304.8


def wvf_cut_offset(view, g, face_out=_WVF_FACE_OUT):
    """立面视图剖切面沿视线方向的偏差（英尺）：正数 = 剖切面比应在的位置更靠井内（落进墙里），负数 = 离井太远。
    取不到（轴向异常）返回 None。"""
    T = view.CropBox.Transform
    bx, by, bz = T.BasisX, T.BasisY, T.BasisZ
    if abs(by.Z - 1.0) > 1e-6:
        return None
    hx = abs(bx.X) * g['dx'] + abs(bx.Y) * g['dy']
    hz = abs(bz.X) * g['dx'] + abs(bz.Y) * g['dy']
    ox = g['cx'] + bz.X * (hz + face_out) - bx.X * (hx + face_out)
    oy = g['cy'] + bz.Y * (hz + face_out) - bx.Y * (hx + face_out)
    return (ox - T.Origin.X) * bz.X + (oy - T.Origin.Y) * bz.Y


def wvf_audit_view(doc, view, elem, g):
    """查一个视图有没有把井显示全。返回问题代码列表：
    裁剪框问题 'clip(横向可见比例,纵向可见比例)'、'depth'（剖切深度没碰到井）、'hidden'（井被隐藏）、'cropoff'、'range'（平面视图范围没盖住井）、
    'cutin'（立面剖切面落进井墙里，整面墙被填成实心）、'cutfar'（剖切面离井太远）。"""
    miss = []
    bb = elem.get_BoundingBox(None)
    cb = view.CropBox
    inv = cb.Transform.Inverse
    zs = (g['cz'] - g['dz'], g['cz'] + g['dz'])
    corners = [DB.XYZ(g['cx'] + sx * g['dx'], g['cy'] + sy * g['dy'], z)
               for sx in (-1, 1) for sy in (-1, 1) for z in zs]
    loc = [inv.OfPoint(c) for c in corners]
    lx = [p.X for p in loc]; ly = [p.Y for p in loc]; lz = [p.Z for p in loc]
    if not view.CropBoxActive:
        miss.append('cropoff')
    if (min(lx) < cb.Min.X - _WVF_TOL or max(lx) > cb.Max.X + _WVF_TOL or
            min(ly) < cb.Min.Y - _WVF_TOL or max(ly) > cb.Max.Y + _WVF_TOL):
        ow = max(0.0, min(max(lx), cb.Max.X) - max(min(lx), cb.Min.X)) / max(1e-6, max(lx) - min(lx))
        oh = max(0.0, min(max(ly), cb.Max.Y) - max(min(ly), cb.Min.Y)) / max(1e-6, max(ly) - min(ly))
        miss.append('clip(%.2f,%.2f)' % (ow, oh))
    if max(lz) < cb.Min.Z or min(lz) > cb.Max.Z:
        miss.append('depth')
    try:
        if elem.IsHidden(view):
            miss.append('hidden')
    except:
        pass
    if wvf_view_dir(view) != u"平面":
        # 剖切面位置：应在井外墙外 face_out 处。剖切面落进墙厚里，整面墙会被当成剖切面填成实心（什么细节都看不到）；
        # 离墙太远则剖面深度吃不到井。
        d = wvf_cut_offset(view, g)
        if d is not None:
            if d > _WVF_FACE_OUT * 0.9:
                miss.append('cutin(%dmm)' % round(d * 304.8))
            elif d < -1.0:
                miss.append('cutfar(%dmm)' % round(-d * 304.8))
    if wvf_view_dir(view) == u"平面":
        try:
            vr = view.GetViewRange()
            el = doc.GetElement(view.GenLevel.Id).ProjectElevation
            if (el + vr.GetOffset(DB.PlanViewPlane.TopClipPlane) < bb.Max.Z - 0.01 or
                    el + vr.GetOffset(DB.PlanViewPlane.BottomClipPlane) > bb.Min.Z + 0.01):
                miss.append('range')
        except:
            pass
    return miss


def wvf_crop_sheet_center(vp, view):
    """视图裁剪框中心在图纸上的位置 (x, y)。"""
    cb = view.CropBox
    T = vp.GetProjectionToSheetTransform()
    sc = float(view.Scale)
    return (T.Origin.X + (cb.Min.X + cb.Max.X) / 2.0 / sc,
            T.Origin.Y + (cb.Min.Y + cb.Max.Y) / 2.0 / sc)


def wvf_fix_view(doc, view, elem, g, crop_ft, face_out, face_depth, fix_range=False):
    """把一个井大样视图原位对准井：只改裁剪框（平面）/ 剖切位置与框（立面），井被隐藏则取消隐藏。
    返回说明字符串；视图不是平面/立面（或朝向异常）时返回 None（没动）。"""
    d = wvf_view_dir(view)
    if d is None:
        return None
    if d == u"平面":
        crop = DB.BoundingBoxXYZ()
        crop.Transform = view.CropBox.Transform
        crop.Min = DB.XYZ(g['cx'] - g['dx'] - crop_ft, g['cy'] - g['dy'] - crop_ft, g['cz'] - g['dz'] - crop_ft)
        crop.Max = DB.XYZ(g['cx'] + g['dx'] + crop_ft, g['cy'] + g['dy'] + crop_ft, g['cz'] + g['dz'] + crop_ft)
        view.CropBoxActive = True
        view.CropBox = crop
        if fix_range:
            try:
                el = doc.GetElement(view.GenLevel.Id).ProjectElevation
                vr = view.GetViewRange()
                vr.SetOffset(DB.PlanViewPlane.TopClipPlane, (g['cz'] + g['dz'] + 3.0) - el)
                vr.SetOffset(DB.PlanViewPlane.CutPlane, (g['cz'] + g['dz'] + 0.6) - el)
                vr.SetOffset(DB.PlanViewPlane.BottomClipPlane, (g['cz'] - g['dz'] - 2.0) - el)
                vr.SetOffset(DB.PlanViewPlane.ViewDepthPlane, (g['cz'] - g['dz'] - 2.0) - el)
                view.SetViewRange(vr)
            except:
                pass
    else:
        T = view.CropBox.Transform
        bx, by, bz = T.BasisX, T.BasisY, T.BasisZ
        if abs(by.Z - 1.0) > 1e-6:
            return None
        hx = abs(bx.X) * g['dx'] + abs(bx.Y) * g['dy']          # 横向半宽
        hz = abs(bz.X) * g['dx'] + abs(bz.Y) * g['dy']          # 视线方向半深
        ox = g['cx'] + bz.X * (hz + face_out) - bx.X * (hx + face_out)
        oy = g['cy'] + bz.Y * (hz + face_out) - bx.Y * (hx + face_out)
        oz = g['cz'] - g['dz'] - face_out
        # 剖面框的位置改 CropBox.Transform 不生效，要移动视图的剖面标记（OST_Viewers）
        delta = DB.XYZ(ox - T.Origin.X, oy - T.Origin.Y, oz - T.Origin.Z)
        if delta.GetLength() > 1e-6:
            for _mk in view.GetDependentElements(DB.ElementCategoryFilter(DB.BuiltInCategory.OST_Viewers)):
                DB.ElementTransformUtils.MoveElement(doc, _mk, delta)
            doc.Regenerate()
        box = DB.BoundingBoxXYZ()
        box.Transform = view.CropBox.Transform
        box.Min = DB.XYZ(0.0, 0.0, -face_depth)
        box.Max = DB.XYZ(2.0 * (hx + face_out), 2.0 * (g['dz'] + face_out), 0.0)
        view.CropBoxActive = True
        view.CropBox = box
    try:
        view.CropBoxVisible = False
    except:
        pass
    try:
        if elem.IsHidden(view):
            view.UnhideElements(System.Collections.Generic.List[DB.ElementId]([elem.Id]))
    except:
        pass
    return u"ok"


def _wvf_world_range(view):
    cb = view.CropBox
    T = cb.Transform
    pts = [T.OfPoint(DB.XYZ(a, b, c)) for a in (cb.Min.X, cb.Max.X) for b in (cb.Min.Y, cb.Max.Y) for c in (cb.Min.Z, cb.Max.Z)]
    return (min(p.X for p in pts), max(p.X for p in pts), min(p.Y for p in pts), max(p.Y for p in pts),
            min(p.Z for p in pts), max(p.Z for p in pts))


def _wvf_neighbors(doc, elem, margin_ft=25.0):
    """井周围别的井（有 Mark、Mark 不同）[(id, 包围盒)]。"""
    bb = elem.get_BoundingBox(None)
    ol = DB.Outline(DB.XYZ(bb.Min.X - margin_ft, bb.Min.Y - margin_ft, bb.Min.Z - margin_ft),
                    DB.XYZ(bb.Max.X + margin_ft, bb.Max.Y + margin_ft, bb.Max.Z + margin_ft))
    own = wvf_mark(elem)
    out = []
    for e in (DB.FilteredElementCollector(doc).WherePasses(DB.BoundingBoxIntersectsFilter(ol))
              .OfCategory(DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType()):
        if e.Id == elem.Id or not wvf_mark(e) or wvf_mark(e) == own:
            continue
        b = e.get_BoundingBox(None)
        if b is not None:
            out.append((e.Id, b))
    return out


def wvf_fix_sheet(doc, sheet, elem, crop_ft, face_out, face_depth):
    """一张井大样图纸原位修复：只动没把井显示全的视图；修完隐藏新范围里的邻井，视口按原来的位置对齐。
    返回 (修了几个视图, 修完仍有问题的 [(视图名, 问题)])。调用方负责事务。"""
    g = wvf_geom(elem)
    vps = [vp for vp in DB.FilteredElementCollector(doc, sheet.Id).OfClass(DB.Viewport)]
    todo = []
    for vp in vps:
        v = doc.GetElement(vp.ViewId)
        if v is None or wvf_view_dir(v) is None:
            continue
        probs = wvf_audit_view(doc, v, elem, g)
        if probs:
            todo.append((vp, v, wvf_crop_sheet_center(vp, v), 'range' in probs))
    if not todo:
        return 0, []
    nbs = _wvf_neighbors(doc, elem)
    n = 0
    for vp, v, _c, fix_range in todo:
        if wvf_fix_view(doc, v, elem, g, crop_ft, face_out, face_depth, fix_range):
            n += 1
    doc.Regenerate()
    left = []
    for vp, v, (tx, ty), _fr in todo:
        r = _wvf_world_range(v)
        hide = [i for i, b in nbs if not (b.Max.X < r[0] or b.Min.X > r[1] or b.Max.Y < r[2] or b.Min.Y > r[3]
                                          or b.Max.Z < r[4] or b.Min.Z > r[5]) and not doc.GetElement(i).IsHidden(v)]
        if hide:
            try:
                v.HideElements(System.Collections.Generic.List[DB.ElementId](hide))
            except:
                pass
        cx, cy = wvf_crop_sheet_center(vp, v)
        if abs(cx - tx) > 0.003 or abs(cy - ty) > 0.003:
            bc = vp.GetBoxCenter()
            vp.SetBoxCenter(DB.XYZ(bc.X + (tx - cx), bc.Y + (ty - cy), 0))
        p2 = wvf_audit_view(doc, v, elem, g)
        if p2:
            left.append((v.Name, u",".join(p2)))
    return n, left
