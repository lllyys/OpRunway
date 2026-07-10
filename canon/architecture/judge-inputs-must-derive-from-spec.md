---
title: What decides how to judge must derive from spec not caseset self-declaration
updated: 2026-07-10
status: proposed
---

# What decides how to judge must derive from spec not caseset self-declaration

**对抗式代码门坐实：验收工具自身可被绕过。** 三组 exploit 实跑成功（带反事实对照）。核心结构性缺陷 =
**validator 用 caseset 自声明字段决定「怎么判」**。[[ADR 0005 — Precision acceptance is a three-layer policy]]
落地时（T5）曾据 spec 复算 policy 夺回权威；但 T7 引入 per-case `effective_standard` 后，选哪套标准取决于
caseset 的 `compare_dtype` → 权威复失。

已复现的绕过：谎报 `compare_dtype` 放宽阈值 / 绕过整型强制 EXACT；`spec` 未 pin 时注入 `acceptance_policy`
攻破放行层；`dims=[]` 使坏点从不被裁；伪造 `storage_dtype` 令值 cast 污染送真机的 `x1.bin`；另坐实 `op`
身份三处无锚点。

**修复原则**：凡决定「怎么判」者一律从 `spec` 派生，caseset/evidence 声明只作**待核对断言**。
这是 [[A gate must validate the object that actually takes effect]] 在判定权威侧的具体化，
强化 [[ADR 0007 — Verdicts come from a deterministic validator]] 与 [[Gate checks evidence integrity not verdict]]。

**tier 说明**：留 `proposed`，待 `bureau:review`。exploit 与修复散见多个未合并分支（perf-fix 等），
逐条对仓核实留待后续；本页记的是**原则**。

**Sources.** [[session 7bae95af-05ff-45ec-a6c0-c7d03483c7b4 · 2026-07-09]]
