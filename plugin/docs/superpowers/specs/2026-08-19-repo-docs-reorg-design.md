# 仓库文档重组设计

**日期：** 2026-08-19
**状态：** 已评审，待实施
**产物：** `README.md`、`CLAUDE.md`、`docs/skills/`、两份 skill 侧 `CLAUDE.md`、一个仓级行文门禁

---

## 0. 一句话

本仓从一个 skill 长成两个，文档结构没跟上——按「谁读、读完要能干什么」重切一遍。

---

## 1. 问题

### 1.1 三处失配

`skill/` 下已经有两个 skill，但三层文档都还是单 skill 时代的形状：

| 文件 | 现状 | 问题 |
| --- | --- | --- |
| `README.md`（120 行） | 通篇只讲 `repo-task-atk-test` | `repo-task-doc-write` 在仓门面上不存在 |
| `docs/QUICKSTART.md`（96 行） | 同上 | 同上 |
| `CLAUDE.md`（347 行） | 约八成是验收 skill 专属 | 写任务书 skill 时也全量入上下文 |

`skill/repo-task-doc-write/CLAUDE.md` 已经在做分层，开头就写「复用仓根规范」。
失配不在它，在根那层没拆干净——它复用的那份里混着大量与它无关的内容。

### 1.2 已有的存量重复

`README.md` 与 `docs/QUICKSTART.md` 各抄了一份 S1-S5 阶段表。两处措辞已经开始
分叉，谁也不是权威。

### 1.3 安装说明假定了所有人都要 ATK

现在的 README 第一步就是 `git clone --recursive`，第二步从源码构建 ATK。

`repo-task-doc-write` 只依赖 python3 标准库，不碰 NPU、CANN、ATK。让它的使用者
先装一遍 ATK 是纯粹的入门税。

---

## 2. 设计

### 2.1 三层 CLAUDE.md，按目录自动加载

Claude Code 的 `@path` 导入是无条件的，写进根 `CLAUDE.md` 就每次全量入上下文，
做不到按开发目标分支加载。**目录级 `CLAUDE.md` 本来就是按需加载的**——碰
`skill/repo-task-doc-write/` 下的文件时它才进上下文。

所以不需要新机制，只需要把内容放对位置：

| 文件 | 装什么 | 何时入上下文 |
| --- | --- | --- |
| `CLAUDE.md` | 仓级共同规范：本仓是什么、行文、开发流程、开发态启动、什么进仓 | 每次会话 |
| `skill/repo-task-atk-test/CLAUDE.md`（新增） | 四条红线、S1-S5、产物契约骨架、判据机械化、L0-L3、行文欠账棘轮 | 碰该目录 |
| `skill/repo-task-doc-write/CLAUDE.md`（已存在） | 四条红线 A-D、骨架、素材只读 | 碰该目录 |

根 `CLAUDE.md` 新增一节「改哪个 skill 读哪份」，说明上面这个加载机制，
免得下一个人以为要手写 import。

### 2.2 根 CLAUDE.md 的去向表

| 现有小节 | 去向 |
| --- | --- |
| 头部 + §0 一句话 | 留根，改写成两个 skill 并列 |
| §1 验收流程 S1-S5 | → `repo-task-atk-test/CLAUDE.md` |
| §2 四条红线 | → 同上 |
| §3 架构理念 3.0–3.5 | → 同上 |
| §4 仓库树 / 什么进仓 / 开发态启动 / ATK 版本锁 | 留根 |
| §4 skill 内部目录树 | → `repo-task-atk-test/CLAUDE.md` |
| §5 开发流程 | 留根，两个 skill 都适用 |
| §6 术语表 | 「骨架」留根，其余验收专属项搬走 |
| §7 常见问题 | → `repo-task-atk-test/CLAUDE.md` |
| §8 当前测试状态 | 留根，数字重新实测 |
| §8 行文欠账棘轮 102 处 | → `repo-task-atk-test/CLAUDE.md` |
| §9 参考文档 | principles/specs 留根，Plan A/B/C 搬走 |

§8 那组测试数字跑的是整棵树，属于仓级事实，所以留根。**实施时重新跑一遍取真值，
不照抄现有数字。**

### 2.3 使用者文档：docs/skills/&lt;name&gt;/

```
docs/
├─ skills/                             # 使用者视角（新增）
│  ├─ repo-task-atk-test/{design,quickstart}.md
│  └─ repo-task-doc-write/{design,quickstart}.md
├─ development/                        # 开发者视角（不动）
├─ superpowers/                        # specs / plans（不动）
└─ atk-facts.md                        # 不动
```

`docs/QUICKSTART.md` 搬成 `docs/skills/repo-task-atk-test/quickstart.md`，
不留重定向。`docs/development/repo-hygiene.md` 里指向它的那行跟着改。

两份 `design.md` 同构，四节：解决什么问题 → 阶段表 → 红线及理由 → 产物与不适用边界。
末尾一行指向开发文档。

两份 `quickstart.md` 同构，四节：前提 → 第一条消息怎么写（可抄的例子）→
中途会问你什么 → 常见卡点。

### 2.4 README 只讲怎么做

三节：两个 skill 的表格 → 安装 → 改 skill 本身去哪。

安装分三段，`<name>` 参数化覆盖两个 skill：取得本仓（不带 `--recursive`）、
装 skill、`repo-task-atk-test` 还要装 ATK。

---

## 3. 两条筛内容的尺子

这两条决定每句话留不留，四份新文档与 README 共用。

**尺子一：怎么做进 README，为什么进 docs。**
README 里删掉的四条理由——为什么 clone 不带 `--recursive`、为什么不能
`pip install atk`、为什么锁 ATK 版本、为什么 `pip show` 过了还要跑
`atk case --help`——全部落到 `repo-task-atk-test/quickstart.md` 的常见卡点。

**尺子二：阶段表全仓只写一遍。**
S1-S5 与 T1-T5 的完整表各自只在对应的 `design.md` 出现，README 与 quickstart 改成链接。

### 篇幅预算

| 文件 | 上限 | 现状 |
| --- | --- | --- |
| `README.md` | 70 行 | 120 |
| `CLAUDE.md` | 100 行 | 347 |
| `design.md` ×2 | 60 行 | 新增 |
| `quickstart.md` ×2 | 50 行 | 96（旧 QUICKSTART） |

**超预算就是没筛干净，不放行。**

---

## 4. 门禁

新写的文档全部纳入行文门禁，基线 0。写的时候就得合规，不开新欠账。

### 4.1 新增仓级门禁

`tests/test_repo_docs_style.py` 扫 `README.md`、`CLAUDE.md`、`docs/skills/**/*.md`，
基线 0。判据函数从 `repo-task-atk-test` 的 `test_document_style.py` 按路径导入，
跟 `repo-task-doc-write` 现在的做法一致。

仓根现在没有 `tests/` 目录，新建。`pytest.ini` 已经是 `--import-mode=importlib`，
同名文件撞不上，不用额外配置。

### 4.2 补齐两个 skill 门禁的不对称

`repo-task-doc-write` 的门禁扫自己的 `CLAUDE.md`，`repo-task-atk-test` 的只扫
`SKILL.md` 与 `references/`。新增的 `repo-task-atk-test/CLAUDE.md` 加进它的
`SCANNED`，基线 0。

从根搬进去的内容若带存量违规，改到 0 再落地——搬家不是继承欠账的理由。

### 4.3 规则正文跟着扩

`.claude/rules/prose-style.md` 开头「约束本仓所有 skill 文档」那句，扩到覆盖
`docs/skills/**` 与 `README.md`。

`skill/repo-task-doc-write/CLAUDE.md` 里「行文门禁的范围比仓根规则多一处」这句，
在仓级门禁存在之后重新措辞。

---

## 5. 验收标准

- 四个篇幅预算全部达标
- `PYTHONPATH=third_party/ATK python3 -m pytest skill/ -q` 的失败数不高于 22
  （实施前实测：22 failed, 886 passed, 13 skipped）
- 两个新门禁范围（仓级、`repo-task-atk-test/CLAUDE.md`）零违规
- 全仓 grep 不到指向 `docs/QUICKSTART.md` 的残留链接
- 完整阶段表按受众各一份——`SKILL.md`（agent 运行时）与 `design.md`（使用者），
  `CLAUDE.md` 只留指针，README 与 quickstart 只留链接
