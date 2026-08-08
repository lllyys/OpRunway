# AGENTS.md — OpRunway 仓根唯一指令入口

**全程中文。** 本文件是 Codex、Claude Code 及其它运行时共同使用的唯一仓规源。仓根
`CLAUDE.md` 只保留 `@AGENTS.md` 路由；不要维护第二份规则。

## 1 · 项目与验收模型

OpRunway 是昇腾 NPU 算子验收工作区。输入是**调用方给定的任务书与被测源码**，输出是机器可校验的
精度/性能证据、确定性裁决和中文报告。

- 调用方传入这对输入，即断言二者对应；工具不以 PR、issue、fork、ref 或 head 再做对应关系鉴权。
- 任务书是语义与验收要求权威；源码和 op_def 是 ABI、能力与被测事实，不能覆盖任务书。
- 输入形式只影响取材：任务书可为 URL 或本地文件，源码可为在线 locator 或本地快照。
- 放松的是 locator 身份鉴定，不是内容可信链。必须绑定任务书摘要、源码内容锚、构建树、vendor ELF、
  实际加载符号、调用、输出和 evidence。

流水线：

1. Task 1：从任务书和源码生成 spec、caseset 与 golden。
2. Task 2：同一 caseset 在 NPU 上生成精度证据和任务书要求的性能证据。

Workflow 不负责 GPU，也不得连接、运行、采集或消费 GPU 数据。任务书出现 GPU 口径时，只按 §6
解析为 CPU 或 NPU 侧可执行口径，并如实记录未覆盖项。

## 2 · 插件根与本地配置

Codex 等运行时先设置：

```bash
export OPRUNWAY_PLUGIN_ROOT="$(git rev-parse --show-toplevel)/plugin"
```

- 主变量：`OPRUNWAY_PLUGIN_ROOT`；Claude 兼容别名：`CLAUDE_PLUGIN_ROOT`。
- 脚本路径统一写 `${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}`。
- 私有主机名、容器名、远端路径不得进入 tracked 文件。
- 远程连接元数据只放 ignored 的 `.oprunway/real-machine.env`；token、密码、私钥不得写入任何仓内文件。
- 不改 `~/.config`、shell rc 或用户全局环境。

## 3 · 架构与确定性裁决

`plugin/acc-common/` 的 JSON 契约（Layer 0）和确定性 Python 脚本（Layer 1）不依赖 agent；
`plugin/agents/`、`plugin/skills/`、`plugin/commands/` 是 Layer 2 薄壳。

判定只能来自：

- `validator.py`：精度裁决；
- `perf_compare.py`：性能裁决；
- `validate_acceptance_state.py`：三级证据完整性门；
- `run_workflow.py`：门控后生成最终 `acceptance.json`。

Agent 和编排层不得自行重判 pass/fail，只能逐字引用确定性产物。`UNCERTAIN`、`needs_review`、证据不完整
均不得升级为 PASS。

正式入口：

```bash
python3 "${OPRUNWAY_PLUGIN_ROOT:-$CLAUDE_PLUGIN_ROOT}/acc-common/run_workflow.py" \
  <spec.json> --out <报告目录> \
  --source-facts <CP-A目录>/source_facts.json
```

正式验收必须提供 CP-A 产出的 `source_facts.json`，且 `completeness.status=complete`。它必须在创建报告目录、
staging 和 Task 1 之前校验；报告目录中的副本不是输入。Mock 等非验收通路不受此要求，但物理上不得产正式
裁决。

关键唯一真源：

- `fetch_source.py`：物化任务书和源码目标内容，生成 facts 与 `content_anchor`；
- `source_provenance.py`：严格解析 caller-trusted association/content anchor；
- `vendor_build_receipt.py`：build receipt schema、生成与校验唯一实现；
- `validate_taskdoc_input.py`：抽 spec 前的任务书门；
- `gen_cases.py --dry-run`：用例计划自检；
- `validate_acceptance_state.py`：facts → build → ELF → execution 的正式复核门。

Fresh 构建必须先 `snapshot-digest`，再由 `emit` 真正执行构建并生成 current vendor receipt。生产端须从
实际 root/scope 重算内容锚、拒绝目标范围内软链，并证明 ELF 由本轮构建改写。旧 head-only receipt 只能历史
只读，不得进入 fresh 裁决。

## 4 · Runner form 与验收准入

- 当前唯一可产正式裁决的 `runner_form` 是 `cpp_extension`。
- 正式 spec 必须显式写 `"runner_form": "cpp_extension"`。
- `spec.runner_form` 是唯一真源；mode 由它派生，编排层不得自行传 `new_example`。
- 只有字段缺席才使用 `repo_adapter.DEFAULT_RUNNER_FORM`；显式 `null`、空串或错误类型必须 fail-closed。
- 读侧统一使用 `repo_adapter.spec_runner_form()` / `resolve_runner_form()`，禁止用 `or` 吞掉坏值。
- `cpp`、`aclnn_py`、mock、catlass 等注册能力不等于验收准入；开发自检结果不得称验收通过。
- 不得恢复 `--allow-experimental-form` 逃生阀。

准入必须在三处保持一致：入口 `_resolve_mode`、出口 `_assert_acceptance_form_allowed`、近路
`finalize_clean_acceptance`。能力表和执行器注册表不得反推验收白名单。

`cpp_extension` 只提供独立调用桥，不把系统 op-plugin 当 DUT；正式 receipt 必须绑定调用方源码内容锚、
构建命令、vendor ELF、实际加载的两个符号以及输出写入证据。

## 5 · 实施纪律

### 5.1 泛化优先

- 禁止 `if op == "具体算子"` 一类身份特判；具体算子只能作 spec 数据或测试见证。
- 接口、目标目录、shape、dtype、硬件从任务书、op_def、header/example 按字段分源探测。
- ABI 以 header/example 为事实源；语义、dtype 和硬件以任务书为权威并与 op_def 交叉。
- 稳定接口能力可扩通用 adapter；未知或域外能力必须 fail-closed，不硬塞、不自动归类。
- 域内契约以 `plugin/acc-common/contract_ir/` 为准。
- 输入内容内部事实缺失或冲突时停下询问用户；不要重新质疑调用方给定的任务书/源码关联。

### 5.2 权限与副作用

- 架构改动、新 skill/agent/workflow 在超出现有方案时，先给方案与边界，经用户同意再实施。
- Clone、checkout、build、真机跑测、删除/覆盖、改远端环境、对外发布前先确认。
- 授权只覆盖明确事项，不自动扩张到其它仓、机器或副作用。
- 支持的脚本优先提供 dry-run；破坏性动作先只读解析精确目标。

### 5.3 Compute 与目标环境

- Build、测试、用例/golden 生成、验收和 profiler 全部在 NPU 目标环境执行。
- 无 NPU 的开发机只做编辑、Git、只读探测与记录，不建验收 venv、不 import torch/numpy 做验收 compute。
- “远程连”和“就地跑”是平级形态；缺 `.oprunway/real-machine.env` 不构成阻塞。
- 若该文件存在，任何远端操作前必须读取 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`；每个保护根及其子目录
  一律只读，禁止作为新执行目录、覆盖、移动或删除。
- 未登记保护根不等于获得删除/覆盖授权。环境细节见 `dev-doc/oprunway-real-machine-environment.md`。

### 5.4 零硬编码与产物

- 仓名、路径、SoC、算子名、阈值、URL/ref/head 等不得写死在通用代码。
- 验收产物落用户 CWD 的 `reports/`；机器本地值只放 ignored 配置。
- Runtime 探测、spec/facts 派生或询问用户；不得静默猜值。

### 5.5 Git、发布与署名

- 不 push、不 merge，除非用户明示。
- 对非本用户仓的 issue、PR、comment 必须先获同意。
- Commit 可按检查点进行；人类署名使用 `lys` / `lllyys`。
- Commit、PR body、报告不得带 AI 署名、trailer 或生成标识；不重写历史遗留 trailer。

### 5.6 文档与 TODO

- 开发过程文档统一放 `dev-doc/`，不写到工作区上层 `markdown/`。
- 每次落地在 `dev-doc/oprunway-changes-brief.md` 顶部追加简短倒序摘要。
- 当前待办唯一入口为 `dev-doc/oprunway-todo.md`；完成项从 TODO 删除，历史查 changes brief 与 Git。
- 主 session 不维护常驻 handoff；只有确实切换 session 或移交执行者时才临时生成。

### 5.7 Push 前审修

- Push 前对自上次 push 以来的全部代码统一走一轮 audit → fix → verify；一轮即停。
- 散文/仓规走独立散文审；verify 剩余 finding 如实报告，不无限迭代。
- `nlpm` 等 lint 不能替代代码审修门。

### 5.8 事实与裁决

- 数字、错误、耗时必须来自真实日志或产物；推断明确标注。
- “代码接通”“有测试”“covered”“collector 有数据”“gate passed”均不等于算子通过。
- FAIL 归因前先复核输入摘要及源码 → build → ELF →加载对象绑定，再解耦 DUT 与 harness。
- 最终裁决只认确定性脚本链；任务书仍是验收权威。

### 5.9 Canon

- Durable 知识遵循 capture → compile → review；不手改 cabinet 页，不自行升 canonical。
- 只有 canonical 可作已定事实；proposed、verified、stale、contested 按 trust tier 对待。
- 开始 durable 设计、组件建设、bureau 写入或 FAIL 归因前，先读 `canon/architecture/`、
  `canon/decisions/` 与 `canon/lint/findings.md`，并按需 query。
- 当前仓规与未 settle canon 冲突时显式记录张力，以本文件的现行执行规则为准。

## 6 · 验收口径

### 6.1 性能：指定场景只做 NPU msprof

- 无性能要求、要求 GPU 比对、新增 dtype/shape/rank/新算子，或任务书明确属于内存优化时，只做
  NPU msprof kernel-only 实测。资源条款仍按 §6.3 处理，不得据此宣称内存达标。
- GPU 性能数据不属于本 workflow 的输入或产物；不建立 Task 3，不因缺 GPU 数据阻塞验收。
- 不属于上述场景时，不得自动套用 measure-only；性能取证与裁决逐字按任务书及正式 spec 执行。
- Spec 必须用 `perf.measure_only_authorization` 记录受控 ground、cite、quote 和任务书摘要，缺一 fail-closed。
- 任务书中的比值、绝对门限或吞吐条款若未实测，必须进入 `task_pr_gaps` 标 `UNVALIDATED`；不得因有
  NPU 绝对耗时而宣称条款达标。
- msprof 必须真机实跑，并与 case、device、CANN、DUT ELF、采样和 timing scope 绑定；不得推算。

### 6.2 精度：GPU 真值写法解析为同族 CPU

- 任务书指定具体 GPU 库作为精度真值时，解析为同一库族的 CPU 实现，并在报告保留原文与解析记录。
- 这是口径解析，不产生“GPU 真值未验收”gap；但阈值若来自不同实现，须标来源差异并由人确认。
- 映射按库能力数据驱动，禁止按算子名分支；粗粒度 `gpu_lib` 或无 CPU 对应时 fail-closed，询问用户。
- 解析后的 `method_kind` 必须属于 `precision_policy.RUNNABLE_METHOD_KINDS`。
- Workflow 不运行 GPU；这里仅解析任务书口径并执行 CPU golden 与 NPU DUT 的精度验收。

### 6.3 验收维度只有精度与性能

- 内存、显存、workspace、带宽等资源指标不构成第三个验收维度。
- 报告应说明未做资源评估，但不得宣称资源条款达标。
- 性能条款属于验收轴，未取证须结构化挂账；资源条款不进入精度/性能裁决。

## 7 · Caller-trusted 来源与内容锚

Fresh facts 必须包含：

```json
{
  "input_association": {
    "schema": "oprunway.caller_trusted_input",
    "schema_version": 1,
    "policy": "caller_trusted_pair_v1",
    "correspondence": "asserted_by_caller"
  },
  "pr": {
    "content_anchor": {
      "schema": "oprunway.source_content_anchor",
      "schema_version": 1,
      "algorithm": "git_blob_manifest_sha256_v1",
      "scope": "<目标子树>",
      "sha256": "<64 hex>",
      "file_count": 1
    }
  }
}
```

规则：

- URL、repository、fork、ref、head、MR 编号和 provenance kind 仅为 transport observation。
- 在线与本地输入必须物化完整目标内容；目标范围任一文件读失败、缺失、软链或无法产锚均阻断。
- `source_facts.pr.content_anchor`、`pr_facts.content_anchor`、build 前实际树重算 anchor 必须逐字一致。
- Vendor receipt current schema 为 v3；source anchor 与 `build.source_snapshot_digest.content_anchor` 必须一致。
- Build 后仍须复核目标 scope，绑定实际改写 ELF、加载路径、双符号 owner、调用和输出写入。
- 任何新 marker 出现但结构不完整时必须拒绝，不能回落 legacy。
- Legacy facts/receipt 不得被兼容层合成 caller assertion；只允许显式 historical-read-only，不能产 current PASS。
- `--pr` 与 `--pr-snapshot` 互斥；`--target-dir` 两种取材方式共用。Locator 改变但内容相同不得改变裁决。

## 8 · 外部仓与依赖

- 外部源码、任务书和样例保持 ignored，不成为运行时依赖。
- 可参考外部 case/精度/性能方法，但正式 caseset、golden 和裁决必须由本轮受信内容链生成。
- 不同仓形态通过稳定能力或 per-repo adapter 接入，不互相硬套。
- “没跑崩”不能替代精度/性能验收。

## 9 · 目录与入口

```text
OpRunway/
├── AGENTS.md                    # 唯一仓规
├── plugin/
│   ├── acc-common/              # 契约、确定性引擎、门与测试
│   ├── agents/                  # Layer 2 agent
│   ├── skills/                  # Layer 2 skill
│   ├── commands/                # 命令入口
│   └── samples/                 # 跟插件分发的样例
├── dev-doc/
│   ├── oprunway-todo.md         # 当前活 backlog
│   └── oprunway-changes-brief.md# 历史流水
├── canon/                       # durable knowledge，受 review 门控制
├── reports/                     # ignored 验收产物
└── .oprunway/                   # ignored 机器本地配置
```

常用入口：

| 目标 | 入口 |
|---|---|
| Workflow 状态机 | `plugin/AGENTS.md` + `plugin/skills/acceptance-workflow/SKILL.md` |
| 当前 TODO | `dev-doc/oprunway-todo.md` |
| 改动流水 | `dev-doc/oprunway-changes-brief.md` |
| 真机环境 | `dev-doc/oprunway-real-machine-environment.md` |
| Canon 状态 | `canon/decisions/` + `canon/lint/findings.md` |

## 10 · 当前诚实边界

- Fresh caller-trusted 产品相关测试与 RGE 定向测试已通过；legacy fixtures 仍需迁移，完成前不得宣称全仓回归全绿。
- A2/A5 等未测硬件与未比较性能条款只作 `UNVALIDATED` 范围限制，不得外推为已覆盖或已达标。
- 运行能力、验收准入和历史产物是三件事；任何历史结果不得绕过 current facts/receipt/gate 生成新裁决。
