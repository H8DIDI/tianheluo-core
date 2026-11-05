# -*- coding: utf-8 -*-
"""只读：全项目井大样图体检 = well-audit 快速体检 + 按真实井身逐视图核显示（well_view_fix）+ 每个视图尺寸数。
结果：C:\\KAIFA\\模型核对_20261104\\sheet_audit_all.json"""
import io, sys, types, re, os, json, traceback, time
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI

uiapp = __revit__
uidoc = uiapp.ActiveUIDocument
d = uidoc.Document
SRC = r'C:\KAIFA\thl\python-src'
OUTP = r'C:\KAIFA\模型核对_20261104\sheet_audit_all.json'
T0 = time.time()


def dump(o):
    with io.open(OUTP, 'w', encoding='utf-8') as f:
        f.write(json.dumps(o, ensure_ascii=False))


class _TLI(object):
    def __init__(self, item, checked=False):
        self.item = item


class _PB(object):
    cancelled = False
    def __init__(self, *a, **k): pass
    def update_progress(self, a, b): pass
    def close(self): pass


def _forms():
    f = types.SimpleNamespace()
    f.TemplateListItem = _TLI
    f.ProgressBar = _PB
    f.alert = lambda *a, **k: False
    f.CommandSwitchWindow = types.SimpleNamespace(show=lambda *a, **k: None)
    f.SelectFromList = types.SimpleNamespace(show=lambda *a, **k: None)
    return f


def _load(plugin):
    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=d, uidoc=uidoc)
    pr.DB = DB; pr.UI = UI; pr.forms = _forms()
    sys.modules['pyrevit'] = pr
    s = io.open(os.path.join(SRC, plugin, 'script.py'), encoding='utf-8').read()
    s = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', s, flags=re.M)
    n = {'__name__': 'thl_run', '__revit__': uiapp}
    exec(compile(s, plugin, 'exec'), n)
    n['WCFG_FIRST_RUN'] = False
    return n


out = {'status': 'running'}
try:
    dump(out)
    na = _load('well-audit')
    rows, stats = na['audit'](False, _PB())
    out['audit_stats'] = stats
    out['audit_rows'] = [[r[0], r[1], r[2], r[3], r[4], r[5], (int(r[6].Value) if r[6] is not None else None)] for r in rows]
    out['t_audit'] = round(time.time() - T0, 1)
    dump(out)

    ns = _load('well-sheets')
    wells = list(DB.FilteredElementCollector(d).OfCategory(DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType())
    by_mark = {}
    for w in wells:
        by_mark.setdefault(ns['_mark_of'](w), []).append(w)
    built = ns['_scan_existing_sheets'](d)
    geo = []
    for mk in sorted(built.keys(), key=ns['_well_sort_key']):
        ws = by_mark.get(mk) or (by_mark.get(mk[:-2]) if mk.endswith(u'-1') else None)
        for sid, kids in built[mk]:
            sh = d.GetElement(sid)
            if sh is None:
                continue
            rec = {'mark': mk, 'sheet': sh.SheetNumber, 'name': sh.Name, 'views': {}, 'probs': []}
            if not ws:
                rec['probs'].append(u'模型里没有井'); geo.append(rec); continue
            if len(ws) > 1:
                rec['probs'].append(u'井号对应 %d 口井' % len(ws)); geo.append(rec); continue
            w = ws[0]
            g = ns['wvf_geom'](w)
            dirs = set()
            for vid in kids:
                v = d.GetElement(vid)
                if v is None:
                    continue
                dr = ns['wvf_view_dir'](v)
                if dr is None:
                    continue
                dirs.add(dr)
                nd = len(list(DB.FilteredElementCollector(d, v.Id).OfClass(DB.Dimension)))
                p = ns['wvf_audit_view'](d, v, w, g)
                rec['views'][dr] = [nd, p]
                if p:
                    rec['probs'].append(dr + u':' + u','.join(p))
            lack = [x for x in ns['_WVF_DIRS'] if x not in dirs]
            if lack:
                rec['probs'].insert(0, u'缺' + u'、'.join(lack))
            rec['id'] = int(w.Id.Value)
            geo.append(rec)
    out['geo'] = geo
    out['status'] = 'done'
    out['t'] = round(time.time() - T0, 1)
except Exception:
    out['status'] = 'error'; out['tb'] = traceback.format_exc()
dump(out)
