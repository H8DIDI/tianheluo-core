# 天河落 AutoCAD 宿主（THL.Acad）

AutoCAD 版的插件平台，对应 Revit 宿主 `THL.Host`。支持 AutoCAD 2018 及以上（含 Civil 3D 等基于 AutoCAD 的产品）。

## 目录

| 路径 | 内容 |
|---|---|
| `THL.Acad/` | 宿主：功能区、命令 `THL` / `THLRUN`、热加载、自更新脚本、`PackageContents.xml` |
| `THL.Host/Store.cs` `Update.cs` `LauncherWindow.cs` `Icons.cs` `LogoGeometry.cs` | 和 Revit 宿主**共用**（商店、.thlp 安装与 SHA256 校验、宿主自更新、插件中心窗口）。AutoCAD 的差异写在 `#if THL_ACAD` 里，Revit 编译结果 IL 不变 |
| `plugins-acad/` | AutoCAD 插件；`Directory.Build.props` 统一两套目标，`_shared/AcadUtil.cs` 共用小工具 |
| `plugins-acad/dwg-slim/` | DWG 瘦身（算法直接编译 `dwgslim/Slim.cs`，和 Revit 导出后台的 THLSLIM 同一份） |
| `plugins-acad/dwg-underlay/` | 套图 |
| `plugins-acad/dwg-tidy/` | 出图美化：Revit 导出 DWG 规范化（缺字体→gbenor+gbcbig、线型周期按纸面重排、删重叠重复线、浅色加深、底图/场地淡显、可选随层/文字遮罩、PURGE）。规则表 `%AppData%\Tianheluo\AutoCAD\settings\dwg-tidy.txt`；`diag=<txt>` 只体检。参数 `current=1` / `files=a|b;outdir=` |
| `plugins-acad/acad-sheets/` | 图集管理（id `acad-sheets`；Revit 商店已有 `sheet-manager`，所以不用这个 id）：图框学习 → 外部参照图框 + 图签属性块、框选/命名视图建图纸、列表排序/编号/目录、批量 PDF。见下文 |
| `tools/pack_acad.py` | 编译 + 打包到 `dist-acad/`；`--install` 装到本机 |
| `setup/install-acad.ps1` | 安装/卸载脚本 |

## 版本跨度

| AutoCAD | R 号 | .NET | 用的程序集 |
|---|---|---|---|
| 2018 | R22.0 | Framework 4.8（2018 原生 4.6，4.8 向下兼容装了 4.8 的机器） | `Contents/net48/` |
| 2019 / 2020 | R23.0 / R23.1 | Framework 4.7–4.8 | `Contents/net48/` |
| 2021–2024 | R24.0–R24.3 | Framework 4.8 | `Contents/net48/` |
| 2025 / 2026 | R25.0 / R25.1 | .NET 8 | `Contents/net8/` |

net48 版按 NuGet `AutoCAD.NET 22.0.0`（= 2018 接口）编译，只编译不分发，往上兼容；net8 版引用本机 AutoCAD 2026 的程序集，只用了 2025 就有的接口。
`PackageContents.xml` 用 `SeriesMin/SeriesMax` 让 AutoCAD 自己挑对应的 DLL。

> **只在 AutoCAD 2026 实机测过。** 2018–2024 只保证 net48 版能编译通过；本机没有这些版本，没法实机测。
> 2018 自带 .NET 4.6/4.7，机器上要装 .NET Framework 4.8（Win10 1903 以后自带）。

## 和 Revit 宿主的对应

| Revit 宿主 | AutoCAD 宿主 |
|---|---|
| `THL.addin` | `%AppData%\Autodesk\ApplicationPlugins\THL.bundle\PackageContents.xml`（自动加载，不用 NETLOAD，不改 SECURELOAD） |
| 功能区 12 个槽位类 | AdWindows 动态按钮，个数不限；切换工作空间（WSCURRENT）后自动补回 |
| 按钮 → IExternalCommand | 按钮 → `SendStringToExecute("_THLRUN <id>")`，在文档上下文里运行；没开图时直接在应用上下文跑 |
| 数据目录 `%AppData%\Tianheluo` | `%AppData%\Tianheluo\AutoCAD`（独立，和 Revit 的已装插件/配置互不相通） |
| 商店 `plugins.json` / `host.json` | `pluginsacad.json` / `hostacad.json`（网站上还没有，待上线） |
| plugin.json `revit` 字段 | `acad` 字段（如 `"2018+"`）；没有 `acad` 字段的包（Revit 插件）拒绝安装 |
| 包内 DLL 在根目录 | 包内分 `net48\` 和 `net8\` 两份，宿主按当前 AutoCAD 选 |
| 热加载 | 同样每次从磁盘读字节到内存加载：net8 用可回收的 AssemblyLoadContext，net48 按内容缓存 |
| 自更新 apply-update.ps1 | `apply-update-acad.ps1`：AutoCAD 退出后原子改名备份 THL.bundle、复制新版，失败回滚 |
| IronPython | 不带 |

## 插件约定

plugin.json：

```json
{ "id": "dwg-slim", "name": "DWG 瘦身", "version": "0.1.0", "acad": "2018+", "hostApi": 1,
  "dll": "ThlDwgSlimAcad.dll", "entry": "Thl.Acad.DwgSlim.Plugin", "category": "出图", "glyph": "瘦", "color": "#2F8CFF" }
```

入口类不用引用宿主 DLL，只要有一个方法（反射调用）：

```csharp
public string Execute(Document doc, IDictionary<string, string> args)   // 推荐
public string Execute(Document doc)
```

- `doc` 可能为 null（开始页没开图）。
- `args` 来自 `THLRUN id?key=value;key=value`，给批处理/自动测试用，平时为空（插件弹对话框）。
- 返回的文字写到命令行。
- 插件里**不要**写 `[assembly: CommandClass]`（热加载的程序集不该注册命令）。

## 命令

- `THL`：插件中心（搜索、运行、安装 .thlp、商店、更新、停用、卸载）。
- `THLRUN <id>[?参数]`：运行插件。功能区按钮就是发这个命令。
- `THLTESTPRESS`：自动测试用，只在环境变量 `THL_TEST=1` 时可用（按 Id 走功能区按钮真正的点击处理）。
- 测试截图：环境变量 `THL_SNAP_DIR=<目录>` 时，插件中心和插件对话框显示后自动截图存 png 并关闭。

## 打包与安装

```
python tools/pack_acad.py              # → dist-acad/
python tools/pack_acad.py --install    # 再装到本机（AutoCAD 要关掉）
powershell -ExecutionPolicy Bypass -File dist-acad\install-acad.ps1 [-Plugins dist-acad] [-Uninstall] [-Force]
```

`THL-Acad-Host_<ver>.zip` 的根就是 THL.bundle 的内容，既是手动安装包也是自更新包（`hostacad.json` 的 assets 键为 `acad`）。

## 一键出图向导（acad-sheets 默认界面）

功能区按钮「图集管理」默认打开向导：① 准备图纸（瘦身 + 出图美化，都在另存的「原名_出图.dwg」里做，原图不动）→ ② 分区（读图层 THL-分区 / 图上框选 / 网格 / 整张图）→ ③ 图框与方案（记住上次）→ ④ 预览（缩略图、比例、主图占图框比例、拆分，可改图名比例、勾掉）→ ⑤ 生成（进度、可取消；向导建的布局带 THL_ZONE 标记，再次生成先删旧的再建，不会重复）。
左下「设置…」：图例中文名表格（自动列出图里的图层）、出图美化规则表格（开关 + 图层规则 + 恢复默认）、方案导入导出（.thlscheme = 图框模板 + 图例 + 美化规则 + 向导选择）。「图纸列表…」= 原来的列表界面（排序、编号、Excel 改图签）。
界面代码（Wizard/*.cs）不依赖 AutoCAD，可用 `C:\KAIFA\_acadtest\tools\uiharness`（假后端）脱离 AutoCAD 点一遍截图；真实后端用 `THLRUN acad-sheets?wizard=1;...` 无界面测。高 DPI 下按 DeviceDpi 放大排版（AutoCAD 是 DPI 感知的）。

## 图集管理（acad-sheets）

- **图框模板**：从真实图框 DWG 学习（`Template.cs`）。外框 = 最大闭合矩形；视口可用区 = 内框去掉右侧整列图签栏；标签词典（DRAWING NO/图号、TITLE/图名、SCALE/比例、DESIGNED/设计……）找格子，格子里（或右侧相邻格子里）的示例文字 → 字段（位置/字高/对齐/图层/格子宽）；修订表按表头列 × 行展开；外框以外的东西删掉（文字记成「图框说明」）。
  模板存 `%AppData%\Tianheluo\AutoCAD\sheet-frames\<模板名>\`：`template.txt`（可手改）+ 去掉示例值的干净图框 DWG（2013 格式）。
- **建图纸**：新布局；图框复制到「图纸文件夹\图框\」后**覆盖型外部参照**（相对路径 `.\图框\x.dwg`；图纸没保存时用绝对路径），块名 `THL图框_<模板>`；图签 = 块 `THL图签_<模板>`，每字段一个属性，和图框同一变换（外框左下 → 纸面原点，缩放到正好铺满图幅，纸面单位 mm）。值太长自动压扁宽度系数留在格子里。
  视口：图框可用区内居中，比例 1:N（或自动挑放得下的标准比例），锁定、可冻结图层，图层 `THL-视口`（不打印）。打印设置：指定打印机、图幅（优先 full_bleed）、窗口 = 图幅、1:1、居中、打印样式。
- **列表**：图签属性就是数据源；排序 = 布局选项卡顺序；批量编号 `{n:000}` `{i:00}` `{total}` `{字段}`；统一填写；目录 = 新布局「图纸目录」+ 表格（多了分栏）。
- **PDF**：PlotEngine 多页打印，一个 PDF；书签 = 布局名（「写入图纸时按规则改布局名」或参数 `rename={图号} {图名}`）。
- **分区出图**（`Zones.cs`）：大总图按楼栋 / 区域切成多张图。分区来自图层 `THL-分区` 的闭合多段线（名称 = 多段线里面同图层文字）、界面框选（可存回图层）、参数坐标，或按图幅 + 比例自动网格（搭接、空块跳过）。每块：布局 + xref 图框 + 主视口（统一比例或各块自动）+ 非矩形分区裁剪视口；右侧栏 = 图例（只列本块里实际出现的图层；`settings\legend.txt` 写「图层通配|文字」或「|-」隐藏，默认隐藏 A-/S-/C-/L-/Q-/G- 底图图层）+ 分区索引图（总图缩略、当前块粗框、各块名）；相邻分区在视口边写「接 XX」。视口直接看模型空间，不复制几何。避开图框自带的小框（出图章等，模板里记为 keepout）。
- **Excel 批量改图签**（`ExcelSync.cs`）：导出一行一张图的 CSV（UTF-8 BOM，Excel 直接开）：天河落图签属性、布局里画出来的普通文字图签（用学习图框的同一套规则认格子）、模型空间里带 图号/图名/DRAWING_NO/TITLE 属性的图框块。改完导入：按「定位」列匹配，先列「旧 → 新」预览，确认后备份原文件（`原名.THL备份-时间.dwg`）再写入；空格子按学到的位置新建文字；可改布局名。
- 无界面参数（自动测试）：`THLRUN acad-sheets?learn=<图框>;tplname=<名>;new=x0,y0,x1,y1,比例,图名|...;all.<字段>=<值>;number=<规则>;sheetno=<规则>;rename=<规则>;toc=1;pdf=<路径>;ui=1`。
- 分区 / Excel 参数：`zones=名:x0,y0,x1,y1|名:x,y x,y ...`、`zonelayer=THL-分区`、`grid=1;gridscale=500;overlap=20`、`zbase=`、`zscale=`、`perzone=1`、`legend/index/match=0|1`、`savezones=1`；`export=<csv>`、`import=<csv>;apply=1`；`pdfall=1` 打印全部布局。
- 无界面测试：`C:\KAIFA\_acadtest\tools\run_sheets.ps1 -Dwg <副本> -A "<参数>" [-SaveAs <dwg>]`（accoreconsole 隔离配置 + 驱动 `tools\thldrv`，不弹安全提示、不碰界面）。
- 测试收件箱（宿主）：`THL_TEST=1` + `THL_TEST_INBOX=<目录>` 时，空闲时把目录里的 `*.cmd` 发给当前文档执行（不碰屏幕驱动测试实例）。注意：`_.OPEN` 从收件箱发会卡在命令行，换图请重开实例。

## 待办

- 网站：`pluginsacad.json`、`hostacad.json`、`/dl/acad/*`、商店页加「AutoCAD」标签（照 2020 版的做法）。
- `THL-Setup.exe` 加「AutoCAD 2018+」勾选项（现在用 `install-acad.ps1`）。
- 2018–2024 实机验证。
