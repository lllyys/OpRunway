#!/usr/bin/env python3
"""昇腾社区 CANN 页面巡检：取链、探测、按七条规则判定，写 last-run.json。

用法：python3 page_inspect.py --out <输出目录>
产物：<输出目录>/cann-page-inspect/.state/last-run.json 与 run.log
报告由 render.py 从 last-run.json 渲染，本脚本不碰 HTML。
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

# ── 可调常量：改这里，不要改下面的逻辑 ────────────────────────────────
# 站点顶部导航下的全部页签。共 21 个 /cann/* 入口，
# 逐个列出而不是运行时从导航抓——列表是可评审的，抓来的不是。
# 站点改版新增页签时，NAV_DRIFT 检测会在 run.log 里点名，不会静默漏测。
PAGES = {
    "主页": "https://www.hiascend.com/cann",
    "下载": "https://www.hiascend.com/cann/download",
    "开发": "https://www.hiascend.com/cann/develop",
    "算子库": "https://www.hiascend.com/cann/aol",
    "通信库": "https://www.hiascend.com/cann/hccl",
    "领域加速库": "https://www.hiascend.com/cann/aal",
    "图引擎": "https://www.hiascend.com/cann/graph-engine",
    "算子编程": "https://www.hiascend.com/cann/ascend-c",
    "毕昇编译器": "https://www.hiascend.com/cann/bisheng",
    "运行时": "https://www.hiascend.com/cann/runtime",
    "驱动": "https://www.hiascend.com/cann/driver",
    "文档": "https://www.hiascend.com/cann/document",
    "学习": "https://www.hiascend.com/cann/learn",
    "SIG中心": "https://www.hiascend.com/cann/sig",
    "贡献路径": "https://www.hiascend.com/cann/contrib-guide",
    "社区治理": "https://www.hiascend.com/cann/gov-arch",
    "治理架构": "https://www.hiascend.com/cann/charter",
    "社区章程": "https://www.hiascend.com/cann/charter/detail",
    "贡献看板": "https://www.hiascend.com/cann/contribution-dashboard",
    "线上会议": "https://www.hiascend.com/cann/meeting",
    "开源项目列表": "https://www.hiascend.com/cann/open-map",
}
SITE = "www.hiascend.com"
# A2 生效范围：这些站内路径是客户端渲染的，状态码与 title 都不可信，必须渲染
CSR_PREFIXES = ("/productbulletins/detail/", "/cann/")
SOFT_404_MARKERS = ("抱歉，您访问的页面不存在", "页面找不到或无权限", "页面不存在")
# 代码仓（gitee/gitcode）访问不存在的文件/目录时，仓库页本身 200 能开，但文件查看器
# 对不存在的路径兜底显示「加载失败，请刷新重试」——这是代码仓的软 404，不是反爬验证码。
REPO_MISSING_MARKERS = ("请刷新重试",)
SHELL_LINK_MAX = 45           # 单页 <a> 数不超过它就是只有页头页脚的外壳
LINKS_STABLE_TIMEOUT_MS = 30000   # 取链：等链接数不再增长的上限
SUBTAB_SETTLE_MS = 12000      # 子页签：点开后等新内容加载的上限
PAGE_WORKERS = 3              # 取链并发页数，每个 worker 自带一个 playwright 实例
SHELL_RETRY_WAIT = 20         # 塌陷页重试前的静置秒数，让站点缓过来
RENDER_WORKERS = 4            # CSR 页渲染并发数
HTTP_WORKERS = 16             # HTTP 探测并发数
SETTLE_POLL_MS = 1200         # 渲染等待：轮询间隔
SETTLE_STABLE_N = 3           # 正文长度连续几次不变就认为渲染完成
RETRIES = 3                   # A1：重试次数
BACKOFF = (0, 2, 5)           # A1：每次重试前的等待秒数
BULLETIN_STALE_DAYS = 90      # B3：最新版本公告距今超过此值告警
NEWS_STALE_DAYS = 30          # B3：最新动态新闻距今超过此值告警
LOGIN_MARKERS = ("/login", "/signin", "/passport", "/user/login")
# 这些状态码是「服务器拒绝这个客户端」而非「链接坏了」，交浏览器复核。
# 405 是 gitee 对 /tree/ 路径的反爬应答（浏览器能正常打开），与 403/418/429 同类。
THROTTLE_CODES = (403, 405, 418, 429)
# 会议类域名只验域名可达，不追登录墙后面的内容
MEETING_HOSTS = ("meeting.huaweicloud.com", "etherpad-cann.meeting.osinfra.cn",
                 "meeting.osinfra.cn")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
NAV_TIMEOUT_MS = 45000
SETTLE_TIMEOUT_MS = 20000
HTTP_TIMEOUT = 25
# 子页签：不是 <a>、没有 href、点击不改 URL，但会换掉整块正文。
# 实测 /cann/aol 的四个子库只有默认选中的那个在 DOM 里，不点就漏掉四分之三。
SUBTAB_SEL = "li.o-menu-item"

# ── 递归爬取：从21 个入口 BFS，只递归站内（www.hiascend.com）页面 ─────
# 外链（github / gitee / docs / 会议等）只探测可达性，不入队递归。
# 全站页头页脚（chrome）不入队——那是全站通用导航，不是 CANN 界面里的内容。
MAX_PAGES = 500            # 递归页数上限，触顶即在日志标记（防失控）
SKIP_EXT = (".pdf", ".zip", ".tar.gz", ".tgz", ".png", ".jpg", ".jpeg",
            ".gif", ".svg", ".mp4", ".webm", ".ico")   # 静态文件不当作页面递归
# 递归白名单：只递归 path 以 /cann/ 开头的站内页面。cann 页「连出去」的其余站内页
# （公告详情 /productbulletins/、课程 /developer/courses/、动态新闻 /zh/activities/、
# 下载 /developer/download/、博客 /developer/blog/ 等）一律当尾节点——进「全部地址」
# 表、逐条探测，但不往深处递归。外链（github/gitee/docs.hiascend.com 等）本就不递归。
CRAWL_PREFIX = "/cann/"
CRAWL_CACHE_TTL_HOURS = 6     # 取链缓存有效期（小时），超时重爬。探测永不缓存

# A 组查「点了之后能不能拿到东西」，B 组查「拿到的东西是不是过期或自相矛盾」。
# 编号自带组别，报告按组分块展示。
RULES = [
    ("A1", "链接可用性", "链接打不开",
     "三次重试全部失败：DNS / 连接 / 超时 / 4xx / 5xx"),
    ("A2", "链接可用性", "打开是「页面不存在」",
     "HTTP 返回 200，但渲染后正文是站点的失效页（含「抱歉，您访问的页面不存在」等标记）"),
    ("A3", "链接可用性", "被弹到登录页或首页",
     "终点 URL 落在登录页、站点首页或根路径，没停在原目标"),
    ("A4", "链接可用性", "落地页与链接说的对不上",
     "锚文本或 URL 的关键 token 未出现在目标页标题或正文"),
    ("B1", "内容时效与一致性", "下载版本与公告不一致",
     "下载页「版本选择」的版本号 ≠ 主页最新版本公告的版本号"),
    ("B2", "内容时效与一致性", "会议已结束仍在展示",
     "会议链接 URL 内的日期早于巡检日"),
    ("B3", "内容时效与一致性", "内容长期未更新",
     f"最新版本公告距今 > {BULLETIN_STALE_DAYS} 天，或最新动态新闻距今 > {NEWS_STALE_DAYS} 天"),
]
# 旧编号 → 新编号。baseline.json 用「编号|url」做键，不迁移的话
# 换编号会让所有历史问题在今天全部变成「今日新增」。
RULE_ID_MIGRATION = {"R1": "A1", "R2": "A2", "R3": "A3", "R4": "A4",
                     "R5": "B1", "R6": "B2", "R7": "B3"}

LOG: list[str] = []


def log(msg: str) -> None:
    line = f"{datetime.now():%H:%M:%S} {msg}"
    LOG.append(line)
    print(line, flush=True)


def die(msg: str, out_dir: Path | None = None) -> None:
    log(f"阻塞·{msg}")
    if out_dir:
        (out_dir / "run.log").write_text("\n".join(LOG), encoding="utf-8")
    sys.exit(2)


# ── S1 前置自检 ────────────────────────────────────────────────────
def preflight(out_dir: Path):
    try:
        import httpx  # noqa: F401
    except ImportError:
        die("缺少 httpx，装：pip install httpx", out_dir)
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        die("缺少 playwright，装：pip install playwright（不必下载 chromium，复用本机 Chrome）", out_dir)
    import httpx
    for name, url in PAGES.items():
        try:
            r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=HTTP_TIMEOUT)
            if r.status_code >= 400:
                die(f"入口页 {name} 返回 {r.status_code}，站点不可用，本轮不出报告", out_dir)
        except Exception as e:
            die(f"入口页 {name} 不可达：{type(e).__name__}，本轮不出报告", out_dir)
    log(f"S1 自检通过：httpx / playwright 就绪，{len(PAGES)} 个入口页可达")


# ── S2 取链：渲染三页，抽 <a> 与锚文本、所在区块 ────────────────────
COLLECT_JS = r"""
() => {
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  let section = '';
  while (walker.nextNode()) {
    const el = walker.currentNode;
    if (/^H[1-4]$/.test(el.tagName)) {
      const t = (el.innerText || '').trim().replace(/\s+/g, ' ');
      if (t && t.length < 40) section = t;
      continue;
    }
    if (el.tagName === 'A' && el.getAttribute('href')) {
      out.push({
        href: el.getAttribute('href'),
        text: (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80),
        section: section,
      });
    }
  }
  return out;
}
"""


STATS_JS = r"""
() => {
  const vis = e => !!(e.offsetParent || e.getClientRects().length);
  // 区块：H1-H4 标题去重
  const sections = new Set([...document.querySelectorAll('h1,h2,h3,h4')]
      .map(h => (h.innerText || '').trim()).filter(t => t && t.length < 40));
  // 非链接可点元素：子页签、role=tab、button，且不在 <a> 里
  const clickable = [...document.querySelectorAll('li.o-menu-item,[role="tab"],button')]
      .filter(e => vis(e) && !e.closest('a')).length;
  return {sections: sections.size, clickables: clickable};
}
"""

# 内容信息点：被 B1 / B2 / B3 拿去判定的那些值——版本号、发布时间、发表时间、
# 会议日期。数出来是为了让报告能说清「这一页除了链接还查了几处内容」。
INFO_RES = (re.compile(r"\d+\.\d+\.\d+[A-Za-z0-9.\-]*"),
            re.compile(r"发布时间\s*\d{4}/\d{2}/\d{2}"),
            re.compile(r"发表于\s*\d{4}/\d{2}/\d{2}"),
            re.compile(r"20\d{2}-\d{2}-\d{2}"))


def count_infopoints(text: str, links: list[dict]) -> int:
    hay = text + " " + " ".join(l["text"] + " " + l["href"] for l in links)
    return sum(len(set(r.findall(hay))) for r in INFO_RES)


def _wait_links_stable(pg, floor: int, cap_ms: int, scroll: bool) -> int:
    """等 <a> 数连续 SETTLE_STABLE_N 次不变。floor 是「还没渲染完」的下界。"""
    last, stable, waited = -1, 0, 0
    while waited < cap_ms:
        if scroll:
            pg.evaluate("window.scrollBy(0, window.innerHeight)")
        pg.wait_for_timeout(SETTLE_POLL_MS)
        waited += SETTLE_POLL_MS
        n = pg.evaluate("() => document.querySelectorAll('a[href]').length")
        stable = stable + 1 if n == last else 0
        if stable >= SETTLE_STABLE_N and n > floor:
            return n
        last = n
    return last


def expand_subtabs(pg, name: str, links: list[dict], text_parts: list[str]) -> list[str]:
    """逐个点开子页签，把每个页签下的链接并进来。

    子页签不是 <a>、点击不改 URL，所以只能点。实测 /cann/aol 的四个子库，
    不点就只拿得到默认选中的那一个。
    """
    labels = pg.evaluate(
        f"""() => [...document.querySelectorAll('{SUBTAB_SEL}')]
              .filter(e => e.offsetParent || e.getClientRects().length)
              .map(e => (e.innerText || '').trim().slice(0, 20))""")
    opened = []
    for idx, label in enumerate(labels):
        if not label:
            continue
        before = pg.url
        try:
            pg.evaluate(
                f"""(i) => {{
                      const els = [...document.querySelectorAll('{SUBTAB_SEL}')]
                          .filter(e => e.offsetParent || e.getClientRects().length);
                      if (els[i]) els[i].click();
                    }}""", idx)
        except Exception as e:
            log(f"S2 {name} 子页签「{label}」点击异常：{type(e).__name__}")
            continue
        if pg.url != before:                      # 点成了跳转，退回来继续
            try:
                pg.goto(before, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                _wait_links_stable(pg, SHELL_LINK_MAX, LINKS_STABLE_TIMEOUT_MS, True)
            except Exception:
                pass
            continue
        _wait_links_stable(pg, 0, SUBTAB_SETTLE_MS, False)
        for l in pg.evaluate(COLLECT_JS):
            links.append({**l, "section": l["section"] or label})
        text_parts.append(pg.evaluate(
            "() => document.body.innerText.replace(/\\s+/g,' ')"))
        opened.append(label)
    return opened


def collect_one(name: str, url: str) -> dict:
    """取一个页签：导航 → 等稳定 → 展开子页签 → 汇总链接、正文、统计。

    每次调用自带一个 playwright 实例，可以直接丢进线程池。
    """
    from playwright.sync_api import sync_playwright

    links, text_parts, subtabs, stats = [], [], [], {"sections": 0, "clickables": 0}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": 1600, "height": 1200},
                                  locale="zh-CN", user_agent=UA)
        pg = ctx.new_page()
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            log(f"S2 {name} 首次导航异常，继续等待正文：{type(e).__name__}")
        try:
            pg.wait_for_function(
                f"() => document.querySelectorAll('a[href]').length > {SHELL_LINK_MAX}",
                timeout=SETTLE_TIMEOUT_MS)
        except Exception:
            log(f"S2 {name} 在 {SETTLE_TIMEOUT_MS}ms 内链接数未超过 {SHELL_LINK_MAX}")
        # 边滚边等，直到链接数连续 SETTLE_STABLE_N 次不变才算取全。
        # 固定滚动次数会在渲染慢时拿到半截：实测同一个主页在不同轮次
        # 取到 40 / 92 / 115 条，而漏掉的链接不会出现在报告的任何地方。
        n = _wait_links_stable(pg, SHELL_LINK_MAX, LINKS_STABLE_TIMEOUT_MS, True)
        if n <= SHELL_LINK_MAX:
            log(f"S2 {name} 链接数 {LINKS_STABLE_TIMEOUT_MS}ms 内未稳定，停在 {n} 条")
        links.extend(pg.evaluate(COLLECT_JS))
        text_parts.append(pg.evaluate(
            "() => document.body.innerText.replace(/\\s+/g,' ')"))
        stats = pg.evaluate(STATS_JS)
        title = pg.title()
        subtabs = expand_subtabs(pg, name, links, text_parts)
        nav = pg.evaluate(
            """() => [...document.querySelectorAll('a[href]')]
                  .map(a => a.getAttribute('href'))
                  .filter(h => h && /^\\/cann\\/?[a-z0-9-]*$/.test(h))""")
        browser.close()

    # 同一条链接可能在多个子页签下重复出现，按 href + 锚文本去重
    seen, uniq = set(), []
    for l in links:
        k = (l["href"], l["text"])
        if k not in seen:
            seen.add(k)
            uniq.append(l)
    text = " ".join(text_parts)
    log(f"S2 {name} 取到 {len(uniq)} 个 <a>"
        + (f"，展开子页签 {len(subtabs)} 个（{'、'.join(subtabs)}）" if subtabs else ""))
    return {"name": name, "links": uniq, "text": text, "subtabs": subtabs,
            "sections": stats["sections"], "clickables": stats["clickables"],
            "nav": nav, "title": title, "shell": len(uniq) <= SHELL_LINK_MAX}


def check_nav_drift(results: list[dict]) -> None:
    """导航里出现了 PAGES 没登记的 /cann/* 页签就点名——改版不能静默漏测。"""
    known = {urlparse(u).path.rstrip("/") for u in PAGES.values()}
    seen = {h.rstrip("/") for r in results for h in r["nav"]}
    extra = sorted(seen - known - {"/cann"})
    if extra:
        log(f"S2 导航出现 {len(extra)} 个未登记页签，本轮未测："
            f"{'、'.join(extra)}。要纳入就加进脚本顶部的 PAGES")


def canonicalize(url: str) -> str:
    """把同一页面的不同写法归一：小写 host、去 fragment、去尾斜杠、丢 query 参数。

    query 整体丢掉——本站是 path 路由（公告/新闻都是 /xxx/detail/999 这种），
    query 基本是子页签选择器（如 open-map?name=h0205）或跟踪参数，不构成新页面。
    """
    u = urlparse(url)
    scheme, netloc = u.scheme.lower(), u.netloc.lower()
    path = u.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", "", ""))


def is_crawlable(url: str) -> bool:
    """是否递归进入：只递归 /cann/ 前缀的站内页面，其余一律当尾节点只探测。"""
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        return False
    if u.netloc.lower() != SITE:
        return False
    if not u.path.startswith(CRAWL_PREFIX):
        return False
    return not u.path.rstrip("/").lower().endswith(SKIP_EXT)


def addr_type(url: str) -> str:
    """全地址表的类型列：站内 / 外链 / 不可探测。"""
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        return "不可探测"
    return "站内" if u.netloc.lower() == SITE else "外链"


# 地址审计分类：只用于实时输出汇总，不做递归判定。递归是否进入只看 is_crawlable 的
# /cann/ 白名单——这里这几个桶是「站内但八成不需要」的页脚导航/文档库/论坛。
ADDR_BUCKETS = (
    ("页脚导航", ("/marketplace/", "/industries/", "/rankings", "/profile/",
                  "/ecosystem", "/feedback", "/poweredbyascend")),
    ("文档库", ("/document/detail/",)),
    ("论坛", ("/forum/", "/dev/forum/")),
)


def bucket_of(url: str) -> str:
    """地址归桶：站内/cann、站内其他、页脚导航、文档库、论坛、外链、不可探测。"""
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        return "不可探测"
    if u.netloc.lower() != SITE:
        return "外链"
    p = u.path
    for name, prefixes in ADDR_BUCKETS:
        if p.startswith(prefixes):
            return name
    return "站内/cann" if p.startswith(CRAWL_PREFIX) else "站内其他"


def dump_addresses(items) -> None:
    """实时输出全部发现地址的分类汇总，把「可能不需要」的桶逐条列出来给人审计。"""
    from collections import Counter
    cnt: dict[str, int] = Counter()
    unwanted: dict[str, list[str]] = {}
    for it in items:
        b = bucket_of(it["url"])
        cnt[b] += 1
        if b in ("页脚导航", "文档库", "论坛"):
            unwanted.setdefault(b, []).append(it["url"])
    order = ("站内/cann", "站内其他", "页脚导航", "文档库", "论坛", "外链", "不可探测")
    log("S3 发现唯一地址 " + "、".join(f"{b} {cnt[b]}" for b in order if cnt[b]))
    for b in ("页脚导航", "文档库", "论坛"):
        if unwanted.get(b):
            log(f"S3  ⚠ {b} {len(unwanted[b])} 条（只探测、不递归）：")
            for u in sorted(set(unwanted[b])):
                log(f"S3      {u}")


def _page_label(name: str) -> str:
    """页面显示名：入口页用中文名，递归子页用路径。"""
    return name if name in PAGES else (urlparse(name).path.rstrip("/") or "/")


# ── 取链缓存：同一天内重跑不再从头爬已抓过的页 ─────────────────────────
# 键是 canonical URL，存 collect_one 的原始结果。探测（S3）永不缓存——
# 巡检的意义就是当天重新验一次链接死活，缓存了就没意义了。
def _cache_path(state: Path, url: str) -> Path:
    import hashlib
    return state / "crawl-cache" / f"{hashlib.sha1(canonicalize(url).encode()).hexdigest()[:16]}.json"


def cache_load(state: Path, url: str):
    p = _cache_path(state, url)
    if not p.exists():
        return None
    if time.time() - p.stat().st_mtime > CRAWL_CACHE_TTL_HOURS * 3600:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def cache_store(state: Path, url: str, result: dict) -> None:
    p = _cache_path(state, url)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def collect_cached(state: Path, name: str, url: str, force: bool = False) -> dict:
    """带缓存的一页取链。force=True 时跳过缓存强制重抓（用于外壳重试）。"""
    if not force:
        c = cache_load(state, url)
        if c is not None:
            c["name"] = name
            log(f"S2 {name} 命中缓存")
            return c
    r = collect_one(name, url)
    cache_store(state, url, r)
    return r


def crawl(state: Path):
    """两阶段递归爬取：先爬21 个入口页算出页头页脚，再从正文链接 BFS 站内页面。

    阶段 A：爬入口页 → 交集 = 全站页头页脚（chrome）。
    阶段 B：从入口页正文链接（非 chrome）BFS，只递归站内页面，直到无新页或到上限。

    返回 (per_page, page_text, page_urls, page_stats, stalled, results)：
    - per_page / page_text：页名 → 链接列表 / 正文，供判定用
    - page_urls：页名 → 该页绝对 URL（递归子页不在 PAGES 里，urljoin 要用它）
    - page_stats：页名 → 统计（含层级/来源页）
    - results：所有爬取页原始记录，供 check_nav_drift
    """
    seen: set[str] = set()
    for u in PAGES.values():
        seen.add(canonicalize(u))

    # ── 阶段 A：21 个入口页 ──
    results: list[dict] = []
    entries = list(PAGES.items())
    for i in range(0, len(entries), PAGE_WORKERS):
        batch = entries[i:i + PAGE_WORKERS]
        with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
            futures = {pool.submit(collect_cached, state, n, u): (n, u) for n, u in batch}
            for fut in as_completed(futures):
                n, u = futures[fut]
                try:
                    r = fut.result()
                except Exception as e:
                    log(f"S2 {n} 取链异常：{type(e).__name__}")
                    r = {"name": n, "links": [], "text": "", "subtabs": [],
                         "sections": 0, "clickables": 0, "nav": [], "shell": False}
                r["level"], r["parent"], r["url"] = 0, "", u
                results.append(r)

    # 外壳塌陷只对入口页重试：递归子页链接数天然少，不能按 45 条判塌陷。
    shells = [r for r in results if r["shell"]]
    if shells:
        log(f"S2 {len(shells)} 个入口页塌成外壳，静置 {SHELL_RETRY_WAIT}s 后串行重取："
            f"{'、'.join(r['name'] for r in shells)}")
        time.sleep(SHELL_RETRY_WAIT)
        for bad in shells:
            again = collect_cached(state, bad["name"], bad["url"], force=True)
            if not again["shell"]:
                again["level"], again["parent"], again["url"] = 0, "", bad["url"]
                results[results.index(bad)] = again
                log(f"S2 {bad['name']} 重取成功")

    # chrome = 入口页共有的 href（页头页脚），递归时排除。
    entry_sets = [{l["href"] for l in r["links"]} for r in results]
    chrome = set.intersection(*entry_sets) if len(entry_sets) > 1 else set()

    # ── 阶段 B：从正文链接 BFS 递归站内页面 ──
    pending: list[tuple[str, str, int, str]] = []
    for r in results:
        for l in r["links"]:
            if l["href"] in chrome:
                continue
            absu = urljoin(r["url"], l["href"])
            if not is_crawlable(absu):
                continue
            c = canonicalize(absu)
            if c in seen:
                continue
            seen.add(c)
            pending.append((absu, absu, 1, r["name"]))
    while pending and len(results) < MAX_PAGES:
        batch: list[tuple[str, str, int, str]] = []
        while pending and len(batch) < PAGE_WORKERS:
            batch.append(pending.pop(0))
        with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
            futures = {pool.submit(collect_cached, state, n, u): (n, u, lv, pa)
                       for n, u, lv, pa in batch}
            for fut in as_completed(futures):
                n, u, lv, pa = futures[fut]
                try:
                    r = fut.result()
                except Exception as e:
                    log(f"S2 {n} 取链异常：{type(e).__name__}")
                    r = {"name": n, "links": [], "text": "", "subtabs": [],
                         "sections": 0, "clickables": 0, "nav": [], "shell": False}
                r["level"], r["parent"], r["url"] = lv, pa, u
                results.append(r)
                for l in r["links"]:
                    if l["href"] in chrome:
                        continue
                    absu = urljoin(u, l["href"])
                    if not is_crawlable(absu):
                        continue
                    c = canonicalize(absu)
                    if c in seen:
                        continue
                    seen.add(c)
                    pending.append((absu, absu, lv + 1, n))
    if pending:
        log(f"S2 递归达到上限 {MAX_PAGES} 页，仍有 {len(pending)} 页未爬")
    log(f"S2 共爬取 {len(results)} 页")

    per_page, page_text, page_urls, page_stats = {}, {}, {}, {}
    stalled = []
    for r in results:
        name = r["name"]
        per_page[name] = r["links"]
        page_text[name] = r["text"]
        page_urls[name] = r["url"]
        page_stats[name] = {
            "页签": _page_label(name),
            "URL": r["url"],
            "层级": r["level"],
            "来源页": _page_label(r["parent"]) if r["parent"] else "—",
            "链接": len(r["links"]),
            "区块": r["sections"],
            "可点元素": r["clickables"],
            "子页签": r["subtabs"],
            "信息点": count_infopoints(r["text"], r["links"]),
        }
        if r["shell"] and r["level"] == 0:
            stalled.append(name)
    check_nav_drift(results)
    return per_page, page_text, page_urls, page_stats, stalled, results


# mailto: / tel: / javascript: / 纯锚点不是可探测的链接。httpx 对它们报
# UnsupportedProtocol，会被 A1 判成「链接打不开」——实测 21 页里两个联系邮箱
# 就是这么变成假阳性的。它们不进判定，但在分页统计里单独计数，不当没看见。
def is_probeable(url: str) -> bool:
    return urlparse(url).scheme in ("http", "https")


def dedup_body_links(per_page, page_urls):
    """去掉各页共有的页头页脚，剩下的是正文链接。

    共有集合只从 21 个入口页的交集里取：递归子页链接少、页头页脚不全，
    用全量页面求交会把入口页的导航也漏算成正文。"""
    entry_names = [n for n in PAGES if n in per_page]
    sets = [{l["href"] for l in per_page[n]} for n in entry_names]
    common = set.intersection(*sets) if len(sets) > 1 else set()
    body, seen, skipped = [], set(), {}
    for name, links in per_page.items():
        for l in links:
            if l["href"] in common:
                continue
            if not is_probeable(urljoin(page_urls[name], l["href"])):
                skipped[name] = skipped.get(name, 0) + 1
                continue
            key = (name, l["href"])
            if key in seen:
                continue
            seen.add(key)
            body.append({"page": name, **l,
                         "url": urljoin(page_urls[name], l["href"])})
    # 页头页脚也是从 CANN 界面跳出去的目标，跳出去就得有效——不在 21 页上重复探测
    # 21 遍，取一次站级唯一集合探测一遍，当作「页头页脚」这一组的链接。
    if common and entry_names:
        base = page_urls[entry_names[0]]
        chrome_text = {l["href"]: l["text"] for l in per_page[entry_names[0]]}
        for href in sorted(common):
            absu = urljoin(base, href)
            if not is_probeable(absu):
                skipped.setdefault("页头页脚", 0)
                skipped["页头页脚"] += 1
                continue
            body.append({"page": "页头页脚", "href": href,
                         "text": chrome_text.get(href, ""),
                         "section": "导航与页脚", "url": absu})
    if skipped:
        log(f"S2 跳过 {sum(skipped.values())} 条不可探测链接"
            f"（mailto / tel / javascript / 纯锚点）："
            f"{'、'.join(f'{k} {v}' for k, v in skipped.items())}")
    return body, len(common), skipped


# ── S3 探测 ────────────────────────────────────────────────────────
def strip_tags(html: str) -> str:
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).strip()


def is_csr(url: str) -> bool:
    u = urlparse(url)
    return u.netloc == SITE and u.path.startswith(CSR_PREFIXES)


def is_meeting(url: str) -> bool:
    return urlparse(url).netloc.split(":")[0] in MEETING_HOSTS


# 外部代码仓：github/gitee/gitcode 等第三方仓库。扫描能看到链接就存在、看不到就没有，
# 仓库死活不归巡检管，且国内网络访问它们常超时，是 A1 的误报源 → 不探测。
CODE_REPO_HOSTS = ("github.com", "gitee.com", "gitcode.com", "atomgit.com", "openi.org.cn")


def is_code_repo(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in CODE_REPO_HOSTS)


def is_repo_file(url: str) -> bool:
    """代码仓「加载失败，请刷新重试」只对 blob/（文件视图）有意义，对 tree/（目录列表）无意义。

    实测：gitcode 对不存在的路径（文件或目录）都会重定向到 blob/ 再兜底显示这行字；
    而存在的目录会留在 tree/，其目录列表是 AJAX 懒加载，并发下瞬时超时也会显示同一行字。
    所以用落地 URL 区分：落到 blob/ 说明 gitcode 认定这是叶子路径、加载失败即软 404；
    留在 tree/ 说明目录存在、只是列表还没刷出来，忽略。
    """
    path = urlparse(url).path
    return "/blob/" in path or "/raw/" in path or "/edit/" in path


def probe_http(url: str):
    """返回 (状态, 终点, 标题, 正文, 重试次数, 错误)。会议类只探到域名根。"""
    import httpx
    target = url
    if is_meeting(url):
        u = urlparse(url)
        target = f"{u.scheme}://{u.netloc}/"
    last_err = ""
    for attempt in range(RETRIES):
        if BACKOFF[attempt]:
            time.sleep(BACKOFF[attempt])
        try:
            r = httpx.get(target, headers={"User-Agent": UA},
                          follow_redirects=True, timeout=HTTP_TIMEOUT)
            title = (re.search(r"(?is)<title[^>]*>(.*?)</title>", r.text) or [None, ""])[1].strip()
            if r.status_code < 400:
                return r.status_code, str(r.url), title, strip_tags(r.text), attempt, ""
            if r.status_code in THROTTLE_CODES:
                return r.status_code, str(r.url), title, "", attempt, f"THROTTLED {r.status_code}"
            last_err = f"HTTP {r.status_code}"
        except Exception as e:
            last_err = f"{type(e).__name__}"
    return None, target, "", "", RETRIES - 1, last_err


def settle(pg) -> str:
    """等到正文长度不再变化，或失败标记出现。

    不用固定字数阈值：实测空壳正文 285 字、图文页 contrib-guide 352 字，
    任何阈值都分不开这两者，只会把还没渲染完的页当成空壳。
    """
    last, stable, waited = -1, 0, 0
    txt = ""
    while waited < SETTLE_TIMEOUT_MS:
        try:
            txt = pg.evaluate("() => document.body.innerText.replace(/\\s+/g,' ').trim()")
        except Exception:
            # 页面在轮询途中自己跳转，执行上下文被销毁。退避一次再取。
            pg.wait_for_timeout(SETTLE_POLL_MS)
            waited += SETTLE_POLL_MS
            try:
                txt = pg.evaluate("() => document.body.innerText.replace(/\\s+/g,' ').trim()")
            except Exception:
                return txt
        if any(m in txt for m in SOFT_404_MARKERS):
            return txt
        stable = stable + 1 if len(txt) == last else 0
        if stable >= SETTLE_STABLE_N and len(txt) > 0:
            return txt
        last = len(txt)
        pg.wait_for_timeout(SETTLE_POLL_MS)
        waited += SETTLE_POLL_MS
    return txt


def render_one(url: str) -> tuple[str, dict]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  locale="zh-CN", user_agent=UA)
        pg = ctx.new_page()
        status = None
        try:
            r = pg.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            status = r.status if r else None
        except Exception as e:
            log(f"S3 渲染 {url} 导航异常：{type(e).__name__}")
        try:
            txt = settle(pg)
            out = {"status": status, "final": pg.url, "title": pg.title(), "body": txt}
        except Exception as e:
            log(f"S3 渲染 {url} 取正文异常：{type(e).__name__}")
            out = {"status": status, "final": url, "title": "", "body": ""}
        browser.close()
    return url, out


def probe_rendered(urls: list[str]):
    """站内 CSR 页：渲染后取标题与正文，状态码与 title 在这些页上不可信。

    串行时实测单页 8.3s，是全流程最大的一块，所以按 RENDER_WORKERS 并发。
    """
    if not urls:
        return {}
    res = {}
    with ThreadPoolExecutor(max_workers=RENDER_WORKERS) as pool:
        futures = {pool.submit(render_one, u): u for u in urls}
        for fut in as_completed(futures):
            try:
                url, out = fut.result()
            except Exception as e:
                url = futures[fut]
                log(f"S3 渲染 {url} worker 异常：{type(e).__name__}")
                out = {"status": None, "final": url, "title": "", "body": ""}
            res[url] = out
    return res


# ── S4 规则判定 ────────────────────────────────────────────────────
VER_RE = re.compile(r"\d+\.\d+\.\d+[A-Za-z0-9.\-]*")  # 只吃 ASCII：\w 会把中文粘进来
DATE_URL_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")


def tokens_for(item: dict) -> list[str]:
    """A4：从锚文本与 URL 提关键 token。提不出就不适用 A4。"""
    toks, url, text = [], item["url"], item["text"]
    host = urlparse(url).netloc
    path = [s for s in urlparse(url).path.split("/") if s]
    if m := VER_RE.search(text):
        toks.append(m.group(0))
    if host in ("gitcode.com", "github.com") and len(path) >= 2:
        toks.append(path[1])                       # 仓库名
        if path[-1] not in ("README.md", "blob", "tree") and len(path) > 3:
            toks.append(path[-2] if path[-1].endswith(".md") else path[-1])
    if "/dynamic-news/" in url and len(text) >= 8:
        toks.append(text[:18])                     # 新闻标题前段
    return [t for t in dict.fromkeys(toks) if len(t) >= 3]


def evaluate(items, page_text, today: date):
    findings, retried = [], 0
    for it in items:
        if it.get("skip"):
            continue
        r = it["probe"]
        if r.get("retries"):
            retried += 1
        loc = f'{it["page"]} · {it["section"] or "正文"}'
        anchor = it["text"] or "（无文字链接）"
        base = {"位置": loc, "锚文本": anchor, "url": it["url"]}

        # A1：确定的「链接没了」只有 404/410。403/405/418/429（反爬验证码）、5xx（瞬时
        # 故障）在探测阶段已标 unresolved → 归「待人工确认」，不判 A1。三次重试仍连不上
        # 的（st 为 None 且无正文）才是真打不开。
        if it.get("unresolved"):
            continue
        st = r.get("status")
        definitive = st in (404, 410)
        if definitive or (not r.get("body") and st is None):
            fact = f"HTTP {st}" if definitive else (r.get("error") or "无响应")
            findings.append({**base, "rule": "A1",
                             "实测": f'三次重试全部失败：{fact}',
                             "期望": "链接可打开"})
            continue
        # 代码仓「文件不存在」：仓库页 200 能开，但文件查看器对不存在的路径兜底显示
        # 「加载失败，请刷新重试」。这是软 404，归 A1（链接指向的东西没了）。
        # 只认落地到 blob/（文件视图）的链接——tree/ 目录列表是 AJAX 懒加载，瞬时超时
        # 也会显示同一行字，见 is_repo_file()。
        if (is_code_repo(it["url"])
                and is_repo_file(r.get("final") or it["url"])
                and any(m in r.get("body", "") for m in REPO_MISSING_MARKERS)):
            findings.append({**base, "rule": "A1",
                             "实测": "仓库页可开，但显示「加载失败，请刷新重试」（文件不存在）",
                             "期望": "文件存在"})
            continue
        # A2 仅站内 CSR
        if it["csr"]:
            body = r.get("body", "")
            hit = next((m for m in SOFT_404_MARKERS if m in body), "")
            if hit:
                findings.append({**base, "rule": "A2",
                                 "实测": f'HTTP {r.get("status")}，渲染后正文 {len(body)} 字，含「{hit}」',
                                 "期望": "正文为该页真实内容"})
                continue
        # A3
        final = r.get("final", "")
        fp = urlparse(final)
        if final and not is_meeting(it["url"]):
            if any(m in final.lower() for m in LOGIN_MARKERS) or (
                    fp.netloc == SITE and fp.path in ("", "/") and urlparse(it["url"]).path not in ("", "/")):
                findings.append({**base, "rule": "A3",
                                 "实测": f"终点跳到 {final}",
                                 "期望": "终点仍是原目标页"})
                continue
        # A4
        if not is_meeting(it["url"]):
            toks = tokens_for(it)
            hay = (r.get("title", "") + " " + r.get("body", "")).lower()
            missing = [t for t in toks if t.lower() not in hay]
            if toks and missing:
                findings.append({**base, "rule": "A4",
                                 "实测": f'目标页标题 {r.get("title","")[:40]!r}，未出现 token {"、".join(missing)}',
                                 "期望": f'目标页出现 {"、".join(toks)}'})
                continue
        # B2
        if is_meeting(it["url"]):
            if m := DATE_URL_RE.search(it["url"]):
                d = date(int(m[1]), int(m[2]), int(m[3]))
                if d < today:
                    findings.append({**base, "rule": "B2",
                                     "实测": f"链接内会议日期 {d}，早于巡检日 {today}",
                                     "期望": "日期不早于巡检日"})

    # B1：下载页版本号必须出现在主页某条版本公告的锚文本里
    dl = VER_RE.search(page_text.get("下载", ""))
    bulletin_vers = [v for it in items if "/productbulletins/detail/" in it["url"]
                     for v in VER_RE.findall(it["text"])]
    if dl and bulletin_vers and dl.group(0) not in bulletin_vers:
        findings.append({"位置": "下载 · 版本选择", "锚文本": "版本选择",
                         "url": PAGES["下载"], "rule": "B1",
                         "实测": f'下载页 {dl.group(0)}，主页版本公告为 {"、".join(bulletin_vers)}',
                         "期望": "下载页版本号出现在主页版本公告中"})

    # B3：公告与新闻各取最新一条的发布日期
    for label, marker, pat, limit, where in (
            ("版本公告", "/productbulletins/detail/",
             re.compile(r"发布时间\s*(\d{4})/(\d{2})/(\d{2})"), BULLETIN_STALE_DAYS, "主页 · 版本公告区"),
            ("动态新闻", "/dynamic-news/",
             re.compile(r"发表于\s*(\d{4})/(\d{2})/(\d{2})"), NEWS_STALE_DAYS, "主页 · 动态新闻区")):
        dated = []
        for it in items:
            if marker in it["url"]:
                if m := pat.search(it["probe"].get("body", "")):
                    dated.append((date(int(m[1]), int(m[2]), int(m[3])), it))
        if not dated:
            continue
        newest, it = max(dated, key=lambda x: x[0])
        age = (today - newest).days
        if age > limit:
            findings.append({"位置": where, "锚文本": it["text"] or label, "url": it["url"],
                             "rule": "B3",
                             "实测": f"最新一条{label}发表于 {newest}，距今 {age} 天",
                             "期望": f"不超过 {limit} 天"})
    return findings, retried


def build_addresses(results, items, findings):
    """全量地址清单：每个唯一 URL 一条，含类型、首次发现页、锚文本、判定结果。"""
    probed = {it["url"] for it in items}
    # 入口页（含「治理架构/社区章程」两个只存在于 sitemap、不被任何页链接的孤儿页）
    # 由 S1 预检 + 爬取验证过，算「通过」，不落进「页头页脚（跳过）」。
    probed.update(PAGES.values())
    unresolved_urls = {it["url"] for it in items if it.get("unresolved")}
    rule_by_url: dict[str, list[str]] = {}
    for f in findings:
        rule_by_url.setdefault(f["url"], []).append(f["rule"])
    # 页面顺序 = 爬取顺序（入口页按导航顺序在前，再递归子页），地址表按界面一个个排。
    page_order: dict[str, int] = {}
    for r in results:
        page_order.setdefault(_page_label(r["name"]), len(page_order))
    addr: dict[str, dict] = {}
    for r in results:
        base = r["url"]
        plabel = _page_label(r["name"])
        for l in r["links"]:
            absu = urljoin(base, l["href"])
            if urlparse(absu).scheme in ("about", "javascript", "data", "void", "blob"):
                continue                      # 占位符/脚本链接，不是地址
            key = canonicalize(absu) if urlparse(absu).scheme in ("http", "https") else absu
            if key not in addr:
                addr[key] = {"URL": absu, "类型": addr_type(absu),
                             "所在页": plabel, "锚文本": (l["text"] or "")[:80],
                             "层级": r["level"]}
    for name, url in PAGES.items():
        key = canonicalize(url)
        if key not in addr:
            addr[key] = {"URL": url, "类型": "站内页", "所在页": name,
                         "锚文本": "", "层级": 0}
    out = []
    for i, a in enumerate(sorted(addr.values(),
                                 key=lambda x: (page_order.get(x["所在页"], 9999), x["URL"])), 1):
        hits = sorted(set(rule_by_url.get(a["URL"], [])))
        if hits:
            verdict = "、".join(hits)
        elif a["类型"] == "不可探测":
            verdict = "—"
        elif a["URL"] in unresolved_urls:
            verdict = "待人工确认"
        elif a["URL"] in probed:
            verdict = "通过"
        else:
            verdict = "页头页脚（跳过）"
        out.append({**a, "序号": i, "判定": verdict})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="昇腾社区 CANN 页面巡检")
    ap.add_argument("--out", required=True, help="输出目录，产物落在 <out>/cann-page-inspect/")
    args = ap.parse_args()

    root = Path(args.out).expanduser().resolve() / "cann-page-inspect"
    state = root / ".state"
    state.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    started = datetime.now()
    today = started.date()

    preflight(state)
    per_page, page_text, page_urls, page_stats, stalled, results = crawl(state)
    if stalled:
        die(f"{'/'.join(stalled)} 入口页取链塌成外壳，本轮结论不可信，不出报告。"
            f"重跑一次；连续两轮同样塌陷见 references/troubleshooting.md", state)
    items, common, skipped = dedup_body_links(per_page, page_urls)
    for name in page_stats:
        page_stats[name]["正文链接"] = sum(1 for it in items if it["page"] == name)
        page_stats[name]["不可探测"] = skipped.get(name, 0)
    log(f"S2 {len(page_stats)} 页共有页头页脚 {common} 条，正文链接 {len(items)} 条")
    if not items:
        die(f"{len(page_stats)} 页去重后正文链接为 0，站点结构可能已改版，本轮不出报告", state)

    dump_addresses(items)
    for it in items:
        it["csr"] = is_csr(it["url"])
    # 爬取阶段已用浏览器渲染过所有站内页面。developer / forum / document 等
    # 非 /cann/ 的站内页同样是 CSR，若再走 HTTP 探测只会拿到 Nuxt 空壳
    # （正文 285 字），版本号/标题 token 全缺 → A4 假阳性。复用爬取时的标题与正文。
    crawled = {canonicalize(r["url"]): r for r in results}
    for it in items:
        c = canonicalize(it["url"])
        if c in crawled:
            rr = crawled[c]
            it["probe"] = {"status": 200, "final": rr["url"],
                           "title": rr.get("title", ""), "body": rr["text"][:4000],
                           "retries": 0, "error": ""}
    csr_urls = sorted({it["url"] for it in items if it["csr"] and "probe" not in it})
    log(f"S3 复用爬取结果 {len([i for i in items if 'probe' in i and not i.get('skip')])} 条，"
        f"站内 CSR 需渲染 {len(csr_urls)} 条，"
        f"其余 {len([i for i in items if 'probe' not in i]) - len(csr_urls)} 条走 HTTP")
    rendered = probe_rendered(csr_urls)
    for it in items:
        if it["csr"] and "probe" not in it:
            r = rendered.get(it["url"], {})
            it["probe"] = {**r, "retries": 0, "error": "" if r.get("body") else "渲染无正文"}

    # 代码仓链接（github/gitee/gitcode）从国内用 httpx 会被 418/405/超时误伤，改走浏览器拿真实
    # 状态码：404/410=仓库或文件没了（A1），200=存在，浏览器也打不开=待人工确认。
    repo_urls = sorted({it["url"] for it in items
                        if "probe" not in it and is_code_repo(it["url"])})
    if repo_urls:
        repo_rendered = probe_rendered(repo_urls)
        # 「加载失败，请刷新重试」有歧义：既可能是文件/目录真不存在（软 404），也可能
        # 是并发下文件列表/内容 AJAX 瞬时超时——gitcode tree/ 目录页在负载下尤其容易
        # 瞬时显示这行字，刷新后目录列表就出来了。所以刷新重试一次再下结论：重试后
        # 内容出来了算瞬时失败（通过），仍显示「加载失败」才是真软 404，留给 A1。
        suspect = [u for u, r in repo_rendered.items()
                   if r.get("body") and is_repo_file(r.get("final") or u)
                   and any(m in r["body"] for m in REPO_MISSING_MARKERS)]
        if suspect:
            log(f"S3 代码仓 {len(suspect)} 条显示「加载失败」，刷新重试复核")
            confirm = probe_rendered(suspect)
            for u in suspect:
                r = confirm.get(u, {})
                if r.get("body") and not any(m in r["body"] for m in REPO_MISSING_MARKERS):
                    repo_rendered[u] = r
        for it in items:
            if "probe" in it or not is_code_repo(it["url"]):
                continue
            r = repo_rendered.get(it["url"], {})
            st = r.get("status")
            if st is not None and st >= 400:
                it["probe"] = {"status": st, "final": r.get("final", it["url"]),
                               "title": r.get("title", ""), "body": "",
                               "retries": 0, "error": f"HTTP {st}"}
            elif st is not None and r.get("body"):
                it["probe"] = {"status": st, "final": r.get("final", it["url"]),
                               "title": r.get("title", ""), "body": r["body"][:4000],
                               "retries": 0, "error": ""}
            else:
                it["unresolved"] = True
                it["probe"] = {"status": None, "final": it["url"], "title": "",
                               "body": "", "retries": 0, "error": "UNREACHABLE"}

    http_items = [it for it in items if "probe" not in it]
    with ThreadPoolExecutor(max_workers=HTTP_WORKERS) as pool:
        futures = {pool.submit(probe_http, it["url"]): it for it in http_items}
        for fut in as_completed(futures):
            it = futures[fut]
            st, final, title, body, retries, err = fut.result()
            it["probe"] = {"status": st, "final": final, "title": title,
                           "body": body[:4000], "retries": retries, "error": err}

    throttled = [it for it in items
                 if not it["csr"] and str(it["probe"].get("error", "")).startswith("THROTTLED")]
    if throttled:
        log(f"S3 {len(throttled)} 条被限流（{THROTTLE_CODES}），用浏览器复核")
        recheck = probe_rendered([it["url"] for it in throttled])
        for it in throttled:
            r = recheck.get(it["url"], {})
            if r.get("body"):
                it["probe"].update({"status": r.get("status"), "final": r.get("final"),
                                    "title": r.get("title"), "body": r["body"][:4000],
                                    "error": ""})
                it["probe"]["retries"] = it["probe"].get("retries", 0) + 1
            else:
                it["probe"]["error"] = f'{it["probe"]["error"]}，浏览器复核仍失败'

    # 反爬/验证码（403/405/418/429）、瞬时 5xx、验证码页都是「现在判不了」而非「链接失效」：
    # 标 unresolved → 地址表归「待人工确认」，不喂给 A1/A4。
    CAPTCHA_MARKERS = ("安全验证码", "滑块验证", "曲线滑块", "滑动验证",
                       "验证失败", "验证码", "请求存在异常")
    for it in items:
        if it.get("skip") or it.get("unresolved"):
            continue
        r = it["probe"]
        st = r.get("status")
        err = str(r.get("error", ""))
        text = f'{r.get("title", "")} {r.get("body", "")}'
        if (st in THROTTLE_CODES
                or (isinstance(st, int) and st >= 500)
                or err.startswith("HTTP 5")
                or any(m in text for m in CAPTCHA_MARKERS)):
            it["unresolved"] = True

    findings, retried = evaluate(items, page_text, today)
    addresses = build_addresses(results, items, findings)
    hits = {rid: sum(1 for f in findings if f["rule"] == rid) for rid, _, _, _ in RULES}

    def _stat_row(name, v):
        return {"页签": v["页签"], "URL": v["URL"], "层级": v["层级"],
                "来源页": v["来源页"], "链接": v["链接"],
                "正文链接": v.get("正文链接", 0), "区块": v["区块"],
                "可点元素": v["可点元素"], "子页签": v["子页签"],
                "信息点": v["信息点"], "不可探测": v.get("不可探测", 0)}

    payload = {
        "巡检时间": started.strftime("%Y-%m-%d %H:%M"),
        "巡检日": today.isoformat(),
        "耗时秒": round(time.time() - t0, 1),
        "目标": list(PAGES.values()),
        "页面总数": len(page_stats),
        "地址总数": len(addresses),
        "链接总数": len(items),
        "分页统计": [_stat_row(n, v) for n, v in
                     sorted(page_stats.items(),
                            key=lambda kv: (kv[1]["层级"], kv[1]["URL"]))],
        "全部地址": addresses,
        "重试后通过": retried,
        "规则": [{"编号": r, "分组": g, "检查项": n, "判定依据": b,
                  "命中": hits[r]} for r, g, n, b in RULES],
        "问题": findings,
    }
    (state / "last-run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"S4 判定完成：{len(items)} 条链接，命中 {len(findings)} 条，重试后通过 {retried} 条")
    # 取链是并发的，日志按时间戳排序才读得懂
    (state / "run.log").write_text("\n".join(sorted(LOG)), encoding="utf-8")
    print(f"\n巡检完成：{len(findings)} 条需处置")
    print(f"结果：{state / 'last-run.json'}")
    print(f"渲染报告：python3 {Path(__file__).parent / 'render.py'} --out {args.out}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
