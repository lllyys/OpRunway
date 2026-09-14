"""Tests for apply_plan: 5 kinds × normal/error paths."""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import apply_plan


# ── env ──────────────────────────────────────────────────────────────────────

def test_env_parses_key_value(tmp_cwd: Path) -> None:
    sol = {"kind": "env", "suggested_fix": "ASCEND_GLOBAL_LOG_LEVEL=1"}
    plan = apply_plan.build_plan(solution=sol, context={})
    assert plan["kind"] == "env"
    assert plan["payload"] == {"ASCEND_GLOBAL_LOG_LEVEL": "1"}
    assert "--env-extra=ASCEND_GLOBAL_LOG_LEVEL=1" in plan["ops_test_args"]
    assert plan["requires_user_action"] is False


def test_env_bad_format_raises(tmp_cwd: Path) -> None:
    sol = {"kind": "env", "suggested_fix": "not-valid-format"}
    with pytest.raises(ValueError, match="Cannot parse env"):
        apply_plan.build_plan(solution=sol, context={})


# ── build_flag ───────────────────────────────────────────────────────────────

def test_build_flag_passthrough(tmp_cwd: Path) -> None:
    sol = {"kind": "build_flag", "suggested_fix": "-DENABLE_HIF8=ON"}
    plan = apply_plan.build_plan(solution=sol, context={})
    assert plan["kind"] == "build_flag"
    assert plan["payload"]["flags"] == ["-DENABLE_HIF8=ON"]
    assert "--build-extra-args=-DENABLE_HIF8=ON" in plan["ops_test_args"]
    assert plan["requires_user_action"] is False


# ── cmd_arg ──────────────────────────────────────────────────────────────────

def test_cmd_arg_strips_buildsh_prefix(tmp_cwd: Path) -> None:
    sol = {"kind": "cmd_arg", "suggested_fix": "build.sh --pkg --soc=ascend950"}
    plan = apply_plan.build_plan(solution=sol, context={})
    assert plan["kind"] == "cmd_arg"
    assert "build.sh" not in plan["payload"]["run_args"]
    assert "--pkg --soc=ascend950" in plan["payload"]["run_args"]


def test_cmd_arg_without_prefix(tmp_cwd: Path) -> None:
    sol = {"kind": "cmd_arg", "suggested_fix": "--run_example foo eager cust"}
    plan = apply_plan.build_plan(solution=sol, context={})
    assert "--run_example foo eager cust" in plan["payload"]["run_args"]


# ── upgrade ──────────────────────────────────────────────────────────────────

def test_upgrade_requires_user_action(tmp_cwd: Path) -> None:
    sol = {"kind": "upgrade", "suggested_fix": "please use v2.1.0"}
    plan = apply_plan.build_plan(solution=sol, context={})
    assert plan["kind"] == "upgrade"
    assert plan["requires_user_action"] is True
    assert plan["ops_test_args"] == []
    assert "please use v2.1.0" in plan["payload"]["hint"]


# ── patch ────────────────────────────────────────────────────────────────────

def test_patch_creates_branch_and_diff(tmp_cwd: Path, fake_repo: Path) -> None:
    diff_content = "--- a/foo.cpp\n+++ b/foo.cpp\n@@ -1 +1 @@\n-old\n+new\n"
    sol = {"kind": "patch", "suggested_fix": diff_content}
    ctx = {
        "repo": "ops-transformer",
        "repo_path": str(fake_repo),
        "issue_id": "101",
    }
    plan = apply_plan.build_plan(solution=sol, context=ctx)
    assert plan["kind"] == "patch"
    assert plan["requires_user_action"] is False
    # Branch was created
    import subprocess
    r = subprocess.run(
        ["git", "-C", str(fake_repo), "branch", "--list", "track-issue-101"],
        capture_output=True, text=True,
    )
    assert "track-issue-101" in r.stdout
    # Diff file was written
    diff_path = Path(plan["payload"]["diff_path"])
    assert diff_path.exists()
    assert diff_path.read_text() == diff_content


def test_patch_duplicate_branch_gets_retry_suffix(tmp_cwd: Path, fake_repo: Path) -> None:
    import subprocess
    subprocess.run(
        ["git", "-C", str(fake_repo), "switch", "-c", "track-issue-202"],
        check=True, capture_output=True,
    )
    # Switch back to original branch so the create-branch in _patch can run
    subprocess.run(
        ["git", "-C", str(fake_repo), "switch", "-"],
        check=True, capture_output=True,
    )
    sol = {"kind": "patch", "suggested_fix": "some diff"}
    ctx = {"repo": "ops-transformer", "repo_path": str(fake_repo), "issue_id": "202"}
    plan = apply_plan.build_plan(solution=sol, context=ctx)
    assert plan["payload"]["branch_name"] == "track-issue-202-retry-1"


def test_patch_non_git_repo_raises(tmp_cwd: Path, tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    sol = {"kind": "patch", "suggested_fix": "diff"}
    ctx = {"repo": "ops-transformer", "repo_path": str(not_a_repo), "issue_id": "99"}
    with pytest.raises(RuntimeError, match="not a git repo"):
        apply_plan.build_plan(solution=sol, context=ctx)


# ── clean ────────────────────────────────────────────────────────────────────

def test_clean_splits_multistatement_shell(tmp_cwd: Path) -> None:
    sol = {
        "kind": "clean",
        "suggested_fix": "pkill -f bisheng; rm -rf scripts/kernel/binary_script/kernel_meta_*",
    }
    ctx = {"repo": "ops-nn", "repo_path": "/tmp/fake-repo", "issue_id": "2749"}
    plan = apply_plan.build_plan(solution=sol, context=ctx)
    assert plan["kind"] == "clean"
    assert plan["payload"]["commands"] == [
        "pkill -f bisheng",
        "rm -rf scripts/kernel/binary_script/kernel_meta_*",
    ]
    assert plan["payload"]["cwd"] == "/tmp/fake-repo"
    assert plan["pre_cleanup_commands"] == plan["payload"]["commands"]
    assert plan["ops_test_args"] == []
    assert plan["requires_user_action"] is False


def test_clean_requires_repo_path(tmp_cwd: Path) -> None:
    sol = {"kind": "clean", "suggested_fix": "rm -rf build"}
    with pytest.raises(ValueError, match="requires context.repo_path"):
        apply_plan.build_plan(solution=sol, context={})


def test_clean_rejects_destructive_rm(tmp_cwd: Path) -> None:
    sol = {"kind": "clean", "suggested_fix": "rm -rf /"}
    ctx = {"repo_path": "/tmp/x"}
    with pytest.raises(ValueError, match="refusing destructive"):
        apply_plan.build_plan(solution=sol, context=ctx)


def test_clean_handles_newline_separator(tmp_cwd: Path) -> None:
    sol = {
        "kind": "clean",
        "suggested_fix": "make clean\nrm -rf build_out",
    }
    ctx = {"repo_path": "/tmp/x"}
    plan = apply_plan.build_plan(solution=sol, context=ctx)
    assert plan["payload"]["commands"] == ["make clean", "rm -rf build_out"]


# ── pre_cleanup_commands default ─────────────────────────────────────────────

def test_non_clean_plans_have_empty_pre_cleanup(tmp_cwd: Path) -> None:
    sol = {"kind": "env", "suggested_fix": "FOO=1"}
    plan = apply_plan.build_plan(solution=sol, context={})
    assert plan["pre_cleanup_commands"] == []


# ── unknown kind ─────────────────────────────────────────────────────────────

def test_unknown_kind_raises(tmp_cwd: Path) -> None:
    sol = {"kind": "magic", "suggested_fix": "do something"}
    with pytest.raises(ValueError, match="Unknown solution kind"):
        apply_plan.build_plan(solution=sol, context={})
