# 在 Revit 里运行的检查脚本

这些脚本要在打开了项目的 Revit 2026 里运行（Dynamo 的 Python 节点，或 HuskyBIM 的 `revit_run_dynamo_python`），读的是仓库里 `python-src/` 的最新源码，不用先打包安装。

| 脚本 | 做什么 | 改模型吗 |
|---|---|---|
| `check_compile.py` | 用 Revit 自带的 IronPython 编译全部插件；弹出共用设置窗口并自动点保存，核对字段读写 | 不改 |
| `annotate_dryrun.py` | 用最新的一键井大样标注对抽样图纸完整跑一遍，最后整体回滚，只出报表 | 不改（事务组回滚） |

运行方式（Dynamo Python 节点里）：

```python
import io
SHEETNOS = None          # 或 ['2018', '1044'] 只跑这几张
exec(compile(io.open(r'C:\KAIFA\thl\tests\revit\annotate_dryrun.py', encoding='utf-8').read(), 'dryrun', 'exec'))
```

结果写在 `%TEMP%\thl_check_compile.txt`、`%TEMP%\thl_annotate_dryrun.txt`。HuskyBIM 调用超过 30 秒会显示超时，但脚本会在 Revit 里继续跑完，等文件出现即可。

不需要 Revit 的离线检查是 `tests/test_python_plugins.py`，发布时自动运行。
