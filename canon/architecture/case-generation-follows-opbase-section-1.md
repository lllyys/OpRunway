---
title: Case generation follows opbase ecosystem precision standard section 1
updated: 2026-07-16
status: proposed
---

# Case generation follows opbase ecosystem precision standard section 1

浮点计算类算子的**精度用例生成规则**取自 `cann/opbase` `docs/zh/ops_precision_standard/experimental_standard.md`
**§1「用例生成规则」**（pin commit `f69d4e4e3f2626ddd37855a8d05063a1764ac4c9`），用户 2026-07-15 指定为权威。
整型/搬运类算子另定，不套本页。

**§1 采纳、§2 不采纳（分工明确）.** §1 只定**怎么生成用例**：dtype × 格式 × 维度 × attr 正交覆盖 + §1.4 特殊场景
（空 Tensor / 标量 / 边界 / inf·nan）+ 值域 50% 均匀 + 50% 正态 + 维度取 2ᵏ / 2ᵏ−1。§2 的**误差指标（MERE/MARE）
不用**——阈值口径仍走 AscendOpTest（见 [[AscendOpTest precision thresholds]]、现有 `precision_policy` 快照零改）。
§0 印证 bool/符号类逐位精确。与 [[Ecosystem precision standard MERE MARE]] 是**同一份 opbase 文档的不同节**：那页记
§2 的 MERE/MARE 口径、本页记 §1 的生成规则。本页是 [[Golden and precision standard come only from the task-doc-specified method]] 的**生成侧**具体化——任务书指定的测试方法（opbase §1）定生成规则、AscendOpTest 定阈值。

**数量以用户为准、默认 50、运行时问用户.** `spec.precision.case_target` 承载数量；acc-spec 用 `AskUserQuestion` 问
（先 `gen_cases --dry-run` 拿 `[强制下限 S, pool_max]` 区间呈现）。用户明示「数量以我为准」——**覆盖 §1.1「不设固定
下限、覆盖优先、非机械凑数」**。50 封顶下 §1.1 的「100% 正交联合」**不可达**，故采「**白名单强制必覆盖组合**（key
dtype × attr × 大 shape、§1.4 特殊场景）+ 常规联合 **1-wise 边际采样**」，并导出**覆盖账本**
（`coverage_strength` / `dropped_combo_classes`），把「覆盖到什么程度、丢了哪些组合类」显式记账、不假装全覆盖。

**Sources.** [[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：opbase §1 生成规则 + case_target 默认 50/问用户 + 覆盖账本）
