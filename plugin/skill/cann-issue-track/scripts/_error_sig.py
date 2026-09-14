"""Failure-log signature: pick first error line, normalize, hash."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

_ERROR_LINE_RE = re.compile(r"(ERROR|undefined|failed|exit=)", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[\.\d]*Z?")
_LINE_COL_RE = re.compile(r":\d+:\d+:")
_LINE_NO_RE = re.compile(r"\bline \d+\b", re.IGNORECASE)
_MULTI_WS_RE = re.compile(r"\s+")

_ABS_PREFIXES = ("/home/", "/root/", "/opt/", "/tmp/", "/data/", "/mnt/")


def first_error_line(log_path: Path | str) -> str:
    """Return the first line matching ERROR|undefined|failed|exit= or '' if none."""
    p = Path(log_path)
    if not p.exists():
        return ""
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if _ERROR_LINE_RE.search(line):
            return line
    return ""


def normalize(line: str) -> str:
    s = _TIMESTAMP_RE.sub("", line)
    s = _LINE_COL_RE.sub(":", s)
    s = _LINE_NO_RE.sub("line N", s)
    # Strip absolute path prefixes: remove everything up to ops-* repo pattern
    for prefix in _ABS_PREFIXES:
        # Greedy match: /prefix/anything/ops-* -> keep ops-* and beyond
        s = re.sub(rf"{re.escape(prefix)}.*?/(ops-[^/]+/)", r"\1", s)
        # Also handle case where there's no ops-* pattern: just remove /prefix/something/
        s = re.sub(rf"{re.escape(prefix)}[^/\s]*/", "", s)
    cwd = str(Path.cwd())
    if cwd:
        s = s.replace(cwd, "")
    home = os.environ.get("HOME", "")
    if home:
        s = s.replace(home, "~")
    s = _MULTI_WS_RE.sub(" ", s).strip()
    return s


def signature(line: str) -> str:
    norm = normalize(line)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]
