# -*- coding: utf-8 -*-
"""verify 双件确定性渲染入口（D 卡，spec §2.5；设计 v2 §3.3「同一个确定性渲染入口」）。

用法：``render_verify.py --op <spotrf|spotrs|spotri> --out <dir>``。向 <dir> 渲染两个
独立可运行的自测辅助件：``verify_accuracy.py``（三层判定的渲染副本：layer1 混合容差
双门 → LAPACK 残差兜底）与 ``verify_perf.py``（perf_baseline 逐 case 比值，方向 =
被测/基线）。

三条渲染契约（spec 波 2 D 行）：

- **确定性**：输出只由模板与 criteria 常量决定，无时间戳、无绝对路径、无环境依赖，
  同版本复渲逐字节一致；包内副本漂移由 manifest 指纹监督，仅告警（spec §2.2 指纹分级）。
- **独立可运行**：副本不 import 本 skill 任何模块；判据参数全部嵌入文件内，含
  spec §2.3′ 任务书契约（rtol 2^-10 / atol 2^-16、matched_ratio 0.99、1e-2 or 32·ULP
  双解释、比对目标、兜底公式）。嵌入常量与卡参数在渲染时逐项与 thresholds /
  cards_cholesky 断言相等，模板与判据源漂移直接渲染失败（fail-closed）。
- **副本身份**：自测辅助件，输出不构成验收证据；正式裁决在 accept（设计 v2 §3.3
  裁决主权三规则）。副本的 formal 层恒为 PENDING_RULING，与 criteria 同语义。

模板正文是 criteria/verdict.py 与 criteria/cards_cholesky.py 相应实现的逐句转写
（模块引用改为文件内直引），行为一致性由 D 卡容器验证段对 A 卡正反例逐条核对。
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
RENDERER_VER = "s1-D1"

# ---- 嵌入常量块（渲染进副本；_check_constants 逐项与 thresholds 核对）----
_CONSTANTS_BLOCK = '''EPS32 = 2.0 ** -24                     # 残差 ε（spec §0：固定 2^-24，README 口径）
TASKBOOK_RTOL_FP32 = 2.0 ** -10        # 任务书 §3.2 主判套（spec §2.3′）
TASKBOOK_ATOL_FP32 = 2.0 ** -16
STANDARD_RTOL_FP32 = 2.0 ** -13        # 标准表对照套（门结论分歧记 T3）
STANDARD_ATOL_FP32 = 2.0 ** -13
REQUIRED_MATCHED_RATIO = 0.99          # layer1 通过率门
MAX_ABS_FIXED = 1e-2                   # max_abs 上限解释一（固定；解释分歧记 T4）
MAX_ABS_ULP32 = 32 * 2.0 ** -24        # max_abs 上限解释二（32·ULP，参照 2^-24 待裁）'''


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


def fallback_threshold(ratio_cpu):
    """收紧式阈值（DPOT01）：min(30, max(10*ratio_cpu, 1))。"""
    r = _check_ratio_cpu(ratio_cpu)
    return min(30.0, max(10.0 * r, 1.0))


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
    """spotrf 主判目标（spec §2.3′）：还原 recon 后取指定三角对原 A（A64）。"""
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


def layer1_diagnostics(case_arrays, dut_out):
    """spotrf 诊断项（spec §2.3′）：F vs golden F 直审，并报不主判。"""
    actual, golden = _tri_pair(case_arrays, dut_out)
    return [("factor_vs_golden", actual, golden)]


def fallback_kwargs(case_arrays, dut_out):
    return {
        "a": _get(case_arrays, "A64"),
        "factor": dut_out["out32"],
        "uplo": normalize_uplo(_get(case_arrays, "uplo")),
    }'''

_SPOTRS_BLOCK = r'''# ---------------------------------------------------------------------------
# spotrs 专属件（criteria/cards_cholesky spotrs 卡 + verdict._dpot02 的渲染副本）
# ---------------------------------------------------------------------------


def fallback_threshold(ratio_cpu):
    """上浮式阈值（DPOT02）：max(2*ratio_cpu, 30)。"""
    r = _check_ratio_cpu(ratio_cpu)
    return max(2.0 * r, 30.0)


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
    """spotrs 主判目标：解矩阵 X 全量逐元素 vs golden32（spec §2.3′ 维持 X 目标）。"""
    golden = _to_f64("golden32", _get(case_arrays, "golden32"))
    out = _to_f64("out32", dut_out["out32"])
    if out.shape != golden.shape:
        raise ValueError(f"shape 不匹配: out32{out.shape} vs golden32{golden.shape}")
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
    """收紧式阈值（DPOT03）：min(30, max(10*ratio_cpu, 1))。"""
    r = _check_ratio_cpu(ratio_cpu)
    return min(30.0, max(10.0 * r, 1.0))


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
    """spotri 主判双目标（spec §2.3′）：A⁻¹ 直审 及 A·A⁻¹ 对 I，全部通过才算过。"""
    direct = _tri_pair(case_arrays, dut_out)
    a32 = _to_f64("A32", _get(case_arrays, "A32"))
    out = _to_f64("out32", dut_out["out32"])
    n = _square_like("out32", "A32", a32, out)
    uplo = normalize_uplo(_get(case_arrays, "uplo"))
    prod = mirror_storage_side(a32, uplo) @ mirror_storage_side(out, uplo)
    eye = np.eye(n)
    return [("ainv_vs_golden",) + direct,
            ("a_ainv_vs_identity", prod.ravel(), eye.ravel())]


def layer1_diagnostics(case_arrays, dut_out):
    """spotri 无诊断项。"""
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

三层判定（spec §2.3/§2.3′，criteria/verdict.judge 的同语义副本）：layer1 逐元素混合
容差双门（主套任务书 rtol 2^-10 / atol 2^-16，标准表 2^-13 对照，门结论分歧记 T3；
max_abs 上限 1e-2 与 32·ULP 双解释，解释分歧记 T4）→ 不过或解释分歧 → __KIND__
残差兜底终审（阈值 = __FORMULA__，ratio_cpu 逐 case 随包）。
__OP__ 主判目标：__TARGET_DOC__

用法：
    python3 verify_accuracy.py --package <包目录> --dut-out <被测输出目录> \
        [--report <json>] [--case-id <id> ...]

输入契约（spec §2.2/§2.5）：包内 cases/index.json 与 cases/*.npz；被测输出
<dut-out>/<case_id>.npz 含 out32、info、status。

身份声明（设计 v2 §3.3）：本件是自测辅助件，输出**不构成验收证据**；数值判定不是
正式结论（formal 恒 PENDING_RULING，T1/T3/T4 未裁），正式裁决由 accept 用自带判据
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


def _as_f64(name, arr):
    """严校验升型（残差接口用）：复数、非数值、非 2 维、NaN/Inf 一律抛异常。"""
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


def _to_f64(name, arr):
    """宽松升型（layer1 取数用）：不查有限性（NaN/Inf 流进统计计为不符）。"""
    a = np.asarray(arr)
    if np.issubdtype(a.dtype, np.complexfloating):
        raise TypeError(f"{name}: 复数不在本片范围（spec §1 不做复数）")
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
    """半三角镜像成全对称阵（输入必须是另侧已置 0 的半三角，含对角）。"""
    return half + half.T - np.diag(np.diag(half))


def mirror_storage_side(a, uplo):
    """按 uplo 取存储侧半三角并镜像成全对称阵（对侧 stale 不能用的统一口径）。"""
    return _mirror_half(_half(np.asarray(a), uplo))


def _sym_norm1_from_half(half):
    """DLANSY('1') 口径的对称 1-范数：半三角镜像后取最大列绝对和。"""
    full = _mirror_half(half)
    return float(np.max(np.sum(np.abs(full), axis=0)))


def _get(case_arrays, key):
    if key not in case_arrays:
        raise KeyError(f"case_arrays 缺 {key}（{OP} 卡需要，spec §2.2）")
    return case_arrays[key]


def _tri_pair(case_arrays, dut_out):
    """存储侧半三角逐元素对（vs golden32）：返回 (actual64, golden64) 一维向量。"""
    golden = _to_f64("golden32", _get(case_arrays, "golden32"))
    out = _to_f64("out32", dut_out["out32"])
    n = _square_like("out32", "golden32", golden, out)
    uplo = normalize_uplo(_get(case_arrays, "uplo"))
    idx = np.tril_indices(n) if uplo == "L" else np.triu_indices(n)
    return out[idx], golden[idx]


__OP_BLOCK__


# ---------------------------------------------------------------------------
# 数值裁决（criteria/verdict.judge 的同语义副本；流转见 spec §2.3/§2.3′）
# ---------------------------------------------------------------------------


def _null_fallback():
    return {"ran": False, "ratio": None, "threshold": None, "formula": None, "pass": None}


def _error_verdict(msg):
    return {
        "layer1": None,
        "fallback": _null_fallback(),
        "numeric": "FAIL",
        "formal": "PENDING_RULING",
        "flags": [],
        "error": str(msg),
    }


def _layer1_stats(actual, golden, rtol, atol):
    err = np.abs(actual - golden)
    tol = atol + rtol * np.abs(golden)
    ok = err <= tol
    n_total = int(err.size)
    n_bad = n_total - int(np.count_nonzero(ok))
    matched_ratio = 1.0 - n_bad / n_total
    max_abs = float(np.max(err)) if bool(np.isfinite(err).all()) else float("inf")
    return matched_ratio, max_abs


def _gate_pair(matched_ratio, max_abs):
    ratio_ok = matched_ratio >= REQUIRED_MATCHED_RATIO
    return (
        bool(ratio_ok and max_abs <= MAX_ABS_FIXED),
        bool(ratio_ok and max_abs <= MAX_ABS_ULP32),
    )


def _target_entry(name, actual, golden):
    mr_tb, max_abs = _layer1_stats(actual, golden, TASKBOOK_RTOL_FP32, TASKBOOK_ATOL_FP32)
    tb_fixed, tb_ulp = _gate_pair(mr_tb, max_abs)
    mr_std, _ = _layer1_stats(actual, golden, STANDARD_RTOL_FP32, STANDARD_ATOL_FP32)
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


def _diagnostics(case_arrays, dut_out):
    try:
        items = layer1_diagnostics(case_arrays, dut_out)
    except Exception as exc:
        return [{"name": "diagnostics", "error": f"{type(exc).__name__}: {exc}"}]
    out = []
    for item in items:
        name = item[0]
        try:
            _, actual, golden = item
            mr, max_abs = _layer1_stats(
                actual, golden, TASKBOOK_RTOL_FP32, TASKBOOK_ATOL_FP32)
            out.append({"name": name, "matched_ratio": float(mr), "max_abs": max_abs})
        except Exception as exc:
            out.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
    return out


def _run_fallback(case_arrays, dut_out):
    status = case_arrays.get("ratio_cpu_status")
    ratio_cpu = case_arrays.get("ratio_cpu")
    if status != "ok" or ratio_cpu is None:
        return (
            _null_fallback(),
            "FAIL",
            f"兜底不可用: ratio_cpu_status={status!r}, ratio_cpu={ratio_cpu!r}"
            "（spec §2.4：准备失败该 case 兜底不可用）",
        )
    threshold = float(fallback_threshold(ratio_cpu))
    formula = FALLBACK_FORMULA
    try:
        ratio = residual_ratio(**fallback_kwargs(case_arrays, dut_out))
    except Exception as exc:
        fb = {"ran": True, "ratio": None, "threshold": threshold,
              "formula": formula, "pass": False}
        return fb, "FAIL", f"fallback 残差不可计算: {type(exc).__name__}: {exc}"
    ok = bool(ratio <= threshold)
    fb = {"ran": True, "ratio": float(ratio), "threshold": threshold,
          "formula": formula, "pass": ok}
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
        "max_abs_limits": {"fixed": MAX_ABS_FIXED,
                           "ulp32": MAX_ABS_ULP32},
        "pass_fixed": pass_fixed,
        "pass_ulp": pass_ulp,
        "targets": entries,
        "standard": {"matched_ratio": min(e["standard"]["matched_ratio"] for e in entries),
                     "pass_fixed": std_fixed, "pass_ulp": std_ulp},
        "diagnostics": _diagnostics(case_arrays, dut_out),
    }

    flags = []
    if any((e["pass_fixed"], e["pass_ulp"])
           != (e["standard"]["pass_fixed"], e["standard"]["pass_ulp"]) for e in entries):
        flags.append("T3")
    if any(e["pass_fixed"] != e["pass_ulp"] for e in entries):
        flags.append("T4")

    if pass_fixed and pass_ulp:
        fallback, numeric, error = _null_fallback(), "PASS", None
    else:
        fallback, numeric, error = _run_fallback(case_arrays, dut_out)

    return {"layer1": layer1, "fallback": fallback, "numeric": numeric,
            "formal": "PENDING_RULING", "flags": flags, "error": error}


def judge(case_arrays, dut_out):
    """数值裁决副本：judge(case_arrays, dut_out) -> verdict（结构同 criteria/verdict.judge）。

    judge 不外抛异常：任何内部异常收敛为 error 字段 + numeric FAIL（fail-closed）；
    error 非空的 FAIL 属「不可裁/证据问题」，不得当精度 FAIL 上报。
    """
    try:
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


_OP_SPECS = {
    "spotrf": {
        "kind": "DPOT01",
        "formula": thresholds.TIGHTEN_FORMULA,
        "threshold_fn": thresholds.tighten_threshold,
        "target_doc": "还原 recon（L·Lᵀ/Uᵀ·U）取指定三角对原 A（A64）；诊断并报 F vs golden F。",
        "block": _SPOTRF_BLOCK,
    },
    "spotrs": {
        "kind": "DPOT02",
        "formula": thresholds.FLOATUP_FORMULA,
        "threshold_fn": thresholds.floatup_threshold,
        "target_doc": "解矩阵 X 全量逐元素 vs golden32。",
        "block": _SPOTRS_BLOCK,
    },
    "spotri": {
        "kind": "DPOT03",
        "formula": thresholds.TIGHTEN_FORMULA,
        "threshold_fn": thresholds.tighten_threshold,
        "target_doc": "双目标——A⁻¹ 直审（存储侧 vs golden32）及 A·A⁻¹ 对 I，全部通过才算过。",
        "block": _SPOTRI_BLOCK,
    },
}

_LEFTOVER_TOKEN = re.compile(r"__[A-Z][A-Z_]*__")


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


def render(op):
    """渲染一个算子的两个副本，返回 {文件名: 文本}；输出是输入的纯函数（确定性）。"""
    spec = _OP_SPECS.get(op)
    if spec is None:
        raise ValueError(f"未知算子 {op!r}，本片只支持 {tuple(_OP_SPECS)}（spec §1）")
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
        ("__OP__", op),
    ]
    files = {}
    for fname, template in (("verify_accuracy.py", _ACCURACY_TEMPLATE),
                            ("verify_perf.py", _PERF_TEMPLATE)):
        text = template
        for token, value in subs:
            text = text.replace(token, value)
        leftover = _LEFTOVER_TOKEN.search(text)
        if leftover:
            raise AssertionError(f"{fname}: 模板残留未替换 token {leftover.group(0)!r}")
        files[fname] = text
    return files


def main(argv=None):
    ap = argparse.ArgumentParser(description="verify 双件确定性渲染入口（spec §2.5）")
    ap.add_argument("--op", required=True, choices=sorted(_OP_SPECS),
                    help="算子名（canonical s 前缀）")
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
