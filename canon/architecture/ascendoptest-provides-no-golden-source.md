---
title: AscendOpTest provides no golden source only a comparison harness
updated: 2026-07-15
status: proposed
---

# AscendOpTest provides no golden source only a comparison harness

真代码坐实（两轮 fan-out 核实 `repos/AscendOpTest`）：**AscendOpTest 自己不产 golden 参考值**，只提供两个**槽位**让开发者填——

- `expect_func`（`文件路径:函数名`）：开发者写的 Python 参考函数（numpy）；`golden/golden_gen/compute.py` importlib 加载并跑它产 golden。
- `golden_path`：开发者预置的 golden bin；`expect_func` 空/加载失败时回退用它（`compute.py` 打印 "use default golden bin"）。

比对端 `compare/compare/compare.py` 的 `compare()` 只 `np.fromfile` 读 output/golden 两文件 + 按 `AccuracyConfig.default_acc` 阈值判，**从不算 golden**；自带用例生成器 `scripts/gen_case.py` 把 `expect_func` 写空。全程**无任何算子参考实现 / golden 算子库 / CPU 自动 golden**。

**推论**：任务书写「满足 AscendOpTest 默认阈值」= 只指定了**比对方法 + 阈值表**（尺子），**未指定 golden 值源**——golden 源须从任务书别处（任务概述/参考实现字段）取。故「用了 AscendOpTest」≠「golden 有独立真值」。印证 [[AscendOpTest precision thresholds]]（canonical：golden 由 expect_func 提供）与 [[ADR 0008 — Reuse AscendOpTest for Task 2]]。是 [[Golden and precision standard come only from the task-doc-specified method]] 的关键前提。

**tier 说明**：正源在 gitignore 的 `repos/AscendOpTest`、不入库、库内不可复现，留 `proposed`（与 target-hardware / community-taskdoc 等 repos 源页同口径）。

**Sources.** [[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]
