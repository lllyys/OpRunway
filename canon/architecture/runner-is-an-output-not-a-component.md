---
title: Runner is an output of the engine not a component
updated: 2026-07-20
status: proposed
---

# Runner is an output of the engine not a component

OpRunway 的**引擎（workflow）是 op-中立的**：它携带**生成器**（`acc-runner-dev` + `runner-skeleton`）与
**编排 / 采集 / 门**（`run_on_npu.sh` / `repo_adapter` / `gen_cases` / gates），但**不含任何具体算子的 runner**。
per-op runner（`oprunway_<op>_runner.cpp`）是引擎为某算子**现生成的输出**——落用户输出空间 `<ops_root>/<op>/`、
交付给用户、用完可弃，**不是引擎的组件**（用户 2026-07-20：「runner 可以作为输出，但不应该作为工作流的一部分」）。

**引擎不回退插件样例（fallback 退役 2026-07-20）.** `repo_adapter.find_runner()` **只查用户目录**、缺则
**fail-closed** 报错；`run_workflow` 门 `runner_source` 仅 `user`（缺失 / 未知 / 伪造 `builtin_sample` 一律 `BLOCKED`，
比旧的 `builtin_sample`→NEEDS_REVIEW 更严）。这是对 committed 决策 `a7c8417`「用户目录优先 → 插件样例 fallback、
可以带样例」的**有意反转**（用户 2026-07-20 再确认；用户仍可自带 runner，只是引擎不再兜底一份插件样例）。3 份历史
样例 runner 迁 `samples/runners/`，降为**只读参考 / 生成器骨架种子**，非运行时回退靶。

方向与 canon 一致：[[ADR 0009 — One generalized workflow with per-repo adapters]]（一个泛化 workflow + 每仓薄
adapter、仓差异是数据）、[[OpRunway component breakdown]]（明文「acc-common 核心只放 schema/policy/判定内核、不放
runner」）、工程约定「产物落用户 CWD、不写插件安装目录」。runner 的 provenance 见
[[opp is provenance-bound to the op source with a fail-closed gate]] 与
[[Real-NPU runner supports only float32 and float16]]。

**范围（诚实）.** 本页只覆盖 **runner** 侧。引擎里另一处「长着具体算子」的是 `gen_cases` 的 `GOLDEN` 硬表（4 算子
numpy 参考）——同类问题、更根本；**golden 去引擎化（改加载器）尚未做**，须先走 ADR（golden 归属属推导非 canon）。
故引擎当前**尚未完全 op-中立**：runner 已是输出、golden 仍内置。

**Sources.** [[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-20：runner 去引擎化第一刀——runner 移出引擎作输出、fallback 退役 fail-closed）
