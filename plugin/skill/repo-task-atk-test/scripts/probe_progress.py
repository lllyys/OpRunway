"""从工作目录已落盘的产物反推当前子 skill 与阶段，并重打当阶段的卡。

一轮验收要几十次工具调用，中途上下文会被压缩。压缩之后 SKILL.md 和读过的
reference 都不在上下文里了，agent 只剩一份摘要，于是二选一：把 reference
重读一遍（一轮的文档开销翻倍），或者凭摘要往下写（判据全靠记忆，红线 4 失效）。

两条都不用走：阶段进展在盘上是可核验的，产物在不在自己会说话。
本脚本不读任何声明、不信任何「我做过了」，只看文件存不存在。

用法：
    probe_progress.py [-C <工作目录>] [--skill {case-gen,acceptance}]

不指定 --skill 时，根据交接包封印与接收证据自动判断运行侧。

退出码：0 推出了当前阶段；2 工作目录不存在；3 骨架不可用。
"""

import argparse
import json
import sys
from pathlib import Path

from _contracts import ContractError, artifacts_of, load, render_card, stages_of

# 骨架里的产物名是给人读的形态，`<op>` 这类占位要换成能 glob 的写法。
PLACEHOLDERS = (("<op>", "*"), ("<side>", "*"), ("<接口分面>", "*"))

# 不是单个文件、盘上认不出来的产物：报出来但不参与判定。
# 把「认不出来」说成「缺」，agent 会回头重做已经做完的一步。
MANUAL = {"验收报告", "复现包"}

# 名字不是路径，但盘上有固定形态的产物。
SPECIAL = {"冻结输入": "frozen_*"}

# 自动判断时，封印已落盘但接收证据尚未落盘是两个子 skill 之间的交接态。
SEALED_HANDOFF = "sealed-handoff"


def pattern_of(name):
    if name in SPECIAL:
        return SPECIAL[name]
    for placeholder, glob in PLACEHOLDERS:
        name = name.replace(placeholder, glob)
    return name


def exists(root, name):
    return bool(list(root.glob(pattern_of(name))))


def read_json(root, relative):
    path = root / relative
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def condition_holds(root, spec):
    """条件产物到底要不要产出，从已落盘的证据里读，读不出来就说读不出来。

    返回 True 要产出、False 不产出、None 判不了（依据本身还没落盘）。
    """
    condition = spec.get("condition") or ""
    if "cann_builtin" in condition:
        interface = read_json(root, "evidence/interface.json")
        if interface is None:
            return None
        return interface.get("baseline_kind") == "cann_builtin"
    if "baseline_adapter.required" in condition:
        alignment = read_json(root, "evidence/signature_alignment.json")
        if alignment is None:
            return None
        return bool((alignment.get("baseline_adapter") or {}).get("required"))
    return None


def infer_skill(root):
    """从交接证据判断当前运行侧；封印后、接收前返回交接特殊态。"""
    evidence = root / "evidence"
    if (evidence / "bundle_intake.json").exists():
        return "acceptance"
    if (evidence / "bundle.json").exists():
        return SEALED_HANDOFF
    return "case-gen"


def survey(root, data, stages=None):
    """逐阶段核对产物在不在，返回 {阶段: (齐的, 缺的, 判不了的, 人工确认的)}。"""
    if stages is None:
        stages = stages_of(data, "case-gen")
    result = {}
    for stage in stages:
        have, missing, undecided, manual = [], [], [], []
        for name, spec in artifacts_of(data, stage).items():
            if name in MANUAL:
                manual.append(name)
                continue
            present = exists(root, name)
            if present:
                have.append(name)
                continue
            if spec.get("condition"):
                holds = condition_holds(root, spec)
                if holds is None:
                    undecided.append(name)
                    continue
                if not holds:
                    continue
            missing.append(name)
        result[stage] = (have, missing, undecided, manual)
    return result


def current_stage(data, table, stages):
    """第一个还有产物没落盘的阶段就是当前阶段。

    往前找而不是往后找：S3 失败退回时 S4 目录可能有上一轮的残留，
    按「最后一个有产物的阶段」判会把人送到还没到的阶段去。
    """
    for stage in stages:
        have, missing, _, manual = table[stage]
        if missing or (not have and not manual):
            return stage
    return stages[-1]


def report(root, data, table, stage, skill, stages, sealed=False):
    status = "（已封印）" if sealed else ""
    lines = [f"子 skill：{skill}{status}　工作目录：{root}", ""]
    for name in stages:
        have, missing, undecided, manual = table[name]
        total = len(have) + len(missing)
        head = f"{name} {data['stages'][name]['name']}　{len(have)}/{total} 齐"
        detail = []
        if missing:
            detail.append("缺 " + "、".join(missing))
        if undecided:
            detail.append("条件未定 " + "、".join(undecided))
        if manual:
            detail.append("人工确认 " + "、".join(manual))
        lines.append(head + ("　" + "；".join(detail) if detail else ""))
    lines += ["", f"当前阶段：{stage} {data['stages'][stage]['name']}", ""]
    lines.append(render_card(data, stage))
    lines += ["",
              "本表只答「产物在不在」，不答「对不对」：上一轮留下的产物同样算齐。",
              "正在返工某阶段时以你自己的判断为准，别让这张表把你推到下一阶段。",
              "压缩后只读三样：evidence/constraints.md、evidence/interface.json，"]
    lines += ["以及卡里为当前那件产物点名的那份 reference。",
              "产物齐了的阶段就是过了，不重做、不重新生成。"]
    return "\n".join(lines)


def sealed_handoff_report(root, data):
    """生成侧封印后只交付验收侧 S0 卡，不再把后续产物报成缺失。"""
    lines = ["子 skill：acceptance　case-gen 已完成（已封印），验收侧当前 S0",
             f"工作目录：{root}", "", render_card(data, "S0")]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="从已落盘的产物反推当前子 skill 与阶段，并重打当阶段作战卡")
    parser.add_argument("-C", "--dir", default=".", help="运行工作目录，默认当前目录")
    parser.add_argument(
        "--skill", choices=("case-gen", "acceptance"),
        help="显式选择子 skill；默认根据 bundle.json 与 bundle_intake.json 自动判断",
    )
    args = parser.parse_args()

    root = Path(args.dir).resolve()
    if not root.is_dir():
        print(f"工作目录不存在：{root}", file=sys.stderr)
        return 2
    try:
        data = load()
    except ContractError as exc:
        print(f"骨架不可用：{exc}", file=sys.stderr)
        return 3

    if args.skill is None:
        skill = infer_skill(root)
        if skill == SEALED_HANDOFF:
            print(sealed_handoff_report(root, data))
            return 0
    else:
        skill = args.skill
    stages = stages_of(data, skill)
    table = survey(root, data, stages)
    stage = current_stage(data, table, stages)
    sealed = skill == "case-gen" and (root / "evidence" / "bundle.json").exists()
    print(report(root, data, table, stage, skill, stages, sealed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
