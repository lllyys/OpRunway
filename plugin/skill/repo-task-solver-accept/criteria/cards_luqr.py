# -*- coding: utf-8 -*-
"""LU/QR 16 接口判据卡（lq-plan §1；1014 四册任务书 §3.2，2026-10-11 口径）。

独立模块（不与 cards_cholesky 合并）：Cholesky 三卡的 fail-closed 断言
（render_verify._check_op_spec）与 ``test_get_card_unknown_op_raises`` 依赖
``cards_cholesky.get_card("sgetrf")`` 抛错，故 LU/QR 卡自带 ``OPS``/``CARDS``/
``get_card``/``accepts``，只在 verdict/accept_run 的「统一取卡」分发层汇合。

范围（1014 四册，每册 4 接口，共 16）：
- LU 实：sgetrf / sgetri / sgetribatched / smatinvbatched；复：c 前缀同形
- QR 实：sgeqrf / sorgqr / sormqr / sgels；复：c 前缀同形

判据卡复用 ``cards_cholesky.CriteriaCard``（s2-A1 一段式单一数据类；本模块消费其
LU/QR 增量字段 residual_kind_of / threshold_ref_of / formula_of / threshold_of）。

一段式残差（与 Cholesky s2-A1 同模型）：每个 accuracy 用例直接算 LAPACK 残差对阈值判。
残差的原始输入取实现实际 fp32 输入（A32/B32/C32/q32 等），升 f64/c128 在
``verdict.residual_ratio`` 内部统一做（与 Cholesky 换基口径一致）。

残差喂数与阈值（1014 四册任务书 §3.2.1/§3.2.2）：
- getrf（S/CGET01）：a=A32、lu=被测打包 LU、ipiv=被测主元；阈值 max(5r, 0.3 实/0.5 复)；
- getri（S/CGET03）：a=restore_lu(输入 LU32, 输入 ipiv)、ainv=被测逆；阈值 max(5r, 0.05)；
- getriBatched（S/CGET03，逐矩阵）：a=restore_lu(输入 LU32, 输入 ipiv)、ainv=被测逆；
  阈值 max(5r_i, 0.05)；
- matinvBatched（S/CGET03，逐矩阵）：a=A32（原始输入）、ainv=被测逆；阈值 max(5r_i, 0.5)；
- geqrf（S/CQRT01）：a=A32、r=被测打包、q=由被测 (v,tau) 构造的 m×m Q；
  阈值 max(5r, 0.1)；
- *gqr（S/CQRT02）：q=被测 Q；阈值 max(5r, 1.5)；
- *mqr（S/CQRT03）：c=C32、opq=被测输出、q=q32（canonical 显式 Q）、side/trans；
  阈值 max(5r, 0.1)；
- gels：**双判据按 m 动态选**——m=n 走 DQRT16（方阵）、m>n 走 DQRT17（超定），
  阈值相对线随形状取 ratio_cpu（方阵）/ ratio_opt_cpu（超定）；实/复同式
  （方阵 max(5r, 0.015)、超定 10·ratio_opt_cpu）——由 threshold_ref_of / formula_of /
  threshold_of 三个钩子按 case 形状分流。

dut_out 契约（判据卡消费面，sim_dut 须照此产）：
- 公共：out32（被测输出）、info、status；
- getrf 另带 ipiv（被测主元，1 基）；
- geqrf 另带 tau（被测反射子系数，长度 min(m,n)）。

实/复：残差核按 dtype 自动分流（verdict.residual_ratio：复模、共轭转置、升 c128），
阈值公式与数值按实/复两册书分别取（getrf 地板不同，其余同式）。
"""

import functools

import numpy as np

try:  # criteria 作为包被导入时
    from . import thresholds, verdict
    from .cards_cholesky import CriteriaCard
except ImportError:  # criteria 目录直接挂 sys.path 时
    import thresholds
    import verdict
    from cards_cholesky import CriteriaCard

REAL_LU_OPS = ("sgetrf", "sgetri", "sgetribatched", "smatinvbatched")
COMPLEX_LU_OPS = ("cgetrf", "cgetri", "cgetribatched", "cmatinvbatched")
REAL_QR_OPS = ("sgeqrf", "sorgqr", "sormqr", "sgels")
COMPLEX_QR_OPS = ("cgeqrf", "cungqr", "cunmqr", "cgels")
OPS = REAL_LU_OPS + COMPLEX_LU_OPS + REAL_QR_OPS + COMPLEX_QR_OPS
COMPLEX_OPS = COMPLEX_LU_OPS + COMPLEX_QR_OPS
BATCHED_OPS = ("sgetribatched", "cgetribatched", "smatinvbatched", "cmatinvbatched")

_GETRF_OPS = ("sgetrf", "cgetrf")
_GETRI_OPS = ("sgetri", "cgetri")
_GETRIBATCHED_OPS = ("sgetribatched", "cgetribatched")
_MATINVBATCHED_OPS = ("smatinvbatched", "cmatinvbatched")
_GEQRF_OPS = ("sgeqrf", "cgeqrf")
_GQR_OPS = ("sorgqr", "cungqr")
_MQR_OPS = ("sormqr", "cunmqr")
_GELS_OPS = ("sgels", "cgels")


def _get(case_arrays, key, op):
    if key not in case_arrays:
        raise KeyError(f"case_arrays 缺 {key}（{op} 卡需要，lq-plan §2.1）")
    return case_arrays[key]


def _is_cx(*arrs):
    return any(np.issubdtype(np.asarray(a).dtype, np.complexfloating) for a in arrs)


def _orgqr_full(packed, tau, nq, complex_):
    """由被测打包反射子 (packed, tau) 升精度经 CPU orgqr/ungqr 生成 nq×nq 完整
    正交/酉矩阵 Q（SQRT01 口径：列数取行数；升精度使构造不引入额外舍入）。

    packed 为 m×n 被测 geqrf 输出，k=min(m,n)；补零到 nq×nq，用前 k 个反射子。
    """
    from scipy.linalg import lapack
    dt = np.complex128 if complex_ else np.float64
    p = np.array(np.asarray(packed), dtype=dt, order="F")
    t = np.asarray(tau, dtype=dt).ravel()
    k = min(p.shape[1], nq)
    pad = np.zeros((nq, nq), dtype=dt, order="F")
    pad[:, :k] = p[:, :k]
    fn = lapack.zungqr if complex_ else lapack.dorgqr
    q, _work, info = fn(pad, t[:k])
    if info != 0:
        raise ValueError(f"orgqr/ungqr 构造 Q 失败: info={info}")
    return q


# ---------------------------------------------------------------------------
# 残差喂数（A 卡 residual_kwargs：(case_arrays, dut_out) -> kwargs）
# ---------------------------------------------------------------------------

def _getrf_kwargs(case_arrays, dut_out):
    return {"a": _get(case_arrays, "A32", "getrf"),
            "lu": dut_out["out32"], "ipiv": dut_out["ipiv"]}


def _getri_kwargs(case_arrays, dut_out):
    """getri：a = 输入 LU32 按 ipiv 还原（实现实际输入升精度口径，任务书
    §3.2.1.2），ainv = 被测逆（SGET03 ‖I−C·A‖）。"""
    lu32 = np.asarray(_get(case_arrays, "LU32", "getri"))
    ipiv = _get(case_arrays, "ipiv", "getri")
    return {"a": verdict.restore_lu(lu32, ipiv), "ainv": dut_out["out32"]}


def _getribatched_kwargs(case_arrays, dut_out):
    """DGET03：a = 输入 LU32 按 ipiv 还原（实现实际输入口径），ainv = 被测逆。"""
    lu32 = np.asarray(_get(case_arrays, "LU32", "getribatched"))
    ipiv = _get(case_arrays, "ipiv", "getribatched")
    return {"a": verdict.restore_lu(lu32, ipiv), "ainv": dut_out["out32"]}


def _matinvbatched_kwargs(case_arrays, dut_out):
    """DGET03：a = 原始输入 A32（实现实际输入口径），ainv = 被测逆。"""
    return {"a": _get(case_arrays, "A32", "matinvbatched"), "ainv": dut_out["out32"]}


def _geqrf_kwargs(case_arrays, dut_out):
    packed = np.asarray(dut_out["out32"])
    cx = _is_cx(packed)
    q = _orgqr_full(packed, dut_out["tau"], packed.shape[0], cx)
    return {"a": _get(case_arrays, "A32", "geqrf"), "r": dut_out["out32"], "q": q}


def _gqr_kwargs(case_arrays, dut_out):
    return {"q": dut_out["out32"]}


def _mqr_kwargs(case_arrays, dut_out):
    return {"c": _get(case_arrays, "C32", "mqr"), "opq": dut_out["out32"],
            "q": _get(case_arrays, "q32", "mqr"),
            "side": _get(case_arrays, "side", "mqr"), "trans": _get(case_arrays, "trans", "mqr")}


def _gels_kwargs(case_arrays, dut_out):
    return {"a": _get(case_arrays, "A32", "gels"), "b": _get(case_arrays, "B32", "gels"),
            "x": dut_out["out32"]}


# ---------------------------------------------------------------------------
# gels 双判据（按 m 形状动态）
# ---------------------------------------------------------------------------

def _gels_is_square(case_arrays):
    m, n = case_arrays.get("m"), case_arrays.get("n")
    if m is None or n is None:
        raise ValueError("gels 卡需要 case_arrays 带 m/n（canonical 字段）")
    return int(m) == int(n)


def _gels_residual_kind_of(case_arrays, dut_out):
    return "DQRT16" if _gels_is_square(case_arrays) else "DQRT17"


def _gels_threshold_ref_of(case_arrays):
    key = "ratio_cpu" if _gels_is_square(case_arrays) else "ratio_opt_cpu"
    return case_arrays.get(key)


def _gels_formula_of(case_arrays):
    return (thresholds.GELS_SQUARE_FORMULA if _gels_is_square(case_arrays)
            else thresholds.GELS_OVERDET_FORMULA)


def _gels_threshold_of(case_arrays, ref):
    return thresholds.gels_threshold(ref, square=_gels_is_square(case_arrays))


# ---------------------------------------------------------------------------
# 卡构造
# ---------------------------------------------------------------------------

def _card_getrf(op, complex_):
    return CriteriaCard(
        op=op,
        residual_kind="DGET01",
        formula=thresholds.GETRF_FORMULA["complex" if complex_ else "real"],
        threshold_fn=functools.partial(thresholds.getrf_threshold, complex_=complex_),
        residual_kwargs=_getrf_kwargs,
    )


def _card_getri(op):
    return CriteriaCard(
        op=op,
        residual_kind="DGET03",
        formula=thresholds.GETRI_FORMULA,
        threshold_fn=thresholds.getri_threshold,
        residual_kwargs=_getri_kwargs,
    )


def _card_batched_inverse(op, threshold_fn, formula, kwargs_fn):
    return CriteriaCard(
        op=op,
        residual_kind="DGET03",
        formula=formula,
        threshold_fn=threshold_fn,
        residual_kwargs=kwargs_fn,
        batched=True,
        info_kind="array",          # 任务书：info 为 batch 个 int 的数组
        base_op=op,
    )


def _card_geqrf(op):
    return CriteriaCard(
        op=op,
        residual_kind="DQRT01",
        formula=thresholds.GEQRF_MQR_FORMULA,
        threshold_fn=thresholds.geqrf_mqr_threshold,
        residual_kwargs=_geqrf_kwargs,
    )


def _card_gqr(op):
    return CriteriaCard(
        op=op,
        residual_kind="DQRT02",
        formula=thresholds.GQR_FORMULA,
        threshold_fn=thresholds.gqr_threshold,
        residual_kwargs=_gqr_kwargs,
    )


def _card_mqr(op):
    return CriteriaCard(
        op=op,
        residual_kind="DQRT03",
        formula=thresholds.GEQRF_MQR_FORMULA,
        threshold_fn=thresholds.geqrf_mqr_threshold,
        residual_kwargs=_mqr_kwargs,
    )


def _card_gels(op):
    return CriteriaCard(
        op=op,
        residual_kind="DQRT16",     # 静态占位；实际由 residual_kind_of 按 m 选
        formula=thresholds.GELS_SQUARE_FORMULA,
        threshold_fn=functools.partial(thresholds.gels_threshold, square=True),
        residual_kwargs=_gels_kwargs,
        residual_kind_of=_gels_residual_kind_of,
        threshold_ref_of=_gels_threshold_ref_of,
        formula_of=_gels_formula_of,
        threshold_of=_gels_threshold_of,
    )


def _build_cards():
    cards = {}
    for op in _GETRF_OPS:
        cards[op] = _card_getrf(op, op in COMPLEX_OPS)
    for op in _GETRI_OPS:
        cards[op] = _card_getri(op)
    for op in _GETRIBATCHED_OPS:
        cards[op] = _card_batched_inverse(
            op, thresholds.getribatched_threshold,
            thresholds.GETRIBATCHED_FORMULA, _getribatched_kwargs)
    for op in _MATINVBATCHED_OPS:
        cards[op] = _card_batched_inverse(
            op, thresholds.matinvbatched_threshold,
            thresholds.MATINVBATCHED_FORMULA, _matinvbatched_kwargs)
    for op in _GEQRF_OPS:
        cards[op] = _card_geqrf(op)
    for op in _GQR_OPS:
        cards[op] = _card_gqr(op)
    for op in _MQR_OPS:
        cards[op] = _card_mqr(op)
    for op in _GELS_OPS:
        cards[op] = _card_gels(op)
    return cards


CARDS = _build_cards()
assert set(CARDS) == set(OPS), (set(OPS) - set(CARDS), set(CARDS) - set(OPS))


def get_card(op):
    """按 canonical 算子名取 LU/QR 判据卡；未知算子抛 ValueError。"""
    card = CARDS.get(op)
    if card is None:
        raise ValueError(
            f"未知 LU/QR 算子 {op!r}，只支持 {OPS}（lq-plan §1）")
    return card


def accepts(op):
    """op 是否属本模块覆盖的 16 接口（统一取卡分发层用）。"""
    return op in CARDS