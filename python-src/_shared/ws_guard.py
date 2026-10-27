# ---- ws_guard（天河落共用片段，打包时内联进脚本）--------------------------------
# 协同模型（中心文件）里，被别人占用、或中心文件里已经被改过（本地不是最新）的图元改不了；
# 只要一批里混进一个，Revit 整个事务回滚。写入前先分出来：能改的照常改，改不了的跳过并告诉用户是谁占着。
# 用法：ok, blocked = wg_split(doc, elems)；msg = wg_note(blocked) 放进确认框（没有被占用的返回空字符串）。
import Autodesk.Revit.DB as _WGDB


def wg_split(doc, elems):
    """返回 (能改的图元, 改不了的 [(图元, 原因)])。非协同模型全部能改。"""
    elems = [e for e in elems if e is not None]
    if not doc.IsWorkshared:
        return elems, []
    ok, blocked = [], []
    for e in elems:
        why = None
        try:
            st = _WGDB.WorksharingUtils.GetCheckoutStatus(doc, e.Id)
            if st == _WGDB.CheckoutStatus.OwnedByOtherUser:
                who = u''
                try:
                    who = _WGDB.WorksharingUtils.GetWorksharingTooltipInfo(doc, e.Id).Owner or u''
                except Exception:
                    pass
                why = u'被 %s 占用' % (who or u'别人')
            else:
                up = _WGDB.WorksharingUtils.GetModelUpdatesStatus(doc, e.Id)
                if up in (_WGDB.ModelUpdatesStatus.UpdatedInCentral, _WGDB.ModelUpdatesStatus.DeletedInCentral):
                    why = u'中心文件里已被改过，需先同步'
        except Exception:
            why = None
        if why:
            blocked.append((e, why))
        else:
            ok.append(e)
    return ok, blocked


def wg_note(blocked, label=None, n=6):
    """确认框里的一段说明：几个改不了、谁占着、示例。label(e) 给出图元的称呼（如井号）。"""
    if not blocked:
        return u''
    by = {}
    for e, why in blocked:
        by.setdefault(why, []).append(e)
    lines = [u'\n⚠ %d 个改不了，会跳过（其余照常写入）：' % len(blocked)]
    for why, es in sorted(by.items(), key=lambda kv: -len(kv[1])):
        names = []
        for e in es[:n]:
            try:
                names.append((label(e) if label else u'') or u'%s' % e.Id.Value)
            except Exception:
                names.append(u'%s' % e.Id.Value)
        lines.append(u'  · %s：%d 个（%s%s）' % (why, len(es), u'、'.join(names), u' 等' if len(es) > n else u''))
    lines.append(u'  请对方同步并释放，或先「与中心文件同步」后再运行一次。')
    return u'\n'.join(lines)
# ---- ws_guard 结束 ------------------------------------------------------------------
