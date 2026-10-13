# -*- coding: utf-8 -*-
__title__ = '全自动智能编号'
__doc__ = '支持先选中再运行。给常规模型按空间坐标全自动排号。'

from pyrevit import revit, DB, UI, forms

doc = revit.doc
uidoc = revit.uidoc

def main():
    # 获取提前选中的元素
    pre_selected_ids = uidoc.Selection.GetElementIds()

    prefix = forms.ask_for_string(default='11KV-', prompt='请输入井的编号前缀:', title='全自动智能编号')
    if prefix is None: return

    start_num_str = forms.ask_for_string(default='1', prompt='请输入起始数字:', title='全自动智能编号')
    if not start_num_str or not start_num_str.isdigit(): return
    start_num = int(start_num_str)
    
    filter_comment = forms.ask_for_string(default='11kv', prompt='【高级过滤】\n你想只给哪种注释的井编号？\n(填 11kv 则跳过其他井，清空则全选):', title='全自动智能编号')

    sort_opts = {
        '从左到右 (X轴递增)': 'X_ASC',
        '从右到左 (X轴递减)': 'X_DESC',
        '从下到上 (Y轴递增)': 'Y_ASC',
        '从上到下 (Y轴递减)': 'Y_DESC'
    }
    selected_sort = forms.CommandSwitchWindow.show(
        sort_opts.keys(),
        message='请选择编号的自动排序方向：'
    )
    if not selected_sort: return
    sort_logic = sort_opts[selected_sort]

    all_elements = []

    # 如果用户提前选中了东西，直接用提前选中的
    if pre_selected_ids:
        all_elements = [doc.GetElement(id) for id in pre_selected_ids]
    else:
        # 否则让他们再去选
        class GenericModelFilter(UI.Selection.ISelectionFilter):
            def AllowElement(self, elem):
                return elem.Category is not None and elem.Category.Id.Value == int(DB.BuiltInCategory.OST_GenericModel)
            def AllowReference(self, ref, pos): return False

        try:
            # 提示用户点“完成”
            references = uidoc.Selection.PickObjects(UI.Selection.ObjectType.Element, GenericModelFilter(), "【重要】请框选井，框选完必须点击屏幕左上角小小的【完成】按钮！")
            all_elements = [doc.GetElement(r) for r in references]
        except:
            return 

    if not all_elements:
        return
    
    valid_elements = []
    for elem in all_elements:
        # 只处理常规模型
        if elem.Category is None or elem.Category.Id.Value != int(DB.BuiltInCategory.OST_GenericModel):
            continue

        if not filter_comment:
            valid_elements.append(elem)
            continue
            
        comment_param = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if not comment_param:
            comment_param = elem.LookupParameter("注释")
            
        if comment_param and comment_param.AsString():
            val = comment_param.AsString()
            if filter_comment.lower() in val.lower():
                valid_elements.append(elem)

    skipped_count = len(all_elements) - len(valid_elements)

    if not valid_elements:
        forms.alert("选中了图元，但没有任何一个图元的【注释】包含 '{}'！".format(filter_comment), title='Antigravity 错误提示')
        return

    def get_sort_val(elem):
        bbox = elem.get_BoundingBox(None)
        if bbox:
            center = (bbox.Min + bbox.Max) / 2.0
            if 'X' in sort_logic: return center.X
            if 'Y' in sort_logic: return center.Y
        return 0.0

    valid_elements.sort(key=get_sort_val, reverse=('DESC' in sort_logic))

    count = 0
    with revit.Transaction("全自动智能编号"):
        for elem in valid_elements:
            num_str = str(start_num + count).zfill(2)
            mark_value = "{}{}".format(prefix, num_str)
            
            mark_param = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
            if mark_param and not mark_param.IsReadOnly:
                mark_param.Set(mark_value)
                count += 1

    msg = "成功为 {} 个 11kv井 打上了编号！".format(count)
    if skipped_count > 0:
        msg += "\n\n(已自动为你跳过了 {} 个不符合注释条件的图元)".format(skipped_count)
        
    forms.alert(msg, title='Antigravity 专属工具')

if __name__ == '__main__':
    main()
