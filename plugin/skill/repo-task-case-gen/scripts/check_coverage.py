"""核对必测组合是否完整产出。

输入：must_cover.json 和 ATK 用例 JSON。
输出：coverage.json。
退出码：0 全覆盖；2 存在确定性问题。
"""

import argparse
import json
import sys

from _case_utils import (
    extract_axis,
    file_sha256,
    iter_cases,
    load_json,
    signature,
)
from _coverage_strategy import _contains, audit_coverage
import _stage_card


def fail(message):
    """使用退出码 2 表示门禁失败。"""
    print(message, file=sys.stderr)
    sys.exit(2)


def collision_hint(groups, dims):
    """撞签名的 combo 里，是哪几根设计轴没被物化区分开。

    原来的提示只说「补齐 axes，或确认这些 combo 本就重复」。真机上这两条都
    走不通：物化出来的用例内容真的一模一样，补 axes 分不开；「确认重复」也没有
    放行的出口，照样退出码 2。于是要靠自己从「分母」的语义反推出真正的两条路，
    一个算子为此返工了三轮。

    真正的两条路，取决于这几根轴的取值在这条 combo 上**能不能**被区分开：

    - 结构上就不可能区分（rank=1 时首轴即末轴、单元素形态下没有规模之分）：
      写进 `infeasible` 并给 `why`，从分母里扣掉且留痕；
    - 能区分只是物化没区分开（四种 shape_form 撞成同一个形状）：
      改物化脚本的映射，让形状在提取轴上两两可分。

    所以这里直接把「差在哪根轴上」算出来报给使用者，不让人再猜一遍。
    """
    axes_in_dims = list(dims or {})
    lines, shown = [], 0
    for combos in groups.values():
        if len(combos) < 2 or shown >= 5:
            continue
        differing = sorted(
            axis for axis in axes_in_dims
            if len({json.dumps(c.get(axis), sort_keys=True, ensure_ascii=False)
                    for c in combos}) > 1)
        same = {axis: combos[0].get(axis) for axis in axes_in_dims
                if axis not in differing}
        lines.append(f"  - {len(combos)} 条只差 {differing or ['(无)']}，"
                     f"其余轴同为 {json.dumps(same, ensure_ascii=False)}")
        shown += 1
    body = "\n".join(lines)
    return (
        f"{body}\n"
        "  → 这几根轴在这些 combo 上没被物化区分开，两条出路二选一：\n"
        "    a) 结构上就分不开（rank=1 的首轴末轴是同一根、单元素形态下没有规模"
        "之分）：\n"
        "       写进声明的 infeasible 并给 why，那几条从分母里扣掉且留痕；\n"
        "    b) 分得开只是物化没分开（几种 shape_form 撞成同一个形状）：\n"
        "       改物化脚本的映射，让形状在 extract 读得到的地方两两可分。\n"
        "  补 axes 只在「用例内容确实不同、只是没被读出来」时有用；"
        "内容相同时补不出差异。")


def check_dims(spec):
    """核对每条 combo 在每个 dims 轴上的取值都落在声明的分母内。

    audit_coverage 校验的是策略本身（应测总数、baseline、交互组是否自洽）。
    本函数校验的是 combos 这份枚举表与分母是否脱节：
    轴值被手改过、分母被缩过、combo 漏写某个轴，都在这里拦下。

    分母以外的键（shape、shifts、coverage_tags 等物化值）不参与校验。
    """
    dims = spec.get("dims")
    if not isinstance(dims, dict) or not dims:
        fail(
            "must_cover 缺少 dims 分母，无法核对 combos 轴值。\n"
            "  → dims 是覆盖率的分母声明，形如 {\"dtype\": [...], \"rank\": [...]}。")

    combos = spec.get("combos")
    if not isinstance(combos, list) or not combos:
        fail("must_cover.combos 必须是非空数组。")

    problems = []
    for index, combo in enumerate(combos):
        if not isinstance(combo, dict):
            problems.append(f"combos[{index}] 不是对象")
            continue
        for axis, values in dims.items():
            if axis not in combo:
                problems.append(f"combos[{index}] 缺少 dims 轴 {axis!r}")
            elif not _contains(values, combo[axis]):
                problems.append(
                    f"combos[{index}].{axis} = {combo[axis]!r} 不在 dims.{axis} 分母内")

    if problems:
        head = problems[:10]
        more = f"\n  …… 另有 {len(problems) - 10} 条" if len(problems) > 10 else ""
        fail(
            "must_cover 的 combos 与 dims 分母脱节：\n  - "
            + "\n  - ".join(head)
            + more
            + "\n  → 要么把该取值补进 dims 分母，要么把 combo 改回分母内的取值。"
            "分母决定覆盖率的分子分母口径，不能只改一边。")


def main():
    parser = argparse.ArgumentParser(description="必测集覆盖核对")
    parser.add_argument("-m", "--must-cover", required=True,
                        help="物化后的组合表（*_materialized.json）。未物化的 must_cover.json 只有语义轴，没有 shape 和 attr 取值，会命中 0 组")
    parser.add_argument("-j", "--case-json", required=True)
    parser.add_argument("-o", "--output", default="coverage.json")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    spec = load_json(args.must_cover)
    axes = spec["axes"]
    rules = spec.get("extract", {})
    missing_rules = [a for a in axes if a not in rules]
    if missing_rules:
        fail(
            f"axes 中的 {missing_rules} 没有对应的 extract 规则，读不回轴值。\n"
            "  → 参与匹配的轴必须能从用例定义直接读出。需要推导才能得出的轴不要放进 axes，"
            "放进 combos 作为注释即可。")

    policy_failures, policy_report = audit_coverage(spec)
    if policy_failures:
        fail(
            "must_cover 覆盖策略未通过：\n  - "
            + "\n  - ".join(policy_failures))

    wanted, groups = {}, {}
    for combo in spec["combos"]:
        key = signature(combo, axes)
        wanted.setdefault(key, combo)
        groups.setdefault(key, []).append(combo)
    collapsed = len(spec["combos"]) - len(wanted)
    if collapsed:
        fail(
            f"{collapsed} 条 combo 的签名与别的 combo 相同，会被静默合并，"
            f"分母从 {len(spec['combos'])} 缩到 {len(wanted)}。\n"
            + collision_hint(groups, spec.get("dims")))

    produced = set()
    for case in iter_cases(load_json(args.case_json)):
        values = {a: extract_axis(case, rules[a]) for a in axes}
        produced.add(signature(values, axes))

    missing = [combo for sig, combo in wanted.items() if sig not in produced]

    report = {
        "must_cover_total": len(wanted),
        "must_cover_hit": len(wanted) - len(missing),
        "missing": missing,
        "produced_distinct_combos": len(produced),
        "axes": axes,
        "must_cover_sha256": file_sha256(args.must_cover),
        "case_file_sha256": file_sha256(args.case_json),
        "coverage_policy": policy_report,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(
        (f"两两覆盖 {policy_report['pairwise']['rate'] * 100:.1f}%｜"
         if policy_report.get("pairwise") else "")
        + f"定向标签 {policy_report['targeted_hit']}/"
        f"{policy_report['targeted_required']}"
        f"｜必测集 {len(wanted)} 组，命中 {report['must_cover_hit']} 组 → {args.output}")
    if missing:
        print(f"缺口 {len(missing)} 组，前 5 组：", file=sys.stderr)
        for combo in missing[:5]:
            print(f"  {combo}", file=sys.stderr)
        print("  → 分母（combos）没问题，是生成器没把它们产出来。用 validate_cases.py 拿逐项诊断："
              "常见成因是注册错配拿到了空枚举表、或 after_case_config 把 generate() 钉住的"
              "shape 又改了一遍。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
