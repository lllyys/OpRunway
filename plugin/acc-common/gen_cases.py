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
  见 dev-doc/oprunway-todo.md gap。本文件仅证「流水线能造/收发 int/bf16 用例」，非「某算子在该 dtype 被验收」。

U3 dtype 单一真源（2026-07-24）：dtype 支持不再由本文件独家说了算，拆成**两层、各自单一真源**——
  · 生成层 = 本文件 `_NATIVE` + bf16（能造输入 / 能算 golden / 能落盘读回；本轮补齐 int64/int8/uint8）；
  · 真机层 = `repo_adapter.supported_np(runner_form)`（能收发）+ `repo_adapter.deferred_np(runner_form)`
    （Track-C 挂账：生成期放行、真机跑到仍 fail-closed）。
  `check_spec_capability` **两层都问**，缺哪层就在报错里点名哪层、并列出两侧各自的支持集。
  修的是这个真 bug：aclnn_py runner 早已支持 int64/int8/uint8，生成端却自带旧硬表挡掉 →
  任务书 8 类 dtype 的覆盖被**工具**压到 4/8（不是被算子）。现有算子 caseset 逐字节不变（sha256 钉住）。
  ⚠ **`runner_form` 缺省不在本文件定义**（P5，2026-08-05）：一律经 `repo_adapter.spec_runner_form`
  / `repo_adapter.DEFAULT_RUNNER_FORM` 取。本文件曾各写一份 `"cpp"` 字面量，而 run_workflow 的缺省
  已是 `cpp_extension` → 同一份省略该键的 spec 在两处被当成两种形态。样例 spec 现已**显式声明**
  `runner_form`，不再依赖缺省。

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
  · **为什么要它**：把本引擎的造例规则对齐参考仓 `Justbin/cannbot-ops-input`（完整笛卡尔精度网格、
    medium shape 档、normal 值域重采样、4-kind 非有限特殊值 …）会**改掉默认造例行为**——而 4 个已真机
    验收的 elementwise 算子（IsClose/Sign/Equal/Neg）的 caseset 与全部 .npy 是**逐字节钉死**的
    （sha256 实测，见 `test_gen_cases_dtype_attr.ExistingOpsByteIdenticalTest`）。所以**先立一道字段驱动的
    档位开关**，后续所有对齐改动一律只在新档位下生效，老算子的字节纹丝不动。
  · **受控词表**（两档，无第三种；实现见 `_case_profile` / `_case_profile_declared`）：
      - `legacy` —— 现行造例规则；**整字段省略即此**，逐字节等于本字段引入前；
      - `torch_parity` —— 对齐参考仓的完整笛卡尔轴模型；带轴选择器时按本仓实测结论去掉被选轴的
        长度-1 退化，属于有意偏离。**仅**用于「任务书对标 torch」场景（不碰 catlass 通路）。
  · **律令 #0 合规**：这是按 **spec 声明的能力档位**分支，**不是按算子名**——换任意声明了 `torch_parity`
    的域内算子，工具零改即用；代码里没有也不许有 `if op == "<算子名>"`。
  · `torch_parity` 必须同时声明 `precision.torch_parity_matrix`，按 dtype×rank×shape profile×attribute
    profile 生成完整笛卡尔；rank 动态轴 class 在逐 case 解析成 first/middle/last。带轴选择器的接口
    按已有 `axis_class` 能力信号把 shape 中**实际会被选择的轴**从 1 提到 2，保留首位长轴同时补出
    `长归约 × batch>1`；无轴选择器的纯 elementwise 仍沿参考布局。`legacy` 与未声明仍保持逐字节兼容。
  · **三重记账**（`_torch_parity_plan`，2026-08-06）：
    `矩阵大小 − |有证据的排除| == case_target == 常规矩阵实产数`，
    任一处漂移当场炸。排除项写 `torch_parity_matrix.excluded`，每条**必须**带 `reason` + `evidence`
    （缩水必须留痕，沿用 `golden_cost.skipped_shapes` / `dropped_combo_classes` 的既有形状）。
    ⚠ `case_target` 仍**必填、无缺省**——这里加的是「它必须与矩阵对得上」的第二重约束，
    不是把缺省值加回来。账本落在 caseset / dry-run 的 `case_matrix_ledger`。
  · **特殊场景决定②**（2026-08-06）：三类受控 `operator_class` 在本档均保持
    `forced_special=0`。参考仓只明确证明 structural 的 `special=0`；本仓现有矩阵对空 / 标量 /
    上下边界的收益也没有实测输入，故按与「值域 regime 暂不引入」相同的证据门槛，不把 legacy
    的四类 forced 项接入本档，也不把 structural 的明文结论反向外推成其它类别应新增场景。
    这是一项**有意不产**的政策，不是遗漏：reason + evidence + 与 `case_target` 的关系落在
    `special_scenario_policy`；未来若有实测支持，特殊场景仍只能独立叠加、不进笛卡尔。
  · **本档拒收 legacy 造例键**（`_TORCH_PARITY_UNCONSUMED_KEYS`，2026-08-06）：`attr_matrix` /
    `attr_axis_lengths` / `allow_empty_tensor` / `empty_axis` / `precision.value_profiles` 在本档
    **一行代码都不消费**，声明即 fail-closed。理由与「为什么是拒绝而不是接上消费」见该表上方长注释；
    要表达算子事实请用 `_` 前缀的纯注释键。
  · 词表外取值 / 非字符串（含**显式 `null`**）→ fail-closed：档位猜错 = 整份用例集悄悄换一套规则，
    比报错贵得多。

用例来源 `spec.precision.case_source` —— **谁出的用例**（字段驱动、op-中立；2026-08-05 引入，
落地见各处 `CS:` 标记）：
  · **为什么要它**：有的任务书**自带整套用例**（`self_test_case/<op>/*_cases.json` + `*_golden.py`）。
    那种情况下本引擎再铺一遍正交网格，验的就不是任务书要求的那批用例——流程看着通过、实际绕过了
    任务书用例，正是本仓最忌的「看起来对」。
  · **受控词表**（两档，无第三种；实现见 `_case_source` / `_case_source_declared`）：
      - `generated`（= 整字段省略时的缺省）：现行造例规则，已验收算子逐字节不变；
      - `taskdoc`：用例身份、shape、dtype、attr、值域全部来自**规范化后的**任务书用例集
        （`taskdoc_caseset.json`，由取材侧产、本文件只消费），本引擎**不再铺网格、不做 1-wise 采样**，
        只负责按 `materialize.seed` 确定性把描述物化成输入字节、算 golden、落盘。
  · ⚠ **`taskdoc` 档必须显式喂入那份 caseset**（`gen_cases(..., taskdoc_caseset=<path>)` /
    CLI `--taskdoc-caseset`）：任务书明确提供了用例却拿不到文件时**一律 fail-closed**，
    **绝不回退自生成**——回退等于再产一次「绕过任务书用例」的产物。
  · ⚠ `golden_unavailable` 是**一等状态**：某条任务书用例算不出 golden（如通道数超参考实现上限），
    该 case 身份仍写进 caseset、允许无 golden 文件、标明原因，**其余 case 照常生成**。
    它退出精度维（无 golden 即无从判精度）、也不进性能候选池，但**在账本里可见**，由门判 BLOCKED。
"""
import collections, hashlib, importlib.util, itertools, json, math, os, re, sys
import numpy as np
import content_address
import perf_mode
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
#
# ⚠ **pin 取完整版本，不做「主.次」收敛。** 初版按 `主.次` 收敛，理由是「补丁版通常不改流，
# 精确相等会造成大量无谓 MISS」——那条推理**被反例推翻了**：numpy **1.18.4** 在补丁版里修了
# `Generator.integers(high=2**32)` 的取值，相对 1.18.3 **输出就变了**，而两者的 `主.次` pin
# 都是 `1.18`。NEP 19 本身也写明正确性修复可以在补丁版破坏流兼容。
# 也就是说「主.次」这个粒度**探测不到已经真实发生过的流变更**——一个逮不住已知反例的门
# 不叫门。宁可多几次 MISS（重跑取材/计划很便宜），不要一次把漂了流的 caseset 判成可复用。
#
# 记两个字段仍然保留，但语义换了：`numpy_stream_pin` 是**判定值**（完整版本），
# `numpy_version` 保留作诊断字段、与 pin 同源，便于人一眼看出比的是什么。
_NUMPY_STREAM_PIN_GRANULARITY = "exact"


def numpy_stream_pin(version):
    """把 numpy 版本规范成随机流比对用的 pin —— **完整版本，不收敛**。

    解析不出「至少两段数字」→ 抛错，**绝不回退成空串或原样放行**：一个含糊的 pin
    会让「版本不同」和「版本存疑」长得一模一样，而后者本该 fail-closed。

    ⚠ 这里**不是**在做版本号语义比较（不判大小、不管 rc/dev 后缀怎么排序），
    只做**身份**比较：两次生成用的是不是同一个 numpy。所以规范化只做形态校验、
    不做归一化——`1.26.4` 与 `1.26.4.post1` 就该判成不同，它们确实是两个包。
    """
    text = str(version).strip()
    parts = text.split(".")
    if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        raise ValueError(
            f"无法把 numpy 版本 {version!r} 认成合法版本号（至少 主.次 两段数字）；"
            f"随机流身份不明时不得放行复用")
    return text


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
# 2026-08-06 扩：uint32 + complex64（真机收发已实测，见 `repo_adapter.SUPPORTED_NP_BY_FORM` 的 provenance 注）。
#   · `uint32` 零特判入表：`is_integer_dtype("uint32")` 本就为真 → `_make_varied` 走整型分支、
#     `np.iinfo` 给 `min=0` → 自动走无符号锚点 (0,1,3)；比对侧 §1.1「int→exact」照旧。
#   · `complex64` 需要显式分支（`_make_varied` / `_make_pairhalf`），且**几处刻意不支持**（见下）。
# ⚠ **复数在生成层是「窄口径支持」，不是全能力支持**，收窄是声明式的、不是漏了：
#     · §1.4 非有限特殊值（inf/-inf/nan）**不铺复数**——「复数的 inf」到底是 `inf+0j` / `0+infj` /
#       `inf+infj` 没有任何权威出处，随手挑一个就是臆造覆盖（`_special_entries` 的 `is_float` 已按
#       此排除复数）；
#     · `value_profile`（nan/tie）、`pairfar`（rtol 跨界）、`nanpair` 对复数一律 fail-closed
#       （见各自函数）——它们的语义都建立在实数序/实数容差上，复数没有天然的序。
#   要放开其中任何一条，先给出口径出处，别为了「矩阵好看」补一个猜出来的实现。
_NATIVE = {"float32": np.float32, "float16": np.float16,
           "int64": np.int64, "int32": np.int32, "int16": np.int16,
           "int8": np.int8, "uint8": np.uint8, "uint32": np.uint32,
           "complex64": np.complex64}
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
    # ⚠ **这里只登记我们手上真有硬件事实的型号**。没有事实的型号（如 Ascend 950PR 的 UB 单次
    #   承载边界）**绝不塞猜测值**——要用就由 spec 显式 `source="spec_supplied"` 直供并留痕。
    "Atlas A3": {"metric": "sum_input_bytes", "small_max_bytes": 256 * 1024},
}
#: `shape_classification.source` 受控词表。缺省 = `hardware_profile`（历史行为：必须命中上表且逐值相符）。
#: `spec_supplied` = 该硬件我们没有受控 profile，边界由 spec 直供并在产物里留痕
#: （门读同一枚标记才不会「生成侧过、门侧卡」）。**不是**绕过已知硬件事实的口子：
#: 上表有该硬件时仍强制逐值相符，spec 改不动我们已核定的数。
_SHAPE_LIMIT_SOURCE_PROFILE = "hardware_profile"
_SHAPE_LIMIT_SOURCE_SPEC = "spec_supplied"
_SHAPE_LIMIT_SOURCES = (_SHAPE_LIMIT_SOURCE_PROFILE, _SHAPE_LIMIT_SOURCE_SPEC)


def _perf_case_policy(spec):
    """解析性能 case 来源与 shape 大小分类契约；未声明则保持历史行为。

    ``case_source=precision_cases`` 只表示性能 case 必须从精度 caseset 中选，不表示每条精度 case
    都必须测性能。``shape_classification`` 仅负责可审计分组，不参与免测、阈值放宽或 pass/fail。

    ``perf.mode``（见 ``perf_mode``）落进本账本，供验收门从 caseset 这份**已过 task1 门的产物**
    独立读取口径，而不是信 perf_report 自报。缺省档 ``ratio_gated`` **不写任何新字段**——
    既有 spec 产出的 caseset 一个字节都不变。
    """
    perf = spec.get("perf")
    if not isinstance(perf, dict):
        return None
    mode = perf_mode.resolve_spec_mode(spec)
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
    limit_source = rule.get("source")
    if limit_source is None:
        limit_source = _SHAPE_LIMIT_SOURCE_PROFILE
    if limit_source not in _SHAPE_LIMIT_SOURCES:
        raise ValueError(
            f"perf.shape_classification.source={limit_source!r} 非受控值，"
            f"须属 {list(_SHAPE_LIMIT_SOURCES)}（字段省略 = {_SHAPE_LIMIT_SOURCE_PROFILE}）")
    profile = _PERF_SHAPE_PROFILES.get(hardware)
    if profile is None and limit_source != _SHAPE_LIMIT_SOURCE_SPEC:
        raise ValueError(
            f"perf.shape_classification.hardware={hardware!r} 尚无受控大小 shape profile；"
            "须先按目标硬件核定 UB 单次承载边界，不能由 spec 任意填写"
            f"（确已按任务书/硬件手册核定过 → 显式声明 source='{_SHAPE_LIMIT_SOURCE_SPEC}' 直供并留痕）")
    # 上表有该硬件时**无论 source 是什么都逐值相符**：spec 不得推翻我们已核定的硬件事实，
    # `spec_supplied` 只解锁「表里没有的硬件」，不是宽档开关。
    if profile is not None and (metric != profile["metric"]
                                or limit != profile["small_max_bytes"]):
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
    shape_contract = {
        "metric": metric,
        "small_max_bytes": int(limit),
        "boundary": "small_if_input_bytes_lte_limit",
        "hardware": hardware,
    }
    # 只在**偏离历史默认**时才多写字段：缺省档（ratio_gated + hardware_profile）产出的 caseset
    # 与改动前逐字节一致，既有 spec（ops-nn / Median / catlass …）零影响。
    if limit_source == _SHAPE_LIMIT_SOURCE_SPEC:
        shape_contract["source"] = limit_source
    policy = {"case_source": source,
              "case_selection": selection_contract,
              "shape_classification": shape_contract}
    if mode != perf_mode.DEFAULT_MODE:
        policy["mode"] = mode
        # 宽档为什么被允许，与 mode 一起落进 Task1 账本：门读的是这份产物，
        # 不读 spec，故授权事实必须随 mode 同行，否则门只能看见「谁自称 measure_only」。
        if perf_mode.is_measure_only(mode):
            policy["measure_only_authorization"] = perf_mode.measure_only_authorization(perf)
    return policy


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
    # CS：`case_source=taskdoc` 时**候选池 = 全部可判精度的任务书用例**，而不是「已带『性能』维的那些」。
    # 理由是实打实的：任务书用例集只描述精度用例，规范化后一条都不带「性能」维 → 旧规则下候选池为空
    # → 性能维零数据。这是**按 spec 的 case_source 字段分档**，不是按算子身份（换任意 taskdoc 档算子同样生效）。
    taskdoc_pool = _case_source(spec) == _CASE_SOURCE_TASKDOC
    eligible, excluded_degenerate_ids, excluded_golden_unavailable_ids = [], [], []
    for case in cases:
        dims = case.get("dims") or []
        tagged = bool(include_tags and set(case.get("tags") or []).intersection(include_tags))
        cid = case.get("id")
        if taskdoc_pool:
            if (case.get("expected") or {}).get("golden_status") == GOLDEN_UNAVAILABLE:
                # 无 golden 的 case 在验收门里是 BLOCKED 身份，不该同时充当性能证据来源。
                # 显式记账、不静默丢——「少了几条性能 case」必须是看得见的。
                excluded_golden_unavailable_ids.append(cid)
                continue
            if "精度" not in dims:
                continue
        elif "性能" not in dims and not tagged:
            continue
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
                # CS：只在 taskdoc 档多这两个键（缺省档产物逐字节不变）。
                **({"candidate_pool": "taskdoc_precision_cases",
                    "excluded_golden_unavailable_case_ids": excluded_golden_unavailable_ids}
                   if taskdoc_pool else {}),
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
    if precision_policy.is_complex_dtype(dtn):
        # 复数：实部、虚部**各自**按同一 regime 独立造，绝不用「实部造完 astype(complex)」——
        # 那样虚部恒 0，一条复数用例连虚部通路都没碰过，账面上却是「complex64 已覆盖」。
        # 锚点同样钉三位，但实/虚**错开**取值，让 (负,零)/(零,正)/(正,负) 三种分量符号组合都出现：
        # 复数没有序、造不出「负数分支」，能钉的就是分量符号组合。op-中立、与算子身份无关。
        re = _make_varied(rng, shape, "float32", regime)
        im = _make_varied(rng, shape, "float32", regime)
        f_im = im.reshape(-1)
        if f_im.size >= 3:
            f_im[0], f_im[1], f_im[2] = np.float32(0.0), np.float32(3.0), np.float32(-2.0)
        return (re + 1j * im).astype(cdt)
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
    if precision_policy.is_complex_dtype(dtn):
        # ⚠ 旧理由（「本仓两个精度标准各选一头：torch=模长、AscendOpTest=分量各判」）**已失效**：
        #   2026-08-06 起本仓比对口径统一为「实虚分量各按 float32 判」，不再分档。
        # 但 fail-closed **照旧成立**，理由换成更根本的一条：这里造的是**被测算子自己**
        # （IsClose 一类）的 near/far 边界，`attrs` 里的 atol/rtol 是**算子属性**、不是我们的比对容差。
        # 复数上「差得远不远」按模长还是按分量，取决于该算子的语义（torch.isclose 对复数就是按模长），
        # 而任务书没给出处 —— 造数时选边即臆造该算子的语义。别拿比对口径的统一去推它。
        raise ValueError(
            f"pairfar（跨容差边界的第二输入）对复数 dtype={dtn!r} 无口径：复数没有天然的序，"
            f"「near/far」按模长还是按实虚分量取决于**被测算子**的 close 语义（任务书未给出处），"
            f"造数时选边即臆造 —— fail-closed，不猜。需要复数的二元 close 类用例请先定该算子的口径。")
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
    if precision_policy.is_complex_dtype(dtn):
        # 复数可支持：「相等 / 不相等」不需要序，+1+1j 后两分量都变 → 必不等。
        # ⚠ 不能落到下面的实数分支：那里 `np.asarray(ref, dtype=np.float32)` 会**静默丢虚部**，
        #   造出来的 b 与 a 的虚部凭空归零，golden 还是「对」的 —— 典型的假覆盖。
        cdt = _compute_np(dtn)
        x = np.asarray(ref, dtype=cdt).copy().reshape(-1)
        x[x.size // 2:] = x[x.size // 2:] + cdt(1 + 1j)
        return x.reshape(shape)
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
    if precision_policy.is_complex_dtype(dtn):
        # 「一个复数 NaN」是 `nan+0j` / `0+nanj` / `nan+nanj`？三种在 numpy 的 `isnan(complex)` 下
        # 全判 True，但喂给 kernel 是三份不同的字节。没有出处就不挑，同 §1.4 特殊值那条收窄。
        raise ValueError(
            f"nan_pair 数据对复数 dtype={dtn!r} 无口径：复数 NaN 有 nan+0j / 0+nanj / nan+nanj 三种"
            f"字节形态，选哪一种都是臆造（isnan 对三者都为 True，看不出差别）—— fail-closed，不猜。")
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
    if precision_policy.is_complex_dtype(dtn):
        # 到不了这里（`_special_entries` 的 `is_float` 已排除复数），留这道门是防「哪天 is_float 的
        # 算法改了」把复数悄悄放进来：`np.float32(inf).astype(complex64)` = `inf+0j`，那是**挑了一种**
        # 复数 inf 形态，且虚部恒 0 —— 一份看着有覆盖、实则从未压过虚部通路的特殊值用例。
        raise ValueError(
            f"§1.4 非有限特殊值（{kind}）对复数 dtype={dtn!r} 无口径：inf+0j / 0+infj / inf+infj "
            f"没有权威出处，挑一种即臆造覆盖 —— fail-closed，不猜。")
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


# ══════════════ CS · 用例来源 `precision.case_source`（generated / taskdoc）══════════════
# 详见模块 docstring「用例来源 case_source」一节。**字段驱动、绝不按算子名分支**（律令 #0）：
# 换任意「任务书自带用例」的算子，声明一句 case_source=taskdoc + 喂进规范化 caseset 即可，工具零改。
_CASE_SOURCES = ("generated", "taskdoc")
#: 未声明时的缺省 = 现行造例规则（向后兼容硬约束：现有 spec 一个都没声明，caseset 逐字节不变）。
_DEFAULT_CASE_SOURCE = "generated"
_CASE_SOURCE_TASKDOC = "taskdoc"

#: 规范化任务书用例集的 schema 标识与版本（取材侧 `taskdoc_caseset.py` 产、本文件只消费）。
#: ⚠ 版本**必须逐字相符**：schema 涨版意味着字段语义可能变了，「照旧解析」正是静默错读的入口。
_TASKDOC_CASESET_SCHEMA = "oprunway.taskdoc_caseset"
_TASKDOC_CASESET_SCHEMA_VERSION = 1
#: 本文件**能确定性复现**的造数分布词表（取材侧 `DISTRIBUTION_BY_FAMILY` 的可实现子集）。
#: 词表外一律 fail-closed——「不认识的分布名」意味着我们造出来的字节与取材侧设想的不是一回事，
#: 而它还会被当成「任务书的那条用例」记账。
#: ⚠ 取材侧还有一个 `uniform_bool`：本引擎**生成层**不支持 bool 输入（`_NATIVE` 无 bool），
#: 故不放进词表——撞上就明说不支持，别造一批下游收发不了的字节。
_TD_DIST_FLOAT = "uniform_float"
_TD_DIST_INT = "uniform_int"
_TASKDOC_DISTRIBUTIONS = (_TD_DIST_FLOAT, _TD_DIST_INT)
#: case_id 安全 token：它会直接当**目录名**用（`<work_dir>/<case_id>/`），故拒路径分隔符、
#: 拒 `..`、拒空串与超长名。与 `repo_adapter._check_id` 同一种把关思路。
_TASKDOC_CASE_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
#: 无 golden 的一等状态标记（写进 `case.expected.golden_status`）。
GOLDEN_UNAVAILABLE = "golden_unavailable"


def _case_source(spec):
    """spec.`precision.case_source` → 受控词表值；**整字段省略 → `"generated"`**（= 现行为）。

    ⚠ 词表外取值 / 非字符串（含显式 `null`）→ fail-closed，口径同 `case_profile` / `tolerance_source`：
      字段一旦出现就必须是词表内的字符串。这个字段猜错的代价特别贵——判成 `generated` 就等于
      「任务书给了用例、我们却自己造了一套」，而报告照样会说「已按用例集验收」。"""
    prec = spec.get("precision") or {}
    if "case_source" not in prec:                        # 整字段省略 = 未声明 → 现行为
        return _DEFAULT_CASE_SOURCE
    raw = prec["case_source"]
    if not isinstance(raw, str) or raw not in _CASE_SOURCES:
        raise ValueError(
            f"spec.precision.case_source={raw!r} 不在受控词表 {list(_CASE_SOURCES)} 里 —— fail-closed，不猜。\n"
            f"  · 'generated' —— 本引擎按覆盖-预算规则造例（= 整字段省略时的缺省）；\n"
            f"  · 'taskdoc'   —— 用例来自任务书自带用例集（须同时喂入规范化 taskdoc_caseset.json）。\n"
            f"  字段一旦出现就必须是这两个字符串之一；拿不准就**整字段省略**（= generated = 现行为）。")
    return raw


def _case_source_declared(spec):
    """spec 是否**显式声明**了 `precision.case_source`（不是「解析出来等于 generated」）。

    同 `_case_profile_declared` 的理由：caseset 的账本键**只在写了的时候才落**，
    现有样例 spec 一个都没写，产物里因此不许多出这个键（字节安全的关键）。"""
    _case_source(spec)                                   # 顺手把词表校验过一遍，不留绕过口
    return "case_source" in (spec.get("precision") or {})


def _hex64(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _finite_number(value):
    """非 bool 的有限实数。⚠ 显式拒 NaN/Inf：python 的 `json` **默认接受**非标准的
    `NaN`/`Infinity` 字面量，一个 `Infinity` 值域端点会静默变成一整片同值输入。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _nonneg_int_shape(value):
    return (isinstance(value, list)
            and all(not isinstance(d, bool) and isinstance(d, int) and d >= 0 for d in value))


def load_taskdoc_caseset(path):
    """读**规范化后的**任务书用例集 → `(payload, sha256)`；结构/词表任一处不合即 fail-closed。

    ⚠ **本函数不解析任务书、不解析原始 `*_cases.json`**——那是取材侧（`taskdoc_caseset.py`）的职责，
    它负责识别、接口映射 IR、阈值规范化并产出这份内容寻址的中间件。本文件只做两件事：
    **校它是不是我们能消费的形状**，以及**按它确定性物化输入**。两侧分工不混，换算子零改。

    校验范围刻意只覆盖**会改变执行语义的字段**（schema / case 身份 / shape / dtype / 值域 / 物化参数 /
    attr / 输出身份 / 阈值），未知的元数据键**保留不动、不阻断**——取材侧记的溯源信息不该被这里当脏数据拦掉。
    """
    if os.path.islink(path):                             # 与 golden.py 同一层把关：拒软链换靶
        raise ValueError(f"taskdoc_caseset 是符号链接，拒绝（防换靶）: {path!r}")
    if not os.path.isfile(path):
        raise ValueError(f"taskdoc_caseset 不存在或不是普通文件: {path!r}")
    with open(path, "rb") as fh:
        raw_bytes = fh.read()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as ex:
        raise ValueError(f"taskdoc_caseset 不是合法 UTF-8 JSON: {path!r}: {ex}") from ex
    if not isinstance(payload, dict):
        raise ValueError(f"taskdoc_caseset 顶层须为 object，得 {type(payload).__name__}: {path!r}")
    if payload.get("schema") != _TASKDOC_CASESET_SCHEMA:
        raise ValueError(
            f"taskdoc_caseset.schema={payload.get('schema')!r} ≠ {_TASKDOC_CASESET_SCHEMA!r}——"
            "只消费取材侧产的规范化用例集，不认任何别的形状（fail-closed，不做格式嗅探）")
    if payload.get("schema_version") != _TASKDOC_CASESET_SCHEMA_VERSION:
        raise ValueError(
            f"taskdoc_caseset.schema_version={payload.get('schema_version')!r} ≠ "
            f"{_TASKDOC_CASESET_SCHEMA_VERSION}——涨版意味着字段语义可能变了，"
            "「照旧解析」正是静默错读的入口；请同步本消费方后再放行")
    if not _hex64(payload.get("taskdoc_sha256")):
        raise ValueError("taskdoc_caseset.taskdoc_sha256 须为 64 位小写十六进制（用例集必须绑定任务书快照）")
    for key in ("mapping_ir", "threshold_schema"):
        if payload.get(key) is None:
            raise ValueError(
                f"taskdoc_caseset 缺 {key}——接口映射与阈值口径是「任务书字段怎么变成 spec 字段」的"
                "唯一交代，缺了就没人说得清这批用例是按什么规则对上号的")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("taskdoc_caseset.cases 须为非空数组（0 条用例不得冒充「已按任务书用例验收」）")
    seen = set()
    for i, case in enumerate(cases):
        where = f"taskdoc_caseset.cases[{i}]"
        if not isinstance(case, dict):
            raise ValueError(f"{where} 非 object")
        cid = case.get("case_id")
        if not isinstance(cid, str) or not _TASKDOC_CASE_ID_RE.fullmatch(cid):
            raise ValueError(
                f"{where}.case_id={cid!r} 非法——它会直接当用例目录名，"
                "只接受 `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}`（拒路径分隔符 / `..` / 空串 / 超长）")
        if cid in seen:
            raise ValueError(f"{where}.case_id={cid!r} 重复——case 身份必须唯一，重名会互相覆盖输入字节")
        seen.add(cid)
        if not _hex64(case.get("content_sha256")):
            raise ValueError(f"{where}.content_sha256 须为 64 位小写十六进制（逐 case 内容寻址）")
        inputs = case.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            raise ValueError(f"{where}.inputs 须为非空数组")
        for j, item in enumerate(inputs):
            iw = f"{where}.inputs[{j}]"
            if not isinstance(item, dict):
                raise ValueError(f"{iw} 非 object")
            if not isinstance(item.get("spec_name"), str) or not item["spec_name"]:
                raise ValueError(f"{iw}.spec_name 须为非空字符串（映射到 spec 的 in 参数名）")
            if not _nonneg_int_shape(item.get("shape")):
                raise ValueError(f"{iw}.shape={item.get('shape')!r} 须为非负整数数组")
            if not isinstance(item.get("dtype"), str) or not item["dtype"]:
                raise ValueError(f"{iw}.dtype 须为非空字符串")
            vr = item.get("value_range")
            if (not isinstance(vr, list) or len(vr) != 2 or not all(_finite_number(v) for v in vr)
                    or float(vr[0]) > float(vr[1])):
                raise ValueError(
                    f"{iw}.value_range={vr!r} 须为 [lo, hi] 两个**有限**实数且 lo<=hi"
                    "（显式拒 NaN/Infinity：json 默认接受它们，一个非有限端点会静默毁掉整片输入）")
        outputs = case.get("outputs")
        if not isinstance(outputs, list) or not outputs:
            raise ValueError(f"{where}.outputs 须为非空数组")
        for j, item in enumerate(outputs):
            ow = f"{where}.outputs[{j}]"
            if not isinstance(item, dict):
                raise ValueError(f"{ow} 非 object")
            if not isinstance(item.get("spec_name"), str) or not item["spec_name"]:
                raise ValueError(f"{ow}.spec_name 须为非空字符串（映射到 spec 的 out 参数名）")
            if not _nonneg_int_shape(item.get("shape")):
                raise ValueError(f"{ow}.shape={item.get('shape')!r} 须为非负整数数组")
            if not isinstance(item.get("dtype"), str) or not item["dtype"]:
                raise ValueError(f"{ow}.dtype 须为非空字符串")
            # 阈值形状按取材侧的规范化产物：`{rtol, atol, raw:[rtol, atol]}`（口径声明在
            # 顶层 `threshold_schema`，AscendOpTest 的 [rtol, atol] 两元组）。
            # ⚠ 本轮**只规范化落盘、不参与裁决**（用户明示：精度按 workflow 默认口径），
            #   但坏值仍当场拒——它会原样进产物，落进去就会被人读成「生效过的阈值」。
            thr = item.get("err_threshold")
            if not isinstance(thr, dict):
                raise ValueError(
                    f"{ow}.err_threshold 须为 object {{rtol, atol, raw}}，得 {thr!r}")
            for key in ("rtol", "atol"):
                if not (_finite_number(thr.get(key)) and float(thr[key]) >= 0):
                    raise ValueError(
                        f"{ow}.err_threshold.{key}={thr.get(key)!r} 须为**有限非负**实数"
                        "（显式拒 NaN/Infinity：json 默认接受它们）")
            raw = thr.get("raw")
            if (not isinstance(raw, list) or len(raw) != 2
                    or [float(raw[0]), float(raw[1])] != [float(thr["rtol"]), float(thr["atol"])]):
                raise ValueError(
                    f"{ow}.err_threshold.raw={raw!r} 与 (rtol, atol)="
                    f"({thr['rtol']!r}, {thr['atol']!r}) 不一致——两处必须同源，"
                    "对不上说明有人动过其中一处，fail-closed")
        attrs = case.get("attrs")
        if not isinstance(attrs, dict):
            raise ValueError(f"{where}.attrs 须为 object（无属性的算子写 {{}}）")
        for name, value in attrs.items():
            _check_attr_value(value, f"{where}.attrs.{name}")
        _check_materialize_plan(case, where)
    return payload, digest


def _check_materialize_plan(case, where):
    """校**造数规格**（取材侧 `materialize_plan` 的产物）——它是「这批字节怎么来的」的唯一契约。

    形状：``{generator_version, seed, seed_derivation, inputs:[{spec_name, shape, dtype,
    distribution, low, high|high_inclusive}]}``。取材侧不 import numpy（Layer 1 纪律），
    真正造数在本文件做；两边只靠这份规格对齐，**只要 `generator_version` 与 `seed` 不变，
    产出的字节就该逐位相同**。

    ⚠ 逐输入的 `spec_name / shape / dtype / 取值边界` 都要**与 case.inputs 交叉核**：
      规格与用例描述是同源派生的，对不上就说明有一处被改过。不核的话，一份
      「value_range 写着 [-1,1]、规格里却按 [-1e9,1e9] 造数」的产物能一路跑到底，
      而账本上还写着任务书的那个值域——典型的 fail-open。
    """
    mat = case.get("materialize")
    if not isinstance(mat, dict):
        raise ValueError(f"{where}.materialize 须为 object（generator_version / seed / inputs）")
    seed = mat.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 32:
        raise ValueError(
            f"{where}.materialize.seed={seed!r} 须为 [0, 2**32) 的整数（numpy 合法 seed 域）——"
            "输入字节的可复现性全靠它，缺了就没法说「重跑还是那批用例」")
    gen_ver = mat.get("generator_version")
    if isinstance(gen_ver, bool) or not isinstance(gen_ver, (int, str)) or gen_ver == "":
        raise ValueError(
            f"{where}.materialize.generator_version={gen_ver!r} 须为非空字符串或整数"
            "（物化规则版本，随产物留痕；换了版本就等于换了一批字节）")
    plans = mat.get("inputs")
    if not isinstance(plans, list) or len(plans) != len(case["inputs"]):
        raise ValueError(
            f"{where}.materialize.inputs 须与 case.inputs 一一对应（各 {len(case['inputs'])} 项），"
            f"得 {plans if not isinstance(plans, list) else len(plans)}")
    for j, (plan, item) in enumerate(zip(plans, case["inputs"])):
        pw = f"{where}.materialize.inputs[{j}]"
        if not isinstance(plan, dict):
            raise ValueError(f"{pw} 非 object")
        for key in ("spec_name", "dtype"):
            if plan.get(key) != item[key]:
                raise ValueError(
                    f"{pw}.{key}={plan.get(key)!r} ≠ case.inputs[{j}].{key}={item[key]!r}"
                    "——造数规格与用例描述必须同源，对不上即有人动过其中一处")
        if list(plan.get("shape") or []) != list(item["shape"]):
            raise ValueError(
                f"{pw}.shape={plan.get('shape')!r} ≠ case.inputs[{j}].shape={item['shape']!r}")
        dist = plan.get("distribution")
        if dist not in _TASKDOC_DISTRIBUTIONS:
            raise ValueError(
                f"{pw}.distribution={dist!r} 非本引擎可复现的分布，须属 {list(_TASKDOC_DISTRIBUTIONS)}"
                "——不认识的分布名一律 fail-closed，绝不「就当均匀分布」糊过去（那造出来的就不是"
                "任务书那条用例了）。取材侧的 `uniform_bool` 也不在此列：本引擎生成层不支持 bool 输入。")
        lo_src, hi_src = float(item["value_range"][0]), float(item["value_range"][1])
        if dist == _TD_DIST_INT:
            low, high = plan.get("low"), plan.get("high_inclusive")
            if (isinstance(low, bool) or not isinstance(low, int)
                    or isinstance(high, bool) or not isinstance(high, int) or low > high):
                raise ValueError(
                    f"{pw}: uniform_int 须给整数 low / high_inclusive 且 low<=high，"
                    f"得 low={low!r} high_inclusive={high!r}")
        else:
            low, high = plan.get("low"), plan.get("high")
            if not (_finite_number(low) and _finite_number(high)) or float(low) > float(high):
                raise ValueError(
                    f"{pw}: uniform_float 须给有限的 low / high 且 low<=high，"
                    f"得 low={low!r} high={high!r}")
        if (float(low), float(high)) != (lo_src, hi_src):
            raise ValueError(
                f"{pw} 的取值边界 [{low}, {high}] ≠ case.inputs[{j}].value_range "
                f"[{lo_src}, {hi_src}]——账本上写着任务书的值域、实际按另一个域造数，"
                "是最典型的 fail-open，一律拒")


def _taskdoc_binding(spec):
    """spec 侧对 taskdoc_caseset 的**逻辑身份声明**（可选）：`precision.taskdoc_caseset.sha256`。

    ⚠ 诚实边界：spec 是**先于**取材产物写出来的，首轮往往还没有那串 sha（写不出来就别编，AGENTS.md 5.8）。
    故本键**可省略**——省略时 gen_cases 仍把**实际**摘要落进 caseset / dry-run 账本，
    「本轮用的是哪一份用例集」因此始终可见；一旦写了就逐字核，对不上当场拒（防换掉用例集还沿用旧结论）。
    """
    prec = spec.get("precision") or {}
    if "taskdoc_caseset" not in prec:
        return None
    binding = prec["taskdoc_caseset"]
    if not isinstance(binding, dict):
        raise ValueError("precision.taskdoc_caseset 须为 object（当前只识别 sha256 一个键）")
    sha = binding.get("sha256")
    if sha is not None and not _hex64(sha):
        raise ValueError(
            f"precision.taskdoc_caseset.sha256={sha!r} 须为 64 位小写十六进制，或显式 null 表示本轮未绑定")
    return sha


def _taskdoc_dtype(case, in_params, where):
    """取该 taskdoc case 的**统一 dtype**（本引擎的 case 模型是「一条 case 一个 dtype」）。

    多输入 dtype 不一致 → fail-closed：`storage_dtype` / compare dtype / 落盘口径全部按单 dtype 派生，
    硬塞会静默按第一个输入的 dtype 存掉其余输入的字节。真要支持混合 dtype 得先一般化那条链，
    不是在这里放行（fail-closed 优于静默降级）。
    ⚠ dtype 名必须已是**本引擎的规范名**（float32/float16/bfloat16/int*/uint8）——
    任务书的 `"float"` 之类要在取材侧的映射 IR 里换完再进来，本文件不做第二套名字映射。
    """
    dts = {item["dtype"] for item in case["inputs"]}
    if len(dts) != 1:
        raise ValueError(
            f"{where}: 同一条 case 的输入 dtype 不唯一 {sorted(dts)}——"
            "本引擎按「一条 case 一个 dtype」落盘与判据，混合 dtype 须先一般化该链路，不在此静默取首个")
    dtn = dts.pop()
    if dtn not in set(_NATIVE) | {_BF16}:
        raise ValueError(
            f"{where}: dtype={dtn!r} 不是本引擎的规范 dtype 名"
            f"（可选 {sorted(set(_NATIVE) | {_BF16})}）——任务书侧的类型名须由取材侧的映射 IR 换成规范名，"
            "本文件不做第二套名字映射（两处映射必然漂）")
    for p in in_params:
        allowed = p.get("dtype") or []
        if dtn not in allowed:
            raise ValueError(
                f"{where}: dtype={dtn!r} 不在 spec 的输入参数 {p['name']!r} 声明的 dtype 集 {allowed} 里"
                "——任务书用例的 dtype 与 spec 声明打架，须先核对，不静默按 spec 或按任务书任一侧胜出")
    return dtn


def _taskdoc_plan(spec, in_params, attrs_default, case_target, taskdoc, taskdoc_sha256):
    """CS · `case_source=taskdoc` 的计划期：任务书用例 → plan entries + 覆盖账本。

    与 `_plan` 的关键区别（也是这一档存在的理由）：**一条网格都不铺、一次采样都不做**。
    entries 与任务书用例**一一对应**，身份、shape、attr、值域全部照抄；本引擎只负责物化与算 golden。

    硬校（全部 fail-closed）：
      · 输入/输出的 `spec_name` 必须与 spec 的 in/out 参数**同序同名同数**——任何一处对不上都说明
        接口映射没做对，此时继续跑只会产出「名字看着像、槽位其实错位」的用例；
      · attr 键集必须**精确等于** spec 的 attr 参数集——多一个是无处安放的属性，少一个下游要兜默认值
        （`_build_aclnn_call` 对 None 取值本来就 fail-closed）；
      · `case_target` 必须**精确等于**用例条数——同 `torch_parity` 完整矩阵的口径：
        账面声明与实产条数不许打架，否则报告里那个数就是编的。
    """
    perf_mode.normalize_change_kind(spec)                 # 与 `_plan` 同一道受控词表门，不留旁路
    if _uses_output_contract(spec):
        # 多输出契约 + 任务书用例集的组合本轮**未实现**（`golden_unavailable` 尚未接进多输出通路）。
        # 半截支持比不支持贵得多：宁可明说不支持，也不产一批「看着有裁决、其实判据链没接全」的用例。
        # 放在计划期 = 正式生成与 dry-run（CP-B 契约自检）两条路都拦得住。
        raise ValueError(
            f"{spec.get('op')}: precision.case_source='taskdoc' 暂不支持多输出契约"
            "（out 参数 >1 或声明了 out_role）——fail-closed，不半截支持")
    cases = taskdoc["cases"]
    in_names = [p["name"] for p in in_params]
    out_names = [p["name"] for p in spec.get("params", []) if p.get("io") == "out"]
    attr_names = set(attrs_default)
    if int(case_target) != len(cases):
        raise ValueError(
            f"precision.case_source='taskdoc'：任务书用例集有 {len(cases)} 条，"
            f"precision.case_target={case_target}——两者必须相等。"
            "账面数与实产数不一致时，报告里写的那个覆盖数就是编的（同 torch_parity 完整矩阵的口径）。")
    entries = []
    for i, case in enumerate(cases):
        where = f"taskdoc_caseset.cases[{i}]({case['case_id']})"
        got_in = [item["spec_name"] for item in case["inputs"]]
        if got_in != in_names:
            raise ValueError(
                f"{where}: 输入身份 {got_in} ≠ spec 的 in 参数 {in_names}（须同序同名同数）——"
                "接口映射对不上就停，绝不按位置硬塞（槽位错位的用例会「跑得过」但验的不是那件事）")
        got_out = [item["spec_name"] for item in case["outputs"]]
        if got_out != out_names:
            raise ValueError(
                f"{where}: 输出身份 {got_out} ≠ spec 的 out 参数 {out_names}（须同序同名同数）")
        if set(case["attrs"]) != attr_names:
            raise ValueError(
                f"{where}: attr 键集 {sorted(case['attrs'])} ≠ spec 的 attr 参数 {sorted(attr_names)}——"
                "缺的属性没人能替任务书补（下游对 None 取值一律 fail-closed），多的属性无处安放")
        dtn = _taskdoc_dtype(case, in_params, where)
        shapes = [tuple(item["shape"]) for item in case["inputs"]]
        entries.append({
            "dims": ["功能", "精度"],       # 性能维由 `_classify_perf_cases` 按 perf 策略事后挑选
            "shape": shapes[0],             # 账本/统计口径用首个输入的形状（逐输入形状在 taskdoc.inputs 里）
            "dtype": dtn,
            "tags": ["任务书用例"],
            "data_kind": "taskdoc",
            "id_kind": "taskdoc",
            "attrs": _copy_attrs(case["attrs"]),
            "attr_idx": None,               # 任务书用例不走 attr_matrix 组合索引，case_id 也不带 a{k}
            "case_origin": f"taskdoc_caseset:{case['case_id']}",
            "rule_ref": "任务书自带 self_test_case（spec.precision.case_source=taskdoc）",
            # 物化与溯源所需的原始描述，逐 case 随行（materializer 与落盘账本都读它）
            "taskdoc": {"case_id": case["case_id"], "inputs": case["inputs"],
                        "outputs": case["outputs"], "materialize": case["materialize"],
                        "content_sha256": case["content_sha256"]},
        })
    n = len(entries)
    meta = {
        "pool_max": n,
        "requested_target": int(case_target),
        "emitted": n,
        "forced_special": 0,
        "operator_class": _operator_class(spec),
        # 任务书用例里有没有非有限值由**任务书**说了算，本引擎这一档不强制铺 inf/-inf/nan。
        "emits_nonfinite_specials": False,
        "case_profile": _case_profile(spec),
        "case_profile_declared": _case_profile_declared(spec),
        "forced_total": n,                  # 一条都不许少：任务书用例集整体即强制下限
        "dropped_combo_classes": [],
        "unpaired_combo_classes": {"count": 0, "classes": [], "attr_values_never_emitted": []},
        "attr_axis_lengths": {"declared": [], "emitted": 0, "items": [], "skipped": []},
        # ⚠ 两档表述必须**不同**：这一档绝不能再声称「1-wise + 白名单」——那是本引擎自己铺网格时的
        #   覆盖强度，拿来描述任务书给的用例就是冒领。覆盖强度由任务书决定，我们只如实报条数。
        "coverage_strength": (
            f"taskdoc_provided：用例集由任务书提供（{n} 条，spec.precision.case_source=taskdoc），"
            "覆盖强度由任务书决定；本引擎不另铺正交网格、不做 1-wise 采样、不加白名单必覆盖组合"),
        # G4 规模预算在这一档**不行使**：降规模会把任务书点名的 shape 改掉，那就不是那条用例了。
        "golden_cost": _empty_cost_ledger(),
        "case_source": _CASE_SOURCE_TASKDOC,
        "taskdoc_caseset_sha256": taskdoc_sha256,
        "taskdoc_sha256": taskdoc["taskdoc_sha256"],
    }
    return entries, meta


def _materialize_taskdoc_inputs(entry, in_params, dtn):
    """CS · 按取材侧的**造数规格**确定性物化输入字节（这一步是本文件的活：取材侧不 import numpy）。

    确定性来源 = `materialize.seed`（取材侧从「任务书摘要 ‖ 单条 case 内容摘要」派生），
    **不掺 case_id、不掺时间、不掺顺序**：同一份 taskdoc_caseset 在任何机器上重跑都是同一批字节。
    多个输入按规格顺序从**同一个** rng 连抽（顺序固定 ⇒ 仍然确定）。

    ⚠ 取值边界**只从规格读**（`low` / `high` / `high_inclusive`），不再自己从 `value_range` 换算：
      「同一个数在两处各算一遍」正是两边悄悄分叉的经典入口；两者是否一致已在
      `_check_materialize_plan` 逐项核过，这里只执行。
    """
    td = entry["taskdoc"]
    mat = td["materialize"]
    rng = np.random.default_rng(int(mat["seed"]))
    cdt = _compute_np(dtn)
    arrays = []
    for plan in mat["inputs"]:
        shape = tuple(int(d) for d in plan["shape"])
        if plan["distribution"] == _TD_DIST_INT:
            # 闭区间：`high_inclusive` 这个名字就是契约，端点必须取得到。
            values = rng.integers(int(plan["low"]), int(plan["high_inclusive"]),
                                  size=shape, endpoint=True)
        else:
            values = rng.uniform(float(plan["low"]), float(plan["high"]), size=shape)
            if dtn == _BF16:                             # bf16 逻辑值必须落在 bf16 网格上（同常规通路）
                values = _bf16_round(np.asarray(values, dtype=np.float32))
        arrays.append(np.ascontiguousarray(values, dtype=cdt))
    return arrays


def _taskdoc_golden_or_unavailable(golden_fn, inputs, attrs, cid):
    """算 golden；算不出来时**不中断全量生成**，返回 `(None, 原因串)`。

    ⚠ 这正是 `golden_unavailable` 一等状态的入口：任务书用例集里总有几条超出参考实现的支持范围
    （实测：通道数 > OpenCV `CV_CN_MAX` 的那几条）。旧行为是任一条抛异常就**整轮生成中断**——
    于是 160 多条能跑的用例一条都产不出来，性能维直接零数据。现在改成逐条记账、其余照跑。
    捕 `BaseException` 与 `load_golden` 同理：golden 是用户/生成的代码，一句 `sys.exit(0)` 不是 `Exception`。
    """
    try:
        return golden_fn(inputs, attrs), None
    except KeyboardInterrupt:                            # 用户主动中断不该被记成「这条用例算不出 golden」
        raise
    except BaseException as ex:                          # noqa: BLE001 —— 见 docstring
        return None, f"{type(ex).__name__}: {ex}"


_TORCH_PARITY_AXIS_CLASSES = ("first_axis", "middle_axis", "last_axis")
_TORCH_PARITY_SHAPE_LAYOUTS = (
    "reference_leading_unit_padding",
    "axis_selector_selected_axes_nontrivial",
)


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


def _torch_parity_shape_layout(profiles):
    """据 attribute profile 的**接口能力信号**派生 shape 布局（不看算子身份）。

    `axis_class` 是 torch_parity 已有的轴选择器声明：出现它说明实际被测语义会沿某根轴归约/排序/
    索引，`(L,1,…,1)` 会把 middle/last 大量退化成长度 1；因此保留首位长轴，并只把 profiles
    **实际会选择的轴**提到至少 2。没有该信号时按参考仓原布局补 1，避免给纯 elementwise 平白
    放大 numel、改输入字节。

    返回值来自受控词表 `_TORCH_PARITY_SHAPE_LAYOUTS`，不得让任意 spec 字符串直接穿透到产物账本。
    """
    selected_classes = set()
    for profile_idx, (_profile_name, attrs) in enumerate(profiles):
        for key, value in attrs.items():
            if isinstance(value, dict) and "axis_class" in value:
                # 在派生布局时就过现成的受控词表校验；不能让拼错的 axis_class 先影响布局、
                # 再拖到生成循环中才报错。
                _resolve_axis_class(
                    value, 1,
                    f"torch_parity_matrix.attribute_profiles[{profile_idx}].attrs.{key}")
                selected_classes.add(value["axis_class"])
    ordered_classes = tuple(cls for cls in _TORCH_PARITY_AXIS_CLASSES if cls in selected_classes)
    layout = _TORCH_PARITY_SHAPE_LAYOUTS[1 if ordered_classes else 0]
    return layout, ordered_classes


def _torch_parity_shape(leading, rank, layout, selected_classes):
    """实例化受控 torch_parity 布局；未知布局属于内部判据漂移，fail-closed。"""
    if layout == "reference_leading_unit_padding":
        if selected_classes:
            raise ValueError("reference_leading_unit_padding 不得携带 axis_class（内部布局判据漂移）")
        return (leading,) + (1,) * (rank - 1)
    if layout != "axis_selector_selected_axes_nontrivial":
        raise ValueError(
            f"torch_parity shape layout={layout!r} 不在受控词表 {list(_TORCH_PARITY_SHAPE_LAYOUTS)}")
    if not selected_classes:
        raise ValueError("axis_selector_selected_axes_nontrivial 缺 axis_class（内部布局判据漂移）")
    shape = [1] * rank
    shape[0] = leading
    for cls in selected_classes:
        axis = _resolve_axis_class({"axis_class": cls}, rank, "torch_parity 内部 shape layout")
        shape[axis] = max(shape[axis], 2)
    return tuple(shape)


# ====== TP · 本档「声明了却没有任何代码消费」的 legacy 造例键 —— 声明即 fail-closed ==========
# `_plan` 在 `case_profile == "torch_parity"` 时**提前返回**（见该函数 `CP:` 分支），于是 legacy 那一整套
# 造例规则（特殊场景叠加 / value_profile / 轴长度定向生成 / attr 正交网格）在本档**一行都不执行**。
# 这些键写在 spec 里因此完全没有作用——可它们读起来恰恰像「我已经声明了所以已经覆盖了」。
# 这是**结构性 fail-open**，与 `operator_class` 那处 fail-closed 防的是同一件事，故：**本档写了就当场炸**。
#
# ⚠ 为什么一律选「拒绝」而不是「接上消费」（这条理由别下次顺手改掉）：决定②已明确
#   `torch_parity` **不新增特殊场景**；这些键要真被消费，
#   **无一例外都得改变本档用例集的构成**——
#     · `value_profiles` / `allow_empty_tensor` / `empty_axis` → 等于让本档开始产特殊场景；
#     · `attr_axis_lengths` → 等于在完整笛卡尔之外定向追加用例，`矩阵大小 == case_target == 实产数`
#       这条三重记账当场破；
#     · `attr_matrix` → 它与 `torch_parity_matrix.attribute_profiles` 是**同一条 attr 轴的两处声明**
#       （median 实测 4 组 vs 7 组、互不核对，已经漂了）。两者语义并不同构：前者是「每 attr 的取值集
#       再笛卡尔展开」，后者是「带 `axis_class` 符号的显式 profile 列表」——要「加一道交叉核对门」
#       就得先发明一套两边的对应关系，那既比删掉重复声明更弱、又是新的可漂移判据。
#       **一条轴只留一处声明**才是治本。
#   拒绝 = 落实决定②并封掉当下确实存在的 fail-open；消费 = 重新打开已定政策。
# ⚠ 维护约定：哪天本档真的开始消费其中某个键，**把它从本表删掉是那次改动的一部分**——
#   留在表里就变成「已经消费了却还在拒收」的反向坑。`test_gen_cases_case_profile` 对本表逐项立了 pin。
_TORCH_PARITY_UNCONSUMED_KEYS = (
    ("spec", "attr_matrix",
     "attr 轴在本档由 precision.torch_parity_matrix.attribute_profiles 唯一声明"),
    ("spec", "attr_axis_lengths",
     "本档不做轴长度定向生成（那会在完整笛卡尔之外追加用例，破坏「矩阵大小==case_target==实产数」）"),
    ("spec", "allow_empty_tensor",
     "本档一条特殊场景都不产（forced_special=0），空 Tensor 用例的开关无处可用"),
    ("spec", "empty_axis",
     "同 allow_empty_tensor：本档不产空 Tensor 用例，放 0 的轴号没有消费方"),
    ("precision", "value_profiles",
     "本档不产 nan/tie 等 value_profile 强制项（generator 受控词表只有 uniform 一档）"),
)


def _reject_unconsumed_legacy_keys(spec):
    """torch_parity 档：legacy 造例键**声明即拒**（理由见 `_TORCH_PARITY_UNCONSUMED_KEYS` 上方长注释）。

    一次报全部命中项，不是撞一个报一个——spec 作者一趟就能改干净。
    """
    precision = spec.get("precision") or {}
    hits = []
    for where, key, why in _TORCH_PARITY_UNCONSUMED_KEYS:
        holder = precision if where == "precision" else spec
        if isinstance(holder, dict) and key in holder:
            hits.append(f"  · {'precision.' if where == 'precision' else ''}{key}：{why}")
    if hits:
        raise ValueError(
            "precision.case_profile='torch_parity' 下，这些 legacy 造例键**没有任何代码消费**——"
            "写了不会产任何用例，却读起来像「声明即覆盖」→ fail-closed，请从 spec 里删掉：\n"
            + "\n".join(hits)
            + "\n  ⚠ 若你要表达的是**算子事实**（如「本算子不支持空 Tensor」），请写成 `_` 前缀的纯注释键"
              "（本仓惯例：`_` 开头 = 无消费方的说明），别用一个看起来像门的键去表达。")


# ====== TP · 决定②：特殊场景有意不产（reason + evidence 落账）=============================
def _torch_parity_special_scenario_policy(operator_class):
    """返回 ``torch_parity`` 的特殊场景政策账本；不生成 case。

    ``operator_class`` 已由 :func:`_operator_class` 校过受控词表；这里仍留一道直接调用守卫，
    防未来调用方绕过唯一解析口。整字段省略的 ``None`` 只为历史/测试夹具兼容，新 spec 仍按
    acc-spec 规则必须显式判类。

    ``case_target`` 只数常规矩阵：特殊场景由 §7.1 明定为独立叠加、不是矩阵轴。今天 emitted=0，
    所以总实产恰好仍等于 case_target；若未来证据足以重开，必须把特殊场景单独记账，不能把它偷塞
    进矩阵乘法，也不能放宽常规矩阵的三重等式。
    """
    if operator_class is not None and operator_class not in _OPERATOR_CLASSES:
        raise ValueError(
            f"torch_parity special policy 收到未知 operator_class={operator_class!r}；"
            f"须属 {list(_OPERATOR_CLASSES)} 或为历史未声明 None")
    return {
        "policy": "omit_until_measured_evidence",
        "operator_class": operator_class,
        "emitted": 0,
        "reason": (
            "空/标量/上下边界在现有 torch_parity 矩阵中没有实测收益证据；"
            "按与值域 regime 相同的证据门槛，本档不新增特殊场景"),
        "evidence": [
            ("dev-doc/oprunway-case-axis-design.md §12.15：决定②的现有实测输入为零，"
             "本轮矩阵未生成空/标量/上下边界，故没有数据可量化其收益"),
            ("参考仓 design_contract.py 明文支持 structural special=0；"
             "没有证据把该结论外推成其它 operator_class 应新增场景"),
        ],
        "case_target_relationship": "outside_cartesian_not_counted_in_case_target",
    }


# ====== TP · 三重记账：矩阵大小 − 有证据的排除 == case_target == 常规矩阵实产数 ==================
# 轴名受控词表。`excluded` 里出现词表外的轴名 = 排除了一条根本不存在的轴，当场炸。
_TORCH_PARITY_AXIS_NAMES = ("dtype", "rank", "shape_profile", "attribute_profile")


def _torch_parity_excluded(cfg, axes):
    """`torch_parity_matrix.excluded` → `(被排除的完整组合集, 逐条账本)`；键缺席 = 无排除。

    形式（每条排除**必须**带 `reason` + `evidence`，沿用 `golden_cost.skipped_shapes` /
    `dropped_combo_classes` 已有的「缩水必须留痕」形状）：

        "excluded": [{"combo": {"dtype": "int8", "attribute_profile": "attr_03"},
                      "reason": "…", "evidence": "…"}]

    `combo` 是**部分赋值**：只写要钉死的轴，其余轴全展开。四条轴的取值来自矩阵本身
    （`axes` = [(轴名, 取值列表)]），所以「排除了一个不存在的取值」当场就能逮住。

    ⚠ 这里刻意**不引入**一份独立的 `case_matrix.axes` 声明：那会让同一条轴在 spec 里出现两处
    （正是 `attr_matrix` 已经踩过的坑）。轴的唯一声明仍是 `torch_parity_matrix` 自己，
    而它本来就是**列取值、不是列基数**——`ranks` / `shape_profiles` / `attribute_profiles` 逐项可读，
    dtype 轴逐项来自 `params[in].dtype`。
    """
    if "excluded" not in cfg:
        return frozenset(), []
    raw = cfg["excluded"]
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            "torch_parity_matrix.excluded 出现即须为**非空**列表；"
            "空列表是「有排除账本」的假象，要没有排除就整个键别写")
    axis_map = dict(axes)
    combos, rows = set(), []
    for i, item in enumerate(raw):
        where = f"torch_parity_matrix.excluded[{i}]"
        if not isinstance(item, dict) or set(item) != {"combo", "reason", "evidence"}:
            raise ValueError(f"{where} 须恰含 combo/reason/evidence 三个键（缩水必须带理由 + 证据）")
        for key in ("reason", "evidence"):
            if not isinstance(item[key], str) or not item[key].strip():
                raise ValueError(f"{where}.{key} 须为非空字符串——没有理由/证据的排除就是静默缩水")
        combo = item["combo"]
        if not isinstance(combo, dict) or not combo:
            raise ValueError(f"{where}.combo 须为非空字典（部分赋值：只写要钉死的轴）")
        unknown = set(combo) - set(_TORCH_PARITY_AXIS_NAMES)
        if unknown:
            raise ValueError(
                f"{where}.combo 含未知轴名 {sorted(unknown)}，受控词表 {list(_TORCH_PARITY_AXIS_NAMES)}")
        for name, value in combo.items():
            if value not in axis_map[name]:
                raise ValueError(
                    f"{where}.combo.{name}={value!r} 不在本矩阵的 {name} 取值 {axis_map[name]} 里——"
                    "排除一个根本不在矩阵里的取值，记账必然对不上")
        # 部分赋值 → 完整组合：钉死的轴取那一个值，其余轴全展开（轴序 = `axes` 的声明序）。
        expanded = set(itertools.product(*[
            [combo[name]] if name in combo else values for name, values in axes]))
        overlap = combos & expanded
        if overlap:
            raise ValueError(
                f"{where}.combo 与前面的排除项重叠（{len(overlap)} 个组合，如 {sorted(overlap)[0]}）——"
                "重叠会让排除数被重复计一次，三重记账当场失真；请合并或改窄")
        combos |= expanded
        rows.append({"combo": dict(combo), "reason": item["reason"], "evidence": item["evidence"],
                     "combos_excluded": len(expanded)})
    return frozenset(combos), rows


def _torch_parity_combination_stats(entries):
    """实产用例的**覆盖组合**统计：`(不同组合数, 重复条数)`。

    组合身份 = `(dtype, 实际 shape, **解析后**的 attrs)`。⚠ 刻意**不含** profile 名：
    `_mk_id` / `_entry_key` 的去重键里带 `id_kind`（profile 名在里面），那是**文件名唯一性**保证，
    不是**覆盖唯一性**保证——低 rank 下 `axis_class` 会塌缩（rank1 时 first=middle=last=0），
    6 个 by-dim profile 解析后只剩 2 个不同 attrs，profile 名不同所以一条都不会被那两处逮到。
    这里数的就是它们：这些 case 的输入字节确实不同（种子吃 `case_id`），是**同一覆盖组合的额外随机样本**，
    有价值，但**不能按「不同组合」计数**。
    """
    keys = {(e["dtype"], tuple(e["shape"]),
             tuple(sorted(((k, _attr_hashable(v)) for k, v in e["attrs"].items()),
                          key=lambda kv: kv[0])))
            for e in entries}
    return len(keys), len(entries) - len(keys)


def _torch_parity_plan(spec, in_params, dtypes, attrs_default, case_target, cost_fn):
    """按 cannbot 冻结设计的轴模型生成完整笛卡尔矩阵。

    配置位于 ``precision.torch_parity_matrix``，只在
    ``case_profile=torch_parity`` 下消费：

    * ``ranks``：完整 rank 轴；
    * ``shape_profiles``：每档 ``leading_dim``；无轴选择器时其余轴按参考布局补 1，存在
      ``axis_class`` 轴选择器时把 profiles 实际选择的轴提到至少 2，保留首轴长归约；
    * ``attribute_profiles``：显式属性 profile，轴属性可写
      ``{"axis_class":"first_axis|middle_axis|last_axis"}``；
    * ``generator``：当前只接受 cannbot Median 冻结设计使用的 uniform；
    * ``excluded``（选填）：**有证据的排除项**，每条带 ``reason`` + ``evidence``（见
      ``_torch_parity_excluded``）。

    完整矩阵不受 1-wise/case_target 抽样。**三重记账**（任一处漂移当场炸）：

        ∏|轴取值| − |excluded 展开后的组合| == precision.case_target == regular_emitted

    特殊场景不计入这条等式，单独以 ``special_emitted`` 记账。第一重防「声明 1152 全覆盖却
    静默只取 60 条」；第三重防「有了排除项之后，常规矩阵实产数与账面数
    靠『完整笛卡尔不采样』这个实现细节隐式对齐」——那条隐式保证一旦有排除就断了。

    ⚠ 本档**不进笛卡尔**的两样东西（已有明文依据，不是省事）：

    * **特殊场景独立叠加、绝不与常规网格交叉**——参考仓 ``design_contract.py`` 明文，
      理由逐字就是避免组合爆炸；本仓 legacy 也是这么做的（特殊场景只配 ``attr_combos[0]``，
      见 ``_plan`` 的 ①）。代价要认：「空 Tensor × 按维归约」这类组合因此永远测不到。
      决定②已按「原计划忠实度 + 最短实施」收敛为本档 ``forced_special=0``：现有实测没有
      空/标量/上下边界的收益输入，与值域 regime 同样不在零证据下扩面。reason + evidence 见
      ``special_scenario_policy``。这不改变「未来若重开也只能独立叠加」的结构约束。
    * **输出个数不是自由轴**，它是 attr 轴的**确定性函数**——``_select_call_variant(variants, attrs, cid)``
      从 attrs 选调用变体，输出集随之定死。当轴放进笛卡尔 = 重复计数，还会造出
      「dim=null 且要求 indices 输出」这种不存在的组合。``_build_multi_output_case`` 那处
      「声明输出数与 golden 实际返回数必须恰好相等」的门是同一口径，别在轴集里开第二个口。
    """
    _reject_unconsumed_legacy_keys(spec)
    cfg = (spec.get("precision") or {}).get("torch_parity_matrix")
    if not isinstance(cfg, dict):
        raise ValueError(
            "precision.case_profile='torch_parity' 时必须声明 "
            "precision.torch_parity_matrix（不再沿用 legacy 造例规则）")
    allowed = {"source", "source_sha256", "ranks", "shape_profiles",
               "attribute_profiles", "generator", "excluded"}
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

    operator_class = _operator_class(spec)
    special_policy = _torch_parity_special_scenario_policy(operator_class)

    shape_layout, selected_axis_classes = _torch_parity_shape_layout(normalized_profiles)
    if shape_layout == "axis_selector_selected_axes_nontrivial":
        unit_profiles = [name for name, leading in shape_rows if leading < 2]
        if unit_profiles:
            raise ValueError(
                "torch_parity 带 axis_class 轴选择器时 shape_profiles[].leading_dim 须为 ≥2，"
                f"否则首轴仍是平凡归约；违规 profile={unit_profiles}")

    # 三重记账第一、二重（**在原处改**，不另立一套判据：另写一份的后果是两处判据必然漂移，
    # 而漂移方向一定是宽的那边赢）。`axes` 只从矩阵自身派生，故轴仍是**列取值、不列基数**。
    axes = (("dtype", list(dtypes)),
            ("rank", list(ranks)),
            ("shape_profile", [name for name, _ in shape_rows]),
            ("attribute_profile", [name for name, _ in normalized_profiles]))
    full_cartesian = 1
    for _name, _values in axes:
        full_cartesian *= len(_values)
    excluded_combos, excluded_rows = _torch_parity_excluded(cfg, axes)
    expected = full_cartesian - len(excluded_combos)
    if expected < 1:
        raise ValueError(
            f"torch_parity_matrix.excluded 排掉了全部 {full_cartesian} 个组合，一条用例都不剩——"
            "零用例空跑不能冒充验收")
    if int(case_target) != expected:
        raise ValueError(
            f"torch_parity 完整矩阵大小={full_cartesian}"
            + (f" − 有证据的排除 {len(excluded_combos)} = {expected}" if excluded_combos else "")
            + f"，precision.case_target={case_target}；两者必须相等，禁止静默抽样")
    entries = []
    for dtn in dtypes:
        dk = _regular_data_kind(dtn, attrs_default, len(in_params))
        for rank in ranks:
            for shape_name, leading in shape_rows:
                shape = _torch_parity_shape(leading, rank, shape_layout, selected_axis_classes)
                for attr_idx, (profile_name, raw_attrs) in enumerate(normalized_profiles):
                    if (dtn, rank, shape_name, profile_name) in excluded_combos:
                        continue                 # 已带 reason+evidence 记账，见 case_matrix_ledger
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
                            "attribute_profile_matrix（完整笛卡尔）；"
                            f"shape_layout={shape_layout}（按 axis_class 接口能力派生）"),
                    })
    # 三重记账第三重：**实产数**。前两重（矩阵大小、case_target）是账面对账面，
    # 这一重才把「循环真的产了几条」接进来。今天它靠「完整笛卡尔不采样」隐式成立，
    # 一旦有 excluded / 循环被改动，隐式保证就断了——所以显式立一道，别指望下一个人记得。
    regular_entries = entries
    special_entries = []                    # 决定②：有意不产；未来重开也须保持独立列表、不得混进矩阵
    regular_emitted = len(regular_entries)
    special_emitted = len(special_entries)
    if special_emitted != special_policy["emitted"]:
        raise ValueError(
            f"torch_parity 特殊场景实产 {special_emitted} 条 ≠ 政策账本 {special_policy['emitted']} 条；"
            "特殊场景政策与生成逻辑已经漂了，绝不放行")
    entries = regular_entries + special_entries
    total_emitted = len(entries)
    if regular_emitted != expected:
        raise ValueError(
            f"torch_parity 常规矩阵实产 {regular_emitted} 条 ≠ 账面 {expected} 条"
            f"（完整笛卡尔 {full_cartesian} − 有证据的排除 {len(excluded_combos)}）；"
            "生成循环与记账已经漂了，绝不放行")
    # 覆盖组合统计只属于常规矩阵；特殊场景是独立叠加，未来即使重开也不能混入这份矩阵账。
    distinct_combinations, duplicate_cases = _torch_parity_combination_stats(regular_entries)
    return entries, {
        "pool_max": expected,
        "requested_target": expected,
        "emitted": total_emitted,
        "forced_special": special_emitted,
        "special_scenario_policy": special_policy,
        "operator_class": operator_class,
        "emits_nonfinite_specials": False,
        "case_profile": "torch_parity",
        "case_profile_declared": True,
        "forced_total": total_emitted,
        "dropped_combo_classes": [],
        "unpaired_combo_classes": {
            "count": 0,
            "classes": [],
            "attr_values_never_emitted": [],
        },
        "attr_axis_lengths": {"declared": [], "emitted": 0, "items": [], "skipped": []},
        # ⚠ 措辞**如实**，别再写「N 个组合全覆盖」：报告是逐字引这句话的。
        #   原文「complete_cartesian：… 全覆盖」是**过强**的表述——实测 median 1344 例里有 144 例
        #   与同批另一例的 (dtype, 实际 shape, 解析后 attrs) 完全相同（低 rank 下 axis_class 塌缩），
        #   所以那不是 1344 个不同组合。本轮**不删重复**（删了 `矩阵大小 == case_target` 当场破，
        #   属待拍板项），只把话说对，并把重复条数落进 `case_matrix_ledger` 让报告能引。
        "coverage_strength": (
            ("complete_cartesian" if not excluded_combos else "cartesian_minus_excluded")
            + f"：dtype×rank×shape_profile×attribute_profile 完整笛卡尔 {full_cartesian} 组合"
            + (f" − 有证据的排除 {len(excluded_combos)} 组合" if excluded_combos else "")
            + f" → 常规矩阵实产 {regular_emitted} 例、无抽样"
              "（矩阵大小 == case_target == 常规矩阵实产数，三重逐字相等）；"
            + "特殊场景独立于笛卡尔且不计入 case_target，本档决定②有意不产（0 例）；"
            + f"shape_layout={shape_layout}（"
              + (f"检测到 axis_class={list(selected_axis_classes)}，保留首轴长归约，"
                 "并把这些 class 在各 rank 的实际落点提到至少 2（未被选择的轴仍可为 1）"
                 if shape_layout == "axis_selector_selected_axes_nontrivial" else
                 "未检测到 axis_class，shape=(L,1,…,1)，沿参考仓 elementwise 布局")
              + "）；"
            + (f"其中 {duplicate_cases} 例与同批另一例的 (dtype, 实际 shape, **解析后** attrs) 完全相同"
               f"（低 rank 下 axis_class 塌缩，如 rank1 的 first/middle/last 同为轴 0），"
               f"故**不同覆盖组合数 = {distinct_combinations}**——"
               f"报告只能按这个数说覆盖，不得把 {regular_emitted} 当成不同组合数"
               if duplicate_cases else
               f"{regular_emitted} 例互不相同，不同覆盖组合数 = {distinct_combinations}")),
        "golden_cost": ({
            "budget": _cost_budget(spec), "model": _COST_MODEL,
            "scaled_cases": [], "skipped_shapes": [], "skipped_shape_classes": 0,
        } if cost_fn is not None else _empty_cost_ledger()),
        "torch_parity_matrix": {
            "source": cfg.get("source"),
            "source_sha256": cfg.get("source_sha256"),
            "ranks": list(ranks),
            "shape_profiles": [dict(row) for row in shapes],
            "shape_layout": shape_layout,
            "selected_axis_classes": list(selected_axis_classes),
            "attribute_profile_count": len(normalized_profiles),
            "generator": dict(generator),
        },
        # 三重记账 + 覆盖组合的**机器可读账本**（报告/门读这里，不必回头人肉转述）。
        # 只在 torch_parity 档出现 → legacy 侧 caseset 字节纹丝不动。
        "case_matrix_ledger": {
            "axes": [{"name": name, "values": list(values)} for name, values in axes],
            "full_cartesian": full_cartesian,
            "excluded": excluded_rows,
            "excluded_total": len(excluded_combos),
            "expected": expected,
            "case_target": int(case_target),
            # `emitted` 保留给既有消费者，值明确等于**常规矩阵**实产；special 独立列账。
            "emitted": regular_emitted,
            "regular_emitted": regular_emitted,
            "special_emitted": special_emitted,
            "total_emitted": total_emitted,
            "distinct_combinations": distinct_combinations,
            "duplicate_cases": duplicate_cases,
            "note": (
                "三重记账：full_cartesian − excluded_total == expected == case_target == "
                "regular_emitted（兼容键 emitted 同值）；特殊场景不进笛卡尔且不计入 case_target，"
                "另以 special_emitted 记账，total_emitted = regular_emitted + special_emitted。"
                "任一处漂移 gen_cases 当场 fail-closed。"
                "duplicate_cases = 与同批另一例 (dtype, 实际 shape, 解析后 attrs) 完全相同的条数"
                "（输入字节仍不同——种子吃 case_id，故它们是同一覆盖组合的额外随机样本，"
                "有价值但不得按『不同组合』计数）；distinct_combinations 才是不同覆盖组合数。"),
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
    if precision_policy.is_complex_dtype(dtn):
        # 正常路径到不了这里（`_pick_vp_dtype` 只从 `_VP_DTYPE_PREF` 的实数浮点里挑代表 dtype）；
        # 这道门是防绕过。nan profile 的理由同 `_make_nanpair`（复数 NaN 有三种字节形态）；
        # tie profile 的理由是并列判据建立在**序**上（`_assert_tie_per_axis` 用 `np.sort`，
        # numpy 对复数按「先实部后虚部」的字典序排——那是 numpy 的实现约定，不是任何精度标准的口径）。
        raise ValueError(
            f"value_profile={profile!r} 对复数 dtype={dtn!r} 无口径（NaN 字节形态三选一无出处；"
            f"tie 依赖的序在复数上只有 numpy 的字典序约定，非标准口径）—— fail-closed，不猜。")
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


def check_spec_capability(in_params, runner_form):
    """引擎**能力边界**的 spec 级预检——`gen_cases()` 与 `_dry_run()` 共用，故 CP-B 契约自检就能拦住。

    ⚠ dtype 白名单**不再自带一份硬表**（U3）：真机侧一律问 `repo_adapter.supported_np(runner_form)`
    （+ `deferred_np` 的 Track-C 挂账集），生成侧问本模块的 `_NATIVE`——**两处口径不一致**曾把任务书
    8 类 dtype 压到 4/8（int64/int8/uint8 被生成端挡掉，覆盖率是被工具而非算子限住的）。

    ⚠ `runner_form` 是**必填形参，本层不存在「未指定」这一档**（P5-b，2026-08-05）。缺省口径只在
    读 spec 的那一步（`repo_adapter.spec_runner_form`）生效，读出来是什么就原样传进来。
    历史病灶：这里曾有个 `runner_form=None` 默认值，而 `spec.runner_form` **显式写成 null** 时
    `spec_runner_form` 返回的正是 `None`——两种意思撞成同一个值，于是一份写坏的 spec 被
    `resolve_runner_form(None)` 兜成当前唯一准入形态，过了 dtype 门，却因为调用方那边比的是原始
    `None` 而**不要求** `call_variants`，产出没有 `aclnn_call` 的 caseset。把默认值删掉，
    `None` / `""` / `0` 就一路走到 `supported_np` 的受控词表处 fail-closed。
    真需要「按缺省形态问一问」的调用方，显式传 `repo_adapter.DEFAULT_RUNNER_FORM`。

    为什么必须有：`_build_inputs` 的常规 `varied` / `pair*` 路径末尾写死 `return [x0, x1]`（二元构造），
    而 `empty` 与特殊值路径按 `arity` 产满——**arity≥3 时多出来的输入被无声丢掉，两边行为还不一致**。
    与其静默截断，不如明说不支持（本仓纪律：**fail-closed 优于静默降级**）。
    支持多输入算子须先一般化 pair 构造，见 `dev-doc/oprunway-todo.md` 的 U7b。"""
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
    # ⚠ 这里**不再做任何归一**（P5-b）：形参必填，`None` 与 `""`、`0`、`"opaque"` 一样是非法 form，
    #   全部交给下面的受控词表当场炸。缺省只在 `spec_runner_form` 那一步吃，本层再兜一次就是
    #   把「spec 写坏了」洗成「调用方没给」。
    form = runner_form
    runner_set = repo_adapter.supported_np(form)       # 未知 form 在此 fail-closed（不兜任何一支）
    deferred_set = repo_adapter.deferred_np(form)
    gen_set = set(_NATIVE) | {_BF16}
    for dtn in dtypes:
        gen_ok = dtn in gen_set
        run_ok = dtn in runner_set or dtn in deferred_set
        if not (gen_ok and run_ok):
            raise ValueError(_dtype_layer_error(dtn, form, gen_ok, run_ok, runner_set, deferred_set))


def _build_inputs(rng, in_params, shp, dtn, attrs, data_kind, runner_form):
    """造该 case 的**逻辑**输入数组列表（compute dtype；bf16=fp32-on-grid）。物理化在保存步单独做。
    data_kind 形如 base 或 base:regime（regime∈{uniform,normal}，仅 varied/pair 系用）；
    特殊 base：empty(§1.4 空)/inf/ninf/nan(§1.4 特殊值)。
    `runner_form` **必填**（同 `check_spec_capability`，P5-b）：兜底预检必须校在与上游**同一层
    口径**上，且这一层不得再有「未指定」——那个默认值曾让显式 `null` 与「没传」撞成同一个值。"""
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
# 决策 v2（dev-doc/oprunway-cases50-design.md）：dtype 分层（key 重点 + 其他 1-2）× shape 阶梯(2^k/2^k-1)
# × 值域(uniform+normal) × attr 正交笛卡尔；白名单强制必覆盖组合 + 1-wise 采样 + case_target 预算封顶；
# §1.4 特殊场景（空→功能only / 标量 / 边界 / inf·nan）强制纳入、id_kind 独立命名空间；per-case 独立种子。
# format 轴：elementwise 仅 ND（op_def/example 佐证）→ 退化为单值，不进正交网格。
KEY_DTYPES = ("float32", "float16", "bfloat16")     # §重点覆盖档
_OTHER_DTYPE_QUOTA = 2                               # 非重点 dtype 每种至多 N 条（主流场景）
# ⚠ **`case_target` 没有缺省值，别再加回来**（2026-08-06 删掉原 `_DEFAULT_CASE_TARGET = 50`）。
#   删的理由是实测：extractor 照散文里的「建议 50」自己填了 50、全程 0 次被审视，
#   792 个候选组合就这么留了 50 条——「用例数没人定过」这件事被一个缺省值静默吞掉了。
#   缺省值在这里等价于 fail-open：它让「谁定的用例数、依据是什么」永远不必回答。
#   现在缺这个键就 fail-fast（读取点唯一：`_require_case_target`）。

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


def _require_case_target(spec):
    """§1 用例预算 `precision.case_target` 的**唯一读取点**：必须由 spec 显式声明，**无缺省值**。

    两道校验、都 fail-closed：
    ① **键缺席** → 报错。原先缺席回落 50，实测的后果是「没人定过用例数」被静默吞掉
       （extractor 照散文的「建议 50」自己填，全程 0 次被审视）。缺省值在这里就是 fail-open。
    ② 键在但不是 ≥1 的整数（含 `True`/`False`——`bool` 是 `int` 的子类，不先挡掉
       `case_target: true` 会被当成 1）→ 报错，堵零用例空跑冒充验收。

    ⚠ `gen_cases` 与 `_dry_run` **必须共用本函数**：dry-run 是 CP-B 的契约自检，
    它若比真跑宽松一格，就是一道「自检过了、真跑照崩」的假门。
    """
    precision = spec.get("precision") or {}
    if "case_target" not in precision:
        raise ValueError(
            "precision.case_target 缺失：用例数须由 spec 显式声明，**无缺省值**——"
            "缺省会让『这个算子该造多少条用例、依据是什么』永远不必回答。"
            "torch_parity 档按完整笛卡尔矩阵大小填（须与矩阵精确相等）；"
            "其它档须给出该数字的依据，不许随手填一个。")
    case_target = precision["case_target"]
    if isinstance(case_target, bool) or not isinstance(case_target, int) or case_target < 1:
        raise ValueError(f"precision.case_target 须为 ≥1 的整数（防零用例空跑冒充验收），得 {case_target!r}")
    return case_target


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
    # 改动类别受控词表（`spec.change.kind`）在此**读一次**、词表外当场 fail-closed。
    # 放在计划期而不是各消费方现用现读，理由同 `case_profile`：gen_cases 与 dry-run 两条路径都过 `_plan`，
    # 写错的 kind 没有绕过口。⚠ 这个字段此前**只是文档字段、无代码消费方**——写错没人管，
    # 而它现在会决定「本轮要不要做性能对比」（perf_mode.derive_mode 支线①），必须真校。
    perf_mode.normalize_change_kind(spec)
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
        # `is_float` 在 `_special_entries` 里的语义是「这个 dtype 该不该铺 inf/-inf/nan」，
        # 判据必须是**实数浮点**：复数既非整型（不会被原来那半个条件挡住）、又没有权威的
        # 非有限字节形态（`inf+0j` / `0+infj` / `inf+infj` 三选一无出处），故显式排除。
        # 这是**声明式收窄**，不是漏——`_build_value_special` 那边还有一道同理由的 fail-closed。
        is_float = not (precision_policy.is_integer_dtype(dtn)
                        or precision_policy.is_complex_dtype(dtn))
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
#: **标量** attr 的 spec dtype → runner 的 ctype token。词表须与 `aclnn_runner._ATTR_CTYPES`
#: （`{int64,bool,float32,float64}`）**逐字对齐**：runner 按这个 token 选 ctypes 类型拼 argtypes，
#: 对不上就是 C ABI 宽度错位。`"double"` 是 spec 侧常见拼法（`isclose.spec.json` 的 rtol/atol 就写
#: `["double"]`），在此归一到 `float64`，不新增第五个 token。
#: ⚠ 旧表把 float32 映成 `"float"` —— 那是个**死 token**：runner 有专门的拒绝分支（C float/double 宽度
#: 不同，须写 float32/float64），所以 aclnn_py 通路上任何浮点 attr 过去都必然 fail-closed。已删。
_ATTR_CTYPE_MAP = {"int64": "int64", "bool": "bool", "float32": "float32",
                   "float64": "float64", "double": "float64"}

#: 数组型 attr 的 ctype token（与 runner 侧同名）。元素宽度由 ACL 的 `aclIntArray` 固定，
#: **不由 spec dtype 派生**，故它不进 `_ATTR_CTYPE_MAP`（那张表是标量 C ABI 宽度表）。
_ATTR_ARRAY_CTYPE = "int_array"

#: “调用方没给值”的哨兵——`None` 是合法 attr 值（表示省略/缺省语义），不能拿它当“未传”。
_UNSET = object()


def _is_int_array(v):
    """值结构判定：非空 `list[int]`（bool 元素拒，与 `_check_attr_value` 同口径）。"""
    return (isinstance(v, list) and bool(v)
            and all(isinstance(d, int) and not isinstance(d, bool) for d in v))


def _attr_ctype(p, value=_UNSET):
    """attr 参数的 aclnn ctype token：int64→"int64"、bool→"bool"、float32→"float32"、
    float64/double→"float64"；**值是 `list[int]` → "int_array"**（`aclIntArray*` 形参）。

    数组与标量的分流**只看值的结构**（op-中立：任何声明了 `list[int]` attr 的算子零改即用），
    不看算子身份、也不新增 spec 字段。`value` 未传时退回看 `p["default"]` 的结构——
    静态预检（`preflight_aclnn._abstract_slots`）只有 spec、没有 per-case 取值，靠这条得出同一答案。

    ⚠ dtype 候选必须**恰有一个**且在映射表内（审计 finding #5）：原先取 `dt[0]`，`["int64","int8"]` /
    `["float32","bogus"]` 都被静默收下 —— 而 attr 的 C 标量宽度拼错 = 远端 argtypes 错位 = 段错误。
    多候选 / 空 / 未知一律 fail-closed（记 gap 交人裁，别静默挑一个）。
    数组分支不查这张表：`aclIntArray` 的元素宽度是 ACL 定死的，spec dtype 在那里不表示 C 宽度。"""
    probe = p.get("default") if value is _UNSET else value
    if _is_int_array(probe):
        return _ATTR_ARRAY_CTYPE
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
            f"（仅 {sorted(_ATTR_CTYPE_MAP)}；数组属性请给 list[int] 取值 → {_ATTR_ARRAY_CTYPE}），"
            f"fail-closed（记 gap，别静默塞默认）")
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
      · `{"role":"attr","name":..,"ctype":..,"value":..}` —— 已解析的实参（**None 一律 fail-closed**）；
        ctype 由**值的结构**分流：标量 → `int64/bool/float32/float64`；非空 `list[int]` → `int_array`
        （runner 侧建 `aclIntArray*`）。分流不看算子身份，只看这一个 case 里该 attr 的取值长什么样；
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
            ctype = _attr_ctype(p, value)
            # 数组值另拷一份：variant["attrs"] / case attrs 里的那个 list 会被多条 case 共享，
            # 落进 caseset 的 slot 不该与它同一个对象（谁就地改一下就串到别的 case）。
            slots.append({"role": "attr", "name": name, "ctype": ctype,
                          "value": list(value) if isinstance(value, list) else value})
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


def _resolve_taskdoc_inputs(spec, taskdoc_caseset):
    """CS：解出本轮的 `(case_source, taskdoc_payload, taskdoc_sha256)`；两向都 fail-closed。

    · `case_source=taskdoc` 却没喂 caseset → 拒。**绝不回退自生成**：任务书明确给了用例，
      拿不到文件时自己造一套，就是再产一次「流程看着通过、实际绕过任务书用例」的产物；
    · 喂了 caseset 却没声明 `case_source=taskdoc` → 也拒。静默忽略一份喂进来的用例集，
      等于让调用方以为用的是任务书用例、实际跑的是自生成网格。
    """
    case_source = _case_source(spec)
    if case_source != _CASE_SOURCE_TASKDOC:
        if taskdoc_caseset is not None:
            raise ValueError(
                f"传入了 taskdoc_caseset={taskdoc_caseset!r}，但 spec.precision.case_source="
                f"{case_source!r} —— 要用任务书用例请显式声明 case_source='taskdoc'；"
                "这里不静默忽略喂进来的用例集（静默忽略 = 调用方以为验的是任务书用例，实际不是）")
        return case_source, None, None
    if not taskdoc_caseset:
        raise ValueError(
            "spec.precision.case_source='taskdoc' 但未喂入规范化用例集 —— "
            "请用 gen_cases(spec, work_dir, taskdoc_caseset=<taskdoc_caseset.json>) 或 CLI "
            "`--taskdoc-caseset <path>` 显式给出。**绝不回退自生成**：任务书明确提供了用例，"
            "识别不到就该 BLOCKED 并保留原因，不是自己造一套顶上（fail-closed）。")
    payload, digest = load_taskdoc_caseset(taskdoc_caseset)
    declared = _taskdoc_binding(spec)
    if declared is not None and declared != digest:
        raise ValueError(
            f"taskdoc_caseset 摘要漂移：spec.precision.taskdoc_caseset.sha256={declared} ≠ "
            f"实际 {digest}（{taskdoc_caseset!r}）—— 用例集换过了，旧结论不适用，fail-closed")
    return case_source, payload, digest


def gen_cases(spec, work_dir, taskdoc_caseset=None):
    op = spec["op"]
    # golden 按算子从用户侧 <ops_root>/<op>/golden.py 加载（elementwise 通路不内置 golden 值、缺则 fail-closed；
    # ADR 0011 决策 1/2/5，proposed）。⚠ 非「引擎零内置算子」——catlass_adapter 的 matmul golden 与本文件 :34
    # 的 _BF16_EXACT_OPS 是两处已知例外，仍是引擎里的算子知识。
    # golden_source 来自加载的 GOLDEN_SOURCE 元数据（决策 5），下游门继续校 oracle_source==映射(golden_source)。
    in_params = [p for p in spec["params"] if p["io"] == "in"]
    import repo_adapter                                  # 延迟 import：repo_adapter 顶层已 import gen_cases
    # dtype 能力门 + `aclnn_call` 需求都按 form 分派（U3）。⚠ 缺省口径**必须**与 run_workflow 的
    # mode 派生同源，否则同一份省略了该键的 spec 会被规划成 cpp、却被派去跑 cpp_extension（P5）。
    runner_form = repo_adapter.spec_runner_form(spec)
    check_spec_capability(in_params, runner_form)        # 能力边界前置：先于 load_golden，别为不支持的算子白加载 golden
    # CS：用例来源（generated / taskdoc）与规范化任务书用例集，**在加载 golden 之前**解出来——
    # 一份「声明了 taskdoc 却没喂用例集」的 spec 应当停在零副作用处，而不是先 import 一遍用户 golden。
    case_source, taskdoc_payload, taskdoc_sha256 = _resolve_taskdoc_inputs(spec, taskdoc_caseset)
    # §1 用例预算 `spec.precision.case_target`（**必填、无缺省**，见 `_require_case_target`）。
    # < 强制下限时 _plan 用 max(target,|forced|)、emit>target 并 note（评审 #8）。
    # ⚠ **位置刻意夹在这里**，两侧都是有意的：
    #   · 排在 `check_spec_capability` / `_resolve_taskdoc_inputs` **之后** —— 与
    #     `_build_dry_run_ledger` 的次序**逐字一致**。两条路的报错优先级一旦分叉，
    #     「CP-B 自检报 A、CP-D 真跑报 B」就会把同一份坏 spec 说成两回事。
    #   · 排在 `load_golden` / `os.makedirs` **之前** —— 那两步会**执行**用户侧 `golden.py`
    #     （顶层副作用照跑）并真建目录；把「这份 spec 压根没定过用例数」拖到它们之后才报，
    #     就成了「先动了外部状态、再说这活不能干」（经 run_workflow 调用时前面还夹着清残留与 staging）。
    case_target = _require_case_target(spec)
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

    # 多输出契约触发（据 spec 字段、op-中立）+ torch_allclose 容差分源参数（仅 torch 对标场景用）。
    uses_multi = _uses_output_contract(spec)
    tol_src = _tolerance_source(spec)
    tol_tuple = _mo_taskdoc_tol(spec)
    # ACLNN 调用变体：ctypes 与官方 C++ Extension 两种执行形态共用逐 case 已解析调用契约。
    # `aclnn_call`，driver 不再自己推变体。变体表必填——没它就只能靠 driver 兜默认值，而兜出来的
    # `dim=0` 既不是全局语义、又可能与单输出签名不符（越界写 / ABI 崩）。
    variants = _call_variants(spec)
    # ⚠ 这是个**成员测试**：它对任何词表外的值都安静答 False（= 不要求 `call_variants`）。
    #   之所以安全，全靠上面的 `check_spec_capability(in_params, runner_form)` 已经把
    #   `runner_form` 钉死在受控词表内——`None` / `""` / `0` / `"opaque"` 到不了这一行。
    #   那道门一旦被绕过或放宽，这里就会退化成 fail-open（P5-b 的原始现场）：别把它挪到门前面。
    needs_aclnn_call = runner_form in ("aclnn_py", "cpp_extension")
    if needs_aclnn_call and not variants:
        raise ValueError(f"{op}: runner_form={runner_form!r} 但 spec 未声明 call_variants —— "
                         f"aclnn 调用形态（符号/实参表/落地输出）必须由 spec 显式声明、逐 case 解析，"
                         f"不许下游兜默认值，fail-closed")

    # CS：用例来源分叉。`taskdoc` 档在 `_plan` **之前**分出去——它一条网格都不铺，
    # G4 的规模预算也不行使（降规模会改掉任务书点名的 shape，那就不是那条用例了）。
    if case_source == _CASE_SOURCE_TASKDOC:
        # ⚠ 「taskdoc 档不支持多输出契约」那道门在 `_taskdoc_plan` 里（计划期一处，dry-run 也拦得住）。
        entries, plan_meta = _taskdoc_plan(spec, in_params, attrs_default, case_target,
                                           taskdoc_payload, taskdoc_sha256)
    else:
        # G4：据 C1 的 out_shape 造生成期规模预算的 cost 模型（未导出 out_shape → 按输入广播形状 = elementwise）。
        cost_fn = _make_cost_fn(in_params, out_shape_fn)
        entries, plan_meta = _plan(spec, in_params, dtypes, attrs_default, op, case_target, cost_fn=cost_fn,
                                   empty_accepts=_make_empty_accepts(in_params, out_shape_fn, attrs_default))
    seen_ids, cases = set(), []
    golden_unavailable = []                              # CS：一等状态账本（case 身份保留、无 golden 文件）
    for entry in entries:
        dims, shp, dtn = entry["dims"], entry["shape"], entry["dtype"]
        attrs, data_kind = entry["attrs"], entry["data_kind"]
        td_entry = entry.get("taskdoc")                  # CS：非 None = 这条 case 的身份来自任务书
        if td_entry is None:
            cid = _mk_id(op, dtn, shp, entry["id_kind"], entry["attr_idx"], seen_ids)
        else:
            cid = td_entry["case_id"]                    # 身份照抄任务书（已在加载期校过唯一性与安全性）
            if cid in seen_ids:                          # 兜底：与自生成 id 空间撞车也当场炸
                raise ValueError(f"case_id 碰撞：{cid!r} 已存在（任务书用例身份与既有 id 冲突）")
            seen_ids.add(cid)
        cdir = os.path.join(work_dir, cid)
        os.makedirs(cdir, exist_ok=True)
        case_rng = _case_rng(cid)                        # per-case 独立种子（数据只依赖稳定 cid，评审 #7）

        if td_entry is None:
            inputs = _build_inputs(case_rng, in_params, shp, dtn, attrs, data_kind,
                                   runner_form)          # 逻辑数组（compute dtype）
        else:                                            # CS：按任务书的 shape×值域×seed 确定性物化
            inputs = _materialize_taskdoc_inputs(entry, in_params, dtn)
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
        if td_entry is None:
            golden = golden_fn(inputs, attrs)            # 用逻辑输入算 golden
        else:
            # CS · `golden_unavailable` 一等状态：任务书用例集里总有几条超出参考实现的支持范围
            # （实测：通道数 > OpenCV CV_CN_MAX 的那几条）。**不中断全量生成**——身份保留、
            # 记原因、退出精度维，其余 case 照跑；由门判 BLOCKED，绝不当 pass。
            golden, unavailable_reason = _taskdoc_golden_or_unavailable(
                golden_fn, inputs, attrs, cid)
            if unavailable_reason is not None:
                in_items = _save_inputs_multi(cdir, cid, inputs, in_params, dtn)
                gu_case = {
                    "id": cid,
                    # 只留「功能」：无 golden 即无从判精度，把它留在精度维会让精度分母里混进
                    # 一条**永远判不了**的 case（validator 要么崩、要么静默 na 冒充无事）。
                    "dims": ["功能"],
                    "tags": list(entry["tags"]) + ["golden不可用"],
                    "inputs": in_items, "attrs": attrs,
                    "expected": {
                        "golden_source": golden_source, "golden_tier": _tier,
                        "golden_status": GOLDEN_UNAVAILABLE,
                        "golden_unavailable_reason": unavailable_reason,
                        "golden_path": None,             # 显式 None，不是缺键：读的人一眼看见「没有」
                        "verify_mode": vmode, "compare": "na", "standard": "na",
                        "compare_dtype": None,
                        "case_origin": entry["case_origin"], "rule_ref": entry["rule_ref"],
                        "note": ("任务书用例，但参考实现算不出 golden → 精度维不可判。"
                                 "case 身份与输入字节仍完整保留，门须判 BLOCKED，"
                                 "**不得**当作通过、也不得当作「该用例不存在」"),
                    },
                }
                if needs_aclnn_call:                     # 调用契约照样解析：这条 case 仍可被人工复现
                    gu_case["aclnn_call"] = _build_aclnn_call(
                        spec, variant, attrs, _active_output_names(spec, variant, cid), cid)
                cases.append(gu_case)
                golden_unavailable.append({"case_id": cid, "reason": unavailable_reason,
                                           "case_origin": entry["case_origin"]})
                continue
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
            # TP 三重记账账本：**仅 torch_parity 档产**（同 operator_class / case_profile 的处理——
            # 其它档一个键都不多，`ExistingOpsByteIdenticalTest` 的 sha256 pin 不破）。
            # 报告要说「这批用例是怎么算出来的、排除了什么、其中多少是重复组合」，读这里就够。
            **({"case_matrix_ledger": plan_meta["case_matrix_ledger"]}
               if "case_matrix_ledger" in plan_meta else {}),
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
            # CS 账本：**仅在 spec 显式声明了 precision.case_source 时才出现**（同 case_profile 的处理，
            # 未声明的算子 caseset 逐字节不变）。声明了就把「用例是谁出的 / 绑的哪一份用例集 /
            # 哪几条算不出 golden」全部如实落进产物——报告与门都读这里，不必回头猜。
            **({"case_source": case_source} if _case_source_declared(spec) else {}),
            **({"taskdoc_caseset_sha256": taskdoc_sha256,
                "taskdoc_sha256": taskdoc_payload["taskdoc_sha256"],
                "golden_unavailable": {
                    "count": len(golden_unavailable),
                    "cases": golden_unavailable,
                    "note": ("这些任务书用例的 golden 算不出来 → 已退出精度维与性能候选池，"
                             "身份与输入字节仍在。门须判 BLOCKED，不得当 pass、也不得当作用例不存在"),
                }}
               if case_source == _CASE_SOURCE_TASKDOC else {}),
            **({"perf_case_policy": perf_case_policy} if perf_case_policy is not None else {}),
            "cases": cases}
    # ⚠ 原先这里挂一份 **op 级** `aclnn_call_template`，由 driver 自己按 case 兜变体（`dim=None`→`dim=0`）——
    # 已被 finding #3 判为不合规并**整体替换**：调用形态现在逐 case 解析、写在 `case["aclnn_call"]` 里。
    # 其它 runner_form（含缺省=cpp）零变更、caseset 字节不动（向后兼容硬约束：现有 4 算子不破）。
    return caseset


def _build_dry_run_ledger(spec, preparation_inputs=None, taskdoc_caseset=None):
    """plan-only（**不跑 golden_fn**、不落 .npy）：构造可持久化覆盖账本 + 确定性自检。

    G4 起会**尽力**加载 golden.py 取 `out_shape` 造规模预算的 cost 模型——好让「归约/成对类算子的大 shape
    算不完」在 **CP-B 契约自检**就暴露，而不是拖到 CP-D 真生成时卡死。加载不到（golden.py 还没写、或写坏了）
    → 明说「未核」并照常出计划，**不阻塞**（那种 spec 到了 gen_cases 本来就会 fail-closed）。
    ⚠ 本仓 golden.py 约定 torch 延迟 import（见 `samples/golden/*/golden.py`），故此处仍不拉 torch；
    但若某算子在模块顶层 `import torch`，dry-run 会跟着 import ——这是加载用户代码的代价，如实记在这。"""
    perf_case_policy = _perf_case_policy(spec)
    op = spec["op"]
    in_params = [p for p in spec["params"] if p["io"] == "in"]
    import repo_adapter                                 # 延迟 import：repo_adapter 顶层已 import gen_cases
    # 能力边界前置：三元算子 / dtype 双层能力门在 CP-B 就拦下，不拖到 CP-D。
    # form 走全仓唯一缺省真源——dry-run 是 CP-B 契约自检，口径与 gen_cases/run_workflow 必须一致，
    # 否则「CP-B 按 cpp 自检过了、CP-D 按 cpp_extension 跑」就是一份骗人的自检（P5）。
    # ⚠ 别改回 `spec.get("runner_form")`：`check_spec_capability` 已删掉 `runner_form=None` 的默认值
    #   （P5-b），键缺席时直接传 None 会在受控词表处炸掉每一份省略该键的合法 spec。
    dry_runner_form = repo_adapter.spec_runner_form(spec)
    check_spec_capability(in_params, dry_runner_form)
    # CS：dry-run 与正式生成走**同一道**用例来源解析 —— 「声明了 taskdoc 却没喂用例集」这类错
    # 必须在 CP-B 契约自检就现形，不许 CP-B 全绿、CP-D 才炸。
    case_source, taskdoc_payload, taskdoc_sha256 = _resolve_taskdoc_inputs(spec, taskdoc_caseset)
    attrs_default = {p["name"]: p.get("default") for p in spec["params"] if p["io"] == "attr"}
    self_param = next((p for p in in_params if p["name"] == "self"), in_params[0])
    dtypes = self_param["dtype"]
    # 与 gen_cases 同一读取点：dry-run 若比真跑宽松，CP-B 的契约自检就是假门。
    case_target = _require_case_target(spec)
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
    if case_source == _CASE_SOURCE_TASKDOC:              # CS：任务书用例集 → 不铺网格、不行使规模预算
        entries, meta = _taskdoc_plan(spec, in_params, attrs_default, case_target,
                                      taskdoc_payload, taskdoc_sha256)
    else:
        entries, meta = _plan(spec, in_params, dtypes, attrs_default, op, case_target, cost_fn=cost_fn,
                              empty_accepts=_make_empty_accepts(in_params, _dry_out_shape_fn, attrs_default))
    seen, ids = set(), []
    for e in entries:                                    # 跑 _mk_id 校 id 唯一（撞则 raise）
        if e.get("taskdoc") is not None:                 # CS：任务书用例的身份照抄、不由 _mk_id 编
            cid = e["taskdoc"]["case_id"]
            if cid in seen:
                raise ValueError(f"case_id 碰撞：{cid!r} 已存在（任务书用例身份重复）")
            seen.add(cid)
            ids.append(cid)
            continue
        ids.append(_mk_id(op, e["dtype"], e["shape"], e["id_kind"], e["attr_idx"], seen))
    specials = {"empty", "scalar", "bndlo", "bndhi", "inf", "ninf", "nan"}
    ranks = _allowed_ranks(in_params)
    eqn = None
    if any(p.get("io") == "attr" and p.get("name") == "equal_nan" for p in spec["params"]):
        eqn = sorted({str(e["attrs"].get("equal_nan")) for e in entries if "equal_nan" in e["attrs"]})
    determinism = None
    if ids:
        cid = ids[0]
        if entries[0].get("taskdoc") is not None:
            # CS：taskdoc 档的输入字节由 `materialize.seed` 定，不由 `_case_rng(case_id)` 定 ——
            # 自检就必须核**真正在用的那条**种子链，否则「确定性已核」是核了个用不上的东西。
            seed = int(entries[0]["taskdoc"]["materialize"]["seed"])
            a = float(np.random.default_rng(seed).random())
            b = float(np.random.default_rng(seed).random())
            determinism = {"case_id": cid, "seed_source": "taskdoc_caseset.materialize.seed",
                           "seed": seed, "first_draw_a": a, "first_draw_b": b, "equal": a == b}
        else:
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
            # ⚠ 原有 `default_case_target` 已随缺省值一并删除：`case_target` 现在必由 spec 显式声明，
            #   账本再落一个「缺省值」会重新暗示存在缺省。实际用的那个数在 `planning.case_target`。
            "default_golden_cost_budget": _GOLDEN_COST_BUDGET,
            "case_profiles": list(_CASE_PROFILES),
            "case_sources": list(_CASE_SOURCES),
            "operator_classes": list(_OPERATOR_CLASSES),
            "change_kinds": list(perf_mode.CHANGE_KINDS),
        },
        "planning": {
            "case_target": case_target,
            # ⚠ 落账的是**已解析**的 form，不是原始键：账本要如实说「这批用例按哪种形态规划的」。
            #   写 `spec.get(k, "cpp")` 会在 spec 省略该键时记下 `cpp`、而 run_workflow 实际按
            #   `cpp_extension` 去跑——一份被下游哈希绑定的产物里躺着假记录（P5）。
            "runner_form": dry_runner_form,
            "case_profile": meta["case_profile"],
            "case_profile_declared": meta["case_profile_declared"],
            # CS：用例是谁出的 + 绑的哪一份用例集（generated 档两个键分别是 "generated"/None，
            # 账本读者一眼看得出「这批用例是本引擎造的」，不必从别处推）。
            "case_source": case_source,
            "case_source_declared": _case_source_declared(spec),
            "taskdoc_caseset_sha256": taskdoc_sha256,
            "change_kind": perf_mode.normalize_change_kind(spec),
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
            # TP：只在 torch_parity 档出现（其它档 dry-run 账本一个键都不多，ledger_digest 不变）。
            **({"case_matrix_ledger": meta["case_matrix_ledger"]}
               if "case_matrix_ledger" in meta else {}),
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
    # CS：taskdoc 档的「不产」是**另一个原因**（这一档一条网格都不铺），措辞必须分开——
    # 沿用「该类别按方法学不适用 NaN·Inf」会把一个 floating_compute 算子说成不适用非有限值，是错话。
    if planning["case_source"] == _CASE_SOURCE_TASKDOC:
        nonfinite = "**不由工具铺**（taskdoc 档：特殊值有没有由任务书用例决定）"
    elif coverage["emits_nonfinite_specials"]:
        nonfinite = "产"
    else:
        nonfinite = "**不产**（该类别按方法学不适用 NaN·Inf）"
    print(f"  operator_class: {planning['operator_class'] or '未声明（缺省 = 现行为）'}"
          f"  → inf/-inf/nan 特殊场景: {nonfinite}")
    # CP：造例档位。未声明时明说「缺省 = legacy = 现行为」，别让人以为已经按参考仓口径造过例。
    print("  case_profile: "
          + (f"{planning['case_profile']}（spec 显式声明）" if planning["case_profile_declared"]
             else "未声明（缺省 = legacy = 现行为）"))
    # CS：用例来源。taskdoc 档必须把绑定的用例集摘要打出来——「这批用例是任务书的哪一份」
    # 是报告里躲不掉的一句话，别让人事后去翻产物。
    print("  case_source: "
          + (f"{planning['case_source']}（spec 显式声明）" if planning["case_source_declared"]
             else "未声明（缺省 = generated = 本引擎造例）")
          + (f"  taskdoc_caseset_sha256={planning['taskdoc_caseset_sha256']}"
             if planning["taskdoc_caseset_sha256"] else ""))
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
    # TP 三重记账（只有 torch_parity 档有）：把「这个四位数是怎么算出来的」直接打出来，
    # 别让人回头自己乘一遍。排除项逐条列 reason + evidence——缩水必须看得见。
    _cm = coverage.get("case_matrix_ledger")
    if _cm:
        print(f"  case_matrix: 完整笛卡尔 {_cm['full_cartesian']} − 有证据的排除 {_cm['excluded_total']}"
              f" = {_cm['expected']} == case_target {_cm['case_target']} == 实产 {_cm['emitted']}"
              f"（三重相等，任一处漂移 fail-closed）")
        print("    轴取值: " + "; ".join(f"{ax['name']}={ax['values']}" for ax in _cm["axes"]))
        print(f"    不同覆盖组合 {_cm['distinct_combinations']}，"
              f"其中重复组合的额外样本 {_cm['duplicate_cases']} 例"
              + ("（低 rank 下 axis_class 塌缩；输入字节仍不同，但不算新覆盖）"
                 if _cm["duplicate_cases"] else ""))
        for row in _cm["excluded"]:
            print(f"    · 排除 {row['combo']} → {row['combos_excluded']} 个组合"
                  f"；reason={row['reason']}；evidence={row['evidence']}")
    print(f"  coverage: {coverage['strength']}")
    det = ledger["determinism"]
    if det:
        # CS：标签必须点名**这一档真正在用的那条种子链**——taskdoc 档的字节由 materialize.seed 定，
        # 打成 `_case_rng[...]` 就是报了个用不上的自检。
        label = (f"materialize.seed={det['seed']}[{det['case_id']}]" if det.get("seed_source")
                 else f"_case_rng[{det['case_id']}]")
        print(f"  determinism({label} first draw): "
              f"{det['first_draw_a']} == {det['first_draw_b']} -> {det['equal']}")
    if coverage["note_target_below_forced"]:
        print(f"  note: {coverage['note_target_below_forced']}")


def _write_json_atomic(path, payload):
    """按内容寻址基础层的严格 JSON/原子写契约落 durable ledger。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    base = os.path.basename(path)
    os.makedirs(directory, exist_ok=True)
    content_address.atomic_write_json(directory, base, payload)


def _dry_run(spec, preparation_inputs=None, taskdoc_caseset=None):
    """兼容入口：构造 ledger 后按既有 stdout 语义渲染，并返回结构化账本。"""
    ledger = _build_dry_run_ledger(spec, preparation_inputs=preparation_inputs,
                                   taskdoc_caseset=taskdoc_caseset)
    _render_dry_run_ledger(ledger)
    return ledger


def _take_taskdoc_caseset_flag(argv):
    """CS：从 argv 里摘出 `--taskdoc-caseset <path>`，返回 `(剩余 argv, path|None)`。

    dry-run 与正式生成**两条路径共用**同一个开关：任务书用例集是「本轮用哪批用例」的输入，
    不是只在预探时才需要的调试项。缺值 → fail-closed（不当成没传）。
    """
    rest, path, i = [], None, 0
    while i < len(argv):
        if argv[i] == "--taskdoc-caseset":
            if i + 1 >= len(argv):
                raise ValueError("--taskdoc-caseset 缺少 taskdoc_caseset.json 路径")
            path = argv[i + 1]
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    return rest, path


def main(argv):
    argv, taskdoc_caseset = _take_taskdoc_caseset_flag(argv)
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
                "[--taskdoc-caseset <taskdoc_caseset.json>] "
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
        ledger = _dry_run(spec, preparation_inputs=preparation_inputs,
                          taskdoc_caseset=taskdoc_caseset)
        if ledger_out:
            _write_json_atomic(ledger_out, ledger)
        return
    if len(argv) != 3:
        raise ValueError(
            "正式用法: gen_cases.py <spec.json> <work_dir> <caseset.json> "
            "[--taskdoc-caseset <taskdoc_caseset.json>]")
    spec_path, work_dir, out_path = argv
    spec = json.load(open(spec_path, encoding="utf-8"))
    caseset = gen_cases(spec, work_dir, taskdoc_caseset=taskdoc_caseset)
    json.dump(caseset, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[gen_cases] {caseset['op']}: {len(caseset['cases'])} cases -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1:])
