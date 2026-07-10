---
title: ADR 0010 — Codex audit-fix double gate
updated: 2026-07-10
status: contested
reviewed: 2026-07-06
---

# ADR 0010 — Codex audit-fix double gate

⚠ **本页 contested**：触发点（*何时*跑门）有两个相互冲突的 claim，见下方「Claim A」与「Claim B」。
**分工与制品映射两 claim 一致、不在争议内**（代码 → `cc-suite:audit-fix`；散文 → `codex exec`；`nlpm` 非本门）。
须经 `bureau:review` 由人裁决后才恢复单一 canonical 表述。

**Context.** OpRunway 会产出三类高代价制品：bureau canon/logbook（会被当作事实）、上真机的代码（本地无法完整编译验证）、md 文档。为降低错误落盘和错误交付的成本，需要独立第二双眼在关键点拦截。

## Claim A — 双触发点（2026-07-06 canonical，此前生效）

**Decision.** 采纳 Codex audit-fix 双门：(1) **bureau 新增/变更之前**，审「拟写入文本」，确认后才写入 / compile / review；(2) **md / 代码生成之后**，审 + 修产物，再交付、引用或记录改动。分工：**代码 / 脚本 → `cc-suite:audit-fix`**（9 维代码审→修→验循环）；**散文（CLAUDE.md 规则 / bureau 决策文本 / 设计 md）→ 底层 `codex exec`（Codex CLI）定制审**。`nlpm` 非本门：`nlpm:check/score/fix` 是 NL 制品确定性 lint，另线，仅用于打磨已发布 skills。执行点 = `CLAUDE.md` 最高优先级规则 #5。

来源：[[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-06 续：散文门 MCP→`codex exec` CLI）

## Claim B — 单触发点，收敛到 commit 之前（2026-07-10，未经 review）

**Decision.** 触发点收敛为**单一的「commit 之前」**：对本次 commit 涉及的全部改动（代码 / md / bureau 文本）统一审 + 修，通过后才 commit；开发迭代中的中间产物不必逐个审。**只跑一轮**：audit → fix → verify 各一次即收工，verify 剩下的 finding 如实列进结论、交用户定夺，不自动再迭代。散文门 `codex exec` 默认模型钉为 **`gpt-5.6-sol` + reasoning `low`**。分工不变。

**理由**：双触发点在快速迭代（开发态反复改插件 / 反复跑测）下摩擦过大，每个中间产物都过门既慢又无收益；攒到 commit 前一次过，审的是真正要落盘的那批。`cc-suite:audit-fix` 默认最多迭代 3 轮，实测太久，故钉死一轮。

**代价（Claim A 的原有保护会被削弱）**：Claim A 的第 (1) 条「bureau 写入前先审拟写文本」是**先审后写**；Claim B 改为「写完攒到 commit 前审」，即 bureau 文本先落盘、后审。若一次会话内 capture 了错误文本却未 commit，该文本会以 logbook 形态存活一段时间。ADR 0010 原 Consequences 里「递归：关于本门自身的 bureau 写入也须过本门，即**先审拟写文本、不先落盘**」在 Claim B 下不再成立。

来源：[[session 64604f71-dd13-4256-9a74-072fec018b48 · 2026-07-09]]（用户 2026-07-10 直接下令；`CLAUDE.md` 规则 #5 已就地改写并标注「领先于 canon」）

**发现路径**：本次改动的 codex 散文门把「未更新 canonical 就反向改最高规则」列为最高严重度 finding，随后形成本 Claim B 并把页面改为 `contested`；裁决留待 `bureau:review`。

## Consequences（两 claim 共有）

每次变更多一道门，可按逻辑制品批量执行。已有一次具体收益：发现 `plugin/bridge/route_b/aclnn_op/CMakeLists.txt` 注释里的 `add_executable` 串会被 `repos/AscendOpTest/run_test.py` 的 `get_exe_name()` 误抠的真实 bug。第二次收益（2026-07-10）：9 维代码门在 `check_manifest_sync.py` 上抓出 5 条 High（fail-open 可致假 SYNCED），其 verify 步又抓出修复过程中新引入的 `_parse_flow_list` 静默丢空项 bug。建立在 [[ADR 0001 — Adopt bureau]] 的 capture → compile → review 写门之上。**2026-07-06 更正**：散文门原写「MCP `mcp__plugin_nlpm_codex-cli__codex`」，该 MCP 由 nlpm 1.1.0 提供、nlpm 1.1.1+ 已移除 → 改为一律走 `codex exec` CLI（codex 二进制健康即可，不依赖该 MCP）。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]，[[session 64604f71-dd13-4256-9a74-072fec018b48 · 2026-07-09]]
