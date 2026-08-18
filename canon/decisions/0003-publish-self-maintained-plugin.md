---
id: pg-0003-publish-self-maintained-plugin
title: ADR 0003 — Publish as a self-maintained plugin
updated: 2026-08-11
status: canonical
reviewed: 2026-06-30
---

# ADR 0003 — Publish as a self-maintained plugin

**Context.** OpRunway 含 skills + agents + workflows。Claude Code 的 plugin 原生可打包 skills + agents，官方/社区 marketplace 两者都收（早前「agents 无法发布」的说法已更正）。

**Decision.** 自维护一个 OpRunway 插件仓，skills、agents、workflows 都放在其中，靠 `/plugin install` 分发。 ^claim

**Consequences.** 仓位置（gitcode/github）与插件名待定。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]
