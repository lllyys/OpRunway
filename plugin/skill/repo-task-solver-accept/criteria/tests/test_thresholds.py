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


def test_taskbook_primary_and_standard_reference_tables():
    """任务书（主判，spec §2.3′）rtol=2^-10 / atol=2^-16；标准表（对照）rtol=atol=2^-13。"""
    assert thresholds.TASKBOOK_RTOL_FP32 == 2.0 ** -10
    assert thresholds.TASKBOOK_ATOL_FP32 == 2.0 ** -16
    assert thresholds.STANDARD_RTOL_FP32 == 2.0 ** -13
    assert thresholds.STANDARD_ATOL_FP32 == 2.0 ** -13
    assert thresholds.REQUIRED_MATCHED_RATIO == 0.99   # 任务书 §3.2 表


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


def test_tighten_threshold_floor_and_cap():
    """收紧式 min(30, max(10·ratio_cpu, 1))：地板 1、上限 30。"""
    assert thresholds.tighten_threshold(0.0) == 1.0        # 底噪 → 地板
    assert thresholds.tighten_threshold(0.05) == 1.0       # 10·0.05=0.5 仍在地板下
    assert thresholds.tighten_threshold(0.5) == 5.0
    assert thresholds.tighten_threshold(2.0) == 20.0
    assert thresholds.tighten_threshold(10.0) == 30.0      # 上限封顶
    assert thresholds.TIGHTEN_FORMULA == "min(30, max(10*ratio_cpu, 1))"


def test_floatup_threshold_floor_and_scale():
    """上浮式 max(2·ratio_cpu, 30)：地板 30，随参考线 2 倍上浮，无上限封顶。"""
    assert thresholds.floatup_threshold(0.0) == 30.0
    assert thresholds.floatup_threshold(1.0) == 30.0
    assert thresholds.floatup_threshold(20.0) == 40.0
    assert thresholds.floatup_threshold(100.0) == 200.0
    assert thresholds.FLOATUP_FORMULA == "max(2*ratio_cpu, 30)"


def test_invalid_ratio_cpu_raises():
    for bad in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            thresholds.tighten_threshold(bad)
        with pytest.raises(ValueError):
            thresholds.floatup_threshold(bad)


def test_fallback_eps_disclosure_matches_eps32():
    """fallback 报告 eps 披露字段与 EPS32 同口径（issue A4，防两处漂移）。"""
    import verdict

    eps_str = verdict._null_fallback()["eps"]
    assert eps_str == "2^-24"
    base, exp = eps_str.split("^")
    assert float(base) ** float(exp) == thresholds.EPS32 == 2.0 ** -24
