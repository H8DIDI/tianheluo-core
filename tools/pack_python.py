"""把 Antigravity.extension 里的 pyRevit 按钮，原样复制打成天河落插件包（不改原文件）。
输出：dist/<id>_<version>.thlp（zip：plugin.json + script.py [+ icon.png]）"""
import json, pathlib, re, ast
import detzip

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = pathlib.Path(r"C:\Users\PCB\Documents\revit_chajian\Antigravity.extension\Antigravity.tab\工具箱.panel")
OUT = pathlib.Path(__file__).resolve().parent.parent / "dist"
VERSION = "0.1.0"
# 单独升级过的插件（其余仍用上面的 VERSION）
VERSION_OVERRIDE = {"well-sheets": "0.2.9", "well-annotate": "0.3.9", "pile-cushion": "0.3.8"}
OUT.mkdir(exist_ok=True)

# 文件夹名 -> (id, 分类, 图标上的字)
PLUGINS = {
    "一键井展开图":       ("well-sheets",     "出图打印", "展"),
    "一键井大样标注":     ("well-annotate",   "出图打印", "标"),
    "一键布置松木桩垫层": ("pile-cushion",    "市政管线", "桩"),
    "清理重复松木桩垫层": ("pile-cleanup",    "市政管线", "清"),
    "一键编号":           ("well-number",     "数据处理", "号"),
    "一键清除井标记":     ("well-clear-marks","数据处理", "除"),
    "强电井清洗合并":     ("elv-merge",       "数据处理", "并"),
    "一键导出井大样DWG":  ("export-dwg",      "导出",     "DWG"),
    "一键导出NWC":        ("export-nwc",      "导出",     "NWC"),
}

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
EXPERIMENTAL = {"一键井大样标注"}

catalog = []
for folder, (pid, cat, glyph) in PLUGINS.items():
    d = SRC / f"{folder}.pushbutton"
    # 仓库里的 python-src/<id>/script.py 优先（可在仓库里直接改规则）；没有就用本机 Antigravity 的原文件
    repo_script = ROOT / "python-src" / pid / "script.py"
    script = repo_script if repo_script.exists() else d / "script.py"
    text = script.read_text(encoding="utf-8")
    for old, new in TEXT_PATCHES.get(folder, []):
        if old not in text: print("  [提示] 文字补丁未命中:", folder, old)
        text = text.replace(old, new)
    desc = doc_of(text) or folder
    if folder in EXPERIMENTAL: desc = "【实验功能，仍在优化】" + desc
    VER = VERSION_OVERRIDE.get(pid, VERSION)
    mf = {"id": pid, "name": folder, "version": VER, "description": desc, "category": cat,
          "revit": "2026", "hostApi": 1, "type": "python", "script": "script.py", "glyph": glyph}
    pkg = OUT / f"{pid}_{VER}.thlp"
    with detzip.create(pkg) as z:
        detzip.writestr(z, "plugin.json", json.dumps(mf, ensure_ascii=False, indent=2))
        detzip.writestr(z, "script.py", text)          # 除界面文字补丁外，逻辑一字不改
        icon = d / "icon.png"
        if icon.exists(): detzip.write(z, icon, "icon.png")
    catalog.append((pid, folder, cat, desc, pkg.stat().st_size))
    print(f"{pkg.name:34} {pkg.stat().st_size/1024:6.1f} KB  {folder}")
json.dump([{"id":a,"name":b,"category":c,"desc":d,"size":e} for a,b,c,d,e in catalog],
          open(OUT / "python_catalog.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
