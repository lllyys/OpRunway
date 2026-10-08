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

S3 批量增量（契约：dev-doc/solver/solver-s3-batched-spec.md，下称 S3 spec，§2/§3/§5）：

- 算子册扩入 spotrfBatched/spotrsBatched/cpotrfBatched/cpotrsBatched。构造转写自
  0923 两册 batched bench cu：batched 的 fillSPD 与单算子版逐字一致（实数
  spotrfbatched_bench.cu:169-181 / spotrsbatched_bench.cu:165-177；复数（HPD）
  cpotrfbatched_bench.cu:161-175 / cpotrsbatched_bench.cu:159-173），故逐矩阵复用
  本文件 fill_spd_cu / fill_hpd_cu，同一 rand() 流跨批续抽（逐矩阵循环：
  spotrfbatched_bench.cu:227、cpotrfbatched_bench.cu:232、spotrsbatched_bench.cu:210、
  cpotrsbatched_bench.cu:214）。
- potrsBatched 的 A/B 次序以 cu 实测为准（S3 spec §2，评审核实「先全部 A 再全部 B」）：
  先逐矩阵 fillSPD 填满全部 A（spotrsbatched_bench.cu:210 / cpotrsbatched_bench.cu:214），
  再一条线性循环填满全部 B（spotrsbatched_bench.cu:211-212 实数 rand()%10000/100-50；
  cpotrsbatched_bench.cu:215-219 复数每元素先 .x 后 .y），cntB=ldb*1 即 nrhs 固定 1
  （spotrsbatched_bench.cu:208 / cpotrsbatched_bench.cu:212）。线性 B 循环按矩阵
  i 的 [i*n,(i+1)*n) 分段，与逐矩阵 fill_rhs_cu(rng,n,1) 续抽逐位等价（每元素一次
  抽取，次序相同），故 B 段同样复用单算子转写函数。
- npz 批维堆叠（S3 spec §2）：A64/A32=(batch,n,n)、B64/B32=(batch,n,1)、golden 同形
  （potrf 族 (batch,n,n)、potrs 族 (batch,n,1)），字段名与 dtype 映射不变；降型校验
  逐 chunk 断言。golden 仍为逐矩阵 FP64 前缀链路（实数 d/复数 z）。
- A0 抽样（HT-2，2026-09 裁定，替代旧「全量批维分块/并行生成」）：每 batch 用例只造
  k=min(5,batch) 个代表内容矩阵（build_batched_contents）。内容/摆放/填充三条子流
  全部从 case seed 派生（禁独立播种）：内容流 = default_rng(seed) 按序取 k 个 A 再
  k 个 B（即同 case 取 batch=k 的旧全量直算；A 段亦等于任意 batch 全量流头部第
  i 个矩阵，可对账）；摆放流 = SeedSequence(entropy=seed,
  spawn_key=(1,))，permutation(batch) 前 k 个槽位升序即内容 c 的代表槽 rep_slot；
  填充流 = spawn_key=(2,)，剩余槽位升序逐一 integers(0,k) 指派内容。槽位映射固化在
  index 条目的 sample_map（[{"content_idx","rep_slot","slots"}] 列表，JSON 友好）。
  **不落 npz**（supplement 24e）：现场构造现场使用，执行器/DUT 挂钩逐段取
  expand_sampled_rows 的槽位区间喂入。判定侧先余槽 bit-wise 一致性（同内容槽位
  输出必须逐位相等），后代表槽逐内容残差判定（repo-task-solver-accept 的 A0 驱动）；
  无 sample_map 的旧包走全遍历兼容分支（_build_batched_arrays 全量批维逐矩阵）。
  旧分块/并行机械（边界态预扫描、流式 npz、worker 池）随 A0 摘除。
- ratio_cpu（S3 spec §3 / HT-2）：逐内容 c_i 经 fill_ratio_cpu.run_chain 同一入口
  计算（残差唯一实现仍在 criteria/verdict.residual_ratio），k 个值列表入 index 条目
  （仅作参考，判定侧现场重算）；逐内容 prep_failed 记 null 并计数。criteria 不可达
  （纯脚本包内独立运行、无兄弟 skill 目录）时如实跳过：ratio_cpu=null 列表、index 记
  ratio_cpu_status="not_computed" 并打印说明，不静默降级——判定不依赖此字段。

CLI（spec §2.5 基础上 S3 增批量；--ops 缺省三册并集并与 canonical 实际所含取交集）：
    gen_data_cholesky.py --canonical <json> --out <dir> [--ops spotrf,...,cpotrsBatched]
                         [--select s1|all] [--criteria <dir>] [--selftest]
产出：单算子 <out>/cases/<case_id>.npz；批量 case 只产 index 条目（A0 不落数组）。
--selftest 不读 canonical，就地跑 A0 抽样自测（sample_map 不变量、内容流头部对账、
展开往返）。
"""

import argparse
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
from numpy.lib import format as npy_format

GENERATOR = "gen_data_cholesky.py"
GENERATOR_VER = "s3-b6"  # HT-9：批量 info 契约混合 case（B3，逐内容 k_expected 入 index）；承 s3-b5-r1
REAL_OPS = ("spotrf", "spotrs", "spotri")
COMPLEX_OPS = ("cpotrf", "cpotrs", "cpotri")
# S3 批量四算子（op 名与判定卡一致取 camelCase；potri 无 batched 接口）
BATCHED_OPS = ("spotrfBatched", "spotrsBatched", "cpotrfBatched", "cpotrsBatched")
SUPPORTED_OPS = REAL_OPS + COMPLEX_OPS
ALL_OPS = SUPPORTED_OPS + BATCHED_OPS
CHUNK_BYTES = 512 * 1024 * 1024   # 旧包 npz 分块直读的工作集上限（fill_ratio_cpu 消费）
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
# HT-17 核对（2026-09-26）：新书 experimental_standard.md「用例生成」要求均匀与
# 正态各占一半（U(-5,5) / N(μ∈[-5,5], σ∈[0.1,2])）——每算子恰 2 条 std（9001/9002
# = 1 均匀 + 1 正态，实/复两册同构共 12 条 6+6 各半），组内分布参数与标准表逐项
# 一致，复数实/虚同分布（见 tests/test_std_distribution.py 固化断言）；登记落点
# 即本表 + freeze_canonical.STD_CASES_BASE，冻结产物不另加字段（real 册与 S1
# 冻结件逐字节一致的回归保护不动）。
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

def validate_batched_case(case):
    """批量 case 进场校验（S3 spec §1/§2）：batch≥1、lda==n、（potrs 族）nrhs==1 且
    ldb==n（官方仅支持 nrhs=1）、无 std 蓝本（source 只能 cu）。"""
    cid = case.get("case_id", "<无 case_id>")
    op = case["op"]
    if case.get("source") != "cu":
        raise ContractError(f"{cid}: batched 无 std 蓝本，source 只能是 cu（S3 spec §1）")
    if case.get("uplo") not in ("L", "U"):
        raise ContractError(f"{cid}: uplo={case.get('uplo')!r} 不在 L/U")
    n = case.get("n")
    if not isinstance(n, int) or n < 1:
        raise ContractError(f"{cid}: n={n!r} 非法")
    batch = case.get("batch")
    if not isinstance(batch, int) or batch < 1:
        raise ContractError(f"{cid}: batch={batch!r} 非法（批量 case 必须 batch>=1）")
    if not isinstance(case.get("seed"), int):
        raise ContractError(f"{cid}: seed={case.get('seed')!r} 非整数（seed 必须显式）")
    if case.get("lda") != n:
        raise ContractError(f"{cid}: lda={case.get('lda')!r} != n={n}，本片无 padding")
    if batched_base_op(op).endswith("potrs"):
        if case.get("nrhs") != 1:
            raise ContractError(
                f"{cid}: nrhs={case.get('nrhs')!r}，potrsBatched 官方仅支持 nrhs=1（S3 spec §1）")
        if case.get("ldb") != n:
            raise ContractError(f"{cid}: ldb={case.get('ldb')!r} != n={n}，本片无 padding")


def validate_case(case):
    """进场校验：字段齐全、枚举合法、本片无 padding（spec §2.1/§2.2）。"""
    cid = case.get("case_id", "<无 case_id>")
    op = case.get("op")
    if op in BATCHED_OPS:
        return validate_batched_case(case)
    if op not in SUPPORTED_OPS:
        raise ContractError(f"{cid}: op={op!r} 不在 {ALL_OPS}")
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
    golden64,golden32。批量 case（S3）返回批维堆叠数组——本函数同时是 verify
    现场重生成的协议入口（render_verify 的 _materialize_case 调 validate_case +
    build_case_arrays），批量分支见 _build_batched_arrays。"""
    if case["op"] in BATCHED_OPS:
        return _build_batched_arrays(case)
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
# 单算子 info 契约用例（HT-8，B2：非正定变体 / 奇异因子 / 非法参数三支）
# ---------------------------------------------------------------------------

INFO_PROBE_OF = {"potrf": "non_posdef", "potri": "singular_factor",
                 "potrs": "bad_param_uplo"}
INFO_CASES_PER_OP = 3


def derive_info_cases(cases, require_s1=True):
    """HT-8 用例侧（B2）：每单算子从其派生基座取 %d 个 info 契约变体。

    选择规则（确定性，随 index 交付供对账）：该算子基座按 (n, case_id) 升序，
    跳过最小例后取随后至多 %d 例（「3 个中等规模用例」）；非正定位置 k 按变体序取
    [1, n//2, n]（构造时即知 k_expected，B2 原话「k_min 构造时即已知」）。
    基座：require_s1=True（默认，v3 前行为）= s1 子集；False（包内 canonical 顶层
    package_scope=="full"，2026-09-27 全量精度用例决策）= 全部精度 case——info 为
    契约抽验性质，条数恒定 INFO_CASES_PER_OP，只随基座扩池换 n/nrhs 样本。
    产出条目只含描述字段（数组由 build_info_arrays 现场构造，A0/纯脚本同策）。
    """ % (INFO_CASES_PER_OP, INFO_CASES_PER_OP)
    by_op = {}
    for c in cases:
        if c.get("op") not in SUPPORTED_OPS:
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
        for j, base in enumerate(picks):
            n = base["n"]
            ks = [kk for kk in (1, n // 2, n) if 1 <= kk <= n]
            k = ks[j % len(ks)]
            probe = INFO_PROBE_OF["potrf" if "potrf" in op else
                                  "potri" if "potri" in op else "potrs"]
            entry = {k: v for k, v in base.items() if k != "s1_subset"}
            entry.update({
                "case_id": f"{base['case_id']}-info{j + 1}",
                "base_case_id": base["case_id"],
                "case_purpose": "info", "info_probe": probe,
                "k_expected": (-1 if probe == "bad_param_uplo" else k),
            })
            out.append(entry)
    return out


def build_info_arrays(entry, base_arrays):
    """HT-8：info 用例数组（B2 判定方式——非正定/奇异/参数错下残差无意义，
    判定只比 info，故**不产 golden**；arrays 仅输入面，schema 键名不变）。

    构造性自检（fail-closed）：本机 FP64 d/z 前缀链路当场验证 info==k_expected，
    不成立即 ContractError——构造失败比漏检危险。

    - non_posdef（potrf 族）：base 正定 A64 第 k 阶对角减大数 (n+1)·‖A‖₁+1，
      前 k-1 阶顺序主子式不动 → {d|z}potrf 恰在第 k 步失败，info=k；
    - singular_factor（potri 族）：potri 接口输入是因子（cholesky README §3：
      「输入因子 F（半三角，potrf 输出）+ uplo」）——取 base 正定 A64 的
      {d|z}potrf 因子，第 k 阶对角置 0（「某阶顺序主子式为零」），
      {d|z}potri 恰报 info=k；A64/A32 存的就是该因子；
    - bad_param_uplo（potrs 族）：base 正定数组原样（参数错与矩阵无关），DUT 按
      probe 以非法 uplo 字符调 {d|z}potrs → info=-1（uplo 是第 1 形参，
      k_expected=-1，B2「不涉及 k」）。
    """
    cid, op, uplo = entry["case_id"], entry["op"], entry["uplo"]
    _, dt32, gprefix = op_kinds(op)
    probe, k_expected = entry["info_probe"], int(entry["k_expected"])
    lapack = _lapack()
    if probe == "non_posdef":
        A64 = np.array(base_arrays["A64"], copy=True)
        n = A64.shape[0]
        if not 1 <= k_expected <= n:
            raise ContractError(f"{cid}: k_expected={k_expected} 越界（n={n}）")
        A64[k_expected - 1, k_expected - 1] -= (n + 1) * float(np.abs(A64).sum(axis=0).max()) + 1.0
        _, info = getattr(lapack, gprefix + "potrf")(A64, lower=(uplo == "L"), clean=1)
        if info != k_expected:
            raise ContractError(
                f"{cid}: 非正定构造自检失败：{gprefix}potrf info={info} != k_expected="
                f"{k_expected}（对角减大数应恰在第 {k_expected} 阶失败）")
        arrays = {"A64": np.ascontiguousarray(A64), "A32": A64.astype(dt32)}
    elif probe == "singular_factor":
        F64, info = getattr(lapack, gprefix + "potrf")(
            base_arrays["A64"], lower=(uplo == "L"), clean=1)
        _check_info(info, gprefix + "potrf", cid)
        n = F64.shape[0]
        if not 1 <= k_expected <= n:
            raise ContractError(f"{cid}: k_expected={k_expected} 越界（n={n}）")
        F64[k_expected - 1, k_expected - 1] = 0.0    # 精确 0 阶跃：存储即读出，无 FP 碰运气
        _, info = getattr(lapack, gprefix + "potri")(F64.copy(), lower=(uplo == "L"))
        if info != k_expected:
            raise ContractError(
                f"{cid}: 奇异因子构造自检失败：{gprefix}potri info={info} != k_expected="
                f"{k_expected}（第 {k_expected} 阶对角置 0 应恰报 {k_expected}）")
        arrays = {"A64": np.ascontiguousarray(F64), "A32": F64.astype(dt32)}
    elif probe == "bad_param_uplo":
        arrays = {kk: base_arrays[kk] for kk in ("A64", "A32", "B64", "B32")
                  if kk in base_arrays}
    else:
        raise ContractError(f"{cid}: 未知 info_probe: {probe!r}")
    for name in arrays:
        _assert_finite(arrays[name], name, cid)
        if name.endswith("32"):
            _assert_cast(arrays[name], arrays[name[:-2] + "64"], name[0], cid,
                         dt32)
    return arrays


BATCHED_INFO_CASES_PER_OP = 1


def derive_batched_info_cases(cases, require_s1=True):
    """HT-9 用例侧（B3）：每批量算子从其派生基座取 %d 个 info 契约混合 case。

    选择规则（确定性，随 index 交付供对账）：该算子基座按 (n, case_id) 升序，
    跳过最小例取第 2 例（「中等规模 case」）。基座与单矩阵侧同策：
    require_s1=True（默认）= s1 子集；False（package_scope=="full"）= 全部精度
    case（info 为契约抽验，条数恒定，只换派生样本）。A0 抽样下「1~2 个矩阵非正定」按
    「1~2 个代表内容」解释（卡面口径，发包前与验收方确认一句；k_expected 按代表
    内容下标给出，槽位经 sample_map 展开）：

    - *potrfBatched（infoArray 分型）：代表内容 0 与 1（k<2 时仅内容 0）构造非正定
      （第 k 阶对角减大数，k 取 [1, n//2] 去重，构造时即知），其余内容保持正定——
      k_expected 为逐内容 k 值列表（正定内容记 0），验证 infoArray 逐矩阵独立写入；
    - *potrsBatched（标量 info 仅报参数错）：k_expected=-1 标量，不涉及 k
      （B2 同款非法参数路径，infoArray 正值分支本接口不存在）。

    产出条目只含描述字段（数组由 build_batched_info_arrays 现场构造，A0 同策
    不落数组）。
    """ % BATCHED_INFO_CASES_PER_OP
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
        probe = INFO_PROBE_OF["potrf" if "potrf" in op else
                              "potri" if "potri" in op else "potrs"]
        entry = {k: v for k, v in base.items() if k != "s1_subset"}
        entry.update({
            "case_id": f"{base['case_id']}-info1",
            "base_case_id": base["case_id"],
            "case_purpose": "info", "info_probe": probe,
        })
        if probe == "non_posdef":
            k = sampled_content_count(base["batch"])
            n = base["n"]
            ks = [kk for kk in (1, n // 2) if 1 <= kk <= n]
            ks = sorted(set(ks))[:k] if k < 2 else sorted(set(ks))
            while len(ks) < k:                      # 未选中的代表内容保持正定 → 0
                ks.append(0)
            entry["k_expected"] = ks
        else:
            entry["k_expected"] = -1
        out.append(entry)
    return out


def build_batched_info_arrays(entry):
    """HT-9：批量 info 契约用例数组（B3 判定方式——info 契约只比 info，**不产
    golden**；A0 条目本不落数组，本函数供 gen 自检 / stream_check / verify 副本
    判定时现场构造）。返回 {A64,A32[,B64,B32]}，批维 k（代表内容）。

    构造性自检（fail-closed）：非正定内容逐内容 {d|z}potrf 当场验证 info==k_expected、
    正定内容验证 info==0，不成立即 ContractError——构造失败比漏检危险。
    - non_posdef（*potrfBatched）：k_expected[c]>0 的代表内容第 k 阶对角减大数
      (n+1)·‖A_c‖₁+1（B2 同款），其余内容不动；
    - bad_param_uplo（*potrsBatched）：输入原样（参数错与矩阵无关，DUT 按 probe
      以非法 uplo 字符调接口 → 标量 info=-1）。"""
    cid, op, uplo = entry["case_id"], entry["op"], entry["uplo"]
    base_op = batched_base_op(op)
    _, dt32, gprefix = op_kinds(base_op)
    probe = entry["info_probe"]
    inputs = _batched_inputs(entry)
    if probe == "bad_param_uplo":
        return inputs
    if probe != "non_posdef":
        raise ContractError(f"{cid}: 未知批量 info_probe: {probe!r}")
    k_expected = [int(x) for x in entry["k_expected"]]
    A64 = np.array(inputs["A64"], copy=True)
    n = A64.shape[1]
    lapack = _lapack()
    for c, k in enumerate(k_expected):
        if not 0 <= k <= n:
            raise ContractError(f"{cid}: 内容 {c} 的 k_expected={k} 越界（n={n}）")
        if k == 0:
            _, info = getattr(lapack, gprefix + "potrf")(
                A64[c].copy(), lower=(uplo == "L"), clean=1)
            if info != 0:
                raise ContractError(
                    f"{cid}: 正定内容 {c} 自检失败：{gprefix}potrf info={info} != 0")
            continue
        A64[c][k - 1, k - 1] -= (n + 1) * float(np.abs(A64[c]).sum(axis=0).max()) + 1.0
        _, info = getattr(lapack, gprefix + "potrf")(
            A64[c].copy(), lower=(uplo == "L"), clean=1)
        if info != k:
            raise ContractError(
                f"{cid}: 非正定构造自检失败：内容 {c} 的 {gprefix}potrf info={info} "
                f"!= k_expected={k}（对角减大数应恰在第 {k} 阶失败）")
    inputs["A64"] = np.ascontiguousarray(A64)
    inputs["A32"] = A64.astype(dt32)
    _assert_cast(inputs["A32"], inputs["A64"], "A", cid, dt32)
    for name in ("A64", "A32"):
        _assert_finite(inputs[name], name, cid)
    return inputs


# ---------------------------------------------------------------------------
# S3 批量算子：分块/并行生成（S3 spec §2/§3；模块 docstring「S3 批量增量」段）
# ---------------------------------------------------------------------------

def batched_base_op(op):
    """批量算子的单算子基名：spotrfBatched → spotrf（判定/链路语义随基名走）。"""
    return op[:-len("Batched")]


# ---------------------------------------------------------------------------
# A0 抽样通路（HT-2；模块 docstring「S3 批量增量」段。每 batch 用例只造
# k=min(5,batch) 个代表内容矩阵；不落 npz，现场构造现场使用）
# ---------------------------------------------------------------------------

A0_MAX_CONTENTS = 5


def sampled_content_count(batch):
    """A0 代表内容数：k = min(5, batchSize)（HT-2）。"""
    return min(A0_MAX_CONTENTS, batch)


def derive_sample_map(seed, batch, k=None):
    """A0 抽样映射（HT-2）。摆放/填充两条子流从 case seed 派生（禁独立播种），
    内容流见 build_batched_contents：

    - 摆放流：SeedSequence(entropy=seed, spawn_key=(1,)) → permutation(batch) 取前
      k 个槽位升序排列，第 c 个升序槽位即内容 c 的代表槽 rep_slot；
    - 填充流：SeedSequence(entropy=seed, spawn_key=(2,)) → 剩余槽位按升序逐一抽
      integers(0, k) 指派内容（无剩余槽时不消费该流）。

    返回列表形态 [{"content_idx": c, "rep_slot": r, "slots": [...]}]（不用整型对象
    键，JSON round-trip 安全）。不变量在此断言：全部槽位恰覆盖一次、rep_slot 是
    slots[c] 首元素、每个内容至少一个槽位。同 (seed, batch) 重跑逐项相等。"""
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


def _batched_inputs(case):
    """A0 批量输入段（HT-9 抽出共用）：k=min(5,batch) 个代表矩阵的
    {A64,A32[,B64,B32]}（内容流 = default_rng(seed) 按序取 k 个 A 再 k 个 B，
    与旧全量直算流头部逐位一致）。build_batched_contents 在此之上补 golden；
    build_batched_info_arrays 在此之上做非正定改造（HT-9）。"""
    cid, op = case["case_id"], case["op"]
    base_op = batched_base_op(op)
    dt64, dt32, _ = op_kinds(base_op)
    cx = base_op in COMPLEX_OPS
    potrs = base_op.endswith("potrs")
    n, k = case["n"], sampled_content_count(case["batch"])
    nrhs = case["nrhs"] if potrs else None
    rng = np.random.default_rng(case["seed"])
    A64 = np.empty((k, n, n), dtype=dt64)
    for i in range(k):
        A64[i] = fill_hpd_cu(rng, n) if cx else fill_spd_cu(rng, n)
    _assert_finite(A64, "A64", cid)
    if cx and np.diagonal(A64, axis1=1, axis2=2).imag.any():
        raise ContractError(f"{cid}: A64 对角虚部非 0（A0 内容）")
    A32 = A64.astype(dt32)
    _assert_cast(A32, A64, "A", cid, dt32)
    out = {"A64": A64, "A32": A32}
    if potrs:
        B64 = np.empty((k, n, nrhs), dtype=dt64)
        for i in range(k):
            B64[i] = fill_rhs_cu_complex(rng, n, nrhs) if cx else fill_rhs_cu(rng, n, nrhs)
        _assert_finite(B64, "B64", cid)
        B32 = B64.astype(dt32)
        _assert_cast(B32, B64, "B", cid, dt32)
        out["B64"] = B64
        out["B32"] = B32
    return out


def build_batched_contents(case):
    """A0 内容数组：k=min(5,batch) 个代表矩阵（HT-2）。内容流 = default_rng(seed)
    按序取 k 个 A 再 k 个 B——与同 case 取 batch=k 的旧全量直算（_build_batched_arrays）
    逐位一致（同一 stream 同序消费，可对账）；A 段另等于任意 batch 全量流头部第 i 个
    矩阵。golden 逐内容 FP64 前缀链路（实数 d/复数 z）。返回
    {A64,A32[,B64,B32],golden64,golden32}，批维为 k。"""
    cid, op, uplo = case["case_id"], case["op"], case["uplo"]
    base_op = batched_base_op(op)
    dt64, dt32, gprefix = op_kinds(base_op)
    potrs = base_op.endswith("potrs")
    n, k = case["n"], sampled_content_count(case["batch"])
    contents = _batched_inputs(case)
    out = dict(contents)
    if potrs:
        g = np.empty((k, n, case["nrhs"]), dtype=dt64)
        for i in range(k):
            g[i] = golden_potrs(contents["A64"][i], contents["B64"][i], uplo, f"{cid}#c{i}", gprefix)
    else:
        g = np.empty((k, n, n), dtype=dt64)
        for i in range(k):
            g[i] = golden_potrf(contents["A64"][i], uplo, f"{cid}#c{i}", gprefix)
    _assert_finite(g, "golden64", cid)
    golden32 = g.astype(dt32)
    _assert_finite(golden32, "golden32", cid)
    out["golden64"] = g
    out["golden32"] = golden32
    return out


def expand_sampled_rows(contents, sample_map, lo, hi):
    """按 sample_map 把 k 个内容复制到批维槽位区间 [lo, hi)（HT-2：现场构造现场
    使用——执行器/DUT 挂钩逐段取数，整批不必驻留内存）。返回与 contents 同键的
    数组，批维为 hi-lo。区间内每个槽位必须恰有一次内容指派，缺指派即报错。"""
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


# ---- 旧包 npz 直读（A0 起批量不再落 npz；保留供 fill_ratio_cpu 消费旧包） ----

def npz_stored_member_meta(path, name):
    """未压缩 npz 成员的行级随机读元数据 {rows_offset, dtype, shape}。
    np.savez 与旧 _NpzStreamWriter 都是 ZIP_STORED，成员原始字节可按文件偏移直读；
    压缩成员不支持（fail-closed）。供 fill_ratio_cpu 批量分块读旧包用。"""
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo(name + ".npy")
        if info.compress_type != zipfile.ZIP_STORED:
            raise ContractError(f"{path}:{name} 非 ZIP_STORED 成员，行级直读不支持")
        header_offset = info.header_offset
    with open(path, "rb") as fh:
        fh.seek(header_offset)
        lh = fh.read(30)                      # ZIP 本地文件头定长 30B
        if lh[:4] != b"PK\x03\x04":
            raise ContractError(f"{path}: 本地文件头魔数不符（偏移 {header_offset}）")
        name_len = int.from_bytes(lh[26:28], "little")
        extra_len = int.from_bytes(lh[28:30], "little")
        fh.seek(header_offset + 30 + name_len + extra_len)
        version = npy_format.read_magic(fh)
        if version == (1, 0):
            shape, fortran, dtype = npy_format.read_array_header_1_0(fh)
        elif version == (2, 0):
            shape, fortran, dtype = npy_format.read_array_header_2_0(fh)
        else:
            raise ContractError(f"{path}:{name} npy 版本 {version} 不支持")
        if fortran:
            raise ContractError(f"{path}:{name} fortran_order 成员不支持（本片一律 C 序）")
        return {"rows_offset": fh.tell(), "dtype": dtype, "shape": tuple(shape)}


def read_npz_member_rows(path, meta, row0, rows):
    """按批维行区间 [row0, row0+rows) 直读 ZIP_STORED npz 成员，返回 C 序数组。"""
    shape, dtype = meta["shape"], meta["dtype"]
    row_elems = int(np.prod(shape[1:], dtype=np.int64)) if len(shape) > 1 else 1
    row_bytes = row_elems * dtype.itemsize
    with open(path, "rb") as fh:
        fh.seek(meta["rows_offset"] + row0 * row_bytes)
        buf = fh.read(rows * row_bytes)
    if len(buf) != rows * row_bytes:
        raise ContractError(f"{path}: 行区间 [{row0},{row0 + rows}) 读取不足")
    return np.frombuffer(buf, dtype=dtype).reshape((rows,) + tuple(shape[1:]))


# ---- ratio 链（逐矩阵 c_i；实现唯一来源见 fill_ratio_cpu / criteria） ----

_RATIO_CTX = {}


def resolve_criteria(arg):
    """ratio 链可达性判定：--criteria 显式给出则必须有效（fail-closed）；缺省按镜像树
    相对布局 ../../repo-task-solver-accept/criteria 探测，且要求同目录有
    fill_ratio_cpu.py（链路入口）。不可达返回 None（纯脚本包内独立运行的常态，
    调用方如实记 not_computed，不静默降级）。"""
    here = Path(__file__).resolve().parent
    if arg:
        crit = Path(arg)
        if not (crit / "verdict.py").is_file():
            raise ContractError(f"--criteria {crit} 下没有 verdict.py")
        if not (here / "fill_ratio_cpu.py").is_file():
            raise ContractError(
                f"--criteria 已给出但 {here} 下没有 fill_ratio_cpu.py（ratio 链入口）")
        return str(crit)
    crit = here.parent.parent / "repo-task-solver-accept" / "criteria"
    if (crit / "verdict.py").is_file() and (here / "fill_ratio_cpu.py").is_file():
        return str(crit)
    return None


def _ratio_ctx(criteria_dir):
    """进程内加载并缓存 ratio 链（fill_ratio_cpu + criteria verdict + lapack）。"""
    if _RATIO_CTX.get("dir") != criteria_dir:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import fill_ratio_cpu as frc
        _RATIO_CTX.update(dir=criteria_dir, frc=frc,
                          verdict=frc.load_verdict(criteria_dir),
                          lapack=frc._lapack())
    return _RATIO_CTX


def _ratio_rows(criteria_dir, base_op, uplo, cid, row0, arrays_rows, rows):
    """逐矩阵 c_i（S3 spec §3：不聚合）。经 fill_ratio_cpu.run_chain 同一入口——
    低精度准备链与残差实现都只此一份。prep_failed 记 NaN 并计数。"""
    ctx = _ratio_ctx(criteria_dir)
    frc = ctx["frc"]
    out = np.empty(rows, dtype=np.float64)
    pf = 0
    for i in range(rows):
        arrays_i = {k: v[i] for k, v in arrays_rows.items()}
        case_i = {"case_id": f"{cid}#m{row0 + i}", "op": base_op, "uplo": uplo}
        try:
            out[i] = frc.run_chain(ctx["verdict"], ctx["lapack"], case_i, arrays_i)
        except frc.PrepFailed as exc:
            out[i] = np.nan
            pf += 1
            print(f"[gen] {cid}#m{row0 + i}: ratio 准备链失败（{exc}）——记 NaN", file=sys.stderr)
    return out, pf


def gen_batched_case(case, criteria_dir):
    """生成一个批量 case 的 A0 抽样产物（HT-2；不落 npz——supplement 24e：现场构造
    现场使用）。返回 (index 条目增量 dict, 运行信息 dict)。

    - 内容数组：build_batched_contents（k=min(5,batch) 个代表矩阵，内容流同旧全量
      流头部）；
    - 槽位映射：derive_sample_map（摆放/填充子流从 case seed 派生），固化进 index
      条目的 sample_map；
    - ratio：逐内容 c_i（_ratio_rows 同一入口），k 个值列表入 index（prep_failed 记
      null 并计数）；criteria 不可达时如实 not_computed，不静默降级。

    代表槽逐内容残差判定与余槽 bit-wise 一致性核对比照在判定侧（repo-task-solver-accept
    的 A0 驱动）进行：判定时现场重造同一 contents，对被测全批输出先验「同内容槽位
    输出逐位相等」，后按代表槽逐内容判定。"""
    cid, op, uplo = case["case_id"], case["op"], case["uplo"]
    base_op = batched_base_op(op)
    batch, n = case["batch"], case["n"]
    k = sampled_content_count(batch)
    contents = build_batched_contents(case)
    sample_map = derive_sample_map(case["seed"], batch, k)
    ratio = None
    pf = 0
    status = "not_computed"
    if criteria_dir is not None:
        rows = {name: contents[name] for name in ("A64", "A32", "B64", "B32")
                if name in contents}
        vals, pf = _ratio_rows(criteria_dir, base_op, uplo, cid, 0, rows, k)
        ratio = [None if np.isnan(v) else float(v) for v in vals]
        ok = vals[~np.isnan(vals)]
        status = "ok" if ok.size else "prep_failed"
    entry = {"materialize": "gen", "arrays": list(contents),
             "sampled_contents": k, "sample_map": sample_map,
             "ratio_cpu": ratio, "ratio_cpu_status": status}
    if pf:
        entry["ratio_cpu_prep_failed"] = pf
    info = {"mode": "a0", "contents": k, "batch": batch, "n": n, "uplo": uplo}
    return entry, info


# ---- 自测钩子（A0 抽样自测；容器内运行——本机纪律仅 py_compile） ----

def _build_batched_arrays(case):
    """批量 case 的全量批维直算（旧包兼容形态；A0 起新包不再落全量数组）。一条
    default_rng(seed) 连续流，先全部 A 再全部 B（cu 实测次序），golden 逐矩阵 FP64
    前缀链路，数组批维堆叠（S3 spec §2）。消费方：verify 对无 sample_map 旧包的
    全遍历兼容分支（经 build_case_arrays 分发）与 --selftest 的内容流头部对账参照
    （build_batched_contents 第 i 个内容 == 本函数第 i 个矩阵）。"""
    cid, op, uplo = case["case_id"], case["op"], case["uplo"]
    base_op = batched_base_op(op)
    dt64, dt32, gprefix = op_kinds(base_op)
    cx = base_op in COMPLEX_OPS
    potrs = base_op.endswith("potrs")
    n, batch = case["n"], case["batch"]
    nrhs = case["nrhs"] if potrs else None
    rng = np.random.default_rng(case["seed"])
    A64 = np.empty((batch, n, n), dtype=dt64)
    for i in range(batch):
        A64[i] = fill_hpd_cu(rng, n) if cx else fill_spd_cu(rng, n)
    _assert_finite(A64, "A64", cid)
    if cx and np.diagonal(A64, axis1=1, axis2=2).imag.any():
        raise ContractError(f"{cid}: A64 对角虚部非 0（批量直算）")
    A32 = A64.astype(dt32)
    _assert_cast(A32, A64, "A", cid, dt32)
    out = {"A64": A64, "A32": A32}
    if potrs:
        B64 = np.empty((batch, n, nrhs), dtype=dt64)
        for i in range(batch):
            B64[i] = fill_rhs_cu_complex(rng, n, nrhs) if cx else fill_rhs_cu(rng, n, nrhs)
        _assert_finite(B64, "B64", cid)
        B32 = B64.astype(dt32)
        _assert_cast(B32, B64, "B", cid, dt32)
        out["B64"] = B64
        out["B32"] = B32
        g = np.empty((batch, n, nrhs), dtype=dt64)
        for i in range(batch):
            g[i] = golden_potrs(A64[i], B64[i], uplo, f"{cid}#m{i}", gprefix)
    else:
        g = np.empty((batch, n, n), dtype=dt64)
        for i in range(batch):
            g[i] = golden_potrf(A64[i], uplo, f"{cid}#m{i}", gprefix)
    _assert_finite(g, "golden64", cid)
    golden32 = g.astype(dt32)
    _assert_finite(golden32, "golden32", cid)
    out["golden64"] = g
    out["golden32"] = golden32
    return out


def run_selftest():
    """A0 抽样自测（HT-2）：四算子小例（n=5, batch=37）上断言——
    ① sample_map 派生确定性：同 (seed, batch) 重跑逐项相等；不变量（覆盖/无重复/
      rep_slot 为首槽）由 derive_sample_map 内建断言把守；
    ② 内容流头部对账：build_batched_contents 第 i 个内容与旧全量流（
      _build_batched_arrays）第 i 个矩阵逐位一致（内容流取头不换种子）；
    ③ 展开往返：expand_sampled_rows 全区间展开后每个槽位 == 其指派内容（复制语义
      无串位）；代表槽与旧全量流对应矩阵逐位一致（内容流只承诺头部）；分段展开
      与整批展开逐位一致；
    ④ 三流独立：内容流不消费摆放/填充流（derive 前后构造的 contents 不变）；
    ⑤ gen_batched_case 条目：sample_map 与直造一致、ratio 状态与长度自洽
      （criteria 可达时 k 值列表）。返回退出码 0/1。"""
    criteria_dir = resolve_criteria(None)
    if criteria_dir is None:
        print("[selftest] criteria 不可达：ratio 子项按 not_computed 核对（其余照跑）")
    failures = []
    for i, op in enumerate(BATCHED_OPS):
        base_op = batched_base_op(op)
        potrs = base_op.endswith("potrs")
        n, batch = 5, 37
        k = sampled_content_count(batch)
        case = {"case_id": f"selftest-{op}", "op": op, "source": "cu", "n": n,
                "batch": batch, "nrhs": 1 if potrs else None,
                "uplo": "L" if i % 2 == 0 else "U", "lda": n,
                "ldb": n if potrs else None, "seed": 923990001 + i}

        def note(bad, tag):
            for m in bad:
                failures.append(f"{op}/{tag}: {m}")

        # ① 派生确定性（覆盖/无重复/首槽不变量由 derive_sample_map 内建断言把守）
        sm1 = derive_sample_map(case["seed"], batch)
        sm2 = derive_sample_map(case["seed"], batch)
        note([] if sm1 == sm2 else ["sample_map 派生不确定（两次不等）"], "derive")
        # ④ 三流独立：先派生映射再构造内容，内容流不受摆放/填充流消费影响
        contents = build_batched_contents(case)
        full = _build_batched_arrays(case)
        # ② 内容流谱系对账：A 段 == 旧全量流头部（内容 i == 旧全量第 i 个矩阵）；
        #    potrs 族 B 段在全量流中位于全部 batch 个 A 之后，故整组内容的强承诺是
        #    「contents == 同 case 取 batch=k 的旧全量直算」（同一 stream 同序消费）
        bad = [name for name in ("A64", "A32")
               if contents[name].tobytes() != full[name][:k].tobytes()]
        note([f"{n_} 内容流 A 段头部与全量流不一致" for n_ in bad], "head-a")
        ref_k = _build_batched_arrays(dict(case, batch=k))
        bad = [name for name, arr in contents.items()
               if arr.tobytes() != ref_k[name].tobytes()]
        note([f"{n_} 内容流与 batch=k 全量直算不一致" for n_ in bad], "head")
        # ③ 展开往返：全区间 == 逐槽指派内容；分段展开与整批展开逐位一致
        exp = expand_sampled_rows(contents, sm1, 0, batch)
        bad = []
        for e in sm1:
            for s in e["slots"]:
                for name in contents:
                    if exp[name][s].tobytes() != contents[name][e["content_idx"]].tobytes():
                        bad.append(f"槽位 {s}（{name}）与指派内容不一致")
        note(bad, "expand")
        lo, hi = 7, 19
        exp2 = expand_sampled_rows(contents, sm1, lo, hi)
        bad = [name for name in contents
               if exp2[name].tobytes() != exp[name][lo:hi].tobytes()]
        note([f"{n_} 分段展开与整批展开不一致" for n_ in bad], "segment")
        # ⑤ gen_batched_case 条目自洽
        entry, info = gen_batched_case(case, criteria_dir)
        bad = []
        if info["mode"] != "a0" or info["contents"] != k:
            bad.append(f"运行信息异常：{info}")
        if entry["sample_map"] != sm1:
            bad.append("条目 sample_map 与直造不一致")
        if entry["sampled_contents"] != k or entry["materialize"] != "gen":
            bad.append("条目 sampled_contents/materialize 异常")
        if criteria_dir is None:
            if entry["ratio_cpu_status"] != "not_computed" or entry["ratio_cpu"] is not None:
                bad.append("criteria 不可达时应记 not_computed")
        else:
            vals = entry["ratio_cpu"]
            if len(vals) != k:
                bad.append(f"ratio 值列表长度 {len(vals)} != k={k}")
            n_null = sum(1 for v in vals if v is None)
            if entry["ratio_cpu_status"] == "ok" and n_null:
                bad.append("status=ok 却含 null 值")
            if entry.get("ratio_cpu_prep_failed", 0) != n_null:
                bad.append("prep_failed 计数与 null 数不符")
        note(bad, "entry")
        print(f"[selftest] {op}: k={k}/{batch} rep_slots="
              f"{[e['rep_slot'] for e in sm1]} ratio={entry['ratio_cpu_status']} "
              f"{'FAIL（%d 项）' % len(bad) if bad else 'OK'}")
    if failures:
        for msg in failures:
            print(f"[selftest] FAIL: {msg}", file=sys.stderr)
        return 1
    print("[selftest] 全部通过：sample_map 确定性与不变量、内容流头部对账、展开往返、"
          "三流独立、gen 条目自洽 OK")
    return 0


# ---------------------------------------------------------------------------
# CLI 与主流程
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
        description="S1-Cholesky 包数据生成（spec §2.5 CLI；产 cases/*.npz 与 cases/index.json 骨架）",
    )
    parser.add_argument("--canonical", help="canonical_cases.json 路径（波 0 冻结件）")
    parser.add_argument("--select", choices=("s1", "all"), default="s1",
                        help="case 选择：s1=仅 s1_subset 子集（默认，保持既有行为）；all=全量")
    parser.add_argument("--out", help="输出目录（staging；产物落 <out>/cases/）")
    parser.add_argument("--ops", default=",".join(ALL_OPS),
                        help="逗号分隔的算子子集，默认三册十算子（与 canonical 实际所含取交集）")
    parser.add_argument("--criteria", default=None,
                        help="repo-task-solver-accept 的 criteria 目录（批量 ratio_cpu 用；"
                             "缺省按镜像树相对布局探测，不可达则如实跳过 ratio）")
    parser.add_argument("--selftest", action="store_true",
                        help="只跑 A0 抽样自测（不读 canonical）")
    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest()
    if not args.canonical or not args.out:
        raise ContractError("--canonical 与 --out 必填（--selftest 模式除外）")

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

    has_batched = any(c["op"] in BATCHED_OPS for c in selected)
    criteria_dir = None
    if has_batched:
        criteria_dir = resolve_criteria(args.criteria)
        if criteria_dir is None:
            print("[gen] criteria 不可达（纯脚本包内独立运行的常态）：批量 case 的 "
                  "ratio_cpu 跳过——index 记 not_computed；判定不受影响（判定时自行"
                  "重造内容重算，HT-2 A0 / S3 spec §3）")

    index_cases = []
    by_id = {c["case_id"]: c for c in canonical["cases"]}
    for case in selected:                               # 保 canonical 原序（spec §2.1 保序编号）
        cid = case["case_id"]
        if case["op"] in BATCHED_OPS:                   # S3 批量通路（A0 抽样，HT-2）
            entry_add, info = gen_batched_case(case, criteria_dir)
            entry = dict(case)
            entry.update(entry_add)
            index_cases.append(entry)
            print(f"[gen] {cid}: n={case['n']} batch={case['batch']} uplo={case['uplo']} "
                  f"mode={info['mode']} contents={info['contents']}/{info['batch']} "
                  f"arrays={','.join(entry['arrays'])} ratio={entry['ratio_cpu_status']}")
            continue
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

    # HT-8/HT-9：info 契约用例（单算子每算子 3 个 B2 变体 + 批量每算子 1 个 B3
    # 混合 case，case_purpose="info" 入 index）。派生基座按包内 canonical 顶层
    # package_scope：full（2026-09-27 全量精度用例决策）= 全部精度 case；
    # 缺省/s1 = s1 子集（v3 前行为）。单算子数组
    # 现场 npz 落盘（供 DUT 读入），批量 A0 不落数组（sample_map 入条目）；均做
    # 构造性自检（info==k_expected）；不产 golden、不参与 ratio 回填，判定只比 info。
    _full = canonical.get("package_scope") == "full"
    info_all = list(derive_info_cases(canonical["cases"], require_s1=not _full)) + \
        list(derive_batched_info_cases(canonical["cases"], require_s1=not _full))
    for info in info_all:
        if info["op"] not in ops:
            continue
        cid = info["case_id"]
        base = by_id[info["base_case_id"]]
        if info["op"] in BATCHED_OPS:
            # HT-9：批量 info 契约条目（A0：不落数组；sample_map 同 base seed 派生，
            # 逐内容 k_expected 经 sample_map 展开即全批期望 infoArray）
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
                  f"uplo={base['uplo']} probe={info['info_probe']} "
                  f"k_expected={info['k_expected']} arrays={','.join(arrays)}")
            continue
        arrays = build_info_arrays(info, build_case_arrays(base))
        np.savez(cases_dir / f"{cid}.npz", **arrays)
        entry = {k: v for k, v in base.items() if k != "s1_subset"}
        entry.update({k: info[k] for k in ("case_id", "case_purpose", "info_probe",
                                           "k_expected", "base_case_id")})
        entry["npz"] = f"cases/{cid}.npz"
        entry["arrays"] = list(arrays)
        entry["ratio_cpu"] = None                       # B2：info 用例不做残差判定
        entry["ratio_cpu_status"] = None
        index_cases.append(entry)
        print(f"[gen] {cid}: n={base['n']} uplo={base['uplo']} probe={info['info_probe']} "
              f"k_expected={info['k_expected']} arrays={','.join(arrays)}")

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
    if has_batched:
        index["spec_batched"] = "dev-doc/solver/solver-s3-batched-spec.md#2"
    index_path = cases_dir / "index.json"
    with index_path.open("w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    per_op = {op: sum(1 for c in selected if c["op"] == op) for op in ops}
    tail = ("index.json 的 ratio_cpu：单算子留空（检查/验收侧判定时现场重算，"
            "亦可由回填工具补写）；批量 case 已记逐内容 k 值列表"
            "（A0 抽样不落数组，判定时现场重造）"
            if has_batched else
            "index.json 的 ratio_cpu 留空（检查/验收侧判定时现场重算，"
            "亦可由回填工具补写）")
    print(f"[gen] 完成：{len(selected)} case（"
          + "，".join(f"{op}={cnt}" for op, cnt in per_op.items())
          + f"）→ {cases_dir}；{tail}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ContractError as exc:
        print(f"[gen] 契约/前置错误：{exc}", file=sys.stderr)
        sys.exit(2)
