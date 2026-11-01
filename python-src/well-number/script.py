# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '一键编号\n/清除标记'
__author__ = 'didi'
__doc__ = ('给选中的构件（井、桩、设备……任何有「标记」的构件）按位置自动编号：\n'
           '可以按 X/Y 方向排，也可以沿一条模型线/详图线（道路中心线、管线走向）排，或从起点开始就近连成一串。\n'
           '前缀、起始号、位数、写入哪个参数都能改，先预览再写入，会提醒和模型里其它构件重号。整次可 Ctrl+Z 撤销。\n'
           '另有「清除选中井的标记」（原「一键清除井标记」已并入）：只清井的【标记】，列出井号确认，已出图的井会提醒。')

import io
import os
import json
import math
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from Autodesk.Revit import DB
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from pyrevit import revit, forms
import System.Windows.Forms as WF
import System.Drawing as SD

doc = revit.doc
uidoc = revit.uidoc
TITLE = '一键编号'
SETTINGS = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments),
                        'KaifaTools', '一键编号设置.json')
SORTS = [('X_ASC', '从左到右（X 递增）'), ('X_DESC', '从右到左（X 递减）'),
         ('Y_ASC', '从下到上（Y 递增）'), ('Y_DESC', '从上到下（Y 递减）'),
         ('PATH', '沿一条线编号（运行后点选模型线/详图线，按投影到线上的位置排）'),
         ('CHAIN', '就近连串（运行后点选起点构件，每次找离上一个最近的）')]
DEFAULTS = {'prefix': '', 'start': 1, 'digits': 2, 'step': 1, 'sort': 'X_ASC', 'filter': '',
            'param': '标记', 'only_empty': False}


# @include _shared/form_fit.py
# @include _shared/ws_guard.py


def load():
    s = dict(DEFAULTS)
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


def center(e):
    loc = e.Location
    if isinstance(loc, DB.LocationPoint):
        return loc.Point
    if isinstance(loc, DB.LocationCurve):
        return loc.Curve.Evaluate(0.5, True)
    bb = e.get_BoundingBox(None)
    if bb is None:
        return None
    return (bb.Min + bb.Max) / 2.0


def target_param(e, name):
    if name in ('标记', 'Mark'):
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        if p is not None:
            return p
    return e.LookupParameter(name)


def comment(e):
    p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
    return (p.AsString() or '') if (p is not None and p.HasValue) else ''


def dialog(s, n_sel):
    f = WF.Form()
    f.Text = TITLE
    f.Font = SD.Font('Microsoft YaHei UI', 9.0)
    f.StartPosition = WF.FormStartPosition.CenterScreen
    f.FormBorderStyle = WF.FormBorderStyle.FixedDialog
    f.MaximizeBox = f.MinimizeBox = False
    y = [14]

    def lab(t, x=16, w=110):
        l = WF.Label(); l.Text = t; l.Location = SD.Point(x, y[0] + 3); l.Size = SD.Size(w, 20); f.Controls.Add(l)

    def box(v, x=130, w=120):
        t = WF.TextBox(); t.Text = v; t.Location = SD.Point(x, y[0]); t.Size = SD.Size(w, 24); f.Controls.Add(t); return t

    def num(v, lo, hi, x=130, w=80):
        n = WF.NumericUpDown(); n.Minimum = lo; n.Maximum = hi; n.Value = max(lo, min(hi, int(v)))
        n.Location = SD.Point(x, y[0]); n.Size = SD.Size(w, 24); f.Controls.Add(n); return n

    info = WF.Label(); info.Text = ('已选 %d 个构件。' % n_sel) if n_sel else '没有预先选中构件：确定后在视图里框选。'
    info.Location = SD.Point(16, y[0]); info.Size = SD.Size(440, 20); f.Controls.Add(info); y[0] += 30
    lab('前缀'); t_pre = box(s['prefix']); y[0] += 30
    lab('起始号'); n_start = num(s['start'], 0, 999999); lab('位数', 230, 40); n_dig = num(s['digits'], 1, 6, 274, 60)
    lab('步长', 346, 40); n_step = num(s['step'], 1, 1000, 390, 60); y[0] += 30
    lab('写入参数'); t_par = box(s['param']); y[0] += 30
    lab('注释含（可空）'); t_fil = box(s['filter'], w=200); y[0] += 30
    lab('排序方式'); y[0] += 22
    cb = WF.ComboBox(); cb.DropDownStyle = WF.ComboBoxStyle.DropDownList
    for _k, t in SORTS:
        cb.Items.Add(t)
    keys = [k for k, _t in SORTS]
    cb.SelectedIndex = keys.index(s['sort']) if s['sort'] in keys else 0
    cb.Location = SD.Point(16, y[0]); cb.Size = SD.Size(440, 24); f.Controls.Add(cb); y[0] += 32
    c_emp = WF.CheckBox(); c_emp.Text = '只给参数还空着的构件编号（已有编号的不动，新号自动跳过已用的号）'
    c_emp.Checked = bool(s['only_empty']); c_emp.Location = SD.Point(16, y[0]); c_emp.Size = SD.Size(440, 22)
    f.Controls.Add(c_emp); y[0] += 34
    ok = WF.Button(); ok.Text = '预览…'; ok.DialogResult = WF.DialogResult.OK; ok.Location = SD.Point(270, y[0]); ok.Size = SD.Size(90, 30)
    ca = WF.Button(); ca.Text = '取消'; ca.DialogResult = WF.DialogResult.Cancel; ca.Location = SD.Point(366, y[0]); ca.Size = SD.Size(90, 30)
    f.Controls.Add(ok); f.Controls.Add(ca); f.AcceptButton = ok; f.CancelButton = ca
    f.ClientSize = SD.Size(472, y[0] + 44)
    if ff_fit(f).ShowDialog() != WF.DialogResult.OK:
        return None
    return {'prefix': t_pre.Text, 'start': int(n_start.Value), 'digits': int(n_dig.Value), 'step': int(n_step.Value),
            'param': (t_par.Text or '标记').strip(), 'filter': t_fil.Text.strip(), 'sort': keys[cb.SelectedIndex],
            'only_empty': c_emp.Checked}


class _LineFilter(ISelectionFilter):
    def AllowElement(self, e):
        return isinstance(e, DB.CurveElement)

    def AllowReference(self, r, p):
        return False


def order(items, mode):
    """items: [(e, 点)]。返回排好序的列表和说明。"""
    if mode in ('X_ASC', 'X_DESC'):
        return sorted(items, key=lambda t: (t[1].X, t[1].Y), reverse=(mode == 'X_DESC')), ''
    if mode in ('Y_ASC', 'Y_DESC'):
        return sorted(items, key=lambda t: (t[1].Y, t[1].X), reverse=(mode == 'Y_DESC')), ''
    if mode == 'PATH':
        r = uidoc.Selection.PickObject(ObjectType.Element, _LineFilter(), '点选一条模型线或详图线（按投影到这条线上的位置编号，从线的起点开始）')
        crv = doc.GetElement(r.ElementId).GeometryCurve
        out = []
        far = 0
        for e, p in items:
            q = DB.XYZ(p.X, p.Y, crv.GetEndPoint(0).Z)
            res = crv.Project(q)
            if res is None:
                continue
            out.append((crv.ComputeNormalizedParameter(res.Parameter), e, p))
            if res.Distance * 304.8 > 50000:
                far += 1
        out.sort(key=lambda t: t[0])
        note = ('（%d 个构件离这条线超过 50 m，请确认选对了线）' % far) if far else ''
        return [(e, p) for _t, e, p in out], note
    if mode == 'CHAIN':
        r = uidoc.Selection.PickObject(ObjectType.Element, '点选第一个（起点）构件')
        rest = list(items)
        cur = None
        for i, (e, p) in enumerate(rest):
            if e.Id == r.ElementId:
                cur = rest.pop(i)
                break
        if cur is None:
            return order(items, 'X_ASC')[0], '（点选的构件不在要编号的范围里，改为 X 递增）'
        out = [cur]
        while rest:
            j = min(range(len(rest)), key=lambda k: rest[k][1].DistanceTo(out[-1][1]))
            out.append(rest.pop(j))
        return out, ''
    return items, ''


def number_main():
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    sel = [e for e in sel if e is not None and e.Category is not None and not isinstance(e, DB.ElementType)]
    s = dialog(load(), len(sel))
    if s is None:
        return
    save(s)
    if not sel:
        try:
            sel = list(uidoc.Selection.PickElementsByRectangle('框选要编号的构件'))
        except Exception:
            return
    pool = []
    skipped_filter = skipped_param = kept = 0
    for e in sel:
        if s['filter'] and s['filter'].lower() not in comment(e).lower():
            skipped_filter += 1
            continue
        p = target_param(e, s['param'])
        if p is None or p.IsReadOnly or p.StorageType != DB.StorageType.String:
            skipped_param += 1
            continue
        if s['only_empty'] and (p.AsString() or '').strip():
            kept += 1
            continue
        c = center(e)
        if c is not None:
            pool.append((e, c))
    # 协同模型：被别人占用的构件改不了，先排除（不占号），确认框里列出来
    _ok, blocked = wg_split(doc, [e for e, _c in pool])
    if blocked:
        bad = set(e.Id for e, _w in blocked)
        pool = [(e, c) for e, c in pool if e.Id not in bad]
    if not pool:
        forms.alert('没有可编号的构件。\n注释不符：%d 个；没有可写的「%s」文字参数：%d 个；已有编号保留：%d 个。%s'
                    % (skipped_filter, s['param'], skipped_param, kept, wg_note(blocked)), title=TITLE)
        return
    try:
        ordered, note = order(pool, s['sort'])
    except Exception:
        return   # 点选时按了 Esc
    # 「只给空着的编号」时，模型里同类构件已经用掉的号跳过，不会编出重号
    used = set()
    if s['only_empty']:
        for cid in set(e.Category.Id for e, _p in ordered):
            for o in DB.FilteredElementCollector(doc).OfCategoryId(cid).WhereElementIsNotElementType():
                op = target_param(o, s['param'])
                v = (op.AsString() or '').strip() if op is not None else ''
                if v:
                    used.add(v)
    plan = []
    k = 0
    for e, _p in ordered:
        while True:
            v = '%s%s' % (s['prefix'], str(s['start'] + k * s['step']).zfill(s['digits']))
            k += 1
            if v not in used:
                break
        plan.append((e, v))
    # 和模型里其它（不在本次范围里的）同类构件重号
    mine = set(e.Id for e, _v in plan)
    new_vals = set(v for _e, v in plan)
    clash = []
    cats = set(e.Category.Id for e, _v in plan)
    for cid in cats:
        for o in DB.FilteredElementCollector(doc).OfCategoryId(cid).WhereElementIsNotElementType():
            if o.Id in mine:
                continue
            op = target_param(o, s['param'])
            v = (op.AsString() or '') if op is not None else ''
            if v and v in new_vals:
                clash.append(v)
    lines = ['%s → %s' % ((target_param(e, s['param']).AsString() or '（空）'), v) for e, v in plan[:12]]
    msg = '将给 %d 个构件写入「%s」：\n\n%s%s' % (len(plan), s['param'], '\n'.join(lines), '\n…' if len(plan) > 12 else '')
    if note:
        msg += '\n\n' + note
    if skipped_filter or skipped_param or kept:
        msg += '\n\n跳过：注释不符 %d、没有可写参数 %d、已有编号保留 %d。' % (skipped_filter, skipped_param, kept)
    if clash:
        msg += '\n\n⚠ 和模型里其它构件重号 %d 个：%s' % (len(set(clash)), '、'.join(sorted(set(clash))[:10]))
    msg += wg_note(blocked, lambda e: (target_param(e, s['param']).AsString() or '').strip())
    msg += '\n\n写入？（可 Ctrl+Z 撤销）'
    if not forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
        return
    t = DB.Transaction(doc, '天河落 · 一键编号')
    t.Start()
    n = 0
    try:
        for e, v in plan:
            target_param(e, s['param']).Set(v)
            n += 1
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        forms.alert('写入失败，已撤销：\n' + traceback.format_exc(), title=TITLE)
        return
    forms.alert('已编号 %d 个（%s ~ %s）。' % (n, plan[0][1], plan[-1][1]), title=TITLE)


TITLE_CM = '一键清除标记'
_WELL_CAT = int(DB.BuiltInCategory.OST_GenericModel)   # 井族所在的类别


class _CmSuppressWarnings(DB.IFailuresPreprocessor):
    """静默处理所有警告，消除弹窗阻断"""
    def PreprocessFailures(self, failuresAccessor):
        for f in failuresAccessor.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                failuresAccessor.DeleteWarning(f)
        return DB.FailureProcessingResult.Continue




def _cm_mark_param(elem):
    p = elem.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
    if not p:
        p = elem.LookupParameter("标记")
    return p


def _cm_is_well(elem):
    try:
        return isinstance(elem, DB.FamilyInstance) and elem.Category is not None and elem.Category.Id.Value == _WELL_CAT
    except Exception:
        return False


def _cm_sheet_names():
    """全部图纸的「图号 图名」，用来提醒「这口井已经出图」。"""
    names = []
    try:
        for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet):
            names.append(u"{} {}".format(s.SheetNumber, s.Name))
    except Exception:
        pass
    return names


def _cm_sample(marks, n=10):
    s = u", ".join(marks[:n])
    if len(marks) > n:
        s += u" 等共 {} 个".format(len(marks))
    return s


def clear_marks():
    # 1. 获取预选图元 (先框选再点按钮)
    selected_ids = list(uidoc.Selection.GetElementIds())

    # 2. 若未预选，进入拉框框选模式 (先点按钮再框选)
    if not selected_ids:
        try:
            picked_elements = uidoc.Selection.PickElementsByRectangle(
                "请在平面图中拉框框选需要清除【标记】的井："
            )
            if picked_elements:
                selected_ids = [e.Id for e in picked_elements]
        except Exception:
            # 用户按 ESC 取消
            return

    if not selected_ids:
        return

    # 3. 收集「标记」非空的图元，分成井和其他图元
    wells = []
    others = []
    for eid in selected_ids:
        elem = doc.GetElement(eid)
        if not elem:
            continue
        p_mark = _cm_mark_param(elem)
        if p_mark and not p_mark.IsReadOnly:
            current_val = (p_mark.AsString() or "").strip()
            if current_val:
                (wells if _cm_is_well(elem) else others).append((elem, p_mark, current_val))

    if not wells and not others:
        forms.alert(
            "选中的图元中没有需要清空的【标记】值（可能标记本就为空，或选中的不是井）。",
            title=TITLE_CM
        )
        return

    # 协同模型：被别人占用的图元清不了，先排除并在确认框里说明（否则整批回滚）
    _ok, blocked = wg_split(doc, [e for e, _p, _m in wells + others])
    if blocked:
        bad = set(e.Id for e, _w in blocked)
        wells = [w for w in wells if w[0].Id not in bad]
        others = [o for o in others if o[0].Id not in bad]
    note = wg_note(blocked, lambda e: (_cm_mark_param(e).AsString() or u'').strip())
    if not wells and not others:
        forms.alert(u"选中的图元都改不了。" + note, title=TITLE_CM)
        return

    # 4. 防呆：先列出要清的内容，确认后才执行
    names = _cm_sheet_names()
    on_sheet = [m for _e, _p, m in wells if any(m in nm for nm in names)]
    msg = u""
    if wells:
        msg += u"将清空 {} 口井的【标记】：\n{}\n".format(len(wells), _cm_sample([m for _e, _p, m in wells]))
    else:
        msg += u"框选范围里没有井（常规模型）。\n"
    if on_sheet:
        msg += u"\n其中 {} 口井已经有图纸（图名里有这个井号）：{}\n清掉后这些图纸会找不到对应的井。\n".format(len(on_sheet), _cm_sample(on_sheet, 6))
    if others:
        cats = {}
        for e, _p, _m in others:
            nm = e.Category.Name if e.Category is not None else u"其他"
            cats[nm] = cats.get(nm, 0) + 1
        msg += u"\n另有 {} 个其他图元也带标记（{}），默认不动。\n".format(
            len(others), u"、".join(u"{} {} 个".format(k, v) for k, v in sorted(cats.items())))
        msg += note
        msg += u"\n\n是：只清井的标记\n否：井和其他图元的标记一起清\n取消：不清"
        ans = WF.MessageBox.Show(msg, TITLE_CM, WF.MessageBoxButtons.YesNoCancel, WF.MessageBoxIcon.Question)
        if ans == WF.DialogResult.Cancel:
            return
        targets = wells if ans == WF.DialogResult.Yes else wells + others
    else:
        msg += note
        msg += u"\n确定清空吗？（Ctrl+Z 可撤销）"
        ans = WF.MessageBox.Show(msg, TITLE_CM, WF.MessageBoxButtons.OKCancel, WF.MessageBoxIcon.Question)
        if ans != WF.DialogResult.OK:
            return
        targets = wells
    if not targets:
        forms.alert(u"没有需要清空的井标记，未做任何修改。", title=TITLE_CM)
        return

    # 5. 在安全事务中仅将【标记】字段清空，不修改任何其他参数或图元
    t = DB.Transaction(doc, TITLE_CM)
    fho = t.GetFailureHandlingOptions()
    fho.SetFailuresPreprocessor(_CmSuppressWarnings())
    fho.SetClearAfterRollback(True)
    t.SetFailureHandlingOptions(fho)

    cleared_marks = []
    t.Start()
    try:
        for elem, p_mark, old_val in targets:
            p_mark.Set("")
            cleared_marks.append(old_val)
        t.Commit()
    except Exception as ex:
        t.RollBack()
        forms.alert("清空标记字段失败: {}".format(str(ex)), title="错误")
        return

    # 6. 反馈提示
    msg = u"【标记清空完成】\n\n"
    msg += u"• 清空了 {} 个图元的标记：{}\n".format(len(cleared_marks), _cm_sample(cleared_marks))
    msg += u"• 其他所有参数、尺寸及模型图元均未改动。\n\n"
    msg += u"(提示: 如需恢复，可在 Revit 中直接按 Ctrl + Z 撤销)"
    forms.alert(msg, title=TITLE_CM)


def main():
    pick = forms.CommandSwitchWindow.show(['按位置编号', '清除选中井的标记'], message=TITLE)
    if not pick:
        return
    if pick.startswith('清除'):
        clear_marks()
    else:
        number_main()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        forms.alert('出错了：\n' + traceback.format_exc(), title=TITLE)
