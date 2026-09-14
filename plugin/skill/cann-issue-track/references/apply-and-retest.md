# 应用方案与复测（P3）

P2 选定方案后读这份。

## P3.0 — 自动发现，先于任何询问

```bash
<python> <skill>/scripts/track.py discover --repo <repo> --op <op> \
  --failure-type <ft> --issue-id <id>
```

输出 `{"soc": …, "repo_path": …, "missing": [...]}`。SOC / repo_path / failure_type
都有持久化来源（`state.json` / `run_state.json` / issue body），
**只对 `missing` 里的字段问用户，且同一次 P3 内一次问完。**

## P3.1 — 建计划

P2 选定的方案先落 `cann-ops-report/issues/plans/<issue_id>.json`，然后：

```bash
<python> <skill>/scripts/track.py plan --repo <repo> --op <op> \
  --failure-type <ft> --issue-id <id> --repo-path <path>
```

输出 `{"plan": …, "needs_user_confirmation": bool}`。

| kind | 做什么 | 要用户确认吗 |
| --- | --- | --- |
| `env` / `build_flag` / `cmd_arg` | 只生成 args，不动文件 | 否 |
| `upgrade` | `requires_user_action=True`，提示用户手动 `git pull` | **是**，用户必须确认已切到新版本 |
| `patch` | 在 `repo_path` 建 `track-issue-<id>` 分支 + 写 `.diff` 到 `cann-ops-report/issues/patches/`。**不自动 git apply**，告知分支已建 | **是**，询问是否继续复测 |
| `clean` | 解析清理命令到 `plan.pre_cleanup_commands`；拒绝针对 `/`、`/home`、`/usr`、`/etc` 等 root 的 `rm` / `find -delete` | **是**，展示命令清单让用户确认 |

`needs_user_confirmation=true` → 先把它列出的命令或分支展示给用户点头，再进 P3.2。

## P3.2 / P3.3 — 复测与判定

```bash
<python> <skill>/scripts/track.py retest --repo <repo> --op <op> \
  --failure-type <ft> --repo-path <path> --soc <soc> --plan-file <上一步的 json>
```

内部依次：① 在 `repo_path` 下执行 `pre_cleanup_commands`（每条 timeout 300）→
② 跑一轮示例跑测 → ③ 从 `run_state.json` 读**权威** status。
不 grep stdout，且只认本轮刷新过的记录——否则会把上轮的旧 PASS 当成这次的结果。

`verdict` 直接给出判定，不用自己读 JSON 推：

| verdict | 含义 | 走哪条分支 |
| --- | --- | --- |
| `PASS` | 算子跑通 | P4「复测 PASS」 |
| `PARTIAL` | 原 BUILD/INSTALL 失败已恢复，但 run 期出现**全新失败面** | P4「复测 partial-PASS」 |
| `FAIL` | 原问题依旧，或原本就是 run 期失败且仍失败 | P4「复测 FAIL」 |
| `UNCERTAIN` | 没真正跑起来 / 无强判定信号（空退、无 examples 等） | **先人工看日志定性，不得据此回评上游** |
| `ERROR` | 复测本身没跑成（清理失败 / 超时 / 缺 repo_path） | 修掉环境问题再来，**不回评** |

`--failure-type` 必须传：判 `PARTIAL` 要拿它跟复测后的状态比。缺了会保守退化成 `FAIL`——
宁可漏判，不可误关 issue。

## P4 — 写 FAQ + 回写社区

三个分支同一条命令，只换 `--outcome`。**先不带 `--execute` 跑**（默认 dry-run，只打印不发），
走完「外发动作确认规则」再加 `--execute` 重跑：

```bash
<python> <skill>/scripts/track.py finish --repo <repo> --op <op> \
  --failure-type <ft> --issue-url <url> --outcome pass|partial|fail \
  --soc <soc> --kind <plan.kind> --payload '<plan.payload 摘要>' \
  --log <原始失败日志路径> \
  [--followup-url <partial 分支必填>] [--op-status <复测后状态>] [--execute]
```

| outcome | `--execute` 时做什么 | state.json |
| --- | --- | --- |
| `pass` | 写 FAQ → 发 PASS 回评 → 关 issue | `status=closed_by_track_issues` + `closed_at` |
| `partial` | 写 FAQ → 发 partial 回评（含 follow-up 链接）→ 关原 issue | `status=closed_by_track_issues_partial` + `followup_issue_url` |
| `fail` | 只发追问评论，**不写 FAQ、不关 issue** | **不改**，issue 仍开着 |

回评内容一律落盘 `cann-ops-report/issues/replies/<repo>/<id>.json`。

`--outcome partial` **必须**先有 follow-up issue——没有 follow-up URL 就关原 issue 等于
新失败面直接丢失，命令会硬拒。完整流程见 [partial-pass.md](partial-pass.md)。

`patch` 类无论 PASS 还是 FAIL 都保留已创建的 git 分支，不自动删除。
