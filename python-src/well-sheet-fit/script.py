# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = '井图比例\n排版'
__author__ = 'didi'
__doc__ = ('给已经出好的井大样图纸（平面 + 南北东西四个立面）按「放得下的最大比例」重新定比例并重排视图位置：\n'
           '每张图纸从候选比例（默认 1:25、30、35、40、45）里取放得下的最大一档，视图组居中放在图框可用区域里，平面居中、北在上、南在下、东西在左右，'
           '间距沿用现有间距（不小于设定的最小间距）。\n'
           '先预览每张图纸「旧比例 → 新比例」和占用大小，确认后再应用；整次可 Ctrl+Z 撤销。只改视图比例和视口位置，不动视图内容、尺寸标注。\n'
           '候选比例、可用区域、整组中心、最小间距都在「设置…」里改（文档\\KaifaTools\\井图排版设置.json）。')

import io
import os
import re
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
import json

# @include _shared/settings_ui.py

doc = revit.doc
uidoc = revit.uidoc
TITLE = '井图比例排版'
FT = 304.8
CFG_FILE = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), 'KaifaTools', '井图排版设置.json')
KINDS = ['plan', 'south', 'north', 'west', 'east']

DEFAULTS = {
    '说明': ('井图比例排版的设置（长度单位 mm，坐标是图纸上的位置，原点在图纸左下角附近）。scales=候选比例分母，从大图到小图排，每张图纸取放得下的第一档；'
            'region_*=图框里可以放视图组的区域（已经留出了尺寸标注的余量）；center_*=整组优先居中的位置（右边有信息栏，所以偏左），'
            '组放不下居中位置时会在可用区域里平移；min_gap_*=视图之间最小间距；view_names=各视图的名字前缀；well_view_tag=井大样视图名里的标记；sheet_filter=只处理图名里包含这段文字的图纸（空=全部）。'),
    'scales': [25, 30, 35, 40, 45],
    'region_x0_mm': 30.0, 'region_x1_mm': 715.0,
    'region_y0_mm': 30.0, 'region_y1_mm': 565.0,
    'center_x_mm': 344.0, 'center_y_mm': 296.0,
    'min_gap_x_mm': 60.0, 'min_gap_y_mm': 30.0,
    'view_names': {'plan': '平面', 'south': '南', 'north': '北', 'west': '西', 'east': '东'},
    'well_view_tag': '_AG-',
    'sheet_filter': '',
}


# ============================================================
# 设置
# ============================================================

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
    """读设置；文件不存在或是空的就写一份默认的。某一项缺了用默认值。"""
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
    try:
        c['scales'] = sorted(set(int(x) for x in c['scales']))
        if not c['scales']:
            raise ValueError()
    except Exception:
        c['scales'] = list(DEFAULTS['scales'])
    return c


def settings_fields():
    return [
        {'kind': 'header', 'label': '处理哪些图纸'},
        {'key': 'sheet_filter', 'label': '只处理图名包含这段文字的图纸', 'kind': 'text',
         'hint': '例如 BTP-IF1，就只处理这类图名的图纸（雨水井等老图纸不动）。留空 = 全部井大样图纸。'},
        {'kind': 'header', 'label': '比例'},
        {'key': 'scales', 'label': '候选比例（分母，逗号隔开）', 'kind': 'list',
         'hint': '例如 25, 30, 35, 40, 45。每张图纸取放得下的最小分母（也就是最大的图）。'},
        {'kind': 'header', 'label': '图框里可以放视图的区域（mm）'},
        {'key': 'region_x0_mm', 'label': '区域左边', 'kind': 'num', 'min': 0, 'max': 2000},
        {'key': 'region_x1_mm', 'label': '区域右边', 'kind': 'num', 'min': 0, 'max': 2000,
         'hint': 'A1 图框（841×594）右边有信息栏，默认到 715 mm，已经留出尺寸标注的余量。'},
        {'key': 'region_y0_mm', 'label': '区域下边', 'kind': 'num', 'min': -100, 'max': 2000},
        {'key': 'region_y1_mm', 'label': '区域上边', 'kind': 'num', 'min': 0, 'max': 2000},
        {'kind': 'header', 'label': '整组位置（mm）'},
        {'key': 'center_x_mm', 'label': '整组优先居中的位置 X', 'kind': 'num', 'min': 0, 'max': 2000,
         'hint': '右边有信息栏，所以默认偏左（344）。放不下时会在可用区域里平移。'},
        {'key': 'center_y_mm', 'label': '整组优先居中的位置 Y', 'kind': 'num', 'min': 0, 'max': 2000},
        {'key': 'min_gap_x_mm', 'label': '平面与东西视图的最小间距', 'kind': 'num', 'min': 0, 'max': 500,
         'hint': '间距沿用现有版面，但不小于这个值。'},
        {'key': 'min_gap_y_mm', 'label': '平面与南北视图的最小间距', 'kind': 'num', 'min': 0, 'max': 500},
    ]


def open_settings():
    c = load_cfg()
    res = su_fields_dialog('井图比例排版 设置', '改完点保存立即生效。「恢复默认」只改窗口里的值，点保存才生效。',
                           settings_fields(), c, DEFAULTS,
                           extra_buttons=[('用记事本打开文件', lambda: su_open_file(CFG_FILE))])
    if res is None:
        return False
    out = json.loads(json.dumps(c))
    for k, v in res.items():
        if k == 'scales':
            try:
                v = sorted(set(int(float(x)) for x in v))
            except Exception:
                v = c['scales']
            if not v or any(x <= 0 for x in v):
                v = c['scales']
        out[k] = v
    save_cfg(out)
    return True


# ============================================================
# 纯逻辑：版面求解（可离线测试）
# ============================================================

def _ctr(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def layout_params(boxes, cfg):
    """从现有版面取出：谁在上下左右、各间距、各视图相对平面中心的偏移。boxes: kind -> [x0,y0,x1,y1] (mm)。"""
    P = boxes['plan']
    pc = _ctr(P)
    top, bot = ('north', 'south') if _ctr(boxes['north'])[1] >= _ctr(boxes['south'])[1] else ('south', 'north')
    left, right = ('west', 'east') if _ctr(boxes['west'])[0] <= _ctr(boxes['east'])[0] else ('east', 'west')
    return {
        'top': top, 'bot': bot, 'left': left, 'right': right,
        'gx_l': max(P[0] - boxes[left][2], cfg['min_gap_x_mm']),
        'gx_r': max(boxes[right][0] - P[2], cfg['min_gap_x_mm']),
        'gy_t': max(boxes[top][1] - P[3], cfg['min_gap_y_mm']),
        'gy_b': max(P[1] - boxes[bot][3], cfg['min_gap_y_mm']),
        'off': dict((k, (_ctr(boxes[k])[0] - pc[0], _ctr(boxes[k])[1] - pc[1])) for k in boxes),
    }


def place(sizes, prm, cfg, scale_ratio=None):
    """按给定的各视图大小 sizes: kind -> (w, h)（mm）摆放。scale_ratio: kind -> 偏移要乘的比例（旧比例/新比例），None = 1。
    返回 (各视图中心 dict, 整组宽, 整组高, 是否放得下)。"""
    r = scale_ratio or dict((k, 1.0) for k in sizes)
    w = dict((k, sizes[k][0]) for k in sizes)
    h = dict((k, sizes[k][1]) for k in sizes)
    L, R, T, B = prm['left'], prm['right'], prm['top'], prm['bot']
    c = {'plan': (0.0, 0.0)}
    c[L] = (-(w['plan'] / 2.0 + prm['gx_l'] + w[L] / 2.0), prm['off'][L][1] * r[L])
    c[R] = (w['plan'] / 2.0 + prm['gx_r'] + w[R] / 2.0, prm['off'][R][1] * r[R])
    c[T] = (prm['off'][T][0] * r[T], h['plan'] / 2.0 + prm['gy_t'] + h[T] / 2.0)
    c[B] = (prm['off'][B][0] * r[B], -(h['plan'] / 2.0 + prm['gy_b'] + h[B] / 2.0))
    x0 = min(c[k][0] - w[k] / 2.0 for k in c)
    x1 = max(c[k][0] + w[k] / 2.0 for k in c)
    y0 = min(c[k][1] - h[k] / 2.0 for k in c)
    y1 = max(c[k][1] + h[k] / 2.0 for k in c)
    gw, gh = x1 - x0, y1 - y0
    rw = cfg['region_x1_mm'] - cfg['region_x0_mm']
    rh = cfg['region_y1_mm'] - cfg['region_y0_mm']
    fits = gw <= rw + 1e-6 and gh <= rh + 1e-6
    gcx = min(max(cfg['center_x_mm'], cfg['region_x0_mm'] + gw / 2.0), cfg['region_x1_mm'] - gw / 2.0)
    gcy = min(max(cfg['center_y_mm'], cfg['region_y0_mm'] + gh / 2.0), cfg['region_y1_mm'] - gh / 2.0)
    dx = gcx - (x0 + x1) / 2.0
    dy = gcy - (y0 + y1) / 2.0
    return dict((k, (c[k][0] + dx, c[k][1] + dy)) for k in c), gw, gh, fits


def solve(boxes, olds, cfg):
    """候选比例从大图到小图试，返回第一个放得下的 (新比例, 各视图中心, 组宽, 组高, 版面参数)；都放不下返回 None。
    boxes: kind -> [x0,y0,x1,y1] mm；olds: kind -> 旧比例分母。视图在图纸上的大小与比例分母成反比。"""
    prm = layout_params(boxes, cfg)
    for s in cfg['scales']:
        ratio = dict((k, olds[k] / float(s)) for k in boxes)
        sizes = dict((k, ((boxes[k][2] - boxes[k][0]) * ratio[k], (boxes[k][3] - boxes[k][1]) * ratio[k])) for k in boxes)
        centers, gw, gh, fits = place(sizes, prm, cfg, ratio)
        if fits:
            return s, centers, gw, gh, prm
    return None


# ============================================================
# Revit：读、预览、应用
# ============================================================

def _crop_rect_mm(vp):
    """视图裁剪框在图纸上的矩形 [x0, y0, x1, y1]（mm）。视图坐标 / 比例 + 视口变换的平移 = 图纸坐标。"""
    v = doc.GetElement(vp.ViewId)
    cb = v.CropBox
    T = vp.GetProjectionToSheetTransform()
    sc = float(v.Scale)
    xs = [T.Origin.X + cb.Min.X / sc, T.Origin.X + cb.Max.X / sc]
    ys = [T.Origin.Y + cb.Min.Y / sc, T.Origin.Y + cb.Max.Y / sc]
    return [min(xs) * FT, min(ys) * FT, max(xs) * FT, max(ys) * FT]


def _content_rect_mm(vp):
    """视口里真正有用的内容矩形（mm）= 裁剪框 ∪ 视图里的尺寸、文字、标记。
    不用 Revit 给的视口外框：视图里若有远处的相机图标之类，外框会被撑到几米，视图被挤出图纸。"""
    v = doc.GetElement(vp.ViewId)
    rect = _crop_rect_mm(vp)
    T = vp.GetProjectionToSheetTransform()
    sc = float(v.Scale)
    o = v.Origin
    r = v.RightDirection
    u = v.UpDirection
    for cls in (DB.Dimension, DB.TextNote, DB.IndependentTag):
        try:
            col = DB.FilteredElementCollector(doc, v.Id).OfClass(cls).ToElements()
        except Exception:
            continue
        for e in col:
            try:
                bb = e.get_BoundingBox(v)
                if bb is None:
                    continue
                for x in (bb.Min.X, bb.Max.X):
                    for y in (bb.Min.Y, bb.Max.Y):
                        for z in (bb.Min.Z, bb.Max.Z):
                            dx, dy, dz = x - o.X, y - o.Y, z - o.Z
                            vx = dx * r.X + dy * r.Y + dz * r.Z
                            vy = dx * u.X + dy * u.Y + dz * u.Z
                            sx = (T.Origin.X + vx / sc) * FT
                            sy = (T.Origin.Y + vy / sc) * FT
                            rect = [min(rect[0], sx), min(rect[1], sy), max(rect[2], sx), max(rect[3], sy)]
            except Exception:
                continue
    return rect


def _box_info(vp):
    """返回 (有效框 mm, 偏移 mm)。有效框 = 视口里真正有用的内容（裁剪框 + 尺寸文字标记）；
    偏移 = 有效框中心 − 视口外框中心，设位置时要减掉它（Revit 的 SetBoxCenter 认的是外框中心）。"""
    ol = vp.GetBoxOutline()
    box = [ol.MinimumPoint.X * FT, ol.MinimumPoint.Y * FT, ol.MaximumPoint.X * FT, ol.MaximumPoint.Y * FT]
    try:
        eff = _content_rect_mm(vp)
    except Exception:
        eff = box
    off = ((eff[0] + eff[2]) / 2.0 - (box[0] + box[2]) / 2.0, (eff[1] + eff[3]) / 2.0 - (box[1] + box[3]) / 2.0)
    return eff, off


def _box_mm(vp):
    return _box_info(vp)[0]


def collect_sheets(cfg):
    """一次扫出全模型的视口，按图纸归类，只留「平面 + 南北东西各一个、视图名带井大样标记」的图纸。"""
    names = cfg['view_names']
    tag = cfg['well_view_tag']
    sheets = {}
    flt = (cfg.get('sheet_filter') or '').strip()
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet).ToElements():
        if flt and flt not in (s.Name or ''):
            continue
        sheets[int(s.Id.Value)] = s
    got = {}
    for vp in DB.FilteredElementCollector(doc).OfClass(DB.Viewport).ToElements():
        sid = int(vp.SheetId.Value)
        if sid not in sheets:
            continue
        v = doc.GetElement(vp.ViewId)
        if v is None or v.IsTemplate:
            continue
        nm = v.Name or ''
        if tag and tag not in nm:
            continue
        kind = None
        for k in KINDS:
            if nm.startswith(names[k] + '_'):
                kind = k
        if kind is None:
            continue
        got.setdefault(sid, {}).setdefault(kind, []).append((vp, v))
    out = []
    for sid, kinds in got.items():
        if all(k in kinds and len(kinds[k]) == 1 for k in KINDS):
            out.append({'sheet': sheets[sid], 'vps': dict((k, kinds[k][0]) for k in KINDS)})
    out.sort(key=lambda r: r['sheet'].SheetNumber or '')
    return out


def make_plan(cfg, progress=None):
    """读出每张图纸 5 个视口的实际框（含框外的尺寸标注）并求解。Revit 第一次算视口框很慢（每个约 0.1 秒，算过有缓存），
    所以带进度回调 progress(i, n)；返回 True 表示用户取消，此时 make_plan 返回 None。"""
    rows = []
    found = collect_sheets(cfg)
    for _i, r in enumerate(found):
        if progress is not None and progress(_i, len(found)):
            return None
        boxes = dict((k, _box_mm(r['vps'][k][0])) for k in KINDS)
        olds = dict((k, int(r['vps'][k][1].Scale)) for k in KINDS)
        sol = solve(boxes, olds, cfg)
        row = {'sheet': r['sheet'], 'vps': r['vps'], 'boxes': boxes, 'olds': olds, 'new': None, 'centers': None, 'size': None, 'prm': None}
        if sol is not None:
            s, centers, gw, gh, prm = sol
            row.update({'new': s, 'centers': centers, 'size': (gw, gh), 'prm': prm})
        row['same'] = (row['new'] is not None and all(olds[k] == row['new'] for k in KINDS))
        rows.append(row)
    return rows


def summarize(rows):
    from collections import Counter
    c = Counter()
    for r in rows:
        c[r['new'] if r['new'] is not None else 0] += 1
    return c


def apply_plan(rows, cfg, progress=None):
    """应用：按批提交。每张图纸先改各视图比例、刷新，再按实际大小重新摆位置。单张失败时该张还原。返回 (改了几张, 失败列表, 是否被取消)。"""
    todo = [r for r in rows if r['new'] is not None and not r['same']]
    tg = DB.TransactionGroup(doc, '井图比例排版')
    tg.Start()
    done = 0
    fails = []
    cancelled = False
    BATCH = 10
    try:
        for b0 in range(0, len(todo), BATCH):
            if progress is not None:
                if progress(b0, len(todo)):
                    cancelled = True
                    break
            t = DB.Transaction(doc, '比例与版面')
            t.Start()
            try:
                for r in todo[b0:b0 + BATCH]:
                    old_scales = dict((k, int(r['vps'][k][1].Scale)) for k in KINDS)
                    old_centers = dict((k, r['vps'][k][0].GetBoxCenter()) for k in KINDS)
                    try:
                        for k in KINDS:
                            v = r['vps'][k][1]
                            if int(v.Scale) != r['new']:
                                v.Scale = r['new']
                        doc.Regenerate()
                        sizes = {}
                        offs = {}
                        for k in KINDS:
                            bx, offs[k] = _box_info(r['vps'][k][0])
                            sizes[k] = (bx[2] - bx[0], bx[3] - bx[1])
                        centers, gw, gh, fits = place(sizes, r['prm'], cfg, dict((k, r['olds'][k] / float(r['new'])) for k in KINDS))
                        for k in KINDS:
                            cx, cy = centers[k]
                            r['vps'][k][0].SetBoxCenter(DB.XYZ((cx - offs[k][0]) / FT, (cy - offs[k][1]) / FT, 0))
                        done += 1
                    except Exception as ex:
                        fails.append((r['sheet'].SheetNumber, str(ex)))
                        try:
                            for k in KINDS:
                                r['vps'][k][1].Scale = old_scales[k]
                                r['vps'][k][0].SetBoxCenter(old_centers[k])
                        except Exception:
                            pass
                t.Commit()
            except Exception:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise
        tg.Assimilate()
    except Exception:
        try:
            tg.RollBack()
        except Exception:
            pass
        raise
    return done, fails, cancelled


def export_list(rows):
    d = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), 'KaifaTools')
    if not os.path.isdir(d):
        os.makedirs(d)
    path = os.path.join(d, '图纸比例清单_{}.csv'.format(time.strftime('%Y%m%d_%H%M%S')))
    lines = ['图号,图名,旧比例,新比例,新版面宽mm,新版面高mm']
    for r in rows:
        olds = sorted(set(r['olds'].values()))
        lines.append('{},"{}",{},{},{},{}'.format(
            r['sheet'].SheetNumber, (r['sheet'].Name or '').replace('"', '""'), '/'.join('1:%d' % x for x in olds),
            ('1:%d' % r['new']) if r['new'] else '放不下，不改', int(r['size'][0]) if r['size'] else '', int(r['size'][1]) if r['size'] else ''))
    with io.open(path, 'w', encoding='utf-8-sig') as f:
        f.write('\n'.join(lines) + '\n')
    return path


# ============================================================
# 窗口
# ============================================================

def show_dialog(rows, cfg):
    """预览窗口。返回 'apply' / 'settings' / 'close'。"""
    f = WF.Form()
    f.Text = TITLE
    f.Font = SD.Font('Microsoft YaHei UI', 9.0)
    f.StartPosition = WF.FormStartPosition.CenterScreen
    f.FormBorderStyle = WF.FormBorderStyle.FixedDialog
    f.MaximizeBox = False
    f.MinimizeBox = False
    f.ClientSize = SD.Size(780, 620)
    res = {'act': 'close'}

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

    cnt = summarize(rows)
    change = [r for r in rows if r['new'] is not None and not r['same']]
    nofit = [r for r in rows if r['new'] is None]
    same = [r for r in rows if r['same']]
    lab('井大样图纸 {} 张（平面 + 南北东西各 1 个视图）'.format(len(rows)), 14, 10, 740, bold=True)
    parts = ['1:{}：{} 张'.format(s, cnt[s]) for s in sorted(cnt) if s]
    lab('按「放得下的最大比例」：' + '；'.join(parts) + ('；放不下 {} 张（不改）'.format(cnt.get(0, 0)) if cnt.get(0) else ''), 14, 34, 752, 36)
    lab('要改比例或位置 {} 张，本来就是这个比例 {} 张。图框可用区域 {:.0f}×{:.0f} mm，候选比例 {}。'.format(
        len(change), len(same), cfg['region_x1_mm'] - cfg['region_x0_mm'], cfg['region_y1_mm'] - cfg['region_y0_mm'],
        ', '.join('1:%d' % s for s in cfg['scales'])), 14, 72, 752, 36, gray=True)

    lv = WF.ListView()
    lv.View = WF.View.Details
    lv.FullRowSelect = True
    lv.GridLines = True
    lv.Location = SD.Point(14, 112)
    lv.Size = SD.Size(752, 440)
    for h, wd in (('图号', 70), ('图名', 300), ('旧比例', 110), ('新比例', 80), ('版面 宽×高 (mm)', 150)):
        lv.Columns.Add(h, wd)
    for r in rows:
        olds = sorted(set(r['olds'].values()))
        it = WF.ListViewItem(r['sheet'].SheetNumber or '')
        it.SubItems.Add(r['sheet'].Name or '')
        it.SubItems.Add('/'.join('1:%d' % x for x in olds) + ('（混着）' if len(olds) > 1 else ''))
        it.SubItems.Add(('1:%d' % r['new']) if r['new'] else '放不下')
        it.SubItems.Add('{:.0f} × {:.0f}'.format(r['size'][0], r['size'][1]) if r['size'] else '')
        if r['new'] is None:
            it.ForeColor = SD.Color.FromArgb(190, 60, 20)
        elif r['same']:
            it.ForeColor = SD.Color.FromArgb(130, 130, 130)
        else:
            it.ForeColor = SD.Color.FromArgb(0, 90, 170)
        lv.Items.Add(it)
    f.Controls.Add(lv)
    lab('蓝色 = 会改；灰色 = 本来就对；红色 = 最小的比例也放不下，不动。只改视图比例和视口位置，尺寸标注跟着视图走。', 14, 556, 752, gray=True)

    def btn(text, x, w, cb, enabled=True):
        b = WF.Button()
        b.Text = text
        b.Location = SD.Point(x, 584)
        b.Size = SD.Size(w, 30)
        b.Enabled = enabled
        f.Controls.Add(b)
        b.Click += cb
        return b

    def on_apply(s, e):
        msg = '将改 {} 张图纸的视图比例并重排位置（只改比例和位置，不改视图内容和尺寸）。\n整次可 Ctrl+Z 撤销，应用期间有进度条可以取消。\n\n确定吗？'.format(len(change))
        if WF.MessageBox.Show(msg, TITLE, WF.MessageBoxButtons.OKCancel, WF.MessageBoxIcon.Question) == WF.DialogResult.OK:
            res['act'] = 'apply'
            f.Close()

    def on_exp(s, e):
        WF.MessageBox.Show('已导出：\n' + export_list(rows), TITLE)

    def on_set(s, e):
        if open_settings():
            res['act'] = 'settings'
            f.Close()

    btn('应用', 14, 100, on_apply, len(change) > 0)
    btn('导出清单', 124, 100, on_exp)
    btn('设置…', 234, 100, on_set)
    btn('关闭', 666, 100, lambda s, e: f.Close())
    f.ShowDialog()
    return res['act']


def main():
    pb0 = None
    try:
        cfg = load_cfg()
        try:
            pb0 = forms.ProgressBar(title='井图比例排版：正在读取每张图纸的视口大小…（第一次要几分钟，点取消退出）', cancellable=True)
        except Exception:
            pb0 = None
        def _plan_prog(i, n):
            try:
                if pb0 is not None:
                    pb0.update_progress(i, n)
                    return bool(pb0.cancelled)
            except Exception:
                pass
            return False
        rows = make_plan(cfg, _plan_prog)
        cancelled0 = rows is None
        try:
            if pb0 is not None:
                cancelled0 = bool(pb0.cancelled)
                pb0.close()
                pb0 = None
        except Exception:
            pass
        if cancelled0:
            return
        if rows is None:
            return
        if not rows:
            forms.alert('项目里没有可排版的井大样图纸（需要每张图纸有 平面 + 南北东西各 1 个视图，视图名带 {}）。'.format(cfg['well_view_tag']), title=TITLE)
            return
        act = show_dialog(rows, cfg)
        while act == 'settings':
            cfg = load_cfg()
            rows = make_plan(cfg) or []
            act = show_dialog(rows, cfg)
        if act != 'apply':
            return
        pb = forms.ProgressBar(title='正在改比例并重排…（点取消可随时停止，已改的保留）', cancellable=True)

        def prog(i, n):
            try:
                pb.update_progress(i, n)
            except Exception:
                pass
            return bool(pb.cancelled)
        try:
            done, fails, cancelled = apply_plan(rows, cfg, prog)
        finally:
            try:
                pb.close()
            except Exception:
                pass
        msg = ('已取消：' if cancelled else '完成：') + '改了 {} 张图纸的比例和位置。'.format(done)
        if fails:
            msg += '\n{} 张失败（已还原）：{}'.format(len(fails), '；'.join('{} {}'.format(a, b[:40]) for a, b in fails[:5]))
        msg += '\n不满意可以 Ctrl+Z 撤销。'
        forms.alert(msg, title=TITLE)
    except Exception as ex:
        try:
            if pb0 is not None:
                pb0.close()
        except Exception:
            pass
        forms.alert('发生错误：\n{}\n\n{}'.format(ex, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
