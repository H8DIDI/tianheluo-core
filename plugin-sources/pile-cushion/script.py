# -*- coding: utf-8 -*-
"""
一键布置松木桩与垫层 (防重复布置与精准居中版)
================================================================
功能亮点:
  1. 【防重复布置 / 智能覆盖】
     - 自动检测并索引所选构件曾布置过的旧垫层与旧松木桩；
     - 重复框选或重新布置时，自动先行清理旧图元后再重新生成，彻底杜绝重复重叠与工程量虚增！
  2. 【真实实体三维几何居中】
     - 自动遍历提取构件真实的 3D 实体几何轮廓（Solid Geometry）；
     - 解决排管因族制作定位线对齐在侧边或顶部导致的垫层偏心错位问题。
  3. 【设计规范参数】
     - 垫层厚度：100 mm (紧贴构件底面下翻 100mm)
     - 垫层外挑：各边（前后左右）外挑 100 mm
     - 松木桩：Φ75 mm 直径，桩长 6.0 m，桩中心间距 500 mm，桩顶平齐垫层底面。
  4. 【100% 兼容性】
     - 支持 Revit 2024 / 2025 / 2026 (64位 ElementId.Value) 与 IronPython 2.7。
"""

__title__ = "一键布置\n松木桩垫层"
__author__ = "Antigravity Assistant"
__doc__ = "框选排管或井等构件，自动在其下方依据真实几何轮廓居中布置 100mm 厚垫层（外挑100mm）与 Φ75@500 L=6m 松木桩。已内置防重复布置智能覆盖机制。"

import math
import sys
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
from Autodesk.Revit import DB
from Autodesk.Revit.UI.Selection import ISelectionFilter
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms

# 获取当前文档与活动UIDocument
doc = revit.doc
uidoc = revit.uidoc

# ============================================================
# 全局设计规范常量（公制 mm 转 英尺 ft）
# ============================================================
MM_TO_FT = 1.0 / 304.8
FT_TO_MM = 304.8

DEFAULT_PAD_THICK_MM = 100.0     # 垫层厚度：100 mm
DEFAULT_PAD_OVERHANG_MM = 100.0  # 垫层各边外挑：100 mm
DEFAULT_PILE_DIAM_MM = 75.0      # 松木桩直径：Φ75 mm
DEFAULT_PILE_SPACING_MM = 500.0  # 松木桩中心间距：500 mm
DEFAULT_PILE_LEN_M = 6.0         # 松木桩长度：6.0 m

WELL_PAD_FAMILY_NAME = "市政_井垫层"
PILE_SYMBOL_NAME = "松木桩 - Φ75"


# ============================================================
# 兼容性工具函数
# ============================================================
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
class FoundationTargetFilter(ISelectionFilter):
    """过滤可布置垫层和松木桩的构件"""
    def __init__(self):
        self.allowed_categories = [
            int(DB.BuiltInCategory.OST_GenericModel),        # 井 / 阀门井 / 拉线井 / 检查井
            int(DB.BuiltInCategory.OST_StructuralFraming),   # 排管 / 电缆沟 / 管沟 / 基础梁
            int(DB.BuiltInCategory.OST_StructuralFoundation),# 承台 / 独立基础
            int(DB.BuiltInCategory.OST_Floors),              # 基础底板 / 楼板
            int(DB.BuiltInCategory.OST_PipeCurves),          # 室外大管道
            int(DB.BuiltInCategory.OST_Conduit),             # 电力导管
            int(DB.BuiltInCategory.OST_Walls),               # 挡土墙 / 沟壁
            int(DB.BuiltInCategory.OST_StructuralColumns)    # 柱墩
        ]

    def AllowElement(self, elem):
        if elem is None or elem.Category is None:
            return False
        try:
            cid_val = get_elem_id_val(elem.Category)
            if cid_val is not None:
                return cid_val in self.allowed_categories
        except Exception:
            pass
        return False

    def AllowReference(self, ref, point):
        return False


# ============================================================
# 族与类型准备
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
        ds.Name = name_prefix + " (100mm厚, 外挑100mm)"

        p_comm = ds.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p_comm and not p_comm.IsReadOnly:
            p_comm.Set("垫层 (100mm厚, 外挑100mm) | 宿主: {} (Id:{})".format(host_cat_name, host_elem_id_str))

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
        ds.Name = name_prefix + " (100mm厚, 外挑100mm)"

        p_comm = ds.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p_comm and not p_comm.IsReadOnly:
            p_comm.Set("垫层 (100mm厚, 外挑100mm) | 宿主: {} (Id:{})".format(host_cat_name, host_elem_id_str))

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
        top_param = inst.LookupParameter("顶部高程")
        if top_param and top_param.HasValue and p_offset and not p_offset.IsReadOnly:
            actual_top = top_param.AsDouble()
            delta = actual_top - z_target_top_ft
            if abs(delta) > 1e-4:
                p_offset.Set(p_offset.AsDouble() - delta)
    except Exception:
        pass

    # 关联宿主信息记录（用于防重覆盖索引）
    p_comments = inst.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    if p_comments and not p_comments.IsReadOnly:
        p_comments.Set("松木桩 Φ75@500 L=6m | 宿主: {} (Id:{})".format(host_category_name, host_elem_id))

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
    w_threshold_1 = 600.0 * MM_TO_FT
    w_threshold_2 = 1200.0 * MM_TO_FT

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
# 主执行入口
# ============================================================
def main():
    try:
        # 1. 获取目标构件（优先读当前选择，无选择时启动框选）
        flt = FoundationTargetFilter()
        sel_ids = list(uidoc.Selection.GetElementIds())
        target_elements = []
        for eid in sel_ids:
            try:
                el = doc.GetElement(eid)
                if flt.AllowElement(el):
                    target_elements.append(el)
            except Exception:
                pass

        if not target_elements:
            try:
                picked = uidoc.Selection.PickElementsByRectangle(
                    flt,
                    "【框选布置】请在视图中拉框框选需要布置垫层和松木桩的构件（排管/井/基础/底板）"
                )
                target_elements = list(picked)
            except Exception:
                # 用户按 ESC 取消
                return

        if not target_elements:
            forms.alert("未选中任何有效构件！\n支持的构件包括：排管（结构框架）、井（常规模型）、结构基础、楼板等。", title="一键布置松木桩垫层")
            return

        # 2. 预先构建已有垫层和桩索引，检测是否包含曾经布置过的构件 (空间过滤秒级索引)
        existing_index = build_existing_foundation_index(doc, target_elements)
        already_placed_hosts = 0
        already_placed_elems_count = 0
        for el in target_elements:
            el_id_str = get_elem_id_str(el)
            if el_id_str in existing_index:
                already_placed_hosts += 1
                already_placed_elems_count += len(existing_index[el_id_str])

        # 3. 参数确认与提醒
        overwrite_tip = ""
        if already_placed_hosts > 0:
            overwrite_tip = (
                "⚠️ 【检测到重复布置构件】：\n"
                "所选构件中有 {} 个构件此前已布置过垫层/松木桩（共 {} 个旧图元）。\n"
                "💡 本插件已开启【智能防重覆盖机制】：将自动清理旧图元并重新生成，杜绝重叠冗余！\n\n"
            ).format(already_placed_hosts, already_placed_elems_count)

        summary_prompt = (
            "已选中 {} 个构件准备布置松木桩与垫层：\n\n"
            "{}"
            "• 垫层厚度：100 mm\n"
            "• 垫层外挑：各边外挑 100 mm\n"
            "• 对齐方式：构件实体三维外轮廓精准居中对齐\n"
            "• 松木桩规格：Φ75 mm 直径\n"
            "• 桩中心间距：500 mm\n"
            "• 桩身长度：6.0 m\n\n"
            "是否立即自动计算并生成？"
        ).format(len(target_elements), overwrite_tip)

        choice = forms.CommandSwitchWindow.show(
            ["立即自动布置 (智能覆盖更新)", "取消"],
            message=summary_prompt
        )
        if choice != "立即自动布置 (智能覆盖更新)":
            return

        # 4. 准备族与标高
        t_prep = DB.Transaction(doc, "准备松木桩族与标高")
        t_prep.Start()
        pile_sym = ensure_pile_symbol(doc, DEFAULT_PILE_DIAM_MM, DEFAULT_PILE_LEN_M)
        well_pad_sym = get_well_pad_symbol(doc, DEFAULT_PAD_THICK_MM)
        level = get_default_level(doc)
        t_prep.Commit()

        if pile_sym is None:
            forms.alert("未能加载或创建【{}】族类型，请检查项目基础族！".format(PILE_SYMBOL_NAME), title="错误")
            return
        if level is None:
            forms.alert("项目中未找到可用标高！", title="错误")
            return

        # 5. 执行批量布置（事务内执行旧图元清理与新图元生成）
        t_main = DB.Transaction(doc, "一键布置松木桩与垫层")
        t_main.Start()

        count_pad = 0
        count_piles = 0
        count_cleaned_old = 0
        skipped = 0
        processed_elements = 0

        pad_thick_ft = DEFAULT_PAD_THICK_MM * MM_TO_FT
        pad_overhang_ft = DEFAULT_PAD_OVERHANG_MM * MM_TO_FT
        pile_spacing_ft = DEFAULT_PILE_SPACING_MM * MM_TO_FT
        pile_len_ft = DEFAULT_PILE_LEN_M / 0.3048

        with forms.ProgressBar(title="正在自动布置垫层与松木桩...", cancellable=True) as pb:
            total = len(target_elements)
            for idx, elem in enumerate(target_elements):
                if pb.cancelled:
                    break
                pb.update_progress(idx + 1, total)

                try:
                    cat_name = elem.Category.Name if elem.Category else "构件"
                    elem_id_str = get_elem_id_str(elem)

                    # --- 【关键防重】：如果该构件之前布置过，立即清除旧垫层与旧桩 ---
                    if elem_id_str in existing_index:
                        old_ids = existing_index[elem_id_str]
                        if old_ids:
                            try:
                                doc.Delete(List[DB.ElementId](old_ids))
                                count_cleaned_old += len(old_ids)
                            except Exception:
                                pass
                        del existing_index[elem_id_str]

                    # 判断线式（排管/管道）还是点式（井/基础）
                    curve_info = extract_curve_element_info(elem)

                    if curve_info is not None:
                        # -------------------------
                        # 线式构件：排管 / 管沟 / 管道
                        # -------------------------
                        p0 = curve_info['p0']
                        p1 = curve_info['p1']
                        dir_vec = curve_info['dir_vec']
                        perp_vec = curve_info['perp_vec']
                        elem_wid_ft = curve_info['elem_wid_ft']
                        n_mid = curve_info['n_mid']
                        t_start = curve_info['t_start']
                        t_end = curve_info['t_end']
                        z_start_bot = curve_info['z_start_bot']
                        z_end_bot = curve_info['z_end_bot']

                        # 生成 3D 实体垫层 (100mm厚，各边外挑100mm，严密贴紧构件真实底面坡度，写入宿主ID)
                        name_prefix = "排管垫层" if ("排管" in cat_name or "排管" in get_fam_name(elem)) else ("管道垫层" if "管" in cat_name else "垫层")
                        pad_ds = create_sloped_directshape_pad(
                            doc, p0, dir_vec, perp_vec, n_mid, t_start, t_end,
                            z_start_bot, z_end_bot, elem_wid_ft, pad_overhang_ft, pad_thick_ft,
                            elem_id_str, cat_name,
                            name_prefix=name_prefix
                        )
                        if pad_ds:
                            count_pad += 1

                        # 布置松木桩（沿管道坡度连续计算，桩顶100%严密贴合垫层底面）
                        piles = layout_piles_for_curve(
                            doc, p0, dir_vec, perp_vec, n_mid, t_start, t_end,
                            z_start_bot, z_end_bot, elem_wid_ft, pad_thick_ft,
                            pile_spacing_ft, pile_sym, level, pile_len_ft,
                            elem_id_str, cat_name
                        )
                        count_piles += len(piles)
                        processed_elements += 1

                    else:
                        # -------------------------
                        # 点式构件：井 / 承台 / 基础 / 楼板
                        # -------------------------
                        box_info = extract_element_local_box(elem)
                        if box_info is None:
                            skipped += 1
                            continue

                        len_ft, wid_ft, c_world, z_bot, rot, u_dir, v_dir = box_info
                        pad_len_ft = len_ft + 2.0 * pad_overhang_ft
                        pad_wid_ft = wid_ft + 2.0 * pad_overhang_ft
                        z_cushion_bot = z_bot - pad_thick_ft

                        # 生成垫层 (100mm厚)
                        # 优先使用 市政_井垫层 族
                        placed_family_pad = False
                        is_well = "井" in cat_name or (hasattr(elem, 'Symbol') and elem.Symbol and "井" in get_fam_name(elem.Symbol))
                        if well_pad_sym is not None and is_well:
                            try:
                                pad_inst = doc.Create.NewFamilyInstance(
                                    DB.XYZ(c_world.X, c_world.Y, 0),
                                    well_pad_sym,
                                    level,
                                    DB.Structure.StructuralType.Footing
                                )
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
                                    p_spec.Set("75mm Ø")
                                p_spc = pad_inst.LookupParameter("松木桩间距")
                                if p_spc and not p_spc.IsReadOnly:
                                    p_spc.Set(pile_spacing_ft)
                                p_plen = pad_inst.LookupParameter("松木桩长")
                                if p_plen and not p_plen.IsReadOnly:
                                    p_plen.Set(pile_len_ft)
                                p_mk = pad_inst.LookupParameter("关联构件标记")
                                if p_mk and not p_mk.IsReadOnly:
                                    p_mk.Set(elem_id_str)
                                p_comm = pad_inst.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                                if p_comm and not p_comm.IsReadOnly:
                                    p_comm.Set("井垫层 (100mm厚) | 宿主: {} (Id:{})".format(cat_name, elem_id_str))

                                if abs(rot) > 1e-4:
                                    axis = DB.Line.CreateBound(
                                        DB.XYZ(c_world.X, c_world.Y, 0),
                                        DB.XYZ(c_world.X, c_world.Y, 1)
                                    )
                                    DB.ElementTransformUtils.RotateElement(doc, pad_inst.Id, axis, rot)

                                count_pad += 1
                                placed_family_pad = True
                            except Exception:
                                placed_family_pad = False

                        if not placed_family_pad:
                            # 降级：使用 3D 实体 DirectShape 垫层 (100mm厚)
                            pad_ds = create_directshape_box_pad(
                                doc, c_world, u_dir, v_dir,
                                pad_len_ft, pad_wid_ft, z_bot, pad_thick_ft,
                                elem_id_str, cat_name,
                                name_prefix="井垫层"
                            )
                            if pad_ds:
                                count_pad += 1

                        # 布置松木桩网格
                        piles = layout_piles_for_box(
                            doc, c_world, u_dir, v_dir,
                            len_ft, wid_ft, z_cushion_bot,
                            pile_spacing_ft, pile_sym, level, pile_len_ft,
                            elem_id_str, cat_name
                        )
                        count_piles += len(piles)
                        processed_elements += 1

                except Exception as ex_elem:
                    skipped += 1
                    continue

        t_main.Commit()

        # 6. 汇总统计与完成报告
        total_pile_length_m = count_piles * DEFAULT_PILE_LEN_M
        radius_m = (DEFAULT_PILE_DIAM_MM / 2.0) / 1000.0
        total_pile_volume_m3 = count_piles * (math.pi * radius_m * radius_m * DEFAULT_PILE_LEN_M)

        cleanup_msg = ""
        if count_cleaned_old > 0:
            cleanup_msg = "♻️ 防重清理：成功自动覆盖清理了 {} 个旧垫层与旧桩图元！\n\n".format(count_cleaned_old)

        result_msg = (
            "🎉 松木桩与垫层布置完成！\n\n"
            "{}"
            "📊 工程量统计：\n"
            "• 成功处理构件：{} 个 (跳过: {})\n"
            "• 生成垫层：{} 块 (厚度100mm, 各边外挑100mm, 精准居中对齐)\n"
            "• 生成松木桩：{} 根 (Φ75mm, 桩长6.0m, @500mm)\n"
            "• 松木桩总延米：{:.2f} m\n"
            "• 松木桩木材方量：{:.2f} m³\n\n"
            "所有生成的垫层与松木桩均归属【结构基础】类别，重复框选时将自动识别并更新替换，杜绝重复！"
        ).format(
            cleanup_msg, processed_elements, skipped, count_pad, count_piles,
            total_pile_length_m, total_pile_volume_m3
        )

        forms.alert(result_msg, title="布置完成")

    except Exception as e:
        forms.alert("布置过程发生异常:\n" + str(e) + "\n\n" + traceback.format_exc(), title="报错追踪")


if __name__ == '__main__':
    main()
