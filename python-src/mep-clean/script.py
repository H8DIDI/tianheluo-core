# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "管线清理\n去重叠 / 悬空管件"
__author__ = "KAIFA"
__doc__ = ("清理自动建模或乱连留下的垃圾：① 重叠的同线管线（水管、风管、桥架、线管：走向平行、高差和横向偏差在 20 mm 内、重叠超过较短一根的一半，删较短的那根）；"
           "② 悬空管件（管件的接口没连上任何东西：默认只删「全部接口都悬空」的孤儿管件，也可以改成「有任何一个接口悬空」）。\n"
           "范围可选：选中的 / 当前视图 / 整个项目。先预览数量和示例编号，确认后才删；整次可 Ctrl+Z 撤销。")

import math
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms

# @include _shared/ws_guard.py
# @include _shared/compat.py

doc = revit.doc
uidoc = revit.uidoc
TITLE = u"管线清理"
FT = 304.8
MM = 1.0 / 304.8

CURVE_CATS = [DB.BuiltInCategory.OST_PipeCurves, DB.BuiltInCategory.OST_DuctCurves, DB.BuiltInCategory.OST_CableTray, DB.BuiltInCategory.OST_Conduit]
FITTING_CATS = [DB.BuiltInCategory.OST_PipeFitting, DB.BuiltInCategory.OST_DuctFitting, DB.BuiltInCategory.OST_CableTrayFitting, DB.BuiltInCategory.OST_ConduitFitting]
LAT_TOL = 20.0 * MM        # 横向偏差
Z_TOL = 20.0 * MM
OVERLAP_MIN = 0.5          # 重叠超过较短一根的比例


def _collector(view_id=None):
    return DB.FilteredElementCollector(doc, view_id) if view_id is not None else DB.FilteredElementCollector(doc)


def gather(scope):
    """按范围收集 (管线列表, 管件列表)。"""
    curves, fits = [], []
    if scope == u"selected":
        ids = list(uidoc.Selection.GetElementIds())
        cc = set(int(c) for c in CURVE_CATS)
        fc = set(int(c) for c in FITTING_CATS)
        for i in ids:
            e = doc.GetElement(i)
            if e is None or e.Category is None:
                continue
            cid = thl_eid(e.Category.Id)
            if cid in cc:
                curves.append(e)
            elif cid in fc:
                fits.append(e)
        return curves, fits
    vid = doc.ActiveView.Id if scope == u"view" else None
    for c in CURVE_CATS:
        curves.extend(_collector(vid).OfCategory(c).WhereElementIsNotElementType())
    for c in FITTING_CATS:
        fits.extend(_collector(vid).OfCategory(c).WhereElementIsNotElementType())
    return curves, fits


def find_overlaps(curves):
    """返回 [(要删的, 保留的)]。按 5 m 格子分桶，避免全项目两两比较。"""
    items = []
    for c in curves:
        loc = c.Location
        ln = getattr(loc, 'Curve', None)
        if ln is None or not isinstance(ln, DB.Line):
            continue
        a, b = ln.GetEndPoint(0), ln.GetEndPoint(1)
        L = ln.Length
        if L < 1e-6:
            continue
        items.append({'e': c, 'a': a, 'b': b, 'L': L, 'u': (b - a).Normalize(), 'cat': thl_eid(c.Category.Id), 'id': thl_eid(c.Id)})
    cell = 5.0 / 0.3048
    grid = {}
    for k, it in enumerate(items):
        x0, x1 = sorted((it['a'].X, it['b'].X))
        y0, y1 = sorted((it['a'].Y, it['b'].Y))
        for gx in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
            for gy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                grid.setdefault((gx, gy), []).append(k)
    dead, pairs, seen = set(), [], set()
    for ks in grid.values():
        ks = sorted(set(ks))
        for ii in range(len(ks)):
            for jj in range(ii + 1, len(ks)):
                i, j = ks[ii], ks[jj]
                if (i, j) in seen or i in dead or j in dead:
                    continue
                seen.add((i, j))
                A, B = items[i], items[j]
                if A['cat'] != B['cat'] or A['u'].CrossProduct(B['u']).GetLength() > 0.02:
                    continue
                if abs(A['a'].Z - B['a'].Z) > Z_TOL and abs(A['b'].Z - B['b'].Z) > Z_TOL:
                    continue
                d = B['a'] - A['a']
                if d.CrossProduct(A['u']).GetLength() > LAT_TOL:
                    continue
                t1, t2 = d.DotProduct(A['u']), (B['b'] - A['a']).DotProduct(A['u'])
                lo, hi = max(0.0, min(t1, t2)), min(A['L'], max(t1, t2))
                if hi - lo > OVERLAP_MIN * min(A['L'], B['L']):
                    kill, keep = (B, A) if A['L'] >= B['L'] else (A, B)
                    dead.add(items.index(kill))
                    pairs.append((kill['e'], keep['e']))
                    if kill is A:
                        break
    return pairs


def find_dangling(fits, strict_all):
    out = []
    for f in fits:
        try:
            cm = f.MEPModel.ConnectorManager
        except Exception:
            continue
        if cm is None:
            continue
        n_free, n = 0, 0
        for c in cm.Connectors:
            n += 1
            if not c.IsConnected:
                n_free += 1
        if n == 0:
            continue
        if (strict_all and n_free == n) or ((not strict_all) and n_free > 0):
            out.append(f)
    return out


def _ids(es, n=6):
    s = u"、".join(u"%d" % thl_eid(e.Id) for e in es[:n])
    return s + (u" 等" if len(es) > n else u"")


def main():
    try:
        sel = list(uidoc.Selection.GetElementIds())
        scopes = [(u"selected", u"选中的图元（%d 个）" % len(sel)), (u"view", u"当前视图里的"), (u"project", u"整个项目")]
        labels = [t for k, t in scopes]
        pick = forms.CommandSwitchWindow.show(labels, message=u"清理范围：")
        if pick is None:
            return
        scope = [k for k, t in scopes if t == pick][0]
        ops = [u"重叠管线 + 孤儿管件（全部接口都悬空）", u"重叠管线 + 悬空管件（任一接口悬空，更狠）", u"只清重叠管线", u"只清孤儿管件（全部接口都悬空）"]
        op = forms.CommandSwitchWindow.show(ops, message=u"清理内容：")
        if op is None:
            return
        do_dup = op in (ops[0], ops[1], ops[2])
        do_fit = op in (ops[0], ops[1], ops[3])
        strict = op != ops[1]
        curves, fits = gather(scope)
        dups = find_overlaps(curves) if do_dup else []
        dang = find_dangling(fits, strict) if do_fit else []
        kill = [k for k, keep in dups] + dang
        if not kill:
            forms.alert(u"扫描了管线 %d 根、管件 %d 个，没有发现要清理的。" % (len(curves), len(fits)), title=TITLE)
            return
        ok, blocked = wg_split(doc, kill)
        msg = [u"扫描：管线 %d 根、管件 %d 个。" % (len(curves), len(fits))]
        if do_dup:
            msg.append(u"重叠管线：将删除 %d 根（示例 %s）。" % (len(dups), _ids([k for k, keep in dups])))
        if do_fit:
            msg.append(u"%s：将删除 %d 个（示例 %s）。" % (u"孤儿管件" if strict else u"悬空管件", len(dang), _ids(dang)))
        msg.append(wg_note(blocked))
        msg.append(u"\n确认删除？（可 Ctrl+Z 撤销）")
        if not forms.alert(u"\n".join(m for m in msg if m), title=TITLE + u" — 预览（还没删）", yes=True, no=True):
            return
        t = DB.Transaction(doc, TITLE)
        t.Start()
        n_ok, n_fail = 0, 0
        try:
            for e in ok:
                try:
                    doc.Delete(e.Id)
                    n_ok += 1
                except Exception:
                    n_fail += 1
            t.Commit()
        except Exception:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            raise
        forms.alert(u"已删除 %d 个%s。" % (n_ok, (u"，%d 个删不掉（被连带删除或受保护）" % n_fail) if n_fail else u""), title=TITLE)
    except Exception as ex:
        forms.alert(u"出错了（已回滚）：\n%s\n\n%s" % (ex, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
