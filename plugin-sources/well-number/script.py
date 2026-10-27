# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '一键编号'
__author__ = 'didi'
__doc__ = ('给选中的构件（井、桩、设备……任何有「标记」的构件）按位置自动编号：\n'
           '可以按 X/Y 方向排，也可以沿一条模型线/详图线（道路中心线、管线走向）排，或从起点开始就近连成一串。\n'
           '前缀、起始号、位数、写入哪个参数都能改，先预览再写入，会提醒和模型里其它构件重号。整次可 Ctrl+Z 撤销。')

import io
import os
import json
import math
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from Autodesk.Revit import DB
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from pyrevit import revit, forms
import System.Windows.Forms as WF
import System.Drawing as SD

doc = revit.doc
uidoc = revit.uidoc
TITLE = '一键编号'
SETTINGS = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments),
                        'KaifaTools', '一键编号设置.json')
SORTS = [('X_ASC', '从左到右（X 递增）'), ('X_DESC', '从右到左（X 递减）'),
         ('Y_ASC', '从下到上（Y 递增）'), ('Y_DESC', '从上到下（Y 递减）'),
         ('PATH', '沿一条线编号（运行后点选模型线/详图线，按投影到线上的位置排）'),
         ('CHAIN', '就近连串（运行后点选起点构件，每次找离上一个最近的）')]
DEFAULTS = {'prefix': '', 'start': 1, 'digits': 2, 'step': 1, 'sort': 'X_ASC', 'filter': '',
            'param': '标记', 'only_empty': False}


# ---- form_fit（天河落共用片段，打包时内联进脚本）--------------------------------
# 让手工摆坐标的 WinForms 窗口在高分屏（125%、150% 缩放）上不截字、能拖大小。
# 插件窗口原来都按 100% 缩放的像素写死：缩放 150% 时字变大了框没变，文字就被截掉。
#   1. 按屏幕 DPI 等比放大全部控件（.NET 不管的列表列宽、表格行高另外放大）；
#   2. 放不下的说明文字、勾选项自动换行，下面的控件依次下移；
#   3. 固定边框改成可拖动：列表/表格/预览区跟着窗口变大，底部按钮贴底，右侧按钮贴右，输入框横向拉伸；
#   4. 不超过屏幕可用区域，屏幕太小时出滚动条。
# 用法：form.ShowDialog() 之前调用 ff_fit(form)。已经可拖动、自己排好版的窗口只做第 1 步。
import clr as _ff_clr
_ff_clr.AddReference('System.Windows.Forms')
_ff_clr.AddReference('System.Drawing')
import System.Windows.Forms as _FFW
import System.Drawing as _FFD

_FF_DOCK_NONE = getattr(_FFW.DockStyle, 'None')
_FF_SCALE_NONE = getattr(_FFW.AutoScaleMode, 'None')
_FF_A = _FFW.AnchorStyles
_FF_FIXED = (_FFW.FormBorderStyle.FixedDialog, _FFW.FormBorderStyle.FixedSingle,
             _FFW.FormBorderStyle.Fixed3D, _FFW.FormBorderStyle.FixedToolWindow)
_FF_WRAP_FLAGS = (_FFW.TextFormatFlags.WordBreak | _FFW.TextFormatFlags.TextBoxControl)


def _ff_kids(parent):
    return [c for c in parent.Controls]


def _ff_walk(parent):
    for c in parent.Controls:
        yield c
        for x in _ff_walk(c):
            yield x


def _ff_dpi(f):
    try:
        d = float(f.DeviceDpi)
        if d > 0:
            return d
    except Exception:
        pass
    g = f.CreateGraphics()
    try:
        return float(g.DpiX)
    finally:
        g.Dispose()


def _ff_scale(f, k):
    """整窗等比放大。窗口自己设了 AutoScaleDimensions 的（会自动缩放）不重复放大。"""
    try:
        # 只有写明了设计时的 DPI（AutoScaleDimensions，且和当前不同）窗口才会自己缩放；
        # 只设 AutoScaleMode 不设它，Revit 里其实不会缩放
        asd = f.AutoScaleDimensions
        if f.AutoScaleMode != _FF_SCALE_NONE and not asd.IsEmpty and \
                abs(asd.Width - f.CurrentAutoScaleDimensions.Width) > 0.5:
            return False
    except Exception:
        pass
    mn, mx = f.MinimumSize, f.MaximumSize
    f.MinimumSize = _FFD.Size.Empty
    f.MaximumSize = _FFD.Size.Empty
    f.AutoScaleMode = _FF_SCALE_NONE
    f.Scale(_FFD.SizeF(k, k))      # 控件位置/大小、表格布局的固定列宽、最小最大尺寸由 .NET 一起放大
    if not mn.IsEmpty:
        f.MinimumSize = _FFD.Size(int(mn.Width * k), int(mn.Height * k))
    if not mx.IsEmpty:
        f.MaximumSize = _FFD.Size(int(mx.Width * k), int(mx.Height * k))
    for c in _ff_walk(f):
        try:
            if isinstance(c, _FFW.ListView):
                for col in c.Columns:
                    if col.Width > 0:
                        col.Width = int(col.Width * k)
            elif isinstance(c, _FFW.DataGridView):
                for col in c.Columns:
                    if col.AutoSizeMode != _FFW.DataGridViewAutoSizeColumnMode.Fill:
                        col.Width = int(col.Width * k)
                        if col.MinimumWidth > 2:
                            col.MinimumWidth = max(2, int(col.MinimumWidth * k))
                c.RowTemplate.Height = int(c.RowTemplate.Height * k)
                for r in c.Rows:
                    r.Height = max(r.Height, c.RowTemplate.Height)
                if c.ColumnHeadersHeightSizeMode != _FFW.DataGridViewColumnHeadersHeightSizeMode.AutoSize:
                    c.ColumnHeadersHeight = int(c.ColumnHeadersHeight * k)
                if c.RowHeadersVisible:
                    c.RowHeadersWidth = max(4, int(c.RowHeadersWidth * k))
        except Exception:
            pass
    return True


def _ff_absolute(parent):
    """容器里的控件是手工摆坐标的（不是表格/流式布局、也不是停靠）。"""
    if isinstance(parent, (_FFW.TableLayoutPanel, _FFW.FlowLayoutPanel)):
        return False
    kids = _ff_kids(parent)
    return bool(kids) and all(c.Dock == _FF_DOCK_NONE for c in kids)


def _ff_grow(ctrl, delta, f):
    """ctrl 变高了 delta：它下面的兄弟控件下移，父容器跟着变高（一直传到窗口）。"""
    parent = ctrl.Parent
    if parent is None or delta <= 0:
        return
    old_bottom = ctrl.Bottom - delta
    for s in _ff_kids(parent):
        if s is not ctrl and s.Dock == _FF_DOCK_NONE and s.Top >= old_bottom - 3:
            s.Top += delta
    if isinstance(parent, _FFW.Form):
        parent.ClientSize = _FFD.Size(parent.ClientSize.Width, parent.ClientSize.Height + delta)
    elif getattr(parent, 'AutoScroll', False) or parent.Dock != _FF_DOCK_NONE or isinstance(parent, _FFW.TabPage):
        return
    else:
        parent.Height += delta
        _ff_grow(parent, delta, f)


def _ff_reflow(parent, k, f):
    """放不下的文字：自动换行、加高，下面的控件顺延；按钮文字放不下就加宽。"""
    if not _ff_absolute(parent):
        for c in _ff_kids(parent):
            if c.Controls.Count:
                _ff_reflow(c, k, f)
        return
    for c in sorted(_ff_kids(parent), key=lambda x: (x.Top, x.Left)):
        text = c.Text or u''
        try:
            if isinstance(c, (_FFW.Label, _FFW.CheckBox, _FFW.RadioButton)) and text and not isinstance(c, _FFW.LinkLabel):
                pw = parent.ClientSize.Width
                if c.AutoSize:
                    one = _FFW.TextRenderer.MeasureText(text, c.Font)
                    if c.Left + one.Width <= pw - 2:
                        continue
                    c.AutoSize = False
                    c.Width = max(40, pw - c.Left - int(8 * k))
                    c.Height = one.Height + int(4 * k)
                box = int(20 * k) if isinstance(c, (_FFW.CheckBox, _FFW.RadioButton)) else int(2 * k)
                avail = max(20, c.Width - box - c.Padding.Horizontal - 2)
                need = _FFW.TextRenderer.MeasureText(text, c.Font, _FFD.Size(avail, 100000), _FF_WRAP_FLAGS).Height
                need += c.Padding.Vertical + int(4 * k)
                if need > c.Height + 1:
                    delta = need - c.Height
                    c.Height = need
                    if isinstance(c, (_FFW.CheckBox, _FFW.RadioButton)):
                        c.CheckAlign = _FFD.ContentAlignment.TopLeft
                        c.TextAlign = _FFD.ContentAlignment.TopLeft
                    _ff_grow(c, delta, f)
            elif isinstance(c, _FFW.Button) and text and not c.AutoSize:
                need = _FFW.TextRenderer.MeasureText(text, c.Font).Width + int(18 * k)
                if need > c.Width:
                    d = need - c.Width
                    if c.Left > parent.ClientSize.Width // 2:
                        c.Left -= d
                    c.Width = need
            elif c.Controls.Count and not isinstance(c, (_FFW.DataGridView, _FFW.ListView, _FFW.NumericUpDown, _FFW.ComboBox)):
                _ff_reflow(c, k, f)
        except Exception:
            pass


def _ff_is_grow(c):
    if isinstance(c, (_FFW.ListView, _FFW.DataGridView, _FFW.TreeView, _FFW.ListBox, _FFW.RichTextBox,
                      _FFW.PictureBox, _FFW.TabControl, _FFW.SplitContainer)):
        return True
    if isinstance(c, _FFW.TextBox) and c.Multiline:
        return True
    if isinstance(c, _FFW.Panel) and not isinstance(c, (_FFW.FlowLayoutPanel, _FFW.TableLayoutPanel)):
        return bool(c.AutoScroll) or c.Controls.Count == 0      # 可滚动的内容区，或代码自绘的预览区
    return False


def _ff_has(a, flag):
    return int(a & flag) != 0


def _ff_vover(a, b):
    return a.Top < b.Bottom and b.Top < a.Bottom


def _ff_anchor(parent, k, vertical=True):
    """按位置给控件定锚点：最大的列表/表格/预览区随窗口变大，它下面的控件贴底；贴右边的拉伸或贴右。"""
    if not _ff_absolute(parent):
        return
    cw, ch = parent.ClientSize.Width, parent.ClientSize.Height
    kids = _ff_kids(parent)
    edge = max(int(30 * k), int(cw * 0.05))
    grow = None
    if vertical:
        cand = [c for c in kids if _ff_is_grow(c) and c.Width * c.Height >= 0.10 * cw * ch]
        if cand:
            grow = max(cand, key=lambda c: c.Width * c.Height)
    rows_grow = [c for c in kids if grow is not None and c is not grow and _ff_is_grow(c) and _ff_vover(c, grow)
                 and c.Height >= grow.Height * 0.6]
    for c in kids:
        a = _FF_A.Top
        if vertical:
            if c is grow or c in rows_grow:
                a = _FF_A.Top | _FF_A.Bottom
            elif grow is not None and c.Top >= grow.Bottom - 2:
                a = _FF_A.Bottom
            elif grow is None and isinstance(c, _FFW.Button) and c.Top >= ch - int(64 * k):
                a = _FF_A.Bottom
        right_gap = cw - c.Right
        right_of = [o for o in kids if o is not c and o.Left >= c.Right - 2 and _ff_vover(o, c)]
        small = isinstance(c, (_FFW.Button, _FFW.CheckBox, _FFW.RadioButton)) or c.Width < cw * 0.25
        if right_of and not small and all(isinstance(o, _FFW.Button) or o.Width < cw * 0.3 for o in right_of) \
                and cw - max(o.Right for o in right_of) <= edge and not any(o in rows_grow or o is grow for o in right_of):
            a |= _FF_A.Left | _FF_A.Right
        elif right_gap <= edge and not small and c.Width >= cw * 0.3:
            a |= _FF_A.Left | _FF_A.Right
        elif c.Left > cw * 0.5 and (right_gap <= edge or isinstance(c, _FFW.Button) or
                                    any(o.Right >= cw - edge for o in right_of)):
            a |= _FF_A.Right
        else:
            a |= _FF_A.Left
        c.Anchor = a
        stretched_w = _ff_has(a, _FF_A.Left) and _ff_has(a, _FF_A.Right)
        stretched_h = _ff_has(a, _FF_A.Top) and _ff_has(a, _FF_A.Bottom)
        if isinstance(c, _FFW.TabControl):
            for p in c.TabPages:
                _ff_anchor(p, k, True)
        elif isinstance(c, _FFW.SplitContainer):
            _ff_anchor(c.Panel1, k, True)
            _ff_anchor(c.Panel2, k, True)
        elif (stretched_w or stretched_h) and isinstance(c, (_FFW.Panel, _FFW.GroupBox)) \
                and not isinstance(c, (_FFW.TableLayoutPanel, _FFW.FlowLayoutPanel)):
            _ff_anchor(c, k, stretched_h and not getattr(c, 'AutoScroll', False))


def ff_fit(f):
    """在 ShowDialog() 前调用。出错时什么都不改，窗口照常弹出。"""
    try:
        k = _ff_dpi(f) / 96.0
        fixed = f.FormBorderStyle in _FF_FIXED
        if k > 1.01:
            _ff_scale(f, k)
        if fixed:
            _ff_reflow(f, max(k, 1.0), f)
            _ff_anchor(f, max(k, 1.0), True)
            f.FormBorderStyle = _FFW.FormBorderStyle.Sizable
            f.SizeGripStyle = _FFW.SizeGripStyle.Show
            f.MaximizeBox = True
        try:
            wa = _FFW.Screen.FromPoint(_FFW.Cursor.Position).WorkingArea
        except Exception:
            wa = _FFW.Screen.PrimaryScreen.WorkingArea
        design = f.ClientSize
        if fixed:
            f.AutoScrollMinSize = design      # 屏幕放不下时出滚动条，控件不挤在一起
            f.AutoScroll = True
            f.MinimumSize = _FFD.Size(min(f.Width, wa.Width), min(f.Height, wa.Height))
        if f.Width > wa.Width or f.Height > wa.Height:
            f.AutoScroll = True
            f.Size = _FFD.Size(min(f.Width, wa.Width), min(f.Height, wa.Height))
            if not f.MinimumSize.IsEmpty:
                f.MinimumSize = _FFD.Size(min(f.MinimumSize.Width, wa.Width), min(f.MinimumSize.Height, wa.Height))
    except Exception:
        pass
    return f
# ---- form_fit 结束 ----------------------------------------------------------------

# ---- ws_guard（天河落共用片段，打包时内联进脚本）--------------------------------
# 协同模型（中心文件）里，被别人占用、或中心文件里已经被改过（本地不是最新）的图元改不了；
# 只要一批里混进一个，Revit 整个事务回滚。写入前先分出来：能改的照常改，改不了的跳过并告诉用户是谁占着。
# 用法：ok, blocked = wg_split(doc, elems)；msg = wg_note(blocked) 放进确认框（没有被占用的返回空字符串）。
import Autodesk.Revit.DB as _WGDB


def wg_split(doc, elems):
    """返回 (能改的图元, 改不了的 [(图元, 原因)])。非协同模型全部能改。"""
    elems = [e for e in elems if e is not None]
    if not doc.IsWorkshared:
        return elems, []
    ok, blocked = [], []
    for e in elems:
        why = None
        try:
            st = _WGDB.WorksharingUtils.GetCheckoutStatus(doc, e.Id)
            if st == _WGDB.CheckoutStatus.OwnedByOtherUser:
                who = u''
                try:
                    who = _WGDB.WorksharingUtils.GetWorksharingTooltipInfo(doc, e.Id).Owner or u''
                except Exception:
                    pass
                why = u'被 %s 占用' % (who or u'别人')
            else:
                up = _WGDB.WorksharingUtils.GetModelUpdatesStatus(doc, e.Id)
                if up in (_WGDB.ModelUpdatesStatus.UpdatedInCentral, _WGDB.ModelUpdatesStatus.DeletedInCentral):
                    why = u'中心文件里已被改过，需先同步'
        except Exception:
            why = None
        if why:
            blocked.append((e, why))
        else:
            ok.append(e)
    return ok, blocked


def wg_note(blocked, label=None, n=6):
    """确认框里的一段说明：几个改不了、谁占着、示例。label(e) 给出图元的称呼（如井号）。"""
    if not blocked:
        return u''
    by = {}
    for e, why in blocked:
        by.setdefault(why, []).append(e)
    lines = [u'\n⚠ %d 个改不了，会跳过（其余照常写入）：' % len(blocked)]
    for why, es in sorted(by.items(), key=lambda kv: -len(kv[1])):
        names = []
        for e in es[:n]:
            try:
                names.append((label(e) if label else u'') or u'%s' % e.Id.Value)
            except Exception:
                names.append(u'%s' % e.Id.Value)
        lines.append(u'  · %s：%d 个（%s%s）' % (why, len(es), u'、'.join(names), u' 等' if len(es) > n else u''))
    lines.append(u'  请对方同步并释放，或先「与中心文件同步」后再运行一次。')
    return u'\n'.join(lines)
# ---- ws_guard 结束 ------------------------------------------------------------------

def load():
    s = dict(DEFAULTS)
    try:
        with io.open(SETTINGS, 'r', encoding='utf-8') as f:
            s.update(json.load(f))
    except Exception:
        pass
    return s


def save(s):
    try:
        d = os.path.dirname(SETTINGS)
        if not os.path.isdir(d):
            os.makedirs(d)
        with io.open(SETTINGS, 'w', encoding='utf-8') as f:
            f.write(json.dumps(s, ensure_ascii=False, indent=2))
    except Exception:
        pass


def center(e):
    loc = e.Location
    if isinstance(loc, DB.LocationPoint):
        return loc.Point
    if isinstance(loc, DB.LocationCurve):
        return loc.Curve.Evaluate(0.5, True)
    bb = e.get_BoundingBox(None)
    if bb is None:
        return None
    return (bb.Min + bb.Max) / 2.0


def target_param(e, name):
    if name in ('标记', 'Mark'):
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        if p is not None:
            return p
    return e.LookupParameter(name)


def comment(e):
    p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    return (p.AsString() or '') if (p is not None and p.HasValue) else ''


def dialog(s, n_sel):
    f = WF.Form()
    f.Text = TITLE
    f.Font = SD.Font('Microsoft YaHei UI', 9.0)
    f.StartPosition = WF.FormStartPosition.CenterScreen
    f.FormBorderStyle = WF.FormBorderStyle.FixedDialog
    f.MaximizeBox = f.MinimizeBox = False
    y = [14]

    def lab(t, x=16, w=110):
        l = WF.Label(); l.Text = t; l.Location = SD.Point(x, y[0] + 3); l.Size = SD.Size(w, 20); f.Controls.Add(l)

    def box(v, x=130, w=120):
        t = WF.TextBox(); t.Text = v; t.Location = SD.Point(x, y[0]); t.Size = SD.Size(w, 24); f.Controls.Add(t); return t

    def num(v, lo, hi, x=130, w=80):
        n = WF.NumericUpDown(); n.Minimum = lo; n.Maximum = hi; n.Value = max(lo, min(hi, int(v)))
        n.Location = SD.Point(x, y[0]); n.Size = SD.Size(w, 24); f.Controls.Add(n); return n

    info = WF.Label(); info.Text = ('已选 %d 个构件。' % n_sel) if n_sel else '没有预先选中构件：确定后在视图里框选。'
    info.Location = SD.Point(16, y[0]); info.Size = SD.Size(440, 20); f.Controls.Add(info); y[0] += 30
    lab('前缀'); t_pre = box(s['prefix']); y[0] += 30
    lab('起始号'); n_start = num(s['start'], 0, 999999); lab('位数', 230, 40); n_dig = num(s['digits'], 1, 6, 274, 60)
    lab('步长', 346, 40); n_step = num(s['step'], 1, 1000, 390, 60); y[0] += 30
    lab('写入参数'); t_par = box(s['param']); y[0] += 30
    lab('注释含（可空）'); t_fil = box(s['filter'], w=200); y[0] += 30
    lab('排序方式'); y[0] += 22
    cb = WF.ComboBox(); cb.DropDownStyle = WF.ComboBoxStyle.DropDownList
    for _k, t in SORTS:
        cb.Items.Add(t)
    keys = [k for k, _t in SORTS]
    cb.SelectedIndex = keys.index(s['sort']) if s['sort'] in keys else 0
    cb.Location = SD.Point(16, y[0]); cb.Size = SD.Size(440, 24); f.Controls.Add(cb); y[0] += 32
    c_emp = WF.CheckBox(); c_emp.Text = '只给参数还空着的构件编号（已有编号的不动，新号自动跳过已用的号）'
    c_emp.Checked = bool(s['only_empty']); c_emp.Location = SD.Point(16, y[0]); c_emp.Size = SD.Size(440, 22)
    f.Controls.Add(c_emp); y[0] += 34
    ok = WF.Button(); ok.Text = '预览…'; ok.DialogResult = WF.DialogResult.OK; ok.Location = SD.Point(270, y[0]); ok.Size = SD.Size(90, 30)
    ca = WF.Button(); ca.Text = '取消'; ca.DialogResult = WF.DialogResult.Cancel; ca.Location = SD.Point(366, y[0]); ca.Size = SD.Size(90, 30)
    f.Controls.Add(ok); f.Controls.Add(ca); f.AcceptButton = ok; f.CancelButton = ca
    f.ClientSize = SD.Size(472, y[0] + 44)
    if ff_fit(f).ShowDialog() != WF.DialogResult.OK:
        return None
    return {'prefix': t_pre.Text, 'start': int(n_start.Value), 'digits': int(n_dig.Value), 'step': int(n_step.Value),
            'param': (t_par.Text or '标记').strip(), 'filter': t_fil.Text.strip(), 'sort': keys[cb.SelectedIndex],
            'only_empty': c_emp.Checked}


class _LineFilter(ISelectionFilter):
    def AllowElement(self, e):
        return isinstance(e, DB.CurveElement)

    def AllowReference(self, r, p):
        return False


def order(items, mode):
    """items: [(e, 点)]。返回排好序的列表和说明。"""
    if mode in ('X_ASC', 'X_DESC'):
        return sorted(items, key=lambda t: (t[1].X, t[1].Y), reverse=(mode == 'X_DESC')), ''
    if mode in ('Y_ASC', 'Y_DESC'):
        return sorted(items, key=lambda t: (t[1].Y, t[1].X), reverse=(mode == 'Y_DESC')), ''
    if mode == 'PATH':
        r = uidoc.Selection.PickObject(ObjectType.Element, _LineFilter(), '点选一条模型线或详图线（按投影到这条线上的位置编号，从线的起点开始）')
        crv = doc.GetElement(r.ElementId).GeometryCurve
        out = []
        far = 0
        for e, p in items:
            q = DB.XYZ(p.X, p.Y, crv.GetEndPoint(0).Z)
            res = crv.Project(q)
            if res is None:
                continue
            out.append((crv.ComputeNormalizedParameter(res.Parameter), e, p))
            if res.Distance * 304.8 > 50000:
                far += 1
        out.sort(key=lambda t: t[0])
        note = ('（%d 个构件离这条线超过 50 m，请确认选对了线）' % far) if far else ''
        return [(e, p) for _t, e, p in out], note
    if mode == 'CHAIN':
        r = uidoc.Selection.PickObject(ObjectType.Element, '点选第一个（起点）构件')
        rest = list(items)
        cur = None
        for i, (e, p) in enumerate(rest):
            if e.Id == r.ElementId:
                cur = rest.pop(i)
                break
        if cur is None:
            return order(items, 'X_ASC')[0], '（点选的构件不在要编号的范围里，改为 X 递增）'
        out = [cur]
        while rest:
            j = min(range(len(rest)), key=lambda k: rest[k][1].DistanceTo(out[-1][1]))
            out.append(rest.pop(j))
        return out, ''
    return items, ''


def main():
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    sel = [e for e in sel if e is not None and e.Category is not None and not isinstance(e, DB.ElementType)]
    s = dialog(load(), len(sel))
    if s is None:
        return
    save(s)
    if not sel:
        try:
            sel = list(uidoc.Selection.PickElementsByRectangle('框选要编号的构件'))
        except Exception:
            return
    pool = []
    skipped_filter = skipped_param = kept = 0
    for e in sel:
        if s['filter'] and s['filter'].lower() not in comment(e).lower():
            skipped_filter += 1
            continue
        p = target_param(e, s['param'])
        if p is None or p.IsReadOnly or p.StorageType != DB.StorageType.String:
            skipped_param += 1
            continue
        if s['only_empty'] and (p.AsString() or '').strip():
            kept += 1
            continue
        c = center(e)
        if c is not None:
            pool.append((e, c))
    # 协同模型：被别人占用的构件改不了，先排除（不占号），确认框里列出来
    _ok, blocked = wg_split(doc, [e for e, _c in pool])
    if blocked:
        bad = set(e.Id for e, _w in blocked)
        pool = [(e, c) for e, c in pool if e.Id not in bad]
    if not pool:
        forms.alert('没有可编号的构件。\n注释不符：%d 个；没有可写的「%s」文字参数：%d 个；已有编号保留：%d 个。%s'
                    % (skipped_filter, s['param'], skipped_param, kept, wg_note(blocked)), title=TITLE)
        return
    try:
        ordered, note = order(pool, s['sort'])
    except Exception:
        return   # 点选时按了 Esc
    # 「只给空着的编号」时，模型里同类构件已经用掉的号跳过，不会编出重号
    used = set()
    if s['only_empty']:
        for cid in set(e.Category.Id for e, _p in ordered):
            for o in DB.FilteredElementCollector(doc).OfCategoryId(cid).WhereElementIsNotElementType():
                op = target_param(o, s['param'])
                v = (op.AsString() or '').strip() if op is not None else ''
                if v:
                    used.add(v)
    plan = []
    k = 0
    for e, _p in ordered:
        while True:
            v = '%s%s' % (s['prefix'], str(s['start'] + k * s['step']).zfill(s['digits']))
            k += 1
            if v not in used:
                break
        plan.append((e, v))
    # 和模型里其它（不在本次范围里的）同类构件重号
    mine = set(e.Id for e, _v in plan)
    new_vals = set(v for _e, v in plan)
    clash = []
    cats = set(e.Category.Id for e, _v in plan)
    for cid in cats:
        for o in DB.FilteredElementCollector(doc).OfCategoryId(cid).WhereElementIsNotElementType():
            if o.Id in mine:
                continue
            op = target_param(o, s['param'])
            v = (op.AsString() or '') if op is not None else ''
            if v and v in new_vals:
                clash.append(v)
    lines = ['%s → %s' % ((target_param(e, s['param']).AsString() or '（空）'), v) for e, v in plan[:12]]
    msg = '将给 %d 个构件写入「%s」：\n\n%s%s' % (len(plan), s['param'], '\n'.join(lines), '\n…' if len(plan) > 12 else '')
    if note:
        msg += '\n\n' + note
    if skipped_filter or skipped_param or kept:
        msg += '\n\n跳过：注释不符 %d、没有可写参数 %d、已有编号保留 %d。' % (skipped_filter, skipped_param, kept)
    if clash:
        msg += '\n\n⚠ 和模型里其它构件重号 %d 个：%s' % (len(set(clash)), '、'.join(sorted(set(clash))[:10]))
    msg += wg_note(blocked, lambda e: (target_param(e, s['param']).AsString() or '').strip())
    msg += '\n\n写入？（可 Ctrl+Z 撤销）'
    if not forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
        return
    t = DB.Transaction(doc, '天河落 · 一键编号')
    t.Start()
    n = 0
    try:
        for e, v in plan:
            target_param(e, s['param']).Set(v)
            n += 1
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        forms.alert('写入失败，已撤销：\n' + traceback.format_exc(), title=TITLE)
        return
    forms.alert('已编号 %d 个（%s ~ %s）。' % (n, plan[0][1], plan[-1][1]), title=TITLE)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        forms.alert('出错了：\n' + traceback.format_exc(), title=TITLE)
