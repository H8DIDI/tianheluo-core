# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '族尺寸\n参数化'
__author__ = 'didi'
__doc__ = ('在族编辑器里用：把「写死」的尺寸（比如井盖离内壁 178 mm）一次变成带参数的尺寸，并保证每个族类型都生效。\n'
           '做法：先选尺寸（在族里选中，或按「当前值」把一模一样的尺寸一次选出来）→ 填参数名、目标值 → 自动新建类型参数、给每个类型设值、把尺寸标注到参数上、'
           '逐个类型重新求解并核对尺寸是不是都等于目标值；全部对上才提交，有一个不对整体回滚，不留半截（避免载入项目时「绑定错乱」）。\n'
           '可以把一次操作存成「配方」，在别的族里一键套用（按尺寸当前值、是否未标注、参照类型来匹配）。\n'
           '所有默认值（分组、公差、推动量、匹配规则）和配方都在文档\\KaifaTools\\族尺寸参数化设置.json 里，换项目、换族直接用。')

import io
import os
import re
import time
import traceback
import collections
import clr
import System
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('System.Windows.Forms')
from Autodesk.Revit import DB
from pyrevit import revit, forms
import System.Windows.Forms as WF
import json

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
uidoc = revit.uidoc
TITLE = '族尺寸参数化'
FT = 304.8
DOCS = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), 'KaifaTools')
CFG_FILE = os.path.join(DOCS, '族尺寸参数化设置.json')

DEFAULTS = {
    '说明': ('族尺寸参数化的设置。group=新参数放在哪个分组（dimensions=尺寸标注、constraints=约束、data=数据、identity=标识数据、general=其他）；'
            'tolerance_mm=核对时尺寸和目标值差多少以内算对；nudge_mm=标注后把参数推一下再设回去的量（逼每个类型重新求解）；'
            'match_tol_mm=按「当前值」选尺寸时，差多少以内算同一个值；include_locked=是否把锁定的尺寸也列出来；'
            'last=上一次的参数名和目标值（下次对话框默认填它）；recipes=配方列表（见下方示例）。'
            '配方的 match：value_mm 尺寸当前值（mm）、tol_mm 公差、unlabeled 只选没标注的、ref_kinds 参照类型必须包含这些（如 ["ReferencePlane","FamilyInstance"]）、'
            'min_count/max_count 选中条数的下限和上限（防止选错）。'),
    'group': 'dimensions',
    'tolerance_mm': 0.5,
    'nudge_mm': 1.0,
    'match_tol_mm': 0.6,
    'include_locked': True,
    'last': {'param': '', 'target_mm': 300.0, 'instance': False},
    'recipes': [
        {'name': '示例：井盖离内壁 300', 'param': '井盖距内壁', 'target_mm': 300.0, 'instance': False,
         'match': {'value_mm': 178.0, 'tol_mm': 0.6, 'unlabeled': True, 'min_count': 2, 'max_count': 8}},
    ],
}

GROUPS = {
    'dimensions': 'Geometry', 'constraints': 'Constraints', 'data': 'Data', 'identity': 'IdentityData', 'general': 'General',
}


# ============================================================
# 设置与配方
# ============================================================

def save_cfg(c):
    if not os.path.isdir(DOCS):
        os.makedirs(DOCS)
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
        {'kind': 'header', 'label': '新参数'},
        {'key': 'group', 'label': '参数分组', 'kind': 'choice',
         'choices': [('dimensions', '尺寸标注'), ('constraints', '约束'), ('data', '数据'), ('identity', '标识数据'), ('general', '其他')]},
        {'kind': 'header', 'label': '核对与求解'},
        {'key': 'tolerance_mm', 'label': '核对公差（mm）', 'kind': 'num', 'min': 0.01, 'max': 50, 'hint': '尺寸和目标值差在这个范围内算对。'},
        {'key': 'nudge_mm', 'label': '推动量（mm）', 'kind': 'num', 'min': 0.1, 'max': 50,
         'hint': '标注后会把每个类型的参数「推」一下再设回去，逼 Revit 重新求解（标注那一刻的当前类型不会自动重算）。'},
        {'kind': 'header', 'label': '选尺寸'},
        {'key': 'match_tol_mm', 'label': '「当前值」的匹配公差（mm）', 'kind': 'num', 'min': 0, 'max': 50},
        {'key': 'include_locked', 'label': '锁定的尺寸也参与', 'kind': 'bool', 'bool_text': '参与（族里常见，锁定的尺寸标注参数后照样生效）'},
    ]


def open_settings():
    c = load_cfg()
    res = su_fields_dialog('族尺寸参数化 设置', '配方和默认值请在设置文件里改（点下面按钮用记事本打开）。',
                           settings_fields(), c, DEFAULTS,
                           extra_buttons=[('用记事本打开文件', lambda: su_open_file(CFG_FILE))])
    if res is None:
        return False
    out = json.loads(json.dumps(c))
    out.update(res)
    save_cfg(out)
    return True


# ============================================================
# 族里的尺寸
# ============================================================

def dim_info(d):
    """一条尺寸的信息；多段、无法标注的返回 None。"""
    try:
        v = d.Value
    except Exception:
        return None
    if v is None:
        return None
    try:
        lab = d.FamilyLabel
    except Exception:
        lab = None
    kinds = []
    refs = []
    try:
        for r in d.References:
            e = doc.GetElement(r.ElementId)
            kinds.append(type(e).__name__ if e is not None else '?')
            refs.append('%s#%d' % (type(e).__name__ if e is not None else '?', int(r.ElementId.Value)))
    except Exception:
        pass
    try:
        lk = bool(d.IsLocked)
    except Exception:
        lk = False
    return {'id': int(d.Id.Value), 'mm': v * FT, 'label': (lab.Definition.Name if lab is not None else None),
            'locked': lk, 'kinds': kinds, 'refs': refs, 'el': d}


def all_dims(cfg):
    out = []
    for d in DB.FilteredElementCollector(doc).OfClass(DB.Dimension).ToElements():
        i = dim_info(d)
        if i is None:
            continue
        if i['locked'] and not cfg.get('include_locked', True):
            continue
        out.append(i)
    return out


def selected_dims(cfg):
    ids = [int(x.Value) for x in uidoc.Selection.GetElementIds()]
    sel = [i for i in all_dims(cfg) if i['id'] in ids]
    return sel


def match_dims(cfg, rule):
    """按配方规则选尺寸：返回 (列表, 说明)。"""
    tol = float(rule.get('tol_mm', cfg.get('match_tol_mm', 0.6)))
    val = rule.get('value_mm')
    need = set(rule.get('ref_kinds') or [])
    out = []
    for i in all_dims(cfg):
        if val is not None and abs(i['mm'] - float(val)) > tol:
            continue
        if rule.get('unlabeled', True) and i['label']:
            continue
        if need and not need.issubset(set(i['kinds'])):
            continue
        out.append(i)
    return out


def group_by_value(cfg):
    """未标注、单段的尺寸按当前值分组：[(值, [尺寸...])]，条数多的在前。"""
    tol = float(cfg.get('match_tol_mm', 0.6))
    groups = []
    for i in sorted([i for i in all_dims(cfg) if not i['label'] and i['mm'] > 0.5], key=lambda x: x['mm']):
        if groups and abs(groups[-1][0] - i['mm']) <= tol:
            groups[-1][1].append(i)
        else:
            groups.append([i['mm'], [i]])
    return groups


# ============================================================
# 参数化（核心）
# ============================================================

class _Fail(DB.IFailuresPreprocessor):
    """提交时 Revit 若报「错误」，就整体回滚（并记下原因）；警告照常放行。"""

    def __init__(self, log):
        self.log = log
        self.errors = 0

    def PreprocessFailures(self, fa):
        rb = False
        for f in fa.GetFailureMessages():
            try:
                sev = f.GetSeverity()
                txt = f.GetDescriptionText()
            except Exception:
                continue
            if sev == DB.FailureSeverity.Warning:
                try:
                    fa.DeleteWarning(f)
                except Exception:
                    pass
                continue
            self.errors += 1
            rb = True
            if len(self.log) < 400:
                self.log.append('   Revit 报错：%s' % txt)
        return DB.FailureProcessingResult.ProceedWithRollBack if rb else DB.FailureProcessingResult.Continue


def _group_id(name):
    attr = GROUPS.get(name, 'Geometry')
    try:
        return getattr(DB.GroupTypeId, attr)
    except Exception:
        return DB.GroupTypeId.Geometry


def _snapshot(fm, skip=()):
    """每个类型的全部「类型参数」当前值：{类型名: {参数名: 值}}。标注/求解之后拿来对比，
    发现别的参数被 Revit 悄悄改了（锁定约束互相矛盾时，求解会把驱动尺寸的参数改掉）就判失败。"""
    out = {}
    params = [p for p in fm.Parameters if not p.IsInstance and p.Definition.Name not in skip]
    for ty in fm.Types:
        row = {}
        for p in params:
            try:
                st = p.StorageType
                if st == DB.StorageType.Double:
                    row[p.Definition.Name] = ty.AsDouble(p)
                elif st == DB.StorageType.Integer:
                    row[p.Definition.Name] = ty.AsInteger(p)
                elif st == DB.StorageType.String:
                    row[p.Definition.Name] = ty.AsString(p)
            except Exception:
                continue
        out[ty.Name] = row
    return out


def _fmt_v(x):
    return ('%.1f' % (x * FT)) if isinstance(x, float) else ('%s' % x)


def _changed_params(before_row, after_row):
    diffs = []
    for k, a in before_row.items():
        b = after_row.get(k)
        if isinstance(a, float) or isinstance(b, float):
            if a is None or b is None or abs(a - b) * FT > 0.05:
                diffs.append((k, a, b))
        elif a != b:
            diffs.append((k, a, b))
    return diffs


def _warn_count():
    try:
        return len(doc.GetWarnings())
    except Exception:
        return 0


def _max_overlap():
    """族里嵌套族实例（如井盖）之间互相重叠的最大深度（mm）。井盖要离内壁更远时，窄的井里两个井盖会撞在一起，
    Revit 不会报错，只有量出来才知道。"""
    boxes = []
    for fi in DB.FilteredElementCollector(doc).OfClass(DB.FamilyInstance).ToElements():
        try:
            b = fi.get_BoundingBox(None)
            if b is not None:
                boxes.append(b)
        except Exception:
            continue
    worst = 0.0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, c = boxes[i], boxes[j]
            ox = min(a.Max.X, c.Max.X) - max(a.Min.X, c.Min.X)
            oy = min(a.Max.Y, c.Max.Y) - max(a.Min.Y, c.Min.Y)
            oz = min(a.Max.Z, c.Max.Z) - max(a.Min.Z, c.Min.Z)
            if ox > 0 and oy > 0 and oz > 0:
                worst = max(worst, min(ox, oy, oz) * FT)
    return worst


def _read_dim_values(dims, types):
    """每个类型下这批尺寸原来的值（mm）：在回滚的事务里逐个类型切换读出。"""
    fm = doc.FamilyManager
    orig = fm.CurrentType
    out = {}
    t = DB.Transaction(doc, '读尺寸原值（回滚）')
    t.Start()
    try:
        for ty in types:
            fm.CurrentType = ty
            doc.Regenerate()
            out[ty.Name] = [doc.GetElement(DB.ElementId(d['id'])).Value * FT for d in dims]
            _OV_BASE[ty.Name] = _max_overlap()
    finally:
        t.RollBack()
    return out


_OV_BASE = {}      # 类型名 → 开工前嵌套族之间的最大重叠（mm）


def _check_type(fm, ty, dims, want, pname, base_snap, base_warn, tol):
    """当前类型已是 ty 且已求解：核对尺寸值、别的类型参数没被改、没有新警告。返回问题说明或 None。"""
    vals = [doc.GetElement(DB.ElementId(d['id'])).Value * FT for d in dims]
    worst = max(abs(v - want) for v in vals) if vals else 0.0
    if worst > tol:
        return '尺寸没达到 %g mm（实际 %s）' % (want, ' / '.join('%.1f' % v for v in vals))
    if base_snap is not None:
        cur = _snapshot(fm, skip=(pname,)).get(ty.Name, {})
        dif = _changed_params(base_snap.get(ty.Name, {}), cur)
        if dif:
            return '求解时别的参数被改掉了：%s' % '；'.join('%s %s→%s' % (k, _fmt_v(a), _fmt_v(b)) for k, a, b in dif[:4])
    if _warn_count() > base_warn:
        return '求解后族里出现新的警告/错误'
    ov = _max_overlap()
    if ov > _OV_BASE.get(ty.Name, 0.0) + 1.0:
        return '嵌套族（如井盖）之间互相重叠了 %.0f mm（原来是 %.0f mm）：这个类型放不下' % (ov, _OV_BASE.get(ty.Name, 0.0))
    return None


def _repair(fm, types, base_snap, pname):
    """Revit 在类型之间切换时，偶尔会把「别的类型的参数值」写进当前类型（实测：标注之后切回原来的当前类型最容易中招）。
    这里把被改掉的参数值逐个写回快照里的原值。返回修了几个类型。"""
    cur = _snapshot(fm, skip=(pname,))
    byname = dict((p.Definition.Name, p) for p in fm.Parameters)
    fixed = 0
    for ty in types:
        dif = _changed_params(base_snap.get(ty.Name, {}), cur.get(ty.Name, {}))
        if not dif:
            continue
        fm.CurrentType = ty
        for k, a, b in dif:
            p = byname.get(k)
            if p is None or a is None:
                continue
            try:
                if p.IsReadOnly or (p.Formula or ''):
                    continue
                fm.Set(p, a)
            except Exception:
                continue
        doc.Regenerate()
        fixed += 1
    return fixed


def _do_pass(cfg, dims, pname, values, instance, commit, log, only_type=None, base_snap=None, base_warn=0):
    """做一遍：加参数/设值/标注/逐类型求解并核对。values={类型名: 该类型的目标 mm}。
    only_type 不为空时只核对这个类型（可行性试探用）。返回 (通过?, {类型名: 问题说明或None})。"""
    fm = doc.FamilyManager
    tol = float(cfg.get('tolerance_mm', 0.5))
    nudge = float(cfg.get('nudge_mm', 1.0))
    types = list(fm.Types)
    orig = fm.CurrentType or types[0]
    t = DB.Transaction(doc, '参数化尺寸：' + pname)
    pre = _Fail(log)
    fho = t.GetFailureHandlingOptions()
    fho.SetFailuresPreprocessor(pre)
    t.SetFailureHandlingOptions(fho)
    t.Start()
    verdict = {}
    ok = False
    done = False
    try:
        exist = [p for p in fm.Parameters if p.Definition.Name == pname]
        fp = exist[0] if exist else fm.AddParameter(pname, _group_id(cfg.get('group')), DB.SpecTypeId.Length, bool(instance))
        for ty in types:
            fm.CurrentType = ty
            fm.Set(fp, values[ty.Name] / FT)
        test_types = [ty for ty in types if (only_type is None or ty.Name == only_type)]
        # 标注那一刻的「当前类型」会漏算：先切到一个不被核对的类型再标注
        anchor = [ty for ty in types if only_type is None or ty.Name != only_type]
        fm.CurrentType = anchor[0] if anchor else types[0]
        for d in dims:
            doc.GetElement(DB.ElementId(d['id'])).FamilyLabel = fp
        doc.Regenerate()
        for ty in test_types:
            fm.CurrentType = ty
            doc.Regenerate()
            want = values[ty.Name]
            fm.Set(fp, (want + nudge) / FT)
            doc.Regenerate()
            fm.Set(fp, want / FT)
            doc.Regenerate()
            problem = _check_type(fm, ty, dims, want, pname, base_snap, base_warn, tol)
            verdict[ty.Name] = problem
            if instance:
                break
        fm.CurrentType = orig
        doc.Regenerate()
        if base_snap is not None and not instance:
            # 修复 + 复核（最多 3 轮）：把被 Revit 悄悄改掉的类型参数写回，再逐类型核对
            for _it in range(3):
                nfix = _repair(fm, types, base_snap, pname)
                fm.CurrentType = orig
                doc.Regenerate()
                bad_now = {}
                for ty in test_types:
                    fm.CurrentType = ty
                    doc.Regenerate()
                    bad_now[ty.Name] = _check_type(fm, ty, dims, values[ty.Name], pname, base_snap, base_warn, tol)
                fm.CurrentType = orig
                doc.Regenerate()
                bad_now[orig.Name] = _check_type(fm, orig, dims, values[orig.Name], pname, base_snap, base_warn, tol) if (only_type is None or only_type == orig.Name) else bad_now.get(orig.Name)
                if nfix:
                    log.append('  （修复了 %d 个被改掉参数的类型，第 %d 轮）' % (nfix, _it + 1))
                verdict.update(dict((k, v) for k, v in bad_now.items() if k in verdict or v is not None))
                if all(v is None for v in bad_now.values()) or not nfix:
                    break
        ok = all(v is None for v in verdict.values())
    except Exception as ex:
        log.append('出错：%s' % str(ex).split('\n')[0])
        ok = False
    finally:
        if ok and commit:
            try:
                res = t.Commit()
                done = (str(res) == 'Committed')
                if not done:
                    log.append('提交被 Revit 拒绝（%s）' % res)
            except Exception as ex:
                log.append('提交失败：%s' % str(ex).split('\n')[0])
        else:
            try:
                t.RollBack()
            except Exception:
                pass
    if commit:
        return (ok and done), verdict
    return ok, verdict


def parameterize(cfg, dims, pname, target_mm, instance=False, apply=True, per_type=None):
    """给这批尺寸标注参数，每个类型都核对。返回 (是否成功, 报告行, 提交了吗)。
    对每个类型先单独试探（回滚）：能做到目标值、且没有别的参数被改、没有新警告/错误，才用目标值；
    做不到的类型（比如井太窄、两个井盖放不下）保持原来的尺寸值，在报告里写明。最后整体再核对一遍才提交。
    per_type 可给个别类型指定不同的值：{类型名或前缀*: mm}。"""
    log = []
    fm = doc.FamilyManager
    types = list(fm.Types)
    if not types:
        return False, ['族里没有类型（先在「族类型」里建至少一个类型）。'], False
    log.append('尺寸 %d 条：%s' % (len(dims), [d['id'] for d in dims]))
    log.append('参数「%s」（%s参数）目标 %g mm；%d 个类型' % (pname, '实例' if instance else '类型', target_mm, len(types)))
    exist = [p for p in fm.Parameters if p.Definition.Name == pname]
    if exist:
        p0 = exist[0]
        if bool(p0.IsInstance) != bool(instance):
            return False, log + ['已有同名参数「%s」但是%s参数，和你选的不一致，停止。' % (pname, '实例' if p0.IsInstance else '类型')], False
        try:
            if 'length' not in str(p0.Definition.GetDataType().TypeId).lower():
                return False, log + ['已有同名参数「%s」，但不是长度类型，停止。' % pname], False
        except Exception:
            pass
        log.append('已有同名参数，复用。')
    base_snap = _snapshot(fm, skip=(pname,))
    base_warn = _warn_count()
    orig_cur = fm.CurrentType.Name if fm.CurrentType is not None else types[0].Name
    orig_vals = _read_dim_values(dims, types)
    want = {}
    for ty in types:
        w = target_mm
        for pat, mm in (per_type or {}).items():
            if pat.endswith('*'):
                if ty.Name.startswith(pat[:-1]):
                    w = float(mm)
            elif ty.Name == pat:
                w = float(mm)
        want[ty.Name] = w
    verdict_all = {}
    use = {}
    if instance:
        use = dict((ty.Name, target_mm) for ty in types)
    else:
        for ty in types:
            ov = orig_vals.get(ty.Name, [None])[0]
            vals_try = {}
            for t2 in types:
                vals_try[t2.Name] = want[t2.Name] if t2.Name == ty.Name else (orig_vals.get(t2.Name, [want[t2.Name]])[0])
            ok1, v1 = _do_pass(cfg, dims, pname, vals_try, instance, False, [], only_type=ty.Name, base_snap=base_snap, base_warn=base_warn)
            prob = v1.get(ty.Name)
            if prob is None and not ok1:
                prob = '试探时出错'
            verdict_all[ty.Name] = prob
            use[ty.Name] = want[ty.Name] if prob is None else (ov if ov is not None else want[ty.Name])
    infeasible = [n for n, p in verdict_all.items() if p is not None]
    for ty in types:
        p = verdict_all.get(ty.Name)
        if instance:
            break
        if p is None:
            log.append('  ✔ %-22s → %g mm' % (ty.Name, use[ty.Name]))
        else:
            log.append('  ⚠ %-22s 做不到 %g mm（%s）→ 保持原值 %.1f mm' % (ty.Name, want[ty.Name], p, use[ty.Name]))
    ok, verdict = _do_pass(cfg, dims, pname, use, instance, apply, log, only_type=None, base_snap=base_snap, base_warn=base_warn)
    for n, p in verdict.items():
        if p is not None:
            log.append('  ✘ 整体核对 %s：%s' % (n, p))
    if ok and apply:
        log.append('已提交。族还没有保存：请检查后保存，再载入项目。%s' % (('其中 %d 个类型保持原值。' % len(infeasible)) if infeasible else ''))
    elif ok:
        log.append('（试运行：全部通过，已回滚，没有改动。%s）' % (('其中 %d 个类型做不到目标值，会保持原值。' % len(infeasible)) if infeasible else ''))
    else:
        log.append('没有任何改动（已回滚）。')
    return ok, log, bool(ok and apply)



# ============================================================
# 交互
# ============================================================

def _ask_param(cfg, dims, default_val=None):
    last = cfg.get('last') or {}
    fields = [
        {'kind': 'header', 'label': '这 %d 条尺寸要变成参数' % len(dims)},
        {'key': 'param', 'label': '参数名', 'kind': 'text', 'required': True, 'hint': '例如：井盖距内壁。已有同名长度参数会复用。'},
        {'key': 'target_mm', 'label': '目标值（mm）', 'kind': 'num', 'min': 0.1, 'max': 100000,
         'hint': '所有类型都设成这个值；各类型要不一样，之后在「族类型」里再改。'},
        {'key': 'instance', 'label': '参数种类', 'kind': 'choice', 'choices': [(False, '类型参数（推荐）'), (True, '实例参数')]},
    ]
    vals = {'param': last.get('param') or '', 'target_mm': default_val if default_val is not None else last.get('target_mm', 300.0),
            'instance': bool(last.get('instance'))}
    res = su_fields_dialog(TITLE, '当前这些尺寸的值：%s mm。' % '、'.join(sorted(set('%.1f' % d['mm'] for d in dims))), fields, vals,
                           {'param': '', 'target_mm': 300.0, 'instance': False}, width=620, height=420)
    return res


def _save_report(lines):
    if not os.path.isdir(DOCS):
        os.makedirs(DOCS)
    path = os.path.join(DOCS, '族尺寸参数化报告_%s.txt' % time.strftime('%Y%m%d_%H%M%S'))
    with io.open(path, 'w', encoding='utf-8-sig') as f:
        f.write('\r\n'.join(lines))
    return path


def do_one(cfg, dims, label):
    res = _ask_param(cfg, dims)
    if res is None:
        return
    pname = (res['param'] or '').strip()
    if not pname:
        forms.alert('参数名不能为空。', title=TITLE)
        return
    target = float(res['target_mm'])
    inst = bool(res['instance'])
    ok, log, done = parameterize(cfg, dims, pname, target, inst, apply=False)       # 先试运行
    head = ['族：%s' % doc.Title, '时间：%s' % time.strftime('%Y-%m-%d %H:%M:%S'), '来源：%s' % label, '']
    if not ok:
        path = _save_report(head + log)
        forms.alert('试运行没有通过，没有改动。\n\n' + '\n'.join(log[-6:]) + '\n\n详细报告：' + path, title=TITLE)
        return
    ans = WF.MessageBox.Show('试运行通过：{} 个类型的尺寸都能做到 {:g} mm。\n\n正式修改这个族吗？（只改族文档，不保存、不载入项目。）'.format(
        len(list(doc.FamilyManager.Types)), target), TITLE, WF.MessageBoxButtons.OKCancel, WF.MessageBoxIcon.Question)
    if ans != WF.DialogResult.OK:
        return
    ok2, log2, done2 = parameterize(cfg, dims, pname, target, inst, apply=True)
    cfg['last'] = {'param': pname, 'target_mm': target, 'instance': inst}
    save_cfg(cfg)
    path = _save_report(head + log2)
    forms.alert(('已完成。\n' if done2 else '没有成功。\n') + '\n'.join(log2[-5:]) + '\n\n详细报告：' + path, title=TITLE)


def do_by_selection(cfg):
    dims = selected_dims(cfg)
    if not dims:
        forms.alert('请先在族编辑器里选中要参数化的尺寸标注（可以多选，按住 Ctrl），再点这个。\n\n或者用「按当前值选尺寸」。', title=TITLE)
        return
    multi = [d for d in dims if d['label']]
    if multi:
        forms.alert('选中的尺寸里有 %d 条已经标注了参数（%s），先把它们去掉再试。' % (len(multi), '、'.join(sorted(set(d['label'] for d in multi)))), title=TITLE)
        return
    do_one(cfg, dims, '选中的尺寸')


def do_by_value(cfg):
    groups = group_by_value(cfg)
    groups = [g for g in groups if len(g[1]) >= 1]
    if not groups:
        forms.alert('族里没有未标注参数的尺寸。', title=TITLE)
        return
    groups.sort(key=lambda g: (-len(g[1]), g[0]))
    labels = ['%.1f mm　（%d 条）' % (g[0], len(g[1])) for g in groups[:30]]
    pick = forms.CommandSwitchWindow.show(labels, message='选一组「当前值」相同的未标注尺寸')
    if not pick:
        return
    g = groups[labels.index(pick)]
    ex = '\n'.join('  #%d  %s  %s' % (d['id'], '锁定' if d['locked'] else '未锁', ' ↔ '.join(d['refs'][:3])) for d in g[1][:10])
    ans = WF.MessageBox.Show('这一组共 %d 条尺寸，值都是 %.1f mm：\n%s\n\n都参数化吗？' % (len(g[1]), g[0], ex), TITLE, WF.MessageBoxButtons.OKCancel, WF.MessageBoxIcon.Question)
    if ans != WF.DialogResult.OK:
        return
    do_one(cfg, g[1], '当前值 %.1f mm 的未标注尺寸' % g[0])


def do_recipes(cfg):
    recs = cfg.get('recipes') or []
    if not recs:
        forms.alert('设置文件里没有配方。', title=TITLE)
        return
    labels = [r.get('name', '未命名') for r in recs]
    pick = forms.CommandSwitchWindow.show(labels, message='选一个配方套用到当前族')
    if not pick:
        return
    r = recs[labels.index(pick)]
    m = r.get('match', {})
    dims = match_dims(cfg, m)
    lo, hi = int(m.get('min_count', 1)), int(m.get('max_count', 999))
    if not (lo <= len(dims) <= hi):
        forms.alert('配方「%s」匹配到 %d 条尺寸，但配方要求 %d 到 %d 条，为避免选错，停止。\n（匹配规则：当前值 %s mm）' % (
            pick, len(dims), lo, hi, m.get('value_mm')), title=TITLE)
        return
    target = float(r['target_mm'])
    pname = r['param']
    inst = bool(r.get('instance'))
    ok, log, _d = parameterize(cfg, dims, pname, target, inst, apply=False)
    if not ok:
        path = _save_report(['配方：%s' % pick] + log)
        forms.alert('试运行没有通过，没有改动。\n\n' + '\n'.join(log[-6:]) + '\n\n报告：' + path, title=TITLE)
        return
    ans = WF.MessageBox.Show('配方「%s」匹配到 %d 条尺寸，试运行通过。\n参数「%s」= %g mm。\n\n正式修改这个族吗？' % (pick, len(dims), pname, target), TITLE,
                             WF.MessageBoxButtons.OKCancel, WF.MessageBoxIcon.Question)
    if ans != WF.DialogResult.OK:
        return
    ok2, log2, done2 = parameterize(cfg, dims, pname, target, inst, apply=True)
    path = _save_report(['配方：%s' % pick] + log2)
    forms.alert(('已完成。\n' if done2 else '没有成功。\n') + '\n'.join(log2[-5:]) + '\n\n报告：' + path, title=TITLE)


def do_save_recipe(cfg):
    last = cfg.get('last') or {}
    dims = selected_dims(cfg)
    if not dims:
        forms.alert('先选中一条或几条已经标注好参数的尺寸，我据此生成配方的匹配规则。', title=TITLE)
        return
    d0 = dims[0]
    name = d0['label'] or last.get('param') or '新配方'
    rule = {'name': '配方：' + name, 'param': name, 'target_mm': round(d0['mm'], 1), 'instance': False,
            'match': {'value_mm': round(d0['mm'], 1), 'tol_mm': 0.6, 'unlabeled': True, 'ref_kinds': sorted(set(d0['kinds'])),
                      'min_count': 1, 'max_count': 12}}
    cfg.setdefault('recipes', []).append(rule)
    save_cfg(cfg)
    forms.alert('已把「%s」存成配方（匹配规则：当前值 %.1f mm 的未标注尺寸，参照类型含 %s）。\n要改匹配条数范围等，在设置文件里改。' % (
        rule['name'], d0['mm'], '、'.join(sorted(set(d0['kinds'])))), title=TITLE)


def do_list(cfg):
    rows = all_dims(cfg)
    by = collections.Counter()
    for i in rows:
        by[(round(i['mm'], 1), i['label'] or '（未标注）')] += 1
    lines = ['族 %s：尺寸 %d 条（单段）' % (doc.Title, len(rows)), '']
    for (v, lab), n in sorted(by.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        lines.append('%-10s %8.1f mm  × %d' % (lab, v, n))
    fm = doc.FamilyManager
    lines += ['', '族参数：']
    for p in fm.Parameters:
        lines.append('  %s%s%s' % (p.Definition.Name, '（实例）' if p.IsInstance else '', '（共享）' if p.IsShared else ''))
    path = _save_report(lines)
    forms.alert('已列出，共 %d 条尺寸。报告：%s' % (len(rows), path), title=TITLE)
    try:
        su_open_file(path)
    except Exception:
        pass


def main():
    try:
        if not doc.IsFamilyDocument:
            forms.alert('这个命令要在族编辑器里用（先在项目里选中族 → 编辑族，或打开 .rfa）。', title=TITLE)
            return
        cfg = load_cfg()
        labels = ['① 参数化「选中的尺寸」', '② 按「当前值」选尺寸再参数化', '③ 套用配方', '④ 把选中的尺寸存成配方', '⑤ 列出族里的尺寸与参数', '⚙ 设置…']
        while True:
            pick = forms.CommandSwitchWindow.show(labels, message='族尺寸参数化（当前族：%s）' % doc.Title)
            if pick == labels[5]:
                if open_settings():
                    cfg = load_cfg()
                continue
            break
        if pick == labels[0]:
            do_by_selection(cfg)
        elif pick == labels[1]:
            do_by_value(cfg)
        elif pick == labels[2]:
            do_recipes(cfg)
        elif pick == labels[3]:
            do_save_recipe(cfg)
        elif pick == labels[4]:
            do_list(cfg)
    except Exception as ex:
        forms.alert('发生错误：\n{}\n\n{}'.format(ex, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
