---
title: Golden is fixed to torch on CPU for determinism
updated: 2026-07-15
status: proposed
---

# Golden is fixed to torch on CPU for determinism

**用户 2026-07-14 拍板**：golden = CPU 标杆，**固定用 torch(CPU) 单后端**——不做「有 torch 用 torch、没 torch 回退 numpy」的优雅兜底。

**理由**：torch 与 numpy 在边界语义上不一致——如 `torch.sign(NaN)=0` 而 `np.sign(NaN)=NaN`（codex 门坐实、附 pytorch/numpy 源）。「谁装了用谁」会产**随环境而变的非确定 golden**，对验收工具不可接受（golden 必须确定、可复现）。故 golden **恒走 torch**、torch 缺失 → **fail-closed 报错要求安装**（`pip install torch --index-url .../cpu`），精度验证一般在装了 torch 的 NPU 机器上做。

**落地（Q9）**：`gen_cases._require_torch()`；四内置算子 golden 恒 `torch.isclose/sign/eq/neg`；`golden_source`/`oracle_source` 恒 `torch_ref`。四算子 reference 已核任务书原文：IsClose/Equal=语义改造「二进制→对齐 CPU 逻辑比较」、Sign/Neg=纯重写、**Neg uint8 任务书点名 `torch.neg` 回绕(256-x)**——torch 正是任务书指定的参考。

是 [[Golden and precision standard come only from the task-doc-specified method]] 的具体后端选择。本机 py3.14 无 torch wheel → 测试挪装了 CPU torch 的 a3（py3.13）跑，符合「验收在 NPU 机器」。

**tier 说明**：留 `proposed`，待 `bureau:review`。

**Sources.** [[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]
