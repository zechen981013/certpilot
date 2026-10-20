#!/usr/bin/env python3
"""web/index.html + web/shell.js  →  dist/index.html

把内容数组从页面里摘掉，装上授权闸门，并把 LS 换成写穿 API 的缓存层。
换 LS 是关键：原有 15 处状态读写一行都不用动，进度自动变成绑账号的。

每条替换都带计数断言，改不动就中止——450 KB 的文件不能靠肉眼确认。
"""
import pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "index.html"
SHELL = ROOT / "web" / "shell.js"
OUT = ROOT / "dist" / "index.html"

GATE_CSS = """
/* ==================== 闸门 ==================== */
#gate{
  display:none; min-height:100dvh; align-items:center; justify-content:center;
  padding:40px 20px;
  /* 不是纯平的底色：一层极淡的暖光从印章后面透出来 */
  background:
    radial-gradient(120% 80% at 50% -10%, rgba(201,162,39,.075), transparent 62%),
    radial-gradient(90% 60% at 50% 108%, rgba(76,154,106,.045), transparent 60%),
    var(--ink);
}
.gate-card{
  width:100%; max-width:400px; padding:40px 34px 28px; text-align:center;
  background:linear-gradient(180deg, rgba(35,45,56,.92), rgba(27,35,44,.96));
  border:1px solid var(--line); border-radius:3px;
  box-shadow:0 1px 0 rgba(237,231,216,.05) inset, 0 24px 60px -20px rgba(0,0,0,.7);
}
.gate-card > *{ animation:gateRise .55s cubic-bezier(.22,.68,.3,1) both; }
.gate-card > *:nth-child(1){animation-delay:.02s} .gate-card > *:nth-child(2){animation-delay:.07s}
.gate-card > *:nth-child(3){animation-delay:.12s} .gate-card > *:nth-child(4){animation-delay:.17s}
.gate-card > *:nth-child(5){animation-delay:.21s} .gate-card > *:nth-child(6){animation-delay:.25s}
.gate-card > *:nth-child(7){animation-delay:.29s}
@keyframes gateRise{ from{opacity:0; transform:translateY(9px);} to{opacity:1; transform:none;} }

/* 印章：双环 + 呼吸，像盖上去的而不是画上去的 */
.gate-seal{
  position:relative; width:68px; height:68px; margin:0 auto 20px;
  display:flex; flex-direction:column; align-items:center; justify-content:center;
  font-family:'Noto Serif SC',serif; color:var(--gold); line-height:1.05;
  border:1.5px solid var(--gold); border-radius:50%;
  box-shadow:0 0 26px -6px rgba(201,162,39,.4);
}
.gate-seal::after{
  content:''; position:absolute; inset:-6px; border-radius:50%;
  border:1px solid rgba(201,162,39,.22);
  animation:sealRing 4.5s ease-in-out infinite;
}
@keyframes sealRing{ 0%,100%{transform:scale(1); opacity:.5;} 50%{transform:scale(1.055); opacity:.16;} }
.gate-seal b{font-size:21px; letter-spacing:.02em;}
.gate-seal span{font-size:8px; letter-spacing:.18em; color:var(--gold-dim); margin-top:1px;}

.gate-card h1{
  font-family:'Noto Serif SC',serif; font-size:22.5px; font-weight:700;
  margin:0 0 10px; letter-spacing:.02em; text-wrap:balance;
}
.gate-sub{
  color:var(--dim,var(--text-dim)); font-size:12.5px; line-height:1.85; margin:0 0 24px;
  letter-spacing:.01em;
}
.gate-sub b{color:var(--gold); font-weight:500;}

/* 页签：下划线滑动，而不是各自闪 */
.gate-tabs{
  position:relative; display:flex; margin-bottom:22px;
  border-bottom:1px solid var(--line);
}
.gate-tab{
  flex:1; background:none; border:none; color:var(--text-dim);
  font-family:inherit; font-size:14px; font-weight:500; letter-spacing:.02em;
  padding:10px 4px 11px; cursor:pointer;
  transition:color .22s ease;
}
.gate-tab.on{color:var(--paper);}
.gate-tab:hover:not(.on){color:rgba(237,231,216,.82);}
.gate-underline{
  position:absolute; bottom:-1px; left:0; height:2px; width:50%;
  background:var(--gold); border-radius:2px;
  transition:transform .32s cubic-bezier(.22,.68,.3,1);
}
.gate-tabs[data-mode="activate"] .gate-underline{ transform:translateX(100%); }

.gate-field{display:block; text-align:left; margin-bottom:15px;}
.gate-field.hide{display:none;}
.gate-field span{
  display:block; font-size:10.5px; letter-spacing:.14em; color:var(--text-dim);
  margin-bottom:6px; text-transform:uppercase; font-weight:500;
}
.gate-field input{
  width:100%; background:rgba(18,24,31,.85); border:1px solid var(--line);
  color:var(--paper); font-family:inherit; font-size:14.5px;
  padding:11px 13px; border-radius:3px;
  transition:border-color .18s ease, box-shadow .18s ease, background-color .18s ease;
}
.gate-field input::placeholder{color:rgba(237,231,216,.28); font-size:12.5px;}
.gate-field input:hover{border-color:rgba(237,231,216,.24);}
.gate-field input:focus{
  outline:none; border-color:var(--gold); background:rgba(18,24,31,1);
  box-shadow:0 0 0 3px rgba(201,162,39,.13);
}
.gate-field input[name="code"]{
  font-family:ui-monospace,'SF Mono',Menlo,monospace;
  letter-spacing:.14em; text-transform:uppercase; font-size:15px;
}

/* Chrome 自动填充会把输入框刷成黄色——用超长过渡的内阴影盖回深色 */
input:-webkit-autofill,
input:-webkit-autofill:hover,
input:-webkit-autofill:focus,
input:-webkit-autofill:active{
  -webkit-box-shadow:0 0 0 1000px #12181F inset !important;
  -webkit-text-fill-color:var(--paper) !important;
  caret-color:var(--paper);
  transition:background-color 100000s ease-in-out 0s;
}

.gate-go{
  width:100%; border:none; cursor:pointer; margin-top:6px;
  font-family:inherit; font-size:15px; font-weight:700; letter-spacing:.04em;
  padding:13px; border-radius:3px; color:#1a1408;
  background:linear-gradient(180deg,#D8B03A,#C9A227);
  box-shadow:0 1px 0 rgba(255,255,255,.28) inset, 0 6px 18px -8px rgba(201,162,39,.75);
  transition:transform .12s ease, box-shadow .2s ease, filter .2s ease;
}
.gate-go:hover:not(:disabled){ filter:brightness(1.07); box-shadow:0 1px 0 rgba(255,255,255,.3) inset, 0 9px 24px -8px rgba(201,162,39,.85); }
.gate-go:active:not(:disabled){ transform:translateY(1px); box-shadow:0 1px 0 rgba(255,255,255,.2) inset; }
.gate-go:disabled{ opacity:.5; cursor:default; }
.gate-go:focus-visible{ outline:2px solid var(--paper); outline-offset:3px; }

.gate-err{
  color:var(--brick); font-size:12.5px; min-height:20px; text-align:left;
  line-height:1.55; padding-top:2px;
}
.gate-err:not(:empty){ animation:errShake .34s ease; }
@keyframes errShake{ 0%,100%{transform:none} 22%{transform:translateX(-4px)} 58%{transform:translateX(3px)} }

.gate-msg{
  font-size:12.5px; line-height:1.7; text-align:left; padding:12px 14px;
  margin-bottom:18px; border-radius:3px;
}
.gate-msg.warn{
  background:rgba(201,162,39,.09); border:1px solid rgba(201,162,39,.4);
  color:rgba(237,231,216,.9);
}
.gate-foot{
  color:var(--text-dim); font-size:11px; line-height:1.85; margin:22px 0 0;
  padding-top:16px; border-top:1px solid var(--line); letter-spacing:.01em;
}

/* ==================== 账号条 ==================== */
.acct-bar{
  display:flex; align-items:center; gap:10px; flex-wrap:wrap;
  font-size:12.5px; color:var(--text-dim);
  padding:9px 13px; margin-bottom:20px;
  background:rgba(35,45,56,.5); border:1px solid var(--line); border-radius:3px;
}
.acct-who{color:var(--paper); font-weight:500;}
.acct-tag{
  color:var(--gold); border:1px solid var(--gold-dim); background:rgba(201,162,39,.08);
  padding:1px 7px; border-radius:2px; font-size:10px; letter-spacing:.08em;
}
.acct-exp{
  font-family:ui-monospace,Menlo,monospace; font-size:12px;
  padding:1px 8px; border-radius:2px;
  background:rgba(76,154,106,.12); color:#7BC49B;
}
.acct-exp.warn{background:rgba(181,84,75,.16); color:#E08880; font-weight:700;}
.acct-dev{margin-left:auto; font-family:ui-monospace,Menlo,monospace; font-size:11.5px; opacity:.75;}
.acct-out{
  background:none; border:1px solid var(--line); color:var(--text-dim);
  font-family:inherit; font-size:11.5px; padding:3px 11px; border-radius:2px;
  cursor:pointer; transition:color .18s ease, border-color .18s ease;
}
.acct-out:hover{color:var(--paper); border-color:var(--text-dim);}
#watermark{
  display:block; margin-top:10px; color:rgba(237,231,216,.3);
  font-size:11px; letter-spacing:.03em;
}

@media (max-width:420px){
  .gate-card{padding:32px 22px 24px;}
  .acct-dev{margin-left:0;}
}
@media (prefers-reduced-motion:reduce){
  *,*::after{animation:none !important; transition:none !important;}
}
"""

NEW_LS = """/* ---------- STORAGE（写穿到服务端） ----------
   wrong / stats 由 attempts 算出，只读；doneDays / checklist / notesOpen /
   examDate 走 progress 表。服务端是唯一真相，localStorage 只当缓存。 */
const LS = (function(){
  const SRV = window.__SRV || {wrong:[], stats:{}, progress:{}};
  const SYNCED = {doneDays:1, checklist:1, notesOpen:1, examDate:1};
  const seed = {
    wrong: SRV.wrong, stats: SRV.stats,
    doneDays: SRV.progress.doneDays, checklist: SRV.progress.checklist,
    notesOpen: SRV.progress.notesOpen, examDate: SRV.progress.examDate
  };
  const pending = {};
  let timer = null;
  function flush(){
    timer = null;
    Object.keys(pending).forEach(function(k){
      const v = pending[k]; delete pending[k];
      call('/progress', {method:'PUT', body:{key:k, value:v}}).catch(function(){});
    });
  }
  function queue(k, v){
    if(!SYNCED[k]) return;
    pending[k] = v;
    if(!timer) timer = setTimeout(flush, 1200);
  }
  return {
    get(k, d){
      if(seed[k] !== undefined && seed[k] !== null) return seed[k];
      try{ const v = localStorage.getItem('sp_'+k); return v==null ? d : JSON.parse(v); }
      catch(e){ return d; }
    },
    set(k, v){
      try{ localStorage.setItem('sp_'+k, JSON.stringify(v)); }catch(e){}
      seed[k] = v; queue(k, v);
    },
    del(k){
      try{ localStorage.removeItem('sp_'+k); }catch(e){}
      delete seed[k]; queue(k, k === 'examDate' ? null : []);
    }
  };
})();"""


def main():
    html = SRC.read_text(encoding="utf-8")
    shell = SHELL.read_text(encoding="utf-8")
    n0 = len(html)

    # 数据数组的边界：从 DATA 注释一路到 ALL_Q
    data_re = re.compile(
        r"/\* -+ DATA -+ \*/\n.*?const ALL_Q = quizData\.concat\(zhentiData\);",
        re.S)
    if not data_re.search(html):
        sys.exit("找不到数据段边界")

    edits = [
        # 1. 闸门样式
        ("闸门 CSS", "</style>", GATE_CSS + "</style>", 1),

        # 2. body：闸门容器 + 应用容器 + 账号条
        ("body 容器", '<body>\n<div class="wrap">\n',
         '<body>\n<div id="gate"></div>\n<div id="appRoot" style="display:none">\n'
         '<div class="wrap">\n\n  <div class="acct-bar" id="acctBar"></div>\n', 1),

        # 3. 收尾：水印 + 关掉 appRoot
        ("appRoot 收尾", "  </footer>\n\n</div>\n\n<script>",
         '  </footer>\n\n</div>\n</div>\n\n<script>', 1),

        # 4. 页脚那句「存在这台设备」已经不成立了
        ("页脚存储说明",
         "进度（日程、清单、笔记展开、刷题记录、考试日期）自动保存在这台设备的当前浏览器里"
         "——换设备、换浏览器或清除网站数据会丢失。",
         "进度、错题本与正确率均保存于账号，更换设备或浏览器后登录即可延续；"
         "本机仅留一份缓存，清除网站数据不影响进度。", 1),

        # 5. 水印挂进页脚（外流截图能定位到人）
        ("水印节点",
         '<div style="margin-top:12px"><button class="reset-btn" id="resetAll">清空全部进度</button></div>',
         '<span id="watermark"></span>'
         '<div style="margin-top:12px"><button class="reset-btn" id="resetAll">清空全部进度</button></div>', 1),

        # 6. 答题上报要知道选了哪个、想了多久
        ("recordAnswer 签名",
         "function recordAnswer(item, correct){",
         "function recordAnswer(item, correct, chosen){", 1),
        ("recordAnswer 上报",
         "  LS.set('stats', stats);\n  LS.set('wrong', [...wrongSet]);\n"
         "  buildFilterOptions();\n  renderStats();\n}",
         "  LS.set('stats', stats);\n  LS.set('wrong', [...wrongSet]);\n"
         "  Q.push({qkey: qid(item), chosen: (chosen == null ? null : chosen),\n"
         "          is_correct: !!correct,\n"
         "          ms_spent: _shownAt ? Math.min(600000, Date.now() - _shownAt) : null});\n"
         "  buildFilterOptions();\n  renderStats();\n}", 1),
        ("答题调用点", "      recordAnswer(item, correct);",
         "      recordAnswer(item, correct, i);", 1),
        ("计时起点",
         "  const item = activeQuizData[qIndex];\n  answered = false;",
         "  const item = activeQuizData[qIndex];\n  _shownAt = Date.now();\n  answered = false;", 1),
        ("计时变量",
         "let qIndex = 0, score = 0, answered = false;",
         "let qIndex = 0, score = 0, answered = false, _shownAt = 0;", 1),

        # 7. 清空刷题记录 → 服务端也清
        ("清空刷题记录",
         "    stats = {}; wrongSet = new Set();\n"
         "    LS.set('stats', stats); LS.set('wrong', []);\n"
         "    buildFilterOptions(); renderStats(); applyFilter();",
         "    stats = {}; wrongSet = new Set();\n"
         "    LS.set('stats', stats); LS.set('wrong', []);\n"
         "    try{ localStorage.removeItem('sp_outbox'); }catch(e){}\n"
         "    call('/attempts', {method:'DELETE'}).catch(function(){});\n"
         "    buildFilterOptions(); renderStats(); applyFilter();", 1),

        # 8. 清空全部进度 → 服务端也清
        ("清空全部进度",
         "  STORE_KEYS.forEach(k=>LS.del(k));\n  location.reload();",
         "  STORE_KEYS.forEach(k=>LS.del(k));\n"
         "  try{ localStorage.removeItem('sp_outbox'); }catch(e){}\n"
         "  call('/progress', {method:'DELETE'})\n"
         "    .catch(function(){})\n"
         "    .then(function(){ location.reload(); });", 1),

        # 9. LS 换成写穿版
        ("写穿 LS", """const LS = {
  get(k, d){ try{ const v = localStorage.getItem('sp_'+k); return v==null ? d : JSON.parse(v); }catch(e){ return d; } },
  set(k, v){ try{ localStorage.setItem('sp_'+k, JSON.stringify(v)); }catch(e){} },
  del(k){ try{ localStorage.removeItem('sp_'+k); }catch(e){} }
};""", NEW_LS, 1),
    ]

    for desc, old, new, want in edits:
        got = html.count(old)
        if got != want:
            sys.exit(f"[{desc}] 期望 {want} 处，实际 {got} 处 — 中止")
        html = html.replace(old, new)
        print(f"  ok  {desc}")

    # 10. 摘掉数据数组，改为从 window.__DATA 注入；App 整体包进 __runApp()
    html = data_re.sub(
        "/* ---------- 内容由 /api/content 注入 ---------- */\n"
        "const schedule = window.__DATA.schedule;\n"
        "const checklistData = window.__DATA.checklistData;\n"
        "const notesData = window.__DATA.notesData;\n"
        "const quizData = window.__DATA.quizData;\n"
        "const zhentiData = window.__DATA.zhentiData;\n"
        "const ALL_Q = quizData.concat(zhentiData);",
        html, count=1)
    print("  ok  数据段改为 API 注入")

    marker = "<script>\n/* ---------- 内容由 /api/content 注入 ---------- */"
    assert html.count(marker) == 1
    html = html.replace(
        marker,
        "<script>\n" + shell +
        "\n/* ==================== 以下为原有 App，数据与进度由壳层注入 ==================== */\n"
        "function __runApp(){\n"
        "/* ---------- 内容由 /api/content 注入 ---------- */", 1)

    tail = "if(_h) activateTab(_h);\n\n</script>"
    assert html.count(tail) == 1, "找不到 App 尾部"
    html = html.replace(tail, "if(_h) activateTab(_h);\n}\n\n</script>", 1)
    print("  ok  App 包进 __runApp()")

    # ---- 兜底校验：内容不能还留在页面里 ----
    for probe in ("source:'基础题'", "source:'2024·金融'", "opts:['"):
        if probe in html:
            sys.exit(f"数据没摘干净，页面里还有 {probe}")
    for need in ("function __runApp(){", "boot();", "window.__DATA.quizData",
                 'id="gate"', 'id="appRoot"', 'id="acctBar"', "Q.push({qkey"):
        if need not in html:
            sys.exit(f"缺少 {need}")
    if html.count("<script>") != 1 or html.count("</script>") != 1:
        sys.exit("script 块数量异常")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"\n{n0/1024:.0f} KB → {len(html)/1024:.0f} KB   "
          f"（摘掉 {(n0-len(html))/1024:.0f} KB 内容）")
    print(f"→ {OUT}")


main()
