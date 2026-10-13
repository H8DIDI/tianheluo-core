#!/usr/bin/env python3
"""一键发布：构建 → 测试 → 发布前检查 → 提交推送（网站 + 源码）→ 等待上线 → 核对线上文件。

用法：
    python tools/publish.py -m "更新说明"            # 完整流程
    python tools/publish.py -m "..." --dry-run        # 只构建+检查，不提交不推送
    python tools/publish.py -m "..." --skip-tests     # 跳过单元测试（不推荐）
    python tools/publish.py -m "..." --allow-same-version well-sheets,all   # 仅改了元数据、不想升版本时放行

发布前检查（任何一条不过就停，不会推送）：
  1. 插件内容变了（SHA256 与线上不同）但 version 没升 → 拒绝（用户收不到更新）。
  2. version 比线上还低 → 拒绝。
  3. 宿主同理（host.json 的版本与包的 SHA256）。
  4. 插件声明的 hostApi 高于宿主支持的版本 → 拒绝。
  5. 每个插件包必须含 plugin.json 且 id/version 与清单一致。
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = pathlib.Path(os.environ.get("THL_SITE_DIR", r"C:\KAIFA\tianheluo-web"))
LIVE = "https://tianheluo.com"
GIT = ["git", "-c", "user.name=H8DIDI", "-c", "user.email=jiajiabimer@gmail.com"]
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def run(cmd, cwd=ROOT, check=True, quiet=False):
    r = subprocess.run(cmd, cwd=cwd, env=ENV, capture_output=quiet, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        if quiet:
            print((r.stdout or "")[-3000:], (r.stderr or "")[-3000:])
        sys.exit(f"失败：{' '.join(map(str, cmd))}（退出码 {r.returncode}）")
    return r


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def git_show(path):
    r = run(["git", "show", f"HEAD:{path}"], cwd=SITE, check=False, quiet=True)
    return r.stdout if r.returncode == 0 else None


def host_api_supported():
    src = (ROOT / "THL.Host" / "Store.cs").read_text(encoding="utf-8")
    return int(re.search(r"HostApiVersion\s*=\s*(\d+)", src).group(1))


def check_release(allow):
    errors = []
    old = git_show("plugins.json")
    prev = {p["id"]: p for p in json.loads(old)["plugins"]} if old else {}
    new = json.loads((SITE / "plugins.json").read_text(encoding="utf-8"))["plugins"]
    api = host_api_supported()
    for p in new:
        pid = p["id"]
        pkg = SITE / p["file"].lstrip("/")
        if not pkg.exists():
            errors.append(f"{pid}: 缺少文件 {p['file']}")
            continue
        sha = hashlib.sha256(pkg.read_bytes()).hexdigest()
        if sha != p.get("sha256"):
            errors.append(f"{pid}: 清单里的 sha256 与包不一致（重新运行 build_site.py）")
        try:
            m = json.loads(zipfile.ZipFile(pkg).read("plugin.json"))
            if m.get("id") != pid or m.get("version") != p["version"]:
                errors.append(f"{pid}: 包内 plugin.json 的 id/version 与清单不一致")
            if int(m.get("hostApi", 1)) > api:
                errors.append(f"{pid}: 需要 hostApi {m.get('hostApi')}，当前宿主只支持 {api}")
        except Exception as e:
            errors.append(f"{pid}: 包无法读取（{e}）")
        o = prev.get(pid)
        if o:
            if version_tuple(p["version"]) < version_tuple(o["version"]):
                errors.append(f"{pid}: 版本从 {o['version']} 降到 {p['version']}")
            elif p["version"] == o["version"] and o.get("sha256") and o["sha256"] != sha and not ({"all", pid} & allow):
                errors.append(f"{pid}: 内容变了但版本号仍是 {p['version']}——用户收不到更新。请升版本（或用 --allow-same-version {pid} 放行）")
            elif p["version"] == o["version"] and not o.get("sha256") and not ({"all", pid} & allow):
                # 线上旧清单没有 sha256：无法比较，只提示
                print(f"  [提示] {pid}: 线上清单无校验值，本次建立基线")
    # 宿主
    host = json.loads((SITE / "host.json").read_text(encoding="utf-8"))
    old_host = git_show("host.json")
    if old_host:
        oh = json.loads(old_host)
        if version_tuple(host["version"]) < version_tuple(oh["version"]):
            errors.append(f"宿主版本从 {oh['version']} 降到 {host['version']}")
        elif host["version"] == oh["version"]:
            for k, a in host["assets"].items():
                if oh["assets"].get(k, {}).get("sha256") not in (None, a["sha256"]) and not ({"all", "host"} & allow):
                    errors.append(f"宿主 {k}: 内容变了但版本号仍是 {host['version']}——已安装的宿主收不到更新。请在 THL.Host.csproj 升版本")
    for k, a in host["assets"].items():
        f = SITE / a["file"].lstrip("/")
        if not f.exists() or hashlib.sha256(f.read_bytes()).hexdigest() != a["sha256"]:
            errors.append(f"宿主 {k}: 包缺失或 sha256 不一致")
    return errors


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "THL-publish/1.0"})
    return urllib.request.urlopen(req, timeout=60).read()


def verify_live(timeout=300):
    host = json.loads((SITE / "host.json").read_text(encoding="utf-8"))
    plugins = json.loads((SITE / "plugins.json").read_text(encoding="utf-8"))["plugins"]
    targets = [(f"宿主 {k}", a["file"], a["sha256"]) for k, a in host["assets"].items()]
    targets += [(p["id"], p["file"], p["sha256"]) for p in plugins]
    deadline = time.time() + timeout
    pending = list(targets)
    while pending and time.time() < deadline:
        still = []
        for name, f, sha in pending:
            try:
                ok = hashlib.sha256(http_get(f"{LIVE}{f}?_={int(time.time())}")).hexdigest() == sha
            except Exception:
                ok = False
            if not ok:
                still.append((name, f, sha))
        pending = still
        if pending:
            print(f"  等待上线：还有 {len(pending)} 个文件与线上不一致…")
            time.sleep(15)
    return pending


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--message", required=True, help="本次发布的说明（用作 git 提交说明）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--allow-same-version", default="", help="逗号分隔的插件 id，或 all / host")
    a = ap.parse_args()
    allow = {x.strip() for x in a.allow_same_version.split(",") if x.strip()}

    print("== 1/5 构建 ==")
    run([sys.executable, "tools/build_all.py"], quiet=True)
    run([sys.executable, str(SITE / "tools" / "build_seo.py")], quiet=True)  # 插件详情页、导航页脚、sitemap
    if not a.skip_tests:
        print("== 2/5 单元测试 ==")
        for t in ("tests/StoreTests", "tests/UpdateTests"):
            r = run(["dotnet", "run", "--project", t], quiet=True)
            if "ALL PASS" not in r.stdout:
                sys.exit(f"测试未全部通过：{t}\n{r.stdout[-1500:]}")
            print("  通过：", t)
        r = run([sys.executable, "tests/test_apply_update.py"], quiet=True)
        if "ALL PASS" not in r.stdout:
            sys.exit("应用更新脚本的测试未通过\n" + r.stdout[-1500:])
        print("  通过： tests/test_apply_update.py")
    print("== 3/5 发布前检查 ==")
    errs = check_release(allow)
    if errs:
        print("发布前检查未通过：")
        for e in errs:
            print("  ✗", e)
        sys.exit(1)
    print("  全部通过")
    if a.dry_run:
        print("（--dry-run：到此为止，未提交）")
        return

    print("== 4/5 提交并推送 ==")
    for repo, name in ((SITE, "网站"), (ROOT, "源码")):
        run(["git", "add", "-A"], cwd=repo)
        if run(["git", "diff", "--cached", "--quiet"], cwd=repo, check=False).returncode == 0:
            print(f"  {name}：没有改动")
            continue
        run(GIT + ["commit", "-q", "-m", a.message + "\n\nCo-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>"], cwd=repo)
        run(["git", "push", "-q", "origin", "HEAD"], cwd=repo)
        print(f"  {name}：已推送")
    print("== 5/5 核对线上文件（SHA256）==")
    bad = verify_live()
    if bad:
        for name, f, _ in bad:
            print("  ✗ 线上与本地不一致：", name, f)
        sys.exit("线上文件在 5 分钟内没有更新成功。Cloudflare Pages 有时会发布失败，请到控制台重试部署后再运行本脚本核对。")
    print("  线上文件全部与本地一致。发布完成。")


if __name__ == "__main__":
    main()
