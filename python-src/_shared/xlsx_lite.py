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
