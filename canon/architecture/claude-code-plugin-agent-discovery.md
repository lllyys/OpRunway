---
title: Claude Code plugin agents load by directory discovery
updated: 2026-07-10
status: proposed
---

# Claude Code plugin agents load by directory discovery

**在 Claude Code `2.1.206`、当前 OpRunway 插件结构下**：agent 靠**约定目录自动发现**（`agents/*.md`），
`.claude-plugin/plugin.json` **不得声明 `agents` 字段**。四种写法实测：

| `plugin.json` 的 `agents` | 插件本身 | agent 注册 |
|---|---|---|
| `["./agents/x.md"]` | ✅ 加载 | **0（静默不注册）** |
| `["agents/x.md"]`（去 `./`） | ❌ 整个插件加载失败 | — |
| `"./agents/"`（字符串） | ❌ 整个插件加载失败 | — |
| **不写该字段** | ✅ 加载 | **4 ✅** |

第一种最危险：不报错，`claude plugin validate` 照常 ✔、skill 照常在，只有 agent 悄悄归零——而
`/op-acceptance` 第一步就是调 `op-acceptance` primary agent，故该配置下产品入口实际是坏的。
（该字段并非「完全不被读取」：另两种写法能让整个插件加载失败，说明它被读了、只是不据以注册 agent。）

`acc-common/check_manifest_sync.py` 设**反向门**：`plugin.json` 一出现 `agents` 字段即 `STATUS: DRIFT`。
本页只讲加载机制；「注册面 ↔ 磁盘」的比对基准见 [[A gate must validate the object that actually takes effect]]。

**待验证的旁支**（未固化证据，勿据以载重）：`commands/` 与 `skills/` 疑似同样靠约定目录发现，且 `plugin details`
似把 command 计入 Skills 计数；`grill` 等能正常加载 agent 的第三方插件，其 `plugin.json` 观察到也不写 `agents`。

**仓内可核部分**（2026-07-10）：`plugin/.claude-plugin/plugin.json` 不含 `agents` 键；`plugin/agents/` 下存在
4 个 `.md`。**表中的加载行为不可从仓机械复现**——它出自 `claude --plugin-dir ./plugin plugin details oprunway`
与真会话 subagent 列表的实测（`2.1.206`），未固化为可重跑的 fixture，故本页整体停在 `proposed`。
其它 Claude Code 版本未验证。要升 `verified`，须先把四组实验固化成脚本 + 原始输出 + verifier。

**Sources.** [[session 64604f71-dd13-4256-9a74-072fec018b48 · 2026-07-09]]
