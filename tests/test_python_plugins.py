"""Python 插件的离线检查（不需要 Revit），发布前自动运行。用法：python tests/test_python_plugins.py
① 每个 python-src/<id>/script.py 展开 `# @include` 后语法正确；被引用的共用片段都存在。
② 井大样项目配置：配置 → 设置窗口里的文本 → 配置，往返一致；格式不对的行要报错。
③ 共用设置窗口的多行字段（lines）在显示时用 \\r\\n 连接（曾被误写成真换行，导致所有引用它的插件语法错误）。"""
import io, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "python-src"
INC = re.compile(r"^# @include (\S+)\s*$", re.M)
fail = 0


def check(name, cond, extra=""):
    global fail
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + extra))
    fail += not cond


def expand(text):
    def rep(m):
        f = SRC / m.group(1)
        if not f.exists():
            raise FileNotFoundError(m.group(1))
        return f.read_text(encoding="utf-8").rstrip() + "\n"
    return INC.sub(rep, text)


# ① 语法
for script in sorted(SRC.glob("*/script.py")):
    pid = script.parent.name
    try:
        src = expand(script.read_text(encoding="utf-8"))
        compile(src, pid, "exec", dont_inherit=True)
        ok, why = True, ""
    except SyntaxError as ex:
        ok, why = False, "%s 行 %s" % (ex.msg, ex.lineno)
    except FileNotFoundError as ex:
        ok, why = False, "找不到共用片段 %s" % ex
    check("语法 " + pid, ok, why)

# ② 井大样项目配置往返
src = (SRC / "_shared" / "well_config.py").read_text(encoding="utf-8") + "\n" + \
      (SRC / "_shared" / "well_config_ui.py").read_text(encoding="utf-8")
src = src.replace("__import__('System').Environment.GetFolderPath(__import__('System').Environment.SpecialFolder.MyDocuments)", "'.'")
ns = {}
exec(compile(src, "well_config", "exec"), ns)
cfg = ns["_wcfg_merge"](ns["WCFG_DEFAULTS"], {})
flat = ns["_wcfg_to_flat"](cfg)
back = ns["_wcfg_lines_to_series"](flat["series"])
key = lambda s: (s["prefix"], s["system"], s["code"], s["label"])
check("井号系列往返一致", [key(s) for s in back] == [key(s) for s in cfg["series"]])
check("图名模板往返一致", ns["_wcfg_lines_to_map"](flat["sheet_name_templates"], "x") == cfg["sheet_name_templates"])
check("图纸集合往返一致", ns["_wcfg_lines_to_map"](flat["sheet_collection_by_prefix"], "x") == cfg["sheet_collection_by_prefix"])
for bad, fn in ((["AA 强电"], "_wcfg_lines_to_series"), (["AA 没有等号"], "_wcfg_lines_to_map")):
    try:
        ns[fn](bad, "x") if fn == "_wcfg_lines_to_map" else ns[fn](bad)
        check("格式错误要报错 " + fn, False, "没有报错")
    except ValueError:
        check("格式错误要报错 " + fn, True)

# ③ lines 字段的连接符
su = (SRC / "_shared" / "settings_ui.py").read_text(encoding="utf-8")
check("settings_ui 的 lines 用 \\r\\n 连接", 'return u"\\r\\n".join(val or [])' in su)

print("\n%s" % ("全部通过" if not fail else "%d 项失败" % fail))
sys.exit(1 if fail else 0)
