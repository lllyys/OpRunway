---
title: ADR 0004 — Orchestrate like cannbot ops-registry-invoke
updated: 2026-06-30
status: canonical
reviewed: 2026-06-30
---

# ADR 0004 — Orchestrate like cannbot ops-registry-invoke

**Context.** 成品编排两条路：Claude Code 的 Workflow 工具（确定性 JS、真并行），或 cannbot 式「workflow 型 SKILL 剧本 + 角色子 agent + JSON 证据 + Python 校验器」。验收成品的硬要求：能随插件分发、对所有安装者可用、能在精度/性能处卡人工 CP。

**Decision.** 成品编排照 [[cannbot ops-registry-invoke workflow]] 的**混合架构**：用户入口 = skill/command；并行 fan-out = 角色子 agent；人工 CP = `AskUserQuestion`；判定 = 确定性 validator（见 [[ADR 0007 — Verdicts come from a deterministic validator]]）。**Claude Code 的 Workflow 工具不作产品骨架，只作「可用则用、不可用就优雅降级为子 agent」的内部并行加速器。**

**为什么不拿 Workflow 工具当骨架（经官方文档核实，2026-06-30）.**
- ① **不能随插件分发**：plugin 组件只有 skills/agents/hooks/MCP/LSP/monitors，`plugin.json` 无 `workflows` 字段；workflow 只能存本地 `.claude/workflows/`。
- ② **要 opt-in、不能假定人人可用**：需 v2.1.154+，Pro 默认关（`/config` 开），可被 `disableWorkflows` / `CLAUDE_CODE_DISABLE_WORKFLOWS` 禁。
- ③ **不支持中途人工 CP**：官方「No mid-run user input」，分阶段签核要把每阶段拆成独立 workflow——与验收「停下等人放行」相悖。
- ④ skill 不能直接调 saved workflow（skill 是指令、workflow 是后台脚本）。

**Consequences.** 强化 [[ADR 0007 — Verdicts come from a deterministic validator]]；塑造 [[OpRunway component breakdown]]（skill 入口 + 子 agent + validator + AskUserQuestion CP）。Workflow 工具仍可作开发侧批量跑测/调研的加速器。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]
