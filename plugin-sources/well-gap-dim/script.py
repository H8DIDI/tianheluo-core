# -*- coding: utf-8 -*-
__title__ = '井间距自动标注'
__doc__ = ('在当前平面视图里，自动给「同一条排管路由上相邻的两口井」标注井中心到中心的距离：\n'
           '先按排管的连接关系找出同一条排管上相邻的井（排管是一段一段的，端点相接、走向基本不拐的连成一条，到井为止；拐到别的支线上的井不算），'
           '同一直线上连续的井合并成一条连续标注，参照的是井族自带的「中心（左/右）」「中心（前/后）」参照平面，数值是准的、井挪了会跟着变。\n'
           '视图里已经手工标好且正确的不重复标；标错的（参照的不是井中心、两口井之间没有排管连接、同一口井参了两次、标注文字被手改）可以一并删掉重标。'
           '先在窗口里列出「要删什么、要新增什么」，确认后一次执行，整次一个事务，Ctrl+Z 可撤销。\n'
           '另有「排管横向中心距标注」：相邻、平行并排的排管，在并行段上沿排管自带的中心线连续标出横向中心距（间距上限、并行长度、标注位置可在设置里改）。')

import re
import math
import traceback
from pyrevit import revit, DB, UI, forms

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

# ---- well_config（天河落共用片段，打包时内联进脚本）-----------------------------
# 井大样系列插件（一键井展开图 / 一键井大样标注 / 图纸归类与图框编号 / 导出井大样DWG）共用的项目配置。
# 配置文件：文档\KaifaTools\井大样配置.json。第一次运行时自动写出一份默认配置（与原来写死的规则完全一样），
# 换项目时只改这个文件：图纸名前缀、图号前缀、井编号系列（前缀 → 系统名 → 系统代码）、图框 logo 位置。
import io as _wc_io
import os as _wc_os
import json as _wc_json
import re as _wc_re

WCFG_FILE = _wc_os.path.join(
    __import__('System').Environment.GetFolderPath(__import__('System').Environment.SpecialFolder.MyDocuments),
    u"KaifaTools", u"井大样配置.json")

WCFG_DEFAULTS = {
    u"说明": (u"井大样系列插件的项目配置。改完保存，下次点插件生效；删掉这个文件会恢复默认。"
            u"series 里每一项是一个井编号系列：prefix=井号前缀（井号形如 AA-01、T-068、室内-A-01，前缀后面可以有一个 -，再跟数字），"
            u"system=系统名（写进图纸的「图纸系统」参数、用来归类），code=系统代码（图号模板里的 {系统码}），label=菜单里显示的名字。"
            u"顺序就是菜单和排序的顺序；匹配时长的前缀优先（AA 先于 A）。"),
    u"sheet_name_prefix": u"井大样_",
    u"sheet_number_prefix": u"AG-",
    u"indoor_prefix": u"室内-",
    u"system_param": u"图纸系统",
    u"series": [
        {u"prefix": u"室内-A", u"system": u"室内强电井", u"code": u"NQ", u"label": u"室内-A- 强电井"},
        {u"prefix": u"室内-T", u"system": u"室内通信井", u"code": u"NT", u"label": u"室内-T- 通信井"},
        {u"prefix": u"室内-E", u"system": u"室内弱电井", u"code": u"NR", u"label": u"室内-E- 弱电井"},
        {u"prefix": u"室内-L", u"system": u"室内低压拉线井", u"code": u"ND", u"label": u"室内-L- 低压拉线井"},
        {u"prefix": u"AA", u"system": u"强电井", u"code": u"QD", u"label": u"AA- 强电特型井"},
        {u"prefix": u"A", u"system": u"强电井", u"code": u"QD", u"label": u"A- 强电井"},
        {u"prefix": u"BB", u"system": u"弱电井", u"code": u"RD", u"label": u"BB- 弱电特型井"},
        {u"prefix": u"B", u"system": u"弱电井", u"code": u"RD", u"label": u"B- 弱电井"},
        {u"prefix": u"T", u"system": u"通信井", u"code": u"TX", u"label": u"T- 通信井"},
        {u"prefix": u"L", u"system": u"低压拉线井", u"code": u"DY", u"label": u"L- 低压拉线井"},
        {u"prefix": u"E", u"system": u"预制井", u"code": u"YZ", u"label": u"E- 预制井"},
        {u"prefix": u"Y", u"system": u"雨水井", u"code": u"YS", u"label": u"Y- 雨水井"},
    ],
    u"other": {u"system": u"其他", u"code": u"QT"},
    # 图名模板（按井号前缀，长前缀优先）：{井号} 换成井号，如 T-001。没有模板的井沿用「井大样_」前缀。
    # 图纸集合（按井号前缀）：新图放进对应的图纸集合，便于按集合导出 PDF。名字须与项目里的图纸集合一致。
    u"sheet_collection_by_prefix": {
        u"AA": u"275KV井大样", u"BB": u"275KV井大样", u"A": u"11KV井大样", u"B": u"11KV井大样",
        u"E": u"ELV井大样", u"L": u"LV井大样", u"T": u"TELCO井大样",
    },
    u"sheet_name_templates": {
        u"AA": u"BTP-IF1 275Kv-{井号} Detail Drawing",
        u"BB": u"BTP-IF1 275Kv-{井号} Detail Drawing",
        u"A": u"BTP-IF1 11Kv-{井号} Detail Drawing",
        u"B": u"BTP-IF1 11Kv-{井号} Detail Drawing",
        u"E": u"BTP-IF1 ELV-{井号} Detail Drawing",
        u"L": u"BTP-IF1 Lv-{井号} Detail Drawing",
        u"T": u"BTP-IF1 Telco-{井号} Detail Drawing",
    },
    u"logo": {u"enabled": True, u"file": u"frame_logo.jpg", u"x0": 736.54, u"y0": 141.10, u"w": 91.49,
              u"说明": u"导出 DWG 后补插的图框 logo：x0/y0 = logo 左下角在 Layout1 里的坐标，w = 宽度（按 A1 横向图框标定）。换图框要改；enabled=false 不补 logo。"},
}

_WCFG_CACHE = [None]
# 本次运行之前配置文件还不存在（第一次在这台电脑上用井大样插件）。wcfg_load 会写出默认文件，所以要在调用它之前记下来。
WCFG_FIRST_RUN = not _wc_os.path.exists(WCFG_FILE)


def _wcfg_merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _wcfg_merge(out[k], v)
        else:
            out[k] = v
    return out


def wcfg_load(write_default=True):
    """读项目配置（缺的项用默认值补齐）。文件不存在时写出默认配置，方便用户找到并修改。"""
    if _WCFG_CACHE[0] is not None:
        return _WCFG_CACHE[0]
    cfg = _wcfg_merge(WCFG_DEFAULTS, {})
    try:
        if _wc_os.path.exists(WCFG_FILE):
            with _wc_io.open(WCFG_FILE, 'r', encoding='utf-8-sig') as f:
                cfg = _wcfg_merge(cfg, _wc_json.load(f))
        elif write_default:
            d = _wc_os.path.dirname(WCFG_FILE)
            if not _wc_os.path.isdir(d):
                _wc_os.makedirs(d)
            with _wc_io.open(WCFG_FILE, 'w', encoding='utf-8') as f:
                f.write(_wc_json.dumps(WCFG_DEFAULTS, ensure_ascii=False, indent=2))
    except Exception:
        cfg = _wcfg_merge(WCFG_DEFAULTS, {})
    # series 至少要有 prefix
    good = []
    for s in cfg.get(u"series") or []:
        try:
            p = (s.get(u"prefix") or u"").strip()
        except Exception:
            continue
        if not p:
            continue
        s = dict(s)
        s[u"prefix"] = p
        s.setdefault(u"system", p)
        s.setdefault(u"code", p)
        s.setdefault(u"label", p + u"-")
        good.append(s)
    cfg[u"series"] = good or list(WCFG_DEFAULTS[u"series"])
    _WCFG_CACHE[0] = cfg
    return cfg


def wcfg_series_of(mark, cfg=None, strict=False):
    """井号属于哪个系列：前缀（长的优先）后面可以有一个 -，必须紧跟数字。认不出返回 None。
    strict=True 时前缀后面必须是「-数字」（井的标记用这个：雨水井的 A1、BB24 这类不带 - 的不算强电/弱电井）。"""
    cfg = cfg or wcfg_load()
    m = (mark or u"").strip()
    for s in sorted(cfg[u"series"], key=lambda x: -len(x[u"prefix"])):
        p = s[u"prefix"]
        if m.startswith(p):
            rest = m[len(p):]
            if rest[:1] == u"-":
                rest = rest[1:]
            elif strict:
                continue
            if rest[:1].isdigit():
                return s
    return None


def wcfg_series_index(s, cfg=None):
    cfg = cfg or wcfg_load()
    for i, x in enumerate(cfg[u"series"]):
        if x is s:
            return i
    return len(cfg[u"series"])


def wcfg_natural_key(text):
    out = []
    for tok in _wc_re.split(r'(\d+)', text or u''):
        out.append((0, int(tok), u'') if tok.isdigit() else (1, 0, tok.lower()))
    return out


def wcfg_well_sort_key(mark, cfg=None, strict=True):
    """先按系列在配置里的顺序，同系列按号（自然排序：A-2 在 A-10 前面）。"""
    s = wcfg_series_of(mark, cfg, strict)
    return (wcfg_series_index(s, cfg) if s is not None else 9999, wcfg_natural_key(mark))


def wcfg_sheet_name(mark, cfg=None):
    """井号 → 图名（按配置里的图名模板）。没有对应模板时返回 None，调用方沿用「井大样_」前缀。
    模板前缀按长度从长到短匹配，且前缀后面必须紧跟 "-"（AA-04 不会误判成 A 系列）。"""
    cfg = cfg or wcfg_load()
    tpls = cfg.get(u"sheet_name_templates") or {}
    m = (mark or u"").strip()
    for key in sorted(tpls.keys(), key=lambda k: -len(k)):
        if m.startswith(key + u"-"):
            return tpls[key].replace(u"{井号}", m)
    return None
# ---- well_config 结束 ----------------------------------------------------------


def wcfg_collection_name(mark, cfg=None):
    """井号 → 图纸集合名（按配置 sheet_collection_by_prefix，长前缀优先，前缀后紧跟 -）。没有对应返回 None。"""
    cfg = cfg or wcfg_load()
    maps = cfg.get(u"sheet_collection_by_prefix") or {}
    m = (mark or u"").strip()
    for key in sorted(maps.keys(), key=lambda k: -len(k)):
        if m.startswith(key + u"-"):
            return maps[key]
    return None


# ---- 井位置缓存（井没动就不重新生成）：Documents\KaifaTools\井大样位置缓存.json ----
def wcfg_cache_path():
    return _wc_os.path.join(_wc_os.path.dirname(WCFG_FILE), u"井大样位置缓存.json")


def wcfg_cache_load():
    """读位置缓存：{井号: {bbox, sheet_id, name, number}}。文件不存在或坏了返回空。"""
    try:
        with _wc_io.open(wcfg_cache_path(), 'r', encoding='utf-8-sig') as f:
            d = _wc_json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def wcfg_cache_save(d):
    """先把内容转成文字，转成功才写文件（避免转换失败时把旧缓存清成空文件）。"""
    try:
        txt = _wc_json.dumps(d, ensure_ascii=False, indent=1)
    except Exception:
        return
    try:
        p = wcfg_cache_path()
        dd = _wc_os.path.dirname(p)
        if not _wc_os.path.isdir(dd):
            _wc_os.makedirs(dd)
        with _wc_io.open(p, 'w', encoding='utf-8') as f:
            f.write(txt)
    except Exception:
        pass

# ---- settings_ui（天河落共用片段，打包时内联进脚本）-----------------------------
# 图形化「设置」窗口：每个参数一行（中文名、输入框/下拉框/勾选、单位和说明），保存、恢复默认、取消。
# 依赖 form_fit（插件里要先 include _shared/form_fit.py）。
# 用法：su_fields_dialog(标题, 说明, 字段列表, 当前值, 默认值) -> 新值字典 或 None（取消）。
# 字段 dict：key、label、kind（header 分组标题 / num 数字 / text 文本 / list 逗号分隔的列表 / choice 下拉（只能选）/
#           combo 下拉（可选可手填）/ bool 勾选 / palette 颜色列表「R,G,B; R,G,B」/ lines 多行文本，每行一项，rows 行高）、hint 说明、
#           choices [(值, 显示文字), ...]、min、max、int（数字取整）。
import re as _su_re
import clr as _su_clr
_su_clr.AddReference('System.Windows.Forms')
_su_clr.AddReference('System.Drawing')
import System.Windows.Forms as _SUW
import System.Drawing as _SUD
import System.Diagnostics as _SUDG


def su_open_file(path):
    """用记事本打开设置文件。"""
    try:
        _SUDG.Process.Start(u"notepad.exe", path)
    except Exception:
        pass


def _su_split(text):
    return [x.strip() for x in _su_re.split(u"[,，;；\\n\\r]+", text or u"") if x.strip()]


def _su_to_text(fld, val):
    kind = fld.get(u"kind", u"text")
    if kind == u"list":
        return u", ".join(u"%s" % x for x in (val or []))
    if kind == u"lines":
        return u"\r\n".join(u"%s" % x for x in (val or []))
    if kind == u"palette":
        return u"; ".join(u"%d,%d,%d" % tuple(c[:3]) for c in (val or []))
    if kind == u"num":
        try:
            v = float(val)
            return (u"%d" % v) if v == int(v) else (u"%g" % v)
        except Exception:
            return u"" if val is None else u"%s" % val
    return u"" if val is None else u"%s" % val


def su_fields_dialog(title, intro, fields, values, defaults=None, extra_buttons=None, width=680, height=640):
    """弹出设置窗口。extra_buttons: [(按钮文字, 回调)]，回调不带参数（如「用记事本打开文件」）。返回新值 dict 或 None。"""
    f = _SUW.Form()
    f.Text = title
    f.Font = _SUD.Font(u"Microsoft YaHei UI", 9.0)
    f.StartPosition = _SUW.FormStartPosition.CenterScreen
    f.FormBorderStyle = _SUW.FormBorderStyle.FixedDialog
    f.MaximizeBox = False
    f.MinimizeBox = False
    f.ClientSize = _SUD.Size(width, height)
    lb = _SUW.Label()
    lb.Text = intro
    lb.Location = _SUD.Point(14, 10)
    lb.Size = _SUD.Size(width - 28, 44)
    f.Controls.Add(lb)
    panel = _SUW.Panel()
    panel.Location = _SUD.Point(10, 58)
    panel.Size = _SUD.Size(width - 20, height - 58 - 54)
    panel.AutoScroll = True
    panel.BorderStyle = _SUW.BorderStyle.FixedSingle
    f.Controls.Add(panel)

    ctrls = {}
    y = 8
    for fld in fields:
        kind = fld.get(u"kind", u"text")
        if kind == u"header":
            l = _SUW.Label()
            l.Text = fld[u"label"]
            l.Font = _SUD.Font(f.Font, _SUD.FontStyle.Bold)
            l.Location = _SUD.Point(6, y + 6)
            l.Size = _SUD.Size(width - 60, 22)
            panel.Controls.Add(l)
            y += 34
            continue
        l = _SUW.Label()
        l.Text = fld[u"label"]
        l.Location = _SUD.Point(6, y + 3)
        l.Size = _SUD.Size(250, 34)
        panel.Controls.Add(l)
        key = fld[u"key"]
        if kind in (u"choice", u"combo"):
            c = _SUW.ComboBox()
            c.DropDownStyle = _SUW.ComboBoxStyle.DropDownList if kind == u"choice" else _SUW.ComboBoxStyle.DropDown
            for _v, _t in fld.get(u"choices", []):
                c.Items.Add(_t)
            c.Location = _SUD.Point(264, y)
            c.Size = _SUD.Size(width - 310, 24)
        elif kind == u"bool":
            c = _SUW.CheckBox()
            c.Text = fld.get(u"bool_text", u"启用")
            c.Location = _SUD.Point(264, y + 2)
            c.Size = _SUD.Size(width - 310, 22)
        elif kind == u"lines":
            c = _SUW.TextBox()
            c.Multiline = True
            c.ScrollBars = _SUW.ScrollBars.Both
            c.WordWrap = False
            c.AcceptsReturn = True
            c.Font = _SUD.Font(u"Consolas", 9.0)
            rows = int(fld.get(u"rows", 6))
            c.Location = _SUD.Point(264, y)
            c.Size = _SUD.Size(width - 310, rows * 18 + 8)
        else:
            c = _SUW.TextBox()
            c.Location = _SUD.Point(264, y)
            c.Size = _SUD.Size(width - 310, 24)
        panel.Controls.Add(c)
        ctrls[key] = c
        y += (int(fld.get(u"rows", 6)) * 18 + 14) if kind == u"lines" else 30
        if fld.get(u"hint"):
            h = _SUW.Label()
            h.Text = fld[u"hint"]
            h.ForeColor = _SUD.Color.FromArgb(110, 110, 110)
            h.Location = _SUD.Point(264, y - 2)
            h.Size = _SUD.Size(width - 310, 34)
            panel.Controls.Add(h)
            y += 36
        y += 6

    def put(fld, val):
        c = ctrls.get(fld[u"key"])
        if c is None:
            return
        kind = fld.get(u"kind", u"text")
        if kind == u"bool":
            c.Checked = bool(val)
        elif kind in (u"choice", u"combo"):
            chs = fld.get(u"choices", [])
            idx = [i for i, (v, t) in enumerate(chs) if v == val]
            if idx:
                c.SelectedIndex = idx[0]
            elif kind == u"combo":
                c.Text = u"" if val is None else u"%s" % val
            elif chs:
                c.SelectedIndex = 0
        else:
            c.Text = _su_to_text(fld, val)

    def get(fld):
        """读控件的值并校验；不合格抛 ValueError(说明)。"""
        c = ctrls[fld[u"key"]]
        kind = fld.get(u"kind", u"text")
        name = fld[u"label"]
        if kind == u"bool":
            return bool(c.Checked)
        if kind == u"choice":
            chs = fld.get(u"choices", [])
            return chs[c.SelectedIndex][0] if 0 <= c.SelectedIndex < len(chs) else None
        if kind == u"combo":
            txt = (c.Text or u"").strip()
            for v, t in fld.get(u"choices", []):
                if t == txt:
                    return v
            return txt
        txt = (c.Text or u"").strip()
        if kind == u"num":
            try:
                v = float(txt)
            except Exception:
                raise ValueError(u"「%s」要填数字。" % name)
            if fld.get(u"min") is not None and v < fld[u"min"]:
                raise ValueError(u"「%s」不能小于 %s。" % (name, fld[u"min"]))
            if fld.get(u"max") is not None and v > fld[u"max"]:
                raise ValueError(u"「%s」不能大于 %s。" % (name, fld[u"max"]))
            return int(round(v)) if fld.get(u"int") else v
        if kind == u"list":
            return _su_split(txt)
        if kind == u"lines":
            return [x.strip() for x in (c.Text or u"").splitlines() if x.strip()]
        if kind == u"palette":
            out = []
            for part in [x.strip() for x in _su_re.split(u"[;；\n\r]+", txt) if x.strip()]:
                nums = [x for x in _su_re.split(u"[ ,]+", part) if x]
                try:
                    rgb = [int(x) for x in nums[:3]]
                except Exception:
                    rgb = []
                if len(rgb) != 3 or any(n < 0 or n > 255 for n in rgb):
                    raise ValueError(u"「%s」里「%s」不是有效的颜色，格式是 R,G,B（0–255），多个颜色用分号隔开。" % (name, part))
                out.append(rgb)
            if not out:
                raise ValueError(u"「%s」至少要有一个颜色。" % name)
            return out
        if fld.get(u"required") and not txt:
            raise ValueError(u"「%s」不能为空。" % name)
        return txt

    for fld in fields:
        if fld.get(u"kind") != u"header":
            put(fld, values.get(fld[u"key"]))

    result = {u"v": None}
    b_ok = _SUW.Button()
    b_ok.Text = u"保存"
    b_ok.Name = u"su_ok"
    b_ok.Location = _SUD.Point(width - 200, height - 44)
    b_ok.Size = _SUD.Size(88, 30)
    b_ca = _SUW.Button()
    b_ca.Text = u"取消"
    b_ca.Name = u"su_cancel"
    b_ca.Location = _SUD.Point(width - 100, height - 44)
    b_ca.Size = _SUD.Size(88, 30)
    f.Controls.Add(b_ok)
    f.Controls.Add(b_ca)
    f.CancelButton = b_ca
    bx = 14
    if defaults:
        b_def = _SUW.Button()
        b_def.Text = u"恢复默认"
        b_def.Name = u"su_default"
        b_def.Location = _SUD.Point(bx, height - 44)
        b_def.Size = _SUD.Size(88, 30)
        f.Controls.Add(b_def)
        bx += 96

        def _on_def(s, e):
            for fld in fields:
                if fld.get(u"kind") != u"header" and fld[u"key"] in defaults:
                    put(fld, defaults[fld[u"key"]])
        b_def.Click += _on_def
    for text, cb in (extra_buttons or []):
        b = _SUW.Button()
        b.Text = text
        b.Location = _SUD.Point(bx, height - 44)
        b.Size = _SUD.Size(130, 30)
        f.Controls.Add(b)
        bx += 138
        b.Click += (lambda s, e, cb=cb: cb())

    def _on_ok(s, e):
        out = {}
        try:
            for fld in fields:
                if fld.get(u"kind") != u"header":
                    out[fld[u"key"]] = get(fld)
        except ValueError as ex:
            _SUW.MessageBox.Show(u"%s" % ex, title)
            return
        result[u"v"] = out
        f.Close()
    b_ok.Click += _on_ok
    b_ca.Click += (lambda s, e: f.Close())
    ff_fit(f).ShowDialog()
    return result[u"v"]

import io
import os
import json
import clr
import System
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import System.Windows.Forms as WF
import System.Drawing as SD

doc = revit.doc
uidoc = revit.uidoc
WCFG = wcfg_load()
TITLE = u'井间距自动标注'
FT = 304.8
RT = DB.FamilyInstanceReferenceType
BANK_PREFIXES = (u'TELCO', u'ELV', u'LV-', u'ELE-')          # 排管族名前缀（雨水沟不是排管）
LINK_TOL = 0.3 / 0.3048          # 排管两段首尾相接的容差（英尺，约 0.3 m）
WELL_TOL = 0.15 / 0.3048         # 排管端点落进井外框的容差
ANG_COS = math.cos(math.radians(3.0))        # 井的中心面和标注方向差 3° 以内算平行
CHORD_COS = math.cos(math.radians(5.0))      # 两井连线和标注方向差 5° 以内算「基本在一条线上」
STRAIGHT_COS = math.cos(math.radians(10.0))      # 排管接点处拐角不超过 10° 才算同一条直排管
CLUSTER_DZ = 1.0 / 0.3048        # 端点相接时，高差不超过 1 m 才算同一接点


def _idv(i):
    try:
        return i.Value
    except Exception:
        return i.IntegerValue


def _mark(e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        return (p.AsString() or u'').strip() if p is not None else u''
    except Exception:
        return u''


def _is_bank(e):
    try:
        f = doc.GetElement(e.GetTypeId()).Family.Name.upper()
    except Exception:
        return False
    return any(f.startswith(k) for k in BANK_PREFIXES)


def collect_wells(view):
    """视图里的井：{id: 信息}。井 = 常规模型族实例，标记属于配置里的某个井系列。"""
    wells = {}
    col = DB.FilteredElementCollector(doc, view.Id).OfCategory(DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType()
    for e in col:
        if not isinstance(e, DB.FamilyInstance):
            continue
        mk = _mark(e)
        if not mk or wcfg_series_of(mk, WCFG, True) is None:
            continue
        bb = e.get_BoundingBox(None)
        loc = e.Location
        if bb is None or not isinstance(loc, DB.LocationPoint):
            continue
        p = loc.Point
        wells[_idv(e.Id)] = {'e': e, 'mark': mk, 'c': (p.X, p.Y, p.Z), 'bb': (bb.Min.X, bb.Min.Y, bb.Max.X, bb.Max.Y),
                             'hand': (e.HandOrientation.X, e.HandOrientation.Y), 'face': (e.FacingOrientation.X, e.FacingOrientation.Y)}
    return wells


def collect_banks():
    out = []
    for e in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType():
        if isinstance(e.Location, DB.LocationCurve) and _is_bank(e):
            out.append(e)
    return out


def build_edges(wells, banks):
    """排管路由图 → 相邻的井对。返回 {(小id, 大id)}。
    排管是一段一段的：端点在 0.3 m 内相接的并成一个接点；端点落在井外框里的算到井上。从每口井出发沿排管走，走到下一口井为止，就是相邻。"""
    pts = []
    for b in banks:
        c = b.Location.Curve
        pts.append(c.GetEndPoint(0))
        pts.append(c.GetEndPoint(1))
    parent = list(range(len(pts)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    grid = {}
    for i, pt in enumerate(pts):
        gx, gy = int(math.floor(pt.X / LINK_TOL)), int(math.floor(pt.Y / LINK_TOL))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((gx + dx, gy + dy), ()):
                    q = pts[j]
                    if (q.X - pt.X) ** 2 + (q.Y - pt.Y) ** 2 <= LINK_TOL * LINK_TOL and abs(q.Z - pt.Z) <= CLUSTER_DZ:
                        parent[find(i)] = find(j)
        grid.setdefault((gx, gy), []).append(i)

    def well_at(pt):
        best = None
        for k, w in wells.items():
            x0, y0, x1, y1 = w['bb']
            if x0 - WELL_TOL <= pt.X <= x1 + WELL_TOL and y0 - WELL_TOL <= pt.Y <= y1 + WELL_TOL:
                dd = (pt.X - w['c'][0]) ** 2 + (pt.Y - w['c'][1]) ** 2
                if best is None or dd < best[0]:
                    best = (dd, k)
        return best[1] if best else None
    adj = {}
    for bi in range(len(banks)):
        vs = []
        for k in (0, 1):
            i = bi * 2 + k
            w = well_at(pts[i])
            vs.append(('w', w) if w is not None else ('c', find(i)))
        c = banks[bi].Location.Curve
        d = c.GetEndPoint(1) - c.GetEndPoint(0)
        L = math.hypot(d.X, d.Y)
        if L < 1e-6:
            continue
        u = (d.X / L, d.Y / L)
        adj.setdefault(vs[0], []).append((vs[1], u))
        adj.setdefault(vs[1], []).append((vs[0], (-u[0], -u[1])))
    # 只沿「直通」的排管走：到接点后，只接着走方向基本不拐（10° 内）的那一段。
    # 这样同一条排管上相邻的井才算相邻；拐到别的支线、绕过三通去到别的排管上的井不算（那种标注是错的）。
    edges = set()
    for wk in wells:
        start = ('w', wk)
        seen = set([start])
        stack = [(n, u) for n, u in adj.get(start, [])]
        while stack:
            n, din = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if n[0] == 'w':
                if n[1] != wk:
                    edges.add((min(wk, n[1]), max(wk, n[1])))
                continue
            for n2, u2 in adj.get(n, []):
                if _dot(din, u2) >= STRAIGHT_COS:
                    stack.append((n2, u2))
    return edges


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _norm_axis(ax):
    """方向取正：x 为正；x≈0 时 y 为正。"""
    if ax[0] < -1e-9 or (abs(ax[0]) <= 1e-9 and ax[1] < 0):
        return (-ax[0], -ax[1])
    return ax


def _center_ref(w, axis):
    """井在 axis 方向上的中心参照面：该方向正好垂直于参照面法向。找不到返回 None。"""
    e = w['e']
    if abs(_dot(w['hand'], axis)) >= ANG_COS:
        refs = e.GetReferences(RT.CenterLeftRight)
    elif abs(_dot(w['face'], axis)) >= ANG_COS:
        refs = e.GetReferences(RT.CenterFrontBack)
    else:
        return None
    return refs[0] if refs is not None and refs.Count > 0 else None


def edge_axis(wa, wb):
    """这一对井能标注的方向（单位向量，取正）；标不了返回 None。
    优先用 X、Y 轴（连线偏差 5° 内），其次用井自己的轴向；而且两口井在这个方向上都得有中心参照面。"""
    dx, dy = wb['c'][0] - wa['c'][0], wb['c'][1] - wa['c'][1]
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return None
    u = (dx / L, dy / L)
    cands = [(1.0, 0.0), (0.0, 1.0)]
    for w in (wa, wb):
        cands.append(w['hand'])
        cands.append(w['face'])
    for ax in cands:
        if abs(_dot(u, ax)) >= CHORD_COS:
            ax = _norm_axis(ax)
            if _center_ref(wa, ax) is not None and _center_ref(wb, ax) is not None:
                return ax
    return None


def _normals_parallel(wells, ids, ax):
    """这几口井在 ax 方向上用的中心参照面，法向是否严格平行（Revit 要求连续标注的参照面平行，差 0.01° 以上就会建失败/被删）。"""
    ns = []
    for i in ids:
        w = wells[i]
        ns.append(w['hand'] if abs(_dot(w['hand'], ax)) >= ANG_COS else w['face'])
    n0 = ns[0]
    for v in ns[1:]:
        if abs(n0[0] * v[1] - n0[1] * v[0]) > 2e-4:
            return False
    return True


def _akey(ax):
    return (round(ax[0], 3), round(ax[1], 3))


def stable(ref):
    try:
        return ref.ConvertToStableRepresentation(doc)
    except Exception:
        return None


def existing_dims(view, wells):
    """视图里「只参照井」的标注：[{dim, wells[id...], 方向, 问题[]}]。参照里有别的构件（墙、轴线…）的不碰。"""
    out = []
    stable_ok = {}
    for k, w in wells.items():
        s = set()
        for t in (RT.CenterLeftRight, RT.CenterFrontBack):
            for r in w['e'].GetReferences(t):
                s.add(stable(r))
        stable_ok[k] = s
    for dm in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Dimension):
        try:
            refs = list(dm.References)
        except Exception:
            continue
        if len(refs) < 2:
            continue
        ids = [_idv(r.ElementId) for r in refs]
        els = [doc.GetElement(r.ElementId) for r in refs]
        if not all(isinstance(e, DB.FamilyInstance) and wcfg_series_of(_mark(e), WCFG, True) is not None for e in els):
            continue                                     # 有别的构件：不是井间标注
        if not all(i in wells for i in ids):
            continue                                     # 参照的井不在本视图范围内：看不全，不动
        try:
            ln = dm.Curve
            d = _norm_axis((ln.Direction.X, ln.Direction.Y))
        except Exception:
            continue
        problems = []
        if len(set(ids)) < len(ids):
            problems.append(u'同一口井参照了两次')
        if any(stable(r) not in stable_ok[i] for r, i in zip(refs, ids)):
            problems.append(u'参照的不是井中心面')
        try:
            if dm.NumberOfSegments and dm.NumberOfSegments > 0:
                ov = any((dm.Segments.get_Item(i).ValueOverride or u'') for i in range(dm.NumberOfSegments))
            else:
                ov = bool(dm.ValueOverride)
            if ov:
                problems.append(u'标注文字被手动改过')
        except Exception:
            pass
        order = sorted(set(ids), key=lambda i: _dot(wells[i]['c'], d))
        out.append({'dim': dm, 'ids': order, 'axis': d, 'problems': problems})
    return out


def _pair(a, b):
    return (min(a, b), max(a, b))


def make_plan(view, wells, edges, dims, off_ft):
    """规划：保留/删除哪些旧标注，新增哪些。返回 dict。"""
    plan = {'keep': [], 'delete': [], 'create': [], 'skip': [], 'covered': set()}
    plan['covered'].update(oblique_covered(view, wells))
    for D in dims:
        ps = [_pair(D['ids'][i], D['ids'][i + 1]) for i in range(len(D['ids']) - 1)]
        probs = list(D['problems'])
        if any(p not in edges for p in ps):
            probs.append(u'相邻两口井之间没有排管连接')
        if probs:
            plan['delete'].append((D, probs))
        else:
            plan['keep'].append(D)
            plan['covered'].update(ps)
    # 新增：没标过的相邻井对，按方向分组
    by_axis = {}
    for e in sorted(edges):
        if e in plan['covered']:
            continue
        wa, wb = wells[e[0]], wells[e[1]]
        ax = edge_axis(wa, wb)
        if ax is None:
            plan['create'].append({'kind': 'oblique', 'ids': [e[0], e[1]]})      # 斜向：沿两井连线标真实中心距
            continue
        by_axis.setdefault(_akey(ax), (ax, []))[1].append(e)
    for key, (ax, es) in by_axis.items():
        nb = {}
        for a, b in es:
            nb.setdefault(a, []).append(b)
            nb.setdefault(b, []).append(a)
        seen = set()
        # 沿方向连续的井合并成链：每口井最多 2 个邻居、且沿方向单调
        for start in sorted(nb, key=lambda i: _dot(wells[i]['c'], ax)):
            if start in seen:
                continue
            comp, stack = [], [start]
            seen.add(start)
            while stack:
                x = stack.pop()
                comp.append(x)
                for y in nb[x]:
                    if y not in seen:
                        seen.add(y)
                        stack.append(y)
            order = sorted(comp, key=lambda i: _dot(wells[i]['c'], ax))
            chain = all(len(nb[i]) <= 2 for i in comp) and all(_pair(order[i], order[i + 1]) in set(_pair(a, b) for a, b in es) for i in range(len(order) - 1))
            if chain and len(order) >= 2:
                items = [{'ax': ax, 'ids': order}]
            else:
                items = []
                for a in comp:
                    for b in nb[a]:
                        if a < b:
                            items.append({'ax': ax, 'ids': sorted([a, b], key=lambda i: _dot(wells[i]['c'], ax))})
            for it in items:
                if _normals_parallel(wells, it['ids'], ax):
                    plan['create'].append(it)
                else:                       # 井有点转角（0.01° 以上），中心面不严格平行：改成沿连线标，每相邻两口井一条
                    for i in range(len(it['ids']) - 1):
                        plan['create'].append({'kind': 'oblique', 'ids': [it['ids'][i], it['ids'][i + 1]]})
    return plan


class _Fails(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        for m in fa.GetFailureMessages():
            if m.GetSeverity() == DB.FailureSeverity.Warning:
                fa.DeleteWarning(m)
        return DB.FailureProcessingResult.Continue


AUX_NOTE = u'井间距辅助线（自动）'


def _invisible_style():
    try:
        cats = doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Lines).SubCategories
        for c in cats:
            if c.Name in (u'<不可见线>', u'<隐藏>', u'<Invisible lines>', u'<Hidden>'):      # 中文版里「不可见线」叫 <隐藏>；<隐藏线> 是另一种（虚线）
                return c.GetGraphicsStyle(DB.GraphicsStyleType.Projection)
    except Exception:
        pass
    return None


def oblique_covered(view, wells):
    """视图里已经做过的斜向标注（辅助线两端各落在一口井中心上）覆盖的井对。"""
    out = set()
    inv = _invisible_style()
    for dc in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.CurveElement):
        try:
            if not isinstance(dc, DB.DetailCurve):
                continue
            if inv is not None and dc.LineStyle is not None and dc.LineStyle.Id != inv.Id:
                continue                                     # 辅助线用的是「不可见线」样式；别的详图线不碰
            c = dc.GeometryCurve
            ends = []
            for k in (0, 1):
                q = c.GetEndPoint(k)
                best = None
                for i, w in wells.items():
                    dd = math.hypot(q.X - w['c'][0], q.Y - w['c'][1])
                    if dd < 0.2 / 0.3048 and (best is None or dd < best[0]):
                        best = (dd, i)
                ends.append(best[1] if best else None)
            if None not in ends and ends[0] != ends[1]:
                out.add(_pair(ends[0], ends[1]))
        except Exception:
            continue
    return out


def create_oblique(view, wells, item, dtype, off_ft):
    """斜向：在两口井中心之间画一条不可见的辅助详图线，沿它标注（真实的中心到中心距离）。井挪了要重跑本插件更新。"""
    a, b = item['ids']
    ca, cb = wells[a]['c'], wells[b]['c']
    z = _view_z(view, wells, item['ids'])
    p0, p1 = DB.XYZ(ca[0], ca[1], z), DB.XYZ(cb[0], cb[1], z)
    try:
        dc = doc.Create.NewDetailCurve(view, DB.Line.CreateBound(p0, p1))
    except Exception as ex:
        return None, (u'%s' % ex).splitlines()[0][:80]
    st = _invisible_style()
    if st is not None:
        try:
            dc.LineStyle = st
        except Exception:
            pass
    try:
        dc.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS).Set(AUX_NOTE)
    except Exception:
        pass
    ln = dc.GeometryCurve
    arr = DB.ReferenceArray()
    arr.Append(ln.GetEndPointReference(0))
    arr.Append(ln.GetEndPointReference(1))
    L = math.hypot(cb[0] - ca[0], cb[1] - ca[1])
    u = ((cb[0] - ca[0]) / L, (cb[1] - ca[1]) / L)
    perp = (-u[1], u[0])
    q0 = DB.XYZ(p0.X + perp[0] * off_ft, p0.Y + perp[1] * off_ft, z)
    q1 = DB.XYZ(p1.X + perp[0] * off_ft, p1.Y + perp[1] * off_ft, z)
    try:
        dm = doc.Create.NewDimension(view, DB.Line.CreateBound(q0, q1), arr, dtype) if dtype is not None else doc.Create.NewDimension(view, DB.Line.CreateBound(q0, q1), arr)
    except Exception as ex:
        doc.Delete(dc.Id)
        return None, (u'%s' % ex).splitlines()[0][:80]
    return dm, None


def _view_z(view, wells, ids):
    try:
        return view.GenLevel.ProjectElevation
    except Exception:
        return wells[ids[0]]['c'][2]


def create_dim(view, wells, item, dtype, off_ft):
    """按一条规划建标注。返回 (标注, 错误说明)。"""
    if item.get('kind') == 'oblique':
        return create_oblique(view, wells, item, dtype, off_ft)
    ax, ids = item['ax'], item['ids']
    perp = (-ax[1], ax[0])
    s = [_dot(wells[i]['c'], ax) for i in ids]
    t = max(_dot(wells[i]['c'], perp) for i in ids) + off_ft
    z = _view_z(view, wells, ids)
    p0 = DB.XYZ(ax[0] * min(s) + perp[0] * t, ax[1] * min(s) + perp[1] * t, z)
    p1 = DB.XYZ(ax[0] * max(s) + perp[0] * t, ax[1] * max(s) + perp[1] * t, z)
    arr = DB.ReferenceArray()
    for i in ids:
        arr.Append(_center_ref(wells[i], ax))
    try:
        dm = doc.Create.NewDimension(view, DB.Line.CreateBound(p0, p1), arr, dtype) if dtype is not None else doc.Create.NewDimension(view, DB.Line.CreateBound(p0, p1), arr)
    except Exception as ex:
        return None, (u'%s' % ex).splitlines()[0][:80]
    return dm, None


def _dim_ok(dm, item, wells):
    """标注是否有效、数值是否等于井中心距（轴向：中心在标注方向上的距离；斜向：连线真实距离）。"""
    try:
        if not dm.IsValidObject:
            return False
        if item.get('kind') == 'oblique':
            ca, cb = wells[item['ids'][0]]['c'], wells[item['ids'][1]]['c']
            return abs(dm.Value - math.hypot(cb[0] - ca[0], cb[1] - ca[1])) <= 0.002
        s = [_dot(wells[i]['c'], item['ax']) for i in item['ids']]
        want = sorted(abs(s[i + 1] - s[i]) for i in range(len(s) - 1))
        if dm.NumberOfSegments and dm.NumberOfSegments > 0:
            got = sorted(dm.Segments.get_Item(i).Value for i in range(dm.NumberOfSegments))
        else:
            got = [dm.Value]
        return len(got) == len(want) and all(abs(a - b) <= 0.002 for a, b in zip(want, got))
    except Exception:
        return False


def apply_plan(view, wells, plan, dtype, off_ft, do_delete, do_create):
    """一个事务：先删不对的旧标注，再新建。返回 (删了几个, 新建几条, 失败 [(井号们, 原因)], 数值校验不符数)。"""
    t = DB.Transaction(doc, u'井间距自动标注')
    fho = t.GetFailureHandlingOptions()
    fho.SetFailuresPreprocessor(_Fails())
    t.SetFailureHandlingOptions(fho)
    t.Start()
    try:
        ndel = 0
        if do_delete and plan['delete']:
            ok, blocked = wg_split(doc, [D['dim'] for D, _ in plan['delete']])
            for e in ok:
                doc.Delete(e.Id)
                ndel += 1
        made, fails, badval = 0, [], 0
        made_dims = []
        if do_create:
            for item in plan['create']:
                dm, err = create_dim(view, wells, item, dtype, off_ft)
                names = u'-'.join(wells[i]['mark'] for i in item['ids'])
                if dm is None:
                    fails.append((names, err))
                    continue
                made_dims.append((dm, item))
            doc.Regenerate()
            retry = []
            for dm, item in made_dims:
                if _dim_ok(dm, item, wells):
                    made += 1
                    continue
                try:                                   # 建出来的数值不对或已失效：删掉，改成沿连线标
                    if dm.IsValidObject:
                        doc.Delete(dm.Id)
                except Exception:
                    pass
                if item.get('kind') == 'oblique':
                    fails.append((u'-'.join(wells[i]['mark'] for i in item['ids']), u'标注数值不对'))
                else:
                    for i in range(len(item['ids']) - 1):
                        retry.append({'kind': 'oblique', 'ids': [item['ids'][i], item['ids'][i + 1]]})
            retry_dims = []
            for item in retry:
                dm, err = create_dim(view, wells, item, dtype, off_ft)
                if dm is None:
                    fails.append((u'-'.join(wells[i]['mark'] for i in item['ids']), err))
                else:
                    retry_dims.append((dm, item))
            if retry_dims:
                doc.Regenerate()
            for dm, item in retry_dims:
                if _dim_ok(dm, item, wells):
                    made += 1
                else:
                    badval += 1
                    fails.append((u'-'.join(wells[i]['mark'] for i in item['ids']), u'标注数值对不上，已保留请检查'))
        st = t.Commit()
        if st != DB.TransactionStatus.Committed:
            raise Exception(u'事务没有提交成功（%s），什么都没改' % st)
        return ndel, made, fails, badval
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise


def _dim_types():
    out = []
    for dt in DB.FilteredElementCollector(doc).OfClass(DB.DimensionType):
        try:
            if dt.StyleType == DB.DimensionStyleType.Linear:
                out.append(dt)
        except Exception:
            pass
    return out


def _default_type(dims, types):
    cnt = {}
    for D in dims:
        k = _idv(D['dim'].GetTypeId())
        cnt[k] = cnt.get(k, 0) + 1
    if cnt:
        best = max(cnt, key=lambda k: cnt[k])
        for t in types:
            if _idv(t.Id) == best:
                return t
    for t in types:
        if u'标注' in DB.Element.Name.GetValue(t):
            return t
    return types[0] if types else None


def _names(wells, ids):
    return u' — '.join(wells[i]['mark'] for i in ids)


def show_dialog(view, wells, plan, types, dtype0, edges):
    """预览窗口：列出要删/要新增/没法标的，选标注类型和偏移，确认。返回 dict 或 None。"""
    f = WF.Form()
    f.Text = TITLE
    f.TopMost = True
    f.StartPosition = WF.FormStartPosition.CenterScreen
    f.ClientSize = SD.Size(860, 600)
    f.MinimumSize = SD.Size(860, 600)
    f.Font = SD.Font(u'Microsoft YaHei UI', 9.0)
    A = WF.AnchorStyles
    head = WF.Label()
    nch = sum(1 for c in plan['create'] if len(c['ids']) > 2)
    nob = sum(1 for c in plan['create'] if c.get('kind') == 'oblique')
    head.Text = (u'视图「%s」：井 %d 口，排管路由上相邻的井 %d 对。已有正确标注 %d 条（覆盖 %d 对，不重复标）；不对的旧标注 %d 条；'
                 u'新增标注 %d 条（其中连续标注 %d 条、斜向 %d 条）。'
                 % (view.Name, len(wells), len(edges), len(plan['keep']), len(plan['covered']), len(plan['delete']),
                    len(plan['create']), nch, nob))
    head.Location = SD.Point(12, 8)
    head.Size = SD.Size(836, 54)
    head.Anchor = A.Top | A.Left | A.Right
    f.Controls.Add(head)
    lv = WF.ListView()
    lv.View = WF.View.Details
    lv.FullRowSelect = True
    lv.GridLines = True
    lv.CheckBoxes = True
    lv.Location = SD.Point(12, 66)
    lv.Size = SD.Size(836, 360)
    lv.Anchor = A.Top | A.Left | A.Right | A.Bottom
    lv.Columns.Add(u'动作', 70)
    lv.Columns.Add(u'井（沿标注方向）', 330)
    lv.Columns.Add(u'说明', 420)
    for D, probs in plan['delete']:
        it = WF.ListViewItem(u'删除')
        it.Checked = True
        it.Tag = D
        it.SubItems.Add(_names(wells, D['ids']))
        it.SubItems.Add(u'；'.join(probs))
        lv.Items.Add(it)
    for c in plan['create']:
        if c.get('kind') == 'oblique':
            ca, cb = wells[c['ids'][0]]['c'], wells[c['ids'][1]]['c']
            it = WF.ListViewItem(u'新增')
            it.Checked = True
            it.Tag = c
            it.SubItems.Add(_names(wells, c['ids']))
            it.SubItems.Add(u'斜向，沿两井连线标真实中心距 %.2f m（带一条不可见辅助线）' % (math.hypot(cb[0] - ca[0], cb[1] - ca[1]) * FT / 1000.0))
            lv.Items.Add(it)
            continue
        s = [_dot(wells[i]['c'], c['ax']) for i in c['ids']]
        segs = u' + '.join(u'%.2f' % (abs(s[i + 1] - s[i]) * FT / 1000.0) for i in range(len(s) - 1))
        it = WF.ListViewItem(u'新增')
        it.Checked = True
        it.Tag = c
        it.SubItems.Add(_names(wells, c['ids']))
        it.SubItems.Add(u'中心距 %s m（%s）' % (segs, u'连续标注' if len(c['ids']) > 2 else u'单段'))
        lv.Items.Add(it)
    for e, why in plan['skip']:
        it = WF.ListViewItem(u'跳过')
        it.Tag = None
        it.SubItems.Add(_names(wells, list(e)))
        it.SubItems.Add(why)
        lv.Items.Add(it)
    f.Controls.Add(lv)

    def _lb(x, y, w, txt):
        l = WF.Label()
        l.Text = txt
        l.Location = SD.Point(x, y)
        l.Size = SD.Size(w, 20)
        l.Anchor = A.Bottom | A.Left
        f.Controls.Add(l)
    cdel = WF.CheckBox()
    cdel.Text = u'删除不对的旧标注（上面勾上的）'
    cdel.Location = SD.Point(12, 436)
    cdel.Size = SD.Size(300, 22)
    cdel.Checked = bool(plan['delete'])
    cdel.Enabled = bool(plan['delete'])
    cdel.Anchor = A.Bottom | A.Left
    f.Controls.Add(cdel)
    ccre = WF.CheckBox()
    ccre.Text = u'新增标注（上面勾上的）'
    ccre.Location = SD.Point(330, 436)
    ccre.Size = SD.Size(260, 22)
    ccre.Checked = bool(plan['create'])
    ccre.Enabled = bool(plan['create'])
    ccre.Anchor = A.Bottom | A.Left
    f.Controls.Add(ccre)
    _lb(12, 474, 90, u'标注类型')
    cb = WF.ComboBox()
    cb.DropDownStyle = WF.ComboBoxStyle.DropDownList
    cb.Location = SD.Point(104, 470)
    cb.Size = SD.Size(330, 24)
    cb.Anchor = A.Bottom | A.Left
    for t in types:
        cb.Items.Add(DB.Element.Name.GetValue(t))
    if dtype0 is not None:
        cb.SelectedItem = DB.Element.Name.GetValue(dtype0)
    f.Controls.Add(cb)
    _lb(460, 474, 190, u'标注线离井距离（图纸 mm）')
    tbo = WF.TextBox()
    tbo.Location = SD.Point(656, 470)
    tbo.Size = SD.Size(60, 24)
    tbo.Text = u'10'
    tbo.Anchor = A.Bottom | A.Left
    f.Controls.Add(tbo)
    note = WF.Label()
    note.Text = u'连续标注：同一直线上相邻的井合成一条，参照井族的「中心（左/右）」「中心（前/后）」面。斜向的沿两井连线标真实中心距（用一条不可见的辅助线，井挪了要重跑本插件）。标得不理想的可以在视图里拖动标注线。'
    note.Location = SD.Point(12, 506)
    note.Size = SD.Size(836, 40)
    note.Anchor = A.Bottom | A.Left | A.Right
    f.Controls.Add(note)
    res = {}
    ok = WF.Button()
    ok.Text = u'执行'
    ok.Size = SD.Size(110, 30)
    ok.Location = SD.Point(628, 556)
    ok.Anchor = A.Bottom | A.Right
    cc = WF.Button()
    cc.Text = u'取消'
    cc.Size = SD.Size(100, 30)
    cc.Location = SD.Point(748, 556)
    cc.Anchor = A.Bottom | A.Right
    cc.DialogResult = WF.DialogResult.Cancel
    f.Controls.Add(ok)
    f.Controls.Add(cc)
    f.CancelButton = cc
    f.AcceptButton = ok

    def _ok(s_, a_):
        try:
            mm = float(tbo.Text.strip())
        except Exception:
            WF.MessageBox.Show(u'偏移请输入数字（图纸上的毫米数，如 10）。', TITLE)
            return
        sel = None
        for t in types:
            if DB.Element.Name.GetValue(t) == cb.SelectedItem:
                sel = t
        res.update({'delete': cdel.Checked, 'create': ccre.Checked, 'dtype': sel, 'off': mm,
                    'del_dims': [it.Tag for it in lv.Items if it.Text == u'删除' and it.Checked],
                    'new_items': [it.Tag for it in lv.Items if it.Text == u'新增' and it.Checked]})
        f.DialogResult = WF.DialogResult.OK
        f.Close()
    ok.Click += _ok
    ff_fit(f)
    if f.ShowDialog() != WF.DialogResult.OK:
        return None
    return res



# ============================================================
# 排管横向中心距标注：相邻平行排管的中心线之间，横向间距
# ============================================================
BANK_SET = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), u'KaifaTools', u'井间距标注设置.json')
BANK_DEFAULTS = {
    u'说明': u'bank_gap_max_m：相邻两排管中心线最大间距（米），更远的不标；bank_overlap_min_m：两排管并行重叠至少多长（米）才标；'
           u'bank_pos：标注线放在重叠段的哪个位置（0~1，0.5=正中间）；bank_min_gap_mm：中心距小于这个值（几乎重合）不标。',
    u'bank_gap_max_m': 3.0,
    u'bank_overlap_min_m': 1.0,
    u'bank_pos': 0.5,
    u'bank_min_gap_mm': 50.0,
}


def bank_cfg():
    s = dict(BANK_DEFAULTS)
    try:
        if os.path.exists(BANK_SET):
            with io.open(BANK_SET, 'r', encoding='utf-8-sig') as f:
                s.update(json.load(f))
    except Exception:
        pass
    return s


def bank_open_settings():
    cur = bank_cfg()
    fields = [
        {'key': 'bank_gap_max_m', 'label': u'相邻排管最大中心距 (m)', 'kind': 'num', 'min': 0.1, 'max': 50,
         'hint': u'两排管中心线相距超过这个值就不标。'},
        {'key': 'bank_overlap_min_m', 'label': u'并行重叠至少 (m)', 'kind': 'num', 'min': 0, 'max': 100,
         'hint': u'两排管并排走的长度不到这个值，不标。'},
        {'key': 'bank_pos', 'label': u'标注线位置 (0~1)', 'kind': 'num', 'min': 0, 'max': 1,
         'hint': u'放在并行重叠段的哪个位置：0 起点、0.5 正中、1 终点。'},
        {'key': 'bank_min_gap_mm', 'label': u'最小中心距 (mm)', 'kind': 'num', 'min': 0, 'max': 1000,
         'hint': u'中心距小于这个值（几乎重合）的不标。'},
    ]
    res = su_fields_dialog(TITLE + u' · 排管横向标注设置', u'改完点保存，下次运行生效。设置文件：' + BANK_SET, fields, cur, BANK_DEFAULTS,
                           extra_buttons=[(u'用记事本打开文件', lambda: su_open_file(BANK_SET))], width=680, height=420)
    if res is None:
        return False
    out = dict(BANK_DEFAULTS)
    out.update(cur)
    out.update(res)
    dd = os.path.dirname(BANK_SET)
    if not os.path.isdir(dd):
        os.makedirs(dd)
    with io.open(BANK_SET, 'w', encoding='utf-8') as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    return True


def view_banks(view):
    """当前视图里的直排管：[{e, id, a, b, t(单位方向), ref(中心线参照)}]"""
    out = []
    col = DB.FilteredElementCollector(doc, view.Id).OfCategory(DB.BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType()
    for e in col:
        if not _is_bank(e) or not isinstance(e.Location, DB.LocationCurve):
            continue
        c = e.Location.Curve
        if not isinstance(c, DB.Line):
            continue
        a, b = c.GetEndPoint(0), c.GetEndPoint(1)
        dx, dy = b.X - a.X, b.Y - a.Y
        L_ = math.hypot(dx, dy)
        if L_ < 1e-6:
            continue
        refs = e.GetReferences(RT.CenterFrontBack)
        if refs is None or refs.Count == 0:
            continue
        out.append({'e': e, 'id': _idv(e.Id), 'a': a, 'b': b, 't': (dx / L_, dy / L_), 'ref': refs[0]})
    return out


def bank_plan(view, banks, cfg):
    """相邻平行排管 → 标注链。每条链：{'banks':[按横向顺序], 'ids', 'gaps'(mm), 'p0', 'p1'}。"""
    gmax = float(cfg['bank_gap_max_m']) * 1000.0 / 304.8
    gmin = float(cfg['bank_min_gap_mm']) / 304.8
    omin = float(cfg['bank_overlap_min_m']) * 1000.0 / 304.8
    frac = min(1.0, max(0.0, float(cfg['bank_pos'])))
    groups = []        # 严格平行（叉积 < 2e-4）：Revit 要求连续标注的参照面严格平行
    for b in banks:
        for g in groups:
            r = g[0]
            if abs(r['t'][0] * b['t'][1] - r['t'][1] * b['t'][0]) <= 2e-4:
                g.append(b)
                break
        else:
            groups.append([b])
    chains = []
    try:
        z0 = view.GenLevel.ProjectElevation
    except Exception:
        z0 = None
    for g in groups:
        if len(g) < 2:
            continue
        ax = g[0]['t']
        if ax[0] < -1e-9 or (abs(ax[0]) <= 1e-9 and ax[1] < 0):
            ax = (-ax[0], -ax[1])
        nrm = (-ax[1], ax[0])
        for b in g:
            sa, sb = _dot((b['a'].X, b['a'].Y), ax), _dot((b['b'].X, b['b'].Y), ax)
            b['s0'], b['s1'] = min(sa, sb), max(sa, sb)
            b['off'] = _dot((b['a'].X, b['a'].Y), nrm)
        g.sort(key=lambda b: (b['off'], b['s0']))
        used = set()
        for si in range(len(g)):
            if si in used:
                continue
            chain = [g[si]]
            lo, hi = g[si]['s0'], g[si]['s1']
            used.add(si)
            for k in range(si + 1, len(g)):
                if k in used:
                    continue
                nb = g[k]
                gap = nb['off'] - chain[-1]['off']
                if gap > gmax:
                    break
                if gap < gmin:
                    continue
                nlo, nhi = max(lo, nb['s0']), min(hi, nb['s1'])
                if nhi - nlo < omin:
                    continue
                chain.append(nb)
                lo, hi = nlo, nhi
                used.add(k)
            if len(chain) >= 2:
                s_at = lo + (hi - lo) * frac
                z = z0 if z0 is not None else chain[0]['a'].Z
                p0 = DB.XYZ(ax[0] * s_at + nrm[0] * chain[0]['off'], ax[1] * s_at + nrm[1] * chain[0]['off'], z)
                p1 = DB.XYZ(ax[0] * s_at + nrm[0] * chain[-1]['off'], ax[1] * s_at + nrm[1] * chain[-1]['off'], z)
                chains.append({'banks': chain, 'ids': [b['id'] for b in chain],
                               'gaps': [(chain[j + 1]['off'] - chain[j]['off']) * 304.8 for j in range(len(chain) - 1)], 'p0': p0, 'p1': p1})
    return chains


def bank_existing(view, bank_ids):
    """视图里已经只参照排管的标注：[set(排管id...)]"""
    out = []
    for dm in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Dimension):
        try:
            ids = set()
            ok = True
            for r in dm.References:
                eid = _idv(r.ElementId)
                if eid in bank_ids:
                    ids.add(eid)
                else:
                    ok = False
                    break
            if ok and len(ids) >= 2:
                out.append(ids)
        except Exception:
            continue
    return out


def bank_main():
    view = uidoc.ActiveView
    if not isinstance(view, DB.ViewPlan):
        forms.alert(u'请在平面视图里运行（当前是「%s」）。' % view.Name, title=TITLE)
        return
    cfg = bank_cfg()
    banks = view_banks(view)
    chains = bank_plan(view, banks, cfg)
    if not chains:
        forms.alert(u'当前视图里 %d 根直排管，没有找到相邻、严格平行、并行重叠 ≥ %g m、中心距 ≤ %g m 的排管。\n条件可以在「排管横向标注设置…」里改。' % (
            len(banks), cfg['bank_overlap_min_m'], cfg['bank_gap_max_m']), title=TITLE)
        return
    have = bank_existing(view, set(b['id'] for b in banks))
    todo = []
    covered = 0
    for c in chains:
        pairs = [(c['ids'][j], c['ids'][j + 1]) for j in range(len(c['ids']) - 1)]
        if all(any(a in h and b in h for h in have) for a, b in pairs):
            covered += 1
        else:
            todo.append(c)
    if not todo:
        forms.alert(u'没有要做的：%d 组相邻排管已经都有横向中心距标注了。' % covered, title=TITLE)
        return
    lines = [u'  %d 根：%s mm' % (len(c['ids']), u' + '.join(u'%.0f' % abs(g) for g in c['gaps'])) for c in todo[:10]]
    msg = u'当前视图 %d 根直排管，找到 %d 组相邻平行排管需要标横向中心距（另有 %d 组已标好）：\n%s%s\n\n每组一条连续标注，标注线放在并行段的 %.0f%% 位置，参照排管自带的中心线。整次可 Ctrl+Z 撤销。\n\n现在标注？' % (
        len(banks), len(todo), covered, u'\n'.join(lines), u'\n  …' if len(todo) > 10 else u'', float(cfg['bank_pos']) * 100)
    if not forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
        return
    types = _dim_types()
    dtype = _default_type([], types)
    t = DB.Transaction(doc, u'天河落 · 排管横向中心距标注')
    fh = t.GetFailureHandlingOptions()
    fh.SetFailuresPreprocessor(_Fails())
    t.SetFailureHandlingOptions(fh)
    t.Start()
    made, bad, fails = 0, 0, []
    try:
        for c in todo:
            arr = DB.ReferenceArray()
            for b in c['banks']:
                arr.Append(b['ref'])
            try:
                ln = DB.Line.CreateBound(c['p0'], c['p1'])
                dm = doc.Create.NewDimension(view, ln, arr, dtype) if dtype is not None else doc.Create.NewDimension(view, ln, arr)
            except Exception as ex:
                fails.append((u'、'.join(str(i) for i in c['ids']), (u'%s' % ex).splitlines()[0][:60]))
                continue
            # 核对数值：每段应约等于排管中心线横向间距（位置线和几何中心线可能差十几毫米，容差 60 mm）
            try:
                if dm.NumberOfSegments and dm.NumberOfSegments > 0:
                    got = sorted(abs(dm.Segments.get_Item(i).Value) * 304.8 for i in range(dm.NumberOfSegments))
                else:
                    got = [abs(dm.Value) * 304.8]
                want = sorted(abs(g) for g in c['gaps'])
                if len(got) != len(want) or any(abs(a - b) > 60 for a, b in zip(got, want)):
                    bad += 1
            except Exception:
                bad += 1
            made += 1
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    msg = u'完成：新增 %d 条排管横向中心距标注（Ctrl+Z 可整次撤销）。' % made
    if bad:
        msg += u'\n⚠ 有 %d 条的数值和排管中心线间距对不上（差 >60 mm），请抽查。' % bad
    if fails:
        msg += u'\n没建成 %d 条：\n' % len(fails) + u'\n'.join(u'  排管 %s：%s' % f for f in fails[:8])
    forms.alert(msg, title=TITLE)


def main():
    pick = forms.CommandSwitchWindow.show([u'井间距标注（相邻井中心到中心）', u'排管横向中心距标注（相邻排管中心线之间）', u'⚙ 排管横向标注设置…'], message=TITLE)
    if not pick:
        return
    try:
        if pick.startswith(u'⚙'):
            if bank_open_settings():
                forms.alert(u'设置已保存，下次运行排管横向标注时生效。', title=TITLE)
        elif pick.startswith(u'排管'):
            bank_main()
        else:
            well_main()
    except Exception:
        forms.alert(u'出错了（已回滚，模型没被改）：\n' + traceback.format_exc(), title=TITLE)


def well_main():
    try:
        view = uidoc.ActiveView
        if not isinstance(view, DB.ViewPlan):
            forms.alert(u'请在平面视图里运行（当前是「%s」）。' % view.Name, title=TITLE)
            return
        pb = forms.ProgressBar(title=u'正在分析排管路由…', cancellable=False)
        try:
            wells = collect_wells(view)
            if len(wells) < 2:
                forms.alert(u'当前视图里没有足够的井（至少 2 口，标记属于井编号系列的常规模型）。', title=TITLE)
                return
            banks = collect_banks()
            edges = build_edges(wells, banks)
            dims = existing_dims(view, wells)
            scale = max(int(view.Scale), 1)
            plan = make_plan(view, wells, edges, dims, 0)
        finally:
            pb.close()
        if not (plan['delete'] or plan['create'] or plan['skip']):
            forms.alert(u'没有要做的：视图里相邻的井都已经有正确的中心距标注了（%d 对）。' % len(plan['covered']), title=TITLE)
            return
        types = _dim_types()
        dtype0 = _default_type(dims, types)
        ans = show_dialog(view, wells, plan, types, dtype0, edges)
        if ans is None:
            return
        off_ft = ans['off'] * scale / FT          # 图纸 mm × 比例 = 模型 mm → 英尺
        plan['delete'] = [(D, []) for D in ans['del_dims']]
        plan['create'] = ans['new_items']
        ndel, made, fails, badval = apply_plan(view, wells, plan, ans['dtype'], off_ft, ans['delete'], ans['create'])
        msg = u'完成：删除旧标注 %d 条，新增标注 %d 条（Ctrl+Z 可整次撤销）。' % (ndel, made)
        if badval:
            msg += u'\n⚠ 有 %d 条标注的数值和井中心距对不上，请抽查。' % badval
        if fails:
            msg += u'\n没建成 %d 条：\n' % len(fails) + u'\n'.join(u'  %s：%s' % (n_, w_) for n_, w_ in fails[:10])
        forms.alert(msg, title=TITLE)
    except Exception:
        forms.alert(u'出错了（已回滚，模型没被改）：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
