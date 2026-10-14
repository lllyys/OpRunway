# -*- coding: utf-8 -*-
"""LU/QR 四册阈值常量与公式回归（LQ-20）。

逐条对照四册任务书 §3.2.2（2026-10-08 定稿口径）与 lq-plan §1：

- getrf：max(5r, 0.3/0.5)，实/复地板分列（SGET01/CGET01）
- getri：max(5r, 0.05)（SGET03/CGET03，1014 新入范围）
- getriBatched：max(5r_i, 0.05)（SGET03/CGET03，逐矩阵）
- matinvBatched：max(5r_i, 0.5)（逐矩阵）
- geqrf 与 *mqr：max(5r, 0.1)（SQRT01/SQRT03）
- *gqr：max(5r, 1.5)（SQRT02）
- gels：方阵 max(5r, 0.015)（DQRT16）/ 超定 10·r_opt（DQRT17），实/复同式

入参校验（nan/inf/负/None）复用 _check_ratio_cpu。
"""
import math

import pytest

import thresholds as th


def test_getrf_threshold_real_complex_floors():
    assert th.GETRF_FORMULA == {"real": "max(5*ratio_cpu, 0.3)",
                                "complex": "max(5*ratio_cpu, 0.5)"}
    assert th.getrf_threshold(0.0, complex_=False) == 0.3     # 地板主导（实）
    assert th.getrf_threshold(0.0, complex_=True) == 0.5      # 地板主导（复）
    assert th.getrf_threshold(0.02, complex_=False) == 0.3    # 5·0.02=0.1 < 0.3
    assert th.getrf_threshold(0.1, complex_=False) == 0.5     # 5·0.1=0.5 > 0.3
    assert th.getrf_threshold(0.04, complex_=True) == 0.5     # 5·0.04=0.2 < 0.5
    assert th.getrf_threshold(0.2, complex_=True) == 1.0      # 相对线主导


def test_getri_threshold():
    assert th.GETRI_FORMULA == "max(5*ratio_cpu, 0.05)"
    assert th.getri_threshold(0.0) == 0.05                    # 地板
    assert th.getri_threshold(0.1) == 0.5                     # 5·0.1 相对线主导


def test_batched_inverse_thresholds():
    assert th.GETRIBATCHED_FORMULA == "max(5*ratio_cpu_i, 0.05)"
    assert th.MATINVBATCHED_FORMULA == "max(5*ratio_cpu_i, 0.5)"
    assert th.getribatched_threshold(0.0) == 0.05             # 地板
    assert th.getribatched_threshold(0.1) == 0.5              # 5·0.1 相对线主导
    assert th.matinvbatched_threshold(0.0) == 0.5             # 地板
    assert th.matinvbatched_threshold(0.2) == 1.0             # 相对线主导


def test_qr_thresholds():
    assert th.GEQRF_MQR_FORMULA == "max(5*ratio_cpu, 0.1)"
    assert th.GQR_FORMULA == "max(5*ratio_cpu, 1.5)"
    assert th.geqrf_mqr_threshold(0.0) == 0.1                 # 地板
    assert th.geqrf_mqr_threshold(0.5) == 2.5                 # 相对线主导
    assert th.gqr_threshold(0.0) == 1.5                       # 地板
    assert th.gqr_threshold(0.1) == 1.5                       # 5·0.1=0.5 < 1.5
    assert th.gqr_threshold(0.4) == 2.0                       # 相对线主导


def test_gels_threshold_square_and_overdet():
    assert th.GELS_SQUARE_FORMULA == "max(5*ratio_cpu, 0.015)"
    assert th.GELS_OVERDET_FORMULA == "10*ratio_opt_cpu"
    assert th.gels_threshold(0.3, square=True) == 1.5         # 方阵线 5·r
    assert th.gels_threshold(0.0, square=True) == 0.015       # 方阵地板
    assert th.gels_threshold(0.7, square=False) == 7.0        # 超定线 10·r_opt


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1])
def test_luqr_thresholds_reject_bad_ratio(bad):
    for fn in (th.getrf_threshold, th.getri_threshold, th.getribatched_threshold,
               th.matinvbatched_threshold, th.geqrf_mqr_threshold,
               th.gqr_threshold, lambda r: th.gels_threshold(r, square=True),
               lambda r: th.gels_threshold(r, square=False)):
        with pytest.raises(ValueError):
            fn(bad)


def test_luqr_thresholds_match_taskbook_constants():
    # 常量与任务书 §3.2.2 字样逐条对齐（防手改漂移）
    assert th.GETRF_FLOOR == {"real": 0.3, "complex": 0.5}
    assert math.isclose(th.GETRI_FLOOR, 0.05)
    assert math.isclose(th.GETRIBATCHED_FLOOR, 0.05)
    assert math.isclose(th.MATINVBATCHED_FLOOR, 0.5)
    assert math.isclose(th.GEQRF_MQR_FLOOR, 0.1)
    assert math.isclose(th.GQR_FLOOR, 1.5)
    assert math.isclose(th.GELS_SQUARE_FLOOR, 0.015)
    assert math.isclose(th.GELS_OVERDET_FACTOR, 10.0)