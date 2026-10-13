# -*- coding: utf-8 -*-
__title__ = '一键排管标注'
__doc__ = ('选中井/图纸 → 勾选要标注的视图 → 给视图内每根排管（结构框架）生成：\n'
           '下方尺寸链（断面宽 + 到内壁净距）、左侧尺寸链（井内底→管底 / 断面高 / 管顶→井内顶）、\n'
           '以及排管断面到井内壁左右定位净距；并自动外扩裁剪框保证标注可见。\n'
           '注意：请先跑「一键井大样标注」再跑本工具（它只清排管相关尺寸，不会动井的尺寸）。')

from pyrevit import revit, DB, UI, forms
import System
import traceback

doc = revit.doc
uidoc = revit.uidoc

# ============================================================
# 可调参数（改这里即可；数值是当前默认值，改后重新运行生效）
# ============================================================

# 尺寸线与构件/井外包边的间距（英尺，1.0 约 305 mm）
_DIM_MARGIN = 1.0

# 裁剪框自动外扩量（英尺，1.8 约 550 mm）：保证下方/左侧尺寸线及文字在裁剪框内
_CROP_PAD = 1.8

# 严格轴向判据（法线分量阈值，0.995 约等于偏角 5.7°）；越接近 1 越严格
_AXIAL_TOL = 0.995

# 井内壁候选面最小面积（平方英尺，21.5 约 2 m2），过滤碎面/倒角面
_AREA_MIN_FT2 = 21.5

# 尺寸类型名：决定尺寸文字样式（名称里的 4mm、1比100 即文字高度与出图比例）；找不到则用默认类型
_DIM_TYPE_NAME = '井大样 - 4mm(1比100)'

# 净距文字位置相对井内壁的水平偏移（英尺，沿壁面法线方向退开的量），原值 0.33
_TXT_SIDE_FT = 0.33

# 净距文字位置相对尺寸线的向上偏移（英尺，文字离开尺寸线的高度），原值 0.66
_TXT_UP_FT = 0.66

# 跨度保险倍数：构件在视图平面上的尺寸超过整个视图框的这个倍数时，判为横穿长构件并剔除
_SPAN_RATIO = 2.0

# 同位置判重精度（英尺，0.01 约 3 mm）：同一视图内两道尺寸的端点在此精度内相同，视为同一位置，第二道跳过
_SPOT_TOL_FT = 0.01

# ============================================================
# 配置
# ============================================================

# 排管类别：本项目用结构框架族承载排管
_BANK_CAT = DB.BuiltInCategory.OST_StructuralFraming

# 不计入包围盒的类别：注释、标记、底图、参照
_EXCLUDE_CATS = [
    DB.BuiltInCategory.OST_Dimensions,
    DB.BuiltInCategory.OST_TextNotes,
    DB.BuiltInCategory.OST_Levels,
    DB.BuiltInCategory.OST_Grids,
    DB.BuiltInCategory.OST_Sections,
    DB.BuiltInCategory.OST_Viewers,
    DB.BuiltInCategory.OST_Cameras,
    DB.BuiltInCategory.OST_RoomTags,
    DB.BuiltInCategory.OST_SpotElevations,
    DB.BuiltInCategory.OST_SpotCoordinates,
    DB.BuiltInCategory.OST_SpotSlopes,
    DB.BuiltInCategory.OST_CLines,
    DB.BuiltInCategory.OST_Constraints,
    DB.BuiltInCategory.OST_Matchline,
    DB.BuiltInCategory.OST_RasterImages,
    DB.BuiltInCategory.OST_ScheduleGraphics,
    DB.BuiltInCategory.OST_ColorFillSchema,
    DB.BuiltInCategory.OST_AreaTags,
    DB.BuiltInCategory.OST_MassTags,
    DB.BuiltInCategory.OST_MaterialTags,
    DB.BuiltInCategory.OST_GenericAnnotation,
    DB.BuiltInCategory.OST_TitleBlocks,
]

# 管道类：井包围盒不含管道与排管（排管单独立项标注），也不含长结构件
_PIPE_CATS = [
    DB.BuiltInCategory.OST_PipeCurves,
    DB.BuiltInCategory.OST_PipeFitting,
    DB.BuiltInCategory.OST_PipeAccessory,
    DB.BuiltInCategory.OST_PipeInsulations,
    DB.BuiltInCategory.OST_FlexPipeCurves,
    DB.BuiltInCategory.OST_DuctCurves,
    DB.BuiltInCategory.OST_DuctFitting,
    DB.BuiltInCategory.OST_DuctAccessory,
    DB.BuiltInCategory.OST_FlexDuctCurves,
    DB.BuiltInCategory.OST_CableTray,
    DB.BuiltInCategory.OST_CableTrayFitting,
    DB.BuiltInCategory.OST_Conduit,
    DB.BuiltInCategory.OST_ConduitFitting,
    DB.BuiltInCategory.OST_StructuralFraming,
    DB.BuiltInCategory.OST_StructuralFramingSystem,
    DB.BuiltInCategory.OST_StructuralColumns,
    DB.BuiltInCategory.OST_StructuralFoundation,
]


def _ints(cats):
    out = []
    for c in cats:
        try:
            out.append(int(c))
        except:
            pass
    return out


_EXCLUDE_IDS = _ints(_EXCLUDE_CATS)
_PIPE_IDS = _ints(_PIPE_CATS)

# 统计用：报告按此顺序列出跳过原因；_ST_* 是统计键（不是跳过原因）
_ST_BANK_OK = '标注成功的排管'
_ST_DUP = '同位置重复尺寸（已跳过）'
_SKIP_BANK_DIR = '排管方向与视口水平不一致（未标注）'
_SKIP_BANK_NOAX = '排管没有严格轴向面（未标注）'
_SKIP_BANK_ERR = '排管处理异常（未标注）'
_SKIP_HIDDEN = '不可见'
_SKIP_NOBB = '没有包围盒'
_SKIP_ZERO = '尺寸为零'
_SKIP_SPAN = '超出视图跨度（长构件）'
_SKIP_CAT = '注释/管道/结构框架等（按口径不计入）'
_SKIP_ERR = '读取异常'
_REPORT_SKIPS = [
    (_SKIP_BANK_DIR, '根'),
    (_SKIP_BANK_NOAX, '根'),
    (_SKIP_BANK_ERR, '根'),
    (_SKIP_HIDDEN, '个'),
    (_SKIP_NOBB, '个'),
    (_SKIP_ZERO, '个'),
    (_SKIP_SPAN, '个'),
    (_SKIP_CAT, '个'),
    (_SKIP_ERR, '个'),
]


def _bump(stats, key, n=1):
    """计数（stats 为 None 时不统计）。"""
    if stats is not None and n:
        stats[key] = stats.get(key, 0) + n


def _fmt_skips(stats):
    """把非零的跳过计数整理成报告行。"""
    txt = ''
    for key, unit in _REPORT_SKIPS:
        n = stats.get(key, 0)
        if n:
            txt += '    - {}：{} {}\n'.format(key, n, unit)
    return txt


_DIM_TYPE_ID = [None]  # 惰性缓存


# ============================================================
# 面提取（只取严格轴向面，天然互相平行，可直接用于标注）
# ============================================================


def _face_area(f):
    try:
        if isinstance(f, DB.PlanarFace):
            return f.Area
    except:
        pass
    return None


def _extract_faces(elem):
    """提取构件所有面引用，带世界法线、原点与面积。"""
    opt = DB.Options()
    opt.ComputeReferences = True
    opt.IncludeNonVisibleObjects = False
    geom = elem.get_Geometry(opt)
    faces = []
    if not geom:
        return faces

    def _rep(f):
        try:
            if isinstance(f, DB.PlanarFace):
                return (f.FaceNormal, f.Origin)
        except:
            return None
        try:
            uvbox = f.GetBoundingBox()
            u = (uvbox.Min.U + uvbox.Max.U) / 2.0
            v = (uvbox.Min.V + uvbox.Max.V) / 2.0
            uv = DB.UV(u, v)
            return (f.ComputeNormal(uv), f.Evaluate(uv))
        except:
            return None

    def _walk(ge, tr):
        for obj in ge:
            if isinstance(obj, DB.GeometryInstance):
                _walk(obj.GetSymbolGeometry(), tr.Multiply(obj.Transform))
            elif hasattr(obj, 'Faces') and obj.Faces.Size > 0:
                for f in obj.Faces:
                    if f.Reference is None:
                        continue
                    rep = _rep(f)
                    if rep is None:
                        continue
                    faces.append({
                        'ref': f.Reference,
                        'norm': tr.OfVector(rep[0]),
                        'orig': tr.OfPoint(rep[1]),
                        'area': _face_area(f),
                    })

    _walk(geom, DB.Transform.Identity)
    return faces


def _axial_all(elem):
    """构件三个方向的严格轴向面，返回 {'X':[(坐标,面),...],...}，按轴排序去重。"""
    res = {'X': [], 'Y': [], 'Z': []}
    seen = {'X': [], 'Y': [], 'Z': []}
    for f in _extract_faces(elem):
        n = f['norm']
        try:
            for ax, v, c in (('X', abs(n.X), f['orig'].X),
                             ('Y', abs(n.Y), f['orig'].Y),
                             ('Z', abs(n.Z), f['orig'].Z)):
                if v < _AXIAL_TOL:
                    continue
                dup = False
                for s in seen[ax]:
                    if abs(s - c) < 0.005:
                        dup = True
                        break
                if dup:
                    continue
                seen[ax].append(c)
                res[ax].append((c, f))
        except:
            continue
    for ax in res:
        res[ax].sort(key=lambda t: t[0])
    return res


# ============================================================
# 视图内构件与包围盒
# ============================================================


def crop_world_range(view):
    """视图裁剪框的世界坐标范围 (x0,x1, y0,y1, z0,z1)。"""
    try:
        cb = view.CropBox
    except:
        return None
    if cb is None:
        return None
    try:
        xs = []
        ys = []
        zs = []
        for a in (cb.Min.X, cb.Max.X):
            for b in (cb.Min.Y, cb.Max.Y):
                for c in (cb.Min.Z, cb.Max.Z):
                    p = cb.Transform.OfPoint(DB.XYZ(a, b, c))
                    xs.append(p.X)
                    ys.append(p.Y)
                    zs.append(p.Z)
        return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
    except:
        return None


def collect_target_elems(view, stats=None):
    """收集视图内应计入包围盒的可见模型构件（排除注释、CAD 底图、管道类）。
    stats 若传入 dict，则按原因累计被跳过的构件数（见 _REPORT_SKIPS）。"""
    out = []
    try:
        col = DB.FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType().ToElements()
    except:
        return out

    rng = crop_world_range(view)
    if rng:
        crop_w = rng[1] - rng[0]
        crop_h = rng[5] - rng[4]
    else:
        crop_w = crop_h = 0.0

    for e in col:
        try:
            cat = e.Category
            if cat is None:
                _bump(stats, _SKIP_CAT)
                continue
            cid = cat.Id.Value
            if cid in _EXCLUDE_IDS or cid in _PIPE_IDS:
                _bump(stats, _SKIP_CAT)
                continue
            try:
                if e.IsHidden(view):
                    _bump(stats, _SKIP_HIDDEN)
                    continue
            except:
                pass
            bb = e.get_BoundingBox(view)
            if bb is None:
                _bump(stats, _SKIP_NOBB)
                continue
            w = abs(bb.Max.X - bb.Min.X)
            h = abs(bb.Max.Z - bb.Min.Z)
            if w <= 0 and h <= 0:
                _bump(stats, _SKIP_ZERO)
                continue
            if crop_w > 0 and crop_h > 0:
                if w > crop_w * _SPAN_RATIO or h > crop_h * _SPAN_RATIO:
                    _bump(stats, _SKIP_SPAN)
                    continue
            out.append(e)
        except:
            _bump(stats, _SKIP_ERR)
            continue
    return out


def union_box(view, elems):
    """构件在模型坐标下的联合包围盒，返回 (x0,x1, y0,y1, z0,z1)。"""
    x0 = x1 = y0 = y1 = z0 = z1 = None
    for e in elems:
        try:
            bb = e.get_BoundingBox(view)
        except:
            continue
        if bb is None:
            continue
        if x0 is None:
            x0, x1 = bb.Min.X, bb.Max.X
            y0, y1 = bb.Min.Y, bb.Max.Y
            z0, z1 = bb.Min.Z, bb.Max.Z
        else:
            x0 = min(x0, bb.Min.X)
            x1 = max(x1, bb.Max.X)
            y0 = min(y0, bb.Min.Y)
            y1 = max(y1, bb.Max.Y)
            z0 = min(z0, bb.Min.Z)
            z1 = max(z1, bb.Max.Z)
    if x0 is None:
        return None
    return (x0, x1, y0, y1, z0, z1)


def _overlap(bb, rng):
    try:
        tol = 0.05
        return (bb.Max.X > rng[0] - tol and bb.Min.X < rng[1] + tol and
                bb.Max.Y > rng[2] - tol and bb.Min.Y < rng[3] + tol and
                bb.Max.Z > rng[4] - tol and bb.Min.Z < rng[5] + tol)
    except:
        return False


def _find_banks(rng, h_axis, stats=None):
    """视图内与裁剪框相交的排管；横断面宽度方向与视口水平方向不一致的不标，计入 stats。"""
    out = []
    try:
        col = DB.FilteredElementCollector(doc).OfCategory(_BANK_CAT).WhereElementIsNotElementType().ToElements()
    except:
        return out
    for e in col:
        try:
            bb = e.get_BoundingBox(None)
            if bb is None:
                continue
            dx = abs(bb.Max.X - bb.Min.X)
            dy = abs(bb.Max.Y - bb.Min.Y)
            if dx <= 0 and dy <= 0:
                continue
            if not _overlap(bb, rng):
                continue
            width_axis = 'Y' if dx > dy else 'X'
            if width_axis != h_axis:
                _bump(stats, _SKIP_BANK_DIR)
                continue
            out.append(e)
        except:
            continue
    return out


# ============================================================
# 视图方向与裁剪框
# ============================================================


def _view_left(view):
    try:
        r = view.UpDirection.CrossProduct(view.ViewDirection)
        return DB.XYZ(-r.X, -r.Y, -r.Z)
    except:
        return DB.XYZ(-1, 0, 0)


def _grow_crop(view, wdir, amt):
    """把裁剪框沿世界方向 wdir 外扩 amt（英尺）。必须同时复制两角点，否则另一角回落默认值把框撑坏。"""
    try:
        cb = view.CropBox
        T = cb.Transform
        dx = wdir.DotProduct(T.BasisX)
        dy = wdir.DotProduct(T.BasisY)
        dz = wdir.DotProduct(T.BasisZ)
        nb = DB.BoundingBoxXYZ()
        nb.Transform = T
        nb.Min = DB.XYZ(cb.Min.X, cb.Min.Y, cb.Min.Z)
        nb.Max = DB.XYZ(cb.Max.X, cb.Max.Y, cb.Max.Z)
        if abs(dx) >= abs(dy) and abs(dx) >= abs(dz):
            if dx > 0:
                nb.Max = DB.XYZ(cb.Max.X + amt, cb.Max.Y, cb.Max.Z)
            else:
                nb.Min = DB.XYZ(cb.Min.X - amt, cb.Min.Y, cb.Min.Z)
        elif abs(dy) >= abs(dz):
            if dy > 0:
                nb.Max = DB.XYZ(cb.Max.X, cb.Max.Y + amt, cb.Max.Z)
            else:
                nb.Min = DB.XYZ(cb.Min.X, cb.Min.Y - amt, cb.Min.Z)
        else:
            if dz > 0:
                nb.Max = DB.XYZ(cb.Max.X, cb.Max.Y, cb.Max.Z + amt)
            else:
                nb.Min = DB.XYZ(cb.Min.X, cb.Min.Y, cb.Min.Z - amt)
        view.CropBox = nb
        return True
    except:
        return False


def _ensure_crop(view, box):
    """幂等外扩：只有当前裁剪框盖不住下方/左侧尺寸时才扩，重复运行不再加大。"""
    try:
        rng = crop_world_range(view)
        if rng is None:
            return
        x0, x1, y0, y1, z0, z1 = box
        pad = _CROP_PAD
        if rng[4] > z0 - pad + 0.001:
            _grow_crop(view, DB.XYZ(0, 0, -1), rng[4] - (z0 - pad))
        left = _view_left(view)
        if left.X > 0.5:
            need = x1 + pad
            if rng[1] < need - 0.001:
                _grow_crop(view, DB.XYZ(1, 0, 0), need - rng[1])
        elif left.X < -0.5:
            need = x0 - pad
            if rng[0] > need + 0.001:
                _grow_crop(view, DB.XYZ(-1, 0, 0), rng[0] - need)
        elif left.Y > 0.5:
            need = y1 + pad
            if rng[3] < need - 0.001:
                _grow_crop(view, DB.XYZ(0, 1, 0), need - rng[3])
        else:
            need = y0 - pad
            if rng[2] > need + 0.001:
                _grow_crop(view, DB.XYZ(0, -1, 0), rng[2] - need)
    except:
        pass


# ============================================================
# 尺寸生成
# ============================================================


def _find_dim_type_id():
    if _DIM_TYPE_ID[0] is not None:
        return _DIM_TYPE_ID[0]
    try:
        for t in DB.FilteredElementCollector(doc).OfClass(DB.DimensionType).ToElements():
            try:
                nm = (t.Name or '').strip()
                if nm == _DIM_TYPE_NAME or ('井大样' in nm and '4mm' in nm):
                    _DIM_TYPE_ID[0] = t.Id
                    return t.Id
            except:
                pass
    except:
        pass
    return None


class _SpotLedger(object):
    """记录同一视图内已生成尺寸的位置：同位置再次生成时跳过并计数，避免重复标注。"""

    def __init__(self):
        self.keys = set()
        self.dup = 0


def _spot_key(p1, p2):
    """两端点按 _SPOT_TOL_FT 取整后的键，与端点先后顺序无关。"""
    def _q(p):
        return (int(round(p.X / _SPOT_TOL_FT)), int(round(p.Y / _SPOT_TOL_FT)), int(round(p.Z / _SPOT_TOL_FT)))
    a = _q(p1)
    b = _q(p2)
    return (a, b) if a <= b else (b, a)


def _try_dim(view, ref_a, ref_b, world_p1, world_p2, ledger=None):
    """两个面引用之间建一条尺寸。传入 ledger 时，同位置已有尺寸则跳过并计入 ledger.dup。"""
    if view is None or ref_a is None or ref_b is None:
        return None
    key = None
    if ledger is not None:
        key = _spot_key(world_p1, world_p2)
        if key in ledger.keys:
            ledger.dup += 1
            return None
    try:
        arr = DB.ReferenceArray()
        arr.Append(ref_a)
        arr.Append(ref_b)
        if arr.Size < 2:
            return None
        line = DB.Line.CreateBound(world_p1, world_p2)
        d = view.Document.Create.NewDimension(view, line, arr)
        try:
            dt = _find_dim_type_id()
            if d is not None and dt is not None:
                d.ChangeTypeId(dt)
        except:
            pass
        if d is not None and key is not None:
            ledger.keys.add(key)
        return d
    except:
        return None


def _pt(h_axis, hv, d_axis, dv, zv):
    if h_axis == 'X':
        return DB.XYZ(hv, dv, zv)
    return DB.XYZ(dv, hv, zv)


def _purge_bank_dims(view):
    """删除该视图内引用了排管（结构框架）的尺寸；不碰井的尺寸。"""
    n = 0
    try:
        bank_id = int(_BANK_CAT)
    except:
        return n
    try:
        dims = DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Dimension).ToElements()
    except:
        return n
    for d in dims:
        try:
            hit = False
            try:
                refs = d.References
            except:
                refs = None
            if refs is not None:
                for r in refs:
                    try:
                        el = doc.GetElement(r.ElementId)
                        if el is not None and el.Category is not None and el.Category.Id.Value == bank_id:
                            hit = True
                            break
                    except:
                        pass
            if hit:
                doc.Delete(d.Id)
                n += 1
        except:
            pass
    return n


def annotate_view(view, stats=None):
    """给一个视图内的每根排管生成 7 道尺寸（下链：宽+净距；左链：底净距+高+顶净距；内部净距）。返回 (生成数, 跳过原因)。
    stats 若传入 dict，则累计标注成功的排管数、各类跳过原因与同位置重复数。"""
    made = 0
    try:
        if view.ViewType == DB.ViewType.FloorPlan:
            return 0, '平面视图暂不处理'
    except:
        pass

    rng = crop_world_range(view)
    if rng is None:
        return 0, '无裁剪框'

    left = _view_left(view)
    h_axis = 'X' if abs(left.X) > 0.5 else 'Y'
    d_axis = 'X' if h_axis == 'Y' else 'Y'
    lsign = 1.0 if ((left.X if h_axis == 'X' else left.Y) >= 0) else -1.0

    banks = _find_banks(rng, h_axis, stats)
    if not banks:
        return 0, '视图内无排管（结构框架）'

    elems = collect_target_elems(view, stats)
    if not elems:
        return 0, '无可用井构件'
    box = union_box(view, elems)
    if box is None:
        return 0, '无法计算井包围盒'
    x0, x1, y0, y1, z0, z1 = box
    d_mid = (x0 + x1) / 2.0 if d_axis == 'X' else (y0 + y1) / 2.0

    # 井内壁候选面（大面积严格轴向面）：水平轴一组用于左右定位，Z 一组用于上下链
    wall_faces = []
    wall_z_faces = []
    for e in elems:
        for f in _extract_faces(e):
            a = f.get('area')
            if a is None or a < _AREA_MIN_FT2:
                continue
            try:
                n = f['norm']
                nv = n.X if h_axis == 'X' else n.Y
                if abs(nv) >= _AXIAL_TOL:
                    c = f['orig'].X if h_axis == 'X' else f['orig'].Y
                    wall_faces.append((c, nv, f))
                if abs(n.Z) >= _AXIAL_TOL:
                    wall_z_faces.append((f['orig'].Z, n.Z, f))
            except:
                continue

    m = _DIM_MARGIN
    led = _SpotLedger()

    def _add_dim(ra, rb, p1, p2):
        return _try_dim(view, ra, rb, p1, p2, led)

    for bank in banks:
        m0 = made
        try:
            bf = _axial_all(bank)
            if not bf['Z'] or not bf[h_axis]:
                _bump(stats, _SKIP_BANK_NOAX)
                continue
            b_zlo, fzlo = bf['Z'][0]
            b_zhi, fzhi = bf['Z'][-1]
            b_lo, flo = bf[h_axis][0]
            b_hi, fhi = bf[h_axis][-1]

            # ---- 下方标宽（断面宽；尺寸链风格=端部不超出引用） ----
            d = _add_dim(flo['ref'], fhi['ref'],
                         _pt(h_axis, b_lo, d_axis, d_mid, z0 - m),
                         _pt(h_axis, b_hi, d_axis, d_mid, z0 - m))
            if d is not None:
                made += 1

            # ---- 左侧标高（线位对齐井外包边 + 间隙） ----
            if lsign > 0:
                hx = (x1 if h_axis == 'X' else y1) + m
            else:
                hx = (x0 if h_axis == 'X' else y0) - m
            d = _add_dim(fzlo['ref'], fzhi['ref'],
                         _pt(h_axis, hx, d_axis, d_mid, b_zlo),
                         _pt(h_axis, hx, d_axis, d_mid, b_zhi))
            if d is not None:
                made += 1

            # ---- 排管断面 ↔ 井内壁（左右定位净距） ----
            z_line = b_zhi + m
            bl = b_hi if lsign > 0 else b_lo
            br = b_lo if lsign > 0 else b_hi
            fl = fhi if lsign > 0 else flo
            fr = flo if lsign > 0 else fhi

            # 左：法线背向左方向、且位于排管左边缘之外的最近井面
            bpl = bl * lsign
            lci = None
            lref = None
            best = None
            for c, nv, f in wall_faces:
                try:
                    if nv * lsign > -_AXIAL_TOL:
                        continue
                    p = c * lsign
                    if p < bpl - 0.005:
                        continue
                    if best is None or p < best:
                        best = p
                        lci = c
                        lref = f
                except:
                    continue
            if lref is not None:
                s0 = min(bl, lci) - m
                s1 = max(bl, lci) + m
                d = _add_dim(fl['ref'], lref['ref'],
                             _pt(h_axis, s0, d_axis, d_mid, z_line),
                             _pt(h_axis, s1, d_axis, d_mid, z_line))
                if d is not None:
                    made += 1
                    try:
                        if abs(bl - lci) < 0.01:
                            nrm = lref['norm']
                            nh = nrm.X if h_axis == 'X' else nrm.Y
                            d.TextPosition = _pt(h_axis, lci - nh * _TXT_SIDE_FT, d_axis, d_mid, z_line + _TXT_UP_FT)
                    except:
                        pass

            # 右：镜像
            bpr = br * (-lsign)
            rci = None
            rref = None
            best = None
            for c, nv, f in wall_faces:
                try:
                    if nv * (-lsign) > -_AXIAL_TOL:
                        continue
                    p = c * (-lsign)
                    if p < bpr - 0.005:
                        continue
                    if best is None or p < best:
                        best = p
                        rci = c
                        rref = f
                except:
                    continue
            if rref is not None:
                s0 = min(br, rci) - m
                s1 = max(br, rci) + m
                d = _add_dim(fr['ref'], rref['ref'],
                             _pt(h_axis, s0, d_axis, d_mid, z_line),
                             _pt(h_axis, s1, d_axis, d_mid, z_line))
                if d is not None:
                    made += 1
                    try:
                        if abs(br - rci) < 0.01:
                            nrm = rref['norm']
                            nh = nrm.X if h_axis == 'X' else nrm.Y
                            d.TextPosition = _pt(h_axis, rci - nh * _TXT_SIDE_FT, d_axis, d_mid, z_line + _TXT_UP_FT)
                    except:
                        pass

            # ---- 底部链右段：排管右(左)边缘 → 对侧内壁（与下方标宽同线） ----
            if rref is not None:
                t0 = min(br, rci)
                t1 = max(br, rci)
                d = _add_dim(fr['ref'], rref['ref'],
                             _pt(h_axis, t0, d_axis, d_mid, z0 - m),
                             _pt(h_axis, t1, d_axis, d_mid, z0 - m))
                if d is not None:
                    made += 1

            # ---- 左侧链上段：排管顶 → 井内顶（向上最近的大面） ----
            zt = None
            ztref = None
            for cz, nvz, f in wall_z_faces:
                try:
                    if nvz > -_AXIAL_TOL:
                        continue
                    if cz < b_zhi - 0.005:
                        continue
                    if zt is None or cz < zt:
                        zt = cz
                        ztref = f
                except:
                    continue
            if ztref is not None and abs(zt - b_zhi) > 0.005:
                d = _add_dim(fzhi['ref'], ztref['ref'],
                             _pt(h_axis, hx, d_axis, d_mid, b_zhi),
                             _pt(h_axis, hx, d_axis, d_mid, zt))
                if d is not None:
                    made += 1

            # ---- 左侧链下段：井内底 → 排管底（向下最近的大面） ----
            zb = None
            zbref = None
            for cz, nvz, f in wall_z_faces:
                try:
                    if nvz < _AXIAL_TOL:
                        continue
                    if cz > b_zlo + 0.005:
                        continue
                    if zb is None or cz > zb:
                        zb = cz
                        zbref = f
                except:
                    continue
            if zbref is not None and abs(b_zlo - zb) > 0.005:
                d = _add_dim(fzlo['ref'], zbref['ref'],
                             _pt(h_axis, hx, d_axis, d_mid, zb),
                             _pt(h_axis, hx, d_axis, d_mid, b_zlo))
                if d is not None:
                    made += 1
        except:
            _bump(stats, _SKIP_BANK_ERR)
            continue
        if made > m0:
            _bump(stats, _ST_BANK_OK)

    _bump(stats, _ST_DUP, led.dup)
    if made:
        _ensure_crop(view, box)
    return made, None


# ============================================================
# 入口
# ============================================================


class _SilentFailures(DB.IFailuresPreprocessor):
    """抑制标注引发的失败消息；绝不自己删元素。"""

    def PreprocessFailures(self, failuresAccessor):
        for f in failuresAccessor.GetFailureMessages():
            try:
                sev = f.GetSeverity()
            except:
                continue
            if sev == DB.FailureSeverity.Warning:
                try:
                    failuresAccessor.DeleteWarning(f)
                except:
                    pass
                continue
            try:
                failuresAccessor.ResolveFailure(f)
            except:
                pass
        return DB.FailureProcessingResult.Continue


def _sheet_of(e):
    if e is None:
        return None
    if isinstance(e, DB.ViewSheet):
        return e
    try:
        if e.OwnerViewId and e.OwnerViewId != DB.ElementId.InvalidElementId:
            return doc.GetElement(e.OwnerViewId)
    except:
        pass
    return None


def _mark_of(e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        return p.AsString().strip() if (p and p.AsString()) else None
    except:
        return None


def _sheet_by_mark(mark):
    target = "井大样_" + mark
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
        try:
            if (s.Name or "") == target:
                return s
        except:
            pass
    return None


def _views_of(sheet):
    """取图纸上的视图，按 平面/南/北/西/东 排序。"""
    out = []
    order = {'平面': 0, '南': 1, '北': 2, '西': 3, '东': 4}
    try:
        vps = DB.FilteredElementCollector(doc, sheet.Id).OfClass(DB.Viewport).ToElements()
    except:
        return out
    for vp in vps:
        try:
            v = doc.GetElement(vp.ViewId)
        except:
            continue
        if v is None or v.IsTemplate:
            continue
        nm = v.Name or ""
        k = 9
        for pref in order:
            if nm.startswith(pref + "_"):
                k = order[pref]
                break
        out.append((k, nm, v))
    out.sort(key=lambda t: t[0])
    return out


class _ViewOption(forms.TemplateListItem):
    """视图选项：显示 图纸 / 类型 / 视图名。"""

    @property
    def name(self):
        try:
            info = self.item
        except:
            return str(self.item)
        try:
            sheet_nm, tcode, v = info
        except:
            return str(info)
        try:
            return "{}   [{}]   {}".format(sheet_nm, tcode, v.Name)
        except:
            return "{}   [{}]".format(sheet_nm, tcode)


def main():
    try:
        # ---------- 1. 收集目标图纸 ----------
        sheets = []
        try:
            sel = list(uidoc.Selection.GetElementIds())
        except:
            sel = []

        for eid in sel:
            e = doc.GetElement(eid)
            if e is None:
                continue
            sh = _sheet_of(e)
            if sh is not None and (sh.Name or "").startswith("井大样_"):
                if sh not in sheets:
                    sheets.append(sh)
                continue
            mk = _mark_of(e)
            if mk:
                sh2 = _sheet_by_mark(mk)
                if sh2 is not None and sh2 not in sheets:
                    sheets.append(sh2)

        if not sheets:
            try:
                cur = doc.ActiveView
                if isinstance(cur, DB.ViewSheet) and (cur.Name or "").startswith("井大样_"):
                    sheets.append(cur)
            except:
                pass

        if not sheets:
            forms.alert('没有可处理的井大样图纸。\n请先选中井大样图纸（或模型里的井），或切换到井大样图纸上再运行。',
                        title='一键排管标注')
            return

        # ---------- 2. 合并所有视图成一个总列表 ----------
        options = []
        for sh in sheets:
            for k, nm, v in _views_of(sh):
                options.append((k, (sh.Name, str(v.ViewType), v)))

        if not options:
            forms.alert('选中的图纸上没有可用视图。', title='一键排管标注')
            return
        options.sort(key=lambda t: t[0])

        picked = forms.SelectFromList.show(
            [_ViewOption(o[1]) for o in options],
            title='选择要标注的视图（可多选；给排管：下方/左侧两段尺寸链 + 内部净距）',
            button_name='开始标注',
            multiselect=True)
        if not picked:
            return
        if not isinstance(picked, (list, tuple)):
            picked = [picked]
        target_views = []
        for p in picked:
            try:
                target_views.append(p[2])
            except:
                pass
        if not target_views:
            return

        # 同一视图若放在多张图纸上会被重复选中：只处理一次，避免重复计数
        uniq_views = []
        uniq_ids = set()
        for v in target_views:
            try:
                vid = v.Id.Value
            except:
                vid = None
            if vid is not None:
                if vid in uniq_ids:
                    continue
                uniq_ids.add(vid)
            uniq_views.append(v)
        dup_views = len(target_views) - len(uniq_views)
        target_views = uniq_views

        # ---------- 3. 执行（整次运行在一个事务内，Ctrl+Z 可整体撤销） ----------
        t = DB.Transaction(doc, "KAIFA 一键排管标注")
        fho = t.GetFailureHandlingOptions()
        fho.SetFailuresPreprocessor(_SilentFailures())
        fho.SetClearAfterRollback(True)
        t.SetFailureHandlingOptions(fho)
        t.Start()

        purged = 0
        made = 0
        ok_views = 0
        no_bank = 0
        stats = {}
        notes = []
        try:
            for v in target_views:
                try:
                    purged += _purge_bank_dims(v)

                    n, reason = annotate_view(v, stats)
                    made += n
                    if n:
                        ok_views += 1
                    elif reason == '视图内无排管（结构框架）':
                        no_bank += 1
                    else:
                        notes.append(('{}（{}）'.format(v.Name, reason or '未生成尺寸'), 0))
                except Exception as ev:
                    notes.append(('{}（异常：{}）'.format(v.Name, ev), 0))
            st = t.Commit()
            committed = (st == DB.TransactionStatus.Committed)
        except Exception as inner:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            forms.alert('标注过程出错：\n' + str(inner) + '\n\n' + traceback.format_exc(),
                        title='一键排管标注')
            return

        if not committed:
            forms.alert('标注未能写入模型，本次修改未保存（Revit 事务状态：{}）。'.format(st), title='一键排管标注')
            return

        # ---------- 4. 报告 ----------
        msg = '排管尺寸标注完成！\n\n'
        msg += '• 处理视图：{} 个（成功 {} 个）\n'.format(len(target_views), ok_views)
        msg += '• 标注排管：{} 根（按视图累计）\n'.format(stats.get(_ST_BANK_OK, 0))
        msg += '• 生成尺寸：{} 道\n'.format(made)
        msg += '• 清除旧排管尺寸：{} 道\n'.format(purged)
        if no_bank:
            msg += '• 跳过（视图内无排管）：{} 个\n'.format(no_bank)
        if dup_views:
            msg += '• 重复选择的视图（已合并）：{} 个\n'.format(dup_views)
        if stats.get(_ST_DUP):
            msg += '• 同位置重复尺寸（已跳过）：{} 道\n'.format(stats[_ST_DUP])
        skip_txt = _fmt_skips(stats)
        if skip_txt:
            msg += '\n跳过的构件（按视图累计）：\n' + skip_txt
        bad = [n for n in notes if n[1] == 0]
        if bad:
            msg += '\n未生成尺寸的视图 {} 个：\n'.format(len(bad))
            for nm, _ in bad[:10]:
                msg += '    - {}\n'.format(nm)
            if len(bad) > 10:
                msg += '    ...（其余 {} 个同类）\n'.format(len(bad) - 10)
        forms.alert(msg, title='一键排管标注')

    except System.Exception as e:
        forms.alert('发生严重系统错误:\n' + str(e) + '\n\n' + traceback.format_exc(), title='报错追踪')
    except Exception as e:
        forms.alert('发生严重Python错误:\n' + str(e) + '\n\n' + traceback.format_exc(), title='报错追踪')


if __name__ == '__main__':
    main()
