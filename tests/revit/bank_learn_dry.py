# -*- coding: utf-8 -*-
"""在 Revit 里运行（只统计，不写入）：实跑井间距插件的「排管横向中心距标注」，提示框一律回答「否」。
核对：学到的参照编号、已标过的相邻对跳过、要补的条数。结果：%TEMP%\\thl_bank_learn_dry.txt"""
import io, sys, types, re, os, traceback
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI

uiapp = __revit__
uidoc = uiapp.ActiveUIDocument
d = uidoc.Document
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_bank_learn_dry.txt')
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
        return False          # 一律回答「否」：不写入


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
    LOG.append(u'视图 %s' % view.Name)
    idx = n['learned_index'](view)
    LOG.append(u'学到的族→参照编号：%s' % idx)
    n['bank_main']()
    LOG.append(u'插件提示：%s' % (MSG[-1].replace(u'\n', u' | ') if MSG else u'（无提示）'))
except Exception:
    LOG.append(traceback.format_exc())
with io.open(OUT, 'w', encoding='utf-8') as fo:
    fo.write(u'\n'.join(LOG))
