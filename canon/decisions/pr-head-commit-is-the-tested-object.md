---
title: PR head commit is the tested object
updated: 2026-07-13
status: proposed
---

# PR head commit is the tested object

被测代码**必须是发起验收那一刻 PR head 的最新 commit**，做成 **fail-closed 硬门**——不满足即 `BLOCKED`，不是告警放行。用户 2026-07-13 拍板。

**前提事实**：真实验收场景 **PR 恒未合并**（主干永远不含本 PR 改动，master 兜底恒错）。故「合并后验哪个」问题不存在；取 head 的最新 commit、钉死一个 `head_sha`、报告记录该 sha。

**被测代码是两条独立来源，硬门卡第二条**：

- **Source 1 · 取材锚**：`fetch_source` 抓 op_def/example 喂 spec/runner。现状分支名兜底 `head→base→master→main`，head 删了静默落 master（IsClose 踩的坑）。
- **Source 2 · 真正被测的二进制**：真机 build 编**当前工作树**，流水线**从不 checkout 被测仓**、无 sha 记录、无校验——「被测 = PR 版」完全外包给人。**硬门必须落在这里**（[[A gate must validate the object that actually takes effect]]）。

**硬门判据（定稿）**：① 解析 PR head **一次**钉 `head_sha`（全程复用，别每步各自取「最新」，防作者中途 push 自造不一致）；② 取材从 `head_sha` 取、删 master 兜底、立即落盘固化该版全文（防 squash-GC 后不可达）；③ 真机仓 checkout 到 `head_sha`、build 前 `git rev-parse HEAD` 记实际编的 sha；④ 门校 `取材 sha == build sha == head_sha`，一致才放行、否则 `BLOCKED` 不启动 build；⑤ 报告记 `head_sha` + `built_commit` 作代码来源 provenance。

**D4 谁 checkout Source 2 = clone-to-scratch**：不碰用户现有仓，永远浅 clone 到独立 scratch 目录 + checkout `head_sha` + 记 sha（既躲开「动用户仓工作树」的副作用、又天然保证版本正确，合并「被测仓按需 clone」问题）。

作 [[Verify spec-PR correspondence before acceptance]] 的姊妹（同属「别拿错被测基准」家族）：那条管「任务书↔PR 配对身份」，本条管「被测代码版本」。与 [[Task spec is authoritative over PR]] 互补——审 PR 的代码、用任务书的尺子。

**⚠ 落地前必验（未做）**：未合并 PR 的 head 常在贡献者 **fork**（`head.repo.full_name`）；open+fork 的 `contents?ref=<sha>` / `raw_url` 可解析性**尚未实测**——实现前拿一个真实未合并社区 PR 试一次 API。代码未落地 → 本页 `proposed`，且**需人立新 ADR**。

**Sources.** [[session 0513d745-9176-41f0-8f4b-cb7a2d19ff86 · 2026-07-10]]（2026-07-13：Q4 被测代码 = PR head 硬门定稿）
