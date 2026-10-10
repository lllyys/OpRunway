#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_harness.py —— 验收共享 harness 的编排层：现场造输入 → 执行 → 判定 → 报告。

四段式 harness（2026-10-09d 裁定）的第四段。前三段是 `build_dut.py`（S1 构建）、
`harness_exec.cpp` 加 `exec_case.py`（S2 执行）、`evidence.py`（取证）。

一套工具两方用（开发者自测与验收者验收走同一条执行通路），但**判定只有一份实现**：
本层直接调 `criteria/` 的 `verdict` / `batched_a0` / `batched_parallel`，不复制判定
逻辑；输入也直接调 case-gen 的 `gen_data_cholesky`，不另建生成器。验收证据只认
验收者自己这次运行的产物。

## 输入来源

| 来源 | 参数 | 构造实现 |
| --- | --- | --- |
| Cholesky 任务包 | `--canonical` 加 `--gen-dir` | `gen_data_cholesky.build_case_arrays`、批量走 `build_batched_contents` 加 `expand_sampled_rows` |
| 通路演练 | `--probe-op` 加 `--n/--batch/--seed` | 同仓的 `fill_spd_cu` / `fill_hpd_cu`（现工程算子无 case-gen 用例，复用矩阵构造件） |

## 判定分流

`--judge auto` 按算子查 criteria 的判定卡：

- Cholesky 十算子有卡 → 走 criteria 一段式残差判定，出数值 PASS/FAIL。
- 现工程算子（cmatinv_batched 等）无卡 → 只做**结构性验证**：三键落地、形状与
  dtype 合法、数值有限、info 形态合规，并记一个诊断用的求逆残差（两种布局口径
  各算一遍，报哪一种对上）。**结构性验证不是数值判定**，报告里 `numeric` 记
  `NOT_JUDGED`，真判定等 Cholesky 交付。

## 复跑

`--rerun N`（N≥2）由执行子进程在同一进程同一 stream 内完成：每轮从原始副本恢复
输入、逐键 bit-wise 比对（本仓 Q4 裁定，判据就是这一句）。**确定性结论独立于数值
结论**——算子跑完了但两轮输出不同，数值面照常判、确定性面单独报。规定轮数没跑完
时确定性不出结论（`det_unknown`），不按通过算。

## 目录生命周期

每次运行自己一套目录：`--work-dir` 与 `--evidence-dir` 下各开一层 `run-<时间戳>-<pid>`。
一件证据只能来自一次运行，所以留证件一律发布进全新目录，不往已有目录补文件。

## 独立重判

`--rejudge <留证目录>` 读 `inputs.npz` 加 `dut.npz` 加 `case.json`，走同一判定函数
复算结论，不连真机。判定参数不重算——用留证件 `case.json` 的 `judge_context`
（当场真正用的 `ratio_cpu`、`ratio_cpu_status`、`ratio_cpu_mean`、`sample_map`）。
确定性结论也要复验，类别与退出码如下：

| 确定性复判类别 | 判据 | 退出码贡献 |
| --- | --- | --- |
| `not_applicable` | 原运行没开复跑 | 0 |
| `consistent_recorded` | 原结论一致（一致时没有另存的逐轮输出），按记录恢复 | 0 |
| `mismatch_confirmed` | 原结论失配，且失配轮字节与首轮确实不同 | 1 |
| `mismatch_not_reproduced` | 原结论失配，但两份字节相同——结论与证据对不上 | 1 |
| `mismatch_unverifiable` | 原结论失配，失配轮字节没留下 | 1 |
| `incomplete` | 原运行规定轮数未跑完，确定性无结论 | 1 |

退出码：0 全部通过（含确定性无失配）；1 存在执行失败、数值 FAIL、结构性不合格或
确定性失配；2 入口参数或契约错误。`formal` 恒 `PENDING_RULING`，本工具不出具正式结论。

## 术语

`三键`指被测输出 `{out32, info, status}`（协议见 `evidence.py` 模块头）；`判定卡`
是 `criteria/cards_cholesky.py` 里一个算子的判据条目；`ratio_cpu` 是同精度 CPU 链
算出的残差基线（实现在 case-gen 的 `fill_ratio_cpu.py`）。文中 `PROBE §x`、
`Qn 裁定`、`HT-nn` 只是本仓开发记录的编号，随包不可达——判据本身都在正文就地写明，
编号仅作出处。
"""

import argparse
import json
import gzip
import uuid
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np

import evidence
import exec_case
import op_abi

TOOL = "run_harness.py"
TOOL_VER = "h1-r2"
HERE = Path(__file__).resolve().parent
CRITERIA = HERE.parent.parent / "criteria"
RATIO_BASIS = "A32-f64"

# 重判时确定性复判类别里**算通过**的两个（其余一律退非零，表见模块头）。
DET_REJUDGE_OK = ("not_applicable", "consistent_recorded")


class ContractError(RuntimeError):
    pass


def run_id():
    """本次运行的目录名：时间戳加 pid，保证两次运行不落同一个目录。"""
    return time.strftime("run-%Y%m%dT%H%M%S") + f"-{os.getpid()}-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# 同源实现装入（与 stream_check 同一套路径注入，避免平行副本）
# ---------------------------------------------------------------------------

def load_modules(gen_dir):
    gen_dir = Path(gen_dir).expanduser().resolve()
    if not (gen_dir / "gen_data_cholesky.py").is_file():
        raise ContractError(f"--gen-dir 里找不到 gen_data_cholesky.py: {gen_dir}")
    for p in (str(CRITERIA), str(gen_dir), str(HERE.parent)):
        if p not in sys.path:
            sys.path.insert(0, p)
    import render_verify as frozen_mod
    import accept_run as package_mod
    import batched_a0 as a0_mod                 # noqa: E402
    import batched_parallel as bp_mod           # noqa: E402
    import cards_cholesky as cards_mod          # noqa: E402
    import fill_ratio_cpu as ratio_mod          # noqa: E402
    import gen_data_cholesky as gen_mod         # noqa: E402
    import verdict as verdict_mod               # noqa: E402
    return {"frozen": frozen_mod, "package": package_mod, "a0": a0_mod, "bp": bp_mod, "cards": cards_mod, "ratio": ratio_mod,
            "gen": gen_mod, "verdict": verdict_mod}


def has_criteria_card(mods, op):
    try:
        mods["cards"].get_card(op)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 输入构造：全部复用既有实现
# ---------------------------------------------------------------------------

def probe_case(mods, op, n, batch, seed, nrhs=0, uplo="L"):
    """通路演练输入：复用 case-gen 的矩阵构造件造一批良态矩阵。

    现工程算子在 case-gen 里没有用例，但矩阵构造件可以直接复用——`fill_hpd_cu`
    产 Hermitian 正定矩阵、`fill_spd_cu` 产对称正定矩阵，两者都非奇异，正是求逆族
    算子的合法输入。本函数只拼批维，不新写分布。
    """
    abi = op_abi.get(op)
    gen = mods["gen"]
    rng = np.random.default_rng(seed)
    cx = abi.dtype == "complex64"
    mats = [(gen.fill_hpd_cu(rng, n) if cx else gen.fill_spd_cu(rng, n))
            for _ in range(batch)]
    A64 = np.ascontiguousarray(np.stack(mats)) if abi.batched \
        else np.ascontiguousarray(mats[0])
    A32 = A64.astype(np.complex64 if cx else np.float32)
    return {"case_id": f"probe-{op}-n{n}-b{batch}-s{seed}", "op": op, "n": n,
            "batch": batch if abi.batched else 1, "nrhs": nrhs, "uplo": uplo,
            "seed": seed, "source": "cu", "case_purpose": "probe",
            "_arrays": {"A64": A64, "A32": A32}}


def out32_shape(abi, case):
    """out32 的声明形状。列数口径由 ABI 表的 out32 来源定，不按算子名特判。

    来源与形状口径的对应在 `op_abi.OUT32_SOURCE` 与 `op_abi.OUT32_LAYOUT`：
    `matrix` 沿矩阵形状（列数 n），`rhs` 列数取 nrhs，`vector` 是长度 n 的向量
    （cheevj 的实数特征值 W 即此类，dtype 也与矩阵不同）——`vector` 的形状派生没有
    实现，明确拒绝，不拿矩阵形状冒充。
    """
    n = int(case["n"])
    batch = int(case.get("batch") or 1)
    nrhs = int(case.get("nrhs") or 0)
    layout = abi.out32_layout
    if layout == "rhs":
        cols = nrhs
    elif layout == "matrix":
        cols = n
    else:
        raise op_abi.AbiError(
            f"{abi.op}: out32 来源是 {abi.out32_source}（形状口径 {layout}），"
            f"形状不沿矩阵形状；本层未实现该口径的形状派生，"
            f"调用形状 {abi.call_shape} 也不在执行段内")
    return (batch, n, cols) if abi.batched else (n, cols)


def build_spec(abi, case, runs, device_id, layout="row_major"):
    """执行子进程的 spec。`layout` 是双口径分流点：现工程行主序，任务书列主序。"""
    shape = out32_shape(abi, case)
    return {
        "op": abi.op, "abi": abi.abi, "call_shape": abi.call_shape,
        "dtype": abi.dtype, "info_kind": abi.info_kind,
        "device_id": device_id, "n": int(case["n"]),
        "batch": int(case.get("batch") or 1), "nrhs": int(case.get("nrhs") or 0),
        "lda": int(case.get("lda") or case["n"]),
        "ldb": int(case.get("ldb") or case.get("nrhs") or case["n"]),
        "uplo": "?" if case.get("info_probe") == "bad_param_uplo" else case.get("uplo") or "L",
        "info_probe": case.get("info_probe"),
        "runs": int(runs), "layout": layout,
        "out32_shape": ",".join(str(x) for x in shape),
    }


# ---------------------------------------------------------------------------
# 判定：criteria 唯一实现 / 结构性验证
# ---------------------------------------------------------------------------

def _inv_residual(A, C):
    """诊断用求逆残差 ‖I − A·C‖₁ / (n·‖A‖₁·‖C‖₁)。只做证据，不做判据。"""
    A64 = np.asarray(A, dtype=np.complex128 if np.iscomplexobj(A) else np.float64)
    C64 = np.asarray(C, dtype=A64.dtype)
    n = A64.shape[-1]
    eye = np.eye(n, dtype=A64.dtype)
    num = np.abs(eye - A64 @ C64).sum(axis=-2).max(axis=-1)
    den = n * np.abs(A64).sum(axis=-2).max(axis=-1) * np.abs(C64).sum(axis=-2).max(axis=-1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(den > 0, num / den, np.inf)
    return float(np.max(ratio))


def structural_check(abi, case, inputs, dut):
    """结构性验证：三键落地、形状 dtype 合法、数值有限、info 形态合规。

    **不是数值判定**：返回的 `numeric` 恒为 `NOT_JUDGED`。criteria 的判定卡只覆盖
    Cholesky 十算子；现工程算子的真判定等 Cholesky 交付后按交付头接入。
    """
    checks, failures = [], []

    def ck(name, ok, detail=""):
        checks.append({"name": name, "pass": bool(ok), "detail": detail})
        if not ok:
            failures.append(f"{name}: {detail}")

    ck("status_ok", dut.get("status") == "ok", f"status={dut.get('status')!r}")
    out32 = dut.get("out32")
    info = dut.get("info")
    ck("out32_present", out32 is not None)
    ck("info_present", info is not None)
    if out32 is None or info is None:
        return {"numeric": "NOT_JUDGED", "structural": "FAIL",
                "checks": checks, "failures": failures, "sanity": None}

    want_shape = out32_shape(abi, case)
    want_dtype = exec_case.NP_DTYPE[abi.dtype]
    ck("out32_shape", tuple(out32.shape) == want_shape,
       f"{tuple(out32.shape)} vs 期望 {want_shape}")
    ck("out32_dtype", out32.dtype == want_dtype,
       f"{out32.dtype} vs 期望 {np.dtype(want_dtype).name}")
    ck("out32_finite", bool(np.isfinite(out32).all()),
       "存在 NaN 或 Inf" if not bool(np.isfinite(out32).all()) else "")
    if abi.info_kind == "array":
        ok = isinstance(info, np.ndarray) and info.dtype == np.int32 \
            and info.shape == (int(case.get("batch") or 1),)
        ck("info_shape_array", ok,
           f"期望 (batch,) int32，得到 {getattr(info, 'shape', type(info))} "
           f"{getattr(info, 'dtype', '')}")
    else:
        ck("info_shape_scalar", np.ndim(info) == 0,
           f"期望标量，得到 ndim={np.ndim(info)}")

    # 诊断证据：求逆残差。片上布局口径两算（现工程文档自述行主序，任务书要列主序），
    # 报哪一种对上——这是证据，不是判据。
    # 口径判别的边界：输入是 Hermitian 或对称矩阵时 conj(Aᵀ) == A，两个残差必然相等，
    # `layout_match` 对这类输入判别不出布局。要判布局得喂非对称输入。
    sanity = None
    if abi.call_shape in ("outofplace_a_ainv", "inplace_a") and "A64" in inputs:
        A = np.asarray(inputs["A64"])
        C = np.asarray(out32)
        try:
            direct = _inv_residual(A, C)
            swapped = _inv_residual(np.conjugate(np.swapaxes(A, -1, -2)), C)
            indistinguishable = np.allclose(A, np.conjugate(np.swapaxes(A, -1, -2)))
            sanity = {"inv_residual_direct": direct,
                      "inv_residual_transpose_conj": swapped,
                      "layout_match": ("indistinguishable" if indistinguishable
                                       else "direct" if direct <= swapped
                                       else "transpose_conj"),
                      "note": "诊断证据，不是判据；判定卡覆盖 Cholesky 十算子。"
                              "对称/Hermitian 输入下两个残差必然相等，布局判别不出"}
        except (ValueError, TypeError) as exc:
            sanity = {"error": f"{type(exc).__name__}: {exc}"}

    return {"numeric": "NOT_JUDGED",
            "structural": "PASS" if not failures else "FAIL",
            "checks": checks, "failures": failures, "sanity": sanity}


def criteria_judge(mods, card, case, arrays, dut, ratio_cpu, ratio_status,
                   ratio_mean=None, jobs=1, sample_map=None):
    """criteria 判定入口。单矩阵直通 verdict.judge，批量走 A0 两层。"""
    case_arrays = dict(arrays)
    case_arrays.update(case)
    case_arrays["ratio_cpu"] = ratio_cpu
    case_arrays["ratio_cpu_status"] = ratio_status
    if sample_map is not None:
        case_arrays["sample_map"] = sample_map
    if ratio_mean is not None:
        case_arrays["ratio_cpu_mean"] = ratio_mean
    if getattr(card, "batched", False):
        return mods["a0"].judge_a0(card, case_arrays, dut, jobs=jobs)
    return mods["bp"].judge_parallel(card, case_arrays, dut, jobs=jobs)


# ---------------------------------------------------------------------------
# case 级编排
# ---------------------------------------------------------------------------

def prepare_case(mods, case, abi):
    """现场造输入，返回 (arrays, exec_inputs, sample_map)。

    `arrays` 给判定用（含 A64/golden 等），`exec_inputs` 只含要落 bin 的 f32/c64
    输入——大数组不重复一份。
    """
    gen = mods["gen"]
    sample_map = None
    if case.get("case_purpose") == "probe":
        arrays = case.pop("_arrays")
        return arrays, {"in_a": arrays["A32"]}, None
    gen.validate_case(case)          # 进场校验走 case-gen 同一实现
    if abi.batched:
        contents = (gen.build_batched_info_arrays(case)
                    if case.get("case_purpose") == "info"
                    else gen.build_batched_contents(case))
        sample_map = case.get("sample_map")
        if sample_map is None:
            raise ContractError(f"{case['case_id']}: 缺冻结 sample_map")
        if sample_map != gen.derive_sample_map(case["seed"], case["batch"]):
            raise ContractError(f"{case['case_id']}: 冻结 sample_map 与输入构造不一致")
        arrays = gen.expand_sampled_rows(contents, sample_map, 0, int(case["batch"]))
        arrays["_contents"] = contents
    else:
        arrays = gen.build_case_arrays(case)
        if case.get("case_purpose") == "info":
            arrays = gen.build_info_arrays(case, arrays)
    exec_inputs = {"in_a": arrays["A32"]}
    if ("potrs" in abi.op or "potri" in abi.op) and case.get("case_purpose") != "info":
        raise ContractError("awaiting_delivery: potrs/potri 因子准备与实际交付接口尚未接入")
    if abi.call_shape == "potrs_b_inplace":
        if "B32" not in arrays:
            raise ContractError(f"{case['case_id']}: potrs 族缺 B32 输入")
        exec_inputs["in_b"] = arrays["B32"]
    return arrays, exec_inputs, sample_map


def run_one(mods, case, abi, ctx):
    """跑一个 case 的全程：造输入 → 执行 → 判定 → 取证。返回报告记录。"""
    cid = case["case_id"]
    t0 = time.time()
    arrays, exec_inputs, sample_map = prepare_case(mods, case, abi)
    contents = arrays.pop("_contents", None)

    if ctx["layout"] != "row_major":
        raise ContractError("column_major 尚未实现；不得按 row_major 执行")
    spec = build_spec(abi, case, ctx["rerun"], ctx["device_id"], ctx["layout"])
    exec_rec = exec_case.run_case(
        ctx["executor"], Path(ctx["work_dir"]) / cid, spec, exec_inputs,
        ctx["repo"], ctx["ascend_home"], ctx["build_dir"], ctx["timeout"],
        ctx["cwd"])
    dut = evidence.three_key(exec_rec["out32"], exec_rec["info"], exec_rec["status"])
    det = exec_case.rerun_record(exec_rec["result"])
    loaded_lib = exec_case.loaded_lib_record(exec_rec["result"].get("lib_path"),
                                             ctx.get("provenance"))

    # 判定上下文：当场真正用的那组参数，原样进留证件供重判照抄（不让重判重算基线）
    judge_arrays = contents if contents is not None else arrays
    judge_context = {"mode": ctx["judge"],
                     "arrays_scope": "a0_contents" if contents is not None
                                     else "single",
                     "jobs": ctx["jobs"], "sample_map": sample_map}
    verdict_rec, numeric = None, None
    if exec_rec["kind"] in exec_case.EXECUTED_KINDS:
        if ctx["judge"] == "criteria":
            card = mods["cards"].get_card(case["op"])
            if case.get("case_purpose") == "info":
                ratio_cpu, ratio_status, ratio_mean = None, None, None
            else:
                if "ratio_cpu" not in case or "ratio_cpu_status" not in case:
                    raise ContractError(f"{cid}: 缺包内固化 ratio_cpu/ratio_cpu_status")
                ratio_cpu, ratio_status = case["ratio_cpu"], case["ratio_cpu_status"]
                ratio_mean = case.get("ratio_cpu_mean")

            judge_context.update({"ratio_cpu": ratio_cpu,
                                  "ratio_cpu_status": ratio_status,
                                  "ratio_cpu_mean": ratio_mean,
                                  "ratio_basis": RATIO_BASIS})
            verdict_rec = criteria_judge(
                mods, card, case, judge_arrays, dut, ratio_cpu, ratio_status,
                ratio_mean, ctx["jobs"], sample_map)
            numeric = verdict_rec.get("numeric")
        else:
            verdict_rec = structural_check(abi, case, arrays, dut)
            numeric = verdict_rec["structural"]   # 结构性结论，不是数值结论

    rec = {
        "case_id": cid, "op": case["op"], "n": case.get("n"),
        "batch": case.get("batch"), "uplo": case.get("uplo"),
        "abi": abi.abi, "call_shape": abi.call_shape,
        "exec": {"kind": exec_rec["kind"], "status": exec_rec["status"],
                 "detail": exec_rec["detail"], "returncode": exec_rec["returncode"],
                 "wall_seconds": exec_rec["wall_seconds"],
                 "lib_path": exec_rec["result"].get("lib_path"),
                 "loaded_lib": loaded_lib,
                 "three_key_error": exec_rec.get("three_key_error"),
                 "run1_ms": exec_rec["result"].get("run1_ms"),
                 "op_ret": exec_rec["result"].get("run1_ret")},
        "judge_mode": ctx["judge"],
        "verdict": verdict_rec,
        "determinism": det,
        "seconds": round(time.time() - t0, 3),
    }

    # numeric 在 criteria 模式是数值结论，在 structural 模式是结构性结论；
    # 两种模式下「不是 PASS 就留证」的规则相同，不分支。
    keep, reason = evidence.should_keep(exec_rec["kind"], numeric, det,
                                        ctx["keep_ids"], cid)
    exec_rec["loaded_lib"] = loaded_lib
    if loaded_lib.get("match") is False and not keep:
        keep, reason = True, "loaded_library_mismatch"
    if keep:
        # 判定用的那套数组进留证件：批量是「代表内容」（批维 k），单矩阵是它自己。
        # 判定侧只读 verdict._BATCH_SLICED_KEYS 这六个键，所以逐键照搬这六个。
        bundle = evidence.export_failure(
            Path(ctx["evidence_dir"]) / cid, case, abi, exec_rec,
            {k: judge_arrays.get(k) for k in
             ("A64", "A32", "B64", "B32", "golden64", "golden32")},
            dut, reason, ctx["provenance"], verdict_rec, HERE,
            determinism=det, judge_context=judge_context)
        ok, missing = evidence.bundle_is_self_contained(
            bundle, require_dut=exec_rec["kind"] in exec_case.EXECUTED_KINDS)
        rec["evidence"] = {"bundle": str(bundle), "reason": reason,
                           "self_contained": ok, "missing": missing}
        # 导出成功后 case 现场就是重复的一份，删大二进制；导出不全时留着现场
        if ok:
            rec["case_sweep"] = evidence.sweep_case_dir(exec_rec["case_dir"])
    else:
        rec["evidence"] = None
        rec["discarded_arrays"] = evidence.discard(arrays, dut, exec_inputs,
                                                   contents or {})
        rec["case_sweep"] = evidence.sweep_case_dir(exec_rec["case_dir"])
    return rec


def compute_ratio(mods, case, arrays, contents, sample_map):
    """CPU 同精度链的 ratio_cpu（批量逐内容）。实现在 fill_ratio_cpu，本层只编排。"""
    ratio_mod, verdict_mod = mods["ratio"], mods["verdict"]
    lapack = ratio_mod._lapack()
    if contents is None:
        try:
            return float(ratio_mod.run_chain(verdict_mod, lapack, case, arrays)), "ok"
        except ratio_mod.PrepFailed:
            return None, "prep_failed"
    base_op = mods["cards"].get_card(case["op"]).base_op
    vals, failed = [], 0
    for i in range(len(sample_map)):
        sub_case = {"case_id": f"{case['case_id']}#c{i}", "op": base_op,
                    "uplo": case["uplo"]}
        sub_arrays = {k: contents[k][i]
                      for k in ("A64", "A32", "B64", "B32") if k in contents}
        try:
            vals.append(float(ratio_mod.run_chain(verdict_mod, lapack, sub_case,
                                                  sub_arrays)))
        except ratio_mod.PrepFailed:
            vals.append(None)
            failed += 1
    return vals, ("ok" if failed < len(sample_map) else "prep_failed")


# ---------------------------------------------------------------------------
# 独立重判
# ---------------------------------------------------------------------------

def recheck_determinism(bundle, meta, dut):
    """复验确定性结论：比留证件里保存的各轮输出字节，不只复述 `case.json` 的结论。

    类别与退出码贡献见模块头的表。能比的只有「首轮」与「失配轮」两份字节：一致的
    运行没有另存件可比，按记录恢复并如实标明没有逐轮字节可核。
    """
    det = meta.get("determinism")
    if not det or int(det.get("runs") or 1) < 2:
        return {"class": "not_applicable", "recorded": det,
                "note": "原运行没开复跑（runs<2），没有确定性结论要复验"}
    consistent = det.get("consistent")
    if consistent is None:
        return {"class": "incomplete", "recorded": det,
                "note": "原运行规定轮数未跑完，确定性无结论——不按通过算"}
    if consistent is True:
        return {"class": "consistent_recorded", "recorded": det,
                "note": "原结论一致；一致时无另存的逐轮输出，按记录恢复，"
                        "逐轮字节不可核"}
    compared, differs = {}, []
    for key, name, dtype in (("out32", "out32.bin.diff.bin", None),
                             ("info", "info.bin.diff.bin", np.int32)):
        saved_path = bundle / name
        base = dut.get(key)
        if not saved_path.is_file() or base is None:
            continue
        base_bytes = np.ascontiguousarray(
            np.asarray(base) if dtype is None
            else np.asarray(base, dtype=dtype)).tobytes()
        saved = saved_path.read_bytes()
        compared[key] = {"first_bytes": len(base_bytes),
                         "saved_bytes": len(saved),
                         "differs": saved != base_bytes}
        if saved != base_bytes:
            differs.append(key)
    if not compared:
        return {"class": "mismatch_unverifiable", "recorded": det,
                "note": "原结论失配，但失配轮字节没留在留证件里，复验不了"}
    if differs:
        return {"class": "mismatch_confirmed", "recorded": det,
                "compared": compared, "differing_keys": differs,
                "note": "失配轮字节与首轮确实不同：确定性失配复现"}
    return {"class": "mismatch_not_reproduced", "recorded": det,
            "compared": compared,
            "note": "失配轮字节与首轮相同：原结论与留下的证据对不上"}


def rejudge(bundle_dir, gen_dir, jobs=1):
    """读留证件复算结论，不连真机。返回 (记录, 退出码)。

    判定参数不重算：用 `case.json` 的 `judge_context`（当场真正用的 ratio_cpu、
    ratio_cpu_status、ratio_cpu_mean、sample_map）。批量件的 `inputs.npz` 存的是
    代表内容（批维 k），配 `dut.npz` 的全批输出走 A0 两层，不退化成单矩阵链。
    """
    bundle = Path(bundle_dir).expanduser().resolve()
    meta_path = bundle / "case.json"
    if not meta_path.is_file():
        raise ContractError(f"{bundle} 不是留证件（缺 case.json）")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    case, abi_d = meta["case"], meta["abi"]
    dut_path = bundle / "dut.npz"
    if not dut_path.is_file():
        return {"case_id": case.get("case_id"), "rejudge": "no_dut",
                "reason": meta.get("reason"),
                "note": "执行未产出三键，没有可重判的被测输出"}, 1
    mods = load_modules(gen_dir)
    abi = op_abi.get(abi_d["op"])
    dut = evidence.load_dut_npz(dut_path)
    det_rec = recheck_determinism(bundle, meta, dut)
    det_ok = det_rec["class"] in DET_REJUDGE_OK
    original_exec = meta.get("exec") or {}
    if (dut.get("status") != "ok"
            or original_exec.get("kind") not in (None, *exec_case.EXECUTED_KINDS)
            or (original_exec.get("loaded_lib") or {}).get("match") is False
            or meta.get("reason") == "loaded_library_mismatch"):
        # status 非 ok 即执行失败，不判精度（SKILL.md 检查条件）。三键仍在留证件里，
        # 供人看现场；重判只复述执行失败这件事，不把它说成数值 FAIL。
        return {"case_id": case.get("case_id"), "op": case.get("op"),
                "rejudge": "exec_failed", "status": dut["status"],
                "reason": meta.get("reason"), "determinism": det_rec,
                "note": "被测输出 status 非 ok：记执行失败，不参与数值统计；"
                        "先解决执行问题再重新运行"}, 1
    with np.load(bundle / "inputs.npz", allow_pickle=False) as z:
        arrays = {k: z[k] for k in z.files}
    jc = meta.get("judge_context")
    recorded_mode = (jc or {}).get("mode")
    if recorded_mode == "criteria" or (recorded_mode is None and has_criteria_card(mods, case["op"])):
        if jc is None:
            raise ContractError(
                f"{bundle} 缺 judge_context（当场判定参数）：重判不重算基线，"
                "旧留证件请重新运行一次产出新件（存量不管理，以新为准）")
        card = mods["cards"].get_card(case["op"])
        v = criteria_judge(mods, card, case, arrays, dut,
                           jc.get("ratio_cpu"), jc.get("ratio_cpu_status"),
                           jc.get("ratio_cpu_mean"), jobs, jc.get("sample_map"))
        numeric = v.get("numeric")
    else:
        v = structural_check(abi, case, arrays, dut)
        numeric = v["structural"]
    rec = {"case_id": case.get("case_id"), "op": case.get("op"),
           "rejudge": "done", "numeric": numeric, "verdict": v,
           "determinism": det_rec,
           "judge_context": jc,
           "original": {"reason": meta.get("reason"),
                        "exec_kind": (meta.get("exec") or {}).get("kind"),
                        "verdict": meta.get("verdict"),
                        "determinism": meta.get("determinism")}}
    return rec, (0 if (numeric == "PASS" and det_ok) else 1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _pick_cases(mods, canonical_path, ops, max_n, limit):
    doc = json.loads(Path(canonical_path).read_text(encoding="utf-8"))
    cases = doc.get("cases")
    if not isinstance(cases, list):
        raise ContractError(f"{canonical_path}: 顶层缺 cases 数组")
    package = Path(canonical_path).parent
    candidates = [package / "cases" / "index.json", package / "cases" / "index.json.gz"]
    available = [p for p in candidates if p.is_file()]
    if len(available) != 1:
        raise ContractError("任务包必须恰有一份 cases/index.json 或 index.json.gz")
    idx = available[0]
    opener = gzip.open if idx.suffix == ".gz" else open
    with opener(idx, "rt", encoding="utf-8") as fh:
        index_doc = json.load(fh)
    if index_doc.get("ratio_basis") != RATIO_BASIS:
        raise ContractError(f"旧基包不受理：index.ratio_basis 必须为 {RATIO_BASIS}")
    manifest_path = package / "manifest.json"
    if not manifest_path.is_file():
        raise ContractError("任务包缺 manifest.json，无法核验固化基线指纹")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mismatches, warnings = mods["package"].check_fingerprints(package, manifest)
    if mismatches:
        raise ContractError(f"任务包证据指纹不符: {mismatches}")
    for warning in warnings:
        print(f"[{TOOL}] {warning}", file=sys.stderr)
    ratio_mean = index_doc.get("ratio_cpu_mean")
    base_by_id = {c["case_id"]: c for c in cases}
    full = doc.get("package_scope") == "full"
    derived = (list(mods["gen"].derive_info_cases(cases, require_s1=not full))
               + list(mods["gen"].derive_batched_info_cases(cases, require_s1=not full)))
    base_by_id.update({c["case_id"]: c for c in derived})
    cases, seen = [], set()
    for entry in index_doc.get("cases", []):
        cid = entry.get("case_id")
        if cid in seen or cid not in base_by_id:
            raise ContractError(f"index case_id 重复或不属于 canonical/info 派生: {cid}")
        seen.add(cid)
        base = base_by_id[cid]
        for key in ("op", "n", "batch", "seed", "uplo", "nrhs", "info_probe", "k_expected"):
            if key in entry and key in base and entry[key] != base[key]:
                raise ContractError(f"{cid}: index.{key} 与 canonical/info 派生不符")
        case = dict(base)
        case.update(entry)
        if case.get("case_purpose") != "info":
            if "ratio_cpu" not in entry or entry.get("ratio_cpu_status") not in ("ok", "prep_failed"):
                raise ContractError(f"{cid}: 包内固化基线缺失或未回填")
        cases.append(case)
    missing = set(base_by_id) - seen
    if missing:
        raise ContractError(f"index 缺 canonical/info 派生用例: {sorted(missing)[:5]}")
    # Reuse the native/package verifier's one frozen-field admission contract.
    for op in sorted({c["op"] for c in cases}):
        op_cases = [c for c in cases if c["op"] == op]
        try:
            frozen = mods["frozen"].load_frozen_index(package, op_cases, op)
        except ValueError as exc:
            raise ContractError(str(exc)) from exc
        for case in op_cases:
            case.pop("ratio_cpu_mean", None)
            case.update(frozen[case["case_id"]])
    want = set(o.strip() for o in ops.split(",")) if ops else None
    picked = [c for c in cases
              if (want is None or c.get("op") in want)
              and (max_n is None or c.get("n", 0) <= max_n)]
    if limit:
        picked = picked[:limit]
    if not picked:
        raise ContractError("筛选后没有 case 可跑（检查 --ops/--max-n）")
    return picked, ratio_mean


def main(argv=None):
    ap = argparse.ArgumentParser(prog=TOOL, description=__doc__.splitlines()[0])
    ap.add_argument("--gen-dir", required=True,
                    help="repo-task-solver-case-gen 的 scripts 目录（提供输入构造与 ratio 链）")
    ap.add_argument("--report", help="判定记录 JSON 输出路径")
    ap.add_argument("--rejudge", help="留证目录；只重判不连真机，其余参数忽略")

    src = ap.add_argument_group("输入来源")
    src.add_argument("--canonical", help="canonical_cases.json 路径（Cholesky 任务包）")
    src.add_argument("--probe-op", help="通路演练：现工程算子名（如 cmatinv_batched）")
    src.add_argument("--n", type=int, default=8, help="演练规格 n")
    src.add_argument("--batch", type=int, default=2, help="演练规格 batch")
    src.add_argument("--seed", type=int, default=923000001, help="演练输入 seed")
    src.add_argument("--ops", help="canonical 的算子子集，逗号分隔")
    src.add_argument("--max-n", type=int, default=None)
    src.add_argument("--limit", type=int, default=None)

    mach = ap.add_argument_group("真机参数")
    mach.add_argument("--repo", help="ops-solver 仓根（S1 已构建）")
    mach.add_argument("--ascend-home", help="缺省取 $ASCEND_HOME_PATH")
    mach.add_argument("--build-dir", default=None, help="缺省 <repo>/build")
    mach.add_argument("--executor", default=None,
                      help="已编译的执行件；缺省就地编译到 <work>/harness_exec")
    mach.add_argument("--work-dir", default="harness-work",
                      help="per-case 现场目录的根；本次运行落在 <root>/run-<时间戳>-<pid>")
    mach.add_argument("--evidence-dir", default="harness-evidence",
                      help="留证件根目录；本次运行落在 <root>/run-<时间戳>-<pid>")
    mach.add_argument("--provenance", default=None, help="S1 构建取证 JSON，进留证件")
    mach.add_argument("--device", type=int, default=0)
    mach.add_argument("--timeout", type=int, default=1800)
    mach.add_argument("--layout", default="row_major",
                      choices=("row_major", "column_major"),
                      help="片上布局口径（双口径分流点：现工程行主序，任务书列主序）")

    pol = ap.add_argument_group("判定与取证")
    pol.add_argument("--judge", default="auto",
                     choices=("auto", "criteria", "structural"))
    pol.add_argument("--rerun", type=int, default=1,
                     help="确定性复跑次数（≥2 启用；同子进程同 stream，每轮恢复输入）")
    pol.add_argument("--jobs", type=int, default=1, help="批量逐矩阵判定的分块数")
    pol.add_argument("--keep", action="append", default=[],
                     help="人工指定留证的 case_id（可重复）")
    args = ap.parse_args(argv)

    if args.rejudge:
        try:
            rec, code = rejudge(args.rejudge, args.gen_dir, args.jobs)
        except ContractError as exc:
            print(f"[{TOOL}] 重判输入错误: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(rec, ensure_ascii=False, indent=1, default=str))
        if args.report:
            exec_case.dump_json(args.report, {"tool": {"name": TOOL, "ver": TOOL_VER},
                                              "mode": "rejudge", "record": rec})
        return code

    try:
        if not args.report:
            raise ContractError("--report 必填（重判模式除外）")
        if bool(args.canonical) == bool(args.probe_op):
            raise ContractError("--canonical 与 --probe-op 二选一")
        if not args.repo:
            raise ContractError("--repo 必填：执行段要对该仓的公开头编译")
        ascend_home = args.ascend_home or os.environ.get("ASCEND_HOME_PATH")
        if not ascend_home:
            raise ContractError("--ascend-home 缺省取 $ASCEND_HOME_PATH，两者都没有")
        # 入口统一解析绝对路径：子进程的 CWD 是被测仓根，相对路径会从那里解析
        repo = str(exec_case.abspath(args.repo))
        ascend_home = str(exec_case.abspath(ascend_home))
        build_dir = str(exec_case.abspath(args.build_dir)) if args.build_dir else None
        mods = load_modules(args.gen_dir)

        ratio_mean = None
        if args.probe_op:
            op_abi.require_exec(args.probe_op)
            cases = [probe_case(mods, args.probe_op, args.n, args.batch, args.seed)]
        else:
            cases, ratio_mean = _pick_cases(mods, args.canonical, args.ops,
                                            args.max_n, args.limit)
            for c in cases:
                op_abi.require_exec(c["op"])

        # 每次运行一套独立目录：现场与留证件都带 run id，旧产物进不来（审计 #2）
        rid = run_id()
        work = exec_case.abspath(args.work_dir) / rid
        evidence_root = exec_case.abspath(args.evidence_dir) / rid
        executor = args.executor
        compile_rec = None
        if not executor:
            wanted = sorted({c["op"] for c in cases})
            compile_rec = exec_case.compile_executor(
                repo, ascend_home, work / "harness_exec",
                ops=wanted, build_dir=build_dir)
            executor = compile_rec["bin"]
        executor = str(exec_case.abspath(executor))
        provenance = (json.loads(
            exec_case.abspath(args.provenance).read_text(encoding="utf-8"))
            if args.provenance else None)
    except (ContractError, op_abi.AbiError, exec_case.ExecError, OSError, ValueError) as exc:
        print(f"[{TOOL}] 输入/契约错误: {exc}", file=sys.stderr)
        return 2

    ctx = {"executor": executor, "work_dir": work,
           "evidence_dir": evidence_root, "repo": repo,
           "ascend_home": ascend_home, "build_dir": build_dir,
           "device_id": args.device, "timeout": args.timeout,
           "cwd": repo, "rerun": args.rerun, "jobs": args.jobs,
           "layout": args.layout, "keep_ids": set(args.keep),
           "provenance": provenance, "ratio_mean": ratio_mean, "judge": None}

    records, t0 = [], time.time()
    n_ok = n_bad = n_err = n_det_fail = n_det_unknown = n_lib_bad = 0
    for case in cases:
        abi = op_abi.get(case["op"])
        ctx["judge"] = (args.judge if args.judge != "auto"
                        else ("criteria" if has_criteria_card(mods, case["op"])
                              else "structural"))
        try:
            rec = run_one(mods, case, abi, ctx)
        except Exception as exc:                       # 单 case 失败不吞整轮
            n_err += 1
            records.append({"case_id": case.get("case_id"), "op": case.get("op"),
                            "error": f"{type(exc).__name__}: {exc}"})
            continue
        records.append(rec)
        v = rec.get("verdict") or {}
        status = v.get("numeric") if ctx["judge"] == "criteria" else v.get("structural")
        if rec["exec"]["kind"] not in exec_case.EXECUTED_KINDS or v.get("error"):
            n_err += 1
        elif status in ("PASS",):
            n_ok += 1
        else:
            n_bad += 1
        det = rec.get("determinism")
        if det is not None:
            if det.get("consistent") is False:
                n_det_fail += 1
            elif det.get("consistent") is None:
                n_det_unknown += 1
        lib = (rec["exec"].get("loaded_lib") or {})
        if lib.get("match") is False:
            n_lib_bad += 1
            print(f"[{TOOL}] 警告 {rec['case_id']}: 实际加载库 {lib.get('path')} "
                  f"的摘要不在 S1 产物表内——{lib.get('note')}", file=sys.stderr)
        det_word = "-" if det is None else {True: "ok", False: "FAIL",
                                            None: "unknown"}[det["consistent"]]
        print(f"[{TOOL}] {rec['case_id']} exec={rec['exec']['kind']} "
              f"{ctx['judge']}={status} det={det_word}", file=sys.stderr)

    report = {
        "tool": {"name": TOOL, "ver": TOOL_VER},
        "run": {"run_id": rid, "work_dir": str(work),
                "evidence_dir": str(evidence_root)},
        "params": {k: v for k, v in vars(args).items() if k != "keep"},
        "keep": args.keep,
        "env": {"python": sys.version.split()[0], "numpy": np.__version__,
                "machine": platform.machine(), "platform": platform.platform()},
        "compile": compile_rec,
        "provenance": provenance,
        "summary": {"total": len(cases), "pass": n_ok, "fail": n_bad,
                    "error": n_err, "det_fail": n_det_fail,
                    "det_unknown": n_det_unknown, "lib_mismatch": n_lib_bad,
                    "wall_seconds": round(time.time() - t0, 1)},
        "formal": "PENDING_RULING",
        "judge_scope": "criteria 判定卡只覆盖 Cholesky 十算子；其余算子记结构性结论，"
                       "numeric=NOT_JUDGED，真判定等 Cholesky 交付后按交付头接入",
        "cases": records,
    }
    exec_case.dump_json(args.report, report)
    print(f"[{TOOL}] 完成 {len(cases)} case: pass={n_ok} fail={n_bad} err={n_err} "
          f"det_fail={n_det_fail} det_unknown={n_det_unknown} "
          f"lib_mismatch={n_lib_bad} → {args.report}")
    return 0 if (n_bad == 0 and n_err == 0 and n_det_fail == 0
                 and n_det_unknown == 0 and n_lib_bad == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
