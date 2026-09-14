"""Collect environment info for the issue body's '## 环境' section.

Returns a dict with: soc, cann_version, git_rev, python_version, os.
All fields default to 'unknown' on failure — never crash, never log noisy stacktraces.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from pathlib import Path


def collect_env(*, repo_path: Path, soc: str) -> dict[str, str]:
    return {
        "soc": soc,
        "cann_version": _cann_version(),
        "git_rev": _git_rev(repo_path),
        "python_version": f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "os": _os_string(),
    }


def _cann_version() -> str:
    ascend_home = os.environ.get("ASCEND_HOME_PATH", "")
    if not ascend_home:
        return "unknown"
    version_file = Path(ascend_home) / "version.info"
    if not version_file.exists():
        return "unknown"
    text = version_file.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"Version\s*=\s*(\S+)", text)
    return m.group(1) if m else "unknown"


def _git_rev(repo_path: Path) -> str:
    if not Path(repo_path).is_dir():
        return "unknown"
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown"
    if proc.returncode != 0:
        return "unknown"
    return proc.stdout.strip()


def _os_string() -> str:
    try:
        return f"{platform.system()} {platform.release()} {platform.machine()}"
    except Exception:
        return "unknown"
