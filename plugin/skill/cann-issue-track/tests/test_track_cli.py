"""track.py（CLI 门面）单测。

重点核验两件事：
  ① 门面只做机械动作，不做方案判断（P2 仍归 agent）；
  ② 外发动作默认 dry-run —— 不带 --execute 时绝不调上游 API、不写 FAQ、不改 state.json。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import track  # noqa: E402
import paths  # noqa: E402


def _run(argv: list[str], capsys) -> tuple[int, dict]:
    rc = track.main(argv)
    out = capsys.readouterr().out
    return rc, (json.loads(out) if out.strip() else {})


# ── scope ────────────────────────────────────────────────────────────────────

def test_scope_groups_by_status(fake_submitted_state: Path, capsys) -> None:
    rc, data = _run(["scope"], capsys)
    assert rc == 0
    assert data["total"] >= 2
    labels = {k for k, v in data["groups"].items() if v}
    assert any("等待回复" in lb for lb in labels)


def test_scope_hides_deleted_upstream(tmp_cwd: Path, capsys) -> None:
    state = {"r::o::BUILD_FAIL": {"issue_url": "https://github.com/a/r/issues/1",
                                  "status": "deleted_upstream"}}
    p = tmp_cwd / "cann-ops-report" / "issues" / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state), encoding="utf-8")
    rc, data = _run(["scope"], capsys)
    assert data["total"] == 0


# ── fetch ────────────────────────────────────────────────────────────────────

_URL = "https://github.com/ascend/ops-transformer/issues/101"
_TARGET = ["--repo", "ops-transformer", "--op", "grouped_matmul",
           "--failure-type", "BUILD_FAIL"]


def test_fetch_closed_upstream_wins_over_comments(fake_submitted_state: Path, capsys) -> None:
    """开关状态优先于评论分类。"""
    with patch.object(track.fetch_comments, "fetch", return_value=[{"author": "x", "body": "hi"}]), \
         patch.object(track.fetch_comments, "fetch_issue_state",
                      return_value={"state": "closed", "closed_at": "2026-06-01", "body": "b"}):
        rc, data = _run(["fetch", *_TARGET, "--issue-url", _URL], capsys)
    assert data["classification"] == "closed_upstream"
    assert data["actionable"] is False


def test_fetch_persists_body_for_p30(fake_submitted_state: Path, tmp_cwd: Path, capsys) -> None:
    """body 必须落盘 —— P3.0 的 SOC 自动发现要读它。"""
    with patch.object(track.fetch_comments, "fetch", return_value=[]), \
         patch.object(track.fetch_comments, "fetch_issue_state",
                      return_value={"state": "open", "closed_at": None,
                                    "body": "| SOC | ascend910b |"}):
        _run(["fetch", *_TARGET, "--issue-url", _URL], capsys)
    body = tmp_cwd / "cann-ops-report" / "issues" / "bodies" / "ops-transformer" / "101.txt"
    assert body.is_file() and "ascend910b" in body.read_text(encoding="utf-8")


def test_fetch_fail_does_not_change_status(fake_submitted_state: Path, capsys) -> None:
    """拉取失败是网络问题，不能污染业务 status。"""
    dedup = track.dedup
    before = dedup.load_all()["ops-transformer::grouped_matmul::BUILD_FAIL"].get("status")
    with patch.object(track.fetch_comments, "fetch",
                      return_value={"status": "fetch_failed", "reason": "HTTP 500"}), \
         patch.object(track.fetch_comments, "fetch_issue_state",
                      return_value={"state": "open", "closed_at": None, "body": ""}):
        rc, data = _run(["fetch", *_TARGET, "--issue-url", _URL], capsys)
    assert data["classification"] == "fetch_failed"
    after = dedup.load_all()["ops-transformer::grouped_matmul::BUILD_FAIL"].get("status")
    assert after == before


def test_fetch_self_only(fake_submitted_state: Path, capsys) -> None:
    with patch.object(track.fetch_comments, "fetch",
                      return_value=[{"author": "me", "body": "ping"}]), \
         patch.object(track.fetch_comments, "fetch_issue_state",
                      return_value={"state": "open", "closed_at": None, "body": ""}):
        rc, data = _run(["fetch", *_TARGET, "--issue-url", _URL, "--submitter", "me"], capsys)
    assert data["classification"] == "self_only"
    assert data["actionable"] is False


def test_fetch_external_comments_are_actionable(fake_submitted_state: Path, capsys) -> None:
    with patch.object(track.fetch_comments, "fetch",
                      return_value=[{"author": "maintainer", "body": "try -DX=ON"}]), \
         patch.object(track.fetch_comments, "fetch_issue_state",
                      return_value={"state": "open", "closed_at": None, "body": ""}):
        rc, data = _run(["fetch", *_TARGET, "--issue-url", _URL, "--submitter", "me"], capsys)
    assert data["classification"] == "has_external_comments"
    assert data["actionable"] is True


# ── discover ─────────────────────────────────────────────────────────────────

def test_discover_reports_missing_fields(tmp_cwd: Path, capsys) -> None:
    with patch.object(track.context_discovery, "discover_soc", return_value=None), \
         patch.object(track.context_discovery, "discover_repo_path", return_value="/x"):
        rc, data = _run(["discover", *_TARGET], capsys)
    assert data["missing"] == ["soc"]
    assert data["repo_path"] == "/x"


# ── retest：三分支判定直接给出 ────────────────────────────────────────────────

def test_retest_surfaces_partial_verdict(tmp_cwd: Path, capsys) -> None:
    fake = {"status": "FAIL", "verdict": "PARTIAL", "op_status": "RUN_EXIT_FAIL",
            "reason": "原始失败已恢复", "detail": "", "log_path": ""}
    with patch.object(track.retest_orchestrator, "retest", return_value=fake) as m:
        rc, data = _run(["retest", *_TARGET, "--repo-path", "/r", "--soc", "ascend950"], capsys)
    assert data["verdict"] == "PARTIAL"
    # failure_type 必须透传，否则永远判不出 PARTIAL
    assert m.call_args.kwargs["context"]["failure_type"] == "BUILD_FAIL"


# ── 外发动作：默认 dry-run ────────────────────────────────────────────────────

_SEND = ["--soc", "ascend950", "--kind", "env", "--payload", "FOO=1"]


def test_finish_dry_run_sends_nothing(fake_submitted_state: Path, capsys) -> None:
    with patch.object(track.upstream_writer, "post_comment") as post, \
         patch.object(track.upstream_writer, "close_issue") as close, \
         patch.object(track.faq_writer, "upsert") as faq:
        rc, data = _run(["finish", *_TARGET, "--issue-url", _URL, "--outcome", "pass", *_SEND],
                        capsys)
    assert data["dry_run"] is True and data["body"]
    post.assert_not_called(); close.assert_not_called(); faq.assert_not_called()


def test_finish_execute_posts_and_closes(fake_submitted_state: Path, capsys) -> None:
    dedup = track.dedup
    with patch.object(track.upstream_writer, "post_comment") as post, \
         patch.object(track.upstream_writer, "close_issue") as close, \
         patch.object(track.faq_writer, "upsert") as faq:
        rc, data = _run(["finish", *_TARGET, "--issue-url", _URL, "--outcome", "pass",
                         *_SEND, "--execute"], capsys)
    assert data["dry_run"] is False and data["faq_written"] is True
    post.assert_called_once(); close.assert_called_once(); faq.assert_called_once()
    rec = dedup.load_all()["ops-transformer::grouped_matmul::BUILD_FAIL"]
    assert rec["status"] == "closed_by_track_issues" and rec["closed_at"]


def test_finish_fail_does_not_close_issue(fake_submitted_state: Path, capsys) -> None:
    """FAIL 分支只追问，不关 issue、不写 FAQ、不改 state。"""
    dedup = track.dedup
    before = dedup.load_all()["ops-transformer::grouped_matmul::BUILD_FAIL"].get("status")
    with patch.object(track.upstream_writer, "post_comment") as post, \
         patch.object(track.upstream_writer, "close_issue") as close, \
         patch.object(track.faq_writer, "upsert") as faq:
        rc, data = _run(["finish", *_TARGET, "--issue-url", _URL, "--outcome", "fail",
                         *_SEND, "--execute"], capsys)
    post.assert_called_once(); close.assert_not_called(); faq.assert_not_called()
    assert data["faq_written"] is False
    assert dedup.load_all()["ops-transformer::grouped_matmul::BUILD_FAIL"].get("status") == before


def test_finish_partial_requires_followup_url(fake_submitted_state: Path, capsys) -> None:
    """没有 follow-up URL 就关原 issue = 丢失新失败面，必须硬拒。"""
    with patch.object(track.upstream_writer, "post_comment") as post, \
         patch.object(track.upstream_writer, "close_issue") as close:
        rc = track.main(["finish", *_TARGET, "--issue-url", _URL, "--outcome", "partial",
                         *_SEND, "--execute"])
    assert rc == 1
    post.assert_not_called(); close.assert_not_called()


def test_followup_dry_run_creates_nothing(fake_submitted_state: Path, capsys) -> None:
    with patch.object(track.upstream_writer, "create_issue") as create:
        rc, data = _run(["followup", *_TARGET, "--issue-url", _URL, *_SEND], capsys)
    assert data["dry_run"] is True and "follow-up to #101" in data["title"]
    create.assert_not_called()


def test_followup_execute_registers_into_state(fake_submitted_state: Path, capsys) -> None:
    """红线：follow-up 必须注册进 state.json，否则下轮无法追踪。"""
    dedup = track.dedup
    new_url = "https://github.com/ascend/ops-transformer/issues/202"
    with patch.object(track.upstream_writer, "create_issue", return_value=new_url):
        rc, data = _run(["followup", *_TARGET, "--issue-url", _URL, *_SEND, "--execute"], capsys)
    assert data["followup_url"] == new_url
    rec = dedup.load_all()["ops-transformer::grouped_matmul::RUN_EXIT_FAIL"]
    assert rec["issue_url"] == new_url
    assert rec["parent_issue_url"] == _URL
    assert rec["status"] == "submitted"


# ── register ─────────────────────────────────────────────────────────────────

def test_register_writes_state_with_soc(tmp_cwd: Path, capsys) -> None:
    dedup = track.dedup
    rc, data = _run(["register", *_TARGET, "--issue-url", _URL, "--soc", "ascend910b"], capsys)
    rec = dedup.load_all()["ops-transformer::grouped_matmul::BUILD_FAIL"]
    assert rec["soc"] == "ascend910b" and rec["status"] == "submitted"
    assert rec["submitted_via"] == "manual"
