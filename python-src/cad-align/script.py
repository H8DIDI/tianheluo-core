# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "CAD 链接\n自动对位"
__author__ = "KAIFA"
__doc__ = ("把链接 / 导入的 DWG 按轴网自动对位到 Revit：从 DWG 里取长直线（优先图层名含 AXIS / DOTE / 轴 的轴线），和 Revit 轴网做「最佳位移」匹配，"
           "自动算出旋转（0/90/180/270 或轴网的任意朝向）和平移，移到位后锁定。\n"
           "两种用法：① 选中视图里已放的 CAD → 对位；② 选一个 DWG 文件 → 链接进当前平面视图（原点、毫米）并对位。"
           "先预览每个 CAD 的旋转、平移和匹配到的轴线数，确认后才动；匹配不够的不动并说明。整次可 Ctrl+Z。")

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

# @include _shared/compat.py

doc = revit.doc
uidoc = revit.uidoc
TITLE = u"CAD 链接自动对位"
FT = 304.8
MM = 1.0 / 304.8
RULES_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools")
RULES_PATH = os.path.join(RULES_DIR, u"CAD对位设置.json")
DEFAULTS = {"layer_keys": [u"AXIS", u"DOTE", u"轴", u"GRID"], "min_len_mm": 8000.0, "tol_mm": 30.0, "min_match": 3, "angle_tol_deg": 0.6}


def _u(x):
    try:
        return u"%s" % x
    except Exception:
        return u"?"


def load_rules():
    r = dict(DEFAULTS)
    try:
        if os.path.isfile(RULES_PATH):
            with open(RULES_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            for k in DEFAULTS:
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


# ============================================================
# 取线
# ============================================================
def revit_grid_lines():
    """Revit 轴网直线 -> [(x0,y0,x1,y1)]（ft）。"""
    out = []
    for g in DB.FilteredElementCollector(doc).OfClass(DB.Grid):
        c = g.Curve
        if isinstance(c, DB.Line):
            a, b = c.GetEndPoint(0), c.GetEndPoint(1)
            out.append((a.X, a.Y, b.X, b.Y))
    return out


def cad_lines(inst, r):
    """CAD 实例里的长直线（项目坐标，已含实例当前位置）。返回 (轴线图层上的, 全部长线)。"""
    opt = DB.Options()
    opt.ComputeReferences = False
    opt.DetailLevel = DB.ViewDetailLevel.Fine
    minlen = r["min_len_mm"] * MM
    keys = [_u(k).upper() for k in r["layer_keys"] if k]
    axis, anyl = [], []

    def layer_of(go):
        try:
            gs = doc.GetElement(go.GraphicsStyleId)
            return _u(gs.GraphicsStyleCategory.Name).upper()
        except Exception:
            return u""

    def add(a, b, layer):
        L = math.hypot(b.X - a.X, b.Y - a.Y)
        if L < minlen:
            return
        seg = (a.X, a.Y, b.X, b.Y)
        anyl.append(seg)
        if any(k in layer for k in keys):
            axis.append(seg)

    def walk(ge):
        for go in ge:
            if isinstance(go, DB.GeometryInstance):
                walk(go.GetInstanceGeometry())
            elif isinstance(go, DB.Line):
                add(go.GetEndPoint(0), go.GetEndPoint(1), layer_of(go))
            elif isinstance(go, DB.PolyLine):
                pts = list(go.GetCoordinates())
                ly = layer_of(go)
                for i in range(len(pts) - 1):
                    add(pts[i], pts[i + 1], ly)
    walk(inst.get_Geometry(opt))
    return axis, anyl


# ============================================================
# 匹配：旋转候选 + 两个方向各自投票求平移
# ============================================================
def _ang(s):
    return math.atan2(s[3] - s[1], s[2] - s[0]) % math.pi            # 0..π（直线无方向）


def _adiff(a, b):
    d = abs(a - b) % math.pi
    return min(d, math.pi - d)


def _families(segs, tol):
    """按走向分组 -> [[角度, [seg...]]]，数量多的在前。"""
    fams = []
    for s in sorted(segs, key=_ang):
        a = _ang(s)
        for f in fams:
            if _adiff(f[0], a) <= tol:
                f[1].append(s)
                break
        else:
            fams.append([a, [s]])
    fams.sort(key=lambda f: -len(f[1]))
    return fams


def _normal(a):
    return (-math.sin(a), math.cos(a))


def _pos(seg, n):
    return ((seg[0] + seg[2]) / 2.0) * n[0] + ((seg[1] + seg[3]) / 2.0) * n[1]


def _rot(seg, th):
    c, s = math.cos(th), math.sin(th)
    return (seg[0] * c - seg[1] * s, seg[0] * s + seg[1] * c, seg[2] * c - seg[3] * s, seg[2] * s + seg[3] * c)


def _vote(cad_pos, grid_pos, tol):
    """一维位移投票：返回 (最佳位移, 匹配数)。"""
    votes = {}
    for c in cad_pos:
        for g in grid_pos:
            k = int(round((g - c) / tol))
            votes[k] = votes.get(k, 0) + 1
    if not votes:
        return 0.0, 0
    best = max(votes.items(), key=lambda kv: kv[1])[0]
    ds = [g - c for c in cad_pos for g in grid_pos if abs((g - c) - best * tol) <= tol * 1.5]
    dm = sum(ds) / len(ds) if ds else best * tol
    cnt = 0
    for c in cad_pos:
        if any(abs(g - (c + dm)) <= tol for g in grid_pos):
            cnt += 1
    return dm, cnt


def solve(cad_segs, grid_segs, r):
    """返回 dict(th, tx, ty, m1, m2, n_fam)；解不出返回 None。th 弧度（绕原点逆时针），tx ty ft。"""
    tol = r["tol_mm"] * MM
    atol = math.radians(r["angle_tol_deg"])
    gf = _families(grid_segs, atol)
    cf = _families(cad_segs, atol)
    if not gf or not cf:
        return None
    use = []
    for g in gf:
        if len(use) >= 2:
            break
        if all(_adiff(g[0], u[0]) > math.radians(20) for u in use):
            use.append(g)
    cands = set()
    for g in gf[:3]:
        for c in cf[:3]:
            base = (g[0] - c[0]) % math.pi
            for k in (0, 1):
                cands.add(round(base + k * math.pi, 6))
    best = None
    for th in cands:
        crf = _families([_rot(s, th) for s in cad_segs], atol)
        res = []
        for g in use:
            n = _normal(g[0])
            cs = [s for f in crf for s in f[1] if _adiff(f[0], g[0]) <= atol]
            if not cs:
                continue
            cad_pos = sorted(set(round(_pos(s, n), 3) for s in cs))
            grid_pos = sorted(set(round(_pos(s, n), 3) for s in g[1]))
            dm, cnt = _vote(cad_pos, grid_pos, tol)
            res.append((n, dm, cnt))
        if not res:
            continue
        score = sum(x[2] for x in res)
        if best is None or score > best[0]:
            best = (score, th, res)
    if best is None:
        return None
    score, th, res = best
    if len(res) >= 2:
        (n1, d1, c1), (n2, d2, c2) = res[0], res[1]
        det = n1[0] * n2[1] - n1[1] * n2[0]
        if abs(det) < 1e-6:
            return None
        tx = (d1 * n2[1] - d2 * n1[1]) / det
        ty = (n1[0] * d2 - n2[0] * d1) / det
        return {'th': th, 'tx': tx, 'ty': ty, 'm1': c1, 'm2': c2, 'n_fam': 2}
    n1, d1, c1 = res[0]
    return {'th': th, 'tx': d1 * n1[0], 'ty': d1 * n1[1], 'm1': c1, 'm2': 0, 'n_fam': 1}


def analyse(inst, grid_segs, r):
    axis, anyl = cad_lines(inst, r)
    src = u"轴线图层" if len(axis) >= 4 else u"全部长线"
    segs = axis if len(axis) >= 4 else anyl
    if len(segs) < 4:
        return {'ok': False, 'why': u"DWG 里取不到足够的长直线（%d 条）" % len(segs)}
    sol = solve(segs, grid_segs, r)
    if sol is None:
        return {'ok': False, 'why': u"没有找到和轴网平行的线"}
    ok = sol['n_fam'] == 2 and sol['m1'] >= r["min_match"] and sol['m2'] >= r["min_match"]
    why = u"" if ok else u"匹配不够（两个方向各 %d / %d 条，至少各 %d 条），不动" % (sol['m1'], sol['m2'], r["min_match"])
    sol.update({'ok': ok, 'why': why, 'src': src, 'n_lines': len(segs)})
    return sol


# ============================================================
# 执行
# ============================================================
def apply_one(inst, sol):
    if inst.Pinned:
        inst.Pinned = False
    th_deg = (math.degrees(sol['th']) + 180.0) % 360.0 - 180.0
    if abs(th_deg) > 1e-6:
        DB.ElementTransformUtils.RotateElement(doc, inst.Id, DB.Line.CreateBound(DB.XYZ.Zero, DB.XYZ.BasisZ), sol['th'])
    DB.ElementTransformUtils.MoveElement(doc, inst.Id, DB.XYZ(sol['tx'], sol['ty'], 0))
    inst.Pinned = True


class _ImpFilter(ISelectionFilter):
    def AllowElement(self, e):
        return isinstance(e, DB.ImportInstance)

    def AllowReference(self, ref, p):
        return False


def selected_imports():
    out = []
    for i in uidoc.Selection.GetElementIds():
        e = doc.GetElement(i)
        if isinstance(e, DB.ImportInstance):
            out.append(e)
    return out


def pick_imports():
    try:
        refs = uidoc.Selection.PickObjects(UI.Selection.ObjectType.Element, _ImpFilter(), u"点选要对位的 CAD（可多选，选完点「完成」）")
        return [doc.GetElement(r) for r in refs]
    except Exception:
        return None


def link_file(path, view):
    o = DB.DWGImportOptions()
    o.Placement = DB.ImportPlacement.Origin
    o.ThisViewOnly = True
    o.Unit = DB.ImportUnit.Millimeter
    o.ColorMode = DB.ImportColorMode.Preserved
    o.OrientToView = False
    out_id = clr.Reference[DB.ElementId]()
    if doc.Link(path, o, view, out_id):
        return out_id.Value
    return None


def describe(name, sol):
    if not sol['ok']:
        return u"✗ %s：%s" % (name, sol.get('why'))
    return u"✓ %s：旋转 %.1f°，平移 (%.0f, %.0f) mm，匹配轴线 %d + %d 条（%s）" % (
        name, (math.degrees(sol['th']) + 180.0) % 360.0 - 180.0, sol['tx'] * FT, sol['ty'] * FT, sol['m1'], sol['m2'], sol['src'])


def _name(inst):
    try:
        t = doc.GetElement(inst.GetTypeId())
        return _u(t.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() or t.Name)
    except Exception:
        return _u(inst.Id)


def run_align(insts, r):
    grids = revit_grid_lines()
    if len(grids) < 2:
        forms.alert(u"项目里没有足够的轴网（至少 2 条），没法对位。", title=TITLE)
        return
    results = [(inst, _name(inst), analyse(inst, grids, r)) for inst in insts]
    good = [x for x in results if x[2]['ok']]
    lines = [describe(n, s) for i, n, s in results]
    if not good:
        forms.alert(u"\n".join(lines), title=TITLE)
        return
    lines.append(u"\n确认后对 %d 个 CAD 旋转 + 平移并锁定（整次可 Ctrl+Z）？" % len(good))
    if not forms.alert(u"\n".join(lines), title=TITLE + u" — 预览（还没动）", yes=True, no=True):
        return
    t = DB.Transaction(doc, TITLE)
    t.Start()
    n = 0
    try:
        for inst, name, sol in good:
            apply_one(inst, sol)
            n += 1
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    forms.alert(u"已对位 %d 个 CAD。" % n, title=TITLE)


def settings_flow(r):
    txt = forms.ask_for_string(default=u", ".join(r["layer_keys"]), prompt=u"轴线所在图层名里包含的关键字（逗号分隔，不分大小写）；\n取不到时会自动改用全部长线。", title=TITLE)
    if txt is None:
        return
    r["layer_keys"] = [x.strip() for x in txt.replace(u"，", u",").split(u",") if x.strip()]
    ml = forms.ask_for_string(default=u"%g" % r["min_len_mm"], prompt=u"取长于多少 mm 的线做匹配：", title=TITLE)
    tl = forms.ask_for_string(default=u"%g" % r["tol_mm"], prompt=u"匹配容差 mm：", title=TITLE)
    try:
        r["min_len_mm"], r["tol_mm"] = float(ml), float(tl)
    except Exception:
        pass
    save_rules(r)


def main():
    try:
        r = load_rules()
        ops = [u"对位已放好的 CAD（选中）", u"选一个 DWG 文件，链接进当前平面视图并对位", u"设置（轴线图层、最小长度、容差）"]
        mode = forms.CommandSwitchWindow.show(ops, message=u"请选择：")
        if mode is None:
            return
        if mode == ops[2]:
            settings_flow(r)
            return
        if mode == ops[0]:
            insts = selected_imports() or pick_imports()
            if insts:
                run_align(insts, r)
            return
        v = doc.ActiveView
        if not isinstance(v, DB.ViewPlan) or v.IsTemplate:
            forms.alert(u"请先切到要放 CAD 的平面视图（链接的 CAD 只在这个视图可见）。", title=TITLE)
            return
        path = forms.pick_file(file_ext='dwg', title=u"选要链接的 DWG")
        if not path:
            return
        t = DB.Transaction(doc, TITLE + u" 链接")
        t.Start()
        try:
            new_id = link_file(path, v)
            t.Commit()
        except Exception:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            raise
        if new_id is None:
            forms.alert(u"链接失败（Revit 拒绝了这个 DWG）。", title=TITLE)
            return
        run_align([doc.GetElement(new_id)], r)
    except Exception as ex:
        forms.alert(u"出错了：\n%s\n\n%s" % (ex, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
