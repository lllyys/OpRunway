---
title: ADR 0009 — One generalized workflow with per-repo adapters
updated: 2026-07-02
status: canonical
reviewed: 2026-07-02
---

# ADR 0009 — One generalized workflow with per-repo adapters

**Context.** OpRunway 要泛化到 11+ 个算子仓（catlass/ops-blas/ops-cv/tilelang…）。选型：**每仓一个工作流**，还是**一个泛化工作流跨仓共享**？

**Decision.** **一个泛化的 `op-acceptance` workflow + 共享 acc-casegen 规则库（[[Primitive-to-case rule library]]）+ 共享验收 policy（精度三层/timing_scope/validator/状态机）+ 共享 acc-common schema；每仓（×每框架）只写一个薄 repo-adapter / generated_harness，实现同一接口（[[Repo adapter interface and modes]] 的 7 方法 + [[generated_harness responsibilities]] 的 4 职责）；repo 差异是数据（repo profile：build 命令/arch/kernel 符号/框架绑定）。绝不为任何仓 fork 工作流。**

**Consequences.**
- **唯一该变的是「怎么在这仓 × 这框架上把用例跑起来」**；「测什么 + 怎么判 + 怎么编排」全共享。变化沿两条轴——**仓轴**（算子怎么表达/build：catlass 模板走 generated_harness、ops-nn 自包含 example、tilelang Python DSL）+ **框架轴**（AscendOpTest vs 其它测试框架）——都被 adapter/harness 吸收，workflow 坐其上。**adapter 是唯一接缝。**
- 避免每仓复制带来的冗余与**漂移**；保证**验收语义全仓一致**（否则「通过」含义不同，验收失效）；演进（加规则/CP/字段）**改一次全生效**。
- 与 [[ADR 0004 — Orchestrate like cannbot ops-registry-invoke]] 一致；cannbot 同款（ops-direct-invoke 底座 + catlass-op-generator 薄特化，唯一差异只 CMake+op_kernel）。
- **开发策略**：「先 catlass 打底」= 写第一个 adapter，非「catlass 工作流」；泛化 = 再写一个 adapter，workflow 一行不动（如 CI：一套流水线 + 每项目一份配置）。塑造 [[OpRunway component breakdown]]。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]
