# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "一键布置\n管道支墩"
__author__ = "KAIFA"
__doc__ = ("一键布置明装管道（HDPE / MSCL）的混凝土支墩与垫层。\n"
           "先框选要布置的明装管道再点插件（没选就先框选一次）。并排同走向的管自动合成一组，共用一个统一墩。\n"
           "间距按规则表：单根 HDPE、单根 MSCL 各一张表，MSCL + HDPE 混合组按 HDPE 管径查统一墩表；超出表格的管径先跳过并列出。\n"
           "支墩：沿管长 200，管外两侧各外延 100，管底以下 300，向上做到管中心，管座按管外径挖弧槽；"
           "地面（地形实体）更低的，墩加高落到地面。垫层：支墩四周各外延 100，厚 100。\n"
           "重新布置时先删这些管上旧的支墩和垫层；另有「删除选中管道的支墩」。规则运行前可改并保存。")

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

doc = revit.doc
uidoc = revit.uidoc

TITLE = u"一键布置管道支墩"
APP_ID = "PipeSupportPlugin"
FT = 304.8
MM = 1.0 / 304.8
RULES_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools")
RULES_PATH = os.path.join(RULES_DIR, u"管道支墩规则.json")
LOG_PATH = os.path.join(RULES_DIR, u"管道支墩日志.txt")
PIPE_CAT = int(DB.BuiltInCategory.OST_PipeCurves)
PIER_TAG = u"管道支墩"
PAD_TAG = u"支墩垫层"

DEFAULT_RULES = {
    "pier_len_mm": 200.0,        # 支墩沿管长
    "pier_side_mm": 100.0,       # 管外两侧外延
    "pier_below_mm": 300.0,      # 管底以下厚度
    "gasket_mm": 0.0,            # 橡胶垫厚（管座弧槽半径 = 管外径/2 + 垫厚）
    "pad_overhang_mm": 100.0,    # 垫层四周外延
    "pad_thick_mm": 100.0,       # 垫层厚
    "end_offset_mm": 300.0,      # 管段端头（弯头、三通处）第一个墩离端头的距离
    "group_gap_mm": 200.0,       # 两根管外壁净距不超过这个才算并排同组
    "group_dz_mm": 150.0,        # 同组管底高差上限
    "min_pier_gap_mm": 600.0,    # 两个墩中心距小于这个只留一个
    "to_ground": True,           # 地面（地形实体顶面）比「管底 - 300」低时，墩底做到地面
    "ground_max_mm": 3000.0,     # 最多加高这么多；地面更低的不加高，列入提示
    "hdpe_keys": [u"HDPE", u"PE"],
    "mscl_keys": [u"MSCL"],
    "mix_cap_by_hdpe": True,     # 混合组间距不超过组里最小 HDPE 的单管间距
    "material_key": u"混凝土",
    "hdpe_table": [u"50=0.9", u"65=1.0", u"80=1.2", u"100=1.3", u"125=1.5", u"150=1.7", u"200=2.0", u"250=2.3", u"300=2.5"],
    "mscl_table": [u"50=2.5", u"65=2.5", u"80=3.0", u"100=3.0", u"125=3.5", u"150=3.5", u"200=4.0", u"250=4.0", u"300=4.5"],
    "mix_table": [u"50=1.2", u"65=1.2", u"80=1.2", u"100=1.2", u"125=1.5", u"150=1.5", u"200=2.0", u"250=2.3", u"300=2.5"],
}


# ============================================================
# 小工具
# ============================================================
def _u(x):
    try:
        return u"%s" % x
    except Exception:
        return u"?"


def _idv(i):
    try:
        return i.Value
    except Exception:
        return i.IntegerValue


def _fmt(v):
    try:
        v = float(v)
        return (u"%d" % v) if abs(v - round(v)) < 1e-9 else (u"%g" % v)
    except Exception:
        return _u(v)


def parse_table(lines):
    """「DN=间距m」每行一项 -> {DN(int): 间距 mm}。写错的行抛 ValueError。"""
    out = {}
    for ln in lines or []:
        s = _u(ln).replace(u"：", u"=").replace(u":", u"=").replace(u"DN", u"").replace(u"dn", u"").strip()
        if not s:
            continue
        if u"=" not in s:
            raise ValueError(u"表格行「%s」要写成 DN=间距(m)，例如 200=2.0" % ln)
        a, b = s.split(u"=", 1)
        try:
            dn = int(round(float(a.strip())))
            sp = float(b.strip().rstrip(u"mM").strip()) * 1000.0
        except Exception:
            raise ValueError(u"表格行「%s」读不出数字" % ln)
        if dn <= 0 or sp <= 0:
            raise ValueError(u"表格行「%s」要大于 0" % ln)
        out[dn] = sp
    return out


def lookup(table, dn):
    """查间距 mm：正好有这个 DN 就用；在表的范围内但没有这一档，用比它小的最近一档（偏安全）；超出表格返回 None。"""
    if not table:
        return None
    if dn in table:
        return table[dn]
    keys = sorted(table)
    if dn < keys[0] or dn > keys[-1]:
        return None
    lower = [k for k in keys if k <= dn]
    return table[lower[-1]]


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


FIELDS = [
    {"kind": "header", "label": u"支墩与垫层尺寸（mm）"},
    {"key": "pier_len_mm", "label": u"支墩沿管长", "kind": "num", "min": 50, "max": 2000},
    {"key": "pier_side_mm", "label": u"管外两侧外延", "kind": "num", "min": 0, "max": 1000},
    {"key": "pier_below_mm", "label": u"管底以下厚度（最少）", "kind": "num", "min": 50, "max": 3000},
    {"key": "gasket_mm", "label": u"橡胶垫厚度", "kind": "num", "min": 0, "max": 50,
     "hint": u"管座弧槽半径 = 管外径/2 + 垫厚；填 0 就是混凝土直接贴管。"},
    {"key": "pad_overhang_mm", "label": u"垫层四周外延", "kind": "num", "min": 0, "max": 1000},
    {"key": "pad_thick_mm", "label": u"垫层厚度", "kind": "num", "min": 0, "max": 500,
     "hint": u"填 0 不生成垫层。"},
    {"kind": "header", "label": u"布置与分组（mm）"},
    {"key": "end_offset_mm", "label": u"端头第一个墩离管端", "kind": "num", "min": 0, "max": 3000},
    {"key": "group_gap_mm", "label": u"并排同组的最大净距", "kind": "num", "min": 0, "max": 2000,
     "hint": u"两根平行管外壁之间的净距不超过这个，就合成一组共用统一墩。"},
    {"key": "group_dz_mm", "label": u"同组管底最大高差", "kind": "num", "min": 0, "max": 1000},
    {"key": "min_pier_gap_mm", "label": u"两墩最小中心距", "kind": "num", "min": 100, "max": 5000,
     "hint": u"比这个近的两个墩只留一个（保留覆盖管子多的那个）。"},
    {"key": "to_ground", "label": u"墩底做到地面", "kind": "bool",
     "bool_text": u"地形实体顶面比「管底 − 管底以下厚度」低时，墩加高落到地面",
     "hint": u"按墩四角和中心下面的地形实体取最低点；墩下没有地形实体的不加高。"},
    {"key": "ground_max_mm", "label": u"最多加高", "kind": "num", "min": 0, "max": 20000,
     "hint": u"地面比这还低的墩不加高（照常管底以下 300），完成后列出数量。"},
    {"kind": "header", "label": u"管材识别（类型名 / 管段名里包含，逗号分隔，不分大小写）"},
    {"key": "hdpe_keys", "label": u"算作 HDPE", "kind": "list"},
    {"key": "mscl_keys", "label": u"算作 MSCL", "kind": "list"},
    {"key": "material_key", "label": u"支墩材质名包含", "kind": "text",
     "hint": u"项目里找名字含这个的材质给支墩和垫层；找不到就不设材质。"},
    {"kind": "header", "label": u"支墩最大间距表（每行 DN=间距 m；不在表格范围的管径跳过）"},
    {"key": "hdpe_table", "label": u"单根 / 全 HDPE 组", "kind": "lines", "rows": 6},
    {"key": "mscl_table", "label": u"单根 / 全 MSCL 组", "kind": "lines", "rows": 6},
    {"key": "mix_table", "label": u"MSCL + HDPE 混合组\n（按组里最小 HDPE 的 DN）", "kind": "lines", "rows": 6},
    {"key": "mix_cap_by_hdpe", "label": u"混合组安全取值", "kind": "bool",
     "bool_text": u"混合组间距不超过该 HDPE 的单管间距",
     "hint": u"混合表 DN50~80 的 1.2 m 比 HDPE 单管表大；勾选后取两者较小值。"},
]


def rules_dialog(r, n_sel):
    while True:
        v = su_fields_dialog(TITLE + u" — 规则", u"已选管道 %d 根。改好点「保存」开始预布置（预布置不改模型，会先告诉你数量）。" % n_sel,
                             FIELDS, r, DEFAULT_RULES, width=720, height=680)
        if v is None:
            return None
        try:
            for k in ("hdpe_table", "mscl_table", "mix_table"):
                parse_table(v[k])
        except ValueError as ex:
            forms.alert(_u(ex), title=TITLE)
            r = dict(r)
            r.update(v)
            continue
        out = dict(r)
        out.update(v)
        return out


# ============================================================
# 读管道
# ============================================================
def pipe_kind(e, r):
    """'H'（HDPE）、'M'（MSCL）或 None。按类型名，再按管段名。"""
    names = []
    try:
        t = doc.GetElement(e.GetTypeId())
        names.append(_u(t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() or u""))
    except Exception:
        pass
    try:
        sp = e.get_Parameter(DB.BuiltInParameter.RBS_PIPE_SEGMENT_PARAM)
        seg = doc.GetElement(sp.AsElementId()) if sp is not None else None
        if seg is not None:
            names.append(_u(seg.Name))
    except Exception:
        pass
    txt = u" ".join(names).upper()
    for k in r.get("mscl_keys") or []:
        if k and _u(k).upper() in txt:
            return 'M'
    for k in r.get("hdpe_keys") or []:
        if k and _u(k).upper() in txt:
            return 'H'
    return None


def read_pipe(e, r):
    """管道 -> dict，或 (None, 跳过原因)。"""
    loc = e.Location
    c = getattr(loc, 'Curve', None)
    if c is None or not isinstance(c, DB.Line):
        return None, u"不是直管段"
    a, b = c.GetEndPoint(0), c.GetEndPoint(1)
    dxy = DB.XYZ(b.X - a.X, b.Y - a.Y, 0)
    if dxy.GetLength() < 0.05:
        return None, u"竖管"
    if abs(b.Z - a.Z) / dxy.GetLength() > 0.2:
        return None, u"坡度太大（>20%）"
    kind = pipe_kind(e, r)
    if kind is None:
        return None, u"不是 HDPE / MSCL"
    p = e.get_Parameter(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM)
    dn = int(round(p.AsDouble() * FT)) if p is not None and p.HasValue else 0
    p = e.get_Parameter(DB.BuiltInParameter.RBS_PIPE_OUTER_DIAMETER)
    od = p.AsDouble() if p is not None and p.HasValue and p.AsDouble() > 1e-4 else dn * MM
    table = TABLES['H' if kind == 'H' else 'M']
    if lookup(table, dn) is None:
        return None, u"DN%d 超出间距表" % dn
    return {'e': e, 'id': _idv(e.Id), 'kind': kind, 'dn': dn, 'od': od, 'a': a, 'b': b,
            'dir': dxy.Normalize(), 'dir3': (b - a).Normalize()}, None


TABLES = {}


def set_tables(r):
    TABLES['H'] = parse_table(r["hdpe_table"])
    TABLES['M'] = parse_table(r["mscl_table"])
    TABLES['X'] = parse_table(r["mix_table"])


# ============================================================
# 分组：平行、净距够近、管底高差小、投影有重叠 -> 一组
# ============================================================
def _uf_find(par, i):
    while par[i] != i:
        par[i] = par[par[i]]
        i = par[i]
    return i


def group_pipes(pipes, r):
    gap = r["group_gap_mm"] * MM
    dzmax = r["group_dz_mm"] * MM
    cell = 5.0 / 0.3048
    grid = {}
    for i, p in enumerate(pipes):
        x0, x1 = sorted((p['a'].X, p['b'].X))
        y0, y1 = sorted((p['a'].Y, p['b'].Y))
        for gx in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
            for gy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                grid.setdefault((gx, gy), set()).add(i)
    par = list(range(len(pipes)))
    seen = set()
    for cellset in grid.values():
        ids = sorted(cellset)
        for ii in range(len(ids)):
            for jj in range(ii + 1, len(ids)):
                i, j = ids[ii], ids[jj]
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                if _same_group(pipes[i], pipes[j], gap, dzmax):
                    ri, rj = _uf_find(par, i), _uf_find(par, j)
                    if ri != rj:
                        par[rj] = ri
    groups = {}
    for i in range(len(pipes)):
        groups.setdefault(_uf_find(par, i), []).append(pipes[i])
    return list(groups.values())


def _same_group(p, q, gap, dzmax):
    d = p['dir']
    if abs(d.X * q['dir'].X + d.Y * q['dir'].Y) < 0.985:      # 夹角 > 约 10°
        return False
    n = DB.XYZ(-d.Y, d.X, 0)

    def uv(pt):
        w = pt - p['a']
        return w.X * d.X + w.Y * d.Y, w.X * n.X + w.Y * n.Y
    ua, va = uv(q['a'])
    ub, vb = uv(q['b'])
    lp = (p['b'] - p['a']).X * d.X + (p['b'] - p['a']).Y * d.Y
    lo, hi = min(ua, ub), max(ua, ub)
    if hi < -0.05 or lo > lp + 0.05:                           # 走向上不重叠（容 15 mm）
        return False
    clear = abs((va + vb) / 2.0) - (p['od'] + q['od']) / 2.0
    if clear > gap:
        return False
    zp = (p['a'].Z + p['b'].Z - p['od']) / 2.0
    zq = (q['a'].Z + q['b'].Z - q['od']) / 2.0
    return abs(zp - zq) <= dzmax


# ============================================================
# 一组的布墩：沿组的公共轴线排墩位
# ============================================================
class Member(object):
    """组里的一根管在组轴线坐标下：u 范围、v（横向偏移）、z（中心）都按 u 线性插值。"""

    def __init__(self, p, origin, ax):
        n = DB.XYZ(-ax.Y, ax.X, 0)
        self.p = p

        def uvz(pt):
            w = pt - origin
            return w.X * ax.X + w.Y * ax.Y, w.X * n.X + w.Y * n.Y, pt.Z
        A, B = uvz(p['a']), uvz(p['b'])
        if A[0] > B[0]:
            A, B = B, A
        self.u0, self.v0, self.z0 = A
        self.u1, self.v1, self.z1 = B
        self.od = p['od']

    def has(self, t):
        return self.u0 - 1e-6 <= t <= self.u1 + 1e-6

    def at(self, t):
        span = self.u1 - self.u0
        k = (t - self.u0) / span if span > 1e-9 else 0.0
        return self.v0 + k * (self.v1 - self.v0), self.z0 + k * (self.z1 - self.z0)


def spacing_for(members, r):
    """一组墩位处的管子 -> 最大间距 mm。"""
    hd = [m.p['dn'] for m in members if m.p['kind'] == 'H']
    ms = [m.p['dn'] for m in members if m.p['kind'] == 'M']
    if hd and ms:
        s = lookup(TABLES['X'], min(hd))
        h = lookup(TABLES['H'], min(hd))
        if s is None:
            s = h
        elif r.get("mix_cap_by_hdpe", True) and h is not None:
            s = min(s, h)
        return s
    if hd:
        return lookup(TABLES['H'], min(hd))
    return lookup(TABLES['M'], min(ms))


def plan_group(group, r, avoid):
    """返回 (墩位列表, 跳过的墩数)。墩位 = dict(t, members, origin, ax)。"""
    longest = max(group, key=lambda p: (p['b'] - p['a']).GetLength())
    ax = longest['dir']
    origin = DB.XYZ(longest['a'].X, longest['a'].Y, 0)
    mem = [Member(p, origin, ax) for p in group]
    e = r["end_offset_mm"] * MM
    half = r["pier_len_mm"] * MM / 2.0
    cuts = sorted(set([m.u0 for m in mem] + [m.u1 for m in mem]))
    stations = []
    for x0, x1 in zip(cuts[:-1], cuts[1:]):
        if x1 - x0 < 1e-3:
            continue
        mid = (x0 + x1) / 2.0
        here = [m for m in mem if m.has(mid)]
        if not here:
            continue
        s = spacing_for(here, r) * MM
        span = x1 - x0
        if span <= 2 * e + 1e-6:
            ts = [mid]
        else:
            inner = span - 2 * e
            n = int(math.ceil(inner / s - 1e-9))
            ts = [x0 + e + inner * k / n for k in range(n + 1)] if n > 0 else [mid]
        for t in ts:
            stations.append(t)
    stations.sort()
    # 太近的合并：保留覆盖管子多的
    gapmin = r["min_pier_gap_mm"] * MM
    kept = []
    for t in stations:
        cov = len([m for m in mem if m.has(t)])
        if kept and t - kept[-1][0] < gapmin:
            if cov > kept[-1][1]:
                kept[-1] = (t, cov)
            continue
        kept.append((t, cov))
    out = []
    skipped = 0
    for t, _c in kept:
        t2 = _shift_off_fittings(t, mem, origin, ax, half, avoid)
        if t2 is None:
            skipped += 1
            continue
        here = [m for m in mem if m.has(t2)]
        if here:
            out.append({'t': t2, 'members': here, 'origin': origin, 'ax': ax})
    return out, skipped


def _world(origin, ax, t, v, z):
    n = DB.XYZ(-ax.Y, ax.X, 0)
    return DB.XYZ(origin.X + ax.X * t + n.X * v, origin.Y + ax.Y * t + n.Y * v, z)


def _hits(t, mem, origin, ax, half, avoid):
    for m in mem:
        for tt in (t - half, t, t + half):
            if not m.has(tt):
                continue
            v, z = m.at(tt)
            p = _world(origin, ax, tt, v, z)
            if _on_fitting(avoid, p.X, p.Y, p.Z):
                return True
    return False


def _shift_off_fittings(t, mem, origin, ax, half, avoid):
    """墩压到管件 / 阀门上就沿管挪开（每次 100 mm，最多 1 m）；挪不开返回 None。"""
    if not _hits(t, mem, origin, ax, half, avoid):
        return t
    step = 0.1 / 0.3048
    lo = min(m.u0 for m in mem)
    hi = max(m.u1 for m in mem)
    for k in range(1, 11):
        for sgn in (1, -1):
            t2 = t + sgn * k * step
            if lo <= t2 <= hi and any(m.has(t2) for m in mem) and not _hits(t2, mem, origin, ax, half, avoid):
                return t2
    return None


AVOID_CELL = 5.0 / 0.3048
AVOID_MARGIN = 0.1 / 0.3048
AVOID_HALF = 1.0 / 0.3048


def build_avoid_grid():
    """管件、管道附件（阀门等）的平面范围，分桶。"""
    grid = {}
    for cat in (DB.BuiltInCategory.OST_PipeFitting, DB.BuiltInCategory.OST_PipeAccessory):
        for e in DB.FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType():
            try:
                bb = e.get_BoundingBox(None)
            except Exception:
                bb = None
            if bb is None:
                continue
            pt = getattr(e.Location, 'Point', None)
            c = pt if pt is not None else (bb.Min + bb.Max) * 0.5
            hx = min((bb.Max.X - bb.Min.X) / 2.0, AVOID_HALF)
            hy = min((bb.Max.Y - bb.Min.Y) / 2.0, AVOID_HALF)
            box = (c.X - hx, c.Y - hy, max(bb.Min.Z, c.Z - AVOID_HALF), c.X + hx, c.Y + hy, min(bb.Max.Z, c.Z + AVOID_HALF))
            for gx in range(int(math.floor((box[0] - AVOID_MARGIN) / AVOID_CELL)), int(math.floor((box[3] + AVOID_MARGIN) / AVOID_CELL)) + 1):
                for gy in range(int(math.floor((box[1] - AVOID_MARGIN) / AVOID_CELL)), int(math.floor((box[4] + AVOID_MARGIN) / AVOID_CELL)) + 1):
                    grid.setdefault((gx, gy), []).append(box)
    return grid


def _on_fitting(grid, x, y, z):
    if not grid:
        return False
    for x0, y0, z0, x1, y1, z1 in grid.get((int(math.floor(x / AVOID_CELL)), int(math.floor(y / AVOID_CELL))), []):
        if (x0 - AVOID_MARGIN <= x <= x1 + AVOID_MARGIN and y0 - AVOID_MARGIN <= y <= y1 + AVOID_MARGIN
                and z0 - 1.0 <= z <= z1 + 1.0):
            return True
    return False


# ============================================================
# 几何：支墩（挖管座）+ 垫层
# ============================================================
def pier_dims(st, r):
    """一个墩位 -> 尺寸（内部单位 ft）：横向中心 vc、宽 W、底 zb、顶 zt。"""
    side = r["pier_side_mm"] * MM
    vs, zbs, zcs = [], [], []
    for m in st['members']:
        v, zc = m.at(st['t'])
        vs.append((v - m.od / 2.0, v + m.od / 2.0))
        zcs.append(zc)
        zbs.append(zc - m.od / 2.0)
    vmin = min(a for a, b in vs) - side
    vmax = max(b for a, b in vs) + side
    zb = min(zbs) - r["pier_below_mm"] * MM
    zt = min(zcs)
    g = st.get('ground')
    if r.get("to_ground", True) and g is not None and g < zb and (zb - g) <= r.get("ground_max_mm", 3000.0) * MM:
        zb = g
    return (vmin + vmax) / 2.0, vmax - vmin, zb, zt


def pier_corners(st, r):
    """墩平面四角 + 中心（世界坐标 XY）。"""
    g = st.pop('ground', None)
    vc, W, zb, zt = pier_dims(st, r)
    if g is not None:
        st['ground'] = g
    half = r["pier_len_mm"] * MM / 2.0
    pts = [_world(st['origin'], st['ax'], st['t'], vc, 0)]
    for du in (-half, half):
        for dv in (-W / 2.0, W / 2.0):
            pts.append(_world(st['origin'], st['ax'], st['t'] + du, vc + dv, 0))
    return pts, zb


# ============================================================
# 地面：地形实体朝上的面，分桶；竖线求交取最高点
# ============================================================
GROUND_CELL = 20.0 / 0.3048


def build_ground():
    faces, grid = [], {}
    for t in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_Toposolid).WhereElementIsNotElementType():
        try:
            geo = t.get_Geometry(DB.Options())
        except Exception:
            continue
        for o in geo or []:
            if not isinstance(o, DB.Solid):
                continue
            for f in o.Faces:
                try:
                    bb = f.GetBoundingBox()
                    nrm = f.ComputeNormal((bb.Min + bb.Max) * 0.5)
                    if nrm.Z <= 0.2:
                        continue
                    vs = list(f.Triangulate().Vertices)
                except Exception:
                    continue
                if not vs:
                    continue
                box = (min(v.X for v in vs), min(v.Y for v in vs), max(v.X for v in vs), max(v.Y for v in vs))
                faces.append((box, f))
    for i, (box, f) in enumerate(faces):
        for gx in range(int(math.floor(box[0] / GROUND_CELL)), int(math.floor(box[2] / GROUND_CELL)) + 1):
            for gy in range(int(math.floor(box[1] / GROUND_CELL)), int(math.floor(box[3] / GROUND_CELL)) + 1):
                grid.setdefault((gx, gy), []).append(i)
    return faces, grid


def ground_at(gd, x, y):
    """(x, y) 处地形实体顶面标高（ft）；下面没有地形实体返回 None。"""
    faces, grid = gd
    best = None
    for i in grid.get((int(math.floor(x / GROUND_CELL)), int(math.floor(y / GROUND_CELL))), []):
        box, f = faces[i]
        if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
            continue
        ra = clr.Reference[DB.IntersectionResultArray]()
        try:
            res = f.Intersect(DB.Line.CreateBound(DB.XYZ(x, y, -3000.0), DB.XYZ(x, y, 3000.0)), ra)
        except Exception:
            continue
        if res == DB.SetComparisonResult.Overlap and ra.Value is not None:
            for k in range(ra.Value.Size):
                z = ra.Value.get_Item(k).XYZPoint.Z
                if best is None or z > best:
                    best = z
    return best


def attach_ground(stations, r, gd):
    """每个墩记下墩下地面最低点；返回 (加高的墩数, 地面太低没加高的墩数)。"""
    up = deep = 0
    if not r.get("to_ground", True) or not gd[0]:
        return 0, 0
    for st in stations:
        pts, zb = pier_corners(st, r)
        gs = [g for g in (ground_at(gd, p.X, p.Y) for p in pts) if g is not None]
        if not gs:
            continue
        g = min(gs)
        if g < zb - 1e-6:
            if (zb - g) <= r.get("ground_max_mm", 3000.0) * MM:
                st['ground'] = g
                up += 1
            else:
                deep += 1
    return up, deep


def _box(origin, ax, t, half_len, vc, half_w, z0, h, opts):
    n = DB.XYZ(-ax.Y, ax.X, 0)
    pts = []
    for du, dv in ((-half_len, -half_w), (half_len, -half_w), (half_len, half_w), (-half_len, half_w)):
        pts.append(DB.XYZ(origin.X + ax.X * (t + du) + n.X * (vc + dv),
                          origin.Y + ax.Y * (t + du) + n.Y * (vc + dv), z0))
    loop = DB.CurveLoop()
    for i in range(4):
        loop.Append(DB.Line.CreateBound(pts[i], pts[(i + 1) % 4]))
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(List[DB.CurveLoop]([loop]), DB.XYZ.BasisZ, h, opts)


def _cylinder(center, d3, radius, length):
    """沿 d3 方向、以 center 为中点的圆柱。"""
    ref = DB.XYZ.BasisZ if abs(d3.Z) < 0.9 else DB.XYZ.BasisX
    x = d3.CrossProduct(ref).Normalize()
    y = d3.CrossProduct(x).Normalize()
    c0 = center - d3 * (length / 2.0)
    loop = DB.CurveLoop()
    loop.Append(DB.Arc.Create(c0, radius, 0.0, math.pi, x, y))
    loop.Append(DB.Arc.Create(c0, radius, math.pi, 2 * math.pi, x, y))
    return DB.GeometryCreationUtilities.CreateExtrusionGeometry(List[DB.CurveLoop]([loop]), d3, length)


def build_pier_solids(st, r, mat_id):
    """返回 (支墩实体, 垫层实体或 None, 尺寸 dict, 管座是否挖成)。"""
    origin, ax, t = st['origin'], st['ax'], st['t']
    vc, W, zb, zt = pier_dims(st, r)
    L = r["pier_len_mm"] * MM
    opts = DB.SolidOptions(mat_id, DB.ElementId.InvalidElementId)
    pier = _box(origin, ax, t, L / 2.0, vc, W / 2.0, zb, zt - zb, opts)
    saddle = True
    rad_add = r["gasket_mm"] * MM
    for m in st['members']:
        v, zc = m.at(t)
        c = _world(origin, ax, t, v, zc)
        cyl = _cylinder(c, m.p['dir3'], m.od / 2.0 + rad_add, L + 0.4)
        try:
            DB.BooleanOperationsUtils.ExecuteBooleanOperationModifyingOriginalSolid(
                pier, cyl, DB.BooleanOperationsType.Difference)
        except Exception:
            saddle = False
    pad = None
    if r["pad_thick_mm"] > 0:
        o = r["pad_overhang_mm"] * MM
        th = r["pad_thick_mm"] * MM
        pad = _box(origin, ax, t, L / 2.0 + o, vc, W / 2.0 + o, zb - th, th, opts)
    dims = {'W': W * FT, 'L': L * FT, 'H': (zt - zb) * FT, 'zb': zb, 'zt': zt}
    return pier, pad, dims, saddle


def _ds(solid, name, comment, data_id):
    ds = DB.DirectShape.CreateElement(doc, DB.ElementId(DB.BuiltInCategory.OST_StructuralFoundation))
    ds.ApplicationId = APP_ID
    ds.ApplicationDataId = data_id
    ds.SetShape(List[DB.GeometryObject]([solid]))
    ds.Name = name
    p = ds.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    if p is not None and not p.IsReadOnly:
        p.Set(comment)
    return ds


def _host_text(st):
    return u",".join(u"%d" % m.p['id'] for m in st['members'])


def _desc(st):
    parts = []
    for m in sorted(st['members'], key=lambda m: m.at(st['t'])[0]):
        parts.append(u"%s DN%d" % (u"HDPE" if m.p['kind'] == 'H' else u"MSCL", m.p['dn']))
    return u" + ".join(parts)


def create_pier(st, r, mat_id):
    pier, pad, dims, saddle = build_pier_solids(st, r, mat_id)
    hosts = _host_text(st)
    tag = u"%dx%dx%d" % (round(dims['W']), round(dims['L']), round(dims['H']))
    made = 0
    if pier is not None and pier.Volume > 1e-6:
        _ds(pier, u"%s %s" % (PIER_TAG, tag),
            u"%s %s (%s) | 宿主: 管道 (Id:%s)" % (PIER_TAG, tag, _desc(st), hosts),
            u"Pier_Host_%s_%s" % (hosts.replace(u",", u"_"), System.Guid.NewGuid()))
        made += 1
    if pad is not None and pad.Volume > 1e-6:
        _ds(pad, u"%s (%dmm厚, 外挑%dmm)" % (PAD_TAG, round(r["pad_thick_mm"]), round(r["pad_overhang_mm"])),
            u"%s %d厚 | 宿主: 管道 (Id:%s)" % (PAD_TAG, round(r["pad_thick_mm"]), hosts),
            u"Pad_Host_%s_%s" % (hosts.replace(u",", u"_"), System.Guid.NewGuid()))
        made += 1
    return made, saddle, dims


def find_material(key):
    if not key:
        return DB.ElementId.InvalidElementId
    best = None
    for m in DB.FilteredElementCollector(doc).OfClass(DB.Material):
        nm = _u(m.Name)
        if key in nm and (best is None or len(nm) < len(_u(best.Name))):
            best = m
    return best.Id if best is not None else DB.ElementId.InvalidElementId


# ============================================================
# 已有支墩：按宿主管道 Id 找
# ============================================================
def _hosts_of(e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        c = _u(p.AsString() or u"") if p is not None else u""
    except Exception:
        c = u""
    i = c.find(u"(Id:")
    if i < 0:
        return set()
    j = c.find(u")", i)
    out = set()
    for s in c[i + 4:j].split(u","):
        try:
            out.add(int(s.strip()))
        except Exception:
            pass
    return out


def existing_supports(pipe_ids):
    """项目里本插件生成的、宿主含这些管道之一的支墩与垫层。"""
    out = []
    for e in DB.FilteredElementCollector(doc).OfClass(DB.DirectShape):
        try:
            if e.ApplicationId != APP_ID:
                continue
        except Exception:
            continue
        if _hosts_of(e) & pipe_ids:
            out.append(e)
    return out


# ============================================================
# 事务
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


# ============================================================
# 流程
# ============================================================
class _PipeFilter(ISelectionFilter):
    def AllowElement(self, e):
        try:
            return e.Category is not None and _idv(e.Category.Id) == PIPE_CAT
        except Exception:
            return False

    def AllowReference(self, ref, p):
        return False


def selected_pipes():
    out = []
    try:
        for i in uidoc.Selection.GetElementIds():
            e = doc.GetElement(i)
            if e is not None and e.Category is not None and _idv(e.Category.Id) == PIPE_CAT:
                out.append(e)
    except Exception:
        pass
    return out


def pick_pipes():
    try:
        return list(uidoc.Selection.PickElementsByRectangle(_PipeFilter(), u"框选要布置支墩的明装管道，框完点「完成」（Esc 取消）"))
    except Exception:
        return None


def plan(elems, r, avoid=None):
    """只读：读管、分组、排墩位。返回 dict。"""
    set_tables(r)
    pipes, skipped = [], []
    for e in elems:
        p, why = read_pipe(e, r)
        if p is None:
            skipped.append((e, why))
        else:
            pipes.append(p)
    groups = group_pipes(pipes, r) if pipes else []
    if avoid is None:
        avoid = build_avoid_grid()
    stations, blocked = [], 0
    for g in groups:
        sts, sk = plan_group(g, r, avoid)
        stations.extend(sts)
        blocked += sk
    up, deep = attach_ground(stations, r, build_ground()) if stations and r.get("to_ground", True) else (0, 0)
    return {'pipes': pipes, 'skipped': skipped, 'groups': groups, 'stations': stations, 'blocked': blocked,
            'raised': up, 'too_deep': deep}


CHUNK = 500


def execute(pl, r, old):
    """写模型：删旧支墩，按墩位生成。整次一个事务（每个墩一个子事务，坏一个只回滚它）。"""
    stats = {'piers': 0, 'pads': 0, 'deleted': 0, 'fail': 0, 'nosaddle': 0, 'errors': [], 'heights': []}
    mat = find_material(r.get("material_key"))
    t = _tx(TITLE)
    t.Start()
    try:
        if old:
            ids = List[DB.ElementId]([e.Id for e in old])
            doc.Delete(ids)
            stats['deleted'] = len(old)
        for k, st in enumerate(pl['stations']):
            if k and k % CHUNK == 0:          # 墩多时分批提交，避免一个事务太大
                t.Commit()
                t = _tx(TITLE)
                t.Start()
            sub = DB.SubTransaction(doc)
            sub.Start()
            try:
                made, saddle, dims = create_pier(st, r, mat)
                sub.Commit()
                stats['piers'] += 1 if made else 0
                stats['pads'] += max(0, made - 1)
                stats['heights'].append(dims['H'])
                if not saddle:
                    stats['nosaddle'] += 1
            except Exception as ex:
                try:
                    sub.RollBack()
                except Exception:
                    pass
                stats['fail'] += 1
                if len(stats['errors']) < 10:
                    stats['errors'].append(u"%s：%s" % (_host_text(st), _u(ex)))
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return stats


def _skip_summary(skipped):
    by = {}
    for e, why in skipped:
        by.setdefault(why, []).append(e)
    lines = []
    for why, es in sorted(by.items(), key=lambda kv: -len(kv[1])):
        ids = u"、".join(u"%d" % _idv(e.Id) for e in es[:5])
        lines.append(u"  · %s：%d 根（%s%s）" % (why, len(es), ids, u" 等" if len(es) > 5 else u""))
    return lines


def write_log(pl, stats):
    try:
        if not os.path.isdir(RULES_DIR):
            os.makedirs(RULES_DIR)
        with open(LOG_PATH, "w", encoding="utf-8") as f:
            f.write(u"%s\n" % TITLE)
            for e, why in pl['skipped']:
                f.write(u"跳过 %d  %s\n" % (_idv(e.Id), why))
            for st in pl['stations']:
                vc, W, zb, zt = pier_dims(st, RULES)
                f.write(u"墩 t=%.0f  %s  宽%.0f 高%.0f  底标高%.0f  宿主 %s\n" % (st['t'] * FT, _desc(st), W * FT, (zt - zb) * FT, zb * FT, _host_text(st)))
            for x in (stats or {}).get('errors', []):
                f.write(u"失败 %s\n" % x)
    except Exception:
        pass


RULES = {}


def layout_flow():
    r = load_rules()
    elems = selected_pipes()
    if not elems:
        elems = pick_pipes()
        if not elems:
            return
    r2 = rules_dialog(r, len(elems))
    if r2 is None:
        return
    save_rules(r2)
    RULES.clear()
    RULES.update(r2)
    pl = plan(elems, r2)
    if not pl['stations']:
        msg = [u"没有可布置的支墩。"] + _skip_summary(pl['skipped'])
        forms.alert(u"\n".join(msg), title=TITLE)
        return
    ids = set(p['id'] for p in pl['pipes'])
    old = existing_supports(ids)
    old_ok, old_blocked = wg_split(doc, old)
    if old_blocked:
        forms.alert(u"这些管上已有的支墩有 %d 个被别人占用、删不了，为避免重复，本次不布置。%s" % (len(old_blocked), wg_note(old_blocked)), title=TITLE)
        return
    n_mix = sum(1 for g in pl['groups'] if len(set(p['kind'] for p in g)) > 1)
    n_multi = sum(1 for g in pl['groups'] if len(g) > 1)
    q = [u"预布置完成（还没有改模型）：",
         u"可布置管道 %d 根，分成 %d 组（多根并排的 %d 组，其中 MSCL + HDPE 混合 %d 组）。" % (len(pl['pipes']), len(pl['groups']), n_multi, n_mix),
         u"将生成支墩 %d 个%s。" % (len(pl['stations']), u"、垫层 %d 块" % len(pl['stations']) if r2["pad_thick_mm"] > 0 else u""),
         u"支墩：沿管长 %s，两侧外延 %s，管底以下 %s，做到管中心；垫层外延 %s、厚 %s（mm）。" % (
             _fmt(r2["pier_len_mm"]), _fmt(r2["pier_side_mm"]), _fmt(r2["pier_below_mm"]), _fmt(r2["pad_overhang_mm"]), _fmt(r2["pad_thick_mm"]))]
    if pl.get('raised'):
        q.append(u"地面比管底以下 %s 还低的墩 %d 个，加高落到地面。" % (_fmt(r2["pier_below_mm"]), pl['raised']))
    if pl.get('too_deep'):
        q.append(u"地面低于最多加高 %s 的墩 %d 个，不加高（照常管底以下 %s）。" % (_fmt(r2["ground_max_mm"]), pl['too_deep'], _fmt(r2["pier_below_mm"])))
    if pl['blocked']:
        q.append(u"压在管件 / 阀门上、挪 1 m 内也避不开的墩位 %d 个，不布置。" % pl['blocked'])
    if old:
        q.append(u"这些管上已有支墩 / 垫层 %d 个，会先删除再重新生成。" % len(old))
    if pl['skipped']:
        q.append(u"跳过的管道 %d 根：" % len(pl['skipped']))
        q.extend(_skip_summary(pl['skipped']))
    q.append(u"确认后开始生成？（整次可 Ctrl+Z 撤销）")
    if not forms.alert(u"\n".join(q), title=TITLE, yes=True, no=True):
        return
    stats = execute(pl, r2, old_ok)
    write_log(pl, stats)
    hs = stats['heights']
    rep = [u"完成：支墩 %d 个，垫层 %d 块；删除旧的 %d 个。" % (stats['piers'], stats['pads'], stats['deleted'])]
    if hs:
        rep.append(u"支墩高度 %.0f ~ %.0f mm。" % (min(hs), max(hs)))
    if stats['nosaddle']:
        rep.append(u"有 %d 个墩管座弧槽没挖成（保留成方墩），请检查。" % stats['nosaddle'])
    if stats['fail']:
        rep.append(u"失败 %d 个：" % stats['fail'])
        rep.extend(u"  · " + x for x in stats['errors'])
    rep.append(u"明细日志：%s" % LOG_PATH)
    forms.alert(u"\n".join(rep), title=TITLE)


def delete_flow():
    elems = selected_pipes()
    if not elems:
        elems = pick_pipes()
        if not elems:
            return
    ids = set(_idv(e.Id) for e in elems)
    old = existing_supports(ids)
    if not old:
        forms.alert(u"选中的 %d 根管道上没有本插件生成的支墩。" % len(elems), title=TITLE)
        return
    ok, blocked = wg_split(doc, old)
    if not forms.alert(u"将删除选中管道上的支墩与垫层 %d 个。%s\n确认？" % (len(ok), wg_note(blocked)), title=TITLE, yes=True, no=True):
        return
    t = _tx(u"删除管道支墩")
    t.Start()
    doc.Delete(List[DB.ElementId]([e.Id for e in ok]))
    t.Commit()
    forms.alert(u"已删除 %d 个。" % len(ok), title=TITLE)


OP_LAYOUT = u"布置管道支墩（框选明装管道）"
OP_DELETE = u"删除选中管道的支墩与垫层"


def main():
    try:
        op = forms.CommandSwitchWindow.show([OP_LAYOUT, OP_DELETE], message=u"请选择操作：")
        if op == OP_LAYOUT:
            layout_flow()
        elif op == OP_DELETE:
            delete_flow()
    except Exception as ex:
        forms.alert(u"出错了（已回滚，模型没被改）：\n%s\n\n%s" % (_u(ex), _u(traceback.format_exc())), title=TITLE)


if __name__ == '__main__':
    main()
