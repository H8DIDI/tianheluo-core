"""apply-update.ps1 的测试：用真实更新包内容，验证 成功替换 / 自检失败回滚 / 文件被占用 / 内容不完整 / 等待超时。
用法：python tests/test_apply_update.py"""
import json, os, pathlib, shutil, subprocess, sys, tempfile, time, zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "THL.Host" / "apply-update.ps1"
REAL_ZIP = next((pathlib.Path(r"C:\KAIFA\tianheluo-web\dl\host")).glob("THL-Host-net10_*.zip"))
VERSION = REAL_ZIP.stem.split("_")[-1]          # 例如 0.2.0
fail = 0


def check(name, ok, extra=""):
    global fail
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else "  " + str(extra)))
    fail += not ok


def sandbox():
    """返回 (根目录, Target, Staging, Backup, Pending, Log)；Target 里放一个“旧版宿主”。"""
    base = pathlib.Path(tempfile.mkdtemp(prefix="thl-apply-"))
    target = base / "Addins" / "2026"
    (target / "THL").mkdir(parents=True)
    (target / "THL" / "THL.Host.dll").write_bytes(b"OLD-DLL")
    (target / "THL" / "marker.txt").write_text("old version", encoding="utf-8")
    (target / "THL.addin").write_text("OLD-ADDIN", encoding="utf-8")
    staging = base / "updates" / f"host-{VERSION}-net10"
    with zipfile.ZipFile(REAL_ZIP) as z:
        z.extractall(staging)
    return base, target, staging, base / "updates" / "backup", base / "updates" / "pending.json", base / "logs" / "update.log"


def run(base, target, staging, backup, pending, log, pid, version=VERSION, wait=15, retry=6, timeout=120):
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps({"version": version, "staging": str(staging)}), encoding="utf-8")
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
           "-RevitPid", str(pid), "-Staging", str(staging), "-Target", str(target), "-Version", version,
           "-Log", str(log), "-Backup", str(backup), "-Pending", str(pending),
           "-WaitSeconds", str(wait), "-RetrySeconds", str(retry)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def fake_revit(seconds):
    return subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", f"Start-Sleep {seconds}"])


def log_text(log):
    return log.read_text(encoding="utf-8-sig") if log.exists() else ""


def asm_version(dll):
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", f"[System.Reflection.AssemblyName]::GetAssemblyName('{dll}').Version.ToString()"], capture_output=True, text=True)
    return r.stdout.strip()


# ---------------------------------------------------------------- 1) 成功
base, target, staging, backup, pending, log = sandbox()
rev = fake_revit(4)
t0 = time.time()
r = run(base, target, staging, backup, pending, log, rev.pid)
elapsed = time.time() - t0
check("成功：退出码 0", r.returncode == 0, r.stdout + r.stderr)
check("成功：确实等了“Revit”结束（>=3 秒）", elapsed >= 3, f"{elapsed:.1f}s")
check("成功：新版 DLL 已就位，程序集版本 = " + VERSION, asm_version(target / "THL" / "THL.Host.dll").startswith(VERSION), asm_version(target / "THL" / "THL.Host.dll"))
check("成功：新版带 Python 运行环境和标准库", (target / "THL" / "IronPython.dll").exists() and (target / "THL" / "lib" / "os.py").exists())
check("成功：旧版已备份（含旧文件）", any((d / "marker.txt").exists() for d in target.glob("THL.old-*")))
check("成功：旧的 marker.txt 不在新目录里", not (target / "THL" / "marker.txt").exists())
check("成功：THL.addin 已更新", (target / "THL.addin").read_text(encoding="utf-8") != "OLD-ADDIN")
check("成功：pending.json 与暂存目录已清理", not pending.exists() and not staging.exists())
check("成功：日志记录了更新", "已更新到" in log_text(log), log_text(log))
rev.wait()

# ---------------------------------------------------------------- 2) 更新后自检失败 → 回滚
base, target, staging, backup, pending, log = sandbox()
(staging / "THL" / "IronPython.dll").unlink()          # DLL 在，但缺少运行环境 → 复制后自检会失败
r = run(base, target, staging, backup, pending, log, fake_revit(1).pid)
check("回滚：退出码 1", r.returncode == 1, r.stdout + r.stderr)
check("回滚：旧版 DLL 已还原", (target / "THL" / "THL.Host.dll").read_bytes() == b"OLD-DLL")
check("回滚：旧版其它文件也还原", (target / "THL" / "marker.txt").exists())
check("回滚：THL.addin 仍是旧的", (target / "THL.addin").read_text(encoding="utf-8") == "OLD-ADDIN")
check("回滚：日志写明失败原因和回滚", "更新失败" in log_text(log) and "已回滚" in log_text(log), log_text(log))
check("回滚：保留 pending（下次关闭 Revit 再试）", pending.exists())

# ---------------------------------------------------------------- 3) 内容不完整 → 什么都不动
base, target, staging, backup, pending, log = sandbox()
(staging / "THL.addin").unlink()
r = run(base, target, staging, backup, pending, log, fake_revit(1).pid)
check("不完整：退出码 1", r.returncode == 1)
check("不完整：目标目录完全没动", (target / "THL" / "THL.Host.dll").read_bytes() == b"OLD-DLL" and (target / "THL.addin").read_text(encoding="utf-8") == "OLD-ADDIN" and not list(target.glob("THL.old-*")))

# ---------------------------------------------------------------- 4) 等待 Revit 超时 → 不更新
base, target, staging, backup, pending, log = sandbox()
rev = fake_revit(30)
r = run(base, target, staging, backup, pending, log, rev.pid, wait=3)
rev.kill()
check("超时：退出码 1，不动目标", r.returncode == 1 and (target / "THL" / "THL.Host.dll").read_bytes() == b"OLD-DLL", r.stdout + r.stderr)
check("超时：日志说明", "超时" in log_text(log), log_text(log))

# ---------------------------------------------------------------- 5) 文件被占用（模拟第二个 Revit 还开着）→ 重试后放弃，旧版完好
base, target, staging, backup, pending, log = sandbox()
dll = target / "THL" / "THL.Host.dll"
holder = subprocess.Popen(["powershell.exe", "-NoProfile", "-Command",
    f"$f=[System.IO.File]::Open('{dll}','Open','Read','None'); Start-Sleep 25; $f.Close()"])
time.sleep(2.5)
t0 = time.time()
r = run(base, target, staging, backup, pending, log, fake_revit(1).pid, retry=6)
elapsed = time.time() - t0
holder.kill(); holder.wait()
check("占用：退出码 1", r.returncode == 1, r.stdout + r.stderr)
check("占用：重试了一段时间才放弃（>=5 秒）", elapsed >= 5, f"{elapsed:.1f}s")
check("占用：旧版完好无损（DLL、其它文件、addin 都在原处）", dll.read_bytes() == b"OLD-DLL" and (target / "THL" / "marker.txt").exists() and (target / "THL.addin").read_text(encoding="utf-8") == "OLD-ADDIN" and not list(target.glob("THL.old-*")), sorted(p.name for p in (target / "THL").glob("*")))
check("占用：日志写明被占用", "占用" in log_text(log), log_text(log))

# ---------------------------------------------------------------- 6) 备份只保留最近 2 份
base, target, staging, backup, pending, log = sandbox()
for n in ("THL.old-20200101-000000", "THL.old-20200102-000000", "THL.old-20200103-000000"):
    (target / n).mkdir(); (target / n / "x.txt").write_text("x")
r = run(base, target, staging, backup, pending, log, fake_revit(1).pid)
left = sorted(d.name for d in target.glob("THL.old-*"))
check("备份轮转：只保留最近 2 份", r.returncode == 0 and len(left) == 2, left)

print("ALL PASS" if fail == 0 else f"{fail} FAILED")
sys.exit(1 if fail else 0)
