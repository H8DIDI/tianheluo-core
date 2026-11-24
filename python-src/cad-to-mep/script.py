# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "CAD 线\n生成管线"
__author__ = "KAIFA"
__doc__ = ("把链接 / 导入的 DWG 里某个图层的直线，一键生成 Revit 水管、风管或桥架：选 CAD → 选图层 → 选类型、系统、尺寸、标高 → 生成。\n"
           "· 同一图层的所有直线按同一个类型 / 系统 / 尺寸 / 标高生成（不同管径、不同标高的管分几次做，每次选不同图层或用「框选范围」）。\n"
           "· 只生成直管段，不自动连弯头（实测大模型里每个接头要好几秒，短管段还会被 Revit 判为反向而整批回滚）；连接请用「管线连续性检查」或 Revit 自带的修剪 / 延伸。\n"
           "· 生成的管线备注写「AUTO-CAD 图层名」，之后可用「管线清理」去重叠、「机电模型体检」检查。\n"
           "预览数量后才生成，整次可 Ctrl+Z 撤销。")

import math
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms
from Autodesk.Revit.UI.Selection import ISelectionFilter
import System.Windows.Forms as W
import System.Drawing as D

# @include _shared/form_fit.py
# @include _shared/compat.py

doc = revit.doc
uidoc = revit.uidoc
TITLE = u"CAD 线生成管线"
FT = 304.8
MM = 1.0 / 304.8
TAG = u"AUTO-CAD"


def _u(x):
    try:
        return u"%s" % x
    except Exception:
        return u"?"


# ============================================================
# 读 CAD：按图层收集直线
# ============================================================
class _ImpFilter(ISelectionFilter):
    def AllowElement(self, e):
        return isinstance(e, DB.ImportInstance)

    def AllowReference(self, ref, p):
        return False


def pick_import():
    for i in uidoc.Selection.GetElementIds():
        e = doc.GetElement(i)
        if isinstance(e, DB.ImportInstance):
            return e
    try:
        r = uidoc.Selection.PickObject(UI.Selection.ObjectType.Element, _ImpFilter(), u"点选要读取的 CAD")
        return doc.GetElement(r)
    except Exception:
        return None


def lines_by_layer(inst, min_len_mm=50.0):
    """{图层名: [(x0,y0,x1,y1)]}（项目坐标 ft，z 忽略）。"""
    opt = DB.Options()
    opt.ComputeReferences = False
    opt.DetailLevel = DB.ViewDetailLevel.Fine
    out = {}

    def layer_of(go):
        try:
            return _u(doc.GetElement(go.GraphicsStyleId).GraphicsStyleCategory.Name)
        except Exception:
            return u"(未知图层)"

    def add(a, b, ly):
        if math.hypot(b.X - a.X, b.Y - a.Y) < min_len_mm * MM:
            return
        out.setdefault(ly, []).append((a.X, a.Y, b.X, b.Y))

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
    return out


# ============================================================
# 类型
# ============================================================
def _name(e):
    try:
        return _u(e.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM).AsString() or e.Name)
    except Exception:
        return _u(e.Name)


def list_types(kind):
    """返回 (线类型列表, 系统类型列表)；桥架没有系统类型。"""
    if kind == u"pipe":
        return (sorted(DB.FilteredElementCollector(doc).OfClass(DB.Plumbing.PipeType), key=_name),
                sorted(DB.FilteredElementCollector(doc).OfClass(DB.Plumbing.PipingSystemType), key=_name))
    if kind == u"duct":
        return (sorted(DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.DuctType), key=_name),
                sorted(DB.FilteredElementCollector(doc).OfClass(DB.Mechanical.MechanicalSystemType), key=_name))
    return (sorted(DB.FilteredElementCollector(doc).OfClass(DB.Electrical.CableTrayType), key=_name), [])


def parse_size(kind, text):
    """水管 '100'（DN mm）；风管 '500x250' 或 'd300'；桥架 '300x100'。返回 dict。"""
    t = _u(text).lower().replace(u"×", u"x").replace(u"*", u"x").replace(u"φ", u"d").replace(u"ø", u"d").replace(u"dn", u"").strip()
    if kind == u"pipe":
        return {'d': float(t)}
    if t.startswith(u"d"):
        return {'d': float(t[1:])}
    a, b = t.split(u"x")
    return {'w': float(a), 'h': float(b)}


def set_size(e, kind, size):
    def setp(bip, mm):
        p = e.get_Parameter(bip)
        if p is not None and not p.IsReadOnly:
            p.Set(mm * MM)
            return True
        return False
    if kind == u"pipe":
        setp(DB.BuiltInParameter.RBS_PIPE_DIAMETER_PARAM, size['d'])
    elif kind == u"duct":
        if 'd' in size:
            setp(DB.BuiltInParameter.RBS_CURVE_DIAMETER_PARAM, size['d'])
        else:
            setp(DB.BuiltInParameter.RBS_CURVE_WIDTH_PARAM, size['w'])
            setp(DB.BuiltInParameter.RBS_CURVE_HEIGHT_PARAM, size['h'])
    else:
        setp(DB.BuiltInParameter.RBS_CABLETRAY_WIDTH_PARAM, size['w'])
        setp(DB.BuiltInParameter.RBS_CABLETRAY_HEIGHT_PARAM, size['h'])


# ============================================================
# 生成
# ============================================================
def create_one(kind, seg, z, level, tid, sid):
    a = DB.XYZ(seg[0], seg[1], z)
    b = DB.XYZ(seg[2], seg[3], z)
    if kind == u"pipe":
        return DB.Plumbing.Pipe.Create(doc, sid, tid, level.Id, a, b)
    if kind == u"duct":
        return DB.Mechanical.Duct.Create(doc, sid, tid, level.Id, a, b)
    return DB.Electrical.CableTray.Create(doc, tid, a, b, level.Id)


def _conns(e):
    try:
        return list(e.ConnectorManager.Connectors)
    except Exception:
        return []


def connect_ends(made, kind):
    """端点重合（<2 mm）的两段尝试生成接头。不用子事务（大模型里每个子事务都要重算，慢到不能用），
    先全部生成，再把「没接满」的接头删掉，免得留孤儿管件。返回 (成功, 失败)。"""
    pts = []
    for e in made:
        for c in _conns(e):
            if not c.IsConnected:
                pts.append((c, c.Origin))
    used = set()
    tol = 2.0 * MM
    fits, fail = [], 0
    for i in range(len(pts)):
        if i in used:
            continue
        for j in range(i + 1, len(pts)):
            if j in used:
                continue
            ci, pi = pts[i]
            cj, pj = pts[j]
            try:
                # 前面生成接头时 Revit 可能替换 / 合并了管段，旧的接口对象就失效了，跳过
                if not (ci.Owner.IsValidObject and cj.Owner.IsValidObject):
                    continue
                if ci.Owner.Id == cj.Owner.Id or pi.DistanceTo(pj) > tol or ci.IsConnected or cj.IsConnected:
                    continue
            except Exception:
                continue
            try:
                if kind != u"tray" and abs(ci.CoordinateSystem.BasisZ.DotProduct(cj.CoordinateSystem.BasisZ) + 1.0) < 1e-3:
                    fit = doc.Create.NewUnionFitting(ci, cj)         # 共线对接
                else:
                    fit = doc.Create.NewElbowFitting(ci, cj)
                if fit is not None:
                    fits.append(fit)
                    used.add(i)
                    used.add(j)
                else:
                    fail += 1
            except Exception:
                fail += 1
            break
    ok = 0
    for f in fits:
        try:
            if not f.IsValidObject:
                continue
            if any(not c.IsConnected for c in _conns(f)):
                doc.Delete(f.Id)
                fail += 1
            else:
                ok += 1
        except Exception:
            pass
    return ok, fail


class _Fails(DB.IFailuresPreprocessor):
    msgs = []

    def PreprocessFailures(self, acc):
        err = False
        for f in acc.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                acc.DeleteWarning(f)
            else:
                err = True
                try:
                    _Fails.msgs.append(_u(f.GetDescriptionText()))
                except Exception:
                    pass
        return DB.FailureProcessingResult.ProceedWithRollBack if err else DB.FailureProcessingResult.Continue


def generate(kind, segs, layer, tid, sid, size, z, level, join):
    """一个事务生成全部。返回 (生成的图元, 创建时的错误示例, 接头成功, 接头失败)。事务被 Revit 回滚时抛 RuntimeError（带 Revit 的报错）。"""
    made, errs = [], []
    _Fails.msgs = []
    t = DB.Transaction(doc, TITLE)
    o = t.GetFailureHandlingOptions()
    o.SetFailuresPreprocessor(_Fails())
    o.SetClearAfterRollback(True)
    o.SetForcedModalHandling(False)
    t.SetFailureHandlingOptions(o)
    t.Start()
    try:
        for s in segs:
            try:
                e = create_one(kind, s, z, level, tid, sid)
                set_size(e, kind, size)
                cm = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
                if cm is not None and not cm.IsReadOnly:
                    cm.Set(u"%s %s" % (TAG, layer))
                made.append(e)
            except Exception as ex:
                if len(errs) < 5:
                    errs.append(_u(ex))
        j_ok = j_fail = 0
        if join and made:
            j_ok, j_fail = connect_ends(made, kind)
        status = t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    if status != DB.TransactionStatus.Committed:
        raise RuntimeError(u"Revit 报错，整批已回滚，没有生成任何东西：" + u"；".join(_Fails.msgs[:3]))
    return made, errs, j_ok, j_fail


# ============================================================
# 界面
# ============================================================
def dialog(layers, view_level_name):
    f = W.Form()
    f.Text = TITLE
    f.Font = D.Font(u"Microsoft YaHei UI", 9.0)
    f.StartPosition = W.FormStartPosition.CenterScreen
    f.FormBorderStyle = W.FormBorderStyle.FixedDialog
    f.MaximizeBox = False
    f.MinimizeBox = False
    f.ClientSize = D.Size(520, 400)
    st = {'ok': False}

    def lab(t, y):
        l = W.Label()
        l.Text = t
        l.Location = D.Point(20, y + 4)
        l.Size = D.Size(110, 22)
        f.Controls.Add(l)

    def combo(y, items):
        c = W.ComboBox()
        c.DropDownStyle = W.ComboBoxStyle.DropDownList
        c.Location = D.Point(135, y)
        c.Size = D.Size(365, 26)
        for it in items:
            c.Items.Add(it)
        if items:
            c.SelectedIndex = 0
        f.Controls.Add(c)
        return c
    lab(u"图层", 16)
    names = sorted(layers.keys(), key=lambda k: -len(layers[k]))
    c_layer = combo(16, [u"%s（%d 条线）" % (n, len(layers[n])) for n in names])
    lab(u"生成", 56)
    kinds = [(u"pipe", u"水管"), (u"duct", u"风管"), (u"tray", u"桥架")]
    c_kind = combo(56, [t for k, t in kinds])
    lab(u"类型", 96)
    c_type = combo(96, [])
    lab(u"系统类型", 136)
    c_sys = combo(136, [])
    lab(u"尺寸", 176)
    t_size = W.TextBox()
    t_size.Location = D.Point(135, 172)
    t_size.Size = D.Size(120, 26)
    t_size.Text = u"100"
    f.Controls.Add(t_size)
    hint = W.Label()
    hint.Location = D.Point(262, 176)
    hint.Size = D.Size(240, 40)
    f.Controls.Add(hint)
    lab(u"标高偏移 mm", 216)
    t_z = W.TextBox()
    t_z.Location = D.Point(135, 212)
    t_z.Size = D.Size(120, 26)
    t_z.Text = u"0"
    f.Controls.Add(t_z)
    zl = W.Label()
    zl.Location = D.Point(262, 216)
    zl.Size = D.Size(240, 22)
    zl.Text = u"相对当前视图标高「%s」，管中心" % view_level_name
    f.Controls.Add(zl)
    cache = {}

    def refill(*a):
        k = kinds[c_kind.SelectedIndex][0]
        if k not in cache:
            cache[k] = list_types(k)
        ts, ss = cache[k]
        c_type.Items.Clear()
        for x in ts:
            c_type.Items.Add(_name(x))
        if ts:
            c_type.SelectedIndex = 0
        c_sys.Items.Clear()
        for x in ss:
            c_sys.Items.Add(_name(x))
        if ss:
            c_sys.SelectedIndex = 0
        c_sys.Enabled = k != u"tray"
        hint.Text = {u"pipe": u"公称直径 DN，如 100", u"duct": u"矩形 500x250，圆形 d300", u"tray": u"宽x高，如 300x100"}[k]
        t_size.Text = {u"pipe": u"100", u"duct": u"500x250", u"tray": u"300x100"}[k]
    c_kind.SelectedIndexChanged += refill
    refill()

    def ok(s, e):
        k = kinds[c_kind.SelectedIndex][0]
        ts, ss = cache[k]
        if not ts or (k != u"tray" and not ss):
            forms.alert(u"项目里没有可用的类型 / 系统类型。", title=TITLE)
            return
        try:
            size = parse_size(k, t_size.Text)
            z = float(t_z.Text)
        except Exception:
            forms.alert(u"尺寸或标高偏移填得不对（尺寸例：100 / 500x250 / d300）。", title=TITLE)
            return
        st.update({'ok': True, 'layer': names[c_layer.SelectedIndex], 'kind': k, 'tid': ts[c_type.SelectedIndex].Id,
                   'sid': (ss[c_sys.SelectedIndex].Id if ss else DB.ElementId.InvalidElementId), 'size': size, 'z': z,
                   'join': False, 'tname': _name(ts[c_type.SelectedIndex]), 'sname': (_name(ss[c_sys.SelectedIndex]) if ss else u"")})
        f.Close()
    b = W.Button()
    b.Text = u"预览并生成"
    b.Location = D.Point(280, 350)
    b.Size = D.Size(120, 34)
    b.Click += ok
    f.Controls.Add(b)
    c = W.Button()
    c.Text = u"取消"
    c.Location = D.Point(410, 350)
    c.Size = D.Size(90, 34)
    c.Click += lambda s, e: f.Close()
    f.Controls.Add(c)
    f.AcceptButton = b
    ff_fit(f)
    f.ShowDialog()
    return st if st['ok'] else None


def main():
    try:
        v = doc.ActiveView
        if not isinstance(v, DB.ViewPlan) or v.IsTemplate or v.GenLevel is None:
            forms.alert(u"请先切到一个楼层平面视图（生成的管线按这个视图的标高放）。", title=TITLE)
            return
        inst = pick_import()
        if inst is None:
            return
        layers = lines_by_layer(inst)
        if not layers:
            forms.alert(u"这个 CAD 里没有取到直线（太短的线被忽略）。", title=TITLE)
            return
        level = v.GenLevel
        r = dialog(layers, _u(level.Name))
        if r is None:
            return
        segs = layers[r['layer']]
        z = level.Elevation + r['z'] * MM
        msg = [u"图层「%s」：%d 条直线" % (r['layer'], len(segs)),
               u"生成 %s，类型「%s」%s，尺寸 %s，标高 %s %+g mm。" % (
                   {u"pipe": u"水管", u"duct": u"风管", u"tray": u"桥架"}[r['kind']], r['tname'], (u"，系统「%s」" % r['sname']) if r['sname'] else u"",
                   u"×".join(u"%g" % x for x in r['size'].values()), _u(level.Name), r['z']),
               u"\n确认生成？（可 Ctrl+Z 撤销）"]
        if not forms.alert(u"\n".join(msg), title=TITLE + u" — 预览（还没生成）", yes=True, no=True):
            return
        made, errs, j_ok, j_fail = generate(r['kind'], segs, r['layer'], r['tid'], r['sid'], r['size'], z, level, r['join'])
        rep = [u"生成 %d 段，失败 %d 段。" % (len(made), len(segs) - len(made))]
        if r['join']:
            rep.append(u"接头：成功 %d 个，没接上 %d 处（已回滚，留着端头悬空，可手动处理）。" % (j_ok, j_fail))
        if errs:
            rep.append(u"失败原因示例：" + u"；".join(errs[:3]))
        forms.alert(u"\n".join(rep), title=TITLE)
    except Exception as ex:
        forms.alert(u"出错了（已回滚）：\n%s\n\n%s" % (ex, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
