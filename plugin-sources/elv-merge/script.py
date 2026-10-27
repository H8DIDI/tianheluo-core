# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '族类型清洗合并'
__author__ = 'didi'
__doc__ = ('把同一个族里「名字只差一点」的重复类型合并成一个（原「强电井清洗合并」，现在任何族都能用）：\n'
           '按规则文件改类型名（例如去掉名字末尾的深度 x3500(D)、把 EM1- 前缀纠正成族对应的 EM2-），\n'
           '改完重名的类型合并：构件换到保留的类型上，多余类型删除。先列出要改什么再确认，整次可 Ctrl+Z 撤销。\n'
           '规则在 文档\\KaifaTools\\族类型清洗规则.json（第一次运行自动生成，默认就是原来强电井的规则）。')

import io
import os
import re
import json
import traceback
import clr
import System
clr.AddReference('RevitAPI')
from Autodesk.Revit import DB
from pyrevit import revit, forms

# ---- settings_ui（天河落共用片段，打包时内联进脚本）-----------------------------
# 图形化「设置」窗口：每个参数一行（中文名、输入框/下拉框/勾选、单位和说明），保存、恢复默认、取消。
# 用法：su_fields_dialog(标题, 说明, 字段列表, 当前值, 默认值) -> 新值字典 或 None（取消）。
# 字段 dict：key、label、kind（header 分组标题 / num 数字 / text 文本 / list 逗号分隔的列表 / choice 下拉（只能选）/
#           combo 下拉（可选可手填）/ bool 勾选 / palette 颜色列表「R,G,B; R,G,B」/ lines 多行文本，每行一项，rows 行高）、hint 说明、
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
    if kind == u"lines":
        return u"\r\n".join(val or [])
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
        elif kind == u"lines":
            c = _SUW.TextBox()
            c.Multiline = True
            c.ScrollBars = _SUW.ScrollBars.Both
            c.WordWrap = False
            c.AcceptsReturn = True
            c.Font = _SUD.Font(u"Consolas", 9.0)
            rows = int(fld.get(u"rows", 6))
            c.Location = _SUD.Point(264, y)
            c.Size = _SUD.Size(width - 310, rows * 18 + 8)
        else:
            c = _SUW.TextBox()
            c.Location = _SUD.Point(264, y)
            c.Size = _SUD.Size(width - 310, 24)
        panel.Controls.Add(c)
        ctrls[key] = c
        y += (int(fld.get(u"rows", 6)) * 18 + 14) if kind == u"lines" else 30
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
        if kind == u"lines":
            return [x.strip() for x in (c.Text or u"").splitlines() if x.strip()]
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
TITLE = '族类型清洗合并'
RULES = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments),
                     'KaifaTools', '族类型清洗规则.json')
DEFAULT = {
    '说明': ('families：要处理的族名 → 这个族的类型名应有的前缀（类型名以别的族的前缀开头时纠正过来，如 EM1- → EM2-）。'
           'strip：对类型名做的正则替换（pattern → repl），默认把「…(L)x…(W)x深度」末尾的深度去掉。'
           '改名后同名的类型合并成一个。'),
    'families': {'ELE-EM1-I': 'EM1', 'ELE-EM1-L': 'EM1', 'ELE-EM2-I': 'EM2', 'ELE-EM2-L': 'EM2',
                 'ELE-EM4-I': 'EM4', 'ELE-EM4-L': 'EM4', 'ELE-EM4-T': 'EM4'},
    'strip': [{'pattern': r'^(.*\(L\)x[^x]*?\))x.*$', 'repl': r'\1'}],
}


def load_rules():
    try:
        if os.path.exists(RULES):
            with io.open(RULES, 'r', encoding='utf-8-sig') as f:
                d = json.load(f)
            return {'families': d.get('families') or {}, 'strip': d.get('strip') or []}
        dd = os.path.dirname(RULES)
        if not os.path.isdir(dd):
            os.makedirs(dd)
        with io.open(RULES, 'w', encoding='utf-8') as f:
            f.write(json.dumps(DEFAULT, ensure_ascii=False, indent=2))
    except Exception:
        pass
    return {'families': DEFAULT['families'], 'strip': DEFAULT['strip']}


def target_name(name, prefix, all_prefixes, strips):
    new = name
    if prefix:
        for q in all_prefixes:
            if q != prefix and new.startswith(q + '-'):
                new = prefix + '-' + new[len(q) + 1:]
                break
    for r in strips:
        try:
            new = re.sub(r['pattern'], r.get('repl', ''), new)
        except Exception:
            pass
    return new.strip() or name


def plan(rules):
    fams = rules['families']
    prefixes = sorted(set(fams.values()), key=lambda x: -len(x))
    out = []
    for fam in DB.FilteredElementCollector(doc).OfClass(DB.Family):
        if fam.Name not in fams:
            continue
        syms = [doc.GetElement(i) for i in fam.GetFamilySymbolIds()]
        tgt = dict((s.Id, target_name(s.Name, fams[fam.Name], prefixes, rules['strip'])) for s in syms)
        if all(tgt[s.Id] == s.Name for s in syms):
            continue
        out.append((fam, syms, tgt))
    return out


def count_instances(sym):
    return DB.FilteredElementCollector(doc).WherePasses(DB.FamilyInstanceFilter(doc, sym.Id)).GetElementCount()


def save_rules(families, strips):
    d = dict(DEFAULT)
    try:
        if os.path.exists(RULES):
            with io.open(RULES, 'r', encoding='utf-8-sig') as f:
                d.update(json.load(f))
    except Exception:
        pass
    d['families'] = families
    d['strip'] = strips
    dd = os.path.dirname(RULES)
    if not os.path.isdir(dd):
        os.makedirs(dd)
    with io.open(RULES, 'w', encoding='utf-8') as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2))


def open_settings():
    """图形化编辑清洗规则。保存返回 True。"""
    rules = load_rules()
    fields = [
        {'key': 'families', 'label': '要处理的族 = 类型名前缀', 'kind': 'lines', 'rows': 10,
         'hint': '每行一个族：族名 = 这个族的类型名应有的前缀。类型名以别的族的前缀开头时会纠正过来，如 EM1- → EM2-。'},
        {'key': 'strip', 'label': '类型名替换（正则）', 'kind': 'lines', 'rows': 4,
         'hint': '每行一条：查找的正则 => 替换为（可留空）。默认一条把「…(L)x…(W)x深度」末尾的深度去掉。'},
    ]
    def flat(r):
        return {'families': ['%s = %s' % (k, v) for k, v in sorted(r['families'].items())],
                'strip': ['%s => %s' % (x.get('pattern', ''), x.get('repl', '')) for x in r['strip']]}
    vals = flat(rules)
    while True:
        res = su_fields_dialog(TITLE + ' · 规则设置', '改完点保存，下次清洗按新规则。规则文件：' + RULES, fields, vals,
                               flat({'families': DEFAULT['families'], 'strip': DEFAULT['strip']}),
                               extra_buttons=[('用记事本打开文件', lambda: su_open_file(RULES))], width=720, height=520)
        if res is None:
            return False
        try:
            fams = {}
            for ln in res['families']:
                if '=' not in ln:
                    raise ValueError('族这一行缺少「=」：%s（格式：族名 = 前缀）' % ln)
                k, v = ln.split('=', 1)
                if not k.strip() or not v.strip():
                    raise ValueError('族这一行不完整：%s' % ln)
                fams[k.strip()] = v.strip()
            strips = []
            for ln in res['strip']:
                pat, _, rep = ln.partition('=>')
                pat = pat.strip()
                re.compile(pat)
                strips.append({'pattern': pat, 'repl': rep.strip()})
        except ValueError as ex:
            forms.alert('%s' % ex, title=TITLE)
            vals = res
            continue
        except re.error as ex:
            forms.alert('正则写得不对：%s' % ex, title=TITLE)
            vals = res
            continue
        save_rules(fams, strips)
        return True


def main():
    pick = forms.CommandSwitchWindow.show(['按当前规则清洗合并', '⚙ 规则设置…'], message=TITLE)
    if not pick:
        return
    if pick.startswith('⚙'):
        if open_settings():
            forms.alert('规则已保存。再点一次本插件开始清洗。', title=TITLE)
        return
    rules = load_rules()
    work = plan(rules)
    if not work:
        forms.alert('按规则没有需要改名或合并的类型。\n\n规则文件：%s' % RULES, title=TITLE)
        return
    lines = []
    n_types = n_inst = 0
    for fam, syms, tgt in work:
        changes = [s for s in syms if tgt[s.Id] != s.Name]
        n_types += len(changes)
        k = sum(count_instances(s) for s in changes)
        n_inst += k
        lines.append('%s：%d 个类型改名/合并，涉及 %d 个构件' % (fam.Name, len(changes), k))
        for s in changes[:3]:
            lines.append('    %s → %s' % (s.Name, tgt[s.Id]))
    msg = '\n'.join(lines[:40]) + ('\n…' if len(lines) > 40 else '')
    msg += '\n\n共 %d 个类型、%d 个构件。编组里的构件改不了类型，会跳过并报告。\n继续？（可 Ctrl+Z 撤销）' % (n_types, n_inst)
    if not forms.alert(msg, title=TITLE, ok=False, yes=True, no=True):
        return
    updated = deleted = renamed = grouped = 0
    t = DB.Transaction(doc, '天河落 · 族类型清洗合并')
    t.Start()
    try:
        for fam, syms, tgt in work:
            base = {}
            for s in syms:
                if tgt[s.Id] == s.Name:
                    base[s.Name] = s
            for s in syms:
                name = tgt[s.Id]
                if name in base:
                    continue
                # 没有现成同名类型：直接改名当作保留的类型（不再复制一份）
                s.Name = name
                base[name] = s
                renamed += 1
            for s in syms:
                keep = base[tgt[s.Id]]
                if keep.Id == s.Id:
                    continue
                if not keep.IsActive:
                    keep.Activate()
                left = 0
                for inst in DB.FilteredElementCollector(doc).WherePasses(DB.FamilyInstanceFilter(doc, s.Id)):
                    if inst.GroupId != DB.ElementId.InvalidElementId:
                        grouped += 1
                        left += 1
                        continue
                    pinned = inst.Pinned
                    if pinned:
                        inst.Pinned = False
                    try:
                        inst.Symbol = keep
                        updated += 1
                    except Exception:
                        left += 1
                    if pinned:
                        inst.Pinned = True
                if left == 0:
                    doc.Delete(s.Id)
                    deleted += 1
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        forms.alert('出错，已撤销，模型未改动：\n' + traceback.format_exc(), title=TITLE)
        return
    msg = '完成：改名 %d 个类型，%d 个构件换到保留的类型，删除重复类型 %d 个。' % (renamed, updated, deleted)
    if grouped:
        msg += '\n\n有 %d 个构件在编组里，没有改（对应的旧类型也保留了）。解组后再运行一次即可。' % grouped
    forms.alert(msg, title=TITLE)


if __name__ == '__main__':
    main()
