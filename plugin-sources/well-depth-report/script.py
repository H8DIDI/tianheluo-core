# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '井深统计\n报表'
__author__ = 'didi'
__doc__ = ('统计全部井（或选中的井）的井深，导出中英双语 Excel：明细、按系列、按族、按类型（数量、最小、最大、平均、\n'
           '不同取值数、缺值数），另附超过设定深度（默认 4 m）的深井清单，可顺手在模型里选中这些深井（做埋深范围图用）。\n'
           '井深参数名按优先顺序依次找（实例参数优先，其次类型参数），参数名和深井阈值都能改。只读，不改模型。')

import io
import os
import json
import time
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
from Autodesk.Revit import DB
from System.Collections.Generic import List
from pyrevit import revit, forms
import System.Windows.Forms as WF

doc = revit.doc
uidoc = revit.uidoc
TITLE = '井深统计报表'
FT_M = 0.3048
DOC_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), 'KaifaTools')
SETTINGS = os.path.join(DOC_DIR, '井深统计设置.json')
DEFAULTS = {
    '说明': 'params：井深参数名，按顺序找第一个有值的；deep_m：深井阈值（米）；family_exclude：族名含这些字的不统计。',
    'params': ['井内深度', '井底深度', '井深', '总深度', '主体净深度', '深度', '井整体高度', '井底深'],
    'deep_m': 4.0,
    'family_exclude': ['井盖', '爬梯', 'mold_'],
}

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
    u"logo": {u"enabled": True, u"file": u"frame_logo.jpg", u"x0": 736.54, u"y0": 141.10, u"w": 91.49,
              u"说明": u"导出 DWG 后补插的图框 logo：x0/y0 = logo 左下角在 Layout1 里的坐标，w = 宽度（按 A1 横向图框标定）。换图框要改；enabled=false 不补 logo。"},
}

_WCFG_CACHE = [None]


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
# ---- well_config 结束 ----------------------------------------------------------

def load():
    s = dict(DEFAULTS)
    try:
        if os.path.exists(SETTINGS):
            with io.open(SETTINGS, 'r', encoding='utf-8-sig') as f:
                s.update(json.load(f))
        else:
            if not os.path.isdir(DOC_DIR):
                os.makedirs(DOC_DIR)
            with io.open(SETTINGS, 'w', encoding='utf-8') as f:
                f.write(json.dumps(DEFAULTS, ensure_ascii=False, indent=2))
    except Exception:
        pass
    return s


def _num(p):
    """参数值换成米（长度）或数字；取不到返回 None。"""
    if p is None or not p.HasValue:
        return None
    try:
        if p.StorageType == DB.StorageType.Double:
            v = p.AsDouble()
            try:
                if p.Definition.GetDataType() == DB.SpecTypeId.Length:
                    return v * FT_M
            except Exception:
                pass
            return v
        if p.StorageType == DB.StorageType.Integer:
            return float(p.AsInteger())
        if p.StorageType == DB.StorageType.String:
            t = (p.AsString() or '').strip().lower().replace('m', '')
            return float(t) if t else None
    except Exception:
        return None
    return None


def read_depths(e, names):
    t = doc.GetElement(e.GetTypeId())
    vals = {}
    for n in names:
        v = _num(e.LookupParameter(n))
        if v is None and t is not None:
            v = _num(t.LookupParameter(n))
        vals[n] = v
    return vals


def fam_type(e):
    try:
        s = e.Symbol
        return s.FamilyName, s.Name
    except Exception:
        return '', e.Name


def mark(e):
    p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
    return (p.AsString() or '').strip() if (p is not None and p.HasValue) else ''


def stats(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return [0, None, None, None, 0, len(vals)]
    return [len(v), round(min(v), 3), round(max(v), 3), round(sum(v) / len(v), 3),
            len(set(round(x, 3) for x in v)), len(vals) - len(v)]


def main():
    s = load()
    names = [n for n in s['params'] if n]
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    if sel:
        elems = [e for e in sel if isinstance(e, DB.FamilyInstance)]
        scope = '选中的 %d 个构件' % len(elems)
    else:
        elems = list(DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_GenericModel)
                     .WhereElementIsNotElementType().OfClass(DB.FamilyInstance))
        scope = '整个项目的常规模型'
    cfg = wcfg_load(write_default=False)
    rows = []
    for e in elems:
        fam, typ = fam_type(e)
        if any(k and k in fam for k in s['family_exclude']):
            continue
        vals = read_depths(e, names)
        used = next((n for n in names if vals[n] is not None), None)
        if used is None:
            continue
        mk = mark(e)
        sr = wcfg_series_of(mk, cfg, True)
        series = sr['label'] if sr else '（未识别系列）'
        rows.append({'e': e, 'fam': fam, 'typ': typ, 'mark': mk, 'series': series, 'vals': vals,
                     'used': used, 'depth': vals[used],
                     'group': e.GroupId != DB.ElementId.InvalidElementId})
    if not rows:
        forms.alert('%s里没有找到带井深参数的构件。\n\n找的参数（按顺序）：%s\n可在 %s 里改。' % (scope, '、'.join(names), SETTINGS), title=TITLE)
        return
    rows.sort(key=lambda r: wcfg_well_sort_key(r['mark'], cfg))
    deep = float(s.get('deep_m', 4.0))
    head_detail = ['ElementId 元素ID', '系列 Series', '族 Family', '类型 Type', '标记 Mark'] + \
                  ['%s (m)' % n for n in names] + ['采用井深 Adopted Depth (m)', '采用参数 Source Param', '在组内 In Group']
    detail = [[('井深统计 Well Depth Report — %s — %s — %s' % (doc.Title, scope, time.strftime('%Y-%m-%d %H:%M')))], head_detail]
    for r in rows:
        detail.append([r['e'].Id.Value, r['series'], r['fam'], r['typ'], r['mark']] +
                      [None if r['vals'][n] is None else round(r['vals'][n], 3) for n in names] +
                      [round(r['depth'], 3), r['used'], '是 Yes' if r['group'] else ''])
    st_head = ['数量 Count', '最小 Min (m)', '最大 Max (m)', '平均 Avg (m)', '不同取值数 Distinct', '缺值 Missing']

    def grouped(keyf, key_head):
        g = {}
        order = []
        for r in rows:
            k = keyf(r)
            if k not in g:
                g[k] = []
                order.append(k)
            g[k].append(r['depth'])
        out = [key_head + st_head]
        for k in order:
            out.append(list(k) + stats(g[k]))
        return out

    by_series = grouped(lambda r: (r['series'],), ['系列 Series'])
    by_family = grouped(lambda r: (r['series'], r['fam']), ['系列 Series', '族 Family'])
    by_type = grouped(lambda r: (r['series'], r['fam'], r['typ']), ['系列 Series', '族 Family', '类型 Type'])
    deep_rows = [r for r in rows if r['depth'] >= deep]
    deep_sheet = [['ElementId 元素ID', '系列 Series', '标记 Mark', '族 Family', '类型 Type', '采用井深 Depth (m)']]
    for r in sorted(deep_rows, key=lambda r: -r['depth']):
        deep_sheet.append([r['e'].Id.Value, r['series'], r['mark'], r['fam'], r['typ'], round(r['depth'], 3)])

    dlg = WF.SaveFileDialog()
    dlg.Title = TITLE
    dlg.Filter = 'Excel 表格 (*.xlsx)|*.xlsx'
    dlg.FileName = '井深统计_%s.xlsx' % time.strftime('%Y%m%d_%H%M')
    try:
        if not os.path.isdir(DOC_DIR):
            os.makedirs(DOC_DIR)
        dlg.InitialDirectory = DOC_DIR
    except Exception:
        pass
    if dlg.ShowDialog() != WF.DialogResult.OK:
        return
    path = dlg.FileName
    xlsx_write(path, [
        ('明细 Detail', detail, {'title_rows': 1, 'header_rows': 1, 'widths': [12, 18, 26, 30, 12] + [12] * len(names) + [14, 14, 10]}),
        ('按系列 By Series', by_series, {'widths': [24] + [12] * 6}),
        ('按族 By Family', by_family, {'widths': [24, 30] + [12] * 6}),
        ('按类型 By Type', by_type, {'widths': [24, 30, 30] + [12] * 6}),
        ('深井 ≥%gm' % deep, deep_sheet, {'widths': [12, 24, 12, 26, 30, 14]}),
    ])
    msg = '已导出 %d 口井（%s）。\n深度 ≥ %g m 的井：%d 口。\n\n%s' % (len(rows), scope, deep, len(deep_rows), path)
    msg += '\n\n要不要现在在模型里选中这些深井？（做埋深范围图、隔离显示用）'
    if deep_rows and forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
        uidoc.Selection.SetElementIds(List[DB.ElementId]([r['e'].Id for r in deep_rows]))
    elif not deep_rows:
        forms.alert(msg.split('\n\n要不要')[0], title=TITLE)
    try:
        System.Diagnostics.Process.Start('explorer.exe', '/select,"%s"' % path)
    except Exception:
        pass


if __name__ == '__main__':
    try:
        main()
    except Exception:
        forms.alert('出错了：\n' + traceback.format_exc(), title=TITLE)
