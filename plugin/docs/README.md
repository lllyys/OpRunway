# 文档地图

仓里的文档分三层，**按读者分，不按主题分**。找东西先认读者。

| 目录 | 读者 | 放什么 | 会不会随代码更新 |
| --- | --- | --- | --- |
| [`guide/`](guide/) | **用 skill 的人** | 每类怎么用：用法、需人工指定、产物、范围之外。不含命令 | 会 |
| [`development/`](development/) | **改 skill 的人** | 契约、真机排错事实、架构演进记录 | 会 |
| `superpowers/specs/` | 考古 | 当时的设计方案 | **不会**，历史快照 |

另有 [`install.md`](install.md)：装 skill 与各 skill 的依赖，独立于三层之外。

## `guide/` — 使用指导

一类一份，与 [README 能力清单](../README.md#-能力清单) 的四个分类一一对应。

| 文件 | 覆盖 |
| --- | --- |
| [community-task.md](guide/community-task.md) | 社区算子任务：任务书 → 用例 → NPU 验收结论 |
| [ops-sample-run.md](guide/ops-sample-run.md) | 开源仓算子样例跑测：搭环境 → 挑算子 → 跑样例台账 |
| [issue-workflow.md](guide/issue-workflow.md) | 社区 issue 处理：起草上报 → 跟踪 → 复测闭环 |
| [quality-inspect.md](guide/quality-inspect.md) | 开源仓质量巡检：快速入门体检 · 教程查证 · 页面巡检 |

**这里不写命令，也不写脚本参数。** 那些是 agent 执行的，写在各 skill 的 `SKILL.md`。
使用指导只回答人需要决定的事。

## `development/` — 开发文档

| 类别 | 文件 |
| --- | --- |
| 跨 skill 契约 | [case-package-contract.md](development/case-package-contract.md) · [blas-case-package-contract.md](development/blas-case-package-contract.md) · [ops-report-contract.md](development/ops-report-contract.md) |
| 真机排错事实 | [atk-accept-troubleshooting.md](development/atk-accept-troubleshooting.md) · [case-gen-troubleshooting.md](development/case-gen-troubleshooting.md) · [executor-divergence.md](development/executor-divergence.md) |
| 演进与卫生 | [architecture-log.md](development/architecture-log.md) · [dev-environment.md](development/dev-environment.md) · [repo-hygiene.md](development/repo-hygiene.md) |
| 只读素材 | [taskdoc-source/](development/taskdoc-source/) —— 上游任务书模板拷贝，有测试机械依赖，一个字节都不改 |

## `superpowers/specs/` — 历史快照

当时的设计方案，**不随代码更新**——读它是为了知道当时为什么这么定，
不是为了照着核对现状。名字对不上现在的实现是正常的。

配套的实施计划（`plans/`）已在 2026-09-04 删除：它答的是「怎么一步步做」，
实现落地之后代码就是真相，不再有读者。要看当时的步骤去 git 历史。

想知道「为什么变成现在这样」，先读 [architecture-log.md](development/architecture-log.md)，
它按日期列了每次架构级改动和约束的落点。
