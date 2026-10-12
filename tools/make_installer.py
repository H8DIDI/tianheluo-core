"""打包客户用的宿主安装包 dist/THL-Host_<ver>.zip（解压后双击 安装.bat）。
同时包含 .NET 8 版（Revit 2026.0~2026.4）和 .NET 10 版（Revit 2026.5+），安装脚本自动选择。"""
import zipfile, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
out = root / "dist/THL-Host_0.1.0.zip"
BUILDS = {"net8": "net8.0-windows", "net10": "net10.0-windows"}
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for key, tfm in BUILDS.items():
        rel = root / "THL.Host/bin/Release" / tfm
        z.write(rel / "THL.addin", f"{key}/THL.addin")
        for f in list(rel.glob("*.dll")) + list(rel.glob("*.deps.json")):
            z.write(f, f"{key}/THL/{f.name}")
        lib = rel / "lib"
        for f in lib.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, f"{key}/THL/lib/{f.relative_to(lib).as_posix()}")
    for n in ("install.bat", "install.ps1", "uninstall.bat", "uninstall.ps1", "README.txt"): z.write(root / "setup" / n, n)
print(out.name, round(out.stat().st_size / 1024 / 1024, 1), "MB")
