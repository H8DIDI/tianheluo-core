# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "机电过滤器\n与视图样板"
__author__ = "KAIFA"
__doc__ = ("按一张配置表，一键给项目建好机电的「系统过滤器 + 视图样板 + 系统类型」：\n"
           "· 系统过滤器：按系统类型名的开头（如 SF- 送风、XHS- 消火栓）建过滤器，带配色；还有「只看某系统」用的隐藏过滤器。\n"
           "· 视图样板：每个样板控制显示哪些机电类别（风管 / 管道 / 卫浴 / 喷头 / 电气 / 机械设备）、挂哪些过滤器并上色，"
           "可以从另一个样板复制（比如「除臭」从「暖通风」复制）。\n"
           "· 系统类型：按配置复制出风 / 水系统类型并上色。\n"
           "配置表在 文档\\KaifaTools\\机电样板配置.json，第一次运行会写出默认表（平面 / 三维、给排水 / 喷淋 / 电气 / 暖通 / 防排烟 / 消火栓 / 工艺管道…），"
           "按自己项目的系统缩写改一下就能用。已经有的过滤器 / 系统类型不会重复建，样板会按配置刷新。预览后才改，整次可 Ctrl+Z。")

import os
import io
import json
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms

# @include _shared/compat.py

doc = revit.doc
uidoc = revit.uidoc
TITLE = u"机电过滤器与视图样板"
CFG_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools")
CFG_PATH = os.path.join(CFG_DIR, u"机电样板配置.json")


def _u(x):
    try:
        return u"%s" % x
    except Exception:
        return u"?"


def _bic(n):
    return getattr(DB.BuiltInCategory, n, None)


# 类别分组（视图样板里按组显示 / 隐藏）
GROUPS = {
    "DUCT": ["OST_DuctCurves", "OST_DuctFitting", "OST_DuctAccessory", "OST_FlexDuctCurves", "OST_DuctTerminal", "OST_PlaceHolderDucts", "OST_DuctInsulations", "OST_DuctLinings"],
    "PIPE": ["OST_PipeCurves", "OST_PipeFitting", "OST_PipeAccessory", "OST_FlexPipeCurves", "OST_PlaceHolderPipes", "OST_PipeInsulations"],
    "PFX": ["OST_PlumbingFixtures"],
    "SPK": ["OST_Sprinklers"],
    "ELEC": ["OST_CableTray", "OST_CableTrayFitting", "OST_Conduit", "OST_ConduitFitting", "OST_ElectricalEquipment", "OST_ElectricalFixtures", "OST_LightingFixtures",
             "OST_LightingDevices", "OST_FireAlarmDevices", "OST_CommunicationDevices", "OST_DataDevices", "OST_SecurityDevices", "OST_TelephoneDevices", "OST_NurseCallDevices", "OST_Wire"],
    "EQ": ["OST_MechanicalEquipment"],
}
ALL_GROUPS = ["DUCT", "PIPE", "PFX", "SPK", "ELEC", "EQ"]
# 过滤器作用类别（按系统参数种类）
FILTER_CATS = {
    "duct": ["OST_DuctCurves", "OST_DuctFitting", "OST_DuctAccessory", "OST_FlexDuctCurves", "OST_DuctTerminal", "OST_PlaceHolderDucts"],
    "pipe": ["OST_PipeCurves", "OST_PipeFitting", "OST_PipeAccessory", "OST_FlexPipeCurves", "OST_PlaceHolderPipes"],
    "tray": ["OST_CableTray", "OST_CableTrayFitting"],
}
FILTER_PARAM = {"duct": "RBS_DUCT_SYSTEM_TYPE_PARAM", "pipe": "RBS_PIPING_SYSTEM_TYPE_PARAM", "tray": "RBS_CTC_SERVICE_TYPE"}


def default_config():
    S = lambda name, kind, pre, rgb: {"name": name, "kind": kind, "prefixes": pre, "rgb": rgb}
    systems = [
        S(u"系统-SF送风", "duct", [u"SF-"], [0, 112, 192]), S(u"系统-PF排风", "duct", [u"PF-"], [237, 125, 49]),
        S(u"系统-PY排烟", "duct", [u"PY-"], [192, 0, 0]), S(u"系统-BF补风", "duct", [u"BF-"], [0, 176, 240]),
        S(u"系统-JY正压送风", "duct", [u"JY-"], [0, 176, 80]), S(u"系统-CC除臭", "duct", [u"CC-"], [112, 48, 160]),
        S(u"系统-XHS消火栓", "pipe", [u"XHS-"], [255, 0, 0]), S(u"系统-JS生活给水", "pipe", [u"JS-"], [0, 176, 80]),
        S(u"系统-WS污水", "pipe", [u"WS-"], [128, 64, 0]), S(u"系统-YS雨水", "pipe", [u"YS-"], [0, 128, 128]),
        S(u"系统-LN冷凝水", "pipe", [u"LN-"], [128, 128, 128]),
        S(u"桥架-强电", "tray", [u"强电"], [255, 0, 0]), S(u"桥架-弱电", "tray", [u"弱电"], [0, 0, 255]), S(u"桥架-消防", "tray", [u"消防"], [255, 128, 0]),
    ]
    hides = [
        {"name": u"隐-非除臭风管", "kind": "duct", "prefixes": [u"CC-"]},
        {"name": u"隐-非防排烟风管", "kind": "duct", "prefixes": [u"PY-", u"JY-", u"BF-"]},
        {"name": u"隐-非消火栓管道", "kind": "pipe", "prefixes": [u"XHS-"]},
    ]
    allsys = [s["name"] for s in systems]
    specs = {
        u"给排水平面图": {"groups": ["PIPE", "PFX", "EQ"], "systems": [u"系统-JS生活给水", u"系统-WS污水", u"系统-YS雨水"], "hide": []},
        u"喷淋平面图": {"groups": ["PIPE", "SPK", "EQ"], "systems": [u"系统-XHS消火栓"], "hide": []},
        u"电气平面图": {"groups": ["ELEC"], "systems": [u"桥架-强电", u"桥架-弱电", u"桥架-消防"], "hide": []},
        u"暖通水平面图": {"groups": ["PIPE", "EQ"], "systems": [u"系统-LN冷凝水"], "hide": []},
        u"暖通风平面图": {"groups": ["DUCT", "EQ"], "systems": [u"系统-SF送风", u"系统-PF排风", u"系统-PY排烟", u"系统-BF补风", u"系统-JY正压送风", u"系统-CC除臭"], "hide": []},
        u"除臭平面图": {"groups": ["DUCT", "EQ"], "systems": [u"系统-CC除臭"], "hide": [u"隐-非除臭风管"]},
        u"防排烟平面图": {"groups": ["DUCT", "EQ"], "systems": [u"系统-PY排烟", u"系统-JY正压送风", u"系统-BF补风"], "hide": [u"隐-非防排烟风管"]},
        u"消火栓平面图": {"groups": ["PIPE", "SPK", "EQ"], "systems": [u"系统-XHS消火栓"], "hide": [u"隐-非消火栓管道"]},
        u"管线综合平面图": {"groups": list(ALL_GROUPS), "systems": allsys, "hide": []},
        u"净高分析平面图": {"groups": list(ALL_GROUPS), "systems": [], "hide": []},
        u"三维-全专业": {"groups": list(ALL_GROUPS), "systems": allsys, "hide": []},
    }
    T = lambda name, spec, parent, kind: {"name": name, "spec": spec, "parent": parent, "kind": kind}
    templates = [
        T(u"建模-01给排水平面图", u"给排水平面图", u"", "plan"), T(u"建模-02喷淋平面图", u"喷淋平面图", u"", "plan"),
        T(u"建模-03电气平面图", u"电气平面图", u"", "plan"), T(u"建模-04暖通水平面图", u"暖通水平面图", u"", "plan"),
        T(u"建模-05暖通风平面图", u"暖通风平面图", u"", "plan"),
        T(u"建模-06除臭平面图", u"除臭平面图", u"建模-05暖通风平面图", "plan"), T(u"建模-07防排烟平面图", u"防排烟平面图", u"建模-05暖通风平面图", "plan"),
        T(u"建模-08消火栓平面图", u"消火栓平面图", u"建模-02喷淋平面图", "plan"),
        T(u"出图-01管线综合平面图", u"管线综合平面图", u"", "plan"), T(u"出图-02净高分析平面图", u"净高分析平面图", u"", "plan"),
        T(u"三维样板-全专业", u"三维-全专业", u"", "3d"),
    ]
    system_types = [
        {"name": u"SF-送风", "kind": "duct", "classification": "SupplyAir", "rgb": [0, 112, 192]},
        {"name": u"PF-排风", "kind": "duct", "classification": "ExhaustAir", "rgb": [237, 125, 49]},
        {"name": u"XHS-消火栓", "kind": "pipe", "classification": "FireProtectWet", "rgb": [255, 0, 0]},
        {"name": u"JS-生活给水", "kind": "pipe", "classification": "DomesticColdWater", "rgb": [0, 176, 80]},
        {"name": u"WS-污水", "kind": "pipe", "classification": "Sanitary", "rgb": [128, 64, 0]},
    ]
    return {"说明": u"改这张表：systems=系统过滤器（prefixes 是系统类型名的开头）；hides=隐藏过滤器；specs=每类样板显示哪些类别组和系统；templates=要建的样板（parent 填已有样板名则复制它）；system_types=要建的系统类型。",
            "systems": systems, "hides": hides, "specs": specs, "templates": templates, "system_types": system_types}


def load_config():
    try:
        if os.path.isfile(CFG_PATH):
            with io.open(CFG_PATH, "r", encoding="utf-8") as f:
                return json.load(f), True
    except Exception as ex:
        forms.alert(u"配置文件读不了（%s），本次用默认配置：\n%s" % (ex, CFG_PATH), title=TITLE)
    return default_config(), False


def write_default():
    if not os.path.isdir(CFG_DIR):
        os.makedirs(CFG_DIR)
    with io.open(CFG_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(default_config(), ensure_ascii=False, indent=1))


# ============================================================
# 过滤器
# ============================================================
def _cat_ids(names):
    out = List[DB.ElementId]()
    for n in names:
        b = _bic(n)
        if b is not None:
            out.Add(DB.ElementId(b))
    return out


def _rule(kind, prefix, negate):
    pid = DB.ElementId(getattr(DB.BuiltInParameter, FILTER_PARAM[kind]))
    f = DB.ParameterFilterRuleFactory.CreateNotBeginsWithRule if negate else DB.ParameterFilterRuleFactory.CreateBeginsWithRule
    try:
        return f(pid, prefix, False)          # 2020~2021：带「区分大小写」参数
    except TypeError:
        return f(pid, prefix)                 # 2022 起去掉了


def build_filter(name, kind, prefixes, negate):
    cats = _cat_ids(FILTER_CATS[kind])
    pf = DB.ParameterFilterElement.Create(doc, name, cats)
    if negate or len(prefixes) == 1:
        rules = List[DB.FilterRule]([_rule(kind, p, negate) for p in prefixes])       # 多条规则是「且」：不以任何一个开头
        pf.SetElementFilter(DB.ElementParameterFilter(rules))
    else:
        subs = List[DB.ElementFilter]([DB.ElementParameterFilter(List[DB.FilterRule]([_rule(kind, p, False)])) for p in prefixes])
        pf.SetElementFilter(DB.LogicalOrFilter(subs))                                  # 多个开头是「或」
    return pf


def existing_filters():
    return dict((_u(x.Name), x.Id) for x in DB.FilteredElementCollector(doc).OfClass(DB.ParameterFilterElement))


def ensure_filters(cfg, log):
    fid = existing_filters()
    made = 0
    for s in cfg["systems"]:
        if s["name"] not in fid:
            fid[s["name"]] = build_filter(s["name"], s["kind"], s["prefixes"], False).Id
            made += 1
    for h in cfg.get("hides", []):
        if h["name"] not in fid:
            fid[h["name"]] = build_filter(h["name"], h["kind"], h["prefixes"], True).Id
            made += 1
    log.append(u"过滤器：新建 %d 个（已有的保留）" % made)
    return fid


# ============================================================
# 视图样板
# ============================================================
def find_template(name):
    for v in DB.FilteredElementCollector(doc).OfClass(DB.View):
        if v.IsTemplate and _u(v.Name) == name:
            return v
    return None


def base_view(kind):
    for v in DB.FilteredElementCollector(doc).OfClass(DB.View3D if kind == "3d" else DB.ViewPlan):
        if v.IsTemplate:
            continue
        try:
            if kind == "plan" and v.ViewType != DB.ViewType.FloorPlan:
                continue
        except Exception:
            pass
        return v
    return None


def make_template(t, log):
    """新建样板：有 parent 就复制它，没有就从一张同类型的视图生成。"""
    if t.get("parent"):
        par = find_template(t["parent"])
        if par is not None:
            try:
                nid = par.Duplicate(DB.ViewDuplicateOption.Duplicate)
                v = doc.GetElement(nid)
                v.Name = t["name"]
                return v
            except Exception:
                pass
    bv = base_view(t["kind"])
    if bv is None:
        log.append(u"✗ %s：项目里没有%s视图可以用来生成样板" % (t["name"], u"三维" if t["kind"] == "3d" else u"楼层平面"))
        return None
    v = bv.CreateViewTemplate()
    v.Name = t["name"]
    return v


def set_groups(v, groups):
    shown = hidden = 0
    for g in ALL_GROUPS:
        vis = g in groups
        for n in GROUPS[g]:
            b = _bic(n)
            if b is None:
                continue
            try:
                cid = DB.ElementId(b)
                if not v.CanCategoryBeHidden(cid):
                    continue
                if v.GetCategoryHidden(cid) == vis:
                    v.SetCategoryHidden(cid, not vis)
                if vis:
                    shown += 1
                else:
                    hidden += 1
            except Exception:
                pass
    return shown, hidden


def apply_spec(v, spec, cfg, fid):
    sysd = dict((s["name"], s) for s in cfg["systems"])
    shown, hidden = set_groups(v, spec["groups"])
    for old in list(v.GetFilters()):
        v.RemoveFilter(old)
    nf = 0
    for fn in spec.get("hide", []):
        if fn in fid:
            v.AddFilter(fid[fn])
            v.SetFilterVisibility(fid[fn], False)
            nf += 1
    for fn in spec.get("systems", []):
        if fn not in fid:
            continue
        v.AddFilter(fid[fn])
        v.SetFilterVisibility(fid[fn], True)
        rgb = sysd.get(fn, {}).get("rgb")
        if rgb:
            c = DB.Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))
            og = DB.OverrideGraphicSettings()
            og.SetProjectionLineColor(c)
            og.SetCutLineColor(c)
            v.SetFilterOverrides(fid[fn], og)
        nf += 1
    return shown, hidden, nf


def build_templates(cfg, fid, log):
    made = updated = 0
    # 有 parent 的放后面，保证 parent 先建好
    order = sorted(cfg["templates"], key=lambda t: 1 if t.get("parent") else 0)
    for t in order:
        spec = cfg["specs"].get(t["spec"])
        if spec is None:
            log.append(u"✗ %s：配置里没有样板规格「%s」" % (t["name"], t["spec"]))
            continue
        v = find_template(t["name"])
        if v is None:
            v = make_template(t, log)
            if v is None:
                continue
            made += 1
        else:
            updated += 1
        shown, hidden, nf = apply_spec(v, spec, cfg, fid)
        log.append(u"%s：显示类别 %d、隐藏 %d、过滤器 %d" % (t["name"], shown, hidden, nf))
    log.append(u"视图样板：新建 %d 个、刷新 %d 个" % (made, updated))


# ============================================================
# 系统类型
# ============================================================
def _system_types(kind):
    cls = DB.Mechanical.MechanicalSystemType if kind == "duct" else DB.Plumbing.PipingSystemType
    return list(DB.FilteredElementCollector(doc).OfClass(cls))


def build_system_types(cfg, log):
    made = 0
    for st in cfg.get("system_types", []):
        kind = st["kind"]
        types = _system_types(kind)
        if any(_u(t.Name) == st["name"] for t in types):
            continue
        cls_name = st["classification"]
        base = None
        for t in types:
            try:
                if _u(t.SystemClassification) == cls_name:
                    base = t
                    break
            except Exception:
                pass
        if base is None:
            log.append(u"✗ 系统类型 %s：项目里没有分类为 %s 的系统类型可以复制" % (st["name"], cls_name))
            continue
        nt = base.Duplicate(st["name"])
        rgb = st.get("rgb")
        if rgb:
            try:
                nt.LineColor = DB.Color(int(rgb[0]), int(rgb[1]), int(rgb[2]))
            except Exception:
                pass
        made += 1
    log.append(u"系统类型：新建 %d 个" % made)


# ============================================================
# 流程
# ============================================================
class _Fails(DB.IFailuresPreprocessor):
    def PreprocessFailures(self, acc):
        err = False
        for f in acc.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                acc.DeleteWarning(f)
            else:
                err = True
        return DB.FailureProcessingResult.ProceedWithRollBack if err else DB.FailureProcessingResult.Continue


def preview(cfg, ops):
    fid = existing_filters()
    lines = []
    if u"filters" in ops:
        need = [s["name"] for s in cfg["systems"] if s["name"] not in fid] + [h["name"] for h in cfg.get("hides", []) if h["name"] not in fid]
        lines.append(u"过滤器：新建 %d 个，已有 %d 个（保留）。" % (len(need), len(cfg["systems"]) + len(cfg.get("hides", [])) - len(need)))
    if u"templates" in ops:
        news = [t["name"] for t in cfg["templates"] if find_template(t["name"]) is None]
        lines.append(u"视图样板：新建 %d 个，刷新 %d 个（刷新 = 按配置重设显示类别和过滤器，样板上手工加的其他设置不动）。" % (len(news), len(cfg["templates"]) - len(news)))
    if u"types" in ops:
        n = 0
        for st in cfg.get("system_types", []):
            if not any(_u(t.Name) == st["name"] for t in _system_types(st["kind"])):
                n += 1
        lines.append(u"系统类型：新建 %d 个（复制同分类的现有系统类型）。" % n)
    return lines


def run(cfg, ops):
    log = []
    t = DB.Transaction(doc, TITLE)
    o = t.GetFailureHandlingOptions()
    o.SetFailuresPreprocessor(_Fails())
    t.SetFailureHandlingOptions(o)
    t.Start()
    try:
        if u"types" in ops:
            build_system_types(cfg, log)
        fid = ensure_filters(cfg, log) if (u"filters" in ops or u"templates" in ops) else {}
        if u"templates" in ops:
            build_templates(cfg, fid, log)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise
    return log


def main():
    try:
        cfg, from_file = load_config()
        choices = [u"建过滤器 + 视图样板（推荐）", u"只建系统过滤器", u"只建系统类型", u"全部（系统类型 + 过滤器 + 视图样板）", u"把默认配置表写到文档里（先改再用）"]
        pick = forms.CommandSwitchWindow.show(choices, message=u"配置：%s" % (u"读自 " + CFG_PATH if from_file else u"内置默认表（还没有配置文件）"))
        if pick is None:
            return
        if pick == choices[4]:
            if os.path.isfile(CFG_PATH) and not forms.alert(u"配置文件已存在，覆盖会丢掉你的修改。\n%s\n覆盖吗？" % CFG_PATH, title=TITLE, yes=True, no=True):
                return
            write_default()
            forms.alert(u"已写出：\n%s\n用记事本改好保存，再运行本插件。" % CFG_PATH, title=TITLE)
            return
        ops = {choices[0]: [u"filters", u"templates"], choices[1]: [u"filters"], choices[2]: [u"types"], choices[3]: [u"types", u"filters", u"templates"]}[pick]
        lines = preview(cfg, ops)
        lines.append(u"\n确认开始？（整次可 Ctrl+Z 撤销）")
        if not forms.alert(u"\n".join(lines), title=TITLE + u" — 预览（还没改）", yes=True, no=True):
            return
        log = run(cfg, ops)
        forms.alert(u"完成：\n" + u"\n".join(log[:40]), title=TITLE)
    except Exception as ex:
        forms.alert(u"出错了（已回滚）：\n%s\n\n%s" % (ex, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
