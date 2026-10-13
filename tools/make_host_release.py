"""发布宿主新版本：为 .NET 8 / .NET 10 各打一个更新包（THL.addin + THL\\），算 SHA256，
写网站的 host.json；宿主在 Revit 里据此检查并下载更新。

版本号的唯一来源：THL.Host/THL.Host.csproj 里的 <InformationalVersion>。
更新说明：docs/host_notes.txt 的第一段（每次发布前改一下）。"""
import datetime
import hashlib
import json
import pathlib
import re
import shutil
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = pathlib.Path(r"C:\KAIFA\tianheluo-web")
BUILDS = {"net8": "net8.0-windows", "net10": "net10.0-windows"}

version = re.search(r"<InformationalVersion>([\d.]+)</InformationalVersion>", (ROOT / "THL.Host/THL.Host.csproj").read_text(encoding="utf-8")).group(1)
notes_file = ROOT / "docs" / "host_notes.txt"
notes = notes_file.read_text(encoding="utf-8").strip().split("\n\n")[0].replace("\n", " ") if notes_file.exists() else ""

out_dir = SITE / "dl" / "host"
if out_dir.exists():
    shutil.rmtree(out_dir)          # 只保留最新版（历史版本在 git 里）
out_dir.mkdir(parents=True)

assets = {}
for key, tfm in BUILDS.items():
    rel = ROOT / "THL.Host/bin/Release" / tfm
    name = f"THL-Host-{key}_{version}.zip"
    pkg = out_dir / name
    with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(rel / "THL.addin", "THL.addin")
        for f in list(rel.glob("*.dll")) + list(rel.glob("*.deps.json")):
            z.write(f, f"THL/{f.name}")
        lib = rel / "lib"
        for f in lib.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, f"THL/lib/{f.relative_to(lib).as_posix()}")
    sha = hashlib.sha256(pkg.read_bytes()).hexdigest()
    assets[key] = {"url": f"/api/dl/host-{key}", "file": f"/dl/host/{name}", "sha256": sha, "size": pkg.stat().st_size}
    print(f"{name:34} {pkg.stat().st_size/1024/1024:5.1f} MB  sha256 {sha[:16]}…")

host = {
    "version": version,
    "released": datetime.date.today().isoformat(),
    "notes": notes,
    "assets": assets,
    "installer": {"file": "/dl/THL-Host.zip"},
}
(SITE / "host.json").write_text(json.dumps(host, ensure_ascii=False, indent=2), encoding="utf-8")
print("host.json -> v" + version)
