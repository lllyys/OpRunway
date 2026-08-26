#!/usr/bin/env python3
"""跑 atk case 生成用例，收敛到工作目录的 cases.json。

--dry-run 用 dtype_numbers=1 快速验证 YAML 与插件能不能组合出合法用例。
退出码 0 通过，2 atk case 失败或用例数不足，3 输入文件缺失。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

DRY_RUN_TIMEOUT = 120
FULL_RUN_TIMEOUT = 1800
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-f", "--yaml", default=None, help="用例设计 YAML，默认自动找")
    parser.add_argument("-p", "--plugin", default=None, help="约束器，默认自动找")
    parser.add_argument("-o", "--out", default="cases.json", help="收敛到哪，默认 cases.json")
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
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONWARNINGS", "ignore")
    sys.exit(main())
