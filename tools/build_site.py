"""生成网站商店清单 plugins.json，并把 dist 里的插件包与宿主安装包复制到网站 dl/ 目录。"""
import json, pathlib, shutil, zipfile, re
root = pathlib.Path(__file__).resolve().parent.parent
dist = root / "dist"
site = pathlib.Path(r"C:\KAIFA\tianheluo-web")
(dl := site / "dl").mkdir(exist_ok=True)
for old in dl.glob("*.thlp"): old.unlink()

plugins = []
for pkg in sorted(dist.glob("*.thlp")):
    m = json.loads(zipfile.ZipFile(pkg).read("plugin.json"))
    desc = m.get("description", "").strip()
    lines = [re.sub(r"^[\s•\-\*\d\.、]+", "", l).strip() for l in desc.splitlines() if l.strip()]
    summary = lines[0] if lines else m["name"]
    feats = lines[1:7] or ["详见使用指南"]
    shutil.copy(pkg, dl / pkg.name)
    plugins.append({
        "id": m["id"], "name": m["name"], "en": m["id"], "category": m.get("category", ""),
        "summary": summary[:80], "description": desc, "features": feats,
        "version": m["version"], "revit": m.get("revit", "2026"),
        "size": f"{pkg.stat().st_size/1024:.0f} KB", "status": "beta",
        "download": f"/dl/{pkg.name}", "date": "2026-10-12",
        "glyph": m.get("glyph", ""), "color": m.get("color", ""),
        "author": m.get("author", ""), "license": m.get("license", ""), "homepage": m.get("homepage", ""),
    })
for f in dist.glob("THL-Host_*.zip"): shutil.copy(f, dl / f.name)
json.dump({"revit": "2026", "plugins": plugins}, open(site / "plugins.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(len(plugins), "个插件已写入商店清单")
