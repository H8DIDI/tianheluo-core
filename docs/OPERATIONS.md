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
- 微信二维码过期（个人群码约 7 天，个人码更久）：替换网站仓库根目录 `wechat-didi.png`，再运行 `powershell -ExecutionPolicy Bypass -File toolsesize_qr.ps1` 重新生成页脚用的小图 `wechat-didi-sm.png`（两个文件都要提交），然后推送。
- 每季度：核对 Cloudflare 令牌权限（最小权限）、检查 D1 `downloads` 数据是否异常。

## 线上组成
- 网站：Cloudflare Pages（仓库 `H8DIDI/tianheluo-web`），Functions：`/api/dl/<id>`、`/api/stats`；D1 数据库 `tianheluo-stats`（绑定名 `DB`，密钥 `HASH_SALT`）。
- 宿主源码：私有→计划公开的 `H8DIDI/tianheluo-core`（本仓库）。
- Pages 偶发“Failed to publish your Function”：到控制台重试部署，再运行 `publish.py` 核对。

## 本机测试注意
- 给 Revit 用的文件（插件、配置）要用 PowerShell 写；Bash 工具看到的 `%APPDATA%` 与 Revit 不是同一视图。
- 不要对真实项目运行会改模型的插件做测试。

## 网站运维与搜索（SEO）
**日常**
- 改了页面、插件清单或 `content/` 里的文案后，运行 `python tools/build_seo.py`（`publish.py` 会自动运行并接着跑 `tools/check_site.py`）。它负责：插件详情页 `/plugins/<id>`、各页导航/页脚、首页与商店的静态列表、资源文件的版本号、结构化数据、`sitemap.xml`。
- 插件长文案写在网站仓库 `content/plugins/<id>.json`（解决什么问题 / 怎么用 / 注意事项 / SEO 标题 / 示意图）。它不属于插件包，改了不用升版本；`summary` 会同步写回 `plugins.json`。示意图在 `img/plugins/`，有真实截图时换掉文件并改 json 里的 `image.src`。
- 新页面一律用干净网址（`/store` 而不是 `/store.html`）；每页只能有一个 `<h1>`。

**自动监控（不用开电脑）**
- GitHub Actions `ci.yml`：每次提交检查“生成的页面是否已更新、站内链接是否有效、JSON 是否合法”。
- GitHub Actions `health.yml`：每天一次检查线上（页面、301/404、插件包与宿主包的 SHA256、`/api/stats`、证书剩余天数、`security.txt` 有效期）。失败会标红并给仓库所有者发邮件。本地也能跑：`python tools/healthcheck.py [--quick]`。

**搜索引擎**
- Google Search Console：域名 DNS 里已有 `google-site-verification` 记录；到 Search Console 确认后提交 `https://tianheluo.com/sitemap.xml`。Bing Webmaster Tools、百度搜索资源平台各验证一次（Bing 可直接导入 Search Console）。
- `publish.py` 发布成功后会自动运行 `tools/indexnow.py`，通知必应、Yandex 等；设置环境变量 `BAIDU_PUSH_TOKEN`（百度搜索资源平台 → 普通收录 → 接口调用地址里的 token）后会同时推送百度。
- 站点在 Cloudflare（境外、未备案），百度收录会比较慢，这是预期的。

**Cloudflare 配置（已做，记录在此以便排查）**
- 重定向规则：`www.tianheluo.com` → `https://tianheluo.com`（301，保留路径和参数）。
- 限流规则：`tianheluo.com/api/*` 每个 IP 每 10 秒最多 40 次，超出封禁 10 秒（保护 Functions 额度和 D1）。
- API 令牌保存在本机用户环境变量 `CLOUDFLARE_API_TOKEN`（账户 `CLOUDFLARE_ACCOUNT_ID`），**不要提交进任何仓库**；到期日 2027-04-19，到期前换新令牌。
- 注意：区域 SSL 模式目前是 **Flexible**。对 Pages 站点没有影响，但同一域名下指向服务器 IP 的其他子域名，Cloudflare 到源站这一段是明文。确认这些服务器都支持 HTTPS 后，建议改成 Full 或 Full (strict)。

**每年一次**
- `.well-known/security.txt` 的 `Expires` 到期前续期（健康检查会在到期前 45 天提醒）。
- 核对 Cloudflare 令牌权限和到期时间。
