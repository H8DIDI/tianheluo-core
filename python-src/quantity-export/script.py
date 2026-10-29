# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "一键框选\n提量表格"
__author__ = "KAIFA"
__doc__ = ("框选（或整个项目、当前视图）里的构件，先在窗口里按标签页给出表格，看完可导出 Excel（xlsx）：\n"
           "结构：混凝土体积（m³）、钢材重量（t，按材料密度算）、钢筋按直径汇总（根数、总长、重量）；\n"
           "小市政：井按系列、族类型、深度档汇总并附逐口明细，排管和导管按族类型汇总根数、长度、孔数、孔·米，松木桩根数和垫层块数、体积。\n"
           "排管、松木桩、垫层各自单算，不会混进混凝土表。也可以在 Revit 里生成明细表（之后模型改了自动更新）。只读，不改模型（生成明细表除外）。")

import re
import json
import math
import io
import os
import time
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from Autodesk.Revit import DB
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms
import System.Windows.Forms as WF
import System.Drawing as SD
from Autodesk.Revit.UI.Selection import ISelectionFilter

doc = revit.doc
uidoc = revit.uidoc

TITLE = u"一键框选提量表格"
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
    DB.BuiltInCategory.OST_GenericModel,      # 只认井（有井号的）
    DB.BuiltInCategory.OST_Conduit,
]
QTO_CAT_IDS = set(int(b) for b in QTO_BICS)

# 明细表：(表名, 类别, 字段候选名)。字段名按 Revit 里显示的名字匹配，中英文都试。
LEVEL_FIELD = [u'标高', u'基准标高', u'基准约束', u'Level', u'Base Level', u'Base Constraint']
SCHEDULE_SPECS = [
    (u'KAIFA-钢筋明细表', DB.BuiltInCategory.OST_Rebar, [
        [u'钢筋直径', u'Bar Diameter'],
        [u'总钢筋长度', u'Total Bar Length', u'Total Length'],
        [u'数量', u'数量(按钢筋集)', u'Quantity'],
    ]),
    (u'KAIFA-结构柱明细表', DB.BuiltInCategory.OST_StructuralColumns, [
        [u'底部标高', u'Base Level'], [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
    (u'KAIFA-结构框架明细表', DB.BuiltInCategory.OST_StructuralFraming, [
        LEVEL_FIELD, [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
    (u'KAIFA-墙体混凝土明细表', DB.BuiltInCategory.OST_Walls, [
        [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
    (u'KAIFA-结构基础明细表', DB.BuiltInCategory.OST_StructuralFoundation, [
        LEVEL_FIELD, [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
    (u'KAIFA-楼板明细表', DB.BuiltInCategory.OST_Floors, [
        LEVEL_FIELD, [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
]


# @include _shared/form_fit.py
# @include _shared/well_config.py
# @include _shared/xlsx_lite.py
WCFG = wcfg_load()
DEPTH_STEP = 500.0         # 井深度按 500 mm 一档汇总（预制井按档订货）
_HOLES = re.compile(u'(\\d+)\\s*孔')
PILE_KEYS = (u'松木桩', u'Φ75')
PAD_KEYS = (u'垫层',)
# 排管（含雨水沟）：族名以这些前缀开头，或名字里有「排管」「N孔」
BANK_PREFIXES = (u'TELCO', u'ELV', u'LV-', u'ELE-', u'雨水沟')


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


def _mark(e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        return (p.AsString() or u'').strip() if p is not None else u''
    except Exception:
        return u''


def _is_well(e):
    """井：常规模型族实例，标记（井号）属于配置里的某个井系列。"""
    try:
        return (isinstance(e, DB.FamilyInstance) and _eid(e.Category.Id) == int(DB.BuiltInCategory.OST_GenericModel)
                and wcfg_series_of(_mark(e), WCFG, True) is not None)
    except Exception:
        return False


def _ftname(e):
    try:
        s = e.Symbol
        return _u(s.FamilyName), _u(s.Name)
    except Exception:
        try:
            t = doc.GetElement(e.GetTypeId())
            return (_u(t.FamilyName) if t is not None else u''), (_u(t.Name) if t is not None else u'')
        except Exception:
            return u'', u''


def _is_bank(e):
    """排管（含雨水沟）：结构框架里族名像排管的；真正的梁仍按混凝土/钢材算。"""
    f, t = _ftname(e)
    fu = f.upper()
    if any(fu.startswith(k) for k in BANK_PREFIXES):
        return True
    return u'排管' in f or u'排管' in t or bool(_HOLES.search(f + u' ' + t))


def _pile_or_pad(e):
    """结构基础里的松木桩 / 垫层；其他返回 None。"""
    f, t = _ftname(e)
    txt = f + u' ' + t + u' ' + _u(e.Name)
    if any(k in txt for k in PILE_KEYS):
        return u'松木桩'
    if any(k in txt for k in PAD_KEYS):
        return u'垫层'
    return None


def _is_qto(e):
    try:
        if e is None or e.Category is None:
            return False
        c = _eid(e.Category.Id)
        if c not in QTO_CAT_IDS:
            return False
        if c == int(DB.BuiltInCategory.OST_GenericModel):
            return _is_well(e)
        return True
    except Exception:
        return False


class _QtoFilter(ISelectionFilter):
    def AllowElement(self, e):
        return _is_qto(e)

    def AllowReference(self, r, p):
        return False


def _depth_mm(e):
    """井内深度（mm）：优先「井内深度」参数，没有就用 偏移 − 井底标高。"""
    p = e.LookupParameter(u'井内深度')
    if p is not None and p.HasValue and p.StorageType == DB.StorageType.Double and p.AsDouble() > 0:
        return p.AsDouble() * FT_TO_MM
    b = e.LookupParameter(u'井底标高')
    o = e.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
    if b is not None and b.HasValue and o is not None and o.HasValue:
        return (o.AsDouble() - b.AsDouble()) * FT_TO_MM
    return None


def _length_m(e):
    try:
        loc = e.Location
        if isinstance(loc, DB.LocationCurve):
            return loc.Curve.Length * FT_TO_M
    except Exception:
        pass
    try:
        p = e.get_Parameter(DB.BuiltInParameter.CURVE_ELEM_LENGTH)
        if p is not None and p.HasValue:
            return p.AsDouble() * FT_TO_M
    except Exception:
        pass
    return 0.0


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
    if p is not None and p.HasValue and p.AsDouble() > 0:
        return p.AsDouble() * FT3_TO_M3
    try:                                        # 体积参数取不到（嵌套族等）：累加几何实体
        tot = 0.0
        for g in e.get_Geometry(DB.Options()):
            if isinstance(g, DB.Solid):
                tot += g.Volume
            elif isinstance(g, DB.GeometryInstance):
                for x in g.GetInstanceGeometry():
                    if isinstance(x, DB.Solid):
                        tot += x.Volume
        return tot * FT3_TO_M3
    except Exception:
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
    """要统计的构件：有选中就用选中的；没有就问：现在框选 / 整个项目 / 当前视图。取消返回 (None, None)。"""
    if sel_ids:
        elems = [e for e in (doc.GetElement(i) for i in sel_ids) if _is_qto(e)]
        return elems, u'框选的构件'
    options = [u'现在框选', u'整个项目', u'当前视图']
    pick = forms.CommandSwitchWindow.show(options, message=u'没有选中构件，统计哪个范围？')
    if not pick:
        return None, None
    if pick == u'现在框选':
        try:
            got = list(uidoc.Selection.PickElementsByRectangle(_QtoFilter(), u'框选要统计的构件（梁柱墙板基础、钢筋、井、排管、导管、松木桩、垫层），框完点 Finish'))
        except Exception:
            return None, None
        return [e for e in got if _is_qto(e)], u'框选的构件'
    if pick == u'当前视图':
        col = DB.FilteredElementCollector(doc, uidoc.ActiveView.Id)
        src = u'当前视图'
    else:
        col = DB.FilteredElementCollector(doc)
        src = u'整个项目'
    flt = DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](QTO_BICS))
    return [e for e in col.WherePasses(flt).WhereElementIsNotElementType().ToElements() if _is_qto(e)], src


# ---- 要排除的楼板（场地里的道路、绿化、开挖范围、遮罩这类面层，不是结构混凝土）
EXCLUDE_FILE = os.path.join(OUT_DIR, u'提量排除规则.json')
EXCLUDE_DEFAULT = {
    u'说明': (u'floor_keywords：楼板类型名里含这些字就不算提量（道路、绿化、开挖范围、遮罩这类场地面层）；'
            u'skip_nonstructural：楼板的「结构」没勾选也不算（这个项目的场地面层都没勾结构）。'
            u'被排除的在窗口「已排除」标签页里列出来，并写明原因，不会悄悄消失。'),
    u'floor_keywords': [u'道路', u'绿化', u'开挖', u'挖掘', u'遮罩', u'临时', u'场地', u'回填', u'土方', u'草坪', u'路面'],
    u'skip_nonstructural': True,
}
_EXCL = {}


def _exclude_rules():
    if _EXCL:
        return _EXCL
    rules = dict(EXCLUDE_DEFAULT)
    try:
        if os.path.exists(EXCLUDE_FILE):
            with io.open(EXCLUDE_FILE, 'r', encoding='utf-8-sig') as f:
                d = json.load(f)
            rules[u'floor_keywords'] = d.get(u'floor_keywords', rules[u'floor_keywords'])
            rules[u'skip_nonstructural'] = bool(d.get(u'skip_nonstructural', True))
        else:
            if not os.path.isdir(OUT_DIR):
                os.makedirs(OUT_DIR)
            with io.open(EXCLUDE_FILE, 'w', encoding='utf-8') as f:
                f.write(json.dumps(EXCLUDE_DEFAULT, ensure_ascii=False, indent=2))
    except Exception:
        pass
    _EXCL.update(rules)
    return _EXCL


def _exclude_reason(e):
    """楼板是不是场地面层：是返回原因文字，否返回 None。只看楼板。"""
    try:
        if _eid(e.Category.Id) != int(DB.BuiltInCategory.OST_Floors):
            return None
        r = _exclude_rules()
        tn = _type_name(e)
        for k in r[u'floor_keywords']:
            if k and k in tn:
                return u'类型名含「%s」' % k
        if r[u'skip_nonstructural']:
            p = e.get_Parameter(DB.BuiltInParameter.FLOOR_PARAM_IS_STRUCTURAL)
            if p is not None and p.AsInteger() == 0:
                return u'非结构楼板（「结构」没勾选）'
    except Exception:
        pass
    return None


def _zrange_m(e):
    """构件的最低、最高标高（m，项目坐标）。取不到返回 (None, None)。"""
    bb = e.get_BoundingBox(None)
    if bb is None:
        return None, None
    return bb.Min.Z * FT_TO_M, bb.Max.Z * FT_TO_M


def _surface_m2(e):
    """构件表面积（m²，几何实体的全部面，可当模板面积的参考）。取不到返回 0。"""
    try:
        tot = 0.0
        for g in e.get_Geometry(DB.Options()):
            if isinstance(g, DB.Solid):
                tot += g.SurfaceArea
            elif isinstance(g, DB.GeometryInstance):
                for x in g.GetInstanceGeometry():
                    if isinstance(x, DB.Solid):
                        tot += x.SurfaceArea
        return tot * FT_TO_M * FT_TO_M
    except Exception:
        return 0.0


def _span_m(e):
    """线状构件（梁、墙）的长度；柱取高度；板返回 0。"""
    c = _eid(e.Category.Id)
    if c == int(DB.BuiltInCategory.OST_StructuralColumns):
        lo, hi = _zrange_m(e)
        return (hi - lo) if lo is not None else 0.0
    if c in (int(DB.BuiltInCategory.OST_StructuralFraming), int(DB.BuiltInCategory.OST_Walls)):
        return _length_m(e)
    return 0.0


def _merge_z(row, i, lo, hi):
    """row[i]、row[i+1] 存最低/最高标高。"""
    if lo is not None:
        row[i] = lo if row[i] is None else min(row[i], lo)
    if hi is not None:
        row[i + 1] = hi if row[i + 1] is None else max(row[i + 1], hi)


_WELL_LW = re.compile(u'(\\d+)\\s*\\(L\\)\\s*x\\s*(\\d+)\\s*\\(W\\)')
_THICK = re.compile(u'(\\d+)\\s*mm\\s*厚')


def _well_elev_m(e):
    """井底标高、井盖面标高（m，项目坐标）。取不到的是 None。"""
    try:
        lv = doc.GetElement(e.LevelId)
        base = lv.Elevation if lv is not None else 0.0
    except Exception:
        base = 0.0
    b = e.LookupParameter(u'井底标高')
    o = e.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
    bot = (base + b.AsDouble()) * FT_TO_M if (b is not None and b.HasValue and b.StorageType == DB.StorageType.Double) else None
    top = (base + o.AsDouble()) * FT_TO_M if (o is not None and o.HasValue) else None
    return bot, top


def _quantities(elems):
    """汇总：混凝土（楼层、类别、类型、材质）、钢材（同上，含重量）、钢筋（楼层、直径），以及井、排管导管、桩垫层、被排除的楼板。
    构件多时显示进度条（读几何较慢），可以取消；取消返回 None（只读，不改模型）。"""
    res = {'conc': {}, 'steel': {}, 'rebar': {}, 'wells': [], 'banks': {}, 'piles': {}, 'excluded': {}, 'skipped': 0}
    pb = forms.ProgressBar(title=u'正在统计工程量…（点取消可停止）', cancellable=True) if len(elems) > 300 else None
    try:
        for i, e in enumerate(elems):
            if pb is not None and i % 20 == 0:
                pb.update_progress(i, len(elems))
                if pb.cancelled:
                    return None
            try:
                _add_one(e, res)
            except Exception:
                res['skipped'] += 1
    finally:
        if pb is not None:
            try:
                pb.close()
            except Exception:
                pass
    return res


def _add_one(e, res):
    """把一个构件的量累加进汇总表。"""
    if _eid(e.Category.Id) == int(DB.BuiltInCategory.OST_Rebar):
        lvl = _level_name(e)
        t = doc.GetElement(e.GetTypeId())
        d_ft = t.BarNominalDiameter if t is not None else 0.0
        dia = round(d_ft * FT_TO_MM, 1)
        bars = int(e.NumberOfBarPositions) if int(e.NumberOfBarPositions) > 0 else 1
        length_m = e.TotalLength * FT_TO_M
        r_m = d_ft * FT_TO_M / 2.0
        kg = length_m * math.pi * r_m * r_m * STEEL_DENSITY
        row = res['rebar'].setdefault((lvl, dia), [0, 0.0, 0.0])
        row[0] += bars
        row[1] += length_m
        row[2] += kg
        return

    c = _eid(e.Category.Id)
    if c == int(DB.BuiltInCategory.OST_GenericModel):          # 井
        mk = _mark(e)
        sr = wcfg_series_of(mk, WCFG, True)
        f, t = _ftname(e)
        m = _WELL_LW.search(t) or _WELL_LW.search(f)
        bot, top = _well_elev_m(e)
        res['wells'].append((mk, sr[u'label'] if sr else u'其他', f, t, _depth_mm(e), e.Id.Value,
                             int(m.group(1)) if m else None, int(m.group(2)) if m else None, bot, top))
        return
    if c == int(DB.BuiltInCategory.OST_Conduit) or (c == int(DB.BuiltInCategory.OST_StructuralFraming) and _is_bank(e)):
        f, t = _ftname(e)
        is_conduit = (c == int(DB.BuiltInCategory.OST_Conduit))
        cat = u'导管' if is_conduit else (u'雨水沟' if f.startswith(u'雨水沟') else u'排管')
        a = res['banks'].setdefault((cat, f, t), [0, 0.0, 0, 0.0, None, None])      # 根数 长度 孔数 体积 最低 最高
        a[0] += 1
        a[1] += _length_m(e)
        m = _HOLES.search(f + u' ' + t)
        if m:
            a[2] = int(m.group(1))
        if not is_conduit:
            a[3] += _volume_m3(e)
        lo, hi = _zrange_m(e)
        _merge_z(a, 4, lo, hi)
        return
    if c == int(DB.BuiltInCategory.OST_StructuralFoundation):
        kind = _pile_or_pad(e)
        if kind:
            f, t = _ftname(e)
            key = (f + u' / ' + t) if (f or t) else _u(e.Name)
            a = res['piles'].setdefault((kind, key), [0, 0.0, 0.0, None, None])          # 数量 体积 总长(桩) 最低 最高
            a[0] += 1
            lo, hi = _zrange_m(e)
            if kind == u'垫层':
                a[1] += _volume_m3(e)
            elif lo is not None:
                a[2] += hi - lo
            _merge_z(a, 3, lo, hi)
            return
    why = _exclude_reason(e)
    if why:
        cat = _u(e.Category.Name)
        a = res['excluded'].setdefault((cat, _type_name(e), why), [0, 0.0, 0.0])
        a[0] += 1
        a[1] += _volume_m3(e)
        ap = e.get_Parameter(DB.BuiltInParameter.HOST_AREA_COMPUTED)
        if ap is not None and ap.HasValue:
            a[2] += ap.AsDouble() * FT_TO_M * FT_TO_M
        return
    lvl = _level_name(e)
    cat = _u(e.Category.Name)
    typ = _type_name(e)
    vol = _volume_m3(e)
    dens = _density(e)
    mname = _material_name(e)
    area = _surface_m2(e)
    span = _span_m(e)
    lo, hi = _zrange_m(e)
    is_steel = (dens is not None and dens >= STEEL_MIN_DENSITY) or (
        dens is None and (u'钢' in mname or u'steel' in mname.lower()))
    if is_steel:
        d = dens if (dens is not None and dens >= STEEL_MIN_DENSITY) else STEEL_DENSITY
        row = res['steel'].setdefault((lvl, cat, typ, mname), [0, 0.0, 0.0, 0.0, 0.0, None, None])
        row[0] += 1
        row[1] += vol
        row[2] += vol * d  # kg，导出时再换算成 t
        row[3] += area
        row[4] += span
        _merge_z(row, 5, lo, hi)
    else:
        row = res['conc'].setdefault((lvl, cat, typ, mname), [0, 0.0, 0.0, 0.0, None, None])
        row[0] += 1
        row[1] += vol
        row[2] += area
        row[3] += span
        _merge_z(row, 4, lo, hi)


# ============================================================
# 表格数据（窗口和 Excel 共用）
# ============================================================
def _r(v, nd=3):
    return u'' if v is None else round(v, nd)


def _table_rows(res):
    """返回若干张表：[(标题, 表头, 行, 合计行)]。行和合计行的数字都已经是最终显示的单位。"""
    conc_rows = []
    conc_sum = 0.0
    ca = cl = 0.0
    for (lvl, cat, typ, mat), (n, vol, area, span, lo, hi) in sorted(res['conc'].items()):
        conc_rows.append([lvl, cat, typ, mat, n, round(vol, 3), round(area, 2), round(span, 2), _r(lo), _r(hi)])
        conc_sum += vol
        ca += area
        cl += span
    steel_rows = []
    steel_sum = 0.0
    sa = sl = 0.0
    for (lvl, cat, typ, mat), (n, vol, kg, area, span, lo, hi) in sorted(res['steel'].items()):
        steel_rows.append([lvl, cat, typ, mat, n, round(kg / 1000.0, 3), round(vol, 3), round(area, 2), round(span, 2), _r(lo), _r(hi)])
        steel_sum += kg / 1000.0
        sa += area
        sl += span
    rebar_rows = []
    rebar_sum = [0, 0.0, 0.0]
    for (lvl, dia), (bars, length_m, kg) in sorted(res['rebar'].items()):
        rebar_rows.append([lvl, dia, bars, round(length_m, 3), round(kg, 3)])
        rebar_sum[0] += bars
        rebar_sum[1] += length_m
        rebar_sum[2] += kg
    tables = [
        (u'混凝土', [u'楼层', u'构件类别', u'类型', u'材质', u'数量', u'体积(m³)', u'表面积(m²)', u'长/高(m)', u'最低标高(m)', u'最高标高(m)'], conc_rows,
         [u'合计', u'', u'', u'', sum(r[4] for r in conc_rows), round(conc_sum, 3), round(ca, 2), round(cl, 2), u'', u'']),
        (u'钢材', [u'楼层', u'构件类别', u'类型', u'材质', u'数量', u'重量(t)', u'体积(m³)', u'表面积(m²)', u'长/高(m)', u'最低标高(m)', u'最高标高(m)'], steel_rows,
         [u'合计', u'', u'', u'', sum(r[4] for r in steel_rows), round(steel_sum, 3), round(sum(v[1] for v in res['steel'].values()), 3), round(sa, 2), round(sl, 2), u'', u'']),
        (u'钢筋', [u'楼层', u'直径(mm)', u'根数', u'总长(m)', u'重量(kg)'], rebar_rows,
         [u'合计', u'', rebar_sum[0], round(rebar_sum[1], 3), round(rebar_sum[2], 3)]),
    ]
    # ---- 小市政：井、排管导管、桩与垫层（没有的不出标签页）
    by, detail = {}, []
    for mk, lab, f, t, d, eid, L, W, bot, top in sorted(res['wells'], key=lambda w: wcfg_well_sort_key(w[0], WCFG)):
        step = (math.ceil((d - 1e-6) / DEPTH_STEP) * DEPTH_STEP) if d else None
        a = by.setdefault((lab, f, t, L, W, step), [0, None, None, None])      # 口数 最小深 最大深 最低井底
        a[0] += 1
        if d:
            a[1] = d if a[1] is None else min(a[1], d)
            a[2] = d if a[2] is None else max(a[2], d)
        if bot is not None:
            a[3] = bot if a[3] is None else min(a[3], bot)
        detail.append([mk, lab, f, t, L if L else u'', W if W else u'', round(d) if d else u'', int(step) if step else u'', _r(bot), _r(top), eid])
    if by:
        def _k(k):
            return (k[0], k[1], k[2], k[3] or 0, k[4] or 0, k[5] or 0)
        wrows = []
        for k in sorted(by, key=_k):
            n, dmin, dmax, bmin = by[k]
            wrows.append([k[0], k[1], k[2], k[3] or u'', k[4] or u'', int(k[5]) if k[5] else u'未取到深度', n,
                          round(dmin) if dmin else u'', round(dmax) if dmax else u'', _r(bmin)])
        tables.append((u'井汇总', [u'系列', u'族', u'类型', u'长(mm)', u'宽(mm)', u'深度档(mm)', u'数量', u'最小井深(mm)', u'最大井深(mm)', u'最低井底标高(m)'], wrows,
                       [u'合计', u'', u'', u'', u'', u'', len(res['wells']), u'', u'', u'']))
        tables.append((u'井明细', [u'井号', u'系列', u'族', u'类型', u'长(mm)', u'宽(mm)', u'井内深度(mm)', u'深度档(mm)', u'井底标高(m)', u'井盖面标高(m)', u'构件ID'], detail, None))
    if res['banks']:
        brows, tl, th, tv = [], 0.0, 0.0, 0.0
        for k in sorted(res['banks']):
            n, L, h, vol, lo, hi = res['banks'][k]
            brows.append([k[0], k[1], k[2], n, round(L, 2), round(L / n, 2) if n else u'', h if h else u'', round(L * h, 1) if h else u'',
                          round(vol, 3) if vol else u'', _r(lo), _r(hi)])
            tl += L
            th += L * h
            tv += vol
        tables.append((u'排管导管', [u'类别', u'族', u'类型', u'根数', u'总长度(m)', u'平均单根长(m)', u'孔数', u'孔·米', u'混凝土体积(m³)', u'最低底标高(m)', u'最高顶标高(m)'], brows,
                       [u'合计', u'', u'', sum(a[0] for a in res['banks'].values()), round(tl, 2), u'', u'', round(th, 1), round(tv, 3), u'', u'']))
    if res['piles']:
        prows = []
        for k, v in sorted(res['piles'].items()):
            m = _THICK.search(k[1])
            area = (v[1] / (int(m.group(1)) / 1000.0)) if (k[0] == u'垫层' and m and v[1]) else None
            prows.append([k[0], k[1], v[0], round(v[1], 3) if k[0] == u'垫层' else u'', _r(area, 1), round(v[2], 2) if k[0] == u'松木桩' else u'',
                          _r(v[3]), _r(v[4])])
        tables.append((u'桩与垫层', [u'项目', u'族 / 类型', u'数量', u'体积(m³)', u'面积(m²，体积÷厚度)', u'桩总长(m)', u'最低标高(m)', u'最高标高(m)'], prows, None))
    if res['excluded']:
        erows = [[k[0], k[1], k[2], v[0], round(v[1], 1), round(v[2], 1)] for k, v in sorted(res['excluded'].items())]
        tables.append((u'已排除', [u'类别', u'类型', u'排除原因', u'数量', u'体积(m³)', u'面积(m²)'], erows,
                       [u'合计', u'', u'', sum(v[0] for v in res['excluded'].values()), round(sum(v[1] for v in res['excluded'].values()), 1),
                        round(sum(v[2] for v in res['excluded'].values()), 1)]))
    summary = {'conc': conc_sum, 'steel': steel_sum, 'rebar': rebar_sum,
               'wells': len(res['wells']), 'banks': sum(a[0] for a in res['banks'].values()),
               'piles': sum(v[0] for k, v in res['piles'].items() if k[0] == u'松木桩'),
               'pads': sum(v[0] for k, v in res['piles'].items() if k[0] == u'垫层'),
               'excluded': sum(v[0] for v in res['excluded'].values())}
    return tables, summary


# ============================================================
# Excel（真 xlsx，标准库直接写，不需要装 Excel）
# ============================================================
def _write_xlsx(tables, path):
    """把所有标签页写成一个 xlsx，每页一个工作表。"""
    sheets = []
    for name, header, rows, total in tables:
        data = [list(header)] + [list(r) for r in rows] + ([list(total)] if total else [])
        widths = []
        for i in range(len(header)):
            w = max(len(_u(r[i])) if i < len(r) else 0 for r in data)
            widths.append(min(max(w * 1.6 + 2, 8), 50))
        sheets.append((name, data, {'widths': widths, 'header_rows': 1}))
    xlsx_write(path, sheets)
    return path


def _ask_save_path():
    """弹出「另存为」对话框让用户选路径。取消返回 None。"""
    dlg = WF.SaveFileDialog()
    dlg.Title = u'导出提量表格'
    dlg.Filter = u'Excel 表格 (*.xlsx)|*.xlsx'
    dlg.DefaultExt = u'xlsx'
    dlg.AddExtension = True
    dlg.FileName = u'提量_%s.xlsx' % time.strftime('%Y%m%d_%H%M')
    dlg.InitialDirectory = System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments)
    if dlg.ShowDialog() == WF.DialogResult.OK:
        return dlg.FileName
    return None


# ============================================================
# 明细表（Revit 里的明细表，随模型自动更新）
# ============================================================
def _existing_schedule_names():
    names = set()
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule).ToElements():
        try:
            names.add(_u(s.Name))
        except Exception:
            pass
    return names


def _has_elements(bic):
    try:
        return DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType().GetElementCount() > 0
    except Exception:
        return False


def _find_field(defn, names):
    """按候选名的先后顺序找字段（前面的优先）。"""
    by_name = {}
    for sf in defn.GetSchedulableFields():
        try:
            by_name.setdefault(_u(sf.GetName(doc)), sf)
        except Exception:
            continue
    for n in names:
        if n in by_name:
            return by_name[n]
    return None


def _make_schedules():
    """按 SCHEDULE_SPECS 建明细表（已经有同名的跳过）。返回 (新建的表名, 跳过的表名, 缺的字段说明)。"""
    existing = _existing_schedule_names()
    created = []
    skipped = []
    missing = []
    t = DB.Transaction(doc, u'生成提量明细表')
    t.Start()
    try:
        for name, bic, field_names in SCHEDULE_SPECS:
            if name in existing:
                skipped.append(name)
                continue
            if not _has_elements(bic):
                continue
            sch = DB.ViewSchedule.CreateSchedule(doc, DB.ElementId(bic))
            sch.Name = name
            defn = sch.Definition
            for names in field_names:
                sf = _find_field(defn, names)
                if sf is None:
                    missing.append(u'%s：找不到字段 %s' % (name, u'/'.join(names)))
                    continue
                defn.AddField(sf)
            created.append(name)
        t.Commit()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise ex
    return created, skipped, missing


# ============================================================
# 表格窗口
# ============================================================
def _grid(headers, rows, total):
    g = WF.DataGridView()
    g.Dock = WF.DockStyle.Fill
    g.ReadOnly = True
    g.AllowUserToAddRows = False
    g.RowHeadersVisible = False
    g.AutoSizeColumnsMode = WF.DataGridViewAutoSizeColumnsMode.AllCells
    for i, h in enumerate(headers):
        g.Columns.Add(u'c%d' % i, _u(h))
    for r in rows + ([total] if total else []):
        idx = g.Rows.Add()
        for i, v in enumerate(r):
            g.Rows[idx].Cells[i].Value = v if not isinstance(v, str) else _u(v)
    if total and g.Rows.Count:
        g.Rows[g.Rows.Count - 1].DefaultCellStyle.Font = SD.Font(g.Font, SD.FontStyle.Bold)
    return g


def _show_window(tables, summary, src_txt, res):
    form = WF.Form()
    form.Text = u'%s — %s' % (TITLE, src_txt)
    form.Width = 920
    form.Height = 580
    form.StartPosition = WF.FormStartPosition.CenterScreen

    tabs = WF.TabControl()
    tabs.Dock = WF.DockStyle.Fill
    for title, headers, rows, total in tables:
        page = WF.TabPage(title)
        page.Controls.Add(_grid(headers, rows, total))
        tabs.TabPages.Add(page)

    bottom = WF.Panel()
    bottom.Dock = WF.DockStyle.Bottom
    bottom.Height = 48

    def _button(text, left, handler):
        b = WF.Button()
        b.Text = text
        b.Left = left
        b.Top = 9
        b.Width = 150
        b.Height = 30
        b.Click += System.EventHandler(handler)
        bottom.Controls.Add(b)
        return b

    msg = []

    def _on_export(sender, args):
        try:
            path = _ask_save_path()
            if not path:
                return
            _write_xlsx(tables, path)
            forms.alert(u'已导出：\n' + path, title=TITLE)
        except Exception as ex:
            forms.alert(u'导出失败：\n%s' % ex, title=TITLE)

    def _on_schedule(sender, args):
        try:
            created, skipped, missing = _make_schedules()
            lines = []
            if created:
                lines.append(u'已新建明细表：\n  ' + u'\n  '.join(created))
            if skipped:
                lines.append(u'已存在（自动更新，不重复建）：\n  ' + u'\n  '.join(skipped))
            if not created and not skipped:
                lines.append(u'没有可建的明细表（对应类别里没有构件）。')
            if missing:
                lines.append(u'有字段没找到，已跳过：\n  ' + u'\n  '.join(missing))
            forms.alert(u'\n'.join(lines), title=TITLE)
        except Exception as ex:
            forms.alert(u'生成明细表失败，已回滚：\n%s' % ex, title=TITLE)

    _button(u'导出 Excel', 12, _on_export)
    _button(u'生成/更新明细表', 172, _on_schedule)
    _button(u'关闭', 332, lambda s, a: form.Close())

    form.Controls.Add(tabs)
    form.Controls.Add(bottom)
    ff_fit(form).ShowDialog()


def main():
    try:
        sel_ids = list(uidoc.Selection.GetElementIds())
        elems, src_txt = _pick_elements(sel_ids)
        if elems is None:
            return
        if not elems:
            forms.alert(u"没有找到可统计的构件（梁柱墙板基础、钢筋、井、排管、导管、松木桩、垫层）。", title=TITLE)
            return
        res = _quantities(elems)
        if res is None:
            return          # 用户取消了统计（只读，没有改模型）
        tables, summary = _table_rows(res)
        if res['skipped']:
            forms.alert(u'有 %d 个构件读取失败，已跳过。' % res['skipped'], title=TITLE)
        _show_window(tables, summary, u'%s（%d 个构件%s）' % (src_txt, len(elems), (u'，已排除场地楼板 %d 块' % summary['excluded']) if summary['excluded'] else u''), res)
    except Exception as e:
        forms.alert(u'提量过程发生异常：\n%s\n\n%s' % (e, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
