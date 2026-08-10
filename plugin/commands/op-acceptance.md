---
name: op-acceptance
description: 用 ATK 正式验收一对任务书与 NPU 算子源码。
argument-hint: "<任务书路径或URL> <源码路径或locator>"
---

# /op-acceptance

调用唯一 `op-acceptance` agent，并加载 `acceptance-workflow` skill。

调用方提供任务书和源码即断言二者对应。若输入是 URL，先完整物化到本轮只读输入；不要按 PR、issue、ref
或 head 再鉴权。根据任务书、header/example 和 op_def 生成 spec 与 ATK design，然后在不存在的新 ASCII
session 目录调用唯一 `accept` 入口。

依赖安装不是 plugin 能力；ATK/CANN/NPU 未准备好时返回 `BLOCKED`。最终只展示确定性
`acceptance.json`、workflow 分阶段耗时及明确的未验证限制，不自行改写 PASS/FAIL。
