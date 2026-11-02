# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '构件标记\n一键整理'
__author__ = 'didi'
__doc__ = ('一键整理当前平面视图里的构件标记（出图用）：\n'
           '① 引线末端设到它自己的构件上（解决标记挪了以后引线还指着别的构件）；\n'
           '② 每个标记自动选空的一侧（8 个方向），不压别的标记、不压别的构件；放不下就沿引线引出去；\n'
           '③ 同一侧的标记对齐到同一行/同一列；\n'
           '④ 悬空标记（构件没了）可以删除；视图里该标但没标的构件可以补标；\n'
           '写入后逐个回读核对，引线末端不在自己构件上的整次回滚。整次一个事务，Ctrl+Z 可撤销。\n'
           '标记离构件的距离、引线上限、对齐容差、是否补标等都能在「设置…」里改。')

import io
import os
import json
import math
import traceback
import clr
import System
clr.AddReference('RevitAPI')
from Autodesk.Revit import DB
from System.Collections.Generic import List
from pyrevit import revit, forms

# @include _shared/form_fit.py
# @include _shared/settings_ui.py

doc = revit.doc
uidoc = revit.uidoc
TITLE = '构件标记一键整理'
FT = 304.8
DOC_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), 'KaifaTools')
SETTINGS = os.path.join(DOC_DIR, '构件标记整理设置.json')
DEFAULTS = {
    '说明': ('gap_mm：标记离构件的最小距离（图纸 mm）；max_leader_mm：引线最长（图纸 mm）；step_mm：往外试探一步；'
           'align_mm：同侧标记对齐的容差；text_w_mm / text_h_mm：标记头图纸尺寸估计（读不到时用）；'
           'add_missing：是否补标缺标构件；delete_orphans：是否删除悬空标记；min_pipe_mm：线性构件短于这个长度（模型 mm）不补标。'),
    'gap_mm': 2.0,
    'max_leader_mm': 40.0,
    'step_mm': 3.0,
    'align_mm': 4.0,
    'text_w_mm': 15.0,
    'text_h_mm': 8.0,
    'add_missing': False,
    'delete_orphans': True,
    'min_pipe_mm': 1500.0,
}
DIRS = [(0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (-1, 1)]


def load():
    s = dict(DEFAULTS)
    try:
        if os.path.exists(SETTINGS):
            with io.open(SETTINGS, 'r', encoding='utf-8-sig') as f:
                s.update(json.load(f))
    except Exception:
        pass
    return s


def open_settings():
    cur = load()
    fields = [
        {'key': 'gap_mm', 'label': '标记离构件最小距离 (图纸 mm)', 'kind': 'num', 'min': 0, 'max': 30, 'hint': '标记头离构件的最小距离。'},
        {'key': 'max_leader_mm', 'label': '引线最长 (图纸 mm)', 'kind': 'num', 'min': 5, 'max': 300, 'hint': '放不下时沿引线引出去，最远到这里。'},
        {'key': 'step_mm', 'label': '往外试探步长 (图纸 mm)', 'kind': 'num', 'min': 0.5, 'max': 30, 'hint': '找空位时每次往外走多远。'},
        {'key': 'align_mm', 'label': '对齐容差 (图纸 mm)', 'kind': 'num', 'min': 0, 'max': 30, 'hint': '同一侧的标记，位置差在这个范围内就对齐到同一行/列。'},
        {'key': 'text_w_mm', 'label': '标记头宽度估计 (图纸 mm)', 'kind': 'num', 'min': 3, 'max': 100, 'hint': '读不到标记头大小时用这个值。'},
        {'key': 'text_h_mm', 'label': '标记头高度估计 (图纸 mm)', 'kind': 'num', 'min': 2, 'max': 50, 'hint': '同上。'},
        {'key': 'add_missing', 'label': '补标缺标的构件', 'kind': 'bool', 'hint': '视图里同类别、同族已有标记，但某些构件没标的，用同样的标记类型补上。'},
        {'key': 'delete_orphans', 'label': '删除悬空标记', 'kind': 'bool', 'hint': '标记的构件已经不存在（显示问号）的，删掉。'},
        {'key': 'min_pipe_mm', 'label': '线性构件补标最短长度 (模型 mm)', 'kind': 'num', 'min': 0, 'max': 100000, 'hint': '太短的管不补标。'},
    ]
    res = su_fields_dialog(TITLE + ' · 设置', '改完点保存，下次运行生效。设置文件：' + SETTINGS, fields, cur, DEFAULTS,
                           extra_buttons=[('用记事本打开文件', lambda: su_open_file(SETTINGS))], width=700, height=560)
    if res is None:
        return False
    out = dict(DEFAULTS)
    out.update(cur)
    out.update(res)
    if not os.path.isdir(DOC_DIR):
        os.makedirs(DOC_DIR)
    with io.open(SETTINGS, 'w', encoding='utf-8') as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    return True


class _Fails(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        for m in fa.GetFailureMessages():
            if m.GetSeverity() == DB.FailureSeverity.Warning:
                fa.DeleteWarning(m)
        return DB.FailureProcessingResult.Continue


def rect_overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _ccw(px, py, qx, qy, rx, ry):
    return (ry - py) * (qx - px) > (qy - py) * (rx - px)


def seg_cross(ax, ay, bx, by, cx, cy, dx, dy):
    return _ccw(ax, ay, cx, cy, dx, dy) != _ccw(bx, by, cx, cy, dx, dy) and _ccw(ax, ay, bx, by, cx, cy) != _ccw(ax, ay, bx, by, dx, dy)


def seg_hits_rect(seg, r):
    x1, y1, x2, y2 = seg
    if r[0] <= x1 <= r[2] and r[1] <= y1 <= r[3]:
        return True
    if r[0] <= x2 <= r[2] and r[1] <= y2 <= r[3]:
        return True
    for ex1, ey1, ex2, ey2 in [(r[0], r[1], r[2], r[1]), (r[2], r[1], r[2], r[3]), (r[2], r[3], r[0], r[3]), (r[0], r[3], r[0], r[1])]:
        if seg_cross(x1, y1, x2, y2, ex1, ey1, ex2, ey2):
            return True
    return False


def rect(cx, cy, w, h):
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def el_segments(el, scale_ft):
    """构件在平面上的真实线段（线式）或小方块（点式）。"""
    loc = el.Location
    if isinstance(loc, DB.LocationCurve):
        c = loc.Curve
        p0, p1 = c.GetEndPoint(0), c.GetEndPoint(1)
        return [(p0.X, p0.Y, p1.X, p1.Y)]
    if isinstance(loc, DB.LocationPoint):
        p = loc.Point
        s = scale_ft
        return [(p.X - s, p.Y - s, p.X + s, p.Y + s)]
    bb = el.get_BoundingBox(None)
    if bb is not None:
        return [(bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y)]
    return []


def on_element_point(el, head):
    """构件上离标记头最近的点（引线末端要落在这里）。取不到返回 None。"""
    loc = el.Location
    if isinstance(loc, DB.LocationCurve):
        p = loc.Curve.Project(head)
        if p is not None:
            return p.XYZPoint
        return loc.Curve.Evaluate(0.5, True)
    if isinstance(loc, DB.LocationPoint):
        return loc.Point
    bb = el.get_BoundingBox(None)
    if bb is not None:
        return DB.XYZ((bb.Min.X + bb.Max.X) / 2.0, (bb.Min.Y + bb.Max.Y) / 2.0, bb.Min.Z)
    return None


def dist_to_element(el, pt):
    loc = el.Location
    if isinstance(loc, DB.LocationCurve):
        p = loc.Curve.Project(pt)
        if p is not None:
            return math.hypot(pt.X - p.XYZPoint.X, pt.Y - p.XYZPoint.Y)
    elif isinstance(loc, DB.LocationPoint):
        return math.hypot(pt.X - loc.Point.X, pt.Y - loc.Point.Y)
    return 0.0


def plan(view, cfg):
    scale = max(int(view.Scale), 1)
    m = lambda mm: mm * scale / FT          # 图纸 mm → 模型 ft
    p_mm = lambda ft: ft * FT / scale       # 模型 ft → 图纸 mm
    gap, step, max_len, tol = m(cfg['gap_mm']), m(cfg['step_mm']), m(cfg['max_leader_mm']), m(cfg['align_mm'])
    tw, th = m(cfg['text_w_mm']), m(cfg['text_h_mm'])
    tags = list(DB.FilteredElementCollector(doc, view.Id).OfClass(DB.IndependentTag))
    orphans, items, skipped = [], [], 0
    for t in tags:
        try:
            if t.IsOrphaned:
                orphans.append(t)
                continue
        except Exception:
            pass
        ids = list(t.GetTaggedLocalElementIds())
        if len(ids) != 1:
            skipped += 1
            continue
        el = doc.GetElement(ids[0])
        if el is None:
            orphans.append(t)
            continue
        try:
            refs = list(t.GetTaggedReferences())
        except Exception:
            refs = []
        if not refs:
            skipped += 1
            continue
        head = t.TagHeadPosition
        a = on_element_point(el, head)
        if a is None:
            skipped += 1
            continue
        items.append({'t': t, 'el': el, 'ref': refs[0], 'a': a, 'head': head, 'own': el.Id.Value})
    obstacles = []
    for it in items:
        for s in el_segments(it['el'], m(1.0)):
            obstacles.append((s, it['own']))
    items.sort(key=lambda x: (round(x['a'].Y, 1), round(x['a'].X, 1)))
    placed = []
    for x in items:
        a = x['a']
        base = max(tw, th) / 2.0 + gap
        if base > max_len:
            base = max_len
        best = None
        k = 0
        r = base
        while r <= max_len + 1e-9:
            for dx, dy in DIRS:
                n = math.hypot(dx, dy)
                hx, hy = a.X + dx / n * r, a.Y + dy / n * r
                rc = rect(hx, hy, tw, th)
                c_tag = sum(1 for p in placed if rect_overlap(rc, p))
                c_obs = sum(1 for s, eid in obstacles if eid != x['own'] and seg_hits_rect(s, rc))
                score = (c_tag + c_obs) * 1000 + k + (0.1 if (dx and dy) else 0)
                if best is None or score < best[0]:
                    best = (score, hx, hy, c_tag + c_obs, dx, dy)
            if best is not None and best[3] == 0:
                break
            k += 1
            r = base + k * step
        _, hx, hy, c, dx, dy = best
        x['new'] = (hx, hy)
        x['dir'] = (dx, dy)
        x['conf'] = c
        placed.append(rect(hx, hy, tw, th))
    # 对齐：上下方向对齐 X，左右方向对齐 Y（容差内、移动不超过 15 mm）
    shift_max = m(15.0)
    for axis in ('x', 'y'):
        if axis == 'x':
            grp = [x for x in items if x['dir'][0] == 0 and x['dir'][1] != 0]
            key = lambda x: x['new'][0]
        else:
            grp = [x for x in items if x['dir'][1] == 0 and x['dir'][0] != 0]
            key = lambda x: x['new'][1]
        grp.sort(key=key)
        cur = []
        for x in grp + [None]:
            if x is not None and (not cur or abs(key(x) - key(cur[-1])) <= tol):
                cur.append(x)
                continue
            if len(cur) > 1:
                mean = sum(key(y) for y in cur) / len(cur)
                for y in cur:
                    orig = y['new'][0] if axis == 'x' else y['new'][1]
                    if abs(mean - orig) <= shift_max:
                        y['new'] = (mean, y['new'][1]) if axis == 'x' else (y['new'][0], mean)
            cur = [x] if x is not None else []
    rects = [rect(x['new'][0], x['new'][1], tw, th) for x in items]
    for it in range(4):
        moved = 0
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if not rect_overlap(rects[i], rects[j]):
                    continue
                y = items[j]
                dx, dy = y['dir']
                if dx == 0 and dy == 0:
                    continue
                n = math.hypot(dx, dy)
                nx, ny = y['new'][0] + dx / n * step, y['new'][1] + dy / n * step
                if math.hypot(nx - y['a'].X, ny - y['a'].Y) > max_len:
                    continue
                y['new'] = (nx, ny)
                rects[j] = rect(nx, ny, tw, th)
                moved += 1
        if moved == 0:
            break
    overlap = sum(1 for i in range(len(rects)) for j in range(i + 1, len(rects)) if rect_overlap(rects[i], rects[j]))
    hit = 0
    for x, rc in zip(items, rects):
        if any(seg_hits_rect(s, rc) for s, eid in obstacles if eid != x['own']):
            hit += 1
    far = sum(1 for x in items if p_mm(math.hypot(x['new'][0] - x['a'].X, x['new'][1] - x['a'].Y)) > cfg['max_leader_mm'] + 1e-6)
    return {'items': items, 'orphans': orphans, 'skipped': skipped, 'overlap': overlap, 'hit': hit, 'far': far, 'scale': scale}


def missing_elements(view, cfg, items):
    """视图里同类别、同族已经有标记，但自己没标的构件：[(el, 标记类型Id)]。"""
    tagged = set(x['own'] for x in items)
    fam_type = {}
    for x in items:
        e = x['el']
        try:
            k = (e.Category.Id.Value, e.GetTypeId().Value)
        except Exception:
            continue
        fam_type.setdefault(k, {}).setdefault(x['t'].GetTypeId().Value, 0)
        fam_type[k][x['t'].GetTypeId().Value] += 1
    cat_type = {}
    for (cid, _tid), cnt in fam_type.items():
        for ttid, n in cnt.items():
            cat_type.setdefault(cid, {}).setdefault(ttid, 0)
            cat_type[cid][ttid] += n
    out = []
    min_len = cfg['min_pipe_mm'] / FT
    for cid, tmap in cat_type.items():
        tt = max(tmap.items(), key=lambda kv: kv[1])[0]
        col = DB.FilteredElementCollector(doc, view.Id).OfCategoryId(DB.ElementId(cid)).WhereElementIsNotElementType()
        for e in col:
            if e.Id.Value in tagged:
                continue
            loc = e.Location
            if isinstance(loc, DB.LocationCurve) and loc.Curve.Length < min_len:
                continue
            if loc is None:
                continue
            out.append((e, DB.ElementId(tt)))
    return out


def verify(items, tol_ft):
    """回读：每个标记的引线末端必须落在自己构件上（离构件 tol 内）。返回不合格的标记 id 列表。"""
    bad = []
    for x in items:
        try:
            le = x['t'].GetLeaderEnd(x['ref'])
        except Exception:
            bad.append(x['t'].Id.Value)
            continue
        if dist_to_element(x['el'], le) > tol_ft:
            bad.append(x['t'].Id.Value)
    return bad


def apply(view, pl, cfg, add_list):
    scale = pl['scale']
    tol_ft = 5.0 / FT * 1.0 + 20.0 / FT          # 末端离自己构件 ≤ 25 mm 才算对
    t = DB.Transaction(doc, '天河落 · 构件标记一键整理')
    fh = t.GetFailureHandlingOptions()
    fh.SetFailuresPreprocessor(_Fails())
    t.SetFailureHandlingOptions(fh)
    t.Start()
    try:
        n_del = 0
        if cfg['delete_orphans'] and pl['orphans']:
            doc.Delete(List[DB.ElementId]([o.Id for o in pl['orphans']]))
            n_del = len(pl['orphans'])
        for x in pl['items']:
            tg = x['t']
            tg.HasLeader = True
            try:
                tg.LeaderEndCondition = DB.LeaderEndCondition.Free
            except Exception:
                pass
            tg.TagHeadPosition = DB.XYZ(x['new'][0], x['new'][1], x['head'].Z)
            try:
                tg.SetLeaderEnd(x['ref'], DB.XYZ(x['a'].X, x['a'].Y, x['head'].Z))
            except Exception:
                pass
        doc.Regenerate()
        n_add = 0
        added_items = []
        if cfg['add_missing'] and add_list:
            for e, ttid in add_list:
                try:
                    p = on_element_point(e, DB.XYZ(0, 0, 0))
                    if p is None:
                        continue
                    ref = DB.Reference(e)
                    ntag = DB.IndependentTag.Create(doc, ttid, view.Id, ref, True, DB.TagOrientation.Horizontal, DB.XYZ(p.X, p.Y, p.Z))
                    n_add += 1
                    added_items.append(ntag)
                except Exception:
                    continue
        bad = verify(pl['items'], tol_ft)
        if bad:
            raise Exception('回读核对失败：%d 个标记的引线末端没落在自己的构件上（如 %s），整次回滚，模型未改。' % (len(bad), bad[:3]))
        st = t.Commit()
        if st != DB.TransactionStatus.Committed:
            raise Exception('事务没有提交（%s），模型未改。' % st)
        return n_del, n_add
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise


def main():
    try:
        view = uidoc.ActiveView
        if not isinstance(view, DB.ViewPlan):
            forms.alert('请在平面视图里运行（当前是「%s」）。' % view.Name, title=TITLE)
            return
        pick = forms.CommandSwitchWindow.show(['整理当前视图的构件标记', '⚙ 设置…'], message=TITLE)
        if not pick:
            return
        if pick.startswith('⚙'):
            if open_settings():
                forms.alert('设置已保存，下次运行生效。', title=TITLE)
            return
        cfg = load()
        pl = plan(view, cfg)
        if not pl['items'] and not pl['orphans']:
            forms.alert('当前视图里没有单构件标记可整理。', title=TITLE)
            return
        add_list = missing_elements(view, cfg, pl['items']) if cfg['add_missing'] else []
        msg = ('当前视图（比例 1:%d）：\n'
               '  单构件标记 %d 个，将重排并把引线末端设到各自构件上\n'
               '  悬空标记 %d 个%s\n'
               '  缺标构件 %d 个%s\n'
               '  多构件标记 %d 个（不动）\n\n'
               '整理后预计：标记重叠 %d 对；压到其它构件的 %d 个；引线超过上限的 %d 个。\n'
               '整次一个事务，写入后会回读核对，Ctrl+Z 可撤销。执行？') % (
            pl['scale'], len(pl['items']), len(pl['orphans']), '（将删除）' if cfg['delete_orphans'] else '（不删）',
            len(add_list), '（将补标）' if cfg['add_missing'] else '（设置里勾选才补标）', pl['skipped'],
            pl['overlap'], pl['hit'], pl['far'])
        if not forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
            return
        n_del, n_add = apply(view, pl, cfg, add_list)
        forms.alert('完成：整理 %d 个标记，删除悬空 %d 个，补标 %d 个。Ctrl+Z 可整次撤销。' % (len(pl['items']), n_del, n_add), title=TITLE)
    except Exception:
        forms.alert('出错了（已回滚，模型没被改）：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
