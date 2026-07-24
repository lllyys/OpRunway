---
name: acceptance-workflow
description: OpRunway 算子验收编排的 CP-A..E 检查点状态机——定义薄 primary orchestrator 如何调度 3 个 subagent、串工件门禁、把 pass/fail 判定唯一交给确定性脚本链（validator/perf_compare/三级验收门），供 op-acceptance primary 首响应加载。
---

# acceptance-workflow — CP-A..E 验收编排状态机（Layer 2 workflow skill）

本 skill 是 `op-acceptance`（`mode:primary` orchestrator）的**唯一状态机脑子**：它规定验收怎么分段（CP-A..E）、每段派哪个 subagent、产哪个工件、哪级门在哪跑、失败怎么路由。**primary 首响应即加载本 skill，禁裸调 subagent。**

设 `${CLAUDE_PLUGIN_ROOT}` = 本插件根（含 `acc-common/`、`skills/`、`agents/`）。全程中文。产物落用户 CWD 的 `reports/<op>/`。

**本 skill 只调三级验收门、不重实现判定**——判定脑子在确定性脚本链里（见 §0），编排层只搬工件、串流程、引用裁决。

---

## 0. 铁律（贯穿全流程，每段都受约束）

1. **判定唯一归确定性脚本链**：`validator.py`（精度）+ `perf_compare.py`（性能）+ `validate_acceptance_state.py`（三级完整性门）→ 门控后由 `run_workflow.py` 写 `acceptance.json`。**编排层（primary）与 subagent 都不自行判 pass/fail，只逐字引用确定性产物的裁决并标来源**（ADR 0007）——这是「不得自行判定、只能引用」，**不是「绝不提 pass/fail」**：可以复述脚本判出的 pass/fail，但不能自己判。

2. **primary 边界**：primary **可直接跑「无 NL 生成、无判定」的确定性脚本**——`fetch_source.py`（取材）、`gen_cases.py --dry-run`（契约自检）、`validate_acceptance_state.py`（复核门）、`check_manifest_sync.py`（漂移门），用 Bash 幕后跑。primary **不做 NL 生成的 durable 工件**（spec / runner 一律派 subagent），**不自行判 pass/fail**（归确定性脚本链），首响应先加载本 skill、**禁裸调 subagent**。

3. **subagent 边界**：每个 subagent **单轮、禁内部循环、禁跨阶段、不自行判定，只回结构化摘要给 orchestrator**。循环由 primary 控（如 dry-run 契约自检异常 → 再派 `refine_spec`），subagent 自己不多轮迭代。

4. **三级门在 `run_workflow.py` 内部**：`run_workflow.py` **一次性串 Task1→2→3**，末尾**统一校门**（`validate_acceptance_state` 的 task1/task2/task3 三级，读**落盘** evidence 独立复核）——是**批量驱动、非阶段间实时阻断**，**不是** orchestrator 分阶段单独调度的 stage。验收门 `validate_acceptance_state.py` STATUS: FAILED → **不出 pass 裁决；仍由 `run_workflow` 写 `acceptance.json.overall="BLOCKED(验收门未过)"`（exit 1）**（验收门未过=证据不可信/不完整）。「不推进下一 Task / 停在当前阶段」是 **agent 编排纪律**，不是脚本里的实时闸。

5. **对外单一对话入口、脚本幕后**（canon conversational-agent-sole-delivery-form·proposed·未 settle，载重前需核）：用户全程只用自然语言（给「任务书 + PR」）；`python3 …` 是 primary 的内部实现，Bash 幕后跑，**不展示脚本命令、不让用户手敲**。缺东西（任务书 / PR / NPU-VPN 开没开 / 目标机是 a3 还是 a5）用对话问。⚠ **别问「mock 还是真机」——验收只有真机一条路**。`OPRUNWAY_*`（真实机器名 / 远端路径 / token）**走环境变量、不写进仓**。**副作用先确认**（真机 clone / build / 跑测、对外动作先列计划点头再做）。

---

## 1. 状态即工件 · 断点续跑

**工件即状态**：每个 CP 的推进由「落盘工件是否存在且合法」判定，没有独立的状态文件。中断后重启，primary **先扫 `reports/<op>/` 现有工件**，从缺口处续跑，不重跑已完成段。

| 工件 | 由哪个 CP 产 | 存在即代表 | 续跑判据 |
|---|---|---|---|
| `correspondence.json` | CP-A | 对应校验已落盘（读 `status` 定去留） | `status=confirmed` 才进 CP-B；`mismatch/empty_task` 停 |
| `<op>.spec.json`（含 `task_pr_gaps`） | CP-B（`extract_spec`） | spec 已抽 | 缺 → 派 `extract_spec` |
| 用例计划账本（stdout，**不落 durable 工件**） | CP-B（primary inline `gen_cases.py --dry-run`） | 用例计划自洽（预算区间 / dtype 分布 / 特殊场景覆盖 / id 唯一 / 种子确定） | dry-run 报错或账本异常 → 派 `refine_spec` 后重跑 dry-run |
| `oprunway_<op>_runner.cpp`（自检证据满足） | CP-C（`gen_runner`→`verify_runner`） | runner 已锚定 example；由 acc-runner-dev 的 runner 自检证据满足/不满足纪律保证（当前**非代码强制 sidecar 硬门、待补**） | 自检证据不满足则停在 CP-C、不上真机 |
| `evidence.json` / `verdict.json` / `baseline.json`（仅有基线时）/ `perf_report.json` / `acceptance.json`（真机裁决） | CP-D（`run_npu`→`run_workflow --mode new_example`） | 真机一次原子跑完、门已校 | `acceptance.json.overall` 非 PASS 且非门问题 → 派 `rootcause` |
| 中文验收报告 | CP-E（primary） | 报告已出 | — |

> 多算子：一份任务书含 N 个算子 → CP-B 产 N 份 spec，每份独立走 CP-B..E，工件按 `reports/<op>/` 分目录。

---

## 2. dispatch 契约（每次派 subagent 的固定模板）

primary 每次派 subagent，都按此五段给全，**不省略**（subagent 单轮、拿不到上下文就无法完成）：

| 契约段 | 内容 |
|---|---|
| **工作区** | `reports/<op>/`（及 `work/` 子目录）绝对/相对路径；`${CLAUDE_PLUGIN_ROOT}` |
| **dispatch_mode** | 本次的模式取值（见各 CP；这是**调度模式**，与 frontmatter 的 `mode:subagent` 不同名、不混用） |
| **输入工件** | 该 mode 需读的已落盘工件（如 `task_doc.md`+`pr_facts.json` / `<op>.spec.json` / gate error 文本） |
| **验收标准** | 本轮「算干完」的判据（如 spec 自检项全过、runner 逐元素等手算 golden、run_npu 出全套工件+门已校） |
| **本次产出** | 要落盘的工件名 + 回给 orchestrator 的**结构化摘要**字段 |

**subagent 回执硬约束**：单轮完成、禁内部循环、禁跨阶段、不自行判 pass/fail，**只回结构化摘要**（产了什么工件 / 关键字段 / gaps / 是否 BLOCKED 及原因）给 orchestrator，由 primary 决定下一步。

---

## 3. CP-A..E 状态机

五个 CP 是**对话暂停点 + 工件门**，不是 run_workflow 内部的 stage。真机执行合并成**一个原子 CP-D**（Task2+Task3+三级门一次成）。

### CP-A 前置（primary 亲自，不派 subagent）

**目的**：取材 + 任务书↔PR 对应校验 + 环境/模式确认，识别并挡掉「未验收空任务 / 任务书↔PR 配错」。

- **取材**（确定性脚本，primary 直接跑）：`python3 ${CLAUDE_PLUGIN_ROOT}/acc-common/fetch_source.py --taskdoc <路径|链接> --pr <PR链接> --out <work>` → `task_doc.md` + `pr_facts.json`。
- **对应校验**（落 `correspondence.json`，schema/枚举见 §4；canon verify-spec-pr-correspondence·proposed·未 settle，载重前需核）：靠三条证据合断——
  1. **改动落点目录**：`pr_facts.target_dir`（机器可比），对上任务书声明的算子目录；
  2. **issue / 追踪号**：**NL 读** `task_doc.md` 与 PR `title`（`pr_facts` **不抽 issue 号**，只能自然语言读），**非算子名字面匹配**；
  3. **用户确认**：证据摆给用户拍板。
- **环境确认**（`AskUserQuestion` **必由 primary 做**）：NPU/VPN 开没开、**目标机按任务书 `适配硬件` × 算子 `op_def` 的 `AddConfig` 双源交叉核定**（a5 真 950 / a3 A2A3；两源不一致入 `task_pr_gaps`）。⚠ **不问 mock 还是真机**——`--mode` 默认已是 `new_example`，验收只认真机。
- **产出**：`correspondence.json`。`status=confirmed` → 进 CP-B；`mismatch`/`empty_task` → 出**程序结论（非 pass/fail）**并停跑；`needs_user_confirmation` → 摆证据、等用户拍板（**不自动 judge 空任务**——Equal #2890 配错作废血教训）。

### CP-B Task1 用例（dispatch + primary inline）

**目的**：任务书→spec + golden，并用 `--dry-run` 做**用例计划的契约自检**（不产任何裁决）。

- **dispatch** `acc-spec-extractor`，`dispatch_mode = extract_spec`：读 `task_doc.md`+`pr_facts.json` → `<op>.spec.json` + `task_pr_gaps`（缺项落 gaps 不臆造；多算子多 spec）。
- **dispatch** `acc-runner-dev`，`dispatch_mode = gen_golden`：读 `task_doc.md`+`spec` → 任务书快照入库 + `<ops_root>/<op>/golden.py`（真值口径走 **R3 两档链**；**PR/仓内参考实现禁作 golden 源**；后端生成期定死）→ 自跑 `check_golden.py <Op>` 出档位账本。**必须在 dry-run 之前**——`gen_cases` 缺 golden.py 即 fail-closed。
  路由**按退出码、不按档位数字**：**0**（可走）→ 进 dry-run；**2**（`needs_human_review`——tier 3 必然如此，⚠ **tier 1 也可能**：`multistep + oracle_method` 判 `(tier 1, 需人核)`）→ 进 dry-run 但**报告里显式标「golden 需人核」**；**1**（blocked / 词表不合规 / 缺件 / 账本自相矛盾 / 参数错误）→ **停在 CP-B**，把 `blocked_reason` 摆给用户，**不自动回落第二档**（R4）。
- **primary inline**（确定性脚本，无 NL 生成）：`python3 ${CLAUDE_PLUGIN_ROOT}/acc-common/gen_cases.py <spec> --dry-run`。plan-only，查这些：用例预算落不落 `[S=强制下限, pool_max]` 区间 · dtype 分布 · 特殊场景（empty/scalar/边界/inf/ninf/nan）覆盖 · 被丢组合类 · `case_id` 唯一（撞则 raise） · per-case 种子确定性。
  ⚠ **能力边界（别当成旧 mock 自检的等价物）**：dry-run **不调 `golden_fn`、不落 `.npy`、不产任何裁决**；但它**会加载执行 `golden.py`**（取 `out_shape` 造规模预算）——所以对 golden 的覆盖是**半道**的：**缺文件 → 只记「未核」、不阻塞**；**文件在但坏了（语法错 / 顶层抛 / 必需导出不全）→ 当场抛、拦得住**。仍**验不了**：来源契约合不合规（那是 `check_golden.py` 的活）/ `oracle_source` 映射 / `validator` 判定链 / 三级门 / evidence 结构——**这些只有 CP-D 真机跑测才验得到**。（照本仓约定 golden.py 把 torch 延迟 import，故 dry-run 通常不拉 torch；某算子若在模块顶层 `import torch`，它会跟着 import。）CP-B 过了**不代表**用例链整体可用。
- **产出**：`<op>.spec.json` + `<ops_root>/<op>/golden.py` + `<ops_root>/<op>/task_doc.snapshot.md`（三件均 subagent 产）+ dry-run 计划账本（stdout，**不落 durable 工件、不产裁决**）。`caseset.json` 由 CP-D 真机跑测时才落盘。
- **路由**：dry-run 报错或账本异常（如预算区间不合理、重点 dtype 未覆盖、特殊场景缺失、id 撞）→ **dispatch** `acc-spec-extractor`，`dispatch_mode = refine_spec`（据报错文本修 spec）→ 重跑 dry-run。**契约自检没过先修 spec，别上真机。**
  ⚠ **`golden.py` 缺文件这一种 dry-run 查不出**（只记「未核」照常出计划），会一路漏到 CP-D 才炸；且 `refine_spec`（改 spec）**变不出 `golden.py`**——**golden 侧的问题一律回 `acc-runner-dev:gen_golden`，不在 refine 循环里空转**。

### CP-C runner（真机路径、需 NPU；dispatch）

**目的**：为算子生成锚定 example 的 per-op runner，并「验证-才-信」后才允许上真机。

- **前置**：先确认用户已开 NPU/VPN（CP-A 已问）。
- **dispatch** `acc-runner-dev`，`dispatch_mode = gen_runner`：**先过 scope gate**——ops-<族> 仓·aclnn 两段式·opp 安装型（含非 experimental 子树）；catlass/非 aclnn 接口/双实现/未支持 dtype → 返回 `BLOCKED` / 转 P3，**不硬塞**。过 gate 后据 `spec` + `pr_facts.key_files` 的 `test_aclnn_*.cpp` **锚定 example 不猜**，生成 `oprunway_<op>_runner.cpp` + 选构建路径。
- **dispatch** `acc-runner-dev`，`dispatch_mode = verify_runner`：造手算 golden 小用例、逐元素比，形成 runner 自检证据（满足/不满足）。
- **产出**：自检证据满足的 `oprunway_<op>_runner.cpp` + 构建路径配置。
- **路由**：**runner 自检证据不满足 → 停在 CP-C、不上真机**（acc-runner-dev 的 runner 自检证据满足/不满足纪律，当前**非代码强制 sidecar 硬门、待补**；acceptance 裁决只逐字引用 `validator.py` / `perf_compare.py` / `validate_acceptance_state.py` 产物，ADR 0007）；scope gate BLOCKED → 停在 CP-C，出程序结论（转 P3 / 需扩 adapter），不进 CP-D。

### CP-D 真机跑测（一次原子；dispatch）

**目的**：一次原子跑完 Task2 精度 + Task3 性能 + 三级门，落全套裁决工件。

- **dispatch** `acc-verify-rootcause`，`dispatch_mode = run_npu`：`python3 ${CLAUDE_PLUGIN_ROOT}/acc-common/run_workflow.py <spec> --mode new_example --out reports/<op>/`（`OPRUNWAY_*` 指真实机器/路径，不写进仓）。
- **run_workflow 内部一次成**（不是 orchestrator 分三段调度）：Task2 真 NPU 精度 vs numpy golden（`validator.py`）+ Task3 msprof 真 kernel-only 性能 vs 基线（`perf_compare.py`）+ **末尾统一校三级门**（`validate_acceptance_state` task1/task2/task3，读落盘 evidence 独立复核：防跑子集报 100%、防放宽阈值、防混 e2e 墙钟）。**验收门 `validate_acceptance_state.py` STATUS: FAILED → 不出 pass 裁决；仍由 `run_workflow` 写 `acceptance.json.overall="BLOCKED(验收门未过)"`（exit 1）**（验收门未过=证据不可信/不完整；见 §5）。
- **产出**：`evidence.json` / `verdict.json` / `baseline.json`（仅有基线时）/ `perf_report.json` / `acceptance.json`。
- **路由**：任何 FAIL → **dispatch** `acc-verify-rootcause`，`dispatch_mode = rootcause`：先「被测物自 build + 声明支持的 dtype + 手算 golden」**独立复现，解耦『被测算子 vs 我的 harness』再归因**——技术判定与官方口径分开、不外发、不臆断、不来回改口（Equal 血教训）。Task3 缺外部 GPU 标杆 / 口径不可比 → 走 §6 的 BLOCKED 路由，不出 pass。

### CP-E 报告（primary）

**目的**：把确定性产物裁决翻成中文验收报告，一个字不自己判。

- **primary 亲自**：**逐字引用** `acceptance.json`（门控后总体裁决）/ `verdict.json`（validator 精度裁决）/ `perf_report.json`（perf_compare 性能）的裁决**并标来源**，加 `spec.task_pr_gaps`（任务书↔PR 落差）+ 各维度（功能 / 精度 / 性能）通过数、失败用例+判据、性能达标比。
- **红线**：数字全引真实产物，推断项标 `(推断)`；`needs_review` **不当 pass**；**验收门 `validate_acceptance_state.py` STATUS: FAILED → 不出 pass 裁决；报告如实呈现 `acceptance.json.overall="BLOCKED(验收门未过)"`（exit 1）**（验收门未过=证据不可信/不完整）；只认任务书为验收权威，「PR 有测试」≠「验收过了」。

---

## 4. `correspondence.json` schema + 状态枚举

CP-A 落盘的对应校验工件（断点续跑读它）。最小 schema：

```json
{
  "op": "<算子 snake 名>",
  "task_doc": "<任务书路径或链接>",
  "pr_url": "<PR 链接>",
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
  - **加 dtype 类** → 同 op 不劣化。
  基线口径以 `spec.perf.baseline` 为准，不写死。
- **Task3 blocked 状态路由**（task3-state-machine）：
  - `BLOCKED_WAIT_GPU_BENCHMARK`：任务书要求 GPU 基线但**缺外部 GPU 标杆数据** → BLOCKED、不出 pass；
  - `BLOCKED_INCOMPARABLE_TIMING_SCOPE`：计时**口径不可比**（如一边 kernel-only 一边含 H2D/D2H 墙钟）→ BLOCKED、不出 pass。
- **GPU external 对比层：consumer 侧已接入 pipeline，缺的是真实数据**。`run_workflow --gpu-baseline <json>` → `gpu_baseline.parse_gpu_baseline`（按字段契约严格校验 + `case_id` 与完整输入签名交叉核对）→ `perf_compare` 出 NPU↔GPU 对比。**真实 GPU 标杆数据仍待外部方提供**；未给数据（或标杆被判废）时，移植类算子一律走 `BLOCKED_WAIT_GPU_BENCHMARK`（正规挂起、非 fail）。本 skill 只写路由文本、不产数据。
