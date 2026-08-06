---
name: op-acceptance
description: OpRunway NPU 算子验收编排。输入=算子任务书(md 本地路径或链接)+PR 链接 → 派 subagent 产 spec/runner/跑测，primary 逐字引用 acceptance.json 等确定性产物裁决、不自行判定、不产 NL durable 工件；出中文验收报告。当用户要验收一个 NPU 算子、或给「任务书+PR」要验收结论时用。
mode: primary
skills:
  - acc-casegen
  - acc-perf
  - acc-precision
  - acc-rootcause
  - acc-runner
  - acc-spec
  - acceptance-workflow
agents:
  - op-acceptance
  - acc-spec-extractor
  - acc-runner-dev
  - acc-verify-rootcause
---

# OpRunway 算子验收 — 跨 CLI 编排清单（AGENTS.md）

> 本文件是 OpRunway 验收体系的 **plugin 级注册清单**，并**拟**作为跨 CLI 单一事实源（后者属 proposed·未 settle，
> 见文末「跨 CLI 单一源」）：Claude Code 按约定目录自动发现 `agents/*.md`（**不读本文件**），**Codex 等读本文件**
> （`AGENTS.md` 是 Codex 原生约定，plugin 根搭车）。编排 / 依赖 / 硬门以此为准。
> **脚本是内部实现——用户全程只对话、不碰脚本、不被要求手敲命令**（proposed·未 settle，载重前需核）。

**输入**：算子任务书（md 本地路径 **或** `http(s)` 链接）+ PR 链接。
**产出**（**验收裁决当前只出自 `--mode cpp_extension`**，见下节与仓根 `AGENTS.md` §4）：`reports/<op>/` 下 `correspondence.json` / `caseset.json` / `evidence.json` / `verdict.json` / `baseline.json`（有基线时）/ `perf_report.json` / `acceptance.json` + 中文验收报告；`cpp_extension` 另产与裁决解耦的 `repro/` 全量人工复现入口。
⚠ **非准入通路（`cpp` / `aclnn_py`，要跑须加 `--allow-experimental-form`）与非验收通路（mock / catlass_mock）产的都是** `dev_run_summary.json` + `dev_precision_check.json`（带 `evidence_grade=development` + NON-ACCEPTANCE 戳），**物理上不产 `acceptance.json` / `verdict.json`**（mock 侧口径自 C5，2026-07-22）。

## 跑测 mode 的唯一真源：`spec.runner_form`（别写死、别问用户）

`--mode` **不问用户、不写死**，由 `spec.runner_form`（受控词表 `{cpp, aclnn_py, cpp_extension}`，**缺省 = `cpp_extension`**）**派生**。
缺省的真源是 `acc-common/repo_adapter.py` 的 `DEFAULT_RUNNER_FORM`（`run_workflow._DEFAULT_RUNNER_FORM` 只是它的别名，不是第二处口径）：

| `spec.runner_form` | `run_workflow.py --mode` | 能否产验收裁决 | 说明 |
|---|---|---|---|
| `cpp_extension`（**或未声明**） | `cpp_extension` | ✅ **当前唯一准入形态** | 隔离构建官方 PyTorch `NpuExtension`，以 build/load/vendor receipt 绑定被测来源锚（PR 通路是 exact PR head，本地 checkout 通路是子树 `root_digest`）与现场 ELF；须 `OPRUNWAY_CPP_EXTENSION_REAL=1` |
| `cpp` | `new_example` | ❌ 要跑须加 `--allow-experimental-form`，且只产开发级产物 | 编译 per-op C++ runner 上真机；真机 dtype 白名单 fp32/fp16/bf16 |
| `aclnn_py` | `aclnn_py` | ❌ 同上 | op 工程即 DUT、通用 ctypes 两段式 runner（**无 per-op runner 源**）；须 `OPRUNWAY_ACLNN_REAL=1` |

`_REAL_MACHINE_MODES = {new_example, aclnn_py, cpp_extension}`（`acc-common/run_workflow.py`）——三者都**在真机上跑得起来**，
但**准入白名单只有一条**：`_ACCEPTANCE_RUNNER_FORMS = frozenset({"cpp_extension"})`。
⚠ **「能跑」≠「能出裁决」，这两件事必须分开读、分开写。** 准入之外，`cpp_extension` 自己的来源/构建/加载收据仍须通过三级门才产裁决。
`mock` / `catlass` / `catlass_mock` **派生不出来**，只能显式指定（局部自检 / catlass 通路的正当逃生口），且不产 `acceptance.json`。

准入门落在**两处、缺一不可**：① 入口门 `_resolve_mode`（正常调用路径在这里被拦；**只对真机通路生效**，显式 `mock` /
`catlass_mock` 逃生口不受影响——它们本来就不产验收产物）；② 出口门 `_assert_acceptance_form_allowed`（写
`acceptance.json` / `verdict.json` **之前**再校一次）。口径照抄 `repo_adapter` 对 `catlass_mock` 后门的处置——
**只拦入口拦不住**；下一个人看到出口门别当冗余删掉。

> ⚠ **`--allow-experimental-form` 放行的是「跑起来」，不是「出裁决」**：`cpp` / `aclnn_py` 加了它才能在真机上跑
> （修通路、复现问题、局部开发验证），但该路径**物理上只产** `dev_run_summary.json` / `dev_precision_check.json`
> （`evidence_grade="development"` + NON-ACCEPTANCE 标记），**不写** `acceptance.json` / `verdict.json`。
> 所以「加了逃生阀跑绿了」**不得**写成验收通过、**不得**进验收报告的裁决栏。
>
> **spec 写着 `cpp` / `aclnn_py` 又要做正式验收时，正确处置是把 spec 迁到 `cpp_extension`——不是问用户走哪条 form。**
> 迁移要补 torch.ops 调用桥 + vendor ELF 构建收据，接入成本明显更高；这是已知账单，不是加个逃生阀就算解决。
> 编排层在 CP-A/CP-B 就该定下来，别按旧 form 一路派生到 CP-D 才被门拦、白做整条前置。

> **收敛理由是真机成熟度，不是形态优劣**（详见仓根 `AGENTS.md` §4.1）：
> ① `cpp_extension` 跑通过完整 torch_parity 矩阵（Median PR6429），是唯一有完整矩阵背书的通路。
>    ⚠ **引用数字前先看仓根 `AGENTS.md` §4.5**：真机结果是**并列的两个 caseset**——1152（真机当轮
>    per-run spec，未入仓）与 1344（仓内 `plugin/samples/specs/median.spec.json`，多一档 global overload），
>    **不存在单一的「Median 精度基线数字」**，引用时必须点名走的是哪一份 spec；两组 `gate.passed` 都是 `true`；
> ② `cpp` 那条路真机 dtype 白名单只有 fp32/fp16/bf16（`acc-common/repo_adapter.py` 的 `_NP`），int32 等落在
> `DEFERRED_NP_BY_FORM["cpp"]`——**生成期能造例、真机跑到 fail-closed** → 声明了 int32 的算子**覆盖实打实缺一块**；
> ③ `aclnn_py` 只有旧 caseset 的历史结果，迁到 torch_parity + `cpp_extension` 后必须重跑，旧结论不得沿用。
> ⚠ **能力表 ≠ 准入表，别互相反推**：`repo_adapter.SUPPORTED_NP_BY_FORM` / `DEFERRED_NP_BY_FORM` 里 `cpp` / `aclnn_py`
> 的条目**照旧保留**——那张表回答「这条通路支持哪些 dtype」，准入白名单回答「这条通路能不能出裁决」，两个问题。
> ⚠ **runner form 不决定性能基线**：`new_example` 那条路的默认对照物是**同法测的内置 TBE**
> （见 `acc-common/new_example/run_on_npu.sh` 头注），`aclnn_py` / `cpp_extension` 则逐字按任务书配置；
> 每份任务书的 baseline 仍须单独核实，别拿 form 反推（另见下文「编排硬约束」的 Task3 条）。

## 硬门（最高规则）

出**任何 pass 裁决前**，**必须**先过机器可校验验收门 `acc-common/validate_acceptance_state.py`
（三级 `--stage task1|task2|task3`，读**落盘** `evidence.json` 独立复核：**防跑子集报 100%、防放宽阈值、防混 e2e 墙钟**）。
验收门 `validate_acceptance_state.py` `STATUS: FAILED` → **不出 pass 裁决；仍由 run_workflow 写 `acceptance.json.overall="BLOCKED(验收门未过)"`（exit 1）**（验收门未过=证据不可信/不完整）。`run_workflow.py` 已内嵌此门（Task1→2→3 **全跑完后统一校门** →
门未过总体 `BLOCKED`；**注：批量驱动、非阶段间实时阻断**）；**「不推进下一 Task」是 agent 编排纪律**。
判定脑子在 `acc-common/validator.py`（ADR 0007）、**不在编排层**；门只管「证据可信完整」，精度/性能 pass-fail 由 `validator`/`perf_compare` 判。

## 编排（CP-A..E · 薄 orchestrator + 3 subagent 状态机）

胖 agent 已改薄为 `mode:primary` 编排器：只做**调度 + 检查点(CP)状态机 + 工件门禁 + 对应校验前置**；
NL 生成 durable 工件（spec / runner）与真机跑测 / 归因**下沉 3 个 `mode:subagent`**。CP 状态机文本承载在
`skills/acceptance-workflow/SKILL.md`（primary 首响应先加载此 skill、禁裸调 subagent）。

### 注册面 vs 调度面（先厘清语义，别混）

- **plugin_agents** = 本 `AGENTS.md` frontmatter `agents:[4]` = `op-acceptance` + 3 subagent，**含 primary 自身** →
  本项目**声明**的 agent 注册清单，**预期**与 Claude Code 自动发现的 `agents/*.md`（按 stem）集合一致。
- **plugin_skills** = 本 frontmatter `skills:[7]` → 本项目声明的 skill 注册清单，**预期**与 `skills/*/SKILL.md` 集合一致。
  ⚠ 这两处 frontmatter **不负责让 Claude Code 暴露组件**（暴露靠约定目录自动发现）；它们是同步门的一侧、供 Codex 等读。
- **child_agents** = `agents/op-acceptance.md` frontmatter `agents:[3]` = `acc-spec-extractor` / `acc-runner-dev` /
  `acc-verify-rootcause`，**不含自己** → primary **可 dispatch 的子 agent**。
- **primary_skills** = `agents/op-acceptance.md` frontmatter `skills:[1]` = `acceptance-workflow` → primary **实际加载**的
  skill（原子 skill 已下沉 subagent，`check_agent_frontmatter.py` 强制恰为此一个）。
- 分**两层**：本清单是 plugin 级**注册面**（含 primary 自身、含全部 skill）；`agents/op-acceptance.md` 里的是 primary 的
  **调度面 / 实际加载**。两层数目本就不等，别互相「对齐」。

> ⚠ **`.claude-plugin/plugin.json` 不要声明 `agents`**。在实测的 Claude Code `2.1.206` 上，写成 `["./agents/x.md"]`
> 会被**静默忽略**——插件照常加载、`claude plugin validate` 照常 ✔、8 个 skill 照常在，但 `Agents (0)`、4 个 agent 全不
> 生效，`/op-acceptance` 调不起 primary。写成 `["agents/x.md"]`（去 `./`）或 `"./agents/"`（字符串）则**整个插件加载失败**。
> 已测的四种写法里**只有省略该字段**能得到 `Agents (4)`（靠约定目录 `agents/` 自动发现）——当前唯一实测可用，不等于
> schema 上唯一合法；其它版本未验证。别「好心」把它加回来（`check_manifest_sync.py` 设了反向门）。

### 检查点（CP，对话暂停点 + 工件门；缺 NPU/VPN 到可验证的非真机准备 / aclnn CP-C0 为止）

- **CP-A 前置**（primary 亲自）：取材 `fetch_source.py` → **任务书↔PR 对应校验**（落 `correspondence.json`；proposed·未 settle，载重前需核）→
  环境确认（**执行形态：就地跑还是远程连** / NPU 通不通 / 目标机按任务书 `适配硬件` × op_def `AddConfig` 双源定），`AskUserQuestion` 由 primary 做。
  ⚠ **`.oprunway/real-machine.env` 只是「远程连」形态的连接元数据，不是开工前置**：就地跑（会话本身已在目标机或其
  NPU 容器里）时设 `OPRUNWAY_TARGET=local` 即可、`OPRUNWAY_SSH_HOST` 免填，**不得**以「缺该文件 / 拿不到 SSH alias、
  容器名、远端工作目录」为由拒绝启动验收。该文件**存在时**才必须读 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`，那些根及其
  子目录是只读保留现场；未登记 ≠ 可随意清理（仓根 `AGENTS.md` §5.3、`skills/acceptance-workflow` 的 CP-A 环境确认）。
  ⚠ **别问「mock 还是真机」、也别问跑哪个 form / mode**——验收裁决当前**只出自 `cpp_extension`**（`--mode cpp_extension`），
  跑哪条据 `spec.runner_form` 在 CP-D **派生**（见上节表，**未声明即缺省 `cpp_extension`**），不由用户选。
  ⚠ **热续跑复用既有 spec 时先看一眼 `runner_form`**：若它写着 `cpp` / `aclnn_py`，正式验收的正确处置是**在 CP-B 把 spec 迁到
  `cpp_extension`**，**不是**按旧 form 继续派生运行、也**不是**回头问用户走哪条——按旧 form 跑下去只能拿到开发级产物。
  校验靠 **改动落点目录 `pr_facts.target_dir`（机器可比）** + **issue/追踪号（NL 读 `task_doc`/PR title，非算子名字面匹配）** + **用户确认**。
  `correspondence.json` 的 `status ∈ {confirmed, mismatch, empty_task, needs_user_confirmation}`：
  `mismatch` / `empty_task` → 出**程序结论（非 pass/fail）**并停跑；`needs_user_confirmation` → primary **摆证据、由用户拍板**（不自动 judge 空任务）。
- **CP-B0 任务书输入校验门**（先于 `extract_spec`）：dispatch `acc-spec-extractor:validate_taskdoc`（**只读任务书自己**）→
  `taskdoc_validation.json`；primary inline `validate_taskdoc_input.py` 按
  `taskdoc_validation_contract.json` 的 18 项复核结构与绑定并**机械派生**阻断清单，
  `acceptance_verdict=null`。`PASSED`/`PASSED_WITH_PENDING` → 进 `extract_spec`；
  `NEEDS_USER` → primary 汇总问用户（阻断项只能补充事实或停止验收，豁免只对不阻断的待确认项开放），决策写回 `decisions` 重跑脚本；
  `BLOCKED` → 校验工件不可信，重做。决策绑 `source_facts_digest`，任务书字节一变即整体失效。
  ⚠ 本门**不随 `validate_preparation_state.py` 的 `REUSABLE` 跳过**（那份收据不绑 `taskdoc_validation*`）：
  脚本每轮都重跑，热续跑省掉的只有贵的 `validate_taskdoc` NL dispatch。
- **CP-B Task1 用例**：dispatch `acc-spec-extractor:extract_spec` → `spec` + `task_pr_gaps`；primary inline
  `gen_cases.py <spec> --dry-run --ledger-out <case_plan.json> --source-facts <source_facts.json> --correspondence <correspondence.json>`（plan-only 契约自检 + 绑定 facts/用户确认的 durable 计划账本，**不产任何裁决**）与
  `validate_preparation_state.py`（只判 CP-A/B 准备工件能否复用，`acceptance_verdict=null`）——**CP-B 只关注 task1 用例计划自洽**；
  `preflight_aclnn.py`（**仅 `aclnn_py` 形态**；⛔ 该形态非准入、产不出验收裁决，本步只服务开发级路径——只做 aclnn PR-head header↔spec slots 静态对账，成功也只是 `READY_WAIT_NPU_TRUST_GATE`，不替代真机 build/harness 门）；
  dry-run 报错或覆盖账本异常 → dispatch `refine_spec`。⚠ C5（2026-07-22）起 **mock 通路物理上不产 `acceptance.json`**，
  改产 `dev_run_summary.json`；本文件别处提到的「门控后写 acceptance.json」**只适用准入通路 `cpp_extension`（`--mode cpp_extension`）**——
  `cpp` / `aclnn_py` 即使加 `--allow-experimental-form` 跑起来，产的也同样只有 `dev_run_summary.json` / `dev_precision_check.json`。
  ⚠ **CP-B 是把 form 定死的地方**：正式验收下 `spec.runner_form` 必须是 `cpp_extension`（未声明即缺省为它，合规）。
  `extract_spec` 产出或复用的 spec 若写着 `cpp` / `aclnn_py`，**先 dispatch `refine_spec` 迁到 `cpp_extension`**
  （迁移账单：torch.ops 调用桥 + vendor ELF 构建收据），**不得**带着旧 form 进 CP-C/CP-D，**也不得**拿这件事去问用户走哪条 form。
- **CP-C runner**（真机路径、需 NPU）：**按 form 分流；准入路径是 `cpp_extension`**（缺省 form）——走下文「vendor 构建收据」那道信任门，
  由 `acc-runner-dev` 生成官方 `NpuExtension` bundle、真机 build/load/执行交显式 driver 并回传内容寻址收据。
  以下 `cpp` / `aclnn_py` 两条分流的机制描述**仍然有效**，但**只服务开发级路径**（须 `--allow-experimental-form`，**产不出验收裁决**）：
  `cpp` 才 dispatch `acc-runner-dev:gen_runner`（**先过 scope gate**）→ `verify_runner`；
  未满足则停在 CP-C、不上正式跑测；acceptance 裁决只逐字引用 `validator.py` / `perf_compare.py` / `validate_acceptance_state.py` 产物（ADR 0007）。
  ⚠ **`spec.runner_form == "aclnn_py"` 例外**（⛔ 非准入形态，本段机制只服务开发级路径）：此形态**无 per-op runner 源**（op 工程即 DUT）→ **不派 `gen_runner`、跳过 per-op `verify_runner`**；
  但**不等于免验证**——dispatch `acc-verify-rootcause:verify_aclnn_harness`，由
  `verify_aclnn_harness.py` 从完整 caseset 确定性选择小见证集（每种实际输入 dtype + 每个签名/slot 变体；本接口存在时覆盖标量 attr / 多输出），
  真机逐输出与 CPU torch golden 对拍并落内容寻址 `work/aclnn_harness_trust.json`。`run_workflow` 会在正式 adapter 前硬复核
  收据与当前 spec/完整 caseset、见证数据字节、golden 源码、PR/build/toolkit/SoC/符号及执行逻辑的绑定；未过、未留证或漂移一律停在 CP-C（详见 `skills/acceptance-workflow` CP-C）。
  ⚠ **`spec.runner_form == "cpp_extension"`（✅ 当前唯一准入形态，也是未声明时的缺省）的信任门是 vendor 构建收据**：DUT **不是** Extension 调用桥本身，而是被测来源
  构建出的那个 vendor `.so`——Extension 自己 build/load 成功，**一个字都没说被加载的 `.so` 是哪来的**。所以真机上构建 vendor 时
  必须用 `vendor_build_receipt.py` 产 `vendor_build_receipt`，**两个子命令、顺序固定**：`snapshot-digest`（**build 之前**
  取源码树整树/子树 merkle，落中间凭据）→ `emit`（**真跑** `--build-argv`，`build.returncode` 是实测值、
  记 `build.returncode_source="measured"`；`--library` 须被这次 build 改写过），**不许人手写**——手写的
  `returncode: 0` 是自报，而这份收据存在的全部意义就是机器可核。
  ⚠ 产出侧**不读 `source_facts`**：收据里的 merkle 由 `--source-root` 现算，与取材锚的对账要到三级门才做，
  所以「收据产出来了」**不等于**「源码身份已对账」。产不出来就停在 CP-C，不带着说不清来源的 ELF 上真机；产出后经
  `OPRUNWAY_CPP_EXTENSION_VENDOR_BUILD_RECEIPT` 传给 CP-D 的 driver（详见 `skills/acceptance-workflow` CP-C）。
- **CP-D 真机跑测（一次原子）**：dispatch `acc-verify-rootcause:run_npu` → `run_workflow.py --mode <mode> --source-facts <CP-A 取材目录>/source_facts.json`
  ⚠ **`--source-facts` 在验收通路上必给、缺席直接拒跑**（在 `os.makedirs` / staging / Task1 之前就拒，不留半个产物目录）：三级门要拿它与
  vendor build receipt 的来源锚逐字对账，缺对照物时「收据自称 `pull_request`、事实其实是 `local_checkout`」查不出来。
  路径就是 **CP-A `fetch_source.py --out <取材目录>` 产的那份**，由编排层随 dispatch 交给 subagent，**与 `--out reports/<op>/` 不是同一个目录**
  （`completeness.status` 须为 `complete`）。非验收通路（`mock`、加了 `--allow-experimental-form` 的 `cpp` / `aclnn_py`）**不受此强制**。
  （`<mode>` **据 `spec.runner_form` 派生**：`cpp_extension`（**或未声明**）→ `cpp_extension`（须 `OPRUNWAY_CPP_EXTENSION_REAL=1` + 过 build/load/vendor receipt 门，
  ✅ **当前唯一能产验收裁决的通路**）、`cpp` → `new_example`、`aclnn_py` → `aclnn_py`（须 `OPRUNWAY_ACLNN_REAL=1`）——
  后两条❌ **要跑须加 `--allow-experimental-form`，且只产 `dev_run_summary.json` / `dev_precision_check.json`，不写 `acceptance.json` / `verdict.json`**；
  `mock` / `catlass` / `catlass_mock` 派生不出、须显式指定，见上文「跑测 mode 的唯一真源」）
  （**Task2 精度 + Task3 性能 + 三级门 task1/2/3 一次成**）→ `evidence.json`/`verdict.json`/`baseline.json`（有基线时）/`perf_report.json`/`acceptance.json`；
  FAIL → dispatch `rootcause`（先解耦「被测算子 vs harness」再归因）。
  ⚠ **正式验收不得按 `cpp` / `aclnn_py` 直接派生运行**：入口门 `_resolve_mode` 会当场拦下，加了逃生阀也只拿到开发级产物
  （无 `acceptance.json` / `verdict.json` 可引，上面那串产物根本不存在）。正确处置是**回 CP-B 把 spec 迁到 `cpp_extension`**
  （详见 CP-B 那条），**不是**在这里问用户走哪条 form、也不是加逃生阀硬跑。
  启动 build 前必须按 `SOURCE_ACQUIRED → HEAD_VERIFIED → BUILD_VERIFIED → WORKFLOW_STARTED`
  四段门推进：精确取得当前 facts bundle 的 PR head、detached checkout、核
  `git rev-parse HEAD == expected head`；shell 须具备 `set -Eeuo pipefail` 等价语义。任一阶段首失败
  立即 blocked，禁止继续 build/workflow，同轮不得换 ref、补 fetch 或重跑；下一轮必须使用新的执行目录，
  不复用失败 checkout/build 制品。
  build 入口检查须与实际 argv 一致：`bash build.sh` 只要求脚本可读，直接执行 `./build.sh` 才要求
  executable bit，禁止用无关的权限假设制造假 BLOCKED。
  固定快照必须把远端执行入口与 payload 一起纳入同一摘要 manifest；上传前须在空目录真实解包并核
  入口存在、可读且 `bash -n` 通过，禁止只校 payload 后漏传入口。
- **CP-E 报告**（primary）：**逐字引用** `acceptance.json`/`verdict.json`/`perf_report.json` 裁决 + `task_pr_gaps` + 各维度；
  性能同时报告 `cases_scored`、有效 us/speedup 数和计划覆盖分母；所有性能 case 都须真实采集，`cases_scored=0` 明确性能未验证；
  `needs_review` 不当 pass；门 `FAILED` → `BLOCKED`。`cpp_extension` 的 `repro/index.tsv` 列全 case 与原结果，
  `show_case.sh` / 逐 case `--describe` 展示冻结输入摘要、attrs、调用槽、golden、policy 与原 metrics，
  `run_case.sh` / `cases/*.sh` 负责重放；全部明确 `acceptance_verdict=null`，不得反向改写验收裁决。

### subagent 与 dispatch_mode 表

| subagent | mode | skill | dispatch_mode | 职责（单轮、禁内部循环、不自行判定、只回结构化摘要） |
|---|---|---|---|---|
| `acc-spec-extractor` | subagent | `acc-spec` | `extract_spec` / `refine_spec` | `extract_spec`：`task_doc`+`pr_facts` → `<op>.spec.json` + `task_pr_gaps`（多算子多 spec）；`refine_spec`：mock 门失败据 gate error 修 spec |
| `acc-runner-dev` | subagent | `acc-runner` | `gen_golden` / `gen_runner` / `verify_runner` | **`gen_golden`：据任务书产 `<ops_root>/<op>/golden.py`（真值口径按两档链定、`GOLDEN_CONTRACT` 带引文锚；⚠ **PR/仓里的参考实现一律禁止作 golden 源**）——批 6 补上的「产出者」，此前 golden.py 全仓无人产**；`gen_runner`：据 spec + 算子自带 example 生成 `oprunway_<op>_runner.cpp` + 选构建路径（**锚定 example 不猜**，含 **scope gate**：ops-<族> 仓·aclnn 两段式·opp 安装型（含非 experimental 子树）；catlass（换构建体系）/ 非 aclnn 接口 / 双实现 / 未支持 dtype → BLOCKED/转 P3、不硬塞；⚠ **只对显式 `spec.runner_form == "cpp"` 派发**——`cpp_extension`（含未声明的缺省）走 `cpp_extension_codegen.py` 的官方 `NpuExtension` bundle、`aclnn_py` 无 per-op runner 源，两者都不派本 mode；⛔ 且 `cpp` 非准入形态，本 mode 的产物**进不了验收裁决**，只服务 `--allow-experimental-form` 的开发级路径）；`verify_runner`：验证-才-信，手算 golden 小用例逐元素比，未过不上真机（⚠ 同为**只对显式 `cpp`** 的开发级路径；`aclnn_py` 形态无源可自检 → 跳过本 mode，改走 CP-C 的 harness 真机信任门，非免验证） |
| `acc-verify-rootcause` | subagent | （无 atomic skill） | `verify_aclnn_harness` / `run_npu` / `rootcause` | `verify_aclnn_harness`：仅 `aclnn_py`（⛔ 非准入形态，本 mode 只服务开发级路径）的 CP-C 真机 harness 确定性小见证，产内容寻址收据、不产 acceptance 裁决；`run_npu`：真机 `run_workflow.py --mode <mode> --source-facts <CP-A 取材目录>/source_facts.json`（⚠ `--source-facts` 验收通路必给、缺席拒跑，路径由编排层随 dispatch 交下来；非验收通路不强制。`<mode>` **据 `spec.runner_form` 派生**，受控词表 `{cpp, aclnn_py, cpp_extension}`、**缺省 `cpp_extension`**：`cpp_extension`（或未声明）→ `cpp_extension`（✅ **唯一产验收裁决的通路**）、`cpp` → `new_example`、`aclnn_py` → `aclnn_py`（❌ 后两条须 `--allow-experimental-form`，只产开发级产物）；`mock`/`catlass*` 派生不出、须显式指定），一次原子跑 Task2+3+三级门；`rootcause`：任何 FAIL 先「被测物自 build + 声明 dtype + 手算 golden」独立复现，解耦 op vs harness 再归因（不外发、不替 PR 作者修到底） |

### 编排硬约束（措辞与 3 subagent / SKILL 一致）

- **判定唯一归确定性脚本链**：`validator.py`（精度）+ `perf_compare.py`（性能）+ `validate_acceptance_state.py`
  （三级完整性门）→ 门控后写 `acceptance.json`。**编排层与 subagent 不自行判 pass/fail，只逐字引用确定性产物的裁决并标来源**
  （ADR 0007）——不是「绝不提 pass/fail」。
- **subagent**：**单轮、禁内部循环、禁跨阶段、只回结构化摘要给 orchestrator、不自行判定**。
- **primary**：**可直接跑「无 NL 生成、无判定」的确定性脚本**（`fetch_source` / `validate_taskdoc_input` / `gen_cases --dry-run --ledger-out` /
  `validate_preparation_state` / `preflight_aclnn` / `validate_acceptance_state` / `check_manifest_sync`）；**不做 NL 生成 durable 工件**（spec / **golden.py** / runner 一律派 subagent）；
  **不自行判 pass/fail**；**首响应先加载 `acceptance-workflow` skill、禁裸调 subagent**。
- **三级门是 `run_workflow.py` 内部**（一次性串 Task1→2→3、末尾统一校门，是**批量驱动、非阶段间实时阻断**），
  **不是** orchestrator 分阶段单独调度；门 `FAILED` → 总体 `BLOCKED`、不出 pass 裁决。「不推进下一 Task/停在当前阶段」是 **agent 编排纪律**。
- **Task3 blocked 路由**：`BLOCKED_WAIT_GPU_BENCHMARK`（缺外部 GPU 标杆）/ `BLOCKED_INCOMPARABLE_TIMING_SCOPE`（口径不可比）；
  基线来源按任务书参考源（`spec.perf.baseline` 驱动，当前 aclnn 重写类 isclose/sign/equal/neg = `tbe`（`--mode new_example` 那条路，同法测的**内置 TBE**；⛔ 该 mode 属非准入的开发级路径，其数据不进验收裁决）；
  **runner form 一律不决定 baseline**（`aclnn_py`、`cpp_extension` 都不）：实际对照物由任务书事实与用户确认共同落进 spec。框架级 Torch 或已确认“小算子拼接等价于 Torch 对应接口”走 `torch_npu`；任务书确实要求直接调用某 ACLNN 实现时才走 `aclnn_builtin`，从 CANN `libopapi.so` 直接调用并记录符号/库 provenance。不能只凭 API 名猜等价，也不重复证明用户已确认的事实；
  所有 form 的性能 case 都必须来自同一份精度 caseset；A3 按全部输入物理载荷之和 `<=256 KiB` / `>256 KiB` 分小/大 shape，分类只分组、不免测；
  catlass matmul 属对标类·synthetic·未定基线；proposed·未 settle，载重前需核）；GPU external 对比层 **consumer 侧已接入 pipeline**（`run_workflow --gpu-baseline` → `gpu_baseline` 校验 → `perf_compare` 对比），但**真实 GPU 标杆数据待外部提供**，缺数据即走 `BLOCKED_WAIT_GPU_BENCHMARK`。

## 约束

- **验收权威 = 任务书**；「PR 有测试」≠「验收过了」。
- **通路准入 = `cpp_extension` 一条**（缺省 form）；**「能跑」≠「能出裁决」**——`--allow-experimental-form` 跑出来的绿不得写进裁决栏，spec 写旧 form 时先迁不问。
- 够不着 NPU（远程连时的 VPN 没开、或就地跑但目标机上无可用设备）→ `cpp_extension`（缺省，验收通路）与 `cpp` 都停在 CP-B 的可验证准备收据（vendor 构建收据须真机才产得出），`aclnn_py` 额外跑到 CP-C0 的 `READY_WAIT_NPU_TRUST_GATE`；明确告知「真机跑测待环境就绪」，不启动 mock、不假装真机、不拿 dry-run/preflight 冒充裁决。
- 私有主机名 / 远端路径走 `OPRUNWAY_*` 环境变量、**不入仓**；产物只落 `reports/`；**副作用先确认**（对外单一对话入口、脚本幕后）。
- **就地跑 / 远程连都是一等执行形态**：`OPRUNWAY_TARGET=local` 时无 ssh/scp、`OPRUNWAY_SSH_HOST` 免填，`.oprunway/real-machine.env` 也不需要存在；它只服务远程连（详见 CP-A 前置那条）。
- **插件根变量（跨 CLI）**：制品里的插件根一律写中立主变量 `${OPRUNWAY_PLUGIN_ROOT}`；**Claude Code 下等价
  `${CLAUDE_PLUGIN_ROOT}`**（harness 自动设），**Codex 等其它运行时须自己显式 `export OPRUNWAY_PLUGIN_ROOT=<插件根>`**
  （或跑一遍 `init.sh`，它同样以 `OPRUNWAY_PLUGIN_ROOT` 为主、`CLAUDE_PLUGIN_ROOT` 为兼容别名）。
  **可执行命令里**统一写自兜底形式 `${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}`——两种运行时都能跑，且不依赖谁先记得 export；
  两个都没设即路径为空、当场报错（fail-closed），不静默跑错。
- **跨 CLI 单一源**（proposed·未 settle，载重前需核）：本 `AGENTS.md` 为事实源，`CLAUDE.md` 与之**手工同步**，由
  `acc-common/check_manifest_sync.py` 做**机器校验漂移门**——**与文件系统两方集合比对**：本 frontmatter `agents` ↔
  `agents/*.md`、本 frontmatter `skills` ↔ `skills/*/SKILL.md`（多登记 / 漏登记都报 DRIFT）；外加**硬拒**
  `.claude-plugin/plugin.json` 声明 `agents` 字段。`plugin.json` **不参与 agents 同步**（见上文：它一声明反而全不加载）。
  **真正的「单一源生成器」是 P2 的 `init.sh` 扇出**，非「派生」。
  换运行时只换注册薄壳，`acc-common/` 脚本 + skills `references/` 不动。
