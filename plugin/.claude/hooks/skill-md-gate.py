#!/usr/bin/env python3
"""PreToolUse 门禁：编辑 skill/<name>/SKILL.md 时把开发流程注回上下文。

为什么要有它：CLAUDE.md 与 .claude/rules/skill-style.md 已经把「先调
skill-creator」和「三问」写进常驻上下文，2026-08-28 那一轮仍然被跳过——
规则读到了没执行。会话开始时载入的文本要和几十条别的约束抢注意力；这个钩子
在**动手改那一刻**才说话，不依赖模型把任务自我归类成「改 SKILL.md」。

只提醒不拦截：不发 permissionDecision，编辑照常进行。
用 python3 不用 jq——本机没装 jq，而 python3 是本仓 pytest 的既有依赖。
"""

import json
import pathlib
import re
import sys

# references/ 也要盯：行文规范同样约束它，而且它比 SKILL.md 长得多。
TARGET = re.compile(r"skill/[^/]+/(SKILL\.md|references/[^/]+\.md)$")
IS_SKILL_MD = re.compile(r"skill/[^/]+/SKILL\.md$")

NOTE = """改 SKILL.md 的门禁（.claude/hooks/skill-md-gate.py 注入）：

1. 还没调 skill-creator 就先调（CLAUDE.md 开发流程第 1 条）。通用怎么写归它，
   本仓特有的归 .claude/rules/skill-style.md。没装就跳过，不要为它停下。
2. 每一段增量当场过三问，答不上就别加：
   - Claude 真的需要这个解释吗？
   - 能不能假定它已经知道？
   - 这一段值它的 token 吗？——SKILL.md 每次触发都全量载入。
3. 落点先于措辞，写之前确认这段该不该在 SKILL.md：
   - 脚本干了什么 -> 脚本自己的打印与报错，不是 SKILL.md
   - 入口参数表 -> 只放进 S0 之前必须确定的量，可选开关放它生效的那一阶段
   - 大段知识 -> references/，SKILL.md 只留路由
4. 预算：SKILL.md <= 8 KB，单阶段最小载入 <= 30 KB。当前实测见下方，
   超了就先问一句「能不能换成删」。
5. 行文按 .claude/rules/doc-style.md：标题用名词短语不用疑问句，不出现拟人
   主语与第一人称，模糊词要给具体判据。改完跑一次：
   python3 .claude/hooks/doc_style_lint.py <这个文件>"""

PROSE_NOTE = """改 skill 文档的门禁（.claude/hooks/skill-md-gate.py 注入）：

行文按 .claude/rules/doc-style.md：标题用名词短语不用疑问句，不出现拟人主语
与第一人称，模糊词（适当／必要时／视情况）要换成具体判据。

改完跑一次，退出码 0 才算完：
   python3 .claude/hooks/doc_style_lint.py <这个文件>"""


def lint_now(path):
    """当场把这份文件的问题量出来。

    只复述规则不够——2026-08-28 那一轮证明过，规则读到了照样不执行。
    直接给行号和替代写法，才不用模型自己把任务归类成「该查行文」。
    """
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        import doc_style_lint
        findings = doc_style_lint.check_prose(
            pathlib.Path(path), pathlib.Path(path).read_text(encoding="utf-8"))
    except Exception:
        return ""                      # 量具自己坏了也绝不挡住编辑
    if not findings:
        return ""
    lines = [f"\n这份文件当前有 {len(findings)} 处待改（改动前的快照）："]
    for _, line, rule, what, fix in sorted(findings, key=lambda x: x[1])[:12]:
        lines.append(f"  L{line} [{rule}] {what} → {fix}")
    if len(findings) > 12:
        lines.append(f"  …另有 {len(findings) - 12} 处，跑量具看全量")
    return "\n".join(lines)


def budget_now(path):
    """这个 skill 当前的载入预算。**只报被改的那一个**，别的超标与这次编辑无关。

    与 lint_now 同一个道理：把数当场量出来，比在 NOTE 里写「注意别超」有用——
    2026-09-09 之前 NOTE 里就写着这两个数，而 SKILL.md 涨到 26 KB 无人察觉。
    """
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).parent))
        import skill_budget_lint
        skill_md = pathlib.Path(path)
        while skill_md.parent.name != "skill":
            skill_md = skill_md.parent
        skill_md = skill_md / "SKILL.md"
        if not skill_md.is_file():
            return ""
        problems, size, worst = skill_budget_lint.check(skill_md)
    except Exception:
        return ""                      # 量具自己坏了也绝不挡住编辑
    if not problems:
        return (f"\n\n载入预算：SKILL.md {size} B，最大单阶段 {worst[0]} B，都在预算内。"
                f"\n加东西之前先想清楚它会不会把这两个数顶出去。")
    return ("\n\n载入预算**已超**（.claude/hooks/skill_budget_lint.py）：\n"
            + "\n".join(problems))


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0                      # 读不懂就闭嘴，绝不因为门禁本身挡住编辑
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not TARGET.search(str(path)):
        return 0
    skill_md = bool(IS_SKILL_MD.search(str(path)))
    note = (NOTE if skill_md else PROSE_NOTE) + lint_now(path) + budget_now(path)
    json.dump({
        "systemMessage": ("SKILL.md 门禁：skill-creator + 三问 + 载体法则 + 行文"
                          if skill_md else "skill 文档门禁：行文规范已注入"),
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": note,
        },
    }, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
