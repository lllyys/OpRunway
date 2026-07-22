# ADR（草案）· Golden 去引擎化：精度 golden 从引擎硬表改为按算子加载

> **状态（2026-07-22 刷新）**：6 条决策已由用户 2026-07-20 逐条拍定（见 §6），其中 **决策 3 已被用户 2026-07-22 的裁定重写**（分级表 → **两档链**，见下）。
> **本 doc 是设计稿、不是 canon**：canon 侧 **ADR 0011（`canon/decisions/0011-golden-decoupling.md`）仍 `status: proposed`**，
> **未经 `bureau:review` 人门 promote** → 按 BUREAU trust tier，**不是事实**，载重前须复核；本轮的重写同样**须走一次
> `capture → compile → review`** 才进 canon（canon 页**绝不手改**）。
> **代码落地状态**：引擎核心已实现（`bc8acb7` 硬表→加载器、`ae94703` 来源契约扩六枚举、`0192e49` 来源契约批 1 = 词表 + tier 派生表 + 授权核验器，**批 1 纯新增未接线**），
> 均在分支 `feat/golden-out-of-engine`（**PR #8 未合**）；**批 2–7（接线 / 快照入库 / spec / 门 / agent 产出 / 报告与 canon 收口）全部未做**，清单见 `doc/oprunway-todo.md`「🔴 下一刀」。
> **前身**：`doc/oprunway-plugin-op-decoupling-design.md`（D1 / S1–S3 + 开放问题 #5）。**承接** runner 去引擎化（PR #7）。

## 0 · 边界先钉（免和别的东西串）

本 ADR **只管精度 golden 值**——拿来和 NPU 输出逐点比的「正确答案」，就是 `gen_cases.py:148` 那张 `GOLDEN` 硬表干的事
（⚠ §0/§1 引的 `gen_cases.py:148` / `:562` 是**重构前** main 上的行号；`feat/golden-out-of-engine` 分支上该表已删、改 `load_golden(op)`）：

- ✅ **在范围**：精度 golden **值**（每条用例的正确答案，从输入现算）。
- ❌ **不在范围**：精度**标准**（阈值 / oracle 口径 / MERE-MARE）——走 ascendoptest / opbase，**不动**；性能**基线**（msTuner / TBE 内置 / GPU 标杆）——那不叫 golden。
- 判精度 = `|NPU 输出 − golden 值|` 是否满足**精度标准**。本 ADR 只动前半的「golden 值怎么来」。

## 1 · Context（为什么要这刀）

`gen_cases.py:148` 把 4 个算子的 golden 写死成函数（`GOLDEN = {"IsClose": (…, golden_isclose), "Sign":…, "Equal":…, "Neg":…}`）；
`gen_cases.py:562` 第 5 个算子 `raise "unsupported op"`。即：**引擎内置了 4 个算子的「正确答案」、只认这 4 个**——
这是「插件肚子里带着 IsClose 的现成答案」的机制性根因（D1）。runner 那刀（PR #7）已让「怎么跑」成为输出；
**golden 这刀让「正确答案」也从引擎搬出去。**
⚠ **范围（2026-07-22 更正，别说过头）**：这一刀**只覆盖 elementwise 那条通路**（`gen_cases` 的 golden 加载器路径），
**不是「引擎零内置算子」**——仓里有两处已知例外、如实记账：① `catlass_adapter.py:152` 的 `golden_catlass_matmul` +
`:162` 的模块级 `GOLDEN_SOURCE`，且 `:186-190` 注释明写「**有意**不进 `gen_cases` 的 golden 加载器路径」
（matmul 的 (m,n,k) plan 与 elementwise 引擎结构不兼容）；② `gen_cases.py:34` 的
`_BF16_EXACT_OPS = frozenset({"Sign", "Neg"})`——按算子名硬编码的表，仍是引擎里的算子知识。

## 2 · Canon 现状（按 trust tier；载重前据此，冲突显式标）

| 出处 | tier | 与本 ADR 的关系 |
|---|---|---|
| [[Golden and precision standard come only from the task-doc-specified method]] | proposed（最高律令） | **约束决策 3**——⚠ **该页记的是写窄了的旧口径**（「只能来自任务书指定方法」、**漏了第二档**）；用户 2026-07-22 重定为**两档链**（见决策 3），canon 页**待走 capture→compile→review 更正**，在此之前以决策 3 为准 |
| [[Golden is fixed to torch on CPU for determinism]] | proposed | **被决策 4 更新**（「恒 torch 单后端」→「按算子 torch>numpy 定档」）；compile 时标 superseded、留 proposed 待 review |
| [[AscendOpTest precision thresholds]] | canonical | golden 由 `expect_func` 提供、输出 dtype 须与算子一致——加载器契约照此 |
| [[oracle_source is a hardcoded constant not a recorded fact]] | proposed（Q9 已止血） | **决策 5 承接**：oracle_source 已据实映射 + 门校；loader 侧补「按算子记录」 |
| [[ADR 0002 — Acceptance grounded in catlass and the spec]] | canonical | 只说「跨仓 golden 会变」、**未规定 golden 归属**——**决策 2 补这个洞** |
| [[OpRunway component breakdown]] | canonical | 「acc-common 核心不放 runner」——golden 同理（本 ADR 把「不放算子 golden」补上） |
| [[Primitive-to-case rule library]] | canonical | 据其理（真值口径一错则**所有精度用例作废**，golden 错亦然）——决策 3 的来源分级即为防此 |

## 3 · 决策（逐条点头）

### 决策 1 · `GOLDEN` 硬表 → 加载器（**elementwise 通路**零内置 golden）【核心】

> ⚠ **2026-07-22 收窄**：原标题写「引擎零算子 golden」是**过度声称**。只覆盖 elementwise 通路；
> `catlass_adapter.py:152/:162` 与 `gen_cases.py:34` `_BF16_EXACT_OPS` 是两处已知例外，见 §1。

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

### 决策 3 · 公式来源政策 —— **两档链**（用户 2026-07-22 重写，取代 07-20 的分级表）

> ⚠ **本节整节重写。** 07-20 的原稿把来源写成四级表「仓自带 / PR 参考 > 任务书期望 > `analytical_ref`」——
> 那张表把 **PR 的参考实现放在最高档**，与项目铁律「绝不信 PR」直接冲突（PR 是**被测物**，拿被测物的参考实现
> 当「正确答案」等于自证），已被用户 2026-07-22 推翻。**原表作废，以本节为准。**

#### 3.1 只有两档，按序

| 档 | 来源 | 成立条件 |
|---|---|---|
| **①** | **任务书指定的测试方法** | 须有**任务书原文授权**且引文可核：任务书**全文快照入库**，cite 严格写 `task_doc.snapshot.md:<行区间>` + 逐字 quote（R12） |
| **②** | **CPU 上的 torch / numpy 现成 API** | 第①档不存在时的回落档；**torch 优先、numpy 兜底**，且是**生成期选型、写死进 `golden.py`**，运行时不按「谁装了」偷换（R6，承决策 4） |

**没有第三档。** 任何来源要么落进这两档、要么 fail-closed 交人。

#### 3.2 硬规矩

- **R2 · PR 里的参考实现一律禁止作 golden 源。** 落地方式**不是写条禁令、而是可产值域里没有那个格子**：
  `precision_policy.PRODUCIBLE_ORACLE_SOURCES` 把 **`cpu_ref`**（其注释语义含「仓 / PR 的 CPU 参考」）与
  **`catlass_existing_ref`** 排除在可产集外——禁令会被绕过，**缺值只能 fail-closed**。
  ⚠ **canonical 的六枚举定义未动**（`oracle_source` 值域仍是六个）——改它须走 bureau review；批 1 收窄的是**可产集**。
- **仓自带（非 PR）的参考实现 = `impl_reference`，不构成 golden 授权**（**本轮保守默认、可推翻**）：
  它只说明「别人怎么实现」，不等于任务书授权了这个测试方法 → 走第②档、落 **tier 2/3**。
- **R4 · 任务书指定了、但本环境跑不起来 → fail-closed 问用户，不自动回落第②档**（`blocked_reason` 记原因）。
- **R5 · 人核门按「怎么算出来的」判**：第②档**现成 API 单调**（一个 API 直出）→ **不必**人核；
  **按公式自拼多步** → **必须**人核（自拼是最大的雷：写错了会「验收通过」却是错的，同
  [[Primitive-to-case rule library]]【canonical】之理——真值口径一错则所有精度用例作废）。
- **R9 · tier 是整数 1..4**：`derive_golden_tier` 九条规则按序首命中即返 + 末行穷举兜底，对任意输入恒有返回、无未定义态；
  **声称有任务书授权却核不实 → 直接 tier 4，不降级照跑**（假授权若只降档，R2/R4 等于没设）。
- **档位本轮 per-op**（**本轮保守默认、可推翻**）：同算子多 dtype 档位不一时**取最保守档**；per-dtype 扩展留待需要。
- **R8 · catlass 通路本轮 out-of-scope**：`catlass_adapter.py:152/:162` 的内置 matmul golden 不走本链，如实记账。

#### 3.3 落地状态与诚实边界

- **已落地（批 1，`0192e49`）**：受控词表 + `derive_golden_tier(g, authorization_verified)` + `verify_authorization(g, snapshot_path)`，
  在 `plugin/acc-common/precision_policy.py`——**纯新增、不接任何调用者**；a3 容器 516 测全绿。
- **未做**：接线到 `load_golden` / 快照入库 / spec 承载 / 门侧消费 / agent 产 `golden.py` / 报告与 canon 收口（批 2–7）。
- **诚实边界**：`verify_authorization` 只证「引文出自快照那几行」，**不证**「这句话该算 `oracle_method` 还是 `impl_reference`」——
  那一刀仍是 **agent 自报**，机器拦不住。**现成反例（2026-07-22 核实，措辞已在代码侧更正）**：`samples/golden/Sign/golden.py` 的
  `GOLDEN_PROVENANCE` 原写「任务书指定纯重写 → `torch.sign`(CPU)」，而 Sign 任务书**一字未提** torch/numpy/公式、
  只说「参考昇腾内置 Sign 的 TBE 实现 + 增加 int16」——那是 `impl_reference`、**不构成授权** → 实为**第②档回落**
  （golden 值本身没错、**措辞错**）。对照 IsClose/Equal：任务书**有**原文（「二进制比较改为逻辑值比较」），写「任务书指定」才准确。
- **候选与取舍**：(a) 两档链 + 值域禁产 PR 来源（本决策）；(b) 保留 07-20 的四级表——**否**（把被测物抬成最高档真值）；
  (c) 任务书没点名就一律 fail-closed、不给第②档——**否**（很多任务书不给现成参考，会全面卡死；第②档的现成 API 单调可复现、
  且带人核门兜自拼多步的风险）。

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

- **elementwise 通路** op-中立：第 5 个 elementwise 算子不再在 `gen_cases:562` 崩；「加算子 = spec + golden + runner 三件、皆用户侧」在该通路成立（对齐 CLAUDE.md 自述）。⚠ **不等于「引擎零内置算子」**——catlass 通路的内置 matmul golden 与 `_BF16_EXACT_OPS` 硬表仍在（见 §1 更正）。
- [[Golden is fixed to torch on CPU for determinism]] 被决策 4 更新 → compile 时据新决策标 superseded、留 proposed 走人门 review。
- **工作项（分批落，进度 2026-07-22）**：
  - ✅ `gen_cases` 表→加载器 + 落点契约（`<ops_root>/<op>/golden.py`）+ `GOLDEN_SOURCE`/`GOLDEN_PROVENANCE` 元数据 + 安全边界文档 + 单测改 fixture golden（`bc8acb7`）；来源契约扩六枚举（`ae94703`）；**来源契约批 1** = 词表 + tier 派生表 + 授权核验器（`0192e49`，**纯新增未接线**）。
  - ⏳ **未做（批 2–7）**：两档链接线到 `load_golden` · 任务书全文快照入库（R12）· spec 侧承载 · 门侧消费 tier · **acc-spec / acc-runner-dev 产 golden 的纪律**（两档链 + R4/R5 人核）· 报告与 canon 收口。清单见 `doc/oprunway-todo.md`「🔴 下一刀」。
- **不动**：精度标准（ascendoptest/opbase）、性能基线、runner（已去引擎化）。

## 5 · Alternatives（整体层面已否）

- **保留硬表**：否——不解决 op-中立，第 5 算子崩、「插件带答案」不变。
- **golden 归 repo_adapter 层**（设计推导）：不选——与 runner 落点/工程约定不一致，且 ADR 0002 未授权此归属。

## 6 · 决策拍定（用户 2026-07-20）

1. **决策 1** ✅ `GOLDEN` 硬表 → 加载器、**elementwise 通路**零内置 golden、fail-closed。
   （⚠ 2026-07-22 收窄：原写「引擎零算子 golden」是过度声称，两处例外见 §1。）
2. **决策 2** ✅ golden 落 `<ops_root>/<op>/`（用户 CWD、同 runner）、加载器读（**非** repo_adapter 层）。
3. ~~**决策 3** ✅ 公式来源**分级**：仓自带/PR 参考 > 任务书期望 > `analytical_ref`（agent 自写、**末位 + 标可信度 + 人核**）；任务书方法不支持 → **fail-closed**。~~
   ⚠ **已被用户 2026-07-22 推翻并重写**（见新版决策 3）：**两档链**——① 任务书指定的测试方法（须原文授权可核）→ ② CPU 上的 torch/numpy 现成 API；
   **PR 参考一律禁用**（落地方式 = 可产值域里没那个格子）、**仓自带参考 = `impl_reference` 不构成授权**（本轮保守默认）、
   指定了跑不起来 → **fail-closed 问用户不自动回落**、现成 API 单调免人核 / 自拼多步必人核、tier 整数 1..4、档位本轮 per-op 取最保守档。
4. **决策 4** ✅ 后端 **B**：CPU · 按算子 torch>numpy 定档记录、选定库必装（fail-closed）、不运行时偷换。**更新** [[Golden is fixed to torch on CPU for determinism]]。
5. **决策 5** ✅ oracle_source 补 loader 侧接线（承 Q9/Q7）。
6. **决策 6** ✅ golden 形态 = `golden.py` 动态 import + **执行边界文档化**（同 runner 信任级）。

**下一步（2026-07-22 刷新）**：
- canon 侧 **ADR 0011 已 capture→compile 成页、但仍 `proposed`**（连同决策 4 对 `golden-fixed-to-torch` 的 superseded 标注）——**待 `bureau:review` 人门 promote**；
  **本轮对决策 3 的重写、以及「最高律令写窄了」的更正，都还没进 canon**，须**再走一次 `capture → compile → review`**（canon 页绝不手改）。
- 代码侧：引擎核心 + 六枚举 + **来源契约批 1**（`0192e49`，未接线）已在分支 `feat/golden-out-of-engine`，**PR #8 未合**；**批 2–7 未做**（清单见 `doc/oprunway-todo.md`「🔴 下一刀」）。
