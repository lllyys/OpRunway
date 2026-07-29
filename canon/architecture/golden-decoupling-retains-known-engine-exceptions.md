---
title: Golden decoupling retains known engine exceptions
updated: 2026-07-26
status: verified
contradicts: [[ADR 0011 — Golden is decoupled from the engine and loaded per operator]], [[Runner is an output of the engine not a component]]
---

# Golden decoupling retains known engine exceptions

“引擎零内置算子 golden”目前只对 elementwise 的 per-op `golden.py` 加载通路成立，不能无条件外推为整个
OpRunway 引擎已完全 op-neutral。当前仍有两处明确例外：

- `catlass_adapter.py` 内置 `golden_catlass_matmul`，且该 caseset 有意不走 elementwise `gen_cases` 加载器；
- `gen_cases.py` 保留按算子身份列举的 `_BF16_EXACT_OPS = {"Sign", "Neg"}` 历史默认。

因此这些例外被移除或改为能力/数据契约前，“引擎零算子知识”只能视为目标态。

**Verified.** 2026-07-26 核：`plugin/acc-common/catlass_adapter.py` 仍定义
`golden_catlass_matmul`；`plugin/acc-common/gen_cases.py` 仍定义并使用 `_BF16_EXACT_OPS`。

**Sources.** [[session 8217ff1b-d287-4074-bfe1-a7d0bdb3809f · 2026-07-22]]
