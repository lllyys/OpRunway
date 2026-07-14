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
        # 显式白名单（fail-closed）：只有 {ascendoptest, none, 缺省} 才映射默认标准。其余 oracle（如
        # torch/scipy/std_exact 这类「与 python 一致」）一律 raise，堵 class C 静默降级为 ascendoptest_default。
        if oracle in ("ascendoptest", "none", None):
            return ASCENDOPTEST_DEFAULT
        raise ValueError(
            f"未验证过的 precision.oracle={oracle!r} 的精度标准——拒绝静默降级为 ascendoptest_default。"
            f"已知映射：{{ascendoptest,none,缺省}}→ascendoptest_default、"
            f"{{mere_mare,atk_double}}→ecosystem_mere_mare。"
            f"请在 spec 显式声明 precision.standard，或由 agent 自行探索/询问用户后再纳入白名单。")
    raise ValueError(f"无法映射 standard：verify_mode={vmode!r}")


def compare_dtype(case_or_golden):
    """解析比对 dtype——按 **golden/输出 dtype**（非输入 dtype，防 bool/int8→int32 输出误判）。

    ⚠ 仅用于**采集层**已持有真实 golden 数组时取 `.dtype.name`。**裁决层严禁**用它从 caseset
    自声明（expected.compare_dtype / io=out）取 cdtype——那是攻击者可控输入，会让「据 spec 复算」退化成
    「据攻击者输入复算」（effective-standard-security finding #1/#2）。裁决层一律走 `derive_output_dtype`。
    """
    dt = getattr(case_or_golden, "dtype", None)
    if dt is not None:
        return dt.name
    raise ValueError("compare_dtype 仅接受 numpy 数组（真实 golden）；据 spec 派生请用 derive_output_dtype")


def derive_output_dtype(spec, case_input_dtypes):
    """**据 spec IO 矩阵**（非 caseset 自声明）派生该 case 的输出/比对 dtype——裁决层 cdtype 的**唯一合法来源**。

    核心原则（effective-standard-security）：凡决定「怎么判」的 dtype，一律从 spec 派生；caseset 的
    `expected.compare_dtype` / `tolerance_policy_id` 后缀只能作「待核对断言」，**绝不作派生输入**。

    `case_input_dtypes`：`[(name, dtype), ...]`（取自 caseset.inputs，仅作断言）。逐条校验：
      · name ∈ spec `io=='in'` 参数；dtype ∈ 该参数允许集（IO schema，finding #5）——不符 ValueError。
    输出 dtype 规则：
      · in_dt ∈ out 参数允许集 → 同 dtype elementwise（Sign/Neg，out dtype==in dtype）；
      · out 允许集为单值（如 bool：IsClose/Equal 固定 bool 输出）→ 取该单值；
      · 否则歧义 → ValueError（保守拒绝，不猜）。
    多输入须同 dtype（elementwise 前提）；不一致 → ValueError。gen_cases 与 validator **共用本函数**，
    保证「造用例的 compare_dtype」与「裁决派生的 cdtype」同源、绝不漂移。
    """
    params = spec.get("params") if isinstance(spec, dict) else None
    if not isinstance(params, list) or not params:
        raise ValueError("spec 无 IO 矩阵（params）——无法据 spec 派生输出 dtype，拒绝以 caseset 自声明代替")
    in_params = {p["name"]: p for p in params
                 if isinstance(p, dict) and p.get("io") == "in" and p.get("name")}
    out_params = [p for p in params if isinstance(p, dict) and p.get("io") == "out"]
    if not in_params or not out_params:
        raise ValueError("spec IO 矩阵缺 in/out 参数（无法据 spec 派生输出 dtype）")
    in_dts = []
    for name, dt in case_input_dtypes:
        if name not in in_params:
            raise ValueError(f"case 输入 {name!r} 不在 spec in-参数 {sorted(in_params)}（IO schema 不符）")
        allowed = in_params[name].get("dtype") or []
        if dt not in allowed:
            raise ValueError(f"case 输入 {name} dtype={dt!r} 不在 spec 允许集 {allowed}（IO schema 不符）")
        in_dts.append(dt)
    if not in_dts:
        raise ValueError("case 无有效输入 dtype（无法派生输出 dtype）")
    in_dt = in_dts[0]
    if any(d != in_dt for d in in_dts):
        raise ValueError(f"case 多输入 dtype 不一致 {in_dts}（elementwise 需同 dtype）")
    out_allowed = out_params[0].get("dtype") or []
    if in_dt in out_allowed:
        return in_dt                              # 同 dtype elementwise（Sign/Neg）
    uniq = set(out_allowed)
    if len(uniq) == 1:
        return next(iter(uniq))                   # 固定输出（bool：IsClose/Equal）
    raise ValueError(f"无法据 spec 派生输出 dtype：in={in_dt} out集={out_allowed}（歧义，保守拒绝）")


def resolve_acceptance(spec, standard, dtype):
    """任务书验收目标口径（可选、独立于平台 standard）的 **canonical 复算**——gen_cases 与 validator 共用。

    据 `spec.precision.acceptance_policy`（形如 `{"standard": "ascendoptest_default", "error_rate": 0.1}`：
    以某标准为底 + 覆盖判据字段）复算 canonical (policy, tolerance_policy_id)。
    无声明 / exact·behavioral 标准 → None（acceptance 继承 standard）。

    ⚠ 安全（finding #3）：validator 用本函数据 **spec** 复算 canonical acceptance，要求 caseset/evidence 三处全等；
    **spec 未声明 acceptance → 返回 None → caseset+evidence 一律不得私带 acceptance**（防 T5 原洞在 acceptance 层重演）。
    """
    if standard in (EXACT, BEHAVIORAL):
        return None
    ap = (spec.get("precision") or {}).get("acceptance_policy")
    if not ap:
        return None
    ap_std = ap.get("standard", standard)
    pol = threshold_for(ap_std, dtype)
    for k in ("tolerance", "error_rate", "threshold", "max_ratio", "eps"):
        if k in ap:
            pol[k] = ap[k]
    return pol, tolerance_policy_id(ap_std, dtype)


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


# ---- oracle_source 六枚举（canonical，acceptance-contract-evidence-chain）+ 据 golden_source 据实映射 ----
ORACLE_SOURCES = ("analytical_ref", "cpu_ref", "torch_ref",
                  "catlass_existing_ref", "task_spec_expected", "external_ref")


def oracle_source_from_golden(golden_source):
    """把 caseset.expected.golden_source（造 golden 时记的**真来源串**）据实映射到 canonical oracle_source 六枚举。

    - "torch ..."  → torch_ref（torch CPU 参考）。
    - "numpy ..."  → analytical_ref（按公式的 numpy 参考——语义上是解析参考、**非 cpu_ref**；
                     见 canon oracle-source-is-a-hardcoded-constant 的诚实边界）。
    识别不出来源前缀 → **fail-closed**（ValueError，绝不默认 cpu_ref）。新增来源须显式纳入本映射。
    """
    s = (golden_source or "").strip().lower()
    first = s.split(None, 1)[0] if s.split() else ""     # 严格首 token，避免 "torchvision"/"numpyish" 误判
    if first == "torch":
        return "torch_ref"
    if first == "numpy":
        return "analytical_ref"
    raise ValueError(
        f"无法从 golden_source={golden_source!r} 映射 oracle_source —— 已知前缀 "
        f"torch→torch_ref / numpy→analytical_ref；新来源须显式纳入映射（fail-closed，不默认 cpu_ref）。")


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
    dtype 校验（finding #2 + pv-4）：**out 与 golden 双侧 dtype 都校验**（旧洞：只校 golden 侧 →
    out=complex64 时 `_replace_inf(o).astype(float64)` 静默丢虚部返 bad_count=0；out=uint8 与 golden=bool
    跨型 `!=` 值相等返 exact_mismatch=0，都是假通过温床）。
      · 数值口径（会 astype float64）：两侧都须 ∈ SUPPORTED_COMPUTE_DTYPES 且 **out.dtype == golden.dtype**，
        任一侧 complex/bf16/fp8 或两侧不一致 → `ValueError` fail-fast。
      · exact 口径：要求 **out.dtype == golden.dtype**（逐位比按同一 dtype），拒 uint8 与 bool 等跨型相等。
    （合法产物两侧本就同 dtype——采集层 out=golden.copy()（mock）或 bool→astype(bool)/numerical 同 _NP[dtn]
    （真机），四算子实测 on-disk 组合恒为 fp32/fp32·fp16/fp16·bool/bool，无 uint8↔bool，故严等不误伤。）

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
        # exact 用 != 逐位比：**同一 dtype** 下对 complex/bf16 亦无信息损失，故不限制具体 dtype，
        # 但 pv-4：**两侧 dtype 必须一致**——拒 uint8 与 bool 跨型逐位比（numpy `uint8([1,0]) != bool([T,F])`
        # 做值提升后相等 → exact_mismatch=0 假通过；uint8 含值 2 vs bool 亦不该被判等价）。
        if o.dtype != g.dtype:
            raise ValueError(f"exact 口径 out/golden dtype 不一致：out={o.dtype.name} golden={g.dtype.name}"
                             "（拒跨型逐位比，如 uint8 与 bool 会值相等假通过）——fail-fast，不静默")
        return {"exact_mismatch": int(np.count_nonzero(o != g)), "numel": int(g.size)}

    if kind == BEHAVIORAL:
        return {"numel": int(g.size)}

    # 数值口径（下均 astype(float64)）：**out 与 golden 双侧** dtype 都须受支持且一致——complex/bf16/fp8
    # 或跨型不一致在此 fail-fast（finding #2 + pv-4）。旧洞：只校 golden 侧，out=complex64 时
    # `_replace_inf(o).astype(float64)` 静默丢虚部 → bad_count=0 假通过。out 侧先校（给 complex-out 更精确的错）。
    _check_compute_supported(o.dtype.name)   # pv-4：out 侧（complex/bf16/fp8 out 在此 fail-fast）
    _check_compute_supported(g.dtype.name)   # golden 侧（既有）
    if o.dtype != g.dtype:
        raise ValueError(f"数值口径 out/golden dtype 不一致：out={o.dtype.name} golden={g.dtype.name}"
                         "（两侧须同 dtype，防 out=complex/错位 dtype 在 float64 化时丢信息假通过）——fail-fast")

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
