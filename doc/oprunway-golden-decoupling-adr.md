# ADR（草案）· Golden 去引擎化：精度 golden 从引擎硬表改为按算子加载

> **状态：6 条决策已由用户 2026-07-20 逐条拍定**（见 §6）；**代码未实施**。按 CLAUDE.md #1，设计已点头，
> 下一步走 bureau `capture → compile → review` 成正式 canon ADR（暂拟 **ADR 0011**），再另开 PR 落代码。
> **前身**：`doc/oprunway-plugin-op-decoupling-design.md`（D1 / S1–S3 + 开放问题 #5）。**承接** runner 去引擎化（PR #7）。

## 0 · 边界先钉（免和别的东西串）

本 ADR **只管精度 golden 值**——拿来和 NPU 输出逐点比的「正确答案」，就是 `gen_cases.py:148` 那张 `GOLDEN` 硬表干的事：

- ✅ **在范围**：精度 golden **值**（每条用例的正确答案，从输入现算）。
- ❌ **不在范围**：精度**标准**（阈值 / oracle 口径 / MERE-MARE）——走 ascendoptest / opbase，**不动**；性能**基线**（msTuner / TBE 内置 / GPU 标杆）——那不叫 golden。
- 判精度 = `|NPU 输出 − golden 值|` 是否满足**精度标准**。本 ADR 只动前半的「golden 值怎么来」。

## 1 · Context（为什么要这刀）

`gen_cases.py:148` 把 4 个算子的 golden 写死成函数（`GOLDEN = {"IsClose": (…, golden_isclose), "Sign":…, "Equal":…, "Neg":…}`）；
`gen_cases.py:562` 第 5 个算子 `raise "unsupported op"`。即：**引擎内置了 4 个算子的「正确答案」、只认这 4 个**——
这是「插件肚子里带着 IsClose 的现成答案」的机制性根因（D1）。runner 那刀（PR #7）已让「怎么跑」成为输出；
**golden 这刀让「正确答案」也从引擎搬出去，引擎才真正 op-中立、能接任意算子。**

## 2 · Canon 现状（按 trust tier；载重前据此，冲突显式标）

| 出处 | tier | 与本 ADR 的关系 |
|---|---|---|
| [[Golden and precision standard come only from the task-doc-specified method]] | proposed（最高律令） | **约束决策 3**：golden 值只来自任务书指定方法、不支持则 fail-closed |
| [[Golden is fixed to torch on CPU for determinism]] | proposed | **被决策 4 更新**（「恒 torch 单后端」→「按算子 torch>numpy 定档」）；compile 时标 superseded、留 proposed 待 review |
| [[AscendOpTest precision thresholds]] | canonical | golden 由 `expect_func` 提供、输出 dtype 须与算子一致——加载器契约照此 |
| [[oracle_source is a hardcoded constant not a recorded fact]] | proposed（Q9 已止血） | **决策 5 承接**：oracle_source 已据实映射 + 门校；loader 侧补「按算子记录」 |
| [[ADR 0002 — Acceptance grounded in catlass and the spec]] | canonical | 只说「跨仓 golden 会变」、**未规定 golden 归属**——**决策 2 补这个洞** |
| [[OpRunway component breakdown]] | canonical | 「acc-common 核心不放 runner」——golden 同理（本 ADR 把「不放算子 golden」补上） |
| [[Primitive-to-case rule library]] | canonical | 据其理（真值口径一错则**所有精度用例作废**，golden 错亦然）——决策 3 的来源分级即为防此 |

## 3 · 决策（逐条点头）

### 决策 1 · `GOLDEN` 硬表 → 加载器（引擎零算子 golden）【核心】

`gen_cases.GOLDEN` 由**内置字典**改为**加载器**：按 op 名从**用户侧**加载该算子的 golden（`golden_fn(inputs, attrs) → 输出`
+ 伴随元数据 `GOLDEN_SOURCE` / `GOLDEN_PROVENANCE`）；插件引擎**不含任何算子的 golden 函数**。第 5 个算子不再崩。
**fail-closed**：加载不到 / 缺元数据 / source 不在枚举内 → 直接失败，不猜、不兜底。

- **取舍**：形态已经对了——现 `GOLDEN[op]` 返回的就是 `(src_label, golden_fn)` 二元组，只是那张表焊在引擎里；「表→加载」改动不大。
- **候选**：(a) 加载器（本决策）；(b) 保留硬表、只加注释——**否**（不解决 op-中立，第 5 算子仍崩）。
- **建议**：a。

### 决策 2 · golden 归属：per-op 落用户 CWD（同 runner）

golden 与 spec / runner **同类**——per-op、用户侧、本任务现生成。落 **`<ops_root>/<op>/`**（用户 CWD 的
`.oprunway/ops/<op>/`，与 runner 并排），由 `gen_cases` 里一个 **op-中立的加载器**读。

- **取舍**：ADR 0002（canonical）只说跨仓 golden 会变、**没规定归 repo_adapter 持有**；设计把「golden 属 repo_adapter 层」列为**推导非 canon**。本决策不选「塞进 repo_adapter 的 7 方法」，而选「golden 是 per-op 输入产物、落用户 CWD、加载器读」——与 runner 一致、与工程约定「产物落用户 CWD」一致、与 component-breakdown「核心不放算子件」一致。
- **候选**：(a) 落 `<ops_root>/<op>/`、加载器读（本决策）；(b) 归 repo_adapter 层持有（设计推导）；(c) 落 `reports/`——**否**（reports 是跑测输出、gitignore，golden 是输入）。
- **建议**：a。

### 决策 3 · 公式来源政策（最高律令：只来自任务书指定方法）

golden 的**公式/参考**（≠ 用哪个库算）只来自**任务书指定的测试方法**（最高律令）。谁生成 golden：由 acc-spec /
acc-runner-dev 从任务书 + 算子 PR 参考实现**抠/生成**，**不是** agent 照语义随手写。按可信度分级、记进 `oracle_source`：

| 级 | oracle_source | 来源 | 处置 |
|---|---|---|---|
| 高 | `catlass_existing_ref` / `cpu_ref` | 仓自带 / PR 的 CPU 参考实现 | 直接用 |
| 中 | `task_spec_expected` | 任务书给的期望值表 | 直接用 |
| **低** | `analytical_ref` | **agent 按公式现写的 CPU 参考** | **排最后 + 报告标可信度 + 触发人工 CP，不静默通过** |
| — | 任务书指定方法**不在支持范围** | | **fail-closed 抛用户**、绝不静默降级 |

- **取舍**：`analytical_ref`（agent 自写）是最大的雷——写错了会「验收通过」却是错的（同 [[Primitive-to-case rule library]] 之理：
  真值口径一错则所有精度用例作废；亦如 Equal——真值/被测物一错则裁决不可信、须先解耦 root-cause）。故它必须排最后 + 人核。
- **候选**：(a) 分级 + analytical 末位人核（本决策）；(b) 只要任务书没点名参考就一律 fail-closed（更严、但很多任务书不给现成参考、会卡死）。
- **建议**：a。

### 决策 4 · 后端政策【已定 2026-07-20 = B，本 ADR 只做记录】

golden 恒 **CPU**；**按算子选后端、优先 torch API、torch 没有该算子才退 numpy**；选定后**记进 `oracle_source`**、该算子跑测时
**那个库必装（fail-closed）**、**不运行时按「谁装了」偷换**。确定性红线保住（每个算子 golden 恒定一种算法、可复现）。

- **更新** [[Golden is fixed to torch on CPU for determinism]]：从「恒 torch 单后端、绝不回退 numpy」放宽为「按算子 torch 优先、
  torch 表达不了才 numpy、选择定档记录」。**确定性理由仍在**（07-14 否掉的是「运行时谁装了用谁」的非确定，本决策按算子定档不触发它）。
- 现有 4 算子 torch 都有 → 仍全 torch；numpy 只留给「torch 没有的未来算子」。
- **状态**：用户已拍（读法 B）。本 ADR 落此记录；不再复议。

### 决策 5 · `oracle_source` 成真实字段 + 门校（承 Q9/Q7，补 loader 侧）

Q9/Q7 已做：`oracle_source` 据 `golden_source` 据实映射（删写死 `cpu_ref`）+ 门校 `evidence.oracle_source == 映射(caseset.golden_source)`。
本 ADR 补 **loader 侧**：`gen_cases` 加载 golden 时把该算子的 `GOLDEN_SOURCE` 写进**每条 case**（不再来自内置常量），门继续校。

- **取舍**：这样门校的是「实际加载的那份 golden 的来源」、不是内置假设——闭合 [[oracle_source is a hardcoded constant not a recorded fact]]。
- **建议**：照做（增量、无争议）。

### 决策 6 · 安全边界：执行用户侧 golden

golden 若是 `golden.py`（`golden_fn` 函数），加载 = **动态 import 执行用户侧 Python**。这是真实执行面。

- **取舍**：性质**同 runner.cpp**（本就要编译并在 NPU 上跑）——同一信任级。且 golden 由 acc-spec/acc-runner-dev **从任务书生成**、
  非用户任意上传，风险可控；但仍是执行，**须显式说清、不装看不见**。
- **候选**：(a) `golden.py` 动态 import（灵活、能表达任意 CPU 参考；执行用户/生成的 Python）；(b) 受限声明式 golden（更安全、但表达力弱、复杂算子写不出）。
- **建议**：a + 文档显式标注执行边界（与 runner 对齐）；(b) 作为「若某类算子只需查表/线性」时的可选简化，不强制。

## 4 · Consequences

- 引擎**真 op-中立**：第 5 个算子不再在 `gen_cases:562` 崩；「加算子 = spec + golden + runner 三件、皆用户侧」成立（对齐 CLAUDE.md 自述）。
- [[Golden is fixed to torch on CPU for determinism]] 被决策 4 更新 → compile 时据新决策标 superseded、留 proposed 走人门 review。
- **工作项（点头后另开 PR、不在本 ADR）**：`gen_cases` 表→加载器 + 加载器落点契约（`<ops_root>/<op>/golden.py`）+ `GOLDEN_SOURCE`/`GOLDEN_PROVENANCE` 元数据 + oracle_source loader 侧接线 + acc-spec/acc-runner-dev 产 golden 的纪律（最高律令 + 分级 + 人核）+ 安全边界文档 + 单测改用 fixture golden（不依赖内置 4 算子）。
- **不动**：精度标准（ascendoptest/opbase）、性能基线、runner（已去引擎化）。

## 5 · Alternatives（整体层面已否）

- **保留硬表**：否——不解决 op-中立，第 5 算子崩、「插件带答案」不变。
- **golden 归 repo_adapter 层**（设计推导）：不选——与 runner 落点/工程约定不一致，且 ADR 0002 未授权此归属。

## 6 · 决策拍定（用户 2026-07-20）

1. **决策 1** ✅ `GOLDEN` 硬表 → 加载器、引擎零算子 golden、fail-closed。
2. **决策 2** ✅ golden 落 `<ops_root>/<op>/`（用户 CWD、同 runner）、加载器读（**非** repo_adapter 层）。
3. **决策 3** ✅ 公式来源**分级**：仓自带/PR 参考 > 任务书期望 > `analytical_ref`（agent 自写、**末位 + 标可信度 + 人核**）；任务书方法不支持 → **fail-closed**。
4. **决策 4** ✅ 后端 **B**：CPU · 按算子 torch>numpy 定档记录、选定库必装（fail-closed）、不运行时偷换。**更新** [[Golden is fixed to torch on CPU for determinism]]。
5. **决策 5** ✅ oracle_source 补 loader 侧接线（承 Q9/Q7）。
6. **决策 6** ✅ golden 形态 = `golden.py` 动态 import + **执行边界文档化**（同 runner 信任级）。

**下一步**：本定稿走 bureau `capture → compile → review` 成 **ADR 0011**（并把决策 4 对 `golden-fixed-to-torch` 的更新走 compile 标 superseded、留 proposed），再**另开 PR 落代码**（§4 工作项）。
