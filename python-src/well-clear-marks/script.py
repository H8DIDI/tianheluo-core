# -*- coding: utf-8 -*-
__title__ = '一键清除井标记'
__doc__ = ('框选平面图中的井，一键清空选中井的【标记】字段（如室内井不需要标记）。\n'
           '支持先在平面图中框选井再点按钮，或先点按钮再拉框框选。\n'
           '只处理井（常规模型）：框选时顺带选到的排管、管线等其他图元默认不动；'
           '清空前会列出要清的井号让你确认。只清空【标记】字段，Ctrl+Z 可整体撤销。\n'
           '注意：井大样图纸靠井号对应井，清掉已出图井的标记后，这些图纸会找不到对应的井。')

from pyrevit import revit, DB, UI, forms
import System
import System.Windows.Forms as _WF

doc = revit.doc
uidoc = revit.uidoc

TITLE = '一键清除井标记'
_WELL_CAT = int(DB.BuiltInCategory.OST_GenericModel)   # 井族所在的类别


class SuppressWarnings(DB.IFailuresPreprocessor):
    """静默处理所有警告，消除弹窗阻断"""
    def PreprocessFailures(self, failuresAccessor):
        for f in failuresAccessor.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                failuresAccessor.DeleteWarning(f)
        return DB.FailureProcessingResult.Continue


# @include _shared/ws_guard.py


def _mark_param(elem):
    p = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
    if not p:
        p = elem.LookupParameter("标记")
    return p


def _is_well(elem):
    try:
        return isinstance(elem, DB.FamilyInstance) and elem.Category is not None and elem.Category.Id.Value == _WELL_CAT
    except Exception:
        return False


def _sheet_names():
    """全部图纸的「图号 图名」，用来提醒「这口井已经出图」。"""
    names = []
    try:
        for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
            names.append(u"{} {}".format(s.SheetNumber, s.Name))
    except Exception:
        pass
    return names


def _sample(marks, n=10):
    s = u", ".join(marks[:n])
    if len(marks) > n:
        s += u" 等共 {} 个".format(len(marks))
    return s


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

    # 3. 收集「标记」非空的图元，分成井和其他图元
    wells = []
    others = []
    for eid in selected_ids:
        elem = doc.GetElement(eid)
        if not elem:
            continue
        p_mark = _mark_param(elem)
        if p_mark and not p_mark.IsReadOnly:
            current_val = (p_mark.AsString() or "").strip()
            if current_val:
                (wells if _is_well(elem) else others).append((elem, p_mark, current_val))

    if not wells and not others:
        forms.alert(
            "选中的图元中没有需要清空的【标记】值（可能标记本就为空，或选中的不是井）。",
            title=TITLE
        )
        return

    # 协同模型：被别人占用的图元清不了，先排除并在确认框里说明（否则整批回滚）
    _ok, blocked = wg_split(doc, [e for e, _p, _m in wells + others])
    if blocked:
        bad = set(e.Id for e, _w in blocked)
        wells = [w for w in wells if w[0].Id not in bad]
        others = [o for o in others if o[0].Id not in bad]
    note = wg_note(blocked, lambda e: (_mark_param(e).AsString() or u'').strip())
    if not wells and not others:
        forms.alert(u"选中的图元都改不了。" + note, title=TITLE)
        return

    # 4. 防呆：先列出要清的内容，确认后才执行
    names = _sheet_names()
    on_sheet = [m for _e, _p, m in wells if any(m in nm for nm in names)]
    msg = u""
    if wells:
        msg += u"将清空 {} 口井的【标记】：\n{}\n".format(len(wells), _sample([m for _e, _p, m in wells]))
    else:
        msg += u"框选范围里没有井（常规模型）。\n"
    if on_sheet:
        msg += u"\n其中 {} 口井已经有图纸（图名里有这个井号）：{}\n清掉后这些图纸会找不到对应的井。\n".format(len(on_sheet), _sample(on_sheet, 6))
    if others:
        cats = {}
        for e, _p, _m in others:
            nm = e.Category.Name if e.Category is not None else u"其他"
            cats[nm] = cats.get(nm, 0) + 1
        msg += u"\n另有 {} 个其他图元也带标记（{}），默认不动。\n".format(
            len(others), u"、".join(u"{} {} 个".format(k, v) for k, v in sorted(cats.items())))
        msg += note
        msg += u"\n\n是：只清井的标记\n否：井和其他图元的标记一起清\n取消：不清"
        ans = _WF.MessageBox.Show(msg, TITLE, _WF.MessageBoxButtons.YesNoCancel, _WF.MessageBoxIcon.Question)
        if ans == _WF.DialogResult.Cancel:
            return
        targets = wells if ans == _WF.DialogResult.Yes else wells + others
    else:
        msg += note
        msg += u"\n确定清空吗？（Ctrl+Z 可撤销）"
        ans = _WF.MessageBox.Show(msg, TITLE, _WF.MessageBoxButtons.OKCancel, _WF.MessageBoxIcon.Question)
        if ans != _WF.DialogResult.OK:
            return
        targets = wells
    if not targets:
        forms.alert(u"没有需要清空的井标记，未做任何修改。", title=TITLE)
        return

    # 5. 在安全事务中仅将【标记】字段清空，不修改任何其他参数或图元
    t = DB.Transaction(doc, TITLE)
    fho = t.GetFailureHandlingOptions()
    fho.SetFailuresPreprocessor(SuppressWarnings())
    fho.SetClearAfterRollback(True)
    t.SetFailureHandlingOptions(fho)

    cleared_marks = []
    t.Start()
    try:
        for elem, p_mark, old_val in targets:
            p_mark.Set("")
            cleared_marks.append(old_val)
        t.Commit()
    except Exception as ex:
        t.RollBack()
        forms.alert("清空标记字段失败: {}".format(str(ex)), title="错误")
        return

    # 6. 反馈提示
    msg = u"【标记清空完成】\n\n"
    msg += u"• 清空了 {} 个图元的标记：{}\n".format(len(cleared_marks), _sample(cleared_marks))
    msg += u"• 其他所有参数、尺寸及模型图元均未改动。\n\n"
    msg += u"(提示: 如需恢复，可在 Revit 中直接按 Ctrl + Z 撤销)"
    forms.alert(msg, title=TITLE)


if __name__ == '__main__':
    main()
