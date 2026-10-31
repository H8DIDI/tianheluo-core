# -*- coding: utf-8 -*-
"""在 Revit 里运行：把当前项目里一片区域的真实井和排管复制进临时项目，实跑「井间距自动标注」：
找井、路由相邻、手工造一条对的和一条错的标注、规划（保留/删除/新增）、执行、数值核对、再跑一遍应当没有可做的。
当前项目只读，临时项目不保存。结果：TEMP 下 thl_well_gap 文件夹的 report.txt。"""
import io, sys, types, re, os, traceback
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI
from System.Collections.Generic import List

uiapp = __revit__
src_doc = uiapp.ActiveUIDocument.Document
src_view = uiapp.ActiveUIDocument.ActiveView
app = uiapp.Application
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_well_gap')
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


def mk(e):
    p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
    return (p.AsString() or u'') if p else u''


tmp = app.NewProjectDocument(r'C:\ProgramData\Autodesk\RVT 2026\Templates\Chinese\Structural Analysis-DefaultCHSCHS.rte')
try:
    # ---- 选区域：A-39…A-43 一带
    marks = (u'A-39', u'A-40', u'A-41', u'A-42', u'A-43')
    core = [e for e in DB.FilteredElementCollector(src_doc, src_view.Id).OfCategory(DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType() if mk(e) in marks]
    xs = [e.Location.Point.X for e in core]; ys = [e.Location.Point.Y for e in core]
    pad = 8.0 / 0.3048
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    ids = []
    nwell = 0
    for e in DB.FilteredElementCollector(src_doc, src_view.Id).OfCategory(DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType():
        if isinstance(e, DB.FamilyInstance) and mk(e):
            p = e.Location.Point
            if x0 <= p.X <= x1 and y0 <= p.Y <= y1:
                ids.append(e.Id); nwell += 1
    nbank = 0
    for e in DB.FilteredElementCollector(src_doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType():
        bb = e.get_BoundingBox(None)
        if bb is not None and bb.Max.X >= x0 and bb.Min.X <= x1 and bb.Max.Y >= y0 and bb.Min.Y <= y1:
            ids.append(e.Id); nbank += 1
    LOG.append(u'区域内：井 %d 口、排管 %d 根（复制进临时项目）' % (nwell, nbank))
    t = DB.Transaction(tmp, u'复制'); t.Start()
    DB.ElementTransformUtils.CopyElements(src_doc, List[DB.ElementId](ids), tmp, DB.Transform.Identity, DB.CopyPasteOptions())
    t.Commit()

    # ---- 临时项目里建平面视图，视图范围放大
    lvl = DB.FilteredElementCollector(tmp).OfClass(DB.Level).FirstElement()
    vft = [x for x in DB.FilteredElementCollector(tmp).OfClass(DB.ViewFamilyType) if x.ViewFamily == DB.ViewFamily.FloorPlan][0]
    t = DB.Transaction(tmp, u'视图'); t.Start()
    view = DB.ViewPlan.Create(tmp, vft.Id, lvl.Id)
    view.Scale = 150
    vr = view.GetViewRange()
    vr.SetOffset(DB.PlanViewPlane.TopClipPlane, 400.0)
    vr.SetOffset(DB.PlanViewPlane.CutPlane, 399.0)
    vr.SetOffset(DB.PlanViewPlane.BottomClipPlane, -400.0)
    vr.SetOffset(DB.PlanViewPlane.ViewDepthPlane, -400.0)
    view.SetViewRange(vr)
    t.Commit()

    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=tmp, uidoc=types.SimpleNamespace(Document=tmp, ActiveView=view))
    pr.DB = DB; pr.UI = UI; pr.forms = types.SimpleNamespace(alert=lambda *a, **k: True)
    sys.modules['pyrevit'] = pr
    s = io.open(os.path.join(SRC, 'well-gap-dim', 'script.py'), encoding='utf-8').read()
    s = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', s, flags=re.M)
    n = {'__name__': 'x', '__revit__': uiapp}
    exec(compile(s, 'well-gap-dim', 'exec'), n)

    wells = n['collect_wells'](view)
    check(u'临时视图里找到井', len(wells) == nwell, u'%d / %d' % (len(wells), nwell))
    banks = n['collect_banks']()
    edges = n['build_edges'](wells, banks)
    nm = lambda ids_: u'-'.join(wells[i]['mark'] for i in ids_)
    LOG.append(u'井 %d、排管 %d、相邻对 %d：%s' % (len(wells), len(banks), len(edges), sorted((wells[a]['mark'], wells[b]['mark']) for a, b in edges)[:12]))
    check(u'找到路由相邻的井对', len(edges) >= 3)
    plan0 = n['make_plan'](view, wells, edges, n['existing_dims'](view, wells), 0)
    check(u'没有旧标注时：新增 > 0、删除 = 0', len(plan0['create']) > 0 and len(plan0['delete']) == 0, u'新增 %d 跳过 %d' % (len(plan0['create']), len(plan0['skip'])))
    for c in plan0['create'][:8]:
        LOG.append(u'  规划新增 %s' % nm(c['ids']))

    types_ = n['_dim_types']()
    dtype = types_[0] if types_ else None
    check(u'有可用的线性标注类型', dtype is not None)
    off = 10 * 150 / FT

    # ---- 手工造：一条对的（规划里的第一条）+ 一条错的（两口没排管连接的井）+ 一条参照井外沿的（不是中心面）
    good_item = plan0['create'][0]
    wrong_pair = None
    ks = sorted(wells)
    for i in range(len(ks)):
        for j in range(i + 1, len(ks)):
            a_, b_ = ks[i], ks[j]
            if (min(a_, b_), max(a_, b_)) in edges:
                continue
            for ax in ((1.0, 0.0), (0.0, 1.0)):
                if n['_center_ref'](wells[a_], ax) is not None and n['_center_ref'](wells[b_], ax) is not None and                         abs(n['_dot'](wells[a_]['c'], ax) - n['_dot'](wells[b_]['c'], ax)) * FT > 3000:
                    wrong_pair = {'ax': ax, 'ids': sorted([a_, b_], key=lambda x: n['_dot'](wells[x]['c'], ax))}
                    break
            if wrong_pair:
                break
        if wrong_pair:
            break
    check(u'找到一对没有排管连接的井（造错误标注用）', wrong_pair is not None)
    t = DB.Transaction(tmp, u'造旧标注'); t.Start()
    try:
        d_good, _ = n['create_dim'](view, wells, good_item, dtype, off)
        d_wrong, e2 = n['create_dim'](view, wells, wrong_pair, dtype, off)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    check(u'手工标注造出来了', d_good is not None and d_wrong is not None, u'%s' % e2)
    gid, wid = d_good.Id, d_wrong.Id

    # ---- 再规划
    dims = n['existing_dims'](view, wells)
    LOG.append(u'纯井旧标注 %d 条：%s' % (len(dims), [(nm(D['ids']), D['problems']) for D in dims]))
    plan = n['make_plan'](view, wells, edges, dims, 0)
    check(u'对的标注被保留（不重复新增）', any(D['dim'].Id == gid for D in plan['keep']) and not any(c['ids'] == good_item['ids'] for c in plan['create']))
    check(u'错的标注（两井无排管连接）被判删除', any(D['dim'].Id == wid for D, _ in plan['delete']))
    check(u'新增数 = 初始新增数 − 1（那条对的已覆盖，链式拆分可能多一点）', len(plan['create']) >= len(plan0['create']) - 1, u'%d vs %d' % (len(plan['create']), len(plan0['create'])))

    ndel, made, fails, badval = n['apply_plan'](view, wells, plan, dtype, off, True, True)
    LOG.append(u'执行：删 %d、新增 %d、失败 %s、数值不符 %d' % (ndel, made, fails, badval))
    check(u'删除了错的、保留了对的', tmp.GetElement(wid) is None and tmp.GetElement(gid) is not None and ndel == 1)
    check(u'新增数量与规划一致、无失败', made == len(plan['create']) and not fails, u'%d / %d' % (made, len(plan['create'])))
    check(u'所有新标注数值 = 井中心距', badval == 0)
    # 抽查几条标注的数值
    for dm in list(DB.FilteredElementCollector(tmp, view.Id).OfClass(DB.Dimension))[:6]:
        if dm.NumberOfSegments and dm.NumberOfSegments > 0:
            LOG.append(u'  标注 %d：分段 %s' % (dm.Id.Value, [round(dm.Segments.get_Item(i).Value * FT / 1000, 3) for i in range(dm.NumberOfSegments)]))
        else:
            LOG.append(u'  标注 %d：%.3f m' % (dm.Id.Value, dm.Value * FT / 1000))
    # ---- 再跑一遍：应该没有要删的、没有要新增的
    dims2 = n['existing_dims'](view, wells)
    plan2 = n['make_plan'](view, wells, edges, dims2, 0)
    check(u'再跑一遍：没有要删的、没有要新增的', not plan2['delete'] and not plan2['create'], u'删 %d 增 %d 跳过 %d' % (len(plan2['delete']), len(plan2['create']), len(plan2['skip'])))
    # ---- 标注方向与方向规则
    check(u'所有新建标注的参照都是井中心面', all(not D['problems'] for D in dims2), u'%s' % [D['problems'] for D in dims2 if D['problems']][:3])
    # ---- 斜向：沿两井连线标真实中心距（不可见辅助线 + 对齐标注）；再规划应视为已覆盖
    ids6 = sorted(wells)
    item_ob = {'kind': 'oblique', 'ids': [ids6[0], ids6[-1]]}
    t = DB.Transaction(tmp, u'斜向'); t.Start()
    try:
        d_ob, e3 = n['create_dim'](view, wells, item_ob, dtype, off)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    import math
    ca, cb = wells[ids6[0]]['c'], wells[ids6[-1]]['c']
    check(u'斜向标注建成、数值 = 两井中心真实距离', d_ob is not None and abs(d_ob.Value - math.hypot(cb[0] - ca[0], cb[1] - ca[1])) < 0.002, u'%s %s' % (e3, (d_ob.Value * FT / 1000) if d_ob else None))
    cov = n['oblique_covered'](view, wells)
    check(u'斜向标注被识别为已覆盖（不会重复新增）', (min(ids6[0], ids6[-1]), max(ids6[0], ids6[-1])) in cov)
except Exception:
    LOG.append(traceback.format_exc())
    RES['f'] += 1
finally:
    flush(True)
    try:
        tmp.Close(False)
    except Exception as ex:
        LOG.append(u'关临时项目失败：%s' % ex)
        flush(True)
