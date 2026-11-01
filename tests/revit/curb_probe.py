# -*- coding: utf-8 -*-
"""在 Revit 里运行（只读）：看新路面（道路楼板）的边，和旧路缘石底边数据的重合情况。结果：TEMP 下 thl_curb 文件夹的 probe.txt。"""
import io, os, json, math
import Autodesk.Revit.DB as DB

d = __revit__.ActiveUIDocument.Document
FT = 304.8
OUTD = os.path.join(os.environ.get('TEMP', '.'), 'thl_curb')
os.makedirs(OUTD) if not os.path.isdir(OUTD) else None
SEGF = r'C:\Users\PCB\AppData\Local\Temp\claude\C--KAIFA\13160e3c-863f-4463-96a4-a3f437aaad49\scratchpad\curbsegs.json'
segs = json.load(io.open(SEGF, encoding='utf-8'))
L = []


def segdist(p, s):
    a, b = s['a'], s['b']
    chains = [(a, s['m']), (s['m'], b)] if 'm' in s else [(a, b)]
    best = 1e18
    for u, v in chains:
        dx, dy = v[0] - u[0], v[1] - u[1]
        L2 = dx * dx + dy * dy
        t = 0 if L2 == 0 else max(0, min(1, ((p[0] - u[0]) * dx + (p[1] - u[1]) * dy) / L2))
        best = min(best, math.hypot(p[0] - (u[0] + t * dx), p[1] - (u[1] + t * dy)))
    return best


def near_curb(p):
    best = 1e18
    for s in segs:
        dd = segdist(p, s)
        if dd < best:
            best = dd
    return best


floors = []
for e in DB.FilteredElementCollector(d).OfCategory(DB.BuiltInCategory.OST_Floors).WhereElementIsNotElementType():
    t = d.GetElement(e.GetTypeId())
    if t.Name in (u'道路',) or u'道路' in t.Name:
        floors.append((e, t.Name))
L.append(u'道路类楼板 %d 块；旧路缘石底边 %d 段、%d 个构件' % (len(floors), len(segs), len(set(s['id'] for s in segs))))
opt = DB.Options()
for e, tn in floors:
    bb = e.get_BoundingBox(None)
    # 顶面的边界
    top = e.GetGeometryObjectFromReference(DB.HostObjectUtils.GetTopFaces(e)[0])
    ne = 0
    tot = 0.0
    cov = 0.0
    for loop in top.GetEdgesAsCurveLoops():
        for c in loop:
            ne += 1
            ln = c.Length * FT
            tot += ln
            n = max(2, int(ln // 2000))
            ok = 0
            for i in range(n + 1):
                q = c.Evaluate(float(i) / n, True)
                if near_curb((q.X * FT, q.Y * FT)) < 600:
                    ok += 1
            if ok >= 0.8 * (n + 1):
                cov += ln
    L.append(u'  楼板 %d %s：边 %d 条、周长 %.0f m、与旧路缘石重合的 %.0f m，顶面 z=%.2f m，bbox x[%.0f,%.0f] y[%.0f,%.0f]' % (
        e.Id.Value, tn, ne, tot / 1000, cov / 1000, top.Origin.Z * FT / 1000 if hasattr(top, 'Origin') else 0, bb.Min.X * FT / 1000, bb.Max.X * FT / 1000, bb.Min.Y * FT / 1000, bb.Max.Y * FT / 1000))
with io.open(os.path.join(OUTD, 'probe.txt'), 'w', encoding='utf-8') as fo:
    fo.write(chr(10).join(L))
