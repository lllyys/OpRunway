---
title: Precision gate precedes performance and fails fast
updated: 2026-07-16
status: proposed
---

# Precision gate precedes performance and fails fast

验收流程**先判精度、全过才跑性能**：精度用例只要不是全过（`pass` / `passed_with_risk` 之外，哪怕一条挂），
则**跳过 Task 3 性能、整体判 FAIL(精度)、非零退出**，提前结束流程。用户 2026-07-15 明示「一条精度不过、整个验收
就不过、提前结束」。

**「跑完再判」而非 early-return.** 精度用例仍**全部跑完**再统一判（不在中途遇挂即 return）——先跑完 gate /
runner_source 分支、复用主 `overall` 路径，只是当精度不全过时把性能段短路。这样门的判定对象仍是「真实跑完的全部
用例」、且异常/证据契约不被半路 return 绕过（蓝图评审 #4 的要求）。

是 [[Task spec is authoritative over PR]] 验收纪律的流程化：任务书定的精度目标是硬门，性能达标不能补偿精度不达标。

**Sources.** [[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：精度门前置 + fail-fast「跑完再判」）
