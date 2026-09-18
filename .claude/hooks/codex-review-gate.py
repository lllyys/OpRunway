#!/usr/bin/env python3
"""PreToolUse 门：派 Codex 做评审时，提示词必须携带 codex-review.md 的八维纪律。

规则（Mr.0 2026-09-17）：每条评审结论都要从八个维度出发打分并给理由
（.claude/rules/codex-review.md）。2026-09-17 实录：spec 段评审带了七维，
plan 段掉成了五维 buildability——纪律靠人记必掉，故立此门。

拦什么：Bash 里 codex-runner.mjs 派单且 --kind 为 review-plan / audit
（形成评审意见的轮次）。verify 与非评审 kind 豁免——确认轮跟随主审轮的维度框架。

放行判据（提示词文本满足其一即可；含 $(cat 文件) 引用的文件内容）：
  1. 八维评审：八个维度名至少出现 5 个（通用性/泛化性/简单性/清晰性/复杂度/
     冷读/爆炸半径/信任成本）。
  2. 冷读专项：出现「冷读专项」或「零上下文」——纪律明许可读性单轴拆分，
     且冷读必须全新 session，硬塞八维反而污染条件。
  3. 确认轮：出现「确认轮」——闭合核对，维度已在主审轮覆盖，派单者以此显式声明。

失败开放：脚本自身异常一律放行（与 skill-edit-gate 同取向——门坏了不该锁死评审，
代价是一次维度不全的评审，可事后补，不是不可逆破坏）。

关掉一次：CODEX_REVIEW_GATE_OFF=1（环境变量或命令前缀），须在回复里声明。
"""
import json
import os
import re
import sys

RUNNER = re.compile(r"codex-runner\.mjs")
GATED_KINDS = re.compile(r"--kind\s+(?:review-plan|audit)\b")

DIMENSIONS = ["通用性", "泛化性", "简单性", "清晰性", "复杂度", "冷读", "爆炸半径", "信任成本"]
MIN_DIMS = 5
EXEMPT_TOKENS = ("冷读专项", "零上下文", "确认轮")

# 从命令里捞可能装着提示词的文件（$(cat …) 形态）
FILE_REF = re.compile(r"[~\w./+-]+\.(?:txt|md)\b")
MAX_PROMPT_BYTES = 512 * 1024


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


def gather_text(command):
    parts = [command]
    for ref in FILE_REF.findall(command)[:4]:
        p = os.path.expanduser(ref)
        try:
            if os.path.isfile(p) and os.path.getsize(p) <= MAX_PROMPT_BYTES:
                with open(p, encoding="utf-8", errors="replace") as f:
                    parts.append(f.read())
        except OSError:
            continue
    return "\n".join(parts)


def main():
    if os.environ.get("CODEX_REVIEW_GATE_OFF"):
        allow()

    data = json.load(sys.stdin)
    if (data.get("tool_name") or "") != "Bash":
        allow()
    command = (data.get("tool_input") or {}).get("command") or ""
    if re.search(r"(?:^|[;&|]\s*)CODEX_REVIEW_GATE_OFF=(?!0\b)\S+\s", command):
        allow()
    if not (RUNNER.search(command) and GATED_KINDS.search(command)):
        allow()

    text = gather_text(command)
    if any(tok in text for tok in EXEMPT_TOKENS):
        allow()
    hit = [d for d in DIMENSIONS if d in text]
    if len(hit) >= MIN_DIMS:
        allow()

    missing = [d for d in DIMENSIONS if d not in hit]
    deny(
        "评审派单没带八维纪律（.claude/rules/codex-review.md）。\n"
        f"  提示词只命中 {len(hit)}/8 个维度，缺：{'、'.join(missing)}\n\n"
        "三选一后原样重试：\n"
        "  · 八维评审：提示词写入八个维度名与打分要求（至少命中 5 个）\n"
        "  · 冷读专项：写明「冷读专项」或「零上下文」（单轴拆分，须全新 session）\n"
        "  · 确认轮：写明「确认轮」（闭合核对，维度已在主审轮覆盖）\n\n"
        "确需跳过时用 CODEX_REVIEW_GATE_OFF=1，并在回复里说明这一轮没走这道门。"
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
