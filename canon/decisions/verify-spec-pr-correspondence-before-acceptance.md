---
title: Verify spec-PR correspondence before acceptance
updated: 2026-07-09
status: proposed
---

# Verify spec-PR correspondence before acceptance

验收开跑前，**先验证「任务书 ↔ PR」的对应关系本身**：确认「这个 PR 确是这份任务书的交付 PR」、且该任务**确有已验收的交付**。配错对应、或对应的其实是「任务书要了但未落地/未验收」的空任务，会让**下游一切裁决作废**——哪怕精度/性能门再严、root-cause 解耦（[[Root-cause decoupling before attribution]]）做得再干净。对应查证靠 **issue 追踪号 + 改动落点目录**，不靠算子名字面匹配（名字常是常见子串、易误配）。

这比 [[Root-cause decoupling before attribution]] 更上游：解耦分清「被测物 vs harness」，但前提是**比对基准（哪份任务书 × 哪个 PR）本身没配错**；也强化 [[Task spec is authoritative over PR]] 的前置——任务书权威的前提是先确认它对应的就是被测 PR。

**动因案例（Equal）**：真机全 0 的归因 refine 了三遍（op-bug → harness-bug → op「真阳性/A3 缺陷」），却始终没质疑最上游。2026-07-09 用户正式确认：**gitcode PR #2890 不是本社区 Equal 任务（`Equal_task_doc.md`）的交付 PR（误配）**，且 **Equal 社区任务至今未验收通过、无已验收对应 PR**。故先前「Equal A3 未达标·真阳性」结论**整体作废**——它是拿误配 PR 去对不相干任务书判出来的。识别「未验收空任务」并跳过，正是 OpRunway 的应有能力之一。

**Sources.** [[session 37223d6d-c20e-48a9-84f5-99aeaddb7f51 · 2026-07-09]]
