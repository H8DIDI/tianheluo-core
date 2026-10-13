# -*- coding: utf-8 -*-
"""Issue register CSV storage for the Issue Manager.

The register is a long-format CSV (one row per sheet per issue) stored in
the root of the archive folder. Long format appends cleanly; the history
matrix (sheets x issues) is pivoted from it on demand.
"""

import os
import codecs

REGISTER_FILENAME = "issue_register.csv"
HEADER = ["Date", "Time", "Issue", "IssuedTo", "Project",
          "SheetNumber", "SheetName", "Revision", "File", "Formats"]


def register_path(archive_folder):
    return os.path.join(archive_folder, REGISTER_FILENAME)


def _csv_field(value):
    text = u"{0}".format(value if value is not None else "")
    text = text.replace("\r", " ").replace("\n", " ")
    if '"' in text or "," in text:
        text = '"' + text.replace('"', '""') + '"'
    return text


def csv_line(fields):
    return u",".join(_csv_field(f) for f in fields)


def parse_csv_line(line):
    fields, cur, in_quotes = [], [], False
    i = 0
    while i < len(line):
        c = line[i]
        if in_quotes:
            if c == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    cur.append('"')
                    i += 1
                else:
                    in_quotes = False
            else:
                cur.append(c)
        else:
            if c == '"':
                in_quotes = True
            elif c == ',':
                fields.append(u"".join(cur))
                cur = []
            else:
                cur.append(c)
        i += 1
    fields.append(u"".join(cur))
    return fields


def append_rows(path, rows):
    """Append row lists to the register, writing the header if new.

    utf-8-sig so Excel opens it with correct encoding.
    """
    is_new = not os.path.exists(path)
    mode = "a" if not is_new else "w"
    with codecs.open(path, mode, "utf-8-sig" if is_new else "utf-8") as f:
        if is_new:
            f.write(csv_line(HEADER) + u"\r\n")
        for row in rows:
            f.write(csv_line(row) + u"\r\n")


def read_rows(path):
    """Read the register into a list of dicts (file order preserved)."""
    if not os.path.exists(path):
        return []
    rows = []
    with codecs.open(path, "r", "utf-8-sig") as f:
        header = None
        for line in f:
            line = line.rstrip("\r\n")
            if not line.strip():
                continue
            fields = parse_csv_line(line)
            if header is None:
                header = fields
                continue
            row = {}
            for i, name in enumerate(header):
                row[name] = fields[i] if i < len(fields) else u""
            rows.append(row)
    return rows


def issue_key(row):
    """Identity of one issue event: (date, issue name)."""
    return (row.get("Date", u""), row.get("Issue", u""))


def pivot(rows):
    """Pivot register rows into an issue matrix.

    Returns:
        (issues, sheets, cells) where
        issues -- list of (date, issue) in first-appearance order
        sheets -- sorted list of (number, name)
        cells  -- dict[(number, name)][(date, issue)] = revision
    """
    issues, seen_issues = [], set()
    sheets, seen_sheets = [], set()
    cells = {}
    for row in rows:
        ikey = issue_key(row)
        if ikey not in seen_issues:
            seen_issues.add(ikey)
            issues.append(ikey)
        skey = (row.get("SheetNumber", u""), row.get("SheetName", u""))
        if skey not in seen_sheets:
            seen_sheets.add(skey)
            sheets.append(skey)
        cells.setdefault(skey, {})[ikey] = row.get("Revision", u"")
    sheets.sort(key=lambda s: s[0])
    return issues, sheets, cells


def last_issued(rows):
    """Latest issue info per sheet number.

    Returns:
        dict[sheet_number] = {date, issue, revision} (last row wins)
    """
    latest = {}
    for row in rows:
        latest[row.get("SheetNumber", u"")] = {
            "date": row.get("Date", u""),
            "issue": row.get("Issue", u""),
            "revision": row.get("Revision", u""),
        }
    return latest
