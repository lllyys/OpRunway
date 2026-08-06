---
name: op-acceptance
description: 跑一个 NPU 算子的验收流水线——输入=算子任务书(md 路径或链接)+PR 链接，自动产 spec→跑测→跑确定性脚本、逐字引用 acceptance.json 裁决并标来源→报告。
argument-hint: "<任务书 md路径或链接> <PR链接> [--mode cpp_extension|mock]"
---

# /op-acceptance — 算子验收（人手动触发）

人触发版：把「任务书 + PR」交给 **`op-acceptance` agent** 跑完整验收。与 agent 同一流程，只是入口不同（agent 供别的 agent 自动调、本命令供人手动跑）。

**参数**：`$1`=任务书（md 本地路径或 http(s) 链接）、`$2`=PR 链接、可选 `--mode`。

**`--mode` 不用人指定、也不问用户——由编排层据 `spec.runner_form` 派生**（`runner_form` 是唯一真源，受控词表
`{cpp, aclnn_py, cpp_extension}`，**缺省 = `cpp_extension`**）。
⚠ **派得出 mode 的只剩一条**：`cpp_extension`（或未声明）→ `--mode cpp_extension`。
`cpp` / `aclnn_py` 自 2026-08-06 **停止准入**——派生表 `run_workflow._RUNNER_FORM_TO_MODE` 里没有它们的条目，
省 `--mode` 派不出、显式指定真机 mode 同样被拒。**词表 ≠ 准入表**：它们仍是合法的 `runner_form` 取值（读得出旧 spec），
但正式验收抽 spec 时一律抽 `cpp_extension`。
`mock` / `catlass` / `catlass_mock` **派生不出来**，只能显式指定（局部自检 / catlass 通路的正当逃生口）。
`run_workflow.py` 省略 `--mode` 时也会据 `spec.runner_form` 派生；显式传入另一条真机 mode 会 fail-closed。编排层仍须在摘要中写清派生结果，不能让 mode 来源隐形。

⚠ **验收裁决当前只出自 `cpp_extension` 一条通路**（`run_workflow._ACCEPTANCE_RUNNER_FORMS
= frozenset({"cpp_extension"})`，入口门 `_resolve_mode` + 出口门 `_assert_acceptance_form_allowed`
两道；理由见仓根 `AGENTS.md` §4）。**别把「能跑」读成「能出裁决」**：

| 通路 | 能不能跑 | 能不能产验收裁决 |
|---|---|---|
| `cpp_extension` | ✅ | ✅ **当前唯一准入形态** |
| `cpp`（`new_example`） | ⛔ **停止准入（2026-08-06）**：派不出 mode，显式 `--mode new_example` 也拒 | ❌ 跑不起来；历史产物保持原裁决与历史效力 |
| `aclnn_py` | ⚠ 同上 | ❌ 同上 |
| `mock` / `catlass_mock` | ✅ 须显式指定 | ❌ C5 起**物理上不产** `acceptance.json` / `verdict.json` |

⚠ 逃生阀 `--allow-experimental-form` **已删除（2026-08-06）**：它当年只放行**执行**、不放行产裁决，但「跑得起来的死路」被 aclnnRoll 试跑证明会有人走进去——
该路径物理上不写 `acceptance.json` / `verdict.json`，所以「加了逃生阀跑绿了」不得写成验收通过、
不得进验收报告的裁决栏。`mock` 通路同理：它的「NPU 输出」就是 golden 本身、精度按构造必过 →
C5（2026-07-22）起改产带 `evidence_grade="development"` + `acceptance_note` NON-ACCEPTANCE 标记的
`dev_run_summary.json` + `dev_precision_check.json`。**不是「产了但不算数」，是压根不产。**

⚠ **不要问用户「走 aclnn_py 还是 cpp_extension」**：验收统一按 `cpp_extension` 走，form 由 spec 派生、
不由人挑。spec 若写着 `cpp` / `aclnn_py`，正确处置是**迁到 `cpp_extension`**（需 torch.ops 调用桥 +
vendor ELF 构建收据，接入成本更高，这是已知账单），而不是回头问用户要不要换条路。

## 做什么

调起 **`op-acceptance` agent**（`agents/op-acceptance.md`，`mode:primary` 薄编排器）。它首响应先加载 **`acceptance-workflow` skill**，按其 **CP-A..E 状态机**推进——**先 CP-A 前置，再 CP-B..E**：

- **CP-A 前置**：primary 跑**确定性** `fetch_source.py` 取材并落 `source_facts.json` → **任务书↔PR 对应校验**（verify-spec-pr-correspondence，proposed·未 settle，载重前需核）→ 环境确认（**执行形态：就地跑还是远程连** / NPU 通不通 / 目标机按任务书 `适配硬件` × op_def `AddConfig` 双源定）。⚠ **两种执行形态平级**：就地跑（会话本身已在目标机或其 NPU 容器里）设 `OPRUNWAY_TARGET=local`、`OPRUNWAY_SSH_HOST` 免填、`.oprunway/real-machine.env` **不需要存在**；远程连才从该文件取 SSH alias / 容器名 / 远端工作根（`OPRUNWAY_TARGET=remote` 缺省时 `OPRUNWAY_SSH_HOST` 必填）。**不得**以「缺该文件 / 拿不到连接元数据」为由拒绝启动验收；该文件**存在时**才必须读 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`，那些根是只读保留现场、未登记 ≠ 可随意清理（仓根 `AGENTS.md` §5.3）。⚠ **`OPRUNWAY_TARGET` 只管 `repo_adapter` / `aclnn_adapter` 的传输层，不是验收通路的形态门**：`cpp_extension` 的传输就是 `OPRUNWAY_CPP_EXTENSION_DRIVER_JSON` 那串 argv 本身（adapter 不读该变量、也不把传输模式绑进收据），声明就地跑时须由编排层自己核 argv 不带 `ssh` / `docker exec -H` 等跨机前缀。组装 `correspondence.json` 时写入当前 `source_facts_digest`；issue/追踪号这类 **NL-read 字段显式标 `source=NL-read` + 出处（task_doc / PR title）**；status 判定靠 **`pr_facts.target_dir` 机器比对 + 用户确认**（`needs_user_confirmation` 由用户拍板），primary **不自行 NL judge 空任务、不把 NL 结论当事实落盘**。
  - **对应不成立（`mismatch`，由 `pr_facts.target_dir` 机器比对判定）→ 出「程序结论」（非 pass/fail）、不跑**；**疑似空任务/证据不足**（需 NL 判断的）→ 归 `needs_user_confirmation`、摆证据由用户拍板（primary 不自行 NL judge 空任务）；`confirmed` 才继续。
- **CP-B Task1 用例**（只关注 task1/caseset 自洽）：facts 刷新后先重跑 `validate_preparation_state.py`；`REUSABLE` 直接跳过 NL dispatch 与 dry-run，`MISS` 只补对应缺口，`BLOCKED` 停止。**CP-B0 任务书输入校验门不随 `REUSABLE` 跳过**（那份收据不绑 `taskdoc_validation*`，替它背书会让本门被旧收据绕过）：**每轮都 inline 重跑 `validate_taskdoc_input.py`** 按 18 项契约复核结构与绑定、机械派生阻断清单（`acceptance_verdict=null`），省掉的只有贵的 NL dispatch。冷启动或 digest 漂移时 dispatch `acc-spec-extractor:validate_taskdoc`（只读任务书自己）→ `taskdoc_validation.json`；`NEEDS_USER` → 汇总问用户（阻断项只能补充事实或停止验收，豁免只对不阻断的待确认项开放）后重跑脚本，转 `PASSED`/`PASSED_WITH_PENDING` 继续，`BLOCKED` → 重做。过门后按六段契约 dispatch `acc-spec-extractor:extract_spec`（输入含 taskdoc/snapshot/pr_facts/source_facts/correspondence+confirmed_constraints）→ `<op>.spec.json` + `task_pr_gaps`；再 dispatch `acc-runner-dev:gen_golden` 产 `golden.py` 并跑 `check_golden.py`，按退出码 0/2/1 路由；之后 primary inline `gen_cases.py <spec> --dry-run --ledger-out <work>/case_plan.json --source-facts <work>/source_facts.json --correspondence <work>/correspondence.json`（plan-only 契约自检：预算区间 / dtype 分布 / 特殊场景覆盖 / id 唯一 / 种子确定；同时绑定事实包与用户确认），再用 `validate_preparation_state.py` 产只限非真机准备阶段的复用收据（不产验收裁决）。
  ⚠ **能力边界**：dry-run **plan-only**：不调 `golden_fn`、不落 `.npy`、不产任何裁决；会加载执行 `golden.py` 取 `out_shape`（缺文件只记「未核」，文件在但坏了则当场抛）；**验不了**来源契约（那是 `check_golden.py`）/ validator 链 / 三级门，那些只有 CP-D 才验得到。
  dry-run 报错或账本异常 → `refine_spec`。
- **CP-C runner**（真机路径、需 NPU）：**不再按 form 分流，只有 `cpp_extension` 一条路由**：dispatch `acc-runner-dev` 生成官方 `NpuExtension` bundle；真机 build/load/执行由显式 driver 完成并回传专属内容寻址收据（须绑定精确 PR head / 构建命令 / 实际加载的 vendor ELF），收据不齐或漂移即停在 CP-C。
  ⚠ spec 写着 `cpp` / `aclnn_py` 时 **CP-C 无事可做**——回 CP-B 迁 `cpp_extension`；旧分流的机制描述见紧随其后的历史区，**不得据它 dispatch**。
<!-- oprunway:retired-begin -->
  ⛔ 历史留档 · 不得 dispatch · 不要照做（`cpp` / `aclnn_py` 已停止准入、无真机入口，编排层不会再走到）：`cpp` 才 dispatch `acc-runner-dev:gen_runner`（先过 scope gate）→ `verify_runner`；`aclnn_py` 不派这两个 mode，以报告根运行 `preflight_aclnn.py --source work/source_facts.json --pr-facts work/pr_facts.json --spec ops/<Op>/<Op>.spec.json`，成功也只标 `READY_WAIT_NPU_TRUST_GATE`。随后 dispatch `acc-verify-rootcause:verify_aclnn_harness`：正式生成完整 caseset/golden，运行 `verify_aclnn_harness.py` 的确定性小见证，产内容寻址 `work/aclnn_harness_trust.json`。该收据绑定见证数据字节、golden 源码、PR/build/toolkit/SoC/符号与执行逻辑，只证 harness、不裁决算子、不裁剪正式用例；`run_workflow` 在正式 adapter 前按当前环境硬复核。任一自检证据未满足或漂移则停在 CP-C、不进 CP-D。（acceptance 裁决只逐字引用 `validator.py` / `perf_compare.py` / `validate_acceptance_state.py` 产物，ADR 0007。）
<!-- oprunway:retired-end -->
- **CP-D 真机跑测**（一次原子）：dispatch `acc-verify-rootcause:run_npu` → `run_workflow.py --mode <mode> --source-facts <CP-A 取材目录>/source_facts.json`（⚠ **`--source-facts` 在验收通路上必给、缺席直接拒跑**——路径就是 CP-A `fetch_source.py --out <取材目录>` 产的那份，由编排层随 dispatch 交给 subagent，**与 `--out reports/<op>/` 不是同一个目录**；三级门要拿它与 vendor build receipt 的来源锚逐字对账。非验收通路（显式 `--mode mock` / `catlass*`）**不受此强制**）（**`<mode>` 据 `spec.runner_form` 定**：`cpp_extension`（缺省）→ `--mode cpp_extension`，须 `OPRUNWAY_CPP_EXTENSION_REAL=1` + 过 build/load/vendor receipt 门——**这是当前唯一能产验收裁决的通路**；`cpp` / `aclnn_py` ⛔ **已停止准入**：派不出 mode、显式指定也被拒；`mock`/`catlass*` 派生不出、只能显式指定）（Task2 精度 + Task3 性能 + 末尾统一校门一次成）。
  - **产出按路径分叉**（`run_workflow.py` 二选一落盘，**两套不并存**；对开发级路径而言缺 `acceptance.json` **不是缺件，是这条路压根不写**，别停下等它）：
    - **验收路径 `cpp_extension`** → `evidence.json` / `verdict.json` / `baseline.json`（有基线时）/ `perf_report.json` / `acceptance.json` + Markdown 验收报告；三级门 task1/task2（+task3）落 `acceptance.json.gate`。
    - **非验收路径**（显式 `--mode mock` / `catlass*`）→ `evidence.json` / `dev_precision_check.json` / `baseline.json`（有基线时）/ `perf_report.json`（带 NON-ACCEPTANCE 戳）/ `dev_run_summary.json`（字段是 `pipeline_result` / `precision_check` / `selfcheck`）；**不产** `verdict.json` / `acceptance.json` / Markdown 验收报告。门只跑 task1（+条件性 task3）的**管路自检**（task2 门读 `verdict.json`，那条路无此文件），`selfcheck.passed=true` **不等于**验收门过。
  - **Task3 性能**：基线来源=`spec.perf.baseline`（perf-baseline-by-reference-source，proposed·未 settle，载重前需核）；缺外部 GPU 标杆 → 路由 `BLOCKED_WAIT_GPU_BENCHMARK`，口径不可比 → `BLOCKED_INCOMPARABLE_TIMING_SCOPE`；**GPU external 对比层 consumer 侧已接入 pipeline，缺的是外部真实数据**。FAIL → primary 再 dispatch `acc-verify-rootcause:rootcause`（先解耦再归因）。
- **CP-E 报告**（primary，**只对验收路径 `cpp_extension` 成立**）：逐字引用 `acceptance.json`/`verdict.json`/`perf_report.json` 裁决 + `task_pr_gaps` + 各维度出中文报告；性能同时报告 `cases_scored`、有效 us/speedup 数及计划覆盖分母。所有性能 case 都须真实采集，`cases_scored=0` 明确性能未验证。
  - ⚠ **非验收路径（显式 `--mode mock` / `catlass*`）不进 CP-E**：无 `acceptance.json` / `verdict.json` 可引，**别卡在这里等文件，也别拿 `dev_run_summary.json` / `dev_precision_check.json` 顶上去出验收报告**。正确处置是回到 CP-B 把 spec **迁到 `cpp_extension`** 重走验收，不是回头问用户换条路。若本就只做局部开发验证，则输出**开发级说明**（逐字引 `dev_*` + `evidence_grade="development"` + NON-ACCEPTANCE + 「本轮无验收裁决」），明确它**不是**验收报告、不填裁决栏。

两种情形（**mode 按 `spec.runner_form` 派生、不由人挑**）：

- **`runner_form: cpp_extension`（缺省）→ `--mode cpp_extension`**（真机）：✅ **当前唯一能产验收裁决的通路**。**先确认执行形态（就地跑 / 远程连）与 NPU 可达**（远程连时另含 VPN / 跳板通不通）；走全 CP-A..E；按官方 `NpuExtension` / `EXEC_NPU_CMD_EXT` 生成独立 `torch.ops` 调用桥，DUT 是指定 PR（或本地 checkout）构建的 vendor `.so`，须由构建收据绑定来源锚、构建命令与实际加载的 ELF；性能基线逐字按任务书配置（runner form **不**决定 baseline）；`OPRUNWAY_*` 环境变量指真实机器/路径（不写进仓）。
- **`mock`（含 `catlass_mock`）**：派生不出、只能显式指定；本地演示与管路自检用。**不产验收裁决**（见上 C5），产物一律标 NON-ACCEPTANCE。

<!-- oprunway:retired-begin -->
> ⛔ 历史留档 · 不得 dispatch · 不要照做
>
> 下面两条形态自 2026-08-06 **停止准入**：派生表里没有它们的条目、显式指定真机 mode 也被拒、逃生阀已删除。
> 留着只为解释**历史产物是怎么来的**（那些产物保持原裁决与历史效力）。spec 写着它们又要正式验收时，
> 唯一处置是**迁到 `cpp_extension`**，不是照下面配一遍再试。
>
> 当年的派生表有三行：`cpp` → `--mode new_example`、`aclnn_py` → `--mode aclnn_py`、
> `cpp_extension` → `--mode cpp_extension`；**现在只剩最后一行**。记下来是为了读懂历史工件里的
> `mode` 字段，不是让你照着再派一次。
>
> - **`runner_form: cpp`**：曾编译 per-op C++ runner 上真机跑，性能基线是**同法测的内置 TBE**
>   （见 `acc-common/new_example/run_on_npu.sh` 头注）；真机 dtype 白名单只到 fp32/fp16/bf16。
> - **`runner_form: aclnn_py`**：曾无 per-op runner 源，op 工程即 DUT、通用 ctypes 两段式调 `.so`；
>   性能基线仍由任务书/spec 决定。历史小 caseset 的 PASS（旧 caseset）**不得**沿用为当前 torch_parity 下的结论。
<!-- oprunway:retired-end -->

> ⚠ **为什么收敛到 `cpp_extension`（理由是真机成熟度，不是形态优劣，详见 `AGENTS.md` §4.1）**：
> ① `cpp_extension` 跑通过完整 torch_parity 矩阵（Median PR6429 1152 例、`gate.passed=true`），是唯一有完整矩阵背书的通路；
> ② `cpp` 那条路的真机 dtype 白名单只有 fp32/fp16/bf16（`repo_adapter.py` 的 `_NP`），
> 声明了 int32 之类的算子（如 median）会落进 `DEFERRED_NP_BY_FORM["cpp"]`：生成期造得出用例、**真机跑到 fail-closed**；
> ③ `aclnn_py` 只有旧 caseset 的历史结果，迁到 torch_parity 后必须重跑。
> ⚠ **能力表 ≠ 准入表**：`repo_adapter.SUPPORTED_NP_BY_FORM` / `DEFERRED_NP_BY_FORM` 里 `cpp` / `aclnn_py` 的条目照旧保留，
> 那张表回答「这条通路支持哪些 dtype」，准入白名单回答「这条通路能不能出裁决」——两个问题，别互相反推。

## 性能对比（Task 3，待散文门）
- **GPU 标杆 consumer（T8）**：`run_workflow.py --gpu-baseline <外部 GPU 标杆 JSON>` 或 `spec.perf.baseline∈{gpu,gpu_external}` → 解析外部 GPU 标杆(按 case_id+完整输入签名对齐)出 NPU↔GPU 对比。缺标杆 → `BLOCKED_WAIT_GPU_BENCHMARK`（正规挂起、非 fail、绝不显 PASS）；双边 timing_scope 不一致 → `BLOCKED_INCOMPARABLE_TIMING_SCOPE`。真 GPU 数据待外部方给。
- **小 shape 例外（T6）**：任务书『<Nus 差 Nus→仿真图』条款 → 达标记 False + 出仿真图证据；**须先过 `gate_task3`**（图齐备+例外行↔图交叉一致+SVG sha 钉死）才 → `PASSED_WITH_RISK`（挂人工 CP，退出码 2）；**门未过 → `BLOCKED(验收门未过)`（exit 1）、非 PASSED_WITH_RISK**。

## 约束
- 全程中文；副作用（真机 clone/build/跑测）先确认；`needs_review` 不当 pass；验收门 `validate_acceptance_state.py` STATUS: FAILED → **不出 pass 裁决；仍由 run_workflow 写 `acceptance.json.overall="BLOCKED(验收门未过)"`（exit 1）**（验收门未过=证据不可信/不完整）。
- 只认任务书为验收权威；**够不着 NPU**（远程连时 VPN / 跳板没通，或就地跑但本机无可用设备）就明说「真机跑测待环境就绪」，不假装跑了真机。⚠ 「够不着 NPU」是**设备真的不可达**，**不是**「读不到 `.oprunway/real-machine.env`」——那只是远程连形态的连接元数据，缺它不构成阻塞。
- 判定唯一归**确定性脚本链**（`validator.py` 精度 + `perf_compare.py` 性能 + `validate_acceptance_state.py` 三级完整性门 → 门控后写 `acceptance.json`，ADR 0007）；本命令与 agent **不自行判 pass/fail**，只逐字引用确定性产物的裁决并标来源。⚠ 这条链的**末端产物只在验收路径 `cpp_extension` 落盘**；开发级路径同样跑判定但落 `dev_*` 产物、不产验收裁决（CP-D 那节的「产出按路径分叉」是权威）。
