---
title: Golden source uses taskdoc method then CPU API fallback
updated: 2026-07-26
status: proposed
contradicts: [[Golden and precision standard come only from the task-doc-specified method]], [[ADR 0011 — Golden is decoupled from the engine and loaded per operator]]
---

# Golden source uses taskdoc method then CPU API fallback

用户 2026-07-22 将 golden 来源裁定为两档链：

1. 任务书明确指定测试方法时，按该方法生成真值；方法在当前环境无法执行时 fail-closed，不自动降级。
2. 任务书未指定真值方法时，回退到 CPU 上现成的 torch / numpy API。单 API 调用无需人核；多步公式
   拼接必须人核。后端在生成期选定并写入 per-op golden，不根据运行时安装情况偷换。

该裁定与现有 proposed 页“只能来自任务书指定方法”以及 ADR 0011 中“PR 参考优先”的来源顺序冲突，
因此三页均保持 contested/proposed，等待 `bureau:review` 人工选择现行表述。

**Sources.** [[session 8217ff1b-d287-4074-bfe1-a7d0bdb3809f · 2026-07-22]]
