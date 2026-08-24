#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性选择性能用例。

输入：精度用例集和性能归格文件。
输出：性能用例集和可选选样 manifest。
退出码：0 完成；非 0 表示无法选样。
"""

import argparse
import json
import os
import sys

from _case_utils import extract_axis, iter_cases, load_json, numel, tensor_inputs
from _policy import PolicyError, load_verdict_policy

BORDER_MARKS = ("nan", "inf", "-inf", "null")

def is_excluded(case, excluded_reasons=None):
    """空张量 / inf-nan / 边界值用例不进性能集。"""
    reason = (excluded_reasons or {}).get(str(case.get("id")))
    if reason:
        return reason
    if case.get("is_boundary"):
        return "boundary"
    for inp in tensor_inputs(case):
        if numel(inp.get("shape")) == 0:
            return "empty"
        rv = inp.get("range_values")
        marks = rv if isinstance(rv, list) else [rv]
        for m in marks:
            if isinstance(m, str) and m.lower() in BORDER_MARKS:
                return "infnan"
    return None


def branch_of(case, spec):
    """使用显式标签或单轴规则确定性能场景。"""
    if "labels" in spec:
        # 用例 id 是【每个文件内】自增的，多份用例集一起传时会撞号，
        # 所以查表键优先用 "<文件名>:<id>"，退化到裸 id 只在单文件时安全。
        labels = spec["labels"]
        key = f"{case['__source__']}:{case.get('id')}"
        if key in labels:
            return labels[key]
        return labels.get(str(case.get("id")))
    value = extract_axis(case, spec["axis"])
    if value is None:
        return None
    for rule in spec["rules"]:
        if "max" not in rule or value <= rule["max"]:
            return rule["name"]
    return None


def allocate(cell_keys, total):
    """均匀分配配额，且总和不超过 total。"""
    keys = sorted(cell_keys, key=str)
    quota = {key: 0 for key in keys}
    if total <= 0 or not keys:
        return quota
    selected = keys[:total] if len(keys) > total else keys
    base, extra = divmod(total, len(selected))
    for index, key in enumerate(selected):
        quota[key] = base + (1 if index < extra else 0)
    return quota


def pick_even(cases, k, size_axis):
    """格内按规模排序后等距取 k 条，保证覆盖到下界与上界。"""
    ordered = sorted(cases, key=lambda c: extract_axis(c, size_axis) or 0)
    n = len(ordered)
    if k >= n:
        return ordered
    if k == 1:
        return [ordered[-1]]
    step = (n - 1) / (k - 1)
    return [ordered[round(i * step)] for i in range(k)]


def main():
    parser = argparse.ArgumentParser(description="从精度用例集筛选性能用例集")
    parser.add_argument("-j", "--case-json", required=True, action="append",
                        help="精度全量用例 JSON，可多次传入（如浮点集与整型集）")
    parser.add_argument("-g", "--grid", required=True, help="perf_grid.json")
    parser.add_argument("-n", "--number", type=int, default=50, help="目标用例数，默认 50")
    parser.add_argument("-o", "--output", required=True, help="性能用例集落盘路径")
    parser.add_argument("--manifest", help="筛选过程 manifest 落盘路径")
    parser.add_argument("-x", "--excluded-cases", help="精度阶段的无效用例记录")
    args = parser.parse_args()

    spec = load_json(args.grid)
    try:
        allowed_causes = set(
            load_verdict_policy()["case_exclusions"]["allowed_causes"])
    except PolicyError as error:
        sys.exit(str(error))
    excluded_reasons = {}
    if args.excluded_cases:
        excluded_doc = load_json(args.excluded_cases)
        excluded_reasons = {
            str(item.get("id")): item.get("cause")
            for item in excluded_doc.get("cases", [])
            if item.get("cause") in allowed_causes
        }
    dtype_classes = spec["dtype_classes"]
    size_axis = spec.get("size_axis", {"from": "input_numel", "index": 0})

    cases, seen_ids = [], {}
    for path in args.case_json:
        source = os.path.basename(path)
        for case in iter_cases(load_json(path)):
            case["__source__"] = source
            seen_ids.setdefault(case.get("id"), set()).add(source)
            cases.append(case)

    collided = {i for i, srcs in seen_ids.items() if len(srcs) > 1}
    if collided and "labels" in spec["branch"]:
        plain_only = not any(":" in k for k in spec["branch"]["labels"])
        if plain_only:
            sys.exit(
                f"用例 id 在多份用例集之间撞号（{len(collided)} 个），"
                f"而 labels 用的是裸 id，查表会取到错误分支。\n"
                f"请把 labels 的键改成 \"<文件名>:<id>\"，例如 \"all_op_fp.json:12\"。"
            )

    excluded = {}
    cells = {}
    ungridded = 0
    for case in cases:
        reason = is_excluded(case, excluded_reasons)
        if reason:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        tensors = tensor_inputs(case)
        if not tensors:
            ungridded += 1
            continue
        branch = branch_of(case, spec["branch"])
        width = dtype_classes.get(tensors[0].get("dtype"))
        if branch is None or width is None:
            ungridded += 1
            continue
        cells.setdefault(f"{branch}|{width}", []).append(case)

    if not cells:
        sys.exit("没有任何用例能归格，检查 perf_grid.json 的 branch/dtype_classes 是否与用例集匹配")

    cell_keys = sorted(cells)
    quota = allocate(cell_keys, args.number)

    # 某格可用用例不足配额时，余额回流给其他格，保证总数打满
    selected, shortfall = {}, 0
    for key in cell_keys:
        picked = pick_even(cells[key], quota[key], size_axis)
        selected[key] = picked
        shortfall += quota[key] - len(picked)
    for key in cell_keys:
        if shortfall <= 0:
            break
        spare = len(cells[key]) - len(selected[key])
        if spare <= 0:
            continue
        extra = pick_even([c for c in cells[key] if c not in selected[key]],
                          min(spare, shortfall), size_axis)
        selected[key].extend(extra)
        shortfall -= len(extra)

    # __source__ 是本脚本注入的内部字段，落盘前必须摘掉——
    # 用例 JSON 会被 ATK 直接消费，多一个字段就是污染待验收算子的输入
    out = [{k: v for k, v in c.items() if k != "__source__"}
           for key in cell_keys for c in selected[key]]
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    manifest = {
        "target_number": args.number,
        "selected": len(out),
        "cells": len(cell_keys),
        "cell_detail": {k: {"available": len(cells[k]), "quota": quota[k],
                            "selected": len(selected[k])} for k in cell_keys},
        "excluded": excluded,
        "ungridded": ungridded,
        "source": args.case_json,
        "note": "空张量、非有限值和边界用例不进入性能集",
    }
    if args.manifest:
        with open(args.manifest, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"格子数 {len(cell_keys)}，选出 {len(out)} / 目标 {args.number}")
    for key in cell_keys:
        d = manifest["cell_detail"][key]
        flag = "  ← 可用不足" if d["selected"] < d["quota"] else ""
        print(f"  {key:24s} 可用 {d['available']:4d}  选 {d['selected']:2d}{flag}")
    if excluded:
        print("已排除:", ", ".join(f"{k}={v}" for k, v in sorted(excluded.items())))
    if ungridded:
        print(f"未能归格 {ungridded} 条（dtype 不在 dtype_classes 或分支判别失败）")
    if len(cell_keys) > args.number:
        print(f"格子数 {len(cell_keys)} 超过目标 {args.number}。"
              "已按稳定顺序选择目标数量的格子。")


if __name__ == "__main__":
    main()
