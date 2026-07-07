---
title: ADR 0007 — Verdicts come from a deterministic validator
updated: 2026-06-30
status: canonical
reviewed: 2026-07-02
---

# ADR 0007 — Verdicts come from a deterministic validator

**Context.** ADR 0004 选了 cannbot 式「AI 编排 + JSON 证据」。但若让 agent 直接宣告 PASS/FAIL，判定就回到主观，验收不可复核。

**Decision.** 所有 PASS/FAIL **由确定性校验器（validator）依据统一 JSON schema + 原始日志 hash 计算**；**agent 只生成解释和建议，不能直接宣告通过**（AI 当发现器、脚本兜底）。validator 与 schema 是 [[Acceptance contract and evidence chain]] 的一部分，由 acc-common 提供。

**Consequences.** 强化 [[ADR 0004 — Orchestrate like cannbot ops-registry-invoke]] 的硬约束；判定结果落成 [[Task 3 acceptance state machine]] 的状态。是 [[OpRunway component breakdown]] 中 acc-common / 验收判定器的设计依据。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]
