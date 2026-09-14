"""Resolve a local repo path to (platform, owner, repo) for issue filing.

Strategy:
    1. Check repos.json cache (CWD/cann-ops-report/issues/repos.json).
    2. Try `git -C <repo_path> remote get-url origin`, parse URL.
    3. Caller prompts user for manual input when both above fail.

Supported URL forms:
    https://{github.com,gitee.com,gitcode.com}/{owner}/{repo}(.git)
    git@{github.com,gitee.com,gitcode.com}:{owner}/{repo}(.git)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import paths

PlatformTuple = tuple[str, str, str]  # (platform, owner, repo)

_HOST_TO_PLATFORM = {
    "github.com": "github",
    "gitee.com": "gitee",
    "gitcode.com": "gitcode",
}

_PATTERNS = [
    re.compile(r"^https?://(?P<host>github\.com|gitee\.com|gitcode\.com)/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^git@(?P<host>github\.com|gitee\.com|gitcode\.com):(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"),
]


def parse_remote_url(url: str) -> Optional[PlatformTuple]:
    """Return (platform, owner, repo) or None if URL not recognized."""
    for pattern in _PATTERNS:
        m = pattern.match(url.strip())
        if m:
            return (_HOST_TO_PLATFORM[m.group("host")], m.group("owner"), m.group("repo"))
    return None


def resolve_from_remote(repo_path: Path) -> Optional[PlatformTuple]:
    """Run `git remote get-url origin` in repo_path; parse result."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None
    return parse_remote_url(proc.stdout.strip())


def read_cache(repo_name: str) -> Optional[PlatformTuple]:
    repos_file = paths.REPOS_FILE._resolve()
    if not repos_file.exists():
        return None
    data = json.loads(repos_file.read_text(encoding="utf-8"))
    entry = data.get(repo_name)
    if not entry:
        return None
    return (entry["platform"], entry["owner"], entry["repo"])


def write_cache(repo_name: str, info: PlatformTuple, source: str) -> None:
    repos_file = paths.REPOS_FILE._resolve()
    repos_file.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if repos_file.exists():
        data = json.loads(repos_file.read_text(encoding="utf-8"))
    data[repo_name] = {
        "platform": info[0],
        "owner": info[1],
        "repo": info[2],
        "resolved_from": source,
        "resolved_at": datetime.now().isoformat(timespec="seconds"),
    }
    _atomic_write(repos_file, json.dumps(data, indent=2, ensure_ascii=False))


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".repos.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, target)
    except Exception:
        if Path(tmp).exists():
            Path(tmp).unlink()
        raise
