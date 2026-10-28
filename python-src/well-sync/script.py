# -*- coding: utf-8 -*-
__title__ = '井大样变动同步'
__doc__ = ('井挪了位置、转了方向之后，井大样图纸里的视图和尺寸就对不上了。本工具逐张检查井大样图纸：\n'
           '① 平面视图的裁剪框是不是还围着这口井（位置、大小）；② 把变动的井的平面裁剪框、南北西东四个剖面（剖切线和裁剪范围）重新对到井上；'
           '③ 删掉这些视图里已经错位的旧尺寸，按新位置重新标注（井盖到内壁、排管尺寸链、总尺寸）。\n'
           '先列出哪些井变了、变了多少，你确认才改；也可以「只检查不改」。没变的井、没变的图纸一概不动。整次可 Ctrl+Z 撤销。')

from pyrevit import revit, DB, UI, forms

# @include _shared/form_fit.py
# @include _shared/well_config.py
# @include _shared/settings_ui.py
# @include _shared/well_config_ui.py
WCFG = wcfg_load()
WS_PREFIX = WCFG[u"sheet_name_prefix"]
# @include _shared/annotate_core.py

# @include _shared/well_geo.py

TITLE = u'井大样变动同步'


class _Silent(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        for f in fa.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                fa.DeleteWarning(f)
        return DB.FailureProcessingResult.Continue


def step(name, work):
    t = DB.Transaction(doc, name)
    fho = t.GetFailureHandlingOptions()
    fho.SetFailuresPreprocessor(_Silent())
    fho.SetClearAfterRollback(True)
    t.SetFailureHandlingOptions(fho)
    t.Start()
    try:
        work()
        return t.Commit() == DB.TransactionStatus.Committed
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        return False


def _viewer_of(v):
    for i in v.GetDependentElements(None):
        e = doc.GetElement(i)
        if e is not None and e.Category is not None and e.Category.Id.Value == int(DB.BuiltInCategory.OST_Viewers):
            return i
    return None


def reframe(row, rules):
    """把平面裁剪框和四个剖面对回井的现在位置（做法同井展开图：平面 = 外轮廓 + 余量；
    剖切线在井外墙外 face_out，向井内 face_depth）。"""
    cx, cy, dx, dy = row['fp']
    vs = row['views']
    m = rules[u'crop_margin_mm'] / FT
    fo = rules[u'face_out_mm'] / FT
    depth = rules[u'face_depth_mm'] / FT
    px0, px1, py0, py1 = cx - dx - m, cx + dx + m, cy - dy - m, cy + dy + m
    # 平面：只改 XY，Z 范围不动
    cb = vs['P'].CropBox
    nb = DB.BoundingBoxXYZ()
    nb.Transform = cb.Transform
    nb.Min = DB.XYZ(px0, py0, cb.Min.Z)
    nb.Max = DB.XYZ(px1, py1, cb.Max.Z)
    vs['P'].CropBox = nb
    # 剖面：先挪剖切线（只有挪 Viewer 元素才能真正移动剖切面），再设局部裁剪范围
    plane = {'S': cy - dy - fo, 'N': cy + dy + fo, 'W': cx - dx - fo, 'E': cx + dx + fo}
    tgt = {'S': (px0, px1, plane['S'], plane['S'] + depth), 'N': (px0, px1, plane['N'] - depth, plane['N']),
           'W': (plane['W'], plane['W'] + depth, py0, py1), 'E': (plane['E'] - depth, plane['E'], py0, py1)}
    keys = [k for k in ('S', 'N', 'W', 'E') if k in vs]
    for k in keys:
        O = vs[k].CropBox.Transform.Origin
        if k in ('S', 'N'):
            vec = DB.XYZ(cx - O.X, plane[k] - O.Y, 0)
        else:
            vec = DB.XYZ(plane[k] - O.X, cy - O.Y, 0)
        vid = _viewer_of(vs[k])
        if vid is not None:
            DB.ElementTransformUtils.MoveElement(doc, vid, vec)
    doc.Regenerate()
    for k in keys:
        v = vs[k]
        cb = v.CropBox
        tf = cb.Transform
        x0, x1, y0, y1 = tgt[k]
        xs = [tf.Inverse.OfPoint(DB.XYZ(x, y, tf.Origin.Z)).X for x in (x0, x1) for y in (y0, y1)]
        zs = [DB.XYZ(x - tf.Origin.X, y - tf.Origin.Y, 0).DotProduct(tf.BasisZ) for x in (x0, x1) for y in (y0, y1)]
        nb = DB.BoundingBoxXYZ()
        nb.Transform = tf
        nb.Min = DB.XYZ(min(xs), cb.Min.Y, min(zs))
        nb.Max = DB.XYZ(max(xs), cb.Max.Y, max(zs))
        v.CropBox = nb
    doc.Regenerate()


def _ndims(v):
    return len(list(DB.FilteredElementCollector(doc, v.Id).OfClass(DB.Dimension)))


def annotate_view(v, mark, stats, failed):
    """标一个视图（和一键井大样标注同一套逻辑）。返回 'ok' / 'rot' / 'nowell' / 'nobox'。"""
    del _DIM_ERRS[:]
    _OUT_CROP.clear()
    _FACE_CACHE.clear(); _VFACE_CACHE.clear()
    elems = _main_well(v, collect_target_elems(v, stats), mark)
    if not elems:
        elems = _main_well(v, collect_target_elems(v, None, True), mark)
    if not elems:
        return 'nowell'
    plan = _frame(v)['plan']
    if not _axes_ok(elems, _frame(v)):
        led = _fresh_ledger(v)
        ok = step(u'旋转井标注', lambda: make_rotated_dims(v, elems, led, stats))
        if not ok:
            _ROT_FORCE_TRACE[0] = True
            try:
                led = _fresh_ledger(v)
                ok = step(u'旋转井标注(引出线)', lambda: make_rotated_dims(v, elems, led, stats))
            finally:
                _ROT_FORCE_TRACE[0] = False
        if not ok:
            failed.append(u'%s 旋转井标注失败' % v.Name)
        if _COVER_DIMS and plan:
            if not step(u'旋转井盖尺寸', lambda: make_cover_dims_rot(v, elems, led, stats, _COVER_DIMS_V)):
                failed.append(u'%s 旋转井井盖尺寸失败' % v.Name)
        return 'rot'
    box = _clip_box(union_box(v, elems), crop_world_range(v))
    if box is not None:
        box = _face_box(elems, box)
    if box is None:
        return 'nobox'
    led = _fresh_ledger(v)
    ext = {}

    def both():
        make_chain_dims(v, elems, box, led, stats, ext)
        make_total_dims(v, elems, box, led, ext)
    if not step(u'标注', both):
        ext.clear()
        led = _fresh_ledger(v)
        if not step(u'排管尺寸链', lambda: make_chain_dims(v, elems, box, led, stats, ext)):
            ext.clear()
            led = _fresh_ledger(v)
            try:
                banks = _find_banks(crop_world_range(v), _frame(v))
            except Exception:
                banks = []
            for bk in banks:
                for dr in ('h', 'v'):
                    if not step(u'排管尺寸链', lambda bk=bk, dr=dr: make_chain_dims(v, elems, box, led, stats, ext, [bk], dr)):
                        failed.append(u'%s 排管 %s %s向失败' % (v.Name, bk.Id, dr))
        if not step(u'总尺寸', lambda: make_total_dims(v, elems, box, led, ext)):
            failed.append(u'%s 总尺寸失败' % v.Name)
    if _COVER_DIMS and plan:
        if not step(u'井盖尺寸', lambda: make_cover_dims(v, elems, box, led, stats, _COVER_DIMS_V)):
            failed.append(u'%s 井盖尺寸失败' % v.Name)
    return 'ok'


def _save_cache(row):
    try:
        cache = wcfg_cache_load()
        ent = cache.get(row['mark'], {})
        bb = row['well'].get_BoundingBox(None)
        ent['bbox'] = [round(bb.Min.X, 2), round(bb.Min.Y, 2), round(bb.Min.Z, 2),
                       round(bb.Max.X, 2), round(bb.Max.Y, 2), round(bb.Max.Z, 2)]
        ent['sheet_id'] = int(row['sheet'].Id.Value)
        ent.setdefault('number', row['sheet'].SheetNumber)
        ent.setdefault('name', row['sheet'].Name)
        cache[row['mark']] = ent
        wcfg_cache_save(cache)
    except Exception:
        pass


def main():
    if not wcfg_first_run(TITLE):
        return
    try:
        _apply_settings()
        _load_sheet_caches()
        rules = _rules()
        all_sheets = [x for x in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet) if _is_well_sheet(x)]
        all_sheets.sort(key=lambda x: x.SheetNumber)
        if not all_sheets:
            forms.alert(u'项目里没有井大样图纸（名称以「%s」开头，可在项目配置里改）。' % WS_PREFIX, title=TITLE)
            return
        cur = None
        try:
            if isinstance(doc.ActiveView, DB.ViewSheet) and _is_well_sheet(doc.ActiveView):
                cur = doc.ActiveView
        except Exception:
            pass
        sel_sheets = []
        for eid in uidoc.Selection.GetElementIds():
            e = doc.GetElement(eid)
            if isinstance(e, DB.ViewSheet) and _is_well_sheet(e):
                sel_sheets.append(e)
        opts = []
        if sel_sheets:
            opts.append((u'检查并更新：项目浏览器里选中的 %d 张图纸' % len(sel_sheets), sel_sheets, True))
        if cur is not None:
            opts.append((u'检查并更新：当前图纸「%s」' % cur.SheetNumber, [cur], True))
        opts.append((u'检查并更新：全部井大样图纸（%d 张）' % len(all_sheets), all_sheets, True))
        opts.append((u'只检查不改：全部井大样图纸，列出变了的井', all_sheets, False))
        pick = forms.CommandSwitchWindow.show([o[0] for o in opts], message=u'要同步哪些井大样图纸？')
        if not pick:
            return
        _t, sheets, do_apply = [o for o in opts if o[0] == pick][0]

        wells = _well_map()
        pb = forms.ProgressBar(title=u'正在检查井有没有变动… ({value}/{max_value})', cancellable=True)
        try:
            rows = []
            for i, sh in enumerate(sheets):
                pb.update_progress(i, len(sheets))
                if pb.cancelled:
                    return
                rows += scan([sh], wells, rules)
        finally:
            pb.close()
        changed = [r for r in rows if r['status'] == 'changed']
        nowell = [r for r in rows if r['status'] in ('nowell', 'noplan')]
        head = u'检查了 %d 张图纸：\n• 变动的井 %d 口\n• 没变 %d 张\n' % (len(rows), len(changed), len(rows) - len(changed) - len(nowell))
        if nowell:
            head += u'• 找不到井或平面视图、没法比对 %d 张：%s\n' % (len(nowell), u'、'.join(r['sheet'].SheetNumber for r in nowell[:12]))
        lines = [u'  %s（图 %s）：%s' % (r['mark'], r['sheet'].SheetNumber, _describe(r)) for r in changed]
        body = u'\n'.join(lines[:25]) + (u'\n  …还有 %d 口' % (len(lines) - 25) if len(lines) > 25 else u'')
        if not changed:
            forms.alert(head + u'\n所有井的视图都还对着井，不用同步。', title=TITLE)
            return
        if not do_apply:
            forms.alert(head + u'\n变动的井：\n' + body + u'\n\n只检查，没有改任何东西。', title=TITLE)
            return
        n_dims = 0
        for r in changed:
            for v in r['views'].values():
                n_dims += _ndims(v)
        msg = (head + u'\n变动的井：\n' + body +
               u'\n\n将对这 %d 口井：\n① 把平面裁剪框和四个剖面对回井上；\n② 删除这些视图里已有的 %d 道尺寸（位置已经错了），按新位置重新标注。\n'
               u'其它井、其它图纸不动。整次可 Ctrl+Z 撤销。\n\n继续吗？' % (len(changed), n_dims))
        if not forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
            return

        tg = DB.TransactionGroup(doc, u'井大样变动同步')
        tg.Start()
        done, failed, stats, log = 0, [], {}, []
        cancelled = False
        pb = forms.ProgressBar(title=u'正在同步变动的井… ({value}/{max_value})', cancellable=True)
        try:
            for i, r in enumerate(changed):
                pb.update_progress(i, len(changed))
                if pb.cancelled:
                    cancelled = True
                    break
                if not step(u'对齐视图 %s' % r['mark'], lambda r=r: reframe(r, rules)):
                    failed.append(u'%s 视图对齐失败，已跳过' % r['mark'])
                    continue

                def _del(r=r):
                    for v in r['views'].values():
                        ids = [d.Id for d in DB.FilteredElementCollector(doc, v.Id).OfClass(DB.Dimension)]
                        for did in ids:
                            doc.Delete(did)
                step(u'删除旧尺寸 %s' % r['mark'], _del)
                made = 0
                for v in r['views'].values():
                    try:
                        b0 = _ndims(v)
                        annotate_view(v, r['mark'], stats, failed)
                        made += _ndims(v) - b0
                    except Exception as ex:
                        failed.append(u'%s %s 标注异常：%s' % (r['mark'], v.Name, str(ex).split(u'\n')[0]))
                _save_cache(r)
                log.append(u'%s：新标 %d 道' % (r['mark'], made))
                done += 1
        finally:
            pb.close()
        tg.Assimilate()
        out = u'同步了 %d / %d 口井。\n' % (done, len(changed)) + (u'已取消，剩下的没有处理。\n' if cancelled else u'')
        if failed:
            out += u'\n未完成的部分：\n' + u'\n'.join(u'  - ' + x for x in failed[:15])
        out += u'\n\n提示：井变大或转向后，视图在图纸上占的面积可能变了，必要时运行「井图比例排版」重新排版。'
        forms.alert(out, title=TITLE)
    except Exception:
        forms.alert(u'出错了：\n' + traceback.format_exc(), title=TITLE)


if __name__ == '__main__':
    main()
