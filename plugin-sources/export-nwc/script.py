# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '一键导出NWC'
__author__ = 'didi'
__doc__ = ('把当前三维视图，或一次勾选多个三维视图，导出成 Navisworks 的 NWC（每个视图一个文件）。\n'
           '可选导出文件夹（会记住）、共享坐标/内部坐标、是否导出链接的 Revit 模型；默认不转换链接的 CAD（最常见的卡死原因）。\n'
           '需要电脑上装有 Navisworks 导出插件（Navisworks NWC Export Utility）。')

import io
import os
import re
import json
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
from Autodesk.Revit import DB
from pyrevit import revit, forms
import System.Windows.Forms as WF

doc = revit.doc
uidoc = revit.uidoc
TITLE = '一键导出NWC'
SETTINGS = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments),
                        'KaifaTools', '导出NWC设置.json')


def load():
    s = {'folder': System.Environment.GetFolderPath(System.Environment.SpecialFolder.Desktop),
         'shared': True, 'links': False, 'cad': False}
    try:
        with io.open(SETTINGS, 'r', encoding='utf-8') as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


def save(s):
    try:
        d = os.path.dirname(SETTINGS)
        if not os.path.isdir(d):
            os.makedirs(d)
        with io.open(SETTINGS, 'w', encoding='utf-8') as f:
            f.write(json.dumps(s, ensure_ascii=False, indent=2))
    except Exception:
        pass


def safe(name):
    return re.sub(r'[\\/:*?"<>|{}]', '_', name or 'model').strip() or 'model'


class _V(object):
    def __init__(self, v):
        self.v = v

    def __str__(self):
        return self.v.Name


def main():
    if not DB.OptionalFunctionalityUtils.IsNavisworksExporterAvailable():
        forms.alert('没有找到 Navisworks 导出插件。\n请安装与 Revit 2026 对应的「Navisworks NWC Export Utility」，'
                    '装好后在「附加模块 → 外部工具」里能看到 Navisworks。', title=TITLE)
        return
    s = load()
    views = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.View3D) if not v.IsTemplate]
    views.sort(key=lambda v: v.Name)
    act = doc.ActiveView
    opts = []
    if isinstance(act, DB.View3D) and not act.IsTemplate:
        opts.append('只导出当前三维视图「%s」' % act.Name)
    opts += ['勾选多个三维视图（每个视图一个 NWC）', '整个模型（不按视图过滤）']
    pick = forms.CommandSwitchWindow.show(opts, message='导出范围：')
    if not pick:
        return
    if pick.startswith('只导出当前'):
        targets = [act]
    elif pick.startswith('勾选'):
        if not views:
            forms.alert('项目里没有三维视图。', title=TITLE)
            return
        got = forms.SelectFromList.show([_V(v) for v in views], title='勾选要导出的三维视图', multiselect=True, button_name='下一步')
        if not got:
            return
        targets = [getattr(x, 'v', x) for x in (got if isinstance(got, list) else [got])]
    else:
        targets = [None]
    coord = forms.CommandSwitchWindow.show(
        ['共享坐标（和其它专业、Navisworks 里对齐用，推荐）', '项目内部坐标'],
        message='坐标：（上次：%s）' % ('共享' if s.get('shared', True) else '内部'))
    if not coord:
        return
    s['shared'] = coord.startswith('共享')
    links = forms.CommandSwitchWindow.show(['不导出链接的 Revit 模型', '连同链接的 Revit 模型一起导出'],
                                           message='链接模型：')
    if not links:
        return
    s['links'] = links.startswith('连同')
    dlg = WF.FolderBrowserDialog()
    dlg.Description = '选择 NWC 保存文件夹'
    dlg.SelectedPath = s.get('folder') or ''
    if dlg.ShowDialog() != WF.DialogResult.OK:
        return
    s['folder'] = dlg.SelectedPath
    save(s)

    base = safe(doc.Title[:-4] if doc.Title.lower().endswith('.rvt') else doc.Title)
    done, fail = [], []
    for v in targets:
        o = DB.NavisworksExportOptions()
        o.ExportScope = DB.NavisworksExportScope.View if v is not None else DB.NavisworksExportScope.Model
        if v is not None:
            o.ViewId = v.Id
        o.Coordinates = DB.NavisworksCoordinates.Shared if s['shared'] else DB.NavisworksCoordinates.Internal
        o.ExportLinks = bool(s['links'])
        o.ConvertElementProperties = True
        o.ConvertLinkedCADFormats = bool(s.get('cad', False))
        name = base if v is None else '%s_%s' % (base, safe(v.Name))
        try:
            doc.Export(s['folder'], name, o)
            done.append(name + '.nwc')
        except Exception as ex:
            fail.append('%s：%s' % (name, ex))
    msg = '已导出 %d 个 NWC 到：\n%s\n\n%s' % (len(done), s['folder'], '\n'.join(done[:15]))
    if fail:
        msg += '\n\n失败 %d 个：\n%s' % (len(fail), '\n'.join(fail[:8]))
    forms.alert(msg, title=TITLE)
    if done:
        try:
            System.Diagnostics.Process.Start('explorer.exe', s['folder'])
        except Exception:
            pass


if __name__ == '__main__':
    try:
        main()
    except Exception:
        forms.alert('导出出错：\n' + traceback.format_exc(), title=TITLE)
