"""URL dispatch + response normalization for the three platforms."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import pytest
import fetch_comments

FIXTURES = Path(__file__).parent / "fixtures" / "comments"


def test_dispatch_github_uses_gh_cli() -> None:
    expected = (FIXTURES / "gh_response.json").read_text(encoding="utf-8")
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=expected, stderr="")
        out = fetch_comments.fetch(
            "https://github.com/ascend/ops-transformer/issues/101"
        )
    assert mock_run.called
    cmd = mock_run.call_args[0][0]
    assert cmd[:2] == ["gh", "api"]
    assert "repos/ascend/ops-transformer/issues/101/comments" in cmd[2]
    assert len(out) == 2
    assert out[0]["body"].startswith("try setting")
    assert out[0]["author"] == "maintainer-bot"
    assert out[0]["role"] == "MEMBER"


def test_dispatch_gitee_calls_v5_api() -> None:
    body = (FIXTURES / "gitee_response.json").read_text(encoding="utf-8").encode()
    with patch("urllib.request.urlopen") as mock_open, \
         patch.dict(os.environ, {"GITEE_TOKEN": "fake"}):
        ctx = MagicMock()
        ctx.read.return_value = body
        mock_open.return_value.__enter__.return_value = ctx
        out = fetch_comments.fetch(
            "https://gitee.com/ascend/ops-cv/issues/I7XYZ"
        )
    req_arg = mock_open.call_args[0][0]
    assert "gitee.com/api/v5/repos/ascend/ops-cv/issues/I7XYZ/comments" in req_arg.full_url
    assert "access_token=fake" in req_arg.full_url
    assert len(out) == 1
    assert out[0]["body"].startswith("建议")


def test_dispatch_gitcode_uses_api_subdomain() -> None:
    body = (FIXTURES / "gitcode_response.json").read_text(encoding="utf-8").encode()
    with patch("urllib.request.urlopen") as mock_open, \
         patch.dict(os.environ, {"GITCODE_TOKEN": "fake"}):
        ctx = MagicMock()
        ctx.read.return_value = body
        mock_open.return_value.__enter__.return_value = ctx
        fetch_comments.fetch("https://gitcode.com/ascend/ops-math/issues/42")
    req_arg = mock_open.call_args[0][0]
    assert req_arg.full_url.startswith("https://api.gitcode.com/api/v5/")
    assert "access_token=fake" in req_arg.full_url


def test_unknown_domain_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        fetch_comments.fetch("https://example.com/foo/bar/issues/1")


def test_404_returns_deleted_marker() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="gh: Not Found (HTTP 404)"
        )
        result = fetch_comments.fetch(
            "https://github.com/x/y/issues/999",
            raise_on_error=False,
        )
    assert result == {"status": "deleted_upstream"}
