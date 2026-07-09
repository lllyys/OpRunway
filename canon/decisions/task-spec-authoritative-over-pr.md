---
title: Task spec is authoritative over PR
updated: 2026-07-09
status: proposed
---

# Task spec is authoritative over PR

验收以**算子任务书**为准，不以交付 PR 为准——18 个真实 PR 深读证明二者**不逐项对齐**：Fmod 任务书要 INT16、PR 实际交 INT32；im2col 任务书写 A2/A3、PR 主攻 950；RightShift 定 10× 性能目标、PR 内零性能证据。**⚠ 前置**：「任务书权威」成立的前提是**先确认这个 PR 确是这份任务书的交付**（见 [[Verify spec-PR correspondence before acceptance]]）——拿错 PR 去对任务书判毫无意义。（原列的「Equal 只注册 A2 未做 A3 → 按任务书判未达标」一例**已作废**：2026-07-09 确认 #2890 配错、Equal 任务未验收。）

含义（落到 [[OpRunway workflow three-layer architecture]] 的 Task 1）：

- `spec.json` 从**任务书**生成；validator 按 spec 判、不按 PR。
- 任务书↔PR 的落差进 `spec.task_pr_gaps`、显式标**待确认**，不当错、也不默默采信 PR。
- **PR 自带的 UT ≠ 精度验收**（多是 `TestGetWorkspaceSize` / `SUCCEED()` 只跑不比）；证据（精度 + 性能）由 **OpRunway 自产**，不指望 PR 里有。仓内**性能证据基本缺席**、精度证据强弱不一——这正是 OpRunway 的价值所在。

依据 `doc/oprunway-spec-pr-analysis.md`（41 任务书 + 18 PR 规律）。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-06 检查点），[[session 7f7b1411-e1d0-47aa-93d5-19ccd6fcd130 · 2026-07-08]]（原 Equal 平台错配例，2026-07-09 撤），[[session 37223d6d-c20e-48a9-84f5-99aeaddb7f51 · 2026-07-09]]（Equal 例作废+前置「先验证对应」）
