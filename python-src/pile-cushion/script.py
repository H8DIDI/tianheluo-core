# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "一键布置\n松木桩垫层"
__author__ = "KAIFA"
__doc__ = ("一键布置松木桩与垫层（天河落插件版）。\n"
           "框选了构件：只处理选中的；没有选中：按「规则」里的范围（默认整个项目）处理全部可布置构件。\n"
           "排管/管道等线式构件：沿实体走向布置 100mm 厚垫层（各边外挑）与松木桩；井/基础等点式构件：按实体外包布置。\n"
           "规则（垫层厚度、外挑、桩径、桩长、桩距、单排/双排阈值、构件类别、范围、底标高过滤）运行前可以修改并保存。\n"
           "已布置过的构件先清理旧垫层与旧桩再重新生成；每批在一个事务里提交，单个构件出错只回滚它自己。")

import math
import os
import json
import time
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from Autodesk.Revit import DB
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms

doc = revit.doc
uidoc = revit.uidoc

TITLE = "一键布置松木桩垫层"
MM_TO_FT = 1.0 / 304.8
FT_TO_MM = 304.8
APP_ID = "PinePileCushionPlugin"
WELL_PAD_FAMILY_NAME = "市政_井垫层"
RULES_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools")
RULES_PATH = os.path.join(RULES_DIR, "松木桩规则.json")
LOG_PATH = os.path.join(RULES_DIR, "松木桩日志.txt")

# 默认规则（与原版设计规范一致）
DEFAULT_PAD_THICK_MM = 100.0
DEFAULT_PAD_OVERHANG_MM = 100.0
DEFAULT_PILE_DIAM_MM = 75.0
DEFAULT_PILE_SPACING_MM = 500.0
DEFAULT_PILE_LEN_M = 6.0
DEFAULT_LINE_SINGLE_MAX_MM = 600.0
DEFAULT_LINE_DOUBLE_MAX_MM = 1200.0

# 运行时规则（apply_rules 设置；下面的函数直接读这些全局量）
PAD_THICK_MM = DEFAULT_PAD_THICK_MM
PAD_OVERHANG_MM = DEFAULT_PAD_OVERHANG_MM
PILE_DIAM_MM = DEFAULT_PILE_DIAM_MM
PILE_SPACING_MM = DEFAULT_PILE_SPACING_MM
PILE_LEN_M = DEFAULT_PILE_LEN_M
LINE_SINGLE_MAX_MM = DEFAULT_LINE_SINGLE_MAX_MM
LINE_DOUBLE_MAX_MM = DEFAULT_LINE_DOUBLE_MAX_MM
PILE_SYMBOL_NAME = "松木桩 - Φ75"
_TOP_DELTA = [None]


def get_elem_id_val(elem_or_id):
    """安全获取 64位/32位 ElementId 整数值"""
    if elem_or_id is None:
        return None
    eid = elem_or_id.Id if hasattr(elem_or_id, 'Id') else elem_or_id
    if hasattr(eid, 'Value'):
        return int(eid.Value)
    if hasattr(eid, 'IntegerValue'):
        return int(eid.IntegerValue)
    try:
        return int(eid)
    except Exception:
        return None


def get_elem_id_str(elem):
    """安全获取图元ID字符串"""
    v = get_elem_id_val(elem)
    return str(v) if v is not None else ""


def get_type_name(elem):
    """安全获取族类型名称"""
    if elem is None:
        return ""
    try:
        p = elem.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
        if p and p.HasValue:
            return p.AsString()
    except Exception:
        pass
    try:
        if hasattr(elem, 'Name'):
            return elem.Name
    except Exception:
        pass
    return ""


def get_fam_name(elem):
    """安全获取族名称"""
    if elem is None:
        return ""
    try:
        p = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_FAMILY_NAME)
        if p and p.HasValue:
            return p.AsString()
    except Exception:
        pass
    try:
        if hasattr(elem, 'Family') and elem.Family:
            return get_type_name(elem.Family)
    except Exception:
        pass
    try:
        if hasattr(elem, 'FamilyName'):
            return elem.FamilyName
    except Exception:
        pass
    return ""


def get_elem_name(elem):
    """安全获取图元名称"""
    if elem is None:
        return ""
    try:
        return revit.query.get_name(elem)
    except Exception:
        pass
    try:
        p = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        if p and p.HasValue:
            return p.AsString()
    except Exception:
        pass
    try:
        return elem.Name
    except Exception:
        return get_elem_id_str(elem)


# ============================================================
# 防重布置：建立已有垫层与松木桩索引
# ============================================================
def build_existing_foundation_index(target_doc, target_elements=None):
    """
    预先扫描已有松木桩与垫层，建立以 host_elem_id 为 key 的哈希索引。
    若指定 target_elements，使用空间包围盒精准过滤，从万级图元中瞬时完成检索（<50毫秒）！
    """
    index = {} # host_id_str -> list of ElementId
    
    bb_filter = None
    if target_elements:
        min_x = min_y = min_z = float('inf')
        max_x = max_y = max_z = float('-inf')
        valid_bb = False
        for el in target_elements:
            try:
                bb = el.get_BoundingBox(None)
                if bb:
                    valid_bb = True
                    min_x = min(min_x, bb.Min.X)
                    min_y = min(min_y, bb.Min.Y)
                    min_z = min(min_z, bb.Min.Z)
                    max_x = max(max_x, bb.Max.X)
                    max_y = max(max_y, bb.Max.Y)
                    max_z = max(max_z, bb.Max.Z)
            except Exception:
                pass
        if valid_bb:
            outline = DB.Outline(
                DB.XYZ(min_x - 10.0, min_y - 10.0, min_z - 30.0),
                DB.XYZ(max_x + 10.0, max_y + 10.0, max_z + 10.0)
            )
            bb_filter = DB.BoundingBoxIntersectsFilter(outline)

    # 1. 扫描松木桩与族垫层
    col_inst = DB.FilteredElementCollector(target_doc)\
        .OfClass(DB.FamilyInstance)\
        .OfCategory(DB.BuiltInCategory.OST_StructuralFoundation)
    if bb_filter:
        col_inst = col_inst.WherePasses(bb_filter)

    for inst in col_inst:
        try:
            comm = ""
            p = inst.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if p and p.HasValue:
                comm = p.AsString()
            
            host_id = None
            if comm and "Id:" in comm:
                try:
                    part = comm.split("Id:")[1]
                    host_id = part.split(")")[0].strip()
                except Exception:
                    pass
            
            if not host_id:
                p_mk = inst.LookupParameter("关联构件标记")
                if p_mk and p_mk.HasValue:
                    host_id = p_mk.AsString().strip()

            if host_id:
                if host_id not in index:
                    index[host_id] = []
                index[host_id].append(inst.Id)
        except Exception:
            pass

    # 2. 扫描 DirectShape 垫层
    col_ds = DB.FilteredElementCollector(target_doc)\
        .OfClass(DB.DirectShape)\
        .OfCategory(DB.BuiltInCategory.OST_StructuralFoundation)
    if bb_filter:
        col_ds = col_ds.WherePasses(bb_filter)

    for ds in col_ds:
        try:
            host_id = None
            app_data = ds.ApplicationDataId or ""
            if "Host_" in app_data:
                try:
                    host_id = app_data.split("Host_")[1].split("_")[0].strip()
                except Exception:
                    pass
            if not host_id:
                comm = ""
                p = ds.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                if p and p.HasValue:
                    comm = p.AsString()
                if comm and "Id:" in comm:
                    try:
                        part = comm.split("Id:")[1]
                        host_id = part.split(")")[0].strip()
                    except Exception:
                        pass
            if host_id:
                if host_id not in index:
                    index[host_id] = []
                index[host_id].append(ds.Id)
        except Exception:
            pass

    return index


# ============================================================
# 构件框选过滤器 (完全兼容 Revit 2024-2026 ElementId.Value)
# ============================================================


def ensure_pile_symbol(target_doc, pile_diam_mm=DEFAULT_PILE_DIAM_MM, pile_len_m=DEFAULT_PILE_LEN_M):
    """获取或自动创建松木桩族类型（Φ75mm, 6m长）"""
    col = DB.FilteredElementCollector(target_doc)\
        .OfClass(DB.FamilySymbol)\
        .OfCategory(DB.BuiltInCategory.OST_StructuralFoundation)
    
    # 查找已有类型
    for sym in col:
        if get_type_name(sym) == PILE_SYMBOL_NAME:
            if not sym.IsActive:
                sym.Activate()
            # 关键规范修复：确保类型参数【最小预埋件】设为0，使桩顶与垫层底面100%贴合，杜绝刺穿或间距！
            p_embed = sym.LookupParameter("最小预埋件")
            if p_embed and not p_embed.IsReadOnly:
                p_embed.Set(0.0)
            return sym

    # 如果未找到，尝试从已载入的圆形桩或钢管桩复制
    base_fams = ["桩 - 混凝土圆形桩", "M_桩 - 钢管", "桩-钢管"]
    base_fam = None
    for f in DB.FilteredElementCollector(target_doc).OfClass(DB.Family):
        if get_elem_name(f) in base_fams:
            base_fam = f
            break

    # 如果未载入，尝试从 Revit 标准库载入
    if base_fam is None:
        lib_paths = [
            r"C:\ProgramData\Autodesk\RVT 2026\Libraries\Chinese\结构\基础\桩 - 混凝土圆形桩.rfa",
            r"C:\ProgramData\Autodesk\RVT 2025\Libraries\Chinese\结构\基础\桩 - 混凝土圆形桩.rfa",
            r"C:\ProgramData\Autodesk\RVT 2024\Libraries\Chinese\结构\基础\桩 - 混凝土圆形桩.rfa",
        ]
        for lp in lib_paths:
            if System.IO.File.Exists(lp):
                try:
                    if clr:
                        loaded_ref = clr.Reference[DB.Family]()
                        if target_doc.LoadFamily(lp, loaded_ref):
                            base_fam = loaded_ref.Value
                            break
                    else:
                        target_doc.LoadFamily(lp)
                except Exception:
                    pass

    if base_fam is not None:
        sym_ids = list(base_fam.GetFamilySymbolIds())
        if sym_ids:
            first_sym = target_doc.GetElement(sym_ids[0])
            new_sym = first_sym.Duplicate(PILE_SYMBOL_NAME)
            
            d_ft = pile_diam_mm * MM_TO_FT
            p_diam = new_sym.LookupParameter("Diameter")
            if p_diam and not p_diam.IsReadOnly:
                p_diam.Set(d_ft)
            p_rad = new_sym.LookupParameter("半径")
            if p_rad and not p_rad.IsReadOnly:
                p_rad.Set(d_ft / 2.0)
            p_depth = new_sym.LookupParameter("Depth")
            if p_depth and not p_depth.IsReadOnly:
                p_depth.Set(pile_len_m / 0.3048)
            p_embed = new_sym.LookupParameter("最小预埋件")
            if p_embed and not p_embed.IsReadOnly:
                p_embed.Set(0.0)
                
            if not new_sym.IsActive:
                new_sym.Activate()
            return new_sym

    return None


def get_well_pad_symbol(target_doc, pad_thick_mm=DEFAULT_PAD_THICK_MM):
    """获取或创建指定厚度（100mm）的市政_井垫层族类型"""
    col = DB.FilteredElementCollector(target_doc)\
        .OfClass(DB.FamilySymbol)\
        .OfCategory(DB.BuiltInCategory.OST_StructuralFoundation)
    
    target_sym_name = "市政_井垫层 - {}mm".format(int(pad_thick_mm)) if pad_thick_mm != 200.0 else "市政_井垫层"

    for sym in col:
        if get_fam_name(sym) == WELL_PAD_FAMILY_NAME:
            if get_type_name(sym) == target_sym_name:
                if not sym.IsActive:
                    sym.Activate()
                return sym

    base_sym = None
    for sym in col:
        if get_fam_name(sym) == WELL_PAD_FAMILY_NAME:
            base_sym = sym
            break

    if base_sym is None:
        fam_paths = [
            r"\\pcb\佳佳工作\族文件\市政_井垫层.rfa",
            r"C:\Users\PCB\Documents\antigravity\jolly-bose\市政_井垫层.rfa"
        ]
        for fp in fam_paths:
            if System.IO.File.Exists(fp):
                try:
                    if clr:
                        loaded_ref = clr.Reference[DB.Family]()
                        if target_doc.LoadFamily(fp, loaded_ref):
                            sym_ids = list(loaded_ref.Value.GetFamilySymbolIds())
                            if sym_ids:
                                base_sym = target_doc.GetElement(sym_ids[0])
                                break
                    else:
                        target_doc.LoadFamily(fp)
                except Exception:
                    pass

    if base_sym is not None:
        if pad_thick_mm == 100.0 and get_type_name(base_sym) != target_sym_name:
            new_sym = base_sym.Duplicate(target_sym_name)
            p_thick = new_sym.LookupParameter("基础厚度")
            if p_thick and not p_thick.IsReadOnly:
                p_thick.Set(pad_thick_mm * MM_TO_FT)
            if not new_sym.IsActive:
                new_sym.Activate()
            return new_sym
        else:
            if not base_sym.IsActive:
                base_sym.Activate()
            return base_sym

    return None


def get_default_level(target_doc):
    """获取标高用于实例承载"""
    levels = DB.FilteredElementCollector(target_doc).OfClass(DB.Level).ToElements()
    for lv in levels:
        if abs(lv.Elevation) < 1e-4:
            return lv
    return levels[0] if levels else None


# ============================================================
# 几何提取核心算法 (实体级投影分析，解决偏心与错位)
# ============================================================
def extract_element_local_box(elem):
    """
    提取点式/箱体构件（如井/承台/独立基础）在局部坐标系下的真实实体几何尺寸和物理中心：
    返回: (len_ft, wid_ft, c_world, z_bot, rot, u_dir, v_dir)
    """
    rot = 0.0
    loc_pt = None
    if isinstance(elem.Location, DB.LocationPoint):
        loc_pt = elem.Location.Point
        rot = elem.Location.Rotation

    u_dir = DB.XYZ(math.cos(rot), math.sin(rot), 0)
    v_dir = DB.XYZ(-math.sin(rot), math.cos(rot), 0)

    bb = elem.get_BoundingBox(None)
    if loc_pt is None:
        if bb is not None:
            loc_pt = (bb.Min + bb.Max) * 0.5
        else:
            loc_pt = DB.XYZ(0, 0, 0)

    # 通过几何顶点求精准局部外包（使用 Coarse 粗略视图，耗时 < 1ms，避免细孔洞遍历卡顿，且防止族内连接件拉伸BBox导致中心严重偏移）
    opt = DB.Options()
    opt.DetailLevel = DB.ViewDetailLevel.Coarse
    geom = elem.get_Geometry(opt)

    bounds = {
        'min_u': float('inf'),
        'max_u': float('-inf'),
        'min_v': float('inf'),
        'max_v': float('-inf'),
        'min_z': float('inf')
    }

    def walk_geom(ge, tr):
        for obj in ge:
            if isinstance(obj, DB.GeometryInstance):
                walk_geom(obj.GetInstanceGeometry(), tr)
            elif isinstance(obj, DB.Solid) and obj.Volume > 1e-5:
                for e in obj.Edges:
                    for pt in e.Tessellate():
                        p = tr.OfPoint(pt) if tr else pt
                        d = p - loc_pt
                        u = d.DotProduct(u_dir)
                        v = d.DotProduct(v_dir)
                        if u < bounds['min_u']: bounds['min_u'] = u
                        if u > bounds['max_u']: bounds['max_u'] = u
                        if v < bounds['min_v']: bounds['min_v'] = v
                        if v > bounds['max_v']: bounds['max_v'] = v
                        if p.Z < bounds['min_z']: bounds['min_z'] = p.Z

    if geom is not None:
        walk_geom(geom, None)

    if bounds['min_u'] != float('inf') and bounds['max_u'] > bounds['min_u']:
        len_ft = bounds['max_u'] - bounds['min_u']
        wid_ft = bounds['max_v'] - bounds['min_v']
        c_u = (bounds['min_u'] + bounds['max_u']) * 0.5
        c_v = (bounds['min_v'] + bounds['max_v']) * 0.5
        c_world = DB.XYZ(loc_pt.X, loc_pt.Y, 0) + c_u * u_dir + c_v * v_dir
        z_bot = bounds['min_z']
        return len_ft, wid_ft, c_world, z_bot, rot, u_dir, v_dir

    # 降级：从 BoundingBox 读取
    if bb is not None:
        len_ft = bb.Max.X - bb.Min.X
        wid_ft = bb.Max.Y - bb.Min.Y
        c_world = (bb.Min + bb.Max) * 0.5
        z_bot = bb.Min.Z
        return len_ft, wid_ft, c_world, z_bot, 0.0, DB.XYZ(1, 0, 0), DB.XYZ(0, 1, 0)

    return None


def extract_curve_element_info(elem):
    """
    提取线式构件（排管/管沟/管道等）的走向矢量、截面物理宽度、偏心对齐以及起点/终点的底面绝对高程。
    优先通过构件真实实体几何投影（Coarse 粗略视图，耗时 < 1ms）提取 100% 精确的实体横向宽度、
    真实中心偏心 n_mid、纵向延伸起止以及底部坡度高程，彻底根除因族原点不在截面中心导致的严重横向偏移！
    返回: dict(p0, p1, dir_vec, perp_vec, elem_wid_ft, n_mid, t_start, t_end, z_start_bot, z_end_bot)
    """
    curve = None
    if isinstance(elem.Location, DB.LocationCurve):
        curve = elem.Location.Curve

    if curve is None or not isinstance(curve, DB.Line):
        return None

    p0 = curve.GetEndPoint(0)
    p1 = curve.GetEndPoint(1)
    line_vec = p1 - p0
    len_ft = line_vec.GetLength()
    if len_ft < 1e-4:
        return None

    dir_xy = DB.XYZ(line_vec.X, line_vec.Y, 0)
    if dir_xy.GetLength() < 1e-4:
        return None
    dir_vec = dir_xy.Normalize()
    perp_vec = DB.XYZ(-dir_vec.Y, dir_vec.X, 0).Normalize()
    slope = (p1.Z - p0.Z) / len_ft

    # 1. 核心首选：通过构件真实实体几何（Coarse 粗略视图，耗时 < 1ms）提取 100% 精确的实体偏心与尺寸
    opt = DB.Options()
    opt.DetailLevel = DB.ViewDetailLevel.Coarse
    geom = elem.get_Geometry(opt)

    bounds = {
        'min_u': float('inf'), 'max_u': float('-inf'),
        'min_v': float('inf'), 'max_v': float('-inf'),
        'min_dz': float('inf')
    }

    def walk_curve_geom(ge, tr):
        for obj in ge:
            if isinstance(obj, DB.GeometryInstance):
                walk_curve_geom(obj.GetInstanceGeometry(), tr)
            elif isinstance(obj, DB.Solid) and obj.Volume > 1e-5:
                for e in obj.Edges:
                    for pt in e.Tessellate():
                        p = tr.OfPoint(pt) if tr else pt
                        d = p - p0
                        u = d.X * dir_vec.X + d.Y * dir_vec.Y
                        v = d.X * perp_vec.X + d.Y * perp_vec.Y
                        z_curve = p0.Z + slope * u
                        dz = p.Z - z_curve
                        if u < bounds['min_u']: bounds['min_u'] = u
                        if u > bounds['max_u']: bounds['max_u'] = u
                        if v < bounds['min_v']: bounds['min_v'] = v
                        if v > bounds['max_v']: bounds['max_v'] = v
                        if dz < bounds['min_dz']: bounds['min_dz'] = dz

    if geom is not None:
        walk_curve_geom(geom, None)

    if bounds['min_v'] != float('inf') and bounds['max_v'] > bounds['min_v']:
        width_ft = bounds['max_v'] - bounds['min_v']
        n_mid = (bounds['min_v'] + bounds['max_v']) * 0.5
        t_start = bounds['min_u'] if bounds['min_u'] != float('inf') else 0.0
        t_end = bounds['max_u'] if bounds['max_u'] != float('-inf') else len_ft
        min_dz = bounds['min_dz'] if bounds['min_dz'] != float('inf') else 0.0
        z_start_bot = p0.Z + slope * t_start + min_dz
        z_end_bot = p0.Z + slope * t_end + min_dz
        return {
            'p0': p0,
            'p1': p1,
            'dir_vec': dir_vec,
            'perp_vec': perp_vec,
            'elem_wid_ft': width_ft,
            'n_mid': n_mid,
            't_start': t_start,
            't_end': t_end,
            'z_start_bot': z_start_bot,
            'z_end_bot': z_end_bot
        }

    # 2. 降级备用：参数与外包计算（仅在构件无有效三维实体时触发）
    cat_id_val = get_elem_id_val(elem.Category) if elem.Category else 0
    is_pipe = (cat_id_val == int(DB.BuiltInCategory.OST_PipeCurves))
    is_conduit = (cat_id_val == int(DB.BuiltInCategory.OST_Conduit))
    is_framing = (cat_id_val == int(DB.BuiltInCategory.OST_StructuralFraming))

    width_ft = 0.0
    height_ft = 0.0
    z_offset_bot = 0.0
    start_ext = 0.0
    end_ext = 0.0
    n_mid = 0.0

    if is_pipe:
        p_od = elem.get_Parameter(DB.BuiltInParameter.RBS_PIPE_OUTER_DIAMETER)
        if p_od and p_od.HasValue and p_od.AsDouble() > 1e-4:
            od = p_od.AsDouble()
        elif hasattr(elem, 'Diameter') and elem.Diameter > 1e-4:
            od = elem.Diameter
        else:
            od = 200.0 * MM_TO_FT
        width_ft = od
        height_ft = od
        z_offset_bot = -od / 2.0
        n_mid = 0.0

    elif is_conduit:
        p_od = elem.get_Parameter(DB.BuiltInParameter.RBS_CONDUIT_OUTER_DIAM_PARAM)
        if p_od and p_od.HasValue and p_od.AsDouble() > 1e-4:
            od = p_od.AsDouble()
        elif hasattr(elem, 'Diameter') and elem.Diameter > 1e-4:
            od = elem.Diameter
        else:
            od = 100.0 * MM_TO_FT
        width_ft = od
        height_ft = od
        z_offset_bot = -od / 2.0
        n_mid = 0.0

    elif is_framing:
        sym = elem.Symbol if hasattr(elem, 'Symbol') else None
        
        for name in ["包裹宽度", "宽度", "Width", "b"]:
            p = sym.LookupParameter(name) if sym else None
            if p and p.HasValue and p.AsDouble() > 1e-4:
                width_ft = p.AsDouble()
                break
            p_inst = elem.LookupParameter(name)
            if p_inst and p_inst.HasValue and p_inst.AsDouble() > 1e-4:
                width_ft = p_inst.AsDouble()
                break

        for name in ["包裹高度", "高度", "Height", "h"]:
            p = sym.LookupParameter(name) if sym else None
            if p and p.HasValue and p.AsDouble() > 1e-4:
                height_ft = p.AsDouble()
                break
            p_inst = elem.LookupParameter(name)
            if p_inst and p_inst.HasValue and p_inst.AsDouble() > 1e-4:
                height_ft = p_inst.AsDouble()
                break

        p_zjust = elem.get_Parameter(DB.BuiltInParameter.Z_JUSTIFICATION)
        z_just = p_zjust.AsValueString() if (p_zjust and p_zjust.HasValue) else "底"
        if z_just in ["顶", "Top"]:
            z_offset_bot = -height_ft
        elif z_just in ["中心", "Center"]:
            z_offset_bot = -height_ft / 2.0
        else:
            z_offset_bot = 0.0

        p_z_off = elem.get_Parameter(DB.BuiltInParameter.Z_OFFSET_VALUE)
        if p_z_off and p_z_off.HasValue:
            z_offset_bot += p_z_off.AsDouble()

        p_sext = elem.LookupParameter("开始延伸")
        if p_sext and p_sext.HasValue:
            start_ext = p_sext.AsDouble()
        p_eext = elem.LookupParameter("终点延伸")
        if p_eext and p_eext.HasValue:
            end_ext = p_eext.AsDouble()

        p_yjust = elem.get_Parameter(DB.BuiltInParameter.Y_JUSTIFICATION)
        y_just = p_yjust.AsValueString() if (p_yjust and p_yjust.HasValue) else "原点"
        p_y_off = elem.get_Parameter(DB.BuiltInParameter.Y_OFFSET_VALUE)
        y_off = p_y_off.AsDouble() if (p_y_off and p_y_off.HasValue) else 0.0
        if y_just in ["左", "Left"]:
            n_mid = -width_ft / 2.0 + y_off
        elif y_just in ["右", "Right"]:
            n_mid = width_ft / 2.0 + y_off
        else:
            n_mid = y_off

    if width_ft < 1e-4:
        width_ft = 1000.0 * MM_TO_FT
        n_mid = 0.0

    t_start = -start_ext
    t_end = len_ft + end_ext
    z_start_bot = p0.Z + z_offset_bot - slope * start_ext
    z_end_bot = p1.Z + z_offset_bot + slope * end_ext

    return {
        'p0': p0,
        'p1': p1,
        'dir_vec': dir_vec,
        'perp_vec': perp_vec,
        'elem_wid_ft': width_ft,
        'n_mid': n_mid,
        't_start': t_start,
        't_end': t_end,
        'z_start_bot': z_start_bot,
        'z_end_bot': z_end_bot
    }


# ============================================================
# 垫层生成函数 (DirectShape 3D 实体垫层)
# ============================================================
def create_sloped_directshape_pad(target_doc, p0, dir_vec, perp_vec, n_mid, t_start, t_end,
                                  z_start_bot, z_end_bot, elem_wid_ft, pad_overhang_ft, pad_thick_ft,
                                  host_elem_id_str, host_cat_name, name_prefix="排管垫层"):
    """
    生成紧贴线式构件（排管/管沟/管道）底面的三维倾斜实体垫层。
    无论管道水平还是倾斜，垫层顶面与管道底面100%贴合，四周各外挑100mm，厚度100mm。
    """
    t_span = t_end - t_start
    slope = (z_end_bot - z_start_bot) / t_span if t_span > 1e-4 else 0.0

    pad_wid_ft = elem_wid_ft + 2.0 * pad_overhang_ft

    # 外挑后的顶面起点与终点 (沿管道坡度自然延伸)
    p_start_top = DB.XYZ(p0.X, p0.Y, 0) + (t_start - pad_overhang_ft) * dir_vec + n_mid * perp_vec + DB.XYZ(0, 0, z_start_bot - slope * pad_overhang_ft)
    p_end_top = DB.XYZ(p0.X, p0.Y, 0) + (t_end + pad_overhang_ft) * dir_vec + n_mid * perp_vec + DB.XYZ(0, 0, z_end_bot + slope * pad_overhang_ft)

    # 纵向立面剖面四边形 (从侧向 -pad_wid/2 拉伸到 +pad_wid/2)
    side_offset = -perp_vec * (pad_wid_ft / 2.0)
    v1 = p_start_top + side_offset
    v2 = p_end_top + side_offset
    v3 = v2 - DB.XYZ(0, 0, pad_thick_ft)
    v4 = v1 - DB.XYZ(0, 0, pad_thick_ft)

    loop = DB.CurveLoop()
    loop.Append(DB.Line.CreateBound(v1, v2))
    loop.Append(DB.Line.CreateBound(v2, v3))
    loop.Append(DB.Line.CreateBound(v3, v4))
    loop.Append(DB.Line.CreateBound(v4, v1))

    solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(List[DB.CurveLoop]([loop]), perp_vec, pad_wid_ft)

    if solid is not None and solid.Volume > 1e-4:
        cat_id = DB.ElementId(DB.BuiltInCategory.OST_StructuralFoundation)
        ds = DB.DirectShape.CreateElement(target_doc, cat_id)
        ds.ApplicationId = "PinePileCushionPlugin"
        ds.ApplicationDataId = "Cushion_Host_" + host_elem_id_str + "_" + str(System.Guid.NewGuid())
        ds.SetShape(List[DB.GeometryObject]([solid]))
        ds.Name = name_prefix + u" ({}mm厚, 外挑{}mm)".format(int(PAD_THICK_MM), int(PAD_OVERHANG_MM))

        p_comm = ds.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p_comm and not p_comm.IsReadOnly:
            p_comm.Set(u"垫层 ({}mm厚, 外挑{}mm) | 宿主: {} (Id:{})".format(int(PAD_THICK_MM), int(PAD_OVERHANG_MM), host_cat_name, host_elem_id_str))

        return ds
    return None


def create_directshape_box_pad(target_doc, p_center, u_dir, v_dir, len_ft, wid_ft, z_top_ft, thick_ft, host_elem_id_str, host_cat_name, name_prefix="垫层"):
    """使用 DirectShape 生成指定物理中心、朝向和长宽厚的 3D 实体混凝土垫层（100mm厚，绑定宿主ID）"""
    half_l = len_ft / 2.0
    half_w = wid_ft / 2.0
    c_top = DB.XYZ(p_center.X, p_center.Y, z_top_ft)

    p_a = c_top - u_dir * half_l - v_dir * half_w
    p_b = c_top + u_dir * half_l - v_dir * half_w
    p_c = c_top + u_dir * half_l + v_dir * half_w
    p_d = c_top - u_dir * half_l + v_dir * half_w

    loop = DB.CurveLoop()
    loop.Append(DB.Line.CreateBound(p_a, p_b))
    loop.Append(DB.Line.CreateBound(p_b, p_c))
    loop.Append(DB.Line.CreateBound(p_c, p_d))
    loop.Append(DB.Line.CreateBound(p_d, p_a))

    z_down = DB.XYZ(0, 0, -1)
    solid = DB.GeometryCreationUtilities.CreateExtrusionGeometry(List[DB.CurveLoop]([loop]), z_down, thick_ft)

    if solid is not None and solid.Volume > 1e-4:
        cat_id = DB.ElementId(DB.BuiltInCategory.OST_StructuralFoundation)
        ds = DB.DirectShape.CreateElement(target_doc, cat_id)
        ds.ApplicationId = "PinePileCushionPlugin"
        ds.ApplicationDataId = "Cushion_Host_" + host_elem_id_str + "_" + str(System.Guid.NewGuid())
        ds.SetShape(List[DB.GeometryObject]([solid]))
        ds.Name = name_prefix + u" ({}mm厚, 外挑{}mm)".format(int(PAD_THICK_MM), int(PAD_OVERHANG_MM))

        p_comm = ds.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p_comm and not p_comm.IsReadOnly:
            p_comm.Set(u"垫层 ({}mm厚, 外挑{}mm) | 宿主: {} (Id:{})".format(int(PAD_THICK_MM), int(PAD_OVERHANG_MM), host_cat_name, host_elem_id_str))

        return ds
    return None


# ============================================================
# 松木桩布置核心算法 (严格物理对称与居中)
# ============================================================
def place_single_pile(target_doc, pos_xy, z_target_top_ft, pile_symbol, level, pile_len_ft, host_elem_id, host_category_name):
    """放置一根松木桩族实例，精确设定桩顶高程（垫层底面）与桩长，确保桩顶与垫层底无缝贴紧"""
    inst = target_doc.Create.NewFamilyInstance(
        DB.XYZ(pos_xy.X, pos_xy.Y, 0),
        pile_symbol,
        level,
        DB.Structure.StructuralType.Footing
    )
    
    # 标高偏移设定
    p_offset = inst.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
    if p_offset and not p_offset.IsReadOnly:
        p_offset.Set(z_target_top_ft - level.Elevation)

    # 桩长度（6.0m）
    p_len = inst.LookupParameter("桩长度")
    if p_len and not p_len.IsReadOnly:
        p_len.Set(pile_len_ft)
    else:
        p_depth = inst.LookupParameter("Depth")
        if p_depth and not p_depth.IsReadOnly:
            p_depth.Set(pile_len_ft)

    # 最小预埋件（0mm，若族实例上有此参数则清零）
    p_embed = inst.LookupParameter("最小预埋件")
    if p_embed and not p_embed.IsReadOnly:
        p_embed.Set(0.0)

    # 核心校准：消除族内部定义或基准面偏移，确保桩顶绝对贴齐垫层底
    try:
        if _TOP_DELTA[0] is None:
            top_param = inst.LookupParameter("顶部高程")
            if top_param and top_param.HasValue:
                _TOP_DELTA[0] = top_param.AsDouble() - z_target_top_ft
            else:
                _TOP_DELTA[0] = 0.0
        delta = _TOP_DELTA[0]
        if abs(delta) > 1e-4 and p_offset and not p_offset.IsReadOnly:
            p_offset.Set(p_offset.AsDouble() - delta)
    except Exception:
        pass

    # 关联宿主信息记录（用于防重覆盖索引）
    p_comments = inst.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    if p_comments and not p_comments.IsReadOnly:
        p_comments.Set(u"松木桩 Φ{}@{} L={}m | 宿主: {} (Id:{})".format(int(PILE_DIAM_MM), int(PILE_SPACING_MM), _fmt_num(PILE_LEN_M), host_category_name, host_elem_id))

    return inst


def layout_piles_for_curve(target_doc, p0, dir_vec, perp_vec, n_mid, t_start, t_end,
                           z_start_bot, z_end_bot, elem_wid_ft, pad_thick_ft,
                           spacing_ft, pile_symbol, level, pile_len_ft, host_elem_id, host_cat_name):
    """
    沿排管/管沟/管道中心线依其实际坡度连续布置松木桩。
    纵向间距500mm，横向居中自适应单排、双排、多排，每根桩顶严格贴合垫层底面。
    """
    t_span = max(0.0, t_end - t_start)
    n_l = max(1, int(round(t_span / spacing_ft))) if t_span > 0.2 else 1

    span_w = max(0.0, elem_wid_ft)
    w_threshold_1 = LINE_SINGLE_MAX_MM * MM_TO_FT
    w_threshold_2 = LINE_DOUBLE_MAX_MM * MM_TO_FT

    if span_w <= w_threshold_1:
        w_coords = [0.0]
    elif span_w <= w_threshold_2:
        w_coords = [-span_w / 2.0, span_w / 2.0]
    else:
        n_w = max(2, int(round(span_w / spacing_ft)))
        w_coords = [-span_w / 2.0 + j * (span_w / n_w) for j in range(n_w + 1)]

    created_piles = []
    for i in range(n_l + 1):
        ratio = float(i) / float(n_l) if n_l > 0 else 0.0
        t = t_start + ratio * t_span
        z_host_bot = z_start_bot + ratio * (z_end_bot - z_start_bot)
        z_cushion_bot = z_host_bot - pad_thick_ft

        for w_offset in w_coords:
            pt = DB.XYZ(p0.X, p0.Y, 0) + t * dir_vec + (n_mid + w_offset) * perp_vec
            pile_inst = place_single_pile(target_doc, pt, z_cushion_bot, pile_symbol, level, pile_len_ft, host_elem_id, host_cat_name)
            if pile_inst:
                created_piles.append(pile_inst)

    return created_piles


def layout_piles_for_box(target_doc, center_xy, u_dir, v_dir, len_ft, wid_ft, z_cushion_bot_ft,
                         spacing_ft, pile_symbol, level, pile_len_ft, host_elem_id, host_cat_name):
    """在矩形井/基础实体范围内，按中心间距 500mm 居中布置松木桩网格，桩顶精确贴合垫层底面"""
    span_u = max(0.0, len_ft)
    span_v = max(0.0, wid_ft)

    n_u = max(1, int(round(span_u / spacing_ft))) if span_u > 0.1 else 0
    n_v = max(1, int(round(span_v / spacing_ft))) if span_v > 0.1 else 0

    u_coords = [0.0] if n_u == 0 else [-span_u / 2.0 + i * (span_u / n_u) for i in range(n_u + 1)]
    v_coords = [0.0] if n_v == 0 else [-span_v / 2.0 + j * (span_v / n_v) for j in range(n_v + 1)]

    created_piles = []
    for u in u_coords:
        for v in v_coords:
            pt = DB.XYZ(center_xy.X, center_xy.Y, 0) + u * u_dir + v * v_dir
            pile_inst = place_single_pile(target_doc, pt, z_cushion_bot_ft, pile_symbol, level, pile_len_ft, host_elem_id, host_cat_name)
            if pile_inst:
                created_piles.append(pile_inst)

    return created_piles


# ============================================================


# ============================================================
# 规则：默认值 / 读写 / 应用
# ============================================================
DEFAULT_RULES = {
    'pad_thick_mm': DEFAULT_PAD_THICK_MM,
    'pad_overhang_mm': DEFAULT_PAD_OVERHANG_MM,
    'pile_diam_mm': DEFAULT_PILE_DIAM_MM,
    'pile_len_m': DEFAULT_PILE_LEN_M,
    'pile_spacing_mm': DEFAULT_PILE_SPACING_MM,
    'line_single_max_mm': DEFAULT_LINE_SINGLE_MAX_MM,
    'line_double_max_mm': DEFAULT_LINE_DOUBLE_MAX_MM,
    'max_bottom_m': '',
    'batch_size': 40,
    'scope': 'project',
    'overwrite_old': True,
    'categories': ['framing', 'generic', 'pipe', 'conduit', 'foundation', 'floor', 'wall', 'column'],
}

NUMBER_FIELDS = [
    ('pad_thick_mm', '垫层厚度 (mm)', 0, 2000),
    ('pad_overhang_mm', '垫层各边外挑 (mm)', 0, 2000),
    ('pile_diam_mm', '松木桩直径 (mm)', 10, 500),
    ('pile_len_m', '松木桩长度 (m)', 0.5, 30),
    ('pile_spacing_mm', '桩中心间距 (mm)', 50, 5000),
    ('line_single_max_mm', '线式构件宽 ≤ 此值 → 单排桩 (mm)', 0, 10000),
    ('line_double_max_mm', '线式构件宽 ≤ 此值 → 双排桩，更宽为多排 (mm)', 0, 20000),
    ('max_bottom_m', '只处理底标高 < 此值的构件 (m，留空=不限)', -100, 1000),
    ('batch_size', '每批处理构件数（越大越快，出错时回退越多）', 1, 500),
]

CAT_OPTIONS = [
    ('framing', '结构框架（排管/基础梁）', [DB.BuiltInCategory.OST_StructuralFraming]),
    ('generic', '常规模型（井/阀门井等）', [DB.BuiltInCategory.OST_GenericModel]),
    ('pipe', '管道（室外大管）', [DB.BuiltInCategory.OST_PipeCurves]),
    ('conduit', '电力导管', [DB.BuiltInCategory.OST_Conduit]),
    ('foundation', '结构基础（承台/独立基础）', [DB.BuiltInCategory.OST_StructuralFoundation]),
    ('floor', '楼板（基础底板）', [DB.BuiltInCategory.OST_Floors]),
    ('wall', '墙（挡土墙/沟壁）', [DB.BuiltInCategory.OST_Walls]),
    ('column', '结构柱（柱墩）', [DB.BuiltInCategory.OST_StructuralColumns]),
]


def _u(x):
    """把 .NET 字符串/任意对象转成 unicode，避免中文在 format 时报错。"""
    t = type(u'')
    if isinstance(x, t):
        return x
    try:
        return t(x)
    except Exception:
        try:
            return str(x).decode('utf-8', 'replace')
        except Exception:
            return t(repr(x))


def _err(ex):
    try:
        return _u(ex)
    except Exception:
        return u'未知错误'


def _fmt_num(v):
    try:
        f = float(v)
    except Exception:
        return u''
    if abs(f - round(f)) < 1e-9:
        return u'{}'.format(int(round(f)))
    return u'{:g}'.format(f)


def load_rules():
    r = dict(DEFAULT_RULES)
    r['categories'] = list(DEFAULT_RULES['categories'])
    try:
        if os.path.exists(RULES_PATH):
            with open(RULES_PATH, 'rb') as f:
                data = json.loads(f.read().decode('utf-8'))
            for k in DEFAULT_RULES:
                if k in data:
                    r[k] = data[k]
    except Exception:
        pass
    return r


def save_rules(r):
    try:
        if not os.path.exists(RULES_DIR):
            os.makedirs(RULES_DIR)
        s = json.dumps(r, ensure_ascii=True, indent=2, sort_keys=True)
        if not isinstance(s, bytes):
            s = s.encode('utf-8')
        with open(RULES_PATH, 'wb') as f:
            f.write(s)
        return True
    except Exception:
        return False


def apply_rules(r):
    global PAD_THICK_MM, PAD_OVERHANG_MM, PILE_DIAM_MM, PILE_SPACING_MM, PILE_LEN_M
    global LINE_SINGLE_MAX_MM, LINE_DOUBLE_MAX_MM, PILE_SYMBOL_NAME
    PAD_THICK_MM = float(r['pad_thick_mm'])
    PAD_OVERHANG_MM = float(r['pad_overhang_mm'])
    PILE_DIAM_MM = float(r['pile_diam_mm'])
    PILE_SPACING_MM = float(r['pile_spacing_mm'])
    PILE_LEN_M = float(r['pile_len_m'])
    LINE_SINGLE_MAX_MM = float(r['line_single_max_mm'])
    LINE_DOUBLE_MAX_MM = float(r['line_double_max_mm'])
    PILE_SYMBOL_NAME = u"松木桩 - Φ{}".format(int(PILE_DIAM_MM))
    _TOP_DELTA[0] = None


def _enabled_bics(r):
    out = []
    for key, text, bics in CAT_OPTIONS:
        if key in r.get('categories', []):
            out.extend(bics)
    return out


def _enabled_cat_ids(r):
    return set(int(b) for b in _enabled_bics(r))


def _max_bottom_ft(r):
    v = r.get('max_bottom_m', '')
    if v in ('', None):
        return None
    return float(v) / 0.3048


# ============================================================
# 规则窗口（运行前修改，保存后下次自动沿用）
# ============================================================
def _parse_num(text, name, lo, hi, allow_blank=False):
    t = _u(text or u'').strip()
    if t == u'':
        if allow_blank:
            return None
        raise ValueError(u'{} 不能为空'.format(name))
    try:
        v = float(t)
    except Exception:
        raise ValueError(u'{} 请输入数字'.format(name))
    if v < lo or v > hi:
        raise ValueError(u'{} 应在 {} 到 {} 之间'.format(name, lo, hi))
    return v


def show_rules_dialog(r, sel_count):
    """弹出规则窗口，返回修改后的规则 dict；取消返回 None。"""
    import System.Windows.Forms as WF
    import System.Drawing as SD
    form = WF.Form()
    form.Text = u"一键布置松木桩垫层 — 规则设置"
    form.ClientSize = SD.Size(640, 690)
    form.FormBorderStyle = WF.FormBorderStyle.FixedDialog
    form.MaximizeBox = False
    form.MinimizeBox = False
    form.StartPosition = WF.FormStartPosition.CenterScreen
    form.TopMost = True
    form.Font = SD.Font(u"Microsoft YaHei UI", 9.0)

    def label(text, x, y, w=330, h=22):
        l = WF.Label()
        l.Text = text
        l.Left = x
        l.Top = y
        l.Width = w
        l.Height = h
        form.Controls.Add(l)
        return l

    def button(text, x, y, w=120):
        b = WF.Button()
        b.Text = text
        b.Left = x
        b.Top = y
        b.Width = w
        b.Height = 32
        form.Controls.Add(b)
        return b

    if sel_count > 0:
        hint = u"已选中 {} 个构件：只处理选中的（清空选择后按下方范围处理全部）".format(sel_count)
    else:
        hint = u"未选中构件：按下方范围处理全部可布置构件"
    label(hint, 12, 10, 616, 24)

    boxes = {}
    y = 42
    for key, text, lo, hi in NUMBER_FIELDS:
        label(text, 12, y + 4, 370)
        tb = WF.TextBox()
        tb.Left = 390
        tb.Top = y
        tb.Width = 230
        tb.Text = _fmt_num(r.get(key, DEFAULT_RULES[key]))
        form.Controls.Add(tb)
        boxes[key] = tb
        y += 30

    label(u"处理范围", 12, y + 4, 370)
    scope_cb = WF.ComboBox()
    scope_cb.Left = 390
    scope_cb.Top = y
    scope_cb.Width = 230
    scope_cb.DropDownStyle = WF.ComboBoxStyle.DropDownList
    scope_cb.Items.Add(u"整个项目（全部构件）")
    scope_cb.Items.Add(u"当前视图")
    scope_cb.SelectedIndex = 1 if r.get('scope') == 'view' else 0
    form.Controls.Add(scope_cb)
    y += 34

    over_cb = WF.CheckBox()
    over_cb.Text = u"重复布置时先清理旧垫层与旧桩（推荐）"
    over_cb.Left = 12
    over_cb.Top = y
    over_cb.Width = 600
    over_cb.Checked = bool(r.get('overwrite_old', True))
    form.Controls.Add(over_cb)
    y += 30

    label(u"布置的构件类别（勾选）", 12, y + 4, 400)
    y += 28
    cat_boxes = {}
    enabled = set(r.get('categories', []))
    for i, (key, text, bics) in enumerate(CAT_OPTIONS):
        c = WF.CheckBox()
        c.Text = text
        c.Left = 24 + (i % 2) * 300
        c.Top = y + (i // 2) * 26
        c.Width = 290
        c.Checked = key in enabled
        form.Controls.Add(c)
        cat_boxes[key] = c
    y += ((len(CAT_OPTIONS) + 1) // 2) * 26 + 10

    label(u"提示：井、排管、管道等地上构件很多，建议在「底标高」里填一个值（如 0.5 m），只处理埋地的。",
          12, y, 616, 36)
    y += 44

    result = {}

    def _on_ok(sender, args):
        try:
            nr = {}
            for key, text, lo, hi in NUMBER_FIELDS:
                if key == 'batch_size':
                    nr[key] = int(_parse_num(boxes[key].Text, text, lo, hi))
                elif key == 'max_bottom_m':
                    v = _parse_num(boxes[key].Text, text, lo, hi, allow_blank=True)
                    nr[key] = '' if v is None else v
                else:
                    nr[key] = _parse_num(boxes[key].Text, text, lo, hi)
            if nr['line_double_max_mm'] < nr['line_single_max_mm']:
                raise ValueError(u'双排阈值应不小于单排阈值')
            nr['categories'] = [k for k, t, b in CAT_OPTIONS if cat_boxes[k].Checked]
            if not nr['categories']:
                raise ValueError(u'至少勾选一类构件')
            nr['scope'] = 'view' if scope_cb.SelectedIndex == 1 else 'project'
            nr['overwrite_old'] = bool(over_cb.Checked)
        except ValueError as ex:
            WF.MessageBox.Show(_err(ex.args[0]) if ex.args else u'输入有误', TITLE)
            return
        result['rules'] = nr
        form.DialogResult = WF.DialogResult.OK

    def _on_default(sender, args):
        for key, text, lo, hi in NUMBER_FIELDS:
            boxes[key].Text = _fmt_num(DEFAULT_RULES[key])
        scope_cb.SelectedIndex = 0
        over_cb.Checked = True
        for k, c in cat_boxes.items():
            c.Checked = k in DEFAULT_RULES['categories']

    btn_ok = button(u"开始布置", 300, y, 130)
    btn_def = button(u"恢复默认", 440, y, 110)
    btn_cancel = button(u"取消", 300, y + 40, 130)
    btn_ok.Click += System.EventHandler(_on_ok)
    btn_def.Click += System.EventHandler(_on_default)
    btn_cancel.DialogResult = WF.DialogResult.Cancel
    form.AcceptButton = btn_ok
    form.CancelButton = btn_cancel
    form.ClientSize = SD.Size(640, y + 90)

    if form.ShowDialog() == WF.DialogResult.OK and 'rules' in result:
        return result['rules']
    return None


# ============================================================
# 目标构件收集
# ============================================================
def is_tool_made(e):
    """本插件自己生成的垫层/桩（DirectShape 或带「宿主」标记的族实例）不再作为目标，避免重复布置。"""
    try:
        if isinstance(e, DB.DirectShape):
            return e.ApplicationId == APP_ID
        cm = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        comm = _u(cm.AsString()) if (cm and cm.HasValue) else u''
        if comm and (u'宿主' in comm) and (u'松木桩' in comm or u'垫层' in comm):
            return True
        sym = getattr(e, 'Symbol', None)
        if sym is not None and _u(get_type_name(sym)).startswith(u'松木桩'):
            return True
    except Exception:
        pass
    return False


def selected_targets(ids, r):
    keep = _enabled_cat_ids(r)
    out = []
    for eid in ids:
        try:
            e = doc.GetElement(eid)
            if e is None or e.Category is None:
                continue
            if get_elem_id_val(e.Category) not in keep:
                continue
            if is_tool_made(e):
                continue
            out.append(e)
        except Exception:
            continue
    return out


def collect_targets(r):
    bics = _enabled_bics(r)
    if not bics:
        return []
    flt = DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](bics))
    if r.get('scope') == 'view':
        col = DB.FilteredElementCollector(doc, uidoc.ActiveView.Id)
    else:
        col = DB.FilteredElementCollector(doc)
    max_bot = _max_bottom_ft(r)
    out = []
    for e in col.WherePasses(flt).WhereElementIsNotElementType().ToElements():
        try:
            if is_tool_made(e):
                continue
            if max_bot is not None:
                bb = e.get_BoundingBox(None)
                if bb is None or bb.Min.Z > max_bot:
                    continue
            out.append(e)
        except Exception:
            continue
    return out


# ============================================================
# 事务与分批
# ============================================================
class _SilentFailures(DB.IFailuresPreprocessor):
    """警告直接删除；无法忽略的错误不弹窗，让当前事务/子事务回滚（由上层隔离）。"""

    def PreprocessFailures(self, acc):
        has_err = False
        for f in acc.GetFailureMessages():
            try:
                sev = f.GetSeverity()
            except Exception:
                continue
            if sev == DB.FailureSeverity.Warning:
                try:
                    acc.DeleteWarning(f)
                except Exception:
                    pass
            else:
                has_err = True
        if has_err:
            return DB.FailureProcessingResult.ProceedWithRollBack
        return DB.FailureProcessingResult.Continue


def _make_tx(name):
    t = DB.Transaction(doc, name)
    fho = t.GetFailureHandlingOptions()
    fho.SetFailuresPreprocessor(_SilentFailures())
    fho.SetClearAfterRollback(True)
    t.SetFailureHandlingOptions(fho)
    return t


def prepare_ctx(r):
    """准备松木桩族、井垫层族和标高（一个小事务）。失败返回 None。"""
    t = _make_tx(u'准备松木桩族与标高')
    t.Start()
    try:
        pile_sym = ensure_pile_symbol(doc, PILE_DIAM_MM, PILE_LEN_M)
        well_pad_sym = get_well_pad_symbol(doc, PAD_THICK_MM)
        level = get_default_level(doc)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        return None
    if pile_sym is None or level is None:
        return None
    return {
        'pile_sym': pile_sym,
        'well_pad_sym': well_pad_sym,
        'level': level,
        'pad_thick_ft': PAD_THICK_MM * MM_TO_FT,
        'pad_overhang_ft': PAD_OVERHANG_MM * MM_TO_FT,
        'pile_spacing_ft': PILE_SPACING_MM * MM_TO_FT,
        'pile_len_ft': PILE_LEN_M / 0.3048,
    }


def process_one(elem, ctx, existing, r):
    """处理一个构件：先读几何（读不到就跳过，不动旧图元），再清旧图元、生成垫层和桩。"""
    host = get_elem_id_str(elem)
    cat_name = _u(elem.Category.Name) if elem.Category else u'构件'
    res = {'host': host, 'cat': cat_name, 'pads': 0, 'piles': 0, 'deleted': 0,
           'kind': u'', 'skip': None, 'error': None}

    curve_info = extract_curve_element_info(elem)
    box_info = None
    if curve_info is None:
        box_info = extract_element_local_box(elem)
        if box_info is None:
            res['skip'] = u'无法读取几何'
            return res

    old_ids = list(existing.get(host, [])) if r.get('overwrite_old', True) else []
    if old_ids:
        doc.Delete(List[DB.ElementId](old_ids))
        res['deleted'] = len(old_ids)

    pad_thick_ft = ctx['pad_thick_ft']
    pad_overhang_ft = ctx['pad_overhang_ft']
    pile_spacing_ft = ctx['pile_spacing_ft']
    pile_len_ft = ctx['pile_len_ft']
    pile_sym = ctx['pile_sym']
    level = ctx['level']

    if curve_info is not None:
        res['kind'] = u'线式'
        p0 = curve_info['p0']
        dir_vec = curve_info['dir_vec']
        perp_vec = curve_info['perp_vec']
        elem_wid_ft = curve_info['elem_wid_ft']
        n_mid = curve_info['n_mid']
        t_start = curve_info['t_start']
        t_end = curve_info['t_end']
        z_start_bot = curve_info['z_start_bot']
        z_end_bot = curve_info['z_end_bot']
        name_prefix = u"排管垫层" if (u"排管" in cat_name or u"排管" in _u(get_fam_name(elem))) else (u"管道垫层" if u"管" in cat_name else u"垫层")
        pad_ds = create_sloped_directshape_pad(
            doc, p0, dir_vec, perp_vec, n_mid, t_start, t_end,
            z_start_bot, z_end_bot, elem_wid_ft, pad_overhang_ft, pad_thick_ft,
            host, cat_name, name_prefix=name_prefix)
        if pad_ds:
            res['pads'] += 1
        piles = layout_piles_for_curve(
            doc, p0, dir_vec, perp_vec, n_mid, t_start, t_end,
            z_start_bot, z_end_bot, elem_wid_ft, pad_thick_ft,
            pile_spacing_ft, pile_sym, level, pile_len_ft, host, cat_name)
        res['piles'] += len(piles)
        return res

    res['kind'] = u'点式'
    len_ft, wid_ft, c_world, z_bot, rot, u_dir, v_dir = box_info
    pad_len_ft = len_ft + 2.0 * pad_overhang_ft
    pad_wid_ft = wid_ft + 2.0 * pad_overhang_ft
    z_cushion_bot = z_bot - pad_thick_ft
    well_pad_sym = ctx['well_pad_sym']
    is_well = (u"井" in cat_name) or (hasattr(elem, 'Symbol') and elem.Symbol and (u"井" in _u(get_fam_name(elem.Symbol))))
    placed_family_pad = False
    if well_pad_sym is not None and is_well and abs(PAD_THICK_MM - DEFAULT_PAD_THICK_MM) < 1e-6:
        try:
            pad_inst = doc.Create.NewFamilyInstance(
                DB.XYZ(c_world.X, c_world.Y, 0), well_pad_sym, level,
                DB.Structure.StructuralType.Footing)
            p_off = pad_inst.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
            if p_off and not p_off.IsReadOnly:
                p_off.Set(z_bot - level.Elevation)
            p_l = pad_inst.LookupParameter("井外包长")
            if p_l and not p_l.IsReadOnly:
                p_l.Set(len_ft)
            p_w = pad_inst.LookupParameter("井外包宽")
            if p_w and not p_w.IsReadOnly:
                p_w.Set(wid_ft)
            p_spec = pad_inst.LookupParameter("松木桩规格")
            if p_spec and not p_spec.IsReadOnly:
                p_spec.Set(u"{}mm Ø".format(int(PILE_DIAM_MM)))
            p_spc = pad_inst.LookupParameter("松木桩间距")
            if p_spc and not p_spc.IsReadOnly:
                p_spc.Set(ctx['pile_spacing_ft'])
            p_plen = pad_inst.LookupParameter("松木桩长")
            if p_plen and not p_plen.IsReadOnly:
                p_plen.Set(pile_len_ft)
            p_mk = pad_inst.LookupParameter("关联构件标记")
            if p_mk and not p_mk.IsReadOnly:
                p_mk.Set(host)
            p_comm = pad_inst.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            if p_comm and not p_comm.IsReadOnly:
                p_comm.Set(u"井垫层 (100mm厚) | 宿主: {} (Id:{})".format(cat_name, host))
            if abs(rot) > 1e-4:
                axis = DB.Line.CreateBound(DB.XYZ(c_world.X, c_world.Y, 0), DB.XYZ(c_world.X, c_world.Y, 1))
                DB.ElementTransformUtils.RotateElement(doc, pad_inst.Id, axis, rot)
            res['pads'] += 1
            placed_family_pad = True
        except Exception:
            placed_family_pad = False
    if not placed_family_pad:
        pad_ds = create_directshape_box_pad(
            doc, c_world, u_dir, v_dir, pad_len_ft, pad_wid_ft, z_bot, pad_thick_ft,
            host, cat_name, name_prefix=u"井垫层")
        if pad_ds:
            res['pads'] += 1
    piles = layout_piles_for_box(
        doc, c_world, u_dir, v_dir, len_ft, wid_ft, z_cushion_bot,
        pile_spacing_ft, pile_sym, level, pile_len_ft, host, cat_name)
    res['piles'] += len(piles)
    return res


def _run_sub(elem, ctx, existing, r):
    """单个构件放在子事务里：出错只回滚它自己，不影响同批的其他构件。"""
    st = DB.SubTransaction(doc)
    st.Start()
    try:
        res = process_one(elem, ctx, existing, r)
        s = st.Commit()
        if s is not None and s != DB.TransactionStatus.Committed:
            raise Exception(u'子事务未提交')
        return res
    except Exception as ex:
        try:
            if st.HasStarted() and not st.HasEnded():
                st.RollBack()
        except Exception:
            pass
        return {'host': get_elem_id_str(elem), 'cat': _u(elem.Category.Name) if elem.Category else u'构件',
                'error': _err(ex), 'pads': 0, 'piles': 0, 'deleted': 0}


def run_batch(elems, ctx, existing, r):
    """一批构件放在一个事务里提交。整批提交失败返回 None，由调用方逐个重试。"""
    t = _make_tx(u'一键布置松木桩与垫层')
    t.Start()
    out = []
    try:
        for el in elems:
            out.append((el, _run_sub(el, ctx, existing, r)))
        s = t.Commit()
        if s != DB.TransactionStatus.Committed:
            return None
        return out
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        return None


def _account(stats, el, res, existing):
    host = res.get('host') or get_elem_id_str(el)
    cat = res.get('cat') or u'构件'
    if res.get('error'):
        stats['failed'].append((host, cat, res['error']))
        return
    if res.get('skip'):
        stats['skipped'].append((host, cat, res['skip']))
        return
    stats['done'] += 1
    stats['pads'] += res.get('pads', 0)
    stats['piles'] += res.get('piles', 0)
    stats['deleted'] += res.get('deleted', 0)
    if res.get('kind') == u'线式':
        stats['line'] += 1
    else:
        stats['point'] += 1
    if res.get('deleted', 0):
        existing.pop(host, None)


def run_pipeline(r, targets, pb=None):
    """主流程：准备族 → 建旧图元索引 → 分批提交（失败的批逐个重试）→ 统计。"""
    t0 = time.time()
    apply_rules(r)
    stats = {'total': len(targets), 'done': 0, 'pads': 0, 'piles': 0, 'deleted': 0,
             'line': 0, 'point': 0, 'skipped': [], 'failed': [], 'cancelled': False,
             'error': None, 'seconds': 0.0}
    ctx = prepare_ctx(r)
    if ctx is None:
        stats['error'] = u'未能加载或创建松木桩族/标高（请检查项目基础族）'
        return stats
    existing = build_existing_foundation_index(doc, targets)
    bs = max(1, int(r.get('batch_size', 40)))
    n = len(targets)
    i = 0
    while i < n:
        if pb is not None and pb.cancelled:
            stats['cancelled'] = True
            break
        batch = targets[i:i + bs]
        out = run_batch(batch, ctx, existing, r)
        if out is None:
            out = []
            for el in batch:
                one = run_batch([el], ctx, existing, r)
                if one is None:
                    out.append((el, {'host': get_elem_id_str(el),
                                     'cat': _u(el.Category.Name) if el.Category else u'构件',
                                     'error': u'Revit 提交失败（已回滚，该构件跳过）'}))
                else:
                    out.extend(one)
        for el, res in out:
            _account(stats, el, res, existing)
        i += len(batch)
        if pb is not None:
            try:
                pb.update_progress(min(i, n), n)
            except Exception:
                pass
    stats['seconds'] = time.time() - t0
    return stats


def write_log(stats):
    if not (stats['failed'] or stats['skipped'] or stats.get('error')):
        return None
    rows = [u'==== {} 运行（构件 {} 个）===='.format(time.strftime('%Y-%m-%d %H:%M:%S'), stats['total'])]
    if stats.get('error'):
        rows.append(u'错误: ' + _u(stats['error']))
    for host, cat, msg in stats['skipped']:
        rows.append(u'跳过 {} [{}] {}'.format(host, cat, msg))
    for host, cat, msg in stats['failed']:
        rows.append(u'失败 {} [{}] {}'.format(host, cat, msg))
    try:
        if not os.path.exists(RULES_DIR):
            os.makedirs(RULES_DIR)
        with open(LOG_PATH, 'ab') as f:
            f.write((u'\n'.join(rows) + u'\n').encode('utf-8'))
        return LOG_PATH
    except Exception:
        return None


def build_report(stats, r, log_path):
    d = float(r['pile_diam_mm'])
    piles = stats['piles']
    pile_len = float(r['pile_len_m'])
    vol = piles * math.pi * (d / 2000.0) ** 2 * pile_len
    lines = [
        u"松木桩与垫层布置完成！" if not stats['cancelled'] else u"已取消（已完成的部分保留）。",
        u"",
        u"• 处理构件：{} / {} 个（线式 {}，点式 {}）".format(stats['done'], stats['total'], stats['line'], stats['point']),
        u"• 生成垫层：{} 块".format(stats['pads']),
        u"• 生成松木桩：{} 根（Φ{} × {} m，总延米 {:.2f} m，木材 {:.2f} m³）".format(
            piles, _fmt_num(d), _fmt_num(pile_len), piles * pile_len, vol),
        u"• 清理旧图元：{} 个".format(stats['deleted']),
        u"• 跳过：{} 个（读不到几何等）".format(len(stats['skipped'])),
        u"• 失败：{} 个（已回滚，不影响其它构件）".format(len(stats['failed'])),
        u"• 用时：{:.1f} 秒".format(stats['seconds']),
    ]
    if stats.get('error'):
        lines.append(u"• 错误：" + _u(stats['error']))
    if log_path:
        lines.append(u"• 跳过/失败明细已写入：" + _u(log_path))
    return u"\n".join(lines)


# ============================================================
# 一键隐藏 / 显示：用视图过滤器隐藏所有视图里的松木桩和垫层（不改图元本身）
# 过滤器条件：基础构件类别 + 备注包含「宿主」（本插件生成的垫层、桩都带这个标记）
# ============================================================
FILTER_NAME = u"KAIFA_松木桩与垫层(隐藏)"
HIDE_KEYWORD = u"宿主"
OP_LAYOUT = u"布置松木桩与垫层"
OP_HIDE = u"一键隐藏所有视图的松木桩和垫层"
OP_SHOW = u"一键显示所有视图的松木桩和垫层（取消隐藏）"
_SKIP_VIEW_TYPES = ('Legend', 'ProjectBrowser', 'SystemBrowser', 'Internal', 'Report', 'DrawingSheet', 'Schedule')


def _find_filter():
    for f in DB.FilteredElementCollector(doc).OfClass(DB.ParameterFilterElement).ToElements():
        try:
            if _u(f.Name) == FILTER_NAME:
                return f
        except Exception:
            continue
    return None


def _ensure_filter():
    f = _find_filter()
    if f is not None:
        return f
    cats = List[DB.ElementId]([DB.ElementId(DB.BuiltInCategory.OST_StructuralFoundation)])
    rule = DB.ParameterFilterRuleFactory.CreateContainsRule(
        DB.ElementId(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS), HIDE_KEYWORD)
    ef = DB.ElementParameterFilter(rule)
    return DB.ParameterFilterElement.Create(doc, FILTER_NAME, cats, ef)


def _views_for_filter():
    out = []
    for v in DB.FilteredElementCollector(doc).OfClass(DB.View).ToElements():
        try:
            if v.IsTemplate or isinstance(v, DB.ViewSheet) or isinstance(v, DB.ViewSchedule):
                continue
            if str(v.ViewType) in _SKIP_VIEW_TYPES:
                continue
            out.append(v)
        except Exception:
            continue
    return out


def set_pile_filter_hidden(hide):
    """hide=True：在所有视图上加过滤器并隐藏匹配的松木桩/垫层；hide=False：移除过滤器，恢复显示。
    返回 (处理的视图数, 失败的视图名列表)。整次操作一个事务，单个视图失败不影响其它视图。"""
    done = 0
    fails = []
    t = _make_tx(u'松木桩与垫层：隐藏/显示')
    t.Start()
    try:
        pf = _ensure_filter() if hide else _find_filter()
        if pf is None:
            t.Commit()
            return 0, []
        for v in _views_for_filter():
            try:
                if hide:
                    if not v.IsFilterApplied(pf.Id):
                        v.AddFilter(pf.Id)
                    v.SetFilterVisibility(pf.Id, False)
                else:
                    if v.IsFilterApplied(pf.Id):
                        v.RemoveFilter(pf.Id)
                done += 1
            except Exception:
                fails.append(_u(v.Name))
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return done, fails


def _layout_flow():
    try:
        r = load_rules()
        sel_ids = []
        try:
            sel_ids = list(uidoc.Selection.GetElementIds())
        except Exception:
            sel_ids = []
        sel_now = selected_targets(sel_ids, r)

        new_r = show_rules_dialog(r, len(sel_now))
        if new_r is None:
            return
        save_rules(new_r)

        if sel_now:
            targets = selected_targets(sel_ids, new_r)
            src_txt = u"选中的"
        else:
            targets = collect_targets(new_r)
            src_txt = u"整个项目" if new_r['scope'] == 'project' else u"当前视图"
        if not targets:
            forms.alert(u"没有找到可布置的构件。\n请检查「规则」里的类别、范围和底标高过滤。", title=TITLE)
            return

        q = (u"将处理 {} 个构件（{}）。\n"
             u"垫层 {} mm 厚，各边外挑 {} mm；松木桩 Φ{} × {} m，间距 {} mm。\n"
             u"开始布置？").format(len(targets), src_txt,
                                   _fmt_num(new_r['pad_thick_mm']), _fmt_num(new_r['pad_overhang_mm']),
                                   _fmt_num(new_r['pile_diam_mm']), _fmt_num(new_r['pile_len_m']),
                                   _fmt_num(new_r['pile_spacing_mm']))
        if not forms.alert(q, title=TITLE, yes=True, no=True):
            return

        with forms.ProgressBar(title=u"正在布置松木桩与垫层...", cancellable=True) as pb:
            stats = run_pipeline(new_r, targets, pb)
        log_path = write_log(stats)
        forms.alert(build_report(stats, new_r, log_path), title=TITLE)
    except Exception as e:
        forms.alert(u"布置过程发生异常（已尽量保留已完成的部分）：\n" + _err(e) + u"\n\n" + _u(traceback.format_exc()),
                    title=TITLE)



def main():
    try:
        op = forms.CommandSwitchWindow.show([OP_LAYOUT, OP_HIDE, OP_SHOW], message=u'请选择操作：')
        if not op:
            return
        if op == OP_LAYOUT:
            _layout_flow()
            return
        hide = (op == OP_HIDE)
        n, fails = set_pile_filter_hidden(hide)
        lines = [u'已在 {} 个视图上{}松木桩与垫层（过滤器「{}」）。'.format(n, u'隐藏' if hide else u'恢复显示', FILTER_NAME)]
        if hide:
            lines.append(u'注意：之后新建的视图不会自动隐藏，需要再点一次「一键隐藏」。')
        if fails:
            lines.append(u'以下 {} 个视图无法加过滤器（可能由视图样板控制），需手动处理：'.format(len(fails)))
            lines.extend([u'  - ' + f for f in fails[:15]])
        forms.alert(u'\n'.join(lines), title=TITLE)
    except Exception as e:
        forms.alert(u'操作异常：\n' + _err(e) + u'\n\n' + _u(traceback.format_exc()), title=TITLE)

if __name__ == '__main__':
    main()
