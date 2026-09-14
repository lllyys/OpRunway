#!/usr/bin/env python3
"""从骨架渲染派生视图。骨架是唯一可编辑对象，视图不手工维护。

默认打印到 stdout，--write 才落盘。测试拿 stdout 与磁盘上的文件比对，
两者不等就说明有人手改了视图。
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SPINE = SKILL_ROOT / "references" / "taskdoc-elements.json"
CHECKLIST = SKILL_ROOT / "references" / "manual-checklist.md"

# xlsx 十四条的原文，来源 docs/development/taskdoc-source/origin/checklist-14.md
ITEMS = {
    1: "检查章节完整性", 2: "任务书标题", 3: "任务概述", 4: "功能要求",
    5: "算子工程模式", 6: "接口定义", 7: "参数说明", 8: "软硬件环境要求",
    9: "精度要求", 10: "性能要求", 11: "内存要求", 12: "自验要求",
    13: "验收交付件", 14: "PR申请合入",
}

# 这几条的语义部分脚本判不了：对不对要人读，不是有没有。
MANUAL = {2: "标题与任务发放纪要的任务名是否一致",
          4: "算法逻辑或计算公式是否正确",
          9: "精度阈值是否覆盖了所有输入场景",
          12: "自验策略是否真的可行",
          13: "交付件要求是否恰当"}


def render():
    spine = json.loads(SPINE.read_text(encoding="utf-8"))
    covered = defaultdict(list)
    for key, element in spine["elements"].items():
        for item in element["checklist_items"]:
            covered[item].append((key, element))

    out = ["# 任务书人工检查清单", "",
           "本文件由 `scripts/render_views.py` 从 "
           "`references/taskdoc-elements.json` 渲染，不要手改。",
           "改内容请改骨架再重新渲染。", "",
           "源清单是 `任务书人工checklist.xlsx` Sheet1 的十四条，"
           "归档在 `docs/development/taskdoc-source/origin/checklist-14.md`。", "",
           "## 已被机械判据覆盖", "",
           "这几条不需要人读，`check_taskdoc.py` 会裁决。", "",
           "| 序号 | 检查项 | 由哪些判据覆盖 |", "| --- | --- | --- |"]
    for item in sorted(ITEMS):
        if item in MANUAL:
            continue
        checks = sorted({c.split(":")[0]
                         for _, element in covered.get(item, [])
                         for c in element["checks"]})
        out.append(f"| {item} | {ITEMS[item]} | {'、'.join(checks) or '—'} |")

    out += ["", "## 仍需人读", "",
            "这几条判的是「对不对」而不是「有没有」，脚本判不了。"
            "交付前逐条核对。", "",
            "| 序号 | 检查项 | 人要判断什么 |", "| --- | --- | --- |"]
    for item in sorted(MANUAL):
        out.append(f"| {item} | {ITEMS[item]} | {MANUAL[item]} |")
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="从骨架渲染派生视图")
    ap.add_argument("--write", action="store_true", help="落盘而不是打印")
    args = ap.parse_args(argv)
    text = render()
    if args.write:
        CHECKLIST.write_text(text, encoding="utf-8")
        print(f"已写入 {CHECKLIST}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
