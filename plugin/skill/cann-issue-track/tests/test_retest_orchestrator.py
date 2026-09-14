"""Tests for retest_orchestrator.

The orchestrator shells out to run_phase1_batched.py, so we mock subprocess.run
and — after the bug-fix — also verify that run_state.json is consulted.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import retest_orchestrator


BASE_CTX = {
    "repo": "ops-transformer",
    "op": "grouped_matmul",
    "repo_path": "/fake/repo",
    "soc": "ascend950",
}
BASE_PLAN = {"ops_test_args": [], "kind": "env"}


def _mock_proc(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def _write_state(tmp_cwd: Path, status: str, attempts: int = 1) -> Path:
    state = {"ops": {"grouped_matmul": {"phase1": {"status": status, "attempts": attempts}}}}
    p = tmp_cwd / "cann-ops-report" / "ops-transformer" / "test" / "run_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state), encoding="utf-8")
    return p


def _runner_writes(p: Path, proc: MagicMock):
    """side_effect simulating a real runner: bump phase1.attempts, then return proc."""
    def _se(*args, **kwargs):
        data = json.loads(p.read_text())
        ph = data["ops"]["grouped_matmul"]["phase1"]
        ph["attempts"] = ph.get("attempts", 0) + 1
        p.write_text(json.dumps(data))
        return proc
    return _se


# ── PASS path ────────────────────────────────────────────────────────────────

def test_pass_when_state_shows_pass(tmp_cwd: Path) -> None:
    """Runner writes PASS (attempts bumped) → result PASS."""
    p = _write_state(tmp_cwd, "PASS")
    with patch("subprocess.run", side_effect=_runner_writes(p, _mock_proc(0, stdout="ok"))):
        result = retest_orchestrator.retest(plan=BASE_PLAN, context=BASE_CTX)
    assert result["status"] == "PASS"


# ── FAIL path ────────────────────────────────────────────────────────────────

def test_fail_when_state_shows_build_fail(tmp_cwd: Path) -> None:
    p = _write_state(tmp_cwd, "BUILD_FAIL")
    with patch("subprocess.run", side_effect=_runner_writes(p, _mock_proc(1, stderr="CMake Error"))):
        result = retest_orchestrator.retest(plan=BASE_PLAN, context=BASE_CTX)
    assert result["status"] == "FAIL"


def test_fail_when_no_state_file_and_nonzero_rc(tmp_cwd: Path) -> None:
    """No state file + rc != 0 → FAIL (safe fallback)."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_proc(1, stderr="linker error")
        result = retest_orchestrator.retest(plan=BASE_PLAN, context=BASE_CTX)
    assert result["status"] == "FAIL"


# ── false-positive regressions ───────────────────────────────────────────────

def test_no_false_pass_from_stdout_keyword(tmp_cwd: Path) -> None:
    """PASS keyword in stdout must NOT override a FAIL freshly written to state."""
    p = _write_state(tmp_cwd, "RUN_EXIT_FAIL")
    with patch("subprocess.run",
               side_effect=_runner_writes(p, _mock_proc(0, stdout="All previous tests: PASS test_init"))):
        result = retest_orchestrator.retest(plan=BASE_PLAN, context=BASE_CTX)
    assert result["status"] == "FAIL"


def test_no_false_pass_from_stale_status(tmp_cwd: Path) -> None:
    """If the runner crashes before updating state, a STALE PASS must not be trusted."""
    _write_state(tmp_cwd, "PASS", attempts=5)  # old PASS from a previous run
    # runner crashes: rc != 0 and attempts is NOT bumped (state untouched)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_proc(1, stderr="bisheng crashed")
        result = retest_orchestrator.retest(plan=BASE_PLAN, context=BASE_CTX)
    assert result["status"] == "FAIL"


# ── ERROR paths ──────────────────────────────────────────────────────────────

def test_error_when_repo_path_missing(tmp_cwd: Path) -> None:
    ctx = {**BASE_CTX, "repo_path": ""}
    result = retest_orchestrator.retest(plan=BASE_PLAN, context=ctx)
    assert result["status"] == "ERROR"
    assert "repo_path" in result["detail"]


def test_error_on_timeout(tmp_cwd: Path) -> None:
    import subprocess
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[], timeout=3600)):
        result = retest_orchestrator.retest(plan=BASE_PLAN, context=BASE_CTX)
    assert result["status"] == "ERROR"
    assert "timed out" in result["detail"]


def test_error_on_generic_exception(tmp_cwd: Path) -> None:
    with patch("subprocess.run", side_effect=OSError("no such file")):
        result = retest_orchestrator.retest(plan=BASE_PLAN, context=BASE_CTX)
    assert result["status"] == "ERROR"
    assert "no such file" in result["detail"]


# ── extra ops_test_args are forwarded ────────────────────────────────────────

def test_extra_args_forwarded_to_runner(tmp_cwd: Path) -> None:
    plan = {"ops_test_args": ["--env-extra=FOO=1", "--build-extra-args=-DBAR=ON"], "kind": "env"}
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_proc(1)
        retest_orchestrator.retest(plan=plan, context=BASE_CTX)
    cmd = mock_run.call_args[0][0]
    assert "--env-extra=FOO=1" in cmd
    assert "--build-extra-args=-DBAR=ON" in cmd


# ── pre-cleanup integration ──────────────────────────────────────────────────

def test_pre_cleanup_commands_run_before_retest(tmp_cwd: Path) -> None:
    plan = {
        "kind": "clean",
        "ops_test_args": [],
        "pre_cleanup_commands": ["pkill -f bisheng", "rm -rf kernel_meta_*"],
    }
    calls = []

    def _capture(cmd, *args, **kwargs):
        calls.append((cmd, kwargs.get("shell", False), kwargs.get("cwd")))
        return _mock_proc(0, stdout="")

    with patch("subprocess.run", side_effect=_capture):
        retest_orchestrator.retest(plan=plan, context=BASE_CTX)

    # First two calls must be the cleanup commands (shell=True, cwd=repo_path),
    # last call must be the runner subprocess (list cmd, no shell).
    assert calls[0][0] == "pkill -f bisheng"
    assert calls[0][1] is True
    assert calls[0][2] == "/fake/repo"
    assert calls[1][0] == "rm -rf kernel_meta_*"
    assert isinstance(calls[-1][0], list)
    assert calls[-1][1] is False


def test_pre_cleanup_failure_returns_error(tmp_cwd: Path) -> None:
    plan = {
        "kind": "clean",
        "ops_test_args": [],
        "pre_cleanup_commands": ["bad-command"],
    }
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_proc(127, stderr="command not found")
        result = retest_orchestrator.retest(plan=plan, context=BASE_CTX)
    assert result["status"] == "ERROR"
    assert "pre-cleanup failed" in result["detail"]


def test_pre_cleanup_timeout_returns_error(tmp_cwd: Path) -> None:
    import subprocess
    plan = {
        "kind": "clean",
        "ops_test_args": [],
        "pre_cleanup_commands": ["sleep 9999"],
    }
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="x", timeout=300)):
        result = retest_orchestrator.retest(plan=plan, context=BASE_CTX)
    assert result["status"] == "ERROR"
    assert "pre-cleanup command timed out" in result["detail"]


# ── verdict：三分支判定下沉到脚本（agent 不再自己读 JSON 判） ──────────────────

@pytest.mark.parametrize("op_status,original,expected", [
    # 跑通
    ("PASS",             "BUILD_FAIL",    "PASS"),
    ("PASS",             "RUN_EXIT_FAIL", "PASS"),
    # 原 build/install 失败已恢复 + run 期新失败面 → partial
    ("RUN_EXIT_FAIL",    "BUILD_FAIL",    "PARTIAL"),
    ("RUN_PATTERN_FAIL", "INSTALL_FAIL",  "PARTIAL"),
    ("TIMEOUT",          "BUILD_FAIL",    "PARTIAL"),
    # 原本就是 run 期失败，run 再失败 = 没修好，不是 partial
    ("RUN_EXIT_FAIL",    "RUN_EXIT_FAIL", "FAIL"),
    ("RUN_PATTERN_FAIL", "TIMEOUT",       "FAIL"),
    # 原问题依旧
    ("BUILD_FAIL",       "BUILD_FAIL",    "FAIL"),
    ("INSTALL_FAIL",     "BUILD_FAIL",    "FAIL"),
    # 没真正跑起来 / 无强信号 → 不下结论
    ("UNCERTAIN",               "BUILD_FAIL", "UNCERTAIN"),
    ("SKIPPED_NO_RUN_ARTIFACT", "BUILD_FAIL", "UNCERTAIN"),
    ("SKIPPED_NO_ARTIFACT",     "BUILD_FAIL", "UNCERTAIN"),
    # 缺 failure_type 时保守退化，绝不误判 PARTIAL
    ("RUN_EXIT_FAIL",    "",              "FAIL"),
])
def test_verdict_of(op_status: str, original: str, expected: str) -> None:
    verdict, reason = retest_orchestrator._verdict_of(op_status, original)
    assert verdict == expected
    assert reason


def test_verdict_partial_end_to_end(tmp_cwd: Path) -> None:
    """原 BUILD_FAIL → 复测 run 期失败：status 仍是 FAIL，但 verdict 给出 PARTIAL。"""
    p = _write_state(tmp_cwd, "RUN_EXIT_FAIL")
    ctx = {**BASE_CTX, "failure_type": "BUILD_FAIL"}
    with patch("subprocess.run", side_effect=_runner_writes(p, _mock_proc(1))):
        result = retest_orchestrator.retest(plan=BASE_PLAN, context=ctx)
    assert result["status"] == "FAIL"          # 历史字段语义不变
    assert result["verdict"] == "PARTIAL"
    assert result["op_status"] == "RUN_EXIT_FAIL"
    assert "已恢复" in result["reason"]


def test_verdict_pass_end_to_end(tmp_cwd: Path) -> None:
    p = _write_state(tmp_cwd, "PASS")
    with patch("subprocess.run", side_effect=_runner_writes(p, _mock_proc(0))):
        result = retest_orchestrator.retest(
            plan=BASE_PLAN, context={**BASE_CTX, "failure_type": "BUILD_FAIL"})
    assert (result["status"], result["verdict"]) == ("PASS", "PASS")


def test_verdict_never_partial_on_stale_state(tmp_cwd: Path) -> None:
    """状态没被本轮刷新时只能按退出码兜底，绝不判 PARTIAL（否则会误关上游 issue）。"""
    _write_state(tmp_cwd, "RUN_EXIT_FAIL", attempts=5)
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _mock_proc(1, stderr="runner crashed")
        result = retest_orchestrator.retest(
            plan=BASE_PLAN, context={**BASE_CTX, "failure_type": "BUILD_FAIL"})
    assert result["verdict"] == "FAIL"
    assert result["op_status"] == ""
    assert "兜底" in result["reason"]


def test_error_result_carries_verdict(tmp_cwd: Path) -> None:
    result = retest_orchestrator.retest(plan=BASE_PLAN, context={**BASE_CTX, "repo_path": ""})
    assert result["verdict"] == "ERROR"
