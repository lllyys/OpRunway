---
name: acceptance-workflow
description: OpRunway 算子验收编排的 CP-A..E 检查点状态机——定义薄 primary orchestrator 如何调度 3 个 subagent、串工件门禁、把 pass/fail 判定唯一交给确定性脚本链（validator/perf_compare/三级验收门），供 op-acceptance primary 首响应加载。
---

# acceptance-workflow — CP-A..E 验收编排状态机（Layer 2 workflow skill）

本 skill 是 `op-acceptance`（`mode:primary` orchestrator）的**唯一状态机脑子**：它规定验收怎么分段（CP-A..E）、每段派哪个 subagent、产哪个工件、哪级门在哪跑、失败怎么路由。**primary 首响应即加载本 skill，禁裸调 subagent。**

设 `${OPRUNWAY_PLUGIN_ROOT}` = 本插件根（含 `acc-common/`、`skills/`、`agents/`）——这是**本插件根的中立主变量**；Claude 下等价 `${CLAUDE_PLUGIN_ROOT}`（由 harness 自动设），**Codex 等非 Claude 运行时须显式 `export OPRUNWAY_PLUGIN_ROOT=<插件根绝对路径>`**。
⚠ 因此本页**可执行命令**一律写成 `${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}`（自兜底：主变量优先、缺了回落 Claude 别名），照抄即可跑、**不依赖谁记得先 export**；散文里提路径时只写中立主变量 `${OPRUNWAY_PLUGIN_ROOT}`。全程中文。产物落用户 CWD 的 `reports/<op>/`。

**本 skill 只调三级验收门、不重实现判定**——判定脑子在确定性脚本链里（见 §0），编排层只搬工件、串流程、引用裁决。

---

## 0. 铁律（贯穿全流程，每段都受约束）

1. **判定唯一归确定性脚本链**：`validator.py`（精度）+ `perf_compare.py`（性能）+ `validate_acceptance_state.py`（三级完整性门）→ 门控后由 `run_workflow.py` 写 `acceptance.json`。**编排层（primary）与 subagent 都不自行判 pass/fail，只逐字引用确定性产物的裁决并标来源**（ADR 0007）——这是「不得自行判定、只能引用」，**不是「绝不提 pass/fail」**：可以复述脚本判出的 pass/fail，但不能自己判。

2. **primary 边界**：primary **可直接跑「无 NL 生成、无判定」的确定性脚本**——`fetch_source.py`（取材 + `source_facts.json`）、`validate_taskdoc_input.py`（只复核任务书输入校验工件的结构与绑定、机械派生阻断清单）、`gen_cases.py --dry-run --ledger-out <case_plan.json>`（契约自检 + durable 计划账本）、`validate_preparation_state.py`（只判非真机准备是否可复用）、`preflight_aclnn.py`（只做 PR header↔spec slots 静态对账）、`validate_acceptance_state.py`（复核门）、`check_manifest_sync.py`（漂移门），用 Bash 幕后跑。primary **不做 NL 生成的 durable 工件**（spec / runner 一律派 subagent），**不自行判 pass/fail**（归确定性脚本链），首响应先加载本 skill、**禁裸调 subagent**。

3. **subagent 边界**：每个 subagent **单轮、禁内部循环、禁跨阶段、不自行判定，只回结构化摘要给 orchestrator**。循环由 primary 控（如 dry-run 契约自检异常 → 再派 `refine_spec`），subagent 自己不多轮迭代。

4. **三级门在 `run_workflow.py` 内部**：`run_workflow.py` **一次性串 Task1→2→3**，末尾**统一校门**（`validate_acceptance_state` 的 task1/task2/task3 三级，读**落盘** evidence 独立复核）——是**批量驱动、非阶段间实时阻断**，**不是** orchestrator 分阶段单独调度的 stage。验收门 `validate_acceptance_state.py` STATUS: FAILED → **不出 pass 裁决；仍由 `run_workflow` 写 `acceptance.json.overall="BLOCKED(验收门未过)"`（exit 1）**（验收门未过=证据不可信/不完整）。「不推进下一 Task / 停在当前阶段」是 **agent 编排纪律**，不是脚本里的实时闸。

5. **对外单一对话入口、脚本幕后**（canon conversational-agent-sole-delivery-form·proposed·未 settle，载重前需核）：用户全程只用自然语言（给「任务书 + PR」）；`python3 …` 是 primary 的内部实现，Bash 幕后跑，**不展示脚本命令、不让用户手敲**。缺东西（任务书 / PR / NPU-VPN 开没开 / 目标机是 a3 还是 a5）用对话问。⚠ **别问「mock 还是真机」——验收只有真机一条路**。`OPRUNWAY_*`（真实机器名 / 远端路径 / token）**走环境变量、不写进仓**。**副作用先确认**（真机 clone / build / 跑测、对外动作先列计划点头再做）。

---

## 1. 状态即工件 · 断点续跑

**工件即状态**：每个 CP 的推进由「落盘工件是否存在且合法」判定，没有独立的状态文件。中断后重启，primary **先扫 `reports/<op>/` 现有工件**，从缺口处续跑，不重跑已完成段。

| 工件 | 由哪个 CP 产 | 存在即代表 | 续跑判据 |
|---|---|---|---|
| `source_facts.json` | CP-A | 任务书字节、PR head 与关键文件 ref/摘要已形成内容身份 | envelope 摘要有效且 `completeness.status=complete`；否则 MISS/BLOCKED |
| `correspondence.json` | CP-A | 对应校验已落盘（读 `status` 定去留） | `status=confirmed` 且 `source_facts_digest` 等于当前事实包才进 CP-B；`mismatch/empty_task` 停 |
| `taskdoc_validation.json` + `taskdoc_validation_receipt.json` | CP-B0（`validate_taskdoc` + primary inline `validate_taskdoc_input.py`） | 任务书输入是否足以充当验收依据已逐项判过并机械复核 | receipt `status ∈ {PASSED, PASSED_WITH_PENDING}` 且 `source_facts_digest` 等于当前事实包才进 `extract_spec`；`NEEDS_USER` 停下问用户；`BLOCKED` 重做 CP-B0 |
| `<op>.spec.json`（含 `task_pr_gaps`） | CP-B（`extract_spec`） | spec 已抽 | 缺 → 派 `extract_spec` |
| `case_plan.json` | CP-B（primary inline `gen_cases.py --dry-run --ledger-out`） | 用例计划及 spec/planner/golden 依赖已结构化落盘 | `validate_preparation_state.py` 返回 `REUSABLE` 才复用；MISS 重做 CP-A/B 对应缺口，BLOCKED 停止并报告损坏 |
| `preparation_receipt.json` | CP-B（primary） | 上述非真机工件绑定已复核 | 只表示 `scope=non-real-machine-preparation-only`；`acceptance_verdict=null`，不得当验收 PASS |
| `aclnn_preflight.json` | CP-C0（primary；`aclnn_py` / `cpp_extension`） | PR head header 与 spec call variants/slots 静态对账完成 | 续跑总是重算；cpp_extension 必须转入独立 build/load trust gate，不得复用 ctypes receipt |
| `oprunway_<op>_runner.cpp`（自检证据满足） | CP-C（`gen_runner`→`verify_runner`） | runner 已锚定 example；由 acc-runner-dev 的 runner 自检证据满足/不满足纪律保证（当前**非代码强制 sidecar 硬门、待补**） | 自检证据不满足则停在 CP-C、不上真机 |
| `work/aclnn_harness_trust.json`（仅 `aclnn_py`） | CP-C（`acc-verify-rootcause:verify_aclnn_harness`） | 内容寻址收据为 `TRUSTED_FOR_CP_D`，且绑定当前完整 caseset/spec/preflight、见证输入+golden+输出真实字节、golden 源码、PR/build/toolkit/SoC/符号与执行逻辑 | `run_workflow` 在正式 adapter 前按当前环境强制复核；缺失、字节漂移或执行来源漂移均停在 CP-C |
| `evidence.json` / `verdict.json` / `baseline.json`（仅有基线时）/ `perf_report.json` / `acceptance.json`（真机裁决） | CP-D；mode 据 form 派生：cpp→new_example、aclnn_py→aclnn_py、cpp_extension→cpp_extension | 真机一次原子跑完、门已校 | `acceptance.json.overall` 非 PASS 且非门问题 → 派 `rootcause` |
| 中文验收报告 | CP-E（primary） | 报告已出 | — |

> 多算子：一份任务书含 N 个算子 → CP-B 产 N 份 spec，每份独立走 CP-B..E，工件按 `reports/<op>/` 分目录。

---

## 2. dispatch 契约（每次派 subagent 的固定模板）

primary 每次派 subagent，都按此六段给全，**不省略**（subagent 单轮、拿不到上下文就无法完成）：

| 契约段 | 内容 |
|---|---|
| **工作区** | `reports/<op>/`（及 `work/` 子目录）绝对/相对路径；`${OPRUNWAY_PLUGIN_ROOT}`（中立主变量；Claude 下等价 `${CLAUDE_PLUGIN_ROOT}`，Codex 等运行时须显式 export 为插件根——**派 subagent 时把解析出的绝对路径给全，别只给变量名**） |
| **dispatch_mode** | 本次的模式取值（见各 CP；这是**调度模式**，与 frontmatter 的 `mode:subagent` 不同名、不混用） |
| **输入工件** | 该 mode 需读的已落盘工件（如 `task_doc.md`+`pr_facts.json` / `<op>.spec.json` / gate error 文本） |
| **已确认约束** | 用户在本轮/既有工件中已经明确的范围与选择（例如保留的 dtype 全集、case_target、不得缩减真机 case）；没有则写 `[]`。subagent 直接消费，不得重新研究或反问同一项 |
| **验收标准** | 本轮「算干完」的判据（如 spec 自检项全过、runner 逐元素等手算 golden、run_npu 出全套工件+门已校） |
| **本次产出** | 要落盘的工件名 + 回给 orchestrator 的**结构化摘要**字段 |

**subagent 回执硬约束**：单轮完成、禁内部循环、禁跨阶段、不自行判 pass/fail，**只回结构化摘要**（产了什么工件 / 关键字段 / gaps / 是否 BLOCKED 及原因）给 orchestrator，由 primary 决定下一步。CP-B 已有 `source_facts` evidence bundle 时禁止重新联网、遍历无关目录或重复研究已确认约束；只读 dispatch 点名工件与 skill 路由到的相关章节。单个 CP-B NL dispatch 的执行预算为 **300 秒**：预算将尽时把仍缺的事实写成结构化 gap/`needs_user` 并交还 primary，不能靠继续扩展阅读无界拖延；这是编排超时边界，不是免测或放宽契约。

**E2E 计时账本硬约束**：干净 session 开始时固定一份 plugin 快照，整轮不得吸收工作树后续改动；以 orchestrator 所在机的同一单调/epoch 时钟记录 `E2E start/end` 与 CP-A/B/C/D 边界，远端时钟只报子命令 duration、不得与本地绝对 epoch 混算。网络重连、NL dispatch 等编排开销必须留在 `E2E start→end` 总数中；首次 SSH 若在远端进程启动前失败，可确认无残留后重连，但不得把这段从总 E2E 隐去。CP-D 是原子命令，不为刷新状态而中断/重跑。

**CP-D 连接恢复边界**：首次 SSH，或首次 clone 在尚未创建远端工作树、写入 Git 对象和启动远端进程前发生的纯连接/传输失败，可确认无残留后至多恢复一次，耗时仍计入 E2E。工作树已经创建，或出现 object-not-found、checkout/HEAD mismatch、build、driver、workflow 错误后，均属于本轮语义/执行失败；同一 subagent 不得换 ref、补 fetch、重建或重跑。由 primary 另派新的干净 CP-D subagent。

---

## 3. CP-A..E 状态机

五个 CP 是**对话暂停点 + 工件门**，不是 run_workflow 内部的 stage。真机执行合并成**一个原子 CP-D**（Task2+Task3+三级门一次成）。

### CP-A 前置（primary 亲自，不派 subagent）

**目的**：取材 + 任务书↔PR 对应校验 + 环境/模式确认，识别并挡掉「未验收空任务 / 任务书↔PR 配错」。

- **取材**（确定性脚本，primary 直接跑）：`python3 ${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}/acc-common/fetch_source.py --taskdoc <路径|链接> --pr <PR链接> --out <work>` → `task_doc.md` + 逐字节 `task_doc.snapshot.md` + `pr_facts.json` + 内容寻址的 `source_facts.json`。快照在 CP-A/spec 之前即落，spec 与 golden 共用同一 SHA，不再后补回填；PR head、关键文件 ref 或任务书字节变化即新身份，`completeness=blocked` 不得复用。
- **对应校验**（落 `correspondence.json`，schema/枚举见 §4；canon verify-spec-pr-correspondence·proposed·未 settle，载重前需核）：靠三条证据合断——
  1. **改动落点目录**：`pr_facts.target_dir`（机器可比），对上任务书声明的算子目录；
  2. **issue / 追踪号**：**NL 读** `task_doc.md` 与 PR `title`（`pr_facts` **不抽 issue 号**，只能自然语言读），**非算子名字面匹配**；
  3. **用户确认**：证据摆给用户拍板。
- **环境确认**（`AskUserQuestion` **必由 primary 做**）：NPU/VPN 开没开、目标机按任务书硬件 × op_def 双源核定。验收只认真机；`spec.runner_form` 受控词表为 `{cpp, aclnn_py, cpp_extension}`，依次派生 `{new_example, aclnn_py, cpp_extension}`。mock/catlass 只能显式指定且不产真机裁决。
  ⚠ **别把 `new_example` 当唯一真机通路**：当前还有 `aclnn_py` 与 `cpp_extension`。历史 Median 60/60 来自 aclnn_py，只证明旧 caseset；迁移到 torch_parity + cpp_extension 后必须重跑，不得沿用旧 PASS。性能 baseline 仍逐字按任务书配置，不能从 runner form 反推。
- **产出**：`correspondence.json`。除既有字段外必须写入当前 `source_facts.json` envelope 的 `digest` 为 `source_facts_digest`；事实包变化后旧确认自动失效，须重新核对应关系。用户已经明确的范围/选择写入可选 `confirmed_constraints` 数组，后续 dispatch 原样传递，避免每个子任务重新澄清同一问题。`status=confirmed` → 进 CP-B；`mismatch`/`empty_task` → 出**程序结论（非 pass/fail）**并停跑；`needs_user_confirmation` → 摆证据、等用户拍板（**不自动 judge 空任务**——Equal #2890 配错作废血教训）。

### CP-B Task1 用例（dispatch + primary inline）

**目的**：先校验任务书输入是否足以充当验收依据（CP-B0），再任务书→spec + golden，并用 `--dry-run` 做**用例计划的契约自检**（不产任何裁决）。

- **先查热续跑，不先派 NL agent**：CP-A 已轻量刷新任务书/PR head 并得到当前 `source_facts` 后，若旧 spec/golden/case-plan/receipt 都存在，primary **先重跑** `validate_preparation_state.py`。结果 `REUSABLE` → 直接复用 CP-B 三件套、跳过 `extract_spec` / `gen_golden` / dry-run，进入 CP-C0；`MISS` → 只重做 checks 指向的最小缺口（source/correspondence 变化才重抽 spec，planner/golden 变化只重跑对应步骤）；`BLOCKED` → 停止并报告损坏。不得因为“可能有缓存”先照旧派完两次 NL 再查 receipt——那会让热续跑优化完全失效。
- **CP-B0 任务书输入校验门（先于 `extract_spec`，热续跑 `REUSABLE` 时随 CP-B 一并跳过）**：
  抽 spec 之前先回答更前面的问题——**这份任务书够不够格当验收依据**。受控清单
  `acc-common/taskdoc_validation_contract.json`（**18 项**：12 项无条件必须 + 2 项有性能要求时必须 +
  3 项条件必须 + 1 项可选），逐项判法在 `skills/acc-spec/references/taskdoc-validation.md`。
  1. **dispatch** `acc-spec-extractor`，`dispatch_mode = validate_taskdoc`：**只读 `task_doc.md` +
     `source_facts.json`，禁读 `pr_facts.json`/op_def/header**——「PR 里写了」补不了任务书的缺，
     那是 `task_pr_gaps` 的分工。产 `<work>/taskdoc_validation.json`，判 `satisfied` 必附任务书逐字原文。
  2. **primary inline**（确定性脚本，无 NL 生成）：`python3 ${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}/acc-common/validate_taskdoc_input.py --root <work> --out taskdoc_validation_receipt.json`。
     它只复核结构与绑定（18 项是否逐项对齐 · 引用是否真出自任务书 · 条件项适用性是否自洽 ·
     决策是否绑定当前 `source_facts` digest），按契约 `on_unsatisfied` **机械派生**清单，
     **不重判任务书内容对不对**，`acceptance_verdict` 恒 null。
  3. **路由按 STATUS 分**：`PASSED` / `PASSED_WITH_PENDING` → 进 `extract_spec`（pending 项写进
     CP-E 报告的「待确认项」，不阻断）；**`NEEDS_USER` → primary 用 `AskUserQuestion` 把
     `blocking_items` 一次性汇总问用户**（不逐项连问），每项三选一：**补充**（给出事实）/
     **豁免**（给出理由）/ **停止验收**（出程序结论、去找任务书负责人）。用户选择原样追加进
     `taskdoc_validation.json.decisions`（`source` 固定 `"user"`）后**重跑脚本**，
     转 `PASSED` 才继续；`supplied` 项的 `confirmed_constraints_candidates` 一并写入
     `correspondence.json.confirmed_constraints`，供后续 dispatch 原样传递、不再重复澄清。
     `BLOCKED` → 校验工件本身不可信（引用编造 / 项数不齐 / 事实包漂移），重做 CP-B0，**不得跳过**。
  ⚠ 决策绑 `source_facts_digest`：**任务书字节一变，本轮校验与用户决策整体失效**，须重做——
  与 `correspondence.json` 同一套失效语义。
  ⚠ 这个门**不产验收裁决**，只挡「输入不足以验收」；它也**判不出**任务书内容本身对不对——
  判宽（模糊的判成明确）会静默生效，唯一护栏是 ref 的判法 + 强制逐字引用。
- **dispatch** `acc-spec-extractor`，`dispatch_mode = extract_spec`：按六段契约读 `task_doc.md` + `task_doc.snapshot.md` + `pr_facts.json` + `source_facts.json` + `correspondence.json`（含 `confirmed_constraints`）→ `<op>.spec.json` + `task_pr_gaps`（缺项落 gaps 不臆造；多算子多 spec）。
- **dispatch** `acc-runner-dev`，`dispatch_mode = gen_golden`：读 `task_doc.md`+`spec` → 任务书快照入库 + `<ops_root>/<op>/golden.py`（真值口径走 **R3 两档链**；**PR/仓内参考实现禁作 golden 源**；后端生成期定死）→ 自跑 `check_golden.py <Op>` 出档位账本。**必须在 dry-run 之前**——`gen_cases` 缺 golden.py 即 fail-closed。
  “任务书快照入库”必须实际落在授权核验生效路径 `<ops_root>/<op>/task_doc.snapshot.md`，内容逐字来自
  当前 CP-A `task_doc.snapshot.md`，并先核 source-facts digest 与 golden contract 声明的 SHA。只在
  `source/` 留一份同 SHA 文件、却漏掉 op 目录生效副本，仍须 BLOCKED，不能口头视为等价。
  交付前还必须在远端执行 golden shape smoke：按 spec 的稳定能力轴选择能覆盖输出 rank 边界、first/middle/last dim、
  正/负轴、keep 双值和全部 active outputs 的最小见证矩阵，逐输出对拍 `golden_fn` 实际 shape/dtype 与
  `out_shape`/输出契约。存在合法 0-D 输出时必须显式覆盖；只通过 import/load 或 dry-run 不算 smoke 通过。
  smoke 只验证 golden 契约，不产验收裁决，也不得用 PR 实现作 oracle。
  路由**按退出码、不按档位数字**：**0**（可走）→ 进 dry-run；**2**（`needs_human_review`——tier 3 必然如此，⚠ **tier 1 也可能**：`multistep + oracle_method` 判 `(tier 1, 需人核)`）→ 进 dry-run 但**报告里显式标「golden 需人核」**；**1**（blocked / 词表不合规 / 缺件 / 账本自相矛盾 / 参数错误）→ **停在 CP-B**，把 `blocked_reason` 摆给用户，**不自动回落第二档**（R4）。
- **primary inline**（确定性脚本，无 NL 生成）：`python3 ${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}/acc-common/gen_cases.py <spec> --dry-run --ledger-out <work>/case_plan.json --source-facts <work>/source_facts.json --correspondence <work>/correspondence.json`。plan-only，查这些：用例预算落不落 `[S=强制下限, pool_max]` 区间 · dtype 分布 · 特殊场景（empty/scalar/边界/inf/ninf/nan）覆盖 · 被丢组合类 · `case_id` 唯一（撞则 raise） · per-case 种子确定性；并绑定 canonical spec、规划器源码、golden.py、source facts 与用户确认摘要。后两项必须成对提供；只绑定一半直接报错。
  ⚠ **能力边界（别当成旧 mock 自检的等价物）**：dry-run **不调 `golden_fn`、不落 `.npy`、不产任何裁决**；但它**会加载执行 `golden.py`**（取 `out_shape` 造规模预算）——所以对 golden 的覆盖是**半道**的：**缺文件 → 只记「未核」、不阻塞**；**文件在但坏了（语法错 / 顶层抛 / 必需导出不全）→ 当场抛、拦得住**。仍**验不了**：来源契约合不合规（那是 `check_golden.py` 的活）/ `oracle_source` 映射 / `validator` 判定链 / 三级门 / evidence 结构——**这些只有 CP-D 真机跑测才验得到**。（照本仓约定 golden.py 把 torch 延迟 import，故 dry-run 通常不拉 torch；某算子若在模块顶层 `import torch`，它会跟着 import。）CP-B 过了**不代表**用例链整体可用。
- **产出**：`<work>/taskdoc_validation.json`（subagent 产）+ `<work>/taskdoc_validation_receipt.json`（primary inline 产）+ `<op>.spec.json` + `<ops_root>/<op>/golden.py` + `<ops_root>/<op>/task_doc.snapshot.md`（三件均 subagent 产）+ `<work>/case_plan.json`。随后 primary 跑 `validate_preparation_state.py` 落 `preparation_receipt.json`；它只判非真机准备能否复用、**不产裁决**。`caseset.json` 仍由 CP-D 真机跑测时才落盘，绝不缓存复用。
- **路由**：dry-run 报错或账本异常（如预算区间不合理、重点 dtype 未覆盖、特殊场景缺失、id 撞）→ **dispatch** `acc-spec-extractor`，`dispatch_mode = refine_spec`（据报错文本修 spec）→ 重跑 dry-run。**契约自检没过先修 spec，别上真机。**
  ⚠ **`golden.py` 缺文件这一种 dry-run 查不出**（只记「未核」照常出计划），会一路漏到 CP-D 才炸；且 `refine_spec`（改 spec）**变不出 `golden.py`**——**golden 侧的问题一律回 `acc-runner-dev:gen_golden`，不在 refine 循环里空转**。

### CP-C runner（真机路径、需 NPU；dispatch）

**目的**：为算子生成锚定 example 的 per-op runner，并「验证-才-信」后才允许上真机。

- **CP-C0 纯静态前置（`runner_form ∈ {"aclnn_py","cpp_extension"}`，primary 亲自）**：运行 `preflight_aclnn.py`，只消费 PR-head header 正文和 spec，逐变体校 symbol、arity、参数顺序/名字/role/ctype。cpp_extension 的 `required_next_gate` 必须为 `CPP_EXTENSION_BUILD_LOAD_AND_HARNESS_TRUST_GATE`。
- **前置**：先确认用户已开 NPU/VPN（CP-A 已问）。
- **按 form 分流**：`runner_form=cpp` 才 dispatch `acc-runner-dev:gen_runner` → `verify_runner`；`runner_form=aclnn_py` **不派这两个 mode**，直接使用 CP-C0 事实进入下述 harness 真机信任门。cpp 路仍先过 scope gate——ops-<族> 仓·aclnn 两段式·opp 安装型（含非 experimental 子树）；catlass/非 aclnn 接口/双实现/未支持 dtype → 返回 `BLOCKED` / 转 P3，**不硬塞**。过 gate 后据 `spec` + `pr_facts.key_files` 的 `test_aclnn_*.cpp` **锚定 example 不猜**，生成 `oprunway_<op>_runner.cpp` + 选构建路径。
  - `runner_form=cpp_extension`：不派手写 runner；codegen 生成官方 bundle 与 invocation plan。真机 driver 收据必须绑定 spec/caseset/manifest/plan/source/setup/ELF、torch/torch_npu/CANN/SoC、独立 namespace/schema、vendor 库与符号归属，缺项或漂移停在 CP-C。
  - **⚠ `spec.runner_form == "aclnn_py"`（torch 对标 · ctypes-aclnn runner form）放行、且路径不同**（蓝图 §3 组件⑥/§4.1）：此形态**无 per-op runner 源**（op 工程即 DUT，`aclnn_runtime` 的 ctypes runner 完全 op-中立、从 header 推 arity），**不生成 `oprunway_<op>_runner.cpp`**。CP-C0 只提前消掉重复的 header/spec 研究，**不替代** scope gate 与信任门。scope gate 仍校 **ops-<族>仓形态**（**仓根** `build.sh` + `<op_subdir>/op_host/` + **在 `<op_subdir>` 下（有界递归，含 `op_host/op_api/`）能找到** `aclnn_*.h`（剔 `*_impl.h`），由 `aclnn_adapter.find_aclnn_project` 复核 + 逐段软链守卫）。⚠ **接口头落点不预设是哪一层**：PR6429 真实布局是 `<op_subdir>/op_host/op_api/aclnn_median.h`，`<op_subdir>/` 下**没有** `op_api/`（2026-07-24 dogfood 实测订正；旧文的 `<op_subdir>/op_api/aclnn_*.h` 是错的，钉死一层会把真 PR 判成「非域内」硬阻塞）。⚠ **不要求 per-op `build.sh`、不要求 `op_graph/`**——2026-07-24 实测坐实 ops-nn 实验算子（PR6429 median）二者皆无、build 走**仓根** `build.sh --pkg --experimental --ops=<op>`（见 `doc/oprunway-torch-baseline-design.md` §9.4/§9.6）；缺件 / 非标准两段式 / 有 opaque descriptor → `BLOCKED`「不支持的接口能力」，**不硬塞、不自动归某类 adapter**（域内假设：无状态 / 标准 aclnn 两段式 / 无 opaque descriptor）。过 gate → **跳过 per-op `verify_runner`（无 runner 源可自检），但必须完成下条的 aclnn_py harness 真机信任门后，方可进入 CP-D（`--mode aclnn_py`）**——**「无源可自检」≠「免验证」**，别把静态 preflight/scope gate 通过当成放行。
- **dispatch** `acc-runner-dev`，`dispatch_mode = verify_runner`（⚠ **仅 `runner_form == "cpp"`**）：造手算 golden 小用例、逐元素比，形成 runner 自检证据（满足/不满足）。
- **产出**（**按 form 分流，别混**）：
  - `runner_form != "aclnn_py"`（cpp runner v1）：自检证据满足的 `oprunway_<op>_runner.cpp` + 构建路径配置。
  - **`runner_form == "aclnn_py"`：无 runner 源可产**，产出 =「**仓形态/接口签名检查结果** + **harness 真机自检证据**」两项（下条）。
- **⚠ `aclnn_py` 的 harness 信任门（等价于 cpp 的 verify_runner，不可跳过）**：dispatch `acc-verify-rootcause`，`dispatch_mode=verify_aclnn_harness`。先在目标真机用正式 `gen_cases.py <spec> <report-root>/work <report-root>/caseset.json` 生成完整 caseset/golden，再运行 `verify_aclnn_harness.py --root <report-root> --spec ops/<Op>/<Op>.spec.json --caseset caseset.json --preflight work/aclnn_preflight.json --out work/aclnn_harness_trust.json`。脚本按**能力与契约**确定性取小见证集：本轮每种实际输入 dtype、每个签名/slot 变体；接口实际含标量 attr / 多输出时各至少一例；逐输出与绑定的 CPU `torch` golden 按既定 policy 对拍。它会执行真机 build/install（来源完全一致且显式允许时可按 provenance 复用）、部署清目录、NPU exec/readback，属于有副作用真机动作，须沿用用户对本轮真机实验的明确确认。成功只产 `TRUSTED_FOR_CP_D` 的内容寻址收据，`acceptance_verdict=null`，**不删正式 case、不改精度标准、不跑/改性能采集**。收据绑定见证输入/golden/输出真实字节、golden 源码、PR/build/toolkit/SoC/符号与执行逻辑；`run_workflow` 在正式 adapter 前用本轮重新生成的完整 caseset及当前环境强制复核，缺失/漂移/对拍失败 → 停在 CP-C。
- **路由**：**runner/harness 自检证据不满足 → 停在 CP-C、不上正式 Task2/Task3**；scope gate BLOCKED → 停在 CP-C，出程序结论（转 P3 / 需扩 adapter），不进 CP-D。harness 收据是代码硬门，不是 agent 口头纪律；算子 acceptance 裁决仍只来自 `validator.py` / `perf_compare.py` / `validate_acceptance_state.py`（ADR 0007）。

### CP-D 真机跑测（一次原子；dispatch）

**目的**：一次原子跑完 Task2 精度 + Task3 性能 + 三级门，落全套裁决工件。

- **dispatch** `acc-verify-rootcause`，`dispatch_mode = run_npu`：`python3 ${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}/acc-common/run_workflow.py <spec> --mode <mode> --out reports/<op>/`（`OPRUNWAY_*` 指真实机器/路径，不写进仓）。**`<mode>` 据 `spec.runner_form` 定**：cpp runner v1 → `--mode new_example`（`OPRUNWAY_*` 见 repo_adapter._ne_cfg）；`runner_form==aclnn_py`（torch 对标）→ `--mode aclnn_py`（`OPRUNWAY_ACLNN_OPS_DIR`（ops 仓 checkout 根）/`OPRUNWAY_ACLNN_OP_SUBDIR`/`OPRUNWAY_ACLNN_VENDOR_DIR`/`OPRUNWAY_ACLNN_VENDOR_NAME`/`OPRUNWAY_ACLNN_BASE_REPO`/`OPRUNWAY_ACLNN_PR_REF`/`OPRUNWAY_ACLNN_SOC` 等见 aclnn_adapter._aclnn_cfg，且须 `OPRUNWAY_ACLNN_REAL=1` + 人工确认 build install 写**用户态 vendor 目录**（`<vendor_dir>/vendors/<vendor_name>_nn`，⚠ `_nn` 后缀由 install 自动追加）、绝不写共享 opp）。
  - **新增 form 优先规则**：`runner_form==cpp_extension` 时，上句旧 aclnn_py 描述不适用，须走 `--mode cpp_extension`，显式设置 `OPRUNWAY_CPP_EXTENSION_REAL=1` 与 `OPRUNWAY_CPP_EXTENSION_DRIVER_JSON`；driver argv 只进本地机器 profile，不写 tracked 文件。
  - **PR 身份钉死**：build receipt 和最终报告必须记录本轮远端 PR ref 解出的**精确 head SHA**。本地工作树里
    即使存在该 head 的后继修复提交，也只能作为诊断线索；未经用户把被测版本改为该提交，禁止用后继 build
    替换指定 PR 的失败证据，更不能把后继 PASS 写成原 PR PASS。
  - **源码身份前置门（build 前硬门）**：期望 SHA 只取当前 `source_facts.json` / `pr_facts.json`
    绑定的 40 位 `head_sha`。先取得精确对象，再做 detached checkout，随后以 `git rev-parse HEAD`
    逐字核对期望 SHA；状态只允许
    `SOURCE_ACQUIRED → HEAD_VERIFIED → BUILD_VERIFIED → WORKFLOW_STARTED` 顺序前进。精确 SHA
    不可直接取得时，只能使用本轮 PR 元数据确定的 PR-head ref 或 head repo，且最终仍只认 SHA 等值；
    候选须在执行前有限列明，不得失败后动态试探。禁止默认分支、base head、可移动分支或后继提交兜底。
  - **shell fail-fast 与首失败终止**：所有 CP-D shell 入口必须具备等价于
    `set -Eeuo pipefail` 的语义，并记录首个失败阶段、退出码和日志。源码对象未取得或 HEAD 未验证时
    `build_started=false`；build 未验证时 `workflow_started=false`。任一前置失败立即产 blocked receipt，
    禁止继续 build、driver 或 `run_workflow`；失败目录和制品不得被下一轮复用。
  - **构建入口按实际调用形态核验**：若构建 argv 是 `bash build.sh ...`，前置门只要求
    `build.sh` 是可读普通文件，不得额外要求 executable bit；只有 argv 直接执行 `./build.sh ...`
    时才校 `-x`。前置门必须与最终 argv 使用同一调用形态，不能用更强但无关的权限假设把合法源码
    挡在 build 启动前。
  - **冻结包入口完整性**：远端执行入口脚本本身必须与 plugin、spec、golden、facts 一起进入冻结包，
    并由同一 manifest 绑定摘要。准备阶段必须实际解包到空目录，先校 manifest，再对最终将执行的相对
    路径做 `test -f`、`test -r` 与 `bash -n`；只校 payload 而漏掉入口脚本不得宣称快照可执行。
    归档成员集合还必须严格等于“manifest 覆盖文件 + manifest 自身”的 allowlist；出现 `._*`、
    `.DS_Store`、`__pycache__`、`.pyc` 或其它未登记成员即准备失败。macOS 打包须禁用 AppleDouble
    元数据（如 `COPYFILE_DISABLE=1`）并以解包后的成员集合复核，不能只靠源目录 exclude。
  - **收件后再清理（事务硬门）**：远端结果必须先复制到本地临时名；本地逐字核对远端记录的 size/SHA，
    运行 `gzip -t`/归档完整性检查，解包到新目录并确认核心 JSON 可解析、摘要可复核后，才允许原子改名
    为正式报告目录并删除远端执行根/中转包。清理包装须全程 fail-fast，禁止用 `tar | grep -q` 这类在
    `pipefail` 下会因早关管道制造假失败的检查；任一收件验证失败必须保留远端原件等待恢复。
  - **测试快照依赖闭包**：定向回归打包不能只列被测 `test_*.py`；须静态追踪其 import、`setUpModule`
    fixture、samples/assets 相对路径，以及测试/代码按路径读取摘要的 producer logic 文件，并纳入同一
    manifest。依赖闭包无法可靠证明时直接打包完整相关组件，不能反复猜最小集。测试框架在 0 tests
    或统一 setUp 阶段因缺文件失败，只能算快照缺件，不能算代码回归通过或失败。
  - **caseset 文件闭包**：重测或复跑冻结包必须从 `caseset.json` 逐项枚举
    `.cases[].inputs[].path`、`.expected.golden_path` 与 `.expected.outputs[].golden_path`，
    按实际引用路径构造 allowlist 并逐文件校摘要；不得按 `input_*.npy`、`golden_*.npy` 等文件名模式
    猜测闭包。引用缺失、越界、符号链接或归档成员不等于 allowlist 均在准备门 fail-closed。
  - **cpp_extension 性能次序**：先完成全量 Extension 精度 readback，用 validator 同源规则筛出精度通过且来自同一 caseset 的性能 case；再显式给 `OPRUNWAY_CPP_EXTENSION_DEVICE`，复用第一阶段内容寻址 ELF/vendor receipt，custom 与任务书 baseline 双侧统一走 `msprof --ai-core=off + ctypes MSTX + CSV` 的 kernel-only 采集。性能 collect 必须完整覆盖计划 case 序列并回绑同一 Extension provenance；partial/stale/换 ELF 一律拒。
  ⚠ **`aclnn_py` 的 perf 通路：代码已接通、真机也跑过一次，但一个耗时数都没产出（仍 BLOCKED）**（2026-07-24 两次更正——① 此前本节写「采集端尚未接入 / `parse_torch_npu_baseline` 仅 schema 占位 / Task3 必须 pending」已被落地的 perf 代码推翻；② 随后写的「一次真机都没跑过」也已被 median 首跑推翻：跑是跑了、**结果是 BLOCKED**。勿再照任一旧文办事）。现状：
    - **已落地**：`aclnn_runtime/perf_msprof.py` 做 msprof kernel-only 采集（`--task-time/--ascendcl/--msproftx`，**MSTX range 圈测量窗、缺 MSTX 证据即 fail-closed**；只累加 device 计算 kernel，MEMCPY_ASYNC 不计入；warmup 5 / repeat 20 取中位数）；基线 = **同机 `torch_npu` 跑同一份 torch reference**，行为五分类（`npu`/`cpu_fallback`/`hybrid_host_device`/`execution_failed`/`no_device_kernel_observed`）**只有 `npu` 才计时**；`repo_adapter.parse_torch_npu_baseline` 已从占位改成**真消费口**（scope / us / 重复 case_id 全 fail-closed，非 npu 行为进 `excluded`）；**精度先筛**（只测已过精度的 case，其余记 `skipped_accuracy_failed`）；双边 `timing_scope` 校验 + speedup 由 `perf_compare` 出（源无关、判定逻辑一行未改）。
    - **最新状态（2026-07-26）**：用户已确认 Median 任务书里的 `aclnnMedian` / `aclnnMedianDim` 小算子拼接版本等价于 Torch 对应接口，故 spec 基线为同机 `torch_npu:torch.median`，无需再证明等价、也不改为直调单个 ACLNN。已有 custom 50/50、baseline 48/50 有效数据；2 个 BF16 case 基线失败，性能整体仍 BLOCKED。
    - **执行口径**：有 spec 指定来源的有效真实基线、且双边 scope 同为 `kernel_only` 时，才引用 `perf_report.json` 裁决；无有效基线 / provenance 缺失 / 缺 MSTX / scope 不可比 → BLOCKED，绝不自己算比值。功能/精度 oracle 与性能 baseline 分开解释。
    - **最短证据链**：任务书已明确或用户已确认实际对照语义时，直接按该事实配置 baseline，不另造证明层。性能 case 通用地从精度 caseset 选择；A3 按全部输入物理载荷之和 `<=256 KiB` 为小 shape、其余为大 shape，分类不免测。Median 的 `target_ratio=1.0` 仍逐字来自“不劣化”，非参考仓默认 0.6。
- **run_workflow 内部一次成**（不是 orchestrator 分三段调度）：Task2 真 NPU 精度 vs numpy golden（`validator.py`）+ Task3 msprof 真 kernel-only 性能 vs 基线（`perf_compare.py`）+ **末尾统一校三级门**（`validate_acceptance_state` task1/task2/task3，读落盘 evidence 独立复核：防跑子集报 100%、防放宽阈值、防混 e2e 墙钟）。**验收门 `validate_acceptance_state.py` STATUS: FAILED → 不出 pass 裁决；仍由 `run_workflow` 写 `acceptance.json.overall="BLOCKED(验收门未过)"`（exit 1）**（验收门未过=证据不可信/不完整；见 §5）。
- **产出**：`evidence.json` / `verdict.json` / `baseline.json`（仅有基线时）/ `perf_report.json` / `acceptance.json`。
- **路由**：任何 FAIL → **dispatch** `acc-verify-rootcause`，`dispatch_mode = rootcause`：先「被测物自 build + 声明支持的 dtype + 手算 golden」**独立复现，解耦『被测算子 vs 我的 harness』再归因**——技术判定与官方口径分开、不外发、不臆断、不来回改口（Equal 血教训）。Task3 缺外部 GPU 标杆 / 口径不可比 → 走 §6 的 BLOCKED 路由，不出 pass。
  - 多输出 index 场景先读 evidence 的结构化 metrics：`index_value_consistency` 已允许 tie 时不同合法位置；
    `invalid_index_count>0` 表示 DUT 给出负数/越界下标，不得再以“重复中位数、设备可选不同位置”为由放宽。
    反之仅下标不同且 gather 后值一致，才是该语义判据允许的合法 tie。

### CP-E 报告（primary）

**目的**：把确定性产物裁决翻成中文验收报告，一个字不自己判。

- **primary 亲自**：**逐字引用** `acceptance.json`（门控后总体裁决）/ `verdict.json`（validator 精度裁决）/ `perf_report.json`（perf_compare 性能）的裁决**并标来源**，加 `spec.task_pr_gaps`（任务书↔PR 落差）+ 各维度（功能 / 精度 / 性能）通过数、失败用例+判据、性能达标比。
  **另列「任务书待确认项」**：逐字引用 `taskdoc_validation_receipt.json` 的 `pending_items`（未阻断但未说明的条款）
  与 `decided_items`（用户补充或豁免的项及其理由）。这两类与 `task_pr_gaps` 分开呈现——前者是**任务书自身**的缺口，
  后者是**任务书↔PR**的落差，混在一起会让读者以为缺口出在 PR。
- **固定汇总视图**：精度逐字展示 `verdict.json.accuracy_summary.report.by_dtype/overall` 的 `total/passed/failed/needs_review`（`na` 单列）；性能逐字展示 `perf_report.json.by_shape_class/shape_overall` 的 `planned_cases/cases_scored/达标/blocked/npu_us/baseline_us/speedup`。这些字段由确定性脚本生成并由三级门做完整性对账，primary 不自行重算。
- **失败明细解耦**：存在性能未通过 case 时，必须生成独立 `性能失败明细.md`，主报告只放汇总和链接。明细逐项展示 `caseset.json` 的输入/shape/dtype/属性/调用接口，以及 `perf_report.json` 的 outcome、双边 behavior/scope/us、speedup、`target_ratio` 和原始 reason；同时给单 case 性能重放入口。runner 尚无该能力时如实标缺口，不用 JSON 查询冒充复现。
- **测量真实性红线**：所有性能 case 都须真实采集并按同口径比较，不允许按 numel 自动免测；必须同时报告 `cases_scored` 和有效 `us/speedup` 条数。`cases_scored=0` 时无论 `达标` 计数为何，统一明确“未产出任何可评分性能数据，性能未验证”。性能计划数须写成 `<dims 含性能的 case>/<caseset 总数>`，功能/精度-only case 不冒充性能覆盖。
- **红线**：数字全引真实产物，推断项标 `(推断)`；`needs_review` **不当 pass**；**验收门 `validate_acceptance_state.py` STATUS: FAILED → 不出 pass 裁决；报告如实呈现 `acceptance.json.overall="BLOCKED(验收门未过)"`（exit 1）**（验收门未过=证据不可信/不完整）；只认任务书为验收权威，「PR 有测试」≠「验收过了」。

### CP-F 验收后人工精度复核与重测（append-only）

**触发条件**：CP-E 已完成，人工针对首次精度失败或 `needs_review` case 明确发起重测。CP-F 不是
首次 CP-A..E 的内部 stage，也不覆盖首次 `spec/caseset/evidence/verdict/perf_report/acceptance`。

- **F0/F1 directive**：primary 把人工原文整理成结构化 directive，展示 case、动作和精度标准；
  人工确认后才能进入准备。含糊指令、未知 policy 或越权 override 一律停。
- **F2 准备**：幕后调用 `cp_f_prepare_attempt.py`；复核首次五类产物 hash、PR/build/runner 身份，
  冻结原 case 与实际 input bytes，产 `reports/<op>/attempts/<NNNN>/`。这里只产
  `preparation.json.acceptance_verdict=null`，不产重测裁决。
  冻结包还必须包含 golden 授权链实际引用的任务书快照/来源文件；只有
  `golden.py` 和 `.npy` 而缺少 contract 引用的 snapshot，只能得到
  `blocked_golden_unauthorized`，不得说明新精度标准已对最终裁决生效。
  同一 `directive_id` 在报告 scope 内受 allocation lock 幂等映射：内容全等返回原 prepared attempt，
  异内容或半写现场拒绝；基础路径以 realpath/commonpath 校验并拒绝任一符号链接路径段。
- **F3/F4 执行与裁决**：幕后调用 `cp_f_execute_attempt.py`；只跑 manifest 指定的原 case，
  不调用 `gen_cases`、性能 collector 或 `perf_compare`。判定仍唯一归
  `validator.py` + `validate_acceptance_state.gate_task2`。`same_policy_rerun` 使用原 spec；
  `relaxed_rerun` 使用绑定原 spec + directive 的完整 `spec.relaxed.json`，只允许精度 acceptance
  容差白名单字段变化。执行顺序必须先对未改写的原 case 校验
  `case_digest` 及 input/golden bytes，通过后才派生重绑 policy 的 effective caseset；
  不得用重绑后的 case 反向对比原 digest，也不得为避免该冲突而忽略 policy 字段。
  进入 adapter 前须显式探测并绑定 `CANN_VERSION`、`ASCEND_TOOLKIT_VERSION`、
  `OPRUNWAY_SOC`，三者均非空且与 spec/基础 receipt/实际 driver runtime 一致；少任一项属
  执行封装的 provenance 不完整，必须在 NPU invoke 前 BLOCKED，不归因 CP-F 裁决。
- **F5 报告**：attempt 内落 `retest_acceptance.json`、`attempt.receipt.json` 和
  `精度重测报告.md`。receipt 只表示执行已结束，固定 `acceptance_verdict=null`，不代表 PASS。
  relaxed 结果固定 `requires_human_cp=true`，由人工追加 disposition。
  execute 使用独占 owner lock；失败保留无最终 receipt 的诊断现场，报告成功生成并校验后才最后原子提交
  `attempt.receipt.json`，已有最终 receipt 的 attempt 永不可重跑。
  `cp_f_execute_attempt.py` 必须由编排层显式传 `--attempts-root` 可信锚；入口先拒绝 symlink、非直接四位
  子目录、已有 receipt/lock，再读取 manifest 对账，禁止从未验证 manifest 自举自己的可信根。
  allocation/execute lock 都以“唯一临时普通文件 fsync → hard-link O_EXCL 发布”一次性公开完整 owner JSON，
  崩溃只能留下无锁或完整锁。
  崩溃遗留锁不得按 mtime 自动删除：只允许专用恢复入口核验受控 attempts containment、owner
  pid/operation/digest、无 final receipt 后，将原锁原子改名为 `abandoned` 留证再释放。
  跨 precision family 是人工明确的完整 policy replacement，不称作简单“放宽容差”；必须显式 standard
  与目标 family 全部数值字段。
  对 workflow 见证要分开两个结论：必需产物、final receipt 和 cleanup 齐全只证明
  **机械闭环完成**；只有 golden 授权通过、relaxed spec/caseset/evidence 一致且
  validator 实际使用新 acceptance 口径时，才可进一步声明 **新标准裁决生效已验证**。
- **权威边界**：基础 `acceptance.json` 始终是首次验收权威；attempt 是追加证据。
  `perf_source=inherited_from_base`、`performance_retested=false`，精度重测不得改变首次性能
  PASS/FAIL/BLOCKED。
- **当前执行边界**：`cpp` / `aclnn_py` 按 form 派生原 runner mode；`cpp_extension` 复用正式
  codegen/adapter/driver 做 fresh Extension build/load/invoke，但走独立 Task-2-only 入口，物理上不生成或执行
  perf plan/collector。F2 冻结首次 invocation plan 与 build/load/vendor receipt；F3 要求 fresh invocation
  逐行全等、PR head/实际 vendor ELF/SoC/toolkit 与基础身份全等，任一漂移 fail-closed。Extension ELF 可 fresh
  build，但必须由本轮完整 receipt 绑定。`replay_only` 只保留契约枚举，当前执行入口不接线，不得用重判冒充重测。

---

## 4. `correspondence.json` schema + 状态枚举

CP-A 落盘的对应校验工件（断点续跑读它）。最小 schema：

```json
{
  "op": "<算子 snake 名>",
  "task_doc": "<任务书路径或链接>",
  "pr_url": "<PR 链接>",
  "source_facts_digest": "<当前 source_facts.json envelope 的 64 位 digest>",
  "confirmed_constraints": [
    {"key": "<受控键，如 dtype_required>", "value": "<用户确认值>", "source": "user"}
  ],
  "status": "confirmed | mismatch | empty_task | needs_user_confirmation",
  "evidence": {
    "target_dir_match": "<pr_facts.target_dir 与任务书声明目录是否对上：机器可比>",
    "issue_ref": "<NL 从 task_doc / PR title 读到的 issue/追踪号，或 null>",
    "user_confirmed": true
  },
  "conclusion": "<程序结论文本（非 pass/fail），供 mismatch/empty_task 停跑时呈现>"
}
```

**状态枚举 `status`（哪些自动停 / 哪些问用户，务必分清）**：

| status | 含义 | primary 动作 |
|---|---|---|
| `confirmed` | 任务书↔PR 对应成立 | 进 CP-B |
| `mismatch` | 目录 / issue 号对不上（任务书↔PR 配错） | 出**程序结论（非 pass/fail）**、**自动停跑** |
| `empty_task` | PR 无对应验收内容（未验收空任务） | 出**程序结论（非 pass/fail）**、**自动停跑** |
| `needs_user_confirmation` | 证据不足以自动判 | primary **摆证据、由用户拍板**，**不自动 judge 空任务** |

> 对应校验靠三条合断：`pr_facts.target_dir`（机器可比）+ issue/追踪号（NL 读 `task_doc`/PR title，非算子名字面匹配）+ 用户确认。`fetch_source` **不抽 issue 号**，issue 号只能 NL 读。

---

## 5. 三级门与 BLOCKED 路由（在 `run_workflow.py` 内部）

- **门在哪跑**：`run_workflow.py` 串完 Task1→2→3 后，内部按 `gate_stages`（`task1`、`task2`，若有性能用例或 `spec.perf.baseline` 再加 `task3`）统一调 `validate_acceptance_state._GATES[st]` 读**落盘产物**独立复核 → 打 `STATUS: PASSED|FAILED`。**批量驱动、非阶段间实时阻断。**
- **门管什么**：只管「证据可信 + 完整」（全覆盖防跑子集、阈值三处一致防放宽、scope=kernel_only 防混 e2e）。**精度/性能 pass-fail 不由门判**——那是 `validator.py` / `perf_compare.py` 的活，门不重判（合法的精度 fail 不该被门当 BLOCKED）。
- **验收门 `validate_acceptance_state.py` STATUS: FAILED → BLOCKED**：**不出 pass 裁决；仍由 `run_workflow` 写 `acceptance.json.overall="BLOCKED(验收门未过)"`（exit 1）**（验收门未过=证据不可信/不完整），一票否决。primary/CP-E 如实呈现 BLOCKED，不美化成 pass。
- **本 skill 只调这三级门、不重实现判定**：编排层不复刻门逻辑、不复刻 validator/perf_compare，只读它们落盘的裁决。

---

## 6. Task3 基线来源与 blocked 路由

- **基线来源按任务书参考源**（`spec.perf.baseline` 驱动；canon perf-baseline-by-reference-source·proposed·未 settle，载重前需核）：
  - **重写类** → `tbe`（无劣化 / `target_ratio` 按任务书；当前接入的 aclnn 重写类 isclose/sign/equal/neg 均 `perf.baseline=tbe`，catlass matmul 属对标类·synthetic demo·未定基线——「均」勿外推为全局，见 `samples/specs/`）；
  - **移植类** → GPU（如 A100，比例区间）；
  - **加 dtype 类** → 同 op 不劣化；
  - **框架级 Torch 或已确认等价于 Torch 接口的小算子拼接 baseline**（`perf.baseline=="torch_npu"`）→ 同机 `torch_npu` kernel-only；
  - **实际要求直接 ACLNN baseline**（`perf.baseline=="aclnn_builtin"`）→ 按 spec `aclnn_baseline.variants` 从 CANN `libopapi.so` 直接调用。两者均须来自任务书事实或用户确认，不凭 API 名猜。
  基线口径以 `spec.perf.baseline` 为准，不写死。
- **Task3 blocked 状态路由**（task3-state-machine）：
  - `BLOCKED_WAIT_GPU_BENCHMARK`：任务书要求 GPU 基线但**缺外部 GPU 标杆数据** → BLOCKED、不出 pass；
  - `BLOCKED_INCOMPARABLE_TIMING_SCOPE`：计时**口径不可比**（如一边 kernel-only 一边含 H2D/D2H 墙钟）→ BLOCKED、不出 pass。
- **GPU external 对比层：consumer 侧已接入 pipeline，缺的是真实数据**。`run_workflow --gpu-baseline <json>` → `gpu_baseline.parse_gpu_baseline`（按字段契约严格校验 + `case_id` 与完整输入签名交叉核对）→ `perf_compare` 出 NPU↔GPU 对比。**真实 GPU 标杆数据仍待外部方提供**；未给数据（或标杆被判废）时，移植类算子一律走 `BLOCKED_WAIT_GPU_BENCHMARK`（正规挂起、非 fail）。本 skill 只写路由文本、不产数据。
