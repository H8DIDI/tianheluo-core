"""网站的 Revit 2020 部分：
- dist2020/*.thlp → 网站 dl/r2020/，生成 plugins2020.json（宿主 2020 的商店清单，下载走 /api/dl/r2020-<id> 计数）
- THL.Host2020 的编译结果 → dl/host2020/THL-Host-net472_<ver>.zip，生成 host2020.json（2020 宿主据此自更新）
- 安装包 THL-Host.zip：沿用网站上现有的 net8 / net10 两套（2026 用户拿到的东西不变），加上 net472 和新的安装脚本
用法：先 python tools/pack_2020.py，再 python tools/build_site_2020.py"""
import datetime
import hashlib
import io
import json
import pathlib
import re
import shutil
import zipfile
import detzip

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = pathlib.Path(r"C:\KAIFA\tianheluo-web")
DIST = ROOT / "dist2020"
PMAP = json.loads((ROOT / "tools" / "plugin_map.json").read_text(encoding="utf-8"))
today = datetime.date.today().isoformat()


def brief(s, n=80):
    s = s.strip()
    if len(s) <= n:
        return s
    cut = s[:n]
    k = max(cut.rfind(c) for c in "。！？")
    return cut[: k + 1] if k >= n * 0.4 else cut + "…"


# ---------------- 插件 ----------------
dl = SITE / "dl" / "r2020"
if dl.exists():
    shutil.rmtree(dl)
dl.mkdir(parents=True)
try:
    previous = {p["id"]: p for p in json.loads((SITE / "plugins2020.json").read_text(encoding="utf-8"))["plugins"]}
except Exception:
    previous = {}
plugins = []
for pkg in sorted(DIST.glob("*.thlp")):
    m = json.loads(zipfile.ZipFile(pkg).read("plugin.json"))
    desc = m.get("description", "").strip()
    lines = [re.sub(r"^[\s•·\-\*\d\.、]+", "", l).strip() for l in desc.splitlines() if l.strip()]
    shutil.copy(pkg, dl / pkg.name)
    prev = previous.get(m["id"])
    date = prev["date"] if prev and prev.get("version") == m["version"] and prev.get("date") else today
    plugins.append({
        "id": m["id"], "name": m["name"], "en": m["id"], "category": m.get("category", ""),
        "summary": brief(lines[0] if lines else m["name"]), "description": desc, "features": lines[1:7],
        "version": m["version"], "revit": m.get("revit", "2020"), "size": f"{pkg.stat().st_size/1024:.0f} KB",
        "status": "beta", "download": f"/api/dl/r2020-{m['id']}", "file": f"/dl/r2020/{pkg.name}",
        "sha256": hashlib.sha256(pkg.read_bytes()).hexdigest(), "hostApi": m.get("hostApi", 1), "date": date,
        "maturity": "tested" if m["id"] == "hanger-auto" else "beta",
        "glyph": m.get("glyph", ""), "color": m.get("color", ""), "tier": "official", "author": m.get("author", "didi"),
    })
(SITE / "plugins2020.json").write_text(json.dumps({"revit": "2020", "groups": PMAP["groups"], "maturity": PMAP["status"], "plugins": plugins},
                                                  ensure_ascii=False, indent=2), encoding="utf-8")
print(len(plugins), "个 2020 插件 → plugins2020.json")

# ---------------- 宿主 2020 自更新包 ----------------
csproj = (ROOT / "THL.Host2020" / "THL.Host2020.csproj").read_text(encoding="utf-8")
version = re.search(r"<InformationalVersion>([\d.]+)</InformationalVersion>", csproj).group(1)
rel = ROOT / "THL.Host2020" / "bin" / "Release" / "net472"
hd = SITE / "dl" / "host2020"
if hd.exists():
    shutil.rmtree(hd)
hd.mkdir(parents=True)
name = f"THL-Host-net472_{version}.zip"
pkg = hd / name
with detzip.create(pkg) as z:
    detzip.write(z, rel / "THL.addin", "THL.addin")
    for f in sorted(rel.glob("*.dll")):
        detzip.write(z, f, f"THL/{f.name}")
    lib = rel / "lib"
    for f in sorted(lib.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            detzip.write(z, f, f"THL/lib/{f.relative_to(lib).as_posix()}")
sha = hashlib.sha256(pkg.read_bytes()).hexdigest()
try:
    prev = json.loads((SITE / "host2020.json").read_text(encoding="utf-8"))
except Exception:
    prev = {}
host = {
    "version": version,
    "released": prev["released"] if prev.get("version") == version and prev.get("released") else today,
    # 更新说明与 2026 版共用 docs/host_notes.txt 的第一段（两版共用同一套源码）
    "notes": (ROOT / "docs" / "host_notes.txt").read_text(encoding="utf-8").strip().split("\n\n")[0].replace("\n", " "),
    "assets": {"net472": {"url": "/api/dl/host2020-net472", "file": f"/dl/host2020/{name}", "sha256": sha, "size": pkg.stat().st_size}},
    "installer": {"file": "/dl/THL-Host.zip"},
}
(SITE / "host2020.json").write_text(json.dumps(host, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"{name} {pkg.stat().st_size/1024/1024:.1f} MB → host2020.json v{version}")

# ---------------- 安装包：2026 两套沿用网站现有的，加 net472 ----------------
old = SITE / "dl" / "THL-Host.zip"
old_bytes = old.read_bytes()
out = io.BytesIO()
src = zipfile.ZipFile(io.BytesIO(old_bytes))
tmp = ROOT / "dist2020" / "THL-Host.zip"
with detzip.create(tmp) as z:
    for info in sorted(src.infolist(), key=lambda i: i.filename):
        if info.filename.startswith(("net8/", "net10/")) and not info.is_dir():
            detzip.writestr(z, info.filename, src.read(info.filename))
    detzip.write(z, rel / "THL.addin", "net472/THL.addin")
    for f in sorted(rel.glob("*.dll")):
        detzip.write(z, f, f"net472/THL/{f.name}")
    for f in sorted(lib.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            detzip.write(z, f, f"net472/THL/lib/{f.relative_to(lib).as_posix()}")
    for n in ("install.bat", "install.ps1", "uninstall.bat", "uninstall.ps1", "README.txt"):
        detzip.write(z, ROOT / "setup" / n, n)
shutil.copy(tmp, old)
print(f"THL-Host.zip {old.stat().st_size/1024/1024:.1f} MB（net8 / net10 沿用线上版本，新增 net472）")
