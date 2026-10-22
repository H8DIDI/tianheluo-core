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
            u"室内井、雨水井、MH、QBJ 等不在 prefixes 里的图纸不动。digits=图号位数。"),
    u"digits": 4,
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
            return cfg
    except Exception:
        pass
    return SB_DEFAULTS


def sb_parse_mark(text):
    """从图名/井号里取井号，返回 (前缀, 数字, 后缀或None, 井号原文)；取不到返回 None。取最后一个匹配（图名里井号在后面）。"""
    text = text or u""
    last = None
    for m in _SB_MARK_RE.finditer(text):
        last = m
    if last is None:
        return None
    pre, num, suf = last.group(1), last.group(2), last.group(3)
    mark = u"%s-%s" % (pre, num) + (u"-%s" % suf if suf else u"")
    if text[max(0, last.start() - 3):last.start()] == u"室内-":     # 室内-T-01：室内井不是任何号段的井
        pre = u"室内-" + pre
        mark = u"室内-" + mark
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
        parsed = sb_parse_mark(r.get("name"))
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
    parsed = sb_parse_mark(mark)
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
