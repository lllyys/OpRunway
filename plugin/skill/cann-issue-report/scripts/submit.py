"""Submit issues to GitHub (via gh CLI), Gitee, and GitCode (via urllib).

GitHub: uses `gh issue create` so auth is managed by gh CLI's own login.
Gitee:  POST gitee.com/api/v5/repos/{owner}/{repo}/issues, token in JSON body.
GitCode: POST api.gitcode.com/api/v5/repos/{owner}/{repo}/issues,
         token as access_token query parameter (NOT a header — gitcode.com's
         CloudWAF blocks /api/* on the bare domain; the api.gitcode.com
         subdomain is the only reliable path). labels MUST be a CSV string,
         never a JSON array (GitCode 422s on arrays — same as Gitee v5).

P0 加固（2026-05-25）：
  - 标签预校验：POST 前先 GET /labels 与请求 labels 做交集，
    避免上游不存在的 label 触发 GitCode 误导性的 400 + "apig token has not permission"
  - 错误信息解包：urllib.error.HTTPError → 读 body 解出 error_code/error_message + 给下一步建议
  - GET 回查：POST 成功拿到 number 后再 GET /issues/{number} 校验 title 匹配
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request as urllib_request
from pathlib import Path
from typing import Optional


# HTTP 错误码 → 可执行的下一步建议（基于真实踩坑经验）
ERROR_HINTS = {
    400: ("可能 labels/assignees 含上游不存在的项（GitCode 会用误导性 400 + "
          "'apig token has not permission' 文案）；先 GET /labels 做交集"),
    401: "token 失效或未携带；检查 GITCODE_TOKEN / GITEE_TOKEN 或重新生成（勾 issues + pull_requests 权限）",
    403: "token 没写权限，或仓库限制提 issue；在平台设置中勾上 issues:write",
    404: "owner/repo 路径不存在；核对 remote URL",
    422: "字段校验失败：labels 必须是 CSV 字符串而非数组，title ≤ 255 字符",
    429: "限流，稍后重试",
}


def _format_http_error(platform: str, e: urllib.error.HTTPError) -> str:
    """解包 HTTPError body 提取 error_code/error_message，并附下一步建议。"""
    try:
        raw = e.read().decode("utf-8", errors="replace") if getattr(e, "fp", None) else ""
    except Exception:
        raw = ""
    err_code = e.code
    err_msg = e.reason or ""
    if raw:
        try:
            j = json.loads(raw)
            if isinstance(j, dict):
                err_code = j.get("error_code", err_code)
                err_msg = (j.get("error_message") or j.get("message")
                           or err_msg or raw[:200])
            else:
                err_msg = raw[:200]
        except Exception:
            err_msg = raw[:200]
    hint = ERROR_HINTS.get(err_code) or ERROR_HINTS.get(e.code) or ""
    out = f"{platform} API HTTP {e.code}"
    if err_code != e.code:
        out += f" (error_code={err_code})"
    out += f": {err_msg}"
    if hint:
        out += f"\n  → {hint}"
    return out


def get_existing_labels(
    *, platform: str, owner: str, repo: str, token: str,
    timeout: int = 15,
) -> Optional[set[str]]:
    """获取仓库现有 label 集合；失败返回 None（caller 视为'放弃过滤'）。

    Gitee/GitCode v5 都是 `GET /repos/{owner}/{repo}/labels?access_token=...&per_page=100`。
    任何不确定情形（网络错 / HTTP 错 / 返回非 list）一律 fail-open 返回 None，
    由 caller 决定是把原 labels 原样发还是另作处理。
    """
    if platform == "gitcode":
        url = (f"https://api.gitcode.com/api/v5/repos/{owner}/{repo}/labels"
               f"?access_token={token}&per_page=100")
    elif platform == "gitee":
        url = (f"https://gitee.com/api/v5/repos/{owner}/{repo}/labels"
               f"?access_token={token}&per_page=100")
    else:
        return None
    req = urllib_request.Request(
        url, method="GET", headers={"Accept": "application/json"},
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    names: set[str] = set()
    for item in data:
        if isinstance(item, dict):
            n = item.get("name")
            if isinstance(n, str) and n:
                names.add(n)
    return names


def _filter_labels(
    labels: list[str], *, platform: str, owner: str, repo: str, token: str,
) -> tuple[list[str], list[str]]:
    """labels 与上游现有 labels 做交集，返回 (filtered, dropped)。

    上游不可用（get_existing_labels 返回 None）时 fail-open，原 labels 继续发，
    交给 POST 自己报错——但 POST 的报错此时会被 _format_http_error 解释清楚。
    """
    if not labels:
        return labels, []
    existing = get_existing_labels(
        platform=platform, owner=owner, repo=repo, token=token)
    if existing is None:
        return labels, []
    filtered = [l for l in labels if l in existing]
    dropped = [l for l in labels if l not in existing]
    return filtered, dropped


def _verify_issue(
    *, platform: str, owner: str, repo: str, number: int,
    expected_title: str, token: str, timeout: int = 15,
) -> None:
    """POST 后回查 issue 内容；title 不匹配仅 warning，不抛——issue 已建。"""
    if platform == "gitcode":
        url = (f"https://api.gitcode.com/api/v5/repos/{owner}/{repo}/issues/{number}"
               f"?access_token={token}")
    elif platform == "gitee":
        url = (f"https://gitee.com/api/v5/repos/{owner}/{repo}/issues/{number}"
               f"?access_token={token}")
    else:
        return
    req = urllib_request.Request(
        url, method="GET", headers={"Accept": "application/json"},
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[warn] {platform} GET-verify failed for issue #{number}: {e} "
              f"(issue created but content not verified)", file=sys.stderr)
        return
    if not isinstance(data, dict):
        print(f"[warn] {platform} GET-verify returned non-dict for issue #{number}",
              file=sys.stderr)
        return
    actual_title = data.get("title", "")
    if actual_title != expected_title:
        print(f"[warn] {platform} GET-verify: title mismatch for issue #{number} "
              f"(expected={expected_title!r}, actual={actual_title!r})",
              file=sys.stderr)


def submit_github(
    *,
    owner: str,
    repo: str,
    title: str,
    body_file: Path,
    labels: list[str],
) -> str:
    args = ["gh", "issue", "create",
             "--repo", f"{owner}/{repo}",
             "--title", title,
             "--body-file", str(body_file)]
    for label in labels:
        args += ["--label", label]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"gh issue create failed: {proc.stderr.strip()}")
    # gh prints the URL on its own line
    for line in proc.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith("https://"):
            return line
    raise RuntimeError(f"gh issue create succeeded but URL not parsed: {proc.stdout!r}")


def submit_gitee(
    *,
    owner: str,
    repo: str,
    title: str,
    body: str,
    labels: list[str],
    token: str,
) -> str:
    labels, dropped = _filter_labels(
        labels, platform="gitee", owner=owner, repo=repo, token=token)
    if dropped:
        print(f"[warn] Gitee labels not in {owner}/{repo}, dropped: {dropped}",
              file=sys.stderr)

    url = f"https://gitee.com/api/v5/repos/{owner}/{repo}/issues"
    payload: dict = {
        "access_token": token,
        "repo": repo,
        "title": title,
        "body": body,
    }
    if labels:
        payload["labels"] = ",".join(labels)
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json;charset=UTF-8"},
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            body_bytes = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(_format_http_error("Gitee", e)) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Gitee API network error: {e}") from e
    result = json.loads(body_bytes.decode("utf-8"))
    issue_url = result.get("html_url") or result.get("url") or "(missing URL in response)"

    number = result.get("number")
    if isinstance(number, int):
        _verify_issue(platform="gitee", owner=owner, repo=repo, number=number,
                      expected_title=title, token=token)
    return issue_url


def submit_gitcode(
    *,
    owner: str,
    repo: str,
    title: str,
    body: str,
    labels: list[str],
    token: str,
) -> str:
    labels, dropped = _filter_labels(
        labels, platform="gitcode", owner=owner, repo=repo, token=token)
    if dropped:
        print(f"[warn] GitCode labels not in {owner}/{repo}, dropped: {dropped}",
              file=sys.stderr)

    url = (f"https://api.gitcode.com/api/v5/repos/{owner}/{repo}/issues"
           f"?access_token={token}")
    payload: dict = {"title": title, "body": body}
    if labels:
        payload["labels"] = ",".join(labels)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(
        url, data=data, method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
    )
    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            body_bytes = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(_format_http_error("GitCode", e)) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitCode API network error: {e}") from e
    result = json.loads(body_bytes.decode("utf-8"))
    issue_url = result.get("html_url") or result.get("url") or "(missing URL in response)"

    number = result.get("number")
    if isinstance(number, int):
        _verify_issue(platform="gitcode", owner=owner, repo=repo, number=number,
                      expected_title=title, token=token)
    return issue_url
