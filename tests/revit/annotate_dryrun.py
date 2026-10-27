# -*- coding: utf-8 -*-
"""在 Revit 里运行：用仓库里最新的 well-annotate/script.py，对井大样图纸做一次完整标注（排管链、总尺寸、井盖尺寸），
全部放在一个事务组里，最后回滚——模型不改，只出报表（每张图纸每个视图新增几道、失败几项、跳过原因统计）。
改标注插件后、发布前跑一遍，对比前后的数字。可选变量：SHEETNOS=['2018', ...] 只跑指定图纸；SAMPLE_STEP 抽样间隔；OUT 报表路径。"""
import io, sys, types, re, os, traceback, time
import Autodesk.Revit.DB as DBmod
import Autodesk.Revit.UI as UImod
doc = uidoc.Document
OUT = globals().get('OUT') or os.path.join(os.environ.get('TEMP', '.'), 'thl_annotate_dryrun.txt')
SHEETNOS = globals().get('SHEETNOS')
SAMPLE_STEP = globals().get('SAMPLE_STEP') or 20
LOG = []
_pr = types.ModuleType('pyrevit')
_pr.revit = types.SimpleNamespace(doc=doc, uidoc=uidoc)
_pr.DB = DBmod; _pr.UI = UImod
class _F(object):
    def __getattr__(self, k):
        return type(k, (object,), {})
_pr.forms = _F()
sys.modules['pyrevit'] = _pr
src = io.open(r'C:\KAIFA\thl\python-src\well-annotate\script.py', encoding='utf-8').read()
src = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(u'C:/KAIFA/thl/python-src/' + m.group(1), encoding='utf-8').read().rstrip() + u'\n', src, flags=re.M)
n = {'__name__': 'annotrun'}


def flush(final=False):
    with io.open(OUT, 'w', encoding='utf-8') as f:
        f.write(u'\n'.join(LOG) + (u'\n完成' if final else u''))


def step(name, work):
    t = DBmod.Transaction(doc, name)
    fho = t.GetFailureHandlingOptions()
    fho.SetFailuresPreprocessor(n['_SilentFailures']())
    fho.SetClearAfterRollback(True)
    t.SetFailureHandlingOptions(fho)
    t.Start()
    try:
        work()
        return t.Commit() == DBmod.TransactionStatus.Committed
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        return False


def ndims(v):
    return len(list(DBmod.FilteredElementCollector(doc, v.Id).OfClass(DBmod.Dimension)))


def annotate_view(v, mark, stats, failed):
    del n['_DIM_ERRS'][:]
    n['_OUT_CROP'].clear()
    n['_FACE_CACHE'].clear(); n['_VFACE_CACHE'].clear()
    elems = n['_main_well'](v, n['collect_target_elems'](v, stats), mark)
    if not elems:
        elems = n['_main_well'](v, n['collect_target_elems'](v, None, True), mark)
    if not elems:
        return 'nowell'
    plan = n['_frame'](v)['plan']
    if not n['_axes_ok'](elems, n['_frame'](v)):
        rled = n['_fresh_ledger'](v)

        def rot():
            n['make_rotated_dims'](v, elems, rled, stats)
        ok = step('旋转井标注', rot)
        if not ok:
            n['_ROT_FORCE_TRACE'][0] = True
            try:
                rled = n['_fresh_ledger'](v)
                ok = step('旋转井标注(引出线)', rot)
            finally:
                n['_ROT_FORCE_TRACE'][0] = False
        if not ok:
            failed.append('%s 旋转井标注失败' % v.Name)
        if n['_COVER_DIMS'] and plan:
            def rc():
                n['make_cover_dims_rot'](v, elems, rled, stats, n['_COVER_DIMS_V'])
            if not step('旋转井盖尺寸', rc):
                failed.append('%s 旋转井井盖尺寸失败' % v.Name)
        return 'rot'
    box = n['_clip_box'](n['union_box'](v, elems), n['crop_world_range'](v))
    if box is not None:
        box = n['_face_box'](elems, box)
    if box is None:
        return 'nobox'
    res = {'cn': 0, 'n': 0}
    led = n['_fresh_ledger'](v)
    ext = {}

    def chains():
        res['cn'], _w = n['make_chain_dims'](v, elems, box, led, stats, ext)

    def totals():
        res['n'] = n['make_total_dims'](v, elems, box, led, ext)

    def both():
        chains(); totals()
    if not step('标注', both):
        ext.clear()
        led = n['_fresh_ledger'](v)
        if not step('排管尺寸链', chains):
            ext.clear()
            led = n['_fresh_ledger'](v)
            try:
                banks = n['_find_banks'](n['crop_world_range'](v), n['_frame'](v))
            except Exception:
                banks = []
            for bk in banks:
                for dr in ('h', 'v'):
                    def one(bk=bk, dr=dr):
                        n['make_chain_dims'](v, elems, box, led, stats, ext, [bk], dr)
                    if not step('排管尺寸链', one):
                        failed.append('%s 排管 %s %s向失败' % (v.Name, bk.Id, dr))
        if not step('总尺寸', totals):
            failed.append('%s 总尺寸失败' % v.Name)
    if n['_COVER_DIMS'] and plan:
        def cov():
            n['make_cover_dims'](v, elems, box, led, stats, n['_COVER_DIMS_V'])
        if not step('井盖尺寸', cov):
            failed.append('%s 井盖尺寸失败' % v.Name)
    return 'ok'


try:
    exec(compile(src, 'ann', 'exec'), n)
    n['_apply_settings']()
    n['_load_sheet_caches']()
    allsh = [x for x in DBmod.FilteredElementCollector(doc).OfClass(DBmod.ViewSheet) if n['_is_well_sheet'](x)]
    allsh.sort(key=lambda x: x.SheetNumber)
    if SHEETNOS:
        allsh = [x for x in allsh if x.SheetNumber in SHEETNOS]
    else:
        allsh = allsh[::SAMPLE_STEP]          # 抽样：默认每 20 张取 1 张
    tg = DBmod.TransactionGroup(doc, '天河落 标注试运行（最后回滚）')
    tg.Start()
    LOG.append('图纸 %d 张' % len(allsh))
    stats = {}
    failed = []
    t0 = time.time()
    tot = 0
    for si, sh in enumerate(allsh):
        mark = n['_sheet_mark'](sh)
        row = []
        for k, nm, v in n['_views_of'](sh):
            try:
                b0 = ndims(v)
                r = annotate_view(v, mark, stats, failed)
                d = ndims(v) - b0
                tot += d
                row.append('%s:%s+%d' % (nm[:1], r, d))
            except Exception as ex:
                row.append('%s:异常 %s' % (nm[:1], str(ex).split(chr(10))[0]))
        if True:
            LOG.append('%s %s' % (sh.SheetNumber, ' '.join(row)))
        if si % 25 == 0:
            LOG.append('进度 %d/%d 新增%d 用时%.0fs' % (si, len(allsh), tot, time.time() - t0))
            flush()
    tg.RollBack()
    LOG.append('（试运行：全部回滚，模型未改动）')
    LOG.append('合计新增尺寸 %d 道；失败 %d 项；统计 %s' % (tot, len(failed), stats))
    for f_ in failed[:30]:
        LOG.append('  失败: ' + f_)
except Exception:
    LOG.append(traceback.format_exc())
    try:
        tg.RollBack()          # 出错也要撤回已做的标注
    except Exception:
        pass
flush(True)
