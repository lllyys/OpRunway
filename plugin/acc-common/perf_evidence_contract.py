"""NPU measure-only 性能证据的确定性契约（stdlib-only）。

本模块只校事实，不判性能达标：

* ``sampling_receipt`` 让 msprof 原始 kernel 样本可重算到 ``us``；
* ``execution_identity`` 把采集绑定到 device / SoC / CANN / DUT；
* ``performance_requirement_unvalidated`` 把任务书中本轮没有比较的性能条款显式挂账。

任何 ratio / pass/fail 仍只归 :mod:`perf_compare`。资源类条款不是性能裁决输入，禁止混进
``task_pr_gaps`` 冒充性能条款。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics


SAMPLING_SCHEMA = "oprunway.perf_sampling_receipt"
EXECUTION_IDENTITY_SCHEMA = "oprunway.perf_execution_identity"
SCHEMA_VERSION = 1
PERFORMANCE_GAP_KIND = "performance_requirement_unvalidated"
PERFORMANCE_DIMENSION = "performance"
RESOURCE_DIMENSION = "resource"
UNVALIDATED_STATUS = "unvalidated"
REQUIREMENT_TYPES = frozenset({
    "gpu_comparison", "ratio", "absolute_latency", "throughput", "no_regression",
})
GPU_REQUIREMENT_TYPES = frozenset({"gpu_comparison"})
GROUND_GPU_COMPARISON = "gpu_comparison"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class PerfEvidenceContractError(ValueError):
    """性能证据结构或恒等关系不成立。"""


def canonical_sha(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as ex:
        raise PerfEvidenceContractError(f"对象不可 canonical JSON 化：{ex}") from ex
    return hashlib.sha256(raw).hexdigest()


def _finite_positive(value, where):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) or float(value) <= 0):
        raise PerfEvidenceContractError(f"{where} 须为有限正数（实得 {value!r}）")
    return float(value)


def validate_sampling_config(warmup, repeat):
    """校 live collector 的最小有效采样配置；不接受隐式 ``int()`` 转换。"""
    for label, value, lower in (("warmup", warmup, 1), ("repeat", repeat, 2)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise PerfEvidenceContractError(
                f"perf {label} 须为整数，不接受 bool/字符串隐式转换（实得 {value!r}）")
        if value < lower or value > 100000:
            raise PerfEvidenceContractError(
                f"perf {label} 须在 {lower}..100000（实得 {value}）")
    return {"warmup": warmup, "repeat": repeat}


def build_sampling_receipt(custom, *, case_id, warmup, repeat):
    """从 collector 的逐 kernel 样本重算单次调用耗时并返回内容寻址收据。"""
    if not isinstance(case_id, str) or not case_id:
        raise PerfEvidenceContractError("sampling_receipt.case_id 须为非空字符串")
    cfg = validate_sampling_config(warmup, repeat)
    if not isinstance(custom, dict) or custom.get("behavior") != "npu" \
            or custom.get("scope") != "kernel_only" \
            or custom.get("execution_path") != "device_kernel":
        raise PerfEvidenceContractError(
            "可计时 custom 记录须为 behavior=npu/scope=kernel_only/execution_path=device_kernel")
    collection = custom.get("collection")
    if not isinstance(collection, dict):
        raise PerfEvidenceContractError("custom.collection 缺失")
    for key, expected in (("warmup", warmup), ("repeat", repeat),
                          ("timing_scope", "kernel_only"),
                          ("kernel_accounting", "median_x_launches")):
        if collection.get(key) != expected:
            raise PerfEvidenceContractError(
                f"custom.collection.{key}={collection.get(key)!r} 与采样计划 {expected!r} 不一致")
    rows = custom.get("breakdown")
    if not isinstance(rows, list) or not rows:
        raise PerfEvidenceContractError("可计时 custom.breakdown 须为非空列表")

    normalized = []
    total = 0.0
    for index, row in enumerate(rows):
        where = f"custom.breakdown[{index}]"
        if not isinstance(row, dict):
            raise PerfEvidenceContractError(f"{where} 须为 object")
        samples = row.get("samples_us")
        launches = row.get("launches_per_invocation")
        if not isinstance(samples, list) or not samples:
            raise PerfEvidenceContractError(f"{where}.samples_us 须为非空列表")
        if isinstance(launches, bool) or not isinstance(launches, int) or launches < 1:
            raise PerfEvidenceContractError(f"{where}.launches_per_invocation 须为正整数")
        values = [_finite_positive(value, f"{where}.samples_us") for value in samples]
        if len(values) != repeat * launches:
            raise PerfEvidenceContractError(
                f"{where} 样本数 {len(values)} != repeat({repeat})*launches({launches})")
        if row.get("sample_count") != len(values) or row.get("repeat") != repeat:
            raise PerfEvidenceContractError(f"{where} sample_count/repeat 与真实样本不一致")
        discarded = row.get("discarded_prefix_count")
        if isinstance(discarded, bool) or not isinstance(discarded, int) \
                or discarded < 0 or discarded >= repeat:
            raise PerfEvidenceContractError(f"{where}.discarded_prefix_count 非法")
        median_us = float(statistics.median(values))
        invocation_us = median_us * launches
        if not math.isclose(float(row.get("median_launch_us", -1)), median_us,
                            rel_tol=1e-12, abs_tol=1e-12):
            raise PerfEvidenceContractError(f"{where}.median_launch_us 不能由 samples_us 重算")
        if not math.isclose(float(row.get("invocation_us", -1)), invocation_us,
                            rel_tol=1e-12, abs_tol=1e-12):
            raise PerfEvidenceContractError(f"{where}.invocation_us 不能由样本中位数重算")
        total += invocation_us
        normalized.append({
            "kernel_name": row.get("kernel_name"),
            "kernel_type": row.get("kernel_type"),
            "launches_per_invocation": launches,
            "repeat": repeat,
            "sample_count": len(values),
            "discarded_prefix_count": discarded,
            "samples_us": values,
            "median_launch_us": median_us,
            "invocation_us": invocation_us,
        })
    measured = _finite_positive(custom.get("us"), "custom.us")
    if not math.isclose(measured, total, rel_tol=1e-12, abs_tol=1e-12):
        raise PerfEvidenceContractError(
            f"custom.us={measured} != breakdown 重算总和 {total}")
    payload = {
        "schema": SAMPLING_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        **cfg,
        "timing_scope": "kernel_only",
        "kernel_accounting": "median_x_launches",
        "kernels": normalized,
        "npu_us": total,
    }
    return {"payload": payload, "sha256": canonical_sha(payload)}


def validate_sampling_receipt(custom, receipt, *, case_id, warmup, repeat):
    """现场从 custom 原始样本重建，并与落盘收据逐字对账。"""
    if not isinstance(receipt, dict) or set(receipt) != {"payload", "sha256"}:
        raise PerfEvidenceContractError("sampling_receipt 须恰含 payload/sha256")
    rebuilt = build_sampling_receipt(
        custom, case_id=case_id, warmup=warmup, repeat=repeat)
    if receipt != rebuilt:
        raise PerfEvidenceContractError("sampling_receipt 与 custom 原始样本现场重算结果漂移")
    return rebuilt


def validate_execution_identity(identity, *, expected=None):
    """校实际性能进程身份，并可与精度/build receipt 派生的期望身份逐字交叉。"""
    if not isinstance(identity, dict):
        raise PerfEvidenceContractError("perf execution_identity 须为 object")
    required = {
        "schema", "schema_version", "device_index", "device_name", "soc",
        "cann_version", "cann_observation_sha256", "dut_library_sha256",
        "dut_symbol_identity_sha256",
    }
    if set(identity) != required:
        raise PerfEvidenceContractError(
            f"perf execution_identity 字段须恰为 {sorted(required)}（实得 {sorted(identity)}）")
    if identity["schema"] != EXECUTION_IDENTITY_SCHEMA \
            or identity["schema_version"] != SCHEMA_VERSION:
        raise PerfEvidenceContractError("perf execution_identity schema 非法")
    if isinstance(identity["device_index"], bool) or not isinstance(identity["device_index"], int) \
            or identity["device_index"] < 0:
        raise PerfEvidenceContractError("perf execution_identity.device_index 须为非负整数")
    for key in ("device_name", "soc", "cann_version"):
        value = identity[key]
        if not isinstance(value, str) or not value.strip() or value.strip().lower() == "unknown":
            raise PerfEvidenceContractError(f"perf execution_identity.{key} 不得缺失/unknown")
    for key in ("cann_observation_sha256", "dut_library_sha256",
                "dut_symbol_identity_sha256"):
        if not isinstance(identity[key], str) or not _HEX64.fullmatch(identity[key]):
            raise PerfEvidenceContractError(f"perf execution_identity.{key} 须为小写 sha256")
    normalized = dict(identity)
    if expected is not None:
        if not isinstance(expected, dict):
            raise PerfEvidenceContractError("expected execution identity 须为 object")
        for key, value in expected.items():
            if key == "device_name":
                continue
            if normalized.get(key) != value:
                raise PerfEvidenceContractError(
                    f"perf execution_identity.{key} 与 receipt/plan 期望身份漂移")
    return normalized


def expected_execution_identity(receipt, *, device_index):
    """从精度阶段 receipt 派生性能进程必须逐字复现的身份子集。"""
    if not isinstance(receipt, dict):
        raise PerfEvidenceContractError("cpp_extension receipt 须为 object")
    runtime = receipt.get("runtime")
    vendor = receipt.get("vendor")
    if not isinstance(runtime, dict) or not isinstance(vendor, dict):
        raise PerfEvidenceContractError("receipt.runtime/vendor 缺失")
    if isinstance(device_index, bool) or not isinstance(device_index, int) or device_index < 0:
        raise PerfEvidenceContractError("性能 device_index 须为非负整数")
    expected = {
        "schema": EXECUTION_IDENTITY_SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "device_index": device_index,
        "soc": runtime.get("soc"),
        "cann_version": runtime.get("cann_version"),
        "cann_observation_sha256": canonical_sha(runtime.get("cann")),
        "dut_library_sha256": vendor.get("library_sha256"),
        "dut_symbol_identity_sha256": canonical_sha(vendor.get("symbol_identity")),
    }
    # 复用 actual validator 的类型/unknown/sha 规则；device_name 只可能由采集进程实测，
    # 不在 receipt 中，临时补受控哨兵后立即丢弃。
    validate_execution_identity(dict(expected, device_name="receipt-bound"), expected=expected)
    return expected


def validate_measure_only_requirement_gaps(spec, authorization):
    """校 measure-only 未验收条款账本；GPU 比较授权必须有对应性能 gap。"""
    if not isinstance(spec, dict) or not isinstance(authorization, dict):
        raise PerfEvidenceContractError("spec/authorization 须为 object")
    raw = spec.get("task_pr_gaps", [])
    if not isinstance(raw, list):
        raise PerfEvidenceContractError("spec.task_pr_gaps 须为 list")
    gaps = []
    for index, gap in enumerate(raw):
        if not isinstance(gap, dict):
            continue
        if gap.get("dimension") == RESOURCE_DIMENSION:
            raise PerfEvidenceContractError(
                f"task_pr_gaps[{index}] 是资源条款；资源不是验收轴，不得进入 task_pr_gaps/裁决")
        if gap.get("kind") != PERFORMANCE_GAP_KIND:
            continue
        required = {
            "kind", "dimension", "status", "requirement_type", "cite", "quote",
            "taskdoc_snapshot_sha256", "reason",
        }
        extra = set(gap) - required - {key for key in gap if key.startswith("_")}
        missing = required - set(gap)
        if missing or extra:
            raise PerfEvidenceContractError(
                f"task_pr_gaps[{index}] 性能未验收条款字段缺失={sorted(missing)} 未知={sorted(extra)}")
        if gap["dimension"] != PERFORMANCE_DIMENSION or gap["status"] != UNVALIDATED_STATUS \
                or gap["requirement_type"] not in REQUIREMENT_TYPES:
            raise PerfEvidenceContractError(f"task_pr_gaps[{index}] 性能 gap 受控值非法")
        for key in ("cite", "quote", "reason"):
            if not isinstance(gap[key], str) or not gap[key].strip():
                raise PerfEvidenceContractError(f"task_pr_gaps[{index}].{key} 须为非空字符串")
        digest = gap["taskdoc_snapshot_sha256"]
        if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
            raise PerfEvidenceContractError(
                f"task_pr_gaps[{index}].taskdoc_snapshot_sha256 须为小写 sha256")
        if digest != authorization.get("taskdoc_snapshot_sha256"):
            raise PerfEvidenceContractError(
                f"task_pr_gaps[{index}] 与 measure_only authorization 未绑定同一任务书快照")
        gaps.append(dict(gap))
    if authorization.get("taskdoc_requirement") == GROUND_GPU_COMPARISON \
            and not any(gap["requirement_type"] in GPU_REQUIREMENT_TYPES for gap in gaps):
        raise PerfEvidenceContractError(
            "measure_only/gpu_comparison 必须把未做的 GPU 性能比较条款以 "
            "performance_requirement_unvalidated gap 显式挂账")
    return gaps
