# -*- coding: utf-8 -*-
"""Sheet collection, filename templating and PDF export helpers."""

import re

from pyrevit import DB

from System import DateTime
from System.Collections.Generic import List

INVALID_FILECHARS = re.compile(r'[<>:"/\\|?*]')


def get_name(el):
    try:
        return DB.Element.Name.GetValue(el)
    except Exception:
        try:
            return el.Name
        except Exception:
            return ""


def collect_sheets(doc):
    sheets = (DB.FilteredElementCollector(doc)
                .OfCategory(DB.BuiltInCategory.OST_Sheets)
                .WhereElementIsNotElementType()
                .ToElements())
    sheets = [s for s in sheets if isinstance(s, DB.ViewSheet)]
    return sorted(sheets, key=lambda s: s.SheetNumber)


def get_current_rev(sheet):
    p = sheet.get_Parameter(DB.BuiltInParameter.SHEET_CURRENT_REVISION)
    return p.AsString() if p else ""


def view_count(sheet):
    try:
        return len(list(sheet.GetAllPlacedViews()))
    except Exception:
        return 0


def sanitize(name):
    return INVALID_FILECHARS.sub("_", (name or "").strip()) or "sheet"


def render_template(doc, sheet, template, extra_tokens=None):
    """Expand filename tokens for one sheet.

    Tokens: {number} {name} {rev} {project} {date} plus any extra_tokens
    (e.g. {issue} during an issue run).
    """
    tokens = {
        "{number}":  sheet.SheetNumber,
        "{name}":    sheet.Name,
        "{rev}":     get_current_rev(sheet),
        "{project}": doc.Title,
        "{date}":    DateTime.Now.ToString("yyyy-MM-dd"),
    }
    if extra_tokens:
        tokens.update(extra_tokens)
    out = template
    for k, v in tokens.items():
        out = out.replace(k, v or "")
    return sanitize(out)


def export_pdf(doc, sheets, folder, template=None, combine=False,
               combined_name=None, extra_tokens=None):
    """Export sheets to PDF (native Revit 2022+ PDF export).

    Returns:
        (exported_files, failures): list of created file names and a list of
        per-sheet error strings.
    """
    opts = DB.PDFExportOptions()
    opts.HideCropBoundaries = True
    opts.HideScopeBoxes = True
    opts.HideReferencePlane = True
    opts.HideUnreferencedViewTags = True
    opts.MaskCoincidentLines = True

    done, fail = [], []
    if combine:
        opts.Combine = True
        fname = sanitize(combined_name)
        opts.FileName = fname
        ids = List[DB.ElementId]([s.Id for s in sheets])
        try:
            if doc.Export(folder, ids, opts):
                done.append(fname + ".pdf")
            else:
                fail.append("combined export returned False")
        except Exception as ex:
            fail.append(str(ex))
    else:
        opts.Combine = True  # FileName is only honored when Combine is True
        for s in sheets:
            fname = render_template(doc, s, template, extra_tokens)
            opts.FileName = fname
            ids = List[DB.ElementId]([s.Id])
            try:
                if doc.Export(folder, ids, opts):
                    done.append(fname + ".pdf")
                else:
                    fail.append("{0}: returned False".format(s.SheetNumber))
            except Exception as ex:
                fail.append("{0}: {1}".format(s.SheetNumber, ex))
    return done, fail


def export_dwg(doc, sheets, folder, template=None, extra_tokens=None,
               options=None):
    """Export each sheet to its own DWG, named by the same template tokens
    as export_pdf so PDF/DWG filenames match.

    Args:
        options: a DB.DWGExportOptions, e.g. from a saved export setup
            (ExportDWGSettings.GetDWGExportOptions()); default merges the
            views on each sheet into a single file instead of xrefs.

    Returns:
        (exported_files, failures)
    """
    opts = options
    if opts is None:
        opts = DB.DWGExportOptions()
        try:
            opts.MergedViews = True  # no per-viewport xref DWGs
        except Exception:
            pass
    done, fail = [], []
    for s in sheets:
        fname = render_template(doc, s, template, extra_tokens)
        ids = List[DB.ElementId]([s.Id])
        try:
            if doc.Export(folder, fname, ids, opts):
                done.append(fname + ".dwg")
            else:
                fail.append("{0}: returned False".format(s.SheetNumber))
        except Exception as ex:
            fail.append("{0}: {1}".format(s.SheetNumber, ex))
    return done, fail
