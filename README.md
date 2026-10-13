# 天河落 THL · Tianheluo — Revit 2026 Plugin Host & Open Plugin Store

**Revit 插件宿主 + 开源共享插件商店。** 装一次宿主，之后在 Revit 里一键下载、热加载、更新各种插件，**不用重启 Revit**。免费、公开、非商业。

> **EN:** An open-source **Revit 2026 add-in host** (plugin manager) with hot reload — like pyRevit's reload, but for a shared plugin store. Install once, then download, update and hot-reload small plugins from inside Revit, no restart. Runs **IronPython 3.4** scripts (pyRevit-compatible subset) and **C# (.NET 8)** plugins. Ships an **AI-readable plugin spec** so ChatGPT / Claude / Revit-MCP-style agent workflows can generate plugins that run on it. Non-commercial, MIT.

🌐 官网 / Website：<https://tianheluo.com> · 插件商店 Store：<https://tianheluo.com/store>
📦 网站源码 Site source：<https://github.com/H8DIDI/tianheluo-web>
🤖 给 AI 的开发说明书 AI plugin spec：<https://tianheluo.com/tianheluo-ai-spec.txt> · [llms.txt](https://tianheluo.com/llms.txt)
💬 交流群：微信 didi（<https://tianheluo.com/contact>）· 邮箱 jiajiabimer@gmail.com

---

## 它能做什么 / Features

| | |
|---|---|
| **一键安装与热更新** | 插件中心里搜索、安装、更新、停用、卸载；替换插件文件后下次点击即生效，无需重启 Revit（每次运行从内存加载，DLL 不被锁） |
| **宿主自更新** | Revit 内检查新版本 → 下载并 SHA256 校验 → 关闭 Revit 时自动替换，失败自动回滚 |
| **两种插件** | Python 脚本（内置 IronPython 3.4 + 极简 `pyrevit` 兼容层，可直接运行 pyRevit 脚本）；C#（.NET 8）插件 |
| **单文件分发** | 一个插件 = 一个 `.thlp`（zip：`plugin.json` + 脚本/DLL），可单独下载 |
| **功能区同步** | 安装后自动在 Revit 功能区出现按钮（最多 12 个，其余在插件中心启动） |
| **下载榜** | 商店显示下载数、官网热门榜（不保存 IP） |
| **安全校验** | 包 SHA256 校验、防路径穿越、`hostApi` 接口版本、社区插件安装前提示、投稿源码人工审核 |
| **适配 Revit 2026 全系** | 同时编译 .NET 8（2026.0–2026.4）与 .NET 10（2026.5+），安装脚本自动选择 |

## 和 Revit MCP 的关系 / Relation to Revit MCP

天河落**不是** MCP 服务器。它解决的是另一个环节：把 AI（包括通过 Revit MCP 或普通聊天驱动的 AI 工作流）**写出来的 Revit 小工具，变成可分发、可更新的插件**。
Revit MCP 让 AI 直接操作模型；天河落让 AI 写好的脚本沉淀成大家都能一键装的插件。二者互补。给你的 AI 一份 [说明书](https://tianheluo.com/tianheluo-ai-spec.txt)，它就能产出符合规范、能被本宿主运行的插件。

> THL is **not** an MCP server. It complements Revit MCP tooling: MCP lets an AI drive the model live; THL turns the scripts an AI writes into shareable, updatable plugins.

## 快速开始 / Quick start

**用户**：<https://tianheluo.com/install> 下载安装包 → 解压 → 双击 `install.bat` → 重启 Revit → 功能区「天河落」→ 插件中心。

**想做插件**：
1. 下载 AI 说明书 <https://tianheluo.com/tianheluo-ai-spec.txt>，交给你的 AI。
2. 本地自查：`python validate.py 你的插件目录`（[tools/validate.py](https://github.com/H8DIDI/tianheluo-web/blob/main/tools/validate.py)，单文件零依赖）。
3. 压缩成 zip 发邮件到 jiajiabimer@gmail.com（或加微信 didi），审核后上架并署名。

**想看宿主怎么实现**：从这里读起 →
`THL.Host/Launcher.cs`（热加载：可回收 AssemblyLoadContext + 内存加载）· `THL.Host/PyRunner.cs`、`PyBridge.cs`、`pyrevit_shim.py`（IronPython 运行与 pyRevit 兼容层）· `THL.Host/Store.cs`（商店、安装、校验）· `THL.Host/Update.cs` + `apply-update.ps1`（宿主自更新与回滚）· `THL.Host/Slots.cs`、`App.cs`（功能区动态同步）。

## 仓库结构

- `THL.Host/`  宿主（同时编译 .NET 8 / .NET 10；Revit 2026.0~2026.4 用 .NET 8，2026.5+ 用 .NET 10）
- `plugins/`   C# 插件示例（井深调整、明细表 Excel 互导）
- `setup/`     安装脚本（`install.bat` 纯 ASCII 外壳，真正逻辑在 `install.ps1`）
- `tools/`     打包与发布脚本（可复现构建；`publish.py` 一键发布）
- `tests/`     离线测试（商店逻辑、更新逻辑、应用更新脚本、校验器、Excel 读写、兼容层）
- `docs/`      [运营手册](docs/OPERATIONS.md) · [AI 插件规范源](docs/ai_spec.md)

## 构建
```
python tools/build_all.py            # 编译 + 打包 + 生成安装包与商店清单
python tools/build_all.py --deploy   # 同时部署到本机 Revit（先关闭 Revit）
python tools/publish.py -m "说明"    # 一键发布：构建→测试→检查→推送→核对线上
```
需要：.NET SDK 8 与 10、Python 3.10+、已安装 Revit 2026（编译 net10 版时引用其 DLL）。
产物在 `dist/`，随后复制到网站仓库（`build_site.py` 自动完成）。用户数据目录：`%APPDATA%\Tianheluo`。

## 文档
[运营手册](docs/OPERATIONS.md) · [更新记录](CHANGELOG.md) · [参与贡献](CONTRIBUTING.md) · [安全策略](SECURITY.md) · [AI 插件规范](docs/ai_spec.md)

## 许可与署名
- 本仓库代码：MIT（见 [LICENSE](LICENSE)），作者 didi。
- 图纸管理 / 视图管理 / 图纸签发管理 来自 [Sendit](https://github.com/lewismconte/sendit)（MIT，作者 lewismconte），许可文件随插件包分发。
- 兼容层参考 pyRevit（GPL-3.0，<https://github.com/pyrevitlabs/pyRevit>）的使用习惯，未包含其代码。
- Revit 是 Autodesk, Inc. 的商标，本项目与 Autodesk 无关联。

<sub>Keywords: Revit plugin manager · Revit add-in host · Revit 2026 · hot reload · pyRevit alternative · IronPython · Revit API · BIM · Revit MCP · AI-generated Revit plugins · llms.txt · 天河落 · Revit 插件 · Revit 二次开发 · 热加载 · 插件商店</sub>
