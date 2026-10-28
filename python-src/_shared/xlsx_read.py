# ---- xlsx_read（天河落共用片段，打包时内联进脚本）------------------------------------
# 读 .xlsx（不需要装 Excel）：xlsx_read(path) -> [(工作表名, [[单元格文字, ...], ...]), ...]
# 合并单元格：被合并的空格子会填上左上角的值（比如一个规格合并了好几行）。公式取缓存的结果。
import zipfile as _xr_zip
import re as _xr_re
import xml.etree.ElementTree as _xr_et

_XR_NS = u'{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_XR_RNS = u'{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def _xr_col(ref):
    m = _xr_re.match(u'([A-Z]+)(\\d+)', ref)
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1, int(m.group(2)) - 1


def _xr_text(el):
    return u''.join(t.text or u'' for t in el.iter(_XR_NS + u't'))


def xlsx_read(path):
    z = _xr_zip.ZipFile(path)
    try:
        shared = []
        if u'xl/sharedStrings.xml' in z.namelist():
            root = _xr_et.fromstring(z.read(u'xl/sharedStrings.xml'))
            shared = [_xr_text(si) for si in root.iter(_XR_NS + u'si')]
        wb = _xr_et.fromstring(z.read(u'xl/workbook.xml'))
        rels = _xr_et.fromstring(z.read(u'xl/_rels/workbook.xml.rels'))
        target = {}
        for r in rels:
            target[r.get(u'Id')] = r.get(u'Target')
        out = []
        for sh in wb.iter(_XR_NS + u'sheet'):
            rid = sh.get(_XR_RNS + u'id')
            t = target.get(rid, u'')
            name = (u'xl/' + t.lstrip(u'/')) if not t.startswith(u'/xl/') else t.lstrip(u'/')
            if name.startswith(u'xl/xl/'):
                name = name[3:]
            root = _xr_et.fromstring(z.read(name))
            grid = {}
            maxc = 0
            maxr = 0
            for c in root.iter(_XR_NS + u'c'):
                ref = c.get(u'r')
                if not ref:
                    continue
                ci, ri = _xr_col(ref)
                tp = c.get(u't')
                v = c.find(_XR_NS + u'v')
                if tp == u's' and v is not None:
                    txt = shared[int(v.text)]
                elif tp == u'inlineStr':
                    txt = _xr_text(c)
                elif v is not None and v.text is not None:
                    txt = v.text
                else:
                    txt = u''
                grid[(ri, ci)] = txt
                maxc = max(maxc, ci)
                maxr = max(maxr, ri)
            for mc in root.iter(_XR_NS + u'mergeCell'):
                a, b = (mc.get(u'ref') + u':' + mc.get(u'ref')).split(u':')[:2]
                c0, r0 = _xr_col(a)
                c1, r1 = _xr_col(b)
                v0 = grid.get((r0, c0), u'')
                for rr in range(r0, r1 + 1):
                    for cc in range(c0, c1 + 1):
                        if (rr, cc) != (r0, c0) and not grid.get((rr, cc)):
                            grid[(rr, cc)] = v0
                            maxc = max(maxc, cc)
                            maxr = max(maxr, rr)
            rows = [[grid.get((r, c), u'') for c in range(maxc + 1)] for r in range(maxr + 1)]
            out.append((sh.get(u'name'), rows))
        return out
    finally:
        z.close()
# ---- xlsx_read 结束 ------------------------------------------------------------------
