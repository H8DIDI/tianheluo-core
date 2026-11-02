# -*- coding: utf-8 -*-
"""在 Revit 里运行（只规划，不写入）：构件标记一键整理的规划部分。结果 %TEMP%\\thl_tag_tidy_dry.txt。
MODE='plan' 只统计；MODE='apply' 真实写入（会写）。"""
import io, sys, types, re, os, traceback
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI

uiapp = __revit__
uidoc = uiapp.ActiveUIDocument
d = uidoc.Document
MODE = globals().get('MODE', 'plan')
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_tag_tidy_dry.txt')
SRC = r'C:\KAIFA\thl\python-src'
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
    cfg = dict(n['load']())
    cfg['add_missing'] = bool(globals().get('ADD_MISSING', False))
    LOG.append(u'视图 %s，文档 %s' % (view.Name, d.Title))
    pl = n['plan'](view, cfg)
    import math as _m
    sc = pl['scale']
    lens = sorted(_m.hypot(x['c'][0] - x['a'].X, x['c'][1] - x['a'].Y) * 304.8 / sc for x in pl['items'])
    LOG.append(u'单构件标记 %d，重复标记 %d，悬空 %d，多构件 %d，障碍 %d，实测头种类 %d，吸附 %d；预计压构件 %d，标记重叠 %d，引线超限 %d；引线中位 %.0f 最大 %.0f mm'
               % (len(pl['items']), len(pl['dups']), len(pl['orphans']), pl['multi'], pl['ob'], pl['sizes'], pl['snapped'], pl['over_el'], pl['over_tag'], pl['far'],
                  lens[len(lens) // 2] if lens else 0, lens[-1] if lens else 0))
    sides = {}
    for x in pl['items']:
        sides[str(x.get('side'))] = sides.get(str(x.get('side')), 0) + 1
    LOG.append(u'路由 %d 条；标记按侧分配：%s' % (len(pl['routes']), sides))
    for x in pl['items']:
        if x['own'] in (10230650, 10231648):
            LOG.append(u'  选中管 %d：侧 %s，新头位置 (%.0f,%.0f)，管中心 (%.0f,%.0f)' % (
                x['own'], x.get('side'), x['new'][0] * 304.8, x['new'][1] * 304.8, x['a'].X * 304.8, x['a'].Y * 304.8))
    vert = sum(1 for x in pl['items'] if x.get('axis') == 'v')
    LOG.append(u'竖向管 %d 个（标记竖排），横向管 %d 个' % (vert, sum(1 for x in pl['items'] if x.get('axis') == 'h')))
    add = n['missing_elements'](view, cfg, pl['items']) if cfg['add_missing'] else []
    LOG.append(u'缺标构件 %d 个（%s）' % (len(add), u'将补标' if cfg['add_missing'] else u'未开启补标'))
    if MODE == 'apply':
        n_del, n_add = n['apply'](view, pl, cfg, add)
        LOG.append(u'已写入：整理 %d，删悬空 %d，补标 %d' % (len(pl['items']), n_del, n_add))
        # 回读：引线末端是否都落在自己构件上
        bad = n['verify'](pl['items'], 25.0 / 304.8)
        LOG.append(u'回读核对：引线末端不在自己构件上的 %d 个' % len(bad))
except Exception:
    LOG.append(traceback.format_exc())
with io.open(OUT, 'w', encoding='utf-8') as fo:
    fo.write(u'\n'.join(LOG))
