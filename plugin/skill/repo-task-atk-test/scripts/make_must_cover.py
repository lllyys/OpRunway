"""从覆盖策略确定性地生成 must_cover 的 combos 骨架。

以前 combos 由算子专属脚本手写，门禁再拿同一份策略去比对它——这是循环的：
模型若把策略和 combos 一起编错，那条断言照样通过。所以组合改由本脚本算，
模型只负责声明策略和物化具体值，不再决定分母。

产出的每条 combo 只含轴取值和空的 coverage_tags。具体 shape、attr 取值是
算子专属的物化，本脚本推不出来，由算子脚本读入后逐条填写。

组合方式：两两覆盖阵列打底，声明的 interaction_groups 完整叉乘叠加其上。

退出码：0 完成；2 策略无效或超出 max_cases。
"""

import argparse
import itertools
import json
import os
import sys
from pathlib import Path

from _axis_binding import (check_axis_vocabulary, check_dtype_source,
                           dtype_source_inventory, structural_infeasible)
from align_signatures import AlignError, require_project_source
from _coverage_strategy import (SIZE_AXIS, SIZE_TARGET, CoveragePolicyError,
                                _key, _matching_rules, _validate,
                                class_profile, default_groups,
                                pairwise_report, pairwise_rows,
                                size_ratio_report)
from _expressibility import (check_axis_values, check_contracts,
                             check_value_ranges)
import _stage_card

PASS_THROUGH = ("axes", "extract", "parameters", "infeasible", "yaml",
                "comparator")

# 声明文件允许的顶层键。本清单与 references/artifact-contracts.json 同步，
# 由 tests/test_contracts.py 的锁 L1 保证——加了键不登记会红。
DECL_KEYS = frozenset(PASS_THROUGH) | {
    "dims", "coverage_policy", "operator_class", "class_profile",
    "dtype_source_excludes"}


def build_combos(dims, policy, infeasible, seed=0, operator_class=None,
                 class_profile_decl=None):
    """两两覆盖打底 + 三轴组完整叉乘，去重后返回。

    三轴组 = 算子类别的默认组（由 `OPERATOR_CLASSES` 固化）加上算子额外声明的组。
    默认组不需要模型自己想，也不写进声明文件——它由类别唯一决定。
    """
    _, _, groups, _, max_cases, rules = _validate(dims, policy, infeasible)
    if operator_class is not None:
        groups = default_groups(
            operator_class, groups,
            class_profile(operator_class, class_profile_decl))
    groups = [g for g in groups if all(axis in dims for axis in g)]

    rows = pairwise_rows(dims, infeasible, size_target=SIZE_TARGET, seed=seed)
    seen = {_key(row) for row in rows}
    fallback = {axis: values[0] for axis, values in dims.items()}
    anchors = list(rows) or [fallback]

    def anchor(index):
        """轮换锚行。固定用 rows[0] 会让展开出来的行全部继承它的取值，
        实测把某个 dtype 堆到四成——组内没变的轴会整体倒向那一行。"""
        return dict(anchors[index % len(anchors)])

    bands = [band for band in SIZE_TARGET if band in (dims.get(SIZE_AXIS) or [])]

    added = {}
    for axes in groups:
        count = 0
        for index, values in enumerate(
                itertools.product(*(dims[axis] for axis in axes))):
            row = anchor(index)
            row.update(zip(axes, values))
            # 组内不含规模轴时，规模会整组继承 baseline——那正是「用例全堆在
            # 一个规模上」的成因。按序轮转，条数不变，规模分布回到目标配比。
            if bands and SIZE_AXIS not in axes:
                row[SIZE_AXIS] = bands[index % len(bands)]
            if _matching_rules(row, rules):
                continue
            row_key = _key(row)
            if row_key in seen:
                continue
            seen.add(row_key)
            rows.append(row)
            count += 1
        added["×".join(axes)] = count

    # 贪心可能漏掉个别轴对，用确定性方式补齐：缺哪对就照那对造一行。
    covered = set()
    for row in rows:
        for a, b in itertools.combinations(sorted(dims), 2):
            covered.add((a, _key(row[a]), b, _key(row[b])))
    fill = 0
    for a, b in itertools.combinations(sorted(dims), 2):
        for x in dims[a]:
            for y in dims[b]:
                if (a, _key(x), b, _key(y)) in covered:
                    continue
                row = anchor(fill)
                fill += 1
                row[a], row[b] = x, y
                if _matching_rules(row, rules) or _key(row) in seen:
                    continue
                seen.add(_key(row))
                rows.append(row)
                covered.add((a, _key(x), b, _key(y)))

    if len(rows) > max_cases:
        raise CoveragePolicyError(
            f"生成 {len(rows)} 条组合，超过 max_cases={max_cases}。\n"
            "  两两覆盖打底的规模由取值数最大的两根轴之积决定，砍轴省不了多少；\n"
            "  先看是不是某个 interaction group 展开过大，或者直接调高 max_cases。")
    return rows, added


def _dtype_source_problems(dims, args, excludes):
    """dtype 轴要指明照哪份文件写的，那份文件必须在待验收算子工程目录里。

    这道核对沿用签名对齐的白名单（`require_project_source`）：CANN 装机目录里
    有同名算子的另一份文档，指过去会拿到另一份 dtype 表。没有 dtype 轴的
    分面（纯搬运类）不需要指这份文件。
    """
    values = (dims or {}).get("dtype")
    if not values:
        return [], None
    if not args.dtype_source:
        return ["dims 有 dtype 轴但没给 --dtype-source。\n"
                "    dtype 只能照待验收算子工程声明的数据类型表写——任务书通常"
                "只写「支持所有走入 aicore 的数据类型」，列不出具体名字。"], None
    try:
        resolved = require_project_source(
            args.dtype_source, args.env, "--dtype-source")
    except AlignError as exc:
        return [str(exc)], None
    problems = check_dtype_source(values, Path(resolved), excludes)
    if problems:
        return problems, None
    # 这份文件核对过了才记进 must_cover：下游的浮点判据要拿它换判法，
    # 记一份没核对过的文件等于把判据建在没验过的事实上。
    binding = {
        "source": str(resolved),
        "declared_in_source": dtype_source_inventory(Path(resolved)),
        "excluded": [str((entry or {}).get("dtype", ""))
                     for entry in (excludes or ())],
    }
    return [], binding


def report_structural_infeasible(dims, infeasible):
    """声明期就把「钉死的轴之间分不开的组合」说完。

    这些组合物化脚本再怎么写都产生不出两个不同的用例，`check_coverage` 的重复
    combo 门禁必然拦下——而那时物化脚本已经写完跑完。真机上一个算子在这里退了
    三轮，每轮重跑一次物化。

    不做硬失败：判「分不分得开」终究要看物化怎么映射，硬拒会误伤。
    给一段能直接粘的 `infeasible`，把三轮压成一轮。
    """
    entries = structural_infeasible(dims)
    declared = [
        {k: v for k, v in (rule or {}).items() if k != "why"}
        for rule in (infeasible or [])
    ]
    missing = [
        entry for entry in entries
        if not any(all(rule.get(k) == v for k, v in
                       {k: v for k, v in entry.items() if k != "why"}.items())
                   for rule in declared if rule)
    ]
    if not missing:
        return
    print(f"\n注意：有 {len(missing)} 组钉死轴的取值在结构上分不开，"
          "现在不写进 infeasible，物化跑完后会被重复 combo 门禁退回。",
          file=sys.stderr)
    print("  这些组合与算子无关，可以直接粘进声明的 infeasible：", file=sys.stderr)
    print("  " + json.dumps(missing, ensure_ascii=False, indent=2)
          .replace("\n", "\n  "), file=sys.stderr)
    print("  确认本算子的物化确实能把它们分开，就不用加。\n", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="从覆盖策略生成 must_cover combos 骨架",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-d", "--declaration", required=True,
                        help="含 dims 与 coverage_policy 的声明 JSON")
    parser.add_argument("-o", "--output", required=True, help="must_cover.json")
    parser.add_argument("--seed", type=int, default=0, help="覆盖阵列随机种子")
    parser.add_argument("--dtype-source",
                        help="dtype 轴照哪份文件写：待验收算子工程里声明数据类型的"
                             "README 或头文件。dims 有 dtype 轴时必填")
    parser.add_argument("--interface", default="evidence/interface.json",
                        help="derive_interface.py 的产物；读 baseline_kind，"
                             "它决定比较器判据走哪一支")
    parser.add_argument("--env", default="evidence/env.json",
                        help="用来核对 --dtype-source 确实在工程目录里的 env.json")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    spec = json.load(open(args.declaration, encoding="utf-8"))
    unknown = sorted(set(spec) - DECL_KEYS)
    if unknown:
        print(f"声明文件有未知顶层键：{', '.join(unknown)}", file=sys.stderr)
        print("拼错的键会被静默忽略，声明看着写了、实际没生效。", file=sys.stderr)
        print("若确属 ATK 或本流程的合法字段，说明骨架的清单不全："
              "登记进 evidence/knowledge_gaps.json 并补进 "
              "references/artifact-contracts.json。", file=sys.stderr)
        return 2
    dims = spec.get("dims")
    policy = spec.get("coverage_policy")
    infeasible = spec.get("infeasible", [])

    # 可表达性在这里判完，不留到第四步的 make_yaml。判据是 decl 自己写下的
    # 轴取值与契约，此刻就在手上；拖到 make_yaml 意味着 agent 要先写完物化
    # 脚本、跑完物化，才被告知第一步定死的取值 ATK 表达不出来。
    problems = [message for _, message in check_contracts(spec.get("parameters"))]
    problems += check_axis_values(dims, spec.get("parameters"),
                                  spec.get("extract"))
    problems += check_value_ranges((dims or {}).get("dtype"),
                                   spec.get("parameters"))
    if problems:
        print(f"声明里有 {len(problems)} 处 ATK 表达不出来的地方：", file=sys.stderr)
        for index, message in enumerate(problems, 1):
            print(f"  {index}. {message}", file=sys.stderr)
        return 2

    # 取值说不说得出照哪写的，和 ATK 表达不表达得出来是两回事：上一道判「能不能跑」，
    # 这道判「凭什么是这批取值」。分母立不住，后面的覆盖率就都是自证。
    problems = check_axis_vocabulary(dims)
    dtype_problems, dtype_binding = _dtype_source_problems(
        dims, args, spec.get("dtype_source_excludes"))
    problems += dtype_problems
    if problems:
        print(f"dims 声明有 {len(problems)} 处站不住的取值：", file=sys.stderr)
        for index, message in enumerate(problems, 1):
            print(f"  {index}. {message}", file=sys.stderr)
        return 2

    report_structural_infeasible(dims, infeasible)

    try:
        rows, added = build_combos(dims, policy, infeasible, args.seed,
                                   spec.get("operator_class"),
                                   spec.get("class_profile"))
    except CoveragePolicyError as exc:
        print(f"覆盖策略无效：{exc}", file=sys.stderr)
        return 2

    # 比较器判据要按「跟谁比」分叉：跟框架基线跨后端比，浮点的位级相等不成立；
    # 跟 CANN 内置实现比是同后端回归比对，任何一位不同都是要抓的东西。
    # 缺 interface.json 不能静默按 torch 走：那会让比较器判据反过来要求
    # 把 equal 改成 mixed_tolerance_bm，门禁主动把人推向错的那条路。
    # interface.json 是 S1 的必产物，缺它本来就不该往下走。
    if not os.path.exists(args.interface):
        print(f"{args.interface} 不在，判不了这一轮跟谁比。\n"
              "  → 它是 S1 的必产物（derive_interface.py 产出），"
              "比较器判据要按它的 baseline_kind 分叉。\n"
              "     换个工作目录跑的话，用 --interface 指过去。", file=sys.stderr)
        return 3
    with open(args.interface, encoding="utf-8") as handle:
        interface = json.load(handle)
    baseline_kind = interface.get("baseline_kind", "torch")
    must_cover = {"dims": dims, "coverage_policy": policy,
                  "baseline_kind": baseline_kind}
    if "interface_mode" in interface:
        must_cover["interface_mode"] = interface["interface_mode"]
    for field in ("operator_class", "class_profile"):
        if field in spec:
            must_cover[field] = spec[field]
    for field in PASS_THROUGH:
        if field in spec:
            must_cover[field] = spec[field]
    if dtype_binding:
        must_cover["dtype_binding"] = dtype_binding
    must_cover["combos"] = [dict(row, coverage_tags=[]) for row in rows]

    with open(args.output, "w", encoding="utf-8") as sink:
        json.dump(must_cover, sink, ensure_ascii=False, indent=2)

    report = pairwise_report(dims, rows, _validate(dims, policy, infeasible)[-1])
    print(f"生成 {len(rows)} 条组合 → {args.output}")
    print(f"  两两覆盖 {report['covered']}/{report['required']} = "
          f"{report['rate'] * 100:.1f}%")
    for name, count in added.items():
        print(f"  交互组 {name} 追加 {count} 条")

    ratio = size_ratio_report(dims, must_cover["combos"])
    if ratio and ratio["total"]:
        shares = "  ".join(
            f"{band} {ratio['ratio'][band] * 100:.0f}%" for band in ratio["target"])
        print(f"  规模配比 {shares}（目标 "
              + "  ".join(f"{b} {v * 100:.0f}%" for b, v in ratio["target"].items())
              + "）")
    elif SIZE_AXIS not in (dims or {}):
        print(f"  未声明 {SIZE_AXIS} 轴，规模配比不参与核对")

    print("\n下一步：算子脚本读入本文件，为每条 combo 填写具体 shape 与 attr 取值，"
          "并按 coverage_policy.targeted 补齐带标签的边界用例。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
