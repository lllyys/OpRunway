# OpRunway

上游社区算子验收 skill [repo-task-atk-test](https://gitcode.com/Justbin/repo-task-atk-test) 的开发与运行工作区。

- `plugin/skill/` —— 上游 `skill/` 的逐字镜像（基线见 `plugin/.claude-plugin/upstream.json`）。
- `plugin/.claude-plugin/` —— 上游兼容 manifest 与镜像来源记录。
- `repos/repo-task-atk-test/` —— 上游完整 clone（ignored），含 ATK submodule 源码与 docs，只读参考。
- 仓规见 `AGENTS.md`；开发流水与待办见 `dev-doc/`；历史文档见 `archive/`。

按任务类型选择 ATK、BLAS 或 Solver 的用例生成及验收 skill。ATK 与 CANN 属环境前置。

## Codex 项目入口

本项目使用 Codex。工作规则见 AGENTS.md；项目 skills 从 `.agents/skills/` 加载，链接到上游镜像实现。
隔离验收入口见 `.agents/skills/isolated-acceptance/SKILL.md`。文档检查和审核规则见 `.codex/rules/`。
项目配置禁用 Claude MCP 和 Grok 委派插件；全局配置不变。旧收据钩子保留空操作脚本，以兼容已启动会话。
