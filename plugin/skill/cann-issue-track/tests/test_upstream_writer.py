"""Tests for upstream_writer: URL dispatch + mock network side-effects."""
import json
import os
import sys
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import upstream_writer


GH_ISSUE = "https://github.com/ascend/ops-transformer/issues/101"
GITEE_ISSUE = "https://gitee.com/ascend/ops-cv/issues/I7XYZ"
GITCODE_ISSUE = "https://gitcode.com/ascend/ops-math/issues/42"

BODY = "感谢您的回复！\n\n修复已验证。"


# ── post_comment ─────────────────────────────────────────────────────────────

def test_post_comment_github_calls_gh_cli() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        upstream_writer.post_comment(GH_ISSUE, BODY)
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "gh"
    assert "repos/ascend/ops-transformer/issues/101/comments" in cmd[2]
    assert f"body={BODY}" in cmd


def test_post_comment_gitee_calls_v5_api() -> None:
    with patch("urllib.request.urlopen") as mock_open, \
         patch.dict(os.environ, {"GITEE_TOKEN": "tok123"}):
        mock_open.return_value.__enter__.return_value.read.return_value = b"{}"
        upstream_writer.post_comment(GITEE_ISSUE, BODY)
    req = mock_open.call_args[0][0]
    assert "gitee.com/api/v5/repos/ascend/ops-cv/issues/I7XYZ/comments" in req.full_url
    assert "access_token=tok123" in req.full_url
    assert json.loads(req.data)["body"] == BODY


def test_post_comment_gitcode_uses_api_subdomain() -> None:
    # GitCode talks to api.gitcode.com via curl (python-ssl fails on some hosts).
    with patch("subprocess.run") as mock_run, \
         patch.dict(os.environ, {"GITCODE_TOKEN": "gc_tok"}):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="{}\n__HTTP_STATUS__=201", stderr="")
        upstream_writer.post_comment(GITCODE_ISSUE, BODY)
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "curl"
    url = next(a for a in cmd if a.startswith("https://"))
    assert url.startswith("https://api.gitcode.com/api/v5/")
    assert "access_token=gc_tok" in url
    payload = cmd[cmd.index("-d") + 1]
    assert json.loads(payload)["body"] == BODY


# ── close_issue ──────────────────────────────────────────────────────────────

def test_close_issue_github_patches_state() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        upstream_writer.close_issue(GH_ISSUE)
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "gh"
    assert "repos/ascend/ops-transformer/issues/101" in cmd[2]
    assert "state=closed" in cmd


def test_close_issue_gitee_sends_closed_state() -> None:
    with patch("urllib.request.urlopen") as mock_open, \
         patch.dict(os.environ, {"GITEE_TOKEN": "tok123"}):
        # close_issue does a GET first; return an open state so it proceeds to PATCH.
        mock_open.return_value.__enter__.return_value.read.return_value = b'{"state": "open"}'
        upstream_writer.close_issue(GITEE_ISSUE)
    req = mock_open.call_args[0][0]
    assert req.method == "PATCH"
    payload = json.loads(req.data)
    assert payload.get("state") == "closed"


def test_post_comment_unknown_url_raises() -> None:
    with pytest.raises(ValueError, match="Unrecognized"):
        upstream_writer.post_comment("https://example.com/foo/issues/1", BODY)


def test_gh_comment_failure_raises_runtime_error() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="gh: Unauthorized")
        with pytest.raises(RuntimeError, match="gh comment failed"):
            upstream_writer.post_comment(GH_ISSUE, BODY)


# ── dry-run mode ─────────────────────────────────────────────────────────────

def test_dry_run_post_comment_does_not_call_network(capsys) -> None:
    with patch.dict(os.environ, {"CANN_OPS_DRY_RUN": "1"}):
        with patch("subprocess.run") as mock_run, \
             patch("urllib.request.urlopen") as mock_open:
            upstream_writer.post_comment(GH_ISSUE, BODY)
            assert not mock_run.called
            assert not mock_open.called
    captured = capsys.readouterr()
    assert "DRY_RUN" in captured.out


def test_dry_run_close_issue_does_not_call_network(capsys) -> None:
    with patch.dict(os.environ, {"CANN_OPS_DRY_RUN": "1"}):
        with patch("subprocess.run") as mock_run, \
             patch("urllib.request.urlopen") as mock_open:
            upstream_writer.close_issue(GH_ISSUE)
            assert not mock_run.called
            assert not mock_open.called
    captured = capsys.readouterr()
    assert "DRY_RUN" in captured.out
