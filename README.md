# 天河落 THL —— Revit 插件宿主

Revit 2026 插件宿主 + 应用商店客户端。官网：https://tianheluo.com

- `THL.Host/`  宿主（同时编译 .NET 8 / .NET 10 两个版本；Revit 2026.0~2026.4 用 .NET 8，2026.5+ 用 .NET 10）
  - 插件中心（搜索 / 运行 / 安装 / 更新 / 停用 / 卸载）、功能区动态同步、热加载
  - 内置 IronPython 3.4 + 极简 `pyrevit` 兼容层（`pyrevit_shim.py`），可直接运行 pyRevit 脚本
- `plugins/`   C# 插件（井深调整、明细表 Excel 互导）
- `setup/`     客户安装脚本（`install.bat` 只是纯 ASCII 外壳，真正逻辑在 `install.ps1`）
- `tools/`     打包 / 发布脚本（`python tools/build_all.py`）
- `tests/`     离线测试（商店逻辑、Excel 读写、兼容层）

## 构建
```
python tools/build_all.py            # 编译 + 打包 + 生成安装包与商店清单
python tools/build_all.py --deploy   # 同时部署到本机 Revit（先关闭 Revit）
```
产物在 `dist/`，随后复制到网站仓库 `tianheluo-web`（`build_site.py` 自动完成）。

## 插件包格式 `.thlp`
zip：`plugin.json` + `script.py`（pyRevit 脚本）或 `*.dll`（C# 插件）+ 可选 `icon.png`。
用户数据目录：`%APPDATA%\Tianheluo`（插件、配置、日志）。

## 许可与署名
- 宿主与原创插件：作者 didi。
- 图纸管理 / 视图管理 / 图纸签发管理 来自 [Sendit](https://github.com/lewismconte/sendit)（MIT，作者 lewismconte），许可文件随插件包分发。
- pyRevit 为 GPL-3.0（https://github.com/pyrevitlabs/pyRevit）。
