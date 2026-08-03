"""验收入口文本一致性门：防止真机通路、源码身份门和历史结果口径回退。

只读、stdlib、fail-closed。它不解析 Markdown 语义，只守最容易造成错误执行或错误宣称的
少量硬约束；历史记录允许保留，但不得在活跃入口里重新宣称旧 Median PASS。
"""
import argparse
import os
import re
import sys


ENTRYPOINTS = (
    "AGENTS.md",
    "agents/op-acceptance.md",
    "agents/acc-verify-rootcause.md",
    "commands/op-acceptance.md",
    "skills/acceptance-workflow/SKILL.md",
    "skills/acc-runner/SKILL.md",
    "workflows/development-guide.md",
    "workflows/task-prompts.md",
)

SOURCE_GATE_FILES = (
    "agents/acc-verify-rootcause.md",
    "skills/acceptance-workflow/SKILL.md",
)

SOURCE_GATE_TOKENS = (
    "SOURCE_ACQUIRED",
    "HEAD_VERIFIED",
    "BUILD_VERIFIED",
    "WORKFLOW_STARTED",
    "set -Eeuo pipefail",
)

_STALE_PASS = re.compile(r"Median.{0,160}(?:56/56|60/60).{0,80}PASS", re.IGNORECASE)
_HISTORICAL_MARKERS = ("历史", "旧", "不得沿用", "不再", "取代", "失效")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeError) as ex:
        raise RuntimeError(f"读取失败 {path}: {ex}") from ex


def collect(plugin_root):
    errors = []
    texts = {}
    for rel in ENTRYPOINTS:
        path = os.path.join(plugin_root, rel)
        try:
            texts[rel] = _read(path)
        except RuntimeError as ex:
            errors.append(str(ex))

    for rel, text in texts.items():
        if "两条真机通路" in text:
            errors.append(f"{rel}: 仍写“两条真机通路”")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _STALE_PASS.search(line) and not any(x in line for x in _HISTORICAL_MARKERS):
                errors.append(f"{rel}:{lineno}: 活跃文本仍宣称旧 Median PASS")

    for rel in SOURCE_GATE_FILES:
        text = texts.get(rel, "")
        for token in SOURCE_GATE_TOKENS:
            if token not in text:
                errors.append(f"{rel}: 缺源码执行门 token {token!r}")
    return errors


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="验收入口文本一致性门")
    ap.add_argument("--plugin-root", default=os.path.dirname(here))
    args = ap.parse_args(argv)
    try:
        errors = collect(args.plugin_root)
    except Exception as ex:  # 门本身不得 traceback 后被误读成通过
        errors = [f"门执行失败: {ex}"]
    for error in errors:
        print(f"  ✗ {error}")
    print(f"STATUS: {'SYNCED' if not errors else 'DRIFT'}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
