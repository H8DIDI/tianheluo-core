# -*- coding: utf-8 -*-
"""在 Revit 里运行（只读）：分析当前视图的井、排管连接、已有的井间标注，作为「井间距标注」插件的原型。结果写到 TEMP 下 thl_well_dim 的 probe.txt。"""
import io, os, re, math
import Autodesk.Revit.DB as DB

d = __revit__.ActiveUIDocument.Document
v = d.GetElement(__revit__.ActiveUIDocument.ActiveView.Id)
FT = 304.8
OUTD = os.path.join(os.environ.get('TEMP', '.'), 'thl_well_dim')
os.makedirs(OUTD) if not os.path.isdir(OUTD) else None
BANK_PREFIX = (u'TELCO', u'ELV', u'LV-', u'ELE-', u'雨水沟')
L = []


def mk(e):
    p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
    return (p.AsString() or u'') if p else u''


wells = []
for e in DB.FilteredElementCollector(d, v.Id).OfCategory(DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType():
    if isinstance(e, DB.FamilyInstance) and mk(e):
        wells.append(e)
L.append(u'视图 %s：井 %d 口' % (v.Name, len(wells)))
W = {}
for e in wells:
    bb = e.get_BoundingBox(None)
    p = e.Location.Point
    W[e.Id.Value] = dict(e=e, mark=mk(e), c=(p.X, p.Y), bb=(bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y), rot=e.Location.Rotation * 180 / math.pi,
                         hand=(e.HandOrientation.X, e.HandOrientation.Y), face=(e.FacingOrientation.X, e.FacingOrientation.Y))
banks = []
for e in DB.FilteredElementCollector(d).OfCategory(DB.BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType():
    f = d.GetElement(e.GetTypeId()).Family.Name
    if f.upper().startswith(BANK_PREFIX) and not f.startswith(u'雨水沟'):
        banks.append(e)
L.append(u'排管 %d 根（全项目）' % len(banks))


def well_at(pt, tol=0.5 / FT * 1000 / 1000):
    """端点落在哪口井的包围盒内（放宽 tol 英尺）。"""
    best = None
    for k, w in W.items():
        x0, y0, x1, y1 = w['bb']
        if x0 - tol <= pt.X <= x1 + tol and y0 - tol <= pt.Y <= y1 + tol:
            dd = (pt.X - w['c'][0]) ** 2 + (pt.Y - w['c'][1]) ** 2
            if best is None or dd < best[0]:
                best = (dd, k)
    return best[1] if best else None


# ---- 路由图：排管是一段一段的，端点相接（0.3 m 内）的连成一条路由；端点落在井里的算到井上
TOL = 0.3 / 0.3048
vid = {}
parent = {}


def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


pts = []
for b in banks:
    c = b.Location.Curve
    for k in (0, 1):
        pts.append((b.Id.Value, k, c.GetEndPoint(k)))
grid = {}
for i, (bid, k, pt) in enumerate(pts):
    parent[i] = i
for i, (bid, k, pt) in enumerate(pts):
    key = (int(pt.X // TOL), int(pt.Y // TOL))
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for j in grid.get((key[0] + dx, key[1] + dy), []):
                q = pts[j][2]
                if (q.X - pt.X) ** 2 + (q.Y - pt.Y) ** 2 <= TOL * TOL:
                    parent[find(i)] = find(j)
    grid.setdefault(key, []).append(i)
adj = {}
piece_v = {}
for i, (bid, k, pt) in enumerate(pts):
    w = well_at(pt)
    piece_v.setdefault(bid, [None, None])[k] = ('w', w) if w is not None else ('c', find(i))
for bid, (v0, v1) in piece_v.items():
    adj.setdefault(v0, []).append(v1)
    adj.setdefault(v1, []).append(v0)
edges = {}
for wk in W:
    start = ('w', wk)
    seen = set([start])
    stack = [n for n in adj.get(start, [])]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        if n[0] == 'w':
            if n[1] != wk:
                edges.setdefault(tuple(sorted((wk, n[1]))), 1)
            continue
        stack.extend(adj.get(n, []))
unattached = sum(1 for b, (v0, v1) in piece_v.items() if v0[0] == 'c' and v1[0] == 'c')
L.append(u'路由图：井-井相邻 %d 对（两端都不在井里的排管片段 %d 根）' % (len(edges), unattached))
aligned = 0
diag = []
for (a, z) in edges:
    dx, dy = W[z]['c'][0] - W[a]['c'][0], W[z]['c'][1] - W[a]['c'][1]
    if min(abs(dx), abs(dy)) <= 0.06 * max(abs(dx), abs(dy)) or min(abs(dx), abs(dy)) * FT < 300:
        aligned += 1
    else:
        diag.append((W[a]['mark'], W[z]['mark'], round(dx * FT / 1000, 2), round(dy * FT / 1000, 2)))
L.append(u'轴向 %d 对，斜向 %d 对：%s' % (aligned, len(diag), diag[:15]))
# 已有标注
ok = bad = other = 0
cnt = 0
for dm in DB.FilteredElementCollector(d, v.Id).OfClass(DB.Dimension):
    refs = [r for r in dm.References]
    els = [d.GetElement(r.ElementId) for r in refs]
    if not all(e is not None and e.Id.Value in W for e in els):
        other += 1
        continue
    ids = [e.Id.Value for e in els]
    pairs = [tuple(sorted((ids[i], ids[i + 1]))) for i in range(len(ids) - 1)]
    good = all(p in edges for p in pairs)
    cnt += 1
    if good:
        ok += 1
    else:
        bad += 1
        L.append(u'  非相邻/未连接 dim %d：%s' % (dm.Id.Value, u'-'.join(W[i]['mark'] for i in ids)))
L.append(u'已有标注：纯井标注 %d（连接对得上 %d，对不上 %d），其他标注 %d' % (cnt, ok, bad, other))
# 每口井的中心参照
w0 = W[list(W)[0]]['e']
L.append(u'中心参照 LR=%d FB=%d' % (len(w0.GetReferences(DB.FamilyInstanceReferenceType.CenterLeftRight)), len(w0.GetReferences(DB.FamilyInstanceReferenceType.CenterFrontBack))))
io.open(os.path.join(OUTD, 'probe.txt'), 'w', encoding='utf-8').write(u'\n'.join(L))

# ---- 抽查：几个「对不上」的两口井之间的实际情况
def mrk(m):
    for k, w in W.items():
        if w['mark'] == m:
            return k
for a, b in ((u'B-19', u'B-20'), (u'A-21', u'A-20'), (u'AA-03', u'AA-01'), (u'B-36', u'B-33'), (u'B-48', u'B-127'), (u'A-36', u'A-132'), (u'A-07', u'B-04'), (u'B-32', u'B-23'), (u'A-12', u'A-04')):
    ka, kb = mrk(a), mrk(b)
    if ka is None or kb is None:
        L.append(u'  抽查 %s-%s：找不到井' % (a, b)); continue
    ca, cb = W[ka]['c'], W[kb]['c']
    dist = math.hypot(ca[0] - cb[0], ca[1] - cb[1]) * FT / 1000
    # 线上的排管：离两井连线 0.6 m 以内的排管片段
    near = 0
    for bk in banks:
        c = bk.Location.Curve
        p0, p1 = c.GetEndPoint(0), c.GetEndPoint(1)
        mx, my = (p0.X + p1.X) / 2, (p0.Y + p1.Y) / 2
        # 点到线段 ca-cb 的距离
        vx, vy = cb[0] - ca[0], cb[1] - ca[1]
        t = max(0, min(1, ((mx - ca[0]) * vx + (my - ca[1]) * vy) / (vx * vx + vy * vy)))
        if math.hypot(ca[0] + t * vx - mx, ca[1] + t * vy - my) * FT < 600:
            near += 1
    # 井附近（1.5m）的排管端点是否落在井包围盒外
    gap = []
    for kk in (ka, kb):
        x0, y0, x1, y1 = W[kk]['bb']
        g = 1e9
        for bk in banks:
            c = bk.Location.Curve
            for e_ in (0, 1):
                q = c.GetEndPoint(e_)
                dxx = max(x0 - q.X, 0, q.X - x1); dyy = max(y0 - q.Y, 0, q.Y - y1)
                g = min(g, math.hypot(dxx, dyy))
        gap.append(round(g * FT / 1000, 2))
    L.append(u'  抽查 %s-%s：中心距 %.2f m，连线附近排管 %d 根，两口井外沿到最近排管端点 %s m，邻接=%s' % (a, b, dist, near, gap, tuple(sorted((ka, kb))) in edges))
io.open(os.path.join(OUTD, 'probe.txt'), 'a', encoding='utf-8').write(chr(10) + chr(10).join(L[-9:]))
