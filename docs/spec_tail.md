
## 13. 常见错误与坑（写代码前先看一遍）

**语法与运行环境**
- ❌ `if (n := len(x)) > 3:`（海象运算符）→ ✅ 先 `n = len(x)` 再 `if n > 3:`
- ❌ `async def` / `await` → ✅ 一律写同步代码（脚本本来就在 Revit 主线程同步执行）。
- ❌ `import numpy / pandas / requests` → ✅ 只用标准库和 Revit API / .NET。
- ❌ 在脚本里 `print(...)` 想给用户看 → ✅ 用 `forms.alert(...)`。
- ❌ 忘了 `if __name__ == "__main__": main()` → ✅ 一定要有，否则 `main()` 不会被调用（宿主以 `__main__` 运行脚本）。
- 除法：`/` 是真除法（得到小数），整除用 `//`。字符串默认是 Unicode，文件头写 `# -*- coding: utf-8 -*-`。
- 读写文本文件时指定编码：`io.open(path, "w", encoding="utf-8")`；路径用 `os.path.join`，不要硬编码盘符。

**Revit API**
- ❌ `element.Id.IntegerValue`（在 Revit 2026 里**已不存在**，会报 AttributeError）→ ✅ `element.Id.Value`（64 位整数）。
- ❌ 在已打开的事务里再开事务（嵌套）→ ✅ 全脚本只开一个 `with revit.Transaction(...)`；需要多步就在同一个 `with` 里做。
- ❌ 在事务**外**修改参数/删除/创建元素 → 会抛异常。读取不需要事务。
- ❌ `LookupParameter("中文名")` 当作唯一可靠的方式 → 参数名随 Revit 语言/项目而变，优先 `element.get_Parameter(DB.BuiltInParameter.XXX)`；`LookupParameter` 只取第一个同名参数，可能为 `None`，使用前必须判空。
- 参数赋值要先判断：`p is not None and not p.IsReadOnly`；数值/带单位的参数用 `p.SetValueString("1500")`（按项目单位解析），文本用 `p.Set("文字")`，整数/双精度用 `p.Set(数值)`（双精度单位是英尺等内部单位）。
- `uidoc.Selection.GetElementIds()` 返回 .NET 集合 → 用 `list(...)` 包一层再 `len()`。
- `doc.GetElement(eid)` 可能返回 `None`（已删除），要判空。
- 过滤构件要用 `FilteredElementCollector` + `OfCategory` / `OfClass` / `WhereElementIsNotElementType()`，**不要**遍历全部元素再用 Python 判断类别——大模型会非常慢。
- 大批量操作：用 `forms.ProgressBar("正在处理... ({value}/{max_value})", cancellable=True)`，循环里 `pb.update_progress(i + 1, total)` 并检查 `pb.cancelled`。
- 长时间运行会冻结 Revit 界面（脚本在主线程）。不要写死循环，不要 `time.sleep` 很久。
- 不要创建线程；不要使用 `ExternalEvent` 或无模态窗口——脚本结束后运行环境会被释放，无法保证之后的回调。所有交互都用模态对话框，在脚本里同步完成。
- 当前文档可能是族文档/没有项目：需要项目时先判断 `doc.IsFamilyDocument`。
- `doc.ActiveView` 可能是图纸、明细表等非模型视图；统计模型构件前可判断 `isinstance(doc.ActiveView, DB.ViewPlan)` 等，或提示用户切换到合适的视图。

**对话框**
- `forms.alert(msg, title="...")` 普通提示；要确认写 `forms.alert(msg, title="...", yes=True, no=True)`，返回 `True` 表示用户点了「是」。
- `forms.SelectFromList.show(items, title="...", button_name="确定", multiselect=True)`：取消返回 `None`，**一定要判空**。
- `forms.ask_for_string(...)` 取消返回 `None`；留空返回 `""`，两者不同。
- 所有对话框取消后要 `return`，不要继续往下执行。

**需求超出能力时**：如果用户要的功能需要这里没有的东西（例如自定义功能区面板、持续监听事件、调用外部程序、联网服务），请**如实告诉用户做不到或需要简化**，并给出一个能做的替代方案；不要编造不存在的接口。

## 14. 给 AI 的交付格式（请严格按这个格式回答用户）

当用户描述完需求后，你的回答按以下顺序：

1. **一两句话复述你理解的需求**，以及你做的关键决定（比如：只读还是会改模型、作用范围是选中构件/当前视图/全项目）。如果有会影响实现的不确定点，**最多问 3 个问题**再动手；其余用合理默认并说明。
2. **完整文件**——每个文件一个代码块，代码块前一行写文件名，例如：

   **`my-plugin/plugin.json`**
   ```json
   { ... }
   ```
   **`my-plugin/script.py`**
   ```python
   ...
   ```
   **`my-plugin/README.md`** 和 **`my-plugin/LICENSE`** 同理。文件内容必须**完整**，不要省略、不要写「其余同上」。
3. **使用与测试说明**（用普通话写给不会编程的用户）：
   - 本地试用：把整个文件夹 `my-plugin` 复制到 `%APPDATA%\Tianheluo\plugins\`，在 Revit 里打开「天河落 → 插件中心」，在「已安装」里点「运行」。
   - 先用空白测试项目试；有错误弹窗或日志（`%APPDATA%\Tianheluo\logs\my-plugin.log`）把内容发回来继续改。
   - 自查：下载 `validate.py`（https://tianheluo.com/tools/validate.py），运行 `python validate.py my-plugin`。
   - 投稿：压缩成 zip，发邮件到 jiajiabimer@gmail.com，主题 `[天河落插件投稿] 插件名 v版本`。
4. **诚实声明**：「我无法在你的 Revit 里运行这段代码，上面没有经过实际测试」。

**plugin.json 的填写规则**（再强调一遍）：`id` 小写连字符；`version` 从 `0.1.0` 开始；`author` 问用户；`license` 默认 `MIT`（请让用户确认）；`type` 固定 `python`；`script` 固定 `script.py`；`glyph` 取名字里最有辨识度的 1 个字；`category` 只能是 市政管线 / 出图打印 / 数据处理 / 建模辅助 / 视图管理 / 导出 / 其他。

**README.md 要写**：一句话功能、使用步骤、**会修改什么（只读则写「不修改模型」）**、已知限制、作者。

**LICENSE**：用户选的开源许可证全文（默认 MIT，署名处填用户的名字和年份 2026）。

