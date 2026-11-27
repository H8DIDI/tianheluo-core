# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '井接入排管\n高差更新'
__author__ = 'didi'
__doc__ = ('统计每口电井（TELCO、LV、ELV、11kV）接入的各根排管的管底标高，把「最高管底 − 最低管底」的最大高差写进井的参数，'
           '并给出高差分段（0-500、500-725、725-1000，往后每档 250mm）和接入排管数。\n'
           '写入三个井参数：接入排管最大高差（长度）、接入高差分段（文字）、接入排管数（整数）；参数不存在会自动新建（常规模型实例参数），'
           '也可以一键建好「井接入排管高差明细表」。排管挪了、改了标高，再点一次就更新，明细表跟着变。\n'
           '只写这三个参数，不动井和排管。运行前先告诉你有几口井会变，确认后才写；被别人占用或本地文件已过期的井会跳过并列出来。\n'
           '管底按排管在井范围内的真实高度算（带坡度的按坡度插值）；穿过井的、进到井里的、离井壁不到 200mm 的排管都算接入；'
           '室内井、临时构件不参与。分段边界、参数名等在「设置…」里改（文档\\KaifaTools\\井接入排管高差设置.json）。')

import io
import os
import math
import json
import tempfile
import uuid
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
from Autodesk.Revit import DB
from pyrevit import revit, forms
import System.Windows.Forms as WF

# @include _shared/form_fit.py
# @include _shared/settings_ui.py
# @include _shared/compat.py

doc = revit.doc
TITLE = '井接入排管高差更新'
FT = 304.8
CFG_FILE = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments),
                        'KaifaTools', '井接入排管高差设置.json')
DEFAULTS = {
    '说明': ('井接入排管高差更新的设置。band_edges_mm=前几档的上限（mm，如 500,725,1000），之后每档加 band_step_mm；'
            'gap_mm=排管端头离井壁多远以内仍算接入；param_diff/param_band/param_count=写入的三个参数名；'
            'skip_marks_containing=井号或族名含这些字的不统计（室内井）；schedule_name=明细表名字。'),
    'band_edges_mm': [500, 725, 1000],
    'band_step_mm': 250,
    'gap_mm': 200,
    'param_diff': '接入排管最大高差',
    'param_band': '接入高差分段',
    'param_count': '接入排管数',
    'skip_marks_containing': ['室内'],
    'schedule_name': '井接入排管高差明细表',
}


def save_cfg(c):
    d = os.path.dirname(CFG_FILE)
    if not os.path.isdir(d):
        os.makedirs(d)
    txt = json.dumps(c, ensure_ascii=False, indent=1)
    if not isinstance(txt, type('')):
        txt = txt.decode('utf-8')
    with io.open(CFG_FILE, 'w', encoding='utf-8') as f:
        f.write(txt)


def load_cfg():
    c = json.loads(json.dumps(DEFAULTS))
    try:
        if (not os.path.isfile(CFG_FILE)) or os.path.getsize(CFG_FILE) == 0:
            save_cfg(c)
            return c
        with io.open(CFG_FILE, 'r', encoding='utf-8') as f:
            u = json.load(f)
        if isinstance(u, dict):
            for k, v in u.items():
                if k in c and k != '说明':
                    c[k] = v
    except Exception:
        pass
    return c


def settings_fields():
    return [
        {'kind': 'header', 'label': '分段'},
        {'key': 'band_step_mm', 'label': '边界之后每档多少 mm', 'kind': 'num', 'min': 10, 'max': 5000,
         'hint': '默认 250：0-500、500-725、725-1000 之后是 1000-1250、1250-1500…（前几档的边界在设置文件里改 band_edges_mm）'},
        {'kind': 'header', 'label': '接入判定'},
        {'key': 'gap_mm', 'label': '管头离井壁多远以内算接入（mm）', 'kind': 'num', 'min': 0, 'max': 1000},
        {'kind': 'header', 'label': '写入的参数名'},
        {'key': 'param_diff', 'label': '最大高差（长度）', 'kind': 'text'},
        {'key': 'param_band', 'label': '高差分段（文字）', 'kind': 'text'},
        {'key': 'param_count', 'label': '接入排管数（整数）', 'kind': 'text'},
        {'key': 'schedule_name', 'label': '明细表名字', 'kind': 'text'},
    ]


def open_settings():
    c = load_cfg()
    res = su_fields_dialog('井接入排管高差 设置', '改完点保存立即生效。', settings_fields(), c, DEFAULTS,
                           extra_buttons=[('用记事本打开文件', lambda: su_open_file(CFG_FILE))])
    if res is None:
        return False
    out = json.loads(json.dumps(c))
    out.update(res)
    save_cfg(out)
    return True


# ============================================================
# 井与排管的识别（规则同「施工体检」）
# ============================================================

def well_kind(fam):
    if fam.startswith('LV-'):
        return 'LV'
    if fam.startswith('ELE-EM'):
        return 'EM'
    if fam.startswith('ELE-TELCO') or fam == 'TELCO' or fam.startswith('TELCO-'):
        return 'TELCO'
    if fam == 'ELV PIT' or fam.startswith('ELV-MANHOLE'):
        return 'ELV'
    return None


def pipe_sys(fam, tname):
    both = fam + '|' + tname
    if '雨水' in both or '排水' in both:
        return None
    if 'TELCO' in both:
        return 'TELCO'
    if '11KV' in both or '275KV' in both:
        return 'EM'
    if fam.startswith('ELV'):
        return 'ELV'
    if fam.startswith('LV') or 'ELE-LV' in tname:
        return 'LV'
    return None


def _txt(e, bip):
    try:
        p = e.get_Parameter(bip)
        return (p.AsString() or '') if p is not None else ''
    except Exception:
        return ''


def is_temp(e):
    return ('临时' in _txt(e, DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            or '临时' in _txt(e, DB.BuiltInParameter.ALL_MODEL_MARK) or '临时' in e.Name)


def _solids(e, lvl):
    opt = DB.Options()
    opt.DetailLevel = lvl
    out = []
    ge = e.get_Geometry(opt)
    if ge is None:
        return out
    for g in ge:
        items = g.GetInstanceGeometry() if isinstance(g, DB.GeometryInstance) else [g]
        for s in items:
            if isinstance(s, DB.Solid) and s.Volume > 1e-6:
                out.append(s)
    return out


def collect_wells(cfg):
    wells = []
    skip_words = cfg.get('skip_marks_containing') or []
    n_indoor = 0
    for e in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType():
        if not isinstance(e, DB.FamilyInstance):
            continue
        fam = e.Symbol.FamilyName
        kind = well_kind(fam)
        if kind is None or is_temp(e):
            continue
        mk = _txt(e, DB.BuiltInParameter.ALL_MODEL_MARK).strip()
        if any((w in mk) or (w in fam) for w in skip_words):
            n_indoor += 1
            continue
        tr = e.GetTotalTransform()
        inv = tr.Inverse
        lp = e.Location.Point if isinstance(e.Location, DB.LocationPoint) else tr.Origin
        sl = _solids(e, DB.ViewDetailLevel.Medium) or _solids(e, DB.ViewDetailLevel.Fine)
        lx, ly, wx, wy = [], [], [], []
        for s in sl:
            for ed in s.Edges:
                for p in ed.Tessellate():
                    q = inv.OfPoint(p)
                    lx.append(q.X)
                    ly.append(q.Y)
                    wx.append(p.X)
                    wy.append(p.Y)
        if not lx:
            continue
        wells.append({'e': e, 'id': thl_eid(e.Id), 'mark': mk, 'kind': kind, 'top': lp.Z, 'inv': inv, 'hits': [],
                      'lbox': (min(lx), min(ly), max(lx), max(ly)), 'wbox': (min(wx), min(wy), max(wx), max(wy))})
    return wells, n_indoor


def collect_pipes():
    pipes = []
    for cat in (DB.BuiltInCategory.OST_StructuralFraming, DB.BuiltInCategory.OST_Conduit):
        for e in DB.FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType():
            lc = e.Location
            if not isinstance(lc, DB.LocationCurve):
                continue
            et = doc.GetElement(e.GetTypeId())
            fam = et.FamilyName if et is not None else ''
            sy = pipe_sys(fam, e.Name)
            if sy is None or is_temp(e):
                continue
            a = lc.Curve.GetEndPoint(0)
            b = lc.Curve.GetEndPoint(1)
            if cat == DB.BuiltInCategory.OST_Conduit:
                d = e.get_Parameter(DB.BuiltInParameter.RBS_CONDUIT_OUTER_DIAM_PARAM)
                if d is None or d.AsDouble() <= 0:
                    d = e.get_Parameter(DB.BuiltInParameter.RBS_CONDUIT_DIAMETER_PARAM)
                zoff = -(d.AsDouble() / 2 if d is not None else 0)
            else:
                # 定位线不一定在管底，用包围盒最低点反推修正量（排管一般是底对齐，修正量为 0）
                bb = e.get_BoundingBox(None)
                zoff = (bb.Min.Z - min(a.Z, b.Z)) if bb is not None else 0
            pipes.append({'sys': sy, 'a': a, 'b': b, 'zoff': zoff})
    return pipes


def _clip(p0, p1, box):
    t0, t1 = 0.0, 1.0
    dx = p1.X - p0.X
    dy = p1.Y - p0.Y
    for pp, q in ((-dx, p0.X - box[0]), (dx, box[2] - p0.X), (-dy, p0.Y - box[1]), (dy, box[3] - p0.Y)):
        if abs(pp) < 1e-9:
            if q < 0:
                return None
            continue
        t = q / pp
        if pp < 0:
            if t > t1:
                return None
            if t > t0:
                t0 = t
        else:
            if t < t0:
                return None
            if t < t1:
                t1 = t
    return (t0, t1)


def _boxdist(q, box):
    dx = max(box[0] - q.X, 0, q.X - box[2])
    dy = max(box[1] - q.Y, 0, q.Y - box[3])
    return math.sqrt(dx * dx + dy * dy)


def match(wells, pipes, gap_mm):
    """给每口井填 hits = 接入排管的管底高度列表（ft）。"""
    TIGHT = 50.0 / FT
    GAP = float(gap_mm) / FT
    bysys = {}
    for w in wells:
        bysys.setdefault(w['kind'], []).append(w)
    for p in pipes:
        cands = bysys.get(p['sys'], [])
        a, b = p['a'], p['b']
        pminx, pmaxx = min(a.X, b.X) - 4, max(a.X, b.X) + 4
        pminy, pmaxy = min(a.Y, b.Y) - 4, max(a.Y, b.Y) + 4
        touched = set()
        near = []
        for w in cands:
            wb = w['wbox']
            if wb[2] < pminx or wb[0] > pmaxx or wb[3] < pminy or wb[1] > pmaxy:
                continue
            near.append(w)
            la = w['inv'].OfPoint(a)
            lb = w['inv'].OfPoint(b)
            bx = w['lbox']
            t = _clip(la, lb, (bx[0] - TIGHT, bx[1] - TIGHT, bx[2] + TIGHT, bx[3] + TIGHT))
            if t is None:
                continue
            zm = a.Z + (b.Z - a.Z) * (t[0] + t[1]) / 2.0     # 井范围内管段中点
            bot = zm + p['zoff']
            if bot >= w['top']:
                continue                                      # 管在井盖面以上，不是井里的管
            w['hits'].append(bot)
            touched.add(w['id'])
        for q in (a, b):
            best, bd = None, 1e9
            for w in near:
                d = _boxdist(w['inv'].OfPoint(q), w['lbox'])
                if d < bd:
                    bd, best = d, w
            if best is not None and TIGHT < bd <= GAP and best['id'] not in touched:
                best['hits'].append(q.Z + p['zoff'])


def band_of(n, diff_mm, cfg):
    """分段名带两位序号前缀，明细表按它排序就是从小到大。"""
    if n == 0:
        return '00 无接入排管'
    if n == 1:
        return '00 单根接入'
    edges = sorted(cfg['band_edges_mm'])
    lo = 0
    for i, hi in enumerate(edges):
        if diff_mm < hi:
            return '%02d %d-%d' % (i + 1, lo, hi)
        lo = hi
    step = float(cfg['band_step_mm'])
    k = int((diff_mm - lo) // step)
    a = int(lo + k * step)
    return '%02d %d-%d' % (len(edges) + 1 + k, a, int(a + step))


def compute(cfg):
    wells, n_indoor = collect_wells(cfg)
    match(wells, collect_pipes(), cfg['gap_mm'])
    rows = []
    for w in wells:
        h = w['hits']
        n = len(h)
        diff = round((max(h) - min(h)) * FT, 1) if n >= 2 else 0.0      # 先取整到 0.1mm 再分段，避免正好 1000 的被浮点误差分进低一档
        rows.append({'e': w['e'], 'id': w['id'], 'mark': w['mark'], 'kind': w['kind'], 'n': n, 'diff': diff,
                     'band': band_of(n, diff, cfg)})
    return rows, n_indoor


# ============================================================
# 参数与写入
# ============================================================

GEN = DB.BuiltInCategory.OST_GenericModel


def _bound(name):
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        if it.Key.Name == name:
            return True
    return False


def ensure_params(cfg):
    """三个参数缺哪个建哪个（常规模型实例共享参数，写临时共享参数文件，用完还原用户原来的共享参数文件路径）。返回新建的名字。"""
    want = [(cfg['param_diff'], 'LENGTH'), (cfg['param_band'], 'TEXT'), (cfg['param_count'], 'INTEGER')]
    need = [(n, t) for n, t in want if not _bound(n)]
    if not need:
        return []
    app = doc.Application
    old = app.SharedParametersFilename
    tmp = os.path.join(tempfile.gettempdir(), 'thl_well_duct_diff_sp.txt')
    text = ('# This is a Revit shared parameter file.\n# Do not edit manually.\n'
            '*META\tVERSION\tMINVERSION\nMETA\t2\t1\n*GROUP\tID\tNAME\nGROUP\t1\t井排管高差\n'
            '*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE\n')
    for n, ty in need:
        text += 'PARAM\t{}\t{}\t{}\t\t1\t1\t\t1\t0\n'.format(uuid.uuid4(), n, ty)
    with io.open(tmp, 'w', encoding='utf-16', newline='\n') as f:        # 共享参数文件必须 UTF-16 + LF
        f.write(text)
    t = DB.Transaction(doc, '新建井接入排管高差参数')
    t.Start()
    try:
        app.SharedParametersFilename = tmp
        grp = app.OpenSharedParameterFile().Groups.get_Item('井排管高差')
        for n, ty in need:
            cats = app.Create.NewCategorySet()
            cats.Insert(doc.Settings.Categories.get_Item(GEN))
            doc.ParameterBindings.Insert(grp.Definitions.get_Item(n), app.Create.NewInstanceBinding(cats), DB.GroupTypeId.Data)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    finally:
        try:
            app.SharedParametersFilename = old
        except Exception:
            pass
    return [n for n, ty in need]


def current_vals(r, cfg):
    e = r['e']
    pd, pb, pn = e.LookupParameter(cfg['param_diff']), e.LookupParameter(cfg['param_band']), e.LookupParameter(cfg['param_count'])
    return pd, pb, pn


def changed(r, cfg):
    pd, pb, pn = current_vals(r, cfg)
    if pd is None or pb is None or pn is None:
        return True
    try:
        return (abs(pd.AsDouble() * FT - r['diff']) > 0.5 or (pb.AsString() or '') != r['band'] or pn.AsInteger() != r['n'])
    except Exception:
        return True


class _Fail(DB.IFailuresPreprocessor):
    def __init__(self, ids):
        self.ids = ids

    def PreprocessFailures(self, fa):
        rb = False
        for f in fa.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                fa.DeleteWarning(f)          # 例如「已经在组编辑模式外修改了组」，允许
                continue
            rb = True
            try:
                for i in f.GetFailingElementIds():
                    self.ids.add(int(thl_eid(i)))
            except Exception:
                pass
        return DB.FailureProcessingResult.ProceedWithRollBack if rb else DB.FailureProcessingResult.Continue


def apply(rows, cfg):
    todo = [r for r in rows if changed(r, cfg)]
    blocked = set()
    st = None
    n = 0
    for _attempt in range(8):
        ids = set()
        t = DB.Transaction(doc, '井接入排管高差更新')
        fho = t.GetFailureHandlingOptions()
        fho.SetFailuresPreprocessor(_Fail(ids))
        t.SetFailureHandlingOptions(fho)
        t.Start()
        n = 0
        try:
            for r in todo:
                if r['id'] in blocked:
                    continue
                pd, pb, pn = current_vals(r, cfg)
                if pd is None or pb is None or pn is None or pd.IsReadOnly:
                    continue
                pd.Set(r['diff'] / FT)
                pb.Set(r['band'])
                pn.Set(int(r['n']))
                n += 1
            st = t.Commit()
        except Exception:
            t.RollBack()
            raise
        if str(st) == 'Committed' or not ids:
            break
        blocked |= ids
    names = dict((r['id'], r['mark']) for r in rows)
    return str(st) == 'Committed', n, [names.get(i, str(i)) for i in sorted(blocked)]


# ============================================================
# 明细表
# ============================================================

def make_schedule(cfg):
    name = cfg['schedule_name']
    for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule):
        if v.Name == name:
            return False, name
    t = DB.Transaction(doc, '建井接入排管高差明细表')
    t.Start()
    try:
        vs = DB.ViewSchedule.CreateSchedule(doc, DB.ElementId(GEN))
        vs.Name = name
        sd = vs.Definition
        avail = {}
        for sf in sd.GetSchedulableFields():
            avail[sf.GetName(doc)] = sf
        fam = sd.AddField(avail['族'])
        mark = sd.AddField(avail['标记'])
        if '分区' in avail:
            sd.AddField(avail['分区'])
        n = sd.AddField(avail[cfg['param_count']])
        diff = sd.AddField(avail[cfg['param_diff']])
        bnd = sd.AddField(avail[cfg['param_band']])
        fam.ColumnHeading = '井类型'
        mark.ColumnHeading = '井号'
        diff.ColumnHeading = '最大高差(mm)'
        bnd.ColumnHeading = '高差分段'
        sd.AddFilter(DB.ScheduleFilter(n.FieldId, DB.ScheduleFilterType.GreaterThanOrEqual, 2))
        sd.AddFilter(DB.ScheduleFilter(mark.FieldId, DB.ScheduleFilterType.NotContains, '室内'))
        sd.AddFilter(DB.ScheduleFilter(fam.FieldId, DB.ScheduleFilterType.NotContains, '室内'))
        s1 = DB.ScheduleSortGroupField(bnd.FieldId)
        s1.ShowHeader = True
        s1.ShowFooter = True
        s1.ShowBlankLine = False
        sd.AddSortGroupField(s1)
        sd.AddSortGroupField(DB.ScheduleSortGroupField(diff.FieldId, DB.ScheduleSortOrder.Descending))
        sd.IsItemized = True
        sd.ShowGrandTotal = True
        sd.ShowGrandTotalCount = True
        sd.ShowGrandTotalTitle = True
        try:
            fo = DB.FormatOptions()
            fo.UseDefault = False
            fo.SetUnitTypeId(DB.UnitTypeId.Millimeters)
            fo.Accuracy = 1.0
            diff.SetFormatOptions(fo)
        except Exception:
            pass
        t.Commit()
        return True, name
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise


def summary(rows):
    cnt = {}
    for r in rows:
        cnt[r['band']] = cnt.get(r['band'], 0) + 1
    return '\n'.join('  {}：{} 口'.format(k, cnt[k]) for k in sorted(cnt))


def main():
    try:
        cfg = load_cfg()
        while True:
            pick = forms.CommandSwitchWindow.show(
                ['更新高差数据（重新算并写入井参数）', '只建「井接入排管高差明细表」', '⚙ 设置…'], message=TITLE)
            if pick == '⚙ 设置…':
                if open_settings():
                    cfg = load_cfg()
                continue
            break
        if not pick:
            return
        if pick.startswith('只建'):
            ensure_params(cfg)
            made, nm = make_schedule(cfg)
            forms.alert(('已建好明细表「{}」。先点「更新高差数据」把值写进去。'.format(nm)) if made else '明细表「{}」已经有了，没有重复建。'.format(nm), title=TITLE)
            return
        rows, n_indoor = compute(cfg)
        if not rows:
            forms.alert('没有找到电井（常规模型里 TELCO / LV / ELV / 11kV 族的井）。', title=TITLE)
            return
        new_params = [] if all(_bound(cfg[k]) for k in ('param_diff', 'param_band', 'param_count')) else ['（会新建参数）']
        diff_rows = [r for r in rows if changed(r, cfg)]
        txt = '共 {} 口井参与统计（室内井 {} 口不算）。\n{}\n\n需要写入/更新：{} 口。{}'.format(
            len(rows), n_indoor, summary(rows), len(diff_rows), ('\n' + new_params[0]) if new_params else '')
        if not diff_rows and not new_params:
            forms.alert(txt + '\n\n数据已经是最新的，不用更新。', title=TITLE)
            return
        txt += '\n\n只写三个参数，不动井和排管。确定吗？'
        if WF.MessageBox.Show(txt, TITLE, WF.MessageBoxButtons.OKCancel, WF.MessageBoxIcon.Question) != WF.DialogResult.OK:
            return
        created = ensure_params(cfg)
        ok, n, blocked = apply(rows, cfg)
        msg = ('完成：更新 {} 口井。'.format(n)) if ok else '提交没有成功，没有任何改动。'
        if created:
            msg += '\n新建了参数：{}。'.format('、'.join(created))
        if blocked:
            msg += '\n以下 {} 口井被跳过（别人占用或你的文件已过期，先「重新载入最新」再点一次）：{}'.format(len(blocked), '、'.join(blocked[:12]))
        has_sched = any(v.Name == cfg['schedule_name'] for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule))
        if not has_sched:
            msg += '\n\n项目里还没有「{}」，要建一张吗？'.format(cfg['schedule_name'])
            if WF.MessageBox.Show(msg, TITLE, WF.MessageBoxButtons.YesNo, WF.MessageBoxIcon.Question) == WF.DialogResult.Yes:
                make_schedule(cfg)
                forms.alert('已建好明细表「{}」。'.format(cfg['schedule_name']), title=TITLE)
            return
        msg += '\n明细表会自动刷新。不满意可以 Ctrl+Z 撤销。'
        forms.alert(msg, title=TITLE)
    except Exception as ex:
        forms.alert('发生错误：\n{}\n\n{}'.format(ex, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
