"""Task 1 · gen_cases — spec.json -> caseset.json (+ per-case input/golden .npy).

Layer 1 确定性脚本（工具中立、op 驱动）。据 spec（参数 arity/attrs、verify_mode、dtype 集、可选 attr_matrix）
× dtype × shape × 泛化生成用例，用参考实现算 golden（逐算子分发；golden_source 记来源，不设全局假设）。
支持 IsClose（二元、bool、exact）、Sign/Neg（一元、numerical）、Equal（二元、bool、exact）。加算子 = 注册 GOLDEN[op]。
确定性：固定种子 SEED，无时间/系统随机。

T7 dtype/attr 扩面（据 codex 审终版）：
  · dtype 扩到 int16/int32（原生）+ bfloat16（**位级双表示**：numpy 无 bf16、本机无 ml_dtypes，故逻辑用 fp32、
    物理落 uint16 位模式，round-half-to-even）。dtype 集从 **spec `params[].dtype` 驱动**（不改 spec 就不产新 dtype）。
  · **storage_dtype 契约**（canonical harness 职责#2/#3）：inputs 项在物理≠逻辑时带 `storage_dtype`（bf16→uint16）；
    `x{j}.npy` 存物理位模式（X_bin·喂 kernel），`golden.npy` 存 op(逻辑值)（喂 golden）——**两份分造、禁共用 reshape**。
  · **per-case compare**（rule-catalog §1.1）：int → exact_equal；Sign/Neg 的 bf16/fp16 输出在网格上精确可表示 →
    也 exact_equal（绕开 bf16 阈值权威难题）；fp32/fp16 数值 → rel_err（沿用 ascendoptest，向后兼容）。
    有效标准由 `precision_policy.effective_standard` 派生（int 不可绕过；bf16 靠 compare 收紧）。
  · **attr_matrix**（显式列表语义，非笛卡尔）：spec.attr_matrix=[{...attrs}] → 每项在**一个代表 (dtype,shape)** 产
    **恰好一条** case；缺省 → 现默认单值行为（向后兼容）。
  · **语义化稳定 case_id** `{op}_{dtype}_{shapetag}_{kind}[_a{k}]` + 碰撞 guard（弃索引 id，扩面重排不毁旧 id）。
  · 每 case 带 `case_origin`/`rule_ref` 可追溯（codex#18）。

⚠ 真机（真 NPU）上 int/bf16 的数值校验本轮**不做**——runner.cpp 的新 dtype 分支属 Track C（挂真机+pr_facts），
  见 doc/oprunway-todo.md gap。本文件仅证「流水线能造/收发 int/bf16 用例」，非「某算子在该 dtype 被验收」。
"""
import json, os, sys
import numpy as np
import precision_policy

SEED = 2026
_BF16 = "bfloat16"
# 原生 numpy dtype（bf16 不在此——它逻辑 fp32、物理 uint16，特判）
_NATIVE = {"float32": np.float32, "float16": np.float16, "int32": np.int32, "int16": np.int16}
# Sign/Neg：输出在 bf16 网格上**精确可表示**（sign∈{-1,0,1}、neg 精确取负）→ bf16/fp16 走 exact_equal。
# genuinely-lossy 数值算子（bf16 阈值须来自 policy/ascendoptest）本轮无、留 gap。
_BF16_EXACT_OPS = frozenset({"Sign", "Neg"})


# ================================================= bf16 位级 codec（零依赖）====
# 前提：little-endian host 落盘（.tofile/.npy）；远端 NPU 同序。round-half-to-even 截 fp32 高 16 位。
def _f32_to_bf16_uint16(v):
    """fp32 -> bf16 的 uint16 位模式（round-half-to-even）。
    ±0 保符号；inf 保 inf；进位可正确溢为 inf；NaN 保 quiet（尾数高位置 1）+ 保符号（low#17）。"""
    x = np.asarray(v, dtype=np.float32)
    u32 = x.view(np.uint32)
    is_nan = np.isnan(x)
    lsb = (u32 >> np.uint32(16)) & np.uint32(1)          # 目标 LSB，用于 round-half-to-even
    bias = np.uint32(0x7FFF) + lsb
    rounded = (u32 + bias) >> np.uint32(16)              # 进位可传入指数域 → 正确溢为 inf
    bf = rounded.astype(np.uint16)
    sign16 = ((u32 >> np.uint32(16)) & np.uint32(0x8000)).astype(np.uint16)
    bf = np.where(is_nan, np.uint16(0x7FC0) | sign16, bf)  # NaN → quiet NaN（防截断后误成 inf）
    return np.ascontiguousarray(bf, dtype=np.uint16)


def _bf16_uint16_to_f32(u):
    """bf16 的 uint16 位模式 -> fp32（低 16 位零扩展；对网格上的值无损）。"""
    uu = (np.asarray(u, dtype=np.uint16).astype(np.uint32) << np.uint32(16))
    return np.ascontiguousarray(uu.view(np.float32), dtype=np.float32)


def _bf16_round(v):
    """fp32 -> fp32-on-bf16-grid（decode(encode(v))）——喂 golden 的逻辑值。"""
    return _bf16_uint16_to_f32(_f32_to_bf16_uint16(v))


def _compute_np(dtn):
    """逻辑/计算 numpy dtype（造 X_logical + 算 golden 用）：bf16→fp32（在网格上）；余原生。"""
    return np.float32 if dtn == _BF16 else _NATIVE[dtn]


def _storage_np(dtn):
    """物理/落盘 numpy dtype（X_bin 用）：bf16→uint16；余=逻辑。"""
    return np.uint16 if dtn == _BF16 else _NATIVE[dtn]


def _storage_name(dtn):
    """物理 storage_dtype 名字（喂 kernel/落盘的字节 dtype）：bf16→uint16；余=逻辑名。"""
    return "uint16" if dtn == _BF16 else dtn


def _assert_equal_nan_effective(golden_fn, inputs, attrs, cid):
    """finding #10：nanpair 用例断言 equal_nan **真起作用**——输入含 aligned-NaN 且翻转 equal_nan 后 golden 有别。

    否则该 attr 对 golden 毫无影响（算子彻底忽略 equal_nan 也逐位对上 golden）→ 假覆盖，fail-fast。
    仅在 data_kind=='nanpair' 路径调用（IsClose·float/bf16 的 equal_nan variant）。"""
    a, b = inputs[0], inputs[1]
    aligned_nan = bool((np.isnan(a) & np.isnan(b)).any())
    if not aligned_nan:
        raise ValueError(f"{cid}: nanpair 用例输入无 aligned-NaN（equal_nan 无从生效 → 假覆盖，fail-fast）")
    g_true = golden_fn(inputs, {**attrs, "equal_nan": True})
    g_false = golden_fn(inputs, {**attrs, "equal_nan": False})
    if np.array_equal(g_true, g_false):
        raise ValueError(f"{cid}: equal_nan 翻转后 golden 不变（该 attr 对 golden 无影响 → 假覆盖，fail-fast）")


# ---- golden 参考实现（逐算子；inputs=按 spec 顺序的**逻辑**输入数组，attrs=属性字典） ----
def golden_isclose(inputs, attrs):
    return np.isclose(inputs[0], inputs[1], rtol=attrs["rtol"], atol=attrs["atol"],
                      equal_nan=attrs["equal_nan"])


def golden_sign(inputs, attrs):
    return np.sign(inputs[0])


def golden_equal(inputs, attrs):
    return np.equal(inputs[0], inputs[1])


def golden_neg(inputs, attrs):
    return np.negative(inputs[0])


GOLDEN = {"IsClose": ("numpy np.isclose", golden_isclose),
          "Sign": ("numpy np.sign", golden_sign),
          "Equal": ("numpy np.equal", golden_equal),
          "Neg": ("numpy np.negative", golden_neg)}


# ================================================= 逻辑输入构造（compute dtype）
def _make_varied(rng, shape, dtn):
    """含负/零/正的一般输入（Sign 全分支覆盖）。int：整数网格且**排除 dtype 最小值**（避 np.negative 溢出，
    codex#14）；bf16：fp32 造后 round 到 bf16 网格（返回 fp32-on-grid 逻辑值）。"""
    cdt = _compute_np(dtn)
    if precision_policy.is_integer_dtype(dtn):
        info = np.iinfo(cdt)
        lo = max(-100, int(info.min) + 1)               # 排除 dtype-min（避免取负溢出未定义）
        hi = min(100, int(info.max))
        x = rng.integers(lo, hi + 1, size=shape).astype(cdt)
        f = x.reshape(-1)
        if f.size >= 3:
            f[0], f[1], f[2] = cdt(-2), cdt(0), cdt(3)  # 保证含负/零/正
        return x
    x = rng.uniform(-5.0, 5.0, size=shape).astype(np.float32)
    f = x.reshape(-1)
    if f.size >= 3:
        f[0], f[1], f[2] = np.float32(-2.0), np.float32(0.0), np.float32(3.0)
    return _bf16_round(x) if dtn == _BF16 else x.astype(cdt)


def _make_pairfar(rng, shape, dtn, ref, attrs):
    """浮点 IsClose 第二输入：前半 near(→True)、后半 far(→False)，跨 tol 边界。"""
    cdt = _compute_np(dtn)
    atol, rtol = float(attrs.get("atol", 0.0)), float(attrs.get("rtol", 0.0))
    near = (ref * (1.0 + rng.uniform(-rtol, rtol, size=shape))
            + rng.uniform(-atol, atol, size=shape)).astype(np.float32)
    far = (np.asarray(ref, dtype=np.float32) + 0.1
           + rng.uniform(0.05, 0.2, size=shape)).astype(np.float32)
    x = far.copy().reshape(-1)
    x[: x.size // 2] = near.reshape(-1)[: x.size // 2]   # 前半 near、后半 far → golden 混合
    x = x.reshape(shape)
    return _bf16_round(x) if dtn == _BF16 else x.astype(cdt)


def _make_pairhalf(shape, dtn, ref):
    """exact-equal 类(Equal, float)第二输入：前半严格相等(→True)、后半+1(→False)。"""
    cdt = _compute_np(dtn)
    x = np.asarray(ref, dtype=np.float32).copy().reshape(-1)
    x[x.size // 2:] = x[x.size // 2:] + np.float32(1.0)
    x = x.reshape(shape)
    return _bf16_round(x) if dtn == _BF16 else x.astype(cdt)


def _make_pairint(shape, dtn, ref):
    """整数 IsClose/Equal 第二输入（codex#13）：前半=ref(相等→near/True)、后半=ref+5(差>atol→far/False)，
    整数网格上构造；golden 天然含 True/False（下游 exact bool 断言校验）。"""
    cdt = _compute_np(dtn)
    x = np.asarray(ref, dtype=cdt).copy().reshape(-1)
    x[x.size // 2:] = x[x.size // 2:] + cdt(5)
    return x.reshape(shape)


def _make_nanpair(rng, shape, dtn, attrs):
    """浮点 IsClose 的 equal_nan/NaN 数据（rule-catalog §1.3）：四段 = 对齐NaN / near相等 / 错位NaN / far；
    equal_nan=True → [T,T,F,F]、=False → [F,T,F,F]，两分支都含 True/False。返回 (a, b)。"""
    n = int(np.prod(shape)) if shape else 0
    a = rng.uniform(-3.0, 3.0, size=n).astype(np.float32)
    b = a.copy()
    q = max(1, n // 4)
    nan = np.float32("nan")
    a[0:q] = nan; b[0:q] = nan                          # seg0 对齐 NaN
    if n >= 3 * q:
        a[2 * q:3 * q] = nan; b[2 * q:3 * q] = np.float32(5.0)  # seg2 错位 NaN
    b[3 * q:] = a[3 * q:] + np.float32(1.0)             # seg3 far（不含 NaN 位）→ False
    a2, b2 = a.reshape(shape), b.reshape(shape)
    if dtn == _BF16:
        return _bf16_round(a2), _bf16_round(b2)
    cdt = _compute_np(dtn)
    return a2.astype(cdt), b2.astype(cdt)


def _build_inputs(rng, in_params, shp, dtn, attrs, data_kind):
    """造该 case 的**逻辑**输入数组列表（compute dtype；bf16=fp32-on-grid）。物理化在保存步单独做。"""
    arity = len(in_params)
    if shp == "broadcast":                               # 仅二元：self (4,1) vs other (1,5)
        return [_make_varied(rng, (4, 1), dtn), _make_varied(rng, (1, 5), dtn)]
    if data_kind == "nanpair":                           # nan_pair 同造 a、b
        a, b = _make_nanpair(rng, shp, dtn, attrs)
        return [a, b]
    x0 = _make_varied(rng, shp, dtn)
    if arity == 1:
        return [x0]
    if data_kind == "pairfar":
        x1 = _make_pairfar(rng, shp, dtn, x0, attrs)
    elif data_kind == "pairhalf":
        x1 = _make_pairhalf(shp, dtn, x0)
    elif data_kind == "pairint":
        x1 = _make_pairint(shp, dtn, x0)
    else:                                                # varied（广播已上文返回）
        x1 = _make_varied(rng, shp, dtn)
    return [x0, x1]


# ================================================= 语义化稳定 case_id ===========
def _shape_tag(shp):
    if shp == "broadcast":
        return "bcast"
    return "x".join(str(int(d)) for d in shp)


def _binary_data_kind(dtn, attrs):
    """二元算子数据构造 kind：int→整数网格；close 类(有 rtol)→跨 tol 边界；否则 exact-equal 前后半。"""
    if precision_policy.is_integer_dtype(dtn):
        return "pairint"
    if "rtol" in attrs:
        return "pairfar"
    return "pairhalf"


def _mk_id(op, dtn, shp, id_kind, attr_idx, seen):
    base = f"{op.lower()}_{dtn}_{_shape_tag(shp)}_{id_kind}"
    if attr_idx is not None:
        base = f"{base}_a{attr_idx}"
    # finding #13：碰撞 fail-fast（不再静默追加 _2 改名——静默改名会让两条本应区分的 plan entry 用同一 base
    # 冒充覆盖）。合法 plan 里 (dtype,shape,kind,attr_idx) 天然唯一；碰撞=上游有重复 dtype/plan 漂移，须暴露。
    if base in seen:
        raise ValueError(f"case_id 碰撞：{base!r} 已存在（plan entry 重复——多为 spec dtype 集含重复项；"
                         f"fail-fast 而非静默改名，防伪造覆盖）")
    seen.add(base)
    return base


# ================================================= 计划构建 ====================
def _plan(spec, in_params, dtypes, attrs_default, op):
    """产用例计划（顺序确定）。每项 dict：dims/shape/dtype/tags/data_kind/id_kind/attrs/attr_idx/origin/rule。
    缺省（无 attr_matrix、dtype 集=fp32/fp16）与现状完全一致，仅 id 变语义化。"""
    arity = len(in_params)
    plan = []

    def add(dims, shp, dtn, tags, data_kind, id_kind, attrs, attr_idx, origin, rule):
        plan.append({"dims": dims, "shape": shp, "dtype": dtn, "tags": tags,
                     "data_kind": data_kind, "id_kind": id_kind, "attrs": attrs,
                     "attr_idx": attr_idx, "case_origin": origin, "rule_ref": rule})

    # 功能/精度：dtype × shape（dtype 集从 spec 驱动）
    for dtn in dtypes:
        rule = ("rule-catalog §1.1 int→exact_equal" if precision_policy.is_integer_dtype(dtn)
                else ("rule-catalog §1.1 bf16 + harness 职责#2/#3(storage_dtype=uint16)" if dtn == _BF16
                      else "rule-catalog §1.0 base + §1.1 dtype"))
        dkind = "varied" if arity == 1 else _binary_data_kind(dtn, attrs_default)
        for shp in [(16,), (4, 4)]:
            add(["功能", "精度"], shp, dtn, ["常规"], dkind, dkind, dict(attrs_default), None,
                "gen_cases:functional_precision", rule)
    # 广播（仅二元）
    if arity == 2:
        add(["功能", "精度"], "broadcast", "float32", ["泛化", "广播"], "varied", "varied",
            dict(attrs_default), None, "gen_cases:broadcast", "rule-catalog §2.5 broadcast")
    # 性能：大 shape（id_kind=perf；数据仍按 arity 造）
    big_dkind = "varied" if arity == 1 else _binary_data_kind("float32", attrs_default)
    add(["性能"], (1024, 1024), "float32", ["性能", "大shape"], big_dkind, "perf",
        dict(attrs_default), None, "gen_cases:perf", "spec.perf.baseline + rule performance")
    # T6：spec 声明小 shape 例外 → 追加 ≥2 个小 shape 性能用例（dtype 从 spec 取 dtypes[0]；id_kind=perfsmall）
    if (spec.get("perf") or {}).get("small_shape_exception"):
        sdt = dtypes[0]
        s_dkind = "varied" if arity == 1 else _binary_data_kind(sdt, attrs_default)
        for shp in [(64,), (256,)]:
            add(["性能"], shp, sdt, ["性能", "小shape"], s_dkind, "perfsmall", dict(attrs_default),
                None, "gen_cases:small_shape_exception", "spec.perf.small_shape_exception + rule performance")
    # T7：attr_matrix 显式列表 → 每 variant 在代表 (dtype0, (4,4)) 产恰好一条 case
    attr_matrix = spec.get("attr_matrix")
    if attr_matrix:
        rep_dt = dtypes[0]
        attr_names = set(attrs_default)          # finding #12：variant key 须 ⊆ spec io=='attr' 名集
        for k_idx, variant in enumerate(attr_matrix):
            if not isinstance(variant, dict):
                raise ValueError(f"attr_matrix[{k_idx}] 须为 attr 字典，得 {type(variant).__name__}")
            unknown = set(variant) - attr_names   # 未知 attr key（如 {foo:12345}）→ 假覆盖，fail-fast
            if unknown:
                raise ValueError(f"attr_matrix[{k_idx}] 含未知 attr key {sorted(unknown)}"
                                 f"（须 ⊆ spec io=='attr' 名集 {sorted(attr_names)}，防伪造覆盖）")
            for k, v in variant.items():          # 基本值类型校验（拒 dict/list 等非标量 attr 值）
                if not isinstance(v, (bool, int, float, str)) or v is None:
                    raise ValueError(f"attr_matrix[{k_idx}].{k}={v!r} 非标量（须 bool/int/float/str）")
            merged = dict(attrs_default); merged.update(variant)
            # bf16 纳入可 NaN 浮点（finding #10）：bf16 能承载 aligned-NaN(0x7FC0)、codec 保 NaN，排除会致
            # equal_nan 假覆盖（走 pairfar 无 NaN → 两版 golden 相等 → 算子忽略 equal_nan 也逐位对上 golden）。
            is_float = not precision_policy.is_integer_dtype(rep_dt)
            # equal_nan 显式出现（IsClose·float/bf16）→ nan_pair 数据让该 flag 真正生效；否则常规二元/一元数据
            if arity == 2 and "equal_nan" in variant and is_float:
                dkind = "nanpair"
            elif arity == 2:
                dkind = _binary_data_kind(rep_dt, merged)
            else:
                dkind = "varied"
            add(["功能", "精度"], (4, 4), rep_dt, ["常规", "attr矩阵"], dkind, dkind, merged, k_idx,
                f"attr_matrix[{k_idx}]", f"spec.attr_matrix#{k_idx} {variant}")
    return plan


def gen_cases(spec, work_dir):
    op = spec["op"]
    if op not in GOLDEN:
        raise ValueError(f"unsupported op {op!r}, supported={list(GOLDEN)}")
    src_name, golden_fn = GOLDEN[op]
    rng = np.random.default_rng(SEED)
    in_params = [p for p in spec["params"] if p["io"] == "in"]
    attrs_default = {p["name"]: p.get("default") for p in spec["params"] if p["io"] == "attr"}
    self_param = next((p for p in in_params if p["name"] == "self"), in_params[0])
    dtypes = self_param["dtype"]
    if len(dtypes) != len(set(dtypes)):                   # finding #13 根因：dtype 集含重复 → plan entry 撞车
        dup = sorted(d for d in set(dtypes) if dtypes.count(d) > 1)
        raise ValueError(f"spec dtype 集含重复项 {dup}（会致 case_id 碰撞/伪造覆盖，fail-fast）")
    for dtn in dtypes:                                    # dtype 白名单校验（fail-fast，不静默）
        if dtn != _BF16 and dtn not in _NATIVE:
            raise ValueError(f"unsupported dtype {dtn!r}（gen_cases 支持 {sorted(_NATIVE)} + bfloat16）")
    spec_standard = precision_policy.select_standard(spec)  # 平台层标准（显式或按 oracle+verify_mode 映射）
    vmode = spec["verify_mode"]
    exact = vmode == "exact"
    os.makedirs(work_dir, exist_ok=True)

    plan = _plan(spec, in_params, dtypes, attrs_default, op)
    seen_ids, cases = set(), []
    for entry in plan:
        dims, shp, dtn = entry["dims"], entry["shape"], entry["dtype"]
        attrs, data_kind = entry["attrs"], entry["data_kind"]
        cid = _mk_id(op, dtn, shp, entry["id_kind"], entry["attr_idx"], seen_ids)
        cdir = os.path.join(work_dir, cid)
        os.makedirs(cdir, exist_ok=True)

        inputs = _build_inputs(rng, in_params, shp, dtn, attrs, data_kind)  # 逻辑数组（compute dtype）
        golden = golden_fn(inputs, attrs)                # 用逻辑输入算 golden
        if not exact:
            golden = golden.astype(_compute_np(dtn))     # numerical：golden 同逻辑 dtype（bf16→fp32-on-grid）
        # finding #11：裸 assert 被 python -O 剥离 → 改 raise，任何优化级别都生效（防 -O 下静默产坏 caseset）。
        if exact and golden.dtype == bool and golden.size > 1:
            if not (golden.any() and (~golden).any()):
                raise ValueError(f"{cid}: golden 未覆盖 True/False 边界（exact bool 用例数据缺陷）")
        # finding #10：equal_nan variant 必须**真起作用**——断言输入含 aligned-NaN 且两版 golden 不相等，
        # 否则 fail-fast（否则 equal_nan 被忽略也逐位对上 golden → 假覆盖，却标着「attr矩阵/功能/精度」）。
        if data_kind == "nanpair":
            _assert_equal_nan_effective(golden_fn, inputs, attrs, cid)

        # 保存：X_bin(x{j}.npy·物理位模式) 与 golden(golden.npy·op(逻辑值)) **分两份造**（canonical 职责#2/#3）
        storage_np = _storage_np(dtn)
        ishapes, has_storage = [], (dtn == _BF16)
        for j, x_logical in enumerate(inputs):
            if dtn == _BF16:                             # 物理 = 从逻辑**单独 encode** 出的 uint16 位模式
                x_bin = _f32_to_bf16_uint16(x_logical)
                if np.shares_memory(x_bin, x_logical):   # finding #11：改 raise（-O 下 assert 会被剥离）
                    raise ValueError(f"{cid}: bf16 X_bin 与 X_logical 共享内存（违 layout 字节契约 职责#2）")
            else:
                x_bin = np.ascontiguousarray(x_logical, dtype=storage_np)
            np.save(os.path.join(cdir, f"x{j + 1}.npy"), x_bin)
            ishapes.append(list(x_logical.shape))
        np.save(os.path.join(cdir, "golden.npy"), golden)

        # 精度口径 per-case：cdtype **据 spec IO 矩阵派生**（与 validator 同源 derive_output_dtype，绝不取 golden
        # 自声明；bf16 numerical 输出→'bfloat16'、bool 输出(IsClose/Equal 即便 bf16 输入)→'bool'）。
        case_in_dts = [(p["name"], dtn) for p in in_params]
        logical_cdtype = precision_policy.derive_output_dtype(spec, case_in_dts)
        out_is_bool = (golden.dtype == bool)
        # finding #14：bf16 白名单与「输出是否 bool/exact 语义」**拆成两道独立校验**——verify_mode=exact 不再
        # 短路豁免 bf16。bf16 且**输出非 bool**（真数值输出）且 op 不在白名单 → 需 lossy 阈值 → fail-fast。
        if dtn == _BF16 and not out_is_bool and op not in _BF16_EXACT_OPS:
            raise ValueError(f"bf16 numerical for op {op!r} 需 lossy 阈值（输出非 bool、不在 _BF16_EXACT_OPS，"
                             f"本轮无此类算子，留 gap；不因 verify_mode=exact 静默放行）")
        if exact:
            compare = "exact_equal"
        elif precision_policy.is_integer_dtype(dtn):
            compare = "exact_equal"                      # §1.1 int→exact（有效标准也会强制 EXACT）
        elif dtn == _BF16:
            compare = "exact_equal"                      # Sign/Neg bf16 输出精确可表示（已过上文白名单）
        else:
            compare = "rel_err"                          # fp32/fp16 数值 → 沿用平台标准（向后兼容）
        eff_std = precision_policy.effective_standard(spec_standard, logical_cdtype, compare)
        policy = precision_policy.threshold_for(eff_std, logical_cdtype)
        tpid = precision_policy.tolerance_policy_id(eff_std, logical_cdtype)
        expected = {"golden_source": src_name, "golden_path": f"{cid}/golden.npy",
                    "verify_mode": vmode, "standard": eff_std, "compare_dtype": logical_cdtype,
                    "compare": compare, "tolerance_policy_id": tpid, "policy": policy,
                    "threshold": precision_policy.threshold_digest(policy),  # digest：向后兼容
                    "case_origin": entry["case_origin"], "rule_ref": entry["rule_ref"]}
        acc = precision_policy.resolve_acceptance(spec, eff_std, logical_cdtype)
        if acc:
            expected["acceptance_policy"], expected["acceptance_tolerance_policy_id"] = acc
        in_items = []
        for j in range(len(inputs)):
            item = {"name": in_params[j]["name"], "shape": ishapes[j], "dtype": dtn,
                    "path": f"{cid}/x{j + 1}.npy"}
            if has_storage:                              # 仅物理≠逻辑时带 storage_dtype（native 保向后兼容）
                item["storage_dtype"] = _storage_name(dtn)
            in_items.append(item)
        cases.append({"id": cid, "dims": dims, "tags": entry["tags"],
                      "inputs": in_items, "attrs": attrs, "expected": expected})
    attr_order = [p["name"] for p in spec["params"] if p["io"] == "attr"]
    return {"op": op, "spec_ref": spec.get("op"), "work_dir": work_dir,
            "attr_order": attr_order, "cases": cases}


def main(argv):
    spec_path, work_dir, out_path = argv[0], argv[1], argv[2]
    spec = json.load(open(spec_path, encoding="utf-8"))
    caseset = gen_cases(spec, work_dir)
    json.dump(caseset, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[gen_cases] {caseset['op']}: {len(caseset['cases'])} cases -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1:])
