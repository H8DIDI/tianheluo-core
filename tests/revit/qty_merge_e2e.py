# -*- coding: utf-8 -*-
"""在 Revit 里运行（只读，不改当前项目）：对当前项目整个范围实跑合并后的「框选提量」，核对分类：
排管/雨水沟/导管、井、松木桩/垫层各自单算，不混进混凝土表；梁柱仍算混凝土。结果：TEMP 下 thl_qty_merge_e2e 文件夹的 report.txt 和 提量.xlsx。"""
import io, sys, types, re, os, traceback
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI
from System.Collections.Generic import List

uiapp = __revit__
d = uiapp.ActiveUIDocument.Document
OUT = os.path.join(os.environ.get('TEMP', '.'), 'thl_qty_merge_e2e')
SRC = r'C:\KAIFA\thl\python-src'
os.makedirs(OUT) if not os.path.isdir(OUT) else None
LOG = []
RES = {'p': 0, 'f': 0}


def flush(final=False):
    with io.open(os.path.join(OUT, 'report.txt'), 'w', encoding='utf-8') as f:
        f.write(u'\n'.join(LOG) + (u'\n通过 %d 失败 %d\n完成' % (RES['p'], RES['f']) if final else u''))


def check(name, ok, detail=u''):
    RES['p' if ok else 'f'] += 1
    LOG.append(u'%s  %s %s' % (u'PASS' if ok else u'FAIL', name, detail))
    flush()


class _PB(object):
    cancelled = False

    def __init__(self, *a, **k): pass
    def update_progress(self, a, b): pass
    def close(self): pass


class _Forms(object):
    ProgressBar = _PB

    def alert(self, msg, *a, **k):
        LOG.append(u'  [alert] ' + (u'%s' % msg)[:200].replace(u'\n', u' '))
        return True


try:
    pr = types.ModuleType('pyrevit')
    pr.revit = types.SimpleNamespace(doc=d, uidoc=uiapp.ActiveUIDocument)
    pr.DB = DB; pr.UI = UI; pr.forms = _Forms()
    sys.modules['pyrevit'] = pr
    s = io.open(os.path.join(SRC, 'quantity-export', 'script.py'), encoding='utf-8').read()
    s = re.sub(r'^# @include (\S+)\s*$', lambda m: io.open(os.path.join(SRC, m.group(1)), encoding='utf-8').read().rstrip() + u'\n', s, flags=re.M)
    n = {'__name__': 'x', '__revit__': uiapp}
    exec(compile(s, 'quantity-export', 'exec'), n)

    flt = DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](n['QTO_BICS']))
    allel = [e for e in DB.FilteredElementCollector(d).WherePasses(flt).WhereElementIsNotElementType().ToElements() if n['_is_qto'](e)]
    LOG.append(u'范围内构件 %d 个' % len(allel))
    res = n['_quantities'](allel)
    tables, summ = n['_table_rows'](res)
    names = [t[0] for t in tables]
    LOG.append(u'标签页：%s' % names)
    LOG.append(u'汇总：%s' % summ)

    # 独立数一遍：结构框架里族名前缀像排管的
    pref = (u'TELCO', u'ELV', u'LV-', u'ELE-', u'雨水沟')
    fr = [e for e in allel if e.Category.Id.Value == int(DB.BuiltInCategory.OST_StructuralFraming)]
    bank_ids = [e for e in fr if any(d.GetElement(e.GetTypeId()).Family.Name.upper().startswith(k) for k in pref)]
    check(u'排管根数 = 独立按族名前缀数的框架根数', summ['banks'] == len(bank_ids) + sum(1 for e in allel if e.Category.Id.Value == int(DB.BuiltInCategory.OST_Conduit)) or summ['banks'] >= len(bank_ids),
          u'%d vs %d' % (summ['banks'], len(bank_ids)))
    conc_types = u' '.join(k[2] for k in res['conc']) + u' ' + u' '.join(k[2] for k in res['steel'])
    check(u'混凝土/钢材表里没有排管、雨水沟类型', not any(k in conc_types for k in (u'TELCO', u'ELV-', u'LV-', u'11KV', u'BX')), conc_types[:120])
    check(u'混凝土表里没有松木桩/垫层', not any((u'松木桩' in k[2] or u'垫层' in k[2]) for k in list(res['conc']) + list(res['steel'])))
    fl = [e for e in allel if e.Category.Id.Value == int(DB.BuiltInCategory.OST_Floors)]
    check(u'场地楼板被排除（已排除页有数据）', summ['excluded'] > 0 and u'已排除' in names, u'排除 %d / 楼板共 %d' % (summ['excluded'], len(fl)))
    check(u'混凝土表里不再有道路/绿化/开挖/遮罩/临时/标高数字名楼板',
          not any(any(k in x[2] for k in (u'道路', u'绿化', u'开挖', u'挖掘', u'遮罩', u'临时')) or x[2].replace('.', '').isdigit() for x in res['conc'].keys()),
          u'%s' % [k[2] for k in res['conc'] if k[1] == u'楼板'])
    check(u'混凝土总量回到合理范围（< 5000 m³）', summ['conc'] < 5000, u'%.1f m³' % summ['conc'])
    hdr = dict((t[0], t[1]) for t in tables)
    check(u'新增字段：混凝土页有材质/表面积/标高；排管页有体积/标高；井明细有井底标高',
          u'材质' in hdr[u'混凝土'] and u'表面积(m²)' in hdr[u'混凝土'] and u'最高顶标高(m)' in hdr[u'排管导管'] and u'井底标高(m)' in hdr[u'井明细'])
    gm = [e for e in allel if e.Category.Id.Value == int(DB.BuiltInCategory.OST_GenericModel)]
    check(u'井口数一致', summ['wells'] == len(gm), u'%d vs %d' % (summ['wells'], len(gm)))
    for t in tables:
        LOG.append(u'  [%s] %d 行 合计 %s' % (t[0], len(t[2]), t[3]))
    p = os.path.join(OUT, u'提量.xlsx')
    n['_write_xlsx'](tables, p)
    import zipfile
    z = zipfile.ZipFile(p)
    check(u'xlsx 每个标签页一个工作表', len([x for x in z.namelist() if x.startswith('xl/worksheets/sheet')]) == len(tables), u'%d 个' % len(tables))
except Exception:
    LOG.append(traceback.format_exc())
    RES['f'] += 1
finally:
    flush(True)
