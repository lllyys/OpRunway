---
title: Golden and precision standard come only from the task-doc-specified method
updated: 2026-07-15
status: proposed
---

# Golden and precision standard come only from the task-doc-specified method

**用户 2026-07-13/14 定为最高律令**：验收里的**精度标准**（阈值 / oracle / 比对口径）与 **golden（标准答案）的口径**，只能来自**任务书指定的测试方式/方法**——**不是**我们照理解自撰的 numpy（除非任务书明确要求写此参考）。任务书要求的标准/方法若**不在我们当前支持范围 → fail-closed 抛用户**、绝不静默降级。

这是 [[Task spec is authoritative over PR]]（绝不信 PR）在**精度维**的延伸：被测物（PR 代码）是受审对象，验收尺子与真值口径恒来自任务书。golden 值源于「任务书指定的测试方法测出来的值」；"照算子语义随手写 numpy 当 golden" 属未经任务书授权的自行填空 = 违规（除非任务书点名该 numpy 参考）。

**落地（本会话 Q9/Q7）**：
- `precision_policy.select_standard` 白名单 fail-closed——未知 oracle（torch/scipy/std_exact 等·任务书没指定、我们没实现的）→ raise 抛用户，不再静默套 AscendOpTest 阈值（堵 class C「与 python 一致」的静默降级）。
- golden 值来源明确记录（见 [[oracle_source is a hardcoded constant not a recorded fact]] 的止血 + [[Golden is fixed to torch on CPU for determinism]]）。
- 强化 [[Verification code provenance for runner and golden]]。

⚠ **关键澄清**（见 [[AscendOpTest provides no golden source only a comparison harness]]）：任务书写「满足 AscendOpTest 默认阈值」只指定了**比对方法+阈值**、并**不指定 golden 值源**——golden 源须从任务书别处（任务概述/参考实现字段）取。

**tier 说明**：留 `proposed`，待 `bureau:review`。

**Sources.** [[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]
