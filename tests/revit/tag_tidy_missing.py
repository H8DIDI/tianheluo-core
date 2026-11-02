# -*- coding: utf-8 -*-
"""在 Revit 里运行：构件标记一键整理的「补标」。MODE='plan' 只统计缺标；MODE='apply' 真实写入（事务组，补标→重排）。
结果 %TEMP%\\thl_tag_tidy_missing.txt"""
import io, sys, types, re, os, traceback
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI
from collections import Counter

uiapp = __revit__
uidoc = uiapp.ActiveUIDocument
d = uidoc.Document
MODE = globals().get('MODE', 'plan')
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_tag_tidy_missing.txt')
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
    LOG.append(u'视图 %s，文档 %s' % (view.Name, d.Title))
    pl = n['plan'](view, cfg)
    add = [a_ for a_ in n['missing_elements'](view, cfg, pl['items']) if a_[0].Id.Value not in pl['locked_tagged']]
    LOG.append(u'漏标核对（插件确认框里会显示的内容）：' + n['untagged_text'](view, cfg, pl).replace(u'\n', u' | '))
    LOG.append(u'组内标记 %d 个（不动，所标构件算已标）' % pl['in_group'])
    c = Counter()
    for e, tt in add:
        k = n['_fam_key'](e)
        c[(e.Category.Name, k[1][:30])] += 1
    LOG.append(u'已标 %d；缺标且同族已有标记的构件 %d 个：%s' % (len(pl['items']), len(add), c.most_common(6)))
    if MODE == 'apply':
        ni, nd, na, pl2 = n['run_all'](view, cfg)
        LOG.append(u'已写入：整理 %d，删重复/悬空 %d，补标成功 %d，补标失败 %d；整理后压构件 %d、重叠 %d、超限 %d' % (
            ni, nd, na, len(pl2.get('add_fails', [])), pl2['over_el'], pl2['over_tag'], pl2['far']))
        for f in pl2.get('add_fails', [])[:8]:
            LOG.append(u'  补标失败 %d：%s' % f)
        pl3 = n['plan'](view, cfg)
        add3 = n['missing_elements'](view, cfg, pl3['items'])
        LOG.append(u'再次统计：仍缺标 %d 个；单构件标记 %d 个' % (len(add3), len(pl3['items'])))
        bad = n['verify'](pl3['items'])
        LOG.append(u'引线末端不在构件中心的 %d 个（整理前的状态，补标后应为 0）' % len(bad))
except Exception:
    LOG.append(traceback.format_exc())
with io.open(OUT, 'w', encoding='utf-8') as fo:
    fo.write(u'\n'.join(LOG))
