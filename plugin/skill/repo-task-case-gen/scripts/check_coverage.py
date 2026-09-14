#!/usr/bin/env python3
"""量 cases.json 的覆盖面：每根轴的取值分布、两两覆盖率、规模档配比。

**纯度量，默认不拦。** 退出码固定 0，除非显式给了 `--min-pairwise`。
先拿真实数字，再决定要不要改生成策略——没有数字就上覆盖阵列，
等于为「可能有用」加东西。

轴是从用例结构里推的，不需要配置：

| 轴 | 怎么来 |
| --- | --- |
| `dtype` | 第一个张量输入的 dtype |
| `rank` | 第一个张量输入的秩 |
| `size` | 第一个张量输入的字节数分档 |
| `arg<i>` | 第 i 个非张量输入：标量按正负零离散化，数组按长度 |

退出码 0 通过，2 低于 `--min-pairwise`，3 输入缺失。
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

# 规模按**字节数**分档，不按元素数——同样 65536 个元素，int8 是 64 KB，
# fp32 是 256 KB，落在算子搬运能力的不同区间里。
SMALL_BYTES = 32 * 1024
MEDIUM_BYTES = 2 * 1024 * 1024
# 全量**不追**配比：追 30% large 意味着几十条数 MB 的张量，golden 涨到几百 MB，
# 而 tiling 边界缺陷在 2^n±1 的小张量上就能暴露。配比只在性能子集上有意义，
# 由 gen_cases.py 抽样时按配额保证。这里只报数据，`large` 为 0 才提示——
# 那时子集一条都填不进去。
LARGE_FLOOR = 1

DTYPE_BYTES = {
    "fp64": 8, "int64": 8, "uint64": 8, "complex64": 8, "complex128": 16,
    "fp32": 4, "int32": 4, "uint32": 4, "tf32": 4, "hf32": 4,
    "fp16": 2, "bf16": 2, "int16": 2, "uint16": 2,
    "int8": 1, "uint8": 1, "bool": 1, "fp8e4m3": 1, "fp8e5m2": 1,
}


def _first_tensor(case):
    for item in case.get("inputs", []):
        if isinstance(item, dict) and item.get("type") == "tensor":
            return item
    return {}


def _size_band(tensor):
    shape = tensor.get("shape") or []
    numel = 1
    for dim in shape:
        numel *= dim
    total = numel * DTYPE_BYTES.get(tensor.get("dtype"), 4)
    if total < SMALL_BYTES:
        return "small"
    if total < MEDIUM_BYTES:
        return "medium"
    return "large"


def _scalar_bucket(value):
    """标量按正负零离散化。轴类参数的缺陷集中在符号上，具体数值不分档。"""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return "neg" if value < 0 else ("zero" if value == 0 else "pos")
    return str(value)


def _axes(case):
    """一条用例 -> {轴名: 取值}。轴集合对同一份 cases.json 是稳定的。"""
    tensor = _first_tensor(case)
    row = {
        "dtype": tensor.get("dtype", "?"),
        "rank": str(len(tensor.get("shape") or [])),
        "size": _size_band(tensor),
    }
    seen_tensor = False
    index = 0
    for item in case.get("inputs", []):
        if isinstance(item, dict) and item.get("type") == "tensor" and not seen_tensor:
            seen_tensor = True
            continue
        index += 1
        if isinstance(item, list):
            row[f"arg{index}"] = f"len{len(item)}"
        elif isinstance(item, dict):
            row[f"arg{index}"] = _scalar_bucket(item.get("range_values"))
    return row


def _pairwise(rows, axis_values):
    """两两覆盖率。

    分母用**实际出现过的取值集合**做笛卡尔积，不是理论全集——理论全集要知道
    每根轴的完整定义域，而那正是 YAML 想表达却未必表达全的东西。
    这么算出来的率偏乐观，但跨轮次可比，用来看趋势够了。
    """
    axes = sorted(axis_values)
    details = []
    covered_total = required_total = 0
    for left, right in combinations(axes, 2):
        want = len(axis_values[left]) * len(axis_values[right])
        if want <= 1:
            continue
        got = len({(row[left], row[right]) for row in rows})
        details.append((f"{left}×{right}", got, want))
        covered_total += got
        required_total += want
    rate = covered_total / required_total if required_total else 1.0
    return rate, covered_total, required_total, details


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--cases", default="cases.json")
    parser.add_argument("--min-pairwise", type=float, default=None,
                        help="低于这个两两覆盖率就退 2。不给就只报数不拦。")
    parser.add_argument("--worst", type=int, default=5,
                        help="列出覆盖率最低的几个轴对")
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

    rows = [_axes(case) for case in cases]
    axis_values = defaultdict(set)
    for row in rows:
        for axis, value in row.items():
            axis_values[axis].add(value)

    print(f"用例数    {len(cases)}")
    print(f"轴        {len(axis_values)} 根：{'、'.join(sorted(axis_values))}")
    print()
    print("每根轴的取值分布")
    for axis in sorted(axis_values):
        counts = Counter(row[axis] for row in rows)
        body = "、".join(f"{value}:{count}" for value, count in counts.most_common())
        flag = "  ← 只有一个取值，这根轴等于没测" if len(counts) == 1 else ""
        print(f"  {axis:8} {body}{flag}")

    rate, covered, required, details = _pairwise(rows, axis_values)
    print()
    print(f"两两覆盖  {rate:.1%}（{covered}/{required} 个取值对）")
    details.sort(key=lambda item: item[1] / item[2])
    for name, got, want in details[:args.worst]:
        print(f"  {name:20} {got}/{want}  {got / want:.0%}")

    print()
    print("规模档    按字节分，small <32KB / medium <2MB / large ≥2MB")
    bands = Counter(row["size"] for row in rows)
    for band in ("small", "medium", "large"):
        share = bands.get(band, 0) / len(rows)
        print(f"  {band:8} {bands.get(band, 0):4} 条  {share:5.1%}")
    print("  全量不追配比，配比由 gen_cases.py 抽性能子集时按配额保证")
    if bands.get("large", 0) < LARGE_FLOOR:
        print(f"\nlarge 档一条都没有，性能子集填不进多核切分路径的用例。"
              f"\n回 S2 按这个顺序试：YAML 配 size_distributions -> 加大 max_length"
              f" -> 给 dim_values 加大值。见 case-strategy.md「规模档」。")

    if args.min_pairwise is not None and rate < args.min_pairwise:
        print(f"\n两两覆盖 {rate:.1%} 低于要求的 {args.min_pairwise:.1%}。"
              f"回 S2 给覆盖最差的那几个轴对加取值。", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
