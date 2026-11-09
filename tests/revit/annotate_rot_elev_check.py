# -*- coding: utf-8 -*-
"""在 Revit 里运行：旋转井（斜视图）立面标注核对。
对指定图纸上的每个视图复制一份（不带注释），用仓库里最新的 well-annotate 源码标注复制出的视图，
读出每道尺寸的数值和两端在视图里的位置（视图局部坐标，mm），再和井内壁位置比对。
全部放在事务组里，最后回滚：模型不留任何改动。变量 SHEET_ID 为图纸 id，OUT 为结果 JSON 路径。"""
import io, sys, types, re, os, json
import Autodesk.Revit.DB as DBmod
import Autodesk.Revit.UI as UImod
uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document
SHEET_ID = globals().get('SHEET_ID') or 13482932
OUT = globals().get('OUT') or os.path.join(os.environ.get('TEMP', '.'), 'thl_rot_elev_check.json')
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
exec(compile(src, 'well-annotate', 'exec'), n)
n['_apply_settings']()
MM = 304.8


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
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        return 'ERR ' + str(ex)


def dim_info(v, d):
    inv = v.CropBox.Transform.Inverse
    segs = list(d.Segments) if d.NumberOfSegments > 0 else [d]
    out = []
    for s in segs:
        o = inv.OfPoint(s.Origin)
        out.append([int(round(s.Value * MM)), round(o.X * MM), round(o.Y * MM)])
    ln = d.Curve
    try:
        dr = ln.Direction
        dl = inv.OfVector(dr)
        dirn = [round(dl.X, 4), round(dl.Y, 4), round(dl.Z, 4)]
    except Exception:
        dirn = None
    return {'segs': out, 'dir': dirn}


sheet = doc.GetElement(DBmod.ElementId(SHEET_ID))
mark = n['_sheet_mark'](sheet) if '_sheet_mark' in n else None
res = {'sheet': sheet.Name, 'views': []}
tg = DBmod.TransactionGroup(doc, 'rot elev check')
tg.Start()
try:
    srcs = [doc.GetElement(doc.GetElement(i).ViewId) for i in sheet.GetAllViewports()]
    copies = []

    def dup():
        for v in srcs:
            nid = v.Duplicate(DBmod.ViewDuplicateOption.Duplicate)
            copies.append((v, doc.GetElement(nid)))
    step('dup', dup)
    for v0, v in copies:
        stats = {}
        rec = {'src': v0.Name}
        try:
            m = re.search(r'AG-(.+?)-[0-9a-f]{4}$', v0.Name)
            wm = m.group(1) if m else mark
            elems = n['_main_well'](v, n['collect_target_elems'](v, stats), wm)
            if not elems:
                elems = n['_main_well'](v, n['collect_target_elems'](v, None, True), wm)
            if not elems:
                rec['err'] = 'nowell'
                res['views'].append(rec)
                continue
            fr = n['_frame'](v)
            rec['axes_ok'] = bool(n['_axes_ok'](elems, fr))
            info = n['_rot_info'](elems)
            if info:
                inv = v.CropBox.Transform.Inverse
                zmid = (info['zB'] + info['zT']) / 2.0
                cs = [inv.OfPoint(n['_rot_pt'](info, u, w, zmid)) for u in (info['uL'], info['uR']) for w in (info['wB'], info['wT'])]
                rec['inner_h_mm'] = [round(min(c.X for c in cs) * MM), round(max(c.X for c in cs) * MM)]
                rec['inner_v_mm'] = [round(min(c.Y for c in cs) * MM), round(max(c.Y for c in cs) * MM)]
            led = n['_fresh_ledger'](v)
            box = {}

            def rot():
                box['r'] = n['make_rotated_dims'](v, elems, led, stats)
            rec['step'] = step('rot', rot)
            rec['made'] = box.get('r')
            if fr['plan'] and n['_COVER_DIMS']:
                def rc():
                    box['c'] = n['make_cover_dims_rot'](v, elems, led, stats, n['_COVER_DIMS_V'])
                rec['cover_step'] = step('cover', rc)
            rec['dims'] = [dim_info(v, d) for d in DBmod.FilteredElementCollector(doc, v.Id).OfClass(DBmod.Dimension)]
            rec['errs'] = list(n['_DIM_ERRS'])
            rec['out_crop'] = dict((str(k), vv) for k, vv in n['_OUT_CROP'].items())
        except Exception as ex:
            import traceback
            rec['err'] = traceback.format_exc()
        res['views'].append(rec)
finally:
    tg.RollBack()
with io.open(OUT, 'w', encoding='utf-8') as f:
    f.write(json.dumps(res, ensure_ascii=False, indent=1))
