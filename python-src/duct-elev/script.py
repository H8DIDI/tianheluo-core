# -*- coding: utf-8 -*-
__title__ = '排管标高批量调整'
__doc__ = ('框选一批排管（结构框架），按系统（TELCO、ELV、LV、ELE-11KV…）批量改标高：\n'
           '① 设底标高：这批排管的管底统一到某个标高；② 设顶标高：管顶统一到某个标高（断面高度不同的排管顶对齐）；③ 整体升降：整体抬高/降低一个量。\n'
           '为什么「顶部高程」「底部高程」是灰的：这两个是 Revit 算出来的只读值（有坡度的排管干脆是空的），不能直接输入。'
           '本插件改的是真正能改的「起点/终点标高偏移」，按排管自己的断面高度和 Z 轴对正换算，所以不论族怎么做的都能调。\n'
           '有坡度的排管默认保持坡度整体平移，也可以勾选拉平成水平。整次一个事务，Ctrl+Z 可撤销；只改框里选中的系统。')

import re
import traceback
from pyrevit import revit, DB, UI, forms
from Autodesk.Revit.UI.Selection import ISelectionFilter

# @include _shared/form_fit.py
# @include _shared/ws_guard.py
import clr
import System
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import System.Windows.Forms as WF
import System.Drawing as SD

doc = revit.doc
uidoc = revit.uidoc
TITLE = u'排管标高批量调整'
FT = 304.8
BIC = DB.BuiltInParameter
FRAMING = int(DB.BuiltInCategory.OST_StructuralFraming)
TOL = 0.5          # mm，比这小的差别当作没变
_SERIES_RE = re.compile(u'^([A-Za-z]+(?:-\\d+KV)?)')
_CN_RE = re.compile(u'^([^\\s\\d\\-—A-Za-z]+)')


def _idv(i):
    try:
        return i.Value
    except Exception:
        return i.IntegerValue


def _mm(p):
    return p.AsDouble() * FT


def system_of(e):
    """排管属于哪个系统：族名前缀（TELCO / ELV / LV / ELE-11KV…）；注释里写了线路的（如 275KV线路B）接在后面分开。"""
    try:
        s = doc.GetElement(e.GetTypeId())
        fn = s.Family.Name if s is not None else u''
    except Exception:
        fn = u''
    m = _SERIES_RE.match(fn) or _CN_RE.match(fn)
    lab = m.group(1) if m else (fn[:6] or u'其他')
    try:
        p = e.get_Parameter(BIC.ALL_MODEL_INSTANCE_COMMENTS)
        c = (p.AsString() or u'').strip() if p is not None else u''
    except Exception:
        c = u''
    if c:
        lab = u'%s · %s' % (lab, c[:14])
    return lab


def read_bank(e):
    """读一根排管当前的标高（mm，项目绝对标高）。返回 dict；读不了返回 {'why': 原因}。
    bottom/top 是起点、终点各一个值；h = 断面高度；s_off/e_off/zo/shift 是反推偏移用的。"""
    p0 = e.get_Parameter(BIC.STRUCTURAL_BEAM_END0_ELEVATION)
    p1 = e.get_Parameter(BIC.STRUCTURAL_BEAM_END1_ELEVATION)
    if p0 is None or p1 is None:
        return {'why': u'没有「起点/终点标高偏移」参数'}
    if p0.IsReadOnly or p1.IsReadOnly:
        return {'why': u'起点/终点标高偏移是只读的'}
    lp = e.get_Parameter(BIC.INSTANCE_REFERENCE_LEVEL_PARAM)
    lvl = doc.GetElement(lp.AsElementId()) if lp is not None else None
    if lvl is None:
        return {'why': u'没有参照标高'}
    lv = lvl.Elevation * FT
    s_off, e_off = _mm(p0), _mm(p1)
    zp = e.get_Parameter(BIC.Z_OFFSET_VALUE)
    zo = _mm(zp) if zp is not None else 0.0
    # 断面高度：顶、底高程都有值（水平排管）→ 直接相减；否则取「包裹高度」；再不行用包围盒（竖向范围 − 坡度落差）
    h = None
    pt, pb = e.get_Parameter(BIC.STRUCTURAL_ELEVATION_AT_TOP), e.get_Parameter(BIC.STRUCTURAL_ELEVATION_AT_BOTTOM)
    if pt is not None and pb is not None and pt.HasValue and pb.HasValue:
        h = _mm(pt) - _mm(pb)
    if h is None or h < 1:
        q = e.LookupParameter(u'包裹高度')
        if q is not None and q.HasValue and q.StorageType == DB.StorageType.Double and q.AsDouble() > 0:
            h = _mm(q)
    if h is None or h < 1:
        bb = e.get_BoundingBox(None)
        if bb is not None:
            h = (bb.Max.Z - bb.Min.Z) * FT - abs(s_off - e_off)
    if h is None or h < 1:
        return {'why': u'取不到断面高度'}
    zj = e.get_Parameter(BIC.Z_JUSTIFICATION)
    zv = zj.AsValueString() if zj is not None else u''
    zi = zj.AsInteger() if zj is not None else -1
    if zv == u'底' or zi == 3:
        shift = 0.0
    elif zv == u'顶' or zi == 0:
        shift = -h
    elif zv in (u'中', u'中心', u'中间') or zi == 1:
        shift = -h / 2.0
    else:
        return {'why': u'Z 轴对正是「%s」，无法换算断面位置' % zv}
    b0, b1 = lv + s_off + zo + shift, lv + e_off + zo + shift
    return {'e': e, 'bot': (b0, b1), 'top': (b0 + h, b1 + h), 'h': h, 's_off': s_off, 'e_off': e_off,
            'sloped': abs(s_off - e_off) > TOL}


def plan_change(info, mode, value, flatten):
    """算新的起点/终点偏移（mm）。mode：'bottom' 底标高=value，'top' 顶标高=value，'shift' 整体升降 value。
    有坡度且不拉平：整体平移，让较低的底端（设底）/ 较高的顶端（设顶）落在目标上。返回 (新起点偏移, 新终点偏移)。"""
    b0, b1 = info['bot']
    t0, t1 = info['top']
    if mode == 'shift':
        d0 = d1 = value
    elif mode == 'bottom':
        if flatten:
            d0, d1 = value - b0, value - b1
        else:
            d0 = d1 = value - min(b0, b1)
    else:
        if flatten:
            d0, d1 = value - t0, value - t1
        else:
            d0 = d1 = value - max(t0, t1)
    return info['s_off'] + d0, info['e_off'] + d1


def collect(elems):
    """分系统：{系统: [info...]}，读不了的放进 bad [(图元, 原因)]。"""
    groups, bad = {}, []
    for e in elems:
        info = read_bank(e)
        if 'why' in info:
            bad.append((e, info['why']))
        else:
            info['sys'] = system_of(e)
            groups.setdefault(info['sys'], []).append(info)
    return groups, bad


def _rng(vals, nd=3):
    lo, hi = min(vals) / 1000.0, max(vals) / 1000.0
    return (u'%.*f' % (nd, lo)) if abs(hi - lo) < 0.0005 else (u'%.*f ~ %.*f' % (nd, lo, nd, hi))


class _FrameFilter(ISelectionFilter):
    def AllowElement(self, e):
        try:
            return e.Category is not None and _idv(e.Category.Id) == FRAMING
        except Exception:
            return False

    def AllowReference(self, r, p):
        return False


def pick_elements():
    """先用已选中的结构框架；没有选就让用户框选。取消返回 None。"""
    try:
        ids = list(uidoc.Selection.GetElementIds())
    except Exception:
        ids = []
    els = [doc.GetElement(i) for i in ids]
    els = [e for e in els if e is not None and e.Category is not None and _idv(e.Category.Id) == FRAMING]
    if els:
        return els
    try:
        return list(uidoc.Selection.PickElementsByRectangle(_FrameFilter(), u'框选要调标高的排管，框完点 Finish（不用管混进来的别的构件）'))
    except Exception:
        return None


def ask(groups):
    """窗口：选系统 + 选方式 + 输入数值。返回 dict(sys=[...], mode, value_mm, flatten) 或 None。"""
    f = WF.Form()
    f.Text = TITLE
    f.StartPosition = WF.FormStartPosition.CenterScreen
    f.ClientSize = SD.Size(720, 470)
    f.MinimumSize = SD.Size(720, 470)
    f.Font = SD.Font(u'Microsoft YaHei UI', 9.0)
    A = WF.AnchorStyles
    tl = A.Top | A.Left
    tlr = A.Top | A.Left | A.Right

    lab = WF.Label()
    lab.Text = u'框里认出下面这些系统（族名前缀；注释里有线路名的分开算）。勾上要调的：'
    lab.Location = SD.Point(12, 10)
    lab.Size = SD.Size(696, 20)
    lab.Anchor = tlr
    f.Controls.Add(lab)

    lv = WF.ListView()
    lv.View = WF.View.Details
    lv.CheckBoxes = True
    lv.FullRowSelect = True
    lv.GridLines = True
    lv.HideSelection = False
    lv.Location = SD.Point(12, 34)
    lv.Size = SD.Size(696, 180)
    lv.Anchor = A.Top | A.Left | A.Right | A.Bottom
    for t, w in ((u'系统', 190), (u'数量', 50), (u'底标高 (m)', 120), (u'顶标高 (m)', 120), (u'断面高 (m)', 90), (u'有坡度', 60)):
        lv.Columns.Add(t, w)
    for k in sorted(groups):
        gs = groups[k]
        it = WF.ListViewItem(k)
        it.Checked = True
        it.SubItems.Add(u'%d' % len(gs))
        it.SubItems.Add(_rng([x for g in gs for x in g['bot']]))
        it.SubItems.Add(_rng([x for g in gs for x in g['top']]))
        it.SubItems.Add(_rng([g['h'] for g in gs], 2))
        it.SubItems.Add(u'%d' % sum(1 for g in gs if g['sloped']))
        lv.Items.Add(it)
    f.Controls.Add(lv)

    def _btn(text, x, y, w, handler):
        b = WF.Button()
        b.Text = text
        b.Location = SD.Point(x, y)
        b.Size = SD.Size(w, 26)
        b.Anchor = A.Bottom | A.Left
        b.Click += handler
        f.Controls.Add(b)
        return b

    def _all(v):
        def h(s, a):
            for it in lv.Items:
                it.Checked = v
        return h
    # 下面的控件整体贴底
    y0 = 224
    _btn(u'全选', 12, y0, 70, _all(True)).Anchor = A.Bottom | A.Left
    _btn(u'全不选', 88, y0, 70, _all(False)).Anchor = A.Bottom | A.Left

    gb = WF.GroupBox()
    gb.Text = u'怎么调'
    gb.Location = SD.Point(12, 258)
    gb.Size = SD.Size(696, 152)
    gb.Anchor = A.Bottom | A.Left | A.Right
    f.Controls.Add(gb)
    rb = []
    for i, (txt, tip) in enumerate(((u'设底标高（管底统一到）', u'm'), (u'设顶标高（管顶统一到）', u'm'), (u'整体升降（正数抬高、负数降低）', u'm'))):
        r = WF.RadioButton()
        r.Text = txt
        r.Location = SD.Point(14, 24 + i * 26)
        r.Size = SD.Size(300, 22)
        r.Checked = (i == 0)
        gb.Controls.Add(r)
        rb.append(r)
    tb = WF.TextBox()
    tb.Location = SD.Point(330, 24)
    tb.Size = SD.Size(110, 24)
    gb.Controls.Add(tb)
    un = WF.Label()
    un.Text = u'米（m）'
    un.Location = SD.Point(446, 27)
    un.Size = SD.Size(80, 20)
    gb.Controls.Add(un)
    fl = WF.CheckBox()
    fl.Text = u'有坡度的排管拉平成水平（不勾：保持坡度整体平移，较低的管底/较高的管顶落在目标上）'
    fl.Location = SD.Point(14, 122)
    fl.Size = SD.Size(670, 22)
    gb.Controls.Add(fl)
    nt = WF.Label()
    nt.Text = u'标高读数同属性里的「底部高程/顶部高程」。断面高度固定，所以顶、底二选一：管底 + 断面高 = 管顶。框选时把整段连着的排管都选上，接头处才不会错位。'
    nt.Location = SD.Point(330, 52)
    nt.Size = SD.Size(358, 66)
    gb.Controls.Add(nt)

    res = {}
    ok = WF.Button()
    ok.Text = u'预览并执行'
    ok.Size = SD.Size(110, 28)
    ok.Location = SD.Point(480, 432)
    ok.Anchor = A.Bottom | A.Right
    cc = WF.Button()
    cc.Text = u'取消'
    cc.Size = SD.Size(90, 28)
    cc.Location = SD.Point(618, 432)
    cc.Anchor = A.Bottom | A.Right
    cc.DialogResult = WF.DialogResult.Cancel
    f.Controls.Add(ok)
    f.Controls.Add(cc)
    f.CancelButton = cc

    def _ok(s, a):
        names = [it.Text for it in lv.Items if it.Checked]
        if not names:
            WF.MessageBox.Show(u'至少勾一个系统。', TITLE)
            return
        try:
            v = float(tb.Text.strip().replace(u'，', u'.').replace(u',', u'.'))
        except Exception:
            WF.MessageBox.Show(u'请输入数字（单位米）。', TITLE)
            return
        res['sys'] = names
        res['mode'] = 'bottom' if rb[0].Checked else ('top' if rb[1].Checked else 'shift')
        res['value'] = v * 1000.0
        res['flatten'] = fl.Checked
        f.DialogResult = WF.DialogResult.OK
        f.Close()
    ok.Click += _ok
    f.AcceptButton = ok
    ff_fit(f)
    if f.ShowDialog() != WF.DialogResult.OK:
        return None
    return res


class _Fails(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        for m in fa.GetFailureMessages():
            if m.GetSeverity() == DB.FailureSeverity.Warning:
                fa.DeleteWarning(m)
        return DB.FailureProcessingResult.Continue


def _refine(info, mode, value, flatten):
    """有坡度的排管断面高度是估的（包围盒）。在事务里先把它拉平读 Revit 自己的顶、底高程得到精确值，再算新偏移。"""
    e = info['e']
    e.get_Parameter(BIC.STRUCTURAL_BEAM_END0_ELEVATION).Set(info['s_off'] / FT)
    e.get_Parameter(BIC.STRUCTURAL_BEAM_END1_ELEVATION).Set(info['s_off'] / FT)
    doc.Regenerate()
    pt, pb = e.get_Parameter(BIC.STRUCTURAL_ELEVATION_AT_TOP), e.get_Parameter(BIC.STRUCTURAL_ELEVATION_AT_BOTTOM)
    if pt is not None and pb is not None and pt.HasValue and pb.HasValue and _mm(pt) - _mm(pb) > 1:
        h = _mm(pt) - _mm(pb)
        d = h - info['h']
        info['h'] = h
        info['top'] = (info['top'][0] + d, info['top'][1] + d)


def apply_plan(infos, mode, value, flatten):
    """一个事务把这批排管改到位；出错整体回滚。infos 是 read_bank 的结果。返回 (实际改的 [(info, 新起点偏移, 新终点偏移)], 校验不符 [(图元, 说明)])。"""
    t = DB.Transaction(doc, u'排管标高批量调整')
    fho = t.GetFailureHandlingOptions()
    fho.SetFailuresPreprocessor(_Fails())
    t.SetFailureHandlingOptions(fho)
    t.Start()
    try:
        plans = []
        for info in infos:
            if info['sloped']:
                _refine(info, mode, value, flatten)
            ns, ne = plan_change(info, mode, value, flatten)
            e = info['e']
            e.get_Parameter(BIC.STRUCTURAL_BEAM_END0_ELEVATION).Set(ns / FT)
            e.get_Parameter(BIC.STRUCTURAL_BEAM_END1_ELEVATION).Set(ne / FT)
            plans.append((info, ns, ne))
        doc.Regenerate()
        bad = []
        for info, ns, ne in plans:
            now = read_bank(info['e'])
            if 'why' in now or abs(now['s_off'] - ns) > TOL or abs(now['e_off'] - ne) > TOL:
                bad.append((info['e'], u'写入后读回来的值不对'))
        t.Commit()
        return plans, bad
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise


def main():
    try:
        elems = pick_elements()
        if elems is None:
            return
        if not elems:
            forms.alert(u'框里没有排管（结构框架）。', title=TITLE)
            return
        groups, bad = collect(elems)
        if not groups:
            forms.alert(u'框里的构件都读不了标高：\n' + u'\n'.join(sorted(set(w for _, w in bad))), title=TITLE)
            return
        ans = ask(groups)
        if ans is None:
            return
        mode, val, flat = ans['mode'], ans['value'], ans['flatten']
        infos = [g for k in ans['sys'] for g in groups[k]]
        ok_els, blocked = wg_split(doc, [g['e'] for g in infos])
        okids = set(_idv(e.Id) for e in ok_els)
        plans, same = [], 0
        for g in infos:
            if _idv(g['e'].Id) not in okids:
                continue
            ns, ne = plan_change(g, mode, val, flat)
            if abs(ns - g['s_off']) < TOL and abs(ne - g['e_off']) < TOL:
                same += 1
            else:
                plans.append((g, ns, ne))
        if not plans:
            forms.alert(u'选中的系统里没有需要改的（%d 根本来就在目标标高上）。' % same + wg_note(blocked), title=TITLE)
            return
        cnt = {}
        for g, _, _ in plans:
            cnt[g['sys']] = cnt.get(g['sys'], 0) + 1
        g0, ns0, ne0 = plans[0]
        what = {'bottom': u'管底 → %.3f m', 'top': u'管顶 → %.3f m', 'shift': u'整体升降 %+.3f m'}[mode] % (val / 1000.0)
        d0 = (ns0 - g0['s_off']) / 1000.0
        msg = (u'调整方式：%s%s\n\n将改 %d 根：\n%s\n\n示例（%s）：起点管底 %.3f → %.3f m，管顶 %.3f → %.3f m（平移 %+.3f m）'
               % (what, u'（有坡度的拉平）' if flat else u'', len(plans),
                  u'\n'.join(u'  · %s：%d 根' % (k, cnt[k]) for k in sorted(cnt)),
                  g0['sys'], g0['bot'][0] / 1000.0, g0['bot'][0] / 1000.0 + d0, g0['top'][0] / 1000.0, g0['top'][0] / 1000.0 + d0, d0))
        if same:
            msg += u'\n另有 %d 根本来就在目标标高，不动。' % same
        if bad:
            msg += u'\n另有 %d 个构件读不了标高，已跳过。' % len(bad)
        msg += wg_note(blocked)
        if not forms.alert(msg + u'\n\n执行吗？（可 Ctrl+Z 撤销）', title=TITLE, ok=False, yes=True, no=True):
            return
        done, mism = apply_plan([g for g, _, _ in plans], mode, val, flat)
        n = len(done)
        try:
            uidoc.Selection.SetElementIds(System.Collections.Generic.List[DB.ElementId]([g['e'].Id for g, _, _ in done]))
        except Exception:
            pass
        out = u'已改 %d 根排管（已为选中状态，可 Ctrl+Z 撤销）。' % n
        if mism:
            out += u'\n⚠ 有 %d 根写入后读回的值不对，请检查。' % len(mism)
        forms.alert(out, title=TITLE)
    except Exception:
        forms.alert(u'出错了（已回滚，模型没被改）：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
