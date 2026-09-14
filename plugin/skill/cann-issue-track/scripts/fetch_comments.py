"""Pull issue comments from GitHub (via gh CLI), Gitee v5, or GitCode v5.

Returns normalized list of {"author", "role", "body", "created_at"}
or {"status": "deleted_upstream"} / {"status": "fetch_failed", "reason": ...}
when raise_on_error=False and error occurs.

Auth:
- GitHub: gh CLI (assumes gh auth login)
- Gitee:  GITEE_TOKEN env var (query param)
- GitCode: GITCODE_TOKEN env var (query param, api.gitcode.com subdomain)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request


_GH_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)")
_GITEE_RE = re.compile(r"https?://gitee\.com/([^/]+)/([^/]+)/issues/([^/?#]+)")
_GITCODE_RE = re.compile(r"https?://gitcode\.com/([^/]+)/([^/]+)/issues/(\d+)")


def fetch(issue_url: str, *, raise_on_error: bool = True):
    """Dispatch by URL; return list of comments or {"status": ...} on known errors."""
    m = _GH_RE.match(issue_url)
    if m:
        return _fetch_github(*m.groups(), raise_on_error=raise_on_error)
    m = _GITEE_RE.match(issue_url)
    if m:
        return _fetch_gitee(*m.groups(), raise_on_error=raise_on_error)
    m = _GITCODE_RE.match(issue_url)
    if m:
        return _fetch_gitcode(*m.groups(), raise_on_error=raise_on_error)
    raise ValueError(f"Unsupported issue URL: {issue_url}")


def fetch_issue_state(issue_url: str, *, raise_on_error: bool = True) -> dict:
    """Return {"state": "open"|"closed", "closed_at": str|None, "body": str}.

    `body` is the issue description; P3.0 (context_discovery.discover_soc) reads it
    back from cann-ops-report/issues/bodies/<repo>/<id>.txt to recover the SOC, so
    the caller must persist it.

    On error returns {"status": "fetch_failed", "reason": ...} when
    raise_on_error=False, or raises RuntimeError when raise_on_error=True.
    """
    m = _GH_RE.match(issue_url)
    if m:
        return _gh_issue_state(*m.groups(), raise_on_error=raise_on_error)
    m = _GITEE_RE.match(issue_url)
    if m:
        return _gitee_issue_state(*m.groups(), raise_on_error=raise_on_error)
    m = _GITCODE_RE.match(issue_url)
    if m:
        return _gitcode_issue_state(*m.groups(), raise_on_error=raise_on_error)
    raise ValueError(f"Unsupported issue URL: {issue_url}")


def _gh_issue_state(owner: str, repo: str, num: str, *, raise_on_error: bool) -> dict:
    proc = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/issues/{num}"],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        if raise_on_error:
            raise RuntimeError(f"gh api failed: {proc.stderr.strip()}")
        return {"status": "fetch_failed", "reason": proc.stderr.strip()[:200]}
    raw = json.loads(proc.stdout)
    return {"state": raw.get("state", "open"), "closed_at": raw.get("closed_at"),
            "body": raw.get("body") or ""}


def _gitee_issue_state(owner: str, repo: str, num: str, *, raise_on_error: bool) -> dict:
    token = os.environ.get("GITEE_TOKEN", "")
    if not token:
        raise RuntimeError("GITEE_TOKEN not set")
    url = (f"https://gitee.com/api/v5/repos/{owner}/{repo}/issues/{num}"
           f"?access_token={token}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as resp:
            raw = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404 and not raise_on_error:
            return {"status": "fetch_failed", "reason": "HTTP 404"}
        if raise_on_error:
            raise RuntimeError(f"Gitee HTTP {e.code}") from e
        return {"status": "fetch_failed", "reason": f"HTTP {e.code}"}
    # Gitee uses "finished_at" for the closure timestamp
    return {"state": raw.get("state", "open"), "closed_at": raw.get("finished_at"),
            "body": raw.get("body") or ""}


def _gitcode_issue_state(owner: str, repo: str, num: str, *, raise_on_error: bool) -> dict:
    token = os.environ.get("GITCODE_TOKEN", "")
    if not token:
        raise RuntimeError("GITCODE_TOKEN not set")
    url = (f"https://api.gitcode.com/api/v5/repos/{owner}/{repo}/issues/{num}"
           f"?access_token={token}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as resp:
            raw = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404 and not raise_on_error:
            return {"status": "fetch_failed", "reason": "HTTP 404"}
        if raise_on_error:
            raise RuntimeError(f"GitCode HTTP {e.code}") from e
        return {"status": "fetch_failed", "reason": f"HTTP {e.code}"}
    return {"state": raw.get("state", "open"), "closed_at": raw.get("finished_at"),
            "body": raw.get("body") or ""}


def _fetch_github(owner: str, repo: str, num: str, *, raise_on_error: bool):
    api_path = f"repos/{owner}/{repo}/issues/{num}/comments"
    proc = subprocess.run(
        ["gh", "api", api_path],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        if "404" in proc.stderr or "Not Found" in proc.stderr:
            if not raise_on_error:
                return {"status": "deleted_upstream"}
        if raise_on_error:
            raise RuntimeError(f"gh api failed: {proc.stderr.strip()}")
        return {"status": "fetch_failed", "reason": proc.stderr.strip()[:200]}
    raw = json.loads(proc.stdout)
    return [
        {"author": c.get("user", {}).get("login", ""),
         "role": c.get("author_association", ""),
         "body": c.get("body", ""),
         "created_at": c.get("created_at", "")}
        for c in raw
    ]


def _fetch_gitee(owner: str, repo: str, num: str, *, raise_on_error: bool):
    token = os.environ.get("GITEE_TOKEN", "")
    if not token:
        raise RuntimeError("GITEE_TOKEN not set")
    url = (f"https://gitee.com/api/v5/repos/{owner}/{repo}/issues/{num}/comments"
           f"?access_token={token}")
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404 and not raise_on_error:
            return {"status": "deleted_upstream"}
        if raise_on_error:
            raise RuntimeError(f"Gitee HTTP {e.code}") from e
        return {"status": "fetch_failed", "reason": f"HTTP {e.code}"}
    return [
        {"author": c.get("user", {}).get("login", ""),
         "role": c.get("user", {}).get("role", ""),
         "body": c.get("body", ""),
         "created_at": c.get("created_at", "")}
        for c in raw
    ]


def _fetch_gitcode(owner: str, repo: str, num: str, *, raise_on_error: bool):
    token = os.environ.get("GITCODE_TOKEN", "")
    if not token:
        raise RuntimeError("GITCODE_TOKEN not set")
    url = (f"https://api.gitcode.com/api/v5/repos/{owner}/{repo}/issues/{num}/comments"
           f"?access_token={token}")
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404 and not raise_on_error:
            return {"status": "deleted_upstream"}
        if raise_on_error:
            raise RuntimeError(f"GitCode HTTP {e.code}") from e
        return {"status": "fetch_failed", "reason": f"HTTP {e.code}"}
    return [
        {"author": c.get("user", {}).get("login", ""),
         "role": c.get("user", {}).get("role", ""),
         "body": c.get("body", ""),
         "created_at": c.get("created_at", "")}
        for c in raw
    ]
