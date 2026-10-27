# -*- coding: utf-8 -*-
"""在 Revit 里运行（Dynamo Python / HuskyBIM revit_run_dynamo_python）：
① 用 Revit 自带的 IronPython 编译全部 python-src 插件（展开 # @include 后只编译、不运行）；
② 共用设置窗口真的弹出来，300 ms 后自动点「保存」，核对文本/多行/数字字段读写一致。
不改模型、不写配置文件。结果写到 OUT（默认 %TEMP%\\thl_check_compile.txt）。"""
import io, os, re, traceback

ROOT = globals().get('ROOT') or r'C:\KAIFA\thl\python-src'
OUT = globals().get('OUT') or os.path.join(os.environ.get('TEMP', '.'), 'thl_check_compile.txt')
LOG = []
INC = re.compile(r'^# @include (\S+)\s*$', re.M)


def expand(t):
    return INC.sub(lambda m: io.open(os.path.join(ROOT, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', t)


bad = 0
for pid in sorted(os.listdir(ROOT)):
    f = os.path.join(ROOT, pid, 'script.py')
    if not os.path.isfile(f):
        continue
    try:
        compile(expand(io.open(f, encoding='utf-8').read()), pid, 'exec')
        LOG.append(u'PASS 编译 %s' % pid)
    except Exception as ex:
        bad += 1
        LOG.append(u'FAIL 编译 %s: %s' % (pid, ex))

try:
    ns = {}
    exec(compile(io.open(os.path.join(ROOT, '_shared', 'settings_ui.py'), encoding='utf-8').read(), 'su', 'exec'), ns)
    W = ns['_SUW']
    fields = [{u'kind': u'header', u'label': u'自测'},
              {u'key': u'a', u'label': u'文本', u'kind': u'text'},
              {u'key': u'b', u'label': u'多行', u'kind': u'lines', u'rows': 4},
              {u'key': u'c', u'label': u'数字', u'kind': u'num'},
              {u'key': u'd', u'label': u'勾选', u'kind': u'bool'}]
    vals = {u'a': u'井大样_', u'b': [u'AA | 强电井 | QD | AA- 强电特型井', u'T | 通信井 | TX | T- 通信井'], u'c': 300, u'd': True}
    t = W.Timer()
    t.Interval = 300

    def tick(s, e):
        t.Stop()
        for frm in W.Application.OpenForms:
            for c in frm.Controls:
                if getattr(c, 'Name', '') == u'su_ok':
                    c.PerformClick()
                    return
    t.Tick += tick
    t.Start()
    res = ns['su_fields_dialog'](u'天河落自测', u'自动点保存', fields, vals, vals)
    ok = res is not None and res[u'a'] == vals[u'a'] and res[u'b'] == vals[u'b'] and res[u'c'] == 300 and res[u'd'] is True
    bad += (not ok)
    LOG.append(u'%s 设置窗口读写往返 %r' % (u'PASS' if ok else u'FAIL', res))
except Exception:
    bad += 1
    LOG.append(u'FAIL 设置窗口自测\n' + traceback.format_exc())

LOG.append(u'\n' + (u'全部通过' if not bad else u'%d 项失败' % bad))
with io.open(OUT, 'w', encoding='utf-8') as f:
    f.write(u'\n'.join(LOG))
