---
title: Ecosystem precision standard MERE MARE
updated: 2026-07-06
status: proposed
---

# Ecosystem precision standard MERE MARE

《生态算子开源精度标准》——一手出自 `cann/opbase` 仓 `docs/zh/ops_precision_standard/experimental_standard.md`，明确管「贡献在 **experimental 目录**下的计算类算子」，正是 SPMV/Trsm 等 experimental 目录社区任务的落点（标准 GE 类多走 AscendOpTest 默认阈值）。与 [[AscendOpTest precision thresholds]]（工具默认 tolerance/error_rate、只出 pass/fail）是**两套并列标准**：部分任务书引 AscendOpTest 默认阈值（如 Sign/IsClose），部分引本标准（如 SPMV/Trsm）。二者同属 [[ADR 0005 — Precision acceptance is a three-layer policy]] 的「平台层」候选口径。

两个相对误差指标（$actual$=NPU 输出，$golden$=参考真值，分母加 `1e-7` 防除零）：

- **MERE = 平均相对误差（Mean Relative Error）** = `avg( |actual−golden| / (|golden|+1e-7) )`
- **MARE = 最大相对误差（Max Relative Error）** = `max( |actual−golden| / (|golden|+1e-7) )`

**通过标准：`MERE < Threshold` 且 `MARE < 10 × Threshold`**。Threshold 按 dtype：

| dtype | FP16 | BF16 | FP32 | HiFLOAT32 | FP8 E4M3 | FP8 E5M2 |
|---|---|---|---|---|---|---|
| Threshold | 2⁻¹⁰ | 2⁻⁷ | 2⁻¹³ | 2⁻¹¹ | 2⁻³ | 2⁻² |

**单标杆比对** = 与更高精度实现（**CPU 或昇腾小算子拼接**）直接比。单标杆不满足时，SPMV 类任务改用 **ATK 双标杆**（`AscendTest/ATK` 的 `cv_fused_double_benchmark`，高精度 CPU 为真值，NPU/同精度 CPU 误差比例 ≤ 2/1.2/1.2）。

> ⚠ 术语更正：MERE=平均、MARE=最大（本会话对话中曾一度对调，以本页一手原文为准）。落进 [[Acceptance contract and evidence chain]] 的 precision policy 作可选口径。同套 MERE/MARE 口径本地亦见 `repos/cannbot-skills/ops/ops-precision-standard`（与 opbase 一手互证）。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-06 检查点）
