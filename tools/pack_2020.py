"""把 python-src/<id>/script.py 展开 include 后打成 Revit 2020 插件包（plugin.json 的 revit="2020"）。
用法：python tools/pack_2020.py [--install]   输出 dist2020/<id>_<version>.thlp；--install 同时解到本机 %AppData%\Tianheluo\Revit2020\plugins\<id>
"""
import json, os, pathlib, re, sys, ast, hashlib
import detzip

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "dist2020"
INCLUDE_RE = re.compile(r"^# @include (\S+)\s*$", re.M)
# id -> (名称, 分类, 版本, 图标字, 适用 Revit)
PLUGINS = {
    "hanger-auto": ("支吊架一键布置", "管线调整", "0.1.0", "架", "2020"),
    # 以下从 2026 版移植（脚本里用 compat / compat2020 做新旧 API 兼容）；2020 里先标 beta，用顺了再升
    "quantity-export": ("框选提量导出Excel", "结构与算量", "0.4.3", "量", "2020"),
    "sheet-organize": ("图纸归类与图框编号", "图纸视图", "0.6.6", "归", "2020"),
    "export-dwg": ("一键导出图纸DWG", "导出", "0.7.1", "DWG", "2020"),
    "export-nwc": ("一键导出NWC", "导出", "0.3.0", "NWC", "2020"),
    "rfi-pack": ("RFI 取证包", "图纸视图", "0.1.0", "证", "2020"),
    "sheet-preview": ("图纸快速预览", "图纸视图", "0.1.1", "览", "2020"),
    "control-points": ("控制点坐标表", "图纸视图", "0.1.2", "点", "2020"),
    "mep-clean": ("管线清理（去重叠/悬空管件）", "管线调整", "0.1.0", "清", "2020,2026"),
    "mep-audit": ("机电模型体检", "管线调整", "0.1.0", "检", "2020,2026"),
    "cad-align": ("CAD 链接自动对位", "图纸视图", "0.1.0", "位", "2020,2026"),
    "mep-vt-kit": ("机电过滤器与视图样板", "管线调整", "0.1.0", "板", "2020,2026"),
    "cad-to-mep": ("CAD 线生成管线", "管线调整", "0.1.0", "线", "2020,2026"),
    "tag-tidy": ("构件标记一键整理", "图纸视图", "0.1.9", "整", "2020"),
    "rebar-cage": ("一键单构件快速配筋", "结构与算量", "0.3.1", "配", "2020"),
}


def expand(text):
    return INCLUDE_RE.sub(lambda m: (ROOT / "python-src" / m.group(1)).read_text(encoding="utf-8").rstrip() + "\n", text)


def doc_of(text):
    try:
        for n in ast.parse(text).body:
            if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "__doc__" for t in n.targets):
                return ast.literal_eval(n.value).strip()
    except Exception:
        pass
    return ""


def pack_dotnet(catalog, install):
    """C# 插件：按 Revit 2020（net472）编译，打成 2020 包（plugin.json 的 revit 改成 2020）。"""
    import subprocess, shutil
    for mf in sorted((ROOT / "plugins").glob("*/plugin.json")):
        d = mf.parent
        m = json.loads(mf.read_text(encoding="utf-8-sig"))
        r = subprocess.run(["dotnet", "build", "-c", "Release", "-p:Revit2020=1", "-v:q", "-nologo"], cwd=d, capture_output=True, text=True, encoding="utf-8", errors="replace")
        dll = d / "bin2020" / "Release" / "net472" / m["dll"]
        if r.returncode != 0 or not dll.exists():
            print("  [跳过] 2020 编译失败：", m["id"])
            continue
        m["revit"] = "2020"
        pkg = OUT / f'{m["id"]}_{m["version"]}.thlp'
        with detzip.create(pkg) as z:
            detzip.writestr(z, "plugin.json", json.dumps(m, ensure_ascii=False, indent=2))
            detzip.write(z, dll, m["dll"])
            pdb = dll.with_suffix(".pdb")
            if pdb.exists():
                detzip.write(z, pdb, pdb.name)
            wo = d / "worker-out"        # 独立求解进程（.NET 8 程序，单独运行，不受 Revit 版本影响）
            if wo.is_dir():
                for f in sorted(x for x in wo.rglob("*") if x.is_file() and x.suffix != ".pdb"):
                    detzip.write(z, f, "worker/" + f.relative_to(wo).as_posix())
        print(pkg.name, f"{pkg.stat().st_size/1024:.1f} KB")
        catalog.append({"id": m["id"], "name": m["name"], "version": m["version"], "summary": m.get("description", m["name"]).splitlines()[0],
                        "category": m.get("category", ""), "status": "beta", "download": pkg.name, "glyph": m.get("glyph", ""),
                        "color": m.get("color", ""), "revit": "2020", "hostApi": m.get("hostApi", 1),
                        "sha256": hashlib.sha256(pkg.read_bytes()).hexdigest(), "tier": "official"})
        if install:
            dst = pathlib.Path(os.environ["APPDATA"]) / "Tianheluo" / "Revit2020" / "plugins" / m["id"]
            if dst.exists():
                shutil.rmtree(dst)
            import zipfile
            zipfile.ZipFile(pkg).extractall(dst)


def main():
    OUT.mkdir(exist_ok=True)
    install = "--install" in sys.argv
    catalog = []
    for pid, (name, cat, ver, glyph, revit) in PLUGINS.items():
        text = expand((ROOT / "python-src" / pid / "script.py").read_text(encoding="utf-8"))
        ast.parse(text)   # 语法自检
        mf = {"id": pid, "name": name, "version": ver, "description": doc_of(text) or name, "category": cat,
              "revit": revit, "hostApi": 1, "type": "python", "script": "script.py", "glyph": glyph}
        pkg = OUT / f"{pid}_{ver}.thlp"
        with detzip.create(pkg) as z:
            detzip.writestr(z, "plugin.json", json.dumps(mf, ensure_ascii=False, indent=2))
            detzip.writestr(z, "script.py", text)
        print(pkg.name, f"{pkg.stat().st_size/1024:.1f} KB")
        catalog.append({"id": pid, "name": name, "version": ver, "summary": (doc_of(text) or name).splitlines()[0], "category": cat,
                        "status": ("tested" if pid == "hanger-auto" else "beta"), "download": pkg.name, "glyph": glyph, "revit": revit, "hostApi": 1,
                        "sha256": hashlib.sha256(pkg.read_bytes()).hexdigest(), "tier": "official"})
        if install:
            d = pathlib.Path(os.environ["APPDATA"]) / "Tianheluo" / "Revit2020" / "plugins" / pid
            d.mkdir(parents=True, exist_ok=True)
            (d / "plugin.json").write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
            (d / "script.py").write_text(text, encoding="utf-8")
            print("  installed ->", d)
    if "--no-dotnet" not in sys.argv:
        pack_dotnet(catalog, install)
    (OUT / "plugins2020.json").write_text(json.dumps({"plugins": catalog}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("catalog ->", OUT / "plugins2020.json")
    if install:   # 本机 2020 宿主的商店目录指向它（上线后改成网站地址）
        cfg = pathlib.Path(os.environ["APPDATA"]) / "Tianheluo" / "Revit2020" / "config.json"
        c = json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}
        c["catalogUrl"] = str(OUT / "plugins2020.json")
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
        print("config catalogUrl ->", c["catalogUrl"])


if __name__ == "__main__":
    main()
