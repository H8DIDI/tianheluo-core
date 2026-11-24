# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "机电模型\n体检"
__author__ = "KAIFA"
__doc__ = ("机电模型一键体检（只读，不改模型）：Revit 警告按类型汇总、没有系统的管线、未连接的管线端口、重叠的同线管线、"
           "孤儿管件、过短管线（<10 mm）、没有标高的管线。结果弹窗给汇总，明细导出到 文档\KaifaTools\机电体检_时间.csv（Excel 能直接打开），"
           "报审前过一遍。")

import os
import io
import csv
import time
import math
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
from pyrevit import revit, DB, UI, forms

# @include _shared/compat.py

doc = revit.doc
TITLE = u"机电模型体检"
FT = 304.8
OUT_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools")
CURVE_CATS = [DB.BuiltInCategory.OST_PipeCurves, DB.BuiltInCategory.OST_DuctCurves, DB.BuiltInCategory.OST_CableTray, DB.BuiltInCategory.OST_Conduit]
FIT_CATS = [DB.BuiltInCategory.OST_PipeFitting, DB.BuiltInCategory.OST_DuctFitting, DB.BuiltInCategory.OST_CableTrayFitting, DB.BuiltInCategory.OST_ConduitFitting]


def _u(x):
    try:
        return u"%s" % x
    except Exception:
        return u"?"


def _pt(e):
    try:
        loc = e.Location
        if hasattr(loc, 'Point'):
            p = loc.Point
        else:
            c = loc.Curve
            p = (c.GetEndPoint(0) + c.GetEndPoint(1)) / 2.0
        return u"%.0f,%.0f,%.0f" % (p.X * FT, p.Y * FT, p.Z * FT)
    except Exception:
        return u""


def _type_name(e):
    try:
        t = doc.GetElement(e.GetTypeId())
        return _u(t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() or t.Name)
    except Exception:
        return u""


def _level(e):
    for bip in (DB.BuiltInParameter.RBS_START_LEVEL_PARAM, DB.BuiltInParameter.FAMILY_LEVEL_PARAM, DB.BuiltInParameter.SCHEDULE_LEVEL_PARAM):
        try:
            p = e.get_Parameter(bip)
            if p is not None and p.HasValue:
                l = doc.GetElement(p.AsElementId())
                if l is not None:
                    return _u(l.Name)
        except Exception:
            pass
    return u""


def run():
    rows = []      # (问题类别, 图元id, 类别, 类型, 位置mm, 说明)
    t0 = time.time()
    # 1) 警告
    wcount = {}
    try:
        for w in doc.GetWarnings():
            txt = _u(w.GetDescriptionText())
            wcount[txt] = wcount.get(txt, 0) + 1
            for i in list(w.GetFailingElements())[:3]:
                e = doc.GetElement(i)
                if e is not None and e.Category is not None and thl_eid(e.Category.Id) in [int(c) for c in CURVE_CATS + FIT_CATS]:
                    rows.append((u"Revit警告", thl_eid(e.Id), _u(e.Category.Name), _type_name(e), _pt(e), txt[:80]))
    except Exception:
        pass
    # 2) 管线：无系统 / 未连接端口 / 过短 / 无标高
    curves = []
    for c in CURVE_CATS:
        curves.extend(DB.FilteredElementCollector(doc).OfCategory(c).WhereElementIsNotElementType())
    n_nosys = n_open = n_short = n_nolvl = 0
    for e in curves:
        cid = thl_eid(e.Id)
        cat = _u(e.Category.Name)
        try:
            ln = e.Location.Curve
            L = ln.Length * FT
        except Exception:
            continue
        if L < 10.0:
            n_short += 1
            rows.append((u"过短管线", cid, cat, _type_name(e), _pt(e), u"长度 %.1f mm" % L))
        is_pipe_duct = thl_eid(e.Category.Id) in (int(DB.BuiltInCategory.OST_PipeCurves), int(DB.BuiltInCategory.OST_DuctCurves))
        if is_pipe_duct:
            try:
                sn = e.get_Parameter(DB.BuiltInParameter.RBS_SYSTEM_NAME_PARAM)
                has_sys = sn is not None and (sn.AsString() or u"").strip() != u""
                if not has_sys:
                    n_nosys += 1
                    rows.append((u"没有系统", cid, cat, _type_name(e), _pt(e), u"没有系统名称"))
            except Exception:
                pass
        try:
            for cn in e.ConnectorManager.Connectors:
                if not cn.IsConnected:
                    n_open += 1
        except Exception:
            pass
        if not _level(e):
            n_nolvl += 1
            rows.append((u"没有标高", cid, cat, _type_name(e), _pt(e), u"取不到参照标高"))
    # 3) 孤儿管件
    n_orphan = 0
    for c in FIT_CATS:
        for f in DB.FilteredElementCollector(doc).OfCategory(c).WhereElementIsNotElementType():
            try:
                cons = list(f.MEPModel.ConnectorManager.Connectors)
            except Exception:
                continue
            if cons and all(not cn.IsConnected for cn in cons):
                n_orphan += 1
                rows.append((u"孤儿管件", thl_eid(f.Id), _u(f.Category.Name), _type_name(f), _pt(f), u"所有接口都悬空"))
    # 4) 重叠管线（只读：借用管线清理的算法太长，这里做简版——同类别、端点几乎重合）
    n_dup = 0
    seen = {}
    for e in curves:
        try:
            ln = e.Location.Curve
            a, b = ln.GetEndPoint(0), ln.GetEndPoint(1)
            k = (thl_eid(e.Category.Id),) + tuple(sorted([(round(a.X * FT / 20), round(a.Y * FT / 20), round(a.Z * FT / 20)), (round(b.X * FT / 20), round(b.Y * FT / 20), round(b.Z * FT / 20))]))
        except Exception:
            continue
        if k in seen:
            n_dup += 1
            rows.append((u"重叠管线", thl_eid(e.Id), _u(e.Category.Name), _type_name(e), _pt(e), u"与 %d 两端重合（20 mm 内）" % seen[k]))
        else:
            seen[k] = thl_eid(e.Id)
    # 导出
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    path = os.path.join(OUT_DIR, u"机电体检_%s.csv" % time.strftime("%Y%m%d_%H%M%S"))
    with io.open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow([u"问题", u"图元ID", u"类别", u"类型", u"位置 x,y,z (mm)", u"说明"])
        for r in rows:
            w.writerow(list(r))
        w.writerow([])
        w.writerow([u"Revit 警告汇总", u"数量"])
        for k, v in sorted(wcount.items(), key=lambda kv: -kv[1]):
            w.writerow([k, v])
    top = sorted(wcount.items(), key=lambda kv: -kv[1])[:5]
    msg = [u"项目：%s" % _u(doc.Title),
           u"管线 %d 根。" % len(curves),
           u"Revit 警告：%d 条（%d 种）%s" % (sum(wcount.values()), len(wcount), u"，最多的：" + u"；".join(u"%s ×%d" % (k[:30], v) for k, v in top) if top else u""),
           u"没有系统的水管/风管：%d" % n_nosys,
           u"未连接的管线端口：%d（含正常的末端）" % n_open,
           u"重叠管线（两端重合）：%d" % n_dup,
           u"孤儿管件：%d" % n_orphan,
           u"过短管线（<10 mm）：%d" % n_short,
           u"没有标高的管线：%d" % n_nolvl,
           u"\n明细（%d 行）：%s" % (len(rows), path), u"用时 %.1f 秒（只读，没改模型）。" % (time.time() - t0)]
    return u"\n".join(msg), path


def main():
    try:
        msg, path = run()
        forms.alert(msg, title=TITLE)
    except Exception as ex:
        forms.alert(u"出错了：\n%s\n\n%s" % (ex, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
