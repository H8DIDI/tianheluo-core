# -*- coding: utf-8 -*-
"""原位修复 sheet_audit_all.json 里有显示问题的井大样图（well_view_fix.wvf_fix_sheet），每张图一个事务。
结果：C:\\KAIFA\\模型核对_20261104\\sheet_fix_log.json"""
import io, json, traceback, time
import Autodesk.Revit.DB as DB
import System

uiapp = __revit__
d = uiapp.ActiveUIDocument.Document
OUTP = r'C:\KAIFA\模型核对_20261104\sheet_fix_log.json'
src = io.open(r'C:\KAIFA\thl\python-src\_shared\well_view_fix.py', encoding='utf-8').read()
ns = {'__name__': 'wvf', 'FT_MM': 304.8}
exec(compile(src, 'well_view_fix.py', 'exec'), ns)
A = json.load(io.open(r'C:\KAIFA\模型核对_20261104\sheet_audit_all.json', encoding='utf-8'))
todo = [g for g in A['geo'] if g['probs'] and g.get('id')]
CROP, FO, FD = 100 / 304.8, 100 / 304.8, 1100 / 304.8
log = {'status': 'running', 'done': [], 'err': []}


def dump():
    with io.open(OUTP, 'w', encoding='utf-8') as f:
        f.write(json.dumps(log, ensure_ascii=False))


sheets = {s.SheetNumber: s for s in DB.FilteredElementCollector(d).OfClass(DB.ViewSheet)}
for g in todo:
    sh = sheets.get(g['sheet'])
    w = d.GetElement(DB.ElementId(g['id']))
    if sh is None or w is None:
        log['err'].append([g['sheet'], g['mark'], u'找不到图纸或井']); continue
    t = DB.Transaction(d, u'井大样原位修复 %s %s' % (g['sheet'], g['mark']))
    try:
        t.Start()
        n, left = ns['wvf_fix_sheet'](d, sh, w, CROP, FO, FD)
        st = t.Commit()
        log['done'].append([g['sheet'], g['mark'], n, left, str(st)])
    except Exception:
        try:
            if t.HasStarted() and not t.HasEnded(): t.RollBack()
        except Exception:
            pass
        log['err'].append([g['sheet'], g['mark'], traceback.format_exc()[-400:]])
    dump()
log['status'] = 'done'
dump()
