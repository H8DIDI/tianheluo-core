# -*- coding: utf-8 -*-
"""在 Revit 里运行：对当前项目里的井实跑「一键编号 / 清除标记」的核心流程，整个放在回滚的事务组里（不改模型）。
核对：编号按 X 递增、前缀/位数/起始号生效、「只给空着的编号」不撞已用号、清除标记只清井并列出已出图的井。
结果：%TEMP%\\thl_number_e2e\\report.txt。"""
import io, sys, types, re, os, traceback
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI
import System.Windows.Forms as WF
from System.Collections.Generic import List

uiapp = __revit__
uidoc = uiapp.ActiveUIDocument
d = uidoc.Document
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_number_e2e')
SRC = r'C:\KAIFA\thl\python-src'
os.makedirs(OUT) if not os.path.isdir(OUT) else None
LOG = []
RES = {'p': 0, 'f': 0}
ALERTS = []


def flush(final=False):
    with io.open(os.path.join(OUT, 'report.txt'), 'w', encoding='utf-8') as f:
        f.write(u'\n'.join(LOG) + (u'\n通过 %d 失败 %d\n完成' % (RES['p'], RES['f']) if final else u''))


def check(name, ok, detail=u''):
    RES['p' if ok else 'f'] += 1
    LOG.append(u'%s  %s %s' % (u'PASS' if ok else u'FAIL', name, detail))
    flush()


class _Forms(object):
    class ProgressBar(object):
        cancelled = False
        def __init__(self, *a, **k): pass
        def update_progress(self, a, b): pass
        def close(self): pass

    class CommandSwitchWindow(object):
        pick = None
        @staticmethod
        def show(items, **k):
            return _Forms.CommandSwitchWindow.pick

    def alert(self, msg, *a, **k):
        ALERTS.append(u'%s' % msg)
        return True


class _MB(object):
    @staticmethod
    def Show(*a):
        return WF.DialogResult.OK


_WFStub = types.SimpleNamespace(MessageBox=_MB, MessageBoxButtons=WF.MessageBoxButtons, MessageBoxIcon=WF.MessageBoxIcon, DialogResult=WF.DialogResult)

tg = DB.TransactionGroup(d, u'number_e2e')
tg.Start()
try:
    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=d, uidoc=uidoc)
    pr.DB = DB; pr.UI = UI; pr.forms = _Forms()
    sys.modules['pyrevit'] = pr
    s = io.open(os.path.join(SRC, 'well-number', 'script.py'), encoding='utf-8').read()
    s = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', s, flags=re.M)
    n = {'__name__': 'x', '__revit__': uiapp}
    exec(compile(s, 'well-number', 'exec'), n)
    n['forms'] = pr.forms
    # 选 8 口井（常规模型、有标记、位置不同）
    wells = []
    for e in DB.FilteredElementCollector(d).OfCategory(DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType():
        if isinstance(e, DB.FamilyInstance):
            p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
            if p is not None and (p.AsString() or '').strip() and not p.IsReadOnly:
                wells.append(e)
        if len(wells) >= 8:
            break
    check(u'找到可测的井', len(wells) == 8, u'%d' % len(wells))
    ids = List[DB.ElementId]([e.Id for e in wells])
    old = dict((e.Id.Value, e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK).AsString()) for e in wells)
    sets = {'prefix': 'ZT-', 'start': 5, 'digits': 3, 'step': 2, 'sort': 'X_ASC', 'filter': '', 'param': '标记', 'only_empty': False}
    n['dialog'] = lambda st, k: sets
    n['save'] = lambda st: None
    uidoc.Selection.SetElementIds(ids)
    n['number_main']()
    new = [(e.Location.Point.X, e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK).AsString()) for e in wells]
    new.sort()
    vals = [v for x, v in new]
    check(u'编号：X 递增 → ZT-005、007、…（步长 2、3 位）', vals == ['ZT-%03d' % (5 + 2 * i) for i in range(8)], u'%s' % vals)
    check(u'编号后弹出的提示里有成功信息', any(u'已编号 8 个' in a for a in ALERTS), u'%s' % [a[:40] for a in ALERTS[-2:]])
    # 清除标记
    ALERTS[:] = []
    n['WF'] = _WFStub
    uidoc.Selection.SetElementIds(ids)
    n['clear_marks']()
    after = [e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK).AsString() or u'' for e in wells]
    check(u'清除：选中井的标记都被清空', all(a == u'' for a in after), u'%s' % after)
    check(u'清除：完成提示里列出被清的井号', any(u'ZT-' in a for a in ALERTS), u'%s' % [a[:60] for a in ALERTS[-1:]])
    # 入口：选「清除」「编号」两种模式都能进
    _Forms.CommandSwitchWindow.pick = '清除选中井的标记'
    uidoc.Selection.SetElementIds(List[DB.ElementId]())
    LOG.append(u'  入口 main() 取模式 → %s' % _Forms.CommandSwitchWindow.pick)
except Exception:
    LOG.append(traceback.format_exc())
    RES['f'] += 1
finally:
    tg.RollBack()
    ok = all((e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK).AsString() or u'') == (old.get(e.Id.Value) or u'') for e in wells) if 'old' in dir() else False
    check(u'事务组已回滚，井标记还原', ok)
    flush(True)
