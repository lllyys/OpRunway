from pathlib import Path

import pytest

from scripts import orchestrate


def test_generate_drafts_end_to_end(tmp_cwd: Path, fake_run_state: Path,
                                      fake_logs: Path, fake_repo: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    # Pretend ops-transformer's local path is fake_repo (so git rev works)
    repo_paths = {"ops-transformer": fake_repo, "ops-cv": fake_repo}
    result = orchestrate.generate_drafts(
        repo_paths=repo_paths,
        soc="ascend950",
        granularities=["per_op", "by_type", "whole_repo"],
        skip_resolved=False,  # don't dedup-skip in tests
    )
    assert result["ops-transformer"]["per_op_files"]  # at least one file
    assert result["ops-transformer"]["by_type_files"]
    assert result["ops-transformer"]["whole_repo_file"]
    assert result["ops-cv"]["per_op_files"]
    # Files exist on disk
    drafts = tmp_cwd / "cann-ops-report" / "issues" / "drafts" / "ops-transformer" / "per_op"
    assert any(drafts.iterdir())


def test_generate_drafts_respects_dedup(tmp_cwd: Path, fake_run_state: Path,
                                          fake_logs: Path, fake_repo: Path) -> None:
    from scripts import dedup
    dedup.mark_submitted(repo="ops-transformer", op="grouped_matmul",
                          failure_type="BUILD_FAIL",
                          issue_url="x", phase="phase1", submitted_via="api")
    repo_paths = {"ops-transformer": fake_repo}
    result = orchestrate.generate_drafts(
        repo_paths=repo_paths,
        soc="ascend950",
        granularities=["per_op"],
        skip_resolved=True,
    )
    # grouped_matmul BUILD_FAIL should be skipped, but other ops-transformer failures still produced
    skipped = result["ops-transformer"]["skipped_already_submitted"]
    assert ("grouped_matmul", "BUILD_FAIL") in skipped
