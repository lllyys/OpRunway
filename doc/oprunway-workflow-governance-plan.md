# 实施方案 · workflow 治理批（目标 1 / 3 / 4 / 5 / 6 / 7）

**日期**：2026-08-05
**上游依据**：`doc/oprunway-roll-complex64-trial-findings.md`（问题清单，§1–§5 已过 codex 审）
**姊妹方案**：`doc/oprunway-local-source-plan.md` —— **目标 2（本地来源）与目标 8（通路收敛）在那份里，本方案不重复**

**本方案覆盖 6 项目标**：

| 目标 | 一句话 | 批次 |
|---|---|---|
| 5 | 生成用例**计划**后停下来，输出数量与覆盖场景让人确认 | **A** |
| 6 | 流程结束后按 checklist 输出执行路径文档 | **A** |
| 7 | 记录各环节耗时；实时监控偏离规定路线即停 | **A** |
| 3 | 每次现场推导时，生成的数据用例不能变化 | **B** |
| 4 | 用例满足笛卡尔积，无遗漏无冗余 | **C** |
| 1 | 删掉老的 sample（会误导） | **D**（前置 **D0**：spec 来源门） |

**为什么 5/6/7 必须同批**：它们是**同一套东西的三个时点**——事前定方案、运行期守方案、事后对账。
分批做收据格式必然漂成三套，而且 5 的产物就是 6 的对账基准、7 的耗时就是 6 的内容。

**批次顺序**：A → B → C → **D0** → D。
⚠ **D0（spec 来源门）是本方案额外认领的**——它原是 findings §2 的 D5，
无人认领而批次 D 依赖它，故收进来。理由与真实依赖图见 §6。

---

> **📍 交接入口：`doc/oprunway-session-handoff-2026-08-05.md`**（实施顺序、已知坑、待办都在那）
> ⚠ 该 handoff 已被 `doc/oprunway-session-handoff-2026-08-05-evening.md` 接替（仓根 `AGENTS.md` §5.6），
> 上面这行只作历史指路。

---

## 分批判定（2026-08-05）

> **本节是复核结论，不是方案修订。** 下面每条都**读了现在的代码**核过、给了 `file:line`；
> 原方案正文（§0 起）一字未改，仍作历史材料保留。
> ⚠ 判定与正文冲突时**以本节为准**——正文写于本轮之前，那之后本地来源成了一等通路、
> `runner_form` 收敛到 `cpp_extension`、`vendor_build_receipt` 产出方落地、CP-F 跑通、CP-B0 任务书输入校验门落地。
>
> ⚠ **本节的 `file:line` 是 2026-08-05 复核当时的快照，且当时 `plugin/` 有并行改动在途。**
> 实测撞上过：复核开始时 `run_workflow.py` 是 735 行、`source_facts` 零命中；复核过程中被另一路两次改动
> （735 → 888 → 945 行），并加上了验收通路的 `--source-facts` 必填门（下文 A-① / D0 两节已按新事实重写）。
> **实施前请按关键词重新 grep 一遍，不要直接信这里的行号**；本节对 `run_workflow.py` 已尽量改用**符号名**而非行号定位。

### 判定总表

| 批次 | 目标 | 判定 | 一句话 |
|---|---|---|---|
| **A-① 确认门** | 5 | ⬜ **仍成立且该做**（前提有变，实现路线要改写） | 一行代码都没有；CP-B0 已把同构机制做出来了，照抄即可。⚠ 但方案里那道门**自身设计就漏**（`--required-items` 可选 → 空集恒真；且「人确认」身份不可证），见下文 Critical 表第 4、5 条 |
| **A-② 执行路径** | 6 | ⬜ **仍成立且该做** | 一行代码都没有；现有 renderer 结构上覆盖不到「没走完」的形态 |
| **A-③ 耗时 + 偏离监控** | 7 | ⬜ **仍成立且该做**，但**证人已失效** | 耗时打点仍是零；A.5 的实证证人 `preflight_aclnn` 已不在准入通路上 |
| **B-1 numpy 流钉账本** | 3（一半） | ✅ **已落地**，且实现**有意否掉**了方案的「主.次」建议 | 反例推翻了方案的粒度建议，别按方案回改 |
| **B-2 用例数据固化复用** | 3（另一半） | ⬜ **仍成立且该做** | `case_data_digest` / `case_data_manifest` 全仓 0 命中 |
| **C 轴集** | 4 | ⬜ **仍成立且该做，优先级应上调** | 退化的 shape 轴现在压在**唯一准入通路**上 |
| **D0 spec 来源门** | 1（前置） | ⬜ **仍成立且该做，全批最高价值**（⚠ 根因须重述） | 来源门**已有**（`--source-facts` 必填），但它锚 ELF 出身；`spec_origin` 收据仍 0 命中，spec 本身无来源绑定 |
| **D 样例隔离** | 1 | ⚠ **前提已变**：依赖清单漂了，**且方案「有 D0 就可推后 D」的推理本判定推翻** | 方案记 12 个测试，实测触及 `samples/` 的已有 15 个、其中真正依赖 `samples/specs/` 的是 11 个；D0 顶不了样例物理隔离 |

---

### A-① 确认门（目标 5 · 方案 §A.4）→ ⬜ 仍成立且该做

**没做的证据**：

- `grep -rn "execution_plan_confirmation\|render_execution_plan\|required_confirm_items" plugin/` → **0 命中**；
  `plugin/acc-common/` 下既无 `render_execution_plan.py` 也无 `validate_execution_plan_confirmation.py`；
- `run_workflow.py` 里 `execution_plan_confirmation` / `case_plan` / `preparation_receipt` 均 **0 命中**
  —— 方案 §A.4「硬门落点」要的入口门①（进 Task1 正式生成前）、出口门②（写 `acceptance.json` 前）**两处都不存在**。

⚠ **别把「来源门」误当成「用例计划确认门」**：`run_workflow.py` 现在**已有**一道来源必填门——
验收通路上 `--source-facts` 缺席即拒跑（`if is_acceptance and source_facts is None`），
三份输入原件按字节 staging 进 `--out`（`_read_acceptance_inputs`），
且每一级门都显式传该副本、不许退回自动发现（`gate._GATES[st](..., source_facts_path=staged_source_facts)`）。
但它锚的是**被测 ELF 的出身**（build receipt ↔ `source_facts`），**不管**「这份用例计划人看过没有」。
A.4 要的那道门与它**不重叠**，别因为「已经有 `--source-facts` 门了」就以为 A.4 已被覆盖。

**⚠ 前提变了（是加分，不是取消）**：CP-B0 已落地，它就是 A.4 想要的那套机制的**现成同构件**：

| A.4 要的 | CP-B0 里已有的 | 位置 |
|---|---|---|
| 内容寻址确认收据 + domain | `_RECEIPT_DOMAIN = "oprunway/taskdoc-validation-receipt/v1"` | `validate_taskdoc_input.py:41` |
| ⚠#1「绑定值由校验方**当场重算**，不读自报」 | `evaluate()` 自己 `_load_taskdoc` 算 `taskdoc_bytes_sha256`、自己算 `validation_digest` / `contract_digest` | `validate_taskdoc_input.py:438-446` |
| ⚠#2「受控词表不许被放宽」 | `contract_path` 只供进程内调用、CLI 不暴露，原话「否则一份放宽的契约就能把整道门降级成 PASSED」 | `validate_taskdoc_input.py:413-414` |
| 「人确认 → 写回 decisions → 重跑脚本 → fail-closed」闭环 | `decisions`（`source: "user"`）+ `NEEDS_USER` 状态机 | `plugin/AGENTS.md` CP-B0 节 |

⇒ **估工从「从零造两套脚本 + 收据 schema」降为「照 CP-B0 复制一套、把绑定对象从任务书换成 `case_plan`」。**

**⚠ 本轮新发现（比原方案记的更严重）**：**CP-B0 自己也只在 NL 编排层被调用**——
`grep -c "taskdoc_validation" plugin/acc-common/run_workflow.py` → **0**。
也就是说 A.4 想堵的那条绕过路径（绕开编排层直接调 `run_workflow.py`）**现在同样绕过 CP-B0**：
跳过 `validate_taskdoc_input.py`，直接拿一份 spec 开跑，`acceptance.json` 照样写得出来。

⚠ **但别把这条当成「A.4 顺手就修了」**：方案 §A.4 的判据只消费 confirmation / case_plan / spec / source_facts，
**一个字都没提 `taskdoc_validation_receipt.json`**。照方案原样实现，CP-B0 的执行层缺口**还在**。
正确处置是：**A.4 落地时一并给 CP-B0 建门**——判据里显式读 `work/taskdoc_validation_receipt.json`，
当场复核它的 `status ∈ {PASSED, PASSED_WITH_PENDING}` 与四个绑定（`taskdoc_bytes_sha256` /
`source_facts_digest` / `validation_digest` / `contract_digest`）对上**当前**文件。
不加这一步，CP-B0 就仍然只是编排层纪律——而本方案 §0 的全部教训就是「纪律会被绕过」。

**估工**：中。2 个新脚本 + 2 处门 + 方案 A.8 已列全的 16 条测试（其中负路 9 条是重点）。

### A-② 执行路径文档（目标 6 · 方案 §A.7）→ ⬜ 仍成立且该做

**没做的证据**：`plugin/acc-common/` 下无 `render_execution_path.py`；只有 `render_acceptance_markdown.py`，
而它按设计只在 `acceptance.json` 之后调用——**结构上覆盖不到「流程没走完」的形态**，而那正是 A.7 的全部价值所在。
它还依赖 A.3b 的 `run_state.json`（`grep -rn "run_state" plugin/` → 0 命中）。

**估工**：渲染器本身小，但地基（A.3b）不小，合并计入 A-③。

### A-③ 耗时 + 偏离监控（目标 7 · 方案 §A.3b / §A.5 / §A.6）→ ⬜ 仍成立，但**证人已失效**

**没做的证据**（复核方案 A.1 那张「零」，本轮仍是零）：

- `grep -n "perf_counter\|monotonic()" plugin/acc-common/*.py | grep -v test_` → **0 命中**；
- `gate_attempts` / `run_state` 全仓 **0 命中**。

**⚠ 前提变了一半——故障模式还在，证人换了**：A.5② 的实证依据是「`preflight_aclnn` 连挂 4 次无人管」，
而 `preflight_aclnn` **只服务 `aclnn_py`**，该形态自 §4 收敛后已产不出验收裁决
（`run_workflow._ACCEPTANCE_RUNNER_FORMS = frozenset({"cpp_extension"})`）。
`run_workflow.py` 里那道 `verify_aclnn_harness.validate_receipt` 的 CP-C 信任门也**只在 `mode == "aclnn_py"` 时才跑**
（`run_workflow.py:548` 起的 `if mode == "aclnn_py"` 分支），**同样不在准入通路上**。

准入通路（`cpp_extension`）上会被反复重试的确定性环节是另一组：
`make_vendor_build_receipt.py`（真跑 build，产不出即停 CP-C）→
`cpp_extension_adapter._validate_vendor_build_receipt`（`cpp_extension_adapter.py:281` 起）→
三级门里的 build receipt / 来源锚对账（`validate_acceptance_state.py:789` 起）。它们**同样**可以被连试 N 次然后放弃转去写报告。

⇒ **阈值机制照做，但「受监控的确定性脚本清单」必须按上面这组重列。**
⚠ 照方案原文去数 `preflight_aclnn` / `verify_aclnn_harness` 的话，
`cpp_extension` 的 build-receipt 连续失败**一次都不会进计数**——阈值门形同虚设，正是本仓最贵的那类假门。

**估工**：中偏重。A.3b 的 `run_state` schema + 原子写 + `self_digest` + 出口门 + `prior_runs` 归档，
是 A 批三件产物里最重的地基；A-① / A-② 都压在它上面。

### B-1 numpy 随机流钉账本（目标 3 一半）→ ✅ 已落地

| 项 | 位置 |
|---|---|
| 产：`case_plan.planner_binding` 落 `numpy_version` / `numpy_stream_pin` / `numpy_stream_pin_granularity` | `gen_cases.py:3065-3067` |
| 消费：复用侧对账 | `validate_preparation_state.py:618-653` |

**实现比方案更细**：方案原文只说「不符 → fail-closed 报错」，实现**把三种「对不上」分开判**——
取不到当前 pin → `BLOCKED`；账本**没有**该键 → `MISS`（老工件正常过期）；有键但**形态非法** → `BLOCKED`（账本不可信，重跑救不了）；
值不等 → `MISS`。这比方案原文更符合仓规的 fail-closed 分层，**别按方案回改成一锅炖**。

**⚠ 实现有意否掉了方案 B.2 那张表的「建议采用主.次」**：`gen_cases.py:157-166` 记了反例——
numpy **1.18.4** 在补丁版里改过 `Generator.integers` 的取值，而 1.18.3/1.18.4 的「主.次」pin 都是 `1.18`，
**该粒度探测不到已经真实发生过的流变更**。故 `_NUMPY_STREAM_PIN_GRANULARITY = "exact"`（`gen_cases.py:166`）。
**方案里的「建议」已被推翻，看到不一致别去「修正」实现。**

**⬜ 残留小缺口（低优先）**：方案 B-1 还要求 `caseset.json` **顶层**加 `numpy_version`，实测 `gen_cases.py:2945` 起的
caseset dict **没有**该字段。判定：**不构成 fail-open**（复用门读的是 `case_plan`，不读 caseset），
只是直接消费 `caseset.json` 的人少一条诊断线索。可顺手补，也可不补——补的话属 caseset 字节变更，要同步重取字节 pin 基线。

### B-2 用例数据固化复用（目标 3 另一半）→ ⬜ 仍成立且该做

**没做的证据**：`grep -rn "case_data_digest\|case_data_manifest" plugin/` → **0 命中**。
方案要的骨架（`validate_preparation_state.evaluate → REUSABLE | MISS | BLOCKED`）确实还在，`.npy` 那一层没接上去。

**估工**：中。方案 §6 说它依赖 A（固化复用的对象是「已确认过的 caseset」）——这条依赖**仍然成立**。

### C 轴集（目标 4）→ ⬜ 仍成立且该做，**优先级应上调**

**没做的证据**：`doc/oprunway-case-axis-design.md` 不存在。

**现状按代码复核（不是凭方案文字）——方案 C.1 那张表逐条仍然成立**：

| 方案 C.1 的说法 | 现在的代码 | 结论 |
|---|---|---|
| torch_parity 的 shape 「退化成 `(leading,)+(1,)*(rank-1)`」 | `gen_cases.py:949` 逐字就是这一行；`shape_profiles` 只让人配一个 `leading_dim` 正整数（`:902` 强制 `set(row) == {"name","leading_dim"}`） | ✅ 仍成立 |
| torch_parity 值域「只有 uniform」 | `gen_cases.py:931-937` 硬限 `kind == "uniform"` | ✅ 仍成立 |
| legacy 有 11 常规 shape + 2 大 shape + 2 值域 | `_REG_SHAPES`（11 条，`:1507`）、`_LARGE_SHAPES`（2 条，`:1516`）、`_VALUE_REGIMES`（2 项，`:1520`） | ✅ 仍成立 |
| 完整矩阵「宁可 raise 也不静默缩形」 | `gen_cases.py:939-943`（`case_target` 必须精确等于矩阵大小）、`:961-964`（超预算 raise） | ✅ 仍成立 |

**⚠ 优先级要上调，理由是前提变了**：方案写作时 torch_parity 只是「两个档之一」；§4 收敛后
`cpp_extension` 是**唯一准入形态**，而「任务书对标 torch」的场景走的正是 torch_parity。
**这条退化的 shape 轴现在压在唯一能出裁决的通路上。**

佐证要如实说，别越界：仓根 `AGENTS.md` §4.5 并列的两个 caseset **共用同一组三档 shape 轴**
（`31 / 2047 / 262144` 加尾随 1）；其中 **1152 caseset 已证 51 条失败全部落在 `[262144,1,1,...]`**（本方案 §C.1）。
⚠ **1344 caseset 的逐 case 失败分布本仓没有证据**——不得写成「两组失败都集中在长轴」。
轴集设计**不得**据此认定长轴以外已排除，否则会把一个未知分布当成已排除，反而做出覆盖盲点。

**⚠ 依赖关系要澄清**：方案 §6 写「C 依赖 A」，理由是「轴集决定权要交给确认单承载」。
那条只约束**决策怎么落地**，不约束**设计说明什么时候写**——C.3 的交付物是一份交人评审的 doc，**可以先于 A 起草**。

**估工**：设计说明 0.5–1 天，**不动 `gen_cases` 代码**（方案 §5 的边界，本判定维持）。

**给下一个 lane 的起草提纲（省掉重新盘点的功夫）**：

1. **轴清单与各自当前规模**：dtype 8 · rank 1–8 ·
   shape（legacy 是 **11 基础阶梯 `_REG_SHAPES` + 2 大 shape `_LARGE_SHAPES`**，
   ⚠ **另有 2 条按 rank 条件加入的 `_EXT_RANK_SHAPES`**（`gen_cases.py:1515`，5 维一大一小，
   由 `_shape_ladder()` 在 rank 约束点名基础阶梯覆盖不到的 rank 时才补）——所以**没有「固定 13 条」这回事**；
   torch_parity 则只有 3 条且退化）· 值域 regime（legacy 2 / torch_parity 1）·
   特殊场景（legacy 7 类 / torch_parity 0）· attr（spec 驱动）；
2. **统一后规模粗算**：方案 §C.2 估 `8 dtype × 8 rank × 13 shape × 2 regime × 7 attr ≈ 11648`，
   相对现状 torch_parity 的 `8×8×3×7 = 1344` 约 **8.7 倍**（数字与方案一致，已复核）。
   ⚠ **这个数只作量级参考**：它按「13 条固定 shape」算，而实际 shape 集合是 rank 条件相关的
   （见上条），准确规模要在**rank 过滤规则与 `_EXT_RANK_SHAPES` 补入规则定下来之后**才算得准；
3. **逐轴给「必须全交叉 / 边际覆盖 / 按 `operator_class` 收窄」三选一并论证**——
   方案 C.2 的四个问题原样保留，**不预设结论**；
4. **golden 成本估算**：`_cost_budget` 是硬约束，现行答案是 raise（`:961-964`），
   「无遗漏」与「算得完」冲突时的裁法要人拍板；
5. **收窄要能被看见**：`operator_class` 的合法收窄（structural 类不产 NaN/Inf）必须在确认单上讲清楚，
   否则人看到「没有 NaN 用例」会误判成漏测——这条把 C 和 A-① 接起来。

### D0 spec 来源门（目标 1 前置）→ ⬜ 仍成立且该做，**全批最高价值**

**没做的证据**：`grep -rn "spec_origin\|spec-origin" plugin/` → **0 命中**。

**⚠ 根因要按现在的代码重述，方案原文那句已不准确**：`run_workflow.py` 现在**已经会问来源了**——
验收通路强制 `--source-facts`、并把它交给三级门与 vendor build receipt 逐字对账（见 A-① 节引的行号）。
所以缺口**不是**「流程不问来源」，而是：

> 现有来源门锚的是 **被测 ELF 的出身**（build receipt ↔ `source_facts`），
> **完全不管「这份 `spec.json` 是照哪一份任务书 / 哪一份被测事实抽出来的」**。
> `run_workflow.py` 里 `correspondence` / `preparation_receipt` / `spec_origin` 均 **0 命中**。

⇒ **手写一份 spec + 一份合法 `source_facts` + 对应的 vendor receipt，当前仍能开跑。**
实施时**别再建一道读 `source_facts` 的门**（那已经有了），要建的是 **spec ↔ `source_facts` / 任务书快照的绑定**，
并复用现有的读取与校验入口。

### D 样例隔离（目标 1）→ ⚠ 前提已变（依赖清单漂了 **且不能靠 D0 顶替**）

方案 §D.1 自带告诫「实施前必须重跑这个 grep」。**本轮重跑了**：

⚠ **数量还在涨，且方案那个笼统计数没法用来估代价**——`samples/` 下三个子目录被不同测试依赖，
D.3 三条路只动 `samples/specs/`，所以要**按子目录分类**，不能只数一个总数。本轮实测（`plugin/acc-common/` 下）：

| 依赖的子目录 | 触及的测试数 | 与 D.3 的关系 |
|---|---|---|
| `samples/specs/` | **11**（`test_catlass_adapter` / `test_gen_cases_case_profile` / `test_gen_cases_dry_run_ledger` / `test_gen_cases_dtype_attr` / `test_gen_cases_multi_output` / `test_gen_cases_perf_shape_classification` / `test_ne_transport` / `test_perf_msprof` / `test_run_workflow_mode` / `test_spec_isolation` / `test_validate_acceptance_state`） | ← **D.3 三条路真正要改的就是这 11 个** |
| `samples/golden/` | **7** | D.3 三条路都不动它 |
| `samples/runners/` | **1**（`test_runner_lookup.py:140`，断言错误文案里必须出现 `samples/runners`） | 与 D.3 无关 |
| 合计触及 `samples/` | **15** | 方案 §D.1 记的是 **12**；方案原式 `grep -rln '"samples"'` 现在也已是 **14** |

⚠ **纠正一处本判定初稿的错误**：初稿说 `test_runner_lookup.py`「走 D.3 的 A/C 路都会撞上」——**不成立**。
它引的是 `samples/runners`，而 D.3 三条路操作的是 `samples/specs`，两者是不同子目录，
只迁 `samples/specs` 时 `test_runner_lookup.py` 照样绿。
⇒ **D.3 的代价按「11 个 specs 依赖方」重算**（方案写 12，实为 11 个真正相关 + 若干无关计数）；
`test_gen_cases_multi_output.py` 的动态 `f"{path}.spec.json"` 仍是最容易断的那处，方案 §D.1 的这条提醒有效。

`test_spec_isolation.py` 那条「专门为防误删而写」的断言仍在（`:36-42`，`assertTrue(os.path.isdir(samples))`
+ `assertTrue(glob(*.spec.json))`），方案 §D.1 的描述准确。

**⚠ 方案 §D.2 / §6 那条「有了 D0，D 就降为顺手清掉诱饵」的推理不成立，本判定推翻它。**
理由：按 §D0 的设计，`spec_origin_receipt.json` 是**由产 spec 的那个 `acc-spec-extractor` 自己产的**
（§D0 原文 `producer: {tool: "acc-spec-extractor", dispatch_mode: "extract_spec"}`）。
那么只要 extractor **误把 `samples/specs/*.spec.json` 抄了一份当输出**——不需要任何恶意——
它会顺手给这份副本产一张**完全合法**的收据：spec sha256 对、任务书快照 sha256 对、`source_facts_digest` 对。
D0 的入口门、出口门和 §D0 列的 4 条测试**全会绿**。这正是本仓定义的**假门**（删掉真正的抽取逻辑，门测试照样绿）。

⇒ 两条结论：

1. **D 的优先级不得从 D0 推导**。样例的**物理隔离**是与 D0 正交的独立措施，D0 顶不了它；
2. D0 的测试要补一条负路：**「extractor 交回一份样例副本、且自带合法收据 → 仍须被拒」**。
   拒法只能靠 spec 内容/来源本身（如 fixture 内容 hash 命中即拒，即方案 D.3 的 A 路，或 D.3 的 B 路标记位），
   **不能靠收据在不在**。

---

### 当初 11 条审修意见里 3 条 Critical 的现状（逐条自核）+ 本轮新发现 2 条

⚠ **这是设计草案的审计意见，不是待办清单。照单改等于让审计意见替代设计决策——下面只给「还成不成立 + 建议改法」，不代表本节授权修改。**

其余 8 条已在原方案正文里以「codex 审出」显式吸收（§A.3b 的账本合并与换目录处置、§A.4 的两条 ⚠、
§A.6 的不进 payload、§B.2 的锁到哪一级、§D.1 的漏 5 个、§6 的伪串行、§D0 认领 D5）。
**这 3 条 Critical 是只被记下、没被改掉的**；第 4、5 两条是本轮复核时新逮到的，性质相同（门看着有、实际拦不住）：

| # | 意见 | 现状 | 能构造出的失败场景 | 建议改法 |
|---|---|---|---|---|
| 1 | `run_id` 未纳入 `source_facts_digest` | **成立，但原始表述把场景说宽了**（本轮自核收窄）。§A.3b 定义 `run_id = sha256(spec_sha256 + case_plan_digest + out_dir_realpath)`；字段表对 `run_binding.source_facts_digest` **只校 64 位 hex 形态** | ⚠ **「合规换来源」这条路走不通**：`case_plan` 已绑 `preparation_inputs.source_facts_digest`（`gen_cases.py:3274-3275`）且该字段进 `ledger_digest`，来源一变、正常重跑 `gen_cases` 后 `case_plan_digest` 就变，`run_id` 本来就会变；`validate_preparation_state.py:561-574` 也会判 `MISS`。**真正可构造的是绕过准备态复核**：拿 PR-A 的**旧** `case_plan` 配 PR-B 的 `source_facts`（`run_workflow.py` 里 `preparation_receipt` 0 命中，没有任何东西强制它 `REUSABLE`）→ `run_id` 不变 → 归档不触发 → A 的 `cp_states` / `gate_attempts` 被当作本轮状态，执行路径文档把 A 的耗时和产物写成 B 的 | **修在配对那一层，不是修 `run_id`**：A.4 的确认门须交叉核 `case_plan.preparation_inputs.source_facts_digest == 当场重算的当前 `source_facts` digest`（或硬要求当前 `validate_preparation_state` 为 `REUSABLE`）。把来源摘要加进 `run_id` 只能重置状态、**挡不住错误配对进入执行**，只算防御加固 |
| 2 | D0 出口门未校来源摘要 | **仍成立，可直接构造** | 同一份任务书快照 + 同一份 spec 字节，被测来源换成另一个 PR / 另一份本地 checkout → 旧收据原样复用通过。spec 来源门对「这份 spec 是照哪一份被测事实抽的」**毫无约束** | 判据补第三条：`source_facts_digest == 当场重算的 source_facts digest` |
| 3 | 「手写 spec 必被拒」不成立 | **仍成立，可直接构造**；⚠ 但本轮自核认为**「只降宣称、机制照做」不够**（原判定写轻了，此处修正） | `spec_origin_receipt.json` 是**无密钥**的内容寻址 JSON，payload 每一项都是任何拿得到这些文件的人当场可算的 → 能手写 spec 的人就能手写配套收据。**更要命的是不需要恶意**：收据由产 spec 的同一个 NL extractor 产出，extractor 误抄一份 `samples/specs/*.spec.json` 也会顺手配一张完全合法的收据（详见上文 D 节） | ①（必须）改宣称：§D0 的「都没有这份收据 → 被拒」改成「**随手 `cp` / 顺手手写的 spec 会因缺收据被拒；刻意伪造、以及 producer 自己抄错，都拦不住**——本门是**完整性绑定**，不是**来源证明**」，§7 验收标准 #9 同步改成「**无收据**的手写 spec 被拒」；②（必须）**不得**据此推后批次 D，样例物理隔离作为独立硬措施保留；③（可选，成本高）若真要证明执行来源，收据得由 NL producer **之外**的确定性包装器产出，绑输入、输出与 dispatch 证据。⚠ 口径照抄 §A.3b 对 `self_digest` 的诚实边界，方案里已有先例 |
| **4** | **本轮新发现**：A.4 的确认门自身可 fail-open | §A.4 的 CLI 把 `[--required-items <rel.json>]` 写成**可选**，判定顺序 ④ 只做 `set(confirmed_items) ⊇ set(required_confirm_items)` 的**集合包含**判断，且没有一条要求核 `required_confirm_items.case_plan_digest` 等于当前 plan | 省掉 `--required-items`，或塞一份 `items: []` 的旧文件 → ④ 对空集**恒真** → dtype 缺口、`dropped_combo_classes` 一项没被确认也能进 Task1，出口门同样放行。**门看着有、实际拦不住** | `--required-items` 改**必填**，缺失即退 2；`items` 为空亦拒；须核其 `case_plan_digest == 当场重算值`；最好由校验方**从当前 case plan 独立派生**必确认项，而不是读渲染器给的清单。A.8 负路补三条：**缺 required-items / 空 items / 拿旧 plan 的 required-items** |
| **5** | **本轮新发现**：A.4 的「人确认」身份**不可证**（与第 3 条同源，方案只对 D0 认了、对 A.4 没认） | §A.4 判定顺序 ⑤ 只要求 `confirmed_by` 非空且不等于自动填充占位符。确认收据同样**无密钥**、envelope digest 人人可重算 | 编排层自己写 `confirmed_by: "lys"`、把 `required_confirm_items` 全勾上、重算 digest → 入口门与出口门都判 `CONFIRMED`，**而用户从未看过那份确认单**。目标 5 想要的「停下来让人确认」在机器层面根本没发生 | 二选一，**必须选一个**：①（强）确认绑定一个自动化进程造不出的外部凭据（运行时用户交互事件 / 签名令牌），两道门都校它；②（诚实降级）承认它只是**内容完整性收据**，在方案里写明「本门能保证『确认单与当前 case_plan/spec/source_facts 一致』，**不能保证『人真的看过』**」，并把目标 5 的验收标准相应改写。⚠ 现状是**既没做 ① 又宣称成人确认门**，这正是本仓定义的假门。⚠ 同一结构性限制也适用于已落地的 CP-B0 `decisions`（`source: "user"` 是自报字段）——**这里只作如实记账，不构成对 CP-B0 的改动要求** |

---

### 本轮能不能做掉（逐个评估）

| ⬜ 批次 | 本轮能做吗 | 为什么 | 估工 |
|---|---|---|---|
| A-① / A-② / A-③ | ❌ **不能** | 全部要改 `plugin/acc-common/`（新脚本 + `run_workflow.py` 两处门 + 测试），而**本 lane 的文件范围只有仓根 `AGENTS.md` 与两份 doc**，`plugin/` 由并行 lane 持有，同轮动会撞车 | A.3b 地基 ~1.5 天；A-① ~1 天；A-② ~0.5 天 |
| B-2 | ❌ **不能** | 同上（改 `gen_cases.py` + `validate_preparation_state.py`），且方案 §6 的「依赖 A」仍成立，A 没落地就做会返工 | ~1 天，须在 A 之后 |
| C | ❌ **本轮不落地**（但已把成本降到最低） | 交付物是**新建** `doc/oprunway-case-axis-design.md`，同样不在本 lane 认领的三份文件里；新增一份要交人评审的设计文档应单独立项。**已把起草提纲与规模估算写进上面的 C 节**，下一个 lane 可直接照着写 | 起草 0.5–1 天，不动代码 |
| D0 | ❌ **不能** | 改 `run_workflow.py` + 新脚本 + 测试 | ~1 天（可复用 A-① 的收据校验代码；⚠ 落地时必须同时修掉上表第 2、3 条） |
| D | ❌ **不能**（文件范围），⚠ **但不得再当「可整批推后」** | 依赖清单要按子目录重算（触及 `samples/` 的 15 个测试里，真正依赖 `samples/specs/` 的是 11 个），否则动手会连带拆掉回归 pin。⚠ 方案 §6「有了 D0，D 降为顺手清掉诱饵」的理由**已被本判定推翻**（见上文 D 节）：D0 的收据由产 spec 的同一个 NL producer 出，producer 抄错样例时收据照样合法 ⇒ D 是与 D0 正交的独立措施 | 走 B 路（标记位）~0.5 天；走 A/C 路 ≥2 天且要重取字节 pin |

**推荐下一轮顺序**（在原方案 §6 基础上按本次判定微调）：

```
A-③ 地基（run_state）──► A-① 确认门 ──► A-② 执行路径
                              └────────► D0（复用收据校验代码，同时修掉 3 条 Critical 的 #2 #3）──► D
C 设计说明   ← 可即刻起草，不必等 A（见上文 C 节的依赖澄清）
B-2          ← 依赖 A
B-1          ← ✅ 已完成，无需排期
```

---

> 以下为**原方案正文**（写于本轮之前，一字未改，作历史材料）。与上面「分批判定」冲突时以判定为准。

---

## 0 · 本方案要解决的根问题

这次 aclnnRoll 试跑（findings §2 C1/C2）暴露的不是「缺规则」，而是**规则齐全但没有硬门**：

| 已有的规则 | 位置 | 这次被绕过的方式 |
|---|---|---|
| 「runner/harness 自检证据不满足 → 停在 CP-C、不上正式 Task2/Task3」 | `skills/acceptance-workflow/SKILL.md:161` | 编排层自己决定「不再深挖基建」，转去写报告 |
| 「任一路未过验证都不上 CP-D、不产真机验收裁决」 | `agents/op-acceptance.md:63` | 同上 |
| 「`needs_review` 不当 pass」 | `agents/op-acceptance.md:67` | 报告 §6.1「通过项」表里写了 3 条 ✅ PASS |
| 确定性 renderer 存在且只在 `acceptance.json` 之后调用 | `render_acceptance_markdown.py`；`run_workflow.py:560` | 用 `Write` 直接手写了一份报告，绕过 renderer |

**所以本方案的每一条修法都必须落成工件硬门或确定性产物，不能再加一句纪律。**

---

## 批次 A · 事前确认 + 运行期守门 + 事后实录（目标 5 / 6 / 7）

### A.1 现状盘点

| 项 | 现状 |
|---|---|
| 用例计划账本 | ✅ **已有且很完整**——`gen_cases --dry-run --ledger-out <case_plan.json>` |
| 人读确认单 | ❌ 无 |
| 确认收据 | ❌ 无（`grep -rn "execution_plan_confirmation" plugin/` → 0 命中） |
| 事后执行路径文档 | ❌ 无 |
| 耗时记录 | ❌ **零**——全仓非测试代码 grep `perf_counter` / `time.monotonic` / `elapsed` / `duration` 均 0 命中 |
| 偏离检测 | ❌ 无 |
| 设计稿 | ⚠ `doc/oprunway-execution-direction-review-checklist.md` 是**评审稿，未实现** |

**好消息：`case_plan.json` 账本已经把确认单要的**计划层**数据备齐了。** 实测字段：

```
schema / schema_version
spec_binding.{op, sha256, canonical_json}
planner_binding.{implementation, gen_cases_py_sha256,
                 logic_files.{gen_cases.py, repo_adapter.py, precision_policy.py},
                 seed, default_case_target, default_golden_cost_budget,
                 case_profiles, operator_classes}
planning.{case_target, runner_form, case_profile, case_profile_declared,
          operator_class, input_ranks, golden_out_shape, golden_cost_note,
          perf_case_policy.{case_source, case_selection, shape_classification}}
golden_dependency.{status, bytes_sha256, contract_sha256}
summary.{emitted, pool_max, forced_total, forced_special, shapes, shape_classes,
         by_dtype, id_kinds, special}
coverage.{strength, golden_cost, dropped_combo_classes, unpaired_combo_classes}
```

**它已经绑定了 `spec_binding.sha256` 和 `planner_binding.logic_files` 的逐文件 sha256**——
确认收据可以直接锚在这份账本的 digest 上，不用另造数据。

⚠ **「备齐」只指计划层字段，不含 golden 实算结果。**
`golden_dependency.status` 在加载不到 `golden.py` 时为 `未核`，
此时 `coverage.golden_cost.model` 也标「未核」。
**确认单必须把这个状态显示在最前面**，不能让人以为覆盖账已经核过（见 A.4 停点取舍）。

### A.2 时点澄清：checklist 与用户目标 6 说的不是同一份

| | `checklist` 的设计 | 用户目标 6 |
|---|---|---|
| 时点 | CP-B 之后、**golden 之前** | **整个流程结束后** |
| 性质 | **事前**确认单——「接下来准备怎么验」 | **事后**实录——「实际是怎么验的」 |

**两份都要，而且必须互相对账。** 事后那份的核心价值就是**计划 vs 实际的差异表**——
这次 Roll 的问题（计划里有 complex64、实际 `dtype_tested` 没有）会在对账表里直接现形。

### A.3 三件产物

```
<报告根>/ops/<Op>/<Op>.execution-plan.md        ← 事前·人读确认单
<报告根>/work/execution_plan_confirmation.json  ← 事前·机器确认收据（内容寻址）
<报告根>/execution-path.md                      ← 事后·执行路径实录（含耗时 + 计划实际对账）
<报告根>/work/run_state.json                    ← 运行期·统一状态账本（CP 状态 + 耗时 + 重试计数，见 A.3b）
```

### A.3b ⚠ 统一状态账本 `run_state.json`（A.5 / A.6 / A.7 共用的地基）

**codex 审出**：A.5 的计数器、A.6 的耗时、A.7 的 CP 状态各自为政，
各有各的绕过方式（删文件 / 换入口 / 换工作目录 / 篡改），
且**没有一处定义「无 `acceptance.json` 时 CP 状态的权威来源」**。

**修法：三者合并为一份运行绑定的原子状态账本，渲染器只消费它、不自己推断状态。**

```jsonc
// <报告根>/work/run_state.json   —— 非内容寻址（内容随运行变化），但有运行绑定与防篡改
{
  "schema": "oprunway.run_state", "schema_version": 1,
  "run_binding": {
    "run_id": "<= sha256(spec_sha256 + case_plan_digest + out_dir_realpath) 前 16 位，可复算>",
    "out_dir_realpath": "<报告根绝对路径>",
    "spec_sha256": "...",
    "case_plan_digest": "...",
    "source_facts_digest": "...",
    "case_data_digest": "<正式 caseset 的 .npy Merkle 摘要；批次 B-2 写入，此前为 null>",
    "logic_files": { "run_workflow.py": "...", "gen_cases.py": "..." }
  },
  "cp_states": [                          // ← A.7 的权威来源
    {"cp": "CP-A", "status": "done",     "artifacts": ["source_facts.json"], "seconds": 192.4},
    {"cp": "CP-C", "status": "blocked",  "artifacts": [], "seconds": 2463.1,
     "blocked_reason": "缺 work/aclnn_harness_trust.json"}
  ],
  "gate_attempts": { "preflight_aclnn": 4 },   // ← A.5 的计数器
  "total_seconds": 3180.0,                     // ← A.6 的耗时
  "self_digest": "<对 {run_binding, cp_states, gate_attempts} 的 sha256；见下方能力边界>"
}
```

**能防什么、不能防什么（⚠ 别把它当安全边界）**：

| 情形 | 能不能防 | 说明 |
|---|---|---|
| 半份写入 / 崩溃残留 | ✅ 能 | 原子写（复用 `content_address.atomic_write_json`）+ `self_digest` 复核 |
| 手滑改错字段 | ✅ 能 | `self_digest` 对不上即 BLOCKED |
| 删掉文件重置计数 | ✅ 能 | **缺文件但 `work/` 下已有下游工件（`caseset.json` / `evidence.json` 等）→ 判状态丢失，BLOCKED** |
| 直接调子脚本绕过入口 | ✅ 能 | 同 local-source-plan §2.9：**出口也校**——写 `acceptance.json` 前必须校 `run_state` 完整且 CP 链无缺口 |
| **主动篡改后重算 `self_digest`** | ❌ **防不住** | 单机、无密钥，摘要谁都能重算。这不是防篡改机制 |
| **换个干净 `--out` 目录重跑** | ❌ **不防，也不该防** | 换目录重来是合法操作；见下 |

⚠ **诚实边界**：`self_digest` 只防**意外**（半份写入、手滑），**不防有意绕过**。
本仓已有同类诚实注记的先例（`validator` 对 evidence 数值的 provenance 边界）。
真正拦住这次 Roll 那类问题的是**出口门 + CP 链完整性**，不是这个摘要。

⚠ **换 `--out` 目录的处理：记录，不阻止**（codex 审出初稿在此含糊）：

- `run_id` 由 `sha256(spec_sha256 + case_plan_digest + out_dir_realpath)` **派生**，可复算、非随机
- 换目录 → `run_id` 不同 → **新 run，计数从 0**。这是**预期行为**，不视为绕过
- **但**：`render_execution_path.py` 渲染时，若 `--out` 下存在
  `work/prior_runs/<run_id>.json`（上一轮结束时归档的 `run_state` 快照），
  必须在执行路径文档里**并列列出历史 run 的 blocked 记录**
- 归档动作：`run_workflow` 每次开跑时，若已有 `run_state.json` 且 `run_id` 不同
  → 先把旧的移进 `work/prior_runs/`，**不删**

  ⚠ 这只覆盖「同一目录换输入」的情况。**「换到全新目录重跑」在文件系统上无法追溯**——
  这条要如实写进执行路径文档的诚实边界节，不要假装能追。

**字段级 schema 契约**（实施时按此写校验函数，缺一即 BLOCKED）：

| 字段 | 类型 | 约束 |
|---|---|---|
| `schema` | str | 恒 `"oprunway.run_state"` |
| `schema_version` | int | 恒 `1` |
| `run_binding.run_id` | str | 16 位小写 hex；须等于按定义复算的值 |
| `run_binding.out_dir_realpath` | str | 绝对路径，须等于 `os.path.realpath(out_dir)` |
| `run_binding.spec_sha256` / `case_plan_digest` / `source_facts_digest` | str | 64 位小写 hex |
| `run_binding.case_data_digest` | str \| null | 64 位小写 hex 或 `null`（B-2 之前恒 null） |
| `run_binding.logic_files` | object | 键为文件名，值为 64 位小写 hex；至少含 `run_workflow.py` / `gen_cases.py` |
| `cp_states` | array | 非空；元素按 CP 顺序**严格递增不重复** |
| `cp_states[].cp` | str | ∈ 受控词表 `{CP-A, CP-B, CP-B2, CP-C, CP-D, CP-E, CP-F}` |
| `cp_states[].status` | str | ∈ `{done, blocked, skipped, not_started}` |
| `cp_states[].artifacts` | array[str] | 相对 `<报告根>` 的路径；`status=done` 时非空 |
| `cp_states[].seconds` | number | ≥ 0；`status=not_started` 时须为 0 |
| `cp_states[].blocked_reason` | str | `status=blocked` 时**必填非空**，其余须缺席 |
| `gate_attempts` | object | 键为脚本名，值为非负整数 |
| `timing_contract` | object | 恒 `{unit:"seconds", clock:"wall_clock_monotonic", includes_subprocess:true}` |
| `total_seconds` | number | 须等于 `sum(cp_states[].seconds)`（容差 0.1s） |
| `self_digest` | str | 64 位小写 hex；须等于对 `{run_binding, cp_states, gate_attempts}` 的 `content_address.canonical_json_bytes` 取 sha256 |

**`required_confirm_items` 的契约**（`render_execution_plan.py` 产、
`validate_execution_plan_confirmation.py` 消费）：

```jsonc
{
  "schema": "oprunway.required_confirm_items", "schema_version": 1,
  "case_plan_digest": "<产它时绑定的 case_plan digest>",
  "items": [                                    // 受控词表，不许自由发挥
    "case_count", "case_profile", "dtype_coverage", "shape_coverage",
    "dropped_combo_classes", "unpaired_combo_classes",
    "precision_standard", "golden_source", "perf_case_policy",
    "operator_class_narrowing", "golden_cost_status"
  ]
}
```

⚠ **`items` 是受控词表**：渲染器只能从这 11 项里选（有缺口的项必选），
校验方按同一词表比对。**不许渲染器自造新项名**——否则
`set(confirmed_items) ⊇ set(required_confirm_items)` 这条判据会因拼写不一致而恒假或恒真。

⚠ **`run_state.json` 不进任何内容寻址 payload**——它每次运行都不同，
塞进去会破坏 `case_plan` / `acceptance` 的 digest 稳定性
（同 local-source-plan §2.2 `producer.logic_sha256` 的教训）。

---

### A.4 事前**用例计划**确认单与收据（目标 5）

⚠ **命名统一**：本节确认的是**用例计划**（`case_plan.json`），不是已落盘的 `caseset.json` + `.npy`。
停点在「计划生成后、正式用例与 golden 生成前」——全文一律称「**用例计划确认**」，避免与「用例生成后」混淆。

#### 新增脚本 `plugin/acc-common/render_execution_plan.py`

```
用法：render_execution_plan.py --root <报告根> --case-plan <rel> --spec <rel>
                               [--source-facts <rel>] --out <rel.md>
                               [--required-items-out <rel.json>]
输入：<root>/<case-plan>   case_plan.json（内容寻址，domain oprunway/case-plan/v1）
      <root>/<spec>        spec.json
      <root>/<source-facts> source_facts.json（可选；缺则确认单标「来源未绑定」）
输出：<root>/<out>                  人读确认单 markdown
      <root>/<required-items-out>   required_confirm_items 清单（供 A.4 校验完整性）
退出码：0 正常 / 2 输入缺失或不可信（domain 不符、digest 漂移）
缺失处理：case_plan 缺 → 退 2，不产半份文档
冲突处理：case_plan.spec_binding.sha256 ≠ 实际 spec 的 sha256 → 退 2 并指出漂移
```

内容按 `checklist` §4 的 12 项组织，**每项标注「任务书事实 / 用户确认 / 方案选择」三选一**
（`checklist` §4.7 原话：「方案性选择必须明确标出。例如『rank 1～8』若不是任务书规定，
就应标记为用例设计选择，而非任务书事实」）。

最小必含（直接从账本取，不新算）：

| 展示项 | 数据来源 |
|---|---|
| 用例总数 / 池上限 / 强制下限 | `summary.{emitted, pool_max, forced_total}` |
| 造例档位 + 是否显式声明 | `planning.{case_profile, case_profile_declared}` |
| dtype 覆盖 | `summary.by_dtype` + `spec.dtype_required` 对照 |
| shape 与 shape 类 | `summary.{shapes, shape_classes}` |
| **被丢弃的组合类** | `coverage.dropped_combo_classes`（**必须显示条数与前若干条**） |
| **从未配对的 attr×shape** | `coverage.unpaired_combo_classes` |
| 特殊场景 / 是否产 NaN·Inf | `summary.special` + `planning.operator_class` |
| 精度标准 | `spec.precision.standard` + 任务书引文锚 |
| 性能 case 口径 | `planning.perf_case_policy` |
| golden 来源 | `golden_dependency` + `spec.golden` |

**⚠ 必须显式呈现「缺口项」**，不能只报好消息：

```markdown
### ⚠ 需要你确认的缺口

| 项 | 状况 |
|---|---|
| dtype 覆盖不全 | 任务书要求 [f16,f32,i8,u8,i32,complex64]，本计划覆盖 5 种，**缺 complex64** |
| 被丢弃的组合类 | 34 类（pool_max=528，emitted=56，丢弃 472） |
| 未配对组合 | attr `dim=-1` × shape 类 `all_unit` 从未同时出现 |
```

#### 新增脚本 `plugin/acc-common/validate_execution_plan_confirmation.py`

```
用法：validate_execution_plan_confirmation.py --root <报告根>
        --confirmation <rel> --case-plan <rel> --spec <rel>
        [--source-facts <rel>] [--required-items <rel.json>]
输出：stdout 打印判定 JSON；退出码 0=CONFIRMED / 1=NOT_CONFIRMED / 2=输入不可信
判定顺序（任一不过即停，错误信息指明是哪一条）：
  ① 收据 envelope 与 domain 合法
  ② status == "confirmed" 且 requested_changes == []
  ③ 三个绑定值 == 校验方**当场重算**的值（不读自报）
  ④ set(confirmed_items) ⊇ set(required_confirm_items)
  ⑤ confirmed_by 非空且不等于自动填充占位符
```

内容寻址收据，domain `oprunway/execution-plan-confirmation/v1`：

```jsonc
{
  "digest": "...", "domain": "oprunway/execution-plan-confirmation/v1",
  "schema_version": 1,
  "payload": {
    "status": "confirmed" | "requested_changes" | "rejected",
    "case_plan_digest": "<case_plan.json 的 digest>",
    "spec_sha256": "<= case_plan.spec_binding.sha256>",
    "source_facts_digest": "<= source_facts.digest>",
    "confirmed_items": ["case_count", "dtype_coverage", "precision_standard", ...],
    "requested_changes": [],
    "confirmed_by": "<用户标识，非自动填>",
    "note": "<自由文本，可空>"
  }
}
```

**失效规则**（`checklist` §7 已设计，照做）：
`case_plan_digest` / `spec_sha256` / `source_facts_digest` 任一与当前不符 → **旧确认自动失效**。

⚠ **两条 codex 审出的必补规则**：

1. **三个绑定值必须由校验方当场从当前文件重算**，不得读收据里自报的值
   —— 否则伪造收据时把三个值一起编了就过。
   实现：`validate_execution_plan_confirmation.py` 自己
   `content_address.read_artifact()` 读当前 `case_plan.json` / `source_facts.json` 算 digest，
   再与收据里的比。
2. **`confirmed_items` 必须完整覆盖确认单列出的全部待确认项**
   —— 否则「只勾了 case_count 就算 confirmed」会让 dtype 缺口悄悄过关。
   实现：确认单渲染时同步产 `required_confirm_items`（写进 `case_plan` 的兄弟文件或收据的输入），
   校验时断言 `set(confirmed_items) ⊇ set(required_confirm_items)`，缺项即拒。

#### 硬门落点

⚠ **停点放在 golden 生成之前**，理由与取舍：

- `--dry-run` 是 **plan-only**：不调 `golden_fn`、不落 `.npy`，但**会加载 `golden.py` 取 `out_shape`**
  → 账本里 `summary.pool_max` 与 `coverage.golden_cost` **是实的**，不是空壳
- 唯一的降级：`golden_dependency.status` 在加载不到 golden.py 时标 `未核`，
  此时 `golden_cost.model` 也标「未核」——**确认单必须把这个状态显示出来**，
  不能让人以为覆盖账已核

门的位置（两处，同 local-source-plan §2.9 的口径：入口 + 出口都要）：

| 位置 | 拦什么 |
|---|---|
| ① `run_workflow.py` 进 Task1 正式生成之前 | 正常路径 |
| ② 写 `acceptance.json` 之前 | 绕过入口的路径 |

判据：`work/execution_plan_confirmation.json` 存在 且 `status == "confirmed"`
且三个绑定值与当前一致 且 `requested_changes` 为空。

**非交互逃生阀**：`--assume-confirmed <收据路径>`，**必须显式给路径**，
不接受「没有收据就跳过」。CI / 批量回归用它。

### A.5 运行期偏离检测（目标 7 的一半）

**⚠ 不做单独的监工 agent。** 理由：这次的教训恰恰是「靠 agent 自觉」不管用
（findings §2 C1：`SKILL.md:161` 与 `op-acceptance.md:63/67` 三条规则齐全，全被绕过）。
再加一个**同样是 agent** 的监工，它同样可以自己决定「算了不管了」。

**做成确定性硬门**，两条都是代码能判的：

#### ① 前置工件门（状态机口径）

每个 CP 有明确的前置工件，缺了不许进下一步：

```
CP-A  →  source_facts.json + task_doc.snapshot.md
CP-B  →  <Op>.spec.json + case_plan.json
CP-B2 →  execution_plan_confirmation.json (status=confirmed)     ← A.4 新增
CP-C  →  aclnn_preflight.json → aclnn_harness_trust.json
         （cpp_extension 走 CPP_EXTENSION_BUILD_LOAD_AND_HARNESS_TRUST_GATE）
CP-D  →  evidence.json + verdict.json
CP-E  →  acceptance.json → 才允许渲染验收报告
```

CP-C 已有雏形（`run_workflow.py:313` 的 trust 复核）。**本批把它补全成每个 CP 都有。**

#### ② 重试计数器

同一确定性脚本连续 N 次返回 `BLOCKED` → 编排层**必须停下上报**，不得继续尝试。

实证依据：这次 `preflight_aclnn` 连挂 4 次（日志 712 / 722 / 739 / 745）无人管，
第 5 次编排层放弃流程转去写报告。**N = 3 建议值**，写进 `run_workflow` 的常量。

落地形态：写进 **A.3b 的 `run_state.json.gate_attempts`**（不另开文件——
独立文件更容易被删掉重置计数，见 A.3b 的防绕过表）。
超阈值 → `run_workflow` 拒绝继续并输出「需人工介入」的诊断收据。

#### ③ 观察者的正确定位（若将来要）

**只读的记录者 / 告警器**（产 A.7 的执行路径文档），
**不是**有权决定继续还是停止的裁决者——后者会变成第二个可被绕过的纪律层。

### A.6 耗时记录（目标 7 的另一半）

**落在确定性脚本自己身上**，不靠编排层观察。

打点位置（`run_workflow.py` 每阶段首尾）：

```
gen_cases / preflight / harness_trust / task2_exec / task3_perf / gate_task1..3 / render
```

**⚠ 不能进内容寻址 payload**——耗时每次都不同，塞进去会破坏 digest 稳定性
（同 local-source-plan §2.2 的教训：`producer.logic_sha256` 进 payload 导致 digest 不稳）。

落点：**并入 A.3b 的 `run_state.json`**（`cp_states[].seconds` + `total_seconds`），
**不另开 `timing.json`**——独立的可变文件没有运行绑定也没有防篡改，
codex 审出这正是一条绕过路径。字段口径：

```jsonc
// run_state.json 里的口径声明（写死在 schema，否则不同实现记出来的数不可比）
"timing_contract": { "unit": "seconds", "clock": "wall_clock_monotonic",
                     "includes_subprocess": true }
```

口径要写死在 schema 里（wall-clock、含子进程），否则不同实现记出来的数不可比。

### A.7 事后执行路径文档（目标 6）

#### 新增脚本 `plugin/acc-common/render_execution_path.py`

```
用法：render_execution_path.py --root <报告根> --run-state <rel> --out <rel.md>
        [--case-plan <rel>] [--caseset <rel>] [--acceptance <rel>]
输入：run_state.json（**必需**，状态唯一权威来源）
      case_plan / caseset / acceptance（**可选**，有则参与「计划 vs 实际」对账，无则该行标「未产出」）
输出：execution-path.md
退出码：0 正常 / 2 run_state 缺失或 integrity 复核不过（不猜、不出文档）
措辞断言：未走完时输出必须含 NON-ACCEPTANCE，且不得含 PASS / 已产证 / 等效覆盖
          （渲染后做字符串黑名单自检，命中即退 2，防模板改坏）
```

⚠ **必须是确定性渲染器，禁止手写**——这正是 findings §2 C2 的教训
（仓里已有 `render_acceptance_markdown.py`，坏报告来自手写旁路）。

**⚠ 关键约束：它不能以 `acceptance.json` 存在为前提。**
这次就是流程没走完，而**没走完的情况最需要这份文档**。
所以它要能渲染「BLOCKED 在哪一步、缺什么」的形态。

内容三段：

**① 执行路径**

| CP | 状态 | 耗时 | 产物 |
|---|---|---|---|
| CP-A 取材 | ✅ 完成 | 3m12s | `source_facts.json` |
| CP-B 造 spec + 用例 | ✅ 完成 | 8m45s | `spec.json` / `case_plan.json` / `caseset.json` |
| CP-B2 执行方向确认 | ✅ confirmed | — | `execution_plan_confirmation.json` |
| CP-C 信任门 | ❌ **BLOCKED** | 41m03s | 缺 `aclnn_harness_trust.json` |
| CP-D 真机跑测 | ⬜ 未启动 | — | — |
| CP-E 报告 | ⬜ 未启动 | — | — |
| **总计** | **BLOCKED** | **53m00s** | |

**② 计划 vs 实际对账**（本节是整份文档的核心价值）

| 项 | 事前计划 | 实际 | 差异 |
|---|---|---|---|
| dtype 覆盖 | 要求 6 种（含 complex64） | 实测 5 种 | ⚠ **缺 complex64**，原因：生成层 `_NATIVE` 不支持 |
| 用例数 | 50 | 生成 50 / **执行 0** | ⚠ Task2 未启动 |
| 精度标准 | `ascendoptest_default` | 未执行 | — |

**③ BLOCKED 诊断**（仅在未走完时出现）

```
卡点：CP-C harness 真机信任门
直接原因：work/aclnn_harness_trust.json 不存在
上游原因：preflight_aclnn 连续 3 次 BLOCKED（source_facts completeness 不是 complete）
需要的输入：--pr 链接 或 --local-repo（见 doc/oprunway-local-source-plan.md）
```

**措辞硬约束**（承 findings §2 C2）：
未走完时产出的文档**必须**标 `NON-ACCEPTANCE`，
且**禁止**出现 `PASS` / `已产证` / `等效覆盖` 字样。建议在渲染器里做成字符串黑名单断言。

### A.8 批次 A 的测试

**正路**：
1. `render_execution_plan.py` 对一份现成 `case_plan.json` 能产出确认单，
   且缺口节列出 `dropped_combo_classes` 条数与 dtype 差集
2. 写一份 `status=confirmed` 的收据 → `run_workflow` 放行
3. 全程走完 → `execution-path.md` 三段齐全，对账表无差异

**负路（更重要）**：
4. **无确认收据** → `run_workflow` 拒绝进 Task1 正式生成
5. **收据 `status=requested_changes`** → 拒绝
6. **改一个字节 spec 后复用旧收据** → `spec_sha256` 不符 → 拒绝（失效规则生效）
7. **绕过入口直接调子脚本产 `acceptance.json`** → 出口门拦住
8. 同一脚本连续 3 次 BLOCKED → `run_workflow` 停下，产诊断收据而非继续
9. **CP-C BLOCKED 时** → `render_execution_path.py` 仍能出文档，
   且文档含 `NON-ACCEPTANCE`、不含 `PASS` / `已产证` / `等效覆盖`
10. `run_state.json` 不参与任何 digest（改耗时不影响 `case_plan` / `acceptance` 的 digest）
11. **删掉 `run_state.json` 但 `work/` 下已有下游工件** → 判篡改，BLOCKED（不许靠删文件重置计数）
12. **手改 `run_state.json` 的 `gate_attempts`** → `integrity` 复核不过 → BLOCKED
13. **换 `--out` 目录重跑** → `out_dir_realpath` 不符 → 认作新 run，计数从 0，但旧 run 的 blocked 记录仍在报告体现
14. **收据自报三个绑定值全对但文件已改** → 校验方当场重算 → 不符 → 拒绝
15. **`confirmed_items` 少勾一项** → 与 `required_confirm_items` 比对 → 拒绝
16. **`run_state.json` 缺失时跑 `render_execution_path.py`** → 退 2，**不产半份文档**

---

## 批次 B · 数据用例确定性（目标 3）

### B.1 现状：per-case 确定性已做对，但绑定 numpy 版本

`gen_cases.py:1461-1465`：

```python
def _case_rng(case_id):
    """per-case 独立种子（评审 #7）：数据只依赖稳定 case_id，与选择/顺序/target 全解耦。
    同一 case_id 在任何 target/子集下产同一字节，扩 target 不改老用例。"""
    h = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:16], 16)
    return np.random.default_rng((SEED ^ h) & ((1 << 64) - 1))   # SEED = 2026
```

改 `case_target`、换档、跑子集**都不会**改变已有 case 的字节。**这条设计是对的，别动。**

**但 numpy 随机流跨版本会漂。** 仓内测试自己写着
（`test_gen_cases_dtype_attr.py:2003` 及上方注释）：

```python
# ⚠ 摘要与 numpy 的随机流绑定：numpy 大版本变了摘要可能整体漂 → 版本不符时 skip 并说明
_U3_BASELINE_NUMPY = "2.4"
```

**实证**：Roll 那台 numpy **2.4.6**，Median 那台 **2.5.1**
→ 同一 `case_id` 在两台机器上产出的 `.npy` 字节不同。现在的处理是「测试 skip」，不是「保证不变」。

### B.2 两步走：先止血，再根治

#### B-1（小改，先做）· 版本钉进账本 + 跨版本 fail-closed

- `case_plan.json` 的 `planner_binding` 增加 `numpy_version`
- `caseset.json` 顶层增加 `numpy_version`
- 复用既有 caseset / 复算 digest 时，若当前 numpy 版本与账本不符 → **fail-closed 报错**，
  错误信息说清「同一 case_id 会产出不同字节，请对齐 numpy 版本或重新生成并重新确认」

⚠ **锁到哪一级要先定**（codex 审出）：

| 口径 | 记录值 | 判定 | 评价 |
|---|---|---|---|
| **完整版本** | `2.4.6` | 精确相等 | 最严；补丁版通常不改随机流，会造成大量无谓 fail |
| **主.次版本**（建议） | `2.4` | 前两段相等 | 与仓内既有 pin 口径一致——`test_gen_cases_dtype_attr.py` 的 `_U3_BASELINE_NUMPY = "2.4"` 就是两段，且用 `startswith(_U3_BASELINE_NUMPY + ".")` 判定 |

**建议采用「主.次」**，并**记录完整版本供诊断**（`numpy_version_full: "2.4.6"`），
判定只看前两段。这样与既有 pin 同口径，不引入第二套标准。

⚠ 这**不解决**「就是要跨版本」的场景，但至少**不再静默换数据**。

#### B-2（中改，与批次 A 配套）· 一次生成、内容寻址固化、后续复用

**这才是用户目标 3 的正解**——用户原话是「**如果要每次现场推导**，数据不能变」，
那么最好的答案是**不要每次现场推导**。

仓里**已有这条通路的骨架**：`validate_preparation_state.py:241`
`evaluate(root, spec_rel, case_plan_rel, ...) → REUSABLE | MISS | BLOCKED`。

本批要补的：把 `work/<case_id>/*.npy` 也纳入内容寻址复用——

```
caseset.json 顶层增加 case_data_digest（全部 .npy 的 Merkle 摘要）
        ↓
preparation 收据 REUSABLE 时，直接复用已有 .npy，不重跑 _make_varied
        ↓
复用前校验 case_data_digest 一致，不一致 → MISS（重新生成 + 重新走 A.4 确认）
```

**`.npy` 集合边界（必须写死，否则两次算出的 digest 不可比）**：

```
纳入：<work_dir>/<case_id>/ 下、且被 caseset.cases[].inputs[].path
      或 expected.outputs[].golden_path 引用到的文件
排除：一切未被 caseset 引用的文件（调试残留、.DS_Store、临时文件）
清单：把纳入的相对路径全集写进 caseset.case_data_manifest（排序后），
      digest 只对清单内的文件算 —— 这样「多出一个未引用文件」不会让 digest 漂，
      而「少了一个被引用的文件」一定会被发现
```

**与确认收据的绑定**：`case_data_digest` 属于**正式 caseset**（A.4 的确认停在计划层，
那时 `.npy` 还没生成）→ 所以它**不进** `execution_plan_confirmation`，
而是进 **A.3b 的 `run_state.run_binding`**，作为「本轮实际用的数据是哪一份」的运行绑定。

⚠ **`.npy` 的 Merkle 摘要算法直接复用 `local-source-plan.md` §2.3 的 `root_digest` 定义**
（kind 前缀 + 长度分帧 + `os.fsencode` 字节序 + 空目录计入），
**不要另写一套**——两套摘要算法迟早漂。

### B.3 批次 B 的测试

1. 同一 spec 连跑两次 `gen_cases` → 全部 `.npy` 逐字节相同
2. 改 `case_target` 从 50 到 60 → **原有 50 条的 `.npy` 字节不变**（保护既有设计）
3. 伪造一个不同 `numpy_version` 的 `case_plan` → 复用时 fail-closed 报错
4. `REUSABLE` 路径下不重新生成 `.npy`（用文件 mtime 或调用计数断言）
5. 改一个 `.npy` 字节 → `case_data_digest` 变 → `MISS`

---

## 批次 C · 笛卡尔轴集（目标 4）

### C.1 现状：无冗余已达成，无遗漏要看「哪些轴」

**无冗余 ✅**：
- legacy 档：`entries = forced + _one_wise_pick(grid, n, used)`，`used` 去重
- torch_parity 档：`forced_special: 0`，单一来源，天然无重复

**不抽样 ✅（仅 torch_parity）**：
- `gen_cases.py:888-892`：`case_target` 必须精确等于矩阵大小，否则 raise「禁止静默抽样」
- `:906-913`：超 golden 预算的 shape **raise 而非静默剔除**——「完整矩阵禁止静默缩形/剔除」

**真问题在轴集**：

| 轴 | legacy 档 | torch_parity 档 |
|---|---|---|
| dtype | ✅ | ✅ |
| rank | ⚠ 实际到 **5**（`_EXT_RANK_SHAPES:1452` 只补 2 条 5 维，且需 spec rank 约束点名） | ✅ **1–8** |
| **shape 形态** | ✅ **11 种**真实 shape（`_REG_SHAPES:1444`）+ **2 种**大 shape（`_LARGE_SHAPES:1453`） | ❌ **退化**：只有 `(leading,)+(1,)*(rank-1)` |
| **值域 regime** | ✅ uniform + normal（`_VALUE_REGIMES:1457`） | ❌ 只有 uniform（`generator.kind` 词表当前只收 `uniform`，`:880-886`） |
| **特殊场景** | ✅ 空/标量/边界下/边界上/inf/-inf/nan（`_special_entries:1948`） | ❌ 无 |
| **tie / value_profile** | ✅ | ❌ 无 |
| attr 组合 | ✅ | ✅ |
| **是否抽样** | ❌ 1-wise 封顶 | ✅ 完整 |

**两个档强弱互补，单独用哪个都覆盖不全。**

**实证代价**：Median 1152 基线的 **51** 条失败**全部**落在 `[262144,1,1,...]`——
（58 是上一轮 1344-case checkpoint 的数字，别与 1152 基线混写；当前基线见 `AGENTS.md` §9）
正是因为 torch_parity 的 shape 轴退化成「首轴长 + 其余全 1」，真实多维 shape 一条没测。

### C.2 ⚠ 本批**不直接改代码**，先出设计说明

**理由**：统一轴集后笛卡尔会爆炸。粗算
`8 dtype × 8 rank × 13 shape × 2 regime × 7 attr ≈ 11648 例`，再叠特殊场景更多
（实际会被 `_fit_rank` 按 rank 约束过滤，但量级不变）。

**在改代码之前必须先回答四个问题，且答案要人拍板**：

1. **哪些轴必须全交叉，哪些只需边际覆盖？**
   当前 legacy 用「1-wise + 白名单强制」是一种答案，torch_parity 用「窄轴集全交叉」是另一种。
   **两者都没被显式论证过。**
2. **`operator_class` 的收窄算不算遗漏？**
   structural 类不产 NaN/Inf 是**按方法学的合法收窄**，不是漏——
   但要在确认单上讲清楚，否则人看到「没有 NaN 用例」会以为漏了
3. **golden 生成成本是硬约束**——`_cost_budget` 存在是有原因的。
   「无遗漏」与「算得完」冲突时怎么裁？torch_parity 现在的答案是 **raise**
   （宁可不跑也不静默缩），这个口径保不保持？
4. **谁来拍板？** 轴集与预算是**执行方向确认单的一等公民**
   （`checklist` §4.7「用例范围与预算」已列该确认的五项）——
   **所以批次 C 依赖批次 A 先落地**

### C.3 交付物

`doc/oprunway-case-axis-design.md`——逐根轴论证「必须全交叉 / 边际覆盖 / 按算子类别收窄」，
并给出统一后的矩阵规模估算与 golden 成本估算，交人评审。

**评审通过后才谈改 `gen_cases`。** 本方案不预设结论。

---

## 批次 D0 · spec 来源门（原 findings D5）

**要解决的**：`run_workflow` 接受任何一份 `spec.json` 就开跑，不问它从哪来
（findings §1 的根因）。现有的两道来源门只覆盖了 golden 授权（绑任务书快照 sha256）
和 build provenance（绑 PR head），**唯独没有覆盖「测什么、测多少、按什么标准判」这组字段**。

**做法**（与 A.4 的确认收据同构，可复用同一套代码）：

```
CP-B 的 acc-spec-extractor 产 spec 时，同时产
  work/spec_origin_receipt.json   （内容寻址，domain oprunway/spec-origin/v1）
  payload: { spec_sha256, taskdoc_snapshot_sha256, source_facts_digest,
             producer: {tool: "acc-spec-extractor", dispatch_mode: "extract_spec"} }

run_workflow 入口 + 写 acceptance.json 出口两处校：
  收据存在 且 spec_sha256 == 当前 spec 的实际 sha256
  且 taskdoc_snapshot_sha256 == 当前 task_doc.snapshot.md 的 sha256
```

**效果**：手写的 spec、`cp` 来的样例 spec，都**没有这份收据** → 被拒。
这样批次 D 删不删样例都不再致命，删只是「顺手清掉诱饵」。

**D0 的测试**：
1. 正常经 CP-B 产的 spec → 有收据 → 放行
2. 手写一份 spec → 无收据 → 拒绝
3. 从 `plugin/samples/specs/` `cp` 一份 → 无收据 → 拒绝
4. 改一字节 spec 后复用旧收据 → `spec_sha256` 不符 → 拒绝

---

## 批次 D · 删掉老的 sample（目标 1）

### D.1 ⚠ `plugin/samples/` 不是纯参考资料，是 **12 个测试文件的输入**

⚠ **初稿写 7 个，漏了 5 个。** 以下为完整清单（`grep -rn '"samples"' plugin/acc-common/test_*.py` 全量）：

| 依赖方 | 引用的样例 | 性质 |
|---|---|---|
| `test_spec_isolation.py` | `samples/specs/`（整个目录） | **专门断言该目录必须存在且含 `*.spec.json`**（原话「证迁移落地、非凭空删除」）——**删了它直接红** |
| `test_gen_cases_dtype_attr.py` | `isclose.spec.json` + `ExistingOpsByteIdenticalTest` | 4 算子 caseset + 全部 `.npy` 逐字节 sha256 pin |
| `test_gen_cases_case_profile.py` | `sign.spec.json` | caseset 字节安全 pin |
| `test_gen_cases_dry_run_ledger.py` | `sign.spec.json` | 初稿漏 |
| `test_gen_cases_multi_output.py` | `median.spec.json` + `samples/golden/Median/golden.py` + **动态 `f"{path}.spec.json"`** | 初稿漏；**动态路径会遍历多份样例**，删任一都可能断 |
| `test_gen_cases_perf_shape_classification.py` | `median.spec.json` + 遍历整个 `samples/specs/` | |
| `test_ne_transport.py` | `sign.spec.json` | |
| `test_perf_msprof.py` | `median.spec.json` | |
| `test_catlass_adapter.py` | `catlass_basic_matmul.spec.json` | 初稿漏 |
| `test_validate_acceptance_state.py` | `isclose.spec.json` | 初稿漏 |
| `test_check_golden.py` | `samples/golden/` | 初稿漏 |
| `test_samples_golden_contract.py` | `samples/golden/` | |

**直接删 = 拆掉 12 个文件里的回归 pin**，其中 `test_spec_isolation.py` 是**专门为防止误删而写的**。

⚠ **实施前必须重跑这个 grep**——本清单是 2026-08-05 的快照，测试会增加。

### D.2 ⚠ 更重要：删文件解决不了根因

这次和 Median 三次的问题是「**spec 没有来源门**」（findings §2 D5）——
删掉旧样例只会让**下一份被随手 `cp` 的文件**成为新诱饵。

**所以目标 1 必须与 D5 打包做**：

```
D5（spec 来源门）：run_workflow 拒绝没有 CP-B 来源收据的 spec
      ↓  有了这道门之后
目标 1（隔离样例）：样例即使还在原地，也不可能被当成验收 spec
```

顺序反了的话，删完样例仍然可以手写一份 spec 绕过。

### D.3 三条候选路（评审后选一条）

| 方案 | 做法 | 代价 |
|---|---|---|
| **A · 改后缀 + 硬门** | `samples/specs/x.spec.json` → `fixtures/specs/x.spec.json.fixture`；`run_workflow` 加「spec 内容 hash 命中 fixture 集 → 拒绝」 | 改 **12 个**测试的路径（含一处动态 f-string 拼接）；硬门要维护 hash 清单 |
| **B · 保留文件 + 标记位** | 每份加 `"_fixture_only": true`，`gen_cases` / `run_workflow` 见到即 fail-closed | 最小改动；文件还在原地，视觉上仍像「可用样例」 |
| **C · 真删 + 测试自造** | 删除目录，pin 用的 spec 内联进测试文件 | 最彻底；要重写 **12 个**测试，`ExistingOpsByteIdenticalTest` 摘要需重取，且 `test_spec_isolation.py` 整个失去意义需删除 |

**建议 B**（配 D5 后已经足够安全，且改动最小）；若人评审倾向「眼不见为净」再走 C。

### D.4 批次 D 的测试

1. 把某份 fixture 原样 `cp` 成验收 spec → `run_workflow` **拒绝**
2. 手写一份没有 CP-B 来源收据的 spec → 同样**拒绝**（证明 D5 起作用，不只是拦样例）
3. **12 个**既有测试全绿（若走 A/C，测试路径改完后仍绿）；
   特别核 `test_gen_cases_multi_output.py` 的动态 `f"{path}.spec.json"` 路径没断

---

## 5 · 不做什么（本方案边界）

- ❌ **不做目标 2 / 8**——在 `doc/oprunway-local-source-plan.md`
- ❌ **不做 A3 complex64 能力扩展**——findings §2 A3，另一批
- ❌ **不做 A1 / A2 两个脚本 bug**（`derive_output_dtype` 的 `<from_input>`、
  `aclnn_driver.py:266` 的 py3.11 f-string）——findings §2，另一批，且优先级高于本方案
- ❌ **批次 C 不改 `gen_cases` 代码**，只出设计说明交评审
- ❌ **不引入监工 agent**（A.5 已论证）
- ❌ 不动 PR 通路 / `cpp_extension` 通路的既有产物字节
- ✅ **D5（spec 来源门）已收进本方案作批次 D0**（初稿曾遗漏认领）

---

## 6 · 批次依赖与顺序

⚠ **初稿写「A→B→C→D 全串行」，codex 审出是伪串行。** 真实依赖只有三条：

```
A ──► B-2   （固化复用的对象是「已确认过的 caseset」）
A ──► C     （轴集决定权要交给确认单承载）
D0 ─► D     （没有 spec 来源门，删样例只是换个诱饵；D0 已收进本方案）

B-1 独立    （版本钉账本，与 A 无依赖，可并行）
无循环依赖
```

**⚠ D5 曾经悬空，现认领为批次 D0**（详见上文「批次 D0」节）。
`D5（spec 来源门）` 在 findings §2 D 类里列为「未修」，且不属于 `local-source-plan`。
批次 D 依赖它，所以**本方案把它收进来作 D0**，不再留作二选一。

**推荐排期**：

| 阶段 | 内容 | 可否并行 |
|---|---|---|
| 1 | **批次 A**（5+6+7 收据骨架） | — |
| 1' | **B-1**（numpy 版本钉账本，小改） | ✅ 与 A 并行 |
| 2 | **B-2**（固化复用） | 依赖 A |
| 2' | **批次 C**（轴集设计说明，不动代码） | 依赖 A，可与 B-2 并行 |
| 3 | **批次 D0**（spec 来源门） | 依赖 A（复用确认收据的校验代码） |
| 4 | **批次 D**（隔离/删样例） | 依赖 D0 |

**本方案之前应先做**：`local-source-plan`（解掉直接起因）
+ findings §2 的 A1/A2 两个脚本 bug（小改；不修的话批次 A 的测试会被它们绊住）。

**为什么把批次 A 排在最前**（不只是「依赖多」）：

1. **数据已经备齐**——确认单要的字段全在 `case_plan.json` 里（A.1），
   主要工作是渲染器 + 硬门，不用先造数据结构
2. **它同时解掉 findings §2 的 C1 + C2**——「BLOCKED 后自行降级」与
   「手写报告绕过 renderer」正是这次 Roll 的根因，A 落地即封死
3. **它是 C 和 D0 的前置**：C 的轴集决策要有地方承载（确认单），
   D0 的来源门可直接复用 A.4 的收据校验代码

**批次 D 的优先级降了**（初稿把它当独立目标）：
有了 D0，手写 spec 与 `cp` 来的样例都拿不出来源收据、一律被拒
→ 目标 1 从「必须做」降为「顺手清掉诱饵」。
若排期紧张，**D 可以整批推后**，不影响其余目标的完整性。

---

## 7 · 验收标准（整批做完怎么算过）

以这次 aclnnRoll 的场景做见证（**不为 Roll 写任何特判**）：

1. **目标 5**：跑到**用例计划生成后**（正式 caseset 与 golden 之前）**自动停下**，
   产出 `<Op>.execution-plan.md`；
   其「⚠ 需要你确认的缺口」节**显式列出** dtype 差集与 `dropped_combo_classes` 条数
2. **目标 5 硬门**：不写确认收据 → `run_workflow` 进不了正式 Task1；
   改一字节 spec 后旧收据失效
3. **目标 7 耗时**：`work/run_state.json` 的 `cp_states[].seconds` 覆盖全部阶段，
   `total_seconds` 与各段之和一致；且它不参与任何 digest；`integrity` 可复核
4. **目标 7 偏离**：同一脚本连续 3 次 BLOCKED → 停下产诊断收据，**不继续尝试**
5. **目标 6**：流程 BLOCKED 在 CP-C 时，仍能产出 `execution-path.md`，
   含三段（路径 / 计划实际对账 / BLOCKED 诊断），标 `NON-ACCEPTANCE`，
   且**不含** `PASS` / `已产证` / `等效覆盖`
6. **目标 6 对账**：对账表里 complex64 那一行显示「计划要求 / 实际未覆盖 / 原因」
7. **目标 3**：同 spec 连跑两次 `.npy` 逐字节相同；换 numpy 版本复用时 fail-closed
8. **目标 4**：`doc/oprunway-case-axis-design.md` 产出并经人评审（**不含代码改动**）
9. **目标 1**：`cp` 一份 fixture 当验收 spec → 被拒绝；手写无来源收据的 spec → 也被拒绝

---

**本方案未实施。** 按 §6 的批次顺序推进，每批跑完对应测试再进下一批；
批次 C / D 需先经人评审再动代码。全部落地后在 `doc/oprunway-changes-brief.md`
顶部追加倒序摘要。
