# 安全策略 Security Policy

天河落是公开共享、非商业的开源项目。代码全部公开，欢迎审查。

## 报告漏洞
请**不要**在公开 issue 里贴可被利用的细节。发邮件到 **jiajiabimer@gmail.com**，标题写 `[THL 安全]`，说明：
受影响的位置（宿主 / 网站 / 某个插件）、复现步骤、可能的影响。我会尽快回复并在修复后致谢（如你愿意署名）。

## 已有的防护（实现位置）
- 宿主更新包与插件包下载后校验 SHA256（`THL.Host/Update.cs`、`THL.Host/Store.cs`）。
- 解压时防路径穿越（zip-slip）；宿主更新失败自动回滚。
- 插件版本声明 `hostApi`，宿主过旧时拒绝安装而不是出错。
- 社区投稿插件：自查脚本 `validate.py` 静态扫描 + 维护者人工审核源码 + 安装前提示。
- 下载计数不保存原始 IP：只存加盐哈希的网段（用于 1 小时内去重，约 2 天后清理），并记下 Cloudflare 估算的城市级地区（国家、省州、城市、大致经纬度，保留 365 天，暂不对外展示）（`functions/api/dl/[id].js`、`migrations/download_geo.sql`）。
- 网站响应头：CSP、`X-Frame-Options: DENY`、HSTS、`nosniff`、Permissions-Policy（`_headers`）。

## 需要你知道的限制
插件（Python 或 DLL）以**当前 Windows 用户**的权限运行，天河落不是沙箱。请只安装你信任来源的插件；社区插件会在安装前提示。
