# -*- coding: utf-8 -*-
__title__ = '一键井大样标注'
__doc__ = ('选中井/图纸 → 勾选要标注的视图 → 生成两类尺寸：\n'
           '① 总尺寸：两侧内壁之间的净尺寸，下方标宽、左侧标高（平面、南标宽和高；北只标宽；东、西不标）；\n'
           '② 井内壁至排管尺寸链：识别井内壁，对位于井内的每根排管，下方/上方标水平尺寸链'
           '（内壁—排管边—排管宽—排管边—内壁，一条多段尺寸），左侧/右侧标竖向尺寸链（同理，一条多段尺寸）。\n'
           '排管尺寸链在平面与剖面/立面视图都生成。\n'
           '本工具只新增尺寸，不改视图：不动裁剪框、不隐藏/显示任何构件、不删除视图里已有的尺寸；'
           '同位置已有尺寸的不重复标；超出注释裁剪框的尺寸照常标，报告里列出，需要显示时往外拉。')

from pyrevit import revit, DB, UI, forms

# 图纸名前缀来自项目配置（文档\KaifaTools\井大样配置.json），默认「井大样_」
# @include _shared/form_fit.py
# @include _shared/well_config.py
# @include _shared/settings_ui.py
# @include _shared/well_config_ui.py
WCFG = wcfg_load()
WS_PREFIX = WCFG[u"sheet_name_prefix"]
# @include _shared/annotate_core.py


def main():
    if not wcfg_first_run(u'一键井大样标注'):   # 这台电脑第一次用井大样插件：先确认项目配置
        return
    pb0 = [None]
    try:
        _apply_settings()
        # 读取图纸要几秒到十几秒（图纸多时）：弹进度窗口让你知道它在干活，也可以点取消退出
        try:
            pb0[0] = forms.ProgressBar(title=u'一键井大样标注：正在读取图纸，请稍候…（点取消退出）', cancellable=True)
        except Exception:
            pb0[0] = None
        _load_sheet_caches()
        _FACE_CACHE.clear()
        _EDGE_CACHE.clear()
        _VFACE_CACHE.clear()
        del _BANK_ERRS[:]
        # ---------- 1. 选标注范围（范围列表同「一键导出图纸」：选中的图纸 / 当前图纸 / 各系列 / 各图集 / 全部 / 手动勾选）----------
        selected = []
        try:
            sel = list(uidoc.Selection.GetElementIds())
        except:
            sel = []
        for eid in sel:
            e = doc.GetElement(eid)
            if e is None:
                continue
            sh = _sheet_of(e)
            if sh is not None and _is_well_sheet(sh):
                if sh not in selected:
                    selected.append(sh)
                continue
            mk = _mark_of(e)
            if mk:
                sh2 = _sheet_by_mark(mk)
                if sh2 is not None and sh2 not in selected:
                    selected.append(sh2)
        cur_sheet = None
        try:
            cur = doc.ActiveView
            if isinstance(cur, DB.ViewSheet) and _is_well_sheet(cur):
                cur_sheet = cur
        except:
            pass

        all_sheets = [x for x in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements()
                      if _is_well_sheet(x)]
        all_sheets.sort(key=lambda x: x.Name or "")
        if not all_sheets:
            try:
                pb0[0].close()
            except Exception:
                pass
            forms.alert(u'项目里没有井大样图纸（名称以「{}」开头，可在 {} 里改）。'.format(WS_PREFIX, WCFG_FILE), title='一键井大样标注')
            return

        scope = []   # (显示文字, 图纸列表 / None=手动勾选)
        if selected:
            scope.append((u'项目浏览器里选中的 {} 张图纸'.format(len(selected)), selected))
        if cur_sheet is not None and cur_sheet not in selected:
            scope.append((u'当前图纸「{} {}」'.format(cur_sheet.SheetNumber, cur_sheet.Name), [cur_sheet]))
        for _sr in WCFG[u"series"]:
            _ss = [x for x in all_sheets if wcfg_series_of(_sheet_mark(x) or u'', WCFG, True) is _sr]
            if _ss:
                scope.append((u'井大样 {}（{} 张）'.format(_sr[u"label"], len(_ss)), _ss))
        col_sheets = {}
        col_order = []
        for x in all_sheets:
            try:
                cid_ = x.SheetCollectionId
                ce_ = doc.GetElement(cid_) if cid_ != DB.ElementId.InvalidElementId else None
                cn_ = ce_.Name if ce_ is not None else None
            except:
                cn_ = None
            if cn_:
                if cn_ not in col_sheets:
                    col_sheets[cn_] = []
                    col_order.append(cn_)
                col_sheets[cn_].append(x)
        for c_ in sorted(col_order, key=lambda t: wcfg_natural_key(t)):
            scope.append((u'图集「{}」（{} 张）'.format(c_, len(col_sheets[c_])), col_sheets[c_]))
        scope.append((u'全部井大样图纸（{} 张）'.format(len(all_sheets)), list(all_sheets)))
        scope.append((u'手动勾选图纸…', None))

        try:
            if pb0[0] is not None:
                _cancelled0 = bool(pb0[0].cancelled)
                pb0[0].close()
                pb0[0] = None
                if _cancelled0:
                    return
        except Exception:
            pass
        set_label = u'⚙ 设置参数…'
        # 先问「要标注哪里」：当前图纸排第一，写明「只标注」；其他范围（图集、系列、全部…）在二级列表里，不会误点
        quick = []
        if cur_sheet is not None:
            quick.append((u'只标注当前图纸「{} {}」（{} 个视图）'.format(cur_sheet.SheetNumber, cur_sheet.Name, len(_views_of(cur_sheet))), [cur_sheet]))
        if selected and not (len(selected) == 1 and cur_sheet is not None and selected[0].Id == cur_sheet.Id):
            quick.append((u'项目浏览器里选中的 {} 张图纸'.format(len(selected)), list(selected)))
        more_label = u'选择其他范围（系列、图集、全部、手动勾选）…'
        pick_ = None
        chosen = None
        if quick:
            while True:
                pick_ = forms.CommandSwitchWindow.show([t for t, _l in quick] + [more_label, set_label], message=u'要标注哪里？')
                if pick_ == set_label:
                    open_settings()
                    continue
                break
            if not pick_:
                return
            hit_ = [l for t, l in quick if t == pick_]
            if hit_:
                chosen = hit_
        if chosen is None:
            while True:
                pick_ = forms.CommandSwitchWindow.show([t for t, _l in scope] + [set_label], message=u'选择标注范围')
                if pick_ == set_label:
                    open_settings()
                    continue
                break
            if not pick_:
                return
            chosen = [l for t, l in scope if t == pick_]
        sheets = []
        if chosen and chosen[0] is None:
            by_name = {}
            for x in all_sheets:
                by_name[x.Name] = x
            picked_names = forms.SelectFromList.show(
                [x.Name for x in all_sheets],
                title='选择要标注的井大样图纸（可多选）',
                button_name='开始标注',
                multiselect=True)
            if not picked_names:
                return
            if not isinstance(picked_names, (list, tuple)):
                picked_names = [picked_names]
            for nm_ in picked_names:
                if nm_ in by_name and by_name[nm_] not in sheets:
                    sheets.append(by_name[nm_])
        elif chosen:
            sheets = list(chosen[0])
        if not sheets:
            return

        # ---------- 2. 取所选图纸上的全部视图 ----------
        target_views = []
        for sh in sheets:
            for k, nm, v in _views_of(sh):
                target_views.append(v)
        if not target_views:
            forms.alert('所选图纸上没有可用视图。', title='一键井大样标注')
            return

        mark_of_view = {}
        for sh in sheets:
            _mk = _sheet_mark(sh) or ''
            for _k, _nm, _v in _views_of(sh):
                mark_of_view[str(_v.Id)] = _mk

        # 同一视图若放在多张图纸上会被重复选中：只处理一次，避免重复计数
        uniq_views = []
        uniq_ids = set()
        for v in target_views:
            try:
                vid = v.Id.Value
            except:
                vid = None
            if vid is not None:
                if vid in uniq_ids:
                    continue
                uniq_ids.add(vid)
            uniq_views.append(v)
        dup_views = len(target_views) - len(uniq_views)
        target_views = uniq_views

        # ---------- 防呆：运行前确认；已经有尺寸的视图默认跳过 ----------
        annotated_ids = set()
        try:
            want_ids = set(int(v_.Id.Value) for v_ in target_views)
            for dm_ in DB.FilteredElementCollector(doc).OfClass(DB.Dimension).ToElements():     # 全模型只扫一遍尺寸，按所属视图归类
                try:
                    ov_ = int(dm_.OwnerViewId.Value)
                except Exception:
                    continue
                if ov_ in want_ids:
                    annotated_ids.add(ov_)
        except Exception:
            annotated_ids = set()
        n_all = len(target_views)
        n_ann = len(annotated_ids)
        skip_annotated = bool(_SKIP_ANNOTATED)
        if len(sheets) == 1:
            scope_txt = u'图纸「{} {}」'.format(sheets[0].SheetNumber, sheets[0].Name)
        else:
            scope_txt = u'{} 张图纸'.format(len(sheets))
        MB = _SUW.MessageBox
        if _CONFIRM_RUN:
            if n_ann == 0:
                ans_ = MB.Show(u'将标注：{}（共 {} 个视图，都还没有尺寸）。\n\n确定开始吗？标注期间会有进度条，可以随时取消。'.format(scope_txt, n_all),
                               u'一键井大样标注', _SUW.MessageBoxButtons.OKCancel, _SUW.MessageBoxIcon.Question)
                if ans_ != _SUW.DialogResult.OK:
                    return
            elif n_ann < n_all:
                ans_ = MB.Show(u'将标注：{}\n共 {} 个视图，其中 {} 个已经有尺寸。\n\n'
                               u'是：只标没有尺寸的 {} 个视图（推荐）\n'
                               u'否：全部都标（已有尺寸的补标，同位置的不会重复）\n'
                               u'取消：不标注'.format(scope_txt, n_all, n_ann, n_all - n_ann),
                               u'一键井大样标注', _SUW.MessageBoxButtons.YesNoCancel, _SUW.MessageBoxIcon.Question)
                if ans_ == _SUW.DialogResult.Cancel:
                    return
                skip_annotated = (ans_ == _SUW.DialogResult.Yes)
            else:
                ans_ = MB.Show(u'将标注：{}\n这 {} 个视图都已经有尺寸了。\n\n'
                               u'是：仍然补标（同位置的不会重复）\n否：不标注'.format(scope_txt, n_all),
                               u'一键井大样标注', _SUW.MessageBoxButtons.YesNo, _SUW.MessageBoxIcon.Question)
                if ans_ != _SUW.DialogResult.Yes:
                    return
                skip_annotated = False
        elif skip_annotated and n_all and n_ann >= n_all:
            forms.alert(u'这些视图都已经有尺寸了，没有需要标注的。想重新补标，请在设置里取消「已经有尺寸的视图默认跳过」。', title='一键井大样标注')
            return

        # ---------- 3. 执行 ----------
        # 只新增尺寸，不改视图：不动裁剪框，不隐藏/显示任何构件，不删除视图里已有的尺寸。
        # 每张视图先整体排（尺寸链 + 总尺寸）；某步被 Revit 判定参照失效则整体回滚，再分开排、逐根排管各自提交。
        # 整次运行合并为一次撤销。
        tg = DB.TransactionGroup(doc, "KAIFA 一键井大样标注")
        tg.Start()

        def _step(name, work):
            """在独立事务里执行 work()；提交成功返回 True，失败（已回滚）返回 False。"""
            t = DB.Transaction(doc, name)
            fho = t.GetFailureHandlingOptions()
            fho.SetFailuresPreprocessor(_SilentFailures())
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

        made = 0
        ok_views = 0
        stats = {}
        notes = []
        dim_errs = []
        failed_steps = []
        out_views = []   # 裁剪框放不下而未标的视图：(视图名, 未标道数, 缺口说明)
        out_total = 0
        cover_total = [0]
        cancelled = [False]
        done_views = [0]
        skipped_annot = [0]
        pb = forms.ProgressBar(title=u'正在标注…（点取消可随时停止，已标注的保留）', cancellable=True)
        try:
            for _vi, v in enumerate(target_views):
                try:
                    pb.update_progress(_vi, len(target_views))
                except Exception:
                    pass
                if pb.cancelled:
                    cancelled[0] = True
                    break
                done_views[0] = _vi + 1
                if skip_annotated and int(v.Id.Value) in annotated_ids:
                    skipped_annot[0] += 1          # 已经有尺寸：不再重复跑
                    continue
                try:
                    del _DIM_ERRS[:]
                    _OUT_CROP.clear()
                    elems = _main_well(v, collect_target_elems(v, stats), mark_of_view.get(str(v.Id)))
                    if not elems:
                        # 井族的包围盒比裁剪框大太多（如斜排管把井体撑大）时会被当成「长构件」剔掉，
                        # 导致整张视图没有构件；这种情况放宽跨度判断再找一次本井
                        elems = _main_well(v, collect_target_elems(v, None, True), mark_of_view.get(str(v.Id)))
                    if not elems:
                        notes.append(('{}（无可用构件）'.format(v.Name), 0))
                        continue
                    if not _axes_ok(elems, _frame(v)):
                        # 井体旋转（墙面不与视图轴平行）：走旋转井的标注
                        rres = {'n': 0, 'why': None}
                        rled = _fresh_ledger(v)

                        def _rot(v=v, elems=elems, rres=rres, rled=rled):
                            rres['n'], rres['why'] = make_rotated_dims(v, elems, rled, stats)
                        ok_rot = _step("旋转井标注", _rot)
                        if not ok_rot:
                            # 提交时 Revit 判某条尺寸的参照无效，整步回滚了：改成全部用引出线再试一次
                            _ROT_FORCE_TRACE[0] = True
                            try:
                                rres['n'], rres['why'] = 0, None
                                rled = _fresh_ledger(v)
                                ok_rot = _step("旋转井标注（引出线）", _rot)
                            finally:
                                _ROT_FORCE_TRACE[0] = False
                        if not ok_rot:
                            failed_steps.append('{}：旋转井标注（Revit 判定参照失效，引出线重试也失败）'.format(v.Name))
                            continue
                        if _COVER_DIMS and _frame(v)['plan']:
                            def _rcover(v=v, elems=elems, rres=rres, rled=rled):
                                rres['cv'] = make_cover_dims_rot(v, elems, rled, stats, _COVER_DIMS_V)
                            rres['cv'] = 0
                            if not _step("井盖尺寸（旋转井）", _rcover):
                                rres['cv'] = 0
                                failed_steps.append('{}：井盖到内壁尺寸（Revit 判定参照失效）'.format(v.Name))
                            cover_total[0] += rres['cv']
                            rres['n'] += rres['cv']
                        _bump(stats, _ST_DUP, rled.dup)
                        dim_errs.extend(_DIM_ERRS)
                        made += rres['n']
                        if rres['n']:
                            ok_views += 1
                            notes.append(('{}（旋转井）'.format(v.Name), rres['n']))
                        elif rled.dup:
                            notes.append(('{}（旋转井：已有尺寸，没有重复标）'.format(v.Name), -1))
                        else:
                            notes.append(('{}（旋转井：{}）'.format(v.Name, rres['why'] or '未生成尺寸'), 0))
                        continue
                    # 标注框：水平范围用可见墙面（TELCO 族的包围盒比井身大一倍，用包围盒会把内壁认偏），竖向仍用整个井
                    box = _clip_box(union_box(v, elems), crop_world_range(v))
                    if box is not None:
                        box = _face_box(elems, box)
                    if box is None:
                        notes.append(('{}（无法计算包围盒）'.format(v.Name), 0))
                        continue

                    res = {'cn': 0, 'n': 0, 'why': None}
                    # 先排尺寸链，再排总尺寸：总尺寸要知道各侧最外一道链线的位置
                    led = _fresh_ledger(v)
                    ext = {}

                    def _chains():
                        res['cn'], res['why'] = make_chain_dims(v, elems, box, led, stats, ext)

                    def _totals():
                        res['n'] = make_total_dims(v, elems, box, led, ext)

                    def _both():
                        _chains()
                        _totals()

                    if not _step("标注", _both):
                        # 整体回滚：已回滚的尺寸不在视图里了，判重表与外移量重建后再分开排
                        res['cn'] = 0
                        res['n'] = 0
                        ext = {}
                        led = _fresh_ledger(v)
                        if not _step("排管尺寸链", _chains):
                            # 逐根排管各自提交，只丢弃参照失效的那一根
                            res['cn'] = 0
                            ext = {}
                            led = _fresh_ledger(v)
                            try:
                                frm = _frame(v)
                                all_banks = _find_banks(crop_world_range(v), frm)
                            except Exception:
                                all_banks = []
                            for bk in all_banks:
                                for dr in ('h', 'v'):
                                    sub = {}

                                    def _one(bk=bk, dr=dr, sub=sub):
                                        sub['cn'], _w = make_chain_dims(v, elems, box, led, stats, ext, [bk], dr)
                                    if _step("排管尺寸链", _one):
                                        res['cn'] += sub.get('cn', 0)
                                    else:
                                        failed_steps.append('{}：排管 {} 的{}向尺寸链（Revit 判定参照失效）'.format(v.Name, bk.Id, '水平' if dr == 'h' else '竖'))
                        if not _step("总尺寸", _totals):
                            res['n'] = 0
                            failed_steps.append('{}：总尺寸（Revit 判定参照失效）'.format(v.Name))

                    # 井盖到内壁尺寸：单独一个事务，失败不影响上面的排管链和总尺寸
                    if _COVER_DIMS and _frame(v)['plan']:
                        def _covers():
                            res['cv'] = make_cover_dims(v, elems, box, led, stats, _COVER_DIMS_V)
                        res['cv'] = 0
                        if not _step("井盖尺寸", _covers):
                            res['cv'] = 0
                            failed_steps.append('{}：井盖到内壁尺寸（Revit 判定参照失效）'.format(v.Name))
                        cover_total[0] += res['cv']
                        res['n'] += res['cv']

                    _bump(stats, _ST_DUP, led.dup)
                    if _OUT_CROP:
                        out_total += len(_OUT_CROP)
                        out_views.append((v.Name, len(_OUT_CROP), sorted(set(_OUT_CROP.values()))[0]))
                    cn = res['cn']
                    n = res['n']
                    made += n + cn
                    dim_errs.extend(_DIM_ERRS)
                    if n + cn:
                        ok_views += 1
                        notes.append(('{}'.format(v.Name), n + cn))
                    elif led.dup:
                        notes.append(('{}（已有尺寸，没有重复标）'.format(v.Name), -1))
                    else:
                        if res['why'] and res['why'] != '视图内无排管（结构框架）':
                            why = res['why']
                        else:
                            why = '未生成尺寸'
                        notes.append(('{}（{}）'.format(v.Name, why), 0))
                except Exception as ev:
                    notes.append(('{}（异常：{}）'.format(v.Name, ev), 0))
            tg.Assimilate()
        except Exception as inner:
            try:
                pb.close()
            except Exception:
                pass
            try:
                tg.RollBack()
            except Exception:
                pass
            forms.alert('标注过程出错：\n' + str(inner) + '\n\n' + traceback.format_exc(),
                        title='一键井大样标注')
            return

        try:
            pb.close()
        except Exception:
            pass

        # ---------- 4. 报告 ----------
        msg = '井大样标注完成！（只新增尺寸：视图的裁剪框、隐藏状态和已有尺寸都没有改动）\n\n'
        if cancelled[0]:
            msg = msg.replace('井大样标注完成！', '井大样标注已取消！只处理了 {} / {} 个视图，已标注的保留（Ctrl+Z 可一次撤销）。'.format(max(done_views[0] - 1, 0), len(target_views)))
        msg += '• 处理视图：{} 个（成功 {} 个）\n'.format(len(target_views), ok_views)
        if skipped_annot[0]:
            msg += '• 已经有尺寸而跳过的视图：{} 个（没有重复标注）' .format(skipped_annot[0]) + chr(10)
        msg += '• 标注排管（井内尺寸链）：{} 根（按视图累计）\n'.format(stats.get(_ST_BANK_OK, 0))
        msg += '• 生成尺寸：{} 道（外包总尺寸 + 尺寸链 + 井盖到内壁）\n'.format(made)
        if cover_total[0]:
            msg += '    其中井盖到井内壁：{} 道\n'.format(cover_total[0])
        if dup_views:
            msg += '• 重复选择的视图（已合并）：{} 个\n'.format(dup_views)
        if stats.get(_ST_DUP):
            msg += '• 同位置已有或重复的尺寸（已跳过，不重复标）：{} 道\n'.format(stats[_ST_DUP])
        if out_total:
            msg += '• 超出注释裁剪框的尺寸：{} 道（已照常标注；视图没有改动，需要显示时往外拉）\n'.format(out_total)
            for nm, cnt_, gap in out_views[:10]:
                msg += '    - {}：{} 道（{}）\n'.format(nm, cnt_, gap)
            if len(out_views) > 10:
                msg += '    ...（其余 {} 个视图同类）\n'.format(len(out_views) - 10)
        if dim_errs:
            uniq = []
            for m in dim_errs:
                if m not in uniq:
                    uniq.append(m)
            msg += '• 尺寸创建失败：{} 道（Revit 拒绝）：{}\n'.format(len(dim_errs), '；'.join(uniq[:3]))
        if failed_steps:
            msg += '• 未能写入的部分（已跳过，其余照常）：\n'
            for x in failed_steps[:10]:
                msg += '    - {}\n'.format(x)
        skip_txt = _fmt_skips(stats)
        if skip_txt:
            msg += '\n跳过的构件（按视图累计）：\n' + skip_txt
        if _BANK_ERRS:
            uniq_b = []
            for m in _BANK_ERRS:
                if m not in uniq_b:
                    uniq_b.append(m)
            msg += '排管处理异常的原因：{}\n'.format('；'.join(uniq_b[:2]))
        had = [n for n in notes if n[1] < 0]
        if had:
            msg += '\n已经有尺寸、没有重复标的视图 {} 个（不是失败）。\n'.format(len(had))
        bad = [n for n in notes if n[1] == 0]
        if bad:
            msg += '\n未生成尺寸的视图 {} 个：\n'.format(len(bad))
            for nm, _ in bad[:10]:
                msg += '    - {}\n'.format(nm)
            if len(bad) > 10:
                msg += '    ...（其余 {} 个同类）\n'.format(len(bad) - 10)
        msg += '\n设置文件：' + SETTINGS_FILE + '（偏移、间距、尺寸类型、各视图标什么、井类别等都能改）'
        forms.alert(msg, title='一键井大样标注')

    except System.Exception as e:
        try:
            pb0[0].close()
        except Exception:
            pass
        forms.alert('发生严重系统错误:\n' + str(e) + '\n\n' + traceback.format_exc(), title='报错追踪')
    except Exception as e:
        try:
            pb0[0].close()
        except Exception:
            pass
        forms.alert('发生严重Python错误:\n' + str(e) + '\n\n' + traceback.format_exc(), title='报错追踪')


if __name__ == '__main__':
    main()
