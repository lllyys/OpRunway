#!/usr/bin/env python3
"""载入预算检查（SA-04）：`SKILL.md` ≤ 12 KB，单阶段最小载入 ≤ 32 KB。

规则在 `.claude/rules/skill-authoring.md`。此前没有检查脚本时，
`repo-task-atk-accept` 的 `SKILL.md` 涨到 26 KB（超限 3.3 倍）没有任何提示，
表现是每次跑测都在第一步为后面所有阶段的知识付钱。

单阶段最小载入怎么算：`SKILL.md` 加上**同一行链出去的全部 reference**。
一行就是一个去处——主流程表的一行是一个阶段，散文里的一句「先读 X」也是一步。
按行算不按节算，是因为一节里的多个链接分属不同阶段，加在一起会虚高。
"""

import re
import sys
from pathlib import Path

# **这两个数是防回涨的护栏，不是要压的指标。** 实测有害发生在 26 KB：A1 开工前
# 10 分钟全在读 A2–A4 的材料。8 KB 那版逼着把 A0 那道人工确认的闸挪出路由器，
# 换来的只是数字好看——判据的可靠性优先于预算。
# 单一用途的 skill（六个 cann-*）实测都在 3.5–8.2 KB，放宽不影响它们。
SKILL_MAX = 12 * 1024
STAGE_MAX = 32 * 1024
LINK = re.compile(r"\]\((references/[A-Za-z0-9_.-]+\.md)\)")


def check(skill_md):
    root = skill_md.parent
    size = len(skill_md.read_bytes())
    problems = []
    if size > SKILL_MAX:
        problems.append(f"  SKILL.md {size} B，超 {SKILL_MAX} B（{size / SKILL_MAX:.1f} 倍）"
                        f"\n      → 阶段展开下沉到 references/，路由器只留阶段表与判据")
    worst = (0, "")
    for number, line in enumerate(skill_md.read_text(encoding="utf-8").splitlines(), 1):
        refs = {m for m in LINK.findall(line)}
        if not refs:
            continue
        total = size + sum(len((root / r).read_bytes()) for r in refs
                           if (root / r).is_file())
        if total > worst[0]:
            worst = (total, f"第 {number} 行 → {'、'.join(sorted(refs))}")
        if total > STAGE_MAX:
            problems.append(f"  第 {number} 行的最小载入 {total} B > {STAGE_MAX} B"
                            f"\n      链的是 {'、'.join(sorted(refs))}"
                            f"\n      → 拆那份 reference，或把这一行的知识挪到用得上的那一步")
    return problems, size, worst


def main():
    root = Path(__file__).resolve().parents[2]
    bad = 0
    for skill_md in sorted(root.glob("skill/*/SKILL.md"))\
            + sorted(root.glob("plugin/skill/*/SKILL.md")):
        problems, size, worst = check(skill_md)
        name = skill_md.parent.name
        if problems:
            bad += 1
            print(f"{name}")
            print("\n".join(problems))
        else:
            print(f"{name:28s} SKILL.md {size:6d} B，最大单阶段 {worst[0]:6d} B")
    if bad:
        print(f"\n共 {bad} 个 skill 超预算。规则见 .claude/rules/skill-authoring.md（SA-04）。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
