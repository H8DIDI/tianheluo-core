# -*- coding: utf-8 -*-
"""在 Revit 里运行：对当前平面视图实跑「排管横向中心距标注」的规划和建标注，整个放在回滚的事务里（不改模型）。
核对：找到的链数、每条链建标注成功、数值 = 排管中心线横向间距（容差 60 mm）、重复运行时已有标注被识别为已覆盖。
结果：%TEMP%\\thl_gap_bank_e2e\\report.txt。"""
import io, sys, types, re, os, traceback
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI

uiapp = __revit__
uidoc = uiapp.ActiveUIDocument
d = uidoc.Document
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_gap_bank_e2e')
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
        @staticmethod
        def show(items, **k):
            return None

    def alert(self, msg, *a, **k):
        ALERTS.append(u'%s' % msg)
        return True


tg = DB.TransactionGroup(d, u'gap_bank_e2e')
tg.Start()
try:
    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=d, uidoc=uidoc)
    pr.DB = DB; pr.UI = UI; pr.forms = _Forms()
    sys.modules['pyrevit'] = pr
    s = io.open(os.path.join(SRC, 'well-gap-dim', 'script.py'), encoding='utf-8').read()
    s = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', s, flags=re.M)
    n = {'__name__': 'x', '__revit__': uiapp}
    exec(compile(s, 'well-gap-dim', 'exec'), n)
    n['forms'] = pr.forms
    view = uidoc.ActiveView
    LOG.append(u'视图：%s' % view.Name)
    banks = n['view_banks'](view)
    cfg = n['bank_cfg']()
    chains = n['bank_plan'](view, banks, cfg)
    LOG.append(u'直排管 %d 根；规划链 %d 条；配置 %s' % (len(banks), len(chains), dict((k, v) for k, v in cfg.items() if k != u'说明')))
    check(u'规划：找到相邻平行排管链', len(chains) > 0, u'%d 条' % len(chains))
    for c in chains[:6]:
        LOG.append(u'  链 %d 根 间距 %s mm' % (len(c['ids']), [round(abs(g)) for g in c['gaps']]))
    # 真建（回滚），逐条核对数值
    types_ = n['_dim_types']()
    dtype = n['_default_type']([], types_)
    t = DB.Transaction(d, u'bank dims'); t.Start()
    ok = bad = failed = 0
    made = []
    for c in chains:
        arr = DB.ReferenceArray()
        for b in c['banks']:
            arr.Append(b['ref'])
        try:
            dm = d.Create.NewDimension(view, DB.Line.CreateBound(c['p0'], c['p1']), arr, dtype)
        except Exception as ex:
            failed += 1
            if failed <= 5:
                LOG.append(u'  建失败 %s：%s' % (c['ids'], (u'%s' % ex).splitlines()[0][:70]))
            continue
        made.append(dm)
        if dm.NumberOfSegments and dm.NumberOfSegments > 0:
            got = sorted(abs(dm.Segments.get_Item(i).Value) * 304.8 for i in range(dm.NumberOfSegments))
        else:
            got = [abs(dm.Value) * 304.8]
        want = sorted(abs(g) for g in c['gaps'])
        if len(got) == len(want) and all(abs(a - b) <= 60 for a, b in zip(got, want)):
            ok += 1
        else:
            bad += 1
            if bad <= 5:
                LOG.append(u'  数值对不上 %s：想 %s 得 %s' % (c['ids'], [round(x) for x in want], [round(x) for x in got]))
    check(u'建标注：成功建出的链', ok + bad == len(made) and len(made) > 0, u'建成 %d / %d，失败 %d' % (len(made), len(chains), failed))
    check(u'建标注：数值都等于排管中心线横向间距（≤60 mm）', bad == 0 and ok > 0, u'对 %d，不对 %d' % (ok, bad))
    d.Regenerate()
    have = n['bank_existing'](view, set(b['id'] for b in banks))
    cov = 0
    for c in chains:
        pairs = [(c['ids'][j], c['ids'][j + 1]) for j in range(len(c['ids']) - 1)]
        if all(any(a in h and b in h for h in have) for a, b in pairs):
            cov += 1
    check(u'重复运行：刚建出的标注被识别为已覆盖', cov == len(made), u'%d / %d' % (cov, len(made)))
    t.RollBack()
except Exception:
    LOG.append(traceback.format_exc())
    RES['f'] += 1
finally:
    try:
        tg.RollBack()
    except Exception:
        pass
    flush(True)
