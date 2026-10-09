# -*- coding: utf-8 -*-
"""阈值常量与残差阈值公式测试（数值出处：spec §0 与 0924 任务书 §3.2.2）。

s2-A1 一段式：layer1 常量（RTOL/ATOL/REQUIRED_MATCHED_RATIO/MAX_ABS_FIXED/
ULP_MULT/max_abs_limit）已退役删除，本文件同时钉住退役事实。
"""
import pytest

pytest.importorskip("scipy")  # spec 波 1 A 行：scipy importorskip 守卫
import numpy as np

import thresholds


def test_criteria_ver_s2a1():
    """版本号钉住判定架构重构（一段式 + 换基两项合一，2026-10-08 裁定）。"""
    assert thresholds.CRITERIA_VER == "s2-A1"


def test_eps32_is_fixed_2_pow_minus_24():
    """ε 固定 2^-24（README 口径，spec §0），不是 numpy float32 eps 的 2^-23。"""
    assert thresholds.EPS32 == 2.0 ** -24
    assert thresholds.EPS32 != float(np.finfo(np.float32).eps)   # 后者是 2^-23


def test_layer1_constants_retired():
    """s2-A1：逐元素混合容差层整体拆除，layer1 常量与 max_abs 动态上限全部退役。"""
    for name in ("LAYER1_RTOL_FP32", "LAYER1_ATOL_FP32", "REQUIRED_MATCHED_RATIO",
                 "MAX_ABS_FIXED", "ULP_MULT", "max_abs_limit"):
        assert not hasattr(thresholds, name), name


def test_potrf_potrs_threshold_new_formula():
    """potrf/potrs 式 max(5·ratio_cpu, 3·ratio_cpu_mean)（HT-3；issue A1 /
    0924 任务书 §3.2.2.1/2）：相对线统一 5×、第二支均值线 3×。"""
    assert thresholds.POTRF_POTRS_FORMULA == "max(5*ratio_cpu, 3*ratio_cpu_mean)"
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
    """potri 式 max(5·ratio_cpu, 0.1)（HT-5；issue A3 / 0924 任务书 §3.2.2.3）：
    绝对线 0.1 收口；相对线 5× 与 potrf/potrs 统一；无 mean 线（结构性不适用）。"""
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


def test_residual_eps_disclosure_matches_eps32():
    """residual 报告 eps 披露字段与 EPS32 同口径（issue A4，防两处漂移）。"""
    import verdict

    eps_str = verdict._null_residual()["eps"]
    assert eps_str == "2^-24"
    base, exp = eps_str.split("^")
    assert float(base) ** float(exp) == thresholds.EPS32 == 2.0 ** -24
