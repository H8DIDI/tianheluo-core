# -*- coding: utf-8 -*-
"""在 Revit 里运行：从当前项目把几根真实排管（TELCO 水平、ELE 有坡度、ELV…）复制进一个临时项目，
实跑「排管标高批量调整」的读取、换算、写入，核对设底/设顶/整体升降/拉平。当前项目只读、不改；临时项目不保存。
结果：%TEMP%\\thl_duct_elev_e2e\\report.txt。"""
import io, sys, types, re, os, traceback
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI

uiapp = __revit__
src_doc = uiapp.ActiveUIDocument.Document
app = uiapp.Application
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_duct_elev_e2e')
SRC = r'C:\KAIFA\thl\python-src'
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


class _Forms(object):
    def alert(self, msg, *a, **k):
        LOG.append(u'  [alert] ' + (u'%s' % msg)[:200].replace(u'\n', u' '))
        return True


d = app.NewProjectDocument(r'C:\ProgramData\Autodesk\RVT 2026\Templates\Chinese\Structural Analysis-DefaultCHSCHS.rte')
try:
    # ---- 选样本：每个系统取水平的和有坡度的各一根
    B = DB.BuiltInParameter
    pick = {}
    for e in DB.FilteredElementCollector(src_doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType():
        p0, p1 = e.get_Parameter(B.STRUCTURAL_BEAM_END0_ELEVATION), e.get_Parameter(B.STRUCTURAL_BEAM_END1_ELEVATION)
        if p0 is None or p1 is None:
            continue
        fam = src_doc.GetElement(e.GetTypeId()).Family.Name
        key = (fam[:5], abs(p0.AsDouble() - p1.AsDouble()) > 1e-6)
        if key not in pick:
            pick[key] = e
    LOG.append(u'样本：%s' % sorted((k[0], k[1]) for k in pick))
    ids = List = None
    from System.Collections.Generic import List as _L
    idl = _L[DB.ElementId]([e.Id for e in pick.values()])
    t = DB.Transaction(d, u'复制样本'); t.Start()
    new_ids = DB.ElementTransformUtils.CopyElements(src_doc, idl, d, DB.Transform.Identity, DB.CopyPasteOptions())
    t.Commit()
    LOG.append(u'复制了 %d 个图元' % len(list(new_ids)))

    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=d, uidoc=types.SimpleNamespace(Document=d))
    pr.DB = DB; pr.UI = UI; pr.forms = _Forms()
    sys.modules['pyrevit'] = pr
    s = io.open(os.path.join(SRC, 'duct-elev', 'script.py'), encoding='utf-8').read()
    s = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', s, flags=re.M)
    n = {'__name__': 'x', '__revit__': uiapp}
    exec(compile(s, 'duct-elev', 'exec'), n)

    els = [d.GetElement(i) for i in new_ids]
    els = [e for e in els if e is not None and e.Category is not None and e.Category.Id.Value == int(DB.BuiltInCategory.OST_StructuralFraming)]
    groups, bad = n['collect'](els)
    check(u'读取：没有读不了的', not bad, u'%s' % [(e.Id.Value, w) for e, w in bad])
    LOG.append(u'系统：%s' % sorted(groups))
    allinfo = [g for gs in groups.values() for g in gs]
    check(u'有水平的也有坡度的样本', any(g['sloped'] for g in allinfo) and any(not g['sloped'] for g in allinfo))
    for g in allinfo:
        LOG.append(u'  %s h=%.0f 底 %.0f/%.0f 顶 %.0f/%.0f 坡=%s' % (g['sys'], g['h'], g['bot'][0], g['bot'][1], g['top'][0], g['top'][1], g['sloped']))
    # 水平排管：算出的顶部高程应该等于 Revit 自己的「顶部高程」
    for g in allinfo:
        p = g['e'].get_Parameter(B.STRUCTURAL_ELEVATION_AT_TOP)
        if p is not None and p.HasValue:
            check(u'顶高程与 Revit 一致 %s' % g['sys'], abs(g['top'][0] - p.AsDouble() * FT) < 0.5, u'%.1f vs %.1f' % (g['top'][0], p.AsDouble() * FT))

    def run(v):
        plans, same, mism = n['apply_plan'](allinfo, v)
        check(u'写入校验无不符 %s' % sorted(k for k in v if v[k] is not None), not mism)
        return [n['read_bank'](g['e']) for g in allinfo]

    def exact_tops(xs):
        """斜度排管的断面高度 read_bank 只能估：这里在回滚的事务里拉平，读 Revit 自己的顶/底高程得精确高度，再算两端的管顶。"""
        out = []
        t2 = DB.Transaction(d, u'量精确高度'); t2.Start()
        try:
            for x in xs:
                e = x['e']
                o0 = e.get_Parameter(B.STRUCTURAL_BEAM_END0_ELEVATION).AsDouble()
                e.get_Parameter(B.STRUCTURAL_BEAM_END1_ELEVATION).Set(o0)
                d.Regenerate()
                h = (e.get_Parameter(B.STRUCTURAL_ELEVATION_AT_TOP).AsDouble() - e.get_Parameter(B.STRUCTURAL_ELEVATION_AT_BOTTOM).AsDouble()) * FT
                out.append((x['bot'][0] + h, x['bot'][1] + h))
        finally:
            t2.RollBack()
        return out

    # 两端管底都 5.000（斜的也被拉平）
    now = run({'bs': 5000.0, 'be': 5000.0})
    check(u'管底两端 5.000：全部水平且=5000', all(abs(x['bot'][0] - 5000) < 0.5 and abs(x['bot'][1] - 5000) < 0.5 and not x['sloped'] for x in now),
          u'%s' % [(round(x['bot'][0]), round(x['bot'][1])) for x in now])
    allinfo = now
    # 两端管顶都 4.000
    now = run({'ts': 4000.0, 'te': 4000.0})
    check(u'管顶两端 4.000：两端顶=4000', all(abs(x['top'][0] - 4000) < 0.5 and abs(x['top'][1] - 4000) < 0.5 for x in now),
          u'%s' % [(round(x['top'][0]), round(x['top'][1])) for x in now])
    allinfo = now
    # 整体升降 -0.5 m
    before = [(x['bot'][0], x['bot'][1]) for x in allinfo]
    now = run({'shift': -500.0})
    check(u'整体降 0.5 m', all(abs(x['bot'][0] - (b0[0] - 500)) < 0.5 and abs(x['bot'][1] - (b0[1] - 500)) < 0.5 for x, b0 in zip(now, before)))
    for x in now:
        p = x['e'].get_Parameter(B.STRUCTURAL_ELEVATION_AT_TOP)
        if p is not None and p.HasValue:
            check(u'改完 Revit 的顶部高程=%.0f' % x['top'][0], abs(x['top'][0] - p.AsDouble() * FT) < 0.5)
    # ---- 斜度：起点、终点各填各的
    allinfo = now
    now = run({'bs': 3000.0, 'be': 4500.0})
    check(u'起点管底=3.000、终点管底=4.500（都变成斜的）', all(abs(x['bot'][0] - 3000) < 0.5 and abs(x['bot'][1] - 4500) < 0.5 and x['sloped'] for x in now))
    allinfo = now
    now = run({'bs': 2000.0})
    check(u'只填起点：起点管底=2.000，终点保持 4.500', all(abs(x['bot'][0] - 2000) < 0.5 and abs(x['bot'][1] - 4500) < 0.5 for x in now))
    allinfo = now
    now = run({'ts': 6000.0, 'te': 6500.0})
    tops = exact_tops(now)
    check(u'斜度排管设顶：起点管顶=6.000、终点管顶=6.500（按精确断面高度）', all(abs(a1 - 6000) < 0.5 and abs(b2 - 6500) < 0.5 for a1, b2 in tops),
          u'%s' % [(round(a1), round(b2)) for a1, b2 in tops])
    allinfo = now
    now = run({'te': 5000.0})
    tops = exact_tops(now)
    check(u'只填终点管顶：终点=5.000，起点管顶保持 6.000', all(abs(b2 - 5000) < 0.5 and abs(a1 - 6000) < 0.5 for a1, b2 in tops))
    allinfo = now
    now = run({'bs': 1000.0, 'te': 3000.0})
    tops = exact_tops(now)
    check(u'起点填管底、终点填管顶（混着填）', all(abs(x['bot'][0] - 1000) < 0.5 for x in now) and all(abs(b2 - 3000) < 0.5 for a1, b2 in tops))
    # ---- write_batch（窗口「应用」背后的整批流程）：用 collect 的结果
    groups2, _ = n['collect'](els)
    infos = [g for k in groups2 for g in groups2[k]]
    note = n['write_batch'](infos, {'bs': 7000.0, 'be': 8000.0})
    LOG.append(u'  write_batch：%s' % note)
    g3, _ = n['collect'](els)
    check(u'write_batch：全部起点管底=7.000、终点=8.000', all(abs(g['bot'][0] - 7000) < 0.5 and abs(g['bot'][1] - 8000) < 0.5 for k in g3 for g in g3[k]))
    note2 = n['write_batch']([g for k in g3 for g in g3[k]], {'bs': 7000.0, 'be': 8000.0})
    check(u'write_batch：再写同样的值 → 提示本来就在目标上', u'本来就在' in note2, note2[:40])
except Exception:
    LOG.append(traceback.format_exc())
    RES['f'] += 1
finally:
    flush(True)
    try:
        d.Close(False)
    except Exception as ex:
        LOG.append(u'关临时项目失败：%s' % ex)
        flush(True)
