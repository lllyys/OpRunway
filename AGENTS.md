# AGENTS.md — OpRunway 仓规

**全程中文，称呼用户 Mr.0。全程使用 Codex。** 实施、审核和隔离验收均不调用 Claude、Grok 或跨模型委派插件。
共享仓规只在这里维护；上游或历史文档中的 Claude 分工不适用于本项目。

- 项目 skills 从 `.agents/skills/` 发现；算子 skills 链接到 `plugin/skill/`，不复制实现。
- 编辑 skill 前使用可用的 `skill-creator`；修改文档时读 `.codex/rules/skill-authoring.md`、
  `.codex/rules/zh-writing.md` 和 `.codex/rules/prose-style.md`，不依赖自动规则加载。
- 文档检查使用 `.codex/hooks/doc_style_lint.py`，预算检查使用 `.codex/hooks/skill_budget_lint.py`；
  按环境规则在远程执行。这些是显式检查命令，不是自动钩子，不要求 Claude 收据。
- 全局可见的 `cc-suite-*` 和 Grok 委派 skills 不用于本项目；全局设置不改。

## 1. 这个仓是什么

OpRunway 是上游社区算子验收 skill（`gitcode.com/Justbin/repo-task-atk-test`）的开发与运行工作区。
验收怎么跑、判据是什么，以 对应的 `plugin/skill/<skill-name>/SKILL.md` 及其 `references/`、`scripts/`
为准；本文件只管仓的组织、同步与纪律，不重复也不改写 skill 内规则。

## 2. 结构与镜像

- `plugin/` 是上游根的逐字镜像（`skill/`、`docs/`、`CLAUDE.md`、`README.md`、`.claude/`、
  `.claude-plugin/plugin.json`、`conftest.py`、`pytest.ini`；`third_party/` 与 git 元文件除外）。
  `plugin/.claude-plugin/upstream.json` 是本仓 overlay（upstream 基线记录）；`plugin.json`
  自上游在 `.claude-plugin/` 落了自己的 manifest 后随镜像同步，不再是本仓自维护件
  （2026-09-14 同步 main 时对齐，旧文「上游永不占用该路径」自此作废）。
- 发布切片：`plugin/.claude-plugin/` 与 `plugin/skill/` 是唯一测试/部署发布物；`docs/`、`CLAUDE.md`、
  `README.md` 是开发件，不进任何测试部署。
- 验收零上下文：一次验收的完整契约就是 `SKILL.md` 加 `references/` 加 `scripts/`，不多一个字。
  `plugin/CLAUDE.md` 与 `plugin/docs/` 是开发件，任何跑测或验收都不得读取、引用或据以判断——
  隔离通路靠不分发开发件实现，不向验收会话补充开发上下文。正式验收一律走 `isolated-acceptance` 的无头通路，不在本仓 session 里跑。
  若一次验收非读开发件不可，那是 `SKILL.md` 自足性有缺口，应修 skill 并提 PR 回上游，不是补喂上下文。
- `.claude-plugin/` 保留上游兼容及镜像身份，不作为 Codex 加载入口。Codex 从
  `.agents/skills/` 加载；隔离会话只链接仓外发布副本。`upstream.json` 继续记录镜像来源。
- 上游完整 clone 在 ignored `repos/repo-task-atk-test/`（含 ATK submodule 源码），只读参考。
  上游开发期红线与设计原则随镜像分发：修改 `plugin/` 前先读 `plugin/CLAUDE.md` 与
  `plugin/docs/development/`；开发时显式读取这些文件，不依赖 Claude 子目录记忆。这些原则约束 skill 开发；与本文件冲突时以本文件为准并记录张力。Codex 迁移配置放在本仓根，不改变上游镜像的兼容性。
- 判据的确定性由 skill 自带 `scripts/`（机械门）与 `tests/` 承担；agent 不得绕过机械门，也不得在
  `plugin/` 外另建一套生成或裁决实现。
- 同步上游：先 `git -C repos/repo-task-atk-test fetch`，再在仓根执行
  `git -C repos/repo-task-atk-test diff --binary <旧基线> <新基线> -- skill/ docs/ CLAUDE.md README.md .claude/ .claude-plugin/plugin.json conftest.py pytest.ini | git apply --3way --directory=plugin`
  ——`git diff` 在上游 clone 里跑（基线是上游 commit），`git apply` 在本仓根跑。完成后更新
  `plugin/.claude-plugin/upstream.json` 的 `baseline` 与 `mirror_commit`。基线差距过大、
  3way 应用不可靠时按 2026-09-14 先例整树替换：镜像面全量换成上游新基线，再把本仓超前的
  skill 目录（未合入上游的 PR 内容）checkout 回本仓版本，diff 核零漂移后落一个同步 commit。
- 提 PR：以 `plugin/.claude-plugin/upstream.json` 的 `mirror_commit`（本仓镜像与上游基线完整一致的那个提交）
  为基线，`git diff --binary --relative=plugin <mirror_commit> HEAD -- plugin/skill/ plugin/docs/ plugin/CLAUDE.md plugin/README.md plugin/.claude/ plugin/conftest.py plugin/pytest.ini`
  得到 patch，在 fork（brian66237）里从上游 `baseline` 切分支应用；该 pathspec 即公私边界
  （`plugin.json` 虽随镜像但由上游维护 skills 登记，PR 不主动改它）。

## 3. 环境与权限

- Build、用例生成、测试与正式验收全在远程 NPU 目标环境执行；本机只编辑、Git、只读探测。
- ATK、CANN、Python 依赖属环境前置，不进本仓，也不由 plugin 安装。
- 私有主机、容器、路径只放 ignored `.oprunway/real-machine.env`；秘密不入仓。该文件存在时，远端操作
  先读 `OPRUNWAY_MACHINE_PROTECTED_ROOTS`；保护根及其子目录永远只读。
- Clone、checkout、build、真机执行、删除/覆盖、远端环境修改与发布须有用户授权；授权不扩张到其它目标。
- 不 push、不 merge，除非用户明示；commit 不加 AI 署名或 trailer。

## 4. 文档纪律

- 开发记录只写 `dev-doc/`；每次落地在 `dev-doc/oprunway-changes-brief.md` 顶部追加倒序摘要；
  待办唯一入口 `dev-doc/oprunway-todo.md`。
- `archive/` 只存历史文档，不作现行依据。
- 报告与验收产物只落 ignored `reports/`。

## 5. 发布前检查

Push 前对自上次 push 以来的改动做一轮 audit → fix → verify；一轮即停，剩余问题如实报告。
局部证据或单阶段跑通不得描述成算子正式通过。

评审的工作流、八个维度与触发条件见 `.codex/rules/codex-review.md`；audit 的
fix 段同受八维约束（见该文件）。
