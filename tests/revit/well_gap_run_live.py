# -*- coding: utf-8 -*-
"""在 Revit 里运行：对当前视图真正执行一次「井间距自动标注」（默认选项：删除不对的旧标注 + 新增全部）。会改模型，Ctrl+Z 可整次撤销。
结果：TEMP 下 thl_well_gap 文件夹的 live_run.txt。"""
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
    types_ = n['_dim_types']()
    dtype = n['_default_type'](dims, types_)
    off_ft = 10.0 * max(int(view.Scale), 1) / 304.8
    obl = sum(1 for c in plan['create'] if c.get('kind') == 'oblique')
    L.append(u'计划：删除 %d、新增 %d（斜向 %d）、已覆盖 %d 对' % (len(plan['delete']), len(plan['create']), obl, len(plan['covered'])))
    ndel, made, fails, badval = n['apply_plan'](view, wells, plan, dtype, off_ft, True, True)
    L.append(u'完成：删除 %d、新增 %d、失败 %d、数值不符 %d' % (ndel, made, len(fails), badval))
    for nm, why in fails[:20]:
        L.append(u'  失败 %s：%s' % (nm, why))
except Exception:
    L.append(traceback.format_exc())
with io.open(os.path.join(OUTD, 'live_run.txt'), 'w', encoding='utf-8') as fo:
    fo.write(chr(10).join(L))
