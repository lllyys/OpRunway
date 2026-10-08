#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fill_ratio_cpu.py —— S1-Cholesky B2 卡（波 1.5；S2c B2c 卡扩入复数三算子）：
index.json 的 ratio_cpu 回填。

契约：dev-doc/solver/solver-s1-cholesky-spec.md §2.4（ratio_cpu 语义）、§2.2（index
schema）、第 3 节波 1.5 行。B1 的 gen_data_cholesky.py 生成 npz 与 index.json 骨架
（ratio_cpu/ratio_cpu_status 留 null），本脚本对同一冻结输入跑低精度同前缀完整准备链
（实数 FP32 s 前缀 / 复数 complex64 c 前缀）回填这两个字段，产出填充后的 index.json 副本与逐 case 报告；npz 一概不改不重生成
（B 冻结产物由 F 逐字节装包，spec 第 3 节）。

ratio_cpu 语义（spec §2.4）：FP32 s 前缀链路对同一冻结输入跑完整准备链
（potrs/potri 先 spotrf），用 criteria 的 residual_ratio 同一实现计算——本脚本不含
任何残差公式，残差只此一份（AGENTS.md §2 机械门纪律）。残差基全族统一为实现实际
输入（fp32/complex64）升 f64（2026-10-08 裁定；此前 potrf 族「README 1.3：分子分母
用 A64」口径自此废止），数组口径与判据卡的 fallback_kwargs 逐字段相同
（cards_cholesky.py）：

- spotrf: F32 = spotrf(A32)；DPOT01(a=A32, factor=F32, uplo)
- spotrs: F32 = spotrf(A32) → X32 = spotrs(F32, B32)；DPOT02(a=A32, b=B32, x=X32)
- spotri: F32 = spotrf(A32) → C32 = spotri(F32)；DPOT03(a=A32, ainv=C32, uplo)

基变更声明（2026-10-08）：potrf 族（spotrf/cpotrf/spotrfBatched/cpotrfBatched）
既有冻结 ratio_cpu 与 ratio_cpu_mean 系 A64 基，换基后数值过期，旧固化值不可与
新基混用（重算与包重产另排）。新基产物在 index 顶层落 ratio_basis:"A32-f64" 标记，
verify/accept 消费时据此辨新旧——旧包无此字段即 A64 基。

与 README ratio_cpu 定义的关系（如实声明，非冲突）：README potrs 2.2 的 ratio_cpu
就是 CPU 同精度 FP32 参考链路（本脚本同口径）；README potrf 1.3 / potri 3.2 的
ratio_cpu 用纯 FP64 参考链路（~1e-13..1e-11），spec §2.4 把三算子统一到 FP32 s 链路
（README 1.6 的 ratio_cpu32 列即此量，实测 0.000~0.008 ulp）。两种口径下
10·ratio_cpu 都远小于收紧式地板 1，阈值取值不受影响；本脚本以 spec 为唯一契约。

prep_failed（spec §2.4）：准备链失败（任一 LAPACK 例程 info≠0，或链路输出含非有限值）
记 ratio_cpu_status="prep_failed"、ratio_cpu=null，该 case 兜底不可用（thresholds 的
阈值函数对 null 拒算，judge 走 error 路径）。启动时先跑一条人工构造的非正定用例
自测该路径（波 1.5 完成条件），自测不过即停。

画像核对（波 1.5 完成条件，对照 README 实测）：potrf 底噪 <1（README 1.6 ratio_cpu32
0.000~0.008）；potrs 相对 potrf 上浮（README 2.4 实测 0.118~5.748，高一个量级以上，
随规模/病态度增长）；potri 与 potrf 同为平坦底噪（README 3.4 DPOT03 0.000~0.009）。
potrf/potri 底噪 <1 是硬断言（不满足即退出码 3）；potrs 上浮画像输出统计并在报告里
给出布尔判读（本片 case 规模 16~512、cu 构造对角占优 κ 小，上浮幅度预期小于 README
的 128~8192 全量表，故不设硬倍数线，如实报数）。

S2c 复数增量（s2 spec §4 + B2c 任务卡）：算子册扩入 cpotrf/cpotrs/cpotri，准备链
用 c 前缀完整链路（cpotrs/cpotri 前置 cpotrf 同精度，complex64）；残差仍唯一取
criteria 的 residual_ratio（DPOT01/02/03 形状不变，复模与升精度由 criteria 侧实现）。
复数画像不设硬门——实数区间不得盲目继承（B2c 任务卡），统计如实输出、判读交
验证段；prep_failed 自测 s/c 两前缀各走一条非正定用例。

S3 批量增量（dev-doc/solver/solver-s3-batched-spec.md §3，下称 S3 spec）：算子册扩入
spotrfBatched/spotrsBatched/cpotrfBatched/cpotrsBatched。批量条目逐矩阵计算 c_i
（**不做 max 聚合**——评审反例：差矩阵会放宽他家阈值；判定侧逐矩阵配对 r_i ≤ f(c_i)），
链路与残差实现与单算子完全同源（run_chain 同一入口逐矩阵调用）。实施细节：

- 旧包（条目带 npz）：npz 批维数组按 CHUNK_BYTES 分块直读（ZIP_STORED 行级偏移，
  gen_data_cholesky 的读者），GB 级 case 不整驻内存；多进程并行（worker 自适应、
  上限 64，--workers 可调）。回填结果与 npz 存量 ratio_cpu 字段比对（同环境应逐位
  一致；相对差 ≤1e-9 归为告警，更大即报错停下——旧产物混入或环境漂移，fail-closed）。
- A0 新包（HT-2：条目 materialize="gen" 且带 sample_map，**无 npz**）：现场重造
  k=min(5,batch) 个内容（gen_data_cholesky.build_batched_contents，同一内容流），
  逐内容 run_chain 回填；条目 ratio_cpu 写 k 个值列表（prep_failed 记 null 并计数），
  与 gen 侧写入值同口径（回填多发生在 gen 时 criteria 不可达的场景）。
- 批量条目不入实数画像硬门（potrf/potri 底噪 <1 仍只审单算子行）；批量摘要与比对
  结果单列 report 的 batched 段，判读交验证段。
- HT-8：case_purpose=="info" 的条目（info 契约用例）整条跳过——无残差可算，
  ratio_cpu 保持 gen 侧的 null，不入画像统计（HT-4 mean 口径同样不计入）。

CLI：
    fill_ratio_cpu.py --cases <dir> --out <dir> [--criteria <dir>] [--ops s1,s2]
                      [--workers N]
--cases 指含 index.json 与 *.npz 的目录（B1 产物的 cases/）；--criteria 指
repo-task-solver-accept 的 criteria 目录，缺省按镜像树/发布位的相对布局
../../repo-task-solver-accept/criteria 解析。产出：<out>/cases/index.json（填充副本）
与 <out>/ratio_cpu_report.json。
"""

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

import gen_data_cholesky as gd    # S3：批维读者/分块常量与 BATCHED_OPS 同源

MAX_WORKERS = 64                  # 旧包批量条目并行 worker 上限（沿 S3 spec §2 裁定）

TOOL = "fill_ratio_cpu.py"
TOOL_VER = "a32base-r1"  # 2026-10-08 换基：DPOT01 a=A32、index 落 ratio_basis；承 s3-b4-r2
REAL_OPS = ("spotrf", "spotrs", "spotri")
COMPLEX_OPS = ("cpotrf", "cpotrs", "cpotri")
SUPPORTED_OPS = REAL_OPS + COMPLEX_OPS
ALL_OPS = SUPPORTED_OPS + gd.BATCHED_OPS
F32 = np.float32
C64 = np.complex64
ACCEPTED_INDEX_SCHEMAS = ("solver-s1/cases-index@1",)  # 两册字段形状相同，共用一个 schema


def prep_kinds(op):
    """准备链数值域：复数走 c 前缀（s2 spec §4：potrs/potri 前置 cpotrf 同精度，
    complex64），实数走 s 前缀（float32）。"""
    if op in COMPLEX_OPS:
        return C64, "c"
    return F32, "s"


class ContractError(RuntimeError):
    """输入不满足 spec 契约或运行前置——报错停下，不猜测、不静默降级。"""


class PrepFailed(RuntimeError):
    """低精度准备链失败（info≠0 或输出非有限）——按 spec §2.4 记 prep_failed。"""


# ---------------------------------------------------------------------------
# criteria 装载（残差实现唯一来源）
# ---------------------------------------------------------------------------

def load_verdict(criteria_dir):
    """把 criteria 目录挂 sys.path 后 import verdict（verdict 自带该导入形态）。"""
    crit = Path(criteria_dir).resolve()
    if not (crit / "verdict.py").is_file():
        raise ContractError(
            f"criteria 目录 {crit} 下没有 verdict.py——"
            "用 --criteria 指向 repo-task-solver-accept 的 criteria 目录"
        )
    sys.path.insert(0, str(crit))
    import verdict  # noqa: E402
    if Path(getattr(verdict, "__file__", "")).resolve().parent != crit:
        raise ContractError(f"import 到的 verdict 不在 {crit}（sys.path 污染？）")
    return verdict


def _lapack():
    try:
        from scipy.linalg import lapack
    except ImportError as exc:
        raise ContractError(
            "缺 scipy：低精度准备链走 scipy.linalg.lapack 的 s/c 前缀例程，"
            "按 spec §1 应在远程容器内补装 scipy 并记录版本后再运行本脚本"
        ) from exc
    return lapack


# ---------------------------------------------------------------------------
# 低精度准备链（实数 s 前缀 spec §2.4 / 复数 c 前缀 s2 spec §4；被测精度参考实现，scipy→LAPACK）
# ---------------------------------------------------------------------------

def _check_low(name, arr, case_id, dt32):
    """低精度例程输出必须仍是该精度（实数 float32 / 复数 complex64）且有限——
    README 1.5 条 7 的降型自检 + 非有限拦截。"""
    if arr.dtype != dt32:
        raise ContractError(
            f"{case_id}: {name} dtype={arr.dtype}，低精度链路输出应为 {np.dtype(dt32).name}"
            "（scipy 静默降型坑，README 1.5 条 7）"
        )
    if not np.isfinite(arr).all():
        raise PrepFailed(f"{case_id}: {name} 含 NaN/Inf")


def _low_potrf(lapack, a32, uplo, case_id, prefix, dt32):
    f32, info = getattr(lapack, prefix + "potrf")(a32, lower=(uplo == "L"), clean=1)
    if info != 0:
        raise PrepFailed(f"{case_id}: {prefix}potrf info={info}")
    _check_low("F32", f32, case_id, dt32)
    return f32


def run_chain(verdict_mod, lapack, case, arrays):
    """跑该 case 的低精度完整准备链（实数 FP32 s 前缀 / 复数 complex64 c 前缀）
    并返回 residual_ratio 值。

    抛 PrepFailed 表示准备失败（调用方记 prep_failed）；其余异常是脚本或数据错误，
    照常向上抛（fail-closed，不吞进 prep_failed）。
    """
    cid, op, uplo = case["case_id"], case["op"], case["uplo"]
    dt32, prefix = prep_kinds(op)
    a32 = np.ascontiguousarray(arrays["A32"])
    if a32.dtype != dt32:
        raise ContractError(
            f"{cid}: A32 dtype={a32.dtype}，{op} 的准备链输入应为 {np.dtype(dt32).name}"
            "（npz 与算子册不匹配？）")
    f32 = _low_potrf(lapack, a32, uplo, cid, prefix, dt32)
    base = op[1:]                                       # potrf/potrs/potri（s/c 前缀共路）
    if base == "potrf":
        # 2026-10-08 换基：a 用实现实际输入 A32（residual_ratio 内升 f64），不再用 A64
        return verdict_mod.residual_ratio("DPOT01", a=a32, factor=f32, uplo=uplo)
    if base == "potrs":
        b32 = np.ascontiguousarray(arrays["B32"])
        x32, info = getattr(lapack, prefix + "potrs")(f32, b32, lower=(uplo == "L"))
        if info != 0:
            raise PrepFailed(f"{cid}: {prefix}potrs info={info}")
        _check_low("X32", x32, cid, dt32)
        return verdict_mod.residual_ratio("DPOT02", a=a32, b=b32, x=x32)
    if base == "potri":
        c32, info = getattr(lapack, prefix + "potri")(f32, lower=(uplo == "L"))
        if info != 0:
            raise PrepFailed(f"{cid}: {prefix}potri info={info}")
        _check_low("C32", c32, cid, dt32)
        return verdict_mod.residual_ratio("DPOT03", a=a32, ainv=c32, uplo=uplo)
    raise ContractError(f"{cid}: op={op!r} 不在 {SUPPORTED_OPS}")


# ---------------------------------------------------------------------------
# S3 批量条目：逐矩阵 c_i（S3 spec §3；分块直读 + 多进程，见模块 docstring S3 段）
# ---------------------------------------------------------------------------

_BATCH_CTX = {}


def _batched_ratio_job(job):
    """一个 chunk 的逐矩阵 c_i。进程内缓存 criteria 链；prep_failed 记 NaN。"""
    if _BATCH_CTX.get("criteria") != job["criteria"]:
        _BATCH_CTX.update(criteria=job["criteria"],
                          verdict=load_verdict(job["criteria"]),
                          lapack=_lapack())
    verdict_mod, lapack = _BATCH_CTX["verdict"], _BATCH_CTX["lapack"]
    rows, row0 = job["rows"], job["row0"]
    arrays_rows = {name: gd.read_npz_member_rows(job["npz"], job["metas"][name], row0, rows)
                   for name in job["names"]}
    out = np.empty(rows, dtype=np.float64)
    pf = 0
    for i in range(rows):
        case_i = {"case_id": f"{job['cid']}#m{row0 + i}", "op": job["base_op"],
                  "uplo": job["uplo"]}
        try:
            out[i] = run_chain(verdict_mod, lapack,
                               case_i, {k: v[i] for k, v in arrays_rows.items()})
        except PrepFailed as exc:
            out[i] = np.nan
            pf += 1
            print(f"[b2] {case_i['case_id']}: prep_failed（{exc}）——记 NaN", file=sys.stderr)
    return {"idx": job["idx"], "c": out, "prep_failed": pf}


def _auto_workers(n_chunks, per_chunk_bytes, requested):
    """worker 数自适应（沿 S3 spec §2：上限 MAX_WORKERS=64）：cpu 数、chunk 数封顶；
    Linux 容器另按 /proc/meminfo 可用内存 ÷ 3×chunk 工作集封顶（无则跳过）。
    （gen_data_cholesky 的同名函数随 A0 摘除并行机械后，旧包回填在此自持。）"""
    if requested:
        return max(1, min(requested, MAX_WORKERS, n_chunks))
    w = min(MAX_WORKERS, os.cpu_count() or 1, n_chunks)
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) * 1024
                    w = min(w, max(1, avail // max(1, 3 * per_chunk_bytes)))
                    break
    except OSError:
        pass
    return max(1, int(w))


def batched_fill_a0(entry, verdict_mod, lapack):
    """A0 批量条目回填（HT-2：新包不落 npz）。现场重造 k 个内容（gd.build_batched_
    contents，与 gen 侧同一内容流同一函数，天然逐位一致），逐内容 run_chain——与
    旧包 batched_fill 完全同源的链路入口。返回 (k 值列表（prep_failed 记 null）,
    status, 运行信息 dict)。"""
    cid, op = entry["case_id"], entry["op"]
    base_op = gd.batched_base_op(op)
    contents = gd.build_batched_contents(entry)
    rows = {name: contents[name] for name in ("A64", "A32", "B64", "B32")
            if name in contents}
    k = int(contents["A64"].shape[0])
    vals = np.empty(k, dtype=np.float64)
    pf = 0
    for i in range(k):
        case_i = {"case_id": f"{cid}#m{i}", "op": base_op, "uplo": entry["uplo"]}
        try:
            vals[i] = run_chain(verdict_mod, lapack, case_i,
                                {name: arr[i] for name, arr in rows.items()})
        except PrepFailed as exc:
            vals[i] = np.nan
            pf += 1
            print(f"[b2] {case_i['case_id']}: prep_failed（{exc}）——记 NaN", file=sys.stderr)
    ratio = [None if np.isnan(v) else float(v) for v in vals]
    status = "ok" if pf < k else "prep_failed"
    return ratio, status, {"mode": "a0", "contents": k, "prep_failed": pf}


def batched_fill(entry, npz_path, criteria_dir, workers):
    """批量条目回填：逐矩阵 c_i 不聚合（S3 spec §3），返回
    (摘要 dict|None, status, 与 npz 存量 ratio_cpu 的比对结果 dict|None)。"""
    cid, op = entry["case_id"], entry["op"]
    base_op = gd.batched_base_op(op)
    potrs = base_op.endswith("potrs")
    batch, n = entry["batch"], entry["n"]
    names = ["A64", "A32"] + (["B64", "B32"] if potrs else [])
    metas = {}
    for name in names:
        try:
            metas[name] = gd.npz_stored_member_meta(npz_path, name)
        except KeyError:
            raise ContractError(f"{cid}: npz 缺数组 {name}（S3 spec §2 批维 schema）")
    if tuple(metas["A64"]["shape"]) != (batch, n, n):
        raise ContractError(
            f"{cid}: A64 形状 {metas['A64']['shape']} != (batch,n,n)=({batch},{n},{n})")
    per_matrix = sum(int(np.prod(m["shape"][1:], dtype=np.int64)) * m["dtype"].itemsize
                     for m in metas.values())
    rows_per_chunk = max(1, gd.CHUNK_BYTES // max(1, per_matrix))
    n_chunks = -(-batch // rows_per_chunk)
    n_workers = _auto_workers(n_chunks, rows_per_chunk * per_matrix, workers)
    jobs = []
    for idx in range(n_chunks):
        row0 = idx * rows_per_chunk
        jobs.append({"idx": idx, "row0": row0, "rows": min(rows_per_chunk, batch - row0),
                     "npz": str(npz_path), "metas": metas, "names": names,
                     "cid": cid, "base_op": base_op, "uplo": entry["uplo"],
                     "criteria": str(criteria_dir)})
    if n_workers > 1 and n_chunks > 1:
        import multiprocessing
        ctx = multiprocessing.get_context("spawn")   # fork 后 BLAS 死锁坑，spawn 规避
        with ctx.Pool(n_workers) as pool:
            results = pool.map(_batched_ratio_job, jobs, chunksize=1)
    else:
        results = [_batched_ratio_job(j) for j in jobs]
    c = np.concatenate([r["c"] for r in results])
    assert c.shape == (batch,)
    pf = int(np.isnan(c).sum())
    ok = c[~np.isnan(c)]
    if ok.size:
        summary = {"min": float(ok.min()), "median": float(np.median(ok)),
                   "max": float(ok.max()), "count": int(ok.size)}
        status = "ok"
    else:
        summary, status = None, "prep_failed"
    if pf and summary is not None:
        summary["prep_failed"] = pf

    # gen_data 落过 ratio_cpu 字段时比对（docstring S3 段）。2026-10-08 换基后，
    # potrf 族 A64 基旧包存量值在此必然失配并报错——属预期拦截（基变更声明），
    # 旧包需按新基重算重产，不是环境漂移
    compare = None
    try:
        meta_r = gd.npz_stored_member_meta(npz_path, "ratio_cpu")
    except KeyError:
        meta_r = None
    if meta_r is not None:
        stored = gd.read_npz_member_rows(npz_path, meta_r, 0, batch)
        if stored.tobytes() == c.tobytes():
            compare = {"stored_match": "bitwise"}
        else:
            nan_a, nan_b = np.isnan(stored), np.isnan(c)
            if (nan_a != nan_b).any():
                raise ContractError(
                    f"{cid}: npz 存量 ratio_cpu 的 prep_failed 矩阵集合与重算不一致"
                    "（旧产物混入或环境漂移）")
            mask = ~nan_a
            # 归一差：分母带 1.0 地板——ratio 是 ulp 计数量级（0.001~30），恰为 0 的
            # 存量值不该把微小线程差放大成假阳性
            if mask.any():
                diff = np.abs(stored[mask] - c[mask])
                scale = np.maximum(np.maximum(np.abs(stored[mask]), np.abs(c[mask])), 1.0)
                rel = float(np.max(diff / scale))
            else:
                rel = 0.0
            if rel > 1e-9:
                raise ContractError(
                    f"{cid}: 重算 c_i 与 npz 存量 ratio_cpu 相对差 {rel:.3e} > 1e-9"
                    "（旧产物混入或环境漂移，fail-closed）")
            compare = {"stored_match": "tolerated", "max_rel_diff": rel}
            print(f"[b2] {cid}: 存量 ratio_cpu 非逐位一致但相对差 {rel:.3e} ≤ 1e-9"
                  "（BLAS 线程数差异量级）——告警放行", file=sys.stderr)
    return summary, status, {"mode": "parallel" if n_workers > 1 and n_chunks > 1 else "serial",
                             "chunks": n_chunks, "workers": n_workers,
                             "prep_failed": pf, "compare": compare}


# ---------------------------------------------------------------------------
# prep_failed 路径自测（人工构造非正定用例，波 1.5 完成条件）
# ---------------------------------------------------------------------------

def prep_failed_selftest(verdict_mod, lapack):
    """人工非正定阵走同一条链路，必须落 prep_failed（potrf info>0）；s/c 两前缀
    各验一条（复数探针在 cpotrf 步即触发，不依赖 criteria 的复数残差支持）。

    实数 A = [[1,2],[2,1]]（特征值 3 与 -1，对称非正定，spotrf 应报 info=2）；
    复数 A = [[1,2-i],[2+i,1]]（Hermitian 非正定，特征值 1±√5）。自测用与真实
    case 完全相同的 run_chain 入口，不另写第二条路径。返回逐探针结果列表。
    """
    probes = [
        ("selftest-nonspd", "spotrf",
         np.array([[1.0, 2.0], [2.0, 1.0]]),
         "A=[[1,2],[2,1]]（对称非正定，特征值 3/-1）"),
        ("selftest-nonhpd", "cpotrf",
         np.array([[1.0, 2.0 - 1.0j], [2.0 + 1.0j, 1.0]]),
         "A=[[1,2-i],[2+i,1]]（Hermitian 非正定，特征值 1±√5）"),
    ]
    results = []
    for cid, op, a64, desc in probes:
        dt32, _ = prep_kinds(op)
        case = {"case_id": cid, "op": op, "uplo": "L"}
        arrays = {"A64": a64, "A32": a64.astype(dt32)}
        try:
            ratio = run_chain(verdict_mod, lapack, case, arrays)
        except PrepFailed as exc:
            results.append({"case": desc, "chain": "run_chain 同一入口",
                            "outcome": str(exc), "status": "prep_failed",
                            "passed": True})
            continue
        raise ContractError(
            f"prep_failed 自测未触发（{op}）：非正定阵竟返回 ratio={ratio}——"
            "准备链的 info 检查失效，停止回填"
        )
    return results


# ---------------------------------------------------------------------------
# 画像核对（波 1.5 完成条件；README 对照见模块 docstring）
# ---------------------------------------------------------------------------

def _stats(vals):
    if not vals:
        return None
    v = sorted(vals)
    return {"count": len(v), "min": v[0], "max": v[-1],
            "median": float(np.median(v))}


def profile_check(rows, ops):
    """逐算子统计 + 判读。实数沿 S1：potrf/potri 底噪 <1 硬断言、potrs 上浮布尔
    判读；复数三算子只输出画像统计，不设硬门——实数区间不得盲目继承（B2c 任务
    卡），判读交验证段。本次回填面没有 case 的算子不参与判读（单册回填的常态）；
    有 case 但全 prep_failed 的实数算子仍触发硬断言（沿 S1 语义）。"""
    rows_by_op = {op: [r for r in rows if r["op"] == op] for op in ops}
    by_op = {op: [r["ratio_cpu"] for r in rs if r["ratio_cpu_status"] == "ok"]
             for op, rs in rows_by_op.items()}
    stats = {op: _stats(by_op[op]) for op in ops if rows_by_op[op]}
    hard_fail = []
    for op in ("spotrf", "spotri"):
        if not rows_by_op.get(op):
            continue                    # 该算子不在本次回填面——底噪门不适用
        bad = [v for v in by_op[op] if not v < 1.0]
        if bad or not by_op[op]:
            hard_fail.append(f"{op} 底噪断言不满足（应全部 <1，越界值 {bad}）")
    s_potrf = stats.get("spotrf")
    s_potrs = stats.get("spotrs")
    checks = {}
    if rows_by_op.get("spotrf"):
        checks["potrf_noise_below_1"] = not any("spotrf" in m for m in hard_fail)
    if rows_by_op.get("spotri"):
        checks["potri_noise_below_1"] = not any("spotri" in m for m in hard_fail)
    if rows_by_op.get("spotrs"):
        checks["potrs_uplift_over_potrf_max"] = bool(
            s_potrs and s_potrf and s_potrs["max"] > s_potrf["max"])
        checks["potrs_all_below_floor_30"] = bool(s_potrs and s_potrs["max"] < 30.0)
    complex_present = [op for op in COMPLEX_OPS if rows_by_op.get(op)]
    if complex_present:
        checks["complex_profile"] = {
            "ops": complex_present,
            "hard_gate": None,
            "note": "复数画像不设硬门（实数区间不得盲目继承，B2c 任务卡）；"
                    "统计如实输出，判读交验证段",
        }
    checks["readme_reference"] = {
        "potrf": "README 1.6 ratio_cpu32 实测 0.000~0.008 ulp（平坦底噪，实数）",
        "potrs": "README 2.4 ratio_cpu 实测 0.118~5.748 ulp（上浮，随规模/病态度涨，实数）",
        "spotri": "README 3.4 DPOT03(FP32) 实测 0.000~0.009 ulp（平坦底噪，实数）",
    }
    return stats, checks, hard_fail


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def default_criteria(script_path):
    """镜像树与发布位同构的相对布局：../../repo-task-solver-accept/criteria。"""
    return script_path.resolve().parent.parent.parent / "repo-task-solver-accept" / "criteria"


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog=TOOL,
        description="S1-Cholesky ratio_cpu 回填（spec §2.4；产填充 index.json 与逐 case 报告）",
    )
    parser.add_argument("--cases", required=True,
                        help="B1 产物 cases/ 目录（含 index.json 与 *.npz，只读）")
    parser.add_argument("--out", required=True,
                        help="输出目录（产 <out>/cases/index.json 与 <out>/ratio_cpu_report.json）")
    parser.add_argument("--criteria", default=None,
                        help="repo-task-solver-accept 的 criteria 目录（缺省按镜像树相对布局解析）")
    parser.add_argument("--ops", default=",".join(ALL_OPS),
                        help="逗号分隔的算子子集，默认三册十算子（与 index 实际所含取交集）")
    parser.add_argument("--workers", type=int, default=0,
                        help="批量条目并行 worker 数；0=自适应（上限 64，S3 spec §2）")
    args = parser.parse_args(argv)

    ops = tuple(s.strip() for s in args.ops.split(",") if s.strip())
    bad = [o for o in ops if o not in ALL_OPS]
    if bad or not ops:
        raise ContractError(f"--ops 含不支持的算子 {bad}（可选：{','.join(ALL_OPS)}）")

    criteria_dir = Path(args.criteria) if args.criteria else default_criteria(Path(__file__))
    verdict_mod = load_verdict(criteria_dir)
    lapack = _lapack()

    selftest = prep_failed_selftest(verdict_mod, lapack)
    for st in selftest:
        print(f"[b2] prep_failed 自测通过：{st['outcome']}")

    cases_dir = Path(args.cases)
    index_path = cases_dir / "index.json"
    if not index_path.is_file():
        raise ContractError(f"{cases_dir} 下没有 index.json（应指向 B1 产物 cases/ 目录）")
    with index_path.open(encoding="utf-8") as fh:
        index = json.load(fh)
    if index.get("schema") not in ACCEPTED_INDEX_SCHEMAS:
        raise ContractError(
            f"index schema={index.get('schema')!r} 不在 {ACCEPTED_INDEX_SCHEMAS}")

    rows = []
    n_ok = n_prep_failed = 0
    for entry in index["cases"]:
        if entry["op"] not in ops:
            continue
        cid = entry["case_id"]
        if entry.get("case_purpose") == "info":
            # HT-8：info 契约用例无残差可算（只比 info，不进 mean），原样透传
            print(f"[b2] {cid}: case_purpose=info 跳过（ratio_cpu 保持 null）")
            continue
        if entry["op"] in gd.BATCHED_OPS:               # S3 批量
            if entry.get("materialize") == "gen" and "sample_map" in entry:
                # HT-2 A0 新包：无 npz，现场重造 k 个内容逐内容回填
                ratio, status, binfo = batched_fill_a0(entry, verdict_mod, lapack)
                entry["ratio_cpu"] = ratio
                entry["ratio_cpu_status"] = status
                if status == "ok":
                    n_ok += 1
                else:
                    n_prep_failed += 1
                row = {k_: entry[k_] for k_ in
                       ("case_id", "op", "source", "n", "nrhs", "uplo",
                        "ratio_cpu", "ratio_cpu_status")}
                row["batch"] = entry["batch"]
                row["batched_fill"] = binfo
                rows.append(row)
                print(f"[b2] {cid}: op={entry['op']} n={entry['n']} batch={entry['batch']} "
                      f"uplo={entry['uplo']} A0 contents={binfo['contents']}/{entry['batch']} "
                      f"ratio={status}")
                continue
            npz_path = cases_dir / Path(entry["npz"]).name
            summary, status, binfo = batched_fill(entry, npz_path, criteria_dir, args.workers)
            entry["ratio_cpu"] = summary                # 摘要（S3 spec §3：仅作参考）
            entry["ratio_cpu_status"] = status
            if status == "ok":
                n_ok += 1
            else:
                n_prep_failed += 1
            row = {k: entry[k] for k in
                   ("case_id", "op", "source", "n", "nrhs", "uplo",
                    "ratio_cpu", "ratio_cpu_status")}
            row["batch"] = entry["batch"]
            row["batched_fill"] = binfo
            rows.append(row)
            print(f"[b2] {cid}: op={entry['op']} n={entry['n']} batch={entry['batch']} "
                  f"uplo={entry['uplo']} 逐矩阵 c_i {binfo['mode']}/{binfo['chunks']}chunk "
                  f"摘要={summary} status={status}")
            continue
        npz_path = cases_dir / Path(entry["npz"]).name
        with np.load(npz_path) as npz:
            arrays = {k: npz[k] for k in npz.files}
        missing = {"A64", "A32"} - set(arrays)
        if entry["op"].endswith("potrs"):
            missing |= {"B64", "B32"} - set(arrays)
        if missing:
            raise ContractError(f"{cid}: npz 缺数组 {sorted(missing)}（spec §2.2）")
        try:
            ratio = run_chain(verdict_mod, lapack, entry, arrays)
        except PrepFailed as exc:
            entry["ratio_cpu"] = None
            entry["ratio_cpu_status"] = "prep_failed"
            n_prep_failed += 1
            print(f"[b2] {cid}: prep_failed（{exc}）——兜底不可用")
        else:
            entry["ratio_cpu"] = float(ratio)
            entry["ratio_cpu_status"] = "ok"
            n_ok += 1
            print(f"[b2] {cid}: op={entry['op']} n={entry['n']} uplo={entry['uplo']} "
                  f"source={entry['source']} ratio_cpu={ratio:.6g}")
        rows.append({k: entry[k] for k in
                     ("case_id", "op", "source", "n", "nrhs", "uplo",
                      "ratio_cpu", "ratio_cpu_status")})

    if not rows:
        raise ContractError(f"index.json 里没有 ops={','.join(ops)} 的 case")

    rows_scalar = [r for r in rows if "batch" not in r]     # 批量行不入画像硬门（S3 段）
    stats, checks, hard_fail = profile_check(rows_scalar, ops)

    import scipy
    # 基版本标记（2026-10-08 换基）：本次回填的 ratio 全族为实现实际输入升 f64 的
    # A32 基；旧 index 无此字段即 A64 基，不可与新基混用
    index["ratio_basis"] = "A32-f64"
    index["ratio_cpu_fill"] = {
        "tool": {"name": TOOL, "ver": TOOL_VER},
        "spec": "dev-doc/solver/solver-s1-cholesky-spec.md#2.4",
        "chain": "低精度完整准备链（实数 FP32 s 前缀 / 复数 complex64 c 前缀；"
                 "potrs/potri 先同前缀 potrf），scipy.linalg.lapack",
        "residual_impl": "repo-task-solver-accept/criteria/verdict.residual_ratio（唯一实现）",
        "env": {"numpy": np.__version__, "scipy": scipy.__version__},
    }

    out_cases = Path(args.out) / "cases"
    out_cases.mkdir(parents=True, exist_ok=True)
    out_index = out_cases / "index.json"
    with out_index.open("w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    report = {
        "schema": "solver-s1/ratio-cpu-report@1",
        "spec": "dev-doc/solver/solver-s1-cholesky-spec.md#2.4 + #3 波1.5",
        "tool": index["ratio_cpu_fill"]["tool"],
        "env": index["ratio_cpu_fill"]["env"],
        "inputs": {"cases_dir": str(cases_dir), "index": str(index_path)},
        "filled": {"ok": n_ok, "prep_failed": n_prep_failed, "total": len(rows)},
        "prep_failed_selftest": selftest,
        "per_case": rows,
        "profile_stats": stats,
        "profile_checks": checks,
        "hard_fail": hard_fail,
        "batched": [r for r in rows if "batch" in r] or None,   # S3 批量摘要与比对，判读交验证段
    }
    report_path = Path(args.out) / "ratio_cpu_report.json"
    with report_path.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"[b2] 回填完成：ok={n_ok} prep_failed={n_prep_failed} → {out_index}")
    for op in ops:
        s = stats.get(op)
        if s:
            print(f"[b2] {op}: min={s['min']:.6g} median={s['median']:.6g} "
                  f"max={s['max']:.6g}（{s['count']} case）")
    print(f"[b2] 画像判读：{ {k: v for k, v in checks.items() if k != 'readme_reference'} }")
    if hard_fail:
        for msg in hard_fail:
            print(f"[b2] 硬断言失败：{msg}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ContractError as exc:
        print(f"[b2] 契约/前置错误：{exc}", file=sys.stderr)
        sys.exit(2)
