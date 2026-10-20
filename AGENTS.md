# AGENTS.md — OpRunway

**全程中文，称呼用户 Mr.0；实施、审核与验收只使用 Codex。** 本文件是项目规则的唯一入口；不修改全局配置。

## 目录

- `plugin/`：上游 `Justbin/repo-task-atk-test` 镜像。保留其 `CLAUDE.md`、`.claude/`、`.claude-plugin/` 兼容文件；本仓根不使用这些入口。
- `.agents/skills/`：Codex skill 入口。上游 skills 链接到 `plugin/skill/`，不复制实现；项目自有 skills 使用实体目录，与上游镜像及参考仓隔离。
- `.worktrees/<任务名>/`：并行工作区，由 Git 忽略；使用 `git worktree add/move/remove` 管理。
- 主仓 `reports/<任务名>/`：集中保存最新交付产物及对应证据；worktree 可通过链接访问。按授权清理旧版本，不删除分支或提交。
- `dev-doc/`：开发记录；每次修改在 `oprunway-changes-brief.md` 顶部追加摘要，待办只写 `oprunway-todo.md`。
- `archive/`：历史材料，不作现行依据。`repos/repo-task-atk-test/`：上游参考 clone，不直接开发。

在 worktree 中，`git rev-parse --path-format=absolute --git-common-dir` 返回共享 `.git` 路径，其父目录为本项目主仓。

## 开发与验收

- 修改 skill 前使用可用的 `skill-creator`；文档规则见 `.codex/rules/skill-authoring.md`、`zh-writing.md`、`prose-style.md`。
- 修改 `plugin/` 前读取 `plugin/CLAUDE.md` 及相关 `plugin/docs/development/`；冲突时以本文件为准。
- 文档与预算检查分别使用 `.codex/hooks/doc_style_lint.py`、`skill_budget_lint.py`，显式执行。
- 正式验收使用 `isolated-acceptance`。只分发 `plugin/skill/` 与 `plugin/.claude-plugin/`，在仓外加载发布副本；不提供仓规、plugin 根开发文档或历史报告。
- 验收依据为被选 skill 的 `SKILL.md` 及其明确引用资源。判定由 skill 自带脚本执行，不绕过，也不在 `plugin/` 外另建生成或裁决实现。
- 构建、用例生成、测试及验收在远程 NPU 环境执行；本机只编辑、Git 和只读探测。ATK、CANN、Python 依赖是环境前置，不随插件安装。
- 私有配置只放主仓 ignored `.oprunway/real-machine.env`，worktree 直接读取。远端操作前核对 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`；保护根始终只读，秘密不入仓。
- Clone、checkout、构建、真机执行、删除覆盖、远端环境修改与发布均须在用户授权范围内。Push、merge 须明确授权；commit 不加 AI 署名或 trailer。
- Push 前按 `.codex/rules/codex-review.md` 完成一轮 Codex 独立审核、修复与复核，剩余问题如实报告。局部验证不代表正式验收通过。

## 上游同步与 PR

镜像范围为 `skill/`、`docs/`、`CLAUDE.md`、`README.md`、`.claude/`、`.claude-plugin/plugin.json`、`conftest.py`、`pytest.ini`。
`plugin/.claude-plugin/upstream.json` 记录上游 `baseline` 和本仓完整镜像提交 `mirror_commit`；manifest 随上游维护。

同步时先 fetch，再在主仓执行：

```bash
git -C repos/repo-task-atk-test diff --binary <旧基线> <新基线> -- skill/ docs/ CLAUDE.md README.md .claude/ .claude-plugin/plugin.json conftest.py pytest.ini | git apply --3way --directory=plugin
```

核对镜像与本仓未合入改动后更新来源记录；三方应用不可靠时，替换镜像范围并恢复本仓未合入的 skill 改动，核对差异后提交。

向上游提 PR 时，从本仓导出以下切片，在 `brian66237` fork 的上游 `baseline` 分支上应用：

```bash
git diff --binary --relative=plugin <mirror_commit> HEAD -- plugin/skill/ plugin/docs/ plugin/CLAUDE.md plugin/README.md plugin/.claude/ plugin/conftest.py plugin/pytest.ini
```

仅提交该切片，不带入项目私有配置；不主动修改上游 manifest。
