# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "一键钢筋三维\n实体显示"
__author__ = "KAIFA"
__doc__ = ("在三维视图里一键显示钢筋的三维实体（精细显示、带真实粗细），并把混凝土构件设为半透明。\n"
           "框选构件则只处理选中的；没有框选则处理当前视图里的全部混凝土构件。\n"
           "可以一键恢复：混凝土恢复为不透明（只恢复本插件改过的构件）。")

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
    """钢筋精细显示 + 混凝土半透明（一个事务，失败整体回滚）。"""
    if view.ViewType != DB.ViewType.ThreeD:
        forms.alert(u"请在三维视图里运行（当前是「%s」）。" % view.Name, title=TITLE)
        return
    targets, src = _targets(view)
    if not targets:
        forms.alert(u"没有找到混凝土构件（柱、梁、墙、基础、楼板）。", title=TITLE)
        return
    ogs = DB.OverrideGraphicSettings()
    ogs.SetSurfaceTransparency(CONCRETE_TRANSPARENCY)
    warn = []
    ids = []
    t = DB.Transaction(doc, u'钢筋三维显示')
    t.Start()
    try:
        view.DetailLevel = DB.ViewDetailLevel.Fine
        try:
            view.SetCategoryHidden(DB.ElementId(DB.BuiltInCategory.OST_Rebar), False)
        except Exception as ex:
            warn.append(u'钢筋类别没能取消隐藏（视图样板可能锁定）：%s' % ex)
        for e in targets:
            view.SetElementOverrides(e.Id, ogs)
            ids.append(_eid(e.Id))
        t.Commit()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        forms.alert(u"设置失败，已回滚：\n%s" % ex, title=TITLE)
        return
    state = _read_state() or {}
    prev = state.get('ids', []) if state.get('view') == _eid(view.Id) else []
    _write_state({'view': _eid(view.Id), 'ids': sorted(set(prev) | set(ids))})
    lines = [u'已处理 %d 个混凝土构件（%s），透明度 %d%%。' % (len(ids), src, CONCRETE_TRANSPARENCY),
             u'视图细节已设为精细，钢筋按三维实体显示。']
    lines += warn
    lines.append(u'要恢复，运行本插件选「恢复」。')
    forms.alert(u'\n'.join(lines), title=TITLE)


def restore(view):
    """恢复本插件改过的混凝土构件为不透明。"""
    state = _read_state()
    if not state or not state.get('ids'):
        forms.alert(u"没有需要恢复的构件（之前没有用本插件设置过，或已经恢复）。", title=TITLE)
        return
    ids = state.get('ids', [])
    target_view = doc.GetElement(DB.ElementId(state.get('view', -1)))
    if target_view is None:
        target_view = view
    t = DB.Transaction(doc, u'恢复混凝土不透明')
    t.Start()
    done = 0
    try:
        blank = DB.OverrideGraphicSettings()
        for i in ids:
            e = doc.GetElement(DB.ElementId(i))
            if e is None:
                continue
            target_view.SetElementOverrides(e.Id, blank)
            done += 1
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
    forms.alert(u"已恢复 %d 个混凝土构件为不透明。" % done, title=TITLE)


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
