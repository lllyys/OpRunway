---
title: Community taskdoc precision requirements fall into four classes
updated: 2026-07-10
status: proposed
---

# Community taskdoc precision requirements fall into four classes

全扫 52 份社区任务书（`repos/cann-ops-competitions`，2026-04/05/07）的「精度要求」小节原文，
按主标准归并为四类：

- **A · AscendOpTest 默认阈值**（43 份）→ standard `ascendoptest_default`（单标杆 + 绝对阈值，
  按 dtype 查 [[AscendOpTest precision thresholds]]）。**已实现、IsClose/Sign 真机验证过。**
- **B · 生态标准 + ATK 双标杆**（5 份：SPMV/GaussianBlur/SDDMM/SpGEMM/SpSM）→ `ecosystem_mere_mare`。
  判据是**误差比例**（NPU误差 ÷ 同精度CPU误差 ≤ 2/1.2/1.2），需**两份参考**（fp64 真值 + fp32 同精度对照）。
  ⚠ MERE/MARE 已实现，但 **ATK 双标杆 fallback 未实现**（明写 out-of-scope）→ 单标杆不过只能 `needs_review`，
  给不出终局裁决。tier 见 [[Ecosystem precision standard MERE MARE]]（proposed）。
- **C · 与 python/预期实现一致**（3 份：aclblasTrsmBatched/aclsolverCheevj/dynamicMap）→ **无对应 standard、不支持**。
  当前会被 [[select_standard silently maps unknown oracle to ascendoptest_default]] 静默套上 AscendOpTest 尺子。
- **D · 不涉及**（1 份：Sleep）→ `behavioral`，精度维度 na。

**类 A vs 类 B 的本质**：A 用单份 golden + 绝对阈值；B 用误差比例、需同一算子的两份不同精度 golden。
故 [[gen_cases GOLDEN hardcodes four elementwise operators]] 的「一算子一 golden 函数」结构**从形状上装不下类 B**。

**tier 说明**：留 `proposed`——正源在 gitignore 的 `repos/`，统计不可库内复现。分桶 43+5+3+1=52。
另注：dtype 覆盖的粗判（曾记 13/17/13）由任务书全文 grep 关键词得来、**非实证**，准确 dtype 全集须逐算子读 op_def。

**Sources.** [[session 0513d745-9176-41f0-8f4b-cb7a2d19ff86 · 2026-07-10]]
