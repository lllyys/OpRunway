"""量具被跑到时,如果这一阶段的作战卡还没送到过,就补送一次。

卡不再贴在 SKILL.md 里(每次调用都进上下文,五张里四张与手上这一步无关),
只由 `mark_step.py` 在阶段入口渲染。代价是送达从「一定在」变成「取决于
agent 记不记得打卡」,而全仓没有任何门禁依赖 `timeline.jsonl`——忘了打卡
不会有任何东西红,agent 就带着一堆已知会踩的坑往下写。

这里把「记得打卡」从指令变成量具会告诉你的事:
跑某个阶段的量具,就是进入了那个阶段,顺手补记一笔并把卡打出来。

三条自我约束:
- 打到 stderr:有脚本的 stdout 是要被读的,卡混进去会干扰。
- 补记一笔:不记的话同阶段每个量具都会重打一遍卡,那才是真的烧上下文。
- 绝不抛异常:它是附加提示,不能让一个提醒功能把量具跑挂。
"""

import json
import sys
from pathlib import Path

DEFAULT_TIMELINE = "evidence/timeline.jsonl"
HELP_FLAGS = {"-h", "--help"}

# probe_env.py 在接收、用例生成和部署后复测都会运行，不能从骨架里的
# S1 环境产物登记推断它只属于 S1。
CROSS_STAGE_SCRIPTS = {"probe_env.py"}


def stage_of(script_name):
    """从骨架反查这个量具属于哪一阶段,查不出或跨阶段就返回 None。

    映射不硬编码:骨架已经写明每件产物由谁产出、属于哪一阶段,
    再抄一份到这里就是第二张要手工同步的表。
    """
    if script_name in CROSS_STAGE_SCRIPTS:
        return None

    from _contracts import load

    data = load()
    stages = set()
    for spec in data["artifacts"].values():
        if spec.get("producer") == script_name:
            stages.add(spec["stage"])
    for spec in (data.get("gate_inventory") or {}).values():
        if spec["script"] == script_name:
            stages.add(spec["stage"])
    # 一个量具横跨两阶段时不猜:猜错就是在 S3 打出一张 S1 的卡。
    return stages.pop() if len(stages) == 1 else None


def marked_stages(path):
    stages = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            stages.add("S" + str(json.loads(line)["step"]))
    return stages


def _announce(script, timeline):
    # `--help` 不是进入阶段,只是问问怎么用。
    if HELP_FLAGS & set(sys.argv[1:]):
        return
    path = Path(timeline)
    # evidence/ 不存在就说明不在验收工作目录里(跑测试、临时试脚本)。
    # 这时候补记会在别人的目录里造文件,闭嘴才对。
    if not path.parent.is_dir():
        return
    stage = stage_of(Path(script).name)
    if stage is None:
        return

    done = marked_stages(path) if path.exists() else set()
    # 已经打过卡,或者已经跑到更靠后的阶段了。后者是关键:S3 重跑
    # `probe_env.py` 时不该回头补一张 S1 的卡。
    if any(other >= stage for other in done):
        return

    from _contracts import load, render_card
    from mark_step import append_mark

    data = load()
    append_mark(path, stage[1:], data["stages"][stage]["name"])
    print(f"\n[本阶段还没打卡,已补记一笔。{stage} 的作战卡:]\n",
          file=sys.stderr)
    print(render_card(data, stage), file=sys.stderr)


def announce(script, timeline=DEFAULT_TIMELINE):
    """入口:出任何问题都当没发生过。"""
    try:
        _announce(script, timeline)
    except Exception:  # noqa: BLE001 - 提示功能不许影响量具自身的成败
        return
