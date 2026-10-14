# -*- coding: utf-8 -*-
"""LU 残差纯接口测试（DGET01/DGET03；lq-plan §1，任务书 SGET01/SGET03 口径）。

覆盖：
- 手算期望值（含 1-范数分母、ε=2^-24 归一）
- 与 scipy LAPACK 真解一致（dgetrf/zgetrf、dgetri/zgetri）→ 残差应 ~O(ε) 量级
- 矩形 m×n（m>n / m<n）打包 LU 重放与 DLASWP(-1) 逆序回放行交换
- 复数 z 前缀（复模范数），并对齐实/复同式
- 异常面：ipiv 长度/取值/类型、shape、零分母、实复混搭、NaN/Inf

kind 命名沿用 verdict 的 D 前缀「dtype 无关」约定（DGET01 覆盖 SGET01/CGET01）。
"""
import pytest

pytest.importorskip("scipy")
import numpy as np
from scipy.linalg import lapack as sla

import thresholds
from verdict import residual_ratio

EPS = thresholds.EPS32


# ---------------------------------------------------------------------------
# DGET01（getrf）
# ---------------------------------------------------------------------------

def test_dget01_hand_value_no_pivot():
    """手算：A=[[4,3],[2,1]]，L=[[1,0],[.5,1]]、U=[[4,3],[0,-1]]（U[1,1] 扰 -0.5）。

    L·U=[[4,3],[2,0.5]] → diff=[[0,0],[0,-0.5]]，‖·‖₁=0.5；‖A‖₁=max(6,4)=6；
    ratio=0.5/(2·6·ε)。
    """
    a = np.array([[4.0, 3.0], [2.0, 1.0]])
    lu = np.array([[4.0, 3.0], [0.5, -1.0]])      # 打包 LU，ipiv 恒等
    ipiv = np.array([1, 2])
    assert residual_ratio("DGET01", a=a, lu=lu, ipiv=ipiv) == pytest.approx(
        0.5 / (2 * 6 * EPS), rel=1e-12)


def test_dget01_exact_recon_zero():
    a = np.array([[4.0, 3.0], [2.0, 1.0]])
    lu = np.array([[4.0, 3.0], [0.5, -0.5]])
    assert residual_ratio("DGET01", a=a, lu=lu, ipiv=np.array([1, 2])) == 0.0


def test_dget01_scipy_pivot_small():
    """带行交换的真解：scipy dgetrf + DLASWP 回放 → ratio 应远小于 1。"""
    rng = np.random.default_rng(7)
    a = rng.standard_normal((40, 40)) + 5 * np.eye(40)
    lu, piv, info = sla.dgetrf(a, overwrite_a=0)
    assert info == 0
    ratio = residual_ratio("DGET01", a=a, lu=lu, ipiv=piv + 1)   # scipy piv 是 0 基
    assert ratio < 100.0          # 健康浮点分解残差量级（归一化后 O(1..几十)）


@pytest.mark.parametrize("shape", [(30, 20), (20, 30)])
def test_dget01_rectangular(shape):
    """矩形 m>n / m<n：打包 LU 维度与回放都应收敛。"""
    rng = np.random.default_rng(11)
    a = rng.standard_normal(shape)
    lu, piv, info = sla.dgetrf(a, overwrite_a=0)
    assert info == 0
    ratio = residual_ratio("DGET01", a=a, lu=lu, ipiv=piv + 1)
    assert ratio < 1e6            # 矩形随机阵可能较病态，但仍应有限且不爆表


def test_dget01_ipiv_wrong_length_raises():
    a = np.array([[4.0, 3.0], [2.0, 1.0]])
    lu = np.array([[4.0, 3.0], [0.5, -0.5]])
    with pytest.raises(ValueError):
        residual_ratio("DGET01", a=a, lu=lu, ipiv=np.array([1, 2, 3]))


def test_dget01_ipiv_out_of_range_raises():
    a = np.array([[4.0, 3.0], [2.0, 1.0]])
    lu = np.array([[4.0, 3.0], [0.5, -0.5]])
    with pytest.raises(ValueError):
        residual_ratio("DGET01", a=a, lu=lu, ipiv=np.array([1, 3]))   # 3 > min(m,n)=2


def test_dget01_ipiv_non_integer_raises():
    a = np.array([[4.0, 3.0], [2.0, 1.0]])
    lu = np.array([[4.0, 3.0], [0.5, -0.5]])
    with pytest.raises(TypeError):
        residual_ratio("DGET01", a=a, lu=lu, ipiv=np.array([1.0, 2.0]))


def test_dget01_shape_mismatch_raises():
    with pytest.raises(ValueError):
        residual_ratio("DGET01", a=np.zeros((2, 2)), lu=np.zeros((3, 2)),
                       ipiv=np.array([1, 2]))


# ---------------------------------------------------------------------------
# DGET03（getri / getriBatched / matinvBatched）
# ---------------------------------------------------------------------------

def test_dget03_hand_value():
    """手算：A=diag(2,4)，C=diag(0.5,0.375) → W=I−C·A=diag(0,−0.5)，‖W‖₁=0.5；
    ‖A‖₁=4、‖C‖₁=0.5 → 0.5/(2·4·0.5·ε)=0.5/(4ε)=2097152。"""
    a = np.array([[2.0, 0.0], [0.0, 4.0]])
    ainv = np.array([[0.5, 0.0], [0.0, 0.375]])
    assert residual_ratio("DGET03", a=a, ainv=ainv) == 2097152.0


def test_dget03_scipy_inverse_small():
    rng = np.random.default_rng(3)
    a = rng.standard_normal((32, 32)) + 6 * np.eye(32)
    lu, piv, info = sla.dgetrf(a, overwrite_a=0)
    assert info == 0
    inv, info2 = sla.dgetri(lu, piv)
    assert info2 == 0
    ratio = residual_ratio("DGET03", a=a, ainv=inv)
    assert ratio < 100.0


def test_dget03_shape_and_zero_raise():
    with pytest.raises(ValueError):
        residual_ratio("DGET03", a=np.zeros((2, 2)), ainv=np.zeros((3, 3)))
    with pytest.raises(ValueError):     # ‖C‖₁ = 0
        residual_ratio("DGET03", a=np.array([[2.0, 0.0], [0.0, 4.0]]),
                       ainv=np.zeros((2, 2)))


# ---------------------------------------------------------------------------
# 复数（z 前缀，复模范数）
# ---------------------------------------------------------------------------

def test_complex_get01_small():
    rng = np.random.default_rng(5)
    a = (rng.standard_normal((24, 24)) + 1j * rng.standard_normal((24, 24))) \
        + 6 * np.eye(24)
    lu, piv, info = sla.zgetrf(a, overwrite_a=0)
    assert info == 0
    ratio = residual_ratio("DGET01", a=a, lu=lu, ipiv=piv + 1)
    assert ratio < 100.0
    assert isinstance(ratio, float)


def test_complex_get03_small():
    rng = np.random.default_rng(9)
    a = (rng.standard_normal((20, 20)) + 1j * rng.standard_normal((20, 20))) \
        + 6 * np.eye(20)
    lu, piv, info = sla.zgetrf(a, overwrite_a=0)
    assert info == 0
    inv, info2 = sla.zgetri(lu, piv)
    assert info2 == 0
    ratio = residual_ratio("DGET03", a=a, ainv=inv)
    assert ratio < 100.0


def test_complex_zero_diag_not_required():
    """DGET01/03 是通用阵口径：不对角实部校验（区别于 DPOT），复数对角可含虚部。"""
    a = np.array([[1.0 + 2.0j, 0.0], [0.0, 1.0 - 3.0j]])
    lu, piv, info = sla.zgetrf(a, overwrite_a=0)
    assert info == 0
    assert residual_ratio("DGET01", a=a, lu=lu, ipiv=piv + 1) < 1.0


def test_mixed_real_complex_raises():
    with pytest.raises(TypeError):
        residual_ratio("DGET03", a=np.eye(2),
                       ainv=np.eye(2, dtype=np.complex128))


def test_nan_inf_raise():
    with pytest.raises(ValueError):
        residual_ratio("DGET03", a=np.array([[1.0, np.nan], [0.0, 1.0]]),
                       ainv=np.eye(2))
    with pytest.raises(ValueError):
        residual_ratio("DGET01", a=np.eye(2),
                       lu=np.array([[1.0, np.inf], [0.0, 1.0]]),
                       ipiv=np.array([1, 2]))


def test_returns_python_float():
    got = residual_ratio("DGET03", a=np.array([[2.0, 0.0], [0.0, 4.0]]),
                         ainv=np.array([[0.5, 0.0], [0.0, 0.25]]))
    assert isinstance(got, float)