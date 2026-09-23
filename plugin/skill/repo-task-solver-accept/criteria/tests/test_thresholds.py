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


def test_max_abs_two_interpretations():
    """max_abs 上限两种解释：固定 1e-2 与 32·ULP=2^-19（任务书 or 语义待裁 → T4）。"""
    assert thresholds.MAX_ABS_FIXED == 1e-2
    assert thresholds.MAX_ABS_ULP32 == 32 * 2.0 ** -24 == 2.0 ** -19


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
