# -*- coding: utf-8 -*-
"""verify 双件确定性渲染入口（D 卡，spec §2.5；设计 v2 §3.3「同一个确定性渲染入口」）。

用法：``render_verify.py --op <算子名> --out <dir>``，算子名为单矩阵六算子
（spotrf/spotrs/spotri/cpotrf/cpotrs/cpotri）或批量四算子（spotrfBatched/
spotrsBatched/cpotrfBatched/cpotrsBatched，S3 spec §4/§5）。
向 <dir> 渲染两个独立可运行的自测辅助件：``verify_accuracy.py``（三层判定的渲染副本：
layer1 混合容差双门 → LAPACK 残差兜底）与 ``verify_perf.py``（perf_baseline 逐 case
比值，方向 = 被测/基线）。

S2c 复数增量（dev-doc/solver/solver-s2-spec.md §4 契约增量表）：c 前缀三算子的
精度副本与 criteria 复数通路同语义——layer1 比对目标拆实/虚两个实数目标各过双门
（不合并成 2N 元素互相稀释）、残差先升 complex128 再取复模、recon/镜像取共轭
（Hermitian）；拆实/虚口径经 issue C3 评审确认与任务书本意一致，T7 已摘、复数
裁决升正式；公式形状、ε 与阈值数值同实数书。

三条渲染契约（spec 波 2 D 行）：

- **确定性**：输出只由模板与 criteria 常量决定，无时间戳、无绝对路径、无环境依赖，
  同版本复渲逐字节一致；包内副本漂移由 manifest 指纹监督，仅告警（spec §2.2 指纹分级）。
- **独立可运行**：副本不 import 本 skill 任何模块；判据参数全部嵌入文件内，含
  任务书契约（标准表单套 rtol=atol=2^-13（HT-14）、matched_ratio 0.99、max_abs
  动态上限 max(1e-2, 32·ULP(g_low))（HT-1）、比对目标、兜底公式与 mean 第二支
  （HT-3））。嵌入常量与卡参数在渲染时逐项与 thresholds /
  cards_cholesky 断言相等，模板与判据源漂移直接渲染失败（fail-closed）。
- **副本身份**：自测辅助件，输出不构成验收证据；正式裁决在 accept（设计 v2 §3.3
  裁决主权三规则）。副本的 formal 层恒为 PENDING_RULING，与 criteria 同语义。

模板正文是 criteria/verdict.py 与 criteria/cards_cholesky.py 相应实现的逐句转写
（模块引用改为文件内直引），行为一致性由 D 卡容器验证段对 A 卡正反例逐条核对。

S3 批量增量（S3 spec §4/§5 + 并行裁定）：批量四算子的精度副本由单矩阵模板的
判定机体（常量、公共件、基算子块、单矩阵裁决）加批量扩展段拼装——逐矩阵完整三层同
criteria 批维通路语义、info 按接口角色分型、flags 恒空（HT-16：T8 暂定聚合标记
摘除，case 数值结论 = 全部矩阵通过、升正式）；**A0 抽样 materialize**（HT-2，先造
数后测）：包内不携带任何数组，副本判定时调包内 gen_data.py 与 canonical 切片现场
重生成 k=min(5,batch) 个代表内容数组、golden 与逐内容参考 ratio（内容/摆放/填充
三条流全部从 case seed 派生，同环境逐位一致，不依赖开发者的 data 目录）；被测全批
输出先过余槽 bit-wise 一致性、后按 rep_slot 逐内容判定（两层先后）。
逐矩阵判定支持 --jobs 多进程分块（判定只读共享数组无流耦合，按矩阵区间切分，合并
结果与串行逐位相同，内建区间覆盖断言）。单矩阵六算子的模板与渲染输出零漂移
（批量走独立版本号 BATCHED_RENDERER_VER，拼装只复用、不改写既有模板字面）。

S4 六算子 v2（Mr.0 2026-09-24 裁定：六算子补发纯脚本 v2、与 batched 统一形态；
v1 从未流通无需兼容）：单矩阵六算子的精度副本改为**纯脚本（全量 materialize）形态**
——包不携带任何数组，副本判定时调包内 gen_data.py 与 canonical 切片现场重生成输入、
golden 与本 case 参考 ratio（低精度准备链 + 本件残差实现，口径与验收侧回填一致）。
拼装机制与批量段同一套（换头部 docstring、切走原 npz CLI、接现场重生成扩展段与
纯脚本 CLI），判定机体（常量、公共件、算子块、单矩阵裁决）字面复用不改写；批量
四算子的拼装来源与输出逐字节零漂移。版本戳按算子路由（renderer_ver_for）：单矩阵
六算子走 RENDERER_VER（本版起为纯脚本形态版本），批量走 BATCHED_RENDERER_VER。

A6 口径同步（2026-09-26，原 HT-19 渲染器核心提前到阶段 2 执行——已归并的
build_package 直接消费本入口，且 fail-closed 断言不允许接线对 A6、机体留旧式的
中间态）：判定机体与六算子块按阶段 1 criteria（s1-A6）逐句重基——golden64 基准
（统计前 FP32 RNE 收窄）、2⁻¹³ 单套容差（T3 双轨拆除）、max_abs 动态锚点单口径
（T4 双解释拆除）、potrf 直审主判/还原降诊断与 potri 收单（HT-7）、新阈值式
max(5·ratio_cpu, 3·ratio_cpu_mean) 与 max(5·ratio_cpu, 0.1)（HT-3/HT-5，缺 mean
走单支兼容口径）、flags 恒空（T3/T4/T7/T8 全摘，HT-16）、fallback 报告 eps 披露。
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path

try:  # criteria 作为包被导入时
    from . import cards_cholesky, thresholds
except ImportError:  # criteria 目录直接挂 sys.path 或作为脚本运行时
    import cards_cholesky
    import thresholds

# 渲染格式版本，供 manifest.renderer_ver（spec §2.2）消费。
# s2-D2：复数三算子（cpotrf/cpotrs/cpotri）渲染支持（S2 spec §4）。
# s4-D5：单矩阵六算子改纯脚本（全量 materialize）形态（S4 v2，Mr.0 2026-09-24 裁定）；
# s2-D3/s2-D4 的去内部指称替换表与三道渲染收尾自检原样沿用。
# s4-D6：判定机体与六算子块按 criteria s1-A6 全口径重基（2026-09-26，原 HT-19
# 渲染器核心提前——golden64、2⁻¹³ 单套、动态锚点、HT-3/HT-5 新阈值式、摘 T3/T4/T7）。
# s4-D7：HT-8 info 契约支路（2026-09-26）——判定机体加 case_purpose 分流
# （_judge_info_inner，只比 info==k_expected）；纯脚本 CLI 现场派生 info 用例并驱动。
# s4-D8：HT-4 ratio_cpu_mean 消费（2026-09-26）——纯脚本 CLI 从包内 index 顶层
# 按算子读固化 mean 注入 case_arrays（判定只读、零重算；缺键走单支兼容口径）。
# s4-D9：HT-19 验收抓漏修复（2026-09-26）——spotri/cpotri 专属块漏定义
# FALLBACK_USES_MEAN（机体无条件引用，potri 副本一进 fallback 即 NameError，
# FAIL 证据被内部异常污染）；补 FALLBACK_USES_MEAN = False 对齐权威卡语义。
RENDERER_VER = "s4-D9"
# 批量四算子的渲染格式版本（S3 spec §4/§5）：与单矩阵版本号分开，保证既有六算子
# 渲染输出逐字节零漂移（S3 spec §7 零漂移门）。
# s3-D4：批量扩展段同步 A6（flags 恒空、layer1_pass_count、HT-16，2026-09-26）。
# s3-D5：批量通路 A0 抽样化（HT-2，2026-09-26）：_materialize_case 重生成
# k=min(5,batch) 代表内容 + sample_map（case seed 派生）+ 逐内容 ratio；新增
# _check_consistency/_judge_a0 两层判定（先余槽 bit-wise 一致性后 rep_slot 逐内容，
# 复用 judge_batched 于 batch=k 子批）。
# s3-D6：HT-9 批量 info 契约支路（2026-09-26）——_judge_a0_info（case_purpose
# 分流：infoArray 全批逐槽核对/标量直接比对，只比 info）；CLI 现场派生批量 info
# 混合 case 并驱动。
# s3-D7：HT-4 case 级 ratio_cpu_mean 消费（2026-09-26）——CLI 从包内 index 条目
# 按 case_id 读固化 mean 注入 case_arrays；_judge_a0 把 mean 透传子批（阈值第二支）。
BATCHED_RENDERER_VER = "s3-D7"


def renderer_ver_for(op):
    """该算子渲染产物的格式版本（manifest.renderer_ver 消费方按算子取值）。"""
    return BATCHED_RENDERER_VER if op in _BATCHED_SPECS else RENDERER_VER
# s2-D3：渲染产物去内部指称（2026-09-24 冷读演练发现 6：包内查无 spec/accept 可解处）。
# 只作用于渲染输出，skill 内部注释不受影响；表驱动便于核对与增删。
_PKG_LOCAL_SUBS = (
    # s2-D4：补两族漏项（复核实测 fail-closed 闸出：spec §2.4 与 S2 spec §4）。
    # 长串在前，避免被短串先替换截断。
    ("S2 spec §4", "复数任务书 §3.2 契约"),
    ("spec §2.4", "包内 ratio_cpu 约定"),
    ("spec §2.3/§2.3′", "任务书 §3.2"),
    ("spec §2.3′", "任务书 §3.2"),
    ("spec §2.2/§2.5", "包契约（README 与 manifest）"),
    ("spec §2.2", "包契约"),
    ("spec §2.5", "包契约"),
    ("spec §0", "包约定"),
    ("在 accept 执行", "由验收流程执行"),
)

# ---- 嵌入常量块（渲染进副本；_check_constants 逐项与 thresholds 核对）----
_CONSTANTS_BLOCK = '''EPS32 = 2.0 ** -24                     # 残差 ε（spec §0：固定 2^-24，README 口径）
LAYER1_RTOL_FP32 = 2.0 ** -13          # 标准表单套 rtol（HT-14：双套收单套）
LAYER1_ATOL_FP32 = 2.0 ** -13          # 标准表单套 atol（HT-14）
REQUIRED_MATCHED_RATIO = 0.99          # layer1 通过率门
MAX_ABS_FIXED = 1e-2                   # max_abs 动态上限的 FP32 兜底值（标准表 §2.1.2）
ULP_MULT = 32                          # 32·ULP(g_low) 项的倍数（动态上限另一支）'''


def _check_constants():
    """嵌入常量与 criteria/thresholds 逐项断言相等；漂移即渲染失败（fail-closed）。"""
    ns = {}
    exec(_CONSTANTS_BLOCK, {"__builtins__": {}}, ns)  # 模板自检，纯字面表达式
    for name, value in ns.items():
        ref = getattr(thresholds, name)
        if value != ref:
            raise AssertionError(
                f"模板常量 {name} 与 criteria/thresholds 漂移: {value!r} != {ref!r}")


# ---------------------------------------------------------------------------
# 逐算子专属代码块（cards_cholesky 各卡 + verdict 残差实现的转写）
# ---------------------------------------------------------------------------

_SPOTRF_BLOCK = r'''# ---------------------------------------------------------------------------
# spotrf 专属件（criteria/cards_cholesky spotrf 卡 + verdict._dpot01 的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu, ratio_cpu_mean):
    """新阈值式（DPOT01，HT-3，0924 任务书 §3.2.2.1）：max(5*ratio_cpu, 3*ratio_cpu_mean)。
    第二支 mean 缺席时由裁决机体走单支兼容口径（残差 ≤ 5*ratio_cpu），不进本函数。"""
    r = _check_ratio_cpu(ratio_cpu)
    m = _check_ratio_cpu_mean(ratio_cpu_mean)
    return max(5.0 * r, 3.0 * m)


# HT-3：potrf/potrs 阈值第二支消费 ratio_cpu_mean（裁决机体据此分流缺 mean 兼容口径）。
FALLBACK_USES_MEAN = True


def residual_ratio(a, factor, uplo):
    """DPOT01：ratio = ‖recon−A‖₁ / (n·‖A‖₁·ε)，L 侧 recon=L·Lᵀ、U 侧 recon=Uᵀ·U。

    a 传 A64（README 1.3 参考链路口径）；因子与差值只用存储侧，范数按 DLANSY
    半三角镜像口径。NaN/Inf、shape 错、零分母 → 抛异常，不返回数值。
    """
    uplo = normalize_uplo(uplo)
    a = _as_f64("a", a)
    f = _as_f64("factor", factor)
    n = _square("a", a)
    if f.shape != a.shape:
        raise ValueError(f"shape 不匹配: a{a.shape} vs factor{f.shape}")
    fh = _half(f, uplo)
    recon = fh @ fh.T if uplo == "L" else fh.T @ fh
    num = _sym_norm1_from_half(_half(recon - a, uplo))
    anorm = _sym_norm1_from_half(_half(a, uplo))
    if anorm <= 0.0:
        raise ValueError("零分母：‖A‖₁ = 0")
    return float(num / (n * anorm * EPS32))


def layer1_targets(case_arrays, dut_out):
    """spotrf 主判目标（HT-7 对调，新任务书 §3.2.1 条 6）：F vs golden 直审——
    存储侧半三角逐元素比对（Hermitian/对称正定下因子数学唯一）。"""
    actual, golden = _tri_pair(case_arrays, dut_out)
    return [("factor_vs_golden", actual, golden)]


def layer1_diagnostics(case_arrays, dut_out):
    """spotrf 诊断项（HT-7 对调）：还原 recon = L·Lᵀ（L 侧）/ Uᵀ·U（U 侧）后取
    指定三角对原 A（A64），并报不主判（数学形式由 DPOT01 兜底层保留，不丢）。"""
    a64 = _to_f64("A64", _get(case_arrays, "A64"))
    out = _to_f64("out32", dut_out["out32"])
    n = _square_like("out32", "A64", a64, out)
    uplo = normalize_uplo(_get(case_arrays, "uplo"))
    if uplo == "L":
        fh = np.tril(out)
        recon = fh @ fh.T
        idx = np.tril_indices(n)
    else:
        fh = np.triu(out)
        recon = fh.T @ fh
        idx = np.triu_indices(n)
    return [("recon_vs_A", recon[idx], a64[idx])]


def fallback_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A64"),
        "factor": dut_out["out32"],
        "uplo": normalize_uplo(_get(case_arrays, "uplo")),
    }'''

_SPOTRS_BLOCK = r'''# ---------------------------------------------------------------------------
# spotrs 专属件（criteria/cards_cholesky spotrs 卡 + verdict._dpot02 的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu, ratio_cpu_mean):
    """新阈值式（DPOT02，HT-3，0924 任务书 §3.2.2.2）：max(5*ratio_cpu, 3*ratio_cpu_mean)。
    第二支 mean 缺席时由裁决机体走单支兼容口径（残差 ≤ 5*ratio_cpu），不进本函数。"""
    r = _check_ratio_cpu(ratio_cpu)
    m = _check_ratio_cpu_mean(ratio_cpu_mean)
    return max(5.0 * r, 3.0 * m)


# HT-3：potrf/potrs 阈值第二支消费 ratio_cpu_mean（裁决机体据此分流缺 mean 兼容口径）。
FALLBACK_USES_MEAN = True


def residual_ratio(a, b, x):
    """DPOT02：ratio = max_j ‖B_j−A·X_j‖₁ / (‖A‖₁·‖X_j‖₁·ε)，逐 RHS 列取 max。

    分母无 n（README 2.3，与 DGET02 同构）；a/b 传 A32/B32，内部统一升 FP64。
    NaN/Inf、shape 错、零分母 → 抛异常，不返回数值。
    """
    a = _as_f64("a", a)
    b = _as_f64("b", b)
    x = _as_f64("x", x)
    n = _square("a", a)
    if b.shape != x.shape or b.shape[0] != n or b.shape[1] < 1:
        raise ValueError(f"shape 不匹配: a{a.shape}, b{b.shape}, x{x.shape}")
    anorm = float(np.max(np.sum(np.abs(a), axis=0)))
    if anorm <= 0.0:
        raise ValueError("零分母：‖A‖₁ = 0")
    resid = b - a @ x
    ratio = 0.0
    for j in range(b.shape[1]):
        xnorm = float(np.sum(np.abs(x[:, j])))
        if xnorm <= 0.0:
            raise ValueError(f"零分母：x 第 {j} 列 1-范数为 0")
        bnorm = float(np.sum(np.abs(resid[:, j])))
        ratio = max(ratio, bnorm / (anorm * xnorm * EPS32))
    return float(ratio)


def layer1_targets(case_arrays, dut_out):
    """spotrs 主判目标：解矩阵 X 全量逐元素 vs golden64（HT-7 基准换 golden64；
    统计前由裁决机体按 FP32 RNE 收窄，golden64 与 golden32 判定逐位等价）。"""
    golden = _to_f64("golden64", _get(case_arrays, "golden64"))
    out = _to_f64("out32", dut_out["out32"])
    if out.shape != golden.shape:
        raise ValueError(f"shape 不匹配: out32{out.shape} vs golden64{golden.shape}")
    return [("x_vs_golden", out.ravel(), golden.ravel())]


def layer1_diagnostics(case_arrays, dut_out):
    """spotrs 无诊断项。"""
    return []


def fallback_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32"),
        "b": _get(case_arrays, "B32"),
        "x": dut_out["out32"],
    }'''

_SPOTRI_BLOCK = r'''# ---------------------------------------------------------------------------
# spotri 专属件（criteria/cards_cholesky spotri 卡 + verdict._dpot03 的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu):
    """新阈值式（DPOT03，HT-5，0924 任务书 §3.2.2.3）：max(5*ratio_cpu, 0.1)。"""
    r = _check_ratio_cpu(ratio_cpu)
    return max(5.0 * r, 0.1)


# HT-3：potri 阈值无 mean 第二支（结构性单支），裁决机体据此走单支式 fallback_threshold。
FALLBACK_USES_MEAN = False


def residual_ratio(a, ainv, uplo):
    """DPOT03：ratio = ‖I−A·C‖₁ / (n·‖A‖₁·‖C‖₁·ε)，双半三角口径。

    a 传 A32（README 3.3：A 用实现实际输入升精度）；a 与 ainv 各取存储侧镜像成
    全对称阵再相乘。NaN/Inf、shape 错、零分母 → 抛异常，不返回数值。
    """
    uplo = normalize_uplo(uplo)
    a = _as_f64("a", a)
    c = _as_f64("ainv", ainv)
    n = _square("a", a)
    if c.shape != a.shape:
        raise ValueError(f"shape 不匹配: a{a.shape} vs ainv{c.shape}")
    a_half = _half(a, uplo)
    c_half = _half(c, uplo)
    anorm = _sym_norm1_from_half(a_half)
    cnorm = _sym_norm1_from_half(c_half)
    if anorm <= 0.0 or cnorm <= 0.0:
        raise ValueError("零分母：‖A‖₁ 或 ‖C‖₁ 为 0")
    w = np.eye(n) - _mirror_half(a_half) @ _mirror_half(c_half)
    num = float(np.max(np.sum(np.abs(w), axis=0)))
    return float(num / (n * anorm * cnorm * EPS32))


def layer1_targets(case_arrays, dut_out):
    """spotri 主判单目标（HT-7 收单）：A⁻¹ 直审，存储侧半三角 vs golden64；
    A·A⁻¹ 对 I 撤出第一步，只留 DPOT03 复核层。"""
    actual, golden = _tri_pair(case_arrays, dut_out)
    return [("ainv_vs_golden", actual, golden)]


def layer1_diagnostics(case_arrays, dut_out):
    """spotri 无诊断项。"""
    return []


def fallback_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32"),
        "ainv": dut_out["out32"],
        "uplo": normalize_uplo(_get(case_arrays, "uplo")),
    }'''

# S2c 复数三块（S2 spec §4）：criteria/verdict 复数通路 + cards_cholesky 复数卡的
# 转写。实数三块不动（追加不改写）；复数公共件作为前缀拼进每个复数算子块。
_COMPLEX_COMMON = r'''# ---------------------------------------------------------------------------
# 复数公共件（criteria/verdict 复数通路 + criteria/cards_cholesky 复数卡的渲染副本）
# ---------------------------------------------------------------------------


def _as_c128(name, arr):
    """严校验升型（复数残差接口用）：先升 complex128 再进任何取模/范数（S2 spec §4，
    防 astype 直转实数丢虚部的坑）；非 2 维、NaN/Inf 一律抛异常。本副本只含复数
    通路，实数阵在此 fail-closed 拒绝（criteria 侧由 dtype 分流承担同一语义）。"""
    a = np.asarray(arr)
    if not np.issubdtype(a.dtype, np.complexfloating):
        raise TypeError(f"{name}: 复数卡要求复数 dtype（S2 spec §4），得到 {a.dtype}")
    a = a.astype(np.complex128)
    if a.ndim != 2:
        raise ValueError(f"{name}: 期望 2 维数组，得到 shape={a.shape}")
    if not np.isfinite(a).all():
        raise ValueError(f"{name}: 含 NaN/Inf")
    return a


def _to_c128(name, arr):
    """宽松升型（复数 layer1 取数用）：先升 complex128 再拆实/虚（S2 spec §4）；
    不查有限性（NaN/Inf 流进统计计为不符），实数阵 fail-closed 拒绝。"""
    a = np.asarray(arr)
    if not np.issubdtype(a.dtype, np.complexfloating):
        raise TypeError(f"{name}: 复数卡要求复数 dtype（S2 spec §4），得到 {a.dtype}")
    return a.astype(np.complex128)


def _check_diag_real(name, a):
    """Hermitian 存储不变量校验（S2 spec §4：对角虚部按 0 处理并校验），
    只抓无意错喂；被测输出不做此校验，其偏差由 layer1 拆实/虚如实计不符。"""
    if np.any(np.diagonal(a).imag != 0.0):
        raise ValueError(f"{name}: Hermitian 对角虚部非 0（S2 spec §4）")


def _reim(name, actual, golden):
    """把一个复数比对目标拆成实/虚两个实数目标（复数任务书 §3.2 第 3 条：实部、
    虚部各自作为 FLOAT32 判定，双侧同时达标才过——多目标聚合取 AND/min/max，
    天然不合并成 2N 元素互相稀释）。golden 允许是实数阵（如单位阵目标），
    其 .imag 即全 0 期望。"""
    actual = np.asarray(actual)
    golden = np.asarray(golden)
    return [(f"{name}_re", actual.real, golden.real),
            (f"{name}_im", actual.imag, golden.imag)]


def _c_tri_pair(case_arrays, dut_out):
    """存储侧半三角逐元素对的复数版（vs golden64，升 complex128）：只取 uplo 侧
    n(n+1)/2 个元素，另侧（golden 置 0 侧 / 被测输入残留侧）不进统计。golden
    统计前由裁决机体按 FP32 RNE 收窄，golden64 与 golden32 判定逐位等价。"""
    golden = _to_c128("golden64", _get(case_arrays, "golden64"))
    out = _to_c128("out32", dut_out["out32"])
    n = _square_like("out32", "golden64", golden, out)
    uplo = normalize_uplo(_get(case_arrays, "uplo"))
    idx = np.tril_indices(n) if uplo == "L" else np.triu_indices(n)
    return out[idx], golden[idx]'''

_CPOTRF_BLOCK = _COMPLEX_COMMON + "\n\n\n" + r'''# ---------------------------------------------------------------------------
# cpotrf 专属件（criteria/cards_cholesky cpotrf 卡 + verdict._dpot01 复数通路的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu, ratio_cpu_mean):
    """新阈值式（DPOT01，HT-3，数值同实数书）：max(5*ratio_cpu, 3*ratio_cpu_mean)。
    第二支 mean 缺席时由裁决机体走单支兼容口径（残差 ≤ 5*ratio_cpu），不进本函数。"""
    r = _check_ratio_cpu(ratio_cpu)
    m = _check_ratio_cpu_mean(ratio_cpu_mean)
    return max(5.0 * r, 3.0 * m)


# HT-3：potrf/potrs 阈值第二支消费 ratio_cpu_mean（裁决机体据此分流缺 mean 兼容口径）。
FALLBACK_USES_MEAN = True


def residual_ratio(a, factor, uplo):
    """DPOT01 复数通路（S2 spec §4）：ratio = ‖recon−A‖₁ / (n·‖A‖₁·ε)，
    L 侧 recon=L·Lᴴ、U 侧 recon=Uᴴ·U，范数与绝对值取复模，先升 complex128。

    a 传 A64（README 1.3 参考链路口径），对角虚部须为 0（Hermitian 存储校验）；
    因子与差值只用存储侧，范数按半三角共轭镜像（DLANHE('1')）口径。
    NaN/Inf、实数阵、shape 错、零分母 → 抛异常，不返回数值。
    """
    uplo = normalize_uplo(uplo)
    a = _as_c128("a", a)
    f = _as_c128("factor", factor)
    _check_diag_real("a", a)
    n = _square("a", a)
    if f.shape != a.shape:
        raise ValueError(f"shape 不匹配: a{a.shape} vs factor{f.shape}")
    fh = _half(f, uplo)
    recon = fh @ fh.conj().T if uplo == "L" else fh.conj().T @ fh
    num = _sym_norm1_from_half(_half(recon - a, uplo))
    anorm = _sym_norm1_from_half(_half(a, uplo))
    if anorm <= 0.0:
        raise ValueError("零分母：‖A‖₁ = 0")
    return float(num / (n * anorm * EPS32))


def layer1_targets(case_arrays, dut_out):
    """cpotrf 主判目标（HT-7 对调）：F vs golden 直审（存储侧半三角 vs golden64），
    实/虚各自成目标。"""
    actual, golden = _c_tri_pair(case_arrays, dut_out)
    return _reim("factor_vs_golden", actual, golden)


def layer1_diagnostics(case_arrays, dut_out):
    """cpotrf 诊断项（HT-7 对调）：还原 recon = L·Lᴴ（L 侧）/ Uᴴ·U（U 侧）后
    取指定三角对原 A（A64=complex128），拆实/虚，并报不主判（数学形式由 DPOT01
    兜底层保留）。"""
    a64 = _to_c128("A64", _get(case_arrays, "A64"))
    out = _to_c128("out32", dut_out["out32"])
    n = _square_like("out32", "A64", a64, out)
    uplo = normalize_uplo(_get(case_arrays, "uplo"))
    if uplo == "L":
        fh = np.tril(out)
        recon = fh @ fh.conj().T
        idx = np.tril_indices(n)
    else:
        fh = np.triu(out)
        recon = fh.conj().T @ fh
        idx = np.triu_indices(n)
    return _reim("recon_vs_A", recon[idx], a64[idx])


def fallback_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A64"),
        "factor": dut_out["out32"],
        "uplo": normalize_uplo(_get(case_arrays, "uplo")),
    }'''

_CPOTRS_BLOCK = _COMPLEX_COMMON + "\n\n\n" + r'''# ---------------------------------------------------------------------------
# cpotrs 专属件（criteria/cards_cholesky cpotrs 卡 + verdict._dpot02 复数通路的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu, ratio_cpu_mean):
    """新阈值式（DPOT02，HT-3，数值同实数书）：max(5*ratio_cpu, 3*ratio_cpu_mean)。
    第二支 mean 缺席时由裁决机体走单支兼容口径（残差 ≤ 5*ratio_cpu），不进本函数。"""
    r = _check_ratio_cpu(ratio_cpu)
    m = _check_ratio_cpu_mean(ratio_cpu_mean)
    return max(5.0 * r, 3.0 * m)


# HT-3：potrf/potrs 阈值第二支消费 ratio_cpu_mean（裁决机体据此分流缺 mean 兼容口径）。
FALLBACK_USES_MEAN = True


def residual_ratio(a, b, x):
    """DPOT02 复数通路（S2 spec §4）：ratio = max_j ‖B_j−A·X_j‖₁ / (‖A‖₁·‖X_j‖₁·ε)，
    逐 RHS 列取 max，范数取复模，先升 complex128。

    分母无 n（README 2.3）；a/b 传 A32/B32（complex64），内部统一升 complex128；
    a 的对角虚部须为 0。NaN/Inf、实数阵、shape 错、零分母 → 抛异常，不返回数值。
    """
    a = _as_c128("a", a)
    b = _as_c128("b", b)
    x = _as_c128("x", x)
    _check_diag_real("a", a)
    n = _square("a", a)
    if b.shape != x.shape or b.shape[0] != n or b.shape[1] < 1:
        raise ValueError(f"shape 不匹配: a{a.shape}, b{b.shape}, x{x.shape}")
    anorm = float(np.max(np.sum(np.abs(a), axis=0)))
    if anorm <= 0.0:
        raise ValueError("零分母：‖A‖₁ = 0")
    resid = b - a @ x
    ratio = 0.0
    for j in range(b.shape[1]):
        xnorm = float(np.sum(np.abs(x[:, j])))
        if xnorm <= 0.0:
            raise ValueError(f"零分母：x 第 {j} 列 1-范数为 0")
        bnorm = float(np.sum(np.abs(resid[:, j])))
        ratio = max(ratio, bnorm / (anorm * xnorm * EPS32))
    return float(ratio)


def layer1_targets(case_arrays, dut_out):
    """cpotrs 主判目标：解矩阵 X 全量逐元素 vs golden64，实/虚各自成目标
    （HT-7 基准换 golden64）。"""
    golden = _to_c128("golden64", _get(case_arrays, "golden64"))
    out = _to_c128("out32", dut_out["out32"])
    if out.shape != golden.shape:
        raise ValueError(f"shape 不匹配: out32{out.shape} vs golden64{golden.shape}")
    return _reim("x_vs_golden", out.ravel(), golden.ravel())


def layer1_diagnostics(case_arrays, dut_out):
    """cpotrs 无诊断项。"""
    return []


def fallback_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32"),
        "b": _get(case_arrays, "B32"),
        "x": dut_out["out32"],
    }'''

_CPOTRI_BLOCK = _COMPLEX_COMMON + "\n\n\n" + r'''# ---------------------------------------------------------------------------
# cpotri 专属件（criteria/cards_cholesky cpotri 卡 + verdict._dpot03 复数通路的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu):
    """新阈值式（DPOT03，HT-5，数值同实数书）：max(5*ratio_cpu, 0.1)。"""
    r = _check_ratio_cpu(ratio_cpu)
    return max(5.0 * r, 0.1)


# HT-3：potri 阈值无 mean 第二支（结构性单支），裁决机体据此走单支式 fallback_threshold。
FALLBACK_USES_MEAN = False


def residual_ratio(a, ainv, uplo):
    """DPOT03 复数通路（S2 spec §4）：ratio = ‖I−A·C‖₁ / (n·‖A‖₁·‖C‖₁·ε)，
    双半三角口径，Hermitian 共轭镜像，范数取复模，先升 complex128。

    a 传 A32（README 3.3：A 用实现实际输入升精度），对角虚部须为 0；a 与 ainv
    各取存储侧镜像成全阵再相乘。NaN/Inf、实数阵、shape 错、零分母 → 抛异常。
    """
    uplo = normalize_uplo(uplo)
    a = _as_c128("a", a)
    c = _as_c128("ainv", ainv)
    _check_diag_real("a", a)
    n = _square("a", a)
    if c.shape != a.shape:
        raise ValueError(f"shape 不匹配: a{a.shape} vs ainv{c.shape}")
    a_half = _half(a, uplo)
    c_half = _half(c, uplo)
    anorm = _sym_norm1_from_half(a_half)
    cnorm = _sym_norm1_from_half(c_half)
    if anorm <= 0.0 or cnorm <= 0.0:
        raise ValueError("零分母：‖A‖₁ 或 ‖C‖₁ 为 0")
    w = np.eye(n) - _mirror_half(a_half) @ _mirror_half(c_half)
    num = float(np.max(np.sum(np.abs(w), axis=0)))
    return float(num / (n * anorm * cnorm * EPS32))


def layer1_targets(case_arrays, dut_out):
    """cpotri 主判单目标（HT-7 收单）：A⁻¹ 直审（存储侧半三角 vs golden64），
    拆实/虚共 2 目标；A·A⁻¹ 对 I 撤出第一步，只留 DPOT03 复核层。"""
    direct_actual, direct_golden = _c_tri_pair(case_arrays, dut_out)
    return _reim("ainv_vs_golden", direct_actual, direct_golden)


def layer1_diagnostics(case_arrays, dut_out):
    """cpotri 无诊断项。"""
    return []


def fallback_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32"),
        "ainv": dut_out["out32"],
        "uplo": normalize_uplo(_get(case_arrays, "uplo")),
    }'''


# --8<-- ACCURACY-TEMPLATE-BEGIN
_ACCURACY_TEMPLATE = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""__OP__ 精度自测辅助件（accept criteria 的渲染副本；由 render_verify.py 生成，勿手改）。

三层判定（任务书 §3.2，criteria/verdict.judge 的同语义副本）：layer1 逐元素混合
容差（标准表单套 rtol=atol=2^-13，HT-14）整体双门（matched_ratio ≥ 0.99 且
max_abs ≤ 动态上限 max(1e-2, 32·ULP(g_low))，HT-1）→ 不过 → __KIND__ 残差兜底
终审（阈值 = __FORMULA__，ratio_cpu 逐 case 随包；potrf/potrs 另吃第二支
ratio_cpu_mean，缺走单支兼容口径，HT-3）。
__OP__ 主判目标：__TARGET_DOC__

用法：
    python3 verify_accuracy.py --package <包目录> --dut-out <被测输出目录> \
        [--report <json>] [--case-id <id> ...]

输入契约（spec §2.2/§2.5）：包内 cases/index.json 与 cases/*.npz；被测输出
<dut-out>/<case_id>.npz 含 out32、info、status。

身份声明（设计 v2 §3.3）：本件是自测辅助件，输出**不构成验收证据**；数值判定不是
正式结论（formal 恒 PENDING_RULING），正式裁决由 accept 用自带判据
独立计算。

退出码：0 = 全部数值 PASS；1 = 存在数值 FAIL 或证据不足；2 = 用法/输入错误。
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

OP = "__OP__"
RESIDUAL_KIND = "__KIND__"
CRITERIA_VER = "__CRITERIA_VER__"
RENDERER_VER = "__RENDERER_VER__"
FALLBACK_FORMULA = "__FORMULA__"
DISCLAIMER = "自测辅助件：输出不构成验收证据；正式裁决在 accept（设计 v2 §3.3）"

# ---- 判据参数（spec §2.3′ 任务书契约；渲染时已与 criteria/thresholds.py 逐项核对）----
__CONSTANTS__


# ---------------------------------------------------------------------------
# 公共小件（criteria/verdict 与 criteria/cards_cholesky 的渲染副本）
# ---------------------------------------------------------------------------


def normalize_uplo(uplo):
    """归一 uplo 枚举：只认 "L"/"U"（spec §0 枚举），bytes 先解码。"""
    if isinstance(uplo, bytes):
        uplo = uplo.decode("ascii", "replace")
    u = str(uplo)
    if u not in ("L", "U"):
        raise ValueError(f'uplo 只允许 "L"/"U"（spec §0 枚举），得到 {uplo!r}')
    return u


def _check_ratio_cpu(ratio_cpu):
    """阈值公式入参校验：ratio_cpu 必须是有限非负实数（spec §2.4）。"""
    r = float(ratio_cpu)
    if not math.isfinite(r):
        raise ValueError(f"ratio_cpu 非有限值: {ratio_cpu!r}")
    if r < 0:
        raise ValueError(f"ratio_cpu 不得为负: {ratio_cpu!r}")
    return r


def _check_ratio_cpu_mean(ratio_cpu_mean):
    """mean 线入参校验（HT-3）：有限非负实数，None 显式拒绝（缺 mean 不静默降级，
    由裁决机体在调用前分流单支兼容口径）。"""
    if ratio_cpu_mean is None:
        raise ValueError("ratio_cpu_mean 缺失（第二支 3·ratio_cpu_mean 不可算）")
    m = float(ratio_cpu_mean)
    if not math.isfinite(m):
        raise ValueError(f"ratio_cpu_mean 非有限值: {ratio_cpu_mean!r}")
    if m < 0:
        raise ValueError(f"ratio_cpu_mean 不得为负: {ratio_cpu_mean!r}")
    return m


def max_abs_limit(g_low):
    """max_abs 动态上限：max(兜底值, 32·ULP(g_low))（HT-1 裁定后的单口径）。

    出处：opbase 混合容差标准 §2.1.2，FP32 兜底值 1e-2。g_low 是最大绝对误差点
    的 golden 值按输出 dtype RNE 收窄后的值（_layer1_stats 选出）；None 表示无
    有限比对点，abs 门空真，返回兜底值。ULP 取 float32 在 |g_low| 处的间距语义
    （np.spacing）——与残差公式的 ε=2^-24（EPS32）分属两个常数，勿混用。
    """
    if g_low is None:
        return MAX_ABS_FIXED
    ulp = float(np.spacing(np.float32(abs(float(g_low)))))
    return max(MAX_ABS_FIXED, ULP_MULT * ulp)


def _as_f64(name, arr):
    """严校验升型（实数残差接口用）：复数、非数值、非 2 维、NaN/Inf 一律抛异常。"""
    a = np.asarray(arr)
    if np.issubdtype(a.dtype, np.complexfloating):
        raise TypeError(f"{name}: 复数不得进实数通路（S2 spec §4：实/复按 dtype 分流）")
    if not (np.issubdtype(a.dtype, np.floating) or np.issubdtype(a.dtype, np.integer)):
        raise TypeError(f"{name}: 非数值 dtype {a.dtype}")
    if a.ndim != 2:
        raise ValueError(f"{name}: 期望 2 维数组，得到 shape={a.shape}")
    a = a.astype(np.float64)
    if not np.isfinite(a).all():
        raise ValueError(f"{name}: 含 NaN/Inf")
    return a


def _to_f64(name, arr):
    """宽松升型（实数 layer1 取数用）：不查有限性（NaN/Inf 流进统计计为不符）。"""
    a = np.asarray(arr)
    if np.issubdtype(a.dtype, np.complexfloating):
        raise TypeError(f"{name}: 复数须走复数卡（S2 spec §4：实/复按 dtype 分流）")
    if not (np.issubdtype(a.dtype, np.floating) or np.issubdtype(a.dtype, np.integer)):
        raise TypeError(f"{name}: 非数值 dtype {a.dtype}")
    return a.astype(np.float64)


def _square(name, a):
    if a.shape[0] != a.shape[1] or a.shape[0] == 0:
        raise ValueError(f"{name}: 期望非空方阵，得到 shape={a.shape}")
    return a.shape[0]


def _square_like(name, ref_name, ref, out):
    if ref.ndim != 2 or ref.shape[0] != ref.shape[1] or ref.shape[0] == 0:
        raise ValueError(f"{ref_name}: 期望非空方阵，得到 shape={ref.shape}")
    if out.shape != ref.shape:
        raise ValueError(f"shape 不匹配: {name}{out.shape} vs {ref_name}{ref.shape}")
    return ref.shape[0]


def _half(a, uplo):
    """取存储侧半三角（含对角），另侧置 0。"""
    return np.tril(a) if uplo == "L" else np.triu(a)


def _mirror_half(half):
    """半三角镜像成全阵（输入必须是另侧已置 0 的半三角，含对角）。

    实数镜像成对称阵；复数按 Hermitian 共轭镜像、对角虚部按 0 处理
    （S2 spec §4）。实数路径与原对称实现逐位等价（conj/.real 对实数恒等）。
    """
    return half + half.conj().T - np.diag(np.diag(half).real)


def mirror_storage_side(a, uplo):
    """按 uplo 取存储侧半三角并镜像成全阵（对侧 stale 不能用的统一口径）；
    复数输入按 Hermitian 共轭镜像（S2 spec §4）。"""
    return _mirror_half(_half(np.asarray(a), uplo))


def _sym_norm1_from_half(half):
    """DLANSY('1') 口径的 1-范数：半三角镜像后取最大列绝对和。

    复数输入即 DLANHE('1') 口径（Hermitian 共轭镜像，绝对值取复模）。"""
    full = _mirror_half(half)
    return float(np.max(np.sum(np.abs(full), axis=0)))


def _get(case_arrays, key):
    if key not in case_arrays:
        raise KeyError(f"case_arrays 缺 {key}（{OP} 卡需要，spec §2.2）")
    return case_arrays[key]


def _tri_pair(case_arrays, dut_out):
    """存储侧半三角逐元素对（vs golden64）：返回 (actual64, golden64) 一维向量。
    golden 统计前由裁决机体按 FP32 RNE 收窄，golden64 与 golden32 判定逐位等价。"""
    golden = _to_f64("golden64", _get(case_arrays, "golden64"))
    out = _to_f64("out32", dut_out["out32"])
    n = _square_like("out32", "golden64", golden, out)
    uplo = normalize_uplo(_get(case_arrays, "uplo"))
    idx = np.tril_indices(n) if uplo == "L" else np.triu_indices(n)
    return out[idx], golden[idx]


__OP_BLOCK__


# ---------------------------------------------------------------------------
# 数值裁决（criteria/verdict.judge 的同语义副本；A6 口径：golden64 基准、
# 2⁻¹³ 单套容差、动态锚点 max_abs 门、HT-3 新阈值式、flags 恒空）
# ---------------------------------------------------------------------------


def _null_fallback():
    # fallback 未运行时 ran=False 其余字段 null（任务书 §3.2）。eps 例外，仍披露口径值：
    # 它是残差 ratio 的归一基准（issue A4；任务书 2026-10 定稿已改 ε=2^-24=SLAMCH('E') 口径，与本值一致），不是运行结果，缺省更易误读。
    return {"ran": False, "ratio": None, "threshold": None, "formula": None,
            "pass": None, "eps": "2^-24"}


def _error_verdict(msg):
    # error 非空时 numeric 恒 FAIL（fail-closed）；上层依 error 区分
    # 「证据不足/环境错」与真精度失败，不得把这种 FAIL 直接当精度结论上报。
    return {
        "layer1": None,
        "fallback": _null_fallback(),
        "numeric": "FAIL",
        "formal": "PENDING_RULING",
        "flags": [],
        "error": str(msg),
    }


def _layer1_stats(actual, golden, rtol, atol):
    """逐元素混合容差统计（任务书 §3.2 判定式）：返回 (matched_ratio, max_abs, g_low)。

    比对前 golden 先按输出 dtype（float32）RNE 收窄（numpy astype 即 RNE，上溢自然
    成 ±inf），与「被测 float32 输出升 f64」的 actual 对齐成 float32 值域语义。
    逐点分类与 ±inf/NaN 规则出处：混合容差标准 §2.1.2（收窄比对规则；双方 NaN
    视作通过同属该收窄语义）：

    - 双方有限 → |a-g| <= atol + rtol*|g| 判定，并参与 max_abs 选取；
    - 双方同号 ±inf、双方 NaN → 通过点，不参与 max_abs；
    - 异号 inf、一方 inf/NaN 一方正常 → 计为不符，不参与 max_abs。

    max_abs 在有限点集上取；g_low 是取到 max_abs 那个点的收窄 golden 值（并列取
    首个扁平下标，确定性）。有限点集为空时 max_abs=0.0、g_low=None（abs 门空真，
    matched_ratio 门独立把关）。
    """
    a = np.asarray(actual, dtype=np.float64)
    with np.errstate(over="ignore"):
        g = np.asarray(golden, dtype=np.float64).astype(np.float32).astype(np.float64)
    finite = np.isfinite(a) & np.isfinite(g)
    with np.errstate(invalid="ignore"):
        err = np.abs(a - g)
        ok = finite & (err <= atol + rtol * np.abs(g))
        ok |= np.isinf(a) & np.isinf(g) & (np.sign(a) == np.sign(g))
    ok |= np.isnan(a) & np.isnan(g)
    n_total = int(a.size)
    matched_ratio = 1.0 - (n_total - int(np.count_nonzero(ok))) / n_total
    if not bool(finite.any()):
        return matched_ratio, 0.0, None
    idx = int(np.argmax(np.where(finite, err, -1.0)))
    return matched_ratio, float(err.flat[idx]), float(g.flat[idx])


def _gate(matched_ratio, max_abs, g_low):
    """整体双门（HT-1 裁定后单口径）：通过率门 AND max_abs 动态上限门。"""
    return bool(matched_ratio >= REQUIRED_MATCHED_RATIO
                and max_abs <= max_abs_limit(g_low))


def _target_entry(name, actual, golden):
    """单个比对目标的 layer1 统计（HT-14 后单套容差）。

    复数拆出的实/虚目标各自走本函数，锚点与上限天然独立。
    """
    matched_ratio, max_abs, g_low = _layer1_stats(
        actual, golden, LAYER1_RTOL_FP32, LAYER1_ATOL_FP32)
    return {
        "name": name,
        "matched_ratio": float(matched_ratio),
        "max_abs": max_abs,
        "max_abs_limit": max_abs_limit(g_low),
        "g_low": g_low,
        "pass": _gate(matched_ratio, max_abs, g_low),
    }


def _diagnostics(case_arrays, dut_out):
    """诊断比对（spec §2.3′：并报不主判，如 potrf 的还原比对 recon vs A64）。

    只报统计（单套容差的 matched_ratio 与 max_abs），不进任何门；算不出记
    error 条目，不影响裁决——诊断降级的含义就是它的失败不阻断主判。
    """
    try:
        items = layer1_diagnostics(case_arrays, dut_out)
    except Exception as exc:
        return [{"name": "diagnostics", "error": f"{type(exc).__name__}: {exc}"}]
    out = []
    for item in items:
        name = item[0]
        try:
            _, actual, golden = item
            mr, max_abs, _ = _layer1_stats(
                actual, golden, LAYER1_RTOL_FP32, LAYER1_ATOL_FP32)
            out.append({"name": name, "matched_ratio": float(mr), "max_abs": max_abs})
        except Exception as exc:
            out.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
    return out


def _run_fallback(case_arrays, dut_out):
    """跑 fallback 残差并对阈值裁决。返回 (fallback dict, numeric, error)。

    阈值消费面（HT-3）：uses_mean 算子按两支式 max(5·ratio_cpu, 3·ratio_cpu_mean)
    裁决，ratio_cpu_mean 从 case_arrays 读（accept 从 index 顶层按算子注入）。
    缺 mean 的兼容口径（fail-closed 方向）：残差 ≤ 单支 5·ratio_cpu → PASS
    （证据注明单支）；超出 → 证据不足不判 FAIL（两支公式只可证一支），待含
    mean 的包复判。"""
    status = case_arrays.get("ratio_cpu_status")
    ratio_cpu = case_arrays.get("ratio_cpu")
    if status != "ok" or ratio_cpu is None:
        return (
            _null_fallback(),
            "FAIL",
            f"兜底不可用: ratio_cpu_status={status!r}, ratio_cpu={ratio_cpu!r}"
            "（spec §2.4：准备失败该 case 兜底不可用）",
        )
    formula = FALLBACK_FORMULA
    mean = case_arrays.get("ratio_cpu_mean")
    if not FALLBACK_USES_MEAN:
        threshold = float(fallback_threshold(ratio_cpu))
    elif mean is not None:
        threshold = float(fallback_threshold(ratio_cpu, mean))
    else:
        threshold = None        # 缺 mean：残差算出后按单支兼容路径裁决
    try:
        ratio = residual_ratio(**fallback_kwargs(case_arrays, dut_out))
    except Exception as exc:  # 残差算不出 → fail-closed，error 指认原因
        fb = {"ran": True, "ratio": None, "threshold": threshold,
              "formula": formula, "pass": False, "eps": "2^-24"}
        return fb, "FAIL", f"fallback 残差不可计算: {type(exc).__name__}: {exc}"
    if threshold is None:
        # 缺 mean 兼容（HT-3）：单支过即 PASS；超单支 → 证据不足。
        line = 5.0 * _check_ratio_cpu(ratio_cpu)   # 单支线 5·ratio_cpu
        if ratio <= line:
            fb = {"ran": True, "ratio": float(ratio), "threshold": line,
                  "formula": "5*ratio_cpu（缺 ratio_cpu_mean 单支，v2 包兼容口径）",
                  "pass": True, "eps": "2^-24"}
            return fb, "PASS", None
        fb = {"ran": True, "ratio": float(ratio), "threshold": None,
              "formula": formula, "pass": None, "eps": "2^-24"}
        return fb, "FAIL", (
            "fallback 证据不足: 缺 ratio_cpu_mean，"
            f"{formula} 第二支不可算；残差 {ratio:.6g} 超单支 "
            f"5·ratio_cpu={line:.6g}，不判 FAIL，待含 mean 的包复判")
    ok = bool(ratio <= threshold)
    fb = {"ran": True, "ratio": float(ratio), "threshold": threshold,
          "formula": formula, "pass": ok, "eps": "2^-24"}
    return fb, ("PASS" if ok else "FAIL"), None


def _judge_inner(case_arrays, dut_out):
    if not isinstance(dut_out, dict):
        return _error_verdict(f"dut_out 必须是 dict，得到 {type(dut_out).__name__}")
    status = dut_out.get("status")
    if status != "ok":
        return _error_verdict(f"被测状态非 ok: status={status!r}")
    info = dut_out.get("info")
    if info is None or int(info) != 0:
        return _error_verdict(f"被测 info 非 0: info={info!r}")
    if dut_out.get("out32") is None:
        return _error_verdict("dut_out 缺 out32")

    targets = layer1_targets(case_arrays, dut_out)
    if not targets:
        return _error_verdict("layer1 无比对目标（卡给出空目标清单）")
    entries = []
    for name, actual, golden in targets:
        if (np.issubdtype(np.asarray(actual).dtype, np.complexfloating)
                or np.issubdtype(np.asarray(golden).dtype, np.complexfloating)):
            return _error_verdict(
                f"layer1 目标 {name} 含复数数组：复数须由卡拆成实/虚目标再进统计"
                "（复数任务书 §3.2 契约不合并稀释）")
        if actual.size == 0:
            return _error_verdict(f"layer1 对比集为空: {name}")
        entries.append(_target_entry(name, actual, golden))

    # 多目标聚合（复数拆实/虚为多目标：全部通过才算过）：pass 取 AND、
    # matched_ratio 取最差（min）、max_abs 取最差（max）；单目标算子退化为原语义。
    # 顶层 max_abs_limit/g_low 随聚合 max_abs 所在目标走（并列取首个），保持三元
    # 自洽；门槛判定在逐目标层完成（复数拆实/虚两侧各自锚点各自上限），顶层字段
    # 只是证据面。
    l1_pass = all(e["pass"] for e in entries)
    worst = max(entries, key=lambda e: e["max_abs"])

    layer1 = {
        "matched_ratio": min(e["matched_ratio"] for e in entries),
        "max_abs": worst["max_abs"],
        "max_abs_limit": worst["max_abs_limit"],
        "g_low": worst["g_low"],
        "pass": l1_pass,
        "targets": entries,
        "diagnostics": _diagnostics(case_arrays, dut_out),
    }

    # flags：T3 已随 HT-14 拆除（双套容差收成标准表单套，无分歧可记）；T4 已随
    # HT-1 拆除（max_abs 动态锚点单口径）；T7 已摘（issue C3 确认拆实/虚口径升
    # 正式）；T8 已随 HT-16 摘除。flags 恒空。
    flags = []

    if l1_pass:
        # layer1 全部目标双门都过 → 数值 PASS（终审），fallback 不跑。
        fallback, numeric, error = _null_fallback(), "PASS", None
    else:
        # 任一目标任一门不过 → 走 fallback，fallback 为数值终审。
        fallback, numeric, error = _run_fallback(case_arrays, dut_out)

    return {"layer1": layer1, "fallback": fallback, "numeric": numeric,
            "formal": "PENDING_RULING", "flags": flags, "error": error}


def _judge_info_inner(case_arrays, dut_out):
    """HT-8 info 契约支路（case_purpose=="info" 的用例专用）：只比 info，
    不进残差不进 layer1/fallback（B2 卡口径：非正定/奇异用例分解中途失败，
    残差无意义；info 契约与数值精度各出独立结论，HT-12）。

    fail-closed：dut_out 非 dict、status 非 "ok"、k_expected 缺失/不可取整、
    info 非标量，一律 error verdict（证据问题不判精度）。"""
    if not isinstance(dut_out, dict):
        return _error_verdict(f"dut_out 必须是 dict，得到 {type(dut_out).__name__}")
    status = dut_out.get("status")
    if status != "ok":
        return _error_verdict(f"被测状态非 ok: status={status!r}")
    try:
        k_expected = int(case_arrays["k_expected"])
    except (KeyError, TypeError, ValueError):
        return _error_verdict("case_arrays 缺可取整的 k_expected（info 用例必带字段）")
    info = dut_out.get("info")
    if info is None or np.ndim(info) != 0:
        return _error_verdict(f"被测 info 应为标量整数，得到 {info!r}")
    try:
        actual = int(info)
    except (TypeError, ValueError):
        return _error_verdict(f"被测 info 应为标量整数，得到 {info!r}")
    ok = actual == k_expected
    return {
        "info": {"expected": k_expected, "actual": actual, "pass": ok},
        "numeric": "PASS" if ok else "FAIL",
        "formal": "PENDING_RULING",
        "flags": [],
        "error": None,
    }


def judge(case_arrays, dut_out):
    """数值裁决副本：judge(case_arrays, dut_out) -> verdict（结构同 criteria/verdict.judge）。

    judge 不外抛异常：任何内部异常收敛为 error 字段 + numeric FAIL（fail-closed）；
    error 非空的 FAIL 属「不可裁/证据问题」，不得当精度 FAIL 上报。
    case_purpose=="info" 的用例分流到 _judge_info_inner（HT-8：只比 info，
    verdict 带 "info" 键、无 layer1/fallback）。
    """
    try:
        if case_arrays.get("case_purpose") == "info":
            return _judge_info_inner(case_arrays, dut_out)
        return _judge_inner(case_arrays, dut_out)
    except Exception as exc:
        return _error_verdict(f"judge 内部异常: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# CLI：读包与被测输出，逐 case 出数值判定
# ---------------------------------------------------------------------------


def _scalar(value):
    """npz 0 维标量 → python 标量；bytes → str。"""
    if isinstance(value, np.ndarray):
        value = value.item() if value.ndim == 0 else value
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, np.generic):
        value = value.item()
    return value


def _load_case_arrays(pkg_dir, entry):
    with np.load(pkg_dir / entry["npz"]) as z:
        arrays = {k: np.array(z[k]) for k in z.files}
    arrays["uplo"] = entry.get("uplo")
    arrays["ratio_cpu"] = entry.get("ratio_cpu")
    arrays["ratio_cpu_status"] = entry.get("ratio_cpu_status")
    for meta in ("case_purpose", "k_expected"):   # HT-8：info 用例 judge 分流依据
        if entry.get(meta) is not None:
            arrays[meta] = entry[meta]
    return arrays


def _load_dut_out(dut_dir, case_id):
    path = dut_dir / f"{case_id}.npz"
    if not path.is_file():
        return None
    with np.load(path) as z:
        found = {k: np.array(z[k]) for k in z.files}
    info = found.get("info")
    status = found.get("status")
    return {"out32": found.get("out32"),
            "info": _scalar(info) if info is not None else None,
            "status": str(_scalar(status)) if status is not None else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=f"{OP} 精度自测辅助件（渲染副本）")
    ap.add_argument("--package", required=True, help="任务包目录（含 cases/index.json）")
    ap.add_argument("--dut-out", required=True, help="被测输出目录（<case_id>.npz）")
    ap.add_argument("--report", help="逐 case 判定 JSON 报告输出路径")
    ap.add_argument("--case-id", action="append", help="只跑指定 case（可重复）")
    args = ap.parse_args(argv)

    pkg_dir = Path(args.package)
    dut_dir = Path(args.dut_out)
    index_path = pkg_dir / "cases" / "index.json"
    try:
        with open(index_path, "r", encoding="utf-8") as fh:
            index = json.load(fh)
        entries = [e for e in index["cases"] if e.get("op") == OP]
    except Exception as exc:
        print(f"[错误] 读不了 {index_path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if args.case_id:
        wanted = list(dict.fromkeys(args.case_id))
        by_id = {e["case_id"]: e for e in entries}
        missing = [c for c in wanted if c not in by_id]
        if missing:
            print(f"[错误] index 中找不到 case: {missing}", file=sys.stderr)
            return 2
        entries = [by_id[c] for c in wanted]
    if not entries:
        print(f"[错误] index 中没有 {OP} 的 case", file=sys.stderr)
        return 2

    print(f"# verify_accuracy — {OP}（criteria {CRITERIA_VER} / renderer {RENDERER_VER}）")
    print(f"# {DISCLAIMER}")
    results = []
    n_pass = n_fail = n_noev = 0
    for entry in entries:
        case_id = entry["case_id"]
        try:
            arrays = _load_case_arrays(pkg_dir, entry)
        except Exception as exc:
            n_noev += 1
            results.append({"case_id": case_id, "status": "证据不足",
                            "note": f"包输入不可读: {type(exc).__name__}: {exc}"})
            print(f"{case_id}  证据不足（包输入不可读）")
            continue
        dut = _load_dut_out(dut_dir, case_id)
        if dut is None:
            n_noev += 1
            results.append({"case_id": case_id, "status": "证据不足",
                            "note": f"缺被测输出 {case_id}.npz"})
            print(f"{case_id}  证据不足（缺被测输出）")
            continue
        v = judge(arrays, dut)
        results.append({"case_id": case_id, "status": "数值判定", "verdict": v})
        if v["numeric"] == "PASS":
            n_pass += 1
        else:
            n_fail += 1
        flags = ",".join(v["flags"]) if v["flags"] else "-"
        err = "-" if v["error"] is None else v["error"]
        print(f"{case_id}  numeric={v['numeric']}  flags={flags}  error={err}")

    total = len(entries)
    print(f"# 合计 {total}：数值 PASS {n_pass} / 数值 FAIL {n_fail} / 证据不足 {n_noev}")
    print("# formal 恒 PENDING_RULING：本输出不构成验收结论（T1/T3/T4 未裁）")

    if args.report:
        report = {
            "tool": {"name": "verify_accuracy.py", "op": OP,
                     "criteria_ver": CRITERIA_VER, "renderer_ver": RENDERER_VER,
                     "residual_kind": RESIDUAL_KIND},
            "disclaimer": DISCLAIMER,
            "params": {
                "taskbook_rtol": TASKBOOK_RTOL_FP32, "taskbook_atol": TASKBOOK_ATOL_FP32,
                "standard_rtol": STANDARD_RTOL_FP32, "standard_atol": STANDARD_ATOL_FP32,
                "required_matched_ratio": REQUIRED_MATCHED_RATIO,
                "max_abs_fixed": MAX_ABS_FIXED, "max_abs_ulp32": MAX_ABS_ULP32,
                "eps32": EPS32, "fallback_formula": FALLBACK_FORMULA,
            },
            "cases": results,
            "summary": {"total": total, "numeric_pass": n_pass,
                        "numeric_fail": n_fail, "no_evidence": n_noev},
        }
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
            fh.write("\n")

    return 0 if (n_fail == 0 and n_noev == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
'''
# --8<-- ACCURACY-TEMPLATE-END


# --8<-- PERF-TEMPLATE-BEGIN
_PERF_TEMPLATE = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""__OP__ 性能自测辅助件（perf_baseline 逐 case 比值；由 render_verify.py 生成，勿手改）。

比值方向（spec 波 2 D 行）：**ratio = 被测耗时 / 基线耗时**；>1 慢于基线参考、
<1 快于基线参考。基线是包内 perf_baseline.json（竞品 bench_result 的逐 case 摘录，
spec §2.2）；matched=false 的 case 记证据不足。

T1 运行语义（spec §2.3′）：正式性能门禁按任务书 §3.3 机制在 accept 执行；
bench_result 仅自测参考，本输出**不构成验收证据**、不出 PASS/FAIL。

用法：
    python3 verify_perf.py --package <包目录> --dut-perf <json> [--report <json>]

--dut-perf 两种形态皆可：
- 列表：[{"case_id": "__OP__-0001", "avg_ms": 0.012, "min_ms": ..., "max_ms": ...}, ...]
  （min_ms/max_ms 可省，省则对应比值不算）
- 字典：{"__OP__-0001": 0.012, ...}（数值按 avg_ms 理解）

退出码：0 = 正常算完（比值只是参考，不设门）；2 = 用法/输入错误。
"""

import argparse
import json
import sys
from pathlib import Path

OP = "__OP__"
CRITERIA_VER = "__CRITERIA_VER__"
RENDERER_VER = "__RENDERER_VER__"
DIRECTION = "ratio = 被测耗时 / 基线耗时（>1 慢于基线参考，<1 快于基线参考）"
DISCLAIMER = ("自测辅助件：输出不构成验收证据；正式性能门禁按任务书 §3.3 机制"
              "在 accept 执行（T1，spec §2.3′）")

_FIELDS = ("min_ms", "max_ms", "avg_ms")


def _normalize_dut_perf(raw):
    """归一 --dut-perf：返回保序 {case_id: {min_ms, max_ms, avg_ms}}（缺省为 None）。"""
    if isinstance(raw, dict):
        seq = [{"case_id": k, "avg_ms": v} if not isinstance(v, dict)
               else {**v, "case_id": k} for k, v in raw.items()]
    elif isinstance(raw, list):
        seq = raw
    else:
        raise ValueError(f"dut-perf 顶层必须是列表或字典，得到 {type(raw).__name__}")
    items = {}
    for row in seq:
        if not isinstance(row, dict) or "case_id" not in row:
            raise ValueError(f"dut-perf 条目缺 case_id: {row!r}")
        cid = str(row["case_id"])
        if cid in items:
            raise ValueError(f"dut-perf 条目重复: {cid}")
        items[cid] = {f: (None if row.get(f) is None else float(row.get(f)))
                      for f in _FIELDS}
    return items


def _ratio(dut_ms, base_ms):
    """单字段比值：任一侧缺失或基线非正 → None（证据不可比，不硬算）。"""
    if dut_ms is None or base_ms is None or base_ms <= 0:
        return None
    return dut_ms / base_ms


def main(argv=None):
    ap = argparse.ArgumentParser(description=f"{OP} 性能自测辅助件（渲染副本）")
    ap.add_argument("--package", required=True, help="任务包目录（含 perf_baseline.json）")
    ap.add_argument("--dut-perf", required=True, help="被测逐 case 耗时 JSON")
    ap.add_argument("--report", help="JSON 报告输出路径")
    args = ap.parse_args(argv)

    pkg_dir = Path(args.package)
    try:
        with open(pkg_dir / "perf_baseline.json", "r", encoding="utf-8") as fh:
            baseline = json.load(fh)
        base_by_id = {}
        for row in baseline:
            base_by_id.setdefault(str(row["case_id"]), row)
    except Exception as exc:
        print(f"[错误] 读不了 perf_baseline.json: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    try:
        with open(args.dut_perf, "r", encoding="utf-8") as fh:
            dut = _normalize_dut_perf(json.load(fh))
    except Exception as exc:
        print(f"[错误] 读不了 --dut-perf: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"# verify_perf — {OP}（criteria {CRITERIA_VER} / renderer {RENDERER_VER}）")
    print(f"# {DIRECTION}")
    print(f"# {DISCLAIMER}")
    items = []
    n_ref = n_noev = n_unknown = 0
    for cid, rec in dut.items():
        base = base_by_id.get(cid)
        if base is None:
            n_unknown += 1
            items.append({"case_id": cid, "status": "未知 case",
                          "note": "perf_baseline.json 无此 case", "dut": rec})
            print(f"{cid}  未知 case（perf_baseline 无此条）")
            continue
        if not base.get("matched"):
            n_noev += 1
            items.append({"case_id": cid, "status": "证据不足",
                          "note": "基线未匹配 bench_result（matched=false，spec §2.2）",
                          "dut": rec})
            print(f"{cid}  证据不足（基线未匹配 bench_result）")
            continue
        if rec["avg_ms"] is None:
            n_noev += 1
            items.append({"case_id": cid, "status": "证据不足",
                          "note": "被测缺 avg_ms", "dut": rec})
            print(f"{cid}  证据不足（被测缺 avg_ms）")
            continue
        base_ms = {f: base.get(f) for f in _FIELDS}
        ratio = {f[:-3]: _ratio(rec[f], base_ms[f]) for f in _FIELDS}
        if ratio["avg"] is None:
            n_noev += 1
            items.append({"case_id": cid, "status": "证据不足",
                          "note": "基线 avg_ms 不可用（null 或非正）",
                          "dut": rec, "baseline": base_ms})
            print(f"{cid}  证据不足（基线 avg_ms 不可用）")
            continue
        n_ref += 1
        if ratio["avg"] > 1.0:
            hint = "慢于基线参考"
        elif ratio["avg"] == 1.0:
            hint = "与基线参考持平"
        else:
            hint = "快于基线参考"
        items.append({"case_id": cid, "status": "参考",
                      "ratio": ratio, "dut": rec, "baseline": base_ms,
                      "bench_key": base.get("bench_key"),
                      "dup_count": base.get("dup_count")})
        extras = "".join(
            f"  ratio_{k}={ratio[k]:.6g}" for k in ("min", "max") if ratio[k] is not None)
        print(f"{cid}  ratio_avg={ratio['avg']:.6g}{extras}  （{hint}）")

    unmeasured = [cid for cid in base_by_id if cid not in dut]
    print(f"# 合计：参考比值 {n_ref} / 证据不足 {n_noev} / 未知 case {n_unknown}；"
          f"基线中未测 {len(unmeasured)} 条")

    if args.report:
        report = {
            "tool": {"name": "verify_perf.py", "op": OP,
                     "criteria_ver": CRITERIA_VER, "renderer_ver": RENDERER_VER},
            "direction": DIRECTION,
            "disclaimer": DISCLAIMER,
            "items": items,
            "summary": {"reference": n_ref, "no_evidence": n_noev,
                        "unknown": n_unknown, "unmeasured_baseline": len(unmeasured)},
        }
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
# --8<-- PERF-TEMPLATE-END


# ---------------------------------------------------------------------------
# s4-D5/s3-D4 拼装件：批量四算子与纯脚本六算子的模板段（判定机体复用单矩阵模板，
# 拼装 = 换头部 docstring + 切走原 npz CLI + 接扩展段与对应 CLI；机械操作逐处断言，
# fail-closed）。s3-D4：批量段与纯脚本段同步 criteria A6（flags 恒空、
# layer1_pass_count、golden64/单套容差/动态锚点措辞，HT-16）。
# ---------------------------------------------------------------------------

# 单矩阵精度模板的 CLI 分节标记：拼装时以它切走原 npz CLI（源模板中该段文本保留，
# 只作分割锚点不参与渲染），替换为批量/纯脚本 CLI。
_ACC_CLI_MARK = """# ---------------------------------------------------------------------------
# CLI：读包与被测输出，逐 case 出数值判定
# ---------------------------------------------------------------------------"""

# 批量精度副本的头部 docstring（替换单矩阵头部；token 与单矩阵同一套替换）。
_BATCHED_HEAD_DOC = r'''__OP__ 精度自测辅助件（批量算子；accept criteria 批维通路的渲染副本；由
render_verify.py 生成，勿手改）。

批量判定语义：逐矩阵完整走单矩阵三层——layer1 逐元素标准表单套容差（rtol = atol
= 2^-13，HT-14；通过率门 0.99）加 max_abs 动态上限 max(1e-2, 32·ULP(g_low))
（HT-1），基准统一 golden64（统计前按 FP32 RNE 收窄）→ 不过 → __KIND__ 残差兜底
终审（阈值 = __FORMULA__，配对该矩阵自己的 c_i，不做 max 聚合）。case 数值结论 =
全部矩阵通过（HT-16：T8 暂定聚合标记摘除，数值结论升正式）；整批统计只入诊断。
逐矩阵主判目标：__TARGET_DOC__
info 按接口角色分型（任务书接口说明第 69 行）：potrfBatched 族被测 info 为 int32
shape=(batch,) infoArray（batch=1 不得标量化）；potrsBatched 族 info 为标量、仅报
参数错，正定性由前置分解的 infoArray 反映，准备失败不归目标接口。本件为
__INFO_ROLE_DOC__。

先造数后测（本包不携带任何数据数组）：判定时逐 case 调包内 gen_data.py 与
canonical_cases.json 现场重生成 k=min(5,batch) 个代表内容数组并计算 golden 与逐内容
参考 ratio（A0 抽样，内容/摆放/填充三条流全部从 case seed 派生，同环境逐位一致），
不读取、不依赖开发者自测用的 data 目录。

A0 抽样判定（HT-2）两层先后：先余槽 bit-wise 一致性——同一内容的全部槽位，被测
out32 与 rep_slot 输出逐位相等、infoArray 逐槽相等（标量 info 不逐槽），失配即数值
FAIL 并报槽位号（证据落 first_fail_index 与 diagnostics.a0_mismatches）；后代表槽
逐内容判定——按 rep_slot 切出 k 个代表输出与 k 个内容数组配成 batch=k 子批走上述
逐矩阵三层，子批 verdict 的序号域是内容下标 0..k-1（first_fail_index/worst_index
同），不是全批槽位号。逐内容参考 ratio 为 k 值列表，准备失败的内容记 null（该内容
若 layer1 不过则走「不可裁」error 语义，不判精度失败）。

用法：
    python3 verify_accuracy.py --package <包目录> --dut-out <被测输出目录> \
        [--report <json>] [--case-id <id> ...] [--jobs N]

--jobs N（缺省 1）把逐矩阵判定按矩阵区间切成至多 N 段多进程并行；判定只读共享
数组、无流耦合，分段结果按矩阵序合并，与串行逐位相同（内建区间覆盖断言；环境
变量 VERIFY_BATCHED_SELFCHECK 非空时另跑串行全比对断言）。

输入契约：包内 canonical_cases.json（本算子切片）与 gen_data.py；被测输出
<dut-out>/<case_id>.npz 含 out32（(batch, n, cols)，与重生成的 golden32 同 dtype）、
info（分型见上）、status。

身份声明：本件是自测辅助件，输出**不构成验收证据**；数值判定不是正式结论
（formal 恒 PENDING_RULING），正式裁决由验收流程用自带判据独立计算。

退出码：0 = 全部数值 PASS；1 = 存在数值 FAIL 或证据不足；2 = 用法/输入错误。
'''

# 批量扩展段：criteria 批维裁决（verdict 批量通路）的转写 + A0 抽样两层判定
# （先余槽 bit-wise 一致性后 rep_slot 逐内容，HT-2）+ 先造数后测的现场重生成，
# 附并行裁定的矩阵区间多进程分块。接在单矩阵判定机体之后（judge 为单矩阵裁决）。
_BATCHED_EXT_TEMPLATE = r'''# ---------------------------------------------------------------------------
# 批量通路（criteria 批维裁决的渲染副本：逐矩阵完整三层，整批统计只入诊断）
# ---------------------------------------------------------------------------

import importlib.util as _importlib_util
import multiprocessing as _mp
import os as _os

INFO_KIND = "__INFO_KIND__"     # info 接口角色："array"=infoArray | "scalar"=标量
BASE_OP = "__BASE_OP__"         # 单矩阵基算子（判据成员来源）
PREP_PREFIX = "__PREP_PREFIX__"  # 参考 ratio 低精度准备链的 LAPACK 前缀（s/c）
_PREP_DT32 = np.complex64 if PREP_PREFIX == "c" else np.float32

# 批量 case 里按首维 batch 切片的数组键；其余键原样透传给逐矩阵裁决。
_BATCH_SLICED_KEYS = ("A64", "A32", "B64", "B32", "golden64", "golden32")
# flags 恒空（HT-16：T8 暂定聚合标记摘除，批量数值结论升正式）。
# fork 子进程通过模块全局读共享数组（写时复制，不经序列化传大数组）。
_WORK = {}


def _batched_error_verdict(msg, batch=None):
    """批量 error verdict：统计域全空、numeric FAIL（fail-closed）、flags 恒空。
    error 非空属「不可裁/证据问题」，不得当精度 FAIL 上报。"""
    return {
        "batch": batch,
        "fail_count": None,
        "first_fail_index": None,
        "worst_index": None,
        "worst": None,
        "diagnostics": None,
        "numeric": "FAIL",
        "formal": "PENDING_RULING",
        "flags": [],
        "error": str(msg),
    }


def _check_batched_info(info, batch, allow_nonzero=False):
    """info 按接口角色分型（任务书接口说明第 69 行）。

    INFO_KIND="array"（potrfBatched 族）：需 shape=(batch,) 整数 infoArray，逐矩阵
    消费；batch=1 也必须是 (1,)，标量拒收（防误掩测试）。
    INFO_KIND="scalar"（potrsBatched 族）：仅报参数错，非 0 属证据问题不属精度失败；
    正定性由前置分解的 infoArray 反映——传入数组即用法错。
    HT-9：info 契约条目（bad_param_uplo 探针）本身要核对 info==-1，传
    allow_nonzero=True 只做分型检查、放过标量非 0（值留给契约比对裁）。
    通过返回 infoArray 或 None（标量已确认为 0 或放行非 0）；非法抛 ValueError。
    """
    if INFO_KIND == "array":
        arr = np.asarray(info) if info is not None else None
        if arr is None or arr.ndim != 1 or arr.shape[0] != batch:
            got = ("None" if arr is None
                   else ("标量" if arr.ndim == 0 else f"shape={arr.shape}"))
            raise ValueError(
                f"{OP} 的被测 info 应为 shape=({batch},) 的 infoArray"
                f"（int32，任务书接口说明第 69 行；batch=1 不得标量化），得到 {got}")
        if not np.issubdtype(arr.dtype, np.integer):
            raise ValueError(
                f"{OP} 的 infoArray 应为整数 dtype（任务书：int32），得到 {arr.dtype}")
        return arr
    if info is None or np.ndim(info) != 0:
        raise ValueError(
            f"{OP} 的被测 info 应为标量（仅报参数错，任务书接口说明第 69 行）；"
            f"正定性由前置分解的 infoArray 反映——得到 "
            f"{'None' if info is None else f'shape={np.shape(info)} 的数组'}")
    if int(info) != 0 and not allow_nonzero:
        raise ValueError(
            f"被测 info 非 0: info={int(info)}（{OP} 标量 info=参数错，"
            "属证据问题不属精度失败）")
    return None


def _matrix_severity(v):
    """单矩阵 verdict 的「差」序键（越大越差），worst_index 的诊断口径：先 numeric
    FAIL、再 error 非空、再 matched_ratio 小、再 max_abs 大；error 矩阵无 layer1，
    按 matched_ratio=-1、max_abs=+inf 参与排序。同差取序号小者（调用方用严格大于
    替换）。仅供诊断指认，不参与任何门。"""
    l1 = v["layer1"]
    mr = l1["matched_ratio"] if l1 is not None else -1.0
    ma = l1["max_abs"] if l1 is not None else float("inf")
    return (1 if v["numeric"] == "FAIL" else 0,
            1 if v["error"] is not None else 0,
            -mr, ma)


def _split_bounds(batch, jobs):
    """把 [0, batch) 切成至多 jobs 段连续矩阵区间，不重不漏（并行裁定的机械前提）。"""
    k = max(1, min(int(jobs), int(batch)))
    base, extra = divmod(int(batch), k)
    bounds, lo = [], 0
    for idx in range(k):
        hi = lo + base + (1 if idx < extra else 0)
        bounds.append((lo, hi))
        lo = hi
    if bounds[0][0] != 0 or bounds[-1][1] != batch:
        raise AssertionError(f"矩阵区间切分未覆盖 [0, {batch}): {bounds}")
    return bounds


def _judge_matrix(i):
    """按全局矩阵序号 i 出该矩阵的单矩阵 verdict（读 _WORK 共享数组，只读、无流
    耦合）。逐矩阵语义与单矩阵 judge 完全相同：layer1 门 → 不过走兜底，兜底
    配对该矩阵自己的 c_i。"""
    w = _WORK
    case_i = dict(w["passthrough"])
    for key, arr in w["sliced"].items():
        case_i[key] = arr[i]
    if w["rarr"] is not None:
        case_i["ratio_cpu"] = float(w["rarr"][i])
    dut_i = {"out32": w["out32"][i],
             "info": int(w["info_arr"][i]) if w["info_arr"] is not None else 0,
             "status": "ok"}
    return judge(case_i, dut_i)


def _chunk_partial(bounds):
    """判一段矩阵区间，返回该段的折叠统计（流式，只留聚合与段内最差一行明细）。"""
    lo, hi = bounds
    part = {"lo": lo, "hi": hi, "fail_count": 0, "first_fail": None,
            "error_count": 0, "first_error": None, "l1_pass": 0,
            "fb_ran": 0, "fb_pass": 0, "mr_min": None, "ma_max": None,
            "worst_i": None, "worst_v": None, "worst_sev": None}
    for i in range(lo, hi):
        v_i = _judge_matrix(i)
        if v_i["numeric"] != "PASS":
            part["fail_count"] += 1
            if part["first_fail"] is None:
                part["first_fail"] = i
        if v_i["error"] is not None:
            part["error_count"] += 1
            if part["first_error"] is None:
                part["first_error"] = f"矩阵 {i}: {v_i['error']}"
        l1 = v_i["layer1"]
        if l1 is not None:
            part["mr_min"] = (l1["matched_ratio"] if part["mr_min"] is None
                              else min(part["mr_min"], l1["matched_ratio"]))
            part["ma_max"] = (l1["max_abs"] if part["ma_max"] is None
                              else max(part["ma_max"], l1["max_abs"]))
            if l1["pass"]:
                part["l1_pass"] += 1
        fb = v_i["fallback"]
        if fb["ran"]:
            part["fb_ran"] += 1
            if fb["pass"]:
                part["fb_pass"] += 1
        sev = _matrix_severity(v_i)
        if part["worst_sev"] is None or sev > part["worst_sev"]:
            part["worst_i"], part["worst_v"], part["worst_sev"] = i, v_i, sev
    return part


def _parallel_parts(bounds):
    """多进程跑各矩阵区间；无 fork 的平台退化为进程内逐段（切分与合并路径不变）。"""
    if len(bounds) == 1:
        return [_chunk_partial(bounds[0])]
    try:
        ctx = _mp.get_context("fork")
    except ValueError:
        return [_chunk_partial(b) for b in bounds]
    with ctx.Pool(processes=len(bounds)) as pool:
        return pool.map(_chunk_partial, bounds)


def _merge_partials(parts, batch):
    """按矩阵区间升序合并分段统计；合并即串行折叠（和/最值/首个非空/严格大于取
    最差），与串行逐段一致。合并前先做区间覆盖断言（并行裁定）。"""
    cursor = 0
    for p in parts:
        if p["lo"] != cursor:
            raise AssertionError(f"矩阵区间不连续: 期望起点 {cursor}，得到 {p['lo']}")
        cursor = p["hi"]
    if cursor != batch:
        raise AssertionError(f"矩阵区间未覆盖整批: 终点 {cursor} != batch {batch}")
    fail_count = sum(p["fail_count"] for p in parts)
    error_count = sum(p["error_count"] for p in parts)
    l1_pass = sum(p["l1_pass"] for p in parts)
    fb_ran = sum(p["fb_ran"] for p in parts)
    fb_pass = sum(p["fb_pass"] for p in parts)
    first_fail = next((p["first_fail"] for p in parts if p["first_fail"] is not None), None)
    first_error = next((p["first_error"] for p in parts if p["first_error"] is not None), None)
    mr_vals = [p["mr_min"] for p in parts if p["mr_min"] is not None]
    ma_vals = [p["ma_max"] for p in parts if p["ma_max"] is not None]
    worst_i = worst_v = worst_sev = None
    for p in parts:
        if p["worst_sev"] is not None and (worst_sev is None or p["worst_sev"] > worst_sev):
            worst_i, worst_v, worst_sev = p["worst_i"], p["worst_v"], p["worst_sev"]
    # 整批统计只入诊断：以下都是诊断口径，不作任何门；case 数值结论只由
    # 「全部矩阵通过」决定。
    diagnostics = {
        "batch": batch,
        "pass_count": batch - fail_count,
        "fail_count": fail_count,
        "error_count": error_count,
        "layer1_pass_count": l1_pass,
        "fallback_ran_count": fb_ran,
        "fallback_pass_count": fb_pass,
        "matched_ratio_min": min(mr_vals) if mr_vals else None,
        "max_abs_max": max(ma_vals) if ma_vals else None,
    }
    return {
        "batch": batch,
        "fail_count": fail_count,
        "first_fail_index": first_fail,
        "worst_index": worst_i,
        "worst": worst_v,
        "diagnostics": diagnostics,
        "numeric": "PASS" if fail_count == 0 else "FAIL",
        "formal": "PENDING_RULING",
        "flags": [],
        "error": first_error,
    }


def _judge_batched_prepared(case_arrays, dut_out, jobs):
    if not isinstance(dut_out, dict):
        return _batched_error_verdict(f"dut_out 必须是 dict，得到 {type(dut_out).__name__}")
    status = dut_out.get("status")
    if status != "ok":
        # 准备失败归 prep 不归目标接口：证据问题，不判精度。
        return _batched_error_verdict(
            f"被测状态非 ok: status={status!r}（prep/接口层失败不归目标，不判精度）")
    out32 = dut_out.get("out32")
    if out32 is None:
        return _batched_error_verdict("dut_out 缺 out32")
    out32 = np.asarray(out32)
    if out32.ndim != 3:
        return _batched_error_verdict(
            f"批量 out32 应为 (batch, n, cols) 三维（batch=1 不得标量化），"
            f"得到 shape={out32.shape}")
    batch = int(out32.shape[0])
    if batch < 1:
        return _batched_error_verdict(f"空批：out32 shape={out32.shape}")

    try:
        info_arr = _check_batched_info(dut_out.get("info"), batch)
    except ValueError as exc:
        return _batched_error_verdict(str(exc), batch)

    declared = case_arrays.get("batch")
    if declared is not None:
        try:
            declared = int(declared)
        except (TypeError, ValueError):
            return _batched_error_verdict(f"case 声明的 batch 不可解析: {declared!r}", batch)
        if declared != batch:
            return _batched_error_verdict(
                f"case 声明 batch={declared} 与被测 out32 首维 {batch} 不一致", batch)

    sliced = {}
    for key in _BATCH_SLICED_KEYS:
        if key in case_arrays:
            arr = np.asarray(case_arrays[key])
            if arr.ndim != 3 or arr.shape[0] != batch:
                return _batched_error_verdict(
                    f"批量 {key} 应为首维 batch={batch} 的三维数组"
                    f"（batch=1 不得标量化），得到 shape={arr.shape}", batch)
            sliced[key] = arr

    ratio_cpu = case_arrays.get("ratio_cpu")
    if ratio_cpu is not None:
        rarr = np.asarray(ratio_cpu, dtype=np.float64)
        if rarr.shape != (batch,):
            return _batched_error_verdict(
                f"批量 ratio_cpu 应为 shape=({batch},) 数组（逐矩阵配对 c_i；"
                f"batch=1 不得标量化），得到 shape={rarr.shape}", batch)
    else:
        rarr = None

    passthrough = {k: v for k, v in case_arrays.items()
                   if k not in _BATCH_SLICED_KEYS and k not in ("ratio_cpu", "batch")}
    _WORK.clear()
    _WORK.update(passthrough=passthrough, sliced=sliced, rarr=rarr,
                 out32=out32, info_arr=info_arr)
    try:
        parts = _parallel_parts(_split_bounds(batch, jobs))
        merged = _merge_partials(parts, batch)
        if len(parts) > 1 and _os.environ.get("VERIFY_BATCHED_SELFCHECK"):
            serial = _merge_partials([_chunk_partial((0, batch))], batch)
            if merged != serial:
                raise AssertionError("并行分块结果与串行不一致（VERIFY_BATCHED_SELFCHECK）")
        return merged
    finally:
        _WORK.clear()


def judge_batched(case_arrays, dut_out, jobs=1):
    """批量数值裁决（本件的批量入口，criteria 批维通路的同语义副本）。

    返回批量 verdict：{batch, fail_count, first_fail_index（全过为 null）,
    worst_index, worst: 最差矩阵的完整单矩阵 verdict, diagnostics: 整批统计
    （仅诊断不入门）, numeric, formal: "PENDING_RULING", flags: 恒空（HT-16）,
    error: 首个逐矩阵 error 带「矩阵 i:」前缀}。
    jobs>1 时按矩阵区间多进程分块，合并结果与串行逐位相同（区间覆盖断言；
    VERIFY_BATCHED_SELFCHECK 非空另跑串行全比对断言）。不外抛异常（fail-closed）。
    """
    try:
        return _judge_batched_prepared(case_arrays, dut_out, int(jobs))
    except Exception as exc:
        return _batched_error_verdict(f"judge 内部异常: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# A0 抽样两层判定（HT-2；criteria/batched_a0 的同语义副本：先一致性后代表槽）
# ---------------------------------------------------------------------------


def _check_consistency(dut_out, sample_map):
    """A0 第 1 层：同内容槽位被测输出逐位比对（参照 = rep_slot 输出，位相等按内容
    传递）。out32 逐槽 tobytes 比对；infoArray（INFO_KIND="array"）逐槽取整比对；
    标量 info 跳过（potrs 族 info 仅报参数错，不逐矩阵）。返回失配清单
    [{"content_idx", "slot", "ref_slot", "field"}]（slot 为全批槽位号），全一致
    返回 []。out32 非三维抛 ValueError。"""
    out32 = np.asarray(dut_out["out32"])
    if out32.ndim != 3:
        raise ValueError(
            f"被测 out32 应为 (batch, n, cols) 三维，得到 shape={out32.shape}")
    info_arr = None
    if INFO_KIND == "array" and dut_out.get("info") is not None:
        info_arr = np.asarray(dut_out["info"])
    bad = []
    for e in sample_map:
        c, ref = int(e["content_idx"]), int(e["rep_slot"])
        for s in e["slots"]:
            s = int(s)
            if s == ref:
                continue
            if out32[s].tobytes() != out32[ref].tobytes():
                bad.append({"content_idx": c, "slot": s,
                            "ref_slot": ref, "field": "out32"})
            if info_arr is not None and int(info_arr[s]) != int(info_arr[ref]):
                bad.append({"content_idx": c, "slot": s,
                            "ref_slot": ref, "field": "info"})
    return bad


def _judge_a0(case_arrays, dut_out, jobs=1):
    """A0 批量条目顶层判定（两层先后，HT-2）：

    1. 余槽 bit-wise 一致性（_check_consistency）：失配 → 数值 FAIL（不是 error
       verdict——被测对同输入出不同输出本身即缺陷，且「代表槽代言全批」的 A0 前提
       被破坏）；槽位号证据落 first_fail_index（最小失配槽位）与
       diagnostics.a0_mismatches，error 恒 None（error 非空 = 证据问题的约定不破）；
    2. 代表槽逐内容判定：按 rep_slot 切出 k 个代表输出，与内容数组配成 batch=k
       子批复用本件 judge_batched（判定机体零改动）。子批 verdict 的序号域是内容下标 0..k-1。ratio_cpu 为 k 值列表，prep_failed 记 None（此处转 NaN；NaN
       基线内容若 layer1 不过，阈值有限性校验抛错 → 该内容 error verdict）。
       case 级 ratio_cpu_mean（HT-4，发包侧固化入 index 条目）非 None 时透传子批。

    结构问题（out32 非三维、batch 声明不符、info 分型不符、缺 sample_map）出
    error verdict（fail_count=None），不得当精度 FAIL 上报。"""
    sample_map = case_arrays.get("sample_map")
    if not sample_map:
        return _batched_error_verdict("A0 条目缺 sample_map（重生成协议不符）")
    if not isinstance(dut_out, dict):
        return _batched_error_verdict(
            f"dut_out 必须是 dict，得到 {type(dut_out).__name__}")
    if dut_out.get("status") != "ok":
        return _batched_error_verdict(
            f"被测状态非 ok: status={dut_out.get('status')!r}"
            "（prep/接口层失败不归目标，不判精度）")
    out32 = dut_out.get("out32")
    if out32 is None:
        return _batched_error_verdict("dut_out 缺 out32")
    out32 = np.asarray(out32)
    if out32.ndim != 3:
        return _batched_error_verdict(
            f"批量卡 out32 应为 (batch, n, cols) 三维（batch=1 不得标量化），"
            f"得到 shape={out32.shape}")
    batch = int(out32.shape[0])
    try:
        _check_batched_info(dut_out.get("info"), batch)
    except ValueError as exc:
        return _batched_error_verdict(str(exc), batch)
    declared = case_arrays.get("batch")
    if declared is not None:
        try:
            declared = int(declared)
        except (TypeError, ValueError):
            return _batched_error_verdict(
                f"case_arrays.batch 不可解析: {declared!r}", batch)
        if declared != batch:
            return _batched_error_verdict(
                f"canonical batch={declared} 与被测 out32 首维 {batch} 不一致", batch)
    try:
        mismatches = _check_consistency(dut_out, sample_map)
    except ValueError as exc:
        return _batched_error_verdict(str(exc), batch)
    if mismatches:
        return {
            "batch": batch,
            "fail_count": len({m["content_idx"] for m in mismatches}),
            "first_fail_index": min(m["slot"] for m in mismatches),
            "worst_index": None,
            "worst": None,
            "diagnostics": {"a0_contents": len(sample_map),
                            "a0_mismatches": mismatches},
            "numeric": "FAIL",
            "formal": "PENDING_RULING",
            "flags": [],
            "error": None,
        }
    k = len(sample_map)
    reps = [int(e["rep_slot"]) for e in sample_map]
    if sorted(reps) != reps or len(set(reps)) != k:
        return _batched_error_verdict(
            f"sample_map 的 rep_slot 应升序且互异，得到 {reps}", batch)
    if min(reps) < 0 or max(reps) >= batch:
        return _batched_error_verdict(
            f"rep_slot {reps} 超出被测批维 {batch}（sample_map 与输出不符）", batch)
    sub_case = {}
    for key in _BATCH_SLICED_KEYS:
        if key in case_arrays:
            arr = np.asarray(case_arrays[key])
            if arr.ndim != 3 or arr.shape[0] != k:
                return _batched_error_verdict(
                    f"内容数组 {key} 首维应为 k={k}，得到 shape={arr.shape}", batch)
            sub_case[key] = arr
    sub_case["uplo"] = case_arrays.get("uplo")
    sub_case["batch"] = k
    ratio_list = case_arrays.get("ratio_cpu") or []
    sub_case["ratio_cpu"] = np.asarray(
        [np.nan if v is None else float(v) for v in ratio_list], dtype=np.float64)
    if sub_case["ratio_cpu"].shape != (k,):
        return _batched_error_verdict(
            f"ratio_cpu 应为 k={k} 值列表（逐内容），得到 "
            f"shape={sub_case['ratio_cpu'].shape}", batch)
    sub_case["ratio_cpu_status"] = case_arrays.get("ratio_cpu_status")
    mean = case_arrays.get("ratio_cpu_mean")   # HT-4：case 级固化 mean 透传子批
    if mean is not None:                       # （potrf/potrs 阈值第二支；缺走单支兼容）
        sub_case["ratio_cpu_mean"] = float(mean)
    sub_dut = {"out32": out32[reps], "status": dut_out.get("status")}
    if INFO_KIND == "array":
        sub_dut["info"] = np.asarray(dut_out["info"])[reps]
    else:
        sub_dut["info"] = dut_out.get("info")
    return judge_batched(sub_case, sub_dut, jobs=jobs)


def _judge_a0_info(case_arrays, dut_out):
    """HT-9 批量 info 契约判定（criteria/batched_a0._judge_a0_info_inner 的同语义
    副本，case_purpose=="info" 条目专用）：只比 info，不进一致性/残差两层（非正定
    内容的 out32 无意义；info 契约与数值精度各出独立结论）。

    - INFO_KIND="array"（potrfBatched 族）：k_expected 为逐内容 k 值列表（正定内容
      记 0），经 sample_map 展开到全批槽位逐槽核对——infoArray 逐矩阵独立写入的
      契约即全批语义；任一槽失配 → 数值 FAIL（fail_count=失配内容数，
      first_fail_index=最小失配槽位，diagnostics.info_mismatches 逐槽证据）；
    - INFO_KIND="scalar"（potrsBatched 族）：k_expected 为标量（-1，仅参数错），
      直接比对。

    结构问题（dut 非 dict/status 非 ok/out32 非三维/info 分型不符/batch 声明不符/
    缺 sample_map/k_expected 形态不符）出 error verdict（fail-closed）。"""
    if not isinstance(dut_out, dict):
        return _batched_error_verdict(f"dut_out 必须是 dict，得到 {type(dut_out).__name__}")
    if dut_out.get("status") != "ok":
        return _batched_error_verdict(
            f"被测状态非 ok: {dut_out.get('status')!r}（prep/接口层失败不归目标）")
    out32 = dut_out.get("out32")
    if out32 is None:
        return _batched_error_verdict("dut_out 缺 out32")
    out32 = np.asarray(out32)
    if out32.ndim != 3:
        return _batched_error_verdict(
            f"批量卡 out32 应为 (batch, n, cols) 三维（batch=1 不得标量化），"
            f"得到 shape={out32.shape}")
    batch = int(out32.shape[0])
    try:  # 分型预检（shape/dtype/标量）；标量非 0 放行——info 契约条目本身要核对
        _check_batched_info(dut_out.get("info"), batch, allow_nonzero=True)
    except ValueError as exc:
        return _batched_error_verdict(str(exc), batch)
    declared = case_arrays.get("batch")
    if declared is not None:
        try:
            declared = int(declared)
        except (TypeError, ValueError):
            return _batched_error_verdict(f"case_arrays.batch 不可解析: {declared!r}", batch)
        if declared != batch:
            return _batched_error_verdict(
                f"canonical batch={declared} 与被测 out32 首维 {batch} 不一致", batch)
    sample_map = case_arrays.get("sample_map")
    if not sample_map:
        return _batched_error_verdict("A0 条目缺 sample_map（重生成协议不符）", batch)

    def _verdict(numeric, fail_count, first_fail, diagnostics):
        return {
            "batch": batch,
            "fail_count": fail_count,
            "first_fail_index": first_fail,
            "worst_index": None,
            "worst": None,
            "diagnostics": diagnostics,
            "numeric": numeric,
            "formal": "PENDING_RULING",
            "flags": [],
            "error": None,
        }

    if INFO_KIND == "array":
        k_expected = case_arrays.get("k_expected")
        if not isinstance(k_expected, (list, tuple)):
            return _batched_error_verdict(
                f"info 契约条目 k_expected 应为逐内容 k 值列表，得到 {k_expected!r}",
                batch)
        k = len(sample_map)
        if len(k_expected) != k:
            return _batched_error_verdict(
                f"k_expected 应为 k={k} 值列表（逐代表内容），得到 {len(k_expected)} 值",
                batch)
        try:
            k_exp = [int(x) for x in k_expected]
        except (TypeError, ValueError):
            return _batched_error_verdict(f"k_expected 含不可取整值: {k_expected!r}", batch)
        info_arr = np.asarray(dut_out.get("info"))
        mismatches = []
        for e in sample_map:
            exp_c = k_exp[int(e["content_idx"])]
            for s in e["slots"]:
                actual = int(info_arr[int(s)])
                if actual != exp_c:
                    mismatches.append({"slot": int(s),
                                       "content_idx": int(e["content_idx"]),
                                       "expected": exp_c, "actual": actual})
        if mismatches:
            return _verdict(
                "FAIL", len({m["content_idx"] for m in mismatches}),
                min(m["slot"] for m in mismatches),
                {"info_contract": True, "a0_contents": k,
                 "info_mismatches": mismatches})
        return _verdict(
            "PASS", 0, None,
            {"info_contract": True, "a0_contents": k,
             "non_posdef_contents": [c for c, kk in enumerate(k_exp) if kk > 0]})
    k_expected = case_arrays.get("k_expected")
    try:
        k_expected = int(k_expected)
    except (TypeError, ValueError):
        return _batched_error_verdict(
            f"info 契约条目 k_expected 应为可取整标量（-1），得到 {k_expected!r}", batch)
    actual = int(dut_out.get("info"))
    ok = actual == k_expected
    return _verdict(
        "PASS" if ok else "FAIL", 0 if ok else 1, None if ok else 0,
        {"info_contract": True, "expected": k_expected, "actual": actual})


# ---------------------------------------------------------------------------
# 先造数后测：包内 gen_data 现场重生成 + 逐内容参考 ratio（A0 抽样，低精度准备链）
# ---------------------------------------------------------------------------


class _PrepFailed(RuntimeError):
    """低精度准备链失败（info≠0 或输出非有限）——该内容参考 ratio 不可用。"""


def _load_gen_module(pkg_dir):
    """导入包内 gen_data.py（先造数后测：包内无数组，判定时现场重生成）。"""
    path = pkg_dir / "gen_data.py"
    if not path.is_file():
        raise RuntimeError(
            f"包内缺 gen_data.py（批量包为纯脚本形态，判定依赖现场重生成）: {path}")
    spec = _importlib_util.spec_from_file_location("_pkg_gen_data", path)
    mod = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("validate_case", "build_batched_contents", "derive_sample_map",
                 "build_batched_info_arrays", "derive_batched_info_cases"):
        if not hasattr(mod, name):
            raise RuntimeError(f"包内 gen_data.py 缺 {name}（与本件的 A0 重生成协议不符）")
    return mod


def _prep_lapack():
    """scipy 是运行前置：参考 ratio 的低精度准备链走 scipy.linalg.lapack。"""
    try:
        from scipy.linalg import lapack
    except ImportError as exc:
        raise RuntimeError(
            "缺 scipy：逐内容参考 ratio 的低精度准备链需要 scipy.linalg.lapack") from exc
    return lapack


def _matrix_ratio(lapack, case_arrays, uplo, i):
    """第 i 个内容矩阵的参考 ratio：低精度完整准备链（__PREP_PREFIX__potrf，求解类再
    __PREP_PREFIX__potrs）后用本件残差实现配对计算——逐内容 c_i，不做 max 聚合，
    与验收侧回填口径同法。准备失败抛 _PrepFailed。"""
    a32 = np.ascontiguousarray(case_arrays["A32"][i])
    f32, info = getattr(lapack, PREP_PREFIX + "potrf")(a32, lower=(uplo == "L"), clean=1)
    if info != 0:
        raise _PrepFailed(f"内容 {i}: {PREP_PREFIX}potrf info={info}")
    if f32.dtype != _PREP_DT32 or not np.isfinite(f32).all():
        raise _PrepFailed(f"内容 {i}: F32 dtype/有限性不符（{f32.dtype}）")
    if BASE_OP.endswith("potrf"):
        return float(residual_ratio(a=case_arrays["A64"][i], factor=f32, uplo=uplo))
    b32 = np.ascontiguousarray(case_arrays["B32"][i])
    x32, info = getattr(lapack, PREP_PREFIX + "potrs")(f32, b32, lower=(uplo == "L"))
    if info != 0:
        raise _PrepFailed(f"内容 {i}: {PREP_PREFIX}potrs info={info}")
    if x32.dtype != _PREP_DT32 or not np.isfinite(x32).all():
        raise _PrepFailed(f"内容 {i}: X32 dtype/有限性不符（{x32.dtype}）")
    return float(residual_ratio(a=a32, b=b32, x=x32))


def _materialize_case(gen_mod, lapack, entry):
    """现场重生成一个 case 的 A0 抽样产物（HT-2）：k=min(5,batch) 个代表内容数组
    （gen_data.build_batched_contents，内容流从 case seed 派生）+ 槽位映射
    （gen_data.derive_sample_map，摆放/填充子流同源派生）+ 逐内容参考 ratio。

    返回 (case_arrays, note)：case_arrays 含批维 k 的内容数组、canonical batch
    声明与 sample_map，ratio_cpu 为 k 值列表（prep_failed 记 None，全失败才记
    prep_failed 状态，与验收侧回填口径一致；本包输入为构造性 SPD/HPD，实际不应
    触发），note 携带失败明细。"""
    gen_mod.validate_case(entry)
    contents = gen_mod.build_batched_contents(entry)
    sample_map = gen_mod.derive_sample_map(entry["seed"], entry["batch"])
    case_arrays = dict(contents)
    for key, v in entry.items():
        case_arrays.setdefault(key, v)
    case_arrays["sample_map"] = sample_map
    uplo = normalize_uplo(entry["uplo"])
    ratios, note = [], None
    for i in range(len(sample_map)):
        try:
            ratios.append(_matrix_ratio(lapack, case_arrays, uplo, i))
        except _PrepFailed as exc:
            ratios.append(None)
            note = str(exc)
    case_arrays["ratio_cpu"] = ratios
    case_arrays["ratio_cpu_status"] = ("ok" if any(v is not None for v in ratios)
                                       else "prep_failed")
    return case_arrays, note'''

# 批量精度副本的 CLI 段（替换单矩阵 CLI：目标 case 取 canonical 清单，输入现场重生成）。
_BATCHED_CLI_TEMPLATE = r'''

# ---------------------------------------------------------------------------
# CLI：读包（canonical 清单 + gen_data 现场重生成）与被测输出，逐 case 出数值判定
# ---------------------------------------------------------------------------


def _scalar(value):
    """npz 0 维标量 → python 标量；bytes → str。"""
    if isinstance(value, np.ndarray):
        value = value.item() if value.ndim == 0 else value
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, np.generic):
        value = value.item()
    return value


def _load_dut_out(dut_dir, case_id):
    """装载被测输出（out32/info/status）。info 形状感知：数组保数组、0 维转 int，
    分型合法性由 _check_batched_info 裁（infoArray 型对 .item() 强转会误伤）。"""
    path = dut_dir / f"{case_id}.npz"
    if not path.is_file():
        return None
    with np.load(path) as z:
        found = {k: np.array(z[k]) for k in z.files}
    info = found.get("info")
    if info is not None:
        info = np.asarray(info)
        info = int(info.item()) if info.ndim == 0 else info
    status = found.get("status")
    return {"out32": found.get("out32"),
            "info": info,
            "status": str(_scalar(status)) if status is not None else None}


def _ratio_summary(ratio_cpu):
    """逐内容参考 ratio（k 值列表）的摘要：None（prep_failed）不入统计，
    vals 空时只报 count/total。仅作参考展示，不入任何门。"""
    if ratio_cpu is None:
        return None
    vals = [float(x) for x in ratio_cpu if x is not None]
    out = {"count": len(vals), "total": len(ratio_cpu)}
    if vals:
        r = np.asarray(vals, dtype=np.float64)
        out["min"] = float(r.min())
        out["median"] = float(np.median(r))
        out["max"] = float(r.max())
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=f"{OP} 精度自测辅助件（批量渲染副本，先造数后测）")
    ap.add_argument("--package", required=True,
                    help="任务包目录（含 canonical_cases.json 与 gen_data.py）")
    ap.add_argument("--dut-out", required=True, help="被测输出目录（<case_id>.npz）")
    ap.add_argument("--report", help="逐 case 判定 JSON 报告输出路径")
    ap.add_argument("--case-id", action="append", help="只跑指定 case（可重复）")
    ap.add_argument("--jobs", type=int, default=1,
                    help="逐矩阵判定的多进程分块数（缺省 1=串行；结果与串行逐位相同）")
    args = ap.parse_args(argv)

    pkg_dir = Path(args.package)
    dut_dir = Path(args.dut_out)
    canonical_path = pkg_dir / "canonical_cases.json"
    try:
        with open(canonical_path, "r", encoding="utf-8") as fh:
            canonical = json.load(fh)
        entries = [c for c in canonical["cases"] if c.get("op") == OP]
    except Exception as exc:
        print(f"[错误] 读不了 {canonical_path}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    if not entries:
        print(f"[错误] canonical 中没有 {OP} 的 case", file=sys.stderr)
        return 2
    try:
        gen_mod = _load_gen_module(pkg_dir)
        lapack = _prep_lapack()
    except Exception as exc:
        print(f"[错误] 现场重生成前置不可用: {exc}", file=sys.stderr)
        return 2
    # HT-9：追加派生批量 info 契约用例（与 gen 侧 derive_batched_info_cases 同一
    # 选择规则同一随机流；canonical 切片只含精度用例，info 用例由包内 gen_data
    # 现场派生）。--case-id 命中 info 用例 id 时同样可单跑。
    by_id = {e["case_id"]: e for e in canonical["cases"]}
    info_entries = [e for e in gen_mod.derive_batched_info_cases(canonical["cases"])
                    if e.get("op") == OP]
    if args.case_id:
        wanted = list(dict.fromkeys(args.case_id))
        lookup = dict(by_id)
        lookup.update({e["case_id"]: e for e in info_entries})
        missing = [c for c in wanted if c not in lookup]
        if missing:
            print(f"[错误] canonical 中找不到 case: {missing}", file=sys.stderr)
            return 2
        entries = [lookup[c] for c in wanted]
    else:
        entries = entries + info_entries
    if not entries:
        print(f"[错误] canonical 中没有 {OP} 的 case", file=sys.stderr)
        return 2
    n_info_run = sum(1 for e in entries if e.get("case_purpose") == "info")
    # HT-4：case 级 ratio_cpu_mean（Σ(count_j·ratio_j)/batchSize 槽位加权，发包侧
    # 固化）从包内 index 条目按 case_id 读——判定只读、零重算；index 缺失或条目无
    # 该键时注入 None → potrf/potrs 阈值第二支走缺 mean 单支兼容口径。
    try:
        with open(pkg_dir / "cases" / "index.json", "r", encoding="utf-8") as fh:
            mean_by_id = {e.get("case_id"): e.get("ratio_cpu_mean")
                          for e in (json.load(fh).get("cases") or [])}
    except OSError:
        mean_by_id = {}

    print(f"# verify_accuracy — {OP}（criteria {CRITERIA_VER} / renderer {RENDERER_VER}）")
    print(f"# {DISCLAIMER}")
    print("# 先造数后测（A0 抽样，HT-2）：本包不携带数组，判定输入、golden 与逐内容"
          "参考 ratio 均现场重生成（不读取 data 目录）；判定先余槽 bit-wise 一致性、"
          "后 rep_slot 逐内容三层")
    results = []
    n_pass = n_fail = n_noev = 0
    for entry in entries:
        case_id = entry["case_id"]
        is_info = entry.get("case_purpose") == "info"
        try:
            if is_info:
                # HT-9：info 契约条目不产 golden/ratio——按 base case 现场构造
                # 混合输入（非正定内容构造性自检 fail-closed），judge 只比 info。
                arrays = gen_mod.build_batched_info_arrays(entry)
                smap = gen_mod.derive_sample_map(entry["seed"], entry["batch"])
                case_arrays = dict(arrays)
                case_arrays.update(entry)
                case_arrays["sample_map"] = smap
                prep_note = None
            else:
                arrays, prep_note = _materialize_case(gen_mod, lapack, entry)
                arrays["ratio_cpu_mean"] = mean_by_id.get(case_id)  # HT-4：只读固化 mean
        except Exception as exc:
            n_noev += 1
            results.append({"case_id": case_id, "status": "证据不足",
                            "note": f"现场重生成失败: {type(exc).__name__}: {exc}"})
            print(f"{case_id}  证据不足（现场重生成失败）")
            continue
        dut = _load_dut_out(dut_dir, case_id)
        if dut is None:
            n_noev += 1
            results.append({"case_id": case_id, "status": "证据不足",
                            "note": f"缺被测输出 {case_id}.npz"})
            print(f"{case_id}  证据不足（缺被测输出）")
            continue
        if is_info:
            v = _judge_a0_info(case_arrays, dut)
        else:
            v = _judge_a0(arrays, dut, jobs=args.jobs)
        row = {"case_id": case_id, "status": "数值判定", "verdict": v}
        if is_info:
            row.update({"mode": "info", "case_purpose": "info",
                        "info_probe": entry.get("info_probe"),
                        "k_expected": entry.get("k_expected"),
                        "batch": entry.get("batch")})
        else:
            row.update({"mode": "a0",
                        "sampled_contents": len(arrays.get("sample_map") or []),
                        "canonical_batch": entry.get("batch"),
                        "ratio_cpu_summary": _ratio_summary(arrays.get("ratio_cpu")),
                        "ratio_cpu_status": arrays.get("ratio_cpu_status")})
        if prep_note:
            row["prep_note"] = prep_note
        results.append(row)
        if v["numeric"] == "PASS":
            n_pass += 1
        else:
            n_fail += 1
        flags = ",".join(v["flags"]) if v["flags"] else "-"
        err = "-" if v["error"] is None else v["error"]
        if is_info:
            # HT-9：info 契约项单独展示比对结果（不展示残差类字段）
            diag = v.get("diagnostics") or {}
            if INFO_KIND == "array":
                print(f"{case_id}  [info契约] numeric={v['numeric']}  batch={v['batch']}  "
                      f"fail_count={v['fail_count']}  first_fail={v['first_fail_index']}")
            else:
                print(f"{case_id}  [info契约] numeric={v['numeric']}  "
                      f"expected={diag.get('expected')} actual={diag.get('actual')}")
        else:
            print(f"{case_id}  numeric={v['numeric']}  batch={v['batch']}  "
                  f"fail_count={v['fail_count']}  first_fail={v['first_fail_index']}  "
                  f"worst={v['worst_index']}  flags={flags}  error={err}")

    total = len(entries)
    print(f"# 合计 {total}（精度 {total - n_info_run} + info 契约 {n_info_run}）："
          f"数值 PASS {n_pass} / 数值 FAIL {n_fail} / 证据不足 {n_noev}")
    print("# formal 恒 PENDING_RULING：本输出不构成验收结论；批量数值裁决 = 逐矩阵"
          "完整三层判定的聚合，数值结论升正式（HT-16）")

    if args.report:
        report = {
            "tool": {"name": "verify_accuracy.py", "op": OP, "base_op": BASE_OP,
                     "info_kind": INFO_KIND, "criteria_ver": CRITERIA_VER,
                     "renderer_ver": RENDERER_VER, "residual_kind": RESIDUAL_KIND},
            "disclaimer": DISCLAIMER,
            "materialize": "gen+A0（先造数后测，HT-2：包内无数组，判定时现场重生成 "
                           "k=min(5,batch) 个代表内容数组、golden 与逐内容参考 ratio；"
                           "精度条目之外另派生 case_purpose==\"info\" 的批量 info 契约"
                           "用例，只比 infoArray[i]==k_expected[i] 或标量 info==-1，"
                           "HT-9）",
            "params": {
                "layer1_rtol": LAYER1_RTOL_FP32, "layer1_atol": LAYER1_ATOL_FP32,
                "required_matched_ratio": REQUIRED_MATCHED_RATIO,
                "max_abs_fixed": MAX_ABS_FIXED, "ulp_mult": ULP_MULT,
                "eps32": EPS32, "fallback_formula": FALLBACK_FORMULA,
                "jobs": args.jobs,
            },
            "cases": results,
            "summary": {"total": total, "numeric_pass": n_pass,
                        "numeric_fail": n_fail, "no_evidence": n_noev},
        }
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
            fh.write("\n")

    return 0 if (n_fail == 0 and n_noev == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
'''


# ---------------------------------------------------------------------------
# S4 六算子 v2 模板件（s4-D5）：纯脚本精度副本 = 单矩阵模板判定机体 + 现场重生成
# 扩展段 + 纯脚本 CLI。拼装与批量段同一套机械操作并逐处断言（fail-closed）；
# 既有模板字面一字不改，批量拼装来源与输出零漂移。
# ---------------------------------------------------------------------------

# 纯脚本精度副本的头部 docstring（替换单矩阵头部；token 与单矩阵同一套替换）。
_PURESCRIPT_HEAD_DOC = r'''__OP__ 精度自测辅助件（纯脚本包；accept criteria 的渲染副本；由 render_verify.py
生成，勿手改）。

三层判定（criteria/verdict.judge 的同语义副本）：layer1 逐元素标准表单套容差
（rtol = atol = 2^-13，HT-14；通过率门 0.99）加 max_abs 动态上限
max(1e-2, 32·ULP(g_low))（HT-1），基准统一 golden64（统计前按 FP32 RNE 收窄）
→ 不过 → __KIND__ 残差兜底终审（阈值 = __FORMULA__，配对本 case 现场重算的
参考 ratio）。
__OP__ 主判目标：__TARGET_DOC__

先造数后测（本包不携带任何数据数组）：判定时逐 case 调包内 gen_data.py 与
canonical_cases.json 现场重生成输入并计算 golden 与本 case 参考 ratio（同环境逐位
一致），不读取、不依赖开发者自测用的 data 目录。

用法：
    python3 verify_accuracy.py --package <包目录> --dut-out <被测输出目录> \
        [--report <json>] [--case-id <id> ...]

输入契约：包内 canonical_cases.json（本算子切片）与 gen_data.py；被测输出
<dut-out>/<case_id>.npz 含 out32（与重生成的 golden32 同 dtype 同布局）、info
（标量，LAPACK 约定）、status。

身份声明：本件是自测辅助件，输出**不构成验收证据**；数值判定不是正式结论
（formal 恒 PENDING_RULING），正式裁决由验收流程用自带判据独立计算。

退出码：0 = 全部数值 PASS；1 = 存在数值 FAIL 或证据不足；2 = 用法/输入错误。
'''

# 纯脚本扩展段：先造数后测的现场重生成 + 本 case 参考 ratio（低精度准备链 + 本件
# 残差实现，口径与验收侧回填一致）。接在单矩阵判定机体之后（judge 为单矩阵裁决）。
_PURESCRIPT_EXT_TEMPLATE = r'''# ---------------------------------------------------------------------------
# 先造数后测：包内 gen_data 现场重生成 + 本 case 参考 ratio（低精度准备链）
# ---------------------------------------------------------------------------

import importlib.util as _importlib_util

PREP_PREFIX = "__PREP_PREFIX__"  # 参考 ratio 低精度准备链的 LAPACK 前缀（s/c）
_PREP_DT32 = np.complex64 if PREP_PREFIX == "c" else np.float32


class _PrepFailed(RuntimeError):
    """低精度准备链失败（info≠0 或输出非有限）——该 case 参考 ratio 不可用。"""


def _load_gen_module(pkg_dir):
    """导入包内 gen_data.py（先造数后测：包内无数组，判定时现场重生成）。"""
    path = pkg_dir / "gen_data.py"
    if not path.is_file():
        raise RuntimeError(
            f"包内缺 gen_data.py（纯脚本包，判定依赖现场重生成）: {path}")
    spec = _importlib_util.spec_from_file_location("_pkg_gen_data", path)
    mod = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("validate_case", "build_case_arrays"):
        if not hasattr(mod, name):
            raise RuntimeError(f"包内 gen_data.py 缺 {name}（与本件的重生成协议不符）")
    return mod


def _prep_lapack():
    """scipy 是运行前置：参考 ratio 的低精度准备链走 scipy.linalg.lapack。"""
    try:
        from scipy.linalg import lapack
    except ImportError as exc:
        raise RuntimeError(
            "缺 scipy：本 case 参考 ratio 的低精度准备链需要 scipy.linalg.lapack") from exc
    return lapack


def _check_prep(name, arr):
    """低精度例程输出必须仍是该精度且有限（防 scipy 静默升型/降型的坑）。"""
    if arr.dtype != _PREP_DT32:
        raise _PrepFailed(
            f"{name} dtype 不符（{arr.dtype}，应 {np.dtype(_PREP_DT32).name}）")
    if not np.isfinite(arr).all():
        raise _PrepFailed(f"{name} 含 NaN/Inf")


def _case_ratio(lapack, case_arrays, uplo):
    """本 case 的参考 ratio：低精度完整准备链（potrs/potri 先 __PREP_PREFIX__potrf）
    后用本件残差实现同法计算——口径与验收侧回填一致（包内 ratio_cpu 约定）。
    准备失败抛 _PrepFailed。"""
    a32 = np.ascontiguousarray(case_arrays["A32"])
    f32, info = getattr(lapack, PREP_PREFIX + "potrf")(a32, lower=(uplo == "L"), clean=1)
    if info != 0:
        raise _PrepFailed(f"{PREP_PREFIX}potrf info={info}")
    _check_prep("F32", f32)
    if OP.endswith("potrf"):
        return float(residual_ratio(a=case_arrays["A64"], factor=f32, uplo=uplo))
    if OP.endswith("potrs"):
        b32 = np.ascontiguousarray(case_arrays["B32"])
        x32, info = getattr(lapack, PREP_PREFIX + "potrs")(f32, b32, lower=(uplo == "L"))
        if info != 0:
            raise _PrepFailed(f"{PREP_PREFIX}potrs info={info}")
        _check_prep("X32", x32)
        return float(residual_ratio(a=a32, b=b32, x=x32))
    c32, info = getattr(lapack, PREP_PREFIX + "potri")(f32, lower=(uplo == "L"))
    if info != 0:
        raise _PrepFailed(f"{PREP_PREFIX}potri info={info}")
    _check_prep("C32", c32)
    return float(residual_ratio(a=a32, ainv=c32, uplo=uplo))


def _materialize_case(gen_mod, lapack, entry, by_id):
    """现场重生成一个 case：包内 gen_data 构造全部数组 + 本 case 参考 ratio。

    返回 (case_arrays, note)。准备链失败记 prep_failed（该 case 兜底不可用，需兜底
    时走 error 路径；本包输入为构造性 SPD/HPD，实际不应触发），note 携带失败明细。
    HT-8：info 契约用例（case_purpose=="info"）不产 golden/ratio——按 base case
    现场构造（构造性自检 fail-closed），judge 只比 info==k_expected。
    """
    gen_mod.validate_case(entry)
    if entry.get("case_purpose") == "info":
        base = by_id[entry["base_case_id"]]
        arrays = gen_mod.build_info_arrays(entry, gen_mod.build_case_arrays(base))
        case_arrays = dict(arrays)
        for k, v in entry.items():
            case_arrays.setdefault(k, v)
        case_arrays["ratio_cpu"] = None
        case_arrays["ratio_cpu_status"] = "info"
        return case_arrays, None
    arrays = gen_mod.build_case_arrays(entry)
    case_arrays = dict(arrays)
    for k, v in entry.items():
        case_arrays.setdefault(k, v)
    uplo = normalize_uplo(entry["uplo"])
    note = None
    try:
        ratio = _case_ratio(lapack, case_arrays, uplo)
    except _PrepFailed as exc:
        case_arrays["ratio_cpu"] = None
        case_arrays["ratio_cpu_status"] = "prep_failed"
        note = str(exc)
    else:
        case_arrays["ratio_cpu"] = ratio
        case_arrays["ratio_cpu_status"] = "ok"
    return case_arrays, note'''

# 纯脚本精度副本的 CLI 段（替换单矩阵 npz CLI：目标 case 取 canonical 清单，
# 输入现场重生成；被测输出装载与单矩阵语义相同——info 标量）。
_PURESCRIPT_CLI_TEMPLATE = r'''

# ---------------------------------------------------------------------------
# CLI：读包（canonical 清单 + gen_data 现场重生成）与被测输出，逐 case 出数值判定
# ---------------------------------------------------------------------------


def _scalar(value):
    """npz 0 维标量 → python 标量；bytes → str。"""
    if isinstance(value, np.ndarray):
        value = value.item() if value.ndim == 0 else value
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, np.generic):
        value = value.item()
    return value


def _load_dut_out(dut_dir, case_id):
    path = dut_dir / f"{case_id}.npz"
    if not path.is_file():
        return None
    with np.load(path) as z:
        found = {k: np.array(z[k]) for k in z.files}
    info = found.get("info")
    status = found.get("status")
    return {"out32": found.get("out32"),
            "info": _scalar(info) if info is not None else None,
            "status": str(_scalar(status)) if status is not None else None}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=f"{OP} 精度自测辅助件（纯脚本渲染副本，先造数后测）")
    ap.add_argument("--package", required=True,
                    help="任务包目录（含 canonical_cases.json 与 gen_data.py）")
    ap.add_argument("--dut-out", required=True, help="被测输出目录（<case_id>.npz）")
    ap.add_argument("--report", help="逐 case 判定 JSON 报告输出路径")
    ap.add_argument("--case-id", action="append", help="只跑指定 case（可重复）")
    args = ap.parse_args(argv)

    pkg_dir = Path(args.package)
    dut_dir = Path(args.dut_out)
    canonical_path = pkg_dir / "canonical_cases.json"
    try:
        with open(canonical_path, "r", encoding="utf-8") as fh:
            canonical = json.load(fh)
        entries = [c for c in canonical["cases"] if c.get("op") == OP]
    except Exception as exc:
        print(f"[错误] 读不了 {canonical_path}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    try:
        gen_mod = _load_gen_module(pkg_dir)
        lapack = _prep_lapack()
    except Exception as exc:
        print(f"[错误] 现场重生成前置不可用: {exc}", file=sys.stderr)
        return 2
    # HT-8：追加派生 info 契约用例（与 gen 侧 derive_info_cases 同一选择规则同一
    # 随机流；canonical 切片只含精度用例，info 用例由包内 gen_data 现场派生）。
    # --case-id 命中 info 用例 id 时同样可单跑。
    by_id = {e["case_id"]: e for e in canonical["cases"]}
    info_entries = [e for e in gen_mod.derive_info_cases(canonical["cases"])
                    if e.get("op") == OP]
    if args.case_id:
        wanted = list(dict.fromkeys(args.case_id))
        lookup = dict(by_id)
        lookup.update({e["case_id"]: e for e in info_entries})
        missing = [c for c in wanted if c not in lookup]
        if missing:
            print(f"[错误] canonical 中找不到 case: {missing}", file=sys.stderr)
            return 2
        entries = [lookup[c] for c in wanted]
    else:
        entries = entries + info_entries
    if not entries:
        print(f"[错误] canonical 中没有 {OP} 的 case", file=sys.stderr)
        return 2
    n_info_run = sum(1 for e in entries if e.get("case_purpose") == "info")
    # HT-4：算子级 ratio_cpu_mean（全部正定精度用例算术平均，发包侧固化）从包内
    # index 顶层按算子读——判定只读、零重算；index 缺失或无该键时注入 None →
    # potrf/potrs 兜底走缺 mean 单支兼容口径（与 accept_run 注入契约一致）。
    try:
        with open(pkg_dir / "cases" / "index.json", "r", encoding="utf-8") as fh:
            op_mean = (json.load(fh).get("ratio_cpu_mean") or {}).get(OP)
    except OSError:
        op_mean = None

    print(f"# verify_accuracy — {OP}（criteria {CRITERIA_VER} / renderer {RENDERER_VER}）")
    print(f"# {DISCLAIMER}")
    print("# 先造数后测：本包不携带数组，判定输入、golden 与本 case 参考 ratio 均"
          "现场重生成（不读取 data 目录）")
    results = []
    n_pass = n_fail = n_noev = 0
    for entry in entries:
        case_id = entry["case_id"]
        try:
            arrays, prep_note = _materialize_case(gen_mod, lapack, entry, by_id)
            arrays["ratio_cpu_mean"] = op_mean    # HT-4：只读固化 mean（阈值第二支）
        except Exception as exc:
            n_noev += 1
            results.append({"case_id": case_id, "status": "证据不足",
                            "note": f"现场重生成失败: {type(exc).__name__}: {exc}"})
            print(f"{case_id}  证据不足（现场重生成失败）")
            continue
        dut = _load_dut_out(dut_dir, case_id)
        if dut is None:
            n_noev += 1
            results.append({"case_id": case_id, "status": "证据不足",
                            "note": f"缺被测输出 {case_id}.npz"})
            print(f"{case_id}  证据不足（缺被测输出）")
            continue
        v = judge(arrays, dut)
        row = {"case_id": case_id, "status": "数值判定", "verdict": v,
               "ratio_cpu": arrays.get("ratio_cpu"),
               "ratio_cpu_status": arrays.get("ratio_cpu_status")}
        if entry.get("case_purpose") == "info":
            row["case_purpose"] = "info"
        if prep_note:
            row["prep_note"] = prep_note
        results.append(row)
        if v["numeric"] == "PASS":
            n_pass += 1
        else:
            n_fail += 1
        flags = ",".join(v["flags"]) if v["flags"] else "-"
        err = "-" if v["error"] is None else v["error"]
        if entry.get("case_purpose") == "info":
            # HT-8：info 契约项单独展示比对结果（不展示残差类字段）
            inf = v.get("info") or {}
            print(f"{case_id}  [info契约] numeric={v['numeric']}  "
                  f"expected={inf.get('expected')} actual={inf.get('actual')}")
        else:
            print(f"{case_id}  numeric={v['numeric']}  flags={flags}  error={err}")

    total = len(entries)
    print(f"# 合计 {total}（精度 {total - n_info_run} + info 契约 {n_info_run}）："
          f"数值 PASS {n_pass} / 数值 FAIL {n_fail} / 证据不足 {n_noev}")
    print("# formal 恒 PENDING_RULING：本输出不构成验收结论")

    if args.report:
        report = {
            "tool": {"name": "verify_accuracy.py", "op": OP,
                     "criteria_ver": CRITERIA_VER, "renderer_ver": RENDERER_VER,
                     "residual_kind": RESIDUAL_KIND},
            "disclaimer": DISCLAIMER,
            "materialize": "gen（先造数后测：包内无数组，判定时现场重生成输入、"
                           "golden 与本 case 参考 ratio；精度条目之外另派生 "
                           "case_purpose==\"info\" 的 info 契约用例，只比 "
                           "info==k_expected，HT-8）",
            "params": {
                "layer1_rtol": LAYER1_RTOL_FP32, "layer1_atol": LAYER1_ATOL_FP32,
                "required_matched_ratio": REQUIRED_MATCHED_RATIO,
                "max_abs_fixed": MAX_ABS_FIXED, "ulp_mult": ULP_MULT,
                "eps32": EPS32, "fallback_formula": FALLBACK_FORMULA,
            },
            "cases": results,
            "summary": {"total": total, "numeric_pass": n_pass,
                        "numeric_fail": n_fail, "no_evidence": n_noev},
        }
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
            fh.write("\n")

    return 0 if (n_fail == 0 and n_noev == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
'''


_OP_SPECS = {
    "spotrf": {
        "prefix": "s",
        "kind": "DPOT01",
        "formula": thresholds.POTRF_POTRS_FORMULA,
        "threshold_fn": thresholds.potrf_potrs_threshold,
        "target_doc": "被测因子 F vs golden64 直审——存储侧半三角逐元素比对"
                      "（HT-7 对调）；诊断并报还原 recon（L·Lᵀ/Uᵀ·U）取指定三角"
                      "对原 A（A64）。",
        "block": _SPOTRF_BLOCK,
    },
    "spotrs": {
        "prefix": "s",
        "kind": "DPOT02",
        "formula": thresholds.POTRF_POTRS_FORMULA,
        "threshold_fn": thresholds.potrf_potrs_threshold,
        "target_doc": "解矩阵 X 全量逐元素 vs golden64。",
        "block": _SPOTRS_BLOCK,
    },
    "spotri": {
        "prefix": "s",
        "kind": "DPOT03",
        "formula": thresholds.POTRI_FORMULA,
        "threshold_fn": thresholds.potri_threshold,
        "target_doc": "单目标（HT-7 收单）：A⁻¹ 直审，存储侧半三角 vs golden64。",
        "block": _SPOTRI_BLOCK,
    },
    # S2c 复数三算子（S2 spec §4）：kind、阈值公式与数值同实数书；复数语义
    # （复模/共轭/拆实虚）在各自 block 与复数公共件内；T7 已摘、复数裁决升正式。
    "cpotrf": {
        "prefix": "c",
        "kind": "DPOT01",
        "formula": thresholds.POTRF_POTRS_FORMULA,
        "threshold_fn": thresholds.potrf_potrs_threshold,
        "target_doc": "被测因子 F vs golden64 直审，实/虚各自成目标、双侧同时达标"
                      "（S2 spec §4）；诊断并报还原 recon（L·Lᴴ/Uᴴ·U）（拆实/虚）。",
        "block": _CPOTRF_BLOCK,
    },
    "cpotrs": {
        "prefix": "c",
        "kind": "DPOT02",
        "formula": thresholds.POTRF_POTRS_FORMULA,
        "threshold_fn": thresholds.potrf_potrs_threshold,
        "target_doc": "解矩阵 X 全量逐元素 vs golden64，实/虚各自成目标、双侧同时"
                      "达标（S2 spec §4）。",
        "block": _CPOTRS_BLOCK,
    },
    "cpotri": {
        "prefix": "c",
        "kind": "DPOT03",
        "formula": thresholds.POTRI_FORMULA,
        "threshold_fn": thresholds.potri_threshold,
        "target_doc": "单目标（HT-7 收单）：A⁻¹ 直审（存储侧 vs golden64），实/虚"
                      "拆两目标、双侧同时达标（S2 spec §4）。",
        "block": _CPOTRI_BLOCK,
    },
}

_LEFTOVER_TOKEN = re.compile(r"__[A-Z][A-Z_]*__")

# S3 批量四算子（S3 spec §4/§5）：批量精度副本 = 单矩阵模板判定机体 + 批量扩展段。
# 判据成员（kind/公式/阈值/基算子块）取基算子的 _OP_SPECS 条目；此处只登记批维身份
#（info 分型、准备链前缀）与批量措辞，零新增判据实现。
_BATCHED_SPECS = {
    "spotrfBatched": {
        "base": "spotrf", "info_kind": "array", "prefix": "s",
        "info_role_doc": "infoArray 型（potrfBatched 族）",
        "target_doc": "逐矩阵被测因子 F_i vs golden64[i] 直审（存储侧半三角，HT-7 "
                      "对调）；诊断并报还原 recon（L·Lᵀ/Uᵀ·U）对该矩阵原 A_i"
                      "（A64[i]）。",
    },
    "spotrsBatched": {
        "base": "spotrs", "info_kind": "scalar", "prefix": "s",
        "info_role_doc": "标量 info 型（potrsBatched 族，官方仅支持 nrhs=1）",
        "target_doc": "逐矩阵解向量 X_i 全量逐元素 vs golden64[i]（nrhs=1）。",
    },
    "cpotrfBatched": {
        "base": "cpotrf", "info_kind": "array", "prefix": "c",
        "info_role_doc": "infoArray 型（potrfBatched 族）",
        "target_doc": "逐矩阵被测因子 F_i vs golden64[i] 直审，实/虚各自成目标、"
                      "双侧同时达标（S2 spec §4）；诊断并报还原 recon（L·Lᴴ/Uᴴ·U）"
                      "（拆实/虚）。",
    },
    "cpotrsBatched": {
        "base": "cpotrs", "info_kind": "scalar", "prefix": "c",
        "info_role_doc": "标量 info 型（potrsBatched 族，官方仅支持 nrhs=1）",
        "target_doc": "逐矩阵解向量 X_i 全量逐元素 vs golden64[i]（nrhs=1），"
                      "实/虚各自成目标、双侧同时达标（S2 spec §4）。",
    },
}


def _check_op_spec(op, spec):
    """嵌入的卡参数与 cards_cholesky 逐项断言一致；漂移即渲染失败（fail-closed）。"""
    card = cards_cholesky.get_card(op)
    if card.residual_kind != spec["kind"]:
        raise AssertionError(
            f"{op}: 模板 kind {spec['kind']!r} 与卡 {card.residual_kind!r} 漂移")
    if card.fallback_formula != spec["formula"]:
        raise AssertionError(
            f"{op}: 模板公式 {spec['formula']!r} 与卡 {card.fallback_formula!r} 漂移")
    if card.fallback_threshold is not spec["threshold_fn"]:
        raise AssertionError(f"{op}: 模板阈值函数与卡的 fallback_threshold 不同源")


def _check_batched_card(op, bspec, base_spec):
    """批量卡身份与判据成员与 cards_cholesky 逐项断言一致（fail-closed）。"""
    card = cards_cholesky.get_card(op)
    if not getattr(card, "batched", False):
        raise AssertionError(f"{op}: 卡未标记 batched，与批量模板不符")
    if card.base_op != bspec["base"]:
        raise AssertionError(
            f"{op}: 模板基算子 {bspec['base']!r} 与卡 base_op {card.base_op!r} 漂移")
    if card.info_kind != bspec["info_kind"]:
        raise AssertionError(
            f"{op}: 模板 info_kind {bspec['info_kind']!r} 与卡 {card.info_kind!r} 漂移")
    if card.residual_kind != base_spec["kind"]:
        raise AssertionError(
            f"{op}: 模板 kind {base_spec['kind']!r} 与卡 {card.residual_kind!r} 漂移")
    if card.fallback_formula != base_spec["formula"]:
        raise AssertionError(
            f"{op}: 模板公式 {base_spec['formula']!r} 与卡 {card.fallback_formula!r} 漂移")
    if card.fallback_threshold is not base_spec["threshold_fn"]:
        raise AssertionError(f"{op}: 模板阈值函数与卡的 fallback_threshold 不同源")


def _batched_accuracy_template():
    """拼装批量精度模板：单矩阵模板换头部 docstring、切走单矩阵 CLI，接批量扩展段
    与批量 CLI。两处机械操作逐处断言；既有模板字面一字不改（单矩阵渲染零漂移）。"""
    parts = _ACCURACY_TEMPLATE.split(_ACC_CLI_MARK)
    if len(parts) != 2:
        raise AssertionError(
            "单矩阵精度模板缺唯一 CLI 分节标记，批量模板无法复用其判定机体")
    head = parts[0].split('"""', 2)
    if len(head) != 3:
        raise AssertionError("单矩阵精度模板头部 docstring 结构变化，批量换头失败")
    return (head[0] + '"""' + _BATCHED_HEAD_DOC + '"""' + head[2]
            + _BATCHED_EXT_TEMPLATE + _BATCHED_CLI_TEMPLATE)


def _purescript_accuracy_template():
    """拼装纯脚本精度模板（S4 六算子 v2）：单矩阵模板换头部 docstring、切走原
    npz CLI，接现场重生成扩展段与纯脚本 CLI。机械操作与批量拼装同一套并逐处断言；
    既有模板字面一字不改（批量拼装的机体来源不受影响）。"""
    parts = _ACCURACY_TEMPLATE.split(_ACC_CLI_MARK)
    if len(parts) != 2:
        raise AssertionError(
            "单矩阵精度模板缺唯一 CLI 分节标记，纯脚本模板无法复用其判定机体")
    head = parts[0].split('"""', 2)
    if len(head) != 3:
        raise AssertionError("单矩阵精度模板头部 docstring 结构变化，纯脚本换头失败")
    return (head[0] + '"""' + _PURESCRIPT_HEAD_DOC + '"""' + head[2]
            + _PURESCRIPT_EXT_TEMPLATE + _PURESCRIPT_CLI_TEMPLATE)


def _finish_render(fname, text, files):
    """渲染收尾三道自检（单矩阵与批量共用同一道门）：token 残留、去内部指称替换
    （s2-D4 表）、替换后 spec § 泄漏零容忍。"""
    leftover = _LEFTOVER_TOKEN.search(text)
    if leftover:
        raise AssertionError(f"{fname}: 模板残留未替换 token {leftover.group(0)!r}")
    for src, dst in _PKG_LOCAL_SUBS:          # s2-D3 去内部指称（确定性纯替换）
        text = text.replace(src, dst)
    if "spec §" in text:
        raise AssertionError(f"{fname}: 渲染产物仍含内部指称 spec §，替换表需补条目")
    files[fname] = text


def render(op):
    """渲染一个算子的两个副本，返回 {文件名: 文本}；输出是输入的纯函数（确定性）。
    批量四算子走 _render_batched（S3 spec §4/§5：机体复用 + 批量扩展段）；单矩阵
    六算子出纯脚本形态副本（S4 v2：先造数后测，机体复用 + 现场重生成扩展段）。"""
    if op in _BATCHED_SPECS:
        return _render_batched(op)
    spec = _OP_SPECS.get(op)
    if spec is None:
        raise ValueError(
            f"未知算子 {op!r}，只支持 {tuple(_OP_SPECS) + tuple(_BATCHED_SPECS)}"
            "（S1 spec §1 + S2 spec §4 + S3 spec §4）")
    _check_constants()
    _check_op_spec(op, spec)
    subs = [
        ("__CONSTANTS__", _CONSTANTS_BLOCK),
        ("__OP_BLOCK__", spec["block"]),
        ("__TARGET_DOC__", spec["target_doc"]),
        ("__FORMULA__", spec["formula"]),
        ("__KIND__", spec["kind"]),
        ("__CRITERIA_VER__", thresholds.CRITERIA_VER),
        ("__RENDERER_VER__", RENDERER_VER),
        ("__PREP_PREFIX__", spec["prefix"]),
        ("__OP__", op),
    ]
    files = {}
    for fname, template in (("verify_accuracy.py", _purescript_accuracy_template()),
                            ("verify_perf.py", _PERF_TEMPLATE)):
        text = template
        for token, value in subs:
            text = text.replace(token, value)
        _finish_render(fname, text, files)
    return files


def _render_batched(op):
    """批量算子渲染（S3 spec §4/§5）：判据成员取基算子，批维身份与卡逐项核对；
    verify_perf 与单矩阵同模板（逐 case 比值本就族无关），版本号走批量渲染版本。"""
    bspec = _BATCHED_SPECS[op]
    base_spec = _OP_SPECS[bspec["base"]]
    _check_constants()
    _check_op_spec(bspec["base"], base_spec)
    _check_batched_card(op, bspec, base_spec)
    subs = [
        ("__CONSTANTS__", _CONSTANTS_BLOCK),
        ("__OP_BLOCK__", base_spec["block"]),
        ("__TARGET_DOC__", bspec["target_doc"]),
        ("__FORMULA__", base_spec["formula"]),
        ("__KIND__", base_spec["kind"]),
        ("__CRITERIA_VER__", thresholds.CRITERIA_VER),
        ("__RENDERER_VER__", BATCHED_RENDERER_VER),
        ("__INFO_ROLE_DOC__", bspec["info_role_doc"]),
        ("__INFO_KIND__", bspec["info_kind"]),
        ("__BASE_OP__", bspec["base"]),
        ("__PREP_PREFIX__", bspec["prefix"]),
        ("__OP__", op),
    ]
    files = {}
    for fname, template in (("verify_accuracy.py", _batched_accuracy_template()),
                            ("verify_perf.py", _PERF_TEMPLATE)):
        text = template
        for token, value in subs:
            text = text.replace(token, value)
        _finish_render(fname, text, files)
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description="verify 双件确定性渲染入口（spec §2.5）")
    ap.add_argument("--op", required=True,
                    choices=sorted(tuple(_OP_SPECS) + tuple(_BATCHED_SPECS)),
                    help="算子名（canonical 前缀：实数 s / 复数 c，S2 spec §0；"
                         "批量四算子带 Batched 后缀，S3 spec §4）")
    ap.add_argument("--out", required=True,
                    help="输出目录（写 verify_accuracy.py 与 verify_perf.py）")
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for fname, text in sorted(render(args.op).items()):
        data = text.encode("utf-8")
        path = out_dir / fname
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        print(f"渲染 {path}  sha256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
