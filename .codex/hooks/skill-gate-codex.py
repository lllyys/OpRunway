#!/usr/bin/env python3
"""Codex 侧 PreToolUse 门：没有上游收据，就不许改 skill。

为什么需要单独一份：本仓的写入通路是「Claude 派 Codex 落盘」。Claude 那边的门
（.claude/hooks/skill-edit-gate.py）卡的是派单那一刻，管得住我们自己的流程；但 Codex 一旦跑起来，
它可以顺手改任何文件，包括计划里没写的 skill。这份就补那个洞。

判据只能是收据，不能是 skill-creator 调用——Codex 自己的会话里永远不会有那个调用，那是上游 Claude
会话里的事实。Claude 侧的门每放行一次 skill 改动就写下
    <cwd>/.claude/.skill-gate-receipt.json
本门要求它存在且够新（默认一小时内）。

实测（2026-08-14，codex-cli 0.147.0）：Codex 的 PreToolUse 载荷与 Claude 同形，含 tool_name /
tool_input / cwd；返回 permissionDecision=deny 确实会阻止该次工具调用，Codex 会如实报告并停手。
但项目级 .codex/hooks.json 需要一次性 hook trust，未授信时被静默跳过。

失败开放：脚本自身出任何异常都放行。
逃生口：SKILL_GATE_OFF=1
"""
import json
import os
import re
import sys
import time

RECEIPT = ".claude/.skill-gate-receipt.json"
RECEIPT_TTL = 3600  # 秒。派单紧跟在过门之后，一小时绰绰有余。

SKILL_PATH = re.compile(r"SKILL\.md|/skills?/[^/\s'\"]+/|\.claude/skills/", re.I)

# 工具名本身就意味着写
WRITE_TOOLS = re.compile(r"write|edit|patch|apply", re.I)
# shell 命令里的写动作
MUTATORS = [
    re.compile(r"\b(?:tee|patch|truncate)\b"),
    re.compile(r"\b(?:sed|perl)\s+-[a-z]*i"),
    re.compile(r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:cp|mv|rm|ln|install)\s"),
    re.compile(r"\bgit\s+(?:apply|checkout|restore|mv|rm)\b"),
    re.compile(r">>?\s*['\"]?[^\s'\";|&]*(?:SKILL\.md|/skills?/)", re.I),
    re.compile(r"\bcat\s*>|\bheredoc\b|<<\s*['\"]?EOF", re.I),
]


def allow():
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


def strings(obj, out, depth=0):
    """把 tool_input 里所有字符串摊平——不同工具的字段名不一样，逐个认不如全看。"""
    if depth > 6:
        return
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            strings(v, out, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            strings(v, out, depth + 1)


def main():
    if os.environ.get("SKILL_GATE_OFF"):
        allow()

    data = json.load(sys.stdin)
    tool = data.get("tool_name") or ""
    vals = []
    strings(data.get("tool_input") or {}, vals)
    text = "\n".join(vals)

    if not SKILL_PATH.search(text):
        allow()
    # 命中 skill 路径，再判是不是在写
    if not (WRITE_TOOLS.search(tool) or any(m.search(text) for m in MUTATORS)):
        allow()

    path = os.path.join(data.get("cwd") or ".", RECEIPT)
    try:
        with open(path, encoding="utf-8") as f:
            age = time.time() - float(json.load(f).get("ts", 0))
    except (OSError, ValueError, TypeError):
        age = None

    if age is not None and 0 <= age <= RECEIPT_TTL:
        allow()

    why = "没有收据" if age is None else f"收据已过期（{int(age)} 秒前，上限 {RECEIPT_TTL}）"
    deny(
        "这次要改的是 skill 制品，但上游没过门。\n"
        f"  {why}：{path}\n\n"
        "本仓的规矩（Mr.0 2026-08-14）：改任何 skill，都要先读 skill-best-practices.md，"
        "并且经由 /skill-creator 来改。收据由 Claude 侧的门在放行时写下。\n"
        "正确做法是回到 Claude 会话补齐前置再重新派单，不要在这里绕过。\n"
        "确需跳过时用 SKILL_GATE_OFF=1，并在回复里说明这一轮没走这道门。"
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
