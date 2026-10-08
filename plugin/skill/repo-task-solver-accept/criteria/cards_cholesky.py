# -*- coding: utf-8 -*-
"""Cholesky 三接口（spotrf/spotrs/spotri）的判据卡（A 卡·criteria 内核，spec §2.3/§2.3′）。

「卡」把三件事从裁决流程里拆出来：layer1 主判比对哪些目标（及并报哪些诊断）、
fallback 喂哪些数组、用哪条阈值公式。judge 只与卡的成员交互（residual_kind、
fallback_formula、fallback_threshold、layer1_targets、layer1_diagnostics、
fallback_kwargs），流程本身族无关，但本片**不承诺卡的构造方式族无关**
（spec §1，跨族泛化推迟证明）。

layer1 主判比对目标按新任务书两步结构（HT-7 对调：issue A5 裁「按任务书」，
supplement 24f；主判基准统一 golden64——supplement 24h 裁「用任务书的」，judge
统计前对 golden 按 FP32 RNE 收窄比对，golden64 与 golden32 判定逐位等价）：
- spotrf: 被测因子 F vs golden 直审——存储侧半三角逐元素比对（新任务书 §3.2.1
  条 6：Hermitian/对称正定下因子数学唯一）。「还原 recon = L·Lᵀ（L 侧）/
  Uᵀ·U（U 侧）后取指定三角对原 A64」降为诊断（layer1_diagnostics，并报不主判；
  其数学形式由 DPOT01 兜底层保留，不丢）。
- spotrs: 解矩阵 X 全量逐元素 vs golden64（目标 X 不变，基准换 golden64）。
- spotri: **单目标**（HT-7 收单）：A⁻¹ 直审，存储侧半三角 vs golden64；
  A·A⁻¹ 对 I 撤出第一步，只留 DPOT03 复核层。

fallback 残差喂数不变（出处：README 各章公式与参考实现，spec §2.2/§2.3），
阈值按 HT-3 换式（0924 任务书 §3.2.2）：
- spotrf: DPOT01，a 传 A64（README 1.3 分子分母都用 A64），阈值
  max(5·ratio_cpu, 3·ratio_cpu_mean)（HT-3，收紧式退役）。
- spotrs: DPOT02，a/b 传 A32/B32（README 2.3：残差对实现实际输入升 FP64 算），
  阈值同式 max(5·ratio_cpu, 3·ratio_cpu_mean)（HT-3，上浮式退役）。
- spotri: DPOT03，a 传 A32（README 3.3：A 用实现实际输入升精度），新式阈值
  max(5·ratio_cpu, 0.1)（HT-5：0924 任务书 §3.2.2.3 / issue A3）。

ratio_cpu_mean 的消费面（HT-3）：卡以 fallback_uses_mean 声明第二支是否参与；
verdict 从 case_arrays 读 "ratio_cpu_mean"（accept_run 从 index 顶层按算子注入，
HT-4 预计算固化；stream_check 现场算链路不产 mean）。缺 mean 的兼容口径
（plan 定义，fail-closed 方向）：残差 ≤ 单支 5·ratio_cpu → PASS；超出 →
证据不足不判 FAIL，待含 mean 的 v3 包复判（verdict._run_fallback）。

case_arrays 的键与 npz/index 对应（spec §2.2）：A64/A32（potrs 另有 B64/B32）、
golden64/golden32、uplo（canonical）、ratio_cpu/ratio_cpu_status（index.json）、
ratio_cpu_mean（index 顶层按算子，potrf/potrs 卡消费，HT-3/HT-4）。
golden 存储侧半三角另侧置 0；layer1 半三角目标只取存储侧，天然对齐；主判基准
读 golden64（HT-7）。

S2c 复数三卡（cpotrf/cpotrs/cpotri，S2 spec §4 契约增量表）：键名与实数完全同构
（A64/B64/golden64=complex128、A32/B32/golden32/out32=complex64），还原用
L·Lᴴ（L 侧）/ Uᴴ·U（U 侧），镜像按存储侧 Hermitian 共轭、对角虚部按 0 处理。
layer1 每个比对目标拆成实/虚两个实数目标（复数任务书 §3.2 第 3 条：实部、虚部
各自作为 FLOAT32 判定，双侧同时达标，不合并成 2N 元素互相稀释）；cpotri 同实数
收单目标（实/虚共 2 目标）。fallback 仍走 DPOT01/02/03（复模、先升 complex128，
verdict 按 dtype 分流），阈值公式与数值同实数书。实数阵进复数卡（或反之）按
实/复混搭抛错（fail-closed），thresholds 零改动。

S3 批量四卡（spotrfBatched/spotrsBatched/cpotrfBatched/cpotrsBatched，S3 spec §4）：
批量卡 = 对应单矩阵基卡（spotrf/spotrs/cpotrf/cpotrs）的判据成员 + 批维身份。
judge 对批量卡逐矩阵切片（A64/A32/B64/B32/golden 首维 batch，ratio_cpu 取
(batch,) 数组的第 i 元），再按单矩阵语义消费基卡成员——三层判定逐矩阵完整走
（新任务书口径：golden64 基准、2⁻¹³ 单套容差、HT-3 新阈值式，HT-16 起 T8 暂定
聚合标记摘除、数值结论升正式），卡本身不新增任何比对实现。info 按接口角色分型
（任务书接口说明第 69 行）由 ``info_kind`` 表达：potrfBatched 族被测 info 是
int32 (batch,) infoArray（"array"）；potrsBatched 族仍为标量、仅报参数错
（"scalar"），正定性由前置分解的 infoArray 反映、准备失败归 prep 不归目标接口。
potrsBatched 官方仅支持 nrhs=1，B/golden 形状 (batch, n, 1)。
"""

from dataclasses import dataclass, replace
from typing import Callable, Optional

import numpy as np

try:  # criteria 作为包被导入时
    from . import thresholds, verdict
except ImportError:  # criteria 目录直接挂 sys.path 时
    import thresholds
    import verdict

OPS = ("spotrf", "spotrs", "spotri", "cpotrf", "cpotrs", "cpotri")
# S3 批量四算子（S3 spec §4/§5：实/复 × 分解/求解四象限；potri 无 batched 接口）。
BATCHED_OPS = ("spotrfBatched", "spotrsBatched", "cpotrfBatched", "cpotrsBatched")


def _get(case_arrays, key, op):
    if key not in case_arrays:
        raise KeyError(f"case_arrays 缺 {key}（{op} 卡需要，spec §2.2）")
    return case_arrays[key]


def _to_f64(name, arr):
    """layer1 取数用的宽松升型：不查有限性（NaN/Inf 要流进统计计为不符），
    只拒复数与非数值 dtype。"""
    a = np.asarray(arr)
    if np.issubdtype(a.dtype, np.complexfloating):
        raise TypeError(f"{name}: 复数须走复数卡（S2 spec §4：实/复按 dtype 分流）")
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
    """存储侧半三角逐元素对（vs golden64）：返回 (actual64, golden64) 一维向量。

    只取 uplo 侧的 n(n+1)/2 个元素：golden 另侧置 0（spec §2.2），被测另侧
    是输入残留——都不进统计，避免用无效元素稀释 matched_ratio。
    spotrf 主判目标（HT-7 对调）与 spotri 单目标共用这一份实现；golden 统计前
    由 verdict 按 FP32 RNE 收窄，golden64 与 golden32 判定等价（supplement 24h）。
    """
    golden = _to_f64("golden64", _get(case_arrays, "golden64", op))
    out = _to_f64("out32", dut_out["out32"])
    n = _square_like("out32", "golden64", golden, out)
    uplo = verdict.normalize_uplo(_get(case_arrays, "uplo", op))
    idx = np.tril_indices(n) if uplo == "L" else np.triu_indices(n)
    return out[idx], golden[idx]


def _potrf_targets(case_arrays, dut_out):
    """spotrf 主判目标（HT-7 对调，新任务书 §3.2.1 条 6）：F vs golden 直审——
    存储侧半三角逐元素比对（正定下因子数学唯一）。"""
    actual, golden = _tri_pair(case_arrays, dut_out, "spotrf")
    return [("factor_vs_golden", actual, golden)]


def _potrf_diagnostics(case_arrays, dut_out):
    """spotrf 诊断项（HT-7 对调）：还原 recon 后取指定三角对原 A64，并报不主判
    （其数学形式由 DPOT01 兜底层保留，不丢）。

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


def _potrs_targets(case_arrays, dut_out):
    """spotrs 主判目标：解矩阵 X 全量逐元素 vs golden64（HT-7 基准换 golden64）。"""
    golden = _to_f64("golden64", _get(case_arrays, "golden64", "spotrs"))
    out = _to_f64("out32", dut_out["out32"])
    if out.shape != golden.shape:
        raise ValueError(f"shape 不匹配: out32{out.shape} vs golden64{golden.shape}")
    return [("x_vs_golden", out.ravel(), golden.ravel())]


def _potri_targets(case_arrays, dut_out):
    """spotri 主判单目标（HT-7 收单）：A⁻¹ 直审，存储侧半三角 vs golden64；
    A·A⁻¹ 对 I 撤出第一步，只留 DPOT03 复核层。"""
    actual, golden = _tri_pair(case_arrays, dut_out, "spotri")
    return [("ainv_vs_golden", actual, golden)]


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


# ---------------------------------------------------------------------------
# S2c 复数三卡（cpotrf/cpotrs/cpotri，S2 spec §4）：实数卡不动，追加不改写
# ---------------------------------------------------------------------------

def _to_c128(name, arr):
    """layer1 取数用的宽松升型（复数卡）：先升 complex128 再拆实/虚或进乘积
    （S2 spec §4 防 astype 直转实数丢虚部的坑）；不查有限性（NaN/Inf 要流进
    统计计为不符），只拒非复数 dtype——实数阵进复数卡即实/复混搭，fail-closed
    抛错（保实数链路产物不被无意混入）。"""
    a = np.asarray(arr)
    if not np.issubdtype(a.dtype, np.complexfloating):
        raise TypeError(f"{name}: 复数卡要求复数 dtype（S2 spec §4），得到 {a.dtype}")
    return a.astype(np.complex128)


def _reim(name, actual, golden):
    """把一个复数比对目标拆成实/虚两个实数目标（复数任务书 §3.2 第 3 条：实部、
    虚部各自作为 FLOAT32 做混合容差判定，双侧同时达标才过——judge 对多目标取
    AND/min/max 聚合，天然不合并成 2N 元素互相稀释）。

    golden 允许是实数阵（如单位阵目标），其 .imag 即全 0 期望。"""
    actual = np.asarray(actual)
    golden = np.asarray(golden)
    return [(f"{name}_re", actual.real, golden.real),
            (f"{name}_im", actual.imag, golden.imag)]


def _c_tri_pair(case_arrays, dut_out, op):
    """_tri_pair 的复数版：存储侧半三角逐元素对（vs golden64，升 complex128）。

    只取 uplo 侧的 n(n+1)/2 个元素，另侧（golden 置 0 侧 / 被测输入残留侧）
    不进统计；cpotrf 主判目标（HT-7 对调）与 cpotri 单目标共用这一份实现。
    """
    golden = _to_c128("golden64", _get(case_arrays, "golden64", op))
    out = _to_c128("out32", dut_out["out32"])
    n = _square_like("out32", "golden64", golden, out)
    uplo = verdict.normalize_uplo(_get(case_arrays, "uplo", op))
    idx = np.tril_indices(n) if uplo == "L" else np.triu_indices(n)
    return out[idx], golden[idx]


def _cpotrf_targets(case_arrays, dut_out):
    """cpotrf 主判目标（HT-7 对调）：F vs golden 直审（存储侧半三角 vs golden64），
    实/虚各自成目标。"""
    actual, golden = _c_tri_pair(case_arrays, dut_out, "cpotrf")
    return _reim("factor_vs_golden", actual, golden)


def _cpotrf_diagnostics(case_arrays, dut_out):
    """cpotrf 诊断项（HT-7 对调）：还原 recon = L·Lᴴ（L 侧）/ Uᴴ·U（U 侧）后
    取指定三角对原 A（A64=complex128），拆实/虚，并报不主判（数学形式由 DPOT01
    兜底层保留）。

    因子只取存储侧参与还原；A 的 Hermitian 另侧由存储侧共轭镜像定义，比对集
    即指定三角的 n(n+1)/2 个元素。
    """
    a64 = _to_c128("A64", _get(case_arrays, "A64", "cpotrf"))
    out = _to_c128("out32", dut_out["out32"])
    n = _square_like("out32", "A64", a64, out)
    uplo = verdict.normalize_uplo(_get(case_arrays, "uplo", "cpotrf"))
    if uplo == "L":
        fh = np.tril(out)
        recon = fh @ fh.conj().T
        idx = np.tril_indices(n)
    else:
        fh = np.triu(out)
        recon = fh.conj().T @ fh
        idx = np.triu_indices(n)
    return _reim("recon_vs_A", recon[idx], a64[idx])


def _cpotrs_targets(case_arrays, dut_out):
    """cpotrs 主判目标：解矩阵 X 全量逐元素 vs golden64，实/虚各自成目标
    （HT-7 基准换 golden64）。"""
    golden = _to_c128("golden64", _get(case_arrays, "golden64", "cpotrs"))
    out = _to_c128("out32", dut_out["out32"])
    if out.shape != golden.shape:
        raise ValueError(f"shape 不匹配: out32{out.shape} vs golden64{golden.shape}")
    return _reim("x_vs_golden", out.ravel(), golden.ravel())


def _cpotri_targets(case_arrays, dut_out):
    """cpotri 主判单目标（HT-7 收单）：A⁻¹ 直审（存储侧半三角 vs golden64），
    拆实/虚共 2 目标；A·A⁻¹ 对 I 撤出第一步，只留 DPOT03 复核层。"""
    direct_actual, direct_golden = _c_tri_pair(case_arrays, dut_out, "cpotri")
    return _reim("ainv_vs_golden", direct_actual, direct_golden)


def _cpotrf_fallback_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A64", "cpotrf"),
        "factor": dut_out["out32"],
        "uplo": verdict.normalize_uplo(_get(case_arrays, "uplo", "cpotrf")),
    }


def _cpotrs_fallback_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32", "cpotrs"),
        "b": _get(case_arrays, "B32", "cpotrs"),
        "x": dut_out["out32"],
    }


def _cpotri_fallback_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32", "cpotri"),
        "ainv": dut_out["out32"],
        "uplo": verdict.normalize_uplo(_get(case_arrays, "uplo", "cpotri")),
    }


@dataclass(frozen=True)
class CriteriaCard:
    """一张判据卡：judge 消费的全部算子特定信息。"""
    op: str                        # canonical 算子名（实数 s / 复数 c 前缀，spec §0 + S2 spec §0）
    residual_kind: str             # residual_ratio 的 kind
    fallback_formula: str          # verdict.fallback.formula 的展示串
    fallback_threshold: Callable   # 阈值公式（thresholds 模块）；uses_mean 卡吃
                                   # (ratio_cpu, ratio_cpu_mean) 两参，否则单参
    layer1_targets: Callable       # (case_arrays, dut_out) -> [(name, actual64, golden64)]
    layer1_diagnostics: Callable   # 同形返回，只并报不主判（spec §2.3′）
    fallback_kwargs: Callable      # (case_arrays, dut_out) -> residual_ratio 关键字参数
    fallback_uses_mean: bool = False   # 阈值第二支是否消费 ratio_cpu_mean（HT-3）
    batched: bool = False          # True = 批量卡，judge 走逐矩阵通路（S3 spec §4）
    info_kind: str = "scalar"      # info 接口角色："scalar"｜"array"=infoArray（S3 spec §4）
    base_op: Optional[str] = None  # 批量卡的单矩阵基卡名；非批量卡为 None


CARDS = {
    "spotrf": CriteriaCard(
        op="spotrf",
        residual_kind="DPOT01",
        fallback_formula=thresholds.POTRF_POTRS_FORMULA,
        fallback_threshold=thresholds.potrf_potrs_threshold,
        layer1_targets=_potrf_targets,
        layer1_diagnostics=_potrf_diagnostics,
        fallback_kwargs=_potrf_fallback_kwargs,
        fallback_uses_mean=True,
    ),
    "spotrs": CriteriaCard(
        op="spotrs",
        residual_kind="DPOT02",
        fallback_formula=thresholds.POTRF_POTRS_FORMULA,
        fallback_threshold=thresholds.potrf_potrs_threshold,
        layer1_targets=_potrs_targets,
        layer1_diagnostics=_no_diagnostics,
        fallback_kwargs=_potrs_fallback_kwargs,
        fallback_uses_mean=True,
    ),
    "spotri": CriteriaCard(
        op="spotri",
        residual_kind="DPOT03",
        fallback_formula=thresholds.POTRI_FORMULA,
        fallback_threshold=thresholds.potri_threshold,
        layer1_targets=_potri_targets,
        layer1_diagnostics=_no_diagnostics,
        fallback_kwargs=_potri_fallback_kwargs,
    ),
    # 复数三卡：residual kind、阈值公式与数值同实数书（S2 spec §4），
    # 复数语义（复模/共轭/升精度）由 verdict 的 dtype 分流承担。
    "cpotrf": CriteriaCard(
        op="cpotrf",
        residual_kind="DPOT01",
        fallback_formula=thresholds.POTRF_POTRS_FORMULA,
        fallback_threshold=thresholds.potrf_potrs_threshold,
        layer1_targets=_cpotrf_targets,
        layer1_diagnostics=_cpotrf_diagnostics,
        fallback_kwargs=_cpotrf_fallback_kwargs,
        fallback_uses_mean=True,
    ),
    "cpotrs": CriteriaCard(
        op="cpotrs",
        residual_kind="DPOT02",
        fallback_formula=thresholds.POTRF_POTRS_FORMULA,
        fallback_threshold=thresholds.potrf_potrs_threshold,
        layer1_targets=_cpotrs_targets,
        layer1_diagnostics=_no_diagnostics,
        fallback_kwargs=_cpotrs_fallback_kwargs,
        fallback_uses_mean=True,
    ),
    "cpotri": CriteriaCard(
        op="cpotri",
        residual_kind="DPOT03",
        fallback_formula=thresholds.POTRI_FORMULA,
        fallback_threshold=thresholds.potri_threshold,
        layer1_targets=_cpotri_targets,
        layer1_diagnostics=_no_diagnostics,
        fallback_kwargs=_cpotri_fallback_kwargs,
    ),
}


def _batched_card(base_op, op, info_kind):
    """批量卡工厂（S3 spec §4）：判据成员逐字段来自基卡，只换身份三元
    （op/batched/info_kind + base_op 溯源）。判定语义即「逐矩阵按基卡走完整三层」，
    由 verdict 的批维通路承担，此处零新判据实现。"""
    return replace(CARDS[base_op], op=op, batched=True, info_kind=info_kind, base_op=base_op)


# S3 批量四卡（实/复 × 分解/求解四象限，S3 spec §5 装包全覆盖的判据侧对应物）：
# potrfBatched 族 info 是 (batch,) infoArray；potrsBatched 族 info 标量仅报参数错
# （任务书接口说明第 69 行，S3 spec §4 info 分型行）。
CARDS["spotrfBatched"] = _batched_card("spotrf", "spotrfBatched", "array")
CARDS["spotrsBatched"] = _batched_card("spotrs", "spotrsBatched", "scalar")
CARDS["cpotrfBatched"] = _batched_card("cpotrf", "cpotrfBatched", "array")
CARDS["cpotrsBatched"] = _batched_card("cpotrs", "cpotrsBatched", "scalar")


def get_card(op):
    """按 canonical 算子名取判据卡；未知算子抛 ValueError。"""
    card = CARDS.get(op)
    if card is None:
        raise ValueError(
            f"未知算子 {op!r}，只支持 {OPS + BATCHED_OPS}"
            "（S1 spec §1 + S2 spec §4 + S3 spec §4）")
    return card
