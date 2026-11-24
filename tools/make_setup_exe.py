"""做一个 exe 安装程序 dist/THL-Setup.exe：内含 Revit 2020 / 2026 三套宿主，双击选版本安装。
来源：网站上的 dl/THL-Host.zip（net8 / net10 / net472 三套），IronPython 标准库三套完全一样，只存一份（省一半多体积）。
用法：python tools/make_setup_exe.py [--site]   （--site：同时复制到网站 dl/THL-Setup.exe）"""
import pathlib
import shutil
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = pathlib.Path(r"C:\KAIFA\tianheluo-web")
EXE_DIR = ROOT / "setup" / "exe"
src = zipfile.ZipFile(SITE / "dl" / "THL-Host.zip")
payload = EXE_DIR / "payload.zip"

libs = {}
with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for info in sorted(src.infolist(), key=lambda i: i.filename):
        n = info.filename
        if info.is_dir():
            continue
        b, _, rest = n.partition("/")
        if b not in ("net8", "net10", "net472") or not rest:
            continue
        data = src.read(n)
        if rest.startswith("THL/lib/"):
            key = rest[len("THL/"):]                  # lib/...
            if key in libs:
                if libs[key] != len(data):
                    raise SystemExit(f"三套的标准库不一样：{key}")
                continue
            libs[key] = len(data)
            zi = zipfile.ZipInfo(key, date_time=(2026, 1, 1, 0, 0, 0))
        else:
            zi = zipfile.ZipInfo(f"builds/{b}/{rest}", date_time=(2026, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, data, compresslevel=9)
print(f"payload.zip {payload.stat().st_size/1024/1024:.1f} MB（标准库 {len(libs)} 个文件只存一份）")

ico = EXE_DIR / "thl.ico"
if not ico.exists():
    shutil.copy(SITE / "favicon.ico", ico)
r = subprocess.run(["dotnet", "build", "-c", "Release", "-nologo", "-v:q"], cwd=EXE_DIR, capture_output=True, text=True, encoding="utf-8", errors="replace")
if r.returncode != 0:
    print(r.stdout[-3000:])
    raise SystemExit("编译失败")
exe = EXE_DIR / "bin" / "Release" / "net472" / "THL-Setup.exe"
out = ROOT / "dist" / "THL-Setup.exe"
shutil.copy(exe, out)
print(f"{out.name} {out.stat().st_size/1024/1024:.1f} MB")
if "--site" in sys.argv:
    shutil.copy(out, SITE / "dl" / "THL-Setup.exe")
    print("→ 网站 dl/THL-Setup.exe")
