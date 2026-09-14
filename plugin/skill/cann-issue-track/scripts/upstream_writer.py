"""Post a comment, create a follow-up issue, or close an upstream issue.

Auth (same as fetch_comments):
- GitHub:  gh CLI
- Gitee:   GITEE_TOKEN env var
- GitCode: GITCODE_TOKEN env var (api.gitcode.com)
           GitCode requests go through curl, because some hosts'
           python-ssl can't complete the handshake to api.gitcode.com.

Dry-run:
  Set CANN_OPS_DRY_RUN=1 to print what would be sent without making network calls.

Issue URL handling:
  Comment / close take a `<base>/issues/<num>` URL.
  Create takes a `<base>` repo URL (e.g. https://gitcode.com/cann/ops-nn).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request

_GH_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)")
_GH_REPO_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)/?$")
_GITEE_RE = re.compile(r"https?://gitee\.com/([^/]+)/([^/]+)/issues/([^/?#]+)")
_GITEE_REPO_RE = re.compile(r"https?://gitee\.com/([^/]+)/([^/]+)/?$")
_GITCODE_RE = re.compile(r"https?://gitcode\.com/([^/]+)/([^/]+)/issues/(\d+)")
_GITCODE_REPO_RE = re.compile(r"https?://gitcode\.com/([^/]+)/([^/]+)/?$")


def _is_dry_run() -> bool:
    return os.environ.get("CANN_OPS_DRY_RUN", "").strip() == "1"


def post_comment(issue_url: str, body: str) -> None:
    """Post a markdown comment. Raises RuntimeError on failure."""
    if _is_dry_run():
        print(f"[DRY_RUN] post_comment → {issue_url}\n{body[:200]}")
        return

    m = _GH_RE.match(issue_url)
    if m:
        _gh_comment(m.group(1), m.group(2), m.group(3), body)
        return

    m = _GITEE_RE.match(issue_url)
    if m:
        _gitee_comment(m.group(1), m.group(2), m.group(3), body)
        return

    m = _GITCODE_RE.match(issue_url)
    if m:
        _gitcode_comment(m.group(1), m.group(2), m.group(3), body)
        return

    raise ValueError(f"Unrecognized issue URL: {issue_url}")


def close_issue(issue_url: str) -> None:
    """Close the issue. Raises RuntimeError on failure."""
    if _is_dry_run():
        print(f"[DRY_RUN] close_issue → {issue_url}")
        return

    m = _GH_RE.match(issue_url)
    if m:
        _gh_close(m.group(1), m.group(2), m.group(3))
        return

    m = _GITEE_RE.match(issue_url)
    if m:
        _gitee_close(m.group(1), m.group(2), m.group(3))
        return

    m = _GITCODE_RE.match(issue_url)
    if m:
        _gitcode_close(m.group(1), m.group(2), m.group(3))
        return

    raise ValueError(f"Unrecognized issue URL: {issue_url}")


def create_issue(repo_url: str, *, title: str, body: str) -> str:
    """Open a new issue. Returns the new issue's HTML URL.

    repo_url may be either the repo root (preferred, e.g.
    `https://gitcode.com/cann/ops-nn`) or any URL containing the owner/repo
    pair followed by `/issues/...` — both shapes are supported so callers can
    pass an existing issue URL when they want to file a sibling.
    """
    if _is_dry_run():
        print(f"[DRY_RUN] create_issue → {repo_url}\nTITLE: {title}\n{body[:200]}")
        return f"{repo_url.rstrip('/')}/issues/DRYRUN"

    owner, name, host = _parse_repo(repo_url)
    if host == "github":
        return _gh_create(owner, name, title, body)
    if host == "gitee":
        return _gitee_create(owner, name, title, body)
    if host == "gitcode":
        return _gitcode_create(owner, name, title, body)
    raise ValueError(f"Unrecognized repo URL: {repo_url}")


def _parse_repo(url: str) -> tuple[str, str, str]:
    """Return (owner, repo, host) where host in {github, gitee, gitcode}."""
    for host, repo_re, issue_re in (
        ("github", _GH_REPO_RE, _GH_RE),
        ("gitee", _GITEE_REPO_RE, _GITEE_RE),
        ("gitcode", _GITCODE_REPO_RE, _GITCODE_RE),
    ):
        m = repo_re.match(url) or issue_re.match(url)
        if m:
            return m.group(1), m.group(2), host
    raise ValueError(f"Unrecognized repo or issue URL: {url}")


# ── GitHub ──────────────────────────────────────────────────────────────────

def _gh_comment(owner: str, repo: str, num: str, body: str) -> None:
    r = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/issues/{num}/comments",
         "-X", "POST", "-f", f"body={body}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh comment failed: {r.stderr}")


def _gh_close(owner: str, repo: str, num: str) -> None:
    r = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/issues/{num}",
         "-X", "PATCH", "-f", "state=closed"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh close failed: {r.stderr}")


def _gh_create(owner: str, repo: str, title: str, body: str) -> str:
    r = subprocess.run(
        ["gh", "api", f"repos/{owner}/{repo}/issues",
         "-X", "POST", "-f", f"title={title}", "-f", f"body={body}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh create failed: {r.stderr}")
    return json.loads(r.stdout).get("html_url", "")


# ── Gitee ───────────────────────────────────────────────────────────────────

def _gitee_req(method: str, path: str, payload: dict | None) -> dict:
    token = os.environ.get("GITEE_TOKEN", "")
    url = f"https://gitee.com/api/v5/{path}?access_token={token}"
    headers = {"Content-Type": "application/json"}
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gitee API error {e.code}: {e.read().decode(errors='replace')}")


def _gitee_comment(owner: str, repo: str, num: str, body: str) -> None:
    _gitee_req("POST", f"repos/{owner}/{repo}/issues/{num}/comments", {"body": body})


def _gitee_close(owner: str, repo: str, num: str) -> None:
    # GitCode/Gitee reject state PATCH on already-closed issues with a misleading
    # "missing params" 400. Pre-check via GET; skip if already closed.
    state = _gitee_req("GET", f"repos/{owner}/{repo}/issues/{num}", None).get("state")
    if state == "closed":
        return
    _gitee_req("PATCH", f"repos/{owner}/{repo}/issues/{num}", {"state": "closed"})


def _gitee_create(owner: str, repo: str, title: str, body: str) -> str:
    # Gitee v5 issue creation lives under the owner, not the repo.
    payload = {"repo": repo, "title": title, "body": body}
    resp = _gitee_req("POST", f"repos/{owner}/issues", payload)
    return resp.get("html_url", "")


# ── GitCode ──────────────────────────────────────────────────────────────────

def _gitcode_req(method: str, path: str, payload: dict | None) -> dict:
    """Speak to api.gitcode.com via curl. Python urllib SSL fails on some hosts."""
    token = os.environ.get("GITCODE_TOKEN", "")
    if not token:
        raise RuntimeError("GITCODE_TOKEN not set")
    url = f"https://api.gitcode.com/api/v5/{path}?access_token={token}"
    cmd = [
        "curl", "-sS", "--fail-with-body",
        "-w", "\n__HTTP_STATUS__=%{http_code}",
        "-X", method, url,
        "-H", "Content-Type: application/json",
    ]
    if payload is not None:
        cmd.extend(["-d", json.dumps(payload)])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"GitCode {method} {path} failed (curl rc={proc.returncode}): {proc.stderr[-500:] or proc.stdout[-500:]}")
    out = proc.stdout
    status = ""
    if "\n__HTTP_STATUS__=" in out:
        out, _, status = out.rpartition("\n__HTTP_STATUS__=")
        status = status.strip()
    if status and not status.startswith("2"):
        raise RuntimeError(f"GitCode {method} {path} HTTP {status}: {out[-500:]}")
    try:
        return json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        return {"_raw": out}


def _gitcode_comment(owner: str, repo: str, num: str, body: str) -> None:
    _gitcode_req("POST", f"repos/{owner}/{repo}/issues/{num}/comments", {"body": body})


def _gitcode_close(owner: str, repo: str, num: str) -> None:
    # GitCode rejects state PATCH on already-closed issues with a misleading
    # "missing params" 400. Pre-check via GET; skip if already closed.
    state = _gitcode_req("GET", f"repos/{owner}/{repo}/issues/{num}", None).get("state")
    if state == "closed":
        return
    _gitcode_req("PATCH", f"repos/{owner}/{repo}/issues/{num}", {"state": "closed"})


def _gitcode_create(owner: str, repo: str, title: str, body: str) -> str:
    # GitCode follows Gitee v5: create via repos/{owner}/issues with repo param.
    payload = {"repo": repo, "title": title, "body": body}
    resp = _gitcode_req("POST", f"repos/{owner}/issues", payload)
    return resp.get("html_url") or f"https://gitcode.com/{owner}/{repo}/issues/{resp.get('number','')}"
