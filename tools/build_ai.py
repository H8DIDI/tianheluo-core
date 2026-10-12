"""把网站仓库里的 ai.md 渲染成 ai.html（服务端渲染，爬虫/AI 不需要执行 JS 就能读到全文）。
用法：python tools/build_ai.py   （需要 pip install markdown）"""
import pathlib
import markdown

SITE = pathlib.Path(r"C:\KAIFA\tianheluo-web")
md_text = (SITE / "ai.md").read_text(encoding="utf-8")
body = markdown.markdown(md_text, extensions=["tables", "fenced_code", "sane_lists", "toc"])

html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI 开发规范 — 天河落</title>
<meta name="description" content="天河落平台的机器可读开发规范：宿主架构、plugin.json、Python 运行环境、可用接口、安全规则、投稿流程。让你的 AI 读完就能开发 Revit 插件。">
<meta name="theme-color" content="#070709">
<link rel="icon" href="/favicon-production.png">
<link rel="alternate" type="text/markdown" href="/ai.md" title="Markdown 原文">
<link rel="stylesheet" href="/style.css">
<link rel="stylesheet" href="/fx.css">
</head>
<body>
<header class="nav" id="nav"></header>
<main class="wrap doc">
<p class="doc-note">这是给 AI 助手读取的规范页面。Markdown 原文：<a href="/ai.md">/ai.md</a> · 索引：<a href="/llms.txt">/llms.txt</a> · 给人看的介绍：<a href="/developers">开发者页面</a></p>
<article>
{body}
</article>
</main>
<footer id="foot"></footer>
<script src="/app.js"></script>
<script src="/fx.js"></script>
</body>
</html>
"""
(SITE / "ai.html").write_text(html, encoding="utf-8")
print("ai.html 已生成：", len(html) // 1024, "KB")
