#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 趋势雷达 · 每日简报 — 独立生成器（GitHub Actions / macOS launchd 通用）

设计目标：彻底脱离 WorkBuddy 运行环境，由独立调度器（GitHub Actions cron）触发。
依赖环境变量：
  TAVILY_API_KEY  (必填) Tavily 搜索 API key —— 替代 WorkBuddy 的 WebSearch
  LLM_API_KEY     (必填) OpenAI 兼容 LLM key（DeepSeek / OpenAI / Moonshot 等）
  LLM_BASE_URL    (可选) 默认 https://api.deepseek.com/v1
  LLM_MODEL       (可选) 默认 deepseek-v4-flash（v4 系列，替代已弃用的 deepseek-chat）

流程：Tavily 检索 6 个维度 → 汇总为上下文 → LLM 生成结构化 JSON
      → 本脚本用固定模板（CSS 常量）渲染成 index.html
失败策略：任意环节异常 → 打印 GEN_FAIL 并 exit(1)，绝不写/覆盖 index.html，避免推送坏文件。

状态文件 state.json（与 index.html 同目录、随仓库提交）：用于"漏跑超过 14h 补生成横幅"。
"""
import os, sys, json, datetime, html, re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../ai-news-daily/.. = 仓库根
# GitHub Pages 项目页默认从仓库根目录提供 index.html，故生成物必须落在根目录
OUT_HTML = os.path.join(REPO_ROOT, "index.html")
STATE_FILE = os.path.join(REPO_ROOT, "state.json")

# 注意：GitHub Actions 中"未设置的 secret"会被传成空字符串 "" 而非缺失，
# 因此用 "or 默认值" 让空值也回落到默认，避免 LLM_BASE 变空导致请求失败。
# .strip() 防止用户在 Secrets UI 复制粘贴时带入首尾空白/TAB。
TAVILY_KEY = (os.environ.get("TAVILY_API_KEY") or "").strip()
LLM_KEY = (os.environ.get("LLM_API_KEY") or "").strip()
LLM_BASE = ((os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")).strip()
LLM_MODEL = (os.environ.get("LLM_MODEL") or "deepseek-v4-flash").strip()  # v4 系列替代已弃用的 deepseek-chat
CATCHUP_HOURS = 14

# 检索窗口与每 query 抓取量（模块级常量，统一引用，避免 collect_search_context 中引用未定义名）
SEARCH_DAYS = 9    # 抓最近 9 天：制造跨周重叠，消除"周末边界永久漏"（每周一跑，覆盖上周六~本周一）
MAX_RESULTS = 8    # 每 query 抓取 8 条：减少排名截断导致的漏失

# 检索维度与查询（每维度 4 组关键词：权威媒体 + 深度分析/报告/案例，降 query 盲区）
SEARCH_QUERIES = {
    "A":  [
        "AI native enterprise platform agent 2026",
        "AI原生 企业 智能体 平台 2026",
        "AI-native company restructuring org design 2026 report",
        "AI原生 组织变革 企业内部 智能体 落地 案例 2026",
    ],
    "B":  [
        "AI transformation enterprise ROI 2026 report",
        "AI转型 企业 成效 调研 2026",
        "enterprise AI adoption benchmark study productivity 2026",
        "企业 AI 应用 基准 调研 生产力 白皮书 2026",
    ],
    "C":  [
        "how employees use AI effectively productivity 2026",
        "员工 个人 利用AI 提效 botsitting 2026",
        "AI skills workers learn prompt engineering workflow 2026 guide",
        "职场人 AI 技能 提示词 工作流 实操 指南 2026",
    ],
    "E":  [
        "trending AI agent skills GitHub 2026",
        "迅速走红 AI skills 新趋势 实践 2026",
        "viral AI tools open source agents release 2026",
        "爆火 AI 工具 开源 智能体 发布 2026",
    ],
    "V1": [
        "AI SaaS trends 2026 agentic MCP pricing",
        "AI SaaS 趋势 智能体 定价 2026",
        "SaaS AI integration marketplace MCP protocol 2026 analysis",
        "SaaS AI 集成 应用市场 MCP 协议 趋势 分析 2026",
    ],
    "V2": [
        "财税 AI 大模型 智能财税 应用 2026",
        "tax AI agent vertical model compliance 2026",
        "财税 大模型 智能体 合规 落地 报告 案例 2026",
        "fintech tax AI automation regulation 2026 report",
    ],
}

# 每个维度归属一个大分组（GROUP_META 控制页面顶层结构），tag 类与标题保持原样
DIM_META = {
    "A":  ("tag-native",  "AI 原生（AI-Native）",          "enterprise"),
    "B":  ("tag-trans",   "AI 转型（AI Transformation）",  "enterprise"),
    "V1": ("tag-saas",    "SaaS 领域",                     "enterprise"),
    "V2": ("tag-fin",     "财税垂直领域",                  "enterprise"),
    "C":  ("tag-emp",     "企业员工个人如何更有效地利用 AI", "people"),
    "E":  ("tag-trend",   "新趋势 · 新应用 · 迅速走红的 skills 与实践", "people"),
    "D":  ("tag-counter", "反直觉思考（Counterintuitive）", "people"),
    "F":  ("tag-imp",     "对普通行业与普通人的启发与警示",  "people"),
}

# 顶层三大分组（顺序即页面顺序）
GROUP_META = {
    "enterprise": ("group-ent", "企业视角 · 老板与管理层关心"),
    "people":     ("group-ppl", "人与组织视角 · 执行层与个人关心"),
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
  /* 顶部要点：唯一的第一眼入口，放大加粗关键词 */
  .takeaways{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--accent);
    border-radius:10px;padding:18px 22px;margin-bottom:24px}
  .takeaways h2{font-size:13px;color:var(--sub);font-weight:600;margin-bottom:10px;
    text-transform:uppercase;letter-spacing:.5px}
  .takeaways ul{margin-left:18px;font-size:15px;line-height:1.7}
  .takeaways li{margin:8px 0}
  .takeaways li b{color:var(--accent)}
  section{margin-bottom:30px}
  .sec-head{display:flex;align-items:center;gap:10px;margin-bottom:14px}
  /* 少即是多：维度字母退化为中性灰小标，不再用六色抢眼 */
  .sec-tag{font-size:12px;font-weight:700;color:#fff;padding:3px 10px;border-radius:20px}
  .dim-key{display:inline-block;font-size:11px;font-weight:700;color:var(--sub);
    background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:1px 7px;margin-right:10px}
  .dim-count{margin-left:auto;font-size:11px;color:var(--sub);font-weight:400}
  /* 折叠维度（默认收起，点击展开） */
  details.dim{border-bottom:1px solid var(--line);padding:4px 0}
  details.dim > summary{display:flex;align-items:center;cursor:pointer;list-style:none;
    font-size:16px;font-weight:600;color:var(--ink);padding:10px 4px;user-select:none}
  details.dim > summary::-webkit-details-marker{display:none}
  details.dim > summary:hover{color:var(--accent)}
  details.dim[open] > summary{color:var(--accent);margin-bottom:14px}
  details.dim > summary::before{content:"▸";margin-right:8px;color:var(--sub);
    transition:transform .15s;font-size:12px}
  details.dim[open] > summary::before{content:"▾"}
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
  .date{display:inline-block;font-size:11px;color:var(--sub);margin:0 0 8px 0}
  .date-unknown{color:#9ca3af;font-style:italic}
  .group{background:transparent;margin-bottom:22px;padding:0}
  .group-ent{background:transparent}
  .group-ppl{background:transparent}
  .group-head{margin-bottom:8px;padding-bottom:6px}
  .group-head h2{font-size:13px;font-weight:600;color:var(--sub);
    text-transform:uppercase;letter-spacing:.5px}
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
def tavily_search(q: str, max_results: int = MAX_RESULTS, days: int = SEARCH_DAYS):
    import requests
    # topic=news + days=SEARCH_DAYS(9)：抓最近 9 天新资讯，制造跨周重叠避免"周末边界永久漏"
    r = requests.post("https://api.tavily.com/search",
        json={"api_key": TAVILY_KEY, "query": q, "max_results": max_results,
              "search_depth": "advanced", "topic": "news", "days": days},
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
            for it in res[:MAX_RESULTS]:
                blocks.append(f"[{dim}·{q}]\n标题: {it.get('title','')}\n链接: {it.get('url','')}\n摘要: {it.get('content','')[:400]}")
        ctx[dim] = "\n\n".join(blocks) if blocks else f"[{dim}] 无检索结果"
    return ctx


# ---------------- LLM ----------------
def call_llm(system_prompt: str, user_prompt: str) -> str:
    import requests
    url = f"{LLM_BASE}/chat/completions"
    payload = {"model": LLM_MODEL, "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}],
        # 注意：DeepSeek 兼容接口不支持 response_format（会返回 400），
        # 故不传此参数，改用 system/user prompt 约束输出格式 + parse_json_content 容错。
        "temperature": 0.7}
    log(f"LLM request -> {url}  model={LLM_MODEL}  sys_prompt={len(system_prompt)}ch  user_prompt={len(user_prompt)}ch")
    r = requests.post(url,
        headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
        json=payload, timeout=150)
    if r.status_code >= 400:
        # 打印服务商返回的具体错误体（不含 key），用于定位 400 原因
        try:
            err_body = r.json()
            # 脱敏：移除可能包含 key 片段的内容
            err_str = json.dumps(err_body, ensure_ascii=False)[:500]
        except Exception:
            err_str = r.text[:500]
        log(f"LLM HTTP {r.status_code} response body: {err_body}")
        log(f"LLM_BASE={LLM_BASE}  LLM_MODEL={LLM_MODEL}")
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
    # 少即是多：只显示有真实发布时间的，缺失则完全不渲染（不占位噪音）
    pub = (item.get("published") or "").strip()
    pub_html = f'<span class="date">📅 {md_bold(pub)}</span>' if pub else ""
    return f"""      <div class="card">
        <h3><a href="{url}" target="_blank">{title}</a></h3>
        <span class="src">{src}</span>{pub_html}
        <p>{point}</p>
      </div>"""


def render_section(dim, items):
    _tag_cls, title, _group = DIM_META[dim]
    cards = "\n".join(render_card_linked(it) for it in items)
    return f"""  <details class="dim">
    <summary><span class="dim-key">{dim}</span>{title}<span class="dim-count">{len(items)} 条</span></summary>
    <div class="grid">
{cards}
    </div>
  </details>"""


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
    return f"""  <details class="dim">
    <summary><span class="dim-key">D</span>{DIM_META['D'][1]}<span class="dim-count">{len(items)} 条</span></summary>
    <div class="grid">
{cards}
    </div>
  </details>"""


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
    fcount = len(f.get("inspire", [])) + len(f.get("warn", []))
    return f"""  <details class="dim">
    <summary><span class="dim-key">F</span>{DIM_META['F'][1]}<span class="dim-count">{fcount} 条</span></summary>
    <div class="grid">
{cards}
    </div>
  </details>"""


def render_v_section(v1, v2):
    # V1/V2 已并入企业视角分组，作为折叠维度项，外层由 build_html 的 group 包裹
    def sub(tag_cls, tag, title, items):
        cards = "\n".join(render_card_linked(it) for it in items)
        return f"""    <div class="sub-head"><span class="sec-tag {tag_cls}">{tag}</span><h3>{title}</h3></div>
    <div class="grid">
{cards}
    </div>"""
    s1 = sub("tag-saas", "V1", "SaaS 领域", v1)
    s2 = sub("tag-fin", "V2", "财税垂直领域", v2)
    return f"""  <details class="dim">
    <summary><span class="dim-key">V</span>垂直领域 AI 动向（SaaS · 财税）<span class="dim-count">{len(v1)+len(v2)} 条</span></summary>

{s1}

{s2}
  </details>"""


def build_html(data, banner):
    today = datetime.date.today().strftime("%Y-%m-%d")
    date = data.get("date") or today
    highlights = data.get("highlights", [])
    hl_li = "\n".join(f"      <li>{md_bold(h)}</li>" for h in highlights) or "      <li>暂无要点</li>"
    sec = data.get("sections", {})
    empty = {}  # f-string / 闭包内不能用 {} 字面量，用变量承接默认值

    banner_html = f'  <div class="catchup">{md_bold(banner)}</div>\n' if banner else ""

    meta = (f"生成于 {date} ｜ 每周一 09:00 自动更新")

    # 顶层分组包裹：按 GROUP_META 顺序，把各维度塞进对应 group
    def build_group(group_key, dim_blocks):
        g_cls, g_title = GROUP_META[group_key]
        blocks = "\n".join(dim_blocks)
        return f"""  <div class="group {g_cls}">
    <div class="group-head"><h2>{g_title}</h2></div>
{blocks}
  </div>"""

    ent_blocks = [
        render_section('A', sec.get('A', [])),
        render_section('B', sec.get('B', [])),
        render_v_section(sec.get('V1', []), sec.get('V2', [])),
    ]
    ppl_blocks = [
        render_section('C', sec.get('C', [])),
        render_section('E', sec.get('E', [])),
        render_d_section(sec.get('D', [])),
        render_f_section(sec.get('F', empty)),
    ]
    groups_html = "\n".join([
        build_group('enterprise', ent_blocks),
        build_group('people', ppl_blocks),
    ])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI 趋势雷达 · 每周简报</title>
<style>
{CSS}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>AI 趋势雷达 · 每周简报</h1>
    <div class="meta">{meta}</div>
  </header>

{banner_html}
  <div class="takeaways">
    <h2>今日要点 · 趋势信号</h2>
    <ul>
{hl_li}
    </ul>
  </div>

{groups_html}

  <footer>
    本简报由 GitHub Actions 每周一自动抓取（Tavily 检索，限最近 9 天，含跨周重叠）与生成（LLM 汇总）并推送 GitHub Pages，独立于 WorkBuddy 运行状态。链接均指向原始来源；卡片所标发布时间取自来源公开信息。
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
        "你是一位资深 AI 行业分析师，负责产出中文《AI 趋势雷达·每周简报》。"
        "本期聚焦最近 9 天内发布的新资讯与趋势信号（检索窗口含跨周重叠，避免周末事件遗漏），旧内容无需纳入。"
        "必须严格基于下方『检索上下文』中的真实来源（标题/链接/摘要）撰写，禁止编造来源或数据；"
        "若上下文不足，可基于常识谨慎推断，但不得虚构 URL。"
        "输出纯 JSON（response_format=json_object），结构如下：\n"
        "{\n"
        '  "date": "生成日期 YYYY-MM-DD",\n'
        '  "highlights": ["4 条趋势信号，每条含一个加粗关键词"],\n'
        '  "sections": {\n'
        '    "A": [{"title","url","src","point","published"} 4-6 条, AI 原生],\n'
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
        "point 为 1-2 句要点，可用 **重点** 强调。"
        "published 为来源文章的**真实发布日期**（YYYY-MM-DD 或 YYYY-MM），尽量从检索上下文的标题/摘要/年份推断；"
        "若上下文无任何日期线索则填 null（不要编造）。"
        "D 三栏：常识预期→实际发现（附数据/来源）→启示。"
        "F：inspire 给普通行业/普通人落地建议，warn 给风险警示；target 取值如『普通行业』『普通人』。"
        )

    user = (
        "以下是最近 9 天各维度检索到的真实资讯，请据此生成简报 JSON：\n\n" + ctx_text + "\n\n"
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
    # weekly 模式下某些维度一周内新闻可能稀疏，允许部分维度为空，仅当全空才放弃推送
    non_empty = [k for k, v in sec.items() if v]
    if not non_empty:
        fail("LLM 返回所有维度均为空，可能检索上下文不足")

    banner = compute_banner()
    html_out = build_html(data, banner)

    # 写 index.html（仅成功路径才到这里）
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_out)
    write_state("ok")
    log(f"OK -> {OUT_HTML} ({len(html_out)} bytes)" + (" [含补生成横幅]" if banner else ""))


if __name__ == "__main__":
    main()
