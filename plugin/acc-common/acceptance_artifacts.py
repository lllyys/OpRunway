#!/usr/bin/env python3
"""正式验收产物与未完成 attempt 的最小确定性边界。

本模块只回答「这份由既有确定性链算出的候选状态能否使用正式文件名」，不计算精度、
性能或证据完整性结论。既有 canonical state 映射也提升到这里成为唯一共享实现，
``run_workflow`` 只保留对象别名；renderer 不重抄任何状态关系。
"""

from __future__ import annotations

import json
import os
import tempfile


ATTEMPT_RECORD_FILE = "attempt_record.json"
FORMAL_ACCEPTANCE_FILE = "acceptance.json"
ATTEMPT_RECORD_SCHEMA = "oprunway.workflow_attempt_record"
ATTEMPT_RECORD_SCHEMA_VERSION = 1

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


class ArtifactNameConflictError(RuntimeError):
    """正式总结与 attempt 总结本应互斥，但目标目录已有另一种文件名。"""


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
        return path
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def publish_acceptance_json(out_dir, acceptance):
    """正式 ``acceptance.json`` 的唯一写出原语。"""
    assert_formal_acceptance_allowed(acceptance)
    _assert_opposite_summary_absent(out_dir, FORMAL_ACCEPTANCE_FILE)
    return _atomic_write_json(out_dir, FORMAL_ACCEPTANCE_FILE, acceptance)


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
        "acceptance_verdict": None,
        "op": acceptance.get("op"),
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
        "note": (
            "本工件只记录未完成/阻塞的 workflow attempt；acceptance_verdict 恒为 null，"
            "不得命名、引用或渲染为正式验收裁决。"),
    }


def write_attempt_record(out_dir, acceptance):
    """原子写未完成 attempt；正式终态会被 ``build_attempt_record`` 拒绝。"""
    record = build_attempt_record(acceptance)
    _assert_opposite_summary_absent(out_dir, ATTEMPT_RECORD_FILE)
    return _atomic_write_json(out_dir, ATTEMPT_RECORD_FILE, record)
