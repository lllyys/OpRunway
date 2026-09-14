#!/usr/bin/env python3
"""跑 atk case 生成用例，收敛到工作目录的 cases.json。

--dry-run 用 dtype_numbers=1 快速验证 YAML 与插件能不能组合出合法用例。
退出码 0 通过，2 atk case 失败或用例数不足，3 输入文件缺失。
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path


# 生成侧只要 CPU 版 torch。装了 torch_npu 的机器上 `import torch` 会去自动加载
# 它，没 source CANN 时抛 RuntimeError，连带 atk 也起不来。关掉自动加载，
# 让「生成侧不需要 NPU 与 CANN」这句话成立。必须在任何 torch 导入之前设。
os.environ.setdefault("TORCH_DEVICE_BACKEND_AUTOLOAD", "0")
# 超时按「生成侧全程 ≤ 20 分钟」的预算切分：dry-run 60 + 正式生成 300 +
# golden 冻结 600 = 最坏 16 分钟。实测三个算子全程约 30 秒，留了 30 倍余量。
# **不要因为某个算子跑得慢就调大它**——慢说明用例体积失控，回 S2 调
# max_length 与 dim_values，那才是根因。
DRY_RUN_TIMEOUT = 60
FULL_RUN_TIMEOUT = 300
MIN_CASES = 100


def _find_yaml(explicit):
    if explicit:
        path = Path(explicit)
        if not path.exists():
            print(f"{path} 不存在", file=sys.stderr)
            return None
        return path
    candidates = sorted(Path(".").glob("*.yaml")) + sorted(Path(".").glob("*.yml"))
    candidates = [p for p in candidates if p.name != "nodes.yaml"]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        print("工作目录下没有 YAML。按 references/yaml-authoring.md 先写 <op>.yaml。",
              file=sys.stderr)
    else:
        names = "、".join(p.name for p in candidates)
        print(f"工作目录下有多个 YAML（{names}），用 -f 点名要哪个。", file=sys.stderr)
    return None


def _find_plugin(yaml_path):
    """约束器按 <stem>_constraint.py 找，找不到就不传 -p。"""
    guess = Path(f"{yaml_path.stem}_constraint.py")
    if guess.exists():
        return guess
    matches = sorted(Path(".").glob("*_constraint.py"))
    return matches[0] if len(matches) == 1 else None


def _dtype_of(case):
    for item in case.get("inputs", []):
        if item.get("type") == "tensor" and item.get("dtype"):
            return item["dtype"]
    return "?"


# 规模按**字节数**分档，与 references/case-strategy.md「规模档」同一套阈值。
# 不按元素数：262144 个元素在 fp32 是 1 MB、在 int8 只有 256 KB，
# 而决定 UB 装不装得下、要不要多核切分的是字节数。
SMALL_BYTES = 32 * 1024
MEDIUM_BYTES = 2 * 1024 * 1024

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


def _size_band(case):
    """单元素单独一档：它是退化边界，和「小张量」不是一回事。"""
    tensor = _first_tensor(case)
    shape = tensor.get("shape") or []
    numel = 1
    for dim in shape:
        numel *= dim
    if numel <= 1:
        return "scalar"
    total = numel * DTYPE_BYTES.get(tensor.get("dtype"), 4)
    if total < SMALL_BYTES:
        return "small"
    if total < MEDIUM_BYTES:
        return "medium"
    return "large"


PERF_BAND_TARGET = {"small": 0.4, "medium": 0.3, "large": 0.3}


def write_perf_subset(cases, out_dir, number, seed):
    """抽性能子集：**先按规模档配额，再按 dtype 补齐**，落盘成 <out_dir>/cases.json。

    配额优先的理由：性能轮要覆盖的是切分路径——UB 装不下时的循环切分、
    多核切分、尾核处理，那是**规模**决定的，不是 dtype。纯按 dtype × 档位
    均分桶时 large 桶数少，全量里有十几条也只进得去两三条。

    配额按目标占比算，某档不够就有多少拿多少，缺口留给别的档补齐。
    **不追全量里的 large 占比**：精度轮追 30% large 意味着几十条数 MB 的张量，
    golden 要几百 MB、冻结时间也上去，而 tiling 边界缺陷在 2^n±1 的小张量上
    就能暴露。全量只要够填满子集配额即可。

    **文件名固定叫 cases.json，换目录不换名。** ATK 拿用例文件基名当 golden 的
    子目录名（`atk/tasks/result_process.py:67`），改名后跑测侧找不到 golden。
    同一份 golden 因此被全量与子集共用，不用重造。
    """
    by_band = defaultdict(list)
    for case in cases:
        by_band[_size_band(case)].append(case)

    random.seed(seed)
    picked, taken = [], set()
    for band, share in PERF_BAND_TARGET.items():
        pool = [c for c in by_band.get(band, []) if id(c) not in taken]
        random.shuffle(pool)
        quota = round(number * share)
        for case in pool[:quota]:
            picked.append(case)
            taken.add(id(case))

    # 配额没填满就按 dtype × 档位补齐，顺带把 scalar 档带进来
    if len(picked) < number:
        rest = [c for c in cases if id(c) not in taken]
        buckets = defaultdict(list)
        for case in rest:
            buckets[f"{_first_tensor(case).get('dtype', '?')}/{_size_band(case)}"].append(case)
        spare = []
        for key in sorted(buckets):
            group = buckets[key][:]
            random.shuffle(group)
            picked.append(group[0])
            taken.add(id(group[0]))
            spare.extend(group[1:])
        random.shuffle(spare)
        picked.extend(spare[:number - len(picked)])
    picked = picked[:number]
    picked.sort(key=lambda case: case["id"])

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "cases.json", "w", encoding="utf-8") as handle:
        json.dump(picked, handle, ensure_ascii=False)

    bands = Counter(_size_band(case) for case in picked)
    source = Counter(_size_band(case) for case in cases)
    print(f"性能子集  {len(picked)} 条 -> {out_dir}/cases.json")
    print("规模档    " + "、".join(
        f"{band}:{bands.get(band, 0)}" for band in ("scalar", "small", "medium", "large")))
    thin = [band for band in PERF_BAND_TARGET
            if bands.get(band, 0) < round(number * PERF_BAND_TARGET[band] * 0.5)]
    if thin:
        detail = "、".join(f"{band}:{source.get(band, 0)}" for band in thin)
        print(f"抽不够    {'、'.join(thin)} 档不到配额的一半 —— 全量里只有 {detail}。"
              f"\n          性能结论不覆盖这些档的切分路径。全量里就没有，"
              f"调大 --perf-number 没用。回 S2 按这个顺序试："
              f"YAML 配 size_distributions -> 加大 max_length -> 给 dim_values 加大值。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-f", "--yaml", default=None, help="用例设计 YAML，默认自动找")
    parser.add_argument("-p", "--plugin", default=None, help="约束器，默认自动找")
    parser.add_argument("-o", "--out", default="cases.json", help="收敛到哪，默认 cases.json")
    parser.add_argument("--perf-dir", default="perf",
                        help="性能子集落在哪，里面固定写 cases.json")
    parser.add_argument("--perf-number", type=int, default=50,
                        help="性能子集条数，默认 50")
    parser.add_argument("--perf-seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true",
                        help="用 dtype_numbers=1 快速验证，不产出 cases.json")
    args = parser.parse_args()

    yaml_path = _find_yaml(args.yaml)
    if yaml_path is None:
        return 3

    plugin = Path(args.plugin) if args.plugin else _find_plugin(yaml_path)
    if args.plugin and not plugin.exists():
        print(f"{plugin} 不存在", file=sys.stderr)
        return 3

    if not shutil.which("atk"):
        print("atk 不在 PATH。先跑 probe_env.py 确认环境。", file=sys.stderr)
        return 3

    command = ["atk", "case", "-f", str(yaml_path)]
    if plugin:
        command += ["-p", str(plugin)]
    if args.dry_run:
        command += ["-dt", "1", "-en", "0"]

    label = "dry-run" if args.dry_run else "正式生成"
    print(f"{label}：{' '.join(command)}")
    if plugin:
        print(f"约束器：{plugin}")
    else:
        print("约束器：无（generate 应为 default）")

    timeout = DRY_RUN_TIMEOUT if args.dry_run else FULL_RUN_TIMEOUT
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        print(f"\natk case 超过 {timeout}s 没结束。"
              f"多半是 dim_values 写成了 range，ATK 在按笛卡尔积展开。"
              f"改成离散列表再来。", file=sys.stderr)
        return 2

    if result.returncode != 0:
        print(f"\natk case 退出码 {result.returncode}：", file=sys.stderr)
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        for line in tail[-25:]:
            print(f"  {line}", file=sys.stderr)
        return 2

    produced = Path("result") / yaml_path.stem / "json" / f"all_{yaml_path.stem}.json"
    if not produced.exists():
        print(f"\natk case 退出码 0 但没写出 {produced}。"
              f"检查 YAML 的 name 与 aclnn_name 是否为空。", file=sys.stderr)
        return 2

    with open(produced, encoding="utf-8") as handle:
        cases = json.load(handle)

    count = len(cases)
    spread = Counter(_dtype_of(case) for case in cases)
    print(f"\n用例数    {count}")
    print(f"dtype     {'、'.join(f'{k}×{v}' for k, v in sorted(spread.items()))}")

    if args.dry_run:
        print(f"\ndry-run 通过。回填 dtype_numbers 后去掉 --dry-run 正式生成。")
        return 0

    if count < MIN_CASES:
        print(f"\n用例数 {count} 少于 {MIN_CASES}。这是 YAML 写窄了，"
              f"回 S2 加 dim_values 取值或 dtype，不要只调大 dtype_numbers。",
              file=sys.stderr)
        return 2

    shutil.copyfile(produced, args.out)
    print(f"收敛到  {args.out}")
    print()
    write_perf_subset(cases, args.perf_dir, args.perf_number, args.perf_seed)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    sys.exit(main())
