# -*- coding: utf-8 -*-
"""Shared find & replace for Sheet Manager and View Manager.

Both tools edit a staged grid: nothing is written to Revit until the user clicks 'Apply edits'.
This module only changes the staged values on the row objects, so the calling tool keeps its own
apply logic. Works in IronPython 2.7 and CPython 3 (no f-strings)."""

from sendit import dialogs


def replace_in_rows(rows, find, repl, attrs):
    """Replace `find` with `repl` in each row's staged attributes.

    rows:  objects with the attributes listed in `attrs` (strings, read and written as properties)
    attrs: e.g. ["Name"] or ["Number", "Name"]
    Returns the number of attribute values that changed."""
    changed = 0
    for r in rows:
        for a in attrs:
            old = getattr(r, a) or ""
            new = old.replace(find, repl)
            if new != old:
                setattr(r, a, new)
                changed += 1
    return changed


def ask_find_replace(rows, noun, owner=None, scopes=None, default_scope=None):
    """Ask for the find / replace text (and the scope if `scopes` is given).

    noun:   what the rows are, for the title, e.g. "sheet(s)" or "view name(s)"
    scopes: optional list of choices for "Apply to", e.g. ["Number", "Name", "Both"]
    Returns a dict {"find": str, "repl": str, "scope": str or None}, or None if cancelled / empty find."""
    fields = [{"key": "find", "label": "Find"},
              {"key": "repl", "label": "Replace with"}]
    if scopes:
        fields.append({"key": "scope", "label": "Apply to", "type": "choice",
                       "choices": list(scopes), "default": default_scope or scopes[0]})
    vals = dialogs.ask_form(
        "Find / Replace in {0} {1}".format(len(rows), noun),
        fields,
        owner=owner,
        message="Changes are staged in the grid - nothing is written until you click 'Apply edits'.")
    if not vals or not vals.get("find"):
        return None
    vals.setdefault("scope", None)
    return vals


def scope_attrs(scope, mapping):
    """Map a scope choice to attribute names. mapping: {"Number": ["Number"], "Name": ["Name"], "Both": [...]}"""
    if scope is None:
        return []
    return list(mapping.get(scope, []))
