# -*- coding: utf-8 -*-
__title__ = '图纸快速预览'
__doc__ = ('把选中的图纸批量渲染成图片，在一个窗口里用左右键 / 上下键 / 鼠标滚轮快速翻页核查；'
           '可给有问题的图纸打标记、写备注、导出问题清单，再一键回到 Revit 打开该图纸去修改。'
           '图片缓存在本机，下次打开不用重新渲染。')

import os
import io
import re
import json
import time
import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
import System
import System.Windows.Forms as WF
import System.Drawing as SD
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms

doc = revit.doc
uidoc = revit.uidoc

BATCH = 12          # 每次导出多少张图纸
CACHE_LRU = 14      # 窗口里最多同时留多少张图在内存


# ------------------------------------------------------------------
# 图纸收集与分组
# ------------------------------------------------------------------

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

def _natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(u"(\\d+)", s or u"")]


def _collection_name(sheet):
    """图纸所属图集（Revit 2025+）；取不到返回空串。"""
    try:
        cid = sheet.SheetCollectionId
        if cid is not None and cid != DB.ElementId.InvalidElementId:
            e = doc.GetElement(cid)
            if e is not None and e.Name:
                return e.Name
    except Exception:
        pass
    return u""


def _sheet_id(sheet):
    try:
        return int(sheet.Id.Value)
    except Exception:
        return int(sheet.Id.IntegerValue)


def collect_sheets():
    out = []
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
        try:
            if s.IsPlaceholder:
                continue
        except Exception:
            pass
        out.append(s)
    out.sort(key=lambda s: _natural_key(s.SheetNumber))
    return out


def pick_scope(sheets):
    """让用户选范围：全部 / 当前选中 / 各个图集。返回图纸列表（已按图号排序）。"""
    groups = {}
    for s in sheets:
        groups.setdefault(_collection_name(s) or u"（未归入图集）", []).append(s)
    selected = []
    try:
        for eid in uidoc.Selection.GetElementIds():
            e = doc.GetElement(eid)
            if isinstance(e, DB.ViewSheet):
                selected.append(e)
    except Exception:
        pass
    options = [u"全部图纸（{} 张）".format(len(sheets))]
    if selected:
        options.append(u"当前选中的图纸（{} 张）".format(len(selected)))
    names = sorted(groups.keys(), key=_natural_key)
    for nm in names:
        options.append(u"图集：{}（{} 张）".format(nm, len(groups[nm])))
    picked = forms.SelectFromList.show(
        options, title=u"图纸快速预览 - 选要预览的范围（可多选）",
        multiselect=True, button_name=u"下一步")
    if not picked:
        return None
    if not isinstance(picked, (list, tuple)):
        picked = [picked]
    chosen = {}
    for p in picked:
        if p.startswith(u"全部"):
            for s in sheets:
                chosen[_sheet_id(s)] = s
        elif p.startswith(u"当前选中"):
            for s in selected:
                chosen[_sheet_id(s)] = s
        else:
            for nm in names:
                if p == u"图集：{}（{} 张）".format(nm, len(groups[nm])):
                    for s in groups[nm]:
                        chosen[_sheet_id(s)] = s
    res = list(chosen.values())
    res.sort(key=lambda s: _natural_key(s.SheetNumber))
    return res


def pick_quality():
    options = [
        u"标准清晰度，只生成还没有的（推荐）",
        u"标准清晰度，全部重新生成（刚改过模型时用）",
        u"快速（图较糊，生成最快），只生成还没有的",
        u"高清（生成较慢），只生成还没有的",
    ]
    c = forms.SelectFromList.show(options, title=u"图纸快速预览 - 清晰度与是否重新生成",
                                  multiselect=False, button_name=u"开始")
    if not c:
        return None
    px = 2200
    force = False
    if c == options[1]:
        force = True
    elif c == options[2]:
        px = 1400
    elif c == options[3]:
        px = 3200
    return px, force


# ------------------------------------------------------------------
# 缓存与渲染
# ------------------------------------------------------------------

def cache_dir_for_doc():
    base = System.Environment.GetFolderPath(System.Environment.SpecialFolder.LocalApplicationData)
    nm = re.sub(u'[\\\\/:*?"<>|]', u"_", doc.Title or u"model")
    d = os.path.join(base, u"THL", u"SheetPreview", nm)
    if not os.path.exists(d):
        os.makedirs(d)
    return d


def _load_json(path, default):
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    try:
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=1))
    except Exception:
        pass


def img_path(cdir, sheet):
    return os.path.join(cdir, u"{}.png".format(_sheet_id(sheet)))


def render_missing(sheets, cdir, px, force):
    """批量把图纸导出成 PNG 放进缓存。返回 (成功数, 失败数, 是否被取消)。"""
    meta_path = os.path.join(cdir, u"meta.json")
    meta = _load_json(meta_path, {})
    todo = []
    for s in sheets:
        key = u"{}|{}|{}".format(s.SheetNumber, s.Name, px)
        if force or (not os.path.exists(img_path(cdir, s))) or meta.get(str(_sheet_id(s))) != key:
            todo.append(s)
    if not todo:
        return 0, 0, False
    tmp = os.path.join(cdir, u"_tmp")
    if not os.path.exists(tmp):
        os.makedirs(tmp)
    ok = 0
    bad = 0
    cancelled = False
    total = len(todo)
    done = 0
    with forms.ProgressBar(title=u"正在渲染图纸图片… ({value}/{max_value})", cancellable=True) as pb:
        for i in range(0, total, BATCH):
            if pb.cancelled:
                cancelled = True
                break
            batch = todo[i:i + BATCH]
            for fn in os.listdir(tmp):
                try:
                    os.remove(os.path.join(tmp, fn))
                except Exception:
                    pass
            ids = List[DB.ElementId]()
            for s in batch:
                ids.Add(s.Id)
            o = DB.ImageExportOptions()
            o.ExportRange = DB.ExportRange.SetOfViews
            o.SetViewsAndSheets(ids)
            o.FilePath = os.path.join(tmp, u"pv")
            o.ZoomType = DB.ZoomFitType.FitToPage
            o.PixelSize = int(px)
            o.ImageResolution = DB.ImageResolution.DPI_150
            o.HLRandWFViewsFileType = DB.ImageFileType.PNG
            o.ShadowViewsFileType = DB.ImageFileType.PNG
            o.FitDirection = DB.FitDirectionType.Horizontal
            try:
                doc.ExportImage(o)
            except Exception:
                bad += len(batch)
                done += len(batch)
                pb.update_progress(done, total)
                continue
            by_num = {}
            for s in batch:
                by_num[s.SheetNumber] = s
            for fn in os.listdir(tmp):
                if not fn.lower().endswith(u".png"):
                    continue
                parts = fn[:-4].split(u" - ")
                hit = None
                for p in parts:
                    if p in by_num:
                        hit = by_num[p]
                        break
                if hit is None:
                    continue
                try:
                    dst = img_path(cdir, hit)
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.rename(os.path.join(tmp, fn), dst)
                    meta[str(_sheet_id(hit))] = u"{}|{}|{}".format(hit.SheetNumber, hit.Name, px)
                    ok += 1
                except Exception:
                    bad += 1
            done += len(batch)
            pb.update_progress(done, total)
            _save_json(meta_path, meta)
    _save_json(meta_path, meta)
    return ok, bad, cancelled


# ------------------------------------------------------------------
# 窗口
# ------------------------------------------------------------------

class Viewer(object):
    def __init__(self, items, cdir, start_index):
        self.items = items              # [{'num','name','id','path'}]
        self.cdir = cdir
        self.flags_path = os.path.join(cdir, u"flags.json")
        self.flags = _load_json(self.flags_path, {})   # id(str) -> {'flag': bool, 'note': u''}
        self.cur = max(0, min(start_index, len(items) - 1))
        self.view_idx = list(range(len(items)))        # 列表里显示哪些（过滤后）
        self.zoom = None                # None = 适合窗口
        self.cache = {}
        self.order = []
        self.result_id = None
        self._busy = False
        self._drag = None
        self._build()

    # ---------- 数据 ----------
    def _fl(self, it):
        return self.flags.get(str(it['id']), {})

    def _is_flag(self, it):
        return bool(self._fl(it).get('flag'))

    def _label(self, it):
        return u"{} {}  {}".format(u"⚑" if self._is_flag(it) else u"  ", it['num'], it['name'])

    def _save_flags(self):
        _save_json(self.flags_path, self.flags)

    # ---------- 图片 ----------
    def _bitmap(self, it):
        k = it['id']
        if k in self.cache:
            return self.cache[k]
        p = it['path']
        if not os.path.exists(p):
            return None
        try:
            data = System.IO.File.ReadAllBytes(p)
            ms = System.IO.MemoryStream(data)
            bmp = SD.Bitmap(ms)
        except Exception:
            return None
        self.cache[k] = bmp
        self.order.append(k)
        while len(self.order) > CACHE_LRU:
            old = self.order.pop(0)
            b = self.cache.pop(old, None)
            if b is not None and b is not self.pic.Image:
                try:
                    b.Dispose()
                except Exception:
                    pass
        return bmp

    # ---------- 界面 ----------
    def _build(self):
        f = WF.Form()
        self.form = f
        f.Text = u"图纸快速预览"
        f.Width = 1560
        f.Height = 940
        f.StartPosition = WF.FormStartPosition.CenterScreen
        f.KeyPreview = True
        f.BackColor = SD.Color.FromArgb(245, 245, 247)

        # 左侧：搜索 + 列表
        left = WF.Panel()
        left.Dock = WF.DockStyle.Left
        left.Width = 360
        self.search = WF.TextBox()
        self.search.Dock = WF.DockStyle.Top
        self.search.Font = SD.Font(u"Microsoft YaHei UI", 10.0)
        self.only_flag = WF.CheckBox()
        self.only_flag.Text = u"只看有问题的"
        self.only_flag.Dock = WF.DockStyle.Top
        self.only_flag.Height = 26
        self.lst = WF.ListBox()
        self.lst.Dock = WF.DockStyle.Fill
        self.lst.Font = SD.Font(u"Microsoft YaHei UI", 9.5)
        self.lst.IntegralHeight = False
        left.Controls.Add(self.lst)
        left.Controls.Add(self.only_flag)
        left.Controls.Add(self.search)

        # 右侧
        right = WF.Panel()
        right.Dock = WF.DockStyle.Fill

        bar = WF.Panel()
        bar.Dock = WF.DockStyle.Top
        bar.Height = 84
        flow = WF.FlowLayoutPanel()
        flow.Dock = WF.DockStyle.Top
        flow.Height = 44
        flow.Padding = WF.Padding(6, 6, 6, 0)

        def _btn(text, w, fn):
            b = WF.Button()
            b.Text = text
            b.Width = w
            b.Height = 32
            b.Click += lambda s, e: fn()
            flow.Controls.Add(b)
            return b

        _btn(u"◀ 上一张", 86, self.prev)
        _btn(u"下一张 ▶", 86, self.next)
        self.btn_flag = _btn(u"⚑ 标记有问题 (F)", 128, self.toggle_flag)
        _btn(u"适合窗口 (0)", 100, self.fit)
        _btn(u"在 Revit 中打开 (Enter)", 160, self.open_in_revit)
        _btn(u"导出问题清单", 110, self.export_list)
        self.lbl = WF.Label()
        self.lbl.AutoSize = True
        self.lbl.Font = SD.Font(u"Microsoft YaHei UI", 10.5, SD.FontStyle.Bold)
        self.lbl.Padding = WF.Padding(10, 8, 0, 0)
        flow.Controls.Add(self.lbl)

        notep = WF.Panel()
        notep.Dock = WF.DockStyle.Fill
        notel = WF.Label()
        notel.Text = u"备注："
        notel.Left = 8
        notel.Top = 8
        notel.AutoSize = True
        self.note = WF.TextBox()
        self.note.Left = 56
        self.note.Top = 4
        self.note.Width = 900
        self.note.Anchor = WF.AnchorStyles.Top | WF.AnchorStyles.Left | WF.AnchorStyles.Right
        hint = WF.Label()
        hint.Text = u"← → ↑ ↓ / 翻页键 / 滚轮 切换图纸；Ctrl+滚轮 缩放；拖动平移；F 标记；Enter 在 Revit 打开；Esc 关闭"
        hint.AutoSize = True
        hint.Left = 8
        hint.Top = 28
        hint.ForeColor = SD.Color.Gray
        notep.Controls.Add(notel)
        notep.Controls.Add(self.note)
        notep.Controls.Add(hint)
        bar.Controls.Add(notep)
        bar.Controls.Add(flow)

        self.host = WF.Panel()
        self.host.Dock = WF.DockStyle.Fill
        self.host.BackColor = SD.Color.FromArgb(60, 60, 64)
        self.pic = WF.PictureBox()
        self.pic.Dock = WF.DockStyle.Fill
        self.pic.SizeMode = WF.PictureBoxSizeMode.Zoom
        self.pic.BackColor = SD.Color.FromArgb(60, 60, 64)
        self.host.Controls.Add(self.pic)

        right.Controls.Add(self.host)
        right.Controls.Add(bar)

        f.Controls.Add(right)
        f.Controls.Add(left)

        # 事件
        self.lst.SelectedIndexChanged += lambda s, e: self._on_list()
        self.search.TextChanged += lambda s, e: self._refilter()
        self.only_flag.CheckedChanged += lambda s, e: self._refilter()
        self.note.TextChanged += lambda s, e: self._on_note()
        f.KeyDown += self._on_key
        self.pic.MouseWheel += self._on_wheel
        self.host.MouseWheel += self._on_wheel
        self.pic.MouseDown += self._on_down
        self.pic.MouseMove += self._on_move
        self.pic.MouseUp += self._on_up
        self.pic.DoubleClick += lambda s, e: self._toggle_zoom()
        f.Shown += lambda s, e: self._refilter(select_cur=True)
        f.FormClosing += lambda s, e: self._on_closing()

    # ---------- 列表 ----------
    def _refilter(self, select_cur=False):
        if self._busy:
            return
        self._busy = True
        try:
            q = (self.search.Text or u"").strip().lower()
            only = self.only_flag.Checked
            keep_id = self.items[self.cur]['id'] if self.items else None
            self.view_idx = []
            self.lst.BeginUpdate()
            self.lst.Items.Clear()
            for i, it in enumerate(self.items):
                if only and not self._is_flag(it):
                    continue
                if q and q not in (it['num'] + u" " + it['name']).lower():
                    continue
                self.view_idx.append(i)
                self.lst.Items.Add(self._label(it))
            self.lst.EndUpdate()
            sel = -1
            for j, i in enumerate(self.view_idx):
                if self.items[i]['id'] == keep_id:
                    sel = j
                    break
            if sel < 0 and self.view_idx:
                sel = 0
            if sel >= 0:
                self.lst.SelectedIndex = sel
        finally:
            self._busy = False
        self._show()

    def _on_list(self):
        if self._busy:
            return
        j = self.lst.SelectedIndex
        if 0 <= j < len(self.view_idx):
            self.cur = self.view_idx[j]
            self._show()

    def _select_list_for(self, i):
        if i in self.view_idx:
            j = self.view_idx.index(i)
            self._busy = True
            try:
                self.lst.SelectedIndex = j
            finally:
                self._busy = False

    # ---------- 显示 ----------
    def _show(self):
        if not self.items:
            return
        it = self.items[self.cur]
        bmp = self._bitmap(it)
        self.zoom = self.zoom
        self.pic.Image = bmp
        self._apply_zoom()
        pos = (self.view_idx.index(self.cur) + 1) if self.cur in self.view_idx else 0
        self.lbl.Text = u"{} / {}   {}   {}".format(pos, len(self.view_idx), it['num'], it['name'])
        self._busy = True
        try:
            self.note.Text = self._fl(it).get('note', u"")
        finally:
            self._busy = False
        self.btn_flag.Text = u"⚑ 取消标记 (F)" if self._is_flag(it) else u"⚑ 标记有问题 (F)"
        self.btn_flag.BackColor = SD.Color.FromArgb(255, 205, 205) if self._is_flag(it) else SD.Color.FromArgb(225, 225, 225)
        self.form.Text = u"图纸快速预览 - {} {}".format(it['num'], it['name'])
        # 预取前后各一张
        for d in (1, -1):
            k = self.cur + d
            if 0 <= k < len(self.items):
                try:
                    self._bitmap(self.items[k])
                except Exception:
                    pass

    def _apply_zoom(self):
        if self.zoom is None or self.pic.Image is None:
            self.host.AutoScroll = False
            self.pic.Dock = WF.DockStyle.Fill
            self.pic.SizeMode = WF.PictureBoxSizeMode.Zoom
        else:
            img = self.pic.Image
            self.pic.Dock = getattr(WF.DockStyle, "None")
            self.pic.SizeMode = WF.PictureBoxSizeMode.StretchImage
            self.pic.Width = int(img.Width * self.zoom)
            self.pic.Height = int(img.Height * self.zoom)
            self.host.AutoScroll = True

    def fit(self):
        self.zoom = None
        self._apply_zoom()

    def _toggle_zoom(self):
        if self.zoom is None and self.pic.Image is not None:
            self.zoom = 1.0
        else:
            self.zoom = None
        self._apply_zoom()

    # ---------- 导航 ----------
    def _step(self, d):
        if not self.view_idx:
            return
        if self.cur in self.view_idx:
            j = self.view_idx.index(self.cur) + d
        else:
            j = 0
        j = max(0, min(j, len(self.view_idx) - 1))
        self.cur = self.view_idx[j]
        self._select_list_for(self.cur)
        self._show()

    def prev(self):
        self._step(-1)

    def next(self):
        self._step(1)

    def _goto_edge(self, first):
        if self.view_idx:
            self.cur = self.view_idx[0] if first else self.view_idx[-1]
            self._select_list_for(self.cur)
            self._show()

    # ---------- 事件 ----------
    def _on_key(self, s, e):
        k = e.KeyCode
        typing = isinstance(self.form.ActiveControl, WF.TextBox)
        if k == WF.Keys.Escape:
            self.form.Close()
            e.Handled = True
            return
        if typing:
            return
        handled = True
        if k in (WF.Keys.Right, WF.Keys.Down, WF.Keys.PageDown, WF.Keys.Space):
            self.next()
        elif k in (WF.Keys.Left, WF.Keys.Up, WF.Keys.PageUp):
            self.prev()
        elif k == WF.Keys.Home:
            self._goto_edge(True)
        elif k == WF.Keys.End:
            self._goto_edge(False)
        elif k == WF.Keys.F:
            self.toggle_flag()
        elif k == WF.Keys.Return:
            self.open_in_revit()
        elif k in (WF.Keys.D0, WF.Keys.NumPad0):
            self.fit()
        else:
            handled = False
        if handled:
            e.Handled = True
            e.SuppressKeyPress = True

    def _on_wheel(self, s, e):
        if (WF.Control.ModifierKeys & WF.Keys.Control) == WF.Keys.Control:
            base = self.zoom if self.zoom else (
                float(self.pic.Width) / self.pic.Image.Width if self.pic.Image is not None and self.pic.Image.Width else 1.0)
            self.zoom = max(0.1, min(4.0, base * (1.25 if e.Delta > 0 else 0.8)))
            self._apply_zoom()
            return
        if e.Delta < 0:
            self.next()
        elif e.Delta > 0:
            self.prev()

    def _on_down(self, s, e):
        if self.zoom is not None and e.Button == WF.MouseButtons.Left:
            self._drag = (e.X, e.Y, -self.host.AutoScrollPosition.X, -self.host.AutoScrollPosition.Y)

    def _on_move(self, s, e):
        if self._drag is not None:
            x0, y0, sx, sy = self._drag
            self.host.AutoScrollPosition = SD.Point(int(sx - (e.X - x0)), int(sy - (e.Y - y0)))

    def _on_up(self, s, e):
        self._drag = None

    def _on_note(self):
        if self._busy or not self.items:
            return
        it = self.items[self.cur]
        d = self.flags.setdefault(str(it['id']), {})
        d['note'] = self.note.Text
        d['num'] = it['num']
        d['name'] = it['name']
        if d['note'] and not d.get('flag'):
            d['flag'] = True
            self.btn_flag.Text = u"⚑ 取消标记 (F)"
            self.btn_flag.BackColor = SD.Color.FromArgb(255, 205, 205)
            self._refresh_label(it)

    def _refresh_label(self, it):
        if self.cur in self.view_idx:
            j = self.view_idx.index(self.cur)
            self._busy = True
            try:
                self.lst.Items[j] = self._label(it)
                self.lst.SelectedIndex = j
            finally:
                self._busy = False

    def toggle_flag(self):
        if not self.items:
            return
        it = self.items[self.cur]
        d = self.flags.setdefault(str(it['id']), {})
        d['flag'] = not d.get('flag', False)
        d['num'] = it['num']
        d['name'] = it['name']
        self._save_flags()
        self._refresh_label(it)
        self._show()

    def open_in_revit(self):
        if self.items:
            self.result_id = self.items[self.cur]['id']
            self.form.Close()

    def export_list(self):
        rows = [(d.get('num', u""), d.get('name', u""), d.get('note', u""))
                for k, d in self.flags.items() if d.get('flag')]
        rows.sort(key=lambda r: _natural_key(r[0]))
        if not rows:
            WF.MessageBox.Show(u"还没有标记任何有问题的图纸。", u"导出问题清单")
            return
        desk = System.Environment.GetFolderPath(System.Environment.SpecialFolder.Desktop)
        path = os.path.join(desk, u"图纸核查清单_{}.csv".format(time.strftime(u"%Y%m%d_%H%M")))
        with io.open(path, "w", encoding="utf-8-sig", newline=u"") as f:
            f.write(u"图号,图名,备注\r\n")
            for n, nm, nt in rows:
                f.write(u'"{}","{}","{}"\r\n'.format(n.replace(u'"', u'""'), nm.replace(u'"', u'""'), (nt or u"").replace(u'"', u'""')))
        try:
            WF.Clipboard.SetText(u"\r\n".join(u"{}\t{}\t{}".format(*r) for r in rows))
        except Exception:
            pass
        WF.MessageBox.Show(u"已导出 {} 张到桌面：\n{}\n\n（同时已复制到剪贴板）".format(len(rows), path), u"导出问题清单")

    def _on_closing(self):
        self._save_flags()
        _save_json(os.path.join(self.cdir, u"state.json"), {"last": self.items[self.cur]['id'] if self.items else None})

    def run(self):
        ff_fit(self.form).ShowDialog()
        return self.result_id


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------

def main():
    try:
        sheets = collect_sheets()
        if not sheets:
            forms.alert(u"项目里没有图纸。", title=u"图纸快速预览")
            return
        scope = pick_scope(sheets)
        if not scope:
            return
        q = pick_quality()
        if q is None:
            return
        px, force = q
        cdir = cache_dir_for_doc()
        ok, bad, cancelled = render_missing(scope, cdir, px, force)
        items = []
        for s in scope:
            p = img_path(cdir, s)
            if os.path.exists(p):
                items.append({'num': s.SheetNumber, 'name': s.Name, 'id': _sheet_id(s), 'path': p})
        if not items:
            forms.alert(u"没有可预览的图片（渲染失败或被取消）。", title=u"图纸快速预览")
            return
        state = _load_json(os.path.join(cdir, u"state.json"), {})
        start = 0
        for i, it in enumerate(items):
            if it['id'] == state.get('last'):
                start = i
                break
        while True:
            v = Viewer(items, cdir, start)
            rid = v.run()
            if rid is None:
                break
            sheet = doc.GetElement(DB.ElementId(rid))
            if sheet is not None:
                try:
                    uidoc.ActiveView = sheet
                except Exception:
                    pass
            break
    except Exception as e:
        import traceback
        forms.alert(u"发生错误:\n" + str(e) + u"\n\n" + traceback.format_exc(), title=u"图纸快速预览")


if __name__ == '__main__':
    main()
