# -*- coding: utf-8 -*-
"""在 Revit 里运行（只规划，不写入）：实跑「一键优化排管标注」的规划部分，提示框一律回答「否」。
结果：%TEMP%\\thl_bank_optimize_dry.txt"""
import io, sys, types, re, os, traceback
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI

uiapp = __revit__
uidoc = uiapp.ActiveUIDocument
d = uidoc.Document
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_bank_optimize_dry.txt')
SRC = r'C:\KAIFA\thl\python-src'
LOG = []
MSG = []


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
        MSG.append(u'%s' % msg)
        return False


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
    LOG.append(u'悬空 %d，重复 %d，对齐 %d，补缺 %d，无参照跳过 %d' % (
        len(plan['dangling']), len(plan['dup']), len(plan['align']), len(plan['add']), plan['no_ref']))
    for a in plan['align'][:5]:
        LOG.append(u'  对齐 %s 目标 %.0f mm' % (a[0].Value, a[2] * 304.8))
    n['optimize_main']()
    LOG.append(u'提示：%s' % (MSG[-1].replace(u'\n', u' | ')[:400] if MSG else u'无'))
except Exception:
    LOG.append(traceback.format_exc())
with io.open(OUT, 'w', encoding='utf-8') as fo:
    fo.write(u'\n'.join(LOG))
