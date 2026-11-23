#!/usr/bin/env python3
"""把脚本里「ElementId 取整数」的直接写法换成 compat 的 thl_eid()，让同一份脚本 2020 和 2026 都能跑。

    python tools/codemod_eid.py            # 干跑：只列出会改哪些文件、多少处
    python tools/codemod_eid.py --apply    # 真改；每个改过的文件自动补 `# @include _shared/compat.py`

只改这几种肯定是 ElementId 的写法（不会误伤 NumericUpDown.Value、Parameter.Value 之类）：
    X.Id.Value / X.OwnerViewId.Value / X.ViewId.Value / X.ScheduleId.Value / X.ElementId.Value / X.LevelId.Value
    X.GetTypeId().Value
其余 `.Value`（变量名叫 eid / i / x 的）逐个看，已经有 try/except 回退的不动。
sendit/ 是第三方库，不改。
"""
import argparse
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "python-src"

EXPR = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[[^\[\]\n]*\]|\([^()\n]*\))*"
PAT_PROP = re.compile(r"(" + EXPR + r")\.(Id|OwnerViewId|ViewId|ScheduleId|ElementId|LevelId)\.Value\b")
PAT_CALL = re.compile(r"(" + EXPR + r"\.GetTypeId\(\))\.Value\b")
INCLUDE = "# @include _shared/compat.py"


def convert(text):
    n = [0]

    def prop(m):
        n[0] += 1
        return "thl_eid(%s.%s)" % (m.group(1), m.group(2))

    def call(m):
        n[0] += 1
        return "thl_eid(%s)" % m.group(1)
    out = PAT_PROP.sub(prop, text)
    out = PAT_CALL.sub(call, out)
    return out, n[0]


def add_include(text):
    if INCLUDE in text:
        return text
    lines = text.split("\n")
    # 放在最后一个已有的 @include 后面；没有就放在 `from pyrevit import ...` 那行后面
    idx = None
    for i, ln in enumerate(lines):
        if ln.startswith("# @include "):
            idx = i
    if idx is None:
        for i, ln in enumerate(lines):
            if re.match(r"^from pyrevit import", ln):
                idx = i
                break
    if idx is None:
        raise SystemExit("找不到插入 include 的位置")
    lines.insert(idx + 1, INCLUDE)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    total = 0
    for p in sorted(SRC.rglob("*.py")):
        rel = p.relative_to(SRC).as_posix()
        if rel.startswith("sendit/") or rel == "_shared/compat.py":
            continue
        text = p.read_text(encoding="utf-8")
        new, n = convert(text)
        if not n:
            continue
        total += n
        print("%-40s %3d 处" % (rel, n))
        if a.apply:
            if not rel.startswith("_shared/"):
                new = add_include(new)
            p.write_text(new, encoding="utf-8", newline="\n")
    if a.apply:
        # 公共片段（_shared/*.py）里用了 thl_eid 时，内联了它们的脚本也要有 compat（先 --apply 改完再跑一次本脚本即可补上）
        for p in sorted(SRC.rglob("script.py")):
            rel = p.relative_to(SRC).as_posix()
            if rel.startswith("sendit/"):
                continue
            t = p.read_text(encoding="utf-8")
            shared_used = [c.relative_to(SRC).as_posix() for c in (SRC / "_shared").glob("*.py")
                           if "thl_eid(" in c.read_text(encoding="utf-8") and c.name != "compat.py" and ("# @include " + c.relative_to(SRC).as_posix()) in t]
            if shared_used and INCLUDE not in t:
                p.write_text(add_include(t), encoding="utf-8", newline="\n")
                print("%-40s 补 compat include（用了 %s）" % (rel, ", ".join(shared_used)))
    print("合计 %d 处%s" % (total, "（已改）" if a.apply else "（干跑，没改文件；加 --apply 真改）"))


if __name__ == "__main__":
    main()
