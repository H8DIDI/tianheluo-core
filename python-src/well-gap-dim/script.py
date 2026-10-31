# -*- coding: utf-8 -*-
__title__ = '井间距自动标注'
__doc__ = ('在当前平面视图里，自动给「同一条排管路由上相邻的两口井」标注井中心到中心的距离：\n'
           '先按排管的连接关系找出同一条排管上相邻的井（排管是一段一段的，端点相接、走向基本不拐的连成一条，到井为止；拐到别的支线上的井不算），'
           '同一直线上连续的井合并成一条连续标注，参照的是井族自带的「中心（左/右）」「中心（前/后）」参照平面，数值是准的、井挪了会跟着变。\n'
           '视图里已经手工标好且正确的不重复标；标错的（参照的不是井中心、两口井之间没有排管连接、同一口井参了两次、标注文字被手改）可以一并删掉重标。'
           '先在窗口里列出「要删什么、要新增什么、哪些没法标」，确认后一次执行，整次一个事务，Ctrl+Z 可撤销。')

import re
import math
import traceback
from pyrevit import revit, DB, UI, forms

# @include _shared/form_fit.py
# @include _shared/ws_guard.py
# @include _shared/well_config.py
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
            dx, dy = (wb['c'][0] - wa['c'][0]) * FT / 1000.0, (wb['c'][1] - wa['c'][1]) * FT / 1000.0
            plan['skip'].append((e, u'斜向（Δ %.1f, %.1f m），井中心面和标注方向对不上，没法用中心参照标' % (dx, dy)))
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
                plan['create'].append({'ax': ax, 'ids': order})
            else:
                for a in comp:
                    for b in nb[a]:
                        if a < b:
                            plan['create'].append({'ax': ax, 'ids': sorted([a, b], key=lambda i: _dot(wells[i]['c'], ax))})
    return plan


class _Fails(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        for m in fa.GetFailureMessages():
            if m.GetSeverity() == DB.FailureSeverity.Warning:
                fa.DeleteWarning(m)
        return DB.FailureProcessingResult.Continue


def _view_z(view, wells, ids):
    try:
        return view.GenLevel.ProjectElevation
    except Exception:
        return wells[ids[0]]['c'][2]


def create_dim(view, wells, item, dtype, off_ft):
    """按一条规划建标注。返回 (标注, 错误说明)。"""
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
        if do_create:
            for item in plan['create']:
                dm, err = create_dim(view, wells, item, dtype, off_ft)
                names = u'-'.join(wells[i]['mark'] for i in item['ids'])
                if dm is None:
                    fails.append((names, err))
                    continue
                made += 1
                try:      # 数值核对：每段应该等于井中心在标注方向上的距离
                    s = [_dot(wells[i]['c'], item['ax']) for i in item['ids']]
                    want = [abs(s[i + 1] - s[i]) for i in range(len(s) - 1)]
                    if dm.NumberOfSegments and dm.NumberOfSegments > 0:
                        got = sorted(dm.Segments.get_Item(i).Value for i in range(dm.NumberOfSegments))
                    else:
                        got = [dm.Value]
                    if any(abs(a - b) > 0.002 for a, b in zip(sorted(want), got)):
                        badval += 1
                except Exception:
                    pass
        t.Commit()
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
    head.Text = (u'视图「%s」：井 %d 口，排管路由上相邻的井 %d 对。已有正确标注 %d 条（覆盖 %d 对，不重复标）；不对的旧标注 %d 条；'
                 u'新增标注 %d 条（其中连续标注 %d 条）；没法用中心参照标的 %d 对。'
                 % (view.Name, len(wells), len(edges), len(plan['keep']), len(plan['covered']), len(plan['delete']),
                    len(plan['create']), nch, len(plan['skip'])))
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
    note.Text = u'连续标注：同一直线上相邻的井合成一条，参照井族的「中心（左/右）」「中心（前/后）」面。标得不理想的可以在视图里拖动标注线。'
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


def main():
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
