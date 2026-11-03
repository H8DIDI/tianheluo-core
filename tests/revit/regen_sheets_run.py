# -*- coding: utf-8 -*-
"""在 Revit 里运行：对指定的井实跑「一键井展开图（全部重做）→ 一键井大样标注 → 井规格表」，界面选择用桩代替。
参数：C:\\KAIFA\\regen_req.json  {"wells": [井 ElementId...], "titleblock": 图框类型Id, "template": 视图样板Id}
结果：C:\\KAIFA\\regen_out.txt。会改模型（与手动点三个按钮一样），每个插件各自一次撤销。"""
import io, sys, types, re, os, json, traceback
import clr
clr.AddReference('System.Windows.Forms')
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI
import System.Windows.Forms as WF
from System.Collections.Generic import List

uiapp = __revit__
uidoc = uiapp.ActiveUIDocument
d = uidoc.Document
SRC = r'C:\KAIFA\thl\python-src'
REQ = json.loads(io.open(r'C:\KAIFA\regen_req.json', encoding='utf-8').read())
LOG = []


def flush():
    with io.open(r'C:\KAIFA\regen_out.txt', 'w', encoding='utf-8') as fo:
        fo.write(u'\n'.join(LOG))


def log(s):
    LOG.append(u'%s' % s)
    flush()


class _TLI(object):
    def __init__(self, item, checked=False):
        self.item = item


class _PB(object):
    cancelled = False
    def __init__(self, *a, **k): pass
    def update_progress(self, a, b): pass
    def close(self): pass


def _make_forms(pick_cmd, pick_list):
    f = types.SimpleNamespace()
    f.TemplateListItem = _TLI
    f.ProgressBar = _PB
    f.alert = lambda msg, *a, **k: (log(u'[alert] ' + u'%s' % msg), False)[1]
    f.pick_folder = lambda *a, **k: None
    f.CommandSwitchWindow = types.SimpleNamespace(show=lambda items, **k: pick_cmd(list(items), k))
    f.SelectFromList = types.SimpleNamespace(show=lambda items, **k: pick_list(list(items), k))
    return f


class _MB(object):
    @staticmethod
    def Show(*a):
        btn = a[2] if len(a) > 2 else None
        return WF.DialogResult.Yes if btn in (WF.MessageBoxButtons.YesNo, WF.MessageBoxButtons.YesNoCancel) else WF.DialogResult.OK


_WFStub = types.SimpleNamespace(MessageBox=_MB, MessageBoxButtons=WF.MessageBoxButtons,
                                MessageBoxIcon=WF.MessageBoxIcon, DialogResult=WF.DialogResult)


def _load(plugin, forms_stub):
    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=d, uidoc=uidoc)
    pr.DB = DB; pr.UI = UI; pr.forms = forms_stub
    sys.modules['pyrevit'] = pr
    s = io.open(os.path.join(SRC, plugin, 'script.py'), encoding='utf-8').read()
    s = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', s, flags=re.M)
    n = {'__name__': 'thl_run', '__revit__': uiapp}
    exec(compile(s, plugin, 'exec'), n)
    n['WCFG_FIRST_RUN'] = False
    for k in ('_SUW', 'WF'):
        if k in n:
            n[k] = _WFStub
    return n


def _mark(e):
    p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
    return p.AsString().strip() if (p and p.AsString()) else u''


try:
    wells = [d.GetElement(DB.ElementId(int(i))) for i in REQ['wells']]
    marks = [_mark(w) for w in wells]
    log(u'井：' + u'、'.join(marks))
    tb = d.GetElement(DB.ElementId(int(REQ['titleblock'])))
    tpl = d.GetElement(DB.ElementId(int(REQ['template'])))

    # ---- 1. 一键井展开图：选中这些井 → 全部重做（替换已有图纸）----
    def cmd1(items, k):
        for want in (u'全部重做', u'选择图框'):
            for it in items:
                if want in it:
                    return it
        log(u'[cmd?] %s' % items)
        return None

    def lst1(items, k):
        t = k.get('title', u'')
        if u'图框' in t:
            return [x.item for x in items if x.item.Id == tb.Id][0]
        if u'样板' in t:
            return [x.item for x in items if x.item.Id == tpl.Id]
        log(u'[list?] %s' % t)
        return None
    STEPS = REQ.get('steps') or ['sheets', 'annotate', 'spec']
    n1 = _load('well-sheets', _make_forms(cmd1, lst1))
    n1['rules_preview_dialog'] = lambda w: n1['load_rules']()
    if 'sheets' in STEPS:
        uidoc.Selection.SetElementIds(List[DB.ElementId]([w.Id for w in wells]))
        n1['main']()
        uidoc.Selection.SetElementIds(List[DB.ElementId]())

    # 新图纸：按井号找
    n_scan = n1['_scan_existing_sheets'](d)
    sheets = []
    for mk in marks:
        for sid, kids in n_scan.get(mk, []):
            sh = d.GetElement(sid)
            if sh is not None and sh not in sheets:
                sheets.append(sh)
                log(u'图纸 %s %s：%d 个视图' % (sh.SheetNumber, sh.Name, len(kids)))

    # ---- 2. 一键井大样标注：选中这些图纸 → 只标这些 ----
    def cmd2(items, k):
        for it in items:
            if it.startswith(u'项目浏览器里选中') or (len(sheets) == 1 and it.startswith(u'只标注当前图纸')):
                return it
        log(u'[cmd?] %s' % items)
        return None
    if 'annotate' in STEPS:
      n2 = _load('well-annotate', _make_forms(cmd2, lambda items, k: None))
      uidoc.Selection.SetElementIds(List[DB.ElementId]([s.Id for s in sheets]))
      n2['main']()
      uidoc.Selection.SetElementIds(List[DB.ElementId]())

    # ---- 3. 井规格表：只做这些图纸 ----
    if 'spec' not in STEPS:
        sheets = []
    n3 = _load('well-spec-table', _make_forms(lambda i, k: None, lambda i, k: None))
    cfg = n3['load_cfg']()
    mks = n3['model_marks'](cfg)
    todo = [(s, n3['_mark_of_sheet'](s, cfg)) for s in sheets]
    todo = [t for t in todo if t[1]]
    try:
        n3['ensure_group_param'](cfg)
    except Exception as ex:
        cfg = dict(cfg); cfg['group_param'] = ''
        log(u'分组参数：%s' % ex)
    stat, fails, miss_f, cancelled = n3['run_all'](todo, cfg, lambda i, n: False, mks)
    log(u'规格表：%s 失败 %s' % (stat, fails))
    log(u'完成')
except Exception:
    log(traceback.format_exc())
