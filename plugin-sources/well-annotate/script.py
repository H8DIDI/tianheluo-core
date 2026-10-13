# -*- coding: utf-8 -*-
__title__ = '一键井大样标注'
__doc__ = '选中井/图纸 → 勾选要标注的视图 → 按视图内可见构件（不含管道）的外包框生成总尺寸标注（下方标宽、左侧标高）。'

from pyrevit import revit, DB, UI, forms
import traceback

doc = revit.doc
uidoc = revit.uidoc

# ============================================================
# 配置
# ============================================================

# 不计入包围盒的类别：注释、标记、底图、参照
_EXCLUDE_CATS = [
    DB.BuiltInCategory.OST_Dimensions,
    DB.BuiltInCategory.OST_TextNotes,
    DB.BuiltInCategory.OST_Levels,
    DB.BuiltInCategory.OST_Grids,
    DB.BuiltInCategory.OST_Sections,
    DB.BuiltInCategory.OST_Viewers,
    DB.BuiltInCategory.OST_Cameras,
    DB.BuiltInCategory.OST_RoomTags,
    DB.BuiltInCategory.OST_SpotElevations,
    DB.BuiltInCategory.OST_SpotCoordinates,
    DB.BuiltInCategory.OST_SpotSlopes,
    DB.BuiltInCategory.OST_CLines,
    DB.BuiltInCategory.OST_Constraints,
    DB.BuiltInCategory.OST_Matchline,
    DB.BuiltInCategory.OST_RasterImages,
    DB.BuiltInCategory.OST_ScheduleGraphics,
    DB.BuiltInCategory.OST_ColorFillSchema,
    DB.BuiltInCategory.OST_AreaTags,
    DB.BuiltInCategory.OST_MassTags,
    DB.BuiltInCategory.OST_MaterialTags,
    DB.BuiltInCategory.OST_GenericAnnotation,
    DB.BuiltInCategory.OST_TitleBlocks,
]

# 管道类：按口径「不取管道」，一律不计入包围盒。
# 注意 OST_StructuralFraming —— 本项目用结构框架族承载 HDPE 排管，
# 那根 64 英尺长的排管正是这个类别，必须排除；真实结构梁/柱在井大样里
# 通常也远超视图框，会被下面的跨度保险再筛一次。
_PIPE_CATS = [
    DB.BuiltInCategory.OST_PipeCurves,
    DB.BuiltInCategory.OST_PipeFitting,
    DB.BuiltInCategory.OST_PipeAccessory,
    DB.BuiltInCategory.OST_PipeInsulations,
    DB.BuiltInCategory.OST_FlexPipeCurves,
    DB.BuiltInCategory.OST_DuctCurves,
    DB.BuiltInCategory.OST_DuctFitting,
    DB.BuiltInCategory.OST_DuctAccessory,
    DB.BuiltInCategory.OST_FlexDuctCurves,
    DB.BuiltInCategory.OST_CableTray,
    DB.BuiltInCategory.OST_CableTrayFitting,
    DB.BuiltInCategory.OST_Conduit,
    DB.BuiltInCategory.OST_ConduitFitting,
    DB.BuiltInCategory.OST_StructuralFraming,
    DB.BuiltInCategory.OST_StructuralFramingSystem,
    DB.BuiltInCategory.OST_StructuralColumns,
    DB.BuiltInCategory.OST_StructuralFoundation,
]

# 跨度保险：构件在视图平面上的尺寸超过整个视图框这个倍数时判为横穿长构件，剔除
_SPAN_RATIO = 2.0

# 尺寸线离框的间距（英尺）
_DIM_MARGIN = 1.0

# 严格轴向判据（法线分量阈值，0.995 约等于偏角 5.7°）
_AXIAL_TOL = 0.995


def _ints(cats):
    out = []
    for c in cats:
        try:
            out.append(int(c))
        except:
            pass
    return out


_EXCLUDE_IDS = _ints(_EXCLUDE_CATS)
_PIPE_IDS = _ints(_PIPE_CATS)


# ============================================================
# 面提取（只取严格轴向面，天然互相平行，可直接用于标注）
# ============================================================


def _extract_faces(elem):
    """提取构件所有面引用，带世界法线与原点。非平面取 UV 中心代表值。"""
    opt = DB.Options()
    opt.ComputeReferences = True
    opt.IncludeNonVisibleObjects = False
    geom = elem.get_Geometry(opt)
    faces = []
    if not geom:
        return faces

    def _rep(f):
        try:
            if isinstance(f, DB.PlanarFace):
                return (f.FaceNormal, f.Origin)
        except:
            return None
        try:
            uvbox = f.GetBoundingBox()
            u = (uvbox.Min.U + uvbox.Max.U) / 2.0
            v = (uvbox.Min.V + uvbox.Max.V) / 2.0
            uv = DB.UV(u, v)
            return (f.ComputeNormal(uv), f.Evaluate(uv))
        except:
            return None

    def _walk(ge, tr):
        for obj in ge:
            if isinstance(obj, DB.GeometryInstance):
                _walk(obj.GetSymbolGeometry(), tr.Multiply(obj.Transform))
            elif hasattr(obj, 'Faces') and obj.Faces.Size > 0:
                for f in obj.Faces:
                    if f.Reference is None:
                        continue
                    rep = _rep(f)
                    if rep is None:
                        continue
                    faces.append({
                        'ref': f.Reference,
                        'norm': tr.OfVector(rep[0]),
                        'orig': tr.OfPoint(rep[1]),
                    })

    _walk(geom, DB.Transform.Identity)
    return faces


def _axial_faces(elem, axis):
    """构件的严格轴向面，返回 [(坐标值, face_dict), ...]，按轴排序去重。"""
    out = []
    seen = []
    for f in _extract_faces(elem):
        n = f['norm']
        v = abs(n.X) if axis == 'X' else (abs(n.Y) if axis == 'Y' else abs(n.Z))
        if v < _AXIAL_TOL:
            continue
        if axis == 'X':
            c = f['orig'].X
        elif axis == 'Y':
            c = f['orig'].Y
        else:
            c = f['orig'].Z
        dup = False
        for s in seen:
            if abs(s - c) < 0.005:
                dup = True
                break
        if dup:
            continue
        seen.append(c)
        out.append((c, f))
    out.sort(key=lambda t: t[0])
    return out


# ============================================================
# 视图内构件与包围盒
# ============================================================


def crop_world_range(view):
    """视图裁剪框的世界坐标范围 (x0,x1, y0,y1, z0,z1)。

    必须换算：CropBox.Min/Max 是视图局部坐标，而 get_BoundingBox(view)
    实测返回的是模型坐标，两者不可直接比较。
    做法是把裁剪框 8 个角点经 CropBox.Transform 转到世界后取包络。
    """
    try:
        cb = view.CropBox
    except:
        return None
    if cb is None:
        return None
    try:
        xs = []
        ys = []
        zs = []
        for a in (cb.Min.X, cb.Max.X):
            for b in (cb.Min.Y, cb.Max.Y):
                for c in (cb.Min.Z, cb.Max.Z):
                    p = cb.Transform.OfPoint(DB.XYZ(a, b, c))
                    xs.append(p.X)
                    ys.append(p.Y)
                    zs.append(p.Z)
        return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
    except:
        return None


def collect_target_elems(view):
    """收集视图内应计入包围盒的可见模型构件（排除注释、CAD 底图、管道类）。"""
    out = []
    try:
        col = DB.FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType().ToElements()
    except:
        return out

    rng = crop_world_range(view)
    if rng:
        crop_w = rng[1] - rng[0]
        crop_h = rng[5] - rng[4]
    else:
        crop_w = crop_h = 0.0

    for e in col:
        try:
            cat = e.Category
            if cat is None:
                continue
            cid = cat.Id.Value
            if cid in _EXCLUDE_IDS or cid in _PIPE_IDS:
                continue
            try:
                if e.IsHidden(view):
                    continue
            except:
                pass
            bb = e.get_BoundingBox(view)
            if bb is None:
                continue
            w = abs(bb.Max.X - bb.Min.X)
            h = abs(bb.Max.Z - bb.Min.Z)
            if w <= 0 and h <= 0:
                continue
            if crop_w > 0 and crop_h > 0:
                if w > crop_w * _SPAN_RATIO or h > crop_h * _SPAN_RATIO:
                    continue
            out.append(e)
        except:
            continue
    return out


def union_box(view, elems):
    """构件在模型坐标下的联合包围盒，返回 (x0,x1, y0,y1, z0,z1)。"""
    x0 = x1 = y0 = y1 = z0 = z1 = None
    for e in elems:
        try:
            bb = e.get_BoundingBox(view)
        except:
            continue
        if bb is None:
            continue
        if x0 is None:
            x0, x1 = bb.Min.X, bb.Max.X
            y0, y1 = bb.Min.Y, bb.Max.Y
            z0, z1 = bb.Min.Z, bb.Max.Z
        else:
            x0 = min(x0, bb.Min.X)
            x1 = max(x1, bb.Max.X)
            y0 = min(y0, bb.Min.Y)
            y1 = max(y1, bb.Max.Y)
            z0 = min(z0, bb.Min.Z)
            z1 = max(z1, bb.Max.Z)
    if x0 is None:
        return None
    return (x0, x1, y0, y1, z0, z1)


# ============================================================
# 生成外包总尺寸
# ============================================================


def _try_dim(view, ref_a, ref_b, world_p1, world_p2):
    """两个面引用之间建一条尺寸。"""
    if view is None or ref_a is None or ref_b is None:
        return None
    try:
        arr = DB.ReferenceArray()
        arr.Append(ref_a)
        arr.Append(ref_b)
        if arr.Size < 2:
            return None
        line = DB.Line.CreateBound(world_p1, world_p2)
        return view.Document.Create.NewDimension(view, line, arr)
    except:
        return None


def make_total_dims(view, elems, box):
    """按外包框生成总尺寸：下方一道宽度、左侧一道高度。

    轴的选择随视图类型走：
      平面视图  横向=世界X，竖向=世界Y，尺寸线摆在某个 Z 深度上
      剖面视图  横向=世界X，竖向=世界Z，尺寸线摆在某个 Y 深度上
    参照面取"沿该轴最外侧的严格轴向面"；跨多个构件时取全局极值，
    标出来的就是整个外包框的总尺寸。
    """
    made = 0
    x0, x1, y0, y1, z0, z1 = box
    m = _DIM_MARGIN

    try:
        is_plan = (view.ViewType == DB.ViewType.FloorPlan)
    except:
        is_plan = False

    if is_plan:
        v_axis = 'Y'
        v0, v1 = y0, y1
        depth = (z0 + z1) / 2.0
    else:
        v_axis = 'Z'
        v0, v1 = z0, z1
        depth = (y0 + y1) / 2.0

    def _wpt(a, b, c):
        return DB.XYZ(a, b, c)

    # ---- 宽度（下方）：X 方向最外两侧 ----
    west = east = None
    for e in elems:
        fl = _axial_faces(e, 'X')
        if not fl:
            continue
        c0, f0 = fl[0]
        c1, f1 = fl[-1]
        if west is None or c0 < west[0]:
            west = (c0, f0)
        if east is None or c1 > east[0]:
            east = (c1, f1)
    if west and east:
        if is_plan:
            p1 = _wpt(x0 - m, v0 - m, depth)
            p2 = _wpt(x1 + m, v0 - m, depth)
        else:
            p1 = _wpt(x0 - m, depth, v0 - m)
            p2 = _wpt(x1 + m, depth, v0 - m)
        if _try_dim(view, west[1]['ref'], east[1]['ref'], p1, p2):
            made += 1

    # ---- 高度（左侧）：竖向最外两侧 ----
    lo = hi = None
    for e in elems:
        fl = _axial_faces(e, v_axis)
        if not fl:
            continue
        c0, f0 = fl[0]
        c1, f1 = fl[-1]
        if lo is None or c0 < lo[0]:
            lo = (c0, f0)
        if hi is None or c1 > hi[0]:
            hi = (c1, f1)
    if lo and hi:
        if is_plan:
            p1 = _wpt(x0 - m, v0 - m, depth)
            p2 = _wpt(x0 - m, v1 + m, depth)
        else:
            p1 = _wpt(x0 - m, depth, v0 - m)
            p2 = _wpt(x0 - m, depth, v1 + m)
        if _try_dim(view, lo[1]['ref'], hi[1]['ref'], p1, p2):
            made += 1

    return made


def purge_dims(view):
    """删除该视图内全部尺寸标注。"""
    n = 0
    try:
        for d in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Dimension).ToElements():
            try:
                doc.Delete(d.Id)
                n += 1
            except:
                pass
    except:
        pass
    return n


# ============================================================
# 入口
# ============================================================


class _SilentFailures(DB.IFailuresPreprocessor):
    """抑制标注引发的失败消息；绝不自己删元素。"""

    def PreprocessFailures(self, failuresAccessor):
        for f in failuresAccessor.GetFailureMessages():
            try:
                sev = f.GetSeverity()
            except:
                continue
            if sev == DB.FailureSeverity.Warning:
                try:
                    failuresAccessor.DeleteWarning(f)
                except:
                    pass
                continue
            try:
                failuresAccessor.ResolveFailure(f)
            except:
                pass
        return DB.FailureProcessingResult.Continue


def _sheet_of(e):
    if e is None:
        return None
    if isinstance(e, DB.ViewSheet):
        return e
    try:
        if e.OwnerViewId and e.OwnerViewId != DB.ElementId.InvalidElementId:
            return doc.GetElement(e.OwnerViewId)
    except:
        pass
    return None


def _mark_of(e):
    try:
        p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        return p.AsString().strip() if (p and p.AsString()) else None
    except:
        return None


def _sheet_by_mark(mark):
    target = "井大样_" + mark
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
        try:
            if (s.Name or "") == target:
                return s
        except:
            pass
    return None


def _views_of(sheet):
    """取图纸上的视图，按 平面/南/北/西/东 排序。"""
    out = []
    order = {'平面': 0, '南': 1, '北': 2, '西': 3, '东': 4}
    try:
        vps = DB.FilteredElementCollector(doc, sheet.Id).OfClass(DB.Viewport).ToElements()
    except:
        return out
    for vp in vps:
        try:
            v = doc.GetElement(vp.ViewId)
        except:
            continue
        if v is None or v.IsTemplate:
            continue
        nm = v.Name or ""
        k = 9
        for pref in order:
            if nm.startswith(pref + "_"):
                k = order[pref]
                break
        out.append((k, nm, v))
    out.sort(key=lambda t: t[0])
    return out


class _ViewOption(forms.TemplateListItem):
    """视图选项：显示 图纸 / 类型 / 视图名。"""

    @property
    def name(self):
        try:
            info = self.item
        except:
            return str(self.item)
        try:
            sheet_nm, tcode, v = info
        except:
            return str(info)
        try:
            return "{}   [{}]   {}".format(sheet_nm, tcode, v.Name)
        except:
            return "{}   [{}]".format(sheet_nm, tcode)


def main():
    try:
        # ---------- 1. 收集目标图纸 ----------
        sheets = []
        try:
            sel = list(uidoc.Selection.GetElementIds())
        except:
            sel = []

        for eid in sel:
            e = doc.GetElement(eid)
            if e is None:
                continue
            sh = _sheet_of(e)
            if sh is not None and (sh.Name or "").startswith("井大样_"):
                if sh not in sheets:
                    sheets.append(sh)
                continue
            mk = _mark_of(e)
            if mk:
                sh2 = _sheet_by_mark(mk)
                if sh2 is not None and sh2 not in sheets:
                    sheets.append(sh2)

        if not sheets:
            try:
                cur = doc.ActiveView
                if isinstance(cur, DB.ViewSheet) and (cur.Name or "").startswith("井大样_"):
                    sheets.append(cur)
            except:
                pass

        if not sheets:
            forms.alert('没有可处理的井大样图纸。\n请先选中井大样图纸（或模型里的井），或切换到井大样图纸上再运行。',
                        title='一键井大样标注')
            return

        # ---------- 2. 合并所有视图成一个总列表 ----------
        options = []
        for sh in sheets:
            for k, nm, v in _views_of(sh):
                options.append((k, (sh.Name, str(v.ViewType), v)))

        if not options:
            forms.alert('选中的图纸上没有可用视图。', title='一键井大样标注')
            return
        options.sort(key=lambda t: t[0])

        picked = forms.SelectFromList.show(
            [_ViewOption(o[1]) for o in options],
            title='选择要标注的视图（可多选；下方标宽、左侧标高）',
            button_name='开始标注',
            multiselect=True)
        if not picked:
            return
        if not isinstance(picked, (list, tuple)):
            picked = [picked]
        target_views = []
        for p in picked:
            try:
                target_views.append(p[2])
            except:
                pass
        if not target_views:
            return

        # ---------- 3. 执行 ----------
        t = DB.Transaction(doc, "井大样外包尺寸标注")
        fho = t.GetFailureHandlingOptions()
        fho.SetFailuresPreprocessor(_SilentFailures())
        fho.SetClearAfterRollback(True)
        t.SetFailureHandlingOptions(fho)
        t.Start()

        purged = 0
        made = 0
        ok_views = 0
        notes = []
        try:
            for v in target_views:
                try:
                    purged += purge_dims(v)

                    elems = collect_target_elems(v)
                    if not elems:
                        notes.append(('{}（无可用构件）'.format(v.Name), 0))
                        continue

                    box = union_box(v, elems)
                    if box is None:
                        notes.append(('{}（无法计算包围盒）'.format(v.Name), 0))
                        continue

                    n = make_total_dims(v, elems, box)
                    made += n
                    if n:
                        ok_views += 1
                        notes.append(('{}'.format(v.Name), n))
                    else:
                        notes.append(('{}（未生成尺寸）'.format(v.Name), 0))
                except Exception as ev:
                    notes.append(('{}（异常：{}）'.format(v.Name, ev), 0))
            t.Commit()
        except Exception as inner:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
            forms.alert('标注过程出错：\n' + str(inner) + '\n\n' + traceback.format_exc(),
                        title='一键井大样标注')
            return

        # ---------- 4. 报告 ----------
        msg = '外包尺寸标注完成！\n\n'
        msg += '• 处理视图：{} 个（成功 {} 个）\n'.format(len(target_views), ok_views)
        msg += '• 生成尺寸：{} 道\n'.format(made)
        msg += '• 清除旧尺寸：{} 道\n'.format(purged)
        bad = [n for n in notes if n[1] == 0]
        if bad:
            msg += '\n未生成尺寸的视图 {} 个：\n'.format(len(bad))
            for nm, _ in bad[:10]:
                msg += '    - {}\n'.format(nm)
            if len(bad) > 10:
                msg += '    ...（其余 {} 个同类）\n'.format(len(bad) - 10)
        forms.alert(msg, title='一键井大样标注')

    except System.Exception as e:
        forms.alert('发生严重系统错误:\n' + str(e) + '\n\n' + traceback.format_exc(), title='报错追踪')
    except Exception as e:
        forms.alert('发生严重Python错误:\n' + str(e) + '\n\n' + traceback.format_exc(), title='报错追踪')


if __name__ == '__main__':
    main()
