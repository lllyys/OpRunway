#!/usr/bin/env python3
"""量 cases.json 的覆盖面：每根轴的取值分布、组合空缺、规模档。

**纯度量，退出码固定 0**（除非 cases.json 读不出来）。它不设阈值、不拦人——
覆盖面够不够是算子语义问题，量具给不出判据，只能把事实摆出来让人看。

轴从用例结构里推，**轴名取自 `facts.json` 的 `params[].name`**，读不到才退回
`arg<i>`。名字很重要：报告里写 `x2.列表长度` 才看得懂，写 `arg1` 得回去翻签名。

| 轴 | 怎么来 |
| --- | --- |
| `<第一个张量输入>.dtype` / `.rank` / `.规模` / `.值域` | 第一个张量输入（张量列表则整个列表求和分档） |
| `<第一个张量输入>.列表长度` | 只有 `type: tensors` 才有 |
| `<其余张量输入>.dtype` | 第二个及以后的张量输入 |
| `<非张量输入>` | 标量按正负零离散化，数组按长度 |

**不报两两覆盖率。** 那个百分比的分母是本轮实际抽到的取值做笛卡尔积，里面混着
结构上不可达的格子（`rank=0` 只可能落 `scalar` 档、语义上绑定等长的两个列表），
天花板本来就不到 100%，划不出及格线。改成直接列**哪些取值组合一条都没有**，
只当事实摆出来：里面混着结构上不可能的格子，**不是判据，也不拿它去问用户**。

唯一有外部参照的是 `rank`：拿 `facts.json` 的 `shape.rank` 对账。注意那是
**S1 抄进 facts.json 的声明**，不是文档原文——对账查的是「S2 有没有兑现 S1 的声明」，
不是「S1 抄得对不对」。

退出码 0 通过，3 输入缺失。
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from case_shape import (  # noqa: E402
    MEDIUM_BYTES, SMALL_BYTES, classify, dtype_of, guard, rank_of, size_band,
    tensor_items,
)

# 全量**不追**配比：追 30% large 意味着几十条数 MB 的张量，golden 涨到几百 MB，
# 而 tiling 边界缺陷在 2^n±1 的小张量上就能暴露。配比只在性能子集上有意义，
# 由 gen_cases.py 抽样时按配额保证。这里只报数据，`large` 为 0 才提示——
# 那时子集一条都填不进去。
LARGE_FLOOR = 1


def _scalar_bucket(value):
    """标量按正负零离散化。轴类参数的缺陷集中在符号上，具体数值不分档。"""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return "neg" if value < 0 else ("zero" if value == 0 else "pos")
    return str(value)


def _range_bucket(tensor):
    """张量取值区间。YAML 的 `ranges.valid.values` 列了几段，ATK 每条挑一段。"""
    values = tensor.get("range_values")
    if isinstance(values, list) and len(values) == 2:
        return f"[{values[0]},{values[1]}]"
    return "未声明"


def _input_names(facts_path):
    """`facts.json` 的输入参数名，按签名顺序。读不到就返回空列表。

    只取 `role == "input"`——`cases.json` 的 `inputs` 里没有输出参数。
    """
    path = Path(facts_path)
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            params = json.load(handle).get("params") or []
    except (ValueError, OSError):
        return []
    return [str(p.get("name") or "") for p in params
            if isinstance(p, dict) and p.get("role") == "input"]


def _axes(case, names):
    """一条用例 -> {轴名: 取值}。轴集合对同一份 cases.json 是稳定的。"""
    row = {}
    first_done = False
    for index, item in enumerate(case.get("inputs", [])):
        kind = classify(item)
        name = names[index] if index < len(names) and names[index] else f"arg{index + 1}"
        if kind in ("tensor", "tensors"):
            if not first_done:
                first_done = True
                row[f"{name}.dtype"] = dtype_of(case)
                row[f"{name}.rank"] = str(rank_of(case))
                row[f"{name}.规模"] = size_band(case)
                row[f"{name}.值域"] = _range_bucket(tensor_items(case)[0])
                # 单张量输入的「列表长度」恒为 1，做成轴只会误报「只有一个取值」
                if kind == "tensors":
                    row[f"{name}.列表长度"] = f"len{len(item)}"
            else:
                head = item[0] if kind == "tensors" else item
                row[f"{name}.dtype"] = head.get("dtype") or "?"
            continue
        if isinstance(item, list):
            row[f"{name}.长度"] = f"len{len(item)}"
        elif isinstance(item, dict):
            row[name] = _scalar_bucket(item.get("range_values"))
        else:
            row[name] = _scalar_bucket(item)
    return row


def _span(values):
    """[0,1,2,3,4] -> "0–4"；[5,7] -> "5、7"。"""
    if not values:
        return "无"
    ordered = sorted(values)
    if ordered == list(range(ordered[0], ordered[-1] + 1)) and len(ordered) > 1:
        return f"{ordered[0]}–{ordered[-1]}"
    return "、".join(str(v) for v in ordered)


def _focus_dtypes(facts_path):
    """facts.json 的 focus_dtypes。没有就返回空——这根判据随之不生效。"""
    try:
        with open(facts_path, encoding="utf-8") as handle:
            return [str(d) for d in (json.load(handle).get("focus_dtypes") or [])]
    except (OSError, ValueError):
        return []


def _declared_rank(facts_path):
    """facts.json 声明的秩区间，读不到就返回 None——没有参照就不对账。"""
    path = Path(facts_path)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            rank = json.load(handle).get("shape", {}).get("rank")
    except (ValueError, OSError):
        return None
    if isinstance(rank, list) and len(rank) == 2 and all(isinstance(v, int) for v in rank):
        return tuple(rank)
    return None


def _gaps(rows, axis_values):
    """列出哪些取值组合一条都没有。

    **不算百分比。** 全组合里混着结构上不可达的格子（`rank=0` 只能是 `scalar`、
    两个等长列表只有对角线可达），算成率会把天花板压到 100% 以下，划不出及格线。
    这里只报「缺了哪些」，是不是该补由看的人按算子语义判断。
    """
    axes = sorted(axis_values)
    found = []
    bound = []
    for left, right in combinations(axes, 2):
        if len(axis_values[left]) <= 1 or len(axis_values[right]) <= 1:
            continue
        seen = {(row[left], row[right]) for row in rows}
        # 一根轴完全决定另一根（取值对数 == 某一侧的取值数）。这时非对角线的格子
        # 全部不可达，列进空缺只会把真空缺挤下去。多半是语义绑定（x2 对齐 x1），
        # 但也可能是约束器无意压塌，所以单独报一行而不是直接丢掉。
        if len(seen) in (len(axis_values[left]), len(axis_values[right])):
            bound.append(f"{left} × {right}")
            continue
        missing = sorted(
            (lv, rv)
            for lv in axis_values[left]
            for rv in axis_values[right]
            if (lv, rv) not in seen
        )
        if missing:
            found.append((f"{left} × {right}", missing, len(seen) + len(missing)))
    found.sort(key=lambda item: len(item[1]), reverse=True)
    return found, bound


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--cases", default="cases.json")
    parser.add_argument("--worst", type=int, default=5,
                        help="最多列几个轴对的组合空缺")
    parser.add_argument("--examples", type=int, default=8,
                        help="每个轴对最多列几个空缺组合")
    parser.add_argument("-F", "--facts", default="facts.json",
                        help="取参数名与 shape.rank，读不到就退回 arg<i> 且不对账")
    args = parser.parse_args()

    path = Path(args.cases)
    if not path.exists():
        print(f"{path} 不存在。先跑 gen_cases.py。", file=sys.stderr)
        return 3
    with open(path, encoding="utf-8") as handle:
        cases = json.load(handle)
    if not cases:
        print(f"{path} 是空的。", file=sys.stderr)
        return 3

    if not guard(cases, sys.stderr):
        return 3

    names = _input_names(args.facts)
    rows = [_axes(case, names) for case in cases]
    axis_values = defaultdict(set)
    for row in rows:
        for axis, value in row.items():
            axis_values[axis].add(value)

    rank_axis = next((a for a in axis_values if a.endswith(".rank")), None)
    band_axis = next((a for a in axis_values if a.endswith(".规模")), None)

    # ---- 给用户看的总览表：原样贴给用户，不要转述 ----
    print("## 用例覆盖总览")
    print()
    print(f"用例数 **{len(cases)}**，覆盖轴 **{len(axis_values)}** 根"
          f"（轴名取自 {'facts.json' if names else 'arg<i> 回退，facts.json 没读到'}）")
    print()
    print("| 轴 | 取值数 | 分布 |")
    print("| --- | ---: | --- |")
    for axis in sorted(axis_values):
        counts = Counter(row[axis] for row in rows if axis in row)
        body = "、".join(f"`{value}`×{count}" for value, count in counts.most_common())
        # 值域轴单值不打 ⚠：它归下面的「值域」行，不是返工判据。
        # 两处口径不一致时，读表的人会照 ⚠ 去返工，白改一轮。
        note = (" ⚠ 只有一个取值，这根轴等于没测"
                if len(counts) == 1 and not axis.endswith(".值域") else "")
        print(f"| {axis} | {len(counts)} | {body}{note} |")

    print()
    print("| 判据 | 结果 |")
    print("| --- | --- |")

    declared = _declared_rank(args.facts)
    tested = sorted(int(v) for v in axis_values[rank_axis]) if rank_axis else []
    if not rank_axis:
        rank_line = "认不出秩轴，跳过"
    elif declared is None:
        rank_line = (f"实测 {_span(tested)}；没读到 {args.facts} 的 `shape.rank`，"
                     f"**不对账**——有没有测到声明的上界，本轮无从判断")
    else:
        low, high = declared
        missing = [r for r in range(low, high + 1) if r not in tested]
        rank_line = (f"实测 {_span(tested)}，facts.json 声明 {low}–{high}；"
                     + (f"**{_span(missing)} 未测**" if missing else "无缺档"))
    # 轴退化放在秩对账之后：秩只有一个取值时，先看 facts 声明的是不是就一个秩
    # （Pdist 这类算子文档写死 2 维），是就不算退化——否则这行会和下一行打架。
    rank_is_pinned = declared is not None and declared[0] == declared[1]
    single = [a for a in sorted(axis_values)
              if len(axis_values[a]) == 1 and not (a == rank_axis and rank_is_pinned)]
    # 值域轴单列。它只有一段区间是 YAML 里写了几段的直接结果，不是约束器写死
    # 造成的覆盖缺口；混进轴退化会让纯浮点算子每次都误报一条返工判据。
    range_single = [a for a in single if a.endswith(".值域")]
    single = [a for a in single if not a.endswith(".值域")]
    if single:
        degraded = (f"**{'、'.join(single)}** 只有一个取值——要么 YAML 只声明了一个取值，"
                    f"要么约束器写死了。这根轴等于没测")
    else:
        degraded = "无"
    if range_single:
        span = "、".join(f"{a}={next(iter(axis_values[a]))}" for a in range_single)
        range_line = (f"{span} 只有一段——**不是返工判据**。任务书点名要测某个值域"
                      f"（NaN/inf/边界值）时才回 S2 加 `ranges.valid.values`")
    else:
        range_line = "各值域轴都有多段"
    print(f"| 轴退化 | {degraded} |")
    print(f"| 值域 | {range_line} |")
    print(f"| 秩覆盖 | {rank_line} |")

    bands = Counter(row[band_axis] for row in rows if band_axis in row) if band_axis else Counter()
    band_body = "、".join(f"{b}×{bands.get(b, 0)}" for b in ("scalar", "small", "medium", "large"))
    large_note = ("；**large 一条都没有**，性能子集填不进多核切分路径"
                  if bands.get("large", 0) < LARGE_FLOOR else "")
    focus = _focus_dtypes(args.facts)
    if focus and band_axis:
        dtype_axis = next((a for a in axis_values if a.endswith(".dtype")), None)
        holes = []
        for dtype in focus:
            got = {row[band_axis] for row in rows
                   if row.get(dtype_axis) == dtype and band_axis in row}
            if not got:
                holes.append(f"{dtype}：一条都没有")
                continue
            missing = [b for b in ("small", "medium", "large") if b not in got]
            if missing:
                holes.append(f"{dtype}：缺 {'、'.join(missing)}")
        focus_line = ("**" + "；".join(holes) + "** —— 重点 dtype 缺规模档，"
                      "回 S2 在约束器里按 dtype 现算门槛显式构造"
                      if holes else
                      f"{'、'.join(focus)} 三档齐全")
        print(f"| 重点 dtype | {focus_line} |")

    print(f"| 规模档 | {band_body}{large_note}（按字节分：scalar 单元素 / "
          f"small <{SMALL_BYTES // 1024}KB / medium <{MEDIUM_BYTES // 1024 // 1024}MB / "
          f"large ≥{MEDIUM_BYTES // 1024 // 1024}MB。全量不追配比，"
          f"配比由 gen_cases.py 抽性能子集时按配额保证） |")
    print()

    # ---- 组合空缺：给 agent 逐条判断，不是判据 ----
    gaps, bound = _gaps(rows, axis_values)
    if bound:
        print(f"绑定轴对  {'、'.join(bound)}")
        print("          一根轴完全决定另一根，非对角线的组合不可达，不列空缺。")
        print("          多半是语义绑定（如 x2 的 dtype 对齐 x1）；"
              "如果这两根轴本该独立，那就是约束器压塌了。")
        print()
    if not gaps:
        print("组合空缺  无：任意两根轴的取值组合都至少有一条用例")
    else:
        print(f"组合空缺  {len(gaps)} 个轴对有空缺，按空缺数排前 {args.worst} 个。")
        print("          **这不是判据，不据此返工，也不要拿它去问用户。**"
              "结构上不可达的组合（秩 0 只能是 scalar、")
        print("          语义上绑定的两根轴）本来就填不满。交付简表里列一行"
              "「组合空缺 N 处」即可，继续往下走。")
        for name, missing, total in gaps[:args.worst]:
            shown = "、".join(f"{lv}×{rv}" for lv, rv in missing[:args.examples])
            more = f" …另 {len(missing) - args.examples} 个" if len(missing) > args.examples else ""
            print(f"  {name}  缺 {len(missing)}/{total}：{shown}{more}")

    if declared is not None and rank_axis and [r for r in range(declared[0], declared[1] + 1)
                                               if r not in tested]:
        print()
        print("秩缺档的修法：低秩是抽样抽到的，高秩不参与抽样——ATK 抽 shape 靠拒绝采样，"
              "接受率随秩指数下降，")
        print("硬抽会一路重抽到它自己的 300s 守卫。要覆盖就在约束器里显式写形状，"
              "见 case-strategy.md「高秩不靠抽样」。")

    if bands.get("large", 0) < LARGE_FLOOR:
        print()
        print("large 档一条都没有，性能子集填不进多核切分路径的用例。")
        print("回 S2 先把 max_length 提到 large 门槛的 2 倍（4194304），"
              "再看 dim_values 最大值。见 case-strategy.md「规模档」。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
