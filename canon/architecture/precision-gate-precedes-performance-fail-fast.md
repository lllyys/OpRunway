---
id: pg-precision-gate-precedes-performance-fail-fast
title: Precision gate precedes performance and fails fast
updated: 2026-08-11
status: proposed
---

# Precision gate precedes performance and fails fast

验收流程**先判精度、精度不达标就不跑性能**：精度不达标时跳过性能段、整体判 FAIL（精度）、非零退出，提前结束流程。
性能达标不能补偿精度不达标。 ^claim

**「达标」不等于逐元素全对.** 单条用例的通过判据取自 opbase 生态算子开源精度标准 §2 的混合容差口径：逐元素满足
`|actual − golden| ≤ atol + rtol × |golden|`，再要求用例级 `matched_ratio ≥ required_matched_ratio`
（该标准现行取值 0.99，六种浮点 dtype 统一）**且** `max_abs_error ≤ max_abs_error_limit`，两条同时成立才判该用例
通过。因此允许至多 1% 的元素落在容差外，但最大绝对误差仍受硬上限约束。

**用例级通过比例该标准未定义.** opbase §2.3 只定义**单条用例**何时通过，全篇没有规定「整套用例允许挂多少条」。
所以「挂了几条算不算整体 FAIL」不是生态精度标准能回答的问题，必须由任务书给出；任务书未给出时按全部用例都要
通过处理，不得自行放宽。

**「跑完再判」而非 early-return.** 精度用例仍**全部跑完**再统一判（不在中途遇挂即 return）——先跑完 gate /
runner_source 分支、复用主 `overall` 路径，只是当精度不达标时把性能段短路。这样门的判定对象仍是「真实跑完的全部
用例」，异常与证据契约不被半路 return 绕过。

**Sources.** [[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：精度门前置 + fail-fast「跑完再判」）
