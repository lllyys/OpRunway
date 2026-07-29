---
title: Elu and Silu real NPU runs support golden decoupling
updated: 2026-07-26
status: proposed
---

# Elu and Silu real NPU runs support golden decoupling

2026-07-23 session 记录：A5-950 上 Elu 的带属性 runner 与 Silu 的零属性 runner 均完成真 NPU 精度对拍，
各有 18 个非空 case `bad_count=0`，另各 2 个空张量 case 为无元素可比的 vacuous 结果。该结果为
golden 去引擎化跨不同 runner 形态提供正向见证，但原始真机产物位于未入库 scratch/container，故本页
只能保持 proposed，不能把 minute 中的数字自动升级为 verified。

**Sources.** [[session 106cca26-c27e-4f76-8624-ee678b6aea61 · 2026-07-23]]
