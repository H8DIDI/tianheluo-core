# -*- coding: utf-8 -*-
__title__ = '井表核对'
__doc__ = ('把设计方/厂家给的井清单（Excel，如「预制井材料计划表」：每行一口井，有井号和「1770(L)x1120(W)x3500(D)」这样的规格）和模型逐口核对：\n'
           '清单里有、模型里没有的井；模型里有、清单里没有的井（只看清单涉及的系列）；井的长宽和类型名对不上；深度差得多。\n'
           '清单格式不限：只要同一行里能找到井号（如 E-008）和规格（长×宽，可带深度）就行，多个工作表都会读。只读，不改模型；结果可导出 Excel。')

import re
import time
from pyrevit import revit, DB, UI, forms

# ---- well_config（天河落共用片段，打包时内联进脚本）-----------------------------
# 井大样系列插件（一键井展开图 / 一键井大样标注 / 图纸归类与图框编号 / 导出井大样DWG）共用的项目配置。
# 配置文件：文档\KaifaTools\井大样配置.json。第一次运行时自动写出一份默认配置（与原来写死的规则完全一样），
# 换项目时只改这个文件：图纸名前缀、图号前缀、井编号系列（前缀 → 系统名 → 系统代码）、图框 logo 位置。
import io as _wc_io
import os as _wc_os
import json as _wc_json
import re as _wc_re

WCFG_FILE = _wc_os.path.join(
    __import__('System').Environment.GetFolderPath(__import__('System').Environment.SpecialFolder.MyDocuments),
    u"KaifaTools", u"井大样配置.json")

WCFG_DEFAULTS = {
    u"说明": (u"井大样系列插件的项目配置。改完保存，下次点插件生效；删掉这个文件会恢复默认。"
            u"series 里每一项是一个井编号系列：prefix=井号前缀（井号形如 AA-01、T-068、室内-A-01，前缀后面可以有一个 -，再跟数字），"
            u"system=系统名（写进图纸的「图纸系统」参数、用来归类），code=系统代码（图号模板里的 {系统码}），label=菜单里显示的名字。"
            u"顺序就是菜单和排序的顺序；匹配时长的前缀优先（AA 先于 A）。"),
    u"sheet_name_prefix": u"井大样_",
    u"sheet_number_prefix": u"AG-",
    u"indoor_prefix": u"室内-",
    u"system_param": u"图纸系统",
    u"series": [
        {u"prefix": u"室内-A", u"system": u"室内强电井", u"code": u"NQ", u"label": u"室内-A- 强电井"},
        {u"prefix": u"室内-T", u"system": u"室内通信井", u"code": u"NT", u"label": u"室内-T- 通信井"},
        {u"prefix": u"室内-E", u"system": u"室内弱电井", u"code": u"NR", u"label": u"室内-E- 弱电井"},
        {u"prefix": u"室内-L", u"system": u"室内低压拉线井", u"code": u"ND", u"label": u"室内-L- 低压拉线井"},
        {u"prefix": u"AA", u"system": u"强电井", u"code": u"QD", u"label": u"AA- 强电特型井"},
        {u"prefix": u"A", u"system": u"强电井", u"code": u"QD", u"label": u"A- 强电井"},
        {u"prefix": u"BB", u"system": u"弱电井", u"code": u"RD", u"label": u"BB- 弱电特型井"},
        {u"prefix": u"B", u"system": u"弱电井", u"code": u"RD", u"label": u"B- 弱电井"},
        {u"prefix": u"T", u"system": u"通信井", u"code": u"TX", u"label": u"T- 通信井"},
        {u"prefix": u"L", u"system": u"低压拉线井", u"code": u"DY", u"label": u"L- 低压拉线井"},
        {u"prefix": u"E", u"system": u"预制井", u"code": u"YZ", u"label": u"E- 预制井"},
        {u"prefix": u"Y", u"system": u"雨水井", u"code": u"YS", u"label": u"Y- 雨水井"},
    ],
    u"other": {u"system": u"其他", u"code": u"QT"},
    # 图名模板（按井号前缀，长前缀优先）：{井号} 换成井号，如 T-001。没有模板的井沿用「井大样_」前缀。
    # 图纸集合（按井号前缀）：新图放进对应的图纸集合，便于按集合导出 PDF。名字须与项目里的图纸集合一致。
    u"sheet_collection_by_prefix": {
        u"AA": u"275KV井大样", u"BB": u"275KV井大样", u"A": u"11KV井大样", u"B": u"11KV井大样",
        u"E": u"ELV井大样", u"L": u"LV井大样", u"T": u"TELCO井大样",
    },
    u"sheet_name_templates": {
        u"AA": u"BTP-IF1 275Kv-{井号} Detail Drawing",
        u"BB": u"BTP-IF1 275Kv-{井号} Detail Drawing",
        u"A": u"BTP-IF1 11Kv-{井号} Detail Drawing",
        u"B": u"BTP-IF1 11Kv-{井号} Detail Drawing",
        u"E": u"BTP-IF1 ELV-{井号} Detail Drawing",
        u"L": u"BTP-IF1 Lv-{井号} Detail Drawing",
        u"T": u"BTP-IF1 Telco-{井号} Detail Drawing",
    },
    u"logo": {u"enabled": True, u"file": u"frame_logo.jpg", u"x0": 736.54, u"y0": 141.10, u"w": 91.49,
              u"说明": u"导出 DWG 后补插的图框 logo：x0/y0 = logo 左下角在 Layout1 里的坐标，w = 宽度（按 A1 横向图框标定）。换图框要改；enabled=false 不补 logo。"},
}

_WCFG_CACHE = [None]
# 本次运行之前配置文件还不存在（第一次在这台电脑上用井大样插件）。wcfg_load 会写出默认文件，所以要在调用它之前记下来。
WCFG_FIRST_RUN = not _wc_os.path.exists(WCFG_FILE)


def _wcfg_merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _wcfg_merge(out[k], v)
        else:
            out[k] = v
    return out


def wcfg_load(write_default=True):
    """读项目配置（缺的项用默认值补齐）。文件不存在时写出默认配置，方便用户找到并修改。"""
    if _WCFG_CACHE[0] is not None:
        return _WCFG_CACHE[0]
    cfg = _wcfg_merge(WCFG_DEFAULTS, {})
    try:
        if _wc_os.path.exists(WCFG_FILE):
            with _wc_io.open(WCFG_FILE, 'r', encoding='utf-8-sig') as f:
                cfg = _wcfg_merge(cfg, _wc_json.load(f))
        elif write_default:
            d = _wc_os.path.dirname(WCFG_FILE)
            if not _wc_os.path.isdir(d):
                _wc_os.makedirs(d)
            with _wc_io.open(WCFG_FILE, 'w', encoding='utf-8') as f:
                f.write(_wc_json.dumps(WCFG_DEFAULTS, ensure_ascii=False, indent=2))
    except Exception:
        cfg = _wcfg_merge(WCFG_DEFAULTS, {})
    # series 至少要有 prefix
    good = []
    for s in cfg.get(u"series") or []:
        try:
            p = (s.get(u"prefix") or u"").strip()
        except Exception:
            continue
        if not p:
            continue
        s = dict(s)
        s[u"prefix"] = p
        s.setdefault(u"system", p)
        s.setdefault(u"code", p)
        s.setdefault(u"label", p + u"-")
        good.append(s)
    cfg[u"series"] = good or list(WCFG_DEFAULTS[u"series"])
    _WCFG_CACHE[0] = cfg
    return cfg


def wcfg_series_of(mark, cfg=None, strict=False):
    """井号属于哪个系列：前缀（长的优先）后面可以有一个 -，必须紧跟数字。认不出返回 None。
    strict=True 时前缀后面必须是「-数字」（井的标记用这个：雨水井的 A1、BB24 这类不带 - 的不算强电/弱电井）。"""
    cfg = cfg or wcfg_load()
    m = (mark or u"").strip()
    for s in sorted(cfg[u"series"], key=lambda x: -len(x[u"prefix"])):
        p = s[u"prefix"]
        if m.startswith(p):
            rest = m[len(p):]
            if rest[:1] == u"-":
                rest = rest[1:]
            elif strict:
                continue
            if rest[:1].isdigit():
                return s
    return None


def wcfg_series_index(s, cfg=None):
    cfg = cfg or wcfg_load()
    for i, x in enumerate(cfg[u"series"]):
        if x is s:
            return i
    return len(cfg[u"series"])


def wcfg_natural_key(text):
    out = []
    for tok in _wc_re.split(r'(\d+)', text or u''):
        out.append((0, int(tok), u'') if tok.isdigit() else (1, 0, tok.lower()))
    return out


def wcfg_well_sort_key(mark, cfg=None, strict=True):
    """先按系列在配置里的顺序，同系列按号（自然排序：A-2 在 A-10 前面）。"""
    s = wcfg_series_of(mark, cfg, strict)
    return (wcfg_series_index(s, cfg) if s is not None else 9999, wcfg_natural_key(mark))


def wcfg_sheet_name(mark, cfg=None):
    """井号 → 图名（按配置里的图名模板）。没有对应模板时返回 None，调用方沿用「井大样_」前缀。
    模板前缀按长度从长到短匹配，且前缀后面必须紧跟 "-"（AA-04 不会误判成 A 系列）。"""
    cfg = cfg or wcfg_load()
    tpls = cfg.get(u"sheet_name_templates") or {}
    m = (mark or u"").strip()
    for key in sorted(tpls.keys(), key=lambda k: -len(k)):
        if m.startswith(key + u"-"):
            return tpls[key].replace(u"{井号}", m)
    return None
# ---- well_config 结束 ----------------------------------------------------------


def wcfg_collection_name(mark, cfg=None):
    """井号 → 图纸集合名（按配置 sheet_collection_by_prefix，长前缀优先，前缀后紧跟 -）。没有对应返回 None。"""
    cfg = cfg or wcfg_load()
    maps = cfg.get(u"sheet_collection_by_prefix") or {}
    m = (mark or u"").strip()
    for key in sorted(maps.keys(), key=lambda k: -len(k)):
        if m.startswith(key + u"-"):
            return maps[key]
    return None


# ---- 井位置缓存（井没动就不重新生成）：Documents\KaifaTools\井大样位置缓存.json ----
def wcfg_cache_path():
    return _wc_os.path.join(_wc_os.path.dirname(WCFG_FILE), u"井大样位置缓存.json")


def wcfg_cache_load():
    """读位置缓存：{井号: {bbox, sheet_id, name, number}}。文件不存在或坏了返回空。"""
    try:
        with _wc_io.open(wcfg_cache_path(), 'r', encoding='utf-8-sig') as f:
            d = _wc_json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def wcfg_cache_save(d):
    """先把内容转成文字，转成功才写文件（避免转换失败时把旧缓存清成空文件）。"""
    try:
        txt = _wc_json.dumps(d, ensure_ascii=False, indent=1)
    except Exception:
        return
    try:
        p = wcfg_cache_path()
        dd = _wc_os.path.dirname(p)
        if not _wc_os.path.isdir(dd):
            _wc_os.makedirs(dd)
        with _wc_io.open(p, 'w', encoding='utf-8') as f:
            f.write(txt)
    except Exception:
        pass

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

import io
import os
import json
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
import System.Windows.Forms as WF

doc = revit.doc
uidoc = revit.uidoc
WCFG = wcfg_load()
TITLE = u'井表核对'
FT = 304.8
TOL_SIZE = 20.0          # 长、宽差在这个范围内算一致（mm）
TOL_DEPTH = 250.0        # 深度差在这个范围内算一致（mm）：预制井深度常按 500 一档取整

_MARK = re.compile(u'([A-Za-z]{1,3}-\\d+(?:-\\d+)?)')
_SPEC = re.compile(u'(\\d{3,5})\\s*(?:\\(\\s*L\\s*\\))?\\s*[xX×*]\\s*(\\d{3,5})\\s*(?:\\(\\s*W\\s*\\))?(?:\\s*[xX×*]\\s*(\\d{3,5})\\s*(?:\\(\\s*D\\s*\\))?)?')
_SPEC_STRICT = re.compile(u'(\\d{3,5})\\s*\\(\\s*L\\s*\\)\\s*[xX×*]\\s*(\\d{3,5})\\s*\\(\\s*W\\s*\\)(?:\\s*[xX×*]\\s*(\\d{3,5})\\s*\\(\\s*D\\s*\\))?')


def norm(mk):
    """井号的数字部分去掉前导 0：清单里写 A-005，模型里是 A-5，算同一口井。"""
    return re.sub(u'-0+(\\d)', u'-\\1', mk or u'')


def parse_list(path):
    """从 Excel 里取出 {井号: (长, 宽, 深或None, 工作表, 行号)}。同一行里有井号和规格才算。"""
    out = {}
    dup = []
    for sname, rows in xlsx_read(path):
        for ri, row in enumerate(rows):
            mark = None
            spec = None
            for c in row:
                c = c or u''
                if mark is None and (u'编号' in c or _MARK.fullmatch(c.strip() or u'x')):
                    m = _MARK.search(c)
                    if m:
                        mark = m.group(1)
                if spec is None:
                    m = _SPEC_STRICT.search(c)
                    if m:
                        spec = (float(m.group(1)), float(m.group(2)), float(m.group(3)) if m.group(3) else None)
            if mark and spec:
                mark = norm(mark)
                if mark in out:
                    dup.append(mark)
                out[mark] = (spec[0], spec[1], spec[2], sname, ri + 1)
    return out, sorted(set(dup))


def model_wells():
    """模型里的井：{井号: 构件}，井 = 常规模型里「标记」属于井号系列的构件。"""
    out = {}
    for e in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType():
        try:
            p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
            mk = (p.AsString() or u'').strip() if p is not None else u''
        except Exception:
            mk = u''
        if mk and wcfg_series_of(mk, WCFG, True) is not None and norm(mk) not in out:
            out[norm(mk)] = e
    return out


def well_size(e):
    """类型名里的 长×宽（mm）。取不到返回 None。"""
    try:
        nm = e.Symbol.Name
    except Exception:
        return None
    m = _SPEC_STRICT.search(nm) or _SPEC.search(nm)
    return (float(m.group(1)), float(m.group(2))) if m else None


def well_depth(e):
    p = e.LookupParameter(u'井内深度')
    if p is not None and p.HasValue and p.StorageType == DB.StorageType.Double and p.AsDouble() > 0:
        return p.AsDouble() * FT
    b = e.LookupParameter(u'井底标高')
    o = e.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
    if b is not None and b.HasValue and o is not None and o.HasValue:
        return (o.AsDouble() - b.AsDouble()) * FT
    return None


def compare(table, wells):
    rows = []
    prefixes = set()
    for mk in table:
        sr = wcfg_series_of(mk, WCFG, True)
        if sr is not None:
            prefixes.add(sr[u'prefix'])
    for mk in sorted(table, key=lambda m: wcfg_well_sort_key(m, WCFG)):
        L, W, D, sn, rn = table[mk]
        e = wells.get(mk)
        where = u'%s 第 %d 行' % (sn, rn)
        if e is None:
            rows.append([u'清单有、模型没有', mk, u'', u'规格 %d×%d%s' % (L, W, u'×%d' % D if D else u''), where, None])
            continue
        sz = well_size(e)
        if sz is not None:
            same = (abs(sz[0] - L) <= TOL_SIZE and abs(sz[1] - W) <= TOL_SIZE) or (abs(sz[0] - W) <= TOL_SIZE and abs(sz[1] - L) <= TOL_SIZE)
            if not same:
                rows.append([u'长宽对不上', mk, e.Symbol.Name, u'清单 %d×%d，模型类型 %d×%d' % (L, W, sz[0], sz[1]), where, e.Id])
        else:
            rows.append([u'类型名里没有长宽', mk, e.Symbol.Name, u'清单 %d×%d，没法核对尺寸' % (L, W), where, e.Id])
        if D:
            md = well_depth(e)
            if md is None:
                rows.append([u'模型里没有深度', mk, e.Symbol.Name, u'清单深度 %d' % D, where, e.Id])
            elif abs(md - D) > TOL_DEPTH:
                rows.append([u'深度差得多', mk, e.Symbol.Name, u'清单 %d，模型 %.0f（差 %.0f mm）' % (D, md, md - D), where, e.Id])
    for mk, e in sorted(wells.items(), key=lambda kv: wcfg_well_sort_key(kv[0], WCFG)):
        sr = wcfg_series_of(mk, WCFG, True)
        if mk not in table and sr is not None and sr[u'prefix'] in prefixes:
            rows.append([u'模型有、清单没有', mk, e.Symbol.Name, u'', u'', e.Id])
    return rows


def main():
    try:
        dlg = WF.OpenFileDialog()
        dlg.Title = TITLE + u' · 选择井清单（Excel）'
        dlg.Filter = u'Excel 表格 (*.xlsx)|*.xlsx'
        if dlg.ShowDialog() != WF.DialogResult.OK:
            return
        table, dup = parse_list(dlg.FileName)
        if not table:
            forms.alert(u'这个 Excel 里没有找到「井号 + 规格」同在一行的数据。\n\n需要：某个格子里有井号（如 E-008 或「编号:E-008」），同一行另一个格子里有规格（如 JC9C-1770(L)x1120(W)x3500(D)）。', title=TITLE)
            return
        wells = model_wells()
        rows = compare(table, wells)
        head = u'清单：%d 口井（%s）\n模型：%d 口井\n' % (len(table), os.path.basename(dlg.FileName), len(wells))
        if dup:
            head += u'清单里井号重复：%s\n' % u'、'.join(dup[:10])
        cnt = {}
        for r in rows:
            cnt[r[0]] = cnt.get(r[0], 0) + 1
        if not rows:
            forms.alert(head + u'\n逐口核对：长宽、深度都对得上，清单和模型的井一一对应。', title=TITLE)
            return
        head += u'\n发现 %d 处不一致：\n' % len(rows) + u''.join(u'  %s：%d\n' % (k, v) for k, v in sorted(cnt.items(), key=lambda kv: -kv[1]))
        head += u'\n前几条：\n' + u'\n'.join(u'  %s %s：%s' % (r[0], r[1], r[3]) for r in rows[:8])
        head += u'\n\n导出完整清单到 Excel？'
        if forms.alert(head, title=TITLE, ok=False, yes=True, no=True):
            sd = WF.SaveFileDialog()
            sd.Title = TITLE
            sd.Filter = u'Excel 表格 (*.xlsx)|*.xlsx'
            sd.FileName = u'井表核对_%s.xlsx' % time.strftime(u'%Y%m%d_%H%M')
            if sd.ShowDialog() == WF.DialogResult.OK:
                data = [[u'问题', u'井号', u'模型类型', u'说明', u'清单位置', u'构件ID']] + \
                       [[r[0], r[1], r[2], r[3], r[4], r[5].Value if r[5] is not None else u''] for r in rows]
                xlsx_write(sd.FileName, [(u'核对结果', data, {'widths': [18, 12, 34, 56, 20, 12], 'header_rows': 1})])
                try:
                    System.Diagnostics.Process.Start(u'explorer.exe', u'/select,"%s"' % sd.FileName)
                except Exception:
                    pass
        ids = [r[5] for r in rows if r[5] is not None]
        if ids and forms.alert(u'要在模型里选中这 %d 口有出入的井吗？' % len(ids), title=TITLE, ok=False, yes=True, no=True):
            from System.Collections.Generic import List
            uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    except Exception:
        forms.alert(u'出错了：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
