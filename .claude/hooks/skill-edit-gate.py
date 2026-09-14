#!/usr/bin/env python3
"""PreToolUse 门：改任何 skill 之前，必须读过权威参考，并且经由 skill-creator 来改。

规则（Mr.0 2026-08-14）：每次对任何 skill 的改动，
  1. 先读与本脚本同目录的 skill-best-practices.md（上游逐字副本，来源见 .SOURCE.txt）；
  2. 用 /skill-creator 来改——**调用**它，不是读一遍它的 SKILL.md。

本仓的写入通路是「Claude 派 Codex 落盘」，所以真正要卡的不是 Edit/Write，而是**派单那一刻**。
覆盖的派单路径有四条，任缺一条都等于没门：
  · Bash 里跑 codex-runner.mjs / codex exec
  · MCP 工具 mcp__codex-cli__codex / codex-reply
  · Skill 工具调用 cc-suite:implement
  · Claude 自己直接 Edit/Write（cc-suite 不可用时的降级通路）
派单文本里往往只出现计划文件路径、不出现被改的 skill 路径，所以对前三条还会**打开计划文件读内容**，
在里面找 skill 制品。

本门只拦「改」，不拦「看」：grep、cat、sed -n、git diff 一律放行。

判据是本会话 transcript 里那份参考的 Read，以及 skill-creator 的实际调用（Skill 工具或 /skill-creator）。
两者一个会话内各满足一次即可，之后改多少次都不再拦。

放行一次 skill 改动时会写下收据 `<cwd>/.claude/.skill-gate-receipt.json`，供 Codex 侧的门核对——
Codex 自己的会话里永远不会有 skill-creator 调用，它只能凭这张收据判断上游是否过了门。

失败开放：脚本自身出任何异常都放行。门坏了不该把所有编辑一起锁死——这里的代价是一次质量较差的
skill 改动，不是不可逆的破坏，与 nas-audit-gate 的 fail-closed 取向不同，是有意为之。

关掉一次：SKILL_GATE_OFF=1
"""
import json
import os
import re
import sys
import time

# 与本脚本同目录：门和它要求读的东西是一套，必须一起走（clone / worktree / 换机器）。
# 放在仓外绝对路径上时，文件被移走会让下面那条要求悄悄消失——门变松而无人察觉。
BEST_PRACTICES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "skill-best-practices.md")
SKILL_CREATOR = os.path.expanduser("~/.claude/skills/skill-creator/SKILL.md")

# 什么算 skill 制品：SKILL.md 本身，或任何 skills/<name>/ 目录下的文件
# （references/、scripts/、assets/ 都是 skill 的一部分）。
SKILL_PATH = re.compile(r"SKILL\.md|/skills?/[^/\s'\"]+/|\.claude/skills/", re.I)

# Bash 里什么算「在改」。只看写动作，读动作一概放行。
MUTATORS = [
    re.compile(r"\b(?:tee|patch|truncate)\b"),
    re.compile(r"\b(?:sed|perl)\s+-[a-z]*i"),
    re.compile(r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:cp|mv|rm|ln|install)\s"),
    re.compile(r"\bgit\s+(?:apply|checkout|restore|mv|rm)\b"),
    # 重定向的目标本身是 skill 路径（排除 2>&1、>/dev/null 之类）
    re.compile(r">>?\s*['\"]?[^\s'\";|&]*(?:SKILL\.md|/skills?/)", re.I),
]

# 派 Codex 落盘的三条路径
# 第三支要求 `codex` 是独立的命令词：前面不能是 `.`（那是 `.codex` 目录），后面不能直接接
# shell 操作符或重定向——`ls .codex 2>/dev/null`、`which codex | head` 都不是派单。
CODEX_CMD = re.compile(r"codex-runner\.mjs|codex\s+exec|(?<!\.)\bcodex\b\s+(?![-;&|<>]|\d+[<>])\S")
CODEX_TOOLS = ("mcp__codex-cli__codex", "mcp__codex-cli__codex-reply")
IMPLEMENT_SKILLS = ("cc-suite:implement", "cc-suite:continue", "cc-suite:audit-fix")

# 从派单文本里捞出可能是计划文件的路径
FILE_REF = re.compile(r"[~\w./+-]+\.(?:md|txt|json|patch|diff)\b")
MAX_PLAN_BYTES = 512 * 1024


def allow(receipt=None):
    if receipt:
        write_receipt(*receipt)
    sys.exit(0)


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    sys.exit(0)


def write_receipt(cwd, session, what):
    """记下「这一次 skill 改动是过了门的」，给 Codex 侧的门看。"""
    try:
        d = os.path.join(cwd or ".", ".claude")
        if not os.path.isdir(d):
            return
        with open(os.path.join(d, ".skill-gate-receipt.json"), "w", encoding="utf-8") as f:
            json.dump({"ts": int(time.time()), "session": session, "target": what[:400]},
                      f, ensure_ascii=False)
    except OSError:
        pass


def target_text(tool, inp):
    """这次调用要检查的字符串：文件工具看路径，其余看命令 / 提示词 / 参数。"""
    if tool in ("Edit", "Write", "MultiEdit"):
        return inp.get("file_path") or ""
    if tool == "NotebookEdit":
        return inp.get("notebook_path") or inp.get("file_path") or ""
    if tool == "Bash":
        return inp.get("command") or ""
    if tool in CODEX_TOOLS:
        return " ".join(v for v in inp.values() if isinstance(v, str))
    if tool == "Skill":
        return f"{inp.get('skill', '')} {inp.get('args', '')}"
    return ""


def mentions_skill(text):
    """文本自身，或它引用的计划文件的内容里，出现 skill 制品。"""
    if SKILL_PATH.search(text):
        return True
    for ref in FILE_REF.findall(text)[:8]:
        p = os.path.expanduser(ref)
        try:
            if not os.path.isfile(p) or os.path.getsize(p) > MAX_PLAN_BYTES:
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                if SKILL_PATH.search(f.read()):
                    return True
        except OSError:
            continue
    return False


def is_skill_change(tool, inp, text):
    if not text:
        return False
    # Skill 工具：只有派 Codex 落盘的那几个算改；调用 skill-creator 本身当然不能被拦。
    if tool == "Skill":
        return inp.get("skill") in IMPLEMENT_SKILLS and mentions_skill(text)
    if tool in CODEX_TOOLS:
        return mentions_skill(text)
    if tool == "Bash":
        if CODEX_CMD.search(text):
            return mentions_skill(text)
        return bool(SKILL_PATH.search(text)) and any(m.search(text) for m in MUTATORS)
    # Edit / Write / MultiEdit / NotebookEdit
    return bool(SKILL_PATH.search(text))


def scan_transcript(path):
    """返回 (读过的文件路径集合, 调用过的 skill 名集合, 命中行原文)。"""
    paths, skills, raw = set(), set(), []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "skill" not in line.lower():
                    continue
                raw.append(line)
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                content = (ev.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    inp = b.get("input") or {}
                    if b.get("name") == "Read":
                        p = inp.get("file_path")
                        if p:
                            paths.add(p)
                    elif b.get("name") == "Skill":
                        s = inp.get("skill")
                        if s:
                            skills.add(s)
    except OSError:
        pass
    return paths, skills, "".join(raw)


def main():
    if os.environ.get("SKILL_GATE_OFF"):
        allow()

    data = json.load(sys.stdin)
    tool = data.get("tool_name") or ""
    inp = data.get("tool_input") or {}
    text = target_text(tool, inp)
    if not is_skill_change(tool, inp, text):
        allow()

    paths, skills, raw = scan_transcript(data.get("transcript_path") or "")

    if not os.path.exists(BEST_PRACTICES):
        # 判据文件不在，门就没法判。这里不静默放行——那正是「一条 mv 就把门解除」的情形。
        deny(
            "skill 改动门装坏了：找不到它要求读的那份参考。\n"
            f"  期望位置：{BEST_PRACTICES}\n\n"
            "这份文件必须和 hook 放在同一目录。补回去再改 skill；\n"
            "确需跳过时用 SKILL_GATE_OFF=1，并在回复里说明这一轮没走这道门。"
        )

    missing = []
    if not any(p.endswith("skill-best-practices.md") for p in paths):
        missing.append(("skill-best-practices.md", BEST_PRACTICES))
    # 注意：读一遍 skill-creator/SKILL.md 不算数，规矩是「用它来改」，所以只认实际调用。
    if os.path.exists(SKILL_CREATOR) and not (
        any("skill-creator" in s for s in skills)
        or "<command-name>skill-creator" in raw
    ):
        missing.append(("/skill-creator", "调用它来改（读一遍 SKILL.md 不算）"))

    what = text if len(text) <= 160 else text[:160] + "…"
    if not missing:
        allow(receipt=(data.get("cwd"), data.get("session_id"), what))

    lines = [
        "改 skill 之前有前置没做完。本次要动：",
        f"  {what}",
        "",
        "先补齐下面这些（本会话内各做一次即可，之后改多少次都不再拦），然后原样重试：",
    ]
    for label, path in missing:
        lines.append(f"  · {label}  →  {path}")
    lines += [
        "",
        "这是 Mr.0 定的规矩：改任何 skill 都要先过那份权威参考，并且经由 skill-creator 来改。",
        "派 Codex 落盘同样算改——门卡的就是派单这一刻。",
        "确需跳过时用 SKILL_GATE_OFF=1，并在回复里说明这一轮没走这道门。",
    ]
    deny("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        # 门自己坏了不该锁死所有编辑。
        sys.exit(0)
