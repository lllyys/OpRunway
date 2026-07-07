---
title: Operator engineering paradigm trichotomy
updated: 2026-07-06
status: proposed
---

# Operator engineering paradigm trichotomy

被验收算子仓的工程范式分三种（深读 18 个 PR 归纳）：

1. **标准 GE/aclnn 算子**（ops-math / ops-nn / ops-cv 主流）：`op_host(def/infershape/tiling) + op_kernel + op_api(aclnn 两段式) + op_graph(proto) + config/<soc>/binary.json + tests(ST 常走 ATK + UT) + docs`。
2. **experimental 库式**（ops-sparse SPMV / ops-blas Trsm）：自定义 `aclsparse*`/`aclblas*` **C API** + op_host/op_kernel + 自包含测试主程序（自带 CPU/scipy golden），**无 GE 注册、无 op_graph**；ops-blas 每算子目录自带 `run.sh`。
3. **纯头文件模板库**（ops-collections dynamicMap）：STL 风格，无 op_host/op_kernel，Catch2 单测。

**关键**：[[Repo adapter interface and modes]] 的三模式 **≠ 这三范式（不一一对应）**——三种范式**都优先复用 `existing_example`/`new_example`**（PR/仓自带可运行工程，直接 build/run），**只有无现成可运行壳或 catlass/aclnn 桥接才用 `generated_harness`**。社区任务这批基本 `new_example`。各仓 build/run/golden/perf 各不同 → repo_adapter 一接口、每仓一份小配置。

依据 `doc/oprunway-spec-pr-analysis.md`。

**Sources.** [[session d31ea446-dec3-479f-a7b3-d6c1dec4f611 · 2026-07-02]]（2026-07-06 检查点）
