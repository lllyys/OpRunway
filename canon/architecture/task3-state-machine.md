---
title: Task 3 acceptance state machine
updated: 2026-07-02
status: canonical
reviewed: 2026-07-02
---

# Task 3 acceptance state machine

验收的最终/Task 3 结论用**状态机**表达，不只是流程描述，由 validator 计算（[[ADR 0007 — Verdicts come from a deterministic validator]]）。**核心状态**（4 个）：

- `PASSED` — 精度与性能均达标；
- `FAILED_PRECISION` — acceptance 精度不过；
- `FAILED_PERFORMANCE` — 性能不达标；
- `BLOCKED_WAIT_GPU_BENCHMARK` — 缺外部 GPU 标杆，挂起。

**扩展/边角状态（按需、非核心）**：`PASSED_WITH_RISK`（放行但有风险，如任务书目标宽于平台底线，见 [[ADR 0005 — Precision acceptance is a three-layer policy]]，需人工 CP 记录）；`BLOCKED_INCOMPARABLE_TIMING_SCOPE`（NPU/GPU timing_scope 不一致、不可比，见 [[ADR 0006 — Compare performance at a matched timing scope]]）。

是 [[OpRunway acceptance pipeline]] Task 3 的结论模型。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]，[[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（4 核心状态 + PASSED_WITH_RISK/BLOCKED_INCOMPARABLE_TIMING_SCOPE 弱化为边角）
