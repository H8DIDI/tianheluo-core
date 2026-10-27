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

# @include _shared/sheet_blocks.py
# @include _shared/settings_ui.py

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
    plan['blocked'] = _check_ownership(plan)
    return plan


def _check_ownership(plan):
    """协同（工作共享）模型里，要改号的图纸若被别人占用，整批改号会半路失败。返回 True = 有人占用，不能应用；
    同时在 plan['warnings'] 里写明是谁占着哪几张。"""
    if not doc.IsWorkshared:
        return False
    owned = {}
    for ch in plan['changes']:
        sh = plan['rows'][ch[0]]['sheet']
        try:
            if DB.WorksharingUtils.GetCheckoutStatus(doc, sh.Id) == DB.CheckoutStatus.OwnedByOtherUser:
                who = DB.WorksharingUtils.GetWorksharingTooltipInfo(doc, sh.Id).Owner
                owned.setdefault(who, []).append(ch[4])
        except Exception:
            continue
    for who, marks in owned.items():
        plan['warnings'].append('{} 占用着 {} 张要改号的图纸（如 {}），现在不能重编。请让对方同步并释放后再来。'.format(who, len(marks), marks[0]))
    return bool(owned)


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
    total = 2 * len(changes)
    pb = forms.ProgressBar(title='正在重编图号…（点取消可停止，全部撤回）', cancellable=True) if len(changes) > 30 else None

    def tick(i):
        if pb is not None and i % 10 == 0:
            pb.update_progress(i, total)
            if pb.cancelled:
                raise Exception('已取消，图号没有任何改动。')
    try:
        t1 = DB.Transaction(doc, '临时图号')
        cur[0] = t1
        t1.Start()
        for i, (key, old, new, sysname, mark) in enumerate(changes):
            tick(i)
            plan['rows'][key]['sheet'].SheetNumber = 'TMP{}'.format(key)      # 图号里不能有 { } [ ] | ; < > ? ` ~
        t1.Commit()
        t2 = DB.Transaction(doc, '正式图号')
        cur[0] = t2
        t2.Start()
        for i, (key, old, new, sysname, mark) in enumerate(changes):
            tick(len(changes) + i)
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
    finally:
        if pb is not None:
            try:
                pb.close()
            except Exception:
                pass


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
    t_try.Text = 'BTP-IF1 Telco-T-022 Detail Drawing'
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
    import re as _re_
    n_suffix = len([1 for c in plan['changes'] if _re_.search(r'\s\(\d+\)\s*$', c[1] or '')])
    lab('井大样图纸 {} 张：图号要改 {} 张（其中 {} 张带「(1)」后缀，是撞号造成的，重编后会消失），本来就对 {} 张；不在号段规则里不动 {} 张。'.format(n_chg + n_same, n_chg, n_suffix, n_same, n_skip),
        16, 134, 728, 34)
    warn = '\n'.join(plan['warnings']) if plan['warnings'] else ''
    if plan['skipped']:
        over = [s for s in plan['skipped'] if '用完' in s[2]]
        if over:
            warn += ('\n' if warn else '') + '有 {} 张放不下（号段用完），不会被改号。'.format(len(over))
    w = lab(warn, 16, 170, 728, 32)
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
    b_apply.Enabled = n_chg > 0 and not plan.get('blocked')
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
