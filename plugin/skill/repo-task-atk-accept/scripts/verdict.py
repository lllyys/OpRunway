#!/usr/bin/env python3
"""汇总各阶段产物推出验收结论，并写 report.md。

结论由本脚本从数据推导，不由报告作者自己写。
退出码 0 结论已产出（通过或不通过都算），3 缺产物。
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# facts.json 的 performance.kind 说的是「任务书要求怎么比」，
# 与 _perf_status 推出来的「本轮比到哪一步」是两件事，报告里分开写。
PERF_LABEL = {
    "none": "任务书未给性能基线",
    "builtin": "对标 CANN 内置实现",
    "cross_dtype": "同一算子内跨 dtype 自比",
}


def _load(path, required=True):
    file = Path(path)
    if not file.exists():
        if required:
            print(f"{path} 不存在，前面的阶段没跑完。", file=sys.stderr)
            return None
        return {}
    with open(file, encoding="utf-8") as handle:
        return json.load(handle)


def _dtype_of(case):
    for item in case.get("inputs", []):
        if isinstance(item, dict) and item.get("type") == "tensor":
            return item.get("dtype", "?")
    return "?"


def _group_failures(cases, failed_ids):
    """失败归因只分组到用例规格特征，不猜成因。"""
    by_id = {case["id"]: case for case in cases}
    groups = Counter()
    for ident in failed_ids:
        case = by_id.get(ident)
        groups[_dtype_of(case) if case else "?"] += 1
    return groups


def _perf_status(facts, accuracy, performance, baseline):
    """性能状态由数据推导。精度未过一律不评级——算错的算子跑得快没有意义。

    kind=builtin 时还要看基线轮**真的采到数据没有**。基线轮跑完但一条不成，
    说明内置实现根本跑不了这批用例，那是没有基线可比，不是已评级。
    """
    kind = (facts.get("performance") or {}).get("kind", "none")
    if not accuracy:
        return "未执行(精度未裁决)", kind
    if not accuracy.get("passed"):
        # 已经采到的耗时不丢，但只当参考数据，不出评级结论。
        return ("未评级(精度未通过，仅留参考数据)" if performance
                else "未执行(精度未通过)"), kind
    if not performance:
        return "未执行(未跑性能轮)", kind
    if kind == "none":
        return "未评级(无基线)", kind
    if kind == "builtin" and not _aclnn_times(baseline or {}):
        return "未评级(基线轮无数据)", kind
    return "已评级", kind


# Device 耗时逐次波动通常在 5% 以内，判劣化要留余量。
NOT_WORSE = 1.05
WORSE = 1.10


def _median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else None


def _aclnn_times(report):
    times = {int(k): v for k, v in (report.get("device_times") or {}).items()}
    return {ident: entry["aclnn"] for ident, entry in times.items() if "aclnn" in entry}


def _verdict_ratio(ratio):
    if ratio <= NOT_WORSE:
        return "不劣化"
    if ratio > WORSE:
        return "劣化"
    return "unknown（落在噪声区间，建议重跑一轮确认）"


def _no_baseline_lines(baseline):
    """基线轮拿不到耗时时，把「为什么拿不到」说清楚，三种去向不一样。"""
    head = ["任务书要求对标 CANN 内置实现，但基线轮没有产出耗时数据，"
            "性能结论 **unknown**。"]
    if not baseline:
        return head + [
            "基线轮还没跑。命令见 run-performance.md「kind = builtin」。",
        ]
    total = baseline.get("total", 0)
    failed = baseline.get("failed", 0)
    if total and failed >= total:
        return head + [
            "",
            f"基线轮 {total} 条用例**全部执行失败**——内置实现跑不了这批用例。",
            "内置实现的参数校验通常比待验收实现窄（社区任务多半就是为此而来），",
            "报错码在 `evidence/performance_builtin.log` 里，捞法见 troubleshooting.md。",
            "",
            "**这不能算「待验收实现更快」。** 没有基线就是没有基线，",
            "结论是 unknown，把内置实现跑不了这件事写进报告备注即可。",
        ]
    return head + [
        "",
        "基线轮跑了但报告里没有 Device 耗时列，多半是任务被中途打断。",
        "确认 `evidence/performance_builtin.log` 的结尾再重跑一次。",
    ]


def _builtin_lines(under_test, baseline, facts, cases):
    """kind=builtin：与 CANN 内置实现逐用例比 Device 耗时。

    比较只能覆盖内置实现支持的 dtype——社区任务新增的那几种它按定义就不支持。
    报告必须写明覆盖了哪些、漏了哪些，否则读者会以为结论覆盖全部 dtype。
    """
    ids = [i for i in under_test if i in baseline]
    if len(ids) < 3:
        return ["与内置实现能配上对的用例少于 3 条，性能结论 unknown。"]

    by_id = {case["id"]: case for case in cases}
    covered = sorted({_dtype_of(by_id[i]) for i in ids if i in by_id})
    declared = sorted({d for param in facts.get("params", [])
                       if param.get("role") == "input"
                       for d in param.get("dtypes", [])
                       if d not in {"int", "attr_bool", "string"}})
    missing = [d for d in declared if d not in covered]

    mine = _median([under_test[i] for i in ids])
    theirs = _median([baseline[i] for i in ids])
    ratio = mine / theirs if theirs else 0

    lines = [
        "| 项 | 待验收实现 | CANN 内置实现 |",
        "| --- | --- | --- |",
        f"| 配对用例 | {len(ids)} 条 | {len(ids)} 条 |",
        f"| Device 耗时中位数 | {mine:.2f} us | {theirs:.2f} us |",
        "",
        f"比值 {ratio:.3f}（判据：≤{NOT_WORSE} 不劣化，>{WORSE} 劣化，之间 unknown）",
        "",
        f"**结论：{_verdict_ratio(ratio)}**",
        "",
        f"比较覆盖的 dtype：{'、'.join(covered)}。",
    ]
    if missing:
        lines += [
            f"**未覆盖：{'、'.join(missing)}** —— 内置实现不支持这几种，没有基线可比，"
            f"它们的性能结论是 `unknown`。",
        ]
    return lines


def _cross_dtype_lines(facts, performance, cases):
    """kind=cross_dtype：同一算子内按 dtype 分组自比。"""
    times = _aclnn_times(performance)
    by_id = {case["id"]: case for case in cases}
    groups = {}
    for ident, value in times.items():
        case = by_id.get(ident)
        if case:
            groups.setdefault(_dtype_of(case), []).append(value)

    lines = ["| 被测 dtype | 基线 dtype | 被测中位数 | 基线中位数 | 比值 | 结论 |",
             "| --- | --- | --- | --- | --- | --- |"]
    for pair in (facts.get("performance") or {}).get("pairs", []):
        mine_group, base_group = groups.get(pair[0], []), groups.get(pair[1], [])
        if len(mine_group) < 3 or len(base_group) < 3:
            lines.append(f"| {pair[0]} | {pair[1]} | — | — | — | unknown（样本不足 3 条）|")
            continue
        mine, theirs = _median(mine_group), _median(base_group)
        ratio = mine / theirs if theirs else 0
        lines.append(f"| {pair[0]} | {pair[1]} | {mine:.2f} us | {theirs:.2f} us | "
                     f"{ratio:.3f} | {_verdict_ratio(ratio)} |")
    lines += ["", "同一 dtype 内不同 shape 的耗时差几个量级，这里比的是各组中位数；"
                  "要更严的结论需按 shape 档位分组后再比。"]
    return lines


def _perf_lines(facts, performance, baseline, cases):
    """按性能形态给出可读结论。数据不足就写 unknown，不猜。"""
    kind = (facts.get("performance") or {}).get("kind", "none")
    under_test = _aclnn_times(performance)
    if not under_test:
        return ["aclnn 侧没有 Device 耗时，性能结论 unknown。"]

    ordered = sorted(under_test.values())
    lines = [f"待验收实现 {len(ordered)} 条用例，Device 耗时中位数 "
             f"{_median(ordered):.2f} us，区间 [{ordered[0]:.2f}, {ordered[-1]:.2f}] us。",
             ""]

    if kind == "builtin":
        base_times = _aclnn_times(baseline) if baseline else {}
        if not base_times:
            lines += _no_baseline_lines(baseline)
        else:
            lines += _builtin_lines(under_test, base_times, facts, cases)
    elif kind == "cross_dtype":
        lines += _cross_dtype_lines(facts, performance, cases)
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--facts", default="facts.json")
    parser.add_argument("--install", default="install.json")
    parser.add_argument("--accuracy", default="accuracy.json")
    parser.add_argument("--performance", default="performance.json")
    parser.add_argument("--performance-builtin", default="performance_builtin.json",
                        help="kind=builtin 时的内置实现基线轮结果")
    parser.add_argument("--isolate", default="isolate.json",
                        help="隔离复验结果，有就用它区分真实失败与连带失败")
    parser.add_argument("--cases", default="cases.json")
    parser.add_argument("-o", "--out", default="verdict.json")
    parser.add_argument("--report", default="report.md")
    args = parser.parse_args()

    facts = _load(args.facts)
    install = _load(args.install)
    if facts is None or install is None:
        return 3
    accuracy = _load(args.accuracy, required=False)
    performance = _load(args.performance, required=False)
    baseline = _load(args.performance_builtin, required=False)
    isolate = _load(args.isolate, required=False)
    cases = _load(args.cases, required=False) or []

    failed_ids = accuracy.get("failed_ids", []) if accuracy else []
    cascade_ids, unchecked_ids = [], []
    if isolate and isolate.get("checked"):
        # aicore 异常会让同批次后续用例连带失败。隔离复验过的才分得清真假；
        # --max-isolate 截断掉的那些没复验过，是 unknown，不能算真实失败。
        cascade_ids = isolate.get("cascade", [])
        unchecked_ids = isolate.get("not_checked", [])
        known = set(cascade_ids) | set(unchecked_ids)
        failed_ids = [i for i in failed_ids if i not in known]
    groups = _group_failures(cases, failed_ids)
    perf_status, perf_kind = _perf_status(facts, accuracy, performance, baseline)

    # 连带失败的用例根本没被测到，不该进通过率的分母。
    total = accuracy.get("total", 0) if accuracy else 0
    matched = accuracy.get("matched", 0) if accuracy else 0
    effective_total = total - len(cascade_ids) - len(unchecked_ids)
    effective_rate = round(100.0 * matched / effective_total, 2) if effective_total else 0.0

    # 总结论要同时看精度与性能。任务书给了性能基线却没验到，
    # 那一条要求就是没验，不能算全通过。
    if not accuracy:
        overall = "未裁决(精度未执行)"
    elif not accuracy.get("passed"):
        overall = "不通过"
    elif perf_kind == "none" or perf_status == "已评级":
        overall = "通过"
    else:
        overall = f"精度通过·性能待定（{perf_status}）"

    verdict = {
        "op": facts.get("op"),
        "aclnn_name": facts.get("aclnn_name"),
        "overall": overall,
        "accuracy": {
            "total": accuracy.get("total", 0),
            "succeeded": accuracy.get("succeeded", 0),
            "failed": accuracy.get("failed", 0),
            "matched": accuracy.get("matched", 0),
            "pass_rate": accuracy.get("pass_rate", 0.0),
            "effective_total": effective_total,
            "effective_pass_rate": effective_rate,
            "passed": accuracy.get("passed", False),
            "real_failed_ids": failed_ids,
            "cascade_failed_ids": cascade_ids,
            "unchecked_failed_ids": unchecked_ids,
            "failed_by_dtype": dict(groups),
        },
        "performance": {"kind": perf_kind, "status": perf_status},
        "deploy": {
            "vendor_dir": install.get("vendor_dir", ""),
            "symbol": install.get("symbol", ""),
            "build_seconds": (install.get("build") or {}).get("elapsed_seconds"),
        },
        "inferred_facts": [p["name"] for p in facts.get("params", [])
                           if p.get("source") == "inferred"],
    }

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(verdict, handle, ensure_ascii=False, indent=2)

    lines = [
        f"# {facts.get('op')} 算子验收结论",
        "",
        f"**总结论：{overall}**",
        "",
        "## 部署",
        "",
        f"- 自定义算子包：`{install.get('vendor_dir', '?')}`",
        f"- 符号可见：`{install.get('symbol', '?')}`",
        f"- 构建耗时：{(install.get('build') or {}).get('elapsed_seconds', '?')} s",
        "",
        "## 精度",
        "",
    ]
    if accuracy:
        lines += [
            f"| 项 | 值 |",
            f"| --- | --- |",
            f"| 总用例数 | {accuracy.get('total')} |",
            f"| 执行成功 | {accuracy.get('succeeded')} |",
            f"| 执行失败 | {accuracy.get('failed')} |",
            f"| 精度通过 | {accuracy.get('matched')} |",
            f"| 通过率 | {accuracy.get('pass_rate')}% |",
            f"| 是否达标 | {'是' if accuracy.get('passed') else '否'} |",
            "",
            f"标杆为生成侧冻结的 CPU golden，比对走 ATK `accuracy_load`，"
            f"精度标准 `{(facts.get('accuracy') or {}).get('acc', 'default')}`。",
            "",
        ]
        if cascade_ids:
            lines += [
                f"其中 {len(cascade_ids)} 条经隔离复验为**连带失败**——单独重跑能通过，",
                "是前一条用例触发 aicore 异常后设备状态未恢复导致的余波。",
                "",
                "这些用例本轮根本没被测到，不进通过率分母：",
                "",
                f"| 项 | 值 |",
                f"| --- | --- |",
                f"| 有效分母 | {effective_total}（{total} 减去 {len(cascade_ids)} 条连带"
                f"{'、' + str(len(unchecked_ids)) + ' 条未复验' if unchecked_ids else ''}） |",
                f"| 有效通过率 | {effective_rate}% |",
                f"| 真实失败 | {len(failed_ids)} 条 |",
                "",
                "连带失败的用例需要在算子缺陷修复后重跑才能得出结论，本轮记 `unknown`。",
                "",
            ]
        if unchecked_ids:
            lines += [
                f"另有 {len(unchecked_ids)} 条执行失败**未做隔离复验**"
                f"（`--max-isolate` 截断）。它们既没被证明是真实失败，"
                f"也没被证明是连带，本轮记 `unknown`。",
                "",
                f"要把它们分清，调大 `--max-isolate` 重跑隔离复验。",
                "",
            ]
        if failed_ids:
            head = failed_ids[:20]
            more = f"…（共 {len(failed_ids)} 条，全量见 verdict.json）" if len(failed_ids) > 20 else ""
            lines += [f"真实失败用例 id：{head}{more}", ""]
        if groups:
            lines += ["失败用例按 dtype 分组：", ""]
            lines += [f"- {dtype}：{count} 条" for dtype, count in groups.most_common()]
            lines += ["", "分组只按用例规格特征划分，成因需算子作者定位，本报告不归因。", ""]
    else:
        lines += ["精度轮未执行。", ""]

    lines += ["## 性能", "", f"形态：{PERF_LABEL.get(perf_kind, perf_kind)}",
              f"状态：{perf_status}", ""]
    if performance:
        if accuracy and not accuracy.get("passed"):
            lines += [
                "精度未通过，本轮性能**不做评级**。下面是已采集到的耗时，",
                "只作缺陷修复后的对照参考，不构成达标或劣化结论。",
                "",
            ]
        lines += _perf_lines(facts, performance, baseline, cases)
        lines.append("")

    if verdict["inferred_facts"]:
        lines += [
            "## 推断项",
            "",
            "下列事实没有任务书或工程文档依据，是从基线接口推断的，结论受其影响：",
            "",
        ]
        lines += [f"- {name}" for name in verdict["inferred_facts"]]
        lines.append("")

    lines += [
        "## 证据链",
        "",
        f"- 构建日志：`evidence/build.log`",
        f"- 安装日志：`evidence/install.log`",
        f"- 精度报告：`{accuracy.get('report', '未产出')}`",
        f"- 性能报告：`{performance.get('report', '未产出')}`",
        "",
    ]

    Path(args.report).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"总结论    {overall}")
    if accuracy:
        print(f"精度      {accuracy.get('matched')}/{accuracy.get('total')} "
              f"通过率 {accuracy.get('pass_rate')}%")
    print(f"性能      {perf_status}")
    print(f"写入      {args.out}、{args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
