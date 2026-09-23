# -*- coding: utf-8 -*-
"""Cholesky 三接口（spotrf/spotrs/spotri）的判据卡（A 卡·criteria 内核，spec §2.3/§2.3′）。

「卡」把三件事从裁决流程里拆出来：layer1 主判比对哪些目标（及并报哪些诊断）、
fallback 喂哪些数组、用哪条阈值公式。judge 只与卡的成员交互（residual_kind、
fallback_formula、fallback_threshold、layer1_targets、layer1_diagnostics、
fallback_kwargs），流程本身族无关，但本片**不承诺卡的构造方式族无关**
（spec §1，跨族泛化推迟证明）。

layer1 主判比对目标按任务书 §3.2（spec §2.3′，优先于 §2.3 冲突部分）：
- spotrf: 被测因子还原 recon = L·Lᵀ（L 侧）/ Uᵀ·U（U 侧）后取**指定三角**对原 A。
  golden 侧用 A64——任务书规定 golden 用 FLOAT64，与 README 1.3 DPOT01 用 A64
  同口径。「F vs golden F」直审降为诊断（layer1_diagnostics，并报不主判）。
- spotrs: 解矩阵 X 全量逐元素 vs golden32（§2.3′ 维持 X 目标不变）。
- spotri: **双目标**，两个都过 layer1 才过——
  ① A⁻¹ 直审：存储侧半三角 vs golden32；
  ② A·A⁻¹ 对 I：两阵各取存储侧镜像成全对称阵后相乘，全量对单位阵。
  乘积用 A32（实现实际输入升 FP64，与 README 3.3 DPOT03 同口径；A64−A32 之差
  在 2⁻²⁴ 相对量级，远低于任务书容差，不影响判定）。

fallback 残差喂数不变（出处：README 各章公式与参考实现，spec §2.2/§2.3）：
- spotrf: DPOT01，a 传 A64（README 1.3 分子分母都用 A64），收紧式阈值。
- spotrs: DPOT02，a/b 传 A32/B32（README 2.3：残差对实现实际输入升 FP64 算），
  上浮式阈值。
- spotri: DPOT03，a 传 A32（README 3.3：A 用实现实际输入升精度），收紧式阈值。

case_arrays 的键与 npz/index 对应（spec §2.2）：A64/A32（potrs 另有 B64/B32）、
golden64/golden32、uplo（canonical）、ratio_cpu/ratio_cpu_status（index.json）。
golden 存储侧半三角另侧置 0；layer1 半三角目标只取存储侧，天然对齐。
"""

from dataclasses import dataclass
from typing import Callable

import numpy as np

try:  # criteria 作为包被导入时
    from . import thresholds, verdict
except ImportError:  # criteria 目录直接挂 sys.path 时
    import thresholds
    import verdict

OPS = ("spotrf", "spotrs", "spotri")


def _get(case_arrays, key, op):
    if key not in case_arrays:
        raise KeyError(f"case_arrays 缺 {key}（{op} 卡需要，spec §2.2）")
    return case_arrays[key]


def _to_f64(name, arr):
    """layer1 取数用的宽松升型：不查有限性（NaN/Inf 要流进统计计为不符），
    只拒复数与非数值 dtype。"""
    a = np.asarray(arr)
    if np.issubdtype(a.dtype, np.complexfloating):
        raise TypeError(f"{name}: 复数不在本片范围（spec §1 不做复数）")
    if not (np.issubdtype(a.dtype, np.floating) or np.issubdtype(a.dtype, np.integer)):
        raise TypeError(f"{name}: 非数值 dtype {a.dtype}")
    return a.astype(np.float64)


def _square_like(name, ref_name, ref, out):
    """校验 ref 为非空方阵且 out 与之同形，返回 n。"""
    if ref.ndim != 2 or ref.shape[0] != ref.shape[1] or ref.shape[0] == 0:
        raise ValueError(f"{ref_name}: 期望非空方阵，得到 shape={ref.shape}")
    if out.shape != ref.shape:
        raise ValueError(f"shape 不匹配: {name}{out.shape} vs {ref_name}{ref.shape}")
    return ref.shape[0]


def _tri_pair(case_arrays, dut_out, op):
    """存储侧半三角逐元素对（vs golden32）：返回 (actual64, golden64) 一维向量。

    只取 uplo 侧的 n(n+1)/2 个元素：golden 另侧置 0（spec §2.2），被测另侧
    是输入残留——都不进统计，避免用无效元素稀释 matched_ratio。
    spotri 的直审目标与 spotrf 的诊断项共用这一份实现。
    """
    golden = _to_f64("golden32", _get(case_arrays, "golden32", op))
    out = _to_f64("out32", dut_out["out32"])
    n = _square_like("out32", "golden32", golden, out)
    uplo = verdict.normalize_uplo(_get(case_arrays, "uplo", op))
    idx = np.tril_indices(n) if uplo == "L" else np.triu_indices(n)
    return out[idx], golden[idx]


def _potrf_targets(case_arrays, dut_out):
    """spotrf 主判目标（spec §2.3′）：还原 recon 后取指定三角对原 A（A64）。

    因子只取存储侧参与还原（另一侧是输入残留）；比对集是指定三角的
    n(n+1)/2 个元素。被测输出中的 NaN/Inf 经乘法传染进 recon，按 layer1
    统计口径计为不符。
    """
    a64 = _to_f64("A64", _get(case_arrays, "A64", "spotrf"))
    out = _to_f64("out32", dut_out["out32"])
    n = _square_like("out32", "A64", a64, out)
    uplo = verdict.normalize_uplo(_get(case_arrays, "uplo", "spotrf"))
    if uplo == "L":
        fh = np.tril(out)
        recon = fh @ fh.T
        idx = np.tril_indices(n)
    else:
        fh = np.triu(out)
        recon = fh.T @ fh
        idx = np.triu_indices(n)
    return [("recon_vs_A", recon[idx], a64[idx])]


def _potrf_diagnostics(case_arrays, dut_out):
    """spotrf 诊断项（spec §2.3′）：F vs golden F 直审，并报不主判。"""
    actual, golden = _tri_pair(case_arrays, dut_out, "spotrf")
    return [("factor_vs_golden", actual, golden)]


def _potrs_targets(case_arrays, dut_out):
    """spotrs 主判目标：解矩阵 X 全量逐元素 vs golden32。"""
    golden = _to_f64("golden32", _get(case_arrays, "golden32", "spotrs"))
    out = _to_f64("out32", dut_out["out32"])
    if out.shape != golden.shape:
        raise ValueError(f"shape 不匹配: out32{out.shape} vs golden32{golden.shape}")
    return [("x_vs_golden", out.ravel(), golden.ravel())]


def _potri_targets(case_arrays, dut_out):
    """spotri 主判双目标（spec §2.3′）：A⁻¹ 直审 及 A·A⁻¹ 对 I。"""
    direct = _tri_pair(case_arrays, dut_out, "spotri")
    a32 = _to_f64("A32", _get(case_arrays, "A32", "spotri"))
    out = _to_f64("out32", dut_out["out32"])
    n = _square_like("out32", "A32", a32, out)
    uplo = verdict.normalize_uplo(_get(case_arrays, "uplo", "spotri"))
    prod = verdict.mirror_storage_side(a32, uplo) @ verdict.mirror_storage_side(out, uplo)
    eye = np.eye(n)
    return [("ainv_vs_golden",) + direct,
            ("a_ainv_vs_identity", prod.ravel(), eye.ravel())]


def _no_diagnostics(case_arrays, dut_out):
    return []


def _potrf_fallback_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A64", "spotrf"),
        "factor": dut_out["out32"],
        "uplo": verdict.normalize_uplo(_get(case_arrays, "uplo", "spotrf")),
    }


def _potrs_fallback_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32", "spotrs"),
        "b": _get(case_arrays, "B32", "spotrs"),
        "x": dut_out["out32"],
    }


def _potri_fallback_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32", "spotri"),
        "ainv": dut_out["out32"],
        "uplo": verdict.normalize_uplo(_get(case_arrays, "uplo", "spotri")),
    }


@dataclass(frozen=True)
class CriteriaCard:
    """一张判据卡：judge 消费的全部算子特定信息。"""
    op: str                        # canonical 算子名（s 前缀，spec §0）
    residual_kind: str             # residual_ratio 的 kind
    fallback_formula: str          # verdict.fallback.formula 的展示串
    fallback_threshold: Callable   # ratio_cpu -> 阈值（thresholds 模块的公式）
    layer1_targets: Callable       # (case_arrays, dut_out) -> [(name, actual64, golden64)]
    layer1_diagnostics: Callable   # 同形返回，只并报不主判（spec §2.3′）
    fallback_kwargs: Callable      # (case_arrays, dut_out) -> residual_ratio 关键字参数


CARDS = {
    "spotrf": CriteriaCard(
        op="spotrf",
        residual_kind="DPOT01",
        fallback_formula=thresholds.TIGHTEN_FORMULA,
        fallback_threshold=thresholds.tighten_threshold,
        layer1_targets=_potrf_targets,
        layer1_diagnostics=_potrf_diagnostics,
        fallback_kwargs=_potrf_fallback_kwargs,
    ),
    "spotrs": CriteriaCard(
        op="spotrs",
        residual_kind="DPOT02",
        fallback_formula=thresholds.FLOATUP_FORMULA,
        fallback_threshold=thresholds.floatup_threshold,
        layer1_targets=_potrs_targets,
        layer1_diagnostics=_no_diagnostics,
        fallback_kwargs=_potrs_fallback_kwargs,
    ),
    "spotri": CriteriaCard(
        op="spotri",
        residual_kind="DPOT03",
        fallback_formula=thresholds.TIGHTEN_FORMULA,
        fallback_threshold=thresholds.tighten_threshold,
        layer1_targets=_potri_targets,
        layer1_diagnostics=_no_diagnostics,
        fallback_kwargs=_potri_fallback_kwargs,
    ),
}


def get_card(op):
    """按 canonical 算子名取判据卡；未知算子抛 ValueError。"""
    card = CARDS.get(op)
    if card is None:
        raise ValueError(f"未知算子 {op!r}，本片只支持 {OPS}（spec §1）")
    return card
