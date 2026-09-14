"""Run cann-ops-run runner with an apply_plan result injected as extra args.

Returns:
    {"status":  "PASS" | "FAIL" | "ERROR",              # 总判（历史字段，语义不变）
     "verdict": "PASS" | "PARTIAL" | "FAIL"
                | "UNCERTAIN" | "ERROR",                 # 三分支判定，见 _verdict_of()
     "op_status": str,   # run_state.json 里该算子的权威 phase1 状态（兜底时为 ""）
     "reason":  str,     # 一句话说明 verdict 怎么来的（给 agent 和用户看）
     "detail":  str, "log_path": str}

`verdict` 由本模块**确定性算出**，agent 不必再去读 run_state / 报告 JSON 自己判。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

try:
    from . import paths
except ImportError:
    import paths  # type: ignore[no-redef]

# Path to run_phase1_batched.py (sibling skill, resolved at call time)
_RUNNER = Path(__file__).resolve().parent.parent.parent.parent / "cann-ops-run" / "scripts" / "run_phase1_batched.py"


def _read_op_phase1(repo: str, op: str) -> dict | None:
    """Return the op's phase1 record from run_state.json, or None if absent."""
    state_path = paths.repo_state_file(repo)
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data["ops"][op]["phase1"]
    except (KeyError, json.JSONDecodeError, TypeError):
        return None


# 原 issue 报的是 build/install 期失败——这两类修好后若 run 期新炸，属「原问题已修复 +
# 全新失败面」，即 partial-PASS。原 issue 就报 run 期失败的，run 再失败只是没修好，不算 partial。
_PREBUILD_FAILURES = {"BUILD_FAIL", "INSTALL_FAIL"}
# run 期失败面（build/install 均已通过才可能出现）
_RUN_FAILURES = {"RUN_EXIT_FAIL", "RUN_PATTERN_FAIL", "TIMEOUT"}
# 「没真正跑起来」——不能据此下 PASS/FAIL 结论，交 agent 复核
_INCONCLUSIVE = {"UNCERTAIN", "SKIPPED_NO_RUN_ARTIFACT", "SKIPPED_NO_ARTIFACT", "SKIPPED_USER"}


def _verdict_of(op_status: str, original_failure_type: str) -> tuple[str, str]:
    """(op_status, 原失败类型) → (verdict, reason)。纯函数,便于单测。

    PASS      —— 算子跑通
    PARTIAL   —— 原 build/install 失败已恢复,但 run 期出现全新失败面(走 follow-up issue 流程)
    UNCERTAIN —— 没真正跑起来 / 无强信号,不能下结论,需 agent 复核
    FAIL      —— 其余(含原问题依旧、原本就是 run 期失败且仍失败)
    """
    if op_status == "PASS":
        return "PASS", "run_state 权威状态为 PASS"
    if op_status in _INCONCLUSIVE:
        return "UNCERTAIN", f"算子状态 {op_status}：没有真正跑起来或无强判定信号，需人工复核"
    if op_status in _RUN_FAILURES and original_failure_type in _PREBUILD_FAILURES:
        return "PARTIAL", (
            f"原始失败 {original_failure_type} 已恢复（build/install 通过），"
            f"但 run 期出现新失败 {op_status}")
    if op_status:
        return "FAIL", f"算子状态 {op_status}"
    return "FAIL", "未取到算子状态"


def retest(
    *,
    plan: dict,
    context: dict,
    python: str = sys.executable,
) -> dict:
    """
    context keys: repo, op, repo_path, soc, failure_type(可选,原 issue 报的失败类型——
                  缺省时永不判 PARTIAL,保守退化为 PASS/FAIL)
    plan keys:    ops_test_args (list[str]), kind
    """
    repo = context["repo"]
    op = context["op"]
    repo_path = context.get("repo_path", "")
    soc = context.get("soc", "ascend950")
    original_failure_type = context.get("failure_type", "")

    def _err(detail: str) -> dict:
        return {"status": "ERROR", "verdict": "ERROR", "op_status": "",
                "reason": detail, "detail": detail, "log_path": ""}

    if not repo_path:
        return _err("context.repo_path is required for retest")

    # Pre-cleanup (clean kind): run shell commands in repo_path before retest.
    cleanup = plan.get("pre_cleanup_commands", []) or []
    for cmd in cleanup:
        try:
            cp = subprocess.run(
                cmd, shell=True, cwd=repo_path,
                capture_output=True, text=True, timeout=300,
            )
        except subprocess.TimeoutExpired:
            return _err(f"pre-cleanup command timed out: {cmd}")
        if cp.returncode != 0:
            return _err(f"pre-cleanup failed (rc={cp.returncode}): {cmd}\n{cp.stderr[-500:]}")

    # Snapshot phase1.attempts before the run. update_op() bumps it on every
    # write, so a higher counter afterwards proves THIS retest wrote state —
    # guarding against trusting a STALE status (e.g. an old PASS) if the runner
    # crashes before writing, which would otherwise produce a false PASS.
    before_attempts = (_read_op_phase1(repo, op) or {}).get("attempts", 0)

    base_args = [
        python, str(_RUNNER),
        f"--repo-mapping={repo}={repo_path}",
        f"--soc={soc}",
        f"--ops={op}",
    ]
    base_args.extend(plan.get("ops_test_args", []))

    try:
        result = subprocess.run(
            base_args,
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except subprocess.TimeoutExpired:
        return _err("retest timed out after 3600s")
    except Exception as exc:
        return _err(str(exc))

    combined = result.stdout + result.stderr

    # Primary: authoritative status from run_state.json — but ONLY if this retest
    # actually wrote it (attempts bumped). A stale record (runner crashed before
    # updating) must not be trusted; fall back to the exit code instead.
    after = _read_op_phase1(repo, op)
    if after is not None and after.get("attempts", 0) > before_attempts:
        op_status = str(after.get("status") or "")
        verdict, reason = _verdict_of(op_status, original_failure_type)
        status = "PASS" if op_status == "PASS" else "FAIL"
    else:
        # 状态没被本轮刷新（runner 崩在写状态之前）→ 只能按退出码兜底，且绝不判 PARTIAL
        op_status = ""
        status = "PASS" if result.returncode == 0 else "FAIL"
        verdict = status
        reason = f"run_state 未被本轮刷新，按 runner 退出码 {result.returncode} 兜底判定"

    # per-op run log lives under cann-ops-report/<repo>/test/logs/ (per-repo layout)
    run_log = paths.repo_logs_dir(repo) / f"{op}.phase1.run.log"
    return {
        "status": status,
        "verdict": verdict,
        "op_status": op_status,
        "reason": reason,
        "detail": combined[-4000:],  # last 4k chars for context
        "log_path": str(run_log) if run_log.exists() else "",
    }
