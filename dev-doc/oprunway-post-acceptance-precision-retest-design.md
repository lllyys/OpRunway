# 验收后人工精度复核与重测设计

> 状态：设计已批准，基础契约、Task-2-only 执行、确定性裁决与追加报告正在实施；尚未完成真实
> NPU 重测见证。
> 日期：2026-07-29。
> 范围：首次 CP-A..E 验收结束后，由人工发起的精度重测；不改变首次验收事实，不重跑性能。

## 1 · 背景与目标

首次验收结束后，人工会审视精度失败或 `needs_review` 的 case，并可能：

1. 要求使用原精度标准重新执行；
2. 给出具体的新精度标准，要求使用原用例重新执行；
3. 根据重测证据作最终人工处置。

新增环节暂称 **CP-F：验收后人工精度复核与重测**。它是首次验收之后的追加流程，不是对
CP-D 的覆盖执行，也不把首次 `FAIL` 改写为首次 `PASS`。

设计目标：

- 首次 `spec`、`caseset`、证据、裁决和报告永久保留；
- 重测只能使用首次验收中的原 case 和原输入；
- 人工新标准有明确授权、作用域和可复核 provenance；
- 重测仍由现有确定性脚本链裁决，agent 不自行判断 pass/fail；
- 支持多轮、断点恢复、并发防冲突和幂等执行；
- 不重新生成用例、不重跑性能、不复制第二套裁决逻辑。

## 2 · 核心边界

### 2.1 首次验收始终是基础事实

以下首次验收产物不得被 CP-F 覆盖：

- `<op>.spec.json`；
- `caseset.json`；
- `evidence.json`；
- `verdict.json`；
- `perf_report.json`；
- `acceptance.json`；
- 首次中文验收报告。

`acceptance.json` 始终表示首次正式验收结果。CP-F 只生成追加 attempt 产物，并在报告中并列展示
基础裁决与重测裁决。

### 2.2 重测不等于重判

受控动作分为：

- `same_policy_rerun`：原 case、原输入、原标准，重新执行 DUT；
- `relaxed_rerun`：原 case、原输入、人工确认的新标准，重新执行 DUT；
- `replay_only`：仅以完全相同的 `policy_id` 和 `threshold_digest` 重放确定性裁决，用于 validator
  修复或裁决复核，不得改变标准，也不得称为“重测”。

人工要求“重测”时，不能用 `replay_only` 替代。放宽标准时必须执行真机重测。

### 2.3 放宽标准不能形成第二真源

人工标准不能直接修改原 spec，也不能以脱离 spec 的 shadow contract 驱动 validator。

`relaxed_rerun` 应生成完整的 `spec.relaxed.json`：

- 绑定原 spec 的 SHA-256；
- 绑定人工 directive；
- 记录受控 `override_diff`；
- 记录自身 SHA-256；
- 除白名单内精度容差字段外，与原 spec 保持一致。

允许 override 的字段只限精度容差策略。以下内容永远不能通过人工精度 directive 放宽：

- case 身份、shape、dtype、attrs 和输入内容；
- dtype/API/任务书覆盖要求；
- oracle 与 golden 来源；
- 输出形状和 ABI；
- PR head、DUT、runner、build receipt 和实际加载 ELF；
- toolkit、SoC 和 harness trust；
- 证据完整性门及 provenance 门。

## 3 · 状态模型

不新增混合含义的顶级 `RETEST_*` verdict。CP-F 使用正交状态维度，避免污染现有 verdict、exit code
和 CP-E 的逐字引用语义：

```text
directive_status:
  drafted → confirmed → expired | revoked

attempt_kind:
  same_policy_rerun | relaxed_rerun | replay_only

drift_status:
  ok | drift_blocked:<field>

gate_status:
  not_run → passed | failed

verdict:
  复用现有确定性 validator 的受控枚举

disposition:
  none | accepted_relaxed | rejected |
  escalated | needs_more_evidence
```

确定性脚本负责重测证据的 verdict；人工负责 disposition。`relaxed_rerun` 即使按新标准通过，也必须：

- 标记 `policy_source=relaxed:<directive_id>`；
- 标记 `requires_human_cp=true`；
- 在报告中展示 `PASSED_UNDER_RELAXED_POLICY`；
- 不覆盖基础 `acceptance.json`。

## 4 · 工件与目录

建议使用 `attempts/`，避免与 bureau 的 `review` 概念混淆：

```text
reports/<op>/
├── acceptance.json
├── verdict.json
├── evidence.json
├── perf_report.json
└── attempts/
    ├── index.jsonl
    ├── latest.pointer
    └── 0001/
        ├── directive.json
        ├── attempt.manifest.json
        ├── spec.relaxed.json
        ├── attempt_evidence.json
        ├── attempt_verdict.json
        ├── retest_acceptance.json
        ├── attempt.receipt.json
        ├── dispositions.jsonl
        └── 精度重测报告.md
```

`spec.relaxed.json` 只在 `relaxed_rerun` 中存在。`dispositions.jsonl` 只追加人工处置，不改写确定性
结果。

### 4.1 `directive.json`

至少包含：

- `directive_id`，同时作为幂等键；
- `attempt_kind`；
- 显式 `case_ids`；
- 用户原始指令、结构化解释和确认记录；
- 原 spec、caseset、evidence、verdict、acceptance 的 SHA-256；
- PR head、build receipt、runner form；
- 新标准及其 case/dtype/output 作用域；
- directive 自身 SHA-256。

directive 只是本轮 attempt 的人工授权，不自动进入 canon。durable 知识仍走
capture → compile → review。

### 4.2 `attempt.manifest.json`

准备门生成的冻结清单至少包含：

- 计划执行的 case ID；
- 每个 case 的 shape、dtype、attrs；
- 每个输入文件的 SHA-256；
- PR head、build receipt、vendor ELF；
- runner form、toolkit、SoC；
- golden 来源、实现和产物 hash；
- `aclnn_py` 的 harness trust receipt 及其绑定 hash；
- 本轮使用的原 spec 或 relaxed spec hash。

### 4.3 `attempt.receipt.json`

完成收据至少包含：

- 实际执行的 case ID；
- evidence、verdict、retest result 的 SHA-256；
- gate 结果及错误；
- lifecycle 与完成时间；
- 本轮是否完整完成。

只有原子写成的完成 receipt 才表示 attempt 可被索引为完成状态。

## 5 · CP-F 状态机

### F0 · 人工发起

- 基础 `acceptance.json` 必须存在；
- 用户指出待复核 case 或允许从原 verdict 的失败/待复核项中确定性选择；
- agent 将自然语言整理成 directive 草案，不执行真机。

### F1 · 指令确认

- 校验 case ID 均来自原 caseset；
- 校验基础产物和 PR/build 身份指纹齐全；
- 显示标准、作用域和标准差异；
- 人工确认后冻结 directive；
- 含糊、冲突、未知 policy 或越过 override 白名单时 fail-closed。

### F2 · 重测准备门

逐项复核：

- 原 spec、caseset、evidence、verdict、acceptance；
- case shape、dtype、attrs 和输入字节；
- PR head、build receipt、vendor ELF；
- runner、toolkit、SoC；
- golden 来源、实现及产物；
- `aclnn_py` harness trust。

每种漂移使用独立原因码。任何漂移都不得被新精度标准豁免。

### F3 · 真机精度重测

- 仍须遵守远程 NPU 环境和副作用确认规则；
- 只执行 directive 指定的原 case；
- `same_policy_rerun` 使用原 spec；
- `relaxed_rerun` 使用完整 `spec.relaxed.json`；
- 不调用性能 collector 或 `perf_compare.py`；
- 不写首次验收目录的同名裁决产物。

### F4 · 确定性裁决与证据门

- 复用现有 `validator.py` 和精度 policy；
- 门的覆盖分母为本轮 `attempt.manifest.planned_case_ids`；
- directive、spec、case、输入、evidence 和 provenance 必须闭环；
- 任一门失败均不得生成通过结果；
- CP-F 薄壳不得重新实现 pass/fail、state map 或 exit code 推导。

### F5 · 追加报告与人工处置

顺序为：

1. 原子写完成 receipt；
2. 追加 attempt index；
3. 原子更新 `latest.pointer`；
4. 生成精度重测报告；
5. 等待并追加人工 disposition。

`latest.pointer` 只指向最新完成的 attempt，不代表替换基础验收权威。

## 6 · 性能结论

CP-F 不重跑性能，也不改变首次性能裁决。重测产物必须记录：

```text
perf_source=inherited_from_base
```

报告须说明：

- 本轮没有重新采集性能；
- 首次 `perf_report.json` 保持不变；
- 基础验收若因缺 GPU benchmark、性能门失败或其它原因处于 BLOCKED，精度重测不能改变该状态；
- 重测后的 case 不自动进入或退出首次性能集合。

## 7 · 脚本边界

### 7.1 必须复用

- `validator.py`；
- `precision_policy.py`；
- `validate_acceptance_state.py` 中可抽取的精度门；
- `verify_aclnn_harness.py`；
- `finalize_clean_acceptance.py` 的 gate 与原子写机制。

### 7.2 必要重构

1. 从 `run_workflow.py` 抽取可独立调用的 Task-2-only 执行入口，保持原工作流行为不变；
2. 参数化 finalize，使基础验收和 attempt 复用相同确定性逻辑，但写不同产物名；
3. 将 Task 2 门参数化为“完整基础 caseset”或“已冻结 attempt manifest”两种受控 scope；
4. 禁止复制既有聚合、状态映射或裁决代码。

### 7.3 新增薄壳

候选脚本：

- `cp_f_draft_directive.py`；
- `cp_f_confirm_directive.py`；
- `cp_f_preflight_attempt.py`；
- `cp_f_execute_attempt.py`；
- `cp_f_finalize_attempt.py`；
- `cp_f_append_report.py`；
- attempt 分配、锁、索引和原子指针工具。

## 8 · 并发、幂等与恢复

- `directive_id` 是提交和重试的幂等键；
- attempt 目录用原子创建方式占号；
- 并发分配和索引追加须加锁；
- 所有裁决和 receipt 使用临时文件 + `fsync` + 原子替换；
- 没有完成 receipt 的 attempt 是未完成现场，不能进入 `latest.pointer`；
- 同一 directive 重试时复用或恢复原 attempt，不另造重复裁决；
- 中断后根据 manifest、checkpoint 和 receipt 从缺口恢复，不覆盖已完成 attempt。

## 9 · 分阶段实现

### 阶段一 · 重测基础设施

- 抽取 Task-2-only；
- 参数化精度门与 finalize；
- 实现 append-only attempt、输入字节绑定、漂移门；
- 实现并发、幂等和中断恢复；
- 接通 `same_policy_rerun`；
- 支持 `cpp` 和 `aclnn_py`；
- 未接通 form 显式 fail-closed，不静默降级。

### 阶段二 · 当前业务所需的放宽标准重测

- 定义精度 override 白名单；
- 定义 `spec.relaxed.json` schema 和确定性派生器；
- 接通 `relaxed_rerun`；
- 并列展示原标准、平台标准和人工标准；
- 强制人工 disposition；
- 为该治理变化单独形成 ADR 草案并走 review。

### 阶段三 · 可选 replay

仅在出现真实的 validator 修复重放需求时评估 `replay_only`。它不得改变 policy 或 threshold，
不得冒充 NPU 重测。

## 10 · 最少测试矩阵

至少覆盖：

- `cpp`、`aclnn_py` 的 same-policy PASS/FAIL；
- 未支持 runner form 显式拒绝；
- 任一 gate 缺证据时不得通过；
- PR head、ELF、receipt、input bytes、golden、toolkit、SoC、harness trust 分别漂移；
- 基础 acceptance/evidence 被篡改；
- 非原 case、修改 shape/dtype/attrs/input 被拒；
- directive 含糊、越权字段、未知 policy、非法 NaN/Inf/负阈值；
- F3 中断、缺 receipt 的半态恢复；
- index 半写和 latest pointer 恢复；
- 同 directive 重复提交幂等；
- 两个 session 并发分配 attempt；
- relaxed spec 只允许白名单 diff；
- relaxed pass 强制 `requires_human_cp`；
- replay policy ID 或 threshold digest 改变时拒绝；
- 基础性能文件字节不变且只作继承展示；
- 基础验收处于性能 BLOCKED 时不被精度 attempt 改写；
- 报告始终以基础 acceptance 为主结论。

## 11 · 尚待实现前确认

- 精度 override 的精确白名单和允许的 policy 类型；
- 是否需要限制最大放宽幅度；
- directive 的有效期和撤销规则；
- 历史 caseset 是否已完整保留输入字节 hash；
- 历史验收是否已完整绑定 PR/build/ELF provenance；
- `cpp_extension` 接入 CP-F 的时机；
- 人工 disposition 的批准者身份字段；
- attempt index 采用 JSONL 还是其它可原子追加的索引形式。

这些事项不影响记录总体设计，但进入实现前须明确；三源缺失或冲突时继续 fail-closed，不静默猜测。

## 12 · Claude 独立审查记录

本设计吸收了 Claude Code 的只读高强度架构审查。审查总体结论为 **Go with changes**，主要修订是：

- 不建立脱离 spec 的第二判据真源；
- 状态拆成正交维度；
- 补齐输入字节与执行 provenance 漂移门；
- 补齐并发、幂等、半写恢复和完成 receipt；
- CP-F 复用现有确定性裁决链，不复制 `run_workflow`；
- 基础设施先行，随后接通当前明确需要的 `relaxed_rerun`。

Claude MCP session：`8f1838e6-572b-4a57-9fb1-546d70d8a765`。

## 13 · 实施状态

截至 2026-07-30，分支 `feature/cp-f-precision-retest` 已落：

- directive、relaxed spec、manifest 与 receipt 的严格契约；
- 首次五类验收产物和原 case/input bytes 的漂移门；
- `cp_f_prepare_attempt.py`；
- `cp_f_execute_attempt.py` 的 Task-2-only 路径；
- `same_policy_rerun` 与 `relaxed_rerun` 的 canonical acceptance 重绑定；
- attempt 内 validator、Task 2 门、追加结果与中文报告；
- `aclnn_py` 首次 evidence 的 PR/build/SoC/toolkit/vendor ELF/golden source provenance 补链。
- `cpp_extension` Task-2-only：复用正式 codegen/adapter/driver，fresh build/load/invoke，但不生成或执行性能计划；
- `cpp_extension` 首次 invocation plan、build/load/vendor receipt 与本轮 fresh receipt 双向绑定；
- 每个 attempt 同时冻结 input 与 golden bytes，任何漂移 fail-closed。
- directive allocation 与 execute 分别使用独占锁；同 directive 同 manifest 幂等返回原 prepared attempt，
  异内容拒绝；报告成功生成后才提交最终 receipt。
- relaxed override 禁止仅 standard/no-op；跨 family 必须显式 standard 与目标 family 的完整数值字段，
  同 family 允许人工明确的收紧或放宽，不强制单调放宽。
- 锁恢复不按 mtime 猜测：专用恢复函数须验证受控目录、owner PID 已退出、operation/digest 一致且无最终
  receipt，再把锁原子标为 abandoned 后释放。跨 family 属人工完整 policy replacement，不表述为单纯放宽。
- F3 CLI 必须显式接收可信 `attempts_root`，attempt 只能是其直接四位子目录；先查入口 symlink、receipt 和
  lock，再读 manifest 对账，禁止信任 manifest 自举根。锁 owner 以 fsync 后 hard-link O_EXCL 单次发布。

远程非保护临时目录的最新精准回归为：

```text
161 passed, 14 subtests passed
```

最新真机见证（2026-08-02）：

- v10 已完成 1152-case `cpp_extension` CP-F 机械闭环：F2=0、F3/F4=0、Task-2 gate
  通过，七类必需产物与 final receipt 齐全，性能未重测，基础 acceptance 未改写。
- 本轮冻结输入缺任务书 snapshot，确定性裁决为 `blocked_golden_unauthorized`；因此已证明
  重测机械链路可闭环，尚未证明 relaxed policy 对最终精度裁决的实际影响。

尚未完成：

- 补齐 golden 授权文件闭包后，使用真实失败 case 证明 relaxed policy 裁决生效；
- attempt 并发 index/latest pointer 与人工 disposition CLI；
- 全量远程回归和发布前审修门。
