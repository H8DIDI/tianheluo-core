# -*- coding: utf-8 -*-
"""在 Revit 里运行（会写入）：实跑「一键优化排管标注」的执行部分（同插件 optimize_main 确认后的动作）。
整次一个事务，Ctrl+Z 可撤销。结果：%TEMP%\\thl_bank_optimize_apply.txt"""
import io, sys, types, re, os, traceback
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI

uiapp = __revit__
uidoc = uiapp.ActiveUIDocument
d = uidoc.Document
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_bank_optimize_apply.txt')
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
        return True


try:
    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=d, uidoc=uidoc)
    pr.DB = DB; pr.UI = UI; pr.forms = _Forms()
    sys.modules['pyrevit'] = pr
    s = io.open(os.path.join(SRC, 'well-gap-dim', 'script.py'), encoding='utf-8').read()
    s = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', s, flags=re.M)
    n = {'__name__': 'x', '__revit__': uiapp}
    exec(compile(s, 'well-gap-dim', 'exec'), n)
    view = uidoc.ActiveView
    cfg = dict(n['bank_cfg']())
    plan = n['opt_plan'](view, cfg)
    LOG.append(u'规划：悬空 %d，重复 %d，对齐 %d，补缺 %d' % (len(plan['dangling']), len(plan['dup']), len(plan['align']), len(plan['add'])))
    dtype = n['_default_type']([], n['_dim_types']())
    done = n['opt_apply'](view, plan, dtype)
    LOG.append(u'执行：删除 %d，对齐 %d，补缺 %d，失败 %d' % (done['del'], done['align'], done['add'], len(done['fail'])))
    for f in done['fail'][:8]:
        LOG.append(u'  ' + f)
except Exception:
    LOG.append(traceback.format_exc())
with io.open(OUT, 'w', encoding='utf-8') as fo:
    fo.write(u'\n'.join(LOG))
