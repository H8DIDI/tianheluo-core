# -*- coding: utf-8 -*-
"""在 Revit 里运行（只读）：用当前视图实跑「井间距自动标注」的分析部分（找井、排管路由、旧标注分类、规划），不改模型。
结果：TEMP 下 thl_well_gap 文件夹的 probe.txt。"""
import io, sys, types, re, os, traceback
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import System.Windows.Forms as WF
import System.Drawing as SD
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
    class F2(WF.Form):
        def ShowDialog(self):
            self.Show(); self.Refresh()
            bm = SD.Bitmap(self.Width, self.Height)
            self.DrawToBitmap(bm, SD.Rectangle(0, 0, self.Width, self.Height))
            bm.Save(os.path.join(OUTD, 'ui.png'))
            self.Close()
            return WF.DialogResult.Cancel

    class _P(object):
        Form = F2
        def __getattr__(self, k):
            return getattr(WF, k)
    n['WF'] = _P()
    types_ = n['_dim_types']()
    n['show_dialog'](view, wells, plan, types_, n['_default_type'](dims, types_), edges)
except Exception:
    L.append(traceback.format_exc())
with io.open(os.path.join(OUTD, 'ui_err.txt'), 'w', encoding='utf-8') as fo:
    fo.write(chr(10).join(L))
