"""精度口径 SSOT（T5）——三标准（AscendOpTest 默认 / 生态 MERE·MARE / exact）唯一真源。

ADR 0005（canonical）：精度验收是**三层口径、非三选一**——放行只看 acceptance。
本文件只承载「标准常量 + 路由 + 误差分布复算（compute_metrics，用 numpy）」；
**judge（比阈值出 pass/fail）在 validator.py 用纯算术**（保 validator stdlib-only）。
`compute_metrics` 里的 numpy 为**惰性 import**（函数体内），故 `import precision_policy` 本身不拉 numpy，
validator 可安全 `import precision_policy` 而不引入 numpy 依赖。

--------------------------------------------------------------------------------
标准一 · ascendoptest_default —— 平台层实体（AscendOpTest 默认阈值）
--------------------------------------------------------------------------------
provenance（verified，本地 clone 指纹，勿凭记忆改）：
  repos/AscendOpTest/compare/compare/accuracy_config.py
    sha256 = 3f439ff320ac483463c184ee1b6792a3c5ae8092af57da2d55571cf24146f91b
  repos/AscendOpTest/compare/compare/compare.py
    sha256 = be009ab5824d13dddbc1bfeec12f40e2959611f6bbf7bcb7560d1e035b015b33

`default_acc` 整表逐字快照（15 dtype，每项 `[tolerance, error_rate, legacy]`；
error_rate 是**第 2 位**、逐 dtype 变；第 3 位 legacy=0.1 代码不读）：
    float     : [0.0001, 0.0001, 0.1]
    float32   : [0.0001, 0.0001, 0.1]
    float64   : [0.0001, 0.0001, 0.1]
    int32     : [0.0001, 0.0001, 0.1]
    int64     : [0.0001, 0.0001, 0.1]
    float16   : [0.001,  0.001,  0.1]
    bfloat16  : [0.004,  0.004,  0.1]
    hfloat32  : [0.001,  0.001,  0.1]
    bool      : [0.0,    0.0,    0.1]
    uint8     : [1,      0.01,   0.1]
    uint32    : [1,      0.01,   0.1]
    int8      : [1,      0.001,  0.1]
    int16     : [1,      0.001,  0.1]
    complex64 : [0.0001, 0.0001, 0.1]
    complex128: [0.0001, 0.0001, 0.1]

`compare_default` 掩码语义（逐 dtype 精确复刻，与 MERE/MARE 公式**分开实现**）：
  - minimum = 10e-10 = 1e-9；maxmin = max(|expect|, |output|) + 1e-9（防除零）。
  - `|expect| >= 1` 用相对误差 `|out-exp|/maxmin <= tolerance`；`|expect| < 1` 用绝对误差
    `|out-exp| <= tolerance`（二者**共用同一 tolerance**）。
  - inf → finfo.max（+inf→finfo.max，-inf→-finfo.max，按数组**原生 dtype** 的 finfo）。
  - NaN==NaN 视为通过（both_nan → valid）。
  - 仅当 `bad_count > numel * error_rate` 才整体 fail（否则通过）。
  - bool 输出：AscendOpTest 里 astype(uint8) 后走 default（tol=0,err=0 → 等价 exact）；
    **本项目 bool 输出统一走 `exact` 标准**（见下），default 表仅作完整快照。

--------------------------------------------------------------------------------
标准二 · ecosystem_mere_mare —— 生态《算子开源精度标准》(proposed / NOT_SETTLED)
--------------------------------------------------------------------------------
⚠ 来自 `canon/architecture/ecosystem-precision-standard.md`（status=proposed，**非事实、未 settle**），
一手出自 cann/opbase `experimental_standard.md`。全部常量打 NOT_SETTLED=True。
  - MERE = 平均相对误差 = avg( |actual-golden| / (|golden|+1e-7) )   —— **平均**
  - MARE = 最大相对误差 = max( |actual-golden| / (|golden|+1e-7) )   —— **最大**
    （⚠ MERE=平均、MARE=最大，务必**不对调**）
  - 通过 = `MERE < Th` 且 `MARE < 10 × Th`；分母 eps = 1e-7。
  - Th 按 dtype（2^-k）：fp16=2^-10 / bf16=2^-7 / fp32=2^-13 / hfloat32=2^-11 /
    fp8_e4m3=2^-3 / fp8_e5m2=2^-2。
  - **单标杆不过 → 记 needs_review（不自动 fail）**；ATK 双标杆 fallback 本轮**不实现**、out-of-scope。

--------------------------------------------------------------------------------
标准三 · exact —— bool / 逐位精确（threshold=0，mismatch<=0 才过）
--------------------------------------------------------------------------------
"""

# ---- 标准名（受控词表） ----
ASCENDOPTEST_DEFAULT = "ascendoptest_default"
ECOSYSTEM_MERE_MARE = "ecosystem_mere_mare"
EXACT = "exact"
BEHAVIORAL = "behavioral"  # 无数值输出（Sleep 类）：精度维度 na，无标准
STANDARDS = (ASCENDOPTEST_DEFAULT, ECOSYSTEM_MERE_MARE, EXACT, BEHAVIORAL)

# ---- AscendOpTest 常量（完整 15 dtype 快照，逐字见文件头 provenance） ----
_AOT_EPS = 1e-9  # compare.py minimum = 10e-10
_AOT_TABLE = {                         # dtype: [tolerance, error_rate, legacy]
    "float":      [0.0001, 0.0001, 0.1],
    "float32":    [0.0001, 0.0001, 0.1],
    "float64":    [0.0001, 0.0001, 0.1],
    "int32":      [0.0001, 0.0001, 0.1],
    "int64":      [0.0001, 0.0001, 0.1],
    "float16":    [0.001,  0.001,  0.1],
    "bfloat16":   [0.004,  0.004,  0.1],
    "hfloat32":   [0.001,  0.001,  0.1],
    "bool":       [0.0,    0.0,    0.1],
    "uint8":      [1,      0.01,   0.1],
    "uint32":     [1,      0.01,   0.1],
    "int8":       [1,      0.001,  0.1],
    "int16":      [1,      0.001,  0.1],
    "complex64":  [0.0001, 0.0001, 0.1],
    "complex128": [0.0001, 0.0001, 0.1],
}

# ---- 生态 MERE/MARE 常量（proposed，全 NOT_SETTLED） ----
_MM_NOT_SETTLED = True
_MM_STATUS = "proposed"
_MM_MAX_RATIO = 10          # MARE < 10 × Th
_MM_EPS = 1e-7              # 分母 |golden| + 1e-7
_MM_TH_EXP = {             # Th = 2 ** -exp
    "float16": 10, "bfloat16": 7, "float32": 13,
    "hfloat32": 11, "fp8_e4m3": 3, "fp8_e5m2": 2,
}
_MM_PROVENANCE = ("canon/architecture/ecosystem-precision-standard.md (proposed) · "
                  "cann/opbase experimental_standard.md")

# ---- 可复算 dtype 支持矩阵（float64 直算，覆盖精度维极小用例；bf16/fp8/complex 未支持→fail-fast） ----
SUPPORTED_COMPUTE_DTYPES = frozenset({
    "float16", "float32", "float64", "float",
    "int8", "int16", "int32", "int64", "uint8", "uint32", "bool",
})


# ================================================================= 路由 =====
def select_standard(spec):
    """从 spec 选标准：显式 `precision.standard` 优先；缺失按 oracle + verify_mode 映射（向后兼容旧 spec）。

    映射：exact verify_mode → exact；behavioral → behavioral；
         numerical + oracle(ascendoptest/缺) → ascendoptest_default；numerical + mere_mare → ecosystem_mere_mare。
    """
    prec = spec.get("precision") or {}
    std = prec.get("standard")
    if std:
        if std not in STANDARDS:
            raise ValueError(f"未知 precision.standard={std!r}，仅 {list(STANDARDS)}")
        return std
    vmode = spec.get("verify_mode")
    if vmode == "exact":
        return EXACT
    if vmode == "behavioral":
        return BEHAVIORAL
    if vmode == "numerical":
        oracle = prec.get("oracle")
        if oracle in ("mere_mare", "atk_double"):
            return ECOSYSTEM_MERE_MARE
        return ASCENDOPTEST_DEFAULT  # ascendoptest / none / 缺省
    raise ValueError(f"无法映射 standard：verify_mode={vmode!r}")


def compare_dtype(case_or_golden):
    """解析比对 dtype——按 **golden/输出 dtype**（非输入 dtype，防 bool/int8→int32 输出误判）。

    入参可为 numpy 数组（取 .dtype.name）或 caseset case 字典（读 expected.compare_dtype，
    退回 io==out 的输出 dtype）。**无输入 dtype 兜底**（finding #4）：输入 dtype 常与输出不同
    （Equal fp32→bool），退回它会违背「按 golden/输出 dtype」的口径 → 无 golden/output dtype 时 fail-fast。
    """
    dt = getattr(case_or_golden, "dtype", None)
    if dt is not None:
        return dt.name
    if isinstance(case_or_golden, dict):
        exp = case_or_golden.get("expected") or {}
        if exp.get("compare_dtype"):
            return exp["compare_dtype"]
        outs = [p for p in case_or_golden.get("inputs", []) if p.get("io") == "out"]
        if outs and outs[0].get("dtype"):
            return outs[0]["dtype"]
    raise ValueError("无法解析 compare_dtype：需 numpy 数组、或含 expected.compare_dtype / "
                     "io=out 输出 dtype 的 case（不退回输入 dtype——见 finding #4）")


def _check_compute_supported(dtype):
    if dtype not in SUPPORTED_COMPUTE_DTYPES:
        raise ValueError(f"未支持复算的 dtype={dtype!r}（SUPPORTED_COMPUTE_DTYPES="
                         f"{sorted(SUPPORTED_COMPUTE_DTYPES)}）——fail-fast，不静默")


def threshold_for(standard, dtype):
    """返回结构化 policy dict（含 kind + 判据常量）。未支持 dtype **fail-fast**（不静默兜底）。"""
    if standard == EXACT:
        return {"kind": EXACT, "max_mismatch": 0, "not_settled": False}
    if standard == BEHAVIORAL:
        return {"kind": BEHAVIORAL, "not_settled": False}
    if standard == ASCENDOPTEST_DEFAULT:
        if dtype not in _AOT_TABLE:
            raise ValueError(f"ascendoptest_default 无 dtype={dtype!r} 阈值（表={list(_AOT_TABLE)}）")
        _check_compute_supported(dtype)
        tol, err, legacy = _AOT_TABLE[dtype]
        return {"kind": ASCENDOPTEST_DEFAULT, "tolerance": tol, "error_rate": err,
                "eps": _AOT_EPS, "legacy": legacy, "not_settled": False}
    if standard == ECOSYSTEM_MERE_MARE:
        if dtype not in _MM_TH_EXP:
            raise ValueError(f"ecosystem_mere_mare 无 dtype={dtype!r} 的 Th（表={list(_MM_TH_EXP)}）")
        _check_compute_supported(dtype)
        th = 2.0 ** (-_MM_TH_EXP[dtype])
        return {"kind": ECOSYSTEM_MERE_MARE, "threshold": th, "max_ratio": _MM_MAX_RATIO,
                "eps": _MM_EPS, "not_settled": _MM_NOT_SETTLED, "status": _MM_STATUS,
                "provenance": _MM_PROVENANCE}
    raise ValueError(f"未知 standard={standard!r}")


def tolerance_policy_id(standard, dtype):
    """口径唯一 id：exact 与 dtype 无关；其余带 dtype（承 acceptance-contract-evidence-chain）。"""
    if standard in (EXACT, BEHAVIORAL):
        return standard
    return f"{standard}:{dtype}"


# ---- 整数 dtype 判定 + per-case 有效标准（T7 dtype 扩面） ----
_INTEGER_DTYPES = frozenset({"int8", "int16", "int32", "int64",
                             "uint8", "uint16", "uint32", "uint64"})


def is_integer_dtype(name):
    """按 dtype 名判整数（含无符号）——rule-catalog §1.1『int→exact_equal』的判据。"""
    return name in _INTEGER_DTYPES


def effective_standard(spec_standard, cdtype, compare=None):
    """per-case **有效标准**（T7；rule-catalog §1.1 + canonical harness 职责）——只会**收紧**、绝不放宽。

    - spec 已是 exact/behavioral → 原样（per-case 无权改）。
    - 数值 spec 但 case 的比对 dtype 是**整数** → 强制 EXACT（§1.1，**不可绕过**：即便 caseset 声明别的，
      validator 据 cdtype 复算也会得 EXACT，同步放宽无效）。
    - 数值 spec 且 case 显式 `compare=="exact_equal"`（算子输出在该 dtype 网格上精确可表示，如 Sign/Neg 的
      bf16/fp16）→ EXACT。这是**更严**方向（阈值 0），故安全；反向（把 exact 降级为数值）本函数不提供。
    - 其余 → 沿用 spec 标准（fp32/fp16 数值不变，**向后兼容**）。

    ⚠ bf16 走此路时 cdtype 记逻辑名 'bfloat16'（非整数）→ 只能靠 compare=='exact_equal' 收紧；若误标
    compare!='exact_equal'，下游 threshold_for(spec_standard,'bfloat16') 会 fail-fast，不会静默放行。
    """
    if spec_standard in (EXACT, BEHAVIORAL):
        return spec_standard
    if cdtype is not None and is_integer_dtype(cdtype):
        return EXACT
    if compare == "exact_equal":
        return EXACT
    return spec_standard


def threshold_digest(policy):
    """向后兼容的标量阈值 digest（旧 gate/spec 的 precision.threshold 语义）。"""
    kind = policy.get("kind")
    if kind == EXACT:
        return 0
    if kind == ASCENDOPTEST_DEFAULT:
        return policy["tolerance"]
    if kind == ECOSYSTEM_MERE_MARE:
        return policy["threshold"]
    if kind == BEHAVIORAL:
        return 0
    raise ValueError(f"无法为 policy.kind={kind!r} 出 digest")


# ============================================================ 误差分布复算 ===
def _replace_inf(arr):
    """镜像 compare.py replace_inf：float 型 ±inf → ±finfo.max（按原生 dtype）；整型原样返回。"""
    import numpy as np
    if np.issubdtype(arr.dtype, np.floating):
        finfo = np.finfo(arr.dtype)
        arr = arr.copy()
        arr[np.isposinf(arr)] = finfo.max
        arr[np.isneginf(arr)] = -finfo.max
        return arr
    return arr


def compute_metrics(out, golden, policy):
    """采集层复算误差分布（numpy，惰性 import）——**只量误差、不判 pass/fail**（judge 在 validator）。

    入口统一（finding #1）：`o=asarray(out).reshape(-1)` / `g=asarray(golden).reshape(-1)`，
    size 不等 **fail-fast**（对齐 compare.py `reshape(-1)` + 长度不等直接 compare failed）。
    dtype 校验（finding #2）：数值口径（会 astype float64）遇 complex/bf16/fp8 等未支持 dtype 直接
    `ValueError`——**绝不静默**（complex→float64 会丢虚部返 0 误差，是假通过温床）。

    按 policy.kind 分开实现：
      - ascendoptest_default → 复刻掩码：{bad_count, numel, max_abs_err, max_rel_err, nan_pair_count}
      - ecosystem_mere_mare  → {mere, mare, numel}（denom |g|+1e-7；MERE=平均/MARE=最大）
      - exact                → {exact_mismatch, numel}
    """
    import numpy as np
    kind = policy.get("kind")
    o = np.asarray(out).reshape(-1)
    g = np.asarray(golden).reshape(-1)
    if o.size != g.size:                      # finding #1：长度不等 fail-fast（对齐 compare.py）
        raise ValueError(f"compute_metrics: out.size={o.size} != golden.size={g.size}"
                         f"（长度不等，对齐 compare.py 直接判失败，不静默）")

    if kind == EXACT:
        # exact 用 != 逐位比，对 complex/bf16 无信息损失 → 不做数值 dtype 限制。
        return {"exact_mismatch": int(np.count_nonzero(o != g)), "numel": int(g.size)}

    if kind == BEHAVIORAL:
        return {"numel": int(g.size)}

    # 数值口径（下均 astype(float64)）：dtype 必须受支持——complex/bf16/fp8 等在此 fail-fast（finding #2）。
    _check_compute_supported(g.dtype.name)

    if kind == ASCENDOPTEST_DEFAULT:
        tol = float(policy["tolerance"])
        eps = float(policy.get("eps", _AOT_EPS))
        if np.issubdtype(g.dtype, np.integer):
            # finding #3 取舍：**整数按原 dtype 复刻**（与 compare.py `np.abs(output-expect)` 语义一致，
            # 保留原整型减法/取绝对值的**溢出回绕**；int8 127-(-128) 回绕，abs(-128) 亦回绕）。
            # ⚠ 显式偏离说明：此路径**不**转 float64 再算 diff（float64 不会溢出、会与 compare.py 分道），
            #   仅在最终相对误差比值处借 float64 除法（compare.py 的 result/maxmin 亦是 float64 真除）。
            diff = np.abs(o - g)                         # 原整型：溢出回绕同 compare.py
            abs_g = np.abs(g)                            # 原整型：abs(最小负值) 回绕，用于 >=1 分支同 compare.py
            atol_ok = diff <= tol
            maxmin = np.maximum(abs_g, np.abs(o)).astype(np.float64) + eps
            rel = diff.astype(np.float64) / maxmin
            rtol_ok = rel <= tol
            valid = np.where(abs_g >= 1, rtol_ok, atol_ok)
            bad_count = int(np.count_nonzero(~valid))
            return {"bad_count": bad_count, "numel": int(g.size),
                    "max_abs_err": float(diff.max()) if diff.size else 0.0,
                    "max_rel_err": float(rel.max()) if rel.size else 0.0,
                    "nan_pair_count": 0}      # 整数无 NaN
        # 浮点：float64 精算 + inf/nan 复刻
        o64 = _replace_inf(o).astype(np.float64)
        g64 = _replace_inf(g).astype(np.float64)
        diff = np.abs(o64 - g64)
        atol_ok = diff <= tol
        maxmin = np.maximum(np.abs(g64), np.abs(o64)) + eps
        rel = diff / maxmin
        rtol_ok = rel <= tol
        valid = np.where(np.abs(g64) >= 1, rtol_ok, atol_ok)
        both_nan = np.isnan(o64) & np.isnan(g64)
        valid = valid | both_nan                          # NaN==NaN 视为通过（同 compare.py）
        bad_count = int(np.count_nonzero(~valid))
        # finding #5：both-NaN（及单侧 NaN 造成的 nan diff）会把 max_abs/max_rel 污染成 nan → 只在**有限**
        #   位置取诊断 max（inf 已被 _replace_inf 换掉，故非有限 = NaN 位置）；both_nan 计数显式返回。
        finite = np.isfinite(diff)
        return {"bad_count": bad_count, "numel": int(g64.size),
                "max_abs_err": float(diff[finite].max()) if finite.any() else 0.0,
                "max_rel_err": float(rel[finite].max()) if finite.any() else 0.0,
                "nan_pair_count": int(np.count_nonzero(both_nan))}

    if kind == ECOSYSTEM_MERE_MARE:
        eps = float(policy.get("eps", _MM_EPS))
        o64 = o.astype(np.float64)
        g64 = g.astype(np.float64)
        rel = np.abs(o64 - g64) / (np.abs(g64) + eps)
        return {"mere": float(rel.mean()) if rel.size else 0.0,
                "mare": float(rel.max()) if rel.size else 0.0,
                "numel": int(g64.size)}

    raise ValueError(f"compute_metrics 未知 policy.kind={kind!r}")
