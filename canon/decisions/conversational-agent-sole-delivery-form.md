---
title: Conversational agent is the sole delivery form
updated: 2026-07-08
status: proposed
---

# Conversational agent is the sole delivery form

**Decision.** OpRunway 的启用形态是**对话式**：`op-acceptance` agent 是**唯一面向用户的入口**，用户全程只用自然语言给「任务书(md 路径或链接) + PR 链接」。确定性脚本（`plugin/acc-common/*.py`）是 **agent 的内部实现**——agent 用 Bash 幕后跑，**绝不把脚本命令展示给用户、不让手敲、不把「跑脚本」当用法说**；缺信息用对话问。

**Why.** 低门槛 + 可移植：用户只对话、看进展与最终中文报告。脚本随插件**自包含**（在 `plugin/acc-common/`，**不移出**——它是插件自己的实现，agent 靠 `${CLAUDE_PLUGIN_ROOT}/acc-common/...` 引用，移出则装上就跑不了），换运行时只换注册薄壳（见 [[Cross-CLI unified form via neutral AGENTS.md]]）。README（插件 + 仓根）已重写成对话用法、删掉 `python3 …` 脚本示例。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-08 续：形态收敛为对话式）
