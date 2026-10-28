# -*- coding: utf-8 -*-
__title__ = '排管井工程量表'
__doc__ = ('一键统计小市政管线的工程量并导出 Excel（有框选就只统计框选的，没有就统计整个项目）：\n'
           '① 井：按系列（11KV、275KV、ELV、TELCO、LV…）、族类型、深度档汇总数量，附逐口明细；\n'
           '② 排管：按族类型汇总根数、总长度（m）、孔数、孔·米（孔数×长度，估管材用）；导管同样统计；\n'
           '③ 松木桩（根数）和垫层（块数、体积）。只读，不改模型。')

import re
import time
import math
from pyrevit import revit, DB, UI, forms

# @include _shared/well_config.py
# @include _shared/xlsx_lite.py
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
TITLE = u'排管井工程量表'
FT = 304.8
DEPTH_STEP = 500.0         # 深度按 500 mm 一档汇总（预制井按档订货）
_HOLES = re.compile(u'(\\d+)\\s*孔')
_DIA = re.compile(u'[ΦφØ]\\s*(\\d+)')
PILE_KEYS = (u'松木桩', u'Φ75')
PAD_KEYS = (u'垫层',)


def _mark(e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        return (p.AsString() or u'').strip() if p is not None else u''
    except Exception:
        return u''


def _depth_mm(e):
    p = e.LookupParameter(u'井内深度')
    if p is not None and p.HasValue and p.StorageType == DB.StorageType.Double and p.AsDouble() > 0:
        return p.AsDouble() * FT
    b = e.LookupParameter(u'井底标高')
    o = e.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
    if b is not None and b.HasValue and o is not None and o.HasValue:
        return (o.AsDouble() - b.AsDouble()) * FT
    return None


def _ftname(e):
    try:
        s = e.Symbol
        return s.FamilyName, s.Name
    except Exception:
        try:
            t = doc.GetElement(e.GetTypeId())
            return (t.FamilyName if t is not None else u''), (t.Name if t is not None else u'')
        except Exception:
            return u'', u''


def _length_m(e):
    try:
        loc = e.Location
        if isinstance(loc, DB.LocationCurve):
            return loc.Curve.Length * FT / 1000.0
    except Exception:
        pass
    try:
        p = e.get_Parameter(DB.BuiltInParameter.CURVE_ELEM_LENGTH)
        if p is not None and p.HasValue:
            return p.AsDouble() * FT / 1000.0
    except Exception:
        pass
    return 0.0


def _volume_m3(e):
    try:
        opt = DB.Options()
        tot = 0.0
        for g in e.get_Geometry(opt):
            if isinstance(g, DB.Solid):
                tot += g.Volume
            elif isinstance(g, DB.GeometryInstance):
                for x in g.GetInstanceGeometry():
                    if isinstance(x, DB.Solid):
                        tot += x.Volume
        return tot * (FT / 1000.0) ** 3
    except Exception:
        return 0.0


def collect(sel_ids):
    """返回 (井, 排管, 导管, 桩, 垫层) 五个列表；有选择时只在选择里找。"""
    def src(bic, cls=None):
        if sel_ids:
            return [e for e in (doc.GetElement(i) for i in sel_ids) if e is not None and e.Category is not None and e.Category.Id.Value == int(bic)]
        col = DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType()
        return list(col.ToElements())
    wells = [e for e in src(DB.BuiltInCategory.OST_GenericModel)
             if isinstance(e, DB.FamilyInstance) and wcfg_series_of(_mark(e), WCFG, True) is not None]
    banks = []
    for e in src(DB.BuiltInCategory.OST_StructuralFraming):
        banks.append(e)
    conduits = src(DB.BuiltInCategory.OST_Conduit)
    piles, pads = [], []
    for e in src(DB.BuiltInCategory.OST_StructuralFoundation):
        f, t = _ftname(e)
        txt = f + u' ' + t + u' ' + (e.Name or u'')
        if any(k in txt for k in PILE_KEYS):
            piles.append(e)
        elif any(k in txt for k in PAD_KEYS):
            pads.append(e)
    return wells, banks, conduits, piles, pads


def build(wells, banks, conduits, piles, pads):
    sheets = []
    # 井
    by = {}
    detail = [[u'井号', u'系列', u'族', u'类型', u'井内深度(mm)', u'深度档(mm)', u'构件ID']]
    for e in sorted(wells, key=lambda x: wcfg_well_sort_key(_mark(x), WCFG)):
        mk = _mark(e)
        sr = wcfg_series_of(mk, WCFG, True)
        lab = sr[u'label'] if sr else u'其他'
        f, t = _ftname(e)
        d = _depth_mm(e)
        step = (math.ceil((d - 1e-6) / DEPTH_STEP) * DEPTH_STEP) if d else None
        by[(lab, f, t, step)] = by.get((lab, f, t, step), 0) + 1
        detail.append([mk, lab, f, t, round(d) if d else u'', int(step) if step else u'', e.Id.Value])
    summ = [[u'系列', u'族', u'类型', u'深度档(mm)', u'数量']]
    for k in sorted(by, key=lambda k: (k[0], k[1], k[2], k[3] or 0)):
        summ.append([k[0], k[1], k[2], int(k[3]) if k[3] else u'未取到深度', by[k]])
    summ.append([u'合计', u'', u'', u'', len(wells)])
    sheets.append((u'井汇总', summ, {'widths': [18, 24, 34, 14, 8], 'header_rows': 1}))
    sheets.append((u'井明细', detail, {'widths': [12, 18, 24, 34, 14, 12, 12], 'header_rows': 1}))
    # 排管、导管
    rows = [[u'类别', u'族', u'类型', u'根数', u'总长度(m)', u'孔数', u'孔·米']]
    agg = {}
    for cat, es in ((u'排管', banks), (u'导管', conduits)):
        for e in es:
            f, t = _ftname(e)
            key = (cat, f, t)
            a = agg.setdefault(key, [0, 0.0, 0])
            a[0] += 1
            a[1] += _length_m(e)
            m = _HOLES.search(f + u' ' + t)
            a[2] = int(m.group(1)) if m else a[2]
    tl = th = 0.0
    for key in sorted(agg):
        n, L, h = agg[key]
        rows.append([key[0], key[1], key[2], n, round(L, 2), h if h else u'', round(L * h, 1) if h else u''])
        tl += L
        th += L * h
    rows.append([u'合计', u'', u'', sum(a[0] for a in agg.values()), round(tl, 2), u'', round(th, 1)])
    sheets.append((u'排管导管', rows, {'widths': [8, 36, 36, 8, 12, 8, 12], 'header_rows': 1}))
    # 桩与垫层
    pv = [[u'项目', u'族 / 类型', u'数量', u'体积(m³)']]
    for lab, es in ((u'松木桩', piles), (u'垫层', pads)):
        g = {}
        for e in es:
            f, t = _ftname(e)
            key = (f + u' / ' + t) if (f or t) else (e.Name or u'')
            a = g.setdefault(key, [0, 0.0])
            a[0] += 1
            if lab == u'垫层':
                a[1] += _volume_m3(e)
        for key in sorted(g):
            pv.append([lab, key, g[key][0], round(g[key][1], 3) if lab == u'垫层' else u''])
    sheets.append((u'桩与垫层', pv, {'widths': [10, 50, 8, 12], 'header_rows': 1}))
    return sheets, (len(wells), len(banks), len(conduits), len(piles), len(pads), tl, th)


def main():
    try:
        try:
            sel = list(uidoc.Selection.GetElementIds())
        except Exception:
            sel = []
        pb = forms.ProgressBar(title=u'正在统计工程量…', cancellable=False)
        try:
            data = collect(sel)
            sheets, st = build(*data)
        finally:
            pb.close()
        nw, nb, nc, npile, npad, tl, th = st
        if nw + nb + nc + npile + npad == 0:
            forms.alert(u'没有找到可统计的内容（井、排管、导管、松木桩、垫层）。' + (u'\n选中的图元里没有这些类别。' if sel else u''), title=TITLE)
            return
        msg = (u'统计范围：%s\n\n• 井 %d 口\n• 排管 %d 根、导管 %d 根，总长 %.1f m，孔·米 %.0f\n• 松木桩 %d 根、垫层 %d 块\n\n导出 Excel？'
               % (u'选中的图元' if sel else u'整个项目', nw, nb, nc, tl, th, npile, npad))
        if not forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
            return
        sd = WF.SaveFileDialog()
        sd.Title = TITLE
        sd.Filter = u'Excel 表格 (*.xlsx)|*.xlsx'
        sd.FileName = u'排管井工程量_%s.xlsx' % time.strftime(u'%Y%m%d_%H%M')
        if sd.ShowDialog() != WF.DialogResult.OK:
            return
        xlsx_write(sd.FileName, sheets)
        try:
            System.Diagnostics.Process.Start(u'explorer.exe', u'/select,"%s"' % sd.FileName)
        except Exception:
            pass
    except Exception:
        forms.alert(u'出错了：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
