---
title: OpRunway component breakdown
updated: 2026-07-02
status: canonical
reviewed: 2026-07-02
---

# OpRunway component breakdown

OpRunway 实现（草案）拆为：**1 个顶层编排入口**（`op-acceptance`，串 Task 1→2→3、按算子/用例 fan-out、卡检查点）——⚠ 插件无 workflow 制品类型，故它**落成一个 orchestrator command/skill**，Workflow 工具仅作内部可选加速器、不随 plugin 分发（见 [[ADR 0004 — Orchestrate like cannbot ops-registry-invoke]]）+ **1 个共享组件 `acc-common`**（统一 JSON schema + 数据模型 + policy + **deterministic validator 判定内核**，即 [[Acceptance contract and evidence chain]] 的单一定义，防三 skill 字段漂移；**只放 schema/模型/policy/判定内核，不放 runner/日志采集/框架解析**，免得 common 反向依赖 Task2）+ **3 个 skill**（`acc-casegen` 用例生成、`acc-npu-run` NPU 跑测、`acc-perf-compare` 性能对比）+ **若干子 agent**（任务书解析、跑测、独立评测——三角色够用，别过早多拆）。`acc-npu-run` **内部按能力分层**：「仓适配器」只**执行 + 产 evidence**（见 [[Repo adapter interface and modes]]，含桥 route A/B + generated_harness + optest 采集）；**判定唯一归 [[ADR 0007 — Verdicts come from a deterministic validator]] 的 deterministic validator（在 `acc-common`）、`acc-npu-run` 内不另立判定语义**（runner 产 evidence、validator 读 evidence 出 verdict）。分工准则：skill 装可复用的「怎么做」与脚本（也是可发布的 IP），子 agent 装需隔离上下文 / 并行 / 独立角色的活；真正要拆的是确定性脚本和 schema，不是角色数量。`acc-casegen` 的核心知识是 [[Primitive-to-case rule library]]；`acc-npu-run` 仓适配器的 generated_harness 须履行 [[generated_harness responsibilities]]。整套「一个泛化验收流程（workflow 剧本语义，非插件 Workflow 制品）+ 每仓薄适配器」的架构决策见 [[ADR 0009 — One generalized workflow with per-repo adapters]]。⚠ **未来拆点**：`acc-npu-run` 的仓适配器/harness 一旦从「catlass 实现细节」变成「≥2 仓/框架共享的执行基建」（信号任一：新仓可原样复用 harness 4 职责 ≥2 项、同仓需接第 2 个测试框架、adapter/harness 与 validator 出现两种 release 节奏、bridge 被 Task1/3 直调、repo 条件分支堆进核心 runner）再毕业成独立模块；是否升为独立 skill 视是否需被用户或编排入口单独调用。

建立在 [[OpRunway acceptance pipeline]] 之上；编排照 [[cannbot ops-registry-invoke workflow]]，理由见 [[ADR 0004 — Orchestrate like cannbot ops-registry-invoke]]；发布形态见 [[ADR 0003 — Publish as self-maintained plugin and sync skills to awesome-ascend-skills]]。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]，[[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（evidence/verdict 分离、acc-common 分层、编排落成 command/skill、未来拆点）
