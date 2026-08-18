---
id: pg-target-hardware-and-dtype-per-operator-from-taskdoc-and-opdef
title: Target hardware and dtype set are determined per operator from taskdoc and op_def
updated: 2026-07-26
status: proposed
rests_on:
  - { page: "[[Generalization precedes operator identity]]", span: "^claim", because: "hardware and dtype must be derived from facts instead of operator-name branches" }
---

# Target hardware and dtype set are determined per operator from taskdoc and op_def

目标硬件与 dtype 全集**不假定、按算子判**，从两个正源双向交叉核验：任务书的 `适配硬件` 字段
（52/52 份社区任务书均有此字段）＋ 算子 `op_def` 的 `AICore().AddConfig(...)` 与
`Input(...).DataType({...})`。两者应一致。 ^claim

**Sources.** [[session 0513d745-9176-41f0-8f4b-cb7a2d19ff86 · 2026-07-10]]，[[session 9f5c778e-cdf9-4c84-bb4f-f0ab8c49a99d · 2026-07-24]]（再次确认 a3/a5 只作机器能力池，逐算子仍须双源核定）
