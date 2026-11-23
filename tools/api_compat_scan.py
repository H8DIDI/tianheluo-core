#!/usr/bin/env python3
"""Revit API 版本兼容扫描：哪些插件脚本用到了「新版本有、旧版本没有」的 API。

用法：
    python tools/api_compat_scan.py                      # 2020（最低）对 2026（最高），扫 python-src 下全部脚本
    python tools/api_compat_scan.py --old 2020 --new 2026 --plugin export-dwg

原理（不用打开 Revit）：
  1. _apidiff/api_<年份>.txt 是用 _apidiff/dump_meta.ps1 从 RevitAPI.dll / RevitAPIUI.dll 的元数据里导出的「类型 / 成员 / 构造函数参数个数」清单；
  2. 对比出：新版才有的类型、新版才有的成员（如 ElementId.Value）、旧版有新版已删的成员（如 ElementId.IntegerValue）；
  3. 逐个脚本（展开 # @include 的公共片段后）找：脚本里出现了这个类型名又出现了 .成员名 → 记一条「可能用到」。
  这是字面匹配，会有少量误报（同名成员在别的类型上）；漏报很少。真正要不要改，看每条的上下文。
结果：_apidiff/report_<old>_<new>.json 和 .md
"""
import argparse
import json
import pathlib
import re
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "python-src"
OUT = ROOT / "_apidiff"


def load(year):
    types, members, ctors = set(), defaultdict(set), defaultdict(set)
    for ln in (OUT / f"api_{year}.txt").read_text(encoding="utf-8").splitlines():
        k, _, rest = ln.partition(" ")
        if k == "T":
            types.add(rest)
        elif k == "M":
            t, _, m = rest.rpartition(".")
            members[t].add(m)
        elif k == "C":
            t, _, n = rest.rpartition("/")
            ctors[t].add(int(n))
    return types, members, ctors


def simple(t):
    return t.rsplit(".", 1)[-1]


def expand(path):
    """展开 # @include 公共片段，返回 (全文, 用到的片段列表)。"""
    text = path.read_text(encoding="utf-8")
    inc = []

    def sub(m):
        p = SRC / m.group(1)
        inc.append(m.group(1))
        return p.read_text(encoding="utf-8") if p.exists() else ""
    return re.sub(r"^# @include (\S+)\s*$", sub, text, flags=re.M), inc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", default="2020")
    ap.add_argument("--new", default="2026")
    ap.add_argument("--plugin")
    a = ap.parse_args()
    to, mo, co = load(a.old)
    tn, mn, cn = load(a.new)
    # 只看 DB / UI 命名空间的类型；简单名重名的（不同命名空间）按全名分开
    new_types = {t for t in tn - to if t.startswith("Autodesk.Revit.")}
    old_only_types = {t for t in to - tn if t.startswith("Autodesk.Revit.")}
    new_members = {t: mn[t] - mo[t] for t in tn & to if mn[t] - mo[t]}
    gone_members = {t: mo[t] - mn[t] for t in tn & to if mo[t] - mn[t]}
    ctor_new = {t: cn[t] - co[t] for t in tn & to if cn[t] - co[t]}
    ctor_gone = {t: co[t] - cn[t] for t in tn & to if co[t] - cn[t]}
    print(f"{a.old}→{a.new}：新增类型 {len(new_types)}，删除类型 {len(old_only_types)}，"
          f"有新增成员的类型 {len(new_members)}，有被删成员的类型 {len(gone_members)}")

    scripts = []
    for p in sorted(SRC.rglob("script.py")):
        if "_shared" in p.parts:
            continue
        plugin = p.parent.name if p.parent.parent == SRC else "/".join(p.parent.relative_to(SRC).parts)
        if a.plugin and plugin != a.plugin:
            continue
        scripts.append((plugin, p))
    report = {}
    for plugin, p in scripts:
        text, inc = expand(p)
        hits = {"new_types": set(), "new_members": set(), "gone_members": set(), "ctor": set()}
        for t in new_types:
            if re.search(r"\b" + re.escape(simple(t)) + r"\b", text):
                hits["new_types"].add(t)
        for t, ms in new_members.items():
            st = simple(t)
            if not re.search(r"\b" + re.escape(st) + r"\b", text):
                continue
            for m in ms:
                if re.search(r"\.\b" + re.escape(m) + r"\b", text):
                    hits["new_members"].add(f"{st}.{m}")
        for t, ms in gone_members.items():
            st = simple(t)
            for m in ms:
                if re.search(r"\.\b" + re.escape(m) + r"\b", text):
                    hits["gone_members"].add(f"{st}.{m}")
        # Id.Value / .IntegerValue 是最常见的一类，单独数个数
        n_value = len(re.findall(r"\.Id\.Value\b", text))
        n_ival = len(re.findall(r"\.IntegerValue\b", text))
        report[plugin] = {"includes": inc, "id_value_uses": n_value, "integer_value_uses": n_ival,
                          **{k: sorted(v) for k, v in hits.items()}}
    (OUT / f"report_{a.old}_{a.new}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    lines = [f"# Revit API 兼容扫描：{a.old} → {a.new}", "",
             "| 插件 | 新版才有的类型 | 新版才有的成员 | `.Id.Value` | 旧写法 `.IntegerValue` | 用到的公共片段 |", "|---|---|---|---|---|---|"]
    for plugin, r in sorted(report.items(), key=lambda kv: -(len(kv[1]["new_types"]) + len(kv[1]["new_members"]) + kv[1]["id_value_uses"])):
        lines.append(f"| {plugin} | {len(r['new_types'])} | {len(r['new_members'])} | {r['id_value_uses']} | {r['integer_value_uses']} | {', '.join(i.replace('_shared/', '') for i in r['includes'])} |")
    lines += ["", "## 逐个插件明细（新版才有的类型 / 成员）", ""]
    for plugin, r in sorted(report.items()):
        if r["new_types"] or r["new_members"]:
            lines.append(f"### {plugin}")
            if r["new_types"]:
                lines.append("- 类型：" + "、".join(simple(t) for t in r["new_types"]))
            if r["new_members"]:
                lines.append("- 成员：" + "、".join(r["new_members"]))
            lines.append("")
    (OUT / f"report_{a.old}_{a.new}.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:4 + len(report) + 1]))


if __name__ == "__main__":
    main()
