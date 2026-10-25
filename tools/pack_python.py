"""把 Antigravity.extension 里的 pyRevit 按钮，原样复制打成天河落插件包（不改原文件）。
输出：dist/<id>_<version>.thlp（zip：plugin.json + script.py [+ icon.png]）"""
import json, pathlib, re, ast
import detzip

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = pathlib.Path(r"C:\Users\PCB\Documents\revit_chajian\Antigravity.extension\Antigravity.tab\工具箱.panel")
OUT = pathlib.Path(__file__).resolve().parent.parent / "dist"
VERSION = "0.1.0"
# 单独升级过的插件（其余仍用上面的 VERSION）
VERSION_OVERRIDE = {"well-sheets": "0.5.10", "well-annotate": "0.3.31", "well-zone": "0.1.3", "well-sheet-no": "0.1.5", "well-sheet-fit": "0.1.1", "well-spec-table": "0.1.1", "pile-cushion": "0.4.0", "pile-cleanup": "0.2.0",
                    "quantity-export": "0.2.1", "rebar-cage": "0.2.1", "sheet-organize": "0.6.4", "export-dwg": "0.4.5",
                    "export-nwc": "0.2.0", "well-number": "0.2.0", "well-clear-marks": "0.1.1", "elv-merge": "0.2.0",
                    "control-points": "0.1.0", "well-depth-report": "0.1.4"}
# 商店里显示的名字改过的插件（文件夹名不变，图标照旧从原文件夹取）
NAME_OVERRIDE = {"elv-merge": "族类型清洗合并", "export-dwg": "一键导出图纸DWG/PDF"}
OUT.mkdir(exist_ok=True)

# 文件夹名 -> (id, 分类, 图标上的字)
PLUGINS = {
    "一键井展开图":       ("well-sheets",     "出图打印", "展"),
    "一键井大样标注":     ("well-annotate",   "出图打印", "标"),
    "一键布置松木桩垫层": ("pile-cushion",    "市政管线", "桩"),
    "清理重复松木桩垫层": ("pile-cleanup",    "市政管线", "清"),
    "一键编号":           ("well-number",     "数据处理", "号"),
    "井分区标记":         ("well-zone",       "数据处理", "区"),
    "井图号重编":         ("well-sheet-no",   "出图打印", "号"),
    "井图比例排版":       ("well-sheet-fit",  "出图打印", "比"),
    "井规格表":           ("well-spec-table", "出图打印", "规"),
    "一键清除井标记":     ("well-clear-marks","数据处理", "除"),
    "强电井清洗合并":     ("elv-merge",       "数据处理", "并"),
    "一键框选提量导出Excel": ("quantity-export", "数据处理", "量"),
    "一键图纸归类与图框编号": ("sheet-organize", "出图打印", "归"),
    "一键钢筋三维实体显示": ("rebar-3d-view", "数据处理", "筋"),
    "一键单构件快速配筋": ("rebar-cage", "数据处理", "配"),
    "一键导出井大样DWG":  ("export-dwg",      "导出",     "DWG"),
    "一键导出NWC":        ("export-nwc",      "导出",     "NWC"),
    "控制点坐标表":       ("control-points",  "市政管线", "点"),
    "井深统计报表":       ("well-depth-report", "市政管线", "深"),
    "图纸快速预览":       ("sheet-preview",   "出图打印", "览"),
}

INCLUDE_RE = re.compile(r"^# @include (\S+)\s*$", re.M)


def expand_includes(text):
    """把脚本里的 `# @include _shared/xxx.py` 换成 python-src/_shared/xxx.py 的内容（插件包只有一个 script.py）。"""
    def rep(m):
        f = ROOT / "python-src" / m.group(1)
        return f.read_text(encoding="utf-8").rstrip() + "\n"
    return INCLUDE_RE.sub(rep, text)


def doc_of(script_text):
    """取脚本里的 __doc__ 字符串作为简介。"""
    try:
        tree = ast.parse(script_text)
        for n in tree.body:
            if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "__doc__" for t in n.targets):
                return ast.literal_eval(n.value).strip()
    except Exception:
        pass
    return ""

# 打包副本里的界面文字修正：(文件夹名) -> [(旧文字, 新文字)]。只改文字，不动逻辑。
TEXT_PATCHES = {
    "一键井展开图": [("正在批量生成井大样图及尺寸标注...", "正在批量生成井大样图...")],
}
# 标注类功能仍在优化，商店里标为实验功能
EXPERIMENTAL = {"一键井大样标注", "图纸快速预览"}

catalog = []
for folder, (pid, cat, glyph) in PLUGINS.items():
    d = SRC / f"{folder}.pushbutton"
    # 仓库里的 python-src/<id>/script.py 优先（可在仓库里直接改规则）；没有就用本机 Antigravity 的原文件
    repo_script = ROOT / "python-src" / pid / "script.py"
    script = repo_script if repo_script.exists() else d / "script.py"
    text = expand_includes(script.read_text(encoding="utf-8"))
    for old, new in TEXT_PATCHES.get(folder, []):
        if old not in text: print("  [提示] 文字补丁未命中:", folder, old)
        text = text.replace(old, new)
    desc = doc_of(text) or folder
    if folder in EXPERIMENTAL: desc = "【实验功能，仍在优化】" + desc
    VER = VERSION_OVERRIDE.get(pid, VERSION)
    mf = {"id": pid, "name": NAME_OVERRIDE.get(pid, folder), "version": VER, "description": desc, "category": cat,
          "revit": "2026", "hostApi": 1, "type": "python", "script": "script.py", "glyph": glyph}
    pkg = OUT / f"{pid}_{VER}.thlp"
    with detzip.create(pkg) as z:
        detzip.writestr(z, "plugin.json", json.dumps(mf, ensure_ascii=False, indent=2))
        detzip.writestr(z, "script.py", text)          # 除界面文字补丁外，逻辑一字不改
        icon = d / "icon.png"
        if icon.exists(): detzip.write(z, icon, "icon.png")
    catalog.append((pid, NAME_OVERRIDE.get(pid, folder), cat, desc, pkg.stat().st_size))
    print(f"{pkg.name:34} {pkg.stat().st_size/1024:6.1f} KB  {folder}")
json.dump([{"id":a,"name":b,"category":c,"desc":d,"size":e} for a,b,c,d,e in catalog],
          open(OUT / "python_catalog.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
