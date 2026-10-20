#!/usr/bin/env python3
"""Phase 0: rename the product, strip every source-attribution trace, relabel 讲义→基础题."""
import pathlib, sys

SRC = pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html"
TITLE = "14天备考证券从业考试"

# (description, old, new, expected_count)   expected_count None = at least 1
EDITS = [
    ("<title>",
     "<title>14天冲刺 · 证券从业资格考试</title>",
     f"<title>{TITLE}</title>", 1),

    ("iOS 添加到主屏幕的名称",
     '<meta name="apple-mobile-web-app-title" content="14天冲刺">',
     '<meta name="apple-mobile-web-app-title" content="14天备考">', 1),

    ("masthead h1",
     "<h1>14天冲刺计划</h1>",
     f"<h1>{TITLE}</h1>", 1),

    ("schedule lede — 去掉「不用再翻 PDF」",
     '每天提到的章节，内容都在"浓缩笔记" tab 里——不用再翻 PDF。',
     '每天提到的章节，正文都在"笔记" tab 里，点过去就能看。', 1),

    ("checklist lede — 去掉「来自你上传的讲义目录」",
     "来自你上传的讲义目录，学完一节就勾掉",
     "按考试大纲的章节结构拆分，学完一节就勾掉", 1),

    ("notes lede — 去掉「三色笔记」",
     "浓缩笔记与三色笔记已合并为一套：按章逐条罗列考点，",
     "按章逐条罗列考点，", 1),

    ("quiz lede — 去掉「讲义题 / 慧考解析」",
     "讲义题 122 道 ＋ 2023–2024 年真题 300 道，共 <b>422</b> 道。讲义题按章节筛选"
     "（科目一含讲义解析，科目二为对照法条整理）；真题按年份 / 科目筛选，解析为原卷「慧考」解析。"
     "答错自动进「错题本」。",
     "基础题 122 道 ＋ 2023–2024 年历年真题 300 道，共 <b>422</b> 道，全部自编解析。"
     "基础题按章节筛选，真题按年份 / 科目筛选。答错自动进「错题本」。", 1),

    ("footer — 换成免责声明",
     "说明：日程与清单基于所提供讲义的目录结构与抽样内容整理，用作学习框架；"
     "具体考点细节请以讲义原文为准。",
     "说明：本产品为自编备考工具，非官方教材，不含任何第三方出版物内容；"
     "考点范围以中国证券业协会最新考试大纲为准。", 1),

    ("Day 12 文案 — 统一口径",
     "掐表限时刷完一整套 100 题，中间不查资料，模拟真实考场。",
     "掐表限时刷完一整套 100 题，中间不翻笔记，模拟真实考场。", 1),

    # ---- 讲义 → 基础题 ----
    ("题库来源标签", "source:'讲义'", "source:'基础题'", 122),
    ("qid() 默认来源", "(q.source||'讲义')", "(q.source||'基础题')", 1),
    ("SRC_LABEL", "{'讲义':'讲义题',", "{'基础题':'基础题',", 1),
    ("筛选器 value", "'src:讲义'", "'src:基础题'", 1),
    ("筛选器 value (模板)", '"src:讲义"', '"src:基础题"', 1),
    ("筛选器 option 文案", "讲义题（${quizData.length}）", "基础题（${quizData.length}）", 1),
    ("筛选器 optgroup", 'label="讲义题 · 按章节"', 'label="基础题 · 按章节"', 1),
    ("无解析兜底文案", "该题讲义未附解析，正确答案已高亮显示。",
     "该题暂无解析，正确答案已高亮显示。", 1),
    ("日程正文里的「浓缩笔记 →」", "浓缩笔记 → ", "笔记 → ", None),
]

html = SRC.read_text(encoding="utf-8")
fails, applied = [], []

for desc, old, new, want in EDITS:
    n = html.count(old)
    if n == 0:
        fails.append(f"  MISS  {desc}: 找不到目标字符串")
        continue
    if want is not None and n != want:
        fails.append(f"  COUNT {desc}: 期望 {want} 处，实际 {n} 处")
        continue
    html = html.replace(old, new)
    applied.append(f"  ok    {desc}  ({n}×)")

print("\n".join(applied))
if fails:
    print("\n未应用：\n" + "\n".join(fails), file=sys.stderr)
    sys.exit(1)

SRC.write_text(html, encoding="utf-8")
print(f"\n写入 {SRC}  ({len(html):,} bytes)")
