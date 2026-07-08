---
title: cannbot orchestration and cross-CLI
updated: 2026-07-08
status: proposed
---

# cannbot orchestration and cross-CLI

对 `repos/cannbot-skills`（OpRunway 对标的方法论参考仓）的深研结论，informs OpRunway 落地设计。补充 [[cannbot ops-registry-invoke workflow]] 的更广范式：

- **三层 Plugin→Agent→Skill**：Plugin=编排入口包（`AGENTS.md` `mode:primary` + `.claude-plugin/plugin.json`）；Agent=角色（architect/developer/reviewer/tester 或 analyst/perf-tuner），primary 只调度、**subagent 带 `mode` 字段单轮执行、禁内部循环、orchestrator 控循环**；Skill=原子能力（`ops/ascendc-*` ~20 个），developer subagent 用 frontmatter `skills:` 组合。
- **workflow 是单数 `workflow/` SKILL**（如 `ops-registry-invoke/workflow/SKILL.md`），承载阶段门控**状态机**：`resources/validate_workflow_state.py --stage cpN` 必须打印 `STATUS: PASSED` 才放行，明写「md/LOG 文字不能替代机器证据」。**复数 `workflows/` 是材料仓**（development-guide 蓝图 + task-prompts subagent 分阶段 prompt + templates 案例），**非 skill**（判据 = 有无 SKILL.md）。
- **跨 CLI = 中立 `AGENTS.md` 单一源 + `init.sh`（约 814 行）安装期扇出**到 opencode/claude/trae/cursor/copilot（Claude→CLAUDE.md、其余→AGENTS.md、symlink skills/agents）；**Codex 读 AGENTS.md 免费搭车**，无专用分支。OpRunway 采纳见 [[Cross-CLI unified form via neutral AGENTS.md]]。
- **复用边界**：cannbot 是「算子开发/生成」工具（免责声明自认生成代码不保证精准）；OpRunway 是「验收」——**只借架构骨架 + 门控机制 + 方法论 skill 口径**（ops-precision-standard / ops-profiling / ascendc-st-design），**不引其生成链 / developer 上库角色当验收依据或精度性能来源**。同 runtime 的 reviewer 门 ≠ OpRunway 的 `codex exec` 双门（[[ADR 0010 — Codex audit-fix double gate]]）。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-08 续：cannbot 深研）
