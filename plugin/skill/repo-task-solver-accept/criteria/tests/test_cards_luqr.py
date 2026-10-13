# -*- coding: utf-8 -*-
"""LU/QR 16 接口判据卡测试（cards_luqr，lq-plan §1）。

覆盖（s2-A1 一段式：每用例直接算 LAPACK 残差对阈值判，无 layer1/fallback 分层）：
- 注册表：16 接口齐全、批量卡身份（batched/info_kind="array"）、残差 kind 合法；
- getrf：DGET01 还原主判（实/复）；
- getri：DGET03 还原喂数（a=restore_lu(LU32,ipiv)）；
- geqrf：DQRT01（被测 (v,tau) 构造 Q，Qᴴ·A vs R）；
- *gqr / *mqr：DQRT02 / DQRT03；
- gels 双判据（m=n→DQRT16 / m>n→DQRT17）与阈值相对线随形状切换；
- 批量 getriBatched/matinvBatched 逐矩阵 DGET03；
- 与 cards_cholesky 的 fail-closed 边界：cards_cholesky.get_card 仍拒 LU/QR 算子。
"""
import pytest

pytest.importorskip("scipy")
import numpy as np
from scipy.linalg import lapack as sla

import cards_cholesky
import cards_luqr
import thresholds
import verdict

F32 = np.float32


def _dut(out, info=0, status="ok", **extra):
    d = {"out32": np.asarray(out), "info": info, "status": status}
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

def test_registry_covers_16_ops():
    assert len(cards_luqr.OPS) == 16
    assert set(cards_luqr.CARDS) == set(cards_luqr.OPS)
    for op in cards_luqr.OPS:
        card = cards_luqr.get_card(op)
        assert card.op == op
        assert card.residual_kind in verdict.KINDS
        assert callable(card.residual_kwargs)


def test_batched_identity():
    for op in cards_luqr.BATCHED_OPS:
        card = cards_luqr.get_card(op)
        assert card.batched is True
        assert card.info_kind == "array"
        assert card.base_op == op
    for op in ("sgetrf", "sgetri", "sgeqrf", "sorgqr", "sormqr", "sgels"):
        assert cards_luqr.get_card(op).batched is False


def test_getri_card_identity():
    for op in ("sgetri", "cgetri"):
        card = cards_luqr.get_card(op)
        assert card.batched is False
        assert card.residual_kind == "DGET03"
        assert card.formula == thresholds.GETRI_FORMULA


def test_unknown_op_raises_and_cholesky_still_rejects():
    with pytest.raises(ValueError):
        cards_luqr.get_card("spotrf")
    with pytest.raises(ValueError):
        cards_luqr.get_card("not_an_op")
    # 边界：Cholesky 模块不得因 LU/QR 注册被污染（fail-closed 断言依赖）
    with pytest.raises(ValueError):
        cards_cholesky.get_card("sgetrf")


# ---------------------------------------------------------------------------
# getrf：DGET01 还原主判
# ---------------------------------------------------------------------------

A2 = np.array([[4.0, 3.0], [2.0, 1.0]])
LU2 = np.array([[4.0, 3.0], [0.5, -0.5]])       # L·U = A（ipiv 恒等）


def _getrf_case(ratio_cpu=0.0, status="ok"):
    return {"A64": A2.copy(), "A32": A2.astype(F32),
            "ratio_cpu": ratio_cpu, "ratio_cpu_status": status}


def test_getrf_residual_pass():
    card = cards_luqr.get_card("sgetrf")
    v = verdict.judge(card, _getrf_case(),
                      _dut(LU2.astype(F32), ipiv=np.array([1, 2])))
    assert v["error"] is None and v["numeric"] == "PASS"
    assert v["residual"]["ran"] is True
    assert v["residual"]["ratio"] == 0.0
    assert v["residual"]["formula"] == thresholds.GETRF_FORMULA["real"]


def test_getrf_bad_lu_fails_residual():
    """lu[1,1] 由 -0.5 扰到 -0.4 → L·U 的 (1,1)=1.1 vs A(1,1)=1；
    DGET01 ratio ≈ 0.1/(2·6·ε) 手算；ratio_cpu=0 → 阈值 max(0,0.3)=0.3
    → 数值 FAIL。（-0.4 非 FP32 精确值，残差按 FP32 量化口径，容差 rel=1e-5。）"""
    card = cards_luqr.get_card("sgetrf")
    bad = LU2.copy()
    bad[1, 1] = -0.4
    v = verdict.judge(card, _getrf_case(), _dut(bad.astype(F32), ipiv=np.array([1, 2])))
    assert v["error"] is None
    assert v["residual"]["ran"] is True
    assert v["residual"]["ratio"] == pytest.approx(0.1 / (2 * 6 * thresholds.EPS32),
                                                   rel=1e-5)
    assert v["residual"]["threshold"] == pytest.approx(0.3)
    assert v["residual"]["formula"] == thresholds.GETRF_FORMULA["real"]
    assert v["numeric"] == "FAIL"


def test_getrf_complex_residual_pass():
    """复数 getrf：scipy zgetrf 真解 → 还原（复模）命中 → CGET01 数值 PASS。"""
    rng = np.random.default_rng(21)
    a = rng.standard_normal((5, 5)) + 1j * rng.standard_normal((5, 5)) + 5 * np.eye(5)
    lu, piv, info = sla.zgetrf(a, overwrite_a=0)
    assert info == 0
    case = {"A64": a, "A32": a.astype(np.complex64),
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    card = cards_luqr.get_card("cgetrf")
    v = verdict.judge(card, case, _dut(lu.astype(np.complex64), ipiv=piv + 1))
    assert v["error"] is None
    assert v["residual"]["ran"] is True
    assert v["numeric"] == "PASS"


def test_getrf_complex_threshold_floor():
    card = cards_luqr.get_card("cgetrf")
    assert card.formula == thresholds.GETRF_FORMULA["complex"]
    assert card.threshold_fn(0.0) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# getri：DGET03（a = restore_lu(LU32, ipiv)）
# ---------------------------------------------------------------------------

def _getri_fixture(n=3, seed=11):
    """返回 (LU32, piv, inv)：inv 对「LU32 按 ipiv 还原后的 a」求（与卡喂数同一 a），
    使正例残差仅 f64 eps 量级。"""
    rng = np.random.default_rng(seed)
    A = rng.standard_normal((n, n)) + 4 * np.eye(n)
    lu, piv, info = sla.dgetrf(A, overwrite_a=0)
    assert info == 0
    lu32 = np.ascontiguousarray(lu.astype(F32))
    a = verdict.restore_lu(lu32, piv + 1)
    inv = np.linalg.inv(a)
    return lu32, piv, inv


def test_getri_residual_pass():
    lu32, piv, inv = _getri_fixture()
    card = cards_luqr.get_card("sgetri")
    case = {"LU64": lu32.astype(np.float64), "LU32": lu32, "ipiv": piv + 1,
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(card, case, _dut(inv))
    assert v["error"] is None and v["numeric"] == "PASS"
    assert v["residual"]["ran"] is True
    assert v["residual"]["formula"] == thresholds.GETRI_FORMULA


def test_getri_bad_inverse_fails_residual():
    lu32, piv, inv = _getri_fixture()
    card = cards_luqr.get_card("sgetri")
    case = {"LU64": lu32.astype(np.float64), "LU32": lu32, "ipiv": piv + 1,
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(card, case, _dut(inv + 1.0))
    assert v["error"] is None
    assert v["residual"]["ran"] is True
    assert v["residual"]["threshold"] == pytest.approx(0.05)
    assert v["numeric"] == "FAIL"


# ---------------------------------------------------------------------------
# geqrf：DQRT01（被测 (v,tau) → Q）
# ---------------------------------------------------------------------------

def _geqrf_fixture(m, n, seed=3, complex_=False):
    """在 FP32 可表示阵上分解：返回 (a32, qr_f64, tau_f64)。a32 与 (qr,tau) 同源，
    升精度进判定后 Qᴴ·A ≈ R 精确成立。"""
    rng = np.random.default_rng(seed)
    a = rng.standard_normal((m, n))
    if complex_:
        a = (a + 1j * rng.standard_normal((m, n))).astype(np.complex64)
        qr, tau, _w, info = sla.zgeqrf(a.astype(np.complex128), overwrite_a=0)
    else:
        a = a.astype(F32)
        qr, tau, _w, info = sla.dgeqrf(a.astype(np.float64), overwrite_a=0)
    assert info == 0
    return a, qr, tau


def test_geqrf_residual_pass():
    a, qr, tau = _geqrf_fixture(6, 4)
    card = cards_luqr.get_card("sgeqrf")
    case = {"A64": a.astype(np.float64), "A32": a,
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(card, case, _dut(qr, tau=tau))
    assert v["error"] is None
    assert v["residual"]["ran"] is True
    assert v["residual"]["formula"] == thresholds.GEQRF_MQR_FORMULA
    assert v["numeric"] == "PASS"


def test_geqrf_bad_tau_fails_residual():
    """扰动 tau → 由 (v,tau) 构造的 Q 偏 → R ≠ Qᴴ·A → DQRT01 挂；ratio_cpu=0 → 阈值 0.1。"""
    a, qr, tau = _geqrf_fixture(5, 3)
    card = cards_luqr.get_card("sgeqrf")
    case = {"A64": a.astype(np.float64), "A32": a,
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    bad_tau = tau.copy()
    bad_tau[0] += 0.5
    v = verdict.judge(card, case, _dut(qr, tau=bad_tau))
    assert v["residual"]["ran"] is True
    assert v["residual"]["threshold"] == pytest.approx(0.1)
    assert v["numeric"] == "FAIL"


# ---------------------------------------------------------------------------
# *gqr / *mqr：DQRT02 / DQRT03
# ---------------------------------------------------------------------------

def test_gqr_residual_pass_and_fail():
    card = cards_luqr.get_card("sorgqr")
    case = {"golden64": np.eye(6, 4), "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(card, case, _dut(np.eye(6, 4)))          # Q 正交 → DQRT02≈0
    assert v["error"] is None and v["numeric"] == "PASS"
    assert v["residual"]["formula"] == thresholds.GQR_FORMULA
    # 挂 → DQRT02 阈值 max(5r,1.5)
    v2 = verdict.judge(card, case, _dut(np.zeros((6, 4), dtype=F32)))
    assert v2["residual"]["ran"] is True
    assert v2["residual"]["threshold"] == pytest.approx(1.5)
    assert v2["numeric"] == "FAIL"


def _mqr_fixture(m, n, k, side, trans, seed=5):
    """构造 (C32, q32)：q 由随机 (nq,k) 阵 geqrf 的反射子经 orgqr 显式生成。"""
    rng = np.random.default_rng(seed)
    nq = m if side == "LEFT" else n
    M = rng.standard_normal((nq, k))
    af, tau, _w, info = sla.dgeqrf(M, overwrite_a=0)
    assert info == 0
    C = rng.standard_normal((m, n))
    pad = np.zeros((nq, nq))
    pad[:, :k] = af[:, :k]
    q, _w3, info3 = sla.dorgqr(np.array(pad, order="F"), tau[:k])
    assert info3 == 0
    return C.astype(F32), q.astype(F32)


def test_mqr_residual_pass_and_fail():
    C32, q32 = _mqr_fixture(5, 4, 3, "LEFT", "N")
    ref = q32.astype(np.float64) @ C32.astype(np.float64)   # side=LEFT, trans=N
    card = cards_luqr.get_card("sormqr")
    case = {"C32": C32, "q32": q32, "golden64": ref, "side": "LEFT", "trans": "N",
            "ratio_cpu": 0.0, "ratio_cpu_status": "ok"}
    v = verdict.judge(card, case, _dut(ref))
    assert v["error"] is None and v["numeric"] == "PASS"
    assert v["residual"]["formula"] == thresholds.GEQRF_MQR_FORMULA
    v2 = verdict.judge(card, case, _dut(ref + 1.0))
    assert v2["residual"]["ran"] is True
    assert v2["residual"]["threshold"] == pytest.approx(0.1)
    assert v2["numeric"] == "FAIL"


# ---------------------------------------------------------------------------
# gels：双判据 + 阈值相对线随形状切换
# ---------------------------------------------------------------------------

def _gels_square(n=4, nrhs=2, seed=7):
    rng = np.random.default_rng(seed)
    a = rng.standard_normal((n, n)) + 3 * np.eye(n)
    b = rng.standard_normal((n, nrhs))
    # scipy dgels 返回 (lqr, x, info)：x 才是解(max(m,n)×nrhs)
    _lqr, x, info = sla.dgels(a, b, trans="N", overwrite_a=0, overwrite_b=0)
    assert info == 0
    return a, b, x[:n, :]


def _gels_over(m, n, nrhs=2, seed=9):
    rng = np.random.default_rng(seed)
    a = rng.standard_normal((m, n))
    b = rng.standard_normal((m, nrhs))
    _lqr, x, info = sla.dgels(a, b, trans="N", overwrite_a=0, overwrite_b=0)
    assert info == 0
    return a, b, x[:n, :]


def test_gels_square_selects_dqrt16_and_ratio_line():
    a, b, x = _gels_square()
    card = cards_luqr.get_card("sgels")
    case = {"A64": a, "A32": a.astype(F32), "B64": b, "B32": b.astype(F32),
            "golden64": x, "golden32": x.astype(F32),
            "m": 4, "n": 4, "ratio_cpu": 0.5, "ratio_opt_cpu": 9.0,
            "ratio_cpu_status": "ok"}
    assert card.residual_kind_of(case, None) == "DQRT16"
    assert card.threshold_ref_of(case) == 0.5
    assert card.formula_of(case) == thresholds.GELS_SQUARE_FORMULA
    assert card.threshold_of(case, 0.5) == pytest.approx(2.5)   # max(5·0.5, 0.015)
    v = verdict.judge(card, case, _dut(x))
    assert v["numeric"] == "PASS"
    # 挂 → DQRT16，阈值 = max(5·ratio_cpu, 0.015) = 2.5
    v2 = verdict.judge(card, case, _dut(x + 1.0))
    assert v2["residual"]["ran"] is True
    assert v2["residual"]["threshold"] == pytest.approx(2.5)
    assert v2["numeric"] == "FAIL"


def test_gels_overdetermined_selects_dqrt17_and_ratio_opt_line():
    a, b, x = _gels_over(7, 4)
    card = cards_luqr.get_card("sgels")
    case = {"A64": a, "A32": a.astype(F32), "B64": b, "B32": b.astype(F32),
            "golden64": x, "golden32": x.astype(F32),
            "m": 7, "n": 4, "ratio_cpu": 0.5, "ratio_opt_cpu": 0.25,
            "ratio_cpu_status": "ok"}
    assert card.residual_kind_of(case, None) == "DQRT17"
    assert card.threshold_ref_of(case) == 0.25
    assert card.formula_of(case) == thresholds.GELS_OVERDET_FORMULA
    v = verdict.judge(card, case, _dut(x))
    assert v["numeric"] == "PASS"
    v2 = verdict.judge(card, case, _dut(x + 1.0))
    assert v2["residual"]["ran"] is True
    assert v2["residual"]["threshold"] == pytest.approx(2.5)   # 10·0.25
    assert v2["numeric"] == "FAIL"


def test_gels_complex_same_formula_as_real():
    """1014 定稿：gels 实/复同式（方阵 max(5r,0.015)、超定 10·r_opt）。"""
    card = cards_luqr.get_card("cgels")
    assert card.formula == thresholds.GELS_SQUARE_FORMULA
    assert card.threshold_fn(0.0) == pytest.approx(thresholds.GELS_SQUARE_FLOOR)
    square = {"m": 4, "n": 4}
    assert card.residual_kind_of(square, None) == "DQRT16"
    assert card.formula_of(square) == thresholds.GELS_SQUARE_FORMULA
    overdet = {"m": 7, "n": 4}
    assert card.residual_kind_of(overdet, None) == "DQRT17"
    assert card.formula_of(overdet) == thresholds.GELS_OVERDET_FORMULA


# ---------------------------------------------------------------------------
# 批量 getriBatched / matinvBatched：逐矩阵 DGET03
# ---------------------------------------------------------------------------

def _batched_inv_fixture(batch=3, n=4, seed=13):
    """逐矩阵真 LU/ipiv 与逆：逆对「LU32 按 ipiv 还原后的 a」求（getriBatched 喂数
    同一 a），使正例残差仅 f64 eps 量级。返回 (A64, LU64, LU32, ipiv, golden)。"""
    rng = np.random.default_rng(seed)
    A64 = np.empty((batch, n, n))
    LU64 = np.empty((batch, n, n))
    LU32 = np.empty((batch, n, n), dtype=F32)
    ipiv = np.empty((batch, n), dtype=np.int32)
    golden = np.empty((batch, n, n))
    for i in range(batch):
        A64[i] = rng.standard_normal((n, n)) + 4 * np.eye(n)
        lu, piv, info = sla.dgetrf(A64[i], overwrite_a=0)
        assert info == 0
        LU64[i] = lu
        LU32[i] = lu.astype(F32)
        ipiv[i] = piv + 1
        golden[i] = np.linalg.inv(verdict.restore_lu(LU32[i], ipiv[i]))
    return A64, LU64, LU32, ipiv, golden


def test_getribatched_all_pass():
    A64, LU64, LU32, ipiv, golden = _batched_inv_fixture()
    batch = A64.shape[0]
    case = {"A64": A64, "A32": A64.astype(F32),
            "LU64": LU64, "LU32": LU32, "ipiv": ipiv,
            "golden64": golden, "golden32": golden.astype(F32),
            "batch": batch, "ratio_cpu": np.zeros(batch), "ratio_cpu_status": "ok"}
    card = cards_luqr.get_card("sgetribatched")
    v = verdict.judge(card, case,
                      _dut(golden, info=np.zeros(batch, dtype=np.int32)))
    assert v["numeric"] == "PASS", v
    assert v["fail_count"] == 0
    assert v["diagnostics"]["batch"] == batch


def test_getribatched_one_bad_matrix_fails_dget03():
    A64, LU64, LU32, ipiv, golden = _batched_inv_fixture()
    batch = A64.shape[0]
    case = {"A64": A64, "A32": A64.astype(F32),
            "LU64": LU64, "LU32": LU32, "ipiv": ipiv,
            "golden64": golden, "golden32": golden.astype(F32),
            "batch": batch, "ratio_cpu": np.zeros(batch), "ratio_cpu_status": "ok"}
    out = golden.copy()
    out[1] = 0.0                                         # 第 1 个矩阵为零逆
    card = cards_luqr.get_card("sgetribatched")
    v = verdict.judge(card, case, _dut(out, info=np.zeros(batch, dtype=np.int32)))
    assert v["numeric"] == "FAIL"
    assert v["fail_count"] == 1
    assert v["first_fail_index"] == 1
    w = v["worst"]
    assert w["residual"]["ran"] is True                  # DGET03 还原喂数路径被走到
    assert w["residual"]["threshold"] == pytest.approx(0.05)


def test_matinvbatched_all_pass():
    A64, _LU64, _LU32, _ipiv, _golden = _batched_inv_fixture()
    batch = A64.shape[0]
    # matinvBatched 的 a 是原始输入 A（非 LU 还原），故逆对 A32 求
    golden = np.stack([np.linalg.inv(A64[i].astype(F32).astype(np.float64))
                       for i in range(batch)])
    case = {"A64": A64, "A32": A64.astype(F32),
            "golden64": golden, "golden32": golden.astype(F32),
            "batch": batch, "ratio_cpu": np.zeros(batch), "ratio_cpu_status": "ok"}
    card = cards_luqr.get_card("smatinvbatched")
    v = verdict.judge(card, case,
                      _dut(golden, info=np.zeros(batch, dtype=np.int32)))
    assert v["numeric"] == "PASS"
    # 一坏矩阵 → DGET03，阈值 max(5r,0.5)
    out = golden.copy()
    out[2] = 0.0
    v2 = verdict.judge(card, case, _dut(out, info=np.zeros(batch, dtype=np.int32)))
    assert v2["numeric"] == "FAIL"
    assert v2["worst"]["residual"]["threshold"] == pytest.approx(0.5)