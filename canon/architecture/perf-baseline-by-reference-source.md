---
title: Performance baseline follows the reference source
updated: 2026-07-09
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

依据 `doc/oprunway-spec-pr-analysis.md`。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-06 检查点；2026-07-08 续：真机三算子任务书原文查证=TBE、GPU 非必需），[[session 37223d6d-c20e-48a9-84f5-99aeaddb7f51 · 2026-07-09]]（Equal 项作废：#2890 配错·任务未验收；Sign/Neg 与 TBE 口径仍留）
