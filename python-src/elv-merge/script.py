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

# @include _shared/settings_ui.py

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
