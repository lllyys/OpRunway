# 手动注册与拉评论

前置检查发现 `state.json` 不存在、或 P1 拉评论时读这份。

## 手动注册流

`CWD/cann-ops-report/issues/state.json` 不存在或为空时 `AskUserQuestion`：

```
还没有已提交 issue 的记录。你是否已在浏览器里手动提过 issue？
  A. 是，我来提供 issue URL，帮我注册到记录里
  B. 还没提过，先去跑 cann-issue-report
```

选 B → 退出。选 A → 请用户逐条给 issue URL，对每条**先尝试自动发现元数据**：

- curl 拿 issue body：
  `https://api.gitcode.com/api/v5/repos/<owner>/<repo>/issues/<num>?access_token=$GITCODE_TOKEN`
- 从 URL 末段推 repo
- 从 title（常形如 `[Bug] <op>: …`）推 op
- 从 body 表格推 failure_type 与 SOC

推断结果用 `AskUserQuestion`（候选 + Other）确认后：

```bash
<python> <skill>/scripts/track.py register --repo <repo> --op <op> \
  --failure-type <ft> --issue-url <url> --soc <soc>
```

SOC 一并存进 `state.json`，下次复测无需再问。告知「已注册 N 条记录」，继续 P0。

## P1 — 拉评论与开关状态

```bash
<python> <skill>/scripts/track.py fetch --repo <repo> --op <op> \
  --failure-type <ft> --issue-url <url> [--submitter <你的账号>]
```

一条命令做完：拉评论 + issue 开关状态 + body、落盘
（`comments/<repo>/<id>.json`、`bodies/<repo>/<id>.txt`）、回写 `last_checked_at`，
并给出 `classification`。

| `classification` | 含义 | 处理 |
| --- | --- | --- |
| `closed_upstream` | 上游 maintainer 已关（不是本 skill 关的） | 已自动回写状态，提示用户，**跳过 P2** |
| `deleted_upstream` | issue 已被删（404） | 已自动标记，跳过 |
| `fetch_failed` | 网络 / token 问题 | 打 warning 跳过，**status 不变**——网络问题不能记成业务状态 |
| `no_reply` / `self_only` | 无回复 / 只有自己的回复 | 跳过 |
| `has_external_comments` | 有外部评论 | 进 P2 |

**开关状态优先于评论分类**：issue 已 closed 时，哪怕有新评论也不进 P2。
这条优先级写在 `track fetch` 里，不用自己排。

`--submitter` 不给就识别不出 `self_only`，这时全部评论都会进 P2 交给你判——
宁可多看一眼，不会漏掉真回复。
