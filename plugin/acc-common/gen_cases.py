"""Task 1 · gen_cases — spec.json -> caseset.json (+ per-case input/golden .npy).

Layer 1 确定性脚本（工具中立、op 驱动）。据 spec（参数 arity/attrs、verify_mode、dtype 集、可选 attr_matrix）
× dtype × shape × 泛化生成用例，用参考实现算 golden（逐算子分发；golden_source 记来源，不设全局假设）。
支持 IsClose/Sign/Equal/Neg（样例 golden 在 `samples/golden/<op>/golden.py`）。**加算子 = 用户侧 `<ops_root>/<op>/golden.py`**——**elementwise 通路**不含内置 golden 值、按算子加载
（ADR 0011：golden 去引擎化，`proposed`）。⚠ 非「引擎零内置算子」：catlass_adapter 的 matmul golden 与本文件
`_BF16_EXACT_OPS` 是两处已知例外。
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
import hashlib, importlib.util, json, math, os, sys
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
# ADR 0011（golden 去引擎化，proposed）：**本 elementwise 通路**不含内置 golden 值——按算子从用户侧
# `<ops_root>/<op>/golden.py` 加载。⚠ 非「引擎零内置算子」：catlass_adapter 的 matmul golden 与上面的
# `_BF16_EXACT_OPS` 仍是引擎里的算子知识（两处已知例外，如实记账）。
# 4 个历史内置 golden（IsClose/Sign/Equal/Neg）迁 `samples/golden/<op>/golden.py` 作只读参考（非运行时回退靶）。
# 后端（决策 4）：golden 恒 CPU、torch 优先——现 4 算子 golden.py 皆 torch；torch 缺失在 golden.py 内 fail-closed。
def load_golden(op):
    """按算子名从用户侧加载 golden——`<ops_root>/<op>/golden.py`，返回 `(golden_fn, golden_source, provenance)`。

    **本加载路径不含内置 golden 值、绝不回退内置/样例**（ADR 0011 决策 1/2）：缺 golden.py → **fail-closed** 报错。
    （⚠ 仅指 elementwise 通路；catlass 通路与 `_BF16_EXACT_OPS` 仍是引擎里的算子知识。）
    golden.py 须导出 `golden_fn(inputs, attrs) -> ndarray` + `GOLDEN_SOURCE`（首 token = oracle_source 六枚举之一：
    cpu_ref/catlass_existing_ref/task_spec_expected/torch_ref/analytical_ref/external_ref——**支撑多仓多算子的各类来源**；
    elementwise 内置样例可用 backend 简写 torch/numpy）+ `GOLDEN_PROVENANCE`（来源出处）；缺任一 → fail-closed。
    样例见 `samples/golden/<op>/golden.py`。

    安全（golden.py 会被 import 执行 = 执行用户/生成的 Python，性质同 runner.cpp、同信任级，ADR 0011 决策 6）：
    `op` 经 `_check_id` 校验、路径由已校验 op 名定死；**软链分两层挡**——`<ops_root>/<op>` **目录段**由
    `repo_adapter.op_dir()` 的 `_reject_symlink_segments` 逐段拒，`golden.py` **最终文件**那一层由本函数
    `os.path.islink` 拒（⚠ 旧注释只写「拒符号链接」，读起来像已全防住：`islink` 只看最终组件，目录段软链
    会被 import 静默跟随出去）。⚠ **两层只挡静态软链、不解 TOCTOU**：校完到真正 import 之间的窗口仍在，
    可被 rename 换靶；真封堵要 `O_NOFOLLOW`/`openat` 逐级打开（本仓 `perf_sim_plot._safe_open_write` 是那
    个路子，此处未跟进）。另 ops_root 自身与 `.oprunway`/`ops` 两段未逐段查（`realpath` 会抹掉「root 本身
    是软链」这一事实）——如实记账，别当已全防住；
    缺则 fail-closed（不回退内置/样例）；`importlib` 隔离 import、不污染 `sys.path`。
    """
    import repo_adapter                              # 延迟 import：repo_adapter 顶层已 import gen_cases，避加载期循环
    repo_adapter._check_id("op_name", op)
    # <ops_root>/<op>/golden.py（拒落插件树、env 覆盖、目录段软链，同 runner——三者都由 op_dir() 把关）
    gpath = os.path.join(repo_adapter.op_dir(op), "golden.py")
    try:
        os.lstat(gpath)                             # lstat：不跟随软链
    except FileNotFoundError:
        raise ValueError(
            f"缺 golden: {gpath}（引擎不回退内置 golden，fail-closed）\n"
            f"  → 新算子需先由 acc-spec/acc-runner-dev 从任务书生成 golden.py 落到用户目录"
            f"（可照 samples/golden/<op>/golden.py 的只读样例）；或设 OPRUNWAY_OPS_DIR / OPRUNWAY_WORK_DIR。")
    except OSError as ex:
        raise ValueError(f"golden.py 不可访问: {gpath!r}: {ex}")
    if os.path.islink(gpath):                       # 仅最终组件；目录段由 repo_adapter.op_dir() 逐段拒
        raise ValueError(f"golden.py 是符号链接，拒绝（防路径逃逸/换靶）: {gpath!r}")
    if not os.path.isfile(gpath):
        raise ValueError(f"golden.py 路径存在但不是普通文件: {gpath!r}")
    spec = importlib.util.spec_from_file_location(f"oprunway_golden_{op.lower()}", gpath)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)                 # 执行 golden.py（同 runner.cpp 信任级：用户/生成的代码）
    except Exception as ex:                          # noqa: BLE001 —— 语法/import 期异常统一 fail-closed 成 ValueError
        raise ValueError(f"golden.py 执行失败: {gpath}: {ex}") from ex
    for attr in ("golden_fn", "GOLDEN_SOURCE", "GOLDEN_PROVENANCE"):
        if not hasattr(mod, attr):
            raise ValueError(f"golden.py 缺 `{attr}`（须导出 golden_fn + GOLDEN_SOURCE + GOLDEN_PROVENANCE）: {gpath}")
    if not callable(mod.golden_fn):
        raise ValueError(f"golden.py 的 golden_fn 不可调用: {gpath}")
    if not (isinstance(mod.GOLDEN_SOURCE, str) and mod.GOLDEN_SOURCE.strip()):
        raise ValueError(f"golden.py 的 GOLDEN_SOURCE 须非空字符串（供 oracle_source 映射）: {gpath}")
    if not (isinstance(mod.GOLDEN_PROVENANCE, str) and mod.GOLDEN_PROVENANCE.strip()):
        raise ValueError(f"golden.py 的 GOLDEN_PROVENANCE 须非空字符串（来源出处）: {gpath}")
    return mod.golden_fn, mod.GOLDEN_SOURCE, mod.GOLDEN_PROVENANCE


# ================================================= 逻辑输入构造（compute dtype）
def _make_varied(rng, shape, dtn, regime="uniform"):
    """含负/零/正的一般输入（Sign 全分支覆盖）。int：整数网格且**排除 dtype 最小值**（避 np.negative 溢出，
    codex#14）；bf16：fp32 造后 round 到 bf16 网格（返回 fp32-on-grid 逻辑值）。
    regime（§1.2 值域）：uniform=均匀[-5,5]；normal=正态(μ,σ) 后 clip 到 [-5,5]。int dtype 忽略 regime。"""
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
    if regime == "normal":                               # §1.2 正态 50%（clip 到 [-5,5] 避极端离群主导）
        x = np.clip(rng.normal(_NORMAL_MU, _NORMAL_SIGMA, size=shape), -5.0, 5.0).astype(np.float32)
    else:                                                # §1.2 均匀 50%
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


def _build_value_special(rng, arity, shp, dtn, kind):
    """§1.4 INF/-INF/NAN 特殊值输入（仅浮点）：前 1/4 位放特殊值（二元对齐）、其余常规均匀。
    对齐放置使 IsClose(inf,inf)=True / (nan,nan,equal_nan)=按 flag，golden 天然含混合。"""
    cdt = _compute_np(dtn)
    val = {"inf": np.inf, "ninf": -np.inf, "nan": np.nan}[kind]
    n = _numel(shp)
    k = max(1, n // 4)

    def one():
        x = rng.uniform(-5.0, 5.0, size=shp).astype(np.float32)
        f = x.reshape(-1)
        f[:k] = np.float32(val)                          # 前 k 位特殊值（二元两输入同位 → 对齐）
        x = f.reshape(shp)
        return _bf16_round(x) if dtn == _BF16 else x.astype(cdt)
    return [one() for _ in range(max(1, arity))]


def _build_inputs(rng, in_params, shp, dtn, attrs, data_kind):
    """造该 case 的**逻辑**输入数组列表（compute dtype；bf16=fp32-on-grid）。物理化在保存步单独做。
    data_kind 形如 base 或 base:regime（regime∈{uniform,normal}，仅 varied/pair 系用）；
    特殊 base：empty(§1.4 空)/inf/ninf/nan(§1.4 特殊值)。"""
    arity = len(in_params)
    base = data_kind.split(":")[0]
    regime = data_kind.split(":")[1] if ":" in data_kind else "uniform"
    if base == "empty":                                  # §1.4 空 Tensor（numel=0）：按 shape 造空数组
        cdt = _compute_np(dtn)
        z = np.zeros(shp, dtype=cdt)
        return [z for _ in range(max(1, arity))]
    if base in ("inf", "ninf", "nan"):                   # §1.4 特殊值遍历
        return _build_value_special(rng, arity, shp, dtn, base)
    if shp == "broadcast":                               # 仅二元：self (4,1) vs other (1,5)
        return [_make_varied(rng, (4, 1), dtn, regime), _make_varied(rng, (1, 5), dtn, regime)]
    if base == "nanpair":                                # nan_pair 同造 a、b
        a, b = _make_nanpair(rng, shp, dtn, attrs)
        return [a, b]
    x0 = _make_varied(rng, shp, dtn, regime)
    if arity == 1:
        return [x0]
    if base == "pairfar":
        x1 = _make_pairfar(rng, shp, dtn, x0, attrs)
    elif base == "pairhalf":
        x1 = _make_pairhalf(shp, dtn, x0)
    elif base == "pairint":
        x1 = _make_pairint(shp, dtn, x0)
    else:                                                # varied（广播已上文返回）
        x1 = _make_varied(rng, shp, dtn, regime)
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


# ============================== §1 覆盖-预算 生成（opbase 精度标准 §1，pin f69d4e…）=====
# 决策 v2（doc/oprunway-cases50-design.md）：dtype 分层（key 重点 + 其他 1-2）× shape 阶梯(2^k/2^k-1)
# × 值域(uniform+normal) × attr 正交笛卡尔；白名单强制必覆盖组合 + 1-wise 采样 + case_target 预算封顶；
# §1.4 特殊场景（空→功能only / 标量 / 边界 / inf·nan）强制纳入、id_kind 独立命名空间；per-case 独立种子。
# format 轴：elementwise 仅 ND（op_def/example 佐证）→ 退化为单值，不进正交网格。
KEY_DTYPES = ("float32", "float16", "bfloat16")     # §重点覆盖档
_OTHER_DTYPE_QUOTA = 2                               # 非重点 dtype 每种至多 N 条（主流场景）
_DEFAULT_CASE_TARGET = 50

# §1.2 shape 阶梯：维度值取 2^k / 2^k-1（∈[1,2^20]），dims 1~8，总元素 ≤ 2^31。有限有序表（CAP 防爆炸）。
_REG_SHAPES = [(3,), (4,), (7,), (16,), (255,), (4, 4), (7, 8), (16, 15),
               (2, 3, 4), (3, 3, 3), (2, 2, 2, 2)]     # 常规功能/精度（2^k 与 2^k-1 混、1~4 维）
_LARGE_SHAPES = [(1024, 1024), (65535,)]               # perf 有意义大 shape（2^20 / 2^16-1）
_MAX_NUMEL = 2 ** 31

# §1.2 值域：50% 均匀[-5,5] + 50% 正态(μ∈[-5,5],σ∈[0.1,2])。正态取确定性代表 (μ,σ)。
_VALUE_REGIMES = ("uniform", "normal")
_NORMAL_MU, _NORMAL_SIGMA = 0.0, 1.0


def _case_rng(case_id):
    """per-case 独立种子（评审 #7）：数据只依赖稳定 case_id，与选择/顺序/target 全解耦。
    同一 case_id 在任何 target/子集下产同一字节，扩 target 不改老用例。"""
    h = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:16], 16)
    return np.random.default_rng((SEED ^ h) & ((1 << 64) - 1))


def _numel(shape):
    n = 1
    for d in shape:
        n *= int(d)
    return n


def _attr_value_sets(spec, attrs_default):
    """§1.3：每 attr 的取值集——布尔→[F,T]、枚举→全值、标量→等价类代表（默认值）。
    有 attr_matrix 时用它给的取值集（每 key 的并集，保序）；否则据 attr dtype/默认派生。
    返回 [(name, [values])]，供笛卡尔展开（attr 作真正交轴，评审 #12）。"""
    attr_params = [p for p in spec["params"] if p["io"] == "attr"]
    matrix = spec.get("attr_matrix")
    # finding #12（§1 重写勿丢）：attr_matrix 每项须为 dict、key ⊆ spec io=='attr' 名集、值为标量——
    # 防伪造 attr key（如 {foo:12345}）冒充覆盖 / 非标量值。fail-fast，不静默忽略未知 key。
    if matrix:
        attr_names = {p["name"] for p in attr_params}
        for k_idx, variant in enumerate(matrix):
            if not isinstance(variant, dict):
                raise ValueError(f"attr_matrix[{k_idx}] 须为 attr 字典，得 {type(variant).__name__}")
            unknown = set(variant) - attr_names
            if unknown:
                raise ValueError(f"attr_matrix[{k_idx}] 含未知 attr key {sorted(unknown)}"
                                 f"（须 ⊆ spec io=='attr' 名集 {sorted(attr_names)}，防伪造覆盖）")
            for k, v in variant.items():
                if not isinstance(v, (bool, int, float, str)) or v is None:
                    raise ValueError(f"attr_matrix[{k_idx}].{k}={v!r} 非标量（须 bool/int/float/str）")
    out = []
    for p in attr_params:
        name = p["name"]
        if matrix:
            vals = []
            for v in matrix:
                if isinstance(v, dict) and name in v and v[name] not in vals:
                    vals.append(v[name])
            if not vals:
                vals = [attrs_default.get(name)]
        else:
            dt = (p.get("dtype") or [None])[0]
            vals = [False, True] if dt == "bool" else [attrs_default.get(name)]
        out.append((name, vals))
    return out


def _attr_combos(attr_sets, attrs_default):
    """attr 取值集笛卡尔展开为 attr 字典列表（保序、确定）。空 attr → 单个默认字典。"""
    combos = [dict(attrs_default)]
    for name, vals in attr_sets:
        combos = [{**c, name: v} for c in combos for v in vals]
    return combos


def _dtype_shapes(dtn, is_key):
    """该 dtype 的常规 shape 集：key dtype 用全阶梯 + 大 shape；非 key 只取前 N 个主流 shape（配额）。"""
    reg = [s for s in _REG_SHAPES if _numel(s) <= _MAX_NUMEL]
    if is_key:
        return reg + [s for s in _LARGE_SHAPES if _numel(s) <= _MAX_NUMEL]
    return reg[:_OTHER_DTYPE_QUOTA]                     # 非重点 dtype：主流少量


def _special_entries(op, dtn, arity, is_float, rep_attrs):
    """§1.4 特殊场景（不与常规正交、强制纳入）：空(功能only)/标量[1]/边界下(全1)/边界上(大)/inf/-inf/nan。
    每项 (dims, shape, data_kind, id_kind)。整型 dtype 跳过 inf/nan（无此值）。"""
    E = []
    # 空 Tensor：某维=0 → 只挂「功能」（无精度/无 kernel profile；validator numel=0→na、adapter 优雅跳过）
    E.append((["功能"], ((0,) if arity else (0,)), "empty", "empty"))
    # 标量 Tensor [1]（numel=1，退化 perf → 下游 trivial-met）
    E.append((["功能", "精度", "性能"], (1,), "varied", "scalar"))
    # 边界：下=各维均 1；上=大 shape 某维取大
    E.append((["功能", "精度", "性能"], (1, 1, 1), "varied", "bndlo"))
    E.append((["功能", "精度", "性能"], _LARGE_SHAPES[0], "varied", "bndhi"))
    # INF/-INF/NAN 遍历（仅浮点；每种值一条，shape 用中等 (16,)）——**带「性能」**（v2：非空皆带性能/同输入；
    # numel=16<阈值 → perf_compare 判 trivial-met 免测，不假 fail）。
    if is_float:
        for val_kind in ("inf", "ninf", "nan"):
            E.append((["功能", "精度", "性能"], (16,), val_kind, val_kind))
    return E


def _regular_data_kind(dtn, attrs, arity):
    """常规 case 的 data_kind base：一元→varied；二元→_binary_data_kind（int/close/exact 三分）。"""
    return "varied" if arity == 1 else _binary_data_kind(dtn, attrs)


def _entry_key(e):
    """case 唯一键（与 _mk_id 的 (dtype,shape_tag,id_kind,attr_idx) 同口径），供去重/采样。"""
    return (e["dtype"], _shape_tag(e["shape"]), e["id_kind"], e["attr_idx"])


def _axes(e):
    """1-wise 采样的四轴取值：dtype / shape / regime / attr。regime 从 data_kind 尾段取（无则 uniform）。"""
    dk = e["data_kind"]
    regime = dk.split(":")[1] if ":" in dk else "uniform"
    return (e["dtype"], _shape_tag(e["shape"]), regime, e["attr_idx"])


def _one_wise_pick(grid, n, used):
    """从 grid 确定性取 n 条（选择端无 rng）：**按 dtype round-robin 均衡**（fp16/fp32/bf16 重点均等，
    不偏斜到排序靠前的 dtype）；每 dtype 队列内先排「引入新 (shape,regime,attr)」的（per-dtype 1-wise），
    余量按原序。跨 dtype 轮转取，直到 n 或 grid 耗尽。tie-break=原始索引。"""
    if n <= 0:
        return []
    from collections import OrderedDict
    by_dt = OrderedDict()
    for e in grid:
        if _entry_key(e) not in used:
            by_dt.setdefault(e["dtype"], []).append(e)

    def _order_within(lst):                              # per-dtype 1-wise 前置
        seen = {"s": set(), "r": set(), "a": set()}
        head, tail = [], []
        for e in lst:
            _, s, r, a = _axes(e)
            if s not in seen["s"] or r not in seen["r"] or a not in seen["a"]:
                head.append(e); seen["s"].add(s); seen["r"].add(r); seen["a"].add(a)
            else:
                tail.append(e)
        return head + tail

    queues = [_order_within(v) for v in by_dt.values()]
    picked, pk, idx = [], set(), [0] * len(queues)
    while len(picked) < n:                               # 跨 dtype round-robin（均衡）
        progressed = False
        for qi, q in enumerate(queues):
            if len(picked) >= n:
                break
            while idx[qi] < len(q):
                e = q[idx[qi]]; idx[qi] += 1
                k = _entry_key(e)
                if k not in pk:
                    picked.append(e); pk.add(k); progressed = True
                    break
        if not progressed:                              # 所有队列耗尽
            break
    return picked


def _dropped_classes(grid, emitted):
    """被采样丢弃的 (dtype×shape) 组合类简述（可审计；上限 50 条防爆）。"""
    emk = {(e["dtype"], _shape_tag(e["shape"])) for e in emitted}
    dropped = sorted({f'{e["dtype"]}×{_shape_tag(e["shape"])}' for e in grid
                      if (e["dtype"], _shape_tag(e["shape"])) not in emk})
    return dropped[:50]


# ================================================= 计划构建（§1 覆盖-预算）=========
def _plan(spec, in_params, dtypes, attrs_default, op, case_target):
    """§1 覆盖-预算计划。返回 (entries, meta)。选择端无 rng（结构序 + 原始索引 tie-break）。
    ① §1.4 特殊场景（每 dtype，强制）→ ② 白名单必覆盖（key dtype × 每 attr × 大 shape，强制，防关键联合被采样丢）
    → ③ 常规正交网格（dtype×shape×值域×attr）作 1-wise 采样源，填到 budget=max(case_target, |forced|)。
    format 轴：elementwise 仅 ND（op_def/example 佐证）→ 退化为单值，不进网格。"""
    arity = len(in_params)
    attr_sets = _attr_value_sets(spec, attrs_default)
    attr_combos = _attr_combos(attr_sets, attrs_default)

    def _akey(a):
        return tuple((k, a.get(k)) for k in attrs_default)
    combo_idx = {_akey(a): i for i, a in enumerate(attr_combos)}

    def mk(dims, shp, dtn, data_kind, id_kind, attrs, origin, rule, tags):
        return {"dims": list(dims), "shape": shp, "dtype": dtn, "tags": list(tags),
                "data_kind": data_kind, "id_kind": id_kind, "attrs": dict(attrs),
                "attr_idx": combo_idx.get(_akey(attrs)), "case_origin": origin, "rule_ref": rule}

    forced, grid = [], []
    # ① §1.4 特殊场景（每 dtype 强制；id_kind 独立命名空间，评审 #8）
    for dtn in dtypes:
        is_float = not precision_policy.is_integer_dtype(dtn)
        for dims, shp, dk, ik in _special_entries(op, dtn, arity, is_float, attr_combos[0]):
            forced.append(mk(dims, shp, dtn, dk, ik, attr_combos[0],
                             f"special:{ik}", f"opbase §1.4 {ik}", ["特殊"]))
    # ② 白名单必覆盖（key dtype × 每 attr 取值 × 大 shape）——保证关键联合不被 1-wise 采样丢（评审 #6）
    for dtn in dtypes:
        if dtn not in KEY_DTYPES:
            continue
        dk = _regular_data_kind(dtn, attrs_default, arity)
        for attrs in attr_combos:
            ai = combo_idx[_akey(attrs)]
            forced.append(mk(["功能", "精度", "性能"], _LARGE_SHAPES[0], dtn, f"{dk}:uniform",
                             f"wl{ai}", attrs, f"whitelist:{dtn}:a{ai}",
                             "opbase §1.1 必覆盖组合(key×attr×大shape)", ["白名单"]))
    # ③ 常规正交网格（1-wise 采样源）：dtype × shape × 值域 × attr（regime 编进 id_kind 保 case_id 唯一）
    for dtn in dtypes:
        is_key = dtn in KEY_DTYPES
        dk = _regular_data_kind(dtn, attrs_default, arity)
        for shp in _dtype_shapes(dtn, is_key):
            for regime in _VALUE_REGIMES:
                for attrs in attr_combos:
                    ai = combo_idx[_akey(attrs)]
                    grid.append(mk(["功能", "精度", "性能"], shp, dtn, f"{dk}:{regime}",
                                   f"grid{regime[0]}", attrs,
                                   f"grid:{dtn}:{_shape_tag(shp)}:{regime}:a{ai}",
                                   "opbase §1.1/§1.2 正交网格", ["常规"]))
    # 预算：forced 全量 + grid 1-wise 采样填到 budget（forced 大于 target 时 emit>target，评审 #8 允许并 note）
    n_special = sum(1 for e in forced if e["tags"] == ["特殊"])
    budget = max(int(case_target), len(forced))
    used = {_entry_key(e) for e in forced}
    entries = list(forced) + _one_wise_pick(grid, budget - len(forced), used)
    grid_avail = sum(1 for e in grid if _entry_key(e) not in used)
    emitted_from_grid = len(entries) - len(forced)
    meta = {
        "pool_max": len(forced) + grid_avail,
        "requested_target": int(case_target),
        "emitted": len(entries),
        "forced_special": n_special,
        "forced_total": len(forced),          # 强制下限 S = 特殊场景 + 白名单（emit 不会少于此；acc-spec 取此作 S）
        "dropped_combo_classes": (_dropped_classes(grid, entries)
                                  if emitted_from_grid < grid_avail else []),
        "coverage_strength": ("1-wise+whitelist：特殊场景(§1.4) + key dtype×attr×大shape 全覆盖；"
                              "常规 dtype×shape×值域×attr 联合仅边际 1-wise（50 封顶下 §1.1 100% 正交不可达）"),
    }
    if int(case_target) < len(forced):                    # 强制下限 > target（评审 #8）：emit>target，note
        meta["note_target_below_forced"] = (f"case_target={case_target} < 强制下限 {len(forced)}"
                                             f"（特殊场景+白名单），实际 emit={len(entries)}")
    return entries, meta


def gen_cases(spec, work_dir):
    op = spec["op"]
    # golden 按算子从用户侧 <ops_root>/<op>/golden.py 加载（elementwise 通路不内置 golden 值、缺则 fail-closed；
    # ADR 0011 决策 1/2/5，proposed）。⚠ 非「引擎零内置算子」——catlass_adapter 的 matmul golden 与本文件 :34
    # 的 _BF16_EXACT_OPS 是两处已知例外，仍是引擎里的算子知识。
    # golden_source 来自加载的 GOLDEN_SOURCE 元数据（决策 5），下游门继续校 oracle_source==映射(golden_source)。
    golden_fn, golden_source, _golden_provenance = load_golden(op)
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

    # §1 用例预算 case_target（spec.precision.case_target，默认 50）。校验 int 且 ≥1——堵 0/负/非整
    # 空跑冒充验收（评审 #5）；< 强制下限时 _plan 用 max(target,|forced|)、emit>target 并 note（评审 #8）。
    case_target = (spec.get("precision") or {}).get("case_target", _DEFAULT_CASE_TARGET)
    if isinstance(case_target, bool) or not isinstance(case_target, int) or case_target < 1:
        raise ValueError(f"precision.case_target 须为 ≥1 的整数（防零用例空跑冒充验收），得 {case_target!r}")

    entries, plan_meta = _plan(spec, in_params, dtypes, attrs_default, op, case_target)
    seen_ids, cases = set(), []
    for entry in entries:
        dims, shp, dtn = entry["dims"], entry["shape"], entry["dtype"]
        attrs, data_kind = entry["attrs"], entry["data_kind"]
        cid = _mk_id(op, dtn, shp, entry["id_kind"], entry["attr_idx"], seen_ids)
        cdir = os.path.join(work_dir, cid)
        os.makedirs(cdir, exist_ok=True)
        case_rng = _case_rng(cid)                        # per-case 独立种子（数据只依赖稳定 cid，评审 #7）

        inputs = _build_inputs(case_rng, in_params, shp, dtn, attrs, data_kind)  # 逻辑数组（compute dtype）
        golden = golden_fn(inputs, attrs)                # 用逻辑输入算 golden
        if not exact:
            golden = golden.astype(_compute_np(dtn))     # numerical：golden 同逻辑 dtype（bf16→fp32-on-grid）

        # §1.4 空 Tensor（numel=0）：只挂「功能」、无精度判定；存空 X/golden，expected compare=na（评审 #1）。
        if entry["id_kind"] == "empty":
            for j, x_logical in enumerate(inputs):
                x_bin = (_f32_to_bf16_uint16(x_logical) if dtn == _BF16
                         else np.ascontiguousarray(x_logical, dtype=_storage_np(dtn)))
                np.save(os.path.join(cdir, f"x{j + 1}.npy"), x_bin)
            np.save(os.path.join(cdir, "golden.npy"), golden)
            in_items = [{"name": in_params[j]["name"], "shape": list(inputs[j].shape),
                         "dtype": dtn, "path": f"{cid}/x{j + 1}.npy",
                         **({"storage_dtype": _storage_name(dtn)} if dtn == _BF16 else {})}
                        for j in range(len(inputs))]
            cases.append({"id": cid, "dims": dims, "tags": entry["tags"], "inputs": in_items,
                          "attrs": attrs,
                          "expected": {"golden_source": golden_source, "golden_path": f"{cid}/golden.npy",
                                       "verify_mode": vmode, "compare": "na", "standard": "na",
                                       "compare_dtype": None, "case_origin": entry["case_origin"],
                                       "rule_ref": entry["rule_ref"],
                                       "note": "空Tensor 功能用例（numel=0，无精度判定，validator→na）"}})
            continue

        # finding #11：裸 assert 被 python -O 剥离 → 改 raise，任何优化级别都生效（防 -O 下静默产坏 caseset）。
        # 评审 #10：§1.4 特殊值(inf/ninf/nan)可产均一 bool golden → 豁免「必混 True/False」断言（仅常规 grid/wl 校）。
        if (exact and golden.dtype == bool and golden.size > 1
                and entry["id_kind"] not in ("inf", "ninf", "nan")):
            if not (golden.any() and (~golden).any()):
                raise ValueError(f"{cid}: golden 未覆盖 True/False 边界（exact bool 用例数据缺陷）")
        # finding #10：equal_nan variant 必须**真起作用**（仅 nanpair 数据路径；新 §1 不产 nanpair、保留兼容）。
        if data_kind.split(":")[0] == "nanpair":
            _assert_equal_nan_effective(golden_fn, inputs, attrs, cid)

        # 保存：X_bin(x{j}.npy·物理位模式) 与 golden(golden.npy·op(逻辑值)) **分两份造**（canonical 职责#2/#3）
        storage_np = _storage_np(dtn)
        ishapes, has_storage = [], (dtn == _BF16)
        for j, x_logical in enumerate(inputs):
            if dtn == _BF16:                             # 物理 = 从逻辑**单独 encode** 出的 uint16 位模式
                x_bin = _f32_to_bf16_uint16(x_logical)
                if x_bin.size and np.shares_memory(x_bin, x_logical):  # finding #11：改 raise（空数组免检）
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
        expected = {"golden_source": golden_source, "golden_path": f"{cid}/golden.npy",
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
    # Q7 dtype 覆盖门用：dtype_required=任务书权威全集（spec 透传，未声明则 None→门不阻塞）；
    # dtype_tested=实测子集，**从实际生成的 cases 归并**（非 in 参数并集——门也用真实 cases 对账，两侧口径一致、
    # 消除「并集过报」与「自报漂移」）；task_pr_gaps 透传供门查 dtype_deferred。
    dtype_tested = sorted({c["inputs"][0]["dtype"] for c in cases
                           if c.get("inputs") and c["inputs"][0].get("dtype")})
    return {"op": op, "spec_ref": spec.get("op"), "work_dir": work_dir,
            "attr_order": attr_order,
            "dtype_required": spec.get("dtype_required"),
            "dtype_tested": dtype_tested,
            "task_pr_gaps": spec.get("task_pr_gaps", []),
            # §1 覆盖账本（评审 #9：导出让数量门/用户区分「结构性达不到」vs「bug 少出」、审计被丢组合）
            "pool_max": plan_meta["pool_max"],
            "requested_target": plan_meta["requested_target"],
            "emitted": plan_meta["emitted"],
            "coverage_strength": plan_meta["coverage_strength"],
            "dropped_combo_classes": plan_meta["dropped_combo_classes"],
            "cases": cases}


def _dry_run(spec):
    """plan-only（不算 golden、不 import torch、不落 .npy）：打印覆盖账本 + 确定性自检。供测试与 acc-spec 预探。"""
    from collections import Counter
    op = spec["op"]
    in_params = [p for p in spec["params"] if p["io"] == "in"]
    attrs_default = {p["name"]: p.get("default") for p in spec["params"] if p["io"] == "attr"}
    self_param = next((p for p in in_params if p["name"] == "self"), in_params[0])
    dtypes = self_param["dtype"]
    case_target = (spec.get("precision") or {}).get("case_target", _DEFAULT_CASE_TARGET)
    entries, meta = _plan(spec, in_params, dtypes, attrs_default, op, case_target)
    seen, ids = set(), []
    for e in entries:                                    # 跑 _mk_id 校 id 唯一（撞则 raise）
        ids.append(_mk_id(op, e["dtype"], e["shape"], e["id_kind"], e["attr_idx"], seen))
    specials = {"empty", "scalar", "bndlo", "bndhi", "inf", "ninf", "nan"}
    print(f"[dry-run] {op} target={case_target} emitted={meta['emitted']} pool_max={meta['pool_max']} "
          f"forced_total(=强制下限S)={meta['forced_total']} forced_special={meta['forced_special']}")
    print(f"  区间: case_target 建议落 [S={meta['forced_total']}, pool_max={meta['pool_max']}]"
          f"（< S 则 emit 抬到 S；> pool_max 则 emit=pool_max、数量门软化 PASS+note）")
    print(f"  by_dtype : {dict(Counter(e['dtype'] for e in entries))}")
    print(f"  id_kinds : {dict(Counter(e['id_kind'] for e in entries))}")
    print(f"  special  : {sorted({e['id_kind'] for e in entries if e['id_kind'] in specials})}")
    if op in ("IsClose", "Equal"):
        eqn = sorted({str(e['attrs'].get('equal_nan')) for e in entries if 'equal_nan' in e['attrs']})
        print(f"  equal_nan values seen: {eqn}")
    print(f"  dropped_combo_classes: {len(meta['dropped_combo_classes'])} "
          f"(first3={meta['dropped_combo_classes'][:3]})")
    print(f"  coverage: {meta['coverage_strength']}")
    if ids:                                              # 确定性自检：同 cid 两次 _case_rng 首 draw 一致
        cid = ids[0]
        a = float(_case_rng(cid).random()); b = float(_case_rng(cid).random())
        print(f"  determinism(_case_rng[{cid}] first draw): {a} == {b} -> {a == b}")
    if meta.get("note_target_below_forced"):
        print(f"  note: {meta['note_target_below_forced']}")


def main(argv):
    if "--dry-run" in argv:                              # plan-only（无 torch/golden/npy），供测试与预探
        rest = [a for a in argv if a != "--dry-run"]
        spec = json.load(open(rest[0], encoding="utf-8"))
        _dry_run(spec)
        return
    spec_path, work_dir, out_path = argv[0], argv[1], argv[2]
    spec = json.load(open(spec_path, encoding="utf-8"))
    caseset = gen_cases(spec, work_dir)
    json.dump(caseset, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[gen_cases] {caseset['op']}: {len(caseset['cases'])} cases -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1:])
