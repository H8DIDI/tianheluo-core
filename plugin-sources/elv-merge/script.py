import clr
clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import *
from pyrevit import forms
import traceback

doc = __revit__.ActiveUIDocument.Document

family_prefix_map = {
    "ELE-EM1-I": "EM1",
    "ELE-EM1-L": "EM1",
    "ELE-EM2-I": "EM2",
    "ELE-EM2-L": "EM2",
    "ELE-EM4-I": "EM4",
    "ELE-EM4-L": "EM4",
    "ELE-EM4-T": "EM4"
}

instances_updated = 0
deleted_count = 0
skipped_grouped = 0
error_msg = ""

t = Transaction(doc, "强电井瘦身合并")
t.Start()

try:
    fam_coll = FilteredElementCollector(doc).OfClass(Family)
    for f in fam_coll:
        if f.Name not in family_prefix_map:
            continue
        correct_prefix = family_prefix_map[f.Name]
        
        symbol_ids = f.GetFamilySymbolIds()
        all_symbols = [doc.GetElement(sid) for sid in symbol_ids]
        
        symbol_target_name = {}
        base_symbols = {}
        
        for fs in all_symbols:
            old_name = fs.Name
            new_name = old_name
            
            if old_name.startswith("EM1-") and correct_prefix == "EM2":
                new_name = "EM2-" + old_name[4:]
                
            if ")x" in new_name:
                depth_index = new_name.rfind(")x")
                first_x = new_name.find("(L)x")
                if first_x > 0 and depth_index > first_x + 2:
                    new_name = new_name[:depth_index+1]
                    
            symbol_target_name[fs] = new_name
            if old_name == new_name:
                base_symbols[new_name] = fs

        for fs in all_symbols:
            if fs not in symbol_target_name: continue
            target = symbol_target_name[fs]
            if target not in base_symbols:
                new_base = fs.Duplicate(target)
                if not new_base.IsActive: new_base.Activate()
                base_symbols[target] = new_base

        symbols_to_delete = []
        for fs in all_symbols:
            if fs not in symbol_target_name: continue
            target_name = symbol_target_name[fs]
            target_symbol = base_symbols[target_name]
            
            if fs.Id != target_symbol.Id:
                if not target_symbol.IsActive: target_symbol.Activate()
                
                inst_coll = FilteredElementCollector(doc).WherePasses(FamilyInstanceFilter(doc, fs.Id))
                for inst in inst_coll:
                    # Check if in a group
                    if inst.GroupId != ElementId.InvalidElementId:
                        skipped_grouped += 1
                        continue
                        
                    # Handle pinned elements
                    was_pinned = inst.Pinned
                    if was_pinned:
                        inst.Pinned = False
                        
                    try:
                        inst.Symbol = target_symbol
                        instances_updated += 1
                    except Exception as e:
                        pass
                        
                    # Re-pin if it was pinned
                    if was_pinned:
                        inst.Pinned = True
                        
                symbols_to_delete.append(fs.Id)
                
        for sid in symbols_to_delete:
            try:
                doc.Delete(sid)
                deleted_count += 1
            except:
                pass

    t.Commit()
except Exception as ex:
    t.RollBack()
    error_msg = traceback.format_exc()

if error_msg:
    forms.alert("程序发生异常:\n" + error_msg, title="错误日志")
else:
    msg = "强电预制井清理完成！\n\n共合并更新了 {} 个井。\n永久删除了 {} 个带有深度的冗余垃圾类型！".format(instances_updated, deleted_count)
    if skipped_grouped > 0:
        msg += "\n\n⚠️ 警告: 有 {} 个井因为被编入了【编组(Group)】中，无法直接修改类型被跳过。请解组后再试。".format(skipped_grouped)
    forms.alert(msg, title="大瘦身成功")
