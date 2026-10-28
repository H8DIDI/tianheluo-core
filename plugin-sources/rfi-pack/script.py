# -*- coding: utf-8 -*-
__title__ = 'RFI 取证包'
__doc__ = ('写 RFI（设计问询）要附井号、位置、尺寸、截图。选中有疑问的井、排管或任何构件（没选就框选），一键生成一个「RFI取证」文件夹：\n'
           '① 构件清单.xlsx：井号/标记、族与类型、注释、井内深度或长度、内部坐标和共享坐标（E/N/RL，米，排管给起点终点）；\n'
           '② 当前视图截图.png；③ RFI正文素材.txt：每个构件一行「位置说明」，可以直接贴进 RFI 的 Location Detail。只读，不改模型。')

import time
import math
from pyrevit import revit, DB, UI, forms

# ---- xlsx_lite（天河落共用片段，打包时内联进脚本）--------------------------------
# 不依赖 Excel，用标准库 zipfile 直接写 .xlsx。IronPython 3.4 可用。
# 用法：xlsx_write(path, [(工作表名, 行列表, {"widths": [..], "header_rows": n, "title_rows": n}), ...])
#   行列表里的数字写成数字单元格，其余写成文字；header_rows 行加粗带底色，title_rows 行加粗（放在表头前）。
import zipfile as _xl_zip


def _xl_esc(s):
    s = u'%s' % s
    return (s.replace(u'&', u'&amp;').replace(u'<', u'&lt;').replace(u'>', u'&gt;')
             .replace(u'"', u'&quot;'))


def _xl_col(i):
    s = u''
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = u'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[r] + s
    return s


def _xl_is_num(v):
    if isinstance(v, bool):
        return False
    try:
        return isinstance(v, (int, float)) or type(v).__name__ in ('long', 'Double', 'Int32', 'Int64')
    except Exception:
        return False


def _xl_sheet_xml(rows, opts):
    widths = opts.get('widths') or []
    title_rows = int(opts.get('title_rows', 0))
    header_rows = int(opts.get('header_rows', 1))
    x = [u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
         u'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if title_rows + header_rows > 0:
        x.append(u'<sheetViews><sheetView workbookViewId="0"><pane ySplit="%d" topLeftCell="A%d" '
                 u'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                 % (title_rows + header_rows, title_rows + header_rows + 1))
    if widths:
        x.append(u'<cols>')
        for i, w in enumerate(widths):
            x.append(u'<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, w))
        x.append(u'</cols>')
    x.append(u'<sheetData>')
    for r, row in enumerate(rows):
        style = 0
        if r < title_rows:
            style = 1
        elif r < title_rows + header_rows:
            style = 2
        x.append(u'<row r="%d">' % (r + 1))
        for c, v in enumerate(row):
            if v is None or v == u'':
                continue
            ref = u'%s%d' % (_xl_col(c), r + 1)
            st = u' s="%d"' % style if style else u''
            if _xl_is_num(v) and style != 2:
                x.append(u'<c r="%s"%s><v>%s</v></c>' % (ref, st, repr(float(v)) if isinstance(v, float) else v))
            else:
                x.append(u'<c r="%s" t="inlineStr"%s><is><t xml:space="preserve">%s</t></is></c>'
                         % (ref, st, _xl_esc(v)))
        x.append(u'</row>')
    x.append(u'</sheetData></worksheet>')
    return u''.join(x)


def xlsx_write(path, sheets):
    """sheets: [(name, rows, opts)]。返回 path。"""
    names = []
    for i, (name, _rows, _o) in enumerate(sheets):
        n = (u'%s' % name)
        for ch in u'[]:*?/\\':
            n = n.replace(ch, u'_')
        n = n[:31] or (u'Sheet%d' % (i + 1))
        names.append(n)
    ct = (u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          u'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          u'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          u'<Default Extension="xml" ContentType="application/xml"/>'
          u'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          u'<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>')
    for i in range(len(sheets)):
        ct += (u'<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % (i + 1))
    ct += u'</Types>'
    rels = (u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            u'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            u'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            u'</Relationships>')
    wb = (u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
          u'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          u'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>')
    wbrels = (u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              u'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">')
    for i, n in enumerate(names):
        wb += u'<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (_xl_esc(n), i + 1, i + 1)
        wbrels += (u'<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i + 1, i + 1))
    wb += u'</sheets></workbook>'
    wbrels += (u'<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>' % (len(names) + 1))
    wbrels += u'</Relationships>'
    styles = (u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
              u'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              u'<fonts count="2"><font><sz val="11"/><name val="等线"/></font>'
              u'<font><b/><sz val="11"/><name val="等线"/></font></fonts>'
              u'<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              u'<fill><patternFill patternType="solid"><fgColor rgb="FFDDEBF7"/><bgColor indexed="64"/></patternFill></fill></fills>'
              u'<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              u'<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              u'<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              u'<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
              u'<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1">'
              u'<alignment wrapText="1" vertical="center"/></xf></cellXfs>'
              u'<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
              u'</styleSheet>')
    try:
        mode = _xl_zip.ZIP_DEFLATED
        z = _xl_zip.ZipFile(path, 'w', mode)
    except Exception:
        z = _xl_zip.ZipFile(path, 'w', _xl_zip.ZIP_STORED)
    try:
        z.writestr('[Content_Types].xml', ct.encode('utf-8'))
        z.writestr('_rels/.rels', rels.encode('utf-8'))
        z.writestr('xl/workbook.xml', wb.encode('utf-8'))
        z.writestr('xl/_rels/workbook.xml.rels', wbrels.encode('utf-8'))
        z.writestr('xl/styles.xml', styles.encode('utf-8'))
        for i, (_n, rows, opts) in enumerate(sheets):
            z.writestr('xl/worksheets/sheet%d.xml' % (i + 1), _xl_sheet_xml(rows, opts or {}).encode('utf-8'))
    finally:
        z.close()
    return path
# ---- xlsx_lite 结束 ------------------------------------------------------------

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
