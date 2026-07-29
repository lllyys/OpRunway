---
title: Performance shape classification
updated: 2026-07-27
status: proposed
---

# Performance shape classification

性能 case 按目标硬件的 UB 单次承载能力分为小 shape 和大 shape。A3 上以全部输入的物理载荷字节
求和：`<= 256 KiB` 为小 shape，`> 256 KiB` 为大 shape；边界归小 shape，因为可一次搬入 UB。

大小 shape 标签只用于分组统计，不产生免测，不自动改变性能阈值。

**Sources.** [[session perf-case-source-shape-rule-20260726 · 2026-07-26]]，
[[session 019fa119-cf94-7993-bd18-dae28be83cf8 · 2026-07-27]]
