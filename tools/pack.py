"""把每个插件打成 dist/<id>_<version>.thlp（zip：plugin.json + dll + pdb）。
用法：python tools/pack.py   （先对每个插件执行 dotnet build -c Release）"""
import json, pathlib, sys
import detzip
root = pathlib.Path(__file__).resolve().parent.parent
dist = root / "dist"; dist.mkdir(exist_ok=True)
for mf in sorted((root / "plugins").glob("*/plugin.json")):
    d = mf.parent
    m = json.loads(mf.read_text(encoding="utf-8"))
    out_dir = d / "bin" / "Release" / "net8.0-windows"
    # 目录里的 DLL 被 Revit 占用时，退而用备用输出目录里较新的那份
    alt = root / "_deploytest" / d.name
    # 固定优先用正式输出目录（可复现）；只有它不存在时才退而用备用目录
    cands = [x for x in (out_dir / m["dll"], alt / m["dll"]) if x.exists()]
    dll = cands[0] if cands else out_dir / m["dll"]
    if not dll.exists():
        print("缺少", dll, "- 请先 dotnet build -c Release"); sys.exit(1)
    pkg = dist / f'{m["id"]}_{m["version"]}.thlp'
    with detzip.create(pkg) as z:
        detzip.write(z, mf, "plugin.json")
        detzip.write(z, dll, m["dll"])
        pdb = dll.with_suffix(".pdb")
        if pdb.exists(): detzip.write(z, pdb, pdb.name)
        # 带独立求解进程的插件（如 duct-route）：把 worker-out/ 整个放进包里的 worker/ 目录
        wo = d / "worker-out"
        if wo.is_dir():
            for f in sorted(x for x in wo.rglob("*") if x.is_file() and x.suffix != ".pdb"):
                detzip.write(z, f, "worker/" + f.relative_to(wo).as_posix())
    print(f'{pkg.name}  {pkg.stat().st_size/1024:.1f} KB')
