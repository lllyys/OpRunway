"""将 ATK xlsx 报告归一化为 JSON。

输入：xlsx 报告和可选用例集。
输出：accuracy 或 performance results。
退出码：0 完成；2 缺少裁决证据。
"""

import argparse
import json
import sys

NON_FINITE_MARKERS = ("nan", "inf")


from _case_utils import file_sha256, iter_cases, load_json, tensor_inputs
from _policy import PolicyError, load_policy
from _report_reader import (
    ACC_DETAIL,
    ACC_PASS,
    CASE_ID_ALIASES,
    PERF_FLUCT,
    PERF_PASS,
    PERF_RATIO,
    PERF_TIME,
    RANGE_COL,
    REASON_COL,
    RESULT_COL,
    TRUE_WORDS,
    backends_of,
    blank,
    cell,
    detect_task,
    find_col,
    node_cols,
    nodes_from_header,
    pick_data_col,
    parse_outputs,
    read_sheet,
    read_workbook,
    to_float,
)
import _stage_card


def row_verdict(row, pass_cols):
    """一行的精度判定。返回 (passed, 冲突证据)。

    ATK 把常规精度结果挂在基准节点列（cpu_0_精度通过），把"报错与预期一致"的
    error-match 用例挂在待验收算子节点列（pyaclnn_0_精度通过），同一行只有一个单元
    有值。只读其中一列会漏掉另一类用例，通过率跟 ATK 自报的对不上。

    但不能退化成"任一列为真即通过"：多节点拓扑下那会把"一个节点过、另一个节点
    挂"判成通过，是假通过。所以只认非空单元；它们互相矛盾时判不通过并记冲突，
    交给人裁决，而不是替报告发明一个结论。
    """
    filled = [(column, cell(row, index)) for column, index in pass_cols
              if not blank(cell(row, index))]
    if not filled:
        return False, None
    verdicts = {str(value).strip().lower() in TRUE_WORDS for _, value in filled}
    if len(verdicts) > 1:
        return False, {column: value for column, value in filled}
    return verdicts.pop(), None


def parse_accuracy(header, rows, partition_map):
    idx_id = find_col(header, *CASE_ID_ALIASES)
    pass_cols = [(column, index) for index, column in enumerate(header)
                 if column == ACC_PASS or column.endswith("_" + ACC_PASS)]
    idx_detail = find_col(header, ACC_DETAIL)
    idx_result = find_col(header, RESULT_COL)
    idx_reason = find_col(header, REASON_COL)
    idx_range = find_col(header, RANGE_COL)

    cases = []
    for row in rows:
        raw_id = cell(row, idx_id)
        if raw_id is None:
            continue
        passed, conflict = row_verdict(row, pass_cols)
        entry = {
            "id": str(raw_id).strip(),
            "partition": partition_map.get(str(raw_id).strip(), "must"),
            "passed": passed,
            "outputs": parse_outputs(cell(row, idx_detail)),
        }
        if conflict:
            entry["verdict_conflict"] = conflict
        range_text = str(cell(row, idx_range) or "").lower()
        if any(marker in range_text for marker in NON_FINITE_MARKERS):
            entry["non_finite_input"] = True
        if not passed:  # 详情只在失败时留，否则报告体积和噪声都翻几倍
            for key, i in (("detail", idx_detail), ("run_result", idx_result),
                           ("fail_reason", idx_reason)):
                value = cell(row, i)
                if not blank(value):
                    entry[key] = value
        cases.append(entry)
    return cases, {"verdict_columns": [column for column, _ in pass_cols]}


def parse_performance(header, rows, partition_map):
    idx_id = find_col(header, *CASE_ID_ALIASES)
    time_cols = node_cols(header, PERF_TIME)
    fluct_cols = node_cols(header, PERF_FLUCT)
    idx_ratio, ratio_col = pick_data_col(header, rows, PERF_RATIO)
    idx_pass, pass_col = pick_data_col(header, rows, PERF_PASS)
    idx_reason = find_col(header, REASON_COL)

    cases = []
    for row in rows:
        raw_id = cell(row, idx_id)
        if raw_id is None:
            continue
        entry = {
            "id": str(raw_id).strip(),
            "partition": partition_map.get(str(raw_id).strip(), "must"),
            "device_us": {n: to_float(cell(row, i)) for n, i in time_cols.items()
                          if not blank(cell(row, i))},
        }
        ratio = to_float(cell(row, idx_ratio))
        if ratio is not None:
            entry["ratio"] = ratio
        if idx_pass is not None and not blank(cell(row, idx_pass)):
            entry["passed"] = str(cell(row, idx_pass)).strip().lower() in TRUE_WORDS
        # 波动结果照实记录，不据此剔除任何用例——是否可信交给人看
        fluct = {n: str(cell(row, i)).strip().lower() in TRUE_WORDS
                 for n, i in fluct_cols.items() if not blank(cell(row, i))}
        if fluct:
            entry["fluctuation_ok"] = fluct
        if entry.get("passed") is False and not blank(cell(row, idx_reason)):
            entry["fail_reason"] = cell(row, idx_reason)
        cases.append(entry)
    return cases, {"ratio_column": ratio_col, "verdict_column": pass_col}


# summary sheet 里只留下面这些列，其余（内存、e2e、cube 等）不进结论，避免噪声
SUMMARY_KEEP = (
    "总用例数", "执行成功用例个数", "执行失败用例个数", "通过用例个数",
    "错误信息匹配用例个数", "通过率", "精度是否达标",
    "device性能通过率", "平均device性能比", "device性能是否达标",
)


def parse_summary(wb):
    """summary 每个节点一行，首列是节点名，其余列不带前缀。"""
    header, rows = read_sheet(wb, "summary")
    out = {}
    for row in rows:
        if row and row[0] is not None:
            kept = {header[i]: row[i] for i in range(min(len(header), len(row)))
                    if header[i] in SUMMARY_KEEP and not blank(row[i])}
            if kept:
                out[str(row[0]).strip()] = kept
    return out


def totals_of(task, cases, excluded_count=0, reported_count=None):
    total = len(cases)
    if task == "accuracy":
        passed = sum(1 for c in cases if c.get("passed"))
        out = {"cases": total, "passed": passed, "failed": total - passed}
        out["pass_rate"] = round(passed / total, 4) if total else 0.0
        if reported_count is not None:
            out["reported_cases"] = reported_count
        if excluded_count:
            out["excluded_cases"] = excluded_count
        non_finite = sum(1 for case in cases if case.get("non_finite_input"))
        if non_finite:
            out["non_finite_inputs"] = non_finite
        return out
    judged = [c for c in cases if "passed" in c]
    passed = sum(1 for c in cases if c.get("passed"))
    ratios = [c["ratio"] for c in cases if c.get("ratio") is not None]
    unstable = sum(1 for c in cases
                   if not all((c.get("fluctuation_ok") or {}).values()))
    out = {"cases": total, "judged": len(judged),
           "passed": passed, "failed": len(judged) - passed}
    if ratios:
        out["avg_ratio"] = round(sum(ratios) / len(ratios), 4)
        out["min_ratio"] = min(ratios)
    if unstable:
        out["fluctuation_failed"] = unstable
    return out


def load_cases(case_file):
    return [case for case in iter_cases(load_json(case_file)) if isinstance(case, dict)]


def load_exclusions(path, raw_cases, reported_cases, partition_map, allowed_causes):
    """读取人工确认的无效用例，并保留对应报告错误。"""
    if not path:
        return [], reported_cases

    doc = load_json(path)
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise ValueError("excluded_cases.json 的 schema_version 必须为 1。")
    items = doc.get("cases")
    if not isinstance(items, list):
        raise ValueError("excluded_cases.json 的 cases 必须是列表。")

    known_ids = {str(case.get("id")) for case in raw_cases}
    reported_by_id = {str(case.get("id")): case for case in reported_cases}
    seen = set()
    exclusions = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("excluded_cases.json 的每条记录必须是对象。")
        case_id = str(item.get("id", "")).strip()
        if not case_id or case_id in seen:
            raise ValueError(f"无效或重复的剔除用例号：{case_id!r}。")
        if case_id not in known_ids:
            raise ValueError(f"剔除用例 {case_id!r} 不在本轮用例 JSON 中。")
        for key in ("stage", "reason", "evidence"):
            if not str(item.get(key, "")).strip():
                raise ValueError(f"剔除用例 {case_id!r} 缺少 {key}。")
        if item.get("cause") not in allowed_causes:
            raise ValueError(
                f"剔除用例 {case_id!r} 的 cause 必须为 "
                f"{sorted(allowed_causes)} 之一。"
            )

        reported = reported_by_id.get(case_id)
        if reported and reported.get("passed"):
            raise ValueError(f"剔除用例 {case_id!r} 在 ATK 报告中已通过。")

        entry = dict(item)
        entry["id"] = case_id
        entry["partition"] = partition_map.get(case_id, "must")
        entry["reported"] = reported is not None
        if reported:
            for key in ("fail_reason", "run_result", "detail"):
                if key in reported:
                    entry[key] = reported[key]
        exclusions.append(entry)
        seen.add(case_id)

    active = [case for case in reported_cases if str(case.get("id")) not in seen]
    return exclusions, active


def summarize_standard_acc(cases):
    values = []
    for case in cases:
        standard = case.get("standard")
        value = standard.get("acc") if isinstance(standard, dict) else None
        if value is not None:
            values.append(value)
    unique = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return {
        "total_cases": len(cases),
        "present_cases": len(values),
        "consistent": len(unique) == 1 and len(values) == len(cases),
        "value": unique[0] if len(unique) == 1 else None,
        "values": unique,
    }


# 归因仅消费报告字段和报错文本。

ACCURACY_GAP = ("accuracy_gap", "developer", "执行成功但精度比对未通过")
UNKNOWN_CAUSE = ("unknown", "unknown", "报错信息不足")


def presume_cause(entry, policy):
    """按报错文本推定归属。返回 (cause, attribution, hint)。"""
    text = " ".join(str(entry.get(k, "")) for k in
                    ("fail_reason", "run_result", "detail")).lower()
    for rule in policy["failure_attribution"]:
        if any(marker in text for marker in rule["markers"]):
            return rule["id"], rule["attribution"], rule["message"]
    # 跑完了、有逐输出结果、只是没通过 —— 那就是纯精度问题
    if entry.get("outputs"):
        return ACCURACY_GAP
    return UNKNOWN_CAUSE


def broadcast_form(shapes):
    """计算右对齐广播形态和输出元素数。"""
    if len(shapes) < 2:
        return "无", None
    rank = max(len(s) for s in shapes)
    aligned = [[1] * (rank - len(s)) + list(s) for s in shapes]
    out_shape = [max(dim[i] for dim in aligned) for i in range(rank)]
    expanded = sum(1 for dim in aligned if dim != out_shape)
    numel = 1
    for d in out_shape:
        numel *= d
    return {0: "无", 1: "单边"}.get(expanded, "双边"), numel


def features_of(case):
    inputs = tensor_inputs(case)
    if not inputs:
        return {}
    shapes = [i["shape"] for i in inputs]
    dtypes = [str(i.get("dtype")) for i in inputs]
    form, out_numel = broadcast_form(shapes)
    feat = {
        "dtypes": dtypes,
        "shapes": shapes,
        "max_rank": max(len(s) for s in shapes),
        "broadcast": form,
    }
    if out_numel is not None:
        feat["out_numel"] = out_numel
    return feat


def feature_signature(feat):
    """把特征压成一行可分组的短签名，例如 `fp64 rank8 广播双边`。"""
    if not feat:
        return "无输入特征"
    dtypes = "/".join(sorted(set(feat["dtypes"])))
    return f"{dtypes} rank{feat['max_rank']} 广播{feat['broadcast']}"


def group_failures(cases, policy):
    """按推定归属分组，每组给出条数、用例号与数据特征分布。"""
    groups = {}
    for case in cases:
        if case.get("passed") or "cause" not in case:
            continue
        g = groups.setdefault(case["cause"], {
            "cause": case["cause"],
            "attribution": case["attribution"],
            "hint": case["hint"],
            "count": 0, "ids": [], "features": {},
        })
        g["count"] += 1
        g["ids"].append(case["id"])
        sig = feature_signature(case.get("features"))
        g["features"][sig] = g["features"].get(sig, 0) + 1
    order = [rule["id"] for rule in policy["failure_attribution"]]
    order.extend([ACCURACY_GAP[0], UNKNOWN_CAUSE[0]])
    return sorted(groups.values(),
                  key=lambda g: (order.index(g["cause"]) if g["cause"] in order else 99))


def main():
    parser = argparse.ArgumentParser(description="解析 ATK xlsx 报告（精度 / 性能）")
    parser.add_argument("-i", "--input", required=True, help="xlsx 报告路径")
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("-p", "--partition-map", help="用例编号 → must/extra 的映射 JSON")
    parser.add_argument("-c", "--case-file",
                        help="用例 JSON。读出 standard.acc，并给失败用例补数据特征与归属推定")
    parser.add_argument(
        "-x",
        "--excluded-cases",
        help="人工确认的无效用例记录。只影响精度分母，不修改原始用例集",
    )
    args = parser.parse_args()
    _stage_card.announce(__file__)

    try:
        policy = load_policy()
    except PolicyError as error:
        print(error, file=sys.stderr)
        return 2

    partition_map = {}
    if args.partition_map:
        with open(args.partition_map, encoding="utf-8") as f:
            partition_map = {str(k): v for k, v in json.load(f).items()}

    wb = read_workbook(args.input)
    header, rows = read_sheet(wb, "statistic")
    if not header:
        raise SystemExit("报告里没有 statistic sheet，无法解析逐用例结果。")

    task = detect_task(header, rows)
    if task is None:
        raise SystemExit(
            "statistic 表里既找不到有数据的精度通过列，也找不到有数据的 device 性能列，"
            "认不出这是什么任务。\n"
            "  → 确认这份 xlsx 是 --task accuracy 或 --task performance_device 的产物，"
            "且用例确实跑出了结果（两族列都存在但整列为空时也会走到这里）。"
        )

    if task == "accuracy" and not args.case_file:
        print("[REPORT_CASE_FILE] 精度报告必须提供 --case-file。", file=sys.stderr)
        return 2

    if task == "accuracy":
        reported_cases, meta = parse_accuracy(header, rows, partition_map)
    else:
        reported_cases, meta = parse_performance(header, rows, partition_map)

    if args.excluded_cases and task != "accuracy":
        print("[EXCLUDED_CASES_TASK] 无效用例剔除只用于精度报告。", file=sys.stderr)
        return 2

    raw_cases = load_cases(args.case_file) if args.case_file else []
    try:
        excluded_cases, cases = load_exclusions(
            args.excluded_cases,
            raw_cases,
            reported_cases,
            partition_map,
            set(policy["case_exclusions"]["allowed_causes"]),
        )
    except ValueError as error:
        print(f"[EXCLUDED_CASES] {error}", file=sys.stderr)
        return 2

    result = {
        "task": task,
        "source": args.input,
        "source_sha256": file_sha256(args.input),
        "totals": totals_of(
            task,
            cases,
            excluded_count=len(excluded_cases),
            reported_count=len(reported_cases),
        ),
        "cases": cases,
        "summary": parse_summary(wb),
    }
    if excluded_cases:
        result["excluded_cases"] = excluded_cases
        result["excluded_cases_source"] = args.excluded_cases
        result["excluded_cases_sha256"] = file_sha256(args.excluded_cases)
    # summary 只给对比节点建行，待验收算子节点只出现在 statistic 的列前缀里，两处取并集
    result["nodes"] = sorted(set(result["summary"]) | nodes_from_header(header))
    result["backends"] = backends_of(result["nodes"])
    result.update({k: v for k, v in meta.items() if v})

    if args.case_file:
        result["case_file_sha256"] = file_sha256(args.case_file)
        result["case_count"] = len(raw_cases)
        result["standard_acc_summary"] = summarize_standard_acc(raw_cases)
        # 失败用例补「长什么样 + 大概是哪一类」。纯离线的表连接，不碰真机。
        by_id = {str(c.get("id")): c for c in raw_cases}
        for entry in cases:
            if entry.get("passed"):
                continue
            cause, attribution, hint = presume_cause(entry, policy)
            entry.update({"cause": cause, "attribution": attribution, "hint": hint})
            feat = features_of(by_id.get(entry["id"], {}))
            if feat:
                entry["features"] = feat
        groups = group_failures(cases, policy)
        if groups:
            result["failure_groups"] = groups

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    t = result["totals"]
    print(f"[{task}] {args.input} → {args.output}")
    print("  " + "  ".join(f"{k}={v}" for k, v in t.items() if v is not None))
    if task == "accuracy" and t.get("non_finite_inputs"):
        print(f"  非有限输入 {t['non_finite_inputs']} 条。该标签不改变通过率。")
    if task == "accuracy" and t.get("excluded_cases"):
        print(f"  无效用例 {t['excluded_cases']} 条。已从精度分母剔除并保留覆盖缺口。")
    if task == "performance" and t.get("fluctuation_failed"):
        print(f"  {t['fluctuation_failed']} 条波动校验未通过，已如实记录，未做剔除；"
              f"下结论前先判断这些数据是否可信。")

    for g in result.get("failure_groups", []):
        head = ", ".join(str(i) for i in g["ids"][:8])
        more = f" …共 {g['count']} 条" if g["count"] > 8 else ""
        print(f"\n  [{g['attribution']}] {g['count']} 条 —— {g['hint']}")
        print(f"    用例号：{head}{more}")
        for sig, n in sorted(g["features"].items(), key=lambda kv: -kv[1]):
            print(f"    {n:>4} 条  {sig}")
    if any(g["cause"] == "cascade" for g in result.get("failure_groups", [])):
        print("\n  存在 stream 同步失败。已保留在客观通过率中。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
