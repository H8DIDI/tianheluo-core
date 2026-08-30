# 天河落核心引擎与插件库

一级 **Tianheluo**，二级 **THL**，功能用纯粹名词。

```
tianheluo-core/
├── Tianheluo.sln
├── 1_Host/
│   └── THL.Host/          # 宿主悬浮窗工程 (THL.Host.dll)
├── 2_Core/
│   └── THL.Contracts/     # 接口规矩工程 (THL.Contracts.dll)
└── 3_Plugins/
    ├── AutoGrid/          # 一键轴网插件 (AutoGrid.dll)
    └── CleanView/         # 视图清理插件 (CleanView.dll)
```

## 构建

```bash
dotnet build Tianheluo.sln
```

## 发布工作流

1. 编译插件 → DLL 上传 Cloudflare R2 → 更新 `tianheluo-web/src/store/*.md` 下载链接
2. 网页仓库 git push → Cloudflare Pages 自动更新