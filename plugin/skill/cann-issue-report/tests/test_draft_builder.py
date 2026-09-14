from pathlib import Path

import pytest

from scripts import draft_builder
from scripts.failures import FailureRecord


@pytest.fixture
def sample_failures() -> dict[str, dict[str, list[FailureRecord]]]:
    return {
        "ops-transformer": {
            "BUILD_FAIL": [
                FailureRecord(repo="ops-transformer", op="grouped_matmul",
                              failure_type="BUILD_FAIL", phase="phase1",
                              duration_s=920.5,
                              log_path="cann-ops-report/ops-transformer/test/logs/grouped_matmul.phase1.build.log",
                              attempts=1),
            ],
            "RUN_EXIT_FAIL": [
                FailureRecord(repo="ops-transformer", op="moe",
                              failure_type="RUN_EXIT_FAIL", phase="phase1",
                              duration_s=12.3,
                              log_path="cann-ops-report/ops-transformer/test/logs/moe.phase1.run.log",
                              attempts=2),
            ],
        },
    }


@pytest.fixture
def sample_env() -> dict[str, str]:
    return {
        "soc": "ascend950",
        "cann_version": "8.0.RC1",
        "git_rev": "abc1234567",
        "python_version": "Python 3.10.0",
        "os": "Linux 6.6 x86_64",
    }


def test_build_per_op_writes_one_file(tmp_cwd: Path, fake_logs: Path,
                                       sample_failures, sample_env) -> None:
    written = draft_builder.build_per_op("ops-transformer",
                                          sample_failures["ops-transformer"],
                                          env=sample_env,
                                          repo_path=tmp_cwd)
    assert len(written) == 2
    per_op_dir = tmp_cwd / "cann-ops-report" / "issues" / "drafts" / "ops-transformer" / "per_op"
    files = sorted(per_op_dir.iterdir())
    assert files[0].name == "grouped_matmul__BUILD_FAIL.md"
    assert files[1].name == "moe__RUN_EXIT_FAIL.md"


def test_per_op_content_has_required_sections(tmp_cwd: Path, fake_logs: Path,
                                                sample_failures, sample_env) -> None:
    draft_builder.build_per_op("ops-transformer",
                                sample_failures["ops-transformer"],
                                env=sample_env, repo_path=tmp_cwd)
    f = (tmp_cwd / "cann-ops-report" / "issues" / "drafts" / "ops-transformer"
         / "per_op" / "grouped_matmul__BUILD_FAIL.md").read_text(encoding="utf-8")
    assert "## 环境" in f
    assert "ascend950" in f
    assert "8.0.RC1" in f
    assert "## 失败算子" in f
    assert "grouped_matmul" in f
    assert "BUILD_FAIL" in f
    assert "## 复现命令" in f
    assert "## 错误日志摘录" in f
    assert "undefined reference" in f  # from fake_logs
    assert "## 建议 labels" in f


def test_build_by_type_aggregates(tmp_cwd: Path, fake_logs: Path,
                                    sample_failures, sample_env) -> None:
    draft_builder.build_by_type("ops-transformer",
                                  sample_failures["ops-transformer"],
                                  env=sample_env, repo_path=tmp_cwd)
    by_type_dir = tmp_cwd / "cann-ops-report" / "issues" / "drafts" / "ops-transformer" / "by_type"
    build_fail = (by_type_dir / "BUILD_FAIL.md").read_text(encoding="utf-8")
    assert "grouped_matmul" in build_fail
    assert "## 失败算子" in build_fail


def test_build_whole_repo_one_file(tmp_cwd: Path, fake_logs: Path,
                                     sample_failures, sample_env) -> None:
    written = draft_builder.build_whole_repo("ops-transformer",
                                              sample_failures["ops-transformer"],
                                              env=sample_env, repo_path=tmp_cwd)
    assert len(written) == 1
    whole = (tmp_cwd / "cann-ops-report" / "issues" / "drafts" / "ops-transformer"
              / "whole_repo.md").read_text(encoding="utf-8")
    # both failure types appear, grouped
    assert "BUILD_FAIL" in whole
    assert "RUN_EXIT_FAIL" in whole
    assert "grouped_matmul" in whole
    assert "moe" in whole
