#!/usr/bin/env python3
"""从全量用例里抽一小撮做冒烟，按 dtype 与 shape 档位分层，不是纯随机。

输出目录固定叫 smoke/，里面的文件固定叫 cases.json——ATK 用**用例文件名**
当 golden 的子目录名（result_process.py:67），文件名换了就对不上 golden。
退出码 0 抽好，3 输入缺失。
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path


def _tensor(case):
    for item in case.get("inputs", []):
        if isinstance(item, dict) and item.get("type") == "tensor":
            return item
    return {}


def _bucket(case):
    """按 dtype × shape 档位分层，让冒烟覆盖到大中小三种切分形态。"""
    tensor = _tensor(case)
    dtype = tensor.get("dtype", "?")
    shape = tensor.get("shape") or []
    total = 1
    for dim in shape:
        total *= dim
    if total <= 1:
        size = "scalar"
    elif total < 1024:
        size = "small"
    elif total < 262144:
        size = "medium"
    else:
        size = "large"
    return f"{dtype}/{size}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--cases", default="cases.json")
    parser.add_argument("-o", "--out-dir", default="smoke",
                        help="抽样输出目录，里面固定写 cases.json")
    parser.add_argument("-n", "--number", type=int, default=30)
    parser.add_argument("-s", "--seed", type=int, default=42)
    parser.add_argument("--exclude-ids", default="",
                        help="逗号分隔的用例 id，从抽样里剔除。"
                             "性能基线轮用：基线实现跑不动的用例没法比，"
                             "剔除后要在报告里写明剔了哪一类。")
    parser.add_argument("--dtypes", default="",
                        help="逗号分隔，只保留这些 dtype 的用例。"
                             "性能基线轮用：CANN 内置实现不支持的新增 dtype 没有基线可比。")
    args = parser.parse_args()

    path = Path(args.cases)
    if not path.exists():
        print(f"{path} 不存在。用例包里应该带 cases.json。", file=sys.stderr)
        return 3

    with open(path, encoding="utf-8") as handle:
        cases = json.load(handle)

    if args.exclude_ids:
        dropped = {int(i) for i in args.exclude_ids.split(",") if i.strip()}
        before = len(cases)
        cases = [c for c in cases if c["id"] not in dropped]
        print(f"id 剔除    {before} → {len(cases)} 条，剔了 {len(dropped)} 条")

    if args.dtypes:
        wanted = {d.strip() for d in args.dtypes.split(",") if d.strip()}
        before = len(cases)
        cases = [c for c in cases if _tensor(c).get("dtype") in wanted]
        print(f"dtype 过滤  {before} → {len(cases)} 条，只留 {'、'.join(sorted(wanted))}")
        if not cases:
            print("过滤后一条不剩，检查 --dtypes 的写法是否在 ATK 词表里。",
                  file=sys.stderr)
            return 3

    buckets = defaultdict(list)
    for case in cases:
        buckets[_bucket(case)].append(case)

    random.seed(args.seed)
    picked, leftovers = [], []
    for key in sorted(buckets):
        group = buckets[key][:]
        random.shuffle(group)
        picked.append(group[0])
        leftovers.extend(group[1:])

    if len(picked) > args.number:
        random.shuffle(picked)
        picked = picked[:args.number]
    else:
        random.shuffle(leftovers)
        picked.extend(leftovers[:args.number - len(picked)])

    picked.sort(key=lambda case: case["id"])

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "cases.json"
    with open(out_file, "w", encoding="utf-8") as handle:
        json.dump(picked, handle, ensure_ascii=False)

    covered = sorted({_bucket(case) for case in picked})
    print(f"全量      {len(cases)} 条，{len(buckets)} 个 dtype×档位分层")
    print(f"抽样      {len(picked)} 条，覆盖 {len(covered)} 层")
    print(f"层次      {'、'.join(covered)}")
    print(f"写入      {out_file}")
    if len(covered) < len(buckets):
        missed = sorted(set(buckets) - set(covered))
        print(f"未覆盖    {'、'.join(missed)}（-n 调大可以全覆盖）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
