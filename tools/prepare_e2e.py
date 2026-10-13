"""准备“Revit 内更新”端到端测试的素材（全部放在 thl/_e2e，不碰线上）：
  - 一份版本号为 0.2.1 的宿主（假装是新版本）→ host.json + 更新包（SHA256 校验）
  - 一份 well-sheets 0.1.1 的插件包 → 本地商店清单
  - 用 Tianheluo/config.json 里的 catalogUrl / hostUpdateUrl 指向它们（测试后由 restore_config 恢复）
用法：python tools/prepare_e2e.py"""
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
E2E = ROOT / "_e2e"
SITE = E2E / "site"
SRC = E2E / "src021"
NEW = "0.2.1"

shutil.rmtree(E2E, ignore_errors=True)
(SITE / "dl" / "host").mkdir(parents=True)

# 1) 复制宿主源码，改版本号后编译（输出到 _e2e，不覆盖正式产物）
shutil.copytree(ROOT / "THL.Host", SRC, ignore=shutil.ignore_patterns("bin", "obj"))
subprocess.run(["dotnet", "build", "-c", "Release", "-f", "net10.0-windows",
                f"-p:AssemblyVersion={NEW}.0", f"-p:FileVersion={NEW}.0", f"-p:InformationalVersion={NEW}",
                f"-p:RevitAddinsDir={E2E / 'fakeaddins'}"], cwd=SRC, check=True, capture_output=True)
rel = SRC / "bin" / "Release" / "net10.0-windows"
assert (rel / "THL.Host.dll").exists()

# 2) 更新包：THL.addin + THL\...（与正式发布包结构一致）
name = f"THL-Host-net10_{NEW}.zip"
pkg = SITE / "dl" / "host" / name
with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as z:
    z.write(rel / "THL.addin", "THL.addin")
    for f in list(rel.glob("*.dll")) + list(rel.glob("*.deps.json")):
        z.write(f, f"THL/{f.name}")
    for f in (rel / "lib").rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts:
            z.write(f, f"THL/lib/{f.relative_to(rel / 'lib').as_posix()}")
sha = hashlib.sha256(pkg.read_bytes()).hexdigest()
(SITE / "host.json").write_text(json.dumps({
    "version": NEW, "released": "2026-10-13", "notes": "（端到端测试版）验证 Revit 内一键更新。",
    "assets": {"net10": {"url": f"/dl/host/{name}", "file": f"/dl/host/{name}", "sha256": sha, "size": pkg.stat().st_size}},
    "installer": {"file": "/dl/THL-Host.zip"},
}, ensure_ascii=False, indent=2), encoding="utf-8")

# 3) 插件更新素材：well-sheets 0.1.1
site_json = json.load(open(r"C:\KAIFA\tianheluo-web\plugins.json", encoding="utf-8"))
src_pkg = ROOT / "dist" / "well-sheets_0.1.0.thlp"
new_pkg = SITE / "dl" / "well-sheets_0.1.1.thlp"
with zipfile.ZipFile(src_pkg) as zin, zipfile.ZipFile(new_pkg, "w", zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        data = zin.read(item.filename)
        if item.filename == "plugin.json":
            m = json.loads(data); m["version"] = "0.1.1"; data = json.dumps(m, ensure_ascii=False, indent=2).encode("utf-8")
        zout.writestr(item.filename, data)
for p in site_json["plugins"]:
    if p["id"] == "well-sheets":
        p["version"] = "0.1.1"; p["download"] = "/dl/well-sheets_0.1.1.thlp"; p["file"] = "/dl/well-sheets_0.1.1.thlp"
    else:   # 其它插件的包也放进来，保证“商店”页能安装
        src = ROOT / "dist" / pathlib.Path(p["file"]).name
        if src.exists():
            shutil.copy(src, SITE / "dl" / src.name); p["download"] = p["file"]
json.dump(site_json, open(SITE / "plugins.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# 4) 备份用户当前配置并指向测试素材
cfg_path = pathlib.Path(os.environ["APPDATA"]) / "Tianheluo" / "config.json"
bak = E2E / "config.json.bak"
if cfg_path.exists():
    shutil.copy(cfg_path, bak)
cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
cfg["catalogUrl"] = str(SITE / "plugins.json")
cfg["hostUpdateUrl"] = str(SITE / "host.json")
cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
print("e2e 素材已就绪。更新包 sha256:", sha[:16], "…")
print("配置已指向测试素材；原配置备份在", bak)
