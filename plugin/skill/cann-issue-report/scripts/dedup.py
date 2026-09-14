"""Local-state dedup. Key = repo::op::failure_type.

state.json schema:
    {
      "<repo>::<op>::<failure_type>": {
        "issue_url": "...",
        "submitted_at": "ISO8601",
        "phase": "phase1",
        "submitted_via": "api" | "manual" | "track_issues_followup",
        "soc": "ascend950" | ...,      # optional; cann-issue-track uses it for retest
        "status": "submitted"          # lifecycle state (see STATUS_* constants)
          | "replied_discuss"          # community replied but no actionable fix
          | "replied_pr_pending"       # community says PR is in progress
          | "plan_selected"            # fix plan chosen, awaiting retest
          | "closed_pass"              # retest passed, issue closed
          | "closed_by_track_issues"   # alias for closed_pass (legacy)
          | "closed_by_track_issues_partial"  # partial-PASS; follow-up opened
          | "retest_fail"              # retest failed, still tracking
          | "closed_upstream"          # maintainer closed it directly
          | "deleted_upstream",        # issue 404'd
        "last_checked_at": "ISO8601",  # when comments were last fetched
        "parent_issue_url": "...",     # set on track_issues_followup entries
      },
      ...
    }
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

from . import paths


def make_key(repo: str, op: str, failure_type: str) -> str:
    return f"{repo}::{op}::{failure_type}"


def _load() -> dict:
    state_file = paths.STATE_FILE._resolve()
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text(encoding="utf-8"))


def _atomic_save(data: dict) -> None:
    state_file = paths.STATE_FILE._resolve()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state.", dir=str(state_file.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, state_file)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def is_submitted(repo: str, op: str, failure_type: str) -> bool:
    return make_key(repo, op, failure_type) in _load()


def get_record(repo: str, op: str, failure_type: str) -> dict | None:
    return _load().get(make_key(repo, op, failure_type))


def mark_submitted(
    *,
    repo: str,
    op: str,
    failure_type: str,
    issue_url: str,
    phase: str,
    submitted_via: str,
    soc: str | None = None,
    status: str = "submitted",
    parent_issue_url: str | None = None,
) -> None:
    data = _load()
    record = {
        "issue_url": issue_url,
        "submitted_at": datetime.now().isoformat(timespec="seconds"),
        "phase": phase,
        "submitted_via": submitted_via,
        "status": status,
    }
    if soc:
        record["soc"] = soc
    if parent_issue_url:
        record["parent_issue_url"] = parent_issue_url
    data[make_key(repo, op, failure_type)] = record
    _atomic_save(data)


def update_status(
    repo: str,
    op: str,
    failure_type: str,
    status: str,
    **extra_fields,
) -> None:
    """Update the status (and any extra fields) of an existing record in-place."""
    data = _load()
    key = make_key(repo, op, failure_type)
    if key not in data:
        raise KeyError(f"No state.json record for key: {key}")
    data[key]["status"] = status
    data[key].update(extra_fields)
    _atomic_save(data)


def load_all() -> dict:
    """Return a copy of the full state dict keyed by repo::op::failure_type."""
    return _load()


def split_new_vs_submitted(
    keys: Iterable[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    data = _load()
    new: list[tuple[str, str, str]] = []
    already: list[tuple[str, str, str]] = []
    for repo, op, ft in keys:
        if make_key(repo, op, ft) in data:
            already.append((repo, op, ft))
        else:
            new.append((repo, op, ft))
    return new, already
