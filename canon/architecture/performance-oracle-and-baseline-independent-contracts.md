---
title: Performance oracle and performance baseline are independent contracts
updated: 2026-07-26
status: proposed
---

# Performance oracle and performance baseline are independent contracts

功能或精度章节指定的 reference / oracle，只回答“结果应与谁一致”；性能章节指定的 baseline，只回答
“耗时应与谁比较”。除非任务书明确把两者绑定，不能因为任务书要求功能对齐某个 Torch API，就自动把
`torch_npu` 包装选为性能 baseline。

因此 spec 必须分别记录 `reference` / `precision.oracle` 与 `perf.baseline`。任务书点名可直接调用的
ACLNN、小算子拼接、TBE 或 GPU 标杆时，性能侧直接服从该条款；框架级端到端性能只有在任务书明确要求时
才成为基线。

**Sources.** [[session perf-baseline-direct-20260726 · 2026-07-26]]
