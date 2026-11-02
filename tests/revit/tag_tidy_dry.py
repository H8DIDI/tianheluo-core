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
    LOG.append(u'单构件标记 %d，悬空 %d，多构件 %d；预计重叠 %d 对，压构件 %d 个，引线超限 %d 个'
               % (len(pl['items']), len(pl['orphans']), pl['skipped'], pl['overlap'], pl['hit'], pl['far']))
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
