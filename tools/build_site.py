"""生成网站商店清单 plugins.json，并把 dist 里的插件包与宿主安装包复制到网站 dl/ 目录。
- download：走计数入口 /api/dl/<id>（宿主和网页都用它；服务端计一次下载后直接返回文件）
- file：静态文件的真实路径（服务端据此取文件）
- date：版本没变就沿用上次的日期，有新版本才更新成今天（商店里的“更新日期”才有意义）"""
import datetime
import json
import pathlib
import re
import shutil
import zipfile

root = pathlib.Path(__file__).resolve().parent.parent
dist = root / "dist"
site = pathlib.Path(r"C:\KAIFA\tianheluo-web")
(dl := site / "dl").mkdir(exist_ok=True)
for old in dl.glob("*.thlp"):
    old.unlink()

today = datetime.date.today().isoformat()
previous = {}
try:
    previous = {p["id"]: p for p in json.load(open(site / "plugins.json", encoding="utf-8"))["plugins"]}
except Exception:
    pass

plugins = []
for pkg in sorted(dist.glob("*.thlp")):
    m = json.loads(zipfile.ZipFile(pkg).read("plugin.json"))
    desc = m.get("description", "").strip()
    lines = [re.sub(r"^[\s•\-\*\d\.、]+", "", l).strip() for l in desc.splitlines() if l.strip()]
    summary = lines[0] if lines else m["name"]
    feats = lines[1:7] or ["详见使用指南"]
    shutil.copy(pkg, dl / pkg.name)
    prev = previous.get(m["id"])
    date = prev["date"] if prev and prev.get("version") == m["version"] and prev.get("date") else today
    plugins.append({
        "id": m["id"], "name": m["name"], "en": m["id"], "category": m.get("category", ""),
        "summary": summary[:80], "description": desc, "features": feats,
        "version": m["version"], "revit": m.get("revit", "2026"),
        "size": f"{pkg.stat().st_size/1024:.0f} KB", "status": "beta",
        "download": f"/api/dl/{m['id']}", "file": f"/dl/{pkg.name}", "date": date,
        "glyph": m.get("glyph", ""), "color": m.get("color", ""),
        "tier": m.get("tier", "official"), "author": m.get("author", ""), "license": m.get("license", ""), "homepage": m.get("homepage", ""),
    })

installer = dist / "THL-Host.zip"
if installer.exists():
    shutil.copy(installer, dl / installer.name)
json.dump({"revit": "2026", "plugins": plugins}, open(site / "plugins.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(len(plugins), "个插件已写入商店清单")
