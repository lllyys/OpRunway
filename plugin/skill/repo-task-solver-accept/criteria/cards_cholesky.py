# -*- coding: utf-8 -*-
"""Cholesky 三接口（spotrf/spotrs/spotri）的判据卡（A 卡·criteria 内核，spec §2.3/§2.3′）。

「卡」把算子特定信息从裁决流程里拆出来：残差喂哪些数组、用哪条阈值公式。judge 只与
卡的成员交互（residual_kind、formula、threshold_fn、residual_kwargs、uses_mean），
流程本身族无关，但本片**不承诺卡的构造方式族无关**（spec §1，跨族泛化推迟证明）。

s2-A1 一段式（2026-10-08 用户裁定）：精度判定收成单步——直接算 LAPACK 残差对阈值判。
原 layer1 比对目标与诊断成员（layer1_targets/layer1_diagnostics 及各目标函数、
拆实/虚辅助件）整体删除；golden32/golden64 不再被 judge 消费（降为自测参考件，
包内文件与 schema 不动）。复数残差由 DPOT 复数版的复模与共轭转置天然处理
（verdict 按 dtype 分流），不再拆实/虚。

残差喂数（s2-A1 换基：全族实际输入 A32/B32 升 f64，README 1.3 的 A64 口径废止）：
- spotrf: DPOT01，a 传 A32（换基，推翻旧裁定 24i；residual_ratio 内升 f64），阈值
  max(5·ratio_cpu, 3·ratio_cpu_mean)（HT-3）。
- spotrs: DPOT02，a/b 传 A32/B32（README 2.3：残差对实现实际输入升 FP64 算），
  阈值同式 max(5·ratio_cpu, 3·ratio_cpu_mean)（HT-3）。
- spotri: DPOT03，a 传 A32（README 3.3：A 用实现实际输入升精度），阈值
  max(5·ratio_cpu, 0.1)（HT-5：0924 任务书 §3.2.2.3 / issue A3）。

ratio_cpu_mean 的消费面（HT-3）：卡以 uses_mean 声明第二支是否参与；verdict 从
case_arrays 读 "ratio_cpu_mean"（accept_run 从 index 顶层按算子注入，HT-4 预计算
固化；stream_check 包内 index 存在时同样注入）。缺 mean 的兼容口径（fail-closed
方向）：残差 ≤ 单支 5·ratio_cpu → PASS；超出 → 证据不足不判 FAIL，待含 mean 的
v3 包复判（verdict._run_residual）。potrf 族 A64 基冻结 ratio_cpu/mean 随换基过期，
需按 A32 基重算（声明，重算另排；case-gen 侧 index 以 ratio_basis:"A32-f64" 标记）。

case_arrays 的键与 npz/index 对应（spec §2.2）：A32（potrs 另有 B32）、uplo
（canonical）、ratio_cpu/ratio_cpu_status（index.json）、ratio_cpu_mean（index 顶层
按算子，potrf/potrs 卡消费，HT-3/HT-4）。A64/B64/golden64/golden32 仍随包交付，
judge 不消费。

S2c 复数三卡（cpotrf/cpotrs/cpotri）：键名与实数完全同构（A32/B32/out32=complex64），
残差仍走 DPOT01/02/03（复模、先升 complex128，verdict 按 dtype 分流；recon/镜像
取共轭 Hermitian），阈值公式与数值同实数书。实数阵进复数链路（或反之）按实/复混搭
抛错（fail-closed），thresholds 零改动。

S3 批量四卡（spotrfBatched/spotrsBatched/cpotrfBatched/cpotrsBatched，S3 spec §4）：
批量卡 = 对应单矩阵基卡（spotrf/spotrs/cpotrf/cpotrs）的判据成员 + 批维身份。
judge 对批量卡逐矩阵切片（A64/A32/B64/B32/golden 首维 batch，ratio_cpu 取 (batch,)
数组的第 i 元），再按单矩阵语义消费基卡成员——一段式残差判定逐矩阵完整走（配对该
矩阵自己的 c_i 与算子级 mean），卡本身不新增任何比对实现。info 按接口角色分型
（任务书接口说明第 69 行）由 ``info_kind`` 表达：potrfBatched 族被测 info 是
int32 (batch,) infoArray（"array"）；potrsBatched 族仍为标量、仅报参数错
（"scalar"），正定性由前置分解的 infoArray 反映、准备失败归 prep 不归目标接口。
potrsBatched 官方仅支持 nrhs=1，B/golden 形状 (batch, n, 1)。
"""

from dataclasses import dataclass, replace
from typing import Callable, Optional

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


def _potrf_kwargs(case_arrays, dut_out):
    # s2-A1 换基：a 传 A32（全族实际输入 A32/B32 升 f64，README 1.3 的 A64 口径废止）。
    return {
        "a": _get(case_arrays, "A32", "spotrf"),
        "factor": dut_out["out32"],
        "uplo": verdict.normalize_uplo(_get(case_arrays, "uplo", "spotrf")),
    }


def _potrs_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32", "spotrs"),
        "b": _get(case_arrays, "B32", "spotrs"),
        "x": dut_out["out32"],
    }


def _potri_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32", "spotri"),
        "ainv": dut_out["out32"],
        "uplo": verdict.normalize_uplo(_get(case_arrays, "uplo", "spotri")),
    }


def _cpotrf_kwargs(case_arrays, dut_out):
    # s2-A1 换基：a 传 A32（同实数书，复数语义由 verdict 的 dtype 分流承担）。
    return {
        "a": _get(case_arrays, "A32", "cpotrf"),
        "factor": dut_out["out32"],
        "uplo": verdict.normalize_uplo(_get(case_arrays, "uplo", "cpotrf")),
    }


def _cpotrs_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32", "cpotrs"),
        "b": _get(case_arrays, "B32", "cpotrs"),
        "x": dut_out["out32"],
    }


def _cpotri_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32", "cpotri"),
        "ainv": dut_out["out32"],
        "uplo": verdict.normalize_uplo(_get(case_arrays, "uplo", "cpotri")),
    }


@dataclass(frozen=True)
class CriteriaCard:
    """一张判据卡：judge 消费的全部算子特定信息（s2-A1 一段式后只余残差判定成员）。"""
    op: str                        # canonical 算子名（实数 s / 复数 c 前缀，spec §0）
    residual_kind: str             # residual_ratio 的 kind
    formula: str                   # verdict.residual.formula 的展示串
    threshold_fn: Callable         # 阈值公式（thresholds 模块）；uses_mean 卡吃
                                   # (ratio_cpu, ratio_cpu_mean) 两参，否则单参
    residual_kwargs: Callable      # (case_arrays, dut_out) -> residual_ratio 关键字参数
    uses_mean: bool = False        # 阈值第二支是否消费 ratio_cpu_mean（HT-3）
    batched: bool = False          # True = 批量卡，judge 走逐矩阵通路（S3 spec §4）
    info_kind: str = "scalar"      # info 接口角色："scalar"｜"array"=infoArray（S3 spec §4）
    base_op: Optional[str] = None  # 批量卡的单矩阵基卡名；非批量卡为 None


CARDS = {
    "spotrf": CriteriaCard(
        op="spotrf",
        residual_kind="DPOT01",
        formula=thresholds.POTRF_POTRS_FORMULA,
        threshold_fn=thresholds.potrf_potrs_threshold,
        residual_kwargs=_potrf_kwargs,
        uses_mean=True,
    ),
    "spotrs": CriteriaCard(
        op="spotrs",
        residual_kind="DPOT02",
        formula=thresholds.POTRF_POTRS_FORMULA,
        threshold_fn=thresholds.potrf_potrs_threshold,
        residual_kwargs=_potrs_kwargs,
        uses_mean=True,
    ),
    "spotri": CriteriaCard(
        op="spotri",
        residual_kind="DPOT03",
        formula=thresholds.POTRI_FORMULA,
        threshold_fn=thresholds.potri_threshold,
        residual_kwargs=_potri_kwargs,
    ),
    # 复数三卡：residual kind、阈值公式与数值同实数书，复数语义（复模/共轭/升精度）
    # 由 verdict 的 dtype 分流承担。
    "cpotrf": CriteriaCard(
        op="cpotrf",
        residual_kind="DPOT01",
        formula=thresholds.POTRF_POTRS_FORMULA,
        threshold_fn=thresholds.potrf_potrs_threshold,
        residual_kwargs=_cpotrf_kwargs,
        uses_mean=True,
    ),
    "cpotrs": CriteriaCard(
        op="cpotrs",
        residual_kind="DPOT02",
        formula=thresholds.POTRF_POTRS_FORMULA,
        threshold_fn=thresholds.potrf_potrs_threshold,
        residual_kwargs=_cpotrs_kwargs,
        uses_mean=True,
    ),
    "cpotri": CriteriaCard(
        op="cpotri",
        residual_kind="DPOT03",
        formula=thresholds.POTRI_FORMULA,
        threshold_fn=thresholds.potri_threshold,
        residual_kwargs=_cpotri_kwargs,
    ),
}


def _batched_card(base_op, op, info_kind):
    """批量卡工厂（S3 spec §4）：判据成员逐字段来自基卡，只换身份三元
    （op/batched/info_kind + base_op 溯源）。判定语义即「逐矩阵按基卡一段式残差判定」，
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
