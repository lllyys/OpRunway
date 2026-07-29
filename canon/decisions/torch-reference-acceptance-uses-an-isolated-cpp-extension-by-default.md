---
title: Torch reference acceptance uses an isolated C++ extension by default
updated: 2026-07-27
status: proposed
---

# Torch reference acceptance uses an isolated C++ extension by default

任务书要求对标 Torch 时，默认采用官方 `CppExtensionInvocation` 路线：

- stock `torch.*` 对应接口作为 baseline；
- DUT 通过独立的 `torch.ops.<namespace>.*` 接口调用 PR 构建出的 ACLNN 或 Ascend C 算子；
- 两侧使用同一批 Torch Tensor 输入、相同设备和匹配的 timing scope；
- baseline 不加载 custom OPP；DUT 以符号定义方和 profiler kernel 证明命中 PR 算子。

只有任务书明确要求替换原生 ATen/Torch dispatch，或独立 extension 无法表达所需语义时，才例外采用
修改 `Ascend/pytorch` op-plugin 的 `PytorchInvocation`。官方 Torch NPU 源码入口为
`https://gitcode.com/Ascend/pytorch`。

该路线解决的是 baseline 与 DUT 的路由隔离和 provenance，不改变“性能 baseline 必须由任务书决定”
这一上位规则。

**Sources.** [[session torch-cppextension-route-20260727 · 2026-07-27]]
