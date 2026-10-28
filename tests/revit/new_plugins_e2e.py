# -*- coding: utf-8 -*-
"""在 Revit 里运行：临时项目里放 3 口井 + 1 根梁，实跑「井表核对」「排管井工程量表」「RFI 取证包」的核心函数并核对结果。不保存。
结果：%TEMP%\\thl_new_plugins_e2e\\report.txt。"""
import io, sys, types, re, os, math, traceback
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI

app = uidoc.Application.Application
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_new_plugins_e2e')
SRC = r'C:\KAIFA\thl\python-src'
FAM = r'\\pcb\佳佳工作\族文件\ELE-EM1-I.rfa'
os.makedirs(OUT) if not os.path.isdir(OUT) else None
LOG = []
RES = {'p': 0, 'f': 0}
FT = 304.8


def flush(final=False):
    with io.open(os.path.join(OUT, 'report.txt'), 'w', encoding='utf-8') as f:
        f.write(u'\n'.join(LOG) + (u'\n通过 %d 失败 %d\n完成' % (RES['p'], RES['f']) if final else u''))


def check(name, ok, detail=u''):
    RES['p' if ok else 'f'] += 1
    LOG.append(u'%s  %s %s' % (u'PASS' if ok else u'FAIL', name, detail))
    flush()


class _PB(object):
    cancelled = False
    def __init__(self, *a, **k): pass
    def update_progress(self, a, b): pass
    def close(self): pass


class _Forms(object):
    ProgressBar = _PB
    def alert(self, msg, *a, **k):
        LOG.append(u'  [alert] ' + (u'%s' % msg)[:160].replace(u'\n', u' '))
        return True
    def __getattr__(self, k):
        return type(k, (object,), {})


d = app.NewProjectDocument(r'C:\ProgramData\Autodesk\RVT 2026\Templates\Chinese\Structural Analysis-DefaultCHSCHS.rte')


def load(pid):
    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=d, uidoc=types.SimpleNamespace(Document=d))
    pr.DB = DB; pr.UI = UI; pr.forms = _Forms()
    sys.modules['pyrevit'] = pr
    src = io.open(os.path.join(SRC, pid, 'script.py'), encoding='utf-8').read()
    src = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', src, flags=re.M)
    n = {'__name__': 'x', '__revit__': uidoc.Application}
    exec(compile(src, pid, 'exec'), n)
    return n


try:
    t = DB.Transaction(d, u'族'); t.Start(); d.LoadFamily(FAM); t.Commit()
    sym = [s for s in DB.FilteredElementCollector(d).OfClass(DB.FamilySymbol) if s.FamilyName == 'ELE-EM1-I'][0]
    lvl = DB.FilteredElementCollector(d).OfClass(DB.Level).FirstElement()
    beams = [s for s in DB.FilteredElementCollector(d).OfClass(DB.FamilySymbol).OfCategory(DB.BuiltInCategory.OST_StructuralFraming)]
    t = DB.Transaction(d, u'场景'); t.Start()
    if not sym.IsActive:
        sym.Activate()
    if not beams[0].IsActive:
        beams[0].Activate()
    wells = []
    for i, mk in enumerate((u'A-1', u'A-2', u'A-3')):
        w = d.Create.NewFamilyInstance(DB.XYZ(30.0 * i, 0, 0), sym, lvl, DB.Structure.StructuralType.NonStructural)
        w.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK).Set(mk)
        wells.append(w)
    beam = d.Create.NewFamilyInstance(DB.Line.CreateBound(DB.XYZ(0, 5, 0), DB.XYZ(20, 5, 0)), beams[0], lvl, DB.Structure.StructuralType.Beam)
    t.Commit()
    tname = wells[0].Symbol.Name
    m = re.search(u'(\\d{3,5})\\D+(\\d{3,5})', tname)
    LOG.append(u'井类型 %s ；梁 %s' % (tname, beams[0].Name))
    # ---- 井表核对
    n = load('well-list-check')
    mod_wells = n['model_wells']()
    check(u'井表核对 模型井识别', sorted(mod_wells) == [u'A-1', u'A-2', u'A-3'], u'%s' % sorted(mod_wells))
    L = float(m.group(1)) if m else 3000.0
    W = float(m.group(2)) if m else 3000.0
    n['xlsx_write'](os.path.join(OUT, u'清单.xlsx'), [(u'预制混凝土井', [
        [u'序号', u'名称', u'规格', u'单位'],
        [1, u'11KV混凝土井\n编号:A-001', u'JC9C-%d(L)x%d(W)x3500(D)' % (L, W), u'座'],
        [2, u'11KV混凝土井\n编号:A-002', u'JC9C-%d(L)x%d(W)x3500(D)' % (L + 500, W), u'座'],
        [3, u'11KV混凝土井\n编号:A-9', u'JC9C-%d(L)x%d(W)x2000(D)' % (L, W), u'座']], {})])
    table, dup = n['parse_list'](os.path.join(OUT, u'清单.xlsx'))
    check(u'井表核对 解析清单（A-001 归一成 A-1）', sorted(table) == [u'A-1', u'A-2', u'A-9'], u'%s' % sorted(table))
    rows = n['compare'](table, mod_wells)
    kinds = sorted((r[0], r[1]) for r in rows)
    LOG.append(u'  核对结果：%s' % kinds)
    check(u'井表核对 报「清单有、模型没有」A-9', (u'清单有、模型没有', u'A-9') in kinds)
    check(u'井表核对 报「长宽对不上」A-2', (u'长宽对不上', u'A-2') in kinds)
    check(u'井表核对 报「模型有、清单没有」A-3', (u'模型有、清单没有', u'A-3') in kinds)
    # ---- 工程量表
    n = load('duct-boq')
    data = n['collect']([])
    sheets, st = n['build'](*data)
    check(u'工程量 井 3 口、排管梁 1 根、总长 6.096 m（20 ft）', st[0] == 3 and st[1] == 1 and abs(st[5] - 6.096) < 0.01, u'%s' % (st,))
    p = os.path.join(OUT, u'工程量.xlsx')
    n['xlsx_write'](p, sheets)
    check(u'工程量 写出 Excel', os.path.exists(p) and os.path.getsize(p) > 2000)
    # ---- RFI 取证
    n = load('rfi-pack')
    rows, lines = n['build_rows']([wells[0], beam])
    check(u'RFI 取证 井 1 行 + 梁 2 行（起点终点）+ 表头', len(rows) == 4, u'%d 行' % len(rows))
    check(u'RFI 取证 素材文字含 E/N/RL', len(lines) == 2 and u'RL' in lines[0] and u'终点' in lines[1], lines[1][:90] if len(lines) > 1 else u'')
except Exception:
    LOG.append(traceback.format_exc())
finally:
    try:
        d.Close(False)
    except Exception:
        pass
    sys.modules.pop('pyrevit', None)
flush(True)
