# -*- coding: utf-8 -*-
"""S1-Cholesky 判据阈值常量与 fallback 阈值公式（A 卡·criteria 内核）。

数值出处（spec §4 + §2.3′）：
- ``dev-doc/solver/solver-s1-cholesky-spec.md``（下称 spec，§2.3′ 优先于 §2.3 冲突部分）
- 实数 Cholesky 任务书 §3.2（Atlas950_Spotrf..._task_doc.md，spec §2.3′ 引用）
- ``repos/solver_tasks-main/cholesky_precision/README.md``（下称 README）
  及其引用的标准表 ``repos/opbase-precision-standard/mixed_tolerance_standard.md``。

层次对应 spec §2.3/§2.3′：
- layer1（逐元素混合容差 |actual-golden| <= atol + rtol*|golden|）双套都算：
  **任务书表为主判**（rtol 2^-10 / atol 2^-16，任务书 §3.2 显式值即该任务契约），
  标准表 2^-13 降为对照；两套结论不同记 flag T3（运行语义：流转按任务书套算）。
- layer1 整体双门：matched_ratio >= 0.99 且 max_abs <= 上限；上限的「1e-2 or 32*ULP」
  or 语义未裁（T4），两种解释并行计算，不自选 max/min。
- fallback（DPOT01/02/03 残差）阈值公式见文末两个函数；数值判定不构成正式验收结论。
"""

import math

# criteria 内核版本号，供 manifest.criteria_ver（spec §2.2）与 report.versions 消费。
# s1-A2：spec §2.3′ 修正（任务书阈值为主、potrf 还原比对、potri 双目标）。
CRITERIA_VER = "s1-A2"

# ---- 残差公式机器精度 ε（spec §0：固定 2^-24，README 口径；非 numpy eps 的 2^-23）----
EPS32 = 2.0 ** -24

# ---- layer1 逐元素混合容差两套（FLOAT32 档）----
# 任务书（主判）：任务书 §3.2 表，rtol = 2^-10、atol = 2^-16（spec §2.3′）。
TASKBOOK_RTOL_FP32 = 2.0 ** -10
TASKBOOK_ATOL_FP32 = 2.0 ** -16
# 标准表（对照）：mixed_tolerance_standard.md §2.2，FLOAT32 rtol = atol = 2^-13。
# 任务书内的标准 URL 是旧版引用，对照按最新版数值（spec §2.3′ T3 收束语义）。
STANDARD_RTOL_FP32 = 2.0 ** -13
STANDARD_ATOL_FP32 = 2.0 ** -13

# layer1 整体双门之一：通过率下限（任务书 §3.2 表；标准表 §2.2 同值 0.99）。
REQUIRED_MATCHED_RATIO = 0.99

# layer1 整体双门之二：max_abs 上限的两种解释（任务书 §3.2「1e-2 or 32 * ULP」，
# or 语义待裁 → flag T4；标准表 §2.2 同款表述）。
MAX_ABS_FIXED = 1e-2                 # 固定上限解释
ULP_MULT = 32                        # "32 * ULP" 备选口径的倍数
MAX_ABS_ULP32 = ULP_MULT * EPS32     # ULP 参照取 2^-24，参照待裁（spec §2.3）

# ---- fallback 阈值公式（spec §2.3；定标依据 README 1.2 / 2.3 / 3.2）----
TIGHTEN_FORMULA = "min(30, max(10*ratio_cpu, 1))"   # DPOT01/DPOT03 收紧式，地板 1
FLOATUP_FORMULA = "max(2*ratio_cpu, 30)"            # DPOT02 上浮式，地板 30


def _check_ratio_cpu(ratio_cpu):
    """阈值公式入参校验：ratio_cpu 必须是有限非负实数（spec §2.4）。"""
    r = float(ratio_cpu)
    if not math.isfinite(r):
        raise ValueError(f"ratio_cpu 非有限值: {ratio_cpu!r}")
    if r < 0:
        raise ValueError(f"ratio_cpu 不得为负: {ratio_cpu!r}")
    return r


def tighten_threshold(ratio_cpu):
    """收紧式阈值（DPOT01/DPOT03）：min(30, max(10*ratio_cpu, 1))。

    地板 1 的定标依据：potrf/potri 参考链路底噪距 1 有百倍以上余量
    （README 1.2、3.2），收紧不碰误伤；30 保留为上限，维持 LAPACK THRESH 生态契约。
    """
    r = _check_ratio_cpu(ratio_cpu)
    return min(30.0, max(10.0 * r, 1.0))


def floatup_threshold(ratio_cpu):
    """上浮式阈值（DPOT02）：max(2*ratio_cpu, 30)。

    求解类 ratio 随规模/病态度幂律增长、最差余量仅个位数倍（README 2.3），
    不能用地板 1 收紧；30 是 LAPACK 对 DPOT02 的既定 THRESH，保留为地板，
    ratio_cpu 大时按 2 倍参考线上浮（劣化于 CPU 参考 2 倍即 FAIL）。
    """
    r = _check_ratio_cpu(ratio_cpu)
    return max(2.0 * r, 30.0)
