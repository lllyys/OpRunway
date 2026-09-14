#!/usr/bin/env python3
"""跑 CPU 标杆并把输出冻结成 golden/，供跑测侧 accuracy_load 离线比对。

多输出算子额外查一遍输出顺序（见 `_check_multi_outputs`）：golden 全绿只证明
标杆**能跑**，不证明**算对**，而输出摊反是唯一一类能被静态查出来的算错。

产出 golden/<backend>/<save_name>/<id>/ 的目录树与 golden/manifest.json。
退出码 0 通过，2 标杆执行失败、产出为空或输出顺序对不上，3 输入缺失。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


# 生成侧只要 CPU 版 torch。装了 torch_npu 的机器上 `import torch` 会去自动加载
# 它，没 source CANN 时抛 RuntimeError，连带 atk 也起不来。关掉自动加载，
# 让「生成侧不需要 NPU 与 CANN」这句话成立。必须在任何 torch 导入之前设。
os.environ.setdefault("TORCH_DEVICE_BACKEND_AUTOLOAD", "0")
# 超时按「生成侧全程 ≤ 20 分钟」的预算切分：dry-run 60 + 正式生成 300 +
# golden 冻结 600 = 最坏 16 分钟。实测三个算子全程约 30 秒，留了 30 倍余量。
# **不要因为某个算子跑得慢就调大它**——慢说明用例体积失控，回 S2 调
# max_length 与 dim_values，那才是根因。
TIMEOUT = 600

# ATK dtype -> output_info.json 里的 torch dtype 名。脚本之间不互相 import，
# check_facts.py 里那份是另一个用途（造探针张量），两处各自维护。
ATK_TO_TORCH = {
    "fp64": "torch.float64", "fp32": "torch.float32", "fp16": "torch.float16",
    "bf16": "torch.bfloat16", "int64": "torch.int64", "int32": "torch.int32",
    "int16": "torch.int16", "int8": "torch.int8", "uint8": "torch.uint8",
    "bool": "torch.bool", "complex64": "torch.complex64",
}


def _case_input_dtypes(cases):
    """用例 id -> 第一个输入张量的 ATK dtype，用来解 same_as_input。"""
    table = {}
    for case in cases:
        for item in case.get("inputs", []):
            if isinstance(item, dict) and item.get("type") == "tensor":
                table[str(case.get("id"))] = item.get("dtype")
                break
    return table


def _check_multi_outputs(out_dir, facts, cases):
    """查多输出算子的输出顺序有没有摊反。返回错误行列表，空表示通过。

    摊反了 golden 照样全绿——它跑得出来，只是 output_0 装的是本该在 output_1
    的那个张量。到 NPU 侧才全部比对失败，而报告会把它归成算子缺陷，
    结论完全反向。dtype 不同的多输出算子一比就抓到。
    """
    declared = (facts.get("output") or {}).get("outputs") or []
    if len(declared) < 2:
        return []
    by_id = _case_input_dtypes(cases)
    problems, checked = [], 0
    for info_path in sorted(Path(out_dir).rglob("output_info.json")):
        case_id = info_path.parent.name
        try:
            with open(info_path, encoding="utf-8") as handle:
                actual = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        checked += 1
        if len(actual) != len(declared):
            problems.append(f"用例 {case_id}：golden 有 {len(actual)} 个输出，"
                            f"facts.json 声明 {len(declared)} 个")
            continue
        for index, (want, got) in enumerate(zip(declared, actual)):
            dtype = want.get("dtype")
            if dtype == "same_as_input":
                dtype = by_id.get(case_id)
                if dtype is None:
                    continue
            expect = ATK_TO_TORCH.get(dtype)
            if expect is None or got.get("dtype") == expect:
                continue
            problems.append(f"用例 {case_id} 的 output_{index}（{want.get('name')}）："
                            f"golden 是 {got.get('dtype')}，声明是 {expect}")
        if len(problems) >= 5:
            break
    if not problems and checked:
        print(f"输出顺序  查了 {checked} 条，{len(declared)} 个输出的 dtype 都对得上")
    return problems


def _newest_output(base):
    """atk 把结果写在 <base>/atk_output/<任务名>_<时间戳>/output/。"""
    root = Path(base) / "atk_output"
    if not root.is_dir():
        return None
    tasks = [p for p in root.iterdir() if p.is_dir() and (p / "output").is_dir()]
    if not tasks:
        return None
    return max(tasks, key=lambda p: p.stat().st_mtime) / "output"


def _count_leaves(output_dir):
    """golden 的叶子是 <backend>/<save_name>/<id>/，数它有多少个。"""
    leaves = []
    for backend in sorted(p for p in output_dir.iterdir() if p.is_dir()):
        for save_name in sorted(p for p in backend.iterdir() if p.is_dir()):
            for case_id in sorted(p for p in save_name.iterdir() if p.is_dir()):
                if any(case_id.iterdir()):
                    leaves.append(f"{backend.name}/{save_name.name}/{case_id.name}")
    return leaves


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--cases", default="cases.json")
    parser.add_argument("-o", "--out", default="golden", help="冻结到哪，默认 golden/")
    parser.add_argument("-p", "--plugin", default=None, help="执行器，默认自动找")
    parser.add_argument("--facts", default="facts.json",
                        help="多输出算子靠它查输出顺序，缺了就跳过这项检查")
    parser.add_argument("--keep-raw", action="store_true",
                        help="保留 atk_output/，默认冻结后删掉")
    args = parser.parse_args()

    cases_path = Path(args.cases)
    if not cases_path.exists():
        print(f"{cases_path} 不存在。先跑 gen_cases.py。", file=sys.stderr)
        return 3

    with open(cases_path, encoding="utf-8") as handle:
        cases = json.load(handle)
    expected = len(cases)

    plugin = args.plugin
    if plugin is None:
        matches = sorted(Path(".").glob("function_*.py"))
        plugin = str(matches[0]) if len(matches) == 1 else None

    if not shutil.which("atk"):
        print("atk 不在 PATH。先跑 probe_env.py。", file=sys.stderr)
        return 3

    out_dir = Path(args.out)
    if out_dir.exists():
        shutil.rmtree(out_dir)

    # 只起 cpu 一个节点：生成侧要的是标杆输出，不是比对结论。
    command = ["atk", "node", "--backend", "cpu",
               "task", "-c", str(cases_path), "--task", "accuracy",
               "--save_data", "output"]
    if plugin:
        command += ["-p", plugin]

    print(f"跑标杆：{' '.join(command)}")
    print(f"用例数：{expected}")
    started = time.time()
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        print(f"\n标杆超过 {TIMEOUT}s 没结束。实测 180 条只要 17 秒，超这么多"
              f"说明用例体积失控：回 S2 调小 max_length，或关掉 has_upper_border。\n"
              f"不要调大这里的 TIMEOUT，生成侧全程预算是 20 分钟。", file=sys.stderr)
        return 2
    elapsed = time.time() - started

    stdout = result.stdout or ""
    stderr = result.stderr or ""

    output_dir = _newest_output(".")
    if output_dir is None:
        print(f"\n没找到 atk_output/*/output/。atk 退出码 {result.returncode}。", file=sys.stderr)
        for line in (stderr or stdout).strip().splitlines()[-25:]:
            print(f"  {line}", file=sys.stderr)
        return 2

    leaves = _count_leaves(output_dir)
    if not leaves:
        print("\n标杆一条都没跑出来。最可能的两个原因：", file=sys.stderr)
        print("  1. YAML 的 name 不是可 eval 的 torch 接口名", file=sys.stderr)
        print("  2. 写了执行器但注册名与 api_type 对不上", file=sys.stderr)
        for line in (stderr or stdout).strip().splitlines()[-25:]:
            print(f"  {line}", file=sys.stderr)
        return 2

    shutil.copytree(output_dir, out_dir)
    if not args.keep_raw:
        shutil.rmtree(Path("atk_output"), ignore_errors=True)

    manifest = {
        "cases": expected,
        "golden_cases": len(leaves),
        "backends": sorted({leaf.split("/")[0] for leaf in leaves}),
        "elapsed_seconds": round(elapsed, 1),
        "command": " ".join(command),
    }
    with open(out_dir / "manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)

    ratio = len(leaves) / expected if expected else 0
    print(f"\n标杆产出  {len(leaves)}/{expected} 条（{ratio:.1%}），耗时 {elapsed:.0f}s")
    print(f"冻结到    {out_dir}/")

    facts_path = Path(args.facts)
    if facts_path.exists():
        with open(facts_path, encoding="utf-8") as handle:
            facts = json.load(handle)
        problems = _check_multi_outputs(out_dir, facts, cases)
        if problems:
            print("\n多输出算子的输出顺序对不上：", file=sys.stderr)
            for item in problems:
                print(f"  - {item}", file=sys.stderr)
            print("\n两种可能，都要回 S2 改，不要动 golden：", file=sys.stderr)
            print("  1. CPU 执行器把输出摊反了，核对 function_<op>.py 的返回顺序", file=sys.stderr)
            print("  2. facts.json 的 output.outputs 顺序写错了，核对接口签名", file=sys.stderr)
            return 2
    elif (facts_path.name == "facts.json"):
        print(f"（没找到 {facts_path}，跳过输出顺序检查）")

    if len(leaves) < expected:
        print(f"\n有 {expected - len(leaves)} 条标杆跑不出来。这些用例在 NPU 上也无法比对，"
              f"留着只会变成假失败。回 S2 修 YAML 或执行器，不要把它们剔掉了事。",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
