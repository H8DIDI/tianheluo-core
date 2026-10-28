# -*- coding: utf-8 -*-
__title__ = '井表核对'
__doc__ = ('把设计方/厂家给的井清单（Excel，如「预制井材料计划表」：每行一口井，有井号和「1770(L)x1120(W)x3500(D)」这样的规格）和模型逐口核对：\n'
           '清单里有、模型里没有的井；模型里有、清单里没有的井（只看清单涉及的系列）；井的长宽和类型名对不上；深度差得多。\n'
           '清单格式不限：只要同一行里能找到井号（如 E-008）和规格（长×宽，可带深度）就行，多个工作表都会读。只读，不改模型；结果可导出 Excel。')

import re
import time
from pyrevit import revit, DB, UI, forms

# @include _shared/well_config.py
# @include _shared/xlsx_lite.py
# @include _shared/xlsx_read.py
import io
import os
import json
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
import System.Windows.Forms as WF

doc = revit.doc
uidoc = revit.uidoc
WCFG = wcfg_load()
TITLE = u'井表核对'
FT = 304.8
TOL_SIZE = 20.0          # 长、宽差在这个范围内算一致（mm）
TOL_DEPTH = 250.0        # 深度差在这个范围内算一致（mm）：预制井深度常按 500 一档取整

_MARK = re.compile(u'([A-Za-z]{1,3}-\\d+(?:-\\d+)?)')
_SPEC = re.compile(u'(\\d{3,5})\\s*(?:\\(\\s*L\\s*\\))?\\s*[xX×*]\\s*(\\d{3,5})\\s*(?:\\(\\s*W\\s*\\))?(?:\\s*[xX×*]\\s*(\\d{3,5})\\s*(?:\\(\\s*D\\s*\\))?)?')
_SPEC_STRICT = re.compile(u'(\\d{3,5})\\s*\\(\\s*L\\s*\\)\\s*[xX×*]\\s*(\\d{3,5})\\s*\\(\\s*W\\s*\\)(?:\\s*[xX×*]\\s*(\\d{3,5})\\s*\\(\\s*D\\s*\\))?')


def norm(mk):
    """井号的数字部分去掉前导 0：清单里写 A-005，模型里是 A-5，算同一口井。"""
    return re.sub(u'-0+(\\d)', u'-\\1', mk or u'')


def parse_list(path):
    """从 Excel 里取出 {井号: (长, 宽, 深或None, 工作表, 行号)}。同一行里有井号和规格才算。"""
    out = {}
    dup = []
    for sname, rows in xlsx_read(path):
        for ri, row in enumerate(rows):
            mark = None
            spec = None
            for c in row:
                c = c or u''
                if mark is None and (u'编号' in c or _MARK.fullmatch(c.strip() or u'x')):
                    m = _MARK.search(c)
                    if m:
                        mark = m.group(1)
                if spec is None:
                    m = _SPEC_STRICT.search(c)
                    if m:
                        spec = (float(m.group(1)), float(m.group(2)), float(m.group(3)) if m.group(3) else None)
            if mark and spec:
                mark = norm(mark)
                if mark in out:
                    dup.append(mark)
                out[mark] = (spec[0], spec[1], spec[2], sname, ri + 1)
    return out, sorted(set(dup))


def model_wells():
    """模型里的井：{井号: 构件}，井 = 常规模型里「标记」属于井号系列的构件。"""
    out = {}
    for e in DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType():
        try:
            p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
            mk = (p.AsString() or u'').strip() if p is not None else u''
        except Exception:
            mk = u''
        if mk and wcfg_series_of(mk, WCFG, True) is not None and norm(mk) not in out:
            out[norm(mk)] = e
    return out


def well_size(e):
    """类型名里的 长×宽（mm）。取不到返回 None。"""
    try:
        nm = e.Symbol.Name
    except Exception:
        return None
    m = _SPEC_STRICT.search(nm) or _SPEC.search(nm)
    return (float(m.group(1)), float(m.group(2))) if m else None


def well_depth(e):
    p = e.LookupParameter(u'井内深度')
    if p is not None and p.HasValue and p.StorageType == DB.StorageType.Double and p.AsDouble() > 0:
        return p.AsDouble() * FT
    b = e.LookupParameter(u'井底标高')
    o = e.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
    if b is not None and b.HasValue and o is not None and o.HasValue:
        return (o.AsDouble() - b.AsDouble()) * FT
    return None


def compare(table, wells):
    rows = []
    prefixes = set()
    for mk in table:
        sr = wcfg_series_of(mk, WCFG, True)
        if sr is not None:
            prefixes.add(sr[u'prefix'])
    for mk in sorted(table, key=lambda m: wcfg_well_sort_key(m, WCFG)):
        L, W, D, sn, rn = table[mk]
        e = wells.get(mk)
        where = u'%s 第 %d 行' % (sn, rn)
        if e is None:
            rows.append([u'清单有、模型没有', mk, u'', u'规格 %d×%d%s' % (L, W, u'×%d' % D if D else u''), where, None])
            continue
        sz = well_size(e)
        if sz is not None:
            same = (abs(sz[0] - L) <= TOL_SIZE and abs(sz[1] - W) <= TOL_SIZE) or (abs(sz[0] - W) <= TOL_SIZE and abs(sz[1] - L) <= TOL_SIZE)
            if not same:
                rows.append([u'长宽对不上', mk, e.Symbol.Name, u'清单 %d×%d，模型类型 %d×%d' % (L, W, sz[0], sz[1]), where, e.Id])
        else:
            rows.append([u'类型名里没有长宽', mk, e.Symbol.Name, u'清单 %d×%d，没法核对尺寸' % (L, W), where, e.Id])
        if D:
            md = well_depth(e)
            if md is None:
                rows.append([u'模型里没有深度', mk, e.Symbol.Name, u'清单深度 %d' % D, where, e.Id])
            elif abs(md - D) > TOL_DEPTH:
                rows.append([u'深度差得多', mk, e.Symbol.Name, u'清单 %d，模型 %.0f（差 %.0f mm）' % (D, md, md - D), where, e.Id])
    for mk, e in sorted(wells.items(), key=lambda kv: wcfg_well_sort_key(kv[0], WCFG)):
        sr = wcfg_series_of(mk, WCFG, True)
        if mk not in table and sr is not None and sr[u'prefix'] in prefixes:
            rows.append([u'模型有、清单没有', mk, e.Symbol.Name, u'', u'', e.Id])
    return rows


def main():
    try:
        dlg = WF.OpenFileDialog()
        dlg.Title = TITLE + u' · 选择井清单（Excel）'
        dlg.Filter = u'Excel 表格 (*.xlsx)|*.xlsx'
        if dlg.ShowDialog() != WF.DialogResult.OK:
            return
        table, dup = parse_list(dlg.FileName)
        if not table:
            forms.alert(u'这个 Excel 里没有找到「井号 + 规格」同在一行的数据。\n\n需要：某个格子里有井号（如 E-008 或「编号:E-008」），同一行另一个格子里有规格（如 JC9C-1770(L)x1120(W)x3500(D)）。', title=TITLE)
            return
        wells = model_wells()
        rows = compare(table, wells)
        head = u'清单：%d 口井（%s）\n模型：%d 口井\n' % (len(table), os.path.basename(dlg.FileName), len(wells))
        if dup:
            head += u'清单里井号重复：%s\n' % u'、'.join(dup[:10])
        cnt = {}
        for r in rows:
            cnt[r[0]] = cnt.get(r[0], 0) + 1
        if not rows:
            forms.alert(head + u'\n逐口核对：长宽、深度都对得上，清单和模型的井一一对应。', title=TITLE)
            return
        head += u'\n发现 %d 处不一致：\n' % len(rows) + u''.join(u'  %s：%d\n' % (k, v) for k, v in sorted(cnt.items(), key=lambda kv: -kv[1]))
        head += u'\n前几条：\n' + u'\n'.join(u'  %s %s：%s' % (r[0], r[1], r[3]) for r in rows[:8])
        head += u'\n\n导出完整清单到 Excel？'
        if forms.alert(head, title=TITLE, ok=False, yes=True, no=True):
            sd = WF.SaveFileDialog()
            sd.Title = TITLE
            sd.Filter = u'Excel 表格 (*.xlsx)|*.xlsx'
            sd.FileName = u'井表核对_%s.xlsx' % time.strftime(u'%Y%m%d_%H%M')
            if sd.ShowDialog() == WF.DialogResult.OK:
                data = [[u'问题', u'井号', u'模型类型', u'说明', u'清单位置', u'构件ID']] + \
                       [[r[0], r[1], r[2], r[3], r[4], r[5].Value if r[5] is not None else u''] for r in rows]
                xlsx_write(sd.FileName, [(u'核对结果', data, {'widths': [18, 12, 34, 56, 20, 12], 'header_rows': 1})])
                try:
                    System.Diagnostics.Process.Start(u'explorer.exe', u'/select,"%s"' % sd.FileName)
                except Exception:
                    pass
        ids = [r[5] for r in rows if r[5] is not None]
        if ids and forms.alert(u'要在模型里选中这 %d 口有出入的井吗？' % len(ids), title=TITLE, ok=False, yes=True, no=True):
            from System.Collections.Generic import List
            uidoc.Selection.SetElementIds(List[DB.ElementId](ids))
    except Exception:
        forms.alert(u'出错了：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
