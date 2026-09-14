# 什么进仓，什么不进仓

维护时查。判断标准只有一条：**这个文件消失了，会不会有东西坏掉或说不清。**

坏掉的分两级——测试红是机械依赖，人看不懂了是人依赖。两者都进仓，
只有「重跑一遍就能再生」的才不进。

## 进仓

### 有机械依赖：删了测试就红

| 路径 | 谁在依赖 |
| --- | --- |
| `docs/development/taskdoc-source/origin/` | `test_source_archive.py` 断言三份归档存在 |
| `docs/development/taskdoc-source/defect-map.md` | 同上断言 D01-D13、T01-T05 齐全 |
| `docs/development/taskdoc-source/{README,repair-log}.md` | 撰写 skill 的行文门禁扫这三份 |

`origin/` 是上游只读拷贝，一个字节都不改（见撰写 skill 的 CLAUDE.md
「素材只读」）。它进仓不是为了给人读，是为了上游改版时能逐条重放。

### 有人依赖：删了没人红，但下一个人查不到

- `docs/development/architecture-log.md` —— 架构演进，仓根 CLAUDE.md「当前状态」指向
- `docs/README.md` —— 文档地图，三层按读者分。找不到东西时的入口
- `docs/guide/` —— 使用指导，一个分类一份，README 指向。**不含命令**，
  命令是 agent 执行的，写在各 skill 的 `SKILL.md`
- `docs/install.md` —— 装 skill 与各 skill 的依赖
- `docs/superpowers/specs/` —— 当时的设计方案。**仓根 CLAUDE.md 已不再指向它们**
  （2026-08-25 精简掉了分节编号与这条链接），查当时为什么这么定就从这里进

specs 是历史快照，**不随代码更新**。读它是为了知道当时为什么这么定，
不是为了照着现状核对。名字对不上现在的实现是正常的。

**实施计划不进仓。** `docs/superpowers/plans/` 两份共 187 KB 已于 2026-09-04 删除：
它答的是「怎么一步步做」，不是「为什么」；实现落地之后代码就是真相，计划不再有读者，
按本文开头那条判据它两样都不占。要看当时的步骤去 git 历史。

**这是 specs 与 plans 的分界线**：同样无人引用、同样不更新，specs 留而 plans 删，
差别只在「删了之后还说不说得清」。

## 不进仓

全部在 `.gitignore` 里，按「重跑一遍就能再生」归的类：

| 类别 | 条目 |
| --- | --- |
| Python 构建与缓存 | `__pycache__/`、`*.py[cod]`、`*.egg-info/`、`build/`、`dist/`、`.pytest_cache/` |
| macOS | `.DS_Store`、`._*` |
| 跑测运行时产物 | `evidence/`、`cann-ops-report/`、`atk_capabilities.json`、`SKILL_FRICTION_*.md` |
| **一次性材料** | `docs/development/scratch/` |
| 本机私有 | `.claude/settings.local.json`、`.claude/RESUME.md`、机器档案（地址 / 账号 / 目录布局 / CANN 版本） |

### 一次性材料为什么不进仓

**上游 issue 文稿**：提交之后权威副本在上游，本地那份只会漂。仓里要留的是
**指向 issue 的链接**，不是文稿正文。2026-09-04 删掉的 `atk-upstream-issues/`
就是这种——三份文稿都已提交（ATK #28/#29/#30），本地留着是双份。

**单算子复现程序、临时探查脚本**：为查一个具体问题写的，问题结案就没有下一个读者。
它证明过的结论该沉淀进 skill 的 `references/` 或本目录的排障事实，程序本身不必留。

**机器档案**：某台机器的地址、账号、目录布局、CANN 版本。这类信息**会漂而没有任何
机制会发现**——机器升一次级，文档不改就错了。而且各 skill 的边界条款本来就写着
「不写死任何机器的路径 / 版本 / 布局 / SOC」，开发文档不该反过来违反它。
2026-09-04 从 `dev-environment.md` 移出的就是这类。

两类都放 `docs/development/scratch/`，已进 `.gitignore`。**判断标准还是开头那条**：
删了会不会有东西坏掉或说不清——结论已经写进别处的，删了说得清。

`evidence/` 那条管的是**验收工作区**里的产物——skill 部署到跑测机后，
agent 在工作目录里生成取证、封条、时间线，那些不是 skill 本体。

**它不该在本仓根目录出现。** 出现了就是有测试拿仓库根当工作目录在写文件，
去查那个测试，不要靠这条 ignore 盖住。2026-08-19 修过一次这样的泄漏
（`check_golden_source.py` 的 `--output` 相对默认值 + 测试没覆盖它），
当时正是因为被这条 ignore 盖着，`git status` 永远干净，漏了很久没人发现。

## 两处 ignore 不在根 `.gitignore` 里

查不到某个文件为什么被忽略时，用 `git check-ignore -v <路径>` 直接问，
别只翻根目录那一份：

- `.superpowers/sdd/.gitignore` 内容是 `*`，整个目录都不进仓。
  里面是 SDD 的会话状态（进度、评审 findings），换台机器就没有意义。
- `.git/info/exclude` 是本机私有的忽略清单，不随仓库分发。
