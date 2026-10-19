# -*- coding: utf-8 -*-
__title__ = '一键清除井标记'
__doc__ = '框选平面图中的井，一键清空选中井的【标记】字段（如室内井不需要标记）。\n支持先在平面图中框选井再点按钮，或先点按钮再拉框框选。\n只清空【标记】字段，其他所有参数、尺寸标注及模型图元均完好无损！'

from pyrevit import revit, DB, UI, forms
import System

doc = revit.doc
uidoc = revit.uidoc


class SuppressWarnings(DB.IFailuresPreprocessor):
    """静默处理所有警告，消除弹窗阻断"""
    def PreprocessFailures(self, failuresAccessor):
        for f in failuresAccessor.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                failuresAccessor.DeleteWarning(f)
        return DB.FailureProcessingResult.Continue


def main():
    # 1. 获取预选图元 (先框选再点按钮)
    selected_ids = list(uidoc.Selection.GetElementIds())

    # 2. 若未预选，进入拉框框选模式 (先点按钮再框选)
    if not selected_ids:
        try:
            picked_elements = uidoc.Selection.PickElementsByRectangle(
                "请在平面图中拉框框选需要清除【标记】的井："
            )
            if picked_elements:
                selected_ids = [e.Id for e in picked_elements]
        except Exception:
            # 用户按 ESC 取消
            return

    if not selected_ids:
        return

    # 3. 收集所有包含“标记”且当前非空的图元
    target_elements = []
    for eid in selected_ids:
        elem = doc.GetElement(eid)
        if not elem:
            continue

        # 优先读取 BuiltInParameter.ALL_MODEL_MARK
        p_mark = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        if not p_mark:
            p_mark = elem.LookupParameter("标记")

        if p_mark and not p_mark.IsReadOnly:
            current_val = p_mark.AsString() or ""
            if current_val.strip():
                target_elements.append((elem, p_mark, current_val.strip()))

    if not target_elements:
        forms.alert(
            "选中的图元中没有需要清空的【标记】值（可能标记本就为空，或选中的不是井）。",
            title="一键清除井标记"
        )
        return

    # 4. 在安全事务中仅将【标记】字段清空，不修改任何其他参数或图元
    t = DB.Transaction(doc, "一键清除井标记")
    fho = t.GetFailureHandlingOptions()
    fho.SetFailuresPreprocessor(SuppressWarnings())
    fho.SetClearAfterRollback(True)
    t.SetFailureHandlingOptions(fho)

    cleared_marks = []
    t.Start()
    try:
        for elem, p_mark, old_val in target_elements:
            p_mark.Set("")
            cleared_marks.append(old_val)
        t.Commit()
    except Exception as ex:
        t.RollBack()
        forms.alert("清空标记字段失败: {}".format(str(ex)), title="错误")
        return

    # 5. 反馈提示
    sample_str = ", ".join(cleared_marks[:10])
    if len(cleared_marks) > 10:
        sample_str += " 等共 {} 个井".format(len(cleared_marks))

    msg = "【井标记字段清空成功】\n\n"
    msg += "• 成功清空标记的井: {} 个\n".format(len(cleared_marks))
    msg += "• 清空的标记编号: {}\n".format(sample_str)
    msg += "• 其他所有参数、尺寸及模型图元均完好无损！\n\n"
    msg += "(提示: 如需恢复，可在 Revit 中直接按 Ctrl + Z 撤销)"

    forms.alert(msg, title="一键清除井标记")


if __name__ == '__main__':
    main()
