"""一键构建：编译宿主(.NET 8 + .NET 10)与 C# 插件 → 签名 → 打包全部插件 → 宿主安装包 → 网站商店清单。
用法：python tools/build_all.py [--deploy] [--sign]
  --deploy  把 .NET 10 版宿主复制到本机 Revit（需先关闭 Revit）
  --sign    用 didi 证书（颁发者 tianheluo.com）给 DLL 签名。
            注意：自签名证书不被 Windows/Revit 信任，Revit 会提示"签名无效"，所以默认不签名；
            只有该证书已加入受信任的根证书和受信任的发布者，或换成正规机构颁发的证书时才使用。"""
import os, pathlib, shutil, subprocess, sys

root = pathlib.Path(__file__).resolve().parent.parent
env = dict(os.environ, PYTHONIOENCODING="utf-8")


def run(cmd, cwd=root, **kw):
    print("\n>>", " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, cwd=cwd, env=env, **kw)
    if r.returncode != 0:
        sys.exit(f"失败（退出码 {r.returncode}）")


# 1) 编译（先清掉上次的输出，避免残留已签名的 DLL）
for d in [root / "THL.Host", *(root / "plugins").glob("*")]:
    for sub in ("bin", "obj"):
        shutil.rmtree(d / sub, ignore_errors=True)
shutil.rmtree(root / "_deploytest", ignore_errors=True)
run(["dotnet", "build", "-c", "Release", "-p:RevitAddinsDir=" + str(root / "_deploytest")], cwd=root / "THL.Host")
for proj in ("well-depth", "schedule-excel", "plan-batch", "tag-check", "duct-snap", "duct-route", "mep-hop", "mep-detour", "mep-bundle", "mep-outline", "mep-corridor", "mep-break", "mep-restore", "mep-clash", "mep-continuity"):
    run(["dotnet", "build", "-c", "Release"], cwd=root / "plugins" / proj)

# 2) 签名（可选，默认不签：见上面的说明）
if "--sign" in sys.argv:
    run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "tools" / "sign.ps1")])
else:
    print("(unsigned build: publisher info stays in file properties and the .addin manifest)")

# 3) 打包
for f in (root / "dist").glob("*.thlp"):
    f.unlink()
for script in ("pack.py", "pack_python.py", "pack_sendit.py", "pack_community.py", "export_sources.py", "make_installer.py", "make_host_release.py", "build_site.py", "build_ai.py"):
    run([sys.executable, str(root / "tools" / script)])

# 4) 部署签名后的 .NET 10 版到本机 Revit（可选）
if "--deploy" in sys.argv:
    src = root / "THL.Host/bin/Release/net10.0-windows"
    addins = pathlib.Path(os.environ["APPDATA"]) / "Autodesk/Revit/Addins/2026"
    dest = addins / "THL"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy(src / "THL.addin", addins / "THL.addin")
    for f in list(src.glob("*.dll")) + list(src.glob("*.deps.json")) + list(src.glob("*.pdb")):
        shutil.copy(f, dest / f.name)
    shutil.copytree(src / "lib", dest / "lib")
    print("\n已部署到", dest)
print("\n全部完成")
