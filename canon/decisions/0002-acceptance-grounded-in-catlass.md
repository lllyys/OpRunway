---
title: ADR 0002 — Acceptance grounded in catlass and the spec
updated: 2026-06-30
status: canonical
reviewed: 2026-06-30
---

# ADR 0002 — Acceptance grounded in catlass and the spec

**Context.** 姊妹项目 cann-ops-test 的 `ops-test` 用「示例跑没跑崩」的 4 层判定（退出码 / 强失败 / 强成功 / 待复核）。那不是数值精度对比，也不是性能度量，用来做验收会跑偏。

**Decision.** OpRunway 的精度/性能验收**以「任务书 + 统一验收策略」为上层规范，catlass 自身机制只作首仓的执行后端**（见 [[catlass acceptance mechanics]]：CPU golden、msTuner），**不基于 `ops-test` 的「跑没跑崩」判定**。即：catlass 机制是仓适配器默认实现，别误升为所有仓的总规范（泛化到 ops-blas/ops-cv/tilelang 时 golden/构建/性能入口都变，见 [[Repo adapter interface and modes]]）。方法论借 cannbot 的 `ops-precision-standard` / `ops-profiling`，只借不依赖。

**Consequences.** 精度口径已细化为三层（见 [[ADR 0005 — Precision acceptance is a three-layer policy]]）；性能口径默认 kernel-only、须同 timing_scope（见 [[ADR 0006 — Compare performance at a matched timing scope]]）。`ops-test` 可保留为 Task 2 前置 smoke gate（字段 `smoke_only: true`），非验收证据。这是 [[OpRunway acceptance pipeline]] 的判定基线。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]
