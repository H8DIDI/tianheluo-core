# -*- coding: utf-8 -*-
"""
一键清理重复松木桩与垫层
================================================================
扫描全项目中因重复框选或重复布置而空间重叠的松木桩与垫层，
每处位置只保留一套（保留最早生成的那个），其余的删除。整次可 Ctrl+Z 撤销。
"""

__title__ = "清理重复\n松木桩垫层"
__author__ = "didi"
__doc__ = ("扫描全项目重复布置、空间完全重合的松木桩与垫层（坐标 5 mm / 外包框 10 mm 内算重合），\n"
           "每处只保留最早生成的一套，其余删除。先列出数量再确认，整次可 Ctrl+Z 撤销。\n"
           "只认「一键布置松木桩垫层」生成的图元（备注含「宿主」），以及族名含「松木桩 / Φ75 / 井垫层」的族。")

import traceback
import clr
clr.AddReference('RevitAPI')
from System.Collections.Generic import List
from pyrevit import revit, DB, forms

doc = revit.doc
TITLE = u"清理重复松木桩垫层"
APP_ID = "PinePileCushionPlugin"     # 一键布置松木桩垫层 生成的垫层（DirectShape）的标识
PILE_KEYS = (u"松木桩", u"Φ75")
PAD_FAMILY_KEYS = (u"市政_井垫层", u"井垫层")


def _eid(x):
    try:
        return x.Value
    except Exception:
        return x.IntegerValue


def _comment(e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        return (p.AsString() or u"") if p is not None else u""
    except Exception:
        return u""


def _type_text(inst):
    out = []
    try:
        sym = inst.Symbol
        out.append(sym.FamilyName or u"")
        out.append(sym.Name or u"")
    except Exception:
        pass
    return u" ".join(out)


def _key_pt(p, step):
    return (round(p.X * 304.8 / step), round(p.Y * 304.8 / step), round(p.Z * 304.8 / step))


def _dups(groups):
    """每组按 Id 从小到大，保留第一个（最早生成的），其余为重复。"""
    out = []
    for ids in groups.values():
        if len(ids) > 1:
            ids.sort(key=_eid)
            out.extend(ids[1:])
    return out


def scan():
    piles, pads, fampads = {}, {}, {}
    n_piles = 0
    for inst in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance)\
            .OfCategory(DB.BuiltInCategory.OST_StructuralFoundation):
        txt = _type_text(inst)
        com = _comment(inst)
        loc = getattr(inst, "Location", None)
        if not isinstance(loc, DB.LocationPoint):
            continue
        is_pile = any(k in txt for k in PILE_KEYS) or (u"宿主" in com and u"松木桩" in com)
        is_pad = any(k in txt for k in PAD_FAMILY_KEYS) or (u"宿主" in com and u"垫层" in com)
        if is_pile:
            n_piles += 1
            piles.setdefault(_key_pt(loc.Point, 5.0), []).append(inst.Id)
        elif is_pad:
            # 井垫层按外包框比：同一插入点但尺寸不同（不同井）的不算重复
            bb = inst.get_BoundingBox(None)
            key = (_key_pt(bb.Min, 10.0) + _key_pt(bb.Max, 10.0)) if bb is not None else _key_pt(loc.Point, 10.0)
            fampads.setdefault(key, []).append(inst.Id)
    for ds in DB.FilteredElementCollector(doc).OfClass(DB.DirectShape)\
            .OfCategory(DB.BuiltInCategory.OST_StructuralFoundation):
        if not (ds.ApplicationId == APP_ID or u"垫层" in (ds.Name or u"") or
                (u"宿主" in _comment(ds) and u"垫层" in _comment(ds))):
            continue
        bb = ds.get_BoundingBox(None)
        if bb is None:
            continue
        pads.setdefault(_key_pt(bb.Min, 10.0) + _key_pt(bb.Max, 10.0), []).append(ds.Id)
    return n_piles, _dups(piles), _dups(pads), _dups(fampads)


def main():
    try:
        n_piles, pile_dups, ds_dups, fampad_dups = scan()
        total = len(pile_dups) + len(ds_dups) + len(fampad_dups)
        if total == 0:
            forms.alert(u"没有发现重复重合的松木桩或垫层。\n\n现有松木桩 {} 根，位置都不重复。".format(n_piles), title=TITLE)
            return
        msg = (u"扫描结果：\n\n"
               u"• 重合的松木桩：{} 根（共 {} 根）\n"
               u"• 重合的垫层（DirectShape）：{} 块\n"
               u"• 重合的井垫层族：{} 块\n\n"
               u"每处位置只保留最早生成的一套，删除其余 {} 个图元。整次可以 Ctrl+Z 撤销。\n\n确定清理？"
               ).format(len(pile_dups), n_piles, len(ds_dups), len(fampad_dups), total)
        # 注意：天河落宿主里 ok+cancel 只会显示「取消」，确认框必须用 yes/no
        if not forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
            return
        t = DB.Transaction(doc, u"清理重复松木桩与垫层")
        t.Start()
        try:
            doc.Delete(List[DB.ElementId](pile_dups + ds_dups + fampad_dups))
            t.Commit()
        except Exception:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            raise
        forms.alert(u"清理完成：删除重复松木桩 {} 根、重复垫层 {} 块，共 {} 个图元。".format(
            len(pile_dups), len(ds_dups) + len(fampad_dups), total), title=TITLE)
    except Exception as e:
        forms.alert(u"清理出错，模型未改动：\n" + str(e) + u"\n\n" + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
