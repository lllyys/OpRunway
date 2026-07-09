# OpRunway 各 todo 实施 plan（每份经 codex exec 审计并修订）

> 生成日 2026-07-09。流程：每条 todo 起草 grounding 好的实施 plan → codex exec 审 5 维（一致/完整/可行/歧义/风险）→ 据 findings 修订。
> **状态：待用户过目 + 点头才实施（CLAUDE.md #1）。** 每条的 codex 结论 / 可实施性 / 残余风险 / 待拍板岔口见各节头部。

## 速览表

| todo | codex 总判 | 可实施 |
|---|---|---|
| T1-drift | needs-revision | no（仅卡在用户决策，技术上本地即可一次做完）。本地无任何技术阻塞——四处纯散文修复，不碰真机/VP |
| T2-P1-orchestration | major-gaps | no（严格说：待 CLAUDE.md #1 用户对方案点头才动手）。技术上本地部分**立即可做**： |
| T3-P2-atomize-distribute | major-gaps | no — 卡在开工决策门。本地机械产出（4 SKILL.md+refs 草稿、workflows 材 |
| T4-P3-catlass-adapter | major-gaps | no（分两层）。① 流程层：本 todo 处 init 阶段，按 CLAUDE.md #1「先抛方案 |
| T5-precision-dual-standard | major-gaps | 基本可以，但需先冻结两个门前决策：① 误差复算落点（推荐 repo_adapter 采集层复算 /  |
| T6-perf-smallshape | needs-revision | no —— 卡在 CLAUDE.md#1 的 step1 用户方案确认，尤其决策项 D1–D5（PA |
| T7-dtype-attr-coverage | needs-revision | no（本地可动手、但先过方案确认门）——Track A(gen_cases/repo_adapter |
| T8-gpu-benchmark-task3 | needs-revision | no（暂不能直接开工）。技术上 consumer 侧全部逻辑（契约/解析器/两 BLOCKED 态/ |
| T9-publish-form | major-gaps | 部分 yes（可立即开工到 STOP 点，之后卡用户拍板）。现在即可做：完成 schema 侦查（本 |
| T10-equal-canon-correction | needs-revision | yes（agent 侧本地可立刻动手）——Step 0 通读、Step 1 只读核验（bureau: |
| T11-external-publish | needs-revision | no（部分可立即动手、关键外发被阻）。可立即做：T11b 本地台账清理（changes-brief  |

---

## T1-drift — 修 4 处一致性漂移（AGENTS↔agent 镜像补硬门 / 两处「验证-才-信硬门」stale 措辞 / acc-casegen 孤儿补 SKILL.md）

**codex 总判**：codex_ran_ok=true（codex_verdict=needs-revision，codex 真跑成、本 plan 已经外审）。10 条 issue（2 high / 6 medium / 2 low）全部逐条核实并处理：2 条 high 均采纳并已在终版闭环——#1（agent:30 第二处 stale 措辞）经 `rg` 实证确认属真缺陷、已补入修复范围；#2（run_workflow 无阶段间阻断）经 Read run_workflow.py 实证、已收窄镜像措辞避免过度承诺。**无 high 未解**。8 条 medium/low 中，7 条全采纳，1 条（#7）部分采纳（保 canonical 要求的路径 + 加描述护栏，否决『移入 draft』）。终版已把验收从纯 grep 升级为『语义 diff + 全仓 stale 扫』，并纠正原 plan 的路径歧义（plugin/AGENTS.md、plugin/.claude-plugin/plugin.json）与遗漏的第二处 stale。

**可实施性**：no（仅卡在用户决策，技术上本地即可一次做完）。本地无任何技术阻塞——四处纯散文修复，不碰真机/VPN/GPU/对外发布，codex 门与 check_manifest_sync/rg 均本地可跑。卡点是流程性的两项用户决策：① 最高规则#1 要求本 plan 经用户点头才落地；② (a) 权威方向依赖 proposed 页 `cross-cli-unified-form-agents-md.md`，需用户确认『以 AGENTS.md 为源、补 agent 镜像』这一方向（而非反删源）。(c) 的 C1/C2 已在终版锁定为 C1、不再需用户在此二选一。用户点头 + 确认 (a) 方向后，step2–9 可本地一次性跑完。

**残余风险**：1. **(a) 方向锚在 proposed 页**：`cross-cli-unified-form-agents-md.md`【proposed·未 settle】——『AGENTS.md 为源、agent 为镜像』方向未经 human 升 canonical；虽与 AGENTS.md 自述 + 架构 + plugin.json 派生一致、风险低，仍需用户拍板方向（open_decision 1）。若日后方向反转（改以 agent 为源），本次镜像方向需回改。
2. **镜像手工维护、无机器门**：check_manifest_sync 只校验『声明→存在』，**不校验 AGENTS.md 与 agent 镜像的 body 文本一致**——本次靠人工语义 diff 对齐，未来两文件仍可能再漂移（属已知机制缺口，非本 todo 修复项；如要根治需另开『镜像 body 一致性校验』代码 todo）。
3. **codex 散文门非零风险**：`codex exec` 依赖 CLI/账号/模型服务/超时——若失败走人工 fallback，则本次交付为『未经外审』，散文质量仅人工自检背书。
4. **acc-casegen/SKILL.md 的『勿自动触发』靠描述软约束**：不同运行时的 skill 发现/召唤策略不一，描述级护栏不是硬隔离；极端情况下仍可能被某 runtime 按描述召唤（已尽量用 description 明示降风险，但非 100% 阻断）。
5. **verified 页复核有效期**：`machine-verifiable-acceptance-gate.md`【verified】已与 AGENTS.md §硬门 逐字核对一致，但 verified≠canonical；若 AGENTS.md 硬门文本后续变更，需重核。

**据 codex 修订**：逐条处理 codex 10 条 issue（均先 Read 原文核实、无一盲从/盲拒）：

【#1 high · consistency —— 采纳，且是真缺陷】核实属实：`plugin/agents/op-acceptance.md:30` 确有「验证-才-信**硬门**」，原 plan 只改了 acc-runner:29、漏掉 agent:30。已把 (b) 从「改一处」扩为「改两处」，并加全仓 `rg '验证-才-信'` 收尾扫（step8/acceptance）。同时厘清：全仓仅这两处是错误断言，`AGENTS.md:32`（无「硬门」）/`validate_acceptance_state.py:3`/`runner-skeleton.md` 的共现均合法、不动。

【#2 high · feasibility —— 采纳（收窄措辞），但不改 AGENTS.md 源】核实 `run_workflow.py`：确是 Task1→2→3 全跑完（32-52 行无条件）后末尾统一校门（55-68 行），**无阶段间实时阻断**。但 `AGENTS.md:21-26` 本就把「不推进下一 Task」（流程纪律）与「run_workflow 内嵌门→BLOCKED」（代码行为）分开写，源无错。故修订：镜像时严格照此区分，步④/⑥指针**只把『总体 BLOCKED / 不出 pass 裁决』归给 run_workflow 内嵌门**、把『不推进下一 Task / 停在当前阶段』标为 **agent 编排纪律**，并在硬门节显式注『run_workflow 是批量驱动、非阶段间实时阻断』。这样消除过度承诺，又不必动源（轻度反驳 codex 的『改措辞』默认落点应在源——落点在镜像即可）。

【#3 medium · consistency —— 采纳】C1/C2 既定稿又留悬确属内部不自洽。已锁 C1（canonical 页 line18 硬性要求 SKILL.md + todo 把缺 SKILL.md 定为缺陷），把 open_decisions 里 C1/C2 岔口降为『已决记录 + C2 已否决理由』，files/steps/acceptance 全线只走 C1。

【#4 medium · completeness —— 采纳】changes-brief 是新生成 md、规则#5 要求过审。已把 `doc/oprunway-changes-brief.md` 纳入同一次 codex 散文门（门 1 从四份→五份），并调 step 顺序：先写 brief（step6）再统一过审（step7）。

【#5 medium · completeness —— 采纳】grep 抓不到 body 级语义漂移。acceptance 已加：(a) 新增硬门节与 AGENTS.md:21-26 的文本 diff/语义逐点核对（非仅 grep）+ 显式核『无 run_workflow 阶段间阻断过度承诺』；(b) 全仓 `rg '验证-才-信'` stale-phrase 扫作验收项。

【#6 medium · feasibility —— 采纳】核实项目根无 AGENTS.md、源在 `plugin/AGENTS.md`、manifest 在 `plugin/.claude-plugin/plugin.json`。全 plan 路径已改真实全路径，消歧义。

【#7 medium · risk —— 部分采纳（保路径、加描述护栏；否决『移入 draft 区』）】接受『新 SKILL.md 可能被发现即半激活』的风险，已在 frontmatter description 加『参考草案·未接入 live 流·勿自动触发』框定。但**否决**『移入 draft 文档区』的替代方案——canonical 页 line18 硬性要求承载体就在 `acc-casegen/SKILL.md` 该路径，移走会违背 canonical；改用描述级护栏 + 顶部诚实 banner 达成同样目的。

【#8 medium · ambiguity —— 采纳】已把 acc-casegen/SKILL.md 收窄为『展开推理规则说明』，显式声明**不落盘 caseset.json、不调用/不替代 gen_cases.py**（live 用例生成走 gen_cases.py），边界清晰。

【#9 low · feasibility —— 采纳】核实 CLAUDE.md 确说 nlpm 非本门、且环境未必有 nlpm。已删可选 nlpm:check，替换为具体 `rg`/`find` 悬挂+孤儿检查（step8/gate3）。

【#10 low · risk —— 采纳】已把『codex 散文门本地可跑、无阻塞』改为写明 `codex exec` 命令 + 失败/超时的人工 fallback（并要求 fallback 时显式标『未经外审』、不假装审过）。

另主动补正一处 plan 内 grounding 强度：新增『已实测确认 check_manifest_sync 只校验声明→存在，故新建未声明的 acc-casegen/SKILL.md 不破坏 SYNCED』——支撑 acceptance 里『check_manifest_sync 仍 SYNCED』的断言不落空。

### 终版 plan

## todo: T1-drift — 修文档一致性漂移（纯本地散文）

### goal
消除文档一致性漂移，四处（原三处 + codex 追加的第 4 处）：
1. `plugin/agents/op-acceptance.md` 缺 `plugin/AGENTS.md` 已有的「## 硬门（最高规则）」节 → 镜像重新对齐（补镜像、不动源）。
2. **两处** stale 措辞「验证-才-信（是）硬门」→ 改回与全文一致的「纪律（sidecar 门待补）」：`plugin/skills/acc-runner/SKILL.md:29` **和** `plugin/agents/op-acceptance.md:30`（后者是 codex 追加发现，原 plan 漏掉）。
3. `plugin/skills/acc-casegen/` 是只有 `references/` 无 `SKILL.md` 的孤儿 → 补一份诚实的 `SKILL.md`（对齐 canonical 页），并修 `references/rule-catalog.md` 的悬挂引用为可解析相对链接。
全部纯本地散文修复，改后过 codex 散文门（**含 changes-brief**）并记 changes-brief。

### approach（逐处定「怎么改 / 以哪份为准」，均对齐已 Read 的 canon，不重推已 settle 的东西）

**(a) 镜像漂移 —— 以 `plugin/AGENTS.md` 为准（补镜像、不动源）。**
依据 `cross-cli-unified-form-agents-md.md`【proposed·未 settle】：`plugin/AGENTS.md` 是跨 CLI 单一事实源，`plugin/CLAUDE.md` / `plugin/.claude-plugin/plugin.json` 从它派生；AGENTS.md 自述 agents/op-acceptance.md 是「同源镜像」。方向 = 给 `agents/op-acceptance.md` 加「## 硬门（最高规则）」节 + 在步④/⑥点到门，措辞镜像 `plugin/AGENTS.md:21-26`。硬门内容 grounded：三级门 = `validate_acceptance_state.py` 见 `machine-verifiable-acceptance-gate.md`【verified·已与 AGENTS.md §硬门 逐字核对一致】；「门只管证据完整、不重判 pass-fail」照 `gate-checks-evidence-integrity-not-verdict.md`【proposed·未 settle】写。
**⚠ 关键（codex #2 吸收）**：`plugin/AGENTS.md:21-26` 本身已谨慎区分「**编排/流程纪律**：门 FAILED → 不推进下一 Task」与「**代码行为**：run_workflow.py 内嵌门→总体 BLOCKED」。已核 `run_workflow.py`：它 Task1→2→3 **全跑完后统一校门**（32-52 行无条件跑三段、55-68 行末尾一次性校门），**无阶段间实时阻断**。故镜像时严格照 AGENTS.md 的既有区分写，且在步④/⑥指针里**只把「总体 BLOCKED / 不出 pass 裁决」归给 run_workflow 的内嵌门**、把「不推进下一 Task / 停在当前阶段」明确标为 **agent 编排纪律**（非代码强制的阶段间墙）。这样不新造 AGENTS.md 里不存在的代码承诺，也不必改源。

**(b) 两处 stale「验证-才-信硬门」—— 以「现状真相 = 纪律、runner 自验证 sidecar 门待补」为准。**
全仓 `rg '验证-才-信'` 只有两处错误断言：`acc-runner/SKILL.md:29`「验证-才-信是硬门」、`agents/op-acceptance.md:30`「…选构建路径 + 验证-才-信**硬门**」。二者都改成「必守纪律（当前非代码硬门、sidecar 待补），不可跳过」，与 `acc-runner/SKILL.md:10/24` + `runner-skeleton.md:6/56` + `README.md:31` 一致。
**⚠ 两个门别混（写进改稿避免误纠）**：canon `machine-verifiable-acceptance-gate.md` 说「验证-才-信从纪律变代码硬门」指的是**验收状态门 `validate_acceptance_state.py`（证据完整性门）**——这就是 (a) 要镜像进 agent 的「## 硬门」节，是**真代码硬门**；而 (b) 改的是 **acc-runner 的 runner 自验证 sidecar 门**，后者仍待补。`validate_acceptance_state.py:3` 与 `runner-skeleton.md` 里「验证-才-信」与「硬门」共现是合法的（分别讲各自的门），不动它们。

**(c) 孤儿 —— 以 canonical 页 `primitive-to-case-rule-library.md`【canonical·当事实】为准（锁定 C1）。**
该页 line18 明写 acc-casegen 展开逻辑「实现为 acc-casegen 的 SKILL.md」、line12 目录「实现为 `references/rule-catalog.md`」。故补 `plugin/skills/acc-casegen/SKILL.md`，并把 `references/rule-catalog.md:5` 悬挂引用改成可解析相对链接 `../SKILL.md`。按诚实收窄（acc-runner 先例 + todo P2 定位 + codex #7/#8）：
- SKILL.md 只描述**展开推理规则**（design-level），**显式声明不落盘 caseset.json、不调用/不替代确定性 `gen_cases.py`**——live 用例生成走 `gen_cases.py`，本 skill 是 P2 规划的原子 skill、尚未接入 op-acceptance live 流；
- frontmatter `description` 明确标「参考草案·未接入 live 流·勿自动触发；仅在需理解/扩展用例展开规则时阅读」，降低被运行时按描述自动召唤的风险（codex #7）；
- **不**加入 `plugin/AGENTS.md` 的 `skills:` 已接入清单（保持 P2 诚实定位；check_manifest_sync 只校验「声明→存在」，不列不影响 SYNCED）。

### canon 依据（grounding；按 trust tier）
- `canon/architecture/primitive-to-case-rule-library.md`【canonical = 事实】— (c) 唯一 canonical 依据：acc-casegen 须有 SKILL.md 承载展开逻辑、rule-catalog.md 是目录。可直接当事实。
- `canon/architecture/cross-cli-unified-form-agents-md.md`【proposed·未 settle】— (a) 权威方向（`plugin/AGENTS.md` 单一事实源、agent 从它派生）。proposed → 在 open_decisions 挑明方向本身未 settle；虽与 AGENTS.md 自述 + plugin 架构一致、风险低。
- `canon/architecture/machine-verifiable-acceptance-gate.md`【verified·载重需复核】— (a) 硬门文字来源（validate_acceptance_state.py 三级/BLOCKED/非零退出）。已与 AGENTS.md §硬门 逐字核对一致，复核通过。
- `canon/decisions/gate-checks-evidence-integrity-not-verdict.md`【proposed·未 settle】— (a) 措辞边界：门只校验证据可信+完整、不重判精度 pass-fail。据此写、防夸大。
- 非 canon 佐证：`plugin/skills/acc-runner/references/runner-skeleton.md` §4 + `plugin/README.md:31` + 已 Read 的 `run_workflow.py`（坐实 (b) 现状=纪律 + (a) run_workflow 是「全跑完再统一校门」的批量驱动）。
- **本 todo 不写/不改任何 canon**（补 SKILL.md 反让 canonical 页更贴现实），故**不触发 bureau 门**；canon 仅用于 grounding。

### files（全部 repo 相对全路径，消除歧义 — codex #6）
| 路径 | 动作 | 目的 |
|---|---|---|
| `plugin/agents/op-acceptance.md` | edit | (a) 在「## 面向用户」后、「## 流程」前插「## 硬门（最高规则）」节，镜像 `plugin/AGENTS.md:21-26`（validate_acceptance_state.py 三级门→FAILED→BLOCKED），步④补「run_workflow 内嵌验收硬门→门未过总体 BLOCKED」、步⑥补「门 FAILED→BLOCKED 不出裁决」；**同时**修 (b) line30「验证-才-信硬门」→「验证-才-信纪律（当前非代码硬门、sidecar 待补）」。只补/改镜像、不动 AGENTS.md 源。 |
| `plugin/skills/acc-runner/SKILL.md` | edit | (b) 改 line29「验证-才-信是硬门，不可跳过」→「验证-才-信是必守纪律（当前非代码硬门、sidecar 待补），不可跳过」，与 line10/24 及 runner-skeleton §4 一致；不引入 validate_acceptance_state.py（那是另一个门）。 |
| `plugin/skills/acc-casegen/SKILL.md` | create | (c) frontmatter(name: acc-casegen + description 含「参考草案·未接入 live 流·勿自动触发」框定) + 展开算法 5 步 + 诚实边界（P2 规划原子 skill·未接入 live·**不落盘·不替代 gen_cases.py**·未列入 AGENTS.md skills）+ progressive disclosure 指向 `references/rule-catalog.md`；对齐 canonical 页。 |
| `plugin/skills/acc-casegen/references/rule-catalog.md` | edit | (c) 修 line5 悬挂引用为可解析相对链接 `[../SKILL.md](../SKILL.md)`（现 SKILL.md 已建、链接可解析）；line3 种子/critique 链接不在本 todo 范围（无新悬挂则不动）。 |
| `doc/oprunway-changes-brief.md` | edit | 规则#4：2026-07-09 节倒序追加一两句大白话，记四处漂移已修 + 以哪份为准。**本文件也纳入 codex 散文门**（codex #4）。 |

### steps
1. **确认 grounding（已完成）**：已逐份 Read 六份 grounding + rule-catalog.md + runner-skeleton §4 + check_manifest_sync.py + run_workflow.py + plugin.json 位置，按 trust tier 定性；四处漂移事实与「以哪份为准」已锁定。**已实测确认**：run_workflow.py 是「三段全跑后统一校门」批量驱动（无阶段间阻断）；check_manifest_sync 只校验「声明→存在」；全仓仅 agent:30 与 acc-runner:29 两处 stale「验证-才-信硬门」。
2. **起草 (a) `plugin/agents/op-acceptance.md` 硬门节**：插「## 硬门（最高规则）」，镜像 `plugin/AGENTS.md:21-26`——出任何 pass 裁决前必过 `acc-common/validate_acceptance_state.py`（三级 `--stage task1|task2|task3`，读落盘 evidence.json 独立复核，防跑子集/放宽阈值/混 e2e）；判定脑子在 validator（ADR 0007），门只管证据可信完整。**措辞守 codex #2**：run_workflow.py 内嵌该门、**门未过→总体 BLOCKED、不出 pass 裁决**（注：run_workflow 是批量驱动、非阶段间实时阻断）；「门 FAILED → 停在当前阶段/不推进下一 Task」标为 **agent 编排纪律**。步④尾补「run_workflow 内嵌验收硬门→门未过总体 BLOCKED」，步⑥补「门 FAILED→BLOCKED 不出裁决」。
3. **改 (b) 两处 stale 措辞**：`plugin/agents/op-acceptance.md:30` 的「+ 验证-才-信硬门」与 `plugin/skills/acc-runner/SKILL.md:29` 的「验证-才-信是硬门」，均改「验证-才-信是必守纪律（当前非代码硬门、sidecar 待补），不可跳过」。保持二文件其余不动。**不误采 acceptance 门（validate_acceptance_state.py）去反说它已是硬门**。
4. **起草 (c) `plugin/skills/acc-casegen/SKILL.md`（create）**：frontmatter `name: acc-casegen` + `description`（『把任务书算子拆原语→按 rule-catalog 拉必测 case 模式→实例化去重→出覆盖矩阵；**参考草案，尚未接入 live 流、勿自动触发**，仅在需理解/扩展用例展开规则时阅读』）。正文含：输入(spec/任务书公式)/输出（**说明性覆盖矩阵推理，不落盘 caseset.json**）、展开算法 5 步（拆原语→兜底 guard→查库叠三轴→实例化→去重+元规则+覆盖报告，摘自 rule-catalog §怎么用）、**诚实边界**一节（『本 skill 是落地设计 P2 规划的原子 skill；live 编排的用例生成走确定性 `gen_cases.py`——**本 skill 不落盘、不调用、不替代 gen_cases.py**；展开逻辑尚未接入 op-acceptance agent 流；故未列入 AGENTS.md skills 已接入清单』）、progressive disclosure『详规见 `references/rule-catalog.md`』、约束（全程中文/阈值口径不在此在 policy/no silent 漏项）。对齐 canonical 页对「展开逻辑=SKILL.md、目录=rule-catalog.md」的划分。
5. **修 (c) rule-catalog.md 悬挂引用**：line5『…的展开逻辑在 acc-casegen 的 SKILL.md』→『…的展开逻辑见 [`../SKILL.md`](../SKILL.md)』（相对链接目标 = `skills/acc-casegen/SKILL.md`，已建、可解析）。核对 SKILL.md ↔ rule-catalog 互指一致（SKILL.md 摘算法要点、rule-catalog 存目录明细）。line3 不在范围、无新悬挂则不动。
6. **写 (记) changes-brief（先写，再统一过审 — codex #4）**：在 `doc/oprunway-changes-brief.md` 2026-07-09 节顶部倒序加一条，记四处漂移已修 + 以哪份为准。**先写此条，好让它一并进 step7 的散文门**。
7. **过 codex 散文门（gate·含 changes-brief）**：对**五份**改/建散文文件（agent / acc-runner SKILL / acc-casegen SKILL / rule-catalog / changes-brief）跑 `codex exec` 定制散文审（**非 cc-suite:audit-fix**——零代码改动），重点查：(a) 镜像是否真与 `plugin/AGENTS.md:21-26` 语义一致无新漂移、且未把 run_workflow 说成阶段间阻断；(b) 两处 stale 是否都清、且未把两个门混为一谈；(c) 是否有新过度声称（把 P2 未接入 skill 说成已在流水线）。按审出问题修产物，复述『发现了什么/改了什么/还有什么风险』。**命令与 fallback（codex #10）**：`codex exec` 依赖 CLI/账号/模型服务，非零风险——若 codex 不可用/超时，改人工逐份自检并在交付里显式标『codex 未成功、本次未经外审』，**不假装审过**。
8. **机器回归 + 悬挂/孤儿 sanity（codex #9：不用 nlpm）**：跑 `python3 plugin/acc-common/check_manifest_sync.py` 期望 `STATUS: SYNCED`（新建 acc-casegen 未列入 AGENTS.md skills、只校验「声明→存在」故不受影响）；用 `rg`/`find` 做悬挂+孤儿核查——`ls plugin/skills/*/SKILL.md` 应三目录各有 SKILL.md（孤儿消）、`rg '验证-才-信' plugin` 复核每处与「硬门」共现的行要么是 acceptance 门文件 `validate_acceptance_state.py`、要么是负式（『非代码硬门』/『硬门待补』），无一行断言 runner 自验证「验证-才-信」是硬门。`validate_acceptance_state.py` 本 todo **N/A**（不产验收裁决）。
9. **落 changes-brief 确认（gate #4）**：step6 已写；step7 已随散文门过审——此步确认 brief 条目最终措辞与实际改动一致。

### gates
1. **codex 散文门（必过）**：**五份**散文文件（agent / acc-runner SKILL / acc-casegen SKILL / rule-catalog / **changes-brief**）走 `codex exec` 定制散文审→修产物→复述结论。**不走 cc-suite:audit-fix**（那是代码门，本 todo 零代码改动）。命令 `codex exec`；失败/超时 → 人工自检 + 显式标『未经外审』，不当零风险步骤（codex #10）。
2. **changes-brief（规则#4，必做）**：`doc/oprunway-changes-brief.md` 倒序追加一两句，且纳入门 1。
3. **机器 sanity（非硬门）**：`check_manifest_sync.py` 期望 `STATUS: SYNCED`；`rg`/`find` 确认孤儿已消、无 runner-自验证「验证-才-信硬门」残留（**替代 nlpm:check**——CLAUDE.md 明说 nlpm 非本门 · codex #9）。
4. **bureau 门：N/A**——不写/不改 canon（补 SKILL.md 让 canonical 页更贴现实，无需 bureau 写入）。
5. **validate_acceptance_state.py：N/A**——本 todo 不产验收裁决。
6. 落地前受最高规则#1 约束：本 plan 属『先抛方案』，需用户点头才动手（(c) 的 C1 已锁、(a) 方向依赖 proposed 页——见 open_decisions）。

### blocked / deps
**现在本地全部能做完**——四处均纯本地散文一致性修复，无外部依赖：不碰真机 NPU、不需 VPN、不涉 GPU 标杆、不做任何对外发布。唯二『前置』是流程性、非技术阻塞：① 最高规则#1 要求本 plan 经用户点头才落地；② (a) 权威方向的 canon 依据 `cross-cli-unified-form-agents-md.md` 是 proposed（未 settle），请用户确认方向（而非反过来删 AGENTS.md 硬门去将就 agent）。codex 散文门 / check_manifest_sync / rg 均本地可跑（codex exec 非零风险，有人工 fallback）。

### acceptance（含语义级核对，不只 grep — codex #5）
- **(a) 镜像对齐**：`grep -nE 'validate_acceptance_state|BLOCKED|硬门' plugin/agents/op-acceptance.md` 有命中；**且**新增「## 硬门」节与 `plugin/AGENTS.md:21-26` 逐点语义核对一致（三级门 / STATUS:FAILED→BLOCKED / 不出裁决 / 判定在 validator / 门只管证据完整）——做一次两节的文本 diff/语义比对，非仅 grep；**并确认无 run_workflow「阶段间实时阻断」这种代码里不存在的过度承诺**（run_workflow 只归得「统一校门→BLOCKED」）。
- **(b) 两处 stale 清零**：`rg '验证-才-信' plugin` 全仓复核——`plugin/agents/op-acceptance.md` 与 `plugin/skills/acc-runner/SKILL.md` 不再出现断言式「验证-才-信（是）硬门」；每处「验证-才-信」与「硬门」共现的行，要么属 acceptance 门文件 `validate_acceptance_state.py`（另一个门）、要么为负式（『非代码硬门』/『硬门待补』）。全文无自相矛盾。
- **(c) 孤儿消除 + 诚实边界**：`plugin/skills/acc-casegen/SKILL.md` 存在且 frontmatter 含合法 `name`/`description`；`ls plugin/skills/*/SKILL.md` 三个 skill 目录各有 SKILL.md（无孤儿）；`rule-catalog.md:5` 的 `../SKILL.md` 相对链接目标存在（可解析）；SKILL.md 含『P2/未接入 live 流/不落盘/不替代 gen_cases.py』诚实边界，description 带『勿自动触发』框定，且**未**被列入 `plugin/AGENTS.md` skills。
- **全局**：五份散文文件过 codex 散文门（问题已修，或显式标『未经外审』）；`check_manifest_sync.py` 仍 `STATUS: SYNCED`；`doc/oprunway-changes-brief.md` 已追加对应条并随门过审。

### open_decisions
1. **(a) 权威方向依赖 proposed 页（保留）**：『以 `plugin/AGENTS.md` 为准、补齐 agent 镜像』的 canon 依据 `cross-cli-unified-form-agents-md.md` 是 proposed（未 settle）；虽与 AGENTS.md 自述 + plugin 架构一致、风险低，仍请用户确认方向（而非反删 AGENTS.md 硬门去将就 agent）。
2. **(c) 实现路径已锁 C1（原 C1/C2 岔口降为已决记录 — codex #3）**：goal/files/acceptance 已定稿走 **C1 = 现在补一份诚实的 `acc-casegen/SKILL.md`**，依据 canonical 页 line18 明确要求 SKILL.md 承载展开逻辑、且 todo 把缺 SKILL.md 定为待修缺陷。**C2（暂不建 SKILL.md、只改悬挂引用不指向 SKILL.md）已否决**——那样目录仍无 SKILL.md、孤儿未真正解掉，且违背 canonical 页。此项不再是未决岔口，仅作决策记录。
3. **(c) 是否接入 live 流 / 列入 AGENTS.md skills（保持不接入）**：建议**不**接入、**不**列入（守 P2『未接入』诚实定位，只补文档不改编排）。若用户想顺带把 acc-casegen 接进 agent 流水线，则超出本 drift-fix 范围、另开 todo。
4. 注：todo 提示里的 T3 canon 分解冲突 / T9 发布形态 / T11 外发授权均属**其它 todo**，本 T1-drift 不涉外发/发布/授权决策。

---

## T2-P1-orchestration — P1 编排升级：薄 primary orchestrator + 3 subagent + acceptance-workflow skill（据 codex 审计修订终版）

**codex 总判**：codex 真跑成（codex_ran_ok=true），总判 major-gaps，16 条（4 high / 11 medium / 1 low）。经本地核对源码与 canon，4 条 high 全部属实（run_workflow 一次性串三 Task、fetch_source 无 issue 号字段、工件即状态未覆盖 CP0、CP0 无落盘 schema），已在终版逐条解决——无 high 未解。11 medium 全采纳（含 check_manifest 非真三方、plugin.json 双写、agents 语义混用、primary skills 自相矛盾、acc-runner scope、Task3 blocked 缺、绝不宣告 pass/fail 字面冲突、mode 撞名、Open Decisions 与 Acceptance 先后矛盾等），1 low 采纳。无驳回条目；对 #3（issue 号）与 #12（lint）选择了保守分支（NL+用户确认兜底 / 推荐加轻量 lint 但列 open），已在 revisions_applied 说明理由。终版 plan 已消除自相矛盾、CP 映射对齐脚本真实行为。

**可实施性**：no（严格说：待 CLAUDE.md #1 用户对方案点头才动手）。技术上本地部分**立即可做**：5 份 .md 编排制品 + AGENTS.md/plugin.json 手工同步 + command 轻改 +（可选）check_agent_frontmatter.py + 改动简表，并可本地完整验证 check_manifest_sync(SYNCED)/测试套件/run_workflow mock 端到端/fixture 演练 CP-A。卡点两处：① 落地前需用户批方案（#1 门）+ open decision (a)(b) 定夺；② 真机 CP-C/CP-D 端到端卡 NPU/VPN，只能设计+mock。

**残余风险**：1) 真机端到端未验证：CP-C verify_runner 真机编译、CP-D run_npu 真机跑测 + FAIL 独立复现解耦，只能设计+mock，待开 NPU/VPN（ascend-a5 真 950 / a3 A2A3）——这是设计固有边界，非本次能消。2) CP-A 对应校验非全自动：issue 号靠 NL 读 + 用户确认（fetch_source 不抽），target_dir 可机器比对但下游判定仍需用户拍板；若坚持机器化需扩 Layer1（open c）。3) Task3 GPU external 对比层当前未接入 pipeline，移植类算子（需 GPU 基线）会走 BLOCKED_WAIT_GPU_BENCHMARK，本 todo 只写编排文本、不接数据。4) 结构合规机器门（check_agent_frontmatter）尚是 open decision，若不加则 subagent 单轮/禁循环/不判定合规回落到散文门，较脆。5) 三个 proposed canon 页（correspondence/conversational/cross-cli）仍未 settle，本 todo 载重它们时按 proposed 存疑处理；升 canonical 需 bureau 人门。

**据 codex 修订**：逐条处理 16 条 codex findings（4 high 全解，无一驳回，部分选定分支并说明）：

【HIGH #1 feasibility·CP1/2/3 分段冲突】已核 run_workflow.py：一次性串 Task1→2→3，new_example 会在同一次调用里跑完精度+性能+三级门并落 acceptance.json。→ 采纳：把三级 validate_acceptance_state 门明确为 run_workflow **内部**、非 orchestrator 分阶段调度；CP 重定义为对话暂停点+工件门，真机执行合并为**一个原子 CP-D**（Task2+Task3+三级门一次成）。CP-B 用 --mode mock 自检覆盖 task1 门。

【HIGH #2 consistency·只调度 vs 亲自跑脚本】采纳：厘清 primary **可跑「无 NL 生成、无判定」的确定性脚本**（fetch_source/run_workflow mock/gate/manifest，与 conversational-agent-sole-delivery-form 一致），**不做 NL 生成工件**（spec/runner 派 subagent），**不自行判 pass/fail**。「禁自行生成工件」原意=禁 NL 生成 durable 工件，已改成此精确措辞。

【HIGH #3 feasibility·CP0 issue 号不存在】已核 fetch_source pr_facts.json 无 issue 号字段（仅 op/target_dir/changed_files/title）。→ 采纳，选分支 B：CP-A 降级为「target_dir 机器比对（已有）+ issue/追踪号 NL 读 task_doc/PR title + 用户确认」的半自动流程，落机读 correspondence.json；驳回把「扩 fetch_source 抽 issue 号」作默认（越出纯 Layer2），列为 open decision (c)。

【HIGH #4 completeness·CP0 无落盘 schema】采纳：定义 correspondence.json schema + 状态枚举 {confirmed, mismatch, empty_task, needs_user_confirmation}，断点续跑读此工件。

【MED #5 consistency·空任务 程序结论 vs 用户确认矛盾】采纳：用状态枚举消歧——mismatch/empty_task 自动停出程序结论，needs_user_confirmation 必问用户；写清哪些自动停/哪些问用户。

【MED #6 completeness·Task3 GPU/timing/BLOCKED 缺】已核 task3-state-machine（canonical，有 BLOCKED_WAIT_GPU_BENCHMARK/BLOCKED_INCOMPARABLE_TIMING_SCOPE）+ perf-baseline-by-reference-source。→ 采纳：acceptance-workflow 补基线来源（按任务书参考源，spec.perf.baseline 驱动）+ blocked 状态路由 + GPU external 对比层当前未接入的诚实说明。

【MED #7 feasibility·check_manifest 非真三方】已核脚本：agents 三方（plugin.json↔AGENTS.md↔文件），skills 只文件存在（无 plugin.json skills 数组）。→ 采纳：Acceptance/正文改为按脚本实际能力表述，不 overclaim skills 三方；扩脚本列为 open (d)。

【MED #8 risk·plugin.json「派生」实为双写】采纳：改诚实表述「手工同步 + 机器校验（check_manifest 漂移门）」，真正生成器是 P2 init.sh。

【MED #9 ambiguity·agents 语义混用】采纳：区分 plugin_agents（AGENTS.md/plugin.json = 全部 4 个含 primary 自身）vs child_agents（op-acceptance frontmatter = 3 subagent 不含自己），并明写 AGENTS.md manifest 含 op-acceptance 本身。

【MED #10 consistency·primary skills 自相矛盾】采纳：二选一定为 primary 只挂 [acceptance-workflow]，acc-spec/acc-runner 下沉 subagent，对齐薄 orchestrator/禁裸调。已删除原 Steps#4 给 primary 挂 3 skill 的写法。

【MED #11 feasibility·acc-runner scope】已核 acc-runner SKILL「legacy/非 math 族/双实现/catlass 当前不支持」。→ 采纳：acc-runner-dev 加显式 scope gate，catlass/legacy/非 math/未支持 dtype→BLOCKED/转 P3，不硬塞。

【MED #12 completeness·合规全靠散文门】采纳（推荐默认，列 open (a)）：加 check_agent_frontmatter.py 轻量机器 lint（mode/dispatch_mode 表/禁用短语/primary skills-agents），与 check_manifest 同类 meta-lint、不入判定链。

【MED #13 risk·「绝不宣告 PASS/FAIL」字面冲突】采纳：全局改「不得自行判定；只能逐字引用确定性产物裁决并标来源」，Goal/Approach/Acceptance 已统一。

【MED #14 ambiguity·mode 字段撞名】采纳：调度 mode 改名 dispatch_mode，与 frontmatter mode:subagent 分开，每 dispatch 给输入模板（dispatch 契约）。

【MED #15 completeness·Open Decisions 是阻塞项却已固定验收】采纳：把 CP 边界/空任务自动化度/状态持久化/issue 提取/primary skills 五项**先决策为默认**（待 #1 点头），从阻塞项移出；真 open 只留 4 项（lint 加否、proposed 页 settle、扩 fetch_source、扩 manifest）。

【LOW #16 risk·「28 单测」写死】采纳：改「测试套件全过并记录实际用例数（当前 28）」。

判定口径统一（跨 #6/#13/Goal）：由原「判定唯一归 validator」修正为「判定唯一归确定性脚本链：validator + perf_compare + acceptance gate」，避免 validator-only 误导。

### 终版 plan

# P1 编排升级 — 薄 orchestrator + 3 subagent + acceptance-workflow skill（终版）

## Goal（目标，已据 codex 修订口径）

把胖 `op-acceptance` agent 改薄为 `mode:primary` 编排器：只做**调度 + 三段式检查点(CP)状态机 + 工件门禁 + 对应校验前置**，并新建 3 个 `mode:subagent`（单轮、`dispatch_mode` 字段、禁内部循环、不自行判定 pass/fail、只回结构化摘要给 orchestrator）。三段 CP 状态机文本沉进新 `skills/acceptance-workflow/SKILL.md`。新增 CP-A「任务书↔PR 对应校验」前置以识别并跳过「未验收空任务」。

**判定口径修正（codex #6/#13）**：PASS/FAIL 唯一归**确定性脚本链**——`validator.py`（精度）+ `perf_compare.py`（性能）+ `validate_acceptance_state.py`（三级完整性门），门控后写 `acceptance.json`。编排层与 subagent **不自行判定，只逐字引用确定性产物的裁决并标来源**（不是「绝不提 pass/fail」）。对外仍是单一对话入口、脚本幕后。

## Approach（落法，已吸收 16 条 findings）

照 ADR 0004（canonical，混合架构：skill/command 入口 + subagent fan-out + AskUserQuestion CP + 确定性 validator）与 cannbot ops-registry-invoke / cannbot-orchestration-and-cross-cli（`mode:primary` + subagent 单轮禁内部循环 + workflow 单数 SKILL 承载状态机）落地。核心动作：

1. **`op-acceptance` 改薄为 primary orchestrator**：frontmatter `mode:primary` + `skills:[acceptance-workflow]`（**只挂 workflow skill**，原子 skill 下沉到 subagent — 解 codex #10）+ `agents:[acc-spec-extractor, acc-runner-dev, acc-verify-rootcause]`（**child_agents，不含自己** — 解 codex #9）。首响应先加载 acceptance-workflow skill、禁裸调 subagent、正文只留调度/CP 引用/工件门/对应前置/失败路由。

2. **职责边界厘清（解 codex #2）**：primary **可直接跑「无 NL 生成、无判定」的确定性脚本**（`fetch_source.py` 取材、`run_workflow.py --mode mock` 自检、`validate_acceptance_state.py` 复核门、`check_manifest_sync.py`）——这与 conversational-agent-sole-delivery-form（proposed，脚本是 agent 内部实现、Bash 幕后跑）一致；primary **不做 NL 生成 durable 工件**（spec/runner 派 subagent），**不自行判 pass/fail**（归确定性脚本链）。

3. **3 个 subagent（`dispatch_mode` 而非 `mode`，避免与 frontmatter `mode:subagent` 撞名 — 解 codex #14）**：
   - `acc-spec-extractor`（`skills:[acc-spec]`）：`dispatch_mode` = `extract_spec`（task_doc+pr_facts→`<op>.spec.json`+`task_pr_gaps`，多算子多 spec）/ `refine_spec`（mock 门失败据 gate error 修 spec）。
   - `acc-runner-dev`（`skills:[acc-runner]`）：`dispatch_mode` = `gen_runner`（据 spec+example 生成 `oprunway_<op>_runner.cpp`+选构建路径，锚定 example 不猜）/ `verify_runner`（验证-才-信：手算 golden 小用例逐元素比）。**显式 scope gate（解 codex #11）**：仅 `experimental/math/<op>` aclnn 闭环；catlass/legacy/非 math 族/未支持 dtype → 返回 `BLOCKED`/转 P3，不硬塞。
   - `acc-verify-rootcause`（无原子 skill）：`dispatch_mode` = `run_npu`（真机 `run_workflow.py --mode new_example`，一次原子跑 Task2 精度 + Task3 性能 + 三级门）/ `rootcause`（任何 FAIL 先「被测物自 build + 声明 dtype + 手算 golden」独立复现，解耦 op vs harness 再归因；技术判定与官方口径分开、不外发）。

4. **CP 映射按脚本真实行为重构（解 codex #1，核心）**：`run_workflow.py` 是**一次性串 Task1→2→3**、`--mode new_example` 会在同一次调用里跑完精度 + 性能 + 三级门并落 `acceptance.json`。故三级 `validate_acceptance_state` 门是 **run_workflow 内部**的、**不是** orchestrator 分阶段单独调度的 stage。CP 重定义为「对话暂停点 + 工件门」，真机执行合并为**一个原子 CP**：
   - **CP-A 前置**（primary 亲自）：取材 → 对应校验（落 `correspondence.json`）→ 环境/模式确认（mock vs new_example、NPU/VPN），AskUserQuestion 必由 primary 做。
   - **CP-B Task1 用例**：dispatch `acc-spec-extractor:extract_spec` → spec + gaps；primary inline `run_workflow --mode mock`（产 `caseset.json` + 内跑 task1 门 + `acceptance.json(mock)`）；mock 裁决异常 → `refine_spec`。
   - **CP-C runner**（真机路径、需 NPU）：dispatch `acc-runner-dev:gen_runner`（先过 scope gate）→ `verify_runner`；未过验证不上真机、不出裁决。
   - **CP-D 真机跑测（一次原子）**：dispatch `acc-verify-rootcause:run_npu` → `run_workflow --mode new_example`（Task2+Task3+三级门 task1/task2/task3 一次成）→ evidence/verdict/baseline/perf_report/acceptance.json；FAIL → `rootcause`。
   - **CP-E 报告**（primary）：逐字引用 `acceptance.json`/`verdict.json`/`perf_report.json` 裁决 + `task_pr_gaps` + 各维度；`needs_review` 不当 pass；门 FAILED→BLOCKED。

5. **CP-A 对应校验落机读工件 + 状态枚举（解 codex #4/#5）**：靠 **改动落点目录（`pr_facts.target_dir`，机器可比）** + **issue/追踪号（NL 读 task_doc/PR title，非算子名字面匹配）** + **用户确认**，落 `correspondence.json`。状态枚举 `status ∈ {confirmed, mismatch, empty_task, needs_user_confirmation}`：`mismatch`/`empty_task` → 出**程序结论（非 pass/fail）**并停跑；`needs_user_confirmation` → primary 摆证据、由用户拍板（Equal 血教训：证据自动摆出 + 用户确认，不自动 judge 空任务）。断点续跑读该工件。

6. **Task3 blocked 路由补齐（解 codex #6）**：acceptance-workflow 写清**基线来源按任务书参考源**（perf-baseline-by-reference-source，proposed：重写类=TBE 无劣化/≥95%、移植类=GPU A100 0.5–0.8×、加 dtype 类=同 op 不劣化；`spec.perf.baseline` 驱动，当前三算子均 `tbe`）与 blocked 状态路由（task3-state-machine，canonical：`BLOCKED_WAIT_GPU_BENCHMARK` 缺外部 GPU 标杆、`BLOCKED_INCOMPARABLE_TIMING_SCOPE` 口径不可比）。GPU external 对比层当前未接入 pipeline（外部给数据）→ 若任务书要求 GPU 基线而无数据即 BLOCKED，不出 pass。

7. **跨 CLI 清单：诚实表述「手工同步 + 机器校验」（解 codex #8），而非「派生」**：`AGENTS.md` frontmatter `agents:[op-acceptance, acc-spec-extractor, acc-runner-dev, acc-verify-rootcause]`（**全部 4 个 plugin 暴露的 agent、含 primary 自身**）+ `skills:[acc-spec, acc-runner, acceptance-workflow]`（全部 3 skill）；`plugin.json` agents 数组手工补齐同 4 路径；`check_manifest_sync.py` 做**漂移门**（agents 三方一致；skills 仅校验文件存在 — 按脚本实际能力表述、不 overclaim skills 三方，解 codex #7）。真正的「单一源生成器」是 P2 的 init.sh 扇出，本 todo 不做。

8. **subagent 结构合规加轻量机器门（解 codex #12，推荐默认）**：新增 `plugin/acc-common/check_agent_frontmatter.py`（只读、stdlib、与 `check_manifest_sync.py` 同类的 dev/CI meta-lint，**不进 run_workflow 判定/执行链**、不违 ADR 0007）——校验各 agent frontmatter 的 `mode` 字段、subagent 的 `dispatch_mode` 表存在、禁用短语存在（如「不得自行判定」「单轮」「禁内部循环」）、primary 的 `skills/agents` 列表。避免「合规全靠散文门」。

9. **只动 Layer 2（.md）+ 1 新 skill + 1 个 meta-lint 脚本**，不碰 Layer 0 契约、不碰 Layer 1 判定/执行脚本（gen_cases/repo_adapter/validator/perf_compare/fetch_source/run_workflow），不重造 P0 门（workflow-three-layer-architecture）。

## Canon 依据（附 trust tier；非 canonical 者已本地核对）

- **ADR 0004 orchestrate-like-cannbot（canonical，当事实）**：混合架构骨架、Workflow 工具不作产品骨架。
- **ADR 0007 deterministic-validator（canonical，当事实，硬约束）**：判定归确定性 validator；编排/subagent 只解释、只引用、不宣告。
- **cannbot-ops-registry-invoke-workflow（canonical）**：阶段+CP+subagent 模板；workflow 单数 SKILL 承载状态机、机器门 STATUS: PASSED 才放行。
- **oprunway-component-breakdown（canonical）**：op-acceptance=orchestrator、三角色够用别过早多拆、判定唯一归 validator、acc-npu-run 内不另立判定。
- **task3-state-machine（canonical）**：4 核心状态 + BLOCKED_WAIT_GPU_BENCHMARK / BLOCKED_INCOMPARABLE_TIMING_SCOPE 边角状态 → CP-D/Task3 路由依据。
- **machine-verifiable-acceptance-gate（verified，非 canonical）**：已本地 Read 核对——三级门 `gate_task1/2/3` 读落盘 evidence 独立复核、接进 run_workflow 做 BLOCKED blocker、28 单测。
- **cannbot-orchestration-and-cross-cli（proposed，未 settle）**：三层 Plugin→Agent→Skill、subagent 单轮禁内部循环 orchestrator 控循环、workflow 单/复数——另以真实 cannbot 制品坐实。
- **verify-spec-pr-correspondence-before-acceptance（proposed，未 settle）**：CP-A 依据——issue 号 + 改动落点目录（非字面名）、识别未验收空任务、Equal #2890 配错作废案例。
- **conversational-agent-sole-delivery-form（proposed，未 settle）**：单一对话入口、脚本是 agent 内部实现 Bash 幕后跑 → 支撑「primary 可跑确定性脚本」的口径。
- **cross-cli-unified-form-agents-md（proposed，未 settle）**：AGENTS.md 单一源、check_manifest 防漂移、生成器是 P2。
- **perf-baseline-by-reference-source（proposed）**：Task3 基线按任务书参考源（TBE/GPU/同 op dtype），GPU 对比层为可选。
- 辅助：root-cause-decoupling-before-attribution（proposed）、task-spec-authoritative-over-pr（proposed）、workflow-three-layer-architecture（proposed，本 P1 不碰 Layer0/1 判定执行链）。

## Files

| 路径 | 动作 | 用途 |
|---|---|---|
| `plugin/skills/acceptance-workflow/SKILL.md` | create | CP-A..E 状态机 + `correspondence.json` schema/状态枚举 + 真机 CP-D 一次原子（Task2+3+三级门内嵌于 run_workflow）+ Task3 blocked 路由（GPU 缺/timing 不可比/基线来源）+ dispatch 契约（工作区/dispatch_mode/输入工件/验收标准/本次产出）+ 断点续跑（工件即状态）+ 单轮/禁内部循环/不自行判定只引用产物裁决 硬约束。skill 只调三级门、不重实现判定。 |
| `plugin/agents/op-acceptance.md` | edit | 改薄为 `mode:primary` + `skills:[acceptance-workflow]`（仅此）+ `agents:[3 child subagent]`（不含自己）；正文只留调度/CP 引用/工件门/CP-A 前置/失败路由；显式：可跑确定性脚本、不做 NL 生成工件、不自行判 pass/fail、首响应加载 workflow skill、禁裸调 subagent；保留单一对话入口/脚本幕后/OPRUNWAY_* 不入仓。 |
| `plugin/agents/acc-spec-extractor.md` | create | `mode:subagent`，`skills:[acc-spec]`，`dispatch_mode`: extract_spec/refine_spec；单轮、禁内部循环、禁跨阶段、不自行判定只回摘要给 orchestrator。 |
| `plugin/agents/acc-runner-dev.md` | create | `mode:subagent`，`skills:[acc-runner]`，`dispatch_mode`: gen_runner/verify_runner + scope gate（非 experimental/math aclnn→BLOCKED/转 P3）；锚定 example 不猜、验证-才-信；单轮、禁内部循环、不自行判定。 |
| `plugin/agents/acc-verify-rootcause.md` | create | `mode:subagent`，`dispatch_mode`: run_npu（run_workflow --mode new_example，一次原子跑 Task2+3+三级门）/ rootcause（先解耦 op vs harness 再归因、不外发、不替 PR 作者修到底）；单轮、禁内部循环、不自行判定只引用产物裁决。 |
| `plugin/AGENTS.md` | edit | frontmatter `agents` 增至 4（含 primary 自身）、`skills` 增 acceptance-workflow；正文加 CP-A 对应校验前置、3 subagent 编排 + dispatch_mode 表 + 单轮/禁内部循环/不自行判定硬约束 + plugin_agents vs child_agents 语义澄清；保留三级门段（诚实表述手工同步+机器校验，非「派生」）。 |
| `plugin/.claude-plugin/plugin.json` | edit | agents 数组补为 4 路径，与 AGENTS.md frontmatter agents 按 stem 完全一致（check_manifest_sync 要求）。 |
| `plugin/acc-common/check_agent_frontmatter.py` | create（推荐，待 open decision (a) 确认） | dev/CI meta-lint：校验 4 agent frontmatter 的 mode/dispatch_mode 表/禁用短语/primary skills-agents 列表。轻量机器门补 codex #12。 |
| `plugin/commands/op-acceptance.md` | edit | 流程描述指向新 orchestrator（先 CP-A 对应校验/取材→再走 CP-B..E），提「对应不成立/未验收空任务→出程序结论不跑」。 |
| `doc/oprunway-changes-brief.md` | edit | 倒序 append 一两句大白话（CLAUDE.md #4）。 |

## Steps

1. **方案确认（CLAUDE.md #1 门）**：把修订后 CP 映射（CP-A..E、真机 CP-D 一次原子）、`correspondence.json` schema + 状态枚举、orchestrator↔确定性脚本↔subagent 职责边界、Task3 blocked 路由、manifest 的 plugin_agents vs child_agents 语义、以及 `check_agent_frontmatter.py` 机器 lint 加不加，呈用户点头后落地。
2. **写 `acceptance-workflow/SKILL.md`**：落 CP-A..E；每 CP 写清 dispatch 哪个 subagent + dispatch_mode、产哪个工件、（CP-B mock / CP-D new_example）run_workflow 内部跑哪级门、门 FAILED→BLOCKED 路由、Task3 blocked 状态与基线来源；写 dispatch 契约、断点续跑（工件即状态）、`correspondence.json` schema/枚举、不自行判定只引用产物裁决。
3. **写 3 个 subagent .md**：均 `mode:subagent` + `dispatch_mode` 字段（取值见 Files）+ 单轮/禁内部循环/禁跨阶段/只回摘要/不自行判定；acc-runner-dev 加 scope gate；把锚定 example 不猜、验证-才-信、先解耦再归因写进对应 subagent。
4. **改薄 `op-acceptance.md`**：frontmatter `mode:primary` + `skills:[acceptance-workflow]` + `agents:[3 child]`；正文薄化，显式职责边界（可跑脚本/不 NL 生成/不判定）。
5. **同步 manifest（手工同步 + 机器校验）**：AGENTS.md frontmatter agents(4)/skills(3) + 正文编排更新；plugin.json agents 补 4 路径；跑 `python3 plugin/acc-common/check_manifest_sync.py` → 必须 `STATUS: SYNCED`。
6. **（可选）加 `check_agent_frontmatter.py` + 轻改 command**：若 open decision (a) 采纳则写 lint 脚本；command 指向新 orchestrator + 提 CP-A 前置。
7. **本地回归验证**：`python3 plugin/acc-common/test_validate_acceptance_state.py`（**测试套件全过，并记录实际用例数——当前 28**，解 codex #16）+ `python3 plugin/acc-common/run_workflow.py plugin/acc-common/specs/isclose.spec.json --mode mock --out <scratchpad>`（确认仍出 `acceptance.json` 且内嵌验收门 PASSED、pipeline 未被 .md 改动破坏）+ check_manifest_sync SYNCED +（若加）check_agent_frontmatter 通过 + 用 fixture task_doc/pr_facts 走一遍 CP-A 对应校验逻辑（issue 号/target_dir 比对 + 空任务识别 + `correspondence.json` 落盘）。
8. **散文门 codex exec 审修**：对全部新增/改动 .md（orchestrator + 3 subagent + skill + AGENTS.md + command）跑 codex exec 定制审（散文，非 cc-suite 代码 9 维）；若加 check_agent_frontmatter.py 则走 `cc-suite:audit-fix` 代码门。审→修→复述「发现什么/改了什么/剩余风险」。
9. **收尾落档**：`doc/oprunway-changes-brief.md` 倒序 append；（可选、若动 canon）bureau:note 捕获 P1 落地，note 文本先过 codex exec 审再写入。

## Gates

- **散文门（核心，CLAUDE.md #5 散文分支）**：每份新增/改动 .md 走 codex exec 定制审修，审后复述发现/改动/风险；nlpm 非本门。
- **代码门（若加 lint 脚本，CLAUDE.md #5 代码分支）**：`check_agent_frontmatter.py` 走 `cc-suite:audit-fix` 9 维审修。
- **机器门（回归证明没破）**：check_manifest_sync→`STATUS: SYNCED`；`test_validate_acceptance_state.py` 测试套件全过（记录实际用例数）；run_workflow --mode mock 出 `acceptance.json` 且内嵌验收门 PASSED；（若加）check_agent_frontmatter 通过。
- **bureau 门（仅当动 canon）**：若借本 todo settle 三 proposed 页或写 P1 落地 note → capture→compile→review，写入前先 codex exec 审文本。
- **用户门（CLAUDE.md #1）**：方案先经用户同意才落地。本 todo 纯本地 .md + meta-lint，无对外发布/改远端环境，副作用门不触发。

## Blocked / 依赖

- **本地能立刻做**：全部 5 份 .md 编排制品 + AGENTS.md/plugin.json 手工同步 + command 轻改 + （可选）check_agent_frontmatter.py + 改动简表；本地可完整验证 check_manifest_sync（SYNCED）、测试套件、run_workflow mock 端到端（spec→caseset→evidence→verdict→acceptance.json 门 PASSED）、以及 fixture 演练 CP-A 对应校验 + correspondence.json 落盘。
- **卡真机 NPU/VPN**：acc-runner-dev 的 verify_runner 真机编译跑、acc-verify-rootcause 的 run_npu 真机跑测 + FAIL「被测物自 build + 声明 dtype + 手算 golden」独立复现解耦——只能设计 + mock，真机端到端待开 NPU/VPN（ascend-a5 真 950 / a3 A2A3）。
- **卡 issue 号机器化**：`fetch_source.py` 当前 `pr_facts.json` **不抽 issue 号**（仅有 op/target_dir/changed_files/title）。默认走 **NL 读 task_doc/PR title + 用户确认**（保持纯 Layer2）；若要机器化抽取需扩 fetch_source（Layer1 小改，越出「纯 .md」范围）→ 见 open decision (c)。
- **卡用户批**：落地前 CLAUDE.md #1 点头；3 个 proposed canon 页是否借本 todo settle/升 canonical（bureau review 是人门）。
- **不涉及**：GPU 标杆外部数据/外发授权与本 todo 无关。

## Acceptance（与已决默认一致，无先后矛盾）

1. `plugin/agents/` 下 4 个 agent 存在且 frontmatter 合规：`op-acceptance`（`mode:primary`、`skills:[acceptance-workflow]` 仅此、`agents:[3 child]` 不含自己、无逐步脚本细节、显式「可跑脚本/不 NL 生成工件/不自行判定/首响应加载 workflow/禁裸调 subagent」）；3 个 subagent（均 `mode:subagent` + `dispatch_mode` 表 + 明写单轮/禁内部循环/不自行判定只引用；acc-runner-dev 含 scope gate）。
2. `plugin/skills/acceptance-workflow/SKILL.md` 存在，承载 CP-A..E 状态机 + `correspondence.json` schema/状态枚举（confirmed/mismatch/empty_task/needs_user_confirmation，哪些自动停/哪些问用户）+ 真机 CP-D 一次原子（Task2+3+三级门内嵌 run_workflow）+ Task3 blocked 路由 + 基线来源 + dispatch 契约 + 断点续跑。
3. `check_manifest_sync.py`→`STATUS: SYNCED`（AGENTS.md agents ↔ plugin.json agents ↔ agent 文件 三方一致；AGENTS.md skills ↔ skill 文件存在——**按脚本实际能力表述、不 overclaim skills 三方**）。
4. 测试套件全过（记录实际用例数，当前 28）；`run_workflow.py isclose.spec.json --mode mock` 出 `acceptance.json` 且验收门 PASSED（回归未破）。
5. （若采纳）`check_agent_frontmatter.py` 通过。
6. 所有新增/改动 .md 过 codex exec 散文门审修并复述；`doc/oprunway-changes-brief.md` 倒序 append 一条。
7. 判定唯一在确定性脚本链（validator + perf_compare + acceptance gate，ADR 0007）；编排层与 subagent **不新增任何自行宣告 pass/fail 的文本**，只逐字引用确定性产物裁决并标来源。

## Open Decisions

**已决默认（待 CLAUDE.md #1 用户点头，Steps/Acceptance 已按此固定，解 codex #15）**：
- CP-A 合并「取材 + 对应校验 + 环境/模式确认」为一个前置暂停点（primary 亲自 AskUserQuestion）。
- 空任务判定 = 证据自动摆出 + 状态枚举：`mismatch`/`empty_task` 自动停并出程序结论，`needs_user_confirmation` 必问用户（不自动 judge）。
- 状态持久化 = 工件即状态 + `correspondence.json`（CP-A 机读工件）。
- issue 号 = NL 读 task_doc/PR title + 用户确认，不改 Layer1。
- primary skills 仅 `[acceptance-workflow]`，原子 skill 下沉 subagent。

**真 open（需用户/人门定）**：
- (a) 是否加 `check_agent_frontmatter.py` 轻量机器 lint（小 Layer1 meta-lint，**我倾向加**——是 codex #12 的诚实答案，且与 check_manifest_sync 同类不入判定链）。
- (b) `verify-spec-pr-correspondence` / `conversational-agent-sole-delivery-form` / `cross-cli-unified-form-agents-md` 三个 proposed 页是否借本 todo 推 bureau review 升 canonical，还是留 proposed。
- (c) 将来是否扩 `fetch_source.py` 机器抽 issue 号（超本 todo 纯 .md 范围）。
- (d) 是否将来扩 `check_manifest_sync.py` 到 skills 真三方校验（当前只文件存在；超本 todo，或按现状诚实表述）。


---

## T3-P2-atomize-distribute — P2 原子化 + 分发（据 codex 审计修订终版）

**codex 总判**：codex 真跑成功（codex_ran_ok=true），总判 major-gaps，6 条 high + 9 条 medium + 2 条 low（含 raw_tail 重复的 4 条）。经逐条代码库核实：6 条 high 全部成立并已在终版解决（#1 trust-tier 措辞、#2 skill 计数+P1 workflow-skill、#3 validator 无 MERE/MARE、#4 perf_compare 无仿真图、#6 库不接 runtime）；唯 #5 一半驳回——cc-suite:audit-fix 本地确实可用（cache+available-skills 双证），codex 误判其不可用，但其 shellcheck 缺失半条成立并已处理。medium/low 全部采纳或部分采纳（#13 部分、其余全采）。**已无 high 未解**：所有 high 要么落进终版 plan，要么（#5 半条）有实证驳回依据。终版尚存 proposed-页依赖与真机/用户决策未决（见 residual_risks），但均已显式标注为开工门或路线图、未当既定事实。

**可实施性**：no — 卡在开工决策门。本地机械产出（4 SKILL.md+refs 草稿、workflows 材料仓、init.sh 编写+bash -n+--dry-run 自测、AGENTS.md+check_manifest_sync、bureau note/compile 到 proposed、两道审修门、改动简表）随时可动手；但据 CLAUDE.md #1/#6 与 codex#9/#16，durable 落地前须用户先拍板 §9 六项（原子化方向 + 授权 supersede canonical + skill/脚本边界口径 + archive 白名单 + init.sh 首发 CLI 面 + 是否扩 validator/perf），且通读 canon 记 logbook。拍板后本地即可全量出草稿，唯 canonical 晋级（用户 review）、init.sh 跨 CLI 真验（目标 CLI 环境）、shellcheck（brew 装）三点仍卡外部。

**残余风险**：1. **proposed 依赖未 settle**：cross-cli / cannbot-orchestration / three-layer 三页仍 proposed，本 plan 只作「拟采纳假设」，若用户或后续 review 推翻方向，workflows 材料仓 + init.sh 扇出形态可能返工。2. **canonical supersede 悬置**：component-breakdown 更正只能到 proposed，canonical 晋级卡用户 bureau:review，期间「3-skill vs 4-atomic」处双 tier 并存态。3. **脚本能力边界 vs 方法论落差**：precision/perf/casegen 三 skill 描述的完整方法论（MERE/MARE、仿真图例外、rule-catalog 通用 generator）超出现脚本判定能力，靠「诚实标注为目标态」弥合；若用户期望 P2 即可判 MERE/MARE，则须折叠 validator/perf 扩展（open_decision③），范围与工期上升。4. **跨 CLI 真验缺口**：init.sh 仅 Claude 分支本地 fixture 实跑，opencode/trae/cursor/copilot 的 symlink 存活 + 配置落点未真机验证；symlink 打包存活风险靠 materialize 兜底但未在目标 CLI 实测。5. **shellcheck 未装**：代码门 shell lint 依赖 brew 装或替代门，未装时 init.sh 静态检查强度略降。6. **archive 种子仅 2 例且含 1 例 FAIL**：案例库覆盖薄，扩面卡真机验收。

**据 codex 修订**：逐条处理 codex 17 条 issue（先在代码库核实再定）：

【HIGH】
#1 proposed 页被称「已定决策/方向已定」违反 trust-tier — **采纳**：核实 cross-cli-unified-form-agents-md / cannbot-orchestration-and-cross-cli / workflow-three-layer-architecture 三页 frontmatter 均 status: proposed。approach 全改为「拟采纳假设（proposed·未 settle），载重前经决策门/review 确认」，不再以之作已 settle 表述。
#2 「skills 集=6」与 acceptance-workflow skill 关系不清、P1 状态机 skill 可能漏 — **采纳**：核 design doc line 21/23/37/73，全集实为「6 原子能力 skill + 1 acceptance-workflow workflow-skill（P1）」。新增 §3 明确口径：P2 交 6 原子能力 skill，acceptance-workflow + 3 subagent 属 P1 非范围。
#3 acc-precision 写 MERE/MARE 但 validator.py 只有 exact_mismatch/max_rel_err — **采纳**：核 validator.py 第 16-30 行确认只支持这俩、无 MERE/MARE。改为 skill 诚实标注 validator 能力边界、MERE/MARE-by-dtype 标为 canon 目标态+待办，不声称已能判；扩 validator 列 open_decision③默认独立 todo（避免把文档/打包 todo 膨胀成脚本能力扩展）。
#4 acc-perf「小 shape 仿真图例外」但 perf_compare.py 只 ratio+scope — **采纳**：核 perf_compare.py 确认只做 ratio=baseline/npu + scope blocked、无仿真图/例外状态。改为方法论标注、仿真图例外标为 task3-state-machine 目标态、脚本未实现。
#5 代码门依赖 cc-suite:audit-fix + shellcheck 不在 PATH、与「本地全量做完」矛盾 — **部分采纳/部分驳回**：核实 cc-suite:audit-fix **本地可用**（~/.claude/plugins/cache/xiaolai/cc-suite/0.8.1 有 audit-fix，且在 available-skills 列表）→ 驳回「cc-suite 不可用」半条；shellcheck **确缺**（which 未找到，brew 可装）→ 采纳，改为 brew 装(网络+确认)或以 bash -n + cc-suite shell 维度替代并标「shellcheck 未跑」，「本地全量做完」措辞收敛。
#6 只改 AGENTS.md 不会让 agent/command 真调新 skill、也没建 P1 subagent — **采纳**：核 agents/commands/op-acceptance.md 确认只引 acc-spec/acc-runner、六步走 run_workflow.py。§0/§3 明确 P2=库交付、不接 runtime、不建 subagent（属 P1）。

【MEDIUM】
#7 check_manifest_sync 不校验 plugin.json skills/CLAUDE.md 派生 — **采纳**：核脚本确认只校验 AGENTS skills-file 存在 + plugin.json agents 一致；核 plugin.json 无 skills 键（grep=0）。删原 plugin.json 编辑项（改 no-change），验收表述收敛为「仅校验其真校验项」。
#8 Sign 列「真机 verified」种子但性能未达成、不能与 PASS 混称 — **采纳**：核台账确认 sign_004 ratio 0.653「性能未达成」、精度 5/5 过；IsClose 无社区 PR。archive 卡片改为区分 verdict：isclose=PASS、sign=精度过·性能 FAIL 样本。
#9 open decision ②③④ 影响 durable 却只有 canon 冲突作硬门 — **采纳**：§5-0 把全部 durable-影响决策前置为单一开工门，未确认不落对应文件。
#10 「全体 SKILL.md 无硬编码阈值」撞 acc-spec 的 1e-3 — **采纳**：核 acc-spec SKILL.md line 23 确有 fp16≈1e-3。grep 范围限 4 个新 skill，并厘清判据＝无 pass/fail 判定阈值；acc-spec 的 1e-3 是写进 spec 的 (推断) 默认、属 spec-population 非判定。
#11 init.sh 缺覆盖/备份/卸载/幂等/回滚、真跑改用户环境 — **采纳**：§6 安装矩阵 + --force/--uninstall/--dry-run + 备份 .bak.<ts> + 幂等 + 真写前二次确认 + 首发只 fixture 实跑。
#12 init.sh tool/level 取值、CLI 目标目录、symlink 策略、冲突行为未定义 — **采纳**：§6 加安装矩阵表 + 冲突规则，验收按矩阵逐项。
#13 ${CLAUDE_PLUGIN_ROOT} 跨 CLI 不中立、设计用 OPRUNWAY_PLUGIN_ROOT/manifest — **部分采纳**：init.sh 主变量改 OPRUNWAY_PLUGIN_ROOT + Claude 别名；但不在本 P2 重写现有 skills/agent 的 ${CLAUDE_PLUGIN_ROOT}（更大改动 + cross-cli 页 proposed 未 settle）→ 列 open_decision⑤，避免越界。
#14 archive_ops symlink 未验证打包/安装/跨平台存活 — **采纳**：init.sh 加 readlink/断链检测 + 不保 symlink 时 materialize-with-provenance 兜底；验收含 symlink 可解析。
#15 bureau 门次序自相矛盾（Steps 先写 proposed，Gates 又说 bureau 在散文门后，而散文门含未生成的 SKILL.md）— **采纳**：拆成 pre-bureau 文本门（只审 supersede 文本、先于 durable 写入）与 post-artifact 散文门（审已生成 SKILL.md/refs/workflows），§5/§6 分别定义输入与次序。
#16 CLAUDE.md #6 要求 durable 前通读 canon、plan 只选择性 grounding — **采纳**：§5-0 加通读 canon（或 overview+query 降级）+ 记 logbook + 「未读页不得作门禁依据」。
#17（low）archive case card 无字段 schema — **采纳**：§8-⑦定义 case.md 最小字段 schema（op/taskdoc/pr/verdict/evidence_path/real_machine/caveat）+ 入库校验清单，落 archive_ops/README.md。

补充修正（核实中发现，非 codex 提出）：canonical component-breakdown 的「别过早多拆」经核实是针对**子 agent 角色**（原文「三角色够用」）、非 skill 数量；真正冲突是「3-skill 枚举」。原 plan 把它读成 skill 层，已在 §1/§2 更正，使冲突范围更准。

### 终版 plan

# T3-P2 · 原子化 + 分发 — 终版 plan（据 codex major-gaps 审计逐条修订）

## 0. 目标（goal）

把当前按「制品/任务」切的 skill 泛化成按「原子能力」切的 **skill 库**（补 `acc-casegen`/`acc-precision`/`acc-perf`/`acc-rootcause` 四份薄壳），配 `workflows/` 材料仓（蓝图 + 分阶段 dispatch prompt + 已验证算子案例，无 SKILL.md）与 `init.sh` 安装期扇出，让 OpRunway 具备 cannbot 同款「原子 skill 供 subagent 组合 + 跨 CLI 分发」形态。

**⚠ 范围校正（吸收 codex#6）：P2 只交付「能力库 + 材料仓 + 分发脚本」，不改运行路径。** 现有 `op-acceptance` agent/command 的六步流程仍走 `run_workflow.py`（内部调 `gen_cases`/`validator`/`perf_compare`），本 P2 **不**把四个新 skill 接进 agent 的 runtime 调用链，也**不**建 P1 的三个 subagent（`acc-spec-extractor`/`acc-runner-dev`/`acc-verify-rootcause`）与 `acceptance-workflow` skill——那些属 P1，见 §3 非范围。

## 1. 打法 / approach（对齐已定决策、不重推；trust-tier 已按 codex#1 纠正）

对齐 cannbot 已验证范式，但**严格区分 canonical（当事实）与 proposed（拟采纳假设，非事实）**：

- **canonical 依据（当事实用）**：ADR 0004（skill 入口 + subagent fan-out + validator 混合架构）、ADR 0007（判定脑子在 validator.py，不烤进 skill）。四个原子 skill 只装「方法论/怎么做」，pass/fail 计算与阈值仍归 `acc-common` 脚本，脚本不搬进 skill、一律以中立根变量引用。
- **proposed 假设（⚠ 未 settle，载重前须用户确认或 bureau review）**：`cannbot-orchestration-and-cross-cli`、`cross-cli-unified-form-agents-md`、`workflow-three-layer-architecture` 三页均为 `status: proposed`。它们提供的「原子 skill 库 + workflows/ 材料仓判据(无 SKILL.md) + AGENTS.md 单一源 + init.sh 扇出 + 三条避坑」是**拟采纳假设，不是已定决策**。本 plan 把它们当**设计方向候选**，durable 落地前经 §9 开工决策门确认；不以 proposed 页作「已 settle」表述。
- **⚠ 核心冲突（canonical 直接抵触）**：`oprunway-component-breakdown`（canonical）明确列 **3 个 skill**（`acc-casegen`/`acc-npu-run`/`acc-perf-compare`）。注意该页的「别过早多拆」是针对**子 agent 角色**（原文「三角色够用，别过早多拆」），**不是**针对 skill 数量；真正抵触的是「3 skill 枚举」与本 todo 的「4 原子能力切分（`acc-casegen`/`acc-precision`/`acc-perf`/`acc-rootcause`）」。该页已含「⚠ 未来拆点」条款（`acc-npu-run` 的 repo-adapter/harness 成共享执行基建后毕业），本 todo 的能力切分是否落在其精神内需用户裁。本 plan **不静默覆盖**，走 §9 决策门 + 拆分后的 pre-bureau 文本门（先审文本）→ bureau note→compile（落 proposed）→ 留用户 review promote。
- **避坑**：单一源生成不双写、不照搬 `external_directory:allow`、不 sed 私有/绝对路径进制品（用中立根变量 + symlink 保相对拓扑、`OPRUNWAY_*` 不入仓）。

## 2. canon 依据（按 tier；#6 通读已置为开工前置）

**⚠ CLAUDE.md #6（吸收 codex#16）：开工任何 durable 制品前，先通读 `canon/`（全部 `architecture/` + `decisions/` + `lint/findings.md`）或按规则降级 `00-overview + bureau:query`，并把「读过哪些页、载重引哪些」记进 logbook。未读页不得作为门禁/落地依据。** 本 plan 已核以下页 tier：

- `oprunway-component-breakdown`（**canonical**，2026-07-02 reviewed）：3-skill 枚举 + 「别过早多拆」（针对子 agent 角色）——**本 todo 与「3-skill 枚举」冲突**，是必须走 bureau 门更正的对象。
- ADR 0004 `orchestrate-like-cannbot`（**canonical**）：混合架构 → 支撑「原子 skill 供 subagent 组合」+「workflows/ 材料仓非 skill」。
- ADR 0007 `deterministic-validator`（**canonical**）：判定归 validator.py → 支撑 precision/perf skill 只做方法论。
- `workflow-three-layer-architecture`、`cannbot-orchestration-and-cross-cli`、`cross-cli-unified-form-agents-md`（**均 proposed·未 settle**）：Layer2 薄壳可替换 / 原子 skill 库判据 / AGENTS.md 单一源 + 扇出——载重前标未 settle，经决策门确认。
- 建各 SKILL.md `references/` 时须再逐个 Read 并按 tier 引：`ecosystem-precision-standard` / `ascendoptest-precision-thresholds` / ADR 0005（精度）、`perf-baseline-by-reference-source` / ADR 0006 / `task3-state-machine`（性能）、`root-cause-decoupling-before-attribution` / `verify-spec-pr-correspondence-before-acceptance`（root-cause，Equal 血教训）、`primitive-to-case-rule-library` / `generated-harness-responsibilities`（casegen）。
- `doc/oprunway-agent-system-design.md`（**doc·非 canon 无 tier**）：P2 意图与 skill 全集参考，非权威。

## 3. P2 范围 vs 非范围（吸收 codex#2、#6 — 校正 skill 计数与运行路径）

**skill 计数口径校正**：设计 doc 的全集是 **6 个原子能力 skill**（`acc-spec`/`acc-runner`/`acc-casegen`/`acc-precision`/`acc-perf`/`acc-rootcause`）**外加 1 个 `acceptance-workflow` workflow-skill**（承载 CP 状态机 + 机器门，属 **P1**）。故：
- **P2 交付**：补齐 4 个原子能力 skill（casegen/precision/perf/rootcause）薄壳 + references；`workflows/` 材料仓；`init.sh`；AGENTS.md skills 列表 → 6。**共 6 原子能力 skill。**
- **P2 非范围（不做，避免误以为漏）**：`acceptance-workflow` skill（P1）；三个 subagent（P1）；把新 skill 接进 agent/command runtime 调用链（P1/后续）；扩 `validator.py`/`perf_compare.py`/`gen_cases.py` 判定能力（见 §9 open_decision③，默认独立 todo）。

**skill↔脚本能力边界（吸收 codex#3/#4/#5 — 诚实标注脚本现状，禁止 skill 声称脚本做不到的判定）：**

| skill | 指向脚本 | 脚本**当前**判定能力 | skill 里方法论 vs 现状的诚实标注 |
|---|---|---|---|
| `acc-casegen` | `gen_cases.py` | **硬注册** `GOLDEN={IsClose,Sign,Equal,Neg}`，非 rule-catalog 驱动 | 首发＝方法论参考（`rule-catalog.md`「拆原语→查库→实例化」）供人/subagent 设计用例 + `gen_cases.py` 仅对已注册算子确定性产 caseset；**「rule-catalog→通用 generator」是路线图、不声称当下可落地** |
| `acc-precision` | `validator.py` | 仅 `exact_mismatch`(exact) / `max_rel_err`(numerical)；**无 MERE/MARE** | 方法论写 oracle 分层 + dtype 分层度量（引 canon）；**明标「validator 现仅支持 exact_mismatch/max_rel_err；MERE/MARE-by-dtype 为 canon 目标态、validator 扩展待办」，不声称已能判 MERE/MARE** |
| `acc-perf` | `perf_compare.py` | 仅 `ratio=baseline/npu` + scope 一致性 blocked；**无仿真图/例外状态** | 方法论写 msprof kernel-only + timing_scope + TBE 基线；**「小 shape 仿真图例外」标为 canon 概念(task3-state-machine)、perf_compare.py 未实现 → 路线图，不声称已有例外状态字段** |
| `acc-rootcause` | 无（纪律 skill） | — | 指向 canon 解耦纪律，无脚本判定，无边界问题 |

**跨 CLI 根变量（吸收 codex#13，部分采纳）**：`init.sh` 自身用中立主变量 **`OPRUNWAY_PLUGIN_ROOT`**（默认由脚本自身位置解析），Claude 分支再兼容 `${CLAUDE_PLUGIN_ROOT}` 别名。**不**在本 P2 重写现有 skills/agent 里已有的 `${CLAUDE_PLUGIN_ROOT}` 引用（属更大改动、且 cross-cli 页 proposed 未 settle）→ 列 §9 open_decision⑤。

## 4. 交付文件清单

| 路径 | 动作 | 说明 |
|---|---|---|
| `plugin/skills/acc-casegen/SKILL.md` | create | 薄壳：能力＝spec→caseset；生成下放 `${OPRUNWAY_PLUGIN_ROOT}/acc-common/gen_cases.py`（现仅注册算子）；知识指 `rule-catalog.md` + canon `primitive-to-case-rule-library`；**明标通用 generator 为路线图**；不 copy 脚本、不硬编码阈值 |
| `plugin/skills/acc-precision/SKILL.md` | create | 精度方法论薄壳：oracle 分层 + dtype 分层度量；pass/fail 计算下放 `validator.py`(ADR 0007)、阈值取自 spec；**明标 validator 现仅 exact_mismatch/max_rel_err、MERE/MARE 为待办** |
| `plugin/skills/acc-precision/references/precision-methodology.md` | create | 详规，引 `ecosystem-precision-standard`/`ascendoptest-precision-thresholds`/ADR 0005（按 tier，载重前逐个 Read 核） |
| `plugin/skills/acc-perf/SKILL.md` | create | 性能方法论薄壳：msprof kernel-only + timing_scope 必填 + TBE 基线；计算下放 `perf_compare.py`（现仅 ratio+scope）；**「小 shape 仿真图例外」标为 canon 目标态、脚本未实现** |
| `plugin/skills/acc-perf/references/perf-methodology.md` | create | 详规，引 `perf-baseline-by-reference-source`/ADR 0006/`task3-state-machine`（按 tier） |
| `plugin/skills/acc-rootcause/SKILL.md` | create | FAIL 解耦纪律薄壳：被测物自 build + 声明 dtype + 手算 golden 独立复现、custom-vs-builtin 对照，再归因 |
| `plugin/skills/acc-rootcause/references/rootcause-decoupling.md` | create | 详规，引 `root-cause-decoupling-before-attribution`/`verify-spec-pr-correspondence`（Equal 血教训） |
| `plugin/workflows/development-guide.md` | create | 六步验收蓝图 + CP 门（源 AGENTS.md 流程 + design §2），材料仓、无 SKILL.md |
| `plugin/workflows/task-prompts.md` | create | P1 subagent 分阶段 dispatch prompt 模板库；**显式前向声明依赖 P1 subagent（本 P2 未建）**，标「模板待 P1 落地后接线」 |
| `plugin/workflows/archive_ops/README.md` | create | 案例库说明 + **case.md 字段 schema + 入库校验清单**（见 §8-⑦）+ 入库判据（仅真机-verified 才收；**须标 verdict 类别，不混称 PASS**） |
| `plugin/workflows/archive_ops/isclose/case.md` | create | **PASS 案例**（精度+性能皆过；⚠ 无社区任务 PR，provenance 标「from-scratch example」）+ symlink 到 `acc-common/specs/isclose.spec.json` 与 `new_example/oprunway_isclose_runner.cpp` |
| `plugin/workflows/archive_ops/sign/case.md` | create | **精度-PASS / 性能-FAIL 案例**（PR #2702；`sign_004` 9.68us vs TBE 6.32us、ratio 0.653；含 caveat）——**明标为「已实测·性能未达成」样本，非 PASS** + symlink 到对应 spec/runner |
| `plugin/init.sh` | create | 安装期扇出（见 §6 安装矩阵）：`--tool`/`--level`/`--dry-run`/`--force`/`--uninstall`；主变量 `OPRUNWAY_PLUGIN_ROOT`；不 sed 私有/绝对路径、不搬 `external_directory:allow`；symlink 存活校验 + 断链检测 + 不保 symlink 时 materialize-with-provenance 兜底 |
| `plugin/AGENTS.md` | edit | frontmatter `skills:` 由 `acc-spec/acc-runner` → 补齐 6（+ casegen/precision/perf/rootcause）。**只改这一处** |
| `plugin/.claude-plugin/plugin.json` | **no-change** | plugin.json **无 skills 键**（只列 agents，本 todo agents 不变）→ 无需改；`check_manifest_sync` 只校验 agents 一致 + 各 skill SKILL.md 存在 |
| `canon/architecture/oprunway-component-breakdown.md` | edit(**经 bureau，非手改**) | 走 pre-bureau 文本门 → note→compile（落 proposed 记 3-skill→4-atomic supersede）→ 留用户 review promote；禁 hand-edit cabinet |
| `doc/oprunway-changes-brief.md` | edit | 倒序追加一两句（CLAUDE.md #4） |

## 5. 步骤

0. **通读 canon + 开工决策门（硬前置，吸收 #16/#9）**：先通读 `canon/`（或 `00-overview`+`query` 降级）并记 logbook；再向用户一次性列 §9 全部**影响 durable 产物的开放决策**（原子化方向 + 授权 supersede canonical + skill/脚本边界口径 + archive 白名单 + init.sh 首发 CLI 面 + 是否在本 todo 扩 validator/perf）。**任一未点头 → 对应 durable 文件不落。**
1. **pre-bureau 文本门**（吸收 #15，拆门次序）：`codex exec` 审「拟写入 bureau 的 supersede 文本」（更正理由 + 与 ADR 0004/0007 一致性；只审这段文本，**不含尚未生成的 SKILL.md**）→ 无误后 `bureau:note` 落 logbook → `bureau:compile` 成 proposed（记 provenance、标 component-breakdown 被 supersede）→ 留 `bureau:review` 由用户 promote。禁手改 cabinet。
2. **建 4 个原子 skill 草稿**（按 §4 与 §3 能力边界表，逐个诚实标脚本现状）：`acc-casegen`（补薄壳，指 rule-catalog + gen_cases，标通用 generator 路线图）；`acc-precision`（SKILL+ref，标 validator 现能力边界）；`acc-perf`（SKILL+ref，标 perf_compare 现能力边界、仿真图例外为目标态）；`acc-rootcause`（SKILL+ref，Equal 血教训）。载重前逐个 Read 引用页核 tier。
3. **建 `workflows/` 材料仓（无 SKILL.md）**：`development-guide.md`；`task-prompts.md`（前向声明依赖 P1）；`archive_ops/README.md`（含 case 字段 schema + 入库清单）+ `isclose/`（PASS）+ `sign/`（精度过·性能 FAIL 样本），案例卡 + symlink 到 specs/new_example（`ls -l` 见箭头）。
4. **写 `init.sh`**（按 §6 安装矩阵）：参数 `--tool`/`--level`/`--dry-run`/`--force`/`--uninstall`；冲突/备份/幂等/回滚规则；主变量 `OPRUNWAY_PLUGIN_ROOT`（Claude 别名兼容）；symlink 存活+断链检测+materialize 兜底；避坑不 sed 私有路径、不搬 external_directory。**真实 project/global 写入前显式二次确认**；首发只在临时 fixture 实跑（吸收 #11/#12/#14）。
5. **单一源同步**：只改 `plugin/AGENTS.md` frontmatter `skills:` → 6；跑 `python3 acc-common/check_manifest_sync.py` 确认 STATUS: SYNCED（它校验 6 skill 各有 SKILL.md + plugin.json agents 一致；**不**校验 plugin.json skills/CLAUDE.md 派生——见 #7，不声称它校验那些）。
6. **post-artifact 散文门**（吸收 #15）：4 SKILL.md + 3 references + `workflows/*.md` + `archive_ops` 卡片 → `codex exec` 定制审→修，复述发现/改动/风险。
7. **代码门**（吸收 #5）：`init.sh` + 任何 helper → `cc-suite:audit-fix`（9 维审→修→验循环，**cc-suite 本地可用**）+ `bash -n`；**shellcheck 本地缺**→ 二选一：`brew install shellcheck`（需网络+用户确认）后再跑，或以 `bash -n` + cc-suite 的 shell 维度替代并在结论标「shellcheck 未跑」。不声称 shellcheck 已跑除非真装。
8. **记账**：`doc/oprunway-changes-brief.md` 倒序追加一两句（原子化 6-skill + workflows 材料仓 + init.sh 扇出 + component-breakdown 经门 supersede + P2=库不接线）。

## 6. 门（五道，含拆分的 bureau 双门；吸收 #15/#5/#7/#11/#12）

1. **开工决策门**（§5-0）：§9 全部 durable-影响决策未确认 → 对应文件不落。
2. **pre-bureau 文本门**：`codex exec` 审 supersede 文本 → 再 note→compile（proposed）。**先于任何 durable 写入**，输入只含该文本、不含尚未生成的 SKILL.md。
3. **post-artifact 散文门**：4 SKILL.md + 3 refs + `workflows/*.md` + 卡片 → `codex exec` 审→修，复述结论。
4. **代码门**：`init.sh`+helper → `cc-suite:audit-fix`（可用）+ `bash -n`；shellcheck 缺 → 装后补跑或替代并标注。
5. **单一源同步门**：`check_manifest_sync.py` → STATUS: SYNCED（口径限其真校验项）。

**bureau 门**（canonical component-breakdown supersede）：note→compile→review，canonical 由用户 promote，禁手改 cabinet；文本已在门2先审。

**⚠ 机器门 `validate_acceptance_state.py` 本 todo 不触发**（不产裁决/verdict）——它只是 precision/perf skill 指向的下游门；`archive_ops` 收录算子须为已过该门/真机 verified 者（含性能 FAIL 的 Sign，其 verdict 已真机产出）。

**init.sh 安装矩阵（吸收 #12）：**

| `--tool` | 注册文件落点 | skills/agents symlink 落点 | 说明 |
|---|---|---|---|
| `claude` | `CLAUDE.md`（project）/`~/.claude/CLAUDE.md`（global） | `.claude/skills`·`.claude/agents` | Claude 别名 `${CLAUDE_PLUGIN_ROOT}` 兼容 |
| `opencode`/`trae`/`cursor`/`copilot` | `AGENTS.md` | 各 CLI 约定目录（**day-1 只对 Claude 实跑，其余干跑/静态审**，见 §9④） |

`--level`∈{project,global}；冲突：默认**不覆盖**已存在文件、报错退出，`--force` 才覆盖并先备份 `<file>.bak.<ts>`；`--dry-run` 只打印计划、零文件系统写；`--uninstall` 逆操作（删 symlink、还原备份）；幂等：重复跑同参数不产生重复 symlink。

## 7. 卡点 / blocked deps

**本地能立刻产出草稿**：4 原子 SKILL.md + references、`workflows/` 材料仓（含 isclose/sign 卡片+symlink）、`init.sh` 编写 + `bash -n`/`--dry-run` 自测、AGENTS.md skills 更新 + `check_manifest_sync`、bureau note/compile（落 proposed）、codex/audit-fix 两门、改动简表。

**卡在外部/需批**：
- ① **开工决策门**（§9）：原子化方向 + 授权 supersede canonical + 脚本边界口径 + archive 白名单 + init.sh CLI 面 + 是否扩 validator/perf——**全部需用户先拍板**，durable 才开工。
- ② **canonical 晋级**：只能到 proposed，promote 须用户 `bureau:review`。
- ③ **init.sh 真·跨 CLI 扇出验证**：opencode/trae/cursor/copilot 的 symlink 解析 + 配置落点需对应 CLI 环境；本地只对 Claude 分支实跑（fixture），其余干跑/静态审，真机多 CLI 验证挂起。
- ④ **shellcheck 本地缺**：需 `brew install`（网络+确认）或以替代门标注。
- ⑤ **archive 扩面**：仅 isclose(PASS)/sign(性能 FAIL) 合格；Equal 按 hard-constraint #1 已作废不收；扩更多案例需新真机验收。
- ⑥ **task-prompts.md** 引用 P1 subagent（未建）→ 前向声明，需与 P1 排序协调。

## 8. 验收标准（acceptance）

1. 4 个原子 skill 各有合规 SKILL.md（name+description frontmatter、progressive disclosure 薄壳+references）；**grep 范围限这 4 个新 skill**（吸收 #10：不含 acc-spec，其 `1e-3` 是写进 spec 的 `(推断)` 默认值、属 spec-population 非判定阈值）：**无硬编码 pass/fail 判定阈值**；脚本引用一律 `${OPRUNWAY_PLUGIN_ROOT}/acc-common/*`（Claude 别名兼容）、无脚本 copy 进 skill。
2. precision/perf/casegen 三 skill **明标脚本能力边界**（validator 仅 exact_mismatch/max_rel_err、perf_compare 仅 ratio+scope、gen_cases 仅注册算子），**未声称脚本做不到的判定**（MERE/MARE/仿真图例外均标为目标态/路线图）。
3. `plugin/workflows/` 下 development-guide + task-prompts + archive_ops 齐备且**无 SKILL.md**；archive_ops 仅收 isclose/sign，**卡片区分 verdict**（isclose=PASS、sign=精度过·性能 FAIL），且为 symlink（`ls -l` 见箭头）。
4. `init.sh` 过 `bash -n` + `cc-suite:audit-fix` 干净（shellcheck 装了则过、没装则标注）；`--dry-run` 零文件系统写；`--force`/`--uninstall` 行为符合安装矩阵；grep init.sh **无 sed 绝对/私有路径**、**无 external_directory:allow**；Claude 分支 fixture 实跑 symlink 正确 + 断链检测生效。
5. `plugin/AGENTS.md` skills=6，`check_manifest_sync.py`→STATUS: SYNCED；**不声称**它校验了 plugin.json skills/CLAUDE.md 派生（#7）。
6. component-breakdown supersede 以 bureau note/compile（proposed）记录、**非** hand-edit；canonical 晋级留用户 review。
7. `archive_ops/README.md` 定义 **case.md 最小字段 schema**（`op` / `taskdoc` / `pr`(或 from-scratch 标记) / `verdict`(pass|precision-pass·perf-fail|…) / `evidence_path` / `real_machine`(a3/a5) / `caveat`）+ 入库校验清单（真机-verified 才收、verdict 如实标、symlink 可解析）。
8. 五门（决策/pre-bureau 文本/post-artifact 散文/代码/同步）均过并复述结论；`doc/oprunway-changes-brief.md` 已倒序追加。

## 9. 开放决策（全部前置为开工门；吸收 #9）

① **【核心·canon 冲突】** canonical component-breakdown 的 3-skill 枚举 vs 本 todo 4-atomic 切法——是否采纳原子化、并授权 bureau 门 supersede 该 canonical 页；敲定 `acc-spec`/`acc-runner` 保留为独立原子 skill（本 plan 取「保留＝共 6 原子能力 skill」）、旧名 `acc-npu-run`/`acc-perf-compare` 与新名 `acc-precision`/`acc-perf` 映射口径。
② **archive 白名单**：仅收真机-verified 且卡片如实标 verdict——**isclose=PASS、sign=精度过·性能 FAIL（非 PASS）**、Equal 作废不收（吸收 #8）。请确认。
③ **skill/脚本边界 + 是否在本 todo 扩脚本**（吸收 #3/#4）：默认 skill 只做方法论指针、不复制阈值/计算，且**诚实标注 validator/perf_compare/gen_cases 当前能力边界、不声称已能判 MERE/MARE / 仿真图例外 / 通用 rule-catalog generator**；扩 validator/perf/gen_cases 判定能力**默认列为独立后续 todo**（各带自己的代码+test 门）。是否改为在本 P2 内折叠扩展，请用户定。
④ **init.sh 首发 CLI 面**（吸收 #11/#12）：day-1 实际支持哪几个 CLI（本地只能验 Claude；opencode/trae/cursor/copilot 需目标环境）；真实 project/global 写入前是否要求显式确认（本 plan 取「要，且首发只 fixture 实跑」）。
⑤ **根变量口径**（吸收 #13）：init.sh 用 `OPRUNWAY_PLUGIN_ROOT` 主 + `${CLAUDE_PLUGIN_ROOT}` 别名；是否/何时把现有 skills/agent 的 `${CLAUDE_PLUGIN_ROOT}` 引用统一迁到中立变量（涉 proposed cross-cli 页、本 P2 默认不迁）。

（T9 发布形态、T11 外发授权本 todo 不涉，未列。）

---

## T4-P3-catlass-adapter — P3 catlass 验收 adapter（终版·据 codex 审计修订）

**codex 总判**：codex_ran_ok=true（codex 真跑成、本 plan 已经外审），总判 VERDICT=major-gaps。4 条 codex-json 顶层 severity=high + tail 里合计 5 条 high（#1 consistency / #2 feasibility-build / #3 feasibility-msprof符号 / #4 completeness-task↔PR / #5 completeness-schema / #6 risk-mock误当evidence）——**全部已在终版解决或正确 scope**：#1 绑 generated_harness、#2 加 staging（已实地核验 build.sh 机制佐证 finding 成立）、#3 device-kernel extern C + 运行后命中门、#4 provenance 字段+synthetic 标注+上游 gate、#5 matmul 专属 schema+兼容性先定、#6 mock 打 development grade + NON-ACCEPTANCE 标记。**无 high 未解**。12 medium + 4 low 亦全部采纳落进 files/steps/gates。终版另附「实地核验」一节，把 codex 的 feasibility 猜测坐实为已验证事实（arch 索引 example 集、build.sh target 机制、validator 单阈值现状）。

**可实施性**：no（分两层）。① 流程层：本 todo 处 init 阶段，按 CLAUDE.md #1「先抛方案经用户同意才落地」——终版需用户点头 + open_decisions 6 项（尤其 #1 mode 绑定、#2 staging 路径、#3 demo spec synthetic 定位、#4 ADR0005 暂缓）待拍板，未获准前不动手。② 能力层：一经批准，**本地可立即开工**全部脚手架（arch 探测/arch 索引 profile/matmul gen_cases+golden+materialize/双 runner 模板/CMake/staging 脚本/静态构建门/csv 解析+fixture/外部 baseline 校验+workflow 入口/catlass_mock 端到端跑穿三级门+单测），无需 NPU；**真机 NPU evidence + msprof 符号命中 + 真 GPU 基线 + 真正 acceptance 裁决**则硬阻塞于 ascend-a5(arch3510)+VPN+人工确认，以及换用真实 task↔PR-backed spec。

**残余风险**：1) **真机全部待验**：runner 能否在 bisheng/ccec + catlass 头下编成、`extern "C" __global__ __aicore__` 符号能否被 msprof `-k` 命中、Task Duration 实数、staging 注入 add_subdirectory 与 build.sh 的交互——Mac 上只能写+静态审，须 ascend-a5(3510)+VPN+人工确认才落实；`profile_hit_gate` 逻辑先备、真值未验。2) **acceptance 双阻塞**：demo spec 为 synthetic（无真实 task↔PR），且无真机 evidence → 本 todo 产出的一切「PASS」仅证明管路/门接通，非 NPU 验收；须后续换真实 task↔PR-backed spec + 真机 evidence 才有裁决意义。3) **精度口径未达 ADR0005 三层**：暂用 max_rel_err 单阈值，fp32 matmul 大 K 累积误差阈值需真机/任务书校准；三层扩展是 validator 级独立 todo。4) **GPU baseline 真数据缺位**：只定 schema+校验+fixture，真实 GPU 标杆由外部 Task 3 提供，未对真数据验。5) **staging 改 catlass 工作副本**：虽幂等+可回退+仅非提交 clone，仍是对第三方仓的 deploy 期改动，首跑须人工确认。6) **fp16(a2) 次要路径**阈值/ArchTag 差异未细化，聚焦主 fp32 路径。

**据 codex 修订**：逐条处理 20 条 codex issue（4 high + 12 medium + 4 low，其中 5 条被 codex 标 high）：

【HIGH，全部解决】
- #1 consistency（模式名脱离 canon taxonomy）→ 采纳：明确把 catlass adapter 绑到 canon `generated_harness` 模式，代码落 `harness_kind="generated_harness"` 字段 + doc 显式声明；`catlass`/`catlass_mock` 是实现标签（同 `mock`/`new_example` 既有惯例），canon 归属靠字段+prose 不含糊。
- #2 feasibility（独立 CMakeLists 不被 build.sh 纳入）→ 采纳（已实地核验 build.sh 只构建 examples 树内 target、且 example 集按 arch 索引）：新增 `stage_into_catlass.sh`，deploy 期把 harness 拷进 `repos/catlass/examples/<harness>/` 并幂等注入 `add_subdirectory`，再 `build.sh <harness>`；原计划「plugin 里 CMakeLists 被 build.sh 自动纳入」的假设作废，改为模板源+staging。
- #3 feasibility（host extern C 不改 device kernel profile 符号）→ 采纳：runner 改为在 **device kernel 入口** `extern "C" __global__ __aicore__` 钉死（basic_matmul_aclnn 范式），而非 host wrapper；且不预设符号可命中，增运行后 `profile_hit_gate()` 解析真 CSV 取实测 kernel 名。
- #4 completeness（漏最高优先级「task↔PR 对应」硬约束）→ 采纳并正确 scope：correspondence gate 归上游 acc-spec/fetch_source（`verify-spec-pr-correspondence` ADR 的落点），adapter 侧补 provenance 字段（spec.provenance/case_origin）+ demo spec 显式标 synthetic（CatlassBasicMatmul 是库 example、无真实 task_doc/PR）+ acceptance 显式阻塞。不在 adapter 里硬造假 PR 映射。
- #5 completeness（caseset/evidence 与固定 schema 兼容性未说明）→ 采纳：matmul 另写 case-plan+materialize（不套 elementwise broadcast）、manifest 改写 m/n/k、evidence 增 layout/provenance/artifact_manifest；确认 validator/validate_acceptance_state 的 precision/perf 字段 op-agnostic 仍兼容，先定 schema 再产物。
- #6 risk（mock 跑穿门被误当 NPU evidence）→ 采纳：mock 打 `evidence_grade=development`，validate_acceptance_state 对 mock 产物输出显式 `NON-ACCEPTANCE (mock evidence)`，run_workflow overall 写 `PASS(mock 管路·非 NPU 验收)`，final acceptance 阻塞到真 NPU evidence。

【MEDIUM，全部采纳】
- #7（「零字面硬编码」不可执行）→ 采纳：acceptance 表述改为「production 路径无默认/兜底硬编码 arch；白名单枚举 + 测试 fixture 允许字面值」。
- #8（arch 注入不足以切模板）→ 采纳：CATLASS_PROFILE 按 arch 索引，决定 example/源码/ArchTag/dtype/runner（已核验 3510→43 fp32/Ascend950、2201→00 fp16/AtlasA2）。
- #9（CatlassBasicMatmul 指向不清）→ 采纳：pin 主目标 = ascend-a5 arch3510→`43_ascend950_basic_matmul` fp32 RowMajor；次 = a3 arch2201→`00_basic_matmul` fp16；repo commit 入 artifact_manifest。
- #10（GPU baseline 无 workflow 入口）→ 采纳：run_workflow 加 `--baseline`/env/spec 入口 + 优先级 + 复制进 baseline.json。
- #11（三套 baseline 语义混用）→ 采纳：spec 显式 `perf.baseline_source=gpu_external`；catlass 无 builtin-TBE 分母、故 run_catlass 不写 `_real_baseline`，perf 分母走外部 GPU（ADR0006 默认）。
- #12（精度未覆盖 ADR0005 三层）→ 采纳但 scope：validator 现仅 max_rel_err 单阈值，三层/MERE/MARE/RMSE/NaN-Inf 是 validator 级扩展、超本 adapter 范围；本 todo 用现有 numerical 路径 + 文档化 fp32 阈值来源，三层扩展列独立 todo（open_decisions #4 待确认暂缓）。
- #13（verify_catlass_build 声称验 msprof 命中）→ 采纳：拆静态构建门（Mac 可跑）+ 运行后 profile 命中门（解析真 CSV）。
- #14（run_on_catlass_npu 固化 /home/lys + 误删风险）→ 采纳：复用既有 `OPRUNWAY_REMOTE_DIR`+`_PATH_RE` 白名单，rm 限专属 run 子目录。
- #15（perf 只取中位、缺 timing 元数据）→ 采纳：warmup/iters/p50/p90/min/sync/clock-power/CANN 版本入 evidence.perf + 编排脚本（clock/power「若可得」）。
- #16（CSV 第 3 列解析脆弱）→ 采纳：按列名 `Task Duration(us)` 解析，fixture 覆盖坏 header/空行。
- #17（out.bin 固定路径 vs AscendOpTest 输出路径）→ 采纳：deliverable 明标 repo-native/OpRunway 自有流水线，不宣称 bridge 就绪（route A/B 延后）。
- #18（collect_artifacts 未列 provenance）→ 采纳：collect 落 artifact_manifest.json（commit/build cmd/CANN/bisheng/msprof 版本/runner hash/log+csv hash）。
- #19（case_id⊆性能用例方向不清）→ 采纳：写成可机检——全部性能用例必须有 baseline（缺→blocked）、extras 忽略并告警。

【LOW，全部采纳】
- #20（perf shape 死钉 1024³、缺 timeout/清理/降级）→ 采纳：shape 从 spec 读可配置、加 timeout/profile size budget/逐用例清理。
- #21（changes-brief 未记 blocked 真机状态）→ 采纳：条目显式写「本地 mock 已过/真机 evidence 未产/synthetic spec」。

【无 outright 驳回】：无 finding 判为错误。两处为「采纳但正确 scope」而非全量吞下：#4（correspondence gate 归上游、adapter 只补 provenance）与 #12（三层精度归 validator 级、本 todo 暂用单阈值）——均在 open_decisions 列出待用户确认，不算驳回。

### 终版 plan


# T4-P3 · catlass 验收 adapter（终版）

## goal
在 `acc-common` 里落成第一个 catlass repo-adapter，把 **CatlassBasicMatmul** 从「spec → NPU 精度+性能 evidence」打通。**明确定位**：这是 canon `repo-adapter.md` 三模式里的 **`generated_harness`**（我们自造 bin-IO 调用壳去包 catlass 自带 example 的 kernel），不是新造第 4 种模式；代码里以 `harness_kind="generated_harness"` 落字段承载。本 todo 只做 **OpRunway 自有流水线（repo-native，main() 全我们控）**，**不宣称 AscendOpTest bridge 就绪**（路线 A/B 留到真接框架时按 canon 重造）。本地先用 `catlass_mock` 端到端跑穿三级机器门以证明「管路接通」，真机 build/run/msprof 在 ascend-a5(arch 3510, fp32) 就绪后接入。catlass 机制仅作本仓执行后端、不升总规范（ADR0002）。

> ⚠ 重要边界（codex #4/#6）：`CatlassBasicMatmul` 是 **catlass 库自带 example**，不是有 task_doc+交付 PR 的社区算子任务。故本 todo 产出的 spec 是 **synthetic demo spec（无真实任务书↔PR 对应）**，只用来驱动/验证 adapter 管路，**不构成 acceptance 级裁决**。真正的验收裁决必须建立在「真机 NPU evidence + 有 verify 过的 task↔PR 对应的 spec」之上——这两个前提本 todo 都尚未满足，故 acceptance 结论一律 **BLOCKED-on-real-NPU / BLOCKED-on-real-provenance**。

## approach（对齐已 settle 的 canon，不重推）
- **ADR0009(canonical)**——一套泛化 workflow + 每仓一薄 adapter，仓差异是「数据」。故不 fork run_workflow，只在 `repo_adapter.py` 加 catlass 模式函数 + 用 `CATLASS_PROFILE` 字典承载差异；**且 profile 按 arch 索引**（见下）。
- **repo-adapter.md(canonical)** 的 7 方法 + **generated-harness-responsibilities.md(canonical)** 的 4 职责：沿用现有「每模式一函数、内部按阶段命名分段」写法（不趁机把 mock/new_example 重构成 ABC，避免超范围）。4 职责逐一落地：bin-IO shim / layout 字节契约 / 固定 seed 数据注入 golden 同源 / msprof kernel-only 双边同 timing_scope。
- **catlass-acceptance-mechanics.md(canonical/verified)**——`build.sh <example> -DCATLASS_ARCH`、CPU float32 golden、msprof op kernel-only Task Duration(us)；msTuner 是调优非验收、`Compare success.` 仅 smoke，均不作裁决依据。
- **ADR0002(canonical)**——精度=真 NPU out vs 我们的 numpy golden、性能=msprof kernel-only；catlass 自带对比只作 smoke。
- **catlass-to-aclnn-bridge.md**——主体 canonical（路线 A/B 设计当事实），但 2026-07-08「route_b 原型已删、真建时据本页重造」補记为 **proposed（未 settle）**，只作背景不载重。本 todo 走「复用 catlass 自带 example 工程 + 换入 bin-IO runner」最低风险路径，**不落真桥**。
- **ADR0006(proposed)**——timing_scope 必填、双边同 scope、kernel-only 默认、warmup/iters/median/p90/min policy、`perf_baseline_source` 默认 `gpu_external`。仅借用、载重前已标 proposed。
- **engineering-paradigm-trichotomy.md(proposed)**——仅借「catlass 走 generated_harness / 优先复用现成可运行壳」判断、不当事实。
- **verify-spec-pr-correspondence + task-spec-authoritative（均 proposed，2026-07-09 最新）**——最高优先级硬约束：验收前先验「任务书↔PR」对应；本 todo 的处置见 goal 的边界说明（correspondence gate 归上游 acc-spec/fetch_source，adapter 侧补 provenance 字段 + demo spec 显式标 synthetic + acceptance 阻塞）。

minimal-first：先 CatlassBasicMatmul 单算子跑穿再谈融合/其它 example。generated_harness 高风险 → runner 用固定模板 + 标注 op 专属边界；构建门拆成「静态构建门（可 Mac 上跑）」+「运行后 profile 命中门（真机跑后解析真实 OpBasicInfo.csv）」；真机跑前人工确认。

## canon 依据（附 trust tier）
- `repo-adapter.md` = **canonical**（7 方法、三模式风险序 → 当事实；catlass 明确落 `generated_harness`）。
- `generated-harness-responsibilities.md` = **canonical**（4 职责 → 当事实）。
- `catlass-acceptance-mechanics.md` = **canonical/verified**（build.sh/arch、CPU fp32 golden、msprof kernel-only；已对 repos/catlass 指纹核验 → 当事实）。
- `ADR0002` = **canonical**；`ADR0009` = **canonical**；`ADR0005` = **canonical**；`ADR0007`（deterministic validator）= **canonical**。
- `ADR0006` = **proposed**（timing policy、gpu_external 分母 → 载重前已标）。
- `catlass-to-aclnn-bridge.md` = 主体 **canonical**，2026-07-08 補记 = **proposed**（只作背景）。
- `engineering-paradigm-trichotomy.md` = **proposed**；`verify-spec-pr-correspondence` / `task-spec-authoritative` = **proposed**（2026-07-09，最高优先级、已据此加 provenance 边界）。
- `doc/oprunway-agent-system-design.md` §5 P3 六子任务 = 设计提案（非 canon），当「计划意图」非事实。
- **实地核验（本次 Read repos/catlass 得，记为 verified-by-inspection）**：`build.sh <target>` 只构建 examples 树内注册的 target；example 集**按 CATLASS_ARCH 索引**（2201→`00_basic_matmul` fp16/AtlasA2；3510→`43_ascend950_basic_matmul` **fp32**/Ascend950/TLA）；`validator.py` 现仅支持 exact / numerical(max_rel_err) 单阈值。

---

## files
1. `plugin/acc-common/repo_adapter.py` — **edit**：新增 `run_catlass`（真机）+ `run_catlass_mock`（本地）两函数，内部按 7 方法阶段命名分段；加 `_catlass_arch()`（运行时探测，白名单 `{2201,3510}`，无默认/无兜底硬编码）+ `_catlass_cfg()`（复用 new_example 的 `OPRUNWAY_REMOTE_DIR`/`_PATH_RE` 安全路径模式，**不写死 /home/lys**）+ **arch 索引的 `CATLASS_PROFILE`**（见 step1）；两函数各带 `harness_kind="generated_harness"`、`evidence_grade`（mock=`development`、真机=`acceptance_candidate`）；MODES 注册 `catlass` / `catlass_mock`。matmul **不复用** new_example 的 broadcast materialize，另写 matmul materialize。
2. `plugin/acc-common/catlass/oprunway_catlass_basic_matmul_950_runner.cpp` — **create**：arch 3510/fp32/Ascend950/TLA 版 bin-IO shim（对齐 `43_ascend950_basic_matmul`）。读 manifest 的 m,n,k + A/B bin → **device kernel 入口以 `extern "C" __global__ __aicore__` 钉死符号**（参照 `advanced/basic_matmul_aclnn` 范式，非 host-only wrapper）→ 写 C(m,n) out.bin。固定模板 + 注释标出唯一 op 专属边界（using 链 / launch 段）。
3. `plugin/acc-common/catlass/oprunway_catlass_basic_matmul_a2_runner.cpp` — **create**：arch 2201/fp16/AtlasA2 版（对齐 `00_basic_matmul`），de-risk/次要路径。
4. `plugin/acc-common/catlass/CMakeLists.txt` — **create**：harness 子目录 CMake，仅一行 `catlass_example_add_executable(<harness> matmul <runner>.cpp)`（复用 examples 作用域已定义的 macro + include_directories + link_libraries + arch 编译选项）；`add_executable` 相关**单独成行**防符号解析漂移。**此文件是模板源**，deploy 时拷进 `repos/catlass/examples/<harness>/`。
5. `plugin/acc-common/catlass/stage_into_catlass.sh` — **create**（新增，补 codex #2）：deploy 期把 `examples/<harness>/{runner.cpp,CMakeLists.txt}` 拷入 catlass 工作副本，并**幂等注入** `add_subdirectory(<harness>)`（带 sentinel 注释，可检测/可回退）到 `examples/CMakeLists.txt` 的 `foreach` 之后 —— 让 `build.sh <harness>` 能纳入构建。全程只改远端**非提交**的 catlass clone、cleanup 时移除注入块。
6. `plugin/acc-common/catlass/run_on_catlass_npu.sh` — **create**：真机编排（ascend-a5）：`stage_into_catlass.sh` → `scripts/build.sh <harness> -DCATLASS_ARCH=$ARCH` → 正确性跑出 out.bin → 逐 perf 用例 msprof op 采 kernel-only（warmup≥10、iters≥30 或方差收敛、median + p90/min、计时前后同步、记 clock/power「若可得」+ CANN/bisheng/msprof 版本）→ 写 perf_result.txt。hash-stamp 防 stale exe；**路径经 `OPRUNWAY_REMOTE_DIR` + 白名单，rm 只在专属 run 子目录内**；profile 目录设 size budget、逐用例清理、单用例 timeout。
7. `plugin/acc-common/catlass/verify_catlass_build.py` — **create**：**静态构建门**（Mac 可跑）——校验 `-DCATLASS_ARCH` 已注入且 ∈ 白名单、`add_subdirectory(<harness>)` 已注入、`add_executable` 单行、runner 含 `extern "C" __global__ __aicore__` 钉死符号声明、include 路径存在。**不**声称能证明 msprof 命中（那是运行后门）。
8. `plugin/acc-common/catlass_parse.py` — **create**：`parse_raw_log()`（哨兵计数、逐 case ok/FAIL、抗缺行）+ `parse_msprof_csv()`（**按列名 `Task Duration(us)` 解析** OpBasicInfo.csv，不写死第 3 列；排序取 median，附 p90/min）+ `profile_hit_gate()`（**运行后 profile 命中门**：解析真实 CSV，断言存在被测 kernel 行；符号未预知时取 CSV 里的实测 kernel 名并回填记录）。全部 fixture 单测、不依赖真机。
9. `plugin/acc-common/gen_cases.py` — **edit**：注册 `GOLDEN["CatlassBasicMatmul"]`（numpy fp32 `C=A.astype(f32)@B.astype(f32)`，golden_source 记「numpy f32 matmul，对齐 catlass `examples/common/golden/matmul.hpp` CPU f32 语义」）+ **matmul 专属 case-plan 分支**（A[m,k]/B[k,n]→C[m,n]，功能/精度小 shape + 性能大 shape，**shape 可配置**、非死钉 1024³）+ layout 元数据(全 RowMajor) + `case_origin`/provenance 字段。matmul 走独立 plan，不套用 elementwise broadcast。
10. `plugin/acc-common/specs/catlass_basic_matmul.spec.json` — **create**：`op=CatlassBasicMatmul`、`repo=catlass`、`harness_kind=generated_harness`、`verify_mode=numerical`、`precision.threshold`（fp32 matmul 的 max_rel_err 阈值 + `threshold_source` 说明）、`perf.baseline_source=gpu_external`（catlass **无 builtin-TBE 分母**）、`perf.target_ratio`、`arch` 不写死（运行时探测）、**`provenance` 显式标 synthetic（catlass 库 example，无 task_doc/PR）** + `acceptance_blocked_reason`。
11. `plugin/acc-common/perf_compare.py` — **edit**：新增 `load_external_baseline(path)`——校验外部 GPU 基线与 `_real_baseline` 同 schema（`source`/`scope=kernel_only`/`per_case:{case_id,us}`）、**scope 不符即 blocked（ADR0006）**、**规则可机检：全部性能用例必须有 baseline，缺 → blocked；extras 忽略并告警**、us 有限>0；文档化 GPU 基线契约字段（case_id/device/dtype/shape/attrs/timing_scope/warmup/iters/sync/statistic/unit/value/tool/clock-power/data_transfer_included，对齐 ADR0006）。
12. `plugin/acc-common/run_workflow.py` — **edit**（补 codex #10）：加 `--baseline <path>`（或 spec `perf.external_baseline_path` / env `OPRUNWAY_GPU_BASELINE`）入口，定义优先级（CLI > env > spec > 真机 `_real_baseline` > mock）、把外部基线复制进产物目录 `baseline.json`；**acceptance.json 记 `evidence_source`（mock/real）+ `evidence_grade`**，mock 产物的 overall 明确写为 `PASS(mock 管路验证·非 NPU 验收)`，绝不呈现为 NPU acceptance。
13. `plugin/acc-common/validate_acceptance_state.py` — **edit**（补 codex #6）：task2/task3 门读到 `evidence_grade=development`（mock）时，允许 STATUS PASSED 以证明「管路/门接通」，但**在输出里显式标 `NON-ACCEPTANCE (mock evidence)`**，使 mock 的 PASSED 不可被误读为 NPU 裁决。
14. `plugin/acc-common/test_catlass_adapter.py` — **create**：单测 + fixtures：arch 探测（拒非法/缺失、无默认落值）、msprof CSV 按列名解析（含坏 header/空行 fixture）、raw log→evidence 解析、外部 GPU 基线校验（scope 不符 blocked、缺用例 blocked）、matmul gen_cases/materialize 形状正确、`catlass_mock` 端到端跑穿 + `validate_acceptance_state` task1/2/3、defect 注入翻 FAIL、mock 产物带 `NON-ACCEPTANCE` 标记。
15. `plugin/acc-common/catlass/artifact_manifest`（由 collect 阶段产出，补 codex #18）：run_catlass 的 collect_artifacts 落 `artifact_manifest.json`——catlass repo commit、build 命令、CANN/bisheng/msprof 版本、runner hash、raw log/profile CSV 的 hash、arch、harness 名。
16. `doc/oprunway-changes-brief.md` — **edit**：倒序追加（大白话），**显式写「本地 catlass_mock 管路已过、真机 NPU evidence 未产出、demo spec 为 synthetic 无真实 task↔PR」**。

---

## steps
1. **arch 探测 + arch 索引 profile**：`_catlass_arch()` 读序 `OPRUNWAY_CATLASS_ARCH → work_dir/environment.json（若存在）→ 报错/AskUser`，白名单 `{2201,3510}`，**production 路径无任何默认/兜底 arch 字面量**（白名单集合与测试 fixture 里出现字面值允许）。`CATLASS_PROFILE = { "3510": {example:"43_ascend950_basic_matmul", src:"basic_matmul_tla.cpp", archtag:"Ascend950", dtype:"float32", runner:"..._950_runner.cpp", layouts:{A,B,C:RowMajor}}, "2201": {example:"00_basic_matmul", src:"basic_matmul.cpp", archtag:"AtlasA2", dtype:"float16", runner:"..._a2_runner.cpp", layouts:RowMajor} }`——**arch 决定 example/源码/ArchTag/dtype/runner**（补 codex #7/#8/#9），主目标 = 3510/fp32。`environment.json` 若不作为源则不依赖它（去掉未定义产物依赖，补 codex #12-feasibility）：默认只支持 env + 明确报错/AskUser，`environment.json` 作可选补充并注明 schema/producer。
2. **MatMul golden + case-plan + spec**：`gen_cases` 注册 `golden_catlass_basic_matmul`，加 **matmul 专属 plan 分支**（A[m,k]/B[k,n]→C[m,n]，功能/精度小 shape + 性能大 shape，shape 从 spec 读、可配置）；写 `catlass_basic_matmul.spec.json`（provenance=synthetic、baseline_source=gpu_external、fp32 threshold+source、arch 探测）。
3. **materialize_case 数据注入 + layout 字节契约**：`run_catlass` 内 matmul materialize（**不走 broadcast**）：固定 seed 造 A/B → 分两份 `X_logical`（喂 golden，逻辑形状）与 `X_bin`（喂 kernel，按声明 layout 摆物理字节）；basic_matmul 全 RowMajor 两者同，但代码显式分开以泛化 ColumnMajor（职责2/3）。manifest 行写 `case_id dtype m n k`（矩阵三维，非 out_shape 广播），排除 npy。
4. **runner cpp（双 arch 模板）+ CMake + 静态构建门**：写 950(fp32) 与 a2(fp16) 两 runner——device kernel 入口 `extern "C" __global__ __aicore__` 钉死符号（basic_matmul_aclnn 范式，**非 host wrapper**，补 codex #3）；CMakeLists 一行 `catlass_example_add_executable`；`verify_catlass_build.py` 落**静态**门（不声称验 msprof 命中）。
5. **staging + 真机编排**：`stage_into_catlass.sh` 拷 harness 进 catlass examples 树 + 幂等注入 `add_subdirectory`（补 codex #2）；`run_on_catlass_npu.sh`：build.sh 注入 `-DCATLASS_ARCH=$ARCH` → 跑 exe → 逐 perf 用例 msprof op 采 kernel-only（timing policy 全字段，补 codex #15）→ perf_result.txt；hash-stamp 防 stale；`OPRUNWAY_REMOTE_DIR` + 路径白名单 + rm 限专属子目录 + profile size budget/timeout/清理（补 codex #14/#20）。
6. **parsers（log→evidence + msprof + 命中门）**：`catlass_parse.py` 的 `parse_raw_log()` + `parse_msprof_csv()`（**按列名**解析，补 codex #16）+ `profile_hit_gate()`（**运行后**解析真 CSV 断言 kernel 行、回填实测符号，补 codex #13）；fixture 单测（含坏 header/空行）。
7. **run_catlass / run_catlass_mock 组装 7 阶段 + 注册**：`run_catlass_mock`（本地：kernel out=golden、可注入 defect、perf=确定性 mock、`evidence_grade=development`）；`run_catlass`（真机：stage→build→run→pull→parse→collect，`evidence_grade=acceptance_candidate`，**catlass 无 builtin-TBE 故不写 `_real_baseline`**，perf 分母走 gpu_external）；两函数内部注释标 discover/build/materialize_case/run_correctness/run_perf/parse_results/collect_artifacts 七段；collect 落 `artifact_manifest.json`（补 codex #18）；MODES 注册 `catlass`/`catlass_mock`。
8. **GPU/baseline schema 对齐 + workflow 入口**：`perf_compare.load_external_baseline()`（schema/scope/覆盖校验，补 codex #11/#19）；`run_workflow.py` 加 `--baseline`/env/spec 入口 + 优先级 + 复制进产物（补 codex #10）；写 sample GPU baseline fixture + 文档化契约。**真实 GPU 数字由外部(Task 3)提供，此处只定契约+校验**。
9. **本地端到端 + 机器门 + mock 非验收标记 + 回归**：`run_workflow.py catlass_basic_matmul.spec.json --mode catlass_mock` 跑穿；`validate_acceptance_state` task1/2/3 全 PASSED **但带 `NON-ACCEPTANCE (mock evidence)`**（补 codex #6）；defect 注入验证门能挡；`test_catlass_adapter.py` + 既有 `test_validate_acceptance_state.py` 全绿；grep 确认 production 路径无默认硬编码 arch。
10. **过门 + 简表 + 待真机**：代码全体过 `cc-suite:audit-fix`（复述发现/改动/风险）；spec/散文过 `codex exec` 审；`doc/oprunway-changes-brief.md` 倒序追加（含 mock/real 区分 + synthetic provenance，补 codex #21）；真机 build/run(ascend-a5 arch 3510) 与 generated_harness 首跑前**向用户人工确认**（高风险边界），待 VPN 开后接入。

---

## gates
- **代码门** → `cc-suite:audit-fix`（9 维审→修→验循环，复述发现/改动/风险）。
- **散文门** → spec 附带说明文本（threshold_source/provenance）、任何新设计 md、changes-brief 条目 → `codex exec` 定制审。
- **机器门** → `validate_acceptance_state.py` task1/2/3 对 `catlass_mock` 产物全 PASSED（且标 `NON-ACCEPTANCE`）+ 既有测试不回归。
- **构建门（拆两层，补 codex #13）** → 静态 `verify_catlass_build.py`（Mac 可跑，真机 build 前必过）+ 运行后 `profile_hit_gate()`（真机 msprof 后解析真 CSV 断命中）。
- **bureau 门** → 本 todo 不改 canon（是对已 settle canon 的实现）；真机跑后若浮出 durable 结论（basic_matmul layout 实测、msprof 实测符号名、路线取舍）→ `bureau:note→compile→review`，绝不手设 canonical。
- **副作用门** → 真机 deploy/build/run、`stage_into_catlass.sh` 注入 catlass 工作副本、generated_harness 首跑前人工确认（CLAUDE.md #1/#3）。
- **落点门** → doc 产出入 `doc/`、改动同步 changes-brief（CLAUDE.md #4）。
- **provenance 门（新增，补 codex #4）** → demo spec 显式标 synthetic、acceptance 阻塞；真正 acceptance 级裁决须上游（acc-spec/fetch_source）先 verify 过 task↔PR 对应（`verify-spec-pr-correspondence`），adapter 不越俎代庖、只补 provenance 字段。

## blocked_deps
**本地现在能做到**：全部 adapter/parser/gen_cases/spec/双 runner cpp/CMake/staging 脚本/编排脚本/静态构建门/GPU 基线校验/workflow 入口代码可写全；`catlass_mock` 可本地端到端跑穿三级机器门（无 NPU）；所有 parser 用 fixture 单测；arch 探测/无默认硬编码/matmul 形状可静态验证。
**卡真机的部分**：① runner cpp 需 bisheng/ccec + catlass 头文件才能 compile，Mac 只能写+审、不能编；② 真实 build+run 必须 ascend-a5 真 950(arch 3510, fp32) + 用户开 VPN，才产真 evidence/真 msprof；③ **msprof 实测符号名 / profile 命中 / Task Duration 实数**须真机验证（`profile_hit_gate` 逻辑先备）；④ GPU 标杆真实数字由外部提供，现只能定 schema+校验+fixture；⑤ 路线 A/B 真桥依赖真机+框架接入，本 todo 只保留 canon 设计、不落真桥；⑥ **acceptance 级裁决**双阻塞：既缺真机 NPU evidence，又缺 verify 过的真实 task↔PR spec（本 demo spec 为 synthetic）。

## acceptance
1. `repo_adapter.py` 出现 `catlass` + `catlass_mock` 两模式，函数内按 7 方法阶段分段，`harness_kind="generated_harness"` + `evidence_grade` 落字段，**CATLASS_PROFILE 按 arch 索引**（3510→43/fp32、2201→00/fp16），MODES 已注册。
2. `run_workflow.py --mode catlass_mock` 对 CatlassBasicMatmul 端到端产 caseset/evidence/verdict/baseline/perf_report/acceptance 全套，`validate_acceptance_state.py` task1/2/3 均 PASSED **且明确带 `NON-ACCEPTANCE (mock evidence)`**；defect 注入时门/裁决翻 FAIL；acceptance.json 记 `evidence_source=mock` 且 overall = `PASS(mock 管路·非 NPU 验收)`。
3. production 代码路径 grep 无默认/兜底 arch 字面（白名单枚举 + 测试 fixture 允许出现 3510/2201）——**表述已按 codex #7 修正为「无默认/兜底」而非「零字面」**。
4. `catlass_parse` 的 log→evidence 与 **按列名** msprof CSV 解析各有 fixture 单测通过（含坏 header/空行），抗坏输入不崩；`profile_hit_gate` 逻辑就绪（真机验证前标 pending）。
5. `perf_compare.load_external_baseline` 能校验外部 GPU 基线 schema、scope 不符 blocked、性能用例未全覆盖 blocked、extras 告警；`run_workflow` 有 `--baseline`/env/spec 入口且优先级明确。
6. matmul gen_cases/materialize 形状正确（A[m,k]/B[k,n]→C[m,n]，非 broadcast），shape 可配置；spec provenance 标 synthetic + acceptance_blocked_reason。
7. `stage_into_catlass.sh` 幂等注入可检测/可回退；`verify_catlass_build.py` 静态门通过；collect 落 `artifact_manifest.json`（commit/build cmd/版本/hash）。
8. `test_catlass_adapter.py` 与既有测试全绿；全体代码过 `cc-suite:audit-fix`、散文过 `codex exec`；changes-brief 已追加且写清「mock 已过/真机未产/synthetic spec」。
9. 真机 build/run 与 generated_harness 步骤已写全并**明确标注「待 ascend-a5+VPN+人工确认」**（诚实区分本地已达 vs 真机待验）；文档不宣称 AscendOpTest bridge 就绪。

## open_decisions
1. **canon mode 绑定确认**：catlass adapter 归 canon `generated_harness`（已在代码 `harness_kind` + 本 doc 落定），请用户点头此 taxonomy 绑定（codex #1）。
2. **最小闭环构建路径**：推荐「stage 进 catlass examples 树 + 幂等注入 `add_subdirectory` + `build.sh <harness>`」（复用 catlass 链库/arch 选项，最低风险）；备选「out-of-tree 独立 cmake」（更重、须复刻 ASC 工具链）。需拍板走 staging（codex #2 的两个候选之一）。
3. **demo spec 的 synthetic 定位**：`CatlassBasicMatmul` 无真实 task_doc↔PR，spec 标 synthetic、acceptance 阻塞、只验管路——请确认接受此定位（codex #4/#6）；若用户希望改用某个有真实 task↔PR 的 catlass 相关算子作首例，则先走上游 acc-spec verify 对应再回本 todo。
4. **ADR0005 三层精度口径暂缓**：`validator.py` 现仅 max_rel_err 单阈值；MERE/MARE/RMSE/NaN-Inf/小值绝对误差/三层放行是 **validator 级扩展**，本 adapter todo 先用现有 numerical 路径 + 文档化 fp32 阈值来源，三层扩展列为独立 validator todo。需确认此暂缓（codex #12）。
5. **7 方法是否现在抽 ABC**：推荐保持「每模式一函数、内部分段」以 minimal-first，正式接口化列后续。需确认暂缓（动接口即动架构）。
6. **首建平台**：主目标 = ascend-a5 arch 3510/fp32（任务书目标平台）；a2/2201/fp16 作 de-risk。真机可用时先跑哪条、以及路线 B(冒牌 exe) vs A(封 aclnn) 首建顺序，均阻塞于真机+人工确认，实施阶段再拍。


---

## T5-precision-dual-standard — 精度口径：两套并列平台标准（AscendOpTest 默认阈值 + 生态 MERE/MARE）+ 三层 pass（ADR 0005 命名）+ PASSED_WITH_RISK，标准由 spec 显式声明、误差分布采集层复算、validator 纯算术判

**codex 总判**：codex 真跑成（codex_ran_ok=true），VERDICT=major-gaps，6 条 high + 8 medium + 3 low。我已逐条核对源码坐实其技术判断（AscendOpTest default_acc 结构、denom 差异、命名偏差、退出码潜伏 bug 均属实），17 条全部吸收（#4/#12 部分收窄并说明理由，无整条驳回）。修订后**已无未解 high**：6 条 high（迁移/结构化阈值/denom 复刻/experimental fallback/目录分流/overall 优先级）均在终版 plan 落到具体设计。残留仅为门前用户决策与真机/真任务书留桩，非 plan 缺陷。

**可实施性**：基本可以，但需先冻结两个门前决策：① 误差复算落点（推荐 repo_adapter 采集层复算 / validator 纯算术判）；② PASSED_WITH_RISK 退出码=2 + requires_human_cp 语义。这两点用户点头后，本地即可全量落地（三标准 SSOT + 四文件改造 + 门与 28 单测同步 + 4 算子 mock 端到端），无需 NPU/VPN；真机 compare.py bool cross-check 与真任务书阈值校准留桩、不卡主体。

**残余风险**：1) 误差复算落点（repo_adapter vs validator）是架构级门前决策，用户若坚持 validator 自复算需返工（已冻结、点头前不动手）。2) ecosystem MERE/MARE 数值来自 proposed 页、未 settle——本轮只实现+打 NOT_SETTLED，真值待首份真任务书 + bureau 提升；ATK 双标杆 fallback 本轮 out-of-scope。3) 我方复算 bad_count/MERE/MARE 判定 == 真 compare.py bool 的一致性未在真机对拍（留桩、需 NPU+VPN）；掩码复刻虽逐行照 compare.py，仍可能有 dtype 边界/replace_inf 细节差。4) 阈值『先默认后校准』——默认值先落、最终数值待真任务书，期间 acceptance 判定用的是推断阈值。5) 28 单测 fixtures 结构化改造可能牵出机器门其它隐含契约，需回归确认不误挡合法 fail。

**据 codex 修订**：逐条处理 codex 17 条（6 high + 8 medium + 3 low），无整条驳回，2 条部分收窄（#4/#12）并说明。

HIGH：
- #1（迁移策略）ACCEPT：现 spec 用 `precision.oracle`+scalar `threshold`（已核 sign/neg/isclose/equal 四份），plan 原文直接换 `standard` 会断旧 spec + acc-spec。改为**保留 oracle+threshold(digest)、新增 standard/tolerance_policy_id/policy**，pipeline 缺 standard 时按 oracle+verify_mode 映射；acc-spec skill+schema+docs+tests 列为 companion 必改。
- #2（tolerance+error_rate 非单标量、会丢坏点率门）ACCEPT：证实 `default_acc[dtype]=[tolerance,error_rate,legacy]`、error_rate 是第 2 位且逐 dtype 变。阈值结构化为 `policy{tolerance,error_rate}`；机器门三处一致改为比 `tolerance_policy_id`+结构化 policy（保留标量 digest 向后兼容）。
- #3（AscendOpTest denom max(|e|,|o|)+1e-9 ≠ MERE |g|+1e-7）ACCEPT：读 compare.py 证实（`minimum=1e-9`、maxmin=max、共用 tolerance、|e|≥1 rel/else abs）。`compute_metrics` 三标准**分开实现**，AscendOpTest 单独复刻逐项掩码（inf/NaN/abs/rel/边界），不复用 MERE 公式。
- #4（experimental 漏 ATK 双标杆 fallback）ACCEPT（收窄）：ecosystem 页确有 ATK fallback，但为 proposed、无真 SPMV spec、ATK 仓未必在本地。采纳 codex 给的第二选项——**experimental 端到端标 out-of-scope**、`ecosystem_mere_mare` 单标杆不过记 `needs_review`（不自动 fail 误判），ATK fallback 待真任务书再实现。驳回「现在就实现 fallback」是因无真 spec + 无标杆数据、属空想镀金。
- #5（按目录分流不可落地）ACCEPT：现 spec 无任务书路径/experimental 目录/仓内落点字段。改为**标准由 `precision.standard` 显式声明**（acc-spec 据任务书目录/引用填），pipeline 绝不从缺失信息推断；plan 标题与 approach 已去掉「自动目录分流」。
- #6（overall 优先级 risk 与 uncertain 冲突）ACCEPT：确认原 plan「有 risk 无 fail→passed_with_risk、有 uncertain→needs_review」会让 risk 掩盖 uncertain。改优先级：`contract/fail > blocked > needs_review(uncertain) > passed_with_risk > pass`。

MEDIUM：
- #7（三层命名摇摆 + 非 catlass smoke 未定义）ACCEPT：改用 ADR 0005 canonical 名 `catlass_compare_pass/standard_profile_pass/acceptance_precision_pass`；new_example 无仓内 catlass smoke → `catlass_compare_pass=na`+reason（不改 canonical、不动 canon）。
- #8（『宽于平台底线』异构指标不能比阈值大小）ACCEPT：spec 可选带独立 `acceptance_policy`，validator **同时判 acceptance_policy 与 platform(standard)**，仅当 acceptance pass & standard fail 才标 risk；缺 acceptance_policy 时继承 standard、不可能出 risk。
- #9（threshold_for 用输入 dtype 会错判 bool/int8→int32 输出）ACCEPT：`compare_dtype` 按 **golden/输出 dtype** 解析。
- #10（evidence 缺 tolerance_policy_id/来源/hash/not-settled/oracle_source）ACCEPT：evidence.precision 补齐结构化 policy + tolerance_policy_id + oracle_source + not_settled + provenance。
- #11（常量表不完整、uint8 error_rate=0.01）ACCEPT：SSOT **完整快照** 15 个 dtype（含 bool/uint8/uint32/float64/complex/int8/int16），uint8/uint32 error_rate=0.01 照抄；不支持 compute 的 dtype fail-fast。
- #12（float64 cast 对 bf16/fp8/complex/超大数组不兼容/爆内存）ACCEPT（收窄）：声明 SUPPORTED_COMPUTE_DTYPES 支持矩阵 + 未支持 fail-fast。**驳回 chunk 分块**——现精度维用例极小（≤16 元素 + 4×5 广播，1024×1024 只在性能维、无精度判），分块属过度工程，标 deferred。
- #13（门『基本不动』错，结构化阈值/三层/risk 都影响门与 fixtures）ACCEPT：明确把 `validate_acceptance_state.py` 门契约 + 28 单测列为**必改**（policy 三处一致 + 新字段校验），删掉原 plan「大概率零改动」表述。
- #14（PASSED_WITH_RISK 非零退出被 CI 当普通 fail 跳过人工 CP）ACCEPT：另发现**潜伏 bug**——现 `run_workflow` 用 `overall.startswith("PASS")` 定退出码，`PASSED_WITH_RISK` 会被误判为 0 干净退出。改枚举退出码 0/2/1 + `requires_human_cp=true` 独立标志。
- #15（复算落点未定但步骤已按 repo_adapter 实施）ACCEPT：升为**门前冻结决策**（open_decision #1 + blocked_deps），用户未点头不动手。

LOW：
- #16（门命令不可复现）ACCEPT：gates 段写出可执行命令 + audit-fix 不可用时 codex exec fallback。
- #17（defect 只改 1 元素、大数组仍 pass 不稳）ACCEPT：单测按 `floor(n*error_rate)+1` 注入坏点。

### 终版 plan

## T5 · 精度口径升级（终版 plan · 已吸收 codex 17 条 audit）

### Goal
把现在过度简化的「单标量 threshold + exact_mismatch/max_rel_err」精度判定，升级为 **ADR 0005 canonical 的三层口径落地**：
- **平台层两套并列标准**：`ascendoptest_default`（逐 dtype `{tolerance, error_rate}`、坏点占比门）与 `ecosystem_mere_mare`（MERE 平均/MARE 最大、`MERE<Th 且 MARE<10×Th`、Th 逐 dtype `2^-k`，**proposed / NOT_SETTLED**）；bool 输出走 `exact`。
- **三层 pass 同出**（canonical 字段名）：`catlass_compare_pass` / `standard_profile_pass` / `acceptance_precision_pass`；**放行只看 `acceptance_precision_pass`**；任务书宽于平台底线（acceptance 过而 standard 不过）→ `PASSED_WITH_RISK` + `requires_human_cp` + 独立退出码，走人工 CP。
- **标准由 spec 显式声明**（`precision.standard`，acc-spec 据任务书目录/引用填），pipeline **绝不从缺失的任务书路径/目录去猜**。
- AscendOpTest 概念上**只出 bool**；误差分布（bad_count/mere/mare/exact_mismatch + numel）由**采集层（repo_adapter，有 numpy）确定性复算**写进 evidence；judge（比阈值）在 **validator 用纯算术**、validator 保持 stdlib-only。全程本地可写 + mock/numpy 自测；真机 + 真 `compare.py` bool cross-check 留桩。

### Approach（对齐 canon、不重推）
1. **ADR 0005（canonical → 当事实）**：三层口径非三选一、三 pass 同出、放行看 acceptance、宽于底线→PASSED_WITH_RISK+人工 CP。本 todo 只做「落地实现」，**用 canonical 三字段名**（不再自造 smoke/standard/acceptance）。
2. **AscendOpTest 阈值页（canonical）+ 本地 `repos/AscendOpTest/compare/compare/{accuracy_config.py,compare.py}`（verified 指纹源）**：默认判据是 `{tolerance, error_rate}` 结构（**不是单标量**）、坏点占比门；相对误差分母 `max(|expect|,|output|)+1e-9`、`|expect|≥1` 用相对/`<1` 用绝对/共用同一 tolerance、`inf→finfo.max`、`NaN==NaN` 过；仅当 `bad_count > numel*error_rate` 才整体 fail。工具只出 bool、分布我方复算——按此**逐 dtype 精确复刻掩码**，与 MERE 公式**分开实现**。
3. **生态 MERE/MARE 页 + ADR 0008（均 proposed → 非事实、未 settle）**：MERE=平均、MARE=最大（**务必不对调**），分母 `|golden|+1e-7`，`MERE<Th 且 MARE<10×Th`，Th 表 `2^-10/2^-7/2^-13/2^-11/2^-3/2^-2`。全部落 `NOT_SETTLED` 常量标 + 顶部 provenance 注释；**其单标杆失败时的 ATK 双标杆 fallback 本轮不实现**，`ecosystem_mere_mare` 单标杆不过 → 记 `needs_review`（不自动终判 fail），端到端标 out-of-scope，等真 SPMV/Trsm 任务书 + bureau 提升。
4. **ADR 0007（canonical）**：判定只从 validator 出、agent 不宣告 → 「误差分布复算」放采集层（量误差、非判 pass，与现有 `_metric` 一脉相承），judge 放 validator 纯算术，validator 仍 stdlib-only。
5. **契约页（canonical）**：用 `tolerance_policy_id` 承载口径 + 结构化 `policy`；阈值「待首份真任务书校准、勿空想镀金」。
6. **机器门（verified → 可用、载重前复核）**：逐 case 三处一致（spec/caseset/evidence）**升级为 `tolerance_policy_id` + 结构化 policy 一致**（保留标量 digest 作向后兼容），门逻辑与 28 单测**必须同步改**（非「零改动」）。
7. **迁移不破坏旧 spec**：**保留** `precision.oracle` + `precision.threshold`（标量 digest），**新增** `precision.standard` + `precision.tolerance_policy_id` + 结构化 `precision.policy`（+ 可选 `precision.acceptance_policy`）；pipeline 当 `standard` 缺失时按 `oracle`+`verify_mode` 映射（`ascendoptest`+exact→`exact`；`ascendoptest`+numerical→`ascendoptest_default`），旧 spec 仍能跑。acc-spec skill/schema/docs 同步产新字段。

### Canon 依据（逐页 + tier）
- `0005-precision-three-layer.md`（**canonical**）：三层非三选一、放行看 acceptance、宽于底线→PASSED_WITH_RISK+人工 CP；字段名 `catlass_compare_pass/standard_profile_pass/acceptance_precision_pass`。
- `ascendoptest-precision-thresholds.md`（**canonical**）：默认阈值=平台层实体、`{tolerance,error_rate}`、只出 bool、分布须我方复算、逐 dtype 常量以本地 accuracy_config.py 为准。
- `ecosystem-precision-standard.md`（**proposed → 非事实**）：MERE=平均/MARE=最大、10× 规则、Th 表、单/双标杆（含 ATK fallback）；实现但打 NOT_SETTLED。
- `0008-reuse-ascendoptest.md`（**proposed → 非事实**）：复用 compare.py+accuracy_config、我方供 expect_func、性能另算；精度复用照做、标未 settle。
- `0007-deterministic-validator.md`（**canonical**）：pass/fail 只从 validator、agent 不宣告。
- `acceptance-contract-evidence-chain.md`（**canonical**）：`tolerance_policy_id`/`oracle_source` 字段、阈值待真任务书校准。
- `task3-state-machine.md`（**canonical 4 核心态；PASSED_WITH_RISK 为边角态、需人工 CP**）。
- `machine-verifiable-acceptance-gate.md`（**verified → 载重前复核**）：三处一致、防子集/放宽/混 e2e、28 单测。
- 代码现状（事实）：`validator/gen_cases/repo_adapter` 现为单标量 threshold + exact_mismatch/max_rel_err（denom `|g|+1e-7`）；specs 用 `oracle`+scalar `threshold`+`verify_mode`；gate + run_workflow 按标量 threshold 三处一致，`run_workflow` 以 `overall.startswith("PASS")` 定退出码。

### Files
| 文件 | 动作 | 目的 |
|---|---|---|
| `plugin/acc-common/precision_policy.py` | **create** | 三标准 SSOT：`ASCENDOPTEST_DEFAULT`（**完整**快照 accuracy_config `default_acc`：float/float32/float64/int32/int64/float16/bfloat16/hfloat32/bool/uint8/uint32/int8/int16/complex64/complex128，含 `[tolerance,error_rate,legacy]` + provenance 注释 + 内容 hash + `EPS=1e-9`/掩码语义常量）、`ECOSYSTEM_MERE_MARE`（Th 表 2^-k + `max_ratio=10` + `EPS=1e-7` + `NOT_SETTLED=True` + status=proposed）、`EXACT`。`select_standard(spec)`（读 `precision.standard`，缺失按 oracle+verify_mode 映射）、`compare_dtype(case)`（按 **golden/输出 dtype** 解析、非输入 dtype）、`tolerance_policy_id(standard,dtype)`、`threshold_for(standard,dtype)→policy dict`（不支持 dtype **fail-fast**）、`compute_metrics(out,golden,standard,dtype)`（numpy：ascendoptest 复刻掩码出 `{bad_count,numel,max_abs_err,max_rel_err}`；mere_mare 出 `{mere,mare,numel}`；exact 出 `{exact_mismatch,numel}`）。judge 逻辑放 validator（见下）。SUPPORTED_COMPUTE_DTYPES 声明支持矩阵。 |
| `plugin/acc-common/gen_cases.py` | **edit** | per-case 阈值改 `precision_policy.threshold_for(standard, golden_dtype)`（不再全局标量）；`expected` 写 `{standard, tolerance_policy_id, policy{...}, threshold(=digest 标量,向后兼容), acceptance_policy?, verify_mode, golden_source, golden_path}`；扩 `_DTYPES` 仅在有 golden 支持时加，未支持 fail-fast。 |
| `plugin/acc-common/repo_adapter.py` | **edit** | `_metric`→`precision_policy.compute_metrics`；`evidence.precision={standard,tolerance_policy_id,policy,threshold(digest),oracle_source,not_settled,metrics{...},golden_path,out_path}`（仍只采集不判定）；`new_example` 加 `ascendoptest_bool` cross-check 占位（真机接 compare.py 时填、现 `None`+注释『待 NPU』）；mock 路径按注入 defect 让分布真实变化。 |
| `plugin/acc-common/validator.py` | **edit** | 新增 stdlib 纯算术 `judge_ascendoptest(policy,metrics)`（`bad_count<=numel*error_rate`）、`judge_mere_mare`（`mere<Th 且 mare<max_ratio*Th`）、`judge_exact`（`exact_mismatch<=0`）；`_judge_precision` 按 `case.standard` 分流。per_case 同出 `catlass_compare_pass`（new_example→`na`+reason）/`standard_profile_pass`（平台标准）/`acceptance_precision_pass`（= 任务书目标；缺 `acceptance_policy` 时继承 standard）。**放行只看 acceptance**；acceptance 过 & standard 不过→该 case `risk=true`；`ecosystem_mere_mare` 单标杆不过→`uncertain`（不自动 fail）。三处一致校验升级为 `tolerance_policy_id`+policy 一致。overall 优先级：`contract_problems/fail` > `blocked` > `uncertain(needs_review)` > `passed_with_risk` > `pass`。 |
| `plugin/acc-common/run_workflow.py` | **edit** | overall 归口加 `PASSED_WITH_RISK`；**修复退出码潜伏 bug**（现 `overall.startswith("PASS")` 会把 `PASSED_WITH_RISK` 误判为 0 退出）：改枚举——`0`=干净 PASS/PASS(无性能要求)、`2`=PASSED_WITH_RISK（`requires_human_cp=true`、CI 挂起转人工、非自动合并非自动失败）、`1`=其余（fail/blocked/needs_review）。`acceptance.json` 落三层 pass 明细 + risk 说明 + `requires_human_cp`。 |
| `plugin/acc-common/validate_acceptance_state.py` | **edit（非零改动）** | 三处一致门升级：`gate_task2` 由「标量 threshold 相等」改为「`tolerance_policy_id` + 结构化 `policy` 三处一致」（保留 threshold digest 校验作向后兼容）；`gate_task1` 校验 `expected.standard/tolerance_policy_id/policy` 必填；容忍 evidence 新增字段但不放过缺 policy。 |
| `plugin/acc-common/test_validate_acceptance_state.py` | **edit** | 28 单测同步：fixtures 换结构化 policy；补「policy 三处不一致→FAILED」「缺 tolerance_policy_id→FAILED」；证 dtype 化 + 三层 pass + risk 状态不破坏完整性门。 |
| `plugin/acc-common/specs/sign.spec.json` | **edit** | `precision` 加 `standard="ascendoptest_default"` + `tolerance_policy_id` + `policy`；保留 `oracle`+`threshold` digest。 |
| `plugin/acc-common/specs/neg.spec.json` | **edit** | 同 sign；沿用 `task_pr_gaps`。 |
| `plugin/acc-common/specs/isclose.spec.json` | **edit** | `standard="exact"` + `tolerance_policy_id="exact"`（`threshold=0` 保持）。 |
| `plugin/acc-common/specs/equal.spec.json` | **edit** | 同 isclose。 |
| `plugin/acc-common/test_precision_policy.py` | **create** | 两标准判定单测（见 steps 8）。 |
| acc-spec skill + 其 schema/docs（`plugin/skills/…`）| **edit（companion）** | 产 spec 时输出 `precision.standard`+`tolerance_policy_id`+`policy`（据任务书目录/引用选标准）；散文/skill 过 **codex exec** 审。 |
| `doc/oprunway-changes-brief.md` | **edit** | 倒序追加一两句大白话。 |

### Steps
1. **坐实常量（本地）**：Read `repos/AscendOpTest/compare/compare/accuracy_config.py`（**已确认**完整 15 个 dtype，`[tolerance,error_rate,legacy]`，`error_rate` 是**第 2 位**、逐 dtype 变：fp32=1e-4、fp16=1e-3、bf16=4e-3、uint8/uint32=0.01、int8/int16=1e-3、bool=0）+ `compare.py`（`compare_default`：`minimum=1e-9`、`maxmin=max(|expect|,|output|)+1e-9`、`|expect|≥1`→rtol/else atol/共用 tolerance、`bad>size*error_rate`才 fail、`inf→finfo.max`、`NaN==NaN` 过；bool 转 uint8 走 default）。把整表 + 掩码语义 + 内容 hash 抄进 SSOT 注释。全本地。
2. **建 `precision_policy.py`**：三标准表 + 路由(`select_standard`/`compare_dtype`) + `threshold_for`(未支持 dtype fail-fast) + `tolerance_policy_id` + `compute_metrics`(numpy，三标准分开：ascendoptest 复刻掩码算 `bad_count`；mere_mare 用 `|g|+1e-7`；exact 算 mismatch)。ECOSYSTEM 全打 NOT_SETTLED + provenance。**单测锚定 MERE/MARE 不对调**。
3. **gen_cases dtype 化**：per-case `threshold_for(standard, golden_dtype)`；`expected` 写全（standard/tolerance_policy_id/policy/threshold digest/acceptance_policy?/verify_mode/golden_*）；保住机器门三处一致。
4. **repo_adapter 采全分布**：`_metric→compute_metrics`；`evidence.precision` 记结构化 policy + metrics + oracle_source + not_settled + 路径；`new_example` 加 `ascendoptest_bool=None` cross-check 占位（注释『待 NPU』）；mock defect 让 metrics 真实变化。
5. **validator 三层判 + 放行口径**：`judge_*` 纯算术；`_judge_precision` 按 standard 分流；per_case 出 `catlass_compare_pass`(new_example→na+reason)/`standard_profile_pass`/`acceptance_precision_pass`（缺 acceptance_policy→继承 standard）；acceptance 过&standard 不过→`risk`；ecosystem 不过→`uncertain`。overall 优先级 fail/contract>blocked>needs_review>passed_with_risk>pass。三处一致改 policy 化。
6. **run_workflow 归口 PASSED_WITH_RISK**：枚举退出码(0/2/1)+`requires_human_cp`；`acceptance.json` 落三层 pass + risk。修 `startswith("PASS")` 潜伏 bug。
7. **改 4 份 spec + acc-spec companion**：sign/neg→ascendoptest_default、isclose/equal→exact；均标 threshold_source/provenance。acc-spec skill/schema/docs 产新字段（走 codex exec 散文审）。experimental(MERE/MARE) 无真 spec，仅单测覆盖判定逻辑。
8. **单测 + 自测**：`test_precision_policy.py` 覆盖——AscendOpTest 逐 dtype tol+error_rate、坏点边界(**注入 `floor(n*error_rate)+1` 个坏点**、非单点)、`|expect|≥1` rel/`<1` abs、inf/NaN、denom `max(|e|,|o|)+1e-9`；MERE=平均/MARE=最大不对调 + 10× + Th 表 + denom `|g|+1e-7`；exact；PASSED_WITH_RISK 路径（造 acceptance_policy 宽于 standard 的 spec/evidence）；不支持 dtype fail-fast。再跑 `run_workflow` mock 对 sign/isclose/equal/neg 各一遍（含 `--defect`）验端到端。
9. **回归机器门**：`python3 plugin/acc-common/test_validate_acceptance_state.py`（28 单测改后须全绿）+ 4 算子 mock run_workflow 后 `python3 plugin/acc-common/validate_acceptance_state.py --stage task1|task2|task3 --dir <out>` 须 STATUS: PASSED。门误挡→按「只管完整性不重判」最小修。
10. **过门与落账**：代码全量 `cc-suite:audit-fix`（9 维审→修→验）；散文 `codex exec` 审；`doc/oprunway-changes-brief.md` 追加；可选 `bureau:note`。动 canon（改 ADR 0005 命名 / 提升 ecosystem）一律 capture→compile→review，**不手改 canonical**。

### Gates（含可执行命令）
- **代码门（必过）**：`precision_policy.py` + validator/gen_cases/repo_adapter/run_workflow/validate_acceptance_state 改动 + 两测试文件 → `cc-suite:audit-fix`（9 维审→修→验循环），复述『发现/改了/剩余风险』。不可用时 fallback：`codex exec` 逐文件审 + 人工核 9 维清单。
- **散文门**：acc-spec skill/schema 文案 + 任何设计 md/bureau 拟写文本 → `codex exec` 定制审；changes-brief 一两句亦按 #5 先审后落。
- **机器门（出裁决必跑）**：`cd plugin/acc-common && python3 test_validate_acceptance_state.py`（28 全绿）+ `python3 test_precision_policy.py`（全绿）+ `python3 run_workflow.py specs/<op>.spec.json --mode mock --out /tmp/run_<op>` 后 `python3 validate_acceptance_state.py --stage task1|task2|task3 --dir /tmp/run_<op>`（STATUS: PASSED）对 4 算子。
- **bureau 门（仅动 canon 时）**：本 todo 默认**不写 canonical**——ecosystem/ADR 0008 维持 proposed；提升 ecosystem 或改 ADR 0005 命名须 capture→compile→review 人批，禁手改。可选 `bureau:note` 记进展（低权 logbook）。
- **落点门（CLAUDE.md #4）**：所有 doc 产出入 `doc/`，改动同步 changes-brief。

### Blocked / Deps
- **本地能立刻做（不卡）**：`precision_policy.py` 三标准全实现（AscendOpTest 常量 + 掩码语义从**本地已 clone** 的 accuracy_config.py + compare.py 直接坐实；ecosystem Th 表来自 proposed 页、打 NOT_SETTLED）；gen_cases/repo_adapter/validator/run_workflow/gate 全改造 + mock/numpy 单测 + 4 算子 mock 端到端 + 机器门 28 单测回归——全本地闭环，无需 NPU/VPN。
- **卡在门前用户决策（需先冻结）**：① **误差复算落点**（repo_adapter 复算 vs validator 自复算）——todo 字面『validator 自复算』，本 plan 提议采集层复算/validator 纯算术判（满足『我方确定性复算、非信工具 bool』+ 保 validator stdlib）；用户点头前不动手，否则架构返工。② **PASSED_WITH_RISK 退出码语义**（本 plan 定 `2`+`requires_human_cp`，需确认 CI 不当普通 fail 跳过人工 CP）。
- **卡在真机 NPU/真工具（留桩）**：③ 把真 `compare.py` bool 接进 `new_example` 作 cross-check（需算子跑 NPU + VPN）；④ 验证『我方复算 bad_count/MERE/MARE 判定 == 工具 bool』在真实数据一致（真机对拍）。
- **卡在真任务书（先默认后校准）**：⑤ 阈值『待首份真任务书校准』——默认值先落、最终数值等真实任务书；⑥ `ecosystem_mere_mare` 端到端 + ATK 双标杆 fallback 目前 out-of-scope，接真 SPMV/Trsm 任务书才有真 spec + 才实现 fallback（现单标杆不过记 needs_review、不误判 fail）。

### Acceptance
1. `precision_policy.py` 为三标准唯一 SSOT，单测证明：MERE=平均/MARE=最大**不对调**、`MERE<Th 且 MARE<10×Th`、Th 逐 dtype(2^-k) 正确、denom `|g|+1e-7`；AscendOpTest **逐 dtype `{tolerance,error_rate}`**、坏点门 `bad_count<=numel*error_rate`、`|e|≥1` rel/`<1` abs/共用 tol、denom `max(|e|,|o|)+1e-9`、inf/NaN 语义正确、error_rate 逐 dtype（fp16 1e-3、uint8 1e-2）；exact 判 `mismatch<=0`；未支持 dtype fail-fast；ECOSYSTEM 全标 NOT_SETTLED。
2. validator per_case 同出 `catlass_compare_pass`(new_example→na+reason)/`standard_profile_pass`/`acceptance_precision_pass`，**放行只看 acceptance**；构造 `acceptance_policy` 宽于 standard 的用例时 overall=`passed_with_risk`、run_workflow 落 `PASSED_WITH_RISK`+`requires_human_cp=true`+退出码 `2`；overall 优先级 fail/contract>blocked>needs_review>passed_with_risk>pass 经单测锚定。
3. gen_cases 按 **golden/输出 dtype** 解析 policy 写入 expected；机器门三处一致（`tolerance_policy_id`+policy）对 mock 产物仍 STATUS: PASSED；`test_validate_acceptance_state.py` 28 单测（已改）+ `test_precision_policy.py` 全绿；sign/isclose/equal/neg 四算子 mock 端到端裁决与预期一致（含 `--defect` 现 fail，defect 按 `floor(n*error_rate)+1` 注入不飘）。
4. AscendOpTest 概念上仍只出 bool；误差分布由采集层（repo_adapter）确定性复算落进 evidence（结构化 policy + metrics + oracle_source + not_settled）；validator 纯算术判、保持 stdlib-only。
5. 旧 spec（仅 oracle+scalar threshold）经映射仍能跑（向后兼容）；acc-spec 产新字段；`cc-suite:audit-fix` 无未决高危；changes-brief 已追加；ecosystem/ADR 0008 未被私自提为 canonical。

### Open Decisions（需用户拍板）
1. **误差复算落点**（门前决策，冻结后才动手）：采集层 repo_adapter 复算 / validator 自复算——本 plan 推荐前者（保 validator stdlib + 满足 ADR 0007）。
2. **PASSED_WITH_RISK 退出码 = 2 + requires_human_cp**：确认 CI 语义（挂起转人工、非自动合并、非自动失败）。
3. **ecosystem MERE/MARE 现在就实现但打 NOT_SETTLED 上线？** 数值来自 proposed 页，等真任务书 + bureau 提升。
4. **阈值『先默认后校准』节奏**确认。
5. **Standard B 端到端 out-of-scope 确认**：现只单测判定逻辑 + 单标杆不过记 needs_review；ATK 双标杆 fallback 待真 SPMV/Trsm 任务书才实现。
6. **catlass_compare 层对 new_example 算子标 `na`+reason**（不改 canonical 名，避免误导也避免动 canon）——确认这一处理，无需 bureau。

---

## T6-perf-smallshape — 性能：小 shape 例外通道 + timing_scope 纪律（据 codex 审计修订终版）

**codex 总判**：codex 真跑成功（codex_ran_ok=true），总判 needs-revision，报 7 high / 10 medium / 2 low 共 19 条。本轮逐条吸收：7 条 high 全部解决——H1 升 object 消 string/object 歧义、H2 补 schema 同步、H3 改口径为「机器产证据+human_cp pending、不伪造人工 CP」、H4 把混在 open 里的前置提升为 step1 决策项 D1–D5、H5 换显式 state→exit 映射、H6 gate 加例外行↔simulation 交叉校验、H7 stale SVG 清理+sha256。**无残留未处理 high**；10 medium + 2 low 亦全部采纳落进 Files/Steps/Gates。仅剩 B 组「真-open」（D6 两 proposed 页张力、D7 schema target/target_ratio 既存漂移、D8 跨 todo 岔口）为本 todo 明确不解、留 bureau:review 或 follow-up 的项，非未处理缺陷。

**可实施性**：no —— 卡在 CLAUDE.md#1 的 step1 用户方案确认，尤其决策项 D1–D5（PASSED_WITH_RISK 退出码=2、「仿真图」口径、复用 canonical 状态、human_cp pending 语义、spec schema 升级范围）需先拍板；用户点头后，全部代码/单测/图/文档本地即可一次落地（不依赖真机/VPN/GPU）。真数据的 PASSED_WITH_RISK 与 ADR0006 warmup/iters 核验、GPU 双边同口径、真正人工 CP 记录另留 blocked（真机/外部/产品形态）。

**残余风险**：1. 真机小 shape 真值未取——本地只 mock 注入演示（已明标 repo_mode=mock / env=(inj-slow)，禁作真实人工 CP 依据）；一条「真数据的 PASSED_WITH_RISK」要等 ascend-a5/a3 真机 msprof。
2. ADR0006（proposed）的 warmup≥10/iters≥30/median 是否真满足，需真机核 msprof 计数，本地无法验证。
3. 两 proposed 页 perf_baseline_source 张力未裁——不影响本 todo 走的 NPU-vs-TBE kernel_only 单边线；GPU 双边同口径闭环 blocked 于外部 GPU schema。
4. gate 对 SVG 只验 sha256 + 交叉校验 simulation↔per_case，**不 re-render 比对几何**（避免 gate 耦合 renderer）——理论上 renderer 有 bug 画错但 simulation 数据对时 sha 仍过；靠 perf_sim_plot 单测覆盖 renderer 正确性兜底，接受此边界。
5. human_cp 停在 pending——真正人工 CP 记录留产品形态（会话 agent 可 AskUserQuestion）阶段补，Layer 1 只产证据+挂 pending。
6. gen_cases 小 shape 派生仅覆盖当前 elementwise 算子族（dtype 从 spec、shape 为该族 fixture）；带 dtype_combinations/特殊 shape 的算子需各自派生规则（follow-up）。
7. schema doc 既存 target/target_ratio 漂移（D7）本 todo 未修，仅记观察，另立 follow-up。
8. cc-suite/bureau 若在实施环境不可用，走降级审计（codex exec）+ bureau deferred，durable 决策的 canonical 化会顺延到工具可用时。

**据 codex 修订**：逐条处理 codex 19 条 issue（7 high / 10 medium / 2 low）：

【HIGH — 全部解决】
- H1（small_shape_exception 同 key 不能既 string 又 object）：采纳。升级为**对象** {text, when_us_below, abs_gap_us_within, requires}——人读串放 text、机读阈值独立字段；parser 兼容 legacy 纯字符串（正则兜底）。彻底消歧。（Files#6/7、Steps2）
- H2（漏改 doc/oprunway-spec-schema.md 致契约漂移）：采纳。新增 Files#8/#9 同步改 spec-schema.md（string?→object?）+ workflow-design.md + taskdoc-to-spec.md。（Steps3）
- H3（把「仿真图+分析」等同 PASSED_WITH_RISK 的「人工 CP 记录」，与 canon 不符）：采纳并改口径。仿真图+分析定位为「供人工 CP 的**证据**」；acceptance.json 落 state=PASSED_WITH_RISK 且 human_cp={status:"pending", evidence:[…]}——机器只产证据挂 pending、不伪造人工签字（Layer 1 确定性脚本无法内联 AskUserQuestion，真正 CP 留会话 agent 形态）。（Approach1、Files#4）
- H4（核心前置仍列 Open Decisions 但 Steps 已按 default 实现，自相矛盾）：采纳。把这些从模糊 open **提升为「决策项 D1–D5：拟定 default + 依据，step1 用户一并拍板」**，Steps1/Gates 明确以其为实现前置；仅保留真正可延后的张力/漂移为 B 组真-open。ready_to_implement 据此诚实=blocked 于 step1。
- H5（run_workflow 退出码用 startswith('PASS')，PASSED_WITH_RISK 会天然 exit0，与「非零」冲突）：采纳。改为**显式 state→exit 映射** {PASSED:0, PASSED_WITH_RISK:2, *:1}，替换 startswith；补 RunWorkflowExitTest 的 exit2 用例。（Files#4/#13）
- H6（gate 只查 simulation 非空+文件存在，无法证明图/分析对应当前例外行/阈值/gap/case_id）：采纳。gate_task3 加**交叉校验**：例外行集合==simulation.points case_id 集合、逐点 npu/base/gap/within 与 per_case exception_detail 一致，并记/验 simulation_plot.sha256。（Files#3）
- H7（复用 out 目录未清旧 SVG，stale 图让「有图」门误过）：采纳。run_workflow 启动即删 out_dir/perf_sim_*.svg；report 记 sha256、gate 重算比对（stale/替换即失败）。（Files#3/#4）

【MEDIUM】
- M1（acceptance.json 用展示串、未映射 canonical PASSED/FAILED_*）：采纳。新增 canonical `state` 枚举字段（PASSED/PASSED_WITH_RISK/FAILED_PRECISION/FAILED_PERFORMANCE/BLOCKED_GATE/BLOCKED_NO_PERF_CASE/NEEDS_REVIEW）+ reason，保留 overall 展示串；退出码 driven by state。
- M2（simulation_plot 路径攻击 ../、symlink、绝对路径）：采纳。gate 用 realpath+commonpath 钉死在 --dir 内、拒绝非普通文件/逃逸。（Files#3）
- M3（例外条件 npu<10 语义含糊、未要求小 shape tag）：采纳。谓词精确化为「小shape-tag ∧ max(npu,base)<阈(严格<) ∧ gap≤容差(≤)」，补 =阈/gap 边界/非-tag 用例测试。（Files#1/#11）
- M4（数值合法性边界 0/负/NaN/inf/None 未覆盖）：采纳。perf_compare 统一 finite-正数校验→非法即 blocked+note，补单测。（Files#1/#11、Acceptance7）
- M5（gen_cases 硬编码 (64,)/(512,)/float32 对无 float32/非一维算子不成立）：采纳（部分）。dtype 从 spec 取；shape 明确为**当前 elementwise 算子族的 fixture 默认**（非通用规则），带 dtype_combinations/特殊 shape 的算子留 follow-up。（Files#5）
- M6（renderer 固定画 10us/±3us、casegen 固定两 shape 违「零硬编码」）：采纳。阈值/容差从 report['simulation'] 传入 renderer（数据驱动）；casegen shape 标为 fixture。（Files#2/#5、Acceptance6）
- M7（cc-suite/bureau 当必过资源但无可执行预检、可见环境只有 codex）：采纳。加 step0 门禁工具 preflight，不可用则降级 codex exec + bureau 标 deferred 并显式注明「未过某门」。
- M8（perf_compare 与 perf_sim_plot 都描述为产 simulation，事实源不清）：采纳。明确 **perf_compare 独家生成 report['simulation']，perf_sim_plot 只 render_svg 消费**，删掉 build_simulation。（Approach5、Files#2）
- M9（漏 run_workflow 分支测试：--perf-slow/acceptance state/门前重 dump/退出码）：采纳。扩 RunWorkflowExitTest 加 test_perf_slow_passed_with_risk（exit2+state+svg+gate 正/负路）。（Files#13）
- M10（示例命令依赖 cwd 在 plugin/acc-common）：采纳。所有 Acceptance/Gate 命令加 `cd plugin/acc-common &&` 前缀。
- M11（mock base=npu*0.8 易被误读为真实小 shape 证据）：采纳。mock 产物显式 env=(inj-slow)、acceptance.json repo_mode=mock、human_cp.evidence 标 demo、changes-brief 明说 mock 不作真实 CP 依据。（Files#1/#4/#14）

【LOW】
- L1（SVG 未 XML escape）：采纳。renderer 对 op/case_id/note/conclusion 全部 escape，补良构断言测试。（Files#2/#12）
- L2（Gates 列了 repo_adapter 但 Files/Steps 没改它）：采纳。从代码审对象删除 repo_adapter（本 todo 确实不改它）。

【无驳回】——19 条全部采纳（H4/M5 以「重构决策结构」「限定 fixture 范围+follow-up」形式吸收，非原样照单，但方向一致）。

### 终版 plan

# T6 · 小 shape 例外通道 + timing_scope 纪律（终版 plan）

## Goal
让流水线实现任务书的「<10us 场景差值在容差内 → 出性能仿真图 + 分析证明与 TBE 一致/更优」例外条款：不再把这类用例一律判「性能未达成」，而是走一条**显式、落进状态机、机器门可验**的例外通道——
1. perf_compare 产**显式新 perf status `exception`**（区别 ok/fail/blocked/no_perf_cases），例外用例 `达标` 仍记 False + 打 `exception` 标（**绝不偷偷置 True**）；
2. perf_compare（**唯一事实源**）产 `report['simulation']` 机读分析；perf_sim_plot **只渲染**该分析成 SVG 仿真图；
3. 映射到 canonical 边角态 **PASSED_WITH_RISK**，`human_cp` 显式挂 `pending`（机器只产证据、不自动替人签 CP）；
4. 机器门 gate_task3 强制「status=exception → 必须有 simulation 分析 + 与例外行**交叉一致** + 落盘 SVG（sha256 校验、路径钉死在产物目录）」，否则判 FAILED——**有图才放行、无图/对不上判 FAILED、不静默绕过**；
5. 全程保持 kernel_only 同口径纪律（例外行不 blocked → 继续受既有 kernel_only 门约束）。

本 todo 只做**本地能做完**的逻辑 + mock 注入 + 单测 + 图/分析生成；真机小 shape 真值另留 blocked。

## Approach（对齐已 Read 的 canon，不重推已 settle）
1. **复用 canonical 状态、不新造 canon**：[[Task 3 acceptance state machine]]（**canonical**，当事实）已有边角态 `PASSED_WITH_RISK`（放行但有风险、**需人工 CP 记录**）。小 shape 例外本质=「本该 FAILED_PERFORMANCE，但任务书给了条件放行、需附证据」→ 正好映射 `PASSED_WITH_RISK`。**修正（据 codex H3）**：仿真图+分析是「供人工 CP 的**证据**」，**不等于**「人工 CP 记录本身」。故 acceptance.json 落 `state=PASSED_WITH_RISK` 且 `human_cp={status:"pending", evidence:[…]}`——机器产齐证据、挂 pending，真正 CP 由后续会话 agent 形态（可 AskUserQuestion）补；Layer 1 确定性脚本无法内联人工确认，不伪造签字。不引入新状态 → 不动 canon 状态清单。
2. **例外落进状态机、不静默绕过**：perf_compare 新增显式 status `exception`；例外用例 `达标` 保持 False + 打 `exception` 标 + 记 `exception_detail`；机器门强制完整性 + 交叉一致 + SVG sha 校验。
3. **timing_scope 纪律沿用现有实现、不重造**：perf_compare 已有「scope 不一致→BLOCKED_INCOMPARABLE_SCOPE」，gate_task3 已强制「非 blocked 行 scope 必须 kernel_only 否则 FAILED」。例外逻辑放在 scope 匹配检查**之后**，例外行不 blocked → 天然继续受 kernel_only 门约束。
4. **阈值来源=任务书/spec、非把 proposed 页当事实**：`when_us_below`/`abs_gap_us_within` 从 `spec.perf.small_shape_exception` 取（结构化对象优先、legacy 字符串正则兜底、解析不出则例外禁用+落 note，**绝不硬编码 10/3**，守零硬编码）。
5. **单一事实源（据 codex M8）**：`report['simulation']` **只由 perf_compare 生成**；perf_sim_plot **只消费+渲染**，不二次推断结论。
6. **纯 stdlib SVG（据 codex L1/M6）**：perf_sim_plot 零第三方依赖（Layer 1 工具中立、不能依赖 matplotlib）；阈值线（when_us_below）/容差带（±abs_gap）**从 simulation 数据传入、非写死**；所有文本字段（op/case_id/note/conclusion）做 **XML escape**。
7. **引用未 settle 页的标注**：[[ADR 0006 — timing scope]] 与 [[Performance baseline follows the reference source]] 均 **status=proposed（未 settle）**——本 plan 只借二者共识的「kernel_only 双边同口径、median/p50、warmup≥10/iters≥30」作策略指引，明确「小 shape 例外条款/<10us 差 3us」出处是这两页归纳的**任务书原文**；两页间关于 `perf_baseline_source` 的张力**本 plan 不碰、不解、留 bureau:review**。本 todo 走的是 NPU-vs-内置TBE(kernel_only) 这条两页都认的线，不依赖该张力裁决。

## Canon 依据（读时已按 trust tier 核）
- [[Task 3 acceptance state machine]] — **canonical（当事实）**：4 核心态 + 边角 `PASSED_WITH_RISK`（放行有风险·**需人工 CP 记录**）/ `BLOCKED_INCOMPARABLE_TIMING_SCOPE`。本 todo 复用 `PASSED_WITH_RISK` 承载小 shape 例外，不新造状态 → 不改 canon。
- [[generated_harness responsibilities]] — **canonical（当事实）**：职责#4「两侧同 timing_scope、warmup/iters/median 自实现、**比值判定归 validator**」。本 todo 不改采集栈，只在判定层加例外分支，比值/例外判定归 perf_compare（validator 家族）。
- [[ADR 0006 — Compare performance at a matched timing scope]] — **proposed（未 settle，存疑）**：timing_scope 必填枚举、NPU/GPU 同 scope 否则 BLOCKED、默认 kernel_only、median/p50、warmup≥10/iters≥30。作策略指引；其 `perf_baseline_source` 张力本 todo 不裁。
- [[Performance baseline follows the reference source]] — **proposed（未 settle，存疑）**：「任务书常带小 shape 例外条款（<10us 差 3us 需仿真图证明）」——本 todo 例外语义的出处；但阈值数从 spec 取、非把此 proposed 页当事实。
- 现码（现状事实）：`perf_compare.py`（ratio=baseline_us/npu_us、scope 不一致→BLOCKED_INCOMPARABLE_SCOPE、status∈{ok,fail,blocked,no_perf_cases}、达标=ratio≥target_ratio）；`validate_acceptance_state.py`（`_PERF_STATUS` 集合 + gate_task3 kernel_only 门）；`run_workflow.py`（退出码现为 `overall.startswith("PASS")`——**须改**，见 Files#4）；`gen_cases.py`（末尾恒追加 (1024,1024) 性能用例）。

## Files（14 个；已删除 codex L2 指出的误列 repo_adapter）

1. **`plugin/acc-common/perf_compare.py`** · edit
   - 新增 `_parse_small_shape_exception(spec)`：`perf.small_shape_exception` 为 **dict** → 取 `when_us_below`/`abs_gap_us_within`（须 finite 正数，否则禁用+note）、`requires` 可选；为 **str（legacy）** → 正则 `<\s*(\d+(?:\.\d+)?)\s*us` 抓阈、`差\s*(\d+(?:\.\d+)?)\s*us` 抓容差，两者都成→构造内部 dict，否则禁用+note；**缺失/None** → 返回 None（例外禁用）。
   - **数值合法性（codex M4）**：ratio/例外判定前，对 `npu_us`、`baseline_us` 统一 finite-正数校验（0/负/NaN/inf → 该行 blocked+note，不进例外/不算 ratio）。
   - **例外判定（codex M3 消歧）**：仅对**本会未达标**的行判例外资格，谓词 = 该行是**指定小 shape 性能用例**（`tags` 含 `小shape`）**且** `max(npu_us, baseline_us) < when_us_below`（「场景 <10us」= 两侧都在阈下，最防误判）**且** `abs(npu_us-baseline_us) <= abs_gap_us_within`。命中 → 行加 `exception="small_shape"` + `exception_detail{npu_us,baseline_us,gap,within,when_us_below,conclusion}`，`达标` **保持 False**；不命中 → 照旧 genuine 未达标（fail）。边界：`<` 严格、容差 `<=`。
   - **status 优先级**：`blocked > fail(存在 genuine 非例外未达标行) > exception(无 genuine fail/blocked 但有例外行) > ok`。
   - **唯一事实源**：组装 `report['simulation'] = {when_us_below, abs_gap_us_within, points:[{case_id,numel,npu_us,baseline_us,gap,within,conclusion}], overall, fit?(≥2点线性拟合斜率对比·标『模型/推断』)}`——**只此处生成**。
   - `mock_baseline(spec, evidence, factor=1.08, slow_cases=None)`：`slow_cases` 内 cid → `base=round(npu*0.8,3)`、`env="mock-TBE(inj-slow)"`（制造「NPU 略慢于 TBE 但小差」以本地触发例外）；其余不变。**mock 产物显式标注（codex M11）**。

2. **`plugin/acc-common/perf_sim_plot.py`** · create
   - **只渲染**（codex M8）：`render_svg(simulation, out_path)` 纯字符串拼 SVG（坐标轴、NPU/TBE 两序列、`when_us_below` 虚线阈、`baseline±abs_gap` 容差带、图例；≥2 点叠线性拟合并标『模型/推断』；1 点退化为测点对比+容差论证）——**阈值/容差从 `simulation` 数据传入、不写死**（codex M6）。
   - **XML escape（codex L1）**：`op/case_id/note/conclusion` 等所有文本字段经 `xml.sax.saxutils.escape` 处理。
   - `sha256_of(path)` 辅助（供 run_workflow 记 hash）。零第三方依赖、可移植。**不再定义 `build_simulation`**（分析生成归 perf_compare 独占）。

3. **`plugin/acc-common/validate_acceptance_state.py`** · edit（gate_task3 加例外门 + 交叉校验）
   - `_PERF_STATUS` 加 `'exception'`。
   - `status=='exception'` **不当硬失败**（合法条件放行），但强制**完整性 + 交叉一致（codex H6）**：
     (a) `report['simulation']` 非空、含 `when_us_below/abs_gap_us_within/points`；
     (b) **例外行集合 == simulation.points 的 case_id 集合**（per_case 中带 `exception` 标的行）；
     (c) 逐点 `npu_us/baseline_us/gap/within` 与对应 per_case 行 `exception_detail` **一致**；
     (d) `report['simulation_plot']={file,sha256}` 存在，`file` 经 `realpath`+`commonpath` **钉死在 `--dir` 内**（拒绝绝对路径/`..`/symlink 逃逸/非普通文件，codex M2），重算 sha256 **与记录相符**（防 stale/替换，codex H7）；
     任一不满足 → `errs.append('小shape例外缺/对不上仿真图或分析·不可静默放行')` → FAILED。
   - 例外行照旧走 `scope=='kernel_only'` 检查（不 blocked → 现有循环覆盖）。

4. **`plugin/acc-common/run_workflow.py`** · edit
   - **stale 清理（codex H7）**：把现有「清 `_real_baseline.json`/`perf_result.txt`」块扩展为**同时删 `out_dir/perf_sim_*.svg`**，防旧图让「有图」门误过。
   - Task3 dump 后：若 `report.summary.status=='exception'` → `import perf_sim_plot` 写 `out_dir/perf_sim_<op>.svg`、`report['simulation_plot']={file:"perf_sim_<op>.svg", sha256:…}`、**重 dump `perf_report.json`（务必在门循环前，门才读得到）**。
   - **canonical state + 退出码（codex H5/M1）**：run() 计算并返回 `state`（canonical 枚举 `PASSED | PASSED_WITH_RISK | FAILED_PRECISION | FAILED_PERFORMANCE | BLOCKED_GATE | BLOCKED_NO_PERF_CASE | NEEDS_REVIEW`）+ 保留 `overall` 展示串。分支加：`精度pass + 门过 + status==exception + 无 genuine fail/blocked → state=PASSED_WITH_RISK`。`acceptance.json` 落 `{op, state, reason, overall, gate, precision_verdict, perf_status, repo_mode, human_cp}`；`human_cp={status:"pending", evidence:["perf_sim_<op>.svg","perf_report.json#simulation"]}`（codex H3）；`repo_mode=mode`（codex M11）。
   - `main()` 退出码改为**显式 state→exit 映射**（替换 `startswith('PASS')`）：`{PASSED:0, PASSED_WITH_RISK:2, *:1}`——干净 0 / 硬失败 1 / 风险放行 2（非零引起注意、CI 不自动合入）。
   - CLI 加 `--perf-slow`（逗号分隔 cid）透传给 `mock_baseline` 的 `slow_cases`（本地 e2e 演示例外）。

5. **`plugin/acc-common/gen_cases.py`** · edit（codex M5 消硬编码）
   - 当 `spec.perf.small_shape_exception` 存在时，额外生成 **≥2 个小 shape 性能用例**：**dtype 从 spec 取**（`self_param["dtype"][0]`），shape 用当前 **elementwise 算子族的 fixture 默认**（如 `(64,)`、`(256,)`），`dims=['性能']`、`tags` 含 `小shape`。不声明例外的 spec **行为完全不变**（保既有基线）。
   - 注：这两个 shape 是**当前 elementwise 族（Sign/IsClose/Equal/Neg）的 fixture**、非通用规则；带 `dtype_combinations`/特殊 shape 的算子需各自派生规则（follow-up，见 open）。

6. **`plugin/acc-common/specs/sign.spec.json`** · edit
   - `small_shape_exception` 由纯字符串**升级为对象**（含人读 `text` + 机读阈值，解决 codex H1 的「同 key 不能既 string 又 object」）：`{"text":"<10us 差 3us→仿真图","when_us_below":10,"abs_gap_us_within":3,"requires":"simulation_plot+analysis"}`。

7. **`plugin/acc-common/specs/isclose.spec.json`** · edit — 同 sign 升级为对象（保留 `text`）。

8. **`doc/oprunway-spec-schema.md`** · edit（codex H2，防入口契约漂移）
   - `perf.small_shape_exception` 定义由 `"string?"` 改为 **`object?`** 并写明 `{text, when_us_below, abs_gap_us_within, requires}` 结构；同步更新 §2 三个真实例中的该字段写法（string→object），注明「legacy 纯字符串向后兼容、由 perf_compare 正则兜底解析」。

9. **`doc/oprunway-workflow-design.md`** · edit（codex H2）
   - 该文 L33 提及 `small_shape_exception` 处同步为结构化口径一行说明（与 schema 对齐）。

10. **`plugin/skills/acc-spec/references/taskdoc-to-spec.md`** · edit
    - 字段映射表 `perf.small_shape_exception` 行 + §示例：说明除人读串外应产结构化对象 `{text,when_us_below,abs_gap_us_within}`（供 perf_compare 机读）。本 todo 只补文档；acc-spec 抽取脚本是否也产 object 见 open D5（默认另立 follow-up）。

11. **`plugin/acc-common/test_perf_compare.py`** · create（新增单测文件）
    - 覆盖：命中(小shape tag + max<阈 + gap≤tol)→status=exception 且该行 `达标==False`+有 `exception` 标+report 有 `simulation`；gap>tol→fail；`max(npu,base)==阈`（`<` 严格）→fail（不命中）；非小shape-tag 但恰好 <阈→**不**误转例外；混合(部分达标+部分例外)→exception；含 genuine fail + 例外→fail 优先；scope 不一致→blocked（不误判例外）；数值非法(0/负/NaN/inf)→blocked；阈值解析 dict/legacy-string/缺失三路。

12. **`plugin/acc-common/test_perf_sim_plot.py`** · create
    - 冒烟：给定 `simulation`→写出合法 SVG（含两序列/阈值线/容差带/图例）；1 点 vs ≥2 点(拟合)两分支；含特殊字符的 case_id/note → SVG 仍良构（**XML escape 生效**，用 `xml.dom.minidom.parseString` 断言可解析）；`render_svg` 只读 `simulation`、不改数据（单一事实源）。

13. **`plugin/acc-common/test_validate_acceptance_state.py`** · edit（既有 28 测不回归）
    - gate_task3 例外：`status=exception`+完整 simulation+交叉一致+plot 文件在 dir 且 sha 对→PASSED；缺 plot / sha 不符 / simulation 与例外行对不上→FAILED(含『仿真图』字样)；plot 指向 `../` 或绝对路径外→FAILED(路径钉死)；例外行 `scope!=kernel_only`→FAILED。
    - **扩 `RunWorkflowExitTest`（codex M9）**：新增 `test_perf_slow_passed_with_risk`——subprocess 跑 `run_workflow.py specs/sign.spec.json --mode mock --perf-slow <小shape cid> --out D`，断言 **exit==2**、`acceptance.json` `state=="PASSED_WITH_RISK"`+`human_cp.status=="pending"`+`repo_mode=="mock"`、`D/perf_sim_sign.svg` 存在、`perf_report.json` `summary.status=="exception"`；再 subprocess 跑 gate `--stage task3 --dir D` 应 exit0；删 SVG 后再跑 gate → exit1（证不静默绕过）。

14. **`doc/oprunway-changes-brief.md`** · edit — 按 CLAUDE.md#4 **倒序**追加一两句大白话：小 shape 例外通道落地（perf status exception→PASSED_WITH_RISK+human_cp pending、perf_compare 独家产 simulation、perf_sim_plot 只渲染、门强制有图+交叉一致+sha 才放行、阈值从 spec/任务书取、复用 canonical 状态不动 canon、mock 注入明标 demo 不作真实 CP 依据）。

## Steps

0. **门禁工具 preflight（codex M7）**：动手前先探 `cc-suite:audit-fix` / `bureau:*` 是否可用；不可用则**降级**——代码审改走 `codex exec` 定制审、bureau 捕获标 blocked/deferred，并在交付说明里注明「未过 X 门、原因」。（本会话 skill 列表含 cc-suite/bureau，正常路可用；此为安全网。）
1. **先抛方案经用户点头（CLAUDE.md#1）**：init 阶段动手前，把本 plan 关键取舍 **+ 决策项 D1–D5**（见 Open）向用户列清、拍板后再落地。不 push 任何远端（#2）。
2. **spec 阈值结构化**：编辑 sign/isclose spec，`small_shape_exception` → 对象（`text`+机读阈值）。这是后续所有例外判定的唯一数值来源。
3. **同步 schema/文档（H2）**：改 `doc/oprunway-spec-schema.md`（`string?`→`object?` + 示例）、`doc/oprunway-workflow-design.md`、`taskdoc-to-spec.md`——防入口契约漂移。
4. **perf_compare 加解析器 + 数值校验**：`_parse_small_shape_exception`（dict→取字段；str→正则；无→None）；finite-正数校验；解析不出/非法落 note。
5. **perf_compare 加例外判定 + 唯一 simulation**：谓词（小shape-tag ∧ max<阈 ∧ gap≤容差 ∧ 未达标）→行打 exception 标、`达标` 保持 False、记 exception_detail；status 优先级 blocked>fail>exception>ok；组装 `report['simulation']`（唯一事实源）。
6. **mock_baseline 加注入 + 标注**：`slow_cases` 造「略慢小差」、env 标 `(inj-slow)`。
7. **gen_cases 加小 shape 性能用例**：`small_shape_exception` 存在→追加 ≥2 个（dtype 从 spec、shape 为 elementwise fixture、tag 含 `小shape`）；无声明则不变。
8. **写 perf_sim_plot.py**：`render_svg(simulation,out)`（阈值/容差从数据、XML escape、≥2点拟合标推断）+ `sha256_of`；CLI `main(report.json,out)`。**不产分析**。
9. **run_workflow 接线**：stale 清 SVG；status==exception→渲图+注 `simulation_plot{file,sha256}`+**门循环前重 dump**；state 分支加 PASSED_WITH_RISK+human_cp pending+repo_mode；`main` 改 **state→exit 映射**；加 `--perf-slow`。
10. **gate_task3 加例外门 + 交叉校验**：`_PERF_STATUS` 加 exception；完整性 + 例外行↔simulation 集合/数值一致 + plot 路径钉死+sha 校验；例外行续走 kernel_only。
11. **补单测**：新建 `test_perf_compare.py`/`test_perf_sim_plot.py`；扩 `test_validate_acceptance_state.py`（gate 例外三/四例 + RunWorkflowExitTest 的 --perf-slow/exit2/删图负路）。
12. **本地 e2e 验证 + 过门 + 记文档 + 捕获决策**：见 Acceptance；代码走 cc-suite:audit-fix（不可用降级 codex），散文走 codex exec 审；机器门 + unittest 必过；durable 决策走 bureau:note 捕获→留 compile→review，绝不手改 canonical。全程不 push（#2）。

## Gates
- **用户方案门（CLAUDE.md#1）**：init 阶段，step1 先抛方案 + 决策项 D1–D5 经用户同意才实施。
- **副作用/推送门（#2#3）**：仅本地新增/改文件（plugin/acc-common、doc、skills 文档），不 push、不动远端环境；mock 注入明标 test-only/demo。
- **门禁工具 preflight（#5 + codex M7）**：step0 先探 cc-suite/bureau 可用性；不可用则降级并显式注明「未过某门」。
- **Codex 双门（#5）**：代码（perf_compare/perf_sim_plot/validate_acceptance_state/gen_cases/run_workflow/specs/3 个 test 文件）→ **cc-suite:audit-fix**（9 维审→修→验；不可用降级 codex exec）；散文（changes-brief/spec-schema/workflow-design/taskdoc-to-spec/bureau note 拟写文本）→ **codex exec** 定制审。**已按 codex L2 从代码审对象删除误列的 repo_adapter（本 todo 不改它）。**
- **机器门（出裁决必过）**：`cd plugin/acc-common && python3 validate_acceptance_state.py --stage task3 --dir <run>` 在「例外+有图+一致」下 exit0、「例外+缺图/sha 不符/对不上/错 scope/路径逃逸」下 exit1；`cd plugin/acc-common && python3 -m unittest test_validate_acceptance_state test_perf_compare test_perf_sim_plot` 全绿且**既有 28 测不回归**。
- **bureau 门**：新决策走 capture(bureau:note)→compile→review 三段，落 proposed、人工才升 canonical；本 todo 复用 canonical 状态、不直接改任何 cabinet 页。

## Blocked / 依赖
**本地现在能做完（无需真机）**：例外判定/解析器/数值校验、gen_cases 小 shape 用例、perf_sim_plot 的 SVG（阈值数据驱动+XML escape）、gate_task3 例外门+交叉校验+路径钉死+sha、run_workflow 接线（state→exit、stale 清图、human_cp pending）、mock_baseline 注入、全部单测、本地 e2e 演示、schema/文档同步、bureau 捕获文本。以上不依赖 NPU/VPN/GPU。

**卡在真机/外部/用户**：
- **真实小 shape us 数**（真 <10us、真差在容差内）需真机 msprof（ascend-a5 真950 / ascend-a3），本地只能 mock 注入演示逻辑；出一条「真数据的 PASSED_WITH_RISK」要等真机跑。
- **ADR0006（proposed）的 warmup≥10/iters≥30/median 实际是否满足**，需真机核 msprof 的 warmup/iters 计数与统计口径——本地无法验证。
- **timing_scope 双边同口径的 GPU 侧尚未接**（属 P2、等外部 GPU 基线 schema），当前例外/对比只在 NPU-vs-内置TBE(kernel_only) 这一边成立。
- **落地实施本身 blocked 于 step1 用户方案确认 + 决策项 D1–D5 拍板**（init 阶段规则）。
- **真正的人工 CP 记录** blocked 于产品形态（会话 agent 可 AskUserQuestion）阶段；Layer 1 只产证据 + 挂 `human_cp=pending`。

## Acceptance（可验证标准；命令均以 `cd plugin/acc-common` 为前提，codex M10）
1. **单测**：`cd plugin/acc-common && python3 -m unittest test_validate_acceptance_state test_perf_compare test_perf_sim_plot -v` 全 PASS；既有 28 测无回归。
2. **例外正路（mock 注入）**：`cd plugin/acc-common && python3 run_workflow.py specs/sign.spec.json --mode mock --perf-slow <小shape性能cid> --out D` 后 —— `perf_report.json` `summary.status=="exception"`；该例外行 `达标==false` 且带 `exception` 标 且 `scope=="kernel_only"`；`D/perf_sim_sign.svg` 存在且含两序列+阈值线+容差带；`perf_report['simulation']` 有逐点结论与总体判词 + `simulation_plot{file,sha256}`；`acceptance.json` `state=="PASSED_WITH_RISK"`、`human_cp.status=="pending"`、`repo_mode=="mock"`；进程 **exit==2**；`[验收门] STATUS PASSED`。
3. **不静默绕过（负路）**：删 `D/perf_sim_sign.svg` 后 `python3 validate_acceptance_state.py --stage task3 --dir D` 输出含『仿真图』的 error 且 exit1；篡改 SVG（sha 不符）/让 simulation 与例外行对不上/例外行 scope 改非 kernel_only/plot 指向 `../` → 门同样 FAILED。
4. **无例外资格→不误放行**：构造 gap>容差 或 max(npu,base)≥阈 或 非小shape-tag 的未达标用例 → `status=="fail"`、`state=="FAILED_PERFORMANCE"`、exit1，无 exception 标（不静默转例外）。
5. **无声明例外的 spec（equal/neg）行为不变**：常规 mock 跑通、用例数与状态与改前一致（gen_cases 不加小 shape 用例）。
6. **阈值零硬编码**：删 spec 的 `small_shape_exception` 后例外禁用并落 note；改结构化阈值（如 `when_us_below:8, abs_gap_us_within:2`）后判定随之变、SVG 阈值线/容差带随之移——证明数值出自 spec 而非写死。
7. **数值非法防污染**：baseline=0/负/NaN/inf、npu=None → 该行 blocked+note，不误入例外、不崩。

## Open Decisions
### A. 决策项（step1 一并拍板，附拟定 default + 依据）— 据 codex H4，这些是**实现前置**、不再是模糊 open
- **D1 · PASSED_WITH_RISK 退出码**：default = **exit 2**（干净 0 / 硬失败 1 / 风险放行 2，非零引起注意、CI 不自动合入）。依据：canon 写「需人工 CP」→ 不能当干净 PASS 静默 exit0。
- **D2 · 「仿真图」口径**：default = **实测 NPU-vs-TBE 对比图 + ±容差带 + (≥2点)线性趋势拟合 + 机读分析**。需确认是否足够，还是要求真正的预测/外推仿真模型。
- **D3 · 状态复用**：default = **复用 canonical `PASSED_WITH_RISK`、不新造状态**（不动 canon）。若要单列新态（如 `PASSED_SMALL_SHAPE_EXCEPTION`）=canon 变更，走 bureau:review。
- **D4 · human_cp 语义**：default = **机器挂 `pending`、不自动替人签 CP**（Layer 1 无法内联人工确认；真正 CP 留会话 agent 形态）。
- **D5 · spec schema 升级范围**：default = **spec 升 object（string 向后兼容）+ 同步 schema/workflow-design/taskdoc 文档**；acc-spec **抽取脚本**是否也产 object（波及 acc-spec skill / spec-schema）**另立 follow-up**、不在本 todo。

### B. 真·open（本 todo 不解、不依赖）
- **D6**：两 proposed 页（ADR0006 ↔ perf-baseline）关于 `perf_baseline_source`（gpu_external 默认 vs 从任务书推、GPU 对比层是否可选）的**张力**——留人工 bureau:review。本 todo 走 NPU-vs-TBE kernel_only 线，不依赖其裁决。
- **D7**：`doc/oprunway-spec-schema.md` 既存漂移——schema 写 `perf.target`(string) 而实际 spec 用 `perf.target_ratio`(number)。**本 todo 未引入、不扩范围修**，记为观察，另立 follow-up。
- **D8**：与其它 todo 的岔口（T3 canon 分解 / T9 发布形态 / T11 外发授权）本 todo 不触及。

---

## T7-dtype-attr-coverage — dtype / attr 覆盖扩面（int/bf16 + attr 值矩阵）——据 codex 审计修订终版

**codex 总判**：codex 真跑成(codex_ran_ok=true)、总判 needs-revision、6 条 high + 8 medium + 2 low。逐条对照源码核实后全部成立(无误报)：#1 gen_cases.py:74 spec 驱动 dtype、#2 repo_adapter.py:51/:127 硬断言 dtype、#3 ADR 已载门不重判 verdict、#4 ls 证 neg_runner 缺失、#5 rule-catalog §1.1 int→exact、#6 _metric 除法遇 non-finite 崩。终版已逐条吸收：6 条 high 全部解决(拆 Track A/B/C 解 #1、storage_dtype 契约解 #2、defect/门分流照 ADR 解 #3、删 runner edit 解 #4、per-case compare 解 #5、per-op special-value 表 + non-finite-aware 解 #6)；medium/low 亦全收。**无残留未解 high。** 注：本终版本身尚未再经 codex 复审(修订后应再过一轮散文门/代码门)——这是交付前应补的动作，不是已完成事实。

**可实施性**：no（本地可动手、但先过方案确认门）——Track A(gen_cases/repo_adapter/validator/fixture spec/单测/mock 端到端/机器门)是纯本地无 NPU、可立即全绿实施；但其核心是「storage_dtype 契约 + per-case compare + 语义 id 迁移」这一设计变更，按 CLAUDE.md #1 必须先向用户抛方案、点头才落代码。Track B(权威 spec dtype)卡任务书原文+用户批；Track C(runner int/bf16 分支 + neg_runner)卡真机 NPU + pr_facts、且须从算子 example 锚定不能手猜。故：设计确认后 Track A 即可开工，B/C 待外部条件。

**残余风险**：1) 契约扩展(storage_dtype/per-case compare/语义 id 迁移)是本轮最重的设计变更，牵动 gen_cases+repo_adapter+validator 三处协同——若用户否掉某一项(如不接受 id 迁移)，approach 需回退重排。2) 语义 id 迁移会改变现有 ephemeral 报告的 case_id(reports/ 已 gitignore、影响可控，但若有外部脚本硬编码旧 id 会断)。3) bf16 位级 helper 的 round-half-even + subnormal/NaN 边界易踩坑，正确性完全押在单测覆盖度上；若单测漏某边界，golden 会静默错(harness 造错→一路错的老教训)。4) Track A 仅证「流水线能处理 int/bf16」，**不等于**「某算子在 dtype 上被验收」——真机能力(aclnn 是否支持 int16/bf16)+ 权威 dtype(任务书)仍是硬缺口，报告措辞须防把「能力」讹成「已验收」。5) 终版修订后尚未再过 codex/audit-fix，落地前须补审。6) machine-verifiable gate 是 verified/proposed tier、未 settle，本轮当约束用但以单测实证——若门语义后续再变，需回看。

**据 codex 修订**：逐条处理 codex 18 条（全部据实读码核过，无一凭空采纳/驳回）：

【HIGH·全部解决】
#1(spec dtype 冲突)·采纳：读证 gen_cases.py:74 `dtypes=self_param["dtype"]` 确认「不改 spec 就不产新 dtype」。拆 Track A(fixture spec 验能力，不碰权威 spec)/Track B(权威 spec dtype 挂任务书+批)/Track C(runner 挂真机)。acceptance 从「run_workflow 对权威 spec 全绿」改为「对 fixture spec 全绿」。
#2(bf16 npy fp32 vs dtype 标签)·采纳(命门)：核实 run_mock:51 与 run_new_example:127 均硬断言 str(arr.dtype)==inp["dtype"]，numpy 无 bf16→必失败。引入 storage_dtype(物理)/dtype(逻辑) 契约拆分，bf16 存 uint16 物理字节+fp32 golden，两处校验点改比 storage_dtype；fp32/16/int 缺省 storage=dtype 保向后兼容。
#3(--defect 门挡措辞)·采纳：核 ADR gate-checks-evidence-integrity-not-verdict 与 run_workflow.py 逻辑，确认 defect→validator FAIL(精度)+exit1、门仍 PASSED；门失败另用子集/篡阈值/篡 scope 触发。acceptance#3、step9、单测据此改写。
#4(neg_runner 不存在)·采纳：ls 证 new_example 仅 sign/equal/isclose；删原 plan 4 份 runner edit 条目，runner 全挪 Track C。
#5(per-dtype 判据)·采纳：rule-catalog §1.1 证 int→exact_equal。新增 case 级 expected.compare(exact_equal/rel_err)；int→exact；Sign/Neg 的 bf16/fp16 因输出∈{-1,0,1}/精确取负→也 exact，绕开 bf16 阈值权威难题(=part of #5 的 bf16 误判担忧解法)。
#6(_metric NaN/inf)·采纳(精化框架)：核 _metric 除 |g|+1e-7、exact 分支 NaN!=NaN。指出 codex 的「NaN 必误 fail」只对数值算子成立——IsClose/Equal 是 bool 输出、NaN 输入安全，故 equal_nan NaN case 保留；仅 Sign/Neg 数值算子本轮不注入产 non-finite golden(§1.3 inf_nan 非 mandatory)；_metric 另加 non-finite-aware。

【MEDIUM·全部解决】
#7(attr 笛卡尔歧义)·采纳：attr_matrix 定义为显式 attr 字典列表，每项一条 case、代表 dtype/shape，期望数=len(attr_matrix) 写进单测。消歧义。
#8(attr_matrix schema 文档)·采纳：files 加 doc/oprunway-spec-schema.md + acc-spec 抽取规则/自检/示例。
#9(mock 不走 bin 路径)·采纳：核 run_mock 只 golden.copy()、不物理化。抽 materialize_input/readback_output 纯函数 + 本地 round-trip 单测；acceptance#4 明确「bf16 收发靠 round-trip 单测证、非 mock」。
#10(§0 三轴误引)·采纳：读 rule-catalog §0 证三轴=dtype/special_value/layout、非 attr。attr 展开改用独立显式列表算法、不引 §0。
#11(inf_nan/§2.5 过宽)·采纳：§1.3 证 inf_nan mandatory_if=算子声明传播。加 per-op special-value 适用表(IsClose/Equal/Sign/Neg 各列生成哪些、为何)。
#12(case_id 稳定性)·采纳：确认 {op}_{i:03d} 扩面重排毁 defect 定位。改语义化稳定 id {op}_{dtype}_{shapetag}_{kind}[_a{k}]+碰撞 guard。
#13(int near/far 退化)·采纳：int isclose/equal near/far 改整数网格构造并断言各命中 True/False。
#14(Neg int 语义)·采纳(收敛)：int 数据排除 dtype-min(避 np.negative 溢出)；uint8 回绕/int64 溢出→out-of-scope(neg.spec task_pr_gaps 已列)。
#15(runner 从 example 非 header)·采纳：acc-runner SKILL 证「入口/dtype 从 example 抠不猜」「超支持→扩 gap 别硬塞」。本轮不手写 runner C++，Track C 锚 pr_facts+真机。
#16(门无可执行命令)·采纳：给出 cc-suite:audit-fix(Skill 工具)/codex exec(CLI)/validate_acceptance_state.py 命令 + 不可用时人工代替标注。

【LOW·全部解决】
#17(bf16 helper 端序/±0/subnormal/NaN)·采纳：helper 注明 little-endian 落盘前提、±0 保符号、subnormal/NaN quiet-payload/inf 溢出处理；单测覆盖。
#18(case_origin/rule ref 缺)·采纳：每 case 加 case_origin/rule_ref(rule-catalog 引用或 attr_matrix 下标)，acceptance#7 收，兑现「契约完整落实」不虚。

无整条驳回项；#6 对 codex 表述做了「数值算子 vs bool 算子」的精化(非驳回、是收窄适用面)。

### 终版 plan

# TODO T7 · dtype / attr 覆盖扩面（int/bf16 + attr 值矩阵）— 终版（codex 审后）

## goal（目标）
把三层流水线（gen_cases → repo_adapter → validator/gate）从「只支持 float32/16 + attr 只测默认值」扩到「能处理 int32/int16/bf16 + attr 值矩阵（如 IsClose 的 equal_nan、跨 rtol/atol 分支）」，全程守住 canon 两条硬约束：**layout 字节契约**（X_logical 喂 golden / X_bin 喂 kernel，分两份造、禁共用 reshape）+ **caseset id==evidence id 一一对应**（机器门防跑子集）。

**本轮范围收敛（codex#1/#4/#15 驱动）**——把「能力」与「权威覆盖」拆成两轨：
- **Track A（本轮·纯本地·可立即做）**：流水线**能力**扩面（gen_cases 产 int/bf16/attr 用例、repo_adapter mock 收发、validator 按 dtype 判、机器门覆盖），用**专设 fixture spec**（非权威 op spec）在本地 mock 端到端验证。不碰 4 份权威 spec 的 `params[].dtype`。
- **Track B（需任务书原文 + 用户批）**：把新增 dtype 提进权威 spec 的 `params[].dtype`（Sign spec 的 `change.dtypes_added:["int16"]`、Neg spec `task_pr_gaps` 已文档化该 gap→有据但仍属权威声明，须 gate）。
- **Track C（挂真机 + pr_facts）**：runner.cpp 的新 dtype 分支——按 acc-runner 纪律必须从**算子自带 example/op_def 抠**、不猜 header，且要真机编译/数值校验。**本轮不手写 runner C++**，仅在 todo/gap 里锚定待办。

> 血教训对齐：`self_param["dtype"]` 直接驱动 gen（gen_cases.py:74），所以「不改 spec 又要 run_workflow 覆盖新 dtype」自相矛盾——Track A 用 fixture spec 解，权威 spec 交 Track B。

## approach（做法）
四个关键决策，均对齐已 Read 的 canon 已定决策、不重推：

**（1）dtype 敏感 case 从 rule-catalog 取、不自造阈值**——按 canonical『Primitive-to-case rule library』落到 `rule-catalog.md`：§1.1（int8/int32 `compare: exact_equal`、bf16 尾数 7b、fp16 overflow_at=65504）、§1.3 special_value（`inf_nan` **仅 `mandatory_if: 算子声明 inf/nan 传播语义`**、`cast_boundary` round-half-even 中点 tie）。当前流水线 4 算子（IsClose/Sign/Equal/Neg）是 elementwise，**逐算子给 special-value 适用表**（见下），不无差别套 §2.5。

**（2）bf16 走「逻辑/物理双表示 + storage_dtype 契约」（codex#2 修订）**——numpy 无 bf16、本机无 ml_dtypes（已实测），故 caseset 输入契约新增 `storage_dtype`（物理，落盘/喂 kernel 的字节 dtype）与保留 `dtype`（逻辑语义，覆盖矩阵/门读它）：
  - bf16 用例：`dtype="bfloat16"`、`storage_dtype="uint16"`；`x{j}.npy` 存 **bf16-round 后值的 uint16 位模式**（round-half-to-even）；`golden.npy` 存**同源 fp32-on-bf16-grid** 值。
  - fp32/fp16/int：`storage_dtype` 缺省 = `dtype`（**向后兼容**，现有 4 份 spec 零改）。
  - 精确落实 canonical harness 职责#2/#3：gen_cases 内从 fp32 源 `v` → round 到 bf16 网格 `v_bf` → 一份存 uint16 物理字节（kernel 输入）、一份 op(v_bf) 存 fp32（golden），**两份分造、绝不把 golden 的 fp32 buffer reshape/reinterpret 成 kernel 输入**。
  - **物理 dtype 校验点必须改**（codex#2 命门）：`run_mock`（repo_adapter.py:51）与 `run_new_example`（:127）现硬断言 `str(arr.dtype)==inp["dtype"]`——改为比 `inp.get("storage_dtype", inp["dtype"])`；逻辑 dtype 另行校验。

**（3）attr 矩阵 = spec 显式声明的 `attr_matrix`（列表语义，非笛卡尔魔法·codex#7/#9 修订）**——`attr_matrix` 是一个**显式 attr 字典列表**（hand-authored variants），每个 dict 在**一个代表 (dtype,shape)** 上产**恰好一条** case → 期望 case 数 = `len(attr_matrix)`（写进单测断言）。缺省无 `attr_matrix` → 维持现默认单值行为。golden 用该 case 的 attrs 算（golden_isclose 已按传入 attrs 算、天然 per-case）。runner 侧已逐行读 attr（isclose runner 已 parse rtol/atol/equal_nan）——**只 gen_cases 改，runner attr 无需动**。**不引用 rule-catalog §0 三轴**（§0 三轴是 dtype/special_value/layout、非 attr 选择算法——codex#9 指正）。

**（4）语义化稳定 case_id（codex#12 修订）**——弃 `{op}_{i:03d}` 索引 id（扩面重排会打乱旧 id、毁 defect 定位/历史对比）。改 `{op}_{dtype}_{shapetag}_{kind}[_a{k}]`（如 `isclose_float16_4x4_pairfar_a1`）；碰撞时确定性追加 `_2/_3`。仍满足门的 id 唯一 + 一一对应，且新增 dtype/attr 不动旧 id。

**（5）int/bf16 的判据走 per-case `compare`（codex#5 修订）**——verify_mode 现为 spec 级；新增 case 级 `expected.compare`（`exact_equal` | `rel_err`），让 `_metric`/`_judge_precision` per-case 选。规则：**int → `exact_equal`**（§1.1）；**Sign/Neg 的 bf16/fp16 → 也 `exact_equal`**（sign∈{-1,0,1}、neg 精确取负，输出在 bf16 网格上精确可表示→绕开 bf16 阈值权威难题）；genuinely-lossy 数值算子的 bf16 阈值须来自 policy/ascendoptest（本轮无此类算子、留 gap）。

**⚠ 载重标注**：`machine-verifiable-acceptance-gate` 是 **verified 非 canonical、未 settle**——其「id==evidence id / threshold 三处一致 / kernel_only」当约束用但在单测实测复核，不当既成事实。`gate-checks-evidence-integrity-not-verdict`（proposed）已明载：**门只查证据完整性、不重判 verdict**；合法精度 fail 由 validator 判 `FAIL(精度)`、门仍 PASSED。其余 3 页（契约链 / harness 职责 / rule-library）canonical、当事实。

## canon 依据（grounding）
- **canonical（当事实）**：
  - `acceptance-contract-evidence-chain.md`——case_id 贯穿脊柱、统一契约含 `dtype`、口径以 spec 为权威（attr_matrix 走 spec 声明即据此；storage_dtype 是契约扩展、需同步 schema）。
  - `generated-harness-responsibilities.md`——职责#2 layout 字节契约（X_logical 逻辑摆喂 golden / X_bin 物理字节喂 kernel、分两份造禁共用 reshape）+ 职责#3 同源双表示；bf16 的 fp32-golden/uint16-bin 直接受此约束。
  - `primitive-to-case-rule-library.md` → reference `rule-catalog.md` §1.1（int exact_equal / bf16 7b / overflow_at）、§1.3（inf_nan mandatory_if / cast_boundary tie）、§0（per_case_tags：case_origin/rule ref）。
  - ADR `task-spec-authoritative-over-pr` + `verify-spec-pr-correspondence-before-acceptance`——**dtype 全集从任务书推、不猜**→ Track B 须任务书+用户批。
- **verified/proposed（存疑·载重前实测复核）**：
  - `machine-verifiable-acceptance-gate.md`（verified）——三条完整性约束，扩面后靠单测 + mock 实证，不空信。
  - `gate-checks-evidence-integrity-not-verdict.md`（proposed）——门不重判 verdict；**已给实证样例**：`--defect`（真精度 fail）→ 门 PASSED、总体 `FAIL(精度)`、exit 1；篡改成子集 → 门 FAILED、`BLOCKED`。**本轮 defect 测试严格照此拆**（codex#3）。
  - acc-runner `SKILL.md`——runner 入口/dtype **从算子自带 example 抠、不猜**；「新算子 dtype 超 runner 支持（bf16/int8）→ 扩 gap，别硬塞」→ Track C 不手写 runner。
- **代码现状**：`gen_cases.py`（`_DTYPES` fp32/16/int32、plan 只叉 fp32/16 两 shape、`attrs` 只取 default、id `{op}_{i:03d}`）；`repo_adapter.py`（`_NP` 仅 fp32/16、:51 与 :119-129 硬断言 dtype、:134 exp_dt 按 verify_mode、mock 不走 bin 物理化路径）；`validator.py`（`_judge_precision` 按 spec 级 verify_mode、无 per-case compare）；`validate_acceptance_state.py`（`_case_key` 按 inputs[0] 的 dtype/shape）；`new_example/` 仅 sign/equal/isclose 三 runner、**无 neg_runner**。

## files（改动清单·已据 codex#4 纠偏）
| 路径 | 动作 | 目的 |
|---|---|---|
| `plugin/acc-common/gen_cases.py` | edit | 加 `_f32_to_bf16_uint16`/`_bf16_round`（round-half-even，含 ±0/subnormal/NaN/inf 处理，注明字节序前提）；扩 `_DTYPES`（int16、bfloat16→uint16 物理）；plan 从 `spec.attr_matrix` 显式列表展开（每 variant 一条 case、代表 dtype/shape）；语义化稳定 id + 碰撞 guard；输入项加 `storage_dtype` + `expected.compare`（int/bf16 numerical→exact_equal）+ `case_origin`/`rule_ref`；int/bf16 golden 造法（bf16 存 fp32-on-grid、int 原生）；int isclose 的 near/far 在整数网格上构造并断言各命中 True/False；IsClose 的 `nan_pair` kind（仅 float、equal_nan 语义）；X_logical(.npy) 与后续 X_bin 分造、禁共用 reshape |
| `plugin/acc-common/repo_adapter.py` | edit | `run_mock`/`run_new_example` 的 dtype 校验改比 `storage_dtype`；扩 `_NP`（int32/int16）+ bf16 特判；**抽出纯函数 `materialize_input(arr, meta)`/`readback_output(bin, meta)`**（bf16: uint16 收发→decode fp32 比 fp32 golden；int: 原生；供本地 round-trip 单测·codex#9）；`_metric` 按 `expected.compare` per-case 选（exact_equal / rel_err）+ **NaN/inf-aware**（若 golden 含 non-finite 显式标而非静默产 NaN）；保广播 materialize 为独立 X_bin（不与 npy 共 buffer） |
| `plugin/acc-common/validator.py` | edit | `_judge_precision` 认 per-case `expected.compare`（exact_equal→按 exact 判、rel_err→按 numerical 判）；threshold 三处一致校验兼容 compare=exact_equal（thr=0） |
| `plugin/acc-common/test_fixtures/sign_int16_bf16.spec.json` 等 | create | **非权威 fixture spec**（显式标 `"_fixture": true`）声明 int16/bf16 + attr_matrix，供 Track A 本地 mock 端到端验证，**不碰权威 4 spec** |
| `plugin/acc-common/test_gen_cases_dtype_attr.py` | create | 单测：bf16 round 正确性（tie/±0/subnormal/NaN/inf/字节序）；`materialize_input`/`readback_output` 各 dtype **round-trip**（codex#9，无需 NPU）；int/bf16 golden 合法；attr_matrix 产 `len(attr_matrix)` 条且 golden 用该 attrs；equal_nan=True 有 NaN case；语义 id 稳定+唯一；mock 端到端 caseset id==evidence id；机器门三级 PASSED；`--defect`→validator FAIL/exit1（门 PASSED）；子集/篡阈值/篡 scope→门 FAILED（codex#3）；坏/混/storage 不符 dtype 被拒 |
| `doc/oprunway-spec-schema.md` | edit | 补 `attr_matrix`（列表语义·默认值·合法值类型·兼容策略）+ `storage_dtype`/per-case `compare`/`case_origin` 契约（codex#8） |
| `plugin/skills/acc-spec/…`（抽取规则+自检清单） | edit | acc-spec 增「何时/如何产 `attr_matrix`」抽取规则 + 自检项 + 示例（codex#8·spec 权威链完整） |
| `plugin/acc-common/specs/sign.spec.json`（Track B·**gated**） | edit | 视用户批+任务书，把 int16 提进 `params[].dtype`（Sign 已 `change.dtypes_added:[int16]`）；**默认不动**，留 open_decision |
| `doc/oprunway-changes-brief.md` | edit | 倒序追加一两句大白话（CLAUDE.md #4） |
| `doc/oprunway-todo.md` | edit | 更新 P1-5 进度：Track A 本地完成 / Track B 挂用户批 / Track C（runner）挂真机+pr_facts；Neg int-min/uint8/int64 语义列 out-of-scope |

> **删除原 plan 的 4 份 runner.cpp edit 条目**（codex#4/#15）：neg_runner 不存在、且按 acc-runner 纪律 runner dtype 须从 example 抠+真机验证，本轮不手写。runner dtype 扩面锚进 todo/gap。

## steps（步骤）
1. **锁范围与契约（先抛方案·CLAUDE.md #1）**：向用户列 open_decisions 1-6；重点让用户拍板**契约扩展**（storage_dtype / per-case compare / 语义 id 迁移）与 **Track A/B/C 拆分**。点头后才动代码。默认建议：位级 uint16 bf16 + 首批 int32/int16/bf16 + fixture spec 走 Track A（权威 spec 暂不动）+ 语义 id 迁移一次到位。
2. **gen_cases：bf16 helper + dtype 表**：`_f32_to_bf16_uint16`（fp32 位模式 round-half-even 截高 16 位、进位可正确溢为 inf、NaN 保 quiet payload、±0 保符号、subnormal 处理）；注明「.tofile 落盘假定 little-endian host（远端 NPU 同序）」；扩 `_DTYPES`。
3. **gen_cases：语义 id + storage_dtype + case_origin/rule_ref**：改 id 生成 + 碰撞 guard；输入项写 `dtype`(逻辑)/`storage_dtype`(物理)/`case_origin`/`rule_ref`。
4. **gen_cases：attr_matrix 显式列表展开**：读 `spec.get("attr_matrix")`（形如 `[{equal_nan:false}, {equal_nan:true}, {rtol:1e-2,atol:1e-3}]`）；每项一条 case（代表 dtype/shape）、golden 用该 attrs；缺省→现默认单值。
5. **gen_cases：per-op special-value 适用表 + int 数据规则**：
   - IsClose（bool 输出）：`nan_pair` kind（对齐位 NaN→equal_nan 决定 True/False；错位 NaN→False），仅 float；cast_boundary tie（bf16/fp16 中点）。
   - Equal（bool 输出）：NaN 输入 → golden False（安全，保留）。
   - Sign/Neg（numerical）：**本轮不注入产生 NaN/inf 的 golden**（specs 未声明 inf/nan 传播→§1.3 非 mandatory·codex#6/#11）；int 数据**排除 dtype 最小值**（避免 np.negative 溢出未定义·codex#14）；bf16/fp16 cast_boundary 仅在输出可精确表示时用。
   - int IsClose/Equal near/far：整数网格上构造（near=相等整数→True、far=差>atol→False）并断言各命中（codex#13）。
6. **repo_adapter：storage_dtype 校验 + 纯函数收发 + per-case compare + NaN-aware metric**：抽 `materialize_input`/`readback_output`；改 :51/:127 dtype 校验；`_metric` per-case + non-finite 显式处理。
7. **validator：per-case compare**：`_judge_precision` 认 `expected.compare`。
8. **写 fixture spec + 本地 round-trip 单测**：`materialize_input`/`readback_output` 各 dtype round-trip（**这是证明 bf16 uint16 收发正确的地方，不是 mock**·codex#9）。
9. **mock 端到端自测**：对 fixture spec 跑 `python run_workflow.py <fixture> --mode mock`——用例数增长、evidence 一一对应、门三级 PASSED、总体 PASS；`--defect`→`FAIL(精度)`+exit1（门 PASSED）；手工篡 evidence 成子集/篡阈值/篡 scope→门 FAILED+`BLOCKED`（codex#3）。
10. **写 test_gen_cases_dtype_attr.py 全绿**（覆盖 acceptance 全部条目）。
11. **过门与落文**：代码产物过 `cc-suite:audit-fix`（Skill 工具，9 维审→修→验）；散文（brief/todo/schema/acc-spec 文本 + 若写 bureau 拟入文本）过 `codex exec` CLI 定制审；doc brief 追加、todo P1-5 更新；契约扩展若定为 durable，走 `bureau:note`→`compile`→`review` 入 canon，绝不手设 canonical。
12. **Track C 锚定（不实施）**：把 4 算子 runner 的 int/bf16 分支 + neg_runner 创建，写进 `doc/oprunway-todo.md` gap，注明「据 pr_facts 的 example/op_def 支持矩阵生成、真机验证」，本轮不写 C++。

## gates（门禁·codex#16 给可执行形态）
- **代码门** → `cc-suite:audit-fix` skill（`Skill` 工具调用，9 维代码审→修→验循环）。作用于 gen_cases.py / repo_adapter.py / validator.py / test 脚本 / fixture spec 契约。**不可用时**：退回人工 9 维走查并显式标注「audit-fix 未跑、人工代替」。
- **散文门** → `codex exec` CLI（Codex CLI；nlpm 1.1.1+ 已移除旧 MCP→一律 CLI）。作用于 brief/todo/spec-schema/acc-spec 文本。**不可用时**：标「codex 未审、人工代替」。
- **机器门** → `python validate_acceptance_state.py --stage task1|task2|task3 --dir <out>`，三级门必须在**扩面后的 fixture caseset** 上 mock 端到端 PASSED（防跑子集/放宽阈值/混 e2e），经 run_workflow 硬 blocker 验证。**注**：此门 verified tier，本 todo 用单测实证其对扩面后 caseset 仍成立；且**门不重判 verdict**——defect 的合法精度 fail 归 validator。
- **bureau 门**（仅当把 storage_dtype 契约 / bf16 表示 / dtype 范围决策写成 durable canon 时）→ 先 codex 审拟写文本，再 `bureau:note`→`compile`→`review`，绝不手设 canonical。
- **CLAUDE.md #1/#4**：契约扩展 + 语义 id 迁移属设计变更，**先抛方案经用户点头才落地**；所有 doc 产出进 `doc/`、改动同步进 `doc/oprunway-changes-brief.md`。

## blocked / deps（卡点）
- **本地能立刻做（Track A，全绿）**：gen_cases 扩 dtype（int32/int16/bf16 位级）+ attr_matrix 显式展开 + per-op special-value + 语义 id + storage_dtype/compare 契约；repo_adapter mock 收发 + 纯函数 round-trip；validator per-case compare；fixture spec + 单测 + run_workflow mock 端到端 + 机器门实证——**全 numpy、无 NPU**（bf16 位级 helper，已实测本机无 ml_dtypes / numpy 无原生 bf16）。**前提**：契约扩展方案先过用户确认门。
- **卡真机 NPU（Track C）**：① runner.cpp 新 dtype 分支 + neg_runner 创建——按 acc-runner 纪律须**从算子自带 example/op_def 抠入口与支持 dtype、不猜 header**（codex#15），且要 `pr_facts.json`（fetch_source 产）；② 被测 aclnn 算子在目标 arch 是否真支持 int16/bf16——真机能力问题、猜不得；③ int/bf16 真 NPU 数值校验（new_example 跑通 + msprof kernel-only）。
- **卡任务书权威 + 用户批（Track B）**：把新增 dtype 提进权威 spec `params[].dtype` 触碰 canon 硬约束「dtype 从任务书推不猜」；Sign 已 `change.dtypes_added:[int16]`、Neg gap 已文档化→有据但仍须任务书原文 + 用户拍板。Neg 的 int-min 取负 / uint8 回绕(256-x) / int64 溢出语义→本轮 **out-of-scope**（neg.spec `task_pr_gaps` 已列 uint8 未覆盖）。

## acceptance（验收标准·已按 codex 收敛）
1. gen_cases 对首批新增 dtype（int32/int16/bf16）产合法 golden：bf16 经 round-half-even helper 落网格并以 uint16 物理存储、`golden.npy` 为 fp32-on-grid；int 原生 + `compare=exact_equal`；**在 fixture spec 上**（非权威 spec）验证。
2. spec 声明 `attr_matrix`（显式列表）时产**恰好 `len(attr_matrix)` 条**独立 case、golden 用该 case attrs、equal_nan=True 有 NaN 数据 case；缺省无 attr_matrix 时行为与现状**一致（向后兼容）**。
3. `run_workflow.py <fixture> --mode mock` 对扩面后 caseset：id==evidence id 一一对应、机器门 task1/2(/3) 全 PASSED、validator 精度全 pass、总体 PASS；`--defect`→总体 `FAIL(精度)` + exit 1（**门 PASSED，不被 BLOCKED 盖**）；手工篡成子集/篡阈值/篡 scope→门 FAILED + `BLOCKED`。
4. `materialize_input`/`readback_output` 纯函数对 fp32/fp16/int16/int32/**bf16-uint16** 各 dtype **本地 round-trip 单测通过**（证明物理收发正确，非靠 mock）。
5. test_gen_cases_dtype_attr.py 全绿：bf16 round（tie/±0/subnormal/NaN/inf/字节序）、int/bf16 golden、attr 变体计数、NaN case、**语义 id 稳定+唯一**、one-to-one、门三级 PASSED、defect/篡改分流正确、坏/混/storage 不符 dtype 被拒。
6. layout 字节契约守住：代码审确认 X_logical(npy) 与 X_bin(bin) 分两份造、无共用 reshape；bf16 golden(fp32) 与 kernel 输入(uint16 字节) 双表示独立生成。
7. 每 case 带 `case_origin`/`rule_ref`（rule-catalog 引用 / attr_matrix 下标），可追溯（codex#18）。
8. doc brief 追加、todo P1-5 更新、spec-schema + acc-spec 抽取规则同步；散文过 codex 审、代码过 audit-fix；**runner（Track C）诚实标 blocked-待 NPU+pr_facts**，不伪装本地写好。

## open_decisions（开放决策·须用户拍板）
1. **契约扩展是否采纳**（storage_dtype 逻辑/物理拆 + per-case `compare` + 语义化稳定 id 一次性迁移）——推荐采纳（是解 codex#2/#5/#12 的根，且向后兼容）；代价=一次 id 迁移（旧 ephemeral 报告 id 变，`reports/` 已 gitignore、可接受）。
2. **Track 拆分是否认可**（A 本地能力 / B 权威 spec dtype 挂任务书+批 / C runner 挂真机+pr_facts）——推荐；避免「不改 spec 又要覆盖」的自相矛盾（codex#1）。
3. **bf16 表示**：位级 uint16 helper（零依赖，对齐「只借方法论不引依赖」，推荐）vs 引 `ml_dtypes.bfloat16`（本机未装、违零依赖）。
4. **dtype 首批范围**：int32/int16/bf16（推荐）vs 一次到位 Neg 全集 {int8,int16,int32,int64,uint8,bf16}（含 uint8 回绕/int64 溢出/int-min 特例，更重）。
5. **attr_matrix 语义**：显式 attr 字典列表（`len` 决定 case 数，无歧义，推荐）vs per-attr 值列表全笛卡尔（易爆炸、codex#7 指出的歧义源）。
6. **是否现在按任务书补权威 spec dtype（Track B）**——触碰硬约束「dtype 从任务书推」，须任务书原文 + 用户批；默认本轮只做 Track A 能力扩面。
7. **是否把契约扩展 + dtype 范围写入 canon**（bureau capture→compile→review）——durable，建议定稿后再 capture，避免过早固化。


---

## T8-gpu-benchmark-task3 — GPU 标杆接入（Task 3）——consumer 侧解析+对比+canonical 状态机全量落地（据 codex 审计修订终版）

**codex 总判**：codex_ran_ok=true（codex 真跑成、verdict=needs-revision，本 plan 已经外审）。5 条 high 全部解决：H1 改 data-driven opt-in 零契约变更、H2 落全量 canonical 枚举、H3 给出 expect_source 接口去掉 baseline.get() 硬崩、H4 加「wait 绝不显 PASS」测试护栏、H5 改完整输入签名+fingerprint 对齐。11 medium 全吸收（M10 部分驳回其「不可复现」定性、澄清 cc-suite/bureau 为环境提供的 Claude Code 插件 skills、并点明仓内确定性门=validate_acceptance_state.py+unittest）、2 low 全吸收。**无残留未解 high**。仍有 4 项需人工 sign-off 才动手（见 residual_risks/open_decisions），非 codex 未解问题，而是本项目「先方案后落地 + P0 merged 代码变更须点头」的既定纪律。

**可实施性**：no（暂不能直接开工）。技术上 consumer 侧全部逻辑（契约/解析器/两 BLOCKED 态/全量枚举/机器门扩展/run_workflow 接线/入口文档/mock/单测/端到端）**本地即可全做、无需真 NPU/GPU/VPN**；但卡在两类前置：①CLAUDE.md #1 init 阶段本 plan 须先经用户点头；②4 项 open_decision 触及 P0 已 merge 代码（overall 全量枚举、退出码表、机器门语义变更）与 bureau 重验，须用户 sign-off。用户对 open_decision#1（全量 vs 最小枚举）+#2（退出码/PASSED_WITH_RISK 是否可交付）拍板后，即可按 steps 1→9 落地；真 GPU 数据与移植类真机对比另待外部。

**残余风险**：1. **触及 P0 已 merge 代码**：全量 overall_state 枚举 + 退出码表 + 机器门语义扩展改动 run_workflow/validate_acceptance_state（现有 4 spec 行为须证零回归、原 28 单测须全绿）——风险在回归，靠「码表 0/1 不动、新态用新码 + 端到端+单测钉死」缓解，但需用户 sign-off（open_decision#1/#2）。2. **bureau verified claim 失效**：改门代码使 machine-verifiable-acceptance-gate(verified)+_verify.json 哈希 stale，须走 compile/review 重验期间该 claim 处 stale 态。3. **真数据未到**：GPU 标杆确切字段编码 provisional，contract_version 迭代可能需微调解析器；本项目无移植类 GPU 算子在册，端到端只走 mock（真 NPU↔GPU 对比未经真机）。4. **canon 未 settle 的张力**：perf_baseline_source 默认 gpu_external vs 从任务书推导，属 proposed·待 bureau:review，本 todo 采 opt-in 顺其意但不裁。5. **PASSED_WITH_RISK 语义**：定为 exit 4/需人工 CP，若用户认为应可交付(0) 需调整。

**据 codex 修订**：逐条处理 17 条 codex issues（5 high 全解、11 medium + 2 low 大多吸收，少量降级/驳回并说明）：

【HIGH】
H1(consistency·spec.perf_baseline_source 冲突)：**吸收**。核过代码——现有 spec 只有 `perf.baseline` 字符串字段（4 spec 全 "tbe"），plan 原方案自相矛盾（一边说新字段待拍板、一边用它触发）。改为**零 Layer0 变更的 data-driven opt-in**：触发 = `--gpu-baseline` 文件 或 复用现有 `perf.baseline∈{gpu,gpu_external}`（既有字段的新合法值，非新字段）；gpu_demo.spec.json 也改用 `perf.baseline="gpu_external"`。新增 `perf_baseline_source` 字段降为 open_decision#4。

H2(consistency·只映射 2 态)：**吸收（扩大 scope）**。核过 run_workflow 现用 ad-hoc 中文串（PASS/FAIL(精度)/性能未达成…）。T8 目标本就是「落 canonical 状态机」，半落会导致词汇不一致。改为**落全量 canonical overall_state 枚举**（新增 overall_state() 映射 6 态 + 2 个明标「非 canon 元态」）。因触及 P0 merged 代码 → 列 open_decision#1 求 sign-off，并给「最小」退路。

H3(feasibility·baseline 必传即 .get() 会崩)：**吸收**。核实 perf_compare line23 `baseline.get("source")`、run_workflow 无 baseline 时造 mock。改签名 `perf_compare(..., expect_source=None)`、移除对 baseline 的无条件 .get()、`baseline=None` 时按 expect_source 产 wait 状态不崩；run_workflow 缺文件时传 baseline=None+expect_source。给出确切接口落点。

H4(risk·wait 当无 error 会显 PASS)：**吸收（关键安全）**。设计护栏：gate_task3 放行 wait 仅代表「NPU 证据完整」且**仍校验 NPU 侧完整性**；整体裁决由 run_workflow 映射 BLOCKED_WAIT_GPU_BENCHMARK(非 PASS)+exit 2，由端到端测试**钉死 acceptance.json 绝不因缺 GPU 显 PASS**。未把机器门改成 tri-state（保持其 binary 完整性门定位不动，安全靠 overall 层+退出码+测试）。

H5(completeness·单 dtype/shape 无法证同一 case)：**吸收**。核实 caseset 是多输入 `inputs:[{name,dtype,shape}]`+`attrs`、含二元广播。改为 GPU baseline per-case 携**完整输入签名**、parser 用 `case_fingerprint`（sorted inputs+attrs 哈希）交叉核对，签名不符→error。

【MEDIUM】
M1(14 vs 15 字段 + clock_power 命名)：**吸收**。canon 实列 15 项，plan 原写「14」已纠正为 15；契约定 schema_version/字段名 snake_case/`clock_power_state` 带 `clock-power` 等别名/顶层 vs per-case 层级。
M2(GPU baseline 集合关系不明)：**吸收**。定「恰好覆盖全部性能维用例」：缺→该行 blocked、多→拒 extra，parser+perf_compare+gate 加校验+测试。
M3(静态 mock fixture 漂移)：**吸收**。单测改为运行时 gen_cases 产 caseset→程序化构造 baseline；mock_gpu_baseline.json 仅作人读示例 + 防漂移断言。
M4(run_workflow --gpu-baseline 步骤缺)：**吸收**。显式列 argparse/run() 参数/provenance 落盘/CLI 集成测试（缺文件 exit2、坏 scope exit3、风险 exit4、正常 0）。
M5(parse 返回值/warning 结构未定义)：**吸收**。定 `{code,severity,case_id,field,message}` 列表、落 gpu_baseline_parse_report.json、hard error→baseline None 阻断、warn→放行记录。
M6(warmup/iters warn 不卡)：**吸收**。sub-policy→记 policy_risk(warn)、不允许干净 PASSED、下游升 PASSED_WITH_RISK（正好接 H2 边角态）。
M7(step1 先写后审违 #5)：**吸收**。step1 改为「先草拟契约文本→codex exec 审→再写盘」。
M8(_verify.json 哈希失效)：**吸收**。改门代码使 machine-verifiable-acceptance-gate(verified) claim stale→加 bureau capture→compile→review 重验步骤，`_verify.json` 哈希由 compile/verify 重算，明禁手改 cabinet/_verify.json。
M9(退出码无具体值)：**吸收（保守版）**。给码表 0=PASSED/1=FAILED_*|门未过/2=WAIT_GPU/3=INCOMPARABLE/4=WITH_RISK|NEEDS_REVIEW；0/1 不动、新态用新码（对现有 CI 零冲击）；PASSED_WITH_RISK 是否可交付列 open_decision#2。
M10(cc-suite:audit-fix/bureau:note 仓内不可执行)：**部分吸收 + 部分驳回**。驳回「不可复现」的定性——它们是本环境真实可用的 Claude Code 插件 skills（经 Skill 工具/slash 调用），codex 作为外部 CLI 看不到故误判。吸收其合理内核：plan 显式标注它们是环境提供的插件 skills（非仓内脚本），并点明**仓内可复现的确定性门 = validate_acceptance_state.py + python3 -m unittest**，任何执行者据此可复核。

【LOW】
L1(入口文档漏更)：**吸收**。核实 plugin/AGENTS.md、README.md、commands/op-acceptance.md 均存在→加同步步骤。
L2(契约被外部方直接覆盖)：**吸收**。契约版本化（schema_version/contract_version）、报告记录 version+hash、旧 parser 保留、变更走版本递增不覆盖。

无驳回的 high；唯一部分驳回是 M10 的「不可复现」定性（澄清为 codex 对 Claude-Code-native 工具的盲区），其合理内核已并入 plan。

### 终版 plan

# T8 · GPU 标杆接入（Task 3）—— consumer 侧解析 + 对比 + canonical 状态机落地（终版·已吸收 codex 审计）

## Goal（目标）
在现有「NPU↔内置 TBE」之外补齐 **consumer 侧** 能力：解析外部 GPU 标杆 JSON（按 canon 字段契约）→ 按
`case_id` + **完整输入签名** 对齐 → 复用 `perf_compare` 出 NPU↔GPU 报告；并把 **canonical Task 3 状态机
的全部结论态**（不止两个 GPU 出口）真正落进代码，让总体裁决词汇与 canon 一致。全程 mock GPU 数据本地自测，
真数据待外部方给。

## Approach（取舍与总思路）
对齐 canon、不重推已 settle：

1. **状态机是 canonical**（`task3-state-machine.md`，reviewed 2026-07-02）→ 直接按它实现、不改语义。
   ⚠ 据 codex：不能只落 2 个 BLOCKED 出口而把其余结论留作 ad-hoc 中文串 → **落全量 canonical
   `overall_state` 枚举**（`PASSED / FAILED_PRECISION / FAILED_PERFORMANCE / BLOCKED_WAIT_GPU_BENCHMARK`
   + 边角 `PASSED_WITH_RISK / BLOCKED_INCOMPARABLE_TIMING_SCOPE`），另加两个明确标注「非 canon、OpRunway
   元态」的 `BLOCKED_EVIDENCE_INCOMPLETE`（门未过/NPU 采集失败）与 `NEEDS_REVIEW`（兜底）。
2. **证据链「GPU 只作性能标杆非精度 oracle」是 canonical** → consumer 只碰性能、不碰精度。
3. **触发方式改为纯 data-driven opt-in，零 Layer 0 契约变更**（据 codex H1）：**不新增 `spec.perf_baseline_source`
   字段**。GPU 分支触发 = ①CLI 传 `--gpu-baseline <file>`（数据驱动）；或 ②复用**现有** `spec.perf.baseline`
   字段取值 ∈ {`gpu`,`gpu_external`}（现有 4 spec 全是 `tbe`，零回归；`gpu_external` 只是这个既有字符串字段
   的一个新合法值，不是新字段）。是否把 canon 里更丰富的 per-case `perf_baseline_source` 枚举正式引入 spec
   契约，留独立 bureau/Layer-0 决策（open）。
4. **ratio 数学不动**：`ratio = baseline_us / npu_us`、达标 = `ratio ≥ spec.perf.target_ratio`。GPU 移植类
   的 0.5–0.8× 同样成立（target_ratio 由 spec 定，如 0.5 = 允许 NPU 最多慢 2×）。T8 的活是**解析器 +
   状态出口 + 全量枚举映射 + mock + 测试**，不是重写比较核。
5. **不把 gpu_external 强加到现有 TBE-baseline 社区算子**（perf-baseline-by-reference-source.md，proposed·未
   settle：这批任务基线其实是 TBE/参考源、GPU 仅移植类需，且「是否让 baseline 从任务书推导」留人工复核）→
   opt-in 顺其意，TBE 路径行为零变化。

## Canon 依据（含 trust tier）
1. `canon/architecture/task3-state-machine.md` — **canonical**（当事实）：4 核心态
   `PASSED/FAILED_PRECISION/FAILED_PERFORMANCE/BLOCKED_WAIT_GPU_BENCHMARK` + 边角
   `PASSED_WITH_RISK`（放行但有风险，需人工 CP）/`BLOCKED_INCOMPARABLE_TIMING_SCOPE`。→ 本 todo 落**全量**。
2. `canon/architecture/acceptance-contract-evidence-chain.md` — **canonical**：`case_id` 贯穿证据链；
   `perf_baseline_source` 默认 `gpu_external`、经 Task3 对比；GPU 只作性能标杆非精度 oracle。
3. `canon/decisions/0006-performance-timing-scope.md` — **proposed·未 settle**：`timing_scope` 必填枚举
   （`kernel_only/device_e2e_no_h2d_d2h/host_e2e_with_h2d_d2h`）、NPU↔GPU 必须同 scope、不一致→不出结论
   （`BLOCKED_INCOMPARABLE_TIMING_SCOPE`）、默认 kernel-only 取 median/p50、warmup≥10 iters≥30 policy。
   **GPU 标杆最小字段契约实为 15 项**（codex M1 纠正原「14」）：`case_id/device/dtype/shape/attrs/timing_scope/
   warmup/iters/sync_policy/statistic/unit/value/tool/clock-power/data_transfer_included`。
4. `canon/architecture/perf-baseline-by-reference-source.md` — **proposed·未 settle**：社区任务基线随参考源
   （多为 TBE、移植类才 GPU）→ 直接约束本 todo 的 opt-in 取向；张力留人工复核、不在代码里单方定。
5. `canon/decisions/0008-reuse-ascendoptest.md` — **proposed·未 settle**：1.2× 分母 = `perf_baseline_source`
   默认 gpu_external、比值由 validator/perf_compare 算。现有 4 spec（sign/neg/isclose/equal）全 `perf.baseline=tbe`
   → 佐证 opt-in 是当下正确落法。
6. `canon/architecture/machine-verifiable-acceptance-gate.md` — **verified**（含 `canon/_verify.json` 钉死
   `validate_acceptance_state.py` + 其测试的哈希）。→ 本 todo 改门代码会使该 verified claim **stale**，须走
   bureau capture→compile→review 重验（codex M8），**不得手改 `_verify.json`/cabinet**。

## Files（改动清单）

**新增（Layer 0/1 + testdata + 测试，均纯本地）**
- `plugin/acc-common/gpu_baseline_contract.json`（create）— Layer 0 契约，机读化 canon **15 项**字段：
  `schema_version` + `contract_version` + 每字段 required/optional + 枚举取值 + 单位换算表（ns/us/ms/s→us）+
  顶层 vs per-case 层级 + 字段名规范（snake_case，`clock_power_state` 带 `clock-power`/`clock`/`power_mode`
  别名映射，codex M1）+ 示例；provenance 指向 ADR 0006（proposed）。**版本化**：契约变更走 `contract_version`
  递增、旧 parser 保留、报告记录所用 version+hash（codex L2，禁「外部方直接覆盖历史契约」）。
- `plugin/acc-common/gpu_baseline.py`（create）— 解析+校验器。签名
  `parse_gpu_baseline(path, caseset) -> (baseline|None, parse_report)`：
  - 逐字段校验（缺/类型/枚举）；`device` 必须是 GPU（非 NPU）；`timing_scope` ∈ 3 枚举；
    `unit`→us 归一；`data_transfer_included` 与 scope 自洽；
  - **按 `case_id` + 完整输入签名对齐**（codex H5）：per-case 携带 `inputs:[{name,dtype,shape}]` + `attrs`
    的**完整签名**（不是单个 dtype/shape），parser 用 `case_fingerprint(case)`（对 caseset 该 case 的
    sorted inputs (name,dtype,shape) + sorted attrs 求稳定哈希）交叉核对，签名不符→error（防拿别 shape 的
    GPU 数字冒充可比、覆盖二元/广播/未来 layout/stride/稀疏）；
  - **集合语义**（codex M2）：GPU 标杆须**恰好覆盖全部性能维用例**——缺某性能 case→该 case error/该行 blocked；
    多出 caseset 没有的 case_id→error（拒 extra）；
  - **计时 policy 风险**（codex M6）：`warmup<10 / iters<30 / statistic∉{median,p50}` → 记
    `severity=warn` 的 `policy_risk`（不硬崩、但**不允许干净 PASSED**，下游据此升 `PASSED_WITH_RISK`）；
  - **结构化 parse_report**（codex M5）：error/warn 均为 `{code, severity, case_id, field, message}` 列表，
    落 `gpu_baseline_parse_report.json`；hard error→返回 `baseline=None`（阻断对比、不静默）；warn→放行但记录。
  - 输出内部 baseline dict `{source, scope, contract_version, per_case:[{case_id,us,env,policy_risk?}]}`，
    与 `perf_compare` 现有消费格式一致。**绝不 raise 崩溃**。
- `plugin/acc-common/testdata/mock_gpu_baseline.json`（create）— **人读示例**（A100、kernel_only、us、median、
  warmup 20/iters 50、data_transfer_included=false）。⚠ 据 codex M3：**单测不静态依赖它**，改为运行时先跑
  `gen_cases` 产 caseset、再据 caseset 的性能 case 程序化构造 baseline（防 fixture 漂移）；本文件仅作文档示例
  + 一条防漂移断言（若与 live caseset 签名不符则测试标记提醒更新）。
- `plugin/acc-common/testdata/gpu_demo.spec.json`（create）— 仅测试用合成 spec：**复用现有** `perf.baseline`
  = `"gpu_external"`（**不新增字段**）+ `target_ratio`（移植类 0.5–0.8×）。放 testdata 不放 specs/（不暗示真有
  GPU 算子在册）。
- `plugin/acc-common/test_gpu_baseline.py`（create）— 单测：合法→内部 baseline；缺字段/非法枚举/非 GPU
  device/单位换算/scope 不符/签名不匹配/缺某性能 case/多出 case/缺文件挂起/policy 风险/端到端 ratio 报告。
  全程 mock、无 NPU/GPU 依赖，caseset 由 gen_cases 运行时产。

**编辑（核心逻辑，触及 P0 已 merge 代码 → 需 open_decision 里的 sign-off）**
- `plugin/acc-common/perf_compare.py`（edit）：
  - 改签名 `perf_compare(spec, caseset, evidence, baseline, expect_source=None)`；**移除对 `baseline.get()`
    的无条件依赖**（codex H3）——`baseline is None` 时按 `expect_source` 决策，不崩；
  - `expect_source=='gpu_external'` 且 baseline 缺 → summary.status = **`blocked_wait_gpu_benchmark`**
    （per_case 仍带 evidence 的 `npu_us`+`npu_scope`、note「await GPU baseline」；不静默 mock、不判 fail）；
  - per-case scope 不符 → 汇总提升为 summary.status = **`blocked_incomparable_timing_scope`**（原只在 per_case
    留 note）；
  - 消费的 baseline case 带 `policy_risk` 且达标 → summary 加 `risk:["sub_policy_timing"]`（codex M6）；
  - TBE 路径 / `mock_baseline` / 无 `expect_source` 分支**一字不改**（零回归）。
- `plugin/acc-common/validate_acceptance_state.py`（edit）：
  - `_PERF_STATUS` 扩 `blocked_wait_gpu_benchmark` + `blocked_incomparable_timing_scope`；
  - `gate_task3`：`blocked_wait_gpu_benchmark` = **正规挂起、非完整性 FAILED**，但**仍校验 NPU 侧完整性**
    （每性能 case 有 `npu_us` 且 `npu_scope==kernel_only`、覆盖全集），只对「GPU 侧缺」不计 error；
    `blocked_incomparable_timing_scope` → 计 error（不可比）；原 `blocked`（NPU 采集失败）语义不变。
    ⚠ **安全护栏（codex H4）**：门放行 wait 只代表「NPU 证据完整」，**绝不代表整体 PASS**——整体裁决由
    run_workflow 映射为 `BLOCKED_WAIT_GPU_BENCHMARK`（非 PASS）、CLI 非零退出，由测试钉死。
- `plugin/acc-common/test_validate_acceptance_state.py`（edit）：补 wait/incomparable 用例 + NPU 侧完整性仍卡
  的用例；**保证原 28 单测全绿**。
- `plugin/acc-common/run_workflow.py`（edit）：
  - argparse 加 `--gpu-baseline PATH`、`run()` 加 `gpu_baseline=None`（codex M4）；
  - `expect_gpu = (gpu_baseline is not None) or (spec.perf.baseline in {"gpu","gpu_external"})`；
    - 有文件 → `parse_gpu_baseline` → 落 `gpu_baseline_parse_report.json`；hard error→status blocked（携 parse
      errors）；scope 不符→incomparable；OK→喂 perf_compare；
    - expect_gpu 但无文件 → `perf_compare(..., baseline=None, expect_source='gpu_external')` → wait；
    - 否则（tbe/mock）→ 原 `_real_baseline.json` / `mock_baseline` 分支**不变**；
  - **全量 `overall_state` 映射**（codex H2）：新增 `overall_state(gate_passed, prec, perf_summary)` →
    canonical 枚举（见上 Approach·1）；`acceptance.json` 增机读 `overall_state`（canonical）、保留人读 `overall`、
    增 `perf_status`/`risk_flags`/`gpu_baseline`(source+contract_version+parse_report 路径) provenance；
  - **退出码表**（codex M9，保守：0/1 不动，新态用新码）：`0=PASSED`；`1=FAILED_PRECISION|
    FAILED_PERFORMANCE|BLOCKED_EVIDENCE_INCOMPLETE`（含门未过/NPU 采集 blocked，保持现有「非 PASS=1」）；
    `2=BLOCKED_WAIT_GPU_BENCHMARK`；`3=BLOCKED_INCOMPARABLE_TIMING_SCOPE`；`4=PASSED_WITH_RISK|NEEDS_REVIEW`
    （需人工 CP，**不当干净 0 放行**）。

**入口文档同步（codex L1）**
- `plugin/AGENTS.md` / `plugin/README.md` / `plugin/commands/op-acceptance.md`（edit）：补 `--gpu-baseline`
  输入、opt-in 触发、新 BLOCKED 状态与退出码。

**doc + bureau 记账**
- `doc/oprunway-changes-brief.md`（edit）：倒序追加一两句。
- `doc/oprunway-todo.md`（edit）：P2-6 标 consumer 侧本地完成、真数据待外部；记 opt-in 取向 + 未决张力。
- **bureau 重验（codex M8）**：改门后走 `bureau:note`→`bureau:compile`→`bureau:review` 更新
  `machine-verifiable-acceptance-gate` dossier（28 单测→新数、门语义扩展），**由 compile/verify 重算
  `_verify.json` 哈希**（禁手改）；未重验前该 verified claim 视为 stale。

## Steps（顺序，已按 codex M7 修正「先审后写」）
1. **草拟契约文本 → 先审后落**（M7）：先写 `gpu_baseline_contract.json` 的**拟写文本**（15 字段
   required/optional + 枚举 + 单位换算 + 层级 + 别名 + `schema_version`/`contract_version` + 示例），
   **先过 `codex exec` 定制审**确认与 ADR 0006 语义一致、标清 proposed·未 settle，**再写盘**——避免解析器照错契约写。
2. 写 `gpu_baseline.py`：load→逐字段校验→device=GPU→scope∈3 枚举→unit 归一→policy 风险记 warn→
   `data_transfer_included`↔scope 自洽→按 `case_id`+`case_fingerprint` 交叉核对完整输入签名→集合恰好覆盖校验→
   输出内部 baseline 或结构化 parse_report（`{code,severity,case_id,field,message}`），绝不 raise。
3. 造 testdata：`gpu_demo.spec.json`（`perf.baseline="gpu_external"`）+ `mock_gpu_baseline.json`（人读示例）；
   单测里改为 **gen_cases 运行时产 caseset → 程序化构造 baseline**（防漂移）+ 一份 scope=host_e2e 坏样本测
   incomparable + 一份 warmup<10 样本测 policy 风险。
4. 改 `perf_compare.py`：加 `expect_source`、去掉 `baseline.get()` 硬依赖、加两 summary 状态 + `risk`；
   TBE/mock 分支零改。
5. 改 `validate_acceptance_state.py`：`_PERF_STATUS` +2；`gate_task3` wait=挂起非 FAIL 但仍卡 NPU 完整性、
   incomparable=error；补 `test_validate_acceptance_state.py`，跑 `python3 -m unittest` 确认**原 28 + 新用例全绿**。
6. 改 `run_workflow.py`：`--gpu-baseline` + `expect_gpu` 触发 + 落 parse_report + 全量 `overall_state` 映射 +
   退出码表 + provenance；TBE/mock 默认分支保持原样；补 CLI 集成测试（缺文件→exit 2、坏 scope→exit 3、
   policy 风险→exit 4、正常→exit 0）。
7. 写 `test_gpu_baseline.py`：覆盖 step2/4 全分支（合法/缺字段/非 GPU/单位换算/scope 不符/签名不匹配/缺 case/
   多出 case/缺文件挂起/policy 风险/端到端 ratio 报告）。
8. 端到端自测：`gpu_demo.spec.json` + 程序化 GPU baseline 跑 `run_workflow`（mock 模式）→ 验产出
   NPU↔GPU perf_report（逐 case ratio、baseline_source=gpu_external、contract_version）、缺标杆→
   `acceptance.overall_state=BLOCKED_WAIT_GPU_BENCHMARK`+exit 2、坏 scope→`BLOCKED_INCOMPARABLE_TIMING_SCOPE`+exit 3、
   sub-policy→`PASSED_WITH_RISK`+exit 4。**钉死 acceptance.json 绝不因缺 GPU 数据显示 PASS**（H4）。
9. 过双门 + 记账 + bureau 重验：代码产物过 `cc-suite:audit-fix`（Skill 工具/`/cc-suite:audit-fix`，9 维审→修→验），
   契约/doc 拟写文本过 `codex exec`；机器门重跑 task1/2/3 三级 + 全量单测；追加 `doc/oprunway-changes-brief.md`、
   更 `doc/oprunway-todo.md`、同步 3 入口文档；`bureau:note` 捕获「consumer 侧已落地、
   perf_baseline_source-from-taskspec 张力仍 open」并走 `bureau:compile/review` 重验机器门 dossier（M8）。

## Gates（门）
- **开工前门（CLAUDE.md #1）**：init 阶段，本 plan 需先经用户点头才动手。**尤其触及 P0 已 merge 代码**
  （run_workflow overall 词汇 / 退出码 / 机器门语义）→ 见 open_decisions，需显式 sign-off。
- **散文/契约门（`codex exec`）**：`gpu_baseline_contract.json`、doc/入口文档、bureau 拟写文本 → **写入前**先审
  （M7 已把 step1 改为先审后落）。
- **代码门（`cc-suite:audit-fix`）**：`gpu_baseline.py` / `perf_compare.py` / `validate_acceptance_state.py` /
  `run_workflow.py` / 两测试文件 → 9 维审-修-验，复述「发现/改了/剩余风险」。
  说明（codex M10）：`cc-suite:audit-fix`、`bureau:*` 是 **Claude Code 插件 skills**（经 Skill 工具/slash 调用、
  环境提供、非仓内脚本，故 codex CLI 看不到）；**仓内可复现的确定性门是 `validate_acceptance_state.py` +
  `python3 -m unittest`**——任何执行者据此即可复核，不依赖外部人工流程。
- **机器门（`validate_acceptance_state.py`）**：改门后必须重跑 task1/2/3 + 原 28 单测全绿（防回归）；端到端自测须
  证明 wait/incomparable/risk 三态正确落盘、缺 GPU 数据绝不显 PASS。
- **bureau 门（M8）**：改门代码使 `machine-verifiable-acceptance-gate`(verified) claim stale → 走
  capture→compile→review 重验，`_verify.json` 哈希由 compile/verify 重算（禁手改）。canonical 状态机本身不改
  语义（只让代码 conform），不新增 canon；若后续裁「GPU 层可选」或引入 `perf_baseline_source` 字段 → 另起 bureau 写门。
- **落点纪律（#4）**：所有 doc 入 `doc/`、改动同步简表。

## Blocked / 依赖
**现在本地能做**（无需真 NPU/GPU/VPN，mock 跑通）：全部 consumer 逻辑（契约+解析器+两 BLOCKED 态+全量枚举映射+
机器门扩展+run_workflow 接线+入口文档）、mock GPU 数据、单测、端到端自测。
**卡外部/待批**：
1. **真 GPU 标杆数据与确切字段编码**：外部方须确认 `unit/statistic/sync_policy/tool/clock-power` 实际取值串、
   是否 15 字段全给 → 解析器先按 canon 契约实现、精确枚举串标 **provisional**，真数据到手按 `contract_version` 微调。
2. **真 NPU 950 性能数**：移植类真 NPU↔GPU 对比需 950 真机跑 msprof kernel-only（现有 A3 TBE 路径已跑通、
   GPU 路径 NPU 侧数可复用同机制）。
3. **业务触发**：本项目当下 4 spec 全 TBE-baseline、无移植类 GPU 算子在册 → 真业务触发需先有一个
   `perf.baseline=gpu_external` 的真任务。
4. **P0 已 merge 代码语义变更**（overall 词汇/退出码/机器门）与 **bureau 重验** 须用户 sign-off 后动。

## Acceptance（验收标准）
1. `gpu_baseline.py` 解析合规 mock GPU 标杆（15 字段齐）→ 正确内部 baseline；缺字段/非法枚举/非 GPU device/
   **完整输入签名不匹配 caseset**/缺某性能 case/多出 case → 结构化 parse_report（`{code,severity,case_id,field,
   message}`）而非崩溃；`unit ms/ns→us` 换算正确；`timing_scope→scope` 映射正确；契约带 `schema_version`+
   `contract_version` 且报告记录之。
2. `perf_compare` 喂 GPU baseline → NPU↔GPU 报告（逐 case `ratio=gpu_us/npu_us`、`baseline_source=gpu_external`）；
   scope 不一致→`summary.status=blocked_incomparable_timing_scope`；expect gpu 但缺标杆→`blocked_wait_gpu_benchmark`
   （不静默 mock、不判 fail、`baseline=None` 不崩）；消费 sub-policy 数据且达标→summary 带 `risk`。
3. `run_workflow` 吐**全量 canonical `overall_state`**：`BLOCKED_WAIT_GPU_BENCHMARK`(exit 2)/
   `BLOCKED_INCOMPARABLE_TIMING_SCOPE`(exit 3)/`PASSED_WITH_RISK`(exit 4)/`PASSED`(0)/`FAILED_*`(1)；
   TBE-baseline 4 spec 行为与退出码零变化；缺 GPU 数据的 acceptance.json **绝不显示 PASS**。
4. `validate_acceptance_state` 把 wait 当挂起（不误判完整性 FAILED、但仍卡 NPU 侧完整性）、incomparable 当失败；
   新单测 + 原 28 单测全绿。
5. testdata mock 端到端自测通过（mock 模式、无真机），caseset 由 gen_cases 运行时产（无静态 fixture 漂移）。
6. 代码过 `cc-suite:audit-fix`、契约/doc/入口文档过 `codex exec`、机器门 task1/2/3 复跑通过；简表已追加、todo 已更、
   3 入口文档已同步；`machine-verifiable-acceptance-gate` dossier 经 bureau compile/review 重验、`_verify.json`
   哈希已更新。

## Open decisions（需用户/bureau 拍板，代码不单方定）
1. **[最需拍板] `overall_state` 落全量 vs 最小**：默认取**全量 canonical 枚举**（codex H2）——但这改动 P0 已 merge
   的 run_workflow 总体词汇（现为 `PASS`/`FAIL(精度)`/`性能未达成` 等中文串）。**替代=最小**：只加 2 个 GPU
   BLOCKED 态、其余保留旧串。推荐全量（canon-conformant），请 sign-off。
2. **退出码表变更**：`0/1` 保持不动、`2/3/4` 为新态新码（对现有 CI 零冲击，因旧态从不产 2/3/4）。
   `PASSED_WITH_RISK` 是否算可交付？本 plan 定为 exit 4（需人工 CP、不当干净 0）——请确认。
3. **`perf_baseline_source` 张力（canon 未 settle）**：perf-baseline-by-reference-source.md 与 ADR 0006 均
   proposed——这批社区任务基线其实是 TBE/参考源、GPU 仅移植类需；「默认到底 gpu_external 还是从任务书推导、
   Task3 GPU 层是否可选」留 `bureau:review` 裁。本 todo 采 opt-in 已顺其意、不单方定。
4. **是否引入 spec 契约的 `perf_baseline_source` 字段**（Layer 0 变更、会波及 acc-spec skill）：本 todo **不引入**，
   改用现有 `perf.baseline∈{gpu,gpu_external}` 触发；若要引入需另起 bureau/契约门。
5. **外部 GPU 标杆确切字段编码**：`unit/statistic/sync_policy/tool/clock-power` 实际取值串待外部方确认，解析器先
   按 canon 契约实现、枚举标 provisional，按 `contract_version` 迭代。
6. 与 T3/T9/T11 无直接耦合，本 todo 不涉。

---

## T9-publish-form — 发布形态定稿（据 codex 审计修订终版）

**codex 总判**：codex 真跑成功（codex_ran_ok=true），总判 VERDICT=major-gaps。5 条 high 全部据实吸收、无 high 悬而未决：H1(proposed 当事实/marketplace 派生误述)、H2(skills 耦合 acc-common、external-sync standalone 即坏)、H3(skill 名单 impl vs canon 分歧)、H4(拍板前落制品)、H5(AGENTS 无法派生双插件 marketplace) 均已在终版消解，且每条都由本会话对仓核验背书（读 plugin.json/AGENTS.md/SKILL.md/测试文件/真实 marketplace 清单）。13 条 medium/low 亦全部吸收。本 plan 已据审改写，非口头声明「审过」。

**可实施性**：部分 yes（可立即开工到 STOP 点，之后卡用户拍板）。现在即可做：完成 schema 侦查（本会话已完成主体）+ 会话内起草决策简报并过 codex exec 审 → AskUserQuestion 让用户拍 4 项。**核心制品（marketplace.json/plugin.json/check_manifest_sync 扩+补测/静态门/隔离安装冒烟/bureau proposed/散文收口）全部本地可做、无真机/VPN/GPU 依赖，但据 CLAUDE.md #1 必须等用户拍定 4 项后才动**——这是设计上的 STOP 门、非工具缺失。external-sync 正式登记不在本 todo（需对外授权 + 依赖其它 TODO 触发全绿）。

**残余风险**：1) 仓根 marketplace 指向子目录 plugin/ 的**本地 source 精确写法**（git-subdir path 或本地相对 source）尚需实施期以官方 schema/实验确认——已列为 Step1 待验，非阻塞但需落实。 2) 「Claude Code 无跨插件 dependencies」是本会话**遍历本机真实清单未见反例**推得、非官方文档明证；若平台其实支持，双插件方案会重新可行——低风险，实施期可再查 anthropic-docs 确认。 3) **skill 分类三分歧是真未决的 canon-vs-impl 裂口**，T9 只是绕开（不阻塞发布形态），真正收敛压在 P2#22 那个 todo；若迟迟不收敛，external-sync 无法登记。 4) external-sync 的硬门「skills standalone 需 acc-common 随行」本 todo 只记为触发前置、**未解决**——需后续设计 acc-common 打包/降级方案。 5) proposed→canonical 需人工 review，本 todo 后只到 proposed。 6) 隔离安装冒烟依赖 CLAUDE_CONFIG_DIR 覆盖对 /plugin 生效——实施期需先验证隔离确实不污染用户真实 ~/.claude。

**据 codex 修订**：
逐条处理 codex 18 条 issue（5 high 全部据实吸收，无外驳；均经本会话对仓核验支撑）：

【HIGH】
- H1（proposed 页当约束 + 「marketplace 从 AGENTS 派生」非原文）→ 接受。核验 cross-cli 页原文只说「CLAUDE.md/plugin.json 从 AGENTS.md 派生」、未含 marketplace.json。终版：所有 proposed 页统一标「proposed·未settle=方向/待确认假设，不当事实」；「marketplace↔plugin 一致」降为**新提规则**走 bureau proposed，不冒充既有约束；acc-common 耦合这类我能本会话独立实证的，标 verified-by-inspection 后才载重。
- H2（skills bundle 依赖 acc-common，external-sync 只抽 skill 目录会缺脚本、standalone 即坏）→ 接受并加码。读 `acc-spec/SKILL.md` 实证耦合（`${CLAUDE_PLUGIN_ROOT}/acc-common/fetch_source.py` + 落盘 `.../acc-common/specs/`）。终版不止「加 SKILL.md 边界注」，而是（a）改推**单插件**避免拆分即坏；（b）把「skills standalone 真能跑（acc-common 随行或降级）」列为 external-sync 触发第 6 硬门；（c）SKILL.md 标注下沉到触发时做。
- H3（skill 名单 impl vs canon 分歧）→ 接受。核验存在**三套**分类（impl / component-breakdown canonical / todo P2#22）。终版：marketplace **只列插件不枚举 skill**（skills 自动发现），故发布形态不被名单阻塞；名单收敛列入触发清单第 5 点 + open_decisions 第 6；删掉原 plan「本 todo 建 acc-casegen/SKILL.md」。
- H4（Step1 先建决策 doc、Step2 才拍板，违「未拍板不动制品」）→ 接受。终版：决策简报**会话内草稿→codex 审→呈用户拍板→拍定后才落盘 doc**；落制品步全部移到 STOP 门之后。
- H5（AGENTS.md 缺 category/source/dependencies，无法派生双插件 marketplace）→ 接受并消解。改单插件后 marketplace 是仓级制品、引用 plugin.json、**不从 AGENTS.md 派生**，AGENTS.md 无需扩字段；双插件仅作备选并注明需先扩受控 manifest 源。

【MEDIUM】
- M5/M6（plugin.json skills/commands 数组、dependencies 未证合法）→ 接受。核验生态所有真实 plugin.json **无跨插件 dependencies**（仅 npm package.json 有）、也无 skills/commands 数组先例（自动发现）。终版：marketplace 不写 dependencies、plugin.json 不加 skills/commands 数组。
- M6b（marketplace 校验漏 source 解析/version/category 等）→ 接受。终版 check_manifest_sync 扩为校验 name==、version 一致、source.path 解析到 plugin 目录、category 合法；因已无 dependencies，不做依赖闭包。
- M7/M8（/plugin install 有副作用、冒烟未定义清单/回滚）→ 接受。终版用**临时 CLAUDE_CONFIG_DIR** 隔离冒烟 + 明确 add→install→列 agent→uninstall→marketplace remove 清单，且真实环境跑前先告知。
- M9（acceptance「已入 canon」易读成 canonical）→ 接受。终版改「capture→compile 为 proposed dossier，待人工 review 才 canonical」，不用「已入 canon」表既定事实。
- M10（触发项缺可验证证据）→ 接受。终版每项补「证据制品→检查命令/判据→谁勾选」。
- M11（「六 JSON schema 冻结」表述不清）→ 接受。终版改「**六类 JSON 契约**字段稳定/文档化」，并注明当前是契约样例非 formal schema 文件、若要 formal schema 另起子任务。
- M12（acc-casegen 只补 SKILL.md 未纳入 AGENTS/manifest/流程，成孤儿）→ 接受。终版**不在本 todo 建 acc-casegen/SKILL.md**（名单未定，建了即孤儿）；任何新增 skill 须同时进 AGENTS.md 并过 check_manifest_sync。
- M13（叙述错误：现有 test 已覆盖 check_manifest_sync）→ 接受。核验 `test_validate_acceptance_state.py` 已 import 且含 FrontmatterParserTest。终版**就地扩**该文件、删掉新建 test_check_manifest_sync.py 与「只有 validate 测试」的错误叙述。
- M14（Step3/4 先写清单、Step6 才扩校验 → 短暂漂移）→ 接受。终版**先扩校验器+补测跑绿→再写清单→静态门 SYNCED 后才安装冒烟**。
- M15（「双镜像两个主」含糊、缺 owner/URL/默认源）→ 接受。终版给具体 URL/owner（gitcode brian66237 主发行 + github lllyys 镜像）、默认安装源=gitcode、镜像策略=两端 push、cann/* 迁移列独立后续。

【LOW】
- L16（门只写「过 X」缺命令/记录位置）→ 接受。终版 Gates 节补各门命令 + 通过留痕位置（changes-brief 附「codex 审已过」等）。
- L17（README 装机命令与「用户只对话」口径冲突）→ 接受。终版 README 装机命令限「安装」节、「使用」节仍只讲对话入口，不把 python 脚本暴露成日用。

【驳回/超出 codex 建议的再收敛】
- 无整条驳回（codex 无事实性错判）。两处**超出** codex 建议：H2 codex 只建议「验证 bundle 是否含 acc-common / 否则打包或延后」，终版更进一步直接改推**单插件**（因平台无 dependencies 机制、拆分反而必坏）；H3/M12 codex 建议「先对账再写 marketplace」，终版指出 marketplace 列插件不列 skill、故对账**不阻塞发布形态**、只 gate external-sync，从而让本 todo 可推进而非空等对账。


### 终版 plan


# T9 · 发布形态定稿（终版 · 已吸收 codex 审计）

## Goal
把 ADR0003（**canonical**，当事实）已定的方向——「自维护一个 OpRunway 插件仓，靠 `/plugin install` 分发；其中 **skills 部分** external-sync 进 awesome-ascend-skills（cannbot 同款）」——从「倾向已定」推到「**本地可 `/plugin install`、可维护、external-sync 触发点写死**」的定稿态：
1. 把 ADR0003 Consequences 明列的 3 项待定（仓位置 / 插件名 / 是否即刻登记同步）+ 触发点，逐项列「选项/取舍/推荐」交用户拍板；
2. 补齐当前**缺失的仓根 `.claude-plugin/marketplace.json`**（现状：仓根无 `.claude-plugin/`，故仓根本无法被 `/plugin marketplace add`），让**单一** `oprunway` 插件真的能装；
3. 把 design §10 里「接口稳定前不 external-sync」的模糊说法，固化成**每项带可验证证据**的触发清单，并走 bureau 门入 canon（proposed，待人工 review）。

> ⚠ 本 todo **不冻结 skill 名单、不做 SKILL.md standalone 标注**——理由见「关键修订①/③」。这两项被下沉为 external-sync 的触发前置。

## 关键修订（相对原 plan 的实质改动，均据本会话对仓核验）
- **① 由「双插件拆分」改推「单插件 `oprunway`」**。原 plan 推荐 cannbot 式双插件（orchestrator + skills bundle，orchestrator `dependencies` 指向 bundle）。核验发现两处硬伤：(a) 现有 `acc-spec/SKILL.md` 用 `${CLAUDE_PLUGIN_ROOT}/acc-common/fetch_source.py` 取材、落盘到 `${CLAUDE_PLUGIN_ROOT}/acc-common/specs/`——`${CLAUDE_PLUGIN_ROOT}` 按**归属插件**解析，skills 若拆进独立 bundle 插件，其 root 里没有 `acc-common`，装上即坏；(b) 通读本机所有真实 marketplace/plugin 清单，**没有任何 Claude Code `plugin.json` 用跨插件 `dependencies` 字段**（只有 npm `package.json` 有），即 orchestrator「依赖」skills bundle 无平台机制可落。而 **external-sync 是目录级操作**（抽 `skills/<name>/` 目录），压根不需要把 skills 拆成独立插件。→ 推荐单插件；把双插件仅作「若用户坚持则须先解决 acc-common 复制」的备选。
- **② marketplace.json 不是从 AGENTS.md 派生的**。cross-cli 页（**proposed**）原文只说「CLAUDE.md 与 `.claude-plugin/plugin.json` 从 AGENTS.md 派生」，**未提 marketplace.json**。marketplace.json 是**仓/市场级**新制品，引用 `plugin.json` 的 name/version/source；「marketplace.json ↔ plugin.json 一致」是**新提规则**，走 bureau 门当 proposed，不冒充既有约束。
- **③ 不在本 todo 冻结 skill 名单**。当前存在**三套互相打架的 skill 分类**：实现=`acc-spec`/`acc-runner`；canon component-breakdown（**canonical**）=`acc-casegen`/`acc-npu-run`/`acc-perf-compare`（且 task-doc-parse 是 **agent 非 skill**）；todo P2#22=`acc-casegen`/`acc-precision`/`acc-perf`/`acc-rootcause`。名单在飞。单插件的 skills 由 Claude Code **自动发现**、marketplace.json 只列**插件**不列 skills，故「发布形态定稿 + 本地可装」不被名单未定阻塞；但 external-sync 会把**公开 skill 名**固化，故「skill 分类与 canon 对账收敛」列入触发清单，SKILL.md 的 standalone 标注也随之下沉到触发时再做。
- **④ 决策简报先在会话内草拟、用户拍板后才落盘**（守 CLAUDE.md #1「未拍板不动任何制品」）。
- **⑤ 校验器先于清单**：先扩 `check_manifest_sync` 并跑绿，再写 marketplace.json，安装冒烟前必须 `STATUS: SYNCED` + 单测绿，杜绝短暂双写漂移把坏清单喂给安装。
- **⑥ 单测就地扩，不新建文件**：`test_validate_acceptance_state.py` 已 `import check_manifest_sync` 且含 `FrontmatterParserTest`——原 plan「当前只有 validate 的测试」「新建 test_check_manifest_sync.py」的叙述不实，改为**在现有文件追加 marketplace 同步用例**。
- **⑦ 安装冒烟隔离化**：`/plugin marketplace add`+`/plugin install` 改本地 `~/.claude` 状态，改用**临时 `CLAUDE_CONFIG_DIR`** 跑冒烟并卸载清理；在用户真实环境跑前先告知。

## Approach
对齐 ADR0003（canonical）方向，本 todo 只落地它明列的待定项，不重推方向。范式借 cannbot（**proposed** 页），但按上面 ① 收敛为**单插件**。执行纪律守 CLAUDE.md #1/#3/#4/#5/#6 + BUREAU 写门：先出决策简报（会话内草稿→codex exec 审散文）→ AskUserQuestion 让用户拍 4 项（STOP 点，未拍不动制品）→ 才动 marketplace.json 等制品，每类制品过对应门（代码/配置→`cc-suite:audit-fix`；散文→`codex exec`；入 canon→bureau capture→compile→人工 review）。**proposed 页一律当「方向/待确认假设」，不当事实**；凡 proposed 页里我能本会话独立核验的（如 acc-common 耦合），标「verified-by-inspection」后才载重。

## Canon 依据（带 trust tier）
- **ADR0003 —[canonical=事实]**：自维护插件仓 + skills external-sync 进 awesome-ascend-skills（cannbot 同款）；Consequences 明写「仓位置(gitcode/github)、插件名、是否即刻登记同步**待定**」——本 todo 正是落这三项 + 触发点。注：ADR0003 说的是**一个插件仓**，**未**要求拆两个插件（双插件是原 plan 自造，本终版驳回为默认）。
- **component-breakdown —[canonical]**：插件**无 workflow 制品类型**，「workflows」实为**材料仓（无 SKILL.md）**随插件走；三 skill 的 canonical 名是 `acc-casegen`/`acc-npu-run`/`acc-perf-compare`（**与当前实现 `acc-spec`/`acc-runner` 分歧** → 见关键修订③）。
- **cross-cli-unified-form-agents-md —[proposed·未settle]**：`plugin/AGENTS.md` 单一事实源，**CLAUDE.md 与 plugin.json** 从它派生、`check_manifest_sync.py` 防双写漂移；**原文未含 marketplace.json**。避坑纪律「清单从单一源生成、不两处手抄」适用，但「marketplace 从 AGENTS 派生」是**新提规则**，非既有约束。
- **conversational-agent-sole-delivery-form —[proposed·未settle；但核心约束 verified-by-inspection]**：唯一入口=`op-acceptance` agent，脚本自包含在 `acc-common`**不移出**（agent/skills 靠 `${CLAUDE_PLUGIN_ROOT}/acc-common/...` 引用，移出则装上就跑不了）。**本会话读 `acc-spec/SKILL.md` 已实证此耦合**（用 `${CLAUDE_PLUGIN_ROOT}/acc-common/fetch_source.py` + 落盘 `.../acc-common/specs/`），故当 verified 事实用：external-sync 抽 skill 目录会缺 acc-common → **skills standalone 可跑**列入触发前置。
- **workflow-three-layer-architecture —[proposed·未settle]**：Layer0 = **6 类 JSON 契约**（spec/caseset/evidence/verdict/baseline/perf_report）、Layer1 脚本=可移植资产**随插件走**；Layer2 薄壳 skills 才是 external-sync 对象；task-doc-parse 是 **agent 非 skill**（印证③分歧）。
- **cannbot-orchestration-and-cross-cli —[proposed·未settle]**：marketplace/plugin 范式来源；**但该页未强制「双插件 + dependencies」**，本会话核验也未在生态中找到 `plugin.json` 用 `dependencies` 字段。

## Verified-by-inspection（本会话对仓核验的事实）
- 仓根**无** `.claude-plugin/marketplace.json`（`ls .claude-plugin` 无此目录）→ 仓根现无法 `/plugin marketplace add`。
- `plugin/.claude-plugin/plugin.json` = `{name:"oprunway", version:"0.1.0", description, author, keywords, agents:["./agents/op-acceptance.md"]}`——**无 skills/commands 数组**。
- 通读本机真实 marketplace.json（官方 + xiaolai）：插件条目字段为 `name/description/author/category/source(github|git-subdir|…)/version/keywords/repository/license/homepage`，市场级可有 `owner`/`renames`；**遍寻无 `plugin.json` 用跨插件 `dependencies`**（仅 npm `package.json` 有）。
- `plugin/AGENTS.md` frontmatter：`agents:[op-acceptance]`、`skills:[acc-spec,acc-runner]`；正文 line42 明写「CLAUDE.md / plugin.json 从它派生」，**未列 marketplace.json**。
- `check_manifest_sync.py` 现只校验 AGENTS.md ↔ plugin.json 的 **agents** 一致 + 声明的 agent/skill 文件存在；不校验 commands，也不要求 plugin.json 列 skills。
- `test_validate_acceptance_state.py` 已 `import check_manifest_sync as C` 且含 `FrontmatterParserTest`（block/flow list、注释跳过、无 frontmatter 等）→ 校验器已有测试覆盖。
- skills 现状：`acc-spec`(SKILL.md✓)、`acc-runner`(SKILL.md✓)、`acc-casegen`(**仅 references/rule-catalog.md，无 SKILL.md**；未在 AGENTS.md skills 列内，故不触发 DRIFT)。
- todo P2#22 计划把 `acc-spec`/`acc-runner` **重构拆成原子 skill**（`acc-casegen`/`acc-precision`/`acc-perf`/`acc-rootcause`）→ skill 名单确在飞。
- design §10 已写「发布不早于接口稳定——等 schema/状态文件/证据 JSON/最小 catlass 端到端跑通再登记 external-sync；每份 SKILL.md 标 standalone 边界」——触发清单有据可依，本 todo 是**形式化 + 补证据 + 入 canon**。

## Files（动作 + 目的）
- `doc/oprunway-publish-form-decision.md` — **create（用户拍板后才落盘）**：记 4 项**已定值** + 取舍留痕；决策前只在会话内呈现草稿。（散文→codex exec 门）
- `.claude-plugin/marketplace.json`（仓根，当前缺失）— **create**：列**单一** `oprunway` 插件（`name`=拍定名、`version` 对齐 plugin.json、`source` 指向子目录 `plugin/`、`category`=拍定、`author.name=lys`、`owner` 按拍定的默认发行源）。**不列 dependencies、不枚举 skills**（skills 自动发现）。（配置→cc-suite:audit-fix）
- `plugin/.claude-plugin/plugin.json` — **edit（仅当用户改名/改版时）**：对齐 name/version。**不加 skills/commands 数组**（自动发现，且生态中无先例证明其为合法必填）。（配置→cc-suite:audit-fix）
- `plugin/acc-common/check_manifest_sync.py` — **edit**：新增 marketplace.json 结构校验——`oprunway` 条目 `name`==plugin.json `name`、`version` 一致、`source.path` 解析到实际 plugin 目录、`category` 合法枚举内；保持 `STATUS: SYNCED/DRIFT`、只读、抗坏输入。**不做跨插件依赖闭包**（已无 dependencies）。（代码→cc-suite:audit-fix）
- `plugin/acc-common/test_validate_acceptance_state.py` — **edit（就地扩，不新建文件）**：追加 marketplace 同步用例（SYNCED / name 不符 DRIFT / version 不符 DRIFT / source 路径不存在 DRIFT / 坏 JSON 不崩）。
- `doc/oprunway-design.md` §10 — **edit**：从「倾向已定」改定稿——写死拍定的仓位置/命名/**单插件结构 + 理由** + **带证据的 external-sync 触发清单**；`/plugin install` 命令限定在「分发/安装」子节，「使用」子节仍只讲对话入口。（散文→codex exec 门）
- `README.md`（仓根）— **edit**：加「分发/安装」节（`/plugin marketplace add` + `/plugin install`），与「使用=对话入口」节分离，**不把 `python3 …` 脚本重新暴露成日常用法**（守 conversational-agent 口径）。（散文→codex exec 门）
- `doc/oprunway-todo.md` — **edit**：更新 P2#8 状态（单插件本地可装态已达、external-sync 登记待触发 + 触发清单落地）。
- `doc/oprunway-changes-brief.md` — **edit**：倒序追加一两句本次定稿（CLAUDE.md #4）。
- `canon/` — **不手改 ADR0003**。走 bureau 门：`bureau:note` 捕获『4 项已定值 + external-sync 触发清单 + 单插件理由 + skill 分类分歧待收敛』→ `bureau:compile` 成 proposed（ADR0003 增补页或新页）→ 提请**人工 `bureau:review`** 提 canonical。logbook append-only，绝不 hand-edit cabinet、绝不自设 canonical。
- **原 plan 中删除/下沉的动作**：不创建 `acc-casegen/SKILL.md`、不改 `acc-spec/acc-runner` SKILL.md 加 standalone 边界、不新建 `test_check_manifest_sync.py`——前两者下沉为 external-sync 触发前置（名单未定期做是白工/误导），后者并入现有测试文件。

## Steps
1. **Schema 侦查 + 对账（无副作用，本会话已完成主体）**：确认单插件可行、生态无跨插件 dependencies、marketplace 合法字段集、仓根缺 marketplace.json、skill 名单三分歧、现有测试已覆盖 check_manifest_sync。实施期再补一处待验：仓根 marketplace 指向子目录 `plugin/` 的**本地 source 写法**（`git-subdir` 的 path，或本地相对 source）以官方 schema/实验确认。
2. **起草决策简报（会话内草稿，不落盘）**：4 节——仓位置 / 插件名与结构（含单插件 vs 双插件的机制约束） / 是否即刻 external-sync / 触发清单——每节给「选项/取舍/推荐」。**过 codex exec 审散文**后呈用户。
3. **用户拍板（硬 STOP 门 · CLAUDE.md #1）**：AskUserQuestion 逐项让用户定 4 项 + external-sync 对外授权意向。未拍不动任何制品。
4. **落盘决策简报**：把拍定值写入 `doc/oprunway-publish-form-decision.md`（此步起才动制品）。
5. **先扩校验器 + 补测（清单之前）**：改 `check_manifest_sync.py` 纳入 marketplace 结构校验；在 `test_validate_acceptance_state.py` 追加用例；跑到单测全绿。过 cc-suite:audit-fix。
6. **写仓根 marketplace.json + 对齐 plugin.json**：按拍定命名写单插件条目；仅当改名/改版时同步 plugin.json；跑 `check_manifest_sync.py` 至 `STATUS: SYNCED`。过 cc-suite:audit-fix。
7. **静态验收门（无副作用，主门）**：JSON 合法性 + 字段合法（据 step1 侦查）+ `check_manifest_sync` `STATUS: SYNCED` + 单测全绿。**此门是安装冒烟的前置**。
8. **隔离安装冒烟（有副作用，先告知）**：临时 `CLAUDE_CONFIG_DIR=<tmp>` → `/plugin marketplace add <本仓路径>` → `/plugin install oprunway` → 核 `op-acceptance` agent 可见 → `/plugin uninstall` + `/plugin marketplace remove` 清理。不 push、不碰远端、不动用户真实 `~/.claude`。
9. **入 canon（bureau 门）**：`bureau:note` 捕获 4 项定值 + 触发清单 + 单插件理由 + skill 分类分歧 → `bureau:compile` 成 proposed → 提请人工 `bureau:review`。绝不 hand-edit ADR0003、不自设 canonical。
10. **收口散文**：定稿 design §10、追加 changes-brief、更新 todo P2#8、README 加「安装」节（与对话用法分离）。统一过 codex exec。
11. **external-sync 登记（延后 · 不在本 todo）**：仅当触发清单**全绿** 且 用户**明示授权**动 `cann/awesome-ascend-skills`（非本用户仓）时才发起，署名 lys。本 todo 内只把条件与授权口子记清。

## external-sync 触发清单（每项带可验证证据 · 提请用户确认收敛）
> 全绿才登记。每项：`证据制品 → 检查命令/判据 → 谁可勾选`。
1. **六类 JSON 契约字段稳定/文档化**（spec/caseset/evidence/verdict/baseline/perf_report）。证据=`doc/oprunway-workflow-design.md` 契约表 + 现存样例；判据=字段集有文档且一版内无破坏性改动；勾选=维护者。（注：当前是**契约样例**如 `specs/*.spec.json`，**非六份正式 JSON Schema 文件**；若要求 formal schema，另起子任务先建 schema + 校验器。）
2. **状态文件格式冻结**：`validate_acceptance_state.py` 读的 `evidence/verdict` 结构冻结。证据=其单测全绿；判据=`test_validate_acceptance_state.py` pass；勾选=维护者。
3. **≥1 个 catlass 算子真 950 端到端跑通**（T7·P3）。证据=`reports/<repo>/<op>/<pr>/verdict.json` overall=pass + 真机日志；判据=门 `STATUS: PASSED`；勾选=维护者据真机产物。
4. **对话式 agent 形态落地**（P1·P2，含 `init.sh` 安装期扇出）。证据=`init.sh` + 各 CLI 注册薄壳；判据=至少 Claude Code + Codex 两运行时装后 `op-acceptance` 可见；勾选=维护者。
5. **skill 分类与 canon 对账收敛**（P2#22 原子化落定，消解 impl/canon/todo 三分歧）。证据=收敛后的 skill 名单 + component-breakdown 同步更新（经 review）；判据=`check_manifest_sync` SYNCED 且 canon 名一致；勾选=维护者 + 人工 review。
6. **每份 SKILL.md 标 standalone 边界 且 skills standalone 真能跑**。证据=各 SKILL.md 的边界节 + acc-common 随行方案（bundle 打包 acc-common 或 skill 缺 acc-common 时优雅降级）；判据=在无顶层插件的隔离环境实测 skill 可用/明确降级；勾选=维护者据实测。**（此项直击 acc-common 耦合，是 external-sync 的真硬门。）**

## Gates
- **散文**（决策简报 / design §10 / changes-brief / README / 触发清单文字）→ **`codex exec`** 定制审（CLAUDE.md #5，cc-suite 代码维度套不上散文）。记录=改动落 changes-brief 时附「codex 审已过」。
- **代码/配置**（marketplace.json / plugin.json / check_manifest_sync.py 及单测）→ **`cc-suite:audit-fix`**（9 维审→修→验循环）。
- **机器门**：`check_manifest_sync.py` 必须 `STATUS: SYNCED`（纳入 marketplace 一致性）+ 单测全绿——**安装冒烟前必须先过此静态门**（防坏清单）。
- **入 canon** → bureau 门：capture(`bureau:note`，低权 logbook 追加)→compile(proposed)→**人工 `bureau:review` 提 canonical**；BUREAU 铁律：绝不 hand-edit cabinet、绝不自设 canonical、logbook append-only。
- **用户确认门**（CLAUDE.md #1/#3）：4 项决策 + external-sync 对外授权先经用户拍板才动制品/外发。
- **N/A**：`validate_acceptance_state.py`（裁决完整性门）本 todo 不涉及——T9 是发布形态、不产精度/性能裁决。

## Blocked / 依赖
- **本地现在能做**：schema 侦查（已完成主体）、起草决策简报；用户拍板后：写 marketplace.json、扩 check_manifest_sync + 补测、静态门、隔离安装冒烟、bureau 捕获与 compile(proposed)、收口散文。**全程不需要真机 NPU/VPN/GPU**。
- **卡住**：① 4 项决策本质是**用户拍板**（CLAUDE.md #1），未拍不动制品——这是本 todo 的主 STOP；② external-sync 正式登记=动 `cann/awesome-ascend-skills`（非本用户仓）→ 需**用户对外授权** + 署名 lys（CLAUDE.md #2），本 todo 内不做；③ 触发清单里的前置本身卡在**其它 TODO**（catlass 真 950 端到端 T7/P3、对话式 agent + init.sh 扇出 P1/P2、六类 JSON 契约字段冻结、**skill 分类原子化收敛 P2#22**、skills standalone 含 acc-common 随行）——这些只 gate『登记时机』，**不 gate 本 todo 的『形态定稿 + 单插件可安装 + 触发点写死』**，后者现在即可完成；④ proposed 升 canonical 需**人工 review**，非本会话可自决。

## Acceptance
1. 4 项待定（仓位置 / 插件名与结构 / 是否即刻 external-sync / 触发点）各有用户明确决策并留痕于 `doc/oprunway-publish-form-decision.md`。
2. 仓根 `.claude-plugin/marketplace.json` 存在，列**单一** `oprunway` 插件；**静态门通过**（JSON 合法、字段合法、`check_manifest_sync` `STATUS: SYNCED`、现有+新增单测全绿）；**隔离**安装冒烟成功（临时 CLAUDE_CONFIG_DIR 下 `/plugin install oprunway` 成、`op-acceptance` 可见、已卸载清理）。
3. `check_manifest_sync.py` 扩到校验 marketplace ↔ plugin.json（name/version/source/category）并 SYNCED；用例**就地加进** `test_validate_acceptance_state.py`（不新建孤立文件）。
4. external-sync「接口稳定前不同步」固化为**每项带证据**的触发清单（≥6 点，见上），已 capture→compile 为 **proposed dossier（待人工 review 才 canonical）**，并写进 design §10。
5. 本 todo **未冻结** skill 名单、**未做** SKILL.md standalone 标注——二者下沉为触发前置并记录为 open（skill 分类分歧写入 open_decisions）。
6. 采**单插件** `oprunway` 结构并记录理由（acc-common 耦合 + 无跨插件 dependencies + external-sync 目录级）；双插件的机制障碍一并记录。
7. external-sync **尚未**向 awesome-ascend-skills 登记（延后）；延后条件（触发全绿）+ 对外授权口子记录在案。
8. design §10 定稿、changes-brief 追加、todo P2#8 更新、README「安装」节与「对话用法」分离；相关 codex exec / cc-suite:audit-fix / bureau(proposed) 门均已过并留痕。

## Open decisions（需用户拍板的岔口 · 已具体化）
1. **仓位置**——具体化推荐：**gitcode `brian66237/OpRunway` 主发行**（对齐生态：awesome-ascend-skills 与 cannbot 均在 gitcode `cann/*`）+ **github `lllyys/OpRunway` 镜像/主开发**；**默认安装源 / marketplace `owner` = gitcode**；镜像策略=两端都 push、gitcode 为 marketplace 事实源。是否迁入 `cann/*` org=**独立后续决策**，本 todo 不定。请用户确认或改。
2. **插件名与结构**——**推荐单插件 `oprunway`**（不拆双插件），因 skills 耦合 `${CLAUDE_PLUGIN_ROOT}/acc-common` + Claude Code 无跨插件 `dependencies` + external-sync 是目录级、无需拆分。若用户坚持双插件，须先解决 acc-common 复制/共享问题。名沿用 `oprunway`。
3. **是否即刻登记 external-sync**——**推荐否**：先让单插件本地可 `/plugin install`，登记待触发（对齐 design §10 + 简表 2026-06-30「接口稳定前不 external-sync」）。
4. **external-sync 触发清单条目**——推荐上文 6 点（每点带证据/判据/勾选人）。请用户确认收敛或增减。
5. **external-sync 登记 = 动 `cann/awesome-ascend-skills`（非本用户仓）**——需用户**明示授权** + 署名 lys（CLAUDE.md #2），且待 ④ 全绿后才发起。
6. **（新增）skill 分类分歧的处置**——impl(`acc-spec`/`acc-runner`) vs canon component-breakdown(`acc-casegen`/`acc-npu-run`/`acc-perf-compare`) vs todo P2#22(`acc-casegen`/`acc-precision`/`acc-perf`/`acc-rootcause`) 三套并存。本 todo **不解决**（marketplace 列插件不列 skill，不被其阻塞），但确认它是 external-sync 的触发前置（清单第 5 点），提请用户知悉「收敛放到 P2#22 那个 todo 做」。


---

## T10-equal-canon-correction — Equal 翻案 canon 更正（bureau 门）+ lint survivors 真正排进 review 视图

**codex 总判**：codex 真跑成（codex_ran_ok=true），verdict=needs-revision，共 14 findings（3 high + 8 medium + 3 low）。3 条 high 已全部解决：#1 通读 canon → 加 Step 0 通读门；#2 canonical 页门语义 → 全面改为「走 gate、不手编 prose/不手升」；#3 bureau 可行性 → 以实据确认命令面=bureau 插件 skills 并补 fallback。修订后**无 high 未解**。最实质的两处改进来自 #2（纠正「canonical 只能人手改」这一违反 BUREAU 门的措辞）与 #8（发现单靠 bureau:note 不会把 survivors 送进 review 视图——这是原 plan 的机制盲点，直接关系 goal 能否达成）。所有 14 条均已在 final_plan 落实，无驳回。本 plan 已经外审并据审修订。

**可实施性**：yes（agent 侧本地可立刻动手）——Step 0 通读、Step 1 只读核验（bureau:status/inspect skill 或 grep+Read fallback）、Step 2–4 起草+散文门+bureau:note、Step 6 简表，全部本地可做、不依赖真机/VPN/GPU/网络。但 T10 的**终态达成卡在用户决策**：①Step 5 是否跑 bureau:lint --apply（默认留 review，需用户拍板，否则 survivors 不进 review 视图）；②4 张 Equal 页 promote + ADR0002/1.2× 五页更正全是人门 bureau:review，agent 不越门。即：agent 能把「核验+capture+议程」一次性做完，但「canonical 促进与 survivor 修正」是人门里程碑、另行追踪。

**残余风险**：1) bureau tool 车道行为未在本会话实跑验证——尤其 bureau:compile 能否把更正折进/superseding 一张 canonical 页（还是只产 proposed 平行页）、以及 bureau:review 视图究竟如何呈现 stale/proposed，均属 bureau 插件实现细节，plan 按「tool 驱动、非手编」的门语义描述，实跑时若车道行为与预期不符需就地调整（已在 Step 1 fallback + Step 5 二选一里留了余地）。2) survivors 可见性依赖用户在 open_decision #1 的选择：若用户选「留 review」，则 2 survivor 事实上仍埋在 note/findings.md、review 人须主动查——goal「排进 review 议程」在「视图可见」这一最强义上未完全达成（plan 已显式标注此权衡）。3) perf-baseline 页的 perf_baseline_source 张力是一条尚未 settle 的悬案，promote 前必须人裁；若草率 promote 会把「GPU 对比层可选」当 canon 固化，风险留在人门。4) codex 散文门（Step 3）本身是外部 codex exec 调用，其可用性/sandbox 行为在真跑前未验证。

**据 codex 修订**：逐条处理 codex 14 项（3 high 全解 + 11 med/low）：

【HIGH-1 通读 canon（consistency）】采纳。原 plan 只做定向核验、违反 CLAUDE.md #6。新增 Step 0 通读门：durable 工作前实读 architecture+decisions+findings，或显式声明复用同 session 通读；明确不走「00-overview+query」降级并给理由（32 页通读划算 + Equal 牵动上游前提的血教训）。acceptance #1 加通读验收项。

【HIGH-2 canonical 页更正门语义（consistency）】采纳（重要修正）。原 plan 通篇「canonical 只能人改/人门 edit」违反 BUREAU 门「不手编 cabinet prose」。改为：canonical 页更正走同一门——capture(note)→compile/lint(tool 产 proposed 修订/stale)→review 促进，无人手编 prose。files 表 5 张 survivor 页的 action 从「人门 edit」改「gate tool 车道→review」；blocked_deps 删「只能人改、agent 不能手编」这类措辞，改「走 gate compile/lint、agent 不手编 prose」。

【HIGH-3 bureau CLI 可行性（feasibility）】采纳并以实据解决。核查确认 bureau 命令面 = bureau 插件 skills（bureau:status/inspect/note/review/lint/compile/query，均在 available-skills 列表、经 Skill 工具调用、非 CLI）。plan 补「工具面」说明 + 车道不可用时退回纯只读核验（grep+Read+直读 _compile-state.json/frontmatter）的 fallback（写进 Step 1 与 blocked_deps）。

【MED-4 logbook create→append（risk）】采纳。files 表 logbook 行 action 从「create」改「bureau:note append 进当前 minute」，明标非手工 create、honor append-only。

【MED-5 fail-stop（completeness）】采纳。Step 1 加 fail-stop：status/inspect 报 dangling/contradiction 或 grep 命中未作废残留 → 停、不进 bureau:note，回 compile/上报，本 todo 阻塞。

【MED-6 残留断言范围（ambiguity）】采纳。acceptance #2 明确「无残留 Equal 裁决」范围**仅限 cabinet pages（architecture/decisions）**；logbook/changes-brief 按 append-only 保留历史、只要求有作废标记。已实测确认 cabinet 干净（grep 命中全是作废叙述、残留只在 logbook/_verify.json）。

【MED-6b grep 关键词完整（completeness）】采纳。Step 1 grep 从示例关键词扩为完整作废横幅集：真阳性/A3 未达标/精度 fail/FAIL(精度)/输出≠golden/#2890/双核 merged/真机 6 挂 5/由 op_def 取 dtype。

【MED-7 lint --apply 改 canonical 与主线矛盾（consistency）】采纳并重构。原「Step5 可选」与「agent 不碰 canonical」自相矛盾。厘清：lint --apply 标 stale 是press 结构车道（tool 驱动、非手编 prose、非手升）→属门允许，与「不手编/不手升」不矛盾；但因改 canonical status 属消耗性变更，升级为 Step 5 独立**用户授权门**，默认不跑、列 open_decision #1，并说明它改变 review 队列/视图与验收结果。

【MED-8 note 不进 review 视图（completeness）】采纳（关键机制修正）。核查 bureau:review 队列=未批 proposed cabinet claim：4 张 Equal 页本就 proposed→天然在队列；但 2 survivor 在 canonical/proposed 页上无 proposed 修订/无 stale 标，单靠 bureau:note **不会**进 review 视图。plan 显式写明此机制，并把「让 survivors 真可见」提为 Step 5 的实质决策（lint --apply 或 compile），不再当可选点缀。

【MED-9 codex exec 约束（ambiguity）】采纳。Step 3 补约束：只读/受控、stdin 传文本、禁落盘、禁新增 canon claim、复核 diff 只改表述。

【MED-10 Step6 拆散文门（consistency）】采纳（合并优化）。Step 6 拆 6a 起草/6b 落盘，且把 changes-brief 草稿并入 Step 3 的同一次 codex 调用（一次审两段散文），与 Gates 声明对齐、避免额外 round-trip。

【MED-11 changes-brief 衔接旧条（risk）】采纳。新增条须显式衔接 07-09 旧待办『canon 两决策页+一架构页待更正(=3 页)』→已由 compile 推进为『4 页 proposed（含新建 verify-spec-pr 页）+ review 队列』，消除页数/状态读者误解。

【LOW-12 ADR0007 佐证（consistency）】部分采纳。findings.md 确实同时引 ADR0007 与 perf_compare.py 论「谁算/pass 何意」，故 ADR0007 非全无关；但原 plan 把它当 target_ratio 机制主佐证是过度。改为：主佐证=findings.md + perf_compare.py + spec.json（化石≠功能 bug 的实据），ADR0007 降为次级佐证（verdict 来自 deterministic validator）。

【LOW-13 每页 promote/hold checklist（completeness）】采纳。steps 末补 4 张 Equal 页最小 review checklist，perf-baseline 明标「先裁 perf_baseline_source 张力再促进」。

【LOW-14 ADR0006/0008 rename-before-promote（risk）】采纳。files 表 + open_decision #2 + Step7 加硬约束『rename 未处理前不得 promote』，纳入同一 review bundle，防 2 张 proposed 页被单独 promote 固化 drift。

无驳回项——14 条全部采纳或部分采纳（仅 LOW-12 因 findings.md 实际引用了 ADR0007 而调为「降级为次级佐证」而非全删）。

### 终版 plan

## T10 · Equal 翻案 canon 更正（bureau 门）+ lint survivors 真正排进 review 视图

### goal
让 Equal「任务书↔PR 配错·空任务·前结论作废」这一更正在 canon 里状态自洽，并把两条 lint survivor **用 bureau 真能被 review 看见的机制**排进人工 review 议程——从而下一次 `bureau:review` 时用户能一次性裁决：升哪几页 canonical、纠哪几页 canonical 上的化石口径。终态 = cabinet（architecture+decisions）无悬空的过时 Equal 裁决、review 队列在 bureau 的 review 视图里真实可见、可照做。

**goal_note**：经核查，本 todo 的「compile 半程」已由上一会话（minute `37223d6d`）落地并入库（commit 419b5d4）——4 张 Equal 相关页均在库为 `proposed`（`verify-spec-pr-correspondence-before-acceptance`[新]、`root-cause-decoupling-before-attribution`[改]、`task-spec-authoritative-over-pr`[改]、`perf-baseline-by-reference-source`[Equal 项作废]），`37223d6d` 已进 `_compile-state.json`。故本 todo 的剩余重心是「核验完整 + 让 survivors 进 review 视图 + 交人门」，不是从零重编。

### approach
对齐 **BUREAU 写门**（capture→compile→review；canonical 不可手升、cabinet prose 不可手编、logbook append-only、read 按 tier）与 **CLAUDE.md #5 散文门**（bureau 决策/散文文本先过 `codex exec` 再写）与 **CLAUDE.md #6 通读 canon**（durable 工作前先通读）。

核查发现 Equal 更正的 `bureau:compile` 已完成，故 approach **不重推**这批已 settle 的 compile 产物，只做四件：

1. **通读 canon（#6 硬门）**：本会话是新会话，动 durable 工作前先通读 `canon/architecture/*` + `canon/decisions/*` + `canon/lint/findings.md`，按 trust tier 读（只 `canonical` 当事实；4 张 Equal 页当前是 `proposed`=拟案、不作事实）。若判定同 session 已通读可复用则显式声明；否则实读。
2. **只读核验（fail-stop）**：`bureau:status` + `bureau:inspect` 确认结构 0 dangling/0 contradiction、4 页 proposed 在库、`37223d6d` compiled；再用全关键词 grep 复核 cabinet 无残留过时 Equal 裁决。任一核验不过 → **停，不进 bureau:note**，回到 compile/上报。
3. **capture 议程 + 让 survivors 真进 review 视图**：`bureau:note` 把「4 页待 promote + 2 survivor + perf_baseline_source 张力 + rename-before-promote 约束」写成显式人读议程（logbook=低权威 capture，非事实）。**关键更正**：`bureau:review` 的队列是「未批的 cabinet proposed claim」——4 张 Equal 页本就 `proposed`、天然在队列里；但 2 条 survivor 落在 **canonical/proposed 页上、没有 proposed 修订也没有 stale 标**，单靠 `bureau:note` **不会**出现在 review 视图。要让它们真被 review 看见，须走 bureau 提供的 tool 车道之一：`bureau:lint --apply`（结构车道把 ADR0002 标 `stale`、写 superseded/drift 边）或 `bureau:compile` 折出 proposed 修订。二者都是 **tool 驱动、非手编 prose、非手升 canonical**，属门允许的动作——但因改动 canonical 页 status（消耗性变更），按 CLAUDE.md #3 先向用户确认再跑（见 open_decisions #1）。
4. **交人门**：`bureau:review` promote/纠正=人门，agent 不越门。

**canonical 页更正的门语义（据 codex 审修正）**：BUREAU 门要求「不手编 cabinet prose、不手升 canonical」。因此 ADR0002 / 3 张『1.2×』canonical 页的**内容更正也走同一门**——capture（note）→ compile/lint（tool 写 proposed 修订或标 stale）→ review（人促进/纠正），**没有任何人手编 cabinet .md prose**。agent 可跑 tool 车道（note/compile/lint --apply/status/inspect/query）；唯一对 agent 关闭的是 review 促进步。所有引用的 4 张 Equal 决策页当前均为 `proposed`=未 settle，本 plan 不把其内容当事实、仅当「待人裁的拟案」对待。

### canon_grounding（逐页标 tier）
- **BUREAU.md**（门规则，binding/权威）——capture→compile→review、canonical 不可手升、cabinet 不可手编 prose、logbook append-only、read 按 tier。
- **被更正的 4 张 Equal 决策/架构页**：`verify-spec-pr-correspondence-before-acceptance`、`root-cause-decoupling-before-attribution`、`task-spec-authoritative-over-pr`、`perf-baseline-by-reference-source`——**全部 `proposed`=未 settle**，是本 todo 的产出对象与待 review 拟案，**不作事实**。
- **canon/lint/findings.md**（report-only，已记 2 survivor）：Survivor#1 Superseded/medium（ADR0002 msTuner→msprof op）；Survivor#2 Drift/medium（『1.2×』→`target_ratio`，跨 5 页）。findings.md 明确定性两者**均非功能 bug、只是误导性化石**。
- **Survivor#1 落点** `0002-acceptance-grounded-in-catlass`（**canonical**，msTuner 化石）。
- **Survivor#2 落点 5 页**：`acceptance-contract-evidence-chain` / `oprunway-acceptance-pipeline` / `generated-harness-responsibilities`（**均 canonical**）+ `0006-performance-timing-scope` / `0008-reuse-ascendoptest`（**均 proposed**）。
- **`target_ratio` 机制的直接佐证**（据 codex #12 修正）：`canon/lint/findings.md` 明写「可执行路径 `perf_compare.py` + `spec.json` 在 `target_ratio` 上一致」——这是化石≠功能 bug 的实据。`0007-deterministic-validator`（canonical）**仅作次级佐证**（verdict 来自 deterministic validator），不直接证明 `target_ratio` 机制，故不再当主佐证。
- **`catlass-acceptance-mechanics`**（canonical）——佐证 ADR0002 该对齐它：msprof op 才是验收 perf 后端、msTuner 只是搜 tiling 的调优工具。

### files
> 语义修正（据 codex #2/#4/#7）：canonical 页一律**不手编 prose、不手升**；其更正走 gate 的 tool 车道（compile/lint）产 proposed 修订/stale 标，再由 review 促进。logbook 一律经 `bureau:note` append，不手工 create。

| 路径 | action | 说明 |
|---|---|---|
| `canon/logbook/2026/07/<当前session>.md` | **bureau:note append**（非手工 create） | 【agent】把 review 议程 append 进当前会话 minute：列 4 张 Equal 页(待 promote，附路径+tier)、2 条 survivor(附落点页+tier+建议改法+『非功能 bug、只是化石』定性)、perf_baseline_source 张力需连带裁、ADR0006/0008 rename-before-promote 约束。低权威 capture、非事实。 |
| `doc/oprunway-changes-brief.md` | edit（过散文门后） | 【agent】按 CLAUDE.md #4 追加一条(倒序、大白话)，并**显式衔接** 07-09 旧待办「canon 两决策页+一架构页待更正」→ 已由 compile 推进为 **4 页 proposed**（含新建 verify-spec-pr 页）+ review 队列；survivors 待人门/tool 车道处置。 |
| `canon/decisions/verify-spec-pr-correspondence-before-acceptance.md` | 人门 promote（bureau:review） | 【proposed→canonical 或 hold】内容已 compile 到位，仅状态促进，agent 不改 prose。 |
| `canon/decisions/root-cause-decoupling-before-attribution.md` | 人门 promote（bureau:review） | 【proposed→canonical 或 hold】同上。 |
| `canon/decisions/task-spec-authoritative-over-pr.md` | 人门 promote（bureau:review） | 【proposed→canonical 或 hold】同上。 |
| `canon/architecture/perf-baseline-by-reference-source.md` | 人门 promote（bureau:review） | 【proposed→canonical 或 hold】促进时须**先裁**页内『perf_baseline_source 张力 / GPU 对比层可选』悬案再升。agent 不改 prose。 |
| `canon/decisions/0002-acceptance-grounded-in-catlass.md` | **gate tool 车道**（compile/lint --apply 产 proposed 修订/stale）→ review | 【canonical，Survivor#1】msTuner→msprof op（或删工具名、转引 catlass-acceptance-mechanics）。**不手编 canonical prose**——经 tool 车道折出修订、review 纠正/促进。 |
| `canon/architecture/acceptance-contract-evidence-chain.md` | **gate tool 车道**→ review | 【canonical，Survivor#2】『1.2×』→`target_ratio`（任务书推导：TBE 无劣化=1.0、≥95%=0.95、GPU 移植 0.5–0.8×）。不手编。 |
| `canon/architecture/oprunway-acceptance-pipeline.md` | **gate tool 车道**→ review | 【canonical，Survivor#2】同上 rename。 |
| `canon/architecture/generated-harness-responsibilities.md` | **gate tool 车道**→ review | 【canonical，Survivor#2】同上 rename。 |
| `canon/decisions/0006-performance-timing-scope.md` | agent 可 bureau:compile 折 rename（proposed 页） | 【proposed，Survivor#2】proposed 页 agent 可经 compile 改；但**与 3 张 canonical 并作一次处置**、加『rename 未处理前不得 promote』硬约束，避免半修留 3 页仍写 1.2×。 |
| `canon/decisions/0008-reuse-ascendoptest.md` | agent 可 bureau:compile 折 rename（proposed 页） | 【proposed，Survivor#2】同上，随整体一次改 + rename-before-promote 约束。 |

### steps
0. **通读 canon（CLAUDE.md #6 硬门）**：动 durable 工作前，通读 `canon/architecture/*` + `canon/decisions/*` + `canon/lint/findings.md`，按 tier 读。若本 session 已实读可复用则在交付里显式声明「已通读、覆盖 X 页」；否则实读。**不走降级**（本 canon 32 页规模通读划算，且 Equal 更正牵动上游前提，血教训要求 grounding）。
1. **只读核验 + fail-stop**：跑 `bureau:status` + `bureau:inspect`，确认①4 张 Equal 页在库 `proposed`；②`37223d6d` 在 `_compile-state.json` compiled；③结构 0 dangling/0 contradiction。再对 `canon/architecture canon/decisions` grep **完整关键词集**：`真阳性 / A3 未达标 / 精度 fail / FAIL(精度) / 输出≠golden / #2890 / 双核 merged / 真机 6 挂 5 / 6 挂 5 / 由 op_def 取 dtype`，确认全部仅以「作废/删除」形态存在。**Fail-stop**：若 status/inspect 报 dangling/contradiction，或 grep 命中任一**未打作废标**的残留 Equal 结论 → **停止，不进 Step 3 的 bureau:note**；改为重跑 `bureau:compile` 或上报用户，本 todo 阻塞。**工具面 fallback**：若某 bureau skill 车道当前不可用，退回纯只读核验（`grep` + `Read` + 直接读 `_compile-state.json`/frontmatter status），并在交付里标注「X 车道未跑、以只读核验替代」。
2. **起草 review 议程文本（散文）**：两块——块A『4 张 Equal 更正页待 promote』逐页给路径+tier(proposed)+一句『内容已 compile、待人裁升 canonical 或 hold』+每页最小 promote/hold checklist（见下）；块B『2 条 lint survivor』#1 ADR0002(canonical) msTuner→msprof op、#2 五页 1.2×→target_ratio（3 canonical+2 proposed，列全路径），各附 findings.md 建议改法 + 『非功能 bug、只是化石』定性 + 『survivors 单靠 note 不进 review 视图，须 lint --apply 或 compile 才可见』的机制说明 + ADR0006/0008 『rename 未处理前不得 promote』约束。
3. **过散文门（codex exec）**：按 CLAUDE.md #5，用 `codex exec` 审+修 Step 2 议程文本**与** Step 5 changes-brief 草稿（一次 codex 调用覆盖两段散文，效率优先）。**约束**：只读/受控、文本经 stdin 传入、**禁止落盘、禁止新增任何 canon claim**；复核 diff 只改表述、不引入新事实。复述『发现了什么/改了什么/剩余风险』。修完再进 bureau 写。
4. **bureau:note 落 review 议程**：把过审文本经 `bureau:note` capture 进当前 minute（logbook=低权威、非事实）。这是 agent 端对本 todo 的实体产出：把「4 页 proposed（已在 review 队列）+ 2 survivor（待经 lint/compile 才进队列）」整合成一份显式、可照做的人读议程/handoff。
5. **（用户确认后）让 survivors 真进 review 视图**：因 `bureau:note` 不足以把 survivors 送进 review 视图，向用户提请二选一并经确认后由 agent 跑（tool 车道、非手编/非手升）：(a) `bureau:lint --apply`——结构车道把 ADR0002 标 `stale`、写 superseded/drift 边，使 survivors 在 review/结构健康视图可见；或 (b) `bureau:compile` 折出 proposed 修订。**默认不自动跑**（改 canonical 页 status 属消耗性变更，按 CLAUDE.md #3 先确认）；列 open_decisions #1。若用户选「留给 review 一次处置」，则 survivors 以 note+findings.md 形态交接，交付里显式标注「survivors 未进 review 视图、需 review 人主动查 findings.md」。
6. **追加改动简表（含散文门）**：6a 起草 changes-brief 条（已并入 Step 3 codex 审）；6b 落盘 `doc/oprunway-changes-brief.md`（倒序、大白话），显式衔接 07-09 旧待办→4 页 proposed + review 队列。
7. **交接人门 bureau:review**：向用户交出人门事项：①promote(或 hold) 4 张 Equal 页→canonical，perf-baseline 连带裁 perf_baseline_source 张力；②纠正 ADR0002(canonical) msTuner→msprof op（走 gate 的 compile/review，非手编）；③五页 1.2×→target_ratio 一致 rename，**ADR0006/0008 rename 未处理前不得 promote**。agent 不代做、不手升 canonical、不手编 cabinet prose。

**每页最小 promote/hold checklist（给 review 人）**：
- `verify-spec-pr-correspondence`：确认「先验证任务书↔PR 对应」表述与 minute 37223d6d 一致、无过度一般化 → promote。
- `root-cause-decoupling`：确认「解耦必要但不充分、上游先验证对应」措辞、Equal 动因案例已标作废 → promote。
- `task-spec-authoritative`：确认 Equal 例已撤、前置「先确认对应」已加 → promote。
- `perf-baseline`：**先裁** perf_baseline_source 张力（GPU 对比层可选/非必需是否成 canon）→ 裁定后再 promote，否则 hold。

### gates
- **①通读门（CLAUDE.md #6）**：Step 0，durable 工作前通读 canon，实读或显式声明复用同 session 通读。
- **②散文门（CLAUDE.md #5）**：Step 2 议程文本 + Step 6a 简表文字 → 一次 `codex exec` 审+修（stdin、禁落盘、禁新增 canon claim）后才 bureau:note/落盘，需复述发现/改动/风险。
- **③bureau 门（本 todo 主体）**：compile 半程经核查已完成（419b5d4）；capture=agent 的 bureau:note；promote canonical + 纠正 canonical 页=人门 bureau:review；canonical 页更正一律走 gate 的 tool 车道（compile/lint），**无人手编 prose、无 agent 手升**，agent 不越 review 促进步。
- **④结构门 + fail-stop（Step 1）**：bureau:status/inspect 确认 0 dangling/0 contradiction + grep 完整关键词无未作废残留；任一不过即停、不进 note。
- **⑤lint --apply 授权门（Step 5）**：改 canonical status 属消耗性变更，先经用户确认再跑；默认留 review。
- **⑥cc-suite:audit-fix = N/A**（本 todo 不动代码/脚本）。
- **⑦机器门 validate_acceptance_state.py = N/A**（纯 canon 治理、不产验收裁决、无 evidence/verdict）。

### blocked_deps
**现在本地全部可做的**：Step 0 通读、Step 1 只读核验（bureau:status/inspect 是 skill、可跑；不可用则 grep+Read fallback）、Step 2–4 起草+散文门+bureau:note、Step 6 简表——均不依赖真机/VPN/GPU/网络。

**卡用户决策的**：①Step 5 是否现在跑 `bureau:lint --apply`（改 ADR0002→stale，影响 review 视图与队列）；②4 张 Equal proposed 页升 canonical = 人门 `bureau:review`，agent 不能手升；③canonical 页更正（ADR0002 及 3 张『1.2×』canonical 页）走 gate 的 compile/lint→review，**agent 不手编 cabinet prose**；④perf-baseline 促进时 perf_baseline_source 张力裁定 = 人裁。

**工具面依赖**：bureau 命令面 = bureau 插件 skills（`bureau:status/inspect/note/review/lint/compile/query`，经 Skill 工具调用，非 CLI）；任一车道不可用时退回只读核验（grep + Read + 直读 `_compile-state.json`/frontmatter），并在交付标注。

**无真机 NPU/VPN/GPU 数据依赖**（纯 canon housekeeping）。相关但不属本 todo 的外发项（公开台账 push、PR#2 body 更正）属 T11 外发授权、另 todo，不在此卡。

### acceptance
1. **通读**：Step 0 完成，交付里说明「已通读 canon（覆盖 architecture+decisions+findings，X 页）」或声明复用同 session 通读。
2. **核验**：bureau:status/inspect 显示 4 张 Equal 页 `proposed` 在库、`37223d6d` compiled、结构 0 dangling/0 contradiction；**完整关键词** grep 复核 cabinet（architecture+decisions）无未作废的残留 Equal 裁决。范围**仅限 cabinet pages**——logbook/changes-brief 按 append-only 保留历史错误叙述、只要求有作废标记即可（据 codex #6 澄清）。
3. **capture**：当前 minute 里有一条经散文门的 `bureau:note`，显式列出『4 页待 promote + 2 survivor 待裁 + perf_baseline_source 张力 + rename-before-promote 约束』的完整路径/tier/建议改法（review 一看即可照做），并注明「survivors 进 review 视图需 lint --apply/compile」。
4. **survivors 可见性**：明确记录 survivors 当前是否已进 review 视图——若用户批了 Step 5 则已进（ADR0002 stale/proposed 修订）；若未批则显式标注「未进视图、待人门一并处置」。
5. **简表**：`doc/oprunway-changes-brief.md` 追加了对应一行，且衔接 07-09 旧待办（3 页待更正→4 页 proposed + review 队列）。
6. **交接**：清单明确区分 agent 已做（通读/核验/capture）vs 人门待做（promote 4 页 + 纠正 ADR0002 + 1.2×→target_ratio 5 页 + rename-before-promote 硬约束）。

> 注：canonical 促进本身**不计入**本 todo 的 agent 完成条件——那是人门里程碑，另行追踪。

### open_decisions
1. **是否现在跑 `bureau:lint --apply`**（agent 置 ADR0002→stale + 写 superseded/drift 边）让 survivors 进 review 视图，还是全留给 review 一次处置——**默认『留 review』**（改 canonical status 属消耗性）；但须知：不跑 lint --apply 也不 compile，则 2 survivor **不会**出现在 review 视图、只埋在 note/findings.md 里，review 人需主动查 findings.md。请用户拍板。
2. **『1.2×』drift 修法**：2 张 proposed 页（ADR0006/0008）是否让 agent 先 compile 半修，还是与 3 张 canonical 页并作一次处置——**建议后者**（避免半修留 3 页仍写 1.2× 的不一致），并对 ADR0006/0008 加硬约束『rename 未处理前不得 promote』，防被单独 promote 把 drift 固化。
3. **4 张 Equal 页是『直接 promote』还是『hold』**：perf-baseline 页仍挂 perf_baseline_source 张力，宜连带裁定后再升；其余 3 页内容已 compile 到位，可直接 promote，按每页 checklist。请用户定。
4. **其余跨 todo 岔口**（T3 canon 分解冲突、T9 发布形态、T11 外发授权）不阻塞 T10，仅登记提示，本 todo 不处理。

---

## T11-external-publish — 外发：公开台账 push（已完成）核验 + PR#2 body Equal 旧结论作废（追加横幅、不覆盖）+ 本地台账 stale 注记清理

**codex 总判**：codex 真跑成（codex_ran_ok=true），verdict=needs-revision，共 14 项（3 high + 9 medium + 2 low）。本轮修订后 3 项 high 全部解决：#1 补 canon 通读+19 canonical 页已查证无约束、honor tier；#3 加执行环境 preflight 硬门并如实标网络受限（本地实测隧道 down + gh token invalid 佐证其网络担忧）；#13 加双镜像推前校验+失败即停+推后双远端复核。9 medium + 2 low 亦全 accept 并落进 steps/gates/acceptance。**无残留未解 high。** 说明：codex 报的「只读 FS」是其自身沙箱产物、不绑定真实施环境（本轮实测本地可写），已在 revisions 中据实澄清，非无视。plan 已经外部审计（codex）+ 本轮据审修订。

**可实施性**：no（部分可立即动手、关键外发被阻）。可立即做：T11b 本地台账清理（changes-brief stale 子句原地改 + 可选 todo 对齐）+ codex 散文门 + 本地 commit——FS 可写、无需网络。被阻部分：① 所有远端动作卡在**用户明示授权**（CLAUDE.md #2/#3）；② 且本轮 infra 实测不通——反向隧道 58231 down、gh lllyys token invalid、GitCode 推送凭据/codex exec 就绪性待确认，preflight 不过则禁止进入 push / gh pr edit；③ PR#2 body 在线核验须待网络恢复。故须先「用户授权 + 恢复隧道/重认证 gh + 确认 GitCode 凭据」，才能推进 T11a 的 PR#2 侧与任何 push。

**残余风险**：1) **PR#2 body 无法在线复核**：本轮隧道 down + gh token invalid，body/reviews/comments 是否已含 Equal 更正无法当场验证——已按 verify-first 如实标「待核」、不写「已完成」；网络恢复后须补做。2) **两镜像短时不一致**：T11b doc-cleanup 提交若推远端、第二个 remote 失败会致 GitHub/GitCode 暂差一条提交（非 Equal 更正本体，低风险）——已加失败即停+补偿+双远端复核护栏，但无法做到严格原子。3) **改已 merge PR body 属对外历史变更**：即便只追加横幅仍改动了 merged PR 的展示——已用「追加不覆盖+保留原描述+先存档」降到最小，但审计链上仍是一次事后编辑。4) **状态不符未收口**：台账记「待批」而 push 实测已完成，若用户预期外发尚未发生，需先排查是谁/哪次已推，再定收尾口径。5) GitCode 推送凭据的 git 使用方式尚未实证跑通（仅计划用 askpass/URL-token），首次 push 可能暴露认证细节问题。

**据 codex 修订**：逐条处理 codex 14 项 issue（含 3 项 high）。总体：全部 accept（无整条驳回），其中 #3 部分重构其措辞。

【high】
- #1（consistency·canon_grounding 与 CLAUDE.md #6 不一致）→ ACCEPT。已按 #6 实地通读相关 canon 切片：ADR verify-spec-pr（实测 status=proposed，已 honor 存疑 tier）+ task-spec-authoritative-over-pr + root-cause-decoupling 决策 + oprunway-acceptance-pipeline/acceptance-contract-evidence-chain/perf-baseline 架构 + lint/findings.md（2 survivor 与本任务无关）。并把原「无 canonical 页约束」从「假设」改为「已查证」：仓内 19 个 canonical 页逐类扫过、确认无一约束 push/PR-body 机械操作，治理归 CLAUDE.md #2/#3/#5。新增「canon 依据」小节固化。
- #3（feasibility·只读 FS + 网络受限，实施前提不成立）→ ACCEPT（部分重构措辞）。新增 step1 执行环境 preflight 硬门（可写性/隧道/gh/GitCode 凭据/codex exec），不过不进 push 分支。同时纠正 codex 措辞：「只读 FS」是 codex 自身沙箱产物、不绑定真 Claude Code 实施环境（本轮实测本地可写）；但「网络受限」被本轮独立证实为真（隧道 58231 refused + gh token invalid）→ preflight 正当、且 PR#2 body 本轮如实标「待核」不假装已核。
- #13（risk·先 push origin 再 push gitcode，第二个失败致两镜像不一致）→ ACCEPT。step6 加：推前双 remote 同时校验凭据+OID 基线、push 失败即停+记补偿动作、推后双远端复核 OID==HEAD。注：本任务待推的仅 doc-cleanup 提交（Equal 更正本体已在两远端），风险低但仍加护栏。

【medium/low】
- #2 & #15（scratchpad/pr2-body.md 落点违 CLAUDE.md #4 / 清理未定）→ ACCEPT。改为会话 scratchpad 绝对路径或 mktemp、仓外临时文件、非持久 doc 产物、用后即删、收尾校验 untracked 干净。
- #4（blocked_deps 过度声称「唯一阻塞是用户授权」）→ ACCEPT。重写 blocked_deps 为显式 preflight 清单，并如实标出本轮 infra 阻塞（隧道 down、gh token invalid、GitCode 凭据/codex exec 待确认）——不再说「非基建」。
- #5（GitCode HTTPS token 如何被 git push 使用未说）→ ACCEPT。step1 加 credential.helper=/临时 GIT_ASKPASS/URL 内嵌 token（不落盘）+ 先 ls-remote 读验证凭据可过。
- #6（AskUserQuestion 执行器相关能力未确认）→ ACCEPT。step3 改为工具中立「向用户取得明确授权文本」，并列出必须覆盖的三项授权 (a)(b)(c)。
- #7（gh pr view comments 漏 review/commit/timeline comments）→ ACCEPT。step2 在线核查改用 GitHub API/GraphQL 一并查 issue comments + reviews + review comments + commit comments + timeline。
- #8（验收只查 OID、漏作者/邮箱/trailer/目标分支）→ ACCEPT。acceptance 加 git branch --show-current=main、git show -s --format=fuller 核 author/committer=lys、无 Claude trailer grep、fast-forward 基线。
- #9 & #14（PR body patch vs 覆盖未定 / 覆盖已 merge PR body 削弱审计链）→ ACCEPT（合并处理）。改为先 gh api 导出现 body 存档 → 仅顶部**追加**作废横幅、保留原描述 → 最小 diff → 过散文门 → apply；已含则不动。
- #10（「外发已完成」与「改 changes-brief 并推」混在同一 T11 验收）→ ACCEPT。拆 T11a（外发核验）/ T11b（本地台账清理），各自定义 acceptance，doc-push 授权不回灌进 T11a。
- #11（「无在用旧结论」不可操作）→ ACCEPT。定义 denylist/allowlist：真阳性/精度 fail 等词允许在作废横幅/删除线/历史区、禁止在当前裁决表与 PR#2 当前结论段（正是 acceptance-evidence doc 现行做法）。
- #12（顶部 T11 dated bullet 无精确位置/措辞、可能破坏横幅结构）→ ACCEPT。改为**原地**编辑 07-09 条现有 bullet 的 stale 子句、给出精确前后文，不新增顶部 bullet；散文门只审这段 diff。

无驳回项。唯一「重构而非照单全收」的是 #3 的 read-only-FS 表述（澄清为 codex 沙箱产物），但其网络受限 + preflight 主张已全盘吸收。

### 终版 plan

## T11 · 外发：公开镜像一致性核验 + PR#2 body 更正 + 本地台账清理

> 依 codex needs-revision 审计逐条修订后的终版。**实地只读核查（2026-07-09 本次）已把「假设」换成「事实」**：push 已完成且两远端与本地 HEAD 一致；但**当前网络隧道 down + gh token 失效**，PR#2 body 本轮无法在线核验——故本 plan 显式拆门、加 preflight、把「无法验证」如实标出，不假装已核。

### 拆分（吸收 codex#10：避免「已完成」与「待授权」混判）
- **T11a — 外发核验（大部分已满足）**：两公开镜像 main 含 Equal 翻案更正、且 PR#2 body 无「在用旧结论」。push 部分**已完成并已核**；PR#2 body 部分**待网络恢复后在线核验**（可能已含更正，见 approach）。
- **T11b — 本地台账清理（纯本地、可立即动手）**：把 `changes-brief` 的 stale「待批（外发）」注记改为如实状态；（可选）对齐 `todo`。其提交是否推远端，属独立的用户授权决定，**不回灌进 T11a 验收**。

---

### goal
让 Equal 翻案后的更正对外可见且一致：两公开镜像（GitHub `lllyys/OpRunway` + GitCode `brian66237/OpRunway`）main 含更正提交（**已达成**）；已 merge 的 PR#2 body 用**追加作废横幅**（非覆盖）方式把 Equal 相关旧结论明标作废，保留原描述作审计链；本地台账不再残留「待批（外发）」stale 注记。全部远端动作在用户明示授权、署名 lys/lllyys、通过 preflight 后执行。

### approach
verify-first + 条件执行 + preflight 硬闸 + 台账对账。
- **push 已完成**：`git ls-remote origin/gitcode refs/heads/main` 均 = `4dcd355` = 本地 HEAD，`git status` clean，无未推的 Equal 更正提交（`419b5d4` 等已在两远端）。故 T11a 的 push 侧实质已满足，退化为「核对 + 事后双远端复核」。
- **PR#2 body 待在线核验**：更正内容的**权威源**是用户 2026-07-09 正式拍板（已固化在 `doc/oprunway-acceptance-evidence.md` 顶部作废横幅 + `doc/oprunway-changes-brief.md` 全局更正横幅，两 doc 已 commit+push）。`acceptance-evidence` 页脚已声明「本 doc 是 PR#2 的镜像说明」，故 body 只需与这份台账镜像一致，**不重推 Equal 归因**。先前会话据只读核查曾报「body 已含 2026-07-09 更正」，但**本轮网络 down 无法复验**——按 verify-first 纪律，未在线复核前一律按「待核」处理，不写「已完成」。
- **更正若需落 body，用追加不用覆盖**（codex#9/#14）：先 `gh api` 导出现 body 存档 → 若缺更正，仅在 body **顶部追加**「⚠ 以下旧结论作废（Equal）」横幅、保留原描述 → 过散文门 → apply；已含则不动。
- 对齐 CLAUDE.md #2/#3：即便本用户自己的仓，push/PR-edit 时机也须用户明示确认。

### canon 依据（吸收 codex#1：通读相关 canon 后再下结论，honor trust tier）
开工前已按 CLAUDE.md #6 通读与 T11 相关的 canon 切片，honor 各页 trust tier：
1. `canon/decisions/verify-spec-pr-correspondence-before-acceptance.md` — **status=proposed（未 settle·存疑 tier）**：解释「为何 Equal 一切结论作废」（任务书↔PR 配错 + 该社区任务未验收/无交付 PR）。T11 只把这条**已由用户拍板**的更正对外发布，**不重推**归因；引用一律标「proposed·未 settle」。
2. `canon/decisions/task-spec-authoritative-over-pr.md`、`root-cause-decoupling-before-attribution.md` — Equal 归因链的上游/相邻决策，读作背景、不载重。
3. `canon/architecture/oprunway-acceptance-pipeline.md`、`acceptance-contract-evidence-chain.md`、`perf-baseline-by-reference-source.md` — 已通读，确认**均不约束** push / PR-body 这类机械外发操作。
4. `canon/lint/findings.md` — 2 survivor（ADR 0002 msTuner→msprof op superseded；1.2×→target_ratio drift），与 Equal 外发无关、不阻塞 T11。
5. **对「无 canonical 页约束」的核实**：仓内现有 **19 个 canonical 页**，逐类目扫过后确认**没有任何 canonical 页对 push/PR-body 机械操作设约束**——治理靠 CLAUDE.md #2（不 push 除非明示、对外动作先同意、署名 lys/lllyys）/#3（副作用先确认）/#5（散文 codex exec 门），非 canon。此结论是「已查证」而非「假设无」，纠正原 plan 的措辞不一致。

### files
| path | action | purpose |
|---|---|---|
| `doc/oprunway-changes-brief.md` | edit | **T11b 主改**：把 07-09 条尾 stale 注记里的「公开台账 push + PR#2 body 更正待批（外发）」替换为如实状态（push 已完成并核；PR#2 body 待网络恢复后在线复核）。**精确改法见 steps#4**（原地改现有 bullet 的该子句，不新增顶部 bullet 以免扰动全局更正横幅/倒序结构——吸收 codex#12）。散文→codex exec 门。 |
| `doc/oprunway-todo.md` | edit（可选、轻） | 硬约束 #1（line 8-9）现注「上报取消」；如需补一句「push 台账已完成、PR#2 body 待复核」以免与 changes-brief 不一致。不新增 T11 独立条目。散文→codex exec 门。 |
| `$SCRATCH/pr2-body-*.txt`（会话 scratchpad 绝对路径 / `mktemp`，**仓外**） | create（仅漂移分支用） | **非持久 doc 产物**、仅作 `gh pr edit --body-file` 的临时输入（吸收 codex#2/#15）：不落 `doc/`、不落仓内相对 `scratchpad/`，用后即删，收尾 `git status` 校验 untracked 干净。 |

### steps
1. **执行环境 preflight（吸收 codex#3/#4/#5/#6）——最先跑、不过不进执行分支**：
   - 可写性：`: > $SCRATCH/.wtest && rm $SCRATCH/.wtest`（注：codex 报「只读 FS」是其自身沙箱产物，**不绑定**真 Claude Code 实施环境；此步只做客观确认）。
   - 网络/隧道：探测 `http://127.0.0.1:58231`（proxy）连通性——**本轮实测 connection refused=隧道 down**。
   - GitHub：`gh auth status`——**本轮实测 lllyys token invalid**。
   - GitCode 推送凭据：确认 `GITCODE_TOKEN` 就绪且**说明如何被 git 使用**——用 `git -c credential.helper= -c http.extraheader=…` 或临时 `GIT_ASKPASS`/URL 内嵌 token（不落盘、不写 config），先 `git ls-remote gitcode`（读操作）验证凭据能过，再谈 push。
   - codex exec：确认 `codex exec` CLI 可用（散文门依赖）。
   - **任一 remote 相关项不通 → 只允许「本地 T11b 改文 + 审计 + 出补丁文本」，禁止进入 push / gh pr edit 分支**，并把「因 X 不可用、外发待恢复」如实记账。
2. **只读核查外发现状（本地/在线，无副作用）**：
   - 已做（本地）：`git ls-remote origin/gitcode refs/heads/main` vs `git rev-parse HEAD` → 均 `4dcd355`；`git status --porcelain` clean；`git branch --show-current`=main。
   - 待网络恢复（在线，吸收 codex#7）：不止 `gh pr view 2 --json comments`——用 GitHub **API/GraphQL** 一并查 issue comments、**reviews、review comments、commit comments、timeline**，确认无「在用旧结论」；导出现 body 存档到 `$SCRATCH`。
   - 记录 delta（当前：push 侧 delta=空；PR#2 body 侧=未知/待核）。
3. **向用户请求明示授权（吸收 codex#6，工具中立）**：不假定 `AskUserQuestion` 可用——以任意可用交互方式向用户取得**明确授权文本**，需覆盖：(a) 是否授权推 T11b 的 doc 清理提交到两远端、及时机；(b) 若在线核查发现 PR#2 body 缺更正，是否授权 `gh pr edit`（追加横幅方式）；(c) 若发现评论/review 带旧结论，是否授权追评更正。任何远端动作须署名 lys/lllyys。
4. **T11b 分支（纯本地、可立即）——精确对账改文（吸收 codex#11/#12）**：
   - `changes-brief` line 20 原文子句：`公开台账 push + PR#2 body 更正待批（外发）` → 改为：`公开台账 push=已完成并核（origin/main + gitcode/main 均 @4dcd355=本地 HEAD、树 clean）；PR#2 body 更正待在线复核（网络恢复后按追加横幅核对，不覆盖原文）`。**其余不动**（保留全局更正横幅、倒序结构、07-08 作废划线区）。
   - **denylist/allowlist（可操作化「无在用旧结论」）**：`真阳性 / 精度 fail / A3 未达标 / FAIL(精度)` 等词——**允许**出现在作废横幅、`~~删除线~~`、历史流水区；**禁止**出现在当前裁决表、PR#2 body 当前结论段。核查按此判，不做字面全禁。
5. **T11b：过散文门后提交（本地）**：step4 改文走 `codex exec` 散文门（审→修，只审这段 diff）。通过后 `git commit`，author=lys（git user 已为 lys），信息中文、**不加 Claude co-author/session trailer**（仓内既定：只署用户）。
6. **T11a/T11b：经用户 OK + preflight 通过后推两远端（吸收 codex#13 双镜像一致性）**：
   - 推前：两 remote 同时校验凭据 + `ls-remote` OID 基线（确认远端 main 就是 `4dcd355`、本次 fast-forward、无他人新提交)。
   - `git push origin main` → `git push gitcode main`。**若第二个失败 → 立即停、记账、给补偿动作（重试 gitcode / 或明示两镜像暂差一条 doc-cleanup 提交，低风险非 Equal 更正本体）**，不放任静默不一致。
   - 推后：重跑 step2 只读核查，**双远端 OID 必须都 == 新 HEAD** 才算收口。
7. **漂移分支（仅当 step2 在线核查发现 PR#2 body 缺更正）**：`gh api` 导出现 body 存档 → 生成**最小 diff**：仅在 body 顶部**追加**「⚠ 2026-07-09 更正：以下关于 Equal 的旧结论（真阳性/A3 未达标）作废——#2890 系误配、Equal 社区任务未验收，详见镜像 doc」横幅、**保留原描述**（吸收 codex#9/#14 保审计链）→ 过散文门 → `gh pr edit 2 --repo lllyys/OpRunway --body-file $SCRATCH/pr2-body-*.txt` → **用后删临时文件** → 若评论/review 带旧结论，按 step3(c) 授权后追评。
8. **收尾核验 + 记账**：重跑 step2（两远端 main==HEAD、PR#2 body/reviews/comments 无在用旧结论、`git status` untracked 干净、临时 body 文件已删）；把最终结果写进 `changes-brief`（与 step4 无出入则确认）；全程中文汇报「核到什么、改了什么、还剩什么」。

### gates
1. **散文门（codex exec）**：changes-brief/todo 对账改文、以及漂移分支下的 PR#2 追加横幅文本，提交/发布前先过 `codex exec` 审+修（CLAUDE.md #5）。
2. **执行环境 preflight 门（新增）**：step1 未全绿 → 禁止进入任何 push / `gh pr edit` / 追评分支，只做本地改文+审计+补丁文本。
3. **用户授权门（CLAUDE.md #2/#3，最硬前置）**：任何 push / PR-body 编辑 / 追评，即便本用户自己的仓，须用户明示同意 + 定时机 + 署名 lys/lllyys。
4. **不触发**：`cc-suite:audit-fix`（T11 不改代码/脚本）；bureau 门（T11 不动 canon，Equal 的 canon 更正是另一条 todo）；机器门 `validate_acceptance_state.py`（T11 不产裁决，上报已取消、无缺陷可报）。

### blocked_deps（吸收 codex#3/#4/#5：显式 preflight，去掉「唯一阻塞是用户授权」的过度声称）
- **本地可立即动手**：T11b 改文 + 散文门 + commit（FS 可写、`codex exec` 就绪）；全部本地只读核查（已做，push 侧结论=已完成）。
- **当前实测被 infra 阻塞（非仅用户授权）**：① 反向隧道 `58231` **down**（proxy connection refused）→ 走代理的在线核查/操作暂不可用；② `gh` 的 lllyys token **invalid**（keyring）→ GitHub API/`gh pr edit` 暂不可用，需 `gh auth login` 重认证；③ GitCode 推送凭据 `GITCODE_TOKEN` 就绪性 + git 使用方式待确认；④ `codex exec` 可用性待确认。
- **另需与用户澄清状态不符**：todo/changes-brief 记「待批（外发）」，而 push 实测已完成 → 请用户确认按「已完成对账收尾」，还是他预期外发尚未发生（需一起排查是谁/哪次已推）。
- 不依赖真机 NPU / GPU 数据。

### acceptance（吸收 codex#8/#10：分门定义 + 补作者/分支/trailer 核查）
**T11a（外发核验）**：
1. `git ls-remote origin/gitcode refs/heads/main` 均 == 本地 HEAD、无未推 Equal 更正提交、树 clean、`git branch --show-current`=main。
2. 承载 Equal 更正的提交经 `git show -s --format=fuller` 核：author/committer=lys、**无 Claude co-author/session trailer**（grep 校验）、落在 main。
3. **网络恢复后**在线核 PR#2：body 含 Equal 作废措辞（无有效被测 PR / 结论作废 / 任务书↔PR 配错），按 denylist/allowlist——当前裁决段/结论段无「真阳性/精度 fail」在用；**reviews / review comments / issue comments / commit comments 均无在用旧结论**。（网络未恢复前，此项标「待核」，不计入「已完成」。）
**T11b（本地台账清理）**：
4. `changes-brief` 的 stale「待批（外发）」子句已改为如实状态（push 已完成并核；PR#2 body 待复核），全局更正横幅/倒序结构未被破坏；（如动 todo）todo 与之一致。
5. 改文过 codex 散文门。
6. 若其提交被推远端：双远端 OID == 新 HEAD、无两镜像不一致遗留、临时文件已删、untracked 干净。全部远端动作在用户明示授权下、署名 lys/lllyys。

### open_decisions
1. **外发时机/授权**：本用户自己仓虽可直接做，仍须用户明示同意并定 push 与 `gh pr edit` 时机（CLAUDE.md #2/#3）。
2. **状态不符如何收口**：push 实测已完成而台账记「待批」——请用户确认是「按已完成对账收尾」还是他预期尚未发生。
3. **对账范围**：只改 changes-brief，还是同步补 todo 硬约束 #1 一句——请用户定。
4. **PR#2 body 是否需动**：待网络恢复在线核；若已含更正则不动，缺则**追加横幅不覆盖**。
5. 署名确认：新提交 author=lys、PR 操作账号=lllyys，如无异议即按此。
（T3 canon 分解、T9 发布形态等其它 todo 岔口不属 T11，不列。）

---

