# -*- coding: utf-8 -*-
"""阈值常量与 fallback 阈值公式测试（数值出处：spec §0/§2.3/§2.3′ 与任务书 §3.2）。"""
import pytest

pytest.importorskip("scipy")  # spec 波 1 A 行：scipy importorskip 守卫
import numpy as np

import thresholds


def test_eps32_is_fixed_2_pow_minus_24():
    """ε 固定 2^-24（README 口径，spec §0），不是 numpy float32 eps 的 2^-23。"""
    assert thresholds.EPS32 == 2.0 ** -24
    assert thresholds.EPS32 != float(np.finfo(np.float32).eps)   # 后者是 2^-23


def test_layer1_single_tolerance_set():
    """layer1 混合容差单套（HT-14，issue C1 裁「用新版」）：rtol = atol = 2^-13
    （生态标准表 mixed_tolerance_standard.md §2.2）；TASKBOOK/STANDARD 双套常量
    与 T3 双轨已退役。"""
    assert thresholds.LAYER1_RTOL_FP32 == 2.0 ** -13
    assert thresholds.LAYER1_ATOL_FP32 == 2.0 ** -13
    assert not hasattr(thresholds, "TASKBOOK_RTOL_FP32")
    assert not hasattr(thresholds, "TASKBOOK_ATOL_FP32")
    assert not hasattr(thresholds, "STANDARD_RTOL_FP32")
    assert not hasattr(thresholds, "STANDARD_ATOL_FP32")
    assert thresholds.REQUIRED_MATCHED_RATIO == 0.99   # 标准表 §2.2 同值


def test_max_abs_limit_dynamic_anchor():
    """max_abs 上限 = max(兜底 1e-2, 32·ULP(g_low))（HT-1 裁定，标准 §2.1.2）。

    g_low=1 时 32·ULP=32·2^-23≈3.8e-6 ≪ 1e-2 → 兜底主导；g_low=1e4 时
    ULP(1e4)=2^-10 → 32·2^-10=3.125e-2 > 1e-2 → ULP 项主导。锚点取绝对值，
    正负对称；None（无有限比对点）与 0（次正规间距）都落在兜底值。
    """
    assert thresholds.MAX_ABS_FIXED == 1e-2
    assert thresholds.ULP_MULT == 32
    assert thresholds.max_abs_limit(None) == 1e-2
    assert thresholds.max_abs_limit(0.0) == 1e-2
    assert thresholds.max_abs_limit(1.0) == 1e-2
    assert thresholds.max_abs_limit(1.0) == max(1e-2, 32 * float(np.spacing(np.float32(1.0))))
    assert thresholds.max_abs_limit(1e4) == 32 * 2.0 ** -10 == 0.03125
    assert thresholds.max_abs_limit(-1e4) == thresholds.max_abs_limit(1e4)
    assert not hasattr(thresholds, "MAX_ABS_ULP32")        # 双解释常数已随 HT-1 退役


def test_potrf_potrs_threshold_new_formula():
    """potrf/potrs 新式 max(5·ratio_cpu, 3·ratio_cpu_mean)（HT-3；issue A1 /
    0924 任务书 §3.2.2.1/2）：相对线统一 5×、第二支均值线 3×；README 定标
    收紧式/上浮式与 30 地板/上限退役。"""
    assert thresholds.POTRF_POTRS_FORMULA == "max(5*ratio_cpu, 3*ratio_cpu_mean)"
    assert not hasattr(thresholds, "TIGHTEN_FORMULA")
    assert not hasattr(thresholds, "FLOATUP_FORMULA")
    assert not hasattr(thresholds, "tighten_threshold")
    assert not hasattr(thresholds, "floatup_threshold")
    assert thresholds.potrf_potrs_threshold(0.0, 0.0) == 0.0    # 底噪双零 → 0
    assert thresholds.potrf_potrs_threshold(0.5, 0.1) == 2.5    # 相对线主导
    assert thresholds.potrf_potrs_threshold(0.1, 1.0) == 3.0    # 均值线主导
    assert thresholds.potrf_potrs_threshold(0.6, 1.0) == 3.0    # 恰交 5·0.6=3·1.0
    assert thresholds.potrf_potrs_threshold(2.0, 0.0) == 10.0
    assert thresholds.potrf_potrs_single_line(0.3) == 1.5       # 单支兼容线 5×


def test_potrf_potrs_threshold_rejects_bad_inputs():
    """入参校验（spec §2.4）：ratio_cpu / ratio_cpu_mean 任一非有限或为负、
    mean 缺失（None）→ 抛 ValueError（缺 mean 不静默降级，由 verdict 兼容路径
    显式分流）。"""
    for bad in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            thresholds.potrf_potrs_threshold(bad, 0.0)
        with pytest.raises(ValueError):
            thresholds.potrf_potrs_threshold(0.0, bad)
        with pytest.raises(ValueError):
            thresholds.potrf_potrs_single_line(bad)
    with pytest.raises(ValueError):
        thresholds.potrf_potrs_threshold(0.0, None)


def test_potri_threshold_new_formula():
    """potri 新式 max(5·ratio_cpu, 0.1)（HT-5；issue A3 / 0924 任务书 §3.2.2.3）：
    绝对线 0.1 收口（DPOT03 双归一把合法实现残差压到远低于 1，旧地板 1 失去
    判别意义）；相对线 5× 与 potrf/potrs 统一；无 mean 线（结构性不适用）。"""
    assert thresholds.POTRI_FORMULA == "max(5*ratio_cpu, 0.1)"
    assert thresholds.potri_threshold(0.0) == 0.1        # 底噪 → 绝对线
    assert thresholds.potri_threshold(0.001) == 0.1      # 5·0.001=0.005 仍在绝对线下
    assert thresholds.potri_threshold(0.02) == 0.1       # 5·0.02=0.1 恰交
    assert thresholds.potri_threshold(0.04) == 0.2       # 5·0.04=0.2 相对线主导
    assert thresholds.potri_threshold(1.0) == 5.0
    assert thresholds.potri_threshold(2.0) == 10.0


def test_invalid_ratio_cpu_raises():
    for bad in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            thresholds.potri_threshold(bad)


def test_fallback_eps_disclosure_matches_eps32():
    """fallback 报告 eps 披露字段与 EPS32 同口径（issue A4，防两处漂移）。"""
    import verdict

    eps_str = verdict._null_fallback()["eps"]
    assert eps_str == "2^-24"
    base, exp = eps_str.split("^")
    assert float(base) ** float(exp) == thresholds.EPS32 == 2.0 ** -24
