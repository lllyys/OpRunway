# -*- coding: utf-8 -*-
"""S1-Cholesky criteria 内核：纯残差接口 residual_ratio 与数值裁决 judge（spec §2.3/§2.3′）。

residual_ratio 无阈值、无 ratio_cpu 依赖——B 卡算基线、E 卡判被测共用这一份实现，
不得在别处另建第二套残差实现（AGENTS.md §2 机械门纪律）。

judge 只出「数值判定」（numeric: PASS/FAIL）；正式结论层 formal 本片恒为
PENDING_RULING（正式裁定待任务方出具，spec §0/§2.3），任何调用方不得把 numeric
当正式验收结论上报。

s2-A1 一段式（2026-10-08 用户裁定，去除生态精度标准层）：精度判定收成单步——每个
accuracy 用例直接算 LAPACK 残差（DPOT01/02/03）对阈值判。原 layer1 逐元素混合容差
层（比对目标、双门、±inf/NaN 收窄、复数拆实/虚统计）整体拆除，不保留诊断；
golden32/golden64 不再被 judge 消费（降为自测参考件）。阈值即原 fallback 公式原样
升为唯一判据：potrf/potrs 取 max(5·ratio_cpu, 3·ratio_cpu_mean)（HT-3，缺 mean 走
单支 5·ratio_cpu 兼容口径），potri 取 max(5·ratio_cpu, 0.1)（HT-5）。

s2-A1 换基：残差的 a 全族传实现实际输入 A32（升 f64 在 residual_ratio 内部统一做），
README 1.3 的 potrf「a 用 A64」口径废止（推翻旧裁定 24i）。

fail-closed 边界（s2-A1）：残差非有限或不可计算（含 DPOT03 零输出零分母、被测含
NaN/Inf 传染）→ 数值 FAIL 并在 error 指认原因，不崩溃不放行；缺 ratio_cpu →
证据不足（error verdict，residual.ran=False）；缺 ratio_cpu_mean → 单支 5·ratio_cpu
从严、过即 PASS、超出记证据不足（HT-3 语义）。

复数通路（S2 spec §4）：residual_ratio 按输入 dtype 自动分流，一次调用的全部数组须
同为实数或同为复数（混搭抛错）；复数先升 complex128 再取模，范数用复模，recon/镜像
取共轭（Hermitian），公式形状、ε 与阈值数值同实数书——复数误差（含纯虚部、漏共轭）
由复模与共轭转置天然捕捉，不再拆实/虚。

HT-8 info 契约支路：case_purpose=="info" 的用例（非正定/奇异因子/非法参数变体，
B2 卡口径）只比 info==k_expected，不进残差——分解中途失败的用例残差无意义，info
契约与数值精度各出独立结论（HT-12 口径）。证据问题（dut_out 非 dict、status 非 ok、
k_expected 缺失/不可取整、info 非标量）一律 error verdict（fail-closed，
「证据问题不判精度」）。
"""

import numpy as np

try:  # criteria 作为包被导入时
    from . import thresholds
except ImportError:  # criteria 目录直接挂 sys.path 时（tests/ 与脚本消费方）
    import thresholds

KINDS = ("DPOT01", "DPOT02", "DPOT03")


# ---------------------------------------------------------------------------
# 公共小件
# ---------------------------------------------------------------------------

def normalize_uplo(uplo):
    """归一 uplo 枚举：只认 "L"/"U"（spec §0 枚举），bytes 先解码。"""
    if isinstance(uplo, bytes):
        uplo = uplo.decode("ascii", "replace")
    u = str(uplo)
    if u not in ("L", "U"):
        raise ValueError(f'uplo 只允许 "L"/"U"（spec §0 枚举），得到 {uplo!r}')
    return u


def _as_f64(name, arr):
    """输入阵统一升 FP64 并做硬校验：复数、非数值、非 2 维、NaN/Inf 一律抛异常。

    有限性检查覆盖整块数组（含非存储侧）：残差是纯接口，进来的就该是干净数据，
    脏数据在这里报错比在范数里静默传染更可诊断（fail-closed）。
    """
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


def _dtype_class(name, arr):
    """数值 dtype 分类："real"（浮点/整数）或 "complex"；其余抛 TypeError。"""
    dt = np.asarray(arr).dtype
    if np.issubdtype(dt, np.complexfloating):
        return "complex"
    if np.issubdtype(dt, np.floating) or np.issubdtype(dt, np.integer):
        return "real"
    raise TypeError(f"{name}: 非数值 dtype {dt}")


def _resolve_mode(named):
    """一次残差调用的实/复通路裁定（S2 spec §4 dtype 一致性）。

    全实数走 float64 通路，全复数走 complex128 通路，实/复混搭抛 TypeError
    （fail-closed：抓「实数阵无意混进复数链路」一类错喂，非对抗防护）。
    """
    kinds = {name: _dtype_class(name, arr) for name, arr in named.items()}
    if len(set(kinds.values())) > 1:
        raise TypeError(f"实/复混搭输入不允许（S2 spec §4 dtype 一致性）: {kinds}")
    return next(iter(kinds.values()))


def _as_c128(name, arr):
    """复数通路输入阵：先升 complex128 再进任何取模/范数（S2 spec §4，防 astype
    直转实数丢虚部的坑）；非 2 维、实部或虚部含 NaN/Inf 一律抛异常，口径同
    _as_f64（fail-closed）。"""
    a = np.asarray(arr).astype(np.complex128)
    if a.ndim != 2:
        raise ValueError(f"{name}: 期望 2 维数组，得到 shape={a.shape}")
    if not np.isfinite(a).all():
        raise ValueError(f"{name}: 含 NaN/Inf")
    return a


def _check_diag_real(name, a):
    """Hermitian 存储不变量校验（S2 spec §4：对角虚部按 0 处理并校验）。

    gen 按契约把 A 的对角虚部置 0 后入包，此处只抓无意错误（错喂非 Hermitian
    阵、脏数据混入复数链路）；被测输出不做此校验，其偏差由残差如实放大。
    """
    if np.any(np.diagonal(a).imag != 0.0):
        raise ValueError(f"{name}: Hermitian 对角虚部非 0（S2 spec §4）")


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


def mirror_storage_side(a, uplo):
    """按 uplo 取存储侧半三角并镜像成全对称阵（对侧 stale 不能用的统一口径）。

    residual_ratio 的 DPOT03 复核（I−A·C 对单位阵，potri 的 A·A⁻¹ 语义在残差层）
    用这一份实现；复数输入按 Hermitian 共轭镜像（S2 spec §4）。
    """
    return _mirror_half(_half(np.asarray(a), uplo))


def _sym_norm1_from_half(half):
    """DLANSY('1') 口径的 1-范数：半三角镜像后取最大列绝对和。

    复数输入即 DLANHE('1') 口径（Hermitian 共轭镜像，绝对值取复模）。"""
    full = _mirror_half(half)
    return float(np.max(np.sum(np.abs(full), axis=0)))


# ---------------------------------------------------------------------------
# 纯残差接口（spec §2.3；公式出处 README 1.3 / 2.3 / 3.3 与其参考实现）
# ---------------------------------------------------------------------------

def _dpot01(a, factor, uplo):
    """DPOT01：ratio = ‖recon−A‖₁ / (n·‖A‖₁·ε)，L 侧 recon=L·Lᵀ、U 侧 recon=Uᵀ·U；
    复数通路（S2 spec §4）recon=L·Lᴴ / Uᴴ·U，范数取复模，公式形状与 ε 不变。

    因子与差值只用存储侧（另一半是输入残留，无效）；差值与 A 的范数都按
    DLANSY 半三角镜像口径。spotrf 卡约定 a 传 A32（s2-A1 换基：全族实际输入
    A32/B32 升 f64，README 1.3 的 A64 口径废止）。
    """
    uplo = normalize_uplo(uplo)
    mode = _resolve_mode({"a": a, "factor": factor})
    cast = _as_c128 if mode == "complex" else _as_f64
    a = cast("a", a)
    f = cast("factor", factor)
    if mode == "complex":
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
    return num / (n * anorm * thresholds.EPS32)


def _dpot02(a, b, x):
    """DPOT02：ratio = max_j ‖B_j−A·X_j‖₁ / (‖A‖₁·‖X_j‖₁·ε)，逐 RHS 列取 max。

    分母无 n（README 2.3，与 DGET02 同构）。残差对实现实际输入升 FP64 算——
    spotrs 卡约定 a/b 传 A32/B32，本函数内部统一升 FP64。
    a 按给定全对称阵使用（‖A‖₁ 取最大列绝对和，对称阵行列和相等）。
    """
    mode = _resolve_mode({"a": a, "b": b, "x": x})
    cast = _as_c128 if mode == "complex" else _as_f64
    a = cast("a", a)
    b = cast("b", b)
    x = cast("x", x)
    if mode == "complex":
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
        ratio = max(ratio, bnorm / (anorm * xnorm * thresholds.EPS32))
    return ratio


def _dpot03(a, ainv, uplo):
    """DPOT03：ratio = ‖I−A·C‖₁ / (n·‖A‖₁·‖C‖₁·ε)，双半三角口径。

    a 与 ainv 都取存储侧镜像成全对称阵再相乘（对侧 stale 不能用）；
    ‖A‖₁、‖C‖₁ 亦按半三角镜像（DLANSY）口径。分子 ‖I−A·C‖₁ 按 DLANGE('1')
    取最大列绝对和。spotri 卡约定 a 传 A32（README 3.3：A 用实现实际输入升精度）。
    """
    uplo = normalize_uplo(uplo)
    mode = _resolve_mode({"a": a, "ainv": ainv})
    cast = _as_c128 if mode == "complex" else _as_f64
    a = cast("a", a)
    c = cast("ainv", ainv)
    if mode == "complex":
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
    return num / (n * anorm * cnorm * thresholds.EPS32)


_KIND_IMPL = {"DPOT01": _dpot01, "DPOT02": _dpot02, "DPOT03": _dpot03}


def residual_ratio(kind, **arrays):
    """纯残差接口（spec §2.3）：kind ∈ {DPOT01, DPOT02, DPOT03}，返回 float。

    关键字参数（输入均为内存 ndarray，uplo 除外；I/O 由调用方负责）：
    - DPOT01: a（n×n 对称阵）, factor（n×n 因子，存储侧有效）, uplo（"L"/"U"）
    - DPOT02: a（n×n 全对称阵）, b（n×nrhs 右端）, x（n×nrhs 解）
    - DPOT03: a（n×n 对称阵）, ainv（n×n 逆，存储侧有效）, uplo（"L"/"U"）

    ε 固定 2^-24（spec §0，复数不变）。实/复自动分流（S2 spec §4）：一次调用的
    全部数组须同为实数或同为复数；复数先升 complex128 再取模，a 的对角虚部
    须为 0（Hermitian 存储校验）。NaN/Inf、shape 错、零分母、未知 kind、
    实/复混搭、多余/缺失关键字 → 抛异常，不返回数值。
    """
    impl = _KIND_IMPL.get(kind)
    if impl is None:
        raise ValueError(f"未知 kind {kind!r}，只支持 {KINDS}")
    return float(impl(**arrays))


# ---------------------------------------------------------------------------
# 数值裁决 judge（spec §2.3/§2.3′，s2-A1 一段式）
# ---------------------------------------------------------------------------

def _null_residual():
    # 残差未运行时 ran=False 其余字段 null。eps 例外，仍披露口径值：它是残差 ratio 的
    # 归一基准（issue A4；任务书 2026-10 定稿 ε=2^-24=SLAMCH('E') 口径，与本值一致），
    # 不是运行结果，缺省更易误读。
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


def _run_residual(card, case_arrays, dut_out):
    """一段式残差判定（s2-A1）：算 LAPACK 残差、对阈值判。返回
    (residual dict, numeric, error)。

    阈值消费面（HT-3）：uses_mean 卡（potrf/potrs）按两支式
    max(5·ratio_cpu, 3·ratio_cpu_mean) 裁决，ratio_cpu_mean 从 case_arrays 读
    （accept_run 从 index 顶层按算子注入，HT-4 固化）。缺 mean 的兼容口径
    （fail-closed 方向）：残差 ≤ 单支 5·ratio_cpu → PASS（证据注明单支）；
    超出 → 证据不足不判 FAIL（两支公式只可证一支），待含 mean 的 v3 包复判。

    ratio_cpu 非有限（如 A0 的 prep_failed 内容转 NaN）由阈值公式的入参校验抛错，
    judge 外层收敛为「judge 内部异常」error verdict——基线不可用即不可裁。"""
    status = case_arrays.get("ratio_cpu_status")
    ratio_cpu = case_arrays.get("ratio_cpu")
    if status != "ok" or ratio_cpu is None:
        return (
            _null_residual(),
            "FAIL",
            f"残差基线不可用: ratio_cpu_status={status!r}, ratio_cpu={ratio_cpu!r}"
            "（spec §2.4：准备失败该 case 不可裁）",
        )
    formula = card.formula
    mean = case_arrays.get("ratio_cpu_mean")
    if not card.uses_mean:
        threshold = float(card.threshold_fn(ratio_cpu))
    elif mean is not None:
        threshold = float(card.threshold_fn(ratio_cpu, mean))
    else:
        threshold = None        # 缺 mean：残差算出后按单支兼容路径裁决
    base = {"ran": True, "ratio_cpu": float(ratio_cpu),
            "ratio_cpu_mean": None if mean is None else float(mean), "eps": "2^-24"}
    try:
        ratio = residual_ratio(card.residual_kind, **card.residual_kwargs(case_arrays, dut_out))
    except Exception as exc:  # 残差算不出（NaN/Inf 传染、零分母等）→ 数值 FAIL，指认原因
        res = {**base, "ratio": None, "threshold": threshold,
               "formula": formula, "pass": False}
        return res, "FAIL", f"残差不可计算: {type(exc).__name__}: {exc}"
    if threshold is None:
        # 缺 mean 兼容（HT-3）：单支从严、过即 PASS；超单支 → 证据不足。
        line = thresholds.potrf_potrs_single_line(ratio_cpu)
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
            f"5·ratio_cpu={line:.6g}，不判 FAIL，待含 mean 的 v3 包复判")
    ok = bool(ratio <= threshold)
    res = {**base, "ratio": float(ratio), "threshold": threshold,
           "formula": formula, "pass": ok}
    return res, ("PASS" if ok else "FAIL"), None


def _judge_inner(card, case_arrays, dut_out):
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

    residual, numeric, error = _run_residual(card, case_arrays, dut_out)
    return {"residual": residual, "numeric": numeric,
            "formal": "PENDING_RULING", "flags": [], "error": error}


def _judge_info_inner(card, case_arrays, dut_out):
    """HT-8 info 契约支路（case_purpose=="info" 的用例专用）：只比 info，
    不进残差（B2 卡口径：非正定/奇异用例分解中途失败，残差无意义；info 契约
    与数值精度各出独立结论，HT-12）。

    fail-closed：dut_out 非 dict、status 非 "ok"、k_expected 缺失/不可取整、
    info 非标量，一律 error verdict（证据问题不判精度，语义同 _error_verdict，
    另带 info: None 保持支路 schema 可辨识）。比对通过性 = int(info) ==
    int(k_expected)；formal 恒 PENDING_RULING，与残差通路同一正式层。
    """
    if not isinstance(dut_out, dict):
        return {**_error_verdict(f"dut_out 必须是 dict，得到 {type(dut_out).__name__}"),
                "info": None}
    status = dut_out.get("status")
    if status != "ok":
        return {**_error_verdict(f"被测状态非 ok: status={status!r}"), "info": None}
    try:
        k_expected = int(case_arrays["k_expected"])
    except (KeyError, TypeError, ValueError):
        return {**_error_verdict("case_arrays 缺可取整的 k_expected（info 用例必带字段）"),
                "info": None}
    info = dut_out.get("info")
    if info is None or np.ndim(info) != 0:
        return {**_error_verdict(f"被测 info 应为标量整数，得到 {info!r}"), "info": None}
    try:
        actual = int(info)
    except (TypeError, ValueError):
        return {**_error_verdict(f"被测 info 应为标量整数，得到 {info!r}"), "info": None}
    ok = actual == k_expected
    return {
        "info": {"expected": k_expected, "actual": actual, "pass": ok},
        "numeric": "PASS" if ok else "FAIL",
        "formal": "PENDING_RULING",
        "flags": [],
        "error": None,
    }


_BATCH_SLICED_KEYS = ("A64", "A32", "B64", "B32", "golden64", "golden32")


def _batched_error_verdict(msg, batch=None):
    """批量卡的 error verdict：统计域全空，error 语义同单矩阵（「不可裁/证据
    问题」，上层不得当精度 FAIL 上报）。HT-16 起 T8 暂定聚合标记摘除，flags 恒空。"""
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


def _check_batched_info(card, info, batch, allow_nonzero=False):
    """info 按接口角色分型（S3 spec §4，任务书接口说明第 69 行）。

    info_kind="array"（potrfBatched 族）：返回 shape=(batch,) 的整数 infoArray，
    逐矩阵消费；batch=1 也必须是 (1,)，标量拒收（防误掩测试）。
    info_kind="scalar"（potrsBatched 族）：info 仅报参数错，非 0 属证据问题不属
    精度失败；正定性由前置分解的 infoArray 反映，不进本卡——传入数组即用法错。
    HT-9：info 契约条目（bad_param_uplo 探针）本身要核对 info==-1，传
    allow_nonzero=True 只做分型检查、放过标量非 0（值留给契约比对裁）。
    校验通过返回 infoArray 或 None（标量型已确认为 0 或放行非 0）；非法抛 ValueError。
    """
    if card.info_kind == "array":
        arr = np.asarray(info) if info is not None else None
        if arr is None or arr.ndim != 1 or arr.shape[0] != batch:
            got = ("None" if arr is None
                   else ("标量" if arr.ndim == 0 else f"shape={arr.shape}"))
            raise ValueError(
                f"{card.op} 的被测 info 应为 shape=({batch},) 的 infoArray"
                f"（int32，任务书接口说明第 69 行；batch=1 不得标量化），得到 {got}")
        if not np.issubdtype(arr.dtype, np.integer):
            raise ValueError(
                f"{card.op} 的 infoArray 应为整数 dtype（任务书：int32），得到 {arr.dtype}")
        return arr
    if info is None or np.ndim(info) != 0:
        raise ValueError(
            f"{card.op} 的被测 info 应为标量（仅报参数错，任务书接口说明第 69 行）；"
            f"正定性由前置分解的 infoArray 反映，不进本卡——得到 "
            f"{'None' if info is None else f'shape={np.shape(info)} 的数组'}")
    if int(info) != 0 and not allow_nonzero:
        raise ValueError(
            f"被测 info 非 0: info={int(info)}（{card.op} 标量 info=参数错，"
            "属证据问题不属精度失败，S3 spec §4）")
    return None


def _matrix_severity(v):
    """单矩阵 verdict 的「差」序键（越大越差），worst_index 的诊断口径：
    先 numeric FAIL、再 error 非空、再残差 ratio 大；ratio 不可得（error 矩阵或
    残差不可计算）按 +inf 参与排序。同差取序号小者（调用方用严格大于替换）。
    仅供诊断指认，不参与任何门。"""
    r = v["residual"]["ratio"]
    return (1 if v["numeric"] == "FAIL" else 0,
            1 if v["error"] is not None else 0,
            float("inf") if r is None else float(r))


def _judge_batched_inner(card, case_arrays, dut_out):
    """批量卡裁决主体（S3 spec §4）。返回批量 verdict dict。

    逐矩阵流式处理：每个矩阵切出单矩阵 case/dut 后走 _judge_inner 一段式残差
    判定（配对该矩阵自己的 c_i=ratio_cpu[i]，算子级 mean 经 passthrough 带入，
    HT-3），只保留聚合统计与最差一行明细，不产逐矩阵明细（百万小矩阵档内存
    O(1)）。case 数值结论 = 全部矩阵通过（HT-16 升正式）。
    """
    if not isinstance(dut_out, dict):
        return _batched_error_verdict(f"dut_out 必须是 dict，得到 {type(dut_out).__name__}")
    status = dut_out.get("status")
    if status != "ok":
        # 准备失败归 prep 不归目标接口（S3 spec §4 硬边界 5）：证据问题，不判精度。
        return _batched_error_verdict(
            f"被测状态非 ok: status={status!r}（prep/接口层失败不归目标，不判精度）")
    out32 = dut_out.get("out32")
    if out32 is None:
        return _batched_error_verdict("dut_out 缺 out32")
    out32 = np.asarray(out32)
    if out32.ndim != 3:
        return _batched_error_verdict(
            f"批量卡 out32 应为 (batch, n, cols) 三维（batch=1 不得标量化，"
            f"S3 spec §4），得到 shape={out32.shape}")
    batch = int(out32.shape[0])
    if batch < 1:
        return _batched_error_verdict(f"空批：out32 shape={out32.shape}")

    try:
        info_arr = _check_batched_info(card, dut_out.get("info"), batch)
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

    sliced = {}
    for key in _BATCH_SLICED_KEYS:
        if key in case_arrays:
            arr = np.asarray(case_arrays[key])
            if arr.ndim != 3 or arr.shape[0] != batch:
                return _batched_error_verdict(
                    f"批量卡 {key} 应为首维 batch={batch} 的三维数组（S3 spec §2；"
                    f"batch=1 不得标量化），得到 shape={arr.shape}", batch)
            sliced[key] = arr

    ratio_cpu = case_arrays.get("ratio_cpu")
    if ratio_cpu is not None:
        rarr = np.asarray(ratio_cpu, dtype=np.float64)
        if rarr.shape != (batch,):
            return _batched_error_verdict(
                f"批量卡 ratio_cpu 应为 shape=({batch},) 数组字段（逐矩阵配对 c_i，"
                f"S3 spec §3/§4；batch=1 不得标量化），得到 shape={rarr.shape}", batch)
    else:
        rarr = None

    # 逐矩阵 passthrough：ratio_cpu_mean/ratio_cpu_status 等标量元数据原样带到
    # 每个矩阵（HT-3：mean 是算子级预计算值，批内共享；缺 mean 单支兼容口径
    # 在逐矩阵残差判定里自然生效）。
    passthrough = {k: v for k, v in case_arrays.items()
                   if k not in _BATCH_SLICED_KEYS and k not in ("ratio_cpu", "batch")}

    fail_count = 0
    first_fail_index = None
    first_error = None
    error_count = 0
    ratio_max = None
    worst_i, worst_v, worst_sev = None, None, None

    for i in range(batch):
        case_i = dict(passthrough)
        for key, arr in sliced.items():
            case_i[key] = arr[i]
        if rarr is not None:
            case_i["ratio_cpu"] = float(rarr[i])
        dut_i = {
            "out32": out32[i],
            "info": int(info_arr[i]) if info_arr is not None else 0,
            "status": "ok",
        }
        try:
            v_i = _judge_inner(card, case_i, dut_i)
        except Exception as exc:
            v_i = _error_verdict(f"judge 内部异常: {type(exc).__name__}: {exc}")

        if v_i["numeric"] != "PASS":
            fail_count += 1
            if first_fail_index is None:
                first_fail_index = i
        if v_i["error"] is not None:
            error_count += 1
            if first_error is None:
                first_error = f"矩阵 {i}: {v_i['error']}"
        r_i = v_i["residual"]["ratio"]
        if r_i is not None:
            ratio_max = r_i if ratio_max is None else max(ratio_max, r_i)

        sev = _matrix_severity(v_i)
        if worst_sev is None or sev > worst_sev:
            worst_i, worst_v, worst_sev = i, v_i, sev

    # 整批统计只入 diagnostics（S3 spec §4）：以下都是诊断口径，不作任何门；
    # case 数值结论只由「全部矩阵通过」决定。ratio_max 是整批可算残差的最大值。
    diagnostics = {
        "batch": batch,
        "pass_count": batch - fail_count,
        "fail_count": fail_count,
        "error_count": error_count,
        "ratio_max": ratio_max,
    }
    return {
        "batch": batch,
        "fail_count": fail_count,
        "first_fail_index": first_fail_index,
        "worst_index": worst_i,
        "worst": worst_v,
        "diagnostics": diagnostics,
        "numeric": "PASS" if fail_count == 0 else "FAIL",
        "formal": "PENDING_RULING",
        "flags": [],
        "error": first_error,
    }


def judge(card, case_arrays, dut_out):
    """数值裁决（spec §2.3/§2.3′，s2-A1 一段式）：judge(card, case_arrays, dut_out)
    -> verdict。

    参数：
    - card: ``cards_cholesky.get_card(op)`` 的判据卡（提供残差喂数与阈值公式；
      见 cards_cholesky 模块文档）。
    - case_arrays: 调用方从 npz 加载并合并了 canonical/index 元数据的 dict。
      需要各卡 residual_kwargs 的数组（spotrf/spotri: A32 与 "uplo"；spotrs:
      A32/B32）与 "ratio_cpu"、"ratio_cpu_status"（index.json，spec §2.2/§2.4）；
      potrf/potrs 卡阈值第二支另需 "ratio_cpu_mean"（index 顶层按算子注入，
      HT-3/HT-4；缺走单支兼容口径，见 _run_residual）。golden32/golden64 不被
      judge 消费（s2-A1 降为自测参考件）。
    - dut_out: {"out32": ndarray, "info": int, "status": "ok"|"prep_failed"|"error"}。

    返回 verdict dict：
    {residual: {ran, ratio, threshold, formula, ratio_cpu, ratio_cpu_mean, pass,
    eps}, numeric: "PASS"|"FAIL", formal: "PENDING_RULING", flags: [],
    error: null|str}。

    数值判定（一段式）：ratio = residual_ratio(kind, 实际输入…)，ratio ≤ 阈值 →
    PASS，否则 FAIL。fail-closed 边界：残差不可计算（NaN/Inf 传染、零分母）→
    数值 FAIL、residual.ran=True、ratio=null、error 指认原因；缺 ratio_cpu →
    error verdict（residual.ran=False，证据不足不判精度）；缺 ratio_cpu_mean →
    单支 5·ratio_cpu 从严、过即 PASS、超出记证据不足（error 指认，pass=null）。
    formal 本片恒为 PENDING_RULING，绝不输出正式 PASS（正式裁定待任务方出具）。

    judge 不外抛异常：任何内部异常收敛为 error 字段 + numeric FAIL（fail-closed）；
    error 非空且 residual.ran=False 的 FAIL 属「不可裁/证据问题」，上层不得当精度
    FAIL 直接上报；residual.ran=True 的 FAIL（含 ratio=null 的残差不可计算）是对
    被测数据的数值裁决。

    S3 批量卡（card.batched=True，S3 spec §4）走逐矩阵通路，上述单矩阵语义对
    批内每个矩阵完整成立（残差配对该矩阵自己的 c_i=ratio_cpu[i]；ratio_cpu_mean
    按算子级 passthrough 带入每个矩阵，HT-3），case 数值结论 = 全部矩阵 PASS。
    批量输入约定：case_arrays 的 A64/A32/B64/B32/golden64/golden32 首维为 batch
    （三维），ratio_cpu 为 (batch,) 数组字段；dut_out.out32 三维；info 按卡的
    info_kind 分型——potrfBatched 族 (batch,) int32 infoArray（逐矩阵消费，
    非 0 记该矩阵 error），potrsBatched 族标量（仅参数错；prep 失败经 status
    归 prep，不归目标）。batch=1 同样必须带批维，标量化输入一律 error verdict
    （fail-closed）。

    批量返回 dict（schema 与单矩阵不同）：
    {batch, fail_count, first_fail_index（全过为 null）, worst_index,
    worst: <最差矩阵的完整单矩阵 verdict，一行明细>, diagnostics: <整批统计，
    仅诊断不入门（S3 spec §4）：batch/pass_count/fail_count/error_count/
    ratio_max>, numeric, formal: "PENDING_RULING", flags: 恒空（HT-16）,
    error: 首个逐矩阵 error 带「矩阵 i:」前缀，或 case 级校验失败原因}。
    worst_index 的排序口径见 _matrix_severity（诊断指认用，不参与判定）。

    HT-8 info 契约用例（case_arrays["case_purpose"]=="info"，仅单矩阵）：走
    _judge_info_inner 独立支路，只比 info==k_expected，返回 schema 带 "info"
    键（{expected, actual, pass}）而无 residual；不消费卡的残差成员，不进 mean
    （HT-4）。
    """
    if getattr(card, "batched", False):
        try:
            return _judge_batched_inner(card, case_arrays, dut_out)
        except Exception as exc:
            return _batched_error_verdict(f"judge 内部异常: {type(exc).__name__}: {exc}")
    try:
        if case_arrays.get("case_purpose") == "info":
            return _judge_info_inner(card, case_arrays, dut_out)
        return _judge_inner(card, case_arrays, dut_out)
    except Exception as exc:
        return _error_verdict(f"judge 内部异常: {type(exc).__name__}: {exc}")
