---
name: cann-issue-track
description: 跟踪已提交到上游社区（GitHub / Gitee / GitCode）的算子 issue：查回复状态、读懂评论里的修复方案、应用并复测，PASS 就关 issue 并写入 FAQ，FAIL 就人工确认后追问。涉及「查 issue 回复 / 跟 issue 走一遍 / 社区给方案了帮我试试 / 按社区方案重试 / retest with community fix」等意图时使用本 skill；还没提过 issue、要给失败算子起草上报时改用 cann-issue-report。
---

# 社区 issue 跟踪与复测

查已提 issue 的社区回复，**自己读懂评论里的修复方案**，应用并复测：
PASS 则关 issue 并写入 FAQ，FAIL 则人工确认后追问。

## 设计原则

- **方案理解交给你，不依赖正则。** `track.py` 里没有、也不许有任何「方案分类器」。
  P2 读评论判 kind 永远是你的活——社区评论的表达方式无穷无尽，关键词匹配只会漏掉真方案。
- **机械动作全部走 CLI。** 拉评论、发现元数据、建计划、复测、回写一律用 `track.py` 子命令。
  在对话里手写 python 片段等于把同一段代码读一遍再写一遍。
- **元数据先自动发现，再问人。** SOC / repo_path / failure_type 都有持久化来源，
  `track discover` 跑一遍后只问它报 missing 的字段，且一次问完。

## 入口参数

| 参数 | 含义 | 取值约束 | 初值推断 |
| --- | --- | --- | --- |
| `<skill>` | 本 skill 的安装路径 | 命令在**项目根**跑，产物落 `CWD/cann-ops-report/` | 从本文件位置得出 |
| `<python>` | 解释器 | 只用 stdlib | 默认 `python3` |
| `GITEE_TOKEN` / `GITCODE_TOKEN` | 上游 API 凭据 | 只读环境变量，**不落盘** | P1 前检测，缺则提示 `export` |

机读结果走 stdout（JSON），人读提示走 stderr。`--help` 可查全部参数。

## 前置检查

读 `CWD/cann-ops-report/issues/state.json`。**不存在或为空**时不要自己去别处翻——
按 [references/register-and-fetch.md](references/register-and-fetch.md) 的手动注册流走。

## 外发动作确认规则（全局，所有阶段适用）

任何**对外可见的写动作**——发评论、关 issue、新建 issue——执行前必须走完这四步。
这些动作发到公开社区就撤不回，维护者会收到通知：

1. **先不带 `--execute` 跑一次**（`track followup` / `track finish` 默认就是 dry-run：
   只打印将要发出的内容，不调任何上游 API、不写 FAQ、不改 `state.json`）
2. 在对话中**完整展示**它打印出来的内容（标题 + 正文，**不截断**——单条评论量不大，
   这里做摘要省不下什么，却可能把要改的地方藏起来）
3. `AskUserQuestion` 三选一：**A. 确认执行** / **B. 我先修改** / **C. 暂不执行**
4. 只有选 A → 同一条命令**加 `--execute` 重跑**；选 B / C → 落草稿到
   `cann-ops-report/issues/replies/<repo>/<id>.draft.md` + 告知路径，流程结束，**不调任何 API**

本地副作用（建 git 分支、执行 shell 清理）同样先确认：`track plan` 会把这类动作标成
`needs_user_confirmation` 并列出具体命令。

## 主流程

| 步 | 做什么 | 命令 / 去向 |
| --- | --- | --- |
| P0 | 范围确认 | `<python> <skill>/scripts/track.py scope` |
| P1 | 拉评论 + issue 开关状态 | 见 [references/register-and-fetch.md](references/register-and-fetch.md) |
| P2 | 方案理解（你自己读，见下） | 落 `plans/<issue_id>.json` |
| P3 | 应用方案 + 复测 | 见 [references/apply-and-retest.md](references/apply-and-retest.md) |
| P4 | 写 FAQ + 回写社区 | 同上 |
| P5 | 收尾汇总 | 见下 |

### P0 — 范围确认

`track scope` 按 status 分组打印（⏳ 等待回复 / 🔁 有回复待处理 / 🔁 方案已选等复测 /
❌ 复测失败待追问 / ✅ 已闭环；`deleted_upstream` 自动隐藏）。转述给用户后问范围：

```
要查哪些？
  A. 全部查（跳过已闭环）    B. 只查等待回复的    C. 只查某些 repo（请指定）
```

**不要在这里问 SOC**——留到 P3.0 自动发现。

### P2 — 方案理解（你自主完成）

对每个有外部评论的 issue，**自己读 `comments[i].body`**，回答三问：

**① 有没有给「动作」？**
- 「我们会修 / PR 进行中 / 等下个版本」→ `pr_pending`（actionable=False），仅展示
- 「请提供更多日志 / 能否复现」→ `discuss`（actionable=False），仅展示
- 「做 X / 执行 Y / 用 Z」→ 有动作，进 ②

**② 动作是什么形态？** 选一个 kind：`env`（设环境变量）· `build_flag`（构建参数）·
`cmd_arg`（命令行参数）· `clean`（清理命令）· `patch`（代码 diff）· `upgrade`（切版本）。

**③ confidence**：评论作者带 MEMBER / OWNER / COLLABORATOR → `high`；
普通贡献者 → `med`；actionable=False → 不算候选。

六种 kind 的判例、**没见过的新形态怎么归类**、候选清单的展示格式与 `plans/` 落盘结构，
全在 [references/solution-kinds.md](references/solution-kinds.md)——**判 kind 时读它**。
**永远不要**因为「形态没见过」就放弃一个可执行的方案。

### P5 — 收尾汇总

```
=== cann-issue-track 汇总 ===
共查 N 个 issue：
  ✅ X 已 PASS 闭环（已 close）
  🟡 P partial-PASS（原问题修好，已开 follow-up issue）
  ❌ Y FAIL 已追问     ⏳ Z 还没有社区回复
  📌 W pr_pending（等上游合并）     ⏭ V 已跳过
FAQ 新增 X 条 → 查看 cann-ops-report/faq/FAQ.md
follow-up issue：
  - <repo>/<op> → <new_issue_url>  (follow-up to #<original>)
```

## 错误处理速查

| 情况 | 处理 |
| --- | --- |
| `state.json` 不存在 | 手动注册流，或先跑 cann-issue-report |
| `track fetch` 报 `fetch_failed` | warning + 跳过，**status 不变** |
| issue 已被删除（404） | 已自动标 `deleted_upstream`，跳过 |
| `track plan` 退 1 + unknown kind | 检查选的 kind 是否在支持列表内 |
| `track plan` 退 1 + refusing destructive | 检查 clean 命令是否针对 `/`、`/home` 等 root |
| `track plan` 退 1 + 非 git 目录 | 询问正确 `repo_path` |
| `track retest` 返回 `verdict=ERROR` | 复测本身没跑成，修掉再来，**不回评上游** |
| `track retest` 返回 `verdict=UNCERTAIN` | 人工看日志定性后再决定走哪条分支 |
| `track finish/followup` 上游 API 失败 | 告知用户评论未发出，展示 dry-run 打印过的正文 |
| token 未设置 | P1 前检测，提示 `export GITEE_TOKEN=…` |
| `track discover` 的 `missing` 非空 | 此时（且只有此时）才 `AskUserQuestion`，一次问完 |

## 红线

- **方案理解不得退化成正则匹配**：自己读评论判 kind，不引入任何关键词分类器
- 不读原仓源码做新诊断（那是跑测侧的职责）
- 不修改原仓工作区（源码方案一律走新分支）
- 不替代 cann-issue-report 提新 issue（只处理已提交的；partial-PASS 的 follow-up 是唯一例外）
- 不持久化 token 到文件（仅读环境变量）
- 不覆盖跑测侧写的 `run_state.json`（只追加 retest 记录）
- `clean` kind 不允许针对 `/`、`/home`、`/root`、`/usr`、`/etc`、`/var`、`/opt` 的
  `rm` 或 `find -delete`
- 任何外发动作未经用户选 A 一律不真发

## 参考资料

- [references/register-and-fetch.md](references/register-and-fetch.md) — 手动注册流、
  `track fetch` 的六种 `classification` 与处置。**没有 state.json、或进 P1 时读**
- [references/solution-kinds.md](references/solution-kinds.md) — 六种 kind 的完整判例、
  没见过的新形态怎么归类。**P2 判 kind 时读**
- [references/apply-and-retest.md](references/apply-and-retest.md) — P3 自动发现、
  建计划的四类确认、复测五态判定、P4 三分支回写。**P2 选定方案后读**
- [references/partial-pass.md](references/partial-pass.md) — partial-PASS 必须先开
  follow-up issue 的完整流程。**`verdict=PARTIAL` 时读**
