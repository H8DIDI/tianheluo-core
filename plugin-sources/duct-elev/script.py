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
