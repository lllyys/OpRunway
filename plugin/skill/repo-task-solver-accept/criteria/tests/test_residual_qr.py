# -*- coding: utf-8 -*-
"""QR 残差纯接口测试（DQRT01/02/03/16/17；lq-plan §1，QR 两册任务书 §3.2.2 口径）。

覆盖：
- 手算期望值（1-范数分母、ε=2^-24 归一、逐列 max、复模）
- 与 numpy 真解一致：complete QR（DQRT01/03）、reduced QR 正交列（DQRT02）、
  solve（DQRT16 方阵）、lstsq（DQRT17 超定）→ 残差应 ~O(1) 量级
- side ∈ {LEFT,RIGHT} × trans ∈ {N,T/C} 四组合
- 复数通路（共轭转置 Qᴴ/Rᴴ、复模范数），并对齐实/复同式
- 异常面：shape、side/trans 枚举、零分母、实复混搭、NaN/Inf、未知 kind

kind 命名沿用 verdict 的 D 前缀「dtype 无关」约定（DQRT01 覆盖 SQRT01/CQRT01）。
"""
import pytest
import numpy as np

import thresholds
from verdict import residual_ratio, KINDS

EPS = thresholds.EPS32


def _complete_qr(a):
    """返回 (q, r)：a = q·r，q 为 m×m 完整正交（酉）矩阵、r 为 m×n（上梯形）。"""
    q, r = np.linalg.qr(a, mode="complete")
    return q, r


# ---------------------------------------------------------------------------
# DQRT01（geqrf）
# ---------------------------------------------------------------------------

def test_dqrt01_hand_value():
    """手算：a=I₂、r=[[1,0],[0,2]]、q=I₂ → R−QᵀA=[[0,0],[0,1]]，‖·‖₁=1；
    ‖A‖₁=1；ratio=1/(2·1·ε)=0.5/ε。"""
    a = np.eye(2)
    r = np.array([[1.0, 0.0], [0.0, 2.0]])
    q = np.eye(2)
    assert residual_ratio("DQRT01", a=a, r=r, q=q) == pytest.approx(
        1.0 / (2 * EPS), rel=1e-12)


def test_dqrt01_exact_zero():
    a = np.eye(3)
    q, r = _complete_qr(a)
    assert residual_ratio("DQRT01", a=a, r=r, q=q) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("shape", [(40, 25), (25, 40), (30, 30)])
def test_dqrt01_true_qr_small(shape):
    """矩形 m>n / m<n / 方阵：真 Q·R 还原 → ratio 应远小于 1。"""
    rng = np.random.default_rng(11)
    a = rng.standard_normal(shape)
    q, r = _complete_qr(a)
    assert residual_ratio("DQRT01", a=a, r=r, q=q) < 100.0


def test_dqrt01_r_lower_half_ignored():
    """下三角（反射子）部分不进比对：塞入任意脏值结果不变。"""
    rng = np.random.default_rng(3)
    a = rng.standard_normal((10, 6))
    q, r = _complete_qr(a)
    polluted = np.tril(rng.standard_normal((10, 6)), -1) + np.triu(r)
    assert residual_ratio("DQRT01", a=a, r=polluted, q=q) == pytest.approx(
        residual_ratio("DQRT01", a=a, r=r, q=q), rel=1e-12)


# ---------------------------------------------------------------------------
# DQRT02（sorgqr / cungqr）
# ---------------------------------------------------------------------------

def test_dqrt02_hand_value():
    """手算：q=[[2,0],[0,1],[0,0]] → QᵀQ=[[4,0],[0,1]]，I−·=[[-3,0],[0,0]]，
    ‖·‖₁=3；分母 m=3 → ratio=3/(3·ε)=1/ε。"""
    q = np.array([[2.0, 0.0], [0.0, 1.0], [0.0, 0.0]])
    assert residual_ratio("DQRT02", q=q) == pytest.approx(1.0 / EPS, rel=1e-12)


def test_dqrt02_hand_value_offdiag():
    """手算：q=[[1,1],[0,0],[0,0]] → QᵀQ=[[1,1],[1,1]]，I−·=[[0,-1],[-1,0]]，
    ‖·‖₁=max(1,1)=1；ratio=1/(3·ε)。"""
    q = np.array([[1.0, 1.0], [0.0, 0.0], [0.0, 0.0]])
    assert residual_ratio("DQRT02", q=q) == pytest.approx(
        1.0 / (3 * EPS), rel=1e-12)


def test_dqrt02_orthonormal_columns_zero():
    rng = np.random.default_rng(5)
    a = rng.standard_normal((30, 12))
    q = np.linalg.qr(a, mode="reduced")[0]
    assert residual_ratio("DQRT02", q=q) == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# DQRT03（sormqr / cunmqr）
# ---------------------------------------------------------------------------

def test_dqrt03_left_exact_zero_and_perturb():
    q = np.eye(2)
    c = np.array([[1.0, 0.0], [0.0, 1.0]])
    opq = np.array([[1.0, 0.0], [0.0, 1.0]])
    assert residual_ratio("DQRT03", c=c, opq=opq, q=q, side="LEFT", trans="N") \
        == pytest.approx(0.0, abs=1e-12)
    # 扰动：diff=[[0,0],[0,2]]，‖·‖₁=2；‖C‖₁=1；m=2 → 2/(2·ε)=1/ε
    bad = np.array([[1.0, 0.0], [0.0, 3.0]])
    assert residual_ratio("DQRT03", c=c, opq=bad, q=q, side="LEFT", trans="N") \
        == pytest.approx(1.0 / EPS, rel=1e-12)


def test_dqrt03_right_transpose_zero():
    q = np.eye(3)
    rng = np.random.default_rng(9)
    c = rng.standard_normal((4, 3))
    opq = c @ q.T          # side=RIGHT, trans=T 的显式结果
    assert residual_ratio("DQRT03", c=c, opq=opq, q=q, side="RIGHT", trans="T") \
        == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("side", ["LEFT", "RIGHT"])
@pytest.mark.parametrize("trans", ["N", "T"])
def test_dqrt03_true_product_small(side, trans):
    """真正交 Q 的显式积 vs 同式 → 残差 ~0（真实链路 sormqr vs gemm 一致性）。"""
    rng = np.random.default_rng(17)
    a = rng.standard_normal((20, 20))
    q = _complete_qr(a)[0]
    c = rng.standard_normal((20, 14) if side == "LEFT" else (14, 20))
    qo = q if trans == "N" else q.T
    opq = qo @ c if side == "LEFT" else c @ qo
    assert residual_ratio("DQRT03", c=c, opq=opq, q=q, side=side, trans=trans) \
        < 100.0


# ---------------------------------------------------------------------------
# DQRT16（gels 方阵判据 m = n）
# ---------------------------------------------------------------------------

def test_dqrt16_hand_value():
    """手算：a=I₂、b=[[1],[2]]、x=[[1],[3]] → resid=[[0],[-1]]，‖·‖₁=1；
    ‖A‖₁=1、‖X₁‖₁=|1|+|3|=4、MAX(m,n)=2 → ratio=1/(2·1·4·ε)=1/(8ε)。"""
    a = np.eye(2)
    b = np.array([[1.0], [2.0]])
    x = np.array([[1.0], [3.0]])
    assert residual_ratio("DQRT16", a=a, b=b, x=x) == pytest.approx(
        1.0 / (8 * EPS), rel=1e-12)


def test_dqrt16_solve_exact_small():
    rng = np.random.default_rng(21)
    a = rng.standard_normal((30, 30)) + 10 * np.eye(30)
    b = rng.standard_normal((30, 4))
    x = np.linalg.solve(a, b)
    assert residual_ratio("DQRT16", a=a, b=b, x=x) < 100.0


def test_dqrt16_per_column_max():
    """逐列取 max：坏列决定结果（第 2 列残差更大）。"""
    a = np.eye(2)
    b = np.array([[1.0, 1.0], [1.0, 1.0]])
    x = np.array([[1.0, 1.0], [1.0, 2.0]])   # 第 2 列 x=[[1],[2]]，resid 第2列=[0,-1]
    # 第1列残差 0；第2列 ‖resid‖₁=1、‖X₂‖₁=3 → 1/(2·1·3·ε)
    assert residual_ratio("DQRT16", a=a, b=b, x=x) == pytest.approx(
        1.0 / (6 * EPS), rel=1e-12)


# ---------------------------------------------------------------------------
# DQRT17（gels 超定判据 m > n，IRESID=1）
# ---------------------------------------------------------------------------

def test_dqrt17_hand_value():
    """手算：a=[[1],[1]]、b=[[1],[3]]、x=[[1]] → resid=[[0],[2]]，RᵀA=[[0,2]]·a=2；
    ‖A‖₁=2、‖B‖₁=4、MAX(m,n,nrhs)=2 → ratio=2/(2·4·2·ε)。"""
    a = np.array([[1.0], [1.0]])
    b = np.array([[1.0], [3.0]])
    x = np.array([[1.0]])
    assert residual_ratio("DQRT17", a=a, b=b, x=x) == pytest.approx(
        2.0 / (16 * EPS), rel=1e-12)


def test_dqrt17_lstsq_optimal_zero():
    """最优解（正规方程残差 0）→ ratio_opt ~0。"""
    a = np.array([[1.0], [1.0]])
    b = np.array([[1.0], [3.0]])
    x = np.array([[2.0]])
    assert residual_ratio("DQRT17", a=a, b=b, x=x) == pytest.approx(0.0, abs=1e-12)


def test_dqrt17_lstsq_small():
    rng = np.random.default_rng(33)
    a = rng.standard_normal((40, 8))
    b = rng.standard_normal((40, 3))
    x = np.linalg.lstsq(a, b, rcond=None)[0]
    assert residual_ratio("DQRT17", a=a, b=b, x=x) < 100.0


# ---------------------------------------------------------------------------
# 复数通路（CQRT01/02/03/16/17 同 kind，conj().T + 复模）
# ---------------------------------------------------------------------------

def test_dqrt01_complex_true_qr_small():
    rng = np.random.default_rng(41)
    a = rng.standard_normal((30, 18)) + 1j * rng.standard_normal((30, 18))
    q, r = _complete_qr(a)
    assert residual_ratio("DQRT01", a=a, r=r, q=q) < 100.0


def test_dqrt02_complex_unitary_zero():
    rng = np.random.default_rng(42)
    a = rng.standard_normal((25, 9)) + 1j * rng.standard_normal((25, 9))
    q = np.linalg.qr(a, mode="reduced")[0]
    assert residual_ratio("DQRT02", q=q) == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("trans", ["N", "C"])
def test_dqrt03_complex_product_small(trans):
    rng = np.random.default_rng(43)
    a = rng.standard_normal((16, 16)) + 1j * rng.standard_normal((16, 16))
    q = _complete_qr(a)[0]
    c = rng.standard_normal((16, 10)) + 1j * rng.standard_normal((16, 10))
    qo = q if trans == "N" else q.conj().T
    opq = qo @ c
    assert residual_ratio("DQRT03", c=c, opq=opq, q=q, side="LEFT", trans=trans) \
        < 100.0


def test_dqrt16_complex_solve_small():
    rng = np.random.default_rng(44)
    a = rng.standard_normal((20, 20)) + 1j * rng.standard_normal((20, 20)) + 10 * np.eye(20)
    b = rng.standard_normal((20, 3)) + 1j * rng.standard_normal((20, 3))
    x = np.linalg.solve(a, b)
    assert residual_ratio("DQRT16", a=a, b=b, x=x) < 100.0


def test_dqrt17_complex_lstsq_small():
    rng = np.random.default_rng(45)
    a = rng.standard_normal((30, 6)) + 1j * rng.standard_normal((30, 6))
    b = rng.standard_normal((30, 2)) + 1j * rng.standard_normal((30, 2))
    x = np.linalg.lstsq(a, b, rcond=None)[0]
    assert residual_ratio("DQRT17", a=a, b=b, x=x) < 100.0


# ---------------------------------------------------------------------------
# 异常面
# ---------------------------------------------------------------------------

def test_kinds_include_qr():
    for k in ("DQRT01", "DQRT02", "DQRT03", "DQRT16", "DQRT17"):
        assert k in KINDS


def test_dqrt01_shape_and_q_size_errors():
    a = np.eye(3)
    q, r = _complete_qr(a)
    with pytest.raises(ValueError):
        residual_ratio("DQRT01", a=a, r=r[:-1, :], q=q)         # r 形状错
    with pytest.raises(ValueError):
        residual_ratio("DQRT01", a=a, r=r, q=q[:, :2])           # q 非 m×m
    with pytest.raises(ValueError):
        residual_ratio("DQRT01", a=np.zeros((3, 3)), r=r, q=q)   # 零分母


def test_dqrt03_enum_and_shape_errors():
    q = np.eye(3)
    c = np.ones((3, 4))
    opq = c.copy()
    with pytest.raises(ValueError):
        residual_ratio("DQRT03", c=c, opq=opq, q=q, side="MIDDLE", trans="N")
    with pytest.raises(ValueError):
        residual_ratio("DQRT03", c=c, opq=opq, q=q, side="LEFT", trans="X")
    with pytest.raises(ValueError):   # LEFT 要求 C 行数 = m
        residual_ratio("DQRT03", c=np.ones((2, 3)), opq=np.ones((2, 3)),
                       q=q, side="LEFT", trans="N")
    with pytest.raises(ValueError):   # opq 形状与 c 不一致
        residual_ratio("DQRT03", c=c, opq=np.ones((3, 5)), q=q,
                       side="LEFT", trans="N")
    with pytest.raises(ValueError):   # 零分母 ‖C‖₁=0
        residual_ratio("DQRT03", c=np.zeros((3, 4)), opq=np.zeros((3, 4)),
                       q=q, side="LEFT", trans="N")


def test_dqrt16_17_shape_errors():
    a = np.ones((4, 2))
    with pytest.raises(ValueError):
        residual_ratio("DQRT16", a=a, b=np.ones((4, 3)), x=np.ones((3, 3)))  # x 行数≠n
    with pytest.raises(ValueError):
        residual_ratio("DQRT16", a=a, b=np.ones((3, 2)), x=np.ones((2, 2)))  # b 行数≠m
    with pytest.raises(ValueError):   # x 某列 1-范数为 0
        residual_ratio("DQRT16", a=np.eye(2), b=np.ones((2, 2)),
                       x=np.array([[1.0, 0.0], [0.0, 0.0]]))
    with pytest.raises(ValueError):
        residual_ratio("DQRT17", a=a, b=np.zeros((4, 2)), x=np.ones((2, 2)))  # ‖B‖₁=0


def test_mixed_dtype_and_dirty_data():
    a = np.eye(3)
    q, r = _complete_qr(a)
    with pytest.raises(TypeError):     # 实/复混搭
        residual_ratio("DQRT01", a=a, r=r, q=q.astype(complex))
    with pytest.raises(ValueError):    # NaN
        residual_ratio("DQRT02", q=np.array([[np.nan, 0.0], [0.0, 1.0]]))
    with pytest.raises(ValueError):    # Inf
        residual_ratio("DQRT16", a=a, b=np.full((3, 1), np.inf), x=np.ones((3, 1)))


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        residual_ratio("DQRT99", q=np.eye(2))