"""Build prefilled issue-creation URLs with a length guard.

Both GitHub and Gitee support:
    https://<host>/<owner>/<repo>/issues/new?title=<urlenc>&body=<urlenc>

GitHub additionally supports &labels=<csv>. Gitee does not.

URLs longer than ~7.5KB get truncated by some browsers + the issue page server.
If we'd cross that, we degrade to a bare 'new issue' URL.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, urlencode

from . import platforms

DEFAULT_MAX_URL_BYTES = 7500


@dataclass(frozen=True)
class UrlResult:
    url: str
    degraded: bool


def build_prefilled_url(
    *,
    platform: str,
    owner: str,
    repo: str,
    title: str,
    body: str,
    labels: list[str],
    max_url_bytes: int = DEFAULT_MAX_URL_BYTES,
) -> UrlResult:
    adapter = platforms.get_adapter(platform)
    base = adapter.new_issue_base(owner, repo)

    params: dict[str, str] = {"title": title, "body": body}
    if labels and adapter.supports_labels_query:
        params["labels"] = ",".join(labels)

    full = f"{base}?{urlencode(params, quote_via=quote)}"
    if len(full.encode("utf-8")) > max_url_bytes:
        return UrlResult(url=base, degraded=True)
    return UrlResult(url=full, degraded=False)
