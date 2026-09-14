from pathlib import Path

import pytest

from scripts import failures


FAIL_STATUSES = {"BUILD_FAIL", "INSTALL_FAIL", "RUN_EXIT_FAIL",
                 "RUN_PATTERN_FAIL", "TIMEOUT"}


def test_load_failures_groups_by_repo_and_type(fake_run_state: Path) -> None:
    grouped = failures.load_failures()

    # ops-transformer has 3 failures: BUILD_FAIL, RUN_EXIT_FAIL, TIMEOUT
    assert set(grouped["ops-transformer"].keys()) == {"BUILD_FAIL", "RUN_EXIT_FAIL", "TIMEOUT"}
    assert grouped["ops-transformer"]["BUILD_FAIL"][0].op == "grouped_matmul"
    assert grouped["ops-transformer"]["RUN_EXIT_FAIL"][0].op == "moe"
    assert grouped["ops-transformer"]["TIMEOUT"][0].op == "topk"

    # ops-cv has 1 failure: BUILD_FAIL
    assert set(grouped["ops-cv"].keys()) == {"BUILD_FAIL"}


def test_load_failures_excludes_pass_pending(fake_run_state: Path) -> None:
    grouped = failures.load_failures()
    all_ops = {f.op for group in grouped.values() for ops in group.values() for f in ops}
    assert "flash_attention" not in all_ops  # PASS
    assert "pending_op" not in all_ops       # PENDING


def test_load_failures_missing_state_file_raises(tmp_cwd: Path) -> None:
    with pytest.raises(FileNotFoundError, match="run_state.json"):
        failures.load_failures()


def test_failure_record_fields(fake_run_state: Path) -> None:
    grouped = failures.load_failures()
    rec = grouped["ops-transformer"]["BUILD_FAIL"][0]
    assert rec.repo == "ops-transformer"
    assert rec.op == "grouped_matmul"
    assert rec.failure_type == "BUILD_FAIL"
    assert rec.phase == "phase1"
    assert rec.duration_s == 920.5
    assert rec.log_path.endswith("grouped_matmul.phase1.build.log")
    assert rec.attempts == 1
