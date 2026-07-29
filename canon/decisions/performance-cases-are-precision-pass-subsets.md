---
title: Performance cases are precision-pass subsets
updated: 2026-07-27
status: proposed
contradicts: [[Performance reuses precision inputs with a trivial-met exemption]]
---

# Performance cases are precision-pass subsets

性能用例从同一份精度 caseset 中选取；带性能维的 case 必须同时带精度维，且只有精度已通过的 case
才能进入性能比较。性能阶段仍可因 ratio 不达标而 FAIL，或因 baseline、profiler 等证据缺失而
BLOCKED。

该规则不允许 `trivial-met`、按 numel 免测或因 shape 小而自动放宽阈值。

**Sources.** [[session perf-case-source-shape-rule-20260726 · 2026-07-26]]，
[[session 019fa119-cf94-7993-bd18-dae28be83cf8 · 2026-07-27]]
