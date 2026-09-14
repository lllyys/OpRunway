import json
import subprocess
from pathlib import Path

import pytest

from scripts import repo_resolver


def test_parse_https_github() -> None:
    assert repo_resolver.parse_remote_url(
        "https://github.com/ascend/ops-transformer.git"
    ) == ("github", "ascend", "ops-transformer")


def test_parse_https_gitee() -> None:
    assert repo_resolver.parse_remote_url(
        "https://gitee.com/ascend/ops-cv.git"
    ) == ("gitee", "ascend", "ops-cv")


def test_parse_ssh_github() -> None:
    assert repo_resolver.parse_remote_url(
        "git@github.com:ascend/ops-math.git"
    ) == ("github", "ascend", "ops-math")


def test_parse_ssh_gitee() -> None:
    assert repo_resolver.parse_remote_url(
        "git@gitee.com:ascend/ops-nn.git"
    ) == ("gitee", "ascend", "ops-nn")


def test_parse_https_gitcode() -> None:
    assert repo_resolver.parse_remote_url(
        "https://gitcode.com/cann/ops-nn.git"
    ) == ("gitcode", "cann", "ops-nn")


def test_parse_ssh_gitcode() -> None:
    assert repo_resolver.parse_remote_url(
        "git@gitcode.com:cann/ops-nn.git"
    ) == ("gitcode", "cann", "ops-nn")


def test_parse_unknown_host_returns_none() -> None:
    assert repo_resolver.parse_remote_url(
        "https://internal-mirror.example.com/team/ops.git"
    ) is None


def test_parse_malformed_returns_none() -> None:
    assert repo_resolver.parse_remote_url("not-a-url") is None


def test_resolve_from_git_remote(fake_repo: Path) -> None:
    result = repo_resolver.resolve_from_remote(fake_repo)
    assert result == ("github", "ascend", "ops-transformer")


def test_resolve_from_git_remote_no_remote(tmp_path: Path) -> None:
    bare = tmp_path / "no-remote"
    bare.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=bare, check=True)
    assert repo_resolver.resolve_from_remote(bare) is None


def test_repos_cache_round_trip(tmp_cwd: Path) -> None:
    repo_resolver.write_cache("ops-transformer", ("github", "ascend", "ops-transformer"),
                              source="git_remote")
    cached = repo_resolver.read_cache("ops-transformer")
    assert cached == ("github", "ascend", "ops-transformer")


def test_repos_cache_miss(tmp_cwd: Path) -> None:
    assert repo_resolver.read_cache("nonexistent") is None
