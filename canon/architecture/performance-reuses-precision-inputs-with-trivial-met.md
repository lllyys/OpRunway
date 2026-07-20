---
title: Performance reuses precision inputs with a trivial-met exemption
updated: 2026-07-16
status: proposed
---

# Performance reuses precision inputs with a trivial-met exemption

性能测试**不另造大 shape 用例**——与精度用**同一套输入**：同一份用例既判精度、又测性能（用户 2026-07-15 明示
「性能判全部、相同输入」）。

**trivial-met 豁免.** 同一套用例里有退化 case（`numel < 4096`，按 **broadcast 输出** 的 numel 算、非任一输入的
numel），对这些标 **trivial-met**：达标免测、不失败——太小的 kernel 测不出有意义的稳态性能。性能达标由用例集里
**代表性大 shape** 主导。trivial-met 做成贯穿 `perf_compare` + `gate_task3` + GPU 对齐的**一等公民**（不是某一处的
特判），门/对齐处对 trivial 的豁免均据 caseset 的**真实对象**复核、防伪造（同 [[A gate must validate the object that actually takes effect]]——退化判据取真实 broadcast numel，不信自报）。

**空 Tensor（na）同理.** §1.4 空 Tensor 功能用例判 `na`（严格真空判定，拒 `shape:[false]/[0.0]` 伪造）：无精度可判、
三门豁免 + 独立复核，真机 runner 已处理 `numel=0`（空入空出）。na 与 trivial-met 都是「门对特殊退化用例的豁免须据
真实对象复核」的实例。

**Sources.** [[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：性能同输入 + trivial-met + 空 Tensor na 豁免）
