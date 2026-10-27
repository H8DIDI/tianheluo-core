# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '一键导出NWC'
__author__ = 'didi'
__doc__ = ('把当前三维视图，或一次勾选多个三维视图，导出成 Navisworks 的 NWC（每个视图一个文件）。\n'
           '一个窗口里选好范围、坐标、链接、是否带属性和保存位置（都会记住），带进度条可取消，导完列出每个文件的大小和用时。\n'
           '默认不转换链接的 CAD（最常见的卡死原因）。\n'
           '需要电脑上装有 Navisworks 导出插件（Navisworks NWC Export Utility）。')

import io
import os
import re
import json
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from Autodesk.Revit import DB
from pyrevit import revit, forms
import System.Windows.Forms as WF
import System.Drawing as SD
import time

doc = revit.doc
uidoc = revit.uidoc
TITLE = '一键导出NWC'
SETTINGS = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments),
                        'KaifaTools', '导出NWC设置.json')


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

def load():
    s = {'folder': System.Environment.GetFolderPath(System.Environment.SpecialFolder.Desktop),
         'shared': True, 'links': False, 'cad': False, 'props': True, 'scope': 'active', 'views': []}
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


def safe(name):
    return re.sub(r'[\\/:*?"<>|{}]', '_', name or 'model').strip() or 'model'


def dialog(s, views, act):
    """一个窗口里选完：范围（当前视图 / 勾选多个三维视图 / 整个模型）、坐标、链接、属性、保存位置。取消返回 None。"""
    f = WF.Form()
    f.Text = TITLE
    f.Font = SD.Font('Microsoft YaHei UI', 9.0)
    f.StartPosition = WF.FormStartPosition.CenterScreen
    f.FormBorderStyle = WF.FormBorderStyle.FixedDialog
    f.MaximizeBox = f.MinimizeBox = False
    W = 560
    y = [14]

    def label(t, bold=False):
        l = WF.Label(); l.Text = t; l.Location = SD.Point(16, y[0]); l.Size = SD.Size(W - 32, 20)
        if bold:
            l.Font = SD.Font(f.Font, SD.FontStyle.Bold)
        f.Controls.Add(l); y[0] += 22

    def check(t, v):
        c = WF.CheckBox(); c.Text = t; c.Checked = bool(v); c.Location = SD.Point(28, y[0]); c.Size = SD.Size(W - 44, 22)
        f.Controls.Add(c); y[0] += 26
        return c

    label('导出范围', True)
    radios = []
    for key, txt in (('active', '当前三维视图「%s」' % act.Name if act is not None else ''),
                     ('views', '勾选多个三维视图（每个视图一个 NWC）'), ('model', '整个模型（不按视图过滤）')):
        if not txt:
            continue
        r = WF.RadioButton(); r.Text = txt; r.Tag = key; r.Location = SD.Point(28, y[0]); r.Size = SD.Size(W - 44, 22)
        f.Controls.Add(r); radios.append(r); y[0] += 24
    want = s.get('scope', 'active')
    (([r for r in radios if r.Tag == want] or radios)[0]).Checked = True
    lst = WF.CheckedListBox(); lst.CheckOnClick = True; lst.IntegralHeight = False
    lst.Location = SD.Point(44, y[0]); lst.Size = SD.Size(W - 60, 150)
    picked = set(s.get('views') or [])
    for v in views:
        lst.Items.Add(v.Name, v.Name in picked)
    f.Controls.Add(lst); y[0] += 158

    def sync(*a):
        lst.Enabled = any(r.Checked and r.Tag == 'views' for r in radios)
    for r in radios:
        r.CheckedChanged += sync
    sync()
    y[0] += 4
    label('选项', True)
    c_shared = check('共享坐标（和其它专业、Navisworks 里对齐用，推荐；不勾 = 项目内部坐标）', s.get('shared', True))
    c_links = check('连同链接的 Revit 模型一起导出', s.get('links', False))
    c_props = check('导出构件属性（Navisworks 里能查参数；不需要时取消勾选，导出更快、文件更小）', s.get('props', True))
    c_cad = check('转换链接的 CAD（经常卡死，一般不要勾）', s.get('cad', False))
    y[0] += 4
    label('保存到', True)
    t_dir = WF.TextBox(); t_dir.Text = s.get('folder') or ''; t_dir.Location = SD.Point(28, y[0]); t_dir.Size = SD.Size(W - 140, 24)
    f.Controls.Add(t_dir)
    b_dir = WF.Button(); b_dir.Text = '浏览…'; b_dir.Location = SD.Point(W - 104, y[0] - 1); b_dir.Size = SD.Size(88, 26)
    f.Controls.Add(b_dir)

    def browse(sender, e):
        d = WF.FolderBrowserDialog(); d.SelectedPath = t_dir.Text
        if d.ShowDialog() == WF.DialogResult.OK:
            t_dir.Text = d.SelectedPath
    b_dir.Click += browse
    y[0] += 42
    ok = WF.Button(); ok.Text = '开始导出'; ok.DialogResult = WF.DialogResult.OK; ok.Location = SD.Point(W - 200, y[0]); ok.Size = SD.Size(90, 30)
    ca = WF.Button(); ca.Text = '取消'; ca.DialogResult = WF.DialogResult.Cancel; ca.Location = SD.Point(W - 104, y[0]); ca.Size = SD.Size(88, 30)
    f.Controls.Add(ok); f.Controls.Add(ca); f.AcceptButton = ok; f.CancelButton = ca
    f.ClientSize = SD.Size(W, y[0] + 44)
    while True:
        if ff_fit(f).ShowDialog() != WF.DialogResult.OK:
            return None
        scope = [r.Tag for r in radios if r.Checked][0]
        names = [lst.Items[i] for i in range(lst.Items.Count) if lst.GetItemChecked(i)]
        folder = t_dir.Text.strip()
        if scope == 'views' and not names:
            forms.alert('请至少勾选一个三维视图。', title=TITLE)
            continue
        if not folder or not os.path.isdir(folder):
            forms.alert('保存位置不存在：%s' % folder, title=TITLE)
            continue
        out = dict(s)
        out.update({'scope': scope, 'views': names, 'shared': c_shared.Checked, 'links': c_links.Checked,
                    'props': c_props.Checked, 'cad': c_cad.Checked, 'folder': folder})
        return out


def main():
    if not DB.OptionalFunctionalityUtils.IsNavisworksExporterAvailable():
        forms.alert('没有找到 Navisworks 导出插件。\n请安装与 Revit 2026 对应的「Navisworks NWC Export Utility」，'
                    '装好后在「附加模块 → 外部工具」里能看到 Navisworks。', title=TITLE)
        return
    views = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.View3D) if not v.IsTemplate]
    views.sort(key=lambda v: v.Name)
    act = doc.ActiveView if isinstance(doc.ActiveView, DB.View3D) and not doc.ActiveView.IsTemplate else None
    s = dialog(load(), views, act)
    if s is None:
        return
    save(s)
    if s['scope'] == 'active' and act is not None:
        targets = [act]
    elif s['scope'] == 'views':
        by = dict((v.Name, v) for v in views)
        targets = [by[n] for n in s['views'] if n in by]
    else:
        targets = [None]

    base = safe(doc.Title[:-4] if doc.Title.lower().endswith('.rvt') else doc.Title)
    done, fail = [], []
    t_all = time.time()
    with forms.ProgressBar(title='正在导出 NWC…（{value}/{max_value}，大模型每个可能要几分钟）', cancellable=True) as pb:
        for i, v in enumerate(targets):
            if pb.cancelled:
                break
            pb.update_progress(i, len(targets))
            o = DB.NavisworksExportOptions()
            o.ExportScope = DB.NavisworksExportScope.View if v is not None else DB.NavisworksExportScope.Model
            if v is not None:
                o.ViewId = v.Id
            o.Coordinates = DB.NavisworksCoordinates.Shared if s['shared'] else DB.NavisworksCoordinates.Internal
            o.ExportLinks = bool(s['links'])
            o.ConvertElementProperties = bool(s.get('props', True))
            o.ConvertLinkedCADFormats = bool(s.get('cad', False))
            name = base if v is None else '%s_%s' % (base, safe(v.Name))
            t0 = time.time()
            try:
                doc.Export(s['folder'], name, o)
                fn = os.path.join(s['folder'], name + '.nwc')
                mb = os.path.getsize(fn) / 1048576.0 if os.path.exists(fn) else 0
                done.append('%s.nwc（%.1f MB，%d 秒）' % (name, mb, time.time() - t0))
            except Exception as ex:
                fail.append('%s：%s' % (name, ex))
    msg = '已导出 %d 个 NWC（共 %d 秒）到：\n%s\n\n%s' % (len(done), time.time() - t_all, s['folder'], '\n'.join(done[:15]))
    if len(done) + len(fail) < len(targets):
        msg += '\n\n已取消，剩下 %d 个没有导出。' % (len(targets) - len(done) - len(fail))
    if fail:
        msg += '\n\n失败 %d 个：\n%s' % (len(fail), '\n'.join(fail[:8]))
    forms.alert(msg, title=TITLE)
    if done:
        try:
            System.Diagnostics.Process.Start('explorer.exe', s['folder'])
        except Exception:
            pass


if __name__ == '__main__':
    try:
        main()
    except Exception:
        forms.alert('导出出错：\n' + traceback.format_exc(), title=TITLE)
