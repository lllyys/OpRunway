#!/usr/bin/env python3
"""正式验收产物与未完成 attempt 的最小确定性边界。

本模块只回答「这份由既有确定性链算出的候选状态能否使用正式文件名」，不计算精度、
性能或证据完整性结论。既有 canonical state 映射也提升到这里成为唯一共享实现，
``run_workflow`` 只保留对象别名；renderer 不重抄任何状态关系。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import contextmanager

import fcntl
import artifact_path_guard


ATTEMPT_RECORD_FILE = "attempt_record.json"
FORMAL_ACCEPTANCE_FILE = "acceptance.json"
ATTEMPT_RECORD_SCHEMA = "oprunway.workflow_attempt_record"
ATTEMPT_RECORD_SCHEMA_VERSION = 2
PRE_EXECUTION_TERMINAL_FILE = "pre_execution_terminal.json"
PRE_EXECUTION_RESERVED_FILES = (
    PRE_EXECUTION_TERMINAL_FILE,
    "vendor_build_attempt.json",
    "前置执行失败明细.md",
)
ARTIFACT_LOCK_FILE = ".oprunway-artifacts.lock"

# 既有 run_workflow 人读 overall → canonical state 实现的唯一真源。公开常量供 workflow
# 保留兼容别名；正式发布门与 workflow 由此使用同一张关系表，而不是各维护一份白名单。
MEASURED_ONLY_OVERALL = "PASS(性能仅实测未裁决)"
MEASURED_ONLY_STATE = "PASSED_PRECISION_PERF_MEASURED_ONLY"
MEASURE_INCOMPLETE_OVERALL = "BLOCKED(measure_only 性能实测未完成)"
MEASURE_INCOMPLETE_STATE = "BLOCKED_PERF_MEASUREMENT_INCOMPLETE"
BLOCKED_WAIT_REAL_BASELINE_STATUS = "blocked_wait_real_baseline"
BLOCKED_WAIT_REAL_BASELINE_STATE = "BLOCKED_WAIT_REAL_BASELINE"

CANONICAL_STATE_BY_OVERALL = {
    "PASS": "PASSED",
    "PASS(无性能要求)": "PASSED",
    MEASURED_ONLY_OVERALL: MEASURED_ONLY_STATE,
    MEASURE_INCOMPLETE_OVERALL: MEASURE_INCOMPLETE_STATE,
    "FAIL(精度)": "FAILED_PRECISION",
    "NEEDS_REVIEW": "NEEDS_REVIEW",
    "PASSED_WITH_RISK": "PASSED_WITH_RISK",
    "PASSED_WITH_GAPS": "PASSED_WITH_GAPS",
    "BLOCKED_GOLDEN_UNAUTHORIZED": "BLOCKED_GOLDEN_UNAUTHORIZED",
    "BLOCKED_GOLDEN_UNAVAILABLE": "BLOCKED_GOLDEN_UNAVAILABLE",
    "BLOCKED_WAIT_GPU_BENCHMARK": "BLOCKED_WAIT_GPU_BENCHMARK",
    BLOCKED_WAIT_REAL_BASELINE_STATE: BLOCKED_WAIT_REAL_BASELINE_STATE,
    "BLOCKED_INCOMPARABLE_TIMING_SCOPE": "BLOCKED_INCOMPARABLE_TIMING_SCOPE",
    "BLOCKED_GPU_BASELINE_INVALID": "BLOCKED_GPU_BASELINE_INVALID",
}


class FormalAcceptanceError(RuntimeError):
    """候选状态不允许使用正式验收产物名。"""


class ArtifactNameConflictError(FormalAcceptanceError):
    """正式总结与 attempt 总结本应互斥，但目标目录已有另一种文件名。"""


def _assert_execution_identity(acceptance):
    identity = acceptance.get("execution_identity") if isinstance(acceptance, dict) else None
    binding = identity.get("source_binding") if isinstance(identity, dict) else None
    candidate = binding.get("candidate") if isinstance(binding, dict) else None
    public_op = identity.get("public_op") if isinstance(identity, dict) else None
    kernel_op = identity.get("kernel_op_type") if isinstance(identity, dict) else None
    if (public_op != acceptance.get("op")
            or not isinstance(kernel_op, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", kernel_op) is None
            or identity.get("resolution") not in {
                "derived_exact_source_candidate", "explicit_spec_binding"}
            or not isinstance(binding, dict)
            or binding.get("schema") != "oprunway.kernel_identity_spec_binding"
            or binding.get("schema_version") != 1
            or re.fullmatch(r"[0-9a-f]{64}", binding.get("identity_sha256") or "") is None
            or not isinstance(candidate, dict)
            or candidate.get("kernel_op_type") != kernel_op
            or not isinstance(candidate.get("source_path"), str)
            or re.fullmatch(r"[0-9a-f]{64}",
                            candidate.get("source_sha256") or "") is None
            or not isinstance(candidate.get("line"), int)
            or isinstance(candidate.get("line"), bool)
            or candidate.get("line", 0) < 1):
        raise FormalAcceptanceError(
            "正式 acceptance.execution_identity 缺失或未绑定 public op、source fact 与 kernel op type")


def _is_incomplete_label(value):
    """只识别状态机的未完成标签形态，不枚举可发布终态。"""
    return (not isinstance(value, str) or not value
            or value == "NEEDS_REVIEW" or value.startswith("BLOCKED"))


def canonical_state(overall, perf_summary):
    """既有人读 overall → canonical state 的唯一实现。

    ``perf_summary`` 沿用 workflow 的 summary object 接口；artifact renderer 的候选只有
    ``perf_status`` 时传 ``{"status": ...}``。未知 overall 保持既有 fail-closed 语义，
    落 ``NEEDS_REVIEW``，因此不能进入正式发布。
    """
    if isinstance(overall, str) and overall in CANONICAL_STATE_BY_OVERALL:
        return CANONICAL_STATE_BY_OVERALL[overall]
    status = perf_summary.get("status") if isinstance(perf_summary, dict) else None
    if status == "blocked_incomparable_timing_scope":
        return "BLOCKED_INCOMPARABLE_TIMING_SCOPE"
    if status == "blocked_gpu_baseline_invalid":
        return "BLOCKED_GPU_BASELINE_INVALID"
    if status == "blocked_wait_gpu_benchmark":
        return "BLOCKED_WAIT_GPU_BENCHMARK"
    if status == BLOCKED_WAIT_REAL_BASELINE_STATUS:
        return BLOCKED_WAIT_REAL_BASELINE_STATE
    if isinstance(overall, str) and overall.startswith("性能未达成"):
        return "FAILED_PERFORMANCE"
    if isinstance(overall, str) and overall.startswith("BLOCKED"):
        return "BLOCKED_EVIDENCE_INCOMPLETE"
    return "NEEDS_REVIEW"


def formal_acceptance_allowed(acceptance):
    """仅完整确定性门已过、且非阻塞/待复核状态时允许正式发布。

    合法的确定性 FAIL 与 ``PASSED_WITH_RISK/GAPS`` 都不是这里要改判的对象；只要它们
    已通过 gate 链，就仍是正式终态。布尔值用 ``is True``，拒绝 ``1`` 等宽松 JSON 形态。
    """
    if not isinstance(acceptance, dict):
        return False
    gate = acceptance.get("gate")
    if not isinstance(gate, dict) or gate.get("passed") is not True:
        return False
    # ``passed`` 与非空/坏形态 errors 同时出现只能说明输入自相矛盾。主链不会造出它，
    # 但 renderer 是公开入口，必须对手写/历史文件 fail-closed。
    errors = gate.get("errors")
    if not isinstance(errors, dict) or errors:
        return False
    state = acceptance.get("state")
    overall = acceptance.get("overall")
    expected_state = canonical_state(
        overall, {"status": acceptance.get("perf_status")})
    # overall 是正式报告直接展示的人读结论；它与 state 必须由同一 canonical helper
    # 得出精确关系。未知 overall 会映射 NEEDS_REVIEW，仍由负向门拒绝。
    return (state == expected_state
            and not _is_incomplete_label(state)
            and not _is_incomplete_label(overall))


def assert_formal_acceptance_allowed(acceptance):
    """拒绝把未过门、阻塞或待复核候选写成/渲染成正式验收产物。"""
    if formal_acceptance_allowed(acceptance):
        return
    gate = acceptance.get("gate") if isinstance(acceptance, dict) else None
    state = acceptance.get("state") if isinstance(acceptance, dict) else None
    overall = acceptance.get("overall") if isinstance(acceptance, dict) else None
    raise FormalAcceptanceError(
        "正式验收产物发布门未通过："
        f"gate.passed={gate.get('passed') if isinstance(gate, dict) else None!r}, "
        f"gate.errors={gate.get('errors') if isinstance(gate, dict) else None!r}, "
        f"state={state!r}, overall={overall!r}；"
        "本轮只能保留非正式 attempt 诊断工件。")


def _assert_opposite_summary_absent(out_dir, filename):
    """公共 writer 也维护两种总结名互斥；不暗中删除调用方已有工件。"""
    opposite = (ATTEMPT_RECORD_FILE
                if filename == FORMAL_ACCEPTANCE_FILE else FORMAL_ACCEPTANCE_FILE)
    opposite_path = os.path.join(out_dir, opposite)
    if os.path.lexists(opposite_path):
        raise ArtifactNameConflictError(
            f"总结工件名必须互斥：写 {filename!r} 前发现已有 {opposite!r}；"
            "请由 workflow 的统一失效步骤先处理上一轮总结。")


def assert_no_pre_execution_artifacts(out_dir):
    """marker 或任一 pre-execution payload 均代表终态/半提交，正式路径不得忽略。"""
    present = [name for name in PRE_EXECUTION_RESERVED_FILES
               if os.path.lexists(os.path.join(out_dir, name))]
    if present:
        raise ArtifactNameConflictError(
            "报告根存在 pre-execution terminal/incomplete 工件："
            + ", ".join(present)
            + "；只有一次新的完整 workflow 能在同一工件锁内显式失效后重跑")


def _atomic_write_json(out_dir, filename, payload):
    """在既有报告根内原子写一份 JSON；不创建或猜测报告根。"""
    fd, tmp = tempfile.mkstemp(prefix=f".{filename}.", suffix=".tmp", dir=out_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(payload, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        path = os.path.join(out_dir, filename)
        os.replace(tmp, path)
        dir_fd = os.open(out_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return path
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def artifact_transaction(out_dir):
    """序列化同一报告根的正式/attempt 终态切换。

    lock 文件只负责并发互斥，不承载状态；崩溃后的 durable 状态由正式总结或
    ``pre_execution_terminal.json`` 表达。调用方必须先创建报告根。
    """
    lock_path = os.path.join(out_dir, ARTIFACT_LOCK_FILE)
    with open(lock_path, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def publish_acceptance_json_locked(out_dir, acceptance):
    """调用方已持有报告根锁时发布正式 acceptance。"""
    assert_formal_acceptance_allowed(acceptance)
    _assert_execution_identity(acceptance)
    _assert_opposite_summary_absent(out_dir, FORMAL_ACCEPTANCE_FILE)
    assert_no_pre_execution_artifacts(out_dir)
    return _atomic_write_json(out_dir, FORMAL_ACCEPTANCE_FILE, acceptance)


def publish_acceptance_json(out_dir, acceptance):
    """正式 ``acceptance.json`` 的唯一写出原语。"""
    assert_formal_acceptance_allowed(acceptance)
    _assert_execution_identity(acceptance)
    try:
        guard = artifact_path_guard.prepare_existing_directory(out_dir)
    except artifact_path_guard.ArtifactPathError as ex:
        raise ArtifactNameConflictError(f"正式报告根不可信：{ex}") from ex
    with artifact_transaction(guard["path"]):
        artifact_path_guard.assert_stable(guard)
        return publish_acceptance_json_locked(guard["path"], acceptance)


def build_attempt_record(acceptance):
    """把不可正式发布的候选投影成无裁决语义的 attempt record。"""
    if formal_acceptance_allowed(acceptance):
        raise ValueError("可正式发布的确定性终态不得降名为 attempt_record.json")
    if not isinstance(acceptance, dict):
        raise TypeError("attempt 候选须为 JSON object")
    return {
        "schema": ATTEMPT_RECORD_SCHEMA,
        "schema_version": ATTEMPT_RECORD_SCHEMA_VERSION,
        "status": "not_publishable",
        "formal_eligible": False,
        "acceptance_verdict": None,
        "op": acceptance.get("op"),
        "execution_identity": acceptance.get("execution_identity"),
        "repo_mode": acceptance.get("repo_mode"),
        "pipeline_result": acceptance.get("overall"),
        "pipeline_state": acceptance.get("state"),
        "exit_code": acceptance.get("exit_code"),
        "requires_human_cp": acceptance.get("requires_human_cp", False),
        "gate": acceptance.get("gate"),
        "diagnostic_sources": acceptance.get("diagnostic_sources") or {
            "precision": "verdict.json",
            "performance": "perf_report.json",
            "evidence": "evidence.json",
        },
        "pre_execution_failure": acceptance.get("pre_execution_failure"),
        "note": (
            "本工件只记录未完成/阻塞的 workflow attempt；acceptance_verdict 恒为 null，"
            "不得命名、引用或渲染为正式验收裁决。"),
    }


def write_attempt_record(out_dir, acceptance):
    """原子写未完成 attempt；正式终态会被 ``build_attempt_record`` 拒绝。"""
    record = build_attempt_record(acceptance)
    try:
        guard = artifact_path_guard.prepare_existing_directory(out_dir)
    except artifact_path_guard.ArtifactPathError as ex:
        raise ArtifactNameConflictError(f"attempt 报告根不可信：{ex}") from ex
    with artifact_transaction(guard["path"]):
        artifact_path_guard.assert_stable(guard)
        _assert_opposite_summary_absent(guard["path"], ATTEMPT_RECORD_FILE)
        assert_no_pre_execution_artifacts(guard["path"])
        return _atomic_write_json(guard["path"], ATTEMPT_RECORD_FILE, record)
