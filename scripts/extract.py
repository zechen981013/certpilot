#!/usr/bin/env python3
"""把 web/index.html 里的五个数据数组抠出来，输出 data/content.json。

用 node 求值而不是手写 JS 解析器：数组里有模板串、中文引号、转义，
正则硬解会在边角上出错，node 是唯一可信的裁判。node 只在本机用，服务器不需要。
"""
import json, pathlib, re, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "index.html"
OUT = ROOT / "data" / "content.json"
ARRAYS = ["schedule", "checklistData", "notesData", "quizData", "zhentiData"]


def main():
    if not shutil.which("node"):
        sys.exit("需要 node 来求值 JS 数组（brew install node）")

    html = SRC.read_text(encoding="utf-8")
    blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
    if len(blocks) != 1:
        sys.exit(f"预期 1 个 <script> 块，实际 {len(blocks)}")
    js = blocks[0]

    parts = []
    for name in ARRAYS:
        m = re.search(r"(const %s = \[.*?\n\];)" % name, js, re.S)
        if not m:
            sys.exit(f"找不到 {name} 的声明")
        parts.append(m.group(1))

    prog = "\n".join(parts) + """
// qkey 必须和前端 qid() 完全一致：source|subject|chapter|stem
const qkey = q => (q.source||'基础题')+'|'+q.subject+'|'+(q.chapter||'')+'|'+q.q;
const all = quizData.concat(zhentiData).map((q,i)=>({
  qkey: qkey(q), subject: q.subject, chapter: q.chapter || null,
  kind: q.kind, source: q.source, stem: q.q, opts: q.opts,
  correct: q.correct, explain: q.explain || null, ord: i,
  pool: i < quizData.length ? 'base' : 'zhenti',
}));
process.stdout.write(JSON.stringify({
  questions: all,
  schedule, checklistData, notesData,
}));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as f:
        f.write(prog)
        tmp = f.name
    raw = subprocess.run(["node", tmp], capture_output=True, text=True)
    pathlib.Path(tmp).unlink()
    if raw.returncode:
        sys.exit(f"node 求值失败：\n{raw.stderr}")

    data = json.loads(raw.stdout)
    qs = data["questions"]

    # ---- 校验：抽错了宁可现在炸，不要等上线才发现 ----
    keys = [q["qkey"] for q in qs]
    assert len(qs) == 422, f"题目应为 422 道，实际 {len(qs)}"
    assert len(set(keys)) == len(keys), "qkey 有重复，前端错题本会串题"
    assert sum(1 for q in qs if q["pool"] == "base") == 122, "基础题应为 122 道"
    bad = [q["qkey"][:40] for q in qs
           if not isinstance(q["opts"], list) or not isinstance(q["correct"], int)
           or not (0 <= q["correct"] < len(q["opts"]))]
    assert not bad, f"选项/答案不合法：{bad[:3]}"
    assert len(data["schedule"]) == 14, f"日程应为 14 天，实际 {len(data['schedule'])}"
    assert data["checklistData"] and data["notesData"]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")

    srcs = {}
    for q in qs:
        srcs[q["source"]] = srcs.get(q["source"], 0) + 1
    print(f"题目 {len(qs)} 道  {srcs}")
    print(f"日程 {len(data['schedule'])} 天 · 清单 {len(data['checklistData'])} 组 "
          f"· 笔记 {len(data['notesData'])} 组")
    print(f"缺解析 {sum(1 for q in qs if not q['explain'])} 道")
    print(f"→ {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")


main()
