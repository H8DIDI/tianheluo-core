# -*- coding: utf-8 -*-
"""在 Revit 里运行：把当前项目几组真实 MSCL / HDPE 管道复制进临时项目，实跑「一键布置管道支墩」：
读管、分组、墩位间距、生成支墩与垫层、几何尺寸、重跑先删旧。当前项目只读；临时项目不保存。
结果：%TEMP%\\thl_pipe_support_e2e\\report.txt"""
import io, sys, types, re, os, math, traceback
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI
from System.Collections.Generic import List

uiapp = __revit__
src_doc = uiapp.ActiveUIDocument.Document
app = uiapp.Application
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_pipe_support_e2e')
SRC = r'C:\KAIFA\thl\python-src'
if not os.path.isdir(OUT):
    os.makedirs(OUT)
LOG = []
RES = {'p': 0, 'f': 0}
FT = 304.8
SAMPLE = [9422320, 11265492, 11247651, 9422580, 11247569, 10033794, 9422749, 10098992]


def flush(final=False):
    with io.open(os.path.join(OUT, 'report.txt'), 'w', encoding='utf-8') as f:
        f.write(u'\n'.join(LOG) + (u'\n通过 %d 失败 %d\n完成' % (RES['p'], RES['f']) if final else u''))


def check(name, ok, detail=u''):
    RES['p' if ok else 'f'] += 1
    LOG.append(u'%s  %s %s' % (u'PASS' if ok else u'FAIL', name, detail))
    flush()


class _Forms(object):
    def alert(self, msg, *a, **k):
        LOG.append(u'  [alert] ' + (u'%s' % msg)[:300].replace(u'\n', u' | '))
        return True


def _comment(e):
    p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    return (p.AsString() or u'') if p is not None else u''


d = app.NewProjectDocument(r'C:\ProgramData\Autodesk\RVT 2026\Templates\Chinese\Structural Analysis-DefaultCHSCHS.rte')
try:
    ids = List[DB.ElementId]()
    for i in SAMPLE:
        e = src_doc.GetElement(DB.ElementId(i))
        if e is not None:
            ids.Add(e.Id)
    t = DB.Transaction(d, u'复制样本')
    t.Start()
    new_ids = list(DB.ElementTransformUtils.CopyElements(src_doc, ids, d, DB.Transform.Identity, DB.CopyPasteOptions()))
    t.Commit()
    LOG.append(u'复制了 %d 个图元' % len(new_ids))

    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=d, uidoc=types.SimpleNamespace(Document=d))
    pr.DB = DB
    pr.UI = UI
    pr.forms = _Forms()
    sys.modules['pyrevit'] = pr
    s = io.open(os.path.join(SRC, 'pipe-support', 'script.py'), encoding='utf-8').read()
    s = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', s, flags=re.M)
    n = {'__name__': 'x', '__revit__': uiapp}
    exec(compile(s, 'pipe-support', 'exec'), n)

    els = [d.GetElement(i) for i in new_ids]
    pipes = [e for e in els if e is not None and e.Category is not None and e.Category.Id.Value == int(DB.BuiltInCategory.OST_PipeCurves)]
    check(u'复制到管道', len(pipes) >= 6, u'%d 根' % len(pipes))
    r = dict(n['DEFAULT_RULES'])
    n['RULES'].update(r)
    pl = n['plan'](pipes, r)
    LOG.append(u'可布置 %d 根，跳过 %s' % (len(pl['pipes']), [(e.Id.Value, w) for e, w in pl['skipped']]))
    for p in pl['pipes']:
        LOG.append(u'  管 %d %s DN%d OD%.0f 长%.1fm' % (p['id'], p['kind'], p['dn'], p['od'] * FT, (p['b'] - p['a']).GetLength() * 0.3048))
    check(u'MSCL 和 HDPE 都认出来', set(p['kind'] for p in pl['pipes']) >= set(['H', 'M']))
    for g in pl['groups']:
        LOG.append(u'  组：%s' % [(p['kind'] + str(p['dn'])) for p in g])
    check(u'有 MSCL+HDPE 混合组', any(len(set(p['kind'] for p in g)) > 1 for g in pl['groups']))

    # 墩距：同组相邻两个墩（有共同的管）不超过该处的表格间距
    bad = []
    by = {}
    for st in pl['stations']:
        by.setdefault(id(st['origin']), []).append(st)
    for k, sts in by.items():
        sts.sort(key=lambda x: x['t'])
        for a, b in zip(sts[:-1], sts[1:]):
            common = [m for m in a['members'] if any(m is mm for mm in b['members'])]
            if not common:
                continue
            lim = max(n['spacing_for'](a['members'], r), n['spacing_for'](b['members'], r))
            gap = (b['t'] - a['t']) * FT
            if gap > lim + 1:
                bad.append((round(gap), lim))
    check(u'墩距不超过表格', not bad, u'%s' % bad[:5])
    LOG.append(u'墩位 %d 个，避不开管件 %d' % (len(pl['stations']), pl['blocked']))

    stats = n['execute'](pl, r, [])
    LOG.append(u'生成：%s' % dict((k, v) for k, v in stats.items() if k != 'heights'))
    check(u'墩全部生成、无失败', stats['piers'] == len(pl['stations']) and stats['fail'] == 0)
    check(u'每个墩一块垫层', stats['pads'] == stats['piers'])
    check(u'管座全部挖成', stats['nosaddle'] == 0)

    dss = [e for e in DB.FilteredElementCollector(d).OfClass(DB.DirectShape) if e.ApplicationId == 'PipeSupportPlugin']
    piers = [e for e in dss if _comment(e).startswith(u'管道支墩')]
    pads = [e for e in dss if not _comment(e).startswith(u'管道支墩')]
    check(u'模型里墩与垫层数量对', len(piers) == stats['piers'] and len(pads) == stats['pads'], u'%d/%d' % (len(piers), len(pads)))

    # 几何：墩底 = 最低管底 - 300，墩顶 = 最低管中心，挖了管座（体积 < 方墩）
    geo_bad = []
    for st in pl['stations'][:40]:
        vc, W, zb, zt = n['pier_dims'](st, r)
        hosts = n['_host_text'](st)
        mine = [e for e in piers if (u'(Id:%s)' % hosts) in _comment(e)]
        if not mine:
            geo_bad.append(u'没找到 %s' % hosts)
            continue
        p = n['_world'](st['origin'], st['ax'], st['t'], vc, 0)
        best = None
        for e in mine:
            b = e.get_BoundingBox(None)
            c = (b.Min + b.Max) * 0.5
            dd = math.hypot(c.X - p.X, c.Y - p.Y)
            if best is None or dd < best[0]:
                best = (dd, e, b)
        dd, e, b = best
        zbot = min(m.at(st['t'])[1] - m.od / 2 for m in st['members'])
        zc = min(m.at(st['t'])[1] for m in st['members'])
        if abs(b.Min.Z - (zbot - 300 / FT)) * FT > 1 or abs(b.Max.Z - zc) * FT > 1:
            geo_bad.append(u'%s 底%.0f/%.0f 顶%.0f/%.0f' % (hosts, b.Min.Z * FT, (zbot - 300 / FT) * FT, b.Max.Z * FT, zc * FT))
        vol = sum(g.Volume for g in e.get_Geometry(DB.Options()) if isinstance(g, DB.Solid))
        box = W * (200 / FT) * (zt - zb)
        if not (vol < box - 1e-4):
            geo_bad.append(u'%s 体积没减少 %.4f/%.4f' % (hosts, vol, box))
    check(u'墩标高与管座（抽查 40 个）', not geo_bad, u'; '.join(geo_bad[:4]))
    pb = []
    for pad in pads[:40]:
        b = pad.get_BoundingBox(None)
        hz = (b.Max.Z - b.Min.Z) * FT
        if abs(hz - 100) > 1:
            pb.append(round(hz))
    check(u'垫层厚 100', not pb, u'%s' % pb[:5])
    singles = [st for st in pl['stations'] if len(st['members']) == 1]
    if singles:
        st = singles[0]
        vc, W, zb, zt = n['pier_dims'](st, r)
        check(u'单管墩宽 = 外径 + 200', abs(W - st['members'][0].od - 200 / FT) * FT < 0.5, u'%.0f mm (%s)' % (W * FT, n['_desc'](st)))
    multi = [st for st in pl['stations'] if len(st['members']) >= 2]
    check(u'有统一墩（一墩多管）', bool(multi), u'%d 个' % len(multi))
    if multi:
        st = multi[0]
        vc, W, zb, zt = n['pier_dims'](st, r)
        vs = [(m.at(st['t'])[0], m.od) for m in st['members']]
        span = max(v + o / 2 for v, o in vs) - min(v - o / 2 for v, o in vs)
        check(u'统一墩宽 = 管外包 + 200', abs(W - span - 200 / FT) * FT < 0.5, u'%.0f mm (%s)' % (W * FT, n['_desc'](st)))
        LOG.append(u'  统一墩示例高 %.0f mm' % ((zt - zb) * FT))

    # 重跑：先删旧的，数量不变
    ids_set = set(p['id'] for p in pl['pipes'])
    old = n['existing_supports'](ids_set)
    check(u'按宿主找到全部旧支墩', len(old) == len(dss), u'%d/%d' % (len(old), len(dss)))
    stats2 = n['execute'](n['plan'](pipes, r), r, old)
    dss2 = [e for e in DB.FilteredElementCollector(d).OfClass(DB.DirectShape) if e.ApplicationId == 'PipeSupportPlugin']
    check(u'重跑后不重复', len(dss2) == len(dss) and stats2['deleted'] == len(dss), u'%d -> %d' % (len(dss), len(dss2)))

    # 墩底做到地面：在管子下面放一块地形实体，墩和垫层应落到地面
    old = n['existing_supports'](ids_set)
    allp = [p['a'] for p in pl['pipes']] + [p['b'] for p in pl['pipes']]
    x0, x1 = min(q.X for q in allp) - 20, max(q.X for q in allp) + 20
    y0, y1 = min(q.Y for q in allp) - 20, max(q.Y for q in allp) + 20
    zmin_bot = min(min(p['a'].Z, p['b'].Z) - p['od'] / 2 for p in pl['pipes'])
    lvl = DB.FilteredElementCollector(d).OfClass(DB.Level).FirstElement()
    tt = DB.FilteredElementCollector(d).OfClass(DB.ToposolidType).FirstElementId()
    tx = DB.Transaction(d, u'地形')
    tx.Start()
    loop = DB.CurveLoop()
    pts = [DB.XYZ(x0, y0, 0), DB.XYZ(x1, y0, 0), DB.XYZ(x1, y1, 0), DB.XYZ(x0, y1, 0)]
    for i in range(4):
        loop.Append(DB.Line.CreateBound(pts[i], pts[(i + 1) % 4]))
    topo = DB.Toposolid.Create(d, List[DB.CurveLoop]([loop]), tt, lvl.Id)
    tx.Commit()
    gtop = topo.get_BoundingBox(None).Max.Z
    LOG.append(u'地形顶 %.0f，最低管底 %.0f' % (gtop * FT, zmin_bot * FT))
    r3 = dict(r)
    r3['ground_max_mm'] = 20000.0
    n['RULES'].update(r3)
    pl3 = n['plan'](pipes, r3)
    check(u'全部墩都要加高到地面', pl3['raised'] == len(pl3['stations']), u'%d/%d' % (pl3['raised'], len(pl3['stations'])))
    st3 = n['execute'](pl3, r3, old)
    dss3 = [e for e in DB.FilteredElementCollector(d).OfClass(DB.DirectShape) if e.ApplicationId == 'PipeSupportPlugin']
    pz = [e.get_BoundingBox(None).Min.Z for e in dss3 if _comment(e).startswith(u'管道支墩')]
    dz = [e.get_BoundingBox(None).Min.Z for e in dss3 if not _comment(e).startswith(u'管道支墩')]
    check(u'墩底 = 地面', pz and all(abs(z - gtop) * FT < 1 for z in pz), u'%.0f~%.0f' % (min(pz) * FT, max(pz) * FT))
    check(u'垫层底 = 地面 - 100', dz and all(abs(z - gtop + 100 / FT) * FT < 1 for z in dz))
    r4 = dict(r)
    r4['ground_max_mm'] = 100.0
    pl4 = n['plan'](pipes, r4)
    check(u'超过最多加高的不加高', pl4['raised'] == 0 and pl4['too_deep'] == len(pl4['stations']))

    tb = n['parse_table']([u'DN50=0.9', u'100：1.3', u'300=2.5m'])
    check(u'表格解析', tb == {50: 900.0, 100: 1300.0, 300: 2500.0}, u'%s' % tb)
    check(u'查表：中间档取小、超出返回 None', n['lookup'](tb, 80) == 900.0 and n['lookup'](tb, 400) is None and n['lookup'](tb, 20) is None)

    class M(object):
        def __init__(s, k, dn):
            s.p = {'kind': k, 'dn': dn}
    check(u'混合 DN50 取 0.9（安全取值）', n['spacing_for']([M('H', 50), M('M', 200)], r) == 900.0)
    check(u'混合 DN250 = 2.3', n['spacing_for']([M('H', 250), M('M', 200)], r) == 2300.0)
    check(u'全 MSCL DN200 = 4.0', n['spacing_for']([M('M', 200), M('M', 250)], r) == 4000.0)
except Exception:
    LOG.append(traceback.format_exc())
finally:
    flush(True)
    try:
        d.Close(False)
    except Exception:
        pass
OUT = u'\n'.join(LOG)
