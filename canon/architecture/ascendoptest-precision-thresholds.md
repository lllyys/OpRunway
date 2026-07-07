---
title: AscendOpTest precision thresholds
updated: 2026-07-02
status: canonical
reviewed: 2026-07-02
---

# AscendOpTest precision thresholds

AscendOpTest 的默认精度判据——这是 [[ADR 0005 — Precision acceptance is a three-layer policy]] 中「平台层」的**实体**（任务书「满足默认阈值」的字面定义），直接引用其常量，不另立 FP16 阈值。

- **FP16 默认阈值 = `tolerance=1e-3` + `error_rate=1e-3`**（逐元素容差 1e-3，允许 **0.1% 坏点**）。第三位 `0.1` 是遗留字段、代码不读。其余：fp32/int32/int64=`1e-4`、bf16=`4e-3`、hfloat32=`1e-3`。
- **判据**：逐元素——`|golden|≥1` 用相对误差、`<1` 用绝对误差（共用同一阈值）；仅当坏点数 > 总数×`error_rate` 才整体 fail。inf→`finfo.max`、NaN==NaN 视为通过。
- **只出 pass/fail 布尔**，不产 rtol/atol/mare/mere 等数值指标——报告要展示误差分布须由 validator 自行复算（可复用同一公式）。
- **golden 由 `expect_func` 提供**：我们写 numpy 融合参考（LayerNorm→分组 Matmul→bias→SiLU），输出 dtype 须与算子输出一致（float16）。

复用 = `compare.py` + `accuracy_config.py`（见 [[ADR 0008 — Reuse AscendOpTest for Task 2]]）；写进 [[Acceptance contract and evidence chain]] 的 precision policy。

> ⚠ 开放：FP16 融合经 Matmul 累加未必稳过 1e-3/0.1%，可能需 fp32 中间 golden 或按 bf16 放宽——M3 实测定。

**Verified.** 2026-07-01，对照 `repos/AscendOpTest`：`compare/compare/accuracy_config.py`（float16=[0.001,0.001,0.1]）、`compare/compare/compare.py`（compare_default 混合 rel/abs + 坏点占比、空阈值回退 default_acc）均已按内容指纹记录（见 `_verify.json`）。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]
