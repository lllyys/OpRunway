"""Pull error-relevant lines from an cann-ops-run phase log for issue bodies.

Keywords (case-insensitive): ERROR | undefined | failed | exit=
Default cap: 100 lines (configurable).
"""
from __future__ import annotations

import re
from pathlib import Path


_KEYWORDS = re.compile(r"ERROR|undefined|failed|exit=", re.IGNORECASE)


def extract_errors(log_path: Path, max_lines: int = 100) -> list[str]:
    """Return up to max_lines log lines that match error keywords.

    Returns a single-element list with a placeholder for missing or keyword-empty logs.
    """
    p = Path(log_path)
    if not p.exists():
        return ["(log file not found)"]
    matched: list[str] = []
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if _KEYWORDS.search(line):
                matched.append(line.rstrip())
                if len(matched) >= max_lines:
                    break
    except OSError:
        return ["(log file unreadable)"]
    if not matched:
        return ["(no error keywords matched in log)"]
    return matched


def format_as_code_block(lines: list[str], lang: str = "") -> str:
    """Fence the lines as a markdown code block."""
    body = "\n".join(lines)
    return f"```{lang}\n{body}\n```"
