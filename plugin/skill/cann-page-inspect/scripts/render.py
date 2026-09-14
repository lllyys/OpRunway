#!/usr/bin/env python3
"""把 last-run.json 渲染成单文件 report.html，并滚动 baseline.json。

用法：python3 render.py --out <输出目录>
只读 last-run.json，不重新探测——判定与渲染分开，避免两处结论不一致。
报告不加载任何外部资源：巡检机器可能没网，报告可能被转发。
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import sys
from datetime import date
from pathlib import Path

BLOCKING = {"A1", "A2", "A3", "A4"}   # A 组：看不到或看错了
# 2026-08-29 编号从 R1-R7 改成 A1-A4 / B1-B3。baseline.json 用「编号|url」做键，
# 不迁移就会把所有历史问题在改版当天全部标成「今日新增」。
RULE_ID_MIGRATION = {"R1": "A1", "R2": "A2", "R3": "A3", "R4": "A4",
                     "R5": "B1", "R6": "B2", "R7": "B3"}
CSS = """
:root{--ground:#f5f6f8;--surface:#fff;--surface-2:#fafbfc;--head:#f0f2f5;
--ink:#111519;--ink-2:#3d454f;--muted:#6c7684;--line:#dfe4ea;--line-2:#ebeef2;
--block:#b8332a;--degr:#9a6410;--ok:#2b7a4b;
--font:-apple-system,BlinkMacSystemFont,"PingFang SC","HarmonyOS Sans SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
--mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
--ground:#0f1216;--surface:#171b21;--surface-2:#1b2027;--head:#1e242c;
--ink:#e7eaee;--ink-2:#b9c0ca;--muted:#8b95a3;--line:#293039;--line-2:#232931;
--block:#f0837a;--degr:#dda65f;--ok:#6cc48c;}}
:root[data-theme="dark"]{--ground:#0f1216;--surface:#171b21;--surface-2:#1b2027;--head:#1e242c;
--ink:#e7eaee;--ink-2:#b9c0ca;--muted:#8b95a3;--line:#293039;--line-2:#232931;
--block:#f0837a;--degr:#dda65f;--ok:#6cc48c;}
*{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);font-family:var(--font);font-size:14.5px;
line-height:1.6;-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:40px 22px 72px;display:flex;flex-direction:column;gap:30px}
.head-block{display:flex;flex-direction:column;gap:12px}
h1{margin:0;font-size:26px;font-weight:650;letter-spacing:-.015em;line-height:1.25}
h1 .n{color:var(--block);font-variant-numeric:tabular-nums}
h1 .allok{color:var(--ok)}
.meta{display:flex;flex-wrap:wrap;gap:5px 22px;font-size:12.5px;color:var(--muted)}
.meta code{font-family:var(--mono);font-size:12px;color:var(--ink-2)}
section{display:flex;flex-direction:column;gap:11px}
.cap{display:flex;align-items:baseline;gap:10px}
.cap h2{margin:0;font-size:15px;font-weight:640;letter-spacing:-.005em}
.cap span{font-size:12.5px;color:var(--muted)}
.tbox{background:var(--surface);border:1px solid var(--line);border-radius:8px;overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13.5px}
thead th{background:var(--head);text-align:left;font-weight:600;font-size:11.5px;
letter-spacing:.07em;color:var(--muted);padding:9px 14px;white-space:nowrap;border-bottom:1px solid var(--line)}
tbody td{padding:11px 14px;border-bottom:1px solid var(--line-2);vertical-align:top}
tbody tr:last-child td{border-bottom:0}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.rid{font-family:var(--mono);font-weight:600;white-space:nowrap}
.check{font-weight:600;white-space:nowrap}
.basis{color:var(--ink-2)}
.hit{font-weight:700;font-size:15px}
.hit.z{color:var(--muted);opacity:.4;font-weight:500}
.r-block{color:var(--block)}.r-degr{color:var(--degr)}
tr.grp td{background:#eef1f5;font-size:12px;color:#3b4252;padding:5px 10px}
tr.sum td{background:#f7f8fa;border-top:2px solid #d8dce3}
tr.zero td{color:var(--muted)}tr.zero .rid,tr.zero .check{font-weight:500}
.where b{display:block;font-weight:640;color:var(--ink);font-size:14px;margin-top:1px}
.where span{font-size:12px;color:var(--muted)}
.fact{color:var(--ink-2);min-width:230px}
.want{color:var(--ink-2);min-width:160px}
.since{white-space:nowrap;font-size:12.5px;color:var(--muted);font-variant-numeric:tabular-nums}
.since b{display:block;color:var(--ink-2);font-weight:600}
.since .new{color:var(--block);font-weight:700}
a.go{display:inline-block;font-size:12.5px;text-decoration:none;white-space:nowrap;
padding:4px 10px;border:1px solid var(--line);border-radius:5px;background:var(--surface-2);color:var(--ink-2)}
a.go:hover{border-color:var(--ink-2);color:var(--ink)}
a.go:focus-visible{outline:2px solid var(--degr);outline-offset:1px}
.urlline{display:block;font-family:var(--mono);font-size:11px;color:var(--muted);
margin-top:5px;max-width:330px;word-break:break-all}
.foot{border-top:1px solid var(--line);padding-top:15px;display:flex;flex-wrap:wrap;
gap:6px 24px;font-size:12.5px;color:var(--muted)}
.foot b.okn{color:var(--ok);font-variant-numeric:tabular-nums}
.foot code{font-family:var(--mono);font-size:11.5px}
@media (max-width:640px){.wrap{padding:26px 14px 56px}h1{font-size:21px}}
"""


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def short(text: str, limit: int = 42) -> str:
    """锚文本可能整条就是一个长 URL，位置列放不下，截断显示。"""
    text = str(text).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def roll_baseline(state: Path, findings: list[dict], today: date):
    """基线只存每条异常的首次发现日期，让人分得出今天新坏的和老问题。"""
    f = state / "baseline.json"
    raw = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    old = {}
    for k, v in raw.items():
        rid, sep, url = k.partition("|")
        old[f"{RULE_ID_MIGRATION.get(rid, rid)}{sep}{url}"] = v
    new = {}
    for item in findings:
        key = f'{item["rule"]}|{item["url"]}'
        first = old.get(key, today.isoformat())
        new[key] = first
        d = date.fromisoformat(first)
        days = (today - d).days + 1
        item["首次发现"] = ("今日新增", first) if days <= 1 else (f"已连续 {days} 天", f"{first} 起")
    f.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(old) - len(set(old) & set(new))


def render(data: dict, state: Path) -> str:
    today = date.fromisoformat(data["巡检日"])
    findings = sorted(data["问题"], key=lambda x: x["rule"])
    roll_baseline(state, findings, today)
    n = len(findings)
    ok = data["链接总数"] - len({f["url"] for f in findings})

    verdict = (f'<span class="n">{n} 条</span>需处置' if n
               else '<span class="allok">全部正常</span>')
    rows, cur_group = [], None
    for r in data["规则"]:
        grp = r.get("分组", "")
        if grp and grp != cur_group:
            cur_group = grp
            n_grp = sum(x["命中"] for x in data["规则"] if x.get("分组") == grp)
            tail = f"命中 {n_grp} 条" if n_grp else "全部通过"
            rows.append(f'<tr class="grp"><td colspan="3"><b>{esc(grp)}</b></td>'
                        f'<td class="num">{esc(tail)}</td></tr>')
        z = "" if r["命中"] else ' class="zero"'
        cls = "r-block" if r["编号"] in BLOCKING else "r-degr"
        hit = (f'<td class="num hit {cls}">{r["命中"]}</td>' if r["命中"]
               else '<td class="num hit z">0</td>')
        rows.append(
            f'<tr{z}><td class="rid">{esc(r["编号"])}</td>'
            f'<td class="check">{esc(r["检查项"])}</td>'
            f'<td class="basis">{esc(r["判定依据"])}</td>{hit}</tr>')

    detail = ""
    if findings:
        drows = []
        for f in findings:
            cls = "r-block" if f["rule"] in BLOCKING else "r-degr"
            tag, sub = f["首次发现"]
            tcls = ' class="new"' if tag == "今日新增" else ""
            page, _, sec = f["位置"].partition(" · ")
            drows.append(
                f'<tr><td class="rid {cls}">{esc(f["rule"])}</td>'
                f'<td class="where"><span>{esc(page)} · {esc(sec)}</span>'
                f'<b>{esc(short(f["锚文本"]))}</b></td>'
                f'<td class="fact">{esc(f["实测"])}</td>'
                f'<td class="want">{esc(f["期望"])}</td>'
                f'<td class="since"><b{tcls}>{esc(tag)}</b>{esc(sub)}</td>'
                f'<td><a class="go" href="{esc(f["url"])}" target="_blank" rel="noopener">'
                f'打开目标 ↗</a><span class="urlline">{esc(f["url"])}</span></td></tr>')
        detail = f"""
  <section>
    <div class="cap"><h2>问题明细</h2><span>按规则编号排序，正常链接不列</span></div>
    <div class="tbox"><table><thead><tr>
      <th style="width:56px">编号</th><th style="width:190px">位置</th><th>实测</th>
      <th style="width:170px">期望</th><th style="width:92px">首次发现</th>
      <th style="width:110px">复核</th>
    </tr></thead><tbody>{''.join(drows)}</tbody></table></div>
  </section>"""

    prows = []
    for st in data.get("分页统计", []):
        prows.append(
            f'<tr><td class="check">{esc(st["页签"])}'
            f'<span class="urlline">{esc(st.get("URL", ""))}</span></td>'
            f'<td class="num">{st.get("正文链接", 0)}</td></tr>')
    tot_links = sum(st.get("正文链接", 0) for st in data.get("分页统计", []))
    if prows:
        prows.append(
            f'<tr class="sum"><td class="check"><b>合计 {len(data.get("分页统计", []))} 个页面</b></td>'
            f'<td class="num"><b>{tot_links}</b></td></tr>')
    perpage = f"""
  <section>
    <div class="cap"><h2>检查域</h2><span>本轮扫到的页面与其正文链接数——七条规则只判正文链接，页头页脚单列、探测一次</span></div>
    <div class="tbox"><table><thead><tr>
      <th style="width:300px">页面 / URL</th>
      <th class="num" style="width:100px">正文链接</th>
    </tr></thead><tbody>{''.join(prows)}</tbody></table></div>
  </section>""" if prows else ""

    arows = []
    for a in data.get("全部地址", []):
        v = a.get("判定", "")
        if v in ("通过", "—", "页头页脚（跳过）"):
            acls = ""
        elif v == "待人工确认":
            acls = "r-degr"
        else:
            acls = "r-block"
        arows.append(
            f'<tr><td class="where"><span>{esc(a["所在页"])}</span>'
            f'<b>{esc(short(a.get("锚文本") or "（无文字链接）"))}</b></td>'
            f'<td class="{acls}">{esc(v)}</td>'
            f'<td><a class="go" href="{esc(a["URL"])}" target="_blank" rel="noopener">打开 ↗</a>'
            f'<span class="urlline">{esc(a["URL"])}</span></td></tr>')
    addrs = f"""
  <section>
    <div class="cap"><h2>全部地址</h2><span>递归爬取到的所有 URL，共 {len(data.get("全部地址", []))} 条。
      完整清单见 <code>addresses.csv</code>（UTF-8，Excel 可直接打开筛选）</span></div>
    <div class="tbox"><table><thead><tr>
      <th style="width:210px">所在页 / 锚文本</th>
      <th style="width:110px">判定</th><th>URL</th>
    </tr></thead><tbody>{''.join(arows)}</tbody></table></div>
  </section>""" if data.get("全部地址") else ""

    return f"""<title>CANN 页面巡检</title>
<style>{CSS}</style>
<div class="wrap">
  <header class="head-block">
    <h1>{verdict}</h1>
    <div class="meta">
      <span>巡检时间 <code>{esc(data["巡检时间"])}</code></span>
      <span>检查域 <code>{len(data.get("分页统计", []))} 个页面</code>
        · <code>{tot_links} 条正文链接</code>
        · <code>{data.get("地址总数", 0)} 条地址</code></span>
      <span>耗时 <code>{esc(data["耗时秒"])} 秒</code></span>
    </div>
  </header>{perpage}
  <section>
    <div class="cap"><h2>规则汇总</h2><span>A 组查「点了能不能拿到东西」，B 组查「拿到的东西是不是过期或自相矛盾」；检查了什么范围见上方「检查域」</span></div>
    <div class="tbox"><table><thead><tr>
      <th style="width:56px">编号</th><th style="width:170px">检查项</th>
      <th>判定依据</th><th class="num" style="width:64px">命中</th>
    </tr></thead><tbody>{''.join(rows)}</tbody></table></div>
  </section>{detail}{addrs}
  <footer class="foot">
    <span>共检查 <b class="okn">{data["链接总数"]}</b> 条链接，<b class="okn">{ok} 条</b>通过全部适用规则</span>
    <span><b class="okn">{data["重试后通过"]}</b> 条经重试后通过</span>
    <span>全量结果 <code>.state/last-run.json</code></span>
  </footer>
</div>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染 CANN 页面巡检报告")
    ap.add_argument("--out", required=True, help="与 inspect.py 相同的输出目录")
    args = ap.parse_args()
    root = Path(args.out).expanduser().resolve() / "cann-page-inspect"
    src = root / ".state" / "last-run.json"
    if not src.exists():
        print(f"阻塞·找不到 {src}，先跑 page_inspect.py", file=sys.stderr)
        return 2
    data = json.loads(src.read_text(encoding="utf-8"))
    out = root / "report.html"
    out.write_text(render(data, root / ".state"), encoding="utf-8")
    print(f"报告已写入：{out}")
    # 导出全部地址 CSV（UTF-8 with BOM，Excel 直接打开不乱码）
    csv_path = root / "addresses.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["序号", "层级", "所在页", "锚文本", "类型", "判定", "URL"])
        for a in data.get("全部地址", []):
            w.writerow([a.get("序号", ""), a.get("层级", ""), a.get("所在页", ""),
                        a.get("锚文本", ""), a.get("类型", ""), a.get("判定", ""),
                        a.get("URL", "")])
    print(f"地址清单已导出：{csv_path}")
    print(f"打开：open {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
