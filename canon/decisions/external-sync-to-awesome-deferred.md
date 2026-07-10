---
title: External-sync of skills to awesome-ascend-skills is deferred indefinitely
updated: 2026-07-10
status: proposed
---

# External-sync of skills to awesome-ascend-skills is deferred indefinitely

**Decision（用户 2026-07-09）.** skills 向 `awesome-ascend-skills` 的 external-sync 推迟到「很久以后」。
**此刻具约束力的范围**：不得修改 `external-sources`、不得提交/创建相关 PR、不得 push、不得运行任何 sync 脚本；
现阶段只允许记录 proposed note / changes-brief。

**「很久以后」的判据（agent 建议，未经用户批准、不作事实）**：① 对抗式代码门坐实的假通过路径全部堵死；
② 至少一个算子在真 NPU 上端到端验收通过；③ `bureau:review` 把相关 canon 页 promote 到 canonical。

**与 ADR 0003 的关系（不是冲突，是补充）.**
[[ADR 0003 — Publish as self-maintained plugin and sync skills to awesome-ascend-skills]]（canonical）原文
**并无**「接口稳定前不 external-sync」，其 Consequences 只把「是否即刻登记同步」列为**待定**。
故本决定是**首次对该事作出有约束力的决定**，填补 ADR 0003 的待定项，**不推翻** ADR 0003——不构成 contested。

**动因（诚实记录）.** 主线 `plugin/skills/` 现多为草案/未接线（`acc-casegen` 未接 live 流、未登记 `AGENTS.md`；
`acc-precision`/`acc-perf`/`acc-rootcause` 在未合并分支）。现在同步出去等于发布一批未接线的 skill。

**tier 说明**：留 `proposed`，待 `bureau:review`。

**Sources.** [[session 7bae95af-05ff-45ec-a6c0-c7d03483c7b4 · 2026-07-09]]
