---
title: ADR 0005 — Precision acceptance is a three-layer policy
updated: 2026-06-30
status: canonical
reviewed: 2026-06-30
---

# ADR 0005 — Precision acceptance is a three-layer policy

**Context.** catlass 内置阈值（fp16≈2^-8）、cannbot 分类标准（fp16 MERE 2^-10、bf16 2^-7、MARE=10×MERE）、任务书目标三者不一致。直接用 catlass 内置会放过平台标准下不合格的结果（Q3）。

**Decision.** 精度验收用**三层口径，不是三选一**：① 任务书显式验收目标优先（交付契约）；② 统一平台标准（借 cannbot `ops-precision-standard`）作默认/补缺；③ catlass 内置阈值只作仓内 smoke/回归，不作最终放行。报告同时出 `catlass_compare_pass` / `standard_profile_pass` / `acceptance_precision_pass`，**放行只看 `acceptance_precision_pass`**。任务书目标若宽于平台底线，不自动放行——标 `PASSED_WITH_RISK`，人工 CP 决（见 [[Task 3 acceptance state machine]]）。

**Consequences.** 解决 Q3。MERE/MARE/RMSE、NaN/Inf、零值附近绝对误差、小值阈值写进 [[Acceptance contract and evidence chain]] 的 precision policy。阈值待首份真实任务书校准。是 [[ADR 0002 — Acceptance grounded in catlass and the spec]] 的精度细化，依据 [[catlass acceptance mechanics]]。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]
