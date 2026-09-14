#!/usr/bin/env python3
"""把 verdict.json 渲染成 report.md 与 index.html。

**只读 verdict.json，不碰别的产物。** 裁决在 verdict.py 里做完了，这里一个数
都不重算——两处各算一遍，早晚会出现「HTML 说通过、md 说不通过」。

`verdict.py` 跑完自己调 `write_all`；也能单独跑，用来改完模板重新出报告：

    python render.py --verdict report/verdict.json --outdir report
"""

import argparse
import html
import json
import sys
from pathlib import Path

# 精度表的细分列。全过时不出这几列——一张干净的表比一排 0 更好读。
DETAIL_COLUMNS = (("acc_false", "精度不符"), ("exec_failed", "执行失败"),
                  ("undetermined", "未判定"))


def _fmt(value, suffix=""):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def _needs_detail(rows):
    """任一 dtype 有非通过的用例时才展开细分列。"""
    return any(row[key] for row in rows for key, _ in DETAIL_COLUMNS)


def _acc_columns(rows, group_by="dtype"):
    """返回 (表头, 取值函数列表)。两个渲染器共用，保证 md 与 html 列一致。

    `group_by` 是精度表第一列的表头，由 `verdict.json` 给。**不写死 dtype**：
    纯 attr 用例按别的轴分组，写死之后报告会说「dtype: csr」，读者以为
    csr 是个 dtype。
    """
    head = [group_by or "dtype", "用例", "通过"]
    getters = [lambda r: r["dtype"], lambda r: r["total"], lambda r: r["matched"]]
    if _needs_detail(rows):
        for key, title in DETAIL_COLUMNS:
            head.append(title)
            getters.append(lambda r, k=key: r[k])
    head += ["通过率", "达标"]
    # 「未覆盖」与「不达标」是两回事：前者没测过，后者测了没过。
    # 混成 ✗ 会让作者去查一个根本没跑过的 dtype；混成 ✓ 更糟，见 verdict 里的说明。
    getters += [lambda r: f"{r['rate']}%",
                lambda r: "—未覆盖" if r.get("uncovered") else ("✓" if r["ok"] else "✗")]
    return head, getters


def _band_columns(perf):
    """性能表的列随 band_metric 变：有基线出加速比，没基线只出绝对耗时。"""
    label = perf.get("row_label", "规模档")
    if perf.get("band_metric") == "absolute":
        return ([label, "用例数", "Device 耗时中位数"],
                [lambda r: r["band"], lambda r: r["n"],
                 lambda r: _fmt(r["mine"], " us")])
    return ([label, "样本", "被测中位数", "基线中位数", "加速比", "结论"],
            [lambda r: r["band"], lambda r: r["n"], lambda r: _fmt(r["mine"], " us"),
             lambda r: _fmt(r["base"], " us"), lambda r: r["speedup"] or "—",
             lambda r: r["verdict"] or "—"])


def _executor_blocks(verdict, acc):
    """双执行器那一节的内容，md 与 html 共用。

    返回块列表：`("p", 文本)` 或 `("table", 表头, 取值函数, 行)`。文本按 markdown
    写，html 侧过 `_inline` 转标记。

    **必须共用。** 早先这一节只有 `report.md` 有，`index.html` 一个字都不说，而
    `shadowed` 非空时首屏那张精度表恰恰是 CANN 内置实现的数——看报告的人拿不到
    任何提示，会把内置实现的通过率当成待验收实现的结论。
    """
    ex = verdict.get("executors") or {}
    if not ex.get("applicable", True):
        # **不写这一节的话，读报告的人分不清「没做」与「不适用」。**
        # 两者在版面上都是「这一节没有内容」，但前者要回去补，后者补不了。
        return [
            ("p", f"**tiling 归属与独立执行器两项不适用**"
                  f"（{ex.get('not_applicable_reason') or '执行剖面不支持'}）。"),
            ("p", "代价是少了一路交叉验证：aclnn 剖面下 ATK 判失败的用例还会被"
                  "独立执行器复核一遍，这条路上只有 ATK 一条路线。"
                  "**所以本轮的失败判定全靠隔离复验**，那一步的结论要逐条看。"),
        ]
    if not ex.get("tiling_checked"):
        return []

    blocks = []
    if ex.get("shadowed_op_types"):
        blocks += [
            ("p", "**CANN 内置的同名 tiling 抢先注册，算子包自带那份被拒**"
                  f"（op type：{'、'.join(ex['shadowed_op_types'])}）。"),
            ("p", "ATK 必须拉起 torch_npu，而 torch_npu 会让 GE 先注册内置 tiling，"
                  "GE 按 op type 先到先得。**所以上面那节 ATK 测的是 CANN 里的旧实现，"
                  "不是本次交付的代码**；只有独立执行器用得上算子包自带的 tiling。"),
            ("p", "**本报告的验收依据是独立执行器那一轮**，ATK 的数只作对照。"),
        ]
    else:
        blocks.append(("p", "没有同名内置 tiling 抢占，ATK 用的就是算子包自带那份，"
                            "上面的精度结论直接可用。"))

    cxx = ex.get("cxx")
    # 两种内存布局各一轮，**通过率分开报**：合成一个数会把「连续下好好的、
    # 非连续下挂一片」这种最常见的形态抹平成一个中间数。
    for label, block in (("连续", cxx), ("非连续", ex.get("cxx_noncontig"))):
        if not block:
            continue
        note = (f"独立执行器**{label}轮**跑了 **{block['scope']}** "
                f"{block['total']} 条，通过 {block['passed']} 条"
                f"（{block['pass_rate']}%）——与上面的精度表同口径，已把 "
                f"{block['batch_only_count']} 条批内污染计入通过"
                f"（改判前是 {block['raw_pass_rate']}%）。")
        if block.get("skipped_count"):
            # 跳过的用例不是通过，报告里必须自己说出来，不能只体现在条数差上。
            gap = block.get("uncovered_dtypes") or []
            note += (f" **另有 {block['skipped_count']} 条没跑成**"
                     + (f"，其中 {'、'.join(gap)} 是整类未覆盖" if gap else "")
                     + "，见 verdict.json 的 `skipped_ids`。")
        blocks.append(("p", note))

    if ex.get("missing_layouts"):
        blocks.append(
            ("p", f"**缺 {'、'.join(ex['missing_layouts'])}布局那一轮。** "
                  "任务书要求支持非连续时两种布局都要在权威路径上跑过；"
                  "少的那一轮只有 ATK 测过，而 ATK 测的是被抢走 tiling 的"
                  "内置实现——那个布局下待验收实现算得对不对，本轮验收没有答案。"))

    if cxx:
        rows = []
        for title, key in (("执行失败", "exec_failed_ids"),
                           ("精度不符", "accuracy_false_ids")):
            atk_ids = set(acc.get(key) or [])
            cxx_ids = set(cxx.get(key) or [])
            only_cxx = sorted(cxx_ids - atk_ids)
            rows.append([title, str(len(atk_ids)), str(len(cxx_ids)),
                         ("、".join(str(i) for i in only_cxx[:14]) or "无")
                         + ("…" if len(only_cxx) > 14 else "")])
        blocks.append(("table", ["类别", "ATK", "独立执行器", "只有独立执行器抓到"],
                       [lambda r, i=i: r[i] for i in range(4)], rows))
        blocks.append(
            ("p", "「只有独立执行器抓到」那一列是 **ATK 结构上测不到的缺陷**——"
                  "它们只在算子包自带的 tiling 下才出现。**这些 id 要一并交给"
                  "算子作者**，用 `repro/` 里的 C++ 那半边复现（`./run_cxx.sh <卡号>`）。"))
    else:
        blocks.append(
            ("p", "独立执行器那一轮没有产物（`stage/accuracy_cxx.json` 不存在），"
                  "**A3.5 没跑或跑失败了**，本报告缺这一层证据。"))
    return blocks


def _atk_round_caveat(verdict):
    """`shadowed` 非空时，精度表上方那句提醒。

    精度表的数**永远来自 ATK**，被抢走 tiling 时它测的不是待验收实现。这句话必须
    紧贴表格，不能只放在下面那一节——首屏读者未必往下翻。
    """
    ex = verdict.get("executors") or {}
    if not ex.get("applicable", True):
        return None
    # `tiling_checked` 也要成立：这句话结尾指「下节」，而下节由 `_executor_blocks`
    # 出，它没出的时候这句就指向一个不存在的章节。
    if not (ex.get("tiling_checked") and ex.get("shadowed_op_types")):
        return ""
    return ("**下表是 ATK 那一轮的数，测的是 CANN 内置的同名实现，不是本次交付的"
            "代码**（内置 tiling 抢先注册，见下节）。待验收实现的结论在下节的"
            "独立执行器那一轮。")


# ---------------------------------------------------------------- markdown


def _scenario_columns():
    """逐场景表的列。**中位数与 90%分位并列摆出来**，那是任务书要的两个读数。

    md 与 html 共用这一份定义：各写一份时两份报告的表会漂，而它们署着同一次验收。
    """
    head = ["性能场景", "来源", "中位数(us)", "90%分位(us)", "标杆(us)", "倍率", "读数口径"]
    getters = [
        lambda r: str(r.get("label") or r.get("id")),
        lambda r: str(r.get("source") or "—"),
        lambda r: _fmt(r.get("median_us")),
        lambda r: _fmt(r.get("p90_us")),
        lambda r: _fmt(r.get("baseline_us")),
        lambda r: _fmt(r.get("ratio")),
        lambda r: str(r.get("stat") or "—"),
    ]
    return head, getters


def _md_table(head, getters, rows):
    out = ["| " + " | ".join(head) + " |",
           "| " + " | ".join("---" for _ in head) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(get(row)) for get in getters) + " |")
    return out


def render_markdown(verdict):
    acc = verdict["accuracy"]
    perf = verdict["performance"]
    lines = [
        f"# {verdict['op']} 算子验收结论",
        "",
        f"**总结论：{verdict['overall']}**",
        "",
        verdict["env"]["summary"],
        "",
        "## 部署",
        "",
        # 路径要露在报告里：拿母仓自己那份当交付物验收时，这一行就露馅。
        f"- 被测算子源码：`{verdict['deploy'].get('op_dir', '?')}`",
        f"- {verdict['deploy'].get('source_line', '源码版本：未记录')}",
        f"- 构建位置：`{verdict['deploy'].get('target', '?')}`",
        f"- 自定义算子包：`{verdict['deploy'].get('vendor_dir', '?')}`",
        f"- 符号可见：`{verdict['deploy'].get('symbol', '?')}`",
        f"- 构建耗时：{verdict['deploy'].get('build_seconds', '?')} s",
        "",
        "## 精度",
        "",
    ]
    caveat = _atk_round_caveat(verdict)
    if caveat:
        lines += [caveat, ""]
    if not acc.get("executed"):
        lines += ["精度轮未执行。", ""]
    else:
        lines += _md_table(*_acc_columns(acc["by_dtype"], acc.get("group_by")),
                             acc["by_dtype"])
        lines += ["", acc["summary"], ""]
        if acc.get("exec_reject"):
            lines += [f"**{acc['exec_reject']}**", ""]
        if acc.get("count_mismatch"):
            lines += [f"**{acc['count_mismatch']}**", ""]
        for para in acc["notes"]:
            lines += [para, ""]
        for title, key in (("执行失败", "exec_failed_ids"),
                           ("精度不符", "accuracy_false_ids")):
            if not acc.get(key):
                continue
            head = acc[key][:20]
            more = (f"…（共 {len(acc[key])} 条，全量见 verdict.json）"
                    if len(acc[key]) > 20 else "")
            lines += [f"{title}用例 id：{head}{more}", ""]
        if acc.get("exec_failed_ids") or acc.get("accuracy_false_ids"):
            lines += ["分组只按用例规格特征划分，成因需算子作者定位，本报告不归因。"
                      "两类都进了 `repro/failed/cases.json`。", ""]

    blocks = _executor_blocks(verdict, acc)
    if blocks:
        lines += ["## tiling 归属与双执行器对照", ""]
        for block in blocks:
            if block[0] == "p":
                lines += [block[1], ""]
            else:
                lines += _md_table(*block[1:])
                lines.append("")

    lines += ["## 性能", "", f"形态：{perf['label']}", "", f"状态：{perf['status']}", ""]
    if perf["bands"]:
        lines += _md_table(*_band_columns(perf), perf["bands"])
        lines.append("")
    # 按来源分组。**每一批各测了多少条要写出来**：合成一个总数会让判据表那几条
    # 和泛化集那几十条在报告上看起来像同一批，覆盖面就说不清了。
    if perf.get("external_by_source"):
        lines += _md_table(
            ["性能场景来源", "条数", "有基线", "倍率中位", "最低", "低于门槛"],
            [lambda r: r["source"], lambda r: str(r["total"]),
             lambda r: str(r.get("with_baseline", 0)),
             lambda r: _fmt(r.get("ratio_median")), lambda r: _fmt(r.get("ratio_min")),
             lambda r: (f"{r['below_threshold']} 条"
                        + (f"（{'、'.join(map(str, r['below_ids']))}）"
                           if r.get("below_ids") else "")
                        if r.get("below_threshold") else "无")],
            perf["external_by_source"])
        lines.append("")
    # 逐场景两个读数。任务书要求「报告耗时中位数及90%分位耗时」，
    # 上面那张按来源分组的汇总替代不了它。
    if perf.get("external_scenarios"):
        lines += _md_table(*_scenario_columns(), perf["external_scenarios"])
        lines.append("")
    for para in perf["notes"]:
        lines += [para, ""]

    if verdict.get("unconsumed_stage"):
        lines += ["### 采了没进结论的产物", "",
                  "下列产物在 `work/stage/` 里有数据，本次结论没有读它们。"
                  "真机时间已经花掉，要么接进结论，要么说明为什么不用。", ""]
        lines += [f"- `stage/{item['file']}`：{item['rows']} 条"
                  for item in verdict["unconsumed_stage"]]
        lines.append("")

    if verdict["inferred_facts"]:
        lines += ["## 推断项", "",
                  "下列事实没有任务书或工程文档依据，是从基线接口推断的，结论受其影响：",
                  ""]
        lines += [f"- {name}" for name in verdict["inferred_facts"]]
        lines.append("")

    repro = verdict["repro"]
    lines += ["## 复现", "",
              f"最小复现包在 `{repro['dir']}`，用法见其中的 `README.md`：",
              "", "```bash", f"cd {repro['dir']}"]
    lines += [f"bash rerun.sh {cmd}" for cmd in repro["commands"]]
    lines += ["```", ""]

    lines += ["## 证据链", ""]
    lines += [f"- {name}：`{path}`" for name, path in verdict["evidence"].items()]
    lines.append("")
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------------- html

CSS = """
:root {
  --bg: #ffffff; --fg: #1a1a1a; --muted: #6b6b70; --line: #e3e3e6;
  --panel: #fafafa; --ok: #1a7f4b; --bad: #b4232c; --warn: #8a6100;
  --ok-bg: #e8f5ee; --bad-bg: #fbeaeb; --warn-bg: #fdf3e0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16171a; --fg: #e8e8ea; --muted: #9a9aa2; --line: #2e2f34;
    --panel: #1d1e22; --ok: #5cc98d; --bad: #f08b91; --warn: #e0b25c;
    --ok-bg: #17301f; --bad-bg: #331a1c; --warn-bg: #302614;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem 3rem; background: var(--bg); color: var(--fg);
  font: 15px/1.6 -apple-system, "Segoe UI", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
}
main { max-width: 52rem; margin: 0 auto; }
h1 { font-size: 1.35rem; margin: 0 0 .2rem; }
h2 {
  font-size: .82rem; letter-spacing: .09em; text-transform: uppercase;
  color: var(--muted); margin: 2rem 0 .6rem; font-weight: 600;
}
.meta { color: var(--muted); font-size: .82rem; margin: 0; }
.badge {
  display: inline-block; padding: .18rem .6rem; border-radius: 4px;
  font-weight: 600; font-size: .95rem; margin-left: .5rem; vertical-align: 2px;
}
.badge.ok { background: var(--ok-bg); color: var(--ok); }
.badge.bad { background: var(--bad-bg); color: var(--bad); }
.badge.warn { background: var(--warn-bg); color: var(--warn); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td {
  text-align: right; padding: .42rem .7rem; border-bottom: 1px solid var(--line);
  white-space: nowrap;
}
th:first-child, td:first-child { text-align: left; }
th { color: var(--muted); font-weight: 600; font-size: .78rem; }
tbody tr:last-child td { border-bottom: none; }
tr.total td { font-weight: 600; border-top: 1px solid var(--line); }
.mark-ok { color: var(--ok); }
.mark-bad { color: var(--bad); }
.status { margin: 0 0 .7rem; }
.note { color: var(--muted); font-size: .85rem; margin: .5rem 0 0; }
.panel {
  background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
  padding: .8rem 1rem; margin-top: .6rem;
}
/* 精度表测的不是待验收实现时的提醒。首屏读者未必往下翻到双执行器那节，
   所以这句必须紧贴表格且颜色上跳出来。 */
.caveat {
  background: var(--warn-bg); border-left: 3px solid var(--warn);
  border-radius: 0 4px 4px 0; padding: .6rem .9rem; margin: 0 0 .8rem;
  font-size: .88rem;
}
code, pre {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: .85em;
}
pre { margin: .4rem 0 0; overflow-x: auto; }
footer {
  margin-top: 2.5rem; padding-top: .8rem; border-top: 1px solid var(--line);
  color: var(--muted); font-size: .8rem;
}
"""


def _esc(value):
    return html.escape(str(value), quote=False)


def _inline(text):
    """把说明段落里的 `code` 与 **bold** 转成 HTML。

    这些段落是 verdict.json 里的 markdown 文本，两个渲染器共用一份。
    HTML 侧不转的话，读者会在网页上看到一堆反引号和星号。
    """
    out, escaped = [], _esc(text)
    for index, chunk in enumerate(escaped.split("`")):
        out.append(f"<code>{chunk}</code>" if index % 2 else chunk)
    joined = "".join(out)
    parts = joined.split("**")
    return "".join(f"<strong>{c}</strong>" if i % 2 else c
                   for i, c in enumerate(parts)) if len(parts) > 2 else joined


def _badge_class(overall):
    if overall.startswith("通过"):
        return "ok"
    if overall.startswith("不通过"):
        return "bad"
    return "warn"


def _html_table(head, getters, rows):
    out = ['<div class="scroll"><table><thead><tr>']
    out += [f"<th>{_esc(h)}</th>" for h in head]
    out.append("</tr></thead><tbody>")
    for row in rows:
        # 精度表与性能表的行是 dict（带 `total_row` 标合计行），双执行器对照那张
        # 表的行是 list——`_md_table` 对两者都无所谓，这里得自己认。
        cls = (' class="total"'
               if isinstance(row, dict) and row.get("total_row") else "")
        cells = []
        for get in getters:
            value = get(row)
            if value == "✓":
                cells.append('<td class="mark-ok">✓</td>')
            elif value == "✗":
                cells.append('<td class="mark-bad">✗</td>')
            else:
                cells.append(f"<td>{_esc(value)}</td>")
        out.append(f"<tr{cls}>" + "".join(cells) + "</tr>")
    out.append("</tbody></table></div>")
    return "\n".join(out)


def render_html(verdict):
    acc = verdict["accuracy"]
    perf = verdict["performance"]
    repro = verdict["repro"]
    parts = [
        "<!doctype html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(verdict['op'])} 算子验收</title>",
        f"<style>{CSS}</style></head><body>",
        "<main>",
        f"<h1>{_esc(verdict['op'])} 算子验收"
        f'<span class="badge {_badge_class(verdict["overall"])}">'
        f"{_esc(verdict['overall'])}</span></h1>",
        f'<p class="meta">{_esc(verdict["env"]["summary"])}</p>',
        # 装错算子目录时，报告里唯一的破绽就是这一行。抬头就露，
        # 不要只藏在 report.md 的部署那节——汇报只看 index.html。
        f'<p class="meta">{_esc(verdict["deploy"].get("op_dir", "?"))}'
        f'｜{_esc(verdict["deploy"].get("source_line", ""))}</p>',
        "<h2>精度</h2>",
    ]
    caveat = _atk_round_caveat(verdict)
    if caveat:
        parts.append(f'<p class="caveat">{_inline(caveat)}</p>')
    if not acc.get("executed"):
        parts.append('<p class="status">未执行。</p>')
    else:
        parts.append(_html_table(*_acc_columns(acc["by_dtype"], acc.get("group_by")),
                                    acc["by_dtype"]))
        parts.append(f'<p class="note">{_inline(acc["summary"])}</p>')
        if acc.get("exec_reject"):
            parts.append(f'<p class="note">{_inline(acc["exec_reject"])}</p>')
        if acc.get("count_mismatch"):
            parts.append(f'<p class="note">{_inline(acc["count_mismatch"])}</p>')

    # 双执行器那一节 md 与 html 都要出。早先只有 md 有，而 shadowed 时首屏那张
    # 精度表恰恰是内置实现的数——只看 index.html 的人会把它当成验收结论。
    blocks = _executor_blocks(verdict, acc)
    if blocks:
        parts.append("<h2>tiling 归属与双执行器对照</h2>")
        for block in blocks:
            if block[0] == "p":
                parts.append(f'<p class="note">{_inline(block[1])}</p>')
            else:
                parts.append(_html_table(*block[1:]))

    parts.append("<h2>性能</h2>")
    parts.append(f'<p class="status">{_esc(perf["label"])} · {_esc(perf["status"])}</p>')
    if perf["bands"]:
        parts.append(_html_table(*_band_columns(perf), perf["bands"]))
    if perf.get("external_by_source"):
        parts.append(_html_table(
            ["性能场景来源", "条数", "有基线", "倍率中位", "最低", "低于门槛"],
            [lambda r: r["source"], lambda r: str(r["total"]),
             lambda r: str(r.get("with_baseline", 0)),
             lambda r: _fmt(r.get("ratio_median")), lambda r: _fmt(r.get("ratio_min")),
             lambda r: (f"{r['below_threshold']} 条"
                        if r.get("below_threshold") else "无")],
            perf["external_by_source"]))
    if perf.get("external_scenarios"):
        parts.append(_html_table(*_scenario_columns(), perf["external_scenarios"]))
    if perf.get("headline"):
        parts.append(f'<p class="note">{_inline(perf["headline"])}</p>')
    for para in perf.get("notes") or []:
        parts.append(f'<p class="note">{_inline(para)}</p>')

    parts.append("<h2>失败用例与复现</h2>")
    ids = repro.get("case_ids") or []
    if not acc.get("executed"):
        body = "精度轮未执行，没有失败用例清单。"
    elif ids:
        shown = "、".join(str(i) for i in ids[:24])
        more = f" …（共 {len(ids)} 条）" if len(ids) > 24 else ""
        body = (f"未通过 {len(ids)} 条"
                f"（执行失败 {len(acc.get('exec_failed_ids') or [])}、"
                f"精度不符 {len(acc.get('accuracy_false_ids') or [])}）：id {shown}{more}")
    else:
        body = "没有未通过的用例。"
    lines = "\n".join(f"bash rerun.sh {_esc(c)}" for c in repro["commands"])
    parts += [
        f'<div class="panel"><p class="status">{_esc(body)}</p>',
        f"<pre>cd {_esc(repro['dir'])}\n{lines}</pre></div>",
    ]
    parts += [
        "<footer>详版证据链见 <code>report.md</code>，"
        "机器可读结论见 <code>verdict.json</code>，"
        "过程数据在 <code>work/</code>。</footer>",
        "</main></body></html>",
    ]
    return "\n".join(parts) + "\n"


def write_all(verdict, outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "report.md").write_text(render_markdown(verdict), encoding="utf-8")
    (outdir / "index.html").write_text(render_html(verdict), encoding="utf-8")
    return outdir / "report.md", outdir / "index.html"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verdict", default="report/verdict.json")
    parser.add_argument("--outdir", default="report")
    args = parser.parse_args()

    source = Path(args.verdict)
    if not source.exists():
        print(f"{source} 不存在，先跑 verdict.py。", file=sys.stderr)
        return 3
    with open(source, encoding="utf-8") as handle:
        verdict = json.load(handle)
    md, page = write_all(verdict, args.outdir)
    print(f"写入      {md}、{page}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
