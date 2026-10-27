"""把 Sendit（MIT，作者 lewismconte）的三个工具各打成一个天河落插件包。
每个包都带 sendit/ 公共库、原作者的 MIT 许可和署名。输出 dist/<id>_<ver>.thlp"""
import json, pathlib
import detzip

SRC = pathlib.Path(r"C:\Users\PCB\AppData\Roaming\pyRevit\Extensions\Sendit.extension")
# 仓库里的副本（python-src/sendit）优先：在仓库里改了，就打进插件；没有时才用本机 pyRevit 的原文件。
REPO = pathlib.Path(__file__).resolve().parent.parent / "python-src" / "sendit"
OUT = pathlib.Path(__file__).resolve().parent.parent / "dist"
OUT.mkdir(exist_ok=True)
VERSION = "0.2.1"
LICENSE = ((REPO if (REPO / "LICENSE").exists() else SRC) / "LICENSE").read_text(encoding="utf-8")
NOTICE = ("本插件基于开源项目 Sendit，作者 lewismconte，MIT 许可。\n"
          "原项目：https://github.com/lewismconte/sendit\n"
          "天河落仅提供运行环境适配，工具本体代码未做功能修改。\n")

# 文件夹名 -> (id, 中文名, 分类, 图标字, 简介)
TOOLS = {
    "Sheet Manager": ("sheet-manager", "图纸管理", "出图打印", "图",
        "可编辑的图纸表格：批量改图号/图名、查找替换、从 CSV 批量创建、复制、删除、修订管理、图纸集，以及按文件名模板导出 PDF/CSV。"),
    "View Manager": ("view-manager", "视图管理", "出图打印", "视",
        "批量管理视图：筛选、重命名、套用视图样板、批量设比例/详细程度/专业、查找替换、定位视图、找出没上图纸的视图并清理；视图样板可导出/导入 JSON，在不同项目间复用。"),
    "Issue Manager": ("issue-manager", "图纸签发管理", "出图打印", "发",
        "图纸签发与归档：把签发的图纸导出成 PDF 放进带日期的文件夹，可同时导出 DWG，维护签发记录 CSV，显示图纸与签发的对照矩阵，并在 Revit 里生成签发记录明细表。"),
}

PLUGIN_MAP = json.loads((pathlib.Path(__file__).resolve().parent / "plugin_map.json").read_text(encoding="utf-8"))["plugins"]
for folder, (pid, name, cat, glyph, desc) in TOOLS.items():
    cat = PLUGIN_MAP.get(pid, {}).get("group", cat)
    repo_tool = REPO / "tools" / folder
    d = repo_tool if (repo_tool / "script.py").exists() else SRC / "Sendit.tab" / "Tools.panel" / f"{folder}.pushbutton"
    mf = {"id": pid, "name": name, "version": VERSION, "description": desc, "category": cat, "revit": "2026",
          "hostApi": 1, "type": "python", "script": "script.py", "glyph": glyph,
          "author": "lewismconte (Sendit)", "license": "MIT", "homepage": "https://github.com/lewismconte/sendit"}
    pkg = OUT / f"{pid}_{VERSION}.thlp"
    with detzip.create(pkg) as z:
        detzip.writestr(z, "plugin.json", json.dumps(mf, ensure_ascii=False, indent=2))
        detzip.write(z, d / "script.py", "script.py")
        lib = REPO / "lib" / "sendit" if (REPO / "lib" / "sendit").exists() else SRC / "lib" / "sendit"
        for f in sorted(lib.glob("*.py")):
            detzip.write(z, f, f"sendit/{f.name}")
        if (d / "icon.png").exists():
            detzip.write(z, d / "icon.png", "icon.png")
        detzip.writestr(z, "LICENSE-Sendit.txt", LICENSE)
        detzip.writestr(z, "NOTICE.txt", NOTICE)
    print(f"{pkg.name:28} {pkg.stat().st_size/1024:6.1f} KB  {name}")
