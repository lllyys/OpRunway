# -*- coding: utf-8 -*-
"""batched_parallel.py —— 批量卡逐矩阵判定的多进程分块驱动（S3 并行裁定，D3 卡）。

并行裁定（Mr.0 2026-09-24「拆吧，并行」）：批量 case 的逐矩阵判定只读共享数组、
无流耦合（与 gen 的单一 rand 流不同），可直接按矩阵区间切分多进程并行，
分段结果按矩阵序合并后与串行逐位相同。

本模块**不含任何判据实现**：每段仍调 criteria/verdict.judge（唯一裁决实现）对
连续矩阵区间的切片子批出子 verdict，父进程按区间升序把子 verdict 合并成与
verdict.judge 串行输出完全相等的批量 verdict（AGENTS.md §2 机械门纪律——切分与
合并是编排，不是第二套裁决）。逐位相同的保障分三层：

- 合并即串行折叠：fail_count/error_count 等取和，min/max 统计取 min/max（可结合、
  与折叠顺序无关），first_fail/first_error 取区间升序首个非空，worst 按
  verdict._matrix_severity 的同一序键做严格大于合并（同差保留更小序号，与串行同则）；
- 区间覆盖断言：切分必须连续、不重不漏地覆盖 [0, batch)，合并前逐段断言；
- 全比对断言：环境变量 ``OPRUNWAY_BATCHED_SELFCHECK`` 非空时另跑一次串行
  verdict.judge 并断言与合并结果相等（tests/test_batched_parallel.py 固定断言）。

任何结构预检不过（out32 非三维、info 分型不符、batch 声明不一致等）或子批出现
case 级 error verdict（fail_count 为 null）时，整体回退串行 verdict.judge——回退
路径的输出就是串行输出本身，逐位相同平凡成立（fail-closed，不产出第二种错误文本）。

入口：``judge_parallel(card, case_arrays, dut_out, jobs=1)``。非批量卡或
jobs<=1 或 batch<2 时直接等价于 ``verdict.judge``（既有六算子零漂移）。
多进程用 fork 上下文（目标环境为 Linux 容器；无 fork 的平台退化为进程内逐段，
结果不变）。
"""

import multiprocessing
import os
import re

import numpy as np

try:  # criteria 作为包被导入时
    from . import verdict
except ImportError:  # criteria 目录直接挂 sys.path 时（scripts 消费方）
    import verdict

# 逐矩阵 error 的串行前缀格式（verdict._judge_batched_inner 的 first_error 口径）。
_MATRIX_ERR_RE = re.compile(r"\A矩阵 (\d+): (.*)\Z", re.S)

# fork 子进程通过模块全局读共享数组（写时复制，不经 pickle 传大数组）。
_WORK = {}


class _FallbackToSerial(Exception):
    """并行前提不满足——整体回退串行 verdict.judge（fail-closed）。"""


def split_bounds(batch, jobs):
    """把 [0, batch) 切成至多 jobs 段连续区间，不重不漏（并行裁定的机械前提）。"""
    k = max(1, min(int(jobs), int(batch)))
    base, extra = divmod(int(batch), k)
    bounds, lo = [], 0
    for idx in range(k):
        hi = lo + base + (1 if idx < extra else 0)
        bounds.append((lo, hi))
        lo = hi
    if bounds[0][0] != 0 or bounds[-1][1] != batch:
        raise AssertionError(f"矩阵区间切分未覆盖 [0, {batch}): {bounds}")
    return bounds


def _stage(card, case_arrays, dut_out):
    """结构预检 + 共享工作集装配。预检口径与 verdict 的批量校验一致：任何不满足
    都抛 _FallbackToSerial，由串行 verdict.judge 出具其规范 error verdict。"""
    if not isinstance(dut_out, dict) or dut_out.get("status") != "ok":
        raise _FallbackToSerial("dut 结构/状态问题")
    out32 = dut_out.get("out32")
    if out32 is None:
        raise _FallbackToSerial("缺 out32")
    out32 = np.asarray(out32)
    if out32.ndim != 3 or out32.shape[0] < 1:
        raise _FallbackToSerial("out32 非批量三维")
    batch = int(out32.shape[0])

    info = dut_out.get("info")
    if card.info_kind == "array":
        info_arr = np.asarray(info) if info is not None else None
        if (info_arr is None or info_arr.ndim != 1 or info_arr.shape[0] != batch
                or not np.issubdtype(info_arr.dtype, np.integer)):
            raise _FallbackToSerial("infoArray 分型不符")
    else:
        if info is None or np.ndim(info) != 0:
            raise _FallbackToSerial("标量 info 分型不符")

    declared = case_arrays.get("batch")
    if declared is not None:
        try:
            if int(declared) != batch:
                raise _FallbackToSerial("batch 声明与被测不一致")
        except (TypeError, ValueError):
            raise _FallbackToSerial("batch 声明不可解析")

    sliced = {}
    for key in verdict._BATCH_SLICED_KEYS:
        if key in case_arrays:
            arr = np.asarray(case_arrays[key])
            if arr.ndim != 3 or arr.shape[0] != batch:
                raise _FallbackToSerial(f"{key} 非批量三维")
            sliced[key] = arr

    ratio_cpu = case_arrays.get("ratio_cpu")
    if ratio_cpu is not None:
        rarr = np.asarray(ratio_cpu, dtype=np.float64)
        if rarr.shape != (batch,):
            raise _FallbackToSerial("ratio_cpu 形状不符")
    else:
        rarr = None

    passthrough = {k: v for k, v in case_arrays.items()
                   if k not in verdict._BATCH_SLICED_KEYS
                   and k not in ("ratio_cpu", "batch")}
    _WORK.clear()
    _WORK.update(card=card, passthrough=passthrough, sliced=sliced,
                 rarr=rarr, out32=out32, info=dut_out.get("info"))
    return batch


def _chunk_worker(bounds):
    """判一段矩阵区间：从 _WORK 切出子批（batch=hi-lo，保批维），调 verdict.judge
    出子 verdict。判定只读共享数组，无跨段耦合。"""
    lo, hi = bounds
    w = _WORK
    card = w["card"]
    case_c = dict(w["passthrough"])
    for key, arr in w["sliced"].items():
        case_c[key] = arr[lo:hi]
    if w["rarr"] is not None:
        case_c["ratio_cpu"] = w["rarr"][lo:hi]
    info = w["info"]
    if card.info_kind == "array":
        info = np.asarray(info)[lo:hi]
    dut_c = {"out32": w["out32"][lo:hi], "info": info, "status": "ok"}
    return lo, hi, verdict.judge(card, case_c, dut_c)


def _shift_error(err, lo):
    """子批 first_error 的矩阵序号由段内序号平移为全局序号（串行同文本）。"""
    if err is None:
        return None
    m = _MATRIX_ERR_RE.match(err)
    if m is None:  # 非逐矩阵前缀 = case 级错误文本，不该出现在正常子批
        raise _FallbackToSerial(f"子批 error 非逐矩阵格式: {err!r}")
    return f"矩阵 {lo + int(m.group(1))}: {m.group(2)}"


def _merge(parts, batch):
    """按区间升序把子 verdict 合并成串行等价的批量 verdict（合并规则见模块文档）。"""
    cursor = 0
    for lo, hi, _ in parts:  # 区间覆盖断言（并行裁定）
        if lo != cursor:
            raise AssertionError(f"矩阵区间不连续: 期望起点 {cursor}，得到 {lo}")
        cursor = hi
    if cursor != batch:
        raise AssertionError(f"矩阵区间未覆盖整批: 终点 {cursor} != batch {batch}")

    fail_count = error_count = 0
    first_fail = first_error = None
    ratio_max = None
    worst_i = worst_v = worst_sev = None
    for lo, hi, v in parts:
        if v.get("fail_count") is None:  # case 级 error verdict → 整体回退串行
            raise _FallbackToSerial(f"子批返回 case 级 error: {v.get('error')!r}")
        if v["batch"] != hi - lo:
            raise AssertionError(f"子批 batch {v['batch']} != 区间宽 {hi - lo}")
        fail_count += v["fail_count"]
        if first_fail is None and v["first_fail_index"] is not None:
            first_fail = lo + v["first_fail_index"]
        if first_error is None and v["error"] is not None:
            first_error = _shift_error(v["error"], lo)
        d = v["diagnostics"]
        error_count += d["error_count"]
        if d["ratio_max"] is not None:
            ratio_max = (d["ratio_max"] if ratio_max is None
                         else max(ratio_max, d["ratio_max"]))
        sev = verdict._matrix_severity(v["worst"])
        if worst_sev is None or sev > worst_sev:
            worst_i, worst_v, worst_sev = lo + v["worst_index"], v["worst"], sev

    diagnostics = {
        "batch": batch,
        "pass_count": batch - fail_count,
        "fail_count": fail_count,
        "error_count": error_count,
        "ratio_max": ratio_max,
    }
    return {
        "batch": batch,
        "fail_count": fail_count,
        "first_fail_index": first_fail,
        "worst_index": worst_i,
        "worst": worst_v,
        "diagnostics": diagnostics,
        "numeric": "PASS" if fail_count == 0 else "FAIL",
        "formal": "PENDING_RULING",
        "flags": [],   # HT-16：T8 摘除，批量 verdict flags 恒空（与串行一致）
        "error": first_error,
    }


def _run_parts(bounds):
    """多进程跑各区间；无 fork 的平台退化为进程内逐段（切分与合并路径不变）。"""
    if len(bounds) == 1:
        return [_chunk_worker(bounds[0])]
    try:
        ctx = multiprocessing.get_context("fork")
    except ValueError:
        return [_chunk_worker(b) for b in bounds]
    with ctx.Pool(processes=len(bounds)) as pool:
        return pool.map(_chunk_worker, bounds)


def judge_parallel(card, case_arrays, dut_out, jobs=1):
    """批量卡逐矩阵判定的多进程分块入口；输出与 verdict.judge 串行逐位相同。

    - 非批量卡、jobs<=1 或 batch<2：直接返回 verdict.judge（零漂移）。
    - 并行路径：结构预检 → 矩阵区间切分（split_bounds）→ 各段 verdict.judge
      子批 → 按序合并（_merge，含覆盖断言）。任何前提不满足即回退串行。
    - 环境变量 OPRUNWAY_BATCHED_SELFCHECK 非空时另跑串行全比对并断言相等。
    """
    if not getattr(card, "batched", False) or int(jobs) <= 1:
        return verdict.judge(card, case_arrays, dut_out)
    try:
        batch = _stage(card, case_arrays, dut_out)
        if batch < 2:
            raise _FallbackToSerial("batch<2 无可切分区间")
        parts = _run_parts(split_bounds(batch, jobs))
        merged = _merge(parts, batch)
    except _FallbackToSerial:
        return verdict.judge(card, case_arrays, dut_out)
    finally:
        _WORK.clear()
    if os.environ.get("OPRUNWAY_BATCHED_SELFCHECK"):
        serial = verdict.judge(card, case_arrays, dut_out)
        if merged != serial:
            raise AssertionError(
                "并行分块结果与串行不一致（OPRUNWAY_BATCHED_SELFCHECK）")
    return merged
