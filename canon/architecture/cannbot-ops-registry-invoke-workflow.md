---
title: cannbot ops-registry-invoke workflow
updated: 2026-06-29
status: canonical
reviewed: 2026-06-30
---

# cannbot ops-registry-invoke workflow

cannbot 的 `ops-registry-invoke` 用**提示词编排**（而非 Claude Code 的 Workflow 工具）实现算子开发工作流：

- `workflow/SKILL.md` 是编排剧本——**4 阶段 + 5 检查点**（CP1/CP2 必需，CP3 精度 / CP4 性能 / CP5 上库 可选）；主 agent 管流程推进/确认/日志，子 agent 管执行，委派时只给输入/输出/验收标准。
- `agents/` 下 **3 个角色子 agent**：architect / developer / tester。
- 状态以 JSON 机读证据保存，由 `workflow/resources/validate_workflow_state.py` **硬校验**（status 必须 passed 等）。

其 **CP3 精度 / CP4 性能 几乎 1:1 对应 OpRunway 的 Task 2**，是现成结构模板。支撑 [[OpRunway component breakdown]] 的编排选型与 [[ADR 0004 — Orchestrate like cannbot ops-registry-invoke]]。

**Verified.** 2026-06-29，对照 `repos/cannbot-skills/plugins-official/ops-registry-invoke`：`workflow/SKILL.md`、`agents/ascendc-ops-architect.md`、`agents/ascendc-ops-developer.md`、`agents/ascendc-ops-tester.md`、`workflow/resources/validate_workflow_state.py` 均存在（见 `_verify.json`）。

**Sources.** [[session f0c36755-189d-4c2c-b321-c0d2ec5c4b1b · 2026-06-29]]
