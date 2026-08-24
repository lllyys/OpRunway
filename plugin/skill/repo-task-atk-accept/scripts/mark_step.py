"""记录流程时间线，收尾时输出耗时归因表。

「哪一步慢」这个问题以前只能事后翻日志刨。实测一轮验收里约束确认花了 46 分钟、
用例生成只花 11 分钟——这个结论当时是人工刨出来的，用户全程只知道「很慢」，
不知道该优化哪。所以把每步的进入时刻记成一条流水，收尾直接算。

只记时间，不记状态：本文件不参与断点续跑，删掉不影响任何门禁。

用法：
    mark_step.py -o <timeline.jsonl> <步号> <步名>   进入某一步时记一笔
    mark_step.py -o <timeline.jsonl> --summary       输出耗时表

退出码：0 完成；2 时间线为空或格式损坏。
"""

import argparse
import json
import sys
import time
import unicodedata
from pathlib import Path

from _contracts import ContractError, load, render_card


def stage_card(step):
    """步号对上阶段号就把当阶段作战卡打出来。

    卡不写进 SKILL.md：那是「开场一次性给全」，不等于「在合适的时机知道」，
    而且 SKILL.md 每次调用都进上下文，五张卡有四张与手上这一步无关。
    打点是阶段推进的必经动作，让它把该阶段的作战指令送到眼前，
    只花一张卡的上下文，且由骨架渲染，不可能与规范脱节。
    """
    stage = f"S{step}"
    try:
        data = load()
    except ContractError as exc:
        # 骨架坏了不该让打点失败——打点不参与任何门禁，卡是附加价值。
        # 但静默吞掉会让 agent 看不到卡为什么没出现，先报个信号再放行。
        print(f"[mark_step] 作战卡骨架读取失败，本次不打印卡：{exc}", file=sys.stderr)
        return None
    if stage not in data["stages"]:
        return None
    return render_card(data, stage)


def human(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{seconds:02d}s"


def read_marks(path):
    if not Path(path).exists():
        return []
    marks = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            marks.append(json.loads(line))
    return marks


def append_mark(path, step, name):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    mark = {"step": step, "name": name, "at": time.time()}
    with Path(path).open("a", encoding="utf-8") as sink:
        sink.write(json.dumps(mark, ensure_ascii=False) + "\n")
    return mark


def durations(marks, now=None):
    """相邻两笔之间的间隔就是前一步的耗时；最后一步用当前时刻收口。"""
    now = time.time() if now is None else now
    rows = []
    for index, mark in enumerate(marks):
        end = marks[index + 1]["at"] if index + 1 < len(marks) else now
        rows.append({"step": mark["step"], "name": mark["name"],
                     "seconds": max(0.0, end - mark["at"])})
    return rows


def display_width(text):
    """终端里中日韩字符占两列，用 len 对齐会把整张表排歪。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1
               for char in text)


def pad(text, width):
    return text + " " * max(0, width - display_width(text))


def summarize(marks, now=None):
    rows = durations(marks, now)
    total = sum(row["seconds"] for row in rows) or 1.0
    width = max((display_width(f"{row['step']} {row['name']}") for row in rows),
                default=8)
    width = max(width, display_width("合计"))
    lines = []
    for row in rows:
        label = pad(f"{row['step']} {row['name']}", width)
        share = row["seconds"] / total * 100
        # 只给占比不给绝对值，人就无法判断值不值得优化；两个都给
        bar = "#" * max(0, round(share / 4))
        lines.append(f"  {label}  {human(row['seconds']):>7}  {share:4.0f}%  {bar}")
    lines.append(f"  {pad('合计', width)}  {human(total):>7}")
    return lines


def main():
    parser = argparse.ArgumentParser(description="记录流程时间线并输出耗时表")
    parser.add_argument("-o", "--output", required=True, help="timeline.jsonl 路径")
    parser.add_argument("--summary", action="store_true", help="输出耗时表")
    parser.add_argument("step", nargs="?", help="步号")
    parser.add_argument("name", nargs="?", help="步名")
    args = parser.parse_args()

    if args.summary:
        marks = read_marks(args.output)
        if not marks:
            print(f"时间线为空：{args.output}", file=sys.stderr)
            return 2
        print("耗时归因：")
        for line in summarize(marks):
            print(line)
        return 0

    if args.step is None or args.name is None:
        print("记一笔需要给出步号和步名，例如：mark_step.py -o t.jsonl 3 设计用例与插件",
              file=sys.stderr)
        return 2

    marks = read_marks(args.output)
    if marks:
        previous = durations(marks)[-1]
        print(f"第 {previous['step']} 步 {previous['name']} 用时 "
              f"{human(previous['seconds'])}")
    append_mark(args.output, args.step, args.name)
    print(f"→ 第 {args.step} 步 {args.name}")
    card = stage_card(args.step)
    if card:
        print()
        print(card)
    return 0


if __name__ == "__main__":
    sys.exit(main())
