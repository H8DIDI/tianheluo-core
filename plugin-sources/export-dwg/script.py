# -*- coding: utf-8 -*-
__title__ = '一键导出井大样DWG'
__doc__ = '一键批量导出所有井大样图纸为 DWG 文件，自动合并外部参照，直接保存在指定文件夹中。'

import os
import sys
import clr
import System
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms

doc = revit.doc
uidoc = revit.uidoc


def main():
    try:
        # 1. 收集全模型所有井大样图纸
        sheets = [
            s for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements()
            if s.Name and s.Name.startswith("井大样_")
        ]

        if not sheets or len(sheets) == 0:
            forms.alert('模型中未找到以【井大样_】开头的图纸！', title='提示')
            return

        # 2. 导出范围选择菜单
        # 菜单数量按当前模型实时统计 —— 原来写死的 947/127 等数字不会随后续
        # 增删图纸变化，容易让人以为"列表没刷新、导的还是老数据"。
        def _pick(prefix, exclude=None):
            out = []
            for s in sheets:
                nm = s.Name or ""
                if not nm.startswith("井大样_" + prefix):
                    continue
                if exclude and nm.startswith("井大样_" + exclude):
                    continue
                out.append(s)
            return out

        _grp = {
            'in': [s for s in sheets if (s.Name or "").startswith("井大样_室内-")],
            'AA': _pick("AA-"),
            'BB': _pick("BB-"),
            'A': _pick("A-", "AA-"),
            'B': _pick("B-", "BB-"),
            'T': _pick("T-"),
            'L': _pick("L-"),
            'E': _pick("E-"),
        }

        menu_opts = [
            '【1】先行测试：导出前 5 张样板井图纸（当前共 {} 张可选）'.format(len(sheets)),
            '【2】室内井全系列（当前 {} 张）'.format(len(_grp['in'])),
            '【3】AA- 强电特型井全系列（当前 {} 张）'.format(len(_grp['AA'])),
            '【4】BB- 弱电特型井全系列（当前 {} 张）'.format(len(_grp['BB'])),
            '【5】A- 强电井全系列（当前 {} 张）'.format(len(_grp['A'])),
            '【6】B- 弱电井全系列（当前 {} 张）'.format(len(_grp['B'])),
            '【7】T- 通信井全系列（当前 {} 张）'.format(len(_grp['T'])),
            '【8】L- 低压拉线井全系列（当前 {} 张）'.format(len(_grp['L'])),
            '【9】E- 预制井全系列（当前 {} 张）'.format(len(_grp['E'])),
            '【10】全模型全量导出（当前共 {} 张）'.format(len(sheets))
        ]

        choice = forms.SelectFromList.show(
            menu_opts,
            title='一键批量导出井大样 DWG - 范围选择',
            button_name='开始导出'
        )
        if not choice:
            return

        target_sheets = []
        if '【1】' in choice:
            # 原实现只取 AA- 前 5 张，模型里没有 AA- 井时就一张也导不出来；
            # 改成取当前全部井大样图纸的前 5 张，任何模型都能试跑。
            target_sheets = list(sheets)[:5]
        elif '【2】' in choice:
            target_sheets = [s for s in sheets if s.Name.startswith("井大样_室内-")]
        elif '【3】' in choice:
            target_sheets = [s for s in sheets if s.Name.startswith("井大样_AA-")]
        elif '【4】' in choice:
            target_sheets = [s for s in sheets if s.Name.startswith("井大样_BB-")]
        elif '【5】' in choice:
            target_sheets = [s for s in sheets if s.Name.startswith("井大样_A-") and not s.Name.startswith("井大样_AA-")]
        elif '【6】' in choice:
            target_sheets = [s for s in sheets if s.Name.startswith("井大样_B-") and not s.Name.startswith("井大样_BB-")]
        elif '【7】' in choice:
            target_sheets = [s for s in sheets if s.Name.startswith("井大样_T-")]
        elif '【8】' in choice:
            target_sheets = [s for s in sheets if s.Name.startswith("井大样_L-")]
        elif '【9】' in choice:
            target_sheets = [s for s in sheets if s.Name.startswith("井大样_E-")]
        elif '【10】' in choice:
            target_sheets = list(sheets)

        if not target_sheets:
            forms.alert('未找到符合选择条件的图纸！', title='警告')
            return

        # 3. 选择或确认导出文件夹
        desktop = System.Environment.GetFolderPath(System.Environment.SpecialFolder.Desktop)
        default_folder = os.path.join(desktop, "所有井大样图纸导出")
        if not os.path.exists(default_folder):
            os.makedirs(default_folder)

        # 4. 配置 DWG 导出参数 (不要把链接作为外部参照，直接合并到图纸中)
        all_dwg_setups = DB.FilteredElementCollector(doc)\
            .OfClass(DB.ExportDWGSettings)\
            .ToElements()

        dwg_setup = None
        for s in all_dwg_setups:
            if s.Name == "机电导图设置":
                dwg_setup = s
                break

        if dwg_setup:
            opt = dwg_setup.GetDWGExportOptions()
        else:
            opt = DB.DWGExportOptions()

        opt.MergedViews = True  # 核心：合并外部参照，不导出为独立 XREF 文件

        # 5. 循环导出并展示进度条
        success_count = 0
        new_count = 0
        overwrite_count = 0
        fail_count = 0
        fail_names = []
        skip_count = 0
        total = len(target_sheets)

        with forms.ProgressBar(title='正在批量导出井大样 DWG... ({value}/{max_value})', cancellable=True) as pb:
            for idx, s in enumerate(target_sheets):
                if pb.cancelled:
                    break
                pb.update_progress(idx + 1, total)

                # 覆盖式导出：不再"文件已存在就整张跳过"。
                # 原逻辑会让重新出图后磁盘上仍留着旧 DWG，看起来像"没更新/不是实时数据"。
                expected_file = os.path.join(default_folder, s.Name + ".dwg")
                existed = os.path.exists(expected_file)

                try:
                    view_ids = List[DB.ElementId]()
                    view_ids.Add(s.Id)
                    ok = doc.Export(default_folder, s.Name, view_ids, opt)
                    if ok:
                        success_count += 1
                        if existed:
                            overwrite_count += 1
                        else:
                            new_count += 1
                    else:
                        fail_count += 1
                        if len(fail_names) < 8:
                            fail_names.append(s.Name + "（导出返回 False）")
                except Exception as ex:
                    fail_count += 1
                    if len(fail_names) < 8:
                        fail_names.append(s.Name + ": " + str(ex))

        # 6. 完成提示并自动打开文件夹
        msg = "批量导出任务执行完毕！（本次为覆盖式导出，磁盘上的 DWG 均为当前模型状态）\n\n"
        msg += "• 目标文件夹: {}\n".format(default_folder)
        msg += "• 本次成功导出: {} 张  (其中新增 {} 张 / 覆盖旧文件 {} 张)\n".format(
            success_count, new_count, overwrite_count)
        if fail_count > 0:
            msg += "• 失败: {} 张\n".format(fail_count)
            for fn in fail_names:
                msg += "    - {}\n".format(fn)
        if skip_count > 0:
            msg += "• 跳过: {} 张\n".format(skip_count)

        forms.alert(msg, title='导出完成')

        try:
            System.Diagnostics.Process.Start("explorer.exe", default_folder)
        except:
            pass

    except Exception as e:
        import traceback
        forms.alert("发生严重错误:\n" + str(e) + "\n\n" + traceback.format_exc(), title="报错追踪")


if __name__ == '__main__':
    main()
