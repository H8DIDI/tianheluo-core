# -*- coding: utf-8 -*-
"""在 Revit 里运行：临时项目里放两口井（一口平移、一口转 30°），按井展开图的做法生成 平面 + 南北西东 视图并放上图纸，
然后用最新的「井大样变动同步」检查、对齐、重标，核对：检测到变动、裁剪框围住井、有新尺寸、再检查已无变动。
不保存，最后关闭。结果：%TEMP%\\thl_well_sync_e2e\\report.txt。"""
import io, sys, types, re, os, math, traceback
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI

app = uidoc.Application.Application
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_well_sync_e2e')
FAM = globals().get('FAM_PATH') or r'\\pcb\佳佳工作\族文件\ELE-EM1-I.rfa'
if not os.path.isdir(OUT):
    os.makedirs(OUT)
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
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Forms(object):
    ProgressBar = _PB
    def alert(self, msg, *a, **k):
        LOG.append(u'  [alert] ' + (u'%s' % msg)[:200].replace(u'\n', u' '))
        return True
    def __getattr__(self, k):
        class _X(object):
            @staticmethod
            def show(*a, **kw):
                return None
        return _X


class _Sel(object):
    def GetElementIds(self):
        return []


d = app.NewProjectDocument(r'C:\ProgramData\Autodesk\RVT 2026\Templates\Chinese\DefaultCHSCHS.rte') \
    if os.path.exists(r'C:\ProgramData\Autodesk\RVT 2026\Templates\Chinese\DefaultCHSCHS.rte') else app.NewProjectDocument(DB.UnitSystem.Metric)
try:
    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=d, uidoc=types.SimpleNamespace(Document=d, Selection=_Sel(), ActiveView=None))
    pr.DB = DB; pr.UI = UI; pr.forms = _Forms()
    sys.modules['pyrevit'] = pr
    SRC = r'C:\KAIFA\thl\python-src'
    src = io.open(os.path.join(SRC, 'well-sync', 'script.py'), encoding='utf-8').read()
    src = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', src, flags=re.M)
    n = {'__name__': 'wsync', '__revit__': uidoc.Application}
    exec(compile(src, 'well-sync', 'exec'), n)
    n['wcfg_cache_save'] = lambda c: None          # 不写用户真实的位置缓存
    # --- 场景
    _t0 = DB.Transaction(d, u'载入族'); _t0.Start()
    fam_ok = d.LoadFamily(FAM)
    _t0.Commit()
    LOG.append(u'LoadFamily=%s exists=%s syms=%s' % (fam_ok, os.path.exists(FAM), [x.FamilyName for x in DB.FilteredElementCollector(d).OfClass(DB.FamilySymbol)][:8]))
    sym = [s for s in DB.FilteredElementCollector(d).OfClass(DB.FamilySymbol) if s.FamilyName == os.path.splitext(os.path.basename(FAM))[0]][0]
    lvl = DB.FilteredElementCollector(d).OfClass(DB.Level).FirstElement()
    tdetail = [x for x in DB.FilteredElementCollector(d).OfClass(DB.ViewFamilyType) if x.ViewFamily == DB.ViewFamily.Detail][0]
    tplan = [x for x in DB.FilteredElementCollector(d).OfClass(DB.ViewFamilyType) if x.ViewFamily == DB.ViewFamily.FloorPlan][0]
    tb = DB.FilteredElementCollector(d).OfCategory(DB.BuiltInCategory.OST_TitleBlocks).WhereElementIsElementType().FirstElement()
    M = 100.0 / FT
    FO = 100.0 / FT
    DEPTH = 1100.0 / FT
    state = {}

    def make_views(well, mark):
        fp = n['footprint'](well)
        cx, cy, dx, dy = fp
        bb = well.get_BoundingBox(None)
        cz = (bb.Min.Z + bb.Max.Z) / 2.0
        dz = (bb.Max.Z - bb.Min.Z) / 2.0
        plan = DB.ViewPlan.Create(d, tplan.Id, lvl.Id)
        plan.CropBoxActive = True
        cb = DB.BoundingBoxXYZ()
        cb.Min = DB.XYZ(cx - dx - M, cy - dy - M, bb.Min.Z - M)
        cb.Max = DB.XYZ(cx + dx + M, cy + dy + M, bb.Max.Z + M)
        plan.CropBox = cb
        plan.Name = u'平面_AG-%s-abcd' % mark
        vs = [plan]
        for nm, look, up, right, half, zn in ((u'北', (0, -1, 0), (0, 0, 1), (1, 0, 0), dx, -dy - FO), (u'南', (0, 1, 0), (0, 0, 1), (-1, 0, 0), dx, -dy - FO),
                                             (u'西', (1, 0, 0), (0, 0, 1), (0, 1, 0), dy, -dx - FO), (u'东', (-1, 0, 0), (0, 0, 1), (0, -1, 0), dy, -dx - FO)):
            t = DB.Transform.Identity
            t.Origin = DB.XYZ(cx, cy, cz)
            t.BasisZ = DB.XYZ(*look); t.BasisY = DB.XYZ(*up); t.BasisX = DB.XYZ(*right)
            sb = DB.BoundingBoxXYZ()
            sb.Transform = t
            sb.Min = DB.XYZ(-half - FO, -dz - FO, zn)
            sb.Max = DB.XYZ(half + FO, dz + FO, zn + DEPTH)
            v = DB.ViewSection.CreateDetail(d, tdetail.Id, sb)
            v.Name = u'%s_AG-%s-abcd' % (nm, mark)
            vs.append(v)
        sh = DB.ViewSheet.Create(d, tb.Id)
        sh.SheetNumber = u'T-' + mark
        sh.Name = u'井大样_' + mark
        d.Regenerate()
        for i, v in enumerate(vs):
            DB.Viewport.Create(d, sh.Id, v.Id, DB.XYZ(0.3 + 0.35 * (i % 3), 0.3 + 0.3 * (i // 3), 0))
        return sh

    def setup():
        if not sym.IsActive:
            sym.Activate()
        out = []
        for i, mk in enumerate((u'A-1', u'A-2', u'A-3', u'A-4')):
            w = d.Create.NewFamilyInstance(DB.XYZ(30.0 * i, 0, 0), sym, lvl, DB.Structure.StructuralType.NonStructural)
            w.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK).Set(mk)
            out.append(w)
        d.Regenerate()
        return out
    t = DB.Transaction(d, u'场景'); t.Start()
    wells = setup()
    t.Commit()
    t = DB.Transaction(d, u'视图'); t.Start()
    sheets = [make_views(w, mk) for w, mk in zip(wells[:3], (u'A-1', u'A-2', u'A-3'))]
    t.Commit()
    LOG.append(u'族 %s ；井 3 口，图纸 3 张，井外廓 %s' % (os.path.basename(FAM), [u'%.0fx%.0f' % (2 * f[2] * FT, 2 * f[3] * FT) for f in (n['footprint'](w) for w in wells)]))
    n['_apply_settings']()
    n['_load_sheet_caches']()
    rules = n['_rules']()
    wm = n['_well_map']()
    shs = [s for s in DB.FilteredElementCollector(d).OfClass(DB.ViewSheet) if n['_is_well_sheet'](s)]
    shs.sort(key=lambda s: s.SheetNumber)
    rows = n['scan'](shs, wm, rules)
    check(u'未动时三张都没变动', all(r['status'] == 'ok' for r in rows), u'%s' % [r['status'] for r in rows])
    # --- 动井：A2 平移 3 m，A3 转 30°
    t = DB.Transaction(d, u'动井'); t.Start()
    DB.ElementTransformUtils.MoveElement(d, wells[1].Id, DB.XYZ(3000 / FT, 1500 / FT, 0))
    loc = wells[2].Location.Point
    DB.ElementTransformUtils.RotateElement(d, wells[2].Id, DB.Line.CreateBound(loc, loc + DB.XYZ.BasisZ), math.radians(30))
    t.Commit()
    rows = n['scan'](shs, wm, rules)
    st = dict((r['mark'], r['status']) for r in rows)
    check(u'A1 没变、A2 A3 检测到变动', st == {u'A-1': 'ok', u'A-2': 'changed', u'A-3': 'changed'}, u'%s' % st)
    rA2 = [r for r in rows if r['mark'] == u'A-2'][0]
    rA3 = [r for r in rows if r['mark'] == u'A-3'][0]
    check(u'A2 判为移动（约 3354 mm）', rA2.get('moved') and abs(math.hypot(*rA2['shift']) * FT - 3354) < 20, u'%.0f' % (math.hypot(*rA2['shift']) * FT))
    check(u'A3 判为大小/方向变化', rA3.get('resized'), n['_describe'](rA3))
    # --- 同步
    stats, failed = {}, []
    for r in (rA2, rA3):
        ok = n['step'](u'对齐', lambda r=r: n['reframe'](r, rules))
        check(u'%s 视图对齐事务提交' % r['mark'], ok)
        for v in r['views'].values():
            n['annotate_view'](v, r['mark'], stats, failed)
    rows2 = n['scan'](shs, n['_well_map'](), rules)
    st2 = dict((r['mark'], r['status']) for r in rows2)
    check(u'同步后再检查：三张都没变动', all(v == 'ok' for v in st2.values()), u'%s' % st2)
    # 剖面：剖切线在井外墙外 100mm，裁剪范围覆盖井宽
    for r in (rA2, rA3):
        cx, cy, dx, dy = n['footprint'](r['well'])
        v = r['views']['S']
        w = n['world_box'](v)
        okx = w[0] <= cx - dx + 1e-3 and w[1] >= cx + dx - 1e-3
        check(u'%s 南立面裁剪覆盖井宽' % r['mark'], okx, u'世界 x[%.0f,%.0f] 井 x[%.0f,%.0f]' % (w[0] * FT, w[1] * FT, (cx - dx) * FT, (cx + dx) * FT))
    nd = sum(len(list(DB.FilteredElementCollector(d, v.Id).OfClass(DB.Dimension))) for r in (rA2, rA3) for v in r['views'].values())
    check(u'重标后生成了尺寸（井没有排管，至少有总尺寸）', nd > 0, u'%d 道；失败 %s' % (nd, failed[:3]))

    # --- 井大样体检
    srcA = io.open(os.path.join(SRC, 'well-audit', 'script.py'), encoding='utf-8').read()
    srcA = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', srcA, flags=re.M)
    na = {'__name__': 'waudit', '__revit__': uidoc.Application}
    exec(compile(srcA, 'well-audit', 'exec'), na)
    na['_apply_settings']()
    rowsA, statsA = na['audit'](True, _PB())
    cats = dict()
    for r in rowsA:
        cats.setdefault(r[1], []).append(r[2])
    LOG.append(u'体检问题：%s' % u'; '.join(u'%s %s' % (k, v) for k, v in cats.items()))
    check(u'体检：统计 4 口井、3 张图纸', statsA.get(u'井') == 4 and statsA.get(u'图纸') == 3, u'%s' % statsA)
    check(u'体检：A-4 没有图纸', u'A-4' in cats.get(u'井没有图纸', []))
    check(u'体检：同步过的井不报「视图没对着井」', not cats.get(u'视图没对着井'))
    # 再动一次 A-1，不同步：体检应报出来
    t = DB.Transaction(d, u'再动'); t.Start()
    DB.ElementTransformUtils.MoveElement(d, wells[0].Id, DB.XYZ(2000 / FT, 0, 0))
    t.Commit()
    rowsB, _s = na['audit'](False, _PB())
    check(u'体检：新挪动的 A-1 报「视图没对着井」', any(r[1] == u'视图没对着井' and r[2] == u'A-1' for r in rowsB))
except Exception:
    LOG.append(traceback.format_exc())
finally:
    try:
        d.Close(False)
    except Exception:
        pass
    sys.modules.pop('pyrevit', None)
flush(True)
