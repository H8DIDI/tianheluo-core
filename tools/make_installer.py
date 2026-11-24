"""打包客户用的宿主安装包 dist/THL-Host_<ver>.zip（解压后双击 安装.bat）。
同时包含 .NET 8 版（Revit 2026.0~2026.4）和 .NET 10 版（Revit 2026.5+），安装脚本自动选择。"""
import pathlib
import detzip
root = pathlib.Path(__file__).resolve().parent.parent
out = root / "dist/THL-Host.zip"
BUILDS = {"net8": "net8.0-windows", "net10": "net10.0-windows"}
# Revit 2020 版宿主（net472）在单独的项目里编译
BUILD_DIRS = {"net472": root / "THL.Host2020/bin/Release/net472"}
with detzip.create(out) as z:
    for key, rel in [(k, root / "THL.Host/bin/Release" / t) for k, t in BUILDS.items()] + list(BUILD_DIRS.items()):
        detzip.write(z, rel / "THL.addin", f"{key}/THL.addin")
        for f in sorted(list(rel.glob("*.dll")) + list(rel.glob("*.deps.json")) + list(rel.glob("THL.Host.pdb"))):
            detzip.write(z, f, f"{key}/THL/{f.name}")
        lib = rel / "lib"
        for f in sorted(lib.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                detzip.write(z, f, f"{key}/THL/lib/{f.relative_to(lib).as_posix()}")
    for n in ("install.bat", "install.ps1", "uninstall.bat", "uninstall.ps1", "README.txt"): detzip.write(z, root / "setup" / n, n)
print(out.name, round(out.stat().st_size / 1024 / 1024, 1), "MB")
