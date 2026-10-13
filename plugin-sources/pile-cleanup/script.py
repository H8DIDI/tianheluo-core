# -*- coding: utf-8 -*-
"""
一键清理重复松木桩与垫层
================================================================
功能描述:
  扫描全项目中因重复框选或重复布置而导致空间重叠的松木桩与垫层，
  自动识别多余图元并一键安全清理，恢复纯净模型与准确工程量。
"""

__title__ = "清理重复\n松木桩垫层"
__author__ = "Antigravity Assistant"

import sys
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
from Autodesk.Revit import DB
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms

doc = revit.doc
uidoc = revit.uidoc


def main():
    try:
        # 1. 扫描重合的松木桩 (坐标重叠容差 5mm 内)
        col_piles = DB.FilteredElementCollector(doc)\
            .OfClass(DB.FamilyInstance)\
            .OfCategory(DB.BuiltInCategory.OST_StructuralFoundation)

        piles_by_coord = {}
        total_piles = 0

        for inst in col_piles:
            fn = ""
            try:
                p = inst.Symbol.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
                fn = p.AsString() if p else ""
            except Exception:
                pass
            if not fn:
                try:
                    fn = inst.Symbol.Name
                except Exception:
                    pass
            
            if "松木桩" in fn or "Φ75" in fn:
                total_piles += 1
                loc = inst.Location.Point
                ckey = (
                    round(loc.X * 304.8 / 5.0) * 5.0,
                    round(loc.Y * 304.8 / 5.0) * 5.0,
                    round(loc.Z * 304.8 / 5.0) * 5.0
                )
                if ckey not in piles_by_coord:
                    piles_by_coord[ckey] = []
                piles_by_coord[ckey].append(inst.Id)

        pile_dups = []
        for ckey, id_list in piles_by_coord.items():
            if len(id_list) > 1:
                for eid in id_list[1:]:
                    pile_dups.append(eid)

        # 2. 扫描重叠的 DirectShape 垫层 (外包框容差 10mm 内)
        col_ds = DB.FilteredElementCollector(doc)\
            .OfClass(DB.DirectShape)\
            .OfCategory(DB.BuiltInCategory.OST_StructuralFoundation)

        ds_by_bb = {}
        for ds in col_ds:
            if ds.ApplicationId == "PinePileCushionPlugin" or "垫层" in (ds.Name or ""):
                bb = ds.get_BoundingBox(None)
                if bb:
                    key = (
                        round(bb.Min.X * 304.8 / 10.0) * 10.0,
                        round(bb.Min.Y * 304.8 / 10.0) * 10.0,
                        round(bb.Min.Z * 304.8 / 10.0) * 10.0,
                        round(bb.Max.X * 304.8 / 10.0) * 10.0,
                        round(bb.Max.Y * 304.8 / 10.0) * 10.0,
                        round(bb.Max.Z * 304.8 / 10.0) * 10.0
                    )
                else:
                    key = ds.Id.Value
                if key not in ds_by_bb:
                    ds_by_bb[key] = []
                ds_by_bb[key].append(ds.Id)

        ds_dups = []
        for key, id_list in ds_by_bb.items():
            if len(id_list) > 1:
                for eid in id_list[1:]:
                    ds_dups.append(eid)

        # 3. 扫描重叠的 市政_井垫层
        col_fampad = DB.FilteredElementCollector(doc)\
            .OfClass(DB.FamilyInstance)\
            .OfCategory(DB.BuiltInCategory.OST_StructuralFoundation)

        fampad_by_coord = {}
        for inst in col_fampad:
            fn = ""
            try:
                p = inst.Symbol.get_Parameter(DB.BuiltInParameter.ALL_MODEL_FAMILY_NAME)
                fn = p.AsString() if p else ""
            except Exception:
                pass
            if "市政_井垫层" in fn or "井垫层" in fn:
                loc = inst.Location.Point
                ckey = (
                    round(loc.X * 304.8 / 10.0) * 10.0,
                    round(loc.Y * 304.8 / 10.0) * 10.0,
                    round(loc.Z * 304.8 / 10.0) * 10.0
                )
                if ckey not in fampad_by_coord:
                    fampad_by_coord[ckey] = []
                fampad_by_coord[ckey].append(inst.Id)

        fampad_dups = []
        for key, id_list in fampad_by_coord.items():
            if len(id_list) > 1:
                for eid in id_list[1:]:
                    fampad_dups.append(eid)

        total_dups = len(pile_dups) + len(ds_dups) + len(fampad_dups)

        if total_dups == 0:
            forms.alert(
                "✅ 模型非常纯净！\n\n"
                "经全模型空间坐标与外包几何比对，未发现任何重复重叠的松木桩或垫层。\n"
                "• 现有松木桩：{} 根 (均为独立位置)".format(total_piles),
                title="清理重复松木桩垫层"
            )
            return

        # 4. 提示确认
        prompt_msg = (
            "🔍 全模型重复图元扫描结果：\n\n"
            "• 重复重合松木桩：{} 根\n"
            "• 重复重叠 DirectShape 垫层：{} 块\n"
            "• 重复重叠族垫层：{} 块\n\n"
            "共检测到 {} 个完全空间重合的冗余图元！\n"
            "点击“确认立即清理”，系统将仅保留每处位置的 1 套有效图元，安全删除多余重合图元。"
        ).format(len(pile_dups), len(ds_dups), len(fampad_dups), total_dups)

        res = forms.alert(prompt_msg, title="清理重复松木桩垫层", ok=True, cancel=True)
        if not res:
            return

        # 5. 执行清理事务
        t = DB.Transaction(doc, "一键清理重复松木桩与垫层")
        t.Start()
        all_del = pile_dups + ds_dups + fampad_dups
        doc.Delete(List[DB.ElementId](all_del))
        t.Commit()

        forms.alert(
            "🎉 重复图元清理完成！\n\n"
            "• 已删除重复松木桩：{} 根\n"
            "• 已删除重复垫层：{} 块\n"
            "• 累计清除冗余图元：{} 个\n\n"
            "模型基础工程量已精准校准！".format(len(pile_dups), len(ds_dups) + len(fampad_dups), total_dups),
            title="清理完成"
        )

    except Exception as e:
        forms.alert("清理过程发生异常:\n" + str(e) + "\n\n" + traceback.format_exc(), title="报错追踪")


if __name__ == '__main__':
    main()
