#!/usr/bin/env python3
"""把外部性能工具的产出收编成 `stage/performance_external.json`。

**为什么不由本仓去跑那个工具。** 任务书要求的性能口径常常超出 ATK 能表达的
范围——分阶段耗时、中位数与 p90、复用描述符再采样、两个独立计时范围、
workspace 峰值。这些要算子自己的 benchmark 脚本才采得到，而那个脚本的命令行、
输出布局逐任务不同，猜它等于把一份猜测写进量具。

所以分工是：**外部工具负责采，本脚本负责收编与核对，`verdict.py` 负责裁决。**
中间靠一份极小的交换格式，见下面的 SCHEMA。

不带 `--from` 时读标准输入，方便 `<外部工具> | collect_perf.py -o ...`。
"""

import argparse
import json
import statistics
import sys
from pathlib import Path
import stage_clock  # noqa: E402 - 同目录量具，给 stage JSON 记阶段墙钟

# ---- 交换格式 -------------------------------------------------------------
#
# 一个 JSON 对象，两个必填键。**字段少是刻意的**：每多一个字段就多一处
# 外部工具与本仓要对齐的地方，而它们不在同一个仓里。
#
# {
#   "source": "任务方 benchmark_sparse_ops_npu.py",   # 谁采的，进报告
#   "unit": "us",                                     # 只认 us
#   "criterion": "性能倍率达到 0.25 倍性能标杆以上",   # 可选，缺省取 facts
#   "cases": [
#     {"id": 0,                       # 与用例包 cases.json 的 id 对齐
#      "under_test_us": 41.2,         # 待验收实现的耗时**中位数**
#      "p90_us": 44.9,                # 90%分位耗时。任务书与中位数并列要求，两个都进报告
#      "baseline_us": 34.688,         # 标杆耗时；缺省表示这条没有基线
#      "label": "P-01 CSR int8",      # 可选，进报告的行名
#      "stage": "convert",            # 可选，分阶段时哪一段
#      "samples": 30,                 # 可选，实际采了几次；报告用它核口径
#      "stat_kind": "median_p90"}     # 可选，这条数的统计口径。见下
#   ]
# }
#
# **`under_test_us` 的口径是中位数，不是平均值。** 任务书原话是「报告耗时中位数
# 及90%分位耗时」（sparse 三份任务书逐字相同），两个统计量里没有平均。量测件
# 一律按 `kernel_trace.summarize` 出这两个数；只有逐次读数认不出、退回自带件
# 汇总的平均列时才写 `stat_kind="mean"`，那样的条目**报告里逐条标出来**，
# 不冒充成任务书口径。缺 `stat_kind` 时按 `median_p90` 认。
#
# 倍率 = baseline_us / under_test_us，**大于 1 表示比标杆快**。
# 方向与任务书「性能倍率 = 标杆耗时 / NPU 耗时」一致，不要反过来。

REQUIRED_CASE_KEYS = ("id", "under_test_us")


def _load(handle):
    try:
        return json.load(handle)
    except ValueError as exc:
        print(f"不是合法 JSON：{exc}", file=sys.stderr)
        return None


def _check(data, case_ids):
    """校验交换格式。返回问题列表，空列表表示通过。

    **宁可在这里拦下，也不要让半份数据流进结论。** 外部工具与本仓不在一个仓里，
    它改了输出格式这边不会有任何编译期提示。
    """
    problems = []
    if not isinstance(data, dict):
        return ["顶层不是 JSON 对象"]
    unit = data.get("unit")
    if unit != "us":
        problems.append(f"unit={unit!r}，只认 'us'。别的单位在这里换算等于"
                        f"把换算错误藏进结论，回外部工具那侧改")
    if not data.get("source"):
        problems.append("缺 source：谁采的这批数要进报告，"
                        "否则读报告的人分不清它和 ATK 那轮")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        return problems + ["cases 缺失或为空"]
    # 摸底数不许进验收结论。外部工具减了采样次数就打这个标记，这里一律拒收——
    # **少采次数的数看着和正常的一模一样**，混进去之后报告上分辨不出来。
    off = [c.get("id") for c in cases if isinstance(c, dict) and c.get("off_spec")]
    if data.get("off_spec") or off:
        problems.append(
            f"这批是摸底数（采样次数低于验收口径）：{off[:5]}。"
            f"按任务书口径重跑，或如实标注后只作参考，不进结论")

    seen = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            problems.append(f"cases[{index}] 不是对象")
            continue
        missing = [k for k in REQUIRED_CASE_KEYS if case.get(k) is None]
        if missing:
            problems.append(f"cases[{index}] 缺 {'、'.join(missing)}")
            continue
        ident = case["id"]
        if ident in seen:
            problems.append(f"用例 id={ident} 出现多次，取哪条无从判断")
        seen.add(ident)
        for key in ("under_test_us", "baseline_us"):
            value = case.get(key)
            if value is None:
                continue
            if not isinstance(value, (int, float)) or value <= 0:
                problems.append(f"用例 id={ident} 的 {key}={value!r} 不是正数")

    if case_ids:
        stray = sorted(seen - case_ids)
        if stray:
            problems.append(f"这些 id 不在用例包里：{stray[:12]}"
                            f"{'…' if len(stray) > 12 else ''}。"
                            f"外部工具用的是另一批用例，两侧的数对不上号")
    return problems


def _summarize(cases):
    """逐条算倍率，再汇总。**逐样本先算比值再取中位数**，不是两组中位数相除。

    后者只在两组分布完全一致时才等价，任何一条掉队都会悄悄失衡——
    与 `verdict.py` 的 `_band_rows` 同一条口径。
    """
    rows, ratios = [], []
    for case in sorted(cases, key=lambda c: c["id"]):
        under = float(case["under_test_us"])
        base = case.get("baseline_us")
        ratio = (float(base) / under) if base else None
        if ratio is not None:
            ratios.append(ratio)
        row = {"id": case["id"], "label": case.get("label", ""),
               "stage": case.get("stage", ""),
               "under_test_us": under,
               "baseline_us": float(base) if base else None,
               "ratio": round(ratio, 4) if ratio is not None else None}
        # 任务书要求中位数与 90%分位**并列进报告**，收编时丢掉 p90 等于少报一项。
        # 采样次数与口径标记跟着数走，报告靠它们核这条数合不合验收口径。
        for key in ("p90_us", "min_us", "max_us", "samples", "warmup",
                    "stat_kind", "stat_note", "sampling_note"):
            if case.get(key) is not None:
                row[key] = case[key]
        row.setdefault("stat_kind", "median_p90")
        rows.append(row)
    # 口径不符的条目单独记一笔：报告要说清哪几条不是任务书要的中位数与 90%分位。
    off_spec = [r["id"] for r in rows if r.get("stat_kind") != "median_p90"]
    no_p90 = [r["id"] for r in rows if r.get("p90_us") is None]
    summary = {"total": len(rows), "with_baseline": len(ratios),
               "stat_off_spec_ids": off_spec[:20],
               "stat_off_spec": len(off_spec),
               "without_p90": len(no_p90)}
    if ratios:
        summary.update(ratio_median=round(statistics.median(ratios), 4),
                       ratio_min=round(min(ratios), 4),
                       ratio_max=round(max(ratios), 4))
    return rows, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="source", default="",
                        help="外部工具产出的 JSON；不给则读标准输入")
    parser.add_argument("-c", "--cases", default="../input/cases.json",
                        help="用例包的用例清单，用来核对 id 对不对得上")
    parser.add_argument("--facts", default="../input/facts.json")
    parser.add_argument("-o", "--out", default="stage/performance_external.json")
    args = parser.parse_args()

    if args.source:
        path = Path(args.source)
        if not path.is_file():
            print(f"{path} 不存在。", file=sys.stderr)
            return 3
        with open(path, encoding="utf-8") as handle:
            data = _load(handle)
    else:
        data = _load(sys.stdin)
    if data is None:
        return 3

    case_ids = set()
    try:
        with open(args.cases, encoding="utf-8") as handle:
            payload = json.load(handle)
        # 自产用例包是裸数组；任务方自带件多包一层 `{"cases": [...]}`。
        # 认不出就退回空集合，那等于放弃对账，所以这里两种都认。
        items = payload["cases"] if isinstance(payload, dict) else payload
        case_ids = {c["id"] for c in items}
    except (OSError, ValueError, KeyError, TypeError):
        print(f"读不到 {args.cases}，跳过 id 对账。**这一步跳过之后，"
              f"外部工具用的是不是同一批用例就没人核了。**", file=sys.stderr)

    problems = _check(data, case_ids)
    if problems:
        print(f"\n交换格式不合规，{len(problems)} 处：", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        print("\n格式说明见 collect_perf.py 顶部的 SCHEMA 注释，"
              "或 references/external-perf.md。", file=sys.stderr)
        return 2

    rows, summary = _summarize(data["cases"])
    criterion = data.get("criterion")
    if not criterion:
        try:
            with open(args.facts, encoding="utf-8") as handle:
                criterion = (json.load(handle).get("performance") or {}).get("criterion")
        except (OSError, ValueError):
            criterion = ""
    record = {"source": data["source"], "unit": "us",
              "criterion": criterion or "", "rows": rows, **summary}

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(stage_clock.stamp(record), handle, ensure_ascii=False, indent=2)

    print(f"外部性能  {data['source']}")
    print(f"用例      {summary['total']} 条，其中 {summary['with_baseline']} 条带基线")
    if summary.get("ratio_median") is not None:
        print(f"倍率      中位数 {summary['ratio_median']}，"
              f"区间 [{summary['ratio_min']}, {summary['ratio_max']}]"
              f"（倍率 = 标杆耗时 / 待验收耗时，大于 1 表示更快）")
    else:
        print("倍率      算不了，一条基线都没给。报告只会有绝对耗时")
    print(f"读数口径  中位数 + 90%分位（任务书口径），{summary['total'] - summary['without_p90']}"
          f"/{summary['total']} 条带 p90")
    if summary["stat_off_spec"]:
        print(f"          其中 {summary['stat_off_spec']} 条只有平均值："
              f"{summary['stat_off_spec_ids'][:8]}，报告逐条标注，不当任务书口径用")
    elif summary["without_p90"]:
        print(f"          其中 {summary['without_p90']} 条没给 p90，"
              f"量测件按 kernel_trace.summarize 出数就会有")
    print(f"写入      {out}")
    print("\n裁决由 verdict.py 做，不在这里判。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
