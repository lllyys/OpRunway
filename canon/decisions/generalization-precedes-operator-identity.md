---
title: Generalization precedes operator identity
updated: 2026-07-26
status: proposed
---

# Generalization precedes operator identity

用户 2026-07-24 将泛化优先定为最高原则：接口、目标目录、shape、dtype、硬件与 ABI 从任务书、
op_def、header/example 等事实源按字段派生；通用代码不得按具体算子名分派，也不得把若干 per-op 特例的
并集包装成通用机制。

具体算子只作为见证和测试输入。域内算子应在工具零改的前提下由 spec/IR/adapter 数据驱动；域外或未知
接口能力必须 fail-closed 标明不支持，不能猜测归类或硬塞进现有 runner。

**Sources.** [[session 9f5c778e-cdf9-4c84-bb4f-f0ab8c49a99d · 2026-07-24]]
