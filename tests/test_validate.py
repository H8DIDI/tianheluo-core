"""validate.py 的测试：正例必须通过，各种违规必须被拦住。用法：python tests/test_validate.py"""
import json, os, shutil, subprocess, sys, tempfile, zipfile, pathlib

VALIDATE = r"C:\KAIFA\tianheluo-web\tools\validate.py"
EXAMPLE = pathlib.Path(r"C:\KAIFA\tianheluo-web\examples\hello-world")
fail = 0

def run(target):
    r = subprocess.run([sys.executable, VALIDATE, str(target), "--offline"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return r.returncode, r.stdout + r.stderr

def check(name, cond, extra=""):
    global fail
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + extra)); fail += not cond

def make(tmp, mutate_manifest=None, script=None, extra_files=None, drop=()):
    d = pathlib.Path(tmp) / "hello-world"
    shutil.copytree(EXAMPLE, d)
    if mutate_manifest:
        m = json.loads((d / "plugin.json").read_text(encoding="utf-8")); mutate_manifest(m)
        (d / "plugin.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    if script is not None: (d / "script.py").write_text(script, encoding="utf-8")
    for n, data in (extra_files or {}).items(): (d / n).write_bytes(data)
    for n in drop: (d / n).unlink()
    return d

BASE = "from pyrevit import revit, forms\n\ndef main():\n    pass\n\nif __name__ == '__main__':\n    main()\n"
cases = [
    ("正例：官方示例通过", {}, 0, ""),
    ("拦截 subprocess", dict(script=BASE + "import subprocess\n"), 1, "subprocess"),
    ("拦截 os.system", dict(script=BASE + "import os\nos.system('calc')\n"), 1, "os.system"),
    ("拦截 eval", dict(script=BASE + "eval('1+1')\n"), 1, "eval"),
    ("拦截 exec", dict(script=BASE + "exec('x=1')\n"), 1, "exec"),
    ("拦截 __import__", dict(script=BASE + "__import__('os')\n"), 1, "__import__"),
    ("拦截 urllib（未声明联网）", dict(script=BASE + "import urllib.request\n"), 1, "urllib"),
    ("拦截 shutil.rmtree", dict(script=BASE + "import shutil\nshutil.rmtree('C:/x')\n"), 1, "rmtree"),
    ("拦截 .NET Process.Start", dict(script=BASE + "import System\nSystem.Diagnostics.Process.Start('calc')\n"), 1, "Process"),
    ("拦截 .NET 加载外部程序集", dict(script=BASE + "import clr\nclr.AddReferenceToFileAndPath('x.dll')\n"), 1, "AddReferenceToFile"),
    ("拦截 powershell 字样", dict(script=BASE + "s='powershell -c x'\n"), 1, "powershell"),
    ("拦截海象运算符", dict(script=BASE + "if (n := 3) > 1:\n    pass\n"), 1, ":="),
    ("拦截 async", dict(script=BASE + "async def f():\n    pass\n"), 1, "async"),
    ("拦截语法错误", dict(script="def (:\n"), 1, "语法错误"),
    ("拦截 .dll 文件", dict(extra_files={"evil.dll": b"MZ"}), 1, ".dll"),
    ("拦截 .exe 文件", dict(extra_files={"x.exe": b"MZ"}), 1, ".exe"),
    ("拦截 .pyc 文件", dict(extra_files={"x.pyc": b"\0"}), 1, ".pyc"),
    ("拦截缺少 LICENSE", dict(drop=("LICENSE",)), 1, "LICENSE"),
    ("拦截缺少 README", dict(drop=("README.md",)), 1, "README"),
    ("拦截缺少 plugin.json 字段", dict(mutate_manifest=lambda m: m.pop("author")), 1, "author"),
    ("拦截非法 id（大写）", dict(mutate_manifest=lambda m: m.update(id="Bad_ID")), 1, "id"),
    ("拦截 type=dotnet", dict(mutate_manifest=lambda m: m.update(type="dotnet")), 1, "python"),
    ("拦截闭源许可证", dict(mutate_manifest=lambda m: m.update(license="Proprietary")), 1, "license"),
    ("拦截非法版本号", dict(mutate_manifest=lambda m: m.update(version="1.0")), 1, "version"),
    ("拦截路径型 script", dict(mutate_manifest=lambda m: m.update(script="../evil.py")), 1, "script"),
    ("拦截超大文件", dict(extra_files={"big.txt": b"x" * 210_000}), 1, "200 KB"),
    ("联网 + 声明 network=true → 通过但有提示", dict(script=BASE + "import urllib.request\n", mutate_manifest=lambda m: m.update(network=True)), 0, "network=true"),
    ("声明 network=true 却没联网 → 提示", dict(mutate_manifest=lambda m: m.update(network=True)), 0, "没有发现联网"),
]
for name, kw, want_code, want_text in cases:
    with tempfile.TemporaryDirectory() as tmp:
        d = make(tmp, **kw)
        code, out = run(d)
        check(name, code == want_code and (want_text in out or want_text == ""), f"code={code}, 输出片段={out[-300:]!r}")

# zip：根目录直接放文件 / 带一层目录 / 路径穿越
with tempfile.TemporaryDirectory() as tmp:
    z1 = pathlib.Path(tmp) / "a.zip"
    with zipfile.ZipFile(z1, "w") as z:
        for f in EXAMPLE.iterdir(): z.write(f, f.name)
    check("zip（根目录直接放文件）通过", run(z1)[0] == 0)
    z2 = pathlib.Path(tmp) / "b.zip"
    with zipfile.ZipFile(z2, "w") as z:
        for f in EXAMPLE.iterdir(): z.write(f, "hello-world/" + f.name)
    check("zip（带一层目录）通过", run(z2)[0] == 0)
    z3 = pathlib.Path(tmp) / "c.zip"
    with zipfile.ZipFile(z3, "w") as z:
        z.writestr("plugin.json", "{}"); z.writestr("../../escaped.txt", "x")
    code, out = run(z3)
    check("zip 路径穿越被拒绝", code == 1 and "非法路径" in out and not (pathlib.Path(tmp).parent / "escaped.txt").exists(), out[-200:])

print("ALL PASS" if fail == 0 else f"{fail} FAILED")
sys.exit(1 if fail else 0)
