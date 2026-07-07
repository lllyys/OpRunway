---
title: ADR 0006 — Compare performance at a matched timing scope
updated: 2026-07-06
status: proposed
---

# ADR 0006 — Compare performance at a matched timing scope

**Context.** catlass msTuner 口径是 kernel-only（不含 H2D/D2H），外部 GPU 标杆可能测端到端。口径不一致直接比会产生系统性偏差（Q4）。

**Decision.** `timing_scope` 设为**必填枚举**：`kernel_only` / `device_e2e_no_h2d_d2h` / `host_e2e_with_h2d_d2h`。**NPU 与 GPU 必须同 scope**；缺失或不一致 → Task 3 不出结论、只出「不可比」诊断（见 [[Task 3 acceptance state machine]] 的 `BLOCKED_INCOMPARABLE_TIMING_SCOPE`）。默认公平口径 = **kernel-only**，取 median/p50、保留 p90/min。**NPU 侧默认用 `msprof op`** 采 kernel-only Task Duration（catlass 的 msTuner 是搜 tiling 的调优工具、不用于验收，见 [[catlass acceptance mechanics]]）；GPU 侧用 CUDA event / 等价 kernel event。warmup/迭代升级为 policy（warmup≥10、iters≥30 或方差收敛、计时前后同步、排除首次编译/缓存、记录频率/功耗/版本）。

**AscendOpTest 与 1.2× 比值（补，2026-07-01）.** AscendOpTest 只给 kernel-only 采集、不算 speedup、无 torch 通路（见 [[ADR 0008 — Reuse AscendOpTest for Task 2]]），故 **1.2× 分母 = `perf_baseline_source`（默认 = `gpu_external`，即 GPU 标杆、经 Task 3 对比；torch 未融合链为备选、由 generated_harness 自测）、比值由 validator 算**。⚠ **1.2× 必须同 timing_scope 对同 timing_scope**——torch 未融合链是 host/e2e 语义，别拿它 e2e 去比融合算子 kernel-only；两侧要么都 kernel-only 求和、要么都 device-e2e。此算子的分母口径用户确认中。**更正（2026-07-02）**：AscendOpTest 并非不能测 e2e——采集层内建 `msprof --application`（含 H2D/D2H），只是自带 `get_prof.py` 只解析 kernel-only 的 OpBasicInfo.csv；**device_e2e 由我们驱动 --application + 自解析 op_summary 实现**（见 [[generated_harness responsibilities]] 职责#4）。故融合类算子走 device_e2e 可行、且更贴合「整体性能」。**坐实（2026-07-06）**：`npu_torch_unfused_chain` 基线的 us = **Σ(链各小算子 kernel-only us)**（非整条链 e2e 墙钟，避免把 Python 调度/launch/sync/队列等待塞进基线）；取舍——kernel-only 会**低估融合省下的 launch/调度/同步**开销，但中间 device 访存的减少仍反映在各 kernel duration 差值里 → 报告可附 e2e 参考、判定用 matched kernel-only。用户 2026-07-06 确认「torch 小算子拼接也只看 kernel 耗时」。（任务书自身的性能验收线随参考源变、与此处 `perf_baseline_source` 默认 gpu_external 的张力见 [[Performance baseline follows the reference source]]，待 review。）

**Consequences.** 解决 Q4、框定 Q5：GPU 标杆「我们需要的最小字段契约」先定（case_id/device/dtype/shape/attrs/timing_scope/warmup/iters/sync_policy/statistic/unit/value/tool/clock-power/data_transfer_included）。落进 [[Acceptance contract and evidence chain]] 的 timing policy。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]，[[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（1.2× 分母=perf_baseline_source，默认=gpu_external、torch 链备选；2026-07-06：kernel-only 双边同口径坐实、baseline 随参考源）
