#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_data_cholesky.py —— S1-Cholesky B1 卡（S2c B2c 卡扩入复数三算子）：
包数据（npz）与 index.json 骨架生成器。

契约：dev-doc/solver/solver-s1-cholesky-spec.md §2.1（canonical_cases）、§2.2（包数据
schema）、§2.5（CLI）。只消费波 0 冻结的 canonical_cases.json 中 s1_subset=true 的
case，逐 case 产出 cases/<case_id>.npz 与 cases/index.json 骨架；ratio_cpu 与
ratio_cpu_status 留 null，由 B2（波 1.5）用 A 卡 residual_ratio 回填。

执行环境（spec §1）：一切执行在远程 NPU 容器；本机只允许 `python -m py_compile`
语法自检。运行时依赖 numpy 与 scipy（golden 走 scipy.linalg.lapack 的 d/z 前缀例程；
scipy 容器内补装并记录版本）。

数据构造两套（spec §0 分布标签），出处如下：

- cu 主构造：转写自 0923 任务目录三个 bench cu。0923 目录 = canonical_cases.json
  的 task_dir 字段，即 repos/community_task/9月/昇腾社区线上发放0923/
  9月社区任务-单精度实数Cholesky分解、求解和批量接口(950)/（repos 路径基准是主检出
  根，spec §4）。fillSPD 在 spotrf_bench.cu:166-178 与 spotrs_bench.cu:161-173、
  spotri_bench.cu:161-173 三处同函数（spotrs/spotri 两份逐字节相同，spotrf 份仅多
  :172/:174 两条行尾注释，代码一致）；spotrs 右端另见 spotrs_bench.cu:210。
  逐行出处见 fill_spd_cu / fill_rhs_cu 内注释。
- std 补充构造：转写自 repos/solver_tasks-main/cholesky_precision/README.md §1.4
  （SPD 构造：底阵 B 按生态标准 §1.2 原样参数，A = B·Bᴴ + n·I，seed=20250912+n）
  与 §2.4、§2.5 条 2（右端 B = A·X_true，X_true 按生态标准分布生成）；实现参照同
  目录 ch_potrf_verdict.py:36-52（gen_spd）与 ch_potrs_verdict.py:54-65（gen_rhs)、
  93-96（抽取顺序 A → X_true → B64 = A64 @ X_true）。

golden（spec §2.2：golden64 = d 前缀链路原值，golden32 = 降型）：
  spotrf → dpotrf(A64)；spotrs → dpotrf(A64) 再 dpotrs(F64, B64)；
  spotri → dpotrf(A64) 再 dpotri(F64)，存储侧半三角、另侧置 0。
  链路对照 ch_potrf_verdict.py:80-83 与 ch_potri_verdict.py:104-105（golden 均为
  d 前缀；potrs 的 golden 按 spec §2.2 取 d 链路 dpotrs，不取 verdict 脚本第一层
  的 np.linalg.solve——spec 是唯一契约）。

声明的转写差异（cu 二进制无法逐位复现，只忠实其构造配方）：
1. C rand() 换 numpy default_rng(seed)（PCG64）：rand()%1000 → rng.integers(0,1000)，
   rand()%10000 → rng.integers(0,10000)。seed 逐 case 显式取 canonical_cases 的
   seed 字段，本脚本无任何全局随机态。
2. cu 在 float32 里算；本脚本按 spec §2.2 以 FP64 为母本（d 前缀 golden 需要 FP64
   输入），A32/B32 一律由 A64/B64.astype(float32) 降型——装包自检项
   「A32 == A64.astype(f32)（B 同理）」由此构造性成立，本脚本仍逐 case 断言。
3. 抽取顺序保持源文件的循环结构（见各构造函数注释）。
4. ±0 符号位：复数 vi 恰为 0 时 cu 镜像存 −0.0、本脚本得 +0.0（数值相等，
   判定无影响；2026-09-23 转写抽查实测命中一次并留档）。std 的 matmul（B·Bᴴ、
   A·X_true）走 BLAS，逐位可复现性以同容器为界（同 README §2.6 冻结 B64 的理由；
   B1 完成条件「同容器重跑两次数组逐位一致」以同容器为前提）。

S2c 复数增量（契约：dev-doc/solver/solver-s2-spec.md §4 契约增量表，冻结）：

- 算子册扩入 cpotrf/cpotrs/cpotri；npz 字段名不变，dtype 按 §4 映射：
  A64/B64/golden64=complex128、A32/B32/golden32=complex64，降型校验
  A32 == A64.astype(complex64)（B 同理，逐 case 断言）。
- cu 主构造转写 0923 复数任务目录（canonical 的 task_dir，
  9月社区任务-单精度复数Cholesky分解、求解和批量接口(950)/）三个 bench cu 的
  fillSPD（对角占优 Hermitian）：cpotrf_bench.cu:161-175（:175 为收尾括号）；cpotrs_bench.cu:155-169
  与 cpotri_bench.cu:160-174 同函数（与 cpotrf 份仅差 :168/:171 两条行尾注释，
  代码逐字一致）。cpotrs 右端见 cpotrs_bench.cu:215-218（:213 fillSPD 之后同一
  rand() 流续抽）。逐段出处见 fill_hpd_cu / fill_rhs_cu_complex 内注释。
- std 补充构造转写 ch_potrf_verdict.py:36-52 gen_spd 复数分支（底阵实/虚两次同
  分布采样，A = B·Bᴴ + n·I——s2 spec §4 记作 A=BᴴB+nI，蓝本与 cholesky README
  §1.4 均为 B·Bᴴ，两式同为 Hermitian 正定构造，实现随蓝本）与
  ch_potrs_verdict.py:54-65 gen_rhs 复数分支、:71-74（A → X_true → B64=A64@X_true）。
- golden 用 z 前缀链路（§4）：cpotrf→zpotrf；cpotrs→zpotrf+zpotrs；
  cpotri→zpotrf+zpotri（实数对照处见各 golden 函数；z 而非 c——c 前缀是复数
  单精度，FP64 golden 必须 z，ch_potrf_verdict.py:77 注）。golden32 由 complex128
  降型 complex64，非有限值即拦（§4 降型校验）。
- Hermitian「对角虚部按 0 处理并校验」（§4）：cu 构造对角虚部恒 0
  （cpotrf_bench.cu:166）；std 的 B·Bᴴ 在带 FMA 的 BLAS 下对角可残留 ~1e-16 量级
  虚部，生成侧按契约置 0 后冻结（不消耗 rng，B64=A64@X_true 用置 0 后的 A64，
  包内自洽），随后 cu/std 两路统一断言对角虚部为 0。

CLI（spec §2.5 逐字；S2c 起 --ops 扩入 cpotrf,cpotrs,cpotri，缺省两册并集并与
canonical 实际所含取交集）：
    gen_data_cholesky.py --canonical <json> --out <dir> [--ops spotrf,...,cpotri]
产出：<out>/cases/<case_id>.npz 与 <out>/cases/index.json。
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

GENERATOR = "gen_data_cholesky.py"
GENERATOR_VER = "s2c-b2c-r1"  # S2c：复数三算子入册（s2 spec §4）；承 s1-b1-r2（golden 落盘前转 C 序）
REAL_OPS = ("spotrf", "spotrs", "spotri")
COMPLEX_OPS = ("cpotrf", "cpotrs", "cpotri")
SUPPORTED_OPS = REAL_OPS + COMPLEX_OPS
F32 = np.float32
F64 = np.float64
C64 = np.complex64
C128 = np.complex128


def op_kinds(op):
    """按算子名解析数值域：返回 (母本 dtype, 降型 dtype, golden LAPACK 前缀)。
    s2 spec §4 dtype 映射：复数 A64/B64/golden64=complex128、A32/B32/golden32=
    complex64，npz 字段名不变；golden 复数用 z 前缀（c 是复数单精度，FP64 golden
    必须 z）。"""
    if op in COMPLEX_OPS:
        return C128, C64, "z"
    return F64, F32, "d"

# std 补充 case 的分布组落点（canonical_report.md §5 留给 B1 裁定，此处采纳其建议）：
# 序号 9001 → 均匀组 U（README §1.4 表 #1：n=128、L、U·S 同点位）；
# 序号 9002 → 正态组 N（README §1.4 表 #10：n=512、U、N·S 同点位，覆盖固定容差
# 误伤高发的正态侧）。同 n 同 seed 同分布 → 跨算子复用同一 A（canonical_report §3）。
STD_DIST_BY_ORDINAL = {1: "U", 2: "N"}


class ContractError(RuntimeError):
    """输入不满足 spec 契约或运行前置——报错停下，不猜测、不静默降级。"""


# ---------------------------------------------------------------------------
# cu 主构造（转写自 0923 目录 bench cu；逐行出处标注为 <文件>:<行号>）
# ---------------------------------------------------------------------------

def fill_spd_cu(rng, n):
    """对角占优对称 SPD 构造，转写 fillSPD（spotrf_bench.cu:166-178；
    spotrs_bench.cu:161-173 与 spotri_bench.cu:161-173 同函数，除行尾注释外逐字一致）。

    cu 原文（列主序缓冲、外层 j 内层 i）：
        for j: for i:
            i==j -> A[j,j] = (float)n + (float)(rand()%1000)/1000.0f      # :170
            i<j  -> v = (float)(rand()%1000)/1000.0f - 0.5f  # [-0.5,0.5)  :172
                    A[i,j] = v; A[j,i] = v                    # 对称       :173-174
    抽取顺序因此是：第 j 列先出 j 个 off-diagonal 值（i=0..j-1 递增），再出 1 个
    对角值——本函数按同一顺序消费 rng（PCG64 的 integers 逐元素消费比特流，
    整列批抽与逐个单抽同流）。lda 只是元数据、本片无 padding（spec §2.2），
    抽取不消费 padding 位。

    构造性正定（issue B1）：对角元 ≥ n、每行非对角绝对值之和 ≤ (n-1)/2，Gershgorin
    圆盘给出特征值下界 (n+1)/2——正定性由构造保证，勿在生成或判定通路上增加运行时
    正定检查（装包自检的抽验 info=0 与 golden 的 PrepFailed 属工具链自检，不在此列）。
    """
    A = np.zeros((n, n), dtype=F64)
    for j in range(n):
        q = rng.integers(0, 1000, size=j + 1)          # rand()%1000 的转写（差异 1）
        off = q[:j].astype(F64) / 1000.0 - 0.5          # spotrf_bench.cu:172 [-0.5,0.5)
        A[:j, j] = off                                  # spotrf_bench.cu:173 A[i+j*lda]=v
        A[j, :j] = off                                  # spotrf_bench.cu:174 A[j+i*lda]=v 对称
        A[j, j] = float(n) + float(q[j]) / 1000.0       # spotrf_bench.cu:170 对角 n+[0,1)
    return A


def fill_rhs_cu(rng, n, nrhs):
    """spotrs 右端构造，转写 spotrs_bench.cu:210：
        for (i = 0; i < cntB; i++) hB[i] = (float)(rand()%10000)/100.0f - 50.0f;
    cntB = ldb*nrhs，i 沿列主内存线性递增——本片 ldb == n（无 padding，进入本函数前
    已校验），故逻辑元素 (i,j) 对应第 j*n+i 次抽取；按列主序还原成 (n, nrhs)。
    在 cu 中该循环紧接 fillSPD(hA) 之后同一 rand() 流续抽（spotrs_bench.cu:208-210），
    调用方保持同一 rng 先 A 后 B 的顺序。
    """
    q = rng.integers(0, 10000, size=n * nrhs)           # rand()%10000 的转写（差异 1）
    B = q.astype(F64) / 100.0 - 50.0                    # spotrs_bench.cu:210 [-50,50)
    return np.ascontiguousarray(B.reshape((n, nrhs), order="F"))


def fill_hpd_cu(rng, n):
    """对角占优 Hermitian 正定（HPD）构造，转写复数 fillSPD（cpotrf_bench.cu:161-174；
    cpotrs_bench.cu:155-169 与 cpotri_bench.cu:160-174 同函数，仅差 cpotrf 份
    :168/:171 两条行尾注释，代码逐字一致）。

    cu 原文（列主序缓冲、外层 j 内层 i；i>j 不落值也不抽数）：
        for j: for i:
            i==j -> A[j,j].x = (float)n + (float)(rand()%1000)/1000.0f       # :165
                    A[j,j].y = 0.0f                                          # :166
            i<j  -> vr = (float)(rand()%500)/1000.0f - 0.25f  # [-0.25,0.25) # :168
                    vi = (float)(rand()%500)/1000.0f - 0.25f                 # :169
                    A[i,j] = vr + i·vi                                       # :170
                    A[j,i] = vr - i·vi                      # Hermitian 镜像 # :171
    抽取顺序：第 j 列先出 j 个非对角元（i=0..j-1 递增，每元素先 vr 后 vi，同界
    %500 连抽），再出 1 个对角值（%1000）——与实数 fill_spd_cu 同一「先非对角后
    对角」列序。vr/vi 同界 500，整列批抽 2j 个与逐个单抽同流（PCG64 的 integers
    逐元素消费比特流，差异声明 1）；对角是另一界（1000），单独一次抽取。
    lda 只是元数据、本片无 padding（spec §2.2），抽取不消费 padding 位（同实数）。

    构造性正定（issue B1）：对角元 ≥ n（虚部 0）、非对角模 ≤ √2/4 < 1/2，每行非对角
    模之和 < (n-1)/2，Gershgorin 圆盘给出特征值下界 > (n+1)/2——正定性由构造保证，
    勿在生成或判定通路上增加运行时正定检查（工具链自检不在此列，同 fill_spd_cu）。
    """
    A = np.zeros((n, n), dtype=C128)
    for j in range(n):
        q = rng.integers(0, 500, size=2 * j)            # rand()%500 的转写（差异 1）
        vr = q[0::2].astype(F64) / 1000.0 - 0.25        # cpotrf_bench.cu:168 [-0.25,0.25)
        vi = q[1::2].astype(F64) / 1000.0 - 0.25        # cpotrf_bench.cu:169
        A[:j, j] = vr + 1j * vi                         # cpotrf_bench.cu:170 A[i+j*lda]=vr+ivi
        A[j, :j] = vr - 1j * vi                         # cpotrf_bench.cu:171 A[j+i*lda]=vr-ivi（Hermitian）
        d = rng.integers(0, 1000)                       # rand()%1000 的转写（差异 1）
        A[j, j] = float(n) + float(d) / 1000.0          # cpotrf_bench.cu:165-166 对角 n+[0,1)、虚部 0
    return A


def fill_rhs_cu_complex(rng, n, nrhs):
    """cpotrs 右端构造，转写 cpotrs_bench.cu:215-218：
        for (i = 0; i < cntB; i++):
            hB[i].x = (float)(rand()%10000)/100.0f - 50.0f;   # :216
            hB[i].y = (float)(rand()%10000)/100.0f - 50.0f;   # :217
    cntB = ldb*nrhs，i 沿列主内存线性递增，每元素先实部（.x）后虚部（.y），同界
    %10000 连抽，批抽 2·cntB 个同流。本片 ldb == n（无 padding，进入本函数前已
    校验），故逻辑元素 (i,j) 对应线性第 j*n+i 元；按列主序还原成 (n, nrhs)。
    在 cu 中该循环紧接 fillSPD(hA) 之后同一 rand() 流续抽（cpotrs_bench.cu:
    213-218），调用方保持同一 rng 先 A 后 B 的顺序。
    """
    q = rng.integers(0, 10000, size=2 * n * nrhs)       # rand()%10000 的转写（差异 1）
    re = q[0::2].astype(F64) / 100.0 - 50.0             # cpotrs_bench.cu:216 [-50,50)
    im = q[1::2].astype(F64) / 100.0 - 50.0             # cpotrs_bench.cu:217
    B = re + 1j * im
    return np.ascontiguousarray(B.reshape((n, nrhs), order="F"))


# ---------------------------------------------------------------------------
# std 补充构造（转写自 solver_tasks-main cholesky README 与其 verdict 脚本）
# ---------------------------------------------------------------------------

def gen_spd_std(rng, n, dist, complex_=False):
    """标准工程精度 SPD/HPD 构造，转写 ch_potrf_verdict.py:36-52 gen_spd（复数
    分支即 S2c std 蓝本）；记法出处 README.md §1.4：底阵 B 按生态标准 §1.2 原样
    参数（均匀 U(-5,5) / 正态 μ∈[-5,5] σ∈[0.1,2]），A = B·Bᴴ + n·I（只保证严格
    正定，最小特征值 ≥ n，不压幅值、不改分布性质；s2 spec §4 记作 A=BᴴB+nI，
    蓝本与 README 均为 B·Bᴴ，实现随蓝本）。
    复数抽取顺序照蓝本：实部底阵在前，虚部底阵续抽同分布（正态组复用同一 μ/σ，
    ch_potrf_verdict.py:49）；实数分支语句原样保留（实数回归零漂移）。
    """
    if dist == "U":
        base = rng.uniform(-5, 5, (n, n))               # ch_potrf_verdict.py:43
    else:
        mu = rng.uniform(-5, 5)                         # ch_potrf_verdict.py:45
        sigma = rng.uniform(0.1, 2)                     # ch_potrf_verdict.py:46
        base = rng.normal(mu, sigma, (n, n))            # ch_potrf_verdict.py:47
    if complex_:
        bi = (rng.uniform(-5, 5, (n, n)) if dist == "U"
              else rng.normal(mu, sigma, (n, n)))       # ch_potrf_verdict.py:49 虚部底阵
        base = base + 1j * bi                           # ch_potrf_verdict.py:50
        return base @ base.conj().T + n * np.eye(n)     # ch_potrf_verdict.py:51 A=B·Bᴴ+nI
    return base @ base.T + n * np.eye(n)                # ch_potrf_verdict.py:51（实数 Bᴴ=Bᵀ）


def gen_rhs_std(rng, n, nrhs, dist, complex_=False):
    """标准构造的真解 X_true，转写 ch_potrs_verdict.py:54-65 gen_rhs（复数分支即
    S2c std 蓝本）；README.md §2.5 条 2：右端 B = A·X_true（先有解再构造右端，
    解精确存在），X_true 按生态标准分布生成。正态组的 μ/σ 在此处独立续抽（与
    gen_spd 内的 μ/σ 是两次采样，顺序同 verdict 脚本：先 gen_spd 后 gen_rhs）。
    复数抽取顺序照蓝本：实部在前、虚部续抽同分布（正态组复用本函数的 μ/σ，
    ch_potrs_verdict.py:63）；实数分支抽取序不变（实数回归零漂移）。
    """
    if dist == "U":
        x = rng.uniform(-5, 5, (n, nrhs))               # ch_potrs_verdict.py:57
        if not complex_:
            return x
        xi = rng.uniform(-5, 5, (n, nrhs))              # ch_potrs_verdict.py:63 虚部
        return x + 1j * xi                              # ch_potrs_verdict.py:64
    mu = rng.uniform(-5, 5)                             # ch_potrs_verdict.py:59
    sigma = rng.uniform(0.1, 2)                         # ch_potrs_verdict.py:60
    x = rng.normal(mu, sigma, (n, nrhs))                # ch_potrs_verdict.py:61
    if not complex_:
        return x
    xi = rng.normal(mu, sigma, (n, nrhs))               # ch_potrs_verdict.py:63 虚部
    return x + 1j * xi                                  # ch_potrs_verdict.py:64


def std_dist_of(case):
    """std case 的分布组：按编号序数查 STD_DIST_BY_ORDINAL（9001→1、9002→2）。
    表外序数说明契约没有给该 case 定义分布——停下报错，不猜测。"""
    ordinal = int(case["case_id"].rsplit("-", 1)[1]) - 9000
    dist = STD_DIST_BY_ORDINAL.get(ordinal)
    if dist is None:
        raise ContractError(
            f"{case['case_id']}: std 序数 {ordinal} 无分布组定义"
            "（STD_DIST_BY_ORDINAL 只登记了本片 9001/9002）；"
            "新增 std case 需先在 spec/canonical_report 里裁定分布组"
        )
    return dist


# ---------------------------------------------------------------------------
# golden：FP64 前缀链路（实数 d，spec §2.2；复数 z，s2 spec §4）
# ---------------------------------------------------------------------------

def _lapack():
    """scipy 是容器内运行时依赖（spec §1），缺失时给出指向性错误而非裸 ImportError。"""
    try:
        from scipy.linalg import lapack
    except ImportError as exc:
        raise ContractError(
            "缺 scipy：golden 走 scipy.linalg.lapack 的 d/z 前缀例程，"
            "按 spec §1 应在远程容器内补装 scipy 并记录版本后再运行本脚本"
        ) from exc
    return lapack


def _check_info(info, routine, case_id):
    if info != 0:
        raise ContractError(f"{case_id}: {routine} info={info}（SPD 构造下应为 0）")


def golden_potrf(A64, uplo, case_id, prefix="d"):
    """golden F：{d|z}potrf(A64) 原值。scipy 包装 clean=1（显式传入）把非存储侧
    置 0，返回的就是存储侧半三角因子（实数对照 ch_potrf_verdict.py:80-81 同链路；
    复数 z 前缀对照 :77-78——c 前缀是复数单精度，FP64 golden 必须 z）。"""
    lapack = _lapack()
    F, info = getattr(lapack, prefix + "potrf")(A64, lower=(uplo == "L"), clean=1)
    _check_info(info, prefix + "potrf", case_id)
    return F


def golden_potrs(A64, B64, uplo, case_id, prefix="d"):
    """golden X：FP64 链路 {d|z}potrf(A64) → {d|z}potrs(F64, B64)（实数 spec §2.2
    potrs=X；复数 z 前缀，s2 spec §4）。"""
    lapack = _lapack()
    F, info = getattr(lapack, prefix + "potrf")(A64, lower=(uplo == "L"), clean=1)
    _check_info(info, prefix + "potrf", case_id)
    X, info = getattr(lapack, prefix + "potrs")(F, B64, lower=(uplo == "L"))
    _check_info(info, prefix + "potrs", case_id)
    return X


def golden_potri(A64, uplo, case_id, prefix="d"):
    """golden C：FP64 链路 {d|z}potrf → {d|z}potri（实数对照 ch_potri_verdict.py:
    104-105；复数 z 前缀对照 :85-86），按 spec §2.2 存储侧半三角、另侧显式置 0。"""
    lapack = _lapack()
    F, info = getattr(lapack, prefix + "potrf")(A64, lower=(uplo == "L"), clean=1)
    _check_info(info, prefix + "potrf", case_id)
    C, info = getattr(lapack, prefix + "potri")(F, lower=(uplo == "L"))
    _check_info(info, prefix + "potri", case_id)
    return np.tril(C) if uplo == "L" else np.triu(C)


# ---------------------------------------------------------------------------
# 逐 case 生成与自检
# ---------------------------------------------------------------------------

def validate_case(case):
    """进场校验：字段齐全、枚举合法、本片无 padding（spec §2.1/§2.2）。"""
    cid = case.get("case_id", "<无 case_id>")
    op = case.get("op")
    if op not in SUPPORTED_OPS:
        raise ContractError(f"{cid}: op={op!r} 不在 {SUPPORTED_OPS}")
    if case.get("source") not in ("cu", "std"):
        raise ContractError(f"{cid}: source={case.get('source')!r} 不在 cu/std（spec §0）")
    if case.get("uplo") not in ("L", "U"):
        raise ContractError(f"{cid}: uplo={case.get('uplo')!r} 不在 L/U（spec §0 枚举）")
    n = case.get("n")
    if not isinstance(n, int) or n < 1:
        raise ContractError(f"{cid}: n={n!r} 非法")
    if not isinstance(case.get("seed"), int):
        raise ContractError(f"{cid}: seed={case.get('seed')!r} 非整数（seed 必须显式）")
    if case.get("lda") != n:
        raise ContractError(f"{cid}: lda={case.get('lda')!r} != n={n}，本片无 padding（spec §2.2）")
    if op.endswith("potrs"):
        nrhs = case.get("nrhs")
        if not isinstance(nrhs, int) or nrhs < 1:
            raise ContractError(f"{cid}: {op} 需要 nrhs>=1，得到 {nrhs!r}")
        if case.get("ldb") != n:
            raise ContractError(f"{cid}: ldb={case.get('ldb')!r} != n={n}，本片无 padding（spec §2.2）")


def _assert_finite(arr, name, case_id):
    if not np.isfinite(arr).all():
        raise ContractError(f"{case_id}: {name} 含 NaN/Inf")


def _assert_cast(a32, a64, name, case_id, dt32=F32):
    """spec §2.2 装包自检项：A32 == A64.astype(降型 dtype) 必须成立（B 同理；
    复数降型 complex64，s2 spec §4）。"""
    if a32.dtype != dt32 or a32.tobytes() != a64.astype(dt32).tobytes():
        raise ContractError(
            f"{case_id}: {name}32 != {name}64.astype({np.dtype(dt32).name})（spec §2.2 校验字段）")


def build_case_arrays(case):
    """按 case 构造 spec §2.2 规定的全部数组（复数 dtype 映射 s2 spec §4，字段名
    不变）。返回 {数组名: ndarray}，键序即 npz 存储序：A64,A32[,B64,B32],
    golden64,golden32。"""
    cid, op, uplo, n = case["case_id"], case["op"], case["uplo"], case["n"]
    dt64, dt32, gprefix = op_kinds(op)
    cx = op in COMPLEX_OPS
    rng = np.random.default_rng(case["seed"])           # 显式 seed，唯一随机源
    B64 = None
    if case["source"] == "cu":
        A64 = fill_hpd_cu(rng, n) if cx else fill_spd_cu(rng, n)
        if op.endswith("potrs"):
            # cu 同一 rand() 流先 A 后 B（spotrs_bench.cu:208-210；cpotrs_bench.cu:213-218）
            B64 = (fill_rhs_cu_complex(rng, n, case["nrhs"]) if cx
                   else fill_rhs_cu(rng, n, case["nrhs"]))
    else:
        dist = std_dist_of(case)
        A64 = gen_spd_std(rng, n, dist, complex_=cx)
        if cx:
            # s2 spec §4 Hermitian 语义「对角虚部按 0 处理并校验」：带 FMA 的 BLAS
            # 复 matmul 可在 B·Bᴴ 对角残留 ~1e-16 量级虚部，生成侧按契约置 0 后冻结
            #（不消耗 rng；zpotrf 本就按对角虚部为 0 的语义读入，golden 不受影响）
            np.fill_diagonal(A64.imag, 0.0)
        if op.endswith("potrs"):
            # verdict 脚本同一 rng 先 gen_spd 后 gen_rhs（实数 ch_potrs_verdict.py:
            # 93-96，复数分支 :71-74）；B64 用置 0 后的 A64，包内自洽
            X_true = gen_rhs_std(rng, n, case["nrhs"], dist, complex_=cx)
            B64 = A64 @ X_true                          # ch_potrs_verdict.py:96（复数 :74）
    if A64.dtype != dt64:
        raise ContractError(f"{cid}: A64 dtype={A64.dtype}，应为 {np.dtype(dt64).name}")
    if cx and np.diag(A64).imag.any():
        raise ContractError(f"{cid}: A64 对角虚部非 0（s2 spec §4 Hermitian 语义校验）")
    _assert_finite(A64, "A64", cid)
    A32 = A64.astype(dt32)
    _assert_cast(A32, A64, "A", cid, dt32)
    arrays = {"A64": A64, "A32": A32}
    if op.endswith("potrs"):
        _assert_finite(B64, "B64", cid)
        B32 = B64.astype(dt32)
        _assert_cast(B32, B64, "B", cid, dt32)
        arrays["B64"] = B64
        arrays["B32"] = B32
        golden64 = golden_potrs(A64, B64, uplo, cid, gprefix)
    elif op.endswith("potrf"):
        golden64 = golden_potrf(A64, uplo, cid, gprefix)
    else:
        golden64 = golden_potri(A64, uplo, cid, gprefix)
    # spec §2.2 行主序：LAPACK 包装返回 F 序数组，直接落盘会带 fortran_order=True；
    # 统一转 C 序再存（逐元素值不变，golden32 经 astype 随之继承 C 序）
    golden64 = np.ascontiguousarray(golden64)
    _assert_finite(golden64, "golden64", cid)
    golden32 = golden64.astype(dt32)
    _assert_finite(golden32, "golden32", cid)           # 降型溢出成 Inf 在此拦截（复数即 §4 降型校验）
    arrays["golden64"] = golden64
    arrays["golden32"] = golden32
    return arrays


# ---------------------------------------------------------------------------
# CLI 与主流程
# ---------------------------------------------------------------------------

def parse_ops(text):
    ops = tuple(s.strip() for s in text.split(",") if s.strip())
    bad = [o for o in ops if o not in SUPPORTED_OPS]
    if bad or not ops:
        raise ContractError(f"--ops 含不支持的算子 {bad}（可选：{','.join(SUPPORTED_OPS)}）")
    return ops


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog=GENERATOR,
        description="S1-Cholesky 包数据生成（spec §2.5 CLI；产 cases/*.npz 与 cases/index.json 骨架）",
    )
    parser.add_argument("--canonical", required=True, help="canonical_cases.json 路径（波 0 冻结件）")
    parser.add_argument("--select", choices=("s1", "all"), default="s1",
                        help="case 选择：s1=仅 s1_subset 子集（默认，保持既有行为）；all=全量")
    parser.add_argument("--out", required=True, help="输出目录（staging；产物落 <out>/cases/）")
    parser.add_argument("--ops", default=",".join(SUPPORTED_OPS),
                        help="逗号分隔的算子子集，默认两册六算子（与 canonical 实际所含取交集）")
    args = parser.parse_args(argv)

    ops = parse_ops(args.ops)
    canonical_path = Path(args.canonical)
    with canonical_path.open(encoding="utf-8") as fh:
        canonical = json.load(fh)
    if "cases" not in canonical:
        raise ContractError(f"{canonical_path}: 顶层缺 cases 数组（spec §2.1）")

    selected = [c for c in canonical["cases"]
                if c.get("op") in ops
                and (args.select == "all" or c.get("s1_subset") is True)]
    if not selected:
        raise ContractError(
            f"canonical_cases 里没有 ops={','.join(ops)} 的可选 case（--select={args.select}）")
    for case in selected:
        validate_case(case)

    cases_dir = Path(args.out) / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)

    index_cases = []
    for case in selected:                               # 保 canonical 原序（spec §2.1 保序编号）
        cid = case["case_id"]
        arrays = build_case_arrays(case)
        np.savez(cases_dir / f"{cid}.npz", **arrays)
        entry = dict(case)                              # canonical 字段逐 case 原样携带
        entry["npz"] = f"cases/{cid}.npz"
        entry["arrays"] = list(arrays)
        entry["ratio_cpu"] = None                       # B2 回填（spec §2.4，波 1.5）
        entry["ratio_cpu_status"] = None                # B2 回填："ok"|"prep_failed"
        index_cases.append(entry)
        print(f"[gen] {cid}: n={case['n']} uplo={case['uplo']} "
              f"source={case['source']} arrays={','.join(arrays)}")

    import scipy
    index = {
        "schema": "solver-s1/cases-index@1",
        "spec": "dev-doc/solver/solver-s1-cholesky-spec.md#2.2",
        "generator": {"name": GENERATOR, "ver": GENERATOR_VER},
        "canonical": {"file": canonical_path.name,
                      "schema": canonical.get("schema"),
                      "frozen_at": canonical.get("frozen_at")},
        "ops": list(ops),
        "env": {"numpy": np.__version__, "scipy": scipy.__version__},
        "cases": index_cases,
    }
    index_path = cases_dir / "index.json"
    with index_path.open("w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    per_op = {op: sum(1 for c in selected if c["op"] == op) for op in ops}
    print(f"[gen] 完成：{len(selected)} case（"
          + "，".join(f"{op}={cnt}" for op, cnt in per_op.items())
          + f"）→ {cases_dir}；index.json 的 ratio_cpu 留待 B2 回填")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ContractError as exc:
        print(f"[gen] 契约/前置错误：{exc}", file=sys.stderr)
        sys.exit(2)
