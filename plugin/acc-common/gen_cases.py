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

U3 dtype 单一真源（2026-07-24）：dtype 支持不再由本文件独家说了算，拆成**两层、各自单一真源**——
  · 生成层 = 本文件 `_NATIVE` + bf16（能造输入 / 能算 golden / 能落盘读回；本轮补齐 int64/int8/uint8）；
  · 真机层 = `repo_adapter.supported_np(runner_form)`（能收发）+ `repo_adapter.deferred_np(runner_form)`
    （Track-C 挂账：生成期放行、真机跑到仍 fail-closed）。
  `check_spec_capability` **两层都问**，缺哪层就在报错里点名哪层、并列出两侧各自的支持集。
  修的是这个真 bug：aclnn_py runner 早已支持 int64/int8/uint8，生成端却自带旧硬表挡掉 →
  任务书 8 类 dtype 的覆盖被**工具**压到 4/8（不是被算子）。缺省 `runner_form=cpp` 口径不变，
  现有算子 caseset 逐字节不变（测试以 sha256 钉住）。

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
    **零新契约**：`load_golden` 的返回结构不动、spec 不加复杂度字段、4 份 elementwise 样例 golden 一字不动。
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

算子类别 `spec.operator_class` → **特殊值口径分档**（字段驱动、op-中立；2026-07-24 修，落地见各处 `OC:` 标记）：
  · **要修的真 bug（实证，不是推测）**：本引擎原先**无条件**给每个浮点 dtype 铺 §1.4 的
    `inf` / `-inf` / `nan` 特殊值用例。对 median 这类**结构类**（选值 / 排序 / 索引 / 规约取元素）算子，
    这些用例**超出验收口径** —— median PR6429 实跑判 `FAIL(精度)`，**6 条 fail 全是 NaN 用例**，
    一个合格 PR 就这么被工具判挂了。
  · **依据**（本项目 case 生成规则要求参照的仓 `Justbin/cannbot-ops-input`）：
      `skills/operator-case-generation/common/design_contract.py:427` 受控词表
      `{floating_compute, integer_compute, structural}`；同文件 `:512`
      `if design["operator_class"] == "floating_compute": _validate_floating_rules(design)` ——
      而 `_validate_floating_rules`（:360-393）**只在这一类里**强制 `nan/pos_inf/neg_inf/mixed_inf`。
      另两类的口径见该仓 `SKILL.md:252`：「define value profiles and specials from the Torch semantics:
      extrema, zero/one/minus-one, duplicate/negative/boundary indices, broadcasting relationships,
      reduction axes, saturation…」——**极值 / 0·1·-1 / 重复 / 越界索引 / 广播 / 规约轴 / 饱和**，不含 NaN·Inf。
      实证：该仓 `ops-bench/ops-eval-dataset/designs/aclnnMedian/case_design.json` 的
      `operator_class = "structural"`、全文零 nan 零 inf；对照 `aclnnPdist`（`floating_compute`）
      有 nan 2 处、inf 8 处。
  · **分档**：`floating_compute` → 保持现状（照产 inf/-inf/nan，`nan` value_profile 可用）；
    `structural` / `integer_compute` → **不产** inf/-inf/nan 特殊场景，且 spec 再声明 `value_profiles` 含
    `nan` → **fail-closed**（不静默忽略——静默会让「账面声明了 NaN 覆盖」与「实际一条没产」长期打架）；
    **整字段省略（未声明）→ 行为与改动前逐字节一致**（向后兼容硬约束：现有 4 算子 isclose/sign/equal/neg
    都没声明，caseset + 全部 .npy 的 sha256 已实测全等）；词表外取值 → fail-closed。
  · **`scalar`/`bndlo`/`bndhi`（标量·上下边界）与 `tie` value_profile 对所有类别保留**——参考仓给结构 /
    整型类列的正是「极值、0/1/-1、**重复**、…」这一档，tie（并列 / 重复值）属其中，不该被一起砍掉。

轴维度边界的定向生成 `spec.attr_axis_lengths`（字段驱动、op-中立；2026-07-24 审计修复后的不变式）：
  · **要解决的**：任务书点名「归约轴上维度为 1」这类边界，而 shape 阶梯与 attr 取值在正交网格里各自独立取，
    含长度-1 轴的 shape 只跟排在前面的 attr combo 撞上 → 点名场景实跑 0 条（pdist/median 首跑实测）。
    声明 `[{"attr":"dim","lengths":[1]}]` 即**定向生成**「dim 指的那根轴长度=1」的强制用例。
  · **不变式 ①（finding #4·高危假覆盖）**：被约束的那根轴在 G4 降规模中**锁定不许动**；锁后仍超预算 →
    **fail-closed**（明说「轴长度约束与 cost 预算冲突」）。预算处理**完成后**还要逐条复验实际轴长
    （`_verify_axis_locks`）。否则 `ax0len100` 会被降成 `(4,3)`、`id_kind`/`case_origin` 却仍宣称覆盖
    长度 100 —— **账本与 case ID 声称覆盖了任务书边界、实际输入根本没覆盖**，比没有这套机制更糟。
  · **不变式 ②（finding #5）**：基准 shape 的 rank **逐轴值挑**（`_axis_base_shape`）——rank 允许 `{2,4}`
    而 attr 含 `dim=3` 时，`dim=3` 必须落到 rank4，不许被当越界静默跳过。账本与 fail-closed 判据按
    `(constraint, length, attr 组合)` **逐项**判，**不看全局 emitted 总数**（部分缺失也是缺失）。
  · **不变式 ③（finding #8）**：零配对告警对**轴型 attr** 用「归一化轴号 + rank + 被指轴的实际长度档」
    作配对键（`_unpaired_combo_classes`）——按 shape 结构类判会把 `shape=(4,1), dim=0`（归约轴其实长 4）
    误记成「已配上单位轴」（漏报），反向又会对该 rank 下已越界的轴值报出不可实现的缺口（误报）。
    普通非轴 attr 仍走 shape 结构类口径。

造例档位 `spec.precision.case_profile` —— **能力档位开关**（字段驱动、op-中立；2026-07-25 引入，
落地见各处 `CP:` 标记）：
  · **为什么要它**：把本引擎的造例规则**忠实对齐**参考仓 `Justbin/cannbot-ops-input`（完整笛卡尔精度网格、
    medium shape 档、normal 值域重采样、4-kind 非有限特殊值 …）会**改掉默认造例行为**——而 4 个已真机
    验收的 elementwise 算子（IsClose/Sign/Equal/Neg）的 caseset 与全部 .npy 是**逐字节钉死**的
    （sha256 实测，见 `test_gen_cases_dtype_attr.ExistingOpsByteIdenticalTest`）。所以**先立一道字段驱动的
    档位开关**，后续所有对齐改动一律只在新档位下生效，老算子的字节纹丝不动。
  · **受控词表**（两档，无第三种；实现见 `_case_profile` / `_case_profile_declared`）：
      - `legacy` —— 现行造例规则；**整字段省略即此**，逐字节等于本字段引入前；
      - `torch_parity` —— 忠实对齐参考仓的造例规则，**仅**用于「任务书对标 torch」场景（不碰 catlass 通路）。
  · **律令 #0 合规**：这是按 **spec 声明的能力档位**分支，**不是按算子名**——换任意声明了 `torch_parity`
    的域内算子，工具零改即用；代码里没有也不许有 `if op == "<算子名>"`。
  · `torch_parity` 必须同时声明 `precision.torch_parity_matrix`，按 dtype×rank×shape profile×attribute
    profile 生成完整笛卡尔；rank 动态轴 class 在逐 case 解析成 first/middle/last，且 `case_target`
    必须精确等于完整矩阵大小，禁止静默抽样。`legacy` 与未声明仍保持逐字节兼容。
  · 词表外取值 / 非字符串（含**显式 `null`**）→ fail-closed：档位猜错 = 整份用例集悄悄换一套规则，
    比报错贵得多。
"""
import collections, hashlib, importlib.util, json, math, os, sys
import numpy as np
import content_address
import precision_policy

SEED = 2026

# ============ 随机流版本钉（B-1）：`SEED` 不是数据身份的全部 ====================
# `_case_rng` 把 `SEED ^ hash(case_id)` 喂给 `np.random.default_rng`，于是「同一 case_id 产同一
# 字节」这条设计**只在同一条 numpy 随机流下成立**。numpy 对 `Generator` 的承诺与 `RandomState`
# 不同：NEP 19 明确保留在 feature release 里改流的权利，所以升级 numpy 就可能让**同一份 spec、
# 同一个 case_id** 落出不同的 `.npy` 字节，而 spec / planner / golden 的摘要一个都不会变。
#
# 仓内已有实证：`test_gen_cases_dtype_attr` 的 caseset 字节 pin 就是按 numpy 版本分基线存的
# （见该文件的 `_U3_CASESET_BASELINES`）——那份 pin 之所以要分版本，正是因为流会漂。
#
# 所以 `SEED` 之外还须把**产数据的那条流是哪一条**记进计划账本，让复用侧能当场失配。
# 记两个字段而不是一个：`numpy_version` 是诚实的全量版本（诊断用，别拿它做判定，补丁版
# 通常不改流、精确相等会造成大量无谓 MISS）；`numpy_stream_pin` 是**判定值**。
_NUMPY_STREAM_PIN_GRANULARITY = "major_minor"


def numpy_stream_pin(version):
    """把完整 numpy 版本号收敛成随机流比对用的 `主.次` 两段 pin。

    口径与仓内既有 pin 一致（`test_gen_cases_dtype_attr` 用 `startswith(pin + ".")` 判定），
    不引入第二套标准。解析不出两段数字 → 抛错，**绝不回退成整串或空串**：一个含糊的 pin
    会让「版本不同」和「版本存疑」长得一模一样，而后者本该 fail-closed。"""
    parts = str(version).split(".")
    if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        raise ValueError(
            f"无法从 numpy 版本 {version!r} 解析出 主.次 两段随机流 pin；"
            f"随机流身份不明时不得放行复用")
    return f"{parts[0]}.{parts[1]}"


def current_numpy_stream_pin():
    """本进程正在用的那条 numpy 随机流的 pin（复用门的**唯一真源**，勿在别处另抄一份）。"""
    return numpy_stream_pin(np.__version__)


_BF16 = "bfloat16"
# 原生 numpy dtype（bf16 不在此——它逻辑 fp32、物理 uint16，特判）。
# 这张表 = **生成层**的 dtype 能力真源（能造输入 / 能算 golden / 能落盘读回），
# 与 `repo_adapter.SUPPORTED_NP_BY_FORM`（**真机层**能收发什么）是两回事、各自单一真源；
# 两层都过才允许进用例生成（`check_spec_capability`），缺哪层报错里点名哪层（U3）。
# U3 扩：int64（aclnn indices 与整型算子必需）+ int8/uint8（op_def 常见整型档）。
# 无符号 dtype 的输入构造见 `_make_varied`（没有负数分支，按有无符号位分、与算子身份无关）。
_NATIVE = {"float32": np.float32, "float16": np.float16,
           "int64": np.int64, "int32": np.int32, "int16": np.int16,
           "int8": np.int8, "uint8": np.uint8}
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


_PERF_SHAPE_PROFILES = {
    # 用户确认的 A3 通用规则：全部物理输入载荷 <= 256 KiB 可一次搬完 UB。
    "Atlas A3": {"metric": "sum_input_bytes", "small_max_bytes": 256 * 1024},
}


def _perf_case_policy(spec):
    """解析性能 case 来源与 shape 大小分类契约；未声明则保持历史行为。

    ``case_source=precision_cases`` 只表示性能 case 必须从精度 caseset 中选，不表示每条精度 case
    都必须测性能。``shape_classification`` 仅负责可审计分组，不参与免测、阈值放宽或 pass/fail。
    """
    perf = spec.get("perf")
    if not isinstance(perf, dict):
        return None
    source = perf.get("case_source")
    rule = perf.get("shape_classification")
    if source is None or rule is None:
        raise ValueError(
            "spec 声明了 perf，就必须同时声明 perf.case_source='precision_cases' 与 "
            "perf.shape_classification；性能 case⊆精度 case、按目标硬件 UB 单次承载边界分大小 shape "
            "是通用规则，不能静默省略")
    if source != "precision_cases":
        raise ValueError(
            f"perf.case_source 仅支持 'precision_cases'，得 {source!r}；"
            "性能用例须从同一份精度 caseset 选取，不另造一套输入")
    if not isinstance(rule, dict):
        raise ValueError("perf.shape_classification 须为 object")
    metric = rule.get("metric")
    if metric != "sum_input_bytes":
        raise ValueError(
            f"perf.shape_classification.metric 仅支持 'sum_input_bytes'，得 {metric!r}")
    limit = rule.get("small_max_bytes")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError(
            f"perf.shape_classification.small_max_bytes 须为 ≥1 的整数，得 {limit!r}")
    hardware = rule.get("hardware")
    if not isinstance(hardware, str) or not hardware.strip():
        raise ValueError("perf.shape_classification.hardware 须为非空字符串")
    hardware = hardware.strip()
    profile = _PERF_SHAPE_PROFILES.get(hardware)
    if profile is None:
        raise ValueError(
            f"perf.shape_classification.hardware={hardware!r} 尚无受控大小 shape profile；"
            "须先按目标硬件核定 UB 单次承载边界，不能由 spec 任意填写")
    if metric != profile["metric"] or limit != profile["small_max_bytes"]:
        raise ValueError(
            f"{hardware} 大小 shape profile固定为 metric={profile['metric']!r}, "
            f"small_max_bytes={profile['small_max_bytes']}，得 metric={metric!r}, "
            f"small_max_bytes={limit!r}")
    selection = perf.get("case_selection") or {}
    if not isinstance(selection, dict):
        raise ValueError("perf.case_selection 须为 object")
    min_total_elements = selection.get("min_total_input_elements", 1)
    if (isinstance(min_total_elements, bool) or not isinstance(min_total_elements, int)
            or min_total_elements < 1):
        raise ValueError(
            "perf.case_selection.min_total_input_elements 须为 ≥1 的整数，"
            f"得 {min_total_elements!r}")
    max_cases = selection.get("max_cases")
    if (max_cases is not None
            and (isinstance(max_cases, bool) or not isinstance(max_cases, int)
                 or max_cases < 1)):
        raise ValueError(
            f"perf.case_selection.max_cases 须为 ≥1 的整数或省略，得 {max_cases!r}")
    include_precision_tags_declared = "include_precision_tags" in selection
    include_precision_tags = selection.get("include_precision_tags") or []
    if (not isinstance(include_precision_tags, list)
            or any(not isinstance(tag, str) or not tag.strip()
                   for tag in include_precision_tags)):
        raise ValueError(
            "perf.case_selection.include_precision_tags 须为非空字符串数组")
    include_precision_tags = [tag.strip() for tag in include_precision_tags]
    if len(set(include_precision_tags)) != len(include_precision_tags):
        raise ValueError(
            "perf.case_selection.include_precision_tags 不得含重复项")
    selection_contract = {
        "min_total_input_elements": int(min_total_elements),
        "reason": "exclude_degenerate_inputs_without_comparable_device_kernel",
    }
    if max_cases is not None:
        selection_contract["max_cases"] = int(max_cases)
    if include_precision_tags_declared:
        selection_contract["include_precision_tags"] = include_precision_tags
    return {"case_source": source,
            "case_selection": selection_contract,
            "shape_classification": {
                "metric": metric,
                "small_max_bytes": int(limit),
                "boundary": "small_if_input_bytes_lte_limit",
                "hardware": hardware,
            }}


def _classify_perf_cases(spec, cases):
    """按 spec 给性能 case 打「小shape/大shape」标签并返回 caseset 级账本。

    输入载荷按各 input 的**物理存储 dtype**计字节；bf16 因 ``storage_dtype=uint16`` 按 2 bytes
    计算。边界是闭区间：恰好 ``small_max_bytes`` 仍属小 shape。若声明 ``max_cases``，
    只会从相同精度 caseset 按 dtype×shape class 轮转选子集；不会产生 ``trivial-met`` 或性能豁免。
    """
    policy = _perf_case_policy(spec)
    if policy is None:
        return None
    rule = policy["shape_classification"]
    limit = rule["small_max_bytes"]
    def _load(case):
        cid = case.get("id")
        inputs = case.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise ValueError(f"{cid}: 性能 case 缺 inputs，无法按输入载荷字节分类")
        total = 0
        total_elements = 0
        for inp in inputs:
            if not isinstance(inp, dict):
                raise ValueError(f"{cid}: input 条目非 object，无法分类")
            shape = inp.get("shape")
            if (not isinstance(shape, list)
                    or any(isinstance(d, bool) or not isinstance(d, int) or d < 0 for d in shape)):
                raise ValueError(f"{cid}: input shape={shape!r} 非非负整数数组，无法分类")
            logical_dtype = inp.get("dtype")
            if not isinstance(logical_dtype, str):
                raise ValueError(f"{cid}: input dtype={logical_dtype!r} 非字符串，无法分类")
            storage_dtype = inp.get("storage_dtype") or _storage_name(logical_dtype)
            try:
                itemsize = int(np.dtype(storage_dtype).itemsize)
            except TypeError as exc:
                raise ValueError(
                    f"{cid}: input storage dtype={storage_dtype!r} 无法换算字节数") from exc
            input_elements = _numel(shape)
            total_elements += input_elements
            total += input_elements * itemsize
        dtypes = sorted({i.get("dtype") for i in inputs
                         if isinstance(i, dict) and isinstance(i.get("dtype"), str)})
        return int(total_elements), int(total), "+".join(dtypes) if dtypes else "unknown"

    # 先列出全部候选并计算分类，再按 (dtype,size) 队列 round-robin 取 max_cases。
    # 选择只读 case 字段，不另造输入；最终仍复用原 precision case_id。
    include_tags = policy["case_selection"].get("include_precision_tags", [])
    min_total_elements = policy["case_selection"]["min_total_input_elements"]
    eligible, excluded_degenerate_ids = [], []
    for case in cases:
        dims = case.get("dims") or []
        tagged = bool(include_tags and set(case.get("tags") or []).intersection(include_tags))
        if "性能" not in dims and not tagged:
            continue
        cid = case.get("id")
        if "精度" not in dims:
            raise ValueError(
                f"{cid}: perf.case_source='precision_cases'，但性能候选的 dims 不含「精度」")
        total_elements, total, dtype_key = _load(case)
        if total_elements < min_total_elements:
            case["perf_selection_exclusion"] = {
                "reason": "degenerate_total_input_elements_below_minimum",
                "total_input_elements": int(total_elements),
                "min_total_input_elements": int(min_total_elements),
            }
            excluded_degenerate_ids.append(cid)
            continue
        size_class = "small" if total <= limit else "large"
        eligible.append((case, total, dtype_key, size_class))

    max_cases = policy["case_selection"].get("max_cases")
    selected_set = None
    if max_cases is not None and len(eligible) > max_cases:
        queues = {}
        for row in eligible:
            queues.setdefault((row[2], row[3]), []).append(row)
        ordered = [queues[key] for key in sorted(queues)]
        positions = [0] * len(ordered)
        chosen = []
        while len(chosen) < max_cases:
            progressed = False
            for qi, queue in enumerate(ordered):
                if positions[qi] >= len(queue):
                    continue
                chosen.append(queue[positions[qi]])
                positions[qi] += 1
                progressed = True
                if len(chosen) == max_cases:
                    break
            if not progressed:
                break
        selected_set = {row[0]["id"] for row in chosen}

    counts = {"small": 0, "large": 0}
    selected_ids, excluded_precision_ids = [], []
    by_dtype = {}
    eligible_by_id = {row[0]["id"]: row for row in eligible}
    for case in cases:
        cid = case.get("id")
        row = eligible_by_id.get(cid)
        if row is None or (selected_set is not None and cid not in selected_set):
            if "性能" in (case.get("dims") or []):
                case["dims"] = [dim for dim in case["dims"] if dim != "性能"]
            if "精度" in (case.get("dims") or []) and isinstance(cid, str):
                excluded_precision_ids.append(cid)
            if row is not None:
                case["perf_selection_exclusion"] = {
                    "reason": "balanced_max_cases_limit",
                    "max_cases": int(max_cases),
                    "balance_axes": ["dtype", "shape_class"],
                }
            continue
        _case, total, dtype_key, size_class = row
        dims = list(case.get("dims") or [])
        if "性能" not in dims:
            case["dims"] = dims + ["性能"]
        label = "小shape" if size_class == "small" else "大shape"
        tags = [t for t in (case.get("tags") or []) if t not in ("小shape", "大shape")]
        case["tags"] = tags + [label]
        case["perf_shape_classification"] = {
            "class": size_class,
            "input_bytes": int(total),
            **rule,
        }
        counts[size_class] += 1
        selected_ids.append(cid)
        by_dtype[dtype_key] = by_dtype.get(dtype_key, 0) + 1
    return {**policy, "counts": counts,
            "selection": {
                "identity_rule": "selected case_id is reused from the same precision caseset",
                "selected_case_ids": selected_ids,
                "excluded_precision_case_ids": excluded_precision_ids,
                "excluded_degenerate_case_ids": excluded_degenerate_ids,
                "selected_by_dtype": dict(sorted(by_dtype.items())),
                "selected_total": len(selected_ids),
                "precision_total": sum(1 for c in cases if "精度" in (c.get("dims") or [])),
            }}


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
# 批 2：`load_golden` 的返回改具名元组。⚠ **两类风险分开说**（2026-07-23 审计更正原注释）：
#   · 改 arity 时，旧的 `a, b, c, d = load_golden(op)` 会当场 `ValueError` —— **不是静默错位**，是好事；
#   · 真正会**静默指错**的是固定数字下标（`load_golden(op)[3]`）：字段插入/重排后下标依旧合法、只是指向变了。
# 具名元组两类都躲开：**按名取**（`g.out_shape`），既不与 arity 耦合、也不与字段序耦合。
Golden = collections.namedtuple("Golden", "fn source provenance out_shape contract")


def load_golden(op):
    """按算子名从用户侧加载 golden——`<ops_root>/<op>/golden.py`，返回
`Golden(fn, source, provenance, out_shape, contract)` —— **具名元组**（位置访问仍可用，
    但解包个数必须与当前 5 字段一致；调用方一律**按名取**）。

    ⚠ **返回 5 字段具名元组 `Golden(fn, source, provenance, out_shape, contract)`**
    （C1 起加 `out_shape`、批 2 起加 `contract`；最早是 3 元组）。`out_shape` / `contract` 均**可选**，未导出即 `None`。
    **按名取用**（`g.out_shape`），别按下标——下标会随契约再扩而漂。
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
            f"  → 新算子需先由 acc-runner-dev:gen_golden 从任务书生成 golden.py 落到用户目录"
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
    except BaseException as ex:                      # noqa: BLE001 —— **必须比 Exception 宽**
        # `SystemExit` / `GeneratorExit` 不是 `Exception` 的子类：golden.py 里一句 `sys.exit(0)`
        # 会穿过 `except Exception` 直达解释器，让整条流水线**以退出码 0 静默收场**——
        # 一个根本没加载成功的 golden 冒充「跑完了」。2026-07-23 审计实测复现，故这里捕 BaseException。
        # `KeyboardInterrupt` 例外：用户主动中断不该被包装成 golden 的问题。
        if isinstance(ex, KeyboardInterrupt):
            raise
        raise ValueError(f"golden.py 执行失败({type(ex).__name__}): {gpath}: {ex}") from ex
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
    # 批 2：**可选**的声明式来源块 `GOLDEN_CONTRACT`（source / method_kind / authorization /
    # taskdoc_snapshot）。导出了就校词表 + 派生档位；没导出 → contract=None、tier 记 None，
    # **行为与批 2 之前完全一致**（不强制既有 golden 立刻改写；强制是批 5 门侧的事）。
    contract = getattr(mod, "GOLDEN_CONTRACT", None)
    if contract is not None:
        precision_policy.validate_golden_contract(contract, f"{gpath} 的 GOLDEN_CONTRACT")
    return Golden(mod.golden_fn, mod.GOLDEN_SOURCE, mod.GOLDEN_PROVENANCE, out_shape_fn, contract)


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
            # 分支覆盖锚点：**有符号** dtype 钉 (-2,0,3)（负/零/正三分支，行为与 U3 前逐字节一致）；
            # **无符号** dtype 根本没有负数这一支，钉 (0,1,3)——`cdt(-2)` 在 numpy≥2 上直接 OverflowError，
            # 静默回绕成 254 更坏（会假装「测了负数」）。按 dtype **有无符号位**分，与算子身份无关。
            anchors = (-2, 0, 3) if int(info.min) < 0 else (0, 1, 3)
            f[0], f[1], f[2] = cdt(anchors[0]), cdt(anchors[1]), cdt(anchors[2])
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
    整数网格上构造；golden 天然含 True/False（下游 exact bool 断言校验）。

    ⚠ +5 为什么不会溢出（U3 扩到 int8/uint8/int64 后仍成立）：`_make_varied` 把整型值域夹在
    `[max(-100,min+1), min(100,max)]`，故 ref ≤ 100；本仓最窄的整型是 int8（max=127）→ 105 ≤ 127。
    再窄的整型（若将来加 int4 之类）须重挑增量，别默认这条不变式还成立。"""
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


# ===================== OC: operator_class —— 算子类别决定「该不该喂 NaN·Inf」（受控词表）==========
# 详见模块 docstring「算子类别 → 特殊值口径分档」一节（含 median PR6429 的实证与参考仓依据出处）。
# ⚠ 这是**字段驱动**：引擎只读 spec 里这个词，**绝不按算子名分支**（律令 #0）。
_OPERATOR_CLASSES = ("floating_compute", "integer_compute", "structural")
# 只有这些类别铺非有限特殊值（inf / -inf / nan）。**未声明**另按「现行为」处理，见 `_emits_nonfinite`。
_NONFINITE_CLASSES = frozenset({"floating_compute"})


def _operator_class(spec):
    """spec.`operator_class` → 受控词表值；**未声明 → `None`**（= 改动前的现行为，向后兼容硬约束）。

    词表外取值 / 非字符串 → fail-closed。这个字段决定「该不该给它喂 NaN·Inf」，猜错的代价两头都很贵：
    该判 floating_compute 的判成 structural → **该测的 NaN 没测**（漏）；反过来 → **不该判挂的判挂**
    （median PR6429：6 条 NaN 用例把合格 PR 判成 FAIL(精度)）。故不兜默认、不猜。"""
    v = spec.get("operator_class")
    if v is None:
        return None
    if not isinstance(v, str) or v not in _OPERATOR_CLASSES:
        raise ValueError(
            f"spec.operator_class={v!r} 不在受控词表 {list(_OPERATOR_CLASSES)} 里 —— fail-closed，不猜。\n"
            f"  判法：浮点算术 / 规约求和类 → floating_compute；"
            f"选值 / 排序 / 索引 / 规约取元素类（median、min、max、topk、sort、argmax…）→ structural；"
            f"纯整型逻辑 → integer_compute。\n"
            f"  拿不准就**整字段省略**（= 现行为：照产 inf/-inf/nan），别编词表外的值。")
    return v


def _emits_nonfinite(op_class):
    """该类别是否铺 §1.4 的非有限特殊值（inf / -inf / nan）。

    `None`（未声明）→ **True**：向后兼容硬约束——现有 4 个算子（isclose/sign/equal/neg）都没声明，
    它们的 caseset 与 .npy 必须逐字节不变（已用 sha256 实测钉住，非推断）。"""
    return op_class is None or op_class in _NONFINITE_CLASSES


# ================= CP: case_profile —— 造例规则的**能力档位**（受控词表）====================
# 详见模块 docstring「造例档位 case_profile」一节（含引入动机与字节安全依据）。
# ⚠ 这是**字段驱动**：引擎只读 spec 里这个词，**绝不按算子名分支**（律令 #0）。
_CASE_PROFILES = ("legacy", "torch_parity")
_PLANNER_DEPENDENCIES = (
    "gen_cases.py",
    "repo_adapter.py",
    "precision_policy.py",
)
# 未声明时的缺省档 = 现行造例规则（向后兼容硬约束：老算子 caseset 逐字节不变）。
_DEFAULT_CASE_PROFILE = "legacy"


def _case_profile(spec):
    """spec.`precision.case_profile` → 受控词表值；**整字段省略 → `"legacy"`**（= 现行为，向后兼容硬约束）。

    **provenance（这条规则抄自哪、我们为何偏离）**：参考仓 `Justbin/cannbot-ops-input`（本项目 case 生成规则
    要求参照的仓）**没有**这个字段——它整仓只跑一套造例规则，不需要向后兼容。**我们需要**：本引擎的造例
    默认行为被 4 个已真机验收的 elementwise 算子（IsClose/Sign/Equal/Neg）**逐字节钉死**
    （`test_gen_cases_dtype_attr.ExistingOpsByteIdenticalTest` 的 sha256 pin，实测非推断），
    照搬参考仓规则会当场破 pin。故我们自造这道**能力档位开关**，把「忠实对齐」关进新档里：

      · `legacy`       —— 现行造例规则（= 整字段省略时的缺省），逐字节等于本字段引入前；
      · `torch_parity` —— **忠实对齐** `repos/cannbot-ops-input` 的造例规则（完整笛卡尔精度网格、
                          medium shape 档、normal 值域重采样、4-kind 非有限特殊值…），
                          **仅**用于「任务书对标 torch」场景，不影响 catlass 通路。

    ⚠ **律令 #0 合规**：按「spec 声明的**能力档位**」分支，**不是按算子名**——换任意声明了 `torch_parity`
      的域内算子，工具零改即用；per-op 的 spec 只是被通用引擎消费的**数据**。
    ⚠ 词表外取值 / 非字符串（**含显式 `null`**：字段一旦出现就必须是词表内的字符串，同
      `precision.tolerance_source` 的口径）→ fail-closed。档位猜错的代价是「整份用例集悄悄换了一套规则」，
      远贵于报错；拿不准就**整字段省略**（= legacy = 现行为），别编词表外的值。"""
    prec = spec.get("precision") or {}
    if "case_profile" not in prec:                       # 整字段省略 = 未声明 → 现行为
        return _DEFAULT_CASE_PROFILE
    raw = prec["case_profile"]
    if not isinstance(raw, str) or raw not in _CASE_PROFILES:
        raise ValueError(
            f"spec.precision.case_profile={raw!r} 不在受控词表 {list(_CASE_PROFILES)} 里 —— fail-closed，不猜。\n"
            f"  · 'legacy'       —— 现行造例规则（= 整字段省略时的缺省；已验收算子的 caseset 逐字节不变靠它）；\n"
            f"  · 'torch_parity' —— 忠实对齐参考仓 cannbot-ops-input 的造例规则，"
            f"仅『任务书对标 torch』场景用。\n"
            f"  字段一旦出现就必须是这两个字符串之一（写 null / \"\" / 数字一律拒，"
            f"同 precision.tolerance_source 的口径）；拿不准就**整字段省略**。")
    return raw


def _case_profile_declared(spec):
    """spec 是否**显式声明**了 `precision.case_profile`（不是「解析出来等于 legacy」）。

    为什么单独要这个信号：`_case_profile` 给未声明的 spec 兜的是 `"legacy"`，光看返回值分不出
    「没写」与「写了 legacy」。而 caseset 的账本键**只在写了的时候才落**——现有样例 spec 一个都没写，
    产物里因此**不许**多出这个键（字节安全的关键，同 `operator_class` 的处理）。
    顺手把词表校验过一遍：单独调用本函数时非法值同样当场炸，不留「只问声明与否就绕过校验」的口子。"""
    _case_profile(spec)
    return "case_profile" in (spec.get("precision") or {})


_TORCH_PARITY_AXIS_CLASSES = ("first_axis", "middle_axis", "last_axis")


def _resolve_axis_class(value, rank, where):
    """cannbot ``scalar_equivalence.values_by_rank`` 的紧凑等价表达。

    first=0、middle=floor((rank-1)/2)、last=rank-1，与本地
    ``case_design.json.coverage.attribute_domains.dim`` 的 rank1..8 表逐项相同。
    普通标量原样返回，故非轴属性仍可与轴 class 组成显式 profile。
    """
    if not isinstance(value, dict) or "axis_class" not in value:
        _check_attr_value(value, where)
        return value
    if set(value) != {"axis_class"} or value["axis_class"] not in _TORCH_PARITY_AXIS_CLASSES:
        raise ValueError(
            f"{where}.axis_class 须为 {list(_TORCH_PARITY_AXIS_CLASSES)}，得 {value!r}")
    cls = value["axis_class"]
    if cls == "first_axis":
        return 0
    if cls == "middle_axis":
        return (rank - 1) // 2
    return rank - 1


def _torch_parity_plan(spec, in_params, dtypes, attrs_default, case_target, cost_fn):
    """按 cannbot 冻结设计的轴模型生成完整笛卡尔矩阵。

    配置位于 ``precision.torch_parity_matrix``，只在
    ``case_profile=torch_parity`` 下消费：

    * ``ranks``：完整 rank 轴；
    * ``shape_profiles``：每档 ``leading_dim``，其余轴补 1；
    * ``attribute_profiles``：显式属性 profile，轴属性可写
      ``{"axis_class":"first_axis|middle_axis|last_axis"}``；
    * ``generator``：当前只接受 cannbot Median 冻结设计使用的 uniform。

    完整矩阵不受 1-wise/case_target 抽样；case_target 必须精确等于矩阵大小，
    防止声明“1152 全覆盖”却静默只取 60 条。
    """
    cfg = (spec.get("precision") or {}).get("torch_parity_matrix")
    if not isinstance(cfg, dict):
        raise ValueError(
            "precision.case_profile='torch_parity' 时必须声明 "
            "precision.torch_parity_matrix（不再沿用 legacy 造例规则）")
    allowed = {"source", "source_sha256", "ranks", "shape_profiles",
               "attribute_profiles", "generator"}
    unknown = set(cfg) - allowed
    if unknown:
        raise ValueError(f"torch_parity_matrix 含未知字段 {sorted(unknown)}")
    source, source_sha = cfg.get("source"), cfg.get("source_sha256")
    if not isinstance(source, str) or not source:
        raise ValueError("torch_parity_matrix.source 须为非空来源说明")
    if not isinstance(source_sha, str) or len(source_sha) != 64:
        raise ValueError("torch_parity_matrix.source_sha256 须为 64 位摘要")
    try:
        int(source_sha, 16)
    except ValueError as ex:
        raise ValueError("torch_parity_matrix.source_sha256 非十六进制摘要") from ex
    ranks = cfg.get("ranks")
    if not isinstance(ranks, list) or not ranks or len(ranks) != len(set(ranks)) \
            or any(isinstance(r, bool) or not isinstance(r, int)
                   or not (1 <= r <= _MAX_RANK) for r in ranks):
        raise ValueError(f"torch_parity_matrix.ranks 须为 1..{_MAX_RANK} 的非空无重复整数列表")
    declared_ranks = _allowed_ranks(in_params)
    if declared_ranks is not None and set(ranks) != set(declared_ranks):
        raise ValueError(
            f"torch_parity_matrix.ranks={ranks} 与 in.rank={sorted(declared_ranks)} 不一致")
    shapes = cfg.get("shape_profiles")
    if not isinstance(shapes, list) or not shapes:
        raise ValueError("torch_parity_matrix.shape_profiles 须为非空列表")
    shape_rows, shape_names = [], set()
    for i, row in enumerate(shapes):
        if not isinstance(row, dict) or set(row) != {"name", "leading_dim"}:
            raise ValueError(
                f"shape_profiles[{i}] 须仅含 name/leading_dim")
        name, leading = row["name"], row["leading_dim"]
        if not isinstance(name, str) or not name or name in shape_names:
            raise ValueError(f"shape_profiles[{i}].name 缺失或重复")
        if isinstance(leading, bool) or not isinstance(leading, int) or leading < 1:
            raise ValueError(f"shape_profiles[{i}].leading_dim 须为正整数")
        shape_names.add(name)
        shape_rows.append((name, leading))
    profiles = cfg.get("attribute_profiles")
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("torch_parity_matrix.attribute_profiles 须为非空列表")
    attr_names = set(attrs_default)
    normalized_profiles = []
    profile_names = set()
    for i, row in enumerate(profiles):
        if not isinstance(row, dict) or set(row) != {"name", "attrs"}:
            raise ValueError(f"attribute_profiles[{i}] 须仅含 name/attrs")
        name, attrs = row["name"], row["attrs"]
        if not isinstance(name, str) or not name or name in profile_names:
            raise ValueError(f"attribute_profiles[{i}].name 缺失或重复")
        if not isinstance(attrs, dict) or set(attrs) != attr_names:
            raise ValueError(
                f"attribute_profiles[{i}].attrs keys={sorted(attrs) if isinstance(attrs, dict) else attrs!r} "
                f"须精确等于 attr 参数 {sorted(attr_names)}")
        profile_names.add(name)
        normalized_profiles.append((name, attrs))
    generator = cfg.get("generator")
    if not isinstance(generator, dict) or set(generator) != {"kind", "min", "max"} \
            or generator.get("kind") != "uniform" \
            or not all(isinstance(generator.get(k), (int, float))
                       and not isinstance(generator.get(k), bool) for k in ("min", "max")) \
            or generator["min"] >= generator["max"]:
        raise ValueError(
            "torch_parity_matrix.generator 当前须为 {kind:'uniform', min:<数>, max:<数>}")

    expected = len(dtypes) * len(ranks) * len(shape_rows) * len(normalized_profiles)
    if int(case_target) != expected:
        raise ValueError(
            f"torch_parity 完整矩阵大小={expected}，precision.case_target={case_target}；"
            "两者必须相等，禁止静默抽样")
    entries = []
    for dtn in dtypes:
        dk = _regular_data_kind(dtn, attrs_default, len(in_params))
        for rank in ranks:
            for shape_name, leading in shape_rows:
                shape = (leading,) + (1,) * (rank - 1)
                for attr_idx, (profile_name, raw_attrs) in enumerate(normalized_profiles):
                    attrs = {
                        key: _resolve_axis_class(
                            value, rank,
                            f"torch_parity_matrix.attribute_profiles[{attr_idx}].attrs.{key}")
                        for key, value in raw_attrs.items()
                    }
                    if cost_fn is not None:
                        cost = cost_fn(
                            shape, attrs,
                            f"torch_parity:{dtn}:rank{rank}:{shape_name}:{profile_name}")
                        if cost > _cost_budget(spec):
                            raise ValueError(
                                f"torch_parity 冻结 shape {shape} 的 golden cost={cost} 超预算；"
                                "完整矩阵禁止静默缩形/剔除")
                    entries.append({
                        "dims": ["功能", "精度", "性能"],
                        "shape": shape,
                        "dtype": dtn,
                        "tags": ["torch_parity", shape_name, profile_name],
                        "data_kind": f"{dk}:uniform",
                        "id_kind": f"tp_r{rank}_{shape_name}_{profile_name}",
                        "attrs": attrs,
                        "attr_idx": attr_idx,
                        "case_origin": (
                            f"torch_parity:{dtn}:rank{rank}:{shape_name}:{profile_name}"),
                        "rule_ref": (
                            "cannbot case_design coverage.regular_axes × "
                            "attribute_profile_matrix（完整笛卡尔）"),
                    })
    return entries, {
        "pool_max": expected,
        "requested_target": expected,
        "emitted": expected,
        "forced_special": 0,
        "operator_class": _operator_class(spec),
        "emits_nonfinite_specials": False,
        "case_profile": "torch_parity",
        "case_profile_declared": True,
        "forced_total": expected,
        "dropped_combo_classes": [],
        "unpaired_combo_classes": {
            "count": 0,
            "classes": [],
            "attr_values_never_emitted": [],
        },
        "attr_axis_lengths": {"declared": [], "emitted": 0, "items": [], "skipped": []},
        "coverage_strength": (
            "complete_cartesian：dtype×rank×shape_profile×attribute_profile 全覆盖"),
        "golden_cost": ({
            "budget": _cost_budget(spec), "model": _COST_MODEL,
            "scaled_cases": [], "skipped_shapes": [], "skipped_shape_classes": 0,
        } if cost_fn is not None else _empty_cost_ledger()),
        "torch_parity_matrix": {
            "source": cfg.get("source"),
            "source_sha256": cfg.get("source_sha256"),
            "ranks": list(ranks),
            "shape_profiles": [dict(row) for row in shapes],
            "attribute_profile_count": len(normalized_profiles),
            "generator": dict(generator),
        },
    }


# ============================ value_profile 受控数值生成（借参考仓 generate_array，op-中立）=========
# 借 `cannbot-ops-input .../common/case_generator.py::generate_array` 的两处**数值生成机制**
# （**只借机制、不搬整个 case_generator**，且据 spec 的 value_profile 计划驱动、绝非按算子名特判）：
#   · special_values：nan/±inf **别名映射** + `np.resize` **循环填充**（原文 aliases + np.resize(values,size)）；
#   · tie：用**小值集循环填充**构造大量重复值 → 归约类算子（median/mode…）命中并列，index 可合法分歧。
# 这是 op-中立的输入构造：它只据 profile 名产受控数值，不知道也不关心是哪个算子。
_SPECIAL_ALIASES = {"nan": np.nan, "+inf": np.inf, "inf": np.inf, "-inf": -np.inf}
_VALUE_PROFILE_KINDS = ("nan", "tie")           # 受控词表（spec.precision.value_profiles 的合法值）
# value_profile 代表 dtype 的**确定性优先序**（审计 finding #8）：原先写死 `float32`，spec 声明了 profile
# 但 dtype 集里没有 float32（如只跑 fp16/bf16）就**静默产零条** value_profile 用例——「声明了覆盖、实际没覆盖」
# 正是本仓最忌的假验收。现在从可用浮点 dtype 里按本序确定性选代表；一个都没有 → fail-closed。
_VP_DTYPE_PREF = ("float32", "float16", "bfloat16")
# tie 的**受控值集**：只有 3 个不同值，而 vp shape 的每一维都 ≥4 → 鸽巢原理保证**任意轴**的任意一条
# 1-D 切片里必有重复值（= 归约类算子必命中并列）。值集大小与最小维长的这个不等式是 tie 成立的依据，
# 改任一边都要重新验（`_assert_tie_per_axis` 会当场把违约逮出来）。
_TIE_VALUES = (-1.0, 0.0, 1.0)
_VP_BASE_DIMS = (4, 6)                          # value_profile 代表 shape 的循环维长（均 > len(_TIE_VALUES)）


def _fill_cyclic(values, shape, cdt):
    """借 generate_array 的 special_values 填充：别名映射（nan/±inf 字符串→np 值）+ `np.resize` 循环铺满 shape。
    op-中立、确定（值序固定，不吃 rng）。"""
    mapped = [(_SPECIAL_ALIASES[v] if isinstance(v, str) and v in _SPECIAL_ALIASES else v) for v in values]
    n = _numel(shape) if shape else 1
    arr = np.resize(np.asarray(mapped, dtype=np.float32), n).reshape(shape)
    return arr.astype(cdt)


def _make_value_profile(rng, shape, dtn, profile):
    """value_profile 输入构造（借 generate_array 的 special_values/tie 机制，op-中立、非某算子特判）：
      · nan：均匀底 + 前 1/4 位置 NaN（既含 NaN 又含常规值 → 归约类可测 NaN 传播、torch_allclose equal_nan 判据）；
      · tie：小值集循环填充 → 大量重复值/并列（median 偶数长度取 lower-middle、index 可合法分歧 → 压 index_value_consistency）。
    bf16 造后 round 到 bf16 网格（返回 fp32-on-grid 逻辑值，同 _make_varied）。"""
    cdt = _compute_np(dtn)
    if profile == "nan":
        if precision_policy.is_integer_dtype(dtn):
            raise ValueError(f"value_profile=nan 不适用于整数 dtype {dtn!r}（整型无 NaN）")
        x = rng.uniform(-5.0, 5.0, size=shape).astype(np.float32)
        f = x.reshape(-1)
        if f.size:
            f[: max(1, f.size // 4)] = np.float32(np.nan)   # 前 1/4 位 NaN（对齐 generate_array special_values 语义）
        x = f.reshape(shape)
        return _bf16_round(x) if dtn == _BF16 else x.astype(cdt)
    if profile == "tie":
        x = _fill_cyclic(list(_TIE_VALUES), shape, np.float32)   # 含负/零/正、值集小 → 每轴每切片必有并列
        x = _bf16_round(x) if dtn == _BF16 else x.astype(cdt)
        _assert_tie_per_axis(x, dtn)                     # 生成后**逐归约轴**验证：真有重复候选，不只是声明
        return x
    raise ValueError(f"未知 value_profile={profile!r}（受控词表 {list(_VALUE_PROFILE_KINDS)}）")


def _assert_tie_per_axis(arr, dtn):
    """tie 用例的**事后核验**（审计 finding #8）：逐归约轴（= 每个轴）检查**每一条** 1-D 切片都含重复值。

    为什么必须核：`_fit_rank` 会给强制项左补 1（如 (4,6)→(1,4,6)），沿 dim=0 归约时每条切片只有 1 个元素、
    **根本不存在并列**——tie 用例就此退化成普通用例，`index_value_consistency` 那条判据一次也没被压到，
    但账面上「tie 覆盖」是绿的。声明覆盖 ≠ 形成覆盖，这里当场对账、不成立就 fail-closed。"""
    a = np.asarray(arr)
    if a.ndim == 0 or a.size == 0:
        raise ValueError(f"value_profile=tie 生成了 {a.shape} 的数组（0 维/空）——构造不出并列，fail-closed")
    for ax in range(a.ndim):
        if a.shape[ax] < 2:
            raise ValueError(f"value_profile=tie 的 shape {a.shape} 在轴 {ax} 长度 {a.shape[ax]}<2 —— "
                             f"该轴归约时每条切片只有 1 个元素、构造不出并列（tie 覆盖名存实亡），fail-closed")
        m = np.moveaxis(a, ax, -1)
        s = np.sort(m, axis=-1)
        has_dup = (np.diff(s, axis=-1) == 0).any(axis=-1)
        if not bool(np.all(has_dup)):
            raise ValueError(f"value_profile=tie（dtype={dtn}）在轴 {ax} 上有切片不含重复值 "
                             f"（shape={a.shape}，{int((~has_dup).sum())} 条切片无并列）—— "
                             f"tie 未真正形成，fail-closed（值集 {list(_TIE_VALUES)} 与该轴长度不匹配？）")


def _pick_vp_dtype(dtypes):
    """从 spec dtype 集里**确定性**选 value_profile 的代表浮点 dtype（按 `_VP_DTYPE_PREF` 序取首个命中）。

    一个浮点 dtype 都没有 → fail-closed：spec 声明了 value_profiles（nan 需浮点、tie 的并列语义也按浮点
    见证），却产不出任何 profile 用例 = 声明的覆盖是假的。宁可停下让人改 spec。"""
    for d in _VP_DTYPE_PREF:
        if d in dtypes:
            return d
    raise ValueError(f"spec 声明了 precision.value_profiles，但 dtype 集 {list(dtypes)} 里没有任何可用浮点 "
                     f"dtype（候选优先序 {list(_VP_DTYPE_PREF)}）→ 一条 value_profile 用例都产不出。"
                     f"声明覆盖却产零条 = 假覆盖，fail-closed（请给 spec 补浮点 dtype 或撤掉 value_profiles）")


def _vp_shape(ranks):
    """value_profile 用例的代表 shape：**每一维都取自 `_VP_BASE_DIMS`（均 ≥4）**，保证任意轴都能形成并列。

    ⚠ 不能用旧写法 `_fit_rank((4,6), ranks)`（审计 finding #8）：它是**左补 1**，rank=3 会得到 (1,4,6)，
    沿 dim=0 归约每条切片只有 1 个元素 → tie 根本不存在。这里改成按目标 rank **循环取基准维长**：
    rank1→(4,)、rank2→(4,6)、rank3→(4,6,4)、rank4→(4,6,4,6)，全部维长 ≥4 > |_TIE_VALUES|。
    目标 rank 的选法与 `_fit_rank` 同规则（离基准 rank2 最近、并列取小），故无 rank 约束时仍是 (4,6)、零变更。"""
    base_rank = len(_VP_BASE_DIMS)
    if ranks is None:
        return tuple(_VP_BASE_DIMS)
    r = min(sorted(ranks), key=lambda x: (abs(x - base_rank), x))
    if r < 1:
        raise ValueError(f"value_profile 无法为 rank={r} 构造 shape（须 ≥1），fail-closed")
    return tuple(_VP_BASE_DIMS[i % len(_VP_BASE_DIMS)] for i in range(r))


def _value_profiles(spec):
    """spec.precision.value_profiles → 受控 profile 列表（去重保序）；缺省 [] = 现行为（不产 value_profile 用例）。
    非列表/含词表外值 → fail-closed（防伪造 profile 冒充覆盖）。

    OC：`operator_class` 声明的类别**不铺 NaN·Inf**（structural / integer_compute）时，
    再声明 `value_profiles: ["nan"]` 是**自相矛盾** → fail-closed，**绝不静默丢掉该 profile**
    （静默 = 账面声明了 NaN 覆盖、实际一条没产 = 假覆盖，本仓最忌）。`tie` 不受影响、所有类别都保留。"""
    raw = (spec.get("precision") or {}).get("value_profiles")
    if raw is None:
        return []
    if not isinstance(raw, list) or any(p not in _VALUE_PROFILE_KINDS for p in raw):
        raise ValueError(f"precision.value_profiles 须为 {list(_VALUE_PROFILE_KINDS)} 的子集列表，得 {raw!r}")
    out = []
    for p in raw:
        if p not in out:
            out.append(p)
    op_class = _operator_class(spec)                     # OC：词表外取值在这里也会当场 fail-closed
    if "nan" in out and not _emits_nonfinite(op_class):
        raise ValueError(
            f"spec.operator_class={op_class!r} 这一类**不适用 nan profile**，但 precision.value_profiles "
            f"里仍写着 'nan'（得 {out!r}）—— 两处自相矛盾，fail-closed。\n"
            f"  依据：参考仓 `Justbin/cannbot-ops-input` 的 design_contract.py:512 只对 "
            f"`floating_compute` 调 `_validate_floating_rules`（= 只有那一类强制 nan/pos_inf/neg_inf/"
            f"mixed_inf）；structural / integer_compute 的特殊值走「极值 / 0·1·-1 / 重复 / 越界索引 / "
            f"广播 / 规约轴 / 饱和」那一档（SKILL.md:252）。\n"
            f"  两条出路，二选一（**别指望引擎静默忽略**）：\n"
            f"    ① 该算子确实要按浮点算术口径验 NaN 传播 → 把 operator_class 改成 'floating_compute'；\n"
            f"    ② 该算子是结构 / 整型类 → 从 precision.value_profiles 里去掉 'nan'（'tie' 可留，"
            f"并列 / 重复值本就属结构类那一档）。")
    return out


def _attr_axis_lengths(spec, attrs_default):
    """spec.`attr_axis_lengths` → 轴维度约束（**定向生成**「某 attr 指向的轴取某长度」的用例）。

    契约（字段驱动、op-中立）::

        "attr_axis_lengths": [{"attr": "dim", "lengths": [1]}]

    语义：把该 attr 的值当作**轴下标**（int 或 int 列表，允许负数），对 `lengths` 里的每个 L 生成
    一条「这些轴的长度 = L」的用例。**为什么需要**：任务书常点名「归约轴上维度为 1」这类边界，
    而 shape 阶梯与 attr 取值在正交网格里是**各自独立**取的——含长度-1 轴的 shape 只会跟排在前面的
    attr combo 撞上，点名的组合可能一条都不出（实测 pdist/median 通路就 0 条且无告警）。

    缺省 `None` → `[]`（现有算子零变更）。结构不对 / 未知 attr / 非正长度一律 fail-closed
    （声明了覆盖却产不出 = 假覆盖，本仓最忌）。长度 0 请走 `allow_empty_tensor` + `empty_axis`，
    这里不收——空张量有它自己的一整套语义（dims 只留「功能」等），不该被轴长度约束顺手造出来。
    """
    raw = spec.get("attr_axis_lengths")
    if raw is None:
        return []
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"attr_axis_lengths 须为非空列表（[{{'attr','lengths'}}]），得 {raw!r}")
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"attr_axis_lengths[{i}] 须为字典，得 {type(item).__name__}")
        name = item.get("attr")
        if name not in attrs_default:
            raise ValueError(f"attr_axis_lengths[{i}].attr={name!r} 不在 spec 的 attr 名集 "
                             f"{sorted(attrs_default)} 里（防伪造覆盖）")
        lengths = item.get("lengths")
        if not isinstance(lengths, list) or not lengths:
            raise ValueError(f"attr_axis_lengths[{i}].lengths 须为非空 int 列表，得 {lengths!r}")
        vals = []
        for L in lengths:
            if isinstance(L, bool) or not isinstance(L, int) or L < 1:
                raise ValueError(f"attr_axis_lengths[{i}].lengths 含 {L!r}——须为 ≥1 的整数"
                                 f"（长度 0 的空张量请用 allow_empty_tensor + empty_axis 表达）")
            if L not in vals:
                vals.append(L)
        out.append({"attr": name, "lengths": vals})
    return out


def _check_axis_length_coverage(ledger):
    """轴长度定向生成的 **fail-closed 判据：逐 `(constraint, length, attr 组合)` 判**（审计 finding #5）。

    原先只看全局 `emitted > 0`：rank 允许 `{2,4}`、attr 含 `dim=0` 与 `dim=3` 时，`dim=0` 产出来了、
    `dim=3` 被静默跳过，总数非零 → 一声不吭。**部分缺失也是缺失**，账本与判据都得逐项算。

    三档语义（据字段判、op-中立）：
      · `not_applicable` —— 该 attr 取值压根不是轴下标（`dim=None` 的全局归约）→ **合法缺席**，不算缺口；
      · `skipped` —— 取值是轴下标、却生成不出来（没有容得下它的合法 rank / numel 超上限）→ **真缺口**，
        多半是 spec 自相矛盾（attr 取值与 in 参数的 rank 约束对不上），fail-closed 交人改；
      · `emitted` —— 真产出来了。
    另：某 `(attr, length)` 下**一个 applicable 都没有** → 声明的覆盖不可能兑现，同样 fail-closed。"""
    groups = {}
    for it in ledger["items"]:
        groups.setdefault((it["constraint_idx"], it["attr"], it["length"]), []).append(it)
    for (_ci, attr, length), items in sorted(groups.items()):
        applicable = [it for it in items if it["status"] != "not_applicable"]
        emitted = [it for it in items if it["status"] == "emitted"]
        skipped = [it for it in items if it["status"] == "skipped"]
        if not applicable:
            raise ValueError(
                f"attr_axis_lengths 声明了「{attr} 指的轴长度={length}」，但**没有任何 attr 取值是轴下标**"
                f"（{[it['attr_value'] for it in items]}）→ 一条都产不出。"
                f"声明覆盖却产零条 = 假覆盖，fail-closed。请核对该 attr 的取值是否为轴下标")
        if not emitted:
            raise ValueError(
                f"attr_axis_lengths 声明了「{attr} 指的轴长度={length}」，可用的轴取值有 "
                f"{[it['attr_value'] for it in applicable]}，但**一条都没产出来**"
                f"（原因：{sorted({it['reason'] for it in skipped})}）——"
                f"声明覆盖却产零条 = 假覆盖，fail-closed。请核对 in 参数的 rank 约束")
        if skipped:
            raise ValueError(
                f"attr_axis_lengths 的「{attr} 指的轴长度={length}」**部分轴取值产不出**："
                f"{[(it['attr_value'], it['reason']) for it in skipped]}（已产出的："
                f"{[it['attr_value'] for it in emitted]}）。"
                f"部分缺失也是缺失——只看总数非零就放行，正是「账本说覆盖了、其实漏了一半」的假覆盖，"
                f"fail-closed。请核对这些 attr 取值与 in 参数的 rank 约束是否自洽")


def _axis_indices(value):
    """attr 值 → 轴下标列表；不是轴下标（None / bool / 非 int）→ None（该变体自然缺席，不报错）。

    `None` 是合法的「该 attr 省略」语义（如 median 的 `dim=None` = 全局归约，压根没有「那根轴」），
    故这里返回 None 让调用方跳过——**不是错误**，别为此 fail-closed。

    ⚠ 也收 `tuple`：零配对告警那条通路拿到的是 `_attr_hashable` 转过的可哈希值（list→tuple），
    不收就会把多轴 attr 误判成「非轴型」、退回旧的结构类口径（正是审计 finding #8 的漏报面）。
    spec 侧的值一律是 list（`_check_attr_value` 只放行 `list[int]`），故对生成路径零影响。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return [value]
    if isinstance(value, (list, tuple)) and value and all(
            isinstance(v, int) and not isinstance(v, bool) for v in value):
        return list(value)
    return None


def _norm_axes(axes, rank):
    """轴下标列表 → **归一化轴号**（负数 +rank）；任一越界 / rank<1 / **归一化后有重复** → None。

    归一化是「轴型 attr」一切判定的地基：`dim=-1` 在 rank2 与 rank3 下指的根本不是同一根轴，
    不归一化就没法说「这个 attr 取值有没有跟『被指轴长度=1』配过对」。

    ⚠ **重复即判非轴集合**（`(2,2)` / rank2 下的 `[0,-2]`）：同一根轴不可能被指两次，这类值
    十有八九根本不是轴（`stride=[2,2]` / `kernel_size=[3,3]` 这种几何参数）。据结构判、不看字段名——
    既压住零配对告警的误判噪声，也让「拿这种 attr 去声明 attr_axis_lengths」走 fail-closed 而非产废用例。"""
    if rank < 1:
        return None
    out = []
    for ax in axes:
        idx = ax + rank if ax < 0 else ax
        if not (0 <= idx < rank) or idx in out:
            return None
        out.append(idx)
    return out


def _min_rank_for_axes(axes):
    """容得下这组轴下标的**最小 rank**：正下标 `ax` 要 rank ≥ ax+1，负下标 `ax` 要 rank ≥ -ax。"""
    return max((ax + 1) if ax >= 0 else (-ax) for ax in axes)


def _pick_axis_dtype(dtypes):
    """轴长度用例的代表 dtype：按 `KEY_DTYPES` 序取首个命中，都不在则取 dtype 集首个（确定性）。"""
    for d in KEY_DTYPES:
        if d in dtypes:
            return d
    return dtypes[0]


def _axis_base_shape(ranks, axes):
    """轴长度用例的基准 shape：**按这组轴下标挑一个容得下它们的合法 rank**，每维取 `_VP_BASE_DIMS`（均 ≥4）。

    ⚠ 为什么不能像原先那样全局只选一个基准 rank（审计 finding #5）：rank 允许 `{2,4}`、attr 含 `dim=0`
    与 `dim=3` 时，只按「离 rank2 最近」选出 rank2 → `dim=3` 被当越界跳过，**可生成的轴变体静默缺失**，
    而全局 `emitted>0` 又让 fail-closed 判据看不出来（覆盖缺口没人知道）。改成**逐轴值**挑 rank：
    先滤掉容不下它的 rank，再在余下的里按同一确定性规则（离基准 rank2 最近、并列取小）选。

    `ranks=None`（无 rank 约束）→ 取 `max(需要的最小 rank, len(_VP_BASE_DIMS))`——轴值容得下时与旧行为同。
    一个合法 rank 都没有（含超 `_MAX_RANK`）→ None：调用方记账并按**逐项**判据 fail-closed。"""
    need = _min_rank_for_axes(axes)
    base_rank = len(_VP_BASE_DIMS)
    if ranks is None:
        r = max(need, base_rank)
    else:
        ok = [x for x in sorted(ranks) if x >= need]
        if not ok:
            return None
        r = min(ok, key=lambda x: (abs(x - base_rank), x))
    if r > _MAX_RANK:
        return None
    return tuple(_VP_BASE_DIMS[i % len(_VP_BASE_DIMS)] for i in range(r))


def _axis_length_shape(base, axes, length):
    """把 `base` shape 里 `axes` 指的轴长度改成 `length` → 新 shape；轴越界 → None（调用方跳过并记账）。"""
    dims = [int(d) for d in base]
    rank = len(dims)
    for ax in axes:
        idx = ax + rank if ax < 0 else ax
        if not (0 <= idx < rank):
            return None
        dims[idx] = int(length)
    if _numel(dims) > _MAX_NUMEL:
        return None
    return tuple(dims)


def generatable_dtypes():
    """**生成层**能造的 dtype 名集合（`_NATIVE` + bf16）——gen_cases 侧的单一真源，供门与报错点名用。"""
    return sorted(set(_NATIVE) | {_BF16})


def _dtype_layer_error(dtn, form, gen_ok, run_ok, runner_set, deferred_set):
    """dtype 不过能力门时的报错——**两层各自的支持集都点名**，不让用户猜是哪一层挡的（U3）。"""
    lines = [f"unsupported dtype {dtn!r}（runner_form={form!r}）——用例生成要求**生成层 × 真机层都支持**，"
             f"缺哪层见下：",
             f"  · 生成层 gen_cases（造输入/算 golden/落盘读回）："
             f"{'支持' if gen_ok else '**不支持**'}；可造 = {generatable_dtypes()}",
             f"  · 真机层 runner（repo_adapter，单一真源）：{'支持' if run_ok else '**不支持**'}；"
             f"{form} 可收发 = {sorted(runner_set)}"
             + (f"；另 Track-C 挂账（生成期放行、真机跑到仍拒）= {sorted(deferred_set)}" if deferred_set else "")]
    if gen_ok and not run_ok:
        lines.append(f"  → 该 dtype 造得出、但 {form} runner 收发不了：换支持它的 runner_form，"
                     f"或先给 runner 补该 dtype 分支（补完把它加进 repo_adapter 的对应表）。")
    elif run_ok and not gen_ok:
        lines.append(f"  → 真机收得了、但 gen_cases 造不出（随机/边界/特殊值生成与 golden 落盘未覆盖该 dtype）："
                     f"须先扩 gen_cases 的 `_NATIVE` 与输入构造，不静默降级。")
    lines.append("  fail-closed —— 绝不静默跳过该 dtype（跳过 = 声明覆盖却没覆盖，假验收）。")
    return "\n".join(lines)


def check_spec_capability(in_params, runner_form=None):
    """引擎**能力边界**的 spec 级预检——`gen_cases()` 与 `_dry_run()` 共用，故 CP-B 契约自检就能拦住。

    ⚠ dtype 白名单**不再自带一份硬表**（U3）：真机侧一律问 `repo_adapter.supported_np(runner_form)`
    （+ `deferred_np` 的 Track-C 挂账集），生成侧问本模块的 `_NATIVE`——**两处口径不一致**曾把任务书
    8 类 dtype 压到 4/8（int64/int8/uint8 被生成端挡掉，覆盖率是被工具而非算子限住的）。
    `runner_form` 缺省 `cpp` = 现有 4 个算子的口径，行为逐字节不变。

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
    # dtype 白名单（fail-fast，不静默）——**双层单一真源**：生成层 `_NATIVE`+bf16 × 真机层 repo_adapter。
    import repo_adapter                                # 延迟 import：repo_adapter 顶层已 import gen_cases
    form = runner_form or "cpp"
    runner_set = repo_adapter.supported_np(form)       # 未知 form 在此 fail-closed（不兜 cpp）
    deferred_set = repo_adapter.deferred_np(form)
    gen_set = set(_NATIVE) | {_BF16}
    for dtn in dtypes:
        gen_ok = dtn in gen_set
        run_ok = dtn in runner_set or dtn in deferred_set
        if not (gen_ok and run_ok):
            raise ValueError(_dtype_layer_error(dtn, form, gen_ok, run_ok, runner_set, deferred_set))


def _build_inputs(rng, in_params, shp, dtn, attrs, data_kind, runner_form=None):
    """造该 case 的**逻辑**输入数组列表（compute dtype；bf16=fp32-on-grid）。物理化在保存步单独做。
    data_kind 形如 base 或 base:regime（regime∈{uniform,normal}，仅 varied/pair 系用）；
    特殊 base：empty(§1.4 空)/inf/ninf/nan(§1.4 特殊值)。
    `runner_form` 只为把兜底预检校在**同一层口径**上——不传就按 cpp 校，aclnn_py 的 int64 会在这被误拒。"""
    arity = len(in_params)
    base = data_kind.split(":")[0]
    regime = data_kind.split(":")[1] if ":" in data_kind else "uniform"
    check_spec_capability(in_params, runner_form)         # 兜底：正式路径也再校一次（dry-run 已前置校过）
    if base == "empty":                                  # §1.4 空 Tensor（numel=0）：按 shape 造空数组
        cdt = _compute_np(dtn)
        z = np.zeros(shp, dtype=cdt)
        return [z for _ in range(max(1, arity))]
    if base in ("inf", "ninf", "nan"):                   # §1.4 特殊值遍历
        return _build_value_special(rng, arity, shp, dtn, base)
    if base in ("vpnan", "vptie"):                       # value_profile（借 generate_array 的 special_values/tie 机制）
        profile = "nan" if base == "vpnan" else "tie"
        return [_make_value_profile(rng, shp, dtn, profile) for _ in range(max(1, arity))]
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


# ── shape 的**结构类**（零配对告警用；按结构、不按具体尺寸，故类数少、告警才读得动）──────
SHAPE_CLASS_BCAST = "bcast"          # 广播哨兵
SHAPE_CLASS_EMPTY = "empty"          # 含 0 长度轴（numel=0）
SHAPE_CLASS_ALL_UNIT = "all_unit"    # 每一轴长度都是 1（标量类）——「归约轴长度=1」这类边界落这
SHAPE_CLASS_HAS_UNIT = "has_unit_axis"   # 含长度 1 的轴、但不全是
SHAPE_CLASS_LARGE = "large"          # numel ≥ 大 shape 门槛（perf 有意义）
SHAPE_CLASS_REGULAR = "regular"
_LARGE_NUMEL = 2 ** 16               # 与 `_LARGE_SHAPES`（2^20 / 2^16-1）同量级的门槛


def _shape_class(shp):
    """shape → 结构类（见 `SHAPE_CLASS_*`）。**纯按结构判**，不看算子、不看具体尺寸。

    为什么不用 `_shape_tag` 当类：tag 是具体尺寸（`4x4` / `2x3x4` …），十几个 tag × 几个 attr 取值
    会报出上百条「从未配对」——噪声淹掉真信号。结构类只有 6 个，「`dim=0` 从没配过全 1 轴的 shape」
    这种任务书点名的边界才浮得出来。"""
    if shp == "broadcast":
        return SHAPE_CLASS_BCAST
    dims = [int(d) for d in shp]
    if not dims or any(d == 0 for d in dims):
        return SHAPE_CLASS_EMPTY
    if all(d == 1 for d in dims):
        return SHAPE_CLASS_ALL_UNIT
    if any(d == 1 for d in dims):
        return SHAPE_CLASS_HAS_UNIT
    if _numel(dims) >= _LARGE_NUMEL:
        return SHAPE_CLASS_LARGE
    return SHAPE_CLASS_REGULAR


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


def _shrink_to_budget(shape, attrs, cost_fn, budget, where, lock=None):
    """把**强制**用例的 shape 逐维减半到进预算，返回 `(新shape, 新cost)`。确定性：每步砍当前最大维、
    并列取最左；**保 rank**（不删维，免与 C3 的 rank 约束打架）。

    为什么强制项只能降规模、不能像常规网格那样剔掉：§1.4 特殊场景与白名单大 shape 是「必覆盖」项，
    剔掉 = 覆盖悄悄缩水。降下来的规模会照常进 case_id / caseset / 覆盖账本（可见、可审计，**不是**静默降级）。
    减到各维皆 1 仍超预算 → fail-closed（不硬塞一条算不完的用例）。

    ⚠ `lock`（`{"attr","axes"(归一化轴号),"length"}`，审计 finding #4）：**被轴长度约束锁住的那几根轴
    一律不许动**。原先不锁：声明「轴长 100」的强制项被降成 `(4,3)`，`id_kind` / `case_origin` 却仍宣称
    覆盖长度 100 —— 账本与 case ID 声称覆盖了任务书点名的边界、实际输入根本没覆盖 = **假覆盖**
    （比压根没有这套定向生成更糟：它让人以为缺口已补上）。锁住后其余维降到底仍超预算 →
    fail-closed 明说「轴长度约束与 cost 预算冲突」，绝不静默降规模。"""
    if shape == "broadcast":                             # 哨兵形状固定微小，理论上到不了这里
        raise ValueError(f"{where}: broadcast 哨兵形状的 golden 开销超预算 {budget}，无维可降 → fail-closed")
    cur = [int(d) for d in shape]
    if not cur:
        raise ValueError(f"{where}: 0 维 shape 的 golden 开销超预算 {budget}，无维可降 → fail-closed")
    locked = set(lock["axes"]) if lock else set()
    for _ in range(_COST_SHRINK_MAX_STEPS):
        cand = [k for k in range(len(cur)) if k not in locked and cur[k] > 1]
        if not cand:                                     # 可降的维都到 1 了（锁住的维不算），降无可降
            break
        i = max(cand, key=lambda k: (cur[k], -k))
        cur[i] //= 2
        c = cost_fn(tuple(cur), attrs, where)
        if c <= budget:
            return tuple(cur), c
    if lock:
        raise ValueError(
            f"{where}: **轴长度约束与 golden 生成期规模预算冲突**——约束「{lock['attr']} 指的轴 "
            f"{lock['axes']} 长度={lock['length']}」的维已锁定不许降，其余维降到 {tuple(cur)}"
            f"（cost={cost_fn(tuple(cur), attrs, where)}）仍超预算 {budget} → fail-closed。"
            f"若在这里降规模，这条 case 的 id_kind / case_origin 会继续宣称覆盖了长度 "
            f"{lock['length']}、实际输入却没有 = **假覆盖**。"
            f"请调 spec 的 precision.golden_cost_budget，或改小/撤掉该 attr_axis_lengths 声明")
    raise ValueError(
        f"{where}: 输入形状 {tuple(int(d) for d in shape)} 的 golden 生成期开销超预算 {budget}，"
        f"且逐维减半到 {tuple(cur)}（cost={cost_fn(tuple(cur), attrs, where)}）仍超预算——"
        f"本条是**强制**覆盖项、不能丢，故 fail-closed。"
        f"请调 spec 的 precision.golden_cost_budget，或核 golden.py 的 out_shape() 是否合理")


def _verify_axis_locks(entries, where_hint=""):
    """预算处理**完成后**逐条重新校验轴长度 case 的**实际轴长**与声明一致（审计 finding #4 的第二道）。

    第一道（`_shrink_to_budget` 锁定约束维）是「不去改」，这道是「改没改都验一遍」：将来任何新的
    规模 / 形状后处理若忘了尊重锁，这里当场炸——而不是让 `id_kind` / `case_origin` 继续宣称覆盖了一个
    实际没跑的轴长度。**声称覆盖 ≠ 真覆盖** 是本仓最忌的错，值得多花这一遍 O(n) 的校验。"""
    for e in entries:
        lock = e.get("axis_lock")
        if not lock:
            continue
        shp = e["shape"]
        dims = None if shp == "broadcast" else [int(d) for d in shp]
        bad = dims is None or any(not (0 <= ax < len(dims)) or dims[ax] != int(lock["length"])
                                  for ax in lock["axes"])
        if bad:
            raise ValueError(
                f"{where_hint}轴长度覆盖身份与实际输入不符（假覆盖）："
                f"case({e['dtype']}·{_shape_tag(shp)}·{e['id_kind']}) 宣称「{lock['attr']} 指的轴 "
                f"{lock['axes']} 长度={lock['length']}」，实际 shape={shp}。"
                f"该 case 的 id / 覆盖账本会冒充覆盖任务书点名的边界 → fail-closed")


def _apply_cost_budget(forced, grid, cost_fn, budget):
    """G4：按生成期规模预算处理 plan entries。返回 `(保留的 grid, 覆盖账本)`；`forced` **就地**降规模。

    强制项 → 降规模 + 三处留痕（账本 / `entry["cost_scaled"]`（后续写进 case 的 expected）/ tag「降规模」）。
    常规网格项 → 超预算就剔出采样池，并按 (dtype, shape) 归类记账（**不**冒充已覆盖）。
    网格被剔空 → fail-closed（只剩强制项 = 「用例数虚高但没有一条常规覆盖」的假验收，同 `_shape_ladder`）。

    ⚠ 带 `axis_lock` 的强制项（轴长度约束，finding #4）：**约束的那根轴在降规模中锁定**，
    锁后仍超预算 → fail-closed（详见 `_shrink_to_budget`）。降完还要过 `_verify_axis_locks` 复验。"""
    scaled, skipped, kept, seen_skip = [], [], [], set()
    for e in forced:
        where = f'{e["dtype"]}·{_shape_tag(e["shape"])}·{e["id_kind"]}'
        c0 = cost_fn(e["shape"], e["attrs"], where)
        if c0 <= budget:
            continue
        new_shp, c1 = _shrink_to_budget(e["shape"], e["attrs"], cost_fn, budget, where,
                                        lock=e.get("axis_lock"))
        rec = {"case_origin": e["case_origin"], "id_kind": e["id_kind"], "dtype": e["dtype"],
               "requested_shape": list(e["shape"]), "requested_cost": int(c0),
               "emitted_shape": list(new_shp), "emitted_cost": int(c1),
               "emitted_numel": _numel(new_shp), "budget": int(budget),
               "reason": "golden 生成期规模超预算 → 强制覆盖项**显式降规模**（非静默跳过）；"
                         "该 case 只覆盖了降下来的这个规模，原目标规模**未跑**"}
        if "性能" in e["dims"]:
            rec["perf_note"] = (f"该 case 带「性能」维度：降规模后 numel={_numel(new_shp)}，性能结论只对这个"
                                f"规模成立；下游仍须对该实际规模采集双边性能数据，"
                                f"不得读成「原规模已测且达标」")
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
    但 mock 通路不造 manifest，只在那边拦就成了「本机跑得过、上真机才炸」。宁可在造用例时就停。

    ⚠ **`None` 合法**（2026-07-24 加）：表示「该 attr 省略/取算子的缺省语义」——如 median 的 `dim=None` 即
    **全局归约**（单输出，vs by-dim 双输出）。这是**据字段**表达可选 attr、op-中立（golden.py/out_shape 自行按
    `attrs.get(name) is None` 分派），非按算子名。现有 4 算子的 attr_matrix 不含 None → 行为零变更。"""
    if v is None:
        return
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


def _snapshot_sha(contract):
    """从契约块取规范化的快照 sha（供 validator 对账 spec.golden）。taskdoc_snapshot 非 dict → None；
    SHA `strip().lower()` 对齐 `precision_policy.verify_authorization` 与 `validator._norm_sha` 的口径。"""
    snap = contract.get("taskdoc_snapshot") if isinstance(contract, dict) else None
    v = snap.get("sha256") if isinstance(snap, dict) else None
    return v.strip().lower() if isinstance(v, str) else None


def _derive_tier(op, contract):
    """据 golden 契约块派生档位，返回可直接写进 caseset 的 dict（无契约 → None）。

    两步分开（沿用批 1 的设计）：`verify_authorization` 读**快照文件**核引文真伪 →
    `derive_golden_tier` 只按词表判档。混在一起就成了「spec 自己核自己」的循环自证。

    ⚠ **记录不阻断**（批 2 的边界）：tier 4 也照常返回、照常产用例，只是把
    `blocked_reason` 如实写进每条 case。阻断归批 5 的门。
    这么切的理由：档位是**结论的一部分**，得先让它可见、可审；先阻断会让
    「快照还没入库」这种真问题以「算子跑不了」的面目出现，反而更难查。"""
    if contract is None:
        return None
    import repo_adapter                                # 延迟 import：避加载期循环
    snap = repo_adapter.taskdoc_snapshot_path(op)
    ok, why = precision_policy.verify_authorization(contract, snap)
    tier, needs_human, blocked = precision_policy.derive_golden_tier(contract, ok)
    return {"tier": tier, "requires_human_review": bool(needs_human),
            "blocked_reason": blocked,
            "authorization_verified": bool(ok),
            "authorization_note": why,          # 核不过时的准确原因，别只留一个 False
            "source": contract.get("source"), "method_kind": contract.get("method_kind"),
            "authorization_kind": (contract.get("authorization") or {}).get("kind"),
            # 批 4：快照指纹随 case 走，供 validator 对账 spec 的判据锚（硬约束 #5）——
            # 「核的是哪份快照」被钉死到 spec.golden.taskdoc_snapshot.sha256，改 caseset 一行绕不过去。
            # ⚠ taskdoc_snapshot 可能非 dict（validate_golden_contract 对 impl_reference/none 不约束它，
            #   codex 审 Medium #7）→ 类型守护；SHA 规范化（strip+lower）对齐 validator/verify_authorization 的对账口径。
            "snapshot_sha": _snapshot_sha(contract)}


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
                     empty_axis=None, empty_accepts=None, emit_nonfinite=True):
    """§1.4 特殊场景（不与常规正交、强制纳入）：空(功能only)/标量[1]/边界下(全1)/边界上(大)/inf/-inf/nan。
    每项 (dims, shape, data_kind, id_kind)。整型 dtype 跳过 inf/nan（无此值）。
    C3：每个基准 shape 过 `_fit_rank`——ranks=None 时恒等（现行为），有约束时保 numel 调到合法维度。

    OC · `emit_nonfinite=False`（spec 的 `operator_class` ∈ {structural, integer_compute}）时
    **不产 inf/-inf/nan 三条**——结构 / 整型类算子按参考仓方法学根本不该被喂 NaN·Inf
    （依据见模块 docstring；实证：median PR6429 的 6 条 fail 全是 NaN 用例，合格 PR 被判挂）。
    ⚠ 只砍这三条：`empty` / `scalar` / `bndlo` / `bndhi`（空 / 标量 / 上下边界）**所有类别一律保留**——
    参考仓给结构 / 整型类列的正是「极值、0/1/-1、重复、越界索引、规约轴、饱和」这一档，边界属其中。
    ⚠ 缺省 `True` = **现行为不变**（未声明 operator_class 的 4 个既有算子逐字节不动）。

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
    # 标量 Tensor [1]：仍带性能维，须真实采集并比较。
    E.append((["功能", "精度", "性能"], _fit_rank((1,), ranks), "varied", "scalar"))
    # 边界：下=各维均 1；上=大 shape 某维取大
    E.append((["功能", "精度", "性能"], _fit_rank((1, 1, 1), ranks), "varied", "bndlo"))
    E.append((["功能", "精度", "性能"], _fit_rank(_LARGE_SHAPES[0], ranks), "varied", "bndhi"))
    # INF/-INF/NAN 遍历（仅浮点**且该算子类别适用**；每种值一条，shape 用中等 (16,)）——**带「性能」**
    # （v2：非空皆带性能/同输入；小 numel 同样真实采集，不自动免测）。
    # OC：`emit_nonfinite=False` = spec 声明了 structural / integer_compute → 整段不产（见本函数 docstring）。
    if is_float and emit_nonfinite:
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


_UNPAIRED_CAP = 50


def _entry_dims(shp):
    """entry 的 shape → 维度列表；广播哨兵 → None（不可按轴索引）。"""
    return None if shp == "broadcast" else [int(d) for d in shp]


def _axis_len_class(n):
    """**被指轴长度**的档：`0`(空轴) / `1`(单位轴) / `>1`。

    用档不用原值：原值（4/6/16/255…）会把告警炸成上百条噪声、真信号被淹；而任务书点名的边界
    （「归约轴上维度为 1」）恰恰就落在「1」这一档，分档不丢它。"""
    n = int(n)
    return "0" if n == 0 else ("1" if n == 1 else ">1")


def _axis_class_label(norm, prof, rank):
    """轴型 attr 的配对类标签：`rank{r}·轴{归一化轴号}·被指轴长度={档}`。"""
    return (f"rank{rank}·轴{','.join(str(a) for a in norm)}"
            f"·被指轴长度={','.join(prof)}")


def _axis_like_attrs(entries):
    """哪些 attr 是**轴型**（值当轴下标解）——据**值的结构**判，不按算子名、不按字段名（泛化优先）。

    判据：该 attr 至少有一个非 None 取值，且**每个**非 None 取值都能解成轴下标（int / int 列表），
    且每个取值在**实际出现过的某个 rank** 下都不越界。`keepdim`(bool) / `rtol`(float) 这类自然落选。

    ⚠ 诚实边界：`kernel_size=[3,3]` 这种「恰好是合法下标」的非轴数组会被误判成轴型。误判的后果只是
    配对口径变细（告警条数变多），**不会**把没覆盖的说成已覆盖——漏报才是危险方向，这里刻意偏保守。"""
    ranks, cand = set(), {}
    for e in entries:
        dims = _entry_dims(e["shape"])
        if dims:
            ranks.add(len(dims))
        for name, value in (e.get("attrs") or {}).items():
            cand.setdefault(name, set()).add(_attr_hashable(value))
    out = set()
    for name, vals in cand.items():
        axis_vals = [_axis_indices(v) for v in vals if v is not None]
        if not axis_vals or any(a is None for a in axis_vals):
            continue
        if all(any(_norm_axes(a, r) is not None for r in ranks) for a in axis_vals):
            out.add(name)
    return out


def _unpaired_combo_classes(entries, attr_sets):
    """**零配对告警**：哪些「attr 取值 × 形状类」在最终用例集里**从未同时出现**。

    为什么要有：`dropped_combo_classes` 只报「被采样丢掉的 dtype×shape 类」，报不出「某 attr 取值
    从没跟某类 shape 撞上」。实测教训——任务书点名要「归约轴维度为 1」，而含长度-1 轴的 shape
    （`[1]`/`[1,1,1]`）全被特殊场景生成、只配 `attr_combos[0]`（如 `dim=None`），于是点名场景**实跑 0 条
    且全程无告警**，只能事后人肉核 caseset 才发现。

    口径分两支（审计 finding #8）：
      · **轴型 attr**（`_axis_like_attrs` 据值结构判，如 `dim`）→ 配对键 = 「**归一化轴号 + rank +
        被指轴的实际长度档**」。原先一律按 shape 结构类，两头都错：`shape=(4,1), dim=0` 的归约轴长度
        其实是 4，却因为这条 shape 属于 `has_unit_axis` 就把「dim=0 × 含单位轴」记成**已配对**
        （**漏报**——任务书点名的「归约轴长度=1」被冒充覆盖）；反过来又会拿「该 rank 下已越界的轴值」
        做笛卡尔积，报出**不可实现**的缺口（误报）。归一化轴号 + rank 两头都堵住。
      · **普通非轴 attr** → 沿用 shape 结构类口径（`_shape_class`），口径与行为不变。

    口径（**只报可疑、不报不可能**）：
      · 非轴 attr 的 shape 类取**实际出现过**的那些（没出现过的类不是「配对缺口」，是这个算子压根没这类 shape）；
      · 轴型 attr 的候选类同理只取**池子里真存在**的：某 (rank, 归一化轴, 长度档) 组合，必须真有一条
        用例的形状长这样，才算「本可以配上却没配」；
      · attr 取值同样只算**实际出现过**的；spec 声明了却一条没生成的取值另记
        `attr_values_never_emitted`（那是更严重的一档：取值本身零覆盖）。
    返回 `{"count", "classes"(≤50), "attr_values_never_emitted"}`。**只报不拦**——这里是告警，
    不是门；要不要补，交人/上层门定。"""
    axis_names = _axis_like_attrs(entries)
    shape_classes, paired, seen_values, dims_by_rank = set(), set(), {}, {}
    for e in entries:
        sc = _shape_class(e["shape"])
        shape_classes.add(sc)
        dims = _entry_dims(e["shape"])
        if dims:
            dims_by_rank.setdefault(len(dims), []).append(dims)
        for name, value in (e.get("attrs") or {}).items():
            key = _attr_hashable(value)
            seen_values.setdefault(name, set()).add(key)
            if name not in axis_names:
                paired.add((name, key, sc))
                continue
            axes = _axis_indices(key)
            norm = _norm_axes(axes, len(dims)) if (axes is not None and dims) else None
            if norm is not None:                         # 解不出（None / 越界 / 广播）→ 这条不构成轴向配对
                paired.add((name, key, _axis_class_label(
                    norm, tuple(_axis_len_class(dims[i]) for i in norm), len(dims))))
    unpaired = []
    for name, _vals in attr_sets:
        for key in sorted(seen_values.get(name, ()), key=repr):
            if name not in axis_names:
                for sc in sorted(shape_classes):
                    if (name, key, sc) not in paired:
                        unpaired.append(f"{name}={key!r} × shape类={sc}")
                continue
            axes = _axis_indices(key)
            if axes is None:
                continue                                 # 全局语义（如 dim=None）没有「那根轴」，不是缺口
            for rank in sorted(dims_by_rank):
                norm = _norm_axes(axes, rank)
                if norm is None:
                    continue                             # 该 rank 下越界 → 不可实现，绝不当缺口报（误报源）
                profiles = {tuple(_axis_len_class(d[i]) for i in norm) for d in dims_by_rank[rank]}
                for prof in sorted(profiles):
                    cls = _axis_class_label(norm, prof, rank)
                    if (name, key, cls) not in paired:
                        unpaired.append(f"{name}={key!r} × {cls}")
    never = []
    for name, vals in attr_sets:
        for v in vals:
            if _attr_hashable(v) not in seen_values.get(name, ()):
                never.append(f"{name}={v!r}")
    return {"count": len(unpaired), "classes": sorted(unpaired)[:_UNPAIRED_CAP],
            "attr_values_never_emitted": never}


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
    → **完全不行使**，行为与 G4 之前逐字节一致，且账本里 model 标「未核」而非谎称已核。
    CP：`spec.precision.case_profile` 在此**读一次**（词表外取值当场 fail-closed）并落进
    `meta["case_profile"]` / `meta["case_profile_declared"]`；`torch_parity` 进入完整矩阵，
    `legacy` 保持原有 forced + 1-wise 行为。"""
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

    # OC：算子类别 → 特殊值口径（受控词表；未声明=None=现行为）。词表外取值在此当场 fail-closed。
    op_class = _operator_class(spec)
    emit_nonfinite = _emits_nonfinite(op_class)
    # CP：造例档位（受控词表；未声明 = legacy = 现行为）。torch_parity 走完整矩阵，
    # legacy 侧字节不动。
    # 在这里读（而不是各处现用现读）是为了「一次解析、一处 fail-closed」：词表外取值在此当场炸，
    # gen_cases 与 _dry_run 两条路径都经过 `_plan`，非法档位没有绕过口。
    case_profile = _case_profile(spec)
    case_profile_declared = _case_profile_declared(spec)
    if case_profile == "torch_parity":
        return _torch_parity_plan(
            spec, in_params, dtypes, attrs_default, case_target, cost_fn)
    forced, grid = [], []
    # ① §1.4 特殊场景（每 dtype 强制；id_kind 独立命名空间，评审 #8）
    for dtn in dtypes:
        is_float = not precision_policy.is_integer_dtype(dtn)
        for dims, shp, dk, ik in _special_entries(op, dtn, arity, is_float, attr_combos[0], ranks,
                                                  allow_empty=_allow_empty_tensor(spec),
                                                  empty_axis=_empty_axis(spec),
                                                  empty_accepts=empty_accepts,
                                                  emit_nonfinite=emit_nonfinite):
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
    # ②' value_profile 强制项（据 spec.precision.value_profiles 驱动、op-中立）：借 generate_array 的
    #     special_values/tie 机制在**代表 dtype × 全部 attr 取值**上各产一条——iterate attr_combos 使
    #     全局(dim=None,单输出) 与 by-dim(双输出) 都被覆盖，by-dim 的 tie 恰压 index_value_consistency。
    #     代表 dtype **从可用浮点里确定性选**（`_pick_vp_dtype`，一个都没有→fail-closed，不静默产零条）；
    #     shape 用 `_vp_shape(ranks)`（每维 ≥4，不走会左补 1 的 `_fit_rank`，否则 tie 名存实亡）。
    #     现有 4 算子不声明 value_profiles → 整段不执行、零变更（向后兼容硬约束）。
    vprofiles = _value_profiles(spec)
    if vprofiles:
        vp_dtype = _pick_vp_dtype(dtypes)
        vp_shp = _vp_shape(ranks)
        for profile in vprofiles:
            for attrs in attr_combos:
                ai = combo_idx[_akey(attrs)]
                forced.append(mk(["功能", "精度"], vp_shp, vp_dtype, f"vp{profile}",
                                 f"vp{profile}", attrs, f"value_profile:{profile}:a{ai}",
                                 "torch-baseline §3.② value_profile（nan/tie 数值生成·op-中立·借 generate_array）",
                                 ["value_profile"]))
    # ②'' 轴维度约束强制项（据 spec.attr_axis_lengths 驱动、op-中立）：把「某 attr 指向的轴取长度 L」
    #     **定向生成**出来，而不是指望 shape 阶梯与 attr 取值在正交网格里恰好撞上（实测撞不上：
    #     含长度-1 轴的 shape 全由特殊场景产、只配 attr_combos[0]，任务书点名的「归约轴长度=1」实跑 0 条）。
    #     attr 值当轴下标解（int / list[int]，允许负）；值是 None（如 median 的全局归约）→ 该变体没有
    #     「那根轴」，自然跳过、**不是错误**。现有 4 算子不声明本字段 → 整段不执行、caseset 零变更。
    #     ⚠ 基准 shape **逐（轴值）挑 rank**（`_axis_base_shape`，finding #5）：原先全局只挑一个基准
    #     rank，rank 允许 {2,4} 而 attr 含 dim=3 时，dim=3 被当越界静默跳过、又因全局 emitted>0 不报错。
    axis_constraints = _attr_axis_lengths(spec, attrs_default)
    axis_ledger = {"declared": axis_constraints, "emitted": 0, "items": [], "skipped": []}
    if axis_constraints:
        ax_dtype = _pick_axis_dtype(dtypes)
        ax_dk = _regular_data_kind(ax_dtype, attrs_default, arity)
        for ci, constraint in enumerate(axis_constraints):
            for length in constraint["lengths"]:
                for attrs in attr_combos:
                    ai = combo_idx[_akey(attrs)]
                    val = attrs.get(constraint["attr"])
                    item = {"constraint_idx": ci, "attr": constraint["attr"], "length": int(length),
                            "attr_idx": ai,
                            "attr_value": list(val) if isinstance(val, list) else val}
                    axis_ledger["items"].append(item)
                    axes = _axis_indices(val)
                    if axes is None:                     # 该变体没有这根轴（如 dim=None 的全局归约）
                        item["status"] = "not_applicable"
                        item["reason"] = ("该 attr 取值不是轴下标（如 dim=None 的全局归约）→ "
                                          "压根没有『那根轴』，自然缺席、不是缺口")
                        continue
                    item["axes"] = list(axes)
                    ax_base = _axis_base_shape(ranks, axes)   # 每维 ≥4、且容得下这组轴下标
                    norm = None if ax_base is None else _norm_axes(axes, len(ax_base))
                    shp = None if norm is None else _axis_length_shape(ax_base, axes, length)
                    if shp is None:
                        item["status"] = "skipped"
                        item["base_shape"] = None if ax_base is None else list(ax_base)
                        item["reason"] = (
                            "没有容得下这组轴下标的合法 rank" if ax_base is None else
                            "轴下标归一化后重复（同一根轴被指了两次）→ 不是合法的轴集合"
                            if norm is None else "改后 numel 超上限")
                        axis_ledger["skipped"].append(item)
                        continue
                    item["status"] = "emitted"
                    item["rank"] = len(ax_base)
                    item["shape"] = list(shp)
                    item["norm_axes"] = norm
                    e = mk(["功能", "精度"], shp, ax_dtype, f"{ax_dk}:uniform",
                           f"ax{ci}len{length}", attrs,
                           f"axis_length:{constraint['attr']}:{length}:a{ai}",
                           "任务书点名的轴维度边界（spec.attr_axis_lengths·字段驱动·op-中立）",
                           ["轴长度"])
                    # finding #4：把「这条 case 宣称覆盖的轴长度」钉在 entry 上——后续 cost 降规模
                    # 必须锁住这几根轴，且预算处理完还要复验（`_verify_axis_locks`）。
                    e["axis_lock"] = {"attr": constraint["attr"], "axes": item["norm_axes"],
                                      "length": int(length), "rank": len(ax_base)}
                    forced.append(e)
                    axis_ledger["emitted"] += 1
        _check_axis_length_coverage(axis_ledger)
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
    # finding #4 第二道：预算处理**完成后**逐条复验轴长度 case 的实际轴长与声明一致（改没改都验）。
    # 无条件跑（cost_fn=None 的 dry-run 也跑）——O(n) 而已，换的是「id/账本绝不冒充覆盖」。
    _verify_axis_locks(forced, where_hint="cost 预算处理后复验：")
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
        # OC 账本：这批用例是按哪个算子类别的特殊值口径产的（None=未声明=现行为），以及有没有铺 inf/-inf/nan。
        # 报告侧读这里就能如实说「结构类算子按方法学不喂 NaN·Inf」，不必事后人肉数 case。
        "operator_class": op_class,
        "emits_nonfinite_specials": emit_nonfinite,
        # CP 账本：这批用例按哪个**造例档位**产的，以及 spec 有没有**显式**声明该档位。
        # 两个字段缺一不可：`case_profile` 未声明时兜的是 "legacy"，靠它分不出「没写」与「写了 legacy」，
        # 而 caseset 的账本键只在**写了**的时候才落（老算子产物不许多出一个键，字节安全硬约束）。
        "case_profile": case_profile,
        "case_profile_declared": case_profile_declared,
        "forced_total": len(forced),          # 强制下限 S = 特殊场景 + 白名单（emit 不会少于此；acc-spec 取此作 S）
        "dropped_combo_classes": (_dropped_classes(grid, entries)
                                  if emitted_from_grid < grid_avail else []),
        # 零配对告警：某 attr 取值 × 某 shape 结构类**从未同时出现**（dropped_combo_classes 报不出这个）。
        "unpaired_combo_classes": _unpaired_combo_classes(entries, attr_sets),
        "attr_axis_lengths": axis_ledger,     # 轴维度约束的定向生成账本（未声明则 emitted=0）
        "coverage_strength": ("1-wise+whitelist：特殊场景(§1.4) + key dtype×attr×大shape 全覆盖；"
                              "常规 dtype×shape×值域×attr 联合仅边际 1-wise（50 封顶下 §1.1 100% 正交不可达）"),
        "golden_cost": cost_ledger,           # G4 覆盖账本：降规模的强制项 + 被剔除的超预算 shape
    }
    if int(case_target) < len(forced):                    # 强制下限 > target（评审 #8）：emit>target，note
        meta["note_target_below_forced"] = (f"case_target={case_target} < 强制下限 {len(forced)}"
                                             f"（特殊场景+白名单），实际 emit={len(entries)}")
    return entries, meta


# ============================ 多输出契约扩展（torch 对标 median 见证，op-中立）====================
# 触发**据 spec 字段、绝非按算子名**：out 参数 >1 或任一 out 参数声明 out_role → 走多输出契约；
# 否则（现有 4 算子：单 out 参数、无 out_role）走 legacy 单输出通路、caseset 字节零变更（向后兼容硬约束）。
def _uses_output_contract(spec):
    """是否走多输出契约（`expected.outputs[]`）——**实现已上提到 precision_policy**（唯一真源）。

    造用例侧（本文件）与裁决侧（validator）必须对「这份 spec 是不是多输出」得出同一个答案：
    严重#1 的一半就是「走不走多输出路径由 caseset 自报」。上提后两侧共用同一判据、不可能漂移。"""
    return precision_policy.uses_output_contract(spec)


# ── ACLNN 调用变体（aclnn_py/cpp_extension 共用；据 spec.call_variants 逐 case 解析、op-中立）─────
# 为什么不是「一份 op 级模板」（审计 finding #3）：同一个算子的不同 attr 取值可能对应**不同的 aclnn 符号**
# 与**不同的实参表**（全局归约 vs 按维归约就是两个 API、两种输出 arity）。原先的 op 级模板让 driver 自己把
# `dim=None` 兜成 `dim=0` —— 那既不是「全局」的语义，还可能与单输出签名对不上（越界写 / ABI 崩）。
# 现在改成：**spec 声明式变体表 + gen_cases 逐 case 选中并完全解析**，写进该 case 的 `aclnn_call`；
# driver 直接执行、不再推断。无匹配变体 → fail-closed，**绝不默认**。
# 变体是**字段驱动**的（按 attr 取值判），不是按算子名分派——换任意声明了 call_variants 的算子零改即用。
_ATTR_CTYPE_MAP = {"int64": "int64", "bool": "bool", "float32": "float", "float": "float"}


def _attr_ctype(p):
    """attr 参数的 aclnn 标量 C 类型：int64→"int64"、bool→"bool"、float32/float→"float"。

    ⚠ dtype 候选必须**恰有一个**且在映射表内（审计 finding #5）：原先取 `dt[0]`，`["int64","int8"]` /
    `["float32","bogus"]` 都被静默收下 —— 而 attr 的 C 标量宽度拼错 = 远端 argtypes 错位 = 段错误。
    多候选 / 空 / 未知一律 fail-closed（记 gap 交人裁，别静默挑一个）。"""
    dt = p.get("dtype")
    if isinstance(dt, (list, tuple)):
        cands = list(dt)
        if len(cands) != 1:
            raise ValueError(
                f"aclnn_call: attr {p.get('name')!r} 的 dtype 候选 {cands!r} 不唯一"
                f"（标量 ABI 宽度必须确定，多候选/空一律 fail-closed；请在 spec 收敛成单值或记 gap）")
        dt = cands[0]
    if dt not in _ATTR_CTYPE_MAP:
        raise ValueError(
            f"aclnn_call: attr {p.get('name')!r} 的 dtype {dt!r} 不支持标量映射"
            f"（仅 {sorted(_ATTR_CTYPE_MAP)}），fail-closed（记 gap，别静默塞默认）")
    return _ATTR_CTYPE_MAP[dt]


def _spec_out_names(spec):
    return precision_policy.spec_out_names(spec)


def _spec_attr_names(spec):
    return precision_policy.spec_attr_names(spec)


def _call_variants(spec):
    """读 + 强校 `spec.call_variants`——**实现已上提到 precision_policy.call_variants**（唯一真源）。

    上提理由（严重#1）：「本 case 该有哪些输出」必须由 spec × attrs 派生，裁决侧（stdlib-only 的
    validator）也要算同一份答案；两处各写一遍必然漂移。"""
    return precision_policy.call_variants(spec)


def _variant_matches(when, attrs):
    return precision_policy.variant_matches(when, attrs)


def _select_call_variant(variants, attrs, cid):
    """逐 case 选中匹配变体（声明序**首个**匹配者胜）；无匹配 → fail-closed，绝不退默认。"""
    return precision_policy.select_call_variant(variants, attrs, cid)


def _build_aclnn_call(spec, variant, attrs, active_names, cid):
    """把选中的变体 + 本 case 的 attr 取值**完全解析**成该 case 的 `aclnn_call`（driver 直接执行、不再推断）。

    slots 顺序 = spec.params 顺序 = aclnn 签名顺序（穿插的标量属性据此保位，ctypes runner 才拼得对 argtypes）。
    每个 slot 都带 `name`（供与 header 签名逐项对账）：
      · `{"role":"in","name":..,"input_idx":i}`      —— 第 i 个 case 输入；
      · `{"role":"attr","name":..,"ctype":..,"value":..}` —— 已解析的标量实参（**None 一律 fail-closed**）；
      · `{"role":"out","name":..,"output_idx":k}`    —— 对应 expected.outputs[k]；
      · `{"role":"out_null","name":..}`              —— 该变体不落地此输出 → 传 NULL、不回读。
    """
    out_pos = {n: k for k, n in enumerate(active_names)}
    slots, in_i = [], 0
    for p in spec.get("params", []):
        if not isinstance(p, dict):
            raise ValueError(f"{cid}: 非法 param 条目 {p!r}")
        io, name = p.get("io"), p.get("name")
        if io == "in":
            slots.append({"role": "in", "name": name, "input_idx": in_i})
            in_i += 1
        elif io == "attr":
            if name not in variant["active_attrs"]:
                continue                                 # 该变体签名里没有这个标量槽（如全局 API 无 dim/keepdim）
            value = variant["attrs"][name] if name in variant["attrs"] else attrs.get(name)
            if value is None:
                raise ValueError(
                    f"{cid}: 变体 {variant['symbol']!r} 的 attr {name!r} 取值为 None —— "
                    f"**绝不静默转标量默认值**（那既不是该 case 的语义、又可能与签名不符）。"
                    f"请在 spec 的该变体里显式声明 attrs.{name}，或把它移出 active_attrs（换用无此形参的变体），"
                    f"fail-closed")
            slots.append({"role": "attr", "name": name, "ctype": _attr_ctype(p), "value": value})
        elif io == "out":
            if name in out_pos:
                slots.append({"role": "out", "name": name, "output_idx": out_pos[name]})
            else:
                slots.append({"role": "out_null", "name": name})
        else:
            raise ValueError(f"{cid}: param {name!r} 的 io {io!r} 未知（须 in/attr/out）")
    return {"symbol": variant["symbol"], "slots": slots}


def _normalize_golden_outputs(golden):
    """golden_fn 返回值 → **数组列表**：tuple/list→list（多输出）；单数组→[数组]（单输出）。
    多输出算子的某些 case 可能只出前缀个输出（如全局 median 只出 values、无 indices）→ 列表随之变短。"""
    if isinstance(golden, (tuple, list)):
        return [np.asarray(g) for g in golden]
    return [np.asarray(golden)]


def _tolerance_source(spec):
    """spec.precision.tolerance_source（torch_allclose 容差权威来源；仅 torch 对标场景用，缺=None）。"""
    return (spec.get("precision") or {}).get("tolerance_source")


def _mo_taskdoc_tol(spec):
    """spec.precision.taskdoc_tol → (rtol, atol)（仅 tolerance_source==taskdoc 时用）；缺/畸形→None（下游 fail-closed）。"""
    t = (spec.get("precision") or {}).get("taskdoc_tol")
    if isinstance(t, (list, tuple)) and len(t) == 2:
        return (t[0], t[1])
    return None


def _save_inputs_multi(cdir, cid, inputs, in_params, dtn):
    """多输出通路存输入（与 legacy 单输出**同口径**：bf16→uint16 位模式 + storage_dtype，其余原生）。"""
    items = []
    for j, x_logical in enumerate(inputs):
        if dtn == _BF16:                                 # 物理 = 从逻辑单独 encode 出的 uint16 位模式
            x_bin = _f32_to_bf16_uint16(x_logical)
            if x_bin.size and np.shares_memory(x_bin, x_logical):
                raise ValueError(f"{cid}: bf16 X_bin 与 X_logical 共享内存（违 layout 字节契约 职责#2）")
        else:
            x_bin = np.ascontiguousarray(x_logical, dtype=_storage_np(dtn))
        np.save(os.path.join(cdir, f"x{j + 1}.npy"), x_bin)
        item = {"name": in_params[j]["name"], "shape": list(np.asarray(x_logical).shape),
                "dtype": dtn, "path": f"{cid}/x{j + 1}.npy"}
        if dtn == _BF16:
            item["storage_dtype"] = _storage_name(dtn)
        items.append(item)
    return items


def _index_golden_array(arr, dtype_name, where):
    """index 角色 golden 的落盘数组——dtype **按 spec 声明的 index out-param 取**（F2 修复，2026-07-24）。

    旧洞（a3 真机首跑 aclnn_py 通路实测）：这里恒存 `np.int64`，而真机 actual 的 dtype 由 caseset 的
    `expected.outputs[k].compare_dtype`（= 同一份 spec 声明，driver 据它开输出 buffer）决定 →
    **spec 声明 int32 indices 的算子两侧 dtype 必然打架**，走到 `precision_policy.compute_metrics` 的
    index 分支「两侧下标 dtype 必须一致」当场 fail-closed → **永远出不了裁决**（工具盲区，只能靠改 spec 绕）。

    修法取「golden 按声明存」而非「比较前归一」，理由：
      · 单一真源——golden 落盘 dtype 与真机 buffer dtype 同出 spec 一处声明，天然同型、无需归一；
      · 归一在比较端做会**抹掉真问题**——实现真的返回了非声明 dtype 时，那正是要被看见的缺陷，
        不该被采集层悄悄铺平（fail-closed 优于静默）；
      · 越界能在**生成期**就拒（此处），比留到比较端再发现更早、错更明确。

    两道 fail-closed：
      · golden_fn 返回的下标必须是**真整数**（bool / 浮点一律拒——`astype` 会静默截断 `[0.9]→[0]`）；
      · 声明 dtype 装不下下标取值域（如 int32 装不下 ≥2**31 的下标）→ 拒，绝不静默回绕/截断。
    """
    np_dt = _NATIVE.get(dtype_name)
    if np_dt is None or not np.issubdtype(np_dt, np.integer):
        raise ValueError(f"{where}: spec 声明的 index dtype={dtype_name!r} 非生成层支持的整数 dtype "
                         f"（可选 {sorted(n for n, t in _NATIVE.items() if np.issubdtype(t, np.integer))}）"
                         f"——下标 dtype 不猜、不兜底，fail-closed")
    a = np.asarray(arr)
    if a.dtype == np.bool_ or not np.issubdtype(a.dtype, np.integer):
        raise ValueError(f"{where}: golden 下标 dtype={a.dtype.name!r} 非整数（bool/浮点一律拒，"
                         f"禁止静默截断成整型），fail-closed")
    info = np.iinfo(np_dt)
    if a.size:
        lo, hi = int(a.min()), int(a.max())
        if lo < info.min or hi > info.max:
            raise ValueError(f"{where}: golden 下标取值域 [{lo},{hi}] 装不下 spec 声明的 index dtype "
                             f"{dtype_name}（可表示 [{info.min},{info.max}]）——窄化会静默回绕成合法下标，"
                             f"一律拒，fail-closed")
    return a.astype(np_dt, copy=False)


def _active_output_names(spec, variant, cid):
    """本 case **真正落地**的输出名——**实现已上提到 precision_policy**（与裁决侧共用唯一真源）。"""
    return precision_policy.active_output_names_for_variant(spec, variant, cid)


def _build_multi_output_case(spec, op, cid, cdir, entry, inputs, in_params, dtn, attrs, dims,
                             vmode, golden_fn, out_shape_fn, golden_source, tier,
                             spec_standard, tol_src, tol_tuple, active_names):
    """多输出契约（torch 对标 median 见证）：golden_fn 返回 tuple → 逐输出 `np.save(golden_{k}.npy)`、
    逐输出 out_shape 对账、据 spec **op-中立**派生每输出判据契约（`derive_output_contracts`：只据 out_role/
    index_of/dtype 字段，绝无算子名分支）→ `expected.outputs[]`。

    ⚠ **输出数量与身份严格绑 spec**（审计 finding #4）：本 case 落地哪些输出由 `active_names`（spec 的
    `call_variants.active_outputs`，无变体则全部 out 参数）**声明**，golden_fn 返回数必须**恰好相等**——
    不再接受「更短的前缀」（by-dim 漏 indices 会整条丢掉 index 验证链却一路绿）。每个 outputs[] 条目保存
    `index`+`name`+`role` 三者，下游按三元组交叉核验、换序当场可见。"""
    if entry["id_kind"] == "empty":
        raise ValueError(f"{cid}: 多输出契约暂不支持空 Tensor 用例（median 等归约类 numel==0 非法、"
                         f"spec 应设 allow_empty_tensor:false）——fail-closed，不为多输出空 case 编造语义")
    gouts = _normalize_golden_outputs(golden_fn(inputs, attrs))   # [values(, indices)]，长度随 case 分派
    if not gouts:
        raise ValueError(f"{cid}: golden_fn 未返回任何输出（多输出契约至少 1 个）")
    declared = None                                      # value/index 归约后同形 → 共用一个声明形状
    if out_shape_fn is not None:
        declared = _declared_out_shape(out_shape_fn, inputs, attrs, cid)
        out_shape_source = "golden.out_shape"
    else:
        out_shape_source = "golden_fn_actual"            # 未导出 → 用实测、不跨校（多输出归约应导出 out_shape）
    # 据 spec 逐输出派生 canonical 判据契约（op-中立）。按 spec out-param 顺序 → 转 name 索引，按身份取。
    case_in_dts = [(p["name"], dtn) for p in in_params]
    contracts = precision_policy.derive_output_contracts(spec, case_in_dts, spec_standard, tol_src, tol_tuple)
    # acceptance 层（任务书验收口径，可选）逐输出 canonical——spec 声明了就必须随 case 落盘，
    # 否则 validator 那边「spec 声明 acceptance 却在 caseset 里找不到」会 fail-closed（审计 finding #2）。
    acc_contracts = precision_policy.derive_acceptance_contracts(spec, contracts)
    by_name = {c["name"]: c for c in contracts}
    acc_by_name = ({c["name"]: acc_contracts[i] for i, c in enumerate(contracts)}
                   if acc_contracts is not None else {})
    if len(gouts) != len(active_names):
        raise ValueError(f"{cid}: golden_fn 返回 {len(gouts)} 个输出 ≠ spec 声明本 case 落地的 "
                         f"{len(active_names)} 个输出 {active_names}（数量严格相等，**不接受更短的前缀**——"
                         f"漏一个输出就少一整条判据链，fail-closed；attrs={attrs}）")
    out_items = []
    for k, arr in enumerate(gouts):
        arr = np.asarray(arr)
        name = active_names[k]                           # 身份由 spec 声明的落地序给，不由 golden 返回序反推
        ct = by_name[name]
        actual_shape = tuple(int(d) for d in arr.shape)
        if declared is not None and actual_shape != declared:
            raise ValueError(f"{cid}: 输出#{k}({name}/{ct['role']}) 实测形状 {actual_shape} ≠ out_shape() "
                             f"声明 {declared}（value/index 归约后同形；声明与实现打架，fail-closed；attrs={attrs}）")
        # golden 落盘 dtype：index 角色按 **spec 声明的 index dtype**（F2：不再恒 int64——恒 int64 与
        # 真机按 compare_dtype 开的 buffer 打架，int32 indices 的算子永远出不了裁决）；value 按 compare dtype。
        if ct["role"] == precision_policy.OUT_ROLE_INDEX:
            garr = _index_golden_array(arr, ct["dtype"], f"{cid}: 输出#{k}({name}/index)")
        else:
            garr = np.asarray(arr, dtype=_compute_np(ct["dtype"]))
        # ⚠ np.ascontiguousarray 会把 0-d 标量**提成 (1,)**（强制 ndim≥1）——全局归约(median 全局)输出是 0-d，
        #   直接存会让落盘 npy 形状 ≠ 记录的 out_shape。故 ascontiguousarray 后 reshape 回 actual_shape（元素数不变、恒合法）。
        gsave = np.ascontiguousarray(garr).reshape(actual_shape)
        np.save(os.path.join(cdir, f"golden_{k}.npy"), gsave)
        item = {"index": k, "name": name, "role": ct["role"],
                "golden_path": f"{cid}/golden_{k}.npy", "golden_tier": tier,
                "out_shape": list(actual_shape), "out_shape_source": out_shape_source,
                "compare": ct["policy"]["kind"], "compare_dtype": ct["dtype"],
                "standard": ct["standard"], "tolerance_policy_id": ct["tolerance_policy_id"],
                "policy": ct["policy"], "threshold": precision_policy.threshold_digest(ct["policy"])}
        if ct.get("index_of") is not None:               # index 输出：所引 value 输出名（同 spec 的 index_of 字段）
            item["index_of"] = ct["index_of"]
        out_items.append(item)
    # 收口自检：(index, name, role) 三元组必须与 spec 声明逐项一致——身份/顺序任一被动过都在这里现形。
    if [(o["index"], o["name"]) for o in out_items] != list(enumerate(active_names)):
        raise ValueError(f"{cid}: outputs[] 的 (index,name) {[(o['index'], o['name']) for o in out_items]}"
                         f" ≠ spec 声明落地序 {list(enumerate(active_names))}（换序/错配，fail-closed）")
    for o in out_items:
        if o["role"] != by_name[o["name"]]["role"]:
            raise ValueError(f"{cid}: 输出 {o['name']!r} 的 role {o['role']!r} ≠ spec out_role "
                             f"{by_name[o['name']]['role']!r}（身份三元组不自洽，fail-closed）")
    in_items = _save_inputs_multi(cdir, cid, inputs, in_params, dtn)
    expected = {"golden_source": golden_source, "golden_tier": tier, "verify_mode": vmode,
                "outputs": out_items, "case_origin": entry["case_origin"], "rule_ref": entry["rule_ref"]}
    if entry.get("cost_scaled"):                         # G4：该 case 被降过规模 → 随 case 一起如实留痕
        expected["cost_scaled"] = entry["cost_scaled"]
    return {"id": cid, "dims": dims, "tags": entry["tags"],
            "inputs": in_items, "attrs": attrs, "expected": expected}


def gen_cases(spec, work_dir):
    op = spec["op"]
    # golden 按算子从用户侧 <ops_root>/<op>/golden.py 加载（elementwise 通路不内置 golden 值、缺则 fail-closed；
    # ADR 0011 决策 1/2/5，proposed）。⚠ 非「引擎零内置算子」——catlass_adapter 的 matmul golden 与本文件 :34
    # 的 _BF16_EXACT_OPS 是两处已知例外，仍是引擎里的算子知识。
    # golden_source 来自加载的 GOLDEN_SOURCE 元数据（决策 5），下游门继续校 oracle_source==映射(golden_source)。
    in_params = [p for p in spec["params"] if p["io"] == "in"]
    runner_form = spec.get("runner_form")                # dtype 能力门按 form 分派（缺省 cpp）；U3
    check_spec_capability(in_params, runner_form)        # 能力边界前置：先于 load_golden，别为不支持的算子白加载 golden
    # C1：load_golden 返回具名元组，`.out_shape` 是**可选**的（未导出=None → 缺省同形语义）。
    _g = load_golden(op)                             # 具名元组：按名取，别再位置解包
    golden_fn, golden_source, out_shape_fn = _g.fn, _g.source, _g.out_shape
    # 批 2：派生 golden 档位（tier 1..4 / 是否需人核 / blocked 原因），**记录不阻断**。
    # 阻断是批 5 门侧的事——这里若直接拦，任何还没把任务书快照入库的算子会当场跑不了，
    # 而「快照没入库」本身正是要被**看见**的问题，不是要被静默绕过的。
    _tier = _derive_tier(op, _g.contract)
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

    # 多输出契约触发（据 spec 字段、op-中立）+ torch_allclose 容差分源参数（仅 torch 对标场景用）。
    uses_multi = _uses_output_contract(spec)
    tol_src = _tolerance_source(spec)
    tol_tuple = _mo_taskdoc_tol(spec)
    # ACLNN 调用变体：ctypes 与官方 C++ Extension 两种执行形态共用逐 case 已解析调用契约。
    # `aclnn_call`，driver 不再自己推变体。变体表必填——没它就只能靠 driver 兜默认值，而兜出来的
    # `dim=0` 既不是全局语义、又可能与单输出签名不符（越界写 / ABI 崩）。
    variants = _call_variants(spec)
    needs_aclnn_call = runner_form in ("aclnn_py", "cpp_extension")
    if needs_aclnn_call and not variants:
        raise ValueError(f"{op}: runner_form={runner_form!r} 但 spec 未声明 call_variants —— "
                         f"aclnn 调用形态（符号/实参表/落地输出）必须由 spec 显式声明、逐 case 解析，"
                         f"不许下游兜默认值，fail-closed")

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

        inputs = _build_inputs(case_rng, in_params, shp, dtn, attrs, data_kind,
                               runner_form)              # 逻辑数组（compute dtype）
        # 逐 case 选中调用变体（无变体声明 → None；有声明但无匹配 → fail-closed，绝不退默认）。
        variant = _select_call_variant(variants, attrs, cid) if variants else None
        if uses_multi:                                   # 多输出契约（torch 对标 median）：全程 op-中立据字段
            case = _build_multi_output_case(
                spec, op, cid, cdir, entry, inputs, in_params, dtn, attrs, dims, vmode,
                golden_fn, out_shape_fn, golden_source, _tier, spec_standard, tol_src, tol_tuple,
                _active_output_names(spec, variant, cid))
            if needs_aclnn_call:                         # 该 case **完全解析好**的调用（driver 直接执行）
                case["aclnn_call"] = _build_aclnn_call(
                    spec, variant, attrs, [o["name"] for o in case["expected"]["outputs"]], cid)
            cases.append(case)
            continue
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
            # 批 2：golden 档位随每条 case 走（无契约块 → None，行为与批 2 前一致）
            empty_expected = {"golden_source": golden_source, "golden_tier": _tier,
                              "golden_path": f"{cid}/golden.npy",
                              "verify_mode": vmode, "compare": "na", "standard": "na",
                              "compare_dtype": None, "case_origin": entry["case_origin"],
                              "rule_ref": entry["rule_ref"],
                              "out_shape": list(actual_out_shape),      # C1：输出形状（供下游收发）
                              "out_shape_source": out_shape_source,
                              "note": "空Tensor 功能用例（numel=0，无精度判定，validator→na）"}
            if entry.get("cost_scaled"):                 # G4：该 case 被降过规模 → 随 case 一起如实留痕
                empty_expected["cost_scaled"] = entry["cost_scaled"]
            empty_case = {"id": cid, "dims": dims, "tags": entry["tags"], "inputs": in_items,
                          "attrs": attrs, "expected": empty_expected}
            if needs_aclnn_call:                         # legacy 单输出 + aclnn_py：同样逐 case 解析调用
                empty_case["aclnn_call"] = _build_aclnn_call(
                    spec, variant, attrs, _active_output_names(spec, variant, cid), cid)
            cases.append(empty_case)
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
        expected = {"golden_source": golden_source, "golden_tier": _tier,
                    "golden_path": f"{cid}/golden.npy",
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
        legacy_case = {"id": cid, "dims": dims, "tags": entry["tags"],
                       "inputs": in_items, "attrs": attrs, "expected": expected}
        if needs_aclnn_call:                             # legacy 单输出 + aclnn_py：同样逐 case 解析调用
            legacy_case["aclnn_call"] = _build_aclnn_call(
                spec, variant, attrs, _active_output_names(spec, variant, cid), cid)
        cases.append(legacy_case)
    perf_case_policy = _classify_perf_cases(spec, cases)
    attr_order = [p["name"] for p in spec["params"] if p["io"] == "attr"]
    # Q7 dtype 覆盖门用：dtype_required=任务书权威全集（spec 透传，未声明则 None→门不阻塞）；
    # dtype_tested=实测子集，**从实际生成的 cases 归并**（非 in 参数并集——门也用真实 cases 对账，两侧口径一致、
    # 消除「并集过报」与「自报漂移」）；task_pr_gaps 透传供门查 dtype_deferred。
    dtype_tested = sorted({c["inputs"][0]["dtype"] for c in cases
                           if c.get("inputs") and c["inputs"][0].get("dtype")})
    caseset = {"op": op, "spec_ref": spec.get("op"), "work_dir": work_dir,
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
            # OC 账本：**仅在 spec 声明了 operator_class 时才出现**——未声明的算子 caseset 逐字节不变
            # （向后兼容硬约束）。声明了就把「按哪类口径产的 / 有没有铺 inf·-inf·nan」如实落进产物。
            **({"operator_class": plan_meta["operator_class"],
                "emits_nonfinite_specials": plan_meta["emits_nonfinite_specials"]}
               if plan_meta["operator_class"] is not None else {}),
            # CP 账本：**仅在 spec 显式声明了 precision.case_profile 时才出现**（同 operator_class 的处理）——
            # 未声明的算子 caseset 逐字节不变（向后兼容硬约束：现有样例 spec 一个都没声明，
            # 多一个键就破 `ExistingOpsByteIdenticalTest` 的 sha256 pin）。
            # 声明了就把「这批用例是按哪个造例档位产的」如实落进产物，报告侧不必回头猜。
            **({"case_profile": plan_meta["case_profile"]}
               if plan_meta["case_profile_declared"] else {}),
            **({"perf_case_policy": perf_case_policy} if perf_case_policy is not None else {}),
            "cases": cases}
    # ⚠ 原先这里挂一份 **op 级** `aclnn_call_template`，由 driver 自己按 case 兜变体（`dim=None`→`dim=0`）——
    # 已被 finding #3 判为不合规并**整体替换**：调用形态现在逐 case 解析、写在 `case["aclnn_call"]` 里。
    # 其它 runner_form（含缺省=cpp）零变更、caseset 字节不动（向后兼容硬约束：现有 4 算子不破）。
    return caseset


def _build_dry_run_ledger(spec, preparation_inputs=None):
    """plan-only（**不跑 golden_fn**、不落 .npy）：构造可持久化覆盖账本 + 确定性自检。

    G4 起会**尽力**加载 golden.py 取 `out_shape` 造规模预算的 cost 模型——好让「归约/成对类算子的大 shape
    算不完」在 **CP-B 契约自检**就暴露，而不是拖到 CP-D 真生成时卡死。加载不到（golden.py 还没写、或写坏了）
    → 明说「未核」并照常出计划，**不阻塞**（那种 spec 到了 gen_cases 本来就会 fail-closed）。
    ⚠ 本仓 golden.py 约定 torch 延迟 import（见 `samples/golden/*/golden.py`），故此处仍不拉 torch；
    但若某算子在模块顶层 `import torch`，dry-run 会跟着 import ——这是加载用户代码的代价，如实记在这。"""
    perf_case_policy = _perf_case_policy(spec)
    op = spec["op"]
    in_params = [p for p in spec["params"] if p["io"] == "in"]
    # 能力边界前置：三元算子 / dtype 双层能力门在 CP-B 就拦下，不拖到 CP-D（form 缺省 cpp）
    check_spec_capability(in_params, spec.get("runner_form"))
    attrs_default = {p["name"]: p.get("default") for p in spec["params"] if p["io"] == "attr"}
    self_param = next((p for p in in_params if p["name"] == "self"), in_params[0])
    dtypes = self_param["dtype"]
    case_target = (spec.get("precision") or {}).get("case_target", _DEFAULT_CASE_TARGET)
    # G4：取 cost 模型。**只对「golden.py 还没写」降级为「未核」**，不吞其它加载失败。
    # ⚠ 原来是 `except Exception` 一把吞：一份**已存在但坏掉**的 golden.py（语法错、顶层抛异常、
    # 契约导出不全）也能安静通过 CP-B —— 而散文把 dry-run 称作「契约自检」，这就是 fail-open。
    # 「还没写」是合法的预览场景（spec 先行、golden 后补）；「写了但坏了」是**真错误**，必须当场炸。
    cost_fn, cost_why, _dry_out_shape_fn = None, "", None
    golden_dependency = {"status": "missing", "bytes_sha256": None,
                         "contract_sha256": None}
    try:
        golden = load_golden(op)
        _dry_out_shape_fn = golden.out_shape            # 具名取：下标在字段重排后会静默指错
        cost_fn = _make_cost_fn(in_params, _dry_out_shape_fn)
        import repo_adapter
        golden_path = os.path.join(repo_adapter.op_dir(op), "golden.py")
        with open(golden_path, "rb") as golden_fh:
            golden_bytes = golden_fh.read()
        contract_bytes = content_address.canonical_json_bytes(golden.contract)
        golden_dependency = {
            "status": "loaded",
            "bytes_sha256": hashlib.sha256(golden_bytes).hexdigest(),
            "contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
        }
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
    ranks = _allowed_ranks(in_params)
    eqn = None
    if any(p.get("io") == "attr" and p.get("name") == "equal_nan" for p in spec["params"]):
        eqn = sorted({str(e["attrs"].get("equal_nan")) for e in entries if "equal_nan" in e["attrs"]})
    determinism = None
    if ids:
        cid = ids[0]
        a = float(_case_rng(cid).random())
        b = float(_case_rng(cid).random())
        determinism = {"case_id": cid, "first_draw_a": a, "first_draw_b": b, "equal": a == b}
    canonical_spec = content_address.canonical_json_bytes(spec)
    logic_files = {}
    logic_root = os.path.dirname(os.path.abspath(__file__))
    for filename in _PLANNER_DEPENDENCIES:
        with open(os.path.join(logic_root, filename), "rb") as source_fh:
            logic_files[filename] = hashlib.sha256(source_fh.read()).hexdigest()
    planner_sha256 = logic_files["gen_cases.py"]
    ledger = {
        "schema": "oprunway.gen_cases.dry_run_ledger",
        "schema_version": 1,
        "spec_binding": {
            "op": op,
            "sha256": hashlib.sha256(canonical_spec).hexdigest(),
            "canonical_json": "oprunway.content_address.canonical_json_bytes/v1",
        },
        "preparation_inputs": preparation_inputs,
        "planner_binding": {
            "implementation": "gen_cases.py::_plan",
            "gen_cases_py_sha256": planner_sha256,
            "logic_files": logic_files,
            "seed": SEED,
            # ⚠ `seed` 只钉住种子，钉不住流本身——同一 seed 在不同 numpy 大版本下可能产不同字节。
            # 判定看 `numpy_stream_pin`；`numpy_version` 是全量版本，只作诊断（理由见
            # 模块上方 `numpy_stream_pin` 的注释）。
            "numpy_version": np.__version__,
            "numpy_stream_pin": current_numpy_stream_pin(),
            "numpy_stream_pin_granularity": _NUMPY_STREAM_PIN_GRANULARITY,
            "default_case_target": _DEFAULT_CASE_TARGET,
            "default_golden_cost_budget": _GOLDEN_COST_BUDGET,
            "case_profiles": list(_CASE_PROFILES),
            "operator_classes": list(_OPERATOR_CLASSES),
        },
        "planning": {
            "case_target": case_target,
            "runner_form": spec.get("runner_form", "cpp"),
            "case_profile": meta["case_profile"],
            "case_profile_declared": meta["case_profile_declared"],
            "operator_class": meta["operator_class"],
            "input_ranks": None if ranks is None else sorted(ranks),
            "golden_out_shape": "loaded" if _dry_out_shape_fn is not None else "not_available",
            "golden_cost_note": cost_why.strip(),
            **({"perf_case_policy": perf_case_policy}
               if perf_case_policy is not None else {}),
        },
        "golden_dependency": golden_dependency,
        "summary": {
            "emitted": meta["emitted"],
            "pool_max": meta["pool_max"],
            "forced_total": meta["forced_total"],
            "forced_special": meta["forced_special"],
            "shapes": sorted({_shape_tag(e["shape"]) for e in entries}),
            "shape_classes": sorted({_shape_class(e["shape"]) for e in entries}),
            "by_dtype": dict(collections.Counter(e["dtype"] for e in entries)),
            "id_kinds": dict(collections.Counter(e["id_kind"] for e in entries)),
            "special": sorted({e["id_kind"] for e in entries if e["id_kind"] in specials}),
            "equal_nan_values_seen": eqn,
        },
        "coverage": {
            "strength": meta["coverage_strength"],
            "dropped_combo_classes": meta["dropped_combo_classes"],
            "unpaired_combo_classes": meta["unpaired_combo_classes"],
            "attr_axis_lengths": meta["attr_axis_lengths"],
            "golden_cost": meta["golden_cost"],
            "emits_nonfinite_specials": meta["emits_nonfinite_specials"],
            "note_target_below_forced": meta.get("note_target_below_forced"),
        },
        "determinism": determinism,
    }
    # 账本本身也带内容摘要：复用校验不能只核 spec/planner/golden 依赖，却允许 coverage/summary
    # 被截断或改写后仍命中。摘要覆盖除自身外的完整 payload，避免 mtime/路径充当身份。
    ledger["ledger_digest"] = content_address.content_digest(
        "oprunway/case-plan/v1", ledger)
    return ledger


def _render_dry_run_ledger(ledger):
    """把结构化 dry-run 账本渲染成既有的人读文本；不重新规划、不改变任何用例策略。"""
    op = ledger["spec_binding"]["op"]
    planning = ledger["planning"]
    summary = ledger["summary"]
    coverage = ledger["coverage"]
    target = planning["case_target"]
    emitted = summary["emitted"]
    pool_max = summary["pool_max"]
    forced_total = summary["forced_total"]
    print(f"[dry-run] {op} target={target} emitted={emitted} pool_max={pool_max} "
          f"forced_total(=强制下限S)={forced_total} forced_special={summary['forced_special']}")
    print(f"  区间: case_target 建议落 [S={forced_total}, pool_max={pool_max}]"
          f"（< S 则 emit 抬到 S；> pool_max 则 emit=pool_max、数量门软化 PASS+note）")
    # OC：算子类别 → 特殊值口径。未声明时明说「= 现行为」，别让人误以为已经按类别裁过。
    print(f"  operator_class: {planning['operator_class'] or '未声明（缺省 = 现行为）'}"
          f"  → inf/-inf/nan 特殊场景: "
          f"{'产' if coverage['emits_nonfinite_specials'] else '**不产**（该类别按方法学不适用 NaN·Inf）'}")
    # CP：造例档位。未声明时明说「缺省 = legacy = 现行为」，别让人以为已经按参考仓口径造过例。
    print("  case_profile: "
          + (f"{planning['case_profile']}（spec 显式声明）" if planning["case_profile_declared"]
             else "未声明（缺省 = legacy = 现行为）"))
    print(f"  input_rank: {'不限制' if planning['input_ranks'] is None else planning['input_ranks']}  "
          f"shapes: {summary['shapes']}")
    print(f"  by_dtype : {summary['by_dtype']}")
    print(f"  id_kinds : {summary['id_kinds']}")
    print(f"  special  : {summary['special']}")
    if summary["equal_nan_values_seen"] is not None:
        print(f"  equal_nan values seen: {summary['equal_nan_values_seen']}")
    dropped = coverage["dropped_combo_classes"]
    print(f"  dropped_combo_classes: {len(dropped)} (first3={dropped[:3]})")
    # 零配对告警：「attr 取值 × shape 结构类」从未同时出现。任务书点名的边界（如「归约轴维度为 1」）
    # 实跑 0 条时，以前**全程无告警**、只能事后人肉核 caseset；现在在计划期就报出来。
    _up = coverage["unpaired_combo_classes"]
    print(f"  shape_classes: {summary['shape_classes']}")
    if _up["attr_values_never_emitted"]:
        print(f"  ⚠ attr 取值零覆盖（spec 声明了但一条没生成）: {_up['attr_values_never_emitted']}")
    if _up["count"]:
        print(f"  ⚠ unpaired_combo_classes（从未配对）: {_up['count']} 条"
              + "".join(f"\n      · {c}" for c in _up["classes"][:8])
              + ("\n      · …（余下已截断）" if _up["count"] > 8 else ""))
        print("     提示：任务书点名的边界若落在这些组合里，可用 spec.attr_axis_lengths 定向生成"
              "（如 [{'attr':'dim','lengths':[1]}] = 让 dim 指的轴长度取 1）")
    else:
        print("  unpaired_combo_classes（从未配对）: 0")
    _ax = coverage["attr_axis_lengths"]
    if _ax["declared"]:
        _na = sum(1 for it in _ax["items"] if it["status"] == "not_applicable")
        print(f"  attr_axis_lengths: 声明 {_ax['declared']} → 定向生成 {_ax['emitted']} 条"
              f"（逐项账本 {len(_ax['items'])} 项：emitted={_ax['emitted']} "
              f"not_applicable={_na}（如 dim=None 的全局归约，合法缺席） skipped={len(_ax['skipped'])}）")
        for it in _ax["items"]:                          # 逐项列，别只报总数（finding #5 的教训）
            if it["status"] == "emitted":
                print(f"      · {it['attr']}={it['attr_value']!r}(a{it['attr_idx']}) "
                      f"轴长度={it['length']} → rank{it['rank']} shape={it['shape']}"
                      f"（轴 {it['norm_axes']} 已锁定，不随 cost 降规模）")
            elif it["status"] == "skipped":                # 到不了这（逐项判据已 fail-closed），留作诊断
                print(f"      · ⚠ {it['attr']}={it['attr_value']!r}(a{it['attr_idx']}) "
                      f"轴长度={it['length']} → 未产出：{it['reason']}")
    _gc = coverage["golden_cost"]                        # G4 规模预算账本
    cost_note = (" " + planning["golden_cost_note"]) if planning["golden_cost_note"] else ""
    print(f"  golden_cost: budget={_gc['budget']} model={_gc['model']}{cost_note}")
    if _gc["scaled_cases"]:
        print(f"    ⚠ 降规模(强制项，已记账) {len(_gc['scaled_cases'])} 条: "
              + "; ".join(f"{r['id_kind']} {r['requested_shape']}→{r['emitted_shape']}"
                          for r in _gc["scaled_cases"][:3]))
    if _gc["skipped_shapes"]:
        print(f"    ⚠ 网格剔除(超预算，已记账，**不计入已覆盖**) {_gc['skipped_shape_classes']} 类: "
              + "; ".join(f"{r['dtype']}×{r['shape']}(cost={r['cost']})"
                          for r in _gc["skipped_shapes"][:3]))
    print(f"  coverage: {coverage['strength']}")
    det = ledger["determinism"]
    if det:
        print(f"  determinism(_case_rng[{det['case_id']}] first draw): "
              f"{det['first_draw_a']} == {det['first_draw_b']} -> {det['equal']}")
    if coverage["note_target_below_forced"]:
        print(f"  note: {coverage['note_target_below_forced']}")


def _write_json_atomic(path, payload):
    """按内容寻址基础层的严格 JSON/原子写契约落 durable ledger。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    base = os.path.basename(path)
    os.makedirs(directory, exist_ok=True)
    content_address.atomic_write_json(directory, base, payload)


def _dry_run(spec, preparation_inputs=None):
    """兼容入口：构造 ledger 后按既有 stdout 语义渲染，并返回结构化账本。"""
    ledger = _build_dry_run_ledger(spec, preparation_inputs=preparation_inputs)
    _render_dry_run_ledger(ledger)
    return ledger


def main(argv):
    dry_only_flags = {"--ledger-out", "--source-facts", "--correspondence"}
    used_dry_only = sorted(dry_only_flags.intersection(argv))
    if used_dry_only and "--dry-run" not in argv:
        raise ValueError(
            f"{', '.join(used_dry_only)} 仅与 --dry-run 一起使用；"
            "正式 gen_cases 产物语义不变")
    if "--dry-run" in argv:                              # plan-only（无 torch/golden/npy），供测试与预探
        rest, ledger_out, source_facts_path, correspondence_path, i = (
            [], None, None, None, 0)
        while i < len(argv):
            arg = argv[i]
            if arg == "--dry-run":
                i += 1
                continue
            if arg == "--ledger-out":
                if i + 1 >= len(argv):
                    raise ValueError("--ledger-out 缺少 JSON 输出路径")
                ledger_out = argv[i + 1]
                i += 2
                continue
            if arg == "--source-facts":
                if i + 1 >= len(argv):
                    raise ValueError("--source-facts 缺少 JSON 路径")
                source_facts_path = argv[i + 1]
                i += 2
                continue
            if arg == "--correspondence":
                if i + 1 >= len(argv):
                    raise ValueError("--correspondence 缺少 JSON 路径")
                correspondence_path = argv[i + 1]
                i += 2
                continue
            rest.append(arg)
            i += 1
        if len(rest) != 1:
            raise ValueError(
                "dry-run 用法: gen_cases.py <spec.json> --dry-run "
                "[--ledger-out <ledger.json>] "
                "[--source-facts <source_facts.json> "
                "--correspondence <correspondence.json>]")
        if bool(source_facts_path) != bool(correspondence_path):
            raise ValueError(
                "--source-facts 与 --correspondence 必须同时提供，防止只绑定一半准备输入")
        preparation_inputs = None
        if source_facts_path:
            source_root = os.path.dirname(os.path.abspath(source_facts_path))
            source_name = os.path.basename(source_facts_path)
            source_payload = content_address.read_artifact(
                source_root, source_name, "oprunway/source-facts/v1")
            source_digest = content_address.content_digest(
                "oprunway/source-facts/v1", source_payload)
            with open(correspondence_path, encoding="utf-8") as corr_fh:
                correspondence = json.load(corr_fh)
            correspondence_bytes = content_address.canonical_json_bytes(
                correspondence)
            if (not isinstance(correspondence, dict)
                    or correspondence.get("status") != "confirmed"
                    or correspondence.get("source_facts_digest") != source_digest):
                raise ValueError(
                    "correspondence 必须 confirmed 且绑定当前 source_facts digest")
            preparation_inputs = {
                "source_facts_digest": source_digest,
                "correspondence_sha256": hashlib.sha256(
                    correspondence_bytes).hexdigest(),
            }
        spec = json.load(open(rest[0], encoding="utf-8"))
        ledger = _dry_run(spec, preparation_inputs=preparation_inputs)
        if ledger_out:
            _write_json_atomic(ledger_out, ledger)
        return
    if len(argv) != 3:
        raise ValueError(
            "正式用法: gen_cases.py <spec.json> <work_dir> <caseset.json>")
    spec_path, work_dir, out_path = argv
    spec = json.load(open(spec_path, encoding="utf-8"))
    caseset = gen_cases(spec, work_dir)
    json.dump(caseset, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[gen_cases] {caseset['op']}: {len(caseset['cases'])} cases -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1:])
