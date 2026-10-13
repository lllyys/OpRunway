# -*- coding: utf-8 -*-
"""verify 双件确定性渲染入口（D 卡，spec §2.5；设计 v2 §3.3「同一个确定性渲染入口」）。

用法：``render_verify.py --op <算子名> --out <dir>``，算子名为单矩阵六算子
（spotrf/spotrs/spotri/cpotrf/cpotrs/cpotri）或批量四算子（spotrfBatched/
spotrsBatched/cpotrfBatched/cpotrsBatched，S3 spec §4/§5）。
向 <dir> 渲染两个独立可运行的自测辅助件：``verify_accuracy.py``（一段式残差判定的
渲染副本：LAPACK 残差对阈值判）与 ``verify_perf.py``（perf_baseline 逐 case 比值，
方向 = 被测/基线）。

s2-A1 一段式（2026-10-08 用户裁定）：精度副本与 criteria 同步收成单步判定——每个
accuracy 用例直接算 LAPACK 残差（DPOT01/02/03）对阈值判，原 layer1 逐元素混合容差
层（比对目标、双门、收窄、拆实/虚统计）随 criteria 整体拆除，不保留诊断；
golden32/golden64 不再被判定消费（降为自测参考件）。残差基换原始 fp32 输入：
DPOT01 的 a 全族传 A32（README 1.3 的 A64 口径废止），副本的本地参考 ratio 链同基。
复数三算子的残差先升 complex128 再取复模、recon/镜像取共轭（Hermitian），纯虚部
错误与漏共轭由复模与共轭转置天然捕捉；公式形状、ε 与阈值数值同实数书。

三条渲染契约（spec 波 2 D 行）：

- **确定性**：输出只由模板与 criteria 常量决定，无时间戳、无绝对路径、无环境依赖，
  同版本复渲逐字节一致；包内副本漂移由 manifest 指纹监督，仅告警（spec §2.2 指纹分级）。
- **独立可运行**：副本不 import 本 skill 任何模块；判据参数全部嵌入文件内（ε 与
  阈值公式，含 mean 第二支（HT-3）与 potri 绝对线（HT-5））。嵌入常量与卡参数在
  渲染时逐项与 thresholds / cards_cholesky 断言相等，模板与判据源漂移直接渲染失败
  （fail-closed）。
- **副本身份**：自测辅助件，输出不构成验收证据；正式裁决在 accept（设计 v2 §3.3
  裁决主权三规则）。副本的 formal 层恒为 PENDING_RULING，与 criteria 同语义。

模板正文是 criteria/verdict.py 与 criteria/cards_cholesky.py 相应实现的逐句转写
（模块引用改为文件内直引），行为一致性由 D 卡容器验证段对 A 卡正反例逐条核对。

S3 批量增量（S3 spec §4/§5 + 并行裁定）：批量四算子的精度副本由单矩阵模板的
判定机体（常量、公共件、基算子块、单矩阵裁决）加批量扩展段拼装——逐矩阵一段式
残差判定同 criteria 批维通路语义、info 按接口角色分型、flags 恒空（HT-16）；
**A0 抽样 materialize**（HT-2，先造数后测）：包内不携带任何数组，副本判定时调包内
gen_data.py 与 canonical 切片现场重生成 k=min(5,batch) 个代表内容数组（内容/摆放/填充三条流全部从 case seed 派生，不依赖开发者的
data 目录），参考值与 sample_map 只读包内冻结 index；被测全批输出先过余槽 bit-wise 一致性、后按 rep_slot 逐内容判定
（两层先后）。逐矩阵判定支持 --jobs 多进程分块（判定只读共享数组无流耦合，按矩阵
区间切分，合并结果与串行逐位相同，内建区间覆盖断言）。批量走独立版本号
BATCHED_RENDERER_VER，拼装只复用、不改写既有模板字面。

S4 六算子 v2（Mr.0 2026-09-24 裁定）：单矩阵六算子的精度副本为**纯脚本（全量
materialize）形态**——包不携带任何数组，副本判定时调包内 gen_data.py 与 canonical
切片现场重生成输入，参考 ratio/status/mean 只读包内冻结 index。拼装机制与批量段同一套（换头部 docstring、接现场重生成扩展段与纯脚本
CLI），判定机体字面复用不改写。版本戳按算子路由（renderer_ver_for）。
"""

import argparse
import functools
import hashlib
import re
import sys
from pathlib import Path

try:  # criteria 作为包被导入时
    from . import cards_cholesky, thresholds
except ImportError:  # criteria 目录直接挂 sys.path 或作为脚本运行时
    import cards_cholesky
    import thresholds

try:  # LU/QR 16 接口卡（lq-plan §1）：可选依赖，缺失时仅 Cholesky 可用（fail-closed）
    from . import cards_luqr
except ImportError:
    try:
        import cards_luqr
    except ImportError:   # 旧镜像无该模块：LU/QR 渲染入口关闭，Cholesky 路径不受影响
        cards_luqr = None

# 渲染格式版本，供 manifest.renderer_ver（spec §2.2）消费。
# s4-D5：单矩阵六算子改纯脚本（全量 materialize）形态（S4 v2，Mr.0 2026-09-24 裁定）。
# s4-D6：判定机体与六算子块按 criteria s1-A6 全口径重基（2026-09-26）。
# s4-D7：HT-8 info 契约支路；s4-D8：HT-4 ratio_cpu_mean 消费；s4-D9：HT-19 抓漏修复。
# s4-D10：s2-A1 一段式重基（2026-10-08）——layer1 层整体拆除（含双门/收窄/拆实虚），
#   判定机体收成单步残差；golden 不再消费；DPOT01 的 a 与本地参考 ratio 链换 A32 基；
#   verdict schema 改 residual 单段。
# s4-D11：info 派生口径对齐 gen 侧（2026-10-09）——按切片顶层 package_scope 显式传
#   require_s1，不再吃 derive_info_cases 的 s1 默认；修 full 包两侧 case_id 分叉
#   （v4 六包命中：开发者算子全对也因「缺被测输出」退 1）。
# s4-D12：info_require_s1 的 docstring 就地写明 True/False 各取什么条目与缺
#   package_scope 字段的默认（2026-10-09 冷读：口径函数只说「唯一事实源」，
#   读者推不出两种取值的选择面）。只动副本内的说明文字，判定行为不变。
# s4-D13：参考 ratio/status/mean 只读冻结 index；支持 gzip，缺失/旧基拒收。
# s4-D14：LU/QR 16 接口增量（lq-plan §1；四册任务书 §3.2，2026-10-11）——_OP_SPECS
#   扩入 12 单矩阵接口、_BATCHED_SPECS 扩入 4 批量接口；_LUQR_KERNELS 共享内核 +
#   薄块；引擎三个动态钩子（fallback_ref/fallback_formula_of/threshold_of，缺省与
#   Cholesky 逐位等价，gels 方阵/超定双判据经 threshold_of 整体接管）；纯脚本/批量
#   准备链按算子族分流。
RENDERER_VER = "s4-D14"
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
# s3-D8：s2-A1 一段式重基（同 s4-D10：逐矩阵残差单步、diagnostics 收
#   batch/pass/fail/error/ratio_max、A32 基参考 ratio 链）。
# s3-D9：info 派生口径对齐 gen 侧（同 s4-D11，derive_batched_info_cases 支路）。
# s3-D10：info_require_s1 的 docstring 就地写明两种取值的选择面（同 s4-D12）。
# s3-D11：同 s4-D13；批量 case mean 与 sample_map 同源读取冻结 index。
# s3-D12：LU/QR 批量两族增量（同 s4-D14，2026-10-11）——getriBatched/matinvBatched
#   入 _BATCHED_SPECS/_BATCH_BASE_SPECS；_BATCH_SLICED_KEYS 增 LU64/LU32/ipiv；
#   批量准备链按基算子族分流（getrf+getri）。
BATCHED_RENDERER_VER = "s3-D12"


def renderer_ver_for(op):
    """该算子渲染产物的格式版本（manifest.renderer_ver 消费方按算子取值）。"""
    return BATCHED_RENDERER_VER if op in _BATCHED_SPECS else RENDERER_VER


def info_require_s1(canonical):
    """info 契约用例的派生基座口径：切片顶层 package_scope 是唯一事实源。

    返回 True 时派生只取 s1_subset 为 True 的精度条目；返回 False 时取切片内
    全部精度条目。package_scope 为 "full" 才返回 False，其余取值与缺该字段的
    旧切片一律返回 True（旧包按 s1 口径）。

    skill 侧（stream_check 的 info 派生）与渲染副本（下面两份模板里逐字相同的
    那份，供装包自检对账）调同一条规则：口径不同会派生出不同 case_id，被测
    输出就对不上。
    """
    return canonical.get("package_scope") != "full"


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
# s2-A1：layer1 常量（RTOL/ATOL/REQUIRED_MATCHED_RATIO/MAX_ABS_FIXED/ULP_MULT）
# 随混合容差层退役，只余残差 ε。
_CONSTANTS_BLOCK = '''EPS32 = 2.0 ** -24                     # 残差 ε（spec §0：固定 2^-24，README 口径）'''


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


def residual_threshold(ratio_cpu, ratio_cpu_mean):
    """阈值式（DPOT01，HT-3，0924 任务书 §3.2.2.1）：max(5*ratio_cpu, 3*ratio_cpu_mean)。
    第二支 mean 缺席时由裁决机体走单支兼容口径（残差 ≤ 5*ratio_cpu），不进本函数。"""
    r = _check_ratio_cpu(ratio_cpu)
    m = _check_ratio_cpu_mean(ratio_cpu_mean)
    return max(5.0 * r, 3.0 * m)


# HT-3：potrf/potrs 阈值第二支消费 ratio_cpu_mean（裁决机体据此分流缺 mean 兼容口径）。
USES_MEAN = True


def residual_ratio(a, factor, uplo):
    """DPOT01：ratio = ‖recon−A‖₁ / (n·‖A‖₁·ε)，L 侧 recon=L·Lᵀ、U 侧 recon=Uᵀ·U。

    a 传 A32（实现实际输入，内部升 FP64；原 README 1.3 的 A64 口径已废止）；
    因子与差值只用存储侧，范数按 DLANSY 半三角镜像口径。NaN/Inf、shape 错、
    零分母 → 抛异常，不返回数值。
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


def residual_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32"),
        "factor": dut_out["out32"],
        "uplo": normalize_uplo(_get(case_arrays, "uplo")),
    }'''

_SPOTRS_BLOCK = r'''# ---------------------------------------------------------------------------
# spotrs 专属件（criteria/cards_cholesky spotrs 卡 + verdict._dpot02 的渲染副本）
# ---------------------------------------------------------------------------


def residual_threshold(ratio_cpu, ratio_cpu_mean):
    """阈值式（DPOT02，HT-3，0924 任务书 §3.2.2.2）：max(5*ratio_cpu, 3*ratio_cpu_mean)。
    第二支 mean 缺席时由裁决机体走单支兼容口径（残差 ≤ 5*ratio_cpu），不进本函数。"""
    r = _check_ratio_cpu(ratio_cpu)
    m = _check_ratio_cpu_mean(ratio_cpu_mean)
    return max(5.0 * r, 3.0 * m)


# HT-3：potrf/potrs 阈值第二支消费 ratio_cpu_mean（裁决机体据此分流缺 mean 兼容口径）。
USES_MEAN = True


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


def residual_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32"),
        "b": _get(case_arrays, "B32"),
        "x": dut_out["out32"],
    }'''

_SPOTRI_BLOCK = r'''# ---------------------------------------------------------------------------
# spotri 专属件（criteria/cards_cholesky spotri 卡 + verdict._dpot03 的渲染副本）
# ---------------------------------------------------------------------------


def residual_threshold(ratio_cpu):
    """阈值式（DPOT03，HT-5，0924 任务书 §3.2.2.3）：max(5*ratio_cpu, 0.1)。"""
    r = _check_ratio_cpu(ratio_cpu)
    return max(5.0 * r, 0.1)


# HT-3：potri 阈值无 mean 第二支（结构性单支），裁决机体据此走单支式 residual_threshold。
USES_MEAN = False


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


def residual_kwargs(case_arrays, dut_out):
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


def _check_diag_real(name, a):
    """Hermitian 存储不变量校验（S2 spec §4：对角虚部按 0 处理并校验），
    只抓无意错喂；被测输出不做此校验，其偏差由残差如实放大。"""
    if np.any(np.diagonal(a).imag != 0.0):
        raise ValueError(f"{name}: Hermitian 对角虚部非 0（S2 spec §4）")'''

_CPOTRF_BLOCK = _COMPLEX_COMMON + "\n\n\n" + r'''# ---------------------------------------------------------------------------
# cpotrf 专属件（criteria/cards_cholesky cpotrf 卡 + verdict._dpot01 复数通路的渲染副本）
# ---------------------------------------------------------------------------


def residual_threshold(ratio_cpu, ratio_cpu_mean):
    """阈值式（DPOT01，HT-3，数值同实数书）：max(5*ratio_cpu, 3*ratio_cpu_mean)。
    第二支 mean 缺席时由裁决机体走单支兼容口径（残差 ≤ 5*ratio_cpu），不进本函数。"""
    r = _check_ratio_cpu(ratio_cpu)
    m = _check_ratio_cpu_mean(ratio_cpu_mean)
    return max(5.0 * r, 3.0 * m)


# HT-3：potrf/potrs 阈值第二支消费 ratio_cpu_mean（裁决机体据此分流缺 mean 兼容口径）。
USES_MEAN = True


def residual_ratio(a, factor, uplo):
    """DPOT01 复数通路（S2 spec §4）：ratio = ‖recon−A‖₁ / (n·‖A‖₁·ε)，
    L 侧 recon=L·Lᴴ、U 侧 recon=Uᴴ·U，范数与绝对值取复模，先升 complex128。

    a 传 A32（实现实际输入，内部升精度；原 README 1.3 的 A64 口径已废止），
    对角虚部须为 0（Hermitian 存储校验）；因子与差值只用存储侧，范数按半三角
    共轭镜像（DLANHE('1')）口径。NaN/Inf、实数阵、shape 错、零分母 → 抛异常。
    纯虚部错误与漏共轭由复模与共轭转置 recon 天然捕捉。
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


def residual_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32"),
        "factor": dut_out["out32"],
        "uplo": normalize_uplo(_get(case_arrays, "uplo")),
    }'''

_CPOTRS_BLOCK = _COMPLEX_COMMON + "\n\n\n" + r'''# ---------------------------------------------------------------------------
# cpotrs 专属件（criteria/cards_cholesky cpotrs 卡 + verdict._dpot02 复数通路的渲染副本）
# ---------------------------------------------------------------------------


def residual_threshold(ratio_cpu, ratio_cpu_mean):
    """阈值式（DPOT02，HT-3，数值同实数书）：max(5*ratio_cpu, 3*ratio_cpu_mean)。
    第二支 mean 缺席时由裁决机体走单支兼容口径（残差 ≤ 5*ratio_cpu），不进本函数。"""
    r = _check_ratio_cpu(ratio_cpu)
    m = _check_ratio_cpu_mean(ratio_cpu_mean)
    return max(5.0 * r, 3.0 * m)


# HT-3：potrf/potrs 阈值第二支消费 ratio_cpu_mean（裁决机体据此分流缺 mean 兼容口径）。
USES_MEAN = True


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


def residual_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32"),
        "b": _get(case_arrays, "B32"),
        "x": dut_out["out32"],
    }'''

_CPOTRI_BLOCK = _COMPLEX_COMMON + "\n\n\n" + r'''# ---------------------------------------------------------------------------
# cpotri 专属件（criteria/cards_cholesky cpotri 卡 + verdict._dpot03 复数通路的渲染副本）
# ---------------------------------------------------------------------------


def residual_threshold(ratio_cpu):
    """阈值式（DPOT03，HT-5，数值同实数书）：max(5*ratio_cpu, 0.1)。"""
    r = _check_ratio_cpu(ratio_cpu)
    return max(5.0 * r, 0.1)


# HT-3：potri 阈值无 mean 第二支（结构性单支），裁决机体据此走单支式 residual_threshold。
USES_MEAN = False


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


def residual_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A32"),
        "ainv": dut_out["out32"],
        "uplo": normalize_uplo(_get(case_arrays, "uplo")),
    }'''


# ===========================================================================
# LU/QR 16 接口专属块（S1-LUQR，lq-plan §1；四册任务书 §3.2，2026-10-08 定稿）
#
# 与 Cholesky 块的结构差异：LU/QR 的残差核在同一个函数里按 dtype 自分流
# （_resolve_mode；实数 float64 / 复数 complex128），故 16 接口共用一份
# _LUQR_KERNELS 前缀，薄块只给算子专属阈值、主判目标、兜底喂数与可选钩子。
# 引擎新增的可选钩子（缺省与 Cholesky 逐位等价）：
# - fallback_ref(case_arrays)：兜底参考线取值，缺省 ratio_cpu（gels 超定覆写）；
# - fallback_formula_of(case_arrays)：兜底公式串，缺省 FALLBACK_FORMULA；
# - HAS_FALLBACK：False 表示该算子任务书未给残差判据，layer1 不过即 FAIL（1014
#   四册范围内 16 接口均给判据，该开关保留备将来无判据算子）。
# ===========================================================================

_LUQR_KERNELS = r'''# ---------------------------------------------------------------------------
# LU/QR 共享内核（criteria/verdict 的 LU/QR 残差通路 + criteria/cards_luqr 公共件
# 的渲染副本；实/复在核内按 dtype 自分流，16 接口共用这一份）
# ---------------------------------------------------------------------------


def _dtype_class(name, arr):
    """数值 dtype 分类："real"（浮点/整数）或 "complex"；其余抛 TypeError。"""
    dt = np.asarray(arr).dtype
    if np.issubdtype(dt, np.complexfloating):
        return "complex"
    if np.issubdtype(dt, np.floating) or np.issubdtype(dt, np.integer):
        return "real"
    raise TypeError(f"{name}: 非数值 dtype {dt}")


def _resolve_mode(named):
    """一次残差调用的实/复通路裁定（dtype 一致性）：全实数走 float64 通路，
    全复数走 complex128 通路，实/复混搭抛 TypeError（fail-closed）。"""
    kinds = {name: _dtype_class(name, arr) for name, arr in named.items()}
    if len(set(kinds.values())) > 1:
        raise TypeError(f"实/复混搭输入不允许: {kinds}")
    return next(iter(kinds.values()))


def _as_c128(name, arr):
    """复数通路输入阵：先升 complex128 再进任何取模/范数；非 2 维、NaN/Inf 抛错。"""
    a = np.asarray(arr).astype(np.complex128)
    if a.ndim != 2:
        raise ValueError(f"{name}: 期望 2 维数组，得到 shape={a.shape}")
    if not np.isfinite(a).all():
        raise ValueError(f"{name}: 含 NaN/Inf")
    return a


def _norm1(m):
    """矩阵 1-范数 ‖M‖₁ = max_j Σ_i |M_ij|（DLANGE('1') 口径；复数取复模）。"""
    if m.ndim != 2 or m.shape[1] == 0:
        raise ValueError(f"1-范数：期望非空 2 维数组，得到 shape={m.shape}")
    return float(np.max(np.sum(np.abs(m), axis=0)))


def _check_ipiv(ipiv, k, m, name="ipiv"):
    """ipiv 契约校验：长度 min(m,n)=k，1 基、落在 [1,m]（LAPACK 语义）。"""
    p = np.asarray(ipiv).ravel()
    if p.size != k:
        raise ValueError(f"{name}: 长度应为 min(m,n)={k}，得到 {p.size}")
    if not np.issubdtype(p.dtype, np.integer):
        raise TypeError(f"{name}: 应为整数数组，得到 dtype={p.dtype}")
    p = p.astype(np.int64)
    if p.size and (p.min() < 1 or p.max() > m):
        raise ValueError(f"{name}: 取值须在 [1,{m}]（1 基），得到 [{p.min()},{p.max()}]")
    return p


def restore_lu(lu, ipiv):
    """由打包 LU 与 ipiv 重放 L·U 并按 DLASWP(-1) 逆序回放行交换，还原到 A 的行序。

    返回升精度后的 m×n 矩阵（实数 float64 / 复数 complex128）。DGET01 残差与
    getrf 卡 layer1 还原比对共用这一份实现（逆序回放逻辑两处漂移风险收单点）。
    """
    mode = _dtype_class("lu", lu)
    cast = _as_c128 if mode == "complex" else _as_f64
    lu = cast("lu", lu)
    m, n = lu.shape
    k = min(m, n)
    p = _check_ipiv(ipiv, k, m)
    eye = np.eye(m, k, dtype=lu.dtype)
    low = np.tril(lu[:, :k], -1)
    L = low + eye
    U = np.triu(lu[:k, :])
    recon = L @ U
    for i in range(k - 1, -1, -1):
        pi = int(p[i]) - 1
        if pi != i:
            recon[[i, pi], :] = recon[[pi, i], :]
    return recon


def _split(name, actual, golden):
    """唯一解直审目标：实数单目标；复数拆实/虚两个实数目标（不合并稀释）。"""
    a = np.asarray(actual)
    g = np.asarray(golden)
    if _dtype_class(name, a) == "complex" or _dtype_class("golden", g) == "complex":
        a = a.astype(np.complex128)
        g = g.astype(np.complex128)
        return [(f"{name}_re", a.real.ravel(), g.real.ravel()),
                (f"{name}_im", a.imag.ravel(), g.imag.ravel())]
    return [(name, np.asarray(a, dtype=np.float64).ravel(),
             np.asarray(g, dtype=np.float64).ravel())]


def normalize_trans(trans):
    """归一 trans 枚举：只认 "N"/"T"/"C"，bytes 先解码；"C" 与 "T" 在实数链路等价。"""
    if isinstance(trans, bytes):
        trans = trans.decode("ascii", "replace")
    t = str(trans).upper()
    if t not in ("N", "T", "C"):
        raise ValueError(f'trans 只允许 "N"/"T"/"C"，得到 {trans!r}')
    return t


def normalize_side(side):
    """归一 side 枚举：只认 "LEFT"/"RIGHT"（兼容 L/R），统一返回 "L"/"R"。"""
    if isinstance(side, bytes):
        side = side.decode("ascii", "replace")
    s = str(side).upper()
    if s in ("L", "LEFT"):
        return "L"
    if s in ("R", "RIGHT"):
        return "R"
    raise ValueError(f'side 只允许 "LEFT"/"RIGHT"，得到 {side!r}')'''


_GETRF_THIN = r'''# ---------------------------------------------------------------------------
# getrf 专属件（criteria/cards_luqr getrf 卡 + verdict._dget01 的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu):
    """getrf 阈值（SGET01/CGET01）：max(5·ratio_cpu, 地板)，地板实数 0.3、复数 0.5
    （任务书 §3.2.2.1；两册分母口径差，地板分列）。"""
    return max(5.0 * _check_ratio_cpu(ratio_cpu), @@FLOOR@@)


FALLBACK_USES_MEAN = False


def residual_ratio(a, lu, ipiv):
    """DGET01：ratio = ‖L·U − A‖₁ / (n·‖A‖₁·ε)，n 取列数（m×n 矩形）。

    由打包 LU 重放 L·U 再按 ipiv 逆序回放行交换（DLASWP 方向 −1）还原到 A 的行序，
    最后取 ‖L·U − A‖₁；复数通路同式、范数取复模。
    """
    mode = _resolve_mode({"a": a, "lu": lu})
    cast = _as_c128 if mode == "complex" else _as_f64
    a = cast("a", a)
    if np.asarray(lu).shape != a.shape:
        raise ValueError(f"shape 不匹配: a{a.shape} vs lu{np.shape(lu)}")
    n = a.shape[1]
    recon = restore_lu(lu, ipiv)
    anorm = _norm1(a)
    if anorm <= 0.0:
        raise ValueError("零分母：‖A‖₁ = 0")
    return _norm1(recon - a) / (n * anorm * EPS32)


def layer1_targets(case_arrays, dut_out):
    """getrf 主判（因子非唯一）：被测打包 LU + 被测 ipiv → restore_lu 还原 vs 原
    A64 直审（任务书 §3.2.1 条 6）。"""
    lu = np.asarray(dut_out["out32"])
    recon = restore_lu(lu, dut_out["ipiv"])
    a64 = np.asarray(_get(case_arrays, "A64"))
    return _split("lu_recon_vs_A", recon, a64)


def layer1_diagnostics(case_arrays, dut_out):
    """getrf 无诊断项。"""
    return []


def fallback_kwargs(case_arrays, dut_out):
    return {"a": _get(case_arrays, "A64"), "lu": dut_out["out32"],
            "ipiv": dut_out["ipiv"]}'''


_GETRI_THIN = r'''# ---------------------------------------------------------------------------
# getri 专属件（criteria/cards_luqr getri 卡 + verdict._dget03 的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu):
    """getri 阈值（SGET03/CGET03）：max(5·ratio_cpu, 0.05)（任务书 §3.2.2.2）。"""
    return max(5.0 * _check_ratio_cpu(ratio_cpu), 0.05)


FALLBACK_USES_MEAN = False


def residual_ratio(a, ainv):
    """DGET03（getri）：ratio = ‖I − C·A‖₁ / (n·‖A‖₁·‖C‖₁·ε)。

    a 传「输入 LU 因子按 ipiv 还原后的矩阵」（实现实际输入升精度口径，任务书
    §3.2.1.2），ainv 传被测逆 C。复数通路（CGET03）同式、范数取复模。
    """
    mode = _resolve_mode({"a": a, "ainv": ainv})
    cast = _as_c128 if mode == "complex" else _as_f64
    a = cast("a", a)
    c = cast("ainv", ainv)
    n = _square("a", a)
    if c.shape != a.shape:
        raise ValueError(f"shape 不匹配: a{a.shape} vs ainv{c.shape}")
    anorm = _norm1(a)
    cnorm = _norm1(c)
    if anorm <= 0.0 or cnorm <= 0.0:
        raise ValueError("零分母：‖A‖₁ 或 ‖C‖₁ 为 0")
    w = np.eye(n, dtype=a.dtype) - c @ a
    return _norm1(w) / (n * anorm * cnorm * EPS32)


def fallback_kwargs(case_arrays, dut_out):
    """a = 输入 LU32 按输入 ipiv 还原（任务书 §3.2.1.2），ainv = 被测逆。"""
    lu32 = np.asarray(_get(case_arrays, "LU32"))
    ipiv = _get(case_arrays, "ipiv")
    return {"a": restore_lu(lu32, ipiv), "ainv": dut_out["out32"]}'''


_GEQRF_THIN = r'''# ---------------------------------------------------------------------------
# geqrf 专属件（criteria/cards_luqr geqrf 卡 + verdict._dqrt01 的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu):
    """geqrf 阈值（SQRT01/CQRT01）：max(5·ratio_cpu, 0.1)（任务书 §3.2.2.1）。"""
    return max(5.0 * _check_ratio_cpu(ratio_cpu), 0.1)


FALLBACK_USES_MEAN = False


def _orgqr_full(packed, tau, nq, complex_):
    """由被测打包反射子 (packed, tau) 升精度经 CPU orgqr/ungqr 生成 nq×nq 完整
    正交/酉矩阵 Q（SQRT01 口径：列数取行数；升精度使构造不引入额外舍入）。"""
    from scipy.linalg import lapack
    p = np.array(packed, order="F")
    t = np.asarray(tau).ravel()
    k = min(p.shape[1], nq)
    pad = np.zeros((nq, nq), dtype=p.dtype, order="F")
    pad[:, :k] = p[:, :k]
    fn = lapack.zungqr if complex_ else lapack.dorgqr
    q, _work, info = fn(pad, t[:k])
    if info != 0:
        raise ValueError(f"orgqr/ungqr 构造 Q 失败: info={info}")
    return q


def residual_ratio(a, r, q):
    """DQRT01：ratio = ‖R − Qᴴ·A‖₁ / (m·‖A‖₁·ε)，m 取行数。

    r 为 geqrf 打包输出（上三角/梯形即 R）；q 为由 (v, tau) 升精度生成的 m×m
    完整正交（酉）矩阵；复数通路取共轭转置。
    """
    mode = _resolve_mode({"a": a, "r": r, "q": q})
    cast = _as_c128 if mode == "complex" else _as_f64
    a = cast("a", a)
    r = cast("r", r)
    q = cast("q", q)
    m, _n = a.shape
    if r.shape != a.shape:
        raise ValueError(f"shape 不匹配: a{a.shape} vs r{r.shape}")
    if q.shape != (m, m):
        raise ValueError(f"q: 期望 m×m={m}×{m} 完整正交矩阵，得到 shape={q.shape}")
    anorm = _norm1(a)
    if anorm <= 0.0:
        raise ValueError("零分母：‖A‖₁ = 0")
    R = np.triu(r)
    qa = q.conj().T @ a
    return _norm1(R - qa) / (m * anorm * EPS32)


def layer1_targets(case_arrays, dut_out):
    """geqrf 主判（因子非唯一）：由被测 (v, tau) 构造 m×m Q，Q·R vs 原 A64 直审
    （R = triu 被测打包输出）。"""
    packed = np.asarray(dut_out["out32"])
    cx = _dtype_class("out32", packed) == "complex"
    q = _orgqr_full(packed, dut_out["tau"], packed.shape[0], cx)
    recon = q @ np.triu(packed)
    a64 = np.asarray(_get(case_arrays, "A64"))
    return _split("qr_recon_vs_A", recon, a64)


def layer1_diagnostics(case_arrays, dut_out):
    """geqrf 无诊断项。"""
    return []


def fallback_kwargs(case_arrays, dut_out):
    packed = np.asarray(dut_out["out32"])
    cx = _dtype_class("out32", packed) == "complex"
    q = _orgqr_full(packed, dut_out["tau"], packed.shape[0], cx)
    return {"a": _get(case_arrays, "A64"), "r": dut_out["out32"], "q": q}'''


_GQR_THIN = r'''# ---------------------------------------------------------------------------
# *gqr 专属件（criteria/cards_luqr gqr 卡 + verdict._dqrt02 的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu):
    """*gqr 阈值（SQRT02/CQRT02）：max(5·ratio_cpu, 1.5)（任务书 §3.2.2.2）。"""
    return max(5.0 * _check_ratio_cpu(ratio_cpu), 1.5)


FALLBACK_USES_MEAN = False


def residual_ratio(q):
    """DQRT02：ratio = ‖I − Qᴴ·Q‖₁ / (m·ε)，m 取 Q 的行数（不随 I 的维度 n 变）。

    I 为 n 阶单位阵（Qᴴ·Q 为 n×n）；复数通路取共轭转置。
    """
    mode = _resolve_mode({"q": q})
    cast = _as_c128 if mode == "complex" else _as_f64
    q = cast("q", q)
    m, n = q.shape
    w = np.eye(n, dtype=q.dtype) - q.conj().T @ q
    return _norm1(w) / (m * EPS32)


def layer1_targets(case_arrays, dut_out):
    """*gqr 主判（唯一）：输出 Q 全量逐元素 vs golden64 直审。"""
    golden = np.asarray(_get(case_arrays, "golden64"))
    out = np.asarray(dut_out["out32"])
    if out.shape != golden.shape:
        raise ValueError(f"shape 不匹配: out32{out.shape} vs golden64{golden.shape}")
    return _split("out_vs_golden", out, golden)


def layer1_diagnostics(case_arrays, dut_out):
    """*gqr 无诊断项。"""
    return []


def fallback_kwargs(case_arrays, dut_out):
    return {"q": dut_out["out32"]}'''


_MQR_THIN = r'''# ---------------------------------------------------------------------------
# *mqr 专属件（criteria/cards_luqr mqr 卡 + verdict._dqrt03 的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu):
    """*mqr 阈值（SQRT03/CQRT03）：max(5·ratio_cpu, 0.1)（任务书 §3.2.2.3）。"""
    return max(5.0 * _check_ratio_cpu(ratio_cpu), 0.1)


FALLBACK_USES_MEAN = False


def residual_ratio(c, opq, q, side, trans):
    """DQRT03：ratio = ‖op2 − op1‖₁ / (m·‖C‖₁·ε)，m 取 Q 的阶数。

    op2 = 反射子形式结果（被测输出 opq）；op1 = 用显式 Q 直接作矩阵乘：
    trans=N 用 Q、trans=T/C 用 Qᵀ/Qᴴ；side=LEFT 时 op(Q)·C，RIGHT 时 C·op(Q)。
    """
    side = normalize_side(side)
    trans = normalize_trans(trans)
    mode = _resolve_mode({"c": c, "opq": opq, "q": q})
    cast = _as_c128 if mode == "complex" else _as_f64
    c = cast("c", c)
    opq = cast("opq", opq)
    q = cast("q", q)
    m = _square("q", q)
    if opq.shape != c.shape:
        raise ValueError(f"shape 不匹配: opq{opq.shape} vs c{c.shape}")
    if side == "L" and c.shape[0] != m:
        raise ValueError(f"side=LEFT 要求 C 行数 = m={m}，得到 shape={c.shape}")
    if side == "R" and c.shape[1] != m:
        raise ValueError(f"side=RIGHT 要求 C 列数 = m={m}，得到 shape={c.shape}")
    cnorm = _norm1(c)
    if cnorm <= 0.0:
        raise ValueError("零分母：‖C‖₁ = 0")
    qo = q if trans == "N" else q.conj().T
    ref = qo @ c if side == "L" else c @ qo
    return _norm1(opq - ref) / (m * cnorm * EPS32)


def layer1_targets(case_arrays, dut_out):
    """*mqr 主判（唯一）：输出 op(Q)·C 全量逐元素 vs golden64 直审。"""
    golden = np.asarray(_get(case_arrays, "golden64"))
    out = np.asarray(dut_out["out32"])
    if out.shape != golden.shape:
        raise ValueError(f"shape 不匹配: out32{out.shape} vs golden64{golden.shape}")
    return _split("out_vs_golden", out, golden)


def layer1_diagnostics(case_arrays, dut_out):
    """*mqr 无诊断项。"""
    return []


def fallback_kwargs(case_arrays, dut_out):
    return {"c": _get(case_arrays, "C64"), "opq": dut_out["out32"],
            "q": _get(case_arrays, "q64"),
            "side": _get(case_arrays, "side"), "trans": _get(case_arrays, "trans")}'''


# gels 双判据（按 m 形状动态）公共段：方阵 m=n 走 DQRT16、超定 m>n 走 DQRT17。
_GELS_COMMON_THIN = r'''

def _gels_is_square(case_arrays):
    """gels 判据分流：m=n 方阵（DQRT16）↔ m>n 超定（DQRT17）。"""
    m, n = case_arrays.get("m"), case_arrays.get("n")
    if m is None or n is None:
        raise ValueError("gels 卡需要 case_arrays 带 m/n（canonical 字段）")
    return int(m) == int(n)


def residual_ratio(a, b, x):
    """gels 双判据（按 m 动态）：m=n 走 DQRT16、m>n 走 DQRT17。

    - DQRT16（方阵）：max_j ‖B_j − A·X_j‖₁ / (max(m,n)·‖A‖₁·‖X_j‖₁·ε)，逐列取 max；
    - DQRT17（超定，IRESID=1）：‖Rᴴ·A‖₁ / (‖A‖₁·‖B‖₁·max(m,n,nrhs)·ε)，R = B − A·X。
    复数通路取共轭转置、范数取复模。
    """
    mode = _resolve_mode({"a": a, "b": b, "x": x})
    cast = _as_c128 if mode == "complex" else _as_f64
    a = cast("a", a)
    b = cast("b", b)
    x = cast("x", x)
    m, n = a.shape
    if x.shape[0] != n or b.shape != (m, x.shape[1]):
        raise ValueError(
            f"shape 不匹配: a{a.shape}, b{b.shape}, x{x.shape}"
            "（a 为 m×n、b 为 m×nrhs、x 为 n×nrhs）")
    anorm = _norm1(a)
    if anorm <= 0.0:
        raise ValueError("零分母：‖A‖₁ = 0")
    if m == n:
        if b.shape[1] < 1:
            raise ValueError("b: 右端项列数 nrhs ≥ 1")
        base = max(m, n) * anorm * EPS32
        resid = b - a @ x
        ratio = 0.0
        for j in range(b.shape[1]):
            xnorm = float(np.sum(np.abs(x[:, j])))
            if xnorm <= 0.0:
                raise ValueError(f"零分母：x 第 {j} 列 1-范数为 0")
            ratio = max(ratio,
                        float(np.sum(np.abs(resid[:, j]))) / (base * xnorm))
        return ratio
    nrhs = b.shape[1]
    bnorm = _norm1(b)
    if bnorm <= 0.0:
        raise ValueError("零分母：‖B‖₁ = 0")
    resid = b - a @ x
    num = _norm1(resid.conj().T @ a)
    return num / (anorm * bnorm * max(m, n, nrhs) * EPS32)


def layer1_targets(case_arrays, dut_out):
    """gels 主判（解唯一）：最小二乘解 X（n×nrhs）全量逐元素 vs golden64 直审。"""
    golden = np.asarray(_get(case_arrays, "golden64"))
    out = np.asarray(dut_out["out32"])
    if out.shape != golden.shape:
        raise ValueError(f"shape 不匹配: out32{out.shape} vs golden64{golden.shape}")
    return _split("out_vs_golden", out, golden)


def layer1_diagnostics(case_arrays, dut_out):
    """gels 无诊断项。"""
    return []


def fallback_kwargs(case_arrays, dut_out):
    return {"a": _get(case_arrays, "A64"), "b": _get(case_arrays, "B64"),
            "x": dut_out["out32"]}
'''

_GELS_REAL_THIN = r'''# ---------------------------------------------------------------------------
# sgels 专属件（criteria/cards_luqr gels 卡（实）+ verdict._dqrt16/_dqrt17 的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ref_cpu):
    """gels 阈值（SGELS，1014 口径）：方阵（m=n）max(5·ref_cpu, 0.015)、超定
    （m>n）10·ref_cpu。缺省走方阵式（实际阈值经 threshold_of 按形状分流）。"""
    return max(5.0 * _check_ratio_cpu(ref_cpu), 0.015)


FALLBACK_USES_MEAN = False


def fallback_ref(case_arrays):
    """gels 参考线：方阵取 ratio_cpu，超定取 ratio_opt_cpu（双判据相对线随形状取）。"""
    return case_arrays.get(
        "ratio_cpu" if _gels_is_square(case_arrays) else "ratio_opt_cpu")


def fallback_formula_of(case_arrays):
    """gels 公式串：方阵 max(5*ratio_cpu, 0.015) / 超定 10*ratio_opt_cpu（1014 口径）。"""
    return ("max(5*ratio_cpu, 0.015)" if _gels_is_square(case_arrays)
            else "10*ratio_opt_cpu")


def threshold_of(case_arrays, ref):
    """gels 阈值整体接管（按形状分流）：方阵 max(5·ref, 0.015)、超定 10·ref。"""
    if _gels_is_square(case_arrays):
        return max(5.0 * _check_ratio_cpu(ref), 0.015)
    return 10.0 * _check_ratio_cpu(ref)
''' + _GELS_COMMON_THIN

_GELS_COMPLEX_THIN = r'''# ---------------------------------------------------------------------------
# cgels 专属件（criteria/cards_luqr gels 卡（复）+ verdict._dqrt16/_dqrt17 的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ref_cpu):
    """gels 阈值（CGELS，1014 实/复同式）：方阵（m=n）max(5·ref_cpu, 0.015)、超定
    （m>n）10·ref_cpu。缺省走方阵式（实际阈值经 threshold_of 按形状分流）。"""
    return max(5.0 * _check_ratio_cpu(ref_cpu), 0.015)


FALLBACK_USES_MEAN = False


def fallback_ref(case_arrays):
    """gels 参考线：方阵取 ratio_cpu，超定取 ratio_opt_cpu（双判据相对线随形状取）。"""
    return case_arrays.get(
        "ratio_cpu" if _gels_is_square(case_arrays) else "ratio_opt_cpu")


def fallback_formula_of(case_arrays):
    """gels 公式串：方阵 max(5*ratio_cpu, 0.015) / 超定 10*ratio_opt_cpu（1014 实/复同式）。"""
    return ("max(5*ratio_cpu, 0.015)" if _gels_is_square(case_arrays)
            else "10*ratio_opt_cpu")


def threshold_of(case_arrays, ref):
    """gels 阈值整体接管（按形状分流）：方阵 max(5·ref, 0.015)、超定 10·ref。"""
    if _gels_is_square(case_arrays):
        return max(5.0 * _check_ratio_cpu(ref), 0.015)
    return 10.0 * _check_ratio_cpu(ref)
''' + _GELS_COMMON_THIN


_GETRIBATCHED_THIN = r'''# ---------------------------------------------------------------------------
# getriBatched 专属件（criteria/cards_luqr getriBatched 卡 + verdict._dget03 的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu_i):
    """getriBatched 阈值（S/CGET03，逐矩阵）：max(5·ratio_cpu_i, 0.05)
    （任务书 §3.2.2.3）。"""
    return max(5.0 * _check_ratio_cpu(ratio_cpu_i), 0.05)


FALLBACK_USES_MEAN = False


def residual_ratio(a, ainv):
    """DGET03：ratio = ‖I − C·A‖₁ / (n·‖A‖₁·‖C‖₁·ε)。

    getriBatched：a 传「输入 LU 因子按 ipiv 还原后的矩阵」、ainv 传输出逆；
    复数通路同式、范数取复模。
    """
    mode = _resolve_mode({"a": a, "ainv": ainv})
    cast = _as_c128 if mode == "complex" else _as_f64
    a = cast("a", a)
    c = cast("ainv", ainv)
    n = _square("a", a)
    if c.shape != a.shape:
        raise ValueError(f"shape 不匹配: a{a.shape} vs ainv{c.shape}")
    anorm = _norm1(a)
    cnorm = _norm1(c)
    if anorm <= 0.0 or cnorm <= 0.0:
        raise ValueError("零分母：‖A‖₁ 或 ‖C‖₁ 为 0")
    w = np.eye(n, dtype=a.dtype) - c @ a
    return _norm1(w) / (n * anorm * cnorm * EPS32)


def layer1_targets(case_arrays, dut_out):
    """getriBatched 主判（唯一，逐矩阵）：输出逆 vs golden64[i] 全量逐元素直审。"""
    golden = np.asarray(_get(case_arrays, "golden64"))
    out = np.asarray(dut_out["out32"])
    if out.shape != golden.shape:
        raise ValueError(f"shape 不匹配: out32{out.shape} vs golden64{golden.shape}")
    return _split("out_vs_golden", out, golden)


def layer1_diagnostics(case_arrays, dut_out):
    """getriBatched 无诊断项。"""
    return []


def fallback_kwargs(case_arrays, dut_out):
    """a = 输入 LU32 按输入 ipiv 还原（实现实际输入升精度口径），ainv = 被测逆。"""
    lu32 = np.asarray(_get(case_arrays, "LU32"))
    ipiv = _get(case_arrays, "ipiv")
    return {"a": restore_lu(lu32, ipiv), "ainv": dut_out["out32"]}'''


_MATINVBATCHED_THIN = r'''# ---------------------------------------------------------------------------
# matinvBatched 专属件（criteria/cards_luqr matinvBatched 卡 + verdict._dget03 的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu_i):
    """matinvBatched 阈值（S/CGET03，逐矩阵）：max(5·ratio_cpu_i, 0.5)
    （任务书 §3.2.2.2）。"""
    return max(5.0 * _check_ratio_cpu(ratio_cpu_i), 0.5)


FALLBACK_USES_MEAN = False


def residual_ratio(a, ainv):
    """DGET03：ratio = ‖I − C·A‖₁ / (n·‖A‖₁·‖C‖₁·ε)。

    matinvBatched：a 传原始输入可逆矩阵、ainv 传输出逆；复数通路同式、范数取复模。
    """
    mode = _resolve_mode({"a": a, "ainv": ainv})
    cast = _as_c128 if mode == "complex" else _as_f64
    a = cast("a", a)
    c = cast("ainv", ainv)
    n = _square("a", a)
    if c.shape != a.shape:
        raise ValueError(f"shape 不匹配: a{a.shape} vs ainv{c.shape}")
    anorm = _norm1(a)
    cnorm = _norm1(c)
    if anorm <= 0.0 or cnorm <= 0.0:
        raise ValueError("零分母：‖A‖₁ 或 ‖C‖₁ 为 0")
    w = np.eye(n, dtype=a.dtype) - c @ a
    return _norm1(w) / (n * anorm * cnorm * EPS32)


def layer1_targets(case_arrays, dut_out):
    """matinvBatched 主判（唯一，逐矩阵）：输出逆 vs golden64[i] 全量逐元素直审。"""
    golden = np.asarray(_get(case_arrays, "golden64"))
    out = np.asarray(dut_out["out32"])
    if out.shape != golden.shape:
        raise ValueError(f"shape 不匹配: out32{out.shape} vs golden64{golden.shape}")
    return _split("out_vs_golden", out, golden)


def layer1_diagnostics(case_arrays, dut_out):
    """matinvBatched 无诊断项。"""
    return []


def fallback_kwargs(case_arrays, dut_out):
    """a = 原始输入 A32（实现实际输入升精度口径），ainv = 被测逆。"""
    return {"a": _get(case_arrays, "A32"), "ainv": dut_out["out32"]}'''


def _luqr_getrf_block(complex_):
    """getrf 薄块：地板按实/复分列（0.3 / 0.5，任务书 §3.2.2.1）。"""
    return _LUQR_KERNELS + "\n\n\n" + _GETRF_THIN.replace(
        "@@FLOOR@@", "0.5" if complex_ else "0.3")


def _luqr_getri_block():
    """getri 薄块（DGET03，实测输入 LU 因子按 ipiv 还原，阈值 max(5r, 0.05)）。"""
    return _LUQR_KERNELS + "\n\n\n" + _GETRI_THIN


def _luqr_geqrf_block():
    return _LUQR_KERNELS + "\n\n\n" + _GEQRF_THIN


def _luqr_gqr_block():
    return _LUQR_KERNELS + "\n\n\n" + _GQR_THIN


def _luqr_mqr_block():
    return _LUQR_KERNELS + "\n\n\n" + _MQR_THIN


def _luqr_gels_block(complex_):
    return _LUQR_KERNELS + "\n\n\n" + (
        _GELS_COMPLEX_THIN if complex_ else _GELS_REAL_THIN)


def _luqr_getribatched_block():
    return _LUQR_KERNELS + "\n\n\n" + _GETRIBATCHED_THIN


def _luqr_matinvbatched_block():
    return _LUQR_KERNELS + "\n\n\n" + _MATINVBATCHED_THIN


# --8<-- ACCURACY-TEMPLATE-BEGIN
# 单矩阵判定机体模板：头部 docstring 是占位（渲染时被纯脚本/批量头替换），正文为
# 常量、公共件、__OP_BLOCK__ 与一段式裁决；末尾 CLI 分节标记供拼装切分。
_ACCURACY_TEMPLATE = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""__OP__ 精度自测辅助件（accept criteria 的渲染副本；由 render_verify.py 生成，勿手改）。
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
FORMULA = "__FORMULA__"
FALLBACK_FORMULA = "__FORMULA__"
DISCLAIMER = "自测辅助件：输出不构成验收证据；正式裁决在 accept（设计 v2 §3.3）"

# ---- 判据参数（任务书 §3.2.2 契约；渲染时已与 criteria/thresholds.py 逐项核对）----
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


def _square(name, a):
    if a.shape[0] != a.shape[1] or a.shape[0] == 0:
        raise ValueError(f"{name}: 期望非空方阵，得到 shape={a.shape}")
    return a.shape[0]


def _half(a, uplo):
    """取存储侧半三角（含对角），另侧置 0。"""
    return np.tril(a) if uplo == "L" else np.triu(a)


def _mirror_half(half):
    """半三角镜像成全阵（输入必须是另侧已置 0 的半三角，含对角）。

    实数镜像成对称阵；复数按 Hermitian 共轭镜像、对角虚部按 0 处理
    （S2 spec §4）。实数路径与原对称实现逐位等价（conj/.real 对实数恒等）。
    """
    return half + half.conj().T - np.diag(np.diag(half).real)


def _sym_norm1_from_half(half):
    """DLANSY('1') 口径的 1-范数：半三角镜像后取最大列绝对和。

    复数输入即 DLANHE('1') 口径（Hermitian 共轭镜像，绝对值取复模）。"""
    full = _mirror_half(half)
    return float(np.max(np.sum(np.abs(full), axis=0)))


def _get(case_arrays, key):
    if key not in case_arrays:
        raise KeyError(f"case_arrays 缺 {key}（{OP} 卡需要，spec §2.2）")
    return case_arrays[key]


__OP_BLOCK__


# ---------------------------------------------------------------------------
# 钩子名归一（合并口径，lq-plan §1 / accept-1010 冻结基线）：Cholesky 六算子块沿用
# USES_MEAN/residual_threshold/residual_kwargs，LU/QR 十六接口块用 FALLBACK_USES_MEAN/
# fallback_threshold/fallback_kwargs。机体统一以 Cholesky/accept-1010 命名为准，缺前者
# 时取后者（两族块的语义一一对应，取值逐位不变）；冻结 index 与 accepts 测试读 USES_MEAN。
# ---------------------------------------------------------------------------
USES_MEAN = globals().get("USES_MEAN", globals().get("FALLBACK_USES_MEAN", False))
if "residual_threshold" not in globals() and "fallback_threshold" in globals():
    residual_threshold = globals()["fallback_threshold"]
if "residual_kwargs" not in globals() and "fallback_kwargs" in globals():
    residual_kwargs = globals()["fallback_kwargs"]


# ---------------------------------------------------------------------------
# 数值裁决（criteria/verdict.judge 的同语义副本；s2-A1 一段式：残差对阈值单步判定，
# 原 layer1 混合容差层已拆除，golden 不参与判定）
# ---------------------------------------------------------------------------


def _null_residual():
    # 残差未运行时 ran=False 其余字段 null。eps 例外，仍披露口径值：它是残差 ratio 的
    # 归一基准（ε=2^-24=SLAMCH('E') 口径），不是运行结果，缺省更易误读。
    return {"ran": False, "ratio": None, "threshold": None, "formula": None,
            "ratio_cpu": None, "ratio_cpu_mean": None, "pass": None, "eps": "2^-24"}


def _error_verdict(msg):
    # error 非空且 residual.ran=False 时 numeric 恒 FAIL（fail-closed）；上层依此区分
    # 「证据不足/环境错」与真精度失败，不得把这种 FAIL 直接当精度结论上报。
    return {
        "residual": _null_residual(),
        "numeric": "FAIL",
        "formal": "PENDING_RULING",
        "flags": [],
        "error": str(msg),
    }


def _fallback_ref(case_arrays):
    """相对线基准取值（可选钩子）：op 块定义 fallback_ref 时覆写（如 gels 超定取
    ratio_opt_cpu）；缺省 ratio_cpu（Cholesky 与各单支算子语义，逐位不变）。"""
    fn = globals().get("fallback_ref")
    if fn is not None:
        return fn(case_arrays)
    return case_arrays.get("ratio_cpu")


def _fallback_formula(case_arrays):
    """公式展示串（可选钩子）：op 块定义 fallback_formula_of 时覆写（如 gels 方阵/
    超定两式）；缺省 FALLBACK_FORMULA（逐位不变）。"""
    fn = globals().get("fallback_formula_of")
    if fn is not None:
        return fn(case_arrays)
    return FALLBACK_FORMULA


def _threshold_of(case_arrays, ref):
    """阈值整体接管（可选钩子）：op 块定义 threshold_of 时覆写（gels 方阵
    max(5·ref, 0.015) / 超定 10·ref 按形状分流，1014 口径）；缺省返回 None，引擎
    走 USES_MEAN 单支/两支式（Cholesky 与各单支算子逐位不变）。"""
    fn = globals().get("threshold_of")
    if fn is not None:
        return fn(case_arrays, ref)
    return None


def _run_residual(case_arrays, dut_out):
    """一段式残差判定（s2-A1；LU/QR 增量 lq-plan §1，1014 口径）：算 LAPACK 残差、
    对阈值判。返回 (residual dict, numeric, error)。

    阈值消费面（HT-3）：uses_mean 算子按两支式 max(5·ratio_cpu, 3·ratio_cpu_mean)
    裁决，ratio_cpu_mean 从 case_arrays 读。缺 mean 的兼容口径（fail-closed 方向）：
    残差 ≤ 单支 5·ratio_cpu → PASS（证据注明单支）；超出 → 证据不足不判 FAIL
    （两支公式只可证一支），待含 mean 的包复判。

    LU/QR 动态钩子（缺省与 Cholesky 逐位等价）：相对线基准经 _fallback_ref 取
    （gels 超定取 ratio_opt_cpu，缺省 ratio_cpu）；公式串经 _fallback_formula；阈值
    经 _threshold_of 整体接管（gels 方阵/超定分流），缺省走 USES_MEAN。"""
    status = case_arrays.get("ratio_cpu_status")
    ratio_cpu = case_arrays.get("ratio_cpu")
    ref = _fallback_ref(case_arrays)
    if status != "ok" or ref is None:
        detail = f"ratio_cpu_status={status!r}, ratio_cpu={ratio_cpu!r}"
        if ref is not ratio_cpu:
            detail += f", ref={ref!r}"
        return (
            _null_residual(),
            "FAIL",
            f"残差基线不可用: {detail}"
            "（spec §2.4：准备失败该 case 不可裁）",
        )
    formula = _fallback_formula(case_arrays)
    mean = case_arrays.get("ratio_cpu_mean")
    threshold = _threshold_of(case_arrays, ref)
    if threshold is None:
        if not USES_MEAN:
            threshold = float(residual_threshold(ref))
        elif mean is not None:
            threshold = float(residual_threshold(ratio_cpu, mean))
        else:
            threshold = None    # 缺 mean：残差算出后按单支兼容路径裁决
    base = {"ran": True, "ratio_cpu": float(ref),
            "ratio_cpu_mean": None if mean is None else float(mean), "eps": "2^-24"}
    try:
        ratio = residual_ratio(**residual_kwargs(case_arrays, dut_out))
    except Exception as exc:  # 残差算不出（NaN/Inf 传染、零分母等）→ 数值 FAIL，指认原因
        res = {**base, "ratio": None, "threshold": threshold,
               "formula": formula, "pass": False}
        return res, "FAIL", f"残差不可计算: {type(exc).__name__}: {exc}"
    if threshold is None:
        # 缺 mean 兼容（HT-3）：单支从严、过即 PASS；超单支 → 证据不足。
        line = 5.0 * _check_ratio_cpu(ratio_cpu)   # 单支线 5·ratio_cpu
        if ratio <= line:
            res = {**base, "ratio": float(ratio), "threshold": line,
                   "formula": "5*ratio_cpu（缺 ratio_cpu_mean 单支，v2 包兼容口径）",
                   "pass": True}
            return res, "PASS", None
        res = {**base, "ratio": float(ratio), "threshold": None,
               "formula": formula, "pass": None}
        return res, "FAIL", (
            "残差证据不足: 缺 ratio_cpu_mean，"
            f"{formula} 第二支不可算；残差 {ratio:.6g} 超单支 "
            f"5·ratio_cpu={line:.6g}，不判 FAIL，待含 mean 的包复判")
    ok = bool(ratio <= threshold)
    res = {**base, "ratio": float(ratio), "threshold": threshold,
           "formula": formula, "pass": ok}
    return res, ("PASS" if ok else "FAIL"), None




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

    residual, numeric, error = _run_residual(case_arrays, dut_out)
    return {"residual": residual, "numeric": numeric,
            "formal": "PENDING_RULING", "flags": [], "error": error}


def _judge_info_inner(case_arrays, dut_out):
    """HT-8 info 契约支路（case_purpose=="info" 的用例专用）：只比 info，
    不进残差（B2 卡口径：非正定/奇异用例分解中途失败，残差无意义；info 契约
    与数值精度各出独立结论，HT-12）。

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

    一段式（s2-A1）：ratio = residual_ratio(实际输入…)，ratio ≤ 阈值 → PASS，否则
    FAIL；残差不可计算（NaN/Inf 传染、零分母）→ 数值 FAIL、ratio=null、error 指认。
    judge 不外抛异常：任何内部异常收敛为 error 字段 + numeric FAIL（fail-closed）；
    error 非空且 residual.ran=False 的 FAIL 属「不可裁/证据问题」，不得当精度 FAIL
    上报。case_purpose=="info" 的用例分流到 _judge_info_inner（HT-8：只比 info，
    verdict 带 "info" 键、无 residual）。
    """
    try:
        if case_arrays.get("case_purpose") == "info":
            return _judge_info_inner(case_arrays, dut_out)
        return _judge_inner(case_arrays, dut_out)
    except Exception as exc:
        return _error_verdict(f"judge 内部异常: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# CLI：读包与被测输出，逐 case 出数值判定
# ---------------------------------------------------------------------------'''
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
# 拼装 = 换头部 docstring + 切走 CLI 分节标记 + 接扩展段与对应 CLI；机械操作逐处
# 断言，fail-closed）。s2-A1：批量段与纯脚本段同步一段式（residual 单段 schema、
# diagnostics 收 ratio_max、参考 ratio 链 A32 基）。
# ---------------------------------------------------------------------------

# 单矩阵精度模板的 CLI 分节标记：拼装时以它切走模板尾部，替换为批量/纯脚本 CLI。
_ACC_CLI_MARK = """# ---------------------------------------------------------------------------
# CLI：读包与被测输出，逐 case 出数值判定
# ---------------------------------------------------------------------------"""

# 批量精度副本的头部 docstring（替换单矩阵头部；token 与单矩阵同一套替换）。
_BATCHED_HEAD_DOC = r'''__OP__ 精度自测辅助件（批量算子；accept criteria 批维通路的渲染副本；由
render_verify.py 生成，勿手改）。

批量判定语义（s2-A1 一段式）：逐矩阵直接算 __KIND__ 残差对阈值判——阈值 =
__FORMULA__，配对该矩阵自己的 c_i（逐矩阵参考 ratio），不做 max 聚合；
ratio_cpu_mean 为 case 级固化值（HT-4），批内共享。case 数值结论 = 全部矩阵通过
（HT-16 升正式）；整批统计只入诊断。golden 不参与判定（降为自测参考件）。
info 按接口角色分型（任务书接口说明第 69 行）：potrfBatched 族被测 info 为 int32
shape=(batch,) infoArray（batch=1 不得标量化）；potrsBatched 族 info 为标量、仅报
参数错，正定性由前置分解的 infoArray 反映，准备失败不归目标接口。本件为
__INFO_ROLE_DOC__。

先造数后测（本包不携带任何数据数组）：判定时逐 case 调包内 gen_data.py 与
canonical_cases.json 现场重生成 k=min(5,batch) 个代表内容数组；逐内容参考
ratio/status、case mean 与 sample_map 只读 cases/index.json（或 .gz），
不读取、不依赖开发者自测用的 data 目录。

A0 抽样判定（HT-2）两层先后：先余槽 bit-wise 一致性——同一内容的全部槽位，被测
out32 与 rep_slot 输出逐位相等、infoArray 逐槽相等（标量 info 不逐槽），失配即数值
FAIL 并报槽位号（证据落 first_fail_index 与 diagnostics.a0_mismatches）；后代表槽
逐内容判定——按 rep_slot 切出 k 个代表输出与 k 个内容数组配成 batch=k 子批走上述
逐矩阵残差判定，子批 verdict 的序号域是内容下标 0..k-1（first_fail_index/
worst_index 同），不是全批槽位号。逐内容参考 ratio 为 k 值列表，准备失败的内容记
null（该内容走「不可裁」error 语义，不判精度失败）。

用法：
    python3 verify_accuracy.py --package <包目录> --dut-out <被测输出目录> \
        [--report <json>] [--case-id <id> ...] [--jobs N]

--jobs N（缺省 1）把逐矩阵判定按矩阵区间切成至多 N 段多进程并行；判定只读共享
数组、无流耦合，分段结果按矩阵序合并，与串行逐位相同（内建区间覆盖断言；环境
变量 VERIFY_BATCHED_SELFCHECK 非空时另跑串行全比对断言）。

输入契约：包内 canonical_cases.json（本算子切片）与 gen_data.py；被测输出
<dut-out>/<case_id>.npz 含 out32（(batch, n, cols)，与重生成输入同 dtype）、
info（分型见上）、status。

身份声明：本件是自测辅助件，输出**不构成验收证据**；数值判定不是正式结论
（formal 恒 PENDING_RULING），正式裁决由验收流程用自带判据独立计算。

退出码：0 = 全部数值 PASS；1 = 存在数值 FAIL 或证据不足；2 = 用法/输入错误。
'''

# 批量扩展段：criteria 批维裁决（verdict 批量通路）的转写 + A0 抽样两层判定
# （先余槽 bit-wise 一致性后 rep_slot 逐内容，HT-2）+ 先造数后测的现场重生成，
# 附并行裁定的矩阵区间多进程分块。接在单矩阵判定机体之后（judge 为单矩阵裁决）。
_BATCHED_EXT_TEMPLATE = r'''# ---------------------------------------------------------------------------
# 批量通路（criteria 批维裁决的渲染副本：逐矩阵一段式残差判定，整批统计只入诊断）
# ---------------------------------------------------------------------------

import importlib.util as _importlib_util
import multiprocessing as _mp
import os as _os

INFO_KIND = "__INFO_KIND__"     # info 接口角色："array"=infoArray | "scalar"=标量
BASE_OP = "__BASE_OP__"         # 单矩阵基算子（判据成员来源）
PREP_PREFIX = "__PREP_PREFIX__"  # 参考 ratio 低精度准备链的 LAPACK 前缀（s/c）
FAMILY = "__FAMILY__"           # "chol"=Cholesky 批量四算子 | "luqr"=LU/QR 批量两族
_PREP_DT32 = np.complex64 if PREP_PREFIX == "c" else np.float32

# 批量 case 里按首维 batch 切片的数组键；其余键原样透传给逐矩阵裁决。
# LU/QR 批量两族另带 LU64/LU32（打包因子，3 维）与 ipiv（主元，2 维）。
_BATCH_SLICED_KEYS = ("A64", "A32", "B64", "B32", "golden64", "golden32",
                      "LU64", "LU32", "ipiv")
_TWO_D_SLICED_KEYS = ("ipiv",)  # 逐矩阵切片后降 1 维的键（其余须 3 维）
# flags 恒空（HT-16：T8 暂定聚合标记摘除，批量数值结论升正式）。
# fork 子进程通过模块全局读共享数组（写时复制，不经序列化传大数组）。
_WORK = {}


def _sliced_ndim(key):
    """该切片键样例应有的维数：ipiv 为 (batch, n) 二维，其余为首维 batch 的三维。"""
    return 2 if key in _TWO_D_SLICED_KEYS else 3


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
    FAIL、再 error 非空、再残差 ratio 大；ratio 不可得（error 矩阵或残差不可计算）
    按 +inf 参与排序。同差取序号小者（调用方用严格大于替换）。仅供诊断指认，
    不参与任何门。"""
    r = v["residual"]["ratio"]
    return (1 if v["numeric"] == "FAIL" else 0,
            1 if v["error"] is not None else 0,
            float("inf") if r is None else float(r))


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
    耦合）。逐矩阵语义与单矩阵 judge 完全相同：一段式残差判定，配对该矩阵自己的
    c_i。"""
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
            "error_count": 0, "first_error": None, "ratio_max": None,
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
        r_i = v_i["residual"]["ratio"]
        if r_i is not None:
            part["ratio_max"] = (r_i if part["ratio_max"] is None
                                 else max(part["ratio_max"], r_i))
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
    first_fail = next((p["first_fail"] for p in parts if p["first_fail"] is not None), None)
    first_error = next((p["first_error"] for p in parts if p["first_error"] is not None), None)
    ratio_vals = [p["ratio_max"] for p in parts if p["ratio_max"] is not None]
    worst_i = worst_v = worst_sev = None
    for p in parts:
        if p["worst_sev"] is not None and (worst_sev is None or p["worst_sev"] > worst_sev):
            worst_i, worst_v, worst_sev = p["worst_i"], p["worst_v"], p["worst_sev"]
    # 整批统计只入诊断：以下都是诊断口径，不作任何门；case 数值结论只由
    # 「全部矩阵通过」决定。ratio_max 是整批可算残差的最大值。
    diagnostics = {
        "batch": batch,
        "pass_count": batch - fail_count,
        "fail_count": fail_count,
        "error_count": error_count,
        "ratio_max": max(ratio_vals) if ratio_vals else None,
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
            if arr.ndim != _sliced_ndim(key) or arr.shape[0] != batch:
                return _batched_error_verdict(
                    f"批量 {key} 应为首维 batch={batch} 的 {_sliced_ndim(key)} 维数组"
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
    （仅诊断不入门：batch/pass_count/fail_count/error_count/ratio_max）, numeric,
    formal: "PENDING_RULING", flags: 恒空（HT-16）, error: 首个逐矩阵 error 带
    「矩阵 i:」前缀}。
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
       子批复用本件 judge_batched（判定机体零改动）。子批 verdict 的序号域是内容
       下标 0..k-1。ratio_cpu 为 k 值列表，prep_failed 记 None（此处转 NaN；
       NaN 基线内容必进阈值公式，由有限性校验抛错 → 该内容 error verdict，
       不判精度）。case 级 ratio_cpu_mean（HT-4，发包侧固化入 index 条目）非
       None 时透传子批。

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
            if arr.ndim != _sliced_ndim(key) or arr.shape[0] != k:
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
# 输入现场构造；逐内容参考值只读冻结 index
# ---------------------------------------------------------------------------


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


def _luqr_batched_ratio(lapack, case_arrays, i):
    """LU/QR 批量两族的第 i 个内容参考 ratio（低精度完整准备链 + DGET03 配对）。

    - getriBatched：输入已是打包 LU32 + ipiv，CPU getri 出低精度逆；a 取
      restore_lu(LU32[i], ipiv[i])（实现实际输入升精度口径，与卡 fallback_kwargs 同源）。
    - matinvBatched：输入原始 A32，CPU getrf→getri 出低精度逆；a 取 A32[i]。
    准备失败（info≠0 或输出非有限）抛 _PrepFailed。复数走 c 前缀同构。
    """
    if BASE_OP.endswith("getribatched"):
        lu32 = np.ascontiguousarray(case_arrays["LU32"][i])
        ipiv = np.asarray(case_arrays["ipiv"][i])
        inv32, info = getattr(lapack, PREP_PREFIX + "getri")(
            lu32, np.asarray(ipiv, dtype=np.int32) - 1)
        if info != 0:
            raise _PrepFailed(f"内容 {i}: {PREP_PREFIX}getri info={info}")
        a_ref = restore_lu(case_arrays["LU32"][i], ipiv)
    else:
        a32 = np.ascontiguousarray(case_arrays["A32"][i])
        lu, piv, info = getattr(lapack, PREP_PREFIX + "getrf")(a32)
        if info != 0:
            raise _PrepFailed(f"内容 {i}: {PREP_PREFIX}getrf info={info}")
        inv32, info2 = getattr(lapack, PREP_PREFIX + "getri")(lu, piv)
        if info2 != 0:
            raise _PrepFailed(f"内容 {i}: {PREP_PREFIX}getri info={info2}")
        a_ref = a32
    ainv = np.ascontiguousarray(inv32)
    if ainv.dtype != _PREP_DT32 or not np.isfinite(ainv).all():
        raise _PrepFailed(f"内容 {i}: 逆 dtype/有限性不符（{ainv.dtype}）")
    return float(residual_ratio(a=a_ref, ainv=ainv))


def _materialize_case(gen_mod, entry, lapack=None):
    """现场重生成一个 case 的 A0 抽样产物（HT-2）：k=min(5,batch) 个代表内容数组
    （gen_data.build_batched_contents，内容流从 case seed 派生）+ 槽位映射
    （gen_data.derive_sample_map，摆放/填充子流同源派生）。

    返回 (case_arrays, note)：case_arrays 含批维 k 的内容数组、canonical batch
    声明与 sample_map。Cholesky 批量族参考 ratio 只读包内冻结 index（accept-1010
    口径，lapack 缺省 None）；LU/QR 批量两族（getriBatched/matinvBatched）按准备链
    （需 lapack=scipy）现场算逐内容 ratio_cpu 列表（prep_failed 记 None，全失败才记
    prep_failed 状态，与验收侧回填口径一致；本包输入为构造性可逆矩阵，实际不应
    触发），note 携带失败明细。"""
    gen_mod.validate_case(entry)
    contents = gen_mod.build_batched_contents(entry)
    sample_map = gen_mod.derive_sample_map(entry["seed"], entry["batch"])
    case_arrays = dict(contents)
    for key, v in entry.items():
        case_arrays.setdefault(key, v)
    case_arrays["sample_map"] = sample_map
    if FAMILY != "luqr":
        return case_arrays, None
    ratios, note = [], None
    for i in range(len(sample_map)):
        try:
            ratios.append(_luqr_batched_ratio(lapack, case_arrays, i))
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


def info_require_s1(canonical):
    """info 契约用例的派生基座口径：切片顶层 package_scope 是唯一事实源，判定与
    包内 gen_data 的 --select 逐字一致。

    返回 True 时派生只取 s1_subset 为 True 的精度条目；返回 False 时取切片内
    全部精度条目。package_scope 为 "full" 才返回 False，其余取值与缺该字段的
    旧切片一律返回 True（旧包按 s1 口径）。

    两侧口径不同会派生出不同 case_id，被测输出就对不上——装包自检调本函数
    对账，不另写一份规则。"""
    return canonical.get("package_scope") != "full"


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
        # LU/QR 批量两族的参考 ratio 按准备链现场算，需 scipy；Cholesky 批量族
        # 只读冻结 index，不加载 scipy（accept-1010 口径）。
        lapack = _prep_lapack() if FAMILY == "luqr" else None
    except Exception as exc:
        print(f"[错误] 现场重生成前置不可用: {exc}", file=sys.stderr)
        return 2
    # HT-9：追加派生批量 info 契约用例（与 gen 侧 derive_batched_info_cases 同一
    # 选择规则同一随机流；canonical 切片只含精度用例，info 用例由包内 gen_data
    # 现场派生）。--case-id 命中 info 用例 id 时同样可单跑。
    by_id = {e["case_id"]: e for e in canonical["cases"]}
    info_entries = [e for e in gen_mod.derive_batched_info_cases(
                        canonical["cases"], require_s1=info_require_s1(canonical))
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
    try:
        frozen_by_id = _load_frozen_index(pkg_dir, entries, batched=True)
    except (OSError, EOFError, ValueError, TypeError) as exc:
        print(f"[错误] 冻结基线契约错误: {exc}", file=sys.stderr)
        return 2

    print(f"# verify_accuracy — {OP}（criteria {CRITERIA_VER} / renderer {RENDERER_VER}）")
    print(f"# {DISCLAIMER}")
    print("# 先造数后测（A0 抽样，HT-2）：本包不携带数组，判定输入现场重生成，逐内容参考 "
          "ratio/status/mean/sample_map 只读冻结 index（不读取 data 目录）；判定先余槽 bit-wise 一致性、"
          "后 rep_slot 逐内容一段式残差判定")
    results = []
    n_pass = n_fail = n_noev = 0
    for entry in entries:
        case_id = entry["case_id"]
        is_info = entry.get("case_purpose") == "info"
        try:
            if is_info:
                # HT-9：info 契约条目不产 ratio——按 base case 现场构造混合输入
                # （非正定内容构造性自检 fail-closed），judge 只比 info。
                arrays = gen_mod.build_batched_info_arrays(entry)
                smap = gen_mod.derive_sample_map(entry["seed"], entry["batch"])
                case_arrays = dict(arrays)
                case_arrays.update(entry)
                case_arrays["sample_map"] = smap
                prep_note = None
            else:
                arrays, prep_note = _materialize_case(gen_mod, entry, lapack)
            _apply_frozen(case_arrays if is_info else arrays, frozen_by_id[case_id])
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
          "一段式残差判定的聚合，数值结论升正式（HT-16）")

    if args.report:
        report = {
            "tool": {"name": "verify_accuracy.py", "op": OP, "base_op": BASE_OP,
                     "info_kind": INFO_KIND, "criteria_ver": CRITERIA_VER,
                     "renderer_ver": RENDERER_VER, "residual_kind": RESIDUAL_KIND},
            "disclaimer": DISCLAIMER,
            "materialize": "gen+A0（先造数后测，HT-2：包内无数组，判定时现场重生成 "
                           "k=min(5,batch) 个代表内容数组，逐内容参考值读取冻结 index；"
                           "精度条目之外另派生 case_purpose==\"info\" 的批量 info 契约"
                           "用例，只比 infoArray[i]==k_expected[i] 或标量 info==-1，"
                           "HT-9）",
            "params": {
                "eps32": EPS32, "formula": FORMULA,
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
# 扩展段 + 纯脚本 CLI。拼装与批量段同一套机械操作并逐处断言（fail-closed）。
# ---------------------------------------------------------------------------

# 纯脚本精度副本的头部 docstring（替换单矩阵头部；token 与单矩阵同一套替换）。
_PURESCRIPT_HEAD_DOC = r'''__OP__ 精度自测辅助件（纯脚本包；accept criteria 的渲染副本；由 render_verify.py
生成，勿手改）。

一段式残差判定（s2-A1，criteria/verdict.judge 的同语义副本）：每个精度用例直接算
__KIND__ 残差（实际输入 A32/B32 升 f64）对阈值判——阈值 = __FORMULA__，配对本
case 在 cases/index.json（或 .gz）固化的参考 ratio。残差超阈或不可计算（NaN/Inf、零分母）→ 数值 FAIL
（后者 error 指认原因）；golden 不参与判定（降为自测参考件）。

先造数后测（本包不携带任何数据数组）：判定时逐 case 调包内 gen_data.py 与
canonical_cases.json 现场重生成输入；参考 ratio/status/mean 从 cases/index.json（或 .gz）读取，
不读取、不依赖开发者自测用的 data 目录。

用法：
    python3 verify_accuracy.py --package <包目录> --dut-out <被测输出目录> \
        [--report <json>] [--case-id <id> ...]

输入契约：包内 canonical_cases.json（本算子切片）与 gen_data.py；被测输出
<dut-out>/<case_id>.npz 含 out32（与重生成输入同 dtype 同布局）、info
（标量，LAPACK 约定）、status。

身份声明：本件是自测辅助件，输出**不构成验收证据**；数值判定不是正式结论
（formal 恒 PENDING_RULING），正式裁决由验收流程用自带判据独立计算。

退出码：0 = 全部数值 PASS；1 = 存在数值 FAIL 或证据不足；2 = 用法/输入错误。
'''

# 纯脚本扩展段：现场构造输入。冻结参考值由共用 index 读取段注入。
_PURESCRIPT_EXT_TEMPLATE = r'''# ---------------------------------------------------------------------------
# 输入现场构造；本 case 参考值只读冻结 index
# ---------------------------------------------------------------------------

import importlib.util as _importlib_util

PREP_PREFIX = "__PREP_PREFIX__"  # 参考 ratio 低精度准备链的 LAPACK 前缀（s/c）
FAMILY = "__FAMILY__"           # "chol"=Cholesky 三算子 | "luqr"=LU/QR 单矩阵算子
_PREP_DT32 = np.complex64 if PREP_PREFIX == "c" else np.float32
_PREP_DT64 = np.complex128 if PREP_PREFIX == "c" else np.float64
_LUQR_RATIO_KEYS = ("ratio_cpu", "ratio_opt_cpu")   # LU/QR 参考 ratio 键（gels 双键）


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


def _luqr_case_ratio(lapack, case_arrays):
    """LU/QR 单矩阵算子的本 case 参考 ratio（低精度完整准备链 + 本件残差实现）。

    返回 {键: 值} 更新字典，口径与验收侧回填一致：
    - getrf：CPU getrf(A32) 得低精度打包 LU/ipiv → DGET01（a=A64）；
    - geqrf：CPU geqrf(A32) 得 (AF32,tau32) → 由反射子升精度构造 Q → DQRT01（a=A64）；
    - *gqr：CPU orgqr/ungqr(A32,tau32) → 正交性残差 DQRT02（q=Q32）；
    - *mqr：CPU ormqr/unmqr(side,trans,A32,tau32,C32) → DQRT03（c=C64, q=q64）；
    - gels：CPU gels(A32,B32) 取解 X32 → 残差式按 m 形状自选，双键同写
      （判据相对线随形状取 ratio_cpu/ratio_opt_cpu）；
    - getri：CPU getri(LU32, ipiv) 得低精度逆 → DGET03（a=restore_lu(LU32, ipiv)）；
    准备失败抛 _PrepFailed。
    """
    if OP.endswith("getri"):
        lu32 = np.ascontiguousarray(case_arrays["LU32"])
        ipiv = np.asarray(case_arrays["ipiv"])
        inv32, info = getattr(lapack, PREP_PREFIX + "getri")(
            lu32, np.asarray(ipiv, dtype=np.int32) - 1)
        if info != 0:
            raise _PrepFailed(f"{PREP_PREFIX}getri info={info}")
        _check_prep("INV32", inv32)
        ratio = residual_ratio(a=restore_lu(lu32, ipiv), ainv=inv32)
        return {"ratio_cpu": float(ratio)}
    if OP.endswith("getrf"):
        a32 = np.ascontiguousarray(case_arrays["A32"])
        lu32, ipiv, info = getattr(lapack, PREP_PREFIX + "getrf")(a32)
        if info != 0:
            raise _PrepFailed(f"{PREP_PREFIX}getrf info={info}")
        _check_prep("LU32", lu32)
        ratio = residual_ratio(a=case_arrays["A64"], lu=lu32,
                               ipiv=np.asarray(ipiv) + 1)
        return {"ratio_cpu": float(ratio)}
    if OP.endswith("geqrf"):
        a32 = np.ascontiguousarray(case_arrays["A32"])
        af32, tau, _work, info = getattr(lapack, PREP_PREFIX + "geqrf")(a32)
        if info != 0:
            raise _PrepFailed(f"{PREP_PREFIX}geqrf info={info}")
        _check_prep("AF32", af32)
        q = _orgqr_full(np.ascontiguousarray(af32).astype(_PREP_DT64), tau,
                        af32.shape[0], PREP_PREFIX == "c")
        ratio = residual_ratio(a=case_arrays["A64"], r=af32, q=q)
        return {"ratio_cpu": float(ratio)}
    if OP.endswith("gqr"):
        a32 = np.ascontiguousarray(case_arrays["A32"])
        tau32 = np.ascontiguousarray(case_arrays["tau32"])
        # 低精度前缀例程（sorgqr/cungqr）：dorgqr/zungqr 对低精度输入升型到 64/128，
        # 会丢同精度链路语义（scipy 静默降型坑，README 1.5 条 7）。
        fn = lapack.cungqr if PREP_PREFIX == "c" else lapack.sorgqr
        q32, _work, info = fn(a32, tau32)
        if info != 0:
            raise _PrepFailed(f"{PREP_PREFIX}orgqr/ungqr info={info}")
        _check_prep("Q32", q32)
        return {"ratio_cpu": float(residual_ratio(q=q32))}
    if OP.endswith("mqr"):
        a32 = np.ascontiguousarray(case_arrays["A32"])
        c32 = np.ascontiguousarray(case_arrays["C32"])
        tau32 = np.ascontiguousarray(case_arrays["tau32"])
        side, trans = case_arrays["side"], case_arrays["trans"]
        nq, k = a32.shape
        lwork = max(1, 64 * max(nq, c32.shape[0], c32.shape[1], k))
        # 低精度前缀例程（sormqr/cunmqr），同 *gqr 升型说明。
        fn = lapack.cunmqr if PREP_PREFIX == "c" else lapack.sormqr
        opq32, _work, info = fn(side, trans, a32, tau32, c32,
                                lwork=lwork, overwrite_c=0)
        if info != 0:
            raise _PrepFailed(f"{PREP_PREFIX}ormqr/unmqr info={info}")
        _check_prep("opQ32", opq32)
        ratio = residual_ratio(c=case_arrays["C64"], opq=opq32,
                               q=case_arrays["q64"], side=side, trans=trans)
        return {"ratio_cpu": float(ratio)}
    # gels（sgels/cgels）：双键同写，残差式按 m 形状自选。
    a32 = np.ascontiguousarray(case_arrays["A32"])
    b32 = np.ascontiguousarray(case_arrays["B32"])
    _lqr, x32, info = getattr(lapack, PREP_PREFIX + "gels")(a32, b32, trans="N")
    if info != 0:
        raise _PrepFailed(f"{PREP_PREFIX}gels info={info}")
    n = a32.shape[1]
    x32 = np.ascontiguousarray(x32[:n, :])
    _check_prep("X32", x32)
    ratio = float(residual_ratio(a=case_arrays["A32"], b=case_arrays["B32"], x=x32))
    return {"ratio_cpu": ratio, "ratio_opt_cpu": ratio}


def _materialize_case(gen_mod, entry, by_id, lapack=None):
    """现场重生成一个 case 的判定输入（先造数后测）。

    返回 (case_arrays, note)。只重建判定输入，不计算 CPU 阈值参考。
    ratio_cpu、gels 超定所需 ratio_opt_cpu 及状态由冻结 index 注入；
    缺失或旧口径参考值不能由现场 LAPACK 计算替代。lapack 参数保留调用兼容。
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
    return case_arrays, None'''

# 纯脚本精度副本的 CLI 段（替换单矩阵 CLI 分节：目标 case 取 canonical 清单，
# 输入现场重生成；被测输出装载 info 标量）。
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
    out = {"out32": found.get("out32"),
           "info": _scalar(info) if info is not None else None,
           "status": str(_scalar(status)) if status is not None else None}
    for k in ("ipiv", "tau"):        # LU/QR：getrf 打包主元 / geqrf 反射子标度
        if k in found:
            out[k] = found[k]
    return out


def info_require_s1(canonical):
    """info 契约用例的派生基座口径：切片顶层 package_scope 是唯一事实源，判定与
    包内 gen_data 的 --select 逐字一致。

    返回 True 时派生只取 s1_subset 为 True 的精度条目；返回 False 时取切片内
    全部精度条目。package_scope 为 "full" 才返回 False，其余取值与缺该字段的
    旧切片一律返回 True（旧包按 s1 口径）。

    两侧口径不同会派生出不同 case_id，被测输出就对不上——装包自检调本函数
    对账，不另写一份规则。"""
    return canonical.get("package_scope") != "full"


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
        # LU/QR 族的参考线（gels 双键）按准备链现场算，需 scipy；Cholesky 族
        # 只读冻结 index，不加载 scipy（accept-1010 口径）。
        lapack = _prep_lapack() if FAMILY == "luqr" else None
    except Exception as exc:
        print(f"[错误] 现场重生成前置不可用: {exc}", file=sys.stderr)
        return 2
    # HT-8：追加派生 info 契约用例（与 gen 侧 derive_info_cases 同一选择规则同一
    # 随机流；canonical 切片只含精度用例，info 用例由包内 gen_data 现场派生）。
    # --case-id 命中 info 用例 id 时同样可单跑。
    by_id = {e["case_id"]: e for e in canonical["cases"]}
    info_entries = [e for e in gen_mod.derive_info_cases(
                        canonical["cases"], require_s1=info_require_s1(canonical))
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
    try:
        frozen_by_id = _load_frozen_index(pkg_dir, entries, batched=False)
    except (OSError, EOFError, ValueError, TypeError) as exc:
        print(f"[错误] 冻结基线契约错误: {exc}", file=sys.stderr)
        return 2

    print(f"# verify_accuracy — {OP}（criteria {CRITERIA_VER} / renderer {RENDERER_VER}）")
    print(f"# {DISCLAIMER}")
    print("# 先造数后测：本包不携带数组，判定输入现场重生成；参考 ratio/status/mean "
          "只读包内冻结 index（不读取 data 目录）")
    results = []
    n_pass = n_fail = n_noev = 0
    for entry in entries:
        case_id = entry["case_id"]
        try:
            arrays, prep_note = _materialize_case(gen_mod, entry, by_id, lapack)
            _apply_frozen(arrays, frozen_by_id[case_id])
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
            "materialize": "gen（先造数后测：包内无数组，判定时现场重生成输入，"
                           "本 case 参考值读取冻结 index；精度条目之外另派生 "
                           "case_purpose==\"info\" 的 info 契约用例，只比 "
                           "info==k_expected，HT-8）",
            "params": {
                "eps32": EPS32, "formula": FORMULA,
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
        "block": _SPOTRF_BLOCK,
    },
    "spotrs": {
        "prefix": "s",
        "kind": "DPOT02",
        "formula": thresholds.POTRF_POTRS_FORMULA,
        "threshold_fn": thresholds.potrf_potrs_threshold,
        "block": _SPOTRS_BLOCK,
    },
    "spotri": {
        "prefix": "s",
        "kind": "DPOT03",
        "formula": thresholds.POTRI_FORMULA,
        "threshold_fn": thresholds.potri_threshold,
        "block": _SPOTRI_BLOCK,
    },
    # S2c 复数三算子（S2 spec §4）：kind、阈值公式与数值同实数书；复数语义
    # （复模/共轭/升精度）在各自 block 与复数公共件内。
    "cpotrf": {
        "prefix": "c",
        "kind": "DPOT01",
        "formula": thresholds.POTRF_POTRS_FORMULA,
        "threshold_fn": thresholds.potrf_potrs_threshold,
        "block": _CPOTRF_BLOCK,
    },
    "cpotrs": {
        "prefix": "c",
        "kind": "DPOT02",
        "formula": thresholds.POTRF_POTRS_FORMULA,
        "threshold_fn": thresholds.potrf_potrs_threshold,
        "block": _CPOTRS_BLOCK,
    },
    "cpotri": {
        "prefix": "c",
        "kind": "DPOT03",
        "formula": thresholds.POTRI_FORMULA,
        "threshold_fn": thresholds.potri_threshold,
        "block": _CPOTRI_BLOCK,
    },
    # ---- LU 实/复四接口（lq-plan §1；四册任务书 §3.2）----
    "sgetrf": {
        "prefix": "s",
        "kind": "DGET01",
        "formula": thresholds.GETRF_FORMULA["real"],
        "threshold_fn": functools.partial(thresholds.getrf_threshold, complex_=False),
        "target_doc": "被测打包 LU + 被测 ipiv → 还原 L·U（按 ipiv 逆序回放行交换）"
                      "vs 原 A64 直审（因子非唯一，任务书 §3.2.1 条 6）。",
        "block": _luqr_getrf_block(False),
    },
    "cgetrf": {
        "prefix": "c",
        "kind": "DGET01",
        "formula": thresholds.GETRF_FORMULA["complex"],
        "threshold_fn": functools.partial(thresholds.getrf_threshold, complex_=True),
        "target_doc": "被测打包 LU + 被测 ipiv → 还原 L·U（按 ipiv 逆序回放行交换）"
                      "vs 原 A64 直审，实/虚拆两目标、双侧同时达标（因子非唯一，"
                      "任务书 §3.2.1 条 6）。",
        "block": _luqr_getrf_block(True),
    },
    "sgetri": {
        "prefix": "s",
        "kind": "DGET03",
        "formula": thresholds.GETRI_FORMULA,
        "threshold_fn": thresholds.getri_threshold,
        "target_doc": "输入 LU 因子按 ipiv 还原 → A，被测逆 C：‖I−C·A‖₁ 直审"
                      "（任务书 §3.2.1.2，SGET03）。",
        "block": _luqr_getri_block(),
    },
    "cgetri": {
        "prefix": "c",
        "kind": "DGET03",
        "formula": thresholds.GETRI_FORMULA,
        "threshold_fn": thresholds.getri_threshold,
        "target_doc": "输入 LU 因子按 ipiv 还原 → A，被测逆 C：‖I−C·A‖₁ 直审，"
                      "实/虚由复模与共轭转置天然捕捉（任务书 §3.2.1.2，CGET03）。",
        "block": _luqr_getri_block(),
    },
    # ---- QR 实/复四接口（lq-plan §1；四册任务书 §3.2）----
    "sgeqrf": {
        "prefix": "s",
        "kind": "DQRT01",
        "formula": thresholds.GEQRF_MQR_FORMULA,
        "threshold_fn": thresholds.geqrf_mqr_threshold,
        "target_doc": "由被测打包反射子 (v, tau) 升精度构造 m×m 完整 Q，Q·R"
                      "（R = triu 被测打包输出）vs 原 A64 直审（因子非唯一，"
                      "任务书 §3.2.1 条 6）。",
        "block": _luqr_geqrf_block(),
    },
    "cgeqrf": {
        "prefix": "c",
        "kind": "DQRT01",
        "formula": thresholds.GEQRF_MQR_FORMULA,
        "threshold_fn": thresholds.geqrf_mqr_threshold,
        "target_doc": "由被测打包反射子 (v, tau) 升精度构造 m×m 完整酉矩阵 Q，Q·R"
                      "（R = triu 被测打包输出）vs 原 A64 直审，实/虚拆两目标、"
                      "双侧同时达标（因子非唯一，任务书 §3.2.1 条 6）。",
        "block": _luqr_geqrf_block(),
    },
    "sorgqr": {
        "prefix": "s",
        "kind": "DQRT02",
        "formula": thresholds.GQR_FORMULA,
        "threshold_fn": thresholds.gqr_threshold,
        "target_doc": "输出正交矩阵 Q（m×n）全量逐元素 vs golden64 直审"
                      "（解唯一，任务书 §3.2.1 条 6）。",
        "block": _luqr_gqr_block(),
    },
    "cungqr": {
        "prefix": "c",
        "kind": "DQRT02",
        "formula": thresholds.GQR_FORMULA,
        "threshold_fn": thresholds.gqr_threshold,
        "target_doc": "输出酉矩阵 Q（m×n）全量逐元素 vs golden64 直审，实/虚拆两目标、"
                      "双侧同时达标（解唯一，任务书 §3.2.1 条 6）。",
        "block": _luqr_gqr_block(),
    },
    "sormqr": {
        "prefix": "s",
        "kind": "DQRT03",
        "formula": thresholds.GEQRF_MQR_FORMULA,
        "threshold_fn": thresholds.geqrf_mqr_threshold,
        "target_doc": "输出 op(Q)·C 全量逐元素 vs golden64 直审（由显式 Q 作矩阵乘的"
                      "等价形式，任务书 §3.2.1 条 6）。",
        "block": _luqr_mqr_block(),
    },
    "cunmqr": {
        "prefix": "c",
        "kind": "DQRT03",
        "formula": thresholds.GEQRF_MQR_FORMULA,
        "threshold_fn": thresholds.geqrf_mqr_threshold,
        "target_doc": "输出 op(Q)·C 全量逐元素 vs golden64 直审，实/虚拆两目标、"
                      "双侧同时达标（由显式 Q 作矩阵乘的等价形式，任务书 §3.2.1 条 6）。",
        "block": _luqr_mqr_block(),
    },
    "sgels": {
        "prefix": "s",
        "kind": "DQRT16",
        "formula": thresholds.GELS_SQUARE_FORMULA,
        "threshold_fn": functools.partial(thresholds.gels_threshold, square=True),
        "target_doc": "最小二乘解 X（n×nrhs）：残差双判据按形状选——m=n 走方阵式"
                      "（DQRT16，max(5·ratio_cpu, 0.015)）、m>n 走超定式"
                      "（DQRT17，10·ratio_opt_cpu），阈值相对线随形状取 "
                      "ratio_cpu / ratio_opt_cpu（任务书 §3.2.2.4）。",
        "block": _luqr_gels_block(False),
    },
    "cgels": {
        "prefix": "c",
        "kind": "DQRT16",
        "formula": thresholds.GELS_SQUARE_FORMULA,
        "threshold_fn": functools.partial(thresholds.gels_threshold, square=True),
        "target_doc": "最小二乘解 X（n×nrhs）：残差双判据按形状选——m=n 走方阵式"
                      "（DQRT16，max(5·ratio_cpu, 0.015)）、m>n 走超定式"
                      "（DQRT17，10·ratio_opt_cpu），阈值相对线随形状取 "
                      "ratio_cpu / ratio_opt_cpu，实/复同式（任务书 §3.2.2.4）。",
        "block": _luqr_gels_block(True),
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
    },
    "spotrsBatched": {
        "base": "spotrs", "info_kind": "scalar", "prefix": "s",
        "info_role_doc": "标量 info 型（potrsBatched 族，官方仅支持 nrhs=1）",
    },
    "cpotrfBatched": {
        "base": "cpotrf", "info_kind": "array", "prefix": "c",
        "info_role_doc": "infoArray 型（potrfBatched 族）",
    },
    "cpotrsBatched": {
        "base": "cpotrs", "info_kind": "scalar", "prefix": "c",
        "info_role_doc": "标量 info 型（potrsBatched 族，官方仅支持 nrhs=1）",
    },
    # ---- LU/QR 批量两族四接口（lq-plan §1；四册任务书 §3.2）----
    # getriBatched/matinvBatched 的判据卡 base_op 是自身（不在 _OP_SPECS），
    # 判据成员条登记于 _BATCH_BASE_SPECS；此处只登记批维身份与批量措辞。
    "sgetribatched": {
        "base": "sgetribatched", "info_kind": "array", "prefix": "s",
        "info_role_doc": "infoArray 型（getriBatched 族，逐矩阵奇异下标）",
        "target_doc": "逐矩阵输出逆 C_i vs golden64[i] 全量逐元素直审（解唯一，"
                      "任务书 §3.2.1 条 6）。",
    },
    "cgetribatched": {
        "base": "cgetribatched", "info_kind": "array", "prefix": "c",
        "info_role_doc": "infoArray 型（getriBatched 族，逐矩阵奇异下标）",
        "target_doc": "逐矩阵输出逆 C_i vs golden64[i] 全量逐元素直审，实/虚各自"
                      "成目标、双侧同时达标（解唯一，任务书 §3.2.1 条 6）。",
    },
    "smatinvbatched": {
        "base": "smatinvbatched", "info_kind": "array", "prefix": "s",
        "info_role_doc": "infoArray 型（matinvBatched 族，逐矩阵奇异下标）",
        "target_doc": "逐矩阵输出逆 C_i vs golden64[i] 全量逐元素直审（解唯一，"
                      "任务书 §3.2.1 条 6）。",
    },
    "cmatinvbatched": {
        "base": "cmatinvbatched", "info_kind": "array", "prefix": "c",
        "info_role_doc": "infoArray 型（matinvBatched 族，逐矩阵奇异下标）",
        "target_doc": "逐矩阵输出逆 C_i vs golden64[i] 全量逐元素直审，实/虚各自"
                      "成目标、双侧同时达标（解唯一，任务书 §3.2.1 条 6）。",
    },
}

# LU/QR 批量两族的判据成员条（getriBatched/matinvBatched 无单矩阵基卡，base_op 即
# 自身）：kind/公式/阈值/块与 cards_luqr 对应卡逐项断言一致（_check_op_spec）。
_BATCH_BASE_SPECS = {
    "sgetribatched": {
        "prefix": "s",
        "kind": "DGET03",
        "formula": thresholds.GETRIBATCHED_FORMULA,
        "threshold_fn": thresholds.getribatched_threshold,
        "target_doc": "逐矩阵输出逆 C_i vs golden64[i] 全量逐元素直审（解唯一，"
                      "任务书 §3.2.1 条 6）。",
        "block": _luqr_getribatched_block(),
    },
    "cgetribatched": {
        "prefix": "c",
        "kind": "DGET03",
        "formula": thresholds.GETRIBATCHED_FORMULA,
        "threshold_fn": thresholds.getribatched_threshold,
        "target_doc": "逐矩阵输出逆 C_i vs golden64[i] 全量逐元素直审，实/虚各自"
                      "成目标、双侧同时达标（解唯一，任务书 §3.2.1 条 6）。",
        "block": _luqr_getribatched_block(),
    },
    "smatinvbatched": {
        "prefix": "s",
        "kind": "DGET03",
        "formula": thresholds.MATINVBATCHED_FORMULA,
        "threshold_fn": thresholds.matinvbatched_threshold,
        "target_doc": "逐矩阵输出逆 C_i vs golden64[i] 全量逐元素直审（解唯一，"
                      "任务书 §3.2.1 条 6）。",
        "block": _luqr_matinvbatched_block(),
    },
    "cmatinvbatched": {
        "prefix": "c",
        "kind": "DGET03",
        "formula": thresholds.MATINVBATCHED_FORMULA,
        "threshold_fn": thresholds.matinvbatched_threshold,
        "target_doc": "逐矩阵输出逆 C_i vs golden64[i] 全量逐元素直审，实/虚各自"
                      "成目标、双侧同时达标（解唯一，任务书 §3.2.1 条 6）。",
        "block": _luqr_matinvbatched_block(),
    },
}


def _spec_card(op):
    """统一取卡（fail-closed）：LU/QR 16 接口走 cards_luqr，其余走 cards_cholesky。"""
    if cards_luqr is not None and cards_luqr.accepts(op):
        return cards_luqr.get_card(op)
    return cards_cholesky.get_card(op)


def _threshold_same(spec_fn, card_fn):
    """阈值函数同源判定：对象恒等或逐探针行为一致。LU/QR 卡阈值是 functools.partial
    （functools.partial 非同一对象），故以行为比较替代 `is` 恒等；探针覆盖各地板
    上下与两册数值区，并探测两支式 (potrf/potrs) 的两参路径。"""
    if spec_fn is card_fn:
        return True
    for r in (0.0, 1e-6, 0.02, 0.09, 0.2, 1.0, 3.0, 100.0):
        try:
            one_a = spec_fn(r)
        except Exception:            # 非法入参：两侧应同为异常，视为一致
            one_a = "ERR"
        try:
            one_b = card_fn(r)
        except Exception:
            one_b = "ERR"
        if one_a != one_b:
            return False
        if one_a == "ERR":
            continue
        try:
            if spec_fn(r, r) != card_fn(r, r):
                return False
        except TypeError:             # 单参式：无两参路径
            continue
    return True


def _check_op_spec(op, spec):
    """嵌入的卡参数与统一取卡结果逐项断言一致；漂移即渲染失败（fail-closed）。"""
    card = _spec_card(op)
    if card.residual_kind != spec["kind"]:
        raise AssertionError(
            f"{op}: 模板 kind {spec['kind']!r} 与卡 {card.residual_kind!r} 漂移")
    if card.formula != spec["formula"]:
        raise AssertionError(
            f"{op}: 模板公式 {spec['formula']!r} 与卡 {card.formula!r} 漂移")
    if not _threshold_same(spec["threshold_fn"], card.threshold_fn):
        raise AssertionError(f"{op}: 模板阈值函数与卡的 threshold_fn 不同源")


def _check_batched_card(op, bspec, base_spec):
    """批量卡身份与判据成员与统一取卡结果逐项断言一致（fail-closed）。"""
    card = _spec_card(op)
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
    if card.formula != base_spec["formula"]:
        raise AssertionError(
            f"{op}: 模板公式 {base_spec['formula']!r} 与卡 {card.formula!r} 漂移")
    if not _threshold_same(base_spec["threshold_fn"], card.threshold_fn):
        raise AssertionError(f"{op}: 模板阈值函数与卡的 threshold_fn 不同源")



_FROZEN_INDEX_TEMPLATE = r'''
# 冻结参考值唯一来源；本件不根据本机 LAPACK 重算裁决阈值。
def _load_frozen_index(pkg_dir, entries, batched=False):
    import gzip
    import zlib
    cases = pkg_dir / "cases"
    plain = cases / "index.json"
    if plain.is_file():
        raw = plain.read_bytes()
    else:
        gz = cases / "index.json.gz"
        parts = [gz] if gz.is_file() else sorted(cases.glob("index.json.gz.part*"))
        if not parts:
            raise ValueError("必须恰有一份 cases/index.json（或 index.json.gz / 切分件）")
        try:
            raw = gzip.decompress(b"".join(p.read_bytes() for p in parts))
        except (OSError, zlib.error) as exc:
            raise ValueError(f"cases/index 切分件不可解: {exc}") from exc
    index = json.loads(raw.decode("utf-8"))
    if not isinstance(index, dict) or index.get("ratio_basis") != "A32-f64":
        raise ValueError("index.ratio_basis 必须为 A32-f64")
    rows = index.get("cases")
    if not isinstance(rows, list):
        raise ValueError("index.cases 必须为列表")
    lookup = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("case_id"), str):
            raise ValueError("case_id 缺失或非法")
        if row["case_id"] in lookup:
            raise ValueError("case_id 重复")
        lookup[row["case_id"]] = row
    def number(value, name):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0):
            raise ValueError(f"{name} 必须为有限非负数")
    result = {}
    for entry in entries:
        cid = entry["case_id"]
        row = lookup.get(cid)
        if row is None:
            raise ValueError(f"index 缺 case {cid}")
        if row.get("op") != OP:
            raise ValueError(f"{cid} op 不匹配")
        if "ratio_cpu" not in row or "ratio_cpu_status" not in row:
            raise ValueError(f"{cid} 缺 ratio_cpu/status")
        ratio, status = row["ratio_cpu"], row["ratio_cpu_status"]
        info = entry.get("case_purpose") == "info"
        frozen = {"ratio_cpu": ratio, "ratio_cpu_status": status}
        if info:
            if (row.get("case_purpose") != "info" or
                    row.get("k_expected") != entry.get("k_expected") or
                    ratio is not None or status not in (None, "info")):
                raise ValueError(f"{cid} info 元数据不匹配")
        else:
            if status not in ("ok", "prep_failed"):
                raise ValueError(f"{cid} ratio_cpu_status 非法")
            values = ratio if batched else [ratio]
            if not isinstance(values, list) or not values:
                raise ValueError(f"{cid} ratio_cpu 形态非法")
            for value in values:
                if value is not None:
                    number(value, f"{cid}.ratio_cpu")
            if status == "ok" and not any(v is not None for v in values):
                raise ValueError(f"{cid} ok 基线缺数值")
            if status == "prep_failed" and any(v is not None for v in values):
                raise ValueError(f"{cid} prep_failed 与数值冲突")
            if OP in ("sgels", "cgels"):
                # Legacy LU/QR indexes claimed A32-f64 while freezing A64/B64 residuals.
                # Only the corrected freeze chain writes this per-case input basis.
                if status == "ok" and row.get("ratio_input_basis") != "A32-f64":
                    raise ValueError(f"{cid} gels CPU 参考需按 A32/B32 重新冻结；缺 ratio_input_basis=A32-f64")
                if entry["m"] > entry["n"]:
                    if "ratio_opt_cpu" not in row:
                        raise ValueError(f"{cid} 缺冻结 ratio_opt_cpu")
                    opt = row["ratio_opt_cpu"]
                    if status == "ok":
                        number(opt, f"{cid}.ratio_opt_cpu")
                    elif opt is not None:
                        raise ValueError(f"{cid} prep_failed 与 ratio_opt_cpu 冲突")
                    frozen["ratio_opt_cpu"] = opt
            if USES_MEAN:
                means = index.get("ratio_cpu_mean")
                mean = row.get("ratio_cpu_mean") if batched else (
                    means.get(OP) if isinstance(means, dict) else None)
                number(mean, f"{cid}.ratio_cpu_mean")
                frozen["ratio_cpu_mean"] = mean
        if batched:
            smap = row.get("sample_map")
            if not isinstance(smap, list) or not smap:
                raise ValueError(f"{cid} 缺 sample_map")
            if not info and len(ratio) != len(smap):
                raise ValueError(f"{cid} ratio_cpu/sample_map 长度不一致")
            frozen["sample_map"] = smap
        result[cid] = frozen
    return result


def _apply_frozen(arrays, frozen):
    if "sample_map" in frozen and arrays.get("sample_map") != frozen["sample_map"]:
        raise ValueError("sample_map 与现场输入映射不一致")
    arrays.update(frozen)
'''

def load_frozen_index(package, entries, op):
    """原生入口复用随包副本的冻结契约，避免两套字段校验漂移。

    执行的仅是本模块固定模板，不包含包内文本或用户代码；与常量模板自检同法。
    """
    import json
    import math
    card = _spec_card(op)
    namespace = {"json": json, "math": math, "OP": op, "USES_MEAN": card.uses_mean}
    exec(_FROZEN_INDEX_TEMPLATE, namespace)
    return namespace["_load_frozen_index"](Path(package), entries, batched=card.batched)


def _batched_accuracy_template():
    """拼装批量精度模板：单矩阵模板换头部 docstring、切走 CLI 分节标记，接批量
    扩展段与批量 CLI。两处机械操作逐处断言；既有模板字面一字不改。"""
    parts = _ACCURACY_TEMPLATE.split(_ACC_CLI_MARK)
    if len(parts) != 2:
        raise AssertionError(
            "单矩阵精度模板缺唯一 CLI 分节标记，批量模板无法复用其判定机体")
    head = parts[0].split('"""', 2)
    if len(head) != 3:
        raise AssertionError("单矩阵精度模板头部 docstring 结构变化，批量换头失败")
    return (head[0] + '"""' + _BATCHED_HEAD_DOC + '"""' + head[2]
            + _BATCHED_EXT_TEMPLATE + _FROZEN_INDEX_TEMPLATE + _BATCHED_CLI_TEMPLATE)


def _purescript_accuracy_template():
    """拼装纯脚本精度模板（S4 六算子 v2）：单矩阵模板换头部 docstring、切走 CLI
    分节标记，接现场重生成扩展段与纯脚本 CLI。机械操作与批量拼装同一套并逐处断言；
    既有模板字面一字不改（批量拼装的机体来源不受影响）。"""
    parts = _ACCURACY_TEMPLATE.split(_ACC_CLI_MARK)
    if len(parts) != 2:
        raise AssertionError(
            "单矩阵精度模板缺唯一 CLI 分节标记，纯脚本模板无法复用其判定机体")
    head = parts[0].split('"""', 2)
    if len(head) != 3:
        raise AssertionError("单矩阵精度模板头部 docstring 结构变化，纯脚本换头失败")
    return (head[0] + '"""' + _PURESCRIPT_HEAD_DOC + '"""' + head[2]
            + _PURESCRIPT_EXT_TEMPLATE + _FROZEN_INDEX_TEMPLATE + _PURESCRIPT_CLI_TEMPLATE)


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


def _family_of(op):
    """算子族标记：LU/QR 16 接口（含批量两族）为 "luqr"，其余为 "chol"。渲染进副本
    扩展段的 FAMILY 常量，供准备链/判据分流（缺省 chol 与既有输出行为等价）。"""
    if cards_luqr is not None and cards_luqr.accepts(op):
        return "luqr"
    return "chol"


def render(op):
    """渲染一个算子的两个副本，返回 {文件名: 文本}；输出是输入的纯函数（确定性）。
    批量四算子走 _render_batched（S3 spec §4/§5：机体复用 + 批量扩展段）；单矩阵
    六算子出纯脚本形态副本（S4 v2：先造数后测，机体复用 + 现场重生成扩展段）。
    LU/QR 16 接口（lq-plan §1）与 Cholesky 同路径，族差异经 FAMILY token 分流。"""
    if op in _BATCHED_SPECS:
        return _render_batched(op)
    spec = _OP_SPECS.get(op)
    if spec is None:
        raise ValueError(
            f"未知算子 {op!r}，只支持 {tuple(_OP_SPECS) + tuple(_BATCHED_SPECS)}"
            "（S1 spec §1 + S2 spec §4 + S3 spec §4 + lq-plan §1）")
    _check_constants()
    _check_op_spec(op, spec)
    subs = [
        ("__CONSTANTS__", _CONSTANTS_BLOCK),
        ("__OP_BLOCK__", spec["block"]),
        ("__FORMULA__", spec["formula"]),
        ("__KIND__", spec["kind"]),
        ("__CRITERIA_VER__", thresholds.CRITERIA_VER),
        ("__RENDERER_VER__", RENDERER_VER),
        ("__PREP_PREFIX__", spec["prefix"]),
        ("__FAMILY__", _family_of(op)),
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
    verify_perf 与单矩阵同模板（逐 case 比值本就族无关），版本号走批量渲染版本。
    LU/QR 批量两族（getriBatched/matinvBatched）的判据成员条取自 _BATCH_BASE_SPECS。"""
    bspec = _BATCHED_SPECS[op]
    base_key = bspec["base"]
    base_spec = _OP_SPECS.get(base_key)
    if base_spec is None:
        base_spec = _BATCH_BASE_SPECS.get(base_key)
    if base_spec is None:
        raise AssertionError(
            f"{op}: 基算子 {base_key!r} 既不在 _OP_SPECS 也不在 _BATCH_BASE_SPECS")
    _check_constants()
    _check_op_spec(base_key, base_spec)
    _check_batched_card(op, bspec, base_spec)
    subs = [
        ("__CONSTANTS__", _CONSTANTS_BLOCK),
        ("__OP_BLOCK__", base_spec["block"]),
        ("__FORMULA__", base_spec["formula"]),
        ("__KIND__", base_spec["kind"]),
        ("__CRITERIA_VER__", thresholds.CRITERIA_VER),
        ("__RENDERER_VER__", BATCHED_RENDERER_VER),
        ("__INFO_ROLE_DOC__", bspec["info_role_doc"]),
        ("__INFO_KIND__", bspec["info_kind"]),
        ("__BASE_OP__", bspec["base"]),
        ("__PREP_PREFIX__", bspec["prefix"]),
        ("__FAMILY__", _family_of(op)),
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
