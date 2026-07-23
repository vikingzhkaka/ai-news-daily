#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 趋势雷达 · 每日简报 — 独立生成器（GitHub Actions / macOS launchd 通用）

设计目标：彻底脱离 WorkBuddy 运行环境，由独立调度器（GitHub Actions cron）触发。
依赖环境变量：
  TAVILY_API_KEY  (必填) Tavily 搜索 API key —— 替代 WorkBuddy 的 WebSearch
  LLM_API_KEY     (必填) OpenAI 兼容 LLM key（DeepSeek / OpenAI / Moonshot 等）
  LLM_BASE_URL    (可选) 默认 https://api.deepseek.com/v1
  LLM_MODEL       (可选) 默认 deepseek-chat

流程：Tavily 检索 6 个维度 → 汇总为上下文 → LLM 生成结构化 JSON
      → 本脚本用固定模板（CSS 常量）渲染成 index.html
失败策略：任意环节异常 → 打印 GEN_FAIL 并 exit(1)，绝不写/覆盖 index.html，避免推送坏文件。

状态文件 state.json（与 index.html 同目录、随仓库提交）：用于"漏跑超过 14h 补生成横幅"。
"""
import os, sys, json, datetime, html, re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../ai-news-daily/.. = 仓库根
OUT_DIR = os.path.join(REPO_ROOT, "ai-news-daily")
OUT_HTML = os.path.join(OUT_DIR, "index.html")
STATE_FILE = os.path.join(OUT_DIR, "state.json")

TAVILY_KEY = os.environ.get("TAVILY_API_KEY")
LLM_KEY = os.environ.get("LLM_API_KEY")
LLM_BASE = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
CATCHUP_HOURS = 14

# 检索维度与查询（每维度多组关键词，覆盖中英文权威来源）
SEARCH_QUERIES = {
    "A":  ["AI native enterprise platform agent 2026", "AI原生 企业 智能体 平台 2026"],
    "B":  ["AI transformation enterprise ROI 2026 report", "AI转型 企业 成效 调研 2026"],
    "C":  ["how employees use AI effectively productivity 2026", "员工 个人 利用AI 提效 botsitting 2026"],
    "E":  ["trending AI agent skills GitHub 2026", "迅速走红 AI skills 新趋势 实践 2026"],
    "V1": ["AI SaaS trends 2026 agentic MCP pricing", "AI SaaS 趋势 智能体 定价 2026"],
    "V2": ["财税 AI 大模型 智能财税 应用 2026", "tax AI agent vertical model compliance 2026"],
}

DIM_META = {
    "A":  ("tag-native",  "AI 原生（AI-Native）"),
    "B":  ("tag-trans",   "AI 转型（AI Transformation）"),
    "C":  ("tag-emp",     "企业员工个人如何更有效地利用 AI"),
    "D":  ("tag-counter", "反直觉思考（Counterintuitive）"),
    "E":  ("tag-trend",   "新趋势 · 新应用 · 迅速走红的 skills 与实践"),
    "F":  ("tag-imp",     "对普通行业与普通人的启发与警示"),
}

CSS = """  :root{
    --bg:#f6f7f9; --card:#ffffff; --ink:#1f2329; --sub:#6b7280;
    --line:#e5e7eb; --accent:#2563eb;
    --a-native:#7c3aed; --a-trans:#059669; --a-emp:#d97706;
    --a-counter:#dc2626; --a-trend:#0d9488; --a-imp:#4f46e5;
    --warn:#b45309; --warnbg:#fef3c7;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",Segoe UI,sans-serif;
    background:var(--bg);color:var(--ink);line-height:1.6;padding:28px 16px}
  .wrap{max-width:1000px;margin:0 auto}
  header{border-bottom:2px solid var(--line);padding-bottom:16px;margin-bottom:22px}
  h1{font-size:24px;font-weight:700;letter-spacing:.5px}
  .meta{color:var(--sub);font-size:13px;margin-top:6px}
  .catchup{background:var(--warnbg);border:1px solid #fcd34d;color:var(--warn);
    border-radius:10px;padding:12px 16px;font-size:14px;font-weight:600;margin-bottom:20px}
  .takeaways{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);
    border-radius:10px;padding:16px 20px;margin-bottom:26px}
  .takeaways h2{font-size:15px;color:var(--accent);margin-bottom:10px}
  .takeaways ul{margin-left:18px;font-size:14px}
  .takeaways li{margin:6px 0}
  section{margin-bottom:30px}
  .sec-head{display:flex;align-items:center;gap:10px;margin-bottom:14px}
  .sec-tag{font-size:12px;font-weight:700;color:#fff;padding:3px 10px;border-radius:20px}
  .tag-native{background:var(--a-native)} .tag-trans{background:var(--a-trans)}
  .tag-emp{background:var(--a-emp)} .tag-counter{background:var(--a-counter)}
  .tag-trend{background:var(--a-trend)} .tag-imp{background:var(--a-imp)}
  .tag-vert{background:#475569} .tag-saas{background:#0891b2} .tag-fin{background:#be123c}
  .sec-head h2{font-size:19px}
  .sub-head{display:flex;align-items:center;gap:8px;margin:18px 0 12px}
  .sub-head h3{font-size:16px;color:var(--ink)}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  @media(max-width:680px){.grid{grid-template-columns:1fr}}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;transition:box-shadow .15s}
  .card:hover{box-shadow:0 4px 14px rgba(0,0,0,.06)}
  .card h3{font-size:15px;font-weight:600;margin-bottom:6px;line-height:1.4}
  .card h3 a{color:var(--accent);text-decoration:none}
  .card h3 a:hover{text-decoration:underline}
  .src{display:inline-block;font-size:11px;color:var(--sub);background:var(--bg);
    border:1px solid var(--line);border-radius:6px;padding:1px 8px;margin-bottom:8px}
  .card p{font-size:13.5px;color:#374151}
  .pill{display:inline-block;font-size:11px;font-weight:700;padding:1px 8px;border-radius:6px;margin-bottom:8px}
  .pill-ins{background:#eef2ff;color:var(--a-imp)}
  .pill-war{background:#fef2f2;color:var(--a-counter)}
  footer{color:var(--sub);font-size:12px;border-top:1px solid var(--line);padding-top:14px;margin-top:10px}"""


def log(*a):
    print("[gen]", *a, flush=True)


def fail(msg):
    print("GEN_FAIL:", msg, flush=True)
    sys.exit(1)


def md_bold(s: str) -> str:
    """转义 HTML 特殊字符，并把 **x** 转成 <b>x</b>。"""
    s = html.escape(s or "", quote=True)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return s


def safe_url(u: str) -> str:
    u = (u or "").strip()
    if re.match(r"^https?://", u):
        return html.escape(u, quote=True)
    return "#"


# ---------------- 搜索 ----------------
def tavily_search(q: str, max_results: int = 4):
    import requests
    r = requests.post("https://api.tavily.com/search",
        json={"api_key": TAVILY_KEY, "query": q, "max_results": max_results,
              "search_depth": "advanced", "topic": "general"},
        timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])


def collect_search_context():
    ctx = {}
    for dim, queries in SEARCH_QUERIES.items():
        blocks = []
        for q in queries:
            try:
                res = tavily_search(q)
            except Exception as e:
                log(f"search {dim}/{q} error: {e}")
                res = []
            for it in res[:4]:
                blocks.append(f"[{dim}·{q}]\n标题: {it.get('title','')}\n链接: {it.get('url','')}\n摘要: {it.get('content','')[:400]}")
        ctx[dim] = "\n\n".join(blocks) if blocks else f"[{dim}] 无检索结果"
    return ctx


# ---------------- LLM ----------------
def call_llm(system_prompt: str, user_prompt: str) -> str:
    import requests
    r = requests.post(f"{LLM_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
        json={"model": LLM_MODEL, "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}],
            "response_format": {"type": "json_object"}, "temperature": 0.7},
        timeout=150)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def parse_json_content(content: str):
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
    return json.loads(content)


# ---------------- 渲染 ----------------
def render_card_linked(item):
    url = safe_url(item.get("url"))
    title = md_bold(item.get("title", ""))
    src = md_bold(item.get("src", ""))
    point = md_bold(item.get("point", ""))
    return f"""      <div class="card">
        <h3><a href="{url}" target="_blank">{title}</a></h3>
        <span class="src">{src}</span>
        <p>{point}</p>
      </div>"""


def render_section(dim, items):
    tag_cls, title = DIM_META[dim]
    cards = "\n".join(render_card_linked(it) for it in items)
    return f"""  <section>
    <div class="sec-head"><span class="sec-tag {tag_cls}">{dim}</span><h2>{title}</h2></div>
    <div class="grid">
{cards}
    </div>
  </section>"""


def render_d_section(items):
    cards = []
    for it in items:
        title = md_bold(it.get("title", ""))
        src = md_bold(it.get("src", ""))
        exp = md_bold(it.get("expectation", ""))
        fin = md_bold(it.get("finding", ""))
        ins = md_bold(it.get("insight", ""))
        cards.append(f"""      <div class="card">
        <h3>{title}</h3>
        <span class="src">{src}</span>
        <p><b>常识预期：</b>{exp}<br><b>实际发现：</b>{fin}<br><b>启示：</b>{ins}</p>
      </div>""")
    cards = "\n".join(cards)
    return f"""  <section>
    <div class="sec-head"><span class="sec-tag tag-counter">D</span><h2>{DIM_META['D'][1]}</h2></div>
    <div class="grid">
{cards}
    </div>
  </section>"""


def render_f_section(f):
    cards = []
    for it in f.get("inspire", []):
        target = md_bold(it.get("target", "普通行业"))
        title = md_bold(it.get("title", ""))
        point = md_bold(it.get("point", ""))
        cards.append(f"""      <div class="card">
        <span class="pill pill-ins">启发 · {target}</span>
        <h3>{title}</h3>
        <p>{point}</p>
      </div>""")
    for it in f.get("warn", []):
        target = md_bold(it.get("target", "普通人"))
        title = md_bold(it.get("title", ""))
        point = md_bold(it.get("point", ""))
        cards.append(f"""      <div class="card">
        <span class="pill pill-war">警示 · {target}</span>
        <h3>{title}</h3>
        <p>{point}</p>
      </div>""")
    cards = "\n".join(cards)
    return f"""  <section>
    <div class="sec-head"><span class="sec-tag tag-imp">F</span><h2>{DIM_META['F'][1]}</h2></div>
    <div class="grid">
{cards}
    </div>
  </section>"""


def render_v_section(v1, v2):
    def sub(tag_cls, tag, title, items):
        cards = "\n".join(render_card_linked(it) for it in items)
        return f"""    <div class="sub-head"><span class="sec-tag {tag_cls}">{tag}</span><h3>{title}</h3></div>
    <div class="grid">
{cards}
    </div>"""
    s1 = sub("tag-saas", "V1", "SaaS 领域", v1)
    s2 = sub("tag-fin", "V2", "财税垂直领域", v2)
    return f"""  <section>
    <div class="sec-head"><span class="sec-tag tag-vert">V</span><h2>垂直领域 AI 动向（SaaS · 财税）</h2></div>

{s1}

{s2}
  </section>"""


def build_html(data, banner):
    today = datetime.date.today().strftime("%Y-%m-%d")
    date = data.get("date") or today
    highlights = data.get("highlights", [])
    hl_li = "\n".join(f"      <li>{md_bold(h)}</li>" for h in highlights) or "      <li>暂无要点</li>"
    sec = data.get("sections", {})

    banner_html = f'  <div class="catchup">{md_bold(banner)}</div>\n' if banner else ""

    meta = (f"生成日期：{date} ｜ 六维：AI 原生 · AI 转型 · 员工提效 · 反直觉 · "
            f"新趋势/走红skills · 启发与警示 ｜ ＋ 垂直领域专区（SaaS · 财税）"
            f"｜ 来源：Tavily 检索 + LLM 汇总")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 趋势雷达 · 每日简报</title>
<style>
{CSS}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>AI 趋势雷达 · 每日简报</h1>
    <div class="meta">{meta}</div>
  </header>

{banner_html}
  <div class="takeaways">
    <h2>今日要点 · 趋势信号</h2>
    <ul>
{hl_li}
    </ul>
  </div>

{render_section('A', sec.get('A', []))}

{render_section('B', sec.get('B', []))}

{render_section('C', sec.get('C', []))}

{render_d_section(sec.get('D', []))}

{render_section('E', sec.get('E', []))}

{render_f_section(sec.get('F', {{}}))}

{render_v_section(sec.get('V1', []), sec.get('V2', []))}

  <footer>
    本简报由 GitHub Actions 每日自动抓取（Tavily 检索）与生成（LLM 汇总）并推送 GitHub Pages，独立于 WorkBuddy 运行状态。链接均指向原始来源。
  </footer>
</div>
</body>
</html>"""


# ---------------- 状态 / 补生成 ----------------
def read_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except Exception:
        return {}


def write_state(status, err=""):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    json.dump({"last_run": now, "last_status": status, "last_error": err},
              open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def compute_banner():
    st = read_state()
    lr = st.get("last_run")
    if not lr:
        return None
    try:
        last = datetime.datetime.fromisoformat(lr)
        if last.tzinfo is None:
            last = last.replace(tzinfo=datetime.timezone.utc)
        gap = (datetime.datetime.now(datetime.timezone.utc) - last).total_seconds() / 3600
    except Exception:
        return None
    if gap > CATCHUP_HOURS:
        return (f"⚠️ 补生成提醒：上次计划生成于 {lr[:16].replace('T',' ')}Z，"
                f"可能因调度中断未执行，本次已为你补生成最新资讯。")
    return None


# ---------------- 主流程 ----------------
def main():
    if not TAVILY_KEY or not LLM_KEY:
        fail("缺少环境变量 TAVILY_API_KEY 或 LLM_API_KEY")

    log("collecting search context (Tavily) ...")
    ctx = collect_search_context()

    ctx_text = "\n\n===== 检索上下文 =====\n\n".join(
        f"# 维度 {k }\n{v}" for k, v in ctx.items())

    system = (
        "你是一位资深 AI 行业分析师，负责产出中文《AI 趋势雷达·每日简报》。"
        "必须严格基于下方『检索上下文』中的真实来源（标题/链接/摘要）撰写，禁止编造来源或数据；"
        "若上下文不足，可基于常识谨慎推断，但不得虚构 URL。"
        "输出纯 JSON（response_format=json_object），结构如下：\n"
        "{\n"
        '  "date": "生成日期 YYYY-MM-DD",\n'
        '  "highlights": ["4 条趋势信号，每条含一个加粗关键词"],\n'
        '  "sections": {\n'
        '    "A": [{"title","url","src","point"} 4-6 条, AI 原生],\n'
        '    "B": [同结构 4-6 条, AI 转型],\n'
        '    "C": [同结构 4-6 条, 员工个人提效],\n'
        '    "E": [同结构 4-6 条, 新趋势/走红skills],\n'
        '    "V1": [同结构 4-5 条, SaaS 领域],\n'
        '    "V2": [同结构 4-5 条, 财税垂直领域],\n'
        '    "D": [{"title","src","expectation","finding","insight"} 3-5 条, 反直觉],\n'
        '    "F": {"inspire":[{"target","title","point"} 3 条], "warn":[{"target","title","point"} 3 条]}\n'
        "  }\n"
        "}\n"
        "要求：A/B/C/E/V1/V2 的 url 必须来自上下文真实链接，src 写来源名+年份；"
        "point 为 1-2 句要点，可用 **重点** 强调。D 三栏：常识预期→实际发现（附数据/来源）→启示。"
        "F：inspire 给普通行业/普通人落地建议，warn 给风险警示；target 取值如『普通行业』『普通人』。"
    )

    user = (
        "以下是今日各维度检索到的真实资讯，请据此生成简报 JSON：\n\n" + ctx_text + "\n\n"
        "请现在输出完整 JSON。"
    )

    log("calling LLM ...")
    try:
        content = call_llm(system, user)
        data = parse_json_content(content)
    except Exception as e:
        # 重试一次（模型偶尔返回非 JSON）
        try:
            log(f"first parse failed ({e}), retry once ...")
            content = call_llm(system, user + "\n\n注意：请只输出可被 json.loads 解析的纯 JSON，不要代码块标记。")
            data = parse_json_content(content)
        except Exception as e2:
            fail(f"LLM/解析失败: {e2}")

    sec = data.get("sections", {})
    if not sec.get("A") or not sec.get("V1") or not sec.get("V2"):
        fail("LLM 返回缺少必需维度（A/V1/V2）")

    banner = compute_banner()
    html_out = build_html(data, banner)

    # 写 index.html（仅成功路径才到这里）
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_out)
    write_state("ok")
    log(f"OK -> {OUT_HTML} ({len(html_out)} bytes)" + (" [含补生成横幅]" if banner else ""))


if __name__ == "__main__":
    main()
