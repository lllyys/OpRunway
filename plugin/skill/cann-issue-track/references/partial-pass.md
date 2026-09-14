# partial-PASS 分支（P4）

> **何时读本文件**：`track retest` 返回 `verdict == "PARTIAL"`，即**原 issue 报的 BUILD_FAIL /
> INSTALL_FAIL 已恢复（build + install 都过了），但 run 期出现了新的失败**。
> partial-PASS **不算 PASS 也不算 FAIL，是单独的第三状态**：原 issue 视为已修复并关闭，
> 但 run 期的**全新失败面**必须开 follow-up issue 并链回原 issue。

所有对外写动作（新建 issue / 发评论 / 关 issue）一律走 SKILL.md 的**外发动作确认规则**：
先不带 `--execute` 跑一次（默认 dry-run，只打印）→ 完整展示 → `AskUserQuestion` 三选一 → 仅选 A 才加 `--execute` 重跑。

**顺序不可颠倒**：先建 follow-up，拿到 URL，再关原 issue。没有 follow-up URL 就关原 issue，
等于把新失败面直接丢掉——`track finish --outcome partial` 会因此硬拒（退出码 1）。

## 1. 开 follow-up issue

```bash
# ① dry-run：只打印将要新建的 issue 标题 + 正文
python3 scripts/track.py followup --repo <repo> --op <op> --failure-type <原失败类型> \
  --issue-url <原 issue URL> --soc <soc> \
  --kind <plan.kind> --payload '<plan.payload 摘要>' \
  --log <复测后的 run 日志路径> --op-status <复测后的算子状态，如 RUN_EXIT_FAIL>

# ② 用户确认后加 --execute 真发
python3 scripts/track.py followup ... --execute
```

它会：从原 issue URL 砍出仓 URL → 标题自动带 `(follow-up to #<原 id>)` → 正文由
`reply_builder.build_followup_issue_body` 生成（body 里必带 `Follow-up to <原 issue URL>`，
这是给 maintainer 的关联线索）→ 从 run 日志抽错误片段。

`--execute` 成功后**自动**把 follow-up 注册进 `state.json`
（`failure_type=RUN_EXIT_FAIL`、`parent_issue_url=<原 issue>`、`status=submitted`）——
**红线**：不注册下轮就追踪不到它，所以这一步由脚本包办，不靠人记得做。

用户选 B/C 不新建 → 告知 dry-run 打印的正文，**后续步骤全部中止**。

## 2. 原 issue：partial 回评 + close + 写 FAQ

拿到 follow-up URL 后，一条命令收尾（同样先 dry-run 再 `--execute`）：

```bash
python3 scripts/track.py finish --repo <repo> --op <op> --failure-type <原失败类型> \
  --issue-url <原 issue URL> --outcome partial --soc <soc> \
  --kind <plan.kind> --payload '<plan.payload 摘要>' \
  --log <原始失败日志路径> --followup-url <上一步拿到的 URL> \
  --op-status <复测后的算子状态> --execute
```

`--execute` 时依次做完：写 FAQ（原 issue 的修复方案确实有效，照写）→ 发 partial 回评（含
follow-up 链接）→ 关闭原 issue → state.json 该条标
`status=closed_by_track_issues_partial` + `closed_at` + `followup_issue_url`。

> `--log` 这里给的是**原始失败日志**（FAQ 的 error_signature 要按它算，才能在下次同样的
> build 失败上命中）；步骤 1 的 `--log` 给的是**复测后的 run 日志**（follow-up 报的是新故障）。
> 两者不是同一份，别传混。

## 3. 收尾汇报

P5 汇总里 partial 单列一行，并列出 follow-up 映射：

```
🟡 P partial-PASS（原问题修好，已开 follow-up issue）
follow-up issue：
  - <repo>/<op> → <new_issue_url>  (follow-up to #<original>)
```
