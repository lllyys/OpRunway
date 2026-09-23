# -*- coding: utf-8 -*-
"""residual_ratio 纯接口测试：手算期望值、U/L 双侧、存储侧口径与异常面。"""
import pytest

pytest.importorskip("scipy")  # spec 波 1 A 行：scipy importorskip 守卫
import numpy as np

import thresholds
from verdict import residual_ratio

EPS = thresholds.EPS32   # 2^-24（spec §0）


# ---------------------------------------------------------------------------
# DPOT01（手算 + U/L 双侧 + 存储侧口径）
# ---------------------------------------------------------------------------

def test_dpot01_lower_hand_value():
    """手算 #1：A=[[4,2],[2,5]] 的准确因子 L=[[2,0],[1,2]]，把 L[1,1] 扰成 2.5。

    L·Lᵀ = [[4,2],[2,1+6.25]] → 差值存储侧只有 (1,1)=2.25 → 分子 2.25；
    ‖A‖₁ = max(4+2, 2+5) = 7 → 分母 2·7·ε → ratio = 2.25/(14ε) ≈ 2.696e6。
    """
    a = np.array([[4.0, 2.0], [2.0, 5.0]])
    factor = np.array([[2.0, 0.0], [1.0, 2.5]])
    expected = 2.25 / (2 * 7 * EPS)
    got = residual_ratio("DPOT01", a=a, factor=factor, uplo="L")
    assert got == pytest.approx(expected, rel=1e-12)


def test_dpot01_upper_equals_lower():
    """U/L 双侧：同一扰动的上三角因子 U=[[2,1],[0,2.5]]（Uᵀ·U 口径）给出同一 ratio。"""
    a = np.array([[4.0, 2.0], [2.0, 5.0]])
    lower = residual_ratio("DPOT01", a=a,
                           factor=np.array([[2.0, 0.0], [1.0, 2.5]]), uplo="L")
    upper = residual_ratio("DPOT01", a=a,
                           factor=np.array([[2.0, 1.0], [0.0, 2.5]]), uplo="U")
    assert upper == pytest.approx(lower, rel=1e-12)
    assert upper == pytest.approx(2.25 / (2 * 7 * EPS), rel=1e-12)


def test_dpot01_ignores_unstored_triangle():
    """只用存储侧：因子与 A 的非存储侧塞入有限垃圾值，ratio 不变。"""
    a = np.array([[4.0, 2.0], [2.0, 5.0]])
    f = np.array([[2.0, 0.0], [1.0, 2.5]])
    clean = residual_ratio("DPOT01", a=a, factor=f, uplo="L")
    a_dirty = a.copy()
    a_dirty[0, 1] = 777.0          # uplo=L 时上三角不是存储侧
    f_dirty = f.copy()
    f_dirty[0, 1] = -999.0
    dirty = residual_ratio("DPOT01", a=a_dirty, factor=f_dirty, uplo="L")
    assert dirty == clean


def test_dpot01_exact_factor_zero_ratio():
    a = np.array([[4.0, 2.0], [2.0, 5.0]])
    f = np.array([[2.0, 0.0], [1.0, 2.0]])   # 准确因子，重构逐位归零
    assert residual_ratio("DPOT01", a=a, factor=f, uplo="L") == 0.0


# ---------------------------------------------------------------------------
# DPOT02（手算 + 多 RHS 最差列）
# ---------------------------------------------------------------------------

def test_dpot02_multi_rhs_takes_worst_column():
    """手算 #2：A=diag(2,4)，三列 RHS，逐列残差 0 / 2^21 / 2^20，取最差列。

    列 0：b=A·x 精确 → 0；
    列 1：x=[1,0]、b=[2.5,0] → ‖r‖₁=0.5、‖x‖₁=1 → 0.5/(4·1·ε) = 2^21 = 2097152；
    列 2：x=[0,1]、b=[0,4.25] → 0.25/(4·1·ε) = 2^20 = 1048576。
    """
    a = np.array([[2.0, 0.0], [0.0, 4.0]])
    x = np.array([[1.0, 1.0, 0.0],
                  [1.0, 0.0, 1.0]])
    b = np.array([[2.0, 2.5, 0.0],
                  [4.0, 0.0, 4.25]])
    assert residual_ratio("DPOT02", a=a, b=b, x=x) == 2097152.0


def test_dpot02_exact_solution_zero_ratio():
    a = np.array([[2.0, 0.0], [0.0, 4.0]])
    x = np.array([[1.0], [1.0]])
    b = a @ x
    assert residual_ratio("DPOT02", a=a, b=b, x=x) == 0.0


# ---------------------------------------------------------------------------
# DPOT03（手算 + U/L 双侧 + 存储侧口径）
# ---------------------------------------------------------------------------

def test_dpot03_hand_value():
    """手算 #3：A=diag(2,4) 的准确逆 diag(0.5,0.25)，把 C[1,1] 扰成 0.375。

    W = I − A·C = diag(0, −0.5) → 分子 0.5；‖A‖₁=4、‖C‖₁=0.5 →
    分母 2·4·0.5·ε = 4ε → ratio = 0.5/(4ε) = 2^21 = 2097152。
    """
    a = np.array([[2.0, 0.0], [0.0, 4.0]])
    ainv = np.array([[0.5, 0.0], [0.0, 0.375]])
    assert residual_ratio("DPOT03", a=a, ainv=ainv, uplo="L") == 2097152.0


def test_dpot03_upper_equals_lower():
    """U/L 双侧：同一逆的两种半三角存储（另侧置 0）给出同一 ratio。"""
    a = np.array([[2.0, 1.0], [1.0, 2.0]])
    c_lower = np.array([[0.625, 0.0], [-0.3125, 0.625]])
    c_upper = np.array([[0.625, -0.3125], [0.0, 0.625]])
    lo = residual_ratio("DPOT03", a=a, ainv=c_lower, uplo="L")
    up = residual_ratio("DPOT03", a=a, ainv=c_upper, uplo="U")
    assert up == pytest.approx(lo, rel=1e-12)
    assert lo > 0.0


def test_dpot03_ignores_unstored_triangle():
    a = np.array([[2.0, 1.0], [1.0, 2.0]])
    c = np.array([[0.625, 0.0], [-0.3125, 0.625]])
    clean = residual_ratio("DPOT03", a=a, ainv=c, uplo="L")
    a_dirty = a.copy()
    a_dirty[0, 1] = 55.0
    c_dirty = c.copy()
    c_dirty[0, 1] = -66.0
    assert residual_ratio("DPOT03", a=a_dirty, ainv=c_dirty, uplo="L") == clean


# ---------------------------------------------------------------------------
# 异常面：NaN / Inf / shape / 零分母 / 未知 kind / uplo / 复数 / 多余参数
# ---------------------------------------------------------------------------

_A2 = np.array([[4.0, 2.0], [2.0, 5.0]])
_F2 = np.array([[2.0, 0.0], [1.0, 2.0]])


def test_nan_raises():
    bad = _F2.copy()
    bad[1, 1] = np.nan
    with pytest.raises(ValueError):
        residual_ratio("DPOT01", a=_A2, factor=bad, uplo="L")
    with pytest.raises(ValueError):
        residual_ratio("DPOT02", a=_A2, b=np.full((2, 1), np.nan), x=np.ones((2, 1)))
    with pytest.raises(ValueError):
        residual_ratio("DPOT03", a=_A2, ainv=bad, uplo="L")


def test_inf_raises():
    bad = _F2.copy()
    bad[0, 0] = np.inf
    with pytest.raises(ValueError):
        residual_ratio("DPOT01", a=_A2, factor=bad, uplo="L")


def test_shape_mismatch_raises():
    with pytest.raises(ValueError):
        residual_ratio("DPOT01", a=_A2, factor=np.zeros((3, 3)), uplo="L")
    with pytest.raises(ValueError):
        residual_ratio("DPOT01", a=np.zeros((2, 3)), factor=np.zeros((2, 3)), uplo="L")
    with pytest.raises(ValueError):
        residual_ratio("DPOT02", a=_A2, b=np.ones((2, 2)), x=np.ones((2, 1)))
    with pytest.raises(ValueError):  # b/x 必须是 2 维（n×nrhs，spec §2.2）
        residual_ratio("DPOT02", a=_A2, b=np.ones(2), x=np.ones(2))
    with pytest.raises(ValueError):
        residual_ratio("DPOT03", a=_A2, ainv=np.zeros((3, 3)), uplo="U")


def test_zero_denominator_raises():
    with pytest.raises(ValueError):  # ‖A‖₁ = 0
        residual_ratio("DPOT01", a=np.zeros((2, 2)), factor=_F2, uplo="L")
    with pytest.raises(ValueError):  # 某列 ‖x‖₁ = 0
        residual_ratio("DPOT02", a=_A2, b=np.ones((2, 1)), x=np.zeros((2, 1)))
    with pytest.raises(ValueError):  # ‖C‖₁ = 0
        residual_ratio("DPOT03", a=_A2, ainv=np.zeros((2, 2)), uplo="L")


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        residual_ratio("DGET01", a=_A2, factor=_F2, uplo="L")


def test_bad_uplo_raises():
    with pytest.raises(ValueError):
        residual_ratio("DPOT01", a=_A2, factor=_F2, uplo="X")


def test_complex_rejected():
    with pytest.raises(TypeError):
        residual_ratio("DPOT01", a=_A2.astype(np.complex64), factor=_F2, uplo="L")


def test_unexpected_kwarg_raises():
    with pytest.raises(TypeError):
        residual_ratio("DPOT01", a=_A2, factor=_F2, uplo="L", bogus=1)


def test_returns_python_float():
    got = residual_ratio("DPOT01", a=_A2, factor=_F2, uplo="L")
    assert isinstance(got, float)
