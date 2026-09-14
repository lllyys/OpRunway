"""Tests for context_discovery: SOC auto-discovery priority chain."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import context_discovery


# ── SOC discovery priority chain ─────────────────────────────────────────────

def test_soc_from_state_record(tmp_cwd: Path) -> None:
    state = {
        "ops-nn::qbmm::BUILD_FAIL": {
            "issue_url": "https://x/issues/1",
            "soc": "ascend950",
        }
    }
    p = tmp_cwd / "cann-ops-report" / "issues" / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state), encoding="utf-8")

    soc = context_discovery.discover_soc(
        repo="ops-nn", op="qbmm", failure_type="BUILD_FAIL",
    )
    assert soc == "ascend950"


def test_soc_from_issue_body_table(tmp_cwd: Path) -> None:
    body = "## 环境信息\n\n| 项目 | 版本 |\n|---|---|\n| SOC | ascend950 |\n"
    soc = context_discovery.discover_soc(
        repo="ops-nn", op="qbmm", failure_type="BUILD_FAIL",
        issue_body=body,
    )
    assert soc == "ascend950"


def test_soc_from_issue_body_flag(tmp_cwd: Path) -> None:
    body = "复现命令：bash build.sh --pkg --soc=ascend910b --ops=foo"
    soc = context_discovery.discover_soc(
        repo="ops-nn", op="qbmm", failure_type="BUILD_FAIL",
        issue_body=body,
    )
    assert soc == "ascend910b"


def test_soc_from_issue_body_bare_mention(tmp_cwd: Path) -> None:
    body = "this only happens on Ascend950PR_9599 boards"
    soc = context_discovery.discover_soc(
        repo="ops-nn", op="qbmm", failure_type="BUILD_FAIL",
        issue_body=body,
    )
    assert soc and soc.lower().startswith("ascend9")


def test_soc_state_takes_priority_over_body(tmp_cwd: Path) -> None:
    state = {
        "ops-nn::qbmm::BUILD_FAIL": {"issue_url": "x", "soc": "ascend950"}
    }
    p = tmp_cwd / "cann-ops-report" / "issues" / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state), encoding="utf-8")

    body = "| SOC | ascend910b |"
    soc = context_discovery.discover_soc(
        repo="ops-nn", op="qbmm", failure_type="BUILD_FAIL",
        issue_body=body,
    )
    assert soc == "ascend950"  # state wins


def test_soc_returns_none_when_no_source(tmp_cwd: Path) -> None:
    soc = context_discovery.discover_soc(
        repo="ops-nn", op="qbmm", failure_type="BUILD_FAIL",
    )
    assert soc is None


def test_soc_from_run_state_when_state_record_absent(tmp_cwd: Path) -> None:
    run_state = {"ops": {"qbmm": {"soc": "ascend950", "phase1": {"status": "PASS"}}}}
    p = tmp_cwd / "cann-ops-report" / "ops-nn" / "test" / "run_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(run_state), encoding="utf-8")

    soc = context_discovery.discover_soc(
        repo="ops-nn", op="qbmm", failure_type="BUILD_FAIL",
    )
    assert soc == "ascend950"


# ── repo_path discovery ──────────────────────────────────────────────────────

def test_repo_path_returns_none_when_state_absent(tmp_cwd: Path) -> None:
    assert context_discovery.discover_repo_path("ops-nn") is None


def test_repo_path_from_run_state(tmp_cwd: Path) -> None:
    run_state = {"repo_path": "/home/user/cann/ops-nn", "ops": {}}
    p = tmp_cwd / "cann-ops-report" / "ops-nn" / "test" / "run_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(run_state), encoding="utf-8")
    assert context_discovery.discover_repo_path("ops-nn") == "/home/user/cann/ops-nn"
