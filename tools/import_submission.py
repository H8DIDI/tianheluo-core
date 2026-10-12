"""维护者工具：审核并导入别人邮件投稿来的插件（zip 或目录）。

用法：
    python tools/import_submission.py <zip或目录>              # 只检查并预览，不改任何东西
    python tools/import_submission.py <zip或目录> --approve    # 审核通过：写入 community/<id>/，之后运行 build_all.py 上架

流程：自动校验 → 打印“审核报告”（文件清单、联网/写文件提示、与旧版的差异）→ 你人工读完源码 → 加 --approve。
校验脚本只能拦住明显问题；最终安全责任在人工审核。社区插件源码保存在 community/ 目录，随宿主源码一起备份。
"""
import difflib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMMUNITY = pathlib.Path(os.environ.get("THL_COMMUNITY_DIR", ROOT / "community"))
VALIDATE = pathlib.Path(os.environ.get("THL_VALIDATE", r"C:\KAIFA\tianheluo-web\tools\validate.py"))


def say(msg=""):
    print(msg)


def extract(src):
    """返回 (插件根目录, 临时目录)。"""
    src = pathlib.Path(src)
    if src.is_dir():
        return src, None
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="thl-submission-"))
    with zipfile.ZipFile(src) as z:
        base = tmp.resolve()
        for n in z.namelist():
            if not (tmp / n).resolve().is_relative_to(base):
                sys.exit("zip 含非法路径，拒绝：" + n)
        z.extractall(tmp)
    if (tmp / "plugin.json").exists():
        return tmp, tmp
    subs = [p for p in tmp.iterdir() if p.is_dir() and not p.name.startswith("__MACOSX")]
    if len(subs) == 1 and (subs[0] / "plugin.json").exists():
        return subs[0], tmp
    sys.exit("找不到 plugin.json（应在 zip 根目录或唯一的一层目录里）")


def version_tuple(v):
    return tuple(int(x) for x in v.split("."))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    approve = "--approve" in sys.argv
    if not args:
        print(__doc__)
        sys.exit(2)

    root, tmp = extract(args[0])
    m = json.loads((root / "plugin.json").read_text(encoding="utf-8-sig"))
    pid = m.get("id", "?")

    say("=" * 64)
    say(f"投稿：{m.get('name')}（{pid}）v{m.get('version')} · 作者 {m.get('author')} · 许可 {m.get('license')}")
    say("=" * 64)

    # 1) 自动校验
    r = subprocess.run([sys.executable, str(VALIDATE), str(root)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    say(r.stdout.rstrip())
    if r.returncode != 0:
        say("\n>>> 自动校验未通过，请把上面的问题回复给投稿人修改。")
        sys.exit(1)

    # 2) 审核报告
    say("\n【审核报告】")
    total = 0
    for p in sorted(root.rglob("*")):
        if p.is_file():
            total += p.stat().st_size
            say(f"  {p.relative_to(root).as_posix():40} {p.stat().st_size:>7} B")
    say(f"  合计 {total} B")
    say(f"  声明联网(network): {m.get('network', False)}")

    old = COMMUNITY / pid
    if old.exists():
        old_m = json.loads((old / "plugin.json").read_text(encoding="utf-8"))
        say(f"\n  这是更新：旧版 v{old_m['version']} → 新版 v{m['version']}")
        if version_tuple(m["version"]) <= version_tuple(old_m["version"]):
            say("  >>> 版本号没有递增，请让投稿人修改 version。")
            sys.exit(1)
        for f in sorted(root.rglob("*.py")):
            rel = f.relative_to(root)
            a = (old / rel).read_text(encoding="utf-8").splitlines() if (old / rel).exists() else []
            b = f.read_text(encoding="utf-8").splitlines()
            diff = list(difflib.unified_diff(a, b, "旧版/" + rel.as_posix(), "新版/" + rel.as_posix(), lineterm="", n=2))
            say(f"\n  --- {rel.as_posix()} 改动 {sum(1 for d in diff if d[:1] in '+-' and d[:3] not in ('+++', '---'))} 行")
            for d in diff[:80]:
                say("  " + d)
    else:
        say(f"\n  这是新插件（尚未在 community/ 中）。请通读 script.py 与 README.md：")
        for f in sorted(root.rglob("*.py")):
            say(f"    • {f}")

    say("\n【人工审核清单】请逐条确认：")
    for item in ("功能与 README 描述一致，没有隐藏行为",
                 "写模型时：单事务 + 预览确认 + 可撤销",
                 "没有执行外部程序/动态代码/未声明联网（校验器已扫，仍需肉眼看一遍）",
                 "只在 script.get_*_data_file() 路径下写文件",
                 "许可证文件真实，作者署名合理，没有抄袭他人闭源代码"):
        say("  [ ] " + item)

    if not approve:
        say("\n>>> 这只是预览。确认无误后加 --approve 导入。")
        return

    # 3) 导入
    dest = COMMUNITY / pid
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(root, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (dest / ".submission.json").write_text(json.dumps({
        "received": date.today().isoformat(), "author": m.get("author"), "version": m.get("version"),
        "reviewed_by": "maintainer"}, ensure_ascii=False, indent=2), encoding="utf-8")
    say(f"\n✓ 已导入 {dest}")
    say("下一步：python tools/build_all.py   （打包进商店清单），然后推送网站。记得回复投稿人。")
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
