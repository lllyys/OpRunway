"""Shared pytest fixtures for cann-issue-report tests.

Conventions:
- tmp_cwd: temp dir set as os.getcwd() for the test (so cann-ops-report/ lands in tmp)
- fake_run_state: synthetic run_state.json with PASS + several failure types
- fake_logs: synthetic phase logs the tests can grep against
- fake_repo: tmp git repo with origin remote (for repo_resolver)
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Iterator

import pytest


@pytest.fixture
def tmp_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture
def fake_run_state(tmp_cwd: Path) -> Path:
    """Write per-repo run_state.json files (cann-ops-report/<repo>/test/)."""
    per_repo = {
        "ops-transformer": {
            "created_at": "2026-05-18T10:00:00",
            "updated_at": "2026-05-18T10:30:00",
            "ops": {
                "grouped_matmul": {
                    "phase1": {"status": "BUILD_FAIL", "attempts": 1,
                               "duration_s": 920.5,
                               "log_path": "cann-ops-report/ops-transformer/test/logs/grouped_matmul.phase1.build.log"},
                },
                "flash_attention": {
                    "phase1": {"status": "PASS", "attempts": 1, "duration_s": 130.0},
                },
                "moe": {
                    "phase1": {"status": "RUN_EXIT_FAIL", "attempts": 2,
                               "duration_s": 12.3,
                               "log_path": "cann-ops-report/ops-transformer/test/logs/moe.phase1.run.log"},
                },
                "topk": {
                    "phase1": {"status": "TIMEOUT", "attempts": 1, "duration_s": 1800.0},
                },
                "pending_op": {
                    "phase1": {"status": "PENDING", "attempts": 0},
                },
            },
        },
        "ops-cv": {
            "created_at": "2026-05-18T10:00:00",
            "updated_at": "2026-05-18T10:30:00",
            "ops": {
                "resize_bilinear": {
                    "phase1": {"status": "BUILD_FAIL", "attempts": 1, "duration_s": 88.0},
                },
            },
        },
    }
    first = None
    for repo, state in per_repo.items():
        f = tmp_cwd / "cann-ops-report" / repo / "test" / "run_state.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(state, indent=2), encoding="utf-8")
        first = first or f
    return first


@pytest.fixture
def fake_logs(tmp_cwd: Path) -> Path:
    """Write synthetic phase logs for grep-based extraction tests."""
    logs_dir = tmp_cwd / "cann-ops-report" / "ops-transformer" / "test" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "grouped_matmul.phase1.build.log").write_text(
        "starting build\n"
        "INFO: configuring\n"
        "ERROR: undefined reference to AscendC::HiFloat8Cast\n"
        "linker failed with exit=1\n"
        "build aborted\n",
        encoding="utf-8",
    )
    (logs_dir / "moe.phase1.run.log").write_text(
        "loading kernel\n"
        "ERROR: kernel launch failed\n"
        "exit=139 (SIGSEGV)\n",
        encoding="utf-8",
    )
    return logs_dir


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """Make a tmp git repo with an origin remote pointing to github."""
    repo = tmp_path / "fake-ops-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin",
                    "https://github.com/ascend/ops-transformer.git"],
                   cwd=repo, check=True)
    return repo
