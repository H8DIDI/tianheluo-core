# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "支吊架\n一键布置"
__author__ = "KAIFA"
__doc__ = ("框选水管、风管、桥架，一键按规范间距布置支吊架。\n"
           "· 并排 / 叠放的管线自动合成一组共用一个支架，按这一组的总重量（管 + 水 + 保温；风管钢板；桥架 + 电缆）和需要的宽度选角钢规格和宽度 b。\n"
           "· 按现场土建选支架类型：上方有楼板/梁 → U 型吊架（1~4 层）；下方有地面 → 落地龙门架（1~2 层），固定端自动对齐建筑完成面；"
           "单根小管 → 单根支撑；贴墙 → 水平支架 / 斜撑支架。需要先加载土建链接模型（有建筑模型时按建筑楼板完成面）。\n"
           "· 布置用的是项目里现有的 HW- 系列族，生成后可以直接改参数；也可以在对话框里指定只用某一类。")

import math
import os
import json
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms
from Autodesk.Revit.UI.Selection import ISelectionFilter

# @include _shared/form_fit.py
# @include _shared/settings_ui.py
# @include _shared/ws_guard.py
# @include _shared/compat.py

doc = revit.doc
uidoc = revit.uidoc

TITLE = u"支吊架一键布置"
FT = 304.8
MM = 1.0 / 304.8
RULES_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools")
RULES_PATH = os.path.join(RULES_DIR, u"支吊架规则.json")
LOG_PATH = os.path.join(RULES_DIR, u"支吊架日志.txt")
TAG = u"天河落支吊架"          # 备注里的标记：重新布置 / 删除时靠它认出本插件生成的支架


def _bic(name):
    return getattr(DB.BuiltInCategory, name)


# ============================================================
# 规则（可在「规则设置」里改，保存在 文档\KaifaTools\支吊架规则.json）
# ============================================================
DEFAULT_RULES = {
    # ---- 搜索与选型 ----
    "mode": "auto",                 # auto 自动 / hang 只用吊架 / stand 只用落地龙门 / wall 只用墙边支架
    "prefer": "short",              # 上下都有依托时：short 取自由长度较短的 / hang 优先吊架 / stand 优先落地
    "finish_add_mm": 0.0,           # 完成面补偿：落地支架底 = 地面顶 + 这个值（地面模型没带面层、要另加面层厚时填）
    "hang_max_mm": 3000.0,          # 吊架最长吊杆（楼板离管底最大距离）
    "stand_max_mm": 2500.0,         # 落地支架最大高度（地面离管底最大距离）
    "wall_reach_mm": 350.0,         # 墙离最近的管外壁不超过这个距离，才考虑墙边支架
    "wall_arm_max_mm": 700.0,       # 墙边支架最长悬臂
    "wall_load_max_kg": 180.0,      # 墙边悬臂支架最大承重（超过的改用别的形式）
    "single_stand": True,           # 单根小管用「单根支撑」
    "single_od_max_mm": 220.0,      # 单根支撑最大管外径
    "single_stand_max_mm": 1200.0,  # 单根支撑最大高度
    "min_clear_mm": 60.0,           # 管底到地面 / 管顶到楼板的最小距离
    "round_variant": True,          # 全是圆管（水管）的组用「m圆形」龙门架
    # ---- 分组与间距 ----
    "group_gap_mm": 300.0,          # 并排同组的最大净距
    "group_dz_mm": 1200.0,          # 同组管线中心最大高差（再大不并组）
    "tier_tol_mm": 120.0,           # 管底高差在这个范围内算同一层
    "end_offset_mm": 300.0,         # 端头第一个支架离管端
    "span_scale_pct": 100.0,        # 间距系数：规范间距的百分之几
    "max_span_mm": 3000.0,          # 任何情况的最大间距
    "skip_existing": True,          # 已有支吊架（别的做法的）旁 250 mm 内不再布置
    # ---- 荷载与材料 ----
    "gamma": 1.35,                  # 荷载分项系数
    "steel_f": 215.0,               # 钢材设计强度 N/mm²（Q235）
    "steel_k": 0.90,                # 强度折减（焊缝、锈蚀、施工误差）
    "defl_limit": 250.0,            # 横担挠度限值 L/x
    "post_mu": 1.0,                 # 落地支架立杆计算长度系数
    "beam_pref_max": "75x6",        # 横担优先不超过这个角钢；超了先缩短间距
    "beam_hard_max": "125x10",      # 角钢最大规格（再大就是要人工设计了）
    "beam_min": "50x5",             # 横担最小规格（再轻的荷载也不小于它）
    "post_min": "50x5",             # 立杆 / 吊杆角钢最小规格
    "water_fill": True,             # 水管按充满水计算
    "ins_density": 60.0,            # 保温密度 kg/m³
    "plastic_keys": [u"HDPE", u"PE", u"PVC", u"PPR", u"UPVC", u"PP-R", u"PPH"],
    "tray_cable_kg_per_m_width": 100.0,   # 桥架电缆荷载：每米宽度每米长 kg（0.4 m 宽 → 40 kg/m）
    "clear_each_side_mm": 50.0,     # 管线两侧到立杆内侧的净空
    "pipe_table_ins": [u"15=1.5", u"20=2.0", u"25=2.5", u"32=2.5", u"40=3.0", u"50=3.0", u"70=4.0", u"80=4.0",
                       u"100=4.5", u"125=6.0", u"150=7.0", u"200=8.0", u"250=8.5", u"300=9.5"],
    "pipe_table_bare": [u"15=2.5", u"20=3.0", u"25=3.5", u"32=4.0", u"40=4.5", u"50=5.0", u"70=6.0", u"80=6.0",
                        u"100=6.5", u"125=7.0", u"150=8.0", u"200=9.5", u"250=11.0", u"300=12.0"],
    "duct_small_span_mm": 4000.0,   # 风管长边 ≤ 400 的最大间距
    "duct_big_span_mm": 3000.0,     # 风管长边 > 400 的最大间距
    "tray_span_mm": 2000.0,         # 桥架最大间距
    "plastic_span_factor": 0.5,     # 塑料管间距 = 钢管间距 × 这个系数
}

FIELDS = [
    {"kind": "header", "label": u"选型与搜索"},
    {"key": "mode", "label": u"支架形式", "kind": "choice",
     "choices": [("auto", u"自动（按现场土建判定）"), ("hang", u"只用吊架"), ("stand", u"只用落地龙门架"), ("wall", u"只用墙边支架")]},
    {"key": "prefer", "label": u"上下都能依托时", "kind": "choice",
     "choices": [("short", u"取自由长度短的"), ("hang", u"优先吊架"), ("stand", u"优先落地")]},
    {"key": "finish_add_mm", "label": u"完成面补偿 mm", "kind": "num", "min": -500, "max": 1000,
     "hint": u"落地支架底 = 地面顶 + 这个值。土建模型里地面已含面层就填 0。"},
    {"key": "hang_max_mm", "label": u"吊杆最长 mm", "kind": "num", "min": 200, "max": 10000},
    {"key": "stand_max_mm", "label": u"落地支架最高 mm", "kind": "num", "min": 200, "max": 10000},
    {"key": "wall_reach_mm", "label": u"墙边支架：墙离管最大距离 mm", "kind": "num", "min": 0, "max": 3000},
    {"key": "wall_arm_max_mm", "label": u"墙边支架：最长悬臂 mm", "kind": "num", "min": 100, "max": 3000},
    {"key": "wall_load_max_kg", "label": u"墙边支架：最大承重 kg", "kind": "num", "min": 10, "max": 2000},
    {"key": "single_stand", "label": u"单根小管", "kind": "bool", "bool_text": u"单根管用「单根支撑」"},
    {"key": "single_od_max_mm", "label": u"单根支撑：最大管外径 mm", "kind": "num", "min": 20, "max": 600},
    {"key": "single_stand_max_mm", "label": u"单根支撑：最大高度 mm", "kind": "num", "min": 100, "max": 5000},
    {"key": "min_clear_mm", "label": u"离地 / 离板最小距离 mm", "kind": "num", "min": 0, "max": 1000},
    {"key": "round_variant", "label": u"圆管组", "kind": "bool", "bool_text": u"全是水管的组用「m圆形」龙门架"},
    {"kind": "header", "label": u"分组与间距"},
    {"key": "group_gap_mm", "label": u"并排同组最大净距 mm", "kind": "num", "min": 0, "max": 2000},
    {"key": "group_dz_mm", "label": u"同组最大高差 mm", "kind": "num", "min": 0, "max": 5000},
    {"key": "tier_tol_mm", "label": u"同一层管底高差 mm", "kind": "num", "min": 0, "max": 1000},
    {"key": "end_offset_mm", "label": u"端头第一个支架离管端 mm", "kind": "num", "min": 0, "max": 2000},
    {"key": "span_scale_pct", "label": u"间距系数 %", "kind": "num", "min": 30, "max": 150,
     "hint": u"100 = 按规范表；想布得密一点就改小。"},
    {"key": "max_span_mm", "label": u"最大间距 mm", "kind": "num", "min": 500, "max": 12000},
    {"key": "skip_existing", "label": u"已有支架", "kind": "bool", "bool_text": u"旁边 250 mm 内已有别的支吊架就不再布置"},
    {"kind": "header", "label": u"荷载与钢材"},
    {"key": "gamma", "label": u"荷载分项系数", "kind": "num", "min": 1.0, "max": 2.5},
    {"key": "steel_f", "label": u"钢材设计强度 N/mm²", "kind": "num", "min": 100, "max": 500},
    {"key": "steel_k", "label": u"强度折减系数", "kind": "num", "min": 0.3, "max": 1.0},
    {"key": "defl_limit", "label": u"横担挠度限值 L/", "kind": "num", "min": 100, "max": 1000},
    {"key": "post_mu", "label": u"立杆计算长度系数", "kind": "num", "min": 0.5, "max": 3.0},
    {"key": "beam_pref_max", "label": u"横担优先不超过", "kind": "text", "hint": u"写法：75x6（等边角钢 边宽×厚）。超过时先缩短间距。"},
    {"key": "beam_min", "label": u"横担最小规格", "kind": "text", "hint": u"荷载再小也不低于它，现场常用 50x5 或 63x5。"},
    {"key": "post_min", "label": u"立杆最小规格", "kind": "text"},
    {"key": "beam_hard_max", "label": u"横担最大规格", "kind": "text", "hint": u"再大的荷载会列出来，需要人工设计（槽钢等）。"},
    {"key": "water_fill", "label": u"水管", "kind": "bool", "bool_text": u"按管内充满水计算"},
    {"key": "tray_cable_kg_per_m_width", "label": u"桥架电缆荷载 kg/(m·m宽)", "kind": "num", "min": 0, "max": 2000},
    {"key": "clear_each_side_mm", "label": u"管线两侧净空 mm", "kind": "num", "min": 0, "max": 300},
    {"kind": "header", "label": u"最大间距表（每行 DN=间距 m）"},
    {"key": "pipe_table_ins", "label": u"保温水管", "kind": "lines", "rows": 5},
    {"key": "pipe_table_bare", "label": u"不保温水管", "kind": "lines", "rows": 5},
    {"key": "duct_small_span_mm", "label": u"风管（长边≤400）mm", "kind": "num", "min": 500, "max": 10000},
    {"key": "duct_big_span_mm", "label": u"风管（长边>400）mm", "kind": "num", "min": 500, "max": 10000},
    {"key": "tray_span_mm", "label": u"桥架 mm", "kind": "num", "min": 500, "max": 10000},
    {"key": "plastic_span_factor", "label": u"塑料管间距系数", "kind": "num", "min": 0.2, "max": 1.0},
    {"key": "plastic_keys", "label": u"算作塑料管（类型/管段名含）", "kind": "list"},
]


def _u(x):
    try:
        return u"%s" % x
    except Exception:
        return u"?"


def load_rules():
    r = dict(DEFAULT_RULES)
    try:
        if os.path.isfile(RULES_PATH):
            with open(RULES_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            for k in DEFAULT_RULES:
                if k in d:
                    r[k] = d[k]
    except Exception:
        pass
    return r


def save_rules(r):
    try:
        if not os.path.isdir(RULES_DIR):
            os.makedirs(RULES_DIR)
        with open(RULES_PATH, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def parse_table(lines):
    out = {}
    for ln in lines or []:
        s = _u(ln).replace(u"：", u"=").replace(u":", u"=").replace(u"DN", u"").replace(u"dn", u"").strip()
        if not s:
            continue
        if u"=" not in s:
            raise ValueError(u"表格行「%s」要写成 DN=间距(m)，例如 100=4.5" % ln)
        a, b = s.split(u"=", 1)
        try:
            out[int(round(float(a.strip())))] = float(b.strip().rstrip(u"mM").strip()) * 1000.0
        except Exception:
            raise ValueError(u"表格行「%s」读不出数字" % ln)
    return out


def lookup(table, dn):
    """DN → 间距 mm：有这一档用这一档；介于两档之间取小的；超过表就取最后一档。"""
    keys = sorted(table)
    if not keys:
        return 3000.0
    if dn in table:
        return table[dn]
    lower = [k for k in keys if k <= dn]
    return table[lower[-1]] if lower else table[keys[0]]


# ============================================================
# 钢材：角钢规格表（几何公式算截面特性，不查表，偏保守）
# ============================================================
ANGLES = [(40, 4), (45, 4), (50, 5), (56, 5), (63, 5), (70, 6), (75, 6), (80, 6), (90, 8), (100, 8), (100, 10), (125, 10)]


def _angle_props(b, t):
    """等边角钢 边宽 b、厚 t（mm）→ 面积 mm²、绕平行边形心轴的 I mm⁴、最小 W mm³、最小回转半径 mm。"""
    a1 = float(t) * b
    a2 = float(b - t) * t
    yc = (a1 * b / 2.0 + a2 * t / 2.0) / (a1 + a2)
    ix = t * b ** 3 / 12.0 + a1 * (b / 2.0 - yc) ** 2 + (b - t) * t ** 3 / 12.0 + a2 * (t / 2.0 - yc) ** 2
    w = ix / max(yc, b - yc)
    return a1 + a2, ix, w, 0.195 * b


def angle_label(b, t):
    return u"∠%d×%d" % (b, t)


def parse_angle(text, default):
    try:
        a, b = _u(text).lower().replace(u"∠", u"").replace(u"×", u"x").split(u"x")
        return (int(float(a)), int(float(b)))
    except Exception:
        return default


# 圆钢吊杆（螺纹处有效截面 mm²）
RODS = [(8, 36.6), (10, 58.0), (12, 84.3), (14, 115.0), (16, 157.0), (20, 245.0), (24, 353.0)]
E_STEEL = 206000.0
G = 9.81


class SizeFail(Exception):
    pass


def size_beam(span_mm, load_kg_max, n_items, r):
    """横担选角钢：先看强度和挠度。返回 (b, t, 比优先上限大了吗)。"""
    pref = parse_angle(r["beam_pref_max"], (75, 6))
    hard = parse_angle(r["beam_hard_max"], (125, 10))
    P = load_kg_max * G * r["gamma"]                         # N
    c_m = 0.25 if n_items <= 1 else (1.0 / 6.0 if n_items == 2 else 0.125)
    c_d = 1.0 / 48.0 if n_items <= 1 else (1.0 / 60.0 if n_items == 2 else 5.0 / 384.0)
    M = c_m * P * span_mm
    fd = r["steel_f"] * r["steel_k"]
    lo = parse_angle(r["beam_min"], (50, 5))
    for (b, t) in ANGLES:
        if (b, t) > hard and (b, t) != hard:
            break
        if (b, t) < lo:
            continue
        A, I, W, i = _angle_props(b, t)
        d = c_d * (P / r["gamma"]) * span_mm ** 3 / (E_STEEL * I)   # 挠度用标准荷载
        if M / W <= fd and d <= span_mm / r["defl_limit"]:
            return b, t, (b, t) > pref
    raise SizeFail(u"荷载 %.0f kg、跨度 %.0f mm，超出角钢最大规格 %s（需人工设计）" % (load_kg_max, span_mm, angle_label(*hard)))


def size_post(height_mm, total_kg, r, tension=False):
    """立杆（落地）受压 / 吊杆（角钢）受拉，选角钢。"""
    N = total_kg * G * r["gamma"] / 2.0
    fd = r["steel_f"] * r["steel_k"]
    hard = parse_angle(r["beam_hard_max"], (125, 10))
    lo = parse_angle(r["post_min"], (50, 5))
    for (b, t) in ANGLES:
        if (b, t) < lo:
            continue
        A, I, W, i = _angle_props(b, t)
        if tension:
            ok = N / A <= fd
        else:
            lam = r["post_mu"] * height_mm / i
            phi = 1.0 / (1.0 + (lam / 110.0) ** 2)
            ok = lam <= 200.0 and N / A <= fd * phi
        if ok:
            return b, t
        if (b, t) == hard:
            break
    raise SizeFail(u"立杆长 %.0f mm、承重 %.0f kg，角钢立杆不够（需人工设计）" % (height_mm, total_kg))


def size_rod(total_kg, r):
    N = total_kg * G * r["gamma"] / 2.0
    ft = 140.0 * r["steel_k"] / 0.9       # 螺纹吊杆抗拉设计值（保守）
    for d, As in RODS:
        if N / As <= ft:
            return d
    raise SizeFail(u"承重 %.0f kg，圆钢吊杆最大 M24 也不够" % total_kg)


# ============================================================
# 现有支架族
# ============================================================
class FamInfo(object):
    def __init__(self, key, kind, tiers, name):
        self.key = key
        self.kind = kind          # stand 落地龙门 / hang U型吊架 / rod 圆钢吊杆 / single 单根支撑 / wall 水平支架 / brace 水平斜撑
        self.tiers = tiers
        self.name = name
        self.symbol = None


def find_families():
    """在项目里按族名认现有 HW- 支架族 → {key: FamInfo}。缺的族后面按需提示。"""
    out = {}
    for f in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        n = _u(f.Name)
        if not n.startswith(u"HW-"):
            continue
        key = kind = None
        tiers = 1
        if u"龙门支架_1层" in n:
            key, kind = (u"gm1r" if u"圆形" in n else u"gm1"), u"stand"
        elif u"龙门支架_2层" in n:
            key, kind, tiers = u"gm2", u"stand", 2
        elif u"U型吊架（吊杆）" in n:
            key, kind = u"rod", u"rod"
        elif u"U型吊架（延长）" in n:
            continue
        elif u"U型吊架_" in n:
            for k in (1, 2, 3, 4):
                if u"U型吊架_%d层" % k in n:
                    key, kind, tiers = u"u%d" % k, u"hang", k
        elif u"单根支撑" in n:
            key, kind = u"single", u"single"
        elif u"水平斜撑支架" in n:
            key, kind = u"brace", u"brace"
        elif u"水平支架" in n:
            key, kind = u"wall", u"wall"
        if key is None:
            continue
        ids = list(f.GetFamilySymbolIds())
        if not ids:
            continue
        fi = FamInfo(key, kind, tiers, n)
        fi.symbol = doc.GetElement(ids[0])
        out[key] = fi
    return out


def all_support_instances():
    """项目里所有 HW- 支吊架族实例 → [(实例, 是否本插件生成)]。"""
    res = []
    for e in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance):
        try:
            fam = e.Symbol.Family
            n = _u(fam.Name)
            if not n.startswith(u"HW-"):
                continue
            if not (u"支架" in n or u"吊架" in n or u"支撑" in n):
                continue
            if u"支墩" in n:
                continue
            p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
            mine = p is not None and (p.AsString() or u"").startswith(TAG)
            res.append((e, mine))
        except Exception:
            pass
    return res


# ============================================================
# 读管线：几何 + 重量
# ============================================================
class Member(object):
    pass


def _dpar(e, bip):
    p = e.get_Parameter(bip)
    return p.AsDouble() if p is not None and p.HasValue else 0.0


def _text_of(e):
    parts = []
    try:
        t = doc.GetElement(e.GetTypeId())
        parts.append(_u(t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() or u""))
    except Exception:
        pass
    try:
        sp = e.get_Parameter(DB.BuiltInParameter.RBS_PIPE_SEGMENT_PARAM)
        seg = doc.GetElement(sp.AsElementId()) if sp is not None else None
        if seg is not None:
            parts.append(_u(seg.Name))
    except Exception:
        pass
    return u" ".join(parts)


def _insulation(e):
    """保温厚度（ft）；没有返回 0。"""
    try:
        ids = DB.InsulationLiningBase.GetInsulationIds(doc, e.Id)
        for i in ids:
            ins = doc.GetElement(i)
            if ins is not None:
                return ins.Thickness
    except Exception:
        pass
    return 0.0


# 钢管外径、壁厚（mm）：按 DN 取最近一档
STEEL_PIPE = [(15, 21.3, 2.8), (20, 26.9, 2.8), (25, 33.7, 3.2), (32, 42.4, 3.5), (40, 48.3, 3.5), (50, 60.3, 3.8),
              (65, 76.1, 4.0), (70, 76.1, 4.0), (80, 88.9, 4.0), (100, 114.3, 4.5), (125, 139.7, 4.5), (150, 168.3, 5.0),
              (200, 219.1, 6.0), (250, 273.0, 7.0), (300, 325.0, 8.0), (350, 377.0, 9.0), (400, 426.0, 9.0), (500, 530.0, 9.0),
              (600, 630.0, 9.0), (800, 820.0, 10.0)]
TRAY_KG = [(100, 4.0), (200, 6.0), (300, 9.0), (400, 12.0), (500, 15.0), (600, 19.0), (800, 26.0), (1000, 32.0), (1200, 40.0)]


def _near(table, x, col=1):
    best = min(table, key=lambda row: abs(row[0] - x))
    return best


def duct_sheet_mm(long_side):
    if long_side <= 320:
        return 0.5
    if long_side <= 630:
        return 0.75
    if long_side <= 1000:
        return 0.75
    if long_side <= 1250:
        return 1.0
    return 1.2


def read_member(e, r):
    """管线 → Member，或 (None, 跳过原因)。"""
    cat = int(DB.BuiltInCategory.OST_PipeCurves), int(DB.BuiltInCategory.OST_DuctCurves), int(DB.BuiltInCategory.OST_CableTray)
    c = getattr(e.Location, 'Curve', None)
    if c is None or not isinstance(c, DB.Line):
        return None, u"不是直管段"
    a, b = c.GetEndPoint(0), c.GetEndPoint(1)
    dxy = DB.XYZ(b.X - a.X, b.Y - a.Y, 0)
    L = dxy.GetLength()
    if L < 0.05:
        return None, u"竖向管线"
    if abs(b.Z - a.Z) / L > 0.2:
        return None, u"坡度太大（>20%）"
    m = Member()
    m.e, m.id, m.a, m.b = e, thl_eid(e.Id), a, b
    m.dir = dxy.Normalize()
    ccat = thl_eid(e.Category.Id) if e.Category is not None else 0
    ins = _insulation(e)
    m.ins = ins > 1e-6
    txt = _text_of(e).upper()
    if ccat == int(DB.BuiltInCategory.OST_PipeCurves):
        m.kind = u"pipe"
        dn = _dpar(e, DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM) * FT
        od = _dpar(e, DB.BuiltInParameter.RBS_PIPE_OUTER_DIAMETER) * FT
        if od < 1:
            od = dn
        m.dn, m.od = int(round(dn)), od
        plastic = any(_u(k).upper() in txt for k in r["plastic_keys"] if k)
        row = _near(STEEL_PIPE, dn)
        if plastic:
            wall = od / 11.0
            rho = 1000.0 if u"PE" in txt or u"PP" in txt else 1400.0
        else:
            wall = row[2]
            rho = 7850.0
        idm = max(od - 2 * wall, 0) / 1000.0
        odm = od / 1000.0
        kg = math.pi / 4.0 * (odm ** 2 - idm ** 2) * rho
        if r["water_fill"]:
            kg += math.pi / 4.0 * idm ** 2 * 1000.0
        w_out = od
        if m.ins:
            t_mm = ins * FT
            kg += math.pi / 4.0 * ((odm + 2 * t_mm / 1000.0) ** 2 - odm ** 2) * r["ins_density"]
            w_out = od + 2 * t_mm
        m.wpm, m.w, m.h = kg, w_out * MM, w_out * MM
        tab = parse_table(r["pipe_table_ins"] if m.ins else r["pipe_table_bare"])
        m.span = lookup(tab, m.dn) * (r["plastic_span_factor"] if plastic else 1.0)
        m.label = u"DN%d水管" % m.dn
    elif ccat == int(DB.BuiltInCategory.OST_DuctCurves):
        m.kind = u"duct"
        w = _dpar(e, DB.BuiltInParameter.RBS_CURVE_WIDTH_PARAM) * FT
        h = _dpar(e, DB.BuiltInParameter.RBS_CURVE_HEIGHT_PARAM) * FT
        if w < 1:
            d = _dpar(e, DB.BuiltInParameter.RBS_CURVE_DIAMETER_PARAM) * FT
            w = h = d
            per = math.pi * d
            long_side = d
        else:
            per = 2 * (w + h)
            long_side = max(w, h)
        kg = per / 1000.0 * duct_sheet_mm(long_side) / 1000.0 * 7850.0
        if m.ins:
            t_mm = ins * FT
            kg += (per + 8 * t_mm) / 1000.0 * t_mm / 1000.0 * r["ins_density"]
            w += 2 * t_mm
            h += 2 * t_mm
        m.dn, m.od = 0, max(w, h)
        m.wpm, m.w, m.h = kg, w * MM, h * MM
        m.span = r["duct_small_span_mm"] if long_side <= 400 else r["duct_big_span_mm"]
        m.label = u"%d×%d风管" % (int(round(w)), int(round(h)))
    elif ccat == int(DB.BuiltInCategory.OST_CableTray):
        m.kind = u"tray"
        w = _dpar(e, DB.BuiltInParameter.RBS_CABLETRAY_WIDTH_PARAM) * FT
        h = _dpar(e, DB.BuiltInParameter.RBS_CABLETRAY_HEIGHT_PARAM) * FT
        kg = _near(TRAY_KG, w)[1] + r["tray_cable_kg_per_m_width"] * w / 1000.0
        m.dn, m.od = 0, max(w, h)
        m.wpm, m.w, m.h = kg, w * MM, h * MM
        m.span = r["tray_span_mm"]
        m.label = u"%d×%d桥架" % (int(round(w)), int(round(h)))
    else:
        return None, u"不是水管 / 风管 / 桥架"
    return m, None


def read_members(elems, r):
    ms, skipped = [], []
    for e in elems:
        m, why = read_member(e, r)
        if m is None:
            skipped.append((e, why))
        else:
            ms.append(m)
    return ms, skipped


# ============================================================
# 分组：平行、走向重叠、净距近、高差不大 → 一组
# ============================================================
def _uf(par, i):
    while par[i] != i:
        par[i] = par[par[i]]
        i = par[i]
    return i


def _same_group(p, q, gap, dzmax):
    d = p.dir
    if abs(d.X * q.dir.X + d.Y * q.dir.Y) < 0.985:
        return False
    X = DB.XYZ(d.Y, -d.X, 0)

    def us(pt):
        w = pt - p.a
        return w.X * d.X + w.Y * d.Y, w.X * X.X + w.Y * X.Y
    ua, sa = us(q.a)
    ub, sb = us(q.b)
    lp = (p.b - p.a).X * d.X + (p.b - p.a).Y * d.Y
    if max(ua, ub) < -0.05 or min(ua, ub) > lp + 0.05:
        return False
    clear = abs((sa + sb) / 2.0) - (p.w + q.w) / 2.0
    if clear > gap:
        return False
    zp, zq = (p.a.Z + p.b.Z) / 2.0, (q.a.Z + q.b.Z) / 2.0
    return abs(zp - zq) <= dzmax


def group_members(ms, r):
    gap, dzmax = r["group_gap_mm"] * MM, r["group_dz_mm"] * MM
    cell = 5.0 / 0.3048
    grid = {}
    for i, p in enumerate(ms):
        x0, x1 = sorted((p.a.X, p.b.X))
        y0, y1 = sorted((p.a.Y, p.b.Y))
        for gx in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
            for gy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                grid.setdefault((gx, gy), set()).add(i)
    par = list(range(len(ms)))
    seen = set()
    for cs in grid.values():
        ids = sorted(cs)
        for ii in range(len(ids)):
            for jj in range(ii + 1, len(ids)):
                i, j = ids[ii], ids[jj]
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                if _same_group(ms[i], ms[j], gap, dzmax):
                    ri, rj = _uf(par, i), _uf(par, j)
                    if ri != rj:
                        par[rj] = ri
    gs = {}
    for i in range(len(ms)):
        gs.setdefault(_uf(par, i), []).append(ms[i])
    return list(gs.values())


class Group(object):
    """一组管线 + 组轴线坐标：u 沿走向，s 沿横向（X = (dir.Y, -dir.X)，和族的本地 X 轴对应）。"""

    def __init__(self, members):
        self.members = members
        lead = max(members, key=lambda m: (m.b - m.a).GetLength())
        self.O = DB.XYZ(lead.a.X, lead.a.Y, 0)
        self.ax = lead.dir
        self.X = DB.XYZ(self.ax.Y, -self.ax.X, 0)
        for m in members:
            m.u0, m.s0 = self.us(m.a)
            m.u1, m.s1 = self.us(m.b)
            if m.u0 > m.u1:
                m.u0, m.u1, m.s0, m.s1 = m.u1, m.u0, m.s1, m.s0
                m.a, m.b = m.b, m.a
        self.U0 = min(m.u0 for m in members)
        self.U1 = max(m.u1 for m in members)
        self.stations = []

    def us(self, pt):
        w = pt - self.O
        return w.X * self.ax.X + w.Y * self.ax.Y, w.X * self.X.X + w.Y * self.X.Y

    def world(self, u, s, z):
        p = self.O + self.ax * u + self.X * s
        return DB.XYZ(p.X, p.Y, z)

    def at(self, m, u):
        """成员 m 在走向位置 u 处：横向中心 s、中心高 z。"""
        L = m.u1 - m.u0
        t = 0.0 if L < 1e-9 else min(max((u - m.u0) / L, 0.0), 1.0)
        return m.s0 + (m.s1 - m.s0) * t, m.a.Z + (m.b.Z - m.a.Z) * t


class Station(object):
    pass


def plan_stations(g, r):
    """沿组的走向排站位，每个站位算：在场成员、横向范围、分层、荷载。"""
    span = min(m.span for m in g.members) * r["span_scale_pct"] / 100.0
    span = max(min(span, r["max_span_mm"]), 600.0)
    return span


def make_stations(g, span_mm, r):
    L = (g.U1 - g.U0) * FT
    e = min(r["end_offset_mm"], L / 2.0)
    if L <= 2 * e + 1:
        us = [(g.U0 + g.U1) / 2.0]
    else:
        n = int(math.ceil((L - 2 * e) / span_mm)) + 1
        us = [g.U0 + (e + (L - 2 * e) * k / float(n - 1)) * MM for k in range(n)]
    sts = []
    for k, u in enumerate(us):
        st = Station()
        st.g, st.u, st.idx = g, u, k
        prev_u = us[k - 1] if k > 0 else None
        next_u = us[k + 1] if k + 1 < len(us) else None
        left = (u - prev_u) / 2.0 if prev_u is not None else e * MM
        right = (next_u - u) / 2.0 if next_u is not None else e * MM
        st.trib_mm = max((left + right) * FT, 500.0)
        st.active = [m for m in g.members if m.u0 - 0.05 <= u <= m.u1 + 0.05]
        sts.append(st)
    return [s for s in sts if s.active]


def analyse_station(st, r):
    """站位几何：横向范围、分层（自下而上）、荷载。"""
    g = st.g
    tol = r["tier_tol_mm"] * MM
    items = []
    for m in st.active:
        s, zc = g.at(m, st.u)
        items.append({'m': m, 's': s, 'lo': s - m.w / 2.0, 'hi': s + m.w / 2.0, 'zb': zc - m.h / 2.0, 'zt': zc + m.h / 2.0})
    st.items = items
    st.smin = min(i['lo'] for i in items)
    st.smax = max(i['hi'] for i in items)
    st.sc = (st.smin + st.smax) / 2.0
    st.width_mm = (st.smax - st.smin) * FT
    order = sorted(items, key=lambda i: i['zb'])
    tiers = []
    for it in order:
        if tiers and it['zb'] - tiers[-1][0]['zb'] <= tol:
            tiers[-1].append(it)
        else:
            tiers.append([it])
    st.tiers = tiers                                           # 自下而上
    st.tier_top = [min(i['zb'] for i in t) for t in tiers]     # 每层横担顶面标高（该层最低管底）
    st.tier_kg = [sum(i['m'].wpm for i in t) * st.trib_mm / 1000.0 for t in tiers]
    st.total_kg = sum(st.tier_kg)
    st.max_tier_kg = max(st.tier_kg)
    st.n_max = max(len(t) for t in tiers)
    st.top_z = max(i['zt'] for i in items)
    st.bot_z = min(i['zb'] for i in items)
    st.has_pipe_only = all(i['m'].kind == u"pipe" for i in items)


# ============================================================
# 土建探测：射线（含链接模型）
# ============================================================
class Probe(object):
    def __init__(self):
        self.view = self._pick_view()
        self.note = u""
        self.ok = self.view is not None
        if not self.ok:
            return

        def mk(cats):
            f = DB.ElementMulticategoryFilter(List[DB.BuiltInCategory]([_bic(c) for c in cats]))
            ri = DB.ReferenceIntersector(f, DB.FindReferenceTarget.Element, self.view)
            try:
                ri.FindReferencesInRevitLinks = True
            except Exception:
                pass
            return ri
        self.up = mk(["OST_Floors", "OST_Roofs", "OST_StructuralFraming"])
        self.down = mk(["OST_Floors", "OST_Roofs", "OST_StructuralFoundation"])
        self.side = mk(["OST_Walls", "OST_StructuralColumns", "OST_Columns"])

    def _pick_view(self):
        cands = []
        for v in DB.FilteredElementCollector(doc).OfClass(DB.View3D):
            if v.IsTemplate:
                continue
            try:
                if v.IsPerspective:
                    continue
            except Exception:
                pass
            score = 0
            try:
                if v.IsSectionBoxActive:
                    score -= 100
            except Exception:
                pass
            for c in ("OST_Floors", "OST_Walls", "OST_StructuralFraming", "OST_Roofs"):
                try:
                    if not v.GetCategoryHidden(DB.ElementId(_bic(c))):
                        score += 1
                except Exception:
                    pass
            if doc.ActiveView is not None and v.Id == doc.ActiveView.Id:
                score += 2
            cands.append((score, v))
        if not cands:
            return None
        cands.sort(key=lambda x: -x[0])
        return cands[0][1]

    @staticmethod
    def _nearest(ri, origin, direction):
        try:
            h = ri.FindNearest(origin, direction)
        except Exception:
            return None
        if h is None:
            return None
        try:
            pt = h.GetReference().GlobalPoint
            return h.Proximity, pt
        except Exception:
            return None

    def cast(self, which, origin, direction):
        return self._nearest(getattr(self, which), origin, direction)


Z = DB.XYZ(0, 0, 1)
NZ = DB.XYZ(0, 0, -1)


def probe_station(st, pr, r, post_half_ft):
    """上 / 下 / 左右墙：返回 dict。post_half_ft = 立杆到中心的横向距离。"""
    g = st.g
    res = {'up': None, 'down': None, 'wl': None, 'wr': None}
    xs = [st.sc - post_half_ft, st.sc + post_half_ft]
    ups, downs = [], []
    for s in xs:
        o_up = g.world(st.u, s, st.top_z + 0.01)
        h = pr.cast('up', o_up, Z)
        if h:
            ups.append(h[1].Z)
        o_dn = g.world(st.u, s, st.bot_z - 0.01)
        h = pr.cast('down', o_dn, NZ)
        if h:
            downs.append(h[1].Z)
    res['up'] = ups
    res['down'] = downs
    zmid = (st.bot_z + st.top_z) / 2.0
    for key, sgn, edge in (('wl', -1.0, st.smin), ('wr', 1.0, st.smax)):
        o = g.world(st.u, edge, zmid)
        h = pr.cast('side', o, g.X * sgn)
        if h:
            res[key] = (h[0], g.us(h[1])[1])          # (到管外壁的距离 ft, 墙面横向坐标 s)
    return res


# ============================================================
# 选型：给站位挑支架族 + 尺寸
# ============================================================
class Plan(object):
    pass


def _round50(mm):
    return int(math.ceil(mm / 50.0 - 1e-9) * 50)


def level_for(z):
    lv = sorted(DB.FilteredElementCollector(doc).OfClass(DB.Level), key=lambda l: l.Elevation)
    cand = [l for l in lv if l.Elevation <= z + 0.01]
    return cand[-1] if cand else lv[0]


def t_plate(fam):
    return 5.0


def choose(st, ctx, fams, r):
    """返回 Plan 或抛 SizeFail（带原因）。"""
    mode = r["mode"]
    clear_side = r["clear_each_side_mm"]
    need_clear = st.width_mm + 2 * clear_side                        # 立杆内侧净宽
    minc = r["min_clear_mm"] * MM
    ntier = len(st.tiers)
    opts = []                                                        # 候选 (优先级, 说明, 构造函数)
    reasons = []

    # ---- 墙边支架 ----
    wall = None
    for key in ('wl', 'wr'):
        w = ctx[key]
        if w is not None and w[0] * FT <= r["wall_reach_mm"]:
            if wall is None or w[0] < wall[0]:
                wall = (w[0], w[1], -1.0 if key == 'wl' else 1.0)
    # ---- 吊架：楼板 / 梁底（取较低的一侧，保证两侧吊杆都能够到）----
    hang_z = None
    if ctx['up']:
        hang_z = min(ctx['up'])
    stand_z = None
    if ctx['down']:
        stand_z = max(ctx['down']) + r["finish_add_mm"] * MM

    def mk_wall():
        if ntier != 1:
            raise SizeFail(u"墙边支架只能放一层")
        if wall is None:
            raise SizeFail(u"管线旁 %.0f mm 内没有墙" % r["wall_reach_mm"])
        # 悬臂长：从墙面到最远一侧管外壁 + 净空
        arm = (st.smax - wall[1]) * FT if wall[2] > 0 else (wall[1] - st.smin) * FT
        arm = _round50(arm + clear_side)
        arm = abs(arm)
        if arm > r["wall_arm_max_mm"]:
            raise SizeFail(u"悬臂 %d mm 超过 %.0f" % (arm, r["wall_arm_max_mm"]))
        if st.total_kg > r["wall_load_max_kg"]:
            raise SizeFail(u"墙边支架承重 %.0f kg 超过 %.0f kg" % (st.total_kg, r["wall_load_max_kg"]))
        bb, bt, over = size_beam(arm, st.total_kg, 1, r)
        use_brace = arm >= 300 or st.total_kg > 80
        key = u"brace" if use_brace else u"wall"
        if key not in fams:
            key = u"wall" if u"wall" in fams else None
        if key is None:
            raise SizeFail(u"项目里没有水平支架族")
        p = Plan()
        p.fam = fams[key]
        p.b_mm = arm
        p.beam = (bb, bt)
        p.post = None
        p.rod = None
        p.pos_s = wall[1]
        p.rot_flip = wall[2] < 0           # 墙在 -X 侧：臂向 +X 伸出，不用翻；墙在 +X 侧要翻 180°
        p.rot_flip = wall[2] > 0
        p.z0 = st.tier_top[0]
        p.hs = [arm * 0.6 if key == u"brace" else 0.0]
        p.desc = u"墙边%s 悬臂%d" % (u"斜撑支架" if key == u"brace" else u"水平支架", arm)
        p.over = over
        return p

    def mk_single():
        if not (r["single_stand"] and len(st.items) == 1 and st.items[0]['m'].kind == u"pipe"):
            raise SizeFail(u"不是单根小管")
        m = st.items[0]['m']
        if m.od > r["single_od_max_mm"]:
            raise SizeFail(u"管径 %.0f 超过单根支撑上限" % m.od)
        if stand_z is None:
            raise SizeFail(u"下方没有地面")
        if u"single" not in fams:
            raise SizeFail(u"项目里没有单根支撑族")
        free = (st.bot_z - stand_z) * FT
        if free > r["single_stand_max_mm"]:
            raise SizeFail(u"管底离地 %.0f mm 超过单根支撑上限" % free)
        if free < r["min_clear_mm"] + 20:
            raise SizeFail(u"管底离地只有 %.0f mm" % free)
        p = Plan()
        p.fam = fams[u"single"]
        p.b_mm = None
        p.beam = None
        p.post = None
        p.rod = None
        p.pos_s = st.items[0]['s']
        p.rot_flip = False
        p.z0 = stand_z
        p.hs = [free - 5.0 - 15.0]               # h1 = 管底到地面 - 固件厚 - 管径偏移量
        p.od_mm = m.od
        p.desc = u"单根支撑 管径%.0f 高%.0f" % (m.od, free)
        p.over = False
        return p

    def mk_stand():
        if stand_z is None:
            raise SizeFail(u"下方没有地面（请确认已加载土建链接模型）")
        free = (st.tier_top[0] - stand_z) * FT
        if free > r["stand_max_mm"]:
            raise SizeFail(u"管底离地 %.0f mm 超过落地支架上限 %.0f" % (free, r["stand_max_mm"]))
        if free < r["min_clear_mm"] + 10:
            raise SizeFail(u"管底离地只有 %.0f mm" % free)
        key = {1: (u"gm1r" if (r["round_variant"] and st.has_pipe_only and u"gm1r" in fams) else u"gm1"), 2: u"gm2"}.get(ntier)
        if key is None:
            raise SizeFail(u"%d 层管线，落地龙门架最多 2 层" % ntier)
        if key not in fams:
            raise SizeFail(u"项目里没有族「%s」" % key)
        post_w = 63
        b_clear = _round50(need_clear)
        span = b_clear + post_w
        bb, bt, over = size_beam(span, st.max_tier_kg, st.n_max, r)
        # 重新按角钢宽度算 b（龙门架的 b 是外包总宽）
        pb, pt = size_post(st.top_z * 0 + (st.tier_top[-1] - stand_z) * FT, st.total_kg, r)
        b_out = max(_round50(b_clear + 2 * pb), 500)
        hs = [free - 5.0]
        for k in range(1, ntier):
            hs.append((st.tier_top[k] - st.tier_top[k - 1]) * FT)
        p = Plan()
        p.fam = fams[key]
        p.b_mm = b_out
        p.beam = (bb, bt)
        p.post = (pb, pt)
        p.rod = None
        p.pos_s = st.sc
        p.rot_flip = False
        p.z0 = stand_z
        p.hs = hs
        p.desc = u"落地龙门架%d层 b=%d 高%.0f" % (ntier, b_out, sum(hs) + 5.0)
        p.over = over
        return p

    def mk_hang():
        if hang_z is None:
            raise SizeFail(u"上方没有楼板 / 梁（请确认已加载土建链接模型）")
        top1 = st.tier_top[-1]                               # 吊架的 tier1 是最高的一层
        drop = (hang_z - top1) * FT
        if drop > r["hang_max_mm"]:
            raise SizeFail(u"楼板离管底 %.0f mm 超过吊杆上限 %.0f" % (drop, r["hang_max_mm"]))
        if hang_z < st.top_z + minc:
            raise SizeFail(u"顶板离管顶不足 %.0f mm" % r["min_clear_mm"])
        key = {1: u"u1", 2: u"u2", 3: u"u3", 4: u"u4"}.get(ntier)
        if key is None:
            raise SizeFail(u"%d 层管线，U 型吊架最多 4 层" % ntier)
        if key not in fams:
            raise SizeFail(u"项目里没有族 U型吊架_%d层" % ntier)
        b_clear = _round50(need_clear)
        span = b_clear + 63
        bb, bt, over = size_beam(span, st.max_tier_kg, st.n_max, r)
        pb, pt = size_post(drop, st.total_kg, r, tension=True)
        hs = [drop - 5.0]
        for k in range(1, ntier):
            hs.append((st.tier_top[ntier - k] - st.tier_top[ntier - k - 1]) * FT)
        p = Plan()
        p.fam = fams[key]
        p.b_mm = b_clear
        p.beam = (bb, bt)
        p.post = (pb, pt)
        p.rod = None
        p.pos_s = st.sc
        p.rot_flip = False
        p.z0 = hang_z
        p.hs = hs
        p.desc = u"U型吊架%d层 b=%d 吊高%.0f" % (ntier, b_clear, drop)
        p.over = over
        # 圆钢吊杆版：单层、较轻时用
        if ntier == 1 and u"rod" in fams and st.total_kg <= 120 and st.width_mm <= 600:
            d = size_rod(st.total_kg, r)
            q = Plan()
            q.__dict__.update(p.__dict__)
            q.fam = fams[u"rod"]
            q.rod = d
            q.post = None
            q.desc = u"圆钢吊杆吊架 b=%d Φ%d 吊高%.0f" % (b_clear, d, drop)
            return q
        return p

    builders = []
    if mode == "wall":
        builders = [("墙边", mk_wall)]
    elif mode == "hang":
        builders = [("吊架", mk_hang)]
    elif mode == "stand":
        builders = [("落地", mk_stand)]
    else:
        builders.append(("墙边", mk_wall))
        builders.append(("单根", mk_single))
        free_h = (hang_z - st.tier_top[-1]) * FT if hang_z is not None else None
        free_s = (st.tier_top[0] - stand_z) * FT if stand_z is not None else None
        pref = r["prefer"]
        if free_h is not None and free_s is not None:
            if pref == "hang" or (pref == "short" and free_h < free_s):
                builders += [("吊架", mk_hang), ("落地", mk_stand)]
            else:
                builders += [("落地", mk_stand), ("吊架", mk_hang)]
        else:
            builders += [("吊架", mk_hang), ("落地", mk_stand)]
    last = []
    for name, fn in builders:
        try:
            p = fn()
            return p
        except SizeFail as ex:
            last.append(u"%s：%s" % (name, _u(ex)))
    raise SizeFail(u"；".join(last) if last else u"没有可用的支架形式")


# ============================================================
# 放置
# ============================================================
def _setp(inst, name, mm=None, text=None):
    p = inst.LookupParameter(name)
    if p is None or p.IsReadOnly:
        return False
    try:
        if mm is not None:
            p.Set(mm * MM)
        elif text is not None:
            p.Set(text)
        return True
    except Exception:
        return False


def place(st, p, r, tag_ids, serial):
    g = st.g
    if p.fam.symbol is not None and not p.fam.symbol.IsActive:
        p.fam.symbol.Activate()
    pt = g.world(st.u, p.pos_s, p.z0)
    lv = level_for(pt.Z)
    # 基于标高的族：NewFamilyInstance 的 Z 是「相对标高的偏移」，不是绝对标高（实测：直接给绝对值会抬高一个标高）
    inst = doc.Create.NewFamilyInstance(DB.XYZ(pt.X, pt.Y, pt.Z - lv.Elevation), p.fam.symbol, lv, DB.Structure.StructuralType.NonStructural)
    ang = math.atan2(g.X.Y, g.X.X) + (math.pi if p.rot_flip else 0.0)
    if abs(ang) > 1e-9:
        axis = DB.Line.CreateBound(pt, DB.XYZ(pt.X, pt.Y, pt.Z + 1.0))
        DB.ElementTransformUtils.RotateElement(doc, inst.Id, axis, ang)
    kind = p.fam.kind
    if p.b_mm is not None:
        _setp(inst, u"b", mm=p.b_mm)
        # 族里 a1+a2+a3（加固杆位置）加上约 388 mm 的固定尺寸必须不超过外包宽，否则立杆会被挤到一边（实测）。
        outer = p.b_mm if kind == u"stand" else p.b_mm + 126
        avail = outer - 450.0
        if avail < 800.0:
            a = max(5.0, math.floor(avail / 3.0))
            for n in (u"a1", u"a2", u"a3"):
                _setp(inst, n, mm=a)
    for i, h in enumerate(p.hs):
        if kind == u"brace":
            _setp(inst, u"h", mm=h)
        else:
            _setp(inst, u"h%d" % (i + 1), mm=max(h, 0.0))
    if p.beam:
        bb, bt = p.beam
        _setp(inst, u"横担高h", mm=bb)
        _setp(inst, u"横担宽b", mm=bb)
        _setp(inst, u"横担厚t", mm=bt)
    if p.post:
        pb, pt_ = p.post
        _setp(inst, u"竖杆高h", mm=pb)
        _setp(inst, u"竖杆宽b", mm=pb)
        _setp(inst, u"竖杆厚t", mm=pt_)
    if p.rod:
        _setp(inst, u"竖杆高h", mm=p.rod)
        _setp(inst, u"竖杆宽b", mm=p.rod)
    if kind == u"single":
        _setp(inst, u"管径", mm=p.od_mm)
    # 说明性文字参数
    names = {u"stand": u"落地龙门架", u"hang": u"U型吊架", u"rod": u"圆钢吊杆吊架", u"single": u"单根支撑", u"wall": u"水平支架", u"brace": u"水平斜撑支架"}
    _setp(inst, u"支吊架类型", text=names.get(kind, u""))
    if p.beam:
        _setp(inst, u"横担规格", text=angle_label(*p.beam))
    if p.post:
        _setp(inst, u"竖杆规格", text=angle_label(*p.post))
    if p.rod:
        _setp(inst, u"竖杆规格", text=u"Φ%d圆钢" % p.rod)
    _setp(inst, u"HW-支吊架尺寸", text=p.desc)
    mk = inst.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
    if mk is not None and not mk.IsReadOnly:
        mk.Set(u"ZDJ-%03d" % serial)
    cm = inst.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    if cm is not None and not cm.IsReadOnly:
        cm.Set(u"%s|%s|%s" % (TAG, p.desc, u",".join(u"%d" % i for i in tag_ids[:8])))
    return inst


# ============================================================
# 计划（只读）+ 执行（写）
# ============================================================
class _Fails(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, acc):
        err = False
        for f in acc.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                acc.DeleteWarning(f)
            else:
                err = True
        return DB.FailureProcessingResult.ProceedWithRollBack if err else DB.FailureProcessingResult.Continue


def _tx(name):
    t = DB.Transaction(doc, name)
    o = t.GetFailureHandlingOptions()
    o.SetFailuresPreprocessor(_Fails())
    o.SetClearAfterRollback(True)
    t.SetFailureHandlingOptions(o)
    return t


def plan_all(elems, r, rep_progress=None):
    """只读：读管线 → 分组 → 站位 → 探测土建 → 选型。返回 dict。"""
    fams = find_families()
    ms, skipped = read_members(elems, r)
    groups = [Group(g) for g in group_members(ms, r)] if ms else []
    pr = Probe()
    existing = []
    if r["skip_existing"]:
        for e, mine in all_support_instances():
            if mine:
                continue
            try:
                lp = e.Location.Point
                existing.append((lp.X, lp.Y, lp.Z))
            except Exception:
                pass
    plans, fails, nexist = [], [], 0
    post_half = (63 + 100) * MM
    for g in groups:
        span = plan_stations(g, r)
        for st in make_stations(g, span, r):
            analyse_station(st, r)
            st.span_mm = span
            if existing:
                c = g.world(st.u, st.sc, 0)
                if any(abs(c.X - x) < 250 * MM and abs(c.Y - y) < 250 * MM and st.bot_z - 3.0 < z < st.top_z + 3.0 for x, y, z in existing):
                    nexist += 1
                    continue
            if not pr.ok:
                fails.append((st, u"项目里没有可用的三维视图，无法探测土建"))
                continue
            try:
                ctx = probe_station(st, pr, r, (st.width_mm / 2.0 + r["clear_each_side_mm"]) * MM + post_half)
                p = choose(st, ctx, fams, r)
                plans.append((st, p))
            except SizeFail as ex:
                fails.append((st, _u(ex)))
    return {'members': ms, 'skipped': skipped, 'groups': groups, 'plans': plans, 'fails': fails, 'existing': nexist,
            'probe': pr, 'fams': fams}


def existing_mine_for(ids):
    """已有的、本插件生成且标记里含这些管线 id 的支架。"""
    out = []
    ids = set(ids)
    for e, mine in all_support_instances():
        if not mine:
            continue
        try:
            txt = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).AsString() or u""
            tail = txt.split(u"|")[-1]
            if any(int(x) in ids for x in tail.split(u",") if x.strip().lstrip(u"-").isdigit()):
                out.append(e)
        except Exception:
            pass
    return out


def execute(pl, r, old):
    stats = {'made': 0, 'deleted': 0, 'fail': 0, 'errors': []}
    t = _tx(TITLE)
    t.Start()
    try:
        if old:
            doc.Delete(List[DB.ElementId]([e.Id for e in old]))
            stats['deleted'] = len(old)
        for k, (st, p) in enumerate(pl['plans']):
            if k and k % 300 == 0:
                t.Commit()
                t = _tx(TITLE)
                t.Start()
            sub = DB.SubTransaction(doc)
            sub.Start()
            try:
                place(st, p, r, [m.id for m in st.active], k + 1)
                sub.Commit()
                stats['made'] += 1
            except Exception as ex:
                try:
                    sub.RollBack()
                except Exception:
                    pass
                stats['fail'] += 1
                if len(stats['errors']) < 10:
                    stats['errors'].append(_u(ex))
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return stats


# ============================================================
# 汇总文字
# ============================================================
def summarize(pl, r):
    lines = []
    ms = pl['members']
    kinds = {}
    for m in ms:
        kinds[m.kind] = kinds.get(m.kind, 0) + 1
    kn = {u"pipe": u"水管", u"duct": u"风管", u"tray": u"桥架"}
    lines.append(u"可布置的管线 %d 根（%s），分成 %d 组。" % (len(ms), u"、".join(u"%s %d" % (kn[k], v) for k, v in sorted(kinds.items())) or u"无", len(pl['groups'])))
    by = {}
    over = 0
    for st, p in pl['plans']:
        key = p.fam.name.replace(u"HW-", u"")
        key = key.split(u"_m")[0]
        spec = u"横担%s" % angle_label(*p.beam) if p.beam else u""
        by.setdefault(key, []).append((st, p))
        if p.over:
            over += 1
    lines.append(u"将布置支吊架 %d 个：" % len(pl['plans']))
    for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        beams = {}
        for st, p in v:
            if p.beam:
                beams[angle_label(*p.beam)] = beams.get(angle_label(*p.beam), 0) + 1
        bs = u"（横担 %s）" % u"、".join(u"%s×%d" % (a, n) for a, n in sorted(beams.items())) if beams else u""
        lines.append(u"  · %s：%d 个%s" % (k, len(v), bs))
    if over:
        lines.append(u"其中 %d 个横担超过优先规格 %s（荷载大，已选更大角钢；也可以缩短间距）。" % (over, r["beam_pref_max"]))
    if pl['existing']:
        lines.append(u"旁边已有别的支吊架、跳过的站位 %d 个。" % pl['existing'])
    if pl['fails']:
        reasons = {}
        for st, why in pl['fails']:
            reasons.setdefault(why, 0)
            reasons[why] += 1
        lines.append(u"无法布置的站位 %d 个，原因：" % len(pl['fails']))
        for why, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:6]:
            lines.append(u"  · %d 个：%s" % (n, why))
    pr = pl['probe']
    if pr.ok:
        lines.append(u"土建探测使用三维视图「%s」（含链接模型；视图里被隐藏的土建探测不到）。" % _u(pr.view.Name))
    if pl['skipped']:
        by2 = {}
        for e, why in pl['skipped']:
            by2.setdefault(why, 0)
            by2[why] += 1
        lines.append(u"跳过的管线 %d 根：%s" % (len(pl['skipped']), u"；".join(u"%s %d" % (w, n) for w, n in by2.items())))
    return lines


def write_log(pl):
    try:
        if not os.path.isdir(RULES_DIR):
            os.makedirs(RULES_DIR)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(TITLE + u"\n")
            for st, p in pl['plans']:
                f.write(u"%s | 成员 %s | 重 %.0f kg | 宽 %.0f | %s\n" % (
                    p.desc, u",".join(u"%d" % m.id for m in st.active), st.total_kg, st.width_mm, u""))
            for st, why in pl['fails']:
                f.write(u"失败 成员 %s：%s\n" % (u",".join(u"%d" % m.id for m in st.active), why))
    except Exception:
        pass


# ============================================================
# 选择与界面
# ============================================================
CATS = (int(DB.BuiltInCategory.OST_PipeCurves), int(DB.BuiltInCategory.OST_DuctCurves), int(DB.BuiltInCategory.OST_CableTray))


class _MepFilter(ISelectionFilter):
    def AllowElement(self, e):
        try:
            return e.Category is not None and thl_eid(e.Category.Id) in CATS
        except Exception:
            return False

    def AllowReference(self, ref, p):
        return False


def selected_mep():
    out = []
    try:
        for i in uidoc.Selection.GetElementIds():
            e = doc.GetElement(i)
            if e is not None and e.Category is not None and thl_eid(e.Category.Id) in CATS:
                out.append(e)
    except Exception:
        pass
    return out


def pick_mep():
    try:
        return list(uidoc.Selection.PickElementsByRectangle(_MepFilter(), u"框选要布置支吊架的水管 / 风管 / 桥架，框完点「完成」（Esc 取消）"))
    except Exception:
        return None


def main_dialog(n_sel, r):
    """主界面：一屏。返回 (动作, 规则)；动作 run / delete / re-pick / None。"""
    import System.Windows.Forms as W
    import System.Drawing as D
    f = W.Form()
    f.Text = TITLE
    f.Font = D.Font(u"Microsoft YaHei UI", 9.0)
    f.StartPosition = W.FormStartPosition.CenterScreen
    f.FormBorderStyle = W.FormBorderStyle.FixedDialog
    f.MaximizeBox = False
    f.MinimizeBox = False
    f.ClientSize = D.Size(500, 330)
    st = {'act': None}

    def lab(text, x, y, w=460, h=22):
        l = W.Label()
        l.Text = text
        l.Location = D.Point(x, y)
        l.Size = D.Size(w, h)
        f.Controls.Add(l)
        return l
    lab(u"已选管线：%d 根（水管 / 风管 / 桥架）" % n_sel, 20, 16)
    b_re = W.Button()
    b_re.Text = u"重新框选"
    b_re.Location = D.Point(380, 12)
    b_re.Size = D.Size(100, 28)
    b_re.Click += lambda s, e: (st.__setitem__('act', 're-pick'), f.Close())
    f.Controls.Add(b_re)
    lab(u"支架形式", 20, 60, 100)
    cb = W.ComboBox()
    cb.DropDownStyle = W.ComboBoxStyle.DropDownList
    modes = [("auto", u"自动：按现场土建判定（推荐）"), ("hang", u"只用 U 型吊架"), ("stand", u"只用落地龙门架"), ("wall", u"只用墙边支架")]
    for k, t in modes:
        cb.Items.Add(t)
    cb.SelectedIndex = [k for k, t in modes].index(r["mode"]) if r["mode"] in [k for k, t in modes] else 0
    cb.Location = D.Point(130, 56)
    cb.Size = D.Size(350, 26)
    f.Controls.Add(cb)
    lab(u"上下都能依托时", 20, 100, 110)
    cp = W.ComboBox()
    cp.DropDownStyle = W.ComboBoxStyle.DropDownList
    prefs = [("short", u"取自由长度较短的"), ("hang", u"优先吊架"), ("stand", u"优先落地龙门架")]
    for k, t in prefs:
        cp.Items.Add(t)
    cp.SelectedIndex = [k for k, t in prefs].index(r["prefer"]) if r["prefer"] in [k for k, t in prefs] else 0
    cp.Location = D.Point(130, 96)
    cp.Size = D.Size(350, 26)
    f.Controls.Add(cp)
    lab(u"完成面补偿 mm", 20, 140, 110)
    tb = W.TextBox()
    tb.Text = u"%g" % r["finish_add_mm"]
    tb.Location = D.Point(130, 136)
    tb.Size = D.Size(100, 26)
    f.Controls.Add(tb)
    lab(u"落地支架底 = 地面顶 + 补偿（建筑模型的楼板已含面层填 0）", 240, 140, 250, 40)
    lab(u"间距系数 %", 20, 180, 110)
    ts = W.TextBox()
    ts.Text = u"%g" % r["span_scale_pct"]
    ts.Location = D.Point(130, 176)
    ts.Size = D.Size(100, 26)
    f.Controls.Add(ts)
    lab(u"100 = 按规范表间距，想密一点改小", 240, 180, 250, 22)
    lab(u"先预览数量和类型，确认后才生成；整次可 Ctrl+Z 撤销。\n荷载只做简化估算，不含抗震支吊架，重要部位请复核。", 20, 224, 460, 40)

    def ok(s, e):
        try:
            st['fin'] = float(tb.Text)
            st['scale'] = float(ts.Text)
        except Exception:
            forms.alert(u"完成面补偿、间距系数要填数字。", title=TITLE)
            return
        st['mode'] = modes[cb.SelectedIndex][0]
        st['prefer'] = prefs[cp.SelectedIndex][0]
        st['act'] = 'run'
        f.Close()
    b_ok = W.Button()
    b_ok.Text = u"预览并布置"
    b_ok.Location = D.Point(20, 280)
    b_ok.Size = D.Size(130, 34)
    b_ok.Click += ok
    f.Controls.Add(b_ok)
    b_del = W.Button()
    b_del.Text = u"删除已布置的"
    b_del.Location = D.Point(160, 280)
    b_del.Size = D.Size(110, 34)
    b_del.Click += lambda s, e: (st.__setitem__('act', 'delete'), f.Close())
    f.Controls.Add(b_del)
    b_set = W.Button()
    b_set.Text = u"规则设置…"
    b_set.Location = D.Point(280, 280)
    b_set.Size = D.Size(100, 34)
    b_set.Click += lambda s, e: (st.__setitem__('act', 'settings'), f.Close())
    f.Controls.Add(b_set)
    b_x = W.Button()
    b_x.Text = u"取消"
    b_x.Location = D.Point(390, 280)
    b_x.Size = D.Size(90, 34)
    b_x.Click += lambda s, e: f.Close()
    f.Controls.Add(b_x)
    f.AcceptButton = b_ok
    ff_fit(f)
    f.ShowDialog()
    out = dict(r)
    if st['act'] == 'run':
        out["mode"], out["prefer"], out["finish_add_mm"], out["span_scale_pct"] = st['mode'], st['prefer'], st['fin'], st['scale']
    return st['act'], out


def settings_dialog(r):
    while True:
        v = su_fields_dialog(TITLE + u" — 规则", u"这里是选型、间距、荷载的全部规则。改好点「保存」。", FIELDS, r, DEFAULT_RULES, width=720, height=700)
        if v is None:
            return r
        try:
            parse_table(v["pipe_table_ins"])
            parse_table(v["pipe_table_bare"])
        except ValueError as ex:
            forms.alert(_u(ex), title=TITLE)
            r = dict(r)
            r.update(v)
            continue
        out = dict(r)
        out.update(v)
        return out


def delete_flow(elems):
    ids = [thl_eid(e.Id) for e in elems]
    old = existing_mine_for(ids)
    if not old:
        forms.alert(u"选中的管线上没有本插件布置的支吊架。", title=TITLE)
        return
    ok, blocked = wg_split(doc, old)
    if not forms.alert(u"将删除选中管线上本插件布置的支吊架 %d 个。%s\n确认？" % (len(ok), wg_note(blocked)), title=TITLE, yes=True, no=True):
        return
    t = _tx(u"删除支吊架")
    t.Start()
    doc.Delete(List[DB.ElementId]([e.Id for e in ok]))
    t.Commit()
    forms.alert(u"已删除 %d 个。" % len(ok), title=TITLE)


def layout_flow(elems, r):
    pl = plan_all(elems, r)
    lines = summarize(pl, r)
    if not pl['plans']:
        forms.alert(u"没有可布置的支吊架。\n" + u"\n".join(lines), title=TITLE)
        return
    old = existing_mine_for([m.id for m in pl['members']])
    old_ok, old_blocked = wg_split(doc, old)
    if old_blocked:
        forms.alert(u"这些管线上已布置的支吊架有 %d 个被别人占用、删不了，为避免重复本次不布置。%s" % (len(old_blocked), wg_note(old_blocked)), title=TITLE)
        return
    if old:
        lines.append(u"这些管线上本插件以前布置的 %d 个支吊架会先删除再重新生成。" % len(old))
    lines.append(u"\n确认后开始生成？")
    if not forms.alert(u"\n".join(lines), title=TITLE + u" — 预览（还没改模型）", yes=True, no=True):
        return
    stats = execute(pl, r, old_ok)
    write_log(pl)
    rep = [u"完成：生成支吊架 %d 个，删除旧的 %d 个。" % (stats['made'], stats['deleted'])]
    if stats['fail']:
        rep.append(u"失败 %d 个：" % stats['fail'])
        rep.extend(u"  · " + x for x in stats['errors'])
    rep.append(u"明细：%s" % LOG_PATH)
    forms.alert(u"\n".join(rep), title=TITLE)


def main():
    try:
        r = load_rules()
        elems = selected_mep()
        if not elems:
            elems = pick_mep()
            if not elems:
                return
        while True:
            act, r2 = main_dialog(len(elems), r)
            if act == 're-pick':
                e2 = pick_mep()
                if e2:
                    elems = e2
                continue
            if act == 'settings':
                r = settings_dialog(r)
                save_rules(r)
                continue
            if act == 'delete':
                delete_flow(elems)
                return
            if act == 'run':
                save_rules(r2)
                layout_flow(elems, r2)
                return
            return
    except Exception as ex:
        forms.alert(u"出错了（已回滚，模型没被改）：\n%s\n\n%s" % (_u(ex), _u(traceback.format_exc())), title=TITLE)


if __name__ == '__main__':
    main()
