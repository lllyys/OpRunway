---
title: Median performance baseline follows the stock Torch interface
updated: 2026-07-27
status: proposed
contradicts: [[Direct ACLNN performance baseline]]
---

# Median performance baseline follows the stock Torch interface

用户确认 Median 任务书所称 `aclnnMedian`、`aclnnMedianDim` 小算子拼接版本等价于 Torch 对应接口。
因此 Median 性能 baseline 是同机 stock `torch.median`，无需为每份新任务书另做包装等价性证明，也
不能以直接测一个内置 ACLNN 接口替代。

新验收设计中，stock `torch.median` 是 baseline；PR DUT 通过独立
`torch.ops.<namespace>.*` C++ Extension 入口执行。旧的直接 ACLNN 性能结果在新设计完成前不作为
任务书验收证据，也不用于决定 Median 实现优化。

**Sources.** [[session perf-case-source-shape-rule-20260726 · 2026-07-26]]，
[[session torch-cppextension-route-20260727 · 2026-07-27]]
