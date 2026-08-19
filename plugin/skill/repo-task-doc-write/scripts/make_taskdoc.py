#!/usr/bin/env python3
"""渲染一次性脚手架：章节、表头、§8 固定内容。

这一步过后 md 就是唯一真相，不再重新渲染。脚手架只解决「结构错误」
这一类最廉价可预防的错，内容由 agent 按 decisions.json 填。

不覆盖已存在的文件——覆盖会把人已经填好的内容抹掉。
"""

import argparse
import json
import re
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL_ROOT / "references" / "task-doc-template.md"
SPINE = SKILL_ROOT / "references" / "taskdoc-elements.json"

PLACEHOLDER = "<!-- 待填写：{name} · 不写会怎样：{failure} -->"


def main(argv=None):
    ap = argparse.ArgumentParser(description="渲染任务书脚手架")
    ap.add_argument("--op", required=True, help="算子或接口名，如 aclnnRoll")
    ap.add_argument("--out", required=True, help="输出 md 路径")
    args = ap.parse_args(argv)

    out = Path(args.out)
    if out.exists():
        print(f"{out} 已存在，不覆盖 · md 是唯一真相，覆盖会抹掉已填内容",
              file=sys.stderr)
        return 2

    spine = json.loads(SPINE.read_text(encoding="utf-8"))
    template = TEMPLATE.read_text(encoding="utf-8")

    by_section = {}
    for element in spine["elements"].values():
        by_section.setdefault(element["section"], []).append(element)

    lines = [f"# {args.op} 任务书", ""]
    for raw in template.splitlines():
        heading = re.match(r"^(#{2,4})\s+([\d.]+)\s*(.*?)\s*$", raw)
        if not heading:
            continue
        number = heading.group(2).rstrip(".")
        lines.append(f"{heading.group(1)} {number} {heading.group(3)}".rstrip())
        lines.append("")
        if number == "8":
            body = template.split("## 8.")[-1].split("\n", 1)[-1]
            lines.append(body.rstrip())
            lines.append("")
            continue
        if number == "2.4":
            lines.append("| 参数名 | 输入／输出/属性 | 描述 | 数据类型 | "
                         "dtype类型 | 数据排布格式 | 维度(shape) | 值域范围 | "
                         "异常行为 |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            lines.append("")
            continue
        for element in by_section.get(number, []):
            lines.append(PLACEHOLDER.format(name=element["name"],
                                            failure=element["failure"]))
        lines.append("")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"脚手架已写入 {out} · 章节 {len(spine['sections'])} 节")
    print("下一步：next_questions.py 看该问什么，填完再跑 check_taskdoc.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
