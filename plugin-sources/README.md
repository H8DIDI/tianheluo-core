# 插件源码索引

这里是**每个插件发布出去的源码**（就是用户装到的那份，由 `tools/export_sources.py` 从发布包展开，不会和线上不一致）。
想学怎么写、想改进某个插件，直接读对应目录里的 `script.py`；`plugin.json` 是它的清单。投稿规范见 <https://tianheluo.com/developers>。

| 插件 id | 名称 | 版本 | 类型 | 作者 | 许可 |
|---|---|---|---|---|---|
| [`control-points`](control-points/) | 控制点坐标表 | 0.1.1 | Python | didi | MIT |
| [`duct-elev`](duct-elev/) | 排管标高批量调整 | 0.1.0 | Python | didi | MIT |
| [`duct-snap`](duct-snap/) | 排管贴井内壁 | 0.1.9 | C#（源码在 `plugins/`） | didi | MIT |
| [`elv-merge`](elv-merge/) | 族类型清洗合并 | 0.2.2 | Python | didi | MIT |
| [`export-dwg`](export-dwg/) | 一键导出图纸DWG/PDF | 0.5.0 | Python | didi | MIT |
| [`export-nwc`](export-nwc/) | 一键导出NWC | 0.3.0 | Python | didi | MIT |
| [`family-param-dim`](family-param-dim/) | 族尺寸参数化 | 0.1.2 | Python | didi | MIT |
| [`issue-manager`](issue-manager/) | 图纸签发管理 | 0.2.1 | Python | lewismconte (Sendit) | MIT |
| [`pile-cleanup`](pile-cleanup/) | 清理重复松木桩垫层 | 0.2.1 | Python | didi | MIT |
| [`pile-cushion`](pile-cushion/) | 一键布置松木桩垫层 | 0.4.1 | Python | didi | MIT |
| [`plan-batch`](plan-batch/) | 批量生成平面图 | 0.2.1 | C#（源码在 `plugins/`） | didi | MIT |
| [`quantity-export`](quantity-export/) | 框选提量导出Excel（结构 + 井排管） | 0.3.1 | Python | didi | MIT |
| [`rebar-3d-view`](rebar-3d-view/) | 一键钢筋三维实体显示 | 0.2.0 | Python | didi | MIT |
| [`rebar-cage`](rebar-cage/) | 一键单构件快速配筋 | 0.3.0 | Python | didi | MIT |
| [`rfi-pack`](rfi-pack/) | RFI 取证包 | 0.1.0 | Python | didi | MIT |
| [`schedule-excel`](schedule-excel/) | 明细表Excel互导 | 0.1.1 | C#（源码在 `plugins/`） | didi | 原创 |
| [`sheet-manager`](sheet-manager/) | 图纸管理 | 0.2.1 | Python | lewismconte (Sendit) | MIT |
| [`sheet-organize`](sheet-organize/) | 一键图纸归类与图框编号 | 0.6.6 | Python | didi | MIT |
| [`sheet-preview`](sheet-preview/) | 图纸快速预览 | 0.1.1 | Python | didi | MIT |
| [`tag-check`](tag-check/) | 标注检查 | 0.2.1 | C#（源码在 `plugins/`） | didi | MIT |
| [`view-manager`](view-manager/) | 视图管理 | 0.2.1 | Python | lewismconte (Sendit) | MIT |
| [`well-annotate`](well-annotate/) | 一键井大样标注 | 0.3.38 | Python | didi | MIT |
| [`well-audit`](well-audit/) | 井大样体检 | 0.1.0 | Python | didi | MIT |
| [`well-clear-marks`](well-clear-marks/) | 一键清除井标记 | 0.1.3 | Python | didi | MIT |
| [`well-config`](well-config/) | 井大样项目配置 | 0.1.1 | Python | didi | MIT |
| [`well-depth-report`](well-depth-report/) | 井深统计报表 | 0.1.6 | Python | didi | MIT |
| [`well-depth`](well-depth/) | 井深调整 | 0.3.4 | C#（源码在 `plugins/`） | didi | MIT |
| [`well-family-guard`](well-family-guard/) | 井族体检 | 0.1.2 | Python | didi | MIT |
| [`well-list-check`](well-list-check/) | 井表核对 | 0.1.0 | Python | didi | MIT |
| [`well-number`](well-number/) | 一键编号 | 0.2.1 | Python | didi | MIT |
| [`well-param-sync`](well-param-sync/) | 井参数同步 | 0.1.2 | Python | didi | MIT |
| [`well-sheet-fit`](well-sheet-fit/) | 井图比例排版 | 0.1.3 | Python | didi | MIT |
| [`well-sheet-no`](well-sheet-no/) | 井图号重编 | 0.1.7 | Python | didi | MIT |
| [`well-sheets`](well-sheets/) | 一键井展开图 | 0.5.12 | Python | didi | MIT |
| [`well-spec-table`](well-spec-table/) | 井规格表 | 0.1.5 | Python | didi | MIT |
| [`well-sync`](well-sync/) | 井大样变动同步 | 0.1.0 | Python | didi | MIT |
| [`well-zone`](well-zone/) | 井分区标记 | 0.1.5 | Python | didi | MIT |
