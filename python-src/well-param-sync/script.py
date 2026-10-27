# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '井参数\n同步'
__author__ = 'didi'
__doc__ = ('让所有井的「井盖面标高」等于它的「相对标高的偏移」（井放在哪个标高，井盖面标高就是多少）。\n'
           '只写「井盖面标高」这个记录用的参数，不会去改偏移，所以不会把井挪位置，也不会改变井的样子。改了井的偏移以后点一下就同步。\n'
           '运行前先告诉你有几口井要改，确认后才写；被别人占用或本地文件已过期的井会跳过并列出来。\n'
           '写入的参数名、哪些元素算「井」，都在「设置…」里改（文档\\KaifaTools\\井参数同步设置.json）。')

import io
import os
import re
import traceback
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('System.Windows.Forms')
from Autodesk.Revit import DB
from pyrevit import revit, forms
import System.Windows.Forms as WF
import json

# @include _shared/form_fit.py
# @include _shared/settings_ui.py

doc = revit.doc
uidoc = revit.uidoc
TITLE = '井参数同步'
CFG_FILE = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), 'KaifaTools', '井参数同步设置.json')

DEFAULTS = {
    '说明': ('井参数同步的设置。category=井所在的 Revit 类别（默认常规模型）；mark_regex=井号的写法（用来把井和别的常规模型分开）；'
            'target_param=要写入的参数名（值取自「相对标高的偏移」）；tolerance_mm=差在这个范围内算已经一致。'),
    'category': 'OST_GenericModel',
    'mark_regex': '^[A-Za-z]{1,3}-?\\d',
    'target_param': '井盖面标高',
    'tolerance_mm': 0.5,
}
FT = 304.8


def save_cfg(c):
    d = os.path.dirname(CFG_FILE)
    if not os.path.isdir(d):
        os.makedirs(d)
    txt = json.dumps(c, ensure_ascii=False, indent=1)
    if not isinstance(txt, type('')):
        txt = txt.decode('utf-8')
    with io.open(CFG_FILE, 'w', encoding='utf-8') as f:
        f.write(txt)


def load_cfg():
    c = json.loads(json.dumps(DEFAULTS))
    try:
        if (not os.path.isfile(CFG_FILE)) or os.path.getsize(CFG_FILE) == 0:
            save_cfg(c)
            return c
        with io.open(CFG_FILE, 'r', encoding='utf-8') as f:
            u = json.load(f)
        if isinstance(u, dict):
            for k, v in u.items():
                if k in c and k != '说明':
                    c[k] = v
    except Exception:
        pass
    return c


def settings_fields():
    return [
        {'kind': 'header', 'label': '哪些算「井」'},
        {'key': 'mark_regex', 'label': '井号的写法（正则）', 'kind': 'text',
         'hint': '常规模型里「标记」符合这个写法的才算井。默认：1~3 个字母，可带横杠，后面是数字（A-21、AA-01、T-003）。'},
        {'kind': 'header', 'label': '写哪个参数'},
        {'key': 'target_param', 'label': '要写入的参数名', 'kind': 'text',
         'hint': '默认「井盖面标高」；值取自「相对标高的偏移」。'},
        {'key': 'tolerance_mm', 'label': '差多少以内算一致（mm）', 'kind': 'num', 'min': 0, 'max': 100},
    ]


def open_settings():
    c = load_cfg()
    res = su_fields_dialog('井参数同步 设置', '改完点保存立即生效。', settings_fields(), c, DEFAULTS,
                           extra_buttons=[('用记事本打开文件', lambda: su_open_file(CFG_FILE))])
    if res is None:
        return False
    out = json.loads(json.dumps(c))
    out.update(res)
    save_cfg(out)
    return True


def collect(cfg):
    """[(元素, 井号, 目标参数或 None, 偏移值)]"""
    rx = re.compile(cfg['mark_regex'])
    cat = getattr(DB.BuiltInCategory, cfg['category'])
    out = []
    for e in DB.FilteredElementCollector(doc).OfCategory(cat).WhereElementIsNotElementType().ToElements():
        try:
            p = e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
            mk = p.AsString() if p is not None else None
            if not mk or not rx.match(mk):
                continue
            off = e.get_Parameter(DB.BuiltInParameter.INSTANCE_FREE_HOST_OFFSET_PARAM)
            if off is None:
                continue
            tp = e.LookupParameter(cfg['target_param'])
            out.append((e, mk, tp, off.AsDouble()))
        except Exception:
            continue
    return out


class _Fail(DB.IFailuresPreprocessor):
    def __init__(self, ids):
        self.ids = ids

    def PreprocessFailures(self, fa):
        rb = False
        for f in fa.GetFailureMessages():
            if f.GetSeverity() == DB.FailureSeverity.Warning:
                fa.DeleteWarning(f)
                continue
            rb = True
            try:
                for i in f.GetFailingElementIds():
                    self.ids.add(int(i.Value))
            except Exception:
                pass
        return DB.FailureProcessingResult.ProceedWithRollBack if rb else DB.FailureProcessingResult.Continue


def apply(rows, cfg):
    """写入。被别人占用/文件过期的井，Revit 会在提交时报错并指出是哪几口：排除后重来，最后列出这些井。"""
    tol = float(cfg['tolerance_mm']) / FT
    todo = [r for r in rows if r[2] is not None and not r[2].IsReadOnly
            and not (r[2].HasValue and abs(r[2].AsDouble() - r[3]) <= tol)]
    blocked = set()
    n = 0
    st = None
    for _attempt in range(8):
        ids = set()
        t = DB.Transaction(doc, '井参数同步')
        fho = t.GetFailureHandlingOptions()
        fho.SetFailuresPreprocessor(_Fail(ids))
        t.SetFailureHandlingOptions(fho)
        t.Start()
        n = 0
        try:
            for e, mk, tp, off in todo:
                if int(e.Id.Value) in blocked:
                    continue
                tp.Set(off)
                n += 1
            st = t.Commit()
        except Exception:
            t.RollBack()
            raise
        if str(st) == 'Committed' or not ids:
            break
        blocked |= ids
    names = dict((int(e.Id.Value), mk) for e, mk, tp, off in rows)
    return str(st) == 'Committed', n, [names.get(i, str(i)) for i in sorted(blocked)]


def main():
    try:
        cfg = load_cfg()
        while True:
            pick = forms.CommandSwitchWindow.show(['开始同步（井盖面标高 = 相对标高的偏移）', '⚙ 设置参数…'], message='井参数同步')
            if pick == '⚙ 设置参数…':
                if open_settings():
                    cfg = load_cfg()
                continue
            break
        if not pick:
            return
        rows = collect(cfg)
        if not rows:
            forms.alert('没有找到井（常规模型里「标记」符合 {} 的元素）。'.format(cfg['mark_regex']), title=TITLE)
            return
        tol = float(cfg['tolerance_mm']) / FT
        no_param = [r for r in rows if r[2] is None]
        ro = [r for r in rows if r[2] is not None and r[2].IsReadOnly]
        diff = [r for r in rows if r[2] is not None and not r[2].IsReadOnly
                and not (r[2].HasValue and abs(r[2].AsDouble() - r[3]) <= tol)]
        txt = '共 {} 口井。\n已经一致 {} 口；需要改 {} 口；没有「{}」参数（跳过）{} 口。'.format(
            len(rows), len(rows) - len(diff) - len(no_param) - len(ro), len(diff), cfg['target_param'], len(no_param))
        if not diff:
            forms.alert(txt + '\n\n没有需要同步的。', title=TITLE)
            return
        txt += '\n\n只写「{}」，不改偏移，不会挪井，也不改井的样子。确定同步吗？'.format(cfg['target_param'])
        if WF.MessageBox.Show(txt, TITLE, WF.MessageBoxButtons.OKCancel, WF.MessageBoxIcon.Question) != WF.DialogResult.OK:
            return
        ok, n, blocked = apply(rows, cfg)
        msg = ('完成：写入 {} 口井。'.format(n)) if ok else '提交没有成功，没有任何改动。'
        if blocked:
            msg += '\n以下 {} 口井被跳过（别人占用或你的文件已过期，先「重新载入最新」再点一次）：{}'.format(len(blocked), '、'.join(blocked[:12]))
        msg += '\n不满意可以 Ctrl+Z 撤销。'
        forms.alert(msg, title=TITLE)
    except Exception as ex:
        forms.alert('发生错误：\n{}\n\n{}'.format(ex, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
