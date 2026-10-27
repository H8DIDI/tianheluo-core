# -*- coding: utf-8 -*-
from __future__ import unicode_literals
__title__ = "一键框选\n提量表格"
__author__ = "KAIFA"
__doc__ = ("框选（或整个项目、当前视图）里的梁柱墙基础楼板和钢筋，先在窗口里显示表格：\n"
           "混凝土体积（m³）、钢材重量（t，按材料密度算）、钢筋按直径汇总（根数、总长、重量）。\n"
           "看完可以导出 Excel；也可以在 Revit 里生成明细表（之后模型改了，明细表自动更新）。")

import math
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
from System.Collections.Generic import List
from pyrevit import revit, DB, UI, forms
import System.Windows.Forms as WF
import System.Drawing as SD

doc = revit.doc
uidoc = revit.uidoc

TITLE = u"一键框选提量表格"
FT3_TO_M3 = 0.3048 ** 3
FT_TO_M = 0.3048
FT_TO_MM = 304.8
STEEL_DENSITY = 7850.0      # kg/m³，钢筋统一用这个密度
STEEL_MIN_DENSITY = 6000.0  # 材料密度达到这个值，按钢材算
OUT_DIR = os.path.join(System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools")

QTO_BICS = [
    DB.BuiltInCategory.OST_StructuralColumns,
    DB.BuiltInCategory.OST_StructuralFraming,
    DB.BuiltInCategory.OST_Walls,
    DB.BuiltInCategory.OST_StructuralFoundation,
    DB.BuiltInCategory.OST_Floors,
    DB.BuiltInCategory.OST_Rebar,
]
QTO_CAT_IDS = set(int(b) for b in QTO_BICS)

# 明细表：(表名, 类别, 字段候选名)。字段名按 Revit 里显示的名字匹配，中英文都试。
LEVEL_FIELD = [u'标高', u'基准标高', u'基准约束', u'Level', u'Base Level', u'Base Constraint']
SCHEDULE_SPECS = [
    (u'KAIFA-钢筋明细表', DB.BuiltInCategory.OST_Rebar, [
        [u'钢筋直径', u'Bar Diameter'],
        [u'总钢筋长度', u'Total Bar Length', u'Total Length'],
        [u'数量', u'数量(按钢筋集)', u'Quantity'],
    ]),
    (u'KAIFA-结构柱明细表', DB.BuiltInCategory.OST_StructuralColumns, [
        [u'底部标高', u'Base Level'], [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
    (u'KAIFA-结构框架明细表', DB.BuiltInCategory.OST_StructuralFraming, [
        LEVEL_FIELD, [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
    (u'KAIFA-墙体混凝土明细表', DB.BuiltInCategory.OST_Walls, [
        [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
    (u'KAIFA-结构基础明细表', DB.BuiltInCategory.OST_StructuralFoundation, [
        LEVEL_FIELD, [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
    (u'KAIFA-楼板明细表', DB.BuiltInCategory.OST_Floors, [
        LEVEL_FIELD, [u'类型', u'Type Name'], [u'体积', u'Volume'],
    ]),
]


# @include _shared/form_fit.py


def _u(x):
    if x is None:
        return u''
    return x if isinstance(x, type(u'')) else u'%s' % x


def _eid(x):
    """ElementId 的整数值；取不到返回 -1。"""
    if x is None:
        return -1
    try:
        return int(x.Value)
    except Exception:
        try:
            return int(x.IntegerValue)
        except Exception:
            return -1


def _is_qto(e):
    try:
        return e is not None and e.Category is not None and _eid(e.Category.Id) in QTO_CAT_IDS
    except Exception:
        return False


def _level_name(e):
    """楼层（标高）名。钢筋取所在主体的标高；没有标高的记为「(无标高)」。"""
    try:
        if e.Category is not None and _eid(e.Category.Id) == int(DB.BuiltInCategory.OST_Rebar):
            host = doc.GetElement(e.GetHostId())
            if host is not None:
                e = host
        lid = e.LevelId
        if _eid(lid) > 0:
            lvl = doc.GetElement(lid)
            if lvl is not None:
                return _u(lvl.Name)
    except Exception:
        pass
    return u'(无标高)'


def _type_name(e):
    try:
        t = doc.GetElement(e.GetTypeId())
        if t is not None:
            return _u(t.Name)
    except Exception:
        pass
    return _u(e.Name)


def _volume_m3(e):
    """构件体积（m³）。取不到返回 0。"""
    p = e.get_Parameter(DB.BuiltInParameter.HOST_VOLUME_COMPUTED)
    if p is None or not p.HasValue:
        p = e.LookupParameter(u'Volume')
    if p is not None and p.HasValue:
        return p.AsDouble() * FT3_TO_M3
    return 0.0


def _density(e):
    """材料的密度（kg/m³）；取不到返回 None。"""
    try:
        mid = e.StructuralMaterialId
        if _eid(mid) < 0:
            return None
        mat = doc.GetElement(mid)
        if mat is None:
            return None
        pse = doc.GetElement(mat.StructuralAssetId)
        if pse is None:
            return None
        # Revit 返回的密度单位是 kg/ft³（混凝土约 68），这里统一换算成 kg/m³（约 2400）
        return float(pse.GetStructuralAsset().Density) / FT3_TO_M3
    except Exception:
        return None


def _material_name(e):
    try:
        mid = e.StructuralMaterialId
        if _eid(mid) >= 0:
            mat = doc.GetElement(mid)
            if mat is not None:
                return _u(mat.Name)
    except Exception:
        pass
    return u''


def _pick_elements(sel_ids):
    """要统计的构件：有选中就用选中的；没有就问整个项目还是当前视图。取消返回 (None, None)。"""
    if sel_ids:
        elems = [e for e in (doc.GetElement(i) for i in sel_ids) if _is_qto(e)]
        return elems, u'框选的构件'
    options = [u'整个项目', u'当前视图']
    pick = forms.CommandSwitchWindow.show(options, message=u'没有框选构件，统计哪个范围？')
    if not pick:
        return None, None
    if pick == u'当前视图':
        col = DB.FilteredElementCollector(doc, uidoc.ActiveView.Id)
        src = u'当前视图'
    else:
        col = DB.FilteredElementCollector(doc)
        src = u'整个项目'
    flt = DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](QTO_BICS))
    return list(col.WherePasses(flt).WhereElementIsNotElementType().ToElements()), src


def _quantities(elems):
    """汇总：混凝土（楼层、类别、类型）、钢材（同上，含重量）、钢筋（楼层、直径）。
    构件多时显示进度条（读几何体积较慢），可以取消；取消返回 None（只读，不改模型）。"""
    res = {'conc': {}, 'steel': {}, 'rebar': {}, 'skipped': 0}
    pb = forms.ProgressBar(title=u'正在统计工程量…（点取消可停止）', cancellable=True) if len(elems) > 300 else None
    try:
        for i, e in enumerate(elems):
            if pb is not None and i % 20 == 0:
                pb.update_progress(i, len(elems))
                if pb.cancelled:
                    return None
            try:
                _add_one(e, res)
            except Exception:
                res['skipped'] += 1
    finally:
        if pb is not None:
            try:
                pb.close()
            except Exception:
                pass
    return res


def _add_one(e, res):
    """把一个构件的量累加进汇总表。"""
    if _eid(e.Category.Id) == int(DB.BuiltInCategory.OST_Rebar):
        lvl = _level_name(e)
        t = doc.GetElement(e.GetTypeId())
        d_ft = t.BarNominalDiameter if t is not None else 0.0
        dia = round(d_ft * FT_TO_MM, 1)
        bars = int(e.NumberOfBarPositions) if int(e.NumberOfBarPositions) > 0 else 1
        length_m = e.TotalLength * FT_TO_M
        r_m = d_ft * FT_TO_M / 2.0
        kg = length_m * math.pi * r_m * r_m * STEEL_DENSITY
        row = res['rebar'].setdefault((lvl, dia), [0, 0.0, 0.0])
        row[0] += bars
        row[1] += length_m
        row[2] += kg
        return

    lvl = _level_name(e)
    cat = _u(e.Category.Name)
    typ = _type_name(e)
    vol = _volume_m3(e)
    dens = _density(e)
    mname = _material_name(e)
    is_steel = (dens is not None and dens >= STEEL_MIN_DENSITY) or (
        dens is None and (u'钢' in mname or u'steel' in mname.lower()))
    if is_steel:
        d = dens if (dens is not None and dens >= STEEL_MIN_DENSITY) else STEEL_DENSITY
        row = res['steel'].setdefault((lvl, cat, typ), [0, 0.0, 0.0])
        row[0] += 1
        row[1] += vol
        row[2] += vol * d  # kg，导出时再换算成 t
    else:
        row = res['conc'].setdefault((lvl, cat, typ), [0, 0.0])
        row[0] += 1
        row[1] += vol


# ============================================================
# 表格数据（窗口和 Excel 共用）
# ============================================================
def _table_rows(res):
    """返回三张表：[(标题, 表头, 行, 合计行)]。行和合计行的数字都已经是最终显示的单位。"""
    conc_rows = []
    conc_sum = 0.0
    for (lvl, cat, typ), (n, vol) in sorted(res['conc'].items()):
        conc_rows.append([lvl, cat, typ, n, round(vol, 3)])
        conc_sum += vol
    steel_rows = []
    steel_sum = 0.0
    for (lvl, cat, typ), (n, vol, kg) in sorted(res['steel'].items()):
        steel_rows.append([lvl, cat, typ, n, round(kg / 1000.0, 3)])
        steel_sum += kg / 1000.0
    rebar_rows = []
    rebar_sum = [0, 0.0, 0.0]
    for (lvl, dia), (bars, length_m, kg) in sorted(res['rebar'].items()):
        rebar_rows.append([lvl, dia, bars, round(length_m, 3), round(kg, 3)])
        rebar_sum[0] += bars
        rebar_sum[1] += length_m
        rebar_sum[2] += kg
    tables = [
        (u'混凝土', [u'楼层', u'构件类别', u'类型', u'数量', u'体积(m³)'], conc_rows,
         [u'合计', u'', u'', sum(r[3] for r in conc_rows), round(conc_sum, 3)]),
        (u'钢材', [u'楼层', u'构件类别', u'类型', u'数量', u'重量(t)'], steel_rows,
         [u'合计', u'', u'', sum(r[3] for r in steel_rows), round(steel_sum, 3)]),
        (u'钢筋', [u'楼层', u'直径(mm)', u'根数', u'总长(m)', u'重量(kg)'], rebar_rows,
         [u'合计', u'', rebar_sum[0], round(rebar_sum[1], 3), round(rebar_sum[2], 3)]),
    ]
    summary = {'conc': conc_sum, 'steel': steel_sum, 'rebar': rebar_sum}
    return tables, summary


# ============================================================
# Excel（Excel 2003 XML，Excel 可直接打开）
# ============================================================
def _xml_escape(s):
    s = _u(s)
    return s.replace(u'&', u'&amp;').replace(u'<', u'&lt;').replace(u'>', u'&gt;').replace(u'"', u'&quot;')


def _cell(v, style=None):
    sa = (u' ss:StyleID="%s"' % style) if style else u''
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, (int, float)):
        num = (u'%.3f' % v) if isinstance(v, float) else (u'%d' % v)
        return u'<Cell%s><Data ss:Type="Number">%s</Data></Cell>' % (sa, num)
    return u'<Cell%s><Data ss:Type="String">%s</Data></Cell>' % (sa, _xml_escape(v))


def _sheet(name, header, rows, total):
    x = [u'<Worksheet ss:Name="%s"><Table>' % _xml_escape(name)]
    x.append(u'<Row>' + u''.join(_cell(h, u'h') for h in header) + u'</Row>')
    for r in rows:
        x.append(u'<Row>' + u''.join(_cell(v) for v in r) + u'</Row>')
    x.append(u'<Row>' + u''.join(_cell(v, u'h') for v in total) + u'</Row>')
    x.append(u'</Table></Worksheet>')
    return u''.join(x)


def _build_xml(tables):
    head = (u'<?xml version="1.0" encoding="UTF-8"?>\n'
            u'<?mso-application progid="Excel.Sheet"?>\n'
            u'<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" '
            u'xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">\n'
            u'<Styles><Style ss:ID="h"><Font ss:Bold="1"/>'
            u'<Interior ss:Color="#D9E1F2" ss:Pattern="Solid"/></Style></Styles>\n')
    body = [_sheet(name, header, rows, total) for name, header, rows, total in tables]
    return head + u'\n'.join(body) + u'\n</Workbook>\n'


def _write_xlsx(tables, path):
    """把三张表写到用户选定的路径（不自动保存到别处）。"""
    with io.open(path, 'w', encoding='utf-8-sig') as f:
        f.write(_build_xml(tables))
    return path


def _ask_save_path():
    """弹出「另存为」对话框让用户选路径。取消返回 None。"""
    dlg = WF.SaveFileDialog()
    dlg.Title = u'导出提量表格（Excel 可直接打开）'
    dlg.Filter = u'Excel 表格 (*.xml)|*.xml'
    dlg.DefaultExt = u'xml'
    dlg.AddExtension = True
    dlg.FileName = u'提量_%s.xml' % time.strftime('%Y%m%d_%H%M')
    dlg.InitialDirectory = System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments)
    if dlg.ShowDialog() == WF.DialogResult.OK:
        return dlg.FileName
    return None


# ============================================================
# 明细表（Revit 里的明细表，随模型自动更新）
# ============================================================
def _existing_schedule_names():
    names = set()
    for s in DB.FilteredElementCollector(doc).OfClass(DB.ViewSchedule).ToElements():
        try:
            names.add(_u(s.Name))
        except Exception:
            pass
    return names


def _has_elements(bic):
    try:
        return DB.FilteredElementCollector(doc).OfCategory(bic).WhereElementIsNotElementType().GetElementCount() > 0
    except Exception:
        return False


def _find_field(defn, names):
    """按候选名的先后顺序找字段（前面的优先）。"""
    by_name = {}
    for sf in defn.GetSchedulableFields():
        try:
            by_name.setdefault(_u(sf.GetName(doc)), sf)
        except Exception:
            continue
    for n in names:
        if n in by_name:
            return by_name[n]
    return None


def _make_schedules():
    """按 SCHEDULE_SPECS 建明细表（已经有同名的跳过）。返回 (新建的表名, 跳过的表名, 缺的字段说明)。"""
    existing = _existing_schedule_names()
    created = []
    skipped = []
    missing = []
    t = DB.Transaction(doc, u'生成提量明细表')
    t.Start()
    try:
        for name, bic, field_names in SCHEDULE_SPECS:
            if name in existing:
                skipped.append(name)
                continue
            if not _has_elements(bic):
                continue
            sch = DB.ViewSchedule.CreateSchedule(doc, DB.ElementId(bic))
            sch.Name = name
            defn = sch.Definition
            for names in field_names:
                sf = _find_field(defn, names)
                if sf is None:
                    missing.append(u'%s：找不到字段 %s' % (name, u'/'.join(names)))
                    continue
                defn.AddField(sf)
            created.append(name)
        t.Commit()
    except Exception as ex:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise ex
    return created, skipped, missing


# ============================================================
# 表格窗口
# ============================================================
def _grid(headers, rows, total):
    g = WF.DataGridView()
    g.Dock = WF.DockStyle.Fill
    g.ReadOnly = True
    g.AllowUserToAddRows = False
    g.RowHeadersVisible = False
    g.AutoSizeColumnsMode = WF.DataGridViewAutoSizeColumnsMode.AllCells
    for i, h in enumerate(headers):
        g.Columns.Add(u'c%d' % i, _u(h))
    for r in rows + [total]:
        idx = g.Rows.Add()
        for i, v in enumerate(r):
            g.Rows[idx].Cells[i].Value = v if not isinstance(v, str) else _u(v)
    if total:
        g.Rows[g.Rows.Count - 1].DefaultCellStyle.Font = SD.Font(g.Font, SD.FontStyle.Bold)
    return g


def _show_window(tables, summary, src_txt, res):
    form = WF.Form()
    form.Text = u'%s — %s' % (TITLE, src_txt)
    form.Width = 920
    form.Height = 580
    form.StartPosition = WF.FormStartPosition.CenterScreen

    tabs = WF.TabControl()
    tabs.Dock = WF.DockStyle.Fill
    for title, headers, rows, total in tables:
        page = WF.TabPage(title)
        page.Controls.Add(_grid(headers, rows, total))
        tabs.TabPages.Add(page)

    bottom = WF.Panel()
    bottom.Dock = WF.DockStyle.Bottom
    bottom.Height = 48

    def _button(text, left, handler):
        b = WF.Button()
        b.Text = text
        b.Left = left
        b.Top = 9
        b.Width = 150
        b.Height = 30
        b.Click += System.EventHandler(handler)
        bottom.Controls.Add(b)
        return b

    msg = []

    def _on_export(sender, args):
        try:
            path = _ask_save_path()
            if not path:
                return
            _write_xlsx(tables, path)
            forms.alert(u'已导出：\n' + path, title=TITLE)
        except Exception as ex:
            forms.alert(u'导出失败：\n%s' % ex, title=TITLE)

    def _on_schedule(sender, args):
        try:
            created, skipped, missing = _make_schedules()
            lines = []
            if created:
                lines.append(u'已新建明细表：\n  ' + u'\n  '.join(created))
            if skipped:
                lines.append(u'已存在（自动更新，不重复建）：\n  ' + u'\n  '.join(skipped))
            if not created and not skipped:
                lines.append(u'没有可建的明细表（对应类别里没有构件）。')
            if missing:
                lines.append(u'有字段没找到，已跳过：\n  ' + u'\n  '.join(missing))
            forms.alert(u'\n'.join(lines), title=TITLE)
        except Exception as ex:
            forms.alert(u'生成明细表失败，已回滚：\n%s' % ex, title=TITLE)

    _button(u'导出 Excel', 12, _on_export)
    _button(u'生成/更新明细表', 172, _on_schedule)
    _button(u'关闭', 332, lambda s, a: form.Close())

    form.Controls.Add(tabs)
    form.Controls.Add(bottom)
    ff_fit(form).ShowDialog()


def main():
    try:
        sel_ids = list(uidoc.Selection.GetElementIds())
        elems, src_txt = _pick_elements(sel_ids)
        if elems is None:
            return
        if not elems:
            forms.alert(u"没有找到可统计的构件（梁、柱、墙、基础、楼板、钢筋）。", title=TITLE)
            return
        res = _quantities(elems)
        if res is None:
            return          # 用户取消了统计（只读，没有改模型）
        tables, summary = _table_rows(res)
        if res['skipped']:
            forms.alert(u'有 %d 个构件读取失败，已跳过。' % res['skipped'], title=TITLE)
        _show_window(tables, summary, u'%s（%d 个构件）' % (src_txt, len(elems)), res)
    except Exception as e:
        forms.alert(u'提量过程发生异常：\n%s\n\n%s' % (e, traceback.format_exc()), title=TITLE)


if __name__ == '__main__':
    main()
