---
title: ADR 0008 — Reuse AscendOpTest for Task 2
updated: 2026-07-02
status: proposed
---

# ADR 0008 — Reuse AscendOpTest for Task 2

**Context.** 任务书精度要求 = 满足 **AscendOpTest**（gitcode `HIT1920/AscendOpTest`）默认阈值，且该工具「性能也能用」。深挖后（结论经对抗验证）：它是 aclnn 导向的精度+性能测试框架，精度链成熟；**性能采集有两口径**——kernel-only（`msprof op`）与 e2e（`msprof --application`，含 H2D/D2H），但自带解析 `get_prof.py` 只认 kernel-only 的 OpBasicInfo.csv、不算 speedup、无 torch 通路；被测对象必须是 aclnn 算子。（e2e 更正见 [[ADR 0006 — Compare performance at a matched timing scope]]；本页原「只有 kernel-only 单口径」的对抗验证漏看了 `--application` 分支。）

**Decision.** Task 2 的精度+性能验收采 **hybrid**：
- **精度：直接复用** AscendOpTest 的 `compare.py` + `accuracy_config.py`（见 [[AscendOpTest precision thresholds]]），我们只提供 `expect_func`（numpy 融合 golden）。
- **性能：部分复用**——复用它的**采集**（kernel-only=`msprof op`；e2e=`msprof --application`）；**e2e 的解析/裁窗、1.2× 分母（`perf_baseline_source`，默认 = `gpu_external`，即 GPU 标杆、经 Task 3 对比；torch 未融合链等备选由 generated_harness 自测）与比值由 validator 计算**（框架不算 speedup、无 torch 通路），scope 选择与「同口径」纪律见 [[ADR 0006 — Compare performance at a matched timing scope]]。
- **接入：必须补桥**——catlass 裸 kernel 不能直测，经 [[catlass to aclnn bridge for AscendOpTest]] 封装。

**Consequences.** `acc-npu-run` = 包一层 AscendOpTest 做精度 + 自建 perf-ratio validator（[[ADR 0007 — Verdicts come from a deterministic validator]]）+ generated_harness 产出 aclnn 桥（[[Repo adapter interface and modes]]）。精度那套不重造，validator 仍握 1.2× 比值与最终判定。塑造 [[OpRunway component breakdown]]。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]，[[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（e2e 口径对齐 + 1.2× 分母默认=gpu_external、torch 链备选）
