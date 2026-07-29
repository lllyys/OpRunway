---
title: Direct ACLNN performance baseline
updated: 2026-07-27
status: contested
contradicts: [[Median performance baseline follows the stock Torch interface]]
---

# Direct ACLNN performance baseline

OpRunway 已提供通用 `perf.baseline="aclnn_builtin"` 通路。spec 通过
`perf.aclnn_baseline.library` 与 `variants[].when/symbol/slots` 描述任务书点名的 ACLNN API 及其 ABI；
采集器从当前 `ASCEND_TOOLKIT_HOME/lib64/libopapi.so` 直接执行两段式接口，不经过 Torch 包装。

baseline runner 使用 `required_symbol_lib` 收窄符号搜索面：GetWorkspaceSize 与执行符号都必须由指定库
定义，不允许全局命名空间、依赖树或 custom vendor 代答。基线文档还要求指定库路径、size、mtime、
sha256 与逐符号定义方 provenance；证据不完整时 fail-closed。

Median spec 以数据映射全局变体到 `Median(self, valuesOut)`、按维变体到
`MedianDim(self, dim, keepDim, valuesOut, indicesOut)`；通用代码没有按 Median 身份分支。

**后续冲突。** 上段是 2026-07-26 的实现与路由快照；用户随后明确 Median 任务书中的小算子拼接
等价于 stock Torch 对应接口，因此不能再用 `aclnn_builtin` 直接调用作为 Median 的任务书性能
baseline。通用 `aclnn_builtin` 能力仍可服务于任务书确实点名直接 ACLNN baseline 的其它任务。
Median 当前口径见 [[Median performance baseline follows the stock Torch interface]]。

**Verified.** 2026-07-26 核：`plugin/acc-common/aclnn_runtime/aclnn_runner.py`、
`plugin/acc-common/aclnn_runtime/perf_msprof.py`、`plugin/acc-common/aclnn_adapter.py`、
`plugin/acc-common/repo_adapter.py`、`plugin/acc-common/run_workflow.py` 与
`plugin/samples/specs/median.spec.json` 均存在上述契约与接线。此处只验证实现事实，不代表远程测试或
Median 50-case 真机性能已通过。

**Sources.** [[session perf-baseline-direct-20260726 · 2026-07-26]]，
[[session perf-case-source-shape-rule-20260726 · 2026-07-26]]
