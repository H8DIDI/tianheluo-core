# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "一键钢筋三维\n实体显示"
__author__ = "KAIFA"
__doc__ = ("在三维视图里一键显示钢筋的三维实体（精细显示、带真实粗细），并把混凝土构件设为半透明。\n"
           "框选构件则只处理选中的；没有框选则处理当前视图里的全部混凝土构件。\n"
           "可以一键恢复：透明度、钢筋显示、视图详细程度都回到运行前（只动本插件改过的，构件原有的其它显示替换保留）。")

import io
import json
import os
import traceback
import clr
import System
clr.AddReference('RevitAPI')
from Autodesk.Revit import DB
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms

doc = revit.doc
uidoc = revit.uidoc

TITLE = u"一键钢筋三维实体显示"
CONCRETE_TRANSPARENCY = 50   # 混凝土透明度 %
STATE_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools")
STATE_PATH = os.path.join(STATE_DIR, "钢筋三维显示状态.json")

CONCRETE_BICS = [
    DB.BuiltInCategory.OST_StructuralColumns,
    DB.BuiltInCategory.OST_StructuralFraming,
    DB.BuiltInCategory.OST_Walls,
    DB.BuiltInCategory.OST_StructuralFoundation,
    DB.BuiltInCategory.OST_Floors,
]
CONCRETE_CAT_IDS = set(int(b) for b in CONCRETE_BICS)


def _eid(x):
    try:
        return int(x.Value)
    except Exception:
        try:
            return int(x.IntegerValue)
        except Exception:
            return -1


def _is_concrete(e):
    try:
        return e is not None and e.Category is not None and _eid(e.Category.Id) in CONCRETE_CAT_IDS
    except Exception:
        return False


def _targets(view):
    """要处理的混凝土构件：有框选用框选的，没有用视图里的全部。"""
    sel = [doc.GetElement(i) for i in uidoc.Selection.GetElementIds()]
    sel = [e for e in sel if _is_concrete(e)]
    if sel:
        return sel, u'框选的构件'
    flt = DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](CONCRETE_BICS))
    col = DB.FilteredElementCollector(doc, view.Id)
    return list(col.WherePasses(flt).WhereElementIsNotElementType().ToElements()), u'当前视图的构件'


def _rebars(view, hosts):
    """要设成「不被遮挡」的钢筋：宿主是这些混凝土构件的（框选时），或视图里全部的钢筋。"""
    host_ids = set(_eid(h.Id) for h in hosts)
    out = []
    for cls in (DB.Structure.Rebar, DB.Structure.RebarInSystem):
        for r in DB.FilteredElementCollector(doc, view.Id).OfClass(cls):
            try:
                if not host_ids or _eid(r.GetHostId()) in host_ids:
                    out.append(r)
            except Exception:
                out.append(r)
    return out


def _read_state():
    if not os.path.exists(STATE_PATH):
        return None
    try:
        with io.open(STATE_PATH, 'r', encoding='utf-8') as f:
            return json.loads(f.read())
    except Exception:
        return None


def _write_state(state):
    if not os.path.exists(STATE_DIR):
        os.makedirs(STATE_DIR)
    with io.open(STATE_PATH, 'w', encoding='utf-8') as f:
        f.write(json.dumps(state, ensure_ascii=False, indent=2))


def show_3d(view):
    """视图精细（Revit 2024 起，三维视图精细时钢筋自动按实体显示）+ 钢筋不被遮挡 + 混凝土半透明（一个事务，失败整体回滚）。
    只改透明度这一项，构件原有的其它显示替换（颜色、线型等）保留；改前的值记下来，恢复时还原。"""
    if view.ViewType != DB.ViewType.ThreeD:
        forms.alert(u"请在三维视图里运行（当前是「%s」）。" % view.Name, title=TITLE)
        return
    targets, src = _targets(view)
    if not targets:
        forms.alert(u"没有找到混凝土构件（柱、梁、墙、基础、楼板）。", title=TITLE)
        return
    state = _read_state() or {}
    if state.get('view') != _eid(view.Id):
        state = {'view': _eid(view.Id), 'detail': None, 'trans': {}, 'unobs': []}
    rebars = _rebars(view, targets if src == u'框选的构件' else [])
    warn = []
    n_conc = n_bar = 0
    t = DB.Transaction(doc, u'钢筋三维显示')
    t.Start()
    try:
        if state.get('detail') is None:
            state['detail'] = int(view.DetailLevel)
        view.DetailLevel = DB.ViewDetailLevel.Fine
        try:
            view.SetCategoryHidden(DB.ElementId(DB.BuiltInCategory.OST_Rebar), False)
        except Exception as ex:
            warn.append(u'钢筋类别没能取消隐藏（视图样板可能锁定了可见性）：%s' % ex)
        for e in targets:
            key = str(_eid(e.Id))
            ogs = view.GetElementOverrides(e.Id)
            if key not in state['trans']:
                state['trans'][key] = ogs.Transparency
            ogs.SetSurfaceTransparency(CONCRETE_TRANSPARENCY)
            view.SetElementOverrides(e.Id, ogs)
            n_conc += 1
        unobs = set(state.get('unobs') or [])
        for r in rebars:
            try:
                if not r.IsUnobscuredInView(view):
                    r.SetUnobscuredInView(view, True)
                    unobs.add(_eid(r.Id))
                n_bar += 1
            except Exception:
                pass
        state['unobs'] = sorted(unobs)
        t.Commit()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        forms.alert(u"设置失败，已回滚：\n%s" % ex, title=TITLE)
        return
    _write_state(state)
    lines = [u'已处理 %d 个混凝土构件（%s），透明度 %d%%。' % (n_conc, src, CONCRETE_TRANSPARENCY),
             u'视图详细程度设为精细（钢筋按三维实体显示），%d 根钢筋设为不被遮挡。' % n_bar]
    if not rebars:
        lines.append(u'这些构件里还没有钢筋（可以先用「一键单构件快速配筋」）。')
    lines += warn
    lines.append(u'要恢复，运行本插件选「恢复」。')
    forms.alert(u'\n'.join(lines), title=TITLE)


def restore(view):
    """恢复本插件改过的东西：混凝土透明度回到原值、钢筋取消「不被遮挡」、视图详细程度还原。"""
    state = _read_state()
    if not state or not (state.get('trans') or state.get('ids')):
        forms.alert(u"没有需要恢复的构件（之前没有用本插件设置过，或已经恢复）。", title=TITLE)
        return
    target_view = doc.GetElement(DB.ElementId(state.get('view', -1)))
    if target_view is None:
        target_view = view
    trans = state.get('trans') or dict((str(i), 0) for i in state.get('ids', []))   # 旧版状态文件只记了 id
    t = DB.Transaction(doc, u'恢复混凝土不透明')
    t.Start()
    done = 0
    try:
        for k, old in trans.items():
            e = doc.GetElement(DB.ElementId(int(k)))
            if e is None:
                continue
            ogs = target_view.GetElementOverrides(e.Id)
            ogs.SetSurfaceTransparency(int(old or 0))
            target_view.SetElementOverrides(e.Id, ogs)
            done += 1
        for i in state.get('unobs') or []:
            r = doc.GetElement(DB.ElementId(int(i)))
            try:
                r.SetUnobscuredInView(target_view, False)
            except Exception:
                pass
        if state.get('detail') is not None:
            try:
                target_view.DetailLevel = DB.ViewDetailLevel(int(state['detail']))
            except Exception:
                pass
        t.Commit()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        forms.alert(u"恢复失败，已回滚：\n%s" % ex, title=TITLE)
        return
    try:
        os.remove(STATE_PATH)
    except Exception:
        pass
    forms.alert(u"已恢复 %d 个混凝土构件的透明度，钢筋和视图详细程度也还原了。" % done, title=TITLE)


def main():
    try:
        view = uidoc.ActiveView
        op = forms.CommandSwitchWindow.show([u'显示：钢筋三维 + 混凝土半透明', u'恢复：混凝土不透明'],
                                            message=u'请选择操作：')
        if not op:
            return
        if op.startswith(u'恢复'):
            restore(view)
        else:
            show_3d(view)
    except Exception as e:
        forms.alert(u'操作异常：\n%s\n\n%s' % (e, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
