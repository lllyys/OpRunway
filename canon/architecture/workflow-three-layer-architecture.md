---
title: OpRunway workflow three-layer architecture
updated: 2026-07-06
status: proposed
---

# OpRunway workflow three-layer architecture

为满足「跨运行时可移植」（Claude Code / Codex / Antigravity 都能跑），workflow 分三层，是 [[OpRunway component breakdown]] 的落地细化：

- **Layer 0 · 数据契约（脊柱）** = 6 个 JSON：`spec`（任务书解析产物）→ `caseset`（用例集）→ `evidence`（跑测证据）→ `verdict`（裁决）；`baseline` + `perf_report`（Task 3）。stage 之间**只传这些 JSON / 数据文件**。
- **Layer 1 · 确定性脚本（核心脑子，工具中立）** = `gen_cases`（spec→caseset）、`repo_adapter`（一接口、按仓换实现）、`validator`（evidence→verdict，[[ADR 0007 — Verdicts come from a deterministic validator]]）、`perf_compare`。
- **Layer 2 · per-tool 编排薄壳（可替换）** = Claude Code 一套（顶层编排 + `task-doc-parse` agent（唯一必需 NL 步）+ acc-casegen/acc-npu-run/acc-perf-compare skills + eval agent）；Codex/Antigravity 各自薄壳，驱动**同一 Layer 0/1**。

**铁律**：价值与难点全压进 Layer 0+1（100% 可移植，是资产）；换运行时只换 Layer 2。核心脑子沉到脚本——连 casegen 的 NL 复核也只产审阅意见、不改 caseset。codex 已核 **Layer 0/1 无 Claude-Code 依赖**。

详见 `doc/oprunway-workflow-design.md`。吃 [[Repo adapter interface and modes]] 与 [[Acceptance contract and evidence chain]]。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-06 检查点）
