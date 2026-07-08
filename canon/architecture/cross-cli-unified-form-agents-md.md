---
title: Cross-CLI unified form via neutral AGENTS.md
updated: 2026-07-08
status: proposed
---

# Cross-CLI unified form via neutral AGENTS.md

**Claim.** OpRunway 跨运行时（Claude Code / Codex / …）的统一形态 = **一份中立 `plugin/AGENTS.md` 作单一事实源**（编排 + 依赖 + 硬门规则），`CLAUDE.md` 与 `.claude-plugin/plugin.json` 从它派生；**Codex 原生读项目根 `AGENTS.md`——免费搭车**，无需专用适配分支。发布走 `init.sh` 式**安装期扇出**（Claude→CLAUDE.md、其余→AGENTS.md、symlink skills/agents）。

**现状**：`plugin/AGENTS.md`（含 `op-acceptance` 编排 + 硬门规则）与 `plugin/acc-common/check_manifest_sync.py`（校验 AGENTS.md ↔ plugin.json 同步、防双写漂移，`STATUS: SYNCED`）**已建**；`init.sh` 扇出尚未建（属落地设计 P2）。故本页记为 proposed（形态定向 + 部分实现）。

来源范式见 [[cannbot orchestration and cross-CLI]]（cannbot 用中立 AGENTS.md + 814 行 init.sh 安装期扇出到 opencode/claude/trae/cursor/copilot）。**避开 cannbot 的坑**：清单从单一源生成而非两处手抄、不照搬 `external_directory: allow`、不 `sed` 私有路径进制品（走 `OPRUNWAY_*` 环境变量、symlink 保相对拓扑）。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-08 续：cannbot 深研 + 跨 CLI 定型）
