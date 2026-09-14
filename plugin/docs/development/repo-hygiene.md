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

- `docs/development/architecture-log.md` —— 架构演进，CLAUDE.md §8 指向
- `docs/skills/<name>/` —— 每个 skill 的 design 与 quickstart，README 指向
- `docs/superpowers/specs/` 与 `plans/` —— 设计与实施计划，CLAUDE.md §9 指向

specs 与 plans 是历史快照，**不随代码更新**。读它们是为了知道当时为什么
这么定，不是为了照着现状核对。名字对不上现在的实现是正常的。

## 不进仓

全部在 `.gitignore` 里，按「重跑一遍就能再生」归的类：

| 类别 | 条目 |
| --- | --- |
| Python 构建与缓存 | `__pycache__/`、`*.py[cod]`、`*.egg-info/`、`build/`、`dist/`、`.pytest_cache/` |
| macOS | `.DS_Store`、`._*` |
| 跑测运行时产物 | `evidence/`、`atk_capabilities.json`、`SKILL_FRICTION_*.md` |
| 本机私有 | `.claude/settings.local.json`、`.claude/RESUME.md` |

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
