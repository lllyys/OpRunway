---
title: Acceptance contract and evidence chain
updated: 2026-07-02
status: canonical
reviewed: 2026-07-02
---

# Acceptance contract and evidence chain

**第一原则：契约先行。** 验收最大隐患是「口径未契约化 → 看似自动化、实则不可比不可复核」。核心是一条 `case_id` 贯穿 **任务书条款 → PR 改动 → NPU 跑测 → GPU 标杆(Task 3) → 最终判定**。

统一数据契约（由 acc-common 单一定义，三 skill 共用，防字段漂移）每条用例至少含：`id`(主键)、`kind`、`case_origin`(spec_clause/pr_change/boundary/regression)、`spec_clause_ref`、`pr_change_ref`、`applicability`、`dtype`、`shape`(留 layout/stride/epilogue/quant 等扩展位)、`oracle_source`(analytical_ref/cpu_ref/torch_ref/catlass_existing_ref/task_spec_expected/external_ref)、`tolerance_policy_id`、`timing_policy_id`、`perf_baseline_source`(gpu_external/npu_torch_unfused_chain/task_spec_target/npu_existing_op；**默认 gpu_external，即 GPU 标杆，经 Task 3 对比**)、`expect`。精度判据见 [[ADR 0005 — Precision acceptance is a three-layer policy]]，性能口径见 [[ADR 0006 — Compare performance at a matched timing scope]]，判定由 validator 算（[[ADR 0007 — Verdicts come from a deterministic validator]]）。

Task 1 须含 **PR 影响面分析**，把改动映射到用例，否则证明不了验收覆盖了 PR。**性能验收基线（1.2× 分母）由 `perf_baseline_source` 定、默认 = `gpu_external`（GPU 标杆，经 Task 3 的 NPU↔GPU 对比，口径见 [[ADR 0006 — Compare performance at a matched timing scope]]）**；备选 = NPU torch 未融合链（NPU 侧自测）/ 任务书目标 / 现成 NPU 算子。**GPU 只作性能标杆、非精度 oracle**（精度不拿 GPU 作 oracle）。故性能验收默认有 GPU 参与、换基线时未必。执行后端见 [[Repo adapter interface and modes]]。是 [[OpRunway acceptance pipeline]] 的脊柱定义。字段随首份真实任务书校准，勿空想镀金。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]，[[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（perf_baseline_source 默认=gpu_external、torch 链备选）
