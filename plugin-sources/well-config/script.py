# -*- coding: utf-8 -*-
__title__ = '井大样项目配置'
__doc__ = ('井大样系列插件（一键井展开图、一键井大样标注、图纸归类与图框编号、导出井大样 DWG 等）共用的项目配置窗口：'
           '井大样图纸名前缀、图号前缀、井号系列（前缀 → 系统名 → 系统代码）、按井号前缀的图名模板与图纸集合、'
           'DWG 导出设置名与图框 logo 位置。换项目时先在这里改好，再用其他井大样插件。'
           '配置保存在 文档\KaifaTools\井大样配置.json，可随时恢复默认。不改模型。')

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

# ---- well_config_ui（天河落共用片段，打包时内联进脚本；须放在 well_config.py 与 settings_ui.py 之后）----
# 「井大样项目配置」图形窗口：图纸名前缀、图号前缀、井号系列、图名模板、图纸集合、图框 logo。
# wcfg_edit() 打开窗口（保存后立即生效）；wcfg_first_run(插件名) 在这台电脑第一次用井大样插件时弹出，让用户先确认配置。


def _wcfg_map_to_lines(d):
    return [u"%s = %s" % (k, v) for k, v in sorted((d or {}).items(), key=lambda t: (-len(t[0]), t[0]))]


def _wcfg_lines_to_map(lines, name):
    out = {}
    for ln in lines:
        if u"=" not in ln:
            raise ValueError(u"「%s」这一行缺少「=」：%s（格式：前缀 = 内容）" % (name, ln))
        k, v = ln.split(u"=", 1)
        k = k.strip(); v = v.strip()
        if not k or not v:
            raise ValueError(u"「%s」这一行不完整：%s" % (name, ln))
        out[k] = v
    return out


def _wcfg_series_to_lines(series):
    return [u"%s | %s | %s | %s" % (s.get(u"prefix", u""), s.get(u"system", u""), s.get(u"code", u""), s.get(u"label", u"")) for s in series or []]


def _wcfg_lines_to_series(lines):
    out = []
    for ln in lines:
        parts = [x.strip() for x in ln.replace(u"｜", u"|").split(u"|")]
        if len(parts) < 3 or not parts[0]:
            raise ValueError(u"井号系列这一行格式不对：%s\n格式：前缀 | 系统名 | 系统代码 | 菜单名（菜单名可省略）" % ln)
        out.append({u"prefix": parts[0], u"system": parts[1] or parts[0], u"code": parts[2] or parts[0],
                    u"label": (parts[3] if len(parts) > 3 and parts[3] else parts[0] + u"-")})
    if not out:
        raise ValueError(u"井号系列至少要有一行。")
    return out


def _wcfg_fields():
    return [
        {u"kind": u"header", u"label": u"图纸命名"},
        {u"key": u"sheet_name_prefix", u"label": u"井大样图纸名前缀", u"kind": u"text", u"required": True,
         u"hint": u"插件按这个前缀认出哪些图纸是井大样，如「井大样_」。"},
        {u"key": u"sheet_number_prefix", u"label": u"图号前缀", u"kind": u"text", u"hint": u"新建图纸的图号前缀，如 AG-。"},
        {u"key": u"indoor_prefix", u"label": u"室内井井号前缀", u"kind": u"text", u"hint": u"如「室内-」，井号形如 室内-A-01。"},
        {u"key": u"system_param", u"label": u"图纸系统参数名", u"kind": u"text", u"hint": u"归类时写入图纸的参数名，用于浏览器按系统分组。"},
        {u"key": u"sheet_name_templates", u"label": u"图名模板（按井号前缀）", u"kind": u"lines", u"rows": 7,
         u"hint": u"每行一条：前缀 = 模板，{井号} 会换成井号。没写模板的井沿用上面的图纸名前缀。"},
        {u"key": u"sheet_collection_by_prefix", u"label": u"图纸集合（按井号前缀）", u"kind": u"lines", u"rows": 7,
         u"hint": u"每行一条：前缀 = 图纸集合名（要与项目里的图纸集合同名），新图放进对应集合，便于按集合导出 PDF。"},
        {u"kind": u"header", u"label": u"井号系列"},
        {u"key": u"series", u"label": u"井号系列", u"kind": u"lines", u"rows": 12,
         u"hint": u"每行一个系列：前缀 | 系统名 | 系统代码 | 菜单名。井号形如 AA-01、T-068；匹配时长前缀优先（AA 先于 A）。顺序就是菜单和排序的顺序。"},
        {u"key": u"other_system", u"label": u"其他井的系统名", u"kind": u"text", u"hint": u"井号不属于以上任何系列时用。"},
        {u"key": u"other_code", u"label": u"其他井的系统代码", u"kind": u"text"},
        {u"kind": u"header", u"label": u"导出 DWG"},
        {u"key": u"dwg_export_setup", u"label": u"DWG 导出设置名", u"kind": u"text", u"hint": u"项目里「导出设置」的名称，找不到时用默认设置。"},
        {u"key": u"logo_enabled", u"label": u"补插图框 logo", u"kind": u"bool", u"bool_text": u"导出 DWG 后在图框里补插光栅 logo"},
        {u"key": u"logo_file", u"label": u"logo 图片文件名", u"kind": u"text", u"hint": u"放在 文档\\KaifaTools 里。"},
        {u"key": u"logo_x0", u"label": u"logo 左下角 X", u"kind": u"num"},
        {u"key": u"logo_y0", u"label": u"logo 左下角 Y", u"kind": u"num"},
        {u"key": u"logo_w", u"label": u"logo 宽度", u"kind": u"num", u"min": 1, u"hint": u"Layout1 里的图纸单位，按 A1 横向图框标定；换图框要改。"},
    ]


def _wcfg_to_flat(cfg):
    logo = cfg.get(u"logo") or {}
    other = cfg.get(u"other") or {}
    return {
        u"sheet_name_prefix": cfg.get(u"sheet_name_prefix", u""),
        u"sheet_number_prefix": cfg.get(u"sheet_number_prefix", u""),
        u"indoor_prefix": cfg.get(u"indoor_prefix", u""),
        u"system_param": cfg.get(u"system_param", u""),
        u"sheet_name_templates": _wcfg_map_to_lines(cfg.get(u"sheet_name_templates")),
        u"sheet_collection_by_prefix": _wcfg_map_to_lines(cfg.get(u"sheet_collection_by_prefix")),
        u"series": _wcfg_series_to_lines(cfg.get(u"series")),
        u"other_system": other.get(u"system", u""),
        u"other_code": other.get(u"code", u""),
        u"dwg_export_setup": cfg.get(u"dwg_export_setup") or u"机电导图设置",
        u"logo_enabled": bool(logo.get(u"enabled", True)),
        u"logo_file": logo.get(u"file", u"frame_logo.jpg"),
        u"logo_x0": logo.get(u"x0", 0), u"logo_y0": logo.get(u"y0", 0), u"logo_w": logo.get(u"w", 0),
    }


def wcfg_edit(intro=None, title=u"井大样项目配置"):
    """打开项目配置窗口。保存返回 True（并写回 文档\\KaifaTools\\井大样配置.json），取消返回 False。"""
    cur = wcfg_load()
    vals = _wcfg_to_flat(cur)
    defaults = _wcfg_to_flat(_wcfg_merge(WCFG_DEFAULTS, {}))
    intro = intro or (u"井大样系列插件（一键井展开图、井大样标注、图纸归类、井图号重编、导出井大样 DWG 等）共用这份配置。"
                      u"换项目时改这里；保存后下次点插件生效。")
    while True:
        res = su_fields_dialog(title, intro, _wcfg_fields(), vals, defaults,
                               extra_buttons=[(u"用记事本打开文件", lambda: su_open_file(WCFG_FILE))], width=760, height=700)
        if res is None:
            return False
        try:
            series = _wcfg_lines_to_series(res[u"series"])
            tpl = _wcfg_lines_to_map(res[u"sheet_name_templates"], u"图名模板")
            col = _wcfg_lines_to_map(res[u"sheet_collection_by_prefix"], u"图纸集合")
        except ValueError as ex:
            _SUW.MessageBox.Show(u"%s" % ex, title)
            vals = res
            continue
        break
    # 在原文件内容上改（保留文件里其它键和说明），没有文件就从默认值开始
    data = _wcfg_merge(WCFG_DEFAULTS, {})
    try:
        if _wc_os.path.exists(WCFG_FILE):
            with _wc_io.open(WCFG_FILE, 'r', encoding='utf-8-sig') as f:
                data = _wcfg_merge(data, _wc_json.load(f))
    except Exception:
        pass
    data[u"sheet_name_prefix"] = res[u"sheet_name_prefix"]
    data[u"sheet_number_prefix"] = res[u"sheet_number_prefix"]
    data[u"indoor_prefix"] = res[u"indoor_prefix"]
    data[u"system_param"] = res[u"system_param"]
    data[u"series"] = series
    data[u"sheet_name_templates"] = tpl
    data[u"sheet_collection_by_prefix"] = col
    data[u"other"] = {u"system": res[u"other_system"] or u"其他", u"code": res[u"other_code"] or u"QT"}
    data[u"dwg_export_setup"] = res[u"dwg_export_setup"]
    logo = dict(data.get(u"logo") or {})
    logo.update({u"enabled": res[u"logo_enabled"], u"file": res[u"logo_file"], u"x0": res[u"logo_x0"],
                 u"y0": res[u"logo_y0"], u"w": res[u"logo_w"]})
    data[u"logo"] = logo
    d = _wc_os.path.dirname(WCFG_FILE)
    if not _wc_os.path.isdir(d):
        _wc_os.makedirs(d)
    with _wc_io.open(WCFG_FILE, 'w', encoding='utf-8') as f:
        f.write(_wc_json.dumps(data, ensure_ascii=False, indent=2))
    _WCFG_CACHE[0] = None
    return True


def wcfg_first_run(plugin_title):
    """这台电脑第一次用井大样插件时，先请用户确认项目配置。
    返回 True：照常运行（用户点了取消，先用默认规则）；返回 False：配置刚保存，插件应退出、请用户再点一次（脚本开头已按旧配置算好的值要重新读）。"""
    if not WCFG_FIRST_RUN:
        return True
    globals()[u"WCFG_FIRST_RUN"] = False
    intro = (u"第一次使用「%s」：井大样系列插件按这里的规则认图纸、认井号、给新图纸命名。"
             u"下面是默认规则（来自示例项目），请按你的项目改好再保存；点取消则先按默认规则运行。"
             u"以后可以在「井大样项目配置」里随时修改。" % plugin_title)
    if wcfg_edit(intro=intro, title=u"井大样项目配置（第一次使用）"):
        _SUW.MessageBox.Show(u"项目配置已保存。请再点一次「%s」，按新配置运行。" % plugin_title, u"井大样项目配置")
        return False
    return True

def main():
    if wcfg_edit():
        forms.alert(u'井大样项目配置已保存：\n%s\n\n下次点井大样系列插件时按新配置运行。' % WCFG_FILE, title=u'井大样项目配置')


if __name__ == '__main__':
    main()
