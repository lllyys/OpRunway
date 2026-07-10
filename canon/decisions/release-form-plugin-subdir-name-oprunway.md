---
title: Release form is a plugin shipped from the main repo plugin directory
updated: 2026-07-10
status: proposed
---

# Release form is a plugin shipped from the main repo plugin directory

**Context.** T9 发布形态定稿（用户 2026-07-09 逐条拍板）。术语澄清：「插件仓」= OpRunway 主仓内
`plugin/` 目录作为自维护插件发行单元，不另建独立 repo。

**Decision.**
1. **仓位置——保持现状**：插件继续作为 OpRunway 主仓 `plugin/` 子目录，不拆独立 repo；
   远端维持 GitHub `lllyys/OpRunway` + GitCode `brian66237/OpRunway` 双镜像。
2. **插件名——保持现状**：`.claude-plugin/plugin.json` 的 `name` 维持 `oprunway`
   （当前仓内该字段确为 `"name": "oprunway"`，与决定一致——corroboration，非本决定的 verified 依据）。
3. `init.sh` 跨 CLI 扇出作为 `/plugin install` 之外的第二条分发路径保留。
   ⚠ 现状更正：`plugin/init.sh` 当时尚未在主线、仅存于未合并 worktree 分支，本决定是「保留该路线、待落地」。

**Consequences.** 承载 [[ADR 0003 — Publish as self-maintained plugin and sync skills to awesome-ascend-skills]]
的具体落地形态。external-sync 的时机另见 [[External-sync of skills to awesome-ascend-skills is deferred indefinitely]]。

**tier 说明**：留 `proposed`，待 `bureau:review` promote（本条是低权威 logbook capture 的蒸馏，非事实）。

**Sources.** [[session 7bae95af-05ff-45ec-a6c0-c7d03483c7b4 · 2026-07-09]]
