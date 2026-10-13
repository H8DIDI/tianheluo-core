# -*- coding: utf-8 -*-
"""View + view-template collection, querying, bulk-edit and JSON transfer.

The Revit-touching helpers take `doc` and run inside a transaction supplied
by the caller. The JSON-matching core (`match_controlled`, `match_categories`,
`merge_report`) is plain Python so it can be unit-tested without Revit.

View templates are just Views with IsTemplate == True; their "controlled"
parameters are GetTemplateParameterIds() minus
GetNonControlledTemplateParameterIds(). Editing a value a template controls
silently no-ops on the governed view, so callers check
template_controls_param() and warn instead of pretending it worked.
"""

import json
import codecs

from pyrevit import DB

# BuiltInParameters for the three bulk-editable view fields. Their ids are
# negative and identical in every Revit model, so they're portable in JSON.
P_SCALE = DB.BuiltInParameter.VIEW_SCALE
P_DETAIL = DB.BuiltInParameter.VIEW_DETAIL_LEVEL
P_DISCIPLINE = DB.BuiltInParameter.VIEW_DISCIPLINE

DETAIL_LEVELS = ["Coarse", "Medium", "Fine"]
DISCIPLINES = ["Architectural", "Structural", "Mechanical", "Electrical",
               "Plumbing", "Coordination"]

# View types that are really UI scaffolding, not user drawing views.
_SKIP_TYPES = set([
    DB.ViewType.ProjectBrowser, DB.ViewType.SystemBrowser,
    DB.ViewType.Internal, DB.ViewType.Undefined, DB.ViewType.DrawingSheet,
])


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------
def _all_views(doc):
    return (DB.FilteredElementCollector(doc)
              .OfClass(DB.View)
              .WhereElementIsNotElementType()
              .ToElements())


def collect_views(doc):
    """User views (not templates, not browsers/sheets), sorted by type+name."""
    out = []
    for v in _all_views(doc):
        try:
            if v.IsTemplate or v.ViewType in _SKIP_TYPES:
                continue
        except Exception:
            continue
        out.append(v)
    return sorted(out, key=lambda v: (view_type_name(v), _safe_name(v)))


def collect_templates(doc):
    """All view templates, sorted by type then name."""
    out = []
    for v in _all_views(doc):
        try:
            if v.IsTemplate:
                out.append(v)
        except Exception:
            pass
    return sorted(out, key=lambda v: (view_type_name(v), _safe_name(v)))


def _safe_name(v):
    try:
        return DB.Element.Name.GetValue(v)
    except Exception:
        try:
            return v.Name
        except Exception:
            return ""


def view_type_name(v):
    try:
        return str(v.ViewType)
    except Exception:
        return "View"


# ---------------------------------------------------------------------------
# Field read helpers
# ---------------------------------------------------------------------------
def scale_of(v):
    try:
        return v.Scale
    except Exception:
        return 0


def detail_of(v):
    try:
        return str(v.DetailLevel)
    except Exception:
        return ""


def discipline_of(v):
    try:
        return str(v.Discipline)
    except Exception:
        return ""


def template_name(doc, v):
    """Name of the template applied to a view, or '' if none."""
    try:
        tid = v.ViewTemplateId
        if tid and tid != DB.ElementId.InvalidElementId:
            t = doc.GetElement(tid)
            return _safe_name(t) if t else ""
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Placement (which sheet a view sits on)
# ---------------------------------------------------------------------------
def sheet_of_view(doc):
    """Map view-id (Int64) -> sheet number for every placed view/schedule."""
    placed = {}
    for vp in (DB.FilteredElementCollector(doc)
                 .OfClass(DB.Viewport).ToElements()):
        try:
            sheet = doc.GetElement(vp.SheetId)
            if sheet is not None:
                placed[vp.ViewId.Value] = sheet.SheetNumber
        except Exception:
            pass
    for si in (DB.FilteredElementCollector(doc)
                 .OfClass(DB.ScheduleSheetInstance).ToElements()):
        try:
            sheet = doc.GetElement(si.OwnerViewId)
            if isinstance(sheet, DB.ViewSheet):
                placed[si.ScheduleId.Value] = sheet.SheetNumber
        except Exception:
            pass
    return placed


# ---------------------------------------------------------------------------
# Template usage + controlled parameters
# ---------------------------------------------------------------------------
def template_usage(doc):
    """Map template-id (Int64) -> count of views currently using it."""
    counts = {}
    for v in _all_views(doc):
        try:
            if v.IsTemplate:
                continue
            tid = v.ViewTemplateId
            if tid and tid != DB.ElementId.InvalidElementId:
                counts[tid.Value] = counts.get(tid.Value, 0) + 1
        except Exception:
            pass
    return counts


def controlled_param_ids(template):
    """ElementIds the template controls (the ticked 'include' boxes)."""
    try:
        allp = set(p.Value for p in template.GetTemplateParameterIds())
        off = set(p.Value for p in template.GetNonControlledTemplateParameterIds())
        return allp - off
    except Exception:
        return set()


def template_controls_param(doc, view, builtin_param):
    """True if the view's applied template governs builtin_param."""
    try:
        tid = view.ViewTemplateId
        if not tid or tid == DB.ElementId.InvalidElementId:
            return False
        template = doc.GetElement(tid)
        pid = DB.ElementId(builtin_param).Value
        return pid in controlled_param_ids(template)
    except Exception:
        return False


def param_label(doc, eid):
    """Human label for a (possibly built-in) parameter ElementId."""
    try:
        v = eid.Value
        if v < 0:
            return _bip_label(v)         # built-in: portable name
        el = doc.GetElement(eid)         # project/shared param element
        return _safe_name(el) if el else "param {0}".format(v)
    except Exception:
        return "param"


def _bip_label(value):
    # map the negative id back to the BuiltInParameter name
    for name in dir(DB.BuiltInParameter):
        try:
            if DB.ElementId(getattr(DB.BuiltInParameter, name)).Value == value:
                return name
        except Exception:
            continue
    return "param {0}".format(value)


# ---------------------------------------------------------------------------
# Category visibility (top-level categories only)
# ---------------------------------------------------------------------------
def model_categories(doc):
    """Visible-in-UI categories that can be toggled, name -> Category."""
    out = {}
    try:
        for cat in doc.Settings.Categories:
            try:
                if cat.get_AllowsVisibilityControl is not None:
                    pass
            except Exception:
                pass
            try:
                name = cat.Name
                out[name] = cat
            except Exception:
                pass
    except Exception:
        pass
    return out


def category_visibility(doc, view):
    """Map category name -> hidden(bool) for a view/template."""
    vis = {}
    for name, cat in model_categories(doc).items():
        try:
            if view.CanCategoryBeHidden(cat.Id):
                vis[name] = view.GetCategoryHidden(cat.Id)
        except Exception:
            pass
    return vis


# ---------------------------------------------------------------------------
# JSON transfer
# ---------------------------------------------------------------------------
def export_templates(doc, templates):
    """Build the JSON-serialisable payload for a list of templates."""
    out = []
    for t in templates:
        controlled = []
        for eid in controlled_param_ids_as_ids(t):
            controlled.append({"id": eid.Value, "name": param_label(doc, eid)})
        out.append({
            "name": _safe_name(t),
            "view_type": view_type_name(t),
            "scale": scale_of(t),
            "detail_level": detail_of(t),
            "discipline": discipline_of(t),
            "controlled": controlled,
            "categories": category_visibility(doc, t),
        })
    return {"sendit_templates": out, "version": 1}


def controlled_param_ids_as_ids(template):
    try:
        allp = list(template.GetTemplateParameterIds())
        off = set(p.Value for p in template.GetNonControlledTemplateParameterIds())
        return [p for p in allp if p.Value not in off]
    except Exception:
        return []


def write_json(path, payload):
    with codecs.open(path, "w", "utf-8") as f:
        f.write(json.dumps(payload, indent=1))


def read_json(path):
    with codecs.open(path, "r", "utf-8") as f:
        return json.load(f)


# ---- pure matching core (unit-testable, no Revit) ----
def match_controlled(source_controlled, target_index):
    """Resolve exported controlled-params against a target model.

    Args:
        source_controlled: list of {"id": int, "name": str}
        target_index: {"builtins": set(negative_ids),
                       "by_name": {name: id_int}}
    Returns:
        (matched_ids, unmatched_names)
    """
    matched, unmatched = [], []
    for item in source_controlled:
        sid = item.get("id")
        name = item.get("name", "")
        if sid is not None and sid < 0 and sid in target_index["builtins"]:
            matched.append(sid)
        elif name in target_index["by_name"]:
            matched.append(target_index["by_name"][name])
        else:
            unmatched.append(name or str(sid))
    return matched, unmatched


def match_categories(source_categories, target_names):
    """Split exported category map into appliable vs missing.

    Returns:
        (appliable {name: hidden}, missing [names])
    """
    appliable, missing = {}, []
    for name, hidden in source_categories.items():
        if name in target_names:
            appliable[name] = hidden
        else:
            missing.append(name)
    return appliable, missing


def merge_report(name, matched_params, unmatched_params,
                 applied_cats, missing_cats):
    """One human-readable import line per template."""
    bits = ["'{0}': {1} params, {2} categories".format(
        name, len(matched_params), len(applied_cats))]
    if unmatched_params:
        bits.append("{0} param(s) skipped ({1})".format(
            len(unmatched_params), ", ".join(unmatched_params[:4])))
    if missing_cats:
        bits.append("{0} category(ies) not in this model".format(
            len(missing_cats)))
    return "; ".join(bits)
