# -*- coding: utf-8 -*-
"""S1-Cholesky criteria 内核：纯残差接口 residual_ratio 与数值裁决 judge（spec §2.3/§2.3′）。

residual_ratio 无阈值、无 ratio_cpu 依赖——B 卡算基线、E 卡判被测共用这一份实现，
不得在别处另建第二套残差实现（AGENTS.md §2 机械门纪律）。

judge 只出「数值判定」（numeric: PASS/FAIL）；正式结论层 formal 本片恒为
PENDING_RULING（T1/T3 未裁，spec §0/§2.3；max_abs 门已按 2026-09-24 HT-1 裁定
收成动态锚点单口径），任何调用方不得把 numeric
当正式验收结论上报。

layer1 主判按 spec §2.3′（任务书 §3.2，优先于 §2.3 冲突部分）：比对目标由卡给出
（可多目标，如 potri 双目标，全部通过才算过），容差主套用任务书表
（rtol 2^-10 / atol 2^-16），标准表 2^-13 降为对照（分歧记 T3）；卡另可给出
「诊断」比对（如 potrf 的 F vs golden F），并报不主判。

S2c 复数增量（S2 spec §4 契约增量表）：residual_ratio 按输入 dtype 自动分流，
一次调用的全部数组须同为实数或同为复数（混搭抛错）；复数先升 complex128 再取模，
范数用复模，recon/镜像取共轭（Hermitian），公式形状、ε 与阈值数值同实数书。
layer1 的复数拆实/虚由判据卡完成（cards_cholesky），judge 拒绝复数数组直接进
逐元素统计（不合并稀释，复数任务书 §3.2 第 3 条；issue C3 确认，2026-09-24 摘 T7）
（拆实/虚口径待任务方确认）。
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
    阵、脏数据混入复数链路）；被测输出不做此校验，其对角虚部经镜像按 0 处理、
    由 layer1 拆实/虚如实计不符。
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

    residual_ratio 的 DPOT03 与卡的 potri 双目标（A·A⁻¹ 对 I）共用这一份实现；
    复数输入按 Hermitian 共轭镜像（S2 spec §4）。
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
    DLANSY 半三角镜像口径。spotrf 卡约定 a 传 A64（README 1.3 参考链路口径）。
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
# 数值裁决 judge（spec §2.3/§2.3′）
# ---------------------------------------------------------------------------

def _null_fallback():
    # fallback 未运行时 ran=False 其余字段 null（spec §2.3）。eps 例外，仍披露口径值：
    # 它是残差 ratio 的归一基准（issue A4，任务书现行文本的 2^-23 有误），不是运行结果，缺省更易误读。
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
    逐点分类与 ±inf/NaN 规则出处：mixed_tolerance_standard.md §2.1.2（2026-09-24 版
    收窄比对规则；双方 NaN 视作通过同属该收窄语义）：

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
    return bool(matched_ratio >= thresholds.REQUIRED_MATCHED_RATIO
                and max_abs <= thresholds.max_abs_limit(g_low))


def _target_entry(name, actual, golden):
    """单个比对目标的 layer1 统计：主套（任务书）+ 对照套（标准表）。

    max_abs、g_low 与容差无关，两套共用同一个值与同一个动态上限；复数拆出的
    实/虚目标各自走本函数，锚点与上限天然独立。
    """
    mr_tb, max_abs, g_low = _layer1_stats(
        actual, golden, thresholds.TASKBOOK_RTOL_FP32, thresholds.TASKBOOK_ATOL_FP32)
    mr_std, _, _ = _layer1_stats(
        actual, golden, thresholds.STANDARD_RTOL_FP32, thresholds.STANDARD_ATOL_FP32)
    return {
        "name": name,
        "matched_ratio": float(mr_tb),
        "max_abs": max_abs,
        "max_abs_limit": thresholds.max_abs_limit(g_low),
        "g_low": g_low,
        "pass": _gate(mr_tb, max_abs, g_low),
        "standard": {"matched_ratio": float(mr_std),
                     "pass": _gate(mr_std, max_abs, g_low)},
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
            mr, max_abs, _ = _layer1_stats(
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
              "formula": formula, "pass": False, "eps": "2^-24"}
        return fb, "FAIL", f"fallback 残差不可计算: {type(exc).__name__}: {exc}"
    ok = bool(ratio <= threshold)
    fb = {"ran": True, "ratio": float(ratio), "threshold": threshold,
          "formula": formula, "pass": ok, "eps": "2^-24"}
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
        if (np.issubdtype(np.asarray(actual).dtype, np.complexfloating)
                or np.issubdtype(np.asarray(golden).dtype, np.complexfloating)):
            return _error_verdict(
                f"layer1 目标 {name} 含复数数组：复数须由卡拆成实/虚目标再进统计"
                "（S2 spec §4 不合并稀释）")
        if actual.size == 0:
            return _error_verdict(f"layer1 对比集为空: {name}")
        entries.append(_target_entry(name, actual, golden))

    # 多目标聚合（spec §2.3′ potri 双目标：全部通过才算过）：pass 取 AND、
    # matched_ratio 取最差（min）、max_abs 取最差（max）；单目标算子退化为原语义。
    # 顶层 max_abs_limit/g_low 随聚合 max_abs 所在目标走（并列取首个），保持三元
    # 自洽；门槛判定在逐目标层完成（复数拆实/虚两侧各自锚点各自上限），顶层字段
    # 只是证据面。
    l1_pass = all(e["pass"] for e in entries)
    std_pass = all(e["standard"]["pass"] for e in entries)
    worst = max(entries, key=lambda e: e["max_abs"])

    layer1 = {
        "matched_ratio": min(e["matched_ratio"] for e in entries),
        "max_abs": worst["max_abs"],
        "max_abs_limit": worst["max_abs_limit"],
        "g_low": worst["g_low"],
        "pass": l1_pass,
        "targets": entries,
        # 标准表对照套并列展示（T3 运行语义：流转按任务书套，对照不参与流转）。
        "standard": {"matched_ratio": min(e["standard"]["matched_ratio"] for e in entries),
                     "pass": std_pass},
        "diagnostics": _diagnostics(card, case_arrays, dut_out),
    }

    flags = []
    # T3：任一目标上主套（任务书）与对照套（标准表）门结论分歧。
    if any(e["pass"] != e["standard"]["pass"] for e in entries):
        flags.append("T3")
    # T7 已摘（2026-09-24）：拆实/虚口径经 issue C3 评审确认与复数任务书 §3.2 第 3 条
    # 本意一致，Mr.0 裁定 issue 即书面确认，复数裁决升正式口径、不再挂歧义 flag。

    if l1_pass:
        # layer1 全部目标双门都过 → 数值 PASS（终审），fallback 不跑。
        fallback, numeric, error = _null_fallback(), "PASS", None
    else:
        # 任一目标任一门不过 → 走 fallback，fallback 为数值终审。
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
    {layer1: {matched_ratio, max_abs, max_abs_limit, g_low, pass,
    targets: [逐目标统计], standard: {对照套聚合}, diagnostics: [并报项]},
    fallback: {ran, ratio, threshold, formula, pass}, numeric: "PASS"|"FAIL",
    formal: "PENDING_RULING", flags: ["T3", ...], error: null|str}。
    layer1 顶层键是任务书主套的多目标聚合（min/max/AND，见 _judge_inner 注释）；
    max_abs_limit/g_low 随聚合 max_abs 所在目标走，g_low 无有限比对点时输出 null。

    数值判定流转（spec §2.3，容差主套按 §2.3′ 换成任务书表；max_abs 门按 2026-09-24
    HT-1 裁定收成单口径 max_abs <= thresholds.max_abs_limit(g_low)）：layer1 全部
    目标双门都过 → 数值 PASS（终审）；任一不过 → 走 fallback，fallback 为数值终审；
    任务书/标准表两套容差门结论不同记 flag T3；T7 已摘（issue C3 确认拆实/虚口径）
    （拆实/虚口径待任务方确认，S2 spec §4）。formal 本片恒为 PENDING_RULING，
    绝不输出正式 PASS（T1/T3 未裁）。

    judge 不外抛异常：任何内部异常收敛为 error 字段 + numeric FAIL（fail-closed）；
    error 非空的 FAIL 属「不可裁/证据问题」，上层不得当精度 FAIL 直接上报。
    """
    try:
        return _judge_inner(card, case_arrays, dut_out)
    except Exception as exc:
        return _error_verdict(f"judge 内部异常: {type(exc).__name__}: {exc}")
