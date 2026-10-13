"""把 community/<id>/ 里审核通过的社区插件打成 dist/<id>_<version>.thlp（带 tier=community 标记）。"""
import json
import os
import pathlib
import detzip

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMMUNITY = pathlib.Path(os.environ.get("THL_COMMUNITY_DIR", ROOT / "community"))
OUT = ROOT / "dist"
OUT.mkdir(exist_ok=True)

count = 0
if COMMUNITY.exists():
    for d in sorted(p for p in COMMUNITY.iterdir() if p.is_dir() and (p / "plugin.json").exists()):
        m = json.loads((d / "plugin.json").read_text(encoding="utf-8-sig"))
        m["tier"] = "community"
        pkg = OUT / f"{m['id']}_{m['version']}.thlp"
        with detzip.create(pkg) as z:
            detzip.writestr(z, "plugin.json", json.dumps(m, ensure_ascii=False, indent=2))
            for f in sorted(d.rglob("*")):
                if f.is_file() and f.name not in ("plugin.json", ".submission.json") and "__pycache__" not in f.parts:
                    detzip.write(z, f, f.relative_to(d).as_posix())
        print(f"{pkg.name:36} {pkg.stat().st_size/1024:6.1f} KB  社区 · {m['name']} · {m.get('author')}")
        count += 1
print(count, "个社区插件已打包")
