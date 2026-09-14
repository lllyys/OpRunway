"""P0:在一个仓里发现 QuickStart / 快速入门 候选文档,供 agent 让用户确认评测哪一份。

只发现、不判断质量。两类命中:
  - 文件名命中(高优先):quickstart / quick_start / quick-start / 快速入门 / 快速开始 / getting_started 等
  - 内容命中(低优先):.md 标题里出现「快速入门 / 快速开始 / Quick Start / Getting Started」

用法:
  <python> <skill>/scripts/find_docs.py <repo_root> [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 文件名关键字(小写匹配)
NAME_KEYS = ["quickstart", "quick_start", "quick-start", "快速入门", "快速开始",
             "getting_started", "getting-started", "gettingstarted"]
# 标题里的关键字(内容命中)
HEADING_RE = re.compile(r"^#{1,3}\s.*(快速入门|快速开始|quick\s*start|getting\s*started)",
                        re.IGNORECASE | re.MULTILINE)
# 跳过的噪声目录
SKIP_DIRS = {".git", "node_modules", "build", "build_out", "third_party", "3rd",
             "__pycache__", ".github"}


def _iter_md(root: Path):
    for p in root.rglob("*.md"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def _first_title(text: str) -> str:
    m = re.search(r"^#{1,3}\s+(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def find_docs(repo_root: str) -> list[dict]:
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"repo root not found: {root}")
    out: list[dict] = []
    seen: set[str] = set()
    for p in _iter_md(root):
        rel = str(p.relative_to(root))
        low_name = p.name.lower()
        reason = None
        if any(k in low_name for k in NAME_KEYS):
            reason = "filename"
        else:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if HEADING_RE.search(text):
                reason = "heading"
        if reason and rel not in seen:
            seen.add(rel)
            try:
                title = _first_title(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                title = ""
            out.append({"path": rel, "abs_path": str(p), "match": reason, "title": title})
    # 文件名命中排前,其次按路径
    out.sort(key=lambda d: (0 if d["match"] == "filename" else 1, d["path"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="发现 QuickStart / 快速入门候选文档")
    ap.add_argument("repo_root")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        docs = find_docs(args.repo_root)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(docs, ensure_ascii=False, indent=2))
    else:
        if not docs:
            print("(未发现任何 quickstart / 快速入门候选文档——文档缺位本身是一种缺陷)")
        for d in docs:
            print(f"[{d['match']:8s}] {d['path']}    {d['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
