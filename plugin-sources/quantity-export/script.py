# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "一键框选\n提量导出Excel"
__author__ = "KAIFA"
__doc__ = ("框选（或整个项目、当前视图）里的结构柱、梁、结构墙、基础、楼板和钢筋，按楼层汇总：\n"
           "混凝土体积（m³）、钢材重量（t，按材料密度算）、钢筋按直径汇总（根数、总长、重量）。\n"
           "导出为 Excel 可直接打开的表格（混凝土、钢材、钢筋三个工作表）。")

import math
import io
import os
import time
import traceback
import clr
import System
clr.AddReference('RevitAPI')
from Autodesk.Revit import DB
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms

doc = revit.doc
uidoc = revit.uidoc

TITLE = u"一键框选提量导出Excel"
FT3_TO_M3 = 0.3048 ** 3
FT_TO_M = 0.3048
FT_TO_MM = 304.8
STEEL_DENSITY = 7850.0      # kg/m³，钢筋统一用这个密度
STEEL_MIN_DENSITY = 6000.0  # 材料密度达到这个值，按钢材算
OUT_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools")

QTO_BICS = [
    DB.BuiltInCategory.OST_StructuralColumns,
    DB.BuiltInCategory.OST_StructuralFraming,
    DB.BuiltInCategory.OST_Walls,
    DB.BuiltInCategory.OST_StructuralFoundation,
    DB.BuiltInCategory.OST_Floors,
    DB.BuiltInCategory.OST_Rebar,
]
QTO_CAT_IDS = set(int(b) for b in QTO_BICS)


def _u(x):
    if x is None:
        return u''
    return x if isinstance(x, type(u'')) else u'%s' % x


def _eid(x):
    """ElementId 的整数值；取不到返回 -1。"""
    if x is None:
        return -1
    try:
        return int(x.Value)
    except Exception:
        try:
            return int(x.IntegerValue)
        except Exception:
            return -1


def _is_qto(e):
    try:
        return e is not None and e.Category is not None and _eid(e.Category.Id) in QTO_CAT_IDS
    except Exception:
        return False


def _level_name(e):
    """楼层（标高）名。钢筋取所在主体的标高；没有标高的记为「(无标高)」。"""
    try:
        if e.Category is not None and _eid(e.Category.Id) == int(DB.BuiltInCategory.OST_Rebar):
            host = doc.GetElement(e.GetHostId())
            if host is not None:
                e = host
        lid = e.LevelId
        if _eid(lid) > 0:
            lvl = doc.GetElement(lid)
            if lvl is not None:
                return _u(lvl.Name)
    except Exception:
        pass
    return u'(无标高)'


def _type_name(e):
    try:
        t = doc.GetElement(e.GetTypeId())
        if t is not None:
            return _u(t.Name)
    except Exception:
        pass
    return _u(e.Name)


def _volume_m3(e):
    """构件体积（m³）。取不到返回 0。"""
    p = e.get_Parameter(DB.BuiltInParameter.HOST_VOLUME_COMPUTED)
    if p is None or not p.HasValue:
        p = e.LookupParameter(u'Volume')
    if p is not None and p.HasValue:
        return p.AsDouble() * FT3_TO_M3
    return 0.0


def _density(e):
    """材料的密度（kg/m³）；取不到返回 None。"""
    try:
        mid = e.StructuralMaterialId
        if _eid(mid) < 0:
            return None
        mat = doc.GetElement(mid)
        if mat is None:
            return None
        pse = doc.GetElement(mat.StructuralAssetId)
        if pse is None:
            return None
        # Revit 返回的密度单位是 kg/ft³（混凝土约 68），这里统一换算成 kg/m³（约 2400）
        return float(pse.GetStructuralAsset().Density) / FT3_TO_M3
    except Exception:
        return None


def _material_name(e):
    try:
        mid = e.StructuralMaterialId
        if _eid(mid) >= 0:
            mat = doc.GetElement(mid)
            if mat is not None:
                return _u(mat.Name)
    except Exception:
        pass
    return u''


def _pick_elements(sel_ids):
    """要统计的构件：有选中就用选中的；没有就问整个项目还是当前视图。取消返回 (None, None)。"""
    if sel_ids:
        elems = [e for e in (doc.GetElement(i) for i in sel_ids) if _is_qto(e)]
        return elems, u'框选的构件'
    options = [u'整个项目', u'当前视图']
    pick = forms.CommandSwitchWindow.show(options, message=u'没有框选构件，统计哪个范围？')
    if not pick:
        return None, None
    if pick == u'当前视图':
        col = DB.FilteredElementCollector(doc, uidoc.ActiveView.Id)
        src = u'当前视图'
    else:
        col = DB.FilteredElementCollector(doc)
        src = u'整个项目'
    flt = DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](QTO_BICS))
    return list(col.WherePasses(flt).WhereElementIsNotElementType().ToElements()), src


def _quantities(elems):
    """汇总：混凝土（楼层、类别、类型）、钢材（同上，含重量）、钢筋（楼层、直径）。"""
    conc = {}
    steel = {}
    rebar = {}
    skipped = 0
    for e in elems:
        try:
            if _eid(e.Category.Id) == int(DB.BuiltInCategory.OST_Rebar):
                lvl = _level_name(e)
                t = doc.GetElement(e.GetTypeId())
                d_ft = t.BarNominalDiameter if t is not None else 0.0
                dia = round(d_ft * FT_TO_MM, 1)
                bars = int(e.NumberOfBarPositions) if int(e.NumberOfBarPositions) > 0 else 1
                length_m = e.TotalLength * FT_TO_M
                r_m = d_ft * FT_TO_M / 2.0
                kg = length_m * math.pi * r_m * r_m * STEEL_DENSITY
                key = (lvl, dia)
                row = rebar.setdefault(key, [0, 0.0, 0.0])
                row[0] += bars
                row[1] += length_m
                row[2] += kg
                continue

            lvl = _level_name(e)
            cat = _u(e.Category.Name)
            typ = _type_name(e)
            vol = _volume_m3(e)
            dens = _density(e)
            is_steel = (dens is not None and dens >= STEEL_MIN_DENSITY) or (
                dens is None and (u'钢' in _material_name(e) or u'steel' in _material_name(e).lower()))
            if is_steel:
                d = dens if (dens is not None and dens >= STEEL_MIN_DENSITY) else STEEL_DENSITY
                row = steel.setdefault((lvl, cat, typ), [0, 0.0, 0.0])
                row[0] += 1
                row[1] += vol
                row[2] += vol * d  # kg，导出时再换算成 t
            else:
                row = conc.setdefault((lvl, cat, typ), [0, 0.0])
                row[0] += 1
                row[1] += vol
        except Exception:
            skipped += 1
    return {'conc': conc, 'steel': steel, 'rebar': rebar, 'skipped': skipped}


def _xml_escape(s):
    s = _u(s)
    return s.replace(u'&', u'&amp;').replace(u'<', u'&lt;').replace(u'>', u'&gt;').replace(u'"', u'&quot;')


def _cell(v, style=None):
    sa = (u' ss:StyleID="%s"' % style) if style else u''
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, (int, float)):
        num = (u'%.3f' % v) if isinstance(v, float) else (u'%d' % v)
        return u'<Cell%s><Data ss:Type="Number">%s</Data></Cell>' % (sa, num)
    return u'<Cell%s><Data ss:Type="String">%s</Data></Cell>' % (sa, _xml_escape(v))


def _sheet(name, header, rows, total):
    x = [u'<Worksheet ss:Name="%s"><Table>' % _xml_escape(name)]
    x.append(u'<Row>' + u''.join(_cell(h, u'h') for h in header) + u'</Row>')
    for r in rows:
        x.append(u'<Row>' + u''.join(_cell(v) for v in r) + u'</Row>')
    if total is not None:
        x.append(u'<Row>' + u''.join(_cell(v, u'h') for v in total) + u'</Row>')
    x.append(u'</Table></Worksheet>')
    return u''.join(x)


def _build_xml(res):
    conc_rows = []
    conc_sum = 0.0
    for (lvl, cat, typ), (n, vol) in sorted(res['conc'].items()):
        conc_rows.append([lvl, cat, typ, n, round(vol, 3)])
        conc_sum += vol
    steel_rows = []
    steel_sum = 0.0
    for (lvl, cat, typ), (n, vol, kg) in sorted(res['steel'].items()):
        steel_rows.append([lvl, cat, typ, n, round(kg / 1000.0, 3)])
        steel_sum += kg / 1000.0
    rebar_rows = []
    rebar_sum = [0, 0.0, 0.0]
    for (lvl, dia), (bars, length_m, kg) in sorted(res['rebar'].items()):
        rebar_rows.append([lvl, dia, bars, round(length_m, 3), round(kg, 3)])
        rebar_sum[0] += bars
        rebar_sum[1] += length_m
        rebar_sum[2] += kg
    head = (u'<?xml version="1.0" encoding="UTF-8"?>\n'
            u'<?mso-application progid="Excel.Sheet"?>\n'
            u'<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
            u'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">\n'
            u'<Styles><Style ss:ID="h"><Font ss:Bold="1"/>'
            u'<Interior ss:Color="#D9E1F2" ss:Pattern="Solid"/></Style></Styles>\n')
    body = [
        _sheet(u'混凝土', [u'楼层', u'构件类别', u'类型', u'数量', u'体积(m³)'], conc_rows,
               [u'合计', u'', u'', sum(r[3] for r in conc_rows), round(conc_sum, 3)]),
        _sheet(u'钢材', [u'楼层', u'构件类别', u'类型', u'数量', u'重量(t)'], steel_rows,
               [u'合计', u'', u'', sum(r[3] for r in steel_rows), round(steel_sum, 3)]),
        _sheet(u'钢筋', [u'楼层', u'直径(mm)', u'根数', u'总长(m)', u'重量(kg)'], rebar_rows,
               [u'合计', u'', rebar_sum[0], round(rebar_sum[1], 3), round(rebar_sum[2], 3)]),
    ]
    return head + u'\n'.join(body) + u'\n</Workbook>\n', conc_sum, steel_sum, rebar_sum


def _export(res, src_txt, out_dir=None):
    xml, conc_sum, steel_sum, rebar_sum = _build_xml(res)
    d = out_dir or OUT_DIR
    if not os.path.exists(d):
        os.makedirs(d)
    path = os.path.join(d, u'提量_%s.xml' % time.strftime('%Y%m%d_%H%M'))
    with io.open(path, 'w', encoding='utf-8-sig') as f:
        f.write(xml)
    return path, conc_sum, steel_sum, rebar_sum


def main():
    try:
        sel_ids = list(uidoc.Selection.GetElementIds())
        elems, src_txt = _pick_elements(sel_ids)
        if elems is None:
            return
        if not elems:
            forms.alert(u"没有找到可统计的构件（梁、柱、墙、基础、楼板、钢筋）。", title=TITLE)
            return
        res = _quantities(elems)
        path, conc_sum, steel_sum, rebar_sum = _export(res, src_txt)
        lines = [u'统计范围：%s，共 %d 个构件。' % (src_txt, len(elems)),
                 u'混凝土体积：%.3f m³' % conc_sum,
                 u'钢材重量：%.3f t' % steel_sum,
                 u'钢筋：%d 根，总长 %.2f m，重量 %.2f kg' % (rebar_sum[0], rebar_sum[1], rebar_sum[2])]
        if res['skipped']:
            lines.append(u'有 %d 个构件读取失败，已跳过。' % res['skipped'])
        lines.append(u'已导出：' + path)
        forms.alert(u'\n'.join(lines), title=TITLE)
    except Exception as e:
        forms.alert(u'提量过程发生异常：\n' + u'%s' % e + u'\n\n' + u'%s' % traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
