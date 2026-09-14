"""Shared pytest fixtures for cann-issue-track tests.

Conventions:
- tmp_cwd: chdir to tmp_path so cann-ops-report/ lands in tmp
- fake_submitted_state: a 5-issue state.json across GitHub/Gitee/GitCode
- fake_failed_run_state: a synthetic run_state.json with one BUILD_FAIL op
- fake_build_log: a log file matching the BUILD_FAIL op's log_path
- fake_repo: tmp git repo with origin remote
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Iterator

import pytest


@pytest.fixture
def tmp_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture
def fake_submitted_state(tmp_cwd: Path) -> Path:
    state = {
        "ops-transformer::grouped_matmul::BUILD_FAIL": {
            "issue_url": "https://github.com/ascend/ops-transformer/issues/101",
            "submitted_at": "2026-05-20T10:00:00",
            "phase": "phase1",
            "submitted_via": "api",
        },
        "ops-cv::resize_bilinear::BUILD_FAIL": {
            "issue_url": "https://gitee.com/ascend/ops-cv/issues/I7XYZ",
            "submitted_at": "2026-05-20T10:30:00",
            "phase": "phase1",
            "submitted_via": "api",
        },
        "ops-math::concat::RUN_EXIT_FAIL": {
            "issue_url": "https://gitcode.com/ascend/ops-math/issues/42",
            "submitted_at": "2026-05-20T11:00:00",
            "phase": "phase1",
            "submitted_via": "manual",
        },
    }
    p = tmp_cwd / "cann-ops-report" / "issues" / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return p


@pytest.fixture
def fake_failed_run_state(tmp_cwd: Path) -> Path:
    state = {
        "created_at": "2026-05-20T10:00:00",
        "updated_at": "2026-05-20T10:30:00",
        "repos": {
            "ops-transformer": {
                "ops": {
                    "grouped_matmul": {
                        "phase1": {
                            "status": "BUILD_FAIL",
                            "attempts": 1,
                            "duration_s": 920.5,
                            "log_path": "cann-ops-report/test/logs/ops-transformer/grouped_matmul.phase1.build.log",
                        },
                    },
                }
            }
        },
    }
    p = tmp_cwd / "cann-ops-report" / "test" / "run_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return p


@pytest.fixture
def fake_build_log(tmp_cwd: Path) -> Path:
    p = tmp_cwd / "cann-ops-report" / "test" / "logs" / "ops-transformer" / "grouped_matmul.phase1.build.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "2026-05-20T10:01:30 INFO: configuring\n"
        "2026-05-20T10:05:12 ERROR: /home/user/cann/ops-transformer/op_kernel/foo.cpp:42:18: undefined reference to AscendC::HiFloat8Cast\n"
        "linker failed with exit=1\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "ops-transformer"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("test\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin",
                    "https://github.com/ascend/ops-transformer.git"],
                   cwd=repo, check=True)
    return repo
