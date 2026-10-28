# ---- well_geo（井大样变动同步 / 井大样体检 共用）：井外轮廓、视图框与井的比对、井盖到内壁距离 ----
# 依赖：先 include annotate_core，且已定义 doc、WCFG。
import math
import time

FT = 304.8
RULES_FILE = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments),
                          u'KaifaTools', u'井展开图规则.json')
TOL_MM = 30.0          # 位置/大小差超过这个值才算变动


def _rules():
    """井展开图生成视图时用的余量（和展开图一致，才能对回去）。"""
    r = {u'crop_margin_mm': 100.0, u'face_out_mm': 100.0, u'face_depth_mm': 1100.0}
    try:
        with io.open(RULES_FILE, 'r', encoding='utf-8') as f:
            r.update(json.load(f))
    except Exception:
        pass
    return r


def footprint(elem):
    """井外轮廓（英尺）：中心 x、y 和半长、半宽。从实体顶点取（薄壳族只取竖直大墙面），和井展开图生成时一致。"""
    bb = elem.get_BoundingBox(None)
    if bb is None:
        return None
    cx = (bb.Min.X + bb.Max.X) / 2.0
    cy = (bb.Min.Y + bb.Max.Y) / 2.0
    dx = (bb.Max.X - bb.Min.X) / 2.0
    dy = (bb.Max.Y - bb.Min.Y) / 2.0
    loc = elem.Location
    if isinstance(loc, DB.LocationPoint) and loc.Point:
        cx, cy = loc.Point.X, loc.Point.Y
    try:
        opt = DB.Options()
        opt.ComputeReferences = False
        opt.DetailLevel = DB.ViewDetailLevel.Fine
        x0 = y0 = float('inf')
        x1 = y1 = float('-inf')
        found = False
        for obj in elem.get_Geometry(opt):
            if not isinstance(obj, DB.GeometryInstance):
                continue
            for s in obj.GetInstanceGeometry():
                if not (isinstance(s, DB.Solid) and s.Faces.Size > 0):
                    continue
                real = s.Volume > 0.001
                for f in s.Faces:
                    if not real:
                        try:
                            if f.Area < 10.0 or abs(f.ComputeNormal(DB.UV(0.5, 0.5)).Z) > 0.2:
                                continue
                        except Exception:
                            continue
                    found = True
                    mesh = f.Triangulate()
                    if mesh:
                        for pt in mesh.Vertices:
                            x0 = min(x0, pt.X); x1 = max(x1, pt.X)
                            y0 = min(y0, pt.Y); y1 = max(y1, pt.Y)
        if found and x1 > x0 and y1 > y0:
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            dx, dy = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    except Exception:
        pass
    return cx, cy, dx, dy


def world_box(v):
    b = v.CropBox
    xs, ys = [], []
    for x in (b.Min.X, b.Max.X):
        for y in (b.Min.Y, b.Max.Y):
            for z in (b.Min.Z, b.Max.Z):
                p = b.Transform.OfPoint(DB.XYZ(x, y, z))
                xs.append(p.X); ys.append(p.Y)
    return min(xs), max(xs), min(ys), max(ys)


def _kind(name):
    for pref, k in ((u'平面', 'P'), (u'南', 'S'), (u'北', 'N'), (u'西', 'W'), (u'东', 'E')):
        if (name or u'').startswith(pref + u'_'):
            return k
    return None


def _well_map():
    out = {}
    for e in DB.FilteredElementCollector(doc).OfCategory(_WELL_CAT).WhereElementIsNotElementType().ToElements():
        mk = _mark_of(e)
        if mk and mk not in out:
            out[mk] = e
    return out


def scan(sheets, wells, rules):
    """逐张图纸比对：平面裁剪框 vs 井现在的外轮廓。返回 [行]。"""
    m = rules[u'crop_margin_mm'] / FT
    rows = []
    for sh in sheets:
        mark = _sheet_mark(sh)
        vs = {}
        for _k, nm, v in _views_of(sh):
            k = _kind(nm)
            if k:
                vs[k] = v
        row = {'sheet': sh, 'mark': mark, 'views': vs, 'status': 'ok'}
        well = wells.get(mark) if mark else None
        if well is None:
            row['status'] = 'nowell'
        elif 'P' not in vs:
            row['status'] = 'noplan'
        else:
            fp = footprint(well)
            if fp is None:
                row['status'] = 'nowell'
            else:
                cx, cy, dx, dy = fp
                px0, px1, py0, py1 = world_box(vs['P'])
                row.update({'well': well, 'fp': fp,
                            'shift': ((px0 + px1) / 2.0 - cx, (py0 + py1) / 2.0 - cy),
                            'dsize': ((px1 - px0) - 2 * (dx + m), (py1 - py0) - 2 * (dy + m))})
                mv = math.hypot(*row['shift']) * FT
                rs = max(abs(row['dsize'][0]), abs(row['dsize'][1])) * FT
                if mv > TOL_MM or rs > TOL_MM:
                    row['status'] = 'changed'
                    row['moved'] = mv > TOL_MM
                    row['resized'] = rs > TOL_MM
        rows.append(row)
    return rows


def _describe(row):
    parts = []
    if row.get('moved'):
        parts.append(u'位置差 %.0f mm' % (math.hypot(*row['shift']) * FT))
    if row.get('resized'):
        parts.append(u'大小/方向变了（框宽差 %.0f、高差 %.0f mm）' % (row['dsize'][0] * FT, row['dsize'][1] * FT))
    return u'、'.join(parts)




MM = 304.8


def cover_gaps(v, el, box=None):
    """井盖外沿到井内壁的距离（mm）：[左或下低端, 右或上高端 …] 共四个值（沿平面横向两侧、纵向两侧）；
    识别不到内壁返回 None，没有井盖层返回 'nocover'。转过的井沿井自己的方向量。"""
    if not _axes_ok(el, _frame(v)):
        info = _rot_info(el)
        if info is None:
            return None
        faces = _cover_layer(el)
        if not faces:
            return 'nocover'
        a, b = info['a'], info['b']
        spans = ((a, b, info['uL'], info['uR'], True), (a, b, info['uL'], info['uR'], False),
                 (b, a, info['wB'], info['wT'], True), (b, a, info['wB'], info['wT'], False))
    else:
        if box is None:
            box = _face_box(el, _clip_box(union_box(v, el), crop_world_range(v)))
        fr = _frame(v)
        walls, _fb = _inner_walls(el, box, fr, None)
        if any(x is None for x in walls.values()):
            return None
        faces = _cover_layer(el)
        if not faces:
            return 'nocover'
        unit = {'X': DB.XYZ(1, 0, 0), 'Y': DB.XYZ(0, 1, 0)}
        hv, vv = unit[fr['h']], unit[fr['v']]
        wL, wR, wB, wT = walls['L'][0], walls['R'][0], walls['B'][0], walls['T'][0]
        spans = ((hv, vv, wL, wR, True), (hv, vv, wL, wR, False), (vv, hv, wB, wT, True), (vv, hv, wB, wT, False))
    out = []
    for av, ov, lo, hi, low in spans:
        h = _cover_edge(faces, av, ov, lo, hi, low)
        out.append(None if h is None else round(((h[0] - lo) if low else (hi - h[0])) * MM))
    return out
