---
title: oracle_source is a hardcoded constant not a recorded fact
updated: 2026-07-15
status: proposed
---

# oracle_source is a hardcoded constant not a recorded fact

canonical 契约 [[Acceptance contract and evidence chain]] 要求每条用例 per-case 记录
`oracle_source`（golden 来源，六枚举之一）。但实现里：

- `plugin/acc-common/gen_cases.py` **根本不产** `oracle_source` 字段；
- 它只在 evidence 层出现，两处都是**写死的常量** `"oracle_source": "cpu_ref"`
  （`repo_adapter.py` 与 `catlass_adapter.py` 各一处）。

于是不论 golden 实际从哪来，evidence 里永远是 `cpu_ref`。门（[[Machine-verifiable acceptance gate]]）
读 evidence 校完整性，永远看到一个合法值——这是 [[A gate must validate the object that actually takes effect]]
的又一实例：字段记录的是假设，不是事实。

**诚实边界（未定项）**：当前四个 golden 都是 NumPy host 计算，但「跑在 host Python 上」不自动等于
canonical 枚举里的 `cpu_ref`——按公式现写的 numpy 参考在语义上更接近 `analytical_ref`。
四个 golden 的真实 `oracle_source` **尚未逐项核定**，故不能声称写死的 `cpu_ref` 整体正确。
无论核定结果如何，这都是 fail-open 设计：来源一变（如按 canonical 从 catlass 抠成 `catlass_existing_ref`），
字段不跟着变、门也校不出来。

**已修复（2026-07-15，Q9）**：删了 `repo_adapter.py`/`catlass_adapter.py` 两处写死 `cpu_ref`，改为据 caseset
`golden_source` 严格首 token **据实映射**（torch→`torch_ref`、numpy 公式→`analytical_ref`），来源缺失/不可映射
→ **fail-closed**。且 [[Golden is fixed to torch on CPU for determinism]] 后四 golden 恒 `torch_ref`。上文描述的是
**修复前**状态；原 `_verify.json` 指纹（写死 `cpu_ref` 存在）已随修复过时、本次撤除，故本页降回 `proposed` 待
`bureau:review` 依修复后代码重核。

**Sources.** [[session 0513d745-9176-41f0-8f4b-cb7a2d19ff86 · 2026-07-10]]，[[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：Q9 治此假常量）
