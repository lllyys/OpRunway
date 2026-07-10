---
title: Evidence provenance binding proves metrics from files not files from NPU
updated: 2026-07-10
status: proposed
---

# Evidence provenance binding proves metrics from files not files from NPU

**问题（架构级）**：validator 全信自报 metrics，与 `out.bin` / `golden.npy` 无 hash 绑定 →
伪造 `bad_count=0` 即 pass。**用户选定方案 A**：产物落盘 + evidence 记 sha256 + 门内用
`precision_policy.compute_metrics` 重算比对。硬纪律：numpy 缺失或产物缺失一律 FAILED，mock 不放宽。

**诚实边界（须写入 canon 与 docstring，不假装已防）**：方案 A 只证「metrics 由这两个文件算出」，
**不证「文件来自真 NPU 跑测」**。后者需另绑 `OPRUNWAY_DONE` 哨兵 / raw log hash / msprof 输出。
此边界不可含糊——它决定了「验收通过」的真实含义止于「这两个落盘文件自洽」，未及「这两个文件确来自真机」。

强化 [[Machine-verifiable acceptance gate]] 与 [[Gate checks evidence integrity not verdict]]：
门内重算比对属「证据可信」范畴、非重判 verdict（判定权仍归 [[ADR 0007 — Verdicts come from a deterministic validator]]）。

**tier 说明**：留 `proposed`，待 `bureau:review`。方案 A 的落地代码见 [[Machine-verifiable acceptance gate]]
相关提交（对仓核实留后续）；本页记的是**边界声明**这一 durable claim。

**Sources.** [[session 7bae95af-05ff-45ec-a6c0-c7d03483c7b4 · 2026-07-09]]
