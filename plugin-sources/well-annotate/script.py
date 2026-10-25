# -*- coding: utf-8 -*-
__title__ = '一键井大样标注'
__doc__ = ('选中井/图纸 → 勾选要标注的视图 → 生成两类尺寸：\n'
           '① 总尺寸：两侧内壁之间的净尺寸，下方标宽、左侧标高（平面、南标宽和高；北只标宽；东、西不标）；\n'
           '② 井内壁至排管尺寸链：识别井内壁，对位于井内的每根排管，下方/上方标水平尺寸链'
           '（内壁—排管边—排管宽—排管边—内壁，一条多段尺寸），左侧/右侧标竖向尺寸链（同理，一条多段尺寸）。\n'
           '排管尺寸链在平面与剖面/立面视图都生成。\n'
           '本工具只新增尺寸，不改视图：不动裁剪框、不隐藏/显示任何构件、不删除视图里已有的尺寸；'
           '同位置已有尺寸的不重复标；超出注释裁剪框的尺寸照常标，报告里列出，需要显示时往外拉。')

from pyrevit import revit, DB, UI, forms

# 图纸名前缀来自项目配置（文档\KaifaTools\井大样配置.json），默认「井大样_」
# ---- well_config（天河落共用片段，打包时内联进脚本）-----------------------------
# 井大样系列插件（一键井展开图 / 一键井大样标注 / 图纸归类与图框编号 / 导出井大样DWG）共用的项目配置。
# 配置文件：文档\KaifaTools\井大样配置.json。第一次运行时自动写出一份默认配置（与原来写死的规则完全一样），
# 换项目时只改这个文件：图纸名前缀、图号前缀、井编号系列（前缀 → 系统名 → 系统代码）、图框 logo 位置。
import io as _wc_io
import os as _wc_os
import json as _wc_json
import re as _wc_re

WCFG_FILE = _wc_os.path.join(
    __import__('System').Environment.GetFolderPath(__import__('System').Environment.SpecialFolder.MyDocuments),
    u"KaifaTools", u"井大样配置.json")

WCFG_DEFAULTS = {
    u"说明": (u"井大样系列插件的项目配置。改完保存，下次点插件生效；删掉这个文件会恢复默认。"
            u"series 里每一项是一个井编号系列：prefix=井号前缀（井号形如 AA-01、T-068、室内-A-01，前缀后面可以有一个 -，再跟数字），"
            u"system=系统名（写进图纸的「图纸系统」参数、用来归类），code=系统代码（图号模板里的 {系统码}），label=菜单里显示的名字。"
            u"顺序就是菜单和排序的顺序；匹配时长的前缀优先（AA 先于 A）。"),
    u"sheet_name_prefix": u"井大样_",
    u"sheet_number_prefix": u"AG-",
    u"indoor_prefix": u"室内-",
    u"system_param": u"图纸系统",
    u"series": [
        {u"prefix": u"室内-A", u"system": u"室内强电井", u"code": u"NQ", u"label": u"室内-A- 强电井"},
        {u"prefix": u"室内-T", u"system": u"室内通信井", u"code": u"NT", u"label": u"室内-T- 通信井"},
        {u"prefix": u"室内-E", u"system": u"室内弱电井", u"code": u"NR", u"label": u"室内-E- 弱电井"},
        {u"prefix": u"室内-L", u"system": u"室内低压拉线井", u"code": u"ND", u"label": u"室内-L- 低压拉线井"},
        {u"prefix": u"AA", u"system": u"强电井", u"code": u"QD", u"label": u"AA- 强电特型井"},
        {u"prefix": u"A", u"system": u"强电井", u"code": u"QD", u"label": u"A- 强电井"},
        {u"prefix": u"BB", u"system": u"弱电井", u"code": u"RD", u"label": u"BB- 弱电特型井"},
        {u"prefix": u"B", u"system": u"弱电井", u"code": u"RD", u"label": u"B- 弱电井"},
        {u"prefix": u"T", u"system": u"通信井", u"code": u"TX", u"label": u"T- 通信井"},
        {u"prefix": u"L", u"system": u"低压拉线井", u"code": u"DY", u"label": u"L- 低压拉线井"},
        {u"prefix": u"E", u"system": u"预制井", u"code": u"YZ", u"label": u"E- 预制井"},
        {u"prefix": u"Y", u"system": u"雨水井", u"code": u"YS", u"label": u"Y- 雨水井"},
    ],
    u"other": {u"system": u"其他", u"code": u"QT"},
    # 图名模板（按井号前缀，长前缀优先）：{井号} 换成井号，如 T-001。没有模板的井沿用「井大样_」前缀。
    # 图纸集合（按井号前缀）：新图放进对应的图纸集合，便于按集合导出 PDF。名字须与项目里的图纸集合一致。
    u"sheet_collection_by_prefix": {
        u"AA": u"275KV井大样", u"BB": u"275KV井大样", u"A": u"11KV井大样", u"B": u"11KV井大样",
        u"E": u"ELV井大样", u"L": u"LV井大样", u"T": u"TELCO井大样",
    },
    u"sheet_name_templates": {
        u"AA": u"BTP-IF1 275Kv-{井号} Detail Drawing",
        u"BB": u"BTP-IF1 275Kv-{井号} Detail Drawing",
        u"A": u"BTP-IF1 11Kv-{井号} Detail Drawing",
        u"B": u"BTP-IF1 11Kv-{井号} Detail Drawing",
        u"E": u"BTP-IF1 ELV-{井号} Detail Drawing",
        u"L": u"BTP-IF1 Lv-{井号} Detail Drawing",
        u"T": u"BTP-IF1 Telco-{井号} Detail Drawing",
    },
    u"logo": {u"enabled": True, u"file": u"frame_logo.jpg", u"x0": 736.54, u"y0": 141.10, u"w": 91.49,
              u"说明": u"导出 DWG 后补插的图框 logo：x0/y0 = logo 左下角在 Layout1 里的坐标，w = 宽度（按 A1 横向图框标定）。换图框要改；enabled=false 不补 logo。"},
}

_WCFG_CACHE = [None]


def _wcfg_merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _wcfg_merge(out[k], v)
        else:
            out[k] = v
    return out


def wcfg_load(write_default=True):
    """读项目配置（缺的项用默认值补齐）。文件不存在时写出默认配置，方便用户找到并修改。"""
    if _WCFG_CACHE[0] is not None:
        return _WCFG_CACHE[0]
    cfg = _wcfg_merge(WCFG_DEFAULTS, {})
    try:
        if _wc_os.path.exists(WCFG_FILE):
            with _wc_io.open(WCFG_FILE, 'r', encoding='utf-8-sig') as f:
                cfg = _wcfg_merge(cfg, _wc_json.load(f))
        elif write_default:
            d = _wc_os.path.dirname(WCFG_FILE)
            if not _wc_os.path.isdir(d):
                _wc_os.makedirs(d)
            with _wc_io.open(WCFG_FILE, 'w', encoding='utf-8') as f:
                f.write(_wc_json.dumps(WCFG_DEFAULTS, ensure_ascii=False, indent=2))
    except Exception:
        cfg = _wcfg_merge(WCFG_DEFAULTS, {})
    # series 至少要有 prefix
    good = []
    for s in cfg.get(u"series") or []:
        try:
            p = (s.get(u"prefix") or u"").strip()
        except Exception:
            continue
        if not p:
            continue
        s = dict(s)
        s[u"prefix"] = p
        s.setdefault(u"system", p)
        s.setdefault(u"code", p)
        s.setdefault(u"label", p + u"-")
        good.append(s)
    cfg[u"series"] = good or list(WCFG_DEFAULTS[u"series"])
    _WCFG_CACHE[0] = cfg
    return cfg


def wcfg_series_of(mark, cfg=None, strict=False):
    """井号属于哪个系列：前缀（长的优先）后面可以有一个 -，必须紧跟数字。认不出返回 None。
    strict=True 时前缀后面必须是「-数字」（井的标记用这个：雨水井的 A1、BB24 这类不带 - 的不算强电/弱电井）。"""
    cfg = cfg or wcfg_load()
    m = (mark or u"").strip()
    for s in sorted(cfg[u"series"], key=lambda x: -len(x[u"prefix"])):
        p = s[u"prefix"]
        if m.startswith(p):
            rest = m[len(p):]
            if rest[:1] == u"-":
                rest = rest[1:]
            elif strict:
                continue
            if rest[:1].isdigit():
                return s
    return None


def wcfg_series_index(s, cfg=None):
    cfg = cfg or wcfg_load()
    for i, x in enumerate(cfg[u"series"]):
        if x is s:
            return i
    return len(cfg[u"series"])


def wcfg_natural_key(text):
    out = []
    for tok in _wc_re.split(r'(\d+)', text or u''):
        out.append((0, int(tok), u'') if tok.isdigit() else (1, 0, tok.lower()))
    return out


def wcfg_well_sort_key(mark, cfg=None, strict=True):
    """先按系列在配置里的顺序，同系列按号（自然排序：A-2 在 A-10 前面）。"""
    s = wcfg_series_of(mark, cfg, strict)
    return (wcfg_series_index(s, cfg) if s is not None else 9999, wcfg_natural_key(mark))


def wcfg_sheet_name(mark, cfg=None):
    """井号 → 图名（按配置里的图名模板）。没有对应模板时返回 None，调用方沿用「井大样_」前缀。
    模板前缀按长度从长到短匹配，且前缀后面必须紧跟 "-"（AA-04 不会误判成 A 系列）。"""
    cfg = cfg or wcfg_load()
    tpls = cfg.get(u"sheet_name_templates") or {}
    m = (mark or u"").strip()
    for key in sorted(tpls.keys(), key=lambda k: -len(k)):
        if m.startswith(key + u"-"):
            return tpls[key].replace(u"{井号}", m)
    return None
# ---- well_config 结束 ----------------------------------------------------------


def wcfg_collection_name(mark, cfg=None):
    """井号 → 图纸集合名（按配置 sheet_collection_by_prefix，长前缀优先，前缀后紧跟 -）。没有对应返回 None。"""
    cfg = cfg or wcfg_load()
    maps = cfg.get(u"sheet_collection_by_prefix") or {}
    m = (mark or u"").strip()
    for key in sorted(maps.keys(), key=lambda k: -len(k)):
        if m.startswith(key + u"-"):
            return maps[key]
    return None


# ---- 井位置缓存（井没动就不重新生成）：Documents\KaifaTools\井大样位置缓存.json ----
def wcfg_cache_path():
    return _wc_os.path.join(_wc_os.path.dirname(WCFG_FILE), u"井大样位置缓存.json")


def wcfg_cache_load():
    """读位置缓存：{井号: {bbox, sheet_id, name, number}}。文件不存在或坏了返回空。"""
    try:
        with _wc_io.open(wcfg_cache_path(), 'r', encoding='utf-8-sig') as f:
            d = _wc_json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def wcfg_cache_save(d):
    """先把内容转成文字，转成功才写文件（避免转换失败时把旧缓存清成空文件）。"""
    try:
        txt = _wc_json.dumps(d, ensure_ascii=False, indent=1)
    except Exception:
        return
    try:
        p = wcfg_cache_path()
        dd = _wc_os.path.dirname(p)
        if not _wc_os.path.isdir(dd):
            _wc_os.makedirs(dd)
        with _wc_io.open(p, 'w', encoding='utf-8') as f:
            f.write(txt)
    except Exception:
        pass

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

WCFG = wcfg_load()
WS_PREFIX = WCFG[u"sheet_name_prefix"]
import io
import os
import json
import re
import System
import traceback

doc = revit.doc
uidoc = revit.uidoc

# ============================================================
# 可调参数（改这里即可；数值是当前默认值，改后重新运行生效）
# ============================================================

# 尺寸线离框的间距（英尺，1.0 约 305 mm）：下方总宽线、左侧总高线与井外包框之间的距离；
# 该侧没有尺寸链时保持此位置，有尺寸链时总线自动外移（见 _TOTAL_GAP_MM）
_DIM_MARGIN = 1.0

# 跨度保险倍数：构件在视图平面上的尺寸超过整个视图框的这个倍数时，判为横穿的长构件并剔除
_SPAN_RATIO = 2.0
_TRACE_EXT_FT = 0.3        # 引出线伸出尺寸线之外的长度（英尺）

# 严格轴向判据（法线分量阈值，0.995 约等于偏角 5.7°）；越接近 1 越严格，只取严格平行于轴的面
_AXIAL_TOL = 0.995

# 排管尺寸用的更严格判据（0.9999 约 0.8°）：有坡度的排管，其面不能直接用来标尺寸（Revit 会得到无效值）
_DIM_AXIAL_TOL = 0.9999

# 同位置判重精度（英尺，0.01 约 3 mm）：同一视图内两道尺寸的端点在此精度内相同，视为同一位置，第二道跳过
_SPOT_TOL_FT = 0.01

# 毫米 → 英尺换算（Revit 内部单位为英尺，1 英尺 = 304.8 mm）
_MM_PER_FT = 304.8


def mm_to_ft(mm):
    return mm / _MM_PER_FT


# 第一道尺寸链线离井外包边的距离（mm，改这里）：第一道链的内侧线（链段线）位于此处，外侧总线在其外一层
_CHAIN_OFFSET_MM = 300.0
_CHAIN_OFFSET_FT = mm_to_ft(_CHAIN_OFFSET_MM)

# 同一侧相邻两道尺寸线之间的间距（mm，改这里）：每道尺寸链占两层（链线 + 外侧总线），层间距均为此值
_CHAIN_STEP_MM = 500.0
_CHAIN_STEP_FT = mm_to_ft(_CHAIN_STEP_MM)

# 外包总尺寸线与同侧最外一道尺寸链线之间的净距（mm，改这里）：应大于一道尺寸文字的高度，避免文字叠压；
# 只在该侧有尺寸链时生效，无尺寸链的侧仍用 _DIM_MARGIN
_TOTAL_GAP_MM = 700.0
_TOTAL_GAP_FT = mm_to_ft(_TOTAL_GAP_MM)

# 井内壁搜索分区（占外包框对应方向长度的比例）：左/右内壁只在外包框左/右各 1/3 内找，上/下内壁只在上/下各 1/3 内找
_WALL_ZONE_FRAC = 1.0 / 3.0

# 井内壁候选面最小面积（平方英尺，10 约 0.93 m2）：过滤碎面、倒角与小构件的面（待实测调整）
_WALL_AREA_MIN_FT2 = 10.0

# 排管边到内壁的净距小于此值（英尺，0.005 约 1.5 mm）时不标：零长尺寸无法生成
_GAP_MIN_FT = 0.005

# 排管是否位于井内壁之内的容差（英尺，0.01 约 3 mm）
_IN_TOL_FT = 0.01

# 排管伸出井内壁的容许量（英尺，0.6 约 180 mm）：排管截面稍伸出井墙、或端头贴着井墙，也算接入这口井，照常标链
_PROTRUDE_FT = 0.6

# 排管伸到井壁的容差（英尺，0.25 约 75 mm）：排管端面离井内壁差几毫米到几厘米也算接入这口井
_REACH_TOL_FT = 0.25

# 尺寸类型名：只用于排管尺寸链（外包总尺寸保持原默认类型不变）
_DIM_TYPE_NAME = '标注尺寸 - 4mm(1比100)'
_DIM_TYPE_ID = [None]  # 惰性缓存

# ============================================================
# 配置
# ============================================================

# 不计入包围盒的类别：注释、标记、底图、参照
_EXCLUDE_CATS = [
    DB.BuiltInCategory.OST_Dimensions,
    DB.BuiltInCategory.OST_TextNotes,
    DB.BuiltInCategory.OST_Levels,
    DB.BuiltInCategory.OST_Grids,
    DB.BuiltInCategory.OST_Sections,
    DB.BuiltInCategory.OST_Viewers,
    DB.BuiltInCategory.OST_Cameras,
    DB.BuiltInCategory.OST_RoomTags,
    DB.BuiltInCategory.OST_SpotElevations,
    DB.BuiltInCategory.OST_SpotCoordinates,
    DB.BuiltInCategory.OST_SpotSlopes,
    DB.BuiltInCategory.OST_CLines,
    DB.BuiltInCategory.OST_Constraints,
    DB.BuiltInCategory.OST_Matchline,
    DB.BuiltInCategory.OST_RasterImages,
    DB.BuiltInCategory.OST_ScheduleGraphics,
    DB.BuiltInCategory.OST_ColorFillSchema,
    DB.BuiltInCategory.OST_AreaTags,
    DB.BuiltInCategory.OST_MassTags,
    DB.BuiltInCategory.OST_MaterialTags,
    DB.BuiltInCategory.OST_GenericAnnotation,
    DB.BuiltInCategory.OST_TitleBlocks,
]

# 管道类：按口径「不取管道」，一律不计入包围盒。
# 注意 OST_StructuralFraming —— 本项目用结构框架族承载 HDPE 排管，
# 那根 64 英尺长的排管正是这个类别，必须排除；真实结构梁/柱在井大样里
# 通常也远超视图框，会被下面的跨度保险再筛一次。
_PIPE_CATS = [
    DB.BuiltInCategory.OST_PipeCurves,
    DB.BuiltInCategory.OST_PipeFitting,
    DB.BuiltInCategory.OST_PipeAccessory,
    DB.BuiltInCategory.OST_PipeInsulations,
    DB.BuiltInCategory.OST_FlexPipeCurves,
    DB.BuiltInCategory.OST_DuctCurves,
    DB.BuiltInCategory.OST_DuctFitting,
    DB.BuiltInCategory.OST_DuctAccessory,
    DB.BuiltInCategory.OST_FlexDuctCurves,
    DB.BuiltInCategory.OST_CableTray,
    DB.BuiltInCategory.OST_CableTrayFitting,
    DB.BuiltInCategory.OST_Conduit,
    DB.BuiltInCategory.OST_ConduitFitting,
    DB.BuiltInCategory.OST_StructuralFraming,
    DB.BuiltInCategory.OST_StructuralFramingSystem,
    DB.BuiltInCategory.OST_StructuralColumns,
    DB.BuiltInCategory.OST_StructuralFoundation,
]


def _ints(cats):
    out = []
    for c in cats:
        try:
            out.append(int(c))
        except:
            pass
    return out


# 剖面框：是各视图的剖切范围，不是井的构件；计入会把外包框拉到模型很远的地方
_SECTION_BOX_CAT = getattr(DB.BuiltInCategory, 'OST_SectionBox', None)
_EXCLUDE_IDS = _ints(_EXCLUDE_CATS + [_SECTION_BOX_CAT])
_PIPE_IDS = _ints(_PIPE_CATS)

# 排管类别：本项目用结构框架族承载排管（只用来找排管，不计入井包围盒）
_BANK_CAT = DB.BuiltInCategory.OST_StructuralFraming
_BANK_IDS = _ints([_BANK_CAT])


# 统计用：跳过原因与同位置重复计数键（报告按 _REPORT_SKIPS 顺序列出，元组为 (键, 单位)）
_ST_BANK_OK = '标注成功的排管'
_ST_DUP = '同位置已有或重复的尺寸（已跳过）'
_SKIP_WALL_FB = '没有识别到井内壁（用了外壁）'
_SKIP_BANK_OUT = '排管不在井内（未标注）'
_SKIP_BANK_DIR = '排管方向不水平/不垂直（未标注）'
_SKIP_BANK_THROUGH = '排管穿过井（两侧都伸出井外，未标注）'
_SKIP_BANK_PASS = '排管不止于本井（从旁边或穿井而过、别的系统或电缆沟，未标注）'
_SKIP_BANK_ERR = '排管处理异常（未标注）'
_SKIP_HIDDEN = '不可见'
_SKIP_NOBB = '没有包围盒'
_SKIP_ZERO = '尺寸为零'
_SKIP_SPAN = '超出视图跨度（长构件）'
_SKIP_CAT = '注释/管道/结构框架等（按口径不计入）'
_SKIP_ERR = '读取异常'
_REPORT_SKIPS = [
    (_SKIP_WALL_FB, '面'),
    (_SKIP_BANK_OUT, '根'),
    (_SKIP_BANK_PASS, '根'),
    (_SKIP_BANK_DIR, '根'),
    (_SKIP_BANK_THROUGH, '根'),
    (_SKIP_BANK_ERR, '根'),
    (_SKIP_HIDDEN, '个'),
    (_SKIP_NOBB, '个'),
    (_SKIP_ZERO, '个'),
    (_SKIP_SPAN, '个'),
    (_SKIP_CAT, '个'),
    (_SKIP_ERR, '个'),
]


def _bump(stats, key, n=1):
    """计数（stats 为 None 时不统计）。"""
    if stats is not None and n:
        stats[key] = stats.get(key, 0) + n


def _fmt_skips(stats):
    """把非零的跳过计数整理成报告行。"""
    txt = ''
    for key, unit in _REPORT_SKIPS:
        n = stats.get(key, 0)
        if n:
            txt += '    - {}：{} {}\n'.format(key, n, unit)
    return txt


# ============================================================
# 面提取（只取严格轴向面，天然互相平行，可直接用于标注）
# ============================================================


def _face_area(f):
    try:
        if isinstance(f, DB.PlanarFace):
            return f.Area
    except:
        pass
    return None


_FACE_CACHE = {}
_EDGE_CACHE = {}


def _extract_faces(elem, instance=False):
    """提取构件所有面引用，带世界法线、原点与面积（非平面面积为 None）。非平面取 UV 中心代表值。
    同一次运行内按构件缓存（几何读取是最慢的一步，一口井会被读很多遍）。"""
    ckey = (str(elem.Id), bool(instance))
    if ckey in _FACE_CACHE:
        return _FACE_CACHE[ckey]
    opt = DB.Options()
    opt.ComputeReferences = True
    opt.IncludeNonVisibleObjects = False
    opt.DetailLevel = DB.ViewDetailLevel.Fine   # 视图是精细级别；默认的中等级别下有些族（如 LV 拉线井）只出碎片面，认不出内壁
    geom = elem.get_Geometry(opt)
    faces = []
    if not geom:
        return faces

    def _rep(f):
        try:
            if isinstance(f, DB.PlanarFace):
                return (f.FaceNormal, f.Origin)
        except:
            return None
        try:
            uvbox = f.GetBoundingBox()
            u = (uvbox.Min.U + uvbox.Max.U) / 2.0
            v = (uvbox.Min.V + uvbox.Max.V) / 2.0
            uv = DB.UV(u, v)
            return (f.ComputeNormal(uv), f.Evaluate(uv))
        except:
            return None

    def _walk(ge, tr):
        for obj in ge:
            if isinstance(obj, DB.GeometryInstance):
                if instance:
                    _walk(obj.GetInstanceGeometry(), tr)
                else:
                    _walk(obj.GetSymbolGeometry(), tr.Multiply(obj.Transform))
            elif hasattr(obj, 'Faces') and obj.Faces.Size > 0:
                for f in obj.Faces:
                    if f.Reference is None:
                        continue
                    rep = _rep(f)
                    if rep is None:
                        continue
                    faces.append({
                        'ref': f.Reference,
                        'norm': tr.OfVector(rep[0]),
                        'orig': tr.OfPoint(rep[1]),
                        'area': _face_area(f),
                    })

    _walk(geom, DB.Transform.Identity)
    _FACE_CACHE[ckey] = faces
    return faces


def _axial_faces(elem, axis):
    """构件的严格轴向面，返回 [(坐标值, face_dict), ...]，按轴排序去重。"""
    out = []
    seen = []
    for f in _extract_faces(elem):
        n = f['norm']
        v = abs(n.X) if axis == 'X' else (abs(n.Y) if axis == 'Y' else abs(n.Z))
        if v < _AXIAL_TOL:
            continue
        if axis == 'X':
            c = f['orig'].X
        elif axis == 'Y':
            c = f['orig'].Y
        else:
            c = f['orig'].Z
        dup = False
        for s in seen:
            if abs(s - c) < 0.005:
                dup = True
                break
        if dup:
            continue
        seen.append(c)
        out.append((c, f))
    out.sort(key=lambda t: t[0])
    return out


def _axial_all(elem, tol=None, instance=False):
    """构件三个方向的严格轴向面，返回 {'X':[(坐标,面),...],...}，按轴排序去重（排管尺寸链用）。"""
    res = {'X': [], 'Y': [], 'Z': []}
    seen = {'X': [], 'Y': [], 'Z': []}
    for f in _extract_faces(elem, instance):
        n = f['norm']
        try:
            for ax, v, c in (('X', abs(n.X), f['orig'].X),
                             ('Y', abs(n.Y), f['orig'].Y),
                             ('Z', abs(n.Z), f['orig'].Z)):
                if v < (tol or _AXIAL_TOL):
                    continue
                idx = -1
                for i, s in enumerate(seen[ax]):
                    if abs(s - c) < 0.005:
                        idx = i
                        break
                if idx >= 0:
                    # 同一坐标有多个面时，留面积最大的（主表面；小面多是内腔碎面，提交后引用会失效）
                    old_a = res[ax][idx][1].get('area') or 0.0
                    if (f.get('area') or 0.0) > old_a:
                        res[ax][idx] = (c, f)
                    continue
                seen[ax].append(c)
                res[ax].append((c, f))
        except:
            continue
    for ax in res:
        res[ax].sort(key=lambda t: t[0])
    return res


# ============================================================
# 视图内构件与包围盒
# ============================================================


def crop_world_range(view):
    """视图裁剪框的世界坐标范围 (x0,x1, y0,y1, z0,z1)。

    必须换算：CropBox.Min/Max 是视图局部坐标，而 get_BoundingBox(view)
    实测返回的是模型坐标，两者不可直接比较。
    做法是把裁剪框 8 个角点经 CropBox.Transform 转到世界后取包络。
    """
    try:
        cb = view.CropBox
    except:
        return None
    if cb is None:
        return None
    try:
        xs = []
        ys = []
        zs = []
        for a in (cb.Min.X, cb.Max.X):
            for b in (cb.Min.Y, cb.Max.Y):
                for c in (cb.Min.Z, cb.Max.Z):
                    p = cb.Transform.OfPoint(DB.XYZ(a, b, c))
                    xs.append(p.X)
                    ys.append(p.Y)
                    zs.append(p.Z)
        return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
    except:
        return None


def collect_target_elems(view, stats=None, relax_span=False):
    """收集视图内应计入包围盒的可见模型构件（排除注释、CAD 底图、管道类、剖面框）。
    只取与裁剪框相交的构件（框外的构件不参与定位，否则会把外包框拉到很远）；
    跨度保险按视图的水平轴、竖向轴判断（不能直接用世界 X/Z，否则东/西立面会误判）。
    stats 若传入 dict，则按原因累计被跳过的构件数（见 _REPORT_SKIPS）。"""
    out = []
    try:
        col = DB.FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType().ToElements()
    except:
        return out

    rng = crop_world_range(view)
    fr = _frame(view)
    if rng:
        hc0, hc1 = _rng_of(rng, fr['h'])
        vc0, vc1 = _rng_of(rng, fr['v'])
        crop_h = hc1 - hc0
        crop_v = vc1 - vc0
    else:
        crop_h = crop_v = 0.0

    for e in col:
        try:
            cat = e.Category
            if cat is None:
                _bump(stats, _SKIP_CAT)
                continue
            cid = cat.Id.Value
            if cid in _EXCLUDE_IDS or cid in _PIPE_IDS:
                _bump(stats, _SKIP_CAT)
                continue
            try:
                if e.IsHidden(view):
                    _bump(stats, _SKIP_HIDDEN)
                    continue
            except:
                pass
            bb = e.get_BoundingBox(view)
            if bb is None:
                _bump(stats, _SKIP_NOBB)
                continue
            if rng and not _overlap(bb, rng):
                continue
            w = _bb_span(bb, fr['h'])
            hh = _bb_span(bb, fr['v'])
            if w <= 0 and hh <= 0:
                _bump(stats, _SKIP_ZERO)
                continue
            if crop_h > 0 and crop_v > 0 and not relax_span:
                if w > crop_h * _SPAN_RATIO or hh > crop_v * _SPAN_RATIO:
                    _bump(stats, _SKIP_SPAN)
                    continue
            out.append(e)
        except:
            _bump(stats, _SKIP_ERR)
            continue
    return out


def union_box(view, elems):
    """构件在模型坐标下的联合包围盒，返回 (x0,x1, y0,y1, z0,z1)。"""
    x0 = x1 = y0 = y1 = z0 = z1 = None
    for e in elems:
        try:
            bb = e.get_BoundingBox(view)
        except:
            continue
        if bb is None:
            continue
        if x0 is None:
            x0, x1 = bb.Min.X, bb.Max.X
            y0, y1 = bb.Min.Y, bb.Max.Y
            z0, z1 = bb.Min.Z, bb.Max.Z
        else:
            x0 = min(x0, bb.Min.X)
            x1 = max(x1, bb.Max.X)
            y0 = min(y0, bb.Min.Y)
            y1 = max(y1, bb.Max.Y)
            z0 = min(z0, bb.Min.Z)
            z1 = max(z1, bb.Max.Z)
    if x0 is None:
        return None
    return (x0, x1, y0, y1, z0, z1)


def _main_well(view, elems, mark=None):
    """视图里可能同时出现几口井（邻井也在裁剪框内）：只认最大的那口作为本井，其余忽略，
    否则外包框会把邻井也算进去、排管链会标到邻井上。"""
    if mark:
        # 图纸的井号（视图名 AG-E-022 → E-022）与井的「标记」一致的构件才是本井。
        # 找不到就返回空（不标），绝不退回到"最大的那口"——那会把邻井当成本井标注
        return [e for e in elems if _mark_of(e) == mark]
    if len(elems) <= 1:
        return elems
    fr = _frame(view)
    best = None
    best_a = -1.0
    for e in elems:
        try:
            bb = e.get_BoundingBox(view)
            a = _bb_span(bb, fr['h']) * _bb_span(bb, fr['v']) * _bb_span(bb, fr['d'])
        except:
            continue
        if a > best_a:
            best, best_a = e, a
    if best is None:
        return elems
    bb0 = best.get_BoundingBox(view)
    out = []
    for e in elems:
        try:
            bb = e.get_BoundingBox(view)
            cx = (bb.Min.X + bb.Max.X) / 2.0
            cy = (bb.Min.Y + bb.Max.Y) / 2.0
            cz = (bb.Min.Z + bb.Max.Z) / 2.0
            if (bb0.Min.X <= cx <= bb0.Max.X and bb0.Min.Y <= cy <= bb0.Max.Y and bb0.Min.Z <= cz <= bb0.Max.Z):
                out.append(e)
        except:
            continue
    return out or [best]


def _face_box(elems, fallback):
    """用本井大面（面积够大的墙面/底面）的位置求外包框，比包围盒准：有的井族包围盒比井体大一倍
    （带着盖板、爬梯等），按包围盒分区会把内壁认错。某个轴找不到足够的面时，该轴沿用 fallback。"""
    xs = {'X': [], 'Y': [], 'Z': []}
    for e in elems:
        try:
            for f in _extract_faces(e):
                a = f.get('area')
                if a is None or a < _WALL_AREA_MIN_FT2:
                    continue
                n = f['norm']
                o = f['orig']
                for ax in ('X', 'Y', 'Z'):
                    if abs(_coord(n, ax)) >= _AXIAL_TOL:
                        xs[ax].append(_coord(o, ax))
        except:
            continue
    out = []
    for i, ax in enumerate(('X', 'Y', 'Z')):
        if len(xs[ax]) >= 2 and max(xs[ax]) - min(xs[ax]) > 0.5:
            out += [min(xs[ax]), max(xs[ax])]
        elif fallback is not None:
            out += [fallback[2 * i], fallback[2 * i + 1]]
        else:
            return fallback
    return tuple(out)


def _overlap(bb, rng):
    try:
        tol = 0.05
        return (bb.Max.X > rng[0] - tol and bb.Min.X < rng[1] + tol and
                bb.Max.Y > rng[2] - tol and bb.Min.Y < rng[3] + tol and
                bb.Max.Z > rng[4] - tol and bb.Min.Z < rng[5] + tol)
    except:
        return False


def _frame(view):
    """视图框架：h 为图纸水平方向的世界轴，v 为图纸竖向的世界轴，d 为视图法线方向的世界轴。
    平面视图：v 为另一条水平轴，d 为 Z；剖面/立面：v 为 Z，d 为另一条水平轴。"""
    left = _view_left(view)
    h = 'X' if abs(left.X) > 0.5 else 'Y'
    try:
        plan = (view.ViewType == DB.ViewType.FloorPlan)
    except:
        plan = False
    other = 'Y' if h == 'X' else 'X'
    if plan:
        return {'h': h, 'v': other, 'd': 'Z', 'plan': True}
    return {'h': h, 'v': 'Z', 'd': other, 'plan': False}


def _find_banks(rng, fr, stats=None):
    """视图裁剪框内与之相交的排管（结构框架）。是否标注、标哪条链由 make_chain_dims 按截面判断。"""
    out = []
    try:
        col = DB.FilteredElementCollector(doc).OfCategory(_BANK_CAT).WhereElementIsNotElementType().ToElements()
    except:
        return out
    for e in col:
        try:
            bb = e.get_BoundingBox(None)
            if bb is None:
                continue
            if not _overlap(bb, rng):
                continue
            if _bb_span(bb, 'X') <= 0 and _bb_span(bb, 'Y') <= 0:
                continue
            out.append(e)
        except:
            continue
    return out


def _view_left(view):
    try:
        r = view.UpDirection.CrossProduct(view.ViewDirection)
        return DB.XYZ(-r.X, -r.Y, -r.Z)
    except:
        return DB.XYZ(-1, 0, 0)


# ============================================================
# 生成外包总尺寸
# ============================================================


class _SpotLedger(object):
    """记录同一视图内已有的尺寸线（世界坐标的两端点）：新尺寸与之同位置时跳过并计数，避免重复标注。
    既含视图里原有的尺寸，也含本次已生成的尺寸。"""

    def __init__(self):
        self.segs = []
        self.sigs = set()      # 已有尺寸的「标了哪些构件、沿哪个轴」签名：同一根排管的同方向尺寸不再重复标
        self.dup = 0


def _same_line(a, b, tol=0.05):
    """两条尺寸线（各为 (p, q) 两个世界点）是否同一位置：平行、垂距不超过 tol（英尺，约 15 mm），
    且沿线方向的范围重叠一半以上。尺寸线被 Revit 挪动端点时仍能认出同一道尺寸。"""
    a1, a2 = a
    b1, b2 = b
    ax, ay, az = a2.X - a1.X, a2.Y - a1.Y, a2.Z - a1.Z
    la = (ax * ax + ay * ay + az * az) ** 0.5
    bx, by, bz = b2.X - b1.X, b2.Y - b1.Y, b2.Z - b1.Z
    lb = (bx * bx + by * by + bz * bz) ** 0.5
    if la < 1e-9 or lb < 1e-9:
        return False
    ux, uy, uz = ax / la, ay / la, az / la
    if abs(ux * bx + uy * by + uz * bz) / lb < 0.999:
        return False
    wx, wy, wz = b1.X - a1.X, b1.Y - a1.Y, b1.Z - a1.Z
    t1 = wx * ux + wy * uy + wz * uz
    if ((wx - t1 * ux) ** 2 + (wy - t1 * uy) ** 2 + (wz - t1 * uz) ** 2) ** 0.5 > tol:
        return False
    t2 = (b2.X - a1.X) * ux + (b2.Y - a1.Y) * uy + (b2.Z - a1.Z) * uz
    lo = max(0.0, min(t1, t2))
    hi = min(la, max(t1, t2))
    return hi - lo >= 0.5 * min(la, lb)


def _has_same_line(led, p1, p2):
    for s in led.segs:
        if _same_line(s, (p1, p2)):
            return True
    return False


def _existing_dim_segs(view):
    """视图里已有尺寸的尺寸线（只读，不改动）：新尺寸若与之同位置就跳过，重复运行不会叠标。"""
    segs = []
    try:
        dims = list(DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Dimension).ToElements())
    except:
        return segs
    for dm in dims:
        try:
            c = dm.Curve
            o = c.Origin
            try:
                length = sum(sg.Value for sg in dm.Segments) if dm.NumberOfSegments else dm.Value
            except:
                length = dm.Value
            p1 = DB.XYZ(o.X + c.Direction.X * length, o.Y + c.Direction.Y * length, o.Z + c.Direction.Z * length)
            segs.append((o, p1))
        except:
            continue
    return segs


def _axis_of(vx, vy, vz):
    """向量最接近的世界轴 'X'/'Y'/'Z'。"""
    a = (abs(vx), abs(vy), abs(vz))
    return 'XYZ'[a.index(max(a))]


def _dim_sig(refs, vec):
    """一条尺寸的签名：(参照所属构件 id 集合, 尺寸方向轴)。"""
    try:
        return (frozenset(int(r.ElementId.Value) for r in refs), _axis_of(vec[0], vec[1], vec[2]))
    except:
        return None


def _existing_dim_sigs(view):
    out = set()
    try:
        dims = list(DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Dimension).ToElements())
    except:
        return out
    for dm in dims:
        try:
            d = dm.Curve.Direction
            sg = _dim_sig(list(dm.References), (d.X, d.Y, d.Z))
            if sg is not None and len(sg[0]) >= 2:
                out.add(sg)
        except:
            continue
    return out


def _fresh_ledger(view):
    """新的同位置判重表，已含视图里原有的尺寸（原有尺寸一律不删、不改）。"""
    led = _SpotLedger()
    led.segs.extend(_existing_dim_segs(view))
    led.sigs = _existing_dim_sigs(view)
    return led


def _spot_key(p1, p2):
    """两端点按 _SPOT_TOL_FT 取整后的键，与端点先后顺序无关。"""
    def _q(p):
        return (int(round(p.X / _SPOT_TOL_FT)), int(round(p.Y / _SPOT_TOL_FT)), int(round(p.Z / _SPOT_TOL_FT)))
    a = _q(p1)
    b = _q(p2)
    return (a, b) if a <= b else (b, a)


def _find_dim_type_id():
    """按名称找尺寸类型（标注尺寸 4mm 1:100，与标准视图一致）；找不到时用名称含 4mm(1比100) 的类型；都没有返回 None。结果缓存。"""
    if _DIM_TYPE_ID[0] is not None:
        return _DIM_TYPE_ID[0]
    try:
        types = list(DB.FilteredElementCollector(doc).OfClass(DB.DimensionType).ToElements())
    except:
        return None
    pick = None
    for t in types:
        try:
            nm = (t.Name or '').strip()
        except:
            continue
        if nm == _DIM_TYPE_NAME:
            pick = t
            break
        if pick is None and '4mm(1比100)' in nm:
            pick = t
    if pick is None:
        return None
    _DIM_TYPE_ID[0] = pick.Id
    return pick.Id


# 尺寸创建失败的原因（每个视图处理前清空，报告时汇总）
_DIM_ERRS = []

# 裁剪框放不下而未标的尺寸：键为尺寸端点（同一尺寸在多次尝试中只计一次），值为缺口说明（每个视图处理前清空）
_OUT_CROP = {}

# 排管处理时抛出的异常（只记最后一句，供报告说明原因；整次运行开始时清空）
_BANK_ERRS = []

# 尺寸线与裁剪框边之间留的最小净距（英尺，0.5 约 150 mm）：尺寸文字要有地方放，不能贴着框边
_CROP_TEXT_FT = 0.5


def _annot_crop_on(view):
    """视图的注释裁剪是否开启（视图属性「注释裁剪」）。只读。"""
    try:
        p = view.get_Parameter(DB.BuiltInParameter.VIEWER_ANNOTATION_CROP_ACTIVE)
        return p is not None and p.AsInteger() == 1
    except:
        return False


def _crop_gap(view, p1, p2):
    """两个端点（已投影到视图平面）的水平、竖向坐标都要落在裁剪框内（各留 _CROP_TEXT_FT）。
    都在框内返回 None；否则返回缺口说明，如「下侧缺 350 mm」。只读，不改视图。"""
    rng = crop_world_range(view)
    if rng is None:
        return None
    fr = _frame(view)
    h0, h1 = _rng_of(rng, fr['h'])
    v0, v1 = _rng_of(rng, fr['v'])
    need = {}
    for p in (p1, p2):
        ph = _coord(p, fr['h'])
        pv = _coord(p, fr['v'])
        for side, short in (('左侧', h0 + _CROP_TEXT_FT - ph), ('右侧', ph - (h1 - _CROP_TEXT_FT)),
                            ('下侧', v0 + _CROP_TEXT_FT - pv), ('上侧', pv - (v1 - _CROP_TEXT_FT))):
            if short > 0:
                need[side] = max(need.get(side, 0.0), short)
    if not need:
        return None
    return '、'.join('{}缺 {:.0f} mm'.format(s, need[s] * _MM_PER_FT) for s in sorted(need))


def _on_plane(view, p):
    """把世界点投影到视图的注释平面上（过 view.Origin、法线为 view.ViewDirection）。
    尺寸线必须在视图平面内；若放在模型中心深度，剖面/立面的尺寸会落在剖切面之外而看不见。"""
    try:
        o = view.Origin
        n = view.ViewDirection
        t = (p.X - o.X) * n.X + (p.Y - o.Y) * n.Y + (p.Z - o.Z) * n.Z
        return DB.XYZ(p.X - t * n.X, p.Y - t * n.Y, p.Z - t * n.Z)
    except:
        return p


def _dim_at_line(d, p1, p2):
    """尺寸线的起点仍在请求的线段附近（约 300 mm 内）时返回 True。
    Revit 有时会按参照面的位置把尺寸另放到别处（如长排管的另一端），这种要撤回。"""
    try:
        o = d.Curve.Origin
        ax = p2.X - p1.X
        ay = p2.Y - p1.Y
        az = p2.Z - p1.Z
        l2 = ax * ax + ay * ay + az * az
        if l2 <= 0:
            return True
        t = ((o.X - p1.X) * ax + (o.Y - p1.Y) * ay + (o.Z - p1.Z) * az) / l2
        t = max(0.0, min(1.0, t))
        q = DB.XYZ(p1.X + t * ax, p1.Y + t * ay, p1.Z + t * az)
        return o.DistanceTo(q) <= 1.0
    except:
        return True


def _dim_valid(d):
    """尺寸数值有效时返回 True：单段看 Value，多段（链）逐段看每段的值都大于 0。
    无效的尺寸不显示在视图里，只会残留在模型中。"""
    try:
        n = d.NumberOfSegments
    except:
        n = 0
    try:
        if n and n > 0:
            for seg in d.Segments:
                if seg.Value is None or seg.Value <= 1e-6:
                    return False
            return True
        return d.Value is not None and d.Value > 1e-6
    except:
        return False


def _dim_ok(d, p1, p2):
    """尺寸位置在请求的线段附近，且数值有效时返回 True。"""
    return _dim_valid(d) and _dim_at_line(d, p1, p2)


def _err_text(ex):
    """异常信息压成一行短文字，便于报告。"""
    try:
        return str(ex).strip().split('\n')[0][:60]
    except:
        return '未知错误'


def _drop_dim(view, d):
    """删除刚建的尺寸（失败或位置不对时用）。"""
    try:
        view.Document.Delete(d.Id)
    except:
        pass


def _try_dim(view, refs, world_p1, world_p2, ledger=None, dim_type=False):
    """用一组面引用建尺寸：两个引用是一条线性尺寸，三个及以上是多段尺寸（链，按坐标顺序，相邻引用之间各为一段）。
    两端先投影到视图平面。传入 ledger 时，同位置已有尺寸则跳过并计入 ledger.dup。
    超出注释裁剪框的照常标，记入 _OUT_CROP 供报告；本函数从不改动裁剪框。
    dim_type=True 时改用 _find_dim_type_id() 找到的尺寸类型。换类型后位置不对的，撤掉改用默认样式重画。失败原因记入 _DIM_ERRS。"""
    if view is None or len(refs) < 2 or any(r is None for r in refs):
        return None
    p1 = _on_plane(view, world_p1)
    p2 = _on_plane(view, world_p2)
    if ledger is not None and _has_same_line(ledger, p1, p2):
        ledger.dup += 1
        return None
    sig = _dim_sig(refs, (p2.X - p1.X, p2.Y - p1.Y, p2.Z - p1.Z)) if ledger is not None else None
    if sig is not None and len(sig[0]) >= 2 and sig in ledger.sigs:
        ledger.dup += 1          # 视图里已经有标这根排管（同方向）的尺寸，只是位置不同：不再叠标
        return None
    # 超出注释裁剪框的尺寸照常标注（不改视图、不挡），只记下缺口供报告；需要显示时往外拉或放大裁剪框
    gap = _crop_gap(view, p1, p2) if _annot_crop_on(view) else None
    if gap is not None:
        _OUT_CROP[_spot_key(p1, p2)] = gap

    def _arr():
        a = DB.ReferenceArray()
        for r in refs:
            a.Append(r)
        return a

    try:
        line = DB.Line.CreateBound(p1, p2)
        d = view.Document.Create.NewDimension(view, line, _arr())
    except Exception as ex:
        _DIM_ERRS.append(_err_text(ex))
        return None
    if d is None:
        _DIM_ERRS.append('Revit 未返回尺寸')
        return None
    if not _dim_ok(d, p1, p2):
        _drop_dim(view, d)
        _DIM_ERRS.append('Revit 尺寸位置不对或数值无效（已撤回）')
        return None
    if dim_type:
        dt = _find_dim_type_id()
        if dt is not None:
            try:
                d.ChangeTypeId(dt)
            except:
                dt = None
            if dt is not None and not _dim_ok(d, p1, p2):
                _drop_dim(view, d)
                d = None
                try:
                    d = view.Document.Create.NewDimension(view, line, _arr())
                except Exception as ex:
                    _DIM_ERRS.append(_err_text(ex))
                    return None
                if d is None or not _dim_ok(d, p1, p2):
                    if d is not None:
                        _drop_dim(view, d)
                    _DIM_ERRS.append('Revit 尺寸位置不对或数值无效（已撤回）')
                    return None
    if ledger is not None:
        ledger.segs.append((p1, p2))
        if sig is not None:
            ledger.sigs.add(sig)
    return d


_ROT_FORCE_TRACE = [False]   # 旋转井标注第一次失败后重试时置真：竖向链全部改用引出线，不再用排管的面做参照
_TRACE_CREATED = []     # 本次新画的引出线（旋转井标注结束后清掉没被任何尺寸用到的）


def _invisible_style_id():
    """「<不可见线>」线样式：画引出线用，不在图上显示，但可以当尺寸参照。找不到返回 None（用默认线样式）。"""
    try:
        for g in DB.FilteredElementCollector(doc).OfClass(DB.GraphicsStyle):
            nm = g.Name or ''
            if nm in ('<不可见线>', '<Invisible lines>') and g.GraphicsStyleType == DB.GraphicsStyleType.Projection:
                return g
    except:
        pass
    return None


def _trace_ref(view, pa, pb, style=None):
    """在视图里画一条引出线（详图线），返回 (线元素, 线参照)。排管的边在某些视图里 Revit 不认作尺寸参照时，
    改在排管边缘位置画线，再从线标到井内壁（线是视图自己的图元，参照一定有效）。"""
    dc = doc.Create.NewDetailCurve(view, DB.Line.CreateBound(_on_plane(view, pa), _on_plane(view, pb)))
    _TRACE_CREATED.append(dc.Id)
    try:
        if style is not None:
            dc.LineStyle = style
    except:
        pass
    try:
        doc.Regenerate()          # 新画的线先重生成，取到的参照才稳定有效（否则提交时可能报「参照无效」）
    except:
        pass
    return dc, dc.GeometryCurve.Reference


def _trace_chain(view, wall_lo, wall_hi, a_lo, a_hi, mk_line, p1, p2, ledger, style):
    """用两条引出线做排管链：井内壁 → 引出线 → 引出线 → 井内壁。mk_line(坐标) 返回该坐标处引出线的两端点。
    失败时把画出的线删掉。返回 0/1。"""
    made = []
    try:
        refs = [wall_lo[1]]
        last = wall_lo[0]
        for c in (a_lo, a_hi):
            if c - last < _GAP_MIN_FT or wall_hi[0] - c < _GAP_MIN_FT:
                continue
            pa, pb = mk_line(c)
            dc, r = _trace_ref(view, pa, pb, style)
            made.append(dc)
            refs.append(r)
            last = c
        refs.append(wall_hi[1])
        if len(refs) < 3:
            for dc in made:
                _drop_dim(view, dc)
            return 0
        if _try_dim(view, refs, p1, p2, ledger, dim_type=True):
            return 1
    except:
        pass
    for dc in made:
        _drop_dim(view, dc)
    return 0


def _chain_dim(view, refs, p1, p2, ledger):
    """排管链：一条多段尺寸。返回生成数 0/1。"""
    return 1 if _try_dim(view, refs, p1, p2, ledger, dim_type=True) else 0


def _coord(p, axis):
    """点或向量在世界轴上的分量。"""
    if axis == 'X':
        return p.X
    if axis == 'Y':
        return p.Y
    return p.Z


def _rng_of(r, axis):
    """(x0,x1,y0,y1,z0,z1) 形式的范围在某世界轴上的 (最小, 最大)。"""
    if axis == 'X':
        return r[0], r[1]
    if axis == 'Y':
        return r[2], r[3]
    return r[4], r[5]


def _bb_span(bb, axis):
    """包围盒在某世界轴上的长度（英尺，取正）。"""
    lo, hi = _rng_of((bb.Min.X, bb.Max.X, bb.Min.Y, bb.Max.Y, bb.Min.Z, bb.Max.Z), axis)
    return abs(hi - lo)


def _collect_edges(bank, run, m, third, axis_dir=None):
    """排管所有与 third 轴平行的短直边：[(沿走向坐标, 沿测量轴坐标, 引用, 沿中心线投影, 方向键), ...]。
    axis_dir 给出时（斜排管）：端面是斜的，边既不一定平行于 third 轴，改取「垂直于测量轴 m、且落在垂直于中心线的端面内」的直边，
    这类边仍是线性尺寸的有效参照（垂直于尺寸方向）。"""
    opt = DB.Options()
    opt.ComputeReferences = True
    opt.DetailLevel = DB.ViewDetailLevel.Fine
    cand = []
    mvec = _unit(m, 1)

    def _walk(ge, tr):
        for obj in ge:
            if isinstance(obj, DB.GeometryInstance):
                _walk(obj.GetSymbolGeometry(), tr.Multiply(obj.Transform))
            elif isinstance(obj, DB.Solid):
                for e in obj.Edges:
                    try:
                        if e.Reference is None:
                            continue
                        c = e.AsCurve()
                        if not isinstance(c, DB.Line):
                            continue
                        p0 = tr.OfPoint(c.GetEndPoint(0))
                        p1 = tr.OfPoint(c.GetEndPoint(1))
                        ln = p0.DistanceTo(p1)
                        if ln <= 0.01:
                            continue
                        dv = (p1 - p0).Normalize()
                        if axis_dir is None:
                            if abs(_coord(p1, third) - _coord(p0, third)) / ln < 0.99:
                                continue
                            dkey = None
                        else:
                            if abs(dv.DotProduct(mvec)) > 0.02 or abs(dv.DotProduct(axis_dir)) > 0.2:
                                continue
                            sg = 1.0 if (dv.X > 1e-6 or (abs(dv.X) <= 1e-6 and (dv.Y > 1e-6 or (abs(dv.Y) <= 1e-6 and dv.Z > 0)))) else -1.0
                            dkey = (round(dv.X * sg, 1), round(dv.Y * sg, 1), round(dv.Z * sg, 1))
                        rc = (_coord(p0, run) + _coord(p1, run)) / 2.0
                        mv = (_coord(p0, m) + _coord(p1, m)) / 2.0
                        if axis_dir is not None:
                            mid = DB.XYZ((p0.X + p1.X) / 2.0, (p0.Y + p1.Y) / 2.0, (p0.Z + p1.Z) / 2.0)
                            sv = mid.DotProduct(axis_dir)
                        else:
                            sv = rc
                        cand.append((rc, mv, e.Reference, sv, dkey))
                    except:
                        continue

    try:
        _walk(bank.get_Geometry(opt), DB.Transform.Identity)
    except:
        return []
    return cand


def _bank_axis(bank):
    """排管中心线（直线）：返回 (单位方向, 端点0, 端点1)；取不到或不是直线返回 None。"""
    try:
        c = bank.Location.Curve
        if not isinstance(c, DB.Line):
            return None
        p0, p1 = c.GetEndPoint(0), c.GetEndPoint(1)
        d = p1 - p0
        if d.GetLength() < 1e-6:
            return None
        return d.Normalize(), p0, p1
    except:
        return None


def _is_oblique(axis):
    """排管走向既不平行于世界轴、也不是水平/垂直（带坡度或斜着走）。"""
    if axis is None:
        return False
    u = axis[0]
    return max(abs(u.X), abs(u.Y), abs(u.Z)) < 0.999


def _bank_ends_at_well(bank, box, tol_ft=1.0):
    """排管是否「止于这口井」：中心线恰有一个端点落在井的外包框（外扩 tol_ft）里。
    两个端点都在框外（排管从井旁边或穿井而过、是别的井的管、或是电缆沟等）不标；取不到中心线的照常标。"""
    axis = _bank_axis(bank)
    if axis is None or box is None:
        return True
    ins = 0
    for p in (axis[1], axis[2]):
        if (box[0] - tol_ft <= p.X <= box[1] + tol_ft and box[2] - tol_ft <= p.Y <= box[3] + tol_ft
                and box[4] - tol_ft <= p.Z <= box[5] + tol_ft):
            ins += 1
    return ins >= 1


def _end_edges(bank, run, m, near):
    """排管靠近井的那一端的端面短边（与排管走向垂直、且垂直于测量轴 m 的直边）。
    标准图用的就是这类线性参照：有坡度的排管也稳定，重新生成后不会失效。
    返回 (小侧值, 小侧引用, 大侧值, 大侧引用)，找不到返回 None。run 为排管走向轴，near 为靠近的坐标。
    排管带坡度或斜着走时，端面是斜的（上下边的走向坐标不同），改用沿排管中心线方向的投影判断哪些边在同一端面上。"""
    third = [a for a in ('X', 'Y', 'Z') if a not in (run, m)][0]
    axis = _bank_axis(bank)
    obl = _is_oblique(axis)
    ekey = (str(bank.Id), run, m, obl)
    if ekey in _EDGE_CACHE:
        cand = _EDGE_CACHE[ekey]
    else:
        cand = _collect_edges(bank, run, m, third, axis[0] if obl else None)
        _EDGE_CACHE[ekey] = cand
    if not cand or len(cand) < 2:
        return None
    if obl:
        # 靠近井的那一端：中心线两个端点里，走向坐标离 near 更近的一个
        u, p0, p1 = axis
        end = p0 if abs(_coord(p0, run) - near) <= abs(_coord(p1, run) - near) else p1
        s_end = end.DotProduct(u)
        near_c = [(c[1], c[2], c[4]) for c in cand if abs(c[3] - s_end) <= 0.15]
        # 端面上可能有几组不同方向的边：按方向分组，取在测量轴上跨度最大的一组（上下两条边必须互相平行）
        groups = {}
        for c in near_c:
            groups.setdefault(c[2], []).append(c)
        best = None
        for g in groups.values():
            lo_ = min(g, key=lambda c: c[0])
            hi_ = max(g, key=lambda c: c[0])
            if best is None or hi_[0] - lo_[0] > best[1][0] - best[0][0]:
                best = (lo_, hi_)
        if best is None:
            return None
        lo, hi = best
    else:
        dist = [(abs(c[0] - near), c[1], c[2]) for c in cand]
        nearest = min(c[0] for c in dist)
        near_c = [(c[1], c[2]) for c in dist if c[0] <= nearest + 0.15]
        lo = min(near_c, key=lambda c: c[0])
        hi = max(near_c, key=lambda c: c[0])
    if hi[0] - lo[0] < 0.01:
        return None
    return lo[0], lo[1], hi[0], hi[1]


def _bb_mid(bb, axis):
    """包围盒在某世界轴上的中点（排管侧别按它判断，比面的范围稳定）。"""
    lo, hi = _rng_of((bb.Min.X, bb.Max.X, bb.Min.Y, bb.Max.Y, bb.Min.Z, bb.Max.Z), axis)
    return (lo + hi) / 2.0


def _mk(fr, hv, vv, dv):
    """按视图框架组装世界点：hv 为水平坐标，vv 为竖向坐标，dv 为视图法线方向坐标。"""
    c = {fr['h']: hv, fr['v']: vv, fr['d']: dv}
    return DB.XYZ(c['X'], c['Y'], c['Z'])


def _unit(axis, sign):
    """世界轴方向的单位向量，sign 为 +1 或 -1。"""
    if axis == 'X':
        return DB.XYZ(sign, 0, 0)
    if axis == 'Y':
        return DB.XYZ(0, sign, 0)
    return DB.XYZ(0, 0, sign)


def _clip_box(box, rng):
    """把外包框裁到视图裁剪框之内（只取框内部分，框外的构件不把总尺寸拉到很远）；
    与裁剪框不相交时返回 None。"""
    if box is None or rng is None:
        return box
    out = (max(box[0], rng[0]), min(box[1], rng[1]),
           max(box[2], rng[2]), min(box[3], rng[3]),
           max(box[4], rng[4]), min(box[5], rng[5]))
    if out[0] > out[1] or out[2] > out[3] or out[4] > out[5]:
        return None
    return out


def _total_margin(extents, key):
    """外包总尺寸线离外包框的距离（英尺）。key 为总线所在的世界方向，如 'Z-'。
    该方向有尺寸链（extents 中有记录）：外移到最外一道链线之外 _TOTAL_GAP_FT；
    没有尺寸链：保持 _DIM_MARGIN 原位。"""
    reach = extents.get(key, 0.0) if extents else 0.0
    if reach <= 0:
        return _DIM_MARGIN
    return max(_DIM_MARGIN, reach + _TOTAL_GAP_FT)


# 每张视图标哪条总线（与标准图一致）：W = 宽（下方），H = 高（左侧）；按视图名前缀判断，未列出的视图两条都标
_TOTAL_RULES = {'平面': 'WH', '北': 'W', '南': 'WH', '西': '', '东': ''}


def _total_rule(view):
    nm = view.Name or ''
    for pref, rule in _TOTAL_RULES.items():
        if nm.startswith(pref + '_'):
            return rule
    return 'WH'


def _extremes(elems, axis):
    """构件在某世界轴上最外侧的两个严格轴向面：返回 (最小侧, 最大侧)，每个为 (坐标, 面字典) 或 None。"""
    lo = hi = None
    for e in elems:
        fl = _axial_faces(e, axis)
        if not fl:
            continue
        c0, f0 = fl[0]
        c1, f1 = fl[-1]
        if lo is None or c0 < lo[0]:
            lo = (c0, f0)
        if hi is None or c1 > hi[0]:
            hi = (c1, f1)
    return lo, hi


def make_total_dims(view, elems, box, ledger=None, extents=None):
    """生成总尺寸，取两侧内壁之间的净尺寸（与标准图一致）：宽度在下方标，高度在左侧标。
    哪些视图标哪条由 _total_rule 决定（平面、南标宽和高；北只标宽；东、西不标）。
    找不到内壁时退回到构件最外侧的面。尺寸线摆在外包框之外，并投影到视图平面上（见 _try_dim）。
    extents 由 make_chain_dims 填好时，总线所在一侧若有尺寸链，总线外移到最外一道链线之外。
    """
    made = 0
    rule = _total_rule(view)
    if not rule:
        return 0
    fr = _frame(view)
    h, v, d = fr['h'], fr['v'], fr['d']
    h0, h1 = _rng_of(box, h)
    v0, v1 = _rng_of(box, v)
    d0, d1 = _rng_of(box, d)
    dep = (d0 + d1) / 2.0

    m_w = _total_margin(extents, v + '-')
    m_h = _total_margin(extents, h + '-')

    walls, _fb = _inner_walls(elems, box, fr)

    # ---- 宽度（下方）：两侧内壁之间 ----
    if 'W' in rule:
        if walls['L'] and walls['R']:
            a = (walls['L'][0], walls['L'][1]['ref'])
            b = (walls['R'][0], walls['R'][1]['ref'])
        else:
            lo, hi = _extremes(elems, h)
            a = (lo[0], lo[1]['ref']) if lo else None
            b = (hi[0], hi[1]['ref']) if hi else None
        if a and b:
            p1 = _mk(fr, a[0] - _DIM_MARGIN, v0 - m_w, dep)
            p2 = _mk(fr, b[0] + _DIM_MARGIN, v0 - m_w, dep)
            if _try_dim(view, [a[1], b[1]], p1, p2, ledger, dim_type=True):
                made += 1

    # ---- 高度（左侧）：上下内壁之间 ----
    if 'H' in rule:
        if walls['B'] and walls['T']:
            a = (walls['B'][0], walls['B'][1]['ref'])
            b = (walls['T'][0], walls['T'][1]['ref'])
        else:
            lo, hi = _extremes(elems, v)
            a = (lo[0], lo[1]['ref']) if lo else None
            b = (hi[0], hi[1]['ref']) if hi else None
        if a and b:
            p1 = _mk(fr, h0 - m_h, a[0] - _DIM_MARGIN, dep)
            p2 = _mk(fr, h0 - m_h, b[0] + _DIM_MARGIN, dep)
            if _try_dim(view, [a[1], b[1]], p1, p2, ledger, dim_type=True):
                made += 1

    return made


# ============================================================
# 井内壁识别与排管尺寸链（剖面/立面）
# ============================================================


def _pick(lst, want_max):
    """从 [(坐标, 面字典), ...] 中取坐标最大或最小的一项。"""
    if not lst:
        return None
    if want_max:
        return max(lst, key=lambda t: t[0])
    return min(lst, key=lambda t: t[0])


def _inner_walls(elems, box, fr, stats=None):
    """在井构件的大面中识别四侧内壁（世界坐标）。L/R 为水平轴两侧，B/T 为竖向轴两侧；
    L、B 为坐标较小的一侧，R、T 为坐标较大的一侧。
    返回 (walls, fb_sides)：walls = {'L','R','B','T': (坐标, 面字典, 是否用了外壁) 或 None}，
    fb_sides 为改用外壁的侧别列表。
    内壁判据：
      L：法线指向 +h（井内）、位于包围盒较小 h 侧 1/3 内的面中坐标最大者；
      R：法线指向 -h、位于较大 h 侧 1/3 内的面中坐标最小者；
      B：法线指向 +v、位于较小 v 侧 1/3 内的面中坐标最大者；
      T：法线指向 -v、位于较大 v 侧 1/3 内的面中坐标最小者。
    某侧找不到时改用外壁（法线朝外、位于同侧 1/3 内、最靠外者），记入 fb_sides，由报告显示。
    """
    h, v = fr['h'], fr['v']
    h0, h1 = _rng_of(box, h)
    v0, v1 = _rng_of(box, v)
    zone_h = (h1 - h0) * _WALL_ZONE_FRAC
    zone_v = (v1 - v0) * _WALL_ZONE_FRAC
    tol = _AXIAL_TOL
    inner_c = {'L': [], 'R': [], 'B': [], 'T': []}
    outer_c = {'L': [], 'R': [], 'B': [], 'T': []}
    for e in elems:
        for f in _extract_faces(e):
            a = f.get('area')
            if a is None or a < _WALL_AREA_MIN_FT2:
                continue
            try:
                n = f['norm']
                o = f['orig']
                nh = _coord(n, h)
                ph = _coord(o, h)
                nv = _coord(n, v)
                pv = _coord(o, v)
                if abs(nh) >= tol:
                    if h0 <= ph <= h0 + zone_h:
                        (inner_c['L'] if nh >= tol else outer_c['L']).append((ph, f))
                    if h1 - zone_h <= ph <= h1:
                        (inner_c['R'] if nh <= -tol else outer_c['R']).append((ph, f))
                if abs(nv) >= tol:
                    if v0 <= pv <= v0 + zone_v:
                        (inner_c['B'] if nv >= tol else outer_c['B']).append((pv, f))
                    if v1 - zone_v <= pv <= v1:
                        (inner_c['T'] if nv <= -tol else outer_c['T']).append((pv, f))
            except:
                continue

    walls = {}
    fb_sides = []
    for side, want_max in (('L', True), ('R', False), ('B', True), ('T', False)):
        hit = _pick(inner_c[side], want_max)
        fb = False
        if hit is None:
            hit = _pick(outer_c[side], not want_max)
            fb = True
            fb_sides.append(side)
        walls[side] = (hit[0], hit[1], fb) if hit is not None else None
    return walls, fb_sides


def _note_reach(extents, key, dist):
    """记下世界方向 key（如 'Z-'）上最外一道链线离外包框的距离（英尺，取最大）。extents 为 None 时不记。"""
    if extents is not None:
        extents[key] = max(extents.get(key, 0.0), dist)


def make_chain_dims(view, elems, box, ledger=None, stats=None, extents=None, only=None, only_dir=None):
    """为视图内位于井内的每根排管生成尺寸链：每侧每根排管一条多段尺寸（内壁 → 排管边 → 排管宽 → 排管边 → 内壁），
    与标准视图一致；链本身不另加总线，外包总尺寸由 make_total_dims 负责。返回 (生成尺寸数, 原因)；原因为 None 表示正常处理。
    排管按截面判断两类链（平面与剖面/立面共用）：
      水平链（沿视图水平轴）：排管截面的水平宽度在井内壁之间时（剖面/立面为端面朝向视线的排管，
        平面为沿竖向轴走的排管）；放在井外下侧或上侧，取排管伸出井外的那一侧（都不伸出则看排管中心）。
      竖向链（沿视图竖向轴）：排管截面的竖向宽度在井内壁之间时（剖面/立面为截面高度 Z，
        平面为沿水平轴走的排管的另一水平宽度）；放在井外左侧或右侧，取排管伸出井外的那一侧。
    两侧都伸出井外的排管（穿井）不标，计入「穿井」跳过原因。
    同一侧的链依次向外排，每条链占一层，层间距为 _CHAIN_STEP_FT。
    extents 若传入 dict，则记下每个世界方向上最外一道链线离外包框的距离（键如 'Z-'、'X+'），
    供 make_total_dims 把外包总尺寸线移到链线之外。
    """
    made = 0
    rng = crop_world_range(view)
    if rng is None:
        return 0, '无裁剪框'
    fr = _frame(view)
    h, v, d = fr['h'], fr['v'], fr['d']
    plan = fr['plan']

    banks = _find_banks(rng, fr, stats)
    if only is not None:
        ids = [str(b.Id) for b in only]
        banks = [b for b in banks if str(b.Id) in ids]
    if not banks:
        return 0, '视图内无排管（结构框架）'

    h0, h1 = _rng_of(box, h)
    v0, v1 = _rng_of(box, v)
    d0, d1 = _rng_of(box, d)
    d_mid = (d0 + d1) / 2.0
    walls, fb_sides = _inner_walls(elems, box, fr, stats)
    if any(x is None for x in walls.values()):
        for _ in banks:
            _bump(stats, _SKIP_BANK_ERR)
        return 0, '井内壁与外壁均无法识别'

    wL, wl = walls['L'][0], walls['L'][1]
    wR, wr = walls['R'][0], walls['R'][1]
    wB, wb = walls['B'][0], walls['B'][1]
    wT, wt = walls['T'][0], walls['T'][1]
    h_mid = (wL + wR) / 2.0
    v_mid = (wB + wT) / 2.0

    def PT(hv, vv):
        return _mk(fr, hv, vv, d_mid)

    cnt = {'L': 0, 'R': 0, 'B': 0, 'T': 0}   # 每侧已占用的层数（每条链占一层）

    def _layer(side):
        k = cnt[side]
        cnt[side] += 1
        return _CHAIN_OFFSET_FT + k * _CHAIN_STEP_FT

    def _pick_side(lo, hi, wlo, whi, mid, neg, pos, center):
        """排管在某轴上的范围 [lo,hi] 相对井内壁 [wlo,whi]：只伸出一侧取该侧；都不伸出看中心；两侧都伸出返回 None（穿井）。"""
        out_lo = lo < wlo - _IN_TOL_FT
        out_hi = hi > whi + _IN_TOL_FT
        if out_lo and out_hi:
            return None
        if out_lo:
            return neg
        if out_hi:
            return pos
        return neg if center < mid else pos

    def _chain_refs(lo, hi, edges):
        """多段尺寸的参照，按坐标从小到大：井内壁、排管边…、井内壁。
        lo、hi 为 (坐标, 面引用)；edges 为已按坐标排序的排管边 [(坐标, 面引用), ...]。
        与前一参照或井内壁的距离小于 _GAP_MIN_FT 的排管边不留（排管贴墙时不生成零长段）。"""
        refs = [lo[1]]
        last = lo[0]
        for c, ref in edges:
            if c - last < _GAP_MIN_FT or hi[0] - c < _GAP_MIN_FT:
                continue
            refs.append(ref)
            last = c
        refs.append(hi[1])
        return refs

    for bank in banks:
        try:
            bb = bank.get_BoundingBox(None)
            if bb is None:
                _bump(stats, _SKIP_BANK_DIR)
                continue
            if not _bank_ends_at_well(bank, box):
                _bump(stats, _SKIP_BANK_PASS)
                continue
            dh = _bb_span(bb, h)
            dv = _bb_span(bb, v)
            dd = _bb_span(bb, d)
            if plan:
                want_h = dh < dv      # 沿竖向轴走：水平截面宽 = dh
                want_v = dv < dh      # 沿水平轴走：竖向截面宽 = dv
            else:
                want_h = dh < dd      # 端面朝向视线：水平截面宽 = dh
                want_v = True         # 截面高度（Z）总可标
            if only_dir == 'h':
                want_v = False
            elif only_dir == 'v':
                want_h = False
            if not (want_h or want_v):
                _bump(stats, _SKIP_BANK_DIR)
                continue
            bf = _axial_all(bank, _DIM_AXIAL_TOL)
            hf = bf[h]
            vf = bf[v]
            # 排管走向（水平面内较长的轴）与靠近井的端面短边：优先用端面短边做参照，没有再用面
            run = 'X' if _bb_span(bb, 'X') >= _bb_span(bb, 'Y') else 'Y'
            he = ve = None
            if want_h and run != h:
                he = _end_edges(bank, run, h, (d0 + d1) / 2.0 if run == d else (wL if abs(_bb_mid(bb, run) - wL) < abs(_bb_mid(bb, run) - wR) else wR))
            if want_v and run != v:
                ve = _end_edges(bank, run, v, (d0 + d1) / 2.0 if run == d else (wL if abs(_bb_mid(bb, run) - wL) < abs(_bb_mid(bb, run) - wR) else wR))
            # 备用参照：排管的轴向侧面。端面边在某些视图里 Revit 不认（斜排管的边不垂直于视图），建尺寸失败时改用侧面
            h_alt = v_alt = None
            try:
                _haf = hf if len(hf) >= 2 else _axial_all(bank)[h]
                if len(_haf) >= 2:
                    h_alt = (_haf[0][0], _haf[0][1]['ref'], _haf[-1][0], _haf[-1][1]['ref'])
                _vaf = vf if len(vf) >= 2 else _axial_all(bank)[v]
                if len(_vaf) >= 2:
                    v_alt = (_vaf[0][0], _vaf[0][1]['ref'], _vaf[-1][0], _vaf[-1][1]['ref'])
            except:
                h_alt = v_alt = None
            if he:
                hb_lo, hb_lo_r, hb_hi, hb_hi_r = he
            elif len(hf) >= 2 or len(_axial_all(bank)[h]) >= 2:
                if len(hf) < 2:
                    hf = _axial_all(bank)[h]
                (hb_lo, hb_lo_f), (hb_hi, hb_hi_f) = hf[0], hf[-1]
                hb_lo_r, hb_hi_r = hb_lo_f['ref'], hb_hi_f['ref']
            else:
                hb_lo = hb_hi = hb_lo_r = hb_hi_r = None
            if ve:
                vb_lo, vb_lo_r, vb_hi, vb_hi_r = ve
            elif len(vf) >= 2 or len(_axial_all(bank)[v]) >= 2:
                if len(vf) < 2:
                    vf = _axial_all(bank)[v]
                (vb_lo, vb_lo_f), (vb_hi, vb_hi_f) = vf[0], vf[-1]
                vb_lo_r, vb_hi_r = vb_lo_f['ref'], vb_hi_f['ref']
            else:
                vb_lo = vb_hi = vb_lo_r = vb_hi_r = None
            if hb_lo is None:
                want_h = False
            if vb_lo is None:
                want_v = False
            if not (want_h or want_v):
                _bump(stats, _SKIP_BANK_DIR)
                continue
            if hb_lo is None or vb_lo is None:
                # 另一个方向缺数据时，用包围盒范围参与判断（链本身仍只在有参照的方向生成）
                if hb_lo is None:
                    hb_lo, hb_hi = _rng_of((bb.Min.X, bb.Max.X, bb.Min.Y, bb.Max.Y, bb.Min.Z, bb.Max.Z), h)
                if vb_lo is None:
                    vb_lo, vb_hi = _rng_of((bb.Min.X, bb.Max.X, bb.Min.Y, bb.Max.Y, bb.Min.Z, bb.Max.Z), v)
            # 水平链：截面水平宽在井内壁之间，且竖向范围与井相交
            # 斜着进井的排管，端面角点会伸到井壁外较远，放宽伸出量
            prot = 1.5 if _is_oblique(_bank_axis(bank)) else _PROTRUDE_FT
            ok_h = (want_h and hb_lo >= wL - prot and hb_hi <= wR + prot
                    and vb_lo < wT + _REACH_TOL_FT and vb_hi > wB - _REACH_TOL_FT)
            # 竖向链：截面竖向范围在井内壁之间，且水平范围与井相交
            ok_v = (want_v and vb_lo >= wB - prot and vb_hi <= wT + prot
                    and hb_lo < wR + _REACH_TOL_FT and hb_hi > wL - _REACH_TOL_FT)
            if not (ok_h or ok_v) or not _overlap(bb, (box[0] - 0.3, box[1] + 0.3, box[2] - 0.3, box[3] + 0.3, box[4] - 0.3, box[5] + 0.3)):
                _bump(stats, _SKIP_BANK_OUT)
                continue
            n_ch = 0
            if ok_h:
                side = _pick_side(vb_lo, vb_hi, wB, wT, v_mid, 'B', 'T', _bb_mid(bb, v))
                if side is None:
                    side = 'B' if cnt['B'] <= cnt['T'] else 'T'   # 穿井的排管：标在目前链较少的一侧
                if True:
                    off = _layer(side)
                    if side == 'B':
                        vs = wB - off
                        _note_reach(extents, v + '-', off)
                    else:
                        vs = wT + off
                        _note_reach(extents, v + '+', off)
                    refs = _chain_refs((wL, wl['ref']), (wR, wr['ref']),
                                       [(hb_lo, hb_lo_r), (hb_hi, hb_hi_r)])
                    dup0 = ledger.dup if ledger is not None else 0
                    got = 0
                    if len(refs) >= 3:
                        got = _chain_dim(view, refs, PT(wL, vs), PT(wR, vs), ledger)
                        if not got and he and h_alt and not (ledger is not None and ledger.dup > dup0):
                            refs = _chain_refs((wL, wl['ref']), (wR, wr['ref']), [(h_alt[0], h_alt[1]), (h_alt[2], h_alt[3])])
                            if len(refs) >= 3:
                                got = _chain_dim(view, refs, PT(wL, vs), PT(wR, vs), ledger)
                        if not got and not (ledger is not None and ledger.dup > dup0):
                            # 最后手段：在排管边缘画引出线，从线标到井内壁
                            _wy = wB if side == 'B' else wT
                            _ye = vs - _TRACE_EXT_FT if side == 'B' else vs + _TRACE_EXT_FT
                            got = _trace_chain(view, (wL, wl['ref']), (wR, wr['ref']), hb_lo, hb_hi,
                                               lambda c, _wy=_wy, _ye=_ye: (PT(c, _wy), PT(c, _ye)),
                                               PT(wL, vs), PT(wR, vs), ledger, _invisible_style_id())
                        n_ch += got
            if ok_v:
                side = _pick_side(hb_lo, hb_hi, wL, wR, h_mid, 'L', 'R', _bb_mid(bb, h))
                if side is None:
                    side = 'L' if cnt['L'] <= cnt['R'] else 'R'
                if True:
                    off = _layer(side)
                    if side == 'L':
                        hs = wL - off
                        _note_reach(extents, h + '-', off)
                    else:
                        hs = wR + off
                        _note_reach(extents, h + '+', off)
                    refs = _chain_refs((wB, wb['ref']), (wT, wt['ref']),
                                       [(vb_lo, vb_lo_r), (vb_hi, vb_hi_r)])
                    dup0 = ledger.dup if ledger is not None else 0
                    got = 0
                    if len(refs) >= 3:
                        got = _chain_dim(view, refs, PT(hs, wB), PT(hs, wT), ledger)
                        if not got and ve and v_alt and not (ledger is not None and ledger.dup > dup0):
                            refs = _chain_refs((wB, wb['ref']), (wT, wt['ref']), [(v_alt[0], v_alt[1]), (v_alt[2], v_alt[3])])
                            if len(refs) >= 3:
                                got = _chain_dim(view, refs, PT(hs, wB), PT(hs, wT), ledger)
                        if not got and not (ledger is not None and ledger.dup > dup0):
                            _wx = wL if side == 'L' else wR
                            _xe = hs - _TRACE_EXT_FT if side == 'L' else hs + _TRACE_EXT_FT
                            got = _trace_chain(view, (wB, wb['ref']), (wT, wt['ref']), vb_lo, vb_hi,
                                               lambda c, _wx=_wx, _xe=_xe: (PT(_wx, c), PT(_xe, c)),
                                               PT(hs, wB), PT(hs, wT), ledger, _invisible_style_id())
                        n_ch += got
            if n_ch:
                made += n_ch
                _bump(stats, _ST_BANK_OK)
        except:
            _bump(stats, _SKIP_BANK_ERR)
            # 记下原因（只留最后一句异常信息，供报告里说明）
            _BANK_ERRS.append(_err_text(traceback.format_exc().strip().split('\n')[-1]))
            continue

    if made:
        for _ in fb_sides:
            _bump(stats, _SKIP_WALL_FB)
    return made, None


# ============================================================
# 旋转井：井体转了一个不是 90° 倍数的角度（如 TELCO 井旋转 171°），墙面与视图轴不平行
# 平面：沿井自己的两个方向标「对齐尺寸」（井内壁净尺寸）；排管用接触点处画的引出线定位
# 立面：竖向用水平面（不受旋转影响），水平方向用井内腔的投影位置画引出线定位
# ============================================================


def _rot_info(elems):
    """识别旋转井的局部坐标：返回 dict，找不到返回 None。
    a、b 为井自己的两个水平方向（单位向量，b = a 逆时针转 90°）；u = p·a、w = p·b 是局部坐标。
    uL/uR（沿 a 的两侧内壁）、wB/wT（沿 b 的两侧内壁）、zB/zT（底、顶内表面）各带面引用；
    uo0/uo1、wo0/wo1 为外壁坐标，zo0/zo1 为外底/外顶。"""
    import math
    faces = []
    for e in elems:
        try:
            for f in _extract_faces(e):
                a_ = f.get('area')
                if a_ is None or a_ < _WALL_AREA_MIN_FT2:
                    continue
                faces.append(f)
        except:
            continue
    hs = [f for f in faces if abs(f['norm'].Z) < 0.05]
    if len(hs) < 4:
        return None
    sx = sy = 0.0
    for f in hs:
        th = math.atan2(f['norm'].Y, f['norm'].X)
        sx += f['area'] * math.cos(4 * th)
        sy += f['area'] * math.sin(4 * th)
    th = math.atan2(sy, sx) / 4.0
    a = DB.XYZ(math.cos(th), math.sin(th), 0.0)
    b = DB.XYZ(-math.sin(th), math.cos(th), 0.0)
    fam_a = []     # (u, 朝向符号, 面)
    fam_b = []
    zs = []
    for f in faces:
        n = f['norm']
        o = f['orig']
        if abs(n.Z) >= 0.95:
            zs.append((o.Z, 1.0 if n.Z > 0 else -1.0, f))
        elif abs(n.Z) < 0.05:
            ua = n.DotProduct(a)
            ub = n.DotProduct(b)
            if abs(ua) >= 0.98:
                fam_a.append((o.DotProduct(a), 1.0 if ua > 0 else -1.0, f))
            elif abs(ub) >= 0.98:
                fam_b.append((o.DotProduct(b), 1.0 if ub > 0 else -1.0, f))
    if len(fam_a) < 2 or len(fam_b) < 2 or len(zs) < 2:
        return None

    def _inner(items):
        lo = min(c for c, _s, _f in items)
        hi = max(c for c, _s, _f in items)
        third = (hi - lo) / 3.0
        near_lo = [t for t in items if t[1] > 0 and t[0] <= lo + third]   # 朝 +方向的面 = 低侧内壁
        near_hi = [t for t in items if t[1] < 0 and t[0] >= hi - third]
        if not near_lo or not near_hi:
            return None
        l = max(near_lo, key=lambda t: t[0])
        r = min(near_hi, key=lambda t: t[0])
        return l, r, lo, hi

    ia = _inner(fam_a)
    ib = _inner(fam_b)
    iz = _inner(zs)
    if ia is None or ib is None or iz is None:
        return None
    return {
        'a': a, 'b': b, 'theta': th,
        'uL': ia[0][0], 'uL_f': ia[0][2], 'uR': ia[1][0], 'uR_f': ia[1][2], 'uo0': ia[2], 'uo1': ia[3],
        'wB': ib[0][0], 'wB_f': ib[0][2], 'wT': ib[1][0], 'wT_f': ib[1][2], 'wo0': ib[2], 'wo1': ib[3],
        'zB': iz[0][0], 'zB_f': iz[0][2], 'zT': iz[1][0], 'zT_f': iz[1][2], 'zo0': iz[2], 'zo1': iz[3],
    }


def _rot_pt(info, u, w, z):
    """井局部坐标 (u, w, z) → 世界点。"""
    a = info['a']
    b = info['b']
    return DB.XYZ(a.X * u + b.X * w, a.Y * u + b.Y * w, z)


def _bank_contacts(bank, info):
    """排管在井口的两个「接触点」：排管两侧面与端面平面的交点（水平投影，世界 XY）。
    返回 (端点, 单位方向, [P1, P2])；取不到返回 None。端点取中心线落在井内腔范围里（外扩 1 ft）的那一端。"""
    axis = _bank_axis(bank)
    if axis is None:
        return None
    u, p0, p1 = axis
    uh = DB.XYZ(u.X, u.Y, 0.0)
    ln = uh.GetLength()
    if ln < 1e-6:
        return None
    uh = uh.Normalize()
    a = info['a']
    b = info['b']

    def _in(p):
        uu = p.DotProduct(a)
        ww = p.DotProduct(b)
        return (info['uo0'] - 1.0 <= uu <= info['uo1'] + 1.0 and info['wo0'] - 1.0 <= ww <= info['wo1'] + 1.0
                and info['zo0'] - 1.0 <= p.Z <= info['zo1'] + 1.0)
    ins = [p for p in (p0, p1) if _in(p)]
    if not ins:
        return None
    if len(ins) == 2:
        # 两端都在：取离井中心更近的……保守起见取靠近井壁的一端（离中心远的）
        cu = (info['uL'] + info['uR']) / 2.0
        cw = (info['wB'] + info['wT']) / 2.0
        ins.sort(key=lambda p: -((p.DotProduct(a) - cu) ** 2 + (p.DotProduct(b) - cw) ** 2))
    e = ins[0]
    # 侧面：法线水平、垂直于排管方向的平面，取沿法线方向两个最外的坐标
    nrm = DB.XYZ(-uh.Y, uh.X, 0.0)
    cs = []
    try:
        for f in _extract_faces(bank):
            n = f['norm']
            if abs(n.Z) > 0.2:
                continue
            if abs(n.DotProduct(nrm)) < 0.98:
                continue
            cs.append(f['orig'].DotProduct(nrm))
    except:
        cs = []
    if len(cs) < 2:
        return None
    c1, c2 = min(cs), max(cs)
    if c2 - c1 < 0.05:
        return None
    # 端面平面：uh·p = uh·e；两侧面：nrm·p = c
    d0 = uh.DotProduct(e)
    pts = []
    for c in (c1, c2):
        pts.append(DB.XYZ(uh.X * d0 + nrm.X * c, uh.Y * d0 + nrm.Y * c, e.Z))
    return e, uh, pts


def _rot_z_extent(bank, e):
    """排管在接触端的竖向范围：返回 (低, 低引用, 高, 高引用)；取不到返回 None。"""
    try:
        z = _axial_all(bank, _DIM_AXIAL_TOL)['Z']
        if len(z) >= 2:
            return z[0][0], z[0][1]['ref'], z[-1][0], z[-1][1]['ref']
    except:
        pass
    try:
        r = _end_edges(bank, 'X' if abs(e.X) >= abs(e.Y) else 'Y', 'Z', e.X if abs(e.X) >= abs(e.Y) else e.Y)
        if r:
            return r
    except:
        pass
    return None


def make_rotated_dims(view, elems, ledger=None, stats=None):
    """旋转井的标注，返回 (生成数, 原因)。结束后删掉没被任何尺寸用到的引出线。"""
    del _TRACE_CREATED[:]
    try:
        return _make_rotated_dims(view, elems, ledger, stats)
    finally:
        _prune_unused_traces(view)


def _prune_unused_traces(view):
    """删除本次新画、但没有被任何尺寸引用的引出线（尺寸因「已有」或失败没生成时，线就是多余的）。"""
    try:
        if not _TRACE_CREATED:
            return
        used = set()
        for dm in DB.FilteredElementCollector(doc, view.Id).OfClass(DB.Dimension).ToElements():
            try:
                for r in dm.References:
                    used.add(int(r.ElementId.Value))
            except:
                continue
        for cid in list(_TRACE_CREATED):
            try:
                if int(cid.Value) not in used:
                    doc.Delete(cid)
            except:
                pass
    finally:
        del _TRACE_CREATED[:]


def _make_rotated_dims(view, elems, ledger=None, stats=None):
    info = _rot_info(elems)
    if info is None:
        return 0, '旋转井的内壁无法识别'
    fr = _frame(view)
    plan = fr['plan']
    a = info['a']
    b = info['b']
    style = _invisible_style_id()
    made = 0
    zmid = (info['zB'] + info['zT']) / 2.0
    rng = crop_world_range(view)
    box_all = union_box(view, elems)
    banks = _find_banks(rng, fr, stats) if rng else []
    live = []
    for bk in banks:
        if not _bank_ends_at_well(bk, box_all):
            _bump(stats, _SKIP_BANK_PASS)
            continue
        c = _bank_contacts(bk, info)
        if c is None:
            _bump(stats, _SKIP_BANK_DIR)
            continue
        live.append((bk, c))
    reach = {'L': 0.0, 'R': 0.0, 'B': 0.0, 'T': 0.0}    # 各侧最外一道链线离外壁的距离
    lines_cache = {}

    def _tline(key, pa, pb):
        """按键缓存的引出线参照。"""
        if key in lines_cache:
            return lines_cache[key]
        try:
            _dc, r = _trace_ref(view, pa, pb, style)
        except:
            r = None
        lines_cache[key] = r
        return r

    if plan:
        uL, uR, wB, wT = info['uL'], info['uR'], info['wB'], info['wT']
        uo0, uo1, wo0, wo1 = info['uo0'], info['uo1'], info['wo0'], info['wo1']
        cnt = {'L': 0, 'R': 0, 'B': 0, 'T': 0}

        def _layer(side):
            k = cnt[side]
            cnt[side] += 1
            off = _CHAIN_OFFSET_FT + k * _CHAIN_STEP_FT
            reach[side] = max(reach[side], off)
            return off

        for bk, (e, uh, pts) in live:
            try:
                qs = [(p.DotProduct(a), p.DotProduct(b)) for p in pts]
                along_a = abs(uh.DotProduct(a)) >= abs(uh.DotProduct(b))
                eu, ew = e.DotProduct(a), e.DotProduct(b)
                if along_a:
                    # 排管沿 a 进井：测它在 b 方向的位置，链放在 a 的低/高侧
                    side = 'L' if abs(eu - uL) <= abs(eu - uR) else 'R'
                    off = _layer(side)
                    ub = (uo0 - off) if side == 'L' else (uo1 + off)
                    refs = [info['wB_f']['ref']]
                    ws = sorted(q[1] for q in qs)
                    keep = [w_ for w_ in ws if wB + _GAP_MIN_FT < w_ < wT - _GAP_MIN_FT]
                    for w_ in keep:
                        # 引出线沿 a 方向（垂直于测量方向 b），从井外壁画到链线之外 0.3 ft
                        r = _tline(('w', round(w_, 3), side),
                                   _rot_pt(info, (uo0 if side == 'L' else uo1), w_, zmid),
                                   _rot_pt(info, ub - _TRACE_EXT_FT if side == 'L' else ub + _TRACE_EXT_FT, w_, zmid))
                        if r is None:
                            raise ValueError('引出线失败')
                        refs.append(r)
                    refs.append(info['wT_f']['ref'])
                    if len(refs) >= 3:
                        if _try_dim(view, refs, _rot_pt(info, ub, wB, zmid), _rot_pt(info, ub, wT, zmid), ledger, dim_type=True):
                            made += 1
                else:
                    side = 'B' if abs(ew - wB) <= abs(ew - wT) else 'T'
                    off = _layer(side)
                    wb = (wo0 - off) if side == 'B' else (wo1 + off)
                    refs = [info['uL_f']['ref']]
                    us = sorted(q[0] for q in qs)
                    keep = [u_ for u_ in us if uL + _GAP_MIN_FT < u_ < uR - _GAP_MIN_FT]
                    for u_ in keep:
                        r = _tline(('u', round(u_, 3), side),
                                   _rot_pt(info, u_, (wo0 if side == 'B' else wo1), zmid),
                                   _rot_pt(info, u_, wb - _TRACE_EXT_FT if side == 'B' else wb + _TRACE_EXT_FT, zmid))
                        if r is None:
                            raise ValueError('引出线失败')
                        refs.append(r)
                    refs.append(info['uR_f']['ref'])
                    if len(refs) >= 3:
                        if _try_dim(view, refs, _rot_pt(info, uL, wb, zmid), _rot_pt(info, uR, wb, zmid), ledger, dim_type=True):
                            made += 1
            except:
                _bump(stats, _SKIP_BANK_ERR)
                continue
        # 总尺寸：沿 a 的宽（下方）、沿 b 的高（左侧），放在最外一道链线之外
        try:
            gB = max(_DIM_MARGIN, reach['B'] + _TOTAL_GAP_FT)
            if _try_dim(view, [info['uL_f']['ref'], info['uR_f']['ref']],
                        _rot_pt(info, uL, wo0 - gB, zmid), _rot_pt(info, uR, wo0 - gB, zmid), ledger, dim_type=True):
                made += 1
            gL = max(_DIM_MARGIN, reach['L'] + _TOTAL_GAP_FT)
            if _try_dim(view, [info['wB_f']['ref'], info['wT_f']['ref']],
                        _rot_pt(info, uo0 - gL, wB, zmid), _rot_pt(info, uo0 - gL, wT, zmid), ledger, dim_type=True):
                made += 1
        except:
            _bump(stats, _SKIP_BANK_ERR)
        if made:
            _bump(stats, _ST_BANK_OK, len(live))
        return made, (None if made else '未生成尺寸')

    # ---------- 立面 ----------
    h, d = fr['h'], fr['d']

    def _hv(p):
        return _coord(p, h)

    # 井内腔四个内角的水平投影，取最左/最右
    corners = [_rot_pt(info, u_, w_, zmid) for u_ in (info['uL'], info['uR']) for w_ in (info['wB'], info['wT'])]
    hl = min(_hv(p) for p in corners)
    hr = max(_hv(p) for p in corners)
    outer = [_rot_pt(info, u_, w_, zmid) for u_ in (info['uo0'], info['uo1']) for w_ in (info['wo0'], info['wo1'])]
    hol = min(_hv(p) for p in outer)
    hor = max(_hv(p) for p in outer)
    dmid = sum(_coord(p, d) for p in corners) / 4.0
    zB, zT = info['zB'], info['zT']
    zo0 = info['zo0']
    hmid = (hl + hr) / 2.0
    cntv = {'L': 0, 'R': 0, 'B': 0, 'T': 0}

    def _layer2(side):
        k = cntv[side]
        cntv[side] += 1
        off = _CHAIN_OFFSET_FT + k * _CHAIN_STEP_FT
        reach[side] = max(reach[side], off)
        return off

    def PT(hv, vv):
        return _mk(fr, hv, vv, dmid)

    def _vline(hv):
        return _tline(('h', round(hv, 3)), PT(hv, zB - 0.2), PT(hv, zT + 0.2))

    # 竖向链：排管接触端的上下边到井内底/内顶
    dvec = _unit(d, 1)
    for bk, (e, uh, pts) in live:
        try:
            zx = _rot_z_extent(bk, e)
            if zx is None:
                _bump(stats, _SKIP_BANK_DIR)
                continue
            lo, lo_r, hi, hi_r = zx
            eh = _hv(e)
            side = 'L' if eh < hmid else 'R'
            off = _layer2(side)
            hs = (hol - off) if side == 'L' else (hor + off)
            refs = [info['zB_f']['ref']]
            if lo > zB + _GAP_MIN_FT:
                refs.append(lo_r)
            if hi < zT - _GAP_MIN_FT:
                refs.append(hi_r)
            refs.append(info['zT_f']['ref'])
            n0 = made
            if len(refs) >= 3:
                dup0 = ledger.dup if ledger is not None else 0
                # 带一点点坡度的排管，它的「近似水平」面 Revit 创建时认、提交时才判无效（整步回滚）：这种直接用引出线
                _ax = _bank_axis(bk)
                _sloped = _ax is not None and abs(_ax[0].Z) > 0.0003
                first_ok = False
                if not (_sloped or _ROT_FORCE_TRACE[0]):
                    first_ok = bool(_try_dim(view, refs, PT(hs, zB), PT(hs, zT), ledger, dim_type=True))
                    if first_ok:
                        made += 1
                if not first_ok and not (ledger is not None and ledger.dup > dup0):
                    # 排管的面做参照被 Revit 拒绝（带坡度的排管常见）：在排管上下边的高度画水平引出线再标
                    wall_h = hol if side == 'L' else hor
                    ext = hs - _TRACE_EXT_FT if side == 'L' else hs + _TRACE_EXT_FT
                    refs3 = [info['zB_f']['ref']]
                    for zc in (lo, hi):
                        if zB + _GAP_MIN_FT < zc < zT - _GAP_MIN_FT:
                            r3 = _tline(('z', round(zc, 3), side), PT(wall_h, zc), PT(ext, zc))
                            if r3 is not None:
                                refs3.append(r3)
                    refs3.append(info['zT_f']['ref'])
                    if len(refs3) >= 3 and _try_dim(view, refs3, PT(hs, zB), PT(hs, zT), ledger, dim_type=True):
                        made += 1
            # 水平链：排管正对视线（走向有较大的视线分量）时，量它在水平方向的位置
            if abs(uh.DotProduct(dvec)) >= 0.5:
                hs_ = sorted(_hv(p) for p in pts)
                keep = [x for x in hs_ if hl + _GAP_MIN_FT < x < hr - _GAP_MIN_FT]
                sideh = 'B' if (lo + hi) / 2.0 < (zB + zT) / 2.0 else 'T'
                offh = _layer2(sideh)
                vs = (zo0 - offh) if sideh == 'B' else (info['zo1'] + offh)
                refs2 = [_vline(hl)]
                for x in keep:
                    refs2.append(_vline(x))
                refs2.append(_vline(hr))
                if len(refs2) >= 3:
                    if _try_dim(view, refs2, PT(hl, vs), PT(hr, vs), ledger, dim_type=True):
                        made += 1
            if made > n0:
                _bump(stats, _ST_BANK_OK)
        except:
            _bump(stats, _SKIP_BANK_ERR)
            continue
    # 总尺寸（按视图名规则）：宽在下方、高在左侧
    try:
        rule = _total_rule(view)
        if 'W' in rule:
            g = max(_DIM_MARGIN, reach['B'] + _TOTAL_GAP_FT)
            if _try_dim(view, [_vline(hl), _vline(hr)], PT(hl, zo0 - g), PT(hr, zo0 - g), ledger, dim_type=True):
                made += 1
        if 'H' in rule:
            g = max(_DIM_MARGIN, reach['L'] + _TOTAL_GAP_FT)
            if _try_dim(view, [info['zB_f']['ref'], info['zT_f']['ref']], PT(hol - g, zB), PT(hol - g, zT), ledger, dim_type=True):
                made += 1
    except:
        _bump(stats, _SKIP_BANK_ERR)
    return made, (None if made else '未生成尺寸')


# ============================================================
# 入口
# ============================================================


class _SilentFailures(DB.IFailuresPreprocessor):
    """抑制标注引发的失败消息：警告直接删除；无法忽略的错误（如「尺寸标注参照无效」）不弹窗，
    直接让当前这一步回滚（上层按步骤隔离，其余步骤不受影响）。绝不自己删元素。"""

    def PreprocessFailures(self, failuresAccessor):
        has_error = False
        for f in failuresAccessor.GetFailureMessages():
            try:
                sev = f.GetSeverity()
            except:
                continue
            if sev == DB.FailureSeverity.Warning:
                try:
                    failuresAccessor.DeleteWarning(f)
                except:
                    pass
                continue
            has_error = True
        if has_error:
            return DB.FailureProcessingResult.ProceedWithRollBack
        return DB.FailureProcessingResult.Continue


def _sheet_of(e):
    if e is None:
        return None
    if isinstance(e, DB.ViewSheet):
        return e
    try:
        if e.OwnerViewId and e.OwnerViewId != DB.ElementId.InvalidElementId:
            return doc.GetElement(e.OwnerViewId)
    except:
        pass
    return None


_WELL_CAT = DB.BuiltInCategory.OST_GenericModel     # 井所在的类别（设置文件 well_category）
_MARK_PARAM = ''                                    # 井号参数名；空 = Revit 自带的「标记」（设置文件 mark_parameter）
_CONFIRM_RUN = True                                 # 运行前先弹确认（设置文件 confirm_before_run）
_SKIP_ANNOTATED = True                              # 已经有尺寸的视图默认跳过（设置文件 skip_annotated_views）


def _mark_of(e):
    try:
        p = e.LookupParameter(_MARK_PARAM) if _MARK_PARAM else e.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
        return p.AsString().strip() if (p and p.AsString()) else None
    except:
        return None


_MODEL_MARKS = [None]


def _model_marks():
    """模型里所有带井号的常规模型构件的井号（只查一次）。"""
    if _MODEL_MARKS[0] is None:
        s = set()
        for e in DB.FilteredElementCollector(doc).OfCategory(_WELL_CAT).WhereElementIsNotElementType().ToElements():
            mk = _mark_of(e)
            if mk:
                s.add(mk)
        _MODEL_MARKS[0] = s
    return _MODEL_MARKS[0]


def _resolve_mark(mark):
    """视图名里的井号可能带 TELCO 旧后缀（T-083-1），模型里的井号是 T-083：原号找不到时去掉 -1。"""
    if mark and mark not in _model_marks():
        base = re.sub(u'-1$', u'', mark)
        if base in _model_marks():
            return base
    return mark


_SHEET_VIEWNAMES = [None]      # 图纸Id -> 这张图纸上各视图的名字；一次扫完全模型的视口，避免对每张图纸单独查询（图纸多时很慢）
_SHEET_MARK_CACHE = {}


def _load_sheet_caches():
    """一次读出全模型的视口，按图纸归好类。之后判断「是不是井大样图纸」「井号是什么」都不再逐张图纸查询。"""
    names = {}
    for vp in DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements():
        try:
            v = doc.GetElement(vp.ViewId)
            if v is None or v.IsTemplate:
                continue
            names.setdefault(int(vp.SheetId.Value), []).append(v.Name or u'')
        except:
            continue
    _SHEET_VIEWNAMES[0] = names
    _SHEET_MARK_CACHE.clear()


def _sheet_mark(sheet):
    """图纸的井号。优先取视图名里的 AG-<井号>-xxxx（井展开图生成视图时写入，与图纸名无关）；
    取不到再按图名去掉前缀（旧命名「井大样_AA-01」）。图纸名改成「BTP-IF1 275Kv-AA-18 Detail Drawing」也能对上。
    结果按图纸缓存（见 _load_sheet_caches）。"""
    try:
        sid = int(sheet.Id.Value)
    except:
        sid = None
    if sid is not None and sid in _SHEET_MARK_CACHE:
        return _SHEET_MARK_CACHE[sid]
    mark = None
    try:
        if _SHEET_VIEWNAMES[0] is not None and sid is not None:
            names = _SHEET_VIEWNAMES[0].get(sid, [])
        else:
            names = [_nm for _k, _nm, _v in _views_of(sheet)]
        for _nm in names:
            m = re.search(u'AG-(.+)-[0-9a-f]{4}$', _nm or u'')
            if m:
                mark = _resolve_mark(m.group(1))
                break
    except:
        mark = None
    if mark is None:
        nm = sheet.Name or u''
        if nm.startswith(WS_PREFIX):
            mark = _resolve_mark(nm[len(WS_PREFIX):])
    if sid is not None and _SHEET_VIEWNAMES[0] is not None:
        _SHEET_MARK_CACHE[sid] = mark
    return mark


def _is_well_sheet(sheet):
    """是不是井大样图纸：图名以项目前缀开头，或平面视图名带 AG-<井号>-xxxx。"""
    if sheet is None:
        return False
    if (sheet.Name or u'').startswith(WS_PREFIX):
        return True
    return _sheet_mark(sheet) is not None


def _sheet_by_mark(mark):
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
        try:
            if _is_well_sheet(s) and (_sheet_mark(s) == mark or (s.Name or "") == WS_PREFIX + mark):
                return s
        except:
            pass
    return None


def _views_of(sheet):
    """取图纸上的视图，按 平面/南/北/西/东 排序。"""
    out = []
    order = {'平面': 0, '南': 1, '北': 2, '西': 3, '东': 4}
    try:
        vps = DB.FilteredElementCollector(doc, sheet.Id).OfClass(DB.Viewport).ToElements()
    except:
        return out
    for vp in vps:
        try:
            v = doc.GetElement(vp.ViewId)
        except:
            continue
        if v is None or v.IsTemplate:
            continue
        nm = v.Name or ""
        k = 9
        for pref in order:
            if nm.startswith(pref + "_"):
                k = order[pref]
                break
        out.append((k, nm, v))
    out.sort(key=lambda t: t[0])
    return out


class _ViewOption(forms.TemplateListItem):
    """视图选项：显示 图纸 / 类型 / 视图名。"""

    @property
    def name(self):
        try:
            info = self.item
        except:
            return str(self.item)
        try:
            sheet_nm, tcode, v = info
        except:
            return str(info)
        try:
            return "{}   [{}]   {}".format(sheet_nm, tcode, v.Name)
        except:
            return "{}   [{}]".format(sheet_nm, tcode)


def _axes_ok(elems, fr):
    """本井是否有足够的正交大面，能定出视图水平、竖向两个方向的井墙位置（同 _face_box 的判据）。
    井被旋转（如 TELCO 族旋转 171°）时墙面不与视图轴平行，按世界轴定位会把尺寸放到错误的位置，这种井暂不标。"""
    cnt = {'X': 0, 'Y': 0, 'Z': 0}
    for e in elems:
        try:
            for f in _extract_faces(e):
                a = f.get('area')
                if a is None or a < _WALL_AREA_MIN_FT2:
                    continue
                for ax in ('X', 'Y', 'Z'):
                    if abs(_coord(f['norm'], ax)) >= _AXIAL_TOL:
                        cnt[ax] += 1
        except:
            continue
    return cnt[fr['h']] >= 2 and cnt[fr['v']] >= 2


# ============================================================
# 设置文件：所有可调的数值、名称都在 文档/KaifaTools/井大样标注设置.json 里（第一次运行自动写出默认值）。
# 换项目、换电脑：把这个文件复制过去即可复现；删掉文件恢复默认。长度类数值单位是 mm。
# ============================================================
SETTINGS_FILE = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments),
                             'KaifaTools', '井大样标注设置.json')


def _settings_defaults():
    return {
        '说明': ('井大样标注的可调设置。长度单位 mm。dim_margin_mm=总尺寸线离井墙的最小距离；chain_offset_mm=第一道排管链离井墙；'
                'chain_step_mm=相邻两道链的间距；total_gap_mm=总尺寸线离最外一道链的净距；protrude_mm=排管伸出井墙多少以内仍算接入这口井；'
                'reach_tol_mm=排管端头离井墙多远以内算接入；crop_text_mm=注释裁剪开启时尺寸离框边的最小距离；'
                'wall_area_min_m2=算作井墙的最小面面积；span_ratio=长构件判定倍数；dim_type_name=排管链用的尺寸类型名（找不到用默认）；'
                'total_rules=各视图标哪条总尺寸（键=视图名前缀，W=宽，H=高，空=不标）；well_category=井所在的类别（Revit 内部类别名，如 OST_GenericModel）；'
                'mark_parameter=井号参数名（空=Revit 自带的「标记」）；bank_category=排管所在类别；'
                'confirm_before_run=运行前先弹确认窗口；skip_annotated_views=已经有尺寸的视图默认跳过。'),
        'dim_margin_mm': round(_DIM_MARGIN * _MM_PER_FT, 3),
        'chain_offset_mm': _CHAIN_OFFSET_MM,
        'chain_step_mm': _CHAIN_STEP_MM,
        'total_gap_mm': _TOTAL_GAP_MM,
        'protrude_mm': round(_PROTRUDE_FT * _MM_PER_FT, 3),
        'reach_tol_mm': round(_REACH_TOL_FT * _MM_PER_FT, 3),
        'crop_text_mm': round(_CROP_TEXT_FT * _MM_PER_FT, 3),
        'wall_area_min_m2': round(_WALL_AREA_MIN_FT2 / 10.7639, 5),
        'span_ratio': _SPAN_RATIO,
        'dim_type_name': _DIM_TYPE_NAME,
        'total_rules': dict(_TOTAL_RULES),
        'well_category': 'OST_GenericModel',
        'mark_parameter': '',
        'bank_category': 'OST_StructuralFraming',
        'confirm_before_run': _CONFIRM_RUN,
        'skip_annotated_views': _SKIP_ANNOTATED,
    }


def _apply_settings():
    """读设置文件（没有就写出默认值）并覆盖脚本里的默认数值。某一项写错时只忽略那一项。返回设置文件路径。"""
    global _DIM_MARGIN, _CHAIN_OFFSET_MM, _CHAIN_OFFSET_FT, _CHAIN_STEP_MM, _CHAIN_STEP_FT, _TOTAL_GAP_MM, _TOTAL_GAP_FT
    global _PROTRUDE_FT, _REACH_TOL_FT, _CROP_TEXT_FT, _WALL_AREA_MIN_FT2, _SPAN_RATIO, _DIM_TYPE_NAME, _TOTAL_RULES
    global _WELL_CAT, _MARK_PARAM, _BANK_CAT, _BANK_IDS, _CONFIRM_RUN, _SKIP_ANNOTATED
    d = _settings_defaults()
    try:
        if not os.path.isfile(SETTINGS_FILE):
            dd = os.path.dirname(SETTINGS_FILE)
            if not os.path.isdir(dd):
                os.makedirs(dd)
            txt = json.dumps(d, ensure_ascii=False, indent=1)
            if not isinstance(txt, type('')):
                txt = txt.decode('utf-8')
            with io.open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                f.write(txt)
        else:
            with io.open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                user = json.load(f)
            if isinstance(user, dict):
                for k, v in user.items():
                    if k in d and k != '说明':
                        d[k] = v
    except Exception:
        pass

    def num(key, default):
        try:
            return float(d[key])
        except Exception:
            return default
    _DIM_MARGIN = num('dim_margin_mm', _DIM_MARGIN * _MM_PER_FT) / _MM_PER_FT
    _CHAIN_OFFSET_MM = num('chain_offset_mm', _CHAIN_OFFSET_MM)
    _CHAIN_OFFSET_FT = mm_to_ft(_CHAIN_OFFSET_MM)
    _CHAIN_STEP_MM = num('chain_step_mm', _CHAIN_STEP_MM)
    _CHAIN_STEP_FT = mm_to_ft(_CHAIN_STEP_MM)
    _TOTAL_GAP_MM = num('total_gap_mm', _TOTAL_GAP_MM)
    _TOTAL_GAP_FT = mm_to_ft(_TOTAL_GAP_MM)
    _PROTRUDE_FT = num('protrude_mm', _PROTRUDE_FT * _MM_PER_FT) / _MM_PER_FT
    _REACH_TOL_FT = num('reach_tol_mm', _REACH_TOL_FT * _MM_PER_FT) / _MM_PER_FT
    _CROP_TEXT_FT = num('crop_text_mm', _CROP_TEXT_FT * _MM_PER_FT) / _MM_PER_FT
    _WALL_AREA_MIN_FT2 = num('wall_area_min_m2', _WALL_AREA_MIN_FT2 / 10.7639) * 10.7639
    _SPAN_RATIO = num('span_ratio', _SPAN_RATIO)
    if d.get('dim_type_name'):
        if d['dim_type_name'] != _DIM_TYPE_NAME:
            _DIM_TYPE_ID[0] = None
        _DIM_TYPE_NAME = d['dim_type_name']
    tr = d.get('total_rules')
    if isinstance(tr, dict):
        _TOTAL_RULES = dict((k, (v or '')) for k, v in tr.items())
    _MARK_PARAM = (d.get('mark_parameter') or '').strip()
    _CONFIRM_RUN = bool(d.get('confirm_before_run', True))
    _SKIP_ANNOTATED = bool(d.get('skip_annotated_views', True))
    wc = getattr(DB.BuiltInCategory, str(d.get('well_category') or ''), None)
    if wc is not None:
        _WELL_CAT = wc
    bc = getattr(DB.BuiltInCategory, str(d.get('bank_category') or ''), None)
    if bc is not None:
        _BANK_CAT = bc
        _BANK_IDS = _ints([_BANK_CAT])
        for i in _BANK_IDS:
            if i not in _PIPE_IDS:
                _PIPE_IDS.append(i)
    return SETTINGS_FILE


_FACTORY = _settings_defaults()       # 出厂默认值（在读设置文件之前取，供「恢复默认」用）

_VIEW_CHOICES = [('WH', '宽和高都标'), ('W', '只标宽'), ('H', '只标高'), ('', '不标总尺寸')]
_CAT_CHOICES = [('OST_GenericModel', '常规模型'), ('OST_StructuralFraming', '结构框架'), ('OST_ElectricalEquipment', '电气设备'),
                ('OST_MechanicalEquipment', '机械设备'), ('OST_PipeFitting', '管件'), ('OST_Conduit', '线管')]


def _settings_fields():
    f = [
        {'kind': 'header', 'label': '防呆'},
        {'key': 'confirm_before_run', 'label': '运行前先确认', 'kind': 'bool', 'bool_text': '标注前弹出确认窗口（说明标哪里、几个视图）',
         'hint': '建议保持勾选，避免一点就开始跑。'},
        {'key': 'skip_annotated_views', 'label': '已经有尺寸的视图', 'kind': 'bool', 'bool_text': '默认跳过（不再重复标注）',
         'hint': '确认窗口里仍然可以选「全部都标」。'},
        {'kind': 'header', 'label': '尺寸线的位置（长度单位 mm）'},
        {'key': 'dim_margin_mm', 'label': '总尺寸线离井墙的最小距离', 'kind': 'num', 'min': 0, 'max': 5000,
         'hint': '没有排管链的一侧，总尺寸线离井墙多远。'},
        {'key': 'chain_offset_mm', 'label': '第一道排管链离井墙', 'kind': 'num', 'min': 0, 'max': 5000,
         'hint': '井内壁到排管的尺寸链，第一道放在离井墙多远的地方。'},
        {'key': 'chain_step_mm', 'label': '相邻两道尺寸链的间距', 'kind': 'num', 'min': 50, 'max': 5000,
         'hint': '同一侧有多道链时，每道往外排的间距。要大于一行尺寸文字的高度。'},
        {'key': 'total_gap_mm', 'label': '总尺寸线离最外一道链的净距', 'kind': 'num', 'min': 0, 'max': 5000,
         'hint': '该侧有排管链时，总尺寸线放在最外一道链外面多远。'},
        {'key': 'crop_text_mm', 'label': '尺寸离注释裁剪框边的最小距离', 'kind': 'num', 'min': 0, 'max': 2000,
         'hint': '只在视图的「注释裁剪」开启时用，超出的尺寸照常标，只在报告里列出。'},
        {'kind': 'header', 'label': '排管怎么算接入这口井（mm）'},
        {'key': 'protrude_mm', 'label': '排管伸出井墙多少以内仍然标', 'kind': 'num', 'min': 0, 'max': 3000,
         'hint': '排管截面稍伸出井墙也算这口井的，照常标尺寸链。'},
        {'key': 'reach_tol_mm', 'label': '排管端头离井墙多远以内算接入', 'kind': 'num', 'min': 0, 'max': 3000,
         'hint': '排管端面离井内壁差几厘米，也算接到这口井。'},
        {'kind': 'header', 'label': '识别井墙'},
        {'key': 'wall_area_min_m2', 'label': '算作井墙的最小面面积 (m²)', 'kind': 'num', 'min': 0, 'max': 100,
         'hint': '小于这个面积的碎面、倒角不当作井墙。'},
        {'key': 'span_ratio', 'label': '长构件判定倍数', 'kind': 'num', 'min': 1, 'max': 20,
         'hint': '构件在视图里的尺寸超过视图框的这个倍数，当作横穿的长构件剔除。'},
        {'kind': 'header', 'label': '各视图标哪条总尺寸'},
    ]
    for name in ('平面', '南', '北', '西', '东'):
        f.append({'key': 'total_rules.' + name, 'label': name + ' 视图', 'kind': 'choice', 'choices': _VIEW_CHOICES})
    f += [
        {'kind': 'header', 'label': '名称和类别（换项目时可能要改）'},
        {'key': 'dim_type_name', 'label': '排管链用的尺寸类型名', 'kind': 'text',
         'hint': '找不到这个类型时用项目默认的尺寸类型。'},
        {'key': 'well_category', 'label': '井所在的类别', 'kind': 'combo', 'choices': _CAT_CHOICES,
         'hint': '可以直接填 Revit 内部类别名，如 OST_GenericModel。'},
        {'key': 'mark_parameter', 'label': '井号参数名', 'kind': 'text',
         'hint': '留空 = Revit 自带的「标记」。井号存在别的参数里时填那个参数名。'},
        {'key': 'bank_category', 'label': '排管所在的类别', 'kind': 'combo', 'choices': _CAT_CHOICES,
         'hint': '本项目用「结构框架」族承载排管。'},
    ]
    return f


def _flat_settings(d):
    out = dict(d)
    for k, v in (d.get('total_rules') or {}).items():
        out['total_rules.' + k] = v
    return out


def open_settings():
    """图形化设置窗口。保存后写回设置文件并立即生效。返回是否保存了。"""
    _apply_settings()                  # 先读文件，窗口里显示当前值
    cur = _settings_defaults()
    res = su_fields_dialog('一键井大样标注 设置',
                           '这里的数值只影响尺寸放在哪里、哪些排管算这口井的。改完点保存；「恢复默认」只改窗口里的值，点保存才生效。',
                           _settings_fields(), _flat_settings(cur), _flat_settings(_FACTORY),
                           extra_buttons=[('用记事本打开文件', lambda: su_open_file(SETTINGS_FILE))])
    if res is None:
        return False
    out = dict(cur)
    rules = {}
    for k, v in res.items():
        if k.startswith('total_rules.'):
            rules[k.split('.', 1)[1]] = v
        else:
            out[k] = v
    out['total_rules'] = rules
    txt = json.dumps(out, ensure_ascii=False, indent=1)
    if not isinstance(txt, type('')):
        txt = txt.decode('utf-8')
    with io.open(SETTINGS_FILE, 'w', encoding='utf-8') as fh:
        fh.write(txt)
    _apply_settings()
    return True


def main():
    pb0 = [None]
    try:
        _apply_settings()
        # 读取图纸要几秒到十几秒（图纸多时）：弹进度窗口让你知道它在干活，也可以点取消退出
        try:
            pb0[0] = forms.ProgressBar(title=u'一键井大样标注：正在读取图纸，请稍候…（点取消退出）', cancellable=True)
        except Exception:
            pb0[0] = None
        _load_sheet_caches()
        _FACE_CACHE.clear()
        _EDGE_CACHE.clear()
        del _BANK_ERRS[:]
        # ---------- 1. 选标注范围（范围列表同「一键导出图纸」：选中的图纸 / 当前图纸 / 各系列 / 各图集 / 全部 / 手动勾选）----------
        selected = []
        try:
            sel = list(uidoc.Selection.GetElementIds())
        except:
            sel = []
        for eid in sel:
            e = doc.GetElement(eid)
            if e is None:
                continue
            sh = _sheet_of(e)
            if sh is not None and _is_well_sheet(sh):
                if sh not in selected:
                    selected.append(sh)
                continue
            mk = _mark_of(e)
            if mk:
                sh2 = _sheet_by_mark(mk)
                if sh2 is not None and sh2 not in selected:
                    selected.append(sh2)
        cur_sheet = None
        try:
            cur = doc.ActiveView
            if isinstance(cur, DB.ViewSheet) and _is_well_sheet(cur):
                cur_sheet = cur
        except:
            pass

        all_sheets = [x for x in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements()
                      if _is_well_sheet(x)]
        all_sheets.sort(key=lambda x: x.Name or "")
        if not all_sheets:
            try:
                pb0[0].close()
            except Exception:
                pass
            forms.alert(u'项目里没有井大样图纸（名称以「{}」开头，可在 {} 里改）。'.format(WS_PREFIX, WCFG_FILE), title='一键井大样标注')
            return

        scope = []   # (显示文字, 图纸列表 / None=手动勾选)
        if selected:
            scope.append((u'项目浏览器里选中的 {} 张图纸'.format(len(selected)), selected))
        if cur_sheet is not None and cur_sheet not in selected:
            scope.append((u'当前图纸「{} {}」'.format(cur_sheet.SheetNumber, cur_sheet.Name), [cur_sheet]))
        for _sr in WCFG[u"series"]:
            _ss = [x for x in all_sheets if wcfg_series_of(_sheet_mark(x) or u'', WCFG, True) is _sr]
            if _ss:
                scope.append((u'井大样 {}（{} 张）'.format(_sr[u"label"], len(_ss)), _ss))
        col_sheets = {}
        col_order = []
        for x in all_sheets:
            try:
                cid_ = x.SheetCollectionId
                ce_ = doc.GetElement(cid_) if cid_ != DB.ElementId.InvalidElementId else None
                cn_ = ce_.Name if ce_ is not None else None
            except:
                cn_ = None
            if cn_:
                if cn_ not in col_sheets:
                    col_sheets[cn_] = []
                    col_order.append(cn_)
                col_sheets[cn_].append(x)
        for c_ in sorted(col_order, key=lambda t: wcfg_natural_key(t)):
            scope.append((u'图集「{}」（{} 张）'.format(c_, len(col_sheets[c_])), col_sheets[c_]))
        scope.append((u'全部井大样图纸（{} 张）'.format(len(all_sheets)), list(all_sheets)))
        scope.append((u'手动勾选图纸…', None))

        try:
            if pb0[0] is not None:
                _cancelled0 = bool(pb0[0].cancelled)
                pb0[0].close()
                pb0[0] = None
                if _cancelled0:
                    return
        except Exception:
            pass
        set_label = u'⚙ 设置参数…'
        # 先问「要标注哪里」：当前图纸排第一，写明「只标注」；其他范围（图集、系列、全部…）在二级列表里，不会误点
        quick = []
        if cur_sheet is not None:
            quick.append((u'只标注当前图纸「{} {}」（{} 个视图）'.format(cur_sheet.SheetNumber, cur_sheet.Name, len(_views_of(cur_sheet))), [cur_sheet]))
        if selected and not (len(selected) == 1 and cur_sheet is not None and selected[0].Id == cur_sheet.Id):
            quick.append((u'项目浏览器里选中的 {} 张图纸'.format(len(selected)), list(selected)))
        more_label = u'选择其他范围（系列、图集、全部、手动勾选）…'
        pick_ = None
        chosen = None
        if quick:
            while True:
                pick_ = forms.CommandSwitchWindow.show([t for t, _l in quick] + [more_label, set_label], message=u'要标注哪里？')
                if pick_ == set_label:
                    open_settings()
                    continue
                break
            if not pick_:
                return
            hit_ = [l for t, l in quick if t == pick_]
            if hit_:
                chosen = hit_
        if chosen is None:
            while True:
                pick_ = forms.CommandSwitchWindow.show([t for t, _l in scope] + [set_label], message=u'选择标注范围')
                if pick_ == set_label:
                    open_settings()
                    continue
                break
            if not pick_:
                return
            chosen = [l for t, l in scope if t == pick_]
        sheets = []
        if chosen and chosen[0] is None:
            by_name = {}
            for x in all_sheets:
                by_name[x.Name] = x
            picked_names = forms.SelectFromList.show(
                [x.Name for x in all_sheets],
                title='选择要标注的井大样图纸（可多选）',
                button_name='开始标注',
                multiselect=True)
            if not picked_names:
                return
            if not isinstance(picked_names, (list, tuple)):
                picked_names = [picked_names]
            for nm_ in picked_names:
                if nm_ in by_name and by_name[nm_] not in sheets:
                    sheets.append(by_name[nm_])
        elif chosen:
            sheets = list(chosen[0])
        if not sheets:
            return

        # ---------- 2. 取所选图纸上的全部视图 ----------
        target_views = []
        for sh in sheets:
            for k, nm, v in _views_of(sh):
                target_views.append(v)
        if not target_views:
            forms.alert('所选图纸上没有可用视图。', title='一键井大样标注')
            return

        mark_of_view = {}
        for sh in sheets:
            _mk = _sheet_mark(sh) or ''
            for _k, _nm, _v in _views_of(sh):
                mark_of_view[str(_v.Id)] = _mk

        # 同一视图若放在多张图纸上会被重复选中：只处理一次，避免重复计数
        uniq_views = []
        uniq_ids = set()
        for v in target_views:
            try:
                vid = v.Id.Value
            except:
                vid = None
            if vid is not None:
                if vid in uniq_ids:
                    continue
                uniq_ids.add(vid)
            uniq_views.append(v)
        dup_views = len(target_views) - len(uniq_views)
        target_views = uniq_views

        # ---------- 防呆：运行前确认；已经有尺寸的视图默认跳过 ----------
        annotated_ids = set()
        try:
            want_ids = set(int(v_.Id.Value) for v_ in target_views)
            for dm_ in DB.FilteredElementCollector(doc).OfClass(DB.Dimension).ToElements():     # 全模型只扫一遍尺寸，按所属视图归类
                try:
                    ov_ = int(dm_.OwnerViewId.Value)
                except Exception:
                    continue
                if ov_ in want_ids:
                    annotated_ids.add(ov_)
        except Exception:
            annotated_ids = set()
        n_all = len(target_views)
        n_ann = len(annotated_ids)
        skip_annotated = bool(_SKIP_ANNOTATED)
        if len(sheets) == 1:
            scope_txt = u'图纸「{} {}」'.format(sheets[0].SheetNumber, sheets[0].Name)
        else:
            scope_txt = u'{} 张图纸'.format(len(sheets))
        MB = _SUW.MessageBox
        if _CONFIRM_RUN:
            if n_ann == 0:
                ans_ = MB.Show(u'将标注：{}（共 {} 个视图，都还没有尺寸）。\n\n确定开始吗？标注期间会有进度条，可以随时取消。'.format(scope_txt, n_all),
                               u'一键井大样标注', _SUW.MessageBoxButtons.OKCancel, _SUW.MessageBoxIcon.Question)
                if ans_ != _SUW.DialogResult.OK:
                    return
            elif n_ann < n_all:
                ans_ = MB.Show(u'将标注：{}\n共 {} 个视图，其中 {} 个已经有尺寸。\n\n'
                               u'是：只标没有尺寸的 {} 个视图（推荐）\n'
                               u'否：全部都标（已有尺寸的补标，同位置的不会重复）\n'
                               u'取消：不标注'.format(scope_txt, n_all, n_ann, n_all - n_ann),
                               u'一键井大样标注', _SUW.MessageBoxButtons.YesNoCancel, _SUW.MessageBoxIcon.Question)
                if ans_ == _SUW.DialogResult.Cancel:
                    return
                skip_annotated = (ans_ == _SUW.DialogResult.Yes)
            else:
                ans_ = MB.Show(u'将标注：{}\n这 {} 个视图都已经有尺寸了。\n\n'
                               u'是：仍然补标（同位置的不会重复）\n否：不标注'.format(scope_txt, n_all),
                               u'一键井大样标注', _SUW.MessageBoxButtons.YesNo, _SUW.MessageBoxIcon.Question)
                if ans_ != _SUW.DialogResult.Yes:
                    return
                skip_annotated = False
        elif skip_annotated and n_all and n_ann >= n_all:
            forms.alert(u'这些视图都已经有尺寸了，没有需要标注的。想重新补标，请在设置里取消「已经有尺寸的视图默认跳过」。', title='一键井大样标注')
            return

        # ---------- 3. 执行 ----------
        # 只新增尺寸，不改视图：不动裁剪框，不隐藏/显示任何构件，不删除视图里已有的尺寸。
        # 每张视图先整体排（尺寸链 + 总尺寸）；某步被 Revit 判定参照失效则整体回滚，再分开排、逐根排管各自提交。
        # 整次运行合并为一次撤销。
        tg = DB.TransactionGroup(doc, "KAIFA 一键井大样标注")
        tg.Start()

        def _step(name, work):
            """在独立事务里执行 work()；提交成功返回 True，失败（已回滚）返回 False。"""
            t = DB.Transaction(doc, name)
            fho = t.GetFailureHandlingOptions()
            fho.SetFailuresPreprocessor(_SilentFailures())
            fho.SetClearAfterRollback(True)
            t.SetFailureHandlingOptions(fho)
            t.Start()
            try:
                work()
                return t.Commit() == DB.TransactionStatus.Committed
            except Exception:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                return False

        made = 0
        ok_views = 0
        stats = {}
        notes = []
        dim_errs = []
        failed_steps = []
        out_views = []   # 裁剪框放不下而未标的视图：(视图名, 未标道数, 缺口说明)
        out_total = 0
        cancelled = [False]
        done_views = [0]
        skipped_annot = [0]
        pb = forms.ProgressBar(title=u'正在标注…（点取消可随时停止，已标注的保留）', cancellable=True)
        try:
            for _vi, v in enumerate(target_views):
                try:
                    pb.update_progress(_vi, len(target_views))
                except Exception:
                    pass
                if pb.cancelled:
                    cancelled[0] = True
                    break
                done_views[0] = _vi + 1
                if skip_annotated and int(v.Id.Value) in annotated_ids:
                    skipped_annot[0] += 1          # 已经有尺寸：不再重复跑
                    continue
                try:
                    del _DIM_ERRS[:]
                    _OUT_CROP.clear()
                    elems = _main_well(v, collect_target_elems(v, stats), mark_of_view.get(str(v.Id)))
                    if not elems:
                        # 井族的包围盒比裁剪框大太多（如斜排管把井体撑大）时会被当成「长构件」剔掉，
                        # 导致整张视图没有构件；这种情况放宽跨度判断再找一次本井
                        elems = _main_well(v, collect_target_elems(v, None, True), mark_of_view.get(str(v.Id)))
                    if not elems:
                        notes.append(('{}（无可用构件）'.format(v.Name), 0))
                        continue
                    if not _axes_ok(elems, _frame(v)):
                        # 井体旋转（墙面不与视图轴平行）：走旋转井的标注
                        rres = {'n': 0, 'why': None}
                        rled = _fresh_ledger(v)

                        def _rot(v=v, elems=elems, rres=rres, rled=rled):
                            rres['n'], rres['why'] = make_rotated_dims(v, elems, rled, stats)
                        ok_rot = _step("旋转井标注", _rot)
                        if not ok_rot:
                            # 提交时 Revit 判某条尺寸的参照无效，整步回滚了：改成全部用引出线再试一次
                            _ROT_FORCE_TRACE[0] = True
                            try:
                                rres['n'], rres['why'] = 0, None
                                rled = _fresh_ledger(v)
                                ok_rot = _step("旋转井标注（引出线）", _rot)
                            finally:
                                _ROT_FORCE_TRACE[0] = False
                        if not ok_rot:
                            failed_steps.append('{}：旋转井标注（Revit 判定参照失效，引出线重试也失败）'.format(v.Name))
                            continue
                        _bump(stats, _ST_DUP, rled.dup)
                        dim_errs.extend(_DIM_ERRS)
                        made += rres['n']
                        if rres['n']:
                            ok_views += 1
                            notes.append(('{}（旋转井）'.format(v.Name), rres['n']))
                        elif rled.dup:
                            notes.append(('{}（旋转井：已有尺寸，没有重复标）'.format(v.Name), -1))
                        else:
                            notes.append(('{}（旋转井：{}）'.format(v.Name, rres['why'] or '未生成尺寸'), 0))
                        continue
                    # 标注框：水平范围用可见墙面（TELCO 族的包围盒比井身大一倍，用包围盒会把内壁认偏），竖向仍用整个井
                    box = _clip_box(union_box(v, elems), crop_world_range(v))
                    if box is not None:
                        box = _face_box(elems, box)
                    if box is None:
                        notes.append(('{}（无法计算包围盒）'.format(v.Name), 0))
                        continue

                    res = {'cn': 0, 'n': 0, 'why': None}
                    # 先排尺寸链，再排总尺寸：总尺寸要知道各侧最外一道链线的位置
                    led = _fresh_ledger(v)
                    ext = {}

                    def _chains():
                        res['cn'], res['why'] = make_chain_dims(v, elems, box, led, stats, ext)

                    def _totals():
                        res['n'] = make_total_dims(v, elems, box, led, ext)

                    def _both():
                        _chains()
                        _totals()

                    if not _step("标注", _both):
                        # 整体回滚：已回滚的尺寸不在视图里了，判重表与外移量重建后再分开排
                        res['cn'] = 0
                        res['n'] = 0
                        ext = {}
                        led = _fresh_ledger(v)
                        if not _step("排管尺寸链", _chains):
                            # 逐根排管各自提交，只丢弃参照失效的那一根
                            res['cn'] = 0
                            ext = {}
                            led = _fresh_ledger(v)
                            try:
                                frm = _frame(v)
                                all_banks = _find_banks(crop_world_range(v), frm)
                            except Exception:
                                all_banks = []
                            for bk in all_banks:
                                for dr in ('h', 'v'):
                                    sub = {}

                                    def _one(bk=bk, dr=dr, sub=sub):
                                        sub['cn'], _w = make_chain_dims(v, elems, box, led, stats, ext, [bk], dr)
                                    if _step("排管尺寸链", _one):
                                        res['cn'] += sub.get('cn', 0)
                                    else:
                                        failed_steps.append('{}：排管 {} 的{}向尺寸链（Revit 判定参照失效）'.format(v.Name, bk.Id, '水平' if dr == 'h' else '竖'))
                        if not _step("总尺寸", _totals):
                            res['n'] = 0
                            failed_steps.append('{}：总尺寸（Revit 判定参照失效）'.format(v.Name))

                    _bump(stats, _ST_DUP, led.dup)
                    if _OUT_CROP:
                        out_total += len(_OUT_CROP)
                        out_views.append((v.Name, len(_OUT_CROP), sorted(set(_OUT_CROP.values()))[0]))
                    cn = res['cn']
                    n = res['n']
                    made += n + cn
                    dim_errs.extend(_DIM_ERRS)
                    if n + cn:
                        ok_views += 1
                        notes.append(('{}'.format(v.Name), n + cn))
                    elif led.dup:
                        notes.append(('{}（已有尺寸，没有重复标）'.format(v.Name), -1))
                    else:
                        if res['why'] and res['why'] != '视图内无排管（结构框架）':
                            why = res['why']
                        else:
                            why = '未生成尺寸'
                        notes.append(('{}（{}）'.format(v.Name, why), 0))
                except Exception as ev:
                    notes.append(('{}（异常：{}）'.format(v.Name, ev), 0))
            tg.Assimilate()
        except Exception as inner:
            try:
                pb.close()
            except Exception:
                pass
            try:
                tg.RollBack()
            except Exception:
                pass
            forms.alert('标注过程出错：\n' + str(inner) + '\n\n' + traceback.format_exc(),
                        title='一键井大样标注')
            return

        try:
            pb.close()
        except Exception:
            pass

        # ---------- 4. 报告 ----------
        msg = '井大样标注完成！（只新增尺寸：视图的裁剪框、隐藏状态和已有尺寸都没有改动）\n\n'
        if cancelled[0]:
            msg = msg.replace('井大样标注完成！', '井大样标注已取消！只处理了 {} / {} 个视图，已标注的保留（Ctrl+Z 可一次撤销）。'.format(max(done_views[0] - 1, 0), len(target_views)))
        msg += '• 处理视图：{} 个（成功 {} 个）\n'.format(len(target_views), ok_views)
        if skipped_annot[0]:
            msg += '• 已经有尺寸而跳过的视图：{} 个（没有重复标注）' .format(skipped_annot[0]) + chr(10)
        msg += '• 标注排管（井内尺寸链）：{} 根（按视图累计）\n'.format(stats.get(_ST_BANK_OK, 0))
        msg += '• 生成尺寸：{} 道（外包总尺寸 + 尺寸链）\n'.format(made)
        if dup_views:
            msg += '• 重复选择的视图（已合并）：{} 个\n'.format(dup_views)
        if stats.get(_ST_DUP):
            msg += '• 同位置已有或重复的尺寸（已跳过，不重复标）：{} 道\n'.format(stats[_ST_DUP])
        if out_total:
            msg += '• 超出注释裁剪框的尺寸：{} 道（已照常标注；视图没有改动，需要显示时往外拉）\n'.format(out_total)
            for nm, cnt_, gap in out_views[:10]:
                msg += '    - {}：{} 道（{}）\n'.format(nm, cnt_, gap)
            if len(out_views) > 10:
                msg += '    ...（其余 {} 个视图同类）\n'.format(len(out_views) - 10)
        if dim_errs:
            uniq = []
            for m in dim_errs:
                if m not in uniq:
                    uniq.append(m)
            msg += '• 尺寸创建失败：{} 道（Revit 拒绝）：{}\n'.format(len(dim_errs), '；'.join(uniq[:3]))
        if failed_steps:
            msg += '• 未能写入的部分（已跳过，其余照常）：\n'
            for x in failed_steps[:10]:
                msg += '    - {}\n'.format(x)
        skip_txt = _fmt_skips(stats)
        if skip_txt:
            msg += '\n跳过的构件（按视图累计）：\n' + skip_txt
        if _BANK_ERRS:
            uniq_b = []
            for m in _BANK_ERRS:
                if m not in uniq_b:
                    uniq_b.append(m)
            msg += '排管处理异常的原因：{}\n'.format('；'.join(uniq_b[:2]))
        had = [n for n in notes if n[1] < 0]
        if had:
            msg += '\n已经有尺寸、没有重复标的视图 {} 个（不是失败）。\n'.format(len(had))
        bad = [n for n in notes if n[1] == 0]
        if bad:
            msg += '\n未生成尺寸的视图 {} 个：\n'.format(len(bad))
            for nm, _ in bad[:10]:
                msg += '    - {}\n'.format(nm)
            if len(bad) > 10:
                msg += '    ...（其余 {} 个同类）\n'.format(len(bad) - 10)
        msg += '\n设置文件：' + SETTINGS_FILE + '（偏移、间距、尺寸类型、各视图标什么、井类别等都能改）'
        forms.alert(msg, title='一键井大样标注')

    except System.Exception as e:
        try:
            pb0[0].close()
        except Exception:
            pass
        forms.alert('发生严重系统错误:\n' + str(e) + '\n\n' + traceback.format_exc(), title='报错追踪')
    except Exception as e:
        try:
            pb0[0].close()
        except Exception:
            pass
        forms.alert('发生严重Python错误:\n' + str(e) + '\n\n' + traceback.format_exc(), title='报错追踪')


if __name__ == '__main__':
    main()
