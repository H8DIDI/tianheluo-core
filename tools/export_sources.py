"""把 dist/*.thlp 里实际发布的源码展开到 plugin-sources/<id>/，让别人在 GitHub 上能读到每个插件的代码。
内容就是用户安装到的那份（逐字节一致），所以不会出现“仓库里的代码和发布的不一样”。
C#（dll）插件只展开 plugin.json 等文本，C# 源码在 plugins/<目录>/。会生成 plugin-sources/README.md 索引。"""
import json
import pathlib
import shutil
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "plugin-sources"
TEXT_EXT = {".py", ".json", ".md", ".txt", ".xaml", ".csv", ".xml", ".yaml", ".yml", ""}
shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(exist_ok=True)

rows = []
for pkg in sorted((ROOT / "dist").glob("*.thlp")):
    z = zipfile.ZipFile(pkg)
    m = json.loads(z.read("plugin.json"))
    base = OUT / m["id"]
    for info in sorted(z.infolist(), key=lambda i: i.filename):
        name = info.filename
        if info.is_dir() or name.startswith("/") or ".." in pathlib.PurePosixPath(name).parts:
            continue
        ext = pathlib.PurePosixPath(name).suffix.lower()
        if ext not in TEXT_EXT and not name.upper().startswith(("LICENSE", "NOTICE")):
            continue  # dll/pdb/png 等二进制不放进源码目录
        dest = base / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(z.read(info))
    kind = "C#（源码在 `plugins/`）" if m.get("dll") else "Python"
    rows.append((m["id"], m["name"], m["version"], kind, m.get("author") or "didi", m.get("license") or "MIT"))

lines = ["# 插件源码索引", "",
         "这里是**每个插件发布出去的源码**（就是用户装到的那份，由 `tools/export_sources.py` 从发布包展开，不会和线上不一致）。",
         "想学怎么写、想改进某个插件，直接读对应目录里的 `script.py`；`plugin.json` 是它的清单。投稿规范见 <https://tianheluo.com/developers>。", "",
         "| 插件 id | 名称 | 版本 | 类型 | 作者 | 许可 |", "|---|---|---|---|---|---|"]
for r in rows:
    lines.append(f"| [`{r[0]}`]({r[0]}/) | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} |")
(OUT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(len(rows), "个插件的源码已展开到 plugin-sources/")
