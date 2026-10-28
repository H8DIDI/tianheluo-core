# -*- coding: utf-8 -*-
__title__ = '井大样体检'
__doc__ = ('出图前一键检查井和井大样图纸，列出问题清单（可导出 Excel、可在模型里选中有问题的井）：\n'
           '井没有图纸、图纸找不到井、井号重复、一口井多张图纸；平面/南北西东视图不全、视图框没围着井（井挪了/转了）、视图里没有尺寸；'
           '井盖外沿到井内壁一个方向上没有一侧是 300 mm（放爬梯留的）；井底标高、井内深度和偏移对不上、井深超出范围。\n'
           '只读，不改模型。')

import time
import math
from pyrevit import revit, DB, UI, forms

# @include _shared/form_fit.py
# @include _shared/well_config.py
# @include _shared/settings_ui.py
# @include _shared/well_config_ui.py
# @include _shared/xlsx_lite.py
WCFG = wcfg_load()
WS_PREFIX = WCFG[u"sheet_name_prefix"]
# @include _shared/annotate_core.py
# @include _shared/well_geo.py

import System.Windows.Forms as WF

TITLE = u'井大样体检'
DEPTH_FILE = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments),
                          u'KaifaTools', u'井深规则.json')
COVER_MM = 300.0         # 井盖外沿到内壁：每个方向至少一侧是 300（爬梯位）
COVER_TOL = 3.0
KINDS = [(u'P', u'平面'), (u'S', u'南'), (u'N', u'北'), (u'W', u'西'), (u'E', u'东')]


def _depth_range():
    lo, hi = 500.0, 6000.0
    try:
        with io.open(DEPTH_FILE, 'r', encoding='utf-8-sig') as f:
            d = json.load(f)
        lo = float(d.get('MinDepthMm', d.get('minDepthMm', lo)))
        hi = float(d.get('MaxDepthMm', d.get('maxDepthMm', hi)))
    except Exception:
        pass
    return lo, hi


def _len_mm(e, name):
    p = e.LookupParameter(name)
    if p is None or not p.HasValue or p.StorageType != DB.StorageType.Double:
        return None
    return p.AsDouble() * FT


def _offset_mm(e):
    p = e.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
    return p.AsDouble() * FT if (p is not None and p.HasValue) else None


def audit(full, pb):
    """返回 (问题行, 统计)。问题行 = [级别, 类别, 井号, 图纸号, 问题, 建议, 构件Id或None]。"""
    rows = []
    stats = {}

    def add(level, cat, mark, sheet, what, tip, eid=None):
        rows.append([level, cat, mark or u'', sheet or u'', what, tip, eid])

    wells_all = list(DB.FilteredElementCollector(doc).OfCategory(_WELL_CAT).WhereElementIsNotElementType().ToElements())
    wells = {}
    by_mark = {}
    for e in wells_all:
        mk = _mark_of(e)
        if not mk or wcfg_series_of(mk, WCFG, True) is None:
            continue                     # 没标记、或不是配置里的井号系列（雨水井、别的常规模型）不算
        by_mark.setdefault(mk, []).append(e)
    for mk, es in by_mark.items():
        wells[mk] = es[0]
        if len(es) > 1:
            add(u'错误', u'井号', mk, u'', u'井号重复：%d 个构件都叫 %s' % (len(es), mk), u'改成不同的号，否则图纸对应不上', es[0].Id)
    stats[u'井'] = len(wells)

    _load_sheet_caches()
    sheets = [x for x in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet) if _is_well_sheet(x)]
    sheets.sort(key=lambda x: x.SheetNumber)
    stats[u'图纸'] = len(sheets)
    sheets_of = {}
    for sh in sheets:
        mk = _sheet_mark(sh)
        if mk:
            sheets_of.setdefault(mk, []).append(sh)
        else:
            add(u'错误', u'图纸', u'', sh.SheetNumber, u'认不出这张图纸是哪口井（图名、视图名里都没有井号）', u'检查图名或视图名')
    for mk in sorted(wells, key=lambda m: wcfg_well_sort_key(m, WCFG)):
        if mk not in sheets_of:
            add(u'提醒', u'井没有图纸', mk, u'', u'这口井还没有井大样图纸', u'用「一键井展开图」生成', wells[mk].Id)
    for mk, shs in sheets_of.items():
        if mk not in by_mark:
            for sh in shs:
                add(u'错误', u'图纸无井', mk, sh.SheetNumber, u'模型里找不到井号 %s（井被删了、改号了，或图纸是别的项目留下的）' % mk, u'删图纸，或把井号改回来')
        elif len(shs) > 1:
            add(u'提醒', u'重复图纸', mk, u'、'.join(s.SheetNumber for s in shs), u'一口井有 %d 张图纸' % len(shs), u'留一张，删掉多余的')

    rules = _rules()
    lo_d, hi_d = _depth_range()
    work = [(mk, wells[mk], shs[0]) for mk, shs in sheets_of.items() if mk in wells]
    total = len(wells) + len(work)
    i = 0
    # --- 井自身：深度参数
    for mk, e in wells.items():
        i += 1
        if i % 20 == 0:
            pb.update_progress(i, total)
        bot, inner, off = _len_mm(e, u'井底标高'), _len_mm(e, u'井内深度'), _offset_mm(e)
        if bot is not None and off is not None:
            depth = off - bot
            if inner is not None and abs(depth - inner) > 5:
                add(u'提醒', u'井深', mk, u'', u'偏移 − 井底标高 = %.0f，但「井内深度」是 %.0f（差 %.0f mm）' % (depth, inner, abs(depth - inner)),
                    u'运行「井深调整」或「井参数同步」核对', e.Id)
            if depth < lo_d or depth > hi_d:
                add(u'提醒', u'井深', mk, u'', u'井深 %.0f mm 不在 %.0f ~ %.0f 范围内' % (depth, lo_d, hi_d), u'确认排管标高和井底', e.Id)
    # --- 图纸与视图
    for mk, well, sh in work:
        i += 1
        pb.update_progress(i, total)
        if pb.cancelled:
            break
        vs = {}
        for _k, nm, v in _views_of(sh):
            k = _kind(nm)
            if k:
                vs[k] = v
        miss = [lab for k, lab in KINDS if k not in vs]
        if miss:
            add(u'错误', u'视图不全', mk, sh.SheetNumber, u'缺少视图：%s' % u'、'.join(miss), u'重新生成这口井的图', well.Id)
        row = scan([sh], {mk: well}, rules)[0] if 'P' in vs else None
        if row is not None and row['status'] == 'changed':
            add(u'错误', u'视图没对着井', mk, sh.SheetNumber, u'井挪了或转了：%s' % _describe(row), u'运行「井大样变动同步」', well.Id)
        for k, lab in KINDS:
            if k in ('P', 'S') and k in vs and _ndims_view(vs[k]) == 0:       # 东西立面本来就只标排管链，不查
                add(u'提醒', u'没有尺寸', mk, sh.SheetNumber, u'%s视图里没有尺寸标注' % lab, u'运行「一键井大样标注」', well.Id)
        if full and 'P' in vs and (row is None or row['status'] == 'ok'):
            try:
                _FACE_CACHE.clear(); _VFACE_CACHE.clear()
                el = _main_well(vs['P'], collect_target_elems(vs['P'], None, True), mk)
                g = cover_gaps(vs['P'], el) if el else None
            except Exception:
                g = None
            if g == 'nocover':
                add(u'提醒', u'井盖', mk, sh.SheetNumber, u'没有识别到井盖层，井盖距内壁没法检查', u'')
            elif g:
                for name, a, b in ((u'横向', g[0], g[1]), (u'纵向', g[2], g[3])):
                    ok = any(x is not None and abs(x - COVER_MM) <= COVER_TOL for x in (a, b))
                    if not ok and (a is not None or b is not None):
                        add(u'提醒', u'井盖距内壁', mk, sh.SheetNumber,
                            u'%s两侧井盖距内壁 %s / %s mm，没有一侧是 300（爬梯位）' % (name, a, b), u'修改族，或确认这口井不放爬梯', well.Id)
    return rows, stats


def _ndims_view(v):
    return len(list(DB.FilteredElementCollector(doc, v.Id).OfClass(DB.Dimension)))


def main():
    if not wcfg_first_run(TITLE):
        return
    try:
        _apply_settings()
        pick = forms.CommandSwitchWindow.show([u'快速体检（不含井盖距内壁）', u'完整体检（含井盖距内壁，几百口井要几分钟）'], message=TITLE)
        if not pick:
            return
        full = pick.startswith(u'完整')
        t0 = time.time()
        pb = forms.ProgressBar(title=u'正在体检… ({value}/{max_value})', cancellable=True)
        try:
            rows, stats = audit(full, pb)
        finally:
            pb.close()
        order = {u'错误': 0, u'提醒': 1}
        rows.sort(key=lambda r: (order.get(r[0], 9), r[1], wcfg_well_sort_key(r[2], WCFG) if r[2] else (9,)))
        cnt = {}
        for r in rows:
            cnt[(r[0], r[1])] = cnt.get((r[0], r[1]), 0) + 1
        head = u'检查了 %d 口井、%d 张井大样图纸，用时 %d 秒。\n' % (stats.get(u'井', 0), stats.get(u'图纸', 0), time.time() - t0)
        if not rows:
            forms.alert(head + u'\n没有发现问题。', title=TITLE)
            return
        n_err = len([r for r in rows if r[0] == u'错误'])
        head += u'\n发现 %d 个问题（错误 %d，提醒 %d）：\n' % (len(rows), n_err, len(rows) - n_err)
        for (lv, cat), n in sorted(cnt.items(), key=lambda kv: (order.get(kv[0][0], 9), -kv[1])):
            head += u'  [%s] %s：%d\n' % (lv, cat, n)
        sample = u'\n'.join(u'  %s %s %s：%s' % (r[1], r[2], (u'图' + r[3]) if r[3] else u'', r[4]) for r in rows[:8])
        head += u'\n前几条：\n' + sample + u'\n\n导出完整清单到 Excel？'
        if forms.alert(head, title=TITLE, ok=False, yes=True, no=True):
            dlg = WF.SaveFileDialog()
            dlg.Title = TITLE
            dlg.Filter = u'Excel 表格 (*.xlsx)|*.xlsx'
            dlg.FileName = u'井大样体检_%s.xlsx' % time.strftime('%Y%m%d_%H%M')
            if dlg.ShowDialog() == WF.DialogResult.OK:
                data = [[u'级别', u'类别', u'井号', u'图纸号', u'问题', u'建议', u'构件ID']] + \
                       [[r[0], r[1], r[2], r[3], r[4], r[5], r[6].Value if r[6] is not None else u''] for r in rows]
                xlsx_write(dlg.FileName, [(u'问题清单', data, {'widths': [8, 14, 12, 12, 70, 36, 12], 'header_rows': 1})])
                try:
                    System.Diagnostics.Process.Start(u'explorer.exe', u'/select,"%s"' % dlg.FileName)
                except Exception:
                    pass
        ids = [r[6] for r in rows if r[6] is not None]
        if ids and forms.alert(u'要在模型里选中这 %d 口有问题的井吗？（方便框选后隔离、查看）' % len(set(i.Value for i in ids)), title=TITLE, ok=False, yes=True, no=True):
            from System.Collections.Generic import List
            uidoc.Selection.SetElementIds(List[DB.ElementId](list(dict((i.Value, i) for i in ids).values())))
    except Exception:
        forms.alert(u'出错了：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
