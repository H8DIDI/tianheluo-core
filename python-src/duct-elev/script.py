# -*- coding: utf-8 -*-
__title__ = '排管标高批量调整'
__doc__ = ('框选一批排管（结构框架），按系统（TELCO、ELV、LV、ELE-11KV…）批量改标高：\n'
           '① 设底标高：管底到某个标高；② 设顶标高：管顶到某个标高（断面高度不同的排管顶对齐）；③ 整体升降：整体抬高/降低一个量。\n'
           '有斜度的排管可以「起点、终点分别设置」：两端各写一个标高；只写一端，另一端不动。也可以只写一个值：保持坡度整体平移，或拉平成水平。\n'
           '窗口保持在最前面，设置一次可以反复「框选 → 写入」，勾上「连续模式」框完一批马上框下一批，按 Esc 回窗口。\n'
           '为什么「顶部高程」「底部高程」是灰的：这两个是 Revit 算出来的只读值（有坡度的排管干脆是空的），不能直接输入。'
           '本插件改的是真正能改的「起点/终点标高偏移」，按排管自己的断面高度和 Z 轴对正换算，所以不论族怎么做的都能调。\n'
           '整次一个事务，Ctrl+Z 可撤销；只改窗口里勾选的系统。')

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


def plan_change(info, mode, value, flatten, value2=None, per_end=False):
    """算新的起点/终点偏移（mm）。mode：'bottom' 管底、'top' 管顶、'shift' 整体升降。
    单值（per_end=False）：bottom/top 的 value 是目标标高；有坡度且不拉平 → 整体平移，让较低的管底（设底）/较高的管顶（设顶）落在目标上；拉平 → 两端都到目标。
    分别设置（per_end=True）：value = 起点标高，value2 = 终点标高；某一端是 None 就不动那一端。返回 (新起点偏移, 新终点偏移)。"""
    b0, b1 = info['bot']
    t0, t1 = info['top']
    if mode == 'shift':
        return info['s_off'] + value, info['e_off'] + value
    cur0, cur1 = (b0, b1) if mode == 'bottom' else (t0, t1)
    if per_end:
        d0 = (value - cur0) if value is not None else 0.0
        d1 = (value2 - cur1) if value2 is not None else 0.0
    elif flatten:
        d0, d1 = value - cur0, value - cur1
    else:
        d0 = d1 = value - (min(cur0, cur1) if mode == 'bottom' else max(cur0, cur1))
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
    """框选排管（只让选结构框架）。取消（Esc）返回 None。"""
    try:
        return list(uidoc.Selection.PickElementsByRectangle(_FrameFilter(), u'框选要调标高的排管，框完点 Finish（Esc 回到窗口）'))
    except Exception:
        return None


def scan_systems():
    """整个项目里的排管系统和数量：{系统: 根数}。"""
    cnt = {}
    for e in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType():
        k = system_of(e)
        cnt[k] = cnt.get(k, 0) + 1
    return cnt


def _num(txt):
    t = (txt or u'').strip().replace(u'，', u'.').replace(u',', u'.')
    return float(t) * 1000.0 if t else None


def main_dialog(st, systems):
    """设置窗口（保持在最前面）。返回动作 'pick' 框选并写入 / 'sel' 写入当前选中的 / 'close'。设置存在 st 里，下次打开还在。"""
    f = WF.Form()
    f.Text = TITLE
    f.TopMost = True
    f.StartPosition = WF.FormStartPosition.CenterScreen
    f.ClientSize = SD.Size(740, 612)
    f.MinimumSize = SD.Size(740, 612)
    f.Font = SD.Font(u'Microsoft YaHei UI', 9.0)
    A = WF.AnchorStyles
    tlr = A.Top | A.Left | A.Right

    stl = WF.Label()
    stl.Text = st.get('note') or u'设置好后点「框选排管并写入」。窗口会在框选时暂时收起，写完自动回来，设置保留。'
    stl.Location = SD.Point(12, 8)
    stl.Size = SD.Size(716, 52)
    stl.Anchor = tlr
    f.Controls.Add(stl)

    lab = WF.Label()
    lab.Text = u'只改勾上的系统（系统 = 族名前缀；注释里有线路名的分开算）：'
    lab.Location = SD.Point(12, 64)
    lab.Size = SD.Size(716, 20)
    lab.Anchor = tlr
    f.Controls.Add(lab)

    lv = WF.ListView()
    lv.View = WF.View.Details
    lv.CheckBoxes = True
    lv.FullRowSelect = True
    lv.GridLines = True
    lv.HideSelection = False
    lv.Location = SD.Point(12, 86)
    lv.Size = SD.Size(716, 130)
    lv.Anchor = A.Top | A.Left | A.Right | A.Bottom
    lv.Columns.Add(u'系统', 440)
    lv.Columns.Add(u'项目里共', 100)
    on = st.get('sys')
    for k in sorted(systems):
        it = WF.ListViewItem(k)
        it.Checked = True if on is None else (k in on)
        it.SubItems.Add(u'%d 根' % systems[k])
        lv.Items.Add(it)
    f.Controls.Add(lv)

    def _btn(text, x, y, w, handler, anchor=A.Bottom | A.Left):
        b = WF.Button()
        b.Text = text
        b.Location = SD.Point(x, y)
        b.Size = SD.Size(w, 28)
        b.Anchor = anchor
        b.Click += handler
        f.Controls.Add(b)
        return b

    def _all(v):
        def h(s_, a_):
            for it in lv.Items:
                it.Checked = v
        return h
    _btn(u'全选', 12, 222, 70, _all(True), A.Top | A.Left)
    _btn(u'全不选', 88, 222, 70, _all(False), A.Top | A.Left)

    gb = WF.GroupBox()
    gb.Text = u'怎么调'
    gb.Location = SD.Point(12, 256)
    gb.Size = SD.Size(716, 236)
    gb.Anchor = A.Bottom | A.Left | A.Right
    f.Controls.Add(gb)

    pm = WF.Panel()                      # 单选按钮要分两组，各放各的容器里
    pm.Location = SD.Point(10, 20)
    pm.Size = SD.Size(280, 100)
    gb.Controls.Add(pm)
    pv = WF.Panel()
    pv.Location = SD.Point(290, 20)
    pv.Size = SD.Size(240, 76)
    gb.Controls.Add(pv)
    rb = []
    for i, txt in enumerate((u'设底标高（管底到）', u'设顶标高（管顶到）', u'整体升降（正数抬高、负数降低）')):
        r = WF.RadioButton()
        r.Text = txt
        r.Location = SD.Point(4, 6 + i * 28)
        r.Size = SD.Size(262, 22)
        pm.Controls.Add(r)
        rb.append(r)
    rb[{'bottom': 0, 'top': 1, 'shift': 2}.get(st.get('mode', 'bottom'), 0)].Checked = True

    rs = WF.RadioButton()
    rs.Text = u'一个值（两端同值，或整体平移）'
    rs.Location = SD.Point(10, 6)
    rs.Size = SD.Size(228, 22)
    pv.Controls.Add(rs)
    rp = WF.RadioButton()
    rp.Text = u'起点、终点分别设置（斜度排管）'
    rp.Location = SD.Point(10, 42)
    rp.Size = SD.Size(228, 22)
    pv.Controls.Add(rp)
    (rp if st.get('per_end') else rs).Checked = True

    def _tb(x, y, w, txt):
        t = WF.TextBox()
        t.Location = SD.Point(x, y)
        t.Size = SD.Size(w, 24)
        t.Text = txt or u''
        gb.Controls.Add(t)
        return t

    def _lb(x, y, w, txt):
        l = WF.Label()
        l.Text = txt
        l.Location = SD.Point(x, y)
        l.Size = SD.Size(w, 20)
        gb.Controls.Add(l)
        return l
    tb1 = _tb(540, 24, 90, st.get('t1'))
    _lb(636, 28, 60, u'米（m）')
    _lb(320, 94, 46, u'起点')
    tbs = _tb(368, 90, 90, st.get('ts'))
    _lb(468, 94, 46, u'终点')
    tbe = _tb(516, 90, 90, st.get('te'))
    _lb(612, 94, 60, u'米（m）')
    _lb(320, 122, 380, u'只填一端，另一端保持不动。起点/终点按每根排管自己的绘制方向，多根一起写时请只框方向一致的一段。')
    gb.Controls[gb.Controls.Count - 1].Size = SD.Size(380, 40)

    fl = WF.CheckBox()
    fl.Text = u'一个值时：有坡度的排管拉平成水平（不勾：保持坡度整体平移，较低的管底/较高的管顶落在目标上）'
    fl.Location = SD.Point(14, 168)
    fl.Size = SD.Size(690, 22)
    fl.Checked = bool(st.get('flatten'))
    gb.Controls.Add(fl)
    nt = WF.Label()
    nt.Text = u'标高读数同属性里的「底部高程/顶部高程」。断面高度固定：管底 + 断面高 = 管顶。把整段连着的排管都框上，接头处才不会错位。'
    nt.Location = SD.Point(14, 194)
    nt.Size = SD.Size(690, 34)
    gb.Controls.Add(nt)

    ck = WF.CheckBox()
    ck.Text = u'连续模式：写完马上框下一批（按 Esc 回到窗口）'
    ck.Location = SD.Point(14, 504)
    ck.Size = SD.Size(380, 22)
    ck.Anchor = A.Bottom | A.Left
    ck.Checked = bool(st.get('continuous'))
    f.Controls.Add(ck)
    cf = WF.CheckBox()
    cf.Text = u'写入前确认'
    cf.Location = SD.Point(420, 504)
    cf.Size = SD.Size(200, 22)
    cf.Anchor = A.Bottom | A.Left
    cf.Checked = bool(st.get('confirm'))
    f.Controls.Add(cf)

    def _refresh(s_=None, a_=None):
        shift = rb[2].Checked
        rs.Enabled = rp.Enabled = not shift
        one = shift or rs.Checked
        tb1.Enabled = one
        tbs.Enabled = tbe.Enabled = (not shift) and rp.Checked
        fl.Enabled = (not shift) and rs.Checked
    for c in rb + [rs, rp]:
        c.CheckedChanged += _refresh
    _refresh()

    res = {'act': 'close'}

    def _go(act):
        def h(s_, a_):
            names = set(it.Text for it in lv.Items if it.Checked)
            if not names:
                WF.MessageBox.Show(u'至少勾一个系统。', TITLE)
                return
            mode = 'bottom' if rb[0].Checked else ('top' if rb[1].Checked else 'shift')
            per = (mode != 'shift') and rp.Checked
            try:
                v1, vs, ve = _num(tb1.Text), _num(tbs.Text), _num(tbe.Text)
            except Exception:
                WF.MessageBox.Show(u'请输入数字（单位米）。', TITLE)
                return
            if per:
                if vs is None and ve is None:
                    WF.MessageBox.Show(u'起点、终点至少填一个。', TITLE)
                    return
            elif v1 is None:
                WF.MessageBox.Show(u'请输入数值（单位米）。', TITLE)
                return
            st.update({'sys': names, 'mode': mode, 'per_end': per, 't1': tb1.Text, 'ts': tbs.Text, 'te': tbe.Text,
                       'flatten': fl.Checked, 'continuous': ck.Checked, 'confirm': cf.Checked,
                       'v1': v1, 'vs': vs, 've': ve})
            res['act'] = act
            f.Close()
        return h
    _btn(u'框选排管并写入', 12, 568, 170, _go('pick'), A.Bottom | A.Left)
    _btn(u'写入当前选中的', 190, 568, 150, _go('sel'), A.Bottom | A.Left)
    b = _btn(u'关闭', 628, 568, 100, lambda s_, a_: f.Close(), A.Bottom | A.Right)
    f.CancelButton = b
    ff_fit(f)
    f.ShowDialog()
    st['sys'] = set(it.Text for it in lv.Items if it.Checked)
    return res['act']


class _Fails(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        for m in fa.GetFailureMessages():
            if m.GetSeverity() == DB.FailureSeverity.Warning:
                fa.DeleteWarning(m)
        return DB.FailureProcessingResult.Continue


def _refine(info):
    """有坡度的排管断面高度是估的（包围盒）。在事务里先把它拉平读 Revit 自己的顶、底高程得到精确值，再把两端偏移还原。"""
    e = info['e']
    p0, p1 = e.get_Parameter(BIC.STRUCTURAL_BEAM_END0_ELEVATION), e.get_Parameter(BIC.STRUCTURAL_BEAM_END1_ELEVATION)
    p0.Set(info['s_off'] / FT)
    p1.Set(info['s_off'] / FT)
    doc.Regenerate()
    pt, pb = e.get_Parameter(BIC.STRUCTURAL_ELEVATION_AT_TOP), e.get_Parameter(BIC.STRUCTURAL_ELEVATION_AT_BOTTOM)
    if pt is not None and pb is not None and pt.HasValue and pb.HasValue and _mm(pt) - _mm(pb) > 1:
        h = _mm(pt) - _mm(pb)
        d = h - info['h']
        info['h'] = h
        info['top'] = (info['top'][0] + d, info['top'][1] + d)
    p0.Set(info['s_off'] / FT)
    p1.Set(info['e_off'] / FT)


def apply_plan(infos, mode, value, flatten, value2=None, per_end=False):
    """一个事务把这批排管改到位；出错整体回滚。infos 是 read_bank 的结果。返回 (实际改的 [(info, 新起点偏移, 新终点偏移)], 校验不符 [(图元, 说明)])。"""
    t = DB.Transaction(doc, u'排管标高批量调整')
    fho = t.GetFailureHandlingOptions()
    fho.SetFailuresPreprocessor(_Fails())
    t.SetFailureHandlingOptions(fho)
    t.Start()
    try:
        plans = []
        for info in infos:
            if info['sloped'] and mode == 'top':
                _refine(info)
            ns, ne = plan_change(info, mode, value, flatten, value2, per_end)
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


def _targets(st):
    """这次写入的目标值：(value, value2, per_end)。"""
    if st['mode'] == 'shift':
        return st['v1'], None, False
    if st['per_end']:
        return st['vs'], st['ve'], True
    return st['v1'], None, False


def _fmt_ends(g, ns, ne):
    d0, d1 = (ns - g['s_off']) / 1000.0, (ne - g['e_off']) / 1000.0
    return (u'起点 管底 %.3f → %.3f、管顶 %.3f → %.3f；终点 管底 %.3f → %.3f、管顶 %.3f → %.3f m'
            % (g['bot'][0] / 1000.0, g['bot'][0] / 1000.0 + d0, g['top'][0] / 1000.0, g['top'][0] / 1000.0 + d0,
               g['bot'][1] / 1000.0, g['bot'][1] / 1000.0 + d1, g['top'][1] / 1000.0, g['top'][1] / 1000.0 + d1))


def write_batch(elems, st):
    """把设置写到这批排管上。返回一句结果说明（窗口顶部显示）。"""
    mode, flat = st['mode'], st['flatten']
    v1, v2, per = _targets(st)
    groups, bad = collect(elems)
    if not groups:
        return u'框里没有能调的排管（%d 个构件读不了标高或不是排管）。' % len(elems)
    infos = [g for k in groups if k in st['sys'] for g in groups[k]]
    skip_sys = sum(len(v) for k, v in groups.items() if k not in st['sys'])
    if not infos:
        return u'框到的排管都不在勾选的系统里（%d 根没动）。' % skip_sys
    ok_els, blocked = wg_split(doc, [g['e'] for g in infos])
    okids = set(_idv(e.Id) for e in ok_els)
    plans, same = [], 0
    for g in infos:
        if _idv(g['e'].Id) not in okids:
            continue
        ns, ne = plan_change(g, mode, v1, flat, v2, per)
        if abs(ns - g['s_off']) < TOL and abs(ne - g['e_off']) < TOL:
            same += 1
        else:
            plans.append((g, ns, ne))
    if not plans:
        return u'没有需要改的：%d 根本来就在目标标高上。%s' % (same, wg_note(blocked).replace(u'\n', u' '))
    if st.get('confirm'):
        cnt = {}
        for g, _, _ in plans:
            cnt[g['sys']] = cnt.get(g['sys'], 0) + 1
        g0, ns0, ne0 = plans[0]
        msg = u'将改 %d 根：\n%s\n\n示例（%s）：\n%s' % (len(plans), u'\n'.join(u'  · %s：%d 根' % (k, cnt[k]) for k in sorted(cnt)),
                                                    g0['sys'], _fmt_ends(g0, ns0, ne0))
        if not forms.alert(msg + wg_note(blocked) + u'\n\n执行吗？（可 Ctrl+Z 撤销）', title=TITLE, ok=False, yes=True, no=True):
            return u'已取消，没有改。'
    done, mism = apply_plan([g for g, _, _ in plans], mode, v1, flat, v2, per)
    try:
        uidoc.Selection.SetElementIds(System.Collections.Generic.List[DB.ElementId]([g['e'].Id for g, _, _ in done]))
    except Exception:
        pass
    g0, ns0, ne0 = done[0]
    out = u'已改 %d 根（已选中，Ctrl+Z 可撤销）。示例：%s' % (len(done), _fmt_ends(g0, ns0, ne0))
    extra = []
    if same:
        extra.append(u'%d 根本来就在目标标高' % same)
    if skip_sys:
        extra.append(u'%d 根不在勾选的系统里' % skip_sys)
    if bad:
        extra.append(u'%d 个读不了标高' % len(bad))
    if blocked:
        extra.append(u'%d 根被占用没改' % len(blocked))
    if mism:
        extra.append(u'⚠ %d 根写入后读回的值不对，请检查' % len(mism))
    return out + (u'  另：' + u'；'.join(extra) if extra else u'')


def main():
    try:
        systems = scan_systems()
        if not systems:
            forms.alert(u'项目里没有结构框架（排管）。', title=TITLE)
            return
        st = {'mode': 'bottom', 'sys': None}
        guard = 0
        while guard < 500:
            guard += 1
            act = main_dialog(st, systems)
            if act == 'close':
                break
            try:
                if act == 'sel':
                    els = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
                    els = [e for e in els if e is not None and e.Category is not None and _idv(e.Category.Id) == FRAMING]
                    st['note'] = write_batch(els, st) if els else u'当前没有选中排管（结构框架）。'
                    continue
                while True:                     # 框选；连续模式下写完马上框下一批，Esc 回窗口
                    els = pick_elements()
                    if els is None:
                        st['note'] = st.get('note') if st.get('batches') else u'已取消框选。'
                        break
                    st['note'] = write_batch(els, st) if els else u'框里没有排管。'
                    st['batches'] = st.get('batches', 0) + 1
                    if not st.get('continuous'):
                        break
            except Exception:
                st['note'] = u'出错了（已回滚，模型没被改）：' + traceback.format_exc().splitlines()[-1]
            st['batches'] = 0
    except Exception:
        forms.alert(u'出错了：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
