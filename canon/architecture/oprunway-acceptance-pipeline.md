---
title: OpRunway acceptance pipeline
updated: 2026-07-02
status: canonical
reviewed: 2026-07-02
---

# OpRunway acceptance pipeline

OpRunway 验收按三段式流水线推进：**Task 1 用例生成（ST）**——从算子任务书 + PR（含 **PR 影响面分析**）产出覆盖功能/精度/性能的测试用例集；**Task 2 NPU 跑测**——在 catlass 上跑出精度对比结果 + 性能数据；**Task 3 性能对比**——把同一份用例喂 GPU 标杆，与 Task 2 的 NPU 性能对比出报告。Task 1 的用例集是连接三段的**共享脊柱**（其字段与证据链见 [[Acceptance contract and evidence chain]]），保证 NPU 与 GPU 测同一组输入。注意 **GPU 只作性能标杆、非 Task 2 精度 oracle**；Task 2 的性能验收基线（1.2× 分母）由 `perf_baseline_source` 定、**默认 = `gpu_external`（GPU 标杆，经 Task 3 的 NPU↔GPU 对比）**，torch 未融合链等为备选（见 [[Acceptance contract and evidence chain]] 与 [[ADR 0006 — Compare performance at a matched timing scope]]）。先以 catlass 打底，跑通再泛化到其它 cann/* 算子仓。最终结论用 [[Task 3 acceptance state machine]] 表达。

组件拆分见 [[OpRunway component breakdown]]；Task 2 的事实依据见 [[catlass acceptance mechanics]]；精度/性能判定基线见 [[ADR 0002 — Acceptance grounded in catlass and the spec]]。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]，[[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（perf 基线默认=gpu_external、torch 链备选；本页因此更正从 canonical 降回 proposed 重审）
