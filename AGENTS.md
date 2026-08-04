# AGENTS.md — OpRunway 仓根唯一指令入口

**全程中文。** 本文件是 Codex、Claude Code 及其它运行时共同使用的**唯一仓规源**。
仓根 `CLAUDE.md` 只保留 `@AGENTS.md` 路由，不再维护第二份规则；规则、状态和环境入口只在这里更新。

---

## 1 · 这个仓是什么

**OpRunway = NPU（昇腾）算子验收工作区**：输入是“算子任务书 + PR 链接”，输出是机器可校验的验收裁决和中文验收报告。

```
任务书 + PR ──① 用例生成（ST）──▶ 测试用例集 ──② NPU 跑测──▶ NPU 精度 + 性能
                                      │
                                      └──③ 同一份用例喂外部 GPU 标杆──▶ NPU↔GPU 性能报告
```

用例集是整条流水线的脊柱：

- Task 1：从任务书与 PR 生成覆盖功能、精度、性能的用例集；
- Task 2：同一份用例在 NPU 上生成精度证据和性能数据；
- Task 3：消费外部 GPU 数据，按同一 case 身份生成跨设备性能报告。

任务书是验收权威；PR 和 op_def 是被测事实与能力证据，不能反过来覆盖任务书。

---

## 2 · 先接插件根变量

制品里的脚本路径统一使用中立变量。Claude harness 通常只提供 `CLAUDE_PLUGIN_ROOT`；Codex 等运行时须显式设置主变量：

```bash
export OPRUNWAY_PLUGIN_ROOT="$(git rev-parse --show-toplevel)/plugin"
```

- 主变量：`OPRUNWAY_PLUGIN_ROOT`；
- Claude 兼容别名：`CLAUDE_PLUGIN_ROOT`；
- 可执行命令统一写 `${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}`；
- 私有主机名、容器名、真实远端路径不得进入 Git tracked 文件；
- 机器本地值只允许写入被 `.gitignore` 忽略的 `.oprunway/real-machine.env`；
- token、密码、私钥连该本地文件也不得写。

---

## 3 · 三层架构与确定性裁决

`plugin/acc-common/` 的 JSON 契约（Layer 0）和确定性 Python 脚本（Layer 1）不依赖任何 agent/CLI 框架；`plugin/agents/`、`plugin/skills/`、`plugin/commands/` 是 Layer 2 薄壳。

**判定的脑子在脚本里，不在 agent：**

- `validator.py`：精度裁决；
- `perf_compare.py`：性能裁决；
- `validate_acceptance_state.py`：三级证据完整性门；
- `run_workflow.py`：门控后写 `acceptance.json`。

任何 agent 或编排层都不得自行重判 pass/fail，只能逐字引用确定性产物并标明来源。

主入口：

```bash
python3 "${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}/acc-common/run_workflow.py" \
  <spec.json> --mode <mode> --out <报告目录>
```

常用脚本：

- `fetch_source.py`：任务书/PR → 中立事实包；
- `validate_taskdoc_input.py`：任务书输入校验门（18 项 + 交付件清单，抽 spec 之前）；
- `reconcile_deliverables.py`：任务书必选交付件 ↔ PR 实际交付物对账（不做模糊名字匹配，认不出即落缺口）；
- `gen_cases.py <spec> --dry-run`：plan-only 用例计划自检，不产裁决；
- `check_golden.py`：golden 来源契约；
- `preflight_aclnn.py`：`aclnn_py` 静态接口预检；
- `verify_aclnn_harness.py`：真机 harness 信任门；
- `validate_preparation_state.py`：非真机复用收据；
- `validate_acceptance_state.py`：验收证据复核门。

跑测信号按“退出码 → 强失败信号 → 强成功信号 → 待复核”分层读取；`UNCERTAIN`、`needs_review` 和证据不完整都不能静默升级为 pass。

---

## 4 · `--mode` 只由 `spec.runner_form` 派生

`spec.runner_form` 是唯一真源，受控词表为 `{cpp, aclnn_py, cpp_extension}`：

| `runner_form` | `--mode` | 执行形态 | 默认性能对照物 |
|---|---|---|---|
| `cpp` 或未声明 | `new_example` | 编译 per-op C++ runner | 同法测的内置 TBE |
| `aclnn_py` | `aclnn_py` | 通用 ctypes 调标准 aclnn 两段式 `.so` | 逐字按任务书配置；可为同机 `torch_npu`，也可直接调用 CANN 内置 ACLNN |
| `cpp_extension` | `cpp_extension` | 按官方 `NpuExtension` / `EXEC_NPU_CMD_EXT` 生成独立 `torch.ops` 调用桥；DUT 仍是指定 PR 构建的 vendor `.so` | 逐字按任务书配置；runner form 不决定 baseline |

- 三条都是真机验收通路、都能产验收裁决；
- `cpp_extension` 不重编 op-plugin，也不把 op-plugin 当 DUT；它只复用官方 C++ Extension 接入机制，
  并须以独立构建收据机校绑定完整 PR head、构建命令和实际加载的 vendor ELF；
- Median + PR6429 当前真机精度基线为 `cpp_extension` 的 torch-parity 完整矩阵：
  1152 例中 1101 PASS、51 FAIL，`gate.passed=true`，确定性裁决为 `FAIL(精度)`；
  此结果取代上一轮 1344 例中 1286 PASS、58 FAIL 的历史 checkpoint；
- `mock`、`catlass`、`catlass_mock` 不能从 `runner_form` 派生，只能显式用于局部开发或对应通路；
- mock 通路物理上不产 `acceptance.json` 或 `verdict.json`；
- argparse 的 `new_example` 默认值不是编排依据，编排层必须按 spec 派生；
- `cpp` 当前真机 dtype 闭环主要是 fp32/fp16/bf16；能力表以 `repo_adapter.SUPPORTED_NP_BY_FORM` 为准，int 等未支持项落 `DEFERRED_NP_BY_FORM`，必须显式挂账并 fail-closed；
- runner form 只决定执行形态，**不能反推任务书指定的实际性能标杆**；每份任务书的 baseline 仍须单独核实。

---

## 5 · 最高纪律

### 5.1 泛化优先，绝不按算子身份特判

这是最高原则：

- 接口、目标目录、shape、dtype、硬件从“任务书 × op_def × header/example”按字段分源探测；
- 代码里不得出现 `if op == "<具体算子>"` 一类身份分派；
- 允许按稳定的接口能力、仓形态或框架扩通用 adapter；
- per-op spec、golden、IR、gap、目标机是通用 schema 消费的数据，合法；
- 手写 per-op runner 或为某算子修改通用判定逻辑，违规；
- 具体算子只能作为见证/测试输入，不能成为通用机制的隐藏特例；
- 建通用能力时优先用能压满结构轴的见证，最小见证只用于冒烟、隔离故障或 baseline；
- 域内定义以 `plugin/acc-common/contract_ir/` 为准；无状态、标准 aclnn 两段式、无 opaque descriptor 的形态应工具零改可跑；
- 域外或未知接口能力一律 fail-closed 标“不支持的接口能力”，不硬塞、不自动归类；
- ABI 以 header/example 为事实源；语义、dtype、硬件以任务书和 op_def 交叉；
- 三源缺失或冲突仍无法确定时，停下询问用户，绝不静默猜测。

### 5.2 方案、权限与副作用

- 构建新 skill/agent/workflow 或做超出既有方案的架构改动前，先给方案、取舍和边界，经用户同意再实施；
- clone、checkout、build、真机跑测、删除/覆盖、改远端环境、对外发布前先确认；
- 支持的脚本优先提供 `*_DRY_RUN=1` 或等价 dry-run；
- 用户授权某项动作不自动扩张到其它仓、其它远端或其它副作用；
- 本地只做编辑、Git、只读探测和知识记录。

### 5.3 一切 compute 在远程 NPU 环境

- build、pytest、用例生成、golden 生成、验收、profiler 全在远程 NPU 容器/目标环境执行；
- 本地不建 venv、不跑 pytest、不 import torch/numpy 做验收 compute；
- 真机环境统一入口：`dev-doc/oprunway-real-machine-environment.md`；
- 实际连接元数据：本地忽略文件 `.oprunway/real-machine.env`；
- 每次新 session 做任何远端 clone/build/跑测/清理前，必须读取
  `.oprunway/real-machine.env` 的 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`。其中每个根及其全部子目录均为
  **只读保留现场**：禁止写入、覆盖、移动、删除或作为新执行目录；只允许经用户明确要求的只读核验。
  未设置表示当前未登记保护根，不得据此猜测或清理其它目录；
- 机器 profile 只负责找到执行环境，不能替代任务书硬件核定和本轮 PR provenance。

### 5.4 零硬编码与本地配置

- 仓名、路径、SoC、目标算子、阈值、PR head 不写死在通用代码；
- 运行时探测、从 spec/pr_facts 派生或询问用户；
- 不碰 `~/.config`、不改 shell rc；
- 验收产物只落用户 CWD 的 `reports/`；
- `.oprunway/real-machine.env` 是机器连接元数据的唯一仓内本地例外，必须保持 ignored。

### 5.5 Git、发布与署名

- 不 push、不 merge，除非用户明示；
- 对非本用户仓的 issue、PR、comment 必须先获同意；
- commit 可以按开发检查点进行，不要求每个 commit 单独审；
- 人类署名使用 `lys` / `lllyys`；
- commit、PR body、报告和其它对外产出不得带任何 AI 署名、trailer 或生成标识；
- 历史遗留 trailer 不重写，只约束新产出。

### 5.6 文档落点与改动简表

- 项目 Markdown、图、SVG 等**开发过程产物**统一放仓根 `dev-doc/`；
  这里放的是设计稿、TODO、handoff、实测记录、环境说明——是**开发者写给自己和后来者看的**，
  不是面向使用者的产品文档（那类若将来要有，另立目录，别混进来）；
- 不写到工作区上层的 `markdown/`；
- 每次落地后在 `dev-doc/oprunway-changes-brief.md` 顶部追加一两句倒序摘要；
- 当前交接以 `dev-doc/oprunway-session-handoff-2026-07-26.md` 为准，旧 handoff 只作历史材料。

### 5.7 push 前审修门

push 前，对自上次 push 以来将要发布的全部改动统一做一轮审修；不逐 commit 审：

- 代码/脚本：走 `cc-suite:audit-fix` 的 audit → fix → verify，一轮即停；
- 散文/设计/仓规：走独立 Codex 散文审；
- 散文审默认使用 `gpt-5.6-sol`、reasoning `low`；
- 当前 CLI 形式为 `codex exec -m gpt-5.6-sol -c model_reasoning_effort=low`；
- verify 剩余 finding 如实交用户，不自动无限迭代；
- `nlpm` 是 NL 制品质量 lint，不替代本门；
- 旧 `mcp__plugin_nlpm_codex-cli__codex` 已退役，散文审走 `codex exec` CLI；
- ADR 0010 当前存在历史触发点张力；在 `bureau:review` settle 前，以本节“push 前统一审修”为现行规则。

### 5.8 不捏造、不越权判定

- 报告数字、错误和耗时必须来自真实日志/产物；
- 推断项显式标“推断”；
- `needs_review` 不当 pass；
- “PR 有测试”“代码接通”“covered”“collector 有数据”都不等于验收通过；
- FAIL 归因前先核任务书↔PR 对应，再解耦 DUT 与 harness；
- 验收权威只认任务书，最终裁决只认确定性脚本链。

### 5.9 canon 写门与开工 grounding

- durable 知识遵循 capture → compile → review；
- 不手改 cabinet 页，不自行把状态升为 canonical；
- 只有 canonical 可当已定事实，proposed/verified/stale/contested 均须按 trust tier 对待；
- 开始 durable 设计、组件建设、bureau 写入或 FAIL 归因前，先读 `canon/architecture/`、`canon/decisions/` 和 `canon/lint/findings.md`；
- canon 过大时至少读 overview，并用 bureau query 按需查证；
- 通读与 query 并用；未读或未 settle 页面不得冒充门禁依据；
- 当前运行规则与未 review canon 冲突时，显式记录张力，不静默覆盖。

### 5.10 性能口径：只测 msprof 实测，不比 GPU

用户 2026-08-03 明示的**全局原则，适用于所有类型的任务书**：

- 任务书**对性能没有要求** → 只用 msprof 采 NPU 实测性能即可；
- 任务书要求的是**与 GPU 比对**（如“以 OpenCV CUDA A100 为参考，ratio ≥ 0.45×”）→ **同样只用 msprof 测实测性能**，
  不必真的去对比 GPU、不必获取 GPU 标杆数据。

理由：GPU 标杆（A100 / OpenCV CUDA / ATK 双标杆）要么拿不到环境，要么获取成本远高于它带来的验收价值；
卡在等 GPU 数据上会把整条流水线阻塞住。NPU 侧 msprof kernel-only 数据本身就是可信、可复现的性能证据。

落地约束：

- 不因缺 GPU 数据把结论落到 `BLOCKED_WAIT_GPU_BENCHMARK`；该终态只在用户明确要求做 GPU 对比时才用；
- 性能维产出 = msprof 实测 kernel 耗时 + 分档说明，不是比值裁决；
- 报告须如实写“按用户口径只做 NPU msprof 实测，未做 GPU 标杆对比”，
  **不得把它包装成“已达标 0.45×”**——没测的比值不能编（5.8）；
- msprof 数据仍须真机真跑，不接受推算（5.3）。

---

## 6 · 仓目录

```text
OpRunway/
├── AGENTS.md                         # 唯一仓规源
├── CLAUDE.md                         # 仅 @AGENTS.md 路由
├── BUREAU.md                         # bureau 入口
├── .oprunway/
│   ├── real-machine.env.example      # tracked 脱敏模板
│   └── real-machine.env              # ignored 本地真实值
├── dev-doc/                          # 开发过程产物：设计、TODO、handoff、实测记录、环境说明
├── plugin/
│   ├── acc-common/                   # Layer 0/1 契约与确定性脚本
│   ├── agents/                       # Layer 2 agent 薄壳
│   ├── skills/                       # Layer 2 skills
│   ├── commands/                     # Layer 2 入口
│   └── samples/                      # spec/golden/runner 样例数据
├── canon/                            # bureau durable knowledge
├── reports/                          # ignored 验收产物
└── repos/                            # ignored 外部被测/参考仓
```

---

## 7 · 外部仓与复用边界

涉及仓会随任务变化，已知范围包括但不限于：

- `cann/catlass`
- `cann/ops-nn`
- `cann/ops-math`
- `cann/ops-sparse`
- `cann/ops-blas`
- `cann/ops-cv`
- `cann/asc-devkit`
- `cann/catccos`
- `cann/shmem`
- `cann/oam-tools`
- `cann/amct`
- `cann/hixl`
- `cann/cann-recipes-infer`（重点子目录 `ops/tilelang`）

复用边界：

- `repos/` 下的外部仓是被测对象或方法论参考，不进入本仓 Git；
- 浅克隆不能冒充指定 tag/commit 已核实，需要特定版本时单独 fetch 并记录 provenance；
- `cannbot-ops-input`/cannbot 只作 case、精度、性能方法参考，不成为运行时依赖；
- catlass、ops-*、稀疏/通信等不同仓形态通过通用能力或 per-repo adapter 接入，不互相硬套；
- 姊妹项目的环境搭建经验可复用，“跑没跑崩”式判定不能替代本仓精度/性能验收；
- 具体任务始终以正确的任务书、对应 PR 和本轮事实包为准。

---

## 8 · 深挖入口

| 目标 | 入口 |
|---|---|
| CP-A..E 状态机、硬门、subagent 契约 | `plugin/AGENTS.md` + `plugin/skills/acceptance-workflow/SKILL.md` |
| 设计与数据契约 | `dev-doc/oprunway-design.md` |
| 最新交接 | `dev-doc/oprunway-session-handoff-2026-07-26.md` |
| 当前 TODO | `dev-doc/oprunway-todo.md` |
| 改动流水 | `dev-doc/oprunway-changes-brief.md` |
| 真机环境 | `dev-doc/oprunway-real-machine-environment.md` + `.oprunway/real-machine.env` |
| 已定决策 | `canon/decisions/`，先看 status/trust tier |
| 人读蓝图/历史案例 | `plugin/workflows/`，冲突时以 acceptance-workflow skill 为准 |

---

## 9 · 当前能力边界

- 真 NPU 已坐实：IsClose、Sign；Median PR6429 当前为 1152 例中 1101 PASS、51 FAIL，
  `gate.passed=true`、确定性裁决 `FAIL(精度)`；上一轮 1344-case 结果仅作历史记录；
  Elu/Silu 在 A5-950 有 18/18 非空例证据；
- Median 性能数据不是零数据：custom 50/50、`torch_npu` baseline 48/50 有效，48 对评分、35 对达到 `ratio >= 1.0`；
- 2 个 BF16、`dim=1` baseline case 报 161002、custom 成功，按 baseline limitation 挂起，不归因 DUT；
- 用户已确认 Median 任务书所称 `aclnnMedian` / `aclnnMedianDim` 小算子拼接版本等价于 Torch 对应接口，故性能 baseline 为同机 `torch_npu` 的 `torch.median`，无需另证等价、也不改为直调单个 ACLNN 接口；
- **通用性能 case 规则**：性能 case 必须从同一份精度 caseset 选择；A3 按全部输入物理载荷之和 `<= 256 KiB` 为小 shape、`> 256 KiB` 为大 shape。硬件边界写入 spec，不按算子身份分支；大小分类只用于分组，所有性能 case 仍须真实采集；
- `aclnn_py` perf collector 已真机产出同口径 kernel-only 数据，但“通路有数据”不等于“任务书条款通过”；
- mock/catlass_mock 只产带 `evidence_grade="development"` 和 NON-ACCEPTANCE 标记的 `dev_run_summary.json` / `dev_precision_check.json`，不产 `acceptance.json` / `verdict.json`；
- ops-<族>、标准 aclnn 两段式、用户态 opp 安装型是当前主要闭环；域外形态 fail-closed；
- 外部 GPU consumer 已接入，真实 GPU 数据仍待提供，缺失时走 `BLOCKED_WAIT_GPU_BENCHMARK`。

---

## 10 · 发布形态

- OpRunway 继续作为本仓 `plugin/` 子目录维护，不拆独立 repo；
- scripts、JSON 契约和 skill references 保持工具中立；
- 各运行时只维护注册/入口薄壳；
- skills 外部同步属于后续发布事项，不是当前验收阻塞项；
- `CLAUDE.md` 不再复制规则，只路由到本文件。

<!-- bureau:start -->
@BUREAU.md
<!-- bureau:end -->
