# -*- coding: utf-8 -*-
"""S1-Cholesky 判据阈值常量与残差阈值公式（A 卡·criteria 内核）。

数值出处（spec §4 + §2.3′）：
- ``dev-doc/solver/solver-s1-cholesky-spec.md``（下称 spec，§2.3′ 优先于 §2.3 冲突部分）
- 0924 任务书 §3.2.2（阈值公式，issue A1/A3）
- ``repos/solver_tasks-main/cholesky_precision/README.md``（下称 README，残差公式出处）。

s2-A1 一段式（2026-10-08 用户裁定）：精度判定收成单步——每个 accuracy 用例直接算
LAPACK 残差（DPOT01/02/03）对阈值判，逐元素混合容差层（原 layer1）整体拆除。
原 layer1 常量（RTOL/ATOL 2^-13、REQUIRED_MATCHED_RATIO、MAX_ABS_FIXED、ULP_MULT、
max_abs_limit）全部退役删除；本模块只余残差 ε 与阈值公式：
- potrf/potrs（DPOT01/02）：max(5·ratio_cpu, 3·ratio_cpu_mean)（HT-3），
  缺 mean 走单支 5·ratio_cpu 兼容口径（verdict 分流）；
- potri（DPOT03）：max(5·ratio_cpu, 0.1)（HT-5）。
"""

import math

# criteria 内核版本号，供 manifest.criteria_ver（spec §2.2）与 report.versions 消费。
# s2-A1：判定架构重构两项合一（2026-10-08 用户裁定）——
#   ① 一段式直接残差判定：layer1 逐元素混合容差层整体拆除（含 2^-13 单套容差、
#     0.99 通过率门、max_abs 动态锚点门），现行 fallback 公式原样升为唯一判据；
#     golden32/golden64 不再被 judge 消费（降为自测参考件，包与 schema 不动）；
#   ② 残差基换原始 fp32 输入：potrf 族 DPOT01 的 a 从 A64 改 A32（README 1.3 的
#     A64 口径废止；推翻旧裁定 24i），全族实际输入 A32/B32 升 f64；potrf 族 A64 基
#     冻结 ratio_cpu/mean 过期需重算（声明，重算另排）。
# s1-A6：criteria 快批四项合一（2026-09-26）——HT-5/HT-14/HT-7/HT-3（已随 s2-A1 重构）。
CRITERIA_VER = "s2-A1"

# ---- 残差公式机器精度 ε（spec §0：固定 2^-24，README 口径；非 numpy eps 的 2^-23）----
# 任务书 2026-10 定稿已改 ε=2^-24（单位舍入=LAPACK SLAMCH('E') 口径），与本实现一致。
# 已按 issue A4 提任务侧修订，本常数以 2^-24 为准，不随任务书现行文本改。
EPS32 = 2.0 ** -24

# ---- 残差阈值公式 ----
# potrf/potrs（DPOT01/02）：0924 任务书 §3.2.2.1/2（issue A1，HT-3）——相对线统一 5×，
# 第二支 3·ratio_cpu_mean（同算子全部正定精度用例的 CPU 残差算术平均，HT-4 预计算
# 固化入 index）。
POTRF_POTRS_FORMULA = "max(5*ratio_cpu, 3*ratio_cpu_mean)"
# potri（DPOT03）：0924 任务书 §3.2.2.3（issue A3，HT-5），公式见 potri_threshold。
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
      系数在正态大 σ 组把合法实现误伤，失去判别力；
    - 第二支 3·ratio_cpu_mean 托住「该算子用例集整体偏难」的 case：单 case 的
      ratio_cpu 偏低而均值偏高时，均值线兜住合法实现的散布；ratio_cpu_mean 是
      同算子全部正定精度用例 CPU 残差的算术平均（HT-4 预计算固化入 index 顶层，
      判定时只读、零重算）；
    - 旧 30 地板/上限随收紧式/上浮式一并退役。
    """
    r = _check_ratio_cpu(ratio_cpu)
    m = _check_ratio_cpu_mean(ratio_cpu_mean)
    return max(5.0 * r, 3.0 * m)


def potrf_potrs_single_line(ratio_cpu):
    """缺 ratio_cpu_mean 时的单支线 5·ratio_cpu（HT-3 兼容口径，v2 包判定用）。

    仅供 verdict 的缺 mean 兼容路径消费：残差 ≤ 本线 → 从严 PASS；超出 →
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
