#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_data_luqr.py —— LU/QR 四册包数据生成器（S1-LUQR，lq-plan §2.1）。

只消费 freeze_canonical.py 冻结的 `solver-luqr/canonical_cases@1` 中 s1_subset=true
的 case，逐 case 产出 `cases/<case_id>.npz` 与 `cases/index.json` 骨架；ratio_cpu/
ratio_cpu_status 留 null，由 fill_ratio_cpu 用 A 卡 residual_ratio 回填。

范围（1014 每册四算子，共 16 接口；Xlarft 无用例目录）：
- LU 实：sgetrf/sgetri/sgetribatched/smatinvbatched；复：c 前缀同形
- QR 实：sgeqrf/sorgqr/sormqr/sgels；复：c 前缀同形

输入构造（lq-plan §2.1，转写 0923 四册 bench .cu 的唯一填充 fillRandom）：
  A[i] = rand()%10000/100 - 50  → U[-50,50)（步长 0.01）；无对角占优/Hermitian 构造。
  复数每元素先 .x 后 .y 各一次独立 rand()%10000（实部偶数位、虚部奇数位）。
  声明差异：cu 二进制不可逐位复现；本脚本以 FP64 为母本（d/z 前缀 golden 需要），
  A32 由 A64 降型（装包自检断言 A32==A64.astype(f32)）。

golden（d/z 前缀链路）：
- getrf：dgetrf → 打包 LU + ipiv（因子非唯一，主判走还原 L·U vs A，见任务书 §3.2.1）
- getri：dgetrf 准备 (LU,ipiv)（DUT 输入），dgetri → 逆（唯一）
- getriBatched：批内逐矩阵 dgetrf 准备 LU/ipiv（DUT 输入），dgetri → 逆（唯一）
- matinvBatched：批内逐矩阵 dgetrf + dgetri → 逆（唯一）
- geqrf：dgeqrf → 打包 AF + tau（因子非唯一，主判走还原 Q·R vs A）
- sorgqr/cungqr：随机 (m,n) 阵 dgeqrf 准备 (AF,tau)，输出 Q=dorgqr(m,n,k,AF,tau[:k])
- sormqr/cunmqr：随机 (nq,k) 阵 dgeqrf 准备 (AF,tau)，Q=dormqr(side,trans,AF,tau,C)
  （nq = m if side=LEFT else n；附显式 Q(orgqr) 供 DQRT03 兜底）
- gels：dgels → 最小二乘解 X（取 B_out 前 n 行，唯一解）

A0 抽样（HT-2）：四批量算子逐 case 只造 k=min(5,batch) 代表内容（复用
gen_data_cholesky 的 derive_sample_map/expand_sampled_rows），slot 映射固化进 index。

内存约束（LQ-14 / 假定 A-4）：`validate_case` 对每条 case 做 fail-closed 断言
`case_input_bytes(op, case) ≤ 4GiB`（与 S1 冻结侧裁剪同模型）。正常流经裁剪后
canonical 不触发；从旧/未裁剪 canonical 重生成时即报错。

CLI：
    gen_data_luqr.py --canonical <json> --out <dir> [--ops a,b,...] [--select s1|all]
                     [--criteria <dir>]
产出：单算子 <out>/cases/<case_id>.npz；批量 case 只产 index 条目（A0 不落数组）。

与 gen_data_cholesky.py 的关系：复用其 A0 抽样机制与契约异常；数据面（填充函数、
golden 链、数组集）全部独立实现——Cholesky 三册通路逐字节不受影响。
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

GENERATOR = "gen_data_luqr.py"
GENERATOR_VER = "lq-b2"  # lq-b2：LQ-14 生成侧 4GiB 内存约束断言（假定 A-4）
INDEX_SCHEMA = "solver-luqr/cases-index@1"

REAL_LU_OPS = ("sgetrf", "sgetri", "sgetribatched", "smatinvbatched")
COMPLEX_LU_OPS = ("cgetrf", "cgetri", "cgetribatched", "cmatinvbatched")
REAL_QR_OPS = ("sgeqrf", "sorgqr", "sormqr", "sgels")
COMPLEX_QR_OPS = ("cgeqrf", "cungqr", "cunmqr", "cgels")
LU_OPS = REAL_LU_OPS + COMPLEX_LU_OPS
QR_OPS = REAL_QR_OPS + COMPLEX_QR_OPS
BATCHED_OPS = ("sgetribatched", "cgetribatched", "smatinvbatched", "cmatinvbatched")
SINGLE_OPS = tuple(o for o in (LU_OPS + QR_OPS) if o not in BATCHED_OPS)
ALL_OPS = SINGLE_OPS + BATCHED_OPS
COMPLEX_OPS = COMPLEX_LU_OPS + COMPLEX_QR_OPS

F32 = np.float32
F64 = np.float64
C64 = np.complex64
C128 = np.complex128
TRANS_INT = {"N": 0, "T": 1, "C": 2}

# 单 case 主输入数组内存约束（任务书「输入数据占用内存 ≤ 4G」，假定 A-4）。生成侧
# fail-closed 断言（LQ-14）：与 S1 冻结侧裁剪（freeze_canonical._luqr_refresh_grid）
# 同模型（只估矩阵本体，不含 workspace/golden；实数 4B / 复数 8B），保证正常流经
# canonical 的 case 不会被误拒；仅当从旧/未裁剪 canonical 重生成时触发。
FOUR_GIB = 4 * (1024 ** 3)
_LUQR_ITEM_BYTES = {False: 4, True: 8}


# ---------------------------------------------------------------------------
# 包内基础契约原语（自包含：装包时本文件被复制为 gen_data.py，不再 import 兄弟件）
# ---------------------------------------------------------------------------

class ContractError(RuntimeError):
    """契约/前置错误（装包自检与现场重生成共用）。"""


def _assert_finite(arr, name, case_id):
    """spec §2.2 装包自检项：数组不得含 NaN/Inf。"""
    if not np.isfinite(arr).all():
        raise ContractError(f"{case_id}: {name} 含 NaN/Inf")


def _assert_cast(a32, a64, name, case_id, dt32=F32):
    """装包自检项：A32 == A64.astype(降型 dtype) 必须成立（B/golden 同理）。"""
    if a32.dtype != dt32 or a32.tobytes() != a64.astype(dt32).tobytes():
        raise ContractError(
            f"{case_id}: {name}32 != {name}64.astype({np.dtype(dt32).name})（自检字段）")


A0_MAX_CONTENTS = 5


def sampled_content_count(batch):
    """A0 代表内容数：k = min(5, batchSize)（HT-2）。"""
    return min(A0_MAX_CONTENTS, batch)


def derive_sample_map(seed, batch, k=None):
    """A0 抽样映射（HT-2，与 gen_data_cholesky 同法同种子域）：

    - 摆放流：SeedSequence(entropy=seed, spawn_key=(1,)) → permutation(batch) 取前
      k 个槽位升序排列，第 c 个升序槽位即内容 c 的代表槽 rep_slot；
    - 填充流：SeedSequence(entropy=seed, spawn_key=(2,)) → 剩余槽位按升序逐一抽
      integers(0, k) 指派内容（无剩余槽时不消费该流）。

    返回列表形态 [{"content_idx","rep_slot","slots"}]（JSON round-trip 安全）。
    不变量在此断言：槽位恰覆盖一次、rep_slot 是 slots 首元素、每内容至少一槽。"""
    if k is None:
        k = sampled_content_count(batch)
    if not 1 <= k <= batch:
        raise ContractError(f"A0 抽样：k={k} 越界（1 ≤ k ≤ batch={batch}）")
    place_rng = np.random.default_rng(
        np.random.SeedSequence(entropy=seed, spawn_key=(1,)))
    rep_slots = np.sort(place_rng.permutation(batch)[:k])
    occupied = {int(s) for s in rep_slots}
    rest = [s for s in range(batch) if s not in occupied]
    fill_rng = np.random.default_rng(
        np.random.SeedSequence(entropy=seed, spawn_key=(2,)))
    assigns = (fill_rng.integers(0, k, size=len(rest)) if rest
               else np.empty(0, dtype=np.int64))
    slots = [[int(s)] for s in rep_slots]
    for slot, c in zip(rest, assigns):
        slots[int(c)].append(int(slot))
    sample_map = [{"content_idx": c, "rep_slot": slots[c][0], "slots": slots[c]}
                  for c in range(k)]
    covered = sorted(s for e in sample_map for s in e["slots"])
    if covered != list(range(batch)):
        raise ContractError(
            f"A0 抽样不变量破坏：槽位覆盖不符（seed={seed}, batch={batch}）")
    for e in sample_map:
        if not e["slots"] or e["slots"][0] != e["rep_slot"]:
            raise ContractError(
                f"A0 抽样不变量破坏：内容 {e['content_idx']} 的 rep_slot 不是首槽")
    return sample_map


def expand_sampled_rows(contents, sample_map, lo, hi):
    """按 sample_map 把 k 个内容复制到批维槽位区间 [lo, hi)（HT-2）。返回与
    contents 同键的数组，批维为 hi-lo。区间内每槽必须恰有一次指派，缺即报错。"""
    if not (0 <= lo <= hi):
        raise ContractError(f"A0 展开：非法区间 [{lo},{hi})")
    out = {name: np.empty((hi - lo,) + arr.shape[1:], dtype=arr.dtype)
           for name, arr in contents.items()}
    hit = 0
    for entry in sample_map:
        c = entry["content_idx"]
        for s in entry["slots"]:
            if lo <= s < hi:
                for name in out:
                    out[name][s - lo] = contents[name][c]
                hit += 1
    if hit != hi - lo:
        raise ContractError(
            f"A0 展开：区间 [{lo},{hi}) 有 {hi - lo - hit} 个槽位无内容指派")
    return out


def op_kinds(op):
    """按算子名解析数值域：返回 (母本 dtype, 降型 dtype, golden LAPACK 前缀)。"""
    if op in COMPLEX_OPS:
        return C128, C64, "z"
    return F64, F32, "d"


def _lapack():
    from scipy.linalg import lapack
    return lapack


# ---------------------------------------------------------------------------
# 输入构造（转写唯一填充 fillRandom；见模块 docstring）
# ---------------------------------------------------------------------------

def fill_random(rng, count, cx):
    """fillRandom 转写：count 个元素先实后复。实数一次 rand()%10000；
    复数每元素 .x 先 .y 后各一次（实部偶数位、虚部奇数位）。返回值域 U[-50,50)。"""
    if cx:
        d = rng.integers(0, 10000, size=2 * count).astype(F64) / 100.0 - 50.0
        return d[0::2] + 1j * d[1::2]
    return rng.integers(0, 10000, size=count).astype(F64) / 100.0 - 50.0


def rand_matrix(rng, shape, cx):
    """列主序（LAPACK lda×n 缓冲）填充随机矩阵（order='F' 对齐 cu 遍历序）。"""
    return fill_random(rng, int(np.prod(shape)), cx).reshape(shape, order="F")


# ---------------------------------------------------------------------------
# golden 链（d/z 前缀 LAPACK；scipy.linalg.lapack）
# ---------------------------------------------------------------------------

def golden_getrf(A, prefix, cid):
    """打包 LU + ipiv（1 基，返回 int32）。奇异（info>0）在 precision case 属构造错。"""
    lu, ipiv, info = getattr(_lapack(), prefix + "getrf")(A, overwrite_a=0)
    if info != 0:
        raise ContractError(f"{cid}: {prefix}getrf info={info}（precision case 应为 0）")
    return np.ascontiguousarray(lu), np.asarray(ipiv, dtype=np.int32) + 1


def golden_inv(A, prefix, cid):
    """d/z 前缀 getrf + getri → 逆（唯一）。"""
    lap = _lapack()
    lu, ipiv, info = getattr(lap, prefix + "getrf")(A, overwrite_a=0)
    if info != 0:
        raise ContractError(f"{cid}: {prefix}getrf info={info}（precision case 应为 0）")
    inv, info2 = getattr(lap, prefix + "getri")(lu, ipiv)
    if info2 != 0:
        raise ContractError(f"{cid}: {prefix}getri info={info2}（precision case 应为 0）")
    return np.ascontiguousarray(inv)


def golden_inv_from_lu(lu, ipiv, prefix, cid):
    """由已备好的打包 LU + ipiv（1 基入参需转 0 基）→ 逆（getriBatched 用）。"""
    inv, info = getattr(_lapack(), prefix + "getri")(lu, np.asarray(ipiv, dtype=np.int32) - 1)
    if info != 0:
        raise ContractError(f"{cid}: {prefix}getri info={info}（precision case 应为 0）")
    return np.ascontiguousarray(inv)


def golden_geqrf_pack(A, prefix, cid):
    """d/z 前缀 geqrf → (打包 AF, tau)（因子非唯一，作参考/兜底 Q 构造源）。"""
    qr, tau, _work, info = getattr(_lapack(), prefix + "geqrf")(A, overwrite_a=0)
    if info != 0:
        raise ContractError(f"{cid}: {prefix}geqrf info={info}")
    return (np.ascontiguousarray(qr), np.ascontiguousarray(tau.astype(op_float64(prefix))))


def op_float64(prefix):
    return F64 if prefix == "d" else C128


def golden_gqr(AF, tau, prefix, cid):
    """dorgqr/zungqr：由打包反射子 (AF) 与 tau 生成 m×n 正交/酉矩阵 Q。"""
    q, _work, info = getattr(_lapack(), prefix + "orgqr" if prefix == "d" else prefix + "ungqr")(
        np.array(AF, order="F"), tau)
    if info != 0:
        raise ContractError(f"{cid}: {prefix}orgqr/ungqr info={info}")
    return np.ascontiguousarray(q)


def golden_mqr(AF, tau, C, side, trans, prefix, cid):
    """dormqr/zunmqr：op(Q)·C（或 C·op(Q)）。"""
    fn = getattr(_lapack(), prefix + "ormqr" if prefix == "d" else prefix + "unmqr")
    nq, k = AF.shape
    lwork = max(1, 64 * max(nq, C.shape[0], C.shape[1], k))
    out, _work, info = fn(side, trans, np.array(AF, order="F"), tau,
                          np.array(C, order="F"), lwork=lwork, overwrite_c=0)
    if info != 0:
        raise ContractError(f"{cid}: {prefix}ormqr/unmqr info={info}")
    return np.ascontiguousarray(out)


def golden_gels(A, B, prefix, cid):
    """dgels/zgels → 最小二乘解 X（B_out 前 n 行；m>=n）。"""
    _a, b_out, info = getattr(_lapack(), prefix + "gels")(
        A, B, trans="N", overwrite_a=0, overwrite_b=0)
    if info != 0:
        raise ContractError(f"{cid}: {prefix}gels info={info}（precision case 应为 0）")
    n = A.shape[1]
    return np.ascontiguousarray(b_out[:n, :])


def explicit_q_mm(AF, tau, nq, prefix, cid):
    """由 (AF,tau) 显式生成 nq×nq 正交/酉矩阵 Q（DQRT01/03 兜底口径：升精度构造）。"""
    pad = np.zeros((nq, nq), dtype=AF.dtype)
    k = min(AF.shape[1], nq)
    pad[:, :k] = AF[:, :k]
    return golden_gqr(pad, tau, prefix, cid)


# ---------------------------------------------------------------------------
# 单算子数组集
# ---------------------------------------------------------------------------

def _pack(a64, dt32, cid, name="A"):
    _assert_finite(a64, name + "64", cid)
    a32 = np.ascontiguousarray(a64.astype(dt32))
    _assert_cast(a32, a64, name, cid, dt32)
    return a64, a32


def _arrays_getrf(case):
    cid, op = case["case_id"], case["op"]
    _dt64, dt32, _prefix = op_kinds(op)
    cx = op in COMPLEX_OPS
    rng = np.random.default_rng(case["seed"])
    A64 = rand_matrix(rng, (case["m"], case["n"]), cx)
    A64, A32 = _pack(A64, dt32, cid)
    return {"A64": A64, "A32": A32}


def _arrays_getri(case):
    cid, op = case["case_id"], case["op"]
    _dt64, dt32, prefix = op_kinds(op)
    cx = op in COMPLEX_OPS
    n = case["n"]
    rng = np.random.default_rng(case["seed"])
    A64 = rand_matrix(rng, (n, n), cx)
    A64, A32 = _pack(A64, dt32, cid)
    lu64, ipiv = golden_getrf(A64, prefix, cid)      # DUT 输入：打包 LU + 1 基 ipiv
    g = golden_inv_from_lu(lu64, ipiv, prefix, cid)
    lu64, lu32 = _pack(lu64, dt32, cid, "LU")
    g64, g32 = _pack(g, dt32, cid, "golden")
    return {"A64": A64, "A32": A32, "LU64": lu64, "LU32": lu32,
            "ipiv": ipiv, "golden64": g64, "golden32": g32}


def _arrays_matinvbatched(case):
    cid, op = case["case_id"], case["op"]
    dt64, dt32, prefix = op_kinds(op)
    cx = op in COMPLEX_OPS
    n, k = case["n"], sampled_content_count(case["batch"])
    rng = np.random.default_rng(case["seed"])
    A64 = np.empty((k, n, n), dtype=dt64)
    for i in range(k):
        A64[i] = rand_matrix(rng, (n, n), cx)
    A64, A32 = _pack(A64, dt32, cid)
    g = np.empty((k, n, n), dtype=dt64)
    for i in range(k):
        g[i] = golden_inv(A64[i], prefix, f"{cid}#c{i}")
    g64, g32 = _pack(g, dt32, cid, "golden")
    return {"A64": A64, "A32": A32, "golden64": g64, "golden32": g32}


def _arrays_getribatched(case):
    cid, op = case["case_id"], case["op"]
    dt64, dt32, prefix = op_kinds(op)
    cx = op in COMPLEX_OPS
    n, k = case["n"], sampled_content_count(case["batch"])
    rng = np.random.default_rng(case["seed"])
    A64 = np.empty((k, n, n), dtype=dt64)
    for i in range(k):
        A64[i] = rand_matrix(rng, (n, n), cx)
    A64, A32 = _pack(A64, dt32, cid)
    lu = np.empty((k, n, n), dtype=dt64)
    ipiv = np.empty((k, n), dtype=np.int32)
    g = np.empty((k, n, n), dtype=dt64)
    for i in range(k):
        lu[i], ipiv[i] = golden_getrf(A64[i], prefix, f"{cid}#c{i}")
        g[i] = golden_inv_from_lu(lu[i], ipiv[i], prefix, f"{cid}#c{i}")
    lu64, lu32 = _pack(lu, dt32, cid, "LU")
    g64, g32 = _pack(g, dt32, cid, "golden")
    return {"A64": A64, "A32": A32, "LU64": lu64, "LU32": lu32,
            "ipiv": ipiv, "golden64": g64, "golden32": g32}


def _arrays_geqrf(case):
    cid, op = case["case_id"], case["op"]
    _dt64, dt32, prefix = op_kinds(op)
    cx = op in COMPLEX_OPS
    m, n = case["m"], case["n"]
    rng = np.random.default_rng(case["seed"])
    A64 = rand_matrix(rng, (m, n), cx)
    A64, A32 = _pack(A64, dt32, cid)
    af, tau = golden_geqrf_pack(A64, prefix, cid)
    af64, af32 = _pack(af, dt32, cid, "golden")
    tau32 = np.ascontiguousarray(tau.astype(dt32))
    return {"A64": A64, "A32": A32, "golden64": af64, "golden32": af32,
            "tau64": np.ascontiguousarray(tau), "tau32": tau32}


def _arrays_gqr(case):
    cid, op = case["case_id"], case["op"]
    _dt64, dt32, prefix = op_kinds(op)
    cx = op in COMPLEX_OPS
    m, n, k = case["m"], case["n"], case["k"]
    if not (0 <= k <= min(m, n)):
        raise ContractError(f"{cid}: k={k} 越界（0<=k<=min(m,n)={min(m, n)}）")
    rng = np.random.default_rng(case["seed"])
    M = rand_matrix(rng, (m, n), cx)
    af, tau = golden_geqrf_pack(M, prefix, cid)
    af_in = np.ascontiguousarray(af)
    tau_k = np.ascontiguousarray(tau[:k])
    af64, af32 = _pack(af_in, dt32, cid, "A")
    tau32 = np.ascontiguousarray(tau_k.astype(dt32))
    g = golden_gqr(af_in, tau_k, prefix, cid)
    g64, g32 = _pack(g, dt32, cid, "golden")
    return {"A64": af64, "A32": af32, "tau64": np.ascontiguousarray(tau_k), "tau32": tau32,
            "golden64": g64, "golden32": g32}


def _arrays_mqr(case):
    cid, op = case["case_id"], case["op"]
    _dt64, dt32, prefix = op_kinds(op)
    cx = op in COMPLEX_OPS
    m, n, k, side, trans = case["m"], case["n"], case["k"], case["side"], case["trans"]
    nq = m if side == "LEFT" else n
    if not (0 <= k <= nq):
        raise ContractError(f"{cid}: k={k} 越界（0<=k<=nq={nq}，side={side}）")
    rng = np.random.default_rng(case["seed"])
    M = rand_matrix(rng, (nq, k), cx)
    af, tau = golden_geqrf_pack(M, prefix, cid)
    C64 = rand_matrix(rng, (m, n), cx)
    af64, af32 = _pack(af, dt32, cid, "A")
    C64, C32 = _pack(C64, dt32, cid, "C")
    tau32 = np.ascontiguousarray(tau.astype(dt32))
    q = explicit_q_mm(af64, tau, nq, prefix, cid)
    g = golden_mqr(af64, tau, C64, side, trans, prefix, cid)
    g64, g32 = _pack(g, dt32, cid, "golden")
    return {"A64": af64, "A32": af32, "tau64": np.ascontiguousarray(tau), "tau32": tau32,
            "C64": C64, "C32": C32, "q64": q,
            "q32": np.ascontiguousarray(q.astype(dt32)),
            "golden64": g64, "golden32": g32}


def _arrays_gels(case):
    cid, op = case["case_id"], case["op"]
    _dt64, dt32, prefix = op_kinds(op)
    cx = op in COMPLEX_OPS
    m, n, nrhs = case["m"], case["n"], case["nrhs"]
    rng = np.random.default_rng(case["seed"])
    A64 = rand_matrix(rng, (m, n), cx)
    B64 = rand_matrix(rng, (m, nrhs), cx)
    A64, A32 = _pack(A64, dt32, cid, "A")
    B64, B32 = _pack(B64, dt32, cid, "B")
    g = golden_gels(A64, B64, prefix, cid)
    g64, g32 = _pack(g, dt32, cid, "golden")
    return {"A64": A64, "A32": A32, "B64": B64, "B32": B32,
            "golden64": g64, "golden32": g32}


_DISPATCH = (
    ("getribatched", _arrays_getribatched),
    ("matinvbatched", _arrays_matinvbatched),
    ("getrf", _arrays_getrf),
    ("getri", _arrays_getri),
    ("geqrf", _arrays_geqrf),
    ("gqr", _arrays_gqr),
    ("mqr", _arrays_mqr),
    ("gels", _arrays_gels),
)


def build_case_arrays(case):
    """按 case 构造数组集（同时是 verify 现场重生成入口：validate_case +
    build_case_arrays）。批量 case 走 A0 内容数组（批维 k=min(5,batch)）。"""
    op = case["op"]
    if op in BATCHED_OPS:
        return _build_batched_arrays(case)
    for suffix, fn in _DISPATCH:
        if op.endswith(suffix):
            return fn(case)
    raise ContractError(f"{case.get('case_id')}: op={op!r} 不在 {ALL_OPS}")


def _build_batched_arrays(case):
    op = case["op"]
    for suffix, fn in _DISPATCH:
        if op.endswith(suffix):
            return fn(case)
    raise ContractError(f"{case.get('case_id')}: op={op!r} 不在批量算子集")


def build_batched_contents(case):
    """A0 内容数组入口（render_verify 现场重生成协议）：批量 case 的 k=min(5,batch)
    个代表内容数组 + 逐内容 golden（键集随算子：getriBatched 含 A/LU/ipiv，
    matinvBatched 含 A）。不落 npz（HT-2），现场构造现场使用。"""
    if case["op"] not in BATCHED_OPS:
        raise ContractError(f"{case.get('case_id')}: op={case['op']!r} 非批量算子")
    return _build_batched_arrays(case)


# ---------------------------------------------------------------------------
# info 契约用例（HT-8/HT-9：LU/QR 正值 info 出现在 getrf/getri 与批量两族；QR 仅参数错）
# ---------------------------------------------------------------------------

# 单算子 info 探针（与任务书 §2.4.2/§3.2 对齐）：
#   getrf       → singular_pivot（构造 U(k,k)=0 的奇异输入，info=k 正值）
#   getri       → singular_lu（输入 LU 的第 k 阶 U(k,k)=0，info=k 正值；值域 0/-i/i）
#   QR 四算子   → bad_param（非法枚举，info=-1；gqr/mqr 实/复拼写不同：orgqr/ungqr、
#                 ormqr/unmqr，两者的基础名都要登记）
#   getriBatched/matinvBatched（批量）→ singular_matrix（逐内容奇异，info[i]=k 列表）
INFO_PROBE_OF = {
    "getrf": "singular_pivot",
    "getri": "singular_lu",
    "geqrf": "bad_param",
    "orgqr": "bad_param",
    "ungqr": "bad_param",
    "ormqr": "bad_param",
    "unmqr": "bad_param",
    "gels": "bad_param",
    "getribatched": "singular_matrix",
    "matinvbatched": "singular_matrix",
}
INFO_CASES_PER_OP = 3
BATCHED_INFO_CASES_PER_OP = 1


def _base_name(op):
    """去实/复前缀：sgetrf→getrf、cgeqrf→geqrf、sorgqr→orgqr。"""
    return op[1:] if op[:1] in ("s", "c") else op


def _info_probe_of(op):
    base = _base_name(op)
    if base not in INFO_PROBE_OF:
        raise ContractError(f"{op}: 无 info 探针登记（{sorted(INFO_PROBE_OF)}）")
    return INFO_PROBE_OF[base]


def _info_k_list(n, count):
    """按变体序取 [1, n//2, n] 中合法 k（去重），循环取 count 个（H8 同法）。"""
    ks = sorted({kk for kk in (1, n // 2, n) if 1 <= kk <= n})
    return [ks[j % len(ks)] for j in range(count)]


def derive_info_cases(cases, require_s1=True):
    """HT-8 用例侧：每单算子从其派生基座取 INFO_CASES_PER_OP 个 info 契约变体。

    选择规则（确定性，随 index 交付供对账）：该算子基座按 (n, case_id) 升序，跳过
    最小例后取随后至多 INFO_CASES_PER_OP 例；正值的 k 按变体序取 [1,n//2,n]。
    基座：require_s1=True = s1 子集；False = 全部精度 case（info 为契约抽验，条数恒定）。
    产出条目只含描述字段（数组由 build_info_arrays 现场构造，纯脚本同策）。"""
    by_op = {}
    for c in cases:
        if c.get("op") not in SINGLE_OPS:
            continue
        if require_s1 and c.get("s1_subset") is not True:
            continue
        if c.get("case_purpose") == "info":
            continue
        by_op.setdefault(c["op"], []).append(c)
    out = []
    for op in sorted(by_op):
        lst = sorted(by_op[op], key=lambda c: (c["n"], c["case_id"]))
        picks = lst[1:1 + INFO_CASES_PER_OP] or lst[:INFO_CASES_PER_OP]
        probe = _info_probe_of(op)
        for j, base in enumerate(picks):
            if probe == "singular_pivot":
                nk = min(base["m"], base["n"])
                k_exp = _info_k_list(nk, INFO_CASES_PER_OP)[j]
            elif probe == "singular_lu":
                nk = base["n"]
                k_exp = _info_k_list(nk, INFO_CASES_PER_OP)[j]
            else:
                k_exp = -1
            entry = {k: v for k, v in base.items() if k != "s1_subset"}
            entry.update({
                "case_id": f"{base['case_id']}-info{j + 1}",
                "base_case_id": base["case_id"],
                "case_purpose": "info", "info_probe": probe,
                "k_expected": k_exp,
            })
            out.append(entry)
    return out


def build_info_arrays(entry, base_arrays):
    """HT-8：单算子 info 用例数组（只比 info，**不产 golden**）。构造性自检
    （fail-closed）：FP64 d/z 前缀链路当场验证目标 info==k_expected，不成立即
    ContractError。k_expected 在构造后回写 entry（正值探针的 k 由 build 侧定）。

    - singular_pivot（getrf）：base 的 (m,n) 上构造随机上三角阵（下三角置 0），
      第 k 阶对角置 0 → {d|z}getrf 恰在第 k 步零主元，info=k；k 取 min(m,n) 内的
      k_expected（entry 给出，越界即报错）。
    - singular_lu（getri）：base 的 LU 因子第 k 阶对角（U(k,k)）置 0 → {d|z}getri
      报 info=k；k 取 n 内的 k_expected（entry 给出，越界即报错）。DUT 输入
      LU32/ipiv，另带 A64/A32（info 用例数组面，sim/stream 读 A32 定形状）。
    - bad_param（QR 四算子）：base 数组原样（参数错与矩阵无关），info=-1。
    """
    cid, op = entry["case_id"], entry["op"]
    _, dt32, gprefix = op_kinds(op)
    probe = entry["info_probe"]
    lapack = _lapack()
    if probe == "singular_pivot":
        A64 = np.array(base_arrays["A64"], copy=True)
        m, n = A64.shape
        nk = min(m, n)
        k = int(entry.get("k_expected") or 0)
        if not 1 <= k <= nk:
            raise ContractError(f"{cid}: singular_pivot 的 k_expected={k} 越界（1≤k≤{nk}）")
        lower = np.tril(np.ones((m, n), dtype=bool), -1)   # 严格下三角
        A64[lower] = 0.0
        for j in range(nk):                                 # 前 k-1 阶对角保证非零
            if A64[j, j] == 0:
                A64[j, j] = 1.0
        A64[k - 1, k - 1] = 0.0                             # 第 k 阶零主元
        _lu, _ipiv, info = getattr(lapack, gprefix + "getrf")(A64.copy())
        if info != k:
            raise ContractError(
                f"{cid}: 奇异主元构造自检失败：{gprefix}getrf info={info} != k={k}"
                f"（上三角第 {k} 阶对角置 0 应恰报 {k}）")
        entry["k_expected"] = k
        arrays = {"A64": np.ascontiguousarray(A64), "A32": A64.astype(dt32)}
    elif probe == "singular_lu":
        lu64 = np.array(base_arrays["LU64"], copy=True)
        ipiv = np.asarray(base_arrays["ipiv"]).copy()
        n = lu64.shape[0]
        k = int(entry.get("k_expected") or 0)
        if not 1 <= k <= n:
            raise ContractError(f"{cid}: singular_lu 的 k_expected={k} 越界（1≤k≤{n}）")
        lu64[k - 1, k - 1] = 0.0                            # U(k,k)=0 → getri info=k
        _inv, info = getattr(lapack, gprefix + "getri")(
            np.array(lu64, order="F"), np.asarray(ipiv, dtype=np.int32) - 1)
        if info != k:
            raise ContractError(
                f"{cid}: 奇异 LU 构造自检失败：{gprefix}getri info={info} != k={k}"
                f"（LU 第 {k} 阶对角置 0 应恰报 {k}）")
        entry["k_expected"] = k
        A64 = np.ascontiguousarray(base_arrays["A64"])
        arrays = {"A64": A64, "A32": A64.astype(dt32),
                  "LU64": np.ascontiguousarray(lu64),
                  "LU32": np.ascontiguousarray(lu64.astype(dt32)), "ipiv": ipiv}
    elif probe == "bad_param":
        arrays = {kk: base_arrays[kk] for kk in ("A64", "A32", "B64", "B32")
                  if kk in base_arrays}
    else:
        raise ContractError(f"{cid}: 未知单矩阵 info_probe: {probe!r}")
    for name in arrays:
        _assert_finite(arrays[name], name, cid)
        if name.endswith("32"):
            _assert_cast(arrays[name], arrays[name[:-2] + "64"], name[0], cid, dt32)
    return arrays


def derive_batched_info_cases(cases, require_s1=True):
    """HT-9 用例侧：每批量算子从派生基座取 BATCHED_INFO_CASES_PER_OP 个 info 混合 case。

    选择规则：该算子基座按 (n, case_id) 升序，跳过最小例取第 2 例。A0 抽样下
    「1~2 个矩阵奇异」按「1~2 个代表内容」解释：
    - *getriBatched / *matinvBatched（infoArray 分型）：代表内容 0 与 1（k<2 时仅
      内容 0）构造奇异（第 k 阶零主元，k 取 [1, n//2] 去重，构造时即知），其余内容
      保持非奇异——k_expected 为逐内容 k 值列表（非奇异内容记 0），验证 infoArray
      逐矩阵独立写入。产出条目只含描述字段，数组由 build_batched_info_arrays 现场造。
    """
    by_op = {}
    for c in cases:
        if c.get("op") not in BATCHED_OPS:
            continue
        if require_s1 and c.get("s1_subset") is not True:
            continue
        if c.get("case_purpose") == "info":
            continue
        by_op.setdefault(c["op"], []).append(c)
    out = []
    for op in sorted(by_op):
        lst = sorted(by_op[op], key=lambda c: (c["n"], c["case_id"]))
        base = lst[1] if len(lst) > 1 else lst[0]
        entry = {k: v for k, v in base.items() if k != "s1_subset"}
        entry.update({
            "case_id": f"{base['case_id']}-info1",
            "base_case_id": base["case_id"],
            "case_purpose": "info", "info_probe": _info_probe_of(op),
        })
        k_contents = sampled_content_count(base["batch"])
        n = base["n"]
        ks = sorted({kk for kk in (1, n // 2) if 1 <= kk <= n})
        k_list = [ks[c % len(ks)] for c in range(min(2, k_contents))]
        k_list += [0] * (k_contents - len(k_list))
        entry["k_expected"] = k_list
        out.append(entry)
    return out


def build_batched_info_arrays(entry):
    """HT-9：批量 info 契约数组（只比 info，**不产 golden**）。返回输入面
    {A64,A32[,LU64,LU32,ipiv]}，批维 k（代表内容）。构造性自检（fail-closed）：

    - singular_matrix（getriBatched）：逐内容 {d|z}getrf 出打包 LU+ipiv，对
      k_expected[c]>0 的内容把 LU 第 k 阶对角（U(k,k)）置 0，{d|z}getri 当场验证
      info==k；非奇异内容维持 getrf 输出并验证 getri info==0。
    - singular_matrix（matinvBatched）：输入原始 A；对奇异内容构造 U(k,k)=0 的
      上三角阵（下三角置 0）→ {d|z}getrf 当场验证 info==k；非奇异内容验证 info==0。
    """
    cid, op = entry["case_id"], entry["op"]
    base_op = _base_name(op)
    dt64, dt32, gprefix = op_kinds(op)
    cx = op in COMPLEX_OPS
    n, k = entry["n"], sampled_content_count(entry["batch"])
    k_expected = [int(x) for x in entry["k_expected"]]
    if len(k_expected) != k:
        raise ContractError(f"{cid}: k_expected 应为 k={k} 值列表，得 {len(k_expected)}")
    rng = np.random.default_rng(entry["seed"])
    lapack = _lapack()
    A64 = np.empty((k, n, n), dtype=dt64)
    for i in range(k):
        A64[i] = rand_matrix(rng, (n, n), cx)
    _assert_finite(A64, "A64", cid)
    lu64 = np.empty((k, n, n), dtype=dt64)
    ipiv = np.empty((k, n), dtype=np.int32)
    if base_op == "getribatched":
        for c in range(k):
            lu64[c], ipiv[c] = golden_getrf(A64[c], gprefix, f"{cid}#c{c}")
            kk = k_expected[c]
            if not 0 <= kk <= n:
                raise ContractError(f"{cid}: 内容 {c} 的 k_expected={kk} 越界（n={n}）")
            if kk > 0:
                lu64[c][kk - 1, kk - 1] = 0.0            # U(kk,kk)=0 → getri info=kk
            _inv, info = getattr(lapack, gprefix + "getri")(
                np.array(lu64[c], order="F"), np.asarray(ipiv[c], dtype=np.int32) - 1)
            if info != kk:
                raise ContractError(
                    f"{cid}: 内容 {c} 奇异构造自检失败：{gprefix}getri info={info} "
                    f"!= k_expected={kk}（LU 第 {kk} 阶对角置 0）")
        lu32 = np.ascontiguousarray(lu64.astype(dt32))
        _assert_cast(lu32, lu64, "LU", cid, dt32)
        arrays = {"A64": np.ascontiguousarray(A64), "A32": A64.astype(dt32),
                  "LU64": np.ascontiguousarray(lu64), "LU32": lu32, "ipiv": ipiv}
    else:
        for c in range(k):
            kk = k_expected[c]
            if not 0 <= kk <= n:
                raise ContractError(f"{cid}: 内容 {c} 的 k_expected={kk} 越界（n={n}）")
            if kk > 0:
                lower = np.tril(np.ones((n, n), dtype=bool), -1)
                A64[c][lower] = 0.0                      # 上三角化
                A64[c][kk - 1, kk - 1] = 0.0
            _lu, _ipiv, info = getattr(lapack, gprefix + "getrf")(
                np.array(A64[c], order="F"))
            if info != kk:
                raise ContractError(
                    f"{cid}: 内容 {c} 奇异构造自检失败：{gprefix}getrf info={info} "
                    f"!= k_expected={kk}")
        arrays = {"A64": np.ascontiguousarray(A64), "A32": A64.astype(dt32)}
    for name in arrays:
        _assert_finite(arrays[name], name, cid)
    _assert_cast(arrays["A32"], arrays["A64"], "A", cid, dt32)
    return arrays


# ---------------------------------------------------------------------------
# 进场校验
# ---------------------------------------------------------------------------

def _require_int(case, key):
    v = case.get(key)
    if not isinstance(v, int) or v < 0:
        raise ContractError(f"{case.get('case_id')}: {key}={v!r} 非法（应为非负整数）")
    return v


def case_input_bytes(op, case):
    """单 case 主输入数组字节数下界（LQ-14 / 假定 A-4）。字段缺失按 0/缺省计；
    与 `freeze_canonical._luqr_case_bytes` 同模型（只估矩阵本体，不含 workspace/golden）。"""
    item = _LUQR_ITEM_BYTES[op in COMPLEX_OPS]
    base = _base_name(op)
    m, n = case.get("m") or 0, case.get("n") or 0
    lda = case.get("lda") or m or n
    batch = case.get("batch") or 1
    if base in ("getrf", "geqrf"):
        return lda * n * item
    if base == "getri":
        return lda * n * item
    if base in ("getribatched", "matinvbatched"):
        return lda * n * item * batch
    if base in ("orgqr", "ungqr"):
        return lda * (case.get("k") or n) * item
    if base in ("ormqr", "unmqr"):
        return (lda * (case.get("k") or n) + case.get("ldc", m) * n) * item
    if base == "gels":
        return (case.get("ldda", m) * n + case.get("lddb", m) * (case.get("nrhs") or 1)) * item
    return 0


def validate_case(case):
    cid = case.get("case_id", "<无 case_id>")
    op = case.get("op")
    if op not in ALL_OPS:
        raise ContractError(f"{cid}: op={op!r} 不在 {ALL_OPS}")
    if not isinstance(case.get("seed"), int):
        raise ContractError(f"{cid}: seed 必须显式整数")
    nb = case_input_bytes(op, case)
    if nb > FOUR_GIB:
        raise ContractError(
            f"{cid}: 单 case 输入约 {nb / (1024 ** 3):.2f}GiB > 4GiB（假定 A-4；"
            f"应由 S1 冻结阶段裁掉，见 freeze_canonical._luqr_refresh_grid）")
    if op in BATCHED_OPS:
        n = _require_int(case, "n")
        if case.get("batch", 0) < 1:
            raise ContractError(f"{cid}: batch={case.get('batch')!r} 非法")
        if case.get("lda") != n:
            raise ContractError(f"{cid}: lda={case.get('lda')!r} != n={n}（无 padding）")
        return
    if op.endswith("getrf") or op.endswith("geqrf") or op.endswith("gels") \
            or op.endswith("getri"):
        _require_int(case, "n")
    if op.endswith("getrf") or op.endswith("geqrf") or op.endswith("gels"):
        _require_int(case, "m")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_ops(text):
    ops = tuple(s.strip() for s in text.split(",") if s.strip())
    bad = [o for o in ops if o not in ALL_OPS]
    if bad or not ops:
        raise ContractError(f"--ops 含不支持的算子 {bad}（可选：{','.join(ALL_OPS)}）")
    return ops


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog=GENERATOR,
        description="LU/QR 四册包数据生成（产 cases/*.npz 与 cases/index.json 骨架）")
    parser.add_argument("--canonical", help="canonical_cases.json 路径")
    parser.add_argument("--out", help="输出目录（staging；产物落 <out>/cases/）")
    parser.add_argument("--ops", default=",".join(ALL_OPS),
                        help="逗号分隔算子子集，默认 16 接口（与 canonical 取交集）")
    parser.add_argument("--select", choices=("s1", "all"), default="s1",
                        help="case 选择：s1=仅 s1_subset；all=全量")
    parser.add_argument("--criteria", default=None,
                        help="repo-task-solver-accept 的 criteria 目录（批量 ratio_cpu 用）")
    args = parser.parse_args(argv)

    if not args.canonical or not args.out:
        raise ContractError("--canonical 与 --out 必填")

    ops = parse_ops(args.ops)
    canonical_path = Path(args.canonical)
    with canonical_path.open(encoding="utf-8") as fh:
        canonical = json.load(fh)
    if "cases" not in canonical:
        raise ContractError(f"{canonical_path}: 顶层缺 cases 数组")

    selected = [c for c in canonical["cases"]
                if c.get("op") in ops
                and (args.select == "all" or c.get("s1_subset") is True)]
    if not selected:
        raise ContractError(f"canonical 里没有 ops={','.join(ops)} 的可选 case")
    for case in selected:
        validate_case(case)

    cases_dir = Path(args.out) / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)

    index_cases = []
    for case in selected:                               # 保 canonical 原序
        cid = case["case_id"]
        arrays = build_case_arrays(case)
        if case["op"] in BATCHED_OPS:                   # A0 不落数组
            smap = derive_sample_map(case["seed"], case["batch"])
            entry = dict(case)
            entry.update({"materialize": "gen", "arrays": list(arrays),
                          "sampled_contents": len(smap), "sample_map": smap,
                          "ratio_cpu": None, "ratio_cpu_status": None})
            index_cases.append(entry)
            print(f"[gen] {cid}: n={case['n']} batch={case['batch']} "
                  f"contents={len(smap)} arrays={','.join(arrays)}")
            continue
        np.savez(cases_dir / f"{cid}.npz", **arrays)
        entry = dict(case)
        entry["npz"] = f"cases/{cid}.npz"
        entry["arrays"] = list(arrays)
        entry["ratio_cpu"] = None
        entry["ratio_cpu_status"] = None
        index_cases.append(entry)
        print(f"[gen] {cid}: {case['op']} arrays={','.join(arrays)}")

    # HT-8/HT-9：info 契约用例（单算子每算子 3 变体 + 批量每算子 1 混合 case，
    # case_purpose="info"）。派生基座按 canonical 顶层 package_scope：full = 全部
    # 精度 case；缺省/s1 = s1 子集。单算子数组落 npz（供 DUT 读入），批量 A0 不落
    # 数组（sample_map 入条目）；均做构造性自检（info==k_expected），不产 golden。
    _full = canonical.get("package_scope") == "full"
    by_id = {c["case_id"]: c for c in canonical["cases"]}
    info_all = list(derive_info_cases(canonical["cases"], require_s1=not _full)) + \
        list(derive_batched_info_cases(canonical["cases"], require_s1=not _full))
    for info in info_all:
        if info["op"] not in ops:
            continue
        cid = info["case_id"]
        base = by_id[info["base_case_id"]]
        if info["op"] in BATCHED_OPS:
            arrays = build_batched_info_arrays(info)
            smap = derive_sample_map(base["seed"], base["batch"])
            entry = {k: v for k, v in base.items() if k != "s1_subset"}
            entry.update({k: info[k] for k in ("case_id", "case_purpose",
                                               "info_probe", "k_expected",
                                               "base_case_id")})
            entry.update({"materialize": "gen", "arrays": list(arrays),
                          "sampled_contents": len(smap), "sample_map": smap,
                          "ratio_cpu": None, "ratio_cpu_status": None})
            index_cases.append(entry)
            print(f"[gen] {cid}: n={base['n']} batch={base['batch']} "
                  f"probe={info['info_probe']} k_expected={info['k_expected']} "
                  f"arrays={','.join(arrays)}")
            continue
        arrays = build_info_arrays(info, build_case_arrays(base))
        np.savez(cases_dir / f"{cid}.npz", **arrays)
        entry = {k: v for k, v in base.items() if k != "s1_subset"}
        entry.update({k: info[k] for k in ("case_id", "case_purpose", "info_probe",
                                           "k_expected", "base_case_id")})
        entry["npz"] = f"cases/{cid}.npz"
        entry["arrays"] = list(arrays)
        entry["ratio_cpu"] = None
        entry["ratio_cpu_status"] = None
        index_cases.append(entry)
        print(f"[gen] {cid}: n={base['n']} probe={info['info_probe']} "
              f"k_expected={info['k_expected']} arrays={','.join(arrays)}")

    import scipy
    index = {
        "schema": INDEX_SCHEMA,
        "generator": {"name": GENERATOR, "ver": GENERATOR_VER},
        "canonical": {"file": canonical_path.name, "schema": canonical.get("schema"),
                      "frozen_at": canonical.get("frozen_at")},
        "ops": list(ops),
        "env": {"numpy": np.__version__, "scipy": scipy.__version__},
        "cases": index_cases,
    }
    with (cases_dir / "index.json").open("w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"[gen] 完成：{len(selected)} case → {cases_dir}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ContractError as exc:
        print(f"[gen] 契约/前置错误：{exc}", file=sys.stderr)
        sys.exit(2)