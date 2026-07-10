---
title: Target hardware and dtype set are determined per operator from taskdoc and op_def
updated: 2026-07-10
status: proposed
---

# Target hardware and dtype set are determined per operator from taskdoc and op_def

目标硬件与 dtype 全集**不假定、按算子判**，从两个正源双向交叉核验：任务书的 `适配硬件` 字段
（52/52 份社区任务书均有此字段）＋ 算子 `op_def` 的 `AICore().AddConfig(...)` 与
`Input(...).DataType({...})`。两者应一致，不一致入 `task_pr_gaps`。**核验须逐算子做，不可外推。**

**任务书侧硬件分布**（仅字段统计，非逐份核 op_def）：`适配硬件` 为 Atlas A2/A3 系 38 份 ·
Ascend 950 系 13 份 · 纯 Atlas 300V Pro 1 份（互斥分桶 38+13+1=52；涉 300V Pro 共 2 份，
另 1 份落在 A2/A3 桶内兼列）。故「任务书目标算子是 950」只对 13/52 成立。
**300V Pro 本仓无硬件、无 de-risk，涉及的 2 份须先停下确认平台。**

**已双源核验的仅 IsClose**：任务书 `Atlas A2/A3` ↔ op_def `AddConfig("ascend910b")` + `AddConfig("ascend910_93")`，
一致；其输入 dtype 全集 = {float32, float16, bfloat16, int32}（4 种），真机 runner 只跑前 2 种
（见 [[Real-NPU runner supports only float32 and float16]]）。故其目标机是 a3（arch 2201）、非 a5。

**tier 说明**：本页留 `proposed`——52 份统计与 IsClose 的 4-dtype 结论的正源在 `repos/cann-ops-competitions`
与 `repos/ops-math`，被 gitignore、不入库，核实**不可在库内复现**，故不升 verified。判定**规则本身**
（双源交叉核验、不假定）是本页的 durable claim。

**Sources.** [[session 0513d745-9176-41f0-8f4b-cb7a2d19ff86 · 2026-07-10]]
