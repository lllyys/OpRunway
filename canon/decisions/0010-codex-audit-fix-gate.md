---
title: ADR 0010 — Codex audit-fix double gate
updated: 2026-07-06
status: canonical
reviewed: 2026-07-06
---

# ADR 0010 — Codex audit-fix double gate

**Context.** OpRunway 会产出三类高代价制品：bureau canon/logbook（会被当作事实）、上真机的代码（本地无法完整编译验证）、md 文档。为降低错误落盘和错误交付的成本，需要独立第二双眼在关键点拦截。

**Decision.** 采纳 Codex audit-fix 双门：(1) **bureau 新增/变更之前**，审「拟写入文本」，确认后才写入 / compile / review；(2) **md / 代码生成之后**，审 + 修产物，再交付、引用或记录改动。分工：**代码 / 脚本 → `cc-suite:audit-fix`**（9 维代码审→修→验循环）；**散文（CLAUDE.md 规则 / bureau 决策文本 / 设计 md）→ 底层 `codex exec`（Codex CLI）定制审**。`nlpm` 非本门：`nlpm:check/score/fix` 是 NL 制品确定性 lint，另线，仅用于打磨已发布 skills。执行点 = `CLAUDE.md` 最高优先级规则 #5。

**Consequences.** 每次变更多一道门，可按逻辑制品批量执行。已有一次具体收益：发现 `plugin/bridge/route_b/aclnn_op/CMakeLists.txt` 注释里的 `add_executable` 串会被 `repos/AscendOpTest/run_test.py` 的 `get_exe_name()` 误抠的真实 bug。递归：关于本门自身的 bureau 写入也须过本门，即先审拟写文本、不先落盘。建立在 [[ADR 0001 — Adopt bureau]] 的 capture → compile → review 写门之上。**2026-07-06 更正**：散文门原写「MCP `mcp__plugin_nlpm_codex-cli__codex`」，该 MCP 由 nlpm 1.1.0 提供、nlpm 1.1.1+ 已移除 → 改为一律走 `codex exec` CLI（codex 二进制健康即可，不依赖该 MCP）。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-06 续：散文门 MCP→`codex exec` CLI）
