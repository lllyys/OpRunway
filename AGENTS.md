# AGENTS.md — OpRunway 仓规

**全程中文。** 仓根 `CLAUDE.md` 路由到本文件；面向所有 agent 的共享仓规只在这里维护。

## 1. 这个仓是什么

OpRunway 是上游社区算子验收 skill（`gitcode.com/brian66237/repo-task-atk-test`）的开发与运行工作区。
验收怎么跑、判据是什么，以 `plugin/skill/repo-task-case-gen/` 与
`plugin/skill/repo-task-atk-accept/` 各自的 `SKILL.md`、`references/`、`scripts/` 为准；
本文件只管仓的组织、同步与纪律，不重复也不改写 skill 内规则。

## 2. 结构与镜像

- `plugin/` 的 `docs/`、`CLAUDE.md`、`README.md`、`.claude/rules/`、`tests/` 与任务书 skill
  延续上游镜像边界；自 S11 起，上游验收单树按骨架归属映射为 `repo-task-case-gen/` 与
  `repo-task-atk-accept/` 两个平级目录，`skill/` 不再逐字同构。`plugin/.claude-plugin/` 是本仓
  overlay（manifest 与 upstream 基线记录），上游永不占用该路径。
- 发布切片：`plugin/.claude-plugin/` 与 `plugin/skill/` 是唯一测试/部署发布物；`docs/`、`CLAUDE.md`、
  `README.md` 是开发件，不进任何测试部署。
- 验收零上下文：一次验收的完整契约就是 `SKILL.md` 加 `references/` 加 `scripts/`，不多一个字。
  `plugin/CLAUDE.md` 与 `plugin/docs/` 是开发件，任何跑测或验收都不得读取、引用或据以判断——
  隔离通路靠不分发做到物理隔离；在本仓内直接发起验收时子目录记忆会注入 `plugin/CLAUDE.md`，
  此时必须当它不存在。正式验收一律走 `isolated-acceptance` 的无头通路，不在本仓 session 里跑。
  若一次验收非读开发件不可，那是 `SKILL.md` 自足性有缺口，应修 skill 并提 PR 回上游，不是补喂上下文。
- 两个 `.claude-plugin/` 身份不同，别混：仓根那个装 `marketplace.json`，是本机发行清单，不上测试机；
  `plugin/` 下那个装 `plugin.json` 与 `upstream.json`，是加载器的硬依赖，必须随部署分发——缺 manifest
  时 `--plugin-dir` 直接报 "No manifest found"，且上游用单数 `skill/` 而非 Claude 约定的复数 `skills/`，
  只有 manifest 里的 `skills` 数组能指到 skill。`upstream.json` 只含上游 URL 与两个公开 SHA，无私有信息，
  随包分发同时给测试机留下派生基线的 provenance。
- 上游完整 clone 在 ignored `repos/repo-task-atk-test/`（含 ATK submodule 源码），只读参考。
  上游开发期红线与设计原则随镜像分发：修改 `plugin/` 前先读 `plugin/CLAUDE.md` 与
  `plugin/docs/development/`；本仓内读写 `plugin/**` 时 `plugin/CLAUDE.md` 会作为子目录记忆自动
  进入上下文。这些原则约束 skill 开发；与本文件冲突时以本文件为准并记录张力。插件安装态不加载
  plugin 根 `CLAUDE.md`，`claude plugin validate` 对此的警告是预期行为。
- 判据的确定性由 skill 自带 `scripts/`（机械门）与 `tests/` 承担；agent 不得绕过机械门，也不得在
  `plugin/` 外另建一套生成或裁决实现。
- 同步上游：先 `git -C repos/repo-task-atk-test fetch`。同构部分在仓根执行：

  ```bash
  git -C repos/repo-task-atk-test diff --binary <旧基线> <新基线> -- \
    skill/repo-task-doc-write/ docs/ CLAUDE.md README.md .claude/rules/ tests/ \
    | git apply --3way --directory=plugin
  ```

  验收单树先用以下命令列出改动，再按两份骨架的 `skill` 归属映射，`shared` 文件两侧都改：

  ```bash
  git -C repos/repo-task-atk-test diff --name-status <旧基线> <新基线> -- \
    skill/repo-task-atk-test/
  ```

  门禁通过后更新 `plugin/.claude-plugin/upstream.json` 的 `baseline` 与 `mirror_commit`。
- 提 PR：以 `plugin/.claude-plugin/upstream.json` 的 `mirror_commit` 为派生基线，执行：

  ```bash
  git diff --binary --relative=plugin <mirror_commit> HEAD -- \
    plugin/skill/ plugin/docs/ plugin/CLAUDE.md plugin/README.md \
    plugin/.claude/rules/ plugin/tests/
  ```

  该 pathspec 保持不变，仍是完整公私边界。得到的是结构提案 patch；在 fork 里从上游
  `baseline` 切分支应用，并处理路径变更。

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
