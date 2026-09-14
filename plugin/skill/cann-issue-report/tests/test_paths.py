from pathlib import Path

from scripts import paths


def test_paths_are_under_cann_ops_report_issues(tmp_cwd: Path) -> None:
    assert paths.WORK_DIR == tmp_cwd / "cann-ops-report" / "issues"
    assert paths.STATE_FILE == paths.WORK_DIR / "state.json"
    assert paths.REPOS_FILE == paths.WORK_DIR / "repos.json"
    assert paths.DRAFTS_DIR == paths.WORK_DIR / "drafts"
    assert paths.SUBMITTED_DIR == paths.WORK_DIR / "submitted"


def test_iter_repo_states(tmp_cwd: Path, fake_run_state: Path) -> None:
    repos = [r for r, _ in paths.iter_repo_states()]
    assert repos == ["ops-cv", "ops-transformer"]
