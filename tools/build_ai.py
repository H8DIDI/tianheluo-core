"""生成给 AI 读取的全部文件（单一事实来源：thl/docs/*.md + 网站 examples/）：
    site/ai.md                    完整说明书（Markdown，含三个示例的完整源码）
    site/ai.html                  同上，服务端渲染成网页（爬虫/AI 不需要执行 JS）
    site/tianheluo-ai-spec.txt    同上 + 给 AI 的开头说明；可下载，用户直接上传/粘贴给自己的聊天 AI（不依赖任何链接）
用法：python tools/build_ai.py   （需要 pip install markdown）"""
import pathlib
import markdown

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = pathlib.Path(r"C:\KAIFA\tianheluo-web")
DOCS = ROOT / "docs"

head = (DOCS / "spec_head.md").read_text(encoding="utf-8")
spec = (DOCS / "ai_spec.md").read_text(encoding="utf-8")
tail = (DOCS / "spec_tail.md").read_text(encoding="utf-8")
# ai_spec.md 自带标题和引言，去掉，从「## 1.」开始（标题与引言由 spec_head 提供）
spec = spec[spec.index("## 1. "):]


def appendix():
    out = ["\n---\n\n## 附录：三个完整示例插件（全文）\n",
           "下面是三个已通过校验的完整插件，可以直接照着写。每个插件都是一个文件夹，含 `plugin.json`、`script.py`、`README.md`、`LICENSE`（LICENSE 为 MIT 全文，此处省略，使用时把年份和署名改成你自己的）。\n"]
    titles = {"hello-world": "示例 A：hello-world（最小插件，只读）",
              "count-by-category": "示例 B：count-by-category（只读统计：选择范围 + 收集器 + 结果弹窗）",
              "set-comments": "示例 C：set-comments（写模型的标准流程：选择 → 输入 → 预览确认 → 单事务写入 → 统计 → 可撤销）"}
    for pid, title in titles.items():
        d = SITE / "examples" / pid
        out.append(f"\n### {title}\n")
        for name, lang in (("plugin.json", "json"), ("script.py", "python"), ("README.md", "markdown")):
            txt = (d / name).read_text(encoding="utf-8").rstrip("\n")
            out.append(f"\n**`{pid}/{name}`**\n\n```{lang}\n{txt}\n```\n")
    return "".join(out)


full = head + spec + tail + appendix()
(SITE / "ai.md").write_text(full, encoding="utf-8")

# ---- HTML
body = markdown.markdown(full, extensions=["tables", "fenced_code", "sane_lists"])
html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Revit 插件 AI 开发说明书（给 ChatGPT / Claude 读）— 天河落 THL</title>
<meta name="description" content="天河落平台专门给 AI 阅读的开发说明书：规则、接口、完整示例、常见错误、交付格式。让你的 AI 读完就能开发 Revit 插件。">
<link rel="canonical" href="https://tianheluo.com/ai">
<meta property="og:type" content="website"><meta property="og:site_name" content="天河落 Tianheluo">
<meta property="og:title" content="Revit 插件 AI 开发说明书（给 ChatGPT / Claude 读）— 天河落 THL"><meta property="og:description" content="天河落平台专门给 AI 阅读的开发说明书：规则、接口、完整示例、常见错误、交付格式。让你的 AI 读完就能开发 Revit 插件。">
<meta property="og:url" content="https://tianheluo.com/ai"><meta property="og:image" content="https://tianheluo.com/og-image.png"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image"><meta name="robots" content="index,follow,max-image-preview:large">
<meta name="theme-color" content="#070709">
<link rel="icon" href="/favicon-production.png">
<link rel="alternate" type="text/markdown" href="/ai.md" title="Markdown 原文">
<link rel="stylesheet" href="/style.css">
<link rel="stylesheet" href="/fx.css">
</head>
<body>
<header class="nav" id="nav"></header>
<main class="wrap doc">
<div class="doc-note">
  <b>给 AI 的说明书（自包含）。</b> 不能联网的 AI？
  <a class="btn sm pri" href="/tianheluo-ai-spec.txt" download>下载 txt 说明书</a>
  然后上传或粘贴给你的 AI。 · Markdown 原文 <a href="/ai.md">/ai.md</a> · 索引 <a href="/llms.txt">/llms.txt</a> · <a href="/developers">开发者页面</a>
</div>
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

# ---- 可下载的 txt（给聊天 AI 用）
preamble = """【给 AI 的使用说明 · 请先读这段】
下面是「天河落」Revit 插件平台的完整开发说明书（Markdown 格式）。它是自包含的：全部规则、可用接口、
常见错误、交付格式和三个完整示例插件的源码都在里面，你不需要访问任何网页。

请你按以下步骤做：
  1. 完整读完说明书。
  2. 回复用户：「已读完天河落开发说明书。请告诉我你想做什么插件，以及你的署名（author）。」
  3. 等用户描述需求后，严格按说明书「第 12 节」的流程和「第 14 节」的交付格式，输出完整的插件文件
     （plugin.json、script.py、README.md、LICENSE）。
  4. 你无法运行用户的 Revit，不要声称已测试；要提醒用户先在空白测试项目里试，并把报错发回来继续改。

============================================================

"""
(SITE / "tianheluo-ai-spec.txt").write_text(preamble + full, encoding="utf-8")

print("ai.md   %d KB" % (len(full.encode()) // 1024))
print("ai.html %d KB" % (len(html.encode()) // 1024))
print("tianheluo-ai-spec.txt %d KB" % ((len(preamble) + len(full)) * 3 // 2 // 1024))
