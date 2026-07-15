---
title: gen_cases GOLDEN hardcodes four elementwise operators
updated: 2026-07-15
status: verified
---

# gen_cases GOLDEN hardcodes four elementwise operators

`plugin/acc-common/gen_cases.py` 的 `GOLDEN` 字典把四个 elementwise 算子的参考实现
（golden 函数）写死在源码里：`IsClose` / `Sign` / `Equal` / `Neg`。`gen_cases` 遇到
不在字典里的算子直接 `if op not in GOLDEN: raise ValueError`。故**通用 elementwise 造用例
路径只支持这四个算子**，加第五个算子须改插件源码。

**Scope（勿外推）**：受限的是通用 elementwise 路径，**不是整个插件**。catlass matmul 另有独立
builder（`catlass_adapter.py`，matmul caseset 有意不进 `GOLDEN`），但那条路径只产
development-grade evidence、不出验收裁决（见 [[Synthetic catlass demo cannot forge a PASS acceptance]]）。
所以准确表述是：**插件的验收裁决能力当前只覆盖这四个硬注册的 elementwise 算子。**

这与 [[ADR 0002 — Acceptance grounded in catlass and the spec]] 的跨仓泛化方向不符——ADR 0002 说
泛化到别的算子仓时 golden 会变（**但并未明文规定 golden 函数必须归 repo_adapter 持有**，
golden 的源码归属仍待 ADR 确认）。另注：`GOLDEN` 里的 golden 是**函数**，不是文件，与
「加算子 = spec + golden + runner 三文件」的口头说法不符。

**后端已定为 torch（2026-07-15，Q9）**：四键结构不变（仍是硬注册四算子、`if op not in GOLDEN: raise`），
但每个 golden 的实现从「agent 自撰 numpy」改为**恒走 torch(CPU)**——`GOLDEN` 值现为 `("torch <fn>", golden_fn)`
二元组、`golden_source`/下游 `oracle_source` 恒 `torch_ref`，torch 缺失 → fail-closed（不回退 numpy）。理由与后端选择见
[[Golden is fixed to torch on CPU for determinism]] 与 [[Golden and precision standard come only from the task-doc-specified method]]。
本页claim（四算子硬注册、加第五个须改源码）**不受影响、仍 verified**。

**Verified.** `plugin/acc-common/gen_cases.py`（2026-07-15 重核，指纹匹配）：`GOLDEN = {"IsClose"…"Sign"…"Equal"…"Neg"…}`
四键、`if op not in GOLDEN: raise ValueError` 均存在；gen_cases 只出 `golden_source`、不直出 `oracle_source`（映射在 adapter 层）。指纹记 `_verify.json`。

**Sources.** [[session 0513d745-9176-41f0-8f4b-cb7a2d19ff86 · 2026-07-10]]，[[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：Q9 golden 定 torch 后端）
