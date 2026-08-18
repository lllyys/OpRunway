"""打包实际验收材料。

输入：接口事实、裁决结论、命令日志和验收文件。
输出：tar.gz 复现包。
退出码：0 完成；2 缺少必要材料。
"""

import argparse
import json
import os
import sys
import tarfile
import time
from pathlib import Path

from _policy import default_policy_path, load_policy, policy_sha256
import _stage_card


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_readme(interface, verdict, cmd_log_name, members):
    policy = load_policy()

    lines = [
        f"# {verdict.get('op') or '未声明'} 验收复现包",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 这次测的是什么",
        "",
        f"- 接口模式：{interface.get('interface_mode') or '未声明'}",
        f"- 实际 ATK 后端：{interface.get('execution_backend') or '未声明'}",
        f"- 待验收算子的接口名：{interface.get('candidate_symbol') or '未声明'}",
        f"- 精度基线接口：{interface.get('baseline_api') or '未声明'}",
        f"- 模式与基线的依据：{interface.get('mode_source') or '未声明'}",
        f"- 验收政策版本：{policy.get('schema_version')}",
        f"- 验收政策摘要：{policy_sha256()}",
        "",
        "接口模式决定待验收对象。换一个后端跑，ATK 不会报错，但测的可能是 CANN 内置实现",
        "而不是本工程——复跑时请保持 " + cmd_log_name + " 里的后端参数不变。",
        "",
        "## 结论",
        "",
        f"- {verdict.get('conclusion') or '未声明'}：{verdict.get('reason') or ''}",
    ]
    if verdict.get("judged_outputs"):
        lines += [
            f"- 只判定这些输出：{', '.join(verdict['judged_outputs'])}",
            f"- 排除其余输出的依据：{verdict.get('excluded_outputs_reason')}",
        ]

    lines += [
        "",
        "## 环境指纹",
        "",
        "复现环境与下表不一致时，数值可能有出入，结论以本表环境为准：",
        "",
    ]
    # probe_env.py 产出的 fingerprint 是给人看的那几行；整份 env.json
    # 大半是探测过程，原样铺进 README 会把真正要看的淹掉。
    full_env = verdict.get("env") or {}
    env = full_env.get("fingerprint") or full_env
    if env:
        lines += [f"- {k}：{v}" for k, v in env.items()]
    else:
        lines.append("- （verdict.json 里没有环境指纹，这是缺陷，请向验收方索取）")

    lines += [
        "",
        "## 怎么跑",
        "",
        "```bash",
        f"bash {cmd_log_name}",
        "```",
        "",
        f"`{cmd_log_name}` 是验收时**实际执行过**的命令，按步骤顺序排列，不是事后补写的。",
        "逐条照抄也可以，用于只复现某一步。",
        "",
        "`cases/` 下是验收时固化的用例集。执行复现直接使用该文件。",
        "不要再次运行 `atk case`。",
        "",
        "详细用例数、通过率和归因见 `verdict.json`。",
        "",
        "## 包内文件",
        "",
    ]
    lines += [f"- `{name}`" for name in members]
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="打包人工复现包")
    parser.add_argument("--interface", required=True,
                        help="derive_interface.py 产出的 evidence/interface.json")
    parser.add_argument("--verdict", required=True, help="verdict.py 产出的 verdict.json")
    parser.add_argument("-j", "--case-json", required=True, help="用例集 JSON")
    parser.add_argument("--cmd-log", required=True,
                        help="命令日志，约定 evidence/repro.sh，边执行边追加")
    parser.add_argument("-y", "--yaml", action="append", default=[],
                        help="YAML 设计文件，拆分成多份时重复传")
    parser.add_argument("-p", "--constraint", help="constraint.py")
    parser.add_argument("-f", "--plugin", action="append", default=[],
                        help="执行插件 function_*.py；没写执行器时不传")
    parser.add_argument("--must-cover", action="append", default=[],
                        help="must_cover*.json，constraint 依赖它才能复跑")
    parser.add_argument("--env", help="probe_env.py 的产出 env.json")
    parser.add_argument("-x", "--excluded-cases", help="无效用例记录")
    parser.add_argument("--extra", action="append", default=[],
                        help="其他要一并交付的文件，可重复")
    parser.add_argument("-o", "--output", required=True, help="输出 tar.gz 路径")
    args = parser.parse_args()
    _stage_card.announce(__file__)

    interface = load(args.interface)
    verdict = load(args.verdict)

    problems = []
    if not interface.get("interface_mode"):
        problems.append("interface.json 缺 interface_mode，复现包说不清测的是什么对象。")
    if not interface.get("execution_backend"):
        problems.append("interface.json 缺 execution_backend，复现包无法固定实际后端。")
    if not os.path.exists(args.case_json):
        problems.append(f"用例集不存在：{args.case_json}")

    # (归档内路径, 磁盘路径)。用例集统一放 cases/，其余保持原文件名，
    # 让开发者解压后目录结构与验收方工作目录一致，命令能直接跑。
    plan = []

    def add(disk_path, arc_name=None, required=True, label=""):
        if not disk_path:
            return
        if not os.path.exists(disk_path):
            if required:
                problems.append(f"{label or disk_path} 不存在：{disk_path}")
            return
        plan.append((arc_name or os.path.basename(disk_path), disk_path))

    add(args.cmd_log, os.path.basename(args.cmd_log), label="命令日志")
    for path in args.yaml:
        add(path, label="YAML")
    add(args.constraint, required=False, label="constraint")
    for path in args.plugin:
        add(path, required=False, label="执行插件")
    for path in args.must_cover:
        add(path, required=False, label="must_cover")
    add(args.env, "env.json", required=False, label="环境指纹")
    add(args.excluded_cases, "excluded_cases.json", required=False, label="无效用例记录")
    add(args.interface, "interface.json", label="接口事实")
    add(args.verdict, "verdict.json", label="裁决结论")
    add(str(default_policy_path()), "acceptance-policy.json", label="验收政策")
    add(args.case_json, f"cases/{os.path.basename(args.case_json)}", label="用例集")

    for path in args.extra:
        add(path, required=False, label="附加文件")

    if problems:
        print("打包前先解决这些问题：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    cmd_log_name = os.path.basename(args.cmd_log)
    readme = build_readme(interface, verdict, cmd_log_name,
                          sorted(name for name, _ in plan) + ["README.md"])

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    root = os.path.basename(args.output).split(".tar")[0]

    with tarfile.open(args.output, "w:gz") as tar:
        for arc_name, disk_path in plan:
            tar.add(disk_path, arcname=f"{root}/{arc_name}")
        readme_path = os.path.join(os.path.dirname(os.path.abspath(args.output)),
                                   ".README.md.tmp")
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(readme)
        tar.add(readme_path, arcname=f"{root}/README.md")
        os.remove(readme_path)

    size_kb = os.path.getsize(args.output) / 1024
    print(f"复现包 → {args.output}（{size_kb:.1f} KB）")
    for arc_name, _ in sorted(plan):
        print(f"  {root}/{arc_name}")
    print(f"  {root}/README.md")

    missing = []
    if not args.plugin:
        missing.append("执行插件（没写执行器时正常）")
    if not args.env:
        missing.append("env.json（环境指纹只剩 verdict.json 里那份）")
    if missing:
        print("\n未包含：" + "；".join(missing), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
