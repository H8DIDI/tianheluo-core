"""把 C# 插件改成 Revit 2020 / 2026 都能编：按 2020 编译的报错定位，自动替换
  CS1061 ElementId 没有 Value      -> .V()
  CS1503 long 转不成 BuiltInParameter（new ElementId(long)） -> MakeEid(...)
用法：python tools/codemod_cs2020.py <插件目录名>...   （循环编译直到这两类错误清零）"""
import re
import subprocess
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent / "plugins"
ERR = re.compile(r"^(?P<file>[A-Za-z]:[^(]+)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>CS\d+): (?P<msg>.*?) \[")


def build(proj):
    r = subprocess.run(["dotnet", "build", "-c", "Release", "-p:Revit2020=1"], cwd=ROOT / proj,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    errs, seen = [], set()
    for ln in r.stdout.splitlines():
        m = ERR.match(ln.strip())
        if m:
            d = m.groupdict()
            k = (d["file"], d["line"], d["col"], d["code"])
            if k not in seen:
                seen.add(k)
                errs.append(d)
    return r.returncode == 0, errs


def fix(errs):
    by_file = {}
    for e in errs:
        v = e["code"] == "CS1061" and "Value" in e["msg"] and "ElementId" in e["msg"]
        b = e["code"] == "CS1503" and "BuiltInParameter" in e["msg"]
        if v or b:
            by_file.setdefault(e["file"], set()).add((int(e["line"]), int(e["col"]), e["code"]))
    n = 0
    for f, locs in by_file.items():
        p = pathlib.Path(f)
        raw = p.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        crlf = "\r\n" in text
        lines = text.replace("\r\n", "\n").split("\n")
        for (li, col, code) in sorted(locs, key=lambda x: (x[0], -x[1])):   # 同一行从右往左改，列号不乱
            s = lines[li - 1]
            c = col - 1
            if code == "CS1061":
                k = s.find(".Value", max(0, c - 1))
                if k >= 0 and not (s[k + 6:k + 7].isalnum() or s[k + 6:k + 7] == "_"):
                    s = s[:k] + ".V()" + s[k + 6:]
                    n += 1
            else:
                k = s.rfind("new ElementId(", 0, c + 1)
                if k >= 0:
                    s = s[:k] + "EidCompat.MakeEid(" + s[k + len("new ElementId("):]
                    n += 1
            lines[li - 1] = s
        out = "\n".join(lines)
        if crlf:
            out = out.replace("\n", "\r\n")
        p.write_bytes((b"\xef\xbb\xbf" if bom else b"") + out.encode("utf-8"))
    return n


if __name__ == "__main__":
    for proj in sys.argv[1:]:
        for it in range(10):
            ok, errs = build(proj)
            n = fix(errs)
            print(f"{proj} 第{it + 1}轮：错误 {len(errs)}，自动改 {n} 处")
            if ok or n == 0:
                left = {}
                for e in errs:
                    key = (e["code"], e["msg"][:100])
                    left[key] = left.get(key, 0) + 1
                for (c, m), k in sorted(left.items(), key=lambda kv: -kv[1])[:12]:
                    print(f"   剩 {k} × {c} {m}")
                print("   编译通过" if ok else "   还有错误要手改")
                break
