# 插件源码索引

这里是**每个插件发布出去的源码**（就是用户装到的那份，由 `tools/export_sources.py` 从发布包展开，不会和线上不一致）。
想学怎么写、想改进某个插件，直接读对应目录里的 `script.py`；`plugin.json` 是它的清单。投稿规范见 <https://tianheluo.com/developers>。

| 插件 id | 名称 | 版本 | 类型 | 作者 | 许可 |
|---|---|---|---|---|---|
| [`control-points`](control-points/) | 控制点坐标表 | 0.1.0 | Python | didi | MIT |
| [`duct-snap`](duct-snap/) | 排管贴井内壁 | 0.1.8 | C#（源码在 `plugins/`） | didi | MIT |
| [`elv-merge`](elv-merge/) | 族类型清洗合并 | 0.2.0 | Python | didi | MIT |
| [`export-dwg`](export-dwg/) | 一键导出图纸DWG/PDF | 0.4.2 | Python | didi | MIT |
| [`export-nwc`](export-nwc/) | 一键导出NWC | 0.2.0 | Python | didi | MIT |
| [`issue-manager`](issue-manager/) | 图纸签发管理 | 0.2.0 | Python | lewismconte (Sendit) | MIT |
| [`pile-cleanup`](pile-cleanup/) | 清理重复松木桩垫层 | 0.2.0 | Python | didi | MIT |
| [`pile-cushion`](pile-cushion/) | 一键布置松木桩垫层 | 0.4.0 | Python | didi | MIT |
| [`plan-batch`](plan-batch/) | 批量生成平面图 | 0.2.0 | C#（源码在 `plugins/`） | didi | MIT |
| [`quantity-export`](quantity-export/) | 一键框选提量导出Excel | 0.2.1 | Python | didi | MIT |
| [`rebar-3d-view`](rebar-3d-view/) | 一键钢筋三维实体显示 | 0.1.0 | Python | didi | MIT |
| [`rebar-cage`](rebar-cage/) | 一键单构件快速配筋 | 0.2.1 | Python | didi | MIT |
| [`schedule-excel`](schedule-excel/) | 明细表Excel互导 | 0.1.0 | C#（源码在 `plugins/`） | didi | 原创 |
| [`sheet-manager`](sheet-manager/) | 图纸管理 | 0.2.0 | Python | lewismconte (Sendit) | MIT |
| [`sheet-organize`](sheet-organize/) | 一键图纸归类与图框编号 | 0.6.2 | Python | didi | MIT |
| [`sheet-preview`](sheet-preview/) | 图纸快速预览 | 0.1.0 | Python | didi | MIT |
| [`tag-check`](tag-check/) | 标注检查 | 0.2.0 | C#（源码在 `plugins/`） | didi | MIT |
| [`view-manager`](view-manager/) | 视图管理 | 0.2.0 | Python | lewismconte (Sendit) | MIT |
| [`well-annotate`](well-annotate/) | 一键井大样标注 | 0.3.15 | Python | didi | MIT |
| [`well-clear-marks`](well-clear-marks/) | 一键清除井标记 | 0.1.1 | Python | didi | MIT |
| [`well-depth-report`](well-depth-report/) | 井深统计报表 | 0.1.2 | Python | didi | MIT |
| [`well-depth`](well-depth/) | 井深调整 | 0.3.3 | C#（源码在 `plugins/`） | didi | MIT |
| [`well-number`](well-number/) | 一键编号 | 0.2.0 | Python | didi | MIT |
| [`well-sheets`](well-sheets/) | 一键井展开图 | 0.5.1 | Python | didi | MIT |
