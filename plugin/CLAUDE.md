# CLAUDE.md — 社区算子 Skill 仓

每次会话自动全量加载。**这里只放所有 skill 共用的开发约束和最简背景**，
任何只服务一个 skill 的东西都下沉到那个 skill 的 `CLAUDE.md`，
任何只在特定场景用到的东西都下沉到 `docs/development/`。

写本仓任何 skill 文档前，形式规范已随下面这行导入常驻上下文，不需要主动去读：

@.claude/rules/skill-style.md

一句话摘要：`SKILL.md` 是路由器不是知识库，知识放 `references/`，指令必须具体到
能直接执行。规范取自 `cannAgent/cannbot-skills`，不做机械判定——文风不可机检，
自检清单在规范末尾。

## 本仓是什么

| skill | 做什么 | 开发规则 |
| --- | --- | --- |
| `repo-task-case-gen` | 按社区任务书生成 ATK 用例并冻结 golden | [CLAUDE.md](skill/repo-task-case-gen/CLAUDE.md) |
| `repo-task-atk-accept` | 编译部署算子工程，跑精度与性能，出验收结论 | [CLAUDE.md](skill/repo-task-atk-accept/CLAUDE.md) |
| `repo-task-doc-write` | 把需求写成可验收的任务书 | [CLAUDE.md](skill/repo-task-doc-write/CLAUDE.md) |

**使用态解耦，开发态不解耦。** 三个 skill 单独都能用，`SKILL.md` 里不许写
「先去跑另一个 skill」这种前置；但生成侧与跑测侧在开发态靠**用例包**有硬契约，
改它要两侧同时动、两侧都在真机上复跑。契约清单与纪律见
[docs/development/case-package-contract.md](docs/development/case-package-contract.md)。

**目录级 `CLAUDE.md` 由 Claude Code 按需加载**，写进本文件的每一行则是每次会话
都要付的常驻成本。所以这里只留所有 skill 共用的东西，**具体到某个 skill 的
一律下沉**——脚本名、行号、字段名都不该出现在本文件里。

## 文件结构

```
repo-task-atk-test/
├── CLAUDE.md                    # 本文件：仓级共用约束
├── .claude/rules/skill-style.md # skill 形式规范，被本文件 @ 导入
├── README.md                    # 三个 skill 的门面与安装
├── skill/<name>/                # 一个 skill 一个目录，结构见 skill-style.md
├── docs/
│   ├── skills/<name>/           # 使用者设计与上手文档
│   └── development/             # 开发环境、架构演进、仓库卫生
└── third_party/ATK/             # 只读 submodule
```

## 开发流程

**不做 TDD。** 改动的验证标准是**在真机上跑通**，不是单元测试通过。
本仓不建 `tests/`，也不为脚本补单测——上一版 950 个单测没拦住任何一个
真机上暴露的缺陷，因为那些缺陷全在环境、路径耦合和 ATK 行为上。

1. 改 `SKILL.md` 或 `references/` 前先回答：**没有它，零上下文 agent 会在哪一步卡住？**
   答不上来就不加
2. 改脚本后推到远程实跑，用真实算子验证，不靠本地 mock
3. 真机跑出来的事实写进对应 skill 的 `CLAUDE.md` 的「真机验证过的事实」表，
   带出处（源码路径行号，或标「实测」）

远程机地址、conda 路径、CANN 与 ATK 版本核对方式见
[docs/development/dev-environment.md](docs/development/dev-environment.md)，
**跑第一条远程命令前必读**。

## 新增一个 skill

四步，缺第 3 步 skill 装不上而且不报错：

1. 建 `skill/<kebab-case-name>/`。目录名必须与 `SKILL.md` frontmatter 的 `name`
   逐字相同，不同名 Claude Code 不认
2. 按 `.claude/rules/skill-style.md` 的骨架写 `SKILL.md`，写完过一遍它末尾的自检清单
3. 在 `.claude-plugin/plugin.json` 的 `skills` 数组加一行 `"./skill/<name>"`，
   并在 `README.md` 首表加一行。**漏了 plugin.json 这行，skill 静默不加载**，
   表现是 `/` 列表里看不到它，没有任何报错
4. 有专属红线、设计取舍或真机事实时才建 `skill/<name>/CLAUDE.md`；
   只有一两条规则就写进 `SKILL.md`，不建空壳

登记完对一下数，两个数不等就是漏了：

```bash
grep -c '"\./skill/' .claude-plugin/plugin.json   # plugin.json 登记数
ls -d skill/*/ | wc -l                            # 实际目录数
```

新 skill 的 `CLAUDE.md` **不要复制仓根这份的任何内容**。同一条规则在上下文里
出现两次，agent 要花预算判断两处是否冲突。它只写这个 skill 独有的部分。

## 当前状态

三个算子的真机跑通结果记在
[docs/development/architecture-log.md](docs/development/architecture-log.md)。

**最后更新：** 2026-08-25（仓根只留共用约束，契约与环境下沉 docs/development）
