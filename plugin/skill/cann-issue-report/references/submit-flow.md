# 提交流程与 token 处置

进入 P4「用户已看过草稿、要提交了」之后读这份。P0–P3 用不上。

## 提交前预览（两条路都要走，不可跳过）

草稿是要发到公开社区的，发出去就撤不回。所以无论用户选哪条路，都先把内容摆到台面上：

逐篇输出**完整标题** + **正文前 15 行** + 行数统计，再 `AskUserQuestion`：

```
即将提交以下 N 篇 issue，请预览确认：

━━━ [1/N] <repo> / <op> ━━━
标题：<草稿实际标题>
摘要：<正文前 15 行>
      …（正文共 X 行，含 Y 行日志摘录）
草稿：cann-ops-report/issues/drafts/<repo>/per_op/<file>.md

确认提交以上 N 篇吗？
  A. 全部提交
  B. 先展开某几篇完整正文（告诉我编号）
  C. 我先看/改草稿，稍后再说
  D. 取消
```

**标题必须完整展示**——它是 issue 的门面，也最容易需要改。正文默认只出前 15 行：
草稿正文里的错误日志摘录在 P3 生成阶段已经过了一遍上下文，逐篇全文复述是纯重复。
用户说「全都给我看完整的」就照办，不受 15 行限制。

| 选项 | 去向 |
| --- | --- |
| A | 继续下面的平台步骤 |
| B | 只把点名的几篇正文完整贴出，再回到本问题 |
| C / D | 告知草稿目录路径，流程结束，**不调任何 API** |

## 用户自己提

```bash
<python> <skill>/scripts/report.py urls --platform <p> --owner <o> --repo <r> \
  [--label bug --label ops-failure] <草稿路径> [<更多草稿>]
```

输出每篇的 `url` 与 `degraded`。`degraded=true` 表示 prefilled URL 超过 7500 字节、
已降级成空白 issue 页——要在交互里告诉用户正文得从草稿里拷。

把 URL 与草稿路径的对照表给用户后，**立即追问，不要等用户主动回来**：

```
请把提交成功的 issue URL 告诉我，我写进记录（后续 cann-issue-track 要用）。
每行一个：<issue_url>  对应草稿：<draft_filename>
没提成功的留空即可。
```

对每个确认的 URL：

```bash
<python> <skill>/scripts/report.py mark --draft <草稿路径> \
  --issue-url <url> --soc <soc>
```

## agent 帮提

| 平台 | 前置 | 命令 |
| --- | --- | --- |
| GitHub | 先 `gh auth status`；未登录则 fatal「先 gh auth login」 | `report.py submit --platform github …` |
| Gitee | 读 `GITEE_TOKEN`；没有则 `AskUserQuestion` 单次 prompt 后用 `--token` | `report.py submit --platform gitee …` |
| GitCode | 读 `GITCODE_TOKEN`；同上 | `report.py submit --platform gitcode …` |

```bash
<python> <skill>/scripts/report.py submit --platform <p> --owner <o> --repo <r> \
  --draft <草稿路径> --soc <soc> [--token <token>] [--label bug]
```

它提交成功后自己回写 `state.json`（`status="submitted"`，供 cann-issue-track 的 P0 状态分组用），
不需要再单独 `mark`。

**两条实测约束已经写进 `submit.py`，不要在别处绕过**：GitCode 走
`api.gitcode.com/api/v5` 子域名（`gitcode.com/api/*` 被 CloudWAF 拦截）；`labels` 传
CSV 字符串而非数组（Gitee 与 GitCode 的 v5 API 传数组都回 422）。

## token 写入 shell 配置

触发条件：用户选了「你帮我提」+ env 里没有对应 token + 单次 prompt 提交成功之后。

```
已用本次会话提供的 token 成功提了 N 个 issue。要不要写入 shell 配置，以后免再问？
  A. ~/.bashrc    B. ~/.zshrc    C. ~/.profile    D. 不用
```

选 A/B/C：先 grep 目标文件有没有 `<env_var>=` 行。有 → 让用户选「覆盖 / 跳过」；
无 → 追加 `export <env_var>=<token>`。写完明确提示「token 以明文存储在该文件中」。
env var 由 `token_helper.env_var_for_platform(platform)` 给出。

选 D → 不动文件。**任何情况下都不把 token 写进 `cann-ops-report/` 或本 skill 目录。**
