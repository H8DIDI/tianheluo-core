# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '井深统计\n报表'
__author__ = 'didi'
__doc__ = ('统计全部井（或选中的井）的井深，导出中英双语 Excel：明细、按系列、按族、按类型（数量、最小、最大、平均、\n'
           '不同取值数、缺值数），另附超过设定深度（默认 4 m）的深井清单，可顺手在模型里选中这些深井（做埋深范围图用）。\n'
           '井深参数名按优先顺序依次找（实例参数优先，其次类型参数），参数名和深井阈值都能改。只读，不改模型。')

import io
import os
import json
import time
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
from Autodesk.Revit import DB
from System.Collections.Generic import List
from pyrevit import revit, forms
import System.Windows.Forms as WF

doc = revit.doc
uidoc = revit.uidoc
TITLE = '井深统计报表'
FT_M = 0.3048
DOC_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), 'KaifaTools')
SETTINGS = os.path.join(DOC_DIR, '井深统计设置.json')
DEFAULTS = {
    '说明': 'params：井深参数名，按顺序找第一个有值的；deep_m：深井阈值（米）；family_exclude：族名含这些字的不统计。',
    'params': ['井内深度', '井底深度', '井深', '总深度', '主体净深度', '深度', '井整体高度', '井底深'],
    'deep_m': 4.0,
    'family_exclude': ['井盖', '爬梯', 'mold_'],
}

# @include _shared/form_fit.py
# @include _shared/xlsx_lite.py
# @include _shared/well_config.py
# @include _shared/settings_ui.py


def load():
    s = dict(DEFAULTS)
    try:
        if os.path.exists(SETTINGS):
            with io.open(SETTINGS, 'r', encoding='utf-8-sig') as f:
                s.update(json.load(f))
        else:
            if not os.path.isdir(DOC_DIR):
                os.makedirs(DOC_DIR)
            with io.open(SETTINGS, 'w', encoding='utf-8') as f:
                f.write(json.dumps(DEFAULTS, ensure_ascii=False, indent=2))
    except Exception:
        pass
    return s


def _num(p):
    """参数值换成米（长度）或数字；取不到返回 None。"""
    if p is None or not p.HasValue:
        return None
    try:
        if p.StorageType == DB.StorageType.Double:
            v = p.AsDouble()
            try:
                if p.Definition.GetDataType() == DB.SpecTypeId.Length:
                    return v * FT_M
            except Exception:
                pass
            return v
        if p.StorageType == DB.StorageType.Integer:
            return float(p.AsInteger())
        if p.StorageType == DB.StorageType.String:
            t = (p.AsString() or '').strip().lower().replace('m', '')
            return float(t) if t else None
    except Exception:
        return None
    return None


def read_depths(e, names):
    t = doc.GetElement(e.GetTypeId())
    vals = {}
    for n in names:
        v = _num(e.LookupParameter(n))
        if v is None and t is not None:
            v = _num(t.LookupParameter(n))
        vals[n] = v
    return vals


def fam_type(e):
    try:
        s = e.Symbol
        return s.FamilyName, s.Name
    except Exception:
        return '', e.Name


def mark(e):
    p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
    return (p.AsString() or '').strip() if (p is not None and p.HasValue) else ''


def stats(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return [0, None, None, None, 0, len(vals)]
    return [len(v), round(min(v), 3), round(max(v), 3), round(sum(v) / len(v), 3),
            len(set(round(x, 3) for x in v)), len(vals) - len(v)]


def open_settings():
    """图形化设置：井深参数名、深井阈值、不统计的族。保存返回 True。"""
    cur = load()
    fields = [
        {'key': 'params', 'label': '井深参数名（按顺序找）', 'kind': 'list',
         'hint': '逗号分隔。每口井按顺序找第一个有值的参数作为井深。'},
        {'key': 'deep_m', 'label': '深井阈值 (m)', 'kind': 'num', 'min': 0, 'max': 100,
         'hint': '井深达到这个值的井在报表里标为深井。'},
        {'key': 'family_exclude', 'label': '不统计的族（族名含）', 'kind': 'list',
         'hint': '逗号分隔。族名含这些字的构件不统计，如井盖、爬梯。'},
    ]
    res = su_fields_dialog(TITLE + ' · 设置', '改完点保存，下次统计生效。设置文件：' + SETTINGS, fields, cur, DEFAULTS,
                           extra_buttons=[('用记事本打开文件', lambda: su_open_file(SETTINGS))], width=680, height=380)
    if res is None:
        return False
    out = dict(DEFAULTS)
    out.update(cur)
    out.update(res)
    if not os.path.isdir(DOC_DIR):
        os.makedirs(DOC_DIR)
    with io.open(SETTINGS, 'w', encoding='utf-8') as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    return True


def main():
    pick = forms.CommandSwitchWindow.show(['生成井深统计报表', '⚙ 设置…'], message=TITLE)
    if not pick:
        return
    if pick.startswith('⚙'):
        if open_settings():
            forms.alert('设置已保存。再点一次本插件生成报表。', title=TITLE)
        return
    s = load()
    names = [n for n in s['params'] if n]
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    if sel:
        elems = [e for e in sel if isinstance(e, DB.FamilyInstance)]
        scope = '选中的 %d 个构件' % len(elems)
    else:
        elems = list(DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_GenericModel)
                     .WhereElementIsNotElementType().OfClass(DB.FamilyInstance))
        scope = '整个项目的常规模型'
    cfg = wcfg_load(write_default=False)
    rows = []
    for e in elems:
        fam, typ = fam_type(e)
        if any(k and k in fam for k in s['family_exclude']):
            continue
        vals = read_depths(e, names)
        used = next((n for n in names if vals[n] is not None), None)
        if used is None:
            continue
        mk = mark(e)
        sr = wcfg_series_of(mk, cfg, True)
        series = sr['label'] if sr else '（未识别系列）'
        rows.append({'e': e, 'fam': fam, 'typ': typ, 'mark': mk, 'series': series, 'vals': vals,
                     'used': used, 'depth': vals[used],
                     'group': e.GroupId != DB.ElementId.InvalidElementId})
    if not rows:
        forms.alert('%s里没有找到带井深参数的构件。\n\n找的参数（按顺序）：%s\n可在 %s 里改。' % (scope, '、'.join(names), SETTINGS), title=TITLE)
        return
    rows.sort(key=lambda r: wcfg_well_sort_key(r['mark'], cfg))
    deep = float(s.get('deep_m', 4.0))
    head_detail = ['ElementId 元素ID', '系列 Series', '族 Family', '类型 Type', '标记 Mark'] + \
                  ['%s (m)' % n for n in names] + ['采用井深 Adopted Depth (m)', '采用参数 Source Param', '在组内 In Group']
    detail = [[('井深统计 Well Depth Report — %s — %s — %s' % (doc.Title, scope, time.strftime('%Y-%m-%d %H:%M')))], head_detail]
    for r in rows:
        detail.append([r['e'].Id.Value, r['series'], r['fam'], r['typ'], r['mark']] +
                      [None if r['vals'][n] is None else round(r['vals'][n], 3) for n in names] +
                      [round(r['depth'], 3), r['used'], '是 Yes' if r['group'] else ''])
    st_head = ['数量 Count', '最小 Min (m)', '最大 Max (m)', '平均 Avg (m)', '不同取值数 Distinct', '缺值 Missing']

    def grouped(keyf, key_head):
        g = {}
        order = []
        for r in rows:
            k = keyf(r)
            if k not in g:
                g[k] = []
                order.append(k)
            g[k].append(r['depth'])
        out = [key_head + st_head]
        for k in order:
            out.append(list(k) + stats(g[k]))
        return out

    by_series = grouped(lambda r: (r['series'],), ['系列 Series'])
    by_family = grouped(lambda r: (r['series'], r['fam']), ['系列 Series', '族 Family'])
    by_type = grouped(lambda r: (r['series'], r['fam'], r['typ']), ['系列 Series', '族 Family', '类型 Type'])
    deep_rows = [r for r in rows if r['depth'] >= deep]
    deep_sheet = [['ElementId 元素ID', '系列 Series', '标记 Mark', '族 Family', '类型 Type', '采用井深 Depth (m)']]
    for r in sorted(deep_rows, key=lambda r: -r['depth']):
        deep_sheet.append([r['e'].Id.Value, r['series'], r['mark'], r['fam'], r['typ'], round(r['depth'], 3)])

    dlg = WF.SaveFileDialog()
    dlg.Title = TITLE
    dlg.Filter = 'Excel 表格 (*.xlsx)|*.xlsx'
    dlg.FileName = '井深统计_%s.xlsx' % time.strftime('%Y%m%d_%H%M')
    try:
        if not os.path.isdir(DOC_DIR):
            os.makedirs(DOC_DIR)
        dlg.InitialDirectory = DOC_DIR
    except Exception:
        pass
    if dlg.ShowDialog() != WF.DialogResult.OK:
        return
    path = dlg.FileName
    xlsx_write(path, [
        ('明细 Detail', detail, {'title_rows': 1, 'header_rows': 1, 'widths': [12, 18, 26, 30, 12] + [12] * len(names) + [14, 14, 10]}),
        ('按系列 By Series', by_series, {'widths': [24] + [12] * 6}),
        ('按族 By Family', by_family, {'widths': [24, 30] + [12] * 6}),
        ('按类型 By Type', by_type, {'widths': [24, 30, 30] + [12] * 6}),
        ('深井 ≥%gm' % deep, deep_sheet, {'widths': [12, 24, 12, 26, 30, 14]}),
    ])
    msg = '已导出 %d 口井（%s）。\n深度 ≥ %g m 的井：%d 口。\n\n%s' % (len(rows), scope, deep, len(deep_rows), path)
    msg += '\n\n要不要现在在模型里选中这些深井？（做埋深范围图、隔离显示用）'
    if deep_rows and forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
        uidoc.Selection.SetElementIds(List[DB.ElementId]([r['e'].Id for r in deep_rows]))
    elif not deep_rows:
        forms.alert(msg.split('\n\n要不要')[0], title=TITLE)
    try:
        System.Diagnostics.Process.Start('explorer.exe', '/select,"%s"' % path)
    except Exception:
        pass


if __name__ == '__main__':
    try:
        main()
    except Exception:
        forms.alert('出错了：\n' + traceback.format_exc(), title=TITLE)
