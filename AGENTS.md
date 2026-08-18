# AGENTS.md — OpRunway 仓规

**全程中文。** 仓根 `CLAUDE.md` 路由到本文件；面向所有 agent 的共享仓规只在这里维护。

## 1. 这个仓是什么

OpRunway 是上游社区算子验收 skill（`gitcode.com/Justbin/repo-task-atk-test`）的开发与运行工作区。
验收怎么跑、判据是什么，以 `plugin/skill/repo-task-atk-test/SKILL.md` 及其 `references/`、`scripts/`
为准；本文件只管仓的组织、同步与纪律，不重复也不改写 skill 内规则。

## 2. 结构与镜像

- `plugin/skill/repo-task-atk-test/` 是上游 `skill/` 的逐字镜像；`plugin/.claude-plugin/` 是本仓
  overlay（manifest 与 upstream 基线记录），上游永不占用该路径。
- 上游完整 clone 在 ignored `repos/repo-task-atk-test/`（含 ATK submodule 源码与 docs/），只读参考；
  上游 `CLAUDE.md` 是他们的开发期仓规，不 import、不构成本仓规则。
- 判据的确定性由 skill 自带 `scripts/`（机械门）与 `tests/` 承担；agent 不得绕过机械门，也不得在
  `plugin/` 外另建一套生成或裁决实现。
- 同步上游：`git -C repos/repo-task-atk-test fetch` 后，用
  `git diff --binary <旧基线> <新基线> -- skill/ | git apply --3way --directory=plugin`，
  再更新 `plugin/.claude-plugin/upstream.json` 里的基线。
- 提 PR：`git diff --binary --relative=plugin <导入基线提交> HEAD -- plugin/skill/` 得到 patch，
  在 fork（届时再建）里从上游基线切分支应用；`plugin/skill/` 路径限定即公私边界。

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
