# -*- coding: utf-8 -*-
"""S1-Cholesky criteria 内核：纯残差接口 residual_ratio 与数值裁决 judge（spec §2.3/§2.3′）。

residual_ratio 无阈值、无 ratio_cpu 依赖——B 卡算基线、E 卡判被测共用这一份实现，
不得在别处另建第二套残差实现（AGENTS.md §2 机械门纪律）。

judge 只出「数值判定」（numeric: PASS/FAIL）；正式结论层 formal 本片恒为
PENDING_RULING（T1/T3/T4 未裁，spec §0/§2.3），任何调用方不得把 numeric
当正式验收结论上报。

layer1 主判按 spec §2.3′（任务书 §3.2，优先于 §2.3 冲突部分）：比对目标由卡给出
（可多目标，如 potri 双目标，全部通过才算过），容差主套用任务书表
（rtol 2^-10 / atol 2^-16），标准表 2^-13 降为对照（分歧记 T3）；卡另可给出
「诊断」比对（如 potrf 的 F vs golden F），并报不主判。
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
        raise TypeError(f"{name}: 复数不在本片范围（spec §1 不做复数）")
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
    """半三角镜像成全对称阵（输入必须是另侧已置 0 的半三角，含对角）。"""
    return half + half.T - np.diag(np.diag(half))


def mirror_storage_side(a, uplo):
    """按 uplo 取存储侧半三角并镜像成全对称阵（对侧 stale 不能用的统一口径）。

    residual_ratio 的 DPOT03 与卡的 potri 双目标（A·A⁻¹ 对 I）共用这一份实现。
    """
    return _mirror_half(_half(np.asarray(a), uplo))


def _sym_norm1_from_half(half):
    """DLANSY('1') 口径的对称 1-范数：半三角镜像后取最大列绝对和。"""
    full = _mirror_half(half)
    return float(np.max(np.sum(np.abs(full), axis=0)))


# ---------------------------------------------------------------------------
# 纯残差接口（spec §2.3；公式出处 README 1.3 / 2.3 / 3.3 与其参考实现）
# ---------------------------------------------------------------------------

def _dpot01(a, factor, uplo):
    """DPOT01：ratio = ‖recon−A‖₁ / (n·‖A‖₁·ε)，L 侧 recon=L·Lᵀ、U 侧 recon=Uᵀ·U。

    因子与差值只用存储侧（另一半是输入残留，无效）；差值与 A 的范数都按
    DLANSY 半三角镜像口径。spotrf 卡约定 a 传 A64（README 1.3 参考链路口径）。
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
    return num / (n * anorm * thresholds.EPS32)


def _dpot02(a, b, x):
    """DPOT02：ratio = max_j ‖B_j−A·X_j‖₁ / (‖A‖₁·‖X_j‖₁·ε)，逐 RHS 列取 max。

    分母无 n（README 2.3，与 DGET02 同构）。残差对实现实际输入升 FP64 算——
    spotrs 卡约定 a/b 传 A32/B32，本函数内部统一升 FP64。
    a 按给定全对称阵使用（‖A‖₁ 取最大列绝对和，对称阵行列和相等）。
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
        ratio = max(ratio, bnorm / (anorm * xnorm * thresholds.EPS32))
    return ratio


def _dpot03(a, ainv, uplo):
    """DPOT03：ratio = ‖I−A·C‖₁ / (n·‖A‖₁·‖C‖₁·ε)，双半三角口径。

    a 与 ainv 都取存储侧镜像成全对称阵再相乘（对侧 stale 不能用）；
    ‖A‖₁、‖C‖₁ 亦按半三角镜像（DLANSY）口径。分子 ‖I−A·C‖₁ 按 DLANGE('1')
    取最大列绝对和。spotri 卡约定 a 传 A32（README 3.3：A 用实现实际输入升精度）。
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
    return num / (n * anorm * cnorm * thresholds.EPS32)


_KIND_IMPL = {"DPOT01": _dpot01, "DPOT02": _dpot02, "DPOT03": _dpot03}


def residual_ratio(kind, **arrays):
    """纯残差接口（spec §2.3）：kind ∈ {DPOT01, DPOT02, DPOT03}，返回 float。

    关键字参数（输入均为内存 ndarray，uplo 除外；I/O 由调用方负责）：
    - DPOT01: a（n×n 对称阵）, factor（n×n 因子，存储侧有效）, uplo（"L"/"U"）
    - DPOT02: a（n×n 全对称阵）, b（n×nrhs 右端）, x（n×nrhs 解）
    - DPOT03: a（n×n 对称阵）, ainv（n×n 逆，存储侧有效）, uplo（"L"/"U"）

    ε 固定 2^-24（spec §0）。NaN/Inf、shape 错、零分母、未知 kind、复数输入、
    多余/缺失关键字 → 抛异常，不返回数值。
    """
    impl = _KIND_IMPL.get(kind)
    if impl is None:
        raise ValueError(f"未知 kind {kind!r}，只支持 {KINDS}")
    return float(impl(**arrays))


# ---------------------------------------------------------------------------
# 数值裁决 judge（spec §2.3/§2.3′）
# ---------------------------------------------------------------------------

def _null_fallback():
    # fallback 未运行时 ran=False 其余字段 null（spec §2.3）。
    return {"ran": False, "ratio": None, "threshold": None, "formula": None, "pass": None}


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
    """逐元素混合容差统计（任务书 §3.2 判定式）：返回 (matched_ratio, max_abs)。

    比较式 err <= tol 对 NaN 恒为 False，故被测输出中的 NaN/Inf 元素计为不符；
    err 含非有限值时 max_abs 记为 +inf（两种上限解释必然双双不过）。
    """
    err = np.abs(actual - golden)
    tol = atol + rtol * np.abs(golden)
    ok = err <= tol
    n_total = int(err.size)
    n_bad = n_total - int(np.count_nonzero(ok))
    matched_ratio = 1.0 - n_bad / n_total
    max_abs = float(np.max(err)) if bool(np.isfinite(err).all()) else float("inf")
    return matched_ratio, max_abs


def _gate_pair(matched_ratio, max_abs):
    """整体双门在两种 max_abs 上限解释下的 (pass_fixed, pass_ulp)。"""
    ratio_ok = matched_ratio >= thresholds.REQUIRED_MATCHED_RATIO
    return (
        bool(ratio_ok and max_abs <= thresholds.MAX_ABS_FIXED),
        bool(ratio_ok and max_abs <= thresholds.MAX_ABS_ULP32),
    )


def _target_entry(name, actual, golden):
    """单个比对目标的 layer1 统计：主套（任务书）+ 对照套（标准表）。

    max_abs 与容差无关，两套共用同一个值。
    """
    mr_tb, max_abs = _layer1_stats(
        actual, golden, thresholds.TASKBOOK_RTOL_FP32, thresholds.TASKBOOK_ATOL_FP32)
    tb_fixed, tb_ulp = _gate_pair(mr_tb, max_abs)
    mr_std, _ = _layer1_stats(
        actual, golden, thresholds.STANDARD_RTOL_FP32, thresholds.STANDARD_ATOL_FP32)
    std_fixed, std_ulp = _gate_pair(mr_std, max_abs)
    return {
        "name": name,
        "matched_ratio": float(mr_tb),
        "max_abs": max_abs,
        "pass_fixed": tb_fixed,
        "pass_ulp": tb_ulp,
        "standard": {"matched_ratio": float(mr_std),
                     "pass_fixed": std_fixed, "pass_ulp": std_ulp},
    }


def _diagnostics(card, case_arrays, dut_out):
    """诊断比对（spec §2.3′：并报不主判，如 potrf 的 F vs golden F）。

    只报统计（主套容差的 matched_ratio 与 max_abs），不进任何门；算不出记
    error 条目，不影响裁决——诊断降级的含义就是它的失败不阻断主判。
    """
    try:
        items = card.layer1_diagnostics(case_arrays, dut_out)
    except Exception as exc:
        return [{"name": "diagnostics", "error": f"{type(exc).__name__}: {exc}"}]
    out = []
    for item in items:
        name = item[0]
        try:
            _, actual, golden = item
            mr, max_abs = _layer1_stats(
                actual, golden,
                thresholds.TASKBOOK_RTOL_FP32, thresholds.TASKBOOK_ATOL_FP32)
            out.append({"name": name, "matched_ratio": float(mr), "max_abs": max_abs})
        except Exception as exc:
            out.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
    return out


def _run_fallback(card, case_arrays, dut_out):
    """跑 fallback 残差并对阈值裁决。返回 (fallback dict, numeric, error)。"""
    status = case_arrays.get("ratio_cpu_status")
    ratio_cpu = case_arrays.get("ratio_cpu")
    if status != "ok" or ratio_cpu is None:
        return (
            _null_fallback(),
            "FAIL",
            f"兜底不可用: ratio_cpu_status={status!r}, ratio_cpu={ratio_cpu!r}"
            "（spec §2.4：准备失败该 case 兜底不可用）",
        )
    threshold = float(card.fallback_threshold(ratio_cpu))
    formula = card.fallback_formula
    try:
        ratio = residual_ratio(card.residual_kind, **card.fallback_kwargs(case_arrays, dut_out))
    except Exception as exc:  # 残差算不出 → fail-closed，error 指认原因
        fb = {"ran": True, "ratio": None, "threshold": threshold,
              "formula": formula, "pass": False}
        return fb, "FAIL", f"fallback 残差不可计算: {type(exc).__name__}: {exc}"
    ok = bool(ratio <= threshold)
    fb = {"ran": True, "ratio": float(ratio), "threshold": threshold,
          "formula": formula, "pass": ok}
    return fb, ("PASS" if ok else "FAIL"), None


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

    targets = card.layer1_targets(case_arrays, dut_out)
    if not targets:
        return _error_verdict("layer1 无比对目标（卡给出空目标清单）")
    entries = []
    for name, actual, golden in targets:
        if actual.size == 0:
            return _error_verdict(f"layer1 对比集为空: {name}")
        entries.append(_target_entry(name, actual, golden))

    # 多目标聚合（spec §2.3′ potri 双目标：全部通过才算过）：pass 取 AND、
    # matched_ratio 取最差（min）、max_abs 取最差（max）；单目标算子退化为原语义。
    pass_fixed = all(e["pass_fixed"] for e in entries)
    pass_ulp = all(e["pass_ulp"] for e in entries)
    std_fixed = all(e["standard"]["pass_fixed"] for e in entries)
    std_ulp = all(e["standard"]["pass_ulp"] for e in entries)

    layer1 = {
        "matched_ratio": min(e["matched_ratio"] for e in entries),
        "max_abs": max(e["max_abs"] for e in entries),
        "max_abs_limits": {"fixed": thresholds.MAX_ABS_FIXED,
                           "ulp32": thresholds.MAX_ABS_ULP32},
        "pass_fixed": pass_fixed,
        "pass_ulp": pass_ulp,
        "targets": entries,
        # 标准表对照套并列展示（T3 运行语义：流转按任务书套，对照不参与流转）。
        "standard": {"matched_ratio": min(e["standard"]["matched_ratio"] for e in entries),
                     "pass_fixed": std_fixed, "pass_ulp": std_ulp},
        "diagnostics": _diagnostics(card, case_arrays, dut_out),
    }

    flags = []
    # T3：任一目标上主套（任务书）与对照套（标准表）门结论分歧。
    if any((e["pass_fixed"], e["pass_ulp"])
           != (e["standard"]["pass_fixed"], e["standard"]["pass_ulp"]) for e in entries):
        flags.append("T3")
    # T4：任一目标上 max_abs 两种上限解释结论分歧（此时聚合必不双过，必走兜底）。
    if any(e["pass_fixed"] != e["pass_ulp"] for e in entries):
        flags.append("T4")

    if pass_fixed and pass_ulp:
        # layer1 两解释一致且过（全部目标）→ 数值 PASS（终审），fallback 不跑。
        fallback, numeric, error = _null_fallback(), "PASS", None
    else:
        # 两解释不一致或不过 → 走 fallback，fallback 为数值终审。
        fallback, numeric, error = _run_fallback(card, case_arrays, dut_out)

    return {"layer1": layer1, "fallback": fallback, "numeric": numeric,
            "formal": "PENDING_RULING", "flags": flags, "error": error}


def judge(card, case_arrays, dut_out):
    """数值裁决（spec §2.3/§2.3′）：judge(card, case_arrays, dut_out) -> verdict。

    参数：
    - card: ``cards_cholesky.get_card(op)`` 的判据卡（提供 layer1 比对目标与诊断、
      fallback 喂数与阈值公式；见 cards_cholesky 模块文档）。
    - case_arrays: 调用方从 npz 加载并合并了 canonical/index 元数据的 dict。
      layer1 需要卡指定的输入阵（spotrf: A64；spotrs: golden32；spotri: golden32
      与 A32）；spotrf/spotri 另需 "uplo"；进入 fallback 时另需 "ratio_cpu" 与
      "ratio_cpu_status"（index.json，spec §2.2/§2.4）。
    - dut_out: {"out32": ndarray, "info": int, "status": "ok"|"prep_failed"|"error"}。

    返回 verdict dict：
    {layer1: {matched_ratio, max_abs, max_abs_limits: {fixed, ulp32}, pass_fixed,
    pass_ulp, targets: [逐目标统计], standard: {对照套聚合}, diagnostics: [并报项]},
    fallback: {ran, ratio, threshold, formula, pass}, numeric: "PASS"|"FAIL",
    formal: "PENDING_RULING", flags: ["T3"|"T4", ...], error: null|str}。
    layer1 顶层键是任务书主套的多目标聚合（min/max/AND，见 _judge_inner 注释）。

    数值判定流转（spec §2.3，容差主套按 §2.3′ 换成任务书表）：layer1 两解释
    （fixed/ulp32）一致且过 → 数值 PASS（终审）；两解释不一致或不过 → 走 fallback，
    fallback 为数值终审，解释分歧记 flag T4；任务书/标准表两套容差门结论不同记
    flag T3。formal 本片恒为 PENDING_RULING，绝不输出正式 PASS（T3/T4 未裁）。

    judge 不外抛异常：任何内部异常收敛为 error 字段 + numeric FAIL（fail-closed）；
    error 非空的 FAIL 属「不可裁/证据问题」，上层不得当精度 FAIL 直接上报。
    """
    try:
        return _judge_inner(card, case_arrays, dut_out)
    except Exception as exc:
        return _error_verdict(f"judge 内部异常: {type(exc).__name__}: {exc}")
