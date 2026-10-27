# -*- coding: utf-8 -*-
"""在 Revit 里运行：把各插件的窗口「造出来但不弹出」，套上 form_fit（高分屏放大、换行、可拖动）后截图，
检查文字有没有被截断、控件有没有重叠/出界，再把窗口拉大 25% 截一张看锚点。不改模型（全程在回滚的事务组里）。
结果：%TEMP%\\thl_form_probe\\ 下每个窗口 3 张 PNG（原样 / 适配后 / 拉大后）和 report.txt。
可选变量：ONLY=['export-dwg', ...] 只测这几个。"""
import io, sys, types, re, os, traceback
import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
import System.Windows.Forms as WF
import System.Drawing as SD
import Autodesk.Revit.DB as DBmod
import Autodesk.Revit.UI as UImod

doc = uidoc.Document
OUTDIR = os.path.join(os.environ.get('TEMP', '.'), 'thl_form_probe')
ONLY = globals().get('ONLY')
SRC = r'C:\KAIFA\thl\python-src'
if not os.path.isdir(OUTDIR):
    os.makedirs(OUTDIR)
LOG = []


def flush(final=False):
    with io.open(os.path.join(OUTDIR, 'report.txt'), 'w', encoding='utf-8') as fo:
        fo.write(u'\n'.join(LOG) + (u'\n完成' if final else u''))


class _Stop(Exception):
    pass


class _PB(object):
    cancelled = False
    def __init__(self, *a, **k): pass
    def update_progress(self, a, b): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Forms(object):
    ProgressBar = _PB
    def alert(self, msg, *a, **k):
        LOG.append(u'    [alert] %s' % (u'%s' % msg)[:160].replace(u'\n', u' '))
        return False
    def __getattr__(self, k):
        class _X(object):
            @staticmethod
            def show(*a, **kw):
                return None
        return _X


def _fake_pyrevit():
    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=doc, uidoc=uidoc)
    pr.DB = DBmod
    pr.UI = UImod
    pr.forms = _Forms()
    pr.script = types.SimpleNamespace(get_output=lambda: None, get_logger=lambda: None)
    sys.modules['pyrevit'] = pr


def _load(pid):
    path = os.path.join(SRC, pid, 'script.py') if pid != '_settings' else None
    src = io.open(path, encoding='utf-8').read()
    src = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n',
                 src, flags=re.M)
    n = {'__name__': 'formprobe', '__revit__': uidoc.Application, '__file__': path}
    exec(compile(src, pid, 'exec'), n)
    return n


def _walk(c):
    for x in c.Controls:
        yield x
        for y in _walk(x):
            yield y


def _issues(f):
    out = []
    flags = WF.TextFormatFlags.WordBreak | WF.TextFormatFlags.TextBoxControl
    for c in _walk(f):
        try:
            t = c.Text or u''
            p = c.Parent
            if isinstance(c, (WF.Label, WF.CheckBox, WF.RadioButton)) and t and not c.AutoSize:
                box = 20 * DPI_K if isinstance(c, (WF.CheckBox, WF.RadioButton)) else 0
                need = WF.TextRenderer.MeasureText(t, c.Font, SD.Size(max(10, int(c.Width - box - c.Padding.Horizontal - 2)), 100000), flags).Height
                if need > c.Height + 2:
                    out.append(u'截字 %s「%s」 需高 %d 实际 %d' % (c.GetType().Name, t[:24], need, c.Height))
            if isinstance(c, WF.Button) and t:
                need = WF.TextRenderer.MeasureText(t, c.Font).Width + 8
                if need > c.Width:
                    out.append(u'按钮截字「%s」 需宽 %d 实际 %d' % (t[:16], need, c.Width))
            if p is not None and not getattr(p, 'AutoScroll', False) and c.Dock == getattr(WF.DockStyle, 'None') \
                    and not isinstance(p, (WF.FlowLayoutPanel, WF.TableLayoutPanel)):
                if c.Right > p.ClientSize.Width + 2 or c.Bottom > p.ClientSize.Height + 2:
                    out.append(u'出界 %s「%s」 %s 父 %s' % (c.GetType().Name, (t or c.Name)[:20], c.Bounds, p.ClientSize))
        except Exception as ex:
            out.append(u'检查出错 %s' % ex)
    # 同一容器里手工摆放的控件互相重叠
    def sib(parent):
        kids = [k for k in parent.Controls if k.Dock == getattr(WF.DockStyle, 'None') and not isinstance(k, WF.TabPage)]
        for i in range(len(kids)):
            for j in range(i + 1, len(kids)):
                a, b = kids[i].Bounds, kids[j].Bounds
                r = SD.Rectangle.Intersect(a, b)
                if r.Width > 3 and r.Height > 3 and not isinstance(kids[i], (WF.GroupBox,)) and not isinstance(kids[j], (WF.GroupBox,)):
                    out.append(u'重叠 %s「%s」 与 %s「%s」' % (kids[i].GetType().Name, (kids[i].Text or u'')[:12], kids[j].GetType().Name, (kids[j].Text or u'')[:12]))
        for k in parent.Controls:
            if k.Controls.Count and not isinstance(k, (WF.FlowLayoutPanel, WF.TableLayoutPanel, WF.DataGridView, WF.ListView, WF.NumericUpDown)):
                sib(k)
    sib(f)
    return out


def _shot(f, name):
    try:
        bmp = SD.Bitmap(max(1, f.Width), max(1, f.Height))
        f.DrawToBitmap(bmp, SD.Rectangle(0, 0, f.Width, f.Height))
        bmp.Save(os.path.join(OUTDIR, name + '.png'), SD.Imaging.ImageFormat.Png)
        bmp.Dispose()
    except Exception as ex:
        LOG.append(u'    截图失败 %s' % ex)


def _probe_factory(name, real_fit):
    def probe(f):
        f.StartPosition = WF.FormStartPosition.Manual
        f.Location = SD.Point(-6000, -4000)      # 屏幕外显示，只为截图
        f.ShowInTaskbar = False
        f.Show()
        WF.Application.DoEvents()
        _shot(f, name + '_0原样')
        f.Hide()
        b = f.ClientSize
        real_fit(f)
        f.Location = SD.Point(-6000, -4000)
        f.Show()
        f.PerformLayout()
        WF.Application.DoEvents()
        LOG.append(u'  %s：%s → %s，边框 %s，最小 %s' % (name, b, f.ClientSize, f.FormBorderStyle, f.MinimumSize))
        iss = _issues(f)
        LOG.extend(u'    · ' + x for x in iss[:30])
        if len(iss) > 30:
            LOG.append(u'    · …另有 %d 处' % (len(iss) - 30))
        _shot(f, name + '_1适配后')
        f.Size = SD.Size(int(f.Width * 1.25), int(f.Height * 1.2))
        f.PerformLayout()
        WF.Application.DoEvents()
        iss2 = [x for x in _issues(f) if x not in iss]
        LOG.extend(u'    · 拉大后新问题：' + x for x in iss2[:15])
        _shot(f, name + '_2拉大后')
        f.Close()
        f.Dispose()
        raise _Stop()
    return probe


def _run(pid, name, call):
    LOG.append(u'== %s / %s' % (pid, name))
    flush()
    try:
        n = _load(pid)
        n['ff_fit'] = _probe_factory(u'%s_%s' % (pid, name), n['ff_fit'])
        call(n)
        LOG.append(u'  （没有走到窗口）')
    except _Stop:
        pass
    except SystemExit:
        LOG.append(u'  （脚本提前退出）')
    except Exception:
        LOG.append(u'  出错：' + traceback.format_exc().splitlines()[-1])
        LOG.append(u'    ' + u' | '.join(traceback.format_exc().splitlines()[-6:-1]))
    flush()


def _bb():
    bb = DBmod.BoundingBoxXYZ()
    bb.Min = DBmod.XYZ(0, 0, -8)
    bb.Max = DBmod.XYZ(10, 7, 0)
    return bb


class _FakeWell(object):
    def get_BoundingBox(self, v):
        return _bb()


def _quantity(n):
    res = n['_quantities']([])
    tables, summary = n['_table_rows'](res)
    n['_show_window'](tables, summary, u'测试（0 个构件）', res)


CASES = [
    ('export-dwg', u'导出设置', lambda n: n['_dialog'](n['_load_settings'](), n['_scope_items']([], [], {}))),
    ('control-points', u'导出控制点', lambda n: n['export_dialog'](n['load_settings']())),
    ('pile-cushion', u'松木桩规则', lambda n: n['show_rules_dialog'](n['load_rules'](), 3)),
    ('quantity-export', u'提量结果', _quantity),
    ('sheet-organize', u'小表单', lambda n: n['form_dialog'](None, u'归类设置（可改名字和系统代码）',
                                                          [(u'电力 11KV', [u'电力', u'EL']), (u'通信 TELCO', [u'通信', u'TC'])], [150, 90])),
    ('sheet-organize', u'主窗口', lambda n: n['ff_fit'](n['build_form']({})[0])),
    ('well-number', u'编号设置', lambda n: n['dialog'](n['load'](), 12)),
    ('well-sheet-fit', u'比例排版', lambda n: n['show_dialog']([], n['load_cfg']())),
    ('well-sheet-no', u'号段规则', lambda n: n['edit_rules'](n['sb_load']())),
    ('well-sheet-no', u'重编预览', lambda n: n['show_dialog'](n['sb_load'](), n['make_plan'](n['sb_load']()))),
    ('well-sheets', u'规则预览', lambda n: n['rules_preview_dialog'](_FakeWell())),
    ('well-zone', u'输入框', lambda n: n['ask_text'](u'新建分区参数', u'参数名（如：分区、施工批次）', u'分区')),
    ('well-zone', u'主窗口', lambda n: n['main_dialog']({'cfg': n['load_cfg'](), 'param': '', 'value': '', 'show': {}})),
    ('well-annotate', u'设置', lambda n: n['open_settings']()),
    ('well-sheet-fit', u'设置', lambda n: n['open_settings']()),
    ('well-spec-table', u'设置', lambda n: n['open_settings']()),
    ('elv-merge', u'设置', lambda n: n['open_settings']()),
    ('well-depth-report', u'设置', lambda n: n['open_settings']()),
    ('well-family-guard', u'设置', lambda n: n['open_settings']()),
    ('well-param-sync', u'设置', lambda n: n['open_settings']()),
    ('family-param-dim', u'设置', lambda n: n['open_settings']()),
    ('well-config', u'项目配置', lambda n: n['wcfg_edit']()),
    ('export-nwc', u'导出设置', lambda n: n['dialog'](n['load'](), [], None)),
]

_g = WF.Form()
DPI_K = _g.DeviceDpi / 96.0
_g.Dispose()
LOG.append(u'DPI 缩放 %.2f，文档 %s' % (DPI_K, doc.Title))
_fake_pyrevit()
tg = DBmod.TransactionGroup(doc, u'窗口自检（回滚）')
tg.Start()
try:
    for pid, name, call in CASES:
        if ONLY and pid not in ONLY:
            continue
        _run(pid, name, call)
finally:
    if tg.HasStarted() and not tg.HasEnded():
        tg.RollBack()
flush(True)
