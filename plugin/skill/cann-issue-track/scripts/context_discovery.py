"""Auto-discover retest context (SOC, repo_path, ...) from existing state.

Priority for SOC:
    1. state.json record's `soc` field (written by cann-issue-report when known)
    2. Parsing the upstream issue body (matches `| SOC | xxx |` or `--soc=xxx`)
    3. run_state.json's per-op record (if cann-ops-run ever persists soc — currently doesn't)
    4. Returning None → caller must ask the user

The agent should call discover_soc() before falling back to AskUserQuestion.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from . import paths
except ImportError:
    import paths  # type: ignore[no-redef]


_SOC_TABLE_RE = re.compile(r"\|\s*SOC\s*\|\s*([a-zA-Z0-9_]+)", re.IGNORECASE)
_SOC_FLAG_RE = re.compile(r"--soc[ =]([a-zA-Z0-9_]+)")
_SOC_BARE_RE = re.compile(r"\b(ascend9\d{2}\w*|ascend910b\w*|ascend310p?\w*)\b", re.IGNORECASE)


def discover_soc(
    *,
    repo: str,
    op: str,
    failure_type: str,
    issue_body: str | None = None,
) -> str | None:
    """Return SOC string or None if cannot determine without user input."""
    # 1. state.json record
    soc = _from_state_record(repo, op, failure_type)
    if soc:
        return soc

    # 2. Parse from issue body
    if issue_body:
        soc = _from_issue_body(issue_body)
        if soc:
            return soc

    # 3. run_state.json (currently cann-ops-run doesn't write soc; reserved for future)
    soc = _from_run_state(repo, op)
    if soc:
        return soc

    return None


def discover_repo_path(repo: str) -> str | None:
    """Return local repo_path if discoverable from run_state.json, else None.

    Reads the per-repo `cann-ops-report/<repo>/test/run_state.json` flat schema
    (top-level `repo_path` key). cann-ops-run doesn't always store it, so this stays
    a best-effort hook — callers fall back to asking the user when it returns None.
    """
    state_path = paths.repo_state_file(repo)
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data.get("repo_path")
    except (KeyError, json.JSONDecodeError, TypeError):
        return None


def _from_state_record(repo: str, op: str, failure_type: str) -> str | None:
    state_path = Path(paths.STATE_FILE)
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        key = f"{repo}::{op}::{failure_type}"
        return data.get(key, {}).get("soc")
    except (json.JSONDecodeError, TypeError):
        return None


def _from_issue_body(body: str) -> str | None:
    m = _SOC_TABLE_RE.search(body)
    if m:
        return m.group(1).lower()
    m = _SOC_FLAG_RE.search(body)
    if m:
        return m.group(1).lower()
    m = _SOC_BARE_RE.search(body)
    if m:
        return m.group(1).lower()
    return None


def _from_run_state(repo: str, op: str) -> str | None:
    state_path = paths.repo_state_file(repo)
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data["ops"][op].get("soc")
    except (KeyError, json.JSONDecodeError, TypeError):
        return None
