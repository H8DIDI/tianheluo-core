# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '井图号\n重编'
__author__ = 'didi'
__doc__ = ('按系统号段给井大样图纸重新编图号：每个系统占一段（默认 11KV 0001–0699、275KV 0700–0999、ELV 1001–1999、TELCO 2001–2999、LV 3001–3999），'
           '段内按井号顺序排（A 在 B 前、AA 在 BB 前、A-10-1 在 A-10 后），号连续，井号跳号不影响图号。\n'
           '先预览「旧图号 → 新图号」和每个系统的占用情况，确认后再应用；应用前自动写出一份「旧号 → 新号」对照表（CSV），'
           '整次可 Ctrl+Z 撤销。室内井、雨水井、MH、QBJ 等不在号段规则里的图纸不动。号段在 文档\\KaifaTools\\井图号号段.json 里改。\n'
           '之后用「一键井展开图」新出的图，会自动从本系统号段里取下一个空号。')

import io
import os
import time
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')
from Autodesk.Revit import DB
from pyrevit import revit, forms
import System.Windows.Forms as WF
import System.Drawing as SD

# ---- sheet_blocks（天河落共用片段，打包时内联进脚本）-----------------------------
# 井大样图号的「号段」规则：每个系统占一段图号，新图从本系统号段里取下一个空号，图号发出后不再跟着井号顺序变。
# 配置文件：文档\KaifaTools\井图号号段.json。第一次运行时自动写出默认值；改完保存，下次点插件生效；删掉文件恢复默认。
import io as _sb_io
import os as _sb_os
import json as _sb_json
import re as _sb_re

SB_FILE = _sb_os.path.join(
    __import__('System').Environment.GetFolderPath(__import__('System').Environment.SpecialFolder.MyDocuments),
    u"KaifaTools", u"井图号号段.json")

SB_DEFAULTS = {
    u"说明": (u"井大样图号号段。blocks 里每一项是一个系统：start/end=图号范围（含），prefixes=归这个系统的井号前缀（按这个顺序排，"
            u"如 A 排在 B 前面）。只有井号形如 前缀-数字（如 T-022、A-098-1）的图纸才会按号段编号；"
            u"室内井、雨水井、MH、QBJ 等不在 prefixes 里的图纸不动。digits=图号位数。"
            u"mark_regex=从图名里认井号的正则，必须有三个括号：前缀、数字、可选的后缀数字（如 T-022、A-098-1）；换项目的井号写法不同，改这里。"
            u"not_drawn_markers=井号前面紧挨着这些文字的，一律当作不编号的井（如「室内-T-01」）。"),
    u"digits": 4,
    u"mark_regex": u"(?<![A-Za-z])([A-Za-z]{1,2})-(\\d+)(?:-(\\d+))?",
    u"not_drawn_markers": [u"室内-"],
    u"blocks": [
        {u"name": u"11KV", u"start": 1, u"end": 699, u"prefixes": [u"A", u"B"]},
        {u"name": u"275KV", u"start": 700, u"end": 999, u"prefixes": [u"AA", u"BB"]},
        {u"name": u"ELV", u"start": 1001, u"end": 1999, u"prefixes": [u"E"]},
        {u"name": u"TELCO", u"start": 2001, u"end": 2999, u"prefixes": [u"T"]},
        {u"name": u"LV", u"start": 3001, u"end": 3999, u"prefixes": [u"L"]},
    ],
}

_SB_MARK_RE = _sb_re.compile(u"(?<![A-Za-z])([A-Za-z]{1,2})-(\\d+)(?:-(\\d+))?")


def sb_load():
    """读号段规则；没有文件就写一份默认的。读不懂时用默认值。"""
    try:
        if not _sb_os.path.isfile(SB_FILE):
            d = _sb_os.path.dirname(SB_FILE)
            if not _sb_os.path.isdir(d):
                _sb_os.makedirs(d)
            txt = _sb_json.dumps(SB_DEFAULTS, ensure_ascii=False, indent=1)
            if not isinstance(txt, type(u"")):
                txt = txt.decode("utf-8")
            with _sb_io.open(SB_FILE, "w", encoding="utf-8") as f:
                f.write(txt)
            return SB_DEFAULTS
        with _sb_io.open(SB_FILE, "r", encoding="utf-8") as f:
            cfg = _sb_json.load(f)
        if isinstance(cfg, dict) and cfg.get(u"blocks"):
            cfg.setdefault(u"digits", 4)
            for _k in (u"mark_regex", u"not_drawn_markers"):
                cfg.setdefault(_k, SB_DEFAULTS[_k])
            return cfg
    except Exception:
        pass
    return SB_DEFAULTS


_SB_RE_CACHE = {}


def _sb_regex(rules):
    pat = (rules or {}).get(u"mark_regex") or SB_DEFAULTS[u"mark_regex"]
    r = _SB_RE_CACHE.get(pat)
    if r is None:
        try:
            r = _sb_re.compile(pat)
            if r.groups < 2:
                raise ValueError(u"mark_regex 至少要有前缀、数字两个括号")
        except Exception:
            r = _sb_re.compile(SB_DEFAULTS[u"mark_regex"])
        _SB_RE_CACHE[pat] = r
    return r


def sb_save(cfg):
    """把号段规则写回设置文件（图形化设置窗口用）。"""
    d = _sb_os.path.dirname(SB_FILE)
    if not _sb_os.path.isdir(d):
        _sb_os.makedirs(d)
    out = dict(cfg)
    out.setdefault(u"说明", SB_DEFAULTS[u"说明"])
    txt = _sb_json.dumps(out, ensure_ascii=False, indent=1)
    if not isinstance(txt, type(u"")):
        txt = txt.decode("utf-8")
    with _sb_io.open(SB_FILE, "w", encoding="utf-8") as f:
        f.write(txt)


def sb_parse_mark(text, rules=None):
    """从图名/井号里取井号，返回 (前缀, 数字, 后缀或0, 井号原文)；取不到返回 None。取最后一个匹配（图名里井号在后面）。
    井号前面紧挨着 not_drawn_markers 里的文字（默认「室内-」）时，前缀变成「文字+前缀」，不属于任何号段。"""
    text = text or u""
    last = None
    for m in _sb_regex(rules).finditer(text):
        last = m
    if last is None:
        return None
    g = last.groups()
    pre, num = g[0], g[1]
    suf = g[2] if len(g) > 2 else None
    mark = u"%s-%s" % (pre, num) + (u"-%s" % suf if suf else u"")
    markers = (rules or SB_DEFAULTS).get(u"not_drawn_markers", SB_DEFAULTS[u"not_drawn_markers"]) or []
    for mk in markers:
        if mk and text[max(0, last.start() - len(mk)):last.start()] == mk:
            return (mk + pre, int(num), int(suf) if suf else 0, mk + mark)
    return (pre, int(num), int(suf) if suf else 0, mark)


def sb_block_of(prefix, rules):
    """前缀属于哪个号段，返回 block dict；不在任何号段返回 None。前缀按字母大小写忽略。"""
    p = (prefix or u"").upper()
    for b in rules[u"blocks"]:
        if p in [x.upper() for x in b[u"prefixes"]]:
            return b
    return None


def sb_order_key(parsed, block):
    """同一号段里的顺序：先按前缀在 prefixes 里的顺序，再按井号数字，再按后缀。"""
    pres = [x.upper() for x in block[u"prefixes"]]
    return (pres.index(parsed[0].upper()), parsed[1], parsed[2])


def sb_fmt(n, rules):
    d = int(rules.get(u"digits", 4) or 0)
    return (u"%0*d" % (d, n)) if d > 0 else (u"%d" % n)


def sb_plan(rows, rules, other_numbers=None):
    """按号段重编。rows: [{'key':任意, 'old':旧图号文字, 'name':图名或井号}]；other_numbers: 不参与重编的图纸已占用的图号集合。
    返回 dict：
      changes  [(key, 旧号, 新号, 系统名, 井号)]，只含号变了的；
      same     [(key, 旧号, 系统名, 井号)]，号本来就对的；
      skipped  [(key, 旧号, 原因)]，不在号段规则里的、或放不下的；
      warnings [文字]；
      summary  {系统名: (张数, 起, 止, 容量)}。"""
    other = set(other_numbers or [])
    groups = {}
    skipped = []
    for r in rows:
        parsed = sb_parse_mark(r.get("name"), rules)
        blk = sb_block_of(parsed[0], rules) if parsed else None
        if blk is None:
            skipped.append((r["key"], r.get("old"), u"井号不在号段规则里（室内井、雨水井等），不动" if parsed else u"图名里认不出井号，不动"))
            continue
        groups.setdefault(blk[u"name"], (blk, []))[1].append((sb_order_key(parsed, blk), r.get("old") or u"", r, parsed))
    changes, same, warnings, summary = [], [], [], {}
    for bname, (blk, items) in groups.items():
        items.sort(key=lambda t: (t[0], t[1]))
        cap = blk[u"end"] - blk[u"start"] + 1
        n = blk[u"start"]
        used = 0
        marks_seen = {}
        for key, old, r, parsed in items:
            marks_seen[parsed[3]] = marks_seen.get(parsed[3], 0) + 1
            while sb_fmt(n, rules) in other and n <= blk[u"end"]:
                n += 1
            if n > blk[u"end"]:
                skipped.append((r["key"], old, u"%s 号段（%d–%d）已用完，放不下" % (bname, blk[u"start"], blk[u"end"])))
                continue
            new = sb_fmt(n, rules)
            n += 1
            used += 1
            if new == old:
                same.append((r["key"], old, bname, parsed[3]))
            else:
                changes.append((r["key"], old, new, bname, parsed[3]))
        dups = [m for m, c in marks_seen.items() if c > 1]
        if dups:
            warnings.append(u"%s：%d 口井有不止一张图纸（例如 %s），都会各占一个号。" % (bname, len(dups), dups[0]))
        if len(items) > cap:
            warnings.append(u"%s：图纸 %d 张，号段只有 %d 个号，放不下。请改大 %s 的号段。" % (bname, len(items), cap, bname))
        summary[bname] = (used, blk[u"start"], blk[u"start"] + max(used, 1) - 1, cap)
    return {"changes": changes, "same": same, "skipped": skipped, "warnings": warnings, "summary": summary}


def sb_next_number(mark, rules, occupied):
    """新图纸的图号：取这口井所在号段里「已用最大号 + 1」（没有就取号段起点），并记入 occupied（已占用的整数集合）。
    返回 (图号文字, 提示)；提示为空表示正常。井号不在任何号段、或号段满了时，图号接在所有号段之后，并返回提示。"""
    parsed = sb_parse_mark(mark, rules)
    blk = sb_block_of(parsed[0], rules) if parsed else None
    last_end = max(b[u"end"] for b in rules[u"blocks"])
    if blk is not None:
        in_blk = [x for x in occupied if blk[u"start"] <= x <= blk[u"end"]]
        n = (max(in_blk) + 1) if in_blk else blk[u"start"]
        while n in occupied:
            n += 1
        if n <= blk[u"end"]:
            occupied.add(n)
            return sb_fmt(n, rules), u""
        warn = u"%s 号段（%d–%d）已用完，%s 的图号接在所有号段之后。" % (blk[u"name"], blk[u"start"], blk[u"end"], mark)
    else:
        warn = u"%s 不在号段规则里，图号接在所有号段之后。" % mark
    after = [x for x in occupied if x > last_end]
    n = (max(after) + 1) if after else last_end + 1
    occupied.add(n)
    return sb_fmt(n, rules), warn

# ---- settings_ui（天河落共用片段，打包时内联进脚本）-----------------------------
# 图形化「设置」窗口：每个参数一行（中文名、输入框/下拉框/勾选、单位和说明），保存、恢复默认、取消。
# 用法：su_fields_dialog(标题, 说明, 字段列表, 当前值, 默认值) -> 新值字典 或 None（取消）。
# 字段 dict：key、label、kind（header 分组标题 / num 数字 / text 文本 / list 逗号分隔的列表 / choice 下拉（只能选）/
#           combo 下拉（可选可手填）/ bool 勾选 / palette 颜色列表「R,G,B; R,G,B」）、hint 说明、
#           choices [(值, 显示文字), ...]、min、max、int（数字取整）。
import re as _su_re
import clr as _su_clr
_su_clr.AddReference('System.Windows.Forms')
_su_clr.AddReference('System.Drawing')
import System.Windows.Forms as _SUW
import System.Drawing as _SUD
import System.Diagnostics as _SUDG


def su_open_file(path):
    """用记事本打开设置文件。"""
    try:
        _SUDG.Process.Start(u"notepad.exe", path)
    except Exception:
        pass


def _su_split(text):
    return [x.strip() for x in _su_re.split(u"[,，;；\\n\\r]+", text or u"") if x.strip()]


def _su_to_text(fld, val):
    kind = fld.get(u"kind", u"text")
    if kind == u"list":
        return u", ".join(val or [])
    if kind == u"palette":
        return u"; ".join(u"%d,%d,%d" % tuple(c[:3]) for c in (val or []))
    if kind == u"num":
        try:
            v = float(val)
            return (u"%d" % v) if v == int(v) else (u"%g" % v)
        except Exception:
            return u"" if val is None else u"%s" % val
    return u"" if val is None else u"%s" % val


def su_fields_dialog(title, intro, fields, values, defaults=None, extra_buttons=None, width=680, height=640):
    """弹出设置窗口。extra_buttons: [(按钮文字, 回调)]，回调不带参数（如「用记事本打开文件」）。返回新值 dict 或 None。"""
    f = _SUW.Form()
    f.Text = title
    f.Font = _SUD.Font(u"Microsoft YaHei UI", 9.0)
    f.StartPosition = _SUW.FormStartPosition.CenterScreen
    f.FormBorderStyle = _SUW.FormBorderStyle.FixedDialog
    f.MaximizeBox = False
    f.MinimizeBox = False
    f.ClientSize = _SUD.Size(width, height)
    lb = _SUW.Label()
    lb.Text = intro
    lb.Location = _SUD.Point(14, 10)
    lb.Size = _SUD.Size(width - 28, 44)
    f.Controls.Add(lb)
    panel = _SUW.Panel()
    panel.Location = _SUD.Point(10, 58)
    panel.Size = _SUD.Size(width - 20, height - 58 - 54)
    panel.AutoScroll = True
    panel.BorderStyle = _SUW.BorderStyle.FixedSingle
    f.Controls.Add(panel)

    ctrls = {}
    y = 8
    for fld in fields:
        kind = fld.get(u"kind", u"text")
        if kind == u"header":
            l = _SUW.Label()
            l.Text = fld[u"label"]
            l.Font = _SUD.Font(f.Font, _SUD.FontStyle.Bold)
            l.Location = _SUD.Point(6, y + 6)
            l.Size = _SUD.Size(width - 60, 22)
            panel.Controls.Add(l)
            y += 34
            continue
        l = _SUW.Label()
        l.Text = fld[u"label"]
        l.Location = _SUD.Point(6, y + 3)
        l.Size = _SUD.Size(250, 34)
        panel.Controls.Add(l)
        key = fld[u"key"]
        if kind in (u"choice", u"combo"):
            c = _SUW.ComboBox()
            c.DropDownStyle = _SUW.ComboBoxStyle.DropDownList if kind == u"choice" else _SUW.ComboBoxStyle.DropDown
            for _v, _t in fld.get(u"choices", []):
                c.Items.Add(_t)
            c.Location = _SUD.Point(264, y)
            c.Size = _SUD.Size(width - 310, 24)
        elif kind == u"bool":
            c = _SUW.CheckBox()
            c.Text = fld.get(u"bool_text", u"启用")
            c.Location = _SUD.Point(264, y + 2)
            c.Size = _SUD.Size(width - 310, 22)
        else:
            c = _SUW.TextBox()
            c.Location = _SUD.Point(264, y)
            c.Size = _SUD.Size(width - 310, 24)
        panel.Controls.Add(c)
        ctrls[key] = c
        y += 30
        if fld.get(u"hint"):
            h = _SUW.Label()
            h.Text = fld[u"hint"]
            h.ForeColor = _SUD.Color.FromArgb(110, 110, 110)
            h.Location = _SUD.Point(264, y - 2)
            h.Size = _SUD.Size(width - 310, 34)
            panel.Controls.Add(h)
            y += 36
        y += 6

    def put(fld, val):
        c = ctrls.get(fld[u"key"])
        if c is None:
            return
        kind = fld.get(u"kind", u"text")
        if kind == u"bool":
            c.Checked = bool(val)
        elif kind in (u"choice", u"combo"):
            chs = fld.get(u"choices", [])
            idx = [i for i, (v, t) in enumerate(chs) if v == val]
            if idx:
                c.SelectedIndex = idx[0]
            elif kind == u"combo":
                c.Text = u"" if val is None else u"%s" % val
            elif chs:
                c.SelectedIndex = 0
        else:
            c.Text = _su_to_text(fld, val)

    def get(fld):
        """读控件的值并校验；不合格抛 ValueError(说明)。"""
        c = ctrls[fld[u"key"]]
        kind = fld.get(u"kind", u"text")
        name = fld[u"label"]
        if kind == u"bool":
            return bool(c.Checked)
        if kind == u"choice":
            chs = fld.get(u"choices", [])
            return chs[c.SelectedIndex][0] if 0 <= c.SelectedIndex < len(chs) else None
        if kind == u"combo":
            txt = (c.Text or u"").strip()
            for v, t in fld.get(u"choices", []):
                if t == txt:
                    return v
            return txt
        txt = (c.Text or u"").strip()
        if kind == u"num":
            try:
                v = float(txt)
            except Exception:
                raise ValueError(u"「%s」要填数字。" % name)
            if fld.get(u"min") is not None and v < fld[u"min"]:
                raise ValueError(u"「%s」不能小于 %s。" % (name, fld[u"min"]))
            if fld.get(u"max") is not None and v > fld[u"max"]:
                raise ValueError(u"「%s」不能大于 %s。" % (name, fld[u"max"]))
            return int(round(v)) if fld.get(u"int") else v
        if kind == u"list":
            return _su_split(txt)
        if kind == u"palette":
            out = []
            for part in [x.strip() for x in _su_re.split(u"[;；\n\r]+", txt) if x.strip()]:
                nums = [x for x in _su_re.split(u"[ ,]+", part) if x]
                try:
                    rgb = [int(x) for x in nums[:3]]
                except Exception:
                    rgb = []
                if len(rgb) != 3 or any(n < 0 or n > 255 for n in rgb):
                    raise ValueError(u"「%s」里「%s」不是有效的颜色，格式是 R,G,B（0–255），多个颜色用分号隔开。" % (name, part))
                out.append(rgb)
            if not out:
                raise ValueError(u"「%s」至少要有一个颜色。" % name)
            return out
        if fld.get(u"required") and not txt:
            raise ValueError(u"「%s」不能为空。" % name)
        return txt

    for fld in fields:
        if fld.get(u"kind") != u"header":
            put(fld, values.get(fld[u"key"]))

    result = {u"v": None}
    b_ok = _SUW.Button()
    b_ok.Text = u"保存"
    b_ok.Name = u"su_ok"
    b_ok.Location = _SUD.Point(width - 200, height - 44)
    b_ok.Size = _SUD.Size(88, 30)
    b_ca = _SUW.Button()
    b_ca.Text = u"取消"
    b_ca.Name = u"su_cancel"
    b_ca.Location = _SUD.Point(width - 100, height - 44)
    b_ca.Size = _SUD.Size(88, 30)
    f.Controls.Add(b_ok)
    f.Controls.Add(b_ca)
    f.CancelButton = b_ca
    bx = 14
    if defaults:
        b_def = _SUW.Button()
        b_def.Text = u"恢复默认"
        b_def.Name = u"su_default"
        b_def.Location = _SUD.Point(bx, height - 44)
        b_def.Size = _SUD.Size(88, 30)
        f.Controls.Add(b_def)
        bx += 96

        def _on_def(s, e):
            for fld in fields:
                if fld.get(u"kind") != u"header" and fld[u"key"] in defaults:
                    put(fld, defaults[fld[u"key"]])
        b_def.Click += _on_def
    for text, cb in (extra_buttons or []):
        b = _SUW.Button()
        b.Text = text
        b.Location = _SUD.Point(bx, height - 44)
        b.Size = _SUD.Size(130, 30)
        f.Controls.Add(b)
        bx += 138
        b.Click += (lambda s, e, cb=cb: cb())

    def _on_ok(s, e):
        out = {}
        try:
            for fld in fields:
                if fld.get(u"kind") != u"header":
                    out[fld[u"key"]] = get(fld)
        except ValueError as ex:
            _SUW.MessageBox.Show(u"%s" % ex, title)
            return
        result[u"v"] = out
        f.Close()
    b_ok.Click += _on_ok
    b_ca.Click += (lambda s, e: f.Close())
    f.ShowDialog()
    return result[u"v"]

doc = revit.doc
uidoc = revit.uidoc
TITLE = '井图号重编'
MAP_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), 'KaifaTools')


def scan_sheets():
    """全部图纸（含占位）：[{'key': 图纸Id, 'sheet': 图纸, 'old': 图号, 'name': 图名}]。"""
    out = []
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
        try:
            out.append({'key': int(s.Id.Value), 'sheet': s, 'old': (s.SheetNumber or '').strip(), 'name': s.Name or ''})
        except Exception:
            continue
    return out


def make_plan(rules):
    """先按图名认出哪些是井大样图纸，再把其他图纸占用的图号当作「已被占用」，重新算一遍。"""
    rows = scan_sheets()
    first = sb_plan(rows, rules)
    skipped_keys = set(k for k, _o, _r in first['skipped'])
    other = set(r['old'] for r in rows if r['key'] in skipped_keys)
    plan = sb_plan(rows, rules, other)
    plan['rows'] = dict((r['key'], r) for r in rows)
    plan['other'] = other
    return plan


def export_map(plan):
    """把「旧号 → 新号」写成 CSV（带 BOM，Excel 直接打开）。返回路径。"""
    if not os.path.isdir(MAP_DIR):
        os.makedirs(MAP_DIR)
    path = os.path.join(MAP_DIR, '图号对照_{}.csv'.format(time.strftime('%Y%m%d_%H%M%S')))
    lines = ['系统,井号,旧图号,新图号,图名']
    for key, old, new, sysname, mark in sorted(plan['changes'], key=lambda t: t[2]):
        lines.append('{},{},{},{},"{}"'.format(sysname, mark, old, new, plan['rows'][key]['name'].replace('"', '""')))
    for key, old, sysname, mark in sorted(plan['same'], key=lambda t: t[1]):
        lines.append('{},{},{},{},"{}"'.format(sysname, mark, old, old, plan['rows'][key]['name'].replace('"', '""')))
    with io.open(path, 'w', encoding='utf-8-sig') as f:
        f.write('\n'.join(lines) + '\n')
    return path


def apply_plan(plan):
    """两步改号（先全部改成临时号，再改成新号），避免互相占号；整次一个事务组，可一次 Ctrl+Z。返回 (改了几张, 失败说明)。"""
    changes = plan['changes']
    if not changes:
        return 0, ''
    tg = DB.TransactionGroup(doc, '井图号重编')
    tg.Start()
    cur = [None]
    try:
        t1 = DB.Transaction(doc, '临时图号')
        cur[0] = t1
        t1.Start()
        for key, old, new, sysname, mark in changes:
            plan['rows'][key]['sheet'].SheetNumber = 'TMP{}'.format(key)      # 图号里不能有 { } [ ] | ; < > ? ` ~
        t1.Commit()
        t2 = DB.Transaction(doc, '正式图号')
        cur[0] = t2
        t2.Start()
        for key, old, new, sysname, mark in changes:
            plan['rows'][key]['sheet'].SheetNumber = new
        t2.Commit()
        tg.Assimilate()
        return len(changes), ''
    except Exception as ex:
        try:
            t = cur[0]
            if t is not None and t.HasStarted() and not t.HasEnded():
                t.RollBack()          # 先收尾内层事务，再撤回整个事务组，文档不会卡在半截
        except Exception:
            pass
        try:
            tg.RollBack()
        except Exception:
            pass
        return 0, str(ex)


def _rules_from_controls(grid, t_digits, t_regex, t_markers, base):
    """读窗口里的值并校验，返回新 rules；不合格抛 ValueError(说明)。"""
    import re as _re
    blocks = []
    seen_names = set()
    seen_prefix = {}
    for row in grid.Rows:
        if row.IsNewRow:
            continue
        cells = [row.Cells[i].Value for i in range(4)]
        if all((c is None or str(c).strip() == '') for c in cells):
            continue
        name = (str(cells[0]) if cells[0] is not None else '').strip()
        if not name:
            raise ValueError('有一行没有填「系统名」。')
        if name in seen_names:
            raise ValueError('系统名「{}」重复了。'.format(name))
        seen_names.add(name)
        try:
            start = int(str(cells[1]).strip())
            end = int(str(cells[2]).strip())
        except Exception:
            raise ValueError('「{}」的起始号和结束号要填整数。'.format(name))
        if start < 0 or end < start:
            raise ValueError('「{}」的结束号要不小于起始号。'.format(name))
        prefixes = [x.strip() for x in _re.split('[,，;；\\s]+', str(cells[3]) if cells[3] is not None else '') if x.strip()]
        if not prefixes:
            raise ValueError('「{}」没有填井号前缀。'.format(name))
        for p in prefixes:
            if p.upper() in seen_prefix:
                raise ValueError('前缀「{}」同时出现在「{}」和「{}」里。'.format(p, seen_prefix[p.upper()], name))
            seen_prefix[p.upper()] = name
        blocks.append({'name': name, 'start': start, 'end': end, 'prefixes': prefixes})
    if not blocks:
        raise ValueError('至少要有一个系统号段。')
    ordered = sorted(blocks, key=lambda b: b['start'])
    for a, b in zip(ordered, ordered[1:]):
        if b['start'] <= a['end']:
            raise ValueError('号段「{}」（{}–{}）和「{}」（{}–{}）重叠了。'.format(a['name'], a['start'], a['end'], b['name'], b['start'], b['end']))
    try:
        digits = int((t_digits.Text or '').strip())
        if digits < 0 or digits > 8:
            raise ValueError()
    except Exception:
        raise ValueError('图号位数要填 0–8 的整数（0 = 不补零）。')
    rx = (t_regex.Text or '').strip()
    try:
        cr = _re.compile(rx)
        if cr.groups < 2:
            raise ValueError('至少要有「前缀」「数字」两个括号')
    except _re.error as ex:
        raise ValueError('井号识别规则不是有效的正则：{}'.format(ex))
    except ValueError:
        raise ValueError('井号识别规则要有两到三个括号：前缀、数字、可选的后缀数字，例如 (?<![A-Za-z])([A-Za-z]{1,2})-(\\d+)(?:-(\\d+))?')
    markers = [x.strip() for x in _re.split('[,，;；]+', t_markers.Text or '') if x.strip()]
    out = dict(base)
    out['blocks'] = blocks
    out['digits'] = digits
    out['mark_regex'] = rx
    out['not_drawn_markers'] = markers
    return out


def edit_rules(rules):
    """图形化编辑号段规则。保存后写回设置文件，返回 True；取消返回 False。"""
    f = WF.Form()
    f.Text = TITLE + ' - 号段设置'
    f.Font = SD.Font('Microsoft YaHei UI', 9.0)
    f.StartPosition = WF.FormStartPosition.CenterScreen
    f.FormBorderStyle = WF.FormBorderStyle.FixedDialog
    f.MaximizeBox = False
    f.MinimizeBox = False
    f.ClientSize = SD.Size(780, 640)
    done = {'saved': False}

    def lab(t, x, y, w, h=20, bold=False, gray=False):
        l = WF.Label()
        l.Text = t
        l.Location = SD.Point(x, y)
        l.Size = SD.Size(w, h)
        if bold:
            l.Font = SD.Font(f.Font, SD.FontStyle.Bold)
        if gray:
            l.ForeColor = SD.Color.FromArgb(110, 110, 110)
        f.Controls.Add(l)
        return l

    lab('每个系统一段图号', 14, 10, 400, bold=True)
    lab('每行 = 一个系统。前缀按填写顺序排（A, B 表示 A 开头的井排在 B 开头的前面）。选中一行按 Delete 键删除，最下面一行空行用来添加。',
        14, 32, 752, 34, gray=True)
    g = WF.DataGridView()
    g.Location = SD.Point(14, 70)
    g.Size = SD.Size(752, 230)
    g.AllowUserToAddRows = True
    g.AllowUserToDeleteRows = True
    g.RowHeadersVisible = False
    g.BackgroundColor = SD.Color.White
    for title, wd in (('系统名', 150), ('起始号', 90), ('结束号', 90), ('井号前缀（逗号分隔，按顺序排）', 400)):
        c = WF.DataGridViewTextBoxColumn()
        c.HeaderText = title
        c.Width = wd
        g.Columns.Add(c)
    for b in rules['blocks']:
        g.Rows.Add(b['name'], str(b['start']), str(b['end']), ', '.join(b['prefixes']))
    f.Controls.Add(g)

    lab('图号位数', 14, 316, 150)
    t_dig = WF.TextBox()
    t_dig.Text = str(rules.get('digits', 4))
    t_dig.Location = SD.Point(180, 313)
    t_dig.Size = SD.Size(80, 24)
    f.Controls.Add(t_dig)
    lab('4 位 = 0001；0 = 不补零。', 270, 316, 400, gray=True)

    lab('图名里认井号的规则', 14, 352, 160)
    t_rx = WF.TextBox()
    t_rx.Text = rules.get('mark_regex', '')
    t_rx.Location = SD.Point(180, 349)
    t_rx.Size = SD.Size(586, 24)
    f.Controls.Add(t_rx)
    lab('正则表达式，三个括号：前缀、数字、可选的后缀。换项目的井号写法不同（如 WH0012）才需要改，不会写可以用下面的「试一试」确认。',
        180, 376, 586, 34, gray=True)

    lab('不编号的井号前缀', 14, 418, 160)
    t_mk = WF.TextBox()
    t_mk.Text = ', '.join(rules.get('not_drawn_markers', []))
    t_mk.Location = SD.Point(180, 415)
    t_mk.Size = SD.Size(586, 24)
    f.Controls.Add(t_mk)
    lab('井号前面紧挨着这些文字的，当作不编号的井，例如「室内-」。多个用逗号隔开。', 180, 442, 586, 20, gray=True)

    lab('试一试', 14, 478, 150, bold=True)
    t_try = WF.TextBox()
    t_try.Text = 'BTP-IF1 TELcon-T-022 Detail Drawing'
    t_try.Location = SD.Point(14, 502)
    t_try.Size = SD.Size(520, 24)
    f.Controls.Add(t_try)
    b_try = WF.Button()
    b_try.Text = '试一试'
    b_try.Location = SD.Point(542, 500)
    b_try.Size = SD.Size(80, 28)
    f.Controls.Add(b_try)
    l_res = lab('输入一个图名，看它会被识别成哪口井、归哪个号段。', 14, 532, 752, 36)

    b_save = WF.Button()
    b_save.Text = '保存'
    b_save.Location = SD.Point(578, 596)
    b_save.Size = SD.Size(88, 30)
    b_ca = WF.Button()
    b_ca.Text = '取消'
    b_ca.Location = SD.Point(678, 596)
    b_ca.Size = SD.Size(88, 30)
    b_def = WF.Button()
    b_def.Text = '恢复默认'
    b_def.Location = SD.Point(14, 596)
    b_def.Size = SD.Size(88, 30)
    b_file = WF.Button()
    b_file.Text = '用记事本打开文件'
    b_file.Location = SD.Point(110, 596)
    b_file.Size = SD.Size(130, 30)
    for b in (b_save, b_ca, b_def, b_file):
        f.Controls.Add(b)
    f.CancelButton = b_ca

    def on_try(s, e):
        try:
            r = _rules_from_controls(g, t_dig, t_rx, t_mk, rules)
        except ValueError as ex:
            l_res.Text = '设置还有问题：{}'.format(ex)
            return
        parsed = sb_parse_mark(t_try.Text, r)
        if parsed is None:
            l_res.Text = '认不出井号（图名里没有符合识别规则的部分）。'
            return
        blk = sb_block_of(parsed[0], r)
        if blk is None:
            l_res.Text = '识别出井号 {}，但前缀「{}」不在任何号段里，这张图不会被编号。'.format(parsed[3], parsed[0])
        else:
            l_res.Text = '识别出井号 {} → 属于「{}」号段（{}–{}）。'.format(parsed[3], blk['name'], sb_fmt(blk['start'], r), sb_fmt(blk['end'], r))

    def on_save(s, e):
        try:
            r = _rules_from_controls(g, t_dig, t_rx, t_mk, rules)
        except ValueError as ex:
            WF.MessageBox.Show(str(ex), TITLE)
            return
        sb_save(r)
        done['saved'] = True
        f.Close()

    def on_def(s, e):
        d = SB_DEFAULTS
        g.Rows.Clear()
        for b in d['blocks']:
            g.Rows.Add(b['name'], str(b['start']), str(b['end']), ', '.join(b['prefixes']))
        t_dig.Text = str(d['digits'])
        t_rx.Text = d['mark_regex']
        t_mk.Text = ', '.join(d['not_drawn_markers'])

    b_try.Click += on_try
    b_save.Click += on_save
    b_def.Click += on_def
    b_file.Click += (lambda s, e: (sb_load(), su_open_file(SB_FILE)))
    b_ca.Click += (lambda s, e: f.Close())
    f.ShowDialog()
    return done['saved']


def show_dialog(rules, plan):
    """预览窗口。返回 'apply' / 'close'。"""
    f = WF.Form()
    f.Text = TITLE
    f.Font = SD.Font('Microsoft YaHei UI', 9.0)
    f.StartPosition = WF.FormStartPosition.CenterScreen
    f.FormBorderStyle = WF.FormBorderStyle.FixedDialog
    f.MaximizeBox = False
    f.MinimizeBox = False
    f.ClientSize = SD.Size(760, 600)
    res = {'act': 'close'}

    def lab(t, x, y, w, h=20, bold=False):
        l = WF.Label()
        l.Text = t
        l.Location = SD.Point(x, y)
        l.Size = SD.Size(w, h)
        if bold:
            l.Font = SD.Font(f.Font, SD.FontStyle.Bold)
        f.Controls.Add(l)
        return l

    lab('各系统号段使用情况', 16, 10, 400, bold=True)
    lines = []
    for b in rules['blocks']:
        u = plan['summary'].get(b['name'])
        if u:
            lines.append('{}：{} 张 → {}–{}（号段 {}–{}，容量 {}）'.format(
                b['name'], u[0], sb_fmt(u[1], rules), sb_fmt(u[2], rules), sb_fmt(b['start'], rules), sb_fmt(b['end'], rules), u[3]))
        else:
            lines.append('{}：0 张（号段 {}–{}）'.format(b['name'], sb_fmt(b['start'], rules), sb_fmt(b['end'], rules)))
    lab('\n'.join(lines), 16, 32, 728, 96)

    n_chg, n_same, n_skip = len(plan['changes']), len(plan['same']), len(plan['skipped'])
    lab('井大样图纸 {} 张：图号要改 {} 张，本来就对 {} 张；不在号段规则里不动 {} 张。'.format(n_chg + n_same, n_chg, n_same, n_skip),
        16, 134, 728)
    warn = '\n'.join(plan['warnings']) if plan['warnings'] else ''
    if plan['skipped']:
        over = [s for s in plan['skipped'] if '用完' in s[2]]
        if over:
            warn += ('\n' if warn else '') + '有 {} 张放不下（号段用完），不会被改号。'.format(len(over))
    w = lab(warn, 16, 156, 728, 44)
    w.ForeColor = SD.Color.FromArgb(190, 60, 20)

    lv = WF.ListView()
    lv.View = WF.View.Details
    lv.FullRowSelect = True
    lv.GridLines = True
    lv.Location = SD.Point(16, 204)
    lv.Size = SD.Size(728, 330)
    for h, wd in (('系统', 80), ('井号', 130), ('旧图号', 100), ('新图号', 100), ('图名', 290)):
        lv.Columns.Add(h, wd)
    rows = []
    for key, old, new, sysname, mark in plan['changes']:
        rows.append((new, sysname, mark, old, new, plan['rows'][key]['name'], True))
    for key, old, sysname, mark in plan['same']:
        rows.append((old, sysname, mark, old, old, plan['rows'][key]['name'], False))
    rows.sort(key=lambda r: r[0])
    for _k, sysname, mark, old, new, name, changed in rows:
        it = WF.ListViewItem(sysname)
        it.SubItems.Add(mark)
        it.SubItems.Add(old)
        it.SubItems.Add(new)
        it.SubItems.Add(name)
        if changed:
            it.ForeColor = SD.Color.FromArgb(0, 90, 170)
        else:
            it.ForeColor = SD.Color.FromArgb(130, 130, 130)
        lv.Items.Add(it)
    f.Controls.Add(lv)
    lab('蓝色 = 图号会改；灰色 = 本来就对。应用前会先写出「旧号 → 新号」对照表。', 16, 540, 728)

    b_apply = WF.Button()
    b_apply.Text = '应用重编'
    b_apply.Location = SD.Point(16, 564)
    b_apply.Size = SD.Size(110, 30)
    b_apply.Enabled = n_chg > 0
    b_exp = WF.Button()
    b_exp.Text = '只导出对照表'
    b_exp.Location = SD.Point(136, 564)
    b_exp.Size = SD.Size(110, 30)
    b_rule = WF.Button()
    b_rule.Text = '号段设置…'
    b_rule.Location = SD.Point(256, 564)
    b_rule.Size = SD.Size(110, 30)
    b_close = WF.Button()
    b_close.Text = '关闭'
    b_close.Location = SD.Point(634, 564)
    b_close.Size = SD.Size(110, 30)
    for b in (b_apply, b_exp, b_rule, b_close):
        f.Controls.Add(b)
    f.CancelButton = b_close

    def on_apply(s, e):
        msg = '将改 {} 张井大样图纸的图号（只改图号，不改图名和内容）。\n应用前会先写出对照表，整次可 Ctrl+Z 撤销。\n\n确定吗？'.format(n_chg)
        if WF.MessageBox.Show(msg, TITLE, WF.MessageBoxButtons.OKCancel, WF.MessageBoxIcon.Question) == WF.DialogResult.OK:
            res['act'] = 'apply'
            f.Close()

    def on_exp(s, e):
        p = export_map(plan)
        WF.MessageBox.Show('已导出：\n' + p, TITLE)

    def on_rule(s, e):
        if edit_rules(rules):
            res['act'] = 'settings'          # 号段改了：关掉预览，按新规则重新算
            f.Close()

    b_apply.Click += on_apply
    b_exp.Click += on_exp
    b_rule.Click += on_rule
    b_close.Click += lambda s, e: f.Close()
    f.ShowDialog()
    return res['act']


def main():
    try:
        rules = sb_load()
        plan = make_plan(rules)
        if not plan['changes'] and not plan['same']:
            forms.alert('项目里没有可按号段编号的井大样图纸（图名里要有形如 T-022、A-098 的井号，且前缀在号段规则里）。\n号段规则：' + SB_FILE, title=TITLE)
            return
        act = show_dialog(rules, plan)
        while act == 'settings':
            rules = sb_load()
            plan = make_plan(rules)
            act = show_dialog(rules, plan)
        if act != 'apply':
            return
        path = export_map(plan)
        n, err = apply_plan(plan)
        if err:
            forms.alert('重编失败，已整体撤回（图号没有改动）：\n{}\n\n对照表已写出：{}'.format(err, path), title=TITLE)
        else:
            forms.alert('已重编 {} 张图纸的图号。\n对照表：{}\n不满意可以 Ctrl+Z 撤销。'.format(n, path), title=TITLE)
    except Exception as ex:
        forms.alert('发生错误：\n{}\n\n{}'.format(ex, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
