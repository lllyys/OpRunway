---
title: session torch-cppextension-route-20260727 · 2026-07-27
updated: 2026-07-27
status: logbook
session: torch-cppextension-route-20260727
transcript: ""
---

## [2026-07-27T03:52:15-04:00] session torch-cp — Torch 对标默认采用独立 C++ Extension

**Intent.** 重新确定任务书要求对标 Torch 时，PR 算子应如何接入 Torch 并与系统小算子拼接版本做精度、性能测试。

**Decisions.**
- 以后任务书要求对标 Torch 的算子，默认采用官方 `CppExtensionInvocation` 路线：stock `torch.*` 对应接口保留为 baseline，DUT 通过独立 `torch.ops.<namespace>.*` 接口调用 PR 构建出的 ACLNN/Ascend C 算子。该决定意味着 dossier **Torch reference acceptance uses an isolated C++ extension by default**。
- baseline 与 DUT 必须使用同一批 Torch Tensor 输入、相同设备和匹配 timing scope；baseline 路径不得加载 custom OPP，DUT 路径必须用符号定义方与 profiler kernel 证明命中 PR 算子。
- 只有任务书明确要求替换原生 ATen/Torch dispatch，或独立 extension 无法表达所需语义时，才例外采用修改 `Ascend/pytorch` op-plugin 的 `PytorchInvocation` 路线；官方 Torch NPU 源码入口以 `https://gitcode.com/Ascend/pytorch` 为准。
- 本轮先考虑 `PytorchInvocation`，但发现两侧都从 `torch.median` 发起时，若缺少隔离 wheel、符号定义方和 profiler 证据，难以区分系统小算子拼接与 PR 算子；因此改为默认 `CppExtensionInvocation`，用不同入口保持路由可辨。

**Changes.**
- `canon/logbook/2026/07/torch-cppextension-route-20260727.md` (new) — 记录本轮通用路线决定及其例外边界。

**Open threads.**
- 依据 Median 的全局与按维重载设计通用 C++ Extension 契约，明确多输出、动态 shape、indices dtype 转换和 ACLNN 两段式调用方式。
- 重写 Torch 对标测试用例设计；在新设计完成前，不采用旧的直接 ACLNN 性能结果，也不据旧用例决定 Median 实现优化。
- 后续按 capture → compile → review 流程将本决定蒸馏并由人复核；当前 logbook 记录不是 canonical。

**Source.** transcript ``
