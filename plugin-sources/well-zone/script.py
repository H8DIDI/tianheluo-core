# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '井分区\n标记'
__author__ = 'didi'
__doc__ = ('给井分区：在视图里框选一批井，写入「分区」参数（可新建参数，如 分区、施工批次），整次可 Ctrl+Z 撤销。\n'
           '窗口里列出所有已写入的分区和井数，每个分区一个勾：去掉勾，这个分区的井在当前视图里就不显示；每个分区有自己的颜色，'
           '点「当前视图配色」就在当前视图里按分区着色（用视图过滤器，只作用当前视图，不改井本身，删掉过滤器即可还原）。\n'
           '写入前先预览（共几口井、其中几口已有分区会被覆盖）。「生成明细表」按分区分组排序，每个分区带井数小计。\n'
           '井按「常规模型 + 有标记」识别。')

import io
import os
import json
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from System.Collections.Generic import List
from Autodesk.Revit import DB
from Autodesk.Revit.UI.Selection import ISelectionFilter
from pyrevit import revit, forms
import System.Windows.Forms as WF
import System.Drawing as SD

doc = revit.doc
uidoc = revit.uidoc
TITLE = '井分区标记'
GEN = DB.BuiltInCategory.OST_GenericModel
CFG_PATH = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments),
                        'KaifaTools', '井分区配置.json')
UNASSIGNED = '未分区'
PALETTE = [(230, 57, 70), (42, 157, 143), (69, 123, 157), (244, 162, 97), (131, 56, 236),
           (6, 214, 160), (255, 190, 11), (251, 86, 7), (58, 134, 255), (141, 153, 174)]
BAD_CHARS = set('{}[]|;<>?`~\\:"')


# ============================================================
# 配置（颜色、上次用的参数）
# ============================================================

def load_cfg():
    try:
        with io.open(CFG_PATH, 'r', encoding='utf-8') as f:
            c = json.load(f)
        if isinstance(c, dict):
            c.setdefault('colors', {})
            return c
    except Exception:
        pass
    return {'colors': {}, 'param': ''}


def save_cfg(c):
    try:
        d = os.path.dirname(CFG_PATH)
        if not os.path.isdir(d):
            os.makedirs(d)
        txt = json.dumps(c, ensure_ascii=False, indent=1)
        if not isinstance(txt, type('')):
            txt = txt.decode('utf-8')
        with io.open(CFG_PATH, 'w', encoding='utf-8') as f:
            f.write(txt)
    except Exception:
        pass


def color_of(cfg, pname, value):
    """分区颜色：配置里有就用，没有按已有颜色数顺序取调色板里的下一个并记下来。"""
    key = pname + '|' + value
    if key not in cfg['colors']:
        used = len([k for k in cfg['colors'] if k.startswith(pname + '|')])
        cfg['colors'][key] = list(PALETTE[used % len(PALETTE)])
    return tuple(cfg['colors'][key])


# ============================================================
# 参数
# ============================================================

def _idv(eid):
    try:
        return int(eid.Value)
    except Exception:
        return int(eid.IntegerValue)


def _is_text_param_on_generic(defn, binding):
    try:
        if not isinstance(binding, DB.InstanceBinding):
            return False
        ok = False
        for c in binding.Categories:
            if _idv(c.Id) == int(GEN):
                ok = True
        if not ok:
            return False
        return defn.GetDataType() == DB.SpecTypeId.String.Text
    except Exception:
        return False


def list_params():
    """项目里绑定到「常规模型」实例的文字参数名。"""
    out = []
    it = doc.ParameterBindings.ForwardIterator()
    while it.MoveNext():
        try:
            if _is_text_param_on_generic(it.Key, it.Current):
                out.append(it.Key.Name)
        except Exception:
            continue
    return sorted(set(out))


def param_id(name):
    for p in DB.FilteredElementCollector(doc).OfClass(DB.ParameterElement).ToElements():
        try:
            if p.GetDefinition().Name == name:
                return p.Id
        except Exception:
            continue
    return None


def create_param(name):
    """新建「常规模型」实例文字参数（共享参数，写临时共享参数文件，用完还原用户原来的共享参数文件路径）。"""
    import tempfile
    import uuid
    app = doc.Application
    old = app.SharedParametersFilename
    tmp = os.path.join(tempfile.gettempdir(), 'thl_well_zone_sp.txt')
    guid = str(uuid.uuid4())
    text = ('# This is a Revit shared parameter file.\n# Do not edit manually.\n'
            '*META\tVERSION\tMINVERSION\nMETA\t2\t1\n*GROUP\tID\tNAME\nGROUP\t1\t井分区\n'
            '*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE\n'
            'PARAM\t{}\t{}\tTEXT\t\t1\t1\t\t1\t0\n').format(guid, name)
    with io.open(tmp, 'w', encoding='utf-16', newline='\n') as f:       # 共享参数文件必须 UTF-16 + LF
        f.write(text)
    t = DB.Transaction(doc, '新建分区参数')
    t.Start()
    try:
        app.SharedParametersFilename = tmp
        sp = app.OpenSharedParameterFile()
        d = sp.Groups.get_Item('井分区').Definitions.get_Item(name)
        cats = app.Create.NewCategorySet()
        cats.Insert(doc.Settings.Categories.get_Item(GEN))
        doc.ParameterBindings.Insert(d, app.Create.NewInstanceBinding(cats), DB.GroupTypeId.Data)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    finally:
        try:
            app.SharedParametersFilename = old
        except Exception:
            pass


# ============================================================
# 井与分区
# ============================================================

def _mark(e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        return (p.AsString() or '').strip() if p else ''
    except Exception:
        return ''


def all_wells():
    """全部井：常规模型实例且有标记。只扫一次、不做视图级查询。"""
    out = []
    for e in DB.FilteredElementCollector(doc).OfCategory(GEN).WhereElementIsNotElementType().ToElements():
        if _mark(e):
            out.append(e)
    return out


def zone_of(e, pname):
    try:
        p = e.LookupParameter(pname)
        if p is not None and p.StorageType == DB.StorageType.String:
            return (p.AsString() or '').strip()
    except Exception:
        pass
    return ''


def zone_counts(wells, pname):
    cnt = {}
    for e in wells:
        v = zone_of(e, pname)
        cnt[v] = cnt.get(v, 0) + 1
    return cnt


class _WellFilter(ISelectionFilter):
    def AllowElement(self, e):
        try:
            return e.Category is not None and _idv(e.Category.Id) == int(GEN) and bool(_mark(e))
        except Exception:
            return False

    def AllowReference(self, r, p):
        return False


def write_zone(elems, pname, value):
    """把分区值写进这批井；整次一个事务（一次 Ctrl+Z）。返回(写入数, 跳过数)。"""
    t = DB.Transaction(doc, '井分区标记：{} = {}'.format(pname, value))
    t.Start()
    done = skipped = 0
    try:
        for e in elems:
            p = e.LookupParameter(pname)
            if p is None or p.IsReadOnly or p.StorageType != DB.StorageType.String:
                skipped += 1
                continue
            p.Set(value)
            done += 1
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return done, skipped


# ============================================================
# 当前视图：过滤器显隐 + 配色
# ============================================================

def _safe_name(s):
    return ''.join('_' if ch in BAD_CHARS else ch for ch in s)


def filter_name(pname, value):
    return '井分区_{}_{}'.format(_safe_name(pname), _safe_name(value or UNASSIGNED))


def existing_filters():
    return dict((f.Name, f) for f in DB.FilteredElementCollector(doc).OfClass(DB.ParameterFilterElement).ToElements())


def ensure_filter(pname, value, flts):
    """取（没有就建）这个分区的视图过滤器。未分区 = 参数没有值且有标记。"""
    nm = filter_name(pname, value)
    if nm in flts:
        return flts[nm]
    pid = param_id(pname)
    mark_id = DB.ElementId(DB.BuiltInParameter.ALL_MODEL_MARK)
    has_mark = DB.ElementParameterFilter(DB.ParameterFilterRuleFactory.CreateHasValueParameterRule(mark_id))
    if value:
        rule_f = DB.ElementParameterFilter(DB.ParameterFilterRuleFactory.CreateEqualsRule(pid, value))
    else:
        rule_f = DB.ElementParameterFilter(DB.ParameterFilterRuleFactory.CreateHasNoValueParameterRule(pid))
    both = DB.LogicalAndFilter(has_mark, rule_f)
    cats = List[DB.ElementId]()
    cats.Add(DB.ElementId(GEN))
    f = DB.ParameterFilterElement.Create(doc, nm, cats, both)
    flts[nm] = f
    return f


def _solid_fill_id():
    for fp in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement).ToElements():
        try:
            pat = fp.GetFillPattern()
            if pat.IsSolidFill and pat.Target == DB.FillPatternTarget.Drafting:
                return fp.Id
        except Exception:
            continue
    return DB.ElementId.InvalidElementId


def view_ok(v):
    """当前视图能不能挂过滤器：要是图形视图，且没有被视图样板接管过滤器。"""
    if v is None or isinstance(v, (DB.ViewSheet, DB.ViewSchedule)):
        return False, '请先切换到一个平面/立面/三维视图再用显隐和配色（当前是图纸或明细表）。'
    try:
        if v.ViewTemplateId != DB.ElementId.InvalidElementId:
            return False, '当前视图套了视图样板，过滤器由样板控制，改不动。请换一个没套样板的视图，或先在视图属性里把样板设为「无」。'
    except Exception:
        pass
    return True, ''


def apply_view(pname, zones, cfg, only=None):
    """zones: {分区值: 是否显示}。只作用当前视图：加过滤器、设颜色、设可见性。only 指定时只处理这一个分区。"""
    v = uidoc.ActiveView
    ok, why = view_ok(v)
    if not ok:
        return why
    t = DB.Transaction(doc, '井分区：当前视图显隐与配色')
    t.Start()
    try:
        flts = existing_filters()
        solid = _solid_fill_id()
        have = set(_idv(i) for i in v.GetFilters())
        for val, show in zones.items():
            if only is not None and val != only:
                continue
            f = ensure_filter(pname, val, flts)
            fid = _idv(f.Id)
            if fid not in have:
                v.AddFilter(f.Id)
                have.add(fid)
            r, g, b = color_of(cfg, pname, val) if val else (150, 150, 150)
            col = DB.Color(r, g, b)
            og = DB.OverrideGraphicSettings()
            og.SetProjectionLineColor(col)
            og.SetCutLineColor(col)
            if solid != DB.ElementId.InvalidElementId:
                og.SetSurfaceForegroundPatternId(solid)
                og.SetSurfaceForegroundPatternColor(col)
                og.SetCutForegroundPatternId(solid)
                og.SetCutForegroundPatternColor(col)
            v.SetFilterOverrides(f.Id, og)
            v.SetFilterVisibility(f.Id, bool(show))
        t.Commit()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        return '当前视图设置失败：{}'.format(ex)
    try:
        uidoc.RefreshActiveView()
    except Exception:
        pass
    return ''


# ============================================================
# 明细表
# ============================================================

def make_schedule(pname):
    sch_name = '井分区明细表_{}'.format(_safe_name(pname))
    names = set(v.Name for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule).ToElements())
    nm, k = sch_name, 1
    while nm in names:
        k += 1
        nm = '{}_{}'.format(sch_name, k)
    t = DB.Transaction(doc, '生成井分区明细表')
    t.Start()
    try:
        sch = DB.ViewSchedule.CreateSchedule(doc, DB.ElementId(GEN))
        sch.Name = nm
        d = sch.Definition
        want_zone = param_id(pname)
        mark_pid = DB.ElementId(DB.BuiltInParameter.ALL_MODEL_MARK)
        fam_pid = DB.ElementId(DB.BuiltInParameter.ELEM_FAMILY_AND_TYPE_PARAM)
        pick = {}
        for sf in d.GetSchedulableFields():
            try:
                pid = sf.ParameterId
                if pid == mark_pid and 'mark' not in pick:
                    pick['mark'] = sf
                elif pid == fam_pid and 'fam' not in pick:
                    pick['fam'] = sf
                elif want_zone is not None and pid == want_zone and 'zone' not in pick:
                    pick['zone'] = sf
            except Exception:
                continue
        fields = {}
        for key in ('zone', 'mark', 'fam'):
            if key in pick:
                fields[key] = d.AddField(pick[key])
        if 'zone' not in fields or 'mark' not in fields:
            raise Exception('明细表里找不到「标记」或「{}」字段'.format(pname))
        # 只列有标记的构件（过滤掉没标记的常规模型）
        d.AddFilter(DB.ScheduleFilter(fields['mark'].FieldId, DB.ScheduleFilterType.HasValue))
        g = DB.ScheduleSortGroupField(fields['zone'].FieldId, DB.ScheduleSortOrder.Ascending)
        g.ShowHeader = True
        g.ShowFooter = True
        g.ShowFooterCount = True
        g.ShowFooterTitle = True
        g.ShowBlankLine = False
        d.AddSortGroupField(g)
        d.AddSortGroupField(DB.ScheduleSortGroupField(fields['mark'].FieldId, DB.ScheduleSortOrder.Ascending))
        d.IsItemized = True
        t.Commit()
        return nm, ''
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        return None, str(ex)


# ============================================================
# 窗口
# ============================================================

def ask_text(title, prompt, default=''):
    f = WF.Form()
    f.Text = title
    f.Font = SD.Font('Microsoft YaHei UI', 9.0)
    f.StartPosition = WF.FormStartPosition.CenterParent
    f.FormBorderStyle = WF.FormBorderStyle.FixedDialog
    f.MaximizeBox = False
    f.MinimizeBox = False
    f.ClientSize = SD.Size(360, 110)
    l = WF.Label(); l.Text = prompt; l.Location = SD.Point(14, 12); l.Size = SD.Size(330, 20)
    t = WF.TextBox(); t.Text = default; t.Location = SD.Point(14, 38); t.Size = SD.Size(330, 24)
    ok = WF.Button(); ok.Text = '确定'; ok.DialogResult = WF.DialogResult.OK; ok.Location = SD.Point(172, 72); ok.Size = SD.Size(80, 28)
    ca = WF.Button(); ca.Text = '取消'; ca.DialogResult = WF.DialogResult.Cancel; ca.Location = SD.Point(264, 72); ca.Size = SD.Size(80, 28)
    for c in (l, t, ok, ca):
        f.Controls.Add(c)
    f.AcceptButton = ok
    f.CancelButton = ca
    if f.ShowDialog() != WF.DialogResult.OK:
        return None
    return (t.Text or '').strip()


def valid_name(s):
    return bool(s) and not any(ch in BAD_CHARS for ch in s)


def main_dialog(st):
    """主窗口。返回动作：'pick' 去框选 / 'close' 关闭。st 是跨次保存的状态。"""
    cfg = st['cfg']
    wells = all_wells()
    params = list_params()
    if st.get('param') not in params:
        st['param'] = cfg.get('param') if cfg.get('param') in params else (params[0] if params else '')

    f = WF.Form()
    f.Text = TITLE
    f.Font = SD.Font('Microsoft YaHei UI', 9.0)
    f.StartPosition = WF.FormStartPosition.CenterScreen
    f.FormBorderStyle = WF.FormBorderStyle.FixedDialog
    f.MaximizeBox = False
    f.MinimizeBox = False
    f.ClientSize = SD.Size(520, 560)
    result = {'act': 'close'}
    busy = [True]
    ready = [False]   # 窗口真正显示之后才响应勾选：显示前 WinForms 会为已勾选的行补发「勾选变化」事件，不能当成用户点击

    def lab(t, x, y, w=300, bold=False):
        l = WF.Label(); l.Text = t; l.Location = SD.Point(x, y); l.Size = SD.Size(w, 20)
        if bold:
            l.Font = SD.Font(f.Font, SD.FontStyle.Bold)
        f.Controls.Add(l)
        return l

    lab('分区参数', 16, 12, bold=True)
    cb_par = WF.ComboBox(); cb_par.DropDownStyle = WF.ComboBoxStyle.DropDownList
    for p in params:
        cb_par.Items.Add(p)
    cb_par.Location = SD.Point(16, 36); cb_par.Size = SD.Size(300, 24)
    if st['param'] in params:
        cb_par.SelectedIndex = params.index(st['param'])
    f.Controls.Add(cb_par)
    b_new = WF.Button(); b_new.Text = '新建参数…'; b_new.Location = SD.Point(326, 34); b_new.Size = SD.Size(110, 28)
    f.Controls.Add(b_new)

    lab('分区名（要写入这批井的值）', 16, 72, bold=True)
    cb_val = WF.ComboBox(); cb_val.DropDownStyle = WF.ComboBoxStyle.DropDown
    cb_val.Location = SD.Point(16, 96); cb_val.Size = SD.Size(300, 24)
    f.Controls.Add(cb_val)
    b_pick = WF.Button(); b_pick.Text = '框选井并写入'; b_pick.Location = SD.Point(326, 94); b_pick.Size = SD.Size(110, 28)
    f.Controls.Add(b_pick)

    lab('已有分区（勾选 = 在当前视图显示）', 16, 134, 400, True)
    lv = WF.ListView()
    lv.View = WF.View.Details
    lv.CheckBoxes = True
    lv.FullRowSelect = True
    lv.HideSelection = False
    lv.Location = SD.Point(16, 158); lv.Size = SD.Size(488, 290)
    lv.Columns.Add('分区', 280)
    lv.Columns.Add('井数', 80)
    lv.Columns.Add('颜色', 100)
    f.Controls.Add(lv)

    info = lab('', 16, 454, 488)
    info.Size = SD.Size(488, 40)
    info.ForeColor = SD.Color.FromArgb(90, 90, 90)

    b_color = WF.Button(); b_color.Text = '改所选分区颜色'; b_color.Location = SD.Point(16, 500); b_color.Size = SD.Size(110, 30)
    b_apply = WF.Button(); b_apply.Text = '当前视图配色'; b_apply.Location = SD.Point(134, 500); b_apply.Size = SD.Size(110, 30)
    b_sch = WF.Button(); b_sch.Text = '生成明细表'; b_sch.Location = SD.Point(252, 500); b_sch.Size = SD.Size(110, 30)
    b_close = WF.Button(); b_close.Text = '关闭'; b_close.Location = SD.Point(394, 500); b_close.Size = SD.Size(110, 30)
    for b in (b_color, b_apply, b_sch, b_close):
        f.Controls.Add(b)
    f.CancelButton = b_close

    counts = {}

    def refresh():
        busy[0] = True
        lv.Items.Clear()
        cb_val.Items.Clear()
        pname = st['param']
        counts.clear()
        if pname:
            counts.update(zone_counts(wells, pname))
        keys = sorted([k for k in counts if k], key=lambda s: s)
        order = ([''] if counts.get('', 0) else []) + keys
        for k in order:
            it = WF.ListViewItem(k or UNASSIGNED)
            it.Tag = k
            it.Checked = bool(st['show'].get(k, True))
            it.SubItems.Add(str(counts[k]))
            it.SubItems.Add('')
            if k:
                r, g, b = color_of(cfg, pname, k)
                it.SubItems[2].Text = '■ {},{},{}'.format(r, g, b)
                it.ForeColor = SD.Color.FromArgb(r, g, b)
            lv.Items.Add(it)
            if k:
                cb_val.Items.Add(k)
        if st.get('value'):
            cb_val.Text = st['value']
        tot = len(wells)
        un = counts.get('', 0)
        info.Text = ((st.pop('note', '') + '\n') if st.get('note') else '') + '共 {} 口井（常规模型且有标记），已分区 {} 口，未分区 {} 口。'.format(tot, tot - un, un)
        busy[0] = not ready[0]

    def on_shown(s, e):
        ready[0] = True
        busy[0] = False

    def on_par(s, e):
        if cb_par.SelectedIndex >= 0:
            st['param'] = cb_par.SelectedItem
            cfg['param'] = st['param']
            save_cfg(cfg)
            refresh()

    def on_new(s, e):
        nm = ask_text('新建分区参数', '参数名（如：分区、施工批次）', '分区')
        if not nm:
            return
        if not valid_name(nm):
            forms.alert('参数名不能为空，也不能含 {} [] | ; < > ? ` ~ \\ : "', title=TITLE)
            return
        if nm in params:
            forms.alert('已经有这个参数了，直接在上面的下拉框里选。', title=TITLE)
            return
        if param_id(nm) is not None:
            forms.alert('项目里已有同名参数「{}」，但它没有绑定到常规模型的实例文字参数上。请换个名字。'.format(nm), title=TITLE)
            return
        try:
            create_param(nm)
        except Exception as ex:
            forms.alert('新建参数失败：\n{}'.format(ex), title=TITLE)
            return
        st['param'] = nm
        cfg['param'] = nm
        save_cfg(cfg)
        result['act'] = 'reopen'
        f.Close()

    def on_pick(s, e):
        if not st['param']:
            forms.alert('请先新建一个分区参数。', title=TITLE)
            return
        v = (cb_val.Text or '').strip()
        if not valid_name(v):
            forms.alert('请先填写分区名（不能为空，也不能含 {} [] | ; < > ? ` ~ \\ : "）。', title=TITLE)
            return
        st['value'] = v
        result['act'] = 'pick'
        f.Close()

    def on_check(s, e):
        if busy[0] or not ready[0] or not lv.Focused:    # 只响应用户在列表里的点击
            return
        k = e.Item.Tag
        st['show'][k] = bool(e.Item.Checked)
        msg = apply_view(st['param'], {k: bool(e.Item.Checked)}, cfg, only=k)
        save_cfg(cfg)
        if msg:
            busy[0] = True
            e.Item.Checked = not e.Item.Checked
            st['show'][k] = bool(e.Item.Checked)
            busy[0] = False
            info.Text = msg

    def on_color(s, e):
        if lv.SelectedItems.Count == 0 or not lv.SelectedItems[0].Tag:
            info.Text = '先在列表里选中一个分区（未分区没有颜色）。'
            return
        k = lv.SelectedItems[0].Tag
        cur = color_of(cfg, st['param'], k)
        d = WF.ColorDialog()
        d.Color = SD.Color.FromArgb(cur[0], cur[1], cur[2])
        d.FullOpen = True
        if d.ShowDialog() == WF.DialogResult.OK:
            cfg['colors'][st['param'] + '|' + k] = [d.Color.R, d.Color.G, d.Color.B]
            save_cfg(cfg)
            refresh()

    def on_apply(s, e):
        zones = {}
        for it in lv.Items:
            zones[it.Tag] = bool(it.Checked)
        msg = apply_view(st['param'], zones, cfg)
        save_cfg(cfg)
        info.Text = msg or '已在当前视图按分区着色（视图过滤器，名称以「井分区_」开头；删掉这些过滤器即可还原）。'

    def on_sch(s, e):
        if not st['param']:
            return
        nm, err = make_schedule(st['param'])
        info.Text = ('已生成明细表「{}」，在项目浏览器的明细表/数量里。'.format(nm)) if nm else '生成明细表失败：{}'.format(err)

    cb_par.SelectedIndexChanged += on_par
    b_new.Click += on_new
    b_pick.Click += on_pick
    lv.ItemChecked += on_check
    b_color.Click += on_color
    b_apply.Click += on_apply
    b_sch.Click += on_sch
    b_close.Click += lambda s, e: f.Close()
    f.Shown += on_shown
    refresh()
    f.ShowDialog()
    save_cfg(cfg)
    return result['act']


def pick_and_write(st):
    pname, value = st['param'], st['value']
    try:
        sel = list(uidoc.Selection.PickElementsByRectangle(_WellFilter(), '框选要写入「{} = {}」的井，框完按 Finish'.format(pname, value)))
    except Exception:
        return '已取消框选。'
    wells = [e for e in sel if _mark(e)]
    if not wells:
        return '框里没有井（常规模型且有标记）。'
    over = [e for e in wells if zone_of(e, pname) and zone_of(e, pname) != value]
    same = [e for e in wells if zone_of(e, pname) == value]
    msg = '将给 {} 口井写入「{} = {}」。'.format(len(wells), pname, value)
    if over:
        msg += '\n其中 {} 口原来已有别的分区，会被覆盖。'.format(len(over))
    if same:
        msg += '\n其中 {} 口本来就是这个分区。'.format(len(same))
    if WF.MessageBox.Show(msg + '\n\n确定写入吗？', TITLE, WF.MessageBoxButtons.OKCancel, WF.MessageBoxIcon.Question) != WF.DialogResult.OK:
        return '已取消，没有写入。'
    done, skipped = write_zone(wells, pname, value)
    return '已写入 {} 口井{}。'.format(done, '，{} 口没有这个参数或只读而跳过'.format(skipped) if skipped else '')


def main():
    try:
        st = {'cfg': load_cfg(), 'param': '', 'value': '', 'show': {}}
        note = ''
        guard = 0
        while guard < 200:
            guard += 1
            act = main_dialog(st)
            if act == 'pick':
                note = pick_and_write(st)
                st['note'] = note
                continue
            if act == 'reopen':
                continue
            break
    except Exception as ex:
        forms.alert('发生错误：\n{}\n\n{}'.format(ex, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
