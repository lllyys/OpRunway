---
title: Performance retry merge discipline
updated: 2026-07-27
status: proposed
---

# Performance retry merge discipline

profiler 偶发失败时，可以按完全相同口径定向重采失败子集；合并时只能以有效双边记录填补主采集中的
无效项，不得覆盖已有有效数据，不得在多次采样间择优挑数。重采输入及其口径需要留下可核验的绑定
证据。

**Sources.** [[session 019fa119-cf94-7993-bd18-dae28be83cf8 · 2026-07-27]]
