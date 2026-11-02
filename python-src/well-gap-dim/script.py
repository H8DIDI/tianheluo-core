# -*- coding: utf-8 -*-
__title__ = '井间距自动标注'
__doc__ = ('在当前平面视图里，自动给「同一条排管路由上相邻的两口井」标注井中心到中心的距离：\n'
           '先按排管的连接关系找出同一条排管上相邻的井（排管是一段一段的，端点相接、走向基本不拐的连成一条，到井为止；拐到别的支线上的井不算），'
           '同一直线上连续的井合并成一条连续标注，参照的是井族自带的「中心（左/右）」「中心（前/后）」参照平面，数值是准的、井挪了会跟着变。\n'
           '视图里已经手工标好且正确的不重复标；标错的（参照的不是井中心、两口井之间没有排管连接、同一口井参了两次、标注文字被手改）可以一并删掉重标。'
           '先在窗口里列出「要删什么、要新增什么」，确认后一次执行，整次一个事务，Ctrl+Z 可撤销。\n'
           '另有「排管横向中心距标注」：相邻、平行并排的排管，在每根排管的转折、端头处，把并排排管的横向中心距沿排管自带的中心线连续标出（间距上限、并行长度、内缩、长段补标间隔可在设置里改）。')

import re
import math
import traceback
from pyrevit import revit, DB, UI, forms

# @include _shared/form_fit.py
# @include _shared/ws_guard.py
# @include _shared/well_config.py
# @include _shared/settings_ui.py
import io
import os
import json
import clr
import System
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import System.Windows.Forms as WF
import System.Drawing as SD

doc = revit.doc
uidoc = revit.uidoc
WCFG = wcfg_load()
TITLE = u'井间距自动标注'
FT = 304.8
RT = DB.FamilyInstanceReferenceType
BANK_PREFIXES = (u'TELCO', u'ELV', u'LV-', u'ELE-')          # 排管族名前缀（雨水沟不是排管）
LINK_TOL = 0.3 / 0.3048          # 排管两段首尾相接的容差（英尺，约 0.3 m）
WELL_TOL = 0.15 / 0.3048         # 排管端点落进井外框的容差
ANG_COS = math.cos(math.radians(3.0))        # 井的中心面和标注方向差 3° 以内算平行
CHORD_COS = math.cos(math.radians(5.0))      # 两井连线和标注方向差 5° 以内算「基本在一条线上」
STRAIGHT_COS = math.cos(math.radians(10.0))      # 排管接点处拐角不超过 10° 才算同一条直排管
CLUSTER_DZ = 1.0 / 0.3048        # 端点相接时，高差不超过 1 m 才算同一接点


def _idv(i):
    try:
        return i.Value
    except Exception:
        return i.IntegerValue


def _mark(e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        return (p.AsString() or u'').strip() if p is not None else u''
    except Exception:
        return u''


def _is_bank(e):
    try:
        f = doc.GetElement(e.GetTypeId()).Family.Name.upper()
    except Exception:
        return False
    return any(f.startswith(k) for k in BANK_PREFIXES)


def collect_wells(view):
    """视图里的井：{id: 信息}。井 = 常规模型族实例，标记属于配置里的某个井系列。"""
    wells = {}
    col = DB.FilteredElementCollector(doc, view.Id).OfCategory(DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType()
    for e in col:
        if not isinstance(e, DB.FamilyInstance):
            continue
        mk = _mark(e)
        if not mk or wcfg_series_of(mk, WCFG, True) is None:
            continue
        bb = e.get_BoundingBox(None)
        loc = e.Location
        if bb is None or not isinstance(loc, DB.LocationPoint):
            continue
        p = loc.Point
        wells[_idv(e.Id)] = {'e': e, 'mark': mk, 'c': (p.X, p.Y, p.Z), 'bb': (bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y),
                             'hand': (e.HandOrientation.X, e.HandOrientation.Y), 'face': (e.FacingOrientation.X, e.FacingOrientation.Y)}
    return wells


def collect_banks():
    out = []
    for e in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType():
        if isinstance(e.Location, DB.LocationCurve) and _is_bank(e):
            out.append(e)
    return out


def build_edges(wells, banks):
    """排管路由图 → 相邻的井对。返回 {(小id, 大id)}。
    排管是一段一段的：端点在 0.3 m 内相接的并成一个接点；端点落在井外框里的算到井上。从每口井出发沿排管走，走到下一口井为止，就是相邻。"""
    pts = []
    for b in banks:
        c = b.Location.Curve
        pts.append(c.GetEndPoint(0))
        pts.append(c.GetEndPoint(1))
    parent = list(range(len(pts)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    grid = {}
    for i, pt in enumerate(pts):
        gx, gy = int(math.floor(pt.X / LINK_TOL)), int(math.floor(pt.Y / LINK_TOL))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), ()):
                    q = pts[j]
                    if (q.X - pt.X) ** 2 + (q.Y - pt.Y) ** 2 <= LINK_TOL * LINK_TOL and abs(q.Z - pt.Z) <= CLUSTER_DZ:
                        parent[find(i)] = find(j)
        grid.setdefault((gx, gy), []).append(i)

    def well_at(pt):
        best = None
        for k, w in wells.items():
            x0, y0, x1, y1 = w['bb']
            if x0 - WELL_TOL <= pt.X <= x1 + WELL_TOL and y0 - WELL_TOL <= pt.Y <= y1 + WELL_TOL:
                dd = (pt.X - w['c'][0]) ** 2 + (pt.Y - w['c'][1]) ** 2
                if best is None or dd < best[0]:
                    best = (dd, k)
        return best[1] if best else None
    adj = {}
    for bi in range(len(banks)):
        vs = []
        for k in (0, 1):
            i = bi * 2 + k
            w = well_at(pts[i])
            vs.append(('w', w) if w is not None else ('c', find(i)))
        c = banks[bi].Location.Curve
        d = c.GetEndPoint(1) - c.GetEndPoint(0)
        L = math.hypot(d.X, d.Y)
        if L < 1e-6:
            continue
        u = (d.X / L, d.Y / L)
        adj.setdefault(vs[0], []).append((vs[1], u))
        adj.setdefault(vs[1], []).append((vs[0], (-u[0], -u[1])))
    # 只沿「直通」的排管走：到接点后，只接着走方向基本不拐（10° 内）的那一段。
    # 这样同一条排管上相邻的井才算相邻；拐到别的支线、绕过三通去到别的排管上的井不算（那种标注是错的）。
    edges = set()
    for wk in wells:
        start = ('w', wk)
        seen = set([start])
        stack = [(n, u) for n, u in adj.get(start, [])]
        while stack:
            n, din = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if n[0] == 'w':
                if n[1] != wk:
                    edges.add((min(wk, n[1]), max(wk, n[1])))
                continue
            for n2, u2 in adj.get(n, []):
                if _dot(din, u2) >= STRAIGHT_COS:
                    stack.append((n2, u2))
    return edges


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _norm_axis(ax):
    """方向取正：x 为正；x≈0 时 y 为正。"""
    if ax[0] < -1e-9 or (abs(ax[0]) <= 1e-9 and ax[1] < 0):
        return (-ax[0], -ax[1])
    return ax


def _center_ref(w, axis):
    """井在 axis 方向上的中心参照面：该方向正好垂直于参照面法向。找不到返回 None。"""
    e = w['e']
    if abs(_dot(w['hand'], axis)) >= ANG_COS:
        refs = e.GetReferences(RT.CenterLeftRight)
    elif abs(_dot(w['face'], axis)) >= ANG_COS:
        refs = e.GetReferences(RT.CenterFrontBack)
    else:
        return None
    return refs[0] if refs is not None and refs.Count > 0 else None


def edge_axis(wa, wb):
    """这一对井能标注的方向（单位向量，取正）；标不了返回 None。
    优先用 X、Y 轴（连线偏差 5° 内），其次用井自己的轴向；而且两口井在这个方向上都得有中心参照面。"""
    dx, dy = wb['c'][0] - wa['c'][0], wb['c'][1] - wa['c'][1]
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return None
    u = (dx / L, dy / L)
    cands = [(1.0, 0.0), (0.0, 1.0)]
    for w in (wa, wb):
        cands.append(w['hand'])
        cands.append(w['face'])
    for ax in cands:
        if abs(_dot(u, ax)) >= CHORD_COS:
            ax = _norm_axis(ax)
            if _center_ref(wa, ax) is not None and _center_ref(wb, ax) is not None:
                return ax
    return None


def _normals_parallel(wells, ids, ax):
    """这几口井在 ax 方向上用的中心参照面，法向是否严格平行（Revit 要求连续标注的参照面平行，差 0.01° 以上就会建失败/被删）。"""
    ns = []
    for i in ids:
        w = wells[i]
        ns.append(w['hand'] if abs(_dot(w['hand'], ax)) >= ANG_COS else w['face'])
    n0 = ns[0]
    for v in ns[1:]:
        if abs(n0[0] * v[1] - n0[1] * v[0]) > 2e-4:
            return False
    return True


def _akey(ax):
    return (round(ax[0], 3), round(ax[1], 3))


def stable(ref):
    try:
        return ref.ConvertToStableRepresentation(doc)
    except Exception:
        return None


def existing_dims(view, wells):
    """视图里「只参照井」的标注：[{dim, wells[id...], 方向, 问题[]}]。参照里有别的构件（墙、轴线…）的不碰。"""
    out = []
    stable_ok = {}
    for k, w in wells.items():
        s = set()
        for t in (RT.CenterLeftRight, RT.CenterFrontBack):
            for r in w['e'].GetReferences(t):
                s.add(stable(r))
        stable_ok[k] = s
    for dm in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Dimension):
        try:
            refs = list(dm.References)
        except Exception:
            continue
        if len(refs) < 2:
            continue
        ids = [_idv(r.ElementId) for r in refs]
        els = [doc.GetElement(r.ElementId) for r in refs]
        if not all(isinstance(e, DB.FamilyInstance) and wcfg_series_of(_mark(e), WCFG, True) is not None for e in els):
            continue                                     # 有别的构件：不是井间标注
        if not all(i in wells for i in ids):
            continue                                     # 参照的井不在本视图范围内：看不全，不动
        try:
            ln = dm.Curve
            d = _norm_axis((ln.Direction.X, ln.Direction.Y))
        except Exception:
            continue
        problems = []
        if len(set(ids)) < len(ids):
            problems.append(u'同一口井参照了两次')
        if any(stable(r) not in stable_ok[i] for r, i in zip(refs, ids)):
            problems.append(u'参照的不是井中心面')
        try:
            if dm.NumberOfSegments and dm.NumberOfSegments > 0:
                ov = any((dm.Segments.get_Item(i).ValueOverride or u'') for i in range(dm.NumberOfSegments))
            else:
                ov = bool(dm.ValueOverride)
            if ov:
                problems.append(u'标注文字被手动改过')
        except Exception:
            pass
        order = sorted(set(ids), key=lambda i: _dot(wells[i]['c'], d))
        out.append({'dim': dm, 'ids': order, 'axis': d, 'problems': problems})
    return out


def _pair(a, b):
    return (min(a, b), max(a, b))


def make_plan(view, wells, edges, dims, off_ft):
    """规划：保留/删除哪些旧标注，新增哪些。返回 dict。"""
    plan = {'keep': [], 'delete': [], 'create': [], 'skip': [], 'covered': set()}
    plan['covered'].update(oblique_covered(view, wells))
    for D in dims:
        ps = [_pair(D['ids'][i], D['ids'][i + 1]) for i in range(len(D['ids']) - 1)]
        probs = list(D['problems'])
        if any(p not in edges for p in ps):
            probs.append(u'相邻两口井之间没有排管连接')
        if probs:
            plan['delete'].append((D, probs))
        else:
            plan['keep'].append(D)
            plan['covered'].update(ps)
    # 新增：没标过的相邻井对，按方向分组
    by_axis = {}
    for e in sorted(edges):
        if e in plan['covered']:
            continue
        wa, wb = wells[e[0]], wells[e[1]]
        ax = edge_axis(wa, wb)
        if ax is None:
            plan['create'].append({'kind': 'oblique', 'ids': [e[0], e[1]]})      # 斜向：沿两井连线标真实中心距
            continue
        by_axis.setdefault(_akey(ax), (ax, []))[1].append(e)
    for key, (ax, es) in by_axis.items():
        nb = {}
        for a, b in es:
            nb.setdefault(a, []).append(b)
            nb.setdefault(b, []).append(a)
        seen = set()
        # 沿方向连续的井合并成链：每口井最多 2 个邻居、且沿方向单调
        for start in sorted(nb, key=lambda i: _dot(wells[i]['c'], ax)):
            if start in seen:
                continue
            comp, stack = [], [start]
            seen.add(start)
            while stack:
                x = stack.pop()
                comp.append(x)
                for y in nb[x]:
                    if y not in seen:
                        seen.add(y)
                        stack.append(y)
            order = sorted(comp, key=lambda i: _dot(wells[i]['c'], ax))
            chain = all(len(nb[i]) <= 2 for i in comp) and all(_pair(order[i], order[i + 1]) in set(_pair(a, b) for a, b in es) for i in range(len(order) - 1))
            if chain and len(order) >= 2:
                items = [{'ax': ax, 'ids': order}]
            else:
                items = []
                for a in comp:
                    for b in nb[a]:
                        if a < b:
                            items.append({'ax': ax, 'ids': sorted([a, b], key=lambda i: _dot(wells[i]['c'], ax))})
            for it in items:
                if _normals_parallel(wells, it['ids'], ax):
                    plan['create'].append(it)
                else:                       # 井有点转角（0.01° 以上），中心面不严格平行：改成沿连线标，每相邻两口井一条
                    for i in range(len(it['ids']) - 1):
                        plan['create'].append({'kind': 'oblique', 'ids': [it['ids'][i], it['ids'][i + 1]]})
    return plan


class _Fails(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        for m in fa.GetFailureMessages():
            if m.GetSeverity() == DB.FailureSeverity.Warning:
                fa.DeleteWarning(m)
        return DB.FailureProcessingResult.Continue


AUX_NOTE = u'井间距辅助线（自动）'


def _invisible_style():
    try:
        cats = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines).SubCategories
        for c in cats:
            if c.Name in (u'<不可见线>', u'<隐藏>', u'<Invisible lines>', u'<Hidden>'):      # 中文版里「不可见线」叫 <隐藏>；<隐藏线> 是另一种（虚线）
                return c.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
    except Exception:
        pass
    return None


def oblique_covered(view, wells):
    """视图里已经做过的斜向标注（辅助线两端各落在一口井中心上）覆盖的井对。"""
    out = set()
    inv = _invisible_style()
    for dc in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.CurveElement):
        try:
            if not isinstance(dc, DB.DetailCurve):
                continue
            if inv is not None and dc.LineStyle is not None and dc.LineStyle.Id != inv.Id:
                continue                                     # 辅助线用的是「不可见线」样式；别的详图线不碰
            c = dc.GeometryCurve
            ends = []
            for k in (0, 1):
                q = c.GetEndPoint(k)
                best = None
                for i, w in wells.items():
                    dd = math.hypot(q.X - w['c'][0], q.Y - w['c'][1])
                    if dd < 0.2 / 0.3048 and (best is None or dd < best[0]):
                        best = (dd, i)
                ends.append(best[1] if best else None)
            if None not in ends and ends[0] != ends[1]:
                out.add(_pair(ends[0], ends[1]))
        except Exception:
            continue
    return out


def create_oblique(view, wells, item, dtype, off_ft):
    """斜向：在两口井中心之间画一条不可见的辅助详图线，沿它标注（真实的中心到中心距离）。井挪了要重跑本插件更新。"""
    a, b = item['ids']
    ca, cb = wells[a]['c'], wells[b]['c']
    z = _view_z(view, wells, item['ids'])
    p0, p1 = DB.XYZ(ca[0], ca[1], z), DB.XYZ(cb[0], cb[1], z)
    try:
        dc = doc.Create.NewDetailCurve(view, DB.Line.CreateBound(p0, p1))
    except Exception as ex:
        return None, (u'%s' % ex).splitlines()[0][:80]
    st = _invisible_style()
    if st is not None:
        try:
            dc.LineStyle = st
        except Exception:
            pass
    try:
        dc.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).Set(AUX_NOTE)
    except Exception:
        pass
    ln = dc.GeometryCurve
    arr = DB.ReferenceArray()
    arr.Append(ln.GetEndPointReference(0))
    arr.Append(ln.GetEndPointReference(1))
    L = math.hypot(cb[0] - ca[0], cb[1] - ca[1])
    u = ((cb[0] - ca[0]) / L, (cb[1] - ca[1]) / L)
    perp = (-u[1], u[0])
    q0 = DB.XYZ(p0.X + perp[0] * off_ft, p0.Y + perp[1] * off_ft, z)
    q1 = DB.XYZ(p1.X + perp[0] * off_ft, p1.Y + perp[1] * off_ft, z)
    try:
        dm = doc.Create.NewDimension(view, DB.Line.CreateBound(q0, q1), arr, dtype) if dtype is not None else doc.Create.NewDimension(view, DB.Line.CreateBound(q0, q1), arr)
    except Exception as ex:
        doc.Delete(dc.Id)
        return None, (u'%s' % ex).splitlines()[0][:80]
    return dm, None


def _view_z(view, wells, ids):
    try:
        return view.GenLevel.ProjectElevation
    except Exception:
        return wells[ids[0]]['c'][2]


def create_dim(view, wells, item, dtype, off_ft):
    """按一条规划建标注。返回 (标注, 错误说明)。"""
    if item.get('kind') == 'oblique':
        return create_oblique(view, wells, item, dtype, off_ft)
    ax, ids = item['ax'], item['ids']
    perp = (-ax[1], ax[0])
    s = [_dot(wells[i]['c'], ax) for i in ids]
    t = max(_dot(wells[i]['c'], perp) for i in ids) + off_ft
    z = _view_z(view, wells, ids)
    p0 = DB.XYZ(ax[0] * min(s) + perp[0] * t, ax[1] * min(s) + perp[1] * t, z)
    p1 = DB.XYZ(ax[0] * max(s) + perp[0] * t, ax[1] * max(s) + perp[1] * t, z)
    arr = DB.ReferenceArray()
    for i in ids:
        arr.Append(_center_ref(wells[i], ax))
    try:
        dm = doc.Create.NewDimension(view, DB.Line.CreateBound(p0, p1), arr, dtype) if dtype is not None else doc.Create.NewDimension(view, DB.Line.CreateBound(p0, p1), arr)
    except Exception as ex:
        return None, (u'%s' % ex).splitlines()[0][:80]
    return dm, None


def _dim_ok(dm, item, wells):
    """标注是否有效、数值是否等于井中心距（轴向：中心在标注方向上的距离；斜向：连线真实距离）。"""
    try:
        if not dm.IsValidObject:
            return False
        if item.get('kind') == 'oblique':
            ca, cb = wells[item['ids'][0]]['c'], wells[item['ids'][1]]['c']
            return abs(dm.Value - math.hypot(cb[0] - ca[0], cb[1] - ca[1])) <= 0.002
        s = [_dot(wells[i]['c'], item['ax']) for i in item['ids']]
        want = sorted(abs(s[i + 1] - s[i]) for i in range(len(s) - 1))
        if dm.NumberOfSegments and dm.NumberOfSegments > 0:
            got = sorted(dm.Segments.get_Item(i).Value for i in range(dm.NumberOfSegments))
        else:
            got = [dm.Value]
        return len(got) == len(want) and all(abs(a - b) <= 0.002 for a, b in zip(want, got))
    except Exception:
        return False


def apply_plan(view, wells, plan, dtype, off_ft, do_delete, do_create):
    """一个事务：先删不对的旧标注，再新建。返回 (删了几个, 新建几条, 失败 [(井号们, 原因)], 数值校验不符数)。"""
    t = DB.Transaction(doc, u'井间距自动标注')
    fho = t.GetFailureHandlingOptions()
    fho.SetFailuresPreprocessor(_Fails())
    t.SetFailureHandlingOptions(fho)
    t.Start()
    try:
        ndel = 0
        if do_delete and plan['delete']:
            ok, blocked = wg_split(doc, [D['dim'] for D, _ in plan['delete']])
            for e in ok:
                doc.Delete(e.Id)
                ndel += 1
        made, fails, badval = 0, [], 0
        made_dims = []
        if do_create:
            for item in plan['create']:
                dm, err = create_dim(view, wells, item, dtype, off_ft)
                names = u'-'.join(wells[i]['mark'] for i in item['ids'])
                if dm is None:
                    fails.append((names, err))
                    continue
                made_dims.append((dm, item))
            doc.Regenerate()
            retry = []
            for dm, item in made_dims:
                if _dim_ok(dm, item, wells):
                    made += 1
                    continue
                try:                                   # 建出来的数值不对或已失效：删掉，改成沿连线标
                    if dm.IsValidObject:
                        doc.Delete(dm.Id)
                except Exception:
                    pass
                if item.get('kind') == 'oblique':
                    fails.append((u'-'.join(wells[i]['mark'] for i in item['ids']), u'标注数值不对'))
                else:
                    for i in range(len(item['ids']) - 1):
                        retry.append({'kind': 'oblique', 'ids': [item['ids'][i], item['ids'][i + 1]]})
            retry_dims = []
            for item in retry:
                dm, err = create_dim(view, wells, item, dtype, off_ft)
                if dm is None:
                    fails.append((u'-'.join(wells[i]['mark'] for i in item['ids']), err))
                else:
                    retry_dims.append((dm, item))
            if retry_dims:
                doc.Regenerate()
            for dm, item in retry_dims:
                if _dim_ok(dm, item, wells):
                    made += 1
                else:
                    badval += 1
                    fails.append((u'-'.join(wells[i]['mark'] for i in item['ids']), u'标注数值对不上，已保留请检查'))
        st = t.Commit()
        if st != DB.TransactionStatus.Committed:
            raise Exception(u'事务没有提交成功（%s），什么都没改' % st)
        return ndel, made, fails, badval
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise


def _dim_types():
    out = []
    for dt in DB.FilteredElementCollector(doc).OfClass(DB.DimensionType):
        try:
            if dt.StyleType == DB.DimensionStyleType.Linear:
                out.append(dt)
        except Exception:
            pass
    return out


def _default_type(dims, types):
    cnt = {}
    for D in dims:
        k = _idv(D['dim'].GetTypeId())
        cnt[k] = cnt.get(k, 0) + 1
    if cnt:
        best = max(cnt, key=lambda k: cnt[k])
        for t in types:
            if _idv(t.Id) == best:
                return t
    for t in types:
        if u'标注' in DB.Element.Name.GetValue(t):
            return t
    return types[0] if types else None


def _names(wells, ids):
    return u' — '.join(wells[i]['mark'] for i in ids)


def show_dialog(view, wells, plan, types, dtype0, edges):
    """预览窗口：列出要删/要新增/没法标的，选标注类型和偏移，确认。返回 dict 或 None。"""
    f = WF.Form()
    f.Text = TITLE
    f.TopMost = True
    f.StartPosition = WF.FormStartPosition.CenterScreen
    f.ClientSize = SD.Size(860, 600)
    f.MinimumSize = SD.Size(860, 600)
    f.Font = SD.Font(u'Microsoft YaHei UI', 9.0)
    A = WF.AnchorStyles
    head = WF.Label()
    nch = sum(1 for c in plan['create'] if len(c['ids']) > 2)
    nob = sum(1 for c in plan['create'] if c.get('kind') == 'oblique')
    head.Text = (u'视图「%s」：井 %d 口，排管路由上相邻的井 %d 对。已有正确标注 %d 条（覆盖 %d 对，不重复标）；不对的旧标注 %d 条；'
                 u'新增标注 %d 条（其中连续标注 %d 条、斜向 %d 条）。'
                 % (view.Name, len(wells), len(edges), len(plan['keep']), len(plan['covered']), len(plan['delete']),
                    len(plan['create']), nch, nob))
    head.Location = SD.Point(12, 8)
    head.Size = SD.Size(836, 54)
    head.Anchor = A.Top | A.Left | A.Right
    f.Controls.Add(head)
    lv = WF.ListView()
    lv.View = WF.View.Details
    lv.FullRowSelect = True
    lv.GridLines = True
    lv.CheckBoxes = True
    lv.Location = SD.Point(12, 66)
    lv.Size = SD.Size(836, 360)
    lv.Anchor = A.Top | A.Left | A.Right | A.Bottom
    lv.Columns.Add(u'动作', 70)
    lv.Columns.Add(u'井（沿标注方向）', 330)
    lv.Columns.Add(u'说明', 420)
    for D, probs in plan['delete']:
        it = WF.ListViewItem(u'删除')
        it.Checked = True
        it.Tag = D
        it.SubItems.Add(_names(wells, D['ids']))
        it.SubItems.Add(u'；'.join(probs))
        lv.Items.Add(it)
    for c in plan['create']:
        if c.get('kind') == 'oblique':
            ca, cb = wells[c['ids'][0]]['c'], wells[c['ids'][1]]['c']
            it = WF.ListViewItem(u'新增')
            it.Checked = True
            it.Tag = c
            it.SubItems.Add(_names(wells, c['ids']))
            it.SubItems.Add(u'斜向，沿两井连线标真实中心距 %.2f m（带一条不可见辅助线）' % (math.hypot(cb[0] - ca[0], cb[1] - ca[1]) * FT / 1000.0))
            lv.Items.Add(it)
            continue
        s = [_dot(wells[i]['c'], c['ax']) for i in c['ids']]
        segs = u' + '.join(u'%.2f' % (abs(s[i + 1] - s[i]) * FT / 1000.0) for i in range(len(s) - 1))
        it = WF.ListViewItem(u'新增')
        it.Checked = True
        it.Tag = c
        it.SubItems.Add(_names(wells, c['ids']))
        it.SubItems.Add(u'中心距 %s m（%s）' % (segs, u'连续标注' if len(c['ids']) > 2 else u'单段'))
        lv.Items.Add(it)
    for e, why in plan['skip']:
        it = WF.ListViewItem(u'跳过')
        it.Tag = None
        it.SubItems.Add(_names(wells, list(e)))
        it.SubItems.Add(why)
        lv.Items.Add(it)
    f.Controls.Add(lv)

    def _lb(x, y, w, txt):
        l = WF.Label()
        l.Text = txt
        l.Location = SD.Point(x, y)
        l.Size = SD.Size(w, 20)
        l.Anchor = A.Bottom | A.Left
        f.Controls.Add(l)
    cdel = WF.CheckBox()
    cdel.Text = u'删除不对的旧标注（上面勾上的）'
    cdel.Location = SD.Point(12, 436)
    cdel.Size = SD.Size(300, 22)
    cdel.Checked = bool(plan['delete'])
    cdel.Enabled = bool(plan['delete'])
    cdel.Anchor = A.Bottom | A.Left
    f.Controls.Add(cdel)
    ccre = WF.CheckBox()
    ccre.Text = u'新增标注（上面勾上的）'
    ccre.Location = SD.Point(330, 436)
    ccre.Size = SD.Size(260, 22)
    ccre.Checked = bool(plan['create'])
    ccre.Enabled = bool(plan['create'])
    ccre.Anchor = A.Bottom | A.Left
    f.Controls.Add(ccre)
    _lb(12, 474, 90, u'标注类型')
    cb = WF.ComboBox()
    cb.DropDownStyle = WF.ComboBoxStyle.DropDownList
    cb.Location = SD.Point(104, 470)
    cb.Size = SD.Size(330, 24)
    cb.Anchor = A.Bottom | A.Left
    for t in types:
        cb.Items.Add(DB.Element.Name.GetValue(t))
    if dtype0 is not None:
        cb.SelectedItem = DB.Element.Name.GetValue(dtype0)
    f.Controls.Add(cb)
    _lb(460, 474, 190, u'标注线离井距离（图纸 mm）')
    tbo = WF.TextBox()
    tbo.Location = SD.Point(656, 470)
    tbo.Size = SD.Size(60, 24)
    tbo.Text = u'10'
    tbo.Anchor = A.Bottom | A.Left
    f.Controls.Add(tbo)
    note = WF.Label()
    note.Text = u'连续标注：同一直线上相邻的井合成一条，参照井族的「中心（左/右）」「中心（前/后）」面。斜向的沿两井连线标真实中心距（用一条不可见的辅助线，井挪了要重跑本插件）。标得不理想的可以在视图里拖动标注线。'
    note.Location = SD.Point(12, 506)
    note.Size = SD.Size(836, 40)
    note.Anchor = A.Bottom | A.Left | A.Right
    f.Controls.Add(note)
    res = {}
    ok = WF.Button()
    ok.Text = u'执行'
    ok.Size = SD.Size(110, 30)
    ok.Location = SD.Point(628, 556)
    ok.Anchor = A.Bottom | A.Right
    cc = WF.Button()
    cc.Text = u'取消'
    cc.Size = SD.Size(100, 30)
    cc.Location = SD.Point(748, 556)
    cc.Anchor = A.Bottom | A.Right
    cc.DialogResult = WF.DialogResult.Cancel
    f.Controls.Add(ok)
    f.Controls.Add(cc)
    f.CancelButton = cc
    f.AcceptButton = ok

    def _ok(s_, a_):
        try:
            mm = float(tbo.Text.strip())
        except Exception:
            WF.MessageBox.Show(u'偏移请输入数字（图纸上的毫米数，如 10）。', TITLE)
            return
        sel = None
        for t in types:
            if DB.Element.Name.GetValue(t) == cb.SelectedItem:
                sel = t
        res.update({'delete': cdel.Checked, 'create': ccre.Checked, 'dtype': sel, 'off': mm,
                    'del_dims': [it.Tag for it in lv.Items if it.Text == u'删除' and it.Checked],
                    'new_items': [it.Tag for it in lv.Items if it.Text == u'新增' and it.Checked]})
        f.DialogResult = WF.DialogResult.OK
        f.Close()
    ok.Click += _ok
    ff_fit(f)
    if f.ShowDialog() != WF.DialogResult.OK:
        return None
    return res



# ============================================================
# 排管横向中心距标注：相邻平行排管的中心线之间，横向间距
# ============================================================
BANK_SET = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), u'KaifaTools', u'井间距标注设置.json')
BANK_DEFAULTS = {
    u'说明': u'bank_gap_max_m：相邻两排管中心线最大间距（米），更远的不标；bank_overlap_min_m：两排管并行重叠至少多长（米）才标；'
           u'bank_inset_mm：标注线离排管转折/端头内缩多少（毫米）；bank_step_m：长直段中间每隔多少米再补一条（0=不补）；'
           u'bank_same_system：只在同一个系统的排管之间标（11KV、275KV、TELCO、ELV、LV 各算一个系统）；'
           u'bank_min_gap_mm：中心距小于这个值（几乎重合）不标。每根排管的两个端点（转折处）都会标。',
    u'bank_gap_max_m': 30.0,
    u'bank_same_system': True,
    u'bank_overlap_min_m': 0.3,
    u'bank_inset_mm': 600.0,
    u'bank_step_m': 30.0,
    u'bank_min_gap_mm': 50.0,
}


def bank_cfg():
    s = dict(BANK_DEFAULTS)
    try:
        if os.path.exists(BANK_SET):
            with io.open(BANK_SET, 'r', encoding='utf-8-sig') as f:
                s.update(json.load(f))
    except Exception:
        pass
    return s


def bank_open_settings():
    cur = bank_cfg()
    fields = [
        {'key': 'bank_gap_max_m', 'label': u'相邻排管最大中心距 (m)', 'kind': 'num', 'min': 0.1, 'max': 200,
         'hint': u'两排管中心线相距超过这个值就不标（默认 30 m，几米到十几米的并排都要标）。'},
        {'key': 'bank_same_system', 'label': u'只在同一系统的排管之间标', 'kind': 'bool',
         'hint': u'勾选：11KV 只和 11KV 标，TELCO 只和 TELCO 标……；不勾：相邻的排管不分系统都标。'},
        {'key': 'bank_overlap_min_m', 'label': u'并行重叠至少 (m)', 'kind': 'num', 'min': 0, 'max': 100,
         'hint': u'两排管并排走的长度不到这个值，不标（转折处的短臂也要标的话调小）。'},
        {'key': 'bank_inset_mm', 'label': u'转折处内缩 (mm)', 'kind': 'num', 'min': 0, 'max': 5000,
         'hint': u'每根排管的端点（转折处、到井处）都标一条，标注线离端点往里内缩这么多。'},
        {'key': 'bank_step_m', 'label': u'长段中间补标间隔 (m)', 'kind': 'num', 'min': 0, 'max': 500,
         'hint': u'两个转折之间超过这个长度，中间每隔这么远再补一条；0 = 只标转折处。'},
        {'key': 'bank_min_gap_mm', 'label': u'最小中心距 (mm)', 'kind': 'num', 'min': 0, 'max': 1000,
         'hint': u'中心距小于这个值（几乎重合）的不标。'},
    ]
    res = su_fields_dialog(TITLE + u' · 排管横向标注设置', u'改完点保存，下次运行生效。设置文件：' + BANK_SET, fields, cur, BANK_DEFAULTS,
                           extra_buttons=[(u'用记事本打开文件', lambda: su_open_file(BANK_SET))], width=680, height=420)
    if res is None:
        return False
    out = dict(BANK_DEFAULTS)
    out.update(cur)
    out.update(res)
    dd = os.path.dirname(BANK_SET)
    if not os.path.isdir(dd):
        os.makedirs(dd)
    with io.open(BANK_SET, 'w', encoding='utf-8') as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    return True


_SYS = re.compile(u'^(ELE-[0-9A-Za-z]+|TELCO|ELV|LV)', re.I)


def bank_system(e):
    """排管所属系统：族名开头的 ELE-11KV / ELE-275KV / TELCO / ELV / LV。取不到返回族名前缀。"""
    try:
        f = doc.GetElement(e.GetTypeId()).Family.Name
    except Exception:
        return u''
    m = _SYS.match(f.strip())
    return (m.group(1).upper() if m else f[:4].upper())


PAIR_IDX = re.compile(u':(\\d+):SURFACE$')


def bank_family(e):
    try:
        return doc.GetElement(e.GetTypeId()).Family.Name
    except Exception:
        return u''


def learned_index(view):
    """从视图里已有的「只参照排管」标注学：每个排管族平时用哪个表面参照编号（出现最多的那个）。返回 {族名: 编号}。"""
    ids = set()
    for e in DB.FilteredElementCollector(doc, view.Id).OfCategory(DB.BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType():
        if _is_bank(e):
            ids.add(_idv(e.Id))
    cnt = {}
    for dm in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Dimension):
        try:
            refs = list(dm.References)
        except Exception:
            continue
        if not refs or not all(_idv(r.ElementId) in ids for r in refs):
            continue
        for r in refs:
            m = PAIR_IDX.search(r.ConvertToStableRepresentation(doc) or u'')
            if m:
                k = bank_family(doc.GetElement(r.ElementId))
                cnt.setdefault(k, {})
                cnt[k][m.group(1)] = cnt[k].get(m.group(1), 0) + 1
    return dict((k, max(v.items(), key=lambda kv: kv[1])[0]) for k, v in cnt.items())


def pick_ref(b, idx_map):
    """这根排管按学到的编号取表面参照；没有学到或取不到返回 None。"""
    want = idx_map.get(b['fam'])
    if want is None:
        return None
    try:
        for x in b['e'].GetReferences(RT.WeakReference):
            m = PAIR_IDX.search(x.ConvertToStableRepresentation(doc) or u'')
            if m and m.group(1) == want:
                return x
    except Exception:
        return None
    return None


def lateral_pairs(ids, by_id):
    """一组排管按横向排好后，相邻两根组成的对（frozenset）。"""
    bs = [by_id[i] for i in ids if i in by_id]
    if len(bs) < 2:
        return []
    ax = bs[0]['t']
    nrm = (-ax[1], ax[0])
    bs.sort(key=lambda b: ((b['a'].X + b['b'].X) / 2.0) * nrm[0] + ((b['a'].Y + b['b'].Y) / 2.0) * nrm[1])
    return [frozenset((bs[k]['id'], bs[k + 1]['id'])) for k in range(len(bs) - 1)]


def view_banks(view):
    """当前视图里的直排管：[{e, id, a, b, t(单位方向), ref(中心线参照)}]"""
    out = []
    col = DB.FilteredElementCollector(doc, view.Id).OfCategory(DB.BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType()
    for e in col:
        if not _is_bank(e) or not isinstance(e.Location, DB.LocationCurve):
            continue
        c = e.Location.Curve
        if not isinstance(c, DB.Line):
            continue
        a, b = c.GetEndPoint(0), c.GetEndPoint(1)
        dx, dy = b.X - a.X, b.Y - a.Y
        L_ = math.hypot(dx, dy)
        if L_ < 1e-6:
            continue
        out.append({'e': e, 'id': _idv(e.Id), 'a': a, 'b': b, 't': (dx / L_, dy / L_), 'fam': bank_family(e), 'sys': bank_system(e)})
    return out


def bank_plan(view, banks, cfg):
    """排管转折处的横向中心距：每根排管的两个端点（转折/到井）往里内缩一点作为「站点」，该站点上并排的排管（中心距 <= 上限）串成一条连续标注；
    两个站点相距太远的长直段中间每隔 step 再补一条。严格平行（叉积 < 2e-4）的才放同一组。
    返回链：{'banks', 'ids', 'gaps'(mm), 'p0', 'p1', 'mid'(x,y)}。"""
    gmax = float(cfg['bank_gap_max_m']) * 1000.0 / 304.8
    gmin = float(cfg['bank_min_gap_mm']) / 304.8
    omin = float(cfg['bank_overlap_min_m']) * 1000.0 / 304.8
    inset = float(cfg['bank_inset_mm']) / 304.8
    same_sys = bool(cfg.get('bank_same_system', True))
    step = float(cfg['bank_step_m']) * 1000.0 / 304.8
    groups = []
    for b in banks:
        for g in groups:
            r = g[0]
            if abs(r['t'][0] * b['t'][1] - r['t'][1] * b['t'][0]) <= 2e-4 and (not same_sys or r['sys'] == b['sys']):
                g.append(b)
                break
        else:
            groups.append([b])
    chains = []
    try:
        z0 = view.GenLevel.ProjectElevation
    except Exception:
        z0 = None
    for g in groups:
        if len(g) < 2:
            continue
        ax = g[0]['t']
        if ax[0] < -1e-9 or (abs(ax[0]) <= 1e-9 and ax[1] < 0):
            ax = (-ax[0], -ax[1])
        nrm = (-ax[1], ax[0])
        for b in g:
            sa, sb = _dot((b['a'].X, b['a'].Y), ax), _dot((b['b'].X, b['b'].Y), ax)
            b['s0'], b['s1'] = min(sa, sb), max(sa, sb)
            b['off'] = _dot((b['a'].X, b['a'].Y), nrm)
        # 站点：每根排管两端内缩 inset（短于 2*inset 的取中点）
        st = []
        for b in g:
            L_ = b['s1'] - b['s0']
            if L_ <= 2 * inset:
                st.append((b['s0'] + b['s1']) / 2.0)
            else:
                st.append(b['s0'] + inset)
                st.append(b['s1'] - inset)
        st.sort()
        # 站点去重（0.25 m 内算同一个）
        ded = []
        for x in st:
            if not ded or x - ded[-1] > 0.25 / 0.3048:
                ded.append(x)
        # 长段补点
        full = list(ded)
        if step > 0:
            for k in range(len(ded) - 1):
                gap = ded[k + 1] - ded[k]
                if gap > step * 1.3:
                    n_ = int(gap // step)
                    for q in range(1, n_ + 1):
                        full.append(ded[k] + gap * q / float(n_ + 1))
            full.sort()
        seen = set()
        for sx in full:
            act = [b for b in g if b['s0'] - 1e-6 <= sx <= b['s1'] + 1e-6]
            act.sort(key=lambda b: b['off'])
            cur = []
            for b in act + [None]:
                if b is not None and cur and gmin <= b['off'] - cur[-1]['off'] <= gmax:
                    cur.append(b)
                    continue
                if b is not None and cur and b['off'] - cur[-1]['off'] < gmin:
                    continue            # 几乎重合的第二根，跳过
                if len(cur) >= 2:
                    # 链里每相邻两根并行重叠要够长
                    ok = all(min(cur[j]['s1'], cur[j + 1]['s1']) - max(cur[j]['s0'], cur[j + 1]['s0']) >= omin for j in range(len(cur) - 1))
                    key = tuple(x['id'] for x in cur)
                    if ok:
                        # 同一组排管在 1.5 m 内重复的站点只留一个
                        dup = [c for c in chains if c['key'] == key and abs(c['s'] - sx) < 1.5 / 0.3048]
                        if not dup:
                            z = z0 if z0 is not None else cur[0]['a'].Z
                            p0 = DB.XYZ(ax[0] * sx + nrm[0] * cur[0]['off'], ax[1] * sx + nrm[1] * cur[0]['off'], z)
                            p1 = DB.XYZ(ax[0] * sx + nrm[0] * cur[-1]['off'], ax[1] * sx + nrm[1] * cur[-1]['off'], z)
                            chains.append({'banks': list(cur), 'ids': list(key), 'key': key, 's': sx,
                                           'gaps': [(cur[j + 1]['off'] - cur[j]['off']) * 304.8 for j in range(len(cur) - 1)],
                                           'p0': p0, 'p1': p1, 'mid': ((p0.X + p1.X) / 2.0, (p0.Y + p1.Y) / 2.0)})
                cur = [b] if b is not None else []
    return chains


def bank_existing(view, bank_ids):
    """视图里已经只参照排管的标注：[(set(排管id...), 标注线)]"""
    out = []
    for dm in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Dimension):
        try:
            ids = set()
            ok = True
            for r in dm.References:
                eid = _idv(r.ElementId)
                if eid in bank_ids:
                    ids.add(eid)
                else:
                    ok = False
                    break
            if ok and len(ids) >= 2:
                out.append((ids, dm.Curve))
        except Exception:
            continue
    return out


def _line_dist(ln, xy):
    """标注线（无限长直线）到点 xy 的平面距离。"""
    try:
        o, t = ln.Origin, ln.Direction
        vx, vy = xy[0] - o.X, xy[1] - o.Y
        return abs(vx * t.Y - vy * t.X) / max(math.hypot(t.X, t.Y), 1e-9)
    except Exception:
        return 1e9


def bank_chain_covered(c, have):
    """这条链的每相邻两根排管，在站点附近（1 m 内）都已有一条标注参照了它们。"""
    for j in range(len(c['ids']) - 1):
        a, b = c['ids'][j], c['ids'][j + 1]
        if not any(a in h and b in h and _line_dist(ln, c['mid']) < 1.0 / 0.3048 for h, ln in have):
            return False
    return True


def bank_main():
    view = uidoc.ActiveView
    if not isinstance(view, DB.ViewPlan):
        forms.alert(u'请在平面视图里运行（当前是「%s」）。' % view.Name, title=TITLE)
        return
    cfg = bank_cfg()
    cfg['bank_step_m'] = 0.0
    banks = view_banks(view)
    by_id = dict((b['id'], b) for b in banks)
    chains = bank_plan(view, banks, cfg)
    sel_ids = set(_idv(i) for i in uidoc.Selection.GetElementIds())
    if sel_ids & set(b['id'] for b in banks):
        chains = [c for c in chains if sel_ids & set(c['ids'])]
    idx_map = learned_index(view)
    # 视图里已标过的相邻对、已标过的间距组合
    covered = set()
    for dm in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Dimension):
        try:
            refs = list(dm.References)
        except Exception:
            continue
        ids = [_idv(r.ElementId) for r in refs]
        if not ids or not all(i in by_id for i in ids):
            continue
        covered.update(lateral_pairs(ids, by_id))
    todo, seen, n_cov, n_ref, n_sig = [], set(), 0, 0, 0
    for c in chains:
        pairs = lateral_pairs(c['ids'], by_id)
        if any(p in covered for p in pairs):
            n_cov += 1
            continue
        refs = [pick_ref(b, idx_map) for b in c['banks']]
        if any(r is None for r in refs):
            n_ref += 1
            continue
        sg = tuple(sorted(int(round(abs(g) / 100.0)) for g in c['gaps']))
        if sg in seen:
            n_sig += 1
            continue
        seen.add(sg)
        c['refs'] = refs
        todo.append(c)
    if not todo:
        forms.alert(u'没有要补的：已标过 %d 处；没有学到参照的 %d 处；间距组合重复 %d 处。' % (n_cov, n_ref, n_sig), title=TITLE)
        return
    lines = [u'  %d 根：%s mm' % (len(c['ids']), u' + '.join(u'%.0f' % abs(g) for g in c['gaps'])) for c in todo[:10]]
    msg = u'要补 %d 处转折标注（样式和参照都按你已标的学）：\n%s%s\n\n已标过 %d 处跳过；没学到参照 %d 处；间距组合重复 %d 处。\n\n现在标注？' % (
        len(todo), u'\n'.join(lines), u'\n  …' if len(todo) > 10 else u'', n_cov, n_ref, n_sig)
    if not forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
        return
    types = _dim_types()
    dtype = _default_type([], types)
    t = DB.Transaction(doc, u'天河落 · 排管转折处横向标注')
    fh = t.GetFailureHandlingOptions()
    fh.SetFailuresPreprocessor(_Fails())
    t.SetFailureHandlingOptions(fh)
    t.Start()
    made, fails = 0, []
    try:
        for c in todo:
            arr = DB.ReferenceArray()
            for r in c['refs']:
                arr.Append(r)
            try:
                ln = DB.Line.CreateBound(c['p0'], c['p1'])
                dm = doc.Create.NewDimension(view, ln, arr, dtype) if dtype is not None else doc.Create.NewDimension(view, ln, arr)
                made += 1
            except Exception as ex:
                fails.append((u'、'.join(str(i) for i in c['ids']), (u'%s' % ex).splitlines()[0][:60]))
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    msg = u'完成：新增 %d 条排管横向标注（Ctrl+Z 可整次撤销）。' % made
    if fails:
        msg += u'\n没建成 %d 条：\n' % len(fails) + u'\n'.join(u'  排管 %s：%s' % f for f in fails[:8])
    forms.alert(msg, title=TITLE)


def main():
    pick = forms.CommandSwitchWindow.show([u'井间距标注（相邻井中心到中心）', u'排管横向中心距标注（相邻排管中心线之间）', u'⚙ 排管横向标注设置…'], message=TITLE)
    if not pick:
        return
    try:
        if pick.startswith(u'⚙'):
            if bank_open_settings():
                forms.alert(u'设置已保存，下次运行排管横向标注时生效。', title=TITLE)
        elif pick.startswith(u'排管'):
            bank_main()
        else:
            well_main()
    except Exception:
        forms.alert(u'出错了（已回滚，模型没被改）：\n' + traceback.format_exc(), title=TITLE)


def well_main():
    try:
        view = uidoc.ActiveView
        if not isinstance(view, DB.ViewPlan):
            forms.alert(u'请在平面视图里运行（当前是「%s」）。' % view.Name, title=TITLE)
            return
        pb = forms.ProgressBar(title=u'正在分析排管路由…', cancellable=False)
        try:
            wells = collect_wells(view)
            if len(wells) < 2:
                forms.alert(u'当前视图里没有足够的井（至少 2 口，标记属于井编号系列的常规模型）。', title=TITLE)
                return
            banks = collect_banks()
            edges = build_edges(wells, banks)
            dims = existing_dims(view, wells)
            scale = max(int(view.Scale), 1)
            plan = make_plan(view, wells, edges, dims, 0)
        finally:
            pb.close()
        if not (plan['delete'] or plan['create'] or plan['skip']):
            forms.alert(u'没有要做的：视图里相邻的井都已经有正确的中心距标注了（%d 对）。' % len(plan['covered']), title=TITLE)
            return
        types = _dim_types()
        dtype0 = _default_type(dims, types)
        ans = show_dialog(view, wells, plan, types, dtype0, edges)
        if ans is None:
            return
        off_ft = ans['off'] * scale / FT          # 图纸 mm × 比例 = 模型 mm → 英尺
        plan['delete'] = [(D, []) for D in ans['del_dims']]
        plan['create'] = ans['new_items']
        ndel, made, fails, badval = apply_plan(view, wells, plan, ans['dtype'], off_ft, ans['delete'], ans['create'])
        msg = u'完成：删除旧标注 %d 条，新增标注 %d 条（Ctrl+Z 可整次撤销）。' % (ndel, made)
        if badval:
            msg += u'\n⚠ 有 %d 条标注的数值和井中心距对不上，请抽查。' % badval
        if fails:
            msg += u'\n没建成 %d 条：\n' % len(fails) + u'\n'.join(u'  %s：%s' % (n_, w_) for n_, w_ in fails[:10])
        forms.alert(msg, title=TITLE)
    except Exception:
        forms.alert(u'出错了（已回滚，模型没被改）：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
