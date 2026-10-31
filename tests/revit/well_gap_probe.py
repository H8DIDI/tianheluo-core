# -*- coding: utf-8 -*-
"""在 Revit 里运行（只读）：用当前视图实跑「井间距自动标注」的分析部分（找井、排管路由、旧标注分类、规划），不改模型。
结果：TEMP 下 thl_well_gap 文件夹的 probe.txt。"""
import io, sys, types, re, os, traceback
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI

uiapp = __revit__
uid = uiapp.ActiveUIDocument
d = uid.Document
view = uid.ActiveView
OUTD = os.path.join(os.environ.get('TEMP', '.'), 'thl_well_gap')
SRC = r'C:\KAIFA\thl\python-src'
os.makedirs(OUTD) if not os.path.isdir(OUTD) else None
L = []
try:
    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=d, uidoc=uid)
    pr.DB = DB; pr.UI = UI; pr.forms = types.SimpleNamespace(alert=lambda *a, **k: True)
    sys.modules['pyrevit'] = pr
    s = io.open(os.path.join(SRC, 'well-gap-dim', 'script.py'), encoding='utf-8').read()
    s = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', s, flags=re.M)
    n = {'__name__': 'x', '__revit__': uiapp}
    exec(compile(s, 'well-gap-dim', 'exec'), n)
    wells = n['collect_wells'](view)
    banks = n['collect_banks']()
    edges = n['build_edges'](wells, banks)
    dims = n['existing_dims'](view, wells)
    plan = n['make_plan'](view, wells, edges, dims, 0)
    nm = lambda ids: u'-'.join(wells[i]['mark'] for i in ids)
    L.append(u'视图 %s：井 %d、排管 %d、相邻对 %d、纯井旧标注 %d' % (view.Name, len(wells), len(banks), len(edges), len(dims)))
    L.append(u'保留 %d（覆盖 %d 对），删除 %d，新增 %d，跳过 %d' % (len(plan['keep']), len(plan['covered']), len(plan['delete']), len(plan['create']), len(plan['skip'])))
    for D, probs in plan['delete']:
        L.append(u'  删除 %s：%s' % (nm(D['ids']), u'；'.join(probs)))
    for c in plan['create'][:40]:
        L.append(u'  新增 %s' % nm(c['ids']))
    for e, why in plan['skip'][:20]:
        L.append(u'  跳过 %s：%s' % (nm(list(e)), why))
except Exception:
    L.append(traceback.format_exc())
with io.open(os.path.join(OUTD, 'probe.txt'), 'w', encoding='utf-8') as fo:
    fo.write(chr(10).join(L))
