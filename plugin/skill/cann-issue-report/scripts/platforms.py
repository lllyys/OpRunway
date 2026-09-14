"""Thin adapter exposing platform-specific URLs + label-query semantics.

Submission (API) lives in submit.py — this module is read-only metadata.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformAdapter:
    name: str
    base_url: str
    supports_labels_query: bool

    def new_issue_base(self, owner: str, repo: str) -> str:
        return f"{self.base_url}/{owner}/{repo}/issues/new"


_ADAPTERS = {
    "github": PlatformAdapter(name="github", base_url="https://github.com",
                               supports_labels_query=True),
    "gitee": PlatformAdapter(name="gitee", base_url="https://gitee.com",
                              supports_labels_query=False),
    "gitcode": PlatformAdapter(name="gitcode", base_url="https://gitcode.com",
                                supports_labels_query=False),
}


def get_adapter(platform: str) -> PlatformAdapter:
    if platform not in _ADAPTERS:
        raise ValueError(f"unknown platform: {platform}")
    return _ADAPTERS[platform]
