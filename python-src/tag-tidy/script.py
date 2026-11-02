# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '构件标记\n一键整理'
__author__ = 'didi'
__doc__ = ('一键整理当前平面视图里的构件标记（出图用）：\n'
           '① 一根构件只留一个标记（重复的可删），引线末端设到它自己构件的中心（线式取线段中点），不会再指到别的构件；标记方向跟管道平行（竖管竖排）；\n'
           '② 每个标记自动选空的一侧（8 个方向），不压别的标记、不压别的构件；放不下就沿引线引出去；\n'
           '③ 同一侧的标记对齐到同一行/同一列；\n'
           '④ 悬空标记（构件没了）可以删除；视图里该标但没标的构件可以补标；\n'
           '写入后逐个回读核对，引线末端不在自己构件上的整次回滚。整次一个事务，Ctrl+Z 可撤销。\n'
           '标记离构件的距离、引线上限、对齐容差、是否补标等都能在「设置…」里改。')

import io
import os
import json
import math
import traceback
import clr
import System
clr.AddReference('RevitAPI')
from Autodesk.Revit import DB
from System.Collections.Generic import List
from pyrevit import revit, forms

# @include _shared/form_fit.py
# @include _shared/settings_ui.py

doc = revit.doc
uidoc = revit.uidoc
TITLE = '构件标记一键整理'
FT = 304.8
DOC_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), 'KaifaTools')
SETTINGS = os.path.join(DOC_DIR, '构件标记整理设置.json')
DEFAULTS = {
    '说明': ('side_offset_mm：标记头离管边缘再往外留多少（图纸 mm）；bundle_gap_m：并行路由之间多远以内算一组（米），组内最左的标左边、最右的标右边；gap_mm：标记离任何构件的最小距离（图纸 mm）；max_leader_mm：引线最长（图纸 mm）；step_mm：往外试探一步；'
           'align_mm：同侧标记吸附到同一行/列的容差；text_w_mm / text_h_mm：读不到标记头实际大小时的估计值；'
           'add_missing：是否补标缺标构件；delete_orphans：是否删除悬空标记；min_pipe_mm：线性构件短于这个长度（模型 mm）不补标。'),
    'gap_mm': 2.0,
    'max_leader_mm': 150.0,
    'side_offset_mm': 5.0,
    'bundle_gap_m': 20.0,
    'step_mm': 3.0,
    'align_mm': 4.0,
    'text_w_mm': 60.0,
    'text_h_mm': 8.0,
    'add_missing': False,
    'delete_orphans': True,
    'min_pipe_mm': 1500.0,
}
DIRS = [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1),
        (0.38, 0.92), (0.92, 0.38), (0.92, -0.38), (0.38, -0.92), (-0.38, -0.92), (-0.92, -0.38), (-0.92, 0.38), (-0.38, 0.92)]
# 障碍只取机电管线/设备类；楼板、墙、柱等大面积建筑背景不当障碍（标记压在楼板上是正常的）
OBSTACLE_CATS = ('管道', '管件', '管道附件', '风管', '风管管件', '风管附件', '线管', '线管配件', '电缆桥架', '电缆桥架配件', '机械设备', '电气设备',
                 '电气装置', '卫浴装置', '喷头', '常规模型', '专用设备', '照明设备', '火警设备', '数据设备', '通讯设备', '安全设备', '灯具')


def load():
    s = dict(DEFAULTS)
    try:
        if os.path.exists(SETTINGS):
            with io.open(SETTINGS, 'r', encoding='utf-8-sig') as f:
                s.update(json.load(f))
    except Exception:
        pass
    return s


def open_settings():
    cur = load()
    fields = [
        {'key': 'gap_mm', 'label': '标记离构件最小距离 (图纸 mm)', 'kind': 'num', 'min': 0, 'max': 30, 'hint': '标记头离构件的最小距离。'},
        {'key': 'max_leader_mm', 'label': '引线最长 (图纸 mm)', 'kind': 'num', 'min': 5, 'max': 300, 'hint': '放不下时沿引线引出去，最远到这里。'},
        {'key': 'side_offset_mm', 'label': '标记离管边缘 (图纸 mm)', 'kind': 'num', 'min': 0, 'max': 100, 'hint': '标记头离管边缘再往外留多少。'},
        {'key': 'bundle_gap_m', 'label': '并行路由成组距离 (米)', 'kind': 'num', 'min': 1, 'max': 500, 'hint': '并行、相邻的路由相距在这个范围内算一组：最左的标左边、最右的标右边，中间留空。'},
        {'key': 'step_mm', 'label': '往外试探步长 (图纸 mm)', 'kind': 'num', 'min': 0.5, 'max': 30, 'hint': '找空位时每次往外走多远。'},
        {'key': 'align_mm', 'label': '对齐容差 (图纸 mm)', 'kind': 'num', 'min': 0, 'max': 30, 'hint': '同一侧的标记，位置差在这个范围内就对齐到同一行/列。'},
        {'key': 'text_w_mm', 'label': '标记头宽度估计 (图纸 mm)', 'kind': 'num', 'min': 3, 'max': 100, 'hint': '读不到标记头大小时用这个值。'},
        {'key': 'text_h_mm', 'label': '标记头高度估计 (图纸 mm)', 'kind': 'num', 'min': 2, 'max': 50, 'hint': '同上。'},
        {'key': 'add_missing', 'label': '补标缺标的构件', 'kind': 'bool', 'hint': '视图里同类别、同族已有标记，但某些构件没标的，用同样的标记类型补上。'},
        {'key': 'delete_orphans', 'label': '删除悬空标记', 'kind': 'bool', 'hint': '标记的构件已经不存在（显示问号）的，删掉。'},
        {'key': 'min_pipe_mm', 'label': '线性构件补标最短长度 (模型 mm)', 'kind': 'num', 'min': 0, 'max': 100000, 'hint': '太短的管不补标。'},
    ]
    res = su_fields_dialog(TITLE + ' · 设置', '改完点保存，下次运行生效。设置文件：' + SETTINGS, fields, cur, DEFAULTS,
                           extra_buttons=[('用记事本打开文件', lambda: su_open_file(SETTINGS))], width=700, height=560)
    if res is None:
        return False
    out = dict(DEFAULTS)
    out.update(cur)
    out.update(res)
    if not os.path.isdir(DOC_DIR):
        os.makedirs(DOC_DIR)
    with io.open(SETTINGS, 'w', encoding='utf-8') as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    return True


class _Fails(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        for m in fa.GetFailureMessages():
            if m.GetSeverity() == DB.FailureSeverity.Warning:
                fa.DeleteWarning(m)
        return DB.FailureProcessingResult.Continue

def rect_overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def rect(cx, cy, w, h):
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def grow(r, g):
    return (r[0] - g, r[1] - g, r[2] + g, r[3] + g)


def _ccw(px, py, qx, qy, rx, ry):
    return (ry - py) * (qx - px) > (qy - py) * (rx - px)


def _seg_cross(ax, ay, bx, by, cx, cy, dx, dy):
    return _ccw(ax, ay, cx, cy, dx, dy) != _ccw(bx, by, cx, cy, dx, dy) and _ccw(ax, ay, bx, by, cx, cy) != _ccw(ax, ay, bx, by, dx, dy)


def seg_hits_rect(seg, r):
    """线段 (x1,y1,x2,y2) 与矩形 r 相交（端点在内，或和四条边相交）。"""
    x1, y1, x2, y2 = seg
    if r[0] <= x1 <= r[2] and r[1] <= y1 <= r[3]:
        return True
    if r[0] <= x2 <= r[2] and r[1] <= y2 <= r[3]:
        return True
    for ex1, ey1, ex2, ey2 in ((r[0], r[1], r[2], r[1]), (r[2], r[1], r[2], r[3]), (r[2], r[3], r[0], r[3]), (r[0], r[3], r[0], r[1])):
        if _seg_cross(x1, y1, x2, y2, ex1, ey1, ex2, ey2):
            return True
    return False


class Obstacles(object):
    """视图里所有模型构件（含被标记构件自己）的平面障碍：线式按线段（带管径半宽），其余按平面包围框。网格索引加速。"""
    CELL = 10.0     # ft

    def __init__(self):
        self.cells = {}
        self.items = []

    def _keys(self, x0, y0, x1, y1):
        c = self.CELL
        for i in range(int(math.floor(x0 / c)), int(math.floor(x1 / c)) + 1):
            for j in range(int(math.floor(y0 / c)), int(math.floor(y1 / c)) + 1):
                yield (i, j)

    def add_seg(self, x1, y1, x2, y2, half, eid):
        idx = len(self.items)
        self.items.append(('s', (x1, y1, x2, y2), half, eid))
        for k in self._keys(min(x1, x2) - half, min(y1, y2) - half, max(x1, x2) + half, max(y1, y2) + half):
            self.cells.setdefault(k, []).append(idx)

    def add_rect(self, r, eid):
        idx = len(self.items)
        self.items.append(('r', r, 0.0, eid))
        for k in self._keys(r[0], r[1], r[2], r[3]):
            self.cells.setdefault(k, []).append(idx)

    def hits(self, rc, margin=0.0, skip_eid=None):
        """矩形 rc（外扩 margin）压到的构件个数。"""
        q = grow(rc, margin)
        seen = set()
        n = 0
        for k in self._keys(q[0], q[1], q[2], q[3]):
            for idx in self.cells.get(k, ()):
                if idx in seen:
                    continue
                seen.add(idx)
                kind, g, half, eid = self.items[idx]
                if skip_eid is not None and eid == skip_eid:
                    continue
                if kind == 'r':
                    if rect_overlap(q, g):
                        n += 1
                else:
                    if seg_hits_rect(g, grow(q, half)):
                        n += 1
        return n


def _half_width(el):
    """线式构件的半宽（ft）：管径/风管宽/线管径，取不到用 0.1 m。"""
    for bip in (DB.BuiltInParameter.RBS_PIPE_OUTER_DIAMETER, DB.BuiltInParameter.RBS_CONDUIT_OUTER_DIAM_PARAM,
                DB.BuiltInParameter.RBS_CURVE_WIDTH_PARAM):
        try:
            p = el.get_Parameter(bip)
            if p is not None and p.HasValue and p.AsDouble() > 0:
                return p.AsDouble() / 2.0
        except Exception:
            pass
    return 0.1 / 0.3048


def build_obstacles(view):
    ob = Obstacles()
    skip_cats = set()
    n = 0
    for e in DB.FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType():
        try:
            c = e.Category
            if c is None or c.CategoryType != DB.CategoryType.Model:
                continue
            if c.Name not in OBSTACLE_CATS:
                continue
            if isinstance(e, (DB.IndependentTag,)):
                continue
            loc = e.Location
            if isinstance(loc, DB.LocationCurve):
                cv = loc.Curve
                p0, p1 = cv.GetEndPoint(0), cv.GetEndPoint(1)
                ob.add_seg(p0.X, p0.Y, p1.X, p1.Y, _half_width(e), e.Id.Value)
                n += 1
                continue
            bb = e.get_BoundingBox(view)
            if bb is None:
                continue
            ob.add_rect((bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y), e.Id.Value)
            n += 1
        except Exception:
            continue
    return ob, n


def center_of(el):
    """构件中心：线式取线段中点，点式取插入点，其它取平面包围框中心。"""
    loc = el.Location
    if isinstance(loc, DB.LocationCurve):
        return loc.Curve.Evaluate(0.5, True)
    if isinstance(loc, DB.LocationPoint):
        return loc.Point
    bb = el.get_BoundingBox(None)
    if bb is not None:
        return DB.XYZ((bb.Min.X + bb.Max.X) / 2.0, (bb.Min.Y + bb.Max.Y) / 2.0, bb.Min.Z)
    return None


def measure_tag_heads(view, items, est_w, est_h):
    """实测每种（标记类型 + 文字）的标记头大小和「包围框中心 − 标记头位置」偏移：临时去掉引线、重新生成、读包围框，回滚。"""
    cache = {}
    t = DB.Transaction(doc, '测量标记头')
    t.Start()
    try:
        for x in items:
            tg = x['t']
            try:
                key = (tg.GetTypeId().Value, tg.TagText, x.get('orient'))
            except Exception:
                key = (tg.GetTypeId().Value, '', x.get('orient'))
            if key in cache:
                x['size'] = cache[key]
                continue
            res = (est_w, est_h, 0.0, 0.0)
            try:
                had = tg.HasLeader
                tg.HasLeader = False
                if x.get('orient') is not None:
                    tg.TagOrientation = x['orient']
                doc.Regenerate()
                bb = tg.get_BoundingBox(view)
                if bb is not None:
                    h = tg.TagHeadPosition
                    w, hh = bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y
                    if w > 0 and hh > 0:
                        res = (w, hh, (bb.Min.X + bb.Max.X) / 2.0 - h.X, (bb.Min.Y + bb.Max.Y) / 2.0 - h.Y)
                tg.HasLeader = had
            except Exception:
                pass
            cache[key] = res
            x['size'] = res
    finally:
        t.RollBack()
    return len(cache)


def build_routes(ob):
    """线式构件按首尾相接（端点 0.3 m 内）连成路由。返回 (eid→路由号, 路由信息列表)。"""
    segs = [(g, eid) for kind, g, half, eid in ob.items if kind == 's']
    tol = 0.3 / 0.3048
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    for g, eid in segs:
        parent.setdefault(eid, eid)
    grid = {}
    for g, eid in segs:
        for (x, y) in ((g[0], g[1]), (g[2], g[3])):
            k = (int(math.floor(x / 2.0)), int(math.floor(y / 2.0)))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for (x2, y2, e2) in grid.get((k[0] + dx, k[1] + dy), ()):
                        if e2 != eid and math.hypot(x - x2, y - y2) <= tol:
                            union(eid, e2)
            grid.setdefault(k, []).append((x, y, eid))
    routes = {}
    for g, eid in segs:
        r = find(eid)
        info = routes.setdefault(r, {'len_x': 0.0, 'len_y': 0.0, 'xs': [], 'ys': [], 'eids': set()})
        info['len_x'] += abs(g[2] - g[0])
        info['len_y'] += abs(g[3] - g[1])
        info['xs'].extend((g[0], g[2]))
        info['ys'].extend((g[1], g[3]))
        info['eids'].add(eid)
    eid2route = {}
    out = []
    for r, info in routes.items():
        info['axis'] = 'v' if info['len_y'] >= info['len_x'] else 'h'
        info['xm'] = sum(info['xs']) / len(info['xs'])
        info['ym'] = sum(info['ys']) / len(info['ys'])
        info['x0'], info['x1'] = min(info['xs']), max(info['xs'])
        info['y0'], info['y1'] = min(info['ys']), max(info['ys'])
        info['side'] = None
        info['id'] = len(out)
        for eid in info['eids']:
            eid2route[eid] = info['id']
        out.append(info)
    return eid2route, out


def assign_sides(routes, bundle_ft):
    """并行相邻的路由成组：竖向路由按 x 排，最左标左边、最右标右边、中间的按靠哪边；横向路由按 y 排，最下标下边、最上标上边。"""
    for axis in ('v', 'h'):
        rs = [r for r in routes if r['axis'] == axis]
        n = len(rs)
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(n):
            for j in range(i + 1, n):
                a, b = rs[i], rs[j]
                if axis == 'v':
                    ov = min(a['y1'], b['y1']) - max(a['y0'], b['y0'])
                    gap = abs(a['xm'] - b['xm'])
                else:
                    ov = min(a['x1'], b['x1']) - max(a['x0'], b['x0'])
                    gap = abs(a['ym'] - b['ym'])
                if ov > 0 and gap <= bundle_ft:
                    parent[find(i)] = find(j)
        groups = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(rs[i])
        for g in groups.values():
            if len(g) < 2:
                continue
            key = (lambda r: r['xm']) if axis == 'v' else (lambda r: r['ym'])
            g.sort(key=key)
            mid = (key(g[0]) + key(g[-1])) / 2.0
            for k, r in enumerate(g):
                low = (k == 0) or (k < len(g) - 1 and key(r) < mid)
                if axis == 'v':
                    r['side'] = (-1, 0) if low else (1, 0)
                else:
                    r['side'] = (0, -1) if low else (0, 1)


def plan(view, cfg):
    scale = max(int(view.Scale), 1)
    m = lambda mm: mm * scale / FT
    p_mm = lambda ft: ft * FT / scale
    gap, step, max_len, tol = m(cfg['gap_mm']), m(cfg['step_mm']), m(cfg['max_leader_mm']), m(cfg['align_mm'])
    tags = list(DB.FilteredElementCollector(doc, view.Id).OfClass(DB.IndependentTag))
    orphans, items, multi = [], [], []
    for t in tags:
        try:
            if t.IsOrphaned:
                orphans.append(t)
                continue
        except Exception:
            pass
        ids = list(t.GetTaggedLocalElementIds())
        if len(ids) != 1:
            multi.append(t)
            continue
        el = doc.GetElement(ids[0])
        if el is None:
            orphans.append(t)
            continue
        try:
            refs = list(t.GetTaggedReferences())
        except Exception:
            refs = []
        a = center_of(el)
        if not refs or a is None:
            multi.append(t)
            continue
        items.append({'t': t, 'el': el, 'ref': refs[0], 'a': a, 'head': t.TagHeadPosition, 'own': el.Id.Value})
    # 一管一标：同一构件上有多个标记，只留离构件中心最近的那个，其余当重复标记（可删除）
    by_el = {}
    for x in items:
        by_el.setdefault(x['own'], []).append(x)
    dups, keep = [], []
    for eid, xs in by_el.items():
        xs.sort(key=lambda x: math.hypot(x['head'].X - x['a'].X, x['head'].Y - x['a'].Y))
        keep.append(xs[0])
        dups.extend(y['t'] for y in xs[1:])
    items = keep
    # 标记朝向与管道平行：沿 Y 的竖向，其余横向（Revit 标记只有横/竖两种）
    for x in items:
        x['orient'] = None
        loc = x['el'].Location
        if isinstance(loc, DB.LocationCurve):
            dd = loc.Curve.GetEndPoint(1) - loc.Curve.GetEndPoint(0)
            x['orient'] = DB.TagOrientation.Vertical if abs(dd.Y) > abs(dd.X) else DB.TagOrientation.Horizontal
            x['axis'] = 'v' if abs(dd.Y) > abs(dd.X) else 'h'
        else:
            x['axis'] = None
    ob, n_ob = build_obstacles(view)
    # 多构件标记不动，当作固定障碍
    fixed = []
    for t in multi:
        try:
            bb = t.get_BoundingBox(view)
            if bb is not None:
                fixed.append((bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y))
        except Exception:
            pass
    eid2route, routes = build_routes(ob)
    assign_sides(routes, cfg['bundle_gap_m'] * 1000.0 / FT)
    for x in items:
        ri = eid2route.get(x['own'])
        x['side'] = None
        if ri is not None:
            r = routes[ri]
            sd = r['side']
            # 只有标记所在管段的方向和路由方向一致，才按路由的侧
            if sd is not None and x.get('axis') == r['axis']:
                x['side'] = sd
    n_sizes = measure_tag_heads(view, items, m(cfg['text_w_mm']), m(cfg['text_h_mm']))
    items.sort(key=lambda x: (round(x['a'].Y, 1), round(x['a'].X, 1)))
    placed = list(fixed)

    def free(rc, skip_own=None):
        if ob.hits(rc, gap, None) > 0:
            return False
        for p in placed:
            if rect_overlap(grow(rc, gap), p):
                return False
        return True

    for x in items:
        w, h, ox, oy = x['size']
        a = x['a']
        base = max(w, h) / 2.0 + gap
        # 优先垂直于管道的两个方向（横管放上下、竖管放左右），其次斜向
        side = x.get('side')
        if side is not None:
            hw = _half_width(x['el'])
            ext = (w / 2.0) if side[0] != 0 else (h / 2.0)
            base = hw + ext + gap + m(cfg['side_offset_mm'])
        best = None
        k = 0
        r = base
        limit = max(max_len, base)
        # 标记头很宽时（如 123 mm），搜索半径至少覆盖一个头宽
        limit = max(limit, base + w)
        while r <= limit + 1e-9:
            for dx, dy in DIRS:
                n = math.hypot(dx, dy)
                cx, cy = a.X + dx / n * r, a.Y + dy / n * r
                rc = rect(cx, cy, w, h)
                c_obs = ob.hits(rc, gap, None)
                c_tag = sum(1 for p in placed if rect_overlap(grow(rc, gap), p))
                conf = c_obs + c_tag
                perp_ok = x.get('axis') is None or (x['axis'] == 'h' and abs(dx) < 0.3) or (x['axis'] == 'v' and abs(dy) < 0.3)
                side_pen = 0.0
                if side is not None:
                    side_pen = 0.0 if (abs(dx - side[0]) < 0.05 and abs(dy - side[1]) < 0.05) else 6.0
                score = conf * 1000 + k + (0.5 if (dx and dy) else 0.0) + (0.0 if perp_ok else 3.0) + side_pen
                if best is None or score < best[0]:
                    best = (score, cx, cy, conf, dx, dy)
            if best is not None and best[3] == 0:
                break
            k += 1
            r = base + k * step
        _, cx, cy, conf, dx, dy = best
        x['c'] = (cx, cy)
        x['dir'] = (dx, dy)
        x['conf'] = conf
        placed.append(rect(cx, cy, w, h))
        x['rect_idx'] = len(placed) - 1
    # 平齐：上下方向的对齐同一列（x），左右方向的对齐同一行（y）；吸附后重新检查，压到东西就不吸附
    shift_max = m(15.0)
    n_snapped = 0
    for axis in ('x', 'y'):
        if axis == 'x':
            grp = [x for x in items if x.get('axis') == 'v' or (x.get('axis') is None and abs(x['dir'][0]) < 0.3)]
            key = lambda x: x['c'][0]
        else:
            grp = [x for x in items if x.get('axis') == 'h' or (x.get('axis') is None and abs(x['dir'][1]) < 0.3)]
            key = lambda x: x['c'][1]
        grp.sort(key=key)
        clusters, cur = [], []
        for x in grp:
            if cur and abs(key(x) - key(cur[-1])) > tol:
                clusters.append(cur)
                cur = []
            cur.append(x)
        if cur:
            clusters.append(cur)
        for cl in clusters:
            if len(cl) < 2:
                continue
            # 基准：组里离自己构件最近的那个标记所在的行/列；其它标记往它靠，只允许靠近构件或持平，不允许被推得更远
            ref_y = min(cl, key=lambda q: math.hypot(q['c'][0] - q['a'].X, q['c'][1] - q['a'].Y))
            target = key(ref_y)
            for y in cl:
                cx, cy = y['c']
                ncx, ncy = (target, cy) if axis == 'x' else (cx, target)
                if abs(ncx - cx) + abs(ncy - cy) < 1e-9 or abs(ncx - cx) + abs(ncy - cy) > shift_max:
                    continue
                if math.hypot(ncx - y['a'].X, ncy - y['a'].Y) > max_len:
                    continue
                if math.hypot(ncx - y['a'].X, ncy - y['a'].Y) > math.hypot(cx - y['a'].X, cy - y['a'].Y) + m(3.0):
                    continue
                w, h = y['size'][0], y['size'][1]
                rc = rect(ncx, ncy, w, h)
                if ob.hits(rc, gap, None) > 0:
                    continue
                old = placed[y['rect_idx']]
                placed[y['rect_idx']] = (0, 0, 0, 0)
                ok = all(not rect_overlap(grow(rc, gap), p) for p in placed if p != (0, 0, 0, 0))
                if ok:
                    placed[y['rect_idx']] = rc
                    y['c'] = (ncx, ncy)
                    n_snapped += 1
                else:
                    placed[y['rect_idx']] = old
    # 头位置 = 包围框中心 − 偏移
    for x in items:
        w, h, ox, oy = x['size']
        x['new'] = (x['c'][0] - ox, x['c'][1] - oy)
    stat = measure_result(items, ob, placed, gap, p_mm, cfg)
    stat.update({'routes': routes, 'items': items, 'dups': dups, 'orphans': orphans, 'multi': len(multi), 'scale': scale, 'ob': n_ob, 'snapped': n_snapped, 'sizes': n_sizes})
    return stat


def measure_result(items, ob, placed, gap, p_mm, cfg):
    over_el = 0
    for x in items:
        w, h = x['size'][0], x['size'][1]
        if ob.hits(rect(x['c'][0], x['c'][1], w, h), 0.0, None) > 0:
            over_el += 1
    rects = [rect(x['c'][0], x['c'][1], x['size'][0], x['size'][1]) for x in items]
    over_tag = sum(1 for i in range(len(rects)) for j in range(i + 1, len(rects)) if rect_overlap(rects[i], rects[j]))
    far = sum(1 for x in items if p_mm(math.hypot(x['c'][0] - x['a'].X, x['c'][1] - x['a'].Y)) > cfg['max_leader_mm'] + 1e-6)
    return {'over_el': over_el, 'over_tag': over_tag, 'far': far}


def missing_elements(view, cfg, items):
    """视图里同类别已经有标记、但自己没标的构件：[(el, 标记类型Id)]。"""
    tagged = set(x['own'] for x in items)
    cat_type = {}
    for x in items:
        e = x['el']
        try:
            cid = e.Category.Id.Value
        except Exception:
            continue
        cat_type.setdefault(cid, {}).setdefault(x['t'].GetTypeId().Value, 0)
        cat_type[cid][x['t'].GetTypeId().Value] += 1
    out = []
    min_len = cfg['min_pipe_mm'] / FT
    for cid, tmap in cat_type.items():
        tt = max(tmap.items(), key=lambda kv: kv[1])[0]
        col = DB.FilteredElementCollector(doc, view.Id).OfCategoryId(DB.ElementId(cid)).WhereElementIsNotElementType()
        for e in col:
            if e.Id.Value in tagged:
                continue
            loc = e.Location
            if loc is None:
                continue
            if isinstance(loc, DB.LocationCurve) and loc.Curve.Length < min_len:
                continue
            out.append((e, DB.ElementId(tt)))
    return out


def verify(items, tol_ft=1.0 / FT):
    """回读：引线末端必须在自己构件的中心（偏差 ≤ 1 mm）。返回不合格的标记 id。"""
    bad = []
    for x in items:
        try:
            le = x['t'].GetLeaderEnd(x['ref'])
        except Exception:
            bad.append(x['t'].Id.Value)
            continue
        if math.hypot(le.X - x['a'].X, le.Y - x['a'].Y) > tol_ft:
            bad.append(x['t'].Id.Value)
    return bad


def apply(view, pl, cfg, add_list):
    t = DB.Transaction(doc, '天河落 · 构件标记一键整理')
    fh = t.GetFailureHandlingOptions()
    fh.SetFailuresPreprocessor(_Fails())
    t.SetFailureHandlingOptions(fh)
    t.Start()
    try:
        n_del = 0
        if cfg['delete_orphans'] and pl['orphans']:
            doc.Delete(List[DB.ElementId]([o.Id for o in pl['orphans']]))
            n_del = len(pl['orphans'])
        n_dup = 0
        if cfg['delete_orphans'] and pl['dups']:
            doc.Delete(List[DB.ElementId]([o.Id for o in pl['dups']]))
            n_dup = len(pl['dups'])
        for x in pl['items']:
            tg = x['t']
            if x.get('orient') is not None:
                try:
                    tg.TagOrientation = x['orient']
                except Exception:
                    pass
            tg.HasLeader = True
            try:
                tg.LeaderEndCondition = DB.LeaderEndCondition.Free
            except Exception:
                pass
            tg.TagHeadPosition = DB.XYZ(x['new'][0], x['new'][1], x['head'].Z)
            tg.SetLeaderEnd(x['ref'], DB.XYZ(x['a'].X, x['a'].Y, x['head'].Z))
        doc.Regenerate()
        n_add = 0
        if cfg['add_missing'] and add_list:
            for e, ttid in add_list:
                try:
                    p = center_of(e)
                    if p is None:
                        continue
                    DB.IndependentTag.Create(doc, ttid, view.Id, DB.Reference(e), True, DB.TagOrientation.Horizontal, DB.XYZ(p.X, p.Y, p.Z))
                    n_add += 1
                except Exception:
                    continue
        bad = verify(pl['items'])
        if bad:
            raise Exception('回读核对失败：%d 个标记的引线末端没落在自己构件的中心（如 %s），整次回滚，模型未改。' % (len(bad), bad[:3]))
        st = t.Commit()
        if st != DB.TransactionStatus.Committed:
            raise Exception('事务没有提交（%s），模型未改。' % st)
        return n_del + n_dup, n_add
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise


def main():
    try:
        view = uidoc.ActiveView
        if not isinstance(view, DB.ViewPlan):
            forms.alert('请在平面视图里运行（当前是「%s」）。' % view.Name, title=TITLE)
            return
        pick = forms.CommandSwitchWindow.show(['整理当前视图的构件标记', '⚙ 设置…'], message=TITLE)
        if not pick:
            return
        if pick.startswith('⚙'):
            if open_settings():
                forms.alert('设置已保存，下次运行生效。', title=TITLE)
            return
        cfg = load()
        pl = plan(view, cfg)
        if not pl['items'] and not pl['orphans']:
            forms.alert('当前视图里没有单构件标记可整理。', title=TITLE)
            return
        add_list = missing_elements(view, cfg, pl['items']) if cfg['add_missing'] else []
        msg = ('当前视图（比例 1:%d）：\n'
               '  单构件标记 %d 个：引线末端设到构件中心，标记头选空的一侧、不压任何构件、同侧平齐（已吸附 %d 个）\n'
               '  悬空标记 %d 个、同一构件上的重复标记 %d 个%s\n'
               '  缺标构件 %d 个%s\n'
               '  多构件标记 %d 个（不动，当作障碍）\n\n'
               '整理后预计：压到构件的标记 %d 个；标记互相重叠 %d 对；引线超过上限 %d 个。\n'
               '整次一个事务，写入后回读核对，Ctrl+Z 可撤销。执行？') % (
            pl['scale'], len(pl['items']), pl['snapped'], len(pl['orphans']), len(pl['dups']), '（将删除）' if cfg['delete_orphans'] else '（不删）',
            len(add_list), '（将补标）' if cfg['add_missing'] else '（设置里勾选才补标）', pl['multi'],
            pl['over_el'], pl['over_tag'], pl['far'])
        if not forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
            return
        n_del, n_add = apply(view, pl, cfg, add_list)
        forms.alert('完成：整理 %d 个标记，删除悬空 %d 个，补标 %d 个。Ctrl+Z 可整次撤销。' % (len(pl['items']), n_del, n_add), title=TITLE)
    except Exception:
        forms.alert('出错了（已回滚，模型没被改）：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
