#!/usr/bin/env python3
"""skill 文档的两个时机：写之前拦新建，写之后报问题。

| 事件 | 条件 | 行为 |
| --- | --- | --- |
| PreToolUse | 新建 skill 文档，且本会话没有 Read 过两份规则 | 拒绝这次写入，要求先读规则 |
| PostToolUse | 每次改完 skill 文档 | 输出这份文件现在还剩哪些问题与载入预算 |

为什么要拦新建：`.claude/rules/` 的 `paths` 规则只在 Read 匹配文件时载入，
新建文件没有 Read，初稿会在规则不在上下文的情况下写出来。改存量不受影响，
读那份文件本身就会触发载入。

规则正文不在这里复述，只给文件路径。检查脚本自身出错时一律放行。
用 python3 不用 jq——本机没装 jq，而 python3 是本仓 pytest 的既有依赖。
"""

import json
import pathlib
import re
import sys

# references/ 也要盯：行文规范同样约束它，而且它比 SKILL.md 长得多。
TARGET = re.compile(r"skill/[^/]+/(SKILL\.md|references/[^/]+\.md)$")
IS_SKILL_MD = re.compile(r"skill/[^/]+/SKILL\.md$")

RULE_FILES = (".claude/rules/skill-authoring.md", ".claude/rules/zh-writing.md")

DENY_REASON = """新建 skill 文档前先读规则，否则初稿写在规则不在上下文的情况下：

    Read .claude/rules/skill-authoring.md    结构与指令写法（SA-xx）
    Read .claude/rules/zh-writing.md         中文排版、用词、术语表（ZH-xx）

读完再写这个文件。改已有文件不受此限制。"""

NOTE = """改 SKILL.md 的提示（.claude/hooks/skill-md-gate.py 注入）：

1. 还没调用 skill-creator 就先调用；没装就跳过。
2. 规则在 .claude/rules/skill-authoring.md（SA-xx）与 .claude/rules/zh-writing.md（ZH-xx）。
3. 改完运行，退 0 才算完成：
   python3 .claude/hooks/doc_style_lint.py <这个文件>
   python3 .claude/hooks/skill_budget_lint.py"""

PROSE_NOTE = """改 skill 文档的提示（.claude/hooks/skill-md-gate.py 注入）：

规则在 .claude/rules/zh-writing.md（ZH-xx）与 .claude/rules/skill-authoring.md（SA-xx）。
改完运行，退 0 才算完成：
   python3 .claude/hooks/doc_style_lint.py <这个文件>"""


def rules_read(transcript_path):
    """本会话有没有用 Read 打开过那两份规则文件。

    只认 Read 工具的 file_path，不做整份转录的字符串匹配——仓根 CLAUDE.md
    里就写着这两个路径，按字符串匹配会永远判成读过。
    """
    path = pathlib.Path(transcript_path or "")
    if not path.is_file():
        return True                        # 拿不到转录就不拦，避免误挡
    seen = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if '"Read"' not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = (entry.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("name") != "Read":
                continue
            target = str((block.get("input") or {}).get("file_path", ""))
            seen |= {r for r in RULE_FILES if target.endswith(r)}
    return set(RULE_FILES) <= seen


def should_deny(path, transcript_path, exists):
    """新建 skill 文档且规则未读时拦一次。"""
    return not exists and not rules_read(transcript_path)


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
        findings = [f for f in findings if doc_style_lint.enforced(f[0], f[2])]
    except Exception:
        return ""                      # 检查脚本出错时不影响编辑
    if not findings:
        return ""
    lines = [f"\n这份文件改动后还有 {len(findings)} 处待改："]
    for _, line, rule, what, fix in sorted(findings, key=lambda x: x[1])[:12]:
        lines.append(f"  L{line} [{rule}] {what} → {fix}")
    if len(findings) > 12:
        lines.append(f"  …另有 {len(findings) - 12} 处，运行 doc_style_lint.py 查看全部")
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
        return ""                      # 检查脚本出错时不影响编辑
    if not problems:
        return (f"\n\n载入预算：SKILL.md {size} B，最大单阶段 {worst[0]} B，都在预算内。"
                f"\n新增内容前确认不会超出这两个上限。")
    return ("\n\n载入预算已超（.claude/hooks/skill_budget_lint.py）：\n"
            + "\n".join(problems))


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0                      # 读不懂就闭嘴，绝不因为门禁本身挡住编辑
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not TARGET.search(str(path)):
        return 0

    if payload.get("hook_event_name") == "PreToolUse":
        if should_deny(path, payload.get("transcript_path"), pathlib.Path(path).exists()):
            json.dump({
                "systemMessage": "新建 skill 文档：先读两份规则",
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": DENY_REASON,
                },
            }, sys.stdout, ensure_ascii=False)
        return 0

    skill_md = bool(IS_SKILL_MD.search(str(path)))
    note = (NOTE if skill_md else PROSE_NOTE) + lint_now(path) + budget_now(path)
    json.dump({
        "systemMessage": ("SKILL.md 提示：skill-creator + 规则文件 + lint"
                          if skill_md else "skill 文档提示：规则文件 + lint"),
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": note,
        },
    }, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
