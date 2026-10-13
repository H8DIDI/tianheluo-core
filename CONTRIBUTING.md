# 参与贡献

天河落欢迎两种贡献：**投稿插件**、**改进宿主/网站**。

## 投稿插件（最常见）
1. 下载 AI 说明书 <https://tianheluo.com/tianheluo-ai-spec.txt>，交给你的 AI，让它按规范做插件。
2. 运行自查：`python validate.py 你的插件目录`（单文件，见网站 `tools/validate.py`）。
3. 压缩成 zip，发邮件到 jiajiabimer@gmail.com，主题 `[天河落插件投稿] 插件名 v版本`；或加微信 didi 进交流群说明。
4. 维护者人工审核源码，通过后用 `tools/import_submission.py` 导入并上架，署名为你。

更新自己的插件：**version 必须递增**，否则用户收不到更新。

## 改进宿主 / 网站
- 提 issue 说明问题或想法；改动较大请先讨论。
- 构建：`python tools/build_all.py`；测试：`tests/StoreTests`、`tests/UpdateTests`、`python tests/test_apply_update.py`、`python tests/test_validate.py`（均需通过）。
- 改动破坏插件接口时：`Store.HostApiVersion` 加 1，并在 `CHANGELOG.md` 写明。
- 提交前请确认没有密钥、个人路径、测试数据。
