# -*- coding: utf-8 -*-
"""S1-Cholesky 判据阈值常量与 fallback 阈值公式（A 卡·criteria 内核）。

数值出处（spec §4 + §2.3′）：
- ``dev-doc/solver/solver-s1-cholesky-spec.md``（下称 spec，§2.3′ 优先于 §2.3 冲突部分）
- 实数 Cholesky 任务书 §3.2（Atlas950_Spotrf..._task_doc.md，spec §2.3′ 引用）
- ``repos/solver_tasks-main/cholesky_precision/README.md``（下称 README）
  及其引用的标准表 ``repos/opbase-precision-standard/mixed_tolerance_standard.md``。

层次对应 spec §2.3/§2.3′：
- layer1（逐元素混合容差 |actual-golden| <= atol + rtol*|golden|）单套容差：
  生态标准表 2^-13（HT-14：issue C1 已裁「用新版」，2026-09-24——旧任务书表
  2^-10/2^-16 与 opbase 最新标准 2^-13 双套矛盾，收成标准表单套，T3 双轨拆除）；
- layer1 整体双门：matched_ratio >= 0.99 且 max_abs <= max_abs_limit(g_low)。上限的
  「1e-2 or 32*ULP」已按 2026-09-24 裁定采 opbase 最新标准（见 max_abs_limit），
  or=取两项较大者，不再并行两种解释。
- fallback（DPOT01/02/03 残差）阈值公式见文末函数；potrf/potrs 已按 0924 任务书
  §3.2.2.1/2 换式 max(5·ratio_cpu, 3·ratio_cpu_mean)（HT-3：收紧式/上浮式与
  30 地板/上限退役），potri 已按 §3.2.2.3 换新式。
"""

import math

import numpy as np

# criteria 内核版本号，供 manifest.criteria_ver（spec §2.2）与 report.versions 消费。
# s1-A6：criteria 快批四项合一（2026-09-26）——HT-5 potri 阈值式 max(5·ratio_cpu, 0.1)、
#   HT-14 混合容差收单套 2^-13（T3 双轨拆除）、HT-7 判定次序对调（potrf 直审主判/
#   还原降诊断、potri 收单、主判基准统一 golden64）、HT-3 potrf/potrs 阈值换式
#   max(5·ratio_cpu, 3·ratio_cpu_mean)（收紧/上浮式与 30 地板/上限退役，缺 mean 走
#   单支兼容口径）。
# s1-A5：0924 任务书批量口径定案摘 T8（核对 criteria 无注入点，判定数值不变）+ 报告申诉指引句。
# s1-A4：max_abs 门动态锚点（HT-1 裁定）+ golden 收窄与 ±inf/NaN 逐点口径 + T4 双解释拆除。
# s1-A3：A4 ε 口径标注 + fallback 报告 eps 披露字段（判定数值不变）。
# s1-A2：spec §2.3′ 修正（任务书阈值为主、potrf 还原比对、potri 双目标）。
CRITERIA_VER = "s1-A6"

# ---- 残差公式机器精度 ε（spec §0：固定 2^-24，README 口径；非 numpy eps 的 2^-23）----
# 任务书 2026-10 定稿已改 ε=2^-24（单位舍入=LAPACK SLAMCH('E') 口径），与本实现一致。
# 已按 issue A4 提任务侧修订，本常数以 2^-24 为准，不随任务书现行文本改。
EPS32 = 2.0 ** -24

# ---- layer1 逐元素混合容差单套（FLOAT32 档）----
# 生态标准表：mixed_tolerance_standard.md §2.2，FLOAT32 rtol = atol = 2^-13。
# HT-14（issue C1）：旧任务书表 2^-10/2^-16 与标准表双套并行（分歧记 T3）已拆除，
# 按原负责人裁定「用新版」收成标准表单套；TASKBOOK_*/STANDARD_* 双常量退役。
LAYER1_RTOL_FP32 = 2.0 ** -13
LAYER1_ATOL_FP32 = 2.0 ** -13

# layer1 整体双门之一：通过率下限（任务书 §3.2 表；标准表 §2.2 同值 0.99）。
REQUIRED_MATCHED_RATIO = 0.99

# layer1 整体双门之二：max_abs 上限（任务书/标准表「1e-2 or 32 * ULP」；2026-09-24
# 裁定采 opbase 最新标准：or=取两项较大者，动态上限见 max_abs_limit）。
MAX_ABS_FIXED = 1e-2                 # FP32 兜底值（mixed_tolerance_standard.md §2.1.2）
ULP_MULT = 32                        # 32·ULP(g_low) 项的倍数


def max_abs_limit(g_low):
    """max_abs 动态上限：max(兜底值, 32·ULP(g_low))。

    出处：opbase mixed_tolerance_standard.md §2.1.2（2026-09-24 版，commit a5e8e71），
    FP32 兜底值 1e-2。g_low 是最大绝对误差点的 golden 值按输出 dtype RNE 收窄后的值
    （verdict._layer1_stats 选出）；None 表示无有限比对点，abs 门空真，返回兜底值。
    ULP 取 float32 在 |g_low| 处的间距语义（np.spacing）——与残差公式的 ε=2^-24
    （EPS32）分属两个常数，勿混用。
    """
    if g_low is None:
        return MAX_ABS_FIXED
    ulp = float(np.spacing(np.float32(abs(float(g_low)))))
    return max(MAX_ABS_FIXED, ULP_MULT * ulp)

# ---- fallback 阈值公式 ----
# potrf/potrs（DPOT01/02）：0924 任务书 §3.2.2.1/2 换式（issue A1，HT-3）——
# 相对线统一 5×，第二支 3·ratio_cpu_mean（同算子全部正定精度用例的 CPU 残差
# 算术平均，HT-4 预计算固化入 index）；README 定标的收紧式（min(30, max(10r,1))）
# 与上浮式（max(2r, 30)）及 30 地板/上限一并退役。
POTRF_POTRS_FORMULA = "max(5*ratio_cpu, 3*ratio_cpu_mean)"
# potri（DPOT03）：0924 任务书 §3.2.2.3 换式（issue A3，HT-5），公式见 potri_threshold。
POTRI_FORMULA = "max(5*ratio_cpu, 0.1)"


def _check_ratio_cpu(ratio_cpu):
    """阈值公式入参校验：ratio_cpu 必须是有限非负实数（spec §2.4）。"""
    r = float(ratio_cpu)
    if not math.isfinite(r):
        raise ValueError(f"ratio_cpu 非有限值: {ratio_cpu!r}")
    if r < 0:
        raise ValueError(f"ratio_cpu 不得为负: {ratio_cpu!r}")
    return r


def _check_ratio_cpu_mean(ratio_cpu_mean):
    """mean 线入参校验：有限非负实数，None 显式拒绝（缺 mean 不静默降级）。"""
    if ratio_cpu_mean is None:
        raise ValueError("ratio_cpu_mean 缺失（第二支 3·ratio_cpu_mean 不可算）")
    m = float(ratio_cpu_mean)
    if not math.isfinite(m):
        raise ValueError(f"ratio_cpu_mean 非有限值: {ratio_cpu_mean!r}")
    if m < 0:
        raise ValueError(f"ratio_cpu_mean 不得为负: {ratio_cpu_mean!r}")
    return m


def potrf_potrs_threshold(ratio_cpu, ratio_cpu_mean):
    """potrf/potrs 阈值式（DPOT01/02，HT-3）：max(5·ratio_cpu, 3·ratio_cpu_mean)
    （0924 任务书 §3.2.2.1/2，issue A1）。

    - 相对线系数统一 5×（与 potri 一致），替换 README 定标收紧式 10×——过紧的
      系数在正态大 σ 组把合法实现误伤进兜底层，失去第一层判别力；
    - 第二支 3·ratio_cpu_mean 托住「该算子用例集整体偏难」的 case：单 case 的
      ratio_cpu 偏低而均值偏高时，均值线兜住合法实现的散布；ratio_cpu_mean 是
      同算子全部正定精度用例 CPU 残差的算术平均（HT-4 预计算固化入 index 顶层，
      判定时只读、零重算）；
    - 旧 30 地板/上限随收紧式/上浮式一并退役（LAPACK THRESH 生态契约在两步
      结构下由 layer1 主判承担，残差层不再需要绝对地板）。
    """
    r = _check_ratio_cpu(ratio_cpu)
    m = _check_ratio_cpu_mean(ratio_cpu_mean)
    return max(5.0 * r, 3.0 * m)


def potrf_potrs_single_line(ratio_cpu):
    """缺 ratio_cpu_mean 时的单支线 5·ratio_cpu（HT-3 兼容口径，v2 包判定用）。

    仅供 verdict 的缺 mean 兼容路径消费：残差 ≤ 本线 → 兜底 PASS；超出 →
    证据不足不判 FAIL（两支公式只可证一支），待含 mean 的 v3 包复判。
    """
    return 5.0 * _check_ratio_cpu(ratio_cpu)


def potri_threshold(ratio_cpu):
    """potri 阈值式（DPOT03）：max(5*ratio_cpu, 0.1)（HT-5；0924 任务书
    §3.2.2.3，issue A3 修订依据）。

    - 相对线系数 5×：与 potrf/potrs 统一——求逆的合法实现间散布实测不超过
      5×，更大系数只起放松作用；
    - 绝对线 0.1 而非旧收紧式地板 1：DPOT03 分母含 n·‖A‖₁·‖C‖₁ 双归一（逆的
      幅值随 ‖A‖₁ 增大而缩小），合法实现的残差被压在远低于 1 的量级，地板 1
      失去判别意义；0.1 为「合法实现散布上界 × 健康余量」的标定结果；
    - mean 线（3·ratio_cpu_mean，potrf/potrs 用）在 potri 上结构性不适用：
      CPU 残差均值比实现间散布上界低一个数量级（归一化过强所致），任何系数
      都无法同时满足「托住散布上界」与「保持健康余量」，第二支取常数。

    验证方式（issue A3）：合法参考实现逐 case 残差对阈值扫描，全 case 通过
    且最薄余量 ≥3×（0.1 地板下满足）；扫描脚本与结论见交接包
    verify/ht5_potri_a3_scan.py。
    """
    r = _check_ratio_cpu(ratio_cpu)
    return max(5.0 * r, 0.1)
