"""S2 出口：适配器该不该写、写没写，在冻结之前判定完。

`align_signatures.py` 在 S2 一开始就产出了判定所需的全部输入，但它给的是
**条件性预警**——某个可空指针「若有用例把它置空」就需要 typed 空指针适配器。
条件成不成立，签名里看不出来。

真机实测（roll，2026-08-15）：agent 判断「S3 冒烟再说」，于是 S3 才发现、
才改 YAML，而改 YAML 要重跑 `atk case`，正撞上「S2 后冻结」的纪律。

两件事都不需要问 agent：

- 预警条件成不成立：用例集就在手上，逐条核实即可
- 实际绑了哪个执行器：ATK 从用例 JSON 的 api_type / aclnn_api_type 取
  （`atk/tasks/backends/backend.py:61`、`atk/tasks/backends/pyaclnn_backend.py:150`），
  默认值 function / aclnn_function（`atk/configs/case_config.py:90-91`）

退出码：0 判定通过；2 该写没写或无法判定；3 输入读不出来。
"""

import argparse
import json
import sys

from _case_utils import iter_cases, iter_input_specs, load_json
import _stage_card

# ATK 的 CaseConfig 默认值。用例里没写这个键不等于「没绑」，
# 等于绑了 ATK 内置的默认执行器。
DEFAULT_WIRING = {"api_type": "function", "aclnn_api_type": "aclnn_function"}

# ATK 把「这个输入这条用例不传值」编码成这几个令牌。
# "null" / ["null"] 见 `atk/case_generator/generator/base_generator.py:87-93`，
# "default" 见 L0 的 default_token。
NULL_TOKENS = ("null", "default")

SIDES = (("基线", "api_type", "baseline_adapter"),
         ("aclnn", "aclnn_api_type", "aclnn_adapter"))


def wiring(cases, key):
    """用例集里这一侧实际绑的执行器名集合。"""
    return {case.get(key) or DEFAULT_WIRING[key] for case in cases}


def nulled_parameters(cases):
    """哪些输入名至少被一条用例置空，返回 {名字: [用例号, ...]}。"""
    hits = {}
    for case in cases:
        for spec in iter_input_specs(case):
            value = spec.get("range_values")
            if isinstance(value, list):
                value = value[0] if len(value) == 1 else None
            if value in NULL_TOKENS:
                hits.setdefault(spec.get("name"), []).append(case.get("id"))
    return hits


def judge(alignment, cases):
    """返回 (报告, 问题列表)。问题列表非空即判不过。"""
    report = {"total_cases": len(cases), "verdicts": {}, "reviews": []}
    problems = []

    for label, key, block_name in SIDES:
        block = alignment.get(block_name) or {}
        bound = wiring(cases, key)
        adapted = bound - {DEFAULT_WIRING[key]}
        report["verdicts"][key] = {
            "required": bool(block.get("required")),
            "determinable": bool(block.get("determinable", True)),
            "bound": sorted(bound),
            "adapted": bool(adapted),
        }
        if block.get("required") and not adapted:
            reasons = "；".join(block.get("reasons") or []) or "见对齐报告"
            problems.append(
                f"{label}侧适配器判定为必需，但用例集里 {key} 还是默认 "
                f"{DEFAULT_WIRING[key]}：{reasons}")
        if not block.get("determinable", True):
            problems.append(
                f"{label}侧适配器 determinable=false：基线形参名没取全，"
                "「没找到理由」不等于「不需要适配器」；"
                "先按 semantic_review 补齐形参名，再冻结")

    nulled = nulled_parameters(cases)
    aclnn_adapted = bool(wiring(cases, "aclnn_api_type")
                         - {DEFAULT_WIRING["aclnn_api_type"]})
    for item in alignment.get("semantic_review") or []:
        name = item.get("parameter")
        if not name:
            # 重载数、变长签名这类预警脚本判不了，硬拦会把每一轮都堵死。
            # 原样列出，交给 S2 的人工过目，但不构成门禁失败。
            report["reviews"].append({
                "parameter": None, "issue": item.get("issue"),
                "triggered": None, "requires_manual_review": True})
            continue
        ids = nulled.get(name) or []
        report["reviews"].append({
            "parameter": name, "issue": item.get("issue"),
            "triggered": bool(ids),
            "triggered_case_count": len(ids),
            "evidence_case_ids": ids[:12],
            "requires_manual_review": False,
        })
        if ids and not aclnn_adapted:
            problems.append(
                f"{name} 有 {len(ids)} 条用例把它置空（例如用例 {ids[:5]}），"
                "默认路径会绑成 c_void_p 而不是 typed 空指针；"
                "现在 aclnn_api_type 仍是默认 "
                f"{DEFAULT_WIRING['aclnn_api_type']}")
    return report, problems


def main():
    parser = argparse.ArgumentParser(
        description="S2 出口：适配器该不该写、写没写")
    parser.add_argument("-j", "--case-json", required=True,
                        help="本分面的用例 JSON，执行器绑定以它为准")
    parser.add_argument("-a", "--alignment", required=True,
                        help="align_signatures.py 产出的 signature_alignment.json")
    parser.add_argument("-o", "--output", required=True,
                        help="判定报告落盘路径，如 evidence/adapter_binding.json")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    try:
        alignment = load_json(args.alignment)
        cases = list(iter_cases(load_json(args.case_json)))
    except (OSError, ValueError) as exc:
        print(f"读不出输入：{exc}", file=sys.stderr)
        return 3
    if not cases:
        print("用例集为空，无从判定适配器。", file=sys.stderr)
        return 3

    report, problems = judge(alignment, cases)
    report["case_json"] = args.case_json
    report["alignment"] = args.alignment
    report["problems"] = problems
    with open(args.output, "w", encoding="utf-8") as sink:
        json.dump(report, sink, ensure_ascii=False, indent=2)

    for key, verdict in report["verdicts"].items():
        state = "需要" if verdict["required"] else "不需要"
        print(f"{key}：适配器{state}，用例集实际绑 {verdict['bound']}")
    for review in report["reviews"]:
        if review["requires_manual_review"]:
            print(f"  ? {review['issue']}（脚本判不了，人工过目）")
        else:
            mark = "触发" if review["triggered"] else "已判定不触发"
            print(f"  - {review['parameter']}：{mark}"
                  f"（{review['triggered_case_count']} 条用例）")
    print(f"判定报告写入 {args.output}")

    if not problems:
        return 0
    print(f"\n✗ {len(problems)} 处适配器判定不通过：", file=sys.stderr)
    for index, message in enumerate(problems, 1):
        print(f"  {index}. {message}", file=sys.stderr)
    print("  → 适配器接线字段在用例 JSON 里，S2 冻结之后改它要重跑 atk case。\n"
          "     现在就写执行器并重新生成；已经冻结的用受控通道 "
          "rewire_adapter.py。", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
