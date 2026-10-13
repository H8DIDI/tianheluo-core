import clr
import os
import System

clr.AddReference('RevitAPI')
from Autodesk.Revit.DB import *

doc = __revit__.ActiveUIDocument.Document
uidoc = __revit__.ActiveUIDocument

def export_nwc():
    try:
        is_avail = OptionalFunctionalityUtils.IsNavisworksExporterAvailable()
        if not is_avail:
            from pyrevit import forms
            forms.alert("Navisworks 导出底层引擎未能与当前 Revit 建立连接。\n请确保在 `附加模块` -> `外部工具` 中能看到 Navisworks。", title="连接失败")
            return
            
        # 导出到桌面
        desktop = System.Environment.GetFolderPath(System.Environment.SpecialFolder.Desktop)
        # 获取当前文档名称 (去掉 .rvt 后缀)
        filename = doc.Title
        if filename.lower().endswith(".rvt"):
            filename = filename[:-4]
        if not filename:
            filename = "Revit_Exported_Model"
            
        options = NavisworksExportOptions()
        # 导出当前视图 (强烈建议在一个干净的 3D 视图中点击此按钮)
        options.ExportScope = NavisworksExportScope.View
        options.ViewId = doc.ActiveView.Id
        options.ConvertElementProperties = True
        
        # 自动屏蔽 CAD 底图，这是导致导出崩溃和卡死的最常见原因
        options.ConvertLinkedCADFormats = False 
        
        doc.Export(desktop, filename, options)
        
        from pyrevit import forms
        forms.alert("✅ 成功！\n\nNWC 文件已全自动导出至您的电脑【桌面】。\n文件名: {}.nwc".format(filename), title="导出成功")
        
    except Exception as e:
        from pyrevit import forms
        import traceback
        forms.alert("导出发生崩溃，详细排查信息:\n\n" + traceback.format_exc(), title="致命错误")

export_nwc()
