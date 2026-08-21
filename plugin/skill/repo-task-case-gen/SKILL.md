---
name: repo-task-case-gen
description: >-
  仅在只有任务书时，为社区算子任务书生成 ATK 测试用例、准备验收用例、把用例冻结封印、
  为多个 PR 复用同一套用例；已有交接包、工程目录和任务书时改用 repo-task-atk-accept。
---

本入口是 `repo-task-atk-test/case-gen/SKILL.md` 的别名，为只扫一层的目录安装提供独立命令。

读 `../repo-task-atk-test/case-gen/SKILL.md` 并完整照它执行；规则、脚本与 reference
都在那边，这里不复制。

真身目录 `repo-task-atk-test` 必须与本目录并列安装。
