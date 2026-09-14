---
name: cann-issue-report
description: 把 CANN 算子跑测产生的失败算子转成上游 GitHub / Gitee / GitCode 社区可受理的 issue 草稿，并支持半自动提交与跨轮去重。涉及"给失败算子提 issue / 向社区报告失败 / 上报 bug 给开源社区 / 把跑挂的算子反馈给上游 / report failures to upstream"等意图时使用本 skill；只想查已提 issue 的回复与复测时改用 cann-issue-track。
---

# 失败算子上报社区

把跑测记下来的失败算子转成上游社区 issue。**默认只生成草稿**，由用户决定「我自己提」
还是「你帮我提」。本地 `state.json` 跨多轮跑测去重，同一个算子的同一种失败不会重复上报。

## 入口参数

| 参数 | 含义 | 取值约束 | 初值推断 |
| --- | --- | --- | --- |
| `<skill>` | 本 skill 的安装路径 | 命令都在**项目根**跑，产物落 `CWD/cann-ops-report/issues/` | 从本文件位置得出 |
| `<python>` | 解释器 | 只用 stdlib | 默认 `python3` |
| `repo=path` | 失败算子所在仓的本地源码路径 | 目录里有 `.git`，用来推上游 owner/repo | 用户给出 |
| `soc` | 本轮跑测的 SOC | `ascend910b` / `ascend950` / … | **必须问用户，不猜**；它要进 issue 正文的环境表 |

## 前置检查

本 skill 读的是跑测状态文件 `CWD/cann-ops-report/<repo>/test/run_state.json`。
`report.py scope` 找不到它会直接 fatal 并说明原因——**先跑一次 scope 拿范围**，
不要自己去翻目录。

`CWD/cann-ops-report/<repo>/scan/_intermediate.json` 是可选输入：有就在草稿里带上
950 特性命中，没有该段留空，不影响流程。

## 主流程

所有机械动作都是 `scripts/report.py` 的子命令，`--help` 可查全部参数。
机读结果走 stdout（JSON），人读提示走 stderr。**不要在对话里手写 python 调这些模块**——
等于把同一段代码读一遍再写一遍。

| 步 | 做什么 | 命令 |
| --- | --- | --- |
| P0 | 圈定范围 | `report.py scope` |
| P1 | 推上游 platform/owner/repo | `report.py resolve --repo-mapping <repo>=<path> …` |
| P2 | 去重（自动，无独立命令） | 由 P3 的 `--force` 开关控制 |
| P3 | 生成草稿 | `report.py drafts --repo-mapping … --soc <soc>` |
| P4 | 审阅 + 提交 | 见 [references/submit-flow.md](references/submit-flow.md) |

### P0 — 圈定范围

```bash
<python> <skill>/scripts/report.py scope
```

它按 `(repo, failure_type)` 分组打印失败算子。把结果转述给用户，用 `AskUserQuestion` 问：

```
发现 N 个仓的失败算子：
  ops-transformer: X 个（BUILD_FAIL=A, RUN_EXIT_FAIL=B, …）
  ops-cv:          Y 个（…）
全部走 issue 流程吗？
  A. 全部    B. 只选某些仓 / 某些失败类型（接下来给我列表）
```

### P1 — 推上游平台

```bash
<python> <skill>/scripts/report.py resolve --repo-mapping ops-cv=/path/to/ops-cv …
```

它先查 `repos.json` 缓存，miss 就读 `git remote get-url origin` 认
github / gitee / gitcode 三个域名，结果写回缓存。

**`unresolved` 非空时把这些仓攒成一次 `AskUserQuestion` 问完**（每仓一问，最多 4 仓一批）。
逐仓来回问要重放多轮上下文，而这些问题彼此无依赖，本来就该合并。

### P3 — 生成草稿

```bash
<python> <skill>/scripts/report.py drafts \
  --repo-mapping <repo>=<path> … --soc <soc> [--force]
```

**一个失败算子一篇草稿**，落 `cann-ops-report/issues/drafts/<repo>/per_op/<op>__<失败类型>.md`。
这是开源社区的基本规范：维护者要独立追踪、关闭、打 label，一篇混多个算子的 issue 闭不了环。

已提交过的 `(repo, op, failure_type)` 默认跳过并在 stderr 报数；`--force` 才重提。

草稿生成后先让用户过目：

```
已为 N 个失败算子生成草稿。草稿路径：cann-ops-report/issues/drafts/
  A. 直接进入提交流程    B. 我先看一下，改好再告诉你
```

选 B → 告知路径等用户回来，用户用自然语言提改法（「把 grouped_matmul 那篇标题改成…」），
直接 Edit 对应文件。选 A → 进 P4。

## 边界与禁忌

- ✗ 不重跑测试、不读算子源码做新诊断（那是跑测侧的职责）
- ✗ 不修改跑测侧的 `run_state.json` 与 `logs/`
- ✗ 不在本 skill 目录或 `cann-ops-report/` 任何位置持久化 token
- ✗ 不假设 SOC，必须问用户
- ✗ 不自动创建上游不存在的 label（不存在就静默丢弃，`submit.py` 已经这么做）
- ✗ 用户选「我先看看」或「取消」时不调任何上游 API

## 数据来源

| 来源 | 强弱 | 用途 |
| --- | --- | --- |
| `cann-ops-report/<repo>/test/run_state.json` | 强 | 失败算子与状态，没有就没法起草 |
| `cann-ops-report/<repo>/test/logs/<op>.*.log` | 强 | 错误日志摘录（grep `ERROR\|undefined\|failed\|exit=`，≤80 行） |
| `cann-ops-report/<repo>/test/failures/<op>.md` | 弱 | 「已尝试的诊断」段 |
| `cann-ops-report/<repo>/test/explorations/<op>.md` | 弱 | SOLVED → 加「已验证修复方案」段；UNSOLVED → 加「已排除路径」段 |
| `cann-ops-report/<repo>/scan/_intermediate.json` | 弱 | 950 特性命中、`is_delegated` |
| `git -C <repo_path> remote get-url origin` | 弱 | 推 platform |
| `$ASCEND_HOME_PATH/version.info` | 弱 | CANN 版本 |

## 产物

```text
cann-ops-report/issues/
├── state.json                                  去重与提交状态（cann-issue-track 也读它）
├── repos.json                                  repo → (platform, owner, repo) 缓存
├── drafts/<repo>/per_op/<op>__<失败类型>.md      草稿，一算子一篇
└── submitted/<repo>/<id>.json                  已提交记录
```

## 参考资料

- [references/submit-flow.md](references/submit-flow.md) — 提交前预览的必走步骤、
  两条提交路径的命令、三个平台各自的前置与实测约束、token 写入 shell 的处置。
  **进入 P4 才读**
