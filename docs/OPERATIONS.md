# 运营手册（维护者用）

## 日常流程
1. 收到投稿/建议（邮件、微信群）。
2. 投稿：`python tools/import_submission.py <zip>` 审核源码并导入 `community/<id>/`。
   自己改插件：改 `plugins/` 或 `community/` 下的源码，**升 `plugin.json` 的 version**。
3. 一键发布：`python tools/publish.py -m "说明"`
   （构建 → 单元测试 → 发布前检查 → 推送网站和源码 → 核对线上文件 SHA256）。
   先看看会发布什么：加 `--dry-run`。
4. 在 `CHANGELOG.md` 记一笔（宿主有变化时）。

## 发布前检查会拦住什么
- 内容变了但 version 没升（用户收不到更新）／version 降低。
- 清单 sha256 与包不一致、包缺失、`hostApi` 高于宿主支持。
- 宿主同理：改了代码必须在 `THL.Host.csproj` 升 `InformationalVersion/AssemblyVersion/FileVersion`。
只改了元数据又不想升版本时：`--allow-same-version <id|all|host>`。

## 发布宿主新版本
改 `THL.Host/THL.Host.csproj` 的三处版本号 → 改 `docs/host_notes.txt` 第一段（更新说明）→ `publish.py`。
已安装用户在插件中心看到更新提示，关闭 Revit 时自动安装。
**破坏插件接口**：`Store.HostApiVersion` +1，发布说明写明；旧宿主遇到新接口的插件会提示更新宿主。

## 一次性/年度事项
- Revit 新大版本（如 2027）：新增构建目标并在真实 Revit 里验证（net8/net10 的对应关系可能变化）。
- 微信二维码过期（个人群码约 7 天，个人码更久）：替换网站仓库根目录 `wechat-didi.png` 后推送。
- 每季度：核对 Cloudflare 令牌权限（最小权限）、检查 D1 `downloads` 数据是否异常。

## 线上组成
- 网站：Cloudflare Pages（仓库 `H8DIDI/tianheluo-web`），Functions：`/api/dl/<id>`、`/api/stats`；D1 数据库 `tianheluo-stats`（绑定名 `DB`，密钥 `HASH_SALT`）。
- 宿主源码：私有→计划公开的 `H8DIDI/tianheluo-core`（本仓库）。
- Pages 偶发“Failed to publish your Function”：到控制台重试部署，再运行 `publish.py` 核对。

## 本机测试注意
- 给 Revit 用的文件（插件、配置）要用 PowerShell 写；Bash 工具看到的 `%APPDATA%` 与 Revit 不是同一视图。
- 不要对真实项目运行会改模型的插件做测试。
