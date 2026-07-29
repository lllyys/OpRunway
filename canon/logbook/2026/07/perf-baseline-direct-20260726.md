---
title: session perf-baseline-direct-20260726 · 2026-07-26
updated: 2026-07-26
status: logbook
session: perf-baseline-direct-20260726
transcript: ""
---

## [2026-07-26T21:36:04-04:00] session perf-baseline-direct-20260726 — 性能标杆改走任务书最短证据链

**Intent.** 解决 Median 当前 `torch_npu torch.median` 性能标杆与任务书点名
`aclnnMedian` / `aclnnMedianDim` 小算子拼接版本不一致的问题，并把处理原则固化为跨算子规则。

**Decisions.**

- 用户明确裁定：任务书已经点名一个可直接执行、可同口径计时的性能标杆时，直接调用并测量该对象；不为本任务或以后每份新任务书增加框架包装等价性证明。这项决定意味着 dossier **Performance baseline follows the reference source**。
- 功能/精度 oracle 与性能 baseline 分开解释；任务书指定 `torch.median` 功能一致性，不自动意味着性能也要在 Torch 层比较。这项决定意味着 dossier **Performance oracle and performance baseline are independent contracts**。
- Median 性能 baseline 改为 ACLNN 层直接调用当前 CANN `libopapi.so` 的 `aclnnMedian` / `aclnnMedianDim`；旧 `torch_npu` ratio 仅保留为历史诊断，新口径真机重跑前性能仍 BLOCKED。这项决定意味着 dossier **Direct ACLNN performance baseline**。

**Changes.**

- `plugin/acc-common/aclnn_runtime/aclnn_runner.py` (updated) — 新增 `required_symbol_lib` 严格来源模式。
- `plugin/acc-common/aclnn_runtime/perf_msprof.py` (updated) — 新增 spec 驱动的 ACLNN baseline 变体解析、直接调用 wrapper 与 provenance 采集。
- `plugin/acc-common/aclnn_adapter.py`, `plugin/acc-common/repo_adapter.py`, `plugin/acc-common/run_workflow.py` (updated) — 接入 `aclnn_builtin` 计划、产物、解析与真实基线分派。
- `plugin/samples/specs/median.spec.json` (updated) — 从 `torch_npu` 切换到 `aclnn_builtin`，显式映射两个任务书 API。
- `plugin/skills/acc-perf/`, `plugin/skills/acc-spec/`, `plugin/skills/acceptance-workflow/`, `plugin/agents/`, `AGENTS.md`, `plugin/AGENTS.md` (updated) — 固化最短证据链与 oracle/baseline 分离规则。
- `doc/oprunway-todo.md`, `doc/oprunway-session-handoff-2026-07-26.md`, `doc/oprunway-changes-brief.md` (updated) — 删除等价性证明待办，改记远程测试与 50-case 真机重跑。

**Open threads.**

- 按仓规在远程 NPU 容器运行相关 pytest 与静态契约测试。
- 经用户单独确认后，用同一 50-case caseset 真机重跑 Median 性能并引用确定性脚本裁决。
- 本 minute 待 bureau compile 蒸馏；canonical 晋级只由用户通过 bureau review 完成。

**Source.** transcript ``
