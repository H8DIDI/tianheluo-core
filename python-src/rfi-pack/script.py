# -*- coding: utf-8 -*-
__title__ = 'RFI 取证包'
__doc__ = ('写 RFI（设计问询）要附井号、位置、尺寸、截图。选中有疑问的井、排管或任何构件（没选就框选），一键生成一个「RFI取证」文件夹：\n'
           '① 构件清单.xlsx：井号/标记、族与类型、注释、井内深度或长度、内部坐标和共享坐标（E/N/RL，米，排管给起点终点）；\n'
           '② 当前视图截图.png；③ RFI正文素材.txt：每个构件一行「位置说明」，可以直接贴进 RFI 的 Location Detail。只读，不改模型。')

import time
import math
from pyrevit import revit, DB, UI, forms

# @include _shared/xlsx_lite.py
import io
import os
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
import System.Windows.Forms as WF

doc = revit.doc
uidoc = revit.uidoc
TITLE = u'RFI 取证包'
FT = 304.8
M = FT / 1000.0


def _shared_tf():
    try:
        return doc.ActiveProjectLocation.GetTotalTransform()
    except Exception:
        return DB.Transform.Identity


def _pts(e):
    """[(说明, 点)]：点状构件一个点；线状构件起点、终点；其他取包围盒中心。"""
    loc = e.Location
    if isinstance(loc, DB.LocationPoint):
        return [(u'位置', loc.Point)]
    if isinstance(loc, DB.LocationCurve):
        c = loc.Curve
        return [(u'起点', c.GetEndPoint(0)), (u'终点', c.GetEndPoint(1))]
    bb = e.get_BoundingBox(None)
    if bb is not None:
        return [(u'中心', (bb.Min + bb.Max) / 2.0)]
    return []


def _par(e, name):
    p = e.LookupParameter(name)
    if p is None or not p.HasValue:
        return None
    if p.StorageType == DB.StorageType.Double:
        return p.AsDouble() * FT
    return p.AsString() if p.StorageType == DB.StorageType.String else p.AsInteger()


def _ftname(e):
    try:
        s = e.Symbol
        return s.FamilyName, s.Name
    except Exception:
        try:
            t = doc.GetElement(e.GetTypeId())
            return (t.FamilyName if t is not None else u''), (t.Name if t is not None else e.Name)
        except Exception:
            return u'', u''


def _mark(e):
    p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
    return (p.AsString() or u'') if (p is not None and p.HasValue) else u''


def _comment(e):
    p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    return (p.AsString() or u'') if (p is not None and p.HasValue) else u''


def build_rows(elems):
    tf = _shared_tf()
    rows = [[u'构件ID', u'类别', u'族', u'类型', u'标记', u'注释', u'井内深度/长度(mm)', u'点', u'内部X(m)', u'内部Y(m)', u'内部Z(m)',
             u'E 东(m)', u'N 北(m)', u'RL 标高(m)']]
    lines = []
    for e in elems:
        f, t = _ftname(e)
        cat = e.Category.Name if e.Category is not None else u''
        mk = _mark(e)
        d = _par(e, u'井内深度')
        if d is None and isinstance(e.Location, DB.LocationCurve):
            d = e.Location.Curve.Length * FT
        for lab, p in _pts(e):
            q = tf.OfPoint(p)
            rows.append([e.Id.Value, cat, f, t, mk, _comment(e), round(d) if d else u'', lab,
                         round(p.X * M, 3), round(p.Y * M, 3), round(p.Z * M, 3),
                         round(q.X * M, 3), round(q.Y * M, 3), round(q.Z * M, 3)])
        pts = _pts(e)
        name = (u'%s %s' % (cat, mk)).strip() if mk else (u'%s %s' % (f, t)).strip()
        if len(pts) == 1:
            q = tf.OfPoint(pts[0][1])
            lines.append(u'%s（%s / %s）：E %.3f, N %.3f, RL %.3f m' % (name, f, t, q.X * M, q.Y * M, q.Z * M))
        elif pts:
            a, b = tf.OfPoint(pts[0][1]), tf.OfPoint(pts[1][1])
            lines.append(u'%s（%s / %s，长 %.1f m）：起点 E %.3f, N %.3f, RL %.3f → 终点 E %.3f, N %.3f, RL %.3f m' %
                         (name, f, t, (d or 0) / 1000.0, a.X * M, a.Y * M, a.Z * M, b.X * M, b.Y * M, b.Z * M))
    return rows, lines


def main():
    try:
        ids = list(uidoc.Selection.GetElementIds())
        if not ids:
            try:
                ids = [e.Id for e in uidoc.Selection.PickElementsByRectangle(u'框选有疑问的井、排管等构件（框完按 Finish）')]
            except Exception:
                return
        elems = [doc.GetElement(i) for i in ids]
        elems = [e for e in elems if e is not None and e.Category is not None and not isinstance(e, DB.ElementType)]
        if not elems:
            forms.alert(u'没有选到构件。', title=TITLE)
            return
        if len(elems) > 300 and not forms.alert(u'选了 %d 个构件，清单会很长。继续吗？' % len(elems), title=TITLE, ok=False, yes=True, no=True):
            return
        dlg = WF.FolderBrowserDialog()
        dlg.Description = u'选择放「RFI取证」文件夹的位置'
        dlg.SelectedPath = System.Environment.GetFolderPath(System.Environment.SpecialFolder.Desktop)
        if dlg.ShowDialog() != WF.DialogResult.OK:
            return
        out = os.path.join(dlg.SelectedPath, u'RFI取证_%s' % time.strftime(u'%Y%m%d_%H%M'))
        os.makedirs(out)
        rows, lines = build_rows(elems)
        xlsx_write(os.path.join(out, u'构件清单.xlsx'), [(u'构件清单', rows, {'widths': [12, 14, 26, 34, 12, 24, 14, 6, 12, 12, 12, 14, 14, 12], 'header_rows': 1})])
        shot = u'（没有截图）'
        try:
            v = doc.ActiveView
            o = DB.ImageExportOptions()
            o.ExportRange = DB.ExportRange.CurrentView
            o.FilePath = os.path.join(out, u'当前视图截图')
            o.HLRandWFViewsFileType = DB.ImageFileType.PNG
            o.ShadowViewsFileType = DB.ImageFileType.PNG
            o.ImageResolution = DB.ImageResolution.DPI_150
            o.PixelSize = 2400
            o.ZoomType = DB.ZoomFitType.FitToPage
            doc.ExportImage(o)
            shot = u'当前视图截图.png（视图「%s」）' % v.Name
        except Exception as ex:
            shot = u'截图失败：%s' % ex
        txt = (u'RFI 正文素材（%s，项目 %s）\r\n坐标系：Revit 共享坐标（E 东、N 北、RL 标高，单位 米）\r\n\r\n' % (time.strftime(u'%Y-%m-%d %H:%M'), doc.Title)
               + u'\r\n'.join(lines) + u'\r\n')
        with io.open(os.path.join(out, u'RFI正文素材.txt'), 'w', encoding='utf-8-sig') as f:
            f.write(txt)
        forms.alert(u'已生成：%s\n\n• 构件清单.xlsx（%d 个构件）\n• %s\n• RFI正文素材.txt（位置说明，可贴进 Location Detail）' % (out, len(elems), shot), title=TITLE)
        try:
            System.Diagnostics.Process.Start(u'explorer.exe', out)
        except Exception:
            pass
    except Exception:
        forms.alert(u'出错了：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
