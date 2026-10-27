# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '井规格表'
__author__ = 'didi'
__doc__ = ('给井大样图纸放一张「井规格表」：用 Revit 明细表只列这口井自己的数据（井号、分区、偏移、井盖面标高、井底标高、井内深度、井整体高度……），'
           '放在图纸左上角。数据是活的：改了井的参数，表跟着变。\n'
           '要显示哪些字段、表放在图纸的哪个位置、只处理哪类图名的图纸，都在「设置…」里改（文档\\KaifaTools\\井规格表设置.json）；换项目直接复制这个文件。\n'
           '运行前会先告诉你要做几张、已经有几张；已经有规格表的图纸默认跳过，不会重复放；进度条可以随时取消，已做的保留。')

import io
import os
import re
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from Autodesk.Revit import DB
from pyrevit import revit, forms
import System.Windows.Forms as WF
import json

# @include _shared/form_fit.py
# @include _shared/settings_ui.py

doc = revit.doc
uidoc = revit.uidoc
TITLE = '井规格表'
FT = 304.8
CFG_FILE = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), 'KaifaTools', '井规格表设置.json')

DEFAULTS = {
    '说明': ('井规格表的设置。fields=表里要显示的参数名（模型里的实际参数名，顺序就是列顺序）；key_field=用来把表限定到这口井的参数（默认「标记」，值是井号）；'
            'x_mm/y_mm=表左上角在图纸上的位置（mm，原点在图纸左下角附近）；name_prefix=明细表名字前缀；sheet_filter=只处理图名包含这段文字的图纸（空=全部井大样图纸）；'
            'view_tag=视图名里的井大样标记，用来从视图名取井号；show_title=表上方是否显示明细表标题；rebuild=已有规格表的图纸是否删掉重做（改了字段后用）；'
            'group_param=给每张规格明细表写「分组」用的项目参数名（第一次运行自动新建，绑到「明细表」类别）；folder_name=项目浏览器里文件夹的名字；'
            'bilingual=true 时列标题写成「中文 换行 英文」，英文取自 headings_en（参数名: 英文，没写的列只显示中文）；align_center=true 时表里的文字（标题和内容）居中；col_width_mm=指定列的宽度（mm，写参数名: 宽度，没写的列自动）；group_by_zone=true 时文件夹名是「文件夹名·分区」（每个分区一个文件夹），false 时全部放进同一个文件夹；zone_field=取分区用的井参数名。'),
    'fields': ['族与类型', '标记', '分区', '相对标高的偏移', '井底标高', '井内深度'],
    'key_field': '标记',
    'category': 'OST_GenericModel',
    'x_mm': 30.0,
    'y_mm': 565.0,
    'name_prefix': '井规格_',
    'sheet_filter': 'BTP-IF1',
    'view_tag': '_AG-',
    'show_title': False,
    'rebuild': False,
    'group_param': '井规格分组',
    'folder_name': '井规格表',
    'group_by_zone': True,
    'zone_field': '分区',
    'col_width_mm': {'族与类型': 95.0, '相对标高的偏移': 42.0, '井底标高': 36.0, '井内深度': 30.0, '井盖面标高': 32.0, '井整体高度': 32.0},
    'bilingual': True,
    'headings_en': {
        '族与类型': 'Family and Type', '标记': 'Mark', '分区': 'Zone', '相对标高的偏移': 'Offset from Level',
        '井底标高': 'Well Bottom Level', '井内深度': 'Inner Depth', '井盖面标高': 'Cover Level', '井整体高度': 'Overall Height',
        '井盖面标高': 'Cover Level', '井形态分类': 'Well Type', '开洞总数': 'Openings', '北面开洞': 'North Opening',
        '南面开洞': 'South Opening', '东面开洞': 'East Opening', '西面开洞': 'West Opening',
    },
    'align_center': True,
}


# ============================================================
# 设置
# ============================================================

def save_cfg(c):
    d = os.path.dirname(CFG_FILE)
    if not os.path.isdir(d):
        os.makedirs(d)
    txt = json.dumps(c, ensure_ascii=False, indent=1)
    if not isinstance(txt, type('')):
        txt = txt.decode('utf-8')
    with io.open(CFG_FILE, 'w', encoding='utf-8') as f:
        f.write(txt)


def load_cfg():
    """读设置；文件不存在或是空的就写一份默认的。某一项缺了用默认值。"""
    c = json.loads(json.dumps(DEFAULTS))
    try:
        if (not os.path.isfile(CFG_FILE)) or os.path.getsize(CFG_FILE) == 0:
            save_cfg(c)
            return c
        with io.open(CFG_FILE, 'r', encoding='utf-8') as f:
            u = json.load(f)
        if isinstance(u, dict):
            for k, v in u.items():
                if k in c and k != '说明':
                    c[k] = v
    except Exception:
        pass
    if not isinstance(c.get('fields'), list) or not c['fields']:
        c['fields'] = list(DEFAULTS['fields'])
    return c


def settings_fields():
    return [
        {'kind': 'header', 'label': '处理哪些图纸'},
        {'key': 'sheet_filter', 'label': '只处理图名包含这段文字的图纸', 'kind': 'text',
         'hint': '例如 BTP-IF1。留空 = 全部井大样图纸（视图名带 _AG- 的图纸）。'},
        {'kind': 'header', 'label': '表里显示什么'},
        {'key': 'fields', 'label': '要显示的参数（逗号隔开，顺序 = 列顺序）', 'kind': 'list',
         'hint': '写模型里的参数名，例如：标记, 分区, 相对标高的偏移, 井盖面标高, 井底标高, 井内深度, 井整体高度, 井形态分类, 开洞总数。'},
        {'key': 'key_field', 'label': '用哪个参数认「这口井」', 'kind': 'text',
         'hint': '默认「标记」（值是井号）。'},
        {'key': 'show_title', 'label': '表上方显示明细表标题', 'kind': 'bool', 'bool_text': '显示（井规格_T-003 这样的标题）'},
        {'kind': 'header', 'label': '放在图纸哪里（表的左上角，mm）'},
        {'key': 'x_mm', 'label': '左上角 X', 'kind': 'num', 'min': 0, 'max': 3000,
         'hint': '默认 30：图框左边内侧。'},
        {'key': 'y_mm', 'label': '左上角 Y', 'kind': 'num', 'min': 0, 'max': 3000,
         'hint': '默认 565：图框上边内侧。表向下长。位置和视图重叠时，改这里，或用「井图比例排版」缩小可用区域。'},
        {'kind': 'header', 'label': '项目浏览器里收纳成文件夹'},
        {'key': 'folder_name', 'label': '文件夹名字', 'kind': 'text',
         'hint': '明细表在项目浏览器的「明细表/数量」里按这个名字收进文件夹，点开才展开。需要先在浏览器组织里按「{}」成组（运行结束会提示具体步骤，只做一次）。'.format('井规格分组')},
        {'key': 'group_by_zone', 'label': '每个分区一个文件夹', 'kind': 'bool', 'bool_text': '分区文件夹（井规格表·B栋北侧 这样）；不勾 = 全部放一个文件夹'},
        {'key': 'group_param', 'label': '分组用的项目参数名', 'kind': 'text', 'hint': '默认「井规格分组」，第一次运行自动新建。'},
        {'kind': 'header', 'label': '其他'},
        {'key': 'name_prefix', 'label': '明细表名字前缀', 'kind': 'text', 'hint': '明细表叫「前缀 + 井号」，例如 井规格_T-003。'},
        {'key': 'rebuild', 'label': '已有规格表的图纸：删掉重做', 'kind': 'bool', 'bool_text': '重做（改了字段或位置后用；默认跳过）'},
    ]


def open_settings():
    c = load_cfg()
    res = su_fields_dialog('井规格表 设置', '改完点保存立即生效。「恢复默认」只改窗口里的值，点保存才生效。',
                           settings_fields(), c, DEFAULTS,
                           extra_buttons=[('用记事本打开文件', lambda: su_open_file(CFG_FILE))])
    if res is None:
        return False
    out = json.loads(json.dumps(c))
    for k, v in res.items():
        out[k] = v
    if not out.get('fields'):
        out['fields'] = list(DEFAULTS['fields'])
    save_cfg(out)
    return True


# ============================================================
# 图纸与井号
# ============================================================

def _mark_of_sheet(sheet, cfg):
    """从图纸上视图名取井号：平面_AG-T-003-7d92 → T-003（去掉末尾 4 位十六进制后缀）。"""
    tag = cfg.get('view_tag') or '_AG-'
    try:
        for vid in sheet.GetAllViewports():
            v = doc.GetElement(doc.GetElement(vid).ViewId)
            nm = v.Name or ''
            i = nm.find(tag)
            if i < 0:
                continue
            m = nm[i + len(tag):]
            m = re.sub(r'-[0-9a-fA-F]{4}$', '', m)
            if m:
                return m
    except Exception:
        pass
    return None


def collect_sheets(cfg):
    """井大样图纸：图名含过滤文字、上面有带井大样标记的视图。返回 [(sheet, mark)]，按图号排序。"""
    flt = (cfg.get('sheet_filter') or '').strip()
    out = []
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
        if flt and flt not in (s.Name or ''):
            continue
        mk = _mark_of_sheet(s, cfg)
        if mk:
            out.append((s, mk))
    out.sort(key=lambda t: t[0].SheetNumber or '')
    return out


def model_marks(cfg):
    """模型里有哪些井号（key_field 的值）：一次扫出 {井号: 构件}，用来跳过模型里找不到的井，也用来按真实参数挑明细表字段。"""
    got = {}
    cat = getattr(DB.BuiltInCategory, cfg.get('category') or 'OST_GenericModel')
    key = cfg.get('key_field') or '标记'
    for e in DB.FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType().ToElements():
        try:
            if key == '标记':
                p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
            else:
                p = e.LookupParameter(key)
            if p is not None:
                s = p.AsString()
                if s and s not in got:
                    got[s] = e
        except Exception:
            continue
    return got


def existing_schedules(cfg):
    """已有的规格明细表：{名字: ViewSchedule}。"""
    pre = cfg.get('name_prefix') or ''
    out = {}
    for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule).ToElements():
        try:
            if (v.Name or '').startswith(pre):
                out[v.Name] = v
        except Exception:
            continue
    return out


def placed_instances(sheet):
    """图纸上的明细表实例。"""
    try:
        return list(DB.FilteredElementCollector(doc, sheet.Id).OfClass(DB.ScheduleSheetInstance).ToElements())
    except Exception:
        return []


# ============================================================
# 项目浏览器分组（收纳成文件夹）
# ============================================================

GROUP_NAME = '天河落'


def _param_element_id(name):
    for p in DB.FilteredElementCollector(doc).OfClass(DB.ParameterElement).ToElements():
        try:
            if p.GetDefinition().Name == name:
                return p.Id
        except Exception:
            continue
    return None


def _bound_to_schedules(name):
    it = doc.ParameterBindings.ForwardIterator()
    sch = int(DB.BuiltInCategory.OST_Schedules)
    while it.MoveNext():
        try:
            if it.Key.Name != name:
                continue
            for c in it.Current.Categories:
                if int(c.Id.Value) == sch:
                    return True
        except Exception:
            continue
    return False


def ensure_group_param(cfg):
    """确保有「分组」项目参数（文字，绑到「明细表」类别）。没有就新建（共享参数，写临时共享参数文件，用完还原用户原来的路径）。
    返回 True 表示本次新建了。"""
    name = cfg.get('group_param') or ''
    if not name or _bound_to_schedules(name):
        return False
    import tempfile
    import uuid
    app = doc.Application
    old = app.SharedParametersFilename
    tmp = os.path.join(tempfile.gettempdir(), 'thl_well_spec_sp.txt')
    guid = str(uuid.uuid4())
    text = ('# This is a Revit shared parameter file.\n# Do not edit manually.\n'
            '*META\tVERSION\tMINVERSION\nMETA\t2\t1\n*GROUP\tID\tNAME\nGROUP\t1\t{}\n'
            '*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\tDESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE\n'
            'PARAM\t{}\t{}\tTEXT\t\t1\t1\t\t1\t0\n').format(GROUP_NAME, guid, name)
    with io.open(tmp, 'w', encoding='utf-16', newline='\n') as f:       # 共享参数文件必须 UTF-16 + LF
        f.write(text)
    t = DB.Transaction(doc, '新建井规格分组参数')
    t.Start()
    try:
        app.SharedParametersFilename = tmp
        sp = app.OpenSharedParameterFile()
        d = sp.Groups.get_Item(GROUP_NAME).Definitions.get_Item(name)
        cats = app.Create.NewCategorySet()
        cats.Insert(doc.Settings.Categories.get_Item(DB.BuiltInCategory.OST_Schedules))
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
    return True


def _group_value(cfg, sample):
    folder = cfg.get('folder_name') or '井规格表'
    if not cfg.get('group_by_zone'):
        return folder
    zone = ''
    try:
        p = sample.LookupParameter(cfg.get('zone_field') or '分区') if sample is not None else None
        zone = (p.AsString() or '').strip() if p is not None else ''
    except Exception:
        zone = ''
    return '{}·{}'.format(folder, zone or '未分区')


def _apply_widths(vs, cfg):
    """按设置加宽指定的列（默认「族与类型」95 mm，族名很长，不加宽会被挤成好几行）。已有的规格表重新运行也会调整。"""
    widths = cfg.get('col_width_mm') or {}
    if not widths:
        return
    try:
        df = vs.Definition
        for i in range(df.GetFieldCount()):
            f = df.GetField(i)
            w = widths.get(f.GetName())
            if w:
                nw = float(w) / FT
                if abs(f.GridColumnWidth - nw) > 1e-6:
                    f.GridColumnWidth = nw
    except Exception:
        pass


def _apply_format(vs, cfg):
    """列标题中英文（中文在上、英文在下）、文字居中。已有的规格表重新运行也会套用。"""
    try:
        df = vs.Definition
        en = cfg.get('headings_en') or {}
        for i in range(df.GetFieldCount()):
            f = df.GetField(i)
            nm = f.GetName()
            if cfg.get('bilingual'):
                e = en.get(nm)
                head = (nm + chr(10) + e) if e else nm
            else:
                head = nm
            if f.ColumnHeading != head:
                f.ColumnHeading = head
            if cfg.get('align_center') and f.HorizontalAlignment != DB.ScheduleHorizontalAlignment.Center:
                f.HorizontalAlignment = DB.ScheduleHorizontalAlignment.Center
    except Exception:
        pass


def _set_group(vs, cfg, sample):
    """给规格明细表写分组参数（项目浏览器按它成文件夹）。"""
    name = cfg.get('group_param') or ''
    if not name:
        return
    try:
        p = vs.LookupParameter(name)
        if p is not None and not p.IsReadOnly:
            v = _group_value(cfg, sample)
            if p.AsString() != v:
                p.Set(v)
    except Exception:
        pass


def folder_ready(cfg, vs):
    """项目浏览器的「明细表/数量」是否已经按分组参数成文件夹（用一张规格表看它被归到了哪个文件夹）。"""
    try:
        pid = _param_element_id(cfg.get('group_param') or '')
        if pid is None or vs is None:
            return False
        bo = DB.BrowserOrganization.GetCurrentBrowserOrganizationForSchedules(doc)
        for f in bo.GetFolderItems(vs.Id):
            if int(f.ElementId.Value) == int(pid.Value):
                return True
    except Exception:
        pass
    return False


# ============================================================
# 建表
# ============================================================

def _field_map(df, sample=None):
    """可用字段：{名字: SchedulableField}。项目里常有好几个同名参数（不同族带进来的共享参数 id 不同）：
    有这口井的实际构件时，认它真正带着的那一个（参数 id 一致）；没有时认实例参数。"""
    cands = {}
    for f in df.GetSchedulableFields():
        try:
            nm = f.GetName(doc)
        except Exception:
            continue
        cands.setdefault(nm, []).append(f)
    out = {}
    for nm, lst in cands.items():
        pick = None
        if sample is not None:
            try:
                p = sample.LookupParameter(nm)
                if p is None and len(lst) == 1:
                    p = None
                if p is not None:
                    for f in lst:
                        if int(f.ParameterId.Value) == int(p.Id.Value):
                            pick = f
                            break
            except Exception:
                pick = None
        if pick is None:
            for f in lst:
                if f.FieldType == DB.ScheduleFieldType.Instance:
                    pick = f
                    break
        out[nm] = pick or lst[0]
    return out


def build_schedule(sheet, mark, cfg, existing, sample=None):
    """给一张图纸建（或重做）规格表并放到图纸上。返回 (状态, 缺的字段列表)：状态 'new' / 'placed' / 'skip' / 'rebuilt'。"""
    name = (cfg.get('name_prefix') or '') + mark
    pre_inst = placed_instances(sheet)
    vs = existing.get(name)
    status = 'new'
    if vs is not None:
        on_sheet = [i for i in pre_inst if int(i.ScheduleId.Value) == int(vs.Id.Value)]
        if cfg.get('rebuild'):
            for i in on_sheet:
                doc.Delete(i.Id)
            try:
                doc.Delete(vs.Id)
            except Exception:
                raise ValueError('明细表「{}」正处于打开状态，先关掉它再重做'.format(name))
            vs = None
            status = 'rebuilt'
        elif on_sheet:
            _set_group(vs, cfg, sample)
            _apply_widths(vs, cfg)
            _apply_format(vs, cfg)
            return 'skip', []
        else:
            status = 'placed'
    missing = []
    if vs is None:
        cat = DB.ElementId(getattr(DB.BuiltInCategory, cfg.get('category') or 'OST_GenericModel'))
        vs = DB.ViewSchedule.CreateSchedule(doc, cat)
        vs.Name = name
        df = vs.Definition
        avail = _field_map(df, sample)
        key = cfg.get('key_field') or '标记'
        ids = {}
        for fn in cfg['fields']:
            f = avail.get(fn)
            if f is None:
                missing.append(fn)
                continue
            ids[fn] = df.AddField(f)
        if key not in ids:
            kf = avail.get(key)
            if kf is None:
                doc.Delete(vs.Id)
                raise ValueError('找不到「{}」参数，没法认井'.format(key))
            ids[key] = df.AddField(kf)
            ids[key].IsHidden = True
        if not ids:
            doc.Delete(vs.Id)
            raise ValueError('设置里的字段一个都没找到')
        df.IsItemized = True
        df.AddFilter(DB.ScheduleFilter(ids[key].FieldId, DB.ScheduleFilterType.Equal, mark))
        try:
            df.ShowTitle = bool(cfg.get('show_title'))
        except Exception:
            pass
        existing[name] = vs
    pt = DB.XYZ(float(cfg.get('x_mm', 30.0)) / FT, float(cfg.get('y_mm', 565.0)) / FT, 0)
    DB.ScheduleSheetInstance.Create(doc, sheet.Id, vs.Id, pt)
    _set_group(vs, cfg, sample)
    _apply_widths(vs, cfg)
    _apply_format(vs, cfg)
    return status, missing


def run_all(todo, cfg, progress=None, samples=None):
    """todo: [(sheet, mark)]。整批放进一个事务组；每张图纸单独一个事务，失败的只回滚这一张。
    返回 (统计 dict, 失败 [(图号, 原因)], 缺字段集合, 是否取消)。"""
    stat = {'new': 0, 'placed': 0, 'rebuilt': 0, 'skip': 0}
    fails = []
    miss_all = set()
    cancelled = False
    existing = existing_schedules(cfg)
    tg = DB.TransactionGroup(doc, '井规格表')
    tg.Start()
    try:
        for i, (sh, mk) in enumerate(todo):
            if progress is not None and progress(i, len(todo)):
                cancelled = True
                break
            t = DB.Transaction(doc, '井规格表 ' + mk)
            t.Start()
            try:
                st, miss = build_schedule(sh, mk, cfg, existing, (samples or {}).get(mk))
                t.Commit()
                stat[st] = stat.get(st, 0) + 1
                for m in miss:
                    miss_all.add(m)
            except Exception as ex:
                try:
                    t.RollBack()
                except Exception:
                    pass
                existing = existing_schedules(cfg)
                fails.append((sh.SheetNumber, '{}'.format(ex).split('\n')[0]))
        tg.Assimilate()
    except Exception:
        try:
            tg.RollBack()
        except Exception:
            pass
        raise
    return stat, fails, miss_all, cancelled


# ============================================================
# 入口
# ============================================================

def main():
    try:
        cfg = load_cfg()
        active = doc.ActiveView
        cur = None
        if isinstance(active, DB.ViewSheet):
            mk = _mark_of_sheet(active, cfg)
            if mk:
                cur = (active, mk)
        sheets = collect_sheets(cfg)
        set_label = '⚙ 设置参数…'
        while True:
            opts = []
            if cur is not None:
                opts.append('只做当前图纸「{}」'.format(cur[0].Name))
            opts.append('全部井大样图纸（共 {} 张）'.format(len(sheets)))
            opts.append(set_label)
            pick = forms.CommandSwitchWindow.show(opts, message='给哪些图纸放井规格表？')
            if pick == set_label:
                if open_settings():
                    cfg = load_cfg()
                    sheets = collect_sheets(cfg)
                continue
            break
        if not pick:
            return
        todo = [cur] if (cur is not None and pick.startswith('只做当前')) else sheets
        if not todo:
            forms.alert('没有找到井大样图纸（图名需包含「{}」，且图纸上有带 {} 的视图）。'.format(cfg.get('sheet_filter') or '', cfg.get('view_tag')), title=TITLE)
            return

        # 防呆：先统计，再确认
        have = existing_schedules(cfg)
        marks = model_marks(cfg)
        miss_well = [t for t in todo if t[1] not in marks]
        todo = [t for t in todo if t[1] in marks]
        done_already = 0
        for sh, mk in todo:
            vs = have.get((cfg.get('name_prefix') or '') + mk)
            if vs is not None and any(int(i.ScheduleId.Value) == int(vs.Id.Value) for i in placed_instances(sh)):
                done_already += 1
        n_new = len(todo) if cfg.get('rebuild') else len(todo) - done_already
        txt = '将处理 {} 张图纸。\n其中已有规格表 {} 张（{}），需要新做 {} 张。\n字段：{}\n位置：左上角 ({:g}, {:g}) mm。'.format(
            len(todo), done_already, '会删掉重做' if cfg.get('rebuild') else '跳过', n_new, '、'.join(cfg['fields']), cfg.get('x_mm', 30), cfg.get('y_mm', 565))
        if miss_well:
            txt += '\n模型里找不到井的图纸 {} 张（跳过），例如 {}。'.format(len(miss_well), '、'.join(t[1] for t in miss_well[:4]))
        txt += '\n\n确定开始吗？进度条可以随时取消，已做的保留。'
        if not todo or n_new <= 0:
            forms.alert(txt.split('\n\n')[0] + '\n\n没有需要做的。想改字段或位置后重做，请在设置里勾选「已有规格表的图纸：删掉重做」。', title=TITLE)
            return
        ans = WF.MessageBox.Show(txt, TITLE, WF.MessageBoxButtons.OKCancel, WF.MessageBoxIcon.Question)
        if ans != WF.DialogResult.OK:
            return
        try:
            ensure_group_param(cfg)
        except Exception as ex_g:
            forms.alert('新建分组参数失败，规格表照做，但不会收进文件夹：\n{}'.format(ex_g), title=TITLE)
            cfg = dict(cfg)
            cfg['group_param'] = ''
        pb = forms.ProgressBar(title='正在放井规格表…（点取消可随时停止，已做的保留）', cancellable=True)

        def prog(i, n):
            try:
                pb.update_progress(i, n)
            except Exception:
                pass
            return bool(pb.cancelled)
        try:
            stat, fails, miss_f, cancelled = run_all(todo, cfg, prog, marks)
        finally:
            try:
                pb.close()
            except Exception:
                pass
        msg = ('已取消：' if cancelled else '完成：') + '新做 {}，补放 {}，重做 {}，跳过 {}。'.format(stat.get('new', 0), stat.get('placed', 0), stat.get('rebuilt', 0), stat.get('skip', 0))
        if miss_f:
            msg += '\n模型里没有这些字段（已忽略）：{}。'.format('、'.join(sorted(miss_f)))
        if fails:
            msg += '\n{} 张失败（已还原）：{}'.format(len(fails), '；'.join('{} {}'.format(a, b[:40]) for a, b in fails[:5]))
        if cfg.get('group_param'):
            try:
                any_vs = list(existing_schedules(cfg).values())
                if any_vs and not folder_ready(cfg, any_vs[0]):
                    msg += ('\n\n还差一步才会收进文件夹（只做一次）：项目浏览器里右键「明细表/数量」→「浏览器组织」→「新建」→ 起个名字 → '
                            '「成组条件」选「{}」→ 确定 → 选中刚建的组织 → 确定。之后规格表都按「{}…」收进文件夹，点开才展开。').format(cfg.get('group_param'), cfg.get('folder_name'))
            except Exception:
                pass
        msg += '\n不满意可以 Ctrl+Z 撤销。'
        forms.alert(msg, title=TITLE)
    except Exception as ex:
        forms.alert('发生错误：\n{}\n\n{}'.format(ex, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
