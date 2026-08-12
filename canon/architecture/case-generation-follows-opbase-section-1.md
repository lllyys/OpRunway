---
id: pg-case-generation-follows-opbase-section-1
title: Case generation follows opbase ecosystem precision standard section 1
updated: 2026-08-11
status: proposed
---

# Case generation follows opbase ecosystem precision standard section 1

浮点计算类算子的**精度用例生成规则**取自 `cann/opbase` `docs/zh/ops_precision_standard/experimental_standard.md`
**§1「用例生成规则」**，用户 2026-07-15 指定为权威。整型计算类与搬运类算子按 §0 明确排除在本标准之外，另定，不套本页。 ^claim

**上游快照.** 2026-08-11 自 `https://raw.gitcode.com/cann/opbase/raw/master/docs/zh/ops_precision_standard/experimental_standard.md`
取得，内容 SHA-256 `5423b15643d0f08b0470cd1c07c0a80c1a9f2b18a046c1d2172fc5ad0eb9290c`，本地只读缓存在
`.oprunway/cache/opbase-experimental_standard.md`（gitignored）。本页据该快照写成。早前引用的 pin commit
`f69d4e4e3f2626ddd37855a8d05063a1764ac4c9` 已不代表现行文本。

**§1 现行内容.**

- §1.1 覆盖目标是**全组合覆盖**：对算子支持的数据类型、数据格式、数据维度、属性取值范围，要求所有有效组合
  **100% 覆盖**；用例数量**不设固定下限**，强调输入组合的遍历式覆盖而非机械要求数量。
- §1.2 张量：覆盖 1~8 维，维度值在 1 到 2 的 20 次方内取「2 的幂次」与「2 的幂次减一」两种取值，总元素数不超过
  2 的 31 次方；覆盖所有支持的数据格式（ND、NCHW 等）；各参数类型之间正交组合遍历；值域 50% 均匀分布（-5 到 5）
  ＋ 50% 正态分布（μ 在 -5 到 5、σ 在 0.1 到 2）。
- §1.3 属性：标量参数覆盖所有等价类场景，布尔参数覆盖 True 与 False，枚举参数覆盖所有支持的枚举值，各类参数
  组合遍历。
- §1.4 特殊场景：空 Tensor（某维为 0，每种 dtype 每个 tensor 至少覆盖一次）、标量 Tensor（shape 为 1，每种 dtype
  覆盖）、边界测试（下边界各维均为 1、上边界某维取最大值，全部覆盖）、INF/-INF/NAN（输入元素值遍历 nan、inf、
  -inf 及其区间，每种 dtype 每种值生成不同 shape 用例）。**特殊场景用例不与常规用例正交组合。**

**§2 不由本页承载，且上游已改口径.** 现行 §2 采用**混合容差**：逐元素判据为绝对容差加相对容差
（`|actual − golden| ≤ atol + rtol × |golden|`），用例级要求 `matched_ratio ≥ required_matched_ratio` 且
`max_abs_error ≤ max_abs_error_limit` 同时成立。**现行文本中已无 MERE/MARE 指标**。

**Sources.** [[session 2488e031-5814-4c61-a723-56aeeb1e6029 · 2026-07-13]]（2026-07-15：opbase §1 生成规则）
