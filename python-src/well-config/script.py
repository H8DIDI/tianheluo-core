# -*- coding: utf-8 -*-
__title__ = '井大样项目配置'
__doc__ = ('井大样系列插件（一键井展开图、一键井大样标注、图纸归类与图框编号、导出井大样 DWG 等）共用的项目配置窗口：'
           '井大样图纸名前缀、图号前缀、井号系列（前缀 → 系统名 → 系统代码）、按井号前缀的图名模板与图纸集合、'
           'DWG 导出设置名与图框 logo 位置。换项目时先在这里改好，再用其他井大样插件。'
           '配置保存在 文档\KaifaTools\井大样配置.json，可随时恢复默认。不改模型。')

from pyrevit import revit, DB, UI, forms

# @include _shared/form_fit.py
# @include _shared/well_config.py
# @include _shared/settings_ui.py
# @include _shared/well_config_ui.py


def main():
    if wcfg_edit():
        forms.alert(u'井大样项目配置已保存：\n%s\n\n下次点井大样系列插件时按新配置运行。' % WCFG_FILE, title=u'井大样项目配置')


if __name__ == '__main__':
    main()
