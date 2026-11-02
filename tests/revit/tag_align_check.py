# -*- coding: utf-8 -*-
"""在 Revit 里运行（只规划，不写入）：用规划出来的标记位置（图纸 mm 坐标）统计对齐情况。
MODE='after' 看规划结果；MODE='now' 看当前模型里的位置。结果 %TEMP%\\thl_tag_align.txt"""
import io, sys, types, re, os, traceback, math
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI

uiapp = __revit__
uidoc = uiapp.ActiveUIDocument
d = uidoc.Document
MODE = globals().get('MODE', 'after')
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_tag_align.txt')
SRC = r'C:\KAIFA\thl\python-src'
FT = 304.8
LOG = []


class _Forms(object):
    class ProgressBar(object):
        cancelled = False
        def __init__(self, *a, **k): pass
        def update_progress(self, a, b): pass
        def close(self): pass

    class CommandSwitchWindow(object):
        @staticmethod
        def show(items, **k):
            return None

    def alert(self, msg, *a, **k):
        return False


def analyze(rows, label):
    """rows: [(orient 'v'/'h', x_mm, y_mm)]"""
    LOG.append(u'—— %s：%d 个标记' % (label, len(rows)))
    for o, idx, nm in (('v', 1, 'x'), ('h', 2, 'y')):
        vals = sorted(r[idx] for r in rows if r[0] == o)
        if not vals:
            continue
        diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
        exact = sum(1 for x in diffs if x < 0.05)
        n1 = sum(1 for x in diffs if 0.05 <= x < 1.0)
        n5 = sum(1 for x in diffs if 1.0 <= x < 5.0)
        n10 = sum(1 for x in diffs if 5.0 <= x < 10.0)
        clusters, cur = [], [vals[0]]
        for x in vals[1:]:
            if x - cur[-1] <= 10.0:
                cur.append(x)
            else:
                clusters.append(cur)
                cur = [x]
        clusters.append(cur)
        multi = [c for c in clusters if len(c) >= 2]
        bad = [c for c in multi if c[-1] - c[0] > 0.05]
        LOG.append(u'  %s向（%s坐标）%d 个：相邻差 <0.05mm 平齐 %d；0.05~1mm %d；1~5mm %d；5~10mm %d；10mm 内成簇 %d 个，其中没完全平齐的 %d 个簇（涉及 %d 个标记，最大偏差 %.2f mm）' % (
            u'竖' if o == 'v' else u'横', nm, len(vals), exact, n1, n5, n10, len(multi), len(bad), sum(len(c) for c in bad),
            max([c[-1] - c[0] for c in bad]) if bad else 0.0))


try:
    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=d, uidoc=uidoc)
    pr.DB = DB; pr.UI = UI; pr.forms = _Forms()
    sys.modules['pyrevit'] = pr
    s = io.open(os.path.join(SRC, 'tag-tidy', 'script.py'), encoding='utf-8').read()
    s = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', s, flags=re.M)
    n = {'__name__': 'x', '__revit__': uiapp}
    exec(compile(s, 'tag-tidy', 'exec'), n)
    view = uidoc.ActiveView
    sc = view.Scale
    cfg = dict(n['load']())
    LOG.append(u'视图 %s，对齐容差 %.1f mm' % (view.Name, cfg['align_mm']))
    pl = n['plan'](view, cfg)
    now = []
    for x in pl['items']:
        h = x['head']
        now.append((x.get('axis') or 'h', h.X * FT / sc, h.Y * FT / sc))
    analyze(now, u'当前模型')
    after = []
    for x in pl['items']:
        after.append((x.get('axis') or 'h', x['new'][0] * FT / sc, x['new'][1] * FT / sc))
    analyze(after, u'规划后（头位置）')
    centre = []
    for x in pl['items']:
        centre.append((x.get('axis') or 'h', x['c'][0] * FT / sc, x['c'][1] * FT / sc))
    analyze(centre, u'规划后（文字框中心，肉眼看到的对齐）')
    off = {}
    for x in pl['items']:
        k = (x.get('axis'), round(x['size'][3] * FT / sc, 1))
        off[k] = off.get(k, 0) + 1
    LOG.append(u'头位置→框中心 的 y 偏移分布（图纸 mm）：%s' % sorted(off.items(), key=lambda kv: -kv[1])[:6])
    # 引线检查（坐标）：每条引线 = 文字框中心 → 构件中心；是否穿过别的标记文字框、或和别的引线相交
    def seg_hit(r, a, b2):
        return n['seg_hits_rect']((a[0], a[1], b2[0], b2[1]), r)
    its = pl['items']
    rects = [n['rect'](x['c'][0], x['c'][1], x['size'][0], x['size'][1]) for x in its]
    leaders = [((x['c'][0], x['c'][1]), (x['a'].X, x['a'].Y)) for x in its]
    thru = 0
    for i, (p0, p1) in enumerate(leaders):
        for j, rc in enumerate(rects):
            if i != j and seg_hit(rc, p0, p1):
                thru += 1
                break
    cross = 0
    for i in range(len(leaders)):
        a0, a1 = leaders[i]
        for j in range(i + 1, len(leaders)):
            b0, b1 = leaders[j]
            if n['_seg_cross'](a0[0], a0[1], a1[0], a1[1], b0[0], b0[1], b1[0], b1[1]):
                cross += 1
    LOG.append(u'引线穿过别的标记文字框的 %d 条；引线互相交叉 %d 对' % (thru, cross))
    LOG.append(u'引线冲突修复：修好 %d 个，仍冲突 %d 个' % (pl.get('lead_fixed', 0), pl.get('lead_left', 0)))
    LOG.append(u'规划：吸附 %d（其中沿线滑动 %d），整队 %d；压构件 %d、标记重叠 %d、引线超限 %d' % (
        pl['snapped'], pl.get('slid', 0), pl.get('stacked', 0), pl['over_el'], pl['over_tag'], pl['far']))
except Exception:
    LOG.append(traceback.format_exc())
with io.open(OUT, 'w', encoding='utf-8') as fo:
    fo.write(u'\n'.join(LOG))
