# ---- compat2020（天河落共用片段，打包时内联进脚本）--------------------------------
# 只给「要在 Revit 2020 跑」的插件用的补丁（和 compat.py 分开，免得改 compat 牵动所有插件）。
# 都是按 API 在不在判断，不按年份。
from Autodesk.Revit import DB as _c2DB


def thl2_is_length(p):
    """参数是不是长度类型（2022+ 用 GetDataType，更早用 ParameterType）。"""
    d = p.Definition
    if hasattr(d, 'GetDataType'):
        try:
            return d.GetDataType() == _c2DB.SpecTypeId.Length
        except Exception:
            return False
    try:
        return d.ParameterType == _c2DB.ParameterType.Length
    except Exception:
        return False


def thl2_bar_dia(t):
    """钢筋类型的公称直径（ft）：新版 BarNominalDiameter，2020 是 BarDiameter。"""
    if t is None:
        return 0.0
    try:
        return t.BarNominalDiameter
    except AttributeError:
        return t.BarDiameter


def thl2_base_point(doc, survey=False):
    """项目基点 / 测量点：新版有静态方法，2020 要从图元里找。"""
    if hasattr(_c2DB.BasePoint, 'GetProjectBasePoint'):
        return _c2DB.BasePoint.GetSurveyPoint(doc) if survey else _c2DB.BasePoint.GetProjectBasePoint(doc)
    cat = _c2DB.BuiltInCategory.OST_SharedBasePoint if survey else _c2DB.BuiltInCategory.OST_ProjectBasePoint
    for e in _c2DB.FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType():
        return e
    return None
def thl2_tagged_ids(t):
    """标记指向的本文档构件 id 列表（2022+ 一个标记可指多个，更早只有一个）。"""
    if hasattr(t, 'GetTaggedLocalElementIds'):
        return list(t.GetTaggedLocalElementIds())
    try:
        i = t.TaggedLocalElementId
        return [] if i is None or i == _c2DB.ElementId.InvalidElementId else [i]
    except Exception:
        return []


def thl2_tagged_refs(t):
    if hasattr(t, 'GetTaggedReferences'):
        return list(t.GetTaggedReferences())
    try:
        r = t.GetTaggedReference()
        return [] if r is None else [r]
    except Exception:
        return []


def thl2_leader_end(t, ref):
    """引线端点：新版按参照取，2020 是标记自己的 LeaderEnd 属性。"""
    if hasattr(t, 'GetLeaderEnd'):
        return t.GetLeaderEnd(ref)
    return t.LeaderEnd


def thl2_set_leader_end(t, ref, pt):
    if hasattr(t, 'SetLeaderEnd'):
        t.SetLeaderEnd(ref, pt)
    else:
        t.LeaderEnd = pt

def thl2_create_tag(doc, ttid, view, ref, add_leader, orient, pt):
    """新建标记：2023+ 的 Create 带标记类型 id，更早的要 TagMode，建完再换类型。"""
    try:
        return _c2DB.IndependentTag.Create(doc, ttid, view.Id, ref, add_leader, orient, pt)
    except TypeError:
        tag = _c2DB.IndependentTag.Create(doc, view.Id, ref, add_leader, _c2DB.TagMode.TM_ADDBY_CATEGORY, orient, pt)
        try:
            if ttid is not None and ttid != _c2DB.ElementId.InvalidElementId:
                tag.ChangeTypeId(ttid)
        except Exception:
            pass
        return tag


# ---- compat2020 结束 ----------------------------------------------------------------
