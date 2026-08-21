"""冻结首轮真实拓扑跑测的 golden，供后续复跑使用。

golden 必须挂在待验收算子后端与 CPU 基线同时在场的真实执行拓扑上。
主节点是 aclnn 时，ATK 不保存被上调过的 output_info；若只跑 CPU 节点，
fp16/bf16 的 output_info 会被保存成 torch.float32，复跑时算子会拒绝输出 dtype。

复跑使用同一份冻结输入，并以 `node -b cpu --task accuracy_load --output_path`
消费本脚本冻结的 golden。

退出码：0 全部冻结；2 golden 缺失或冻结拓扑不正确；3 输入或环境错误。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

from _case_utils import find_input_root as _find_input_root
from _case_utils import file_sha256, iter_cases, load_json
import _stage_card


def fail(message, code=2):
    print(message, file=sys.stderr)
    sys.exit(code)


def find_input_root(atk_output, since):
    """保留原模块入口；目录定位实现由两个冻结脚本共享。"""
    return _find_input_root(atk_output, since)


def golden_path_conflict(baseline_kind):
    """真值来自 CANN 内置实现时，golden 不在这一步冻。

    两条路的产物形状不同：常规验收的 golden 是基线节点当场算出来的，
    冻的是 output_info 的摘要；内置真值是先单独跑一轮存盘再搬过去的目录。
    混用会冻出一份没人消费的摘要，然后拿它当真值来历写进报告。
    """
    if baseline_kind != "cann_builtin":
        return None
    return ("baseline_kind 是 cann_builtin，真值不由这一步产生。\n"
            "  → 走 capture_reference.py：内置那一轮跑完之后取证并搬运，\n"
            "     命令见 references/builtin-baseline.md#两步跑测。")


def freeze_golden(args, wanted, log):
    """冻结首轮跑测的 golden，并校验冻结拓扑正确。"""
    stage = os.path.abspath(args.frozen_dir)
    runs = [os.path.join(stage, "atk_output", name)
            for name in os.listdir(os.path.join(stage, "atk_output"))] \
        if os.path.isdir(os.path.join(stage, "atk_output")) else []
    runs = [path for path in runs if os.path.isdir(os.path.join(path, "output"))]
    if not runs:
        fail("没找到本轮的 output 产物。\n"
             "  → 确认命令里保留了 --save_data output:bin，且跑测确实执行到了比对阶段。", 3)
    output_root = os.path.join(max(runs, key=os.path.getmtime), "output")

    backends = sorted(os.listdir(output_root))
    baseline = [name for name in backends if name.startswith("cpu")]
    candidate = [name for name in backends if not name.startswith("cpu")]
    if not baseline:
        fail(f"output 下没有 cpu 节点目录（现有 {backends}），没有可用的 golden。", 3)
    if not candidate:
        fail(
            f"output 下只有 {backends}，说明这次冻结**不是**真实执行拓扑。\n"
            "  → 主节点不是 aclnn 时，ATK 会把被上调过的 output_info 一并保存"
            "（`opp_tasks.py:316-323` 的 is_save_output_info 保护不生效）。\n"
            "     用这份 golden 复跑，fp16/bf16 用例会在 GetWorkspaceSize 阶段被算子拒绝。\n"
            "     正确做法是把 freeze_golden.py 挂在待验收算子后端与 cpu "
            "同时在场的那次跑测上。", 2)

    node = baseline[0]
    case_root = os.path.join(output_root, node)
    frozen, missing = {}, []
    for case_id in wanted:
        found = None
        for save_name in os.listdir(case_root):
            probe = os.path.join(case_root, save_name, case_id, "output_info.json")
            if os.path.exists(probe):
                found = probe
                break
        if not found:
            missing.append(case_id)
            continue
        info = load_json(found)
        first = info[0][0] if isinstance(info[0], list) else info[0]
        frozen[case_id] = {
            "output_info_sha256": file_sha256(found),
            "dtype": first.get("dtype"),
        }

    report = {
        "mode": "golden",
        "case_json": args.case_json,
        "case_json_sha256": file_sha256(args.case_json),
        "golden_dir": output_root,
        "baseline_node": node,
        "candidate_nodes": candidate,
        "total": len(wanted),
        "frozen": len(frozen),
        "missing_ids": missing,
        "cases": frozen,
    }
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    print(f"已冻结 golden {len(frozen)}/{len(wanted)} 条（标杆节点 {node}，"
          f"待验收算子节点 {candidate}）→ {output_root}")
    print(f"摘要写入 {args.output}")
    print("复跑时加：node -b cpu --task accuracy_load --output_path " + output_root)

    if missing:
        print(f"\n✗ {len(missing)} 条用例没有 golden：{missing[:12]}", file=sys.stderr)
        return 2
    return 0


def main():
    parser = argparse.ArgumentParser(description="冻结首轮真实拓扑跑测的 golden")
    parser.add_argument("-j", "--case-json", required=True)
    parser.add_argument("--atk-cli", required=True, help="probe_env.py 探出的绝对路径")
    parser.add_argument("-d", "--frozen-dir", default="frozen_inputs",
                        help="本轮真实拓扑跑测与 golden 的落盘目录；会被清空重建")
    parser.add_argument("-p", "--plugin", help="执行插件路径")
    parser.add_argument("--device", help="待验收算子节点使用的 device")
    parser.add_argument("--backend", default="pyaclnn", help="待验收算子后端")
    parser.add_argument("--input-data", help="消费的已冻结输入目录")
    parser.add_argument("--interface", default="evidence/interface.json",
                        help="derive_interface.py 的产物；读 baseline_kind")
    parser.add_argument("-o", "--output", default="frozen_inputs.json")
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    _stage_card.announce(__file__)

    cases = list(iter_cases(load_json(args.case_json)))
    if not cases:
        fail("用例集为空，没有可冻结 golden 的用例。")
    wanted = [str(case.get("id")) for case in cases]

    baseline_kind = "torch"
    if os.path.exists(args.interface):
        with open(args.interface, encoding="utf-8") as handle:
            baseline_kind = json.load(handle).get("baseline_kind", "torch")
    conflict = golden_path_conflict(baseline_kind)
    if conflict:
        fail(conflict, 2)
    if not args.device:
        fail("需要 --device：golden 必须在真实执行拓扑下冻结。", 3)
    if not args.input_data:
        fail("需要 --input-data：golden 要与已冻结的输入配对。", 3)

    stage_dir = os.path.abspath(args.frozen_dir)
    shutil.rmtree(stage_dir, ignore_errors=True)
    os.makedirs(stage_dir, exist_ok=True)
    command = [args.atk_cli,
               "node", "-b", args.backend, "--devices", str(args.device),
               "-o", stage_dir,
               "node", "-b", "cpu", "-o", stage_dir,
               "task", "-c", args.case_json, "-tk", "accuracy",
               "--input_data", args.input_data,
               "--save_data", "output:bin"]
    if args.plugin:
        command += ["-p", args.plugin]
    print(f"冻结 {len(wanted)} 条用例的 golden，使用真实执行拓扑：")
    print("  " + " ".join(command))

    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=args.timeout, env=dict(os.environ))
    except FileNotFoundError:
        fail(f"找不到 ATK CLI {args.atk_cli}，用 probe_env.py 输出的绝对路径。", 3)
    except subprocess.TimeoutExpired:
        fail(f"golden 冻结超过 {args.timeout}s 未结束。", 3)

    log = re.sub(r"\x1b\[[0-9;]*m", "", done.stdout + done.stderr)
    return freeze_golden(args, wanted, log)


if __name__ == "__main__":
    sys.exit(main())
