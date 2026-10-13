# 天河落 THL —— Revit 插件开发说明书（专门给 AI 阅读）

> 版本 1.1（2026-10）· 官网 https://tianheluo.com · 投稿与联系邮箱 jiajiabimer@gmail.com
> 宿主与工具源码（公开，MIT）：https://github.com/H8DIDI/tianheluo-core · 网站源码：https://github.com/H8DIDI/tianheluo-web
> 本说明书是**自包含**的：规则、接口、完整示例代码都在里面，不需要访问任何链接。

## 0. 先读这个（30 秒版）

**你要做什么**：用户想为「天河落」平台开发一个 Revit 2026 插件。你要写出一个**文件夹**，里面是 4 个文件：`plugin.json`、`script.py`、`README.md`、`LICENSE`。用户把它压缩成 zip 发给平台维护者，审核后上架。

**插件长什么样**：一个 Python 脚本（运行在 Revit 里，用 Revit API 读写模型），点一下按钮就运行一次。不需要安装任何东西，也不需要编译。

**你必须遵守的 8 条硬规则**（后面有详细说明）：
1. 语言是 **Python（IronPython 3.4）**，不是 CPython：没有 numpy/pandas；**不能用 `:=`、`async/await`、`match`**；f-string 可以用。
2. 脚本结构固定：`main()` 函数 + 末尾 `if __name__ == "__main__": main()`。
3. 只能用说明书「第 4 节」列出的接口：`from pyrevit import revit, DB, UI, forms, script`。**不要使用没列出的 pyRevit 功能**（比如 `script.get_output()`、`script.exit()`）。
4. **修改模型必须**：先弹窗预览并让用户确认 → **只开一个** `with revit.Transaction("名称"):` → 统计成功/失败。这样用户按一次 `Ctrl+Z` 就能撤销全部。
5. 所有给用户看的信息用 `forms.alert(...)`，**不要用 `print` 给用户看**（`print` 只写日志文件）。
6. **禁止**：执行外部程序/命令、`eval/exec`、未声明的联网、删除文件、加载外部 DLL、读取密钥/注册表。
7. 长度单位：Revit API 里是**英尺**。毫米请 `mm / 304.8`。
8. 你**无法运行**用户的 Revit，不要声称「已测试通过」。要提醒用户在**空白测试项目**里先试，把报错发回来继续改。

**交付格式**：见「第 14 节」。**不确定时的做法**：见「第 13 节」。

---

