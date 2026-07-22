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

shape_transform 形态扩面（2026-07-22 用户拍板的契约 C1/C2/C3，落地见下面各处 `C1:` / `C2:` / `C3:` 标记）：
  · **C1 · 输出形状交给 per-op golden.py**：`<ops_root>/<op>/golden.py` **可选**导出
    `out_shape(in_shapes, attrs) -> tuple[int,...]`。**未导出 = 输出同输入形状**（elementwise 缺省语义，
    现有 4 份 **elementwise** 样例 golden（IsClose/Sign/Equal/Neg）一律不加此函数、行为零变更）；导出了就以它为准，并**与 golden_fn 实际返回的形状对账**
    （不一致 → fail-closed，别让声明与实际悄悄打架）。caseset 的 `expected.out_shape` 记最终输出形状、
    `expected.out_shape_source` 记这形状是「声明并已核」还是「从 golden 实测」。
    ⚠ **诚实边界**：`out_shape` 是**代码不是数据**——门没法「不执行就校验」它（校验必须真跑一次 golden.py）。
    对照方案「spec 里写表达式语言」被用户否掉（im2col 那类带 floor/连乘/多维归约的公式表达不下），
    这份执行代价是**用户明确接受**的取舍，不是遗漏。
  · **C2 · attr 值放开到 `list[int]`**：原本只吃标量；`output_size`/`kernel_size` 这类**既是数组、又决定输出
    形状**的属性靠它。attr_matrix 笛卡尔展开 / combo 索引 / JSON 落盘全线支持；**case_id 仍用 `a{k}` 索引**
    表示 attr（不把数组值编进文件名——既保文件名安全，也保「同 id → 同数据字节」那条回归不变）。
  · **C3 · spec 的 in 参数可选 `rank`**（int 或 int 列表）：限制 shape 阶梯只在合法维度内取值。不写 = 不限制。
    常规网格按 rank **过滤**；过滤后没有合法常规 shape → **fail-closed**（拒绝产 0 条常规用例冒充验收）。
    §1.4 特殊场景与白名单大 shape 是**强制**项、过滤会丢掉强制覆盖 → 改用 `_fit_rank` **保 numel 调维**。

G4 · 归约/成对类算子的**生成期规模预算**（2026-07-22，落地见下面各处 `G4:` 标记）：
  · **病灶（实测）**：`_REG_SHAPES`/`_LARGE_SHAPES` 的规模假设是按 **elementwise（O(numel)）** 定的，
    对归约/成对类算子完全错配。Pdist 首跑 mock 探针 2 分钟超时（Exit 143）；本地复现：引擎把 `(1024,1024)`
    直喂成对距离 golden = 要它算 **549,755,289,600 对**、输出 **2.2 TB** —— golden 在**生成期**就跑不完。
  · **复杂度信息从哪来 = 从 shape 推**：`cost(shape, attrs) = max(最大输入元素数, 输出元素数)`，
    其中输出元素数取 **C1 已有的 `golden.py::out_shape()`**（未导出则按输入广播形状 = elementwise 缺省语义）。
    **零新契约**：`load_golden` 仍返 4 元组、spec 不加复杂度字段、4 份 elementwise 样例 golden 一字不动。
    否掉的两个候选（同样是用户列的候选，这里记下取舍理由）：
      ① **spec 显式声明 `"complexity": "quadratic"`** —— 复杂度是 shape 的函数（Pdist 是 `N(N-1)/2·D`），
         一个枚举词表达不下、写成表达式又回到 C1 已被否掉的「spec 表达式语言」；且 spec 归 acc-spec 生成，
         多一个必填字段就多一处「忘了写 → 静默按 elementwise 处理」。
      ② **给 golden 计算加超时** —— 墙钟不可靠（numpy 的 C 调用期间 Python 信号处理器不执行，SIGALRM
         打不断正在跑的 ufunc）；且它把**机器快慢写进了验收结论**（同一 spec 快机过、慢机炸），
         与本仓「确定性：固定种子、同 id 同字节」的硬约束直接冲突。
  · **超预算怎么办**（⚠ 明令**禁止**「静默跳过大 shape」——那会让覆盖悄悄缩水、报告却显示已覆盖）：
      - §1.4 特殊场景 + 白名单大 shape 是**强制**项 → **显式降规模**（逐维减半到进预算，保 rank、确定性），
        并三处留痕：caseset 的 `golden_cost.scaled_cases`、该 case 的 `expected.cost_scaled`、case 的
        tag「降规模」。报告因此能如实说「大 shape 覆盖是降规模后达成的、原目标规模没跑」。
      - 常规正交网格里超预算的 shape → 从采样池**剔除并记账**（`golden_cost.skipped_shapes`），不冒充已覆盖。
      - 减到各维皆 1 仍超预算 / 常规网格被剔空 → **fail-closed**（不硬塞算不完的用例，也不只留强制项冒充覆盖）。
  · 预算 `precision.golden_cost_budget`（int ≥1，缺省 `_GOLDEN_COST_BUDGET`=2^26）。现有 4 个 elementwise
    算子最大 cost = 2^20 ≪ 2^26 → **用例集零变更**（回归测试钉住 `scaled_cases`/`skipped_shapes` 皆空）。
  · ⚠ **诚实边界**：本模型只看「进出的元素数」，**不计算子内部每元素开销**。所以「输出小但计算大」的算子
    ——matmul（O(M·N·K) 但 I/O 只 O(M·K+K·N+M·N)）、成对求和归约（O(N²) 却输出 O(N)）——**本模型看不见**，
    它们仍会在生成期跑很久。这类算子目前只能由用户把 `precision.golden_cost_budget` 调小（降规模会照常记账）。
    别把本机制当成「大 shape 已全防住」。
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
# bf16 数值输出**逐位可达**的算子（纯搬运/纯符号类：输出恒等于某个输入元素、不做算术）。
# ⚠ 这曾是**写死的算子名白名单**——「引擎零内置算子知识」的一处反例，且任何新的纯搬运算子
# （im2col、Upsample 最近邻…）都被迫把 bf16 挂 deferred。2026-07-23 改由 **spec 显式声明**
# `precision.bf16_bitexact: true`；本表退役成**历史默认**，只为让这两个既有算子的 spec 不必改动、
# 行为零变更。新算子一律走 spec 声明，别再往这张表里加名字。
_BF16_EXACT_OPS = frozenset({"Sign", "Neg"})   # 历史默认，勿扩充——新算子用 spec.precision.bf16_bitexact


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
    """按算子名从用户侧加载 golden——`<ops_root>/<op>/golden.py`，返回
    `(golden_fn, golden_source, provenance, out_shape_fn)`。

    ⚠ **返回 4 元组**（C1 起；此前是 3 元组）。第 4 项 `out_shape_fn` 是**可选**的，未导出即 `None`。
    刻意改 arity 而非另开函数：老式 `a, b, c = load_golden(op)` 会当场 ValueError 炸掉，
    **不会**静默丢掉输出形状声明（fail-closed 优于静默降级）。

    **本加载路径不含内置 golden 值、绝不回退内置/样例**（ADR 0011 决策 1/2）：缺 golden.py → **fail-closed** 报错。
    （⚠ 仅指 elementwise 通路；catlass 通路与 `_BF16_EXACT_OPS` 仍是引擎里的算子知识。）
    golden.py 须导出 `golden_fn(inputs, attrs) -> ndarray` + `GOLDEN_SOURCE`（首 token = oracle_source 六枚举之一：
    cpu_ref/catlass_existing_ref/task_spec_expected/torch_ref/analytical_ref/external_ref——**支撑多仓多算子的各类来源**；
    elementwise 内置样例可用 backend 简写 torch/numpy）+ `GOLDEN_PROVENANCE`（来源出处）；缺任一 → fail-closed。
    **可选**导出 `out_shape(in_shapes, attrs) -> tuple[int,...]`（C1，见模块 docstring）：
    `in_shapes` 是按 spec 顺序的输入形状列表（`list[tuple[int,...]]`），`attrs` 是该 case 的属性字典。
    未导出 = 缺省语义「输出同输入形状」（elementwise）。导出了必须可调用，否则 fail-closed。
    ⚠ **门校不了它**：`out_shape` 是代码、不是数据，唯一的核法是真跑一次（`gen_cases` 每条 case 都拿它与
    `golden_fn` 的实际输出形状对账）——这份执行代价是用户明知并接受的取舍，别当成「已被静态校验」。
    样例见 `samples/golden/<op>/golden.py`。⚠ **别再照抄「样例都不导出 out_shape」**（2026-07-23 起已过时）：
    现有 7 份样例里 **4 份 elementwise 不导出**（IsClose/Sign/Equal/Neg，走缺省同形语义）、
    **3 份形变类导出**（Im2col / UpsampleNearest3d / UpsampleNearestExact2d）——后者是 C1 的正例，可照抄。

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
            f"（可照 ${{OPRUNWAY_PLUGIN_ROOT}}/samples/golden/<op>/golden.py 的只读样例；"
            f"samples/ 随插件分发、2026-07-22 由仓根迁入插件内）；或设 OPRUNWAY_OPS_DIR / OPRUNWAY_WORK_DIR。")
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
    # C1：可选 out_shape。导出了但不可调用 → fail-closed（别把一个字符串/数组当函数、到 case 循环里才炸）。
    out_shape_fn = getattr(mod, "out_shape", None)
    if out_shape_fn is not None and not callable(out_shape_fn):
        raise ValueError(f"golden.py 的 out_shape 须可调用 `def out_shape(in_shapes, attrs)`，"
                         f"得 {type(out_shape_fn).__name__}: {gpath}")
    return mod.golden_fn, mod.GOLDEN_SOURCE, mod.GOLDEN_PROVENANCE, out_shape_fn


def _norm_out_shape(raw, where):
    """把 `out_shape()` 的返回值规范化成 `tuple[int,...]`；坏返回一律 fail-closed（不猜、不修正）：
    非序列 / 含负数 / 含 bool / 含非整数 → 报错。
    允许 `numpy` 整数（用户可能直接回 `np.int64`），转成 python int 落盘。
    允许 0 维（`()`，标量输出）与含 0 的维度（空 Tensor 输出）。"""
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (tuple, list)):
        raise ValueError(f"{where}: out_shape() 须返回 int 序列（tuple/list），得 {raw!r}")
    dims = []
    for d in raw:
        if isinstance(d, bool) or not isinstance(d, (int, np.integer)) or int(d) < 0:
            raise ValueError(f"{where}: out_shape() 的维度须为非负整数，得 {raw!r}")
        dims.append(int(d))
    return tuple(dims)


def _call_out_shape(out_shape_fn, in_shapes, attrs, where):
    """调用户的 `out_shape(in_shapes, attrs)` 并规范化返回值；用户代码异常收敛成带上下文的 ValueError。

    `attrs` 传**副本**（`_copy_attrs`，list 值另拷）——out_shape 是用户代码，就地改一下数组 attr 就会串到
    别的 case。`where` 是上下文标签：case 循环里传 case_id，plan 期（G4 规模预算）传 `dtype·shape·kind`。"""
    ins = [tuple(int(d) for d in s) for s in in_shapes]
    try:
        raw = out_shape_fn(ins, _copy_attrs(attrs))
    except Exception as ex:                              # noqa: BLE001 —— 用户代码异常统一收敛成 ValueError
        raise ValueError(f"{where}: golden.py 的 out_shape({ins}, …) 执行失败: {ex}") from ex
    return _norm_out_shape(raw, where)


def _declared_out_shape(out_shape_fn, inputs, attrs, cid):
    """C1：调 golden.py 的 `out_shape(in_shapes, attrs)` 取**声明**输出形状 → 规范化成 `tuple[int,...]`。"""
    return _call_out_shape(out_shape_fn, [np.asarray(x).shape for x in inputs], attrs, cid)


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


def check_spec_capability(in_params):
    """引擎**能力边界**的 spec 级预检——`gen_cases()` 与 `_dry_run()` 共用，故 CP-B 契约自检就能拦住。

    为什么必须有：`_build_inputs` 的常规 `varied` / `pair*` 路径末尾写死 `return [x0, x1]`（二元构造），
    而 `empty` 与特殊值路径按 `arity` 产满——**arity≥3 时多出来的输入被无声丢掉，两边行为还不一致**。
    与其静默截断，不如明说不支持（本仓纪律：**fail-closed 优于静默降级**）。
    支持多输入算子须先一般化 pair 构造，见 `doc/oprunway-todo.md` 的 U7b。"""
    arity = len(in_params)
    if arity > 2:
        raise ValueError(
            f"gen_cases 暂不支持 {arity} 元输入算子（in 参数：{[p['name'] for p in in_params]}）——"
            f"常规输入构造是二元的，多出来的输入会被静默丢弃。请先一般化 _build_inputs（TODO U7b）。")
    if not in_params:
        raise ValueError("spec 无 io=='in' 参数 → 产不出任何用例（0 用例不得冒充验收），fail-closed。")
    # dtype 集的三道校验也放这里，好让 **`_dry_run`（= CP-B 契约自检）** 也能拦住，
    # 而不是只在正式生成期才炸——CP-B 过了却在 CP-D 才发现，正是本轮要消灭的「漏到下游」。
    self_param = next((p for p in in_params if p["name"] == "self"), in_params[0])
    dtypes = self_param.get("dtype") or []
    if not dtypes:
        # 空 dtype 集 → 一条用例都产不出。**0 用例冒充验收**是本仓明令禁止的
        # （跑 0 条也能显示「无失败」），与 case_target=0 同一判据。（预先存在的洞，2026-07-22 补。）
        raise ValueError(
            f"spec 的输入参数 {self_param['name']!r} dtype 集为空 → 产不出任何用例。"
            f"0 用例不得冒充验收（同 case_target=0 的判据），fail-closed。")
    if len(dtypes) != len(set(dtypes)):               # finding #13：dtype 集含重复 → plan entry 撞车
        dup = sorted(d for d in set(dtypes) if dtypes.count(d) > 1)
        raise ValueError(f"spec dtype 集含重复项 {dup}（会致 case_id 碰撞/伪造覆盖，fail-fast）")
    for dtn in dtypes:                                # dtype 白名单（fail-fast，不静默）
        if dtn != _BF16 and dtn not in _NATIVE:
            raise ValueError(f"unsupported dtype {dtn!r}（gen_cases 支持 {sorted(_NATIVE)} + bfloat16）")


def _build_inputs(rng, in_params, shp, dtn, attrs, data_kind):
    """造该 case 的**逻辑**输入数组列表（compute dtype；bf16=fp32-on-grid）。物理化在保存步单独做。
    data_kind 形如 base 或 base:regime（regime∈{uniform,normal}，仅 varied/pair 系用）；
    特殊 base：empty(§1.4 空)/inf/ninf/nan(§1.4 特殊值)。"""
    arity = len(in_params)
    base = data_kind.split(":")[0]
    regime = data_kind.split(":")[1] if ":" in data_kind else "uniform"
    check_spec_capability(in_params)                     # 兜底：正式路径也再校一次（dry-run 已前置校过）
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
# 高 rank 补充阶梯——**只在 spec 的 rank 约束点名要它时才进池**（`_shape_ladder`），
# 无 rank 约束的算子（全部 elementwise）看不到它、用例集**一字不变**。
# 为什么需要：`_MAX_RANK` 本是 8，但主阶梯只到 4 维，于是 `rank:[5]` 的算子
# （UpsampleNearest3d 的 (N,C,D,H,W)）过滤后一条常规 shape 都不剩 → dry-run fail-closed、整个算子跑不了。
# ⚠ 只补到 5 维：**没有实际算子要求 6~8 维**，凭空铺满只会让笛卡尔积与 golden 开销白涨。
# ⚠ 也**不能**直接并进 `_REG_SHAPES`：那会改变既有 elementwise 算子的用例集 = 悄悄改变已验收过的东西。
_EXT_RANK_SHAPES = [(2, 3, 2, 4, 4), (1, 2, 3, 3, 3)]  # 5 维：一大一小
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


# ============================ G4 · golden 生成期规模预算（归约/成对类算子）====================
# 见模块 docstring「G4」：cost = max(最大输入元素数, 输出元素数)，输出元素数取 C1 的 out_shape()。
_GOLDEN_COST_BUDGET = 2 ** 26        # 缺省预算。现有 elementwise 最大 cost=2^20 → 留 64× 余量、零误伤
_COST_SHRINK_MAX_STEPS = 256         # 逐维减半步数上限（8 维 × log2(2^20) 也就 ~160 步，纯保险）
_COST_LEDGER_CAP = 50                # 账本条目上限（同 _dropped_classes，防爆；总数另记 *_classes）
_COST_MODEL = (
    "shape_derived(推断)：cost = max(最大输入元素数, 输出元素数)；输出元素数取 golden.py 的 out_shape() 声明、"
    "未导出则按输入广播形状。⚠ 未计算子内部每元素开销——「输出小但计算大」的算子"
    "（matmul / O(N²) 却输出 O(N) 的归约）本模型看不见，别当作大 shape 已全防住")
_COST_MODEL_UNCHECKED = "未核（golden.py 未加载 → 本次未行使规模预算；正式生成 gen_cases 必核）"


def _empty_cost_ledger():
    """未行使预算时的账本。**每次造新的**（含新的空 list）——共享的模块级常量一旦被谁 append 就会全局串味。"""
    return {"budget": None, "model": _COST_MODEL_UNCHECKED,
            "scaled_cases": [], "skipped_shapes": [], "skipped_shape_classes": 0}


def _cost_budget(spec):
    """G4 预算：`spec.precision.golden_cost_budget`（int ≥1）覆盖缺省 `_GOLDEN_COST_BUDGET`。
    0/负/非整 → fail-fast（预算 0 等于把所有 shape 判超预算，是另一种「用例集清零」）。"""
    raw = (spec.get("precision") or {}).get("golden_cost_budget", _GOLDEN_COST_BUDGET)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(f"precision.golden_cost_budget 须为 ≥1 的整数（golden 生成期规模预算，"
                         f"缺省 {_GOLDEN_COST_BUDGET}），得 {raw!r}")
    return int(raw)


def _entry_in_shapes(shp, arity):
    """该 plan entry 下各输入的形状：常规路径所有输入同形；`broadcast` 哨兵是二元 (4,1)/(1,5)。
    与 `_build_inputs` 的构造口径一致——cost 估的必须是**真会喂给 golden 的那组形状**。"""
    if shp == "broadcast":
        return [(4, 1), (1, 5)]
    t = tuple(int(d) for d in shp)
    return [t for _ in range(max(1, arity))]


def _make_cost_fn(in_params, out_shape_fn):
    """G4：造 `cost(shape, attrs, where) -> int` —— golden 在**生成期**要吞吐的元素数量级。

    唯一信息源是 shape：`max(最大输入元素数, 输出元素数)`；输出元素数走 C1 的 `out_shape()`（未导出 →
    输入广播形状 = elementwise 缺省语义）。**不新增契约**——成对/归约类算子本来就必须导出 `out_shape`
    （C1：真改形状却不导出 = fail-closed），所以这份信息对它们是**已经在手**的。
    诚实边界见模块 docstring G4 最后一条：内部每元素开销不在模型里。"""
    arity = max(1, len(in_params))

    def cost(shp, attrs, where):
        ins = _entry_in_shapes(shp, arity)
        in_n = max(_numel(s) for s in ins)
        if out_shape_fn is None:
            out_n = _numel(tuple(np.broadcast_shapes(*ins)))
        else:
            out_n = _numel(_call_out_shape(out_shape_fn, ins, attrs, where))
        return max(in_n, out_n)
    return cost


def _shrink_to_budget(shape, attrs, cost_fn, budget, where):
    """把**强制**用例的 shape 逐维减半到进预算，返回 `(新shape, 新cost)`。确定性：每步砍当前最大维、
    并列取最左；**保 rank**（不删维，免与 C3 的 rank 约束打架）。

    为什么强制项只能降规模、不能像常规网格那样剔掉：§1.4 特殊场景与白名单大 shape 是「必覆盖」项，
    剔掉 = 覆盖悄悄缩水。降下来的规模会照常进 case_id / caseset / 覆盖账本（可见、可审计，**不是**静默降级）。
    减到各维皆 1 仍超预算 → fail-closed（不硬塞一条算不完的用例）。"""
    if shape == "broadcast":                             # 哨兵形状固定微小，理论上到不了这里
        raise ValueError(f"{where}: broadcast 哨兵形状的 golden 开销超预算 {budget}，无维可降 → fail-closed")
    cur = [int(d) for d in shape]
    if not cur:
        raise ValueError(f"{where}: 0 维 shape 的 golden 开销超预算 {budget}，无维可降 → fail-closed")
    for _ in range(_COST_SHRINK_MAX_STEPS):
        i = max(range(len(cur)), key=lambda k: (cur[k], -k))
        if cur[i] <= 1:                                  # 各维皆 1，降无可降
            break
        cur[i] //= 2
        c = cost_fn(tuple(cur), attrs, where)
        if c <= budget:
            return tuple(cur), c
    raise ValueError(
        f"{where}: 输入形状 {tuple(int(d) for d in shape)} 的 golden 生成期开销超预算 {budget}，"
        f"且逐维减半到 {tuple(cur)}（cost={cost_fn(tuple(cur), attrs, where)}）仍超预算——"
        f"本条是**强制**覆盖项、不能丢，故 fail-closed。"
        f"请调 spec 的 precision.golden_cost_budget，或核 golden.py 的 out_shape() 是否合理")


def _apply_cost_budget(forced, grid, cost_fn, budget):
    """G4：按生成期规模预算处理 plan entries。返回 `(保留的 grid, 覆盖账本)`；`forced` **就地**降规模。

    强制项 → 降规模 + 三处留痕（账本 / `entry["cost_scaled"]`（后续写进 case 的 expected）/ tag「降规模」）。
    常规网格项 → 超预算就剔出采样池，并按 (dtype, shape) 归类记账（**不**冒充已覆盖）。
    网格被剔空 → fail-closed（只剩强制项 = 「用例数虚高但没有一条常规覆盖」的假验收，同 `_shape_ladder`）。"""
    scaled, skipped, kept, seen_skip = [], [], [], set()
    for e in forced:
        where = f'{e["dtype"]}·{_shape_tag(e["shape"])}·{e["id_kind"]}'
        c0 = cost_fn(e["shape"], e["attrs"], where)
        if c0 <= budget:
            continue
        new_shp, c1 = _shrink_to_budget(e["shape"], e["attrs"], cost_fn, budget, where)
        rec = {"case_origin": e["case_origin"], "id_kind": e["id_kind"], "dtype": e["dtype"],
               "requested_shape": list(e["shape"]), "requested_cost": int(c0),
               "emitted_shape": list(new_shp), "emitted_cost": int(c1),
               "emitted_numel": _numel(new_shp), "budget": int(budget),
               "reason": "golden 生成期规模超预算 → 强制覆盖项**显式降规模**（非静默跳过）；"
                         "该 case 只覆盖了降下来的这个规模，原目标规模**未跑**"}
        if "性能" in e["dims"]:
            rec["perf_note"] = (f"该 case 带「性能」维度：降规模后 numel={_numel(new_shp)}，性能结论只对这个"
                                f"规模成立；numel 若落到下游 trivial 阈值以下，性能判定会退化成 trivial-met"
                                f"（免测达标）——别读成「原规模已测且达标」")
        e["shape"] = new_shp
        e["tags"] = list(e["tags"]) + ["降规模"]
        e["cost_scaled"] = rec
        scaled.append(rec)
    for e in grid:
        where = f'{e["dtype"]}·{_shape_tag(e["shape"])}·{e["id_kind"]}'
        c0 = cost_fn(e["shape"], e["attrs"], where)
        if c0 <= budget:
            kept.append(e)
            continue
        k = (e["dtype"], _shape_tag(e["shape"]))
        if k not in seen_skip:
            seen_skip.add(k)
            skipped.append({"dtype": e["dtype"], "shape": _shape_tag(e["shape"]),
                            "cost": int(c0), "budget": int(budget),
                            "reason": "常规正交网格的该 shape 超 golden 生成期规模预算 → 剔出采样池并记账"
                                      "（**不**计入已覆盖；强制项走降规模、不走这条）"})
    if grid and not kept:
        raise ValueError(
            f"常规正交网格的**全部** shape 都超 golden 生成期规模预算 {budget} → 只剩强制项，"
            f"那是「用例数虚高但没有一条常规覆盖」的假验收，fail-closed。"
            f"请调 spec 的 precision.golden_cost_budget，或核 golden.py 的 out_shape() 是否合理")
    return kept, {"budget": int(budget), "model": _COST_MODEL, "scaled_cases": scaled,
                  "skipped_shapes": skipped[:_COST_LEDGER_CAP], "skipped_shape_classes": len(skipped)}


# ================================================= C2 · attr 值类型（含 list[int]）
def _check_attr_value(v, where):
    """C2 attr 值类型闸：标量 `bool/int/float/str` **或** `list[int]`。

    放开 list 是为 `output_size`/`kernel_size` 这类**既是数组、又决定输出形状**的属性。
    只放开到 `list[int]`、不放开嵌套/浮点数组：多一层就多一种「悄悄改变语义还对上了 golden」的假覆盖面，
    真需要时再单独放（本仓纪律：fail-closed 优于静默降级）。⚠ list 里的 `bool` 也拒——`[True]` 与 `[1]`
    在 python 里等值，放行就等于让两种写法在 combo 去重时互相吞掉。

    ⚠ **空数组 `[]` 也拒**，且刻意在这里拒（而不是等到部署时）：`repo_adapter._manifest_attr_token` 把
    `list[int]` 编成逗号连接的**单个** token，空数组会编成空串、把后面所有 token 挤错位——它那边已 fail-closed。
    但 mock 通路不造 manifest，只在那边拦就成了「本机跑得过、上真机才炸」。宁可在造用例时就停。"""
    if isinstance(v, list):
        if not v:
            raise ValueError(f"{where}=[] 是空数组（manifest 行是空格分隔的扁平 token，空数组编成空串会让"
                             f"后续 token 全错位；repo_adapter 侧同样拒）——请给非空 list[int]")
        for i, d in enumerate(v):
            if isinstance(d, bool) or not isinstance(d, int):
                raise ValueError(f"{where}[{i}]={d!r} 非 int（attr 的数组值只支持 list[int]，"
                                 f"拒嵌套/浮点/bool 元素）")
        return
    if not isinstance(v, (bool, int, float, str)):
        raise ValueError(f"{where}={v!r} 非法（attr 值须为 bool/int/float/str 标量，或 list[int]）")


def _attr_hashable(v):
    """attr 值 → 可哈希键（`list` → `tuple`；标量原样）。仅供 combo 索引 `_akey` 用，不改落盘的值。"""
    return tuple(v) if isinstance(v, list) else v


def _copy_attrs(a):
    """attrs 拷一份、**list 值另拷**：不让同一个 list 对象被多条 case 共享。
    `golden_fn` / `out_shape` 是用户代码，就地改一下 attr 的数组就会串到别的 case（数据被污染还查不出来）。"""
    return {k: (list(v) if isinstance(v, list) else v) for k, v in a.items()}


def _attr_value_sets(spec, attrs_default):
    """§1.3：每 attr 的取值集——布尔→[F,T]、枚举→全值、标量→等价类代表（默认值）。
    有 attr_matrix 时用它给的取值集（每 key 的并集，保序）；否则据 attr dtype/默认派生。
    返回 [(name, [values])]，供笛卡尔展开（attr 作真正交轴，评审 #12）。"""
    attr_params = [p for p in spec["params"] if p["io"] == "attr"]
    matrix = spec.get("attr_matrix")
    # finding #12（§1 重写勿丢）：attr_matrix 每项须为 dict、key ⊆ spec io=='attr' 名集、值受类型闸约束——
    # 防伪造 attr key（如 {foo:12345}）冒充覆盖 / 非法值类型。fail-fast，不静默忽略未知 key。
    # C2：值类型闸从「只许标量」放开到「标量 或 list[int]」，判定统一走 _check_attr_value。
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
                _check_attr_value(v, f"attr_matrix[{k_idx}].{k}")
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
        # C2 补闸：**`default` 值也要过类型闸**，不能只校 attr_matrix。
        # 否则 `"default": []` / `[1.5, 2.0]` 这类会一路 gen_cases + mock 全绿、
        # 直到真机造 manifest 才炸——正是本文件声称已堵住的那条「本机过、真机炸」。
        # ⚠ 只对 list 值行使：标量与 `None`（未定哨兵）的既有语义**一字不动**，避免误伤现存 spec。
        for v in vals:
            if isinstance(v, list):
                _check_attr_value(v, f"params[attr={name}].default")
        out.append((name, vals))
    return out


def _attr_combos(attr_sets, attrs_default):
    """attr 取值集笛卡尔展开为 attr 字典列表（保序、确定）。空 attr → 单个默认字典。
    C2：每一层都过 `_copy_attrs`，list[int] 值每条 combo 各持一份（不共享同一个 list 对象）。"""
    combos = [_copy_attrs(attrs_default)]
    for name, vals in attr_sets:
        combos = [_copy_attrs({**c, name: v}) for c in combos for v in vals]
    return combos


# ================================================= C3 · input_rank 约束 =========
_MAX_RANK = 8                                        # §1.2 阶梯设定 dims 1~8，rank 声明不得越界


def _allowed_ranks(in_params):
    """C3：从 spec 的 in 参数读可选 `rank`（int 或 int 列表）→ 合法输入维度集（frozenset）；
    **无人声明 → None = 不限制**（现行为，零变更）。

    多个 in 参数各自声明时取**交集**：常规构造路径下所有输入同形，只有交集里的维度对每个输入都合法。
    交集为空 → fail-closed（与其挑一个「大概能跑」的维度，不如停下让人改 spec）。"""
    sets = []
    for p in in_params:
        if "rank" not in p or p.get("rank") is None:
            continue
        raw = p["rank"]
        vals = raw if isinstance(raw, list) else [raw]
        if isinstance(raw, list) and not raw:
            raise ValueError(f"in 参数 {p.get('name')!r} 的 rank 是空列表（无任何合法维度，"
                             f"等于把用例集清零）——不写 rank 才表示不限制")
        got = set()
        for r in vals:
            if isinstance(r, bool) or not isinstance(r, int) or not (1 <= r <= _MAX_RANK):
                raise ValueError(f"in 参数 {p.get('name')!r} 的 rank={r!r} 非法"
                                 f"（须为 1..{_MAX_RANK} 的整数，或这种整数的列表）")
            got.add(int(r))
        sets.append(got)
    if not sets:
        return None
    inter = set.intersection(*sets)
    if not inter:
        raise ValueError(f"各 in 参数声明的 rank 交集为空（{[sorted(s) for s in sets]}）——"
                         f"常规构造路径下所有输入同形，没有对每个输入都合法的维度，fail-closed")
    return frozenset(inter)


def _rank_ok(shape, ranks):
    return ranks is None or len(shape) in ranks


def _fit_rank(shape, ranks):
    """把**强制**用例的基准 shape 调到合法 rank（`ranks=None` 或本来就合法 → 原样返回，零行为变更）。

    为什么强制项不能像常规网格那样过滤掉：§1.4 特殊场景（空/标量/边界/inf-nan）与白名单大 shape 是
    「必覆盖」项，过滤=直接丢掉强制覆盖。故按确定性规则改维、**保 numel**（numel 保住了，「空 / 标量 /
    大」这些特殊场景的性质也就保住了）：
      · 目标 rank r = 合法集中离原 rank 最近的（并列取小）；
      · r > 原 rank → 左补 1（如 (1024,1024) @rank4 → (1,1,1024,1024)）；
      · r < 原 rank → 前 (原rank-r+1) 维连乘折进首维（如 (1024,1024) @rank1 → (1048576,)）。
    调完的 shape 会照常进 case_id 与 caseset（可见、可审计，不是静默降级）。"""
    if ranks is None:
        return shape
    shp = tuple(int(d) for d in shape)
    r0 = len(shp)
    if r0 in ranks:
        return shape
    r = min(sorted(ranks), key=lambda x: (abs(x - r0), x))
    if r > r0:
        return (1,) * (r - r0) + shp
    head = 1
    for d in shp[: r0 - r + 1]:
        head *= d
    return (head,) + shp[r0 - r + 1:]


def _shape_ladder(ranks):
    """按 rank 约束过滤 §1.2 shape 阶梯，返回 (reg, large)。

    ⚠ 常规阶梯被过滤空 → **fail-closed**：常规网格是 dtype×shape×值域×attr 正交采样的唯一来源，
    它空了就只剩强制项——那是「用例数虚高但没有一条常规覆盖」的假验收。宁可停下让人补阶梯/放宽 rank。"""
    # 高 rank 补充阶梯**只在被点名时并入**（`ranks` 非空且含主阶梯覆盖不到的 rank）——
    # 无 rank 约束的算子看不到它，既有 elementwise 用例集因此一字不变。
    pool = list(_REG_SHAPES)
    if ranks and (set(ranks) - {len(s) for s in _REG_SHAPES}):
        pool += _EXT_RANK_SHAPES
    reg = [s for s in pool if _numel(s) <= _MAX_NUMEL and _rank_ok(s, ranks)]
    large = [s for s in _LARGE_SHAPES if _numel(s) <= _MAX_NUMEL and _rank_ok(s, ranks)]
    if not reg:
        raise ValueError(
            f"input_rank 约束 {sorted(ranks)} 过滤后无合法常规 shape（阶梯覆盖的 rank 为 "
            f"{sorted({len(s) for s in pool})}）——拒绝只产强制用例冒充覆盖。"
            f"请放宽 spec 中 in 参数的 rank，或给 _REG_SHAPES / _EXT_RANK_SHAPES 补该 rank 的阶梯值")
    return reg, large


def _dtype_shapes(dtn, is_key, reg, large):
    """该 dtype 的常规 shape 集：key dtype 用全阶梯 + 大 shape；非 key 只取前 N 个主流 shape（配额）。
    reg/large 由 `_shape_ladder(ranks)` 供（已按 numel 上限 + C3 rank 约束过滤）。"""
    if is_key:
        return list(reg) + list(large)
    return list(reg[:_OTHER_DTYPE_QUOTA])               # 非重点 dtype：主流少量


def _empty_axis(spec):
    """空 Tensor 用例把 0 放在**哪一轴**（`spec.empty_axis`，缺省 None = 现行为）。

    ⚠ 为什么需要：`_fit_rank((0,), ranks)` 是左补 1，**0 恒落在最后一维**（rank=[3,4] → `(1,1,0)`）。
    而很多算子的空 Tensor 只在**某一特定轴**为 0 时合法——im2col 只允许「4 维且 N==0」
    （`aclnn_im2col.cpp` CheckInputDims 只放过 dim0），`(1,1,0)` 是 W=0、非法。
    结果是这类算子只能整个关掉空 Tensor 用例（`allow_empty_tensor: false`）=
    **本该测的那一种合法空形态也一起没了**。声明轴号后就能精确造出 `(0,C,H,W)`。
    取值：非负 int（0=首轴/batch）。⚠ 只收真 int（`True` 是 bool 子类、会被 isinstance 放过，显式排除）。"""
    v = spec.get("empty_axis")
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError(f"spec.empty_axis 须为非负整数（轴号，0=首轴），得 {v!r}")
    return v


def _bf16_bitexact(spec, op):
    """该算子的 bf16 数值输出是否**逐位可达**（纯搬运/纯符号类）。

    来源优先级：spec 显式声明 `precision.bf16_bitexact` > `_BF16_EXACT_OPS` 历史默认。
    ⚠ 只接受真布尔——`"false"`/`0` 会被真值性判断误读，fail-closed 拒收（同 allow_empty_tensor）。
    ⚠ 这不是「放松阈值」的旋钮：声明为真等于断言「该算子输出恒等于某个输入元素、不做算术」，
    声明错了会让**本该用 lossy 阈值的算子被按逐位相等判**，直接产假 fail 或假 pass。"""
    v = (spec.get("precision") or {}).get("bf16_bitexact")
    if v is None:
        return op in _BF16_EXACT_OPS                  # 历史默认（Sign/Neg），行为零变更
    if not isinstance(v, bool):
        raise ValueError(
            f"spec.precision.bf16_bitexact 须为布尔真值，得 {v!r}（{type(v).__name__}）——"
            f"字符串 \"false\" / 数字 0 会被真值性判断误读，fail-closed 拒收。")
    return v


def _allow_empty_tensor(spec):
    """spec 是否允许空 Tensor 用例（缺省 True = 现行为）。

    只接受真正的布尔——写成 `"false"` / `0` 这类会被真值性判断悄悄误读成「允许」，
    正是本仓栽过的那类 fail-open（批 1 的 `authorization_verified` 同款），故 fail-closed 拒非 bool。"""
    v = spec.get("allow_empty_tensor", True)
    if not isinstance(v, bool):
        raise ValueError(
            f"spec.allow_empty_tensor 须为布尔真值，得 {v!r}（{type(v).__name__}）——"
            f"字符串 \"false\" / 数字 0 会被真值性判断误读成「允许」，fail-closed 拒收。")
    return v


def _empty_shape(ranks, axis, accepts=None):
    """按声明的轴造空 shape：该轴 0、其余 1。`ranks=None` → 退回 1 维 `(0,)`（现行为）。

    ⚠ **轴号定不了 rank**：im2col 的 rank 是 `[3,4]`，而合法空形态只有「**4 维**且 N==0」——
    取最小 rank 会造出 3 维的 `(0,1,1)`，算子当场拒。
    所以按合法 rank **从小到大逐个试**，第一个被算子接受的就是它；一个都不接受 → fail-closed。
    **判据交给算子自己的 `out_shape()`**（`accepts`）——「哪个 rank 的空形态合法」本就是算子知识，
    而 `out_shape` 正是算子知识的所在地（C1 的前提）。引擎不猜。
    `accepts=None`（算子没导出 out_shape）→ 退回取最小合法 rank，无从询问、也无从校验。"""
    if ranks is None:
        return (0,)
    cands = [tuple(0 if i == axis else 1 for i in range(r)) for r in sorted(ranks) if axis < r]
    if not cands:
        raise ValueError(
            f"spec.empty_axis={axis} 对该算子的所有合法 rank {sorted(ranks)} 都越界（轴号需 < rank）。"
            f"fail-closed——不静默退回「0 放最后一维」，那正是本字段要修的。")
    if accepts is None:
        return cands[0]
    for shp in cands:
        if accepts(shp):
            return shp
    raise ValueError(
        f"spec 声明了 allow_empty_tensor + empty_axis={axis}，但算子的 out_shape() **拒绝了所有候选空形态** "
        f"{cands}。要么 empty_axis 写错了轴，要么该算子根本不支持空 Tensor（那就设 allow_empty_tensor: false）。"
        f"fail-closed——绝不为它挑一个算子不认的形状硬塞。")


def _special_entries(op, dtn, arity, is_float, rep_attrs, ranks=None, allow_empty=True,
                     empty_axis=None, empty_accepts=None):
    """§1.4 特殊场景（不与常规正交、强制纳入）：空(功能only)/标量[1]/边界下(全1)/边界上(大)/inf/-inf/nan。
    每项 (dims, shape, data_kind, id_kind)。整型 dtype 跳过 inf/nan（无此值）。
    C3：每个基准 shape 过 `_fit_rank`——ranks=None 时恒等（现行为），有约束时保 numel 调到合法维度。

    `allow_empty=False`（spec 声明 `allow_empty_tensor: false`）时**不产空 Tensor 用例**。
    ⚠ 为什么需要这个开关（2026-07-23 · 三个真算子实测撞上）：opbase §1.4 把「空 Tensor」当成
    普适特殊场景，但**很多算子任务书白纸黑字写「不支持空Tensor」**（Upsample 系、im2col 的
    3 维形态…）。强塞一条它们语义上不存在的用例，只有两个出口——要么 golden **为非法输入编造输出**
    （= 替算子发明它并不支持的语义，本仓最不能接受的那种「看起来对」），要么整条链 fail-closed 卡死。
    实测：Im2col / UpsampleNearestExact2d / UpsampleNearest3d **三个真算子全撞这一堵墙**。
    ⚠ 缺省 True = **现行为不变**（4 个 elementwise 样例一字不动）；关掉是算子的显式声明，不是默认放松。"""
    E = []
    # 空 Tensor：某维=0 → 只挂「功能」（无精度/无 kernel profile；validator numel=0→na、adapter 优雅跳过）
    if allow_empty:
        # 空 shape：声明了 empty_axis 就按轴精确造（如 (0,1,1,1)）；没声明走老路 _fit_rank（0 落最后一维）。
        _es = (_empty_shape(ranks, empty_axis, empty_accepts) if empty_axis is not None
               else _fit_rank((0,), ranks))
        E.append((["功能"], _es, "empty", "empty"))
    # 标量 Tensor [1]（numel=1，退化 perf → 下游 trivial-met）
    E.append((["功能", "精度", "性能"], _fit_rank((1,), ranks), "varied", "scalar"))
    # 边界：下=各维均 1；上=大 shape 某维取大
    E.append((["功能", "精度", "性能"], _fit_rank((1, 1, 1), ranks), "varied", "bndlo"))
    E.append((["功能", "精度", "性能"], _fit_rank(_LARGE_SHAPES[0], ranks), "varied", "bndhi"))
    # INF/-INF/NAN 遍历（仅浮点；每种值一条，shape 用中等 (16,)）——**带「性能」**（v2：非空皆带性能/同输入；
    # numel=16<阈值 → perf_compare 判 trivial-met 免测，不假 fail）。
    if is_float:
        for val_kind in ("inf", "ninf", "nan"):
            E.append((["功能", "精度", "性能"], _fit_rank((16,), ranks), val_kind, val_kind))
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
def _make_empty_accepts(in_params, out_shape_fn, attrs):
    """造 `accepts(shape) -> bool`：拿候选空 shape 问算子的 `out_shape()` 认不认。

    只吞 ValueError（算子自己的 fail-closed 报错就是「不认」的表达）；别的异常照抛——
    那是 golden 本身写坏了，不该被当成「这个形状不合法」悄悄跳过。
    `out_shape_fn=None`（算子没导出）→ 返回 None，调用方退回取最小合法 rank。"""
    if out_shape_fn is None:
        return None
    arity = max(1, len(in_params))

    def accepts(shp):
        try:
            out_shape_fn(_entry_in_shapes(shp, arity), _copy_attrs(attrs))
            return True
        except ValueError:
            return False
    return accepts


def _plan(spec, in_params, dtypes, attrs_default, op, case_target, cost_fn=None, empty_accepts=None):
    """§1 覆盖-预算计划。返回 (entries, meta)。选择端无 rng（结构序 + 原始索引 tie-break）。
    ① §1.4 特殊场景（每 dtype，强制）→ ② 白名单必覆盖（key dtype × 每 attr × 大 shape，强制，防关键联合被采样丢）
    → ③ 常规正交网格（dtype×shape×值域×attr）作 1-wise 采样源，填到 budget=max(case_target, |forced|)。
    format 轴：elementwise 仅 ND（op_def/example 佐证）→ 退化为单值，不进网格。
    C3：先解出 in 参数的 rank 约束（无声明→None=不限制），常规阶梯按它过滤（空则 fail-closed）、
    强制项按它保 numel 调维。
    G4：`cost_fn`（`_make_cost_fn` 造，据 C1 的 out_shape 推）非空时行使 **golden 生成期规模预算**——
    强制项降规模、网格项剔除，全部记进 `meta["golden_cost"]`。`cost_fn=None`（如 dry-run 加载不到 golden.py）
    → **完全不行使**，行为与 G4 之前逐字节一致，且账本里 model 标「未核」而非谎称已核。"""
    arity = len(in_params)
    ranks = _allowed_ranks(in_params)                    # C3：None=不限制（现行为）
    reg_shapes, large_shapes = _shape_ladder(ranks)      # 过滤后无合法常规 shape → 已 fail-closed
    big_shape = _fit_rank(_LARGE_SHAPES[0], ranks)       # 白名单/bndhi 的大 shape（ranks=None 时恒等）
    attr_sets = _attr_value_sets(spec, attrs_default)
    attr_combos = _attr_combos(attr_sets, attrs_default)

    def _akey(a):                                        # C2：list[int] 值转 tuple 才可哈希
        return tuple((k, _attr_hashable(a.get(k))) for k in attrs_default)
    combo_idx = {_akey(a): i for i, a in enumerate(attr_combos)}

    def mk(dims, shp, dtn, data_kind, id_kind, attrs, origin, rule, tags):
        return {"dims": list(dims), "shape": shp, "dtype": dtn, "tags": list(tags),
                "data_kind": data_kind, "id_kind": id_kind, "attrs": _copy_attrs(attrs),
                "attr_idx": combo_idx.get(_akey(attrs)), "case_origin": origin, "rule_ref": rule}

    forced, grid = [], []
    # ① §1.4 特殊场景（每 dtype 强制；id_kind 独立命名空间，评审 #8）
    for dtn in dtypes:
        is_float = not precision_policy.is_integer_dtype(dtn)
        for dims, shp, dk, ik in _special_entries(op, dtn, arity, is_float, attr_combos[0], ranks,
                                                  allow_empty=_allow_empty_tensor(spec),
                                                  empty_axis=_empty_axis(spec),
                                                  empty_accepts=empty_accepts):
            forced.append(mk(dims, shp, dtn, dk, ik, attr_combos[0],
                             f"special:{ik}", f"opbase §1.4 {ik}", ["特殊"]))
    # ② 白名单必覆盖（key dtype × 每 attr 取值 × 大 shape）——保证关键联合不被 1-wise 采样丢（评审 #6）
    for dtn in dtypes:
        if dtn not in KEY_DTYPES:
            continue
        dk = _regular_data_kind(dtn, attrs_default, arity)
        for attrs in attr_combos:
            ai = combo_idx[_akey(attrs)]
            forced.append(mk(["功能", "精度", "性能"], big_shape, dtn, f"{dk}:uniform",
                             f"wl{ai}", attrs, f"whitelist:{dtn}:a{ai}",
                             "opbase §1.1 必覆盖组合(key×attr×大shape)", ["白名单"]))
    # ③ 常规正交网格（1-wise 采样源）：dtype × shape × 值域 × attr（regime 编进 id_kind 保 case_id 唯一）
    for dtn in dtypes:
        is_key = dtn in KEY_DTYPES
        dk = _regular_data_kind(dtn, attrs_default, arity)
        for shp in _dtype_shapes(dtn, is_key, reg_shapes, large_shapes):
            for regime in _VALUE_REGIMES:
                for attrs in attr_combos:
                    ai = combo_idx[_akey(attrs)]
                    grid.append(mk(["功能", "精度", "性能"], shp, dtn, f"{dk}:{regime}",
                                   f"grid{regime[0]}", attrs,
                                   f"grid:{dtn}:{_shape_tag(shp)}:{regime}:a{ai}",
                                   "opbase §1.1/§1.2 正交网格", ["常规"]))
    # G4：golden 生成期规模预算——强制项显式降规模（记账+打 tag）、网格项超预算剔除（记账）。
    # 必须在 1-wise 采样**之前**做：先把算不完的 shape 处理掉，再从剩下的池子里采样，
    # 否则采样名额会被注定要剔掉的 entry 占走（覆盖数虚高）。
    # ⚠ 预算值**无条件先校**：坏值（0/负/非整）不许靠「这次没加载到 golden.py」蒙混过 CP-B 的 dry-run。
    budget = _cost_budget(spec)
    if cost_fn is not None:
        grid, cost_ledger = _apply_cost_budget(forced, grid, cost_fn, budget)
    else:
        cost_ledger = _empty_cost_ledger()
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
        "golden_cost": cost_ledger,           # G4 覆盖账本：降规模的强制项 + 被剔除的超预算 shape
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
    in_params = [p for p in spec["params"] if p["io"] == "in"]
    check_spec_capability(in_params)                     # 能力边界前置：先于 load_golden，别为不支持的算子白加载 golden
    # C1：load_golden 返回 4 元组，第 4 项是**可选**的 out_shape（未导出=None → 缺省同形语义）。
    golden_fn, golden_source, _golden_provenance, out_shape_fn = load_golden(op)
    attrs_default = {p["name"]: p.get("default") for p in spec["params"] if p["io"] == "attr"}
    self_param = next((p for p in in_params if p["name"] == "self"), in_params[0])
    dtypes = self_param["dtype"]
    # （dtype 空/重复/白名单三道校验已提进 check_spec_capability，先于 load_golden 执行）
    spec_standard = precision_policy.select_standard(spec)  # 平台层标准（显式或按 oracle+verify_mode 映射）
    vmode = spec["verify_mode"]
    exact = vmode == "exact"
    os.makedirs(work_dir, exist_ok=True)

    # §1 用例预算 case_target（spec.precision.case_target，默认 50）。校验 int 且 ≥1——堵 0/负/非整
    # 空跑冒充验收（评审 #5）；< 强制下限时 _plan 用 max(target,|forced|)、emit>target 并 note（评审 #8）。
    case_target = (spec.get("precision") or {}).get("case_target", _DEFAULT_CASE_TARGET)
    if isinstance(case_target, bool) or not isinstance(case_target, int) or case_target < 1:
        raise ValueError(f"precision.case_target 须为 ≥1 的整数（防零用例空跑冒充验收），得 {case_target!r}")

    # G4：据 C1 的 out_shape 造生成期规模预算的 cost 模型（未导出 out_shape → 按输入广播形状 = elementwise）。
    cost_fn = _make_cost_fn(in_params, out_shape_fn)
    entries, plan_meta = _plan(spec, in_params, dtypes, attrs_default, op, case_target, cost_fn=cost_fn,
                               empty_accepts=_make_empty_accepts(in_params, out_shape_fn, attrs_default))
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
        # C1：算子声明了 out_shape → **与 golden_fn 实际返回的形状对账**，不一致即 fail-closed。
        # 两者打架时既不信声明也不信实测：下游 runner 按 caseset 的形状收发、validator 按 golden 判，
        # 谁静默胜出都会产出「看起来对」的结果。out_shape_source 如实记这形状是「声明并已核」还是「实测」。
        actual_out_shape = tuple(int(d) for d in np.shape(golden))
        if out_shape_fn is not None:
            declared = _declared_out_shape(out_shape_fn, inputs, attrs, cid)
            if actual_out_shape != declared:
                raise ValueError(f"{cid}: golden_fn 实际输出形状 {actual_out_shape} ≠ golden.py 的 "
                                 f"out_shape() 声明 {declared}（声明与实现打架，fail-closed；"
                                 f"in_shapes={[tuple(np.asarray(x).shape) for x in inputs]} attrs={attrs}）")
            out_shape_source = "golden.out_shape"        # 声明并已与实测对账
        else:
            # 未声明 → 缺省语义是「输出同各输入广播形状」。**必须当场校**，别只是照抄实测形状：
            # 一个真会改形状的 golden 若**忘了导出 out_shape**，照抄下去 CP-B 全绿、拖到下游 runner
            # 按错形状收发才炸——正是本仓最忌的「本机过、真机炸」。缺省语义是承诺，不是默认值。
            bshape = tuple(int(d) for d in np.broadcast_shapes(
                *[np.asarray(x).shape for x in inputs])) if inputs else ()
            if actual_out_shape != bshape:
                raise ValueError(
                    f"{cid}: golden_fn 实际输出形状 {actual_out_shape} ≠ 各输入广播形状 {bshape}，"
                    f"但 golden.py **未导出 out_shape()**。缺省语义是「输出同输入形状」（elementwise）——"
                    f"该算子既然改形状，就必须导出 out_shape(in_shapes, attrs) 显式声明（C1），"
                    f"否则下游按错形状收发。fail-closed。"
                    f"（in_shapes={[tuple(np.asarray(x).shape) for x in inputs]} attrs={attrs}）")
            out_shape_source = "golden_fn_actual"        # 未声明且已核 = 缺省同形语义成立
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
            empty_expected = {"golden_source": golden_source, "golden_path": f"{cid}/golden.npy",
                              "verify_mode": vmode, "compare": "na", "standard": "na",
                              "compare_dtype": None, "case_origin": entry["case_origin"],
                              "rule_ref": entry["rule_ref"],
                              "out_shape": list(actual_out_shape),      # C1：输出形状（供下游收发）
                              "out_shape_source": out_shape_source,
                              "note": "空Tensor 功能用例（numel=0，无精度判定，validator→na）"}
            if entry.get("cost_scaled"):                 # G4：该 case 被降过规模 → 随 case 一起如实留痕
                empty_expected["cost_scaled"] = entry["cost_scaled"]
            cases.append({"id": cid, "dims": dims, "tags": entry["tags"], "inputs": in_items,
                          "attrs": attrs, "expected": empty_expected})
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
        if dtn == _BF16 and not out_is_bool and not _bf16_bitexact(spec, op):
            raise ValueError(
                f"bf16 numerical for op {op!r} 需 lossy 阈值：输出非 bool，且该算子未声明 bf16 逐位可达。\n"
                f"  → 若本算子是**纯搬运/纯符号**类（输出恒等于某个输入元素、不做算术，如 gather/\n"
                f"    转置/最近邻采样/符号），在 spec 写 `precision.bf16_bitexact: true` 显式声明；\n"
                f"  → 若它真做算术（加乘、插值、归约），bf16 输出本就不可能逐位重现，"
                f"应挂 dtype_deferred 或给 lossy 阈值。\n"
                f"  ⚠ 不因 verify_mode=exact 静默放行——exact 是判据、不是算子性质。")
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
                    "out_shape": list(actual_out_shape),      # C1：输出形状（供下游 runner/validator 收发）
                    "out_shape_source": out_shape_source,     # golden.out_shape（声明并已核）/ golden_fn_actual
                    "case_origin": entry["case_origin"], "rule_ref": entry["rule_ref"]}
        if entry.get("cost_scaled"):                     # G4：该 case 被降过规模 → 随 case 一起如实留痕
            expected["cost_scaled"] = entry["cost_scaled"]
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
            # G4 覆盖账本：预算 + cost 模型（含其诚实边界）+ 被降规模的强制项 + 被剔除的超预算 shape。
            # 报告侧读这里就能说清「大 shape 是降规模后覆盖的 / 哪些规模根本没跑」，不靠猜。
            "golden_cost": plan_meta["golden_cost"],
            "cases": cases}


def _dry_run(spec):
    """plan-only（**不跑 golden_fn**、不落 .npy）：打印覆盖账本 + 确定性自检。供测试与 acc-spec 预探。

    G4 起会**尽力**加载 golden.py 取 `out_shape` 造规模预算的 cost 模型——好让「归约/成对类算子的大 shape
    算不完」在 **CP-B 契约自检**就暴露，而不是拖到 CP-D 真生成时卡死。加载不到（golden.py 还没写、或写坏了）
    → 明说「未核」并照常出计划，**不阻塞**（那种 spec 到了 gen_cases 本来就会 fail-closed）。
    ⚠ 本仓 golden.py 约定 torch 延迟 import（见 `samples/golden/*/golden.py`），故此处仍不拉 torch；
    但若某算子在模块顶层 `import torch`，dry-run 会跟着 import ——这是加载用户代码的代价，如实记在这。"""
    from collections import Counter
    op = spec["op"]
    in_params = [p for p in spec["params"] if p["io"] == "in"]
    check_spec_capability(in_params)                     # 能力边界前置：三元算子在 CP-B 就拦下，不拖到 CP-D
    attrs_default = {p["name"]: p.get("default") for p in spec["params"] if p["io"] == "attr"}
    self_param = next((p for p in in_params if p["name"] == "self"), in_params[0])
    dtypes = self_param["dtype"]
    case_target = (spec.get("precision") or {}).get("case_target", _DEFAULT_CASE_TARGET)
    # G4：取 cost 模型。**只对「golden.py 还没写」降级为「未核」**，不吞其它加载失败。
    # ⚠ 原来是 `except Exception` 一把吞：一份**已存在但坏掉**的 golden.py（语法错、顶层抛异常、
    # 契约导出不全）也能安静通过 CP-B —— 而散文把 dry-run 称作「契约自检」，这就是 fail-open。
    # 「还没写」是合法的预览场景（spec 先行、golden 后补）；「写了但坏了」是**真错误**，必须当场炸。
    cost_fn, cost_why, _dry_out_shape_fn = None, "", None
    try:
        _dry_out_shape_fn = load_golden(op)[3]
        cost_fn = _make_cost_fn(in_params, _dry_out_shape_fn)
    except ValueError as ex:
        msg = str(ex)
        if not msg.startswith("缺 golden:"):            # 文件在、但契约/执行有问题 → 不降级
            raise
        cost_why = f" ← 未核（{msg.splitlines()[0][:80]}）"
    entries, meta = _plan(spec, in_params, dtypes, attrs_default, op, case_target, cost_fn=cost_fn,
                          empty_accepts=_make_empty_accepts(in_params, _dry_out_shape_fn, attrs_default))
    seen, ids = set(), []
    for e in entries:                                    # 跑 _mk_id 校 id 唯一（撞则 raise）
        ids.append(_mk_id(op, e["dtype"], e["shape"], e["id_kind"], e["attr_idx"], seen))
    specials = {"empty", "scalar", "bndlo", "bndhi", "inf", "ninf", "nan"}
    print(f"[dry-run] {op} target={case_target} emitted={meta['emitted']} pool_max={meta['pool_max']} "
          f"forced_total(=强制下限S)={meta['forced_total']} forced_special={meta['forced_special']}")
    print(f"  区间: case_target 建议落 [S={meta['forced_total']}, pool_max={meta['pool_max']}]"
          f"（< S 则 emit 抬到 S；> pool_max 则 emit=pool_max、数量门软化 PASS+note）")
    _rk = _allowed_ranks(in_params)                      # C3：rank 约束（None=不限制）
    print(f"  input_rank: {'不限制' if _rk is None else sorted(_rk)}  "
          f"shapes: {sorted({_shape_tag(e['shape']) for e in entries})}")
    print(f"  by_dtype : {dict(Counter(e['dtype'] for e in entries))}")
    print(f"  id_kinds : {dict(Counter(e['id_kind'] for e in entries))}")
    print(f"  special  : {sorted({e['id_kind'] for e in entries if e['id_kind'] in specials})}")
    if op in ("IsClose", "Equal"):
        eqn = sorted({str(e['attrs'].get('equal_nan')) for e in entries if 'equal_nan' in e['attrs']})
        print(f"  equal_nan values seen: {eqn}")
    print(f"  dropped_combo_classes: {len(meta['dropped_combo_classes'])} "
          f"(first3={meta['dropped_combo_classes'][:3]})")
    _gc = meta["golden_cost"]                            # G4 规模预算账本
    print(f"  golden_cost: budget={_gc['budget']} model={_gc['model']}{cost_why}")
    if _gc["scaled_cases"]:
        print(f"    ⚠ 降规模(强制项，已记账) {len(_gc['scaled_cases'])} 条: "
              + "; ".join(f"{r['id_kind']} {r['requested_shape']}→{r['emitted_shape']}"
                          for r in _gc["scaled_cases"][:3]))
    if _gc["skipped_shapes"]:
        print(f"    ⚠ 网格剔除(超预算，已记账，**不计入已覆盖**) {_gc['skipped_shape_classes']} 类: "
              + "; ".join(f"{r['dtype']}×{r['shape']}(cost={r['cost']})"
                          for r in _gc["skipped_shapes"][:3]))
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
