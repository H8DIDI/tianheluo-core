# -*- coding: utf-8 -*-
"""在 Revit 里运行：把「排管标高批量调整」的窗口渲染成图片（不弹窗），看排版。输出 TEMP 下 thl_duct_elev_e2e 文件夹的 ui.png。当前项目只读。"""
import io, sys, types, re, os
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI
import System.Windows.Forms as WF
import System.Drawing as SD

uiapp = __revit__
d = uiapp.ActiveUIDocument.Document
SRC = r'C:\KAIFA\thl\python-src'
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_duct_elev_e2e')
pr = types.ModuleType('pyrevit')
pr.revit = types.SimpleNamespace(doc=d, uidoc=uiapp.ActiveUIDocument)
pr.DB = DB; pr.UI = UI; pr.forms = types.SimpleNamespace(alert=lambda *a, **k: True)
sys.modules['pyrevit'] = pr
s = io.open(os.path.join(SRC, 'duct-elev', 'script.py'), encoding='utf-8').read()
s = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', s, flags=re.M)
n = {'__name__': 'x', '__revit__': uiapp}
exec(compile(s, 'duct-elev', 'exec'), n)


class F2(WF.Form):
    def ShowDialog(self):
        self.Show()
        self.Refresh()
        bm = SD.Bitmap(self.Width, self.Height)
        self.DrawToBitmap(bm, SD.Rectangle(0, 0, self.Width, self.Height))
        bm.Save(os.path.join(OUT, 'ui.png'))
        self.Close()
        return WF.DialogResult.Cancel


class _WFProxy(object):
    Form = F2

    def __getattr__(self, k):
        return getattr(WF, k)


n['WF'] = _WFProxy()
st = {'mode': 'bottom', 'sys': None, 'note': u'已改 12 根（已选中，Ctrl+Z 可撤销）。示例：起点 管底 3.140 → 3.000、管顶 4.250 → 4.110；终点 管底 3.140 → 3.000、管顶 4.250 → 4.110 m'}
n['main_dialog'](st, {u'TELCO': 12}, True)
