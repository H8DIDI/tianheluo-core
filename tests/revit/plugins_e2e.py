# -*- coding: utf-8 -*-
"""在 Revit 里运行：新建一个临时结构项目（不保存，最后关闭），放柱（含一根旋转 30° 的）、基础、三维视图、明细表、图纸，
用仓库里最新的插件源码实际跑一遍「没在真实项目里用过」的插件的核心步骤，并核对结果。不碰当前打开的模型。
结果：%TEMP%\\thl_plugins_e2e\\report.txt。"""
import io, sys, types, re, os, math, traceback
import clr
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI
from System.Collections.Generic import List

app = uidoc.Application.Application
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_plugins_e2e')
SRC = globals().get('SRC_DIR') or r'C:\KAIFA\thl\python-src'
DLL = globals().get('DLL_DIR')      # 复制出来的 C# 插件 dll 所在文件夹（ScheduleExcel.dll）
if not os.path.isdir(OUT):
    os.makedirs(OUT)
LOG = []
ALERTS = []
RES = {'pass': 0, 'fail': 0}


def flush(final=False):
    with io.open(os.path.join(OUT, 'report.txt'), 'w', encoding='utf-8') as f:
        f.write(u'\n'.join(LOG) + (u'\n\n通过 %d，失败 %d\n完成' % (RES['pass'], RES['fail']) if final else u''))


def check(name, ok, detail=u''):
    RES['pass' if ok else 'fail'] += 1
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
        ALERTS.append(u'%s' % msg)
        return True
    def __getattr__(self, k):
        class _X(object):
            @staticmethod
            def show(*a, **kw):
                return None
        return _X


class _Sel(object):
    def __init__(self):
        self.ids = []
    def GetElementIds(self):
        return List[DB.ElementId](self.ids)
    def SetElementIds(self, ids):
        self.ids = list(ids)


class _UIDoc(object):
    def __init__(self, d, v):
        self.Document = d
        self.Selection = _Sel()
        self.ActiveView = v


def load(pid, d, v):
    pr = types.ModuleType('pyrevit')
    fake = _UIDoc(d, v)
    pr.revit = types.SimpleNamespace(doc=d, uidoc=fake)
    pr.DB = DB
    pr.UI = UI
    pr.forms = _Forms()
    sys.modules['pyrevit'] = pr
    path = os.path.join(SRC, pid, 'script.py')
    src = io.open(path, encoding='utf-8').read()
    src = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n',
                 src, flags=re.M)
    n = {'__name__': 'e2e', '__revit__': uidoc.Application, '__file__': path}
    exec(compile(src, pid, 'exec'), n)
    n['doc'] = d
    n['uidoc'] = fake
    return n


class _Fails(DB.IFailuresPreprocessor):
    """警告直接删掉；错误记下来（提交会回滚，测试要报出原因）。"""
    def __init__(self, sink):
        self.sink = sink
    def PreprocessFailures(self, fa):
        for f in fa.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                fa.DeleteWarning(f)
            else:
                self.sink.append(f.GetDescriptionText())
        return DB.FailureProcessingResult.Continue


def tx(d, name, fn):
    t = DB.Transaction(d, name)
    errs = []
    fho = t.GetFailureHandlingOptions()
    fho.SetFailuresPreprocessor(_Fails(errs))
    fho.SetClearAfterRollback(True)
    t.SetFailureHandlingOptions(fho)
    t.Start()
    try:
        r = fn()
        st = t.Commit()
        if st != DB.TransactionStatus.Committed:
            raise Exception(u'事务「%s」没提交成功（%s）：%s' % (name, st, u'；'.join(errs)))
        return r
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise


def run(name, fn):
    LOG.append(u'== ' + name)
    flush()
    del ALERTS[:]
    try:
        fn()
    except Exception:
        check(name + u' 运行', False, traceback.format_exc().replace(u'\n', u' | ')[-900:])


def solid_of(e):
    opt = DB.Options()
    for g in e.get_Geometry(opt):
        if isinstance(g, DB.Solid) and g.Volume > 1e-9:
            return g
        if isinstance(g, DB.GeometryInstance):
            for x in g.GetInstanceGeometry():
                if isinstance(x, DB.Solid) and x.Volume > 1e-9:
                    return x
    return None


def inside(solid, p, tol=1e-4):
    """点在凸实体里（柱子是长方体）：对每个平面，点都在外法线的背面。"""
    for f in solid.Faces:
        if not isinstance(f, DB.PlanarFace):
            continue
        if (p - f.Origin).DotProduct(f.FaceNormal) > tol:
            return False
    return True


# ------------------------------------------------------------------ 建测试项目
tpl = r'C:\ProgramData\Autodesk\RVT 2026\Templates\Chinese\Structural Analysis-DefaultCHSCHS.rte'
d = app.NewProjectDocument(tpl)
try:
    LOG.append(u'临时项目：%s' % tpl)
    lv = sorted(DB.FilteredElementCollector(d).OfClass(DB.Level), key=lambda x: x.Elevation)
    lvl = lv[0]
    col_syms = [s for s in DB.FilteredElementCollector(d).OfClass(DB.FamilySymbol).OfCategory(DB.BuiltInCategory.OST_StructuralColumns)]
    col_sym = ([s for s in col_syms if u'混凝土' in s.FamilyName] or col_syms)[0]
    fnd_syms = [s for s in DB.FilteredElementCollector(d).OfClass(DB.FamilySymbol).OfCategory(DB.BuiltInCategory.OST_StructuralFoundation)]
    LOG.append(u'柱族 %s : %s；基础族 %d 个' % (col_sym.FamilyName, col_sym.Name, len(fnd_syms)))

    def setup():
        if not col_sym.IsActive:
            col_sym.Activate()
        c1 = d.Create.NewFamilyInstance(DB.XYZ(0, 0, 0), col_sym, lvl, DB.Structure.StructuralType.Column)
        c2 = d.Create.NewFamilyInstance(DB.XYZ(20, 0, 0), col_sym, lvl, DB.Structure.StructuralType.Column)
        for c in (c1, c2):
            if len(lv) > 1:
                c.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_PARAM).Set(lv[1].Id)
            else:
                c.get_Parameter(DB.BuiltInParameter.FAMILY_TOP_LEVEL_OFFSET_PARAM).Set(3000 / 304.8)
        d.Regenerate()
        ax = DB.Line.CreateBound(DB.XYZ(20, 0, 0), DB.XYZ(20, 0, 1))
        DB.ElementTransformUtils.RotateElement(d, c2.Id, ax, math.radians(30))
        c1.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).Set(u'控制点测试')
        fds = []
        if fnd_syms:
            fs = fnd_syms[0]
            if not fs.IsActive:
                fs.Activate()
            for x in (40, 40, 60):       # 两个重合 + 一个不重合
                f = d.Create.NewFamilyInstance(DB.XYZ(x, 0, 0), fs, lvl, DB.Structure.StructuralType.Footing)
                f.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).Set(u'宿主 123 松木桩')
                fds.append(f)
        vft3 = [x for x in DB.FilteredElementCollector(d).OfClass(DB.ViewFamilyType) if x.ViewFamily == DB.ViewFamily.ThreeDimensional][0]
        v3 = DB.View3D.CreateIsometric(d, vft3.Id)
        v3.Name = u'测试三维'
        sch = DB.ViewSchedule.CreateSchedule(d, DB.ElementId(DB.BuiltInCategory.OST_StructuralColumns))
        sch.Name = u'测试柱明细表'
        for sf in sch.Definition.GetSchedulableFields():
            if sf.ParameterId in (DB.ElementId(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS), DB.ElementId(DB.BuiltInParameter.ALL_MODEL_MARK)):
                sch.Definition.AddField(sf)
        vftp = [x for x in DB.FilteredElementCollector(d).OfClass(DB.ViewFamilyType) if x.ViewFamily == DB.ViewFamily.StructuralPlan or x.ViewFamily == DB.ViewFamily.FloorPlan][0]
        plan = DB.ViewPlan.Create(d, vftp.Id, lvl.Id)
        tb = DB.FilteredElementCollector(d).OfCategory(DB.BuiltInCategory.OST_TitleBlocks).WhereElementIsElementType().FirstElement()
        sh = DB.ViewSheet.Create(d, tb.Id if tb else DB.ElementId.InvalidElementId)
        sh.SheetNumber = u'T-001'
        sh.Name = u'测试图纸'
        d.Regenerate()
        DB.Viewport.Create(d, sh.Id, plan.Id, DB.XYZ(1.0, 0.8, 0))
        return c1, c2, fds, v3, sch, sh
    c1, c2, fds, v3, sch, sh = tx(d, u'测试场景', setup)
    LOG.append(u'场景：柱 2 根（1 根旋转 30°），基础 %d 个，三维视图、明细表、图纸各 1' % len(fds))
    flush()

    # ---------------- 一键单构件快速配筋：两根柱（含旋转的）钢筋都在混凝土里
    def t_cage():
        n = load('rebar-cage', d, v3)
        cover = n['_cover_ft']()
        bt, _a = n['_pick_bar_type'](20)
        st, _b = n['_pick_bar_type'](10)
        hook = n['_pick_hook_135']()
        check(u'配筋 找到保护层/钢筋/弯钩类型', cover is not None and bt is not None and st is not None and hook is not None,
              u'%s %s %s %s' % (cover, bt, st, hook))
        for c in (c1, c2):
            nb, ns = tx(d, u'配筋', lambda: n['build_cage'](c, cover, 10 / 304.8, 20 / 304.8, 8, 150, bt, st, hook))
            so = solid_of(c)
            bad = 0
            tot = 0
            for r in DB.FilteredElementCollector(d).OfClass(DB.Structure.Rebar):
                if r.GetHostId() != c.Id:
                    continue
                for cv in r.GetCenterlineCurves(False, False, False, DB.Structure.MultiplanarOption.IncludeOnlyPlanarCurves, 0):
                    for k in (0, 1):
                        tot += 1
                        if not inside(so, cv.GetEndPoint(k)):
                            bad += 1
            check(u'配筋 %s 柱：主筋 %d 根、箍筋 %d 道，钢筋端点都在混凝土里' % (u'旋转' if c is c2 else u'正放', nb, ns),
                  nb == 8 and ns > 5 and tot > 0 and bad == 0, u'（端点 %d 个，在外面 %d 个）' % (tot, bad))
    run(u'一键单构件快速配筋', t_cage)

    # ---------------- 一键钢筋三维实体显示：显示 → 实体、半透明；恢复 → 还原
    def t_r3d():
        n = load('rebar-3d-view', d, v3)
        n['STATE_PATH'] = os.path.join(OUT, u'钢筋三维显示状态.json')      # 不碰用户真实的状态文件
        try:
            os.remove(n['STATE_PATH'])
        except Exception:
            pass
        keep = DB.OverrideGraphicSettings()
        keep.SetProjectionLineColor(DB.Color(255, 0, 0))
        tx(d, u'预设颜色', lambda: v3.SetElementOverrides(c1.Id, keep))

        before = int(v3.DetailLevel)
        n['show_3d'](v3)
        bars = [r for r in DB.FilteredElementCollector(d).OfClass(DB.Structure.Rebar)]
        check(u'钢筋三维 视图精细、钢筋不被遮挡', int(v3.DetailLevel) == int(DB.ViewDetailLevel.Fine) and bars and all(r.IsUnobscuredInView(v3) for r in bars), u'%d 根' % len(bars))
        o = v3.GetElementOverrides(c1.Id)
        check(u'钢筋三维 混凝土半透明且保留原来的线颜色', o.Transparency == 50 and o.ProjectionLineColor.IsValid and o.ProjectionLineColor.Red == 255)
        n['restore'](v3)
        o = v3.GetElementOverrides(c1.Id)
        check(u'钢筋三维 恢复：透明度回 0、线颜色还在、钢筋取消不被遮挡、详细程度还原',
              o.Transparency == 0 and o.ProjectionLineColor.IsValid and not any(r.IsUnobscuredInView(v3) for r in bars)
              and int(v3.DetailLevel) == before, u'详细程度 %s→%s' % (before, int(v3.DetailLevel)))
    if globals().get('ONLY_CAGE'):
        raise SystemExit()
    run(u'一键钢筋三维实体显示', t_r3d)

    # ---------------- 一键框选提量：混凝土体积 = 两根柱的体积，钢筋重量 > 0
    def t_qty():
        n = load('quantity-export', d, v3)
        elems = [c1, c2] + list(DB.FilteredElementCollector(d).OfClass(DB.Structure.Rebar))
        res = n['_quantities'](elems)
        vol = sum(r[1] for r in res['conc'].values())
        expect = sum(c.get_Parameter(DB.BuiltInParameter.HOST_VOLUME_COMPUTED).AsDouble() for c in (c1, c2)) * 0.3048 ** 3
        kg = sum(r[2] for r in res['rebar'].values())
        check(u'提量 混凝土体积与 Revit 一致', abs(vol - expect) < 1e-4, u'%.4f vs %.4f m³' % (vol, expect))
        check(u'提量 钢筋按直径汇总、重量合理', len(res['rebar']) == 2 and kg > 10, u'%s，%.1f kg' % (sorted(k[1] for k in res['rebar']), kg))
        tables, summary = n['_table_rows'](res)
        p = os.path.join(OUT, u'提量.xml')
        n['_write_xlsx'](tables, p)
        check(u'提量 导出 Excel 文件', os.path.exists(p) and os.path.getsize(p) > 500)
    run(u'一键框选提量导出Excel', t_qty)

    # ---------------- 控制点坐标表：按名称筛选取点、导出 xlsx + csv
    def t_cp():
        n = load('control-points', d, v3)
        s = n['load_settings']()
        s.update({'source': 'family', 'family_kw': u'控制点测试', 'also_csv': True})
        rows = n['build_rows'](s)
        real = [r for r in rows if r['no'] not in (u'PBP', u'SP')]
        check(u'控制点 按注释筛选取到 1 个点', len(real) == 1, u'%d 个' % len(real))
        p = os.path.join(OUT, u'控制点.xlsx')
        n['ask_save'] = lambda *a, **k: p
        path = n['export_table'](rows, u'按名称筛选', s, u'控制点坐标表')
        check(u'控制点 导出 xlsx 和 csv', path == p and os.path.exists(p) and os.path.exists(p[:-5] + u'.csv'))
    run(u'控制点坐标表', t_cp)

    # ---------------- 清理重复松木桩垫层：两个重合的基础删一个
    def t_pile():
        if not fds:
            check(u'清理重复 模板里没有基础族，跳过', True)
            return
        n = load('pile-cleanup', d, v3)
        n_piles, pd, dd, fd = n['scan']()
        check(u'清理重复 找到 1 个重复（3 个里 2 个重合）', len(pd) + len(fd) == 1, u'桩 %d 垫层族 %d' % (len(pd), len(fd)))
    run(u'清理重复松木桩垫层', t_pile)

    # ---------------- 图纸快速预览：把图纸渲染成图片
    def t_prev():
        n = load('sheet-preview', d, v3)
        cdir = os.path.join(OUT, u'preview')
        if not os.path.isdir(cdir):
            os.makedirs(cdir)
        ok, bad, canc = n['render_missing']([sh], cdir, 1200, True)
        check(u'图纸预览 渲染 1 张', ok == 1 and bad == 0 and os.path.exists(n['img_path'](cdir, sh)), u'成功 %d 失败 %d' % (ok, bad))
    run(u'图纸快速预览', t_prev)

    # ---------------- 一键导出NWC
    def t_nwc():
        if not DB.OptionalFunctionalityUtils.IsNavisworksExporterAvailable():
            check(u'NWC 这台电脑没装 Navisworks 导出插件，跳过', True)
            return
        o = DB.NavisworksExportOptions()
        o.ExportScope = DB.NavisworksExportScope.View
        o.ViewId = v3.Id
        d.Export(OUT, u'测试', o)
        check(u'NWC 导出三维视图', os.path.exists(os.path.join(OUT, u'测试.nwc')))
    run(u'一键导出NWC', t_nwc)

    # ---------------- 明细表 Excel 互导（C#）：导出 → 改注释 → 预览 → 写回
    def t_sched():
        if not DLL:
            check(u'明细表互导 没给 DLL_DIR，跳过', True)
            return
        clr.AddReferenceToFileAndPath(os.path.join(DLL, 'ScheduleExcel.dll'))
        from THL.Plugins.ScheduleExcel import ScheduleService, Xlsx
        p = os.path.join(OUT, u'明细表.xlsx')
        nrows = ScheduleService.Export(d, List[DB.ViewSchedule]([sch]), p)
        check(u'明细表互导 导出', nrows == 2 and os.path.exists(p), u'%d 行' % nrows)
        book = Xlsx.Read(p)
        ws = [b for b in book if b.Name != ScheduleService.MetaSheet][0]
        head = list(ws.Rows[0])
        ci = [i for i, h in enumerate(head) if h in (u'注释', u'Comments')][0]
        ws.Rows[2][ci] = u'改过的注释'
        Xlsx.Write(p, book)
        plan = ScheduleService.Plan(d, p)
        check(u'明细表互导 只识别出 1 处改动（数值列不误报）', plan.Changes.Count == 1, u'%d 处：%s' % (plan.Changes.Count, u'; '.join(u'%s %s→%s' % (c.Field, c.Old, c.New) for c in plan.Changes)))
        res = ScheduleService.Apply(d, plan)
        eid = int(ws.Rows[2][0])
        val = d.GetElement(DB.ElementId(eid)).get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).AsString()
        check(u'明细表互导 写回成功', res.Ok == 1 and val == u'改过的注释', u'ok=%d 值=%s' % (res.Ok, val))
    run(u'明细表Excel互导', t_sched)
except SystemExit:
    pass
except Exception:
    LOG.append(traceback.format_exc())
finally:
    try:
        d.Close(False)
    except Exception:
        pass
    sys.modules.pop('pyrevit', None)
flush(True)
