#!/usr/bin/env python3
"""把确定性 JSON 产物渲染为中文 Markdown；只展示，不重新裁决。"""

from __future__ import annotations

import argparse
import json
import os


def _load(root, name):
    with open(os.path.join(root, name), encoding="utf-8") as src:
        return json.load(src)


def _cell(value):
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _pct(value):
    return "—" if value is None else f"{float(value) * 100:.2f}%"


def _gap_line(gap):
    if not isinstance(gap, dict):
        return f"- {_cell(gap)}"
    title = gap.get("issue", gap.get("kind"))
    detail = gap.get("impact", gap.get("reason"))
    line = f"- `{_cell(title)}`：{_cell(detail)}"
    if gap.get("pr_fact") is not None:
        line += f"（PR 事实：{_cell(gap['pr_fact'])}）"
    shown = {"issue", "kind", "impact", "reason", "pr_fact"}
    extra = {key: value for key, value in gap.items()
             if key not in shown and value is not None}
    if extra:
        line += "；补充：" + _cell(json.dumps(
            extra, ensure_ascii=False, sort_keys=True))
    return line


def _gap_items(value):
    if value is None or value == []:
        return []
    return value if isinstance(value, list) else [value]


def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as out:
        out.write(text)
    os.replace(tmp, path)


def _precision_failure_detail(failed):
    lines = [
        "# 精度失败明细",
        "",
        "> 本文件由 `verdict.json` 确定性渲染，只展示既有裁决，不重新判断 pass/fail。",
        "",
        f"- 失败总数：**{len(failed)}**",
        "- 返回主报告：[验收报告.md](验收报告.md)",
        "- 审核主入口：`./repro/audit_case.sh <序号>`（一次显示接入、输入、接口、差异和阈值）",
        "",
        "| 序号 | case_id | 判据 | 查看用例 | 重放复现 |",
        "|---:|---|---|---|---|",
    ]
    for index, row in enumerate(failed, 1):
        case_id = row.get("case_id")
        lines.append(
            f"| {index} | `{_cell(case_id)}` | {_cell(row.get('判据'))} | "
            f"`./repro/review.sh show {index}` | `./repro/audit_case.sh {index}` |")
    lines += [
        "",
        "也可按 case_id 操作：",
        "",
        "- `./repro/show_case.sh <case_id>`：查看冻结输入、golden、policy 和原始 metrics。",
        "- `./repro/run_case.sh <case_id>`：在原验收环境重放。",
        "",
    ]
    return "\n".join(lines)


def _performance_failure_detail(non_passing, caseset):
    case_by_id = {
        case.get("id"): case
        for case in (caseset.get("cases") or [])
        if isinstance(case, dict) and isinstance(case.get("id"), str)
    }
    lines = [
        "# 性能失败明细",
        "",
        "> 本文件由 `perf_report.json` 确定性渲染；blocked、exception 等未通过状态按原字段展示，不自行归因为 DUT 失败。",
        "",
        f"- 未通过总数：**{len(non_passing)}**",
        "- 返回主报告：[验收报告.md](验收报告.md)",
        "",
        "| 序号 | case_id | outcome | dtype | 输入 shape | shape 类别 | NPU us | baseline us | speedup | 阈值 |",
        "|---:|---|---|---|---|---|---:|---:|---:|---:|",
    ]
    for index, row in enumerate(non_passing, 1):
        custom = row.get("custom") or {}
        baseline = row.get("baseline") or {}
        ratio = row.get("ratio", row.get("speedup"))
        shapes = [
            inp.get("shape") for inp in (row.get("inputs") or [])
            if isinstance(inp, dict)
        ]
        lines.append(
            f"| {index} | `{_cell(row.get('case_id'))}` | `{_cell(row.get('outcome'))}` | "
            f"`{_cell(row.get('dtype'))}` | `{_cell(shapes)}` | "
            f"`{_cell(row.get('shape_class'))}` | "
            f"{_cell(custom.get('us', row.get('npu_us')))} | "
            f"{_cell(baseline.get('us', row.get('baseline_us')))} | "
            f"{_cell(ratio)} | {_cell(row.get('target_ratio'))} |")
    lines += ["", "## 逐 case 审核", ""]
    for index, row in enumerate(non_passing, 1):
        case_id = row.get("case_id")
        case = case_by_id.get(case_id) or {}
        custom = row.get("custom") or {}
        baseline = row.get("baseline") or {}
        call = case.get("aclnn_call") or case.get("invocation") or {}
        symbol = call.get("symbol") if isinstance(call, dict) else None
        repro = row.get("repro")
        if isinstance(repro, dict):
            repro = repro.get("command") or repro.get("script")
        repro_text = (
            f"`{_cell(repro)}`"
            if isinstance(repro, str) and repro.strip()
            else "**缺单 case 性能重放能力（本轮产物未记录可执行入口）**"
        )
        lines += [
            f"### {index}. `{_cell(case_id)}`",
            "",
            f"- 结果类别：`{_cell(row.get('outcome'))}`",
            f"- 输入：`{_cell(json.dumps(row.get('inputs') or [], ensure_ascii=False, sort_keys=True))}`",
            f"- 属性：`{_cell(json.dumps(case.get('attrs') or {}, ensure_ascii=False, sort_keys=True))}`",
            f"- DUT 接口：`{_cell(symbol or call or '未记录')}`",
            f"- custom：behavior=`{_cell(custom.get('behavior'))}`，"
            f"scope=`{_cell(custom.get('scope'))}`，us=`{_cell(custom.get('us'))}`",
            f"- baseline：behavior=`{_cell(baseline.get('behavior'))}`，"
            f"scope=`{_cell(baseline.get('scope'))}`，us=`{_cell(baseline.get('us'))}`",
            f"- 实测 speedup：`{_cell(row.get('ratio', row.get('speedup')))}`；"
            f"要求阈值：`{_cell(row.get('target_ratio'))}`",
            f"- 确定性原因：{_cell(row.get('reason') or row.get('note'))}",
            f"- 单 case 性能重放：{repro_text}",
            "",
        ]
    lines += [
        "复核时以同目录的 `perf_report.json`、`evidence.json` 和原始 profiler 证据为准；"
        "本文件不把缺 baseline、scope 不可比或环境异常静默改判为 DUT 失败。",
        "",
    ]
    return "\n".join(lines)


def render(report_root):
    report_root = os.path.realpath(report_root)
    acceptance = _load(report_root, "acceptance.json")
    verdict = _load(report_root, "verdict.json")
    perf = _load(report_root, "perf_report.json")
    evidence = _load(report_root, "evidence.json")
    caseset = _load(report_root, "caseset.json")

    op = acceptance.get("op") or verdict.get("op") or caseset.get("op") or "?"
    accuracy = verdict.get("accuracy_summary") or {}
    counts = (verdict.get("overall") or {}).get("counts") or {}
    receipt = evidence.get("cpp_extension_receipt") or {}
    runtime = receipt.get("runtime") or {}
    vendor = receipt.get("vendor") or {}
    build_receipt = vendor.get("build_receipt") or {}
    source = build_receipt.get("source") or {}

    lines = [
        f"# {op} 算子验收报告",
        "",
        "> 本报告由确定性 JSON 产物渲染，只展示既有裁决，不重新判断 pass/fail。",
        "",
        "## 验收结论",
        "",
        "| 项目 | 结果 |",
        "|---|---|",
        f"| 最终裁决 | `{_cell(acceptance.get('overall'))}` |",
        f"| 状态 | `{_cell(acceptance.get('state'))}` |",
        f"| 精度裁决 | `{_cell(acceptance.get('precision_verdict'))}` |",
        f"| 性能状态 | `{_cell(acceptance.get('perf_status'))}` |",
        f"| 验收门 | `{'PASSED' if (acceptance.get('gate') or {}).get('passed') else 'FAILED'}` |",
        f"| runner mode | `{_cell(acceptance.get('repo_mode'))}` |",
        "",
        "## 审核员快速操作",
        "",
        "进入本报告目录后：",
        "",
        "```bash",
        "./repro/audit_case.sh 1",
        "```",
        "",
        "`audit_case.sh` 直接完成单 case 重放，并按五段展示 Torch 接入、输入、接口、差异阈值和结论。",
        "",
        "## 被测物与运行环境",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        f"| 源码仓 | `{_cell(source.get('repo'))}` |",
        f"| PR head | `{_cell(source.get('pr_head_sha'))}` |",
        f"| vendor ELF SHA256 | `{_cell(vendor.get('library_sha256'))}` |",
        f"| Extension ELF SHA256 | `{_cell((receipt.get('artifact') or {}).get('sha256'))}` |",
        f"| SoC | `{_cell(runtime.get('soc'))}` |",
        f"| CANN | `{_cell(runtime.get('cann_version'))}` |",
        f"| torch | `{_cell(runtime.get('torch_version'))}` |",
        f"| torch_npu | `{_cell(runtime.get('torch_npu_version'))}` |",
        "",
        "## 精度汇总",
        "",
        f"- 合计：{accuracy.get('passed', counts.get('total', 0) - counts.get('fail', 0))}/"
        f"{accuracy.get('total', counts.get('total', 0))} 通过；"
        f"失败 {accuracy.get('failed', counts.get('fail', 0))}；"
        f"通过率 {_pct(accuracy.get('overall_pass_rate'))}。",
        f"- 精度标准：`{_cell(verdict.get('standard'))}`。",
        "",
        "| dtype | 总数 | 通过 | 失败 | uncertain | 通过率 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in accuracy.get("by_dtype") or []:
        lines.append(
            f"| `{_cell(row.get('dtype'))}` | {row.get('count', 0)} | "
            f"{row.get('passed', 0)} | {row.get('failed', 0)} | "
            f"{row.get('uncertain', 0)} | {_pct(row.get('pass_rate'))} |")

    failed = [
        row for row in (verdict.get("per_case") or [])
        if row.get("精度") != "pass"
    ]
    lines += ["", "## 精度失败明细", ""]
    if failed:
        lines += [
            f"共 **{len(failed)}** 条，逐项判据和复现入口见 "
            "[精度失败明细.md](精度失败明细.md)。",
            "",
            "快速复核：`./repro/audit_case.sh 1`。",
        ]
    else:
        lines.append("无精度失败。")

    ps = perf.get("summary") or {}
    lines += [
        "",
        "## 性能汇总",
        "",
        f"- 状态：`{_cell(ps.get('status'))}`。",
        f"- 计划 case：{ps.get('planned_cases', ps.get('perf_cases', 0))}；"
        f"实际采集：{ps.get('perf_cases', 0)}；有效评分：{ps.get('cases_scored', 0)}；"
        f"达标：{ps.get('达标', 0)}。",
        "",
        "| shape 类别 | 计划 | 实采 | 有效评分 | 达标 | NPU us | baseline us | speedup |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in perf.get("by_shape_class") or []:
        lines.append(
            f"| `{_cell(row.get('class'))}` | {row.get('planned_cases', 0)} | "
            f"{row.get('cases', 0)} | {row.get('cases_scored', 0)} | "
            f"{row.get('达标', 0)} | {_cell(row.get('npu_us'))} | "
            f"{_cell(row.get('baseline_us'))} | {_cell(row.get('speedup'))} |")
    if ps.get("status") == "skipped_precision_gate":
        lines += ["", "> 精度门未通过，性能未执行；本报告不提供虚构加速比。"]
    perf_non_passing = perf.get("non_passing_cases") or []
    if perf_non_passing:
        lines += [
            "",
            f"性能未通过共 **{len(perf_non_passing)}** 条，逐项状态与原始原因见 "
            "[性能失败明细.md](性能失败明细.md)。",
        ]
    elif ps.get("perf_cases", 0):
        lines += ["", "无性能未通过 case。"]

    gaps = _gap_items(caseset.get("task_pr_gaps"))
    lines += ["", "## 任务书与 PR 差额", ""]
    if gaps:
        for gap in gaps:
            lines.append(_gap_line(gap))
    else:
        lines.append("- 无已记录差额。")

    lines += [
        "",
        "## 证据与人工复核入口",
        "",
        "- `acceptance.json`：最终确定性裁决。",
        "- `verdict.json`：逐 case 精度裁决与 dtype 汇总。",
        "- `精度失败明细.md`：存在精度失败时生成的逐项复现索引。",
        "- `evidence.json`：逐 case 实测 metrics 和构建/加载收据。",
        "- `perf_report.json`：性能计划、采集和大小 shape 汇总。",
        "- `性能失败明细.md`：存在性能未通过 case 时生成的逐项状态索引。",
        "- `caseset.json`：完整用例契约。",
        "- `repro/index.tsv`：全部 case 与启动脚本索引。",
        "- `repro/failed.tsv`：带编号的失败 case 清单。",
        "- `repro/review.sh`：审核员 list/show/run 快捷入口。",
        "- `repro/audit_case.sh`：审核员单 case 直接复现主入口。",
        "- `repro/show_case.sh`：查看具体用例内容。",
        "- `repro/run_case.sh`：重放指定用例。",
        "",
    ]
    return "\n".join(lines)


def write_report(report_root, filename="验收报告.md"):
    report_root = os.path.realpath(report_root)
    text = render(report_root)
    path = os.path.join(report_root, filename)
    _atomic_write(path, text)

    verdict = _load(report_root, "verdict.json")
    failed = [
        row for row in (verdict.get("per_case") or [])
        if row.get("精度") != "pass"
    ]
    precision_path = os.path.join(report_root, "精度失败明细.md")
    if failed:
        _atomic_write(precision_path, _precision_failure_detail(failed))
    elif os.path.exists(precision_path):
        os.unlink(precision_path)

    perf = _load(report_root, "perf_report.json")
    perf_non_passing = perf.get("non_passing_cases") or []
    performance_path = os.path.join(report_root, "性能失败明细.md")
    if perf_non_passing:
        _atomic_write(
            performance_path,
            _performance_failure_detail(
                perf_non_passing, _load(report_root, "caseset.json")))
    elif os.path.exists(performance_path):
        os.unlink(performance_path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="从确定性验收 JSON 渲染中文 Markdown 报告")
    parser.add_argument("report_root")
    parser.add_argument("--filename", default="验收报告.md")
    args = parser.parse_args(argv)
    print(write_report(args.report_root, args.filename))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
