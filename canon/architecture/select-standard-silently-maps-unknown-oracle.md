---
title: select_standard silently maps unknown oracle to ascendoptest_default
updated: 2026-07-10
status: verified
---

# select_standard silently maps unknown oracle to ascendoptest_default

`plugin/acc-common/precision_policy.py` 的 `select_standard` 对 `verify_mode == "numerical"`
的算子，只把 `oracle ∈ {mere_mare, atk_double}` 映到 `ecosystem_mere_mare`，其余一律
`return ASCENDOPTEST_DEFAULT`（catch-all）。这是 fail-open：任何未知 oracle 都被静默套上
AscendOpTest 的阈值尺子，而不是拒绝。

`precision_policy.py` 其余部分基本 fail-closed（满屏 `raise`），仅此一行是 catch-all。

**推断（未实跑，标注）**：acc-spec 的受控词表含 `torch` / `scipy` / `std_exact`。这些值若被抽出，
按上述代码路径会静默落到 `ascendoptest_default`——即用 AscendOpTest 的阈值去判「与 python 实现一致」。
社区任务书里「与 python/预期实现一致」类（见 [[Community taskdoc precision requirements fall into four classes]]）
正是这种。此为读代码得出的可达路径推断，**要坐实须补负向测试**
（参照 [[Synthetic catlass demo cannot forge a PASS acceptance]] 的先例）。

**已定方案（尚未实现）**：把 catch-all 改为显式白名单 `{ascendoptest, none, 缺省}`，其余 oracle 一律 `raise`
并提示「该精度标准未验证过，建议 agent 自行探索」——即 fail-closed。用户 2026-07-10 定：先拒绝、后期再改降级告知。

**Verified.** `plugin/acc-common/precision_policy.py`（2026-07-10）：`select_standard` 末尾
`return ASCENDOPTEST_DEFAULT` catch-all 存在。指纹记 `_verify.json`。映射到 torch/scipy 的后果为推断、未验。

**Sources.** [[session 0513d745-9176-41f0-8f4b-cb7a2d19ff86 · 2026-07-10]]
