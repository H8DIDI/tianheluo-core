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
