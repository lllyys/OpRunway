---
id: pg-precision-gate-precedes-performance-fail-fast
title: Precision gate precedes performance and fails fast
updated: 2026-08-13
status: proposed
---

# Precision gate precedes performance and fails fast

精度判据一律取自生态算子开源精度标准；**用例通过比例未达标时跳过性能段**，不再采集性能数据。性能达标不能
补偿精度不达标。 ^claim

**单条用例的判据取自生态标准 §2 的混合容差口径.** 逐元素满足 `|actual − golden| ≤ atol + rtol × |golden|`，
再要求用例级 `matched_ratio ≥ required_matched_ratio`（该标准现行取值 0.99，六种浮点 dtype 统一）**且**
`max_abs_error ≤ max_abs_error_limit`，两条同时成立才判该用例通过。因此允许至多 1% 的元素落在容差外，但最大
绝对误差仍受硬上限约束。判据不取自执行工具的默认比较器——工具只负责执行，阈值来自任务书引用的标准。

**整套用例的通过比例该标准未定义.** 生态标准 §2.3 只定义**单条用例**何时通过，全篇没有规定「整套用例允许挂
多少条」。所以「挂了几条算不算整体不达标」不是生态精度标准能回答的问题，必须由任务书给出；任务书未给出时按
全部用例都要通过处理，不得自行放宽。

**跳过性能，不是提前中断精度.** 精度用例仍全部跑完再统一判，不在中途遇挂即返回。判定对象因此始终是「真实跑完
的全部用例」，异常与证据契约不被半路返回绕过。只有在精度通过比例已判定为未达标之后，才短路掉性能段。

**当前未生效.** 本页是规则，不是现状。截至 2026-08-13，实现中精度与性能是独立执行、互不阻塞的
（`plugin/oprunway/atk.py` 的 `_run_independent_phases`），`plugin/skills/acceptance-workflow/SKILL.md`
步骤 9 也明写「精度与性能独立取证」。要让本页生效，需要同时改动该 skill 的步骤 9 与执行编排，并为「任务书给定
的通过比例」在 spec 契约中安排字段——目前没有这样的字段，实现等价于「全部用例都要通过」这一默认情形。在完成
这些改动之前，本页记录的是意图而非行为。**这一段是临时的，实现跟上后应连同本标题一并删除。**

**Sources.** [[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：精度门前置 + fail-fast「跑完再判」）；[[session f3bb5155-2e5e-4185-8b52-11d9e935a76f · 2026-08-12]]（2026-08-13：判据改述为一律取自生态标准、通过比例未达标即跳过性能；同时记录本页当前未生效）
