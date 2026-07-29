---
title: Performance baseline follows the reference source
updated: 2026-07-27
status: proposed
---

# Performance baseline follows the reference source

社区任务的性能基线**由任务书按参考源/改动目标定**，不是单一 `gpu_external`（深读 18 个 PR 归纳）：

- **重写类（参考内置 TBE）** → 基线 = **TBE 95% / 无劣化**（Sign 是「无劣化」、Equal/IsClose/Relu 等是「≥95%」）；多数「加 dtype」类也走 TBE。
- **移植类（对标 GPU 库 cuSPARSE/cuBLAS/cuCollections）** → 基线 = **GPU A100 的 0.5–0.8×**（SPMV 0.5×、dynamicMap 0.7/0.5×、Trsm 0.8×，常给具体用例 us）。
- **部分「加 dtype」类** → 基线 = **相对同 op 其他 dtype 不劣化**（IndexFill：新 dtype 不劣于同宽 int32/int64）。
- **可选** = **昇腾小算子拼接（torch 小算子链）**——[[Ecosystem precision standard MERE MARE]] 的单标杆也认这条。
- 任务书**常带「小 shape 例外条款」**（如 <10us 差 3us 需仿真图证明）。

**这是任务书自身的性能验收线**（主线，按参考源）。它与 [[Acceptance contract and evidence chain]] 的 `perf_baseline_source` 字段是两回事——后者当前 canonical 默认 `gpu_external`（OpRunway 在 [[Task 3 acceptance state machine]] 额外加的对比层）。⚠ **张力待 review 裁**：这批社区任务的验收线其实由任务书按参考源定（多为 TBE/GPU、非 gpu_external），是否应让 `perf_baseline_source` 也从任务书推导、而非固定默认——留人工复核，**不在此页单方改 canonical**。对比口径见 [[ADR 0006 — Compare performance at a matched timing scope]]。

**2026-07-08 实锤（强化上述张力，待 review 裁）**：真机验证的 **Sign / Neg**（~~Equal~~ 那项 2026-07-09 作废：#2890 配错、Equal 任务未验收，见 [[Verify spec-PR correspondence before acceptance]]），逐一读**任务书原文**均写「参考内置 **TBE**、性能不劣化 / ≥TBE 95%」——**性能基线是 TBE、GPU 非必需**（GPU 仅移植类算子要），spec 也全是 `perf.baseline=tbe`。这给「`perf_baseline_source` 应从任务书推导、别固定默认 `gpu_external`」添了具体证据 → **建议 review 裁定：这批社区任务的 Task 3 GPU 对比层为可选、非必需**。（顺带修正：`sign.spec.json` 原 `target_ratio=0.95` 与任务书「无劣化」(=ratio 1.0) 不符，2026-07-08 已改为 1.0。）

**2026-07-26 最短证据链补充（用户明确裁定，待 review）**：任务书已经点名一个可直接执行、可按匹配
scope 计时的性能标杆时，直接调用并测量该对象；不先绕到框架包装，再为每份新任务书证明包装与点名实现
等价。功能/精度 oracle 与性能 baseline 分开解释，见
[[Performance oracle and performance baseline are independent contracts]]。任务书点名既有 ACLNN 实现时的
落地见 [[Direct ACLNN performance baseline]]。

**2026-07-27 Torch-reference 接入补充（待 review）**：baseline 的语义仍由任务书决定；当任务书要求
对标 Torch 时，默认以隔离 C++ Extension 分开 stock Torch baseline 与 PR DUT，避免两边路由不可辨。
见 [[Torch reference acceptance uses an isolated C++ extension by default]]。这不把所有任务书 baseline
强制改成 Torch，也不取消任务书明确点名直接 ACLNN 时的通用能力。

依据 `doc/oprunway-spec-pr-analysis.md`。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-06 检查点；2026-07-08 续：真机三算子任务书原文查证=TBE、GPU 非必需），[[session 37223d6d-c20e-48a9-84f5-99aeaddb7f51 · 2026-07-09]]（Equal 项作废：#2890 配错·任务未验收；Sign/Neg 与 TBE 口径仍留），[[session perf-baseline-direct-20260726 · 2026-07-26]]，[[session torch-cppextension-route-20260727 · 2026-07-27]]
