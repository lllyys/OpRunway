#!/usr/bin/env python3
"""跑 CPU 标杆并把输出冻结成 golden/，供跑测侧 accuracy_load 离线比对。

产出 golden/<backend>/<save_name>/<id>/ 的目录树与 golden/manifest.json。
退出码 0 通过，2 标杆执行失败或产出为空，3 输入缺失。
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

TIMEOUT = 3600


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
        print(f"\n标杆超过 {TIMEOUT}s 没结束。用例里有超大 shape，"
              f"回 S2 调小 max_length 或关掉 has_upper_border。", file=sys.stderr)
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

    if len(leaves) < expected:
        print(f"\n有 {expected - len(leaves)} 条标杆跑不出来。这些用例在 NPU 上也无法比对，"
              f"留着只会变成假失败。回 S2 修 YAML 或执行器，不要把它们剔掉了事。",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
