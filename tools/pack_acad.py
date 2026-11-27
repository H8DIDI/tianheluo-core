r"""天河落 AutoCAD：编译宿主 + 插件并打包到 dist-acad/。

  python tools/pack_acad.py            # 编译 + 打包
  python tools/pack_acad.py --install  # 再装到本机（宿主 → %AppData%\Autodesk\ApplicationPlugins\THL.bundle，
                                       #             插件 → %AppData%\Tianheluo\AutoCAD\plugins\<id>）。AutoCAD 要先关掉。

产物：
  dist-acad/THL.bundle/                 自动加载包（PackageContents.xml + Contents/net48 + Contents/net8）
  dist-acad/THL-Acad-Host_<ver>.zip     同上内容（zip 根 = bundle 根），宿主自更新包 / 手动安装用
  dist-acad/<id>_<ver>.thlp             插件包：plugin.json + net48/ + net8/
  dist-acad/install-acad.ps1            安装脚本（复制 THL.bundle、装 dist-acad 里的插件）
所有 zip 用 detzip 固定时间戳：同样的内容得到逐字节相同的包。
"""
import io
import json
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import detzip  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
DIST = ROOT / "dist-acad"
HOST = ROOT / "THL.Acad"
PLUGINS = ROOT / "plugins-acad"
FX = {"net48": "net48", "net8": "net8.0-windows"}


def build(proj):
    r = subprocess.run(["dotnet", "build", str(proj), "-c", "Release", "-nologo", "-v", "q"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-4000:], r.stderr[-2000:])
        sys.exit(f"编译失败：{proj}")


def files_of(out_dir):
    return sorted(p for p in pathlib.Path(out_dir).iterdir() if p.is_file() and p.suffix.lower() in (".dll", ".pdb", ".json") and not p.name.endswith(".runtimeconfig.json"))


def host_version():
    return re.search(r"<InformationalVersion>([^<]+)<", (HOST / "THL.Acad.csproj").read_text(encoding="utf-8")).group(1)


def pack_host():
    build(HOST / "THL.Acad.csproj")
    ver = host_version()
    pc = (HOST / "PackageContents.xml").read_text(encoding="utf-8")
    pc = re.sub(r'AppVersion="[^"]*"', f'AppVersion="{ver}"', pc, count=1)
    bundle = DIST / "THL.bundle"
    if bundle.exists():
        shutil.rmtree(bundle)
    entries = [("PackageContents.xml", pc.encode("utf-8"))]
    for short, tfm in FX.items():
        for f in files_of(HOST / "bin" / "Release" / tfm):
            entries.append((f"Contents/{short}/{f.name}", f.read_bytes()))
    for name, data in entries:
        p = bundle / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    z = DIST / f"THL-Acad-Host_{ver}.zip"
    with detzip.create(z) as zf:
        for name, data in entries:
            detzip.writestr(zf, name, data)
    print(f"宿主 v{ver}：{bundle}  {z.name}")
    return ver


def pack_plugin(d):
    m = json.loads((d / "plugin.json").read_text(encoding="utf-8"))
    proj = next(d.glob("*.csproj"))
    build(proj)
    pkg = DIST / f'{m["id"]}_{m["version"]}.thlp'
    with detzip.create(pkg) as zf:
        detzip.write(zf, d / "plugin.json", "plugin.json")
        if (d / "icon.png").exists():
            detzip.write(zf, d / "icon.png", "icon.png")
        for short, tfm in FX.items():
            out = d / "bin" / "Release" / tfm
            dlls = files_of(out)
            if not any(f.name == m["dll"] for f in dlls):
                sys.exit(f"{m['id']}：{out} 里没有 {m['dll']}")
            for f in dlls:
                detzip.write(zf, f, f"{short}/{f.name}")
    print(f"插件 {m['id']} v{m['version']}：{pkg.name}")
    return pkg


def main():
    DIST.mkdir(exist_ok=True)
    pack_host()
    pkgs = [pack_plugin(d) for d in sorted(PLUGINS.iterdir()) if (d / "plugin.json").exists()]
    shutil.copy2(ROOT / "setup" / "install-acad.ps1", DIST / "install-acad.ps1")
    if "--install" in sys.argv:
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(DIST / "install-acad.ps1"),
                            "-Bundle", str(DIST / "THL.bundle"), "-Plugins", str(DIST)])
        sys.exit(r.returncode)


if __name__ == "__main__":
    main()
