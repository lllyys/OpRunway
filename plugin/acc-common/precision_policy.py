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

# 顶层只允许 **stdlib**——本模块的不变量是「`import precision_policy` 不拉 numpy」
# （validator 靠它保持 stdlib-only）。numpy 仍在 compute_metrics 等函数体内惰性 import，勿上提。
import hashlib
import math
import os
import re

# ---- 标准名（受控词表） ----
ASCENDOPTEST_DEFAULT = "ascendoptest_default"
ECOSYSTEM_MERE_MARE = "ecosystem_mere_mare"
EXACT = "exact"
BEHAVIORAL = "behavioral"  # 无数值输出（Sleep 类）：精度维度 na，无标准
TORCH_ALLCLOSE = "torch_allclose"  # torch-对标场景：任务书指定 torch 为真值口径 → |o-g|<=atol+rtol*|g|，容错率=0
STANDARDS = (ASCENDOPTEST_DEFAULT, ECOSYSTEM_MERE_MARE, EXACT, BEHAVIORAL, TORCH_ALLCLOSE)

# ---- policy.kind（≠ spec-level standard）：多输出 index 一致性判据 ----
# 不入 STANDARDS——它不是 spec 声明的标准，而是 index 输出的**比对形态**（compare/policy.kind）：
# 下标不逐位比（tie 上 NPU 与 golden 可合法给不同下标），改判 gather(self,idx_npu) allclose gather(self,idx_golden)。
INDEX_VALUE_CONSISTENCY = "index_value_consistency"
FROM_INPUT_SENTINEL = "<from_input>"  # 输出 dtype 随（某个）输入 dtype（如 median.values 随 self）

# ---- out_role 受控词表（多输出契约的输出角色；op-中立、据字段驱动）----
# ⚠ 必须是**受控词表**、不能「非 index 即 value」：审计实证 `out_role="bogus"` 会被当 value 收下，
# 且 index 的 `index_of` 还能合法指向这个伪 value —— 判据链就此建在一个谁也没定义的角色上。
# 空串同理（`out_role=""` 既非缺省也非合法值）。未知/空 → fail-closed（本仓纪律：fail-closed 优于静默兜底）。
OUT_ROLE_VALUE = "value"
OUT_ROLE_INDEX = "index"
OUT_ROLES = (OUT_ROLE_VALUE, OUT_ROLE_INDEX)

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

# ---- torch_allclose 逐 dtype 容差 (rtol, atol) ----
# provenance（adapt，勿凭记忆改）：抄自参考仓 cannbot-ops-input
#   `skills/operator-evaluation/scripts/accuracy.py:47-54` 的 `_ALLCLOSE_TOLS`（该表存 **(atol, rtol)**），
#   一手出自 tilelang2ascend `verification_ascendc.py`。判据 |actual-golden| <= atol + rtol*|golden|。
#   fp16/bf16/fp32 逐字抄参考表；fp64 为**外推**；整型/bool 输出走 exact（不入此表）。
#   ⚠ 本表刻意存 **(rtol, atol)**，与参考仓 (atol, rtol) 顺序相反——下方逐条已按此顺序核对，勿对调。
# ⚠ **complex64/complex128 已移出本表**（审计 finding #9）：`compute_metrics` 的 SUPPORTED_COMPUTE_DTYPES
#   不含 complex（复数 allclose 未实现），留在表里等于「能生成一份永远算不出来的 policy」——声明与实现
#   不一致比缺能力更坏。要支持复数须先实现按模长的 allclose 并同时入 SUPPORTED_COMPUTE_DTYPES。
#   （`_AOT_TABLE` 里的 complex 条目是 AscendOpTest 的**逐字快照**、保留作 provenance；那条路径由
#    `threshold_for` 里的 `_check_compute_supported` 当场 fail-fast，不会产出不可执行 policy。）
_TA_DTYPE_TOLS = {                 # dtype: (rtol, atol)
    "float16":    (2 ** -10, 9e-2),
    "bfloat16":   (2 ** -7,  1e-1),
    "float32":    (2 ** -13, 1e-3),
    "float64":    (2 ** -30, 1e-6),
}
_TA_TORCH_DEFAULT = (1e-5, 1e-8)   # torch.allclose 缺省 (rtol=1e-5, atol=1e-8)
TOLERANCE_SOURCES = ("dtype_table", "taskdoc", "torch_default")  # rtol/atol 权威来源（spec.precision.tolerance_source）

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
        # torch-对标：任务书把 torch 指定为真值口径 → torch_allclose（容差按 tolerance_source 分源，见 threshold_for）。
        if oracle == "torch":
            return TORCH_ALLCLOSE
        # 显式白名单（fail-closed）：只有 {ascendoptest, none, 缺省} 才映射默认标准。其余 oracle（如
        # scipy/std_exact 这类「与 python 一致」但未验证的）一律 raise，堵 class C 静默降级为 ascendoptest_default。
        if oracle in ("ascendoptest", "none", None):
            return ASCENDOPTEST_DEFAULT
        raise ValueError(
            f"未验证过的 precision.oracle={oracle!r} 的精度标准——拒绝静默降级为 ascendoptest_default。"
            f"已知映射：{{ascendoptest,none,缺省}}→ascendoptest_default、"
            f"{{mere_mare,atk_double}}→ecosystem_mere_mare、torch→torch_allclose。"
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


def _value_tol_of(policy):
    """从一个 value 输出的 canonical policy 取 (rtol, atol)，供 index 输出的 value_consistency 复用。

    torch_allclose → (rtol, atol)；exact（int/bf16 逐位）→ (0.0, 0.0)。其余 kind fail-closed（域外、不硬塞）。"""
    kind = policy.get("kind")
    if kind == TORCH_ALLCLOSE:
        return float(policy["rtol"]), float(policy["atol"])
    if kind == EXACT:
        return 0.0, 0.0
    raise ValueError(f"index_value_consistency 无法从 value 输出 policy.kind={kind!r} 取容差"
                     f"（仅 value 输出为 torch_allclose / exact 时可派生 index 判据）")


def derive_output_contracts(spec, case_input_dtypes, spec_standard,
                            tolerance_source=None, taskdoc_tol=None):
    """据 **spec params**（io=='out' + out_role）逐输出派生 canonical 判据契约——**op-中立**。

    只据 `out_role` / `index_of` / `dtype`(可含 `<from_input>`) 字段驱动，**绝无算子名分支**（律令#0）：
    换任意声明「多输出 + torch 对标」的域内算子零改即用。gen_cases 造 caseset.expected.outputs[] 与
    validator 多输出裁决**共用本函数**，保证同源不漂移（同 derive_output_dtype 的纪律）。

    返回按 spec out-param 顺序的列表，每项：
      `{"name", "role", "dtype", "standard", "tolerance_policy_id"(可 None), "policy", "index_of"}`

    派生规则（分两趟：先 value/plain、后 index——index 的容差取自它所引 value 输出）：
      · value/plain 输出：dtype 若 `<from_input>` → 取输入 dtype；`eff_std=effective_standard(spec_standard,dtype)`
        （int→exact、bf16 靠 compare、余=spec 标准）→ `policy=threshold_for(eff_std,dtype,tolerance_source,...)`。
      · index 输出：`policy={kind:index_value_consistency, gather_from:<spec 的 gather_from 字段>,
        value_rtol/atol:<所引 value 容差>}`；`standard` 随所引 value 输出（float→torch_allclose / int→exact）；
        tolerance_policy_id=None（index 无 dtype 阈）。

    ⚠ 输入校验（审计 finding #7 收紧）：case inputs 必须与 spec in-params **完整、唯一、同序同名**——
      旧版只校「⊆ spec in-params」，而 index 的 gather 源当时取「case 的第一个输入」→ 调一下 case.inputs
      的顺序就换掉了 canonical 判据的 gather 源（判据必须只从 spec 派生，不得随攻击者可控输入漂移）。
      现在 gather 源改由 spec out-param 的**必填 `gather_from`** 锚定，case inputs 只作身份对账。
    """
    params = spec.get("params") if isinstance(spec, dict) else None
    if not isinstance(params, list) or not params:
        raise ValueError("spec 无 IO 矩阵（params）——无法派生多输出契约")
    in_params = {p["name"]: p for p in params
                 if isinstance(p, dict) and p.get("io") == "in" and p.get("name")}
    out_params = [p for p in params if isinstance(p, dict) and p.get("io") == "out"]
    if not in_params or not out_params:
        raise ValueError("spec IO 矩阵缺 in/out 参数（无法派生多输出契约）")
    # ── out 参数身份 + 角色的**前置强校**（审计 finding #6）──────────────────────────────
    # ① 名字必须**具名且唯一**：outputs[] / manifest / evidence 三处都靠 name 交叉核验身份，
    #    无名或重名 → 「换序不可发现」的洞（同 role/同 shape 的两个输出互换查不出来）。
    # ② out_role 必须**在受控词表内**：不做「非 index 即 value」的真值判断（`""`/`"bogus"` 都得拒）。
    out_names = [p.get("name") for p in out_params]
    if any(not isinstance(n, str) or not n for n in out_names):
        raise ValueError(f"spec out-params 存在无名输出 {out_names}（多输出契约按 name 核身份），fail-closed")
    dup_out = sorted({n for n in out_names if out_names.count(n) > 1})
    if dup_out:
        raise ValueError(f"spec out-params 名字重复 {dup_out}（身份不唯一→换序/错配不可发现），fail-closed")
    for p in out_params:
        if "out_role" not in p:
            raise ValueError(f"输出 {p.get('name')!r} 未声明 out_role（多输出契约必填，受控词表 "
                             f"{list(OUT_ROLES)}）——不猜角色，fail-closed")
        if p.get("out_role") not in OUT_ROLES:
            raise ValueError(f"输出 {p.get('name')!r} 的 out_role={p.get('out_role')!r} 不在受控词表 "
                             f"{list(OUT_ROLES)}（空值/未知角色一律拒，防伪造角色骗过判据派生），fail-closed")
    # ③ index 输出必须**显式声明 `gather_from`**（finding #7）：gather 源是 index 判据的一半，
    #    它必须由 spec 锚定、绝不能取「case 的第一个输入」——后者随 caseset 的输入顺序漂移。
    in_order = [p["name"] for p in params
                if isinstance(p, dict) and p.get("io") == "in" and p.get("name")]
    for p in out_params:
        if p.get("out_role") != OUT_ROLE_INDEX:
            continue
        gf = p.get("gather_from")
        if not isinstance(gf, str) or gf not in in_params:
            raise ValueError(f"index 输出 {p.get('name')!r} 的 gather_from={gf!r} 未指向 spec 的具名 in-参数 "
                             f"{in_order}（index_value_consistency 的 gather 源必须由 spec 锚定，"
                             f"不得取 caseset 的「第一个输入」——那随输入顺序漂移），fail-closed")
    # ── case inputs 与 spec in-params 的**完整/唯一/同序同名**对账（finding #7）────────────
    case_names = [n for n, _ in case_input_dtypes]
    if len(set(case_names)) != len(case_names):
        raise ValueError(f"case 输入名重复 {case_names}（身份不唯一 → gather 源/dtype 对账不可靠），fail-closed")
    if case_names != in_order:
        raise ValueError(f"case 输入 {case_names} 与 spec in-参数 {in_order} 不是同一序同一身份"
                         f"（缺项/多项/换序一律拒——判据只从 spec 派生，输入顺序不得改变 canonical），fail-closed")
    in_dts = []
    for name, dt in case_input_dtypes:
        allowed = in_params[name].get("dtype") or []
        if dt not in allowed:
            raise ValueError(f"case 输入 {name} dtype={dt!r} 不在 spec 允许集 {allowed}（IO schema 不符）")
        in_dts.append(dt)
    if not in_dts:
        raise ValueError("case 无有效输入 dtype（无法派生多输出契约）")
    in_dt = in_dts[0]
    if any(d != in_dt for d in in_dts):
        raise ValueError(f"case 多输入 dtype 不一致 {in_dts}（reduce/elementwise 需同 dtype）")

    def _resolve_out_dtype(p):
        allowed = p.get("dtype") or []
        if FROM_INPUT_SENTINEL in allowed:
            return in_dt
        uniq = list(dict.fromkeys(allowed))
        if len(uniq) == 1:
            return uniq[0]
        raise ValueError(f"输出 {p.get('name')!r} dtype 无法据 spec 派生 allowed={allowed}"
                         f"（须 <from_input> 或单值）")

    contracts = [None] * len(out_params)
    value_tol_by_name, value_std_by_name = {}, {}
    for i, p in enumerate(out_params):                    # 第一趟：value 输出（词表已保证非 index 即 value）
        if p.get("out_role") == OUT_ROLE_INDEX:
            continue
        odt = _resolve_out_dtype(p)
        eff = effective_standard(spec_standard, odt, p.get("compare"))
        pol = threshold_for(eff, odt, tolerance_source, taskdoc_tol)
        contracts[i] = {"name": p.get("name"), "role": p.get("out_role"), "dtype": odt,
                        "standard": eff, "tolerance_policy_id": tolerance_policy_id(eff, odt),
                        "policy": pol, "index_of": None}
        value_tol_by_name[p.get("name")] = _value_tol_of(pol)
        value_std_by_name[p.get("name")] = eff
    for i, p in enumerate(out_params):                    # 第二趟：index 输出
        if p.get("out_role") != OUT_ROLE_INDEX:
            continue
        ref = p.get("index_of")
        # index_of 必须指向 out_role=="value" 的**唯一具名**输出（名字唯一性上文已强校）。
        # 不接受 None/空/非字符串，也不接受指向另一个 index 输出——否则容差会从一条不存在的
        # value 判据上抄过来，index 判据就悬空了。
        if not isinstance(ref, str) or not ref or ref not in value_tol_by_name:
            raise ValueError(f"index 输出 {p.get('name')!r} 的 index_of={ref!r} 未指向任一 "
                             f"out_role=='value' 的具名输出（可引 {sorted(value_tol_by_name)}），fail-closed")
        rtol, atol = value_tol_by_name[ref]
        pol = {"kind": INDEX_VALUE_CONSISTENCY, "gather_from": p["gather_from"],
               "value_rtol": rtol, "value_atol": atol}
        contracts[i] = {"name": p.get("name"), "role": OUT_ROLE_INDEX, "dtype": _resolve_out_dtype(p),
                        "standard": value_std_by_name[ref], "tolerance_policy_id": None,
                        "policy": pol, "index_of": ref}
    if any(c is None for c in contracts):
        bad = [out_params[i].get("name") for i, c in enumerate(contracts) if c is None]
        raise ValueError(f"spec out-params 存在未能派生契约的输出 {bad}（out_role 缺失/非法？）")
    return contracts


def resolve_acceptance(spec, standard, dtype):
    """任务书验收目标口径（可选、独立于平台 standard）的 **canonical 复算**——gen_cases 与 validator 共用。

    据 `spec.precision.acceptance_policy`（形如 `{"standard": "ascendoptest_default", "error_rate": 0.1}`：
    以某标准为底 + 覆盖判据字段）复算 canonical (policy, tolerance_policy_id)。
    无声明 / exact·behavioral 标准 → None（acceptance 继承 standard）。

    ⚠ 安全（finding #3）：validator 用本函数据 **spec** 复算 canonical acceptance，要求 caseset/evidence 三处全等；
    **spec 未声明 acceptance → 返回 None → caseset+evidence 一律不得私带 acceptance**（防 T5 原洞在 acceptance 层重演）。

    ⚠ 覆盖字段**逐个受控校验**（承 finding #5 同一族的残留）：旧写法 `pol[k] = ap[k]` 原样搬运，
    于是 `{"tolerance": inf}` 之类能生成一份「阈值恒真」的 acceptance policy —— 判据整条被废掉却一路绿。
    acceptance 是任务书授权的**放宽口径**（放宽本身合法、由 risk/passed_with_risk 如实上报），
    但「放宽到无穷大」不是口径、是把门拆了。故：非 bool 实数、有限、非负，否则 fail-closed。
    """
    if standard in (EXACT, BEHAVIORAL):
        return None
    prec = spec.get("precision") if isinstance(spec, dict) else None
    if prec is not None and not isinstance(prec, dict):
        raise ValueError(f"spec.precision 须为对象，得 {type(prec).__name__}（无法据 spec 复算 acceptance），fail-closed")
    ap = (prec or {}).get("acceptance_policy")
    if not ap:
        return None
    if not isinstance(ap, dict):
        raise ValueError(f"spec.precision.acceptance_policy 须为对象，得 {type(ap).__name__}，fail-closed")
    ap_std = ap.get("standard", standard)
    pol = threshold_for(ap_std, dtype)
    for k in ("tolerance", "error_rate", "threshold", "max_ratio", "eps"):
        if k in ap:
            pol[k] = _checked_tol(ap[k], f"acceptance_policy.{k}")
    return pol, tolerance_policy_id(ap_std, dtype)


def derive_acceptance_contracts(spec, contracts):
    """多输出场景的 **acceptance canonical 逐输出复算**（审计 finding #2）——op-中立。

    `contracts` = `derive_output_contracts` 的返回值。返回：
      · `None` —— spec 未声明 `precision.acceptance_policy`（→ 全部输出 acceptance 继承 standard，
        且 caseset/evidence 两侧一律不得私带 acceptance 字段）；
      · 与 `contracts` 等长同序的列表，每项为 `{"policy", "tolerance_policy_id"}`
        或 `None`（该输出的有效标准是 exact/behavioral → acceptance 继承 standard，无独立口径）。

    规则：value 输出直接走 `resolve_acceptance(spec, 该输出有效标准, 该输出 dtype)`；
    index 输出**复用它所引 value 输出的 acceptance 容差**（同 canonical 派生里 standard 层的做法）。
    ⚠ 旧洞：多输出路径**整个忽略** `acceptance_policy`，直接令 acceptance=standard —— spec 声明了更严的
    任务书验收口径也照样按平台 standard 放行。取不出 acceptance 容差（如 acceptance 底是 ascendoptest_default，
    `_value_tol_of` 无法据它派生 index 判据）→ **fail-closed**，绝不静默退回 standard。
    """
    prec = spec.get("precision") if isinstance(spec, dict) else None
    if prec is not None and not isinstance(prec, dict):
        raise ValueError(f"spec.precision 须为对象，得 {type(prec).__name__}"
                         f"（无法据 spec 派生多输出 acceptance），fail-closed")
    if not isinstance(spec, dict) or not (prec or {}).get("acceptance_policy"):
        return None
    acc_by_name, out = {}, [None] * len(contracts)
    for i, ct in enumerate(contracts):                       # 第一趟：value 输出
        if ct["role"] == OUT_ROLE_INDEX:
            continue
        acc = resolve_acceptance(spec, ct["standard"], ct["dtype"])
        # acc is None ⟺ 该输出有效标准是 exact/behavioral（阈值已是 0，没有可放宽的 acceptance 口径）
        # → 与单输出 legacy 同语义：继承 standard。**不是**「忽略 acceptance_policy」。
        if acc is not None:
            out[i] = {"policy": acc[0], "tolerance_policy_id": acc[1]}
            acc_by_name[ct["name"]] = acc[0]
        else:
            acc_by_name[ct["name"]] = None
    for i, ct in enumerate(contracts):                       # 第二趟：index 输出（复用所引 value 的 acceptance 容差）
        if ct["role"] != OUT_ROLE_INDEX:
            continue
        if ct["index_of"] not in acc_by_name:
            raise ValueError(f"index 输出 {ct['name']!r} 的 index_of={ct['index_of']!r} 无对应 value 输出"
                             f"（可引 {sorted(acc_by_name)}），fail-closed")
        ref_pol = acc_by_name[ct["index_of"]]
        if ref_pol is None:                                  # 所引 value 继承 standard → index 一并继承
            continue
        rtol, atol = _value_tol_of(ref_pol)                  # 不支持的 acceptance kind 在此 fail-closed
        out[i] = {"policy": {"kind": INDEX_VALUE_CONSISTENCY,
                             "gather_from": ct["policy"]["gather_from"],
                             "value_rtol": rtol, "value_atol": atol},
                  "tolerance_policy_id": None}
    return out


# ==================================================== spec 驱动的「本 case 有哪些输出」====
# 判据链的**入口身份**：一条 case 该有哪些输出，只能由 **spec**（out 参数 + call_variants × case attrs）
# 说了算，绝不能由 caseset 自报（审计严重#1：caseset 删掉一个输出即整体假通过）。
# gen_cases（造用例）与 validator（裁决）**共用下面这组函数**，保证两边看到同一份权威输出集。
def spec_out_names(spec):
    return [p.get("name") for p in (spec.get("params") or []) if isinstance(p, dict) and p.get("io") == "out"]


def spec_attr_names(spec):
    return [p.get("name") for p in (spec.get("params") or []) if isinstance(p, dict) and p.get("io") == "attr"]


def uses_output_contract(spec):
    """是否走多输出契约（`expected.outputs[]`）。据字段：out 参数 >1 个 或 任一 out **声明了** out_role 键。

    ⚠ 触发门用 `"out_role" in p`、**不做真值判断**：`out_role: ""` 是一份写坏的 spec，真值判断会让它
    悄悄退回 legacy 单输出通路（= 判据链整条换掉还没人发现）。声明了这个键就必须走多输出契约、
    在 `derive_output_contracts` 的受控词表上当场炸掉；没声明才是 legacy。"""
    outs = [p for p in (spec.get("params") or []) if isinstance(p, dict) and p.get("io") == "out"]
    return len(outs) > 1 or any("out_role" in p for p in outs)


def call_variants(spec):
    """读 + 强校 `spec.call_variants`（缺省 None = 不声明变体）。返回校验过的变体列表。

    变体字段（全部 op-中立、按**字段**判，绝无算子名）：
      · `when`   ：匹配谓词。`{"always": true}` = 无条件；`{"attr":"<名>","is_null":true|false}` = 按该 attr
                   是否为 null 判；`{"attr":"<名>","equals":<值>}` = 按取值判。**必填**。
      · `symbol` ：该变体调用的 aclnn 符号（不同变体可以是不同 API）。**必填**。
      · `attrs`  ：选填。该变体**显式声明**的 attr 取值覆盖（人在 spec 里写死的，不是代码兜的默认值）。
      · `active_attrs`：选填。该变体签名里真正出现的 attr 槽（缺省 = spec 全部 attr 参数，按 spec 顺序）。
      · `active_outputs`：**必填**。该变体真正落地的 out 参数名；其余 out 参数在 slots 里写成 `out_null`。
                   它同时决定本 case 的 `expected.outputs[]` 身份集（严格绑定就绑在这）。
    """
    raw = spec.get("call_variants")
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"spec.call_variants 须为非空列表，得 {raw!r}（fail-closed）")
    out_names, attr_names = spec_out_names(spec), spec_attr_names(spec)
    checked = []
    for i, v in enumerate(raw):
        if not isinstance(v, dict):
            raise ValueError(f"call_variants[{i}] 须为对象，得 {v!r}")
        sym = v.get("symbol")
        if not isinstance(sym, str) or not sym:
            raise ValueError(f"call_variants[{i}] 缺 symbol（aclnn 符号名必填），得 {sym!r}")
        when = v.get("when")
        if not isinstance(when, dict) or not when:
            raise ValueError(f"call_variants[{i}]({sym}) 缺 when 谓词（不允许隐式全匹配；"
                             f"要无条件请显式写 {{\"always\": true}}）")
        if not when.get("always"):
            an = when.get("attr")
            if an not in attr_names:
                raise ValueError(f"call_variants[{i}]({sym}) 的 when.attr={an!r} 不是 spec attr 参数 {attr_names}")
            if ("is_null" in when) == ("equals" in when):
                raise ValueError(f"call_variants[{i}]({sym}) 的 when 须**恰有一个**判据 is_null / equals，得 {when!r}")
            if "is_null" in when and not isinstance(when["is_null"], bool):
                raise ValueError(f"call_variants[{i}]({sym}) 的 when.is_null 须为 bool，得 {when['is_null']!r}")
        act = v.get("active_outputs")
        if not isinstance(act, list) or not act:
            raise ValueError(f"call_variants[{i}]({sym}) 缺 active_outputs（该变体落地的输出名，必填非空）")
        if len(set(act)) != len(act):
            raise ValueError(f"call_variants[{i}]({sym}) 的 active_outputs 有重复名 {act}")
        unknown = [n for n in act if n not in out_names]
        if unknown:
            raise ValueError(f"call_variants[{i}]({sym}) 的 active_outputs 含非 spec out 参数 {unknown}"
                             f"（spec out：{out_names}）")
        # 必须是 spec out 顺序的**子序列**：golden 返回序与 outputs[] 落盘序都按 spec 序，
        # 变体里换序 = 身份/顺序两套口径打架 → 换序不可发现。
        if act != [n for n in out_names if n in act]:
            raise ValueError(f"call_variants[{i}]({sym}) 的 active_outputs {act} 未按 spec out 参数顺序 "
                             f"{out_names} 排列（须为其子序列），fail-closed")
        aa = v.get("active_attrs")
        if aa is None:
            aa = list(attr_names)
        else:
            if not isinstance(aa, list):
                raise ValueError(f"call_variants[{i}]({sym}) 的 active_attrs 须为列表，得 {aa!r}")
            bad = [n for n in aa if n not in attr_names]
            if bad:
                raise ValueError(f"call_variants[{i}]({sym}) 的 active_attrs 含非 spec attr 参数 {bad}")
            if aa != [n for n in attr_names if n in aa]:
                raise ValueError(f"call_variants[{i}]({sym}) 的 active_attrs {aa} 未按 spec attr 顺序 "
                                 f"{attr_names} 排列（须为其子序列），fail-closed")
        ov = v.get("attrs") or {}
        if not isinstance(ov, dict):
            raise ValueError(f"call_variants[{i}]({sym}) 的 attrs 须为对象，得 {ov!r}")
        bad = [k for k in ov if k not in attr_names]
        if bad:
            raise ValueError(f"call_variants[{i}]({sym}) 的 attrs 覆盖了非 spec attr 参数 {bad}")
        checked.append({"when": when, "symbol": sym, "attrs": dict(ov),
                        "active_attrs": list(aa), "active_outputs": list(act)})
    return checked


def variant_matches(when, attrs):
    if when.get("always"):
        return True
    val = attrs.get(when["attr"])
    if "is_null" in when:
        return (val is None) == bool(when["is_null"])
    return val == when["equals"]


def select_call_variant(variants, attrs, cid):
    """逐 case 选中匹配变体（声明序**首个**匹配者胜）；无匹配 → fail-closed，绝不退默认。"""
    for v in variants:
        if variant_matches(v["when"], attrs):
            return v
    raise ValueError(f"{cid}: attrs={attrs} 无匹配的 call_variants 条目 "
                     f"（已声明 {[v['when'] for v in variants]}）——不为它编造调用形态，fail-closed")


def active_output_names_for_variant(spec, variant, cid):
    """本 case **真正落地**的输出名（有序、= spec out 顺序的子序列）。

    有变体 → 取该变体的 `active_outputs`（spec 显式声明）；`variant is None` → **spec 全部 out 参数**。
    ⚠ 这是「输出集由 spec 声明、不由产物反推」的锚。"""
    out_names = spec_out_names(spec)
    if variant is None:
        return list(out_names)
    act = list(variant["active_outputs"])
    idx_names = {p.get("name") for p in (spec.get("params") or [])
                 if isinstance(p, dict) and p.get("io") == "out" and p.get("out_role") == OUT_ROLE_INDEX}
    for n in act:                                        # index 输出落地了、它引的 value 却没落地 → 判据悬空
        if n in idx_names:
            ref = next(p.get("index_of") for p in spec["params"]
                       if isinstance(p, dict) and p.get("name") == n)
            if ref not in act:
                raise ValueError(f"{cid}: 变体 {variant['symbol']!r} 落地了 index 输出 {n!r}，"
                                 f"但它 index_of 所引的 value 输出 {ref!r} 不在 active_outputs {act}"
                                 f"（index_value_consistency 判据无所依），fail-closed")
    return act


def active_output_names(spec, attrs, cid="case"):
    """据 **spec × 本 case 的 attrs** 派生本 case 的权威输出名序列（裁决层的输出集唯一合法来源）。"""
    variants = call_variants(spec)
    variant = None if variants is None else select_call_variant(variants, attrs or {}, cid)
    return active_output_names_for_variant(spec, variant, cid)


def _check_compute_supported(dtype):
    if dtype not in SUPPORTED_COMPUTE_DTYPES:
        raise ValueError(f"未支持复算的 dtype={dtype!r}（SUPPORTED_COMPUTE_DTYPES="
                         f"{sorted(SUPPORTED_COMPUTE_DTYPES)}）——fail-fast，不静默")


def _checked_tol(v, what):
    """容差标量的**受控校验**（审计 finding #5）：非 bool 的实数、有限、非负——否则 fail-closed。

    旧洞：taskdoc 容差只做 `float()` → `rtol=inf` 也能生成 canonical policy，
    而 `|o-g| <= atol + inf*|g|` 对任意有限误差恒真 = 判据被整条废掉却一路绿。
    `True` 会被 `float()` 悄悄变成 1.0，同样拒。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"{what}={v!r} 须为非 bool 的实数（容差不接受 bool/字符串/None），fail-closed")
    f = float(v)
    if not math.isfinite(f):
        raise ValueError(f"{what}={v!r} 须有限（inf/NaN 会让 allclose 判据恒真或恒假），fail-closed")
    if f < 0:
        raise ValueError(f"{what}={v!r} 须非负，fail-closed")
    return f


def _torch_allclose_tol(dtype, tolerance_source=None, taskdoc_tol=None):
    """返回 torch_allclose 的 (rtol, atol)，按 tolerance_source 分源（op-中立、**仅 None 用缺省**）。

    · dtype_table → adapt 参考仓逐 dtype 表（_TA_DTYPE_TOLS，provenance 见其定义处）；
    · torch_default → torch.allclose 缺省 (1e-5, 1e-8)；
    · taskdoc → 从 spec 派生，须由调用方传入 `taskdoc_tol=(rtol, atol)`（缺则 fail-closed）。

    ⚠ finding #5：**只有 `None` 才落缺省源**。旧写法 `tolerance_source or "dtype_table"` 把显式写坏的
    `""` / `False` / `0` 也当「没写」→ 一份坏 spec 悄悄拿到了 dtype_table 的容差。字段一旦出现就必须是
    `TOLERANCE_SOURCES` 里的受控字符串。
    """
    src = "dtype_table" if tolerance_source is None else tolerance_source
    if not isinstance(src, str) or src not in TOLERANCE_SOURCES:
        raise ValueError(f"未知/非法 tolerance_source={tolerance_source!r}（须为 {list(TOLERANCE_SOURCES)} "
                         f"之一，或省略=None 用缺省 dtype_table）——空串/False/0 一律拒，fail-closed")
    if src == "torch_default":
        return _TA_TORCH_DEFAULT
    if src == "taskdoc":
        if not (isinstance(taskdoc_tol, (list, tuple)) and len(taskdoc_tol) == 2):
            raise ValueError("tolerance_source=taskdoc 需从 spec 派生 (rtol, atol) 并传入 taskdoc_tol；缺 → fail-closed")
        return (_checked_tol(taskdoc_tol[0], "taskdoc rtol"),
                _checked_tol(taskdoc_tol[1], "taskdoc atol"))
    if src == "dtype_table":
        if dtype not in _TA_DTYPE_TOLS:
            raise ValueError(f"torch_allclose dtype_table 无 dtype={dtype!r} 容差（表={list(_TA_DTYPE_TOLS)}；"
                             "整型/bool 输出应走 exact，不进本表）")
        return _TA_DTYPE_TOLS[dtype]
    raise ValueError(f"未知 tolerance_source={tolerance_source!r}（仅 {TOLERANCE_SOURCES}）")


def threshold_for(standard, dtype, tolerance_source=None, taskdoc_tol=None):
    """返回结构化 policy dict（含 kind + 判据常量）。未支持 dtype **fail-fast**（不静默兜底）。

    `tolerance_source`/`taskdoc_tol` 仅对 `torch_allclose` 生效（其余标准忽略，向后兼容原 2 参调用）。"""
    if standard == EXACT:
        return {"kind": EXACT, "max_mismatch": 0, "not_settled": False}
    if standard == BEHAVIORAL:
        return {"kind": BEHAVIORAL, "not_settled": False}
    if standard == TORCH_ALLCLOSE:
        rtol, atol = _torch_allclose_tol(dtype, tolerance_source, taskdoc_tol)
        # equal_nan=True：torch.allclose(..., equal_nan=True) 语义——both-NaN 视为相等（NaN 传播 case 需要）。
        return {"kind": TORCH_ALLCLOSE, "rtol": rtol, "atol": atol, "equal_nan": True, "not_settled": False}
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

    **首 token = 六枚举之一 → 直接用**（golden.py 直接声明来源 provenance，支撑**多仓多算子**的全部来源）：
      `cpu_ref`(仓/PR 的 CPU 参考) · `catlass_existing_ref`(仓自带 golden) · `task_spec_expected`(任务书期望值) ·
      `torch_ref` · `analytical_ref`(agent 按公式自写) · `external_ref`(外部给定)。
    兼容 **backend 简写**（elementwise 内置样例沿用）：`"torch ..."`→torch_ref、`"numpy ..."`→analytical_ref。
    识别不出 → **fail-closed**（ValueError，绝不默认 cpu_ref）。
    """
    s = (golden_source or "").strip()
    first = s.split(None, 1)[0].lower() if s.split() else ""     # 严格首 token，避免 "torchvision"/"numpyish" 误判
    if first in ORACLE_SOURCES:                                  # golden.py 直接声明 provenance 枚举（多仓多算子）
        return first
    if first == "torch":                                        # backend 简写：torch CPU 参考
        return "torch_ref"
    if first == "numpy":                                        # backend 简写：解析 numpy 参考（语义上非 cpu_ref）
        return "analytical_ref"
    raise ValueError(
        f"无法从 golden_source={golden_source!r} 映射 oracle_source —— 首 token 须为六枚举之一 {ORACLE_SOURCES} "
        f"或 backend 简写 torch/numpy；新来源须显式声明（fail-closed，不默认 cpu_ref）。")


# ================================================================================
# golden 来源契约 —— 受控词表 + 档位派生 + 授权核验（批 1）
# ================================================================================
# 用户 2026-07-22 裁定（R1–R12），本节是「档位怎么算」的**唯一**实现，别处只准调、不准复述判断逻辑：
#   R2  PR 里的参考实现**一律禁止**作 golden 源 —— 落地方式是**值域里根本没有那个格子**
#       （禁令会被绕过，缺值只能 fail-closed）。
#   R3  golden 来源是**两档链**：① 任务书指定的测试方法 → ② CPU 上的 torch/numpy API。
#   R4  任务书**指定了、但本环境跑不起来**的方法（内置 TBE / cuSPARSE / OpenCV-GPU 等）
#       → fail-closed 抛用户，**不自动回落**第二档。
#   R5  第二档分两级：**现成 API 单调** → 不人核；**按公式自拼多步** → 必须人核。
#   R9  tier 是**整数 1..4**（不用字符串词表）。
#   R12 任务书**全文快照入库** → 授权锚才可机器核（见 verify_authorization）。
#
# ⚠ 边界：本节**不判 pass/fail**，只判「golden 来源可不可信、够不够格往下跑」。
#    验收裁决仍归 validator（精度）+ perf_compare（性能）+ validate_acceptance_state（三级门），ADR 0007。

# 可**产出**的 oracle_source 子集（≠ 合法集）。ORACLE_SOURCES 六枚举是 canonical 契约（见上），本节不动它；
# 这里收窄的是「谁能被产出来」：
#   - `cpu_ref` 注释语义含「仓/**PR** 的 CPU 参考」→ R2 直接禁产（不拆成两个新枚举：R3 两档链里
#     「仓自带参考」根本没有位置，整格在本上下文是空的）。
#   - `catlass_existing_ref`（仓自带 golden）同属「仓参考」，一并禁产。
PRODUCIBLE_ORACLE_SOURCES = ("torch_ref", "analytical_ref", "task_spec_expected", "external_ref")

# golden 的**实现形态**（谁来算 golden）。⚠ 值域里没有任何指向 PR / 仓内参考实现的取值——这就是 R2。
GOLDEN_SOURCE_KIND = ("single_api", "multistep", "external_method", "needs_user")

# golden 的**方法族**，用来判「本环境跑不跑得起来」（R4）。
GOLDEN_METHOD_KIND = ("torch_cpu", "numpy_cpu", "builtin_tbe", "gpu_lib", "other_external", "needs_user")
RUNNABLE_METHOD_KINDS = frozenset({"torch_cpu", "numpy_cpu"})   # R3 第二档：CPU 上的 torch/numpy

# 任务书对 golden 的**授权强度**。三者区别是本节最吃重的判断：
#   oracle_method  —— 任务书就**真值口径/怎么测**作出了指定（如 IsClose「二进制比较改为逻辑值比较」）；
#   formula        —— 任务书给了**算子公式**（如 catlass 系的 LaTeX），可据以自拼实现；
#   impl_reference —— 任务书只指出**被重写/被对标的实现出处**（如 Sign「参考内置 TBE」）。
#                     ⚠ **impl_reference 不构成 golden 授权**——它说的是「照着谁重写」，
#                     不是「真值该怎么算」。样例 Sign 曾把它误当指定，是本轮更正的错源。
AUTHORIZATION_KIND = ("oracle_method", "formula", "impl_reference", "none")

GOLDEN_BLOCKED_REASON = ("method_unavailable", "unverifiable_authorization", "needs_user")

# 授权锚的引用格式：`task_doc.snapshot.md:<起>` 或 `task_doc.snapshot.md:<起>-<止>`（1-based 闭区间）。
# ⚠ **只认这一个文件名**——指向 pr_facts.json / 仓内文件 / 任何其它路径一律非法。这是 R2 落到机器上的那一刀：
#    PR 连「被引用」的资格都没有。
TASKDOC_SNAPSHOT_NAME = "task_doc.snapshot.md"
_CITE_RE = re.compile(r"^" + re.escape(TASKDOC_SNAPSHOT_NAME) + r":(\d+)(?:-(\d+))?$")


def validate_golden_contract(g, where="golden.py 的 GOLDEN_CONTRACT"):
    """校 golden 契约块的**受控词表**，不合规 → ValueError（批 2）。

    只校「词表 + 结构」，**不核授权真伪**（那是 `verify_authorization` 读快照做的），
    也不判档（那是 `derive_golden_tier`）。三者分开，避免「自己核自己」。

    ⚠ 为什么必须 fail-closed 而不是「不认识就忽略」：`derive_golden_tier` 的第 ⑨ 条穷举兜底
    会把任何不认识的组合判成 tier 4。若这里静默放过拼错的词（如 `source: "singleapi"`），
    结果是**一个本该 tier 2 的正当 golden 被判 blocked**，而报错信息是含糊的
    「unverifiable_authorization」——查半天查不到是拼错了。早拦、报准。"""
    if not isinstance(g, dict):
        raise ValueError(f"{where} 须为对象，得 {type(g).__name__}")
    src = g.get("source")
    if src not in GOLDEN_SOURCE_KIND:
        raise ValueError(f"{where}.source={src!r} 不在受控词表 {GOLDEN_SOURCE_KIND}")
    mk = g.get("method_kind")
    if mk not in GOLDEN_METHOD_KIND:
        raise ValueError(f"{where}.method_kind={mk!r} 不在受控词表 {GOLDEN_METHOD_KIND}")
    auth = g.get("authorization")
    if not isinstance(auth, dict):
        raise ValueError(f"{where}.authorization 须为对象，得 {type(auth).__name__}")
    kind = auth.get("kind")
    if kind not in AUTHORIZATION_KIND:
        raise ValueError(f"{where}.authorization.kind={kind!r} 不在受控词表 {AUTHORIZATION_KIND}")
    if kind in ("oracle_method", "formula"):
        # 声称有任务书授权 → 引文锚三件必须齐（cite / quote / 快照指纹）。
        # 缺任一都**不在这里判死**（`verify_authorization` 会给出更准的原因），但要求字段存在，
        # 免得「声称有授权却连引文都没写」这种一眼可见的漏也要跑到读文件那步才发现。
        for k in ("cite", "quote"):
            if not str(auth.get(k) or "").strip():
                raise ValueError(
                    f"{where}.authorization.kind={kind!r} 声称有任务书授权，但 {k} 为空——"
                    f"引文锚不全则授权无从核实（R12）。若本算子其实只是「参考谁的实现」，"
                    f"kind 应为 impl_reference（它不构成 golden 授权）。")
        snap = g.get("taskdoc_snapshot")
        if not isinstance(snap, dict) or not str(snap.get("sha256") or "").strip():
            raise ValueError(
                f"{where} 声称有任务书授权，但缺 taskdoc_snapshot.sha256——"
                f"须先把任务书全文快照入库（`fetch_source.py --snapshot-into <ops_root>/<op>/`）"
                f"再把它打印的 sha256 填进来（R12 / 批 3）。")
    return True


def derive_golden_tier(g, authorization_verified):
    """据 golden 契约块派生 **(tier:int 1..4, requires_human_review:bool, blocked_reason:str|None)**。

    纯 stdlib、纯词表推导、**按序首命中即返、末行穷举兜底**（故对任意输入组合恒有返回，无未定义态）。

    ⚠ 本函数**不判断授权真伪**——真伪由 `verify_authorization` 独立读快照产出后，经 `authorization_verified`
    灌进来。两层分开的原因：判档只需词表，核授权要读文件；混在一起就会变成「spec 自己核自己」的循环自证。

    档位语义：
      1 = 任务书指定了真值口径、且本环境跑得起来（第一档命中）
      2 = 任务书没指定 → 回落 CPU 现成 API 单调（R3 第二档 · R5 一级，不人核）
      3 = 任务书给了公式 → 自拼多步实现（R5 末位档，必须人核）
      4 = 不许往下跑（blocked_reason 说明为什么）
    """
    # ⚠ 严格布尔：`authorization_verified` 是安全边界参数，**只有 True 才算核实**。
    #    若用真值性判断（`not authorization_verified`），字符串 "false" / 整数 1 这类 truthy 值
    #    会把未核实的授权直接抬进 tier 1 —— 典型 fail-open。非布尔一律视为编程错误、当场抛，
    #    别悄悄按「未核实」吞掉（吞掉会让调用方的类型 bug 一路潜伏到验收结论里）。
    if authorization_verified is not True and authorization_verified is not False:
        raise TypeError(
            f"authorization_verified 必须是 bool（得到 {type(authorization_verified).__name__}）"
            f"——它是安全边界参数，只接受 verify_authorization() 的布尔返回，不做真值性转换。")

    if not isinstance(g, dict):
        g = {}
    src = g.get("source")
    method = g.get("method_kind")
    _auth_obj = g.get("authorization")
    auth = _auth_obj.get("kind") if isinstance(_auth_obj, dict) else None

    def _out(tier, reason):
        # requires_human_review 的**唯一**算法（别处不得另算）：
        #   tier>=3 → 要人（3 是自拼待核、4 是被挡住、都得人介入）；
        #   source==multistep → 要人（R5：任何多步自拼一律人核，出错面远大于单 API）。
        return tier, (tier >= 3 or src == "multistep"), reason

    # ① 显式未定 —— 哨兵沿用仓里已有的 needs_user，不新造第二套「未知」词汇
    if src == "needs_user" or method == "needs_user":
        return _out(4, "needs_user")
    # ② 声称有任务书授权、但核不实 → **直接 blocked，不降级到 2/3 照跑**。
    #    这条是整套设计防 fail-open 的核心：假授权若只降档，R2/R4 等于没设。
    if auth in ("oracle_method", "formula") and not authorization_verified:
        return _out(4, "unverifiable_authorization")
    # ③ R4 字面落地：方法族跑不起来 → fail-closed 抛用户，**不自动回落**第二档
    if method not in RUNNABLE_METHOD_KINDS:
        return _out(4, "method_unavailable")
    # ④ 第一档：任务书就真值口径作出指定
    if auth == "oracle_method" and src in ("single_api", "multistep"):
        return _out(1, None)
    # ⑤ 公式 + 自拼多步 → R5 末位档
    if auth == "formula" and src == "multistep":
        return _out(3, None)
    # ⑥ 公式恰好等于一个现成 API（catlass 系给 LaTeX 的任务书真实可能命中）→ 与 ⑦ 同档
    if auth == "formula" and src == "single_api":
        return _out(2, None)
    # ⑦ 无授权 + 现成 API 单调 → R3 第二档 / R5 一级，正当且不人核（**Sign 归此**）
    if auth in ("impl_reference", "none") and src == "single_api":
        return _out(2, None)
    # ⑧ 无授权却要自拼多步 = 凭空捏造，不许
    if auth in ("impl_reference", "none") and src == "multistep":
        return _out(4, "unverifiable_authorization")
    # ⑨ 穷举兜底（含 source=external_method 与一切自相矛盾的组合）——恒有返回，杜绝未定义态
    return _out(4, "unverifiable_authorization")


def verify_authorization(g, snapshot_path):
    """核 golden 契约块的**任务书授权锚**是否属实，返 (ok:bool, reason:str|None)。

    R12（任务书全文快照入库）的机器落点。逐步：
      ① 快照文件存在，且其字节 sha256 == `g["taskdoc_snapshot"]["sha256"]`（防换文件）；
      ② `authorization.cite` 严格匹配 `task_doc.snapshot.md:<起>[-<止>]`（**只认这一个文件名**，R2）；
      ③ 行号 1-based 闭区间、不得越界；
      ④ `authorization.quote` 必须是该行区间文本的**逐字子串**。

    `authorization.kind ∈ {impl_reference, none}` 无需锚 → 直接 (True, None)
    （它们本来就不构成授权，见 AUTHORIZATION_KIND 注释；档位由 derive_golden_tier 按无授权处理）。

    ⚠ **诚实边界**：本函数只证「这句引文确实出自这份快照的这几行」，**不证**「这句话到底算
       oracle_method 还是 impl_reference」——那一刀仍由 agent 判、属自报。Sign 的错源正在这一刀上，
       机器目前拦不住，只能靠人核与样例回归兜。别把本函数的绿色读成「授权已被完整验证」。
    """
    # 容器类型防御（与 derive_golden_tier 对称）：agent 产坏了给个字符串/列表，必须 fail-closed
    # 返回 (False, 原因)，**不能抛 AttributeError** —— 崩出去的异常在调用方可能被 except 吞成放行。
    if not isinstance(g, dict):
        return False, f"golden 契约块不是对象（得到 {type(g).__name__}）"
    auth = g.get("authorization")
    if not isinstance(auth, dict):
        return False, f"authorization 不是对象（得到 {type(auth).__name__}）"
    kind = auth.get("kind")
    if kind in ("impl_reference", "none"):
        return True, None
    if kind not in ("oracle_method", "formula"):
        return False, f"authorization.kind={kind!r} 不在受控词表 {AUTHORIZATION_KIND}"

    snap = g.get("taskdoc_snapshot")
    if snap is not None and not isinstance(snap, dict):
        return False, f"taskdoc_snapshot 不是对象（得到 {type(snap).__name__}）"
    declared = ((snap or {}).get("sha256") or "").strip().lower()
    if not declared:
        return False, "缺 taskdoc_snapshot.sha256——无快照指纹则锚不可核（fail-closed）"
    if not snapshot_path or not os.path.isfile(snapshot_path):
        return False, f"任务书快照不存在：{snapshot_path!r}"
    with open(snapshot_path, "rb") as fh:
        raw = fh.read()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != declared:
        return False, f"快照指纹不符：声明 {declared[:12]}… 实际 {actual[:12]}…（快照被换或 spec 未更新）"

    cite = (auth.get("cite") or "").strip()
    m = _CITE_RE.match(cite)
    if not m:
        return False, (f"cite={cite!r} 格式非法——须为 {TASKDOC_SNAPSHOT_NAME}:<起>[-<止>]；"
                       f"指向 PR / 仓内文件 / 其它路径一律不接受（R2）")
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else start
    if start < 1 or end < start:
        return False, f"cite 行区间非法：{start}-{end}（须 1-based 且 起<=止）"

    lines = raw.decode("utf-8", errors="replace").splitlines()
    if end > len(lines):
        return False, f"cite 行区间越界：{start}-{end}，快照仅 {len(lines)} 行"

    quote = auth.get("quote") or ""
    if not quote.strip():
        return False, "authorization.quote 为空——无引文则锚不可核（fail-closed）"
    segment = "\n".join(lines[start - 1:end])
    if quote not in segment:
        return False, (f"quote 不是 {cite} 行区间的逐字子串——引文与出处对不上"
                       f"（防「引了一句原文里没有的话」）")
    return True, None


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
    """向后兼容的标量阈值 digest（旧 gate/spec 的 precision.threshold 语义）。

    ⚠ 返回值必须 **JSON-native**（审计 finding #6）：多阈值口径返回 **list**、不是 tuple。
    digest 会落进 caseset/evidence 再被读回来做「三处一致」比对——tuple 落 JSON 变 list，
    内存里比得过、JSON 往返就 `list != tuple` 恒不等，对账门等于自己把自己判死。"""
    kind = policy.get("kind")
    if kind == EXACT:
        return 0
    if kind == ASCENDOPTEST_DEFAULT:
        return policy["tolerance"]
    if kind == ECOSYSTEM_MERE_MARE:
        return policy["threshold"]
    if kind == BEHAVIORAL:
        return 0
    if kind == TORCH_ALLCLOSE:
        return [policy["rtol"], policy["atol"]]          # 无单标量阈值——digest = [rtol, atol]（JSON-native）
    if kind == INDEX_VALUE_CONSISTENCY:
        return [policy["value_rtol"], policy["value_atol"]]
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


def _allclose_close_mask(a, g, rtol, atol, equal_nan):
    """torch.allclose 语义的逐元素「接近」掩码 + 诊断用 diff（**inf 显式处理，绝不替换成 finfo.max**）。

    审计 finding #3：旧实现在 value 路径先 `_replace_inf`（±inf→±finfo.max）——于是
    `actual=finfo.max, golden=+inf` 被判相等（一个有限数冒充无穷大而毫无察觉）；index 路径又不替换，
    `inf-inf=NaN` 反把**同号 inf** 判成失配。两条路径互相矛盾且都不是 torch 语义。现在显式四象限：
      · 两侧同号 inf        → 相等；
      · 单侧 inf / 异号 inf → 失配；
      · 两侧有限            → `|a-g| <= atol + rtol*|g|`；
      · NaN                 → 只由 `equal_nan`（两侧同为 NaN）决定，其余一律失配。
    `_replace_inf` 保留给 `ascendoptest_default`——那条路径是 compare.py 的逐字复刻，语义由 provenance 锚定。
    返回 `(close, diff)`；`diff` 在 inf/NaN 位可能是 inf/NaN，诊断 max 只取有限位（调用方已如此）。
    """
    import numpy as np
    with np.errstate(invalid="ignore"):
        diff = np.abs(a - g)
    fin = np.isfinite(a) & np.isfinite(g)
    close = fin & (diff <= (atol + rtol * np.abs(g)))
    both_inf_same = np.isinf(a) & np.isinf(g) & (np.signbit(a) == np.signbit(g))
    close = close | both_inf_same
    if equal_nan:
        close = close | (np.isnan(a) & np.isnan(g))
    return close, diff


def _check_index_array(arr, side):
    """index 输出的**类型闸**（审计 finding #4）：必须是真整数数组，拒 bool / 浮点 / 静默转换。

    旧洞：`astype(np.intp)` 把 `[0.9, 1.7]` 静默截成 `[0, 1]`——一份浮点/坏 dtype 的「下标」就这样
    被 gather 消费掉了，判据看起来还全绿。"""
    import numpy as np
    if arr.dtype == np.bool_ or not np.issubdtype(arr.dtype, np.integer):
        raise ValueError(f"index_value_consistency 的 {side} 下标 dtype={arr.dtype.name!r} 非整数"
                         f"（bool/浮点/字符串一律拒，禁止静默转换），fail-closed")


def _gather_along_dim(src, idx, dim, keepdim):
    """`take_along_axis` 的薄封装：沿 dim 用 idx 从 src 取值，返回与 idx 同形（归约轴已去）的数组。

    idx 是「沿 dim 的下标」（median/argmax 类归约输出）：keepdim=False 时 idx 比 src 少一维（dim 被归约掉），
    keepdim=True 时 idx 在 dim 处为长度 1。用于 index_value_consistency——gather(src,idx) 还原下标处的值。

    ⚠ finding #4：gather 前**逐元素校 `0 <= idx < src.shape[dim]`**。`take_along_axis` 会把负下标按
    Python 语义回绕（实测 idx=-1 取到最后一个元素、mismatch=0 假通过），正向越界又抛未归一的 IndexError；
    两者统一收敛成 ValueError，由上层判 fail。"""
    import numpy as np
    src = np.asarray(src)
    idx = np.asarray(idx)
    _check_index_array(idx, "gather")
    ndim = src.ndim
    d = dim if dim >= 0 else dim + ndim
    if not (0 <= d < ndim):
        raise ValueError(f"index_value_consistency dim={dim} 越界（源 ndim={ndim}）")
    limit = int(src.shape[d])
    if idx.size:
        lo, hi = int(idx.min()), int(idx.max())
        if lo < 0 or hi >= limit:
            raise ValueError(f"index_value_consistency 下标越界：取值域 [{lo},{hi}] 不在 "
                             f"[0,{limit - 1}]（dim={d} 源长 {limit}）——负下标会被回绕成合法取值，"
                             f"一律拒，fail-closed")
    idx = idx.astype(np.intp, copy=False)
    idx_exp = idx if keepdim else np.expand_dims(idx, axis=d)
    if idx_exp.ndim != ndim:
        raise ValueError(f"index_value_consistency 下标维度 {idx_exp.ndim} 与源 {ndim} 不匹配"
                         f"（keepdim={keepdim} dim={d}）")
    gathered = np.take_along_axis(src, idx_exp, axis=d)
    return np.squeeze(gathered, axis=d)


def compute_metrics(out, golden, policy, gather_ctx=None):
    """采集层复算误差分布（numpy，惰性 import）——**只量误差、不判 pass/fail**（judge 在 validator）。

    `gather_ctx`（仅 index_value_consistency 用）：`{"source": <输入张量 self>, "dim": int, "keepdim": bool}`——
    采集层从 case 重读 policy.gather_from 指的输入 + 归约轴，供 gather 还原下标处的值做 allclose。

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
        mism = (o != g)
        # §1.4 NaN 特殊场景（如 bf16/int Neg 的 torch.neg(NaN)=NaN 输出）：NaN!=NaN=True 会误计 mismatch。
        # 对齐 both_nan 视为通过（同数值口径 L422-423 与 compare.py）。仅浮点有 NaN；bool/int 无、不受影响。
        if np.issubdtype(o.dtype, np.floating):
            mism = mism & ~(np.isnan(o) & np.isnan(g))
        return {"exact_mismatch": int(np.count_nonzero(mism)), "numel": int(g.size)}

    if kind == BEHAVIORAL:
        return {"numel": int(g.size)}

    if kind == TORCH_ALLCLOSE:
        # |o-g| <= atol + rtol*|g|；容错率=0（judge 判 mismatch==0）。equal_nan=True 时 both-NaN 视为通过。
        # 双侧 dtype 严校（承 finding #2/pv-4）：bf16 以 storage fp32 落盘比对，故 on-disk 恒 fp32/fp16/int，
        # 两侧须同 dtype 且受支持——complex/真 bf16 数组在此 fail-fast（median 见证集不触发）。
        _check_compute_supported(o.dtype.name)
        _check_compute_supported(g.dtype.name)
        if o.dtype != g.dtype:
            raise ValueError(f"torch_allclose out/golden dtype 不一致：out={o.dtype.name} golden={g.dtype.name}"
                             "（两侧须同 dtype）——fail-fast，不静默")
        rtol = _checked_tol(policy["rtol"], "policy.rtol")
        atol = _checked_tol(policy["atol"], "policy.atol")
        equal_nan = bool(policy.get("equal_nan", True))
        # ⚠ **不做 _replace_inf**（finding #3）：inf 由 `_allclose_close_mask` 显式按四象限判，
        #   把 ±inf 换成 ±finfo.max 会让「有限最大值 vs 无穷大」被判相等。
        o64 = o.astype(np.float64)
        g64 = g.astype(np.float64)
        close, diff = _allclose_close_mask(o64, g64, rtol, atol, equal_nan)
        finite = np.isfinite(diff)
        denom = np.abs(g64)
        # 诊断量只在**有限 diff 且分母>0** 的位置算（inf/NaN 位算出来是 inf/nan，既无意义又会刷 RuntimeWarning）。
        rel = np.divide(diff, denom, out=np.zeros_like(diff), where=finite & (denom > 0))
        return {"mismatch": int(np.count_nonzero(~close)), "numel": int(g64.size),
                "max_abs_err": float(diff[finite].max()) if finite.any() else 0.0,
                "max_rel_err": float(rel[finite].max()) if finite.any() else 0.0}

    if kind == INDEX_VALUE_CONSISTENCY:
        # 下标不逐位比：gather(self, idx_actual) allclose gather(self, idx_golden)（tie 上下标可合法不同、值须一致）。
        if not isinstance(gather_ctx, dict) or "source" not in gather_ctx or "dim" not in gather_ctx:
            raise ValueError("index_value_consistency 需 gather_ctx={source, dim, keepdim}"
                             "（采集层据 policy.gather_from 重读输入 + 归约轴）")
        src = np.asarray(gather_ctx["source"])
        dim = gather_ctx["dim"]
        keepdim = bool(gather_ctx.get("keepdim", False))
        ia = np.asarray(out)                              # 用未 flatten 的原下标数组（gather 需保形）
        ig = np.asarray(golden)
        if ia.shape != ig.shape:
            raise ValueError(f"index_value_consistency 下标形状不一致 actual={ia.shape} golden={ig.shape}")
        # finding #4：两侧都必须是**同一个**整数 dtype（int64 下标 vs float 下标不是同一种东西；
        # 跨整型比也会掩盖窄化截断）。逐元素越界/负数在 `_gather_along_dim` 里拒。
        _check_index_array(ia, "actual")
        _check_index_array(ig, "golden")
        if ia.dtype != ig.dtype:
            raise ValueError(f"index_value_consistency 两侧下标 dtype 不一致 actual={ia.dtype.name} "
                             f"golden={ig.dtype.name}（须同一整数 dtype）——fail-closed")
        rtol = _checked_tol(policy["value_rtol"], "policy.value_rtol")
        atol = _checked_tol(policy["value_atol"], "policy.value_atol")
        ga = _gather_along_dim(src, ia, dim, keepdim).astype(np.float64)
        gg = _gather_along_dim(src, ig, dim, keepdim).astype(np.float64)
        # 与 value 路径**同一个实现**（finding #3）：inf 四象限一致、equal_nan 语义一致。
        close, diff = _allclose_close_mask(ga, gg, rtol, atol, True)
        finite = np.isfinite(diff)
        return {"mismatch": int(np.count_nonzero(~close)), "numel": int(ig.size),
                "gathered_max_abs_err": float(diff[finite].max()) if finite.any() else 0.0}

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
