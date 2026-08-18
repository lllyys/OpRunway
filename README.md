# OpRunway

上游社区算子验收 skill [repo-task-atk-test](https://gitcode.com/Justbin/repo-task-atk-test) 的开发与运行工作区。

- `plugin/skill/repo-task-atk-test/` —— 上游 `skill/` 的逐字镜像（基线见 `plugin/.claude-plugin/upstream.json`）。
- `plugin/.claude-plugin/` —— 本仓 overlay：Claude 插件 manifest 与 upstream 基线记录。
- `repos/repo-task-atk-test/` —— 上游完整 clone（ignored），含 ATK submodule 源码与 docs，只读参考。
- 仓规见 `AGENTS.md`；开发流水与待办见 `dev-doc/`；历史文档见 `archive/`。

作为 Claude Code 插件加载后，唯一入口是 skill `/oprunway:repo-task-atk-test`：给它一份任务书与配对的
算子源码，它按 S1–S5 在 NPU 目标环境完成用例生成、编译部署、精度性能测试并输出结论。ATK 与 CANN
属环境前置，安装方式见上游 README 与其 `docs/QUICKSTART.md`。
