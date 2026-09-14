"""Translate a chosen solution + failure context into an executable plan.

The agent (LLM) is responsible for reading upstream comments and constructing
the `solution` dict — this module is a thin executor, not a classifier.

Output dict:
    {
        "kind": "env" | "build_flag" | "cmd_arg" | "patch" | "upgrade" | "clean",
        "payload": dict,
        "ops_test_args": list[str],        # extra args passed to run_phase1_batched.py
        "pre_cleanup_commands": list[str], # shell commands to run before retest (clean kind)
        "requires_user_action": bool,      # True for upgrade (needs manual git pull)
    }
"""
from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

try:
    from . import paths
except ImportError:
    import paths


_SUPPORTED_KINDS = ("env", "build_flag", "cmd_arg", "upgrade", "patch", "clean")


def build_plan(*, solution: dict, context: dict) -> dict:
    kind = solution["kind"]
    dispatch = {
        "env": _env,
        "build_flag": _build_flag,
        "cmd_arg": _cmd_arg,
        "upgrade": _upgrade,
        "patch": _patch,
        "clean": _clean,
    }
    if kind not in dispatch:
        raise ValueError(
            f"Unknown solution kind: {kind!r}. Supported: {_SUPPORTED_KINDS}"
        )
    plan = dispatch[kind](solution, context)
    # Ensure every plan has the new pre_cleanup_commands key (default empty)
    plan.setdefault("pre_cleanup_commands", [])
    return plan


def _env(sol: dict, ctx: dict) -> dict:
    s = sol["suggested_fix"].strip()
    m = re.match(r"([A-Z][A-Z0-9_]*)=(.+)", s)
    if not m:
        raise ValueError(f"Cannot parse env suggestion: {s!r}")
    key, val = m.group(1), m.group(2).strip()
    return {
        "kind": "env",
        "payload": {key: val},
        "ops_test_args": [f"--env-extra={key}={val}"],
        "requires_user_action": False,
    }


def _build_flag(sol: dict, ctx: dict) -> dict:
    flag = sol["suggested_fix"].strip()
    return {
        "kind": "build_flag",
        "payload": {"flags": [flag]},
        "ops_test_args": [f"--build-extra-args={flag}"],
        "requires_user_action": False,
    }


def _cmd_arg(sol: dict, ctx: dict) -> dict:
    cmd_tail = sol["suggested_fix"].strip()
    if cmd_tail.startswith("build.sh"):
        cmd_tail = cmd_tail[len("build.sh"):].strip()
    return {
        "kind": "cmd_arg",
        "payload": {"run_args": cmd_tail},
        "ops_test_args": [f"--run-extra-args={cmd_tail}"],
        "requires_user_action": False,
    }


def _upgrade(sol: dict, ctx: dict) -> dict:
    return {
        "kind": "upgrade",
        "payload": {"hint": sol.get("suggested_fix", "")},
        "ops_test_args": [],
        "requires_user_action": True,
    }


def _patch(sol: dict, ctx: dict) -> dict:
    repo_path = Path(ctx["repo_path"])
    if not (repo_path / ".git").exists():
        raise RuntimeError(f"{repo_path} is not a git repo")

    issue_id = ctx["issue_id"]
    branch_name = _unique_branch(repo_path, f"track-issue-{issue_id}")
    subprocess.run(
        ["git", "-C", str(repo_path), "switch", "-c", branch_name],
        check=True, capture_output=True,
    )

    patches_dir = Path(paths.PATCHES_DIR) / ctx["repo"]
    patches_dir.mkdir(parents=True, exist_ok=True)
    diff_path = patches_dir / f"{issue_id}.diff"
    diff_path.write_text(sol["suggested_fix"], encoding="utf-8")

    return {
        "kind": "patch",
        "payload": {"diff_path": str(diff_path), "branch_name": branch_name},
        "ops_test_args": [],
        "requires_user_action": False,
    }


_DESTRUCTIVE_ROOTS = ("/", "/home", "/root", "/usr", "/etc", "/var", "/opt")


def _clean(sol: dict, ctx: dict) -> dict:
    """Pre-retest shell cleanup (e.g. pkill bisheng; rm -rf kernel_meta_*).

    The agent supplies `suggested_fix` as a multi-statement shell string. We
    split on `;` and `\\n`, refuse any command that would touch a destructive
    root path, and return them as `pre_cleanup_commands` for retest_orchestrator
    to execute in `repo_path` before the build.
    """
    raw = sol["suggested_fix"].strip()
    commands = [c.strip() for c in re.split(r"[;\n]+", raw) if c.strip()]
    for cmd in commands:
        _reject_destructive(cmd)
    repo_path = ctx.get("repo_path", "")
    if not repo_path:
        raise ValueError("clean kind requires context.repo_path")
    return {
        "kind": "clean",
        "payload": {"commands": commands, "cwd": repo_path},
        "ops_test_args": [],
        "pre_cleanup_commands": commands,
        "requires_user_action": False,
    }


def _reject_destructive(cmd: str) -> None:
    """Reject rm/find -delete against absolute roots that would nuke the system."""
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        # Unparseable shell (e.g. unbalanced quotes) — let it through; retest
        # orchestrator runs via shell=True and the user already confirmed.
        return
    if not tokens:
        return
    head = tokens[0]
    if head not in {"rm", "find"}:
        return
    for tok in tokens[1:]:
        if tok in _DESTRUCTIVE_ROOTS:
            raise ValueError(
                f"refusing destructive clean command (targets system root): {cmd!r}"
            )


def _unique_branch(repo_path: Path, base: str) -> str:
    def _exists(name: str) -> bool:
        r = subprocess.run(
            ["git", "-C", str(repo_path), "branch", "--list", name],
            capture_output=True, text=True,
        )
        return name in r.stdout

    if not _exists(base):
        return base
    n = 1
    while True:
        candidate = f"{base}-retry-{n}"
        if not _exists(candidate):
            return candidate
        n += 1
