# 社区任务书 ↔ PR 对应表（7 月前 · 2026-04/05）

来源：`gitcode.com/cann/cann-ops-competitions` 的 `04_tasks/01_community-task-2026/docs/202604`(4月) + `202605`(5月)，共 **41 份任务书**。PR 由 3 个 agent 在各算子仓里逐个查、用 `/pulls/<n>/files` 核实「改动落在该算子源码目录」+ 社区任务 issue 追踪号确认（不是只看标题）。

**总览**：41 份任务书（其中 `Cast&EmbeddingDenseGrad` 等一份含 2 算子）。多数找到开发 PR；**7 个未找到 / 仅设计 / 未开发**（见末尾）；另 Gcd 找到 #1087 但在 2 月、无社区标记，算「找到但存疑」。**很多主开发 PR 还是 open（在评审）**。**一任务对多 PR 是常态**（设计文档 PR + 主开发 PR + 后续修复 + 多人并行提交）。

## ops-math（18）

| 月 | 算子 | 主开发 PR（状态） | 其他相关 PR | 置信 | 落点 / issue |
|---|---|---|---|---|---|
| 04 | Equal | #2890 merged | #3289、#3419 后续(golden/用例) | 高 | experimental/math/equal；issue#368（排除#2303 ApproximateEqual） |
| 04 | IsClose | #2943 merged | #2873 早期同款；#3607 后续st | 高 | experimental/math/is_close；issue#1629/#384 |
| 04 | MaxUnpool2d | **未找到** | #2831 仅设计文档 | — | 无实现 PR、无社区 issue |
| 04 | Neg | #2680 merged | #2540/#2566 设计；#2128 另条 AICPU neg | 高 | experimental/math/neg；issue#375 |
| 04 | Pdist | #2663 merged | #2529 并行(open)；#3319 后续 | 高 | experimental/math/pdist |
| 04 | Polar | #2923 社区(open) + #2827 官方(merged) | #2802 设计；#3497/#3498/#3528/#3529/#3816 后续 | 高 | 两条线：experimental/math/polar + math/polar；issue#1647 |
| 04 | Sign | #2702 merged | #2580/#2646 设计 | 高 | experimental/math/sign；issue#235 |
| 04 | AngleV2 | #2643 社区bf16(open) + #3228 bf16(merged) | #2557/#2560 设计；#2674/#3347/#3511/#3512/#3561/#3562/#3564/#3629 后续 | 中高 | 既有算子加 bf16；math/angle_v2；issue#1493 |
| 05 | Arange | #3168 (open) | #3166 早期同款 | 高 | experimental/math/arange；issue#1956 |
| 05 | Cast & EmbeddingDenseGrad | #3473（Cast, open） | — | 高 / 半 | Cast：experimental/math/cast、issue#373；**EmbeddingDenseGrad 未找到**（无实现无 issue，#1901 "WIP embedding" 实为 mul_no_nan 误标） |
| 05 | Fmod（Scalar&Tensor） | #3240 merged | #3701/#3737/#3764/#3769/#3776/#3786/#3788/#3789/#3790 后续(多重复) | 中高 | Fmod↔CANN Mod；experimental/math/mod |
| 05 | Gcd | #1087 merged | #1667 文档；#2616 重构；#3287 golden；#3440 用例 | 中 | math/gcd；issue#802；**在 2 月、无社区标记，可能早于任务月** |
| 05 | InplaceRsqrt | #3400 (open) | #3548 后续扩类型 | 高 | experimental/math/rsqrt；issue#1744/#369 |
| 05 | MinDim&MaxDim | **未找到** | 候选 #3362/#3176/#3203/#2856/#1029/#1049 均修复/加ST | 低 | 疑 = ArgMin/MaxWithValue（成熟算子，早于窗口）；无社区 issue |
| 05 | RightShift | #3255 (open) | #3607 后续st | 高 | experimental/math/right_shift；issue#1940 |
| 05 | bincount | #3640 (open) | #3610 早期同款 | 高 | experimental/math/bincount；issue#2069 |
| 05 | im2col | #927 merged | #1003…#1743 大量后续(perf/pad/format) | 高 | conversion/im2col（主开发 +7033 行）；issue#255 |
| 05 | logspace | #3496 (open) | — | 高 | experimental/math/log_space；issue#2029 |

## ops-nn（16）

| 月 | 算子 | 主开发 PR（状态） | 其他相关 PR | 置信 | 落点 |
|---|---|---|---|---|---|
| 04 | ForeachAddListV2 | #5213 (open) | #6161 后续 fallback | 高 | foreach/foreach_add_list |
| 04 | ForeachAddScalarV2 | #5667 (open) | — | 高 | foreach/foreach_add_scalar |
| 04 | ForeachMulList | #4940 (open) | — | 高 | foreach/foreach_mul_list |
| 04 | ForeachRoundOffNumberV2 | #5446 (open) | #4454 设计 | 高 | foreach/foreach_round_off_number |
| 04 | ForeachSubListV2 | #5170 (open) | — | 高 | foreach/foreach_sub_list |
| 04 | Logit | #4945 (open) | #4500 早期；#4422/#4438/#4440 设计/修复 | 中高 | loss/logit |
| 04 | MaxUnpool3d | **未找到** | issue#3462 | — | 维护者：复用现有 gather_elements、无需新增算子 |
| 04 | Sleep | #5276 merged | #4453 设计 | 高 | experimental/control/sleep |
| 04 | ForeachExp | #5454 (open) | — | 高 | foreach/foreach_exp |
| 04 | ForeachExpm1 | #5214 (open) | #5231 后续arch35 | 高 | foreach/foreach_expm1 |
| 04 | ForeachNeg | #5514 (open) | #4506/#4507 设计 | 高 | foreach/foreach_neg |
| 04 | median | #6429 (open) | #5304/#5298/#4690 设计 | 高 | experimental/index/median |
| 05 | IndexFillTensor | #5694 (open) | — | 高 | experimental/index/index_fill |
| 05 | InplaceSigmoid | #6438 (open) | #5989 设计 | 高 | activation/sigmoid |
| 05 | Relu | #6387 (open) | #5638 设计+实现(open)；#4478 原始实验(merged) | 中高 | activation/relu |
| 05 | SyncBatchNormGatherStats | #6149 (open) | #5806/#5643 设计 | 高 | experimental/norm/sync_batch_norm_gather_stats |

## 其余 6 仓（7）

| 算子 | 仓 | 主开发 PR（状态） | 其他相关 PR | 置信 | 落点 |
|---|---|---|---|---|---|
| UpsampleNearestExact1d&2d | ops-cv | #1012 closed | #1048/#1070 交付(双分支,issue#561)；#937 设计 | 高 | image/upsample_nearest |
| SPMV | ops-sparse | #6 merged | #13/#12/#3；#31/#32/#36 修复 | 高 | src/spmv |
| aclblasTrsmBatched | ops-blas | #243 open | #241 重构；[非批量：#126/#236] | 高 | experimental/aclblasTrsmBatched |
| dynamicMap | ops-collections | #20 merged | — | 高 | dynamic_map |
| UpsampleNearest3d | ops-cv | **未找到** | 跟进 #217/#257/#266/#406/#920/#606… | 高 | 算子早存在；uint8 由 Dec-2025 批量 #90 引入、无离散交付 PR |
| SlidingTileAttention | ops-transformer | **仅设计 #4853** | 实现未找到 | 高 | experimental/attention 无该子目录 |
| aclsolverCheevj | ops-solver | **未找到** | issue#76 需求(open) | 高 | 未开发（Hermitian 特征值 Jacobi） |

## 7 个未找到 / 仅设计 / 未开发（对工作流设计重要）

- **未开发 / 复用已有**：MaxUnpool3d（复用 gather_elements）、aclsolverCheevj（仅需求 issue #76）、EmbeddingDenseGrad（无实现无 issue）。
- **仅设计、无实现**：SlidingTileAttention（只有方案 PR #4853）、MaxUnpool2d（仅设计文档 #2831）。
- **dtype 已被批量 PR 引入、无离散交付**：UpsampleNearest3d（uint8 混在批量 #90 里）。
- **成熟算子、开发早于数据窗口、无社区任务交付 PR**：MinDim&MaxDim（疑 ArgMin/MaxWithValue）。

> 合计 7 个：MaxUnpool2d、EmbeddingDenseGrad、MinDim&MaxDim、MaxUnpool3d、UpsampleNearest3d、SlidingTileAttention、aclsolverCheevj。**另 Gcd 找到 #1087（merged）但在 2 月、无「社区任务」标记——算「找到但存疑」，不计入这 7 个。**

## 对工作流设计的输入（规律）

- **社区任务多是「给已有算子加 dtype」**（int8/uint8/int16/bf16）或新算子开发 → Task 1「PR 影响面」= 新增 dtype 那条路径。
- **一任务对多 PR 是常态** → PR 影响面分析要能吃「设计文档 PR + 主开发 PR + 后续修复 + 多人并行提交」这一组，而非单个 PR。
- 主实现基本落 `experimental/<类>/<算子>/` 或 `<类>/<算子>/`（op_host/op_kernel/examples/tests；也有 `conversion/im2col`、`src/spmv`、`dynamic_map` 等变体）→ 这些是**示例算子仓**（自带 examples/tests），对应 **new_example 模式、不用桥**（与 catlass 模板库两码事）。
- **主开发 PR 多为 open**（在评审）→ 验收发生在 open PR 上，不能只认 merged。
- 有「未开发/复用/仅设计」的空任务 → 工作流要能识别并跳过（不是每个任务都有可验收的交付）。
