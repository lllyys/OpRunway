---
title: ADR 0003 — Publish as self-maintained plugin and sync skills to awesome-ascend-skills
updated: 2026-06-29
status: canonical
reviewed: 2026-06-30
---

# ADR 0003 — Publish as self-maintained plugin and sync skills to awesome-ascend-skills

**Context.** OpRunway 含 skills + agents + workflows。awesome-ascend-skills 的 external 同步只抽 skills（其自身策略）；但 Claude Code 的 plugin 原生可打包 skills + agents，官方/社区 marketplace 两者都收（早前「agents 无法发布」的说法已更正）。

**Decision.** 自维护一个 OpRunway 插件仓（skills + agents + workflows 都放，靠 `/plugin install` 分发），把其中 **skills 部分**经 external-sources 同步进 awesome-ascend-skills——cannbot 同款模式。

**Consequences.** agents/workflows 留自有仓；只有 skills 那部分进 awesome。仓位置（gitcode/github）、插件名、是否即刻登记同步待定。承载 [[OpRunway component breakdown]] 的分发。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]
