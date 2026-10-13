# -*- coding: utf-8 -*-
__title__ = '一键井大样标注'
__doc__ = ('选中井/图纸 → 勾选要标注的视图 → 生成两类尺寸：\n'
           '① 外包总尺寸：按视图内可见构件（不含管道）的外包框，下方标宽、左侧标高；\n'
           '② 井内壁至排管尺寸链：识别井内壁，对位于井内的每根排管，下方/上方标水平尺寸链'
           '（内壁—排管边—排管宽—排管边—内壁，外侧标总内宽），左侧/右侧标竖向尺寸链（同理，外侧标总内高）。\n'
           '排管尺寸链只在剖面/立面视图生成；每次运行先清除视图内引用井构件或排管的旧尺寸再重新生成（不动管道等其它尺寸）。')

from pyrevit import revit, DB, UI, forms
import System
import traceback

doc = revit.doc
uidoc = revit.uidoc

# ============================================================
# 可调参数（改这里即可；数值是当前默认值，改后重新运行生效）
# ============================================================

# 尺寸线离框的间距（英尺，1.0 约 305 mm）：下方总宽线、左侧总高线与井外包框之间的距离；
# 该侧没有尺寸链时保持此位置，有尺寸链时总线自动外移（见 _TOTAL_GAP_MM）
_DIM_MARGIN = 1.0

# 跨度保险倍数：构件在视图平面上的尺寸超过整个视图框的这个倍数时，判为横穿的长构件并剔除
_SPAN_RATIO = 2.0

# 严格轴向判据（法线分量阈值，0.995 约等于偏角 5.7°）；越接近 1 越严格，只取严格平行于轴的面
_AXIAL_TOL = 0.995

# 同位置判重精度（英尺，0.01 约 3 mm）：同一视图内两道尺寸的端点在此精度内相同，视为同一位置，第二道跳过
_SPOT_TOL_FT = 0.01

# 毫米 → 英尺换算（Revit 内部单位为英尺，1 英尺 = 304.8 mm）
_MM_PER_FT = 304.8


def mm_to_ft(mm):
    return mm / _MM_PER_FT


# 第一道尺寸链线离井外包边的距离（mm，改这里）：第一道链的内侧线（链段线）位于此处，外侧总线在其外一层
_CHAIN_OFFSET_MM = 600.0
_CHAIN_OFFSET_FT = mm_to_ft(_CHAIN_OFFSET_MM)

# 同一侧相邻两道尺寸线之间的间距（mm，改这里）：每道尺寸链占两层（链线 + 外侧总线），层间距均为此值
_CHAIN_STEP_MM = 500.0
_CHAIN_STEP_FT = mm_to_ft(_CHAIN_STEP_MM)

# 外包总尺寸线与同侧最外一道尺寸链线之间的净距（mm，改这里）：应大于一道尺寸文字的高度，避免文字叠压；
# 只在该侧有尺寸链时生效，无尺寸链的侧仍用 _DIM_MARGIN
_TOTAL_GAP_MM = 500.0
_TOTAL_GAP_FT = mm_to_ft(_TOTAL_GAP_MM)

# 井内壁搜索分区（占外包框对应方向长度的比例）：左/右内壁只在外包框左/右各 1/3 内找，上/下内壁只在上/下各 1/3 内找
_WALL_ZONE_FRAC = 1.0 / 3.0

# 井内壁候选面最小面积（平方英尺，10 约 0.93 m2）：过滤碎面、倒角与小构件的面（待实测调整）
_WALL_AREA_MIN_FT2 = 10.0

# 排管边到内壁的净距小于此值（英尺，0.005 约 1.5 mm）时不标：零长尺寸无法生成
_GAP_MIN_FT = 0.005

# 排管是否位于井内壁之内的容差（英尺，0.01 约 3 mm）
_IN_TOL_FT = 0.01

# 尺寸类型名：只用于排管尺寸链（外包总尺寸保持原默认类型不变）
_DIM_TYPE_NAME = '井大样 - 4mm(1比100)'
_DIM_TYPE_ID = [None]  # 惰性缓存

# ============================================================
# 配置
# ============================================================

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

# 管道类：按口径「不取管道」，一律不计入包围盒。
# 注意 OST_StructuralFraming —— 本项目用结构框架族承载 HDPE 排管，
# 那根 64 英尺长的排管正是这个类别，必须排除；真实结构梁/柱在井大样里
# 通常也远超视图框，会被下面的跨度保险再筛一次。
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

# 排管类别：本项目用结构框架族承载排管（只用来找排管，不计入井包围盒）
_BANK_CAT = DB.BuiltInCategory.OST_StructuralFraming
_BANK_IDS = _ints([_BANK_CAT])

# 裁剪框自动外扩量（英尺，1.8 约 550 mm）：保证尺寸链线及文字落在裁剪框内
_CROP_PAD = 1.8

# 统计用：跳过原因与同位置重复计数键（报告按 _REPORT_SKIPS 顺序列出，元组为 (键, 单位)）
_ST_BANK_OK = '标注成功的排管'
_ST_DUP = '同位置重复尺寸（已跳过）'
_SKIP_WALL_FB = '没有识别到井内壁（用了外壁）'
_SKIP_BANK_OUT = '排管不在井内（未标注）'
_SKIP_BANK_DIR = '排管方向不水平/不垂直（未标注）'
_SKIP_BANK_ERR = '排管处理异常（未标注）'
_SKIP_HIDDEN = '不可见'
_SKIP_NOBB = '没有包围盒'
_SKIP_ZERO = '尺寸为零'
_SKIP_SPAN = '超出视图跨度（长构件）'
_SKIP_CAT = '注释/管道/结构框架等（按口径不计入）'
_SKIP_ERR = '读取异常'
_REPORT_SKIPS = [
    (_SKIP_WALL_FB, '面'),
    (_SKIP_BANK_OUT, '根'),
    (_SKIP_BANK_DIR, '根'),
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
    """提取构件所有面引用，带世界法线、原点与面积（非平面面积为 None）。非平面取 UV 中心代表值。"""
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


def _axial_faces(elem, axis):
    """构件的严格轴向面，返回 [(坐标值, face_dict), ...]，按轴排序去重。"""
    out = []
    seen = []
    for f in _extract_faces(elem):
        n = f['norm']
        v = abs(n.X) if axis == 'X' else (abs(n.Y) if axis == 'Y' else abs(n.Z))
        if v < _AXIAL_TOL:
            continue
        if axis == 'X':
            c = f['orig'].X
        elif axis == 'Y':
            c = f['orig'].Y
        else:
            c = f['orig'].Z
        dup = False
        for s in seen:
            if abs(s - c) < 0.005:
                dup = True
                break
        if dup:
            continue
        seen.append(c)
        out.append((c, f))
    out.sort(key=lambda t: t[0])
    return out


def _axial_all(elem):
    """构件三个方向的严格轴向面，返回 {'X':[(坐标,面),...],...}，按轴排序去重（排管尺寸链用）。"""
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
    """视图裁剪框的世界坐标范围 (x0,x1, y0,y1, z0,z1)。

    必须换算：CropBox.Min/Max 是视图局部坐标，而 get_BoundingBox(view)
    实测返回的是模型坐标，两者不可直接比较。
    做法是把裁剪框 8 个角点经 CropBox.Transform 转到世界后取包络。
    """
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
    """视图裁剪框内与之相交的排管（结构框架）；横断面宽度方向与视口水平不一致的计入 _SKIP_BANK_DIR。"""
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


def _ensure_crop_need(view, h_axis, ext):
    """幂等外扩裁剪框，四侧都照顾到（尺寸链可能在井的左右/上下任一侧）。
    ext = (h_lo, h_hi, z_lo, z_hi)，世界坐标，为所有尺寸线与端点的范围；只在裁剪框盖不住时才加 _CROP_PAD 扩。"""
    try:
        rng = crop_world_range(view)
        if rng is None:
            return
        h_lo, h_hi, z_lo, z_hi = ext
        pad = _CROP_PAD
        if h_axis == 'X':
            cmin, cmax = rng[0], rng[1]
            wneg, wpos = DB.XYZ(-1, 0, 0), DB.XYZ(1, 0, 0)
        else:
            cmin, cmax = rng[2], rng[3]
            wneg, wpos = DB.XYZ(0, -1, 0), DB.XYZ(0, 1, 0)
        if cmin > h_lo - pad + 0.001:
            _grow_crop(view, wneg, cmin - (h_lo - pad))
        if cmax < h_hi + pad - 0.001:
            _grow_crop(view, wpos, (h_hi + pad) - cmax)
        if rng[4] > z_lo - pad + 0.001:
            _grow_crop(view, DB.XYZ(0, 0, -1), rng[4] - (z_lo - pad))
        if rng[5] < z_hi + pad - 0.001:
            _grow_crop(view, DB.XYZ(0, 0, 1), (z_hi + pad) - rng[5])
    except:
        pass


# ============================================================
# 生成外包总尺寸
# ============================================================


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


def _find_dim_type_id():
    """按名称找尺寸类型（井大样 4mm 1:100）；找不到返回 None，则用默认类型。结果缓存。"""
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


def _try_dim(view, ref_a, ref_b, world_p1, world_p2, ledger=None, dim_type=False):
    """两个面引用之间建一条尺寸。传入 ledger 时，同位置已有尺寸则跳过并计入 ledger.dup。
    dim_type=True 时改用 _find_dim_type_id() 找到的尺寸类型（仅排管尺寸链使用）。"""
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
        if d is not None and dim_type:
            try:
                dt = _find_dim_type_id()
                if dt is not None:
                    d.ChangeTypeId(dt)
            except:
                pass
        if d is not None and key is not None:
            ledger.keys.add(key)
        return d
    except:
        return None


def _seg_dim(view, ra, rb, p1, p2, ledger, length):
    """排管尺寸链的一段：长度小于 _GAP_MIN_FT（如排管贴壁）不标，返回生成数 0/1。"""
    if length < _GAP_MIN_FT:
        return 0
    d = _try_dim(view, ra, rb, p1, p2, ledger, dim_type=True)
    return 1 if d is not None else 0


def _pt(h_axis, hv, d_axis, dv, zv):
    """按视图水平轴组装世界点：h_axis 为水平世界轴，dv 为深度（视图法向）坐标，zv 为竖向 Z。"""
    if h_axis == 'X':
        return DB.XYZ(hv, dv, zv)
    return DB.XYZ(dv, hv, zv)


def _to_s(h, lsign):
    """世界水平坐标 → 图纸水平坐标 s（s 随图纸向右增大，图纸左侧 s 最小）。"""
    return -lsign * h


def _to_h(s, lsign):
    """图纸水平坐标 s → 世界水平坐标（_to_s 的逆）。"""
    return -lsign * s


def _total_margin(extents, key):
    """外包总尺寸线离外包框的距离（英尺）。key 为总线所在的世界方向，如 'Z-'。
    该方向有尺寸链（extents 中有记录）：外移到最外一道链线之外 _TOTAL_GAP_FT；
    没有尺寸链：保持 _DIM_MARGIN 原位。"""
    reach = extents.get(key, 0.0) if extents else 0.0
    if reach <= 0:
        return _DIM_MARGIN
    return max(_DIM_MARGIN, reach + _TOTAL_GAP_FT)


def make_total_dims(view, elems, box, ledger=None, extents=None):
    """按外包框生成总尺寸：下方一道宽度、左侧一道高度。

    轴的选择随视图类型走：
      平面视图  横向=世界X，竖向=世界Y，尺寸线摆在某个 Z 深度上
      剖面视图  横向=世界X，竖向=世界Z，尺寸线摆在某个 Y 深度上
    参照面取"沿该轴最外侧的严格轴向面"；跨多个构件时取全局极值，
    标出来的就是整个外包框的总尺寸。
    extents 由 make_chain_dims 填好时，总线所在一侧若有尺寸链，总线外移到最外一道链线之外；
    无尺寸链的侧位置不变。外移后补一次裁剪框外扩，保证总线落在裁剪框内。
    """
    made = 0
    x0, x1, y0, y1, z0, z1 = box

    try:
        is_plan = (view.ViewType == DB.ViewType.FloorPlan)
    except:
        is_plan = False

    # 宽度线在下方（平面为世界 Y 最小侧，剖面为 Z 最小侧）；高度线在世界 X 最小侧
    m_w = _total_margin(extents, 'Y-' if is_plan else 'Z-')
    m_h = _total_margin(extents, 'X-')
    moved = (m_w > _DIM_MARGIN) or (m_h > _DIM_MARGIN)

    if is_plan:
        v_axis = 'Y'
        v0, v1 = y0, y1
        depth = (z0 + z1) / 2.0
    else:
        v_axis = 'Z'
        v0, v1 = z0, z1
        depth = (y0 + y1) / 2.0

    def _wpt(a, b, c):
        return DB.XYZ(a, b, c)

    # ---- 宽度（下方）：X 方向最外两侧 ----
    west = east = None
    for e in elems:
        fl = _axial_faces(e, 'X')
        if not fl:
            continue
        c0, f0 = fl[0]
        c1, f1 = fl[-1]
        if west is None or c0 < west[0]:
            west = (c0, f0)
        if east is None or c1 > east[0]:
            east = (c1, f1)
    # 线两端的延伸量仍为 _DIM_MARGIN（与原来一致），外移只改变线离框的距离 m_w / m_h
    if west and east:
        if is_plan:
            p1 = _wpt(x0 - _DIM_MARGIN, v0 - m_w, depth)
            p2 = _wpt(x1 + _DIM_MARGIN, v0 - m_w, depth)
        else:
            p1 = _wpt(x0 - _DIM_MARGIN, depth, v0 - m_w)
            p2 = _wpt(x1 + _DIM_MARGIN, depth, v0 - m_w)
        if _try_dim(view, west[1]['ref'], east[1]['ref'], p1, p2, ledger):
            made += 1

    # ---- 高度（左侧）：竖向最外两侧 ----
    lo = hi = None
    for e in elems:
        fl = _axial_faces(e, v_axis)
        if not fl:
            continue
        c0, f0 = fl[0]
        c1, f1 = fl[-1]
        if lo is None or c0 < lo[0]:
            lo = (c0, f0)
        if hi is None or c1 > hi[0]:
            hi = (c1, f1)
    if lo and hi:
        if is_plan:
            p1 = _wpt(x0 - m_h, v0 - _DIM_MARGIN, depth)
            p2 = _wpt(x0 - m_h, v1 + _DIM_MARGIN, depth)
        else:
            p1 = _wpt(x0 - m_h, depth, v0 - _DIM_MARGIN)
            p2 = _wpt(x0 - m_h, depth, v1 + _DIM_MARGIN)
        if _try_dim(view, lo[1]['ref'], hi[1]['ref'], p1, p2, ledger):
            made += 1

    # 外移后的总线须在裁剪框内（只有外移时才补；剖面才有这套外扩规则）
    if moved and not is_plan:
        _ensure_crop_need(view, 'X', (x0 - m_h, x1, z0 - m_w, z1))

    return made


def _in_purge_scope(cid):
    """本工具的尺寸只引用这三类构件：井构件（非注释、非管道）或排管（结构框架）。"""
    if cid in _BANK_IDS:
        return True
    return cid not in _EXCLUDE_IDS and cid not in _PIPE_IDS


def purge_dims(view):
    """清除该视图内「所有引用都是井构件或排管」的尺寸：外包总尺寸与排管尺寸链都在其中。
    引用了管道、注释等其它构件的尺寸，以及无引用的尺寸保留不动。返回删除数。"""
    n = 0
    try:
        dims = list(DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Dimension).ToElements())
    except:
        return n
    for d in dims:
        try:
            refs = d.References
            els = []
            for r in refs:
                els.append(doc.GetElement(r.ElementId))
            if not els:
                continue
            ok = True
            for el in els:
                if el is None or el.Category is None or not _in_purge_scope(el.Category.Id.Value):
                    ok = False
                    break
            if ok:
                doc.Delete(d.Id)
                n += 1
        except:
            pass
    return n


# ============================================================
# 井内壁识别与排管尺寸链（剖面/立面）
# ============================================================


def _pick(lst, want_max):
    """从 [(坐标, 面字典), ...] 中取坐标最大或最小的一项。"""
    if not lst:
        return None
    if want_max:
        return max(lst, key=lambda t: t[0])
    return min(lst, key=lambda t: t[0])


def _inner_walls(elems, box, h_axis, lsign, stats=None):
    """在井构件的大面中识别四侧内壁。图纸坐标：左右两侧用 s（向图纸右增大），上下两侧用 z。
    返回 (walls, fb_sides)：walls = {'L','R','B','T': (坐标, 面字典, 是否用了外壁) 或 None}，
    fb_sides 为改用外壁的侧别列表。
    内壁判据（设 s 向图纸右为正）：
      左内壁：法线朝右（+s，指向井内）、位于外包框左 1/3 内的面中 s 最大者；
      右内壁：法线朝左（-s）、位于外包框右 1/3 内的面中 s 最小者；
      下内壁：法线朝上（+z）、位于外包框下 1/3 内的面中 z 最大者；
      上内壁：法线朝下（-z）、位于外包框上 1/3 内的面中 z 最小者。
    某侧找不到时改用外壁（法线朝外、位于同侧 1/3 内、最靠外者），不静默：记入 fb_sides，由报告显示。
    """
    x0, x1, y0, y1, z0, z1 = box
    h0, h1 = (x0, x1) if h_axis == 'X' else (y0, y1)
    s_lo, s_hi = sorted((_to_s(h0, lsign), _to_s(h1, lsign)))
    zone_s = (s_hi - s_lo) * _WALL_ZONE_FRAC
    zone_z = (z1 - z0) * _WALL_ZONE_FRAC
    tol = _AXIAL_TOL
    inner_c = {'L': [], 'R': [], 'B': [], 'T': []}
    outer_c = {'L': [], 'R': [], 'B': [], 'T': []}
    for e in elems:
        for f in _extract_faces(e):
            a = f.get('area')
            if a is None or a < _WALL_AREA_MIN_FT2:
                continue
            try:
                n = f['norm']
                o = f['orig']
                ns = -lsign * (n.X if h_axis == 'X' else n.Y)
                s = _to_s(o.X if h_axis == 'X' else o.Y, lsign)
                z = o.Z
                if abs(ns) >= tol:
                    if s_lo <= s <= s_lo + zone_s:
                        if ns >= tol:
                            inner_c['L'].append((s, f))
                        else:
                            outer_c['L'].append((s, f))
                    if s_hi - zone_s <= s <= s_hi:
                        if ns <= -tol:
                            inner_c['R'].append((s, f))
                        else:
                            outer_c['R'].append((s, f))
                if abs(n.Z) >= tol:
                    if z0 <= z <= z0 + zone_z:
                        if n.Z >= tol:
                            inner_c['B'].append((z, f))
                        else:
                            outer_c['B'].append((z, f))
                    if z1 - zone_z <= z <= z1:
                        if n.Z <= -tol:
                            inner_c['T'].append((z, f))
                        else:
                            outer_c['T'].append((z, f))
            except:
                continue

    walls = {}
    fb_sides = []
    for side, want_max in (('L', True), ('R', False), ('B', True), ('T', False)):
        hit = _pick(inner_c[side], want_max)
        fb = False
        if hit is None:
            hit = _pick(outer_c[side], not want_max)
            fb = True
            fb_sides.append(side)
        walls[side] = (hit[0], hit[1], fb) if hit is not None else None
    return walls, fb_sides


def _note_reach(extents, key, dist):
    """记下世界方向 key（如 'Z-'）上最外一道链线离外包框的距离（英尺，取最大）。extents 为 None 时不记。"""
    if extents is not None:
        extents[key] = max(extents.get(key, 0.0), dist)


def make_chain_dims(view, elems, box, ledger=None, stats=None, extents=None):
    """为视图内位于井内的每根排管生成尺寸链。返回 (生成尺寸数, 原因)；原因为 None 表示正常处理。
    每根排管两条链（每条链 3 段 + 1 道总内尺寸）：
      水平链：排管在下半部 → 放在井外下侧；上半部 → 放在井外上侧。
      竖向链：排管在左半部 → 放在井外左侧；否则放在井外右侧。
    同一侧的链依次向外排，每道链占两层（链线 + 外侧总线），层间距为 _CHAIN_STEP_FT。
    extents 若传入 dict，则记下每个世界方向上最外一道链线离外包框的距离（键如 'Z-'、'X+'），
    供 make_total_dims 把外包总尺寸线移到链线之外。"""
    made = 0
    try:
        if view.ViewType == DB.ViewType.FloorPlan:
            return 0, '平面视图不生成排管尺寸链'
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

    x0, x1, y0, y1, z0, z1 = box
    d_mid = (x0 + x1) / 2.0 if d_axis == 'X' else (y0 + y1) / 2.0
    walls, fb_sides = _inner_walls(elems, box, h_axis, lsign, stats)
    if any(v is None for v in walls.values()):
        for _ in banks:
            _bump(stats, _SKIP_BANK_ERR)
        return 0, '井内壁与外壁均无法识别'

    h0, h1 = (x0, x1) if h_axis == 'X' else (y0, y1)
    s_lo, s_hi = sorted((_to_s(h0, lsign), _to_s(h1, lsign)))
    sL, wl = walls['L'][0], walls['L'][1]
    sR, wr = walls['R'][0], walls['R'][1]
    zB, wb = walls['B'][0], walls['B'][1]
    zT, wt = walls['T'][0], walls['T'][1]
    s_mid = (sL + sR) / 2.0
    z_mid = (zB + zT) / 2.0
    h_mid = (h0 + h1) / 2.0

    def P(sv, zv):
        """图纸坐标 (s, z) → 世界点（落在视图深度 d_mid 上）。"""
        return _pt(h_axis, _to_h(sv, lsign), d_axis, d_mid, zv)

    cnt = {'L': 0, 'R': 0, 'B': 0, 'T': 0}   # 每侧已占用的尺寸线层数
    ext_s = [s_lo, s_hi]
    ext_z = [z0, z1]

    for bank in banks:
        try:
            bf = _axial_all(bank)
            hf = bf[h_axis]
            zf = bf['Z']
            if len(hf) < 2 or len(zf) < 2:
                _bump(stats, _SKIP_BANK_DIR)
                continue
            bs = sorted([(_to_s(c, lsign), f) for c, f in hf], key=lambda t: t[0])
            bl_s, bl_f = bs[0]
            br_s, br_f = bs[-1]
            bb_z, bb_f = zf[0]
            bt_z, bt_f = zf[-1]
            if br_s - bl_s <= 0 or bt_z - bb_z <= 0:
                _bump(stats, _SKIP_BANK_DIR)
                continue
            if (bl_s < sL - _IN_TOL_FT or br_s > sR + _IN_TOL_FT or
                    bb_z < zB - _IN_TOL_FT or bt_z > zT + _IN_TOL_FT):
                _bump(stats, _SKIP_BANK_OUT)
                continue

            v_side = 'B' if (bb_z + bt_z) / 2.0 < z_mid else 'T'
            h_side = 'L' if (bl_s + br_s) / 2.0 < s_mid else 'R'
            n_ch = 0

            # ---- 水平链：内壁—排管—内壁 + 总内宽（图纸 s 方向） ----
            k = cnt[v_side]
            off_seg = _CHAIN_OFFSET_FT + k * _CHAIN_STEP_FT
            off_tot = off_seg + _CHAIN_STEP_FT
            cnt[v_side] += 2
            if v_side == 'B':
                zs, zt = z0 - off_seg, z0 - off_tot
                ext_z[0] = min(ext_z[0], zt)
                _note_reach(extents, 'Z-', off_tot)
            else:
                zs, zt = z1 + off_seg, z1 + off_tot
                ext_z[1] = max(ext_z[1], zt)
                _note_reach(extents, 'Z+', off_tot)
            n_ch += _seg_dim(view, wl['ref'], bl_f['ref'], P(sL, zs), P(bl_s, zs), ledger, bl_s - sL)
            n_ch += _seg_dim(view, bl_f['ref'], br_f['ref'], P(bl_s, zs), P(br_s, zs), ledger, br_s - bl_s)
            n_ch += _seg_dim(view, br_f['ref'], wr['ref'], P(br_s, zs), P(sR, zs), ledger, sR - br_s)
            n_ch += _seg_dim(view, wl['ref'], wr['ref'], P(sL, zt), P(sR, zt), ledger, sR - sL)

            # ---- 竖向链：内底—排管—内顶 + 总内高（图纸 z 方向） ----
            k = cnt[h_side]
            off_seg = _CHAIN_OFFSET_FT + k * _CHAIN_STEP_FT
            off_tot = off_seg + _CHAIN_STEP_FT
            cnt[h_side] += 2
            if h_side == 'L':
                ss, st = s_lo - off_seg, s_lo - off_tot
                ext_s[0] = min(ext_s[0], st)
            else:
                ss, st = s_hi + off_seg, s_hi + off_tot
                ext_s[1] = max(ext_s[1], st)
            # 竖向链线在世界坐标中的方向（X+/X-，或视图为 X 向时 Y+/Y-）
            _note_reach(extents, h_axis + ('+' if _to_h(st, lsign) > h_mid else '-'), off_tot)
            n_ch += _seg_dim(view, wb['ref'], bb_f['ref'], P(ss, zB), P(ss, bb_z), ledger, bb_z - zB)
            n_ch += _seg_dim(view, bb_f['ref'], bt_f['ref'], P(ss, bb_z), P(ss, bt_z), ledger, bt_z - bb_z)
            n_ch += _seg_dim(view, bt_f['ref'], wt['ref'], P(ss, bt_z), P(ss, zT), ledger, zT - bt_z)
            n_ch += _seg_dim(view, wb['ref'], wt['ref'], P(st, zB), P(st, zT), ledger, zT - zB)

            if n_ch:
                made += n_ch
                _bump(stats, _ST_BANK_OK)
        except:
            _bump(stats, _SKIP_BANK_ERR)
            continue

    if made:
        for _ in fb_sides:
            _bump(stats, _SKIP_WALL_FB)
        ha, hb = _to_h(ext_s[0], lsign), _to_h(ext_s[1], lsign)
        _ensure_crop_need(view, h_axis, (min(ha, hb), max(ha, hb), ext_z[0], ext_z[1]))
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
                        title='一键井大样标注')
            return

        # ---------- 2. 合并所有视图成一个总列表 ----------
        options = []
        for sh in sheets:
            for k, nm, v in _views_of(sh):
                options.append((k, (sh.Name, str(v.ViewType), v)))

        if not options:
            forms.alert('选中的图纸上没有可用视图。', title='一键井大样标注')
            return
        options.sort(key=lambda t: t[0])

        picked = forms.SelectFromList.show(
            [_ViewOption(o[1]) for o in options],
            title='选择要标注的视图（可多选；下方标宽、左侧标高）',
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
        t = DB.Transaction(doc, "KAIFA 一键井大样标注")
        fho = t.GetFailureHandlingOptions()
        fho.SetFailuresPreprocessor(_SilentFailures())
        fho.SetClearAfterRollback(True)
        t.SetFailureHandlingOptions(fho)
        t.Start()

        purged = 0
        made = 0
        ok_views = 0
        stats = {}
        notes = []
        try:
            for v in target_views:
                try:
                    purged += purge_dims(v)

                    elems = collect_target_elems(v, stats)
                    if not elems:
                        notes.append(('{}（无可用构件）'.format(v.Name), 0))
                        continue

                    box = union_box(v, elems)
                    if box is None:
                        notes.append(('{}（无法计算包围盒）'.format(v.Name), 0))
                        continue

                    # 先排尺寸链，再排总尺寸：总尺寸需要知道各侧最外一道链线的位置
                    led = _SpotLedger()
                    ext = {}
                    cn, creason = make_chain_dims(v, elems, box, led, stats, ext)
                    n = make_total_dims(v, elems, box, led, ext)
                    _bump(stats, _ST_DUP, led.dup)
                    made += n + cn
                    if n + cn:
                        ok_views += 1
                        notes.append(('{}'.format(v.Name), n + cn))
                    else:
                        why = creason if (creason and creason != '视图内无排管（结构框架）') else '未生成尺寸'
                        notes.append(('{}（{}）'.format(v.Name, why), 0))
                except Exception as ev:
                    notes.append(('{}（异常：{}）'.format(v.Name, ev), 0))
            st = t.Commit()
            committed = (st == DB.TransactionStatus.Committed)
        except Exception as inner:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            forms.alert('标注过程出错：\n' + str(inner) + '\n\n' + traceback.format_exc(),
                        title='一键井大样标注')
            return

        if not committed:
            forms.alert('标注未能写入模型，本次修改未保存（Revit 事务状态：{}）。'.format(st), title='一键井大样标注')
            return

        # ---------- 4. 报告 ----------
        msg = '井大样标注完成！\n\n'
        msg += '• 处理视图：{} 个（成功 {} 个）\n'.format(len(target_views), ok_views)
        msg += '• 标注排管（井内尺寸链）：{} 根（按视图累计）\n'.format(stats.get(_ST_BANK_OK, 0))
        msg += '• 生成尺寸：{} 道（外包总尺寸 + 尺寸链）\n'.format(made)
        msg += '• 清除旧尺寸：{} 道\n'.format(purged)
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
        forms.alert(msg, title='一键井大样标注')

    except System.Exception as e:
        forms.alert('发生严重系统错误:\n' + str(e) + '\n\n' + traceback.format_exc(), title='报错追踪')
    except Exception as e:
        forms.alert('发生严重Python错误:\n' + str(e) + '\n\n' + traceback.format_exc(), title='报错追踪')


if __name__ == '__main__':
    main()
