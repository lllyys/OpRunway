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
        "./repro/review.sh list",
        "./repro/review.sh show 1",
        "./repro/review.sh run 1",
        "```",
        "",
        "`show` 只读展示；`run` 会重放并把“原 FAIL 再次失败”解释为稳定复现，审核员无需记底层退出码语义。",
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
    lines += [
        "",
        "## 精度失败明细",
        "",
        f"共 {len(failed)} 条。可先运行 `./repro/show_case.sh <case_id>` 查看冻结输入、"
        "golden、policy 与原始 metrics，再运行 `./repro/run_case.sh <case_id>` 重放。",
        "",
        "| case_id | 判据 | 查看 | 重放 |",
        "|---|---|---|---|",
    ]
    for row in failed:
        case_id = row.get("case_id")
        lines.append(
            f"| `{_cell(case_id)}` | {_cell(row.get('判据'))} | "
            f"`./repro/show_case.sh {case_id}` | `./repro/run_case.sh {case_id}` |")

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

    gaps = caseset.get("task_pr_gaps") or []
    lines += ["", "## 任务书与 PR 差额", ""]
    if gaps:
        for gap in gaps:
            lines.append(
                f"- `{_cell(gap.get('issue'))}`：{_cell(gap.get('impact'))} "
                f"（PR 事实：{_cell(gap.get('pr_fact'))}）")
    else:
        lines.append("- 无已记录差额。")

    lines += [
        "",
        "## 证据与人工复核入口",
        "",
        "- `acceptance.json`：最终确定性裁决。",
        "- `verdict.json`：逐 case 精度裁决与 dtype 汇总。",
        "- `evidence.json`：逐 case 实测 metrics 和构建/加载收据。",
        "- `perf_report.json`：性能计划、采集和大小 shape 汇总。",
        "- `caseset.json`：完整用例契约。",
        "- `repro/index.tsv`：全部 case 与启动脚本索引。",
        "- `repro/failed.tsv`：带编号的失败 case 清单。",
        "- `repro/review.sh`：审核员 list/show/run 快捷入口。",
        "- `repro/show_case.sh`：查看具体用例内容。",
        "- `repro/run_case.sh`：重放指定用例。",
        "",
    ]
    return "\n".join(lines)


def write_report(report_root, filename="验收报告.md"):
    report_root = os.path.realpath(report_root)
    text = render(report_root)
    path = os.path.join(report_root, filename)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as out:
        out.write(text)
    os.replace(tmp, path)
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
